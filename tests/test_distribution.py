"""Le paquet d'installation (#641) : ce que l'assembleur pose, et ce que la coque y cherche.

La vérification qui compte est celle de `scripts/distribution/verifier.sh` — installer le paquet
construit sur un poste dépouillé et le voir démarrer —, et elle ne se joue pas en CI (pas
d'Electron ni de NSIS dans le conteneur, #929). Ce module garde les **accords** dont elle dépend
et qu'un changement d'un seul côté casserait sans bruit :

- le `._pth` du runtime embarqué, qui l'isole du poste et met la racine du produit sur son chemin ;
- les fichiers du produit, pris parmi les fichiers **suivis** — jamais une donnée locale de
  `core/` ;
- l'emplacement du Node embarqué, celui où le lanceur le cherche ;
- le dossier où electron-builder prend le produit, celui où l'assembleur le pose, celui où la coque
  installée le sert ;
- le jeton de l'API, qui ne doit jamais entrer dans le front embarqué.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from maestro.controltower.documentation import MOTIFS_DOCUMENTATION
from maestro.controltower.persistence import SUPPORT_SQLITE
from maestro.lanceur import emplacement as lieu

RACINE = Path(__file__).resolve().parents[1]
ASSEMBLER = RACINE / "scripts" / "distribution" / "assembler.py"
CONSTRUIRE = RACINE / "scripts" / "distribution" / "construire.sh"
COQUE = RACINE / "apps" / "desktop"


@pytest.fixture(scope="module")
def assembleur() -> ModuleType:
    """`assembler.py` en module — il ne vit dans aucun paquet, d'où le chargement par chemin."""
    spec = importlib.util.spec_from_file_location("maestro_assembler_distribution", ASSEMBLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(depot: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=depot, check=True, capture_output=True)


def _depot_lisible() -> bool:
    """Le dépôt de la suite se lit-il par git, ici ?

    Pas dans le conteneur du filet local quand la suite joue depuis un worktree : son `.git` y
    désigne un répertoire git commun resté sur l'hôte. En CI, un checkout complet : jamais un saut
    (#333) — un dépôt illisible y est une panne, que le test rend rouge.
    """
    if shutil.which("git") is None:
        return False
    sonde = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=RACINE, capture_output=True)
    return sonde.returncode == 0 or bool(os.environ.get("CI"))


# ------------------------------------------------------------------ le runtime Python


def test_le_pth_isole_le_runtime_et_porte_la_racine_du_produit(assembleur: ModuleType) -> None:
    """Le `._pth` est ce qui fait ignorer PYTHONPATH, PYTHONHOME et le registre au runtime.

    Son contenu décide de ce que l'installation importe : la bibliothèque standard zippée du
    paquet officiel, les dépendances, et la racine du produit (`..\\..` depuis
    `runtime/python/`), où `maestro/` vit à côté de `core/`. `import site` en dernier, sans quoi
    les `.pth` des dépendances (pywin32) ne seraient pas lus.
    """
    embarquable = assembleur.Embarquable(version="3.13.14", url="u", sha256="e")
    assert embarquable.nom_pth == "python313._pth"
    lignes = assembleur.contenu_pth(embarquable).split("\r\n")
    assert lignes == ["python313.zip", ".", "Lib\\site-packages", "..\\..", "import site", ""]


def test_le_runtime_epingle_vient_de_python_org_avec_son_empreinte(assembleur: ModuleType) -> None:
    runtime = assembleur.PYTHON_EMBARQUE
    assert runtime.url.startswith("https://www.python.org/ftp/python/")
    assert f"/{runtime.version}/" in runtime.url
    assert runtime.url.endswith(f"python-{runtime.version}-embed-amd64.zip")
    assert re.fullmatch(r"[0-9a-f]{64}", runtime.sha256)


def test_une_archive_qui_ne_porte_pas_l_empreinte_publiee_est_refusee(
    assembleur: ModuleType, tmp_path: Path
) -> None:
    """L'empreinte se vérifie à chaque assemblage, archive en cache comprise."""
    embarquable = assembleur.Embarquable(
        version="3.13.14",
        url="https://exemple.invalid/python-3.13.14-embed-amd64.zip",
        sha256="0" * 64,
    )
    (tmp_path / "python-3.13.14-embed-amd64.zip").write_bytes(b"pas le paquet officiel")
    with pytest.raises(assembleur.Refus, match="empreinte"):
        assembleur.runtime_verifie(embarquable, tmp_path)


