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

import functools
import http.server
import importlib.util
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
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
    assert "poser_version(depot, sortie, version)" in ASSEMBLER.read_text(encoding="utf-8")


def test_une_version_demandee_va_jusqu_au_produit(assembleur: ModuleType, tmp_path: Path) -> None:
    """#644 : `construire.sh --version` fait deux paquets du même code. La version demandée doit
    arriver dans `version.json` — celle que l'API et la coque relisent —, pas seulement dans le nom
    de l'installeur (le premier paquet construit ainsi disait encore celle de package.json)."""
    from maestro.installation import FICHIER_VERSION

    assert assembleur.poser_version(RACINE, tmp_path, " 9.8.7 ") == "9.8.7"
    assert json.loads((tmp_path / FICHIER_VERSION).read_text(encoding="utf-8")) == {
        "version": "9.8.7"
    }
    assert "version=arguments.version" in ASSEMBLER.read_text(encoding="utf-8")
    with pytest.raises(assembleur.Refus, match="vide"):
        assembleur.poser_version(RACINE, tmp_path, "  ")


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


# ------------------------------------------------------------------ la mise à jour (#644)

PUBLIER = RACINE / "scripts" / "distribution" / "publier.sh"
VERIFIER = RACINE / "scripts" / "distribution" / "verifier.sh"
BASH = shutil.which("bash")
sans_bash = pytest.mark.skipif(BASH is None, reason="bash introuvable")


