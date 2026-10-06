"""Un run interrompu se reprend **sur son plan** : ce qui est fait ne se refait pas (#1391).

Le constat de p5 (2026-10-01) : « Reprendre » un run qu'une extinction avait soldé
appelait la relance de #349 — un **nouveau** run, reparti de la synthèse du brief,
qui re-planifiait (`socle-projet` devenu `socle-nextjs`) et refaisait son socle de
zéro. docs/28 §12 (cadrage #701) avait tranché la réponse sans la livrer : un **état
acquis durable**, hors du process du run, à la granularité de la tâche terminée.

Ce que ce fichier garde, et qui ne se voit nulle part ailleurs :

① **Le magasin** tient son contrat sur ses trois supports — mémoire, Redis (le
   double partagé `tests/redis_factice.py`), SQLite : le plan exécutable et les
   seules issues **réussies**, sorties comprises ; rien sans plan ; l'oubli.

② **Le moteur** range son plan dès qu'il est figé et chaque issue réussie à
   l'instant où elle l'est ; un run interrompu garde ce qu'il a payé, un run
   entièrement réussi oublie son état. Repris (`run(reprise=…)`), il ne replanifie
   pas, ne réexécute **aucune** tâche acquise, et l'aval reçoit la **sortie** de
   l'amont acquis — le deuxième critère du ticket, exercé sur la vraie boucle.

③ **Le service et sa route** : `POST …/reprendre` continue un run éteint — en pause
   ou non — **sous le même identifiant**, confie à son hôte l'état acquis, ses
   bornes, son projet et son ticket ; garde le double clic ; solde d'abord un run
   dont l'hôte s'est tu ; refuse une annulation voulue ; refuse (`503`) plutôt que
   de relancer quand l'état ne se relit pas ; reprend aussi un run sans brief
   approuvé, que la relance refusait (docs/28 §12.7).

④ **De bout en bout**, sur la vraie app, le vrai moteur et l'hôte en process : un
   run lancé, éteint pendant sa deuxième tâche, repris — la première tâche n'est
   exécutée qu'**une** fois, la deuxième lit sa sortie, et le run finit sous son
   identifiant d'origine. C'est le premier critère, sans fournisseur ni process.

**Ni réseau, ni modèle, ni process de run** (`tests/conftest.py`, #195). L'épreuve
sur le réel — extinction d'une vraie stack, redémarrage, reprise — est celle du
banc (S12) et se consigne à la clôture.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis_factice import ClientSynchrone, brancher

from maestro.controltower import (
    ControlTowerState,
    Event,
    InMemoryEventBus,
    InMemoryEventLog,
    RegistreBattementsMemoire,
    create_app,
)
from maestro.controltower.acquis import (
    PREFIXE_ACQUIS,
    MagasinAcquisRedis,
    MagasinAcquisSqlite,
)
from maestro.controltower.battement import horodatage_battement
from maestro.controltower.bornes import BornesRun
from maestro.controltower.causes import CAUSE_ANNULATION, CAUSE_EXTINCTION
from maestro.controltower.events import (
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_RUN_PLAN,
    EVENEMENT_TACHE_STATUT,
)
from maestro.controltower.executions import DETAIL_HOTE_TU
from maestro.controltower.hote import HoteMort, HoteRun, OrdreRun
from maestro.controltower.hote_detache import ordre_depuis_dict, ordre_vers_dict
from maestro.controltower.progression import STATUT_BACKLOG, STATUT_INTERROMPUE
from maestro.controltower.state import (
    EXECUTION_ANNULEE,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    ORDRE_PAUSE,
)
from maestro.engine import (
    MODE_BRIEF_AUTO,
    MODE_BRIEF_SANS,
    STATUT_ECHEC,
    STATUT_EN_COURS,
    STATUT_TERMINEE,
    OrchestrationEngine,
    TaskExecutor,
    TaskResult,
)
from maestro.engine.acquis import EtatAcquis, MagasinAcquis, MagasinAcquisMemoire
from maestro.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task
from maestro.plan_run import NoeudPlan
from maestro.providers.base import ModelProvider
from maestro.references import ReferenceTicket
from maestro.telemetry import RunJournal, StepUsage
from maestro.telemetry.costs import ETAPE_REPRISE, RunCost

#: Plafond d'attente d'un fait **asynchrone** (pompe, tâche de fond de l'app). Jamais
#: atteint quand tout va bien : c'est un ordonnancement qu'on attend, pas un travail.
DELAI_ATTENTE_S = 10.0

#: Le run des scénarios : un projet, trois tâches en chaîne — le socle, les pages qui
#: le lisent, les tests qui lisent les pages. C'est p5 en petit.
RUN = "5352205e1c6d"
PROJET = "prj-recettes"
SOCLE, PAGES, TESTS = "socle-projet", "pages-recettes", "tests-recettes"


# ------------------------------------------------------------------ décor commun


def _tache(identifiant: str, *dependances: str) -> Task:
    return Task(
        id=identifiant,
        titre=f"Tâche {identifiant}",
        description=f"Réaliser {identifiant}.",
        competences_requises=("frontend",),
        format_sortie="Texte",
        dependances=tuple(dependances),
        projet_id=PROJET,
    )


PLAN = (_tache(SOCLE), _tache(PAGES, SOCLE), _tache(TESTS, PAGES))


def _issue(
    identifiant: str,
    sortie: str = "",
    *,
    statut: str = STATUT_TERMINEE,
    cout_usd: float | None = 0.25,
) -> TaskResult:
    return TaskResult(
        task_id=identifiant,
        titre=f"Tâche {identifiant}",
        agent="dev",
        role="Développeur",
        competences_requises=("frontend",),
        score=1,
        statut=statut,
        sortie=sortie or (f"LIVRABLE {identifiant}" if statut == STATUT_TERMINEE else ""),
        erreur=None if statut == STATUT_TERMINEE else "panne",
        usage=StepUsage(appels=1, tokens_entree=100, tokens_sortie=50, cout_usd=cout_usd),
    )


def _plan_json() -> str:
    return json.dumps([t.to_dict() for t in PLAN], ensure_ascii=False)


def _attendre(condition: Callable[[], bool], quoi: str) -> None:
    limite = time.monotonic() + DELAI_ATTENTE_S
    while not condition():
        if time.monotonic() > limite:  # pragma: no cover - filet anti-blocage
            pytest.fail(f"{quoi} n'est jamais arrivé en {DELAI_ATTENTE_S} s")
        time.sleep(0.02)


class PlanificateurCompte(ModelProvider):
    """Rend toujours le même plan, et compte qu'on le lui demande."""

    name = "planificateur-compte"

    def __init__(self, reponse: str) -> None:
        self._reponse = reponse
        self.appels = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        self.appels += 1
        return self._reponse


