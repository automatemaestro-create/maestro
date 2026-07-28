"""Tests du parcours « un worktree par ticket » — `scripts/git/worktree.sh` (ticket #152).

Même principe que [`test_setup.py`](test_setup.py) : un **dépôt jetable** monté dans `tmp_path`,
sur lequel le vrai script est lancé. Rien n'est jamais écrit dans le dépôt de travail — `HOME` et
l'emplacement des worktrees (`MAESTRO_WORKTREE_DIR`) sont eux aussi redirigés vers `tmp_path`.

**Ni réseau ni glab.** Le dépôt jetable a son propre `origin` (un dépôt *bare* local), et la
branche est toujours imposée par `--branche` : la seule étape qui interroge GitLab
(`lib.sh branch-for`, qui résout le nom depuis le ticket) est ainsi contournée. Ce qui est testé
ici, c'est la mécanique git + l'équipement du worktree, pas la résolution du nom de branche.

Ce que ces tests épinglent, parce que c'est exactement ce qui casse quand deux sessions
travaillent en parallèle : des **ports** et un **profil de navigateur** distincts par worktree,
un `.env` présent, des artefacts lourds partagés, et une branche que le retrait du worktree
**ne supprime pas**.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
GIT = shutil.which("git")

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash introuvable"),
    pytest.mark.skipif(GIT is None, reason="git introuvable"),
]

BRANCHE = "chore/152-essai"
CONTENU_ENV = "CLAUDE_AUTH_MODE=subscription\nGITLAB_TOKEN=jeton-de-test\n"

# Réglages Claude Code du clone principal : ce dont le worktree doit hériter (les serveurs MCP
# approuvés) et ce qu'il doit au contraire remplacer (le profil du navigateur).
REGLAGES_PRINCIPAL = {
    "env": {"MAESTRO_CHROME_PROFILE": "C:\\profil\\principal"},
    "enabledMcpjsonServers": ["chrome-maestro", "figma-officiel"],
}


@dataclass
class Depot:
    """Clone principal jetable, avec son `origin` local et son dossier de worktrees."""

    racine: Path
    origin: Path
    worktrees: Path
    home: Path
    fauxbin: Path

    # --- exécution ---
    def lance(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        """Lance `bash scripts/git/worktree.sh <args>` (depuis le clone principal par défaut)."""
        return self._bash("scripts/git/worktree.sh", *args, cwd=cwd)

    def lib(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        """Lance `bash scripts/gitlab/lib.sh <args>`."""
        return self._bash("scripts/gitlab/lib.sh", *args, cwd=cwd)

    def _bash(self, script: str, *args: str, cwd: Path | None) -> subprocess.CompletedProcess[str]:
        environnement = os.environ.copy()
        # Le profil et les ports viennent parfois de la machine (bloc `env` des réglages Claude
        # Code de ce dépôt-ci) : on repart d'une base neutre.
        for cle in ("MAESTRO_CHROME_PROFILE", "MAESTRO_PORT_API", "MAESTRO_PORT_UI"):
            environnement.pop(cle, None)
        environnement["HOME"] = str(self.home)
        environnement["MAESTRO_WORKTREE_DIR"] = str(self.worktrees)
        environnement["PATH"] = os.pathsep.join(
            [str(self.fauxbin), environnement.get("PATH", "")]
        )
        assert BASH is not None
        # Le script est appelé par son chemin DANS le clone principal : c'est lui qui porte les
        # artefacts à partager, quel que soit le répertoire depuis lequel on lance.
        return subprocess.run(  # noqa: S603
            [BASH, str(self.racine / script), *args],
            cwd=str(cwd or self.racine),
            env=environnement,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )

    def git(self, *args: str, cwd: Path | None = None) -> str:
        assert GIT is not None
        acheve = subprocess.run(  # noqa: S603
            [GIT, *args],
            cwd=str(cwd or self.racine),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return acheve.stdout.strip()

    # --- raccourcis ---
    def worktree(self, nom: str = "152-essai") -> Path:
        return self.worktrees / nom

    def reglages(self, nom: str = "152-essai") -> dict:
        fichier = self.worktree(nom) / ".claude" / "settings.local.json"
        return json.loads(fichier.read_text(encoding="utf-8"))


@pytest.fixture
def depot(tmp_path: Path) -> Depot:
    """Monte un clone principal jetable : vrais scripts, faux contenu, `origin` local."""
    assert GIT is not None
    origin = tmp_path / "origin.git"
    racine = tmp_path / "principal"
    worktrees = tmp_path / "worktrees"
    home = tmp_path / "home"
    fauxbin = tmp_path / "fauxbin"
    for dossier in (home, fauxbin):
        dossier.mkdir()

    def git(*args: str, cwd: Path) -> None:
        subprocess.run(  # noqa: S603
            [GIT, *args], cwd=str(cwd), check=True, capture_output=True
        )

    origin.mkdir()
    git("init", "--bare", "--quiet", "--initial-branch=main", cwd=origin)

    racine.mkdir()
    git("init", "--quiet", "--initial-branch=main", cwd=racine)
    git("config", "user.email", "test@maestro.invalid", cwd=racine)
    git("config", "user.name", "Maestro Test", cwd=racine)

    # Les vrais scripts, dans la vraie arborescence (worktree.sh appelle lib.sh en relatif).
    for relatif in ("scripts/git/worktree.sh", "scripts/gitlab/lib.sh"):
        cible = racine / relatif
        cible.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RACINE / relatif, cible)

    # Contenu gitignoré que le worktree doit recevoir (copie) ou partager (lien).
    (racine / ".env").write_text(CONTENU_ENV, encoding="utf-8", newline="\n")
    (racine / ".claude").mkdir()
    (racine / ".claude" / "settings.local.json").write_text(
        json.dumps(REGLAGES_PRINCIPAL, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    for lourd in (".venv", ".tools", "apps/web/node_modules"):
        dossier = racine / lourd
        dossier.mkdir(parents=True)
        (dossier / "marqueur.txt").write_text(lourd, encoding="utf-8", newline="\n")

    (racine / ".gitignore").write_text(
        ".env\n.venv/\n.tools/\nnode_modules/\n.claude/settings.local.json\n",
        encoding="utf-8",
        newline="\n",
    )
    (racine / "README.md").write_text("dépôt jetable\n", encoding="utf-8", newline="\n")

    git("add", "-A", cwd=racine)
    git("-c", "core.hooksPath=", "commit", "--quiet", "-m", "chore: dépôt jetable", cwd=racine)
    git("remote", "add", "origin", str(origin), cwd=racine)
    git("push", "--quiet", "-u", "origin", "main", cwd=racine)

    # Shim `python3` vers l'interpréteur de pytest : le script écrit les réglages Claude Code en
    # Python et cherche d'abord le venv du dépôt — absent ici (c'est un dossier factice).
    interpreteur = sys.executable.replace("\\", "/")
    shim = fauxbin / "python3"
    shim.write_text(
        f'#!/usr/bin/env bash\nexec "{interpreteur}" "$@"\n', encoding="utf-8", newline="\n"
    )
    shim.chmod(0o755)

    return Depot(
        racine=racine, origin=origin, worktrees=worktrees, home=home, fauxbin=fauxbin
    )


# --- Création ------------------------------------------------------------------------------


def test_creation_monte_un_worktree_equipe(depot: Depot) -> None:
    """Le worktree est créé sur sa branche, avec .env, liens et réglages dédiés."""
    acheve = depot.lance("create", "152", "--branche", BRANCHE)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    wt = depot.worktree()
    # À la racine d'un worktree lié, `.git` est un FICHIER (« gitdir: … »).
    assert (wt / ".git").is_file()
    assert depot.git("branch", "--show-current", cwd=wt) == BRANCHE
    # Le clone principal, lui, n'a pas bougé de main.
    assert depot.git("branch", "--show-current") == "main"

    assert (wt / ".env").read_text(encoding="utf-8") == CONTENU_ENV
    # Artefacts partagés : le lien traverse jusqu'au contenu du clone principal.
    for lourd in (".venv", ".tools"):
        assert (wt / lourd / "marqueur.txt").read_text(encoding="utf-8") == lourd


def test_node_modules_n_est_jamais_un_lien(depot: Depot) -> None:
    """Turbopack rejette un `node_modules` lié — « it points out of the filesystem root ».

    L'UI ne démarre alors pas du tout : ces dépendances-là s'installent sur place (délégué à
    `scripts/setup.sh`, absent du dépôt jetable — c'est le refus de lier qui est testé ici).
    """
    acheve = depot.lance("create", "152", "--branche", BRANCHE)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    node_modules = depot.worktree() / "apps" / "web" / "node_modules"
    assert not node_modules.is_symlink()
    assert "apps/web" in acheve.stdout      # l'étape est rapportée, pas passée sous silence


def test_ports_et_profil_sont_propres_au_worktree(depot: Depot) -> None:
    """Ce qui ferait se télescoper deux sessions est distinct ; le reste est hérité."""
    depot.lance("create", "152", "--branche", BRANCHE)
    reglages = depot.reglages()

    # 152 mod 100 = 52.
    assert reglages["env"]["MAESTRO_PORT_API"] == "8052"
    assert reglages["env"]["MAESTRO_PORT_UI"] == "3052"
    assert "chrome-profile-152" in reglages["env"]["MAESTRO_CHROME_PROFILE"]
    assert reglages["env"]["MAESTRO_CHROME_PROFILE"] != (
        REGLAGES_PRINCIPAL["env"]["MAESTRO_CHROME_PROFILE"]
    )
    # …mais l'approbation des serveurs MCP, elle, est héritée du clone principal.
    assert reglages["enabledMcpjsonServers"] == REGLAGES_PRINCIPAL["enabledMcpjsonServers"]


def test_ports_imposes(depot: Depot) -> None:
    depot.lance("create", "152", "--branche", BRANCHE, "--ports", "8123:3123")
    reglages = depot.reglages()
    assert reglages["env"]["MAESTRO_PORT_API"] == "8123"
    assert reglages["env"]["MAESTRO_PORT_UI"] == "3123"


def test_iid_multiple_de_cent_ne_retombe_pas_sur_les_ports_du_principal(depot: Depot) -> None:
    """200 mod 100 = 0 : sans garde-fou, le worktree écouterait sur 8000/3000."""
    depot.lance("create", "200", "--branche", "chore/200-essai")
    reglages = depot.reglages("200-essai")
    assert reglages["env"]["MAESTRO_PORT_API"] == "8100"
    assert reglages["env"]["MAESTRO_PORT_UI"] == "3100"


def test_second_passage_idempotent_et_non_destructif(depot: Depot) -> None:
    """Relancer complète sans rien casser — et sans écraser les réglages du worktree."""
    depot.lance("create", "152", "--branche", BRANCHE)
    fichier = depot.worktree() / ".claude" / "settings.local.json"
    reglages = json.loads(fichier.read_text(encoding="utf-8"))
    reglages["env"]["AJOUT_MANUEL"] = "à préserver"
    fichier.write_text(json.dumps(reglages, indent=2) + "\n", encoding="utf-8", newline="\n")

    acheve = depot.lance("create", "152", "--branche", BRANCHE)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "déjà en place" in acheve.stdout

    apres = depot.reglages()
    assert apres["env"]["AJOUT_MANUEL"] == "à préserver"
    assert apres["env"]["MAESTRO_PORT_API"] == "8052"
    assert (depot.worktree() / ".env").read_text(encoding="utf-8") == CONTENU_ENV


def test_iid_non_numerique_refuse(depot: Depot) -> None:
    acheve = depot.lance("create", "abc")
    assert acheve.returncode == 2
    assert "IID de ticket attendu" in acheve.stderr


def test_dossier_occupe_refuse(depot: Depot) -> None:
    """Un dossier déjà là et qui n'est pas un worktree n'est jamais écrasé."""
    occupe = depot.worktree()
    occupe.mkdir(parents=True)
    (occupe / "important.txt").write_text("ne pas perdre", encoding="utf-8", newline="\n")

    acheve = depot.lance("create", "152", "--branche", BRANCHE)
    assert acheve.returncode == 1
    assert "existe déjà sans être un worktree" in acheve.stderr
    assert (occupe / "important.txt").read_text(encoding="utf-8") == "ne pas perdre"


# --- Inventaire et retrait -------------------------------------------------------------------


def test_list_montre_le_principal_et_les_worktrees(depot: Depot) -> None:
    depot.lance("create", "152", "--branche", BRANCHE)
    acheve = depot.lance("list")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert BRANCHE in acheve.stdout
    assert "8052/3052" in acheve.stdout
    assert "8000/3000" in acheve.stdout      # le clone principal garde les ports par défaut


def test_remove_retire_le_worktree_mais_garde_la_branche(depot: Depot) -> None:
    """Supprimer une branche reste le monopole de /branch-cleanup, après merge confirmé."""
    depot.lance("create", "152", "--branche", BRANCHE)
    acheve = depot.lance("remove", "152")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    assert not depot.worktree().exists()
    assert BRANCHE in depot.git("branch", "--list", BRANCHE)


def test_remove_ne_vide_pas_les_artefacts_du_clone_principal(depot: Depot) -> None:
    """Régression : les artefacts partagés sont des **jonctions** sous Windows.

    Un retrait qui ne délie pas d'abord descend dedans et vide le `.venv` et le
    `node_modules` du clone principal — c'est arrivé pendant le développement de #152.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    acheve = depot.lance("remove", "152")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    for lourd in (".venv", ".tools", "apps/web/node_modules"):
        marqueur = depot.racine / lourd / "marqueur.txt"
        assert marqueur.is_file(), f"{lourd} du clone principal amputé par le retrait"
        assert marqueur.read_text(encoding="utf-8") == lourd


def test_remove_refuse_un_worktree_au_travail(depot: Depot) -> None:
    """Un worktree qui porte des changements non commités n'est pas retiré par surprise."""
    depot.lance("create", "152", "--branche", BRANCHE)
    (depot.worktree() / "README.md").write_text("modifié", encoding="utf-8", newline="\n")

    acheve = depot.lance("remove", "152")
    assert acheve.returncode == 1
    assert "changements non commités" in acheve.stderr
    assert depot.worktree().exists()
    # Et rien n'a été délié au passage.
    assert (depot.worktree() / ".venv" / "marqueur.txt").is_file()


def test_remove_vise_le_worktree_meme_si_le_principal_porte_le_meme_iid(depot: Depot) -> None:
    """Cas courant : on ouvre un worktree depuis le ticket sur lequel on travaille déjà.

    Le clone principal est alors sur `<type>/152-…` lui aussi, et il est listé en premier —
    le retenir ferait échouer le retrait sur « is a main working tree ».
    """
    depot.lib("start-branch", "chore/152-travaux-en-cours")
    depot.lance("create", "152", "--branche", BRANCHE)

    acheve = depot.lance("remove", "152")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert not depot.worktree().exists()
    assert depot.git("branch", "--show-current") == "chore/152-travaux-en-cours"


def test_remove_refuse_le_clone_principal(depot: Depot) -> None:
    acheve = depot.lance("remove", str(depot.racine))
    assert acheve.returncode == 1
    assert "ne se retire pas" in acheve.stderr


# --- Branche de travail (lib.sh start-branch) ------------------------------------------------


def test_start_branch_cree_depuis_main_dans_le_clone_principal(depot: Depot) -> None:
    acheve = depot.lib("start-branch", "chore/999-autre")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert depot.git("branch", "--show-current") == "chore/999-autre"


def test_start_branch_ne_touche_pas_a_main_depuis_un_worktree(depot: Depot) -> None:
    """`main` est emprunté par le clone principal : un `git checkout main` y échouerait."""
    depot.lance("create", "152", "--branche", BRANCHE)
    wt = depot.worktree()

    # Déjà sur la bonne branche (cas normal juste après la création) : rien à faire.
    acheve = depot.lib("start-branch", BRANCHE, cwd=wt)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "Déjà sur" in acheve.stdout

    # Un autre ticket depuis ce même worktree : la branche part d'origin/main, sans détour.
    acheve = depot.lib("start-branch", "chore/153-suite", cwd=wt)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert depot.git("branch", "--show-current", cwd=wt) == "chore/153-suite"
    assert depot.git("branch", "--show-current") == "main"


def test_start_branch_refuse_une_branche_sans_prefixe(depot: Depot) -> None:
    """`<type>/…` est le marqueur d'un ticket sans label type:: — pas un nom de branche."""
    acheve = depot.lib("start-branch", "<type>/152-essai")
    assert acheve.returncode == 2
    assert "déduire le type" in acheve.stderr
