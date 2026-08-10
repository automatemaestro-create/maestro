"""Tests de la boucle d'orchestration autonome — `scripts/orchestrate/` (tickets #172 et #175).

Tests différés des lots #168 à #171 (parent #167) puis #176-#177 (parent #174), réunis ici selon
la convention de découpage (`docs/10-workflow-git.md` §5.1).

**Ni réseau, ni quota, ni écriture GitLab.** Trois bouchons posés en tête de `PATH` ou par
variable d'environnement remplacent tout ce qui sortirait de la machine :

* `glab` — un script qui répond aux quelques appels que `scripts/gitlab/lib.sh` émet
  (GraphQL des milestones, du backlog, du statut d'un ticket ; `issue view` ; `mr view`), à
  partir d'un **état écrit par le test** dans un dossier de fixtures. Aucune requête ne part.
* `claude` — via `MAESTRO_CLAUDE_BIN` : un script qui joue le scénario voulu (succès, limite
  d'usage, reprise) et **ne consomme aucun quota**.
* le montage de worktree — via `MAESTRO_ORCHESTRATE_WORKTREE` : une commande qui imprime un
  dossier déjà là, donc **aucune branche ni aucun worktree réels** ne sont créés.
* l'ouverture d'une console — via `MAESTRO_ORCHESTRATE_SPAWN` (#173) : une commande qui reçoit le
  lanceur au lieu qu'une vraie fenêtre s'ouvre, donc **aucune console** ne surgit pendant les tests.

**Un dépôt jetable.** Chaque test monte dans `tmp_path` un mini-clone qui ne porte que les
scripts visés, `scripts/gitlab/lib.sh` et un `.claude/settings.json` synthétique. Le vrai
dépôt n'est jamais touché : `HOME` et `TMPDIR` sont eux aussi redirigés.

`status.sh` (#177) lit en plus **le dépôt lui-même** (branche, worktree, commits) : les rares
tests qui portent là-dessus initialisent un vrai dépôt git dans `tmp_path` — toujours en local,
sans `origin` distant, avec une simple référence `refs/remotes/origin/main` posée à la main.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import subprocess
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from conftest import CLE_COULEUR_ORCHESTRATE  # le conftest du dossier, sur le sys.path de pytest

RACINE = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash introuvable")
besoin_git = pytest.mark.skipif(GIT is None, reason="git introuvable")

# Les scripts sous test, recopiés tels quels dans le dépôt jetable.
SCRIPTS = (
    "scripts/gitlab/lib.sh",
    "scripts/orchestrate/queue.sh",
    "scripts/orchestrate/guard.sh",
    "scripts/orchestrate/run.sh",
    "scripts/orchestrate/status.sh",
    "scripts/orchestrate/pilote.sh",
    "scripts/orchestrate/journal.sh",
    "scripts/orchestrate/settings.run.json",
)

# Le bouchon `glab`. Il ne cherche pas à imiter GitLab : il répond au strict nécessaire, en lisant
# des fichiers que le test a écrits. Le dispatch se fait sur des fragments de la requête GraphQL
# telle que lib.sh la compose — si lib.sh change de requête, ces tests le diront.
STUB_GLAB = r"""#!/usr/bin/env bash
FIX="$MAESTRO_FIXTURES"
# Tout appel est journalisé : c'est ce qui permet de vérifier qu'une option comme `--no-gitlab`
# n'interroge VRAIMENT rien, plutôt que de se contenter du message qu'elle imprime.
printf '%s\n' "$*" >> "$FIX/glab.log"
case "$1 $2" in
  "auth status") exit 0 ;;
esac
if [ "$1" = "api" ] && [ "$2" = "graphql" ]; then
  requete="$*"
  case "$requete" in
    *"milestones("*)      cat "$FIX/milestones.json" 2>/dev/null; exit 0 ;;
    *"milestoneTitle:"*)
      # Le titre demandé sert de clé : sans ça, deux milestones rendraient forcément la même table
      # de tickets, et `queue.sh --milestones` ne pourrait pas être testé sur des comptes distincts.
      # Espaces → « _ » (les titres des tests sont en ASCII, cf. milestone_tickets côté Python).
      titre="${requete#*milestoneTitle:[\"}"; titre="${titre%%\"*}"
      par_titre="$FIX/milestone-issues-${titre// /_}.json"
      if [ -f "$par_titre" ]; then cat "$par_titre"; else
        cat "$FIX/milestone-issues.json" 2>/dev/null
      fi
      exit 0 ;;
    *"workItems(state:"*) cat "$FIX/backlog.json" 2>/dev/null; exit 0 ;;
    *"mergeRequests("*)   cat "$FIX/mr-iid.json" 2>/dev/null; exit 0 ;;
    *'workItems(iids:["'*)
      iid="${requete#*workItems(iids:[\"}"; iid="${iid%%\"*}"
      if [ -f "$FIX/owner-$iid.json" ]; then cat "$FIX/owner-$iid.json"; else
        printf '{"data":{"project":{"workItems":{"nodes":[]}}}}'
      fi
      exit 0 ;;
  esac
  exit 1
fi
if [ "$1" = "issue" ] && [ "$2" = "view" ]; then
  [ -f "$FIX/issue-$3.txt" ] || exit 1
  cat "$FIX/issue-$3.txt"; exit 0
fi
if [ "$1" = "mr" ] && [ "$2" = "view" ]; then
  # Une branche porte des « / » : le nom du fichier de fixture les remplace (comme côté Python).
  ref="${3//\//__}"
  [ -f "$FIX/mr-$ref.json" ] || exit 1
  cat "$FIX/mr-$ref.json"; exit 0
fi
exit 1
"""

# Le bouchon de montage de worktree : il imprime un dossier qui existe déjà, sans rien créer.
STUB_WORKTREE = """#!/usr/bin/env bash
printf '%s\\n' "$MAESTRO_STUB_WORKTREE_DIR"
"""


# Cycle de vie : libellé (surface) -> slug (stockage du label). Depuis #209 le cycle de vie est
# porté par un label `workflow::<slug>` et non plus par le champ Status natif — les bouchons GraphQL
# doivent donc répondre un widget Labels. Les tests continuent d'écrire et d'attendre le LIBELLÉ
# (« À faire »), conformément au contrat de surface documenté en tête de scripts/gitlab/lib.sh.
_SLUG_WORKFLOW = {
    "À faire": "a-faire",
    "En cours": "en-cours",
    "En revue": "en-revue",
    "Terminé": "termine",
    "Abandonné": "abandonne",
    "Doublon": "doublon",
}


def _label_workflow(statut: str) -> str:
    """Le nœud de label portant le cycle de vie, ou une chaîne vide si `statut` est vide."""
    if not statut:
        return ""
    return f'{{"title":"workflow::{_SLUG_WORKFLOW.get(statut, statut)}"}}'


def _statut_json(iid: str, statut: str, assigne: str = "") -> str:
    """La réponse GraphQL que `gl_issue_owner` sait lire."""
    assignes = f'{{"username":"{assigne}"}}' if assigne else ""
    return (
        f'{{"data":{{"project":{{"workItems":{{"nodes":[{{"iid":"{iid}","widgets":['
        f'{{"labels":{{"nodes":[{_label_workflow(statut)}]}}}},'
        f'{{"assignees":{{"nodes":[{assignes}]}}}}]}}]}}}}}}}}'
    )


@dataclass
class Depot:
    """Un dépôt jetable, ses bouchons et de quoi lancer les scripts dessus."""

    racine: Path
    fixtures: Path
    env: dict[str, str]
    tickets: dict[str, dict] = field(default_factory=dict)

    # --- Mise en place de l'état GitLab simulé ---------------------------------------------------
    def milestone(self, titre: str) -> None:
        self.milestones([(titre, "active", 3, 10)])

    def milestones(self, jalons: list[tuple[str, str, int, int]]) -> None:
        """La table des milestones du projet : (titre, état, fermés, total) chacun.

        Les dates sont fixes : `gl_current_milestone` trie déjà côté API (`sort: DUE_DATE_ASC`) et
        le bouchon rend les nœuds dans l'ordre où on les écrit — c'est donc cet ordre-là qui fait
        foi dans les tests, pas les dates.
        """
        noeuds = ",".join(
            f'{{"title":"{t}","state":"{etat}","startDate":"2026-01-01","dueDate":"2026-12-31",'
            f'"stats":{{"totalIssuesCount":{total},"closedIssuesCount":{fermes}}}}}'
            for t, etat, fermes, total in jalons
        )
        (self.fixtures / "milestones.json").write_text(
            f'{{"data":{{"project":{{"milestones":{{"nodes":[{noeuds}]}}}}}}}}',
            encoding="utf-8",
        )

    def milestone_tickets(self, titre: str, iids: list[int]) -> None:
        """Les tickets d'UN milestone donné (les autres gardent la table de `publie`).

        Le bouchon `glab` retrouve ce fichier par le titre demandé — d'où des titres ASCII dans les
        tests qui s'en servent, la clé n'étant qu'un remplacement des espaces par « _ ».
        """
        noeuds = ",".join(self._noeud(str(iid)) for iid in iids)
        (self.fixtures / f"milestone-issues-{titre.replace(' ', '_')}.json").write_text(
            f'{{"data":{{"project":{{"workItems":{{"nodes":[{noeuds}]}}}}}}}}',
            encoding="utf-8",
        )

    def ticket(
        self,
        iid: int,
        titre: str,
        *,
        statut: str = "À faire",
        prio: str = "moyenne",
        type_: str = "feature",
        assigne: str = "",
        parent: int | None = None,
        lots: list[tuple[int, str, bool]] | None = None,
    ) -> None:
        """Déclare un ticket : son statut, ses labels, et son rôle éventuel de lot ou de parent."""
        corps = f"Sous-ticket de #{parent} — lot 1/5.\n" if parent else ""
        if lots:
            corps += "\n## Sous-tickets\n\n" + "".join(
                f"- [ ] #{i} — {t}{' (parallèle)' if p else ''}\n" for i, t, p in lots
            )
        (self.fixtures / f"issue-{iid}.txt").write_text(
            f"title:\t{titre}\nstate:\topen\nlabels:\tagent::dev, prio::{prio}, type::{type_}\n"
            f"assignees:\t{assigne}\n--\n{corps}\n",
            encoding="utf-8",
        )
        (self.fixtures / f"owner-{iid}.json").write_text(
            _statut_json(str(iid), statut, assigne), encoding="utf-8"
        )
        self.tickets[str(iid)] = {
            "titre": titre, "statut": statut, "prio": prio, "type": type_, "assigne": assigne
        }

    def _noeud(self, iid: str) -> str:
        """Un ticket déclaré, au format de nœud que les deux tables partagent."""
        t = self.tickets[iid]
        assignes = f'{{"username":"{t["assigne"]}"}}' if t["assigne"] else ""
        workflow = _label_workflow(t["statut"])
        return (
            f'{{"iid":"{iid}","title":"{t["titre"]}","state":"opened","widgets":['
            f'{{"labels":{{"nodes":[{{"title":"type::{t["type"]}"}},'
            f'{{"title":"prio::{t["prio"]}"}},{{"title":"agent::dev"}}'
            f'{"," + workflow if workflow else ""}]}}}},'
            f'{{"assignees":{{"nodes":[{assignes}]}}}}]}}'
        )

    def publie(self) -> None:
        """Compose les deux tables que `queue.sh` lit (milestone et backlog) depuis les tickets."""
        jointure = ",".join(self._noeud(iid) for iid in self.tickets)
        charge = f'{{"data":{{"project":{{"workItems":{{"nodes":[{jointure}]}}}}}}}}'
        (self.fixtures / "milestone-issues.json").write_text(charge, encoding="utf-8")
        (self.fixtures / "backlog.json").write_text(charge, encoding="utf-8")

    def mr(self, branche: str, etat: str = "opened", iid: int = 99) -> None:
        # Le nom du fichier aplatit les « / » de la branche — le bouchon fait la même chose.
        (self.fixtures / f"mr-{branche.replace('/', '__')}.json").write_text(
            f'{{"iid":{iid},"state":"{etat}","draft":true}}', encoding="utf-8"
        )
        (self.fixtures / "mr-iid.json").write_text(
            f'{{"data":{{"project":{{"mergeRequests":{{"nodes":[{{"iid":"{iid}"}}]}}}}}}}}',
            encoding="utf-8",
        )

    # --- Lancement -------------------------------------------------------------------------------
    def lance(
        self,
        script: str,
        *args: str,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess:
        # Le script est appelé par son chemin ABSOLU : les scripts se repèrent sur `BASH_SOURCE`,
        # donc ils doivent marcher depuis n'importe quel répertoire — `cwd` sert à le vérifier.
        return subprocess.run(
            [BASH, str(self.racine / "scripts/orchestrate" / script), *args],
            cwd=cwd or self.racine,
            env={**self.env, **(env or {})},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )


@pytest.fixture
def depot(tmp_path: Path) -> Depot:
    racine = tmp_path / "depot"
    fixtures = tmp_path / "fixtures"
    binaires = tmp_path / "bin"
    for d in (racine, fixtures, binaires, tmp_path / "home", tmp_path / "tmp"):
        d.mkdir(parents=True, exist_ok=True)

    for rel in SCRIPTS:
        cible = racine / rel
        cible.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RACINE / rel, cible)

    # Un .claude/settings.json synthétique : `guard.sh --check` compare les `deny` du dépôt à ceux
    # de settings.run.json, il lui faut donc les deux fichiers.
    (racine / ".claude").mkdir(parents=True, exist_ok=True)
    reference = json.loads((RACINE / ".claude/settings.json").read_text(encoding="utf-8"))
    (racine / ".claude/settings.json").write_text(
        json.dumps({"permissions": reference["permissions"]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    (binaires / "glab").write_text(STUB_GLAB, encoding="utf-8", newline="\n")
    (binaires / "glab").chmod(0o755)
    (binaires / "worktree-stub").write_text(STUB_WORKTREE, encoding="utf-8", newline="\n")
    (binaires / "worktree-stub").chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{binaires}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path / "tmp"),
        "MAESTRO_FIXTURES": str(fixtures),
        "MAESTRO_STUB_WORKTREE_DIR": str(racine),
        "MAESTRO_ORCHESTRATE_WORKTREE": str(binaires / "worktree-stub"),
        "GL_GQL_RETRIES": "1",
        "GL_GQL_RETRY_DELAY": "0",
    }
    return Depot(racine=racine, fixtures=fixtures, env=env)


def _claude_stub(depot: Depot, corps: str) -> str:
    """Écrit un bouchon de `claude` et renvoie son chemin."""
    chemin = depot.racine.parent / "bin" / "claude-stub"
    chemin.write_text(
        "#!/usr/bin/env bash\n" + textwrap.dedent(corps), encoding="utf-8", newline="\n"
    )
    chemin.chmod(0o755)
    return str(chemin)


def _spawn_stub(depot: Depot, corps: str = "") -> str:
    """Bouchon de `MAESTRO_ORCHESTRATE_SPAWN` : note le lanceur reçu, n'ouvre aucune console."""
    chemin = depot.racine.parent / "bin" / "spawn-stub"
    chemin.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$1" > "$MAESTRO_FIXTURES/spawn.txt"\n'
        + textwrap.dedent(corps),
        encoding="utf-8",
        newline="\n",
    )
    chemin.chmod(0o755)
    return str(chemin)


def _groupe(parent: str, rang: int) -> str:
    """Le groupe de dépendance posé par queue.sh (#288) : « - » hors lot, « <parent>.<n> » sinon."""
    return "-" if parent == "-" else f"{parent}.{rang}"


def _plan(depot: Depot, lignes: list[tuple[int, int, str, str]]) -> str:
    """Écrit un plan figé (le TSV que queue.sh produit) et renvoie son chemin."""
    chemin = depot.racine / "plan.tsv"
    contenu = "# rang\tiid\tparent\tprio\tgroupe\ttitre\n" + "".join(
        f"{rang}\t{iid}\t{parent}\t{prio}\t{_groupe(parent, rang)}\tTicket {iid}\n"
        for rang, iid, parent, prio in lignes
    )
    chemin.write_text(contenu, encoding="utf-8", newline="\n")
    return str(chemin)


def _lignes_du_plan(sortie: str) -> list[list[str]]:
    return [ligne.split("\t") for ligne in sortie.splitlines()
            if ligne and not ligne.startswith("#")]


# =====================================================================================
# queue.sh — l'ordre de traitement (#168)
# =====================================================================================

def _backlog_type(depot: Depot) -> None:
    """Un parent de suivi et ses cinq lots, plus un ticket isolé — le cas de référence."""
    depot.milestone("Phase X")
    depot.ticket(500, "Parent de suivi", lots=[(501, "Lot 1", False), (502, "Lot 2", False),
                                               (503, "Lot 3", False)])
    for i, titre in ((501, "Lot 1"), (502, "Lot 2"), (503, "Lot 3")):
        depot.ticket(i, titre, parent=500)
    depot.ticket(600, "Ticket isolé prioritaire", prio="haute")
    depot.publie()


def test_le_plan_ecarte_le_parent_et_garde_les_lots_dans_l_ordre(depot: Depot) -> None:
    _backlog_type(depot)
    r = depot.lance("queue.sh")
    assert r.returncode == 0, r.stderr
    iids = [ligne[1] for ligne in _lignes_du_plan(r.stdout)]
    assert "500" not in iids, "le parent de suivi ne porte ni branche ni code : il ne se traite pas"
    assert iids == ["600", "501", "502", "503"], (
        "le ticket prioritaire passe devant, puis les lots dans l'ordre de la checklist"
    )


def test_les_lots_d_un_meme_parent_restent_contigus(depot: Depot) -> None:
    """Un ticket isolé de priorité moyenne ne doit pas s'intercaler entre deux lots."""
    depot.milestone("Phase X")
    depot.ticket(500, "Parent", lots=[(501, "Lot 1", False), (502, "Lot 2", False)])
    depot.ticket(501, "Lot 1", parent=500)
    depot.ticket(502, "Lot 2", parent=500)
    depot.ticket(501 + 1000, "Isolé au milieu des iids", prio="moyenne")
    depot.publie()
    iids = [ligne[1] for ligne in _lignes_du_plan(depot.lance("queue.sh").stdout)]
    assert iids.index("502") - iids.index("501") == 1


def test_le_plan_ecarte_les_tickets_pris_et_les_statuts_autres(depot: Depot) -> None:
    depot.milestone("Phase X")
    depot.ticket(700, "Libre")
    depot.ticket(701, "Pris par quelqu'un", assigne="alice")
    depot.ticket(702, "Déjà en cours", statut="En cours")
    depot.ticket(703, "Déjà livré", statut="Terminé")
    depot.publie()
    r = depot.lance("queue.sh", "--check")
    iids = [ligne[1] for ligne in _lignes_du_plan(r.stdout)]
    assert iids == ["700"]
    assert "assigné à alice" in r.stderr
    assert "En cours" in r.stderr and "Terminé" in r.stderr


def test_le_plan_est_reproductible(depot: Depot) -> None:
    _backlog_type(depot)
    assert depot.lance("queue.sh").stdout == depot.lance("queue.sh").stdout


def test_plan_vide_sort_l_en_tete_sans_erreur(depot: Depot) -> None:
    depot.milestone("Phase X")
    depot.ticket(800, "Tout est fait", statut="Terminé")
    depot.publie()
    r = depot.lance("queue.sh")
    assert r.returncode == 0
    assert r.stdout.startswith("# rang")
    assert _lignes_du_plan(r.stdout) == []


# =====================================================================================
# guard.sh — le garde-fou en dur (#169)
# =====================================================================================

INTERDITS = [
    "git push --force origin main",
    "git push -f",
    "git push --force-with-lease origin x",
    "glab mr merge 143",
    "glab mr close 143",
    "glab ci delete 1",
    "git reset --hard HEAD~1",
    "git commit --no-verify -m x",
    "npm test && git push --force",
]

AUTORISES = [
    "git push -u origin chore/1-x",
    "git commit -m 'feat: x'",
    "npm test",
    "glab mr create --draft",
    "glab ci retry 1",
    "git reset --soft HEAD~1",
    "cat -n fichier.txt",
]


@pytest.mark.parametrize("commande", INTERDITS)
def test_le_garde_fou_refuse_les_gestes_irreversibles(depot: Depot, commande: str) -> None:
    r = depot.lance("guard.sh", "--test", commande)
    assert r.returncode == 2, f"« {commande} » aurait dû être refusé : {r.stdout}"
    assert "REFUSÉ" in r.stdout


@pytest.mark.parametrize("commande", AUTORISES)
def test_le_garde_fou_laisse_passer_le_travail_ordinaire(depot: Depot, commande: str) -> None:
    r = depot.lance("guard.sh", "--test", commande)
    assert r.returncode == 0, f"« {commande} » aurait dû passer : {r.stdout}"
    assert "AUTORISÉ" in r.stdout