class ExecuteurQuiCoupe(TaskExecutor):
    """L'exécuteur d'un run qu'on interrompt : il **journalise comme le vrai**, et bloque.

    `bloquees` sont les tâches dont la **première** exécution ne rend jamais la main —
    celle qu'une extinction viendra couper ; les suivantes rendent leur livrable. Il
    consigne le début et l'issue de chaque tâche comme `LocalExecutor`
    (`<id>:debut`, puis `<id>`) : c'est par là que la projection apprend qu'une tâche
    tourne, est faite, ou a été interrompue.

    `executees` trace chaque transmission, `dependances_recues` le tableau noir que
    chacune a reçu — c'est ce que les critères regardent.
    """

    def __init__(self, bloquees: Sequence[str] = ()) -> None:
        self._bloquees = set(bloquees)
        self.executees: list[str] = []
        self.dependances_recues: dict[str, tuple[TaskResult, ...]] = {}

    async def execute(
        self, task: Task, dependances: Sequence[TaskResult], journal: RunJournal
    ) -> TaskResult:
        self.executees.append(task.id)
        self.dependances_recues[task.id] = tuple(dependances)
        journal.consigne(
            etape=f"{task.id}:debut",
            nom=task.titre,
            agent="dev",
            role="Développeur",
            statut=STATUT_EN_COURS,
            entree=task.description,
            sortie="",
            usage=StepUsage(),
            projet_id=task.projet_id,
        )
        if task.id in self._bloquees:
            self._bloquees.discard(task.id)
            await asyncio.Event().wait()
        lu = " + ".join(d.sortie for d in dependances)
        issue = _issue(task.id, f"LIVRABLE {task.id}" + (f" (lu : {lu})" if lu else ""))
        journal.consigne(
            etape=task.id,
            nom=task.titre,
            agent="dev",
            role="Développeur",
            statut=issue.statut,
            entree=task.description,
            sortie=issue.sortie,
            usage=issue.usage,
            projet_id=task.projet_id,
        )
        return issue


def _moteur(
    planificateur: ModelProvider, executeur: TaskExecutor, acquis: MagasinAcquis | None
) -> OrchestrationEngine:
    return OrchestrationEngine(
        planificateur,
        Orchestrator(planificateur, model="claude-opus-4-8"),
        executor=executeur,
        acquis=acquis,
    )


# ======================================================= ① le magasin, trois supports


def _magasins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, MagasinAcquis]:
    brancher(monkeypatch)
    return {
        "memoire": MagasinAcquisMemoire(),
        "redis": MagasinAcquisRedis("redis://factice"),
        "sqlite": MagasinAcquisSqlite(tmp_path / "local.sqlite3"),
    }


