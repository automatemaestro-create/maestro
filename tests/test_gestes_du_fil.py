"""Tests des **gestes sur un run depuis le fil** — pause, reprise, annulation, relance (#1179).

Le fil de l'orchestrateur ne savait qu'ouvrir un run : « mets-le en pause »,
« annule », « relance-le avec 5 $ de plus » repartaient en proposition de run neuf.
Il agit désormais sur les runs existants, par les **mêmes** services que les boutons
des écrans, et chaque geste qui a un effet est **proposé** sur une carte puis
**confirmé** par la personne.

Couvre les deux critères du ticket :

① **la carte, la confirmation, l'état relu** — un verdict `geste` sur un run pose
   la carte (`geste_run`) sans rien exécuter ; la confirmation, au clic ou d'un
   « oui » tapé, passe par le service (`agir`) et rend l'état **relu** du run
   (`geste_fait`), dont le modèle parle — « en pause depuis 14:02 ». Joué deux fois :
   sur un pilote double, où l'on voit ce que le répondeur demande au service, puis
   par l'API entière sur le **vrai** `ServiceExecutions`, pour les quatre gestes ;
② **l'ambiguïté et le refus** — une demande qui désigne plusieurs runs nomme ses
   candidats (`runs_candidats`) et n'agit pas ; un geste que l'état du run refuse
   n'est pas proposé, et celui que le temps fait refuser entre la carte et le clic
   se dit, motivé par le service.

Et ce que le service y gagne, parce que c'est la condition du « sans doublon » : les
règles de refus vivent **une fois** (`ServiceExecutions.refus_du_geste`), pour les
routes et pour le fil.

Aucun réseau, aucun modèle : le fournisseur est un double qui sépare le juge, la
rédaction et le tour de lecture par leur prompt système. La qualité du jugement —
« celui d'hier » désigne-t-il le bon run ? — n'est pas posée ici : on tient que ce que
le modèle désigne atteint le service par la carte, et rien d'autre.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.controltower.app import create_app
from maestro.controltower.bornes import AUCUNE_BORNE, BornesRun
from maestro.controltower.brief import evenement_demande_brief
from maestro.controltower.causes import CAUSE_PLAFOND_COUT
from maestro.controltower.chat import (
    ChatStore,
    GesteRunFait,
    GesteRunIntrouvable,
    GesteRunPropose,
    MessageChat,
    RunVise,
    geste_run_en_attente,
    transcription,
)
from maestro.controltower.events import (
    EVENEMENT_BRIEF_DECISION,
    EVENEMENT_EXECUTION_STATUT,
    Event,
    InMemoryEventBus,
)
from maestro.controltower.executions import (
    MOTIF_GESTE_INCONNU,
    MOTIF_GESTE_RUN_NON_SUSPENDU,
    MOTIF_GESTE_RUN_SUSPENDU,
    MOTIF_RELANCE_RUN_INCONNU,
    MOTIF_RELANCE_RUN_SOLDE,
    ServiceExecutions,
)
from maestro.controltower.gestes import (
    GESTE_ANNULATION,
    GESTE_PAUSE,
    GESTE_RELANCE,
    GESTE_REPRISE,
    GesteRefuse,
)
from maestro.controltower.orchestration import (
    _MARQUEUR_VERDICT,
    _PROMPT_CONSULTATION,
    _PROMPT_ORCHESTRATION,
    AGENT_ORCHESTRATION,
    NOM_ORCHESTRATION,
    VERDICT_ACCORD,
    VERDICT_GESTE,
    RepondeurOrchestration,
    faits_des_runs,
    heure_locale,
)
from maestro.controltower.state import (
    BRIEF_APPROUVE,
    EXECUTION_ANNULEE,
    EXECUTION_ECHEC,
    EXECUTION_EN_COURS,
    ORDRE_PAUSE,
    ORDRE_REPRISE,
    ControlTowerState,
)
from maestro.engine import MODE_BRIEF_HUMAIN, MODE_BRIEF_SANS, DemandeBrief, RunReport
from maestro.orchestrator import Brief
from maestro.providers.base import ModelProvider

UTILISATEUR = "utilisateur"

#: Deux runs réels du décor, et ce qu'on en dit.
RUN = "8a15f78f45d3"
AUTRE = "3ff0bcb065f9"
OBJECTIF = "Ajouter la pagination à la liste des projets"
OBJECTIF_AUTRE = "Corriger le tri des dates"

#: Ce que la rédaction écrit : une phrase qu'aucun gabarit du code ne contient, si
#: bien que `contenu == REDIGE` prouve que le modèle a parlé et que le code n'a rien
#: ajouté.
REDIGE = "C'est fait : il est suspendu, et rien de son travail n'est perdu."

#: L'heure de pause que le double pose — relue telle que le fil la dit.
PAUSE_A = "2026-09-27T12:02:00+00:00"


def _geste(
    action: str, *runs: str, reponse: str = "Je vous propose ce geste.", **extra: Any
) -> str:
    """La réponse du juge au contrat de #1222 : la prose, puis la ligne du verdict."""
    geste = {"action": action, "runs": list(runs), **extra}
    charge = {"verdict": VERDICT_GESTE, "objectif": "", "geste": geste}
    return f"{reponse}\n{_MARQUEUR_VERDICT} {json.dumps(charge, ensure_ascii=False)}"


def _dit(nom: str, reponse: str) -> str:
    charge = json.dumps({"verdict": nom, "objectif": ""}, ensure_ascii=False)
    return f"{reponse}\n{_MARQUEUR_VERDICT} {charge}"


