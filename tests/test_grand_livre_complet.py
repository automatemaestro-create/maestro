"""Le grand livre compte tout ce qu'un projet dépense (#1534).

Instruit par #1508 (docs/50 § 1.2) sur le run `fea601a5c629` de p5 : les
transcriptions de ses sessions donnaient 15,97 $, le run en déclarait 11,52 $. Il
manquait 4,46 $, soit 28 %, en deux trous que ce fichier referme.

- **La tentative coupée en vol** (2,908 $). Une tâche que l'extinction de Maestro
  arrête est soldée `interrompue` (#1390) — et ce soldage ne portait **aucune
  mesure**. Ce qu'elle avait dépensé n'existait plus que dans ses relevés en cours
  (#835), qu'aucun lecteur comptable ne compte, et que la reprise de la même tâche
  écrasait au premier relevé de sa nouvelle tentative. `page-liste` avait publié
  1,4464 $ « engagés » en direct ; ce montant disparaissait.
- **Le fil** (1,548 $) : la déclaration du projet, ses questions d'outillage, ses
  pièces, son équipe, son lancement, ses récits. Les réponses du fil ne publiaient
  aucun usage, si bien que rien de ce qu'il dépense n'entrait au grand livre.

Les deux se lisent sur les **trois** lecteurs d'un même montant, qui ne doivent pas
se contredire : le cumul du run (`EtatExecution.cout_usd`), son grand livre
(`EtatExecution.cout`, #57) et la vue Coûts & analytics (`GET /api/analytics/couts`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.agents.catalog import Agent
from maestro.controltower import (
    ControlTowerState,
    Event,
    InMemoryEventBus,
    InMemoryEventLog,
    RegistreBattementsMemoire,
    create_app,
)
from maestro.controltower.analytics import PAS_AUTO, agrege_couts
from maestro.controltower.chat import ChatStore, MessageChat, RepondeurScripte
from maestro.controltower.depense_du_fil import au_compte_du_fil, en_clair
from maestro.controltower.events import (
    EVENEMENT_FIL_DEPENSE,
    EVENEMENT_RUN_PLAN,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_TACHE_USAGE,
    EventBus,
)
from maestro.controltower.hote import HoteMort, HoteRun, OrdreRun
from maestro.controltower.orchestration import NOM_ORCHESTRATION
from maestro.controltower.portee import PorteeProjet
from maestro.controltower.progression import STATUT_INTERROMPUE
from maestro.engine import STATUT_EN_COURS
from maestro.plan_run import NoeudPlan
from maestro.telemetry.usage import StepUsage, report_usage

#: Plafond d'attente d'un fait asynchrone (la pompe applique ce qu'on pousse).
DELAI_ATTENTE_S = 5.0

#: La tâche coupée en vol — celle de p5 qui avait publié 1,4464 $ « engagés ».
COUPEE = "page-liste"

#: Ce que la tentative coupée avait relevé en direct : un coût engagé, et des
#: tokens que ce coût ne couvre pas encore (le tour en vol n'est tarifé qu'à son
#: résultat, qui ne viendra jamais — #1280).
RELEVE_COUPE = StepUsage(
    appels=3,
    tokens_entree=180_000,
    tokens_sortie=6_000,
    cout_usd=1.4464,
    tokens_non_tarifes=40_000,
)

#: L'issue de la tentative reprise, qui aboutit.
ISSUE_REPRISE = StepUsage(appels=2, tokens_entree=90_000, tokens_sortie=4_000, cout_usd=0.5)


class HoteDouble(HoteRun):
    """Un hôte qui ne lance aucun process : ce qu'on éprouve ici est le grand livre."""

    def __init__(self) -> None:
        self.lances: list[str] = []

    async def lancer(self, ordre: OrdreRun) -> None:
        self.lances.append(ordre.run_id)

    async def annuler(self, run_id: str, *, delai_s: float) -> bool:
        if run_id not in self.lances:
            return False
        self.lances.remove(run_id)
        return True

    def en_vol(self, run_id: str) -> bool:
        return run_id in self.lances

    def runs_en_vol(self) -> tuple[str, ...]:
        return tuple(self.lances)

    def ramasser(self) -> tuple[HoteMort, ...]:
        return ()

    async def fermer(self, *, delai_s: float) -> None:
        return None


def _app(state: ControlTowerState) -> TestClient:
    """L'app réelle sur bus, journal et registre mémoire — seul l'hôte est double."""
    return TestClient(
        create_app(
            bus=InMemoryEventBus(),
            state=state,
            event_log=InMemoryEventLog(),
            battements=RegistreBattementsMemoire(),
            hote_run=HoteDouble(),
        )
    )


