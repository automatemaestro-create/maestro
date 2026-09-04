"""L'écriture en temps réel du projet — les tests différés des lots de #703 (ticket #707).

Le parent #703 renverse la décision D2 de docs/24 §2.4 : le projet de
l'utilisateur **se remplit pendant le run**, tâche par tâche, au lieu d'attendre un
geste d'application qui n'avait aucun appelant (défaut B1 de #568). Chaque lot a
livré sa pièce en différant ses tests ici, sauf ce qui était critique et gardé sur
place — la frontière d'écriture et la sérialisation d'un projet non versionné
(`tests/test_espace_projet.py`, #839), le régime d'accord par run
(`tests/test_accord_ecriture_continue.py`, #706). Cette suite garde le reste, dans
l'ordre des lots :

1. **#704** — une racine non versionnée devient un dépôt Git **sur demande**
   (`ProjetStore.versionner`), et jamais autrement : rien de ce qui parcourt un
   projet n'initialise quoi que ce soit, un projet déjà versionné est rendu tel
   quel, et un échec laisse la racine dans l'état d'avant, avec son motif ;
2. **#705** — la branche d'une tâche soldée **en succès** est fusionnée dans la
   branche de base ; une tâche en **échec** ne fusionne rien et sa branche conserve
   le travail ; la tâche suivante **voit** le travail de la précédente (défaut B2 de
   #568, rejoué dans les deux sens) ; une fusion refusée — conflit, racine occupée,
   chemin hors périmètre — laisse le projet intact, le dit, et ne fait pas échouer
   la tâche qui vient de réussir.

Vrais dépôts jetables, vrais worktrees, vraies fusions et vrais conflits — rien
n'est simulé côté Git (sautés sans `git`). Le seul refus **joué** est celui du
périmètre : Git ne sait pas produire un chemin hors de son propre arbre, et la
brique qui le refuse (`chemin_dans_racine`) est éprouvée sur du vrai disque dans
`tests/test_projets.py` ; ce qu'on garde ici est sa **place** dans le geste — avant
l'accord, avant l'écriture. Aucun réseau, aucun modèle : les fournisseurs sont
des fonctions du test qui écrivent dans l'espace qu'on leur donne.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.engine import OrchestrationEngine
from maestro.engine.executor import (
    STATUT_ECRITURE_EN_PLACE,
    STATUT_FUSION_FAITE,
    STATUT_FUSION_NON_ACCORDEE,
    STATUT_FUSION_NON_TENTEE,
    STATUT_FUSION_REFUSEE,
    LocalExecutor,
)
from maestro.engine.guardrails import DemandeValidation, Guardrails, Validateur
from maestro.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task
from maestro.projets import (
    MESSAGE_PREMIER_COMMIT,
    ApplicationRefusee,
    Projet,
    ProjetStore,
    VersionnementRefuse,
    detecter_vcs,
    initialiser_depot,
)
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
    """Coupe l'identité Git du poste (#333) : commits et fusions passent par le `-c` du code."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig-absent"))


# --------------------------------------------------------------------------- #
# Fabriques
# --------------------------------------------------------------------------- #


def _git(cwd: Path, *arguments: str) -> str:
    """Lance `git` dans `cwd` et rend sa sortie — échoue le test si Git échoue."""
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


def _commiter(cwd: Path, message: str) -> None:
    """Un commit avec une identité posée par `-c` (les dépôts jetables n'en ont pas)."""
    _git(cwd, "add", "-A")
    _git(
        cwd,
        "-c",
        "user.email=tests@maestro",
        "-c",
        "user.name=Tests",
        "commit",
        "--quiet",
        "-m",
        message,
    )


def _racine_nue(tmp_path: Path, nom: str = "agenda") -> Path:
    """Un dossier de projet **sans** `.git`, avec un fichier : ce que déclare un utilisateur."""
    racine = tmp_path / "projets" / nom
    (racine / "src").mkdir(parents=True)
    (racine / "README.md").write_text("# Agenda\n", encoding="utf-8")
    (racine / "src" / "app.py").write_text("un\ndeux\n", encoding="utf-8")
    return racine


