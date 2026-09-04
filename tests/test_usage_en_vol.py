"""Le coût d'une tâche en cours se voit pendant qu'il se dépense (#835 ; tests différés → #838).

Ce lot 1/4 de #834 est livré sans sa suite complète — elle vient avec le lot
« tests + doc » —, mais deux de ses propriétés sont **critiques** et gardées ici
dès maintenant, parce qu'un défaut sur l'une ou l'autre ne se verrait pas : il
ferait un run **plus cher** sans qu'aucun écran ne rougisse.

- le **total soldé ne bouge pas d'un token** : les tours signalés en cours de
  route, plus le reste que porte le résultat, font exactement ce que le résultat
  seul faisait avant ce lot — `appels`, `tours`, `outils`, coût et durée compris ;
- un relevé **ne compte jamais deux fois** : hors de `records`, donc hors de
  `usage_totale`, du grand livre et du plafond de dépense qui le relit ; hors de
  l'export Langfuse ; et, côté Control Tower, hors du grand livre du run pendant
  que le cumul du run, lui, le reflète.

Le reste — la carte qui distingue « rien consommé encore » de « coût inconnu »,
le run dont le montant bouge entre deux lectures, le graphe qui porte la même
réserve que la carte — est gardé au passage, sur le même flux. Le lot 4 (#838,
`tests/test_run_qui_travaille.py`) reprend la question par l'autre bout : la
carte d'une tâche en vol restée `null` y est l'**échantillon fautif** sur lequel
le contrôle rougit d'abord, et les trois lectures de la carte y sont prouvées
distinctes deux à deux.

**Ni réseau, ni SDK, ni Redis** : le flux du SDK est joué par des doubles, comme
dans `test_providers.py`, et le journal émet sur un logger de test.
"""

from __future__ import annotations

import json
import logging

import pytest

from maestro.controltower import ControlTowerState
from maestro.controltower.analytics import agrege_couts
from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.events import EVENEMENT_TACHE_STATUT, EVENEMENT_TACHE_USAGE
from maestro.engine.executor import (
    STATUT_EN_COURS,
    STATUT_TERMINEE,
    STATUT_USAGE,
    SUFFIXE_ETAPE_DEBUT,
    LocalExecutor,
    _ReleveUsage,
)
from maestro.orchestrator.schema import Task
from maestro.providers import claude as claude_mod
from maestro.telemetry import (
    SUFFIXE_ETAPE_USAGE,
    RunCost,
    RunJournal,
    StepUsage,
    collect_usage,
    est_releve_usage,
    usage_en_cours,
)
from maestro.telemetry.langfuse import evenements_depuis_step as langfuse_depuis_step

RUN = "run-835"


# ------------------------------------------------- ① Le fournisseur signale tour par tour


class _Texte:
    def __init__(self, text: str) -> None:
        self.text = text


class _Outil:
    def __init__(self, name: str) -> None:
        self.name = name
        self.input: dict[str, object] = {}


class _Assistant:
    """Le `AssistantMessage` du SDK : ses blocs, et le `usage` de son appel d'API."""

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
    """Substitue les types du SDK par des doubles — même harnais que test_providers."""
    monkeypatch.setattr(claude_mod, "AssistantMessage", _Assistant)
    monkeypatch.setattr(claude_mod, "TextBlock", _Texte)
    monkeypatch.setattr(claude_mod, "ToolUseBlock", _Outil)
    monkeypatch.setattr(claude_mod, "ResultMessage", _Resultat)


def _absorbeur(compteur):
    parts: list[str] = []
    outils: list[str] = []
    return lambda message: claude_mod._absorbe(
        message, parts, outils, None, None, compteur=compteur
    )