class Juge(ModelProvider):
    """Juge, rédacteur et lecteur à la fois — séparés par leur prompt système.

    Le juge rend le verdict qu'on lui pose (`verdict`, modifiable entre deux
    tours) ; la rédaction d'un geste rend `REDIGE` et **note ses faits** — c'est là
    qu'on vérifie ce que le modèle apprend de l'état relu ; le tour de lecture ne
    demande rien.
    """

    name = "juge-des-gestes"

    def __init__(self, verdict: str = "") -> None:
        self.verdict = verdict or _dit("echange", "Bien noté.")
        self.jugements: list[str] = []
        self.redactions: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(
        self, prompt: str, *, model: str, system_prompt: str | None = None
    ) -> str:
        if system_prompt == _PROMPT_ORCHESTRATION:
            self.jugements.append(prompt)
            return self.verdict
        if system_prompt == _PROMPT_CONSULTATION:
            return "RIEN"
        self.redactions.append(prompt)
        return REDIGE


def _resume(
    run_id: str,
    objectif: str,
    statut: str = EXECUTION_EN_COURS,
    *,
    en_pause: bool = False,
    pause_depuis: str | None = None,
    projet_id: str | None = None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "objectif": objectif,
        "titre": objectif,
        "statut": statut,
        "en_pause": en_pause,
        "pause_depuis": pause_depuis,
        "cause": "",
        "projet_id": projet_id,
    }


class PiloteEspion:
    """Un `PiloteDesRuns` qui joue le service à la main et note ce qu'on lui demande.

    Il **refuse** ce qu'on lui dit de refuser — avant la carte (`refus`) ou au clic
    seulement (`refus_au_clic`, l'état qui change entre les deux) — et relit l'état
    que le geste a produit, comme le vrai service rend `resume_vivant`.
    """

    def __init__(self, *runs: dict[str, Any]) -> None:
        self.runs = {run["run_id"]: dict(run) for run in runs}
        self.refus: dict[tuple[str, str], GesteRefuse] = {}
        self.refus_au_clic: dict[tuple[str, str], GesteRefuse] = {}
        self.appels: list[tuple[str, str, BornesRun]] = []
        self.panne: Exception | None = None

    def resume(self, run_id: str) -> dict[str, Any] | None:
        run = self.runs.get(run_id)
        return dict(run) if run is not None else None

    async def refus_du_geste(self, geste: str, run_id: str) -> GesteRefuse | None:
        return self.refus.get((geste, run_id))

    async def agir(
        self, geste: str, run_id: str, *, bornes: BornesRun = AUCUNE_BORNE
    ) -> dict[str, Any]:
        self.appels.append((geste, run_id, bornes))
        if self.panne is not None:
            raise self.panne
        refus = self.refus_au_clic.get((geste, run_id)) or self.refus.get((geste, run_id))
        if refus is not None:
            raise refus
        run = self.runs[run_id]
        if geste == GESTE_PAUSE:
            run.update(en_pause=True, pause_depuis=PAUSE_A)
        elif geste == GESTE_REPRISE:
            run.update(en_pause=False, pause_depuis=None)
        elif geste == GESTE_ANNULATION:
            run.update(statut=EXECUTION_ANNULEE, en_pause=False, pause_depuis=None)
        else:
            run.update(statut=EXECUTION_ANNULEE)
            nouveau = _resume("run-suite", run["objectif"], projet_id=run.get("projet_id"))
            self.runs["run-suite"] = nouveau
            return dict(nouveau)
        return dict(run)


def _fil(*contenus: str) -> list[MessageChat]:
    return [
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=UTILISATEUR if rang % 2 == 0 else NOM_ORCHESTRATION,
            contenu=contenu,
        )
        for rang, contenu in enumerate(contenus)
    ]


def _proposer(
    pilote: PiloteEspion, verdict: str, message: str = "Mets-le en pause"
) -> tuple[Any, Juge]:
    juge = Juge(verdict)
    repondeur = RepondeurOrchestration(provider=juge, pilote=pilote)
    reponse = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil(message)))
    return reponse, juge


# ── ① la carte : proposée, jamais exécutée ────────────────────────────────────


def test_une_demande_sur_un_run_devient_une_carte_et_n_execute_rien() -> None:
    """Le verdict `geste` pose la carte — le run tel qu'on le montre — et c'est tout."""
    pilote = PiloteEspion(_resume(RUN, OBJECTIF))

    reponse, _ = _proposer(pilote, _geste(GESTE_PAUSE, RUN, reponse="Je le suspends ?"))

    assert pilote.appels == []
    assert reponse.contenu == "Je le suspends ?"
    carte = reponse.geste_run
    assert carte is not None
    assert carte.action == GESTE_PAUSE
    assert carte.run.run_id == RUN
    assert carte.run.titre == OBJECTIF
    # L'état en mots de l'écran (#571), jamais le code de la machine à états.
    assert carte.run.etat == "En cours"
    assert carte.bornes is None
    assert reponse.porte_une_demande


@pytest.mark.parametrize("action", [GESTE_PAUSE, GESTE_REPRISE, GESTE_ANNULATION, GESTE_RELANCE])
def test_les_quatre_gestes_se_proposent(action: str) -> None:
    pilote = PiloteEspion(_resume(RUN, OBJECTIF))

    reponse, _ = _proposer(pilote, _geste(action, RUN))

    assert reponse.geste_run is not None
    assert reponse.geste_run.action == action
    assert pilote.appels == []


