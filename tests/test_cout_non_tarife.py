"""Le coût d'une tâche morte avant son résultat ne disparaît plus du total (#1280).

Le run réel `3fe501fc0878` (projet `p3`, 2026-09-24) : la tâche `maquette-sections`
échoue trois fois sans jamais produire de `ResultMessage`, la session tuée en cours
de route. Ses 2 092 911 tokens restent au grand livre, à coût `null` — le SDK ne
tarife qu'au résultat (#835), et le résultat n'est jamais venu. Le total du run
affiche alors 1,17 $, qui ne compte que ce qui a été tarifé, et le run se déclare
**complet** (`cout_partiel: false`) alors que 75 % de ses tokens n'ont pas de prix.

Ce qui est gardé ici, couche par couche :

① le **contrat** de mesure (`StepUsage.tokens_non_tarifes`) : une mesure sans coût
   a tous ses tokens non tarifés, et la fusion n'en perd aucun — ni dans un cumul,
   ni dans l'agrégat de tâches dont l'une est tarifée et l'autre non ;
② l'**adaptateur** Claude : le résultat d'une session tarifie les tours de **cette**
   session, et d'elle seule — une session coupée garde ses tokens non tarifés, une
   relance qui aboutit ne les efface pas ;
③ le **run**, du moteur à l'API : une tâche qui a consommé des tokens puis échoue
   sans résultat laisse un total qui le dit et un run marqué `cout_partiel: true` —
   c'est le troisième critère du ticket, joué sur le chemin réel (moteur, journal,
   pont, projection, routes) avec un fournisseur double.

Maestro n'invente aucun prix (`maestro.telemetry.costs`, docstring de module) : le
total reste ce qui a été tarifé, et c'est le drapeau qui dit qu'il n'est qu'un
plancher. **Ni réseau, ni SDK, ni Redis.**
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.bridge import evenements_depuis_step
from maestro.engine.executor import STATUT_EN_COURS, STATUT_TERMINEE, STATUT_USAGE, LocalExecutor
from maestro.engine.retry import PolitiqueRelance
from maestro.orchestrator import Task
from maestro.providers import claude as claude_mod
from maestro.providers.base import ModelProvider
from maestro.telemetry import (
    SUFFIXE_ETAPE_USAGE,
    RunJournal,
    StepUsage,
    collect_usage,
    report_usage,
)

RUN = "run-1280"


# ----------------------------------------------------- ① Le contrat de la mesure


def test_une_mesure_sans_cout_a_tous_ses_tokens_non_tarifes():
    assert StepUsage(tokens_entree=900, tokens_sortie=100).tokens_non_tarifes == 1000
    assert StepUsage(tokens_entree=900, cout_usd=0.3).tokens_non_tarifes == 0
    # Rien consommé, rien à tarifer : le coût inconnu d'une mesure vide ne laisse
    # aucun token en souffrance.
    assert StepUsage().tokens_non_tarifes == 0


def test_la_fusion_de_taches_tarifee_et_non_tarifee_garde_les_tokens_sans_cout():
    """Le cas du ticket, réduit à l'agrégat : 1,17 $ connus, 2 M de tokens sans prix."""
    tarifee = StepUsage(appels=1, tokens_entree=700_000, cout_usd=1.17)
    morte = StepUsage(tokens_entree=2_000_000, tokens_sortie=92_911, tours=46)

    total = tarifee.fusion(morte)

    # Le montant connu reste ce qui a été tarifé — Maestro n'invente aucun prix…
    assert total.cout_usd == pytest.approx(1.17)
    # …mais les tokens de la tâche morte ne sont plus couverts par ce montant.
    assert total.tokens_non_tarifes == 2_092_911


