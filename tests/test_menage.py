"""Ce que Maestro a posé sur le poste, et la règle des trois racines confrontée au dépôt (#1458).

Ce que ce fichier tient (`maestro.menage` en porte la démonstration) :

① **l'inventaire** — chaque racine déclarée et le hors-racine, avec taille, propriétaire et
  sort ; `--check` (le défaut) n'écrit rien ;
② **la purge** — sur un « oui » seulement, rien que ce qu'aucun ramasseur ne connaît, jamais un
  dossier vivant, jamais `~/Maestro/` ni un projet déclaré dans une copie du dépôt, et le sort
  rejugé au moment de retirer ;
③ **la règle confrontée au dépôt** — `CLAUDE.md` la porte avec les noms que le code déclare,
  la doc du chantier sa démonstration, et aucun script ni prompt ne compose de chemin sous le
  dossier personnel hors des racines, hors des lectures que l'inventaire nomme avec leur raison.
  Le motif est d'abord **prouvé sur un échantillon fautif**.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TextIO, cast

import pytest
from balayage import fichiers_du_depot

from maestro import menage
from maestro.controltower.donnees import donnees_du_banc
from maestro.emplacements import (
    NOM_ETAT,
    NOM_JETABLE,
    NOM_PROJETS,
    TEMOIN_PID,
    racine_ateliers,
    racine_etat,
    racine_projets,
)
from maestro.menage import (
    A_RETIRER,
    A_SON_PROPRIETAIRE,
    ANCIEN_ATELIER_RELECTURE,
    CODE_FAIT,
    CODE_NON_CONFIRME,
    CODE_USAGE,
    CONSERVE,
    DECLARE,
    HORS_RACINE,
    JAMAIS,
    MODULE,
    NOM_PROFIL_NAVIGATEUR,
    RACINE_ETAT,
    RACINE_JETABLE,
    RAMASSABLE,
    Entree,
    Protections,
    copies_du_depot,
    inventorier,
    protections,
)
from maestro.projets.modele import Projet
from maestro.projets.store import ProjetStore
from maestro.sandbox.ramassage import FAMILLES_TIERCES, FAMILLES_TIERCES_JETABLES
from maestro.scenarios.projets import ANCIEN_NOM_ATELIER

RACINE_DEPOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")

besoin_de_git = pytest.mark.skipif(GIT is None, reason="git introuvable")


# =================================================================================================
# Le poste du test : un dossier personnel et un répertoire temporaire à lui
# =================================================================================================


@pytest.fixture
def maison(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    dossier = tmp_path_factory.mktemp("maison")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: dossier))
    return dossier


@pytest.fixture
def temporaire(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("temp-du-poste")


def _vieillir(chemin: Path, age_h: float = 48) -> None:
    """Recule la date d'un élément et de ses enfants directs — ceux que le verdict lit."""
    quand = time.time() - age_h * 3600
    cibles = [*chemin.iterdir(), chemin] if chemin.is_dir() else [chemin]
    for cible in cibles:
        os.utime(cible, (quand, quand))


def _dossier(chemin: Path, *, contenu: str = "brouillon", age_h: float = 48) -> Path:
    chemin.mkdir(parents=True, exist_ok=True)
    (chemin / "notes.txt").write_text(contenu, encoding="utf-8")
    if age_h:
        _vieillir(chemin, age_h)
    return chemin


def _pid_mort() -> int:
    mort = subprocess.Popen([sys.executable, "-c", "pass"])
    mort.wait()
    return mort.pid


def _gardes(*projets: Path, worktrees: tuple[str, ...] = ()) -> Protections:
    return Protections(projets=(racine_projets(), *projets), worktrees=frozenset(worktrees))


def _jouer(
    argv: list[str],
    temporaire: Path,
    *,
    gardes: Protections | None = None,
    reponse: str | _ReponseTardive | _RacineRevenue = "",
    copies: list[Path] | None = None,
) -> tuple[int, str, str]:
    """La commande sur le poste du test — avec `copies`, ses déclarations relues à chaque fois."""
    sortie, erreur = io.StringIO(), io.StringIO()
    entree = io.StringIO(reponse) if isinstance(reponse, str) else cast(TextIO, reponse)
    code = menage.main(
        argv,
        gardes=None if copies is not None else gardes or _gardes(),
        copies=copies,
        anciennes_adresses=(temporaire,),
        environnement={"TMPDIR": str(temporaire)},
        entree=entree,
        sortie=sortie,
        erreur=erreur,
    )
    return code, sortie.getvalue(), erreur.getvalue()


