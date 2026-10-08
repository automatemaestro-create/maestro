"""Les trois racines du poste, et la garde qui empêche d'en créer une quatrième (#1455).

Ce que ce fichier tient (`maestro.emplacements` en porte la démonstration) :

① **la garde** — sous `maestro/`, aucun module ne compose un chemin sous le dossier
  personnel ni n'ouvre un dossier temporaire sans passer par `maestro.emplacements`,
  hors des lectures que l'inventaire nomme avec leur raison. Son motif est d'abord
  **prouvé sur un échantillon fautif** : une garde qui ne sait rien reconnaître
  passerait au vert sur n'importe quel dépôt ;
② **un run sur `tmp_path`** — l'atelier d'un hôte et les espaces de ses tâches
  naissent tous sous `<temp>/maestro/`, rien à côté, et chacun nomme son occupant ;
③ **les racines du profil** — le jeton, le journal SQLite et le répertoire des
  projets se lisent sous les deux racines déclarées, et nulle part ailleurs.
"""

from __future__ import annotations

import ast
import asyncio
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from maestro import emplacements
from maestro.config import load_settings
from maestro.controltower import hote_detache
from maestro.controltower.acces import chemin_du_jeton
from maestro.controltower.hote import OrdreRun
from maestro.controltower.persistence import chemin_sqlite
from maestro.emplacements import (
    NOM_JETABLE,
    TEMOIN_PID,
    jetable,
    occupant,
    racine_etat,
    racine_jetable,
    racine_projets,
    repertoire_temporaire,
)
from maestro.projets.modele import Perimetre, Projet
from maestro.projets.racine import detecter_vcs
from maestro.projets.reglages import repertoire_par_defaut
from maestro.sandbox import espace_de_travail, isolated_workspace
from maestro.sandbox.ramassage import pid_dans
from maestro.sandbox.verification import copie_de_verification, dossier_de_sonde

RACINE_DEPOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")

besoin_de_git = pytest.mark.skipif(GIT is None, reason="git introuvable")


# =================================================================================================
# ① La garde : aucune quatrième racine
# =================================================================================================

#: Les appels qui ouvrent un dossier ou un fichier temporaire — tous, hors du module
#: des racines, passent par `maestro.emplacements.jetable`.
_TEMPFILE = frozenset(
    {
        "mkdtemp",
        "mkstemp",
        "TemporaryDirectory",
        "NamedTemporaryFile",
        "SpooledTemporaryFile",
        "gettempdir",
    }
)

#: Les modules qui lisent le dossier personnel **sans y rien créer**, chacun avec sa
#: raison. L'inventaire ne peut que décroître : une entrée qui ne sert plus fait
#: rougir (`test_linventaire_ne_garde_que_ce_qui_sert`).
INVENTAIRE: dict[str, str] = {
    "maestro/controltower/projets.py": (
        "propose le dossier personnel comme racine de parcours du sélecteur de "
        "dossier ; n'y crée rien"
    ),
    "maestro/projets/racine.py": (
        "juge une racine déclarée contre le dossier personnel (racine nue refusée) ; "
        "n'y crée rien"
    ),
    "maestro/scenarios/projets.py": (
        "atelier du banc des scénarios, encore à la racine du profil — il rejoint "
        "`~/.maestro/` avec #1457"
    ),
}


def sites_interdits(source: str) -> list[tuple[int, str]]:
    """Les lignes de `source` qui créent hors des racines déclarées, et ce qu'on y lit.

    L'arbre syntaxique et non le texte : un docstring qui **raconte** un
    `tempfile.mkdtemp()` (il y en a, et c'est leur droit) n'est pas un appel.
    """
    sites: list[tuple[int, str]] = []
    for noeud in ast.walk(ast.parse(source)):
        if isinstance(noeud, ast.ImportFrom) and noeud.module == "tempfile":
            for alias in noeud.names:
                if alias.name in _TEMPFILE:
                    sites.append((noeud.lineno, f"from tempfile import {alias.name}"))
        if not isinstance(noeud, ast.Call):
            continue
        fonction = noeud.func
        if isinstance(fonction, ast.Attribute) and isinstance(fonction.value, ast.Name):
            if fonction.value.id == "tempfile" and fonction.attr in _TEMPFILE:
                sites.append((noeud.lineno, f"tempfile.{fonction.attr}"))
            elif fonction.value.id == "Path" and fonction.attr == "home":
                sites.append((noeud.lineno, "Path.home"))
        premier = noeud.args[0] if noeud.args else None
        if (
            isinstance(premier, ast.Constant)
            and isinstance(premier.value, str)
            and premier.value.startswith("~")
            and (
                (isinstance(fonction, ast.Name) and fonction.id == "Path")
                or (isinstance(fonction, ast.Attribute) and fonction.attr == "expanduser")
            )
        ):
            sites.append((noeud.lineno, f"chemin écrit sous ~ ({premier.value!r})"))
    return sorted(sites)


