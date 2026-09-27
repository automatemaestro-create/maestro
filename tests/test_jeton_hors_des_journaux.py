"""Le jeton d'API ne s'écrit dans aucun journal de l'API (ticket #1292).

Un navigateur ne pose aucun en-tête sur une poignée de main WebSocket : le jeton
y voyage en paramètre d'URL (`?jeton=`, `maestro.controltower.acces`), et le
serveur consigne l'URL entière. Relevé sur un poste : le `api.log` que
`scripts/controltower/start.sh` écrit contenait le jeton en clair.

Ce qui est exercé ici est **le serveur réel**, pas une imitation : uvicorn sert
l'app sur la boucle locale, avec **la configuration de journal que `maestro-api`
lui passe** (interceptée à l'appel de `uvicorn.run`), ses sorties redirigées
dans un fichier comme `> api.log 2>&1`. Un client ouvre le flux temps réel avec
le jeton, puis on lit le fichier.

La sonde — « le jeton est dans le journal » — est **prouvée sur un échantillon
fautif** d'abord : la configuration d'uvicorn nue, celle que l'API servait avant
#1292, écrit le jeton, et la sonde le voit. Sans cette preuve, un journal vide
ou une sonde mal écrite passeraient pour un journal propre.

Trois familles :

① **le serveur réel** — un appel temps réel authentifié, puis le journal ;
② **le masque** — ce qu'il masque (le paramètre, la valeur du jeton, la trace
   d'une exception) et ce qu'il garde (chemin, autres paramètres, code) ;
③ **les journaux d'avant** — non réécrits, mais nommés par
   `scripts/controltower/start.sh` : sa fonction `signaler_journaux_exposes`,
   extraite du script et jouée telle quelle sur un dossier jetable (démarrer la
   stack entière exigerait Redis et le front).
"""

from __future__ import annotations

import copy
import logging
import os
import re
import shutil
import socket
import string
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from uvicorn.config import LOGGING_CONFIG
from websockets.sync.client import connect

from maestro.config import load_settings
from maestro.controltower.acces import (
    MASQUE_JETON,
    PARAM_JETON,
    REGIME_JETON,
    FiltreDuJeton,
    PolitiqueAcces,
    masquer_le_jeton,
    politique_depuis,
)
from maestro.controltower.app import create_app
from maestro.controltower.cli import FILTRE_DU_JETON, config_du_journal
from maestro.controltower.cli import main as cli

RACINE = Path(__file__).resolve().parents[1]
START = RACINE / "scripts" / "controltower" / "start.sh"
BASH = shutil.which("bash")

#: Un jeton de forme réelle (43 caractères de base64 URL-safe), pour les tests
#: qui n'ont pas besoin de celui du poste.
JETON = "Xq7-deTest_0123456789abcdefghijklmnopqrstuv"

#: Les loggers qu'une configuration d'uvicorn touche — rendus tels quels après
#: chaque test, pour que la suite ne garde aucun gestionnaire vers un fichier
#: jetable.
LOGGERS_UVICORN = ("uvicorn", "uvicorn.error", "uvicorn.access")

#: Plafond d'attente du démarrage et de l'arrêt du serveur, en secondes.
DELAI_SERVEUR_S = 20.0


@pytest.fixture(autouse=True)
def _journaux_rendus() -> Iterator[None]:
    """Rend les loggers d'uvicorn dans l'état où on les a trouvés."""
    avant = {}
    for nom in LOGGERS_UVICORN:
        journal = logging.getLogger(nom)
        avant[nom] = (list(journal.handlers), journal.level, journal.propagate)
    try:
        yield
    finally:
        for nom, (gestionnaires, niveau, propagation) in avant.items():
            journal = logging.getLogger(nom)
            for gestionnaire in journal.handlers:
                if gestionnaire not in gestionnaires:
                    gestionnaire.close()
            journal.handlers[:] = gestionnaires
            journal.setLevel(niveau)
            journal.propagate = propagation


@pytest.fixture()
def politique(monkeypatch: pytest.MonkeyPatch) -> PolitiqueAcces:
    """La politique durcie du poste — celle que `maestro-api` résout aussi."""
    monkeypatch.setenv("MAESTRO_API_AUTH", REGIME_JETON)
    resolue = politique_depuis(load_settings())
    assert resolue.jeton, "la politique durcie porte un jeton"
    return resolue