def _instantane(*racines: Path) -> list[str]:
    """Tout ce qui existe sous `racines`, avec sa taille — de quoi prouver que rien n'a bougé."""
    vus = []
    for racine in racines:
        for chemin in sorted(racine.rglob("*")):
            taille = chemin.stat().st_size if chemin.is_file() else -1
            vus.append(f"{chemin}|{taille}")
    return vus


@pytest.fixture
def poste(maison: Path, temporaire: Path) -> dict[str, Path]:
    """Un poste comme celui du constat : chaque racine, ses intrus, et le hors-racine."""
    etat = racine_etat()
    jetable = temporaire / NOM_JETABLE
    elements = {
        "projet": _dossier(racine_projets() / "depensio"),
        "jeton": etat / "jeton-api",
        "ateliers": _dossier(racine_ateliers() / "scenarios"),
        "profil": _dossier(etat / NOM_PROFIL_NAVIGATEUR),
        "intrus_etat": _dossier(etat / "banc-728"),
        "hote_mort": _dossier(jetable / "maestro-hote-run-1-abcdefgh"),
        "stack": _dossier(jetable / "controltower-8058-3058"),
        "essais": _dossier(maison / "maestro-essais", contenu="x" * 2048),
        "ancien_banc": _dossier(maison / ANCIEN_NOM_ATELIER),
        "ancienne_relecture": _dossier(maison / ANCIEN_ATELIER_RELECTURE),
        "demo": _dossier(temporaire / "maestro-chat-demo-abcdefgh"),
        "presentation": _dossier(temporaire / "maestro-presentation"),
        "etranger": _dossier(maison / "Documents"),
    }
    elements["jeton"].write_text("secret", encoding="utf-8")
    (elements["hote_mort"] / TEMOIN_PID).write_text(str(_pid_mort()), encoding="utf-8")
    _vieillir(elements["hote_mort"])
    return elements


def _par_chemin(entrees: tuple[Entree, ...]) -> dict[Path, Entree]:
    return {entree.chemin: entree for entree in entrees}


# =================================================================================================
# ① L'inventaire
# =================================================================================================


def test_le_check_montre_chaque_racine_et_le_hors_racine_avec_taille_et_proprietaire(
    poste: dict[str, Path], temporaire: Path
) -> None:
    """Chaque élément sous sa racine, avec son sort ; ce qui n'est pas à Maestro n'y est pas."""
    entrees = _par_chemin(
        inventorier(_gardes(), environnement={"TMPDIR": str(temporaire)},
                    anciennes_adresses=(temporaire,))
    )
    attendus = {
        "projet": None,  # la racine des projets se montre entière, pas projet par projet
        "jeton": (RACINE_ETAT, DECLARE),
        "profil": (RACINE_ETAT, DECLARE),
        "intrus_etat": (RACINE_ETAT, A_RETIRER),
        "hote_mort": (RACINE_JETABLE, RAMASSABLE),
        "stack": (RACINE_JETABLE, A_SON_PROPRIETAIRE),
        "essais": (HORS_RACINE, A_RETIRER),
        "ancien_banc": (HORS_RACINE, A_SON_PROPRIETAIRE),
        "ancienne_relecture": (HORS_RACINE, A_SON_PROPRIETAIRE),
        "demo": (HORS_RACINE, RAMASSABLE),
        "presentation": (HORS_RACINE, A_SON_PROPRIETAIRE),
    }
    for nom, attendu in attendus.items():
        if attendu is None:
            assert poste[nom] not in entrees
            continue
        entree = entrees[poste[nom]]
        assert (entree.racine, entree.sort) == attendu, nom
        assert entree.proprietaire, f"{nom} : propriétaire vide"
        assert entree.taille is not None and entree.taille > 0, nom
    assert entrees[racine_projets()].sort == JAMAIS
    assert entrees[racine_ateliers()].sort == DECLARE
    assert poste["etranger"] not in entrees, "un dossier sans le préfixe n'est pas à Maestro"
    assert "pid" in entrees[poste["hote_mort"]].proprietaire, "l'occupant d'un jetable est nommé"
    assert entrees[poste["essais"]].taille == 2048