def test_le_garde_fou_ne_juge_que_les_appels_bash(depot: Depot) -> None:
    """Ce dépôt DOCUMENTE les commandes interdites : les écrire ne doit pas être refusé."""
    charge = json.dumps(
        {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "docs/10.md",
                "content": "Ne jamais lancer glab mr merge ni git push --force.",
            },
        }
    )
    r = subprocess.run(
        [BASH, "scripts/orchestrate/guard.sh"],
        cwd=depot.racine, env=depot.env, input=charge,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert r.returncode == 0, "un Write de documentation n'est pas un appel Bash"


def test_le_garde_fou_bloque_un_appel_bash_en_mode_hook(depot: Depot) -> None:
    charge = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}}
    )
    r = subprocess.run(
        [BASH, "scripts/orchestrate/guard.sh"],
        cwd=depot.racine, env=depot.env, input=charge,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert r.returncode == 2, "sortie 2 = appel bloqué (contrat PreToolUse)"
    assert "force-push" in r.stderr


def test_check_valide_la_copie_des_deny_du_depot(depot: Depot) -> None:
    r = depot.lance("guard.sh", "--check")
    assert r.returncode == 0, r.stderr


def test_check_detecte_une_regle_deny_oubliee(depot: Depot) -> None:
    """Un interdit ajouté au dépôt et oublié dans settings.run.json ne protégerait plus les runs."""
    chemin = depot.racine / ".claude/settings.json"
    reglages = json.loads(chemin.read_text(encoding="utf-8"))
    reglages["permissions"]["deny"].append("Bash(glab mr merge --yes:*)")
    chemin.write_text(json.dumps(reglages, ensure_ascii=False, indent=2), encoding="utf-8")
    r = depot.lance("guard.sh", "--check")
    assert r.returncode == 1
    assert "Bash(glab mr merge --yes:*)" in r.stderr, "la règle manquante est nommée en entier"


# =====================================================================================
# settings.run.json — l'allowlist des sessions autonomes (#179)
# =====================================================================================
# Ces trois tests gardent des décisions qui ont coûté un run entier à apprendre (§11.7). Ils lisent
# le fichier VERSIONNÉ, pas la copie du dépôt jetable : c'est le régime réel des runs qui est en
# jeu.

def _allow() -> list:
    chemin = RACINE / "scripts/orchestrate/settings.run.json"
    return json.loads(chemin.read_text(encoding="utf-8"))["permissions"]["allow"]


def test_une_session_autonome_peut_invoquer_les_skills() -> None:
    """Sans `Skill`, la session de #130 a refait le cycle /ticket-start À LA MAIN (100 tours)."""
    allow = _allow()
    assert "Skill" in allow, "le tool Skill doit être autorisé, et nu"
    # Le tool Skill ne déclare pas de `ruleContentField` (là où Bash expose `command`) : une règle
    # `Skill(ticket-start)` ne matcherait jamais rien tout en donnant l'illusion d'autoriser.
    assert not [r for r in allow if r.startswith("Skill(")], \
        "une règle Skill avec spécificateur ne matche rien — elle donnerait une fausse sécurité"


def test_le_decor_de_pipeline_est_autorise() -> None:
    """Une chaîne vaut son maillon le plus faible : un `echo` de confort la faisait tomber."""
    allow = _allow()
    for binaire in ("cd", "echo", "printf", "grep", "sed"):
        assert f"Bash({binaire}:*)" in allow, \
            f"{binaire} manquant : il ferait tomber des chaînes dont tout le reste est autorisé"


def test_les_refus_merites_ne_sont_pas_leves() -> None:
    """#178 a fermé le mode d'échec « la session attend un résultat » — ne pas le rouvrir ici."""
    allow = _allow()
    assert not [r for r in allow if r.startswith("Bash(sleep")], \
        "les attentes actives ont coûté le run de #131 : un résultat s'obtient en avant-plan"
    assert "Bash(bash:*)" not in allow, \
        "« bash » tout court exécuterait n'importe quel script, hors du dépôt compris"


# --- Ce que onze runs de plus ont appris (#235, parent #232) -------------------------------------
# 83 refus sur 16 sessions, dont quinze ne tenaient à AUCUNE règle de matching : l'outil
# était
# simplement absent de la liste, alors qu'aucun n'écrit hors du worktree. Deux d'entre eux
# revenaient à CHAQUE run par construction — `env` sur la fausse alerte de la couleur (#236),
# `node` sur un `tsc` sans script npm — et se sont payés en tours à chaque fois.

@pytest.mark.parametrize(
    ("regle", "pourquoi"),
    [
        ("Bash(env:*)", "lire l'environnement est le premier geste devant un test rouge en local"),
        ("Bash(printenv:*)", "le pendant de `env`, que les sessions essaient tout autant"),
        ("Bash(awk:*)", "le seul dépouillement de JSON sans jq ni Python, partout ici"),
        ("Bash(command -v:*)", "« cet outil est-il là ? », que les scripts posent avant d'agir"),
        ("Bash(git ls-remote:*)", "une lecture, comme `git config --get`"),
        ("Bash(git config:*)", "n'écrit au pire que dans le dépôt du worktree"),
        ("Bash(node:*)", "`npm run:*` passait, un outil sans script npm dédié non"),
        ("Bash(npx:*)", "même raison que `node`"),
    ],
)
def test_les_outils_absents_qui_ont_coute_quinze_refus_sont_autorises(
    regle: str, pourquoi: str
) -> None:
    assert regle in _allow(), f"{regle} manquant — {pourquoi}"


def test_le_destructif_et_l_interpreteur_nu_restent_dehors() -> None:
    """Deux exclusions délibérées, que le `$comment` du fichier doit continuer d'expliquer.

    Elles ne se redécouvrent pas au refus suivant : leur raison est écrite là où on regarde quand
    on s'apprête à élargir la liste.
    """
    reglages = json.loads(
        (RACINE / "scripts/orchestrate/settings.run.json").read_text(encoding="utf-8")
    )
    allow = reglages["permissions"]["allow"]
    assert not [r for r in allow if r.startswith("Bash(rm")], \
        "aucune règle de préfixe ne borne la cible de `rm` : ce serait `rm -rf <n'importe quoi>`"
    assert "Bash(bash:*)" not in allow, \
        "`bash <chemin absolu>` ferait sauter la borne des règles `Bash(bash scripts/…)`"
    commentaire = " ".join(reglages["$comment"])
    for mot in ("rm", "chemin absolu"):
        assert mot in commentaire, f"l'exclusion de « {mot} » doit être expliquée dans le fichier"
    assert "journal.sh refus" in commentaire, \
        "la boucle de retour de §11.7 ne tient que si la commande qui l'outille est nommée ici"


def test_le_prompt_nomme_les_trois_formes_qu_aucune_regle_ne_matche(depot: Depot) -> None:
    """Elles ne se devinent PAS depuis un refus, qui ne dit jamais ce qui a manqué.

    Et la plus coûteuse tombe sur la dernière action du ticket : huit sessions sur seize ont buté
    sur un `glab mr create --description` multi-ligne, puis sur le `$(cat …)` par lequel elles
    essayaient de s'en sortir. Le prompt doit donc les nommer, et dire le geste de remplacement.
    """
    depot.ticket(130, "Ticket a traiter")
    claude = _claude_stub(depot, """
        printf '%s' "$2" > "$MAESTRO_FIXTURES/prompt-formes.txt"
        printf '{"type":"result","subtype":"success","is_error":false}\\n'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    depot.lance("run.sh", "--plan", plan, "--run-id", "formes",
                env={"MAESTRO_CLAUDE_BIN": claude})

    prompt = (depot.fixtures / "prompt-formes.txt").read_text(encoding="utf-8")
    assert "SAUT DE LIGNE" in prompt
    assert "SUBSTITUTION" in prompt and "$(" in prompt
    assert "HEREDOC" in prompt
    # Nommer l'interdit ne suffit pas : sans le geste de remplacement, la session cherche, et
    # c'est cette recherche qui coûte des tours.
    assert "l'outil Write" in prompt, "le remplacement doit être nommé, pas seulement l'interdit"
    assert "CHEMIN de ce fichier" in prompt


def test_le_prefixe_de_variable_est_ecarte_avec_sa_raison_et_son_remplacement() -> None:
    """Le seul VRAI trou d'allowlist des onze runs suivant #232 — et il n'est PAS comblé (#307).

    Une règle est un préfixe de COMMANDE, or la commande commence par la variable : la seule règle
    qui matcherait devrait figer la valeur en dur, ne couvrirait que celle-là et se périmerait au
    premier port changé. Le geste est donc dans la forme — et il existe déjà.
    """
    reglages = json.loads(
        (RACINE / "scripts/orchestrate/settings.run.json").read_text(encoding="utf-8")
    )
    allow = reglages["permissions"]["allow"]
    assert not [r for r in allow if re.match(r"Bash\([A-Z_]+=", r)], (
        "une règle à préfixe de variable figerait la VALEUR : elle ne couvrirait que ce cas-là"
    )
    assert "Bash(env:*)" in allow, "`env VAR=… <commande>` est le remplacement — il doit passer"
    commentaire = " ".join(reglages["$comment"])
    assert "env VAR=" in commentaire, "l'écart n'est un choix que s'il dit par quoi remplacer"
    assert "#307" in commentaire


def test_le_prompt_designe_un_atelier_dans_le_worktree(depot: Depot) -> None:
    """Interdire `/tmp` sans DÉSIGNER un remplaçant ne fait que déplacer le refus (#307).

    Une session écrit forcément ses fichiers de travail quelque part, et les deux endroits qu'elle
    connaît spontanément sont hors du répertoire de travail. C'est la cause n°1 des refus : 9 sur
    12 du dernier run complet.
    """
    depot.ticket(130, "Ticket a traiter")
    claude = _claude_stub(depot, """
        printf '%s' "$2" > "$MAESTRO_FIXTURES/prompt-atelier.txt"
        printf '{"type":"result","subtype":"success","is_error":false}\\n'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    depot.lance("run.sh", "--plan", plan, "--run-id", "atelier",
                env={"MAESTRO_CLAUDE_BIN": claude})

    prompt = (depot.fixtures / "prompt-atelier.txt").read_text(encoding="utf-8")
    assert ".maestro/session/" in prompt, "l'endroit désigné doit être nommé, pas sous-entendu"
    assert "/tmp" in prompt, "et celui qu'on remplace aussi, sinon la session y retourne"
    assert "env VAR=" in prompt, "le remplacement du préfixe de variable (#307) se dit ici aussi"


# =====================================================================================
# run.sh — la boucle (#170)
# =====================================================================================

def test_dry_run_n_execute_rien_et_ne_laisse_aucun_run(depot: Depot) -> None:
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "essai")
    assert r.returncode == 0, r.stderr
    assert "#130" in r.stdout
    assert not (depot.racine / ".maestro/orchestrate/essai").exists()


def test_un_ticket_reussi_est_celui_dont_gitlab_atteste_l_etat(depot: Depot) -> None:
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    # Le bouchon bascule le statut du ticket comme /ticket-ship le ferait : le run doit le lire
    # « À faire » avant de le prendre, « En revue » après la session.
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":3.5}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "ok", env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    resume = (depot.racine / ".maestro/orchestrate/ok/resume.tsv").read_text(encoding="utf-8")
    assert "130	OK	99" in resume
    assert "3.5" in resume, "le cout de la session est consigne"


def test_une_session_qui_se_dit_reussie_sans_mr_est_un_echec(depot: Depot) -> None:
    """Le verdict vient de GitLab, jamais de la prose de la session."""
    depot.ticket(130, "Ticket à traiter")
    claude = _claude_stub(depot, """
        printf '{"type":"result","subtype":"success","is_error":false,"result":"tout est fait !"}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "menteur",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 1
    chemin = depot.racine / ".maestro/orchestrate/menteur/resume.tsv"
    assert "ECHEC" in chemin.read_text(encoding="utf-8")


def test_un_echec_fait_sauter_les_lots_suivants_du_meme_parent(depot: Depot) -> None:
    for iid in (501, 502, 601):
        depot.ticket(iid, f"Ticket {iid}", parent=500 if iid < 600 else None)
    claude = _claude_stub(depot, 'printf \'{"is_error":true,"result":"boom"}\'\nexit 1\n')
    plan = _plan(
        depot,
        [(1, 501, "500", "haute"), (2, 502, "500", "haute"), (3, 601, "-", "haute")],
    )
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "casse",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    resume = (depot.racine / ".maestro/orchestrate/casse/resume.tsv").read_text(encoding="utf-8")
    assert "502\tSAUTE" in resume, "un lot dont le prédécesseur a échoué part d'une base incomplète"
    assert "lot précédent de #500" in resume
    assert "601\tECHEC" in resume, "les autres groupes du plan s'enchaînent malgré tout"
    assert r.returncode == 1


def test_un_ticket_pris_entre_temps_est_saute_pas_vole(depot: Depot) -> None:
    """Le plan est figé, le backlog non : quelqu'un a pu prendre le ticket depuis."""
    depot.ticket(130, "Déjà pris depuis", statut="En cours", assigne="alice")
    claude = _claude_stub(depot, 'echo "la session ne doit jamais démarrer" >&2\nexit 1\n')
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "collision",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, "un ticket sauté n'est pas un échec"
    chemin = depot.racine / ".maestro/orchestrate/collision/resume.tsv"
    resume = chemin.read_text(encoding="utf-8")
    assert "130\tSAUTE" in resume and "En cours" in resume


def test_max_borne_les_tickets_tentes_meme_en_cas_de_panne(depot: Depot) -> None:
    """Sans cela, une panne systématique épuiserait tout le plan malgré --max."""
    for iid in (130, 131, 132):
        depot.ticket(iid, f"Ticket {iid}")
    echec = depot.racine.parent / "bin" / "worktree-ko"
    echec.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8", newline="\n")
    echec.chmod(0o755)
    plan = _plan(depot, [(1, 130, "-", "haute"), (2, 131, "-", "haute"), (3, 132, "-", "haute")])
    r = depot.lance(
        "run.sh", "--plan", plan, "--run-id", "borne", "--max", "1",
        env={"MAESTRO_CLAUDE_BIN": "true", "MAESTRO_ORCHESTRATE_WORKTREE": str(echec)},
    )
    lignes = (depot.racine / ".maestro/orchestrate/borne/resume.tsv").read_text(encoding="utf-8")
    assert len([x for x in lignes.splitlines() if not x.startswith("#")]) == 1
    assert "Plafond --max 1" in r.stdout


def test_un_ticket_saute_avance_la_position_mais_pas_le_quota_de_max(depot: Depot) -> None:
    """Les deux compteurs disent deux choses (#230) : la position suit le plan, sautés compris ;
    `--max` ne compte que les tickets réellement TENTÉS, un saut ne coûtant rien."""
    depot.ticket(130, "Pris par quelqu un d autre", statut="En cours", assigne="alice")
    depot.ticket(131, "Ticket 131")
    depot.ticket(132, "Ticket 132")
    depot.mr("feat/131-ticket-131", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("131", "En revue")}' > "$MAESTRO_FIXTURES/owner-131.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "haute"), (2, 131, "-", "haute"), (3, 132, "-", "haute")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "positions", "--max", "1",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[2/3] #131" in r.stdout, "#131 est le 2e du plan, même s'il est le 1er à être tenté"
    assert "Plafond --max 1" in r.stdout
    resume = (depot.racine / ".maestro/orchestrate/positions/resume.tsv").read_text(
        encoding="utf-8")
    iids = [x.split("\t")[0] for x in resume.splitlines() if not x.startswith("#")]
    assert iids == ["130", "131"], "le saut n'a rien consommé, le plafond a arrêté avant #132"


def test_le_fichier_stop_empeche_un_run_de_demarrer(depot: Depot) -> None:
    (depot.racine / ".maestro/orchestrate").mkdir(parents=True, exist_ok=True)
    (depot.racine / ".maestro/orchestrate/STOP").touch()
    # Bouchon qui échoue bruyamment : sans lui, le test emprunterait le `claude` de la machine —
    # vert sur un poste de dev, rouge en CI où le CLI n'existe pas. Il vaut mieux qu'il ait aussi
    # à dire que la session n'a pas démarré du tout.
    claude = _claude_stub(depot, 'echo "la session ne doit jamais démarrer" >&2\nexit 1\n')
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "stoppe",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0
    assert "Arrêt demandé" in r.stdout


def test_une_duree_de_timeout_invalide_est_refusee(depot: Depot) -> None:
    """Un timeout mal interprété tuerait des sessions valides : mieux vaut refuser tout de suite."""
    r = depot.lance("run.sh", "--timeout", "3j", "--dry-run")
    assert r.returncode == 2
    assert "durée invalide" in r.stderr


# =====================================================================================
# L'effort de raisonnement, épinglé par le dépôt (#217)
# =====================================================================================
#
# Ce que ces tests protègent n'est pas une valeur mais une PROVENANCE. Avant #217, `run.sh` ne
# passait aucun `--effort` et le niveau venait de `~/.claude/settings.json` du poste : un dépôt qui
# ne dit rien laisse la machine décider, et rien dans la sortie d'un run ne le montre. Le bouchon
# note donc les arguments reçus, et c'est sur eux qu'on juge — pas sur la prose du run.


def _claude_note_les_arguments(depot: Depot, journal: Path) -> str:
    """Bouchon qui consigne ses arguments, puis réussit comme /ticket-ship l'aurait fait."""
    return _claude_stub(depot, f"""
        printf '%s\\n' "$@" > "{journal}"
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
        exit 0
    """)


def test_l_effort_est_xhigh_sans_qu_on_le_demande(depot: Depot) -> None:
    """Le défaut du dépôt, celui qui vaut quand personne ne passe l'option."""
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    journal = depot.racine.parent / "args-defaut"
    claude = _claude_note_les_arguments(depot, journal)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "eff", env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    recus = journal.read_text(encoding="utf-8").splitlines()
    assert "--effort" in recus, "sans l'option, l'effort viendrait des settings du poste"
    assert recus[recus.index("--effort") + 1] == "xhigh"


@pytest.mark.parametrize(
    "args, env, attendu",
    [
        (["--effort", "max"], {}, "max"),
        ([], {"MAESTRO_ORCHESTRATE_EFFORT": "high"}, "high"),
        # L'option gagne sur la variable : c'est le geste le plus explicite des deux.
        (["--effort", "low"], {"MAESTRO_ORCHESTRATE_EFFORT": "medium"}, "low"),
    ],
)
def test_l_effort_se_surcharge_en_connaissance_de_cause(
    depot: Depot, args: list[str], env: dict[str, str], attendu: str
) -> None:
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    journal = depot.racine.parent / f"args-{attendu}"
    claude = _claude_note_les_arguments(depot, journal)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance(
        "run.sh", "--plan", plan, "--run-id", f"eff-{attendu}", *args,
        env={"MAESTRO_CLAUDE_BIN": claude, **env},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    recus = journal.read_text(encoding="utf-8").splitlines()
    assert recus[recus.index("--effort") + 1] == attendu


def test_un_effort_inconnu_est_refuse_avant_le_premier_ticket(depot: Depot) -> None:
    """Le CLI refuserait la valeur à chaque session : le run brûlerait son plan en échecs
    jumeaux."""
    r = depot.lance("run.sh", "--effort", "extra-high", "--dry-run")
    assert r.returncode == 2
    assert "effort inconnu" in r.stderr
    assert "xhigh" in r.stderr, "le message nomme les niveaux acceptés"


def test_la_session_reprise_porte_aussi_l_effort(depot: Depot) -> None:
    """Deux invocations de `claude` dans la boucle — la reprise est la plus oubliable."""
    depot.ticket(130, "Ticket interrompu")
    journal = depot.racine.parent / "args-reprise"
    claude = _claude_stub(depot, f"""
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          printf '%s\\n' "$@" > "{journal}"
          printf '{{"is_error":false,"subtype":"success","total_cost_usd":2}}'; exit 0
        fi
        printf '{{"is_error":true,"total_cost_usd":1,"result":"Claude AI usage limit reached"}}'
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    depot.lance("run.sh", "--plan", plan, "--run-id", "eff-reprise", "--effort", "max",
                env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "1"})
    recus = journal.read_text(encoding="utf-8").splitlines()
    assert recus[recus.index("--effort") + 1] == "max", "la session reprise garde le régime du run"


def test_l_effort_est_annonce_dans_le_plan(depot: Depot) -> None:
    """Journalisé à côté du modèle : relire un run doit dire sous quel régime il a tourné."""
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "eff-plan")
    assert "effort xhigh" in r.stdout
    assert "--effort xhigh" in r.stdout, "l'aperçu de la commande de session reste fidèle"


def test_l_effort_traverse_le_lancement_detache(depot: Depot) -> None:
    """Le run détaché est un autre processus : ce que l'appelant a choisi doit le suivre."""
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    spawn = _spawn_stub(depot)
    claude = _claude_stub(depot, 'echo "aucune session côté pilote" >&2\nexit 1\n')
    r = depot.lance(
        "run.sh", "--detach", "--plan", plan, "--run-id", "eff-detache", "--effort", "max",
        env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_SPAWN": spawn},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    lanceur = depot.racine / ".maestro/orchestrate/eff-detache/lancer.sh"
    corps = lanceur.read_text(encoding="utf-8")
    commande = next(ligne for ligne in corps.splitlines() if ligne.startswith("bash "))
    assert "--effort max" in commande


# =====================================================================================
# Le plafond de dépense, posé seulement s'il est demandé (#286)
# =====================================================================================
#
# Miroir exact de la section précédente, à l'inverse près : ce qu'on protège ici n'est pas la
# présence d'un réglage mais son ABSENCE. `run.sh` passait `--max-budget-usd 15` à chaque session ;
# une session qui touche le plafond meurt en plein travail, sans commit ni MR, et la boucle la
# compte en échec — ce qui saborde les lots suivants du même parent. Les deux runs du 2026-08-06 y
# ont laissé 2 tickets coupés au même montant (15.07 $) et 13 sautés en cascade. Le bouchon note ses
# arguments, et c'est sur eux qu'on juge.


def test_aucun_plafond_de_budget_sans_qu_on_le_demande(depot: Depot) -> None:
    """Le défaut du dépôt : une session va au bout de son ticket, pas d'un montant."""
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    journal = depot.racine.parent / "args-budget-defaut"
    claude = _claude_note_les_arguments(depot, journal)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "bud", env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    recus = journal.read_text(encoding="utf-8").splitlines()
    assert "--max-budget-usd" not in recus, "un plafond non demandé coupe la session en plein vol"


@pytest.mark.parametrize(
    "args, env, attendu",
    [
        (["--budget", "20"], {}, "20"),
        ([], {"MAESTRO_ORCHESTRATE_BUDGET": "8"}, "8"),
        # L'option gagne sur la variable : c'est le geste le plus explicite des deux.
        (["--budget", "20"], {"MAESTRO_ORCHESTRATE_BUDGET": "8"}, "20"),
    ],
)
def test_le_plafond_se_pose_explicitement(
    depot: Depot, args: list[str], env: dict[str, str], attendu: str
) -> None:
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    journal = depot.racine.parent / f"args-budget-{attendu}-{len(env)}"
    claude = _claude_note_les_arguments(depot, journal)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance(
        "run.sh", "--plan", plan, "--run-id", f"bud-{attendu}-{len(env)}", *args,
        env={"MAESTRO_CLAUDE_BIN": claude, **env},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    recus = journal.read_text(encoding="utf-8").splitlines()
    assert recus[recus.index("--max-budget-usd") + 1] == attendu


@pytest.mark.parametrize("zero", ["0", "0.00"])
def test_un_plafond_a_zero_vaut_pas_de_plafond(depot: Depot, zero: str) -> None:
    """Seule façon d'annuler une variable déjà posée dans l'environnement — et surtout, un
    « --max-budget-usd 0 » transmis tel quel tuerait chaque session avant son premier outil."""
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    journal = depot.racine.parent / f"args-budget-zero-{zero}"
    claude = _claude_note_les_arguments(depot, journal)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance(
        "run.sh", "--plan", plan, "--run-id", f"bud-zero-{zero}", "--budget", zero,
        env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_BUDGET": "15"},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    recus = journal.read_text(encoding="utf-8").splitlines()
    assert "--max-budget-usd" not in recus


def test_un_budget_illisible_est_refuse_avant_le_premier_ticket(depot: Depot) -> None:
    """Même raison que pour l'effort : le CLI le refuserait à CHAQUE session."""
    r = depot.lance("run.sh", "--budget", "vingt", "--dry-run")
    assert r.returncode == 2
    assert "budget invalide" in r.stderr


def test_la_session_reprise_porte_le_meme_regime_de_budget(depot: Depot) -> None:
    """Deux invocations de `claude` dans la boucle — la reprise est la plus oubliable."""
    depot.ticket(130, "Ticket interrompu")
    journal = depot.racine.parent / "args-budget-reprise"
    claude = _claude_stub(depot, f"""
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          printf '%s\\n' "$@" > "{journal}"
          printf '{{"is_error":false,"subtype":"success","total_cost_usd":2}}'; exit 0
        fi
        printf '{{"is_error":true,"total_cost_usd":1,"result":"Claude AI usage limit reached"}}'
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    depot.lance("run.sh", "--plan", plan, "--run-id", "bud-reprise",
                env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "1"})
    recus = journal.read_text(encoding="utf-8").splitlines()
    assert "--max-budget-usd" not in recus, "la session reprise garde le régime du run"


def test_le_regime_de_budget_est_annonce_dans_les_deux_sens(depot: Depot) -> None:
    """« Illimité » est un choix, pas un oubli : relire un run doit dire lequel s'appliquait —
    un ticket coupé au plafond ne se distingue d'un échec de session que par cette ligne."""
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    sans = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "bud-plan")
    assert "budget illimité" in sans.stdout
    assert "--max-budget-usd" not in sans.stdout, "l'aperçu de la commande de session reste fidèle"
    avec = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "bud-plan-20",
                       "--budget", "20")
    assert "budget 20 $/ticket" in avec.stdout
    assert "--max-budget-usd 20" in avec.stdout


# =====================================================================================
# La reprise après limite d'usage (#171)
# =====================================================================================

def _fixture_limite(depot: Depot, nom: str, contenu: str) -> str:
    chemin = depot.racine / f"{nom}.json"
    chemin.write_text(contenu, encoding="utf-8", newline="\n")
    return str(chemin)


def test_un_reset_en_epoch_donne_l_attente_jusqu_au_reset(depot: Depot) -> None:
    futur = int(time.time()) + 3600
    f = _fixture_limite(
        depot, "epoch", f'{{"is_error":true,"result":"Claude AI usage limit reached|{futur}"}}'
    )
    r = depot.lance("run.sh", "--test-reprise", f)
    assert r.returncode == 0
    assert "LIMITE D'USAGE détectée" in r.stdout
    secondes = int(r.stdout.split("(")[1].split(" s)")[0])
    assert 3600 < secondes <= 3600 + 130, "reset + la marge, à la seconde d'exécution près"


def test_un_reset_en_millisecondes_n_attend_pas_mille_fois_trop(depot: Depot) -> None:
    futur_ms = (int(time.time()) + 3600) * 1000
    f = _fixture_limite(
        depot,
        "ms",
        f'{{"is_error":true,"rate_limits":{{"five_hour":{{"resetsAt":"{futur_ms}"}}}},'
        f'"result":"usage limit reached"}}',
    )
    r = depot.lance("run.sh", "--test-reprise", f)
    secondes = int(r.stdout.split("(")[1].split(" s)")[0])
    assert 3500 < secondes <= 3600 + 130


def test_sans_heure_de_reset_on_retombe_sur_le_palier(depot: Depot) -> None:
    f = _fixture_limite(depot, "sans-reset",
                        '{"is_error":true,"api_error_status":429,"result":"rate limited"}')
    r = depot.lance("run.sh", "--test-reprise", f)
    assert r.returncode == 0
    assert "palier" in r.stdout


def test_un_reset_deja_passe_ne_relance_pas_aussitot(depot: Depot) -> None:
    """Horloge décalée ou en-tête périmé : sans garde-fou, la boucle retaperait la même limite."""
    passe = int(time.time()) - 7200
    f = _fixture_limite(
        depot, "passe", f'{{"is_error":true,"result":"usage limit reached|{passe}"}}'
    )
    r = depot.lance("run.sh", "--test-reprise", f)
    secondes = int(r.stdout.split("(")[1].split(" s)")[0])
    assert secondes == 900, "on retombe sur le palier plutôt que d'attendre zéro"


@pytest.mark.parametrize("contenu", [
    '{"type":"result","subtype":"success","is_error":false,"result":"tout va bien"}',
    '{"is_error":true,"result":"ENOENT: no such file or directory"}',
])
def test_un_echec_ordinaire_ne_declenche_aucune_reprise(depot: Depot, contenu: str) -> None:
    f = _fixture_limite(depot, "ordinaire", contenu)
    r = depot.lance("run.sh", "--test-reprise", f)
    assert r.returncode == 1
    assert "PAS UNE LIMITE" in r.stdout


# =====================================================================================
# La télémétrie du flux stream-json n'est pas un refus (#203)
# =====================================================================================
# Le CLI ouvre CHAQUE session par un événement qui rapporte la fenêtre de 5 h en cours — y compris
# une session qui ira au bout. Depuis que le flux brut est grepé (#176), il faisait dormir un run
# jusqu'au reset après un ticket pourtant LIVRÉ. Noter `overageStatus` : « rejected » dès que
# l'organisation interdit le dépassement, sur une ligne qui n'est pas un refus pour autant.
def _evenement_fenetre(statut: str, reset: int) -> str:
    """L'événement d'ouverture du flux, tel que le CLI l'écrit."""
    return (
        '{"type":"rate_limit_event","rate_limit_info":{"status":"' + statut + '",'
        '"resetsAt":' + str(reset) + ',"rateLimitType":"five_hour",'
        '"overageStatus":"rejected","isUsingOverage":false},"session_id":"06cacb83"}'
    )


def test_la_telemetrie_de_fenetre_n_est_pas_une_limite(depot: Depot) -> None:
    futur = int(time.time()) + 3600
    f = _fixture_limite(
        depot, "telemetrie",
        _evenement_fenetre("allowed", futur) + "\n"
        + '{"type":"result","subtype":"success","is_error":false,"result":"livré"}\n',
    )
    r = depot.lance("run.sh", "--test-reprise", f)
    assert r.returncode == 1, r.stdout
    assert "PAS UNE LIMITE" in r.stdout


def test_un_refus_dans_le_meme_evenement_reste_une_limite(depot: Depot) -> None:
    """Le filtre écarte l'information, pas le refus — sinon il masquerait ce qu'il doit détecter."""
    futur = int(time.time()) + 3600
    f = _fixture_limite(
        depot, "refus",
        _evenement_fenetre("rejected", futur) + "\n"
        + '{"type":"result","is_error":true,"result":"usage limit reached"}\n',
    )
    r = depot.lance("run.sh", "--test-reprise", f)
    assert r.returncode == 0, r.stdout
    assert "LIMITE D'USAGE détectée" in r.stdout
    secondes = int(r.stdout.split("(")[1].split(" s)")[0])
    assert 3500 < secondes <= 3600 + 130, "l'heure de reset du refus reste celle qu'on attend"


def test_une_session_reussie_ne_part_jamais_en_reprise(depot: Depot) -> None:
    """La ceinture des bretelles : sortie en 0 ⇒ verdict GitLab, sans passer par la détection.

    La session dit ici « usage limit reached » dans son message final — le marqueur SURVIT au
    filtre, et c'est voulu : une session qui travaille justement sur les limites d'usage en écrit
    les mots (celle-ci en est un cas réel). Seule la sortie en 0 doit alors la sauver.

    Le plafond est mis à 1 s pour qu'une régression échoue *vite* : toute limite détectée
    dépasserait alors le cumul autorisé et arrêterait le run au lieu de dormir jusqu'au reset.
    """
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    compteur = depot.racine.parent / "appels-succes"
    futur = int(time.time()) + 3600
    claude = _claude_stub(depot, f"""
        n=$(( $(cat "{compteur}" 2>/dev/null || echo 0) + 1 )); echo "$n" > "{compteur}"
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '%s\\n' '{_evenement_fenetre("allowed", futur)}'
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":2,'
        printf '"result":"corrigé le message usage limit reached"}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "succes",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PLAFOND": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert compteur.read_text().strip() == "1", "une seule session : aucune reprise"
    assert "limite d'usage" not in r.stdout.lower()
    resume = (depot.racine / ".maestro/orchestrate/succes/resume.tsv").read_text(encoding="utf-8")
    assert "130\tOK" in resume, "le verdict GitLab est lu — le ticket livré n'est pas dit en échec"


def test_apres_la_limite_la_session_reprend_au_lieu_de_recommencer(depot: Depot) -> None:
    depot.ticket(130, "Ticket interrompu")
    compteur = depot.racine.parent / "appels"
    claude = _claude_stub(depot, f"""
        n=$(( $(cat "{compteur}" 2>/dev/null || echo 0) + 1 )); echo "$n" > "{compteur}"
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          printf '{{"is_error":false,"subtype":"success","total_cost_usd":2}}'; exit 0
        fi
        printf '{{"is_error":true,"total_cost_usd":1,"result":"Claude AI usage limit reached"}}'
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "reprise",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "1"})
    assert compteur.read_text().strip() == "2", "une session neuve, puis UNE reprise"
    assert "reprise 1/3" in r.stdout
    assert "limite d'usage atteinte" in r.stdout


def test_une_reprise_impossible_repart_a_froid(depot: Depot) -> None:
    """Session perdue : on redémarre, le travail déjà commité étant sur la branche."""
    depot.ticket(130, "Ticket interrompu")
    compteur = depot.racine.parent / "appels-froid"
    claude = _claude_stub(depot, f"""
        n=$(( $(cat "{compteur}" 2>/dev/null || echo 0) + 1 )); echo "$n" > "{compteur}"
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          printf '{{"is_error":true,"result":"No conversation found with session ID"}}'; exit 1
        fi
        if [ "$n" = 1 ]; then
          printf '{{"is_error":true,"result":"usage limit reached"}}'; exit 1
        fi
        printf '{{"is_error":false,"subtype":"success"}}'; exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "froid",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "1"})
    assert compteur.read_text().strip() == "3", "neuve, reprise refusée, puis redémarrage à froid"
    assert "redémarrage à froid" in r.stdout


def test_les_reprises_sont_plafonnees(depot: Depot) -> None:
    depot.ticket(130, "Ticket bloqué")
    claude = _claude_stub(depot, """
        printf '{"is_error":true,"result":"usage limit reached"}'
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "plafond", "--max-reprises", "2",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "1"})
    assert "après 2 reprise(s)" in r.stdout
    assert r.returncode == 1


def test_une_attente_trop_longue_est_lue_comme_la_limite_hebdomadaire(depot: Depot) -> None:
    """Au-delà de 5 h 30, on ne dort pas des jours : le run s'arrête et se relance plus tard."""
    depot.ticket(130, "Ticket bloqué")
    claude = _claude_stub(depot, """
        LOIN=$(( $(date +%s) + 90000 ))
        printf '{"is_error":true,"result":"Claude AI usage limit reached|%s"}' "$LOIN"
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne"), (2, 131, "-", "moyenne")])
    debut = time.monotonic()
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "hebdo",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert time.monotonic() - debut < 60, "le run ne doit jamais attendre une limite hebdomadaire"
    assert "Limite hebdomadaire" in r.stdout
    resume = (depot.racine / ".maestro/orchestrate/hebdo/resume.tsv").read_text(encoding="utf-8")
    assert "131" not in resume, "le reste du plan est laissé intact pour un prochain run"


# =====================================================================================
# Le lancement détaché (#173)
# =====================================================================================

def test_detach_ecrit_un_lanceur_et_rend_la_main_sans_calculer_le_plan(depot: Depot) -> None:
    """`--detach` prépare et délègue : le plan est figé par le run détaché, pas ici."""
    claude = _claude_stub(depot, 'echo "aucune session ne démarre côté pilote" >&2\nexit 1\n')
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    spawn = _spawn_stub(depot)
    r = depot.lance(
        "run.sh", "--detach", "--plan", plan, "--run-id", "detache", "--max", "1",
        env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_SPAWN": spawn},
    )
    assert r.returncode == 0, r.stdout + r.stderr

    lanceur = depot.racine / ".maestro/orchestrate/detache/lancer.sh"
    assert lanceur.exists(), "la console n'exécute qu'un lanceur écrit sur disque"
    corps = lanceur.read_text(encoding="utf-8")
    commande = next(ligne for ligne in corps.splitlines() if ligne.startswith("bash "))
    assert "run.sh" in commande and "--max 1" in commande, "les options d'origine sont repassées"
    assert "--detach" not in commande, "sans quoi la console relancerait une console, à l'infini"
    assert commande.count("--run-id") == 1, "le run-id est imposé une fois, pas repris en double"
    assert "MAESTRO_ORCHESTRATE_COULEUR=1" in corps, "la fenêtre est un écran : couleurs gardées"

    dossier = depot.racine / ".maestro/orchestrate/detache"
    assert not (dossier / "plan.tsv").exists(), "deux calculs du plan risqueraient de diverger"
    # Comparaison sur la fin du chemin : bash le rend en style MSYS, Python en style Windows.
    recu = (depot.fixtures / "spawn.txt").read_text(encoding="utf-8").strip()
    assert recu.endswith("/detache/lancer.sh"), f"la console reçoit le lanceur, pas {recu}"
    assert "reprendre" in r.stdout, "le filet en cas de console tuée est annoncé au lancement"


def test_detach_avec_dry_run_reste_en_lecture_seule(depot: Depot) -> None:
    """Rien à détacher pour un plan qui s'affiche en une seconde — et aucune trace laissée."""
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    spawn = _spawn_stub(depot)
    r = depot.lance(
        "run.sh", "--detach", "--dry-run", "--plan", plan, "--run-id", "sec",
        env={"MAESTRO_ORCHESTRATE_SPAWN": spawn},
    )
    assert r.returncode == 0, r.stderr
    assert "#130" in r.stdout, "le plan s'affiche en direct"
    assert not (depot.fixtures / "spawn.txt").exists(), "aucune console n'est ouverte"
    assert not (depot.racine / ".maestro/orchestrate/sec").exists()


def test_un_lancement_detache_en_echec_ne_laisse_pas_de_run_fantome(depot: Depot) -> None:
    """Un journal annoncé mais jamais écrit vaudrait pire que pas de journal du tout."""
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    spawn = _spawn_stub(depot, "exit 1\n")
    r = depot.lance(
        "run.sh", "--detach", "--plan", plan, "--run-id", "rate",
        env={"MAESTRO_CLAUDE_BIN": "true", "MAESTRO_ORCHESTRATE_SPAWN": spawn},
    )
    assert r.returncode == 1
    assert "n'a pas démarré" in r.stderr
    assert not (depot.racine / ".maestro/orchestrate/rate").exists()


def test_le_lanceur_detache_lance_vraiment_le_run(depot: Depot) -> None:
    """Le lanceur est le seul lien entre le pilote et le run : on l'exécute pour de bon."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1.25}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    spawn = _spawn_stub(depot)
    depot.lance(
        "run.sh", "--detach", "--plan", plan, "--run-id", "vrai",
        env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_SPAWN": spawn},
    )

    lanceur = depot.racine / ".maestro/orchestrate/vrai/lancer.sh"
    r = subprocess.run(
        [BASH, str(lanceur)],
        cwd=depot.racine,
        env={**depot.env, "MAESTRO_CLAUDE_BIN": claude},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    resume = (depot.racine / ".maestro/orchestrate/vrai/resume.tsv").read_text(encoding="utf-8")
    assert "130\tOK" in resume
    journal = (depot.racine / ".maestro/orchestrate/vrai/run.log").read_text(encoding="utf-8")
    assert "#130" in journal, "la sortie survit à la fermeture de la fenêtre"

    # La fenêtre est un écran (couleurs), le journal se relit plus tard et souvent par un outil
    # (pas de codes ANSI) — `tee` les enverrait pourtant aux deux.
    assert "\x1b[" in r.stdout, "la console garde ses couleurs malgré le tee"
    assert "\x1b[" not in journal, "le journal est décoloré en fin de run"


def test_sans_le_marqueur_la_sortie_reste_sans_couleur(depot: Depot) -> None:
    """Le contre-test : hors console détachée, une sortie redirigée ne doit pas être colorée."""
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "terne")
    assert r.returncode == 0, r.stderr
    assert "#130" in r.stdout
    assert "\x1b[" not in r.stdout


def test_le_conftest_neutralise_la_couleur_heritee_du_poste() -> None:
    """Le contre-test ci-dessus ne tient que si le poste ne pose pas la variable (#236).

    `MAESTRO_ORCHESTRATE_COULEUR=1` dans le bloc `env` d'un `.claude/settings.local.json` fuit dans
    l'environnement de toute session de ce poste, donc dans les sous-processus lancés ici : la
    sortie ressort truffée de codes ANSI et le test précédent échoue **en local seulement**, la CI
    restant verte. Quatre sessions ont rouvert la même enquête sur cette fausse alerte — c'est le
    dépôt, pas chaque run, qui doit la tarir.

    Vide plutôt que supprimée, comme les clés Langfuse : `run.sh` lit
    `${MAESTRO_ORCHESTRATE_COULEUR:-0}`, pour qui vide et absente valent 0, et une valeur vide
    traverse sans surprise les `env={**os.environ, …}` de ces tests.
    """
    assert os.environ.get(CLE_COULEUR_ORCHESTRATE) == "", (
        "le conftest doit vider la variable à l'import, avant le premier module de test "
        "(tests/conftest.py, #236)"
    )


# =====================================================================================
# Le flux d'activité en direct (#176)
# =====================================================================================

def _flux(dossier: Path, iid: int = 130) -> str:
    """Le flux archivé d'un ticket, qu'il soit encore brut ou déjà compacté (#198).

    Ces tests-ci portent sur ce que le flux CONTIENT ; son format de stockage est le sujet de la
    section « journal.sh », qui vérifie explicitement la compaction.
    """
    brut = dossier / f"{iid}.jsonl"
    if brut.exists():
        return brut.read_text(encoding="utf-8")
    return gzip.decompress((dossier / f"{iid}.jsonl.gz").read_bytes()).decode("utf-8")


def _stub_flux(depot: Depot) -> str:
    """Un bouchon qui émet un vrai flux stream-json : plusieurs événements, `result` en dernier.

    Le premier événement porte un `total_cost_usd` LEURRE : c'est la régression que ce lot peut
    introduire (`champ_json` prend la première occurrence d'une clé), et elle serait silencieuse.
    """
    # Concaténation implicite : chaque ligne de source reste courte, le JSON produit tient sur une
    # seule ligne — c'est le format du flux, un objet par ligne.
    flux = "\n".join([
        '{"type":"system","subtype":"init","total_cost_usd":0.01}',
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read",'
        '"input":{"file_path":"docs/21-configuration-mcp.md"}}]}}',
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit",'
        '"input":{"file_path":"core/models/mcp.py"}},{"type":"tool_use","name":"Bash",'
        '"input":{"command":"pytest -q"}}]}}',
        '{"type":"result","subtype":"success","is_error":false,"total_cost_usd":4.2}',
    ])
    return _claude_stub(depot, f"""
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        cat <<'FLUX'
{flux}
FLUX
        exit 0
    """)


def test_le_flux_donne_une_ligne_par_action_et_garde_le_resultat_final(depot: Depot) -> None:
    """Le flot d'une ligne par appel d'outil : depuis #240 il ne survit qu'en `--verbeux`.

    C'est le mode de diagnostic qui le porte désormais — le comportement par défaut est vérifié
    par la section suivante, qui exige justement son absence.
    """
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _stub_flux(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "flux", "--verbeux",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr

    # 1. La console dit ce que la session fabrique, au lieu de rester muette.
    assert "· Read docs/21-configuration-mcp.md" in r.stdout
    assert "· Edit core/models/mcp.py" in r.stdout
    assert "· Bash pytest -q" in r.stdout, "les tool_use multiples d'un événement sont tous vus"

    dossier = depot.racine / ".maestro/orchestrate/flux"
    # 2. Le flux brut est archivé en entier…
    lignes = [x for x in _flux(dossier).splitlines() if x]
    assert len(lignes) == 4

    # 3. …mais <iid>.json ne porte QUE le résultat final : sinon le coût lu serait le leurre.
    final = (dossier / "130.json").read_text(encoding="utf-8")
    assert '"type":"result"' in final
    assert "0.01" not in final
    resume = (dossier / "resume.tsv").read_text(encoding="utf-8")
    assert "4.2" in resume and "0.01" not in resume


def test_une_limite_d_usage_annoncee_dans_le_flux_est_detectee(depot: Depot) -> None:
    """Le signal peut n'apparaître qu'au fil du flux, sans jamais atteindre l'objet `result`."""
    depot.ticket(130, "Ticket bloqué")
    claude = _claude_stub(depot, r"""
        LOIN=$(( $(date +%s) + 120 ))
        printf '{"type":"system","subtype":"init"}\n'
        printf '{"type":"assistant","message":{"content":[{"type":"text",'
        printf '"text":"Claude AI usage limit reached|%s"}]}}\n' "$LOIN"
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "limite-flux", "--max-reprises", "0",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    # Avec --max-reprises 0 la boucle renonce tout de suite : ce qui est vérifié ici, c'est qu'elle
    # a bien RECONNU une limite d'usage (et non un échec ordinaire, qui ne la mentionnerait pas).
    assert "limite d'usage" in r.stdout, "le flux est lu, pas seulement le résultat final"


def test_un_flux_sans_saut_de_ligne_final_ne_perd_pas_son_resultat(depot: Depot) -> None:
    """La dernière ligne d'un flux EST l'objet `result` : la perdre, c'est perdre le verdict."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"system","subtype":"init"}}\\n'
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":7.75}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "tronque",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    dossier = depot.racine / ".maestro/orchestrate/tronque"
    assert '"type":"result"' in (dossier / "130.json").read_text(encoding="utf-8")
    assert "7.75" in (dossier / "resume.tsv").read_text(encoding="utf-8")


def test_sans_objet_result_le_dernier_evenement_en_tient_lieu(depot: Depot) -> None:
    """Repli pour un CLI plus ancien (ou un flux coupé) : mieux vaut la dernière ligne que rien."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"system","subtype":"init","total_cost_usd":0.01}}\\n'
        printf '{{"is_error":false,"subtype":"success","total_cost_usd":2.5}}\\n'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "repli",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    resume = (depot.racine / ".maestro/orchestrate/repli/resume.tsv").read_text(encoding="utf-8")
    assert "2.5" in resume and "0.01" not in resume, "le repli prend la dernière ligne, pas la 1re"


def test_la_session_reprise_passe_aussi_par_le_flux(depot: Depot) -> None:
    """Les DEUX invocations de `lance_session` sont concernées : sans quoi la console redeviendrait
    muette juste après une reprise — exactement le moment où l'on regarde."""
    depot.ticket(130, "Ticket interrompu")
    depot.mr("feat/130-ticket-interrompu", "opened")
    claude = _claude_stub(depot, f"""
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
          printf '{{"type":"assistant","message":{{"content":[{{"type":"tool_use",'
          printf '"name":"Bash","input":{{"command":"pytest -q"}}}}]}}}}\\n'
          printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":6}}\\n'
          exit 0
        fi
        printf '{{"type":"result","is_error":true,"total_cost_usd":1,'
        printf '"result":"Claude AI usage limit reached"}}\\n'
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "flux-reprise", "--verbeux",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "· Bash pytest -q" in r.stdout, "la reprise doit rester bavarde, elle aussi"
    dossier = depot.racine / ".maestro/orchestrate/flux-reprise"
    assert "6" in (dossier / "resume.tsv").read_text(encoding="utf-8")
    # Chaque tentative repart sur un flux propre, et c'est porteur : la détection de limite grepe
    # le `.jsonl` entier, donc un marqueur laissé par la tentative précédente ferait attendre puis
    # reprendre une session qui vient pourtant d'aboutir — indéfiniment.
    jsonl = _flux(dossier)
    assert "usage limit reached" not in jsonl, "le flux de la tentative précédente doit être effacé"


# =====================================================================================
# La console d'un run : une checklist vivante (#240)
# =====================================================================================
#
# Ces tests n'ont pas de pseudo-terminal, et n'en ont pas besoin : `run.sh` choisit le descripteur
# de ses frames, et `MAESTRO_ORCHESTRATE_CONSOLE` le fait pointer sur un FICHIER. Ce qu'une console
# aurait reçu se relit donc à l'octet près — et son absence dans `stdout` est vérifiable, ce qui est
# l'invariant central : `stdout` finit dans `run.log`, où une frame n'a rien à faire.

def _console(depot: Depot) -> Path:
    """Le fichier qui tient lieu de console pour les frames."""
    return depot.racine.parent / "console.txt"


def _stub_livre(depot: Depot) -> str:
    """Un bouchon qui livre le ticket qu'on lui confie, quel qu'il soit.

    L'iid se lit dans le prompt : un plan à plusieurs tickets réutilise le même bouchon, et poser
    « En revue » sur tous d'entrée ferait sauter les suivants avant qu'ils soient pris.
    """
    gabarit = _statut_json("%s", "En revue")
    return _claude_stub(depot, f"""
        iid=$(printf '%s\\n' "$@" | grep -o 'GitLab #[0-9]*' | head -1 | tr -dc '0-9')
        printf '{gabarit}' "$iid" > "$MAESTRO_FIXTURES/owner-$iid.json"
        printf '{{"type":"assistant","message":{{"content":[{{"type":"tool_use",'
        printf '"name":"Read","input":{{"file_path":"docs/21-configuration-mcp.md"}}}}]}}}}\\n'
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":2}}\\n'
        exit 0
    """)


def test_par_defaut_le_flot_d_outils_ne_s_imprime_plus_mais_rien_n_est_perdu(depot: Depot) -> None:
    """Le critère central de #240 : l'écran cesse de défiler, le journal ne perd rien."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _stub_flux(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "muet",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "· Read docs/21-configuration-mcp.md" not in r.stdout
    assert "· Edit core/models/mcp.py" not in r.stdout

    dossier = depot.racine / ".maestro/orchestrate/muet"
    # Le flux brut est intégral : c'est lui qui porte le diagnostic, et il n'a pas changé.
    assert len([x for x in _flux(dossier).splitlines() if x]) == 4
    # Et `<iid>.json` ne porte toujours que le résultat final — le coût, le verdict et la détection
    # de limite d'usage le lisent (« à ne pas casser » du ticket).
    final = (dossier / "130.json").read_text(encoding="utf-8")
    assert '"type":"result"' in final and "0.01" not in final


def test_la_variable_d_environnement_vaut_l_option_verbeuse(depot: Depot) -> None:
    """`MAESTRO_ORCHESTRATE_VERBEUX=1` : de quoi rallumer le flot sans retoucher la ligne de
    commande d'un run déjà lancé par un lanceur."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _stub_flux(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "verbeux-env",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_VERBEUX": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "· Read docs/21-configuration-mcp.md" in r.stdout


def test_sans_console_la_vue_retombe_en_plein_texte(depot: Depot) -> None:
    """Détachement Unix, CI, tests : personne ne peut redessiner. La checklist s'imprime alors une
    fois par ticket, en clair — et SURTOUT sans une seule séquence de repositionnement, que le
    `sed` final du lanceur ne retire pas (il ne connaît que les codes de couleur)."""
    depot.ticket(130, "Ticket 130")
    depot.mr("feat/130-ticket-130", "opened")
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "texte",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot)})
    assert r.returncode == 0, r.stdout + r.stderr
    # Le marqueur « > » du ticket courant, et non le seul « 1. #130 » : le récapitulatif du plan
    # imprimé au démarrage porte déjà celui-là, et le test passerait sans qu'aucune vue soit rendue.
    assert ">  1. #130" in r.stdout, "la checklist du plan est rendue en plein texte"
    assert "\x1b[" not in r.stdout, "aucune séquence ANSI ne doit atterrir dans run.log"


def test_avec_une_console_les_frames_y_vont_et_jamais_dans_run_log(depot: Depot) -> None:
    """Les deux flux sont séparés : les frames vers la console, la trace permanente vers stdout."""
    depot.ticket(130, "Ticket 130")
    depot.mr("feat/130-ticket-130", "opened")
    console = _console(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "console",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr

    vue = console.read_text(encoding="utf-8", errors="replace")
    assert ". #130" in vue, "la checklist est dessinée sur la console"
    assert "\x1b[" in vue, "et elle y est redessinée — c'est tout l'objet du descripteur dédié"

    assert "\x1b[" not in r.stdout, "aucune frame dans le journal"
    assert ">  1. #130" not in r.stdout, "ni la vue plein texte en double : la console la porte"
    # Ce que `run.log` garde, lui : l'en-tête du ticket et son verdict — de quoi relire un run.
    assert "[1/1] #130" in r.stdout


def test_la_checklist_porte_les_verdicts_deja_rendus_et_le_cumul_du_run(depot: Depot) -> None:
    """Au deuxième ticket, le premier n'est plus « à venir » : il porte sa marque, sa MR et son
    coût, et le pied dit où en est le run — c'est l'information que le flot d'outils avait chassée
    de l'écran."""
    for iid in (130, 131):
        depot.ticket(iid, f"Ticket {iid}")
        depot.mr(f"feat/{iid}-ticket-{iid}", "opened")
    console = _console(depot)
    plan = _plan(depot, [(1, 130, "-", "haute"), (2, 131, "-", "haute")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "checklist",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr

    vue = console.read_text(encoding="utf-8", errors="replace")
    assert "✓  1. #130" in vue, "le ticket livré porte sa marque dans la checklist"
    assert "MR !99" in vue, "avec sa MR"
    assert "2.00 $" in vue, "et son coût, arrondi comme dans resume.tsv"
    assert "reste " in vue and "✓ 1" in vue, "le pied donne le cumul du run"


def test_l_attente_et_la_reprise_sont_des_etats_de_la_vue(depot: Depot) -> None:
    """Une limite d'usage se compte en heures : l'écran ne doit ni paraître figé, ni laisser croire
    que la session rouverte est un ticket qui démarre. Et le chrono suit le TICKET — sans quoi il
    repartirait de zéro à chaque tentative, alors que c'est la durée du ticket qu'on consigne.

    Le palier est de TROIS secondes et non d'une (#292). Depuis #290 la session ne dessine plus,
    elle *publie* son état et le pilote l'échantillonne ; depuis #291 le délai annoncé est ce qui
    reste du rendez-vous, donc `fin - maintenant` — deux horloges lues à quelques forks d'écart,
    ce qui coûte
    sous MSYS de quoi franchir une seconde entière. À un palier d'une seconde, l'attente retombait à
    « 0s » : elle était publiée puis écrasée par la reprise dans le même souffle, et aucune frame ne
    pouvait tomber dessus. Ce n'était pas la vue qui manquait l'état, c'était le décor qui n'en
    créait plus. Trois secondes laissent une quinzaine de tours de pilote — l'attente redevient ce
    qu'elle est en production, un état qui dure.
    """
    depot.ticket(130, "Ticket interrompu")
    depot.mr("feat/130-ticket-interrompu", "opened")
    gabarit = _statut_json("%s", "En revue")
    claude = _claude_stub(depot, f"""
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          iid=$(printf '%s\\n' "$@" | grep -o 'GitLab #[0-9]*' | head -1 | tr -dc '0-9')
          printf '{gabarit}' "${{iid:-130}}" > "$MAESTRO_FIXTURES/owner-130.json"
          printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":6}}\\n'
          exit 0
        fi
        printf '{{"type":"result","is_error":true,"total_cost_usd":1,'
        printf '"result":"Claude AI usage limit reached"}}\\n'
        exit 1
    """)
    console = _console(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "attente",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "3",
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr
    vue = console.read_text(encoding="utf-8", errors="replace")
    assert "en attente de la fin de la limite d'usage" in vue, "l'attente est un état, pas un gel"
    assert "=  1. #130" in vue, "et son marqueur est fixe : une session en pause ne tourne pas"
    assert "reprise 1/3" in vue, "et la reprise en est un autre"


def test_le_mode_verbeux_eteint_la_vue_vivante(depot: Depot) -> None:
    """Les deux se disputeraient l'écran — et c'est justement quand on lit chaque ligne qu'on ne
    veut rien qui bouge."""
    depot.ticket(130, "Ticket 130")
    depot.mr("feat/130-ticket-130", "opened")
    console = _console(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "verbeux-vue", "--verbeux",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "· Read docs/21-configuration-mcp.md" in r.stdout
    assert not console.exists() or console.read_text(encoding="utf-8") == "", (
        "aucune frame ne doit être dessinée en mode verbeux"
    )


# =====================================================================================
# Un bloc qui tient en place, et rien d'autre à l'écran (#284)
# =====================================================================================
#
# #240 avait donné à la console son tableau de bord ; il restait trois façons pour lui de salir
# l'écran, dont deux invisibles à la relecture de `run.log` — c'est justement pour ça qu'elles
# avaient tenu. Ces tests les fixent à l'octet près, sur le fichier qui tient lieu de console.

def test_la_frame_ne_se_termine_pas_par_un_saut_de_ligne(depot: Depot) -> None:
    """Le défaut coûteux : un « \\n » écrit sur la rangée du bas fait défiler le tampon. Le bloc vit
    précisément en bas de l'écran, et il se redessinait plusieurs fois par seconde — l'écran
    paraissait stable pendant que l'historique se remplissait d'une copie du bloc par frame."""
    depot.ticket(130, "Ticket 130")
    depot.mr("feat/130-ticket-130", "opened")
    console = _console(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "sans-defilement",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr

    vue = console.read_text(encoding="utf-8", errors="replace")
    # Le pied est la dernière ligne du bloc : il se termine par « efface jusqu'au bout », et rien
    # d'autre. C'est ce qui laisse le curseur SUR la ligne, d'où le repositionnement en hauteur - 1.
    assert "reste 0\x1b[K" in vue, "le pied ferme la frame en effaçant la fin de ligne"
    assert "reste 0\x1b[K\n" not in vue, (
        "une frame finie par un saut de ligne pousse une ligne dans l'historique à chaque redessin"
    )
    # « ESC[F » nu vaut « remonte d'une ligne » : la hauteur est toujours dite explicitement.
    assert "\x1b[F" not in vue, "un repositionnement sans hauteur remonterait d'une ligne"


def test_le_curseur_est_cache_pendant_la_vue_et_rendu_en_sortant(depot: Depot) -> None:
    """Redessiner, c'est faire sauter le curseur d'un bout à l'autre du bloc — et c'est ce
    mouvement, plus que le texte, qui donnait à la console son air agité. Il est rendu à la sortie :
    une fenêtre gardée ouverte après le run ne doit pas rester sans curseur."""
    depot.ticket(130, "Ticket 130")
    depot.mr("feat/130-ticket-130", "opened")
    console = _console(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "curseur",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr

    vue = console.read_text(encoding="utf-8", errors="replace")
    assert "\x1b[?25l" in vue, "le curseur est caché dès que la vue prend l'écran"
    assert vue.rindex("\x1b[?25h") > vue.rindex("\x1b[?25l"), (
        "et rendu APRÈS — le dernier geste de la vue, sinon la console reste amputée"
    )
    assert "\x1b[?25" not in r.stdout, "rien de tout cela n'a à finir dans run.log"


def test_le_battement_va_dans_le_journal_et_non_a_l_ecran(depot: Depot) -> None:
    """Le battement est fait pour `run.log`, où il est la seule trace d'une session qui dure. À
    l'écran il n'apprenait rien que le bloc ne dise déjà en plus frais, et il coûtait double : une
    ligne poussée sous le bloc chaque minute, plus un redessin « à neuf » qui laissait le bloc
    précédent derrière lui."""
    depot.ticket(130, "Ticket 130")
    depot.mr("feat/130-ticket-130", "opened")
    gabarit = _statut_json("130", "En revue")
    # Une session qui dure : de quoi laisser passer deux battements d'une seconde.
    claude = _claude_stub(depot, f"""
        printf '%s' '{gabarit}' > "$MAESTRO_FIXTURES/owner-130.json"
        sleep 2.5
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}\\n'
        exit 0
    """)
    console = _console(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "battement",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_BATTEMENT": "1",
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr

    vue = console.read_text(encoding="utf-8", errors="replace")
    # Sans descripteur dédié (le lanceur détaché en ouvre un), le battement retombe sur stdout —
    # c'est-à-dire sur le journal, exactement là où il sert.
    assert "  … " in r.stdout, "le journal garde la trace d'une session qui dure"
    assert "  … " not in vue, "l'écran, lui, n'en veut pas : le bloc dit déjà la même chose"


def test_le_lanceur_detache_ouvre_un_descripteur_sur_le_journal(depot: Depot) -> None:
    """Ce descripteur est ce qui permet d'écrire au journal SANS passer par `tee` — donc sans
    passer par l'écran — et d'écrire soi-même sur la console les lignes qui doivent y être : `tee`
    est un autre processus, et une ligne qui arrive après la frame suivante dédouble le bloc."""
    claude = _claude_stub(depot, "exit 1\n")
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance(
        "run.sh", "--detach", "--plan", plan, "--run-id", "fd-journal",
        env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_SPAWN": _spawn_stub(depot)},
    )
    assert r.returncode == 0, r.stdout + r.stderr

    corps = (depot.racine / ".maestro/orchestrate/fd-journal/lancer.sh").read_text(encoding="utf-8")
    assert "exec 4>&1" in corps and "MAESTRO_ORCHESTRATE_CONSOLE_FD=4" in corps
    assert "exec 5>>" in corps and "MAESTRO_ORCHESTRATE_TRACE_FD=5" in corps
    # Filet de dernier recours : `run.sh` rend le curseur par un trap, mais un trap ne s'exécute pas
    # sur un SIGKILL — et c'est ainsi qu'un run est arrêté par un autre (§11.9). La fenêtre survit à
    # son run : elle ne doit pas rester sans curseur.
    assert "\\033[?25h" in corps and ">&4" in corps, (
        "la fenêtre récupère son curseur quoi qu'il arrive"
    )
    # Les deux descripteurs sont ouverts AVANT le tube : le 4 doit désigner la fenêtre et non le
    # tube vers `tee`, et le 5 le fichier de journal lui-même.
    lignes = corps.splitlines()
    commande = next(i for i, ligne in enumerate(lignes) if ligne.startswith("bash "))
    assert next(i for i, ligne in enumerate(lignes) if ligne.startswith("exec 4>")) < commande
    assert next(i for i, ligne in enumerate(lignes) if ligne.startswith("exec 5>")) < commande


# =====================================================================================
# status.sh — savoir où en est un run, hors de sa console (#177)
# =====================================================================================

def _run_dir(
    depot: Depot,
    run_id: str,
    plan: list[tuple[int, int, str, str]],
    *,
    resume: list[tuple] | None = None,
    sessions: tuple[int, ...] = (),
    journal: str | None = None,
    age: int = 0,
) -> Path:
    """Monte à la main un répertoire de run, tel que `run.sh` le laisse derrière lui.

    Écrire ces fichiers plutôt que de lancer un vrai run est ce qui permet de poser les cas que
    `status.sh` doit distinguer — dont ceux qu'un run ne produit qu'en tombant en panne. `age`
    vieillit toutes les dates de modification : c'est le seul levier sur les états qui se
    déduisent du silence (« interrompu », « en cours ? »).
    """
    dossier = depot.racine / ".maestro/orchestrate" / run_id
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "plan.tsv").write_text(
        "# rang\tiid\tparent\tprio\tgroupe\ttitre\n"
        + "".join(f"{r}\t{i}\t{p}\t{prio}\t{_groupe(p, r)}\tTicket {i}\n"
                  for r, i, p, prio in plan),
        encoding="utf-8",
        newline="\n",
    )
    if resume is not None:
        (dossier / "resume.tsv").write_text(
            "# iid\tverdict\tmr\tduree_s\tcout_usd\traison\n"
            + "".join("\t".join(str(c) for c in ligne) + "\n" for ligne in resume),
            encoding="utf-8",
            newline="\n",
        )
    for iid in sessions:
        (dossier / f"{iid}.session").write_text(
            "11111111-2222-4333-a444-555555555555", encoding="utf-8", newline="\n"
        )
        (dossier / f"{iid}.log").write_text("", encoding="utf-8", newline="\n")
    if journal is not None:
        (dossier / "run.log").write_text(journal, encoding="utf-8", newline="\n")
    if age:
        quand = time.time() - age
        for chemin in (*sorted(dossier.rglob("*")), dossier):
            os.utime(chemin, (quand, quand))
    return dossier


def _init_git(depot: Depot, branche: str) -> None:
    """Fait du dépôt jetable un vrai dépôt git, posé sur `branche`, avec un `origin/main` local.

    Aucun distant : `refs/remotes/origin/main` est une simple référence locale — c'est tout ce que
    `status.sh` lit pour compter les commits d'avance, et ça évite un dépôt *bare* de plus.
    """
    assert GIT is not None

    def git(*args: str) -> None:
        subprocess.run(  # noqa: S603
            [GIT, *args], cwd=str(depot.racine), check=True, capture_output=True
        )

    git("init", "--quiet", "--initial-branch=main")
    git("config", "user.email", "test@maestro.invalid")
    git("config", "user.name", "Maestro Test")
    # Le journal du run vit sous .maestro/ : ignoré ici comme dans le vrai dépôt, sans quoi il
    # apparaîtrait dans les « fichiers modifiés » du worktree.
    (depot.racine / ".gitignore").write_text(".maestro/\n", encoding="utf-8", newline="\n")
    git("add", "-A")
    git("-c", "core.hooksPath=", "commit", "--quiet", "-m", "chore: depot jetable")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("checkout", "--quiet", "-b", branche)


def test_aucun_run_est_un_cas_normal_pas_une_erreur(depot: Depot) -> None:
    r = depot.lance("status.sh")
    assert r.returncode == 0, r.stderr
    assert "Aucun run d'orchestration" in r.stdout
    assert "run.sh --dry-run" in r.stdout, "on dit comment en lancer un"


def test_un_run_en_cours_montre_le_ticket_courant_le_reste_et_le_bilan(depot: Depot) -> None:
    depot.ticket(131, "Ticket en cours", statut="En cours")
    _run_dir(
        depot,
        "20260729-090000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne"), (3, 132, "-", "moyenne")],
        resume=[(130, "OK", "99", 600, "3.50", "-")],
        sessions=(131,),
    )
    r = depot.lance("status.sh")
    assert r.returncode == 0, r.stderr
    assert "— en cours" in r.stdout
    assert "En cours — #131" in r.stdout
    assert "Reste au plan (1)" in r.stdout and "#132" in r.stdout
    assert "Traités (1)" in r.stdout and "#130" in r.stdout
    assert "GitLab     ticket « En cours »" in r.stdout, "le statut du ticket courant est relu"
    assert "status.sh --watch" in r.stdout and "touch" in r.stdout, "suivre / arrêter sont donnés"


def test_un_run_termine_rend_son_bilan_et_ne_se_dit_plus_en_cours(depot: Depot) -> None:
    _run_dir(
        depot,
        "20260729-100000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne")],
        resume=[
            (130, "OK", "99", 620, "3.50", "-"),
            (131, "ECHEC", "-", 300, "1.20", "MR « aucune », cycle de vie « En cours »"),
        ],
    )
    r = depot.lance("status.sh")
    assert r.returncode == 0, r.stderr
    assert "— terminé" in r.stdout
    assert "Traités (2)" in r.stdout
    assert "10min20" in r.stdout, "la durée d'un ticket est rendue lisible"
    assert "review-queue" in r.stdout, "le travail d'un run terminé attend une revue humaine"
    assert "En cours — " not in r.stdout, "plus aucun ticket n'est en cours"


def test_un_run_detache_arrete_est_lu_dans_son_journal(depot: Depot) -> None:
    """Le code de sortie écrit par le lanceur tranche : sans lui, un run coupé en plein plan
    passerait pour « interrompu » alors qu'il s'est arrêté de lui-même (limite hebdomadaire…)."""
    _run_dir(
        depot,
        "20260729-110000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne")],
        resume=[(130, "OK", "99", 600, "3.50", "-")],
        journal="[1/2] #130\n\n--- run 20260729-110000 terminé (code 1) ---\n",
    )
    r = depot.lance("status.sh")
    assert r.returncode == 0, r.stderr
    assert "terminé (code 1)" in r.stdout
    assert "Reste au plan (1)" in r.stdout, "ce qui n'a pas été traité reste visible"


def test_un_run_sans_activite_recente_est_dit_interrompu(depot: Depot) -> None:
    """Aucun ticket pris en main et plus rien d'écrit : le run est mort sans le dire."""
    _run_dir(
        depot,
        "20260729-120000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne")],
        resume=[(130, "OK", "99", 600, "3.50", "-")],
        age=7200,
    )
    r = depot.lance("status.sh")
    assert r.returncode == 0, r.stderr
    assert "interrompu" in r.stdout
    # Le filet, c'est le plan resté sur disque — mais on le désigne par son RUN-ID (#204), pas par
    # le chemin de son plan : un argument qui se retient est un argument qu'on retape.
    assert "reprendre" in r.stdout and "--resume 20260729-120000" in r.stdout


def test_un_silence_prolonge_fait_douter_l_en_tete_sans_trancher(depot: Depot) -> None:
    """Sans PID, une session qui réfléchit et une session morte se ressemblent : on le dit."""
    depot.ticket(131, "Ticket peut-être bloqué", statut="En cours")
    _run_dir(
        depot,
        "20260729-130000",
        [(1, 131, "-", "moyenne")],
        resume=[],
        sessions=(131,),
        age=7200,
    )
    r = depot.lance("status.sh")
    assert r.returncode == 0, r.stderr
    assert "en cours ?" in r.stdout, "le doute est dans l'en-tête, pas seulement plus bas"
    assert "rien d'écrit depuis 2h00" in r.stdout
    assert "peut-être bloquée ou morte" in r.stdout


def test_un_repertoire_de_run_sans_plan_le_dit(depot: Depot) -> None:
    (depot.racine / ".maestro/orchestrate/20260729-140000").mkdir(parents=True)
    r = depot.lance("status.sh")
    assert r.returncode == 0, r.stderr
    assert "sans plan" in r.stdout


def test_le_fichier_stop_est_signale(depot: Depot) -> None:
    _run_dir(depot, "20260729-150000", [(1, 130, "-", "haute")], resume=[], sessions=(130,))
    (depot.racine / ".maestro/orchestrate/STOP").touch()
    r = depot.lance("status.sh")
    assert "arrêt demandé" in r.stdout
    assert "s'arrêtera entre deux tickets" in r.stdout


def test_le_run_par_defaut_est_le_plus_recent_et_run_id_cible_un_autre(depot: Depot) -> None:
    _run_dir(depot, "20260728-080000", [(1, 130, "-", "haute")],
             resume=[(130, "OK", "99", 60, "1")])
    _run_dir(depot, "20260729-080000", [(1, 140, "-", "haute")],
             resume=[(140, "OK", "98", 60, "1")])

    defaut = depot.lance("status.sh")
    assert "Run 20260729-080000" in defaut.stdout and "#140" in defaut.stdout

    cible = depot.lance("status.sh", "--run-id", "20260728-080000")
    assert "Run 20260728-080000" in cible.stdout and "#130" in cible.stdout

    inconnu = depot.lance("status.sh", "--run-id", "jamais-vu")
    assert inconnu.returncode == 1
    assert "--list" in inconnu.stderr, "on oriente vers la liste plutôt que de laisser deviner"


def test_la_liste_enumere_les_runs_connus(depot: Depot) -> None:
    _run_dir(depot, "20260728-080000", [(1, 130, "-", "haute")],
             resume=[(130, "OK", "99", 60, "1")])
    _run_dir(depot, "20260729-080000", [(1, 140, "-", "haute"), (2, 141, "-", "haute")], resume=[])
    r = depot.lance("status.sh", "--list")
    assert r.returncode == 0, r.stderr
    lignes = [x for x in r.stdout.splitlines() if x.strip().startswith("20260")]
    assert len(lignes) == 2
    assert lignes[0].strip().startswith("20260728"), "du plus ancien au plus récent"
    assert "2 ticket(s)" in lignes[1] and "0 traité(s)" in lignes[1]


def test_le_suivi_ne_boucle_pas_sur_un_run_qui_ne_tourne_plus(depot: Depot) -> None:
    """`--watch` sur un run terminé doit rendre la main : une boucle infinie n'apprend plus rien."""
    _run_dir(
        depot,
        "20260729-160000",
        [(1, 130, "-", "haute")],
        resume=[(130, "OK", "99", 600, "3.50", "-")],
    )
    debut = time.monotonic()
    r = depot.lance("status.sh", "--watch", "30")
    assert r.returncode == 0, r.stderr
    assert time.monotonic() - debut < 25, "un seul passage, pas d'attente"
    assert "rafraîchi toutes les 30 s" in r.stdout


def test_sans_gitlab_rien_n_est_interroge(depot: Depot) -> None:
    """La promesse « hors ligne » se vérifie sur les appels réellement émis, pas sur le message."""
    depot.ticket(131, "Ticket en cours", statut="En cours")
    _run_dir(depot, "20260729-170000", [(1, 131, "-", "moyenne")], resume=[], sessions=(131,))
    r = depot.lance("status.sh", "--no-gitlab")
    assert r.returncode == 0, r.stderr
    assert "non interrogé (--no-gitlab)" in r.stdout
    assert "En cours — #131" in r.stdout, "tout le reste est lu en local"
    assert not (depot.fixtures / "glab.log").exists(), "pas même un « glab auth status »"


def test_status_n_ecrit_rien(depot: Depot) -> None:
    """Un run en cours doit pouvoir être observé sans risquer de le perturber."""
    dossier = _run_dir(
        depot, "20260729-180000", [(1, 131, "-", "moyenne")], resume=[], sessions=(131,)
    )
    depot.ticket(131, "Ticket en cours", statut="En cours")

    def empreinte() -> dict[str, tuple[int, int]]:
        return {
            str(c.relative_to(dossier)): (c.stat().st_size, c.stat().st_mtime_ns)
            for c in sorted(dossier.rglob("*"))
        }

    avant = empreinte()
    assert depot.lance("status.sh").returncode == 0
    assert empreinte() == avant


@besoin_git
def test_le_worktree_est_le_signal_de_progression(depot: Depot) -> None:
    """`<iid>.json` reste vide jusqu'à la fin : ce qui dit que ça avance, ce sont les commits."""
    branche = "feat/130-ticket-130"
    _init_git(depot, branche)
    depot.ticket(130, "Ticket en cours", statut="En cours")
    depot.mr(branche, "opened")
    _run_dir(depot, "20260729-190000", [(1, 130, "-", "haute")], resume=[], sessions=(130,))

    assert GIT is not None

    def git(*args: str) -> None:
        subprocess.run(  # noqa: S603
            [GIT, *args], cwd=str(depot.racine), check=True, capture_output=True
        )

    (depot.racine / "livrable.txt").write_text("le travail\n", encoding="utf-8", newline="\n")
    git("add", "livrable.txt")
    git("-c", "core.hooksPath=", "commit", "--quiet", "-m", "feat: premiere moitie")
    (depot.racine / "livrable.txt").write_text("en cours\n", encoding="utf-8", newline="\n")

    r = depot.lance("status.sh")
    assert r.returncode == 0, r.stderr
    assert f"[{branche}]" in r.stdout, "le worktree du ticket est nommé avec sa branche"
    assert "commits    1 en avance sur origin/main" in r.stdout
    assert "feat: premiere moitie" in r.stdout
    assert "fichiers   1 modifié(s) : livrable.txt" in r.stdout
    assert "MR !99 ouverte" in r.stdout, "l'état GitLab complète ce que le disque sait"


@besoin_git
def test_l_activite_suit_le_worktree_et_pas_seulement_le_journal(depot: Depot) -> None:
    """Une session qui édite sans rien écrire dans le répertoire du run travaille quand même."""
    branche = "feat/130-ticket-130"
    _init_git(depot, branche)
    depot.ticket(130, "Ticket en cours", statut="En cours")
    _run_dir(
        depot, "20260729-200000", [(1, 130, "-", "haute")], resume=[], sessions=(130,), age=7200
    )
    # L'index git est touché à chaque `git add`/`status` de la session : c'est lui qui vit.
    assert GIT is not None
    subprocess.run(  # noqa: S603
        [GIT, "status", "--porcelain"], cwd=str(depot.racine), check=True, capture_output=True
    )

    r = depot.lance("status.sh", "--no-gitlab")
    assert r.returncode == 0, r.stderr
    assert "en cours ?" not in r.stdout, "le worktree bouge : le run n'est pas muet"
    assert "peut-être bloquée" not in r.stdout

    # « Depuis n'importe quel terminal » est la raison d'être de la commande : lancée d'ailleurs,
    # elle doit lire le même worktree. `git rev-parse --git-path index` rend un chemin RELATIF sur
    # un répertoire de travail principal — non repris, il se résoudrait depuis le mauvais dossier
    # et l'activité du ticket retomberait sur les seuls fichiers du run, tous vieillis ici.
    ailleurs = depot.lance("status.sh", "--no-gitlab", cwd=depot.racine.parent)
    assert ailleurs.returncode == 0, ailleurs.stderr
    assert "en cours ?" not in ailleurs.stdout
    assert ailleurs.stdout.count("commits") == r.stdout.count("commits")


# =====================================================================================
# Une session qui rend la main sans verdict (#178)
# =====================================================================================
# Le mode d'échec le plus coûteux du premier run réel : la session croit faire une pause
# (« j'attends la fin du run de couverture »), or en `claude -p` la fin du tour est la fin du
# processus. Le CLI sort en `end_turn` / `success` / code 0 — indiscernable d'une session qui a
# fini — et le ticket reste « À faire », son travail non commité dans le worktree.
#
# Les tests reprennent `_init_git` : distinguer « a produit sans clore » de « n'a rien produit »
# se lit dans un vrai dépôt git, pas dans un dossier quelconque. Toujours sans quota ni réseau :
# le bouchon `claude` joue la sortie en code 0 sans MR, et écrit (ou non) dans le worktree.

def _stub_sans_cloture(depot: Depot, corps: str = "") -> str:
    """Un `claude` qui sort comme un succès sans avoir rien clos — le cas du run 20260729-132807."""
    return _claude_stub(depot, textwrap.dedent(corps) + """
        printf '{"type":"result","subtype":"success","is_error":false,"total_cost_usd":6.04,'
        printf '"result":"Je poursuivrai avec /ticket-ship des le verdict connu."}\\n'
        exit 0
    """)


@besoin_git
def test_une_session_qui_croit_faire_une_pause_dit_le_travail_laisse_dans_le_worktree(
    depot: Depot,
) -> None:
    depot.ticket(130, "Ticket a traiter")
    # Le plan d'abord : écrit dans le dépôt jetable, il doit être commité par `_init_git` pour ne
    # pas compter comme du travail de la session.
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    _init_git(depot, "feat/130-ticket-a-traiter")
    claude = _stub_sans_cloture(depot, """
        for f in un deux trois quatre cinq; do printf 'travail\\n' > "$f.txt"; done
    """)
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "pause",
                    env={"MAESTRO_CLAUDE_BIN": claude})

    assert r.returncode == 1, "sans MR ni « En revue », c'est un échec : le code 0 ne dit rien"
    resume = (depot.racine / ".maestro/orchestrate/pause/resume.tsv").read_text(encoding="utf-8")
    assert "130\tECHEC" in resume
    assert "session terminée sans clôture, 5 fichier(s) non commité(s)" in resume, (
        "la raison consignée doit être exploitable, pas juste « MR aucune, cycle de vie À faire »"
    )
    assert "MR « aucune »" in resume, "le verdict GitLab reste dit, il n'est pas remplacé"
    assert "le travail est conservé dans" in r.stdout, "la console dit où le retrouver"


@besoin_git
def test_une_session_qui_n_a_rien_laisse_est_dite_telle_quelle(depot: Depot) -> None:
    """L'autre moitié de la distinction : un worktree propre est à refaire, pas à reprendre."""
    depot.ticket(130, "Ticket a traiter")
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    _init_git(depot, "feat/130-ticket-a-traiter")
    claude = _stub_sans_cloture(depot)
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "vide",
                    env={"MAESTRO_CLAUDE_BIN": claude})

    assert r.returncode == 1
    resume = (depot.racine / ".maestro/orchestrate/vide/resume.tsv").read_text(encoding="utf-8")
    assert "session terminée sans rien produire (worktree propre)" in resume
    assert "non commité" not in resume
    assert "le travail est conservé dans" not in r.stdout, "il n'y a rien à conserver"


@besoin_git
def test_un_travail_commite_mais_non_clos_compte_aussi_comme_du_travail_en_attente(
    depot: Depot,
) -> None:
    """Une session peut avoir tout commité et s'être arrêtée juste avant `/ticket-ship`."""
    depot.ticket(130, "Ticket a traiter")
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    _init_git(depot, "feat/130-ticket-a-traiter")
    claude = _stub_sans_cloture(depot, """
        printf 'le travail\\n' > livrable.txt
        git add livrable.txt >/dev/null 2>&1
        git -c core.hooksPath= commit --quiet -m 'feat: livrable' >/dev/null 2>&1
    """)
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "commite",
                    env={"MAESTRO_CLAUDE_BIN": claude})

    assert r.returncode == 1
    resume = (depot.racine / ".maestro/orchestrate/commite/resume.tsv").read_text(encoding="utf-8")
    assert "session terminée sans clôture, 1 commit(s) sur la branche" in resume
    assert "le travail est conservé dans" in r.stdout


def test_le_prompt_interdit_d_attendre_un_resultat_et_couvre_le_travail_non_commite(
    depot: Depot,
) -> None:
    """Les deux causes du run perdu : le prompt ne parlait que de *validation*, et sa consigne de
    reprise ne couvrait que les *commits* — pas l'arbre sale qu'une session interrompue laisse."""
    depot.ticket(130, "Ticket a traiter")
    claude = _claude_stub(depot, """
        # `-p` est le premier argument : le prompt est le second.
        printf '%s' "$2" > "$MAESTRO_FIXTURES/prompt.txt"
        printf '{"type":"result","subtype":"success","is_error":false}\\n'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    depot.lance("run.sh", "--plan", plan, "--run-id", "prompt",
                env={"MAESTRO_CLAUDE_BIN": claude})

    prompt = (depot.fixtures / "prompt.txt").read_text(encoding="utf-8")
    assert "N'attends AUCUN RÉSULTAT" in prompt
    assert "ORCHESTRATE: ECHEC" in prompt, "la sortie franche reste la troisième issue"
    assert "modifications non commitées" in prompt, (
        "un arbre sale sans commit est la trace d'une session perdue : elle doit la reprendre"
    )


def test_le_prompt_de_reprise_porte_la_meme_interdiction(depot: Depot) -> None:
    depot.ticket(130, "Ticket interrompu")
    claude = _claude_stub(depot, """
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          printf '%s' "$2" > "$MAESTRO_FIXTURES/prompt-reprise.txt"
          printf '{"is_error":false,"subtype":"success","total_cost_usd":2}\\n'; exit 0
        fi
        printf '{"is_error":true,"result":"Claude AI usage limit reached"}\\n'
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    depot.lance("run.sh", "--plan", plan, "--run-id", "prompt-reprise",
                env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "1"})

    prompt = (depot.fixtures / "prompt-reprise.txt").read_text(encoding="utf-8")
    assert "aucun résultat différé" in prompt
    assert "ORCHESTRATE: ECHEC" in prompt


# =====================================================================================
# journal.sh — la rétention du journal d'orchestration (#198)
# =====================================================================================

def _vieux_run(depot: Depot, run_id: str, *, age: int, flux: str | None = None) -> Path:
    """Un répertoire de run figé dans le passé — `age` en secondes depuis sa dernière écriture.

    L'âge est le seul levier sur les décisions de `journal.sh` : un run qui a écrit récemment est
    présumé vivant, donc épargné quoi qu'il arrive. Sans vieillissement, tous les runs d'un test
    seraient protégés et la rétention n'aurait jamais rien à ramasser.
    """
    dossier = _run_dir(depot, run_id, [(1, 130, "-", "moyenne")], resume=[])
    if flux is not None:
        (dossier / "130.jsonl").write_text(flux, encoding="utf-8", newline="\n")
    quand = time.time() - age
    for chemin in (*sorted(dossier.rglob("*")), dossier):
        os.utime(chemin, (quand, quand))
    return dossier


def _runs_presents(depot: Depot) -> list[str]:
    dossier = depot.racine / ".maestro/orchestrate"
    return sorted(p.name for p in dossier.iterdir() if p.is_dir())


def test_la_retention_ne_garde_que_les_runs_les_plus_recents(depot: Depot) -> None:
    """Le cœur du ticket : sans elle, `.maestro/orchestrate/` ne fait que grossir."""
    for i in range(1, 7):
        _vieux_run(depot, f"run-{i:02d}", age=3600 + (7 - i) * 60)
    r = depot.lance("journal.sh", "gc", env={"MAESTRO_ORCHESTRATE_JOURNAL_RUNS": "3"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert _runs_presents(depot) == ["run-04", "run-05", "run-06"]


def test_ni_le_run_courant_ni_un_run_qui_ecrit_encore_ne_sont_purges(depot: Depot) -> None:
    """Purger sous les pieds d'un run détaché lui ferait perdre son journal — et `status.sh` avec.

    Deux protections distinctes, éprouvées ensemble : le run que `run.sh` désigne (`--courant`) et
    celui dont la dernière écriture est récente, seul indice d'activité en l'absence de PID.
    """
    for i in range(1, 4):
        _vieux_run(depot, f"vieux-{i}", age=3600 + i * 60)
    _vieux_run(depot, "courant", age=3600)
    _run_dir(depot, "en-cours", [(1, 130, "-", "moyenne")])  # écrit à l'instant

    r = depot.lance("journal.sh", "gc", "--courant", "courant",
                    env={"MAESTRO_ORCHESTRATE_JOURNAL_RUNS": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    restants = _runs_presents(depot)
    assert "courant" in restants, "le run désigné n'est jamais candidat"
    assert "en-cours" in restants, "un run qui écrit encore est présumé vivant"
    assert "vieux-1" in restants, "le plus récent des candidats tient dans la rétention"
    assert "vieux-2" not in restants and "vieux-3" not in restants


def test_check_dit_ce_qui_partirait_sans_rien_ecrire(depot: Depot) -> None:
    _vieux_run(depot, "garde", age=3600, flux='{"type":"result"}\n')
    _vieux_run(depot, "vieux", age=7200, flux='{"type":"result"}\n')
    r = depot.lance("journal.sh", "gc", "--check",
                    env={"MAESTRO_ORCHESTRATE_JOURNAL_RUNS": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "vieux à retirer" in r.stdout
    assert "rien n'a été touché" in r.stdout
    assert _runs_presents(depot) == ["garde", "vieux"], "--check ne supprime rien"
    dossier = depot.racine / ".maestro/orchestrate/garde"
    assert (dossier / "130.jsonl").exists(), "--check ne compacte rien non plus"
    assert not (dossier / "130.jsonl.gz").exists()


def test_un_repertoire_de_run_vide_est_ramasse(depot: Depot) -> None:
    """Les sorties précoces de `run.sh` (plan vide, `queue.sh` en échec) laissent un `mkdir -p`
    derrière elles : aucun `rm -rf` du script ne couvre ces chemins-là."""
    vide = depot.racine / ".maestro/orchestrate/20260728-201836"
    vide.mkdir(parents=True)
    quand = time.time() - 3600
    os.utime(vide, (quand, quand))
    _vieux_run(depot, "plein", age=3600)

    r = depot.lance("journal.sh", "gc")
    assert r.returncode == 0, r.stdout + r.stderr
    # La rétention par défaut (10) garderait les deux : un répertoire vide n'y entre pas, il ne
    # porte rien à conserver.
    assert _runs_presents(depot) == ["plein"]


def test_un_repertoire_vide_tout_juste_cree_est_epargne(depot: Depot) -> None:
    """Un run qui vient de démarrer est vide pendant les secondes que dure le calcul du plan."""
    neuf = depot.racine / ".maestro/orchestrate/tout-neuf"
    neuf.mkdir(parents=True)
    r = depot.lance("journal.sh", "gc")
    assert r.returncode == 0, r.stdout + r.stderr
    assert neuf.exists(), "le vide n'autorise le retrait qu'une fois le silence installé"


def test_le_flux_d_un_run_conserve_est_compacte_sans_le_rajeunir(depot: Depot) -> None:
    """Compacter ne doit pas faire passer un vieux run pour un run actif : la date de la dernière
    écriture est ce dont `status.sh` — et la rétention elle-même — déduisent l'activité."""
    contenu = '{"type":"system"}\n{"type":"result","total_cost_usd":4.2}\n'
    dossier = _vieux_run(depot, "garde", age=3600, flux=contenu)
    avant = (dossier / "plan.tsv").stat().st_mtime

    r = depot.lance("journal.sh", "gc")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (dossier / "130.jsonl").exists()
    gz = dossier / "130.jsonl.gz"
    assert gzip.decompress(gz.read_bytes()).decode("utf-8") == contenu, "rien n'est perdu"
    assert abs(gz.stat().st_mtime - avant) < 5, "la date du flux survit à la compaction"


def test_le_flux_est_compacte_une_fois_le_verdict_rendu(depot: Depot) -> None:
    """Bout en bout : `run.sh` laisse un `.jsonl.gz`, pas un flux brut, dès le ticket terminé."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _stub_flux(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "compacte",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    dossier = depot.racine / ".maestro/orchestrate/compacte"
    assert not (dossier / "130.jsonl").exists()
    assert '"type":"result"' in _flux(dossier), "le flux reste relisible, compacté"
    assert (dossier / "130.json").exists(), "le résultat final, lui, reste en clair"


def test_la_compaction_attend_le_verdict_et_ne_casse_pas_la_reprise(depot: Depot) -> None:
    """Compacter pendant le ticket ferait passer une pause pour un échec : `delai_avant_reprise`
    relit le `.jsonl` ENTIER à chaque tentative pour y trouver la limite d'usage."""
    depot.ticket(130, "Ticket interrompu")
    depot.mr("feat/130-ticket-interrompu", "opened")
    claude = _claude_stub(depot, f"""
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
          printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":6}}\\n'
          exit 0
        fi
        printf '{{"type":"result","is_error":true,"result":"Claude AI usage limit reached"}}\\n'
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "reprise-compacte",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "reprise 1/3" in r.stdout, "la limite est toujours détectée, donc le flux toujours lu"
    dossier = depot.racine / ".maestro/orchestrate/reprise-compacte"
    assert (dossier / "130.jsonl.gz").exists(), "la compaction a bien eu lieu, mais à la fin"
    assert "usage limit reached" not in _flux(dossier), "la tentative perdue n'est pas réarchivée"


def test_un_run_fait_le_menage_du_journal_en_demarrant(depot: Depot) -> None:
    """La rétention n'est pas une commande à se rappeler : un run la déclenche en partant."""
    for i in range(1, 4):
        _vieux_run(depot, f"vieux-{i}", age=3600 + i * 60)
    depot.ticket(130, "Ticket à traiter")
    claude = _claude_stub(depot, """
        printf '{"type":"result","subtype":"success","is_error":false}\\n'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    depot.lance("run.sh", "--plan", plan, "--run-id", "neuf",
                env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_JOURNAL_RUNS": "1"})
    restants = _runs_presents(depot)
    assert "neuf" in restants, "le run qui fait le ménage ne se retire jamais lui-même"
    assert "vieux-1" in restants
    assert "vieux-2" not in restants and "vieux-3" not in restants


def test_le_menage_du_journal_se_desactive(depot: Depot) -> None:
    _vieux_run(depot, "vieux-1", age=3600)
    _vieux_run(depot, "vieux-2", age=7200)
    depot.ticket(130, "Ticket à traiter")
    claude = _claude_stub(depot, """
        printf '{"type":"result","subtype":"success","is_error":false}\\n'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    depot.lance("run.sh", "--plan", plan, "--run-id", "sans-menage",
                env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_JOURNAL_RUNS": "1",
                     "MAESTRO_ORCHESTRATE_JOURNAL_GC": "0"})
    assert "vieux-2" in _runs_presents(depot)


def test_un_seuil_de_retention_absurde_retombe_sur_le_defaut(depot: Depot) -> None:
    """Un `RUNS=0` mal posé viderait le journal entier : on préfère le défaut au pire."""
    for i in range(1, 4):
        _vieux_run(depot, f"run-{i}", age=3600 + i * 60)
    r = depot.lance("journal.sh", "gc", env={"MAESTRO_ORCHESTRATE_JOURNAL_RUNS": "0"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(_runs_presents(depot)) == 3


def test_un_journal_absent_est_un_cas_normal_pas_une_erreur(depot: Depot) -> None:
    r = depot.lance("journal.sh", "gc")
    assert r.returncode == 0, r.stderr
    assert "rien à ramasser" in r.stdout


def test_le_menage_automatique_se_tait_quand_il_n_a_rien_fait(depot: Depot) -> None:
    """`--auto` parle dans la console d'un run : le silence doit y être le cas normal."""
    _vieux_run(depot, "seul", age=3600)
    r = depot.lance("journal.sh", "gc", "--auto")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""


# =====================================================================================
# Le résultat d'une session, lisible à l'œil nu (#180)
# =====================================================================================

def _objet_result(**champs) -> str:
    """Un objet `result` tel que le CLI l'écrit : minifié, sur une ligne, accents en clair.

    `json.dumps` reproduit exactement ce qui rend `<iid>.json` illisible — les retours à la ligne du
    message final y sont des « \\n » littéraux, et les antislashs d'une commande refusée y sont
    doublés. C'est cette matière-là que la vue doit désescaper.
    """
    base = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 2086510,
        "duration_api_ms": 1308490,
        "num_turns": 100,
        "result": "Le ticket est traité.\n\n## Résumé\n\n- un point « accentué »\n- un autre",
        "stop_reason": "end_turn",
        "session_id": "dba6a0ea-f843-441a-aed1-218fb3162221",
        "total_cost_usd": 10.686978499999995,
        "permission_denials": [
            {"tool_name": "Skill", "tool_use_id": "t1",
             "tool_input": {"skill": "ticket-start", "args": "130"}},
            {"tool_name": "Bash", "tool_use_id": "t2",
             "tool_input": {"command": 'cd "E:/Projets" && git status', "description": "état"}},
        ],
    }
    base.update(champs)
    return json.dumps(base, ensure_ascii=False, separators=(",", ":"))


def _stub_resultat(depot: Depot, corps_json: str, *, iid: int = 130, code: int = 0,
                   statut: str | None = "En revue") -> str:
    """Un bouchon `claude` qui recrache un flux écrit dans un fichier.

    Passer par un fichier plutôt que par des `printf` évite d'avoir à échapper deux fois le JSON
    (une fois pour Python, une fois pour le shell) — et c'est justement l'échappement qu'on teste.
    """
    (depot.fixtures / f"flux-{iid}.jsonl").write_text(
        '{"type":"system","subtype":"init"}\n' + corps_json + "\n",
        encoding="utf-8",
        newline="\n",
    )
    corps = ""
    if statut:
        corps += (
            f"printf '%s' '{_statut_json(str(iid), statut)}' "
            f'> "$MAESTRO_FIXTURES/owner-{iid}.json"\n'
        )
    corps += f'cat "$MAESTRO_FIXTURES/flux-{iid}.jsonl"\nexit {code}\n'
    return _claude_stub(depot, corps)


def test_le_resultat_d_une_session_se_lit_a_l_oeil_apres_le_run(depot: Depot) -> None:
    """Le cœur du ticket : après un run, plus besoin d'un script pour lire ce qui s'est passé."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _stub_resultat(depot, _objet_result())
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "lisible",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr

    vue = (depot.racine / ".maestro/orchestrate/lisible/130.resultat.txt").read_text(
        encoding="utf-8"
    )
    # 1. De quoi on parle, et ce que GitLab en a dit — le verdict ne vient jamais de la prose.
    #    Le titre est celui du PLAN (« Ticket 130 » ici) : la vue est écrite par la boucle.
    assert "ticket #130" in vue and "Ticket 130" in vue
    assert "✓ OK" in vue and "MR !99" in vue
    # 2. Ce qu'on vient y chercher : coût, durée, refus.
    assert "10.69 $" in vue and "10.686978499999995" not in vue
    assert "34min46" in vue, "duration_ms se lit en heures et minutes, pas en millisecondes"
    assert "- Skill — ticket-start" in vue
    assert '- Bash — cd "E:/Projets" && git status' in vue
    # 3. Le message final DÉSESCAPÉ : c'est ce qui distingue une vue lisible du JSON brut.
    assert "\\n" not in vue, "les retours à la ligne sont de vrais retours à la ligne"
    assert "## Résumé" in vue and "« accentué »" in vue
    assert len(vue.splitlines()) > 10


def test_la_vue_lisible_ne_touche_pas_au_json_dont_depend_le_verdict(depot: Depot) -> None:
    """`champ_json` et `limite_atteinte` grepent `<iid>.json` : il reste brut, et sur une ligne."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    attendu = _objet_result()
    claude = _stub_resultat(depot, attendu)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "intact",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    dossier = depot.racine / ".maestro/orchestrate/intact"
    brut = (dossier / "130.json").read_text(encoding="utf-8")
    assert brut.strip() == attendu, "le fichier machine est recopié tel quel, octet pour octet"
    assert len(brut.strip().splitlines()) == 1
    assert "130\tOK\t99" in (dossier / "resume.tsv").read_text(encoding="utf-8")


def test_le_cout_est_arrondi_dans_le_bilan_et_dans_la_console(depot: Depot) -> None:
    """Quinze décimales n'apprennent rien de plus que deux, et débordent de toutes les colonnes."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _stub_resultat(depot, _objet_result())
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "arrondi",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    resume = (depot.racine / ".maestro/orchestrate/arrondi/resume.tsv").read_text(encoding="utf-8")
    assert "\t10.69\t" in resume, "le coût consigné tient en deux décimales"
    assert "10.686978499999995" not in resume
    assert "10.69 $" in r.stdout and "10.686978499999995" not in r.stdout
    # Le point décimal, pas la virgule : `status.sh` additionne cette colonne en awk.
    assert "10,69" not in resume


def test_une_session_morte_sans_resultat_le_dit_au_lieu_d_une_vue_vide(depot: Depot) -> None:
    """Un `<iid>.json` vide est le cas le plus opaque de tous — et le plus fréquent en échec."""
    depot.ticket(130, "Ticket à traiter")
    claude = _claude_stub(depot, "exit 1\n")
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "muet",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 1
    vue = (depot.racine / ".maestro/orchestrate/muet/130.resultat.txt").read_text(encoding="utf-8")
    assert "✗ ECHEC" in vue
    assert "aucun résultat final" in vue
    # Sans résultat, la vue ne peut que dire où regarder : le flux et la sortie d'erreur.
    assert "130.jsonl.gz" in vue and "130.log" in vue
    assert "130.resultat.txt" in r.stdout, "la console pointe la vue lisible, pas le JSON minifié"


def test_la_vue_lisible_se_rejoue_sur_un_journal_deja_ecrit(depot: Depot) -> None:
    """Les runs d'avant ce lot n'ont pas de `.resultat.txt` : `--resultat` les rattrape."""
    vieux = depot.racine / "130.json"
    vieux.write_text(_objet_result(), encoding="utf-8", newline="\n")
    r = depot.lance("run.sh", "--resultat", str(vieux))
    assert r.returncode == 0, r.stderr
    assert "ticket #130" in r.stdout, "l'iid se déduit du nom du fichier"
    assert "## Résumé" in r.stdout and "- Skill — ticket-start" in r.stdout
    assert "10.69 $" in r.stdout
    # Diagnostic = lecture seule : ni run, ni journal, ni appel à GitLab.
    assert not (depot.racine / ".maestro").exists()
    assert not (depot.fixtures / "glab.log").exists()


def test_un_resultat_illisible_est_refuse_sans_rien_inventer(depot: Depot) -> None:
    r = depot.lance("run.sh", "--resultat", str(depot.racine / "jamais-ecrit.json"))
    assert r.returncode == 2
    assert "illisible" in r.stderr


def test_un_plan_vide_ne_laisse_pas_de_repertoire_de_run(depot: Depot) -> None:
    """Quatre vestiges de ce genre traînaient dans `.maestro/orchestrate/` — dont aucun n'était
    strictement vide, donc aucun ramassable par la rétention de #198."""
    # Bouchon qui échoue bruyamment : sans lui, le test emprunterait le `claude` de la machine —
    # vert sur un poste de dev, rouge en CI où le CLI n'existe pas (le préflight le réclame avant
    # même de lire le plan). Un plan vide ne doit de toute façon démarrer aucune session.
    claude = _claude_stub(depot, 'echo "la session ne doit jamais démarrer" >&2\nexit 1\n')
    plan = _plan(depot, [])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "sans-suite",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "le plan est vide" in r.stdout
    assert not (depot.racine / ".maestro/orchestrate/sans-suite").exists()


def test_un_journal_qui_a_servi_n_est_jamais_emporte_par_ce_renoncement(depot: Depot) -> None:
    """Le garde-fou du renoncement : il ne retire un run que s'il ne porte QUE son plan."""
    dossier = depot.racine / ".maestro/orchestrate/deja-la"
    dossier.mkdir(parents=True)
    (dossier / "resume.tsv").write_text("# iid\n130\tOK\t99\t60\t1.00\t-\n", encoding="utf-8")
    claude = _claude_stub(depot, 'echo "la session ne doit jamais démarrer" >&2\nexit 1\n')
    plan = _plan(depot, [])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "deja-la",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    assert (dossier / "resume.tsv").exists(), "un bilan déjà écrit n'est pas un run sans suite"


# =====================================================================================
# Reprendre un run qui ne s'est pas terminé (#204)
# =====================================================================================

def _reprenables(depot: Depot) -> list[list[str]]:
    """Les lignes de `status.sh --reprenables`, découpées sur les tabulations."""
    r = depot.lance("status.sh", "--reprenables")
    assert r.returncode == 0, r.stderr
    return [ligne.split("\t") for ligne in r.stdout.splitlines() if ligne]


def test_un_run_qui_a_tout_livre_n_est_pas_a_reprendre(depot: Depot) -> None:
    _run_dir(
        depot, "20260730-100000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne")],
        resume=[(130, "OK", 99, 600, "3.50", "-"), (131, "OK", 98, 300, "1.20", "-")],
        age=4000,
    )
    assert _reprenables(depot) == [], "un plan entièrement soldé ne se rejoue pas"


def test_un_run_interrompu_est_reprenable_avec_ce_qu_il_lui_reste(depot: Depot) -> None:
    _run_dir(
        depot, "20260730-100000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne"), (3, 132, "-", "basse")],
        resume=[(130, "OK", 99, 600, "3.50", "-")],
        age=4000,
    )
    lignes = _reprenables(depot)
    assert len(lignes) == 1
    run_id, etat, restants, _debut, silence, courant = lignes[0]
    assert run_id == "20260730-100000"
    assert etat == "interrompu"
    assert restants == "2", "les tickets sans verdict, ticket en vol compris"
    assert int(silence) >= 4000
    assert courant == "", "ce run-là s'est arrêté entre deux tickets"
    # La colonne vide est la raison pour laquelle cette sortie ne se relit pas avec
    # « IFS=$'\t' read » : le tab est un blanc IFS, qui FUSIONNE les champs vides.
    assert len(lignes[0]) == 6, "six colonnes, y compris quand la dernière est vide"


def test_un_run_qui_ecrit_encore_n_est_pas_propose_a_la_reprise(depot: Depot) -> None:
    """Sans carte de pilote (journal d'avant #213), le silence reste le seul témoin — et il vaut.

    Le repli n'est pas décoratif : les journaux déjà sur disque n'ont pas de carte, et un run tué
    par SIGKILL laisse la sienne sans que personne la retire.
    """
    depot.ticket(131, "Ticket en cours", statut="En cours")
    _run_dir(
        depot, "20260730-100000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne")],
        resume=[(130, "OK", 99, 600, "3.50", "-")],
        sessions=(131,),
    )
    assert _reprenables(depot) == [], "on ne propose pas de reprendre un run qui travaille"


def test_un_run_tue_en_plein_ticket_est_reprenable_malgre_son_ticket_en_cours(
    depot: Depot,
) -> None:
    """Machine éteinte au milieu : le témoin de session reste, personne n'écrit de code de sortie.

    Sans le critère de silence, ce run garderait le visage d'un run qui travaille pour toujours —
    et c'est précisément celui qu'on veut pouvoir reprendre.
    """
    _run_dir(
        depot, "20260730-100000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne")],
        resume=[(130, "OK", 99, 600, "3.50", "-")],
        sessions=(131,),
        age=4000,
    )
    lignes = _reprenables(depot)
    assert len(lignes) == 1
    assert lignes[0][1] == "en-cours", "l'état déduit est dit tel quel, sans être maquillé"
    assert lignes[0][5] == "131", "le ticket en vol est nommé : c'est lui qu'une reprise reprend"


def test_les_runs_reprenables_ne_touchent_ni_a_gitlab_ni_au_disque(depot: Depot) -> None:
    _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute")], resume=[], age=4000)
    avant = sorted(p.name for p in (depot.racine / ".maestro/orchestrate").rglob("*"))
    assert _reprenables(depot)[0][0] == "20260730-100000"
    assert not (depot.fixtures / "glab.log").exists(), "une liste qui doit marcher hors ligne"
    apres = sorted(p.name for p in (depot.racine / ".maestro/orchestrate").rglob("*"))
    assert avant == apres


def test_la_liste_des_runs_signale_ceux_qu_on_peut_reprendre(depot: Depot) -> None:
    _run_dir(depot, "20260729-090000", [(1, 130, "-", "haute")],
             resume=[(130, "OK", 99, 60, "1.00", "-")], age=4000)
    _run_dir(depot, "20260730-100000", [(1, 131, "-", "haute")], resume=[], age=4000)
    r = depot.lance("status.sh", "--list")
    assert r.returncode == 0, r.stderr
    lignes = r.stdout.splitlines()
    ligne_soldee = next(x for x in lignes if "20260729-090000" in x)
    ligne_reprenable = next(x for x in lignes if "20260730-100000" in x)
    assert "reprenable" not in ligne_soldee
    assert "reprenable" in ligne_reprenable
    assert "--resume" in r.stdout, "on dit quoi taper, pas seulement qu'il reste quelque chose"


def test_un_run_interrompu_propose_sa_propre_reprise(depot: Depot) -> None:
    """La vue détaillée nomme le run-id, jamais un chemin de journal à recopier."""
    _run_dir(depot, "20260730-100000",
             [(1, 130, "-", "haute"), (2, 131, "-", "moyenne")],
             resume=[(130, "OK", 99, 600, "3.50", "-")], age=4000)
    r = depot.lance("status.sh", "--run-id", "20260730-100000", "--no-gitlab")
    assert r.returncode == 0, r.stderr
    assert "/orchestrate --resume 20260730-100000" in r.stdout
    assert "1 ticket(s) sans verdict" in r.stdout


def test_resume_rejoue_le_plan_du_run_vise_sans_le_recalculer(depot: Depot) -> None:
    """Le backlog a pu bouger : un ordre recalculé n'aurait plus rien du run qu'on croit reprendre.

    Aucun backlog n'est publié dans ce test — `queue.sh` échouerait s'il était appelé. C'est la
    preuve que le plan vient bien du run repris.
    """
    depot.ticket(130, "Deja livre", statut="En revue")
    depot.ticket(131, "Reste a faire")
    depot.mr("feat/131-reste-a-faire", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("131", "En revue")}' > "$MAESTRO_FIXTURES/owner-131.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":2}}'
        exit 0
    """)
    source = _run_dir(
        depot, "20260730-100000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne")],
        resume=[(130, "OK", 99, 600, "3.50", "-")],
        age=4000,
    )
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "suite",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr

    nouveau = depot.racine / ".maestro/orchestrate/suite"
    assert (nouveau / "plan.tsv").read_text(encoding="utf-8") == \
        (source / "plan.tsv").read_text(encoding="utf-8")
    assert (nouveau / "reprise-de").read_text(encoding="utf-8").strip() == "20260730-100000"
    resume = (nouveau / "resume.tsv").read_text(encoding="utf-8")
    assert "130\tSAUTE" in resume, "un ticket livré depuis se saute de lui-même, par son statut"
    assert "131\tOK" in resume
    assert "reprise du run 20260730-100000" in r.stdout


def test_en_reprise_le_compteur_dit_la_position_dans_le_plan(depot: Depot) -> None:
    """Une reprise saute tout ce qui a été livré depuis, or le compteur suivait les tickets TENTÉS :
    le 3e du plan s'annonçait « [1/3] », et le run se terminait sur un compte qui n'y était pas."""
    depot.ticket(130, "Deja livre", statut="En revue")
    depot.ticket(131, "Livre aussi", statut="En revue")
    depot.ticket(132, "Reste a faire")
    depot.mr("feat/132-reste-a-faire", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("132", "En revue")}' > "$MAESTRO_FIXTURES/owner-132.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":2}}'
        exit 0
    """)
    _run_dir(
        depot, "20260730-100000",
        [(1, 130, "-", "haute"), (2, 131, "-", "haute"), (3, 132, "-", "moyenne")],
        resume=[(130, "OK", 99, 600, "3.50", "-")],
        age=4000,
    )
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "suite",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[3/3] #132" in r.stdout, "le seul ticket restant est le 3e du plan, pas le 1er"
    assert "[1/3]" not in r.stdout


def test_reprendre_n_ecrase_jamais_le_bilan_du_run_repris(depot: Depot) -> None:
    """`resume.tsv` s'écrit en tête de run : rejouer dans le même répertoire effacerait tout."""
    depot.ticket(130, "Deja livre", statut="En revue")
    source = _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute")],
                      resume=[(130, "ECHEC", "-", 60, "1.00", "session coupée")], age=4000)
    avant = (source / "resume.tsv").read_text(encoding="utf-8")
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "suite",
                    env={"MAESTRO_CLAUDE_BIN": "true"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert (source / "resume.tsv").read_text(encoding="utf-8") == avant
    assert (depot.racine / ".maestro/orchestrate/suite/resume.tsv").exists()


def test_resume_sans_argument_prend_le_run_reprenable_le_plus_recent(depot: Depot) -> None:
    depot.ticket(131, "Reste a faire")
    _run_dir(depot, "20260101-000000", [(1, 130, "-", "haute")],
             resume=[(130, "OK", 99, 60, "1.00", "-")], age=4000)
    _run_dir(depot, "20260202-000000", [(1, 131, "-", "haute")], resume=[], age=4000)
    r = depot.lance("run.sh", "--resume", "--dry-run",
                    env={"MAESTRO_CLAUDE_BIN": "true"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "reprise du run 20260202-000000" in r.stdout
    assert "#131" in r.stdout


def test_resume_sans_rien_a_reprendre_le_dit_et_ne_cree_aucun_run(depot: Depot) -> None:
    _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute")],
             resume=[(130, "OK", 99, 60, "1.00", "-")], age=4000)
    r = depot.lance("run.sh", "--resume", env={"MAESTRO_CLAUDE_BIN": "true"})
    assert r.returncode == 1
    assert "aucun run à reprendre" in r.stderr
    assert "--detach" in r.stderr, "on oriente vers le run neuf plutôt que de laisser en plan"
    runs = sorted(p.name for p in (depot.racine / ".maestro/orchestrate").iterdir())
    assert runs == ["20260730-100000"], "rien de créé pour une reprise qui n'a pas eu lieu"


def test_resume_sur_un_run_inconnu_est_refuse_sans_rien_inventer(depot: Depot) -> None:
    r = depot.lance("run.sh", "--resume", "jamais-lance", env={"MAESTRO_CLAUDE_BIN": "true"})
    assert r.returncode == 1
    assert "n'a pas de plan lisible" in r.stderr
    assert not (depot.racine / ".maestro/orchestrate/jamais-lance").exists()


def test_resume_et_plan_ensemble_sont_refuses(depot: Depot) -> None:
    """Deux façons de désigner le plan à jouer : en garder deux serait un piège silencieux."""
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--plan", plan,
                    env={"MAESTRO_CLAUDE_BIN": "true"})
    assert r.returncode == 2
    assert "n'en garder qu'un" in r.stderr


def test_le_ticket_en_vol_est_repris_au_lieu_d_etre_saute(depot: Depot) -> None:
    """La victime de la coupure : `/ticket-start` lui a posé « En cours », donc le filtre de statut
    l'écarterait comme s'il appartenait à quelqu'un d'autre — avec son worktree et son travail."""
    depot.ticket(130, "Ticket en vol", statut="En cours")
    depot.mr("feat/130-ticket-en-vol", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s\\n' "$@" > "$MAESTRO_FIXTURES/argv.txt"
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":2}}'
        exit 0
    """)
    _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute")],
             resume=[], sessions=(130,), age=4000)
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "suite",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "repris en vol" in r.stdout
    resume = (depot.racine / ".maestro/orchestrate/suite/resume.tsv").read_text(encoding="utf-8")
    assert "130\tOK" in resume and "SAUTE" not in resume

    argv = (depot.fixtures / "argv.txt").read_text(encoding="utf-8")
    assert "--resume" in argv, "la session de la coupure est rouverte, pas recommencée à zéro"
    assert "11111111-2222-4333-a444-555555555555" in argv, "et c'est bien SON uuid"
    # L'uuid a été recopié dans le journal neuf : la reprise suivante le retrouvera là.
    session = depot.racine / ".maestro/orchestrate/suite/130.session"
    assert session.read_text(encoding="utf-8").strip() == "11111111-2222-4333-a444-555555555555"


def test_un_ticket_en_cours_que_le_run_n_avait_pas_en_main_reste_saute(depot: Depot) -> None:
    """L'exception est étroite : sans témoin de session dans le run repris, « En cours » veut dire
    qu'une autre session travaille dessus — et on ne lui prend pas son ticket."""
    depot.ticket(130, "Pris par quelqu'un d'autre", statut="En cours", assigne="alice")
    claude = _claude_stub(depot, 'echo "la session ne doit jamais démarrer" >&2\nexit 1\n')
    _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute")], resume=[], age=4000)
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "suite",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, "un ticket sauté n'est pas un échec"
    resume = (depot.racine / ".maestro/orchestrate/suite/resume.tsv").read_text(encoding="utf-8")
    assert "130\tSAUTE" in resume and "En cours" in resume


def test_un_ticket_deja_solde_par_le_run_repris_n_est_pas_repris_en_vol(depot: Depot) -> None:
    """Témoin de session ET ligne de bilan : le ticket a rendu son verdict, la coupure est venue
    après. Son « En cours » est alors celui d'un échec, pas d'un travail en cours de session."""
    depot.ticket(130, "Echoue puis laisse", statut="En cours")
    claude = _claude_stub(depot, 'echo "la session ne doit jamais démarrer" >&2\nexit 1\n')
    _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute"), (2, 131, "-", "haute")],
             resume=[(130, "ECHEC", "-", 60, "1.00", "session terminée sans clôture")],
             sessions=(130,), age=4000)
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "suite", "--max", "1",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    resume = (depot.racine / ".maestro/orchestrate/suite/resume.tsv").read_text(encoding="utf-8")
    assert "130\tSAUTE" in resume
    assert "repris en vol" not in r.stdout


def test_resume_avec_detach_passe_le_run_resolu_au_lanceur(depot: Depot) -> None:
    """Le lanceur doit porter le run REPRIS, pas un « --resume » à re-résoudre : la liste aura
    changé d'ici là — le run qu'on vient de créer y figurerait, entre autres."""
    _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute")], resume=[], age=4000)
    spawn = _spawn_stub(depot)
    r = depot.lance(
        "run.sh", "--resume", "--detach", "--run-id", "detachee",
        env={"MAESTRO_CLAUDE_BIN": "true", "MAESTRO_ORCHESTRATE_SPAWN": spawn},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    corps = (depot.racine / ".maestro/orchestrate/detachee/lancer.sh").read_text(encoding="utf-8")
    commande = next(ligne for ligne in corps.splitlines() if ligne.startswith("bash "))
    assert "--resume 20260730-100000" in commande, "le run repris est nommé, la valeur est résolue"
    assert commande.count("--resume") == 1
    assert commande.count("--run-id") == 1
    assert "--detach" not in commande
    assert "reprise    du run 20260730-100000" in r.stdout


def test_le_journal_neuf_dit_de_quel_run_il_est_la_suite(depot: Depot) -> None:
    """Deux journaux partiels racontent la même liste de tickets : ils doivent se répondre."""
    depot.ticket(130, "Reste a faire")
    _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute")], resume=[], age=4000)
    depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "suite",
                env={"MAESTRO_CLAUDE_BIN": "true"})
    r = depot.lance("status.sh", "--run-id", "suite", "--no-gitlab")
    assert r.returncode == 0, r.stderr
    assert "reprise    du run 20260730-100000" in r.stdout


def test_une_reprise_sans_suite_ne_laisse_pas_de_repertoire(depot: Depot) -> None:
    """Le renoncement (#180) doit aussi savoir jeter le marqueur de reprise qu'il vient de poser."""
    claude = _claude_stub(depot, 'echo "la session ne doit jamais démarrer" >&2\nexit 1\n')
    _run_dir(depot, "20260730-100000", [], resume=[], age=4000)
    # Un plan vide n'est pas reprenable : on vise donc le run explicitement.
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "sans-suite",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "le plan est vide" in r.stdout
    assert not (depot.racine / ".maestro/orchestrate/sans-suite").exists()


def test_reprendre_un_run_dans_son_propre_repertoire_est_refuse(depot: Depot) -> None:
    """Le cas tordu qui viderait le bilan qu'on prétend préserver : plan recopié sur lui-même,
    puis `resume.tsv` réécrit en tête de run."""
    source = _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute")],
                      resume=[(130, "OK", 99, 60, "1.00", "-")], age=4000)
    avant = (source / "resume.tsv").read_text(encoding="utf-8")
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "20260730-100000",
                    env={"MAESTRO_CLAUDE_BIN": "true"})
    assert r.returncode == 2
    assert "son bilan serait écrasé" in r.stderr
    assert (source / "resume.tsv").read_text(encoding="utf-8") == avant


# =====================================================================================
# Un seul run à la fois — carte du pilote et arrêt des runs en vol (#213)
# =====================================================================================
#
# Ces tests-là lancent de VRAIS processus (un `sleep` qui pose sa carte comme un run le ferait) et
# les tuent pour de bon : c'est le seul moyen de vérifier qu'un arrêt arrête. Aucun n'appelle Claude
# ni GitLab — `--tuer-les-runs` ne touche ni au plan ni au réseau.


def _pilote_factice(depot: Depot, dossier: Path, duree: int = 120) -> subprocess.Popen:
    """Un processus bien vivant qui pose SA carte dans `dossier`, comme le ferait un run.

    La carte est écrite par `pilote.sh` lui-même, pas fabriquée à la main : un test qui inventerait
    le format ne vérifierait plus que sa propre invention (et la naissance, en ticks, ne se devine
    pas depuis Python).
    """
    script = depot.racine.parent / "bin" / "faux-pilote"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'. "{depot.racine}/scripts/orchestrate/pilote.sh"\n'
        'pilote_ecrit "$1"\n'
        'sleep "$2"\n',
        encoding="utf-8",
        newline="\n",
    )
    script.chmod(0o755)
    proc = subprocess.Popen(
        [BASH, str(script), str(dossier), str(duree)],
        env=depot.env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # On attend une carte COMPLÈTE, pas seulement présente : le fichier apparaît dès l'ouverture de
    # la redirection, et un test qui le relirait dans cette fenêtre verrait ses retouches écrasées
    # par la fin de l'écriture. `hote` est le dernier champ posé.
    carte = dossier / "pid"
    for _ in range(200):
        if carte.exists() and "hote=" in carte.read_text(encoding="utf-8", errors="replace"):
            return proc
        time.sleep(0.05)
    proc.kill()
    raise AssertionError("le pilote factice n'a jamais posé sa carte")


def _carte(dossier: Path, **remplacements: str) -> None:
    """Réécrit la carte du pilote en changeant les champs demandés (PID recyclé, autre hôte…)."""
    champs = dict(
        ligne.split("=", 1)
        for ligne in (dossier / "pid").read_text(encoding="utf-8").splitlines()
        if "=" in ligne
    )
    champs.update(remplacements)
    (dossier / "pid").write_text(
        "".join(f"{c}={v}\n" for c, v in champs.items()), encoding="utf-8", newline="\n"
    )


def test_un_run_en_vol_est_arrete_avant_qu_un_autre_demarre(depot: Depot) -> None:
    """Le cœur de #213 : le processus est réellement tué, pas seulement signalé.

    On tue par `--tuer-les-runs`, qui est exactement le geste que tout démarrage fait d'office —
    sans avoir à dérouler un run entier pour l'observer.
    """
    dossier = _run_dir(depot, "20260803-171434", [(1, 130, "-", "haute"), (2, 131, "-", "haute")],
                       resume=[(130, "OK", 99, 600, "3.50", "-")], sessions=(131,))
    proc = _pilote_factice(depot, dossier)
    try:
        r = depot.lance("run.sh", "--tuer-les-runs")
        assert r.returncode == 0, r.stderr
        assert proc.wait(timeout=30) is not None, "le pilote tourne toujours après son arrêt"
        assert "20260803-171434" in r.stdout, "le run arrêté est nommé"
        assert "#131" in r.stdout, "le ticket en vol est nommé : c'est lui qu'on interrompt"
        assert "--resume" in r.stdout, "un run tué reste reprenable, et le rapport doit le dire"
        assert (dossier / "plan.tsv").exists() and (dossier / "resume.tsv").exists(), \
            "tuer un run ne touche pas à son journal"
    finally:
        proc.kill()


def test_un_run_tue_redevient_reprenable_immediatement(depot: Depot) -> None:
    """Sans la carte, le run qu'on vient de tuer resterait invisible un quart d'heure.

    C'est ce qui rend cohérent l'enchaînement « je tue, puis je reprends » : `--reprenables` écarte
    les runs qui écrivent encore, et celui-là vient tout juste d'écrire.
    """
    dossier = _run_dir(depot, "20260803-171434", [(1, 130, "-", "haute"), (2, 131, "-", "haute")],
                       resume=[(130, "OK", 99, 600, "3.50", "-")], sessions=(131,))
    proc = _pilote_factice(depot, dossier)
    try:
        assert _reprenables(depot) == [], "un run vivant ne se reprend pas : il travaille"
        depot.lance("run.sh", "--tuer-les-runs")
        proc.wait(timeout=30)
        lignes = _reprenables(depot)
        assert [ligne[0] for ligne in lignes] == ["20260803-171434"], \
            "pilote mort : reprenable tout de suite, sans attendre que le silence s'installe"
        assert lignes[0][1] == "interrompu"
    finally:
        proc.kill()


def test_un_pilote_vivant_n_est_jamais_propose_a_la_reprise(depot: Depot) -> None:
    """La carte l'emporte sur le silence : une session qui réfléchit longuement reste vivante."""
    dossier = _run_dir(depot, "20260803-171434", [(1, 130, "-", "haute")], resume=[],
                       sessions=(130,), age=4000)
    proc = _pilote_factice(depot, dossier)
    try:
        assert _reprenables(depot) == [], \
            "4000 s sans une écriture, mais le pilote répond : ce run n'est pas à reprendre"
        r = depot.lance("status.sh", "--list")
        assert "en cours" in r.stdout, "un run vivant se voit dans la liste"
    finally:
        proc.kill()


def test_un_pid_recycle_n_est_jamais_tue(depot: Depot) -> None:
    """Le garde-fou qui protège les processus des autres : le numéro seul ne prouve rien.

    Un run tué par SIGKILL laisse sa carte derrière lui (aucun trap ne survit), et son numéro finit
    par désigner quelqu'un d'autre. La naissance du processus, elle, ne se recycle pas.
    """
    dossier = _run_dir(depot, "20260803-171434", [(1, 130, "-", "haute")], resume=[])
    proc = _pilote_factice(depot, dossier)
    try:
        _carte(dossier, naissance="999999999")
        r = depot.lance("run.sh", "--tuer-les-runs")
        assert r.returncode == 0, r.stderr
        assert "Aucun run en cours" in r.stdout
        assert proc.poll() is None, "un processus dont l'identité ne colle pas doit être épargné"
    finally:
        proc.kill()


def test_une_carte_orpheline_ne_fait_ni_erreur_ni_degat(depot: Depot) -> None:
    """Carte laissée par un run mort depuis longtemps : rien à tuer, et ce n'est pas une panne."""
    dossier = _run_dir(depot, "20260803-171434", [(1, 130, "-", "haute")], resume=[])
    proc = _pilote_factice(depot, dossier)
    proc.kill()
    proc.wait(timeout=30)
    r = depot.lance("run.sh", "--tuer-les-runs")
    assert r.returncode == 0, r.stderr
    assert "Aucun run en cours" in r.stdout


def test_un_run_qui_demarre_pose_sa_carte_et_la_retire_en_partant(depot: Depot) -> None:
    """La carte vit le temps du run : posée avant le premier ticket, retirée à la sortie.

    Le bouchon `claude` relève ce qui est sur le disque PENDANT la session — seul moment où la
    carte du run courant existe.
    """
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _claude_stub(depot, f"""
        cat "$MAESTRO_STUB_WORKTREE_DIR"/.maestro/orchestrate/*/pid > "$MAESTRO_FIXTURES/carte.txt"
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "carte",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    pendant = (depot.fixtures / "carte.txt").read_text(encoding="utf-8")
    assert "pid=" in pendant and "naissance=" in pendant, \
        "un run en cours doit être identifiable, sans quoi personne ne peut l'arrêter"
    assert not (depot.racine / ".maestro/orchestrate/carte/pid").exists(), \
        "un run qui se termine proprement ne laisse pas sa carte derrière lui"


def test_un_run_ne_se_tue_pas_lui_meme(depot: Depot) -> None:
    """Le garde-fou de base : le run courant est exclu du tri, sans quoi il se suiciderait."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "seul",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    resume = (depot.racine / ".maestro/orchestrate/seul/resume.tsv").read_text(encoding="utf-8")
    assert "130\tOK" in resume, "le run est allé jusqu'au bout de son propre plan"


def test_sans_kill_laisse_cohabiter_les_runs(depot: Depot) -> None:
    """L'échappatoire explicite — et elle prévient, parce qu'elle rend le doublon possible."""
    dossier = _run_dir(depot, "20260803-171434", [(1, 131, "-", "haute")], resume=[],
                       sessions=(131,))
    proc = _pilote_factice(depot, dossier)
    try:
        depot.ticket(130, "Ticket à traiter")
        depot.mr("feat/130-ticket-a-traiter", "opened")
        claude = _claude_stub(depot, f"""
            printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
            printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
            exit 0
        """)
        plan = _plan(depot, [(1, 130, "-", "moyenne")])
        r = depot.lance("run.sh", "--plan", plan, "--run-id", "cohabite", "--sans-kill",
                        env={"MAESTRO_CLAUDE_BIN": claude})
        assert r.returncode == 0, r.stdout + r.stderr
        assert proc.poll() is None, "--sans-kill ne tue rien"
        assert "sans-kill" in r.stdout, "le doublon assumé se dit, il ne se subit pas"
    finally:
        proc.kill()


def test_un_run_neuf_arrete_ce_qui_tourne_avant_de_partir(depot: Depot) -> None:
    """Le geste est bien câblé dans un démarrage ordinaire, pas seulement dans `--tuer-les-runs`."""
    dossier = _run_dir(depot, "20260803-171434", [(1, 131, "-", "haute")], resume=[],
                       sessions=(131,))
    proc = _pilote_factice(depot, dossier)
    try:
        depot.ticket(130, "Ticket à traiter")
        depot.mr("feat/130-ticket-a-traiter", "opened")
        claude = _claude_stub(depot, f"""
            printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
            printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
            exit 0
        """)
        plan = _plan(depot, [(1, 130, "-", "moyenne")])
        r = depot.lance("run.sh", "--plan", plan, "--run-id", "neuf",
                        env={"MAESTRO_CLAUDE_BIN": claude})
        assert r.returncode == 0, r.stdout + r.stderr
        assert proc.wait(timeout=30) is not None
        assert "20260803-171434" in r.stdout, "le run arrêté est nommé avant que le nouveau parte"
    finally:
        proc.kill()


def test_un_dry_run_ne_tue_rien(depot: Depot) -> None:
    """`--dry-run` n'exécute rien : il n'a aucune place à faire, et ne doit pas en faire."""
    dossier = _run_dir(depot, "20260803-171434", [(1, 131, "-", "haute")], resume=[],
                       sessions=(131,))
    proc = _pilote_factice(depot, dossier)
    try:
        plan = _plan(depot, [(1, 130, "-", "moyenne")])
        r = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "essai")
        assert r.returncode == 0, r.stderr
        assert proc.poll() is None, "regarder un plan n'arrête pas un run"
    finally:
        proc.kill()


# =====================================================================================
# L'arrêt quand N sessions sont en vol (#291)
# =====================================================================================
#
# Ces tests-là sont dans CE lot et non dans le lot « tests + doc » (#292), pour la raison de #213 :
# vérifier qu'un arrêt arrête ne se simule pas. Ils lancent donc de vrais processus — un pilote, ses
# N sous-shells et, sous chacun, un petit-fils — et les tuent pour de bon.
#
# Ce qu'on observe est un BATTEMENT et non un `kill -0` : un processus tué mais pas encore ramassé
# par son parent reste un zombie, et `kill -0` lui répond « vivant » (c'est tout l'objet de
# `pilote_zombie`). Un fichier qui cesse de grossir, lui, ne ment pas — plus rien ne tourne.
#
# Ce que ces tests NE couvrent pas, faute de pouvoir le faire ailleurs que sous Windows : le
# `taskkill //T //F` par WINPID, seul chemin jusqu'aux `claude.exe` natifs. Ailleurs, `pilote_tue`
# atteint toute la descendance par `kill`, et c'est cette récursion-là — jamais éprouvée au-delà du
# pilote lui-même avant ce lot — que les tests d'ici vérifient.


def _pilote_factice_a_n_sessions(
    depot: Depot, dossier: Path, n: int, duree: int = 120
) -> subprocess.Popen:
    """Un pilote vivant, ses `n` sous-shells, et sous chacun un petit-fils qui bat.

    La forme reproduit celle de `lance_ticket` : le pilote garde l'état, seule la session part dans
    un sous-shell, et c'est SOUS elle que vit le processus long (le `claude.exe` d'un vrai run).
    Trois étages, donc, pour que l'arrêt ait quelque chose de récursif à descendre.
    """
    battements = dossier.parent / "battements"
    battements.mkdir(exist_ok=True)
    script = depot.racine.parent / "bin" / "faux-pilote-n"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'. "{depot.racine}/scripts/orchestrate/pilote.sh"\n'
        'pilote_ecrit "$1"\n'
        'for ((i = 1; i <= $2; i++)); do\n'
        '  (\n'
        '    ( while :; do printf . >>"$4/session-$i"; sleep 0.2; done ) &\n'
        '    wait\n'
        '  ) &\n'
        'done\n'
        'sleep "$3"\n',
        encoding="utf-8",
        newline="\n",
    )
    script.chmod(0o755)
    proc = subprocess.Popen(  # noqa: S603
        [BASH, str(script), str(dossier), str(n), str(duree), str(battements)],
        env=depot.env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # On attend que les N battements aient VRAIMENT commencé : tuer avant qu'ils existent ferait
    # passer le test pour de mauvaises raisons.
    carte = dossier / "pid"
    for _ in range(300):
        if (
            carte.exists()
            and "hote=" in carte.read_text(encoding="utf-8", errors="replace")
            and all((battements / f"session-{i}").exists() for i in range(1, n + 1))
        ):
            return proc
        time.sleep(0.05)
    proc.kill()
    raise AssertionError("le pilote factice n'a pas démarré ses N sessions")


def _bat_encore(battements: Path, n: int) -> bool:
    """Vrai si l'un des N battements grossit encore — donc si quelque chose tourne toujours."""
    avant = [(battements / f"session-{i}").stat().st_size for i in range(1, n + 1)]
    time.sleep(1.5)
    apres = [(battements / f"session-{i}").stat().st_size for i in range(1, n + 1)]
    return avant != apres


def test_l_arret_atteint_les_n_sessions_en_vol_et_leurs_enfants(depot: Depot) -> None:
    """Le cœur de #291 côté arrêt : ce n'est pas le pilote qu'on tue, c'est tout son arbre.

    Avant ce lot, l'arrêt n'avait jamais été éprouvé sur plus d'un descendant — un run séquentiel
    n'en a qu'un. À N, une session laissée derrière soi continue de brûler du quota sans que rien ne
    la rattache plus à un run.
    """
    dossier = _run_dir(depot, "20260803-171434",
                       [(1, 130, "-", "haute"), (2, 131, "-", "haute"), (3, 132, "-", "haute")],
                       resume=[], sessions=(130, 131, 132))
    battements = dossier.parent / "battements"
    proc = _pilote_factice_a_n_sessions(depot, dossier, 3)
    try:
        assert _bat_encore(battements, 3), "les trois sessions doivent battre AVANT l'arrêt"
        r = depot.lance("run.sh", "--tuer-les-runs")
        assert r.returncode == 0, r.stderr
        assert proc.wait(timeout=30) is not None, "le pilote tourne toujours après son arrêt"
        assert not _bat_encore(battements, 3), \
            "une session survivante après l'arrêt, c'est du quota brûlé pour personne"
    finally:
        proc.kill()


def test_l_arret_nomme_tous_les_tickets_qu_il_interrompt(depot: Depot) -> None:
    """N'en nommer qu'un ferait croire qu'un seul worktree garde du travail non commité."""
    dossier = _run_dir(depot, "20260803-171434",
                       [(1, 130, "-", "haute"), (2, 131, "-", "haute"), (3, 132, "-", "haute")],
                       resume=[(130, "OK", 99, 600, "3.50", "-")], sessions=(130, 131, 132))
    proc = _pilote_factice(depot, dossier)
    try:
        r = depot.lance("run.sh", "--tuer-les-runs")
        assert r.returncode == 0, r.stderr
        proc.wait(timeout=30)
        assert "#131" in r.stdout and "#132" in r.stdout, \
            "les deux tickets encore en vol sont nommés, pas seulement le premier"
        assert "#130" not in r.stdout, \
            "un ticket déjà soldé n'est pas en vol : on ne l'interrompt pas"
    finally:
        proc.kill()


def test_un_run_a_n_sessions_reste_reprenable_apres_l_arret(depot: Depot) -> None:
    """L'arrêt est sans sommation mais BORNÉ : le journal reste entier, et tout est rejouable.

    C'est ce qui rend acceptable de tuer N sessions d'un coup — le travail non commité dort dans les
    worktrees, et les témoins de session (les uuid) sont ce qui permettra de les rouvrir.
    """
    dossier = _run_dir(depot, "20260803-171434",
                       [(1, 130, "-", "haute"), (2, 131, "-", "haute"), (3, 132, "-", "haute")],
                       resume=[(130, "OK", 99, 600, "3.50", "-")], sessions=(130, 131, 132))
    proc = _pilote_factice_a_n_sessions(depot, dossier, 2)
    try:
        depot.lance("run.sh", "--tuer-les-runs")
        proc.wait(timeout=30)
        lignes = _reprenables(depot)
        assert [ligne[0] for ligne in lignes] == ["20260803-171434"]
        assert lignes[0][2] == "2", "les deux tickets en vol restent à faire"
        for iid in (131, 132):
            assert (dossier / f"{iid}.session").exists(), \
                f"l'uuid de #{iid} survit : c'est par lui que sa session se rouvrira"
        assert (dossier / "resume.tsv").exists(), "tuer un run ne touche pas à son bilan"
    finally:
        proc.kill()


def test_le_fichier_stop_arrete_un_run_concurrent_sans_couper_ce_qui_est_en_vol(
    depot: Depot,
) -> None:
    """STOP garde à N le sens qu'il avait à 1 : il arrête de LANCER, il ne tue personne.

    Les deux tickets partis ensemble vont donc au bout — c'est ce qui distingue le fichier STOP de
    `--tuer-les-runs`, et ce qui fait qu'il ne coûte aucun travail non commité. Le reste du plan est
    laissé intact pour un prochain run.
    """
    for iid in (130, 131, 132, 133):
        depot.ticket(iid, f"Ticket {iid}")
    run_dir = depot.racine / ".maestro/orchestrate/stop-n"
    stop = depot.racine / ".maestro/orchestrate/STOP"
    # La session n'attend pas une durée mais un ÉVÉNEMENT : le témoin de session du second ticket,
    # posé par le pilote juste avant de le lancer. Sans cela, la première session pourrait poser
    # STOP pendant que le pilote remplit encore son deuxième créneau, et le test dirait « un seul
    # en vol » là où c'est la course qui aurait tranché, pas le code.
    claude = _claude_stub(depot, f"""
        for _ in $(seq 1 200); do
          [ -e "{run_dir}/131.session" ] && break
          sleep 0.05
        done
        touch "{stop}"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "haute"), (2, 131, "-", "haute"),
                         (3, 132, "-", "haute"), (4, 133, "-", "haute")])
    r = depot.lance(
        "run.sh", "--plan", plan, "--run-id", "stop-n", "--concurrence", "2",
        env={"MAESTRO_CLAUDE_BIN": claude},
    )
    # 1 : les deux sessions n'ont rien clos côté GitLab, donc deux ECHEC — ce n'est pas ce qu'on
    # regarde ici, seule compte la LISTE des tickets que le run a pris en main.
    assert r.returncode in (0, 1), r.stdout + r.stderr
    assert "Arrêt demandé" in r.stdout
    traites = [x.split("\t")[0]
               for x in (run_dir / "resume.tsv").read_text(encoding="utf-8").splitlines()
               if not x.startswith("#")]
    assert traites == ["130", "131"], (
        f"les deux en vol vont au bout, et rien de plus n'est lancé — obtenu {traites}"
    )
    assert not (run_dir / "132.session").exists(), "le reste du plan n'a pas été touché"


# =====================================================================================
# La limite d'usage quand N sessions sont en vol (#291)
# =====================================================================================
#
# Le reste de la couverture de ce chantier est au lot « tests + doc » (#292). Celui-ci est ici parce
# qu'il garde la mécanique la plus facile à casser sans que rien ne le montre : deux sessions qui
# attendent chacune dans leur coin ont exactement la même allure à l'écran qu'une attente partagée,
# et la différence ne se voit qu'au moment où la moins bien informée se réveille trop tôt.


def test_une_limite_d_usage_ne_declenche_qu_une_attente_pour_les_n_sessions(
    depot: Depot,
) -> None:
    """Une attente pour le run, chaque session rouverte PAR SON UUID : les deux moitiés du critère.

    Le palier est ramené à quelques secondes : ce qu'on vérifie n'est pas sa durée mais le fait que
    les deux sessions se rangent derrière LE MÊME rendez-vous, puis repartent chacune sur sa propre
    conversation. Sans le rendez-vous, la seconde ouvrirait la sienne et la sortie ne dirait rien de
    différent — d'où l'assertion sur le fichier, et pas seulement sur la prose.
    """
    for iid in (130, 131):
        depot.ticket(iid, f"Ticket {iid}")
        depot.mr(f"feat/{iid}-ticket-{iid}", "opened")
    run_dir = depot.racine / ".maestro/orchestrate/limite-n"
    # Les deux sessions annoncent leur limite EN MÊME TEMPS — chacune attend que l'autre soit
    # arrivée. C'est le cas réel (la fenêtre se referme sur toutes à la fois) et c'est ce qui rend
    # le test insensible à la charge : laquelle des deux ouvre le rendez-vous est une course, mais
    # qu'il n'y en ait qu'UN ne l'est pas — et c'est cela, le critère.
    claude = _claude_stub(depot, f"""
        iid="$(printf '%s\\n' "$@" | grep -oE 'ticket (GitLab )?#[0-9]+' | head -1 |
               grep -oE '[0-9]+$')"
        printf '%s\\n' "$@" >> "$MAESTRO_FIXTURES/args-$iid.txt"
        n=$(( $(cat "$MAESTRO_FIXTURES/n-$iid" 2>/dev/null || echo 0) + 1 ))
        printf '%s' "$n" > "$MAESTRO_FIXTURES/n-$iid"
        if [ "$n" = 1 ]; then
          : > "$MAESTRO_FIXTURES/arrivee-$iid"
          for _ in $(seq 1 300); do
            [ -e "$MAESTRO_FIXTURES/arrivee-130" ] &&
              [ -e "$MAESTRO_FIXTURES/arrivee-131" ] && break
            sleep 0.05
          done
          printf '{{"type":"result","subtype":"error","is_error":true,"result":"rate limited"}}'
          exit 1
        fi
        printf '%s' '{_statut_json("$iid", "En revue")}' > "$MAESTRO_FIXTURES/owner-$iid.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "haute"), (2, 131, "-", "haute")])
    r = depot.lance(
        "run.sh", "--plan", plan, "--run-id", "limite-n", "--concurrence", "2",
        env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "8"},
    )
    assert r.returncode in (0, 1), r.stdout + r.stderr

    rendez_vous = (run_dir / ".limite").read_text(encoding="utf-8").splitlines()
    assert len(rendez_vous) == 1, f"une seule attente pour le run, pas N — {rendez_vous}"
    ouvreur = rendez_vous[0].split("\t")[2]
    assert ouvreur in ("130", "131"), f"le rendez-vous nomme son ouvreur — {rendez_vous}"
    # Une qui ouvre, une qui rejoint : c'est ce couple, et non son ordre, qui dit que l'attente est
    # partagée. Deux annonces, ce serait deux attentes.
    assert r.stdout.count("avant reprise (fin vers") == 1, \
        f"une seule attente doit être annoncée — {r.stdout}"
    assert r.stdout.count(f"rejoint l'attente du run ouverte par #{ouvreur}") == 1, \
        f"l'autre session doit s'y ranger, pas ouvrir la sienne — {r.stdout}"

    # Chaque session est REPRISE, et sur SA conversation : même uuid au deuxième appel, et deux
    # uuid différents d'un ticket à l'autre.
    uuids = {}
    for iid in (130, 131):
        args = (depot.fixtures / f"args-{iid}.txt").read_text(encoding="utf-8").split("\n")
        assert (depot.fixtures / f"n-{iid}").read_text(encoding="utf-8") == "2", \
            f"#{iid} doit avoir été rejouée une fois après l'attente"
        neuf = args[args.index("--session-id") + 1]
        repris = args[args.index("--resume") + 1]
        assert neuf == repris, f"#{iid} doit rouvrir SA session, pas en ouvrir une neuve"
        uuids[iid] = neuf
    assert uuids[130] != uuids[131], "deux sessions, deux conversations"


# =====================================================================================
# Choisir le milestone d'un run neuf — queue.sh --milestones (#204)
# =====================================================================================

def _milestones(depot: Depot) -> list[list[str]]:
    """Les lignes de `queue.sh --milestones`, en-tête « # » ôtée."""
    r = depot.lance("queue.sh", "--milestones")
    assert r.returncode == 0, r.stderr
    return [ligne.split("\t") for ligne in r.stdout.splitlines()
            if ligne and not ligne.startswith("#")]


def test_seuls_les_milestones_actifs_sont_proposes_avec_leur_reste(depot: Depot) -> None:
    depot.milestones([("Phase A", "active", 3, 10), ("Phase B", "active", 0, 4),
                      ("Phase Z", "closed", 8, 8)])
    depot.ticket(501, "A faire 1")
    depot.ticket(502, "A faire 2")
    depot.ticket(503, "Deja en revue", statut="En revue")
    depot.ticket(504, "B faire 1")
    depot.ticket(505, "B faire 2")
    depot.ticket(506, "B en cours", statut="En cours")
    depot.publie()
    depot.milestone_tickets("Phase A", [501, 502, 503])
    depot.milestone_tickets("Phase B", [504, 505, 506])

    lignes = _milestones(depot)
    assert [x[0] for x in lignes] == ["Phase A", "Phase B"], \
        "une phase soldée n'est pas un run à lancer"

    titre, courant, a_faire, ouverts, echeance = lignes[0]
    assert (titre, courant) == ("Phase A", "1"), "la phase courante est marquée, pas devinée"
    assert a_faire == "2", "les « À faire » et libres, pas les ouverts"
    assert ouverts == "7" and echeance == "2026-12-31"

    assert lignes[1][1] == "0", "les autres phases actives sont proposables sans être le défaut"
    assert lignes[1][2] == "2"


def test_un_milestone_dont_les_tickets_sont_deja_pris_n_a_rien_a_traiter(depot: Depot) -> None:
    """Le compte suit le filtre de la boucle (« À faire » ET libre) : proposer un milestone dont
    tout est assigné mènerait à un plan vide, et le choix serait un piège."""
    depot.milestones([("Phase A", "active", 0, 2)])
    depot.ticket(501, "Pris par alice", assigne="alice")
    depot.ticket(502, "Pris par bob", assigne="bob")
    depot.publie()
    depot.milestone_tickets("Phase A", [501, 502])

    lignes = _milestones(depot)
    assert lignes[0][2] == "0", "aucun ticket que la boucle pourrait prendre"
    assert lignes[0][3] == "2", "... alors qu'il reste bien deux tickets ouverts"


def test_le_listing_des_milestones_n_imprime_aucun_plan(depot: Depot) -> None:
    """C'est une sortie de données pour la question du milestone, pas un plan : rien d'autre ne doit
    s'y mêler — et surtout aucun run n'est préparé."""
    depot.milestones([("Phase A", "active", 0, 1)])
    depot.ticket(501, "A faire")
    depot.publie()
    depot.milestone_tickets("Phase A", [501])

    r = depot.lance("queue.sh", "--milestones")
    assert r.returncode == 0, r.stderr
    assert "501" not in r.stdout, "le plan n'est pas calculé ici"
    assert not (depot.racine / ".maestro").exists()


# =====================================================================================
# journal.sh refus — l'agrégat des permission_denials (#235, parent #232)
# =====================================================================================
# §11.7 pose le principe : l'`allow` se complète À PARTIR DES REFUS OBSERVÉS. Il n'était outillé
# que par ticket (`<iid>.resultat.txt`, #180) ; la question qu'on se pose APRÈS un run est l'autre
# — « qu'est-ce qui a été refusé, en tout ? » —, et y répondre demandait de dépouiller 16 JSON à la
# main. Ce que l'agrégat voit et qu'une lecture ticket par ticket rate : le POIDS d'une forme, le
# MAILLON FAIBLE d'une chaîne, et les refus que rien dans le dépôt ne lèvera.


def _refus(*commandes: str, outil: str = "Bash") -> list[dict]:
    champ = {"Bash": "command", "Skill": "skill"}.get(outil, "file_path")
    return [
        {"tool_name": outil, "tool_use_id": f"t{i}", "tool_input": {champ: cmd}}
        for i, cmd in enumerate(commandes)
    ]


def _journal_refus(depot: Depot, run_id: str, sessions: dict[int, list[dict]]) -> Path:
    """Un run déjà terminé, dont chaque session a laissé son `<iid>.json` — la matière de #180."""
    plan = [(rang, iid, "-", "moyenne") for rang, iid in enumerate(sessions, 1)]
    dossier = _run_dir(depot, run_id, plan)
    for iid, refus in sessions.items():
        objet = {
            "type": "result", "subtype": "success", "is_error": False,
            "total_cost_usd": 1.5, "permission_denials": refus,
        }
        (dossier / f"{iid}.json").write_text(
            json.dumps(objet, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8", newline="\n",
        )
    return dossier


def test_refus_compte_chaque_maillon_d_une_chaine_pour_lui_meme(depot: Depot) -> None:
    """Le CLI découpe sur `&&`, `;` et `|` et exige CHAQUE morceau : l'agrégat doit faire pareil.

    Sans ça, `grep … | tail -8` serait rangé sous « grep » alors que c'est le seul mot qui a fait
    tomber la ligne — et on instruirait à côté, en ajoutant `tail`, déjà autorisé.
    """
    _journal_refus(depot, "chaines", {130: _refus(
        'cd "E:/ailleurs" && git status',
        'grep -nE "a|b" journal.log | tail -8',
    )})
    r = depot.lance("journal.sh", "refus", "chaines")

    assert r.returncode == 0, r.stderr
    for verbe in ("cd", "git status", "grep", "tail"):
        assert verbe in r.stdout, f"le maillon « {verbe} » doit être compté pour lui-même"
    assert "en commande composée" in r.stdout
    # Un `grep -E "a|b"` est UNE commande, pas deux : le `|` entre guillemets ne coupe rien.
    assert '  b"' not in r.stdout


def test_refus_pese_une_forme_qu_une_lecture_ticket_par_ticket_raterait(depot: Depot) -> None:
    """Six refus `env` sur cinq sessions ne se voient pas un par un : d'où le total."""
    _journal_refus(depot, "poids", {
        130: _refus("env | grep MAESTRO", "printf 'x'"),
        131: _refus("env | grep LANGFUSE"),
    })
    r = depot.lance("journal.sh", "refus", "poids")

    assert r.returncode == 0, r.stderr
    assert "2 session(s)" in r.stdout
    ligne = next(x for x in r.stdout.splitlines() if x.split()[1:2] == ["env"])
    assert ligne.split()[0] == "2", f"les deux sessions doivent s'additionner : {ligne}"
    assert "#130" in ligne and "#131" in ligne, "la provenance dit OÙ regarder"


def test_refus_releve_les_formes_qu_aucune_regle_ne_matchera(depot: Depot) -> None:
    """Ces trois-là ne s'instruisent pas en élargissant la liste : le geste est dans la FORME.

    Elles sont relevées AVANT que la commande soit aplatie pour le TSV interne — le saut de ligne
    n'y survivrait pas, et c'est justement la forme la plus coûteuse (huit sessions sur seize).
    """
    _journal_refus(depot, "formes", {130: _refus(
        'glab mr create --description "ligne un\nligne deux"',
        'glab mr create --description "$(cat brouillon.md)"',
        "cat > note.md <<'EOF'\ntexte\nEOF",
    )})
    r = depot.lance("journal.sh", "refus", "formes")

    assert r.returncode == 0, r.stderr
    assert "Formes immatchables" in r.stdout
    for forme in ("saut de ligne", "substitution", "heredoc"):
        assert forme in r.stdout, f"la forme « {forme} » doit être nommée"
    assert "l'outil Write" in r.stdout, "le geste de remplacement, pas seulement le constat"


def test_refus_signale_a_part_ce_qu_aucune_regle_ne_levera(depot: Depot) -> None:
    """Écrire sous `.claude/` vient du CLI, pas de la liste (#229) : aucune règle n'y peut rien."""
    _journal_refus(depot, "claude", {130: _refus(
        ".claude/skills/control-tower/SKILL.md", outil="Write",
    )})
    r = depot.lance("journal.sh", "refus", "claude")

    assert r.returncode == 0, r.stderr
    assert "Hors Bash" in r.stdout and "Write" in r.stdout
    assert ".claude/" in r.stdout
    assert "aucune règle ne les lèvera" in r.stdout


def test_refus_agrege_tout_le_journal_ou_un_seul_run(depot: Depot) -> None:
    """Deux portées, une même lecture : `--tous` pour la tendance, un run-id pour le run du jour."""
    _journal_refus(depot, "20260801-100000", {130: _refus("awk '{print}' f")})
    _journal_refus(depot, "20260804-100000", {131: _refus("npx tsc --noEmit")})

    cible = depot.lance("journal.sh", "refus", "20260801-100000")
    assert "1 session(s) · 1 refus" in cible.stdout
    assert "npx" not in cible.stdout

    tous = depot.lance("journal.sh", "refus", "--tous")
    assert tous.returncode == 0, tous.stderr
    assert "2 session(s) · 2 refus" in tous.stdout
    assert "awk" in tous.stdout and "npx" in tous.stdout


def test_refus_sans_argument_prend_le_dernier_run_qui_en_porte(depot: Depot) -> None:
    """Un run tout frais dont aucune session n'a rendu la main masquerait le seul run lisible.

    Les run-id sont horodatés : l'ordre alphabétique EST l'ordre chronologique, sans interroger le
    système de fichiers.
    """
    _journal_refus(depot, "20260801-100000", {130: _refus("awk '{print}' f")})
    _run_dir(depot, "20260805-090000", [(1, 999, "-", "haute")])  # parti, rien rendu

    r = depot.lance("journal.sh", "refus")
    assert r.returncode == 0, r.stderr
    assert "20260801-100000" in r.stdout
    assert "awk" in r.stdout


# --- Le classement en familles (#307) ------------------------------------------------------------
# L'agrégat disait COMBIEN et DE QUOI, jamais POURQUOI — d'où un sujet qui passait pour clos pendant
# que le compte, lui, ne baissait pas : le gisement des trous d'allowlist, #232 l'avait fini. Les
# sept commandes les plus refusées du journal sont TOUTES dans l'`allow` — c'est la CIBLE qui tombe.


def _familles(sortie: str) -> dict[str, int]:
    """Le classement, lu comme une TABLE et jamais par recherche de texte : le rappel de l'ordre de
    décision, juste en dessous, nomme les mêmes familles — un `in sortie` y matcherait toujours."""
    familles = {}
    for ligne in sortie.splitlines():
        trouve = re.match(r"\s+(\d+)\s+(\S.*?)\s+(\d+) %", ligne)
        if trouve:
            familles[trouve.group(2)] = int(trouve.group(1))
    return familles


def _maillons(sortie: str) -> list[str]:
    """La liste « ce qui s'instruit », vide quand aucun maillon n'est vraiment découvert."""
    if "s'instruit" not in sortie:
        return []
    bloc = sortie.split("s'instruit", 1)[1].split("── Par outil", 1)[0]
    return [
        ligne.split(maxsplit=1)[1]
        for ligne in bloc.splitlines()
        if ligne.strip() and ligne.split()[0].isdigit()
    ]


def test_refus_distingue_l_echappee_de_chemin_du_trou_d_allowlist(depot: Depot) -> None:
    """La distinction est tout l'objet du ticket : le geste n'est pas le même des deux côtés.

    `cat`/`head` sont autorisés — une chaîne qui n'échoue que par sa cible ne s'instruit pas dans
    `settings.run.json`, et l'y chercher est ce qui a fait passer le sujet pour clos.
    """
    _journal_refus(depot, "familles", {130: _refus(
        'cat "E:/ailleurs/notes.md" | head -20',      # tout est autorisé : c'est le chemin
        "bash scripts/orchestrate/queue.sh > /tmp/plan.txt",
        "for f in a b; do node $f; done",             # `for` n'est couvert par aucune règle
    )})
    r = depot.lance("journal.sh", "refus", "familles")

    assert r.returncode == 0, r.stderr
    familles = _familles(r.stdout)
    assert familles.get("échappée de chemin") == 2, f"les deux cibles hors worktree : {r.stdout}"
    assert familles.get("trou d'allowlist") == 1, f"le seul maillon découvert : {r.stdout}"
    # Et la liste qui s'instruit ne porte QUE ce maillon-là : y voir « cat » enverrait ajouter une
    # règle qui est déjà là.
    assert _maillons(r.stdout) == ["for"]


def test_refus_ne_prend_pas_un_chemin_absolu_pour_une_regle_manquante(depot: Depot) -> None:
    """Sa forme RELATIVE serait couverte, et aucune règle de préfixe ne bornera jamais un absolu :
    le compter comme un trou enverrait élargir la liste pour rien."""
    _journal_refus(depot, "absolu", {130: _refus(
        '"E:/depot/.venv/Scripts/python.exe" -m pytest',
    )})
    r = depot.lance("journal.sh", "refus", "absolu")

    assert r.returncode == 0, r.stderr
    assert _familles(r.stdout) == {"échappée de chemin": 1}
    assert _maillons(r.stdout) == [], "aucun maillon à instruire — le geste est la forme"


def test_refus_ne_confond_pas_une_url_ni_un_sed_avec_un_chemin(depot: Depot) -> None:
    """Trois faux positifs qui rangeraient des refus ordinaires en échappées : `https://`, `sed
    s/a/b/` et `2>/dev/null`. Le premier ferait basculer toute commande portant une URL."""
    _journal_refus(depot, "faux-positifs", {130: _refus(
        "sed -i 's/avant/apres/' fichier.md",
        "ls apps/web 2>/dev/null; cat .node-version",
    )})
    r = depot.lance("journal.sh", "refus", "faux-positifs")

    assert r.returncode == 0, r.stderr
    assert "échappée de chemin" not in _familles(r.stdout), (
        f"aucune de ces commandes ne sort du répertoire de travail : {r.stdout}"
    )


def test_refus_reconnait_un_refus_voulu_par_une_regle_ask_du_depot(depot: Depot) -> None:
    """`git commit --no-verify` est demandé en confirmation par le dépôt : en autonome, personne ne
    peut l'accorder. Le ranger ailleurs enverrait chercher une règle qui existe déjà et dit non.

    Le matching y est plus large qu'ailleurs, et à dessein : le CLI comprend les OPTIONS, un
    préfixe non — `git commit --no-edit --no-verify` doit tomber sous la même règle.
    """
    _journal_refus(depot, "voulu", {130: _refus(
        "git add x.py; git commit --no-edit --no-verify",
    )})
    r = depot.lance("journal.sh", "refus", "voulu")

    assert r.returncode == 0, r.stderr
    assert _familles(r.stdout) == {"refus voulu (ask/deny)": 1}


def test_refus_ne_prend_pas_une_tete_de_regle_pour_une_option(depot: Depot) -> None:
    """La contrepartie du matching large : seules les OPTIONS flottent, jamais la tête de la règle.

    Sans ça, `git commit -m "clean up"` tomberait sous `Bash(git clean:*)` et un refus ordinaire
    passerait pour voulu.
    """
    _journal_refus(depot, "tete", {130: _refus('git commit -m "clean up"')})
    r = depot.lance("journal.sh", "refus", "tete")

    assert r.returncode == 0, r.stderr
    assert "refus voulu (ask/deny)" not in _familles(r.stdout), r.stdout


def test_refus_lit_les_regles_la_ou_elles_vivent(depot: Depot) -> None:
    """Une copie figée du `allow` se périmerait en silence — le défaut même que #307 corrige.

    Le test le prouve par l'absurde : on retire une règle du fichier du dépôt jetable, et le
    classement doit changer d'avis sur la même commande.
    """
    _journal_refus(depot, "vivantes", {130: _refus("awk '{print}' fichier.txt")})
    avant = _familles(depot.lance("journal.sh", "refus", "vivantes").stdout)
    assert "trou d'allowlist" not in avant, "`awk` est autorisé — rien à instruire"

    chemin = depot.racine / "scripts/orchestrate/settings.run.json"
    reglages = json.loads(chemin.read_text(encoding="utf-8"))
    reglages["permissions"]["allow"].remove("Bash(awk:*)")
    chemin.write_text(json.dumps(reglages, ensure_ascii=False, indent=2), encoding="utf-8")

    r = depot.lance("journal.sh", "refus", "vivantes")
    assert _familles(r.stdout) == {"trou d'allowlist": 1}, (
        f"le classement doit suivre le fichier, pas une copie : {r.stdout}"
    )
    assert _maillons(r.stdout) == ["awk"]


def test_refus_range_chaque_refus_dans_une_seule_famille(depot: Depot) -> None:
    """La somme des familles EST le total : sans ça le classement serait un comptage de plus.

    Le premier cas porte les deux causes à la fois — un maillon découvert ET une cible hors du
    worktree —, et l'ordre de décision tranche en faveur du trou d'allowlist.
    """
    _journal_refus(depot, "partition", {130: _refus(
        "for f in a b; do cat /tmp/$f; done",
        'cd "E:/ailleurs" && git status',
        'glab mr create --description "un\ndeux"',
    ) + _refus(".claude/settings.json", outil="Write")})
    r = depot.lance("journal.sh", "refus", "partition")

    assert r.returncode == 0, r.stderr
    assert "1 session(s) · 4 refus" in r.stdout
    familles = _familles(r.stdout)
    assert sum(familles.values()) == 4, f"un refus, une famille — {r.stdout}"
    assert familles == {
        "trou d'allowlist": 1,
        "échappée de chemin": 1,
        "forme immatchable": 1,
        "blocage dur .claude/": 1,
    }, r.stdout


def test_refus_ne_touche_a_rien_et_dit_franchement_qu_il_n_a_rien_trouve(depot: Depot) -> None:
    """Lecture seule : `refus` sert à décider, il ne décide pas — et un run propre le dit."""
    dossier = _journal_refus(depot, "propre", {130: []})
    fichiers = [p for p in sorted(dossier.rglob("*")) if p.is_file()]
    empreinte = {p: p.stat().st_mtime_ns for p in fichiers}

    r = depot.lance("journal.sh", "refus", "propre")

    assert r.returncode == 0, r.stderr
    assert "Aucun refus de permission" in r.stdout
    assert {p: p.stat().st_mtime_ns for p in fichiers} == empreinte


def test_refus_nomme_les_runs_presents_quand_le_run_id_est_inconnu(depot: Depot) -> None:
    """Une faute de frappe ne doit pas rendre un vide, qu'on lirait « rien à faire »."""
    _journal_refus(depot, "20260801-100000", {130: _refus("awk '{print}' f")})
    r = depot.lance("journal.sh", "refus", "20260801-999999")
    assert r.returncode == 2
    assert "run inconnu" in r.stderr
    assert "20260801-100000" in r.stderr


def test_la_console_renvoie_vers_l_agregat_en_fin_de_run(depot: Depot) -> None:
    """Le seul moment où quelqu'un lit un run est celui-là : c'est là que l'invitation porte."""
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _claude_stub(depot, """
        printf '{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}\\n'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "invite",
                    env={"MAESTRO_CLAUDE_BIN": claude})

    assert "journal.sh refus invite" in r.stdout, (
        "sans cette ligne, la boucle de retour de §11.7 ne part que si on y pense — "
        "et onze runs ont montré que non"
    )


# =====================================================================================
# `main` remise à niveau au démarrage d'un run (#283)
# =====================================================================================
#
# Un run est ce qui fait vieillir le plus vite la ref LOCALE `refs/heads/main` du clone principal :
# il ouvre N MR destinées à être mergées, et plus personne ne repasse par `main` depuis #181. Elle
# n'avançait jusqu'ici qu'à l'intérieur d'une session (`worktree.sh ensure`, donc /ticket-start) —
# donc pas du tout quand le run part sur un plan vide, saute tous ses tickets ou échoue avant le
# premier. Le code produit, lui, n'a jamais été en cause : chaque worktree part d'`origin/main`.
#
# Ces tests portent donc sur la ref locale, et jamais sur du réseau : le dépôt jetable n'a aucun
# distant, `refs/remotes/origin/main` y est une simple référence posée à la main (comme pour
# status.sh), et le `git fetch` de `sync-main` y échoue en silence — exactement le cas « hors
# ligne » que le helper sait traiter.


def _git(depot: Depot, *args: str) -> str:
    assert GIT is not None
    return subprocess.run(  # noqa: S603
        [GIT, *args], cwd=str(depot.racine), check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_git_sur_main(depot: Depot) -> None:
    """Le dépôt jetable en CLONE PRINCIPAL : posé sur `main`, propre, avec un `origin/main` local.

    C'est la situation réelle d'un run depuis #181 (le clone principal ne change plus de branche),
    et celle qui met `sync-main` sur son chemin le plus délicat : `main` étant EMPRUNTÉE par un
    répertoire de travail, la ref ne se pose pas — elle s'avance par un `merge --ff-only` dans ce
    répertoire-là. D'où le `.gitignore` : le plan et le journal du run salissent l'arbre, et un
    arbre sale fait (à juste titre) renoncer le helper.
    """
    _git(depot, "init", "--quiet", "--initial-branch=main")
    _git(depot, "config", "user.email", "test@maestro.invalid")
    _git(depot, "config", "user.name", "Maestro Test")
    (depot.racine / ".gitignore").write_text(".maestro/\nplan.tsv\n", encoding="utf-8",
                                             newline="\n")
    _git(depot, "add", "-A")
    _git(depot, "-c", "core.hooksPath=", "commit", "--quiet", "-m", "chore: depot jetable")
    _git(depot, "update-ref", "refs/remotes/origin/main", "HEAD")


def _commit(depot: Depot, fichier: str, message: str) -> str:
    (depot.racine / fichier).write_text(message, encoding="utf-8", newline="\n")
    _git(depot, "add", fichier)
    _git(depot, "-c", "core.hooksPath=", "commit", "--quiet", "-m", message)
    return _git(depot, "rev-parse", "HEAD")


def _origin_main_avance(depot: Depot) -> str:
    """Un commit de plus sur `origin/main`, et rien sur `main` : le retard type d'après un merge."""
    _git(depot, "checkout", "--quiet", "-b", "amont")
    sha = _commit(depot, "livre.txt", "feat: un lot merge pendant la nuit")
    _git(depot, "update-ref", "refs/remotes/origin/main", sha)
    _git(depot, "checkout", "--quiet", "main")
    _git(depot, "branch", "--quiet", "-D", "amont")
    return sha


def _run_d_un_ticket(depot: Depot, run_id: str, **env: str) -> subprocess.CompletedProcess:
    """Un run d'un ticket, livré (MR ouverte + « En revue ») — le décor de ces tests."""
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    return depot.lance("run.sh", "--plan", plan, "--run-id", run_id,
                       env={"MAESTRO_CLAUDE_BIN": claude, **env})


@besoin_git
def test_un_run_remet_main_a_niveau_avant_son_premier_ticket(depot: Depot) -> None:
    _init_git_sur_main(depot)
    livre = _origin_main_avance(depot)

    r = _run_d_un_ticket(depot, "amont")

    assert r.returncode == 0, r.stdout + r.stderr
    assert _git(depot, "rev-parse", "refs/heads/main") == livre
    # La ref ne suffit pas : `main` est empruntée par ce répertoire, donc son ARBRE doit avoir suivi
    # — sans quoi tout le delta apparaîtrait en « supprimé » au prochain git status.
    assert (depot.racine / "livre.txt").exists(), "le répertoire de travail a suivi la ref"
    assert "main mis à jour" in r.stdout, "le run le dit, il ne le fait pas en douce"


@besoin_git
def test_main_deja_a_jour_ne_dit_rien(depot: Depot) -> None:
    """Le cas de loin le plus fréquent : une ligne à chaque run n'apprendrait rien à personne."""
    _init_git_sur_main(depot)
    r = _run_d_un_ticket(depot, "ajour")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "sync-main" not in r.stdout and "main mis à jour" not in r.stdout


@besoin_git
def test_le_dry_run_ne_touche_pas_a_main_mais_annonce_l_etape(depot: Depot) -> None:
    _init_git_sur_main(depot)
    avant = _git(depot, "rev-parse", "refs/heads/main")
    _origin_main_avance(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])

    r = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "sec")

    assert r.returncode == 0, r.stderr
    assert _git(depot, "rev-parse", "refs/heads/main") == avant, (
        "« rien n'a été lancé » vaut aussi pour main"
    )
    assert "sync-main" in r.stdout, "…mais le dry-run dit ce qu'un vrai run ferait"


@besoin_git
def test_une_main_divergente_est_signalee_sans_empecher_le_run(depot: Depot) -> None:
    """`sync-main` s'abstient plutôt que de forcer (#205) — et son abstention n'annule pas un run.

    Un `main` local divergent porte un commit que personne n'a poussé : l'écraser serait une perte
    de données. Mais refuser de traiter le backlog pour autant le serait tout autant, à l'échelle
    d'une nuit entière.
    """
    _init_git_sur_main(depot)
    _origin_main_avance(depot)
    local = _commit(depot, "local.txt", "chore: commit local jamais pousse")

    r = _run_d_un_ticket(depot, "diverge")

    assert r.returncode == 0, "le run a traité son ticket malgré l'abstention"
    assert _git(depot, "rev-parse", "refs/heads/main") == local, "rien n'a été écrasé"
    assert "divergé" in r.stderr, "l'abstention est relayée, pas avalée"


@besoin_git
def test_maestro_sync_main_a_zero_eteint_l_etape(depot: Depot) -> None:
    """Même interrupteur que /ticket-start : un poste peut vouloir garder la main sur sa `main`."""
    _init_git_sur_main(depot)
    avant = _git(depot, "rev-parse", "refs/heads/main")
    _origin_main_avance(depot)

    r = _run_d_un_ticket(depot, "eteint", MAESTRO_SYNC_MAIN="0")

    assert r.returncode == 0, r.stdout + r.stderr
    assert _git(depot, "rev-parse", "refs/heads/main") == avant
    assert "main mis à jour" not in r.stdout


# =====================================================================================
# L'orchestration concurrente — la couverture des lots 1 à 4 (#292, parent #287)
# =====================================================================================
#
# Lot final du chantier : il ne pouvait s'écrire qu'une fois #288 à #291 livrés. Deux morceaux de la
# couverture sont restés dans leur lot, pour la seule raison qui vaille — ils ne se simulent pas :
# l'arrêt de N sessions (#291, de vrais processus qu'on tue) et l'attente partagée d'une limite
# d'usage (#291, deux sessions qui doivent se ranger derrière le même rendez-vous). Tout le reste
# est ici.
#
# Le décor est celui de tout ce fichier — ni réseau, ni quota, ni écriture GitLab — avec une
# contrainte de plus, propre à la concurrence : **ce qui doit être simultané l'est par une BARRIÈRE,
# jamais par un `sleep`**. Chaque session bouchon signale son arrivée puis attend celle des autres.
# Sans cela, « deux tickets en vol » serait une course que la charge de la machine tranche, et le
# test dirait tantôt le code, tantôt l'ordonnancement du système.


def _plan_groupes(depot: Depot, lignes: list[tuple[int, int, str, str, str]]) -> str:
    """Un plan figé dont on choisit le GROUPE de chaque ligne — ce que `_plan` ne permet pas.

    `_plan` dérive le groupe du rang (« <parent>.<rang> »), ce qui donne à chaque lot le sien et
    rend tout parent séquentiel : parfait pour les tests d'avant #288, inutilisable pour éprouver
    l'indépendance. Ici la colonne est posée à la main, exactement comme `queue.sh` la calcule.
    """
    chemin = depot.racine / "plan-groupes.tsv"
    chemin.write_text(
        "# rang\tiid\tparent\tprio\tgroupe\ttitre\n"
        + "".join(f"{rang}\t{iid}\t{parent}\t{prio}\t{groupe}\tTicket {iid}\n"
                  for rang, iid, parent, prio, groupe in lignes),
        encoding="utf-8",
        newline="\n",
    )
    return str(chemin)


def _stub_barriere(depot: Depot, iids: tuple[int, ...], *, apres: str = "") -> str:
    """Un bouchon `claude` qui livre son ticket, mais pas avant que TOUS soient arrivés.

    C'est ce qui rend « N en vol en même temps » observable sans dépendre d'un `sleep` : la première
    session ne peut pas se solder avant que la dernière soit partie, donc le pilote a forcément eu
    ses N créneaux occupés. Le bouchon note aussi son passage (`vus.txt`) et le nombre maximal
    d'arrivées simultanées (`pic.txt`), les deux mesures dont les tests d'ordonnancement se servent.
    """
    attente = " ".join(f'[ -e "$MAESTRO_FIXTURES/arrivee-{i}" ] &&' for i in iids)
    gabarit = _statut_json("$iid", "En revue")
    # « ticket GitLab #N » au premier tour, « le ticket #N » à la reprise : les deux formes
    # comptent, sans quoi une session rouverte n'écrirait son verdict sous aucun nom (et le test
    # dirait, à tort, que la reprise n'a rien livré).
    return _claude_stub(depot, f"""
        iid="$(printf '%s\\n' "$@" | grep -oE 'ticket (GitLab )?#[0-9]+' | head -1 |
               grep -oE '[0-9]+$')"
        printf '%s\\n' "$iid" >> "$MAESTRO_FIXTURES/vus.txt"
        : > "$MAESTRO_FIXTURES/arrivee-$iid"
        # Le pic de simultanéité, mesuré par les sessions elles-mêmes : un compteur incrémenté à
        # l'entrée, décrémenté à la sortie, dont on garde le maximum. C'est la seule façon de
        # constater qu'on N'A JAMAIS eu deux tickets liés en vol — une lecture d'après coup ne
        # distingue pas « jamais ensemble » de « ensemble mais vite ».
        n=$(( $(cat "$MAESTRO_FIXTURES/vol" 2>/dev/null || echo 0) + 1 ))
        printf '%s' "$n" > "$MAESTRO_FIXTURES/vol"
        [ "$n" -gt "$(cat "$MAESTRO_FIXTURES/pic.txt" 2>/dev/null || echo 0)" ] &&
          printf '%s' "$n" > "$MAESTRO_FIXTURES/pic.txt"
        for _ in $(seq 1 300); do
          {attente} break
          sleep 0.05
        done
        {apres}
        printf '%s' '{gabarit}' > "$MAESTRO_FIXTURES/owner-$iid.json"
        printf '%s' "$(( $(cat "$MAESTRO_FIXTURES/vol") - 1 ))" > "$MAESTRO_FIXTURES/vol"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":2}}\\n'
        exit 0
    """)


def _livrables(depot: Depot, iids: tuple[int, ...]) -> None:
    """Déclare des tickets libres dont la MR est déjà ouverte — de quoi rendre un verdict « OK »."""
    for iid in iids:
        depot.ticket(iid, f"Ticket {iid}")
        depot.mr(f"feat/{iid}-ticket-{iid}", "opened")


def _resume(run_dir: Path) -> list[list[str]]:
    return [ligne.split("\t")
            for ligne in (run_dir / "resume.tsv").read_text(encoding="utf-8").splitlines()
            if ligne and not ligne.startswith("#")]


# --- Lot 1 : le plan déclare ce qui est indépendant (#288) ---------------------------------------


def _parent_a_vagues(depot: Depot, marques: list[bool]) -> None:
    """Un parent dont les lots portent (ou non) le marqueur « (parallèle) », dans cet ordre."""
    lots = [(501 + i, f"Lot {i + 1}", p) for i, p in enumerate(marques)]
    depot.milestone("Phase X")
    depot.ticket(500, "Parent de suivi", lots=lots)
    for iid, titre, _ in lots:
        depot.ticket(iid, titre, parent=500)
    depot.publie()


def _groupes_du_plan(sortie: str) -> dict[str, str]:
    return {ligne[1]: ligne[4] for ligne in _lignes_du_plan(sortie)}


def test_une_suite_de_lots_marques_forme_une_seule_vague(depot: Depot) -> None:
    """Le cœur de #288 : le marqueur de la checklist cesse d'être jeté après le tri.

    Deux lots marqués qui se suivent tombent dans la MÊME vague, donc dans le même groupe — c'est
    exactement ce que le run pourra mener de front. Le lot non marqué qui les précède et celui qui
    les suit sont chacun leur propre barrière.
    """
    _parent_a_vagues(depot, [False, True, True, False])
    groupes = _groupes_du_plan(depot.lance("queue.sh").stdout)
    assert groupes["502"] == groupes["503"], "deux lots marqués consécutifs partent ensemble"
    assert len({groupes["501"], groupes["502"], groupes["504"]}) == 3, (
        "un lot non marqué est une barrière : ni avec ce qui précède, ni avec ce qui suit"
    )


def test_un_seul_lot_marque_dans_une_chaine_reste_seul_dans_sa_vague(depot: Depot) -> None:
    """Le cas que la règle du parent, prise à la lettre, rendrait faux.

    « Deux lots du même parent tous deux marqués » suppose DEUX marqués. Un seul lot marqué au
    milieu de lots qui ne le sont pas n'est indépendant de personne : il forme sa propre vague, et
    le run reste séquentiel. Se tromper ici lancerait un lot par-dessus un prédécesseur non terminé.
    """
    _parent_a_vagues(depot, [False, True, False])
    groupes = _groupes_du_plan(depot.lance("queue.sh").stdout)
    assert len(set(groupes.values())) == 3, f"trois vagues distinctes attendues — {groupes}"


def test_les_tickets_hors_lot_partagent_le_groupe_neutre(depot: Depot) -> None:
    """L'autre moitié de la règle : `parent` ne les départage pas (ils portent tous « - »), c'est
    leur groupe commun qui les rend indépendants entre eux."""
    depot.milestone("Phase X")
    for iid in (601, 602, 603):
        depot.ticket(iid, f"Isolé {iid}")
    depot.publie()
    groupes = _groupes_du_plan(depot.lance("queue.sh").stdout)
    assert set(groupes.values()) == {"-"}, f"un seul groupe pour tout le hors-lot — {groupes}"


def test_la_vague_se_compte_sur_toute_la_checklist_lots_livres_compris(depot: Depot) -> None:
    """Un lot déjà livré ne disparaît pas de la chaîne : il continue de faire barrière.

    Sans cela le groupe d'un lot dépendrait de ce qui reste à faire au moment du calcul — deux runs
    successifs sur le même parent ne diraient pas la même chose, et le second pourrait paralléliser
    ce que le premier tenait pour séquentiel.
    """
    lots = [(501, "Lot 1", False), (502, "Lot 2", True), (503, "Lot 3", True)]
    depot.milestone("Phase X")
    depot.ticket(500, "Parent de suivi", lots=lots)
    depot.ticket(501, "Lot 1", parent=500, statut="Terminé")
    depot.ticket(502, "Lot 2", parent=500)
    depot.ticket(503, "Lot 3", parent=500)
    depot.publie()
    groupes = _groupes_du_plan(depot.lance("queue.sh").stdout)
    assert "501" not in groupes, "le lot livré n'est plus à traiter"
    assert groupes["502"] == groupes["503"] == "500.2", (
        f"la vague reste comptée depuis le premier lot de la checklist — {groupes}"
    )


def test_le_check_rend_les_groupes_lisibles(depot: Depot) -> None:
    """Une colonne de plus dans le plan ne dit pas d'elle-même ce qu'elle a conclu."""
    _parent_a_vagues(depot, [False, True, True])
    r = depot.lance("queue.sh", "--check")
    assert "groupes de dépendance" in r.stderr
    assert "(parallélisables)" in r.stderr, "un groupe à plusieurs membres est nommé comme tel"
    assert "#502, #503" in r.stderr, "et ses membres listés dans l'ordre du plan"


# --- Lot 2 : l'ordonnanceur (#289) ----------------------------------------------------------------


def test_deux_tickets_independants_partent_vraiment_ensemble(depot: Depot) -> None:
    """Le critère central de #289, mesuré par les sessions elles-mêmes.

    La barrière ne peut se lever que si les deux sessions sont vivantes en même temps : un run
    séquentiel s'y bloquerait jusqu'au timeout du bouchon. Le pic mesuré vaut donc 2, et un pic de 1
    voudrait dire que la concurrence n'a pas eu lieu — pas qu'elle est passée inaperçue.
    """
    _livrables(depot, (130, 131))
    plan = _plan_groupes(depot, [(1, 130, "-", "haute", "-"), (2, 131, "-", "haute", "-")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "duo", "--concurrence", "2",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, (130, 131))})
    assert r.returncode == 0, r.stdout + r.stderr
    assert (depot.fixtures / "pic.txt").read_text(encoding="utf-8") == "2", (
        "les deux sessions doivent avoir été en vol au même instant"
    )


def test_jamais_deux_lots_du_meme_parent_hors_vague_en_vol(depot: Depot) -> None:
    """La garde de l'ordonnanceur : le plan a déclaré ces deux lots dépendants, `--concurrence 2` ne
    les rend pas indépendants pour autant.

    Le bouchon n'attend personne — il livre tout de suite —, sinon un run correct se bloquerait sur
    sa propre barrière. Ce qu'on lit est le pic de simultanéité : il doit rester à 1.
    """
    _livrables(depot, (130, 131))
    plan = _plan_groupes(depot, [(1, 130, "500", "haute", "500.1"),
                                 (2, 131, "500", "haute", "500.2")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "barriere", "--concurrence", "2",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, ())})
    assert r.returncode == 0, r.stdout + r.stderr
    assert (depot.fixtures / "pic.txt").read_text(encoding="utf-8") == "1", (
        "deux vagues d'un même parent ne partent jamais ensemble, quelle que soit la concurrence"
    )
    assert [ligne[0] for ligne in _resume(depot.racine / ".maestro/orchestrate/barriere")] == \
        ["130", "131"], "et l'ordre du plan est respecté"


def test_un_creneau_libere_va_au_prochain_ELIGIBLE_et_non_au_suivant(depot: Depot) -> None:
    """Le balayage complet du plan, et non la ligne d'après.

    Le plan est : un lot (500.1), son successeur bloqué (500.2), puis un ticket isolé. Avec deux
    créneaux, le second doit aller à l'ISOLÉ — la ligne suivante, elle, est barrée. Un ordonnanceur
    qui se contenterait de « la prochaine ligne » laisserait un créneau vide tout le run.
    """
    _livrables(depot, (130, 131, 132))
    plan = _plan_groupes(depot, [(1, 130, "500", "haute", "500.1"),
                                 (2, 131, "500", "haute", "500.2"),
                                 (3, 132, "-", "haute", "-")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "eligible", "--concurrence", "2",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, (130, 132))})
    assert r.returncode == 0, r.stdout + r.stderr
    vus = (depot.fixtures / "vus.txt").read_text(encoding="utf-8").split()
    assert vus[:2] == ["130", "132"] or vus[:2] == ["132", "130"], (
        f"le second créneau saute le lot barré pour prendre l'isolé — vu {vus}"
    )
    assert vus[2] == "131", "et le lot barré ne part qu'une fois son créneau libéré"