@pytest.mark.parametrize("support", ["memoire", "redis", "sqlite"])
def test_le_magasin_rend_le_plan_et_les_seules_issues_reussies(
    support: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le contrat, à l'identique sur les trois supports : plan entier, réussites seules.

    Le plan revient **exécutable** — description, compétences, format, projet hérité :
    ce que `NoeudPlan` ne porte pas (docs/28 §12.2). Une issue en échec n'est pas
    acquise : elle n'a rien produit qu'une autre puisse lire.
    """
    magasin = _magasins(tmp_path, monkeypatch)[support]

    async def scenario() -> EtatAcquis | None:
        await magasin.poser_plan(RUN, PLAN)
        await magasin.acquerir(RUN, _issue(SOCLE, "le socle Next.js"))
        await magasin.acquerir(RUN, _issue(PAGES, statut=STATUT_ECHEC))
        etat = await magasin.lire(RUN)
        await magasin.close()
        return etat

    etat = asyncio.run(scenario())

    assert etat is not None
    assert etat.plan == PLAN
    assert etat.acquises == (SOCLE,)
    assert etat.a_reprendre == (PAGES, TESTS)
    assert etat.resultats[SOCLE].sortie == "le socle Next.js"
    assert etat.resultats[SOCLE].usage.cout_usd == pytest.approx(0.25)


@pytest.mark.parametrize("support", ["memoire", "redis", "sqlite"])
def test_sans_plan_rien_a_reprendre_et_l_oubli_efface_tout(
    support: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une issue sans plan ne fait pas un état ; un run oublié n'en a plus."""
    magasin = _magasins(tmp_path, monkeypatch)[support]

    async def scenario() -> tuple[EtatAcquis | None, EtatAcquis | None, EtatAcquis | None]:
        await magasin.acquerir(RUN, _issue(SOCLE))
        sans_plan = await magasin.lire(RUN)
        await magasin.poser_plan(RUN, PLAN)
        avant = await magasin.lire(RUN)
        await magasin.oublier(RUN)
        apres = await magasin.lire(RUN)
        await magasin.close()
        return sans_plan, avant, apres

    sans_plan, avant, apres = asyncio.run(scenario())

    assert sans_plan is None
    assert avant is not None and avant.acquises == (SOCLE,)
    assert apres is None


def test_une_issue_refaite_remplace_la_premiere() -> None:
    """Un livrable refait à la demande d'une QA (#1177) : c'est lui que l'aval lira."""
    magasin = MagasinAcquisMemoire()

    async def scenario() -> EtatAcquis | None:
        await magasin.poser_plan(RUN, PLAN)
        await magasin.acquerir(RUN, _issue(SOCLE, "première version"))
        await magasin.acquerir(RUN, _issue(SOCLE, "version corrigée"))
        return await magasin.lire(RUN)

    etat = asyncio.run(scenario())
    assert etat is not None and etat.resultats[SOCLE].sortie == "version corrigée"


def test_une_entree_illisible_ne_fait_perdre_qu_elle_meme() -> None:
    """Une issue illisible est écartée seule (sa tâche sera refaite) ; un plan illisible
    rend None — reprendre de travers serait pire que ne pas reprendre (docs/28 §12.8 ①)."""
    plan = json.dumps([t.to_dict() for t in PLAN])
    socle = json.dumps(_issue(SOCLE).to_dict())

    etat = EtatAcquis.depuis_entrees(
        {"plan": plan, f"tache:{SOCLE}": socle, f"tache:{PAGES}": "{pas du json"}
    )
    assert etat is not None and etat.acquises == (SOCLE,)
    assert EtatAcquis.depuis_entrees({"plan": "[{incomplet", f"tache:{SOCLE}": socle}) is None


def test_l_etat_traverse_l_ordre_du_run_tel_quel() -> None:
    """L'ordre d'un run repris voyage en JSON vers le process détaché : rien ne s'y perd."""
    etat = EtatAcquis.depuis(PLAN, [_issue(SOCLE, "le socle")])
    ordre = OrdreRun(run_id=RUN, objectif="Un site de recettes", reprise=etat)

    relu = ordre_depuis_dict(json.loads(json.dumps(ordre_vers_dict(ordre))))

    assert relu.reprise is not None
    assert relu.reprise.plan == PLAN
    assert relu.reprise.resultats[SOCLE].sortie == "le socle"
    assert ordre_depuis_dict(ordre_vers_dict(OrdreRun(run_id=RUN, objectif="x"))).reprise is None


# ================================================================= ② le moteur


def test_le_moteur_range_son_plan_et_chaque_issue_reussie_au_fil_du_run() -> None:
    """Un run qui échoue en route garde ce qu'il a payé : plan, et les seules réussites."""
    magasin = MagasinAcquisMemoire()
    planificateur = PlanificateurCompte(_plan_json())

    class EchoueSurLesPages(ExecuteurQuiCoupe):
        async def execute(self, task, dependances, journal):  # type: ignore[override]
            if task.id == PAGES:
                self.executees.append(task.id)
                return _issue(PAGES, statut=STATUT_ECHEC)
            return await super().execute(task, dependances, journal)

    rapport = asyncio.run(
        _moteur(planificateur, EchoueSurLesPages(), magasin).run(
            "Un site de recettes", journal=RunJournal(run_id=RUN)
        )
    )
    etat = asyncio.run(magasin.lire(RUN))

    assert not all(r.ok for r in rapport.resultats)
    assert etat is not None
    assert [t.id for t in etat.plan] == [SOCLE, PAGES, TESTS]
    assert etat.acquises == (SOCLE,)


def test_un_run_entierement_reussi_oublie_son_etat() -> None:
    """Tout est fait : il n'y a plus rien à reprendre, et ses sorties n'occupent plus rien."""
    magasin = MagasinAcquisMemoire()
    rapport = asyncio.run(
        _moteur(PlanificateurCompte(_plan_json()), ExecuteurQuiCoupe(), magasin).run(
            "Un site de recettes", journal=RunJournal(run_id=RUN)
        )
    )

    assert all(r.ok for r in rapport.resultats)
    assert asyncio.run(magasin.lire(RUN)) is None


def _interrompre_pendant(tache: str, magasin: MagasinAcquis) -> ExecuteurQuiCoupe:
    """Lance le run, le coupe quand `tache` tourne — l'extinction vue du moteur."""
    executeur = ExecuteurQuiCoupe(bloquees=(tache,))
    moteur = _moteur(PlanificateurCompte(_plan_json()), executeur, magasin)

    async def scenario() -> None:
        run = asyncio.create_task(
            moteur.run("Un site de recettes", journal=RunJournal(run_id=RUN))
        )
        while tache not in executeur.executees:
            await asyncio.sleep(0)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

    asyncio.run(scenario())
    return executeur


def test_un_run_interrompu_garde_ce_qu_il_a_paye() -> None:
    """Coupé pendant les pages : le socle est acquis, sortie comprise ; les pages non."""
    magasin = MagasinAcquisMemoire()
    _interrompre_pendant(PAGES, magasin)

    etat = asyncio.run(magasin.lire(RUN))
    assert etat is not None
    assert etat.acquises == (SOCLE,)
    assert etat.a_reprendre == (PAGES, TESTS)
    assert etat.resultats[SOCLE].sortie == f"LIVRABLE {SOCLE}"


def test_la_reprise_ne_refait_rien_d_acquis_et_l_aval_lit_sa_sortie() -> None:
    """Les deux premiers critères, sur la vraie boucle.

    ① Le socle, fait avant l'interruption, n'atteint **jamais** l'exécuteur de la
    reprise ; les pages et les tests, eux, s'exécutent. ② Les pages reçoivent la
    **sortie** du socle acquis — le tableau noir qui entre dans leur prompt —, comme
    si le run ne s'était jamais arrêté. Et rien n'est replanifié : le planificateur
    qui lèverait n'est pas appelé.
    """
    magasin = MagasinAcquisMemoire()
    premier = _interrompre_pendant(PAGES, magasin)
    etat = asyncio.run(magasin.lire(RUN))
    assert etat is not None

    class PlanificateurInterdit(PlanificateurCompte):
        async def generate(self, prompt, *, model, system_prompt=None):  # type: ignore[override]
            raise AssertionError("une reprise ne replanifie pas")

    reprise = ExecuteurQuiCoupe()
    rapport = asyncio.run(
        _moteur(PlanificateurInterdit(""), reprise, magasin).run(
            "Un site de recettes", journal=RunJournal(run_id=RUN), reprise=etat
        )
    )

    assert premier.executees == [SOCLE, PAGES]
    assert reprise.executees == [PAGES, TESTS]
    (socle_lu,) = reprise.dependances_recues[PAGES]
    assert socle_lu.task_id == SOCLE
    assert socle_lu.sortie == f"LIVRABLE {SOCLE}"
    # Le rapport dit le run entier, pas sa seule seconde moitié.
    assert [r.task_id for r in rapport.resultats] == [SOCLE, PAGES, TESTS]
    assert all(r.ok for r in rapport.resultats)
    # Tout est fait : l'état s'oublie.
    assert asyncio.run(magasin.lire(RUN)) is None


def test_la_reprise_se_dit_au_fil_du_run_et_compte_ce_qui_est_paye() -> None:
    """La césure part au fil (étape `reprise`) ; l'acquis rejoint le journal **sans** repartir.

    Le plafond de dépense relit le grand livre du journal à chaque mesure : une
    reprise qui l'ignorerait accorderait le plafond entier une seconde fois. L'étape
    acquise n'est pourtant **pas** réémise — la projection l'a déjà, la republier
    compterait la tâche deux fois à l'écran.
    """
    etat = EtatAcquis.depuis(PLAN, [_issue(SOCLE, cout_usd=0.40)])
    emises: list[dict[str, Any]] = []

    class Capteur:
        def info(self, ligne: str) -> None:
            emises.append(json.loads(ligne))

    journal = RunJournal(run_id=RUN, logger=Capteur())  # type: ignore[arg-type]
    asyncio.run(
        _moteur(PlanificateurCompte(""), ExecuteurQuiCoupe(), None).run(
            "Un site de recettes", journal=journal, reprise=etat
        )
    )

    (cesure,) = [e for e in emises if e["etape"] == ETAPE_REPRISE]
    assert "1 tâche(s) déjà faite(s)" in cesure["sortie"]
    assert "2 à faire" in cesure["sortie"]
    assert SOCLE not in {e["etape"] for e in emises}
    # 0,40 $ acquis + 2 × 0,25 $ refaits ou neufs.
    assert RunCost.depuis_journal(journal).total.cout_usd == pytest.approx(0.90)


def test_une_ecriture_refusee_ne_fait_jamais_echouer_le_run() -> None:
    """Le magasin rend un run reprenable, jamais l'inverse : en panne, le run va au bout."""

    class MagasinEnPanne(MagasinAcquisMemoire):
        async def poser_plan(self, run_id, tasks):  # type: ignore[override]
            raise ConnectionError("Redis injoignable")

        async def acquerir(self, run_id, resultat):  # type: ignore[override]
            raise ConnectionError("Redis injoignable")

    rapport = asyncio.run(
        _moteur(PlanificateurCompte(_plan_json()), ExecuteurQuiCoupe(), MagasinEnPanne()).run(
            "Un site de recettes", journal=RunJournal(run_id=RUN)
        )
    )
    assert all(r.ok for r in rapport.resultats)


# ======================================================= ③ le service et sa route


class HoteEspion(HoteRun):
    """Un hôte qui ne lance rien et **retient chaque ordre** — c'est l'ordre qu'on juge."""

    def __init__(self) -> None:
        self.ordres: list[OrdreRun] = []
        self.annules: list[str] = []
        self._en_vol: list[str] = []

    async def lancer(self, ordre: OrdreRun) -> None:
        self.ordres.append(ordre)
        self._en_vol.append(ordre.run_id)

    async def annuler(self, run_id: str, *, delai_s: float) -> bool:
        self.annules.append(run_id)
        if run_id not in self._en_vol:
            return False
        self._en_vol.remove(run_id)
        return True

    def en_vol(self, run_id: str) -> bool:
        return run_id in self._en_vol

    def runs_en_vol(self) -> tuple[str, ...]:
        return tuple(self._en_vol)

    def ramasser(self) -> tuple[HoteMort, ...]:
        return ()

    async def fermer(self, *, delai_s: float) -> None:
        return None


BORNES = BornesRun(plafond_cout_usd=5.0, parallelisme=2)
TICKET = ReferenceTicket.depuis({"outil": "github", "id": "42", "url": "https://x/42"})


def _journal_du_run(
    *,
    issue: str | None = EXECUTION_ANNULEE,
    cause: str = CAUSE_EXTINCTION,
    en_pause: bool = False,
) -> InMemoryEventLog:
    """La trace d'un run lancé `auto` hors du fil (brief **jamais approuvé**), interrompu en route.

    Le plan a déclaré ses trois cartes (#924), le socle est fait, les pages tournaient
    quand l'arrêt est venu. `issue=None` laisse le run `en_cours` — son hôte s'est tu.
    """
    evenements = [
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN,
            titre="Un site de recettes",
            description="Un site de recettes",
            agent="orchestrateur",
            role="Orchestrateur",
            statut=EXECUTION_EN_COURS,
            mode_brief=MODE_BRIEF_AUTO,
            projet_id=PROJET,
            ticket=TICKET,
            bornes=BORNES,
        ),
        Event(
            type=EVENEMENT_RUN_PLAN,
            run_id=RUN,
            projet_id=PROJET,
            plan=[NoeudPlan(id=t.id, titre=t.titre, dependances=t.dependances) for t in PLAN],
        ),
        Event(
            type=EVENEMENT_TACHE_STATUT,
            run_id=RUN,
            tache_id=SOCLE,
            titre=f"Tâche {SOCLE}",
            agent="dev",
            role="Développeur",
            statut=STATUT_TERMINEE,
            projet_id=PROJET,
        ),
        Event(
            type=EVENEMENT_TACHE_STATUT,
            run_id=RUN,
            tache_id=PAGES,
            titre=f"Tâche {PAGES}",
            agent="dev",
            role="Développeur",
            statut=STATUT_EN_COURS,
            projet_id=PROJET,
        ),
    ]
    if en_pause:
        evenements.append(
            Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=ORDRE_PAUSE)
        )
    if issue is not None:
        evenements += [
            Event(
                type=EVENEMENT_TACHE_STATUT,
                run_id=RUN,
                tache_id=PAGES,
                titre=f"Tâche {PAGES}",
                agent="dev",
                role="Développeur",
                statut=STATUT_INTERROMPUE,
                projet_id=PROJET,
                cause=cause,
            ),
            Event(
                type=EVENEMENT_EXECUTION_STATUT,
                run_id=RUN,
                agent="orchestrateur",
                role="Orchestrateur",
                statut=issue,
                detail="Maestro s'est éteint",
                cause=cause,
            ),
        ]
    journal = InMemoryEventLog()

    async def consigner() -> None:
        for evenement in evenements:
            await journal.consigner(evenement)

    asyncio.run(consigner())
    return journal