def test_sans_option_la_commande_verifie_et_n_ecrit_rien(
    poste: dict[str, Path], maison: Path, temporaire: Path
) -> None:
    avant = _instantane(maison, temporaire)
    code, sortie, _ = _jouer([], temporaire, reponse="oui\n")
    assert code == CODE_FAIT
    assert _instantane(maison, temporaire) == avant, "--check par défaut : rien n'a bougé"
    assert "rien n'est écrit" in sortie
    for titre in (f"~/{NOM_PROJETS}/", f"~/{NOM_ETAT}/", f"<temp>/{NOM_JETABLE}/", HORS_RACINE):
        assert titre in sortie
    assert "~/maestro-essais" in sortie and "2,0 Ko" in sortie
    assert f"python -m {MODULE} --purger" in sortie


def test_chaque_famille_que_le_ramassage_laisse_a_un_autre_a_son_proprietaire() -> None:
    """Une famille tierce ajoutée au ramassage sans propriétaire ici serait montrée sans nom."""
    assert set(menage.PROPRIETAIRES_TIERS) == set(FAMILLES_TIERCES)
    assert set(menage.PROPRIETAIRES_TIERS_JETABLES) == set(FAMILLES_TIERCES_JETABLES)


def test_un_profil_de_navigateur_part_avec_son_worktree(maison: Path, temporaire: Path) -> None:
    vivant = _dossier(racine_etat() / f"{NOM_PROFIL_NAVIGATEUR}-1458")
    orphelin = _dossier(racine_etat() / f"{NOM_PROFIL_NAVIGATEUR}-999")
    entrees = _par_chemin(
        inventorier(_gardes(worktrees=("1458",)), anciennes_adresses=(),
                    environnement={"TMPDIR": str(temporaire)})
    )
    assert entrees[vivant].sort == DECLARE
    assert (entrees[orphelin].sort, entrees[orphelin].raison) == (A_RETIRER, "worktree retiré")
    assert "#999" in entrees[orphelin].proprietaire


# =================================================================================================
# ② La purge
# =================================================================================================


def test_purger_sans_oui_ne_retire_rien(
    poste: dict[str, Path], maison: Path, temporaire: Path
) -> None:
    avant = _instantane(maison, temporaire)
    for reponse in ("non\n", "o\n", "", "oui, mais\n"):
        code, sortie, _ = _jouer(["--purger"], temporaire, reponse=reponse)
        assert code == CODE_NON_CONFIRME, reponse
        assert "Rien n'est retiré" in sortie
    assert _instantane(maison, temporaire) == avant


def test_purger_sur_oui_ne_retire_que_ce_qu_aucun_ramasseur_ne_connait(
    poste: dict[str, Path], temporaire: Path
) -> None:
    code, sortie, _ = _jouer(["--purger"], temporaire, reponse=" OUI \n")
    assert code == CODE_FAIT, sortie
    assert not poste["intrus_etat"].exists()
    assert not poste["essais"].exists()
    restent = ("projet", "jeton", "ateliers", "profil", "hote_mort", "stack", "ancien_banc",
               "ancienne_relecture", "demo", "presentation", "etranger")
    for nom in restent:
        assert poste[nom].exists(), f"{nom} n'était pas à retirer"
    assert "Retiré(s) : 2 élément(s)" in sortie


def test_purger_ne_retire_jamais_un_dossier_vivant(
    maison: Path, temporaire: Path, tmp_path: Path
) -> None:
    """Un occupant qui tourne (dans le nom ou par témoin), une activité récente, du travail."""
    marque = _dossier(maison / f"maestro-dev-pid{os.getpid()}-abcdefgh")
    temoin = _dossier(maison / "maestro-banc-temoin")
    (temoin / TEMOIN_PID).write_text(str(os.getpid()), encoding="utf-8")
    _vieillir(temoin)
    recent = _dossier(maison / "maestro-mesure-882", age_h=0)
    travail = maison / "maestro-verif-1160"
    _worktree_dun_depot_existant(travail, tmp_path / "depot")
    _vieillir(travail)
    mort = _dossier(maison / f"maestro-dev-pid{_pid_mort()}-zyxwvuts")

    entrees = _par_chemin(
        inventorier(_gardes(), anciennes_adresses=(), environnement={"TMPDIR": str(temporaire)})
    )
    for vivant, motif in ((marque, "occupant vivant"), (temoin, "occupant vivant"),
                          (recent, "actif"), (travail, "worktree")):
        assert entrees[vivant].sort == CONSERVE and motif in entrees[vivant].raison, vivant.name
    assert entrees[mort].sort == A_RETIRER

    code, _, _ = _jouer(["--purger"], temporaire, reponse="oui\n")
    assert code == CODE_FAIT
    assert all(chemin.exists() for chemin in (marque, temoin, recent, travail))
    assert not mort.exists()