def test_le_bilan_n_a_aucune_ligne_tronquee_sous_n_verdicts(depot: Depot) -> None:
    """`resume.tsv` est écrit par le PILOTE seul — c'est ce qui règle la question par construction.

    Le test ne vérifie pas l'atomicité d'un `printf >>` (elle dépend de la plateforme, MSYS émulant
    O_APPEND) : il vérifie l'invariant qui la rend inutile — une ligne par ticket, six colonnes
    chacune, aucun iid en double, même quand quatre verdicts tombent en même temps.
    """
    iids = (130, 131, 132, 133)
    _livrables(depot, iids)
    plan = _plan_groupes(depot, [(r, i, "-", "haute", "-") for r, i in enumerate(iids, 1)])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "bilan", "--concurrence", "4",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, iids)})
    assert r.returncode == 0, r.stdout + r.stderr
    lignes = _resume(depot.racine / ".maestro/orchestrate/bilan")
    assert [len(ligne) for ligne in lignes] == [6] * 4, f"six colonnes par ligne — {lignes}"
    assert sorted(ligne[0] for ligne in lignes) == [str(i) for i in iids]
    assert {ligne[1] for ligne in lignes} == {"OK"}


def test_max_borne_les_tickets_tentes_meme_a_plusieurs_creneaux(depot: Depot) -> None:
    """`--max` compte les tickets TENTÉS, et cela ne change pas parce qu'ils partent par deux.

    Le plafond est vérifié avant CHAQUE lancement, pas une fois par tour de boucle : sans quoi un
    run à quatre créneaux dépasserait son plafond de trois tickets.
    """
    iids = (130, 131, 132, 133)
    _livrables(depot, iids)
    plan = _plan_groupes(depot, [(r, i, "-", "haute", "-") for r, i in enumerate(iids, 1)])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "max-n", "--concurrence", "4",
                    "--max", "2", env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, (130, 131))})
    assert r.returncode == 0, r.stdout + r.stderr
    lignes = _resume(depot.racine / ".maestro/orchestrate/max-n")
    assert sorted(ligne[0] for ligne in lignes) == ["130", "131"], (
        f"deux tickets tentés, pas un de plus — {lignes}"
    )
    assert "Plafond --max 2 atteint" in r.stdout


