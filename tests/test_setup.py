"""Tests du parcours de mise en route — `scripts/setup.sh` (ticket #147, parent #144).

Premier harnais de test de SCRIPT SHELL du dépôt : le reste de `tests/` porte sur le
paquet Python. `scripts/setup.sh` est le premier contact d'un nouveau venu avec le
dépôt — une régression y est silencieuse (personne ne relit un rapport qui « a l'air
vert ») et coûteuse. D'où ces tests, différés au lot final du parent #144.

**Principe : un dépôt jetable.** Chaque test monte dans `tmp_path` un mini-clone qui ne
contient que ce dont l'étape visée a besoin (le vrai `scripts/setup.sh`, un `.env.example`
et un `.mcp.json` synthétiques), puis lance le script dessus. Rien n'est jamais écrit dans
le vrai dépôt : `HOME` et `TMPDIR` sont eux aussi redirigés vers le `tmp_path`.

**Ni réseau ni Docker.** Les étapes qui installent (`venv` → pip, `web` → npm, `runner` →
Docker + GitLab, `infra` → docker compose) et l'étape `verif` (qui interroge `glab`) sont
neutralisées par `--skip` : on vérifie la DÉCISION du script, jamais l'installation
elle-même. `MAESTRO_AUTO_INSTALL=0` est posé par défaut dans l'environnement de test —
filet de sécurité pour qu'un oubli de `--no-install` ne déclenche jamais un vrai
`winget`/`brew`/`apt-get` sur la machine qui lance la suite.

**Python garanti.** Un shim `python3` (vers l'interpréteur qui exécute pytest) est placé en
tête de PATH : les étapes qui en dépendent (`mcp`) ne se sautent donc pas selon ce que porte
la machine. À l'inverse, les tests de prérequis manquants reconstruisent un PATH MINIMAL —
juste les coreutils — pour simuler une machine sans python.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest

RACINE = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash introuvable")

# L'étape `hooks` délègue à `git config core.hooksPath` : sans git, rien à vérifier.
besoin_git = pytest.mark.skipif(GIT is None, reason="git introuvable")

# Ordre de ETAPES_CONNUES dans scripts/setup.sh — le rapport final le suit.
ETAPES = ("node", "prerequis", "venv", "env", "hooks", "web", "mcp", "runner", "infra", "verif")

# Étapes à neutraliser pour rester hors ligne (voir le docstring du module).
HORS_LIGNE = ("--skip", "venv,web,runner,infra,verif")

GABARIT_ENV = """\
# Gabarit de test — aucune valeur réelle.
CLAUDE_AUTH_MODE=
ANTHROPIC_API_KEY=
GITLAB_TOKEN=
"""

MCP_JSON = {
    "mcpServers": {
        "serveur-a": {"type": "stdio", "command": "true"},
        "serveur-b": {"type": "http", "url": "https://exemple.invalid/mcp"},
    }
}

# Commande d'installation attendue pour python, par plateforme (miroir de `commande_install`
# dans scripts/setup.sh — c'est justement ce que le test épingle).
INSTALL_PYTHON = {
    "windows": "winget install Python.Python.3.12",
    "macos": "brew install python@3.12",
    "linux": "sudo apt install python3 python3-venv python3-pip",
}


# --- Lecture du rapport final --------------------------------------------------------------

# `printf '  %s %-10s %s  %s\\n' <symbole> <étape> <libellé> <détail>`
LIGNE_RAPPORT = re.compile(
    r"^ {2}(?P<symbole>\S) (?P<etape>\S+) +"
    r"(?P<statut>OK|DÉJÀ FAIT|IGNORÉ|ÉCHEC) {2,}(?P<detail>.*)$"
)


@dataclass(frozen=True)
class Ligne:
    """Une ligne du tableau de synthèse imprimé en fin d'exécution."""

    symbole: str
    etape: str
    statut: str
    detail: str


