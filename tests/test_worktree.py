"""Tests du parcours « un worktree par ticket » — `scripts/git/worktree.sh` (ticket #152).

Même principe que [`test_setup.py`](test_setup.py) : un **dépôt jetable** monté dans `tmp_path`,
sur lequel le vrai script est lancé. Rien n'est jamais écrit dans le dépôt de travail — `HOME` et
l'emplacement des worktrees (`MAESTRO_WORKTREE_DIR`) sont eux aussi redirigés vers `tmp_path`.

**Ni réseau ni CLI de forge.** Le dépôt jetable a son propre `origin` (un dépôt *bare* local), et la
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

# `setup.sh` factice (#216) : il journalise ce qu'on lui demande, rend la dérive qu'on lui a mise
# dans la table, et le code qu'on lui a demandé pour la réparation. Ni pip, ni npm, ni réseau —
# ce qui est testé ici, c'est le CÂBLAGE (qui appelle quoi, avec quoi, et sans jamais bloquer).
SHIM_SETUP = """\
#!/usr/bin/env bash
printf 'setup %s\\n' "$*" >> "$MAESTRO_FAUX_JOURNAL"
if [ "$1" = --derive ]; then
  [ -s "$MAESTRO_FAUX_DERIVE" ] || exit 0
  cat "$MAESTRO_FAUX_DERIVE"
  exit 3