def _attendre(condition, quoi: str) -> None:
    """Attend qu'un fait asynchrone advienne — la pompe qui applique un événement poussé."""
    limite = time.monotonic() + DELAI_ATTENTE_S
    while not condition():
        if time.monotonic() > limite:  # pragma: no cover - filet anti-blocage
            pytest.fail(f"{quoi} n'est jamais arrivé en {DELAI_ATTENTE_S} s")
        time.sleep(0.02)


def _analytics(client: TestClient) -> dict[str, Any]:
    """La vue Coûts & analytics, transverse."""
    reponse = client.get("/api/analytics/couts", params={"projet": "tous"})
    assert reponse.status_code == 200, reponse.text
    return dict(reponse.json())


def _tache(state: ControlTowerState, run_id: str, statut: str, usage: StepUsage | None) -> None:
    """Un `tache.statut` de la tâche coupée, porteur ou non de son issue mesurée."""
    state.appliquer(
        Event(
            type=EVENEMENT_TACHE_STATUT,
            run_id=run_id,
            tache_id=COUPEE,
            titre="Page liste",
            agent="dev",
            role="Développeur",
            statut=statut,
            usage=usage,
            cout_usd=usage.cout_usd if usage is not None else None,
        )
    )


def _releve(state: ControlTowerState, run_id: str, usage: StepUsage) -> None:
    """Un relevé en cours (#835) : ce que la tentative a consommé **jusqu'ici**."""
    state.appliquer(
        Event(
            type=EVENEMENT_TACHE_USAGE,
            run_id=run_id,
            tache_id=COUPEE,
            agent="dev",
            role="Développeur",
            usage=usage,
            cout_usd=usage.cout_usd,
        )
    )


def _couper_en_vol(client: TestClient, state: ControlTowerState) -> str:
    """Lance un run, fait travailler la tâche jusqu'à un relevé, puis éteint Maestro."""
    reponse = client.post("/api/executions", json={"objectif": "Un catalogue de recettes"})
    assert reponse.status_code == 202, reponse.text
    run_id = str(reponse.json()["run_id"])
    state.appliquer(
        Event(type=EVENEMENT_RUN_PLAN, run_id=run_id, plan=[NoeudPlan(id=COUPEE, titre=COUPEE)])
    )
    _tache(state, run_id, STATUT_EN_COURS, None)
    _releve(state, run_id, RELEVE_COUPE)

    assert client.post("/api/extinction").status_code == 200
    _attendre(
        lambda: (tache := state.tache(COUPEE)) is not None and tache.statut == STATUT_INTERROMPUE,
        "la tâche en vol soldée par l'extinction",
    )
    return run_id


def test_la_tentative_coupee_en_vol_garde_ce_qu_elle_a_coute() -> None:
    """Le soldage `interrompue` porte ce que la tentative avait relevé, et il le solde.

    C'est l'argent « engagé » relevé en direct qui entre au grand livre, et ses tokens
    sans prix y restent marqués (#1280) : le run ne se dit pas complet.
    """
    state = ControlTowerState()
    with _app(state) as client:
        run_id = _couper_en_vol(client, state)

        execution = state.execution(run_id)
        assert execution is not None
        soldage = [
            e
            for e in execution.evenements
            if e.type == EVENEMENT_TACHE_STATUT and e.tache_id == COUPEE
        ][-1]
        assert soldage.statut == STATUT_INTERROMPUE
        assert soldage.usage == RELEVE_COUPE
        assert soldage.cout_usd == pytest.approx(1.4464)

        assert execution.cout_usd == pytest.approx(1.4464)
        assert execution.cout.total.cout_usd == pytest.approx(1.4464)
        assert execution.cout_partiel is True
        assert _analytics(client)["total"]["cout_usd"] == pytest.approx(1.4464)
        assert _analytics(client)["total"]["tokens_non_tarifes"] == 40_000


def test_la_reprise_de_la_tache_n_efface_pas_la_tentative_coupee() -> None:
    """La même tâche, reprise dans le même run, relève et se solde par-dessus.

    Avant #1534, son premier relevé **remplaçait** celui de la tentative coupée (le
    dernier relevé fait foi), et le run déclarait le seul coût de la reprise : c'est
    ainsi que 2,908 $ des trois tâches coupées de p5 ont disparu.
    """
    state = ControlTowerState()
    with _app(state) as client:
        run_id = _couper_en_vol(client, state)

        _tache(state, run_id, STATUT_EN_COURS, None)
        _releve(state, run_id, StepUsage(appels=1, tokens_entree=30_000, cout_usd=0.2))
        _tache(state, run_id, "terminee", ISSUE_REPRISE)

        execution = state.execution(run_id)
        assert execution is not None
        attendu = 1.4464 + 0.5
        assert execution.cout_usd == pytest.approx(attendu)
        assert execution.cout.total.cout_usd == pytest.approx(attendu)
        assert _analytics(client)["total"]["cout_usd"] == pytest.approx(attendu)
        # La carte de la tâche cumule ses deux tentatives (#1177).
        tache = state.tache(COUPEE)
        assert tache is not None
        assert tache.cout_usd == pytest.approx(attendu)