def test_la_cascade_d_echec_saute_ce_qui_n_est_pas_parti_et_laisse_finir_ce_qui_l_est(
    depot: Depot,
) -> None:
    """La cascade se décide à la FIN d'un ticket, plus à son tour de boucle (#289).

    Deux lots de la même vague partent ensemble ; le premier échoue. Le second est déjà en vol : le
    plan l'avait déclaré indépendant, on ne le rappelle pas. Le troisième, d'une vague suivante,
    n'est pas parti : il est sauté au moment de le lancer.
    """
    _livrables(depot, (131, 132))
    depot.ticket(130, "Ticket 130")  # sans MR : la session ne clôt rien, verdict ECHEC
    plan = _plan_groupes(depot, [(1, 130, "500", "haute", "500.1"),
                                 (2, 131, "500", "haute", "500.1"),
                                 (3, 132, "500", "haute", "500.2")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "cascade", "--concurrence", "2",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, (130, 131))})
    assert r.returncode in (0, 1), r.stdout + r.stderr
    verdicts = {ligne[0]: ligne[1]
                for ligne in _resume(depot.racine / ".maestro/orchestrate/cascade")}
    assert verdicts["130"] == "ECHEC"
    assert verdicts["131"] == "OK", "un lot déjà en vol n'est pas rappelé"
    assert verdicts["132"] == "SAUTE", "un lot pas encore parti l'est"
    assert "lot précédent de #500 a échoué" in r.stdout