def _worktree_dun_depot_existant(espace: Path, depot: Path) -> None:
    """La signature lue par le ramassage : un `.git` fichier qui pointe vers un dépôt présent."""
    administration = depot / ".git" / "worktrees" / "tache"
    administration.mkdir(parents=True)
    espace.mkdir(parents=True)
    (espace / ".git").write_text(f"gitdir: {administration.as_posix()}\n", encoding="utf-8")


def test_purger_ne_retire_jamais_la_racine_des_projets_ni_un_projet_declare(
    maison: Path, temporaire: Path
) -> None:
    porteur = _dossier(maison / "maestro-banc-1285")
    projet = _dossier(porteur / "20261001-101010" / "s1-vider")
    _vieillir(porteur)
    reglee = _dossier(maison / "maestro-projets")  # un répertoire des projets réglé
    racine = _dossier(racine_projets())
    gardes = _gardes(projet, reglee)

    entrees = _par_chemin(
        inventorier(gardes, anciennes_adresses=(), environnement={"TMPDIR": str(temporaire)})
    )
    assert entrees[porteur].sort == CONSERVE and "porte le projet" in entrees[porteur].raison
    assert entrees[reglee].sort == CONSERVE
    assert entrees[racine].sort == JAMAIS

    code, _, _ = _jouer(["--purger"], temporaire, gardes=gardes, reponse="oui\n")
    assert code == CODE_FAIT
    assert projet.exists() and reglee.exists() and racine.exists()


def test_les_declarations_de_chaque_copie_du_depot_protegent(
    maison: Path, temporaire: Path, tmp_path: Path
) -> None:
    """Un projet déclaré depuis un worktree en est un ; une déclaration illisible arrête tout."""
    principal, worktree = tmp_path / "Maestro", tmp_path / "worktrees" / "1458-menage"
    projet = _dossier(maison / "maestro-banc-259" / "p1")
    _declarer(worktree, Projet(id="prj-00001458", nom="banc", racine=projet.as_posix()))
    reglages = principal / menage.REGLAGES_DANS_UNE_COPIE
    reglages.mkdir(parents=True)
    (reglages / "projets.json").write_text(
        json.dumps({"repertoire": (maison / "maestro-projets").as_posix()}), encoding="utf-8"
    )

    gardes = protections(copies=[principal, worktree])
    assert gardes.illisible == ()
    assert gardes.worktrees == frozenset({"1458"})
    cles = {Path(p).resolve() for p in gardes.projets}
    assert projet.resolve() in cles
    assert (maison / "maestro-projets").resolve() in cles
    assert racine_projets().resolve() in cles

    illisible = principal / menage.DECLARATIONS_DANS_UNE_COPIE / "prj-0000dead.json"
    illisible.parent.mkdir(parents=True)
    illisible.write_text("{ pas du json", encoding="utf-8")
    gardes = protections(copies=[principal, worktree])
    assert gardes.illisible and "prj-0000dead" in gardes.illisible[0]

    intrus = _dossier(maison / "maestro-essais")
    code, sortie, erreur = _jouer(["--purger"], temporaire, gardes=gardes, reponse="oui\n")
    assert code == CODE_NON_CONFIRME
    assert "Purge impossible" in erreur and "rien n'est retiré à l'aveugle" in sortie
    assert intrus.exists()