# --------------------------------------------------------------------------- le fil


#: Ce qu'un tour du fil coûte au modèle, dans ces tests.
TOUR_DU_FIL = StepUsage(appels=1, tokens_entree=12_000, tokens_sortie=800, cout_usd=0.05)


class BusQuiRetient(EventBus):
    """Un bus qui retient ce qu'on lui publie — la seule chose qu'on regarde ici."""

    def __init__(self) -> None:
        self.publies: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.publies.append(event)

    async def subscribe(self) -> AsyncIterator[Event]:  # pragma: no cover - jamais lu
        for event in ():
            yield event


def test_au_compte_du_fil_publie_ce_que_le_bloc_a_depense() -> None:
    """Ce que les appels du bloc signalent sort en une `fil.depense`, projet lu à la sortie."""
    bus = BusQuiRetient()
    projet: list[str] = []

    async def scenario() -> None:
        async with au_compte_du_fil(
            bus,
            titre="déclaration du projet",
            agent=NOM_ORCHESTRATION,
            projet=lambda: projet[0] if projet else None,
            conversation="c1",
        ):
            report_usage(TOUR_DU_FIL)
            report_usage(TOUR_DU_FIL)
            projet.append("prj-recettes")  # c'est souvent le travail qui fait naître le projet

    asyncio.run(scenario())

    (depense,) = bus.publies
    assert depense.type == EVENEMENT_FIL_DEPENSE
    assert depense.run_id == ""  # à part de tout run
    assert depense.projet_id == "prj-recettes"
    assert depense.conversation == "c1"
    assert depense.titre == "déclaration du projet"
    assert depense.cout_usd == pytest.approx(0.10)
    assert depense.usage is not None
    assert depense.usage.tokens_total == 25_600
    # Le montant en clair, écrit comme l'écran l'écrit : le journal requêtable ne
    # garde que le texte.
    assert depense.detail == "0,10 $US · 25 600 tokens"
    assert en_clair(StepUsage(tokens_entree=900, cout_usd=0.004)) == "< 0,01 $US · 900 tokens"
    assert en_clair(StepUsage(tokens_entree=900)) == "900 tokens · coût pas encore tarifé"


def test_au_compte_du_fil_publie_meme_quand_l_echange_echoue_et_se_tait_sans_mesure() -> None:
    """Ce qui a été dépensé l'a été ; un bloc qui n'a rien mesuré ne publie rien."""
    bus = BusQuiRetient()

    async def en_panne() -> None:
        async with au_compte_du_fil(bus, titre="réponse du fil"):
            report_usage(TOUR_DU_FIL)
            raise RuntimeError("le répondeur a cassé")

    async def muet() -> None:
        async with au_compte_du_fil(bus, titre="réponse du fil"):
            pass

    with pytest.raises(RuntimeError):
        asyncio.run(en_panne())
    asyncio.run(muet())

    assert [e.cout_usd for e in bus.publies] == [pytest.approx(0.05)]


def _depense(agent: str, conversation: str, projet_id: str | None, cout: float) -> Event:
    """Une dépense du fil, telle que `au_compte_du_fil` la publie."""
    return Event(
        type=EVENEMENT_FIL_DEPENSE,
        agent=agent,
        titre="réponse du fil",
        projet_id=projet_id,
        conversation=conversation,
        usage=StepUsage(appels=1, tokens_entree=1000, cout_usd=cout),
        cout_usd=cout,
        horodatage="2026-10-15T09:00:00+00:00",
    )


def test_les_tours_d_avant_la_naissance_reviennent_au_projet_que_la_conversation_nomme() -> None:
    """La déclaration d'un projet se paie avant qu'il existe : elle lui revient quand même.

    Seulement dans **la même conversation du même agent** (`origine` est celle de
    chacun), et jamais par-dessus un projet déjà nommé.
    """
    state = ControlTowerState()
    for event in (
        _depense(NOM_ORCHESTRATION, "c1", None, 0.10),  # « je veux un site de recettes »
        _depense(NOM_ORCHESTRATION, "c2", None, 0.20),  # une autre conversation
        _depense("assistance", "c1", None, 0.04),  # un autre agent, même identifiant
        _depense(NOM_ORCHESTRATION, "c1", "prj-recettes", 0.30),  # le projet naît
        # Plus tard, la même conversation passe à un autre projet : ce qui est
        # rangé ne bouge plus.
        _depense(NOM_ORCHESTRATION, "c1", "prj-autre", 0.07),
    ):
        state.appliquer(event)

    def total(portee: PorteeProjet) -> float:
        return sum(e.cout_usd or 0.0 for e in state.depenses_du_fil(portee))

    assert total(PorteeProjet.projet("prj-recettes")) == pytest.approx(0.40)
    assert total(PorteeProjet.projet("prj-autre")) == pytest.approx(0.07)
    assert total(PorteeProjet.aucun()) == pytest.approx(0.24)


