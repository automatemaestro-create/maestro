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
import shutil
import subprocess
import sys
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
    def lib(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return self._bash("scripts/gitlab/lib.sh", *args, cwd=cwd)

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