def _acquis(*, avec_plan: bool = True) -> MagasinAcquisMemoire:
    """Ce que l'hôte du run avait rangé avant l'arrêt : le plan, et le socle fait."""
    magasin = MagasinAcquisMemoire()

    async def ranger() -> None:
        if avec_plan:
            await magasin.poser_plan(RUN, PLAN)
        await magasin.acquerir(RUN, _issue(SOCLE, "le socle Next.js"))

    asyncio.run(ranger())
    return magasin


def _app(
    hote: HoteRun,
    journal: InMemoryEventLog,
    acquis: MagasinAcquis,
    *,
    state: ControlTowerState | None = None,
    battements: RegistreBattementsMemoire | None = None,
) -> TestClient:
    return TestClient(
        create_app(
            bus=InMemoryEventBus(),
            state=state if state is not None else ControlTowerState(),
            event_log=journal,
            battements=battements if battements is not None else RegistreBattementsMemoire(),
            hote_run=hote,
            acquis=acquis,
        )
    )


def _resume(client: TestClient, run_id: str = RUN) -> dict[str, Any]:
    reponse = client.get(f"/api/executions/{run_id}")
    assert reponse.status_code == 200, reponse.text
    return dict(reponse.json())


def test_un_run_eteint_se_reprend_sur_son_plan_sous_le_meme_identifiant() -> None:
    """Le premier critère, au niveau du service : **le même run**, et ce qu'il a payé.

    Le run repart `en_cours` sous son identifiant — ni nouveau run, ni `reprise_de` —,
    et son hôte reçoit l'état acquis : le socle à ne pas refaire, sa sortie, ce qui
    reste. Ses bornes, son projet et son ticket voyagent avec lui ; le cadrage, lui,
    ne se rejoue pas (`sans`).
    """
    hote = HoteEspion()
    with _app(hote, _journal_du_run(), _acquis()) as client:
        reponse = client.post(f"/api/executions/{RUN}/reprendre")
        assert reponse.status_code == 200, reponse.text
        repris = reponse.json()

    assert repris["run_id"] == RUN
    assert repris["reprise_de"] == ""
    assert repris["statut"] == EXECUTION_EN_COURS
    assert repris["cause"] == ""
    assert repris["fin"] is None
    (ordre,) = hote.ordres
    assert ordre.run_id == RUN
    assert ordre.reprise is not None
    assert ordre.reprise.acquises == (SOCLE,)
    assert ordre.reprise.a_reprendre == (PAGES, TESTS)
    assert ordre.reprise.resultats[SOCLE].sortie == "le socle Next.js"
    assert ordre.mode_brief == MODE_BRIEF_SANS
    assert (ordre.plafond_cout_usd, ordre.parallelisme) == (5.0, 2)
    assert ordre.projet_id == PROJET
    assert ordre.ticket == TICKET
    assert ordre.objectif == "Un site de recettes"