fi
exit "${MAESTRO_FAUX_SETUP_CODE:-0}"
"""


@dataclass
class Depot:
    """Clone principal jetable, avec son `origin` local et son dossier de worktrees."""

    racine: Path
    origin: Path
    worktrees: Path
    home: Path
    fauxbin: Path
    verdicts: dict[str, str] | None = None
    derive: str | None = None
    code_setup: str = "0"
    pose: bool = False
    mrs: dict[str, str] | None = None
    orphelins: str | None = None
    code_orphelins: str = "0"

    # --- exécution ---
    def lance(
        self,
        *args: str,
        cwd: Path | None = None,
        environnement: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Lance `bash scripts/git/worktree.sh <args>` (depuis le clone principal par défaut)."""
        return self._bash("scripts/git/worktree.sh", *args, cwd=cwd, surcharges=environnement)

    def lib(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        """Lance `bash scripts/gitlab/lib.sh <args>`."""
        return self._bash("scripts/gitlab/lib.sh", *args, cwd=cwd)

    def _bash(
        self,
        script: str,
        *args: str,
        cwd: Path | None,
        surcharges: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environnement = os.environ.copy()
        # Le profil et les ports viennent parfois de la machine (bloc `env` des réglages Claude
        # Code de ce dépôt-ci) : on repart d'une base neutre.
        for cle in ("MAESTRO_CHROME_PROFILE", "MAESTRO_PORT_API", "MAESTRO_PORT_UI"):
            environnement.pop(cle, None)
        # Historique des sessions (#385) : `sessions` lit `CLAUDE_CONFIG_DIR` avant `HOME`. Sur un
        # poste qui la pose, la rediriger dans `HOME` seul ne suffirait pas — les tests liraient le
        # VRAI historique de la machine, et le verdict de la suite dépendrait du poste.
        environnement.pop("CLAUDE_CONFIG_DIR", None)
        environnement["HOME"] = str(self.home)
        environnement["MAESTRO_WORKTREE_DIR"] = str(self.worktrees)
        # Ramassage des worktrees (#197) : désactivé par défaut dans les tests de création — il
        # interrogerait GitLab, absent d'ici. Les tests du ramassage le rallument avec un verdict
        # imposé (`impose_verdicts`), seule couture par laquelle ils disent ce qui est « soldé ».
        environnement["MAESTRO_WORKTREE_GC"] = "0"
        if self.verdicts is not None:
            environnement.pop("MAESTRO_WORKTREE_GC")
            environnement["MAESTRO_WORKTREE_VERDICT"] = str(self.fauxbin / "verdict")
        # Pose du cycle de vie par le ramassage (#275) : ÉTEINTE par défaut, et ce défaut est un
        # garde-fou, pas un confort. Sans lui, un test qui rallume `gc` appellerait le VRAI
        # `lib.sh reconcile-workflow` avec des iid de fixture (« 152 »…) — sur un poste où `gh`
        # est authentifié, ça poserait « Terminé » sur les vrais tickets du projet.
        environnement["MAESTRO_WORKFLOW_POSE"] = "0"
        if self.pose:
            environnement["MAESTRO_WORKFLOW_POSE"] = str(self.fauxbin / "pose")
        # Mise à niveau des dépendances (#216) : même dispositif. Éteinte par défaut, rallumée par
        # `impose_derive`, qui pose en même temps le `setup.sh` factice qu'elle appellera — le vrai
        # installerait pour de bon.
        # Purge des branches mergées (#305) : même dispositif encore. Éteinte par défaut — elle
        # demanderait à la forge l'état de la PR de chaque branche, et `gh` est celui du poste, donc
        # authentifié sur le VRAI dépôt. `impose_mr` la rallume en posant le faux `gh` qui répond
        # à sa place, seule couture par laquelle ces tests disent ce qui est mergé.
        environnement["MAESTRO_PURGE_BRANCHES"] = "0"
        if self.mrs is not None:
            environnement.pop("MAESTRO_PURGE_BRANCHES")
        # Signalement des tickets « En cours » orphelins (#328) : ÉTEINT par défaut, même
        # garde-fou que la pose ci-dessus. Sans lui, un test qui rallume `gc` appellerait le VRAI
        # `lib.sh reconcile-en-cours`, qui lirait le backlog du VRAI projet depuis le poste.
        environnement["MAESTRO_EN_COURS_SIGNAL"] = "0"
        if self.orphelins is not None:
            environnement["MAESTRO_EN_COURS_SIGNAL"] = str(self.fauxbin / "orphelins")
        environnement["MAESTRO_MAJ_DEPENDANCES"] = "0"
        if self.derive is not None:
            environnement.pop("MAESTRO_MAJ_DEPENDANCES")
            environnement["MAESTRO_FAUX_JOURNAL"] = str(self.journal)
            environnement["MAESTRO_FAUX_DERIVE"] = str(self.fauxbin / "derive.tsv")
            environnement["MAESTRO_FAUX_SETUP_CODE"] = self.code_setup
        environnement["PATH"] = os.pathsep.join([str(self.fauxbin), environnement.get("PATH", "")])
        # Les surcharges du test passent EN DERNIER : elles disent ce que ce test-là veut voir, et
        # doivent pouvoir reposer une variable que les neutralisations ci-dessus viennent d'ôter
        # (`CLAUDE_CONFIG_DIR`, notamment, retirée plus haut pour couper les tests du vrai poste).
        environnement.update(surcharges or {})
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
            f'awk -F\'\\t\' -v iid="$1" \'iid == $1 {{ print $2 "\\t" $3 "\\t" $4; exit }}\''
            f' "{str(table).replace(chr(92), "/")}"\n',
            encoding="utf-8",
            newline="\n",
        )
        shim.chmod(0o755)

    # --- couture de la pose du cycle de vie (#275) ---
    def impose_pose(self) -> None:
        """Remplace `lib.sh reconcile-workflow` par un mouchard — et rallume la pose.

        Le shim journalise l'iid reçu et imprime une ligne, ce que `gc` lit comme « quelque chose a
        été posé ». Aucun appel au CLI de la forge, donc ni réseau ni écriture.
        """
        self.pose = True
        shim = self.fauxbin / "pose"
        shim.write_text(
            "#!/usr/bin/env bash\n"
            f'printf \'%s\\n\' "$1" >> "{(self.fauxbin / "poses.txt").as_posix()}"\n'
            "printf 'Cycle de vie de #%s → « Terminé »\\n' \"$1\"\n",
            encoding="utf-8",
            newline="\n",
        )
        shim.chmod(0o755)

    def poses(self) -> list[str]:
        """Les iid pour lesquels le ramassage a demandé la pose du cycle de vie."""
        journal = self.fauxbin / "poses.txt"
        if not journal.exists():
            return []
        return [ligne for ligne in journal.read_text(encoding="utf-8").splitlines() if ligne]

    # --- couture de la purge des branches mergées (#305) ---
    def impose_mr(self, etats: dict[str, str]) -> None:
        """Impose l'état de la PR de chaque branche — et rallume la purge.

        Un faux `gh` sert la seule lecture dont `gl_mr_state` a besoin : la requête GraphQL de
        `gh_mr_brief`, dont on ne retient que le `headRefName`. Une branche absente de la table
        n'a pas de PR du tout, ce que le vrai `gh` rend par une liste de nœuds vide.

        Le vocabulaire de sortie est celui de GitHub (`OPEN`/`MERGED`/`CLOSED`) : c'est `lib.sh`
        qui le retraduit en `opened`/`merged`/`closed`, et le shim n'a pas à connaître ce
        contrat — sans quoi le test passerait quoi que fasse la traduction.
        """
        self.mrs = dict(etats)
        table = self.fauxbin / "etats-mr.tsv"
        table.write_text(
            "".join(f"{branche}\t{etat.upper()}\n" for branche, etat in self.mrs.items()),
            encoding="utf-8",
            newline="\n",
        )
        shim = self.fauxbin / "gh"
        corps = f"""#!/usr/bin/env bash
# Faux `gh` : sert la requête GraphQL de `gh_mr_brief`, et rien d'autre.
[ "$1" = auth ] && exit 0
if [ "$1" = api ] && [ "$2" = graphql ]; then
  # La requête arrive en « -f query=… » : on en extrait le headRefName visé.
  branche="$(printf '%s' "$*" | sed -n 's/.*headRefName: "\\([^"]*\\)".*/\\1/p')"
  etat="$(awk -F'\\t' -v b="$branche" 'b == $1 {{ print $2; exit }}' "{table.as_posix()}")"
  if [ -z "$etat" ]; then
    printf '{{"data":{{"repository":{{"pullRequests":{{"nodes":[]}}}}}}}}\\n'
    exit 0
  fi
  noeud='{{"number":42,"state":"'"$etat"'","headRefOid":"deadbeef"}}'
  printf '{{"data":{{"repository":{{"pullRequests":{{"nodes":[%s]}}}}}}}}\\n' "$noeud"
  exit 0
fi
exit 1
"""
        shim.write_text(corps, encoding="utf-8", newline="\n")
        shim.chmod(0o755)

    # --- couture du signalement des orphelins (#328) ---
    def impose_orphelins(self, lignes: str, *, code: str = "0") -> None:
        """Remplace `lib.sh reconcile-en-cours` par un mouchard — et rallume le signalement.

        Le shim journalise les arguments reçus (c'est ainsi qu'on vérifie que `ensure` passe bien
        `--sauf <iid>`) puis imprime `lignes`, exactement comme le mode `--auto` du vrai verbe :
        rien quand il n'y a rien à dire. `code` permet d'éprouver un verbe en échec — le
        signalement est best-effort et ne doit jamais faire échouer un ramassage.

        Ce qui est vérifié ici, c'est le CÂBLAGE (qui appelle quoi, avec quoi, et sans jamais
        bloquer) ; la règle qui départage un vivant d'un orphelin est du ressort du verbe, et vit
        dans tests/test_collaboration.py.
        """
        self.orphelins = lignes
        self.code_orphelins = code
        shim = self.fauxbin / "orphelins"
        shim.write_text(
            "#!/usr/bin/env bash\n"
            f'printf \'%s\\n\' "$*" >> "{(self.fauxbin / "appels-orphelins.txt").as_posix()}"\n'
            f'cat "{(self.fauxbin / "orphelins.txt").as_posix()}"\n'
            f"exit {code}\n",
            encoding="utf-8",
            newline="\n",
        )
        shim.chmod(0o755)
        (self.fauxbin / "orphelins.txt").write_text(lignes, encoding="utf-8", newline="\n")

    def appels_orphelins(self) -> list[str]:
        """Les arguments passés au signalement, un appel par ligne."""
        journal = self.fauxbin / "appels-orphelins.txt"
        if not journal.exists():
            return []
        return [ligne for ligne in journal.read_text(encoding="utf-8").splitlines() if ligne]

    # --- couture de la mise à niveau des dépendances (#216) ---
    @property
    def journal(self) -> Path:
        """Ce que le `setup.sh` factice a reçu, un appel par ligne."""
        return self.fauxbin / "appels-setup.txt"

    def impose_derive(self, lignes: str, *, code_setup: str = "0") -> None:
        """Impose la réponse de `setup.sh --derive` — et rallume la mise à niveau.

        `lignes` : le TSV que rend le vrai mode (« <étape><TAB><raison> »), vide pour « à jour ».
        `code_setup` : ce que rend la réparation (`--only …`), pour éprouver son échec.
        """
        self.derive = lignes
        self.code_setup = code_setup
        (self.fauxbin / "derive.tsv").write_text(lignes, encoding="utf-8", newline="\n")
        setup = self.racine / "scripts" / "setup.sh"
        setup.write_text(SHIM_SETUP, encoding="utf-8", newline="\n")
        setup.chmod(0o755)

    def appels_setup(self) -> list[str]:
        if not self.journal.exists():
            return []
        return [ligne for ligne in self.journal.read_text(encoding="utf-8").splitlines() if ligne]

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

    # Copie conforme des motifs du dépôt, y compris l'ABSENCE de barre oblique finale sur `.venv`
    # et `.tools` (#333) : ils sont partagés par un LIEN, que git ne voit comme un répertoire que
    # sous Windows. Un `/` ici les rendrait à nouveau non ignorés sous Linux, et le worktree
    # fraîchement monté repasserait pour « porteur de travail non sauvegardé ».
    (racine / ".gitignore").write_text(
        ".env\n.venv\n.tools\nnode_modules/\n.claude/settings.local.json\n",
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

    return Depot(racine=racine, origin=origin, worktrees=worktrees, home=home, fauxbin=fauxbin)


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


def test_un_worktree_fraichement_monte_est_propre(depot: Depot) -> None:
    """Rien de ce que `create` dépose ne doit apparaître dans `git status` (#333).

    L'invariant a l'air décoratif ; il porte en fait tout le cycle de vie. « Travail non
    sauvegardé » se mesure par `git status --porcelain` (`travail_non_sauvegarde`), et c'est le
    garde-fou qui fait REFUSER un retrait — à juste titre, mieux vaut 535 Mo de trop qu'un commit
    perdu. Un worktree sale dès sa création rend donc `remove` et `gc` définitivement inopérants :
    onze tests tombaient en cascade, tous en aval, aucun ne nommant la cause.

    Il n'était vérifié nulle part directement, et les onze tests qui l'auraient trahi sont gardés
    par `skipif(git absent)` — donc sautés dans l'image `python:3.11-slim` du pipeline, et joués
    seulement sur des postes Windows, où les artefacts partagés sont des JONCTIONS (des
    répertoires) et non des liens symboliques (des fichiers). D'où un motif `.venv/` qui ignorait
    sous Windows et laissait passer sous Linux.
    """
    acheve = depot.lance("create", "152", "--branche", BRANCHE)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    salissures = depot.git("status", "--porcelain", cwd=depot.worktree())
    assert salissures == "", (
        "un worktree fraîchement monté doit être propre — sinon le garde-fou « travail non "
        f"sauvegardé » refuse à jamais de le retirer. Laissé derrière :\n{salissures}"
    )


def test_creation_monte_l_atelier_de_session(depot: Depot) -> None:
    """`.maestro/session/` est le seul endroit qu'une session atteigne en chemin RELATIF (#307).

    Son répertoire temporaire et `/tmp` sont hors du répertoire de travail : un fichier qu'elle y
    dépose lui devient illisible au tour suivant, et c'est la cause n°1 des refus de permission des
    sessions autonomes. Le désigner sans le créer ne vaudrait pas mieux qu'une consigne.
    """
    acheve = depot.lance("create", "152", "--branche", BRANCHE)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    assert (depot.worktree() / ".maestro" / "session").is_dir()
    assert ".maestro/session/" in acheve.stdout, "l'étape est rapportée, pas silencieuse"


def test_ensure_complete_l_atelier_d_un_worktree_deja_monte(depot: Depot) -> None:
    """La voie `ICI` ne rejoue pas `create` : sans ça, un worktree monté avant #307 n'en aurait
    jamais, et le prompt renverrait vers un répertoire absent — pire qu'une consigne absente."""
    depot.lance("create", "152", "--branche", BRANCHE)
    wt = depot.worktree()
    shutil.rmtree(wt / ".maestro")

    acheve = depot.lance("ensure", "152", "--branche", BRANCHE, cwd=wt)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    assert (wt / ".maestro" / "session").is_dir()
    # Le contrat de sortie d'`ensure` reste tenu : le verdict est toujours la dernière ligne.
    assert _verdict(acheve).startswith("ICI ")


def test_node_modules_n_est_jamais_un_lien(depot: Depot) -> None:
    """Turbopack rejette un `node_modules` lié — « it points out of the filesystem root ».

    L'UI ne démarre alors pas du tout : ces dépendances-là s'installent sur place (délégué à
    `scripts/setup.sh`, absent du dépôt jetable — c'est le refus de lier qui est testé ici).
    """
    acheve = depot.lance("create", "152", "--branche", BRANCHE)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    node_modules = depot.worktree() / "apps" / "web" / "node_modules"
    assert not node_modules.is_symlink()
    assert "apps/web" in acheve.stdout  # l'étape est rapportée, pas passée sous silence


def test_ports_et_profil_sont_propres_au_worktree(depot: Depot) -> None:
    """Ce qui ferait se télescoper deux sessions est distinct ; le reste est hérité."""
    depot.lance("create", "152", "--branche", BRANCHE)
    reglages = depot.reglages()

    # 152 mod 100 = 52.
    assert reglages["env"]["MAESTRO_PORT_API"] == "8052"
    assert reglages["env"]["MAESTRO_PORT_UI"] == "3052"
    assert "chrome-profile-152" in reglages["env"]["MAESTRO_CHROME_PROFILE"]
    assert (
        reglages["env"]["MAESTRO_CHROME_PROFILE"]
        != (REGLAGES_PRINCIPAL["env"]["MAESTRO_CHROME_PROFILE"])
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
    assert "8000/3000" in acheve.stdout  # le clone principal garde les ports par défaut


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
# ici par `Depot.impose_verdicts` : ni réseau, ni CLI de forge, ni écriture côté forge.


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
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée")})

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#152 retiré" in acheve.stdout
    assert "PR #42 mergée" in acheve.stdout
    assert not depot.worktree().exists()
    assert BRANCHE in depot.git("branch", "--list", BRANCHE)
    # Le clone principal n'est ni retiré, ni amputé de ses artefacts partagés (jonctions, #152).
    assert (depot.racine / ".git").is_dir()
    for lourd in (".venv", ".tools", "apps/web/node_modules"):
        assert (depot.racine / lourd / "marqueur.txt").read_text(encoding="utf-8") == lourd


def test_gc_ne_touche_pas_un_worktree_actif(depot: Depot) -> None:
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("actif", "ticket #152 « open » (PR « aucune »)")})

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#152 conservé" in acheve.stdout
    assert depot.worktree().exists()


def test_gc_ne_deduit_jamais_le_merge_du_nom_de_la_branche(depot: Depot) -> None:
    """Verdict « inconnu » (gh absent, hors ligne, ticket illisible) : on ne touche à rien.

    Ne rien savoir n'autorise rien — même garde-fou que `cleanup-merged` sur les branches
    (docs/10 §6) : `chore/152-…` a tout l'air d'un ticket clos, ce n'est pas une preuve.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("inconnu", "gh indisponible")})

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
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée")})

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
    depot.impose_verdicts({"152": _verdict_ligne("fini", "ticket #152 fermé (PR « aucune »)")})

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
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée", sha)})

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

    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée", sha)})
    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#152 retiré" in acheve.stdout
    assert not depot.worktree().exists()


def test_gc_signale_un_commit_posterieur_au_merge(depot: Depot) -> None:
    """Symétrique du précédent : ce qui a été commité APRÈS le merge n'est nulle part ailleurs."""
    depot.lance("create", "152", "--branche", BRANCHE)
    sha = _commit_local(depot, depot.worktree(), "mergé en squash")
    _commit_local(depot, depot.worktree(), "ajouté après le merge")
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée", sha)})

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
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée")})

    acheve = depot.lance("gc", cwd=depot.worktree())
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "session courante" in acheve.stdout
    assert depot.worktree().exists()


def test_gc_check_ne_retire_rien(depot: Depot) -> None:
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée")})

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
    depot.impose_verdicts({"152": _verdict_ligne("actif", "ticket #152 « open » (PR « aucune »)")})

    acheve = depot.lance("gc", "--auto")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert acheve.stdout.strip() == "", acheve.stdout


def test_gc_ignore_une_branche_hors_convention(depot: Depot) -> None:
    """Sans iid dans le nom, aucun ticket à interroger — donc aucune décision à prendre."""
    depot.lance("create", "152", "--branche", "experimentation")
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée")})

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "hors convention" in acheve.stdout
    assert depot.worktree("experimentation").exists()


def test_ensure_retrouve_le_travail_d_un_ticket_repris(depot: Depot) -> None:
    """La dernière boucle du critère de #329 : « sans rien perdre » se vérifie ICI, pas ailleurs.

    Rendre un orphelin prenable n'écrit que dans GitLab — le worktree, la branche, les commits non
    poussés et le travail non commité ne sont pas touchés. Ce qui reste à prouver est l'autre bout :
    que le démarrage suivant les RETROUVE. Deux choses pourraient le défaire à ce moment précis, et
    ce sont justement les deux ménages câblés dans `ensure` — le ramassage des worktrees et la purge
    des branches. Ni l'un ni l'autre ne doit y toucher : le ticket est ouvert, sa PR n'existe pas,
    donc il est « actif » et sa branche n'est pas mergée.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    wt = depot.worktree()
    sha = _commit_local(depot, wt, "2047 lignes jamais poussées\n")
    (wt / "en-chantier.txt").write_text("pas encore commité\n", encoding="utf-8", newline="\n")
    # Un ticket repris est OUVERT et sans PR mergée : c'est ce que `worktree-done` en dit.
    depot.impose_verdicts({"152": _verdict_ligne("actif", "ticket ouvert, aucune PR mergée")})
    depot.impose_mr({BRANCHE: "opened"})

    acheve = depot.lance("ensure", "152", "--branche", BRANCHE)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    verdict = _verdict(acheve)
    assert verdict.startswith("WORKTREE "), f"verdict inattendu : {verdict!r}"
    assert Path(verdict[len("WORKTREE ") :]).resolve() == wt.resolve(), (
        "c'est le worktree du ticket qui doit être rendu, pas un neuf"
    )
    assert depot.git("rev-parse", "HEAD", cwd=wt) == sha, "le commit non poussé est retrouvé"
    assert (wt / "en-chantier.txt").exists(), "le travail non commité aussi"
    assert BRANCHE in depot.git("branch", "--list", BRANCHE), "la branche n'a pas été purgée"


def test_ensure_ramasse_les_worktrees_soldes_avant_de_monter_le_sien(depot: Depot) -> None:
    """Le câblage qui fait tout le ticket : plus aucun geste dédié à se rappeler (#197).

    `/ticket-start` appelle `ensure` — c'est le seul moment où quelqu'un passe forcément par ici.
    Et le verdict doit rester la DERNIÈRE ligne de stdout : le rapport de ramassage ne casse pas
    le contrat de sortie sur lequel /ticket-start s'appuie (#181).
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée")})

    acheve = depot.lance("ensure", "153", "--branche", "chore/153-suite")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    assert "#152 retiré" in acheve.stdout
    assert not depot.worktree().exists(), "le worktree soldé devait être ramassé au passage"
    verdict = _verdict(acheve)
    assert verdict.startswith("WORKTREE ")
    assert Path(verdict[len("WORKTREE ") :]).resolve() == depot.worktree("153-suite").resolve()


# --- Coquilles laissées par un retrait (#422) -------------------------------------------------
# `git worktree remove` supprime le CONTENU, échoue sur le DOSSIER lui-même quand un processus le
# tient (« Permission denied » sous Windows) — et va au bout de son DÉSENREGISTREMENT quand même.
# Il reste un dossier vide que plus rien ne revendique : ni `git worktree list`, ni `list`, ni `gc`,
# qui itèrent tous les trois dessus. Onze s'étaient accumulées sur le poste de référence, dont dix
# sans que rien ne les ait jamais nommées — et la onzième est née pendant l'écriture de ce ticket.
#
# Ce n'est pas qu'une affaire de propreté : `create` refusait tout dossier déjà là, donc une
# coquille BLOQUAIT le remontage de son ticket (`ensure` rend 1, /ticket-start s'arrête).


def _git_qui_laisse_une_coquille(depot: Depot, *, vide: bool = True) -> None:
    """Faux `git` qui reproduit la panne : contenu supprimé, worktree désenregistré, dossier resté.

    Tout est délégué au vrai git — seul `worktree remove` est habillé de son échec, APRÈS coup,
    exactement comme Windows le rend. `vide=False` laisse en plus un fichier dans le dossier, ce
    qui est le cas où le rattrapage ne peut RIEN faire et doit le dire.

    C'est la seule façon d'éprouver ce chemin sans dépendre d'un verrou de système de fichiers :
    « un processus tient le dossier » ne se met pas en scène de façon reproductible.
    """
    assert GIT is not None
    reste = "" if vide else '  printf reste > "$cible/reste.txt"\n'
    shim = depot.fauxbin / "git"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f'VRAI="{Path(GIT).as_posix()}"\n'
        # `-C <racine>` précède le verbe : on cherche la paire, pas une position.
        'case " $* " in\n'
        '  *" worktree remove "*)\n'
        '    cible="${@: -1}"\n'
        '    "$VRAI" "$@" || exit $?\n'
        '    mkdir -p "$cible"\n'
        f"{reste}"
        '    printf "error: failed to delete \'%s\': Permission denied\\n" "$cible" >&2\n'
        "    exit 1 ;;\n"
        "esac\n"
        'exec "$VRAI" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    shim.chmod(0o755)


def test_le_retrait_ecarte_la_coquille_que_git_laisse_derriere_lui(depot: Depot) -> None:
    """Le rattrapage : git a désenregistré, il ne reste qu'un dossier vide — on le retire."""
    depot.lance("create", "152", "--branche", BRANCHE)
    _git_qui_laisse_une_coquille(depot)

    acheve = depot.lance("remove", "152")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert not depot.worktree().exists(), "la coquille laissée par git devait être écartée"


def test_un_dossier_qui_resiste_est_dit_desenregistre_et_non_non_retire(depot: Depot) -> None:
    """« non retiré » annoncerait l'inverse de ce qui s'est passé : git ne le connaît plus.

    C'est le fond du défaut : onze coquilles sont nées derrière autant de lignes rouges qui
    disaient toutes qu'il n'y avait rien eu de fait.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    _git_qui_laisse_une_coquille(depot, vide=False)

    acheve = depot.lance("remove", "152")
    assert acheve.returncode == 1
    assert "désenregistré" in acheve.stderr
    assert "non retiré" not in acheve.stderr


def test_creation_accepte_une_coquille_vide(depot: Depot) -> None:
    """Une coquille ne porte rien : la refuser barrait le remontage pour un dossier de 0 octet."""
    coquille = depot.worktree()
    coquille.mkdir(parents=True)

    acheve = depot.lance("create", "152", "--branche", BRANCHE)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert (depot.worktree() / ".git").exists(), "le worktree devait être monté malgré la coquille"
    assert BRANCHE in depot.git("branch", "--show-current", cwd=depot.worktree())


def test_ensure_remonte_un_ticket_dont_la_coquille_traine_encore(depot: Depot) -> None:
    """Le symptôme d'origine, bout en bout : `ensure` rendait 1 et /ticket-start s'arrêtait là."""
    depot.worktree().mkdir(parents=True)

    acheve = depot.lance("ensure", "152", "--branche", BRANCHE)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert _verdict(acheve).startswith("WORKTREE ")


def test_gc_ecarte_les_coquilles_vides(depot: Depot) -> None:
    """Elles sont hors de `git worktree list` : rien d'autre que ce balayage ne les rencontre."""
    depot.lance("create", "152", "--branche", BRANCHE)
    coquille = depot.worktree("183-partie-depuis-longtemps")
    coquille.mkdir(parents=True)
    depot.impose_verdicts({"152": _verdict_ligne("actif", "ticket ouvert")})

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "coquille vide écartée" in acheve.stdout
    assert not coquille.exists()
    assert depot.worktree().exists(), "le worktree vivant n'est pas concerné"


def test_gc_ecarte_les_coquilles_meme_quand_il_ne_reste_aucun_worktree(depot: Depot) -> None:
    """Le cas le plus probable : elles restent quand tout le reste est parti."""
    coquille = depot.worktree("183-partie-depuis-longtemps")
    coquille.mkdir(parents=True)

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "coquille vide écartée" in acheve.stdout
    assert not coquille.exists()


def test_gc_ne_touche_jamais_a_un_dossier_inconnu_non_vide(depot: Depot) -> None:
    """Vide, c'est un déchet ; porteur, c'est le travail de quelqu'un — on le nomme, c'est tout."""
    inconnu = depot.worktree("carnet-de-notes")
    inconnu.mkdir(parents=True)
    (inconnu / "notes.md").write_text("ne pas perdre", encoding="utf-8", newline="\n")

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "aucun worktree ne revendique" in acheve.stdout
    assert (inconnu / "notes.md").read_text(encoding="utf-8") == "ne pas perdre"


def test_gc_check_ne_retire_aucune_coquille(depot: Depot) -> None:
    coquille = depot.worktree("183-partie-depuis-longtemps")
    coquille.mkdir(parents=True)

    acheve = depot.lance("gc", "--check")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "à écarter" in acheve.stdout
    assert coquille.exists(), "--check ne touche à rien"


def test_gc_auto_rompt_le_silence_pour_une_coquille(depot: Depot) -> None:
    """Se taire dessus est exactement ce qui les a laissées s'accumuler."""
    coquille = depot.worktree("183-partie-depuis-longtemps")
    coquille.mkdir(parents=True)

    acheve = depot.lance("gc", "--auto")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "coquille vide écartée" in acheve.stdout
    assert not coquille.exists()


def test_gc_auto_reste_muet_quand_il_n_y_a_aucune_coquille(depot: Depot) -> None:
    """Le silence reste le cas normal : le balayage n'ajoute pas une ligne à chaque démarrage."""
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("actif", "ticket ouvert")})

    acheve = depot.lance("gc", "--auto")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert acheve.stdout.strip() == "", acheve.stdout


