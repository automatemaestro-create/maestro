"""Tests du chantier « travail à plusieurs sur des clones distincts » (ticket #156, parent #155).

Lot final : les lots #157 à #164 ont délibérément différé leurs tests ici (docs/10 §5.1). Ce
module couvre ce qu'ils ont ajouté à [`scripts/gitlab/lib.sh`](../scripts/gitlab/lib.sh) et à
[`scripts/gitlab/doctor.sh`](../scripts/gitlab/doctor.sh) :

* **anti-collision** (#159) — `issue-owner` / `issue-taken` / `start-brief` : savoir qu'un ticket
  est déjà pris **avant** de le démarrer, `begin` REMPLAÇANT la liste des assignés ;
* **lots parallélisables** (#160) — `subtickets` / `startables` : le marqueur « (parallèle) » et la
  règle de blocage entre lots d'un parent ;
* **revue best-effort** (#161, révisé par #196) — `project-humans` / `pick-reviewer` /
  `set-reviewer` / `review-queue` : un relecteur humain ≠ auteur, jamais remplacé, et la file la
  plus ancienne d'abord. Depuis #196 la pose n'est plus **automatique** : le helper reste outillé
  pour un appel manuel, mais aucune commande du workflow ne l'invoque ;
* **retard sur `origin/main`** (#163) — `behind-main` : le constat et l'heuristique de conflit ;
* **garde-fou de clôture** (#164) — `branch-iid` / `close-guard` : la session traite-t-elle bien
  ce ticket ;
* **contrôle doctor du runner** (#157) — section 7 de `doctor.sh`.

S'y ajoute, parce que c'est le module qui outille `lib.sh` face à un `glab` factice, la **création
depuis un fichier** (#233, parent #232) — `create-mr` / `issue-note` / `issue-title` : le texte
long voyage par FICHIER pour qu'aucune commande d'une session autonome ne porte de saut de ligne
ni de `$(…)`, formes qu'aucune règle de permission ne peut reconnaître (docs/10 §11.7).

Même parti pris que [`test_setup.py`](test_setup.py) et [`test_worktree.py`](test_worktree.py) :
un **dépôt jetable** monté dans `tmp_path`, sur lequel les VRAIS scripts sont lancés. Rien n'est
jamais écrit dans le dépôt de travail (`HOME` est lui aussi redirigé).

**Ni réseau ni compte GitLab.** Un `glab` factice est placé en tête du `PATH` : il répond depuis
un fichier JSON que chaque test compose, et **journalise** les commandes reçues (c'est ainsi qu'on
vérifie qu'aucune écriture n'a lieu). Il écrit ses réponses en **octets UTF-8** — le mojibake de
#141 est venu d'un décodage approximatif sous Windows, on ne le réintroduit pas dans le harnais.
Ce qui est testé ici, c'est la **décision** des helpers, jamais l'API GitLab elle-même.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
GIT = shutil.which("git")

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash introuvable"),
    pytest.mark.skipif(GIT is None, reason="git introuvable"),
]

PROJET = "equipe-test/maestro"
MOI = "MaestroAgents"          # le compte d'automatisation partagé (cf. GL_BOT_USERS)

# Verbes d'ÉCRITURE de `glab` : leur absence du journal est ce qui atteste qu'un helper annoncé
# « consultatif » l'est resté. `list`/`view` en sont volontairement absents — lire est le travail
# normal d'un bilan de santé ou d'un garde-fou.
ECRITURES = (
    "mr\tupdate", "mr\tcreate", "mr\tmerge", "mr\tclose",
    "issue\tupdate", "issue\tcreate", "issue\tclose", "issue\tnote",
    "label\tcreate", "variable\tset",
)

# --- Le glab factice -----------------------------------------------------------------------------
# Piloté par $MAESTRO_FAUX_GLAB (état JSON) et $MAESTRO_FAUX_GLAB_JOURNAL (trace des appels).
# Les réponses GraphQL/REST sont choisies par la PREMIÈRE règle dont tous les fragments `contient`
# apparaissent dans la requête — les règles les plus spécifiques se placent donc en tête.
FAUX_GLAB = r'''
import json
import os
import sys

with open(os.environ["MAESTRO_FAUX_GLAB"], encoding="utf-8") as f:
    etat = json.load(f)

args = sys.argv[1:]

journal = os.environ.get("MAESTRO_FAUX_GLAB_JOURNAL")
if journal:
    # Une ligne PAR APPEL, quoi qu'on reçoive : les sauts de ligne d'une description sont
    # échappés en « \n » littéral (#233). Sans ça, un `--description` multi-ligne — la matière
    # même de ce que les helpers de création font voyager — casserait le découpage du journal
    # et un test lirait un demi-appel.
    with open(journal, "a", encoding="utf-8") as f:
        f.write("\t".join(a.replace("\\", "\\\\").replace("\n", "\\n") for a in args) + "\n")


def sortie(texte="", code=0):
    # Écriture en octets : sous Windows, sys.stdout encoderait en cp1252 et rendrait du mojibake
    # là où l'API GitLab renvoie de l'UTF-8 (statuts « À faire », « Terminé »…).
    sys.stdout.buffer.write(texte.encode("utf-8"))
    sys.stdout.buffer.flush()
    raise SystemExit(code)


def compact(obj):
    # L'outillage parse en grep/awk sur le JSON BRUT de glab : pas d'espaces, pas d'échappement
    # des non-ASCII, sinon les motifs (« "workItems":{"nodes":[]} ») ne matchent plus.
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n"


def repond(regles, sujet):
    for regle in regles:
        if all(fragment in sujet for fragment in regle.get("contient", [])):
            if "brut" in regle:
                return regle["brut"]
            return compact(regle["reponse"])
    return None


if args[:2] == ["auth", "status"]:
    sortie(code=0 if etat.get("authentifie", True) else 1)

if args[:2] == ["api", "user"]:
    sortie(compact({"username": etat.get("moi", "inconnu"), "id": 4242}))

if args[:2] == ["api", "graphql"]:
    requete = "".join(a[len("query="):] for a in args[2:] if a.startswith("query="))
    reponse = repond(etat.get("graphql", []), requete)
    sortie(reponse) if reponse is not None else sortie(code=1)

if args[:1] == ["api"]:
    reponse = repond(etat.get("rest", []), args[1] if len(args) > 1 else "")
    sortie(reponse) if reponse is not None else sortie(code=1)

if args[:2] == ["issue", "view"]:
    corps = etat.get("issues", {}).get(args[2] if len(args) > 2 else "")
    sortie(corps) if corps is not None else sortie(code=1)

if args[:2] == ["mr", "update"]:
    sortie("Merge request mise à jour.\n")

if args[:2] == ["mr", "create"]:
    sortie(etat.get("mr_create_sortie", "MR ouverte : " + etat.get("mr_url", "") + "\n"),
           code=etat.get("mr_create_code", 0))

if args[:2] == ["issue", "note"]:
    sortie("Commentaire ajouté.\n", code=etat.get("issue_note_code", 0))

sortie(code=1)
'''


# --- Fabrication des réponses --------------------------------------------------------------------
# Des fabriques plutôt que du JSON en dur : la FORME des réponses (ordre des clés compris) fait
# partie de ce que les parsers awk/grep de lib.sh attendent — la centraliser évite qu'un test
# passe pour une raison qui n'a rien à voir avec ce qu'il prétend vérifier.


def noeud_ticket(iid: str, titre: str, statut: str, labels: list[str], assignes: list[str]) -> dict:
    """Un work item tel que le rend la requête backlog (ordre des clés : iid, title, widgets).

    `statut` reste un LIBELLÉ côté test (« En revue ») : depuis #209 il voyage dans le widget
    Labels, sous la forme `workflow::<slug>`, aux côtés des `type::`/`agent::`/`prio::`.
    """
    return {
        "iid": iid,
        "title": titre,
        "widgets": [
            {
                "labels": {
                    "nodes": labels_workflow(statut) + [{"title": label} for label in labels]
                }
            },
            {"assignees": {"nodes": [{"username": u} for u in assignes]}},
        ],
    }


def reponse_backlog(tickets: list[dict]) -> dict:
    return {"data": {"project": {"workItems": {"nodes": tickets}}}}


def colonnes(sortie: str) -> list[list[str]]:
    """Découpe une sortie TSV de `lib.sh` en lignes de colonnes, en-tête « # … » écartée."""
    return [
        ligne.split("\t")
        for ligne in sortie.splitlines()
        if ligne and not ligne.startswith("#")
    ]


# Cycle de vie : libellé (surface) -> slug (stockage). Depuis #209 le cycle de vie est porté par un
# LABEL `workflow::<slug>` et non plus par le champ Status natif — les doubles de test doivent donc
# répondre un widget Labels. Les tests, eux, continuent d'écrire et d'attendre le LIBELLÉ : c'est
# exactement le contrat de surface documenté en tête de scripts/gitlab/lib.sh.
SLUG_WORKFLOW = {
    "À faire": "a-faire",
    "En cours": "en-cours",
    "En revue": "en-revue",
    "Terminé": "termine",
    "Abandonné": "abandonne",
    "Doublon": "doublon",
}


def labels_workflow(statut: str) -> list[dict]:
    """Nœuds de labels d'un ticket portant le cycle de vie `statut` (vide si `statut` est vide)."""
    if not statut:
        return []
    return [{"title": f"workflow::{SLUG_WORKFLOW.get(statut, statut)}"}]


def reponse_owner(statut: str, assignes: list[str]) -> dict:
    """Réponse de la requête cycle de vie + assignés d'un seul ticket (gl_issue_owner)."""
    return {
        "data": {
            "project": {
                "workItems": {
                    "nodes": [
                        {
                            "widgets": [
                                {"labels": {"nodes": labels_workflow(statut)}},
                                {"assignees": {"nodes": [{"username": u} for u in assignes]}},
                            ]
                        }
                    ]
                }
            }
        }
    }


def regle_owner(statut: str, assignes: list[str]) -> dict:
    """Règle de réponse à la requête cycle de vie + assignés d'UN ticket.

    Le fragment `workItems(iids:` est indispensable : la requête backlog porte elle aussi
    `WorkItemWidgetAssignees` et capterait la règle si elle était moins spécifique.
    """
    return {
        "contient": ["workItems(iids:", "WorkItemWidgetAssignees"],
        "reponse": reponse_owner(statut, assignes),
    }


def corps_ticket(titre: str, labels: str, description: str) -> str:
    """Sortie de `glab issue view` : en-têtes `clé:<TAB>valeur`, séparateur `--`, puis le corps."""
    return (
        f"title:\t{titre}\n"
        "state:\topen\n"
        "author:\tMaestroAgents\n"
        f"labels:\t{labels}\n"
        "comments:\t0\n"
        "assignees:\t\n"
        "milestone:\tPhase 4 — Control Tower UX\n"
        "--\n"
        f"{description}\n"
    )