def test_les_taches_faites_gardent_leur_etat_et_les_autres_restent_a_faire() -> None:
    """« Est-ce bien le même, et qu'est-ce qui reste à faire ? » — la projection le dit.

    Le socle reste fait, la carte jamais démarrée reste à faire, et rien n'est soldé
    une seconde fois par la reprise.
    """
    hote = HoteEspion()
    state = ControlTowerState()
    with _app(hote, _journal_du_run(), _acquis(), state=state) as client:
        assert client.post(f"/api/executions/{RUN}/reprendre").status_code == 200
        progression = _resume(client)["progression"]

    assert state.tache(SOCLE).statut == STATUT_TERMINEE  # type: ignore[union-attr]
    assert state.tache(TESTS).statut == STATUT_BACKLOG  # type: ignore[union-attr]
    assert progression["terminees"] == 1
    assert progression["a_faire"] == 1
    assert progression["total"] == 3


def test_un_run_eteint_en_pause_se_reprend_et_perd_sa_pause() -> None:
    """Le cas de p5 : pause, puis extinction. « Reprendre » le remet en route, le même."""
    hote = HoteEspion()
    with _app(hote, _journal_du_run(en_pause=True), _acquis()) as client:
        assert _resume(client)["en_pause"] is True

        reponse = client.post(f"/api/executions/{RUN}/reprendre")

        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["run_id"] == RUN
        assert reponse.json()["en_pause"] is False
    assert [o.run_id for o in hote.ordres] == [RUN]