def test_les_bornes_d_une_relance_voyagent_sur_la_carte() -> None:
    """« Relance-le avec 5 $ de plus » : les bornes du nouveau run, montrées avant."""
    pilote = PiloteEspion(_resume(RUN, OBJECTIF, EXECUTION_ECHEC))

    reponse, _ = _proposer(
        pilote,
        _geste(GESTE_RELANCE, RUN, bornes={"plafond_cout_usd": 15}),
        "relance-le avec 5 $ de plus",
    )

    assert reponse.geste_run is not None
    assert reponse.geste_run.bornes == BornesRun(plafond_cout_usd=15.0)


def test_des_bornes_sur_un_autre_geste_que_la_relance_ne_voyagent_pas() -> None:
    """Seule la relance ouvre un run, donc seule elle en reçoit."""
    pilote = PiloteEspion(_resume(RUN, OBJECTIF))

    reponse, _ = _proposer(pilote, _geste(GESTE_PAUSE, RUN, bornes={"plafond_cout_usd": 3}))

    assert reponse.geste_run is not None and reponse.geste_run.bornes is None


def test_une_action_hors_des_quatre_ne_pose_aucune_carte() -> None:
    """La liste est blanche : un verbe inventé n'atteint jamais le service."""
    pilote = PiloteEspion(_resume(RUN, OBJECTIF))

    reponse, _ = _proposer(pilote, _geste("supprimer", RUN, reponse="Je le supprime ?"))

    assert reponse.geste_run is None
    assert reponse.contenu == "Je le supprime ?"
    assert pilote.appels == []


def test_sans_pilote_le_fil_dit_qu_il_ne_peut_pas_agir() -> None:
    juge = Juge(_geste(GESTE_PAUSE, RUN))
    repondeur = RepondeurOrchestration(provider=juge)

    reponse = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil("pause")))

    assert reponse.geste_run is None
    assert "Aucun pilotage des runs" in reponse.contenu


def test_le_juge_sait_quels_runs_il_peut_designer_et_depuis_quand() -> None:
    """Les faits portent l'heure de lancement et la pause : « celui d'hier » se désigne."""
    state = ControlTowerState()
    state.appliquer(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN,
            statut=EXECUTION_EN_COURS,
            description=OBJECTIF,
            horodatage="2026-09-26T08:30:00+00:00",
        )
    )
    state.appliquer(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN,
            statut=ORDRE_PAUSE,
            horodatage=PAUSE_A,
        )
    )

    faits = faits_des_runs(state)(None, ())

    assert f"lancé : {heure_locale('2026-09-26T08:30:00+00:00')}" in faits
    assert f"en pause depuis {heure_locale(PAUSE_A)}" in faits


def test_le_contrat_du_juge_porte_le_verdict_geste_et_ses_quatre_actions() -> None:
    """Le verdict existe dans la consigne, avec ses actions et la règle des candidats."""
    assert '"geste"' in _PROMPT_ORCHESTRATION
    assert "pause|reprise|annulation|relance" in _PROMPT_ORCHESTRATION
    assert "mets-les TOUS" in _PROMPT_ORCHESTRATION


def test_le_juge_n_annonce_pas_une_carte_que_la_verification_peut_retirer() -> None:
    """Relecture visuelle de #1179 : « confirmez sur la carte ci-dessous », puis aucune carte.

    Le juge parle avant la vérification : une carte qu'il promet peut ne jamais venir.
    """
    assert "N'annonce pas de carte" in _PROMPT_ORCHESTRATION


# ── ② l'ambiguïté et le refus avant la carte ──────────────────────────────────


def test_une_demande_ambigue_nomme_ses_candidats_au_lieu_d_agir() -> None:
    """Deux runs désignés : aucune carte, les deux sur la réponse, rien d'exécuté."""
    pilote = PiloteEspion(_resume(RUN, OBJECTIF), _resume(AUTRE, OBJECTIF_AUTRE))
    question = "Deux runs tournent : celui de la pagination et celui du tri. Lequel ?"

    reponse, _ = _proposer(pilote, _geste(GESTE_PAUSE, RUN, AUTRE, reponse=question))

    assert reponse.geste_run is None
    assert not reponse.porte_une_demande
    assert [run.run_id for run in reponse.runs_candidats] == [RUN, AUTRE]
    assert [run.titre for run in reponse.runs_candidats] == [OBJECTIF, OBJECTIF_AUTRE]
    assert reponse.contenu == question
    assert pilote.appels == []


def test_un_run_inconnu_ne_pose_aucune_carte_et_le_dit() -> None:
    """Un identifiant que la projection ne connaît pas : l'empêchement est au code."""
    pilote = PiloteEspion(_resume(RUN, OBJECTIF))

    reponse, _ = _proposer(pilote, _geste(GESTE_ANNULATION, "run-imagine"))

    assert reponse.geste_run is None
    assert "run-imagine" in reponse.contenu
    assert "Je ne connais aucun run" in reponse.contenu


def test_un_seul_run_connu_parmi_deux_designes_pose_sa_carte() -> None:
    """Un identifiant inventé n'en fait pas une ambiguïté : il ne désigne rien."""
    pilote = PiloteEspion(_resume(RUN, OBJECTIF))

    reponse, _ = _proposer(pilote, _geste(GESTE_PAUSE, RUN, "run-imagine"))

    assert reponse.geste_run is not None and reponse.geste_run.run.run_id == RUN
    assert reponse.runs_candidats == ()


