"""Pourquoi les tâches d'un run passent une à une, et ce qui les libère (ticket #1298).

Le retour d'expérience du 2026-09-24 : *« je n'ai jamais remarqué un parallélisme
dans le traitement des tâches »*. Le moteur savait paralléliser ; trois choses l'en
empêchaient, et aucun écran ne le disait. Cette suite garde, dans l'ordre où le fait
remonte :

① **La cause, lue dans le moteur** (`maestro.engine.cadence`) — un plan en chaîne,
   un plan large sur un projet non versionné, rien sur un plan large versionné ;
② **la boucle la dit et propose** — sur un projet non versionné, la carte d'une
   question du fil propose de le versionner, avec sa raison, **sans que le run
   l'attende** ; l'accord en fait un dépôt Git local et les tâches suivantes
   partent chacune dans sa copie, de front ; un refus, une phrase, un silence ou un
   run fini avant la réponse laissent tout en l'état — et chaque issue est dite ;
③ **l'exécuteur tient le passage** — le versionnement accordé passe devant les
   tâches qui attendaient l'atelier (#839), une tâche qui a écrit en place se solde
   en place, et un agent au complet dit une fois qu'il fait passer ses tâches une à
   une ;
④ **le transport** — l'étape `cadence` devient une activité du run qui porte sa
   cause, sans carte fantôme au Kanban ni tâche fantôme au grand livre ;
⑤ **la vue et le fil** — le graphe servi porte la mention des causes qui tiennent
   (aucune une fois le projet versionné), et les faits du run que le fil lit en
   portent la phrase.

Vrais dépôts jetables et vraie mise sous Git (sautés sans `git`), vrais worktrees,
fournisseurs factices. Ce qui doit être simultané l'est par une **barrière**, jamais
par un `sleep` (#292).
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.agents.capacity import CapacityStore, JaugeInstances
from maestro.controltower import (
    EVENEMENT_RUN_PLAN,
    ControlTowerState,
    Event,
    InMemoryEventBus,
    InMemoryEventLog,
    create_app,
)
from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.events import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_QUESTION_DEMANDE,
    EVENEMENT_QUESTION_REPONSE,
)
from maestro.controltower.orchestration import fiche_du_run
from maestro.controltower.question import (
    ArbitreQuestionControlTower,
    evenement_question,
    evenement_retrait,
)
from maestro.controltower.regime import CE_QUI_NE_SE_PREVOIT_PAS
from maestro.controltower.state import (
    EVENEMENT_EXECUTION_STATUT,
    EXECUTION_EN_COURS,
    QUESTION_EN_ATTENTE,
    QUESTION_REPONDUE,
    QUESTION_RETIREE,
)
from maestro.engine import OrchestrationEngine
from maestro.engine.cadence import (
    CAUSE_CHAINE,
    CAUSE_INSTANCES,
    CAUSE_PROJET_NON_VERSIONNE,
    CHOIX_GARDER,
    CHOIX_VERSIONNER,
    HYPOTHESE_VERSIONNEMENT,
    PROPOSITION_ACCEPTEE,
    PROPOSITION_EN_ATTENTE,
    STATUT_PROJET_VERSIONNE,
    STATUT_UNE_A_UNE,
    STATUT_VERSIONNEMENT_DECLINE,
    STATUT_VERSIONNEMENT_SANS_REPONSE,
    STATUT_VERSIONNEMENT_SANS_SUITE,
    Cadence,
    avec_proposition,
    cadence_du_plan,
    consigne_cadence,
    largeur_du_plan,
)
from maestro.engine.executor import (
    ACTEUR_ORCHESTRATEUR,
    ROLE_ORCHESTRATEUR,
    STATUT_ECRITURE_EN_PLACE,
    STATUT_FUSION_FAITE,
    LocalExecutor,
)
from maestro.engine.guardrails import DemandeValidation, Guardrails
from maestro.engine.questions import DemandeQuestion
from maestro.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task
from maestro.plan_run import NoeudPlan
from maestro.projets import MESSAGE_PREMIER_COMMIT, Projet, ProjetStore, detecter_vcs
from maestro.providers.arbitrage import BornesArbitrage
from maestro.providers.base import ModelProvider
from maestro.telemetry import ETAPE_CADENCE, RunCost, RunJournal

GIT = shutil.which("git")
avec_git = pytest.mark.skipif(GIT is None, reason="git introuvable")

RUN = "run-1298"
PROJET = "prj-0001"


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`Path.home()` sous `tmp_path` (#221) : sous Windows, `tmp_path` vit sous `AppData`."""
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