_ECHANTILLON_FAUTIF = '''\
"""Un module qui raconte `tempfile.mkdtemp()` et `Path.home()` sans les appeler."""
import os
import tempfile
from pathlib import Path
from tempfile import mkdtemp

# Path.home() / "cache" — un commentaire n'est pas un appel non plus.
a = tempfile.mkdtemp(prefix="x-")
b = Path.home() / ".cache-maison"
c = tempfile.TemporaryDirectory()
d = os.path.expanduser("~/sauvage")
e = Path("~/ailleurs").expanduser()
f = tempfile.gettempdir()
'''


def test_le_motif_reconnait_chaque_forme_fautive_et_aucun_texte() -> None:
    """La preuve d'abord : sans elle, un balayage vert ne dirait rien."""
    sites = sites_interdits(_ECHANTILLON_FAUTIF)
    assert [ligne for ligne, _ in sites] == [5, 8, 9, 10, 11, 12, 13]
    lus = " ".join(motif for _, motif in sites)
    for attendu in ("mkdtemp", "Path.home", "TemporaryDirectory", "~/sauvage", "~/ailleurs"):
        assert attendu in lus, f"{attendu} échappe au motif"


def test_le_motif_laisse_passer_un_chemin_donne_par_la_personne() -> None:
    """`Path(reglage).expanduser()` lit un réglage, il ne choisit pas d'emplacement."""
    assert sites_interdits("from pathlib import Path\nx = Path(brut).expanduser()\n") == []


def _sources_du_produit() -> list[Path]:
    module = RACINE_DEPOT / "maestro" / "emplacements.py"
    return sorted(
        chemin for chemin in (RACINE_DEPOT / "maestro").rglob("*.py") if chemin != module
    )


def _fautes_du_produit() -> dict[str, list[tuple[int, str]]]:
    fautes: dict[str, list[tuple[int, str]]] = {}
    for chemin in _sources_du_produit():
        sites = sites_interdits(chemin.read_text(encoding="utf-8"))
        if sites:
            fautes[chemin.relative_to(RACINE_DEPOT).as_posix()] = sites
    return fautes


def test_le_produit_ne_cree_rien_hors_des_racines_declarees() -> None:
    """Le balayage : tout ce qui reste hors du module des racines est inventorié."""
    fautes = _fautes_du_produit()
    hors_inventaire = {fichier: fautes[fichier] for fichier in fautes if fichier not in INVENTAIRE}
    assert hors_inventaire == {}, (
        "créer sous le dossier personnel ou le répertoire temporaire passe par "
        "maestro.emplacements (racine_etat, racine_projets, jetable) ; une simple "
        f"lecture s'inscrit dans INVENTAIRE avec sa raison : {hors_inventaire}"
    )


def test_linventaire_ne_garde_que_ce_qui_sert() -> None:
    """Une entrée sans site ne protège plus rien : elle se retire, elle ne s'accumule pas."""
    perimees = set(INVENTAIRE) - set(_fautes_du_produit())
    assert perimees == set(), f"entrées d'INVENTAIRE devenues sans objet : {perimees}"


# =================================================================================================
# ② Un run sur tmp_path : tout sous <temp>/maestro, et chaque dossier nomme son occupant
# =================================================================================================


@pytest.fixture
def temporaire(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un répertoire temporaire du poste vide, que rien d'autre que le test n'habite."""
    racine = tmp_path_factory.mktemp("temp-du-poste")
    for nom in emplacements.VARIABLES_TEMP:
        monkeypatch.setenv(nom, str(racine))
    return racine


@pytest.fixture
def maison(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier personnel à part (#221 : `AppData` n'est pas une racine de projet)."""
    dossier = tmp_path_factory.mktemp("maison")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: dossier))
    return dossier


def _git(racine: Path, *arguments: str) -> None:
    subprocess.run([GIT or "git", *arguments], cwd=racine, check=True, capture_output=True)