def test_un_geste_que_le_service_refuse_n_est_pas_propose_et_dit_pourquoi() -> None:
    """La règle du service, lue **avant** la carte : aucune confirmation ne l'honorerait."""
    pilote = PiloteEspion(_resume(RUN, OBJECTIF))
    pilote.refus[(GESTE_REPRISE, RUN)] = GesteRefuse(
        MOTIF_GESTE_RUN_NON_SUSPENDU,
        "il n'y a rien à reprendre d'un run qui n'a pas été mis en pause.",
    )

    reponse, _ = _proposer(pilote, _geste(GESTE_REPRISE, RUN), "reprends-le")

    assert reponse.geste_run is None
    # La phrase **corrige** ce que le modèle venait d'annoncer : elle dit qu'elle y revient,
    # et que la carte qu'il annonçait ne viendra pas.
    assert "aucune carte ne suivra" in reponse.contenu
    assert "je ne peux finalement pas vous proposer de reprendre ce run" in reponse.contenu
    # La raison est le **fait** du refus, marqué sous la bulle comme un refus au clic —
    # en prose, elle se lisait comme une réponse de plus (relecture visuelle de #1179).
    fait = reponse.geste_fait
    assert fait is not None and fait.action == GESTE_REPRISE and fait.run.run_id == RUN
    assert "pas été mis en pause" in fait.refus
    assert "pas été mis en pause" not in reponse.contenu


# ── ① la confirmation : exécutée par le service, l'état relu ──────────────────


def _carte(action: str = GESTE_PAUSE, bornes: BornesRun | None = None) -> GesteRunPropose:
    return GesteRunPropose(
        action=action, run=RunVise(run_id=RUN, titre=OBJECTIF, etat="En cours"), bornes=bornes
    )


def _trancher(
    pilote: PiloteEspion, demande: GesteRunPropose, *, approuve: bool = True
) -> tuple[Any, Juge]:
    juge = Juge()
    repondeur = RepondeurOrchestration(provider=juge, pilote=pilote)
    fil = _fil("Mets-le en pause", "Je le suspends ?", "Oui, mets ce run en pause.")
    reponse = asyncio.run(
        repondeur.trancher_geste(AGENT_ORCHESTRATION, fil, demande=demande, approuve=approuve)
    )
    return reponse, juge


def test_la_confirmation_passe_par_le_service_et_le_fil_dit_l_etat_relu() -> None:
    """« En pause depuis 14:02 » : relu après le geste, dit par le modèle, porté par un fait."""
    pilote = PiloteEspion(_resume(RUN, OBJECTIF))

    reponse, juge = _trancher(pilote, _carte())

    assert pilote.appels == [(GESTE_PAUSE, RUN, AUCUNE_BORNE)]
    fait = reponse.geste_fait
    assert fait is not None and not fait.refus
    assert fait.run.en_pause is True
    assert fait.run.pause_depuis == PAUSE_A
    assert reponse.contenu == REDIGE
    # Le modèle a reçu l'état relu, heure comprise — c'est ce qu'il doit dire.
    [redaction] = juge.redactions
    assert f"en pause depuis {heure_locale(PAUSE_A)}" in redaction
    # Aucune carte n'attend après : la rédaction dit la suite elle-même.
    assert not reponse.porte_une_demande
    assert juge.jugements == []


@pytest.mark.parametrize(
    ("action", "etat"),
    [
        (GESTE_REPRISE, f"relu juste après : run {RUN} « {OBJECTIF} » (En cours)."),
        (GESTE_ANNULATION, f"relu juste après : run {RUN} « {OBJECTIF} » (Annulée)."),
    ],
)
def test_reprise_et_annulation_rendent_leur_etat_relu(action: str, etat: str) -> None:
    """Le run suspendu est repris ou annulé ; ce que la rédaction lit est l'état **après**."""
    pilote = PiloteEspion(_resume(RUN, OBJECTIF, en_pause=True, pause_depuis=PAUSE_A))

    reponse, juge = _trancher(pilote, _carte(action))

    fait = reponse.geste_fait
    assert fait is not None and fait.run.en_pause is False
    assert fait.run.pause_depuis is None
    [redaction] = juge.redactions
    assert etat in redaction
    assert "en pause depuis" not in redaction


def test_une_relance_rattache_le_nouveau_run_et_ses_bornes_au_fil() -> None:
    """Le nouveau run est sur la réponse (`run_id`), le fait nomme les deux."""
    pilote = PiloteEspion(_resume(RUN, OBJECTIF, EXECUTION_ECHEC))
    bornes = BornesRun(plafond_cout_usd=15.0)

    reponse, juge = _trancher(pilote, _carte(GESTE_RELANCE, bornes))

    assert pilote.appels == [(GESTE_RELANCE, RUN, bornes)]
    assert reponse.run_id == "run-suite"
    fait = reponse.geste_fait
    assert fait is not None and fait.nouveau is not None
    assert fait.nouveau.run_id == "run-suite"
    [redaction] = juge.redactions
    assert "NOUVEAU run" in redaction
    assert bornes.en_phrase() in redaction


def test_un_refus_au_clic_se_dit_motive_et_rien_n_est_fait() -> None:
    """L'état a changé entre la carte et le clic : le service refuse, le fil dit pourquoi."""
    pilote = PiloteEspion(_resume(RUN, OBJECTIF, EXECUTION_ANNULEE))
    raison = f"exécution déjà soldée (annulee) : {RUN} — il n'y a rien à suspendre."
    pilote.refus_au_clic[(GESTE_PAUSE, RUN)] = GesteRefuse(MOTIF_RELANCE_RUN_SOLDE, raison)

    reponse, juge = _trancher(pilote, _carte())

    fait = reponse.geste_fait
    assert fait is not None
    assert fait.refus == raison
    # Relu pour le dire : c'est l'état qui a fait refuser.
    assert fait.run.statut == EXECUTION_ANNULEE
    assert reponse.contenu == REDIGE
    [redaction] = juge.redactions
    assert "REFUSÉ" in redaction and raison in redaction


