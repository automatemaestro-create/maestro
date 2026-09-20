"""Les espaces jetables ne laissent ni coquille ni orphelin (#992, défauts S6 et S13 de #568).

Trois questions, dans l'ordre du ticket :

① **la coquille** — un espace où un agent a fait un `git init` disparaît **en
   entier**. Éprouvé sur un `.git` réel, parce que c'est la lecture seule des
   objets de Git qui fait le défaut, et qu'un faux `.git` fabriqué à la main ne la
   porte pas. Le contre-exemple est joué d'abord : sans lui, un test qui passe ne
   prouverait rien sous POSIX, où le droit d'écriture d'un fichier n'empêche pas sa
   suppression ;
② **l'orphelin** — le ramassage retire ce qu'un process mort a laissé, conserve ce
   qu'une tâche vivante occupe, et ne touche **jamais** à un worktree ;
③ **l'adresse** (S13) — une valeur `TMPDIR` venue de MSYS est écartée sous
   Windows, et seulement sous Windows.

Aucun backend, aucun réseau : des dossiers jetables, `git` quand il est là.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from maestro.fichiers import retirer_arbre
from maestro.sandbox import isolated_workspace
from maestro.sandbox.ramassage import (
    PREFIXES_HISTORIQUES,
    SEUIL_ORPHELIN_H,
    VALEUR_MSYS_HISTORIQUE,
    VARIABLE_RAMASSAGE,
    VARIABLE_SEUIL,
    Ramassage,
    espaces,
    est_orphelin,
    marquer,
    pid_dans,
    pid_vivant,
    porte_un_worktree,
    racine_des_espaces,
    racines_connues,
    ramasser,
)

GIT = shutil.which("git")

besoin_de_git = pytest.mark.skipif(GIT is None, reason="git introuvable")
sous_windows = pytest.mark.skipif(
    sys.platform != "win32", reason="le refus d'`os.remove` et l'ambiguïté de `/tmp` sont Windows"
)


# =================================================================================================
# ① La coquille : un `.git` réel disparaît en entier
# =================================================================================================


def _depot(racine: Path) -> None:
    """Un vrai dépôt Git avec un vrai objet — donc des fichiers en lecture seule.

    Identité posée par `-c` : la suite n'exige aucune configuration globale, et le
    conteneur du filet CI n'en a pas (docs/10 §8).
    """
    racine.mkdir(parents=True, exist_ok=True)
    (racine / "livrable.txt").write_text("le travail de l'agent", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=racine, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=racine, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "objets"],
        cwd=racine,
        check=True,
        capture_output=True,
    )


def _objets_en_lecture_seule(racine: Path) -> list[Path]:
    """Les fichiers de `.git/objects/` privés du droit d'écriture — le cœur du défaut.

    Le **mode** du fichier, jamais `os.access` : celui-ci répond « puis-*je* écrire »,
    et dans le conteneur du filet CI la suite tourne en root, qui peut toujours. La
    question porte sur le fichier, pas sur qui la pose.
    """
    dossier = racine / ".git" / "objects"
    return [
        chemin
        for chemin in dossier.rglob("*")
        if chemin.is_file() and not chemin.stat().st_mode & stat.S_IWUSR
    ]


@besoin_de_git
def test_un_depot_reel_porte_bien_des_fichiers_en_lecture_seule(tmp_path: Path) -> None:
    """Le contre-exemple du harnais : sans objet en lecture seule, les deux tests suivants
    ne prouveraient rien. Git en écrit ; si un jour il cesse, c'est ici qu'on l'apprend."""
    racine = tmp_path / "depot"
    _depot(racine)
    assert _objets_en_lecture_seule(racine), "aucun objet en lecture seule : le défaut est ailleurs"


@besoin_de_git
@sous_windows
def test_rmtree_nu_laisse_la_coquille_git(tmp_path: Path) -> None:
    """Le fautif, joué d'abord (#568 : « le seul enfant survivant est `.git` »).

    Sous POSIX la suppression ne regarde que le dossier parent, donc `rmtree` nu
    réussit et il n'y a rien à démontrer — le test s'y abstient plutôt que de
    prétendre le contraire."""

    racine = tmp_path / "espace"
    _depot(racine)
    shutil.rmtree(racine, ignore_errors=True)
    assert racine.exists(), "rmtree nu aurait tout emporté : le remède n'aurait plus d'objet"
    assert (racine / ".git").exists(), "la coquille est bien `.git`"


@besoin_de_git
def test_retirer_arbre_emporte_un_depot_git_entier(tmp_path: Path) -> None:
    """Le remède (#707, factorisé par #992) : plus rien, `.git` compris."""
    racine = tmp_path / "espace"
    _depot(racine)
    assert retirer_arbre(racine) is True
    assert not racine.exists()