@dataclass
class Depot:
    """Dépôt jetable équipé du vrai `lib.sh`, d'un `origin` local et d'un `glab` factice."""

    racine: Path
    origin: Path
    home: Path
    fauxbin: Path
    etat_json: Path
    journal: Path
    etat: dict = field(default_factory=dict)

    # --- pilotage du glab factice ---
    def pose_etat(self, **entrees: object) -> None:
        """Remplace tout ou partie de l'état du glab factice (`graphql`, `rest`, `issues`…)."""
        self.etat.update(entrees)
        self.etat_json.write_text(
            json.dumps(self.etat, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
        )

    def appels(self) -> list[str]:
        """Commandes `glab` reçues depuis le début du test (une par ligne, arguments en TAB)."""
        if not self.journal.exists():
            return []
        lignes = self.journal.read_text(encoding="utf-8").splitlines()
        return [ligne for ligne in lignes if ligne]

    # --- exécution ---
    def lib(
        self,
        *args: str,
        cwd: Path | None = None,
        reglages: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self._bash("scripts/gitlab/lib.sh", *args, cwd=cwd, reglages=reglages)

    def doctor(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self._bash("scripts/gitlab/doctor.sh", *args, cwd=None)

    def ensure_runner(self, *args: str, **reglages: str) -> subprocess.CompletedProcess[str]:
        return self._bash("scripts/gitlab/ensure-runner.sh", *args, cwd=None, reglages=reglages)

    def _bash(
        self,
        script: str,
        *args: str,
        cwd: Path | None,
        reglages: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environnement = os.environ.copy()
        environnement.update(
            {
                "HOME": str(self.home),
                "PATH": os.pathsep.join([str(self.fauxbin), environnement.get("PATH", "")]),
                "GL_PROJECT": PROJET,
                # Aucune attente : le retry de gl_graphql_read ne sert qu'aux hoquets réseau,
                # et une réponse volontairement muette ne doit pas coûter trois secondes au test.
                "GL_GQL_RETRIES": "1",
                "GL_GQL_RETRY_DELAY": "0",
                "MAESTRO_FAUX_GLAB": str(self.etat_json),
                "MAESTRO_FAUX_GLAB_JOURNAL": str(self.journal),
                # Rien ne doit toucher au Docker ni au runner du poste : le démon est déclaré
                # injoignable et l'attente est nulle (voir les shims `docker`/`powershell.exe`).
                "MAESTRO_RUNNER_ID": "",
                "MAESTRO_DOCKER_TIMEOUT": "0",
                "MAESTRO_RUNNER_TIMEOUT": "0",
                "MAESTRO_RUNNER_POLL": "1",
            }
        )
        environnement.update(reglages or {})
        assert BASH is not None
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

    # --- git ---
    def git(self, *args: str) -> str:
        assert GIT is not None
        acheve = subprocess.run(  # noqa: S603
            [GIT, "-c", "core.hooksPath=", *args],
            cwd=str(self.racine),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return acheve.stdout.strip()

    def commit(self, fichier: str, contenu: str, message: str = "chore: essai") -> None:
        (self.racine / fichier).write_text(contenu, encoding="utf-8", newline="\n")
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", message)


@pytest.fixture
def depot(tmp_path: Path) -> Depot:
    assert GIT is not None
    racine = tmp_path / "clone"
    origin = tmp_path / "origin.git"
    home = tmp_path / "home"
    fauxbin = tmp_path / "fauxbin"
    for dossier in (home, fauxbin):
        dossier.mkdir()

    def git(*args: str, cwd: Path) -> None:
        subprocess.run(  # noqa: S603
            [GIT, "-c", "core.hooksPath=", *args], cwd=str(cwd), check=True, capture_output=True
        )

    origin.mkdir()
    git("init", "--bare", "--quiet", "--initial-branch=main", cwd=origin)

    racine.mkdir()
    git("init", "--quiet", "--initial-branch=main", cwd=racine)
    git("config", "user.email", "test@maestro.invalid", cwd=racine)
    git("config", "user.name", "Maestro Test", cwd=racine)

    for relatif in (
        "scripts/gitlab/lib.sh",
        "scripts/gitlab/doctor.sh",
        "scripts/gitlab/ensure-runner.sh",
        # `reconcile-en-cours` (#328) délègue la relecture des cartes de pilote à ce fichier — il
        # ne la refait pas, deux formules qui divergeraient se remarqueraient trop tard.
        "scripts/orchestrate/pilote.sh",
        # `reprendre-en-cours` (#329) lui demande d'où sort le ticket qu'on reprend : le journal
        # d'un run est le seul endroit qui sache dire quel run l'a laissé là, et avec quel verdict.
        "scripts/orchestrate/journal.sh",
    ):
        cible = racine / relatif
        cible.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RACINE / relatif, cible)

    (racine / "fichier-a.txt").write_text("a\n", encoding="utf-8", newline="\n")
    (racine / "fichier-b.txt").write_text("b\n", encoding="utf-8", newline="\n")
    git("add", "-A", cwd=racine)
    git("commit", "--quiet", "-m", "chore: dépôt jetable", cwd=racine)
    git("remote", "add", "origin", str(origin), cwd=racine)
    git("push", "--quiet", "-u", "origin", "main", cwd=racine)

    # Le glab factice : un script Python, appelé par un lanceur nommé `glab` (sans extension) pour
    # que `command -v glab` de lib.sh le trouve comme le vrai.
    (fauxbin / "faux_glab.py").write_text(FAUX_GLAB, encoding="utf-8", newline="\n")
    lanceur = fauxbin / "glab"
    interpreteur = sys.executable.replace(chr(92), "/")
    lanceur.write_text(
        "#!/usr/bin/env bash\n"
        f'exec "{interpreteur}" "{(fauxbin / "faux_glab.py").as_posix()}" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    lanceur.chmod(0o755)

    # Neutralisation du poste : `docker` répond toujours en échec (démon injoignable) et
    # `powershell.exe` ne fait rien — aucun test ne doit démarrer Docker Desktop pour de vrai.
    for nom, corps in (
        ("docker", "#!/usr/bin/env bash\nexit 1\n"),
        ("powershell.exe", "#!/usr/bin/env bash\nexit 0\n"),
    ):
        shim = fauxbin / nom
        shim.write_text(corps, encoding="utf-8", newline="\n")
        shim.chmod(0o755)

    depot = Depot(
        racine=racine,
        origin=origin,
        home=home,
        fauxbin=fauxbin,
        etat_json=tmp_path / "faux-glab.json",
        journal=tmp_path / "faux-glab.log",
    )
    depot.pose_etat(moi=MOI, authentifie=True, graphql=[], rest=[], issues={})
    return depot


# =================================================================================================
# Anti-collision : à qui appartient le ticket ? (#159)
# =================================================================================================


def test_issue_owner_rend_statut_et_assignes(depot: Depot) -> None:
    depot.pose_etat(
        graphql=[regle_owner("En cours", ["bea"])]
    )
    acheve = depot.lib("issue-owner", "159")
    assert acheve.returncode == 0, acheve.stderr
    assert acheve.stdout.strip() == "En cours\tbea"


def test_issue_owner_rend_un_champ_vide_pour_un_ticket_libre(depot: Depot) -> None:
    depot.pose_etat(
        graphql=[regle_owner("À faire", [])]
    )
    acheve = depot.lib("issue-owner", "159")
    assert acheve.returncode == 0, acheve.stderr
    assert acheve.stdout.rstrip("\n") == "À faire\t"


def test_issue_owner_refuse_un_ticket_introuvable(depot: Depot) -> None:
    """Sans ce garde-fou, deux champs vides passeraient pour « statut non posé, ticket libre »."""
    depot.pose_etat(
        graphql=[
            {
                "contient": ["WorkItemWidgetAssignees"],
                "reponse": {"data": {"project": {"workItems": {"nodes": []}}}},
            }
        ]
    )
    acheve = depot.lib("issue-owner", "999")
    assert acheve.returncode == 1
    assert "introuvable" in acheve.stderr
    assert acheve.stdout.strip() == ""


def test_issue_owner_refuse_un_projet_illisible(depot: Depot) -> None:
    """« project:null » (projet inconnu ou droits insuffisants) sort en code 0 côté GraphQL."""
    depot.pose_etat(
        graphql=[
            {"contient": ["WorkItemWidgetAssignees"], "reponse": {"data": {"project": None}}}
        ]
    )
    acheve = depot.lib("issue-owner", "159")
    assert acheve.returncode == 1
    assert "illisible" in acheve.stderr


def test_issue_taken_signale_un_ticket_en_cours_chez_un_autre(depot: Depot) -> None:
    depot.pose_etat(
        graphql=[regle_owner("En cours", ["bea"])]
    )
    acheve = depot.lib("issue-taken", "159")
    assert acheve.returncode == 0
    assert acheve.stdout.strip() == "bea"


def test_issue_taken_ne_signale_pas_mon_propre_ticket(depot: Depot) -> None:
    depot.pose_etat(
        graphql=[regle_owner("En cours", [MOI])]
    )
    assert depot.lib("issue-taken", "159").returncode == 1


def test_issue_taken_ne_signale_ni_un_ticket_libre_ni_un_autre_statut(depot: Depot) -> None:
    """Prédicat volontairement étroit : seul « En cours » chez un tiers est une collision."""
    depot.pose_etat(
        graphql=[regle_owner("En cours", [])]
    )
    assert depot.lib("issue-taken", "159").returncode == 1

    depot.pose_etat(
        graphql=[regle_owner("En revue", ["bea"])]
    )
    assert depot.lib("issue-taken", "159").returncode == 1


def test_issue_taken_ne_confond_pas_un_username_avec_son_prefixe(depot: Depot) -> None:
    """« bea » ne doit pas se reconnaître dans « bea-bot » : l'appartenance est EXACTE."""
    depot.pose_etat(
        moi="bea",
        graphql=[
            regle_owner("En cours", ["bea-bot"])
        ],
    )
    acheve = depot.lib("issue-taken", "159")
    assert acheve.returncode == 0, "un ticket de bea-bot n'appartient pas à bea"
    assert acheve.stdout.strip() == "bea-bot"


# =================================================================================================
# Préflight de /ticket-start (#159)
# =================================================================================================

TICKET_SIMPLE = corps_ticket(
    "Anti-collision sur les tickets",
    "agent::devops, prio::moyenne, type::infra",
    "## Contexte\n\nDeux sessions, un seul ticket.\n\n"
    "## Critères d'acceptation\n\n- [ ] Le ticket pris est signalé\n",
)


def test_start_brief_signale_un_arbre_non_propre_sans_bloquer(depot: Depot) -> None:
    """Depuis #181, l'arbre sale est un AVERTISSEMENT et non plus un refus.

    L'intention d'origine — ne pas démarrer par-dessus le travail d'une autre session — n'est pas
    abandonnée, elle change de porteur : `/ticket-start` monte désormais un worktree par ticket, si
    bien que des changements non commités ici restent derrière nous, intacts et hors du chemin.
    Les refuser bloquerait le démarrage pour une saleté sans rapport avec le ticket visé. La
    décision revient à la commande, seule à connaître le verdict de `worktree.sh ensure` :
    bloquante sur « ICI » (on travaillerait dans cet arbre), anodine sur « WORKTREE ».
    """
    depot.pose_etat(issues={"159": TICKET_SIMPLE})
    (depot.racine / "fichier-a.txt").write_text("modifié\n", encoding="utf-8", newline="\n")

    acheve = depot.lib("start-brief", "159")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "arbre de travail non propre" in acheve.stderr
    assert "1 fichier(s) non commité(s)" in acheve.stderr, "le compte rend l'alerte actionnable"
    # Le brief est bien rendu : c'est tout l'intérêt de ne plus sortir avant de l'imprimer.
    assert "#159" in acheve.stdout


def test_start_brief_annonce_un_ticket_libre(depot: Depot) -> None:
    depot.pose_etat(
        issues={"159": TICKET_SIMPLE},
        graphql=[regle_owner("À faire", [])],
    )
    acheve = depot.lib("start-brief", "159")
    assert acheve.returncode == 0, acheve.stderr
    assert "statut : À faire — libre (aucun assigné)" in acheve.stdout
    assert "⚠" not in acheve.stdout
    assert "branche proposée : chore/159-anti-collision-sur-les-tickets" in acheve.stdout
    # Le brief est une PROJECTION : critères oui, contexte non (moins de contexte réinjecté).
    assert "Le ticket pris est signalé" in acheve.stdout
    assert "Deux sessions, un seul ticket." not in acheve.stdout


def test_start_brief_avertit_sur_un_ticket_deja_pris(depot: Depot) -> None:
    """`begin` REMPLACE les assignés : démarrer un ticket pris le retirerait en silence."""
    depot.pose_etat(
        issues={"159": TICKET_SIMPLE},
        graphql=[
            regle_owner("En cours", ["bea"])
        ],
    )
    acheve = depot.lib("start-brief", "159")
    assert acheve.returncode == 0, acheve.stderr
    assert "⚠ déjà pris par bea" in acheve.stdout
    assert "retirerait son assignation" in acheve.stdout


def test_start_brief_ne_s_alarme_pas_de_mon_propre_ticket(depot: Depot) -> None:
    depot.pose_etat(
        issues={"159": TICKET_SIMPLE},
        graphql=[
            regle_owner("En cours", [MOI])
        ],
    )
    acheve = depot.lib("start-brief", "159")
    assert f"statut : En cours — pris par : {MOI}" in acheve.stdout
    assert "déjà pris" not in acheve.stdout


def test_start_brief_dit_quand_l_appartenance_est_illisible(depot: Depot) -> None:
    """Une lecture en échec ne doit pas se lire comme un feu vert."""
    depot.pose_etat(issues={"159": TICKET_SIMPLE}, graphql=[])
    acheve = depot.lib("start-brief", "159")
    assert acheve.returncode == 0, acheve.stderr
    assert "appartenance illisible" in acheve.stdout


def test_start_brief_signale_une_branche_sans_prefixe(depot: Depot) -> None:
    """Sans label `type::`, on ne fabrique pas une branche mal nommée : on le dit."""
    depot.pose_etat(
        issues={"159": corps_ticket("Sans type", "agent::dev, prio::basse", "Corps.")},
        graphql=[regle_owner("À faire", [])],
    )
    acheve = depot.lib("start-brief", "159")
    assert "<type>/159-sans-type (label type:: absent" in acheve.stdout


# =================================================================================================
# Lots parallélisables d'un parent de suivi (#160)
# =================================================================================================

PARENT = corps_ticket(
    "Travail à plusieurs",
    "agent::devops, prio::moyenne, type::infra",
    """## Sous-tickets

- [x] #201 — Socle du chantier
- [ ] #202 — Écran de suivi (parallèle)
- [ ] #203 — Endpoint de lecture (parallèle)
- [ ] #204 — Tests + doc

## Notes

Rien à voir avec la checklist.
""",
)


def backlog_des_lots(statuts: dict[str, str]) -> list[dict]:
    return [
        {
            "contient": ["workItems(state: all"],
            "reponse": reponse_backlog(
                [
                    noeud_ticket(iid, f"Lot {iid}", statut, ["type::infra"], [])
                    for iid, statut in statuts.items()
                ]
            ),
        }
    ]


def test_subtickets_lit_la_checklist_et_le_marqueur_parallele(depot: Depot) -> None:
    depot.pose_etat(
        issues={"155": PARENT},
        graphql=backlog_des_lots(
            {"201": "Terminé", "202": "À faire", "203": "À faire", "204": "À faire"}
        ),
    )
    acheve = depot.lib("subtickets", "155")
    assert acheve.returncode == 0, acheve.stderr
    lignes = colonnes(acheve.stdout)
    assert [ligne[0] for ligne in lignes] == ["201", "202", "203", "204"]
    assert [ligne[1] for ligne in lignes] == ["x", "-", "-", "-"]      # coche de la checklist
    assert [ligne[2] for ligne in lignes] == ["Terminé", "À faire", "À faire", "À faire"]
    assert [ligne[3] for ligne in lignes] == ["-", "∥", "∥", "-"]      # marqueur « (parallèle) »
    # Le marqueur est extrait dans sa colonne, pas laissé dans le titre.
    assert lignes[1][4] == "Écran de suivi"
    # Et la section suivante du corps n'a pas débordé dans la checklist.
    assert "Rien à voir" not in acheve.stdout


def test_subtickets_refuse_un_ticket_sans_checklist(depot: Depot) -> None:
    depot.pose_etat(issues={"159": TICKET_SIMPLE})
    acheve = depot.lib("subtickets", "159")
    assert acheve.returncode == 1
    assert "pas un ticket parent" in acheve.stderr


def test_startables_laisse_les_lots_paralleles_se_prendre_ensemble(depot: Depot) -> None:
    """Le cœur de #160 : deux lots marqués ne se bloquent pas — deux personnes les prennent."""
    depot.pose_etat(
        issues={"155": PARENT},
        graphql=backlog_des_lots(
            {"201": "Terminé", "202": "À faire", "203": "À faire", "204": "À faire"}
        ),
    )
    acheve = depot.lib("startables", "155")
    assert acheve.returncode == 0, acheve.stderr
    assert "#202" in acheve.stdout and "(parallèle)" in acheve.stdout
    assert "#203" in acheve.stdout
    # …mais le lot final « tests + doc », non marqué, reste derrière tout le monde.
    assert "#204" not in acheve.stdout


def test_startables_ne_bloque_pas_sur_un_lot_precedent_en_revue(depot: Depot) -> None:
    """Les lots s'enchaînent dès « En revue » : une MR en attente de merge ne barre rien (#63)."""
    depot.pose_etat(
        issues={"155": PARENT},
        graphql=backlog_des_lots(
            {"201": "En revue", "202": "Terminé", "203": "En revue", "204": "À faire"}
        ),
    )
    acheve = depot.lib("startables", "155")
    assert "#204" in acheve.stdout


def test_startables_barre_un_lot_parallele_derriere_un_lot_non_marque(depot: Depot) -> None:
    """Le marqueur n'affranchit que des AUTRES lots marqués, pas d'un lot ordinaire en retard."""
    depot.pose_etat(
        issues={"155": PARENT},
        graphql=backlog_des_lots(
            {"201": "En cours", "202": "À faire", "203": "À faire", "204": "À faire"}
        ),
    )
    acheve = depot.lib("startables", "155")
    assert acheve.stdout.strip() == ""


def test_start_brief_sur_un_parent_liste_les_lots_demarrables(depot: Depot) -> None:
    """Un parent ne porte ni branche ni code : le brief redirige, il ne propose pas de branche."""
    depot.pose_etat(
        issues={"155": PARENT},
        graphql=[
            regle_owner("En cours", []),
            *backlog_des_lots(
                {"201": "Terminé", "202": "À faire", "203": "À faire", "204": "À faire"}
            ),
        ],
    )
    acheve = depot.lib("start-brief", "155")
    assert acheve.returncode == 0, acheve.stderr
    assert "parent de suivi — ne porte ni branche ni code" in acheve.stdout
    assert "lots démarrables maintenant" in acheve.stdout
    assert "#202" in acheve.stdout and "#203" in acheve.stdout
    assert "branche proposée" not in acheve.stdout


def test_start_brief_sur_un_sous_ticket_controle_les_lots_precedents(depot: Depot) -> None:
    sous_ticket = corps_ticket(
        "Endpoint de lecture",
        "agent::dev, prio::moyenne, type::infra",
        "Sous-ticket de #155 — lot 3/4.\n\nTests différés → #204.\n",
    )
    depot.pose_etat(
        issues={"155": PARENT, "203": sous_ticket},
        graphql=[
            regle_owner("À faire", []),
            *backlog_des_lots(
                {"201": "En cours", "202": "À faire", "203": "À faire", "204": "À faire"}
            ),
        ],
    )
    acheve = depot.lib("start-brief", "203")
    assert acheve.returncode == 0, acheve.stderr
    assert "sous-ticket de #155 — lot 3/4" in acheve.stdout
    assert "lot marqué « parallèle »" in acheve.stdout
    assert "⚠ non livrés : #201 (En cours)" in acheve.stdout
    # #202 est marqué « parallèle » comme #203 : il ne compte pas comme bloquant.
    assert "#202" not in acheve.stdout.split("lots précédents :")[1].splitlines()[0]
    # Les tests différés sont annoncés — livrer sans tests est prévu, pas un oubli.
    assert "tests différés → #204" in acheve.stdout


# =================================================================================================
# Revue best-effort : file de revue et relecteur posé à la main (#161, révisé par #196)
# =================================================================================================


def reponse_membres(membres: list[tuple[str, int, bool, str]]) -> dict:
    return {
        "data": {
            "project": {
                "projectMembers": {
                    "nodes": [
                        {
                            "accessLevel": {"integerValue": niveau},
                            "user": {"username": nom, "bot": bot, "state": etat},
                        }
                        for nom, niveau, bot, etat in membres
                    ]
                }
            }
        }
    }


MEMBRES = [
    ("bea", 40, False, "active"),
    ("cam", 30, False, "active"),
    ("dan", 30, False, "active"),
    (MOI, 40, False, "active"),        # compte d'automatisation : jamais relecteur
    ("invite", 20, False, "active"),   # sous le niveau Developer : ne peut ni pousser ni merger
    ("robot", 40, True, "active"),     # vrai bot au sens de GitLab
    ("parti", 40, False, "blocked"),
]


def test_project_humans_ecarte_bots_niveaux_faibles_et_comptes_inactifs(depot: Depot) -> None:
    """Quatre exclusions, dont une que l'API seule ne saurait faire.

    Le compte de l'agent Maestro n'est pas un « bot » au sens de GitLab (`User.bot` y vaut
    false) : seule la configuration `GL_BOT_USERS` l'écarte. Sans elle, l'outillage se
    désignerait lui-même relecteur.
    """
    depot.pose_etat(graphql=[{"contient": ["projectMembers"], "reponse": reponse_membres(MEMBRES)}])
    acheve = depot.lib("project-humans")
    assert acheve.returncode == 0, acheve.stderr
    retenus = {ligne[0] for ligne in colonnes(acheve.stdout)}
    assert retenus == {"bea", "cam", "dan"}


def test_pick_reviewer_ecarte_l_auteur_et_le_compte_d_automatisation(depot: Depot) -> None:
    depot.pose_etat(graphql=[{"contient": ["projectMembers"], "reponse": reponse_membres(MEMBRES)}])
    for graine in range(6):
        acheve = depot.lib("pick-reviewer", "bea", str(graine))
        assert acheve.returncode == 0, acheve.stderr
        assert acheve.stdout.strip() in {"cam", "dan"}, acheve.stdout


def test_pick_reviewer_est_reproductible_mais_tourne(depot: Depot) -> None:
    """Même MR → même relecteur (pose idempotente) ; MR différentes → charge répartie."""
    depot.pose_etat(graphql=[{"contient": ["projectMembers"], "reponse": reponse_membres(MEMBRES)}])
    choisis = [depot.lib("pick-reviewer", "bea", str(g)).stdout.strip() for g in range(4)]
    assert choisis[0] == depot.lib("pick-reviewer", "bea", "0").stdout.strip()
    assert len(set(choisis)) > 1, f"aucune rotation : {choisis}"


def test_pick_reviewer_echoue_proprement_sur_un_projet_d_une_personne(depot: Depot) -> None:
    """La revue est best-effort : l'appelant poursuit sans relecteur, sans planter."""
    depot.pose_etat(
        graphql=[
            {"contient": ["projectMembers"],
             "reponse": reponse_membres([("bea", 40, False, "active")])}
        ]
    )
    acheve = depot.lib("pick-reviewer", "bea", "1")
    assert acheve.returncode == 1
    assert "aucun relecteur humain disponible" in acheve.stderr


def etat_revue(depot: Depot, auteur: str, relecteurs: list[str]) -> None:
    depot.pose_etat(
        graphql=[
            {
                "contient": ["mergeRequest(iid:"],
                "reponse": {
                    "data": {
                        "project": {
                            "mergeRequest": {
                                "author": {"username": auteur},
                                "reviewers": {"nodes": [{"username": r} for r in relecteurs]},
                            }
                        }
                    }
                },
            },
            {"contient": ["projectMembers"], "reponse": reponse_membres(MEMBRES)},
        ]
    )


def test_set_reviewer_pose_un_humain_distinct_de_l_auteur(depot: Depot) -> None:
    etat_revue(depot, auteur="bea", relecteurs=[])
    acheve = depot.lib("set-reviewer", "12")
    assert acheve.returncode == 0, acheve.stderr
    assert "relecteur → @" in acheve.stdout
    poses = [a for a in depot.appels() if a.startswith("mr\tupdate")]
    assert len(poses) == 1
    assert poses[0].split("\t")[-1] in {"cam", "dan"}


def test_set_reviewer_ne_remplace_jamais_un_relecteur_deja_pose(depot: Depot) -> None:
    """Idempotent et non destructif : un relecteur posé à la main survit à un second passage."""
    etat_revue(depot, auteur="bea", relecteurs=["dan"])
    acheve = depot.lib("set-reviewer", "12")
    assert acheve.returncode == 0, acheve.stderr
    assert "relecteur déjà posé (@dan) — inchangé" in acheve.stdout
    assert [a for a in depot.appels() if a.startswith("mr\tupdate")] == []


def test_set_reviewer_refuse_de_designer_l_auteur(depot: Depot) -> None:
    etat_revue(depot, auteur="bea", relecteurs=[])
    acheve = depot.lib("set-reviewer", "12", "bea")
    assert acheve.returncode == 1
    assert "est l'auteur de la MR" in acheve.stderr
    assert [a for a in depot.appels() if a.startswith("mr\tupdate")] == []


def test_aucune_commande_ne_pose_de_relecteur_automatiquement() -> None:
    """#196 : la pose d'un relecteur reste outillée, mais n'est plus AUTOMATIQUE.

    Le helper `set-reviewer` continue d'exister et de fonctionner (tests ci-dessus) ; ce qui
    disparaît, c'est son **appel** par le cycle de clôture — désigner un relecteur attribue une MR
    à quelqu'un qui ne l'a pas demandé, alors que la file de revue porte déjà le signal. Cette
    règle vit dans des **prompts** (`.claude/commands/*.md`), pas dans du code : seule une lecture
    de ces fichiers peut la garder. On cherche donc une *invocation* (une ligne de commande), pas
    une mention — les commandes ont le droit de nommer le helper pour dire de ne pas l'appeler.
    """
    invocations: list[str] = []
    for commande in sorted((RACINE / ".claude" / "commands").glob("*.md")):
        lignes = commande.read_text(encoding="utf-8").splitlines()
        for numero, ligne in enumerate(lignes, 1):
            nue = ligne.strip()
            appel_helper = nue.startswith("bash ") and "set-reviewer" in nue
            appel_glab = nue.startswith("glab ") and "--reviewer" in nue
            if appel_helper or appel_glab:
                invocations.append(f"{commande.name}:{numero}: {nue}")
    assert invocations == [], (
        "pose automatique de relecteur réintroduite (#196) :\n" + "\n".join(invocations)
    )


def invocations_des_commandes() -> list[tuple[str, int, str]]:
    """Les lignes de `.claude/commands/*.md` qui sont des APPELS, pas de la prose.

    Même heuristique que le test #196 ci-dessus, et pour la même raison : ces commandes ont le
    droit — le devoir, même — de *nommer* une forme interdite pour dire de ne pas l'employer. Seule
    une ligne qui commence par le verbe est une prescription.
    """
    lignes: list[tuple[str, int, str]] = []
    for commande in sorted((RACINE / ".claude" / "commands").glob("*.md")):
        for numero, ligne in enumerate(commande.read_text(encoding="utf-8").splitlines(), 1):
            nue = ligne.strip()
            if nue.startswith(("bash ", "git ", "glab ", "npm ")):
                lignes.append((commande.name, numero, nue))
    return lignes


def test_aucune_commande_ne_prescrit_de_forme_immatchable() -> None:
    """#233 : la couche permissions découpe un appel sur ses SAUTS DE LIGNE et ne matche aucune
    SUBSTITUTION `$(…)`.

    Une commande prescrite sous l'une de ces deux formes est refusée *alors même que* le verbe est
    autorisé, et le refus tombe sans personne pour l'accorder : 10 fois sur 8 sessions autonomes,
    toujours sur la **dernière action du ticket**, quand tout est commité et que rien ne le
    déclare. La règle vit dans des **prompts**, pas dans du code — seule une lecture de ces
    fichiers peut la garder.
    """
    fautives = [
        f"{nom}:{numero}: {nue}"
        for nom, numero, nue in invocations_des_commandes()
        if "$(" in nue or "<<" in nue
    ]
    assert fautives == [], (
        "forme immatchable réintroduite dans un prompt (#233) — passer par un fichier :\n"
        + "\n".join(fautives)
    )


def test_le_cycle_de_cloture_appelle_les_helpers_de_creation() -> None:
    """Le pendant POSITIF du test ci-dessus, et la leçon de !198 : livrer `create-mr` ne sert à
    rien tant que rien ne l'appelle.

    Cette MR-là avait ajouté les trois helpers à `lib.sh` — testés, fonctionnels — sans toucher aux
    prompts, restés sur le `glab mr create` multi-ligne. Tout était vert : aucun test ne regardait
    le **raccordement**. C'est ce trou-ci que ce test bouche.
    """
    appels = [
        (nom, nue) for nom, _, nue in invocations_des_commandes() if "lib.sh create-mr" in nue
    ]
    assert [nom for nom, _ in appels] == ["ticket-finish.md"], (
        "`/ticket-finish` doit être le seul à créer la MR, et il doit le faire par le helper "
        f"(#233) — trouvé : {appels}"
    )
    assert not [
        f"{nom}:{numero}: {nue}"
        for nom, numero, nue in invocations_des_commandes()
        if "mr create" in nue
    ], "un `glab mr create` direct est réapparu : sa description est multi-ligne, donc refusée"


def test_branch_cleanup_delegue_sa_boucle_au_helper() -> None:
    """#309 : la boucle « quel est l'état de la MR de cette branche ? » vit dans `lib.sh`.

    `/branch-cleanup` la décrivait en prose — un `glab mr view <branche> --output json` par
    branche locale, soit ~3 500 octets réinjectés pour en tirer un mot, **~43 000 tokens** sur ce
    dépôt à chaque invocation (audit #304 §4.1, le plus gros gisement du lot). `cleanup-merged`
    fait la même chose en shell, avec le **même** garde-fou, et n'imprime qu'un bilan.

    Deux implémentations du même garde-fou, dont une en prose, c'est aussi la divergence que
    supprime la délégation : le jour où le garde-fou change, la prose ne suit pas. D'où la seconde
    assertion — plus aucun `glab mr` **prescrit** dans cette commande.

    Le discriminant entre prescription et citation est ici le bloc `>` d'en-tête : la commande a le
    droit — le devoir, même — de **nommer** la forme qu'elle remplace pour dire de ne pas y
    revenir, et c'est là qu'elle le fait. Ailleurs, une ligne qui nomme `glab mr` est une consigne.
    Même parti pris que les deux tests ci-dessus, dont l'heuristique est le début de ligne.
    """
    lignes = [
        (numero, nue)
        for nom, numero, nue in invocations_des_commandes()
        if nom == "branch-cleanup.md"
    ]
    assert any("lib.sh cleanup-merged" in nue for _, nue in lignes), (
        "/branch-cleanup doit appeler `lib.sh cleanup-merged` — trouvé : "
        f"{[nue for _, nue in lignes]}"
    )

    commande = RACINE / ".claude" / "commands" / "branch-cleanup.md"
    fautives = [
        f"branch-cleanup.md:{numero}: {nue}"
        for numero, ligne in enumerate(commande.read_text(encoding="utf-8").splitlines(), 1)
        if "glab mr" in (nue := ligne.strip()) and not nue.startswith(">")
    ]
    assert fautives == [], (
        "lecture `glab mr` réintroduite dans /branch-cleanup (#309) — passer par lib.sh "
        "(`mr-state` rend un mot, `cleanup-merged` fait toute la boucle) :\n" + "\n".join(fautives)
    )


def test_branch_cleanup_garde_les_trois_fonctions_hors_du_helper() -> None:
    """Le revers de la délégation : `cleanup-merged` ne fait pas TOUT (#309).

    Trois fonctions restent à la commande — basculer sur `main` quand la branche courante est
    mergée (le helper ne touche jamais à celle du clone principal), supprimer la branche
    **distante** restée là (case « Delete source branch » décochée au merge), et poser le cycle de
    vie « Terminé » (le merge ferme le ticket sans toucher à aucun label, docs/10 §3). Déléguer en
    les perdant au passage transformerait une économie de tokens en régression silencieuse.
    """
    texte = (RACINE / ".claude" / "commands" / "branch-cleanup.md").read_text(encoding="utf-8")
    manquantes = [
        forme
        for forme in ("git checkout main", "git push origin --delete", "reconcile-workflow")
        if forme not in texte
    ]
    assert manquantes == [], (
        "/branch-cleanup a perdu une fonction que `cleanup-merged` ne couvre pas (#309) : "
        f"{manquantes}"
    )


def test_review_queue_rend_la_plus_ancienne_d_abord_avec_son_anciennete(depot: Depot) -> None:
    aujourdhui = date.today()
    depot.pose_etat(
        graphql=[
            {
                "contient": ["mergeRequests(state: opened, sort: CREATED_ASC"],
                "reponse": {
                    "data": {
                        "project": {
                            "mergeRequests": {
                                "nodes": [
                                    {
                                        "iid": "10",
                                        "title": "Draft: Socle du chantier",
                                        "createdAt": f"{aujourdhui - timedelta(days=6)}T09:00:00Z",
                                        "draft": True,
                                        "sourceBranch": "chore/201-socle",
                                        "author": {"username": "bea"},
                                        "reviewers": {"nodes": []},
                                        "headPipeline": {"status": "FAILED"},
                                    },
                                    {
                                        "iid": "11",
                                        "title": "Écran de suivi",
                                        "createdAt": f"{aujourdhui - timedelta(days=1)}T09:00:00Z",
                                        "draft": False,
                                        "sourceBranch": "feat/202-ecran",
                                        "author": {"username": "cam"},
                                        "reviewers": {"nodes": [{"username": "dan"}]},
                                        "headPipeline": {"status": "SUCCESS"},
                                    },
                                ]
                            }
                        }
                    }
                },
            }
        ]
    )
    acheve = depot.lib("review-queue")
    assert acheve.returncode == 0, acheve.stderr
    lignes = colonnes(acheve.stdout)
    assert [ligne[0] for ligne in lignes] == ["10", "11"]           # la plus ancienne en tête
    assert [ligne[1] for ligne in lignes] == ["6", "1"]             # ancienneté en jours
    assert [ligne[2] for ligne in lignes] == ["draft", "ready"]
    assert [ligne[3] for ligne in lignes] == ["failed", "success"]
    assert [ligne[5] for ligne in lignes] == ["-", "dan"]           # relecteur, « - » si personne
    # Le préfixe « Draft: » est retiré du titre : l'information est déjà dans la colonne `etat`.
    assert lignes[0][7] == "Socle du chantier"


# =================================================================================================
# Retard sur origin/main (#163)
# =================================================================================================


def prepare_retard(depot: Depot, fichier_main: str) -> None:
    """Branche de ticket qui touche `fichier-a`, puis un commit sur origin/main."""
    depot.git("checkout", "--quiet", "-b", "chore/900-essai")
    depot.commit("fichier-a.txt", "travail du ticket\n", "chore(essai): travail\n\nRefs #900")
    depot.git("checkout", "--quiet", "main")
    depot.commit(fichier_main, "avancée de main\n", "chore(main): avancée")
    depot.git("push", "--quiet", "origin", "main")
    depot.git("checkout", "--quiet", "chore/900-essai")


def test_behind_main_ne_dit_rien_quand_la_branche_est_a_jour(depot: Depot) -> None:
    depot.git("checkout", "--quiet", "-b", "chore/900-essai")
    depot.commit("fichier-a.txt", "travail\n", "chore(essai): travail\n\nRefs #900")
    acheve = depot.lib("behind-main")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "à jour avec origin/main (1 commit(s) d'avance)" in acheve.stdout


def test_behind_main_annonce_un_rebase_serein_sans_fichier_commun(depot: Depot) -> None:
    prepare_retard(depot, fichier_main="fichier-b.txt")
    acheve = depot.lib("behind-main")
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "en retard : 1 commit(s) derrière origin/main" in acheve.stdout
    assert "aucun fichier modifié des deux côtés" in acheve.stdout
    assert "git fetch origin main && git rebase origin/main" in acheve.stdout


def test_behind_main_signale_le_conflit_probable_et_nomme_les_fichiers(depot: Depot) -> None:
    """C'est le signal qui manquait sur les fichiers aimants à conflits (CLAUDE.md, docs/10…)."""
    prepare_retard(depot, fichier_main="fichier-a.txt")
    acheve = depot.lib("behind-main")
    assert acheve.returncode == 4, acheve.stdout + acheve.stderr
    assert "conflit probable — 1 fichier(s) modifié(s) des deux côtés" in acheve.stdout
    assert "- fichier-a.txt" in acheve.stdout


def test_behind_main_ne_rebase_ni_ne_pousse_jamais(depot: Depot) -> None:
    """Purement consultatif : un rebase appellerait un force-push, interdit (docs/10 §6)."""
    prepare_retard(depot, fichier_main="fichier-a.txt")
    avant = depot.git("rev-parse", "HEAD")
    depot.lib("behind-main")
    assert depot.git("rev-parse", "HEAD") == avant
    assert depot.git("branch", "--show-current") == "chore/900-essai"


def test_behind_main_sur_main_n_a_rien_a_comparer(depot: Depot) -> None:
    acheve = depot.lib("behind-main", "main")
    assert acheve.returncode == 0
    assert "rien à comparer" in acheve.stdout


# =================================================================================================
# Conflit RÉEL avec origin/main (#303)
# =================================================================================================
#
# `behind-main` ci-dessus répond « ces fichiers sont modifiés des deux côtés » ; `mr-conflict`
# répond « git sait-il fusionner ? ». La différence n'est pas cosmétique : c'est elle qui décide
# si /mr-fix va résoudre un conflit ou n'a rien à faire.


def prepare_meme_fichier_regions_disjointes(depot: Depot) -> None:
    """Le cas des fichiers aimants du dépôt : les deux côtés touchent CLAUDE.md, sans se croiser.

    C'est le contre-exemple qui justifie le helper — `behind-main` crie au conflit probable
    (le fichier est modifié des deux côtés) là où le merge passe tout seul.
    """
    lignes = [f"ligne {n}\n" for n in range(1, 21)]
    depot.commit("aimant.txt", "".join(lignes), "chore(base): fichier aimant")
    depot.git("push", "--quiet", "origin", "main")

    depot.git("checkout", "--quiet", "-b", "chore/900-essai")
    debut = lignes.copy()
    debut[0] = "ligne 1 — retouchée par le ticket\n"
    depot.commit("aimant.txt", "".join(debut), "chore(essai): en-tête\n\nRefs #900")

    depot.git("checkout", "--quiet", "main")
    fin = lignes.copy()
    fin[19] = "ligne 20 — retouchée par main\n"
    depot.commit("aimant.txt", "".join(fin), "chore(main): pied de fichier")
    depot.git("push", "--quiet", "origin", "main")
    depot.git("checkout", "--quiet", "chore/900-essai")


def test_mr_conflict_dit_propre_la_ou_behind_main_croit_au_conflit(depot: Depot) -> None:
    """La raison d'être du helper : `behind-main` est pessimiste, `merge-tree` tranche."""
    prepare_meme_fichier_regions_disjointes(depot)

    pessimiste = depot.lib("behind-main")
    assert pessimiste.returncode == 4, pessimiste.stdout + pessimiste.stderr
    assert "conflit probable" in pessimiste.stdout
    assert "- aimant.txt" in pessimiste.stdout

    reel = depot.lib("mr-conflict")
    assert reel.returncode == 0, reel.stdout + reel.stderr
    assert "se merge proprement dans origin/main" in reel.stdout


def test_mr_conflict_nomme_les_fichiers_reellement_en_conflit(depot: Depot) -> None:
    prepare_retard(depot, fichier_main="fichier-a.txt")
    acheve = depot.lib("mr-conflict")
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "en conflit avec origin/main — 1 fichier(s)" in acheve.stdout
    assert "- fichier-a.txt" in acheve.stdout
    # La résolution proposée est un MERGE : un rebase appellerait un force-push (docs/10 §6).
    # `behind-main`, lui, propose bien `git rebase origin/main` — ici ce serait un contresens.
    assert "git merge origin/main" in acheve.stdout
    assert "git rebase" not in acheve.stdout


def test_mr_conflict_ne_touche_ni_a_l_arbre_ni_a_l_index(depot: Depot) -> None:
    """Lecture seule : ni checkout, ni index — d'où l'appel possible sur une branche non sortie."""
    prepare_retard(depot, fichier_main="fichier-a.txt")
    avant_tete = depot.git("rev-parse", "HEAD")
    avant_branche = depot.git("branch", "--show-current")

    depot.lib("mr-conflict")

    assert depot.git("rev-parse", "HEAD") == avant_tete
    assert depot.git("branch", "--show-current") == avant_branche
    assert depot.git("status", "--porcelain") == ""
    # Aucun appel GitLab : le verdict est purement local, donc disponible sans réseau ni compte.
    assert depot.appels() == []


def test_mr_conflict_juge_une_branche_qu_on_ne_sort_pas(depot: Depot) -> None:
    """Le cas d'usage réel de /mr-fix : juger la branche d'une MR depuis le clone principal."""
    prepare_retard(depot, fichier_main="fichier-a.txt")
    depot.git("checkout", "--quiet", "main")

    acheve = depot.lib("mr-conflict", "chore/900-essai")
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "- fichier-a.txt" in acheve.stdout
    assert depot.git("branch", "--show-current") == "main"


def test_mr_conflict_sur_main_n_a_rien_a_merger(depot: Depot) -> None:
    acheve = depot.lib("mr-conflict", "main")
    assert acheve.returncode == 0
    assert "rien à merger" in acheve.stdout


def test_mr_conflict_ne_prend_pas_une_erreur_pour_un_conflit(depot: Depot) -> None:
    """Sans ancêtre commun, git rend 128 — le confondre avec le 1 d'un conflit (docs/10 §8.3)
    enverrait /mr-fix résoudre un merge impossible."""
    # `--orphan` seul suffit : le commit qui suit est une RACINE, donc sans ancêtre commun avec
    # main. Surtout, ne rien effacer de l'arbre — le dépôt jetable y porte le `lib.sh` sous test.
    depot.git("checkout", "--quiet", "--orphan", "chore/901-orpheline")
    depot.commit("orpheline.txt", "sans ancêtre\n", "chore(orpheline): première racine")

    acheve = depot.lib("mr-conflict")
    assert acheve.returncode == 1, acheve.stdout + acheve.stderr
    assert "merge impossible à évaluer" in acheve.stderr
    assert "en conflit" not in acheve.stdout


def test_mr_conflict_sans_branche_ni_argument_refuse_proprement(depot: Depot) -> None:
    tete = depot.git("rev-parse", "HEAD")
    depot.git("checkout", "--quiet", tete)          # HEAD détachée
    acheve = depot.lib("mr-conflict")
    assert acheve.returncode == 2, acheve.stdout + acheve.stderr
    assert "branche indéterminée" in acheve.stderr


# =================================================================================================
# Garde-fou de clôture : la session traite-t-elle bien ce ticket ? (#164)
# =================================================================================================


@pytest.mark.parametrize(
    ("branche", "attendu"),
    [
        ("chore/164-garde-fou-de-cloture", "164"),
        ("feat/6-boucle-orchestration", "6"),
        ("chore/164", "164"),           # slug toléré absent : c'est l'iid qui porte l'information
    ],
)
def test_branch_iid_lit_l_iid_du_nom_de_branche(depot: Depot, branche: str, attendu: str) -> None:
    assert depot.lib("branch-iid", branche).stdout.strip() == attendu


@pytest.mark.parametrize("branche", ["main", "master", "brouillon", "chore/sans-iid"])
def test_branch_iid_reste_muet_hors_convention(depot: Depot, branche: str) -> None:
    acheve = depot.lib("branch-iid", branche)
    assert acheve.returncode == 1
    assert acheve.stdout.strip() == ""


def test_close_guard_valide_une_session_coherente(depot: Depot) -> None:
    depot.git("checkout", "--quiet", "-b", "chore/164-garde-fou")
    depot.pose_etat(
        graphql=[regle_owner("En cours", [MOI])]
    )
    acheve = depot.lib("close-guard", "164")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "cohérents" in acheve.stdout
    assert f"dont moi ({MOI})" in acheve.stdout


def test_close_guard_detecte_le_decalage_ticket_branche(depot: Depot) -> None:
    """Le contrôle FORT : `/ticket-finish 158` depuis `chore/163-…` poserait la MR sur #158."""
    depot.git("checkout", "--quiet", "-b", "chore/163-retard-sur-main")
    depot.pose_etat(
        graphql=[regle_owner("En cours", [MOI])]
    )
    acheve = depot.lib("close-guard", "158")
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "décalage ticket ↔ branche" in acheve.stdout
    assert "porte le ticket #163, pas #158" in acheve.stdout


def test_close_guard_signale_un_ticket_appartenant_a_un_tiers(depot: Depot) -> None:
    depot.git("checkout", "--quiet", "-b", "chore/164-garde-fou")
    depot.pose_etat(
        graphql=[regle_owner("En cours", ["bea"])]
    )
    acheve = depot.lib("close-guard", "164")
    assert acheve.returncode == 4, acheve.stdout + acheve.stderr
    assert "appartient à quelqu'un d'autre" in acheve.stdout


def test_close_guard_dit_la_coherence_invérifiable_sur_une_branche_sans_iid(depot: Depot) -> None:
    depot.pose_etat(
        graphql=[regle_owner("À faire", [])]
    )
    acheve = depot.lib("close-guard", "164")           # on est resté sur main
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr
    assert "aucun iid dans son nom" in acheve.stdout


def test_close_guard_ne_prend_pas_une_lecture_muette_pour_un_feu_vert(depot: Depot) -> None:
    """Le sens du doute va vers le refus : ticket illisible ⇒ verdict partiel, pas « libre »."""
    depot.git("checkout", "--quiet", "-b", "chore/164-garde-fou")
    depot.pose_etat(graphql=[])
    acheve = depot.lib("close-guard", "164")
    assert acheve.returncode == 1, acheve.stdout + acheve.stderr
    assert "propriété de #164 : indéterminée" in acheve.stdout


def test_close_guard_n_ecrit_jamais_rien(depot: Depot) -> None:
    """Consultatif comme `behind-main` : il constate, l'appelant décide."""
    depot.git("checkout", "--quiet", "-b", "chore/163-retard-sur-main")
    depot.pose_etat(
        graphql=[regle_owner("En cours", ["bea"])]
    )
    depot.lib("close-guard", "158")
    appels = depot.appels()
    assert [a for a in appels if a.startswith(ECRITURES)] == []
    assert "mutation" not in "\n".join(appels)


# =================================================================================================
# Contrôle doctor : un runner de projet est-il en ligne ? (#157)
# =================================================================================================


def section_runner(sortie: str) -> str:
    """Isole la section 7 du bilan (jusqu'au titre suivant)."""
    debut = sortie.index("7. Runner CI de projet")
    reste = sortie[debut:]
    suivant = reste.find("\n8. ")
    return reste if suivant < 0 else reste[:suivant]


def runners(*definitions: tuple[int, str, str]) -> list[dict]:
    """Règle REST pour l'inventaire des runners de PROJET (`?type=project_type`)."""
    return [
        {
            "contient": ["runners?type=project_type"],
            "brut": json.dumps(
                [
                    {"id": rid, "description": desc, "status": statut}
                    for rid, desc, statut in definitions
                ],
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        }
    ]


def test_doctor_valide_un_runner_de_projet_en_ligne(depot: Depot) -> None:
    depot.pose_etat(rest=runners((7, "runner-partage-atelier", "online")))
    acheve = depot.doctor()
    section = section_runner(acheve.stdout)
    assert "✓ runner de projet en ligne : runner-partage-atelier (#7)" in section
    assert "⚠" not in section


def test_doctor_avertit_quand_aucun_runner_n_est_en_ligne(depot: Depot) -> None:
    """Première cause de MR bloquée : les jobs restent « pending » sans que rien ne le dise."""
    depot.pose_etat(
        rest=runners((7, "runner-partage-atelier", "offline"), (9, "maestro-portable", "offline"))
    )
    section = section_runner(depot.doctor().stdout)
    assert "aucun runner de projet en ligne" in section
    assert "runner-partage-atelier (offline)" in section
    assert "maestro-portable (offline)" in section
    assert "scripts/gitlab/ensure-runner.sh" in section


def test_doctor_avertit_quand_aucun_runner_n_est_declare(depot: Depot) -> None:
    depot.pose_etat(rest=[{"contient": ["runners"], "brut": "[]"}])
    section = section_runner(depot.doctor().stdout)
    assert "aucun runner de projet déclaré" in section
    assert "scripts/gitlab/setup-runner.sh" in section


def test_doctor_reste_en_lecture_seule(depot: Depot) -> None:
    """Le bilan de santé n'écrit jamais rien : ni statut, ni label, ni MR (docs/10)."""
    depot.pose_etat(rest=runners((7, "runner-partage-atelier", "online")))
    depot.doctor()
    appels = depot.appels()
    assert "mutation" not in "\n".join(appels)          # aucune mutation GraphQL
    assert [a for a in appels if a.startswith(ECRITURES)] == []


# =================================================================================================
# Runner partagé toujours en ligne : `ensure-runner.sh` (#158)
# =================================================================================================


def statut_runner(rid: int, statut: str) -> dict:
    """Règle REST pour `GET runners/<id>` — plus spécifique que l'inventaire de projet."""
    return {
        "contient": [f"runners/{rid}"],
        "brut": json.dumps({"id": rid, "status": statut}, separators=(",", ":")),
    }


def nom_machine() -> str:
    """Le nom que `machine_nom()` lira — résolu ici pour ne pas coupler le test à un poste."""
    acheve = subprocess.run(  # noqa: S603
        [BASH or "bash", "-c", "hostname"], capture_output=True, text=True, check=False
    )
    return acheve.stdout.strip() or "machine"


def test_ensure_runner_est_no_op_quand_le_partage_tient_deja_la_ci(depot: Depot) -> None:
    """Le cœur de #158 : inutile de réveiller Docker sur un portable si la CI est servie."""
    depot.pose_etat(rest=runners((7, "runner-partage-atelier", "online")))
    acheve = depot.ensure_runner()
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "déjà en ligne (runner-partage-atelier)" in acheve.stderr
    assert "rien à démarrer" in acheve.stderr
    # Aucun runner INDIVIDUEL n'a même été résolu : on n'a pas touché au poste.
    assert not any("runners/" in a for a in depot.appels())


def test_ensure_runner_accepte_n_importe_quel_runner_de_projet_en_ligne(depot: Depot) -> None:
    """Tous les runners sont non-taggés : le premier en ligne suffit, quel que soit son hôte."""
    depot.pose_etat(
        rest=runners(
            (7, "runner-partage-atelier", "offline"),
            (9, f"maestro-{nom_machine()}", "offline"),
            (11, "maestro-poste-de-quelquun-dautre", "online"),
        )
    )
    acheve = depot.ensure_runner()
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#11" in acheve.stderr


def test_ensure_runner_strict_ne_regarde_que_le_runner_de_cette_machine(depot: Depot) -> None:
    """`--strict` rend compte du POSTE courant, pas de l'état global de la CI (setup-runner)."""
    depot.pose_etat(
        rest=[
            statut_runner(9, "online"),
            *runners((7, "runner-partage-atelier", "online"), (9, "maestro-ici", "online")),
        ]
    )
    acheve = depot.ensure_runner("--strict", MAESTRO_RUNNER_ID="9")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#9 déjà en ligne" in acheve.stderr
    # Le statut a bien été demandé au runner nommé, pas déduit de l'inventaire.
    assert any("runners/9" in a for a in depot.appels())


def test_ensure_runner_distingue_le_runner_local_du_partage_sur_le_meme_hote(depot: Depot) -> None:
    """Sur l'hôte du partagé, les DEUX descriptions portent le nom de la machine.

    D'où la préférence pour le motif du runner local (`maestro-<machine>`) : sans elle,
    `--strict` sur cette machine viserait le runner de l'équipe.
    """
    machine = nom_machine()
    depot.pose_etat(
        rest=[
            statut_runner(9, "online"),
            *runners(
                (7, f"runner-partage-{machine}", "offline"),
                (9, f"maestro-{machine}", "online"),
            ),
        ]
    )
    acheve = depot.ensure_runner("--strict")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#9" in acheve.stderr


def test_ensure_runner_le_dit_quand_aucun_runner_n_est_declare(depot: Depot) -> None:
    depot.pose_etat(rest=[{"contient": ["runners?type=project_type"], "brut": "[]"}])
    acheve = depot.ensure_runner()
    assert acheve.returncode == 1
    assert "aucun runner de projet trouvé" in acheve.stderr
    assert "setup-runner.sh" in acheve.stderr


def test_ensure_runner_dit_si_aucun_runner_ne_porte_le_nom_de_la_machine(depot: Depot) -> None:
    depot.pose_etat(
        rest=runners(
            (7, "runner-partage-ailleurs", "offline"),
            (9, "maestro-un-autre-poste", "offline"),
        )
    )
    acheve = depot.ensure_runner()
    assert acheve.returncode == 1
    assert "aucun au nom de cette machine" in acheve.stderr
    assert "MAESTRO_RUNNER_ID" in acheve.stderr


def test_ensure_runner_echoue_proprement_sans_docker(depot: Depot) -> None:
    """Best-effort : câblé en `ensure-runner.sh || …`, son échec n'interrompt pas la clôture."""
    depot.pose_etat(
        rest=[statut_runner(9, "offline"), *runners((9, "maestro-ici", "offline"))]
    )
    acheve = depot.ensure_runner(MAESTRO_RUNNER_ID="9")
    assert acheve.returncode == 1
    assert "hors ligne — tentative de démarrage" in acheve.stderr
    assert "démon Docker toujours injoignable" in acheve.stderr


def test_ensure_runner_refuse_une_option_inconnue(depot: Depot) -> None:
    acheve = depot.ensure_runner("--force")
    assert acheve.returncode == 2
    assert "option inconnue" in acheve.stderr


def test_ensure_runner_s_arrete_si_glab_n_est_pas_authentifie(depot: Depot) -> None:
    depot.pose_etat(authentifie=False, rest=runners((7, "runner-partage-atelier", "online")))
    acheve = depot.ensure_runner()
    assert acheve.returncode == 1
    assert "glab non authentifié" in acheve.stderr


# =================================================================================================
# Création depuis un fichier : MR et notes (#233, parent #232)
# =================================================================================================
# Le texte long d'une MR ou d'un commentaire est la SEULE chose qu'une session autonome ne peut pas
# faire tenir sur une ligne de commande, et les deux replis naturels sont pires que le mal : la
# couche permissions découpe un appel sur ses SAUTS DE LIGNE et ne matche aucune SUBSTITUTION
# `$(…)` (docs/10 §11.7). D'où ces helpers, qui prennent un CHEMIN — le `$(cat …)` survit, mais à
# l'INTÉRIEUR du script, où aucune permission ne s'applique.
#
# Ce que ces tests gardent : le contenu arrive INTACT (c'est tout l'intérêt du détour par un
# fichier), l'appel est IDEMPOTENT (la création est la dernière action du ticket, elle doit
# supporter d'être rejouée), et un refus n'écrit RIEN — une MR sans description se découvrirait à
# la revue, quand tout est déjà commité.

BRANCHE = "chore/237-tests-doc-appels-dune-session-autonome-a"

#: Le texte porte tout ce qui rend une commande immatchable — sauts de ligne, `$(…)`, backquotes,
#: heredoc — plus des accents et un em-dash (le mojibake de #141). Il doit ressortir tel quel : ce
#: qui casse une ligne de commande ne doit rien casser du tout quand il voyage par fichier.
DESCRIPTION = (
    "Closes #237\n"
    "\n"
    "## Checklist\n"
    "- [x] Respecte les conventions de branche/commit — docs/10-workflow-git.md\n"
    "- [ ] Tests ajoutés/mis à jour si applicable\n"
    "\n"
    "Formes que la ligne de commande ne supporterait pas : `$(cat fichier)`, `whoami`,\n"
    "un heredoc `<<'EOF'`, et des accents « à é ù ».\n"
)


def regle_titre(iid: int, titre: str) -> dict:
    """Réponse REST d'un ticket : `title` D'ABORD, celui du milestone ensuite.

    L'ordre n'est pas décoratif — `gl_issue_title` prend la PREMIÈRE occurrence de `"title":"` dans
    la charge, exactement comme `gl_get_description` pour la description. Le jour où GitLab
    renverrait le milestone avant, ce test tomberait, et c'est ce qu'on veut.
    """
    return {
        "contient": [f"issues/{iid}"],
        "reponse": {
            "iid": iid,
            "title": titre,
            "description": "peu importe",
            "milestone": {"title": "Phase 7 — Projets & espace de travail réel"},
        },
    }


def regle_mr_de_branche(iid: str | None) -> dict:
    """Réponse à la résolution « quelle MR ouverte porte cette branche ? » (`gl_mr_iid`)."""
    return {
        "contient": ["mergeRequests(state: opened"],
        "reponse": {
            "data": {"project": {"mergeRequests": {"nodes": [] if iid is None else [{"iid": iid}]}}}
        },
    }


def sur_une_branche(depot: Depot, branche: str = BRANCHE) -> None:
    depot.git("checkout", "--quiet", "-b", branche)


def appel(depot: Depot, *debut: str) -> str:
    """L'unique appel `glab` commençant par ces arguments — échoue s'il y en a zéro ou deux."""
    prefixe = "\t".join(debut)
    trouves = [ligne for ligne in depot.appels() if ligne.startswith(prefixe)]
    assert len(trouves) == 1, f"un seul {prefixe!r} attendu, reçu {len(trouves)} : {depot.appels()}"
    return trouves[0]


def valeur_option(ligne: str, option: str) -> str:
    """La valeur qui suit `--option` dans un appel journalisé (arguments séparés par des TAB)."""
    champs = ligne.split("\t")
    assert option in champs, f"{option} absent de {champs}"
    return champs[champs.index(option) + 1]


def journalise(texte: str) -> str:
    """Le texte tel que le journal du glab factice le rend — sauts de ligne échappés.

    `rstrip` parce qu'une substitution de commande mange les sauts de ligne FINAUX, et seulement
    ceux-là : c'est la seule altération que le détour par un fichier laisse passer.
    """
    return texte.rstrip("\n").replace("\n", "\\n")


def fichier_description(depot: Depot, contenu: str = DESCRIPTION) -> Path:
    chemin = depot.racine / "description-mr.md"
    chemin.write_text(contenu, encoding="utf-8", newline="\n")
    return chemin


def ecritures(depot: Depot) -> list[str]:
    """Les appels `glab` qui ÉCRIVENT côté GitLab — vides tant qu'un helper s'abstient."""
    return [ligne for ligne in depot.appels() if any(verbe in ligne for verbe in ECRITURES)]


def test_create_mr_ouvre_une_draft_avec_le_titre_du_ticket_et_le_fichier(depot: Depot) -> None:
    """Le cas nominal : titre lu dans GitLab, description lue dans le fichier, MR en Draft.

    Draft et `--remove-source-branch` sont dans le contrat : un run produit N MR **à relire**, il
    ne dé-drafte ni ne merge jamais (docs/10 §11), et la branche part au merge comme partout.
    """
    depot.pose_etat(
        graphql=[regle_mr_de_branche(None)],
        rest=[regle_titre(237, "Tests + doc : appels d'une session autonome — allowlist")],
    )
    sur_une_branche(depot)
    fichier = fichier_description(depot)

    acheve = depot.lib("create-mr", "237", str(fichier))
    assert acheve.returncode == 0, acheve.stderr

    ligne = appel(depot, "mr", "create")
    for drapeau in ("--draft", "--remove-source-branch", "--yes"):
        assert f"\t{drapeau}" in ligne, f"{drapeau} attendu dans {ligne}"
    assert valeur_option(ligne, "--target-branch") == "main"
    assert valeur_option(ligne, "--source-branch") == BRANCHE
    assert valeur_option(ligne, "--title").startswith("Tests + doc")
    # L'em-dash du titre survit : il traverse REST puis un argument shell sans repasser par un
    # décodage approximatif (#141).
    assert "—" in valeur_option(ligne, "--title")


def test_create_mr_transmet_le_fichier_octet_pour_octet(depot: Depot) -> None:
    """Le cœur du détour par un fichier : ce qui casserait une ligne de commande passe intact.

    Sauts de ligne, `$(…)`, backquotes et heredoc arrivent LITTÉRAUX côté `glab` — non réévalués,
    non tronqués. Si quelqu'un « simplifiait » un jour le helper en passant le texte autrement,
    c'est ici que ça se verrait.
    """
    depot.pose_etat(graphql=[regle_mr_de_branche(None)], rest=[regle_titre(237, "Titre")])
    sur_une_branche(depot)
    fichier = fichier_description(depot)

    assert depot.lib("create-mr", "237", str(fichier)).returncode == 0

    recue = valeur_option(appel(depot, "mr", "create"), "--description")
    assert recue == journalise(DESCRIPTION)
    assert "$(cat fichier)" in recue, "la substitution n'a pas été réévaluée : c'est du texte"


def test_create_mr_met_a_jour_la_mr_deja_ouverte_au_lieu_d_echouer(depot: Depot) -> None:
    """Idempotence : la création est la DERNIÈRE action du ticket, elle doit se rejouer.

    Reprise de session, second passage après un commit de plus : `/ticket-finish` repasse ici et
    ne doit ni échouer ni ouvrir une seconde MR sur la même branche.
    """
    depot.pose_etat(graphql=[regle_mr_de_branche("77")], rest=[regle_titre(237, "Titre")])
    sur_une_branche(depot)
    fichier = fichier_description(depot)

    acheve = depot.lib("create-mr", "237", str(fichier))
    assert acheve.returncode == 0, acheve.stderr
    assert "!77" in acheve.stdout, "la MR retrouvée est nommée"
    assert "merge_requests/77" in acheve.stdout, "l'URL reste rendue, comme à la création"

    assert not [ligne for ligne in depot.appels() if ligne.startswith("mr\tcreate")], \
        "une seconde MR aurait été ouverte sur la même branche"
    assert valeur_option(appel(depot, "mr", "update"), "--description") == journalise(DESCRIPTION)


def test_create_mr_refuse_depuis_main_sans_rien_ecrire(depot: Depot) -> None:
    """`main` n'a pas de MR à ouvrir : le dire vaut mieux qu'un appel qui échouera plus loin."""
    depot.pose_etat(graphql=[regle_mr_de_branche(None)], rest=[regle_titre(237, "Titre")])
    fichier = fichier_description(depot)

    acheve = depot.lib("create-mr", "237", str(fichier))
    assert acheve.returncode == 1
    assert "main" in acheve.stderr
    assert not ecritures(depot)


@pytest.mark.parametrize(
    ("nom", "contenu", "attendu"),
    [("absent.md", None, "introuvable"), ("vide.md", "", "vide")],
)
def test_create_mr_refuse_un_fichier_inutilisable(
    depot: Depot, nom: str, contenu: str | None, attendu: str
) -> None:
    """Une MR sans description est pire qu'aucune MR : le helper s'arrête AVANT d'écrire.

    Le fichier vide est le cas réel — un `Write` qui n'a rien écrit, ou le chemin de scratchpad
    d'une session précédente.
    """
    depot.pose_etat(graphql=[regle_mr_de_branche(None)], rest=[regle_titre(237, "Titre")])
    sur_une_branche(depot)
    chemin = depot.racine / nom
    if contenu is not None:
        chemin.write_text(contenu, encoding="utf-8", newline="\n")

    acheve = depot.lib("create-mr", "237", str(chemin))
    assert acheve.returncode == 1
    assert attendu in acheve.stderr
    assert not ecritures(depot)


def test_create_mr_signale_un_titre_illisible_plutot_que_d_en_inventer_un(depot: Depot) -> None:
    """Sans titre, pas de MR : une MR intitulée « » ne se remarquerait qu'à la revue."""
    depot.pose_etat(graphql=[regle_mr_de_branche(None)], rest=[])
    sur_une_branche(depot)
    fichier = fichier_description(depot)

    acheve = depot.lib("create-mr", "237", str(fichier))
    assert acheve.returncode == 1
    assert "#237" in acheve.stderr
    assert not [ligne for ligne in depot.appels() if ligne.startswith("mr\tcreate")]


def test_issue_note_poste_le_fichier_tel_quel(depot: Depot) -> None:
    """Le pendant de `create-mr` : `-m "$(cat …)"` n'est pas matchable non plus (#186)."""
    note = "Note de travail — « à relire ».\n\nDeuxième paragraphe.\n"
    fichier = fichier_description(depot, note)

    acheve = depot.lib("issue-note", "237", str(fichier))
    assert acheve.returncode == 0, acheve.stderr
    assert valeur_option(appel(depot, "issue", "note", "237"), "-m") == journalise(note)


def test_issue_note_refuse_un_fichier_vide_sans_rien_poster(depot: Depot) -> None:
    fichier = fichier_description(depot, "")
    acheve = depot.lib("issue-note", "237", str(fichier))
    assert acheve.returncode == 1
    assert "vide" in acheve.stderr
    assert not ecritures(depot)


def test_issue_title_rend_le_titre_en_utf8_intact(depot: Depot) -> None:
    """Lecture seule, et fidèle : c'est ce titre qui devient celui de la MR."""
    depot.pose_etat(rest=[regle_titre(237, "Tests + doc — appels « autonomes » d'une session")])
    acheve = depot.lib("issue-title", "237")
    assert acheve.returncode == 0, acheve.stderr
    assert acheve.stdout.strip() == "Tests + doc — appels « autonomes » d'une session"
    assert not ecritures(depot)


def test_les_helpers_de_creation_sont_annonces_par_l_usage(depot: Depot) -> None:
    """Un helper qu'on ne trouve pas n'existe pas : c'est l'usage qui l'apprend à une session.

    Le refus d'un `glab mr create` multi-ligne tombe **sans humain pour l'expliquer** ; la seule
    chose que la session puisse lire pour s'en sortir est la sortie d'usage de `lib.sh`.
    """
    usage = depot.lib().stderr
    for verbe in ("create-mr", "issue-note", "issue-title"):
        assert verbe in usage, f"{verbe} absent de l'usage de lib.sh"
    assert "fichier" in usage.lower()


# =================================================================================================
# « Quelqu'un s'occupe-t-il encore de ce ticket ? » — la détection des orphelins (#328, parent #327)
# =================================================================================================
#
# Ce que ces tests épinglent est le GARDE-FOU, et lui seul (les critères du lot le disent : le reste
# des tests part au lot final #330). L'asymétrie est le cœur du sujet — désigner à tort le ticket
# d'une session vivante coûte infiniment plus cher que de rater un orphelin d'un tour, puisque #329
# rendra l'orphelin prenable —, donc c'est le faux positif qui est traqué ici, pas le faux négatif.
#
# Deux sources à éprouver, et elles ne se valent pas : la CARTE DU PILOTE fait foi (elle nomme un
# processus vérifiable) là où la fraîcheur du worktree n'est qu'une DÉDUCTION. La carte ne prouve
# jamais la mort, seulement la vie.


def _regle_backlog(statuts: dict[str, str]) -> list[dict]:
    """Règle de réponse au backlog OUVERT — « iid : cycle de vie » pour chaque ticket."""
    return [
        {
            "contient": ["workItems(state: opened"],
            "reponse": reponse_backlog(
                [
                    noeud_ticket(iid, f"Ticket {iid}", statut, ["type::infra"], [MOI])
                    for iid, statut in statuts.items()
                ]
            ),
        }
    ]


def _worktree(depot: Depot, iid: str) -> Path:
    """Monte un worktree du dépôt jetable sur la branche du ticket, et rend son chemin."""
    chemin = depot.racine.parent / "worktrees" / f"{iid}-essai"
    depot.git("worktree", "add", "--quiet", "-b", f"chore/{iid}-essai", str(chemin), "main")
    return chemin


def _index_de(chemin: Path) -> Path:
    """L'index git du worktree — le témoin que touche tout `git add`/`commit`/`status`."""
    assert GIT is not None
    brut = subprocess.run(  # noqa: S603
        [GIT, "-C", str(chemin), "rev-parse", "--git-path", "index"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return Path(brut) if Path(brut).is_absolute() else chemin / brut


def _silence(chemin: Path, secondes: int) -> None:
    """Recule TOUTES les dates d'écriture du worktree : c'est ça, « plus personne dessus ».

    Toutes, et pas seulement l'index : la mesure prend le maximum de trois témoins (index, fichiers
    rendus par `git status`, atelier de session). En reculer un seul laisserait les autres frais, et
    le verdict « vivant » tomberait pour une raison étrangère à ce que le test prétend éprouver.
    """
    quand = time.time() - secondes
    cibles = [_index_de(chemin), chemin]
    cibles += [f for f in chemin.rglob("*") if ".git" not in f.parts]
    for cible in cibles:
        try:
            os.utime(cible, (quand, quand))
        except OSError:      # un chemin disparu entre-temps ne doit pas casser le harnais
            pass


def _run_vivant(depot: Depot, iid: str, run_id: str = "20260811-090000") -> subprocess.Popen:
    """Un run tenant `iid` en vol : plan, témoin de session, et un vrai processus posant sa carte.

    La carte est écrite par `pilote.sh` lui-même, jamais fabriquée à la main — la naissance du
    processus est en ticks et ne se devine pas depuis Python, si bien qu'un test qui inventerait le
    format ne vérifierait plus que sa propre invention. Même recette que les tests de l'arrêt dans
    `test_orchestrate.py`, et pour la même raison : la vivacité d'un processus ne se simule pas.
    """
    assert BASH is not None
    dossier = depot.racine / ".maestro" / "orchestrate" / run_id
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "plan.tsv").write_text(
        "# rang\tiid\tparent\tprio\tgroupe\ttitre\n"
        f"1\t{iid}\t-\thaute\t-\tTicket en vol\n",
        encoding="utf-8", newline="\n",
    )
    # Témoin de session SANS ligne de bilan dans resume.tsv : c'est ce couple-là qui veut dire
    # « pris en main, pas encore jugé », donc « en vol » (même définition que status.sh).
    (dossier / f"{iid}.session").write_text("uuid-de-session\n", encoding="utf-8", newline="\n")

    script = depot.racine.parent / "faux-pilote.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'. "{(depot.racine / "scripts" / "orchestrate" / "pilote.sh").as_posix()}"\n'
        'pilote_ecrit "$1"\n'
        'sleep "$2"\n',
        encoding="utf-8", newline="\n",
    )
    script.chmod(0o755)
    proc = subprocess.Popen(  # noqa: S603
        [BASH, str(script), str(dossier), "120"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # On attend une carte COMPLÈTE et non seulement présente : le fichier apparaît dès l'ouverture
    # de la redirection, et `hote` est le dernier champ posé.
    carte = dossier / "pid"
    for _ in range(200):
        if carte.exists() and "hote=" in carte.read_text(encoding="utf-8", errors="replace"):
            return proc
        time.sleep(0.05)
    proc.kill()
    raise AssertionError("le pilote factice n'a jamais posé sa carte")


def _verdicts(acheve: subprocess.CompletedProcess[str]) -> dict[str, str]:
    """La sortie `--tsv` en « iid -> verdict »."""
    return {ligne[0]: ligne[1] for ligne in colonnes(acheve.stdout)}


# --- Le garde-fou : ne jamais désigner le ticket d'une session vivante ----------------------------


def test_un_worktree_qui_vient_d_etre_ecrit_n_est_jamais_orphelin(depot: Depot) -> None:
    depot.pose_etat(graphql=_regle_backlog({"328": "En cours"}))
    _worktree(depot, "328")

    acheve = depot.lib("reconcile-en-cours", "--tsv")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert _verdicts(acheve) == {"328": "vivant"}
    assert "déduction" in acheve.stdout, "la source d'un verdict déduit doit être annoncée"


def test_un_silence_de_cinq_heures_reste_vivant(depot: Depot) -> None:
    """Le seuil est GÉNÉREUX à dessein, et c'est ce qu'il protège qui le fixe.

    Une session qui épuise la limite d'usage dort jusqu'à son reset sans rien écrire, et `run.sh`
    l'attend jusqu'à 5 h 30. Un seuil plus court déclarerait abandonné un ticket dont la session
    attend légitimement — exactement le faux positif que #329 transformerait en reprise à tort.
    """
    depot.pose_etat(graphql=_regle_backlog({"328": "En cours"}))
    _silence(_worktree(depot, "328"), 5 * 3600)

    assert _verdicts(depot.lib("reconcile-en-cours", "--tsv")) == {"328": "vivant"}


def test_la_carte_du_pilote_protege_un_ticket_silencieux(depot: Depot) -> None:
    """LE test du lot : la carte fait foi, et elle l'emporte sur un worktree muet depuis longtemps.

    Une session peut très bien réfléchir des heures sans rien écrire — c'est précisément ce que la
    déduction ne sait pas distinguer d'une mort, et ce que la carte, elle, tranche.
    """
    depot.pose_etat(graphql=_regle_backlog({"328": "En cours"}))
    _silence(_worktree(depot, "328"), 48 * 3600)
    proc = _run_vivant(depot, "328")
    try:
        acheve = depot.lib("reconcile-en-cours", "--tsv")
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert _verdicts(acheve) == {"328": "vivant"}
        assert "carte du pilote" in acheve.stdout
    finally:
        proc.kill()
        proc.wait(timeout=30)


def test_une_carte_de_pilote_morte_ne_prouve_rien(depot: Depot) -> None:
    """L'asymétrie, dans l'autre sens : la carte prouve la VIE, jamais la mort.

    Un pilote arrêté au `taskkill` laisse sa carte derrière lui (aucun trap ne s'exécute). Elle ne
    doit ni sauver le ticket — c'est justement le mode de mort qui fabrique l'orphelin — ni le
    condamner : c'est la déduction qui reprend la main, et elle seule.
    """
    depot.pose_etat(graphql=_regle_backlog({"328": "En cours"}))
    _silence(_worktree(depot, "328"), 48 * 3600)
    dossier = depot.racine / ".maestro" / "orchestrate" / "20260810-141208"
    dossier.mkdir(parents=True)
    (dossier / "plan.tsv").write_text(
        "# rang\tiid\tparent\tprio\tgroupe\ttitre\n1\t328\t-\thaute\t-\tTicket\n",
        encoding="utf-8", newline="\n",
    )
    (dossier / "328.session").write_text("uuid\n", encoding="utf-8", newline="\n")
    # Une carte qui ne désigne personne : PID hors de portée, naissance impossible à confirmer.
    (dossier / "pid").write_text(
        "pid=4294967294\nwinpid=\nnaissance=1\nepoch=1\nhote=inconnu\n",
        encoding="utf-8", newline="\n",
    )

    acheve = depot.lib("reconcile-en-cours", "--tsv")
    assert _verdicts(acheve) == {"328": "orphelin"}
    assert "carte du pilote" not in acheve.stdout


def test_un_ticket_sans_worktree_ici_est_hors_de_portee_jamais_orphelin(depot: Depot) -> None:
    """La couverture est celle des worktrees de CETTE machine, et elle se dit.

    Un ticket travaillé sur le clone de quelqu'un d'autre n'apprend rien d'ici : le déclarer
    orphelin reviendrait à proposer de reprendre le travail d'un vivant.
    """
    depot.pose_etat(graphql=_regle_backlog({"316": "En cours"}))

    acheve = depot.lib("reconcile-en-cours", "--tsv")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert _verdicts(acheve) == {"316": "hors-portee"}


def test_seuls_les_tickets_en_cours_sont_examines(depot: Depot) -> None:
    """« En revue » a livré, « À faire » n'a jamais commencé : ni l'un ni l'autre n'est orphelin."""
    depot.pose_etat(
        graphql=_regle_backlog({"325": "En revue", "326": "À faire", "328": "En cours"})
    )
    for iid in ("325", "326", "328"):
        _silence(_worktree(depot, iid), 48 * 3600)

    acheve = depot.lib("reconcile-en-cours", "--tsv")
    assert _verdicts(acheve) == {"328": "orphelin"}


# --- La détection elle-même, et ce qu'elle ne fait pas --------------------------------------------


def test_un_worktree_silencieux_sans_pilote_est_un_orphelin(depot: Depot) -> None:
    depot.pose_etat(graphql=_regle_backlog({"325": "En cours"}))
    chemin = _worktree(depot, "325")
    _silence(chemin, 48 * 3600)

    acheve = depot.lib("reconcile-en-cours")
    assert acheve.returncode == 3, "un orphelin se dit aussi par le code de retour"
    assert "#325 orphelin" in acheve.stdout
    assert "déduction" in acheve.stdout, "une déduction s'annonce comme telle"
    assert chemin.exists(), "la détection ne retire jamais un worktree"


def test_la_detection_n_ecrit_rien_du_tout(depot: Depot) -> None:
    """Elle SIGNALE : ni label, ni assignation, ni worktree touchés. La reprise est #329."""
    depot.pose_etat(graphql=_regle_backlog({"325": "En cours"}))
    _silence(_worktree(depot, "325"), 48 * 3600)

    depot.lib("reconcile-en-cours")
    assert not ecritures(depot)


def test_check_est_accepte_et_ne_change_rien(depot: Depot) -> None:
    """Le verbe est en lecture seule par nature ; refuser `--check` serait un piège de famille."""
    depot.pose_etat(graphql=_regle_backlog({"325": "En cours"}))
    _silence(_worktree(depot, "325"), 48 * 3600)

    sans = depot.lib("reconcile-en-cours", "--tsv")
    avec = depot.lib("reconcile-en-cours", "--check", "--tsv")
    assert avec.returncode == sans.returncode
    assert _verdicts(avec) == _verdicts(sans) == {"325": "orphelin"}


def test_sauf_ecarte_le_ticket_qu_on_est_en_train_de_demarrer(depot: Depot) -> None:
    """`ensure` le passe : le ticket qu'on démarre est repris à l'instant même.

    Sans ça, /ticket-start sur un ticket laissé en plan la veille annoncerait comme orphelin celui
    qu'il est justement en train de reprendre — vrai une seconde, faux la suivante.
    """
    depot.pose_etat(graphql=_regle_backlog({"325": "En cours", "328": "En cours"}))
    for iid in ("325", "328"):
        _silence(_worktree(depot, iid), 48 * 3600)

    acheve = depot.lib("reconcile-en-cours", "--tsv", "--sauf", "328")
    assert _verdicts(acheve) == {"325": "orphelin"}


def test_auto_se_tait_sans_orphelin_et_parle_avec(depot: Depot) -> None:
    """Le mode des points de passage : le silence est le cas normal (comme `gc --auto`)."""
    depot.pose_etat(graphql=_regle_backlog({"328": "En cours"}))
    chemin = _worktree(depot, "328")

    muet = depot.lib("reconcile-en-cours", "--auto")
    assert muet.returncode == 0
    assert muet.stdout == ""

    _silence(chemin, 48 * 3600)
    bavard = depot.lib("reconcile-en-cours", "--auto")
    assert bavard.returncode == 3
    assert "#328 orphelin" in bavard.stdout
    assert "vivant" not in bavard.stdout, "en --auto, seuls les orphelins sont une nouvelle"


def test_le_backlog_illisible_ne_fait_pas_conclure_a_l_orphelin(depot: Depot) -> None:
    """Ne rien savoir n'autorise rien — même règle que le ramassage devant un `glab` muet."""
    depot.pose_etat(graphql=[])

    acheve = depot.lib("reconcile-en-cours")
    assert acheve.returncode == 1
    assert "orphelin" not in acheve.stdout


def test_le_verbe_est_annonce_par_l_usage(depot: Depot) -> None:
    """Un helper qu'on ne trouve pas n'existe pas — et #329 partira de celui-ci."""
    usage = depot.lib().stderr
    assert "reconcile-en-cours" in usage
    assert "orphelin" in usage


# =================================================================================================
# La reprise : rendre un orphelin prenable, sur un geste explicite (#329, parent #327)
# =================================================================================================
#
# Le lot précédent DÉSIGNE, celui-ci REND PRENABLE — et « prenable » est une CONJONCTION, parce que
# le filtre de `queue.sh` en est une : « À faire » ET libre. Trois choses sont épinglées ici, et
# elles ne se valent pas :
#
#   1. LE GARDE-FOU — ne jamais reprendre un ticket vivant. C'est le seul défaut de ce lot qui
#      coûterait cher : reprendre le ticket d'une session en cours le lui retire (le prochain run
#      l'assigne à quelqu'un d'autre), là où rater un orphelin ne coûte qu'un tour.
#   2. LE TRAVAIL PRÉSERVÉ — la reprise n'écrit QUE dans GitLab. #316, c'est 2047 lignes commitées
#      et jamais poussées : un verbe qui « nettoierait » au passage détruirait exactement ce qu'il
#      est censé sauver.
#   3. LE BORNAGE — ses tests ne sont PAS différés au lot final (contrairement au reste), et c'est
#      dit dans le ticket : sans plafond, un ticket qui retombe à chaque run transforme la reprise
#      en boucle, sur un quota partagé. C'est la seule ligne qui sépare un geste d'un emballement.


def _labels_workflow_gids() -> dict:
    """Règle de réponse à la liste des labels du scope — la brique qui permet de retirer les
    cinq autres. GID volontairement non contigus : rien ne doit pouvoir en deviner un à partir
    d'un autre.
    """
    return {
        "contient": ["labels(searchTerm:"],
        "reponse": {
            "data": {
                "project": {
                    "labels": {
                        "nodes": [
                            {"id": f"gid://gitlab/ProjectLabel/{gid}", "title": f"workflow::{slug}"}
                            for slug, gid in (
                                ("a-faire", 9007), ("en-cours", 31), ("en-revue", 4512),
                                ("termine", 88), ("abandonne", 1203), ("doublon", 677),
                            )
                        ]
                    }
                }
            }
        },
    }


WORKITEM_GID = "gid://gitlab/WorkItem/55501"


def _regles_reprise(statuts: dict[str, str]) -> list[dict]:
    """Tout ce qu'il faut pour qu'une reprise aboutisse : backlog, labels, work item, mutation."""
    return [
        {
            "contient": ["workItemUpdate"],
            "reponse": {"data": {"workItemUpdate": {"errors": []}}},
        },
        _labels_workflow_gids(),
        {
            # La résolution du GID du work item. Le fragment `nodes { id }` la distingue de la
            # requête cycle de vie + assignés, qui porte `WorkItemWidgetAssignees`.
            "contient": ["workItems(iids:", "nodes { id }"],
            "reponse": {"data": {"project": {"workItems": {"nodes": [{"id": WORKITEM_GID}]}}}},
        },
        *_regle_backlog(statuts),
    ]


def _mutations(depot: Depot) -> list[str]:
    """Les mutations GraphQL reçues — ce qui distingue « repris » de « refusé »."""
    return [ligne for ligne in depot.appels() if "workItemUpdate" in ligne]


def _gids(mutation: str, champ: str) -> list[str]:
    trouve = re.search(rf"{champ}:\[(.*?)\]", mutation)
    return re.findall(r'"([^"]+)"', trouve.group(1)) if trouve else []


def _registre(depot: Depot) -> Path:
    return depot.racine / ".maestro" / "orchestrate" / "reprises.tsv"


def _run_juge(
    depot: Depot, iid: str, verdict: str, raison: str, run_id: str = "20260810-141208"
) -> None:
    """Un run passé qui a jugé ce ticket — la moitié « d'où il sort » de la trace."""
    dossier = depot.racine / ".maestro" / "orchestrate" / run_id
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "resume.tsv").write_text(
        "# iid\tverdict\tmr\tduree_s\tcout_usd\traison\n"
        f"{iid}\t{verdict}\t-\t2702\t14.75\t{raison}\n",
        encoding="utf-8", newline="\n",
    )


def _orphelin(depot: Depot, iid: str, statuts: dict[str, str] | None = None) -> Path:
    """Un ticket « En cours » dont le worktree est muet depuis deux jours."""
    depot.pose_etat(graphql=_regles_reprise(statuts or {iid: "En cours"}))
    chemin = _worktree(depot, iid)
    _silence(chemin, 48 * 3600)
    return chemin


# --- Le garde-fou : la reprise ne prend rien à personne -------------------------------------------


def test_reprendre_refuse_un_ticket_dont_quelqu_un_s_occupe(depot: Depot) -> None:
    """LE test du lot. Un worktree écrit à l'instant : quelqu'un travaille, on ne touche à rien.

    Le coût de l'erreur est asymétrique et c'est ce qui fixe le sens du refus — reprendre un ticket
    vivant le retire à sa session (le prochain run le prend et l'assigne), rater un orphelin ne
    coûte qu'un tour de boucle.
    """
    depot.pose_etat(graphql=_regles_reprise({"325": "En cours"}))
    _worktree(depot, "325")

    acheve = depot.lib("reprendre-en-cours", "325")
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "quelqu'un s'en occupe" in acheve.stdout
    assert _mutations(depot) == [], "un refus n'écrit RIEN côté GitLab"


def test_reprendre_refuse_un_ticket_hors_de_portee(depot: Depot) -> None:
    """Aucun worktree ici : ne rien savoir n'autorise rien — le ticket vit peut-être ailleurs.

    C'est la borne annoncée du dispositif (les worktrees de CETTE machine) tenue jusque dans le
    geste : elle ne se relâche pas au moment d'écrire.
    """
    depot.pose_etat(graphql=_regles_reprise({"316": "En cours"}))

    acheve = depot.lib("reprendre-en-cours", "316")
    assert acheve.returncode == 3
    assert "hors de portée" in acheve.stdout
    assert "--force" in acheve.stdout, "un refus doit dire par où passer quand on sait, soi"
    assert _mutations(depot) == []


def test_reprendre_refuse_un_ticket_qui_n_est_pas_en_cours(depot: Depot) -> None:
    """« En revue » a livré, « À faire » n'a jamais commencé : il n'y a rien à reprendre."""
    depot.pose_etat(graphql=_regles_reprise({"325": "En revue"}))
    _silence(_worktree(depot, "325"), 48 * 3600)

    acheve = depot.lib("reprendre-en-cours", "325")
    assert acheve.returncode == 3
    assert "n'est pas « En cours »" in acheve.stdout
    assert _mutations(depot) == []


def test_force_passe_outre_le_verdict_et_le_dit(depot: Depot) -> None:
    """Le geste de qui sait quelque chose que la machine ignore — jamais en silence."""
    depot.pose_etat(graphql=_regles_reprise({"316": "En cours"}))

    acheve = depot.lib("reprendre-en-cours", "--force", "316")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert len(_mutations(depot)) == 1
    assert "--force" in acheve.stdout, "lever le garde-fou se dit, sinon la sortie ment"


# --- Le geste lui-même : « À faire » ET libre, en une seule mutation ----------------------------


def test_reprendre_remet_a_faire_et_libere_dans_la_meme_mutation(depot: Depot) -> None:
    """La conjonction. Poser le cycle de vie sans libérer laisserait le ticket écarté par l'autre
    moitié du filtre de `queue.sh`, et l'inverse par la première — il resterait invisible.

    « En une seule mutation » n'est pas un raffinement : deux appels, c'est un intervalle pendant
    lequel le ticket est « À faire » et encore assigné (ou l'inverse), et un run qui passe là
    tombe sur un état que personne n'a voulu.
    """
    _orphelin(depot, "316")

    acheve = depot.lib("reprendre-en-cours", "316")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    mutations = _mutations(depot)
    assert len(mutations) == 1, f"une seule mutation attendue : {mutations}"
    mutation = mutations[0]
    assert f'id:"{WORKITEM_GID}"' in mutation
    assert "assigneeIds:[]" in mutation, "libérer, c'est VIDER la liste des assignés"
    a_faire = ["gid://gitlab/ProjectLabel/9007"]
    assert _gids(mutation, "addLabelIds") == a_faire, "le cycle de vie repasse à workflow::a-faire"
    assert len(_gids(mutation, "removeLabelIds")) == 5, "les cinq autres partent dans le même appel"


def test_la_reprise_ne_touche_ni_au_worktree_ni_aux_commits(depot: Depot) -> None:
    """#316, c'est 2047 lignes commitées et jamais poussées : elles doivent être là après.

    Le ticket est explicite — « sans rien perdre » — et c'est ce qui distingue cette reprise d'un
    `gc` : le verbe n'écrit QUE dans GitLab, il ne connaît même pas de chemin à supprimer.
    """
    depot.pose_etat(graphql=_regles_reprise({"316": "En cours"}))
    chemin = _worktree(depot, "316")
    (chemin / "travail-non-commite.txt").write_text("2047 lignes\n", encoding="utf-8", newline="\n")
    depot.git("-C", str(chemin), "add", "-A")
    depot.git("-C", str(chemin), "commit", "--quiet", "-m", "feat: travail jamais poussé")
    (chemin / "encore-en-chantier.txt").write_text("en cours\n", encoding="utf-8", newline="\n")
    sha = depot.git("-C", str(chemin), "rev-parse", "HEAD")
    # Le silence vient APRÈS le travail : un `git commit` touche l'index, donc rend le worktree
    # frais — le faire dans l'autre ordre ferait conclure « vivant » et le test mesurerait le
    # refus au lieu de la préservation.
    _silence(chemin, 48 * 3600)

    assert depot.lib("reprendre-en-cours", "316").returncode == 0

    assert chemin.exists(), "le worktree n'est jamais retiré"
    assert (chemin / "encore-en-chantier.txt").exists(), "le non-commité reste où il est"
    apres = depot.git("-C", str(chemin), "rev-parse", "HEAD")
    assert apres == sha, "le commit non poussé est intact"
    assert depot.git("-C", str(chemin), "branch", "--show-current") == "chore/316-essai"


def test_check_dit_ce_qu_il_ferait_sans_rien_ecrire(depot: Depot) -> None:
    """Cohérence de famille (`reconcile-workflow --check`, `setup --derive`) : voir avant d'agir."""
    _orphelin(depot, "316")

    acheve = depot.lib("reprendre-en-cours", "--check", "316")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "passerait à « À faire »" in acheve.stdout
    assert _mutations(depot) == []
    assert not ecritures(depot)
    assert not _registre(depot).exists(), "un --check ne consigne rien non plus"


# --- La trace : d'où sortait le ticket, et combien de fois il est déjà revenu -------------------


def test_la_reprise_consigne_le_run_et_le_verdict_d_origine(depot: Depot) -> None:
    """« D'où sort ce ticket revenu à “À faire” ? » ne se répond nulle part ailleurs.

    Le ticket, lui, ne porte aucune trace de la session morte dessus : c'est le journal du run qui
    la porte, et il sera ramassé au bout de dix runs (#198) — d'où une trace qui lui survit.
    """
    _orphelin(depot, "316")
    _run_juge(depot, "316", "ECHEC", "timeout — session terminée sans clôture, 1 commit(s)")

    acheve = depot.lib("reprendre-en-cours", "316")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "20260810-141208" in acheve.stdout and "ECHEC" in acheve.stdout

    ligne = _registre(depot).read_text(encoding="utf-8").splitlines()[-1].split("\t")
    assert ligne[1:5] == ["316", "20260810-141208", "ECHEC", "1"]

    lecture = depot.lib("reprises", "316")
    assert lecture.returncode == 0, lecture.stderr
    assert "#316" in lecture.stdout and "20260810-141208" in lecture.stdout

    # Le commentaire sur le ticket : l'autre moitié de la trace, celle qu'on lit dans GitLab des
    # semaines plus tard, sans la machine sous la main.
    assert [ligne for ligne in depot.appels() if ligne.startswith("issue\tnote")], (
        "la reprise laisse un commentaire sur le ticket"
    )


def test_une_reprise_sans_run_d_origine_le_dit_au_lieu_d_inventer(depot: Depot) -> None:
    """#325 : session interactive laissée en plan, aucun journal — ne rien trouver est une
    réponse."""
    _orphelin(depot, "325")

    acheve = depot.lib("reprendre-en-cours", "325")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "aucun run" in acheve.stdout
    assert _registre(depot).read_text(encoding="utf-8").splitlines()[-1].split("\t")[2] == "-"


# --- Le bornage : une reprise est un geste, pas une boucle --------------------------------------


def test_le_plafond_arrete_un_ticket_qui_retombe_a_chaque_run(depot: Depot) -> None:
    """Le garde-fou dont les tests ne sont PAS différés (le ticket le dit, et voici pourquoi).

    Un ticket que chaque session fait tomber au même endroit repartirait à chaque run, brûlerait
    une session entière à chaque fois et redeviendrait orphelin : la reprise serait une boucle, sur
    un quota partagé. Le plafond ne l'interdit pas — il exige qu'on le demande.
    """
    _orphelin(depot, "316")
    for essai in (1, 2):
        assert depot.lib("reprendre-en-cours", "316").returncode == 0, f"reprise {essai}"

    acheve = depot.lib("reprendre-en-cours", "316")
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "plafond" in acheve.stdout
    assert len(_mutations(depot)) == 2, "la troisième reprise n'a rien écrit"

    forcee = depot.lib("reprendre-en-cours", "--force", "316")
    assert forcee.returncode == 0, forcee.stdout + forcee.stderr
    assert len(_mutations(depot)) == 3, "--force reste la porte de sortie, jamais silencieuse"


def test_le_plafond_se_compte_par_ticket(depot: Depot) -> None:
    """Deux tickets, deux compteurs — sans quoi un ticket bloquerait la reprise de son voisin."""
    depot.pose_etat(graphql=_regles_reprise({"316": "En cours", "325": "En cours"}))
    for iid in ("316", "325"):
        _silence(_worktree(depot, iid), 48 * 3600)
    for _ in (1, 2):
        assert depot.lib("reprendre-en-cours", "316").returncode == 0

    assert depot.lib("reprendre-en-cours", "316").returncode == 3
    assert depot.lib("reprendre-en-cours", "325").returncode == 0


def test_le_plafond_est_reglable(depot: Depot) -> None:
    """Deux essais est un choix, pas une constante de la nature : il se déplace sans code."""
    _orphelin(depot, "316")
    acheve = depot.lib("reprendre-en-cours", "316")
    assert acheve.returncode == 0, acheve.stderr

    borne = depot.lib("reprendre-en-cours", "316", reglages={"MAESTRO_REPRISES_MAX": "1"})
    assert borne.returncode == 3
    assert "plafond 1" in borne.stdout


def test_plusieurs_tickets_en_un_appel_et_un_refus_n_arrete_pas_les_autres(depot: Depot) -> None:
    """/orchestrate reprend en UN appel ce que l'utilisateur a coché : un refus au milieu de la
    liste ne doit pas laisser la moitié du travail non fait.
    """
    depot.pose_etat(graphql=_regles_reprise({"316": "En cours", "325": "En cours"}))
    _silence(_worktree(depot, "316"), 48 * 3600)
    _worktree(depot, "325")   # celui-là est vivant : il sera refusé

    acheve = depot.lib("reprendre-en-cours", "316", "325")
    assert acheve.returncode == 3, "un refus se dit par le code de retour, même partiel"
    assert "#316 repris" in acheve.stdout
    assert "quelqu'un s'en occupe" in acheve.stdout
    assert len(_mutations(depot)) == 1


def test_le_verbe_de_reprise_est_annonce_par_l_usage(depot: Depot) -> None:
    usage = depot.lib().stderr
    assert "reprendre-en-cours" in usage
    assert "reprises" in usage
    assert "--force" in usage