def test_un_second_clic_ne_lance_pas_un_second_hote() -> None:
    """Repris, le run bat de nouveau : le second « Reprendre » est refusé, sans hôte de plus."""
    hote = HoteEspion()
    with _app(hote, _journal_du_run(), _acquis()) as client:
        assert client.post(f"/api/executions/{RUN}/reprendre").status_code == 200
        seconde = client.post(f"/api/executions/{RUN}/reprendre")

    assert seconde.status_code == 409, seconde.text
    assert len(hote.ordres) == 1


def test_un_run_annule_exprès_ne_se_reprend_pas() -> None:
    """Une annulation voulue a rendu son issue : rien à reprendre, même avec un plan rangé."""
    hote = HoteEspion()
    with _app(hote, _journal_du_run(cause=CAUSE_ANNULATION), _acquis()) as client:
        reponse = client.post(f"/api/executions/{RUN}/reprendre")

    assert reponse.status_code == 409, reponse.text
    assert hote.ordres == []


def test_un_run_dont_l_hote_s_est_tu_est_solde_puis_repris() -> None:
    """L'orphelin (#348) : soldé d'abord — l'ordre qu'un hôte encore vivant entendrait —,
    ses tâches en vol interrompues, puis repris sous le même identifiant."""
    battements = RegistreBattementsMemoire()
    asyncio.run(
        battements.battre(
            RUN, horodatage=horodatage_battement(datetime.now(UTC) - timedelta(hours=2))
        )
    )
    hote = HoteEspion()
    state = ControlTowerState()
    with _app(
        hote, _journal_du_run(issue=None), _acquis(), state=state, battements=battements
    ) as client:
        assert _resume(client)["vitalite"] == "orphelin"

        reponse = client.post(f"/api/executions/{RUN}/reprendre")

        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["run_id"] == RUN
        _attendre(
            lambda: state.tache(PAGES) is not None
            and state.tache(PAGES).statut == STATUT_INTERROMPUE,  # type: ignore[union-attr]
            "les pages soldées avant la reprise",
        )
        execution = state.execution(RUN)
        assert execution is not None and execution.statut == EXECUTION_EN_COURS
        details = [
            e.detail for e in execution.evenements if e.type == EVENEMENT_EXECUTION_STATUT
        ]
        assert DETAIL_HOTE_TU in details
    assert hote.annules == [RUN]
    assert [o.run_id for o in hote.ordres] == [RUN]


