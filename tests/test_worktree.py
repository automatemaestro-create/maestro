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
    verdicts: dict[str, str] | None = None

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
        # Ramassage des worktrees (#197) : désactivé par défaut dans les tests de création — il
        # interrogerait GitLab, absent d'ici. Les tests du ramassage le rallument avec un verdict
        # imposé (`impose_verdicts`), seule couture par laquelle ils disent ce qui est « soldé ».
        environnement["MAESTRO_WORKTREE_GC"] = "0"
        if self.verdicts is not None:
            environnement.pop("MAESTRO_WORKTREE_GC")
            environnement["MAESTRO_WORKTREE_VERDICT"] = str(self.fauxbin / "verdict")
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

    # --- couture du ramassage (#197) ---
    def impose_verdicts(self, verdicts: dict[str, str]) -> None:
        """Impose la réponse de `lib.sh worktree-done` pour chaque iid — et rallume le ramassage.

        Valeur : la ligne de verdict telle que `gc` la lit, en TSV —
        « <fini|actif|inconnu><TAB><sha de merge><TAB><raison> ». Un iid absent de la table rend une
        ligne vide, ce que `gc` doit traiter comme « je ne sais pas », donc « je n'y touche pas ».
        """
        self.verdicts = dict(verdicts)
        table = self.fauxbin / "verdicts.tsv"
        table.write_text(
            "".join(f"{iid}\t{ligne}\n" for iid, ligne in self.verdicts.items()),
            encoding="utf-8",
            newline="\n",
        )
        shim = self.fauxbin / "verdict"
        shim.write_text(
            "#!/usr/bin/env bash\n"
            f"awk -F'\\t' -v iid=\"$1\" 'iid == $1 {{ print $2 \"\\t\" $3 \"\\t\" $4; exit }}'"
            f' "{str(table).replace(chr(92), "/")}"\n',
            encoding="utf-8",
            newline="\n",
        )
        shim.chmod(0o755)

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


# --- Aiguillage `ensure` (#181) -------------------------------------------------------------
# `/ticket-start` ne bascule plus la branche du répertoire courant : il demande à `ensure` OÙ la
# session doit travailler, et s'y relocalise. Trois situations d'appel, deux verdicts — et c'est
# la dernière ligne de stdout qui les porte, pour que l'appelant n'ait pas à lire le rapport.


def _verdict(acheve: subprocess.CompletedProcess[str]) -> str:
    """La dernière ligne non vide de stdout — le contrat de sortie de `ensure`."""
    lignes = [ligne for ligne in acheve.stdout.splitlines() if ligne.strip()]
    return lignes[-1] if lignes else ""


def test_ensure_depuis_le_clone_principal_monte_le_worktree_et_ne_bouge_pas_main(
    depot: Depot,
) -> None:
    """Le cas nominal, et la raison d'être du ticket : `main` ne doit plus changer de branche."""
    acheve = depot.lance("ensure", "152", "--branche", BRANCHE)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    verdict = _verdict(acheve)
    assert verdict.startswith("WORKTREE "), f"verdict inattendu : {verdict!r}"
    assert Path(verdict[len("WORKTREE ") :]).resolve() == depot.worktree().resolve()

    assert depot.git("branch", "--show-current") == "main", (
        "le clone principal doit rester où il était — c'est tout l'objet de #181"
    )
    assert depot.git("branch", "--show-current", cwd=depot.worktree()) == BRANCHE


def test_ensure_depuis_le_worktree_du_ticket_ne_monte_rien(depot: Depot) -> None:
    """Le cas d'`orchestrate/run.sh`, qui monte le worktree lui-même avant d'y lancer la session :
    un second worktree y serait une régression franche."""
    depot.lance("create", "152", "--branche", BRANCHE)
    wt = depot.worktree()
    avant = depot.git("worktree", "list", "--porcelain").count("worktree ")

    acheve = depot.lance("ensure", "152", "--branche", BRANCHE, cwd=wt)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    verdict = _verdict(acheve)
    assert verdict.startswith("ICI "), f"verdict inattendu : {verdict!r}"
    assert Path(verdict[len("ICI ") :]).resolve() == wt.resolve()

    apres = depot.git("worktree", "list", "--porcelain").count("worktree ")
    assert apres == avant, "aucun worktree ne doit être monté quand on est déjà au bon endroit"