def _config_de_maestro_api(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """La configuration de journal que `maestro-api` passe à uvicorn.

    `uvicorn.run` est intercepté : on lit ce que le point d'entrée lui donne
    **vraiment**, sans rien servir. Sans `log_config`, uvicorn prend la sienne.
    """
    appels: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *_args, **kwargs: appels.append(kwargs))

    assert cli(["--port", "8123"]) == 0

    (arguments,) = appels
    return arguments.get("log_config", LOGGING_CONFIG)


def _vers_le_fichier(config: dict[str, Any], fichier: Path) -> dict[str, Any]:
    """La même configuration, ses sorties dans un fichier — comme `> api.log 2>&1`.

    Seule la destination change : formateurs et filtres restent ceux de la
    configuration éprouvée.
    """
    copie = copy.deepcopy(config)
    for gestionnaire in copie.get("handlers", {}).values():
        gestionnaire.pop("stream", None)
        gestionnaire["class"] = "logging.FileHandler"
        gestionnaire["filename"] = str(fichier)
        gestionnaire["encoding"] = "utf-8"
    return copie


@contextmanager
def _api_servie(config: dict[str, Any], politique: PolitiqueAcces) -> Iterator[int]:
    """L'app durcie servie par uvicorn sur la boucle locale ; rend le port."""
    prise = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    prise.bind(("127.0.0.1", 0))
    port = prise.getsockname()[1]
    serveur = uvicorn.Server(
        uvicorn.Config(create_app(acces=politique), log_config=config, lifespan="on")
    )
    fil = threading.Thread(target=serveur.run, kwargs={"sockets": [prise]}, daemon=True)
    fil.start()
    try:
        limite = time.monotonic() + DELAI_SERVEUR_S
        while not serveur.started:
            assert fil.is_alive(), "le serveur s'est arrêté avant d'écouter"
            assert time.monotonic() < limite, "le serveur n'a pas démarré"
            time.sleep(0.05)
        yield port
    finally:
        serveur.should_exit = True
        fil.join(DELAI_SERVEUR_S)
        prise.close()


def _journal_d_une_session(config: dict[str, Any], politique: PolitiqueAcces, fichier: Path) -> str:
    """Sert l'API, ouvre le flux temps réel avec le jeton, rend le journal écrit.

    Deux appels, ceux qui portent une URL dans le journal : la poignée de main
    WebSocket authentifiée (le chemin du front), et une requête REST qui passe le
    jeton en paramètre — refusée (`401`), mais consignée par le journal d'accès.
    """
    with _api_servie(_vers_le_fichier(config, fichier), politique) as port:
        url = f"ws://127.0.0.1:{port}/ws/evenements?projet=tous&{PARAM_JETON}={politique.jeton}"
        with connect(url, open_timeout=DELAI_SERVEUR_S):
            pass
        with pytest.raises(urllib.error.HTTPError) as refus:
            urllib.request.urlopen(  # noqa: S310 - boucle locale, schéma écrit ici
                f"http://127.0.0.1:{port}/api/sante?{PARAM_JETON}={politique.jeton}",
                timeout=DELAI_SERVEUR_S,
            )
        assert refus.value.code == 401
    return fichier.read_text(encoding="utf-8")


# --- ① Le serveur réel : un appel temps réel authentifié, puis le journal -----


def test_la_sonde_voit_le_jeton_dans_un_journal_fautif(tmp_path: Path, politique):
    # L'échantillon fautif : uvicorn configuré par lui-même, ce que l'API
    # servait avant #1292. Le jeton y est, et la sonde le dit.
    journal = _journal_d_une_session(LOGGING_CONFIG, politique, tmp_path / "api.log")

    assert "/ws/evenements" in journal, "la poignée de main doit être consignée"
    assert str(politique.jeton) in journal