def test_list_nomme_les_coquilles(depot: Depot) -> None:
    """L'autre moitié du remède : sans ça, elles ne réapparaissent qu'en bloquant un remontage."""
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.worktree("183-partie-depuis-longtemps").mkdir(parents=True)

    acheve = depot.lance("list")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "183-partie-depuis-longtemps" in acheve.stdout
    assert "coquille vide" in acheve.stdout


# --- Purge des branches mergées (#305) --------------------------------------------------------
# Le pendant du ramassage ci-dessus, pour les branches. Il existait depuis #23 mais était accroché à
# `lib.sh start-branch`, devenu injoignable avec #181 : /ticket-start monte le worktree AVANT
# d'appeler `start-branch`, qui sort alors par « déjà sur la branche » ou par sa voie worktree,
# jamais par celle qui purgeait. Plus rien ne supprimait de branche sans /branch-cleanup manuel, et
# 35 s'étaient accumulées sur le clone principal (constat du 2026-08-07, la plus ancienne #220).
#
# Ces tests épinglent donc d'abord le CÂBLAGE — la purge part bien d'`ensure`, et APRÈS le
# ramassage — puis ce que le compte rendu doit dire, une branche silencieusement non supprimée
# étant exactement ce qui laissait la panne invisible.


def test_ensure_purge_les_branches_mergees(depot: Depot) -> None:
    """Le câblage qui fait le ticket : plus aucun geste dédié à se rappeler.

    Et le garde-fou reste entier — seule part la branche dont la forge confirme la PR `merged`
    (docs/10 §6) ; une PR ouverte, comme une branche sans PR, ne prouve rien.
    """
    depot.git("branch", "chore/140-livree")
    depot.git("branch", "chore/141-en-cours")
    depot.git("branch", "experimentation")
    depot.impose_mr({"chore/140-livree": "merged", "chore/141-en-cours": "opened"})

    acheve = depot.lance("ensure", "152", "--branche", BRANCHE)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    assert "supprimée : chore/140-livree" in acheve.stdout
    assert depot.git("branch", "--list", "chore/140-livree") == ""
    assert "chore/141-en-cours" in depot.git("branch", "--list", "chore/141-en-cours")
    assert "experimentation" in depot.git("branch", "--list", "experimentation")
    # Le verdict reste la DERNIÈRE ligne de stdout : le rapport de purge ne casse pas le contrat
    # de sortie sur lequel /ticket-start s'appuie (#181).
    assert _verdict(acheve).startswith("WORKTREE ")


