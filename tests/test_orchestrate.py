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
    *"milestoneTitle:"*)  cat "$FIX/milestone-issues.json" 2>/dev/null; exit 0 ;;
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


def _statut_json(iid: str, statut: str, assigne: str = "") -> str:
    """La réponse GraphQL que `gl_issue_owner` sait lire."""
    assignes = f'{{"username":"{assigne}"}}' if assigne else ""
    return (
        f'{{"data":{{"project":{{"workItems":{{"nodes":[{{"iid":"{iid}","widgets":['
        f'{{"status":{{"name":"{statut}"}}}},{{"assignees":{{"nodes":[{assignes}]}}}}]}}]}}}}}}}}'
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
        (self.fixtures / "milestones.json").write_text(
            f'{{"data":{{"project":{{"milestones":{{"nodes":[{{"title":"{titre}",'
            f'"stats":{{"totalIssuesCount":10,"closedIssuesCount":3}}}}]}}}}}}}}',
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

    def publie(self) -> None:
        """Compose les deux tables que `queue.sh` lit (milestone et backlog) depuis les tickets."""
        noeuds = []
        for iid, t in self.tickets.items():
            assignes = f'{{"username":"{t["assigne"]}"}}' if t["assigne"] else ""
            noeuds.append(
                f'{{"iid":"{iid}","title":"{t["titre"]}","state":"opened","widgets":['
                f'{{"labels":{{"nodes":[{{"title":"type::{t["type"]}"}},'
                f'{{"title":"prio::{t["prio"]}"}},{{"title":"agent::dev"}}]}}}},'
                f'{{"status":{{"name":"{t["statut"]}"}}}},'
                f'{{"assignees":{{"nodes":[{assignes}]}}}}]}}'
            )
        jointure = ",".join(noeuds)
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
    lignes = [x for x in (dossier / "130.jsonl").read_text(encoding="utf-8").splitlines() if x]
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
    jsonl = (dossier / "130.jsonl").read_text(encoding="utf-8")
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
            (131, "ECHEC", "-", 300, "1.20", "MR « aucune », statut « En cours »"),
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
    assert "reprendre" in r.stdout and "--plan" in r.stdout, "le plan sur disque est le filet"


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
        "la raison consignée doit être exploitable, pas seulement « MR aucune, statut À faire »"
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
