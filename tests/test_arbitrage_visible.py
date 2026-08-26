"""Un run bloqué sur un arbitrage le dit, et la demande atteint l'écran (#572).

Lot final du parent #569, dont la panne d'origine (#568) était **muette** : elle ne
se manifestait que par un écran affirmant qu'il n'y avait rien à décider pendant
qu'un run dormait — 31 % de son temps de mur, débloqué par un `POST` à la main. Ce
n'est pas une suite de régression ordinaire : le dispositif tient à deux bouts (le
moteur qui **émet** la demande avec son run et son projet, la projection qui **pose**
l'attente sur l'exécution), et un défaut à l'un des deux redonne exactement la même
absence de symptôme.

Trois sections, qui sont les trois critères du lot :

① **L'ordre nominal**, et lui seul. Une demande de validation qui garde le démarrage
   de sa propre tâche est publiée **avant** que cette tâche n'existe pour qui que ce
   soit — c'est le cas *nominal* de toute tâche sensible, pas un cas limite, et c'est
   précisément l'ordre où le repli déductif de `state.py` (« le projet se lit sur la
   tâche déjà projetée ») ne peut rien. Un test qui projetterait la tâche d'abord
   passerait **sans** le correctif du lot 1 : un ✓ sur une question jamais posée. Le
   scénario est donc joué par un **vrai run** — le pont télémétrie (#46) et le
   validateur publient sur la même liste, dont l'ordre est l'ordre réel —, et le
   motif est prouvé sur l'échantillon fautif avant d'être vérifié sur le bon.

② **Les trois attentes humaines ensemble.** `en_attente_brief` (#320),
   `en_attente_reponses` (#321) et `en_attente_arbitrage` (#571) sont trois
   exemplaires d'un même motif ; ce qu'elles ont en commun est éprouvé **par une
   table**, et la table est confrontée à `STATUTS_EXECUTION_EN_ATTENTE`. Une
   quatrième attente ajoutée plus tard hérite donc du filet, ou fait rougir la
   confrontation — jamais elle n'en sort en silence.

③ **Ce qui n'appartient qu'à l'arbitrage** : les trois abstentions de la suspension
   et la demande encore en vol qui garde le run suspendu.

Ni réseau, ni Redis, ni fournisseur réel : la planification et l'exécution sont
pilotées par des `ModelProvider` factices, comme dans `tests/test_guardrails.py`.
Le versant UI de ces mêmes critères est dans `apps/web/tests/arbitrage.test.tsx`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from maestro.controltower.battement import VITALITE_VIVANT, vitalite
from maestro.controltower.bridge import activer_publication
from maestro.controltower.events import (
    EVENEMENT_BRIEF_DECISION,
    EVENEMENT_BRIEF_DEMANDE,
    EVENEMENT_BRIEF_QUESTIONS,
    EVENEMENT_BRIEF_REPONSES,
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DECISION,
    EVENEMENT_VALIDATION_DEMANDE,
    Event,
)
from maestro.controltower.portee import PorteeProjet
from maestro.controltower.state import (
    EXECUTION_ANNULEE,
    EXECUTION_EN_ATTENTE_ARBITRAGE,
    EXECUTION_EN_ATTENTE_BRIEF,
    EXECUTION_EN_ATTENTE_REPONSES,
    EXECUTION_EN_COURS,
    STATUTS_EXECUTION_EN_ATTENTE,
    STATUTS_EXECUTION_TERMINAUX,
    VALIDATION_APPROUVEE,
    VALIDATION_EN_ATTENTE,
    VALIDATION_REFUSEE,
    ControlTowerState,
)
from maestro.controltower.validation import appliquer_sous_validation, evenement_demande
from maestro.engine import MOTS_SENSIBLES, Guardrails, OrchestrationEngine
from maestro.engine.guardrails import DemandeValidation
from maestro.orchestrator import Orchestrator
from maestro.projets.modele import Perimetre, Projet
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal
from maestro.telemetry.journal import LOGGER_NAME

#: Le run, sa tâche sensible et son projet — les trois identités du scénario de #568.
RUN = "5f531654e03b"
TACHE = "deploiement"
PROJET = "prj-7f3a"


# --------------------------------------------------------------------------- #
# ① L'ordre nominal : la demande naît avant sa tâche
# --------------------------------------------------------------------------- #


class _Planificateur(ModelProvider):
    """Rend toujours le même plan : la décomposition n'est pas le sujet ici."""

    name = "plan-fige"

    def __init__(self, plan: str) -> None:
        self._plan = plan

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._plan


