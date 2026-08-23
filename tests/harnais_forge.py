"""Le dépôt jetable et le `gh` factice, partagés par les suites d'outillage de forge (#366).

Ce module n'est pas une suite : il ne porte aucun test, seulement le HARNAIS que
[`test_collaboration.py`](test_collaboration.py) avait construit pour le chantier #155 et que
[`test_cycle_de_vie.py`](test_cycle_de_vie.py) reprend tel quel pour le chantier #358. Il a été
sorti du premier au moment d'écrire le second : le recopier aurait fait deux `gh` factices à tenir
d'accord, et le premier symptôme de deux doubles est une suite verte sur une forme de réponse que
l'autre a corrigée depuis.

**Ni réseau ni compte de forge.** Un `gh` factice est placé en tête du `PATH` : il répond depuis un
fichier JSON que chaque test compose, et **journalise** les commandes reçues (c'est ainsi qu'on
vérifie qu'aucune écriture n'a lieu). Il écrit ses réponses en **octets UTF-8** — le mojibake de
#141 est venu d'un décodage approximatif sous Windows, on ne le réintroduit pas dans le harnais. Ce
qui est testé à travers lui, c'est la **décision** des helpers, jamais l'API GitHub elle-même.

**Un dépôt jetable** est monté dans `tmp_path`, sur lequel les VRAIS scripts sont lancés — même
parti pris que [`test_setup.py`](test_setup.py) et [`test_worktree.py`](test_worktree.py). Rien
n'est jamais écrit dans le dépôt de travail (`HOME` est lui aussi redirigé).

Usage, dans une suite :

    from harnais_forge import BASH, GIT, Depot, monte_depot, regle_owner

    pytestmark = [
        pytest.mark.skipif(BASH is None, reason="bash introuvable"),
        pytest.mark.skipif(GIT is None, reason="git introuvable"),
    ]

    @pytest.fixture
    def depot(tmp_path: Path) -> Depot:
        return monte_depot(tmp_path)

Ce qui reste dans CHAQUE module, et pourquoi : le `pytestmark` (une marque posée ici ne
s'appliquerait qu'à ce fichier, qui ne porte aucun test) et la **fixture** (voir `monte_depot` —
l'importer la mettrait en collision avec le paramètre `depot` de chaque test aux yeux du linter).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
GIT = shutil.which("git")
DEPOT = "equipe-test/maestro"
MOI = "MaestroAgents"          # le compte d'automatisation partagé (cf. GL_BOT_USERS)

# Verbes d'ÉCRITURE de `gh` : leur absence du journal est ce qui atteste qu'un helper annoncé
# « consultatif » l'est resté. Les lectures en sont volontairement absentes — lire est le travail
# normal d'un bilan de santé ou d'un garde-fou.
#
# Le premier motif couvre TOUTES les écritures de l'API REST d'un coup : `gh api -X <MÉTHODE>` est
# la forme unique qu'elles prennent (PATCH d'un ticket, POST d'un commentaire…), et une liste de
# chemins se serait périmée au premier verbe ajouté.
ECRITURES = (
    "api\t-X", "pr\tcreate", "pr\tedit", "pr\tmerge", "pr\tclose", "pr\tready",
    "issue\tcreate", "issue\tedit", "issue\tclose", "issue\tcomment", "label\tcreate",
)

# --- Le gh factice --------------------------------------------------------------------------------
# Piloté par $MAESTRO_FAUX_GH (état JSON) et $MAESTRO_FAUX_GH_JOURNAL (trace des appels).
# Les réponses GraphQL/REST sont choisies par la PREMIÈRE règle dont tous les fragments `contient`
# apparaissent dans la requête — les règles les plus spécifiques se placent donc en tête.
FAUX_GH = r'''
import json
import os
import sys

with open(os.environ["MAESTRO_FAUX_GH"], encoding="utf-8") as f:
    etat = json.load(f)

args = sys.argv[1:]

journal = os.environ.get("MAESTRO_FAUX_GH_JOURNAL")
if journal:
    # Une ligne PAR APPEL, quoi qu'on reçoive : les sauts de ligne d'un corps sont échappés en
    # « \n » littéral (#233). Sans ça, un `--body` multi-ligne — la matière même de ce que les
    # helpers de création font voyager — casserait le découpage du journal et un test lirait un
    # demi-appel.
    def journalisable(a):
        # « champ=@chemin » est la forme par laquelle `gh` téléverse un FICHIER : c'est lui qui le
        # lit, donc le double doit le résoudre pour que le journal porte ce qui part réellement.
        # Sans ça, un test sur les octets transmis ne verrait qu'un chemin temporaire.
        cle, sep, valeur = a.partition("=@")
        if sep and os.path.isfile(valeur):
            with open(valeur, encoding="utf-8") as source:
                a = cle + "=" + source.read()
        return a.replace("\\", "\\\\").replace("\n", "\\n")

    with open(journal, "a", encoding="utf-8") as f:
        f.write("\t".join(journalisable(a) for a in args) + "\n")


def sortie(texte="", code=0):
    # Écriture en octets : sous Windows, sys.stdout encoderait en cp1252 et rendrait du mojibake
    # là où l'API renvoie de l'UTF-8 (statuts « À faire », « Terminé »…).
    sys.stdout.buffer.write(texte.encode("utf-8"))
    sys.stdout.buffer.flush()
    raise SystemExit(code)


def compact(obj):
    # L'outillage parse en grep/awk sur le JSON BRUT : pas d'espaces, pas d'échappement des
    # non-ASCII, sinon les motifs (« "issues":{"nodes":[]} ») ne matchent plus.
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n"


def repond(regles, sujet):
    for regle in regles:
        if all(fragment in sujet for fragment in regle.get("contient", [])):
            if "brut" in regle:
                return regle["brut"]
            return compact(regle["reponse"])
    return None


def vue_texte_en_json(texte):
    """La vue canonique d'un ticket, re-rendue sous la forme que lit `gh_issue_raw`.

    Les tests décrivent leurs tickets dans le FORMAT DE SORTIE (« title:<TAB>… », « -- », corps) :
    c'est le contrat que six verbes parsent, et il n'a pas bougé avec la migration. Le double se
    charge donc de la traduction, plutôt que d'imposer à chaque test d'écrire du JSON GraphQL.
    """
    entete, _, corps = texte.partition("\n--\n")
    champs = {}
    for ligne in entete.splitlines():
        cle, _, valeur = ligne.partition(":\t")
        champs[cle] = valeur
    def nodes(cle, brut):
        valeurs = [v.strip() for v in brut.split(",") if v.strip()]
        return {"nodes": [{cle: v} for v in valeurs]}
    return {"data": {"repository": {"issue": {
        "title": champs.get("title", ""),
        "state": "CLOSED" if champs.get("state") == "closed" else "OPEN",
        "author": {"login": champs.get("author", "")},
        "labels": nodes("name", champs.get("labels", "")),
        "assignees": nodes("login", champs.get("assignees", "")),
        "milestone": {"jalon": champs.get("milestone", "")},
        "body": corps.rstrip("\n"),
    }}}}


if args[:2] == ["auth", "status"]:
    sortie(code=0 if etat.get("authentifie", True) else 1)

if args[:2] == ["api", "user"]:
    sortie(compact({"login": etat.get("moi", "inconnu"), "id": 4242}))

def requete_graphql(args):
    """Le corps de la requête, quelle que soit la forme par laquelle `gh` la reçoit.

    Les trois formes sont dans le dépôt, et une seule ne suffit donc pas (#366) :

    * ``-f query=<texte>`` — la forme de `scripts/gitlab/lib.sh`, sur la ligne de commande ;
    * ``-F query=@-`` — celle de `scripts/github/bootstrap-project.sh`, par l'ENTRÉE STANDARD :
      la couche permissions découpe un appel sur ses sauts de ligne, et ses requêtes sont
      multi-lignes (docs/10 §11.7). ⚠ `-F` et non `-f` : seul le premier interprète le `@` ;
    * ``--input <fichier>`` — un objet ``{"query":…, "variables":…}`` complet, seule façon de
      passer une LISTE d'objets, que `-f` ne sait pas porter (les options du champ Status).

    Les variables (`-f nom=valeur`) ne sont pas substituées : les règles du double matchent sur
    le TEXTE de la requête, et c'est GitHub qui substituerait.
    """
    for i, a in enumerate(args):
        if a == "--input" and i + 1 < len(args):
            with open(args[i + 1], encoding="utf-8") as f:
                return json.load(f).get("query", "")
    if any(a == "query=@-" for a in args):
        return sys.stdin.read()
    return "".join(a[len("query="):] for a in args if a.startswith("query="))


if args[:2] == ["api", "graphql"]:
    requete = requete_graphql(args[2:])
    # La vue canonique d'un ticket est servie d'office quand le test l'a décrite : c'est la
    # requête la plus spécifique du lot, elle passe donc avant les règles.
    if "issue(number:" in requete and '"body"' not in requete and "body }" in requete:
        iid = requete.split("issue(number:", 1)[1].split(")", 1)[0]
        texte = etat.get("issues", {}).get(iid)
        if texte is not None:
            sortie(compact(vue_texte_en_json(texte)))
    reponse = repond(etat.get("graphql", []), requete)
    sortie(reponse) if reponse is not None else sortie(code=1)

def chemin_api(args):
    """Le chemin REST d'un `gh api`, isolé de ses drapeaux ET de leurs valeurs.

    `-f labels[]=x` ne commence pas par un tiret côté VALEUR : filtrer sur le tiret seul ferait
    prendre la première donnée pour un chemin.
    """
    porte_valeur = {"-X", "-f", "-F", "-H", "--jq", "--method", "--field", "--raw-field"}
    reste = list(args[1:])
    while reste:
        a = reste.pop(0)
        if a in porte_valeur:
            if reste:
                reste.pop(0)
            continue
        if a.startswith("-"):
            continue
        return a
    return ""


if args[:1] == ["api"]:
    chemin = chemin_api(args)
    # ÉCRITURE : `gh api -X <MÉTHODE>`. Le double rend le minimum que lib.sh lit — un « number » —,
    # sauf si une règle `ecritures` décrit autre chose (un refus, une URL précise).
    if "-X" in args and args[args.index("-X") + 1] in ("POST", "PATCH", "PUT", "DELETE"):
        reponse = repond(etat.get("ecritures", []), chemin)
        if reponse is not None:
            sortie(reponse, code=etat.get("ecriture_code", 0))
        if etat.get("ecriture_en_echec"):
            sortie(compact({"message": "refus simulé"}), code=1)
        numero = etat.get("pr_numero", 42) if chemin.endswith("/pulls") else 1
        sortie(compact({
            "number": numero,
            "html_url": "https://github.com/" + etat.get("depot", "equipe-test/maestro")
                        + "/pull/" + str(numero),
        }))
    reponse = repond(etat.get("rest", []), chemin)
    sortie(reponse) if reponse is not None else sortie(code=1)

if args[:2] == ["pr", "create"]:
    sortie(etat.get("pr_create_sortie", "PR ouverte : " + etat.get("mr_url", "") + "\n"),
           code=etat.get("pr_create_code", 0))

if args[:2] == ["issue", "comment"]:
    sortie("Commentaire ajouté.\n", code=etat.get("issue_note_code", 0))

sortie(code=1)
'''


# --- Fabrication des réponses --------------------------------------------------------------------
# Des fabriques plutôt que du JSON en dur : la FORME des réponses (ordre des clés compris) fait
# partie de ce que les parsers awk/grep de lib.sh attendent — la centraliser évite qu'un test
# passe pour une raison qui n'a rien à voir avec ce qu'il prétend vérifier.


def noeud_ticket(iid: str, titre: str, statut: str, labels: list[str], assignes: list[str]) -> dict:
    """Un ticket tel que le rend la requête backlog (ordre des clés : number, title, labels…).

    ⚠ `statut` n'est PAS porté ici, et c'est le sujet du chantier #358 : depuis #365 l'état vit
    dans le champ Status d'un ITEM DE PROJET, pas sur l'issue. Le paramètre est conservé — les
    tests décrivent leurs tickets avec leur état, c'est lisible — mais il part alimenter la CARTE
    (`regles_carte`), que `st_backlog_table` recouvre sur cette réponse-ci. Le laisser fabriquer un
    label rendrait des tests verts sur une donnée que plus personne ne lit.
    """
    del statut  # → regles_carte : l'état ne vit pas sur l'issue
    return {
        "number": int(iid),
        "title": titre,
        "labels": {"nodes": [{"name": label} for label in labels]},
        "assignees": {"nodes": [{"login": u} for u in assignes]},
    }


def reponse_backlog(tickets: list[dict]) -> dict:
    return {"data": {"repository": {"issues": {"nodes": tickets}}}}


def regles_backlog(statuts: dict[str, str], labels: list[str] | None = None) -> list[dict]:
    """Règles de réponse au backlog OUVERT — « iid : cycle de vie » pour chaque ticket.

    DEUX SOURCES depuis #365, et c'est le recouvrement de `st_backlog_table` : les issues disent
    QUI EXISTE, la carte du projet dit QUEL ÉTAT. Un iid dont le libellé est vide est un item au
    Status non posé ; un iid absent du dictionnaire est un ticket hors projet — les deux sortent
    « - » de la table, et les distinguer est le travail de doctor.sh (#363).
    """
    return regles_carte(statuts) + [
        {
            "contient": ["states: [OPEN]"],
            "reponse": reponse_backlog(
                [
                    noeud_ticket(iid, f"Ticket {iid}", statut, labels or ["type::infra"], [MOI])
                    for iid, statut in statuts.items()
                ]
            ),
        }
    ]


def colonnes(sortie: str) -> list[list[str]]:
    """Découpe une sortie TSV de `lib.sh` en lignes de colonnes, en-tête « # … » écartée."""
    return [
        ligne.split("\t")
        for ligne in sortie.splitlines()
        if ligne and not ligne.startswith("#")
    ]


# ================================================================================================
# LE CYCLE DE VIE VIT DANS LE CHAMP STATUS D'UN PROJET (#365, chantier #358)
# ================================================================================================
# Les doubles ci-dessous ont porté trois supports successifs : le champ Status natif de GitLab, les
# six labels `workflow::*` (#209), puis, depuis #365, le champ **Status** d'un projet Projects v2.
# Les tests, eux, n'ont jamais bougé : ils écrivent et attendent le LIBELLÉ (« En revue »), qui est
# le contrat de surface documenté en tête de scripts/gitlab/lib.sh — c'est précisément ce que ce
# contrat sert à démontrer.
#
# ⚠ CE QUI CHANGE POUR UN DOUBLE, ET QU'IL FAUT SAVOIR AVANT D'EN AJOUTER UN. Le backend Status
# passe ses lectures par `gh api graphql --jq`, où le PROGRAMME JQ fait tout l'aplatissement — et le
# `gh` factice ne l'exécute pas. Les règles rendent donc le résultat DÉJÀ APLATI, par leur clé
# `brut` : des lignes `clé<TAB>…` copiées des en-têtes de `st_jq_contexte` et `st_jq_items`. Un
# double qui rendrait du JSON ici passerait le filtre en silence et le verbe lirait zéro ligne,
# c'est-à-dire « ticket sans état » — un feu vert sur une question jamais posée.
#
# Deux formes de lecture, deux jeux de règles :
#   • L'UNITÉ (`issue-owner`, `set-workflow`, `begin`, `liberer-ticket`) passe par `st_contexte` :
#     le ticket, ses assignés, ses items de projet, et les projets du compte avec leurs options.
#   • L'ENSEMBLE (`backlog-table`, `milestone-issues`, `workflow-derives`) passe par la CARTE :
#     un appel pour résoudre le projet par son titre, puis une page d'items.
#
# Les identifiants sont inventés ici et n'ont aucune importance : lib.sh les résout PAR NOM à chaque
# appel et n'en code aucun en dur (contrat en tête du fichier). C'est même ce qu'ils vérifient.

#: Titre du projet que lib.sh cherche (défaut de `GL_PROJET_TITRE`, cf. `MAESTRO_PROJECT_TITRE`).
PROJET = "Maestro"
ID_PROJET = "PVT_projet"
ID_CHAMP = "PVTSSF_status"

#: Les six états, dans l'ordre du flux. `st_cible` cherche l'option par son LIBELLÉ.
LIBELLES_WORKFLOW = ("À faire", "En cours", "En revue", "Terminé", "Abandonné", "Doublon")


def lignes_projet() -> list[str]:
    """Le projet et ses six options, tels que `st_jq_contexte` les aplatit."""
    return [f"projet\t{PROJET}\t{ID_PROJET}\t{ID_CHAMP}"] + [
        f"option\t{PROJET}\t{ID_PROJET}_opt{i}\t{libelle}"
        for i, libelle in enumerate(LIBELLES_WORKFLOW)
    ]


def reponse_owner(
    statut: str, assignes: list[str], iid: str = "1", dans_projet: bool = True
) -> str:
    """Le contexte d'UN ticket, aplati (`st_contexte`) : état, assignés, projet et options.

    `dans_projet=False` rend un ticket ABSENT du projet — donc sans ligne `item`. C'est l'état d'un
    ticket créé depuis l'interface web : une lecture le rend « sans état », une écriture le refuse.
    """
    lignes = [f"ticket\t{iid}"]
    lignes += [f"assigne\t{u}" for u in assignes]
    if dans_projet:
        lignes.append(f"item\t{PROJET}\t{ID_PROJET}\tPVTI_{iid}\t{statut}")
    lignes += lignes_projet()
    return "\n".join(lignes) + "\n"


def regle_owner(
    statut: str, assignes: list[str], iid: str = "1", dans_projet: bool = True
) -> dict:
    """Règle de réponse au contexte d'UN ticket (st_contexte).

    Le fragment `projectItems(first:` est ce qui distingue cette requête de toutes les autres : la
    requête backlog porte elle aussi `issue(number:` et `assignees(first:`, et capterait la règle
    si elle était moins spécifique.
    """
    return {
        "contient": ["issue(number:", "projectItems(first:"],
        "brut": reponse_owner(statut, assignes, iid, dans_projet),
    }


def regle_pose_status() -> dict:
    """Règle de réponse à la mutation qui pose le champ Status (`st_set_workflow`).

    Sans elle, toute écriture d'état échoue dans le double : le `gh` factice ne répond qu'aux règles
    qu'on lui donne, et une mutation sans règle sort en code 1. `st_set_workflow` ne lit qu'une
    chose dans la réponse — la présence de `projectV2Item` — et c'est tout ce qu'on rend.
    """
    return {
        "contient": ["updateProjectV2ItemFieldValue"],
        "reponse": {
            "data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "PVTI_pose"}}}
        },
    }


def regles_carte(statuts: dict[str, str]) -> list[dict]:
    """Les DEUX règles de la carte d'ensemble : résolution du projet, puis sa page d'items.

    `statuts` est « iid -> libellé ». Un ticket ABSENT de ce dictionnaire est hors du projet : les
    tables le rendent alors avec un statut « - », ce qui est exactement le contrat (cf. l'en-tête de
    `st_backlog_table`). Un libellé VIDE est un item présent au Status non posé — même « - » en
    sortie, autre cause, et c'est #363 qui les distingue.

    La ligne `page` est toujours émise, y compris sur un projet vide : sans elle, la garde « réponse
    vide » de `gh_graphql_read` déclencherait trois tentatives puis une erreur.
    """
    items = "".join(f"item\t{iid}\t{statut}\n" for iid, statut in statuts.items())
    return [
        {
            "contient": ["projectsV2(first:100){nodes{ id title }}"],
            "brut": f"projets\nprojet\t{PROJET}\t{ID_PROJET}\n",
        },
        {
            "contient": ["items(first:100"],
            "brut": f"page\tfalse\t\n{items}",
        },
    ]


# --- Le merge et ses prérequis (#414, chantier #413) ----------------------------------------------
# `merge-mr` (#415) tranche sur deux lectures et une écriture, toutes trois parsées en grep/awk : la
# PLACE des clés y compte autant que leur valeur. Ces fabriques les centralisent pour la raison
# donnée en tête de section — un test qui décrirait sa PR à la main passerait, ou échouerait, pour
# une raison qui n'est pas celle qu'il annonce.


def regle_pr(
    branche: str,
    pr: int = 42,
    sha: str = "",
    etat: str = "OPEN",
    brouillon: bool = False,
    ferme: tuple[int, ...] = (),
) -> dict:
    """La PR d'UNE branche, telle que `gh_merge_facts` la lit — en une seule requête.

    ⚠ `number` vient EN PREMIER, et ce n'est pas cosmétique : le verbe retient le premier
    `"number":<n>` de la réponse pour le numéro de la PR. Placé après `closingIssuesReferences`, un
    numéro de TICKET prendrait sa place — et le test serait vert sur la mauvaise donnée.

    `etat=""` décrit l'absence de PR (aucun nœud), qui n'est pas la même chose qu'une PR fermée :
    l'une dit « rien à merger », l'autre « plus rien à merger », et le verbe les rend toutes deux
    en `6` par des chemins différents.
    """
    noeuds = [] if not etat else [{
        "number": pr,
        "state": etat,
        "isDraft": brouillon,
        "headRefOid": sha,
        "closingIssuesReferences": {"nodes": [{"number": n} for n in ferme]},
    }]
    return {
        "contient": [f'pullRequests(headRefName: "{branche}"'],
        "reponse": {"data": {"repository": {"pullRequests": {"nodes": noeuds}}}},
    }


def regle_prs_ouvertes(branches: tuple[str, ...]) -> dict:
    """Les branches des PR ouvertes — ce qui résout un iid en branche (`gl_branche_du_ticket`)."""
    return {
        "contient": ["pullRequests(states: OPEN"],
        "reponse": {"data": {"repository": {"pullRequests": {
            "nodes": [{"headRefName": b} for b in branches]}}}},
    }


def regle_run(
    branche: str,
    sha: str = "",
    statut: str = "completed",
    conclusion: str = "success",
    run: int = 900,
) -> dict:
    """Le dernier run Actions d'une branche (`gh_pipeline_latest`), côté REST.

    `statut`/`conclusion` sont ceux de GitHub, pas le vocabulaire normalisé de `lib.sh` : la
    traduction est faite par `gh_etat_run`, et la traverser est tout l'intérêt — un test qui
    poserait directement « success » sauterait la moitié qui peut se tromper.
    """
    return {
        "contient": [f"actions/runs?branch={branche}"],
        "reponse": {"workflow_runs": [{
            "id": run,
            "status": statut,
            "conclusion": conclusion,
            "head_sha": sha,
            "html_url": f"https://github.com/{DEPOT}/actions/runs/{run}",
        }]},
    }


def regle_run_absent(branche: str) -> dict:
    """Aucun run pour cette branche — la CI ne se déclenche que sur les PR (docs/10 §8)."""
    return {"contient": [f"actions/runs?branch={branche}"], "reponse": {"workflow_runs": []}}


def regle_merge(pr: int = 42, merge: bool = True) -> dict:
    """Le PUT qui merge.

    `merge=False` rend le refus de GitHub, c'est-à-dire une réponse SANS `"merged":true` — la forme
    exacte que le verbe lit pour conclure à l'échec, et non un code d'erreur qu'il ne regarde pas.
    """
    corps = (
        {"merged": True, "message": "Pull Request successfully merged"}
        if merge
        else {"message": "Head branch was modified. Review and try the merge again."}
    )
    return {"contient": [f"pulls/{pr}/merge"], "reponse": corps}


def corps_ticket(titre: str, labels: str, description: str) -> str:
    """La VUE CANONIQUE d'un ticket : en-têtes `clé:<TAB>valeur`, séparateur `--`, puis le corps.

    C'est le format de sortie de `lib.sh issue-raw`, dont six verbes descendent — et c'est lui que
    les tests décrivent, le double se chargeant de le re-rendre en JSON (cf. `vue_texte_en_json`).
    """
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
    """Dépôt jetable équipé du vrai `lib.sh`, d'un `origin` local et d'un `gh` factice."""

    racine: Path
    origin: Path
    home: Path
    fauxbin: Path
    etat_json: Path
    journal: Path
    etat: dict = field(default_factory=dict)

    # --- pilotage du gh factice ---
    def pose_etat(self, **entrees: object) -> None:
        """Remplace tout ou partie de l'état du gh factice (`graphql`, `rest`, `issues`…)."""
        self.etat.update(entrees)
        self.etat_json.write_text(
            json.dumps(self.etat, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
        )

    def appels(self) -> list[str]:
        """Commandes `gh` reçues depuis le début du test (une par ligne, arguments en TAB)."""
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

    def bootstrap_project(
        self, *args: str, reglages: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """`scripts/github/bootstrap-project.sh` — le monteur du projet Projects v2 (#359)."""
        return self._bash(
            "scripts/github/bootstrap-project.sh", *args, cwd=None, reglages=reglages
        )

    def ticket_ferme(
        self, *args: str, reglages: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """`scripts/github/ticket-ferme.sh` — la décision du workflow `issues: closed` (#377).

        ⚠ `GH_TOKEN` est posé D'OFFICE, et c'est un garde-fou de poste et non un confort : le
        script s'abstient quand il est vide, si bien qu'une machine qui n'en porte pas rendrait
        « abstention » là où le test attend une pose (et une machine qui en porte un ferait passer
        le test qui vérifie l'abstention). Le vider explicitement reste possible — c'est ce que
        fait le test de l'abstention, en le passant à "" dans `reglages`.
        """
        return self._bash(
            "scripts/github/ticket-ferme.sh",
            *args,
            cwd=None,
            reglages={"GH_TOKEN": "jeton-de-test", **(reglages or {})},
        )

    def bash_inline(
        self, script: str, reglages: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Joue un fragment de shell DANS le dépôt jetable, `lib.sh` sourçable en chemin relatif.

        Nécessaire dès qu'on veut plusieurs verbes DANS UN MÊME PROCESSUS : c'est le seul régime
        où la mémoire de la carte (#362) existe — une substitution de commande la mettrait dans un
        sous-shell, d'où aucune affectation ne remonte. C'est ainsi que `queue.sh` demande ses deux
        tables, donc ainsi qu'il faut le tester.
        """
        chemin = self.racine / ".maestro" / "session" / "fragment.sh"
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(script, encoding="utf-8", newline="\n")
        return self._bash(str(chemin.relative_to(self.racine)), cwd=None, reglages=reglages)

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
                "MAESTRO_GITHUB_REPO": DEPOT,
                # Aucune attente : le retry de gl_graphql_read ne sert qu'aux hoquets réseau,
                # et une réponse volontairement muette ne doit pas coûter trois secondes au test.
                "GL_GQL_RETRIES": "1",
                "GL_GQL_RETRY_DELAY": "0",
                "MAESTRO_FAUX_GH": str(self.etat_json),
                "MAESTRO_FAUX_GH_JOURNAL": str(self.journal),
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


def ecritures(depot: Depot) -> list[str]:
    """Les appels `gh` qui ÉCRIVENT côté forge — vides tant qu'un helper s'abstient.

    C'est par cette liste qu'un helper annoncé « lecture seule » le prouve : les lectures sont
    volontairement absentes d'`ECRITURES`, lire étant le travail normal d'un bilan de santé.
    """
    return [ligne for ligne in depot.appels() if any(verbe in ligne for verbe in ECRITURES)]


def monte_depot(tmp_path: Path) -> Depot:
    """Monte le dépôt jetable et son `gh` factice — le corps de la fixture, sans la marque.

    ⚠ C'EST UNE FABRIQUE ET NON UNE FIXTURE, et la raison est le linter plutôt que le goût :
    importer une fixture (`from harnais_forge import depot`) la met en collision avec le paramètre
    `depot` de chaque test, que ruff compte alors comme une redéfinition — 112 F811 sur la seule
    `test_collaboration.py`, tous à faire taire un par un. Chaque suite déclare donc sa fixture en
    deux lignes autour de cette fabrique, ce qui est aussi ce qui lui permet d'en poser une variante
    (un `gh` déjà instruit, un dépôt pré-peuplé) sans toucher au harnais.
    """
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
        # `reconcile-en-cours` (#328) délègue la relecture des cartes de pilote à ce fichier — il
        # ne la refait pas, deux formules qui divergeraient se remarqueraient trop tard.
        "scripts/orchestrate/pilote.sh",
        # `reprendre-en-cours` (#329) lui demande d'où sort le ticket qu'on reprend : le journal
        # d'un run est le seul endroit qui sache dire quel run l'a laissé là, et avec quel verdict.
        "scripts/orchestrate/journal.sh",
        # Le monteur du projet Projects v2 (#359) : c'est lui qui pose les six options du champ
        # Status, donc le seul endroit du dépôt où le VOCABULAIRE du cycle de vie est écrit deux
        # fois (ici et dans `gl_workflow_label`). Voir tests/test_cycle_de_vie.py.
        "scripts/github/bootstrap-project.sh",
        # La décision du workflow `issues: closed` (#377) : elle délègue la pose à
        # `lib.sh reconcile-workflow`, donc les deux fichiers doivent être là ENSEMBLE — c'est le
        # chaînage des deux filtres (raison de fermeture, puis état courant) qui se teste.
        "scripts/github/ticket-ferme.sh",
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

    # Le gh factice : un script Python, appelé par un lanceur nommé `gh` (sans extension) pour
    # que `command -v gh` de lib.sh le trouve comme le vrai.
    (fauxbin / "faux_gh.py").write_text(FAUX_GH, encoding="utf-8", newline="\n")
    lanceur = fauxbin / "gh"
    interpreteur = sys.executable.replace(chr(92), "/")
    lanceur.write_text(
        "#!/usr/bin/env bash\n"
        f'exec "{interpreteur}" "{(fauxbin / "faux_gh.py").as_posix()}" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    lanceur.chmod(0o755)

    # Neutralisation du poste : `docker` répond toujours en échec. Plus aucun helper testé ici ne
    # l'appelle depuis le retrait de l'outillage runner (#344) — le shim reste parce qu'un `docker`
    # atteignable depuis un test est une porte qu'on n'a aucune raison de rouvrir.
    shim = fauxbin / "docker"
    shim.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8", newline="\n")
    shim.chmod(0o755)

    depot = Depot(
        racine=racine,
        origin=origin,
        home=home,
        fauxbin=fauxbin,
        etat_json=tmp_path / "faux-gh.json",
        journal=tmp_path / "faux-gh.log",
    )
    depot.pose_etat(moi=MOI, authentifie=True, graphql=[], rest=[], issues={})
    return depot
