"""Tests de l'espace de travail dérivé du projet (ticket #224, EF-36, D2).

Couvre les trois critères du lot : **worktree Git** sur une branche dédiée quand
le projet est versionné (branche jamais supprimée), **copie du périmètre** quand
il ne l'est pas (exclusions du socle respectées), et l'invariant qui les tient
tous les deux — aucune écriture d'agent n'atteint la racine, une tâche sans
`projet_id` gardant le `mkdtemp()` d'avant.

Aucun réseau, aucun modèle : les tests qui ont besoin d'un vrai dépôt en montent
un jetable et sont sautés là où `git` manque ; les chemins d'échec de Git (binaire
absent, worktree refusé) sont joués sur un `subprocess.run` factice, donc couverts
partout.
"""

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.engine.executor import LocalExecutor
from maestro.projets.modele import Perimetre, Projet, Vcs
from maestro.projets.racine import RacineRefusee, detecter_vcs
from maestro.projets.store import ProjetStore
from maestro.providers.base import ModelProvider
from maestro.sandbox import EspaceProjetIndisponible, branche_de_tache, espace_de_travail

GIT = shutil.which("git")

besoin_de_git = pytest.mark.skipif(GIT is None, reason="git introuvable")


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `Path.home()` sous `tmp_path`, comme dans les tests du socle (#221).

    Indispensable sous Windows : le `tmp_path` de pytest vit sous `AppData`, que
    `valider_racine` interdit à juste titre — sans cette isolation, toutes les
    racines de projet de ce fichier seraient refusées.
    """
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


# --------------------------------------------------------------------------- #
# Fabriques de projets
# --------------------------------------------------------------------------- #


def _arborescence(racine: Path) -> None:
    """Un projet plausible : du code, de la plomberie et deux gisements de secrets."""
    (racine / "src").mkdir(parents=True)
    (racine / "src" / "app.py").write_text("print('salut')\n", encoding="utf-8")
    (racine / "README.md").write_text("# Démo\n", encoding="utf-8")
    (racine / ".env").write_text("CLE=secrète\n", encoding="utf-8")
    (racine / "secrets").mkdir()
    (racine / "secrets" / "cle.pem").write_text("---", encoding="utf-8")
    (racine / "node_modules").mkdir()
    (racine / "node_modules" / "paquet.js").write_text("module.exports={}", encoding="utf-8")
    (racine / "apps" / "web" / "node_modules").mkdir(parents=True)
    (racine / "apps" / "web" / "node_modules" / "profond.js").write_text("x", encoding="utf-8")
    (racine / "apps" / "web" / "page.tsx").write_text("export default null", encoding="utf-8")
    (racine / "services" / "api").mkdir(parents=True)
    (racine / "services" / "api" / ".env").write_text("JETON=zz\n", encoding="utf-8")
    (racine / "services" / "api" / "secrets").mkdir()
    (racine / "services" / "api" / "secrets" / "jeton.txt").write_text("zz", encoding="utf-8")


def _projet(racine: Path, *, vcs: Vcs | None = None, perimetre: Perimetre | None = None) -> Projet:
    """Un projet inerte pointant sur `racine` (le dépôt n'est pas sollicité ici)."""
    return Projet(
        id="prj-0000dead",
        nom="Démo",
        racine=racine.as_posix(),
        vcs=vcs,
        perimetre=perimetre if perimetre is not None else Perimetre(),
    )


def _projet_copie(tmp_path: Path) -> Projet:
    """Un projet **non versionné** posé sur le disque, avec son arborescence."""
    racine = tmp_path / "projets" / "depensio"
    racine.mkdir(parents=True)
    _arborescence(racine)
    return _projet(racine)


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


def _projet_git(tmp_path: Path, *, branche: str = "main") -> Projet:
    """Un projet **versionné** : dépôt jetable, un commit, VCS détecté comme en vrai."""
    racine = tmp_path / "projets" / "depensio-git"
    racine.mkdir(parents=True)
    _arborescence(racine)
    _git(racine, "init", "--quiet")
    _git(racine, "symbolic-ref", "HEAD", f"refs/heads/{branche}")
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
    return _projet(racine, vcs=detecter_vcs(racine))


# --------------------------------------------------------------------------- #
# Tâche sans projet : le comportement d'avant, au caractère près
# --------------------------------------------------------------------------- #


def test_sans_projet_l_espace_reste_le_repertoire_jetable() -> None:
    with espace_de_travail(None, tache_id="t1") as ws:
        chemin = ws.path
        assert chemin.is_dir()
        assert not any(chemin.iterdir())  # créé vide, comme avant #224
        (chemin / "livrable.md").write_text("fait", encoding="utf-8")
        assert [f.chemin for f in ws.produced_files()] == ["livrable.md"]
    assert not chemin.exists()


# --------------------------------------------------------------------------- #
# Projet non versionné : une copie du périmètre
# --------------------------------------------------------------------------- #


def test_projet_non_versionne_copie_le_perimetre(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    with espace_de_travail(projet, tache_id="t1") as ws:
        copies = {f.relative_to(ws.path).as_posix() for f in ws.path.rglob("*") if f.is_file()}
    assert copies == {"README.md", "src/app.py", "apps/web/page.tsx"}


def test_les_exclusions_du_socle_valent_a_toute_profondeur(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    with espace_de_travail(projet, tache_id="t1") as ws:
        copies = {f.relative_to(ws.path).as_posix() for f in ws.path.rglob("*") if f.is_file()}
    # `.env`, `node_modules` et `secrets/` sont exclus partout, pas seulement à la racine.
    assert not any(chemin.endswith(".env") for chemin in copies)
    assert not any("node_modules" in chemin for chemin in copies)
    assert not any("secrets/" in chemin for chemin in copies)


def test_la_plomberie_git_n_est_pas_recopiee(tmp_path: Path) -> None:
    racine = tmp_path / "projets" / "faux-depot"
    (racine / ".git").mkdir(parents=True)
    (racine / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (racine / "note.md").write_text("bonjour", encoding="utf-8")
    # Projet déclaré non versionné bien qu'un `.git` traîne : c'est `vcs` qui décide
    # du patron, et l'exclusion doit tenir quand même.
    with espace_de_travail(_projet(racine), tache_id="t1") as ws:
        copies = {f.relative_to(ws.path).as_posix() for f in ws.path.rglob("*") if f.is_file()}
    assert copies == {"note.md"}


def test_inclus_restreint_ce_qui_est_copie(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    restreint = Projet(
        id=projet.id,
        nom=projet.nom,
        racine=projet.racine,
        # `./src` et `src` désignent la même chose — un utilisateur écrit les deux.
        perimetre=Perimetre(inclus=("./src",), exclus=Perimetre().exclus),
    )
    with espace_de_travail(restreint, tache_id="t1") as ws:
        copies = {f.relative_to(ws.path).as_posix() for f in ws.path.rglob("*") if f.is_file()}
    # Inclure un dossier inclut son contenu ; le reste du projet n'est pas copié.
    assert copies == {"src/app.py"}


def test_un_motif_a_etoiles_ne_traverse_pas_les_dossiers(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    restreint = Projet(
        id=projet.id,
        nom=projet.nom,
        racine=projet.racine,
        perimetre=Perimetre(inclus=("*.md",), exclus=Perimetre().exclus),
    )
    with espace_de_travail(restreint, tache_id="t1") as ws:
        copies = {f.relative_to(ws.path).as_posix() for f in ws.path.rglob("*") if f.is_file()}
    # `*` s'arrête au segment : `README.md` oui, `docs/guide.md` non — sans quoi
    # `*.md` serait un motif récursif, ce que personne n'écrit en pensant à ça.
    assert copies == {"README.md"}


def test_les_liens_symboliques_ne_sont_jamais_suivis(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    dehors = tmp_path / "dehors"
    dehors.mkdir()
    (dehors / "cle.txt").write_text("secret du poste", encoding="utf-8")
    try:
        (Path(projet.racine) / "src" / "evasion").symlink_to(dehors, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - dépend des droits Windows
        pytest.skip("liens symboliques indisponibles sur ce poste")
    with espace_de_travail(projet, tache_id="t1") as ws:
        copies = {f.relative_to(ws.path).as_posix() for f in ws.path.rglob("*") if f.is_file()}
    assert copies == {"README.md", "src/app.py", "apps/web/page.tsx"}


def test_l_ecriture_de_l_agent_n_atteint_jamais_la_racine(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    racine = Path(projet.racine)
    avant = {f.relative_to(racine).as_posix() for f in racine.rglob("*") if f.is_file()}
    with espace_de_travail(projet, tache_id="t1") as ws:
        # Le chemin de travail est hors de la racine — vérifié, pas supposé.
        with pytest.raises(ValueError):
            ws.path.resolve().relative_to(racine.resolve())
        (ws.path / "src" / "app.py").write_text("print('réécrit')\n", encoding="utf-8")
        (ws.path / "NOUVEAU.md").write_text("livrable", encoding="utf-8")
        produits = [f.chemin for f in ws.produced_files()]
    apres = {f.relative_to(racine).as_posix() for f in racine.rglob("*") if f.is_file()}
    assert avant == apres
    assert (racine / "src" / "app.py").read_text(encoding="utf-8") == "print('salut')\n"
    # Le livrable, c'est ce que l'agent a écrit — pas le projet qu'on lui a prêté.
    assert sorted(produits) == ["NOUVEAU.md", "src/app.py"]


def test_l_espace_est_nettoye_meme_sur_exception(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    capture: dict[str, Path] = {}
    with pytest.raises(RuntimeError):
        with espace_de_travail(projet, tache_id="t1") as ws:
            capture["path"] = ws.path
            raise RuntimeError("boum")
    assert not capture["path"].exists()


def test_keep_conserve_l_espace(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    with espace_de_travail(projet, tache_id="t1", keep=True) as ws:
        chemin = ws.path
    try:
        assert (chemin / "README.md").is_file()
    finally:
        shutil.rmtree(chemin.parent, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Projet versionné : un worktree Git par tâche
# --------------------------------------------------------------------------- #


@besoin_de_git
def test_projet_versionne_monte_un_worktree_sur_une_branche_dediee(tmp_path: Path) -> None:
    projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="t-1") as ws:
        assert (ws.path / "src" / "app.py").is_file()  # l'agent voit le projet
        assert (ws.path / ".env").is_file()  # un worktree porte tout le suivi Git
        with pytest.raises(ValueError):  # …mais hors de la racine
            ws.path.resolve().relative_to(racine.resolve())
        branches = _git(racine, "worktree", "list")
        assert "maestro/t-1" in branches
    assert not ws.path.exists()


@besoin_de_git
def test_le_worktree_est_retire_mais_jamais_la_branche(tmp_path: Path) -> None:
    projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="t-1") as ws:
        (ws.path / "src" / "nouveau.py").write_text("x = 1\n", encoding="utf-8")
        _git(ws.path, "add", "-A")
        _git(
            ws.path,
            "-c",
            "user.email=tests@maestro",
            "-c",
            "user.name=Tests",
            "commit",
            "--quiet",
            "-m",
            "travail de la tâche",
        )
    assert not ws.path.exists()  # worktree retiré
    assert "maestro/t-1" in _git(racine, "branch", "--list", "maestro/t-1")  # branche gardée
    assert "src/nouveau.py" in _git(racine, "show", "--name-only", "--format=", "maestro/t-1")


@besoin_de_git
def test_une_tache_rejouee_retrouve_sa_branche(tmp_path: Path) -> None:
    projet = _projet_git(tmp_path)
    with espace_de_travail(projet, tache_id="t-1") as ws:
        (ws.path / "trace.txt").write_text("premier passage\n", encoding="utf-8")
        _git(ws.path, "add", "-A")
        _git(
            ws.path,
            "-c",
            "user.email=tests@maestro",
            "-c",
            "user.name=Tests",
            "commit",
            "--quiet",
            "-m",
            "premier passage",
        )
    with espace_de_travail(projet, tache_id="t-1") as ws:
        # La branche n'ayant pas été supprimée, le second montage la reprend :
        # une tâche rejouée continue son travail au lieu de l'écraser.
        assert (ws.path / "trace.txt").read_text(encoding="utf-8") == "premier passage\n"


@besoin_de_git
def test_la_branche_part_de_la_branche_de_base_declaree(tmp_path: Path) -> None:
    projet = _projet_git(tmp_path, branche="develop")
    racine = Path(projet.racine)
    assert projet.vcs is not None and projet.vcs.branche_base == "develop"
    # Une branche `main` divergente existe aussi : c'est bien `develop` qui doit servir de base.
    _git(racine, "branch", "main")
    with espace_de_travail(projet, tache_id="t-1"):
        pass
    fusion = _git(racine, "merge-base", "maestro/t-1", "develop").strip()
    assert fusion == _git(racine, "rev-parse", "develop").strip()


@besoin_de_git
def test_une_branche_de_base_disparue_ne_condamne_pas_la_tache(tmp_path: Path) -> None:
    projet = _projet_git(tmp_path)
    disparue = Projet(
        id=projet.id,
        nom=projet.nom,
        racine=projet.racine,
        vcs=Vcs(type="git", branche_base="jamais-creee"),
    )
    with espace_de_travail(disparue, tache_id="t-1") as ws:
        assert (ws.path / "README.md").is_file()  # parti de HEAD, sans broncher


@besoin_de_git
def test_keep_conserve_le_worktree(tmp_path: Path) -> None:
    projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="t-1", keep=True) as ws:
        chemin = ws.path
    try:
        assert (chemin / "README.md").is_file()
        assert chemin.as_posix() in _git(racine, "worktree", "list").replace("\\", "/")
    finally:
        _git(racine, "worktree", "remove", "--force", str(chemin))
        shutil.rmtree(chemin.parent, ignore_errors=True)


@besoin_de_git
def test_un_enregistrement_orphelin_ne_bloque_pas_le_montage(tmp_path: Path) -> None:
    projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="t-1", keep=True) as ws:
        orphelin = ws.path
    # Le répertoire disparaît sans que Git en soit informé (session tuée, disque nettoyé) :
    # sans `worktree prune`, Git refuserait la branche pour cause de « already checked out ».
    shutil.rmtree(orphelin.parent, ignore_errors=True)
    with espace_de_travail(projet, tache_id="t-1") as ws:
        assert (ws.path / "README.md").is_file()
    assert orphelin.as_posix() not in _git(racine, "worktree", "list").replace("\\", "/")


# --------------------------------------------------------------------------- #
# Refus motivés
# --------------------------------------------------------------------------- #


def test_git_absent_est_un_refus_motive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projet = _projet(tmp_path / "vide", vcs=Vcs(type="git", branche_base="main"))
    Path(projet.racine).mkdir(parents=True)

    def _sans_git(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", _sans_git)
    with pytest.raises(EspaceProjetIndisponible) as refus:
        with espace_de_travail(projet, tache_id="t-1"):
            pass  # pragma: no cover - le montage a déjà échoué
    assert refus.value.motif == "git-indisponible"


def test_worktree_refuse_est_un_refus_motive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projet = _projet(tmp_path / "vide", vcs=Vcs(type="git", branche_base="main"))
    Path(projet.racine).mkdir(parents=True)

    def _git_fache(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git"], returncode=128, stdout="", stderr="fatal: invalid reference: HEAD\n"
        )

    monkeypatch.setattr(subprocess, "run", _git_fache)
    with pytest.raises(EspaceProjetIndisponible) as refus:
        with espace_de_travail(projet, tache_id="t-1"):
            pass  # pragma: no cover - le montage a déjà échoué
    assert refus.value.motif == "worktree-refuse"
    assert "invalid reference" in str(refus.value)


def test_un_temporaire_dans_la_racine_est_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projet = _projet_copie(tmp_path)
    # Un TMPDIR posé *dans* le projet ferait de la copie une écriture en place.
    monkeypatch.setattr(tempfile, "tempdir", projet.racine)
    with pytest.raises(EspaceProjetIndisponible) as refus:
        with espace_de_travail(projet, tache_id="t1"):
            pass  # pragma: no cover - le montage a déjà échoué
    assert refus.value.motif == "espace-dans-la-racine"


def test_une_racine_disparue_est_refusee_avec_son_motif(tmp_path: Path) -> None:
    projet = _projet(tmp_path / "projets" / "envole")
    with pytest.raises(RacineRefusee) as refus:
        with espace_de_travail(projet, tache_id="t1"):
            pass  # pragma: no cover - la validation a déjà échoué
    assert refus.value.motif == "dossier-absent"


# --------------------------------------------------------------------------- #
# Nommage de la branche
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("tache_id", "attendu"),
    [
        ("t1", "maestro/t1"),
        ("T-3 Analyse", "maestro/T-3-Analyse"),
        ("../evasion", "maestro/evasion"),
        ("a..b", "maestro/a-b"),
        ("refs.lock", "maestro/refs-lock"),
        ("", "maestro/tache"),
        ("@{}~^:?*[", "maestro/tache"),
    ],
)
def test_le_nom_de_branche_est_assaini(tache_id: str, attendu: str) -> None:
    assert branche_de_tache(tache_id) == attendu


# --------------------------------------------------------------------------- #
# Câblage : du `projet_id` de la tâche à l'espace de travail
# --------------------------------------------------------------------------- #


class _FournisseurEcrivain(ModelProvider):
    """Fournisseur factice outillé : note l'espace reçu et y écrit un fichier."""

    name = "ecrivain"

    def __init__(self) -> None:
        self.espaces: list[Path] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None,
        on_arbitrage=None, credit_arbitrage=None,
        on_courrier=None,
        plafond_tours=None, projet=None,
    ):
        self.espaces.append(Path(workspace))
        (Path(workspace) / "RAPPORT.md").write_text("fait", encoding="utf-8")
        return "Fait."


def test_le_runtime_derive_l_espace_du_projet(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    fournisseur = _FournisseurEcrivain()
    runtime = AgentRuntime(fournisseur, DEVELOPER_PROFILE)

    issue = asyncio.run(runtime.execute("Corriger le calcul", projet=projet, tache_id="t1"))

    (espace,) = fournisseur.espaces
    assert not espace.exists()  # démonté en sortie
    # L'agent a bien vu le projet, et son livrable ne contient que ce qu'il a écrit.
    assert [f.chemin for f in issue.fichiers] == ["RAPPORT.md"]


def test_l_executeur_relit_le_projet_de_la_tache(tmp_path: Path) -> None:
    from maestro.orchestrator.schema import Task

    depot = ProjetStore(tmp_path / "depot")
    racine = tmp_path / "projets" / "depensio"
    racine.mkdir(parents=True)
    _arborescence(racine)
    projet = depot.creer("Démo", racine)

    def _tache(id_: str, projet_id: str | None) -> Task:
        return Task(
            id=id_,
            titre="Corriger le calcul",
            description="d",
            competences_requises=(),
            format_sortie="markdown",
            projet_id=projet_id,
        )

    executeur = LocalExecutor(_FournisseurEcrivain(), projets=depot)

    relu = executeur._projet(_tache("t1", projet.id))
    assert relu is not None and relu.racine == projet.racine
    assert executeur._projet(_tache("t2", None)) is None
    # Un `projet_id` orphelin ramène au `mkdtemp()` d'avant plutôt qu'à un échec.
    assert executeur._projet(_tache("t3", "prj-00000000")) is None
    # Sans dépôt câblé, aucune dérivation : le câblage est ce qui l'active.
    assert LocalExecutor(_FournisseurEcrivain())._projet(_tache("t4", projet.id)) is None
