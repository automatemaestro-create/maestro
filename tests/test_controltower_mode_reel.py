"""Tests du mode réel du lanceur Control Tower (ticket #188, lot 4/4 de #184).

**Aucune stack démarrée, aucun Redis, aucun réseau.** Ce sont les invariants du
lanceur local passé en mode réel par défaut (#186) qui sont vérifiés ici, chacun
par le levier qui l'expose sans rien lancer :

① **Le réel est le seul mode** — le mode démo a quitté le produit (#1168) :
   `--demo`, `--demonstration` et `--scenario` sont **refusés en le disant**, avant
   tout nettoyage ni démarrage, et le refus nomme les états réels qui les remplacent
   (stack neuve, état du banc, vraie panne). `scripts/controltower/start.sh` sait
   dire ce qu'il ferait sans démarrer quoi que ce soit : `--diagnostic-navigateur`
   imprime ses lignes `clé: valeur` puis s'arrête (avant le préflight comme avant le
   nettoyage). C'est le même levier que
   [`test_controltower_start.py`](test_controltower_start.py) utilise pour le choix
   du navigateur (#200) — d'où des tests déterministes et multiplateformes.

② **Le diagnostic quand Redis manque** — il vit dans `maestro.controltower.cli`
   (`--verifier-redis`), seul endroit où `REDIS_URL` est résolue. On l'exerce en
   Python, le client `redis` remplacé par un double : ni serveur joignable, ni
   time-out réseau à subir, et les deux issues (répond / ne répond pas) sont
   jouables à volonté.

③ **Le refus du lanceur** — bout en bout, en shell : sans Redis, le script sort
   en erreur avec le geste exact **sans avoir rien démarré ni arrêté** (c'est la
   promesse de #186 : pas de session en place sacrifiée pour découvrir que Redis
   manque). Ce
   seul test a besoin du venv du dépôt — le préflight du script s'y réfère en dur
   —, donc il se saute là où il n'existe pas (l'image CI installe le paquet sans
   passer par `.venv/`) ; ② couvre le même diagnostic partout.

⑤ **L'état du banc** (#1164) — `--etat-banc [--rejouer[=S…]]`, par le même
   diagnostic que ① : il se **demande**, ne change que les données servies, et
   `--rejouer` ne vaut qu'avec lui (un rejeu coûte du vrai modèle).
   Son préflight passe, comme ③, avant tout nettoyage. Ce que l'état contient, et la
   façon dont il se sauve et se rouvre, est gardé par `tests/test_etat_banc.py`.

⑥ **La stack neuve et la vraie panne** (#1165) — ce que la relecture visuelle ouvre pour les
   états vide et erreur. `--etat-neuf` se demande comme ⑤ et ne se mêle ni à lui, ni à un
   rejeu ; `--couper-api` fait tomber l'API **seule**, sans rien solder — éprouvé
   sur deux vrais processus à l'écoute, puisque c'est un geste et non une sélection.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
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


# ------------------------------------ ① Le réel est le seul mode (#186, #1168)


def test_le_diagnostic_ne_dit_plus_de_stack() -> None:
    """Une seule stack, la réelle : le diagnostic n'a plus de mode à nommer (#1168)."""
    champs = diagnostic()
    assert "stack" not in champs
    assert champs["famille"], "le diagnostic répond toujours"


@pytest.mark.parametrize(
    "options",
    [("--demo",), ("--demonstration",), ("--scenario", "vide"), ("--scenario=vide",)],
)
def test_le_mode_demo_est_refuse_en_le_disant(options: tuple[str, ...]) -> None:
    """Le mode démo est parti : on le dit, on nomme ce qui le remplace, et rien n'est touché.

    Un « Option inconnue » aurait laissé chercher une faute de frappe. Le refus tombe au
    premier argument, donc avant le diagnostic, le préflight et tout nettoyage.
    """
    acheve = lanceur(*options, "--diagnostic-navigateur")

    assert acheve.returncode == 2, acheve.stdout + acheve.stderr
    assert "le mode démo a quitté le produit" in acheve.stderr
    for geste in ("--etat-neuf", "--etat-banc", "--couper-api"):
        assert geste in acheve.stderr
    assert "famille:" not in acheve.stdout, "refusé avant le diagnostic, donc avant tout le reste"
    assert "[nettoyage]" not in acheve.stdout
    assert "[api]" not in acheve.stdout


