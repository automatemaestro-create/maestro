"""L'état de la stack d'une copie de travail — `scripts/controltower/etat-stack.sh` (#1456).

`start.sh` y range le jeton de session, le pid du chien de garde et le profil de la fenêtre
isolée ; il vit sous la racine du jetable (`<temp>/maestro/`, #1454) et **part avec la copie qui
l'a démarrée** — jamais tant que sa stack vit (un port qui écoute, un chien de garde vivant).

Les tests jouent la bibliothèque dans un vrai `bash`, sur un `TMPDIR` à eux : jamais le répertoire
temporaire du poste, que `tests/conftest.py` protège en coupant ce ramassage
(`MAESTRO_RAMASSAGE_ETAT_STACK=0`) — ils le rallument explicitement. Ce qui doit être vivant l'est
pour de vrai : un port ouvert par le test, un `sleep` lancé par le script lui-même.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
LIB = RACINE / "scripts" / "controltower" / "etat-stack.sh"
START_SH = RACINE / "scripts" / "controltower" / "start.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash introuvable")


def _ramasse(temp: Path, preambule: str = "", *, argument: str = "--ancien", **env: str) -> dict:
    """Source la bibliothèque, joue `preambule` puis `etat_stack_ramasser`, rend ses compteurs."""
    script = (
        f'. "{LIB.as_posix()}"\n'
        f"{preambule}\n"
        f"etat_stack_ramasser {argument}\n"
        'printf "retires=%s anciens=%s resistants=%s conserves=%s\\n" '
        '"$ETAT_STACK_RETIRES" "$ETAT_STACK_ANCIENS" "$ETAT_STACK_RESISTANTS" '
        '"$ETAT_STACK_CONSERVES"\n'
    )
    environnement = os.environ.copy()
    environnement.update({"TMPDIR": temp.as_posix(), "MAESTRO_RAMASSAGE_ETAT_STACK": "1"})
    environnement.update(env)
    assert BASH is not None
    acheve = subprocess.run(  # noqa: S603
        [BASH, "-c", script],
        env=environnement,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    derniere = acheve.stdout.strip().splitlines()[-1]
    return {cle: int(valeur) for cle, valeur in (paire.split("=") for paire in derniere.split())}


def _etat(racine: Path, nom: str, *, copie: Path | None = None) -> Path:
    """Un dossier d'état tel que `start.sh` le laisse : un profil, et le témoin de sa copie."""
    dossier = racine / nom
    (dossier / "profil").mkdir(parents=True)
    (dossier / "profil" / "Preferences").write_text("{}", encoding="utf-8")
    if copie is not None:
        (dossier / "copie").write_text(copie.as_posix() + "\n", encoding="utf-8", newline="\n")
    return dossier


@pytest.fixture
def temp(tmp_path: Path) -> Path:
    dossier = tmp_path / "temp"
    (dossier / "maestro").mkdir(parents=True)
    return dossier


@pytest.fixture
def port_ouvert() -> Iterator[int]:
    """Un port qui ÉCOUTE vraiment, le temps du test — ce que `netstat`/`/proc` relèvent."""
    serveur = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    serveur.bind(("127.0.0.1", 0))
    serveur.listen(1)
    try:
        yield serveur.getsockname()[1]
    finally:
        serveur.close()


# --- Où l'état vit --------------------------------------------------------------------------------