def test_le_journal_de_maestro_api_ne_contient_pas_le_jeton(
    tmp_path: Path, politique, monkeypatch: pytest.MonkeyPatch
):
    config = _config_de_maestro_api(monkeypatch)

    journal = _journal_d_une_session(config, politique, tmp_path / "api.log")

    assert str(politique.jeton) not in journal
    # Ce qui sert au diagnostic reste : le chemin, les autres paramètres, le
    # code du refus — et la place du paramètre, masqué.
    assert f"/ws/evenements?projet=tous&{PARAM_JETON}={MASQUE_JETON}" in journal
    assert f"/api/sante?{PARAM_JETON}={MASQUE_JETON}" in journal
    assert " 401" in journal


def test_le_filtre_est_sur_chaque_gestionnaire_du_serveur():
    # Sur chaque gestionnaire, sans en nommer aucun : le journal d'accès et
    # celui des poignées de main WebSocket passent tous deux l'URL entière.
    config = config_du_journal(JETON)

    assert config["handlers"], "la configuration d'uvicorn porte des gestionnaires"
    for nom, gestionnaire in config["handlers"].items():
        assert FILTRE_DU_JETON in gestionnaire.get("filters", []), nom
    assert config["filters"][FILTRE_DU_JETON]["()"] is FiltreDuJeton


def test_la_configuration_d_uvicorn_n_est_pas_modifiee_en_place():
    # Une copie : la valeur par défaut d'uvicorn sert d'autres appelants dans
    # le même process (les tests, un uvicorn lancé à la main).
    config_du_journal(JETON)

    for gestionnaire in LOGGING_CONFIG["handlers"].values():
        assert FILTRE_DU_JETON not in gestionnaire.get("filters", [])


# --- ② Le masque : ce qu'il masque, ce qu'il garde ----------------------------


def test_le_parametre_est_masque_et_le_reste_de_la_ligne_garde():
    ligne = f'127.0.0.1:5 - "GET /ws/evenements?projet=tous&{PARAM_JETON}={JETON}&x=1 HTTP/1.1" 101'

    assert masquer_le_jeton(ligne) == (
        f'127.0.0.1:5 - "GET /ws/evenements?projet=tous&{PARAM_JETON}={MASQUE_JETON}&x=1 '
        'HTTP/1.1" 101'
    )


@pytest.mark.parametrize(
    "texte",
    [
        f"/ws/evenements?{PARAM_JETON}=pas-le-bon",  # un jeton faux reste un secret tenté
        "/ws/evenements?Jeton=mal-casse",  # un client qui l'écrit mal y a mis le secret
        f'"WebSocket /ws/evenements?{PARAM_JETON}=abc" [accepted]',  # fin au guillemet
    ],
)
def test_toute_valeur_du_parametre_est_masquee(texte: str):
    masque = masquer_le_jeton(texte)

    assert MASQUE_JETON in masque
    assert "pas-le-bon" not in masque
    assert "mal-casse" not in masque
    assert "=abc" not in masque


def test_un_parametre_qui_finit_par_jeton_n_est_pas_le_jeton():
    assert masquer_le_jeton("/x?monjeton=abc") == "/x?monjeton=abc"


def test_le_jeton_est_masque_par_sa_valeur_ou_qu_il_paraisse():
    # Le paramètre n'est que l'endroit attendu : un en-tête recopié dans une
    # trace, un message d'erreur, ne passeraient pas par `jeton=`.
    texte = f"Authorization: Bearer {JETON} refusé"

    assert masquer_le_jeton(texte, JETON) == f"Authorization: Bearer {MASQUE_JETON} refusé"


def test_le_masque_est_idempotent():
    une_fois = masquer_le_jeton(f"/ws?{PARAM_JETON}={JETON}", JETON)

    assert masquer_le_jeton(une_fois, JETON) == une_fois


def test_le_masque_est_hors_de_l_alphabet_du_jeton():
    # C'est ce qui rend un journal masqué distinct d'un journal fautif.
    alphabet = set(string.ascii_letters + string.digits + "-_")

    assert not set(MASQUE_JETON) & alphabet


def test_le_filtre_garde_les_arguments_d_un_journal_d_acces_a_leur_place():
    # Le formateur d'accès relit les arguments par position ; le code reste un
    # entier, sans quoi `%d` casserait la ligne.
    enregistrement = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5", "GET", f"/ws/evenements?{PARAM_JETON}={JETON}", "1.1", 401),
        None,
    )

    assert FiltreDuJeton(JETON).filter(enregistrement) is True
    assert enregistrement.args[4] == 401
    assert enregistrement.getMessage() == (
        f'127.0.0.1:5 - "GET /ws/evenements?{PARAM_JETON}={MASQUE_JETON} HTTP/1.1" 401'
    )