def test_une_declaration_dont_la_racine_a_disparu_ne_protege_plus_rien(
    maison: Path, temporaire: Path, tmp_path: Path
) -> None:
    """Le constat de #1477 : dix déclarations du banc, racines parties avec leur passage,
    gardaient `~/maestro-scenarios` « porteur d'un projet déclaré », et avec lui les worktrees
    de ses dépôts. Une racine disparue ne protège plus rien ; sa déclaration est montrée, avec
    sa copie et sa racine, et se solde sur un « oui » — nommée par son identifiant, ou avec le
    reste —, par le verbe du produit. Une déclaration vivante, elle, protège toujours."""
    principal, worktree = tmp_path / "Maestro", tmp_path / "worktrees" / "1477-declarations"
    porteur = _dossier(maison / "maestro-banc-1477")
    passage = porteur / "20260922-134829" / "s1-vider"
    passage.mkdir(parents=True)
    depot = principal / menage.DECLARATIONS_DANS_UNE_COPIE
    morte = ProjetStore(depot).creer("banc-s1-vider", passage)
    shutil.rmtree(passage.parent)
    _vieillir(porteur)
    vivant = _dossier(maison / "maestro-banc-vivant" / "p1")
    _vieillir(vivant.parent)
    banc = donnees_du_banc(racine=worktree).racines["projets"]
    garde = ProjetStore(banc).creer("p1", vivant)

    gardes = protections(copies=[principal, worktree])
    assert gardes.illisible == ()
    cles = {Path(p).resolve() for p in gardes.projets}
    assert vivant.resolve() in cles, "le jeu du banc d'une copie est un dépôt de déclarations"
    assert passage.resolve() not in cles
    assert [(o.depot, o.id) for o in gardes.orphelines] == [(depot, morte.id)]

    entrees = _par_chemin(
        inventorier(gardes, anciennes_adresses=(), environnement={"TMPDIR": str(temporaire)})
    )
    assert entrees[porteur].sort == A_RETIRER, "plus rien ne l'épingle"
    assert entrees[vivant.parent].sort == CONSERVE
    assert "porte le projet" in entrees[vivant.parent].raison
    fiche = entrees[ProjetStore(depot).fichier(morte.id)]
    assert fiche.sort == A_RETIRER and passage.as_posix() in fiche.raison

    code, sortie, _ = _jouer(["--purger", morte.id], temporaire, gardes=gardes, reponse="oui\n")
    assert code == CODE_FAIT, sortie
    assert "déclarations de projets" in sortie and "racine disparue" in sortie
    assert ProjetStore(depot).ids() == (), "oubliée par son identifiant"
    assert porteur.exists(), "non désigné : il reste"
    assert ProjetStore(banc).ids() == (garde.id,) and vivant.exists()


def test_une_declaration_se_rejuge_au_moment_de_l_oublier(
    maison: Path, temporaire: Path, tmp_path: Path
) -> None:
    """La racine revenue entre la liste et la réponse : la déclaration protège de nouveau, et
    reste."""
    principal = tmp_path / "Maestro"
    racine = _dossier(maison / "maestro-banc-revenu" / "p1")
    depot = principal / menage.DECLARATIONS_DANS_UNE_COPIE
    projet = ProjetStore(depot).creer("p1", racine)
    shutil.rmtree(racine)

    code, sortie, _ = _jouer(
        ["--purger", projet.id], temporaire, reponse=_RacineRevenue(racine), copies=[principal]
    )
    assert code == CODE_FAIT, sortie
    assert ProjetStore(depot).ids() == (projet.id,)
    assert "gardé" in sortie


