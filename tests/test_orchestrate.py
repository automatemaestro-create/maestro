"""Tests de la boucle d'orchestration autonome — `scripts/orchestrate/` (ticket #172, parent #167).

Tests différés des lots #168 à #171, réunis ici selon la convention de découpage
(`docs/10-workflow-git.md` §5.1).

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

pytestmark = pytest.mark.skipif(BASH is None, reason="bash introuvable")

# Les scripts sous test, recopiés tels quels dans le dépôt jetable.
SCRIPTS = (
    "scripts/gitlab/lib.sh",
    "scripts/orchestrate/queue.sh",
    "scripts/orchestrate/guard.sh",
    "scripts/orchestrate/run.sh",
    "scripts/orchestrate/settings.run.json",
)

# Le bouchon `glab`. Il ne cherche pas à imiter GitLab : il répond au strict nécessaire, en lisant
# des fichiers que le test a écrits. Le dispatch se fait sur des fragments de la requête GraphQL
# telle que lib.sh la compose — si lib.sh change de requête, ces tests le diront.
STUB_GLAB = r"""#!/usr/bin/env bash
FIX="$MAESTRO_FIXTURES"
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
        self, script: str, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [BASH, f"scripts/orchestrate/{script}", *args],
            cwd=self.racine,
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