def test_ensure_purge_la_branche_du_worktree_qu_il_vient_de_ramasser(depot: Depot) -> None:
    """L'ordre entre les deux n'est pas cosmétique (#197 puis #305).

    `git branch -D` refuse une branche empruntée par un worktree : sans le ramassage juste avant,
    la branche d'un ticket soldé resterait là à chaque passage.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée")})
    depot.impose_mr({BRANCHE: "merged"})

    acheve = depot.lance("ensure", "153", "--branche", "chore/153-suite")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    assert "#152 retiré" in acheve.stdout
    assert f"supprimée : {BRANCHE}" in acheve.stdout
    assert depot.git("branch", "--list", BRANCHE) == ""


def test_purge_compte_et_nomme_une_branche_retenue_par_un_worktree(depot: Depot) -> None:
    """Le second défaut de #305 : l'échec de `git branch -D` ne se voyait nulle part.

    Il n'incrémentait aucun des deux compteurs, si bien que la branche sortait du compte rendu sans
    un mot et que le bilan annonçait moins de branches qu'il n'en avait examinées (3 sur 41 lors de
    la purge de rattrapage). Ici le worktree est encore là — verdict non imposé, donc rien n'est
    ramassé — et c'est le cas que la ligne doit nommer.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_mr({BRANCHE: "merged"})

    acheve = depot.lib("cleanup-merged")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    assert "empruntée par le worktree" in acheve.stdout
    assert "152-essai" in acheve.stdout, "le worktree qui retient la branche doit être nommé"
    assert "1 mergée(s) mais empruntée(s) par un worktree" in acheve.stdout
    assert BRANCHE in depot.git("branch", "--list", BRANCHE)


def test_purge_auto_est_muette_quand_il_n_y_a_rien_a_faire(depot: Depot) -> None:
    """Le mode câblé dans `ensure` : il ne s'annonce que s'il agit ou s'il alerte.

    Sans `--auto`, le bilan est rendu — c'est ce qu'attend un appel à la main.
    """
    depot.git("branch", "chore/141-en-cours")
    depot.impose_mr({"chore/141-en-cours": "opened"})

    muet = depot.lib("cleanup-merged", "--auto")
    assert muet.returncode == 0, muet.stdout + muet.stderr
    assert muet.stdout.strip() == "", muet.stdout

    bavard = depot.lib("cleanup-merged")
    assert "0 supprimée(s), 1 conservée(s)" in bavard.stdout


def test_purge_s_abstient_quand_le_clone_principal_est_sale(depot: Depot) -> None:
    depot.git("branch", "chore/140-livree")
    depot.impose_mr({"chore/140-livree": "merged"})
    (depot.racine / "README.md").write_text("modifié\n", encoding="utf-8", newline="\n")

    acheve = depot.lib("cleanup-merged")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "changements non commités" in acheve.stderr
    assert "chore/140-livree" in depot.git("branch", "--list", "chore/140-livree")


