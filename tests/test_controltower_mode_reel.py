"""Tests du mode réel du lanceur Control Tower (ticket #188, lot 4/4 de #184).

**Aucune stack démarrée, aucun Redis, aucun réseau.** Ce sont les invariants du
lanceur local passé en mode réel par défaut (#186) qui sont vérifiés ici, chacun
par le levier qui l'expose sans rien lancer :

① **Quel mode est choisi** — `scripts/controltower/start.sh` sait le dire sans
   démarrer quoi que ce soit : `--diagnostic-navigateur` imprime `stack: reel` ou
   `stack: demo` puis s'arrête (avant le préflight comme avant le nettoyage).
   C'est le même levier que [`test_controltower_start.py`](test_controltower_start.py)
   utilise pour le choix du navigateur (#200) — d'où des tests déterministes et
   multiplateformes. On y vérifie que **le réel est le défaut**, que la démo est
   **demandée** (`--demo`), et que le mode ne déteint pas sur le reste du
   lanceur (même navigateur, mêmes ports).

② **Le diagnostic quand Redis manque** — il vit dans `maestro.controltower.cli`
   (`--verifier-redis`), seul endroit où `REDIS_URL` est résolue. On l'exerce en
   Python, le client `redis` remplacé par un double : ni serveur joignable, ni
   time-out réseau à subir, et les deux issues (répond / ne répond pas) sont
   jouables à volonté.

③ **Le refus du lanceur** — bout en bout, en shell : mode réel sans Redis, le
   script sort en erreur avec le geste exact **sans avoir rien démarré ni
   arrêté** (c'est la promesse de #186 : jamais de repli silencieux sur la démo,
   et pas de session en place sacrifiée pour découvrir que Redis manque). Ce
   seul test a besoin du venv du dépôt — le préflight du script s'y réfère en dur
   —, donc il se saute là où il n'existe pas (l'image CI installe le paquet sans
   passer par `.venv/`) ; ② couvre le même diagnostic partout.

Ce qui n'est **pas** testé ici, faute de pouvoir l'être sans démarrer la stack :
que le mode démo saute effectivement le préflight Redis. Le lancer pour
l'observer contredirait la contrainte du ticket ; ① établit que `--demo`
sélectionne l'autre stack, et le préflight est gardé par cette seule condition.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from maestro.controltower.cli import COMMANDE_REDIS, endpoint_lisible, verifier_redis

RACINE = Path(__file__).resolve().parent.parent
SCRIPT = RACINE / "scripts" / "controltower" / "start.sh"
BASH = shutil.which("bash")
#: Le préflight du lanceur invoque le python du venv en dur (cf. CLAUDE.md) : sans
#: lui, ③ s'arrêterait sur « Python du venv introuvable » avant même Redis.
PYTHON_VENV = RACINE / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash introuvable")

#: Surcharges du poste (bloc `env` de settings.local.json, vrai navigateur imposé,
#: ports d'un worktree) : retirées, chaque scénario posant les siennes.
_A_NETTOYER = ("MAESTRO_BROWSER", "MAESTRO_BROWSER_DEFAUT", "MAESTRO_PORT_API", "MAESTRO_PORT_UI")


def diagnostic(*options: str, env_extra: dict[str, str] | None = None) -> dict[str, str]:
    """Lance `start.sh … --diagnostic-navigateur` et rend ses lignes `clé: valeur`.

    Le diagnostic n'ouvre rien et ne démarre rien : c'est ce qui permet d'exercer
    la sélection du mode sans stack.
    """
    environnement = os.environ.copy()
    for cle in _A_NETTOYER:
        environnement.pop(cle, None)
    # Un défaut hors Chromium évite de dépendre d'un navigateur installé sur le poste.
    environnement.setdefault("MAESTRO_BROWSER_DEFAUT", "firefox")
    environnement.update(env_extra or {})
    assert BASH is not None
    acheve = subprocess.run(  # noqa: S603
        [BASH, str(SCRIPT), *options, "--diagnostic-navigateur"],
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


# ------------------------------------------- ① Le mode choisi par le lanceur (#186)


def test_le_mode_reel_est_le_defaut() -> None:
    """Sans option, le lanceur monte la vraie orchestration — plus la démo (#186)."""
    assert diagnostic()["stack"] == "reel"


@pytest.mark.parametrize("option", ["--demo", "--demonstration"])
def test_le_scenario_factice_doit_etre_demande(option: str) -> None:
    """La démo n'est plus un défaut : elle a son drapeau, sous ses deux orthographes."""
    assert diagnostic(option)["stack"] == "demo"


def test_le_mode_ne_change_rien_au_reste_du_lanceur() -> None:
    """« Tout le reste est IDENTIQUE dans les deux modes » : seule la stack change.

    Mêmes ports, donc mêmes dossiers de logs et même profil de navigateur (#152),
    et même stratégie d'ouverture : basculer en démo ne doit pas déplacer la
    session ailleurs.
    """
    reel = diagnostic()
    demo = diagnostic("--demo")

    assert reel != demo  # sans quoi le test ne prouverait rien
    assert {c: v for c, v in reel.items() if c != "stack"} == {
        c: v for c, v in demo.items() if c != "stack"
    }


def test_une_option_inconnue_est_refusee() -> None:
    """Un drapeau mal orthographié ne doit pas démarrer silencieusement le mode réel."""
    assert BASH is not None
    acheve = subprocess.run(  # noqa: S603
        [BASH, str(SCRIPT), "--demoo"],
        cwd=str(RACINE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )

    assert acheve.returncode == 2
    assert "--demoo" in acheve.stderr
    # Rien n'a été touché : ni nettoyage d'une session en place, ni démarrage.
    assert "[nettoyage]" not in acheve.stdout
    assert "[api]" not in acheve.stdout


# ------------------------------- ② Le diagnostic Redis, client remplacé par un double


class ClientDouble:
    """Le client `redis` réduit à ce dont `verifier_redis` se sert : ping + close."""

    def __init__(self, erreur: Exception | None = None) -> None:
        self.erreur = erreur
        self.ferme = False
        self.url: str | None = None
        self.reglages: dict[str, object] = {}

    def ping(self) -> bool:
        if self.erreur is not None:
            raise self.erreur
        return True

    def close(self) -> None:
        self.ferme = True


def brancher_redis(monkeypatch: pytest.MonkeyPatch, client: ClientDouble) -> ClientDouble:
    """Substitue un faux module `redis` — aucun serveur, aucune socket, aucun time-out."""

    def from_url(url: str, **reglages: object) -> ClientDouble:
        client.url = url
        client.reglages = reglages
        return client

    faux = types.ModuleType("redis")
    faux.Redis = types.SimpleNamespace(from_url=from_url)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redis", faux)
    return client


def test_redis_joignable_rend_zero_et_nomme_l_endpoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Le cas nominal : code 0, et le diagnostic dit OÙ il a frappé."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    client = brancher_redis(monkeypatch, ClientDouble())

    assert verifier_redis() == 0

    assert "redis://127.0.0.1:6379/0" in capsys.readouterr().out
    assert client.ferme  # pas de connexion laissée ouverte par un diagnostic


def test_redis_injoignable_rend_un_et_donne_le_geste(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """L'invariant du préflight : dire l'échec, l'endroit, et QUOI FAIRE ensuite."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    brancher_redis(monkeypatch, ClientDouble(erreur=ConnectionError("connexion refusée")))

    assert verifier_redis() == 1

    erreurs = capsys.readouterr().err
    assert "redis://127.0.0.1:6399/0" in erreurs
    assert "connexion refusée" in erreurs
    assert COMMANDE_REDIS in erreurs  # le geste exact, pas « lancez Redis »


def test_le_diagnostic_ne_publie_pas_le_mot_de_passe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`REDIS_URL` peut porter des identifiants : un diagnostic ne les imprime pas."""
    monkeypatch.setenv("REDIS_URL", "redis://admin:s3cret@cache.interne:6380/1")
    brancher_redis(monkeypatch, ClientDouble(erreur=ConnectionError("hôte injoignable")))

    assert verifier_redis() == 1

    capture = capsys.readouterr()
    assert "s3cret" not in capture.out + capture.err
    assert "redis://cache.interne:6380/1" in capture.err


def test_l_url_par_defaut_sert_quand_rien_n_est_configure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sans `REDIS_URL`, c'est l'instance locale mutualisée qui est visée."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    client = brancher_redis(monkeypatch, ClientDouble())

    assert verifier_redis() == 0

    assert client.url == "redis://localhost:6379/0"
    assert "redis://localhost:6379/0" in capsys.readouterr().out


def test_le_ping_est_borne_dans_le_temps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un hôte injoignable ne doit pas faire patienter le lanceur le time-out système."""
    monkeypatch.setenv("REDIS_URL", "redis://cache.interne:6379/0")
    client = brancher_redis(monkeypatch, ClientDouble())

    verifier_redis()

    assert client.reglages["socket_connect_timeout"] == pytest.approx(3.0)
    assert client.reglages["socket_timeout"] == pytest.approx(3.0)


def test_client_redis_absent_est_dit_sans_trace(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Venv incomplet : un message actionnable, pas un ImportError brut."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setitem(sys.modules, "redis", None)  # rend l'import ImportError

    assert verifier_redis() == 1

    assert "scripts/setup.sh" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("url", "attendu"),
    [
        pytest.param("redis://localhost:6379/0", "redis://localhost:6379/0", id="nue"),
        pytest.param("redis://:pass@h:6379/0", "redis://h:6379/0", id="mot-de-passe-seul"),
        pytest.param("rediss://u:p@h.io/2", "rediss://h.io/2", id="tls-sans-port"),
        pytest.param("redis://[::1]:6379/0", "redis://[::1]:6379/0", id="ipv6"),
    ],
)
def test_endpoint_lisible_ne_garde_que_le_point_de_connexion(url: str, attendu: str) -> None:
    """Schéma, hôte, port, base — et rien d'autre, quelle que soit la forme de l'URL."""
    assert endpoint_lisible(url) == attendu


# --------------------- ③ Le refus du lanceur, bout en bout (venv du dépôt requis)


@pytest.mark.skipif(
    not PYTHON_VENV.exists(),
    reason="préflight du lanceur : venv du dépôt requis (absent de l'image CI)",
)
def test_le_lanceur_refuse_le_mode_reel_sans_redis() -> None:
    """Sans Redis : sortie en erreur, geste donné, et RIEN démarré ni arrêté (#186).

    Le préflight passe **avant** le nettoyage, exprès : découvrir que Redis manque
    ne doit pas coûter la session en place (peut-être en train de servir). On le
    vérifie à l'absence de toute trace de nettoyage ou de démarrage.

    Ports volontairement hors des défauts : même dans le pire des cas, ce test ne
    peut pas toucher une Control Tower ouverte sur 8000/3000.
    """
    environnement = os.environ.copy()
    for cle in _A_NETTOYER:
        environnement.pop(cle, None)
    environnement["REDIS_URL"] = "redis://127.0.0.1:6399/0"  # port fermé, refus immédiat
    environnement["MAESTRO_PORT_API"] = "18099"
    environnement["MAESTRO_PORT_UI"] = "18098"
    environnement["MAESTRO_BROWSER_DEFAUT"] = "firefox"
    assert BASH is not None

    acheve = subprocess.run(  # noqa: S603
        [BASH, str(SCRIPT)],
        cwd=str(RACINE),
        env=environnement,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )

    assert acheve.returncode == 1
    # Le diagnostic du CLI, remonté tel quel par le lanceur.
    assert "redis://127.0.0.1:6399/0" in acheve.stderr
    assert COMMANDE_REDIS in acheve.stderr
    # Jamais de repli silencieux : la démo est proposée, pas prise d'office.
    assert "--demo" in acheve.stderr
    assert "rien n'a été démarré ni arrêté" in acheve.stderr
    # Et c'est vrai : aucune session touchée, aucun service lancé.
    assert "[nettoyage]" not in acheve.stdout
    assert "[api]" not in acheve.stdout
    assert "[ui]" not in acheve.stdout