def test_une_racine_indecidable_n_est_jamais_dite_disparue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seule une absence certaine libère : un volume qui ne répond pas, un accès refusé, et la
    racine protège comme si elle était là."""
    present = _dossier(tmp_path / "present")
    assert not menage.racine_disparue(present)
    assert menage.racine_disparue(tmp_path / "parti" / "p1")

    refusee = tmp_path / "refusee"
    stat_reel = os.stat

    def stat(chemin: object, *args: object, **kwargs: object) -> os.stat_result:
        if isinstance(chemin, str | os.PathLike) and Path(chemin) == refusee:
            raise PermissionError(13, "accès refusé", str(refusee))
        return stat_reel(chemin, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(menage.os, "stat", stat)
    assert not menage.racine_disparue(refusee)
    monkeypatch.undo()

    if sys.platform == "win32":
        lettres = "ZYXWVUTSRQPONMLKJIHGFED"
        libres = [f"{x}:\\" for x in lettres if not os.path.exists(f"{x}:\\")]
        if libres:
            assert not menage.racine_disparue(Path(libres[0]) / "projets" / "p1")


def _declarer(copie: Path, projet: Projet) -> None:
    depot = copie / menage.DECLARATIONS_DANS_UNE_COPIE
    depot.mkdir(parents=True, exist_ok=True)
    (depot / f"{projet.id}.json").write_text(json.dumps(projet.to_dict()), encoding="utf-8")


def test_purger_ne_retire_que_les_noms_designes(
    poste: dict[str, Path], temporaire: Path
) -> None:
    code, sortie, _ = _jouer(["--purger", "maestro-essais"], temporaire, reponse="oui\n")
    assert code == CODE_FAIT, sortie
    assert not poste["essais"].exists()
    assert poste["intrus_etat"].exists(), "non désigné : il reste"


def test_un_nom_sans_element_a_retirer_est_une_erreur_d_usage(
    poste: dict[str, Path], maison: Path, temporaire: Path
) -> None:
    avant = _instantane(maison, temporaire)
    for argv in (["--purger", "jeton-api"], ["--purger", "inexistant"], ["maestro-essais"],
                 ["--check", "--purger"], ["--tout"]):
        code, _, erreur = _jouer(argv, temporaire, reponse="oui\n")
        assert code == CODE_USAGE, argv
        assert "Usage" in erreur
    assert _instantane(maison, temporaire) == avant


class _ReponseTardive:
    """Une réponse qui arrive après qu'un process s'est installé dans un des dossiers listés."""

    def __init__(self, dossier: Path) -> None:
        self._dossier = dossier

    def readline(self) -> str:
        (self._dossier / TEMOIN_PID).write_text(str(os.getpid()), encoding="utf-8")
        return "oui\n"


class _RacineRevenue:
    """Une réponse qui arrive après que la racine d'un projet déclaré est revenue."""

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    def readline(self) -> str:
        _dossier(self._racine)
        return "oui\n"


def test_le_sort_est_rejuge_au_moment_de_retirer(
    poste: dict[str, Path], temporaire: Path
) -> None:
    code, sortie, _ = _jouer(
        ["--purger"], temporaire, reponse=_ReponseTardive(poste["essais"])
    )
    assert code == CODE_FAIT
    assert poste["essais"].exists(), "devenu vivant entre la liste et la réponse : gardé"
    assert "gardé" in sortie
    assert not poste["intrus_etat"].exists()


@besoin_de_git
def test_copies_du_depot_rend_le_clone_et_ses_worktrees(tmp_path: Path) -> None:
    depot = tmp_path / "depot"
    depot.mkdir()
    for arguments in (
        ["init", "--quiet"],
        ["-c", "user.email=t@t", "-c", "user.name=T", "commit", "--quiet", "--allow-empty",
         "-m", "socle"],
        ["worktree", "add", "--quiet", str(tmp_path / "1458-menage")],
    ):
        subprocess.run([GIT or "git", *arguments], cwd=depot, check=True, capture_output=True)
    copies, manque = copies_du_depot(depot)
    assert manque == ""
    assert {copie.resolve() for copie in copies} == {
        depot.resolve(), (tmp_path / "1458-menage").resolve()
    }
    assert copies_du_depot(tmp_path / "sans-git") == ((tmp_path / "sans-git",), "")


# =================================================================================================
# ③ La règle confrontée au dépôt
# =================================================================================================

#: Un chemin composé sous le dossier personnel : `$HOME/x`, `${HOME}/x`, `${HOME//…}/x`, `~/x`.
_CHEMIN_DU_PROFIL = re.compile(r"(?:\$HOME|\$\{HOME[^}]*\}|(?<![\w~/.\\])~)/([A-Za-z0-9._-]+)")

#: Le dossier personnel lu par du code, sans chemin écrit en clair.
_APPEL_DU_PROFIL = re.compile(r"\bPath\.home\(\)|\bos\.homedir\(\)")

#: Ce qu'un script ou un prompt a le droit de nommer sous le dossier personnel : les deux
#: racines du profil, et les deux anciens ateliers que leur propriétaire reprend et vide.
SEGMENTS_ADMIS = frozenset({NOM_PROJETS, NOM_ETAT, ANCIEN_NOM_ATELIER, ANCIEN_ATELIER_RELECTURE})

