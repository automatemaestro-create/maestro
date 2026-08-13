"""Tests de l'application du travail dans le projet de l'utilisateur (#227, EF-37).

Couvre les trois critères du lot : **ce qui changerait** (`diff_du_travail`, sur
les deux chemins de la décision D2 — branche Git ou copie), le **contrôle de
frontière** qui précède toute écriture (`verifier_perimetre`, EF-38) et
l'**écriture** elle-même (`appliquer` : fusion ou recopie), plus le branchement
sur la validation humaine existante (`appliquer_sous_validation`).

Aucun réseau, aucun modèle, aucun humain : les tests qui ont besoin d'un vrai
dépôt en montent un jetable et sont sautés là où `git` manque ; le validateur est
une fonction du test, ce qui rend l'accord et le refus jouables sans UI. Les deux
invariants qui coûtent le plus cher s'ils lâchent ont chacun leur test nommé :
un refus **n'écrit rien** (ni partiellement, ni « presque »), et une suppression
n'est **jamais** appliquée à un projet non versionné (ENF-13).

La dernière section vient du lot final tests + doc (#220) : trois façons de perdre
le travail de l'utilisateur que le reste du fichier ne mettait pas à l'épreuve —
un diff qui écrirait au passage, une UI qui tombe pendant qu'on lui demande
l'accord, et un refus qui emporterait la branche de tâche avec lui.
"""

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from maestro.controltower.validation import appliquer_sous_validation
from maestro.engine.guardrails import DemandeValidation
from maestro.projets.application import (
    APPLICATION_APPROUVEE,
    APPLICATION_REFUSEE,
    APPLICATION_SANS_OBJET,
    NATURE_AJOUT,
    NATURE_MODIFICATION,
    NATURE_SUPPRESSION,
    ApplicationRefusee,
    DiffProjet,
    Modification,
    ResultatApplication,
    appliquer,
    diff_du_travail,
    verifier_perimetre,
)
from maestro.projets.modele import Perimetre, Projet, Vcs
from maestro.projets.racine import detecter_vcs
from maestro.sandbox import branche_de_tache

GIT = shutil.which("git")