@besoin_de_git
def test_un_espace_ou_lagent_a_fait_git_init_ne_laisse_rien(tmp_path: Path) -> None:
    """Le critère du ticket, sur le vrai chemin : `isolated_workspace` nettoie tout.

    C'est l'étape de reproduction n°1 du ticket, en un test : une tâche sans projet
    dont l'agent initialise un dépôt, puis la fin normale de la tâche."""
    with isolated_workspace(prefix="maestro-dev-") as ws:
        chemin = ws.path
        _depot(chemin)
        assert (chemin / ".git").is_dir()
    assert not chemin.exists(), "la fin normale d'une tâche ne laisse rien, `.git` compris"


def test_retirer_arbre_sur_un_chemin_absent_ne_leve_pas(tmp_path: Path) -> None:
    assert retirer_arbre(tmp_path / "jamais-cree") is True


# =================================================================================================
# ② L'orphelin : qui occupe l'espace ?
# =================================================================================================


def _espace(racine: Path, nom: str, *, age_h: float = 0.0) -> Path:
    """Un espace de travail factice, vieilli à la demande (lui et son contenu)."""
    chemin = racine / nom
    chemin.mkdir(parents=True)
    (chemin / "brouillon.txt").write_text("wip", encoding="utf-8")
    if age_h:
        _vieillir(chemin, age_h)
    return chemin


def _vieillir(chemin: Path, age_h: float) -> None:
    """Recule la date de l'espace **et de ses enfants directs** — ceux que le verdict lit.

    Toujours en dernier : créer quoi que ce soit dans un dossier remet sa propre
    date à maintenant, et un espace « vieux » qu'on vient de garnir ne l'est plus.
    """
    quand = time.time() - age_h * 3600
    for cible in (*chemin.iterdir(), chemin):
        os.utime(cible, (quand, quand))


def test_le_nom_porte_le_pid_du_process_qui_ouvre_lespace() -> None:
    """Le marqueur est dans le nom, jamais dans un fichier — sinon il serait un livrable."""
    marque = marquer("maestro-dev-")
    assert marque == f"maestro-dev-pid{os.getpid()}-"
    assert pid_dans(f"{marque}a1b2c3d4") == os.getpid()
    assert pid_dans("maestro-dev-a1b2c3d4") is None, "l'aléa seul ne se lit pas comme un pid"


def test_un_espace_ouvert_pour_de_vrai_porte_le_pid_et_ne_contient_rien() -> None:
    """Le marqueur voyage bien jusqu'au vrai espace, sans rien y déposer."""
    with isolated_workspace(prefix="maestro-qa-") as ws:
        assert ws.path.name.startswith("maestro-qa-")
        assert pid_dans(ws.path.name) == os.getpid()
        assert not any(ws.path.iterdir()), "créé vide : aucun témoin posé dedans"
        assert ws.produced_files() == ()


def test_un_pid_vivant_et_un_pid_mort_se_distinguent() -> None:
    """Le contre-exemple de la sonde : sans lui, un `pid_vivant` toujours vrai passerait."""
    assert pid_vivant(os.getpid()) is True
    mort = subprocess.Popen([sys.executable, "-c", "pass"])
    mort.wait()
    assert pid_vivant(mort.pid) is False
    assert pid_vivant(0) is False


def test_lespace_dune_tache_vivante_est_conserve(tmp_path: Path) -> None:
    vivant = _espace(tmp_path, f"maestro-dev-pid{os.getpid()}-aaaaaaaa", age_h=48)
    passage = ramasser(racines=[tmp_path], environnement={})
    assert passage.conserves == (vivant,)
    assert passage.retires == ()
    assert vivant.exists(), "même vieux : un pid vivant l'emporte sur l'horloge"


def test_lespace_dun_process_mort_est_retire_sans_attendre(tmp_path: Path) -> None:
    mort = subprocess.Popen([sys.executable, "-c", "pass"])
    mort.wait()
    orphelin = _espace(tmp_path, f"maestro-bdd-pid{mort.pid}-bbbbbbbb")
    passage = ramasser(racines=[tmp_path], environnement={})
    assert passage.retires == (orphelin,)
    assert not orphelin.exists(), "le marqueur dispense d'attendre que l'espace refroidisse"