@pytest.fixture(autouse=True)
def _sans_identite_git_ambiante(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Coupe l'identité Git du poste (#333) : le premier commit passe par le `-c` du code."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig-absent"))


# --------------------------------------------------------------------------- #
# Fabriques
# --------------------------------------------------------------------------- #


def _git(cwd: Path, *arguments: str) -> str:
    resultat = subprocess.run(
        [GIT or "git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert resultat.returncode == 0, f"git {' '.join(arguments)} : {resultat.stderr}"
    return resultat.stdout


def _projet_nu(tmp_path: Path, nom: str = "p3") -> tuple[ProjetStore, Projet]:
    """Un dépôt de projets et un projet **non versionné** — le `p3` du retour d'expérience."""
    racine = tmp_path / "projets" / nom
    racine.mkdir(parents=True)
    (racine / "README.md").write_text("# p3\n", encoding="utf-8")
    depot = ProjetStore(tmp_path / "depot")
    projet = depot.creer(nom, racine)
    assert not projet.versionne, projet
    return depot, projet


def _projet_git(tmp_path: Path) -> tuple[ProjetStore, Projet]:
    """Un projet **versionné** : dépôt jetable, un commit, VCS détecté."""
    racine = tmp_path / "projets" / "versionne"
    racine.mkdir(parents=True)
    (racine / "README.md").write_text("# versionné\n", encoding="utf-8")
    _git(racine, "init", "--quiet")
    _git(racine, "add", "-A")
    _git(racine, "-c", "user.email=t@m", "-c", "user.name=T", "commit", "--quiet", "-m", "socle")
    depot = ProjetStore(tmp_path / "depot")
    projet = depot.creer("versionné", racine)
    assert projet.versionne, projet
    return depot, projet


def _tache(id_: str, projet_id: str | None, *, dependances: tuple[str, ...] = ()) -> Task:
    """Une tâche confiée au développeur, seul rôle outillé monté ici ; son livrable porte son nom."""
    return Task(
        id=id_,
        titre=f"Tâche {id_}",
        description=f"livrable:{id_}.md",
        competences_requises=("backend",),
        format_sortie="markdown",
        dependances=dependances,
        projet_id=projet_id,
    )


def _plan(*taches: tuple[str, tuple[str, ...]]) -> str:
    """Le plan que le planificateur factice rend : `(id, dépendances)` par tâche."""
    return json.dumps(
        [
            {
                "id": id_,
                "titre": f"Tâche {id_}",
                "description": f"livrable:{id_}.md",
                "competences_requises": ["backend"],
                "format_sortie": "markdown",
                "dependances": list(dependances),
            }
            for id_, dependances in taches
        ],
        ensure_ascii=False,
    )


class _Planificateur(ModelProvider):
    """Rend toujours le même plan — le seul rôle du planificateur ici."""

    name = "plan"

    def __init__(self, plan: str) -> None:
        self._plan = plan

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._plan

    async def run_agent(self, prompt, **kwargs):  # pragma: no cover
        raise AssertionError("le planificateur ne s'exécute pas outillé")


class _Ecrivain(ModelProvider):
    """Dépose le livrable de chaque tâche, et mesure ce qui travaille **de front**.

    Chaque exécution signale son arrivée et attend que `de_front` soient là : quand
    rien ne l'interdit, elles la lèvent ensemble — c'est la barrière qui rend la
    simultanéité certaine ; quand l'atelier les sérialise, l'attente expire, et le
    pic dit une. `retenues` sont les livrables qui attendent qu'on les relâche :
    c'est ainsi qu'une tâche **tient** l'atelier le temps qu'on veut.
    """

    name = "ecrivain"

    def __init__(self, *, de_front: int = 1, patience: float = 0.5) -> None:
        self._de_front = de_front
        self._patience = patience
        self._tous_la = asyncio.Event()
        self.arrivees = 0
        self.en_vol = 0
        self.pic = 0
        self.espaces: dict[str, Path] = {}
        self.entrees: dict[str, asyncio.Event] = {}
        self.retenues: dict[str, asyncio.Event] = {}

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **canaux):
        nom = re.search(r"livrable:([\w./-]+)", prompt)
        assert nom is not None, prompt
        livrable = nom.group(1)
        espace = Path(workspace)
        self.espaces[livrable] = espace
        self.entrees.setdefault(livrable, asyncio.Event()).set()
        if livrable in self.retenues:
            await self.retenues[livrable].wait()
        else:
            self.en_vol += 1
            self.pic = max(self.pic, self.en_vol)
            self.arrivees += 1
            if self.arrivees >= self._de_front:
                self._tous_la.set()
            try:
                await asyncio.wait_for(self._tous_la.wait(), timeout=self._patience)
            except TimeoutError:
                pass  # sérialisé : les autres attendent l'atelier, elles ne viendront pas
            self.en_vol -= 1
        (espace / livrable).write_text(f"fait par {livrable}\n", encoding="utf-8")
        return "Fait."


class _Validateur:
    """L'accord d'écriture continue (#706) : toujours oui."""

    async def __call__(self, demande: DemandeValidation) -> bool:
        return True


class _Fil:
    """Le fil, vu du moteur : il note chaque carte posée, et y répond comme on le lui dit."""

    def __init__(self, reponse: str | None) -> None:
        self._reponse = reponse
        self.demandes: list[DemandeQuestion] = []

    async def __call__(self, demande: DemandeQuestion) -> str:
        self.demandes.append(demande)
        if self._reponse is None:
            await asyncio.Event().wait()  # personne ne répond
        return self._reponse or ""


def _executeur(fournisseur: ModelProvider, depot: ProjetStore | None, **kwargs) -> LocalExecutor:
    runtimes = {DEVELOPER_PROFILE.nom: AgentRuntime(fournisseur, DEVELOPER_PROFILE)}
    return LocalExecutor(
        fournisseur,
        runtimes=runtimes,
        projets=depot,
        guardrails=Guardrails(validateur=_Validateur()),
        **kwargs,
    )


def _moteur(
    fournisseur: ModelProvider,
    depot: ProjetStore,
    plan: str,
    *,
    fil: _Fil | None = None,
    attente_s: float = 5.0,
) -> OrchestrationEngine:
    return OrchestrationEngine(
        fournisseur,
        Orchestrator(_Planificateur(plan), model="factice"),
        executor=_executeur(fournisseur, depot),
        questionneur=fil,
        bornes_question=BornesArbitrage(attente_s=attente_s),
    )


def _cadences(journal: RunJournal) -> list[Any]:
    return [record for record in journal.records if record.etape == ETAPE_CADENCE]


def _fusions(journal: RunJournal) -> dict[str, str]:
    return {
        record.etape.removesuffix(":fusion"): record.statut
        for record in journal.records
        if record.etape.endswith(":fusion")
    }


# --------------------------------------------------------------------------- #
# ① La cause, lue dans le moteur
# --------------------------------------------------------------------------- #


def test_un_plan_en_chaine_passe_une_a_une_et_versionner_n_y_changerait_rien() -> None:
    """Chaque tâche attend la précédente : on le dit, on ne propose rien — même non versionné."""
    chaine = [_tache("a", None), _tache("b", None, dependances=("a",))]

    cadence = cadence_du_plan(chaine, atelier="p3")

    assert cadence is not None and cadence.cause == CAUSE_CHAINE
    assert cadence.mention() == "une tâche à la fois : chaque tâche attend la précédente"


def test_un_plan_large_sur_un_projet_non_versionne_passe_une_a_une() -> None:
    larges = [_tache("a", None), _tache("b", None), _tache("c", None, dependances=("a",))]

    cadence = cadence_du_plan(larges, atelier="p3")

    assert cadence == Cadence(cause=CAUSE_PROJET_NON_VERSIONNE, largeur=2, projet="p3")
    assert cadence.mention() == "une tâche à la fois : projet non versionné"
    assert "le projet « p3 » n'est pas versionné" in cadence.phrase()
    assert "Le plan en permettrait 2 de front" in cadence.phrase()


def test_un_plan_large_sur_un_projet_versionne_n_a_rien_a_dire() -> None:
    """Et un plan d'une seule tâche non plus : il n'y a rien qui passe « une à une »."""
    assert cadence_du_plan([_tache("a", None), _tache("b", None)], atelier=None) is None
    assert cadence_du_plan([_tache("a", None)], atelier="p3") is None


def test_la_largeur_est_le_niveau_le_plus_peuple_au_plus_long_chemin() -> None:
    """Deux tâches indépendantes tombent au même niveau, même si l'une attend plus loin."""
    losange = [
        _tache("socle", None),
        _tache("api", None, dependances=("socle",)),
        _tache("ui", None, dependances=("socle",)),
        _tache("fin", None, dependances=("api", "ui")),
    ]

    assert largeur_du_plan(losange) == 2
    assert largeur_du_plan([_tache("a", None), _tache("b", None), _tache("c", None)]) == 3


def test_une_cause_levee_n_a_plus_de_mention_et_dit_pourquoi() -> None:
    cadence = avec_proposition(
        Cadence(cause=CAUSE_PROJET_NON_VERSIONNE, largeur=3, projet="p3"), PROPOSITION_ACCEPTEE
    )

    assert cadence.liberee and cadence.mention() == ""
    assert cadence.phrase().startswith("Le projet « p3 » a été versionné pendant le run")


def test_une_cause_fait_l_aller_retour_json() -> None:
    cadence = Cadence(cause=CAUSE_INSTANCES, agent="interface", instances=1)

    assert Cadence.from_dict(json.loads(json.dumps(cadence.to_dict()))) == cadence
    assert cadence.to_dict()["mention"] == (
        "une tâche à la fois pour « interface » : une seule instance"
    )


# --------------------------------------------------------------------------- #
# ② La boucle la dit, et propose de versionner — sans attendre la réponse
# --------------------------------------------------------------------------- #


@avec_git
def test_sur_accord_le_projet_devient_un_depot_et_ses_taches_partent_chacune_dans_sa_copie(
    tmp_path: Path,
) -> None:
    """Le second critère : la proposition, sa raison, l'accord, puis l'isolation par tâche."""
    depot, projet = _projet_nu(tmp_path)
    racine = Path(projet.racine)
    fournisseur = _Ecrivain(de_front=2)
    fil = _Fil(CHOIX_VERSIONNER)
    plan = _plan(("t1", ()), ("t2", ()), ("t3", ()))
    journal = RunJournal(run_id=RUN)

    rapport = asyncio.run(
        _moteur(fournisseur, depot, plan, fil=fil).run(
            "Un site", journal=journal, projet_id=projet.id
        )
    )

    assert all(resultat.ok for resultat in rapport.resultats)
    # La proposition : dans le fil, sur la carte d'une question du run, avec sa raison.
    [demande] = fil.demandes
    assert demande.tache_id == "" and demande.run_id == RUN
    assert demande.retirer_sans_reponse, "une proposition du run quitte le fil quand elle ne vaut plus"
    assert demande.choix == (CHOIX_VERSIONNER, CHOIX_GARDER)
    assert demande.hypothese == HYPOTHESE_VERSIONNEMENT
    assert "le projet « p3 » n'est pas versionné" in demande.question
    assert "deux agents écriraient dans le même dossier" in demande.question
    # L'accord : un dépôt Git local, un premier commit de l'existant.
    assert detecter_vcs(racine) is not None
    assert depot.lire(projet.id).versionne
    assert MESSAGE_PREMIER_COMMIT in _git(racine, "log", "--format=%s")
    # Dit au journal : la cause, puis sa levée — sans mention désormais.
    lignes = _cadences(journal)
    assert [ligne.statut for ligne in lignes] == [STATUT_UNE_A_UNE, STATUT_PROJET_VERSIONNE]
    assert lignes[0].cadence["proposition"] == PROPOSITION_EN_ATTENTE
    assert lignes[-1].cadence["liberee"] is True and lignes[-1].cadence["mention"] == ""
    # L'isolation par tâche : celles qui n'avaient pas encore l'atelier ont chacune
    # leur copie, et avancent de front.
    fusions = _fusions(journal)
    assert list(fusions.values()).count(STATUT_FUSION_FAITE) >= 2
    assert set(fusions.values()) <= {STATUT_FUSION_FAITE, STATUT_ECRITURE_EN_PLACE}
    assert fournisseur.pic >= 2
    assert all((racine / f"t{rang}.md").is_file() for rang in (1, 2, 3))


@avec_git
def test_un_refus_laisse_tout_en_l_etat_et_les_taches_passent_une_a_une(tmp_path: Path) -> None:
    depot, projet = _projet_nu(tmp_path)
    racine = Path(projet.racine)
    fournisseur = _Ecrivain(de_front=2, patience=0.2)
    journal = RunJournal(run_id=RUN)

    rapport = asyncio.run(
        _moteur(fournisseur, depot, _plan(("t1", ()), ("t2", ())), fil=_Fil(CHOIX_GARDER)).run(
            "Un site", journal=journal, projet_id=projet.id
        )
    )

    assert all(resultat.ok for resultat in rapport.resultats)
    assert not (racine / ".git").exists() and not depot.lire(projet.id).versionne
    lignes = _cadences(journal)
    assert [ligne.statut for ligne in lignes] == [STATUT_UNE_A_UNE, STATUT_VERSIONNEMENT_DECLINE]
    assert "décliné d'un geste" in lignes[-1].sortie
    assert lignes[-1].cadence["mention"] == "une tâche à la fois : projet non versionné"
    assert fournisseur.pic == 1
    assert set(_fusions(journal).values()) == {STATUT_ECRITURE_EN_PLACE}


@avec_git
def test_une_phrase_n_est_pas_un_accord_d_ecriture(tmp_path: Path) -> None:
    """Seul le geste écrit dans le projet ; une phrase tapée le laisse tel quel, et c'est dit."""
    depot, projet = _projet_nu(tmp_path)
    journal = RunJournal(run_id=RUN)

    asyncio.run(
        _moteur(_Ecrivain(), depot, _plan(("t1", ()), ("t2", ())), fil=_Fil("oui vas-y")).run(
            "Un site", journal=journal, projet_id=projet.id
        )
    )

    assert not (Path(projet.racine) / ".git").exists()
    derniere = _cadences(journal)[-1]
    assert derniere.statut == STATUT_VERSIONNEMENT_DECLINE
    assert f"seul le geste « {CHOIX_VERSIONNER} » écrit dans le projet" in derniere.sortie


@avec_git
def test_sans_reponse_le_run_continue_une_tache_a_la_fois(tmp_path: Path) -> None:
    """La borne est celle d'une question (`BornesArbitrage.attente_s`) ; le run n'a rien attendu."""
    depot, projet = _projet_nu(tmp_path)
    journal = RunJournal(run_id=RUN)

    class _QuiAttendLaBorne(_Ecrivain):
        """La première tâche ne se solde qu'une fois la borne passée : le run dure plus qu'elle."""

        async def run_agent(self, prompt, **kwargs):
            if "livrable:t1.md" in prompt:
                for _ in range(500):
                    if any(r.statut == STATUT_VERSIONNEMENT_SANS_REPONSE for r in journal.records):
                        break
                    await asyncio.sleep(0.01)
            return await super().run_agent(prompt, **kwargs)

    asyncio.run(
        _moteur(
            _QuiAttendLaBorne(), depot, _plan(("t1", ()), ("t2", ())), fil=_Fil(None), attente_s=0.05
        ).run("Un site", journal=journal, projet_id=projet.id)
    )

    assert not (Path(projet.racine) / ".git").exists()
    assert [ligne.statut for ligne in _cadences(journal)] == [
        STATUT_UNE_A_UNE,
        STATUT_VERSIONNEMENT_SANS_REPONSE,
    ]


@avec_git
def test_un_run_fini_avant_la_reponse_retire_la_proposition_et_le_dit(tmp_path: Path) -> None:
    """Le run ne l'attend pas : il se solde, et la cause dit qu'elle est restée sans suite."""
    depot, projet = _projet_nu(tmp_path)
    journal = RunJournal(run_id=RUN)

    rapport = asyncio.run(
        _moteur(_Ecrivain(), depot, _plan(("t1", ()), ("t2", ())), fil=_Fil(None), attente_s=60).run(
            "Un site", journal=journal, projet_id=projet.id
        )
    )

    assert all(resultat.ok for resultat in rapport.resultats)
    assert not (Path(projet.racine) / ".git").exists()
    derniere = _cadences(journal)[-1]
    assert derniere.statut == STATUT_VERSIONNEMENT_SANS_SUITE
    assert "le run s'est achevé avant la réponse" in derniere.cadence["phrase"]


@avec_git
def test_un_plan_en_chaine_le_dit_sans_rien_proposer(tmp_path: Path) -> None:
    depot, projet = _projet_nu(tmp_path)
    fil = _Fil(CHOIX_VERSIONNER)
    journal = RunJournal(run_id=RUN)

    asyncio.run(
        _moteur(_Ecrivain(), depot, _plan(("t1", ()), ("t2", ("t1",))), fil=fil).run(
            "Un site", journal=journal, projet_id=projet.id
        )
    )

    assert fil.demandes == []
    [ligne] = _cadences(journal)
    assert ligne.cadence["cause"] == CAUSE_CHAINE
    assert not (Path(projet.racine) / ".git").exists()


@avec_git
def test_un_plan_large_sur_un_projet_versionne_ne_dit_rien(tmp_path: Path) -> None:
    depot, projet = _projet_git(tmp_path)
    fil = _Fil(CHOIX_VERSIONNER)
    fournisseur = _Ecrivain(de_front=2)
    journal = RunJournal(run_id=RUN)

    asyncio.run(
        _moteur(fournisseur, depot, _plan(("t1", ()), ("t2", ())), fil=fil).run(
            "Un site", journal=journal, projet_id=projet.id
        )
    )

    assert _cadences(journal) == [] and fil.demandes == []
    assert fournisseur.pic == 2


@avec_git
def test_sans_fil_la_cause_est_dite_sans_rien_proposer(tmp_path: Path) -> None:
    """Une carte que personne ne verrait serait une promesse creuse : on ne dit que la cause."""
    depot, projet = _projet_nu(tmp_path)
    journal = RunJournal(run_id=RUN)

    asyncio.run(
        _moteur(_Ecrivain(), depot, _plan(("t1", ()), ("t2", ()))).run(
            "Un site", journal=journal, projet_id=projet.id
        )
    )

    [ligne] = _cadences(journal)
    assert ligne.statut == STATUT_UNE_A_UNE
    assert ligne.cadence["cause"] == CAUSE_PROJET_NON_VERSIONNE
    assert ligne.cadence["proposition"] == ""


# --------------------------------------------------------------------------- #
# ③ L'exécuteur tient le passage d'un régime à l'autre
# --------------------------------------------------------------------------- #


class _Instrumente(LocalExecutor):
    """L'exécuteur, qui signale chaque tâche arrivée à l'atelier — avant d'attendre son verrou."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.a_l_atelier: dict[str, asyncio.Event] = {}

    async def _prendre_l_atelier(self, task):
        self.a_l_atelier.setdefault(task.id, asyncio.Event()).set()
        return await super()._prendre_l_atelier(task)


@avec_git
def test_le_versionnement_accorde_passe_devant_les_taches_qui_attendaient_l_atelier(
    tmp_path: Path,
) -> None:
    """Sans cette priorité, un niveau déjà en file passerait encore une à une.

    `t1` tient l'atelier (elle écrit en place) ; `t2` et `t3` attendent son verrou ;
    l'accord arrive. À la sortie de `t1`, la mise sous Git passe avant elles — le
    premier commit prend ce que `t1` a écrit —, puis `t2` et `t3` partent chacune dans
    sa copie, **ensemble**. `t1`, elle, se solde en place : elle n'a pas de branche.
    """
    depot, projet = _projet_nu(tmp_path)
    racine = Path(projet.racine)
    fournisseur = _Ecrivain(de_front=2)
    fournisseur.retenues["t1.md"] = asyncio.Event()
    runtimes = {DEVELOPER_PROFILE.nom: AgentRuntime(fournisseur, DEVELOPER_PROFILE)}
    executeur = _Instrumente(
        fournisseur, runtimes=runtimes, projets=depot, guardrails=Guardrails(validateur=_Validateur())
    )
    journal = RunJournal(run_id=RUN)

    async def _scenario():
        t1 = asyncio.create_task(executeur.execute(_tache("t1", projet.id), [], journal))
        await fournisseur.entrees.setdefault("t1.md", asyncio.Event()).wait()
        suivantes = [
            asyncio.create_task(executeur.execute(_tache(nom, projet.id), [], journal))
            for nom in ("t2", "t3")
        ]
        for nom in ("t2", "t3"):
            # Posé avant l'attente du verrou, sans rien céder entre les deux : quand
            # on reprend ici, la tâche est déjà dans la file de l'atelier.
            await executeur.a_l_atelier.setdefault(nom, asyncio.Event()).wait()
        versionnement = asyncio.create_task(executeur.versionner_le_projet(projet.id))
        while projet.id not in executeur._versionnements:
            await asyncio.sleep(0)  # un tour de boucle : l'annonce se pose avant le verrou
        fournisseur.retenues["t1.md"].set()
        return await t1, await asyncio.gather(*suivantes), await versionnement

    premiere, suivantes, versionne = asyncio.run(_scenario())

    assert versionne.versionne and detecter_vcs(racine) is not None
    initial = _git(racine, "rev-list", "--max-parents=0", "HEAD").strip()
    assert _git(racine, "show", f"{initial}:t1.md").strip() == "fait par t1.md"
    assert premiere.ok and all(resultat.ok for resultat in suivantes)
    assert fournisseur.espaces["t1.md"] == racine
    assert racine not in (fournisseur.espaces["t2.md"], fournisseur.espaces["t3.md"])
    assert fournisseur.pic == 2
    assert _fusions(journal) == {
        "t1": STATUT_ECRITURE_EN_PLACE,
        "t2": STATUT_FUSION_FAITE,
        "t3": STATUT_FUSION_FAITE,
    }


@avec_git
def test_une_tache_qui_a_ecrit_en_place_se_solde_en_place_meme_versionnee_entre_temps(
    tmp_path: Path,
) -> None:
    """Le projet change de régime pendant qu'elle écrit (la route des projets, #855) :
    elle n'a pas de branche, et sa fusion ne doit pas en chercher une."""
    depot, projet = _projet_nu(tmp_path)
    fournisseur = _Ecrivain()
    fournisseur.retenues["t1.md"] = asyncio.Event()
    executeur = _executeur(fournisseur, depot)
    journal = RunJournal(run_id=RUN)

    async def _scenario():
        t1 = asyncio.create_task(executeur.execute(_tache("t1", projet.id), [], journal))
        await fournisseur.entrees.setdefault("t1.md", asyncio.Event()).wait()
        await asyncio.to_thread(depot.versionner, projet.id)
        fournisseur.retenues["t1.md"].set()
        return await t1

    resultat = asyncio.run(_scenario())

    assert resultat.ok
    assert fournisseur.espaces["t1.md"] == Path(projet.racine)
    assert _fusions(journal) == {"t1": STATUT_ECRITURE_EN_PLACE}


def test_un_agent_au_complet_dit_une_fois_qu_il_fait_passer_ses_taches_une_a_une(
    tmp_path: Path,
) -> None:
    """Constaté à l'instant où une tâche prête attend son créneau — jamais prédit du routage."""
    fournisseur = _Ecrivain(de_front=2, patience=0.2)
    executeur = _executeur(fournisseur, None, capacites=CapacityStore(tmp_path / "capacite"))
    journal = RunJournal(run_id=RUN)

    async def _trois():
        return await asyncio.gather(
            *(executeur.execute(_tache(nom, None), [], journal) for nom in ("t1", "t2", "t3"))
        )

    resultats = asyncio.run(_trois())

    assert all(resultat.ok for resultat in resultats)
    assert fournisseur.pic == 1
    [ligne] = _cadences(journal)
    assert ligne.statut == STATUT_UNE_A_UNE and ligne.agent == ACTEUR_ORCHESTRATEUR
    assert ligne.cadence["cause"] == CAUSE_INSTANCES
    assert ligne.cadence["agent"] == DEVELOPER_PROFILE.nom and ligne.cadence["instances"] == 1


def test_la_jauge_ne_previent_que_la_tache_qu_elle_retient() -> None:
    jauge = JaugeInstances()
    prevenus: list[int] = []

    async def _deux():
        async with jauge.creneau("dev", lambda: 1, on_attente=prevenus.append):
            seconde = asyncio.create_task(_prendre())
            await asyncio.sleep(0)  # un tour : la seconde arrive et attend
        await seconde

    async def _prendre():
        async with jauge.creneau("dev", lambda: 1, on_attente=prevenus.append):
            pass

    asyncio.run(_deux())

    assert prevenus == [1]


# --------------------------------------------------------------------------- #
# ④ Le transport : une activité du run, sans rien de fantôme
# --------------------------------------------------------------------------- #


def _ligne_de_cadence(cadence: Cadence, statut: str = STATUT_UNE_A_UNE) -> dict[str, Any]:
    journal = RunJournal(run_id=RUN)
    consigne_cadence(
        journal,
        cadence,
        statut,
        agent=ACTEUR_ORCHESTRATEUR,
        role=ROLE_ORCHESTRATEUR,
        projet_id=PROJET,
    )
    return journal.records[0].to_dict()


def _activite(cadence: Cadence, statut: str = STATUT_UNE_A_UNE) -> Event:
    [event] = evenements_depuis_step(_ligne_de_cadence(cadence, statut))
    return event


NON_VERSIONNE = Cadence(
    cause=CAUSE_PROJET_NON_VERSIONNE, largeur=4, projet="p3", proposition=PROPOSITION_EN_ATTENTE
)


def test_l_etape_de_cadence_devient_une_activite_du_run_qui_porte_sa_cause() -> None:
    event = _activite(NON_VERSIONNE)

    assert event.type == EVENEMENT_AGENT_ACTIVITE
    assert event.tache_id == "" and event.etape_run == ETAPE_CADENCE
    assert event.cadence is not None
    assert event.cadence["mention"] == "une tâche à la fois : projet non versionné"
    assert event.detail == NON_VERSIONNE.phrase()
    assert Event.from_dict(json.loads(json.dumps(event.to_dict()))).cadence == event.cadence


def test_la_cadence_n_ouvre_aucune_tache_au_grand_livre() -> None:
    journal = RunJournal(run_id=RUN)
    consigne_cadence(
        journal, NON_VERSIONNE, STATUT_UNE_A_UNE, agent="o", role="O", projet_id=PROJET
    )

    assert RunCost.depuis_journal(journal).taches == ()


# --------------------------------------------------------------------------- #
# ⑤ La vue du run et le fil
# --------------------------------------------------------------------------- #


def _lancement() -> Event:
    return Event(
        type=EVENEMENT_EXECUTION_STATUT,
        run_id=RUN,
        statut=EXECUTION_EN_COURS,
        titre="Un site",
        projet_id=PROJET,
    )


def _plan_publie() -> Event:
    return Event(
        type=EVENEMENT_RUN_PLAN,
        run_id=RUN,
        plan=[NoeudPlan(id=nom, titre=f"Tâche {nom}") for nom in ("a", "b", "c", "d")],
        projet_id=PROJET,
    )


def _graphe_servi(*evenements: Event) -> dict[str, Any]:
    log = InMemoryEventLog()
    for event in evenements:
        asyncio.run(log.consigner(event))
    app = create_app(bus=InMemoryEventBus(), state=ControlTowerState(), event_log=log)
    with TestClient(app) as client:
        reponse = client.get(f"/api/executions/{RUN}/graphe")
    assert reponse.status_code == 200
    return reponse.json()


def test_la_vue_du_run_dit_pourquoi_ses_taches_passent_une_a_une() -> None:
    graphe = _graphe_servi(_lancement(), _plan_publie(), _activite(NON_VERSIONNE))

    assert graphe["largeur"] == 4
    assert graphe["cadence"] == [
        {
            "cle": "projet_non_versionne:",
            "cause": CAUSE_PROJET_NON_VERSIONNE,
            "agent": "",
            "mention": "une tâche à la fois : projet non versionné",
            "phrase": NON_VERSIONNE.phrase(),
        }
    ]


def test_une_fois_le_projet_versionne_la_vue_n_a_plus_rien_a_dire() -> None:
    levee = avec_proposition(NON_VERSIONNE, PROPOSITION_ACCEPTEE)

    graphe = _graphe_servi(
        _lancement(),
        _plan_publie(),
        _activite(NON_VERSIONNE),
        _activite(levee, STATUT_PROJET_VERSIONNE),
    )

    assert graphe["cadence"] == []


def test_un_run_dont_les_taches_partent_de_front_ne_porte_aucune_mention() -> None:
    assert _graphe_servi(_lancement(), _plan_publie())["cadence"] == []


def test_un_agent_au_complet_se_dit_a_cote_de_la_cause_du_projet() -> None:
    instance = Cadence(cause=CAUSE_INSTANCES, agent="interface", instances=1)

    graphe = _graphe_servi(
        _lancement(), _plan_publie(), _activite(NON_VERSIONNE), _activite(instance)
    )

    assert [cause["mention"] for cause in graphe["cadence"]] == [
        "une tâche à la fois : projet non versionné",
        "une tâche à la fois pour « interface » : une seule instance",
    ]


def test_le_fil_lit_pourquoi_les_taches_passent_une_a_une() -> None:
    """Les faits du run que reçoit le fil : il répond à « pourquoi ? » sans rien deviner."""
    state = ControlTowerState()
    for event in (_lancement(), _plan_publie(), _activite(NON_VERSIONNE)):
        state.appliquer(event)
    execution = state.execution(RUN)
    assert execution is not None

    lignes = fiche_du_run(state, execution)

    [cadence] = [ligne for ligne in lignes if ligne.startswith("  cadence : ")]
    assert "le projet « p3 » n'est pas versionné" in cadence
    assert "Le versionner est proposé dans le fil" in cadence


class _BusNote(InMemoryEventBus):
    """Le bus mémoire, qui garde ce qu'on y publie."""

    def __init__(self) -> None:
        super().__init__()
        self.publies: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.publies.append(event)
        await super().publish(event)


def _demande_du_run(*, retirer: bool) -> DemandeQuestion:
    return DemandeQuestion(
        question_id="cadence:0123456789",
        question="Versionner le projet « p3 » ?",
        hypothese=HYPOTHESE_VERSIONNEMENT,
        choix=(CHOIX_VERSIONNER, CHOIX_GARDER),
        titre="Versionner le projet « p3 »",
        agent=ACTEUR_ORCHESTRATEUR,
        role=ROLE_ORCHESTRATEUR,
        run_id=RUN,
        projet_id=PROJET,
        attente_s=0.05,
        retirer_sans_reponse=retirer,
    )


def _sans_reponse(bus: _BusNote, demande: DemandeQuestion) -> None:
    async def _attendre():
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(ArbitreQuestionControlTower(bus)(demande), 0.05)

    asyncio.run(_attendre())


def test_une_proposition_du_run_sans_reponse_quitte_le_fil() -> None:
    """Passé la borne, son geste ne ferait plus rien : elle se retire au lieu de mentir."""
    bus = _BusNote()
    demande = _demande_du_run(retirer=True)

    _sans_reponse(bus, demande)

    assert [(e.type, e.statut) for e in bus.publies] == [
        (EVENEMENT_QUESTION_DEMANDE, QUESTION_EN_ATTENTE),
        (EVENEMENT_QUESTION_REPONSE, QUESTION_RETIREE),
    ]
    state = ControlTowerState()
    for event in bus.publies:
        state.appliquer(event)
    question = state.question(demande.question_id)
    assert question is not None and question.statut == QUESTION_RETIREE
    assert not question.en_attente


def test_la_question_d_un_agent_reste_ouverte_apres_sa_borne() -> None:
    """Ce qui ne bouge pas (#1023, #584) : une réponse tardive sert encore à l'agent."""
    bus = _BusNote()

    _sans_reponse(bus, _demande_du_run(retirer=False))

    assert [e.type for e in bus.publies] == [EVENEMENT_QUESTION_DEMANDE]


def test_un_retrait_n_efface_pas_une_reponse_deja_donnee() -> None:
    demande = _demande_du_run(retirer=True)
    state = ControlTowerState()
    state.appliquer(evenement_question(demande))
    state.appliquer(
        Event(
            type=EVENEMENT_QUESTION_REPONSE,
            run_id=RUN,
            question_id=demande.question_id,
            statut=QUESTION_REPONDUE,
            detail=CHOIX_GARDER,
        )
    )

    state.appliquer(evenement_retrait(demande))

    question = state.question(demande.question_id)
    assert question is not None
    assert (question.statut, question.reponse) == (QUESTION_REPONDUE, CHOIX_GARDER)


def test_repondre_a_une_proposition_retiree_est_refuse_en_le_disant() -> None:
    demande = _demande_du_run(retirer=True)
    log = InMemoryEventLog()
    for event in (_lancement(), evenement_question(demande), evenement_retrait(demande)):
        asyncio.run(log.consigner(event))
    app = create_app(bus=InMemoryEventBus(), state=ControlTowerState(), event_log=log)

    with TestClient(app) as client:
        reponse = client.post(
            f"/api/questions/{demande.question_id}/reponse", json={"reponse": CHOIX_VERSIONNER}
        )

    assert reponse.status_code == 409
    assert "retirée sans réponse" in reponse.json()["detail"]


def test_le_fil_sait_d_avance_qu_un_projet_non_versionne_fait_passer_ses_taches_une_a_une() -> None:
    """Ce qu'un run fera (#1323) : le fil n'annonce pas un parallélisme que le projet interdit."""
    assert "Sur un projet non versionné" in CE_QUI_NE_SE_PREVOIT_PAS
    assert "versionner le projet" in CE_QUI_NE_SE_PREVOIT_PAS