def _projet_versionne(maison: Path) -> Projet:
    racine = maison / "projets" / "depensio"
    racine.mkdir(parents=True)
    (racine / "app.py").write_text("print('salut')\n", encoding="utf-8")
    _git(racine, "init", "--quiet")
    _git(racine, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(racine, "add", "-A")
    _git(racine, "-c", "user.email=t@t", "-c", "user.name=T", "commit", "--quiet", "-m", "socle")
    return Projet(
        id="prj-0000dead",
        nom="Dépensio",
        racine=racine.as_posix(),
        vcs=detecter_vcs(racine),
        perimetre=Perimetre(),
    )


def _occupant_nomme(dossier: Path) -> int | None:
    """Le pid qui occupe un jetable : celui de son nom, sinon celui de son témoin."""
    return pid_dans(dossier.name) or occupant(dossier)


@besoin_de_git
def test_un_run_ne_cree_rien_hors_de_la_racine_jetable_et_chaque_dossier_nomme_son_occupant(
    temporaire: Path, maison: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un hôte et ses tâches, joués sur `tmp_path` : le répertoire temporaire ne porte
    que `maestro/`, et tout ce qui est dessous désigne un process vivant."""
    # L'hôte : l'API ouvre l'atelier (le process fils est un double — on ne veut pas
    # de Redis ici), puis le fils s'y nomme, c'est la première chose que fait `main`.
    monkeypatch.setattr(hote_detache.HoteRunDetache, "_ouvrir_process", _process_double)
    hote = hote_detache.HoteRunDetache(delai_demarrage_s=0.0)
    asyncio.run(hote.lancer(OrdreRun(run_id="run-1455", objectif="Ranger le poste")))
    (atelier,) = racine_jetable().iterdir()
    assert atelier.name.startswith("maestro-hote-run-1455-")
    assert pid_dans(atelier.name) is None, "l'occupant n'est pas l'API : pas de marque"
    (atelier / hote_detache.FICHIER_ORDRE).unlink()  # le fils réel l'aurait lu et effacé
    assert hote_detache.main([str(atelier)]) == 2, "ordre déjà lu : le fils s'arrête là"
    assert occupant(atelier) == os.getpid(), "… mais il s'est nommé avant tout le reste"

    # Les tâches : sans projet, sur projet versionné (worktree), et leurs vérifications.
    projet = _projet_versionne(maison)
    with (
        isolated_workspace(prefix="maestro-dev-") as sans_projet,
        espace_de_travail(projet, tache_id="t-1455", prefix="maestro-integrateur-css-") as tache,
        copie_de_verification(Path(projet.racine)) as copie,
        dossier_de_sonde() as sonde,
    ):
        assert [enfant.name for enfant in temporaire.iterdir()] == [NOM_JETABLE]
        jetables = sorted(racine_jetable().iterdir())
        assert len(jetables) == 5, jetables
        for dossier in jetables:
            assert _occupant_nomme(dossier) == os.getpid(), f"{dossier.name} ne nomme personne"
        for chemin in (sans_projet.path, tache.path, copie, sonde):
            assert racine_jetable() in Path(chemin).parents, chemin


class _ProcessDouble:
    """Un hôte qui ne démarre jamais vraiment — c'est l'atelier qu'on regarde."""

    pid = 4242

    def poll(self) -> int | None:
        return None


def _process_double(self: object, atelier: Path, journal: Path) -> _ProcessDouble:
    return _ProcessDouble()


def test_un_jetable_non_marque_reste_sans_marque_et_sans_temoin(temporaire: Path) -> None:
    """`marque=False` ne pose rien à la place du pid : c'est à l'occupant de se nommer."""
    dossier = jetable("maestro-hote-x-", marque=False)
    assert pid_dans(dossier.name) is None
    assert not (dossier / TEMOIN_PID).exists()
    assert occupant(dossier) is None


def test_un_temoin_illisible_ne_nomme_personne(temporaire: Path) -> None:
    dossier = jetable("maestro-hote-x-", marque=False)
    (dossier / TEMOIN_PID).write_text("pas-un-pid", encoding="utf-8")
    assert occupant(dossier) is None


def test_la_racine_jetable_est_sous_le_temporaire_et_se_cree_a_la_demande(
    temporaire: Path,
) -> None:
    assert racine_jetable() == temporaire / NOM_JETABLE
    assert not racine_jetable().exists(), "lue, jamais créée"
    jetable("maestro-qa-")
    assert racine_jetable().is_dir()


# =================================================================================================
# ③ Les racines du profil
# =================================================================================================


def test_le_jeton_et_le_journal_vivent_sous_la_racine_de_letat(maison: Path) -> None:
    assert racine_etat() == maison / ".maestro"
    reglages = replace(load_settings(), api_jeton_fichier="", sqlite_fichier="")
    assert chemin_du_jeton(reglages).parent == racine_etat()
    assert chemin_sqlite(reglages).parent == racine_etat()


def test_le_repertoire_des_projets_propose_est_la_racine_des_projets(maison: Path) -> None:
    assert repertoire_par_defaut() == racine_projets() == maison / "Maestro"


def test_le_repertoire_temporaire_ecarte_une_valeur_msys_sous_windows(
    temporaire: Path,
) -> None:
    """La parade de #992 (S13) a déménagé ici avec la racine : elle tient toujours."""
    if sys.platform != "win32":
        pytest.skip("une valeur `/tmp` n'est illisible pour Python que sous Windows")
    env = {"TMPDIR": "/tmp", "TEMP": str(temporaire)}
    assert repertoire_temporaire(env) == temporaire