def lignes_du_rapport(sortie: str) -> list[Ligne]:
    """Extrait le tableau « Rapport » de la sortie standard du script."""
    lignes: list[Ligne] = []
    dedans = False
    for brute in sortie.splitlines():
        if not dedans:
            dedans = brute.startswith("-------")
            continue
        if not brute.strip():
            break
        trouvee = LIGNE_RAPPORT.match(brute)
        if trouvee:
            lignes.append(Ligne(**trouvee.groupdict()))
    return lignes


def statut(sortie: str, etape: str) -> str:
    """Statut rapporté pour une étape (`OK`, `DÉJÀ FAIT`, `IGNORÉ`, `ÉCHEC`)."""
    for ligne in lignes_du_rapport(sortie):
        if ligne.etape == etape:
            return ligne.statut
    raise AssertionError(f"étape {etape!r} absente du rapport :\n{sortie}")


def detail(sortie: str, etape: str) -> str:
    for ligne in lignes_du_rapport(sortie):
        if ligne.etape == etape:
            return ligne.detail
    raise AssertionError(f"étape {etape!r} absente du rapport :\n{sortie}")


# --- Dépôt jetable --------------------------------------------------------------------------


def empreinte(racine: Path) -> dict[str, str]:
    """Empreinte de l'arborescence : chemin → hachage du contenu (dossiers inclus).

    `.git/` est exclu (git y écrit pour son propre compte, ça n'a rien à voir avec ce que
    le script écrit) ; l'étape `hooks` est vérifiée séparément, par `core.hooksPath`.
    """
    resultat: dict[str, str] = {}
    for chemin in sorted(racine.rglob("*")):
        relatif = chemin.relative_to(racine).as_posix()
        if relatif == ".git" or relatif.startswith(".git/"):
            continue
        if chemin.is_dir():
            resultat[relatif + "/"] = "<dossier>"
        else:
            resultat[relatif] = hashlib.sha256(chemin.read_bytes()).hexdigest()
    return resultat


