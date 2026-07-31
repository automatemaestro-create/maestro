"""Tests du choix de navigateur du lanceur Control Tower — `scripts/controltower/start.sh` (#200).

**Ni navigateur, ni stack, ni réseau.** Le script sait dire, sans rien démarrer ni ouvrir, QUEL
navigateur il ouvrirait et COMMENT : c'est le sous-commande `--diagnostic-navigateur`. On l'exerce
ici sur toutes les branches de résolution, en pilotant la détection par l'échappatoire
`MAESTRO_BROWSER_DEFAUT` (qui court-circuite la lecture de l'association système) — d'où des tests
**déterministes et multiplateformes**, qui ne touchent jamais au registre Windows, à `xdg-settings`
ni à LaunchServices, et n'ouvrent aucune fenêtre.

Ce qu'on vérifie, ce sont les **critères d'acceptation** de #200 :
- le navigateur **par défaut du poste** est lu à chaud (Chromium ⇒ fenêtre isolée + arrêt auto) ;
- `MAESTRO_BROWSER` **prime** sur cette détection ;
- un défaut **hors famille Chromium** (Firefox, Safari) ne casse rien : repli annoncé, limite dite ;
- l'**indépendance** de deux sessions parallèles (profil/marqueur indexés sur les ports, #152).

L'adaptateur Linux réel (`xdg-settings`) est couvert à part, en le mockant par le `PATH` — comme
[`test_clean_runner_containers.py`](test_clean_runner_containers.py) le fait pour `docker` — et
sauté hors Linux (le poste des autres OS lirait sa vraie association système).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
SCRIPT = RACINE / "scripts" / "controltower" / "start.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash introuvable")

# Les surcharges du poste (bloc env de settings.local.json, ou un vrai MAESTRO_BROWSER) ne doivent
# pas déteindre sur les scénarios : on les retire, chaque test posant les siennes.
_A_NETTOYER = ("MAESTRO_BROWSER", "MAESTRO_BROWSER_DEFAUT", "MAESTRO_PORT_API", "MAESTRO_PORT_UI")


def diagnostic(env_extra: dict[str, str] | None = None) -> dict[str, str]:
    """Lance `start.sh --diagnostic-navigateur` et rend ses lignes `clé: valeur` en dict."""
    environnement = os.environ.copy()
    for cle in _A_NETTOYER:
        environnement.pop(cle, None)
    environnement.update(env_extra or {})
    assert BASH is not None
    acheve = subprocess.run(  # noqa: S603
        [BASH, str(SCRIPT), "--diagnostic-navigateur"],
        cwd=str(RACINE),
        env=environnement,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert acheve.returncode == 0, acheve.stderr
    champs: dict[str, str] = {}
    for ligne in acheve.stdout.splitlines():
        if ":" in ligne:
            cle, _, valeur = ligne.partition(":")
            champs[cle.strip()] = valeur.strip()
    return champs


def faux_navigateur(dossier: Path, nom: str) -> str:
    """Crée un exécutable bidon (nommé `nom`) et rend son chemin POSIX (invocable en Git Bash)."""
    dossier.mkdir(parents=True, exist_ok=True)
    binaire = dossier / nom
    binaire.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
    binaire.chmod(0o755)
    return binaire.as_posix()


# ------------------------------------------------ Navigateur par défaut du poste (critère 1)


def test_defaut_chromium_ouvre_une_fenetre_isolee(tmp_path: Path) -> None:
    """Défaut Chromium du poste (ici son exécutable exact) ⇒ fenêtre isolée + arrêt auto."""
    chrome = faux_navigateur(tmp_path / "bin", "chrome")
    champs = diagnostic({"MAESTRO_BROWSER_DEFAUT": chrome})

    assert champs["famille"] == "chromium"
    assert champs["mode"] == "isole"  # profil jetable + chien de garde
    assert champs["source"] == "defaut-os"  # lu depuis le défaut, pas un binaire codé en dur
    assert champs["cible"] == chrome


def test_le_defaut_est_relu_a_chaud_pas_code_en_dur(tmp_path: Path) -> None:
    """Changer le défaut du poste change la cible, sans toucher au code (Edge puis Brave)."""
    edge = faux_navigateur(tmp_path / "a", "msedge")
    brave = faux_navigateur(tmp_path / "b", "brave")

    assert diagnostic({"MAESTRO_BROWSER_DEFAUT": edge})["cible"] == edge
    apres = diagnostic({"MAESTRO_BROWSER_DEFAUT": brave})
    assert apres["cible"] == brave
    assert apres["famille"] == "chromium"


# ------------------------------------------------ MAESTRO_BROWSER prime (critère 1)


def test_maestro_browser_prime_sur_la_detection(tmp_path: Path) -> None:
    """MAESTRO_BROWSER l'emporte, même quand le défaut du poste est autre chose."""
    impose = faux_navigateur(tmp_path / "impose", "chrome")
    champs = diagnostic({
        "MAESTRO_BROWSER": impose,
        "MAESTRO_BROWSER_DEFAUT": "firefox.desktop",  # ignoré : MAESTRO_BROWSER prime
    })

    assert champs["source"] == "MAESTRO_BROWSER"
    assert champs["mode"] == "isole"
    assert champs["cible"] == impose