def test_un_service_qui_casse_sans_motif_est_un_empechement_dit_par_le_code() -> None:
    pilote = PiloteEspion(_resume(RUN, OBJECTIF))
    pilote.panne = RuntimeError("Redis injoignable")

    reponse, juge = _trancher(pilote, _carte())

    assert reponse.geste_fait is None
    assert reponse.contenu.startswith("Je n'ai pas pu mettre en pause le run")
    assert "Redis injoignable" in reponse.contenu
    assert juge.redactions == []


def test_ecarter_la_carte_n_execute_rien_et_laisse_parler_le_modele() -> None:
    pilote = PiloteEspion(_resume(RUN, OBJECTIF))

    reponse, juge = _trancher(pilote, _carte(), approuve=False)

    assert pilote.appels == []
    assert reponse.geste_fait is None
    assert reponse.contenu == REDIGE
    [redaction] = juge.redactions
    assert "écarté" in redaction


def test_un_oui_tape_execute_ce_que_la_carte_montrait() -> None:
    """Le « oui » vaut le clic — et c'est la carte relue du fil qui part, pas le modèle."""
    pilote = PiloteEspion(_resume(RUN, OBJECTIF), _resume(AUTRE, OBJECTIF_AUTRE))
    juge = Juge(_dit(VERDICT_ACCORD, "Je le mets en pause."))
    repondeur = RepondeurOrchestration(provider=juge, pilote=pilote)
    fil = [
        MessageChat(agent=NOM_ORCHESTRATION, auteur=UTILISATEUR, contenu="pause"),
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=NOM_ORCHESTRATION,
            contenu="Je le suspends ?",
            geste_run=_carte(),
        ),
        MessageChat(agent=NOM_ORCHESTRATION, auteur=UTILISATEUR, contenu="oui vas-y"),
    ]

    reponse = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, fil))

    assert pilote.appels == [(GESTE_PAUSE, RUN, AUCUNE_BORNE)]
    assert reponse.geste_fait is not None and reponse.geste_fait.run.en_pause is True
    assert reponse.contenu == "Je le mets en pause."


def test_un_oui_tape_que_le_service_refuse_corrige_les_mots_du_juge() -> None:
    """Le juge a dit « je le fais » avant de savoir : le refus s'écrit à sa suite."""
    pilote = PiloteEspion(_resume(RUN, OBJECTIF))
    pilote.refus_au_clic[(GESTE_PAUSE, RUN)] = GesteRefuse(
        MOTIF_GESTE_RUN_SUSPENDU, "exécution déjà suspendue."
    )
    juge = Juge(_dit(VERDICT_ACCORD, "Je le mets en pause."))
    repondeur = RepondeurOrchestration(provider=juge, pilote=pilote)
    fil = [
        MessageChat(agent=NOM_ORCHESTRATION, auteur=UTILISATEUR, contenu="pause"),
        MessageChat(
            agent=NOM_ORCHESTRATION, auteur=NOM_ORCHESTRATION, contenu="?", geste_run=_carte()
        ),
        MessageChat(agent=NOM_ORCHESTRATION, auteur=UTILISATEUR, contenu="oui"),
    ]

    reponse = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, fil))

    assert reponse.contenu.startswith("Je le mets en pause.")
    assert "Ce geste n'a pas eu lieu : exécution déjà suspendue." in reponse.contenu
    assert reponse.geste_fait is not None and reponse.geste_fait.refus


def test_une_carte_attend_tant_que_rien_ne_l_a_suivie() -> None:
    """La règle des cinq autres demandes : le dernier message, et lui seul."""
    carte = MessageChat(
        agent=NOM_ORCHESTRATION, auteur=NOM_ORCHESTRATION, contenu="?", geste_run=_carte()
    )
    suite = MessageChat(agent=NOM_ORCHESTRATION, auteur=UTILISATEUR, contenu="non merci")

    assert geste_run_en_attente([carte]) is carte
    assert geste_run_en_attente([carte, suite]) is None


def test_un_repondeur_qui_ne_propose_aucun_geste_n_a_rien_a_trancher() -> None:
    from maestro.controltower.chat import RepondeurScripte

    with pytest.raises(GesteRunIntrouvable):
        asyncio.run(
            RepondeurScripte().trancher_geste(
                AGENT_ORCHESTRATION, [], demande=_carte(), approuve=True
            )
        )


def test_le_fil_relu_porte_la_carte_le_fait_et_les_candidats() -> None:
    """Le fil est la seule mémoire : un message persisté se relit avec ses gestes, et
    le tour suivant les voit dans la transcription — « le premier » sait de quoi il parle."""
    fait = GesteRunFait(
        GESTE_PAUSE,
        run=RunVise(run_id=RUN, en_pause=True, pause_depuis=PAUSE_A, etat="En cours — en pause"),
    )
    candidats = (RunVise(run_id=RUN, titre=OBJECTIF), RunVise(run_id=AUTRE, titre=OBJECTIF_AUTRE))
    messages = [
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=NOM_ORCHESTRATION,
            contenu="Lequel ?",
            runs_candidats=candidats,
        ),
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=NOM_ORCHESTRATION,
            contenu="?",
            geste_run=_carte(GESTE_RELANCE, BornesRun(plafond_cout_usd=5.0)),
        ),
        MessageChat(
            agent=NOM_ORCHESTRATION, auteur=NOM_ORCHESTRATION, contenu="ok", geste_fait=fait
        ),
    ]

    relus = [MessageChat.from_dict(json.loads(json.dumps(m.to_ligne()))) for m in messages]

    assert relus == messages
    texte = transcription(relus)
    assert f"run {AUTRE} « {OBJECTIF_AUTRE} »" in texte
    assert "[Geste proposé sur la carte : relancer le run" in texte
    assert "5,00 $" in texte
    assert "[Geste sur un run : mettre en pause le run" in texte