#: Les autres chemins du profil, chacun avec sa raison : des **lectures** de ce que d'autres
#: outils y tiennent, jamais une écriture de Maestro. L'inventaire ne peut que décroître.
INVENTAIRE: dict[tuple[str, str], str] = {
    ("scripts/git/worktree.sh", ".claude"): (
        "la configuration de Claude Code (CLAUDE_CONFIG_DIR par défaut), lue pour retrouver "
        "les sessions d'un ticket ; Maestro n'y pose rien"
    ),
    ("scripts/controltower/start.sh", ".local"): (
        "les lanceurs XDG du poste, lus pour reconnaître le navigateur par défaut sous Linux"
    ),
    ("scripts/controltower/start.sh", "Library"): (
        "les préférences de LaunchServices, lues pour reconnaître le navigateur par défaut "
        "sous macOS"
    ),
}

#: Où vivent les scripts et les prompts qui agissent sur le poste. Les docs racontent, elles
#: n'agissent pas ; `CLAUDE.md` énonce la règle et a le droit d'en nommer les fautes.
_PERIMETRE = ("scripts", ".claude/commands", ".claude/skills", ".claude/agents", ".claude/hooks",
              "apps/desktop")
_FICHIERS_ISOLES = (".claude/settings.json", ".claude/settings.local.example.json", ".mcp.json")
_SUFFIXES = {".sh", ".py", ".mjs", ".js", ".cjs", ".ts", ".md", ".json", ".ps1", ".Dockerfile",
             ".yml", ".yaml", ".txt", ".tsv"}


def _est_un_commentaire(ligne: str, suffixe: str) -> bool:
    """Un commentaire raconte, il ne compose rien : `#` en shell et Python, `//` et `*` en JS."""
    nue = ligne.lstrip()
    if suffixe in {".sh", ".py", ".ps1", ".Dockerfile", ".yml", ".yaml"}:
        return nue.startswith("#")
    if suffixe in {".mjs", ".js", ".cjs", ".ts"}:
        return nue.startswith(("//", "*", "/*"))
    return False


def sites_du_profil(texte: str, suffixe: str) -> list[tuple[int, str]]:
    """Les lignes de `texte` qui nomment sous le dossier personnel autre chose qu'une racine."""
    sites: list[tuple[int, str]] = []
    for numero, ligne in enumerate(texte.splitlines(), start=1):
        if _est_un_commentaire(ligne, suffixe):
            continue
        for segment in _CHEMIN_DU_PROFIL.findall(ligne):
            if segment not in SEGMENTS_ADMIS:
                sites.append((numero, segment))
        if _APPEL_DU_PROFIL.search(ligne):
            sites.append((numero, "(dossier personnel lu par le code)"))
    return sites


_ECHANTILLON_FAUTIF = """\
#!/usr/bin/env bash
# ~/maestro-raconte : un commentaire n'est pas un chemin composé
mkdir -p "$HOME/maestro-essais"
cp a "${HOME}/.cache-maestro/x"
racine="${HOME//\\\\//}/maestro-mesure"
cd ~/maestro-verif && ls
echo "$HOME/.maestro/ateliers et ~/Maestro/projet sont admis"
node -e "const os = require('os'); console.log(os.homedir())"
"""


def test_le_motif_reconnait_chaque_chemin_du_profil_hors_des_racines() -> None:
    """La preuve d'abord : sans elle, un balayage vert ne dirait rien."""
    sites = sites_du_profil(_ECHANTILLON_FAUTIF, ".sh")
    assert sites == [
        (3, "maestro-essais"),
        (4, ".cache-maestro"),
        (5, "maestro-mesure"),
        (6, "maestro-verif"),
        (8, "(dossier personnel lu par le code)"),
    ]
    assert sites_du_profil("Écrire dans `~/maestro-essais`.\n", ".md") == [(1, "maestro-essais")]


def _fichiers_du_perimetre() -> list[Path]:
    fichiers = [RACINE_DEPOT / chemin for chemin in _FICHIERS_ISOLES]
    for dossier in _PERIMETRE:
        for chemin in fichiers_du_depot(RACINE_DEPOT / dossier):
            if not chemin.is_file():
                continue
            if chemin.suffix in _SUFFIXES or chemin.name.endswith(".Dockerfile"):
                fichiers.append(chemin)
    return sorted(chemin for chemin in fichiers if chemin.is_file())