class _Executant(ModelProvider):
    """Exécutant factice : enregistre ses appels, pour prouver qu'un refus n'exécute rien."""

    name = "executant"

    def __init__(self) -> None:
        self.appels: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.appels.append(prompt)
        return "LIVRABLE"


class _ValidateurQuiPublie:
    """`ValidateurControlTower` réduit à son geste **amont** : publier la demande.

    Le validateur de production s'abonne au bus, publie, puis attend une décision
    humaine — cette plomberie-là est déjà couverte par `tests/test_controltower.py`
    et rendrait ce scénario indéterministe pour rien. Ce qui nous intéresse ici est
    ce qu'il **publie** et *quand*, donc l'événement est construit par le vrai
    `evenement_demande` : c'est la moitié aval du lot 1, et elle n'est pas simulée.
    """

    def __init__(self, recueil: list[Event], decision: bool) -> None:
        self._recueil = recueil
        self._decision = decision
        self.demandes: list[DemandeValidation] = []

    async def __call__(self, demande: DemandeValidation) -> bool:
        self.demandes.append(demande)
        self._recueil.append(evenement_demande(demande))
        return self._decision


_PLAN_SENSIBLE = json.dumps(
    [
        {
            "id": TACHE,
            "titre": "Déployer l'API",
            "description": "Mettre la nouvelle API en production.",
            "competences_requises": ["deploy"],
            "format_sortie": "Note",
            "dependances": [],
        }
    ],
    ensure_ascii=False,
)


@dataclass(frozen=True)
class Partie:
    """Ce qu'un run de tâche sensible laisse derrière lui : le bus, et l'exécutant."""

    evenements: list[Event]
    executant: _Executant


def _joue_une_tache_sensible(*, decision: bool = True) -> Partie:
    """Un run d'une seule tâche sensible, et **tout** ce que la Control Tower en voit.

    Le pont télémétrie (#46) publie sur la même liste que le validateur, et les deux
    écrivent en synchrone : l'ordre du recueil est donc l'ordre réel de publication,
    qui est le sujet du critère n°1. Le handler est retiré dans tous les cas — le
    logger `maestro.trace` est global, un handler oublié suivrait la suite entière.

    La classification par mots-clés est **armée explicitement** (#585 l'a désarmée
    par défaut) : ce qu'on éprouve ici est le trajet d'une `DemandeValidation`
    jusqu'aux vues, pas ce qui l'a produite. Le mot-clé reste le producteur le
    moins coûteux à déclencher depuis un test de bout en bout, et le trajet est
    le même pour les trois (politique `ask`, main levée de l'agent, diff).
    """
    recueil: list[Event] = []
    executant = _Executant()
    moteur = OrchestrationEngine(
        executant,
        Orchestrator(_Planificateur(_PLAN_SENSIBLE), model="claude-opus-4-8"),
        guardrails=Guardrails(
            validateur=_ValidateurQuiPublie(recueil, decision),
            mots_sensibles=MOTS_SENSIBLES,
        ),
    )
    handler = activer_publication(recueil.append)
    try:
        asyncio.run(
            moteur.run(
                "Mettre l'API en production",
                journal=RunJournal(run_id=RUN),
                projet_id=PROJET,
            )
        )
    finally:
        logging.getLogger(LOGGER_NAME).removeHandler(handler)
    return Partie(evenements=recueil, executant=executant)


def _la_demande(recueil: list[Event]) -> Event:
    """L'unique `validation.demande` du recueil — échoue le test s'il n'y en a pas."""
    demandes = [e for e in recueil if e.type == EVENEMENT_VALIDATION_DEMANDE]
    assert len(demandes) == 1, f"{len(demandes)} demande(s) de validation publiée(s)"
    return demandes[0]


