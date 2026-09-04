"""Le régime d'accord humain d'une écriture continue dans le projet (#706).

Ce que ce lot ajoute est **critique** — c'est l'accord sans lequel Maestro
écrirait dans la branche de travail de l'utilisateur —, d'où ces tests-ci malgré
le report du reste à #707 (lot final « tests + doc » de #703). Ils gardent le
régime tranché et rien d'autre : un run demande **une fois** par projet, à sa
première fusion, avec le diff et son origine (`run_id`, `projet_id` — #570) ; ce
qu'on lui répond vaut pour ses fusions suivantes, refus compris ; un accord non
rendu ne perd rien.

Vrai dépôt jetable, vrai worktree, vraie fusion (sautés sans `git`) ; le
validateur est une fonction du test, comme dans `tests/test_application_projet.py`.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.engine.executor import (
    STATUT_FUSION_FAITE,
    STATUT_FUSION_NON_ACCORDEE,
    STATUT_FUSION_SANS_OBJET,
    STATUT_VALIDATION_APPROUVE,
    STATUT_VALIDATION_REFUSE,
    LocalExecutor,
)
from maestro.engine.guardrails import DemandeValidation, Guardrails, Validateur
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