def _sites_du_depot() -> dict[tuple[str, str], list[int]]:
    trouves: dict[tuple[str, str], list[int]] = {}
    for chemin in _fichiers_du_perimetre():
        suffixe = ".Dockerfile" if chemin.name.endswith(".Dockerfile") else chemin.suffix
        texte = chemin.read_text(encoding="utf-8", errors="replace")
        relatif = chemin.relative_to(RACINE_DEPOT).as_posix()
        for numero, segment in sites_du_profil(texte, suffixe):
            trouves.setdefault((relatif, segment), []).append(numero)
    return trouves


def test_le_depot_ne_compose_aucun_chemin_du_profil_hors_des_racines() -> None:
    """Scripts et prompts n'écrivent sous `~` que dans les racines — ou lisent, avec raison."""
    hors_inventaire = {cle: lignes for cle, lignes in _sites_du_depot().items()
                       if cle not in INVENTAIRE}
    assert hors_inventaire == {}, (
        f"sous le dossier personnel, seuls ~/{NOM_PROJETS}/ et ~/{NOM_ETAT}/ sont des racines "
        "(maestro/emplacements.py) ; une session ou un script n'ouvre pas ~/<autre chose>. Une "
        f"simple lecture s'inscrit dans INVENTAIRE avec sa raison : {hors_inventaire}"
    )


def test_l_inventaire_des_chemins_du_profil_ne_garde_que_ce_qui_sert() -> None:
    perimees = set(INVENTAIRE) - set(_sites_du_depot())
    assert perimees == set(), f"entrées d'INVENTAIRE devenues sans objet : {perimees}"


def test_les_scripts_posent_les_noms_que_l_inventaire_connait() -> None:
    """Deux noms qu'aucun module Python n'écrit : ceux des scripts, tels quels."""
    attendus = {
        "scripts/setup.sh": f"/{NOM_ETAT}/{NOM_PROFIL_NAVIGATEUR}}}",
        "scripts/git/worktree.sh": f"/{NOM_ETAT}/{NOM_PROFIL_NAVIGATEUR}-$iid",
        "scripts/design/relecture-visuelle.sh": f'/{ANCIEN_ATELIER_RELECTURE}"',
    }
    for script, motif in attendus.items():
        texte = (RACINE_DEPOT / script).read_text(encoding="utf-8")
        assert motif in texte, f"{script} ne pose plus « {motif} » : maestro.menage le suit"


def _section(texte: str, titre: str) -> str:
    """De `titre` au titre suivant de même niveau ou plus haut."""
    debut = texte.index(titre)
    suite = re.search(r"\n#{1,3} ", texte[debut + len(titre) :])
    return texte[debut : debut + len(titre) + suite.start()] if suite else texte[debut:]


def test_claude_md_porte_la_regle_des_trois_racines() -> None:
    """La règle que chaque session lit, aux noms que le code déclare."""
    texte = (RACINE_DEPOT / "CLAUDE.md").read_text(encoding="utf-8")
    for attendu in (
        f"`~/{NOM_PROJETS}/`",
        f"`~/{NOM_ETAT}/`",
        f"`<temp>/{NOM_JETABLE}/`",
        "`maestro/emplacements.py`",
        "`.maestro/session/`",
        "scratchpad",
        "jamais dans `~`",
        f"-m {MODULE}",
        "--purger",
        "tests/test_menage.py",
    ):
        assert attendu in texte, f"CLAUDE.md ne dit plus {attendu}"


def test_la_doc_du_chantier_porte_la_demonstration() -> None:
    """La commande pour qui se sert de Maestro ; la règle des sessions pour qui le développe."""
    texte = (RACINE_DEPOT / "docs" / "24-projets-locaux-et-poste-de-travail.md").read_text(
        encoding="utf-8"
    )
    produit = _section(texte, "### 2.7 Où Maestro écrit sur le poste")
    for attendu in (f"-m {MODULE}", "--purger", "« oui »", "jamais retiré", "à retirer"):
        assert attendu in produit, f"docs/24 §2.7 ne dit plus {attendu}"
    developpement = _section(texte, "### 2.7.1 ")
    assert developpement.splitlines()[1] == "<!-- documentation: développement -->"
    for attendu in ("`.maestro/session/`", "jamais dans `~`", "tests/test_menage.py"):
        assert attendu in developpement, f"docs/24 §2.7.1 ne dit plus {attendu}"