def test_start_sh_range_l_etat_sous_la_racine_jetable(tmp_path: Path, temp: Path) -> None:
    """Critère 1 — `start.sh` sait dire, sans rien démarrer, le profil de sa fenêtre : il vit
    dans le dossier d'état, donc sous `<TMPDIR>/maestro/controltower-<api>-<ui>`. Le diagnostic
    ne crée rien."""
    chrome = tmp_path / "bin" / "chrome"
    chrome.parent.mkdir()
    chrome.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
    chrome.chmod(0o755)
    environnement = os.environ.copy()
    environnement.update(
        {
            "TMPDIR": temp.as_posix(),
            "MAESTRO_BROWSER": chrome.as_posix(),
            "MAESTRO_PORT_API": "9123",
            "MAESTRO_PORT_UI": "4123",
        }
    )
    assert BASH is not None
    acheve = subprocess.run(  # noqa: S603
        [BASH, str(START_SH), "--diagnostic-navigateur"],
        cwd=str(RACINE),
        env=environnement,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert acheve.returncode == 0, acheve.stderr
    champs = dict(
        ligne.split(": ", 1) for ligne in acheve.stdout.splitlines() if ": " in ligne
    )
    # Sur la fin du chemin : sous Git Bash, MSYS réécrit `TMPDIR` en chemin POSIX (`/tmp/…`).
    attendu = f"/{tmp_path.name}/temp/maestro/controltower-9123-4123/{champs['marqueur']}"
    assert champs["profil"].endswith(attendu), champs["profil"]
    assert list((temp / "maestro").iterdir()) == []


def test_start_sh_ramasse_au_demarrage_et_temoigne_de_sa_copie() -> None:
    """Critère 3 — le ramassage se joue au DÉMARRAGE seulement : après `arreter_session` (l'ancien
    dossier de cette stack, qu'on vient d'arrêter, part avec les autres), avant que le dossier
    neuf ne naisse avec le témoin de sa copie. Jamais sur `--stop`, qui sort avant."""
    texte = START_SH.read_text(encoding="utf-8")
    arret = texte.index('if [ "$MODE" = "arreter" ]; then\n  echo "Control Tower arrêtée."')
    ramassage = texte.index("etat_stack_ramasser --ancien\n")
    creation = texte.index('mkdir -p "$LOG_DIR" "$ETAT_DIR"\n# Le témoin')
    temoin = texte.index('printf \'%s\\n\' "$RACINE" >"$ETAT_DIR/$ETAT_STACK_TEMOIN"')
    assert texte.index("arreter_session\n\nif [ \"$MODE\" = \"arreter\" ]") < arret
    assert arret < ramassage < creation < temoin
    assert texte.count("etat_stack_ramasser") == 1


# --- Ce qui part ----------------------------------------------------------------------------------


def test_l_ancien_emplacement_part_quand_sa_stack_est_eteinte(temp: Path) -> None:
    """Critère 3 — `<temp>/maestro-controltower-*` sans stack vivante : retiré, profil compris.
    Le reste du répertoire temporaire n'est à personne qu'on connaisse : on n'y touche pas."""
    ancien = _etat(temp, "maestro-controltower-8901-3901")
    vide = temp / "maestro-controltower-8902-3902"
    vide.mkdir()
    voisin = _etat(temp, "maestro-presentation")
    compte = _ramasse(temp)
    assert compte == {"retires": 2, "anciens": 2, "resistants": 0, "conserves": 0}
    assert not ancien.exists() and not vide.exists()
    assert voisin.exists()


def test_l_etat_d_une_copie_disparue_part_avec_elle(tmp_path: Path, temp: Path) -> None:
    """L'état neuf porte le témoin de la copie qui l'a démarré : copie partie, état parti ; copie
    là, état gardé ; sans témoin, il n'est à personne qu'on sache nommer — il reste."""
    copie_vivante = tmp_path / "worktree-vivant"
    copie_vivante.mkdir()
    orphelin = _etat(temp / "maestro", "controltower-8903-3903", copie=tmp_path / "parti")
    garde = _etat(temp / "maestro", "controltower-8904-3904", copie=copie_vivante)
    anonyme = _etat(temp / "maestro", "controltower-8905-3905")
    compte = _ramasse(temp, argument="")
    assert compte == {"retires": 1, "anciens": 0, "resistants": 0, "conserves": 0}
    assert not orphelin.exists()
    assert garde.exists() and anonyme.exists()


# --- Ce qui ne part jamais : une stack vivante ----------------------------------------------------


def test_une_stack_dont_un_port_ecoute_garde_son_etat(
    tmp_path: Path, temp: Path, port_ouvert: int
) -> None:
    """Critère 2 — « jamais celui d'une stack vivante » : un de ses deux ports écoute, son état
    reste, à l'ancien emplacement comme sous la racine jetable, copie disparue ou non."""
    ancien = _etat(temp, f"maestro-controltower-{port_ouvert}-1")
    neuf = _etat(temp / "maestro", f"controltower-1-{port_ouvert}", copie=tmp_path / "parti")
    compte = _ramasse(temp)
    assert compte == {"retires": 0, "anciens": 0, "resistants": 0, "conserves": 2}
    assert ancien.exists() and neuf.exists()


def test_une_stack_dont_le_chien_de_garde_vit_garde_son_etat(tmp_path: Path, temp: Path) -> None:
    """Critère 2 — l'autre témoin de vie : le pid du chien de garde, tel que `start.sh` l'écrit
    (`$!`). Le `sleep` est lancé par le script lui-même — son pid est celui que `kill -0` du
    même shell sait interroger, sous MSYS comme ailleurs."""
    neuf = _etat(temp / "maestro", "controltower-8906-3906", copie=tmp_path / "parti")
    mort = _etat(temp / "maestro", "controltower-8907-3907", copie=tmp_path / "parti")
    preambule = (
        "sleep 60 &\n"
        "chien=$!\n"
        f'printf "%s\\n" "$chien" >"{(neuf / "chien-de-garde.pid").as_posix()}"\n'
        f'printf "999999\\n" >"{(mort / "chien-de-garde.pid").as_posix()}"\n'
        "trap 'kill \"$chien\" 2>/dev/null' EXIT"
    )
    compte = _ramasse(temp, preambule, argument="")
    assert compte == {"retires": 1, "anciens": 0, "resistants": 0, "conserves": 1}
    assert neuf.exists()
    assert not mort.exists()


def test_des_ports_illisibles_valent_une_stack_vivante(temp: Path) -> None:
    """Le doute garde : un nom dont on ne lit pas les ports n'est jamais jugé éteint."""
    bizarre = _etat(temp, "maestro-controltower-demo")
    compte = _ramasse(temp)
    assert compte["conserves"] == 1 and compte["retires"] == 0
    assert bizarre.exists()


def test_le_ramassage_s_eteint_par_sa_variable(temp: Path) -> None:
    """Critère 3 — éteignable sur le patron du dépôt : `MAESTRO_RAMASSAGE_ETAT_STACK=0`."""
    ancien = _etat(temp, "maestro-controltower-8908-3908")
    compte = _ramasse(temp, MAESTRO_RAMASSAGE_ETAT_STACK="0")
    assert compte == {"retires": 0, "anciens": 0, "resistants": 0, "conserves": 0}
    assert ancien.exists()