def test_un_espace_sans_marqueur_attend_le_seuil(tmp_path: Path) -> None:
    """Les 76 résidus d'avant ce ticket : pas de pid à interroger, donc l'âge tranche."""
    jeune = _espace(tmp_path, "maestro-dev-cccccccc", age_h=1)
    vieux = _espace(tmp_path, "maestro-qa-dddddddd", age_h=SEUIL_ORPHELIN_H + 1)
    passage = ramasser(racines=[tmp_path], environnement={})
    assert passage.retires == (vieux,)
    assert jeune.exists(), "un espace d'une version antérieure peut être encore au travail"


def test_le_seuil_du_poste_lemporte(tmp_path: Path) -> None:
    recent = _espace(tmp_path, "maestro-dev-eeeeeeee", age_h=0.5)
    passage = ramasser(racines=[tmp_path], environnement={VARIABLE_SEUIL: "0.1"})
    assert passage.retires == (recent,)


def test_un_seuil_illisible_retombe_sur_le_defaut(tmp_path: Path) -> None:
    recent = _espace(tmp_path, "maestro-dev-ffffffff", age_h=1)
    assert ramasser(racines=[tmp_path], environnement={VARIABLE_SEUIL: "bientôt"}).retires == ()
    assert recent.exists()


def test_un_worktree_nest_jamais_ramasse(tmp_path: Path) -> None:
    """La règle de `worktree.sh gc` (docs/10 §9.2) : on ne ramasse pas du travail non sauvegardé.

    Un worktree porte un `.git` **fichier** ; c'est la seule chose qui le distingue,
    sans appeler Git, du `git init` d'un agent dans son espace jetable."""
    mort = subprocess.Popen([sys.executable, "-c", "pass"])
    mort.wait()
    parent = _espace(tmp_path, f"maestro-dev-pid{mort.pid}-11111111", age_h=99)
    tache = parent / "t-42"
    tache.mkdir()
    (tache / ".git").write_text("gitdir: /ailleurs/.git/worktrees/t-42\n", encoding="utf-8")
    assert porte_un_worktree(parent) is True
    passage = ramasser(racines=[tmp_path], environnement={})
    assert passage.retires == ()
    assert parent.exists()


def test_un_git_init_dagent_nest_pas_un_worktree(tmp_path: Path) -> None:
    """Le contre-exemple du refus précédent : sans lui, `porte_un_worktree` toujours vrai
    ferait un ramassage qui ne ramasse jamais rien."""
    espace = _espace(tmp_path, "maestro-dev-22222222")
    (espace / ".git").mkdir()
    _vieillir(espace, 99)
    assert porte_un_worktree(espace) is False
    assert ramasser(racines=[tmp_path], environnement={}).retires == (espace,)


def test_les_autres_dossiers_temporaires_ne_sont_pas_candidats(tmp_path: Path) -> None:
    """L'atelier d'un hôte, un aperçu de source, un masque de conteneur : pas notre affaire."""
    for nom in ("maestro-hote-run-1-aaaaaaaa", "maestro-apercu-bbbbbbbb", "projet-de-quelquun"):
        _espace(tmp_path, nom, age_h=99)
    assert list(espaces([tmp_path])) == []
    assert ramasser(racines=[tmp_path], environnement={}).retires == ()


def test_un_espace_marque_dun_autre_prefixe_reste_candidat(tmp_path: Path) -> None:
    """Le marqueur suffit : un rôle ajouté demain n'a pas à entrer dans une liste."""
    mort = subprocess.Popen([sys.executable, "-c", "pass"])
    mort.wait()
    espace = _espace(tmp_path, f"maestro-redacteur-pid{mort.pid}-33333333")
    assert ramasser(racines=[tmp_path], environnement={}).retires == (espace,)


def test_la_liste_historique_couvre_tous_les_roles_outilles() -> None:
    """La seule liste écrite du module, confrontée aux profils réels.

    Sans cette garde, un rôle dont le préfixe manque laisserait ses espaces d'avant
    le marqueur sur le disque pour toujours."""
    from maestro.agents import TOOLED_PROFILES

    attendus = {profile.workspace_prefix for profile in TOOLED_PROFILES}
    assert attendus <= set(PREFIXES_HISTORIQUES), (
        f"préfixes de rôle absents de PREFIXES_HISTORIQUES : {attendus - set(PREFIXES_HISTORIQUES)}"
    )


def test_le_ramassage_seteint_par_le_poste(tmp_path: Path) -> None:
    espace = _espace(tmp_path, "maestro-dev-44444444", age_h=99)
    assert ramasser(racines=[tmp_path], environnement={VARIABLE_RAMASSAGE: "0"}) == Ramassage()
    assert espace.exists()


def test_le_ramassage_ne_leve_pas_sur_une_racine_absente(tmp_path: Path) -> None:
    assert ramasser(racines=[tmp_path / "nulle-part"], environnement={}) == Ramassage()