def test_sans_l_option_le_run_reste_sequentiel_au_bit_pres(depot: Depot) -> None:
    """Le défaut est 1, et c'est ce qui rend tout le chantier mergeable : deux tickets hors lot sont
    indépendants par le plan, et pourtant rien ne part à deux."""
    _livrables(depot, (130, 131))
    plan = _plan_groupes(depot, [(1, 130, "-", "haute", "-"), (2, 131, "-", "haute", "-")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "seq",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, ())})
    assert r.returncode == 0, r.stdout + r.stderr
    assert (depot.fixtures / "pic.txt").read_text(encoding="utf-8") == "1"
    assert "tickets en vol" not in r.stdout, "aucun régime particulier à annoncer"


def test_une_concurrence_illisible_est_refusee_avant_le_premier_ticket(depot: Depot) -> None:
    """Même raison que l'effort et le budget : un réglage qu'on ne comprend pas ne doit pas se
    découvrir au premier ticket. Et `0` n'y vaut pas « pas de limite » — ce serait zéro créneau."""
    _livrables(depot, (130,))
    plan = _plan_groupes(depot, [(1, 130, "-", "haute", "-")])
    for valeur in ("0", "deux", "-1"):
        r = depot.lance("run.sh", "--plan", plan, "--run-id", "refus", "--concurrence", valeur,
                        env={"MAESTRO_CLAUDE_BIN": _claude_stub(depot, "exit 1\n")})
        assert r.returncode == 2, f"« {valeur} » aurait dû être refusé — {r.stdout}{r.stderr}"
        assert "concurrence invalide" in r.stderr
    assert not (depot.racine / ".maestro/orchestrate/refus").exists(), "rien n'a été entamé"