def _jouer(script: Path, *argv: str, cwd: Path, env: dict[str, str] | None = None):
    assert BASH is not None
    return subprocess.run(
        [BASH, str(script), *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )


@sans_bash
@pytest.mark.parametrize(
    ("argv", "motif"),
    [
        (["--version", "1.2"], "trois nombres"),
        (["--version", "v1.2.3"], "trois nombres"),
        (["--version"], "attend une valeur"),
        (["--flux", "ailleurs", "--produit"], "ne va pas avec --produit"),
    ],
)
def test_construire_refuse_une_version_ou_un_canal_mal_formes(argv: list[str], motif: str) -> None:
    """Refusé avant toute étape — et avant la plateforme : l'usage se dit partout pareil. Le canal
    compare des versions x.y.z ; une autre forme y passerait pour plus ancienne ou plus récente."""
    sortie = _jouer(CONSTRUIRE, *argv, cwd=RACINE)
    assert sortie.returncode == 2, sortie.stderr
    assert motif in sortie.stderr


def test_construire_donne_la_version_demandee_au_produit_et_a_la_coque() -> None:
    construire = CONSTRUIRE.read_text(encoding="utf-8")
    assert '${VERSION:+--version "$VERSION"}' in construire
    assert '${VERSION:+"-c.extraMetadata.version=$VERSION"}' in construire
    assert 'cp "$SORTIE/paquet/latest.yml" "$INSTALLEUR" "$FLUX/"' in construire


def test_la_publication_n_est_jamais_celle_de_la_ci() -> None:
    """Ce qui part s'installe chez chacun : c'est un geste humain, depuis main (docs/54 §6.3)."""
    for workflow in (RACINE / ".github" / "workflows").glob("*.y*ml"):
        texte = workflow.read_text(encoding="utf-8")
        assert "publier.sh" not in texte, workflow.name
        assert "gh release create" not in texte, workflow.name


def test_la_verification_du_paquet_sert_le_canal_que_la_coque_lit() -> None:
    """La variable de la coque, éteinte hors de la phase de mise à jour, et le cache que le
    désinstalleur emporte : trois fichiers qui doivent dire le même nom."""
    verifier = VERIFIER.read_text(encoding="utf-8")
    main = (COQUE / "main.js").read_text(encoding="utf-8")
    construction = json.loads((COQUE / "package.json").read_text(encoding="utf-8"))["build"]
    nom = construction["extraMetadata"]["name"]

    assert "const VARIABLE_FLUX = 'MAESTRO_MISES_A_JOUR_FLUX';" in main
    assert 'MAESTRO_MISES_A_JOUR_FLUX="$FLUX_COQUE"' in verifier
    assert "\nFLUX_COQUE=0\n" in verifier
    assert f'CACHE_DES_MISES_A_JOUR="$PROFIL/AppData/Local/{nom}-updater"' in verifier
    assert f"updaterCacheDirName: {nom}-updater" in verifier
    # La ligne que le script guette est celle que la coque écrit.
    assert "direDeLaMiseAJour('stdout', `prête : ${info.version}`);" in main
    assert '"[mise à jour] prête : $VERSION_SUIVANTE"' in verifier


def test_la_verification_lit_le_dialogue_et_la_pose_que_la_coque_ecrit() -> None:
    """Le script presse « Plus tard » puis juge la pose sur le journal de la coque : les textes
    qu'il cherche sont ceux que `main.js` montre et écrit, sans quoi il échouerait sur un paquet
    juste, ou passerait sur un dialogue qui ne dit plus ce que coûte le redémarrage."""
    verifier = VERIFIER.read_text(encoding="utf-8")
    main = (COQUE / "main.js").read_text(encoding="utf-8")

    assert "buttons: ['Redémarrer maintenant', 'Plus tard']" in main
    assert "presser \"$PID_FENETRE\" 'Plus*'" in verifier
    assert "message: `Maestro ${version} est prête à s'installer.`" in main
    assert "texte : Maestro $VERSION_SUIVANTE est prête à s'installer." in verifier
    assert "un run en cours \" +\n        \"s'arrête avec lui" in main
    assert "un run en cours s'arrête avec lui" in verifier
    pose = "`installation de ${versionPrete}${relancer ? ', puis redémarrage' : ', en silence'}`"
    assert pose in main
    assert '"[mise à jour] installation de $VERSION_SUIVANTE, en silence"' in verifier
    assert "redémarrage demandé sur ${version}" in main
    assert "grep -qF 'redémarrage demandé'" in verifier


DIALOGUE = RACINE / "scripts" / "distribution" / "dialogue.sh"


def test_le_dialogue_se_presse_sans_la_souris() -> None:
    """Un clic aux coordonnées du bouton tombe dans la fenêtre qui le couvre, ou se perd quand
    Windows refuse le premier plan (#1647). Le bouton se presse par la notification qu'il enverrait
    à son parent sous un clic (`WM_COMMAND`, `BN_CLICKED`) : ni curseur, ni premier plan, ni écran.
    Un bouton sans fenêtre ne se presse pas, et la vérification le dit."""
    for script in (VERIFIER, DIALOGUE):
        texte = script.read_text(encoding="utf-8")
        for geste in ("mouse_event", "SetCursorPos", "SetForegroundWindow", "Cursor]::Position"):
            assert geste not in texte, f"{script.name} touche à l'écran : {geste}"
    presser = DIALOGUE.read_text(encoding="utf-8")
    assert "GetParent(" in presser
    assert "0x0111" in presser  # WM_COMMAND, wParam = MAKEWPARAM(id, BN_CLICKED = 0)
    assert "'bouton sans fenetre : '" in presser
    assert '. "$RACINE/scripts/distribution/dialogue.sh"' in VERIFIER.read_text(encoding="utf-8")


CANAL = RACINE / "scripts" / "distribution" / "canal.sh"
PILOTE = RACINE / "scripts" / "orchestrate" / "pilote.sh"


def _port_libre() -> int:
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        return prise.getsockname()[1]


def _repond(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def _arreter_ce_qui_sert(dossier: Path) -> None:
    """Le filet du test, qui ne doit rien au code éprouvé : tout processus dont la ligne de
    commande nomme le dossier servi. Sans lui, un test rouge laisserait un serveur au poste."""
    if os.name == "nt":
        powershell = shutil.which("powershell") or "powershell.exe"
        filtre = (
            "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
            f"$_.CommandLine.Contains('{dossier}') -and "
            "-not $_.CommandLine.Contains('Get-CimInstance') } | ForEach-Object { "
            "Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", filtre],
            capture_output=True,
            timeout=60,
        )
        return
    for ligne in Path("/proc").glob("[0-9]*/cmdline"):
        pid = int(ligne.parent.name)
        try:
            if pid != os.getpid() and str(dossier).encode() in ligne.read_bytes():
                os.kill(pid, signal.SIGKILL)
        except OSError:
            continue


def _jouer_le_canal(script: str, **variables: str) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    entete = f'. "{PILOTE.as_posix()}"\n. "{CANAL.as_posix()}"\n'
    return subprocess.run(
        [BASH, "-c", entete + script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, **variables},
        timeout=120,
    )


def test_la_verification_arrete_le_canal_qu_elle_a_servi() -> None:
    """Le canal se sert et s'arrête par `canal.sh`, à la fin de la phase comme à toute sortie, et
    l'arrêt est un constat du rapport : il ne se suppose plus."""
    verifier = VERIFIER.read_text(encoding="utf-8")
    assert '. "$RACINE/scripts/orchestrate/pilote.sh"' in verifier
    assert '. "$RACINE/scripts/distribution/canal.sh"' in verifier
    assert 'canal_en_vol "$TRACE" && canal_arreter "$PORT_FLUX" "$TRACE"' in verifier
    arret = '"le canal local est arrêté : plus rien ne répond sur http://127.0.0.1:$PORT_FLUX/"'
    assert arret in verifier
    assert "http.server" not in verifier


@sans_bash
def test_le_canal_s_arrete_avec_le_serveur_qui_ecoute(tmp_path: Path) -> None:
    """Le serveur du canal est lancé par l'interpréteur du venv. Sous Windows, ce `python.exe` est
    un lanceur : celui qui écoute est son enfant Windows, que `kill` n'atteint pas (#1644). Le
    canal arrêté, plus rien ne répond sur son port."""
    dossier = tmp_path / "canal"
    _ecrire(dossier / "latest.yml", "version: 0.0.1\n")
    trace = tmp_path / "trace"
    trace.mkdir()
    port = _port_libre()
    try:
        sortie = _jouer_le_canal(
            'canal_servir "$PY" "$PORT" "$DOSSIER" "$TRACE"; echo "servir=$?"\n'
            'canal_arreter "$PORT" "$TRACE"; echo "arreter=$?"\n',
            PY=sys.executable,
            PORT=str(port),
            DOSSIER=str(dossier),
            TRACE=trace.as_posix(),
        )
        reste = _repond(port)
    finally:
        _arreter_ce_qui_sert(dossier)
    assert "servir=0" in sortie.stdout, sortie.stdout + sortie.stderr
    assert "arreter=0" in sortie.stdout, sortie.stdout + sortie.stderr
    assert not reste, f"le port {port} répond encore après l'arrêt du canal"


@sans_bash
def test_le_canal_refuse_un_port_qu_un_autre_serveur_sert_deja(tmp_path: Path) -> None:
    """Un serveur resté d'une vérification tuée servirait l'ANCIEN `latest.yml` : le canal ne le
    prend pas pour le sien, le dit (`3`) et n'y touche pas."""
    ancien = tmp_path / "ancien"
    _ecrire(ancien / "latest.yml", "version: 0.0.0\n")
    nouveau = tmp_path / "nouveau"
    _ecrire(nouveau / "latest.yml", "version: 0.0.1\n")
    trace = tmp_path / "trace"
    trace.mkdir()
    gestionnaire = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ancien))
    serveur = http.server.ThreadingHTTPServer(("127.0.0.1", 0), gestionnaire)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    port = serveur.server_address[1]
    try:
        sortie = _jouer_le_canal(
            'canal_servir "$PY" "$PORT" "$DOSSIER" "$TRACE"; echo "servir=$?"\n',
            PY=sys.executable,
            PORT=str(port),
            DOSSIER=str(nouveau),
            TRACE=trace.as_posix(),
        )
        assert "servir=3" in sortie.stdout, sortie.stdout + sortie.stderr
        assert _repond(port), "le serveur déjà là a été arrêté"
    finally:
        serveur.shutdown()
        serveur.server_close()
        _arreter_ce_qui_sert(nouveau)