besoin_de_git = pytest.mark.skipif(GIT is None, reason="git introuvable")


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `Path.home()` sous `tmp_path`, comme dans les tests du socle (#221, #224).

    Indispensable sous Windows : le `tmp_path` de pytest vit sous `AppData`, que
    `valider_racine` interdit à juste titre — sans cette isolation, toutes les
    racines de projet de ce fichier seraient refusées.
    """
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


@pytest.fixture(autouse=True)
def _sans_identite_git_ambiante(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Coupe l'identité Git du poste, pour que ces tests répondent la même chose partout (#333).

    Le dépôt jetable n'a jamais posé d'identité — à dessein : c'est la situation que
    `maestro.projets.application` dit prendre en charge, un projet d'utilisateur qui n'a aucune
    raison d'en avoir une valable pour un agent. Mais le `~/.gitconfig` du poste en fournissait une
    en douce, si bien que la fusion réussissait sur toute machine de développement **sans jamais
    passer par le `-c` du code**. Le trou ne s'est vu que sur un runner nu, où git répond
    « Committer identity unknown » — et il ne s'est vu qu'une fois, l'image du pipeline GitLab
    n'ayant pas git du tout.

    `GIT_CONFIG_GLOBAL` sur un fichier inexistant (git ≥ 2.32) vaut un `~/.gitconfig` vide. La
    configuration SYSTÈME est laissée en place : sous Windows elle porte `core.autocrlf`, dont ces
    tests dépendent bien plus que d'une identité, et l'identité ne s'y trouve pas.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig-absent"))


# --------------------------------------------------------------------------- #
# Fabriques
# --------------------------------------------------------------------------- #


def _projet(racine: Path, *, vcs: Vcs | None = None) -> Projet:
    """Un projet pointant sur `racine` (le dépôt n'est pas sollicité par la fabrique)."""
    return Projet(
        id="prj-0000dead",
        nom="Démo",
        racine=racine.as_posix(),
        vcs=vcs,
        perimetre=Perimetre(),
    )


def _projet_copie(tmp_path: Path) -> tuple[Projet, Path]:
    """Un projet **non versionné** et l'espace de travail (la copie) de sa tâche."""
    racine = tmp_path / "projets" / "depensio"
    (racine / "src").mkdir(parents=True)
    (racine / "src" / "app.py").write_text("un\ndeux\n", encoding="utf-8")
    (racine / "README.md").write_text("# Démo\n", encoding="utf-8")
    espace = tmp_path / "espaces" / "t1"
    (espace / "src").mkdir(parents=True)
    shutil.copy2(racine / "README.md", espace / "README.md")  # inchangé
    (espace / "src" / "app.py").write_text("un\ndeux\ntrois\n", encoding="utf-8")  # modifié
    (espace / "src" / "neuf.py").write_text("neuf\n", encoding="utf-8")  # ajouté
    return _projet(racine), espace


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
    """Un commit avec une identité posée par `-c` (le dépôt jetable n'en a pas)."""
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


def _projet_git(tmp_path: Path, *, branche: str = "main") -> tuple[Projet, Path]:
    """Un projet **versionné** : dépôt jetable, un commit, VCS détecté comme en vrai."""
    racine = tmp_path / "projets" / "depensio-git"
    (racine / "src").mkdir(parents=True)
    (racine / "src" / "app.py").write_text("un\ndeux\n", encoding="utf-8")
    (racine / "README.md").write_text("# Démo\n", encoding="utf-8")
    _git(racine, "init", "--quiet")
    _git(racine, "symbolic-ref", "HEAD", f"refs/heads/{branche}")
    _commiter(racine, "socle")
    return _projet(racine, vcs=detecter_vcs(racine)), racine


def _worktree(racine: Path, tmp_path: Path, tache_id: str = "t1") -> tuple[Path, str]:
    """Monte le worktree de la tâche, comme le fait l'espace de travail dérivé (#224)."""
    branche = branche_de_tache(tache_id)
    espace = tmp_path / "espaces" / tache_id
    espace.parent.mkdir(parents=True, exist_ok=True)
    _git(racine, "worktree", "add", "--quiet", "-b", branche, str(espace))
    return espace, branche


# --------------------------------------------------------------------------- #
# Projet non versionné : le diff est la comparaison de la copie avec la racine
# --------------------------------------------------------------------------- #


def test_le_diff_d_une_copie_distingue_ajout_modification_et_inchange(tmp_path: Path) -> None:
    projet, espace = _projet_copie(tmp_path)
    diff = diff_du_travail(projet, espace=espace)
    par_chemin = {m.chemin: m for m in diff.modifications}
    # `README.md` est identique des deux côtés : il ne fait pas partie du diff.
    assert set(par_chemin) == {"src/app.py", "src/neuf.py"}
    assert par_chemin["src/app.py"].nature == NATURE_MODIFICATION
    assert (par_chemin["src/app.py"].ajouts, par_chemin["src/app.py"].suppressions) == (1, 0)
    assert par_chemin["src/neuf.py"].nature == NATURE_AJOUT
    assert par_chemin["src/neuf.py"].ajouts == 1
    assert not diff.versionne and not diff.vide
    assert (diff.fichiers, diff.ajouts, diff.suppressions) == (2, 2, 0)


def test_une_copie_ne_rend_jamais_de_suppression(tmp_path: Path) -> None:
    """Invariant 4 du module : un fichier absent de la copie est ambigu, jamais effacé."""
    projet, espace = _projet_copie(tmp_path)
    (Path(projet.racine) / "src" / "ancien.py").write_text("vieux\n", encoding="utf-8")
    diff = diff_du_travail(projet, espace=espace)
    assert all(m.nature != NATURE_SUPPRESSION for m in diff.modifications)
    assert "src/ancien.py" not in {m.chemin for m in diff.modifications}


def test_un_fichier_binaire_est_signale_sans_etre_compte(tmp_path: Path) -> None:
    projet, espace = _projet_copie(tmp_path)
    (espace / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    diff = diff_du_travail(projet, espace=espace)
    binaire = next(m for m in diff.modifications if m.chemin == "logo.png")
    assert binaire.binaire and (binaire.ajouts, binaire.suppressions) == (0, 0)


def test_les_liens_symboliques_de_la_copie_sont_ignores(tmp_path: Path) -> None:
    """Même règle qu'à la copie (#224) : un lien vers `~/.ssh` serait la fuite qu'on ferme."""
    projet, espace = _projet_copie(tmp_path)
    dehors = tmp_path / "dehors.txt"
    dehors.write_text("secret\n", encoding="utf-8")
    try:
        (espace / "lien.txt").symlink_to(dehors)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows sans droit de lien
        pytest.skip("liens symboliques indisponibles sur ce poste")
    diff = diff_du_travail(projet, espace=espace)
    assert "lien.txt" not in {m.chemin for m in diff.modifications}


def test_sans_espace_un_projet_non_versionne_refuse_avec_son_motif(tmp_path: Path) -> None:
    projet, _ = _projet_copie(tmp_path)
    with pytest.raises(ApplicationRefusee) as refus:
        diff_du_travail(projet)
    assert refus.value.motif == "espace-introuvable"


def test_un_espace_disparu_refuse_aussi(tmp_path: Path) -> None:
    projet, _ = _projet_copie(tmp_path)
    with pytest.raises(ApplicationRefusee) as refus:
        diff_du_travail(projet, espace=tmp_path / "espaces" / "envole")
    assert refus.value.motif == "espace-introuvable"


# --------------------------------------------------------------------------- #
# Projet versionné : le diff se lit dans Git
# --------------------------------------------------------------------------- #


@besoin_de_git
def test_un_projet_versionne_exige_la_branche_de_tache(tmp_path: Path) -> None:
    """Le nom de la branche est *passé*, jamais recalculé ici (parti pris 1)."""
    projet, _ = _projet_git(tmp_path)
    with pytest.raises(ApplicationRefusee) as refus:
        diff_du_travail(projet)
    assert refus.value.motif == "branche-inconnue"


@besoin_de_git
def test_une_branche_de_tache_inexistante_refuse_avec_son_motif(tmp_path: Path) -> None:
    projet, _ = _projet_git(tmp_path)
    with pytest.raises(ApplicationRefusee) as refus:
        diff_du_travail(projet, branche="maestro/jamais-creee")
    assert refus.value.motif == "branche-introuvable"


@besoin_de_git
def test_un_depot_sans_branche_de_travail_declaree_refuse(tmp_path: Path) -> None:
    """HEAD détachée à la déclaration : il n'y a rien vers quoi fusionner."""
    _, racine = _projet_git(tmp_path)
    projet = _projet(racine, vcs=Vcs(type="git", branche_base=""))
    with pytest.raises(ApplicationRefusee) as refus:
        diff_du_travail(projet, branche="maestro/t1")
    assert refus.value.motif == "base-introuvable"


@besoin_de_git
def test_le_diff_d_un_worktree_monte_porte_le_travail_non_commite(tmp_path: Path) -> None:
    """Un agent écrit des fichiers ; il ne fait pas forcément `git add` (cf. `_diff_worktree`)."""
    projet, racine = _projet_git(tmp_path)
    espace, branche = _worktree(racine, tmp_path)
    (espace / "src" / "app.py").write_text("un\ndeux\ntrois\n", encoding="utf-8")  # suivi, modifié
    (espace / "note.md").write_text("bonjour\n", encoding="utf-8")  # non suivi
    diff = diff_du_travail(projet, branche=branche, espace=espace)
    par_chemin = {m.chemin: m for m in diff.modifications}
    assert set(par_chemin) == {"src/app.py", "note.md"}
    assert par_chemin["note.md"].nature == NATURE_AJOUT
    assert par_chemin["src/app.py"].nature == NATURE_MODIFICATION
    assert diff.versionne and (diff.branche, diff.base) == (branche, "main")


@besoin_de_git
def test_le_diff_se_lit_encore_sur_la_branche_une_fois_le_worktree_retire(
    tmp_path: Path,
) -> None:
    """Pour un projet versionné, l'application peut être demandée bien après la tâche."""
    projet, racine = _projet_git(tmp_path)
    espace, branche = _worktree(racine, tmp_path)
    (espace / "src" / "app.py").write_text("un\ndeux\ntrois\n", encoding="utf-8")
    (espace / "README.md").unlink()
    _commiter(espace, "travail de la tâche")
    _git(racine, "worktree", "remove", "--force", str(espace))
    diff = diff_du_travail(projet, branche=branche)
    par_chemin = {m.chemin: m.nature for m in diff.modifications}
    assert par_chemin == {"src/app.py": NATURE_MODIFICATION, "README.md": NATURE_SUPPRESSION}


@besoin_de_git
def test_le_travail_pris_par_la_base_entre_temps_ne_ressort_pas_en_suppression(
    tmp_path: Path,
) -> None:
    """`base...branche` (trois points) : on fusionne les changements de la branche."""
    projet, racine = _projet_git(tmp_path)
    espace, branche = _worktree(racine, tmp_path)
    (espace / "src" / "app.py").write_text("un\ndeux\ntrois\n", encoding="utf-8")
    _commiter(espace, "travail de la tâche")
    _git(racine, "worktree", "remove", "--force", str(espace))
    # Un collègue avance `main` de son côté, sur un autre fichier.
    (racine / "collegue.md").write_text("moi aussi\n", encoding="utf-8")
    _commiter(racine, "travail du collègue")
    diff = diff_du_travail(projet, branche=branche)
    assert {m.chemin for m in diff.modifications} == {"src/app.py"}


# --------------------------------------------------------------------------- #
# Le contrôle de frontière (EF-38)
# --------------------------------------------------------------------------- #


def test_le_perimetre_rend_les_chemins_absolus_sous_la_racine(tmp_path: Path) -> None:
    projet, _ = _projet_copie(tmp_path)
    diff = DiffProjet(modifications=(Modification(chemin="src/app.py"),))
    (chemin,) = verifier_perimetre(projet, diff)
    assert chemin == (Path(projet.racine) / "src" / "app.py").resolve()


def test_un_chemin_qui_sort_de_la_racine_est_refuse_avec_son_motif(tmp_path: Path) -> None:
    projet, _ = _projet_copie(tmp_path)
    diff = DiffProjet(modifications=(Modification(chemin="../dehors.txt"),))
    with pytest.raises(ApplicationRefusee) as refus:
        verifier_perimetre(projet, diff)
    assert refus.value.motif == "hors-racine"


def test_le_perimetre_est_verifie_en_entier_avant_la_premiere_ecriture(tmp_path: Path) -> None:
    """« Refusée avec son motif, pas appliquée partiellement » : le critère du lot."""
    projet, espace = _projet_copie(tmp_path)
    diff = DiffProjet(
        modifications=(
            Modification(chemin="src/neuf.py", nature=NATURE_AJOUT),
            Modification(chemin="../dehors.txt", nature=NATURE_AJOUT),
        )
    )
    with pytest.raises(ApplicationRefusee) as refus:
        appliquer(projet, diff, espace=espace)
    assert refus.value.motif == "hors-racine"
    assert not (Path(projet.racine) / "src" / "neuf.py").exists()  # rien n'a été écrit
    assert not (tmp_path / "projets" / "dehors.txt").exists()


# --------------------------------------------------------------------------- #
# Application — projet non versionné : la recopie
# --------------------------------------------------------------------------- #


def test_appliquer_recopie_les_fichiers_du_diff_dans_la_racine(tmp_path: Path) -> None:
    projet, espace = _projet_copie(tmp_path)
    racine = Path(projet.racine)
    (espace / "docs").mkdir()
    (espace / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")
    diff = diff_du_travail(projet, espace=espace)
    appliques = appliquer(projet, diff, espace=espace)
    assert set(appliques) == {"src/app.py", "src/neuf.py", "docs/guide.md"}
    assert (racine / "src" / "app.py").read_text(encoding="utf-8") == "un\ndeux\ntrois\n"
    assert (racine / "src" / "neuf.py").read_text(encoding="utf-8") == "neuf\n"
    # Le dossier absent de la racine est créé au passage.
    assert (racine / "docs" / "guide.md").read_text(encoding="utf-8") == "guide\n"


def test_aucune_suppression_n_est_appliquee_a_un_projet_non_versionne(tmp_path: Path) -> None:
    """ENF-13 : effacer sur une ambiguïté, dans un projet sans historique, jamais."""
    projet, espace = _projet_copie(tmp_path)
    racine = Path(projet.racine)
    diff = DiffProjet(
        modifications=(Modification(chemin="README.md", nature=NATURE_SUPPRESSION),)
    )
    assert appliquer(projet, diff, espace=espace) == ()
    assert (racine / "README.md").exists()


def test_un_diff_vide_n_ecrit_rien_et_ne_derange_personne(tmp_path: Path) -> None:
    projet, espace = _projet_copie(tmp_path)
    assert appliquer(projet, DiffProjet(), espace=espace) == ()


def test_une_ecriture_impossible_refuse_avec_son_motif(tmp_path: Path) -> None:
    """La source a disparu entre le diff et l'accord : refus motivé, pas de trace."""
    projet, espace = _projet_copie(tmp_path)
    diff = DiffProjet(
        modifications=(Modification(chemin="src/envole.py", nature=NATURE_AJOUT),)
    )
    with pytest.raises(ApplicationRefusee) as refus:
        appliquer(projet, diff, espace=espace)
    assert refus.value.motif == "ecriture-refusee"


# --------------------------------------------------------------------------- #
# Application — projet versionné : la fusion
# --------------------------------------------------------------------------- #


@besoin_de_git
def test_appliquer_fusionne_la_branche_de_tache_vers_la_branche_de_travail(
    tmp_path: Path,
) -> None:
    projet, racine = _projet_git(tmp_path)
    espace, branche = _worktree(racine, tmp_path)
    (espace / "src" / "app.py").write_text("un\ndeux\ntrois\n", encoding="utf-8")
    (espace / "note.md").write_text("bonjour\n", encoding="utf-8")
    diff = diff_du_travail(projet, branche=branche, espace=espace)
    appliques = appliquer(projet, diff, espace=espace)
    assert set(appliques) == {"src/app.py", "note.md"}
    # Le travail non commité du worktree a été commité sur sa branche, puis fusionné.
    assert (racine / "src" / "app.py").read_text(encoding="utf-8") == "un\ndeux\ntrois\n"
    assert (racine / "note.md").read_text(encoding="utf-8") == "bonjour\n"
    # `--no-ff` : c'est un commit de fusion, donc `git revert` le défait (D2, option B).
    assert len(_git(racine, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3
    # La branche de tâche n'est jamais supprimée : le travail reste consultable.
    assert branche in _git(racine, "branch", "--list", branche)


@besoin_de_git
def test_la_fusion_refuse_si_la_racine_est_sur_une_autre_branche(tmp_path: Path) -> None:
    projet, racine = _projet_git(tmp_path)
    espace, branche = _worktree(racine, tmp_path)
    (espace / "note.md").write_text("bonjour\n", encoding="utf-8")
    diff = diff_du_travail(projet, branche=branche, espace=espace)
    _git(racine, "checkout", "--quiet", "-b", "autre-chose")
    with pytest.raises(ApplicationRefusee) as refus:
        appliquer(projet, diff, espace=espace)
    assert refus.value.motif == "racine-occupee"
    assert not (racine / "note.md").exists()


@besoin_de_git
def test_la_fusion_refuse_si_la_racine_a_des_changements_en_cours(tmp_path: Path) -> None:
    """Fusionner sous les pieds de quelqu'un est exactement ce que EF-37 empêche."""
    projet, racine = _projet_git(tmp_path)
    espace, branche = _worktree(racine, tmp_path)
    (espace / "note.md").write_text("bonjour\n", encoding="utf-8")
    diff = diff_du_travail(projet, branche=branche, espace=espace)
    (racine / "README.md").write_text("# En cours d'édition\n", encoding="utf-8")
    with pytest.raises(ApplicationRefusee) as refus:
        appliquer(projet, diff, espace=espace)
    assert refus.value.motif == "racine-occupee"
    assert not (racine / "note.md").exists()


@besoin_de_git
def test_une_fusion_en_conflit_est_refusee_et_la_branche_conserve_le_travail(
    tmp_path: Path,
) -> None:
    projet, racine = _projet_git(tmp_path)
    espace, branche = _worktree(racine, tmp_path)
    (espace / "src" / "app.py").write_text("version de l'agent\n", encoding="utf-8")
    diff = diff_du_travail(projet, branche=branche, espace=espace)
    # La branche de travail change la même ligne de son côté : la fusion ne peut pas trancher.
    (racine / "src" / "app.py").write_text("version de l'humain\n", encoding="utf-8")
    _commiter(racine, "édition concurrente")
    with pytest.raises(ApplicationRefusee) as refus:
        appliquer(projet, diff, espace=espace)
    assert refus.value.motif == "fusion-refusee"
    # `merge --abort` a été joué : la racine est propre et sur sa branche.
    assert _git(racine, "status", "--porcelain").strip() == ""
    assert _git(racine, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert branche in _git(racine, "branch", "--list", branche)


@besoin_de_git
def test_un_worktree_deja_commite_est_fusionne_sans_commit_de_rattrapage(
    tmp_path: Path,
) -> None:
    projet, racine = _projet_git(tmp_path)
    espace, branche = _worktree(racine, tmp_path)
    (espace / "note.md").write_text("bonjour\n", encoding="utf-8")
    _commiter(espace, "travail de la tâche")
    diff = diff_du_travail(projet, branche=branche, espace=espace)
    avant = _git(espace, "rev-parse", "HEAD").strip()
    appliquer(projet, diff, espace=espace)
    assert _git(espace, "rev-parse", "HEAD").strip() == avant  # rien à rattraper
    assert (racine / "note.md").read_text(encoding="utf-8") == "bonjour\n"


# --------------------------------------------------------------------------- #
# Git absent ou fâché : les chemins d'échec, jouables partout
# --------------------------------------------------------------------------- #


def test_git_indisponible_devient_un_refus_motive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projet, racine = (
        _projet(tmp_path / "projets" / "faux", vcs=Vcs(type="git", branche_base="main")),
        tmp_path / "projets" / "faux",
    )
    racine.mkdir(parents=True)

    def _pas_de_git(*args: object, **kwargs: object) -> object:
        raise OSError("git introuvable")

    monkeypatch.setattr(subprocess, "run", _pas_de_git)
    with pytest.raises(ApplicationRefusee) as refus:
        diff_du_travail(projet, branche="maestro/t1")
    assert refus.value.motif == "git-indisponible"


@besoin_de_git
def test_un_verdict_non_nul_de_git_devient_un_refus_motive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un code de retour est une réponse, que l'appelant traduit — pas une exception."""
    projet, racine = _projet_git(tmp_path)
    _, branche = _worktree(racine, tmp_path)
    vrai_run = subprocess.run

    def _echoue_sur_le_diff(commande: list[str], **kwargs: object) -> object:
        if "diff" in commande:
            return subprocess.CompletedProcess(commande, 128, "", "fatal: objet illisible")
        return vrai_run(commande, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", _echoue_sur_le_diff)
    with pytest.raises(ApplicationRefusee) as refus:
        diff_du_travail(projet, branche=branche)
    assert refus.value.motif == "git-refuse"
    assert "objet illisible" in str(refus.value)


# --------------------------------------------------------------------------- #
# Le diff en tant que pièce jointe : résumé et aller-retour JSON
# --------------------------------------------------------------------------- #


def test_le_resume_d_un_diff_vide_le_dit() -> None:
    assert DiffProjet().resume() == "Aucune modification à appliquer."


def test_le_resume_porte_les_totaux_la_fusion_et_le_detail_par_fichier() -> None:
    diff = DiffProjet(
        modifications=(
            Modification(chemin="src/app.py", nature=NATURE_MODIFICATION, ajouts=3,
                         suppressions=1),
            Modification(chemin="logo.png", nature=NATURE_AJOUT, binaire=True),
        ),
        branche="maestro/t1",
        base="main",
    )
    lignes = diff.resume().splitlines()
    assert lignes[0] == "2 fichier(s), +3 / −1 — fusion de maestro/t1 vers main"
    assert lignes[1] == "  ~ src/app.py (+3 −1)"
    assert lignes[2] == "  + logo.png (binaire)"


def test_le_resume_borne_la_liste_et_compte_le_reste() -> None:
    """C'est la phrase du journal qu'on borne — l'UI, elle, affiche tout."""
    diff = DiffProjet(
        modifications=tuple(Modification(chemin=f"f{i}.txt") for i in range(45))
    )
    lignes = diff.resume().splitlines()
    assert lignes[-1] == "  … et 5 autre(s) fichier(s)"
    assert len(lignes) == 1 + 40 + 1


def test_le_diff_fait_l_aller_retour_json_sans_perdre_ses_natures() -> None:
    diff = DiffProjet(
        modifications=(
            Modification(chemin="a.py", nature=NATURE_AJOUT, ajouts=2),
            Modification(chemin="b.py", nature=NATURE_SUPPRESSION, suppressions=7),
            Modification(chemin="c.bin", binaire=True),
        ),
        branche="maestro/t1",
        base="main",
    )
    assert DiffProjet.from_dict(diff.to_dict()) == diff


def test_les_totaux_d_un_diff_relu_sont_derives_jamais_relus() -> None:
    """Un en-tête qui contredit sa liste ne doit pas pouvoir entrer par le JSON."""
    brut = DiffProjet(
        modifications=(Modification(chemin="a.py", nature=NATURE_AJOUT, ajouts=2),)
    ).to_dict()
    brut["fichiers"], brut["ajouts"], brut["suppressions"] = 99, 99, 99
    relu = DiffProjet.from_dict(brut)
    assert (relu.fichiers, relu.ajouts, relu.suppressions) == (1, 2, 0)


def test_un_diff_relu_ignore_les_modifications_qui_ne_sont_pas_des_objets() -> None:
    relu = DiffProjet.from_dict({"modifications": ["pas un objet", {"chemin": "a.py"}]})
    assert [m.chemin for m in relu.modifications] == ["a.py"]


def test_le_resultat_d_une_application_se_serialise() -> None:
    resultat = ResultatApplication(
        statut=APPLICATION_APPROUVEE,
        diff=DiffProjet(modifications=(Modification(chemin="a.py"),)),
        detail="approuvée",
        appliques=("a.py",),
    )
    assert resultat.approuvee
    brut = resultat.to_dict()
    assert brut["statut"] == APPLICATION_APPROUVEE
    assert brut["appliques"] == ["a.py"]
    assert brut["diff"]["fichiers"] == 1


# --------------------------------------------------------------------------- #
# Le branchement sur la validation humaine existante (EF-08)
# --------------------------------------------------------------------------- #


def test_un_diff_vide_ne_derange_aucun_humain(tmp_path: Path) -> None:
    projet, espace = _projet_copie(tmp_path)
    shutil.rmtree(espace / "src")
    (espace / "src").mkdir()
    (espace / "src" / "app.py").write_text("un\ndeux\n", encoding="utf-8")  # identique
    demandes: list[DemandeValidation] = []

    def _valide(demande: DemandeValidation) -> bool:
        demandes.append(demande)
        return True

    resultat = asyncio.run(
        appliquer_sous_validation(
            projet, tache_id="t1", validateur=_valide, espace=espace
        )
    )
    assert resultat.statut == APPLICATION_SANS_OBJET
    assert demandes == []  # personne n'a été sollicité


def test_un_accord_humain_declenche_l_ecriture(tmp_path: Path) -> None:
    projet, espace = _projet_copie(tmp_path)
    demandes: list[DemandeValidation] = []

    def _valide(demande: DemandeValidation) -> bool:
        demandes.append(demande)
        return True

    resultat = asyncio.run(
        appliquer_sous_validation(
            projet,
            tache_id="t1",
            validateur=_valide,
            espace=espace,
            agent="dev",
            role="Dev",
        )
    )
    assert resultat.statut == APPLICATION_APPROUVEE and resultat.approuvee
    assert set(resultat.appliques) == {"src/app.py", "src/neuf.py"}
    assert (Path(projet.racine) / "src" / "neuf.py").exists()
    # Le diff voyage en pièce jointe : sans lui, la question n'est pas tranchable.
    (demande,) = demandes
    assert demande.diff is not None and demande.diff.fichiers == 2
    assert "2 fichier(s)" in demande.description


def test_un_refus_humain_n_ecrit_rien_et_laisse_le_travail_consultable(
    tmp_path: Path,
) -> None:
    projet, espace = _projet_copie(tmp_path)
    resultat = asyncio.run(
        appliquer_sous_validation(
            projet, tache_id="t1", validateur=lambda _: False, espace=espace
        )
    )
    assert resultat.statut == APPLICATION_REFUSEE and not resultat.approuvee
    assert resultat.appliques == ()
    assert not (Path(projet.racine) / "src" / "neuf.py").exists()
    assert (espace / "src" / "neuf.py").exists()  # la copie reste où elle est


def test_sans_validateur_le_fail_safe_des_garde_fous_refuse(tmp_path: Path) -> None:
    """Le fail-safe est celui de #9, réutilisé tel quel plutôt que réécrit."""
    projet, espace = _projet_copie(tmp_path)
    resultat = asyncio.run(
        appliquer_sous_validation(projet, tache_id="t1", validateur=None, espace=espace)
    )
    assert resultat.statut == APPLICATION_REFUSEE
    assert not (Path(projet.racine) / "src" / "neuf.py").exists()


def test_un_chemin_hors_perimetre_leve_avant_meme_la_demande(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On ne fait pas approuver ce qu'on refusera d'écrire (étape 2 du déroulé).

    Le diff est forgé : `diff_du_travail` ne sait pas produire de chemin hors
    racine, et c'est bien pour un diff venu d'ailleurs — un événement rejoué, un
    fichier de projet édité à la main — que le contrôle de frontière existe.
    """
    projet, espace = _projet_copie(tmp_path)
    sollicite = False

    def _valide(_: DemandeValidation) -> bool:
        nonlocal sollicite
        sollicite = True
        return True

    monkeypatch.setattr(
        "maestro.controltower.validation.diff_du_travail",
        lambda *_args, **_kwargs: DiffProjet(
            modifications=(Modification(chemin="../dehors.txt", nature=NATURE_AJOUT),)
        ),
    )
    with pytest.raises(ApplicationRefusee) as refus:
        asyncio.run(
            appliquer_sous_validation(
                projet, tache_id="t1", validateur=_valide, espace=espace
            )
        )
    assert refus.value.motif == "hors-racine"
    assert not sollicite


# --------------------------------------------------------------------------- #
# Trois façons de perdre le travail de l'utilisateur (lot tests + doc, #220)
# --------------------------------------------------------------------------- #


def test_le_diff_n_ecrit_rien_dans_la_racine(tmp_path: Path) -> None:
    """« Ce que le travail changerait » se demande **avant** d'avoir décidé."""
    projet, espace = _projet_copie(tmp_path)
    racine = Path(projet.racine)

    diff = diff_du_travail(projet, espace=espace)

    assert not diff.vide  # il y avait bien quelque chose à écrire…
    assert (racine / "src" / "app.py").read_text(encoding="utf-8") == "un\ndeux\n"
    assert not (racine / "src" / "neuf.py").exists()  # …et rien ne l'a été


@besoin_de_git
def test_le_diff_d_un_projet_versionne_laisse_la_racine_ou_elle_etait(tmp_path: Path) -> None:
    """Le diff passe par Git : il pourrait déplacer la racine sans rien y écrire."""
    projet, racine = _projet_git(tmp_path)
    espace, branche = _worktree(racine, tmp_path)
    (espace / "note.md").write_text("bonjour\n", encoding="utf-8")

    diff = diff_du_travail(projet, branche=branche, espace=espace)

    assert not diff.vide
    assert _git(racine, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _git(racine, "status", "--porcelain") == ""
    assert not (racine / "note.md").exists()


def test_un_validateur_qui_leve_ne_fait_rien_ecrire(tmp_path: Path) -> None:
    """L'UI tombe pendant qu'on demande l'accord : c'est un refus, pas un blanc-seing."""
    projet, espace = _projet_copie(tmp_path)

    def _tombe(_: DemandeValidation) -> bool:
        raise RuntimeError("l'onglet a été fermé pendant la demande")

    resultat = asyncio.run(
        appliquer_sous_validation(
            projet, tache_id="t1", validateur=_tombe, espace=espace
        )
    )

    assert resultat.statut == APPLICATION_REFUSEE
    assert not (Path(projet.racine) / "src" / "neuf.py").exists()


@besoin_de_git
def test_un_refus_ne_supprime_jamais_la_branche_de_tache(tmp_path: Path) -> None:
    """Refuser, c'est remettre à plus tard : le travail doit rester resoumettable."""
    projet, racine = _projet_git(tmp_path)
    espace, branche = _worktree(racine, tmp_path)
    (espace / "note.md").write_text("bonjour\n", encoding="utf-8")

    resultat = asyncio.run(
        appliquer_sous_validation(
            projet,
            tache_id="t1",
            validateur=lambda _: False,
            branche=branche,
            espace=espace,
        )
    )

    assert resultat.statut == APPLICATION_REFUSEE
    assert not (racine / "note.md").exists()
    assert branche in _git(racine, "branch", "--list", branche)
    assert (espace / "note.md").exists()  # le travail est resté dans son espace