def test_ensure_depuis_le_worktree_d_un_autre_ticket_monte_le_bon(depot: Depot) -> None:
    """L'emplacement se résout depuis le clone principal : il reste correct où qu'on appelle."""
    depot.lance("create", "152", "--branche", BRANCHE)
    autre_branche = "chore/153-autre"

    acheve = depot.lance("ensure", "153", "--branche", autre_branche, cwd=depot.worktree())
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    verdict = _verdict(acheve)
    assert verdict.startswith("WORKTREE ")
    assert Path(verdict[len("WORKTREE ") :]).resolve() == depot.worktree("153-autre").resolve()
    assert depot.git("branch", "--show-current", cwd=depot.worktree("153-autre")) == autre_branche
    assert depot.git("branch", "--show-current") == "main"


def test_ensure_annonce_les_ports_et_dit_qu_ils_ne_suivent_pas_la_relocalisation(
    depot: Depot,
) -> None:
    """Mesuré sur #181 : `EnterWorktree` ne réévalue pas le bloc `env`. Une session relocalisée
    garde donc les ports et le profil du clone principal — l'outil doit le dire, pas seulement
    la doc, parce que c'est au moment de démarrer la stack que ça mord."""
    acheve = depot.lance("ensure", "152", "--branche", BRANCHE)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    assert "8052" in acheve.stdout and "3052" in acheve.stdout, "les ports dédiés sont annoncés"
    assert "non hérités par une session relocalisée" in acheve.stdout


def test_creation_dit_ou_la_branche_est_deja_empruntee(depot: Depot) -> None:
    """git refuse la même branche dans deux worktrees — c'est un verrou utile, mais son message
    ne dit pas OÙ elle est prise. Ici le clone principal la tient : il doit être nommé."""
    depot.git("checkout", "--quiet", "-b", BRANCHE)

    acheve = depot.lance("create", "152", "--branche", BRANCHE)
    assert acheve.returncode == 1
    assert "déjà empruntée par le worktree" in acheve.stderr
    assert "principal" in acheve.stderr, "le chemin de l'emprunteur doit apparaître"


def test_ensure_refuse_un_iid_non_numerique(depot: Depot) -> None:
    acheve = depot.lance("ensure", "chore/152", "--branche", BRANCHE)
    assert acheve.returncode == 2
    assert "IID de ticket attendu" in acheve.stderr


# --- Ramassage `gc` (#197) -------------------------------------------------------------------
# Un worktree pèse ~535 Mo et #181 en a fait la voie par défaut : sans ramassage, ils s'accumulent
# (9 worktrees soldés constatés le 2026-07-30, ~4,8 Go). `gc` les retire — mais rien n'est plus
# cher qu'un ramassage trop zélé : les tests ci-dessous portent d'abord sur ses REFUS.
#
# La question « ce travail est-il soldé ? » est posée à GitLab via `lib.sh worktree-done`, remplacé
# ici par `Depot.impose_verdicts` : ni réseau, ni glab, ni écriture GitLab.


def _verdict_ligne(verdict: str, raison: str, sha: str = "-") -> str:
    """La ligne TSV que rend `lib.sh worktree-done` : « <verdict><TAB><sha><TAB><raison> ».

    Le sha vaut « - » quand il n'y en a pas — un champ VIDE serait avalé côté shell, où la
    tabulation est un séparateur blanc (deux d'affilée comptent pour une), et la raison prendrait
    la place du sha.
    """
    return f"{verdict}\t{sha}\t{raison}"


def _commit_local(depot: Depot, wt: Path, texte: str) -> str:
    """Commite dans le worktree SANS pousser, et rend le sha obtenu."""
    (wt / "travail.txt").write_text(texte, encoding="utf-8", newline="\n")
    depot.git("add", "-A", cwd=wt)
    depot.git("-c", "core.hooksPath=", "commit", "--quiet", "-m", "chore: travail", cwd=wt)
    return depot.git("rev-parse", "HEAD", cwd=wt)