def test_un_run_sans_brief_approuve_se_reprend_sur_son_plan() -> None:
    """docs/28 §12.7 : un run éteint sans brief approuvé n'était reprenable **du tout**.

    #1174 a refermé le cas du fil (l'accord qu'on y donne vaut approbation), pas celui
    d'un run lancé `auto` hors du fil, ou `sans` — celui de ce décor. La relance exige
    un brief approuvé et le refuse en `422` ; la reprise sur son plan, elle, n'a
    besoin que de ce que le run a payé.
    """
    hote = HoteEspion()
    with _app(hote, _journal_du_run(), _acquis()) as client:
        assert client.post(f"/api/executions/{RUN}/relancer").status_code == 422
        assert client.post(f"/api/executions/{RUN}/reprendre").status_code == 200


def test_sans_rien_d_acquis_ni_brief_approuve_la_reprise_le_dit() -> None:
    """Arrêté avant son plan, sans cadrage validé : rien à reprendre, et le refus le dit."""
    hote = HoteEspion()
    with _app(hote, _journal_du_run(), _acquis(avec_plan=False)) as client:
        reponse = client.post(f"/api/executions/{RUN}/reprendre")

    assert reponse.status_code == 422, reponse.text
    assert hote.ordres == []


def test_un_etat_illisible_refuse_au_lieu_de_relancer() -> None:
    """Un stockage en panne ne se rattrape pas en refaisant un plan qui existe : `503`."""

    class MagasinMuet(MagasinAcquisMemoire):
        async def lire(self, run_id):  # type: ignore[override]
            raise ConnectionError("Redis injoignable")

    hote = HoteEspion()
    with _app(hote, _journal_du_run(), MagasinMuet()) as client:
        reponse = client.post(f"/api/executions/{RUN}/reprendre")
        resume = _resume(client)

    assert reponse.status_code == 503, reponse.text
    assert hote.ordres == []
    assert resume["statut"] == EXECUTION_ANNULEE
    assert resume["cause"] == CAUSE_EXTINCTION