def test_un_plan_d_avant_la_colonne_groupe_retombe_en_sequentiel_en_le_disant(
    depot: Depot,
) -> None:
    """Un plan à cinq colonnes, rejoué par `--resume` : rien n'y dit ce qui est indépendant.

    Deviner serait pire que se taire — le run retombe à un créneau et l'annonce, plutôt que de
    paralléliser sur une colonne qu'il aurait lue de travers.
    """
    _livrables(depot, (130, 131))
    chemin = depot.racine / "plan-ancien.tsv"
    chemin.write_text(
        "# rang\tiid\tparent\tprio\ttitre\n1\t130\t-\thaute\tTicket 130\n"
        "2\t131\t-\thaute\tTicket 131\n",
        encoding="utf-8",
        newline="\n",
    )
    r = depot.lance("run.sh", "--plan", str(chemin), "--run-id", "ancien", "--concurrence", "2",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, ())})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "antérieur à la colonne « groupe »" in r.stdout
    assert (depot.fixtures / "pic.txt").read_text(encoding="utf-8") == "1"


# --- Lot 3 : la vue rend N tickets en vol, et c'est le pilote qui dessine (#290) ------------------

REPOSITIONNEMENT = re.compile(r"\x1b\[(\d+)F")


def _frames(vue: str) -> list[str]:
    """Découpe la console aux repositionnements et rend les morceaux écrits entre eux.

    Un « ESC[<n>F » annonce de combien de rangées on remonte pour redessiner — donc la hauteur de la
    frame PRÉCÉDENTE, moins un (le curseur reste sur sa dernière ligne, #284). C'est cette relation
    entre l'annonce et ce qui a réellement été écrit que les tests d'ici vérifient : fausse, le bloc
    se dédouble ou se mange.
    """
    return REPOSITIONNEMENT.split(vue)[::2]


