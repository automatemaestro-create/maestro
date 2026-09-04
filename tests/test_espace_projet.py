"""Tests de l'espace de travail dérivé du projet (ticket #224, EF-36, D2 — puis #839).

Couvre les critères de #224 qui tiennent encore : **worktree Git** sur une branche
dédiée quand le projet est versionné (branche jamais supprimée, hors de la
racine), une tâche sans `projet_id` gardant le `mkdtemp()` d'avant — et le régime
que #839 leur a substitué pour le projet **non versionné** : l'espace de travail
**est la racine** (rien n'est copié, rien n'est retiré), le recensement passe par
le périmètre, et ce que la copie garantissait par absence est garanti par refus
(`FrontiereEcriture`). La copie du périmètre, elle, n'existe plus — et ses tests
d'inclusion (`inclus`) avec elle : l'inclusion ne restreint plus aucun espace
dérivé (voir `maestro.sandbox.en_place`).

Les tests des lots de #703 sont différés à #707 ; ceux qui sont ici gardent la
seule logique **critique** du lot — la frontière d'écriture (EF-38), la
sérialisation d'un projet non versionné et la ligne de journal qui manquait.

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
from maestro.engine.executor import (
    STATUT_ECRITURE_EN_PLACE,
    STATUT_ECRITURE_SANS_OBJET,
    STATUT_PROJET_INTROUVABLE,
    LocalExecutor,
)
from maestro.orchestrator.schema import Task
from maestro.projets.modele import Perimetre, Projet, Vcs
from maestro.projets.racine import RacineRefusee, detecter_vcs
from maestro.projets.store import ProjetStore
from maestro.providers.base import ModelProvider
from maestro.sandbox import (
    EspaceProjetIndisponible,
    FrontiereEcriture,
    branche_de_tache,
    espace_de_travail,
    frontiere_de,
)
from maestro.telemetry import RunJournal

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
# Projet non versionné : la racine elle-même, en place (#839)
# --------------------------------------------------------------------------- #


def test_projet_non_versionne_travaille_dans_sa_racine(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="t1") as ws:
        assert ws.path.resolve() == racine.resolve()  # l'espace **est** la racine
        (ws.path / "NOUVEAU.md").write_text("livrable", encoding="utf-8")
        # Visible pendant que l'agent l'écrit, pas seulement à la fin.
        assert (racine / "NOUVEAU.md").read_text(encoding="utf-8") == "livrable"
        (ws.path / "src" / "app.py").write_text("print('réécrit')\n", encoding="utf-8")
        produits = [f.chemin for f in ws.produced_files()]
    # Rien n'est perdu à la fermeture de l'espace — le défaut mesuré par #568.
    assert (racine / "NOUVEAU.md").read_text(encoding="utf-8") == "livrable"
    assert (racine / "src" / "app.py").read_text(encoding="utf-8") == "print('réécrit')\n"
    # Le livrable, c'est ce que l'agent a écrit — pas le projet qu'on lui a prêté.
    assert sorted(produits) == ["NOUVEAU.md", "src/app.py"]


def test_le_recensement_ne_rend_que_ce_que_l_agent_a_ecrit_dans_le_perimetre(
    tmp_path: Path,
) -> None:
    projet = _projet_copie(tmp_path)
    with espace_de_travail(projet, tache_id="t1") as ws:
        # Ce qu'un `Bash` pourrait toucher hors périmètre ne ressort pas au rapport :
        # `.env`, `node_modules` et `secrets/` sont exclus à toute profondeur.
        (ws.path / ".env").write_text("CLE=changée\n", encoding="utf-8")
        (ws.path / "node_modules" / "ajout.js").write_text("x", encoding="utf-8")
        (ws.path / "apps" / "web" / "node_modules" / "b.js").write_text("y", encoding="utf-8")
        (ws.path / "services" / "api" / "secrets" / "neuf.txt").write_text("s", encoding="utf-8")
        (ws.path / "livrable.md").write_text("ok", encoding="utf-8")
        produits = [f.chemin for f in ws.produced_files()]
    assert produits == ["livrable.md"]


def test_la_plomberie_git_n_entre_pas_au_recensement(tmp_path: Path) -> None:
    racine = tmp_path / "projets" / "faux-depot"
    (racine / ".git").mkdir(parents=True)
    (racine / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (racine / "note.md").write_text("bonjour", encoding="utf-8")
    # Projet déclaré non versionné bien qu'un `.git` traîne : c'est `vcs` qui décide
    # du régime, et l'exclusion doit tenir quand même.
    with espace_de_travail(_projet(racine), tache_id="t1") as ws:
        (ws.path / ".git" / "index").write_bytes(b"\x00")
        (ws.path / "note.md").write_text("modifiée", encoding="utf-8")
        produits = [f.chemin for f in ws.produced_files()]
    assert produits == ["note.md"]


def test_le_recensement_ne_suit_aucun_lien_symbolique(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    dehors = tmp_path / "dehors"
    dehors.mkdir()
    (dehors / "cle.txt").write_text("secret du poste", encoding="utf-8")
    try:
        (Path(projet.racine) / "src" / "evasion").symlink_to(dehors, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - dépend des droits Windows
        pytest.skip("liens symboliques indisponibles sur ce poste")
    with espace_de_travail(projet, tache_id="t1") as ws:
        # Modifié derrière le lien : `rglob` le verrait, le parcours par le périmètre non.
        (dehors / "cle.txt").write_text("modifié", encoding="utf-8")
        produits = [f.chemin for f in ws.produced_files()]
    assert produits == []


def test_rien_n_est_retire_de_la_racine_meme_sur_exception(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    racine = Path(projet.racine)
    with pytest.raises(RuntimeError):
        with espace_de_travail(projet, tache_id="t1") as ws:
            (ws.path / "en-cours.md").write_text("à moitié", encoding="utf-8")
            raise RuntimeError("boum")
    # Le `rmtree` du `finally` est ce qui a effacé `squelette-p1` (#568) : plus jamais.
    assert (racine / "README.md").is_file()
    assert (racine / "en-cours.md").read_text(encoding="utf-8") == "à moitié"


# --------------------------------------------------------------------------- #
# La frontière d'écriture du régime en place (#839, EF-38)
# --------------------------------------------------------------------------- #


def _frontiere(tmp_path: Path) -> tuple[Projet, FrontiereEcriture]:
    projet = _projet_copie(tmp_path)
    return projet, FrontiereEcriture.pour(projet.racine, projet.perimetre)


def test_la_frontiere_laisse_passer_une_ecriture_ordinaire(tmp_path: Path) -> None:
    projet, frontiere = _frontiere(tmp_path)
    racine = Path(projet.racine)
    assert frontiere.refus("Write", {"file_path": "src/nouveau.py", "content": ""}) is None
    assert frontiere.refus("Edit", {"file_path": str(racine / "README.md")}) is None
    assert frontiere.refus("Read", {"file_path": "src/app.py"}) is None
    # Un outil sans chemin, ou un appel sans l'argument attendu, n'a rien à confronter.
    assert frontiere.refus("Bash", {"command": "cat .env"}) is None
    assert frontiere.refus("Write", {}) is None
    assert frontiere.refus("Write", "pas un objet") is None


@pytest.mark.parametrize("chemin", ["../dehors.txt", "src/../../evasion.py"])
def test_la_frontiere_refuse_l_ecriture_hors_de_la_racine(tmp_path: Path, chemin: str) -> None:
    _, frontiere = _frontiere(tmp_path)
    motif = frontiere.refus("Write", {"file_path": chemin})
    assert motif is not None and "sort de la racine" in motif


def test_un_chemin_absolu_d_ailleurs_est_refuse_en_ecriture_pas_en_lecture(
    tmp_path: Path,
) -> None:
    _, frontiere = _frontiere(tmp_path)
    ailleurs = tmp_path / "ailleurs" / "note.txt"
    motif = frontiere.refus("Write", {"file_path": str(ailleurs)})
    assert motif is not None and "sort de la racine" in motif
    # Lire hors de la racine n'est pas le sujet du périmètre — c'était déjà vrai de la copie.
    assert frontiere.refus("Read", {"file_path": str(ailleurs)}) is None


@pytest.mark.parametrize(
    "chemin",
    [
        ".env",
        "services/api/.env",
        "secrets/cle.pem",
        "secrets/neuf.txt",  # n'existe pas encore : l'exclusion vaut avant l'écriture
        "node_modules/paquet.js",
        "apps/web/node_modules/lib/profond.js",  # sous un dossier exclu, à toute profondeur
        ".git/config",
    ],
)
def test_la_frontiere_refuse_les_chemins_exclus_en_lecture_comme_en_ecriture(
    tmp_path: Path, chemin: str
) -> None:
    _, frontiere = _frontiere(tmp_path)
    for outil in ("Write", "Edit", "Read"):
        motif = frontiere.refus(outil, {"file_path": chemin})
        assert motif is not None and "exclu du périmètre" in motif, (outil, chemin)


def test_la_frontiere_ne_suit_aucun_lien_symbolique(tmp_path: Path) -> None:
    projet, frontiere = _frontiere(tmp_path)
    racine = Path(projet.racine)
    dehors = tmp_path / "dehors"
    dehors.mkdir()
    try:
        (racine / "evasion").symlink_to(dehors, target_is_directory=True)
        (racine / "raccourci").symlink_to(racine / "src", target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - dépend des droits Windows
        pytest.skip("liens symboliques indisponibles sur ce poste")
    # Vers l'extérieur : résolu avant comparaison, donc hors racine.
    motif = frontiere.refus("Write", {"file_path": "evasion/cle.txt"})
    assert motif is not None and "sort de la racine" in motif
    # Vers l'intérieur : refusé quand même — aucun lien n'est suivi, où qu'il mène.
    motif = frontiere.refus("Write", {"file_path": "raccourci/app.py"})
    assert motif is not None and "lien symbolique" in motif


def test_la_frontiere_n_est_armee_que_sur_la_racine(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    racine = Path(projet.racine)
    assert frontiere_de(racine, projet) is not None
    assert frontiere_de(racine / ".." / racine.name, projet) is not None  # même racine, résolue
    # Un espace qui n'est pas la racine n'en reçoit aucune : l'armer y refuserait tout.
    assert frontiere_de(tmp_path / "ailleurs", projet) is None
    assert frontiere_de(racine, None) is None
    versionne = _projet(racine, vcs=Vcs(type="git", branche_base="main"))
    assert frontiere_de(tmp_path / "worktree", versionne) is None


def test_le_hook_du_fournisseur_refuse_sur_la_frontiere_meme_sans_politique(
    tmp_path: Path,
) -> None:
    from maestro.providers import claude as claude_mod

    _, frontiere = _frontiere(tmp_path)
    refus: list[tuple[str, str]] = []
    hook = claude_mod._hook_permissions(
        None, lambda outil, motif: refus.append((outil, motif)), frontiere=frontiere
    )

    def _joue(outil: str, chemin: str) -> object:
        return asyncio.run(
            hook({"tool_name": outil, "tool_input": {"file_path": chemin}}, "tu-1", None)
        )

    sortie = _joue("Write", ".env")
    assert sortie["hookSpecificOutput"]["permissionDecision"] == "deny"  # type: ignore[index]
    assert refus and refus[0][0] == "Write" and "exclu du périmètre" in refus[0][1]
    # Une écriture ordinaire passe, et sans politique il n'y a rien d'autre à juger.
    assert _joue("Write", "src/ok.py") == {}
    assert _joue("Bash", "") == {}


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
    racine = Path(_projet_copie(tmp_path).racine)
    projet = _projet(racine, vcs=Vcs(type="git", branche_base="main"))
    # Un TMPDIR posé *dans* le projet ferait du worktree une écriture en place —
    # contrôle joué avant tout appel à Git, donc sans dépôt réel.
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
        on_arbitrage=None, on_blocage=None, credit_arbitrage=None,
        on_courrier=None,
        plafond_tours=None, projet=None,
    ):
        self.espaces.append(Path(workspace))
        (Path(workspace) / "RAPPORT.md").write_text("fait", encoding="utf-8")
        return "Fait."


def test_le_runtime_derive_l_espace_du_projet(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    racine = Path(projet.racine)
    fournisseur = _FournisseurEcrivain()
    runtime = AgentRuntime(fournisseur, DEVELOPER_PROFILE)

    issue = asyncio.run(runtime.execute("Corriger le calcul", projet=projet, tache_id="t1"))

    (espace,) = fournisseur.espaces
    assert espace.resolve() == racine.resolve()  # non versionné : en place (#839)
    assert (racine / "RAPPORT.md").read_text(encoding="utf-8") == "fait"  # et rien n'est retiré
    # L'agent a bien vu le projet, et son livrable ne contient que ce qu'il a écrit.
    assert [f.chemin for f in issue.fichiers] == ["RAPPORT.md"]


def test_l_executeur_relit_le_projet_de_la_tache(tmp_path: Path) -> None:
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


# --------------------------------------------------------------------------- #
# L'exécuteur : le journal dit ce qui est arrivé au projet, une tâche à la fois (#839)
# --------------------------------------------------------------------------- #


class _FournisseurMuet(ModelProvider):
    """Fournisseur factice qui réussit **sans rien écrire** : le cas de la racine vide."""

    name = "muet"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **canaux):
        return "Rien à écrire."


class _FournisseurQuiTombe(ModelProvider):
    """Fournisseur factice qui écrit puis **échoue** : ce qui est écrit doit rester."""

    name = "tombe"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **canaux):
        (Path(workspace) / "AVANT-LA-CHUTE.md").write_text("écrit puis tombé", encoding="utf-8")
        raise RuntimeError("aléa fournisseur")


class _FournisseurBarriere(ModelProvider):
    """Compte les exécutions **en vol** : c'est la mesure de la sérialisation.

    Chaque exécution signale son arrivée puis attend que `attendus` soient là,
    l'attente étant **bornée** : sur un projet non versionné, l'autre ne viendra
    pas tant que celle-ci tient l'atelier, et l'attente expire — c'est le verdict
    attendu, pas un aléa. Sans projet, les deux arrivent et la barrière se lève.
    Une barrière et non un `sleep` (règle de #292) : c'est elle qui rend la
    simultanéité **certaine** quand rien ne l'interdit.
    """

    name = "barriere"

    def __init__(self, attendus: int) -> None:
        self._attendus = attendus
        self._tous_la = asyncio.Event()
        self.arrivees = 0
        self.en_vol = 0
        self.pic = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **canaux):
        self.en_vol += 1
        self.pic = max(self.pic, self.en_vol)
        self.arrivees += 1
        if self.arrivees >= self._attendus:
            self._tous_la.set()
        try:
            await asyncio.wait_for(self._tous_la.wait(), timeout=0.5)
        except TimeoutError:
            pass  # sérialisé : l'autre attend l'atelier, elle ne viendra pas
        self.en_vol -= 1
        (Path(workspace) / "RAPPORT.md").write_text("fait", encoding="utf-8")
        return "Fait."


def _tache_routee(id_: str, projet_id: str | None) -> Task:
    """Une tâche que le routeur confie au développeur, seul rôle outillé monté ici."""
    return Task(
        id=id_,
        titre=f"Tâche {id_}",
        description="d",
        competences_requises=("backend",),
        format_sortie="markdown",
        projet_id=projet_id,
    )


def _executeur(fournisseur: ModelProvider, depot: ProjetStore | None) -> LocalExecutor:
    runtimes = {DEVELOPER_PROFILE.nom: AgentRuntime(fournisseur, DEVELOPER_PROFILE)}
    return LocalExecutor(fournisseur, runtimes=runtimes, projets=depot)


def _depot_et_projet(tmp_path: Path) -> tuple[ProjetStore, Projet]:
    """Un dépôt de projets et un projet **non versionné** qui y est déclaré."""
    depot = ProjetStore(tmp_path / "depot")
    racine = tmp_path / "projets" / "depensio"
    racine.mkdir(parents=True)
    _arborescence(racine)
    return depot, depot.creer("Démo", racine)


def _etape_projet(journal: RunJournal, tache_id: str):
    """L'unique ligne « Projet — … » de la tâche, ou l'assertion qui dit ce qu'il y a."""
    etapes = [r for r in journal.records if r.etape == f"{tache_id}:fusion"]
    assert len(etapes) == 1, [r.etape for r in journal.records]
    return etapes[0]


def _joue(fournisseur: ModelProvider, depot: ProjetStore | None, projet_id: str | None):
    journal = RunJournal(run_id="run-839")
    resultat = asyncio.run(
        _executeur(fournisseur, depot).execute(_tache_routee("t1", projet_id), [], journal)
    )
    return resultat, journal


def test_l_ecriture_en_place_est_consignee_avec_ce_qui_a_ete_ecrit(tmp_path: Path) -> None:
    depot, projet = _depot_et_projet(tmp_path)
    resultat, journal = _joue(_FournisseurEcrivain(), depot, projet.id)
    assert resultat.ok
    assert (Path(projet.racine) / "RAPPORT.md").read_text(encoding="utf-8") == "fait"
    etape = _etape_projet(journal, "t1")
    assert etape.statut == STATUT_ECRITURE_EN_PLACE
    assert projet.racine in etape.entree
    assert "1 fichier(s)" in etape.sortie and "RAPPORT.md" in etape.sortie


def test_une_racine_restee_vide_est_dite_et_non_tue(tmp_path: Path) -> None:
    """Le défaut de #568 : un run vert sur une racine vide. La ligne existe désormais."""
    depot, projet = _depot_et_projet(tmp_path)
    resultat, journal = _joue(_FournisseurMuet(), depot, projet.id)
    assert resultat.ok
    etape = _etape_projet(journal, "t1")
    assert etape.statut == STATUT_ECRITURE_SANS_OBJET
    assert "rien n'a été écrit" in etape.sortie


def test_une_tache_en_echec_laisse_la_racine_telle_quelle_et_le_dit(tmp_path: Path) -> None:
    depot, projet = _depot_et_projet(tmp_path)
    resultat, journal = _joue(_FournisseurQuiTombe(), depot, projet.id)
    assert not resultat.ok
    assert (Path(projet.racine) / "AVANT-LA-CHUTE.md").is_file()  # rien n'est retiré
    etape = _etape_projet(journal, "t1")
    assert etape.statut == STATUT_ECRITURE_EN_PLACE
    assert "tâche en échec" in etape.sortie


def test_un_projet_introuvable_est_nomme_au_journal(tmp_path: Path) -> None:
    depot = ProjetStore(tmp_path / "depot")
    resultat, journal = _joue(_FournisseurEcrivain(), depot, "prj-00000000")
    assert resultat.ok  # la tâche a travaillé dans un espace jetable (règle de `_projet`)
    etape = _etape_projet(journal, "t1")
    assert etape.statut == STATUT_PROJET_INTROUVABLE
    assert "prj-00000000" in etape.sortie


def test_sans_projet_aucune_ligne_projet_n_est_consignee(tmp_path: Path) -> None:
    """La seule abstention muette : il n'y a pas de racine dont parler."""
    _, journal = _joue(_FournisseurEcrivain(), None, None)
    assert not any(r.etape.endswith(":fusion") for r in journal.records)


def test_deux_taches_du_meme_projet_non_versionne_ne_travaillent_jamais_ensemble(
    tmp_path: Path,
) -> None:
    """Le régime de concurrence retenu : **sérialisation par projet** (`_atelier_projet`)."""
    depot, projet = _depot_et_projet(tmp_path)

    async def _deux(executeur: LocalExecutor, projet_id: str | None):
        journal = RunJournal(run_id="run-839")
        return await asyncio.gather(
            executeur.execute(_tache_routee("t1", projet_id), [], journal),
            executeur.execute(_tache_routee("t2", projet_id), [], journal),
        )

    # Le motif d'abord : sans projet, le même fournisseur voit bien deux exécutions
    # ensemble — sans quoi un pic de 1 ne prouverait rien.
    temoin = _FournisseurBarriere(attendus=2)
    asyncio.run(_deux(_executeur(temoin, depot), None))
    assert temoin.pic == 2

    # Puis la règle : dans la racine d'un projet non versionné, une seule à la fois.
    serialise = _FournisseurBarriere(attendus=2)
    resultats = asyncio.run(_deux(_executeur(serialise, depot), projet.id))
    assert all(r.ok for r in resultats)
    assert serialise.pic == 1