def test_les_contraintes_sont_les_versions_du_venv_sans_le_produit(assembleur: ModuleType) -> None:
    """Le venv où la suite passe fixe les versions posées chez l'utilisateur."""

    class Distribution:
        def __init__(self, nom: str, version: str) -> None:
            self.metadata = {"Name": nom}
            self.version = version

    contraintes = assembleur.contraintes_du_venv(
        [
            Distribution("FastAPI", "0.140.7"),
            Distribution("maestro", "0.1.0"),
            Distribution("fastapi", "0.1.0"),
            Distribution("claude-agent-sdk", "0.2.159"),
        ]
    )
    assert contraintes == ["FastAPI==0.140.7", "claude-agent-sdk==0.2.159"]


def test_chaque_commande_du_produit_a_son_lanceur(assembleur: ModuleType) -> None:
    """`maestro-confinement` est cherché à côté de l'interpréteur : il doit être posé."""
    commandes = assembleur.points_d_entree(RACINE / "pyproject.toml")
    assert commandes["maestro-confinement"] == "maestro.sandbox.confinement:main"
    assert commandes["maestro-lanceur"] == "maestro.lanceur.cli:main"


@pytest.mark.skipif(
    sys.platform != "win32", reason="les lanceurs .exe de distlib sont propres à Windows"
)
def test_le_lanceur_d_une_commande_designe_l_interpreteur_de_son_dossier(
    assembleur: ModuleType, tmp_path: Path
) -> None:
    """Aucun chemin du poste qui construit : le lanceur vise `<son dossier>\\python.exe`."""
    dossier = tmp_path / "dossier avec espace"
    dossier.mkdir()
    poses = assembleur.poser_lanceurs_console(dossier, RACINE)
    assert "maestro-confinement" in poses
    contenu = (dossier / "maestro-confinement.exe").read_bytes()
    assert b"#!<launcher_dir>\\python.exe" in contenu
    assert str(Path(sys.executable).parent).encode() not in contenu


# ------------------------------------------------------------------ le produit


def test_le_produit_ne_prend_que_les_fichiers_suivis(
    assembleur: ModuleType, tmp_path: Path
) -> None:
    """Un dépôt de `core/` rempli à l'usage (non suivi) reste sur le poste qui construit."""
    if shutil.which("git") is None:
        pytest.skip("git absent")
    _git(tmp_path, "init", "-q")
    (tmp_path / "core" / "projets").mkdir(parents=True)
    (tmp_path / "core" / "gabarit.yaml").write_text("suivi", encoding="utf-8")
    (tmp_path / "core" / "projets" / "mon-projet.json").write_text("{}", encoding="utf-8")
    _git(tmp_path, "add", "core/gabarit.yaml")
    assert assembleur.fichiers_suivis(tmp_path, ["core"]) == ["core/gabarit.yaml"]


def test_le_corpus_de_l_assistant_entre_dans_le_produit(assembleur: ModuleType) -> None:
    """L'assistant lit sa documentation sous la racine du produit : ses motifs doivent y être."""
    motifs = {motif for motif, _ in assembleur.CONTENU_DU_PRODUIT}
    assert set(MOTIFS_DOCUMENTATION) <= motifs


def test_chaque_motif_du_produit_designe_des_fichiers_suivis(assembleur: ModuleType) -> None:
    if not _depot_lisible():
        pytest.skip("le dépôt de la suite ne se lit pas par git ici (voir `_depot_lisible`)")
    for motif, _raison in assembleur.CONTENU_DU_PRODUIT:
        assert assembleur.fichiers_suivis(RACINE, [motif]), motif


def test_le_node_embarque_est_celui_que_le_lanceur_trouve(
    assembleur: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lieu.shutil, "which", lambda _nom: "/ailleurs/node")
    nom = "node.exe" if sys.platform == "win32" else "node"
    source = tmp_path / "outils" / nom
    source.parent.mkdir()
    source.write_bytes(b"")
    produit = tmp_path / "produit"
    pose = assembleur.poser_node(source, produit)
    trouve, manque = lieu.resoudre_node(produit, {})
    assert manque == ""
    assert trouve is not None and Path(trouve) == pose