def _hauteur_de_frame(morceau: str) -> int:
    """Le nombre de rangées qu'une frame a écrites, compté sur « ESC[K ».

    Chaque ligne du bloc se termine par « efface jusqu'au bout » — et rien d'autre n'en porte : ni
    l'en-tête d'un ticket, ni un verdict, ni les lignes permanentes qu'un effacement a laissé passer
    dans le même morceau. Compter les sauts de ligne les compterait avec la frame, et le morceau qui
    ouvre la console (bannière du run comprise) paraîtrait trois rangées trop haut.
    """
    return morceau.count("\x1b[K")


def _hauteurs_annoncees_et_reelles(vue: str) -> list[tuple[int, int]]:
    """(hauteur annoncée par un repositionnement, rangées écrites par la frame d'avant)."""
    morceaux = REPOSITIONNEMENT.split(vue)
    return [(int(morceaux[i]), _hauteur_de_frame(morceaux[i - 1]))
            for i in range(1, len(morceaux) - 1, 2)]


def _ecritures_hors_bloc(vue: str) -> list[str]:
    """Ce qui a été écrit sur l'écran APRÈS un bloc sans l'avoir retiré — la faute qui dédouble.

    Tout ce que la vue écrit se termine par « ESC[J » : le pied d'une frame comme un effacement. Un
    morceau qui précède un repositionnement et finit autrement porte donc du texte tombé sous un
    bloc resté affiché — et le repositionnement qui suit, compté sur la hauteur du bloc seul,
    remontera trop peu. La hauteur annoncée, elle, reste juste : c'est pourquoi elle ne suffit pas à
    voir ce défaut-là.
    """
    morceaux = REPOSITIONNEMENT.split(vue)
    return [morceaux[i - 1] for i in range(1, len(morceaux) - 1, 2)
            if not morceaux[i - 1].endswith("\x1b[J")]


