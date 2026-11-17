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
    # L'appel, pas sa contiguïté : entre les deux se dit un arrêt qui laisse répondre (#1579).
    assert texte.count("\narreter_session\n") == 1
    assert texte.index("\narreter_session\n") < arret
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


# --- La table des écoutes (#1696) -----------------------------------------------------------------
# Sous Windows, la table du système qui porte les écoutes (`ecoutes.py`), pas `netstat`, qui tait
# celles qu'un poste range dans un état 0 ; ailleurs `/proc`, puis `lsof`. Une lecture impossible
# retombe sur `netstat`, le dit, et ne fait jamais mourir une stack.


def _executable(chemin: Path, corps: str) -> Path:
    """Un exécutable bash, là où le test le veut."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(f"#!/usr/bin/env bash\n{corps}\n", encoding="utf-8", newline="\n")
    chemin.chmod(0o755)
    return chemin


def _joue(script: str, *, chemin: Path | None = None, **env: str) -> list[str]:
    """Source la bibliothèque puis joue `script` dans un vrai `bash` ; rend ses lignes."""
    environnement = os.environ.copy()
    environnement.pop("MAESTRO_TABLE_DES_ECOUTES", None)
    environnement["MAESTRO_RAMASSAGE_ETAT_STACK"] = "1"
    if chemin is not None:
        environnement["PATH"] = str(chemin) + os.pathsep + environnement.get("PATH", "")
    environnement.update(env)
    assert BASH is not None
    acheve = subprocess.run(  # noqa: S603
        [BASH, "-c", f'. "{LIB.as_posix()}"\n{script}'],
        env=environnement,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    return acheve.stdout.splitlines()


def _repli(tmp_path: Path, netstat: str) -> dict[str, str]:
    """Une table refusée, et un `netstat` à la place du vrai : ce qu'il imprime, ou son refus."""
    refus = _executable(tmp_path / "refus", 'echo "appel refusé" >&2\nexit 1')
    _executable(tmp_path / "bin" / "netstat", netstat)
    return {"MAESTRO_TABLE_DES_ECOUTES": refus.as_posix()}


def test_la_table_des_ecoutes_nomme_le_port_qui_ecoute_et_son_pid(port_ouvert: int) -> None:
    """Critère 1 — la table du système porte l'écoute du test, avec le pid qui la tient : celui
    de ce processus. Sous Windows, c'est ce que `netstat` taisait sur ce poste ; dans le
    conteneur, `/proc` le dit sans `lsof`."""
    lignes = _joue('etat_stack_ecoutes; echo "code=$?"\nprintf "%s\\n" "$ETAT_STACK_ECOUTES"')
    assert lignes[0] == "code=0"
    assert f"{port_ouvert} {os.getpid()}" in lignes[1:]


def test_une_table_illisible_retombe_sur_netstat_le_dit_et_garde_l_etat(
    tmp_path: Path, temp: Path
) -> None:
    """Critère 2 — la table refusée, on lit `netstat` et on le dit. Ce qu'il voit écouter vit ;
    ce qu'il ne voit pas n'est pas mort pour autant : son silence ne prouve rien (#1579)."""
    vue = _etat(temp / "maestro", "controltower-8912-3912", copie=tmp_path / "parti")
    muette = _etat(temp / "maestro", "controltower-8913-3913", copie=tmp_path / "parti")
    env = _repli(
        tmp_path,
        "printf '  TCP    0.0.0.0:8912     0.0.0.0:0     LISTENING       4242\\r\\n'",
    )
    lignes = _joue(
        'etat_stack_ecoutes; echo "code=$?"\nprintf "%s\\n" "$ETAT_STACK_ECOUTES"\n'
        "etat_stack_ramasser\n"
        'printf "conserves=%s retires=%s\\n" "$ETAT_STACK_CONSERVES" "$ETAT_STACK_RETIRES"\n'
        'printf "avis=%s\\n" "$ETAT_STACK_PORTS_AVIS"',
        chemin=tmp_path / "bin",
        TMPDIR=temp.as_posix(),
        **env,
    )
    assert lignes[:2] == ["code=2", "8912 4242"]
    assert "conserves=2 retires=0" in lignes
    avis = next(ligne for ligne in lignes if ligne.startswith("avis="))
    assert "illisible (appel refusé, code 1)" in avis
    assert "repli sur netstat" in avis
    assert vue.exists() and muette.exists()


def test_sans_table_ni_netstat_aucune_ecoute_ne_se_lit_et_rien_ne_part(
    tmp_path: Path, temp: Path
) -> None:
    """Critère 2 — rien ne se lit : on le dit, et on ne conclut jamais qu'aucun port n'écoute."""
    etat = _etat(temp / "maestro", "controltower-8914-3914", copie=tmp_path / "parti")
    lignes = _joue(
        'etat_stack_ecoutes; echo "code=$?"\n'
        "etat_stack_ramasser\n"
        'printf "conserves=%s retires=%s\\n" "$ETAT_STACK_CONSERVES" "$ETAT_STACK_RETIRES"\n'
        'printf "avis=%s\\n" "$ETAT_STACK_PORTS_AVIS"',
        chemin=tmp_path / "bin",
        TMPDIR=temp.as_posix(),
        **_repli(tmp_path, "exit 1"),
    )
    assert lignes[0] == "code=1"
    assert "conserves=1 retires=0" in lignes
    assert "netstat ne répond pas : aucune écoute ne se lit" in lignes[-1]
    assert etat.exists()


def test_une_table_lue_et_vide_fait_partir_l_etat_d_une_stack_eteinte(
    tmp_path: Path, temp: Path
) -> None:
    """La table lue fait foi, vide comprise : la stack éteinte dont la copie est partie s'en va,
    et rien n'est dit."""
    etat = _etat(temp / "maestro", "controltower-8915-3915", copie=tmp_path / "parti")
    vide = _executable(tmp_path / "vide", "exit 0")
    lignes = _joue(
        "etat_stack_ramasser\n"
        'printf "retires=%s avis=%s\\n" "$ETAT_STACK_RETIRES" "$ETAT_STACK_PORTS_AVIS"',
        TMPDIR=temp.as_posix(),
        MAESTRO_TABLE_DES_ECOUTES=vide.as_posix(),
    )
    assert lignes == ["retires=1 avis="]
    assert not etat.exists()