_TOUR_1 = {"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 10}
_TOUR_2 = {"input_tokens": 200, "output_tokens": 20}
_RESULTAT = {"input_tokens": 300, "cache_read_input_tokens": 50, "output_tokens": 30}


def test_les_tours_se_signalent_au_fil_et_le_total_solde_ne_bouge_pas(sdk):
    """Le critère qui rend le lot livrable : ce que le grand livre voit n'a pas changé."""
    vus: list[StepUsage] = []
    absorbe = _absorbeur(claude_mod._CompteurTours())

    with collect_usage(on_mesure=vus.append) as recolte:
        absorbe(_Assistant([_Texte("je lis")], usage=_TOUR_1, message_id="m1"))
        # Le CLI répète l'usage d'un même appel d'API sur chacun de ses blocs :
        # dédupliqué par identifiant, ce second message n'ajoute rien.
        absorbe(_Assistant([_Outil("Write")], usage=_TOUR_1, message_id="m1"))
        absorbe(_Assistant([_Texte("j'écris")], usage=_TOUR_2, message_id="m2"))
        absorbe(
            _Resultat(usage=_RESULTAT, total_cost_usd=0.042, duration_api_ms=900, num_turns=2)
        )

    # En cours de route : les tokens montent, le coût n'est pas encore tarifé.
    assert [v.tokens_total for v in vus] == [160, 380, 380]
    assert [v.tours for v in vus] == [1, 2, 2]
    assert [v.cout_usd for v in vus[:2]] == [None, None]
    assert [v.appels for v in vus[:2]] == [0, 0]
    # Soldé : exactement ce que le résultat seul rendait avant ce lot.
    assert recolte.total == StepUsage(
        appels=1,
        tokens_entree=350,
        tokens_sortie=30,
        cout_usd=0.042,
        duree_api_ms=900,
        tours=2,
        outils=("Write",),
    )


def test_un_resultat_qui_en_dit_moins_que_les_tours_fait_foi(sdk):
    """Tours + reste = résultat, même quand le reste est négatif : le soldé est celui d'avant."""
    absorbe = _absorbeur(claude_mod._CompteurTours())

    with collect_usage() as recolte:
        absorbe(_Assistant([_Texte("…")], usage=_TOUR_1, message_id="m1"))
        absorbe(_Resultat(usage=None, total_cost_usd=None, num_turns=1))

    assert recolte.total == StepUsage(
        appels=1, tokens_entree=0, tokens_sortie=0, duree_api_ms=0, tours=1
    )


def test_une_session_coupee_avant_son_resultat_garde_les_tokens_de_ses_tours(sdk):
    """Avant ce lot, elle ne laissait rien : c'est ce qui donne prise au plafond en tokens."""
    absorbe = _absorbeur(claude_mod._CompteurTours())

    with collect_usage() as recolte:
        absorbe(_Assistant([_Texte("…")], usage=_TOUR_1, message_id="m1"))
        absorbe(_Assistant([_Texte("…")], usage=_TOUR_2, message_id="m2"))

    assert recolte.total.tokens_total == 380
    assert recolte.total.tours == 2
    assert recolte.total.appels == 0
    assert recolte.total.cout_usd is None


def test_un_message_sans_usage_ni_identifiant_ne_signale_rien(sdk):
    """Les doubles d'avant ce lot (et un SDK plus ancien) passent sans bruit."""
    vus: list[StepUsage] = []
    absorbe = _absorbeur(claude_mod._CompteurTours())

    with collect_usage(on_mesure=vus.append):
        absorbe(_Assistant([_Texte("…")]))

    assert vus == []


def test_sans_compteur_le_resultat_signale_tout_comme_avant(sdk):
    parts: list[str] = []
    with collect_usage() as recolte:
        claude_mod._absorbe(_Assistant([_Texte("…")], usage=_TOUR_1, message_id="m1"), parts, [])
        claude_mod._absorbe(_Resultat(usage=_RESULTAT, total_cost_usd=0.01), parts, [])

    assert recolte.total == StepUsage(
        appels=1, tokens_entree=350, tokens_sortie=30, cout_usd=0.01, duree_api_ms=0, tours=1
    )


# ---------------------------------------------- ② Le collecteur rappelle, sans rien compter


def test_le_rappel_recoit_le_cumul_avant_le_controle_et_ne_casse_jamais_la_mesure():
    class _PlafondQuiLeve:
        def verifie(self, en_cours):
            raise RuntimeError("plafond crevé")

    vus: list[StepUsage] = []

    def observateur(cumul):
        vus.append(cumul)
        raise ValueError("l'observateur tombe")

    with collect_usage(plafond=_PlafondQuiLeve(), on_mesure=observateur) as recolte:
        with pytest.raises(RuntimeError, match="plafond crevé"):
            claude_mod.report_usage(StepUsage(tokens_entree=5))

    # Le relevé est parti **avant** que le contrôle ne stoppe l'étape, l'erreur
    # de l'observateur n'a pas remplacé celle du contrôle, et la mesure est comptée.
    assert [v.tokens_entree for v in vus] == [5]
    assert recolte.total.tokens_entree == 5


def test_usage_en_cours_lit_le_collecteur_actif_et_rien_hors_de_lui():
    assert usage_en_cours() == StepUsage()
    with collect_usage():
        claude_mod.report_usage(StepUsage(appels=1, tokens_sortie=3))
        assert usage_en_cours() == StepUsage(appels=1, tokens_sortie=3)
    assert usage_en_cours() == StepUsage()


# ------------------------------------------- ③ Le journal émet le relevé sans le conserver


class _Lignes(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lignes: list[dict] = []

    def emit(self, record):
        self.lignes.append(json.loads(record.getMessage()))


def _journal() -> tuple[RunJournal, _Lignes]:
    logger = logging.getLogger(f"test.releve.{id(object())}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    lignes = _Lignes()
    logger.addHandler(lignes)
    return RunJournal(run_id=RUN, logger=logger), lignes


def test_un_releve_est_emis_mais_ne_compte_dans_aucun_agregat_du_journal():
    journal, lignes = _journal()
    journal.consigne(
        etape="t0", nom="Avant", agent="dev", role="Dev", statut=STATUT_TERMINEE,
        entree="", sortie="", usage=StepUsage(appels=1, tokens_entree=10, cout_usd=0.2),
    )

    journal.releve(
        tache_id="t1", nom="En cours", agent="dev", role="Dev", statut=STATUT_USAGE,
        usage=StepUsage(tokens_entree=999, tours=3), sortie="999 tokens · 3 tour(s)",
    )

    (_, releve) = lignes.lignes
    assert releve["etape"] == f"t1{SUFFIXE_ETAPE_USAGE}"
    assert releve["usage"]["tokens_entree"] == 999
    assert est_releve_usage(releve["etape"])
    # Émis, jamais conservé : ni les étapes, ni le total, ni le grand livre
    # (donc ni le plafond de dépense, qui le relit) ne l'ont vu.
    assert [r.etape for r in journal.records] == ["t0"]
    assert journal.usage_totale == StepUsage(appels=1, tokens_entree=10, cout_usd=0.2)
    grand_livre = RunCost.depuis_journal(journal)
    assert [t.tache_id for t in grand_livre.taches] == ["t0"]
    assert grand_livre.total.tokens_entree == 10


def test_l_export_langfuse_ignore_un_releve():
    """Une génération par relevé ferait un run trois fois plus cher sur sa trace."""
    ligne = {
        "run_id": RUN,
        "etape": f"t1{SUFFIXE_ETAPE_USAGE}",
        "nom": "En cours",
        "usage": {"appels": 0, "tokens_entree": 999, "tokens_sortie": 1},
        "horodatage": "2026-09-04T10:00:00+00:00",
    }
    assert langfuse_depuis_step(ligne) == ()
    # Le motif est prouvé sur une vraie étape : elle, est exportée.
    assert langfuse_depuis_step({**ligne, "etape": "t1"}) != ()


# ---------------------------------------- ④ Le moteur relève : ouverture, cadence, phrase


def _tache() -> Task:
    return Task(
        id="t1",
        titre="Interface agenda",
        description="…",
        competences_requises=("python",),
        format_sortie="Module",
        dependances=(),
    )


class _Agent:
    nom = "developpeur"
    role = "Développeur"


def test_une_mesure_vide_est_relevee_a_cout_zero_et_le_dit():
    """Rien consommé n'est pas coût inconnu — troisième critère du ticket."""
    journal, lignes = _journal()
    executeur = LocalExecutor.__new__(LocalExecutor)

    LocalExecutor._releve_usage(executeur, _tache(), _Agent(), journal, StepUsage())

    (releve,) = lignes.lignes
    assert releve["etape"] == f"t1{SUFFIXE_ETAPE_USAGE}"
    assert releve["statut"] == STATUT_USAGE
    assert releve["usage"]["cout_usd"] == 0.0
    assert releve["sortie"] == "rien consommé encore"
    assert journal.records == ()


def test_une_mesure_non_tarifee_garde_son_cout_inconnu_et_dit_ses_tokens():
    journal, lignes = _journal()
    executeur = LocalExecutor.__new__(LocalExecutor)

    mesure = StepUsage(tokens_entree=12000, tokens_sortie=480, tours=3)
    LocalExecutor._releve_usage(executeur, _tache(), _Agent(), journal, mesure)

    (releve,) = lignes.lignes
    assert releve["usage"]["cout_usd"] is None
    assert releve["sortie"] == "12480 tokens · 3 tour(s) · coût pas encore tarifé"


def test_le_releve_bat_a_la_cadence_des_salves_et_le_premier_part_sans_attendre():
    journal, lignes = _journal()
    executeur = LocalExecutor.__new__(LocalExecutor)
    horloge = [100.0]
    releve = _ReleveUsage(executeur, _tache(), journal, horloge=lambda: horloge[0], periode_s=5.0)

    releve.mesure(StepUsage(tokens_entree=1))  # aucun agent encore : tu
    releve.agent = _Agent()
    releve.mesure(StepUsage(tokens_entree=2))  # premier : part
    horloge[0] += 2.0
    releve.mesure(StepUsage(tokens_entree=3))  # dans la fenêtre : sauté
    horloge[0] += 4.0
    releve.mesure(StepUsage(tokens_entree=4))  # fenêtre écoulée : part

    assert [ligne["usage"]["tokens_entree"] for ligne in lignes.lignes] == [2, 4]


# ------------------------------------- ⑤ Du journal à la Control Tower : partiel, puis soldé


def _ligne(etape: str, statut: str, usage: StepUsage | None = None, **champs) -> dict:
    return {
        "run_id": RUN,
        "etape": etape,
        "nom": "Interface agenda",
        "agent": "developpeur",
        "role": "Développeur",
        "statut": statut,
        "entree": "",
        "sortie": champs.pop("sortie", ""),
        "erreur": None,
        "usage": (usage or StepUsage()).to_dict(),
        **champs,
    }


def _applique(state: ControlTowerState, ligne: dict) -> None:
    for event in evenements_depuis_step(ligne):
        state.appliquer(event)


def test_le_pont_range_un_releve_en_tache_usage_et_garde_sa_mesure():
    (event,) = evenements_depuis_step(
        _ligne(f"t1{SUFFIXE_ETAPE_USAGE}", STATUT_USAGE, StepUsage(tokens_entree=999, tours=2))
    )
    assert event.type == EVENEMENT_TACHE_USAGE
    assert event.tache_id == "t1"
    assert event.usage is not None and event.usage.tokens_entree == 999
    assert event.cout_usd is None


def test_le_cout_d_une_tache_en_cours_se_voit_puis_se_solde_sans_compter_double():
    state = ControlTowerState()
    # Une tâche déjà soldée : le run vaut 0,20 $ avant que t1 ne démarre.
    _applique(state, _ligne("t0", STATUT_TERMINEE, StepUsage(appels=1, cout_usd=0.2)))
    _applique(state, _ligne(f"t1{SUFFIXE_ETAPE_DEBUT}", STATUT_EN_COURS))

    # Relevé d'ouverture : rien consommé encore — mesuré, donc 0 et non inconnu.
    _applique(state, _ligne(f"t1{SUFFIXE_ETAPE_USAGE}", STATUT_USAGE, StepUsage(cout_usd=0.0)))
    t1 = state.tache("t1")
    assert t1 is not None and t1.statut == STATUT_EN_COURS
    assert t1.cout_usd == 0.0 and t1.cout_partiel is True
    run = state.execution(RUN)
    assert run is not None
    assert run.cout_usd == pytest.approx(0.2) and run.cout_partiel is True

    # Des tokens consommés, pas encore tarifés : le coût redevient inconnu,
    # **partiel**, et les tokens se lisent dans `usage`.
    _applique(
        state,
        _ligne(f"t1{SUFFIXE_ETAPE_USAGE}", STATUT_USAGE, StepUsage(tokens_entree=5000, tours=2)),
    )
    t1 = state.tache("t1")
    assert t1 is not None and t1.cout_usd is None and t1.cout_partiel is True
    assert t1.usage is not None and t1.usage.tokens_total == 5000
    assert run.cout_usd == pytest.approx(0.2) and run.cout_partiel is True

    # Un fournisseur qui tarife en cours de route : le cumul du run bouge entre
    # deux lectures, sans attendre la fin de la tâche.
    tarife = StepUsage(tokens_entree=9000, cout_usd=0.3)
    _applique(state, _ligne(f"t1{SUFFIXE_ETAPE_USAGE}", STATUT_USAGE, tarife))
    assert run.cout_usd == pytest.approx(0.5) and run.cout_partiel is True
    assert state.tache("t1").cout_usd == pytest.approx(0.3)
    assert state.graphe(RUN).noeuds[1].cout_partiel is True

    # Le grand livre, lui, ne compte que le soldé — et une seule fois.
    assert run.cout.total.cout_usd == pytest.approx(0.2)

    # L'issue solde : le montant final remplace le relevé, rien ne s'ajoute.
    _applique(
        state,
        _ligne("t1", STATUT_TERMINEE, StepUsage(appels=1, tokens_entree=12000, cout_usd=0.5)),
    )
    t1 = state.tache("t1")
    assert t1 is not None and t1.cout_usd == pytest.approx(0.5) and t1.cout_partiel is False
    assert run.cout_usd == pytest.approx(0.7) and run.cout_partiel is False
    assert run.cout.total.cout_usd == pytest.approx(0.7)
    assert state.graphe(RUN).noeuds[1].cout_partiel is False
    assert run.resume()["cout_usd"] == pytest.approx(0.7)
    assert run.resume()["cout_partiel"] is False
    assert t1.to_dict()["cout_partiel"] is False
    # La vue analytique compte comme le grand livre : les relevés n'y entrent pas.
    assert agrege_couts(state.executions()).total.cout_usd == pytest.approx(0.7)


def test_un_releve_ne_deplace_aucune_carte_et_se_rejoue_a_l_identique():
    state = ControlTowerState()
    _applique(state, _ligne(f"t1{SUFFIXE_ETAPE_DEBUT}", STATUT_EN_COURS))
    mesure = StepUsage(tokens_entree=10, cout_usd=0.1)
    releve = _ligne(f"t1{SUFFIXE_ETAPE_USAGE}", STATUT_USAGE, mesure)

    _applique(state, releve)
    _applique(state, releve)  # rejeu du journal durable (#97)

    t1 = state.tache("t1")
    assert t1 is not None and t1.statut == STATUT_EN_COURS and t1.agent == "developpeur"
    assert t1.cout_usd == pytest.approx(0.1) and t1.cout_partiel is True
    assert state.execution(RUN).cout_usd == pytest.approx(0.1)
    dev = state.agent("developpeur")
    assert dev is not None and dev.cout_usd is None  # rien de soldé sur sa fiche


def test_une_issue_sans_cout_tarife_solde_quand_meme_le_releve():
    """Un fournisseur sans tarification (Ollama) : la tâche finit, le partiel s'éteint."""
    state = ControlTowerState()
    _applique(state, _ligne(f"t1{SUFFIXE_ETAPE_USAGE}", STATUT_USAGE, StepUsage(tokens_entree=10)))
    assert state.execution(RUN).cout_partiel is True

    _applique(state, _ligne("t1", STATUT_TERMINEE, StepUsage(appels=1, tokens_entree=12)))

    t1 = state.tache("t1")
    assert t1 is not None and t1.cout_usd is None and t1.cout_partiel is False
    assert state.execution(RUN).cout_usd is None
    assert state.execution(RUN).cout_partiel is False


def test_la_frise_et_le_kanban_ne_confondent_pas_un_releve_avec_un_statut():
    (event,) = evenements_depuis_step(
        _ligne(f"t1{SUFFIXE_ETAPE_USAGE}", STATUT_USAGE, StepUsage(tokens_entree=1))
    )
    assert event.type != EVENEMENT_TACHE_STATUT
    state = ControlTowerState()
    state.appliquer(event)
    assert state.tache("t1").statut == ""  # créée, mais aucune colonne posée