def test_gc_retire_un_worktree_dont_la_mr_est_mergee(depot: Depot) -> None:
    """Le cas nominal — et la branche, elle, survit : sa suppression reste à /branch-cleanup."""
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("fini", "MR !42 mergée")})

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#152 retiré" in acheve.stdout
    assert "MR !42 mergée" in acheve.stdout
    assert not depot.worktree().exists()
    assert BRANCHE in depot.git("branch", "--list", BRANCHE)
    # Le clone principal n'est ni retiré, ni amputé de ses artefacts partagés (jonctions, #152).
    assert (depot.racine / ".git").is_dir()
    for lourd in (".venv", ".tools", "apps/web/node_modules"):
        assert (depot.racine / lourd / "marqueur.txt").read_text(encoding="utf-8") == lourd


def test_gc_ne_touche_pas_un_worktree_actif(depot: Depot) -> None:
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("actif", "ticket #152 « open » (MR « aucune »)")})

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#152 conservé" in acheve.stdout
    assert depot.worktree().exists()


def test_gc_ne_deduit_jamais_le_merge_du_nom_de_la_branche(depot: Depot) -> None:
    """Verdict « inconnu » (glab absent, hors ligne, ticket illisible) : on ne touche à rien.

    Ne rien savoir n'autorise rien — même garde-fou que `cleanup-merged` sur les branches
    (docs/10 §6) : `chore/152-…` a tout l'air d'un ticket clos, ce n'est pas une preuve.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("inconnu", "glab indisponible")})

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert depot.worktree().exists()
    assert "0 retiré(s)" in acheve.stdout


def test_gc_sans_verdict_du_tout_ne_retire_rien(depot: Depot) -> None:
    """Même exigence, cas dégradé : une réponse vide n'est pas un feu vert."""
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({})

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert depot.worktree().exists()


def test_gc_refuse_et_signale_un_travail_non_commite(depot: Depot) -> None:
    """Le ticket est soldé côté GitLab, mais le worktree porte des fichiers non commités.

    Mieux vaut 535 Mo de trop qu'un fichier perdu : on garde, et on le DIT (le silence serait le
    vrai défaut — personne ne va inspecter un worktree qu'il croit ramassé).
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    (depot.worktree() / "README.md").write_text("modifié", encoding="utf-8", newline="\n")
    depot.impose_verdicts({"152": _verdict_ligne("fini", "MR !42 mergée")})

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "non commité" in acheve.stdout
    assert depot.worktree().exists()
    # Rien n'a été délié au passage : le worktree reste utilisable tel quel.
    assert (depot.worktree() / ".venv" / "marqueur.txt").is_file()


def test_gc_refuse_et_signale_des_commits_non_pousses(depot: Depot) -> None:
    """Un commit qui n'est jamais parti vers `origin` n'existe que là : il n'est pas jetable."""
    depot.lance("create", "152", "--branche", BRANCHE)
    _commit_local(depot, depot.worktree(), "jamais poussé")
    depot.impose_verdicts({"152": _verdict_ligne("fini", "ticket #152 fermé (MR « aucune »)")})

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "non poussé" in acheve.stdout
    assert depot.worktree().exists()