def test_un_passage_sans_rien_a_faire_est_muet(tmp_path: Path) -> None:
    """« L'absence est muette » : rien à retirer, rien à dire dans le journal d'un hôte."""
    _espace(tmp_path, f"maestro-dev-pid{os.getpid()}-55555555")
    passage = ramasser(racines=[tmp_path], environnement={})
    assert bool(passage) is False
    assert passage.resume() == ""


def test_un_passage_qui_retire_le_dit(tmp_path: Path) -> None:
    _espace(tmp_path, "maestro-dev-66666666", age_h=99)
    passage = ramasser(racines=[tmp_path], environnement={})
    assert bool(passage) is True
    assert "1 espace(s) orphelin(s) retiré(s)" in passage.resume()


def test_est_orphelin_se_pose_espace_par_espace(tmp_path: Path) -> None:
    """Le verdict est public : il se pose sur un espace, sans balayer une racine."""
    vivant = _espace(tmp_path, f"maestro-dev-pid{os.getpid()}-77777777", age_h=99)
    vieux = _espace(tmp_path, "maestro-dev-88888888", age_h=99)
    assert est_orphelin(vivant) is False
    assert est_orphelin(vieux) is True


# =================================================================================================
# ③ L'adresse : S13, une valeur `TMPDIR` de MSYS ne vaut rien pour Python sous Windows
# =================================================================================================


@sous_windows
def test_une_valeur_msys_est_ecartee_sous_windows(tmp_path: Path) -> None:
    """S13, vérifiée le 2026-09-20 : Git Bash pose `TMPDIR=/tmp` et monte `/tmp` sur `%TEMP%`,
    quand Python résout `/tmp` sur le lecteur courant (`C:\\tmp`, où dormaient les 76 résidus).
    Écarter la valeur fait retomber les deux côtés au même endroit."""
    environnement = {"TMPDIR": "/tmp", "TEMP": str(tmp_path)}
    assert racine_des_espaces(environnement) == tmp_path


@pytest.mark.skipif(sys.platform == "win32", reason="hors Windows, `/tmp` est un chemin normal")
def test_une_valeur_posix_est_gardee_hors_windows(tmp_path: Path) -> None:
    """Le pendant : rien ne change là où `/tmp` veut dire `/tmp`."""
    assert racine_des_espaces({"TMPDIR": str(tmp_path)}) == tmp_path


def test_une_racine_inexistante_est_passee(tmp_path: Path) -> None:
    """Comme `tempfile` : une variable qui pointe dans le vide n'est pas une panne."""
    environnement = {"TMPDIR": str(tmp_path / "absent"), "TEMP": str(tmp_path)}
    assert racine_des_espaces(environnement) == tmp_path


def test_sans_aucune_variable_on_retombe_sur_tempfile() -> None:

    assert racine_des_espaces({}) == Path(tempfile.gettempdir())


def test_lespace_nait_bien_sous_la_racine_retenue() -> None:
    """Le lien entre la décision et le disque : l'espace est créé là où on l'a dit."""
    racine = racine_des_espaces()
    with isolated_workspace() as ws:
        assert ws.path.parent == racine


def test_les_racines_connues_ne_rendent_que_des_dossiers_sans_doublon() -> None:
    racines = racines_connues()
    assert racines, "au moins le répertoire temporaire du système"
    assert all(chemin.is_dir() for chemin in racines)
    vues = [str(chemin.resolve()).casefold() for chemin in racines]
    assert len(vues) == len(set(vues))


@sous_windows
def test_les_racines_connues_cherchent_la_resolution_windows_meme_sans_tmpdir() -> None:
    """Le process qui ramasse n'est pas celui qui a fui.

    Un hôte lancé depuis PowerShell n'hérite d'aucun `TMPDIR` ; il doit quand même
    trouver ce qu'un hôte lancé depuis Git Bash a laissé sous `<lecteur>:\\tmp` —
    76 espaces sur le poste de référence le 2026-09-20. `<lecteur>:\\tmp` absent,
    il n'y a rien à balayer et rien n'est rendu : c'est la même règle, vue de
    l'autre côté."""
    lecteur = os.path.splitdrive(str(Path(tempfile.gettempdir()).resolve()))[0]
    attendu = Path(f"{lecteur}{VALEUR_MSYS_HISTORIQUE}")
    for env in ({"TEMP": tempfile.gettempdir()}, {"TMPDIR": "/tmp", "TEMP": tempfile.gettempdir()}):
        rendues = {chemin.resolve() for chemin in racines_connues(env)}
        assert (attendu.resolve() in rendues) is attendu.is_dir()