def test_les_reglages_de_la_distribution_sont_le_mode_local_installe(
    assembleur: ModuleType,
) -> None:
    """Mode local (#639), et le produit se sait installé (#1620) : c'est le paquet qui le
    dit, jamais un chemin deviné ni la coque."""
    from maestro.emplacements import VALEUR_INSTALLATION, VARIABLE_INSTALLATION, installation
    from maestro.installation import forme_du_produit

    reglages = dict(
        ligne.split("=", 1)
        for ligne in assembleur.REGLAGES_DISTRIBUTION.splitlines()
        if ligne and not ligne.startswith("#")
    )
    assert reglages == {
        "MAESTRO_PERSISTANCE": SUPPORT_SQLITE,
        VARIABLE_INSTALLATION: VALEUR_INSTALLATION,
    }
    assert installation(reglages) is True
    # Et le même réglage le présente installé (#1633) : `GET /api/installation` ne devine rien.
    assert forme_du_produit(reglages) == "installation"


def test_un_front_qui_porte_le_jeton_est_trouve(assembleur: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "chunks").mkdir()
    (tmp_path / "chunks" / "a.js").write_text('const j="jeton-du-poste";', encoding="utf-8")
    (tmp_path / "chunks" / "b.js").write_text("const j=configPoste().jeton;", encoding="utf-8")
    assert assembleur.fuites_du_jeton(tmp_path, "jeton-du-poste") == [tmp_path / "chunks" / "a.js"]


def test_le_front_embarque_exige_son_serveur_autonome(
    assembleur: ModuleType, tmp_path: Path
) -> None:
    with pytest.raises(assembleur.Refus, match="server.js"):
        assembleur.copier_le_front(tmp_path / "standalone", RACINE, tmp_path / "produit")


def test_un_chemin_que_l_installeur_perdrait_est_trouve(
    assembleur: ModuleType, tmp_path: Path
) -> None:
    """Au-delà de MAX_PATH, NSIS perd le fichier et rend 0 : la garde le trouve avant."""
    court = tmp_path / "web" / "server.js"
    court.parent.mkdir(parents=True)
    court.write_text("", encoding="utf-8")
    profond = tmp_path.joinpath("runtime", *["d" * 20] * 9, "f.py")
    profond.parent.mkdir(parents=True)
    profond.write_text("", encoding="utf-8")
    trop_longs = assembleur.chemins_trop_longs(tmp_path)
    assert trop_longs and all(len(chemin) > assembleur.LONGUEUR_MAX for chemin in trop_longs)
    assert "resources/produit/runtime/" + "/".join(["d" * 20] * 9) + "/f.py" in trop_longs
    assert not any(chemin.endswith("server.js") for chemin in trop_longs)


def test_le_poids_des_parties_compte_chaque_octet_du_produit_une_fois(
    assembleur: ModuleType, tmp_path: Path
) -> None:
    """Le relevé de docs/54 §3.1 : la somme des parties est le produit, rien de plus."""
    fichiers = {
        "runtime/python/python.exe": 7,
        "runtime/python/python313.zip": 11,
        "runtime/python/maestro-lanceur.exe": 3,
        "runtime/python/Lib/site-packages/claude_agent_sdk/_bundled/claude.exe": 101,
        "runtime/python/Lib/site-packages/temporalio/a.py": 13,
        "runtime/node.exe": 17,
        "web/server.js": 19,
        "web/.next/static/a.js": 23,
        "maestro/__init__.py": 29,
        "docs/54.md": 31,
        ".env": 37,
    }
    for chemin, taille in fichiers.items():
        (tmp_path / chemin).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / chemin).write_bytes(b"x" * taille)
    parties = assembleur.poids_des_parties(tmp_path, tmp_path / "runtime" / "node.exe")
    assert sum(parties.values()) == assembleur.poids(tmp_path) == sum(fichiers.values())
    assert parties["runtime/python/Lib (dépendances, CLI claude compris)"] == 114
    assert parties["runtime/python (interpréteur et bibliothèque standard)"] == 21
    assert parties["maestro, core, agents, docs"] == 97


def test_le_budget_de_longueur_tient_dans_max_path(assembleur: ModuleType) -> None:
    """Le budget se recalcule ici : un nom de compte de 20 caractères, le TEMP de NSIS."""
    profil = "C:\\Users\\" + "u" * 20
    extraction = "\\AppData\\Local\\Temp\\nsXXXX.tmp\\7z-out\\"
    assert len(profil) + len(extraction) + assembleur.LONGUEUR_MAX + 1 == 260


# ------------------------------------------------------------------ le paquet