VERSION_PUBLIEE = "1.2.3"


def _ecrire(chemin: Path, texte: str) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(texte, encoding="utf-8")


def _empreinte(chemin: Path) -> str:
    """Le `sha512` de `latest.yml` : l'empreinte binaire, en base64 — mesurée ici sans openssl."""
    import base64
    import hashlib

    return base64.b64encode(hashlib.sha512(chemin.read_bytes()).digest()).decode("ascii")


@pytest.fixture
def depot_a_publier(tmp_path: Path) -> dict[str, object]:
    """Un dépôt sur `main`, propre et à jour de son `origin`, avec un paquet construit — et un `gh`
    factice qui consigne ce qu'on lui demande : aucun appel ne quitte le poste."""
    if shutil.which("git") is None or shutil.which("openssl") is None:
        pytest.skip("git et openssl sont requis")
    depot = tmp_path / "depot"
    script = depot / "scripts" / "distribution" / "publier.sh"
    _ecrire(script, PUBLIER.read_text(encoding="utf-8"))
    paquet_npm = {"name": "desktop", "version": VERSION_PUBLIEE, "dependencies": {"x": "6.8.10"}}
    _ecrire(depot / "apps" / "desktop" / "package.json", json.dumps(paquet_npm, indent=2) + "\n")
    _ecrire(depot / ".gitignore", ".maestro/\n")
    origine = tmp_path / "origine.git"
    _git(tmp_path, "init", "--bare", "-q", str(origine))
    _git(tmp_path, "init", "-q", "-b", "main", str(depot))
    _git(depot, "add", "-A")
    identite = ("-c", "user.name=essai", "-c", "user.email=essai@exemple.invalid")
    _git(depot, *identite, "commit", "-qm", "paquet")
    _git(depot, "remote", "add", "origin", str(origine))
    _git(depot, "push", "-q", "origin", "main")

    paquet = depot / ".maestro" / "distribution" / "paquet"
    installeur = paquet / f"Maestro-{VERSION_PUBLIEE}-installation-x64.exe"
    _ecrire(installeur, "octets de l'installeur\n")
    empreinte = _empreinte(installeur)
    _ecrire(
        paquet / "latest.yml",
        f"version: {VERSION_PUBLIEE}\nfiles:\n  - url: {installeur.name}\n"
        f"    sha512: {empreinte}\npath: {installeur.name}\nsha512: {empreinte}\n",
    )

    outils = tmp_path / "outils"
    journal = tmp_path / "gh.log"
    _ecrire(
        outils / "gh",
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >>"{journal.as_posix()}"\n'
        '[ "$1 $2" = "release view" ] && exit "${GH_VUE:-1}"\n'
        "exit 0\n",
    )
    (outils / "gh").chmod(0o755)
    env = {**os.environ, "PATH": f"{outils}{os.pathsep}{os.environ['PATH']}"}
    return {"depot": depot, "script": script, "env": env, "journal": journal, "paquet": paquet}