def test_le_lanceur_ne_connait_plus_le_module_de_la_demo() -> None:
    """Plus rien à lancer : ni `maestro.controltower.demo`, ni une liste de scénarios à lire."""
    source = SCRIPT.read_text(encoding="utf-8")
    assert "maestro.controltower.demo" not in source
    assert "controltower/demo.py" not in source


def test_une_option_inconnue_est_refusee() -> None:
    """Un drapeau mal orthographié ne doit pas démarrer silencieusement le mode réel."""
    acheve = lanceur("--demoo")

    assert acheve.returncode == 2
    assert "--demoo" in acheve.stderr
    # Rien n'a été touché : ni nettoyage d'une session en place, ni démarrage.
    assert "[nettoyage]" not in acheve.stdout
    assert "[api]" not in acheve.stdout


def lanceur(*options: str) -> subprocess.CompletedProcess[str]:
    """`start.sh` joué tel quel — pour les refus, qui tombent avant tout démarrage."""
    assert BASH is not None
    return subprocess.run(  # noqa: S603
        [BASH, str(SCRIPT), *options],
        cwd=str(RACINE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


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
    # Le seul geste proposé est de lancer Redis : il n'y a plus d'autre stack (#1168).
    assert "--demo" not in acheve.stderr
    assert "rien n'a été démarré ni arrêté" in acheve.stderr
    # Et c'est vrai : aucune session touchée, aucun service lancé.
    assert "[nettoyage]" not in acheve.stdout
    assert "[api]" not in acheve.stdout
    assert "[ui]" not in acheve.stdout


# ------------------------------------------- ⑤ L'état du banc (#1164)


def test_l_etat_du_banc_se_demande_et_ne_change_que_les_donnees() -> None:
    """Sans l'option, le diagnostic d'avant ; avec, la même stack réelle sur d'autres données."""
    reel = diagnostic()
    banc = diagnostic("--etat-banc")

    assert "donnees" not in reel, "sans --etat-banc, le diagnostic d'avant, au bit près"
    assert banc["donnees"] == "banc"
    assert {c: v for c, v in banc.items() if c != "donnees"} == reel


@pytest.mark.parametrize(
    ("option", "attendu"), [("--rejouer", "tous"), ("--rejouer=S2,S4", "S2,S4")]
)
def test_rejouer_l_etat_du_banc_se_demande_avec_lui(option: str, attendu: str) -> None:
    assert diagnostic("--etat-banc", option)["rejouer"] == attendu


@pytest.mark.parametrize("option", ["--rejouer", "--rejouer=S2"])
def test_rejouer_sans_l_etat_du_banc_est_refuse(option: str) -> None:
    """Un rejeu égaré ne lance pas un passage du banc : il coûte du vrai modèle."""
    acheve = lanceur(option, "--diagnostic-navigateur")
    assert acheve.returncode == 2, acheve.stdout + acheve.stderr
    assert "--etat-banc --rejouer" in acheve.stderr
    assert "famille:" not in acheve.stdout, "refusé avant le diagnostic, donc avant tout le reste"


def test_rejouer_sans_scenario_apres_le_egal_est_refuse() -> None:
    acheve = lanceur("--etat-banc", "--rejouer=", "--diagnostic-navigateur")
    assert acheve.returncode == 2, acheve.stdout + acheve.stderr
    assert "famille:" not in acheve.stdout


@pytest.mark.skipif(
    not PYTHON_VENV.exists(),
    reason="préflight du lanceur : venv du dépôt requis (absent de l'image CI)",
)
def test_le_lanceur_refuse_l_etat_du_banc_sans_redis() -> None:
    """Même promesse que ③ sur l'état du banc : le préflight tombe AVANT le nettoyage.

    Le banc vit sur Redis : sans lui, rien à rouvrir, et la session en place n'est
    pas sacrifiée pour le découvrir.
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
        [BASH, str(SCRIPT), "--etat-banc"],
        cwd=str(RACINE),
        env=environnement,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )

    assert acheve.returncode == 1, acheve.stdout + acheve.stderr
    assert COMMANDE_REDIS in acheve.stderr
    assert "rien n'a été démarré ni arrêté" in acheve.stderr
    assert "[nettoyage]" not in acheve.stdout
    assert "[api]" not in acheve.stdout


# ------------------------------------------- ⑥ La stack neuve et la vraie panne (#1165)


def test_la_stack_neuve_se_demande_et_ne_change_que_les_donnees() -> None:
    """L'état « vide » de la relecture : la stack réelle, sur un jeu de données neuf."""
    reel = diagnostic()
    neuf = diagnostic("--etat-neuf")

    assert neuf["donnees"] == "neuf"
    assert {c: v for c, v in neuf.items() if c != "donnees"} == reel


@pytest.mark.parametrize(
    ("options", "motif"),
    [
        (("--etat-neuf", "--etat-banc"), "l'un ou l'autre"),
        (("--etat-banc", "--etat-neuf"), "l'un ou l'autre"),
        (("--etat-neuf", "--rejouer"), "--etat-banc --rejouer"),
    ],
)
def test_la_stack_neuve_ne_se_mele_a_rien(options: tuple[str, ...], motif: str) -> None:
    """Neuve et rouverte s'excluent, et rejouer coûte du vrai modèle : aucun des deux ne se
    demande avec elle."""
    acheve = lanceur(*options, "--diagnostic-navigateur")
    assert acheve.returncode == 2, acheve.stdout + acheve.stderr
    assert motif in acheve.stderr
    assert "famille:" not in acheve.stdout, "refusé avant le diagnostic, donc avant tout le reste"


def _port_libre() -> int:
    with socket.socket() as sonde:
        sonde.bind(("127.0.0.1", 0))
        return int(sonde.getsockname()[1])


def _ecoute(port: int) -> bool:
    with socket.socket() as sonde:
        sonde.settimeout(1)
        return sonde.connect_ex(("127.0.0.1", port)) == 0


def _couper(api: int, ui: int) -> subprocess.CompletedProcess[str]:
    """`start.sh --couper-api` sur ces ports-là, et sur aucun autre."""
    environnement = os.environ.copy()
    for cle in _A_NETTOYER:
        environnement.pop(cle, None)
    environnement.update({"MAESTRO_PORT_API": str(api), "MAESTRO_PORT_UI": str(ui)})
    assert BASH is not None
    return subprocess.run(  # noqa: S603
        [BASH, str(SCRIPT), "--couper-api"],
        cwd=str(RACINE),
        env=environnement,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def test_couper_l_api_sans_rien_a_couper_n_est_pas_une_erreur() -> None:
    """La panne est déjà là : on le dit, et rien d'autre n'est touché — ni soldage, ni UI."""
    acheve = _couper(_port_libre(), _port_libre())
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "déjà coupée" in acheve.stdout
    assert "[extinction]" not in acheve.stdout, "une panne ne solde rien : ce n'est pas un arrêt"
    assert "[nettoyage]" not in acheve.stdout


@pytest.mark.skipif(
    os.name != "nt" and shutil.which("lsof") is None,
    reason="hors Windows, le lanceur repère un port par lsof, absent de ce poste",
)
def test_couper_l_api_la_fait_tomber_et_laisse_l_ui_servie() -> None:
    """La vraie panne « API injoignable » (#996) : l'API tombe net, l'UI reste servie.

    Deux vrais processus à l'écoute, pour que le test dise ce que le lanceur FAIT — ce que
    l'écran montre ensuite est l'affaire de la relecture, pas de ce module.
    """
    api, ui = _port_libre(), _port_libre()
    serveurs = [
        subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for port in (api, ui)
    ]
    try:
        for _ in range(100):
            if _ecoute(api) and _ecoute(ui):
                break
            time.sleep(0.1)
        assert _ecoute(api) and _ecoute(ui), "les deux serveurs d'essai n'ont pas démarré"

        acheve = _couper(api, ui)

        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert "coupée net, sans soldage" in acheve.stdout
        assert "[extinction]" not in acheve.stdout
        assert serveurs[0].wait(timeout=10) is not None, "l'API est tombée"
        assert serveurs[1].poll() is None, "l'UI est restée servie"
        assert _ecoute(ui)
    finally:
        for serveur in serveurs:
            if serveur.poll() is None:
                serveur.kill()
                serveur.wait(timeout=10)
