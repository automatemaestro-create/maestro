"""Le régime d'accord humain d'une écriture continue dans le projet (#706).

Ce que ce lot ajoute est **critique** — c'est l'accord sans lequel Maestro
écrirait dans la branche de travail de l'utilisateur —, d'où ces tests-ci malgré
le report du reste à #707 (lot final « tests + doc » de #703). Ils gardent le
régime tranché et rien d'autre : un run demande **une fois** par projet, à sa
première fusion, avec le diff et son origine (`run_id`, `projet_id` — #570) ; ce
qu'on lui répond vaut pour ses fusions suivantes, refus compris ; un accord non
rendu ne perd rien.

Et depuis #1504, **ce qu'on lui répond survit à l'extinction** : un run repris sur
son plan (#1391) est le même run, et la personne n'est pas consultée deux fois pour
le même accord. Ces tests-là passent par la vraie boucle et un exécuteur **neuf** à
la reprise — c'est ce qu'est un process rallumé —, le seul lien entre les deux
étant le magasin d'état acquis.

Vrai dépôt jetable, vrai worktree, vraie fusion (sautés sans `git`) ; le
validateur est une fonction du test, comme dans `tests/test_application_projet.py`.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.engine import OrchestrationEngine
from maestro.engine.acquis import MagasinAcquisMemoire
from maestro.engine.executor import (
    STATUT_FUSION_FAITE,
    STATUT_FUSION_NON_ACCORDEE,
    STATUT_FUSION_SANS_OBJET,
    STATUT_VALIDATION_APPROUVE,
    STATUT_VALIDATION_REFUSE,
    LocalExecutor,
)
from maestro.engine.guardrails import DemandeValidation, Guardrails, Validateur
from maestro.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task
from maestro.projets.modele import Projet
from maestro.projets.store import ProjetStore
from maestro.providers.base import ModelProvider
from maestro.sandbox import branche_de_tache
from maestro.telemetry import RunJournal

GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(GIT is None, reason="git introuvable")


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`Path.home()` sous `tmp_path` (#221, #224) : sous Windows, `tmp_path` vit sous `AppData`."""
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