def test_gc_retire_malgre_le_squash_grace_au_sha_de_merge(depot: Depot) -> None:
    """Le piège qui rendrait `gc` inutile : le projet merge en **squash**.

    Les commits de la branche ne sont donc jamais des ancêtres de `main`, et GitLab supprime la
    branche distante au merge : `origin/main..HEAD` compte le travail de TOUT worktree mergé, et un
    ramassage naïf refuserait chaque candidat. Le sha rendu par `worktree-done` (tête de la branche
    source au moment du merge) est la référence qui tranche.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    sha = _commit_local(depot, depot.worktree(), "mergé en squash")
    assert depot.git("rev-list", "--count", "origin/main..HEAD", cwd=depot.worktree()) == "1"
    depot.impose_verdicts({"152": _verdict_ligne("fini", "MR !42 mergée", sha)})

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#152 retiré" in acheve.stdout
    assert not depot.worktree().exists()


def test_gc_retire_une_branche_recreee_depuis_main_apres_son_merge(depot: Depot) -> None:
    """Le sha de merge a divergé, mais tout ce que porte le worktree est déjà sur `origin/main`.

    Cas concret : le ticket est clos, sa branche a été supprimée puis re-créée depuis `main` (un
    `/ticket-start` de trop, un worktree remonté). Comparer au sha de merge compterait alors comme
    « non poussés » des commits qui sont sur `main` — d'où la question posée en premier : HEAD
    est-il un ancêtre d'`origin/main` ?
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    wt = depot.worktree()
    sha = _commit_local(depot, wt, "parti au merge, sur une ligne qui a divergé")

    # `main` avance de son côté (le squash du travail, puis la suite) et le worktree repart de là.
    (depot.racine / "README.md").write_text("suite", encoding="utf-8", newline="\n")
    depot.git("add", "-A")
    depot.git("-c", "core.hooksPath=", "commit", "--quiet", "-m", "chore: suite")
    depot.git("push", "--quiet", "origin", "main")
    depot.git("fetch", "--quiet", "origin", cwd=wt)
    depot.git("reset", "--hard", "--quiet", "origin/main", cwd=wt)
    assert depot.git("rev-list", "--count", f"{sha}..HEAD", cwd=wt) != "0", (
        "le sha de merge doit bien avoir divergé, sinon le test ne prouve rien"
    )

    depot.impose_verdicts({"152": _verdict_ligne("fini", "MR !42 mergée", sha)})
    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#152 retiré" in acheve.stdout
    assert not depot.worktree().exists()


def test_gc_signale_un_commit_posterieur_au_merge(depot: Depot) -> None:
    """Symétrique du précédent : ce qui a été commité APRÈS le merge n'est nulle part ailleurs."""
    depot.lance("create", "152", "--branche", BRANCHE)
    sha = _commit_local(depot, depot.worktree(), "mergé en squash")
    _commit_local(depot, depot.worktree(), "ajouté après le merge")
    depot.impose_verdicts({"152": _verdict_ligne("fini", "MR !42 mergée", sha)})

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "1 commit(s) non poussé(s)" in acheve.stdout
    assert depot.worktree().exists()


def test_gc_ne_retire_jamais_le_worktree_de_la_session_courante(depot: Depot) -> None:
    """On ne se retire pas le sol sous les pieds — même quand GitLab dit que c'est soldé.

    Cas réel : `/ticket-ship` merge plus tard, mais la session tourne encore dans ce worktree ;
    et un `gc` lancé depuis un worktree ne doit pas se saborder.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("fini", "MR !42 mergée")})

    acheve = depot.lance("gc", cwd=depot.worktree())
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "session courante" in acheve.stdout
    assert depot.worktree().exists()


def test_gc_check_ne_retire_rien(depot: Depot) -> None:
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("fini", "MR !42 mergée")})

    acheve = depot.lance("gc", "--check")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#152 à retirer" in acheve.stdout
    assert "rien n'a été touché" in acheve.stdout
    assert depot.worktree().exists()


def test_gc_auto_est_muet_quand_il_n_y_a_rien_a_dire(depot: Depot) -> None:
    """Le mode câblé dans `ensure` : il ne s'annonce que s'il agit ou s'il alerte.

    Sans quoi chaque `/ticket-start` s'ouvrirait sur un inventaire dont personne n'a besoin.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("actif", "ticket #152 « open » (MR « aucune »)")})

    acheve = depot.lance("gc", "--auto")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert acheve.stdout.strip() == "", acheve.stdout