def test_le_paquet_prend_le_produit_la_ou_l_assembleur_le_pose(assembleur: ModuleType) -> None:
    """Trois chemins qui doivent n'en faire qu'un, lus là où ils vivent."""
    reglage = json.loads((COQUE / "package.json").read_text(encoding="utf-8"))["build"]
    (ressource,) = reglage["extraResources"]
    construire = CONSTRUIRE.read_text(encoding="utf-8")
    produit = (RACINE / ".maestro" / "distribution" / "produit").resolve()
    assert (COQUE / ressource["from"]).resolve() == produit
    assert '--sortie "$SORTIE/produit"' in construire
    assert 'SORTIE="$RACINE/.maestro/distribution"' in construire
    main = (COQUE / "main.js").read_text(encoding="utf-8")
    assert re.search(rf"process\.resourcesPath,\s*'{ressource['to']}'", main)
    assert f"resources/{ressource['to']}/" == assembleur.SOUS_L_INSTALLATION


def test_l_assemblage_pose_la_version_que_le_produit_installe_relit(
    assembleur: ModuleType, tmp_path: Path
) -> None:
    """#1622 : la version du paquet, écrite à la racine du produit — là où l'API installée
    la relit pour les Paramètres, et où la coque la compare à la sienne."""
    from maestro.installation import FICHIER_VERSION, version_du_depot, version_du_produit

    produit = tmp_path / "produit"
    produit.mkdir()
    attendue = version_du_depot(RACINE)

    assert assembleur.poser_version(RACINE, produit) == attendue
    assert (produit / FICHIER_VERSION).is_file()
    assert version_du_produit(produit, "installation").version == attendue
    assert "poser_version(depot, sortie)" in ASSEMBLER.read_text(encoding="utf-8")


def test_un_depot_sans_version_arrete_l_assemblage(assembleur: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "apps" / "desktop").mkdir(parents=True)
    (tmp_path / "apps" / "desktop" / "package.json").write_text("{}", encoding="utf-8")
    with pytest.raises(assembleur.Refus, match="version"):
        assembleur.poser_version(tmp_path, tmp_path)


def test_l_installeur_ne_demande_aucun_droit_d_administrateur() -> None:
    nsis = json.loads((COQUE / "package.json").read_text(encoding="utf-8"))["build"]["nsis"]
    assert nsis["perMachine"] is False


def test_l_installeur_sauve_l_ancien_core_la_ou_la_reprise_le_cherche() -> None:
    """Le crochet NSIS et `maestro.lanceur.reprise` se parlent par des noms : réécrits d'un seul
    côté, une mise à jour effacerait les données d'une 0.1.0 sans que rien ne les ait copiées."""
    from maestro.emplacements import NOM_ENV, NOM_ETAT, NOM_LIVREE, NOM_REPRISE
    from maestro.lanceur.reprise import TEMOIN

    nsis = json.loads((COQUE / "package.json").read_text(encoding="utf-8"))["build"]["nsis"]
    crochet = COQUE / nsis["include"]
    octets = crochet.read_bytes()
    assert octets.isascii(), "makensis lit le fichier dans le jeu de caractères du poste"
    texte = octets.decode("ascii")
    # Le dossier personnel du produit : `USERPROFILE`, que lisent `Path.home()` et `os.homedir()`.
    assert "ReadEnvStr $0 USERPROFILE" in texte
    copie = rf"$0\{NOM_ETAT}\{NOM_REPRISE}"
    produit = r"$INSTDIR\resources\produit"
    assert "!macro customInit" in texte
    assert f'IfFileExists "{copie}\\{TEMOIN}" maestro_reprise_fin' in texte
    assert f'CopyFiles /SILENT "{produit}\\{NOM_LIVREE}\\*.*" "{copie}\\{NOM_LIVREE}"' in texte
    assert f'CopyFiles /SILENT "{produit}\\{NOM_ENV}" "{copie}\\{NOM_ENV}"' in texte


def test_le_front_se_construit_sans_le_jeton_ni_l_url_du_poste() -> None:
    """Propres au poste qui sert le produit, ils lui arrivent au démarrage (`configPoste.ts`)."""
    construire = CONSTRUIRE.read_text(encoding="utf-8")
    assert "-u NEXT_PUBLIC_MAESTRO_API_JETON" in construire
    assert "-u NEXT_PUBLIC_MAESTRO_API_URL" in construire
    assert "MAESTRO_FRONT_AUTONOME=1" in construire
