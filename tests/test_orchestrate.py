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
import shutil
import subprocess
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

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


def _plan(depot: Depot, lignes: list[tuple[int, int, str, str]]) -> str:
    """Écrit un plan figé (le TSV que queue.sh produit) et renvoie son chemin."""
    chemin = depot.racine / "plan.tsv"
    contenu = "# rang\tiid\tparent\tprio\ttitre\n" + "".join(
        f"{rang}\t{iid}\t{parent}\t{prio}\tTicket {iid}\n" for rang, iid, parent, prio in lignes
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
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _stub_flux(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "flux",
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
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "flux-reprise",
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
        "# rang\tiid\tparent\tprio\ttitre\n"
        + "".join(f"{r}\t{i}\t{p}\t{prio}\tTicket {i}\n" for r, i, p, prio in plan),
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