@pytest.fixture(autouse=True)
def _sans_identite_git_ambiante(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Coupe l'identité Git du poste (#333) : la fusion doit passer par le `-c` du code."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig-absent"))


# --------------------------------------------------------------------------- #
# Fabriques
# --------------------------------------------------------------------------- #


def _git(racine: Path, *arguments: str) -> str:
    """Lance `git` dans `racine` et rend sa sortie — échoue le test si Git échoue."""
    resultat = subprocess.run(
        [GIT or "git", *arguments],
        cwd=racine,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert resultat.returncode == 0, f"git {' '.join(arguments)} : {resultat.stderr}"
    return resultat.stdout


def _projet_git(tmp_path: Path) -> tuple[ProjetStore, Projet]:
    """Un dépôt de projets et un projet **versionné** : dépôt jetable, un commit, VCS détecté."""
    depot = ProjetStore(tmp_path / "depot")
    racine = tmp_path / "projets" / "agenda"
    racine.mkdir(parents=True)
    (racine / "README.md").write_text("# Agenda\n", encoding="utf-8")
    _git(racine, "init", "--quiet")
    _git(racine, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(racine, "add", "-A")
    _git(
        racine,
        "-c",
        "user.email=tests@maestro",
        "-c",
        "user.name=Tests",
        "commit",
        "--quiet",
        "-m",
        "socle",
    )
    projet = depot.creer("Agenda", racine)
    assert projet.versionne, projet
    return depot, projet


class _Ecrivain(ModelProvider):
    """Fournisseur factice outillé : chaque tâche dépose le fichier que sa description nomme."""

    name = "ecrivain"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **canaux):
        nom = re.search(r"livrable:([\w.-]+)", prompt)
        assert nom is not None, prompt
        (Path(workspace) / nom.group(1)).write_text("fait", encoding="utf-8")
        return "Fait."


class _Muet(ModelProvider):
    """Fournisseur factice qui réussit **sans rien écrire** : rien à faire approuver."""

    name = "muet"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **canaux):
        return "Rien à écrire."


class _Validateur:
    """Le validateur du test : note chaque demande, répond `reponse` — ou n'en finit pas (None)."""

    def __init__(self, reponse: bool | None) -> None:
        self.reponse = reponse
        self.demandes: list[DemandeValidation] = []
        self.sollicite = asyncio.Event()

    async def __call__(self, demande: DemandeValidation) -> bool:
        self.demandes.append(demande)
        self.sollicite.set()
        if self.reponse is None:
            await asyncio.Event().wait()  # jamais levée : l'accord n'est pas rendu
        return bool(self.reponse)


def _tache(id_: str, projet_id: str, livrable: str) -> Task:
    """Une tâche que le routeur confie au développeur, seul rôle outillé monté ici."""
    return Task(
        id=id_,
        titre=f"Tâche {id_}",
        description=f"livrable:{livrable}",
        competences_requises=("backend",),
        format_sortie="markdown",
        projet_id=projet_id,
    )


def _executeur(
    fournisseur: ModelProvider, depot: ProjetStore, validateur: Validateur | None
) -> LocalExecutor:
    runtimes = {DEVELOPER_PROFILE.nom: AgentRuntime(fournisseur, DEVELOPER_PROFILE)}
    return LocalExecutor(
        fournisseur,
        runtimes=runtimes,
        projets=depot,
        guardrails=Guardrails(validateur=validateur),
    )


def _etape(journal: RunJournal, tache_id: str, suffixe: str):
    """L'unique étape `<tâche><suffixe>` du journal, ou l'assertion qui dit ce qu'il y a."""
    etapes = [r for r in journal.records if r.etape == f"{tache_id}{suffixe}"]
    assert len(etapes) == 1, [r.etape for r in journal.records]
    return etapes[0]


async def _joue(executeur: LocalExecutor, journal: RunJournal, *taches: Task):
    """Exécute les tâches l'une après l'autre dans le même run, comme la boucle le ferait."""
    return [await executeur.execute(tache, [], journal) for tache in taches]


def _sur_la_branche(racine: Path, tache_id: str, fichier: str) -> str:
    """Le contenu de `fichier` sur la branche de tâche — la preuve que le travail est conservé."""
    return _git(racine, "show", f"{branche_de_tache(tache_id)}:{fichier}").strip()


# --------------------------------------------------------------------------- #
# Une question par run, à la première fusion, avec de quoi trancher
# --------------------------------------------------------------------------- #


def test_un_run_demande_une_fois_avec_le_diff_et_son_origine(tmp_path: Path) -> None:
    depot, projet = _projet_git(tmp_path)
    validateur = _Validateur(True)
    executeur = _executeur(_Ecrivain(), depot, validateur)
    journal = RunJournal(run_id="run-706")

    resultats = asyncio.run(
        _joue(
            executeur,
            journal,
            _tache("t1", projet.id, "t1.md"),
            _tache("t2", projet.id, "t2.md"),
        )
    )

    assert all(r.ok for r in resultats)
    racine = Path(projet.racine)
    # Les deux fusions ont eu lieu : le projet a avancé tâche par tâche.
    assert (racine / "t1.md").read_text(encoding="utf-8") == "fait"
    assert (racine / "t2.md").read_text(encoding="utf-8") == "fait"
    assert _etape(journal, "t1", ":fusion").statut == STATUT_FUSION_FAITE
    assert _etape(journal, "t2", ":fusion").statut == STATUT_FUSION_FAITE
    # Une seule question, à la première fusion, avec tout ce qu'il faut pour trancher —
    # dont son run et son projet (#570), sans quoi elle n'atteint aucun écran.
    (demande,) = validateur.demandes
    assert demande.task_id == "t1"
    assert demande.run_id == "run-706"
    assert demande.projet_id == projet.id
    assert demande.diff is not None
    assert demande.diff.branche == branche_de_tache("t1")
    assert demande.diff.fichiers == 1
    assert "fusions suivantes" in demande.raison
    # La décision est au journal, sur la tâche qui l'a obtenue — et nulle part ailleurs.
    assert _etape(journal, "t1", ":validation").statut == STATUT_VALIDATION_APPROUVE
    assert not [r for r in journal.records if r.etape == "t2:validation"]


def test_un_autre_run_repose_la_question(tmp_path: Path) -> None:
    depot, projet = _projet_git(tmp_path)
    validateur = _Validateur(True)
    executeur = _executeur(_Ecrivain(), depot, validateur)

    asyncio.run(_joue(executeur, RunJournal(run_id="run-a"), _tache("a1", projet.id, "a1.md")))
    asyncio.run(_joue(executeur, RunJournal(run_id="run-b"), _tache("b1", projet.id, "b1.md")))

    assert [d.run_id for d in validateur.demandes] == ["run-a", "run-b"]


def test_un_diff_vide_ne_derange_personne(tmp_path: Path) -> None:
    depot, projet = _projet_git(tmp_path)
    validateur = _Validateur(True)
    executeur = _executeur(_Muet(), depot, validateur)
    journal = RunJournal(run_id="run-706")

    asyncio.run(_joue(executeur, journal, _tache("t1", projet.id, "rien.md")))

    assert validateur.demandes == []
    assert _etape(journal, "t1", ":fusion").statut == STATUT_FUSION_SANS_OBJET


# --------------------------------------------------------------------------- #
# Ce qu'on répond vaut pour le run, et rien n'est perdu
# --------------------------------------------------------------------------- #


def test_un_refus_vaut_pour_le_run_et_ne_perd_rien(tmp_path: Path) -> None:
    depot, projet = _projet_git(tmp_path)
    validateur = _Validateur(False)
    executeur = _executeur(_Ecrivain(), depot, validateur)
    journal = RunJournal(run_id="run-706")

    resultats = asyncio.run(
        _joue(
            executeur,
            journal,
            _tache("t1", projet.id, "t1.md"),
            _tache("t2", projet.id, "t2.md"),
        )
    )

    # Le sort du projet n'est pas le verdict de la tâche (critère de #705).
    assert all(r.ok for r in resultats)
    racine = Path(projet.racine)
    assert not (racine / "t1.md").exists()
    assert not (racine / "t2.md").exists()
    assert _git(racine, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    # Les branches portent le travail, à fusionner quand on le voudra.
    assert _sur_la_branche(racine, "t1", "t1.md") == "fait"
    assert _sur_la_branche(racine, "t2", "t2.md") == "fait"
    # Une seule question : le non de la première vaut pour la seconde, qui le dit.
    assert len(validateur.demandes) == 1
    premiere = _etape(journal, "t1", ":fusion")
    seconde = _etape(journal, "t2", ":fusion")
    assert premiere.statut == STATUT_FUSION_NON_ACCORDEE
    assert seconde.statut == STATUT_FUSION_NON_ACCORDEE
    assert "refusée par le validateur humain" in premiere.sortie
    assert "à la tâche t1" in seconde.sortie
    assert f"git merge {branche_de_tache('t2')}" in seconde.sortie
    assert _etape(journal, "t1", ":validation").statut == STATUT_VALIDATION_REFUSE


def test_sans_validateur_le_fail_safe_refuse_et_la_branche_reste(tmp_path: Path) -> None:
    depot, projet = _projet_git(tmp_path)
    executeur = _executeur(_Ecrivain(), depot, None)
    journal = RunJournal(run_id="run-706")

    (resultat,) = asyncio.run(_joue(executeur, journal, _tache("t1", projet.id, "t1.md")))

    assert resultat.ok
    racine = Path(projet.racine)
    assert not (racine / "t1.md").exists()
    assert _sur_la_branche(racine, "t1", "t1.md") == "fait"
    etape = _etape(journal, "t1", ":fusion")
    assert etape.statut == STATUT_FUSION_NON_ACCORDEE
    assert "aucun validateur humain configuré" in etape.sortie


def test_un_run_interrompu_pendant_l_attente_laisse_la_branche_et_le_dit(tmp_path: Path) -> None:
    depot, projet = _projet_git(tmp_path)
    validateur = _Validateur(None)
    executeur = _executeur(_Ecrivain(), depot, validateur)
    journal = RunJournal(run_id="run-706")

    async def scenario() -> None:
        execution = asyncio.create_task(
            executeur.execute(_tache("t1", projet.id, "t1.md"), [], journal)
        )
        await asyncio.wait_for(validateur.sollicite.wait(), timeout=30)
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution

    asyncio.run(scenario())

    racine = Path(projet.racine)
    assert not (racine / "t1.md").exists()
    assert _sur_la_branche(racine, "t1", "t1.md") == "fait"
    etape = _etape(journal, "t1", ":fusion")
    assert etape.statut == STATUT_FUSION_NON_ACCORDEE
    assert "interrompu" in etape.sortie
    assert f"git merge {branche_de_tache('t1')}" in etape.sortie
    # Rien n'a été rendu, donc rien n'est retenu : le run suivant reposera la question.
    assert executeur._accords_fusion == {}


# --------------------------------------------------------------------------- #
# Ce qu'on répond survit à l'extinction et à la reprise du run (#1504)
# --------------------------------------------------------------------------- #

RUN_REPRIS = "run-1504"


class _EcrivainQuiCoupe(_Ecrivain):
    """L'écrivain d'un run qu'on éteint : il ne rend jamais la main sur `coupe`.

    `atteint` se lève quand la tâche à couper tourne — c'est l'instant où le test
    éteint le run, comme Maestro qui tombe au milieu d'une tâche.
    """

    def __init__(self, coupe: str) -> None:
        self._coupe = coupe
        self.atteint = asyncio.Event()

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **canaux):
        if f"livrable:{self._coupe}" in prompt:
            self.atteint.set()
            await asyncio.Event().wait()  # jamais levée : l'extinction vient avant
        return await super().run_agent(
            prompt, model=model, system_prompt=system_prompt, workspace=workspace, tools=tools
        )


class _Planificateur(_Muet):
    """Rend le plan du run tel quel — la planification n'est pas ce qu'on éprouve ici."""

    def __init__(self, plan: Sequence[Task]) -> None:
        self._plan = json.dumps([t.to_dict() for t in plan], ensure_ascii=False)

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._plan


def _plan(projet_id: str) -> tuple[Task, ...]:
    """Deux tâches en chaîne : la première fusionne avant l'extinction, la seconde après."""
    return (
        _tache("t1", projet_id, "t1.md"),
        replace(_tache("t2", projet_id, "t2.md"), dependances=("t1",)),
    )


def _moteur(
    plan: Sequence[Task], executeur: LocalExecutor, acquis: MagasinAcquisMemoire
) -> OrchestrationEngine:
    planificateur = _Planificateur(plan)
    return OrchestrationEngine(
        planificateur,
        Orchestrator(planificateur, model="claude-opus-4-8"),
        executor=executeur,
        acquis=acquis,
    )


def _eteint_pendant_t2(
    depot: ProjetStore, projet: Projet, validateur: Validateur | None
) -> MagasinAcquisMemoire:
    """Lance le run, laisse t1 fusionner (ou non), et l'éteint pendant t2."""
    magasin = MagasinAcquisMemoire()
    ecrivain = _EcrivainQuiCoupe("t2.md")
    moteur = _moteur(_plan(projet.id), _executeur(ecrivain, depot, validateur), magasin)

    async def scenario() -> None:
        run = asyncio.create_task(
            moteur.run("Un agenda", journal=RunJournal(run_id=RUN_REPRIS), projet_id=projet.id)
        )
        await asyncio.wait_for(ecrivain.atteint.wait(), timeout=30)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

    asyncio.run(scenario())
    return magasin


def _reprend(
    depot: ProjetStore, projet: Projet, magasin: MagasinAcquisMemoire, validateur: Validateur
) -> RunJournal:
    """Rallume et reprend le run sur son plan : un exécuteur **neuf**, le même `run_id`."""
    journal = RunJournal(run_id=RUN_REPRIS)

    async def scenario() -> None:
        etat = await magasin.lire(RUN_REPRIS)
        assert etat is not None and etat.acquises == ("t1",)
        moteur = _moteur(_plan(projet.id), _executeur(_Ecrivain(), depot, validateur), magasin)
        rapport = await moteur.run(
            "Un agenda", journal=journal, projet_id=projet.id, reprise=etat
        )
        assert all(r.ok for r in rapport.resultats)

    asyncio.run(scenario())
    return journal


def test_l_accord_donne_avant_l_extinction_vaut_pour_le_run_repris(tmp_path: Path) -> None:
    """Le cas du run `fea601a5c629` : l'accord donné à 09:46 n'est pas redemandé à 10:43."""
    depot, projet = _projet_git(tmp_path)
    avant = _Validateur(True)
    magasin = _eteint_pendant_t2(depot, projet, avant)
    racine = Path(projet.racine)
    assert len(avant.demandes) == 1
    assert (racine / "t1.md").read_text(encoding="utf-8") == "fait"

    apres = _Validateur(True)
    journal = _reprend(depot, projet, magasin, apres)

    # La personne n'est pas consultée une seconde fois, et le projet avance quand même.
    assert apres.demandes == []
    assert (racine / "t2.md").read_text(encoding="utf-8") == "fait"
    assert _etape(journal, "t2", ":fusion").statut == STATUT_FUSION_FAITE
    assert not [r for r in journal.records if r.etape.endswith(":validation")]


def test_un_refus_de_la_personne_vaut_aussi_pour_le_run_repris(tmp_path: Path) -> None:
    """Un non est une décision comme un oui : la reprise ne le fait pas redire."""
    depot, projet = _projet_git(tmp_path)
    magasin = _eteint_pendant_t2(depot, projet, _Validateur(False))

    apres = _Validateur(True)
    journal = _reprend(depot, projet, magasin, apres)

    assert apres.demandes == []
    racine = Path(projet.racine)
    assert not (racine / "t2.md").exists()
    assert _sur_la_branche(racine, "t2", "t2.md") == "fait"
    seconde = _etape(journal, "t2", ":fusion")
    assert seconde.statut == STATUT_FUSION_NON_ACCORDEE
    assert "à la tâche t1" in seconde.sortie


def test_un_refus_par_defaut_ne_survit_pas_a_la_reprise(tmp_path: Path) -> None:
    """Le fail-safe n'est pas une décision : personne n'a répondu, la reprise demande.

    Sans validateur, ou canal en panne, la fusion est refusée par défaut (#9) et ce
    refus vaut pour le reste **du process** (#706). Il dit l'état d'un déploiement,
    que le rallumage a pu changer — le retenir au-delà ferait d'une panne passagère
    un refus que la personne n'a jamais donné.
    """
    depot, projet = _projet_git(tmp_path)
    magasin = _eteint_pendant_t2(depot, projet, None)

    apres = _Validateur(True)
    journal = _reprend(depot, projet, magasin, apres)

    (demande,) = apres.demandes
    assert (demande.task_id, demande.run_id) == ("t2", RUN_REPRIS)
    assert _etape(journal, "t2", ":fusion").statut == STATUT_FUSION_FAITE
    assert (Path(projet.racine) / "t2.md").read_text(encoding="utf-8") == "fait"


def test_un_accord_qui_ne_se_range_pas_ne_fait_pas_echouer_le_run(tmp_path: Path) -> None:
    """Le magasin rend un run reprenable, jamais l'inverse (#1391) — l'accord compris.

    Redis injoignable à l'instant de ranger l'accord : le run va au bout et fusionne,
    sous l'accord que ce process a retenu. Seule une reprise future reposerait la
    question.
    """

    class MagasinEnPanne(MagasinAcquisMemoire):
        async def retenir_accord(self, run_id, projet_id, accord):  # type: ignore[override]
            raise ConnectionError("Redis injoignable")

    depot, projet = _projet_git(tmp_path)
    validateur = _Validateur(True)
    moteur = _moteur(
        _plan(projet.id), _executeur(_Ecrivain(), depot, validateur), MagasinEnPanne()
    )

    rapport = asyncio.run(
        moteur.run("Un agenda", journal=RunJournal(run_id=RUN_REPRIS), projet_id=projet.id)
    )

    assert all(r.ok for r in rapport.resultats)
    assert len(validateur.demandes) == 1
    racine = Path(projet.racine)
    assert (racine / "t1.md").read_text(encoding="utf-8") == "fait"
    assert (racine / "t2.md").read_text(encoding="utf-8") == "fait"