def _projet_nu(tmp_path: Path) -> tuple[ProjetStore, Projet]:
    """Un dépôt de projets et un projet **non versionné** qui y est déclaré."""
    depot = ProjetStore(tmp_path / "depot")
    projet = depot.creer("Agenda", _racine_nue(tmp_path))
    assert not projet.versionne, projet
    return depot, projet


def _projet_git(tmp_path: Path) -> tuple[ProjetStore, Projet]:
    """Un dépôt de projets et un projet **versionné** : dépôt jetable, un commit, VCS détecté."""
    depot = ProjetStore(tmp_path / "depot")
    racine = _racine_nue(tmp_path, "agenda-git")
    _git(racine, "init", "--quiet")
    _git(racine, "symbolic-ref", "HEAD", "refs/heads/main")
    _commiter(racine, "socle")
    projet = depot.creer("Agenda", racine)
    assert projet.versionne, projet
    return depot, projet


def _commits(racine: Path, revision: str = "HEAD") -> list[str]:
    """Les sujets des commits de `revision`, du plus récent au plus ancien."""
    return _git(racine, "log", "--format=%s", revision).splitlines()


def _fusions(racine: Path) -> list[str]:
    """Les sujets des seuls commits **de fusion** de la branche courante."""
    return _git(racine, "log", "--merges", "--format=%s").splitlines()


def _sur_la_branche(racine: Path, tache_id: str, fichier: str) -> str:
    """Le contenu de `fichier` sur la branche de tâche — la preuve que le travail est conservé."""
    return _git(racine, "show", f"{branche_de_tache(tache_id)}:{fichier}").strip()


def _branche_existe(racine: Path, tache_id: str) -> bool:
    branche = branche_de_tache(tache_id)
    return branche in _git(racine, "branch", "--list", branche)


# --------------------------------------------------------------------------- #
# Fournisseurs factices : ils écrivent dans l'espace qu'on leur donne, rien d'autre
# --------------------------------------------------------------------------- #


def _livrable(prompt: str) -> str:
    """Le nom du fichier que la description de la tâche demande (`livrable:<nom>`)."""
    nom = re.search(r"livrable:([\w./-]+)", prompt)
    assert nom is not None, prompt
    return nom.group(1)


class _Ecrivain(ModelProvider):
    """Dépose le livrable nommé par la tâche, et note ce que l'espace contenait en entrant.

    Ce relevé à l'entrée est **la** mesure du défaut B2 : ce qu'une tâche voit de
    la précédente ne se lit nulle part ailleurs que dans son espace au moment où
    elle démarre.
    """

    name = "ecrivain"

    def __init__(self) -> None:
        self.vus: dict[str, list[str]] = {}

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **canaux):
        nom = _livrable(prompt)
        espace = Path(workspace)
        self.vus[nom] = sorted(
            chemin.relative_to(espace).as_posix()
            for chemin in espace.rglob("*")
            if chemin.is_file() and ".git" not in chemin.relative_to(espace).parts
        )
        cible = espace / nom
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text(f"fait par {nom}", encoding="utf-8")
        return "Fait."


class _QuiTombe(ModelProvider):
    """Écrit le livrable puis **échoue** : le travail doit survivre à l'échec, hors du projet."""

    name = "tombe"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **canaux):
        (Path(workspace) / _livrable(prompt)).write_text("écrit puis tombé", encoding="utf-8")
        raise RuntimeError("aléa fournisseur")


class _PendantQueLHumainEdite(ModelProvider):
    """Écrit son livrable **pendant que l'humain touche à la racine** — le cas de la fusion refusée.

    `geste` est joué sur la racine au milieu de la tâche, c'est-à-dire **après** le
    montage du worktree (qui part de la base d'avant) et **avant** la fusion (qui
    vise la base d'après) : c'est exactement la fenêtre où un conflit ou une racine
    occupée peuvent naître.
    """

    name = "concurrent"

    def __init__(self, geste) -> None:
        self._geste = geste

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **canaux):
        nom = _livrable(prompt)
        (Path(workspace) / nom).write_text("version de l'agent\n", encoding="utf-8")
        self._geste()
        return "Fait."