def test_gc_ignore_une_branche_hors_convention(depot: Depot) -> None:
    """Sans iid dans le nom, aucun ticket à interroger — donc aucune décision à prendre."""
    depot.lance("create", "152", "--branche", "experimentation")
    depot.impose_verdicts({"152": _verdict_ligne("fini", "MR !42 mergée")})

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "hors convention" in acheve.stdout
    assert depot.worktree("experimentation").exists()


def test_ensure_ramasse_les_worktrees_soldes_avant_de_monter_le_sien(depot: Depot) -> None:
    """Le câblage qui fait tout le ticket : plus aucun geste dédié à se rappeler (#197).

    `/ticket-start` appelle `ensure` — c'est le seul moment où quelqu'un passe forcément par ici.
    Et le verdict doit rester la DERNIÈRE ligne de stdout : le rapport de ramassage ne casse pas
    le contrat de sortie sur lequel /ticket-start s'appuie (#181).
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("fini", "MR !42 mergée")})

    acheve = depot.lance("ensure", "153", "--branche", "chore/153-suite")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    assert "#152 retiré" in acheve.stdout
    assert not depot.worktree().exists(), "le worktree soldé devait être ramassé au passage"
    verdict = _verdict(acheve)
    assert verdict.startswith("WORKTREE ")
    assert Path(verdict[len("WORKTREE ") :]).resolve() == depot.worktree("153-suite").resolve()


# --- Mise à jour de `main` (#205) --------------------------------------------------------------
# Depuis #181 la session travaille dans un worktree, donc plus personne ne repasse par `main` : la
# branche locale du clone principal prenait du retard à chaque merge sans que rien ne la rattrape.
# `lib.sh sync-main` la remet à niveau — en FAST-FORWARD SEULEMENT, et en s'abstenant dès qu'il y a
# le moindre doute, comme `behind-main` et `gc` : ça dit, ça ne casse pas.


def _avance_origin(depot: Depot, nom: str = "NOUVEAU.md") -> str:
    """Simule un merge côté serveur : `origin/main` avance, le clone local reste en arrière.

    Le commit est fabriqué depuis le clone principal (le seul répertoire qui ait `main`), poussé,
    puis **défait localement** — refs de suivi comprises. Le clone se retrouve exactement dans
    l'état de quelqu'un qui n'a pas encore vu le merge : seul un `fetch` peut le lui apprendre,
    ce qui met aussi celui de `sync-main` à l'épreuve.
    """
    avant = depot.git("rev-parse", "HEAD")
    (depot.racine / nom).write_text("du neuf sur main\n", encoding="utf-8", newline="\n")
    depot.git("add", nom)
    depot.git("-c", "core.hooksPath=", "commit", "--quiet", "-m", "feat: du neuf sur main")
    depot.git("push", "--quiet", "origin", "main")
    apres = depot.git("rev-parse", "HEAD")
    depot.git("reset", "--hard", "--quiet", avant)
    depot.git("update-ref", "refs/remotes/origin/main", avant)
    return apres


def test_sync_main_avance_main_du_clone_principal_depuis_un_worktree(depot: Depot) -> None:
    """Le cas nominal : la session est ailleurs, et `main` se remet quand même à jour.

    `main` étant empruntée par le clone principal, la ref ne suffit pas — l'index et les fichiers
    doivent suivre, sans quoi tout le delta apparaîtrait en « supprimé » dans ce répertoire.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    attendu = _avance_origin(depot)

    acheve = depot.lib("sync-main", cwd=depot.worktree())
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "main mis à jour : 1 commit(s)" in acheve.stdout
    assert depot.git("rev-parse", "main") == attendu
    assert (depot.racine / "NOUVEAU.md").exists(), "le répertoire de travail devait suivre la ref"
    assert depot.git("status", "--porcelain") == ""


def test_sync_main_est_muet_et_idempotent_quand_main_est_a_jour(depot: Depot) -> None:
    """Le cas de loin le plus fréquent : rien à faire, donc rien à dire.

    Sans ça, chaque `/ticket-start` s'ouvrirait sur une ligne de bruit — même exigence que le
    `gc --auto` du ticket #197.
    """
    for _ in range(2):
        acheve = depot.lib("sync-main")
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert acheve.stdout.strip() == "", acheve.stdout