def test_la_part_non_tarifee_fait_l_aller_retour_json_et_se_deduit_d_une_ligne_ancienne():
    usage = StepUsage(appels=1, tokens_entree=10, cout_usd=0.1).fusion(
        StepUsage(tokens_entree=40)
    )
    forme = usage.to_dict()

    assert forme["tokens_non_tarifes"] == 40
    assert StepUsage.from_dict(forme) == usage
    # Une ligne de journal écrite avant #1280 ne porte pas la clé : une mesure
    # sans coût s'y relit avec tous ses tokens non tarifés, une mesure tarifée
    # avec aucun — c'est tout ce qu'on peut en savoir, et c'est vrai.
    ancienne = {"appels": 0, "tokens_entree": 30, "tokens_sortie": 5, "cout_usd": None}
    assert StepUsage.from_dict(ancienne).tokens_non_tarifes == 35
    assert StepUsage.from_dict({**ancienne, "cout_usd": 0.02}).tokens_non_tarifes == 0


# ---------------------------------------------- ② L'adaptateur Claude : une session


class _Texte:
    def __init__(self, text: str) -> None:
        self.text = text


class _Assistant:
    def __init__(self, content, *, usage=None, message_id=None) -> None:
        self.content = content
        self.usage = usage
        self.message_id = message_id


class _Resultat:
    def __init__(self, *, usage=None, total_cost_usd=None, duration_api_ms=0, num_turns=1):
        self.usage = usage
        self.total_cost_usd = total_cost_usd
        self.duration_api_ms = duration_api_ms
        self.num_turns = num_turns


@pytest.fixture()
def sdk(monkeypatch):
    """Substitue les types du SDK par des doubles — même harnais que test_usage_en_vol."""
    monkeypatch.setattr(claude_mod, "AssistantMessage", _Assistant)
    monkeypatch.setattr(claude_mod, "TextBlock", _Texte)
    monkeypatch.setattr(claude_mod, "ResultMessage", _Resultat)


def _session():
    """Une session : son propre compteur de tours, comme un appel `_collect_response`."""
    compteur = claude_mod._CompteurTours()
    return lambda message: claude_mod._absorbe(message, [], [], None, compteur=compteur)