def test_purge_vise_le_clone_principal_meme_appelee_depuis_un_worktree(depot: Depot) -> None:
    """L'arbre regardé est celui du clone principal, d'où qu'on appelle (#305).

    C'est ce qui distingue ce helper de sa version d'avant : appelée depuis le worktree du ticket
    en cours — le cas de tout /ticket-start depuis #181 —, elle regardait un arbre en plein travail
    et s'abstenait en silence dès le premier fichier modifié.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.git("branch", "chore/140-livree")
    depot.impose_mr({"chore/140-livree": "merged"})
    (depot.worktree() / "travail.txt").write_text("en cours\n", encoding="utf-8", newline="\n")

    acheve = depot.lib("cleanup-merged", cwd=depot.worktree())
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "supprimée : chore/140-livree" in acheve.stdout
    assert depot.git("branch", "--list", "chore/140-livree") == ""


def test_start_branch_ne_purge_plus_les_branches_mergees(depot: Depot) -> None:
    """Un seul déclencheur automatique, `ensure` (#305).

    `start-branch` a porté la purge de #23 à #305. Garder ce second point d'appel, injoignable
    depuis #181, est exactement ce qui a rendu la panne invisible : le code était là, la doc le
    décrivait, et plus rien ne l'exécutait.
    """
    depot.git("branch", "chore/140-livree")
    depot.impose_mr({"chore/140-livree": "merged"})

    acheve = depot.lib("start-branch", "chore/153-suite")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "Nettoyage des branches" not in acheve.stdout
    assert "chore/140-livree" in depot.git("branch", "--list", "chore/140-livree")


# --- Cycle de vie posé sur le verdict du ramassage (#275) ------------------------------------
# Le merge FERME le ticket mais ne pose aucun label : depuis #207 seul `/branch-cleanup`, lancé à la
# main, posait « Terminé ». La greffe est ici plutôt que dans `ensure` parce que « fini » — PR
# mergée ou ticket fermé — est DÉJÀ la question de la réconciliation : aucune lecture de découverte
# en plus, et les trois points de passage de `gc` en héritent d'un coup.
#
# `Depot.impose_pose` remplace `lib.sh reconcile-workflow` par un mouchard : ce qui est vérifié ici,
# c'est QUAND `gc` demande la pose (et pour quel iid) — la règle « ne jamais écraser Abandonné /
# Doublon », elle, est du ressort de `reconcile-workflow`. Ses tests vivaient dans
# tests/test_cycle_de_vie.py, retiré par #365 avec l'invariant d'exclusion mutuelle des labels qui
# en était le sujet ; leur pendant sur le champ Status est le lot #366.


def test_gc_pose_le_cycle_de_vie_du_ticket_solde(depot: Depot) -> None:
    """Cas nominal : worktree ramassé ⇒ cycle de vie du ticket posé, et le rapport le dit."""
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée")})
    depot.impose_pose()

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert depot.poses() == ["152"]
    assert "cycle de vie → Terminé" in acheve.stdout


def test_gc_ne_pose_aucun_cycle_de_vie_sur_un_verdict_non_solde(depot: Depot) -> None:
    """« actif » ou « inconnu » : ne rien savoir n'autorise pas plus à écrire qu'à supprimer."""
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("actif", "ticket #152 « open »")})
    depot.impose_pose()

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert depot.poses() == []


def test_gc_check_ne_pose_aucun_cycle_de_vie(depot: Depot) -> None:
    """`--check` est un diagnostic : il ne retire rien, donc il n'écrit rien côté GitLab."""
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée")})
    depot.impose_pose()

    acheve = depot.lance("gc", "--check")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert depot.poses() == []
    assert depot.worktree().exists()


def test_gc_pose_le_cycle_de_vie_meme_quand_le_worktree_est_conserve(depot: Depot) -> None:
    """Travail non commité : le worktree reste, le ticket passe quand même « Terminé ».

    Les deux décisions n'ont pas la même source. Le retrait dépend de ce que porte le répertoire
    LOCAL ; le cycle de vie ne dépend que du verdict de GitLab. Les lier ferait qu'un fichier oublié
    dans un worktree laisserait son ticket « En revue » sur le board pour toujours.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    (depot.worktree() / "brouillon.txt").write_text("en cours", encoding="utf-8", newline="\n")
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée")})
    depot.impose_pose()

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert depot.poses() == ["152"], "le cycle de vie ne dépend pas de la propreté du worktree"
    assert depot.worktree().exists(), "un travail non commité reste protégé"
    assert "conservé" in acheve.stdout


def test_ensure_pose_le_cycle_de_vie_des_tickets_soldes_au_passage(depot: Depot) -> None:
    """Le câblage qui fait tout le ticket : `/ticket-start` passe par `ensure`, donc par `gc`.

    Et le verdict reste la DERNIÈRE ligne de stdout — le contrat de sortie de #181 survit à la
    ligne de pose ajoutée au rapport.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée")})
    depot.impose_pose()

    acheve = depot.lance("ensure", "153", "--branche", "chore/153-suite")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert depot.poses() == ["152"]
    assert _verdict(acheve).startswith("WORKTREE ")


def test_gc_survit_a_une_pose_en_echec(depot: Depot) -> None:
    """Pose impossible (gh absent, hors ligne) : le ramassage continue, muet sur ce point.

    Même statut que `sync-main` : ça signale ou ça se tait, ça n'empêche jamais un ticket de
    démarrer ni un run de continuer.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée")})
    depot.impose_pose()
    (depot.fauxbin / "pose").write_text(
        "#!/usr/bin/env bash\nexit 1\n", encoding="utf-8", newline="\n"
    )
    (depot.fauxbin / "pose").chmod(0o755)

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#152 retiré" in acheve.stdout
    assert "cycle de vie" not in acheve.stdout
    assert not depot.worktree().exists()


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


# --- Mise à niveau des dépendances (#216) ----------------------------------------------------
# Un clone existant ne prend pas tout seul les paquets ajoutés au dépôt : la CI les prend à chaque
# pipeline, un clone neuf à son `/setup`, un clone déjà monté jamais. `ensure` est le point de
# passage obligé de tout /ticket-start — c'est là que la mise à niveau s'accroche, comme
# `sync-main` (#205) et le ramassage (#197) avant elle. Elle SIGNALE et ne bloque jamais.

DERIVE_VENV = "venv\tpyproject.toml modifié depuis la dernière installation du venv\n"


def test_ensure_remet_les_dependances_a_niveau_en_appelant_setup(depot: Depot) -> None:
    """Détection puis réparation, toutes deux déléguées à `setup.sh` : rien n'est réimplémenté."""
    depot.impose_derive(DERIVE_VENV + "web\tapps/web/package-lock.json modifié\n")

    acheve = depot.lance("ensure", "152", "--branche", BRANCHE)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    assert depot.appels_setup() == ["setup --derive", "setup --only venv,web"], (
        "la dérive est demandée à setup.sh, puis réparée par lui — jamais par un pip/npm d'ici"
    )
    assert "pyproject.toml modifié" in acheve.stdout + acheve.stderr, "la raison est annoncée"
    assert _verdict(acheve).startswith("WORKTREE "), "le contrat de sortie tient toujours"


def test_ensure_se_tait_quand_les_dependances_sont_a_jour(depot: Depot) -> None:
    """Le cas de tous les jours : la sonde coûte trois comparaisons de dates, et ne dit rien."""
    depot.impose_derive("")

    acheve = depot.lance("ensure", "152", "--branche", BRANCHE)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    assert depot.appels_setup() == ["setup --derive"], "rien à réparer, donc rien n'est installé"
    assert "dépendances en retard" not in acheve.stdout
    assert "mise à niveau" not in acheve.stdout


def test_ensure_demarre_le_ticket_meme_si_la_mise_a_niveau_echoue(depot: Depot) -> None:
    """Même statut que `sync-main` : un échec se dit, il n'interdit pas de démarrer un ticket."""
    depot.impose_derive(DERIVE_VENV, code_setup="1")

    acheve = depot.lance("ensure", "152", "--branche", BRANCHE)

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert _verdict(acheve).startswith("WORKTREE ")
    assert depot.git("branch", "--show-current", cwd=depot.worktree()) == BRANCHE
    assert "en échec" in acheve.stdout
    assert "scripts/setup.sh --only venv" in acheve.stdout, "le rattrapage manuel est donné"


def test_ensure_remet_a_niveau_le_clone_principal_meme_appele_depuis_un_worktree(
    depot: Depot,
) -> None:
    """`.venv/` et `.tools/` vivent dans le clone principal, partagés par lien (docs/10 §9) : c'est
    LUI qu'on équipe, où que la session soit — et l'installation éditable de `maestro` doit
    continuer d'y pointer (#194). Le `setup.sh` factice n'existe que là : l'appel le prouve."""
    depot.impose_derive(DERIVE_VENV)
    depot.lance("create", "152", "--branche", BRANCHE)
    assert not (depot.worktree() / "scripts" / "setup.sh").exists()

    acheve = depot.lance("ensure", "153", "--branche", "chore/153-suite", cwd=depot.worktree())
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    assert depot.appels_setup() == ["setup --derive", "setup --only venv"]


def test_ensure_ignore_une_sonde_indisponible(depot: Depot) -> None:
    """Pas de `setup.sh` (dépôt partiel, clone en cours de montage) : ni bruit, ni blocage."""
    depot.derive = ""  # rallume la mise à niveau sans poser le script factice
    assert not (depot.racine / "scripts" / "setup.sh").exists()

    acheve = depot.lance("ensure", "152", "--branche", BRANCHE)

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert _verdict(acheve).startswith("WORKTREE ")
    assert "dépendances en retard" not in acheve.stdout


# --- Signalement des tickets « En cours » orphelins (#328) ------------------------------------
# Greffé sur `gc` et non sur `ensure`, pour la raison exacte qui a fait greffer la pose du cycle de
# vie au même endroit (#275) : les TROIS points de passage de `gc` — `ensure` donc tout
# /ticket-start, /branch-cleanup, le démarrage d'un run — en héritent d'un coup.
#
# Ce qui se vérifie ici est le CÂBLAGE et lui seul : que `gc` demande, qu'il relaie, qu'il écarte le
# ticket qu'on démarre, et qu'il ne bloque jamais. La règle qui départage un vivant d'un orphelin —
# carte du pilote, fraîcheur du worktree, portée annoncée — est celle du verbe, et vit dans
# tests/test_collaboration.py.

ORPHELIN = "  ⚠ #325 orphelin — déduction : worktree silencieux depuis 7h12 — /ailleurs/325\n"


def test_gc_signale_les_tickets_en_cours_orphelins(depot: Depot) -> None:
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({})  # aucun verdict : rien à ramasser, tout est conservé
    depot.impose_orphelins(ORPHELIN)

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#325 orphelin" in acheve.stdout
    assert "dont plus personne ne s'occupe" in acheve.stdout
    # Consultatif : le worktree présent n'est pas touché, et rien n'est retiré.
    assert depot.worktree().exists()
    assert "0 retiré(s)" in acheve.stdout


def test_gc_auto_reste_muet_quand_personne_n_est_orphelin(depot: Depot) -> None:
    """Le silence est le cas normal — c'est ce qui rend le signal lisible quand il tombe."""
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({})
    depot.impose_orphelins("")

    acheve = depot.lance("gc", "--auto")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert acheve.stdout.strip() == ""


def test_gc_auto_parle_pour_un_orphelin_meme_sans_rien_a_ramasser(depot: Depot) -> None:
    """Le cas qui compte : sans ça, /ticket-start ne verrait JAMAIS le signal.

    Le ramassage n'a presque jamais rien à dire — c'est tout l'intérêt de son `--auto` —, donc un
    orphelin doit rompre ce silence à lui seul, sans quoi il serait avalé par le mutisme du bloc
    qui le porte.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({})
    depot.impose_orphelins(ORPHELIN)

    acheve = depot.lance("gc", "--auto")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#325 orphelin" in acheve.stdout
    # …sans réveiller le compte rendu du ramassage, qui, lui, n'a rien à raconter.
    assert "retiré(s)" not in acheve.stdout


def test_ensure_ecarte_du_signalement_le_ticket_qu_il_demarre(depot: Depot) -> None:
    """`--sauf <iid>` : le ticket qu'on démarre est repris à l'instant même.

    Son worktree peut très bien dormir depuis la veille — l'annoncer orphelin au moment précis où
    on le reprend serait vrai une seconde et faux la suivante.
    """
    depot.impose_verdicts({})
    depot.impose_orphelins(ORPHELIN)

    acheve = depot.lance("ensure", "152", "--branche", BRANCHE)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert _verdict(acheve).startswith("WORKTREE ")
    assert depot.appels_orphelins() == ["--auto --sauf 152"]


def test_gc_ne_bloque_pas_sur_un_signalement_en_echec(depot: Depot) -> None:
    """Best-effort comme le reste de la famille : un verbe muet ou en erreur n'arrête rien."""
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée")})
    depot.impose_orphelins("", code="1")

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#152 retiré" in acheve.stdout, "le ramassage fait son travail malgré le signal en échec"


def test_les_trois_points_de_passage_passent_bien_par_le_ramassage(depot: Depot) -> None:  # noqa: ARG001
    """Le mutisme épinglé plus haut ne vaut que si les TROIS points de passage en héritent (#330).

    Les tests précédents montrent que `gc --auto` se tait quand il n'y a rien à dire ; encore
    faut-il que ce soit bien par là que chacun passe. C'est la leçon de #305, qui a coûté 35
    branches mergées : un déclencheur qui a cessé d'être atteint ne se remarque pas, le code étant
    toujours là et la doc le décrivant toujours. Un second câblage — un appel direct à
    `reconcile-en-cours` quelque part — serait la même panne en préparation, avec en prime deux
    formulations du signal à garder d'accord.

    /branch-cleanup appelle `gc` SANS `--auto`, et c'est voulu : c'est un geste explicite, dont on
    attend le compte rendu. Les deux autres sont des passages obligés dont personne n'a rien
    demandé, d'où le mode muet.
    """
    passages = {
        "scripts/git/worktree.sh": "gc --auto",  # `ensure`, donc tout /ticket-start
        "scripts/orchestrate/run.sh": "gc --auto",  # le démarrage d'un run
        ".claude/commands/branch-cleanup.md": "worktree.sh gc",
    }
    for relatif, attendu in passages.items():
        texte = (RACINE / relatif).read_text(encoding="utf-8")
        assert attendu in texte, f"{relatif} ne passe plus par le ramassage ({attendu!r})"

    # Le signalement n'a qu'un seul câblage automatique : `gc`. Ailleurs, `reconcile-en-cours` ne
    # peut être que NOMMÉ (une commande proposée à un humain), jamais appelé — sans quoi le mutisme
    # vérifié ci-dessus ne dirait plus rien de ce que voit réellement un /ticket-start.
    for relatif in ("scripts/orchestrate/run.sh", ".claude/commands/branch-cleanup.md"):
        for ligne in (RACINE / relatif).read_text(encoding="utf-8").splitlines():
            if "reconcile-en-cours" in ligne and "--auto" in ligne:
                raise AssertionError(f"{relatif} : second câblage du signalement — {ligne.strip()}")


# --- Sessions d'un ticket (#385) ----------------------------------------------------------------
# Claude Code range un transcript sous le RÉPERTOIRE COURANT de la session, et son sélecteur
# `/resume` ne montre que celui d'où on l'appelle : l'historique d'un ticket, produit dans son
# worktree, est donc invisible depuis le clone principal — puis `gc` retire le worktree. Ce que ces
# tests épinglent, c'est que l'adressage se DÉRIVE : rien n'est indexé au moment du ramassage, donc
# un ticket dont le worktree est parti depuis des semaines se retrouve exactement comme un autre.


def _encode_chemin(chemin: Path) -> str:
    """Le chemin, encodé comme Claude Code encode un répertoire courant : `:`, `\\`, `/`, ` ` → `-`.

    Écrit ici en clair plutôt que demandé au script : un test qui interroge l'implémentation pour
    savoir ce qu'il doit attendre ne prouve que la cohérence de celle-ci avec elle-même.
    """
    texte = str(chemin)
    for caractere in (":", "\\", "/", " "):
        texte = texte.replace(caractere, "-")
    return texte


def _bucket(depot: Depot, iid: str, slug: str = "essai") -> Path:
    """Le répertoire de projet que Claude Code aurait créé pour le worktree de `<iid>`."""
    nom = _encode_chemin(depot.worktrees / f"{iid}-{slug}")
    return depot.home / ".claude" / "projects" / nom


def _bucket_dossier(depot: Depot, dossier: Path) -> Path:
    """Le répertoire de projet d'un dossier quelconque — le clone principal, typiquement (#397)."""
    return depot.home / ".claude" / "projects" / _encode_chemin(dossier)


def _pose_fiche(
    depot: Depot,
    pid: int,
    session_id: str,
    nom: str,
    dossier: Path | None = None,
    debut: int = 1_787_000_000_000,
) -> Path:
    """Écrit une fiche du registre des sessions — `<config>/sessions/<PID>.json`.

    Reproduit la forme réelle, `nameSource` COMPRIS et placé juste après `name` : c'est la clé qui
    piège une extraction trop lâche, un motif sur « name » sans son guillemet fermant la ramassant à
    la place du nom.
    """
    dossier_sessions = depot.home / ".claude" / "sessions"
    dossier_sessions.mkdir(parents=True, exist_ok=True)
    fiche = dossier_sessions / f"{pid}.json"
    fiche.write_text(
        json.dumps(
            {
                "pid": pid,
                "sessionId": session_id,
                "cwd": str(dossier if dossier is not None else depot.racine),
                "startedAt": debut,
                "kind": "interactive",
                "entrypoint": "claude-vscode",
                "name": nom,
                "nameSource": "derived",
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return fiche


def _pose_transcript(
    dossier: Path,
    session_id: str,
    titre: str | None = None,
    quand: float | None = None,
    titres_successifs: tuple[str, ...] = (),
) -> Path:
    """Écrit un transcript plausible : quelques lignes JSONL, dont les entrées `ai-title`.

    Sérialisé COMPACT, comme Claude Code l'écrit (`{"type":"ai-title","aiTitle":"…"}`) : le
    `json.dumps` par défaut espace ses séparateurs, ce qu'aucun transcript réel ne fait — un fixture
    plus permissif que la réalité ferait passer une extraction qui ne lit pas le vrai format.
    """
    dossier.mkdir(parents=True, exist_ok=True)
    fichier = dossier / f"{session_id}.jsonl"

    def compact(objet: dict[str, object]) -> str:
        return json.dumps(objet, separators=(",", ":"), ensure_ascii=False)

    lignes = [compact({"type": "user", "message": {"role": "user", "content": "bonjour"}})]
    for intitule in (*titres_successifs, *(() if titre is None else (titre,))):
        lignes.append(compact({"type": "ai-title", "aiTitle": intitule, "sessionId": session_id}))
    lignes.append(compact({"type": "assistant", "message": {"role": "assistant"}}))
    fichier.write_text("\n".join(lignes) + "\n", encoding="utf-8", newline="\n")
    if quand is not None:
        os.utime(fichier, (quand, quand))
    return fichier


def test_sessions_retrouve_un_ticket_dont_le_worktree_est_ramasse(depot: Depot) -> None:
    """Le cas qui motive le verbe : le worktree est parti, l'historique doit rester adressable.

    C'est ici que se joue le choix de DÉRIVER plutôt que d'indexer au ramassage : aucun `gc` n'a
    tourné dans ce test, aucun index n'existe, et le ticket se retrouve quand même.
    """
    _pose_transcript(_bucket(depot, "152"), "aaaa1111-2222-3333-4444-555566667777", "Boucle")
    assert not (depot.worktrees / "152-essai").exists()

    acheve = depot.lance("sessions", "152")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#152" in acheve.stdout
    assert "worktree ramassé" in acheve.stdout
    assert "Boucle" in acheve.stdout
    assert "claude --resume aaaa1111-2222-3333-4444-555566667777" in acheve.stdout


def test_sessions_distingue_un_worktree_encore_en_place(depot: Depot) -> None:
    """Même dérivation, verdict opposé — le répertoire est là, on le dit."""
    depot.lance("create", "152", "--branche", BRANCHE)
    _pose_transcript(_bucket(depot, "152"), "bbbb1111-2222-3333-4444-5555", "En cours")

    acheve = depot.lance("sessions", "152")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "worktree en place" in acheve.stdout
    assert "worktree ramassé" not in acheve.stdout


def test_sessions_replie_quand_le_transcript_n_a_pas_de_titre(depot: Depot) -> None:
    """Beaucoup de sessions n'ont jamais reçu de titre — ce n'est pas une anomalie à taire.

    Sans repli, la ligne sortirait amputée et la commande de reprise, elle, resterait juste : on
    perdrait la session la plus difficile à identifier autrement.
    """
    _pose_transcript(_bucket(depot, "152"), "cccc1111-2222-3333-4444-5555", titre=None)

    acheve = depot.lance("sessions", "152")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "(sans titre)" in acheve.stdout
    assert "claude --resume cccc1111-2222-3333-4444-5555" in acheve.stdout


def test_sessions_retient_le_dernier_titre_pose(depot: Depot) -> None:
    """Le titre est réévalué en cours de session : c'est le DERNIER qui décrit le travail fait."""
    _pose_transcript(
        _bucket(depot, "152"),
        "dddd1111-2222-3333-4444-5555",
        titre="Retrait des labels workflow",
        titres_successifs=("Premier jet",),
    )

    acheve = depot.lance("sessions", "152")
    assert "Retrait des labels workflow" in acheve.stdout
    assert "Premier jet" not in acheve.stdout


def test_sessions_rend_le_plus_recent_d_abord(depot: Depot) -> None:
    """Une reprise vise presque toujours la dernière session : elle doit être en tête."""
    dossier = _bucket(depot, "152")
    _pose_transcript(
        dossier, "aaaa0000-0000-0000-0000-000000000000", "Ancienne", quand=1_600_000_000
    )
    _pose_transcript(
        dossier, "zzzz9999-9999-9999-9999-999999999999", "Récente", quand=1_700_000_000
    )

    acheve = depot.lance("sessions", "152")
    assert acheve.stdout.index("Récente") < acheve.stdout.index("Ancienne")


def test_sessions_suit_le_dossier_de_worktrees_impose(depot: Depot) -> None:
    """`MAESTRO_WORKTREE_DIR` déplace les worktrees, donc l'encodage, donc ce qui est à trouver.

    Figer « maestro-worktrees » dans le verbe le ferait répondre juste sur cette machine et vide
    partout ailleurs — un silence indiscernable de « ce ticket n'a pas de session ».
    """
    ailleurs = (
        depot.home
        / ".claude"
        / "projects"
        / _encode_chemin(depot.racine.parent / "autre-dossier" / "152-essai")
    )
    _pose_transcript(ailleurs, "eeee1111-2222-3333-4444-5555", "Hors du dossier imposé")
    _pose_transcript(
        _bucket(depot, "152"), "ffff1111-2222-3333-4444-5555", "Dans le dossier imposé"
    )

    acheve = depot.lance("sessions", "152")
    assert "Dans le dossier imposé" in acheve.stdout
    assert "Hors du dossier imposé" not in acheve.stdout, (
        "le verbe a cherché ailleurs que dans MAESTRO_WORKTREE_DIR"
    )


def test_sessions_tous_liste_tous_les_tickets(depot: Depot) -> None:
    """`--tous` : l'inventaire — par où l'on entre quand on ne sait plus de quel ticket il s'agit.

    C'était le comportement du verbe SANS argument jusqu'à #397 ; il n'a pas disparu, il se demande.
    """
    _pose_transcript(_bucket(depot, "152"), "aaaa1111-1111-1111-1111-111111111111", "Un")
    _pose_transcript(_bucket(depot, "207"), "bbbb2222-2222-2222-2222-222222222222", "Deux")

    acheve = depot.lance("sessions", "--tous")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#152" in acheve.stdout
    assert "#207" in acheve.stdout
    assert "2 session(s)." in acheve.stdout


def test_sessions_ignore_la_casse_de_la_lettre_de_lecteur(depot: Depot) -> None:
    """Claude Code encode le chemin TEL QU'IL LUI A ÉTÉ DONNÉ, sans le normaliser.

    Sur la machine de référence, le clone principal est rangé sous « e-- » et ses worktrees sous
    « E-- » : un motif sensible à la casse en manquerait la moitié, silencieusement. Ce test ne
    prouve quelque chose que sur un système de fichiers sensible à la casse (le job CI) ; sous
    Windows il passe par construction, ce qui est sans danger — l'invariant y est vrai aussi.
    """
    attendu = _bucket(depot, "152")
    variante = attendu.with_name(attendu.name.upper())
    _pose_transcript(variante, "aaaa3333-3333-3333-3333-333333333333", "Casse inversée")

    acheve = depot.lance("sessions", "152")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "Casse inversée" in acheve.stdout


def test_sessions_refuse_un_iid_qui_n_en_est_pas_un(depot: Depot) -> None:
    """Un argument mal formé se dit, il ne se traduit pas en « aucune session » (verdict faux)."""
    acheve = depot.lance("sessions", "chore/152")
    assert acheve.returncode == 2
    assert "iid attendu" in acheve.stdout + acheve.stderr


def test_sessions_est_franc_quand_il_n_y_a_rien(depot: Depot) -> None:
    """Aucun historique sur la machine : le dire, plutôt que rendre une liste vide sans explication.

    La portée du verbe est celle de CE poste (comme `gc` et `reconcile-workflow`) : un transcript
    vit là où il a été produit, et confondre « pas ici » avec « nulle part » ferait conclure à tort
    qu'une session est perdue.
    """
    acheve = depot.lance("sessions", "152")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "Aucun historique de session" in acheve.stdout
    assert "cette machine" in acheve.stdout


# --- Sessions du dossier courant (#397) ---------------------------------------------------------
# La dérivation par iid de #385 ne couvre que les worktrees, alors que la question la plus fréquente
# se pose LÀ OÙ L'ON EST : « je rouvre VS Code dans ce dossier, qu'est-ce que je reprends ? ». Le
# clone principal, d'où l'on travaille le plus souvent, en était exclu. Ce que ces tests épinglent :
# le mode par défaut regarde le répertoire courant, le NOM d'onglet vient du registre — la seule
# source qui le porte, et elle survit à la fermeture de VS Code — et une liste bornée le DIT.


def test_sessions_sans_iid_rend_le_dossier_courant(depot: Depot) -> None:
    """Le cas qui motive le ticket : le clone principal, qu'aucun iid ne désigne."""
    _pose_transcript(
        _bucket_dossier(depot, depot.racine), "aaaa1111-1111-1111-1111-111111111111", "Ici"
    )

    acheve = depot.lance("sessions")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "Ici" in acheve.stdout
    assert "claude --resume aaaa1111-1111-1111-1111-111111111111" in acheve.stdout
    assert "1 session(s) ici." in acheve.stdout


def test_sessions_sans_iid_ignore_les_sessions_des_autres_dossiers(depot: Depot) -> None:
    """« Ce dossier » veut dire ce dossier : une session de worktree n'est pas reprenable ici.

    Elle est ANNONCÉE — le renvoi vers `--tous` existe pour ça — mais jamais mêlée à la liste, où
    elle ferait proposer une reprise dans un répertoire qui n'est pas celui de la session.
    """
    _pose_transcript(_bucket(depot, "152"), "bbbb2222-2222-2222-2222-222222222222", "Ailleurs")

    acheve = depot.lance("sessions")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "Ailleurs" not in acheve.stdout
    assert "aucune session enregistrée pour ce dossier" in acheve.stdout
    assert "sessions --tous" in acheve.stdout


def test_sessions_nomme_l_onglet_d_apres_le_registre(depot: Depot) -> None:
    """Le nom de l'onglet ne vit que dans le registre — aucun transcript ne le porte.

    C'est pourtant le repère par lequel on reconnaît la session cherchée : un titre est posé en
    cours de route, parfois jamais, alors que le nom est celui qu'on a eu sous les yeux.
    """
    _pose_transcript(
        _bucket_dossier(depot, depot.racine), "cccc3333-3333-3333-3333-333333333333", "Un titre"
    )
    _pose_fiche(depot, 4242, "cccc3333-3333-3333-3333-333333333333", "maestro-d3")

    acheve = depot.lance("sessions")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "[maestro-d3] Un titre" in acheve.stdout


def test_sessions_retient_le_nom_de_la_fiche_la_plus_recente(depot: Depot) -> None:
    """Une session REPRISE garde son identifiant sous un nouveau PID : trois fiches pour un même id.

    Le nom à rendre est celui de la dernière — c'est le dernier qu'on a vu à l'écran ; prendre le
    premier fichier venu ferait dépendre le rendu de l'ordre du système de fichiers.
    """
    _pose_transcript(
        _bucket_dossier(depot, depot.racine), "dddd4444-4444-4444-4444-444444444444", "Titre"
    )
    _pose_fiche(depot, 111, "dddd4444-4444-4444-4444-444444444444", "ancien-nom", debut=1_000)
    _pose_fiche(depot, 222, "dddd4444-4444-4444-4444-444444444444", "nom-du-jour", debut=2_000)

    acheve = depot.lance("sessions")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "[nom-du-jour]" in acheve.stdout
    assert "ancien-nom" not in acheve.stdout


def test_sessions_rend_la_session_meme_sans_registre(depot: Depot) -> None:
    """Le registre enrichit, il ne conditionne pas : sans fiche, la session reste reprenable.

    Un poste peut n'avoir aucun registre (session lancée hors VS Code, fiche ramassée) — le rendu ne
    doit pas y perdre la seule chose qui compte, l'identifiant de reprise.
    """
    _pose_transcript(
        _bucket_dossier(depot, depot.racine), "eeee5555-5555-5555-5555-555555555555", "Sans fiche"
    )
    assert not (depot.home / ".claude" / "sessions").exists()

    acheve = depot.lance("sessions")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "Sans fiche" in acheve.stdout
    assert "claude --resume eeee5555-5555-5555-5555-555555555555" in acheve.stdout
    assert "[" not in acheve.stdout


def test_sessions_lit_le_registre_de_claude_config_dir(depot: Depot) -> None:
    """`CLAUDE_CONFIG_DIR` prime sur `HOME` — pour le registre comme pour les transcripts.

    Les deux doivent suivre la MÊME configuration : un registre lu dans `HOME` pendant que les
    transcripts viennent d'ailleurs rendrait des noms appartenant à une autre installation.
    """
    ailleurs = depot.home / "config-a-part"
    (ailleurs / "projects").mkdir(parents=True)
    _pose_transcript(
        ailleurs / "projects" / _encode_chemin(depot.racine),
        "ffff6666-6666-6666-6666-666666666666",
        "Config à part",
    )
    (ailleurs / "sessions").mkdir()
    (ailleurs / "sessions" / "9.json").write_text(
        '{"pid":9,"sessionId":"ffff6666-6666-6666-6666-666666666666",'
        '"startedAt":1787000000000,"name":"config-a-part","nameSource":"derived"}\n',
        encoding="utf-8",
        newline="\n",
    )

    acheve = depot.lance("sessions", environnement={"CLAUDE_CONFIG_DIR": str(ailleurs)})
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "[config-a-part] Config à part" in acheve.stdout


def test_sessions_borne_la_liste_et_annonce_ce_qu_elle_tait(depot: Depot) -> None:
    """Le clone principal de référence compte 192 transcripts, soit 390 lignes de sortie.

    Tout rendre, c'est reperdre la conversation d'hier dans le flot ; en rendre 10 sans le dire,
    c'est faire passer une troncature pour un inventaire — et conclure qu'une session n'existe plus.
    """
    bucket = _bucket_dossier(depot, depot.racine)
    for rang in range(12):
        _pose_transcript(bucket, f"aaaa0000-0000-0000-0000-00000000{rang:04d}", f"Session {rang}")

    acheve = depot.lance("sessions")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert acheve.stdout.count("claude --resume ") == 10
    assert "12 session(s) ici." in acheve.stdout
    assert "2 plus anciennes non listées" in acheve.stdout


def test_sessions_limite_zero_rend_tout(depot: Depot) -> None:
    """L'échappatoire qu'annonce la troncature doit exister — et tout rendre, sans rien taire."""
    bucket = _bucket_dossier(depot, depot.racine)
    for rang in range(12):
        _pose_transcript(bucket, f"bbbb0000-0000-0000-0000-00000000{rang:04d}", f"Session {rang}")

    acheve = depot.lance("sessions", "--limite", "0")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert acheve.stdout.count("claude --resume ") == 12
    assert "plus anciennes non listées" not in acheve.stdout


def test_sessions_refuse_une_limite_qui_n_en_est_pas_une(depot: Depot) -> None:
    """Une limite illisible se dit : repliée en silence sur le défaut, elle tronquerait à tort."""
    acheve = depot.lance("sessions", "--limite", "beaucoup")
    assert acheve.returncode == 2
    assert "--limite attend un nombre" in acheve.stdout + acheve.stderr


def test_sessions_refuse_tous_avec_un_iid(depot: Depot) -> None:
    """Les deux portées s'excluent : en servir une en silence rendrait ce qu'on n'a pas demandé."""
    acheve = depot.lance("sessions", "--tous", "152")
    assert acheve.returncode == 2
    assert "ne se combine pas" in acheve.stdout + acheve.stderr


def test_sessions_ne_rend_un_identifiant_qu_une_fois(depot: Depot) -> None:
    """Une session reprise ailleurs laisse un transcript de MÊME identifiant dans deux répertoires.

    Le rendre deux fois ferait croire à deux conversations, et gonflerait un compte qui sert à
    décider si l'on a tout vu.
    """
    _pose_transcript(_bucket(depot, "152"), "cccc7777-7777-7777-7777-777777777777", "Reprise")
    _pose_transcript(_bucket(depot, "207"), "cccc7777-7777-7777-7777-777777777777", "Reprise")

    acheve = depot.lance("sessions", "--tous")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert acheve.stdout.count("claude --resume cccc7777-7777-7777-7777-777777777777") == 1
    assert "1 session(s)." in acheve.stdout


def test_gc_nomme_les_sessions_du_worktree_qu_il_retire(depot: Depot) -> None:
    """Le retrait n'efface pas les transcripts, mais il coupe le seul chemin qui les montrait.

    C'est l'instant où l'information disparaît de l'écran : après coup, plus rien ne rappellera
    qu'il y avait un historique ni par quoi le rouvrir.
    """
    depot.lance("create", "152", "--branche", BRANCHE)
    _pose_transcript(_bucket(depot, "152"), "aaaa4444-4444-4444-4444-4444", "Travail")
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée")})

    acheve = depot.lance("gc")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#152 retiré" in acheve.stdout
    assert "1 session(s) conservée(s)" in acheve.stdout
    assert "worktree.sh sessions 152" in acheve.stdout
    assert not depot.worktree().exists()


def test_gc_ne_parle_pas_de_sessions_quand_il_n_y_en_a_pas(depot: Depot) -> None:
    """La mention est portée par un fait, pas par le passage : sans transcript, rien à dire."""
    depot.lance("create", "152", "--branche", BRANCHE)
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée")})

    acheve = depot.lance("gc")
    assert "#152 retiré" in acheve.stdout
    assert "session(s) conservée(s)" not in acheve.stdout


def test_gc_check_annonce_les_sessions_sans_rien_retirer(depot: Depot) -> None:
    """`--check` sert à décider : il doit montrer ce que le retrait rendrait moins accessible."""
    depot.lance("create", "152", "--branche", BRANCHE)
    _pose_transcript(_bucket(depot, "152"), "aaaa5555-5555-5555-5555-5555", "Travail")
    depot.impose_verdicts({"152": _verdict_ligne("fini", "PR #42 mergée")})

    acheve = depot.lance("gc", "--check")
    assert "#152 à retirer" in acheve.stdout
    assert "1 session(s) conservée(s)" in acheve.stdout
    assert depot.worktree().exists(), "--check ne retire rien"