@dataclass
class Depot:
    """Mini-clone jetable sur lequel `scripts/setup.sh` est lancé."""

    racine: Path
    home: Path
    tmp: Path
    fauxbin: Path

    # --- fichiers du dépôt ---
    @property
    def env(self) -> Path:
        return self.racine / ".env"

    @property
    def gabarit(self) -> Path:
        return self.racine / ".env.example"

    @property
    def reglages(self) -> Path:
        return self.racine / ".claude" / "settings.local.json"

    def ecrire(self, relatif: str, contenu: str) -> Path:
        cible = self.racine / relatif
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text(contenu, encoding="utf-8", newline="\n")
        return cible

    def json_reglages(self) -> dict:
        return json.loads(self.reglages.read_text(encoding="utf-8"))

    def empreinte(self) -> dict[str, str]:
        return empreinte(self.racine)

    # --- exécution ---
    def lance(
        self,
        *args: str,
        env: dict[str, str] | None = None,
        path: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Lance `bash scripts/setup.sh <args>` dans le dépôt jetable.

        `path` (optionnel) impose un PATH POSIX *à l'intérieur* de bash — on ne peut pas le
        passer par l'environnement du processus : sous Windows, bash convertit le PATH
        hérité (style Windows) au démarrage et malmènerait des chemins déjà POSIX.
        """
        environnement = os.environ.copy()
        # Le profil navigateur et les planchers de version viennent parfois de la machine
        # (bloc `env` des réglages Claude Code) : on repart d'une base neutre.
        for cle in ("MAESTRO_CHROME_PROFILE", "MAESTRO_PYTHON_MIN", "MAESTRO_NODE_MIN"):
            environnement.pop(cle, None)
        environnement["HOME"] = str(self.home)
        environnement["TMPDIR"] = str(self.tmp)
        environnement["MAESTRO_AUTO_INSTALL"] = "0"
        environnement["PATH"] = os.pathsep.join(
            [str(self.fauxbin), environnement.get("PATH", "")]
        )
        if env:
            environnement.update(env)

        assert BASH is not None
        if path is None:
            commande = [BASH, "scripts/setup.sh", *args]
        else:
            # $BASH = l'interpréteur courant : on relance le script même si le PATH imposé
            # ne contient pas de bash.
            commande = [
                BASH,
                "-c",
                'PATH="$1"; shift; exec "$BASH" scripts/setup.sh "$@"',
                "maestro-test",
                path,
                *args,
            ]
        return subprocess.run(  # noqa: S603
            commande,
            cwd=self.racine,
            env=environnement,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )


@pytest.fixture
def depot(tmp_path: Path) -> Depot:
    """Mini-clone : le vrai `setup.sh`, un gabarit `.env` et un `.mcp.json` synthétiques."""
    racine = tmp_path / "depot"
    (racine / "scripts" / "git" / "hooks").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    fauxbin = tmp_path / "fauxbin"
    fauxbin.mkdir()

    shutil.copy2(RACINE / "scripts" / "setup.sh", racine / "scripts" / "setup.sh")
    shutil.copy2(
        RACINE / "scripts" / "git" / "install-hooks.sh",
        racine / "scripts" / "git" / "install-hooks.sh",
    )
    for hook in (RACINE / "scripts" / "git" / "hooks").iterdir():
        shutil.copy2(hook, racine / "scripts" / "git" / "hooks" / hook.name)

    (racine / ".env.example").write_text(GABARIT_ENV, encoding="utf-8", newline="\n")
    (racine / ".mcp.json").write_text(
        json.dumps(MCP_JSON, indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    # Shim `python3` vers l'interpréteur de pytest : garantit un python >= 3.11 en tête de
    # PATH, quelle que soit la machine. MSYS/Git Bash reconnaît l'exécutabilité au `#!`.
    interpreteur = sys.executable.replace("\\", "/")
    shim = fauxbin / "python3"
    shim.write_text(
        f'#!/usr/bin/env bash\nexec "{interpreteur}" "$@"\n', encoding="utf-8", newline="\n"
    )
    shim.chmod(0o755)

    if GIT is not None:
        subprocess.run(  # noqa: S603
            [GIT, "init", "--quiet"], cwd=racine, check=True, capture_output=True
        )

    return Depot(racine=racine, home=home, tmp=tmp, fauxbin=fauxbin)


@pytest.fixture(scope="session")
def path_sans_python() -> str:
    """PATH minimal — les coreutils, rien d'autre — pour simuler une machine sans python.

    Construit depuis la machine hôte (les coreutils ne vivent pas au même endroit sous
    Git Bash et sous Debian), puis VÉRIFIÉ : si un python y reste visible (distribution où
    l'interpréteur cohabite avec les coreutils dans `/usr/bin`), le test se saute plutôt
    que de tester autre chose que ce qu'il annonce.
    """
    assert BASH is not None
    outils = "uname dirname cut cat grep sort tr comm cp mkdir date head id sed rm".split()
    trouves = subprocess.run(  # noqa: S603
        [BASH, "-c", 'for o in "$@"; do command -v "$o" || true; done', "maestro-test", *outils],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    dossiers: list[str] = []
    for chemin in trouves.stdout.split("\n"):
        chemin = chemin.strip()
        if not chemin:
            continue
        parent = str(PurePosixPath(chemin).parent)
        if parent not in dossiers:
            dossiers.append(parent)
    if not dossiers:
        pytest.skip("coreutils introuvables : PATH minimal non reconstructible")

    minimal = ":".join(dossiers)
    reste = subprocess.run(  # noqa: S603
        [
            BASH,
            "-c",
            'PATH="$1"; command -v python3 || command -v python || command -v py || true',
            "maestro-test",
            minimal,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if reste.stdout.strip():
        pytest.skip("python cohabite avec les coreutils : absence non simulable par le PATH")
    return minimal


@pytest.fixture(scope="session")
def systeme() -> str:
    """Plateforme telle que `os_kind()` de setup.sh la voit (windows / macos / linux)."""
    assert BASH is not None
    noyau = subprocess.run(  # noqa: S603
        [BASH, "-c", "uname -s"], capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()
    if noyau.startswith(("MINGW", "MSYS", "CYGWIN")):
        return "windows"
    if noyau == "Darwin":
        return "macos"
    return "linux"


# --- Drapeaux et aide -----------------------------------------------------------------------


def test_aide_decrit_les_etapes_et_les_drapeaux(depot: Depot) -> None:
    resultat = depot.lance("--help")

    assert resultat.returncode == 0
    for drapeau in ("--check", "--no-install", "--with-infra", "--only", "--skip"):
        assert drapeau in resultat.stdout
    for etape in ETAPES:
        assert etape in resultat.stdout


def test_option_inconnue_sort_en_2(depot: Depot) -> None:
    resultat = depot.lance("--nawak")

    assert resultat.returncode == 2
    assert "Option inconnue" in resultat.stderr


def test_only_et_skip_selectionnent_les_etapes(depot: Depot) -> None:
    resultat = depot.lance("--only", "env", "--no-install")

    assert statut(resultat.stdout, "env") == "OK"
    for etape in ETAPES:
        if etape != "env":
            assert statut(resultat.stdout, etape) == "IGNORÉ"
            assert "sautée (--only/--skip)" in detail(resultat.stdout, etape)


# --- Rapport final --------------------------------------------------------------------------


def test_le_rapport_couvre_toutes_les_etapes_dans_l_ordre(depot: Depot) -> None:
    resultat = depot.lance(*HORS_LIGNE, "--no-install")

    vues = [ligne.etape for ligne in lignes_du_rapport(resultat.stdout)]
    assert vues == list(ETAPES)


def test_une_etape_dure_en_echec_fait_sortir_en_code_non_nul(depot: Depot) -> None:
    depot.gabarit.unlink()  # sans gabarit, l'étape `env` ne peut rien faire

    resultat = depot.lance("--only", "env", "--no-install")

    assert resultat.returncode == 1
    assert statut(resultat.stdout, "env") == "ÉCHEC"
    assert ".env.example introuvable" in detail(resultat.stdout, "env")
    assert "1 étape(s) en échec" in resultat.stderr


def test_reglages_illisibles_sont_laisses_intacts_et_signales(depot: Depot) -> None:
    # `env` doit être un objet : une liste est une configuration cassée, que le script
    # refuse de « réparer » en écrasant le fichier.
    depot.ecrire(".claude/settings.local.json", '{"env": [], "aGarder": 1}\n')
    avant = depot.reglages.read_bytes()

    resultat = depot.lance("--only", "mcp", "--no-install")

    assert resultat.returncode == 1
    assert statut(resultat.stdout, "mcp") == "ÉCHEC"
    assert depot.reglages.read_bytes() == avant


# --- Mode --check : diagnostic seul, aucune écriture -----------------------------------------


def test_check_n_ecrit_aucun_fichier(depot: Depot) -> None:
    # Balayage complet, `verif` excepté (elle interroge `glab`, donc le réseau).
    avant = depot.empreinte()

    resultat = depot.lance("--check", "--skip", "verif", "--no-install")

    assert depot.empreinte() == avant
    assert not depot.env.exists()
    assert not depot.reglages.exists()
    assert not (depot.racine / ".venv").exists()
    assert "Mode --check : diagnostic seul" in resultat.stdout
    if GIT is not None:
        # git est un prérequis DUR : son absence ferait sortir en 1 pour une autre raison,
        # et l'épilogue « Diagnostic terminé » n'est imprimé qu'en l'absence d'échec dur —
        # les deux constats supposent donc un hôte outillé (le conteneur CI n'a pas git).
        assert "Diagnostic terminé" in resultat.stdout
        assert resultat.returncode == 0
        # L'étape `hooks` n'a pas non plus touché à la configuration git du dépôt.
        hooks = subprocess.run(  # noqa: S603
            [GIT, "config", "core.hooksPath"], cwd=depot.racine, capture_output=True, text=True
        )
        assert hooks.stdout.strip() == ""


def test_check_annonce_ce_qu_il_ferait_sans_le_faire(depot: Depot) -> None:
    resultat = depot.lance("--check", "--only", "env,mcp", "--no-install")

    assert statut(resultat.stdout, "env") == "IGNORÉ"
    assert "--check : rien écrit" in detail(resultat.stdout, "env")
    assert statut(resultat.stdout, "mcp") == "IGNORÉ"
    assert "--check : rien écrit" in detail(resultat.stdout, "mcp")
    assert not depot.env.exists()
    assert not depot.reglages.exists()


# --- Idempotence ----------------------------------------------------------------------------


def test_deuxieme_passage_ne_modifie_rien(depot: Depot) -> None:
    premier = depot.lance("--only", "env,mcp", "--no-install")
    assert statut(premier.stdout, "env") == "OK"
    assert statut(premier.stdout, "mcp") == "OK"
    apres_le_premier = depot.empreinte()

    second = depot.lance("--only", "env,mcp", "--no-install")

    assert second.returncode == 0
    assert statut(second.stdout, "env") == "DÉJÀ FAIT"
    assert statut(second.stdout, "mcp") == "DÉJÀ FAIT"
    assert depot.empreinte() == apres_le_premier


@besoin_git
def test_hooks_deuxieme_passage_deja_fait(depot: Depot) -> None:
    premier = depot.lance("--only", "hooks", "--no-install")
    assert statut(premier.stdout, "hooks") == "OK"

    second = depot.lance("--only", "hooks", "--no-install")

    assert statut(second.stdout, "hooks") == "DÉJÀ FAIT"
    assert GIT is not None
    hooks = subprocess.run(  # noqa: S603
        [GIT, "config", "core.hooksPath"], cwd=depot.racine, capture_output=True, text=True
    )
    assert hooks.stdout.strip() == "scripts/git/hooks"


# --- .env : création puis préservation --------------------------------------------------------


def test_env_est_cree_depuis_le_gabarit(depot: Depot) -> None:
    resultat = depot.lance("--only", "env", "--no-install")

    assert statut(resultat.stdout, "env") == "OK"
    assert depot.env.read_text(encoding="utf-8") == GABARIT_ENV
    # Le .env fraîchement copié est vide de valeurs : c'est un geste humain, annoncé.
    assert "Renseigner .env" in resultat.stdout


def test_env_existant_n_est_jamais_ecrase(depot: Depot) -> None:
    depot.ecrire(".env", "CLAUDE_AUTH_MODE=subscription\nANTHROPIC_API_KEY=sk-a-moi\n")
    avant = depot.env.read_bytes()

    resultat = depot.lance("--only", "env", "--no-install")

    assert resultat.returncode == 0
    assert depot.env.read_bytes() == avant
    assert statut(resultat.stdout, "env") == "DÉJÀ FAIT"
    assert "préservé" in detail(resultat.stdout, "env")


def test_env_signale_les_cles_du_gabarit_qui_manquent(depot: Depot) -> None:
    depot.ecrire(".env", "CLAUDE_AUTH_MODE=subscription\n")

    resultat = depot.lance("--only", "env", "--no-install")

    ligne = detail(resultat.stdout, "env")
    assert "ANTHROPIC_API_KEY" in ligne
    assert "GITLAB_TOKEN" in ligne
    assert "CLAUDE_AUTH_MODE" not in ligne  # déjà présente, rien à signaler
    assert "Compléter .env" in resultat.stdout


# --- .claude/settings.local.json : fusion, pas remplacement -------------------------------------


def test_reglages_locaux_sont_crees_avec_les_serveurs_du_depot(depot: Depot) -> None:
    resultat = depot.lance("--only", "mcp", "--no-install")

    assert statut(resultat.stdout, "mcp") == "OK"
    reglages = depot.json_reglages()
    assert sorted(reglages["enabledMcpjsonServers"]) == ["serveur-a", "serveur-b"]
    assert reglages["env"]["MAESTRO_CHROME_PROFILE"]


def test_reglages_locaux_sont_fusionnes_sans_rien_perdre(depot: Depot) -> None:
    depot.ecrire(
        ".claude/settings.local.json",
        json.dumps(
            {
                "env": {"MA_CLE": "à moi", "MAESTRO_CHROME_PROFILE": "/profil/choisi"},
                "enabledMcpjsonServers": ["serveur-perso"],
                "permissions": {"allow": ["Bash(ls:*)"]},
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
    )

    resultat = depot.lance("--only", "mcp", "--no-install")

    assert statut(resultat.stdout, "mcp") == "OK"
    reglages = depot.json_reglages()
    # Rien de préexistant n'a bougé…
    assert reglages["env"]["MA_CLE"] == "à moi"
    assert reglages["env"]["MAESTRO_CHROME_PROFILE"] == "/profil/choisi"
    assert reglages["permissions"] == {"allow": ["Bash(ls:*)"]}
    # … et les serveurs du dépôt se sont ajoutés à ceux qui étaient déjà là.
    assert reglages["enabledMcpjsonServers"] == ["serveur-perso", "serveur-a", "serveur-b"]


def test_le_profil_navigateur_du_env_prend_le_pas(depot: Depot) -> None:
    # Seule exception à « on n'écrase jamais » : les clés PILOTÉES PAR LE .env, pour qu'une
    # rotation de valeur se propage au lieu de rester lettre morte.
    depot.ecrire(".env", "MAESTRO_CHROME_PROFILE=/profil/du/env\n")
    depot.ecrire(
        ".claude/settings.local.json",
        json.dumps({"env": {"MAESTRO_CHROME_PROFILE": "/ancien/profil", "MA_CLE": "gardée"}})
        + "\n",
    )

    resultat = depot.lance("--only", "mcp", "--no-install")

    reglages = depot.json_reglages()
    assert reglages["env"]["MAESTRO_CHROME_PROFILE"] == "/profil/du/env"
    assert reglages["env"]["MA_CLE"] == "gardée"
    # Aucune VALEUR ne doit transiter par la sortie du script — seulement des noms de clés.
    assert "/profil/du/env" not in resultat.stdout


# --- Prérequis manquants : diagnostic, sans installation ---------------------------------------


def test_prerequis_absent_donne_la_commande_d_installation(
    depot: Depot, path_sans_python: str, systeme: str
) -> None:
    resultat = depot.lance("--only", "prerequis", "--no-install", path=path_sans_python)

    # python est un prérequis DUR : son absence fait sortir en code non nul.
    assert resultat.returncode == 1
    assert statut(resultat.stdout, "prerequis") == "ÉCHEC"
    assert "python" in detail(resultat.stdout, "prerequis")
    assert INSTALL_PYTHON[systeme] in resultat.stdout


def test_prerequis_en_check_n_installe_rien(
    depot: Depot, path_sans_python: str, tmp_path: Path
) -> None:
    # Faux gestionnaires de paquets : s'ils sont appelés, ils laissent une trace.
    fauxpm = tmp_path / "fauxpm"
    fauxpm.mkdir()
    temoin = tmp_path / "installation-lancee"
    for nom in ("winget", "brew", "apt-get"):
        shim = fauxpm / nom
        shim.write_text(
            f'#!/usr/bin/env bash\necho "$@" >> "{temoin.as_posix()}"\n',
            encoding="utf-8",
            newline="\n",
        )
        shim.chmod(0o755)

    resultat = depot.lance(
        "--only",
        "prerequis",
        "--check",
        env={"MAESTRO_AUTO_INSTALL": "1"},  # installation ACTIVE : c'est --check qui retient
        path=f"{fauxpm.as_posix()}:{path_sans_python}",
    )

    assert not temoin.exists(), f"un gestionnaire de paquets a été appelé : {resultat.stdout}"
    assert "--check : rien fait" in resultat.stdout


# --- Volet Docker / CI : délégué, et sauté quand il n'est pas là ---------------------------------


def test_etape_runner_sautee_si_le_script_dedie_est_absent(depot: Depot) -> None:
    # Le mini-clone ne porte pas scripts/gitlab/setup-runner.sh : l'étape doit se sauter
    # proprement, sans jamais toucher à Docker ni à GitLab.
    assert not (depot.racine / "scripts" / "gitlab" / "setup-runner.sh").exists()

    resultat = depot.lance("--only", "runner", "--no-install")

    assert resultat.returncode == 0
    assert statut(resultat.stdout, "runner") == "IGNORÉ"
    assert "introuvable" in detail(resultat.stdout, "runner")


# --- Dérive des dépendances : `--derive` (#216) ------------------------------------------------
# « Ce clone a-t-il pris les dépendances ajoutées au dépôt depuis sa mise en route ? » Le mode
# expose la détection que les étapes faisaient déjà pour leur propre compte, sous une forme
# qu'un script peut lire : TSV sur stdout, verdict dans le code de sortie (0 à jour / 3 dérive).
# C'est ce qui permet à `worktree.sh ensure` — donc à tout /ticket-start — de réparer sans
# réimplémenter pip ni npm, et à `ci/local.sh` de signaler sans rien installer.

DERIVE_A_JOUR = 0
DERIVE_DETECTEE = 3


def date_fichier(chemin: Path, quand: float) -> None:
    """Impose la date d'un fichier — la dérive se mesure en dates, pas en contenus."""
    os.utime(chemin, (quand, quand))


def prepare_venv(depot: Depot, *, a_jour: bool = True) -> Path:
    """Un `.venv` déjà installé (témoin posé) et un `pyproject.toml` daté de part et d'autre.

    Tout se passe dans le PASSÉ : une réparation repose le témoin à l'instant présent, qui doit
    alors être postérieur au `pyproject.toml` — un fichier daté du futur ne dériverait jamais.
    """
    installation = os.stat(depot.racine).st_mtime - 3600
    pyproject = depot.ecrire("pyproject.toml", '[project]\nname = "maestro"\n')
    temoin = depot.racine / ".venv" / ".maestro-setup-stamp"
    temoin.parent.mkdir(parents=True, exist_ok=True)
    temoin.write_text("2026-08-04T00:00:00Z\n", encoding="utf-8", newline="\n")
    date_fichier(temoin, installation)
    date_fichier(pyproject, installation + (-60 if a_jour else 60))
    return pyproject


def lignes_derive(resultat: subprocess.CompletedProcess[str]) -> dict[str, str]:
    """Le TSV de `--derive`, en table « étape → raison »."""
    table: dict[str, str] = {}
    for ligne in resultat.stdout.splitlines():
        if not ligne.strip():
            continue
        etape, _, raison = ligne.partition("\t")
        assert raison, f"ligne non tabulée : {ligne!r}"
        table[etape] = raison
    return table


def test_derive_muette_quand_le_clone_est_a_jour(depot: Depot) -> None:
    """Rien à dire = rien à lire : le silence est ce qui rend l'appel gratuit à chaque ticket."""
    prepare_venv(depot)

    resultat = depot.lance("--derive")

    assert resultat.returncode == DERIVE_A_JOUR, resultat.stdout + resultat.stderr
    assert resultat.stdout == ""


def test_derive_signale_un_pyproject_plus_recent_que_l_installation(depot: Depot) -> None:
    """Le cas qui a motivé le ticket : #214 ajoute pytest-xdist, ce clone ne l'a pas."""
    prepare_venv(depot, a_jour=False)

    resultat = depot.lance("--derive")

    assert resultat.returncode == DERIVE_DETECTEE
    assert "pyproject.toml" in lignes_derive(resultat)["venv"]


def test_derive_signale_un_venv_jamais_installe_par_le_script(depot: Depot) -> None:
    depot.ecrire("pyproject.toml", '[project]\nname = "maestro"\n')

    resultat = depot.lance("--derive")

    assert resultat.returncode == DERIVE_DETECTEE
    assert "témoin" in lignes_derive(resultat)["venv"]


def test_derive_signale_le_lockfile_npm(depot: Depot) -> None:
    prepare_venv(depot)
    depot.ecrire("apps/web/package.json", '{"name": "web"}\n')
    node_modules = depot.racine / "apps" / "web" / "node_modules"
    node_modules.mkdir(parents=True)
    lock = depot.ecrire("apps/web/package-lock.json", '{"lockfileVersion": 3}\n')
    date_fichier(lock, os.stat(node_modules).st_mtime + 60)

    resultat = depot.lance("--derive")

    assert resultat.returncode == DERIVE_DETECTEE
    assert "package-lock.json" in lignes_derive(resultat)["web"]


def test_derive_signale_la_version_de_node_epinglee(depot: Depot) -> None:
    """`.node-version` se compare par CONTENU, pas par date : le verdict vaut aussi en worktree."""
    prepare_venv(depot)
    depot.ecrire(".node-version", "20.19.0\n")

    resultat = depot.lance("--derive")

    assert resultat.returncode == DERIVE_DETECTEE
    assert "20.19.0" in lignes_derive(resultat)["node"]


def test_derive_n_ecrit_rien_et_n_installe_rien(depot: Depot) -> None:
    """Une sonde appelée à chaque /ticket-start et avant chaque filet local ne doit RIEN faire."""
    prepare_venv(depot, a_jour=False)
    depot.ecrire(".node-version", "20.19.0\n")
    avant = depot.empreinte()

    resultat = depot.lance("--derive", env={"MAESTRO_AUTO_INSTALL": "1"})

    assert resultat.returncode == DERIVE_DETECTEE
    assert depot.empreinte() == avant
    assert not depot.env.exists()


def test_derive_se_tait_apres_reparation_par_setup(depot: Depot) -> None:
    """La boucle complète : dérive détectée → `setup.sh --only venv` → plus de dérive.

    La réparation est celle du script, pas une réimplémentation : c'est l'étape `venv` qui
    rejoue `pip install -e ".[dev]"` (ici un shim — ni réseau ni vrai pip) et repose le témoin.
    """
    prepare_venv(depot, a_jour=False)
    for relatif in (".venv/Scripts/python.exe", ".venv/bin/python"):
        shim = depot.racine / relatif
        shim.parent.mkdir(parents=True, exist_ok=True)
        shim.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
        shim.chmod(0o755)

    assert depot.lance("--derive").returncode == DERIVE_DETECTEE

    repare = depot.lance("--only", "venv", "--no-install")
    assert repare.returncode == 0, repare.stdout + repare.stderr
    assert statut(repare.stdout, "venv") == "OK"

    assert depot.lance("--derive").returncode == DERIVE_A_JOUR


def test_derive_ignore_ce_que_le_depot_ne_porte_pas(depot: Depot) -> None:
    """Pas d'`apps/web`, pas de `.node-version` : deux silences, pas deux fausses alertes."""
    prepare_venv(depot)
    assert not (depot.racine / "apps").exists()
    assert not (depot.racine / ".node-version").exists()

    resultat = depot.lance("--derive")

    assert resultat.returncode == DERIVE_A_JOUR
    assert "web" not in resultat.stdout
    assert "node" not in resultat.stdout