def test_le_fil_nomme_l_annulation_du_verbe_de_son_bouton() -> None:
    """« Interrompre » à l'écran (#467) : le clic ne s'écrit pas « annule » dans le fil.

    Vu sur la vraie stack : la carte disait « Interrompre », la trace du clic « Oui,
    annule ce run. » — deux mots pour le même geste, à une ligne d'écart.
    """
    from maestro.controltower.chat import _geste_sur_un_run

    assert _geste_sur_un_run(True, _carte(GESTE_ANNULATION)) == "Oui, interromps ce run."
    assert _carte(GESTE_ANNULATION).en_phrase().startswith("interrompre le run")


def test_l_etat_relu_d_un_run_annule_ne_redit_pas_son_statut() -> None:
    """« Annulée — Interrompu » : la cause d'une annulation redisait le statut, et le fil
    la citait mot pour mot. Une cause qui dit pourquoi — l'extinction — reste."""
    from maestro.controltower.causes import CAUSE_ANNULATION, CAUSE_EXTINCTION
    from maestro.controltower.orchestration import etat_du_run

    annule = {**_resume(RUN, OBJECTIF, EXECUTION_ANNULEE), "cause": CAUSE_ANNULATION}
    eteint = {**_resume(RUN, OBJECTIF, EXECUTION_ANNULEE), "cause": CAUSE_EXTINCTION}

    assert etat_du_run(annule) == "Annulée"
    assert etat_du_run(eteint) == "Annulée — Maestro s'est éteint"


def test_une_ligne_ecrite_avant_ce_lot_se_relit_sans_geste() -> None:
    message = MessageChat.from_dict(
        {"agent": NOM_ORCHESTRATION, "auteur": NOM_ORCHESTRATION, "contenu": "Bonjour"}
    )

    assert message.geste_run is None and message.geste_fait is None
    assert message.runs_candidats == ()


# ── Le service : les règles de refus, une fois pour les deux portes ────────────


def _service(state: ControlTowerState) -> ServiceExecutions:
    return ServiceExecutions(InMemoryEventBus(), state)


def _inscrit(state: ControlTowerState, run_id: str = RUN, statut: str = EXECUTION_EN_COURS) -> None:
    state.appliquer(
        Event(type=EVENEMENT_EXECUTION_STATUT, run_id=run_id, statut=statut, description=OBJECTIF)
    )


def _refus(state: ControlTowerState, geste: str, run_id: str = RUN) -> GesteRefuse | None:
    return asyncio.run(_service(state).refus_du_geste(geste, run_id))


def test_le_service_refuse_un_geste_sur_un_run_inconnu() -> None:
    refus = _refus(ControlTowerState(), GESTE_PAUSE, "run-fantome")

    assert refus is not None and refus.motif == MOTIF_RELANCE_RUN_INCONNU


def test_le_service_refuse_un_geste_qui_n_en_est_pas_un() -> None:
    state = ControlTowerState()
    _inscrit(state)

    refus = _refus(state, "supprimer")

    assert refus is not None and refus.motif == MOTIF_GESTE_INCONNU


@pytest.mark.parametrize("geste", [GESTE_PAUSE, GESTE_ANNULATION])
def test_un_run_solde_ne_se_suspend_ni_ne_s_annule(geste: str) -> None:
    state = ControlTowerState()
    _inscrit(state, statut=EXECUTION_ANNULEE)

    refus = _refus(state, geste)

    assert refus is not None and refus.motif == MOTIF_RELANCE_RUN_SOLDE
    # Le statut au libellé des écrans : la phrase s'affiche sous un bouton comme sous une
    # bulle, et « (annulee) » y rendait l'identifiant de la machine à états (#946).
    assert "(Annulée)" in str(refus)
    assert f"({EXECUTION_ANNULEE})" not in str(refus)


def test_une_pause_ne_se_pose_pas_deux_fois_et_une_reprise_veut_une_pause() -> None:
    state = ControlTowerState()
    _inscrit(state)

    assert _refus(state, GESTE_PAUSE) is None
    reprise = _refus(state, GESTE_REPRISE)
    assert reprise is not None and reprise.motif == MOTIF_GESTE_RUN_NON_SUSPENDU

    state.appliquer(Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=ORDRE_PAUSE))

    pause = _refus(state, GESTE_PAUSE)
    assert pause is not None and pause.motif == MOTIF_GESTE_RUN_SUSPENDU
    assert _refus(state, GESTE_REPRISE) is None
    # Une pause n'empêche pas l'annulation.
    assert _refus(state, GESTE_ANNULATION) is None


def test_agir_leve_le_refus_que_la_route_aurait_rendu() -> None:
    state = ControlTowerState()
    _inscrit(state, statut=EXECUTION_ANNULEE)

    with pytest.raises(GesteRefuse) as refus:
        asyncio.run(_service(state).agir(GESTE_PAUSE, RUN))

    assert refus.value.motif == MOTIF_RELANCE_RUN_SOLDE