def _projette(recueil: list[Event], *, jusqu_a_la_demande: bool = False) -> ControlTowerState:
    """Rejoue le recueil dans une projection neuve, dans l'ordre de publication.

    `jusqu_a_la_demande` arrête le rejeu **à l'instant du blocage** : c'est l'état
    que l'écran montrait pendant les treize minutes de #568, et le seul moment où la
    question « où est cette demande ? » se pose vraiment.
    """
    etat = ControlTowerState()
    for event in recueil:
        etat.appliquer(event)
        if jusqu_a_la_demande and event.type == EVENEMENT_VALIDATION_DEMANDE:
            break
    return etat


def test_la_demande_precede_le_premier_statut_de_sa_tache():
    """Critère n°1 : c'est l'ordre nominal, pas un cas limite.

    Une tâche sensible est stoppée **avant toute exécution** (`_realise_gardee`), donc
    son premier `tache.statut` — l'étape `<tâche>:debut` — n'est publié qu'après
    l'accord. Le repli déductif de `state.py` cherche le projet « sur la tâche déjà
    projetée » : à cet instant, il n'y a rien à interroger.
    """
    evenements = _joue_une_tache_sensible().evenements

    rang_demande = next(
        i for i, e in enumerate(evenements) if e.type == EVENEMENT_VALIDATION_DEMANDE
    )
    rangs_tache = [
        i
        for i, e in enumerate(evenements)
        if e.type == EVENEMENT_TACHE_STATUT and e.tache_id == TACHE
    ]

    assert rangs_tache, "la tâche n'a jamais publié de statut : le scénario est faux"
    assert rang_demande < min(rangs_tache)


def test_la_demande_porte_son_run_et_son_projet_des_sa_publication():
    """Le lot 1, vu du producteur : `run_id` et `projet_id` sont sur l'événement.

    Rien en aval ne peut les recoller — c'est tout l'argument de #570 —, donc c'est
    ici qu'ils doivent être, et pas ailleurs.
    """
    demande = _la_demande(_joue_une_tache_sensible().evenements)

    assert demande.run_id == RUN
    assert demande.projet_id == PROJET
    assert demande.tache_id == TACHE
    assert demande.statut == VALIDATION_EN_ATTENTE


def test_projetee_dans_cet_ordre_la_demande_reste_dans_la_vue_du_projet():
    """Le lot 1, vu du consommateur : la demande est là où on la cherche.

    Les trois chemins d'affichage coupés par #568, dans l'ordre où le ticket les
    nomme : la portée projet qui cadre **tous** les écrans, le journal du run, et le
    run lui-même qui ne changeait d'aucun champ.
    """
    etat = _projette(_joue_une_tache_sensible().evenements, jusqu_a_la_demande=True)

    # La tâche n'existe pour personne : c'est bien l'ordre nominal qui est projeté.
    assert etat.taches(PorteeProjet.tous()) == []

    (validation,) = etat.validations(PorteeProjet.projet(PROJET))
    assert validation.tache_id == TACHE and validation.en_attente
    assert validation.to_dict()["run_id"] == RUN  # « quel run attend ? », enfin servi

    execution = etat.execution(RUN)
    assert execution is not None
    assert any(e.type == EVENEMENT_VALIDATION_DEMANDE for e in execution.evenements)

    resume = execution.resume()
    assert resume["statut"] == EXECUTION_EN_ATTENTE_ARBITRAGE
    assert resume["attente_depuis"] == validation.horodatage


def test_sans_run_ni_projet_sur_l_evenement_la_demande_n_est_nulle_part():
    """Le **motif** du test ci-dessus, prouvé sur l'échantillon fautif (#568).

    Le même événement, ramené à ce qu'il portait avant le lot 1, et rejoué dans le
    même ordre : il reste projeté — il a toujours existé — mais sort des trois vues
    où quelqu'un le chercherait. Sans cette moitié, le test précédent pourrait passer
    pour une raison qui n'est pas la sienne.
    """
    publiee = _la_demande(_joue_une_tache_sensible().evenements)
    avant_le_lot_1 = replace(publiee, run_id="", projet_id=None)

    etat = ControlTowerState()
    etat.appliquer(avant_le_lot_1)

    assert etat.validations(PorteeProjet.projet(PROJET)) == []  # écartée de l'écran
    assert etat.execution(RUN) is None  # absente du journal du run, et du run
    assert etat.validations(PorteeProjet.tous())  # elle existe pourtant : c'est la panne