def test_sync_main_s_abstient_si_le_repertoire_porteur_de_main_est_sale(depot: Depot) -> None:
    """Un `merge --ff-only` sur un arbre sale échouerait à mi-chemin : on n'essaie même pas."""
    depot.lance("create", "152", "--branche", BRANCHE)
    attendu_avant = depot.git("rev-parse", "main")
    _avance_origin(depot)
    (depot.racine / "README.md").write_text("travail en cours\n", encoding="utf-8", newline="\n")

    acheve = depot.lib("sync-main", cwd=depot.worktree())
    assert acheve.returncode == 4, acheve.stdout + acheve.stderr
    assert "changements non commités" in acheve.stderr
    assert depot.git("rev-parse", "main") == attendu_avant, "main ne devait pas bouger"


def test_sync_main_s_abstient_si_main_a_diverge(depot: Depot) -> None:
    """Un commit local jamais poussé : l'écraser serait une perte de données, jamais une synchro."""
    _avance_origin(depot)
    (depot.racine / "local.md").write_text("commit local\n", encoding="utf-8", newline="\n")
    depot.git("add", "local.md")
    depot.git("-c", "core.hooksPath=", "commit", "--quiet", "-m", "chore: commit local")
    divergent = depot.git("rev-parse", "main")

    acheve = depot.lib("sync-main")
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "divergé" in acheve.stderr
    assert depot.git("rev-parse", "main") == divergent, "main ne devait pas bouger"
    assert (depot.racine / "local.md").exists(), "le commit local devait survivre intact"


def test_sync_main_pose_la_ref_quand_main_n_est_empruntee_nulle_part(depot: Depot) -> None:
    """Sans répertoire de travail sur `main`, la ref se pose seule — aucun fichier touché.

    C'est ce qui rend l'appel valide même là où un `git checkout main` n'aurait aucun sens.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    attendu = _avance_origin(depot)
    depot.git("checkout", "--quiet", "-b", "autre")

    acheve = depot.lib("sync-main", cwd=depot.worktree())
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "main mis à jour : 1 commit(s)" in acheve.stdout
    assert depot.git("rev-parse", "main") == attendu
    assert depot.git("branch", "--show-current") == "autre", "on ne bascule sur rien"
    assert not (depot.racine / "NOUVEAU.md").exists(), "aucun fichier ne devait bouger"


def test_sync_main_check_ne_touche_a_rien(depot: Depot) -> None:
    attendu_avant = depot.git("rev-parse", "main")
    _avance_origin(depot)

    acheve = depot.lib("sync-main", "--check")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "avancerait de 1 commit(s)" in acheve.stdout
    assert depot.git("rev-parse", "main") == attendu_avant


def test_ensure_met_main_a_jour_sans_casser_son_verdict(depot: Depot) -> None:
    """Le câblage qui fait tout le ticket : aucun geste dédié à se rappeler.

    `/ticket-start` appelle `ensure` — c'est le point de passage obligé de tout démarrage, manuel
    comme autonome. Et le verdict doit rester la DERNIÈRE ligne de stdout : le compte rendu de
    synchronisation ne casse pas le contrat de sortie sur lequel /ticket-start s'appuie (#181).
    """
    attendu = _avance_origin(depot)

    acheve = depot.lance("ensure", "153", "--branche", "chore/153-suite")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "main mis à jour" in acheve.stdout
    assert depot.git("rev-parse", "main") == attendu
    assert _verdict(acheve).startswith("WORKTREE ")


def test_ensure_demarre_le_ticket_meme_si_main_ne_peut_pas_suivre(depot: Depot) -> None:
    """Une abstention est un signalement, jamais un blocage — le ticket doit partir quand même."""
    _avance_origin(depot)
    (depot.racine / "README.md").write_text("travail en cours\n", encoding="utf-8", newline="\n")

    acheve = depot.lance("ensure", "153", "--branche", "chore/153-suite")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "changements non commités" in acheve.stderr
    assert _verdict(acheve).startswith("WORKTREE ")