class _Validateur:
    """Le validateur du test : note chaque demande et répond toujours `reponse`."""

    def __init__(self, reponse: bool) -> None:
        self.reponse = reponse
        self.demandes: list[DemandeValidation] = []

    async def __call__(self, demande: DemandeValidation) -> bool:
        self.demandes.append(demande)
        return self.reponse


def _tache(id_: str, projet_id: str, livrable: str, *, dependances: tuple[str, ...] = ()) -> Task:
    """Une tâche que le routeur confie au développeur, seul rôle outillé monté ici."""
    return Task(
        id=id_,
        titre=f"Tâche {id_}",
        description=f"livrable:{livrable}",
        competences_requises=("backend",),
        format_sortie="markdown",
        dependances=dependances,
        projet_id=projet_id,
    )


def _executeur(
    fournisseur: ModelProvider, depot: ProjetStore, validateur: Validateur | None = None
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


def _joue(executeur: LocalExecutor, journal: RunJournal, *taches: Task):
    """Exécute les tâches l'une après l'autre dans le même run, chacune recevant ses dépendances."""

    async def _scenario():
        acquis = {}
        resultats = []
        for tache in taches:
            dependances = [acquis[d] for d in tache.dependances]
            resultat = await executeur.execute(tache, dependances, journal)
            acquis[tache.id] = resultat
            resultats.append(resultat)
        return resultats

    return asyncio.run(_scenario())


# --------------------------------------------------------------------------- #
# Lot 1 (#704) — un projet non versionné peut le devenir, sur demande et jamais autrement
# --------------------------------------------------------------------------- #


def test_versionner_fait_de_la_racine_declaree_un_depot_dont_la_base_existe(tmp_path: Path):
    depot, projet = _projet_nu(tmp_path)
    racine = Path(projet.racine)

    versionne = depot.versionner(projet.id)

    # Le `Vcs` est celui que Git a posé — constaté par `detecter_vcs`, jamais fabriqué.
    assert versionne.versionne and versionne.vcs is not None
    assert versionne.vcs.type == "git" and versionne.vcs.branche_base
    assert detecter_vcs(racine) == versionne.vcs
    # …et persisté : le dépôt des projets relit le projet versionné.
    relu = depot.lire(projet.id)
    assert relu is not None and relu.vcs == versionne.vcs
    # Ce n'est pas un `git init` nu : la branche de base **résout**, portée par un premier
    # commit qui enregistre la racine telle qu'elle est — sans lui, `HEAD` désigne une branche
    # qui n'existe pas et rien de ce que #703 vise ne fonctionne (worktree orphelin, base
    # introuvable).
    base = versionne.vcs.branche_base
    _git(racine, "rev-parse", "--verify", "--quiet", f"refs/heads/{base}")
    assert _commits(racine) == [MESSAGE_PREMIER_COMMIT]
    assert set(_git(racine, "ls-files").split()) == {"README.md", "src/app.py"}
    assert _git(racine, "status", "--porcelain").strip() == ""  # rien laissé hors de l'index


def test_un_projet_versionne_par_maestro_sert_de_base_a_la_tache_suivante(tmp_path: Path):
    """« Renseigné comme s'il l'avait toujours été » : la première tâche part du projet entier."""
    depot, projet = _projet_nu(tmp_path)
    racine = Path(projet.racine)
    depot.versionner(projet.id)
    fournisseur = _Ecrivain()
    journal = RunJournal(run_id="run-704")

    (resultat,) = _joue(
        _executeur(fournisseur, depot, _Validateur(True)),
        journal,
        _tache("t1", projet.id, "t1.md"),
    )

    assert resultat.ok
    # Le worktree de la tâche est parti de la base : l'agent y a vu le projet tel qu'il était.
    assert fournisseur.vus["t1.md"] == ["README.md", "src/app.py"]
    # Et le régime versionné a joué jusqu'au bout : fusion faite, racine avancée.
    assert _etape(journal, "t1", ":fusion").statut == STATUT_FUSION_FAITE
    assert (racine / "t1.md").read_text(encoding="utf-8") == "fait par t1.md"
    (fusion,) = _fusions(racine)
    assert branche_de_tache("t1") in fusion


def test_rien_n_initialise_un_depot_sans_qu_on_le_demande(tmp_path: Path):
    """Le geste est explicite ou n'a pas lieu : déclarer, lire, exécuter ne posent aucun `.git`."""
    depot, projet = _projet_nu(tmp_path)
    racine = Path(projet.racine)
    assert not (racine / ".git").exists()  # `creer` a constaté Git, sans l'imposer
    assert detecter_vcs(racine) is None

    # Une tâche entière sur ce projet — espace en place, journal, tout — n'en pose pas non plus.
    journal = RunJournal(run_id="run-704")
    (resultat,) = _joue(
        _executeur(_Ecrivain(), depot, _Validateur(True)),
        journal,
        _tache("t1", projet.id, "t1.md"),
    )
    assert resultat.ok
    assert (racine / "t1.md").is_file()  # écrit en place (#839)…
    assert not (racine / ".git").exists()  # …sans qu'aucun dépôt ne soit né
    assert _etape(journal, "t1", ":fusion").statut == STATUT_ECRITURE_EN_PLACE
    relu = depot.lire(projet.id)
    assert relu is not None and relu.vcs is None


def test_un_projet_deja_versionne_est_rendu_tel_quel(tmp_path: Path):
    depot, projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    avant = _commits(racine)

    # Par le verbe de bas niveau : le `Vcs` constaté, et pas une commande de plus.
    assert initialiser_depot(racine) == projet.vcs
    assert _commits(racine) == avant
    # Par le dépôt : le projet est rendu tel quel, sans même un `modifie_le` rafraîchi.
    assert depot.versionner(projet.id) == projet
    assert _commits(racine) == avant


def test_une_declaration_en_retard_sur_le_disque_est_rattrapee_sans_rien_creer(tmp_path: Path):
    """Un `git init` à la main depuis la déclaration : `versionner` rattrape, n'initialise pas."""
    depot, projet = _projet_nu(tmp_path)
    racine = Path(projet.racine)
    _git(racine, "init", "--quiet")
    _git(racine, "symbolic-ref", "HEAD", "refs/heads/main")
    _commiter(racine, "socle posé à la main")

    versionne = depot.versionner(projet.id)

    assert versionne.vcs == detecter_vcs(racine)
    assert versionne.vcs is not None and versionne.vcs.branche_base == "main"
    assert _commits(racine) == ["socle posé à la main"]  # aucun commit de Maestro


def test_une_racine_dans_un_autre_depot_est_refusee_sans_rien_toucher(tmp_path: Path):
    """Un dépôt imbriqué modifierait celui du dessus : refus `depot-englobant`, rien n'a bougé."""
    englobant = tmp_path / "projets" / "monorepo"
    englobant.mkdir(parents=True)
    _git(englobant, "init", "--quiet")
    (englobant / "README.md").write_text("# Mono\n", encoding="utf-8")
    _commiter(englobant, "socle")
    racine = englobant / "paquets" / "agenda"
    racine.mkdir(parents=True)
    (racine / "app.py").write_text("x\n", encoding="utf-8")
    depot = ProjetStore(tmp_path / "depot")
    projet = depot.creer("Agenda", racine)
    assert not projet.versionne  # `detecter_vcs` ne regarde que `<racine>/.git`

    with pytest.raises(VersionnementRefuse) as refus:
        depot.versionner(projet.id)

    assert refus.value.motif == "depot-englobant"
    assert not (racine / ".git").exists()
    assert _commits(englobant) == ["socle"]  # le dépôt du dessus n'a rien vu passer
    etat = _git(englobant, "status", "--porcelain", "--untracked-files=all")
    assert etat.split() == ["??", "paquets/agenda/app.py"]  # tel qu'avant : non suivi, rien d'autre
    relu = depot.lire(projet.id)
    assert relu is not None and relu.vcs is None  # rien d'enregistré sur un refus


def test_un_premier_commit_refuse_retire_le_depot_qui_vient_de_naitre(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Les hooks de l'utilisateur tiennent : un `pre-commit` qui refuse annule tout."""
    depot, projet = _projet_nu(tmp_path)
    racine = Path(projet.racine)
    _pre_commit_qui_refuse(tmp_path, monkeypatch)

    with pytest.raises(VersionnementRefuse) as refus:
        depot.versionner(projet.id)

    assert refus.value.motif == "commit-refuse"
    assert not (racine / ".git").exists()  # le `.git` né ici est reparti avec l'échec
    assert (racine / "README.md").read_text(encoding="utf-8") == "# Agenda\n"  # le reste : intact
    relu = depot.lire(projet.id)
    assert relu is not None and relu.vcs is None


def test_un_git_residuel_trouve_sur_place_n_est_jamais_retire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un `.git` sans `HEAD` (clone avorté) n'est pas un dépôt — mais il est à l'utilisateur."""
    depot, projet = _projet_nu(tmp_path)
    racine = Path(projet.racine)
    residuel = racine / ".git"
    residuel.mkdir()
    (residuel / "description").write_text("résidu\n", encoding="utf-8")
    assert detecter_vcs(racine) is None  # le motif : sans `HEAD`, ce n'est pas un dépôt
    _pre_commit_qui_refuse(tmp_path, monkeypatch)

    with pytest.raises(VersionnementRefuse) as refus:
        depot.versionner(projet.id)

    assert refus.value.motif == "commit-refuse"
    assert residuel.is_dir()  # présent avant l'appel, donc pas à nous : laissé en place


def _pre_commit_qui_refuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un `core.hooksPath` global dont le `pre-commit` refuse tout commit."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'refusé par le pre-commit' >&2\nexit 1\n", encoding="utf-8")
    os.chmod(hook, 0o755)
    config = tmp_path / "gitconfig-hooks"
    config.write_text(f"[core]\n\thooksPath = {hooks.as_posix()}\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))


# --------------------------------------------------------------------------- #
# Lot 2 (#705) — fusion à la clôture d'une tâche en succès, rien sur un échec
# --------------------------------------------------------------------------- #


def test_une_tache_soldee_en_succes_est_fusionnee_dans_la_base(tmp_path: Path):
    depot, projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    journal = RunJournal(run_id="run-705")

    (resultat,) = _joue(
        _executeur(_Ecrivain(), depot, _Validateur(True)),
        journal,
        _tache("t1", projet.id, "docs/t1.md"),
    )

    assert resultat.ok
    # La racine a avancé, par un commit de fusion sur la branche de base…
    assert (racine / "docs" / "t1.md").read_text(encoding="utf-8") == "fait par docs/t1.md"
    assert _git(racine, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    (fusion,) = _fusions(racine)
    assert branche_de_tache("t1") in fusion
    assert _git(racine, "status", "--porcelain").strip() == ""
    # …le worktree est retiré (il ne reste que la racine), jamais la branche.
    assert len(_git(racine, "worktree", "list").splitlines()) == 1
    assert _branche_existe(racine, "t1")
    etape = _etape(journal, "t1", ":fusion")
    assert etape.statut == STATUT_FUSION_FAITE
    assert "docs/t1.md" in etape.sortie


def test_une_tache_en_echec_ne_fusionne_rien_et_sa_branche_conserve_le_travail(tmp_path: Path):
    depot, projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    validateur = _Validateur(True)
    journal = RunJournal(run_id="run-705")

    (resultat,) = _joue(
        _executeur(_QuiTombe(), depot, validateur),
        journal,
        _tache("t1", projet.id, "t1.md"),
    )

    assert not resultat.ok
    # Le projet est intact : ni fichier, ni commit de fusion, ni question posée.
    assert not (racine / "t1.md").exists()
    assert _fusions(racine) == []
    assert _commits(racine) == ["socle"]
    assert validateur.demandes == []
    # Mais le travail existe, sur la branche, commité au démontage du worktree.
    assert _sur_la_branche(racine, "t1", "t1.md") == "écrit puis tombé"
    etape = _etape(journal, "t1", ":fusion")
    assert etape.statut == STATUT_FUSION_NON_TENTEE
    assert "tâche en échec" in etape.sortie and branche_de_tache("t1") in etape.sortie


# --------------------------------------------------------------------------- #
# Lot 2 (#705) — la tâche suivante voit le travail de la précédente (défaut B2 de #568)
# --------------------------------------------------------------------------- #


def test_la_tache_dependante_part_du_travail_de_la_precedente(tmp_path: Path):
    """Le défaut B2, rejoué dans les deux sens : sans fusion il est là, avec elle il tombe."""
    # Le motif d'abord — **sans** fusion (accord refusé, la base n'avance pas), la seconde tâche
    # repart d'un espace qui ignore la première : c'est le défaut mesuré par #568.
    depot, projet = _projet_git(tmp_path)
    temoin = _Ecrivain()
    journal = RunJournal(run_id="run-b2-avant")
    _joue(
        _executeur(temoin, depot, _Validateur(False)),
        journal,
        _tache("t1", projet.id, "t1.md"),
        _tache("t2", projet.id, "t2.md", dependances=("t1",)),
    )
    assert _etape(journal, "t1", ":fusion").statut == STATUT_FUSION_NON_ACCORDEE
    assert "t1.md" not in temoin.vus["t2.md"]

    # Puis la règle — **avec** fusion, le worktree de t2 est monté après la fusion de t1, donc
    # depuis une base qui porte déjà son travail.
    depot, projet = _projet_git(tmp_path / "apres")
    racine = Path(projet.racine)
    fournisseur = _Ecrivain()
    journal = RunJournal(run_id="run-b2-apres")
    resultats = _joue(
        _executeur(fournisseur, depot, _Validateur(True)),
        journal,
        _tache("t1", projet.id, "t1.md"),
        _tache("t2", projet.id, "t2.md", dependances=("t1",)),
    )
    assert all(r.ok for r in resultats)
    assert "t1.md" in fournisseur.vus["t2.md"]
    assert _etape(journal, "t1", ":fusion").statut == STATUT_FUSION_FAITE
    assert _etape(journal, "t2", ":fusion").statut == STATUT_FUSION_FAITE
    # Et la racine porte les deux, dans l'ordre : deux fusions, tâche par tâche.
    assert (racine / "t1.md").is_file() and (racine / "t2.md").is_file()
    assert [branche_de_tache("t2") in f for f in _fusions(racine)] == [True, False]


class _Planificateur(ModelProvider):
    """Un fournisseur qui rend toujours le même plan — le seul rôle du planificateur ici."""

    name = "plan"

    def __init__(self, plan: str) -> None:
        self._plan = plan

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._plan

    async def run_agent(self, prompt, **kwargs):  # pragma: no cover
        raise AssertionError("le planificateur ne s'exécute pas outillé")


def test_la_boucle_du_moteur_ne_libere_l_aval_qu_une_fois_la_fusion_faite(tmp_path: Path):
    """Le même enchaînement, par la vraie boucle (#43) : l'aval attend le retour de `execute`."""
    depot, projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    fournisseur = _Ecrivain()
    plan = json.dumps(
        [
            {
                "id": "socle",
                "titre": "Socle",
                "description": "livrable:socle.md",
                "competences_requises": ["backend"],
                "format_sortie": "markdown",
                "dependances": [],
            },
            {
                "id": "suite",
                "titre": "Suite",
                "description": "livrable:suite.md",
                "competences_requises": ["backend"],
                "format_sortie": "markdown",
                "dependances": ["socle"],
            },
        ],
        ensure_ascii=False,
    )
    moteur = OrchestrationEngine(
        fournisseur,
        Orchestrator(_Planificateur(plan), model="factice"),
        executor=_executeur(fournisseur, depot, _Validateur(True)),
    )
    journal = RunJournal(run_id="run-boucle")

    rapport = asyncio.run(moteur.run("Un agenda", journal=journal, projet_id=projet.id))

    assert all(r.ok for r in rapport.resultats)
    assert "socle.md" in fournisseur.vus["suite.md"]
    assert (racine / "socle.md").is_file() and (racine / "suite.md").is_file()
    assert _etape(journal, "socle", ":fusion").statut == STATUT_FUSION_FAITE
    assert _etape(journal, "suite", ":fusion").statut == STATUT_FUSION_FAITE


# --------------------------------------------------------------------------- #
# Lot 2 (#705) — une fusion refusée laisse le projet intact, le dit, et la tâche reste réussie
# --------------------------------------------------------------------------- #


def test_un_conflit_refuse_la_fusion_et_laisse_le_projet_intact(tmp_path: Path):
    depot, projet = _projet_git(tmp_path)
    racine = Path(projet.racine)

    def _edition_concurrente() -> None:
        # L'humain change la même ligne de son côté, et la commite : la fusion ne peut pas trancher.
        (racine / "src" / "app.py").write_text("version de l'humain\n", encoding="utf-8")
        _commiter(racine, "édition concurrente")

    validateur = _Validateur(True)
    journal = RunJournal(run_id="run-705")
    (resultat,) = _joue(
        _executeur(_PendantQueLHumainEdite(_edition_concurrente), depot, validateur),
        journal,
        _tache("t1", projet.id, "src/app.py"),
    )

    assert resultat.ok  # le sort du projet n'est pas le verdict de la tâche
    # Le projet est intact : la version de l'humain, une racine propre, sur sa branche, sans
    # fusion — `merge --abort` a été joué.
    assert (racine / "src" / "app.py").read_text(encoding="utf-8") == "version de l'humain\n"
    assert _git(racine, "status", "--porcelain").strip() == ""
    assert _git(racine, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _fusions(racine) == []
    assert not (racine / ".git" / "MERGE_HEAD").exists()
    # La branche conserve le travail de l'agent, à reprendre à la main.
    assert _sur_la_branche(racine, "t1", "src/app.py") == "version de l'agent"
    etape = _etape(journal, "t1", ":fusion")
    assert etape.statut == STATUT_FUSION_REFUSEE
    assert "fusion-refusee" in etape.sortie and "le projet est intact" in etape.sortie
    # L'accord avait été donné : c'est Git qui a refusé, et la phrase le distingue.
    assert len(validateur.demandes) == 1


def test_une_racine_occupee_refuse_la_fusion_sans_toucher_aux_changements_en_cours(tmp_path):
    depot, projet = _projet_git(tmp_path)
    racine = Path(projet.racine)

    def _edition_non_commitee() -> None:
        (racine / "README.md").write_text("# En cours d'édition\n", encoding="utf-8")

    journal = RunJournal(run_id="run-705")
    (resultat,) = _joue(
        _executeur(_PendantQueLHumainEdite(_edition_non_commitee), depot, _Validateur(True)),
        journal,
        _tache("t1", projet.id, "t1.md"),
    )

    assert resultat.ok
    # Rien n'a été fusionné sous les pieds de l'humain : son édition est là, intacte, et rien
    # de l'agent n'est entré.
    assert (racine / "README.md").read_text(encoding="utf-8") == "# En cours d'édition\n"
    assert not (racine / "t1.md").exists()
    assert _fusions(racine) == []
    assert _sur_la_branche(racine, "t1", "t1.md") == "version de l'agent"
    etape = _etape(journal, "t1", ":fusion")
    assert etape.statut == STATUT_FUSION_REFUSEE
    assert "racine-occupee" in etape.sortie


def test_un_chemin_hors_perimetre_est_refuse_avant_l_accord_et_avant_d_ecrire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le périmètre (EF-38) se contrôle avant tout : on ne fait pas approuver ce qu'on refusera."""
    depot, projet = _projet_git(tmp_path)
    racine = Path(projet.racine)

    def _refuse(projet_: Projet, diff) -> tuple[Path, ...]:
        raise ApplicationRefusee(
            "hors-racine",
            f"Application refusée — {diff.modifications[0].chemin} sort du périmètre.",
        )

    monkeypatch.setattr("maestro.engine.executor.verifier_perimetre", _refuse)
    validateur = _Validateur(True)
    journal = RunJournal(run_id="run-705")
    (resultat,) = _joue(
        _executeur(_Ecrivain(), depot, validateur),
        journal,
        _tache("t1", projet.id, "t1.md"),
    )

    assert resultat.ok
    assert not (racine / "t1.md").exists()
    assert _commits(racine) == ["socle"]
    assert validateur.demandes == []  # refusé **avant** l'accord : personne n'a été dérangé
    assert _sur_la_branche(racine, "t1", "t1.md") == "fait par t1.md"
    etape = _etape(journal, "t1", ":fusion")
    assert etape.statut == STATUT_FUSION_REFUSEE
    assert "hors-racine" in etape.sortie and "le projet est intact" in etape.sortie