@pytest.fixture()
def maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `Path.home()` sous `tmp_path`, comme dans les tests du socle (#221, #224).

    Indispensable sous Windows : le `tmp_path` de pytest vit sous `AppData`, que
    `valider_racine` interdit à juste titre — sans cette isolation, la racine de
    projet ci-dessous serait refusée avant même la demande.
    """
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


def test_l_autre_producteur_porte_lui_aussi_son_run_et_son_projet(tmp_path: Path, maison_isolee):
    """`appliquer_sous_validation` (#227) est la seconde demande du produit.

    Le projet est le **sujet** de la question, le run vient de l'appelant : les deux
    branches du lot 1, sur le producteur que le scénario du moteur ne traverse pas.
    """
    racine = tmp_path / "depensio"
    (racine / "src").mkdir(parents=True)
    (racine / "src" / "app.py").write_text("un\ndeux\n", encoding="utf-8")
    espace = tmp_path / "espace"
    shutil.copytree(racine, espace)
    (espace / "src" / "neuf.py").write_text("neuf\n", encoding="utf-8")
    projet = Projet(
        id=PROJET, nom="Dépensio", racine=racine.as_posix(), vcs=None, perimetre=Perimetre()
    )
    demandes: list[DemandeValidation] = []

    def valide(demande: DemandeValidation) -> bool:
        demandes.append(demande)
        return True

    asyncio.run(
        appliquer_sous_validation(
            projet, tache_id="t1", validateur=valide, espace=espace, run_id=RUN
        )
    )

    (demande,) = demandes
    assert demande.run_id == RUN and demande.projet_id == PROJET
    evenement = evenement_demande(demande)
    assert evenement.run_id == RUN and evenement.projet_id == PROJET


# --------------------------------------------------------------------------- #
# ② Les trois attentes humaines, éprouvées ensemble
# --------------------------------------------------------------------------- #


Geste = Callable[[ControlTowerState], None]


@dataclass(frozen=True)
class Issue:
    """Une façon de sortir d'une attente, et l'état où elle laisse le run."""

    nom: str
    geste: Geste
    statut_apres: str


@dataclass(frozen=True)
class Attente:
    """Une attente humaine : comment on y entre, et comment on en sort.

    Le run est **toujours** `en_cours` avant `suspend` — c'est l'état d'un run en vol,
    et les trois attentes s'y posent de la même façon.
    """

    nom: str
    statut: str
    suspend: Geste
    issues: tuple[Issue, ...]


def _evenement(type_: str, **champs) -> Geste:
    """Un geste qui applique un seul événement portant le run du scénario."""

    def geste(etat: ControlTowerState) -> None:
        etat.appliquer(Event(type=type_, run_id=RUN, **champs))

    return geste


def _decision_de_validation(statut: str) -> Geste:
    """La décision telle que l'API la publie (`app.py`) : **sans `run_id`**.

    Ce n'est pas un raccourci de test, c'est le contrat : `validation.decision` porte
    la tâche, jamais le run, et c'est pour ça que `_libere_de_arbitrage` lit l'identité
    du run sur la **demande projetée**. Un événement de décision muni d'un `run_id` ne
    prouverait pas ce que ce test doit prouver.
    """

    def geste(etat: ControlTowerState) -> None:
        etat.appliquer(
            Event(type=EVENEMENT_VALIDATION_DECISION, tache_id=TACHE, statut=statut)
        )

    return geste


#: Les trois attentes, telles que la projection les pose et les lève.
#:
#: L'issue **défavorable** n'a pas la même nature partout, et la table le dit plutôt
#: que de l'aplatir : on refuse un brief, on refuse un arbitrage, mais on ne « refuse »
#: pas des questions — le geste symétrique y est l'annulation du run, éprouvée pour les
#: trois par `test_une_annulation_en_pleine_attente_efface_l_anciennete`. Ce qui est
#: commun n'est pas la route, c'est l'invariant : quelle que soit l'issue, le run cesse
#: d'attendre et son ancienneté disparaît avec l'attente.
ATTENTES: tuple[Attente, ...] = (
    Attente(
        nom="brief",
        statut=EXECUTION_EN_ATTENTE_BRIEF,
        suspend=_evenement(EVENEMENT_BRIEF_DEMANDE, horodatage="2026-08-26T09:00:00+00:00"),
        issues=(
            Issue(
                "accord",
                _evenement(EVENEMENT_BRIEF_DECISION, statut=VALIDATION_APPROUVEE),
                EXECUTION_EN_COURS,
            ),
            Issue(
                "refus",
                _evenement(EVENEMENT_BRIEF_DECISION, statut=VALIDATION_REFUSEE),
                EXECUTION_ANNULEE,
            ),
        ),
    ),
    Attente(
        nom="réponses",
        statut=EXECUTION_EN_ATTENTE_REPONSES,
        suspend=_evenement(
            EVENEMENT_BRIEF_QUESTIONS,
            tour=1,
            tours_max=2,
            horodatage="2026-08-26T09:05:00+00:00",
        ),
        issues=(
            Issue("réponses", _evenement(EVENEMENT_BRIEF_REPONSES), EXECUTION_EN_COURS),
        ),
    ),
    Attente(
        nom="arbitrage",
        statut=EXECUTION_EN_ATTENTE_ARBITRAGE,
        suspend=_evenement(
            EVENEMENT_VALIDATION_DEMANDE,
            tache_id=TACHE,
            projet_id=PROJET,
            statut=VALIDATION_EN_ATTENTE,
            horodatage="2026-08-26T09:10:00+00:00",
        ),
        issues=(
            Issue("accord", _decision_de_validation(VALIDATION_APPROUVEE), EXECUTION_EN_COURS),
            Issue("refus", _decision_de_validation(VALIDATION_REFUSEE), EXECUTION_EN_COURS),
        ),
    ),
)

#: `(attente, issue)` aplati, pour que chaque cas porte un identifiant lisible.
ISSUES: tuple[tuple[Attente, Issue], ...] = tuple(
    (attente, issue) for attente in ATTENTES for issue in attente.issues
)


def _run_en_vol() -> ControlTowerState:
    """Une projection portant un run `en_cours`, avant toute attente."""
    etat = ControlTowerState()
    etat.appliquer(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN,
            titre="Mettre l'API en production",
            statut=EXECUTION_EN_COURS,
            projet_id=PROJET,
        )
    )
    return etat


@pytest.mark.parametrize("attente", ATTENTES, ids=lambda a: a.nom)
def test_la_table_des_attentes_est_celle_du_backend(attente: Attente):
    """Chaque entrée de la table désigne bien un statut d'attente du backend."""
    assert attente.statut in STATUTS_EXECUTION_EN_ATTENTE


def test_aucune_attente_n_echappe_a_la_table():
    """Le filet dont hérite une **quatrième** attente ajoutée plus tard (critère n°2).

    C'est la moitié du critère qui ne se voit pas : sans cette confrontation, un
    `en_attente_<quelque chose>` de plus s'ajouterait à `STATUTS_EXECUTION_EN_ATTENTE`
    sans qu'aucun des tests ci-dessous ne le regarde — et il en sortirait exactement
    comme `en_attente_arbitrage` en est sorti, c'est-à-dire en silence.
    """
    assert {attente.statut for attente in ATTENTES} == set(STATUTS_EXECUTION_EN_ATTENTE)


@pytest.mark.parametrize("attente", ATTENTES, ids=lambda a: a.nom)
def test_l_attente_pose_son_statut_et_son_anciennete(attente: Attente):
    """« Quelle attente ? » et « depuis quand ? » — les deux moitiés du signal.

    Sans l'ancienneté, une attente est indiscernable d'un run planté ; sans le statut,
    elle est indiscernable d'un run qui travaille. C'est ce second défaut que #568 a
    mesuré : 88 relevés de `GET /api/executions/{id}` strictement identiques entre
    « bloqué » et « au travail ».
    """
    etat = _run_en_vol()

    attente.suspend(etat)

    resume = etat.execution(RUN).resume()
    assert resume["statut"] == attente.statut
    assert resume["attente_depuis"]


@pytest.mark.parametrize("attente", ATTENTES, ids=lambda a: a.nom)
def test_une_attente_n_est_jamais_soldee(attente: Attente):
    """Un run suspendu est en vol : il reste annulable, et il n'a pas d'issue."""
    assert attente.statut not in STATUTS_EXECUTION_TERMINAUX

    etat = _run_en_vol()
    attente.suspend(etat)

    assert etat.execution(RUN).resume()["fin"] is None


@pytest.mark.parametrize(("attente", "issue"), ISSUES, ids=lambda x: getattr(x, "nom", x))
def test_toute_issue_retire_l_anciennete(attente: Attente, issue: Issue):
    """Le retrait vaut **sur refus autant que sur accord** (critère n°2).

    Ne lever l'attente que sur la réponse favorable laisserait un run refusé « en
    attente » pour toujours, avec un compteur d'ancienneté qui court : la promesse
    fausse que ce chantier supprime, retournée.
    """
    etat = _run_en_vol()
    attente.suspend(etat)
    assert etat.execution(RUN).attente_depuis  # l'attente est bien posée avant l'issue

    issue.geste(etat)

    resume = etat.execution(RUN).resume()
    assert resume["statut"] == issue.statut_apres
    assert resume["statut"] not in STATUTS_EXECUTION_EN_ATTENTE
    assert resume["attente_depuis"] is None


@pytest.mark.parametrize("attente", ATTENTES, ids=lambda a: a.nom)
def test_une_annulation_en_pleine_attente_efface_l_anciennete(attente: Attente):
    """Le geste symétrique commun aux trois : arrêter le run au lieu de lui répondre.

    C'est l'issue *défavorable* de l'attente de réponses, qui n'en a pas d'autre, et
    c'est un chemin que les deux autres partagent — d'où un test pour les trois plutôt
    qu'une colonne de plus dans la table.
    """
    etat = _run_en_vol()
    attente.suspend(etat)

    etat.appliquer(
        Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=EXECUTION_ANNULEE)
    )

    resume = etat.execution(RUN).resume()
    assert resume["statut"] == EXECUTION_ANNULEE
    assert resume["attente_depuis"] is None


@pytest.mark.parametrize("attente", ATTENTES, ids=lambda a: a.nom)
def test_la_vitalite_ne_distingue_pas_une_attente_d_un_travail(attente: Attente):
    """Une attente humaine **garde** un verdict de vitalité, et c'est voulu (#348).

    Le run est suspendu, son hôte bat toujours : `vitalite` répond `vivant`, exactement
    comme sur un run qui travaille. C'est ce qui rend le statut indispensable — « ce run
    attend-il quelqu'un ? » ne se lit pas ici, et #568 l'a payé en croyant l'inverse
    (« le cœur bat, la vitalité dit `vivant` »). Seuls les statuts **terminaux** rendent
    la question sans objet.
    """
    battement, maintenant = "2026-08-26T09:00:00+00:00", datetime(2026, 8, 26, 9, 5, tzinfo=UTC)

    assert vitalite(attente.statut, battement, maintenant=maintenant) == VITALITE_VIVANT
    assert vitalite(EXECUTION_ANNULEE, battement, maintenant=maintenant) is None


# --------------------------------------------------------------------------- #
# ③ Ce qui n'appartient qu'à l'arbitrage
# --------------------------------------------------------------------------- #


def _demande(tache_id: str, horodatage: str, *, run_id: str = RUN) -> Event:
    """Une `validation.demande` du scénario, datée — l'ancienneté est le sujet."""
    return Event(
        type=EVENEMENT_VALIDATION_DEMANDE,
        tache_id=tache_id,
        run_id=run_id,
        projet_id=PROJET,
        statut=VALIDATION_EN_ATTENTE,
        horodatage=horodatage,
    )


def test_l_anciennete_est_celle_de_la_premiere_demande():
    """Trois tâches sensibles sur trois (#568) : « depuis quand ? » n'a qu'une réponse.

    Repousser l'horodatage à chaque nouvelle demande rajeunirait indéfiniment une
    attente qui dure — c'est-à-dire détruirait l'information qu'on vient poser.
    """
    etat = _run_en_vol()

    etat.appliquer(_demande("t1", "2026-08-26T09:00:00+00:00"))
    etat.appliquer(_demande("t2", "2026-08-26T09:07:00+00:00"))
    etat.appliquer(_demande("t3", "2026-08-26T09:13:00+00:00"))

    assert etat.execution(RUN).attente_depuis == "2026-08-26T09:00:00+00:00"


def test_une_seconde_demande_en_vol_garde_le_run_suspendu():
    """Trancher la première de trois ne rend pas au run un « en cours » qu'il n'a pas."""
    etat = _run_en_vol()
    etat.appliquer(_demande("t1", "2026-08-26T09:00:00+00:00"))
    etat.appliquer(_demande("t2", "2026-08-26T09:07:00+00:00"))

    etat.appliquer(
        Event(type=EVENEMENT_VALIDATION_DECISION, tache_id="t1", statut=VALIDATION_APPROUVEE)
    )

    execution = etat.execution(RUN)
    assert execution.statut == EXECUTION_EN_ATTENTE_ARBITRAGE
    assert execution.attente_depuis == "2026-08-26T09:00:00+00:00"

    etat.appliquer(
        Event(type=EVENEMENT_VALIDATION_DECISION, tache_id="t2", statut=VALIDATION_REFUSEE)
    )

    assert etat.execution(RUN).statut == EXECUTION_EN_COURS
    assert etat.execution(RUN).attente_depuis is None


def test_une_demande_sans_run_ne_suspend_rien():
    """Un producteur qui ne porte pas son run est projeté comme avant, sans plus.

    C'est le filet du lot 1 vu de l'autre côté : la demande existe, elle est
    consultable sous la portée transverse, mais aucun run ne se déclare en attente —
    il n'y a rien à suspendre.
    """
    etat = _run_en_vol()

    etat.appliquer(_demande(TACHE, "2026-08-26T09:00:00+00:00", run_id=""))

    assert etat.validations(PorteeProjet.tous())
    assert etat.execution(RUN).statut == EXECUTION_EN_COURS
    assert etat.execution(RUN).attente_depuis is None


def test_une_demande_sur_un_run_solde_ne_le_ressuscite_pas():
    """Rediffusion, journal rejoué dans le désordre : un run annulé le reste."""
    etat = _run_en_vol()
    etat.appliquer(
        Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=EXECUTION_ANNULEE)
    )

    etat.appliquer(_demande(TACHE, "2026-08-26T09:00:00+00:00"))

    execution = etat.execution(RUN)
    assert execution.statut == EXECUTION_ANNULEE
    assert execution.attente_depuis is None
    assert etat.validation(TACHE) is not None  # la demande est projetée, le run ne bouge pas