def test_maestro_browser_introuvable_est_dit(tmp_path: Path) -> None:
    """Un MAESTRO_BROWSER pointant dans le vide n'ouvre rien en douce : il le dit."""
    champs = diagnostic({"MAESTRO_BROWSER": str(tmp_path / "nexiste-pas" / "chrome")})

    assert champs["mode"] == "aucun"
    assert champs["source"] == "MAESTRO_BROWSER"
    assert "introuvable" in champs["message"]


# ------------------------------------------------ Défaut hors Chromium (critère 4)


@pytest.mark.parametrize("defaut", ["firefox.desktop", "firefox", "org.mozilla.firefox"])
def test_defaut_hors_chromium_repli_annonce(defaut: str) -> None:
    """Firefox par défaut : le lancement ne casse pas, la limite (pas d'arrêt auto) est dite."""
    champs = diagnostic({"MAESTRO_BROWSER_DEFAUT": defaut})

    assert champs["famille"] == "autre"
    assert champs["mode"] == "defaut"  # ouverture dans le défaut via l'ouvreur système
    assert champs["source"] == "defaut-os"
    assert "hors famille Chromium" in champs["message"]
    assert "sans" in champs["message"]  # « sans profil jetable ni arrêt automatique »


def test_safari_est_hors_chromium() -> None:
    champs = diagnostic({"MAESTRO_BROWSER_DEFAUT": "com.apple.safari"})
    assert champs["famille"] == "autre" and champs["mode"] == "defaut"


# ------------------------------------------------ Repli quand le binaire du défaut échappe


def test_defaut_chromium_sans_binaire_replie_sur_un_chromium_installe(tmp_path: Path) -> None:
    """Un progId Chromium (ChromeHTML) sans exécutable localisable ⇒ repli sur un Chromium présent.

    On garantit qu'un Chromium existe en en plaçant un bidon (`google-chrome`) en tête du `PATH` ;
    sur un poste qui a déjà Chrome/Edge, c'est celui-là qui serait pris — le verdict (mode isolé,
    source « repli-chromium ») ne dépend pas duquel.
    """
    fauxbin = tmp_path / "bin"
    faux_navigateur(fauxbin, "google-chrome")
    env = {
        "MAESTRO_BROWSER_DEFAUT": "ChromeHTML",  # famille Chromium, mais pas un binaire
        "PATH": os.pathsep.join([str(fauxbin), os.environ.get("PATH", "")]),
    }
    champs = diagnostic(env)

    assert champs["famille"] == "chromium"
    assert champs["mode"] == "isole"
    assert champs["source"] == "repli-chromium"


# ------------------------------------------------ Indépendance des sessions parallèles (critère 3)


def test_profil_et_marqueur_indexes_sur_les_ports(tmp_path: Path) -> None:
    """Deux sessions sur des ports différents ⇒ profils et marqueurs distincts (worktrees, #152)."""
    chrome = faux_navigateur(tmp_path / "bin", "chrome")
    a = diagnostic(
        {"MAESTRO_BROWSER": chrome, "MAESTRO_PORT_API": "8000", "MAESTRO_PORT_UI": "3000"}
    )
    b = diagnostic(
        {"MAESTRO_BROWSER": chrome, "MAESTRO_PORT_API": "9000", "MAESTRO_PORT_UI": "4000"}
    )

    assert "8000-3000" in a["marqueur"] and "9000-4000" in b["marqueur"]
    assert a["marqueur"] != b["marqueur"]
    assert a["profil"] != b["profil"]
    # Le profil de la fenêtre est jetable et dédié — jamais un profil personnel (critère 2).
    assert a["marqueur"] in a["profil"]


# ------------------------------------------------ Adaptateur Linux réel (xdg-settings)


@pytest.mark.skipif(sys.platform != "linux", reason="lit la vraie association système hors Linux")
def test_adaptateur_linux_lit_xdg_settings(tmp_path: Path) -> None:
    """Sans MAESTRO_BROWSER_DEFAUT, le poste Linux lit `xdg-settings` — ici mocké sur Firefox."""
    fauxbin = tmp_path / "bin"
    fauxbin.mkdir()
    shim = fauxbin / "xdg-settings"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1 $2" = "get default-web-browser" ]; then echo firefox.desktop; fi\n',
        encoding="utf-8",
        newline="\n",
    )
    shim.chmod(0o755)
    champs = diagnostic({"PATH": os.pathsep.join([str(fauxbin), os.environ.get("PATH", "")])})

    assert champs["famille"] == "autre"
    assert champs["mode"] == "defaut"
    assert champs["source"] == "defaut-os"