def test_la_pause_porte_son_heure_et_la_perd_a_la_reprise() -> None:
    """« Depuis quand ? » : l'heure de l'ordre, gardée si la pompe le rediffuse."""
    state = ControlTowerState()
    _inscrit(state)
    pause = Event(
        type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=ORDRE_PAUSE, horodatage=PAUSE_A
    )

    state.appliquer(pause)
    state.appliquer(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN,
            statut=ORDRE_PAUSE,
            horodatage="2026-09-27T12:09:00+00:00",
        )
    )

    execution = state.execution(RUN)
    assert execution is not None
    assert execution.resume()["pause_depuis"] == PAUSE_A

    state.appliquer(Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=ORDRE_REPRISE))
    assert execution.resume()["pause_depuis"] is None


def test_un_run_solde_pendant_sa_pause_n_a_plus_d_heure_de_pause() -> None:
    state = ControlTowerState()
    _inscrit(state)
    state.appliquer(
        Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=ORDRE_PAUSE, horodatage=PAUSE_A)
    )
    state.appliquer(Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=EXECUTION_ANNULEE))

    execution = state.execution(RUN)
    assert execution is not None
    assert execution.resume()["en_pause"] is False
    assert execution.resume()["pause_depuis"] is None


# ── Bout en bout : l'API entière, le vrai service d'exécutions ─────────────────
#
# Le répondeur est celui que `create_app` construit — celui-là même qui tient le
# service des boutons —, et seul le fournisseur est substitué, à la fabrique (#195).


class MoteurEnVol:
    """Moteur injecté qui ne rend jamais la main, et note les garde-fous reçus.

    Les bornes d'une relance arrivent ici, en `guardrails` : c'est le seul endroit
    où l'on voit qu'elles ont traversé la carte, la confirmation et le service.
    """

    def __init__(self) -> None:
        self.garde_fous: list[tuple[float | None, int | None]] = []
        self.objectifs: list[str] = []

    def __call__(self, **reglages: Any) -> MoteurEnVol:
        gardes = reglages.get("guardrails")
        self.garde_fous.append(
            (getattr(gardes, "plafond_cout_usd", None), getattr(gardes, "plafond_tokens", None))
        )
        return self

    async def run(self, objectif: str, **_reste: Any) -> RunReport:
        self.objectifs.append(objectif)
        await asyncio.Event().wait()
        return RunReport(objectif=objectif, resultats=())  # pragma: no cover


@pytest.fixture()
def juge(monkeypatch: pytest.MonkeyPatch) -> Juge:
    juge = Juge()
    monkeypatch.setattr("maestro.providers.factory.provider_from_settings", lambda *a, **k: juge)
    return juge


@pytest.fixture()
def maison(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier utilisateur factice : aucun test n'écrit dans le vrai."""
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


def _app(state: ControlTowerState, tmp_path: Path, moteur: MoteurEnVol | None = None) -> TestClient:
    return TestClient(
        create_app(
            bus=InMemoryEventBus(),
            state=state,
            chat_store=ChatStore(tmp_path / "chat"),
            fabrique_moteur=moteur or MoteurEnVol(),
        )
    )


def _dire(client: TestClient, contenu: str) -> dict[str, Any]:
    reponse = client.post(f"/api/chat/{NOM_ORCHESTRATION}/messages", json={"contenu": contenu})
    assert reponse.status_code == 201, reponse.text
    return reponse.json()["messages"][1]


def _confirmer(client: TestClient, approuve: bool = True) -> Any:
    return client.post(f"/api/chat/{NOM_ORCHESTRATION}/geste", json={"approuve": approuve})


def _run(client: TestClient, run_id: str = RUN) -> dict[str, Any]:
    reponse = client.get(f"/api/executions/{run_id}")
    assert reponse.status_code == 200
    return reponse.json()


def test_de_bout_en_bout_la_pause_proposee_puis_confirmee_suspend_le_vrai_run(
    tmp_path: Path, maison: Path, juge: Juge
) -> None:
    state = ControlTowerState()
    _inscrit(state)
    juge.verdict = _geste(GESTE_PAUSE, RUN, reponse="Je mets ce run en pause ?")

    with _app(state, tmp_path) as client:
        carte = _dire(client, "mets le run en pause")
        # La carte est posée, et rien n'a bougé.
        assert carte["geste_run"]["action"] == GESTE_PAUSE
        assert carte["geste_run"]["run"]["run_id"] == RUN
        assert _run(client)["en_pause"] is False

        reponse = _confirmer(client)

        assert reponse.status_code == 201, reponse.text
        geste, suite = reponse.json()["messages"]
        assert geste["contenu"] == "Oui, mets ce run en pause."
        assert suite["contenu"] == REDIGE
        assert suite["geste_fait"]["run"]["en_pause"] is True
        assert suite["geste_fait"]["run"]["pause_depuis"]
        assert suite["geste_fait"]["refus"] == ""
        # Le vrai run est suspendu : c'est le service des boutons qui a agi.
        assert _run(client)["en_pause"] is True
        # Un second clic ne suspend pas deux fois.
        assert _confirmer(client).status_code == 409


@pytest.mark.parametrize(
    ("action", "avant", "apres"),
    [
        (GESTE_REPRISE, True, {"en_pause": False, "statut": EXECUTION_EN_COURS}),
        (GESTE_ANNULATION, False, {"statut": EXECUTION_ANNULEE}),
    ],
)
def test_de_bout_en_bout_reprise_et_annulation_passent_par_le_service(
    tmp_path: Path, maison: Path, juge: Juge, action: str, avant: bool, apres: dict[str, Any]
) -> None:
    state = ControlTowerState()
    _inscrit(state)
    if avant:
        state.appliquer(
            Event(
                type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=ORDRE_PAUSE, horodatage=PAUSE_A
            )
        )
    juge.verdict = _geste(action, RUN)

    with _app(state, tmp_path) as client:
        assert _dire(client, "fais-le")["geste_run"]["action"] == action

        reponse = _confirmer(client)

        assert reponse.status_code == 201, reponse.text
        relu = _run(client)
        for cle, valeur in apres.items():
            assert relu[cle] == valeur
        assert reponse.json()["messages"][1]["geste_fait"]["run"]["statut"] == relu["statut"]


def _run_arrete_sur_sa_borne(state: ControlTowerState) -> None:
    """Un run au brief approuvé, arrêté par son plafond de dépense — relançable (#1179)."""
    brief = Brief.from_dict(
        {
            "objectif": OBJECTIF,
            "perimetre": ["La liste des projets"],
            "hors_perimetre": [],
            "contraintes": [],
            "criteres_acceptation": ["Vingt projets par page"],
            "hypotheses": [],
            "questions": [],
        }
    )
    state.appliquer(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN,
            statut=EXECUTION_EN_COURS,
            description=OBJECTIF,
            mode_brief=MODE_BRIEF_HUMAIN,
        )
    )
    state.appliquer(
        evenement_demande_brief(DemandeBrief(run_id=RUN, objectif=OBJECTIF, brief=brief))
    )
    state.appliquer(
        Event(type=EVENEMENT_BRIEF_DECISION, run_id=RUN, statut=BRIEF_APPROUVE, brief=brief)
    )
    state.appliquer(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN,
            statut=EXECUTION_ECHEC,
            detail="PlafondDepenseDepasse : 10.02 $ > 10 $",
            cause=CAUSE_PLAFOND_COUT,
        )
    )