def _run_a_trois_en_vol(depot: Depot, run_id: str) -> tuple[subprocess.CompletedProcess, Path]:
    """Un run de quatre tickets dont trois sont en vol au même instant, console dans un fichier."""
    iids = (130, 131, 132, 133)
    _livrables(depot, iids)
    console = _console(depot)
    plan = _plan_groupes(depot, [(r, i, "-", "haute", "-") for r, i in enumerate(iids, 1)])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", run_id, "--concurrence", "3",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, (130, 131, 132)),
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    return r, console


def test_le_bloc_ne_se_dedouble_pas_quand_sa_hauteur_varie(depot: Depot) -> None:
    """L'invariant qui tient tout le reste : ce qu'une frame annonce remonter est ce que la
    précédente a écrit.

    À un ticket la hauteur était constante et l'erreur impossible. À N elle VARIE d'une frame à
    l'autre — un ticket qui se solde rend sa ligne d'action —, et une annonce fausse d'une seule
    rangée laisse une copie du bloc dans l'historique à chaque redessin, cinq fois par seconde.
    """
    r, console = _run_a_trois_en_vol(depot, "hauteur")
    assert r.returncode == 0, r.stdout + r.stderr
    vue = console.read_text(encoding="utf-8", errors="replace")
    paires = _hauteurs_annoncees_et_reelles(vue)
    assert paires, "aucune frame n'a été redessinée — le test ne prouve rien"
    assert all(annonce == reel - 1 for annonce, reel in paires), (
        f"une frame remonte d'autant de rangées qu'elle en a écrit, moins une — {paires}"
    )
    assert len({reel for _, reel in paires}) > 1, (
        f"la hauteur doit VARIER pendant ce run, sinon l'invariant n'est pas mis à l'épreuve — "
        f"{paires}"
    )
    # L'autre moitié, que la hauteur seule ne voit pas : rien ne doit s'écrire SOUS un bloc resté
    # affiché — les verdicts des N sessions passent par la file du pilote, qui retire le bloc
    # d'abord.
    assert not _ecritures_hors_bloc(vue), "une ligne a été écrite par-dessus le bloc"


def test_une_frame_donne_une_ligne_d_action_a_chaque_ticket_en_vol(depot: Depot) -> None:
    """Le bloc à N : une ligne par entrée du plan, une de plus par ticket en vol, une pour le pied.

    N'en montrer qu'un serait pire que rien — les autres tiennent un worktree et une session sans
    que rien ne le dise.
    """
    r, console = _run_a_trois_en_vol(depot, "trois-lignes")
    assert r.returncode == 0, r.stdout + r.stderr
    vue = console.read_text(encoding="utf-8", errors="replace")
    corps = [m for m in _frames(vue) if "reste " in m]
    # 4 lignes de plan + 3 actions + 1 pied : la seule hauteur possible quand les trois sont en vol.
    assert any(_hauteur_de_frame(m) == 8 for m in corps), (
        "aucune frame ne rend les trois tickets en vol avec leur ligne d'action"
    )
    pleine = next(m for m in corps if _hauteur_de_frame(m) == 8)
    for iid in (130, 131, 132):
        assert f"#{iid}" in pleine, f"#{iid} manque au bloc alors qu'il est en vol"


def test_le_pied_compte_ce_qui_n_est_ni_solde_ni_en_vol(depot: Depot) -> None:
    """« reste » se compte sur le plan moins les soldés moins les en-vol.

    `nb_plan - POSITION` désignait la position du DERNIER ticket lancé : juste tant que les tickets
    partaient dans l'ordre, faux dès qu'ils ne se prennent plus un par un.
    """
    r, console = _run_a_trois_en_vol(depot, "pied")
    assert r.returncode == 0, r.stdout + r.stderr
    vue = console.read_text(encoding="utf-8", errors="replace")
    assert "3 en vol" in vue, "le pied dit combien de tickets sont en vol dès que N > 1"
    # Quatre au plan, trois en vol, aucun soldé : il en reste exactement un à venir.
    assert re.search(r"3 en vol.*reste 1", vue), (
        "le pied doit annoncer « reste 1 » quand trois des quatre sont en vol"
    )
    assert "reste 0" in vue, "et retomber à zéro quand tout est soldé"


def test_une_session_publie_son_etat_et_ne_dessine_jamais(depot: Depot) -> None:
    """Le choix de #290 : retirer l'écran à tous sauf un, plutôt que le partager entre N écrivains.

    Le contrat tient dans un fichier PAR TICKET (`<iid>.vue` — marqueur puis action) que le pilote
    relit à chaque frame. C'est ce qui permet à la hauteur du bloc de redevenir une simple
    variable : un seul processus la lit et l'écrit.
    """
    r, console = _run_a_trois_en_vol(depot, "publication")
    assert r.returncode == 0, r.stdout + r.stderr
    run_dir = depot.racine / ".maestro/orchestrate/publication"
    for iid in (130, 131, 132):
        publie = (run_dir / f"{iid}.vue").read_text(encoding="utf-8")
        assert publie.count("\t") == 1 and publie.startswith((".", "=")), (
            f"« <marqueur><TAB><action> », et un marqueur JAMAIS vide — obtenu {publie!r}"
        )
    assert "\x1b[" not in r.stdout, "aucune frame ne doit fuir dans run.log, même à N sessions"


def test_une_ligne_permanente_de_session_passe_par_la_file_et_ne_casse_pas_le_bloc(
    depot: Depot,
) -> None:
    """Le défaut que la réunion de #290 et #291 avait fabriqué, et que ce lot corrige.

    #291 annonçait l'attente d'une limite d'usage par `trace` — écrire sur l'écran — pour une raison
    juste à sa date : la frame suit immédiatement, et une ligne passée par `tee` pouvait arriver
    après elle. Depuis #290 c'est le contraire qu'il faut : la session n'écrit plus à l'écran, elle
    met en file. `trace` s'appuyait sur `VUE_HAUT` pour retirer le bloc d'abord, or c'est désormais
    une variable du PILOTE dont la session n'a qu'une copie figée au fork — rien n'était donc
    retiré, la ligne s'écrivait sous un bloc toujours affiché, et la frame suivante remontait d'une
    hauteur qui ne correspondait plus à rien. Ce que la hauteur annoncée, elle, ne montre pas :
    elle reste juste — c'est ce qui s'est glissé entre deux frames qui ne l'est pas.
    """
    depot.ticket(130, "Ticket interrompu")
    depot.mr("feat/130-ticket-interrompu", "opened")
    gabarit = _statut_json("%s", "En revue")
    claude = _claude_stub(depot, f"""
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          printf '{gabarit}' 130 > "$MAESTRO_FIXTURES/owner-130.json"
          printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":6}}\\n'
          exit 0
        fi
        printf '{{"type":"result","is_error":true,"total_cost_usd":1,'
        printf '"result":"Claude AI usage limit reached"}}\\n'
        exit 1
    """)
    console = _console(depot)
    plan = _plan_groupes(depot, [(1, 130, "-", "moyenne", "-")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "file-attente",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "3",
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr
    vue = console.read_text(encoding="utf-8", errors="replace")
    assert "limite d'usage atteinte" in vue, "l'annonce arrive bien à l'écran, par la file"
    assert _hauteurs_annoncees_et_reelles(vue), "aucune frame redessinée — le test ne prouve rien"
    egarees = _ecritures_hors_bloc(vue)
    assert not egarees, (
        f"une ligne a été écrite sous un bloc resté affiché — {[m[-90:] for m in egarees]}"
    )


def test_chaque_ligne_permanente_porte_le_numero_de_son_ticket(depot: Depot) -> None:
    """Dans une trace entrelacée, rien d'autre ne dit à qui appartient un « ✓ MR !99 ouverte »."""
    r, _ = _run_a_trois_en_vol(depot, "prefixes")
    assert r.returncode == 0, r.stdout + r.stderr
    verdicts = [ligne for ligne in r.stdout.splitlines() if "MR !99 ouverte" in ligne]
    assert len(verdicts) == 4, f"un verdict par ticket — {verdicts}"
    assert all(re.search(r"#\d+", ligne) for ligne in verdicts), (
        f"chaque verdict doit nommer son ticket — {verdicts}"
    )


# --- Lot 4 : la reprise d'un run qui avait N tickets en main (#291) -------------------------------


def test_une_reprise_rejoue_tous_les_tickets_que_le_run_avait_en_vol(depot: Depot) -> None:
    """La question est posée PAR TICKET, jamais une fois pour le run.

    Un run concurrent coupé en avait N en main, chacun avec son témoin de session : les laisser
    derrière soi, c'est abandonner N worktrees porteurs de travail non commité. Leur cycle de vie
    est « En cours » — posé par leur propre `/ticket-start` —, donc c'est bien le filtre ordinaire
    qui les écarterait sans cette exception.
    """
    # #130 est allé au bout avant la coupure : son cycle de vie a suivi, et c'est par là qu'il se
    # saute tout seul. #131 et #132 étaient EN VOL — « En cours » posé par leur propre
    # `/ticket-start`, donc écartés par le filtre ordinaire sans l'exception de #204/#291.
    depot.ticket(130, "Ticket 130", statut="Terminé")
    for iid in (131, 132):
        depot.ticket(iid, f"Ticket {iid}", statut="En cours")
        depot.mr(f"feat/{iid}-ticket-{iid}", "opened")
    _run_dir(depot, "20260803-100000",
             [(1, 130, "-", "haute"), (2, 131, "-", "haute"), (3, 132, "-", "haute")],
             resume=[(130, "OK", 99, 600, "3.50", "-")], sessions=(131, 132), age=7200)
    r = depot.lance("run.sh", "--resume", "20260803-100000", "--run-id", "suite",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, ())})
    assert r.returncode == 0, r.stdout + r.stderr
    verdicts = {ligne[0]: ligne[1]
                for ligne in _resume(depot.racine / ".maestro/orchestrate/suite")}
    assert verdicts == {"130": "SAUTE", "131": "OK", "132": "OK"}, (
        f"les deux tickets en vol à la coupure sont repris, le livré est sauté — {verdicts}"
    )
    assert r.stdout.count("repris en vol") == 2, (
        "la question est posée par ticket : deux tickets en main, deux reprises"
    )


def test_un_en_cours_que_le_run_repris_n_avait_pas_en_main_reste_saute(depot: Depot) -> None:
    """L'exception est étroite à dessein : sans témoin de session dans le journal repris, ce « En
    cours » est le ticket de quelqu'un d'autre — le reprendre lui retirerait son travail."""
    depot.ticket(130, "Ticket 130", statut="En cours")
    depot.ticket(131, "Ticket 131", statut="En cours")
    depot.mr("feat/130-ticket-130", "opened")
    _run_dir(depot, "20260803-110000", [(1, 130, "-", "haute"), (2, 131, "-", "haute")],
             resume=[], sessions=(130,), age=7200)
    r = depot.lance("run.sh", "--resume", "20260803-110000", "--run-id", "etroit",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, ())})
    assert r.returncode == 0, r.stdout + r.stderr
    verdicts = {ligne[0]: ligne[1]
                for ligne in _resume(depot.racine / ".maestro/orchestrate/etroit")}
    assert verdicts["130"] == "OK", "celui dont le run repris avait le témoin est repris"
    assert verdicts["131"] == "SAUTE", "l'autre appartient à une session voisine"


def test_une_reprise_rejoue_la_concurrence_du_run_coupe(depot: Depot) -> None:
    """La concurrence est un trait DU RUN, pas de la ligne de commande qui le rejoue.

    `/orchestrate --resume` ne passe aucune option : sans le fichier `concurrence`, un run qui
    tournait à trois se reprendrait en séquentiel, et le gain en temps de mur disparaîtrait
    exactement au moment où on en a le plus besoin.
    """
    iids = (130, 131, 132)
    _livrables(depot, iids)
    dossier = _run_dir(depot, "20260803-120000",
                       [(r, i, "-", "haute") for r, i in enumerate(iids, 1)],
                       resume=[], age=7200)
    (dossier / "concurrence").write_text("3\n", encoding="utf-8", newline="\n")
    os.utime(dossier / "concurrence", (time.time() - 7200,) * 2)
    r = depot.lance("run.sh", "--resume", "20260803-120000", "--run-id", "meme-regime",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, iids)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert (depot.fixtures / "pic.txt").read_text(encoding="utf-8") == "3", (
        "la reprise doit repartir au régime du run coupé, pas au défaut de la ligne de commande"
    )


def test_une_concurrence_explicite_l_emporte_sur_celle_du_run_repris(depot: Depot) -> None:
    """`--resume --concurrence 1` reste la façon de dérouler en séquentiel un run qu'on veut suivre
    de près : ce qui est relu est un DÉFAUT, jamais un verrou."""
    iids = (130, 131)
    _livrables(depot, iids)
    dossier = _run_dir(depot, "20260803-130000",
                       [(r, i, "-", "haute") for r, i in enumerate(iids, 1)],
                       resume=[], age=7200)
    (dossier / "concurrence").write_text("2\n", encoding="utf-8", newline="\n")
    os.utime(dossier / "concurrence", (time.time() - 7200,) * 2)
    r = depot.lance("run.sh", "--resume", "20260803-130000", "--run-id", "explicite",
                    "--concurrence", "1",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, ())})
    assert r.returncode == 0, r.stdout + r.stderr
    assert (depot.fixtures / "pic.txt").read_text(encoding="utf-8") == "1"


def test_un_run_ecrit_sa_concurrence_pour_celui_qui_le_reprendra(depot: Depot) -> None:
    """Le fichier est posé au démarrage, avant le premier ticket : un run coupé à sa première
    minute doit être reprenable au même régime que celui qui serait allé au bout."""
    _livrables(depot, (130,))
    plan = _plan_groupes(depot, [(1, 130, "-", "haute", "-")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "trace-regime", "--concurrence", "2",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, ())})
    assert r.returncode == 0, r.stdout + r.stderr
    trace = depot.racine / ".maestro/orchestrate/trace-regime/concurrence"
    assert trace.read_text(encoding="utf-8").strip() == "2"