def test_une_decision_sur_un_run_qui_n_attendait_pas_ne_le_touche_pas():
    """Décision rejouée, ou run annulé pendant l'attente : rien à remettre en vol."""
    etat = _run_en_vol()
    etat.appliquer(_demande(TACHE, "2026-08-26T09:00:00+00:00"))
    etat.appliquer(
        Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=EXECUTION_ANNULEE)
    )

    etat.appliquer(
        Event(type=EVENEMENT_VALIDATION_DECISION, tache_id=TACHE, statut=VALIDATION_APPROUVEE)
    )

    assert etat.execution(RUN).statut == EXECUTION_ANNULEE


def test_un_refus_stoppe_la_tache_et_libere_le_run_comme_un_accord():
    """Bout en bout : rien n'est exécuté, et le run repart quand même (critère n°2).

    Deux moitiés, et la seconde est celle qu'une lecture littérale de « les retire à
    la décision » laisserait tomber : un refus rend la main au moteur aussi sûrement
    qu'une approbation, donc le run cesse d'attendre. La décision est appliquée comme
    l'API la publie — sur la **tâche**, sans `run_id` : c'est la demande projetée qui
    dit quel run repart.
    """
    partie = _joue_une_tache_sensible(decision=False)

    assert partie.executant.appels == []  # stoppée avant toute exécution
    etat = _projette(partie.evenements)
    assert etat.execution(RUN).statut == EXECUTION_EN_ATTENTE_ARBITRAGE

    etat.appliquer(
        Event(type=EVENEMENT_VALIDATION_DECISION, tache_id=TACHE, statut=VALIDATION_REFUSEE)
    )

    execution = etat.execution(RUN)
    assert execution.statut == EXECUTION_EN_COURS
    assert execution.attente_depuis is None