def test_de_bout_en_bout_relancer_avec_5_dollars_de_plus(
    tmp_path: Path, maison: Path, juge: Juge
) -> None:
    """Le run arrêté sur sa borne repart dans un nouveau run, avec les bornes dites."""
    state = ControlTowerState()
    _run_arrete_sur_sa_borne(state)
    moteur = MoteurEnVol()
    juge.verdict = _geste(GESTE_RELANCE, RUN, bornes={"plafond_cout_usd": 15})

    with _app(state, tmp_path, moteur) as client:
        carte = _dire(client, "relance-le avec 5 $ de plus")
        assert carte["geste_run"]["bornes"]["plafond_cout_usd"] == 15

        reponse = _confirmer(client)

        assert reponse.status_code == 201, reponse.text
        geste, suite = reponse.json()["messages"]
        assert "bornes : s'interrompt à 15,00 $" in geste["contenu"]
        nouveau = suite["run_id"]
        assert nouveau and nouveau != RUN
        assert suite["geste_fait"]["nouveau"]["run_id"] == nouveau
        assert _run(client, nouveau)["reprise_de"] == RUN
        assert _run(client)["statut"] == EXECUTION_ANNULEE
        # Les bornes ont traversé carte, confirmation et service jusqu'au moteur.
        assert moteur.garde_fous[-1][0] == 15.0
        assert _run(client, nouveau)["mode_brief"] == MODE_BRIEF_SANS


def test_de_bout_en_bout_un_run_solde_entre_la_carte_et_le_clic_est_refuse_et_dit(
    tmp_path: Path, maison: Path, juge: Juge
) -> None:
    """Le bouton de l'écran a annulé le run pendant que la carte attendait."""
    state = ControlTowerState()
    _inscrit(state)
    juge.verdict = _geste(GESTE_PAUSE, RUN)

    with _app(state, tmp_path) as client:
        _dire(client, "mets-le en pause")
        assert client.post(f"/api/executions/{RUN}/annuler").status_code == 200

        reponse = _confirmer(client)

        assert reponse.status_code == 201, reponse.text
        fait = reponse.json()["messages"][1]["geste_fait"]
        assert "déjà soldée" in fait["refus"]
        assert fait["run"]["statut"] == EXECUTION_ANNULEE
        assert "REFUSÉ" in juge.redactions[-1]
        assert _run(client)["en_pause"] is False


def test_de_bout_en_bout_deux_runs_possibles_sont_nommes_et_rien_n_attend(
    tmp_path: Path, maison: Path, juge: Juge
) -> None:
    state = ControlTowerState()
    _inscrit(state)
    _inscrit(state, AUTRE)
    juge.verdict = _geste(GESTE_ANNULATION, RUN, AUTRE, reponse="Lequel des deux ?")

    with _app(state, tmp_path) as client:
        reponse = _dire(client, "annule le run")

        assert reponse["geste_run"] is None
        assert {run["run_id"] for run in reponse["runs_candidats"]} == {RUN, AUTRE}
        assert _confirmer(client).status_code == 409
        assert _run(client)["statut"] == EXECUTION_EN_COURS
        assert _run(client, AUTRE)["statut"] == EXECUTION_EN_COURS


def test_de_bout_en_bout_un_geste_que_l_etat_refuse_ne_pose_pas_de_carte(
    tmp_path: Path, maison: Path, juge: Juge
) -> None:
    """Reprendre un run qui travaille : la règle du service, dite avant toute carte."""
    state = ControlTowerState()
    _inscrit(state)
    juge.verdict = _geste(GESTE_REPRISE, RUN, reponse="Je le reprends ?")

    with _app(state, tmp_path) as client:
        reponse = _dire(client, "reprends-le")

        assert reponse["geste_run"] is None
        assert "finalement pas vous proposer de reprendre" in reponse["contenu"]
        assert "pas été mis en pause" in reponse["geste_fait"]["refus"]
        assert _confirmer(client).status_code == 409