def test_le_filtre_masque_la_trace_d_une_exception():
    try:
        raise RuntimeError(f"poignée de main ratée sur /ws?{PARAM_JETON}={JETON}")
    except RuntimeError:
        enregistrement = logging.LogRecord(
            "uvicorn.error", logging.ERROR, __file__, 1, "échec", (), sys.exc_info()
        )

    FiltreDuJeton(JETON).filter(enregistrement)

    rendu = logging.Formatter().format(enregistrement)
    assert "RuntimeError" in rendu
    assert JETON not in rendu


def test_en_regime_ouvert_le_parametre_reste_masque():
    # Aucun jeton exigé, mais un client peut encore en passer un : le filtre
    # sans valeur connue masque toujours le paramètre.
    enregistrement = logging.LogRecord(
        "uvicorn.error",
        logging.INFO,
        __file__,
        1,
        '"WebSocket %s"',
        (f"/ws?{PARAM_JETON}={JETON}",),
        None,
    )

    FiltreDuJeton(None).filter(enregistrement)

    assert JETON not in enregistrement.getMessage()


# --- ③ Les journaux d'avant : nommés par start.sh, jamais réécrits ------------


def _signaler(racine: Path, jeton: str) -> subprocess.CompletedProcess[str]:
    """Joue `signaler_journaux_exposes` de `start.sh`, extraite telle quelle."""
    texte = START.read_text(encoding="utf-8")
    fonction = re.search(r"^signaler_journaux_exposes\(\) \{\n.*?^\}\n", texte, re.M | re.S)
    assert fonction, "start.sh définit signaler_journaux_exposes"
    environnement = {**os.environ, "RACINE": racine.as_posix(), "JETON_API": jeton}
    assert BASH is not None
    return subprocess.run(  # noqa: S603
        [BASH, "-c", f"{fonction.group(0)}signaler_journaux_exposes"],
        env=environnement,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=True,
    )


def _journal(racine: Path, relatif: str, contenu: str) -> None:
    fichier = racine / relatif
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_text(contenu, encoding="utf-8", newline="\n")


@pytest.mark.skipif(BASH is None, reason="bash introuvable")
def test_start_nomme_un_journal_qui_porte_encore_le_jeton(tmp_path: Path):
    # L'échantillon fautif : un api.log écrit avant #1292, le jeton en clair.
    fautif = ".maestro/controltower/8000-3000/api.log"
    _journal(tmp_path, fautif, f'"WebSocket /ws/evenements?{PARAM_JETON}={JETON}" [accepted]\n')

    sortie = _signaler(tmp_path, JETON).stdout

    assert fautif in sortie
    assert "#1292" in sortie
    # Nommé, jamais réécrit.
    assert JETON in (tmp_path / fautif).read_text(encoding="utf-8")


@pytest.mark.skipif(BASH is None, reason="bash introuvable")
def test_start_ne_nomme_ni_un_journal_masque_ni_un_autre_jeton(tmp_path: Path):
    _journal(
        tmp_path,
        ".maestro/controltower/8000-3000/api.log",
        f'"WebSocket /ws/evenements?{PARAM_JETON}={MASQUE_JETON}" [accepted]\n',
    )
    _journal(
        tmp_path,
        ".maestro/presentation/api.log",
        f'"WebSocket /ws/evenements?{PARAM_JETON}=un-jeton-perime-depuis" [accepted]\n',
    )

    # Muet : le masque n'est pas le jeton, et un jeton périmé n'est plus un secret.
    assert _signaler(tmp_path, JETON).stdout == ""


@pytest.mark.skipif(BASH is None, reason="bash introuvable")
def test_start_se_tait_en_regime_ouvert(tmp_path: Path):
    _journal(tmp_path, ".maestro/controltower/8000-3000/api.log", f"{PARAM_JETON}={JETON}\n")

    assert _signaler(tmp_path, "").stdout == ""