def _publier(contexte: dict[str, object], *argv: str, **env: str):
    return _jouer(
        contexte["script"],  # type: ignore[arg-type]
        *argv,
        cwd=contexte["depot"],  # type: ignore[arg-type]
        env={**contexte["env"], **env},  # type: ignore[dict-item]
    )


def _demandes(contexte: dict[str, object]) -> list[str]:
    journal = contexte["journal"]
    assert isinstance(journal, Path)
    return journal.read_text(encoding="utf-8").splitlines() if journal.exists() else []


@sans_bash
def test_la_publication_part_de_main_et_de_main_seulement(depot_a_publier) -> None:
    _git(depot_a_publier["depot"], "switch", "-q", "-c", "feat/1-essai")
    sortie = _publier(depot_a_publier, "--essai", "--sans-construire")
    assert sortie.returncode == 4
    assert "depuis main" in sortie.stderr
    assert _demandes(depot_a_publier) == []


@sans_bash
def test_la_publication_refuse_un_arbre_sale(depot_a_publier) -> None:
    _ecrire(depot_a_publier["depot"] / "apps" / "desktop" / "main.js", "// non commité\n")
    sortie = _publier(depot_a_publier, "--essai", "--sans-construire")
    assert sortie.returncode == 4
    assert "non commités" in sortie.stderr


@sans_bash
def test_une_version_ne_se_publie_qu_une_fois(depot_a_publier) -> None:
    sortie = _publier(depot_a_publier, "--sans-construire", GH_VUE="0")
    assert sortie.returncode == 4
    assert f"v{VERSION_PUBLIEE} est déjà publiée" in sortie.stderr
    assert not any(d.startswith("release create") for d in _demandes(depot_a_publier))


@sans_bash
@pytest.mark.parametrize(
    ("alteration", "motif"),
    [
        ("version", "le paquet n'est pas celui de cette version"),
        ("installeur", "l'empreinte"),
        ("absent", "aucun latest.yml"),
    ],
)
def test_un_paquet_qui_ne_dit_pas_cette_version_ne_part_pas(
    depot_a_publier, alteration: str, motif: str
) -> None:
    paquet = depot_a_publier["paquet"]
    assert isinstance(paquet, Path)
    canal = paquet / "latest.yml"
    if alteration == "version":
        canal.write_text(canal.read_text(encoding="utf-8").replace("1.2.3", "1.2.4", 1), "utf-8")
    elif alteration == "installeur":
        (paquet / f"Maestro-{VERSION_PUBLIEE}-installation-x64.exe").write_text("autre\n", "utf-8")
    else:
        canal.unlink()
    sortie = _publier(depot_a_publier, "--sans-construire")
    assert sortie.returncode == 4
    assert motif in sortie.stderr
    assert not any(d.startswith("release create") for d in _demandes(depot_a_publier))


@sans_bash
def test_l_essai_dit_ce_qui_partirait_sans_rien_envoyer(depot_a_publier) -> None:
    sortie = _publier(depot_a_publier, "--essai", "--sans-construire")
    assert sortie.returncode == 0, sortie.stderr
    assert f"v{VERSION_PUBLIEE}" in sortie.stdout
    assert "rien n'est envoyé" in sortie.stdout
    assert _demandes(depot_a_publier) == [f"release view v{VERSION_PUBLIEE}"]


@sans_bash
def test_la_publication_envoie_l_installeur_et_le_canal_sur_la_tete_de_main(
    depot_a_publier,
) -> None:
    depot = depot_a_publier["depot"]
    assert isinstance(depot, Path)
    tete = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=depot, capture_output=True, text=True, check=True
    ).stdout.strip()
    sortie = _publier(depot_a_publier, "--sans-construire")
    assert sortie.returncode == 0, sortie.stderr

    (creation,) = [d for d in _demandes(depot_a_publier) if d.startswith("release create")]
    assert creation.startswith(f"release create v{VERSION_PUBLIEE} ")
    assert f"Maestro-{VERSION_PUBLIEE}-installation-x64.exe" in creation
    assert "latest.yml" in creation
    assert f"--target {tete}" in creation
    assert "--latest" in creation