def test_couts_et_analytics_rangent_le_fil_a_part_du_run() -> None:
    """Le fil entre au total et à la série, sous son poste — jamais dans une exécution."""
    state = ControlTowerState()
    state.appliquer(
        Event(
            type=EVENEMENT_TACHE_STATUT,
            run_id="run-1",
            tache_id="page-liste",
            agent="dev",
            statut="terminee",
            projet_id="prj-recettes",
            usage=StepUsage(appels=2, tokens_entree=5000, cout_usd=1.0),
            cout_usd=1.0,
            horodatage="2026-10-15T10:00:00+00:00",
        )
    )
    state.appliquer(_depense(NOM_ORCHESTRATION, "c1", "prj-recettes", 0.30))
    state.appliquer(_depense(NOM_ORCHESTRATION, "c9", "prj-autre", 0.50))

    vue = agrege_couts(
        state.executions(),
        fil=state.depenses_du_fil(),
        portee=PorteeProjet.projet("prj-recettes"),
        pas=PAS_AUTO,
    )

    assert vue.total.cout_usd == pytest.approx(1.30)
    assert vue.fil is not None
    assert vue.fil.usage.cout_usd == pytest.approx(0.30)
    assert [e.usage.cout_usd for e in vue.executions] == [pytest.approx(1.0)]
    assert sum(p.usage.cout_usd or 0.0 for p in vue.serie) == pytest.approx(1.30)
    assert vue.to_dict()["fil"]["usage"]["cout_usd"] == pytest.approx(0.30)
    # Une fenêtre où le fil n'a rien dépensé n'a pas de poste : « 0,00 $ » dirait faux.
    assert agrege_couts(state.executions(), portee=PorteeProjet.aucun()).fil is None


class RepondeurQuiDepense(RepondeurScripte):
    """Le fil qui appelle le modèle : chaque réponse signale un tour, comme un fournisseur."""

    async def repondre(self, agent: Agent, fil: Sequence[MessageChat]) -> str:
        report_usage(TOUR_DU_FIL)
        return await super().repondre(agent, fil)


def test_une_reponse_du_fil_entre_au_grand_livre_par_la_vraie_route(tmp_path: Path) -> None:
    """De bout en bout : `POST /api/chat/orchestrateur/messages`, puis Coûts & analytics.

    Avant #1534, le tour était payé et la vue ne le voyait pas : `report_usage` ne
    trouvait aucun collecteur, et la mesure tombait dans le vide.
    """
    state = ControlTowerState()
    with TestClient(
        create_app(
            bus=InMemoryEventBus(),
            state=state,
            event_log=InMemoryEventLog(),
            battements=RegistreBattementsMemoire(),
            hote_run=HoteDouble(),
            chat_store=ChatStore(tmp_path / "chat"),
            orchestration_repondeur=RepondeurQuiDepense(),
        )
    ) as client:
        for contenu in ("Je veux un catalogue de recettes", "Avec une page liste"):
            envoi = client.post(
                f"/api/chat/{NOM_ORCHESTRATION}/messages",
                json={"contenu": contenu, "projet_id": "prj-recettes"},
            )
            assert envoi.status_code == 201, envoi.text
        _attendre(lambda: len(state.depenses_du_fil()) == 2, "les deux dépenses du fil")

        vue = _analytics(client)
        assert vue["fil"]["usage"]["cout_usd"] == pytest.approx(0.10)
        assert vue["total"]["cout_usd"] == pytest.approx(0.10)
        assert vue["executions"] == []
        assert {e.projet_id for e in state.depenses_du_fil()} == {"prj-recettes"}

        # La part que le cumul du shell ajoute aux grands livres des runs : le même
        # poste, par le même calcul, sans la série ni les tables.
        lecture = client.get("/api/fil/depense", params={"projet": "tous"})
        assert lecture.status_code == 200, lecture.text
        assert lecture.json()["fil"] == vue["fil"]
        assert client.get("/api/fil/depense").status_code == 422  # portée exigée (#277)