_TOUR_1 = {"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 10}
_TOUR_2 = {"input_tokens": 200, "output_tokens": 20}
_RESULTAT = {"input_tokens": 300, "cache_read_input_tokens": 50, "output_tokens": 30}


def test_le_resultat_d_une_session_tarifie_tous_ses_tours(sdk):
    absorbe = _session()
    with collect_usage() as recolte:
        absorbe(_Assistant([_Texte("…")], usage=_TOUR_1, message_id="m1"))
        # En vol, les tours sont en attente de leur prix.
        assert recolte.total.tokens_non_tarifes == 160
        absorbe(_Assistant([_Texte("…")], usage=_TOUR_2, message_id="m2"))
        absorbe(_Resultat(usage=_RESULTAT, total_cost_usd=0.042, num_turns=2))

    assert recolte.total.cout_usd == pytest.approx(0.042)
    assert recolte.total.tokens_non_tarifes == 0


def test_une_session_tuee_avant_son_resultat_laisse_ses_tokens_non_tarifes(sdk):
    absorbe = _session()
    with collect_usage() as recolte:
        absorbe(_Assistant([_Texte("…")], usage=_TOUR_1, message_id="m1"))
        absorbe(_Assistant([_Texte("…")], usage=_TOUR_2, message_id="m2"))

    assert recolte.total.cout_usd is None
    assert recolte.total.tokens_non_tarifes == recolte.total.tokens_total == 380


def test_une_relance_qui_aboutit_ne_tarifie_pas_la_session_tuee_avant_elle(sdk):
    """Le résultat de la seconde session ne couvre que ses propres tours (#91, #1280).

    C'est le cas qu'aucune règle sur la seule mesure finale ne voit : la tâche finit
    tarifée, son coût est connu — et il ne couvre pas les tokens de la première
    tentative, tuée avant son résultat.
    """
    premiere, seconde = _session(), _session()
    with collect_usage() as recolte:
        premiere(_Assistant([_Texte("…")], usage=_TOUR_1, message_id="a1"))
        seconde(_Assistant([_Texte("…")], usage=_TOUR_2, message_id="b1"))
        seconde(_Resultat(usage=_TOUR_2, total_cost_usd=0.01, num_turns=1))

    assert recolte.total.cout_usd == pytest.approx(0.01)
    assert recolte.total.tokens_total == 160 + 220
    assert recolte.total.tokens_non_tarifes == 160


# ------------------------------------------------ ③ Le run, du moteur à l'API


def _tache(identifiant: str) -> Task:
    return Task(
        id=identifiant,
        titre="Maquette des sections",
        description="…",
        competences_requises=("sql",),
        format_sortie="Maquette",
        dependances=(),
    )


class _SessionTuee(ModelProvider):
    """Fournisseur double : des tours consommés, puis la session meurt sans résultat.

    C'est ce que fait le SDK quand il tue une session en cours de route — le
    plafond de flux de #1277 en est la cause qui a produit le constat du ticket.
    """

    name = "session-tuee"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        report_usage(StepUsage(tokens_entree=600_000, tokens_sortie=4_000, tours=1))
        report_usage(StepUsage(tokens_entree=90_000, tokens_sortie=3_637, tours=1))
        raise RuntimeError("Command failed with exit code 1 (session tuée)")


class _Lignes(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lignes: list[dict] = []

    def emit(self, record):
        self.lignes.append(json.loads(record.getMessage()))


def _journal() -> tuple[RunJournal, _Lignes]:
    logger = logging.getLogger(f"test.cout-non-tarife.{id(object())}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    lignes = _Lignes()
    logger.addHandler(lignes)
    return RunJournal(run_id=RUN, logger=logger), lignes


def _run_avec_une_tache_morte() -> ControlTowerState:
    """Une tâche tarifée à 1,17 $, puis une tâche qui meurt trois fois sans résultat."""
    journal, lignes = _journal()
    journal.consigne(
        etape="brief-produit", nom="Brief produit", agent="developpeur",
        role="Développeur", statut=STATUT_TERMINEE, entree="", sortie="fait",
        usage=StepUsage(appels=1, tokens_entree=700_000, cout_usd=1.17),
    )
    executeur = LocalExecutor(
        _SessionTuee(),
        runtimes={},
        relance=PolitiqueRelance(max_tentatives=3, backoff_s=0.0),
    )
    resultat = asyncio.run(executeur.execute(_tache("maquette-sections"), [], journal))
    assert not resultat.ok

    state = ControlTowerState()
    for ligne in lignes.lignes:
        for event in evenements_depuis_step(ligne):
            state.appliquer(event)
    return state


def test_une_tache_morte_sans_resultat_laisse_un_total_qui_le_dit_et_un_run_partiel():
    """Le troisième critère du ticket, sur le chemin réel : moteur, journal, pont, routes."""
    state = _run_avec_une_tache_morte()

    with TestClient(create_app(bus=InMemoryEventBus(), state=state)) as client:
        cout = client.get(f"/api/executions/{RUN}/cout").json()
        run = client.get(f"/api/executions/{RUN}").json()
        runs = client.get("/api/executions", params={"projet": "tous"}).json()
        (resume,) = [r for r in runs if r["run_id"] == RUN]
        taches = {
            t["id"]: t for t in client.get("/api/taches", params={"projet": "tous"}).json()
        }

    morte = next(t for t in cout["taches"] if t["tache_id"] == "maquette-sections")
    # Trois tentatives, chacune tuée avant son résultat : 3 × 697 637 tokens, sans prix.
    assert morte["usage"]["cout_usd"] is None
    assert morte["usage"]["tokens_total"] == 3 * 697_637
    assert morte["usage"]["tokens_non_tarifes"] == 3 * 697_637
    # Le total ne compte en dollars que ce qui a été tarifé, et dit ce qui ne l'a pas été.
    assert cout["total"]["cout_usd"] == pytest.approx(1.17)
    assert cout["total"]["tokens_non_tarifes"] == 3 * 697_637
    # Le run ne se déclare plus complet — ni dans sa vue, ni dans la liste des runs.
    assert run["cout_usd"] == pytest.approx(1.17)
    assert run["cout_partiel"] is True
    assert resume["cout_partiel"] is True
    # Et la carte de la tâche porte la même réserve que le run.
    assert taches["maquette-sections"]["cout_usd"] is None
    assert taches["maquette-sections"]["cout_partiel"] is True
    assert taches["brief-produit"]["cout_partiel"] is False


def test_un_run_dont_tout_est_tarife_reste_complet():
    """L'échantillon témoin : le drapeau ne se lève pas sur un run sans token orphelin."""
    journal, lignes = _journal()
    journal.consigne(
        etape="t1", nom="Tarifée", agent="developpeur", role="Développeur",
        statut=STATUT_TERMINEE, entree="", sortie="fait",
        usage=StepUsage(appels=1, tokens_entree=700_000, cout_usd=1.17),
    )
    state = ControlTowerState()
    for ligne in lignes.lignes:
        for event in evenements_depuis_step(ligne):
            state.appliquer(event)

    run = state.execution(RUN)
    assert run is not None
    assert run.cout_partiel is False
    assert run.cout.total.tokens_non_tarifes == 0
    assert state.tache("t1").cout_partiel is False


def _applicateur(state: ControlTowerState):
    def applique(etape: str, statut: str, usage: StepUsage) -> None:
        ligne = {
            "run_id": RUN, "etape": etape, "nom": "Maquette", "agent": "developpeur",
            "role": "Développeur", "statut": statut, "entree": "", "sortie": "",
            "erreur": None, "usage": usage.to_dict(),
        }
        for event in evenements_depuis_step(ligne):
            state.appliquer(event)

    return applique


def test_le_releve_en_vol_puis_l_echec_sans_cout_ne_rend_pas_le_run_complet():
    """Le moment exact du défaut : l'issue solde le relevé, et le partiel s'éteignait."""
    state = ControlTowerState()
    applique = _applicateur(state)

    applique("t0", STATUT_TERMINEE, StepUsage(appels=1, cout_usd=1.17))
    applique("t1:debut", STATUT_EN_COURS, StepUsage())
    applique(f"t1{SUFFIXE_ETAPE_USAGE}", STATUT_USAGE, StepUsage(tokens_entree=5000, tours=2))
    run = state.execution(RUN)
    assert run is not None and run.cout_partiel is True

    applique("t1", "echec", StepUsage(tokens_entree=9000, tours=3))

    assert run.cout_usd == pytest.approx(1.17)
    assert run.cout_partiel is True
    t1 = state.tache("t1")
    assert t1 is not None and t1.cout_usd is None and t1.cout_partiel is True
    assert state.graphe(RUN).noeuds[-1].cout_partiel is True


def test_une_tache_morte_ne_garde_pas_le_zero_de_son_releve_d_ouverture():
    """« Rien consommé encore » (0 $, #835) ne survit pas à une issue qui a consommé.

    Le relevé d'ouverture d'une tentative pose un coût **mesuré** de zéro ; si la
    session meurt avant que la cadence n'ait laissé passer un autre relevé, l'issue
    sans coût laissait ce zéro sur la carte — le coût d'une tâche morte présenté
    comme nul, exactement ce que le ticket interdit.
    """
    state = ControlTowerState()
    applique = _applicateur(state)

    applique("t1:debut", STATUT_EN_COURS, StepUsage())
    applique(f"t1{SUFFIXE_ETAPE_USAGE}", STATUT_USAGE, StepUsage(cout_usd=0.0))
    assert state.tache("t1").cout_usd == 0.0

    applique("t1", "echec", StepUsage(tokens_entree=9000, tours=3))

    t1 = state.tache("t1")
    assert t1 is not None and t1.cout_usd is None and t1.cout_partiel is True