def test_la_purge_vide_l_etat_acquis_des_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un poste vide n'a plus rien à reprendre : l'état acquis part avec l'état d'exécution."""
    from maestro.controltower import purge
    from maestro.espace import nom_redis

    serveur = brancher(monkeypatch)
    magasin = MagasinAcquisRedis("redis://factice")

    async def ranger() -> None:
        await magasin.poser_plan(RUN, PLAN)
        await magasin.acquerir(RUN, _issue(SOCLE))

    asyncio.run(ranger())
    assert f"{nom_redis(PREFIXE_ACQUIS)}{RUN}" in serveur.cles()


    client = ClientSynchrone(serveur)
    perimetre = purge.Perimetre(
        espace=purge.espace_courant(),
        journal="j",
        battements="b",
        prefixe_acquis=nom_redis(PREFIXE_ACQUIS),
        file_taches="f",
        prefixe_boites="boite:",
        diffusion="diffusion",
        conversations=tmp_path / "c",
        ingestion=tmp_path / "i",
        projets=tmp_path / "p",
    )
    assert purge.inventaire(client, perimetre, projets=False).acquis == 1  # type: ignore[arg-type]
    purge.purger(client, perimetre, projets=False)  # type: ignore[arg-type]
    assert not any(cle.startswith(nom_redis(PREFIXE_ACQUIS)) for cle in serveur.cles())


# ========================================== ④ de bout en bout, l'hôte en process


def test_de_bout_en_bout_un_run_eteint_reprend_sur_son_plan_sans_rien_refaire() -> None:
    """Le premier critère, entier, sur la vraie app, le vrai moteur et l'hôte en process.

    Un run part (`sans` brief), fait son socle, et Maestro s'éteint pendant ses pages
    (`POST /api/extinction`, la porte de `start.sh --stop`). « Reprendre » le continue :
    le socle n'est exécuté qu'**une** fois sur toute sa vie, les pages repartent et
    lisent sa sortie, les tests suivent, et le run finit `terminee` **sous son
    identifiant d'origine**. Le plan n'est demandé qu'une fois.
    """
    planificateur = PlanificateurCompte(_plan_json())
    executeur = ExecuteurQuiCoupe(bloquees=(PAGES,))

    def fabrique(**reglages: Any) -> OrchestrationEngine:
        return _moteur(planificateur, executeur, reglages["acquis"])

    state = ControlTowerState()
    app = create_app(
        bus=InMemoryEventBus(),
        state=state,
        event_log=InMemoryEventLog(),
        fabrique_moteur=fabrique,
        acquis=MagasinAcquisMemoire(),
    )
    with TestClient(app) as client:
        lance = client.post(
            "/api/executions", json={"objectif": "Un site de recettes", "brief": "sans"}
        )
        assert lance.status_code == 202, lance.text
        run_id = lance.json()["run_id"]
        _attendre(lambda: PAGES in executeur.executees, "les pages en vol")

        assert client.post("/api/extinction").status_code == 200
        eteint = _resume(client, run_id)
        assert (eteint["statut"], eteint["cause"]) == (EXECUTION_ANNULEE, CAUSE_EXTINCTION)

        reprise = client.post(f"/api/executions/{run_id}/reprendre")
        assert reprise.status_code == 200, reprise.text
        assert reprise.json()["run_id"] == run_id
        _attendre(
            lambda: _resume(client, run_id)["statut"] == EXECUTION_TERMINEE,
            "le run repris au bout",
        )
        fin = _resume(client, run_id)

    assert executeur.executees == [SOCLE, PAGES, PAGES, TESTS]
    (socle_lu,) = executeur.dependances_recues[PAGES]
    assert (socle_lu.task_id, socle_lu.sortie) == (SOCLE, f"LIVRABLE {SOCLE}")
    assert planificateur.appels == 1
    assert fin["progression"]["terminees"] == 3
    assert fin["reprise_de"] == ""
