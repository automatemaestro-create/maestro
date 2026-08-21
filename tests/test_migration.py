"""Tests de la migration GitLab → GitHub — export (#337) et import (#340), lot final #345.

Les deux scripts de `scripts/migration/` sont les seuls du dépôt à n'avoir **aucune couverture** :
ils sont nés avec le chantier #335 et les lots précédents n'ont adapté que les suites qui nommaient
déjà les fichiers qu'ils touchaient. Or ce sont aussi les deux seuls dont le travail **ne se rejoue
pas** — l'import a créé 345 objets numérotés, GitHub ne laissant pas choisir un numéro d'issue.

**Ce qui est couvert, et pourquoi ces invariants-là.** Un test ne sert ici qu'à une chose : garder
vraies les propriétés qu'un second passage ne pourrait plus corriger.

* **l'ordre** — l'objet créé pour l'iid N porte le numéro N. 270 commits de l'historique portent un
  `Refs #<n>` : un décalage d'un rang ne rend pas ces liens morts mais **faux**, donc plausibles et
  jamais signalés (docs/27 §6) ;
* **les trous** — les iid supprimés côté GitLab consomment un objet bouche-trou, sans quoi tout ce
  qui suit décale ;
* **les octets** — aucune étape ne décode puis ré-encode du texte, à l'export comme à l'import.
  C'est l'aller-retour où le mojibake de #141 s'était introduit, et un terminal cp1252 le réaffiche
  de façon plausible : la vérification est donc faite **sur les octets**, jamais à l'affichage ;
* **l'idempotence** — le ré-assemblage d'un export rend le même fichier, au bit près ;
* **la reprise** — un import coupé reprend sur son journal, sans doublon et sans re-poster les
  commentaires déjà écrits.

**Ni réseau ni écriture réelle.** Un répertoire jetable est monté dans `tmp_path`, sur lequel les
VRAIS scripts sont lancés — même parti pris que `test_setup.py` et `test_worktree.py`.

⚠ **Le `gh` factice de ce module n'est pas celui de [`harnais_forge.py`](harnais_forge.py), et ce
n'est pas un oubli de mutualisation.** Celui du harnais partagé est **sans mémoire** : il répond à
une requête par une règle, et rend `"number": 1` à toute écriture. C'est exactement ce qu'il faut
pour juger la *décision* d'un helper, et exactement ce qui rend l'invariant d'ordre **intestable** —
une séquence ne s'observe que sur un dépôt qui en tient une. Le double ci-dessous est donc un
**dépôt GitHub simulé** : il attribue les numéros, garde les corps reçus, et le test relit son état
final comme on relirait le dépôt. Deux doubles, deux questions ; les fusionner obligerait le
harnais partagé à porter un état dont ses deux suites n'ont aucun usage.

Le `glab` factice, lui, ne sert qu'à **une** chose : la preuve par seconde voie de l'export, qui
compare la description rendue par GraphQL à celle rendue par REST. C'est le seul contrôle de
l'export qui demande le réseau — les autres tournent en `--hors-ligne`.
"""

from __future__ import annotations

import json
import os
import re
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

DEPOT = "equipe-test/maestro"
PROJET_GL = "groupe-test/maestro"

# ==================================================================================================
# Le dépôt GitHub simulé — un `gh` qui TIENT UNE SÉQUENCE
# ==================================================================================================
# Le double vit dans [`faux_gh_migration.sh`](faux_gh_migration.sh) — un vrai fichier, et en BASH.
# Son en-tête porte le pourquoi ; le résumé tient en deux points.
#
# **Il tient une séquence**, et c'est ce qui le distingue du `gh` de
# [`harnais_forge.py`](harnais_forge.py) : celui-ci est sans mémoire, il répond à une requête par
# une règle et rend `"number": 1` à toute écriture. Parfait pour juger la *décision* d'un helper,
# et exactement ce qui rend l'invariant d'ordre **intestable** — une séquence ne s'observe que sur
# un dépôt qui en tient une. Les fusionner obligerait le harnais partagé à porter un état dont ses
# deux suites n'ont aucun usage.
#
# **Il est en bash pour une raison de coût, mesurée** : sous MSYS, un shim « bash → python » coûte
# ~0,38 s par appel contre ~0,12 s pour un shim bash seul, et l'invariant d'ordre impose de relire
# le dernier numéro AVANT chaque création. La version Python a fait dépasser son délai à l'import
# et poussé quatre tests sensibles à la charge dans le rouge — 1 h 17 de suite complète au lieu de
# ~15 min.
#
# Le double **ne comprend pas** ce qu'on lui envoie : il range les corps reçus tels quels, et c'est
# le test qui les relit avec un vrai parseur JSON (`Migration.etat_depot`). Les pannes sont décrites
# côté DONNÉES (fichier `scenario`) et non par un shim jetable, pour que le test dise ce qu'il
# simule au lieu de le cacher dans un exécutable.
FAUX_GH = (RACINE / "tests" / "faux_gh_migration.sh").read_text(encoding="utf-8")


# ==================================================================================================
# L'archive GitLab simulée — un `glab` en LECTURE SEULE
# ==================================================================================================
# Il ne sert qu'à la preuve par seconde voie de l'export : la description d'un ticket par le chemin
# REST, à comparer octet pour octet à ce que le découpage GraphQL a rendu. Le test pose le contenu
# de chaque ticket dans $MAESTRO_FAUX_GITLAB — poser une valeur DIFFÉRENTE est ce qui simule un
# export infidèle, et c'est le seul moyen de vérifier que ce contrôle peut échouer.
FAUX_GLAB = r'''
import json
import os
import sys

args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    raise SystemExit(0)
with open(os.environ["MAESTRO_FAUX_GITLAB"], encoding="utf-8") as f:
    etat = json.load(f)
chemin = args[-1] if args else ""
iid = chemin.rsplit("/", 1)[1] if "/issues/" in chemin else ""
corps = etat.get("descriptions", {}).get(iid)
if corps is None:
    raise SystemExit(1)
# COMPACT et avec les échappements de GitLab, comme l'API : `gl_json_string_field` cherche la
# suite d'octets `"description":"`, qu'un espace après le deux-points suffit à faire manquer —
# la preuve par seconde voie comparerait alors une chaîne vide et crierait à la divergence.
texte = (
    json.dumps({"iid": int(iid), "description": corps}, ensure_ascii=False,
               separators=(",", ":"))
    .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
)
sys.stdout.buffer.write(texte.encode("utf-8"))
'''


# ==================================================================================================
# Fabrication d'un export — les octets qu'un test veut voir traverser
# ==================================================================================================
def _chaine(valeur: str) -> str:
    """Un littéral JSON de chaîne tel que l'API GitLab le rend.

    `json.dumps` produit les échappements du format ; GitLab y ajoute les siens — `&`, `<` et `>`
    partent en `\\u0026`, `\\u003c`, `\\u003e`. Les reproduire n'est pas de la coquetterie : c'est
    la forme sur laquelle butent `lisible()` à l'export et `denorm()` à l'import, et les trois
    milestones du backlog qui contiennent un « & » sont exactement les seuls tickets sur lesquels
    une comparaison de titres bruts échouait (#340).
    """
    return _echappe_gitlab(json.dumps(valeur, ensure_ascii=False))


def _echappe_gitlab(json_brut: str) -> str:
    """Les trois échappements que l'API GitLab ajoute à ceux du format JSON."""
    return (
        json_brut.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    )


def _rest(objets: list[dict]) -> str:
    """Un référentiel REST tel que GitLab le rend : COMPACT, et avec ses échappements.

    Le compactage n'est pas une commodité. Les extracteurs awk de l'import cherchent la suite
    d'octets `"title":"` — un espace après le deux-points, que `json.dumps` met par défaut, suffit
    à ne rien trouver et à importer tous les tickets SANS milestone, sans que rien n'échoue. Un
    double qui écrirait du JSON « lisible » testerait donc un format que l'API ne produit pas.
    """
    return _echappe_gitlab(json.dumps(objets, ensure_ascii=False, separators=(",", ":")))


@dataclass
class Ticket:
    iid: int
    titre: str
    description: str
    etat: str = "OPEN"
    labels: tuple[str, ...] = ("type::infra",)
    milestone: str = ""
    notes: tuple[tuple[str, str], ...] = ()      # (auteur, corps) — commentaires HUMAINS
    notes_systeme: tuple[str, ...] = ()          # journal d'activité GitLab, jamais repris
    temps: int = 0
    assignes: tuple[str, ...] = ()
    lies: tuple[int, ...] = ()

    def noeud(self) -> str:
        """Le ticket dans la forme EXACTE que rend la requête GraphQL de l'export.

        L'ordre des clés n'est pas décoratif : la ligne doit commencer par `{"iid":"N",` (l'import
        y ancre sa recherche), et `title`/`description` doivent précéder les widgets, où « title »
        désigne aussi chaque label et chaque milestone.

        Un auteur vide rend `"author":null`, comme GitLab le fait pour un compte supprimé — et non
        un `username` vide, qui n'existe pas côté API.
        """
        notes = [
            "{\"author\":"
            + ("null" if not auteur else '{"username":' + _chaine(auteur) + "}")
            + ',"createdAt":"2026-02-01T09:00:00Z",'
            '"body":' + _chaine(corps) + ',"system":false}'
            for auteur, corps in self.notes
        ] + [
            '{"author":{"username":"MaestroAgents"},"createdAt":"2026-02-01T09:30:00Z",'
            '"body":' + _chaine(corps) + ',"system":true}'
            for corps in self.notes_systeme
        ]
        timelogs = ""
        if self.temps:
            timelogs = (
                '{"timeSpent":' + str(self.temps) + ',"spentAt":"2026-02-02",'
                '"summary":' + _chaine("relevé — é") + ',"user":{"username":"alice"}}'
            )
        return (
            '{"iid":"' + str(self.iid) + '","title":' + _chaine(self.titre) + ','
            '"state":"' + self.etat + '","createdAt":"2026-01-15T08:00:00Z",'
            '"updatedAt":"2026-02-03T08:00:00Z","closedAt":'
            + ('"2026-02-03T08:00:00Z"' if self.etat == "CLOSED" else "null")
            + ',"webUrl":"https://gitlab.example/' + PROJET_GL + '/-/issues/' + str(self.iid) + '",'
            '"workItemType":{"name":"Issue"},"author":{"username":"alice"},'
            '"description":' + _chaine(self.description) + ','
            '"widgets":['
            '{"labels":{"nodes":['
            + ",".join('{"title":' + _chaine(le) + "}" for le in self.labels)
            + "]}},"
            + (
                '{"milestone":{"title":' + _chaine(self.milestone) + "}},"
                if self.milestone
                else '{"milestone":null},'
            )
            + '{"startDate":"2026-01-15","dueDate":"2026-01-20"},'
            '{"timeEstimate":0,"totalTimeSpent":' + str(self.temps) + ','
            '"timelogs":{"nodes":[' + timelogs + "]}},"
            '{"assignees":{"nodes":['
            + ",".join('{"username":' + _chaine(u) + "}" for u in self.assignes)
            + "]}},"
            '{"linkedItems":{"nodes":['
            + ",".join(
                '{"linkType":"relates_to","workItem":{"iid":"' + str(le) + '"}}' for le in self.lies
            )
            + "]}},"
            '{"discussions":{"nodes":['
            + ",".join('{"notes":{"nodes":[' + n + "]}}" for n in notes)
            + "]}}]}"
        )


def page(tickets: list[Ticket], curseur: str = "FIN", suite: bool = False) -> bytes:
    """Une réponse GraphQL brute, telle qu'elle est écrite sous `pages/`.

    `pageInfo` est placé AVANT `nodes`, comme dans la requête : le découpage repère le tableau des
    tickets par la PREMIÈRE occurrence de `"nodes":[`, et inverser l'ordre le casserait en silence.
    """
    return (
        '{"data":{"project":{"workItems":{"pageInfo":{"endCursor":"' + curseur + '",'
        '"hasNextPage":' + ("true" if suite else "false") + '},"nodes":['
        + ",".join(t.noeud() for t in tickets)
        + "]}}}}"
    ).encode("utf-8")


# Le backlog d'essai. Petit, mais il porte une occurrence de chaque chose qui a déjà cassé :
# des accents et un em-dash (la contre-preuve UTF-8 de l'export en dépend), un bloc de code avec
# des accolades et des guillemets échappés (le découpage compte des accolades), un trou (#2), un
# ticket abandonné (`state_reason` = not_planned), un milestone contenant une esperluette (le piège
# du `awk -v` de #340) et une note SYSTÈME (qui ne doit jamais être reprise).
DESCRIPTION_1 = (
    "Première ligne — accentuée é è à ç ù.\n\n"
    "Un bloc de code, avec des accolades que le découpage doit traverser :\n\n"
    "```json\n"
    '{"cle": ["valeur", {"imbrique": true}]}\n'
    "```\n\n"
    'Et une citation « avec des "guillemets" » plus un \\ antislash isolé.\n'
    "Enfin une référence croisée : voir #3 et <https://exemple.test/a&b>.\n"
)
MILESTONE_ESPERLUETTE = "Projets & espace de travail"

BACKLOG = [
    Ticket(
        iid=1,
        titre="Premier ticket — avec un tiret cadratin",
        description=DESCRIPTION_1,
        labels=("type::infra", "agent::devops", "workflow::en-cours"),
        milestone="Phase 1 — POC",
        # Le troisième commentaire n'a pas d'auteur : sur GitLab, un compte supprimé rend
        # `"author":null`. C'est le champ vide au MILIEU d'une ligne du flux TSV interne, celui qui
        # décale tout ce qui suit à la lecture.
        notes=(
            ("bob", "Un commentaire — é à ç.\n\nAvec un paragraphe."),
            ("alice", "Réponse."),
            ("", "Écrit par un compte depuis supprimé — é."),
        ),
        notes_systeme=("added ~52011709 labels", "mentioned in issue #3"),
        temps=7200,
        assignes=("alice",),
        lies=(3,),
    ),
    Ticket(
        iid=3,
        titre="Troisième ticket, terminé",
        description="Court, mais accentué : é.\n",
        etat="CLOSED",
        labels=("type::bug", "workflow::termine"),
        milestone="Phase 1 — POC",
    ),
    # Le dernier porte DEUX choses à la fois — l'abandon et le milestone à esperluette — parce que
    # chaque objet du jeu d'essai coûte ~5 s d'import (une quarantaine de forks côté script), et
    # qu'un jeu qui grossit finit par déstabiliser les suites voisines plutôt que de mieux prouver.
    Ticket(
        iid=4,
        titre="Quatrième — abandonné, sur un milestone à esperluette",
        description="Rien à faire — abandonné. Sur « Projets & espace de travail » — é.\n",
        etat="CLOSED",
        labels=("type::feature", "workflow::abandonne"),
        milestone=MILESTONE_ESPERLUETTE,
    ),
]
TROU = 2
PLAGE_MAX = 4

LABELS_GITLAB = [
    {"name": "type::infra", "color": "#428BCA", "description": "Outillage"},
    {"name": "type::bug", "color": "#D9534F", "description": "Correction"},
    {"name": "type::feature", "color": "#5CB85C", "description": "Fonctionnalité"},
    {"name": "type::doc", "color": "#F0AD4E", "description": "Documentation"},
    {"name": "agent::devops", "color": "#8E44AD", "description": "Agent DevOps"},
    {"name": "workflow::en-cours", "color": "#1F75CB", "description": "En cours"},
    {"name": "workflow::termine", "color": "#108548", "description": "Terminé"},
    {"name": "workflow::abandonne", "color": "#666666", "description": "Abandonné"},
]
MILESTONES_GITLAB = [
    {"id": 10, "iid": 1, "title": "Phase 1 — POC", "description": "Le POC",
     "state": "active", "due_date": "2026-03-31"},
    {"id": 11, "iid": 2, "title": MILESTONE_ESPERLUETTE, "description": "Projets & espace",
     "state": "closed", "due_date": ""},
]


# ==================================================================================================
# Le répertoire jetable
# ==================================================================================================
@dataclass
class Migration:
    """Un dépôt jetable équipé des vrais scripts de migration, d'un `gh` et d'un `glab` factices."""

    racine: Path
    sortie: Path
    fauxbin: Path
    depot: Path
    gitlab_json: Path

    @property
    def journal(self) -> Path:
        return self.depot / "journal.tsv"

    # --- pilotage des doubles ---
    def etat_depot(self) -> dict:
        """L'état du dépôt simulé, reconstruit à partir des corps que le double a rangés.

        C'est ICI que le JSON est compris, et nulle part ailleurs : le double se contente de
        classer des fichiers. Un corps traverse donc l'import puis revient au test **sans avoir
        été re-sérialisé une seule fois** — ce qui est exactement ce que la fidélité des octets
        demande, et qu'un double qui « comprendrait » les payloads aurait détruit en silence.
        """

        def lire(chemin: Path) -> dict:
            return json.loads(chemin.read_text(encoding="utf-8"))

        seq = self.depot / "seq"
        issues: dict[str, dict] = {}
        for chemin in sorted(self.depot.glob("issue-*.json")):
            numero = chemin.stem.split("-", 1)[1]
            corps = lire(chemin)
            etat = self.depot / f"etat-{numero}.json"
            ferme = lire(etat) if etat.exists() else {}
            # Le rang est comparé après découpage, et non laissé au motif du `glob` :
            # « commentaire-1-* » matcherait aussi « commentaire-11-1 ». Sans objet sur un jeu
            # d'essai à quatre objets, faux dès qu'il en porterait dix.
            miens = [
                c for c in self.depot.glob("commentaire-*.json")
                if c.stem.split("-")[1] == numero
            ]
            commentaires = [
                lire(c)["body"] for c in sorted(miens, key=lambda p: int(p.stem.rsplit("-", 1)[1]))
            ]
            issues[numero] = {
                "number": int(numero),
                "title": corps["title"],
                "body": corps.get("body", ""),
                "labels": corps.get("labels", []),
                "milestone": corps.get("milestone"),
                "state": ferme.get("state", "open"),
                "state_reason": ferme.get("state_reason"),
                "comments": commentaires,
            }
        milestones = []
        for k, chemin in enumerate(
            sorted(self.depot.glob("milestone-*.json"), key=lambda p: int(p.stem.split("-")[1])), 1
        ):
            milestones.append({"number": k, **lire(chemin)})
        etiquettes = self.depot / "labels.txt"
        return {
            "sequence": int(seq.read_text(encoding="utf-8")) if seq.exists() else 0,
            "issues": issues,
            "milestones": milestones,
            "labels": [
                {"name": n}
                for n in (
                    etiquettes.read_text(encoding="utf-8").split() if etiquettes.exists() else []
                )
            ],
        }

    def pose_scenario(self, **entrees: object) -> None:
        """La panne à simuler, en « clé=valeur » — le format que lit `val` côté bash.

        Une valeur `None` retire la clé : c'est ainsi qu'une reprise annule la panne du premier
        lancement sans avoir à réécrire tout le fichier.
        """
        chemin = self.depot / "scenario"
        courant = {}
        if chemin.exists():
            for ligne in chemin.read_text(encoding="utf-8").splitlines():
                if "=" in ligne:
                    cle, _, valeur = ligne.partition("=")
                    courant[cle] = valeur
        for cle, valeur in entrees.items():
            if valeur is None:
                courant.pop(cle, None)
            else:
                courant[cle] = "1" if valeur is True else str(valeur)
        chemin.write_text(
            "".join(f"{c}={v}\n" for c, v in courant.items()), encoding="utf-8", newline="\n"
        )

    def pose_gitlab(self, descriptions: dict[str, str]) -> None:
        self.gitlab_json.write_text(
            json.dumps({"descriptions": descriptions}, ensure_ascii=False),
            encoding="utf-8",
            newline="\n",
        )

    def appels(self) -> list[str]:
        if not self.journal.exists():
            return []
        return [ligne for ligne in self.journal.read_text(encoding="utf-8").splitlines() if ligne]

    def ecritures(self) -> list[str]:
        """Les appels `gh` qui ÉCRIVENT — vides tant qu'une commande annoncée sans effet l'est."""
        return [ligne for ligne in self.appels() if "--method\tPOST" in ligne
                or "--method\tPATCH" in ligne]

    # --- exécution ---
    # `--tsv` par défaut, et ce n'est pas qu'une économie : la sortie machine est le contrat
    # explicite des deux scripts (« clé <TAB> valeur »), là où la sortie humaine ALIGNE ses colonnes
    # en comptant des colonnes d'affichage — un test qui matcherait cet alignement se casserait au
    # premier libellé rallongé, pour une raison sans rapport avec ce qu'il vérifie. Elle est aussi
    # deux fois plus rapide (`kv` n'appelle plus `largeur`, qui forke un `printf | sed` par ligne).
    # `texte=True` pour les rares tests qui portent sur un message d'accompagnement (`note`), que
    # la sortie machine ne rend pas — c'est précisément ce qui la rend machine.
    def export(self, *args: str, texte: bool = False) -> subprocess.CompletedProcess[str]:
        return self._bash(
            "scripts/migration/export-gitlab.sh", *args, *([] if texte else ["--tsv"])
        )

    def importe(self, *args: str, texte: bool = False) -> subprocess.CompletedProcess[str]:
        return self._bash(
            "scripts/migration/import-github.sh", "--source", str(self.sortie),
            "--depot", DEPOT, "--pause", "0", *args, *([] if texte else ["--tsv"]),
        )

    def _bash(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        environnement = os.environ.copy()
        environnement.update({
            "PATH": os.pathsep.join([str(self.fauxbin), environnement.get("PATH", "")]),
            "HOME": str(self.racine.parent / "home"),
            "MAESTRO_MIGRATION_DIR": str(self.sortie),
            "MIG_GL_PROJECT": PROJET_GL,
            "MAESTRO_GITHUB_REPO": DEPOT,
            "MAESTRO_FAUX_DEPOT": str(self.depot),
            "MAESTRO_FAUX_NOM": DEPOT,
            "MAESTRO_FAUX_GITLAB": str(self.gitlab_json),
            # Le retry de mig_graphql_read ne sert qu'aux hoquets réseau : une réponse
            # volontairement muette ne doit pas coûter trois secondes au test.
            "MIG_GQL_RETRIES": "1",
            "MIG_GQL_RETRY_DELAY": "0",
        })
        assert BASH is not None
        return subprocess.run(  # noqa: S603
            [BASH, str(self.racine / script), *args],
            cwd=str(self.racine),
            env=environnement,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )

    # --- lecture des artefacts ---
    def jsonl(self) -> list[bytes]:
        brut = (self.sortie / "backlog.jsonl").read_bytes()
        return [ligne for ligne in brut.split(b"\n") if ligne]

    def tickets(self) -> list[dict]:
        return [json.loads(ligne.decode("utf-8")) for ligne in self.jsonl()]

    def manifeste(self) -> list[list[str]]:
        texte = (self.sortie / "manifeste.tsv").read_text(encoding="utf-8")
        return [ligne.split("\t") for ligne in texte.splitlines() if ligne
                and not ligne.startswith("#")]

    def plan(self) -> list[list[str]]:
        texte = (self.sortie / "import" / "plan.tsv").read_text(encoding="utf-8")
        return [ligne.split("\t") for ligne in texte.splitlines() if ligne
                and not ligne.startswith("#")]

    def journal_import(self) -> list[list[str]]:
        chemin = self.sortie / "import" / "journal.tsv"
        if not chemin.exists():
            return []
        return [ligne.split("\t") for ligne in
                chemin.read_text(encoding="utf-8").splitlines() if ligne]


def _shim_bash(fauxbin: Path, nom: str, source: str) -> None:
    """Le double `gh` : un script bash posé tel quel, sans lanceur ni interpréteur intermédiaire.

    C'est l'économie qui rend la suite jouable — voir l'en-tête de `faux_gh_migration.sh`.
    """
    cible = fauxbin / nom
    cible.write_text(source, encoding="utf-8", newline="\n")
    cible.chmod(0o755)


def _shim_python(fauxbin: Path, nom: str, source: str) -> None:
    """Un exécutable Python appelé par un lanceur nu, pour que `command -v <nom>` le trouve.

    Réservé au double `glab`, que trois tests d'export appellent une poignée de fois : son coût
    (~0,38 s par appel sous MSYS) ne pèse rien à cette échelle, et il lui faut un vrai encodeur
    JSON pour rendre une description échappée comme l'API GitLab la rend.
    """
    (fauxbin / f"faux_{nom}.py").write_text(source, encoding="utf-8", newline="\n")
    lanceur = fauxbin / nom
    interpreteur = sys.executable.replace(chr(92), "/")
    lanceur.write_text(
        "#!/usr/bin/env bash\n"
        f'exec "{interpreteur}" "{(fauxbin / f"faux_{nom}.py").as_posix()}" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    lanceur.chmod(0o755)


def monte_migration(tmp_path: Path, tickets: list[Ticket] | None = None) -> Migration:
    """Monte le répertoire jetable, ses doubles, et un export DÉJÀ ASSEMBLÉ sous `pages/`.

    Les pages sont posées mais l'export n'est pas joué : c'est à chaque test de l'appeler dans le
    mode qu'il veut mesurer. Les tests d'import, eux, passent par `prepare_export` — l'import lit un
    export, jamais des pages.

    ⚠ C'EST UNE FABRIQUE ET NON UNE FIXTURE, pour la même raison que `harnais_forge.monte_depot` :
    importer une fixture la mettrait en collision, aux yeux du linter, avec le paramètre de même
    nom de chaque test.
    """
    assert GIT is not None
    racine = tmp_path / "clone"
    fauxbin = tmp_path / "fauxbin"
    depot = tmp_path / "depot-github"
    for dossier in (fauxbin, depot, tmp_path / "home"):
        dossier.mkdir()
    racine.mkdir()

    # Un vrai dépôt git : `lib.sh`, que l'export source, résout la racine du dépôt au chargement.
    subprocess.run(  # noqa: S603
        [GIT, "-c", "core.hooksPath=", "init", "--quiet", "--initial-branch=main"],
        cwd=str(racine), check=True, capture_output=True,
    )

    for relatif in (
        "scripts/gitlab/lib.sh",
        "scripts/migration/gitlab-lecture.sh",
        "scripts/migration/export-gitlab.sh",
        "scripts/migration/import-github.sh",
    ):
        cible = racine / relatif
        cible.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RACINE / relatif, cible)

    sortie = racine / ".maestro" / "migration"
    (sortie / "pages").mkdir(parents=True)
    (sortie / "pages" / "page-001.json").write_bytes(page(tickets or BACKLOG))

    _shim_bash(fauxbin, "gh", FAUX_GH)
    _shim_python(fauxbin, "glab", FAUX_GLAB)

    mig = Migration(
        racine=racine,
        sortie=sortie,
        fauxbin=fauxbin,
        depot=depot,
        gitlab_json=tmp_path / "faux-gitlab.json",
    )
    mig.pose_scenario()
    mig.pose_gitlab({str(t.iid): t.description for t in (tickets or BACKLOG)})
    return mig


def prepare_export(mig: Migration) -> None:
    """Joue l'export hors ligne et pose les référentiels, pour donner à l'import sa matière.

    Les référentiels (`labels.json`, `milestones.json`) sont écrits ici plutôt que par l'export :
    `fetch_referentiels` est la seule étape que `--hors-ligne` ne joue pas, puisqu'elle interroge
    l'API REST de GitLab. Un import a besoin des deux — un ticket ne porte que le NOM de son
    milestone, et l'API des issues en demande le NUMÉRO GitHub.
    """
    acheve = mig.export("--hors-ligne")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    # Les référentiels portent les échappements de GitLab, `&` compris : c'est cette forme-là
    # que l'import journalise comme clé de milestone, et c'est elle qui déclenche le piège du
    # `awk -v` (#340). Les écrire « propres » rendrait le test vert sur une donnée que la vraie
    # migration n'a jamais vue.
    (mig.sortie / "labels.json").write_text(
        _rest(LABELS_GITLAB), encoding="utf-8", newline="\n"
    )
    (mig.sortie / "milestones.json").write_text(
        _rest(MILESTONES_GITLAB), encoding="utf-8", newline="\n"
    )
    # L'import LIT le verdict de l'export au lieu de le supposer (#337 distingue « produit » de
    # « digne de confiance ») : un export hors ligne n'a pas joué la preuve par seconde voie, donc
    # son resume.txt ne porte pas « vérification : OK » et l'import le refuserait à juste titre.
    resume = (mig.sortie / "resume.txt").read_text(encoding="utf-8")
    (mig.sortie / "resume.txt").write_text(
        resume.replace(
            "vérification : contrôles hors ligne seuls (preuve par seconde voie NON jouée)",
            "vérification : OK, par octets (dont preuve par seconde voie GraphQL vs REST)",
        ),
        encoding="utf-8", newline="\n",
    )


@pytest.fixture
def mig(tmp_path: Path) -> Migration:
    return monte_migration(tmp_path)


@pytest.fixture
def importable(tmp_path: Path) -> Migration:
    monte = monte_migration(tmp_path)
    prepare_export(monte)
    return monte


@pytest.fixture(scope="module")
def exporte(tmp_path_factory: pytest.TempPathFactory) -> Migration:
    """Un export hors ligne du backlog d'essai, joué UNE FOIS pour qui n'en lit que le résultat."""
    monte = monte_migration(tmp_path_factory.mktemp("export"))
    acheve = monte.export("--hors-ligne")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    return monte


@pytest.fixture(scope="module")
def importe_complet(tmp_path_factory: pytest.TempPathFactory) -> Migration:
    """Un import complet, joué une fois pour les tests qui n'en lisent que le résultat.

    Ils ne veulent que l'état final et ne le modifient pas : le rejouer neuf fois ne prouverait
    rien de plus. Ceux qui décrivent une PANNE gardent leur propre dépôt (fixture `importable`) —
    une panne est par définition ce qui change l'état, et deux tests qui se le partageraient se
    rendraient dépendants de leur ordre d'exécution.

    ⚠ **Portée « module », et surtout pas un partage entre workers xdist.** La première version le
    faisait — un `mkdir` atomique désignait le worker qui monte l'import, les autres attendaient
    son témoin — et c'est cette économie qui a fait le plus de dégâts : l'import a dépassé son
    délai sous la charge de la suite complète, le témoin n'a jamais été écrit, et **sept workers
    ont attendu dix minutes chacun**, sérialisant tout ce qui restait. 1 h 17 de suite au lieu de
    ~15 min, onze tests rouges par contagion. Une attente partagée transforme un échec local en
    panne globale : le vrai remède n'était pas de mieux attendre mais de rendre l'import assez
    bon marché pour qu'un worker puisse le rejouer (cf. `faux_gh_migration.sh`).
    """
    monte = monte_migration(tmp_path_factory.mktemp("import-complet"))
    prepare_export(monte)
    acheve = monte.importe()
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    # Aucun bruit sur stderr : `echec()` y écrit, et lui seul. Une ligne de plus est un contrôle
    # qui a mal tourné sans faire échouer — la forme d'erreur la plus facile à ne jamais voir.
    assert acheve.stderr == "", acheve.stderr
    return monte


# ==================================================================================================
# EXPORT — le découpage, les octets, l'idempotence
# ==================================================================================================
def test_le_decoupage_rend_une_ligne_par_ticket_dans_l_ordre_des_pages(tmp_path: Path):
    """Deux pages, un ticket par ligne, l'ordre des pages conservé.

    Le format JSONL n'est tenable que si aucun élément émis ne contient de saut de ligne brut : le
    découpage joint les lignes SANS séparateur, un saut de ligne étant insignifiant entre deux
    jetons JSON et impossible à l'intérieur d'une chaîne (il y serait échappé « \\n »).
    """
    mig = monte_migration(tmp_path)
    (mig.sortie / "pages" / "page-001.json").write_bytes(page(BACKLOG[:2], suite=True))
    (mig.sortie / "pages" / "page-002.json").write_bytes(page(BACKLOG[2:]))

    acheve = mig.export("--hors-ligne")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    assert [t["iid"] for t in mig.tickets()] == ["1", "3", "4"]
    assert [ligne[0] for ligne in mig.manifeste()] == ["1", "3", "4"]
    assert "lignes == iid distincts\t3" in acheve.stdout
    assert "verdict\thors-ligne" in acheve.stdout


def test_les_octets_de_la_description_traversent_sans_etre_reencodes(exporte: Migration):
    """La preuve par octets : le littéral échappé de la page se retrouve TEL QUEL dans le jsonl.

    Deux contrôles, et le premier est le plus fort. Comparer les textes DÉCODÉS dirait seulement
    que le sens a survécu ; comparer les octets ENCORE ÉCHAPPÉS dit qu'aucune étape n'a décodé —
    ce qui est l'invariant réel, un aller-retour décodage/ré-encodage étant précisément là où le
    mojibake de #141 s'était introduit.
    """
    litteral = _chaine(DESCRIPTION_1).encode("utf-8")
    assert litteral in (exporte.sortie / "pages" / "page-001.json").read_bytes()
    assert litteral in exporte.jsonl()[0]

    ticket = exporte.tickets()[0]
    assert ticket["description"] == DESCRIPTION_1
    assert ticket["title"] == "Premier ticket — avec un tiret cadratin"
    note = ticket["widgets"][6]["discussions"]["nodes"][0]["notes"]["nodes"][0]
    assert note["body"] == "Un commentaire — é à ç.\n\nAvec un paragraphe."


def test_une_accolade_citee_ne_coupe_pas_le_ticket(exporte: Migration):
    """Le découpage compte des accolades en SAUTANT ce qui est entre guillemets.

    Sans cette règle, le bloc de code de `DESCRIPTION_1` (`{"cle": [...]}`) fermerait l'objet trop
    tôt : la ligne serait tronquée, la suivante commencerait au milieu d'un ticket, et le compte
    « lignes == iid distincts » ne le dirait même pas — les deux moitiés porteraient un iid.
    """
    lignes = exporte.jsonl()

    assert len(lignes) == len(BACKLOG)
    for ligne in lignes:
        assert ligne.startswith(b'{"iid":"')
        json.loads(ligne.decode("utf-8"))          # chaque ligne est un objet JSON complet


def test_l_assemblage_est_idempotent(mig: Migration):
    """Rejouer l'export hors ligne rend les mêmes octets — c'est ce qui autorise à le rejouer.

    `resume.txt` est volontairement hors du contrôle : il porte l'heure de production. L'idempotence
    porte sur la MATIÈRE, pas sur la trace.
    """
    assert mig.export("--hors-ligne").returncode == 0
    empreinte = {
        nom: (mig.sortie / nom).read_bytes()
        for nom in ("backlog.jsonl", "manifeste.tsv", "trous.txt")
    }

    assert mig.export("--hors-ligne").returncode == 0
    for nom, octets in empreinte.items():
        assert (mig.sortie / nom).read_bytes() == octets, nom


def test_les_trous_sont_les_iid_absents_de_la_plage(mig: Migration):
    """`trous.txt` : les iid absents entre le plus petit et le plus grand RÉELLEMENT exportés.

    Calculé sur l'export et non sur ce que l'inventaire annonçait — un export est la seule source
    qui engage l'import.
    """
    acheve = mig.export("--hors-ligne")
    assert acheve.returncode == 0

    assert (mig.sortie / "trous.txt").read_text(encoding="utf-8").split() == [str(TROU)]
    assert "plage d'iid\t#1 → #4" in acheve.stdout
    assert "trous à combler (trous.txt)\t1" in acheve.stdout
    assert "tickets exportés\t3" in acheve.stdout
    assert "iid attendus sur la plage\t4" in acheve.stdout


def test_le_manifeste_ancre_ses_extractions_sur_leur_conteneur(exporte: Migration):
    """« title » désigne le ticket, mais aussi chaque label et le milestone — d'où l'ancrage.

    Une recherche à plat rendrait un manifeste faux d'apparence PLAUSIBLE : le milestone y prendrait
    le titre du premier label, et rien ne le signalerait.
    """
    ligne = {colonnes[0]: colonnes for colonnes in exporte.manifeste()}["1"]

    assert ligne[3] == "en-cours"                 # workflow::
    assert ligne[4] == "infra"                    # type::
    assert ligne[5] == "devops"                   # agent::
    assert ligne[7] == "Phase 1 — POC"            # milestone, et non « type::infra »
    assert ligne[10] == "7200"                    # temps passé
    assert ligne[11] == "alice"                   # assignés, et non l'auteur ni un commentateur
    assert ligne[12] == "3"                       # liés
    assert (ligne[13], ligne[14]) == ("5", "3")   # notes, dont humaines
    assert ligne[17] == "Premier ticket — avec un tiret cadratin"


def test_sans_accent_ni_em_dash_la_verification_echoue(tmp_path: Path):
    """La contre-preuve : zéro accent sur un backlog français, ce n'est pas « propre ».

    Un contrôle qui ne peut pas échouer ne vérifie rien. Celui-ci existe pour attraper l'export qui
    aurait MANGÉ les caractères non-ASCII plutôt que de les corrompre — un défaut qu'aucune
    signature de mojibake ne montrerait, puisqu'il ne laisse aucune trace.
    """
    mig = monte_migration(tmp_path, [
        Ticket(iid=1, titre="Sans accent", description="Rien que de l'ASCII ici.\n"),
    ])
    acheve = mig.export("--hors-ligne")

    assert acheve.returncode == 3, acheve.stdout
    assert "UTF-8 attendu présent\t« é »=0, « — »=0 — suspect ✗" in acheve.stdout
    assert "verdict\techec" in acheve.stdout
    # Et le verdict est écrit sur disque : l'import LIT `resume.txt` au lieu de rejouer l'export.
    resume = (mig.sortie / "resume.txt").read_text(encoding="utf-8")
    assert "vérification : ÉCHEC" in resume
    assert "ne pas importer sur cet export" in resume


def test_hors_ligne_ne_parle_jamais_a_gitlab(mig: Migration):
    """`--hors-ligne` doit tenir sans `glab` du tout : c'est ce qui rend l'export rejouable.

    Le PATH est privé de son shim `glab` — un export qui l'appellerait quand même échouerait ici,
    et un export qui « marche » parce qu'un poste a `glab` installé n'a rien prouvé.
    """
    (mig.fauxbin / "glab").unlink()
    acheve = mig.export("--hors-ligne", texte=True)

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    # Le verdict NOMME ce qui n'a pas été joué : « vérifié » sans la preuve par seconde voie n'est
    # pas le même mot que « vérifié » avec elle, et c'est sur ce mot que l'import s'engagera.
    assert "preuve par seconde voie : sautée" in acheve.stdout
    assert "n'a pas été jouée" in acheve.stdout
    assert "preuve par seconde voie NON jouée" in (
        mig.sortie / "resume.txt"
    ).read_text(encoding="utf-8")


def test_la_preuve_par_seconde_voie_valide_un_export_fidele(mig: Migration):
    """GraphQL et REST tombent d'accord au bit près : l'export est fidèle."""
    assert mig.export("--hors-ligne").returncode == 0
    acheve = mig.export("--verifier", "--echantillon", "2")

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "0 divergent(s)" in acheve.stdout
    assert "octets identiques" in acheve.stdout


def test_une_divergence_avec_la_voie_rest_echoue_en_code_3(mig: Migration):
    """Et il PEUT échouer : une description qui diffère d'un octet suffit.

    C'est le contrôle qui donne son sens au mot « vérifié » du verdict — l'import lit ce mot dans
    `resume.txt` avant de créer 345 objets irréversibles.
    """
    assert mig.export("--hors-ligne").returncode == 0
    mig.pose_gitlab({
        **{str(t.iid): t.description for t in BACKLOG},
        "1": DESCRIPTION_1.replace("é è à", "e e a"),
    })

    acheve = mig.export("--verifier", "--echantillon", "1")

    assert acheve.returncode == 3, acheve.stdout
    assert "DIVERGENT" in acheve.stdout
    assert "1 divergent(s)" in acheve.stdout
    assert "verdict\techec" in acheve.stdout


def test_un_mojibake_deja_dans_gitlab_est_signale_sans_faire_echouer(tmp_path: Path):
    """Une signature n'est pas un verdict : elle ne dit pas QUI a corrompu.

    Cinq tickets du vrai backlog en portaient une, et aucun n'était de notre fait (#337). Échouer
    dessus dirait « l'export est faux » alors qu'il est exact — et masquerait la seule information
    qui compte : ces tickets arriveront abîmés sur GitHub si personne ne tranche.
    """
    abime = "Une description dejÃ  abÃ®mÃ©e dans GitLab — é.\n"
    mig = monte_migration(tmp_path, [
        Ticket(iid=1, titre="Victime de #141", description=abime),
        Ticket(iid=2, titre="Sain", description="Tout va bien — é.\n"),
    ])
    assert mig.export("--hors-ligne").returncode == 0

    acheve = mig.export("--verifier", "--echantillon", "0")

    assert acheve.returncode == 0, acheve.stdout
    assert "1 ticket(s) à rejuger" in acheve.stdout
    assert "corruption ANTÉRIEURE, pas la nôtre" in acheve.stdout
    source = (mig.sortie / "mojibake-source.txt").read_text(encoding="utf-8")
    assert [ligne for ligne in source.splitlines() if not ligne.startswith("#")] == ["1"]


# ==================================================================================================
# IMPORT — le plan, l'ordre, les trous
# ==================================================================================================
def test_check_rend_le_plan_sans_aucune_ecriture(importable: Migration):
    """`--check` est la première commande à jouer, et elle ne doit RIEN écrire.

    La preuve ne se lit pas dans la sortie du script mais dans le journal du double : la seule
    manière de démontrer une abstention est de constater qu'aucune écriture n'a été reçue.
    """
    acheve = importable.importe("--check")

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert importable.ecritures() == []
    assert importable.etat_depot()["sequence"] == 0
    assert "aucune écriture" in acheve.stdout
    assert importable.journal_import() == []


def test_le_plan_couvre_la_plage_entiere_ticket_ou_bouche_trou(importable: Migration):
    """Une ligne par NUMÉRO CIBLE, de 1 au plus grand iid — jamais une ligne par ticket."""
    assert importable.importe("--check").returncode == 0

    plan = importable.plan()
    assert [ligne[0] for ligne in plan] == [str(n) for n in range(1, PLAGE_MAX + 1)]
    assert [ligne[1] for ligne in plan] == [
        "bouche-trou" if n == TROU else "ticket" for n in range(1, PLAGE_MAX + 1)
    ]


def test_un_manifeste_en_desaccord_avec_trous_refuse_avant_toute_ecriture(importable: Migration):
    """Un numéro ni ticket ni trou déclaré signe un export incohérent : on s'arrête AVANT d'écrire.

    Le laisser passer créerait le décalage silencieux que tout le script cherche à éviter.

    Le désaccord est simulé par un `trous.txt` NON VIDE mais faux — un fichier vide serait déjà
    rejeté un cran plus tôt, par le contrôle « export incomplet », et le test ne prouverait pas ce
    qu'il annonce.
    """
    (importable.sortie / "trous.txt").write_text("7\n", encoding="utf-8", newline="\n")

    acheve = importable.importe()

    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "ni ticket ni trou déclaré : 2" in acheve.stdout + acheve.stderr
    assert importable.ecritures() == []


def test_un_export_non_verifie_est_refuse_avant_toute_ecriture(importable: Migration):
    """« produit » et « digne de confiance » sont deux questions distinctes (code 3 de #337).

    Importer sur un export non vérifié, c'est créer des objets irréversibles sur une matière dont
    personne n'a dit qu'elle était bonne.
    """
    (importable.sortie / "resume.txt").write_text(
        "vérification : ÉCHEC (1 contrôle(s)) — ne pas importer sur cet export\n",
        encoding="utf-8", newline="\n",
    )

    acheve = importable.importe()

    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "n'a pas passé sa propre vérification" in acheve.stdout + acheve.stderr
    assert importable.ecritures() == []


def test_la_sequence_est_alignee_et_les_trous_sont_combles(importe_complet: Migration):
    """L'invariant du chantier : #n sur GitHub est #n de GitLab, trous compris.

    Les iid ABSENTS côté GitLab consomment un objet **bouche-trou**, créé puis fermé. Ne pas les
    combler décalerait tout ce qui suit — et un décalage ne rend pas les `Refs #<n>` de
    l'historique morts mais **faux**, donc plausibles et jamais signalés (docs/27 §6).

    Le bouche-trou n'a qu'un travail — consommer un numéro — mais il sera lu un jour par quelqu'un
    qui y arrive depuis un « Refs #2 » : d'où un corps qui explique, et un label dédié pour les
    filtrer tous d'un clic sans polluer aucun décompte par `type::`.
    """
    depot = importe_complet.etat_depot()

    assert depot["sequence"] == PLAGE_MAX
    assert sorted(int(n) for n in depot["issues"]) == list(range(1, PLAGE_MAX + 1))
    for ticket in BACKLOG:
        assert depot["issues"][str(ticket.iid)]["title"] == ticket.titre

    trou = depot["issues"][str(TROU)]
    assert trou["title"].startswith("[trou] #2")
    assert trou["labels"] == ["import::bouche-trou"]
    assert (trou["state"], trou["state_reason"]) == ("closed", "not_planned")
    assert "consomme le numéro" in trou["body"]
    assert trou["comments"] == []


def test_le_contenu_du_ticket_arrive_intact(importe_complet: Migration):
    """Octets, état de fermeture et commentaires : ce que l'objet créé porte réellement.

    **Les octets** d'abord : la description créée est celle de GitLab au bit près. Aucune étape ne
    décode puis ré-encode du texte — c'est l'aller-retour où le mojibake de #141 s'était introduit.

    **L'état** ensuite. « Abandonné » et « Doublon » ne sont pas des tickets réalisés, et
    `state_reason` est la seule nuance du cycle de vie que GitHub porte NATIVEMENT ; le reste
    reste dans les labels `workflow::`, transposés tels quels. ⚠ Le motif qui les distinguait était
    `grep -E '^B\\t(workflow::abandonne|…)$'`, où « \\t » ne désigne PAS une tabulation : les
    expressions rationnelles étendues ne connaissent pas cet échappement, et le motif matchait le
    littéral « Btworkflow::… ». Il ne pouvait donc jamais tomber juste — tous les tickets fermés
    partaient en « completed » (#345). Rien ne le montrait : la fermeture réussissait, seul son
    motif était faux.

    **Les commentaires** enfin. Les 144 commentaires humains sont repris, le journal d'activité de
    GitLab reste à GitLab. L'en-tête est une DONNÉE et non une décoration : l'API attribue tout
    commentaire au porteur du jeton, donc l'auteur d'origine ne survit que si on l'écrit — et il
    est en CODE (`@nom`), jamais nu, « @nom » écrit nu étant une mention qui notifierait un
    homonyme inconnu. ⚠ Un compte GitLab supprimé rend `"author":null`, donc un champ VIDE au
    milieu du flux TSV interne : `IFS=$'\\t' read` fusionne deux tabulations — la tabulation est un
    caractère « IFS whitespace » pour bash —, le corps passait dans la colonne de la date, et le
    `[ -n "$ncorps" ] || continue` de la boucle SAUTAIT le commentaire. Une perte de donnée
    silencieuse au milieu d'un import irréversible (#345).
    """
    issues = importe_complet.etat_depot()["issues"]

    assert issues["1"]["body"] == DESCRIPTION_1
    assert issues["4"]["body"] == BACKLOG[-1].description
    assert issues["1"]["title"] == "Premier ticket — avec un tiret cadratin"

    assert (issues["3"]["state"], issues["3"]["state_reason"]) == ("closed", "completed")
    assert (issues["4"]["state"], issues["4"]["state_reason"]) == ("closed", "not_planned")
    assert issues["1"]["state"] == "open"

    commentaires = issues["1"]["comments"]
    assert len(commentaires) == 4                              # métadonnées + 3 humains
    assert commentaires[0].startswith("<!-- maestro:meta v1")
    assert "Un commentaire — é à ç." in commentaires[1]
    assert "`@bob`" in commentaires[1] and "**@bob**" not in commentaires[1]
    assert "Réponse." in commentaires[2]
    for corps in commentaires:
        assert "added ~52011709 labels" not in corps
        assert "mentioned in issue" not in corps

    orphelins = [c for c in commentaires if "Écrit par un compte depuis supprimé" in c]
    assert len(orphelins) == 1
    assert "`@compte supprimé`" in orphelins[0]
    assert "@`" not in orphelins[0]                    # jamais un « @ » suivi de rien


def test_les_referentiels_precedent_les_tickets_et_leur_jointure_tient(
    importe_complet: Migration,
):
    """Labels et milestones sont créés AVANT les tickets, et la jointure par titre tient.

    **L'ordre** n'est pas qu'une dépendance de données : la première écriture de l'import est un
    LABEL, et les labels ne consomment aucun numéro d'issue. Un jeton en lecture seule échoue donc
    là, avant que la séquence ait commencé, et sans rien coûter d'irréversible.

    **La jointure** est le piège du `awk -v`, et il est silencieux — c'est ce qui le rend
    dangereux. `awk -v c="…"` interprète les séquences d'échappement DE LA VALEUR : le `\\u0026`
    que GitLab rend pour « & » y devient « u0026 », donc la comparaison porte sur un texte que
    personne n'a écrit. `fait` qui se trompe ne coûte qu'un arrêt bruyant ; `valeur_journal` qui se
    trompe rend une chaîne vide — et un milestone introuvable n'échoue pas, il importe le ticket
    SANS milestone. Trois phases du vrai backlog seraient arrivées nues, sans un mot.

    **L'échéance**, enfin : un champ vide au MILIEU d'une ligne décale tous les suivants, et le
    lecteur ne le voit pas. `IFS=$'\\t' read -r iid etat echeance titre desc` sur un milestone sans
    `due_date` lisait sa DESCRIPTION comme titre et son TITRE comme échéance — milestone créé sous
    le mauvais nom, `due_on` absurde, et jointure ticket → milestone qui ne retrouve plus rien
    (#345). Le milestone à esperluette du jeu d'essai est justement sans échéance : c'est ce qui a
    fait sortir le défaut.
    """
    depot = importe_complet.etat_depot()

    types = [ligne[0] for ligne in importe_complet.journal_import()]
    assert types.index("label") < types.index("milestone") < types.index("issue")
    noms = {le["name"] for le in depot["labels"]}
    assert {le["name"] for le in LABELS_GITLAB} | {"import::bouche-trou"} == noms

    numeros = {m["title"]: m["number"] for m in depot["milestones"]}
    assert set(numeros) == {"Phase 1 — POC", MILESTONE_ESPERLUETTE}
    assert depot["issues"]["4"]["milestone"] == numeros[MILESTONE_ESPERLUETTE]
    assert depot["issues"]["1"]["milestone"] == numeros["Phase 1 — POC"]

    esperluette = [m for m in depot["milestones"] if m["title"] == MILESTONE_ESPERLUETTE][0]
    assert esperluette.get("due_on") is None           # absente, et non « le titre + T12:00:00Z »
    phase = [m for m in depot["milestones"] if m["title"] == "Phase 1 — POC"][0]
    assert phase["due_on"] == "2026-03-31T12:00:00Z"   # midi UTC : minuit basculerait d'un jour


def test_le_payload_rend_les_octets_qui_partiront(importable: Migration):
    """`--payload` est le seul point de contrôle AVANT une action irréversible.

    Il rend ce que le VRAI code construirait — pas un double qui pourrait diverger de lui — sans
    réseau ni écriture : le corps se décode par un parseur JSON et se compare aux octets d'origine.
    """
    acheve = importable.importe("--payload", "1")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert importable.ecritures() == []

    issue, meta = (json.loads(ligne) for ligne in acheve.stdout.strip().splitlines() if ligne)
    assert issue["body"] == DESCRIPTION_1
    assert issue["title"] == "Premier ticket — avec un tiret cadratin"
    assert set(issue["labels"]) == {"type::infra", "agent::devops", "workflow::en-cours"}
    assert "<!-- maestro:meta v1 iid=1 temps_s=7200" in meta["body"]


def test_un_milestone_deja_la_est_retrouve_malgre_deux_echappements(importable: Migration):
    """Retrouver un milestone par son titre compare DEUX APIS QUI N'ÉCHAPPENT PAS PAREIL.

    GitLab rend « Projets \\u0026 espace », GitHub rend « Projets & espace ». Comparer les formes
    brutes ferait échouer la recherche sur les seuls titres contenant un « & » — la pire forme de
    défaut, puisque tout le reste passe. Le chemin ne s'emprunte qu'à la REPRISE : le milestone
    existe déjà côté GitHub, et son numéro doit être retrouvé au lieu d'être supposé.

    On retire du journal les seules lignes « milestone », ce qui remet l'import dans l'état exact
    d'une coupure entre la création d'un milestone et son enregistrement.
    """
    assert importable.importe("--max", "1").returncode == 5
    journal = importable.sortie / "import" / "journal.tsv"
    journal.write_text(
        "".join(
            ligne + "\n"
            for ligne in journal.read_text(encoding="utf-8").splitlines()
            if ligne and not ligne.startswith("milestone\t")
        ),
        encoding="utf-8", newline="\n",
    )

    reprise = importable.importe("--max", "1")

    assert reprise.returncode == 5, reprise.stdout + reprise.stderr
    assert "milestones créés / déjà là / en échec\t0 / 2 / 0" in reprise.stdout
    retrouves = {ligne[1]: ligne[2] for ligne in importable.journal_import()
                 if ligne[0] == "milestone"}
    assert retrouves == {"Phase 1 — POC": "1",
                         MILESTONE_ESPERLUETTE.replace("&", "\\u0026"): "2"}


# ==================================================================================================
# IMPORT — la reprise et la rupture de séquence
# ==================================================================================================
def test_un_import_coupe_reprend_sans_doublon(importable: Migration):
    """Le journal fait foi, et la reprise n'a pas d'option : relancer suffit.

    `--max` est le seul arrêt PROPRE du script (code 5, « interrompu mais REPRENABLE ») : il coupe
    entre deux objets, exactement là où une coupure réelle laisse le journal cohérent.

    ⚠ Il compte les objets **traités**, pas les objets **créés** — « les n premiers objets de la
    plage », déjà-faits compris. D'où le `--max 4` du second lancement : un second `--max 2`
    repasserait sur #1 et #2, les trouverait au journal, et n'avancerait pas d'une ligne.
    """
    premier = importable.importe("--max", "2")
    assert premier.returncode == 5, premier.stdout + premier.stderr
    assert importable.etat_depot()["sequence"] == 2

    dernier = importable.importe()
    assert dernier.returncode == 0, dernier.stdout + dernier.stderr

    depot = importable.etat_depot()
    assert depot["sequence"] == PLAGE_MAX
    # Aucun doublon : autant de lignes « issue » au journal que de numéros dans la plage, et un
    # seul commentaire de métadonnées par ticket.
    lignes = [ligne for ligne in importable.journal_import() if ligne[0] == "issue"]
    assert sorted(int(ligne[1]) for ligne in lignes) == list(range(1, PLAGE_MAX + 1))
    assert len(depot["issues"]["1"]["comments"]) == 4
    assert sum(c.startswith("<!-- maestro:meta v1") for c in depot["issues"]["1"]["comments"]) == 1


def test_une_coupure_au_milieu_des_commentaires_ne_les_reposte_pas(importable: Migration):
    """Chaque commentaire est journalisé POUR LUI-MÊME, et pas seulement le fil une fois complet.

    Sans ça, une coupure au troisième commentaire d'un ticket qui en porte six reposterait les deux
    premiers à la reprise. Un doublon ne casse rien, mais il ne se répare qu'à la main — et il
    salit une donnée qu'on migre précisément pour la garder propre.
    """
    # Le ticket #1 reçoit : métadonnées, puis trois commentaires humains. On refuse le deuxième
    # humain, soit le troisième commentaire de l'objet — le fil est donc coupé en son milieu, ce
    # qui est le seul endroit où la question se pose.
    importable.pose_scenario(refuser_commentaire_issue=1, refuser_commentaire_rang=3)
    coupe = importable.importe("--max", "1")
    assert coupe.returncode == 5, coupe.stdout + coupe.stderr
    assert len(importable.etat_depot()["issues"]["1"]["comments"]) == 2

    importable.pose_scenario(refuser_commentaire_issue=None, refuser_commentaire_rang=None)
    reprise = importable.importe("--max", "1")
    assert reprise.returncode == 5, reprise.stdout + reprise.stderr

    commentaires = importable.etat_depot()["issues"]["1"]["comments"]
    assert len(commentaires) == 4
    assert sum("Un commentaire — é à ç." in corps for corps in commentaires) == 1
    assert sum("Réponse." in corps for corps in commentaires) == 1
    assert sum(corps.startswith("<!-- maestro:meta v1") for corps in commentaires) == 1


def test_une_reponse_perdue_ne_cree_pas_de_doublon(importable: Migration):
    """Un POST dont la réponse s'est perdue a pu ABOUTIR : on relit avant de conclure.

    C'est le seul cas où `creer_objet` accepte de continuer sans avoir vu la réponse de sa propre
    création — et il ne l'accepte qu'après avoir constaté que le dépôt porte DÉJÀ le numéro attendu.
    Rendre « 0 » sur une lecture ratée ferait croire le dépôt vierge et relancerait la séquence
    depuis le début.
    """
    importable.pose_scenario(perdre_reponse=1)

    acheve = importable.importe("--max", "2")

    assert acheve.returncode == 5, acheve.stdout + acheve.stderr    # --max, pas un échec
    depot = importable.etat_depot()
    assert depot["sequence"] == 2
    assert depot["issues"]["1"]["title"] == "Premier ticket — avec un tiret cadratin"
    lignes = [ligne for ligne in importable.journal_import()
              if ligne[0] == "issue" and ligne[1] == "1"]
    assert len(lignes) == 1
    assert lignes[0][2] == "1"


def test_un_objet_intercale_arrete_la_sequence_en_code_4(importable: Migration):
    """Sur GitHub, issues et pull requests partagent UNE SEULE séquence.

    Une PR ouverte pendant l'import consomme donc un numéro, et tout ce qui suivrait serait décalé.
    L'arrêt est immédiat et demande un arbitrage humain — jamais une relance à l'aveugle.
    """
    importable.pose_scenario(intercaler_apres_n=1, intercaler_apres_k=2)

    acheve = importable.importe()

    assert acheve.returncode == 4, acheve.stdout + acheve.stderr
    assert "SÉQUENCE" in acheve.stdout + acheve.stderr
    assert "ne PAS relancer à l'aveugle" in acheve.stdout
    # Rien après la rupture : le ticket #3 n'a pas été créé sur le numéro d'un autre.
    assert importable.etat_depot()["issues"]["3"]["title"] == "PR d'un tiers"


def test_un_objet_deja_la_se_distingue_d_une_sequence_rompue(importable: Migration):
    """Deux causes mènent au même symptôme, et les confondre coûte cher (#183).

    L'objet est DÉJÀ LÀ (l'arrêt précédent est tombé entre le POST et sa ligne de journal — rien
    n'est décalé, une ligne suffit à reprendre), ou la séquence a réellement dérivé. Le message
    unique envoyait chercher un décalage inexistant.
    """
    importable.pose_scenario(intercaler_apres_n=1, intercaler_apres_k=1)

    acheve = importable.importe(texte=True)
    sortie = acheve.stdout + acheve.stderr

    assert acheve.returncode == 4
    assert "existe déjà côté GitHub alors que le journal l'ignore" in sortie
    assert "rien n'est décalé" in sortie
    assert "--payload 2" in sortie                 # la commande pour vérifier les octets


def test_un_numero_inattendu_arrete_avant_de_continuer(importable: Migration):
    """Le contrôle APRÈS : le numéro obtenu vaut-il l'attendu ?

    Il double le contrôle AVANT à dessein — l'un mesure le dépôt, l'autre lit la réponse de la
    création. Un import qui ne ferait confiance qu'à sa propre mesure confirmerait ses erreurs.
    """
    importable.pose_scenario(numero_menteur=1)

    acheve = importable.importe()

    assert acheve.returncode == 4, acheve.stdout + acheve.stderr
    assert "SÉQUENCE ROMPUE" in acheve.stdout + acheve.stderr
    assert importable.etat_depot()["sequence"] == 1     # rien n'a suivi


def test_une_liste_en_retard_ne_declenche_pas_une_fausse_rupture(importable: Migration):
    """La liste des issues est RÉPLIQUÉE, donc en retard sur la création ; l'invariant, lui,
    compare au rang près.

    Mesuré pendant #340 : la liste rendait #252 alors que #253 existait (GET direct : 200). On
    avance donc tant que le suivant RÉPOND, ce qui ne dépend d'aucun index répliqué — le coût de
    l'erreur n'étant pas symétrique, un appel de plus contre un arbitrage humain au milieu d'une
    action à sens unique.
    """
    importable.pose_scenario(liste_en_retard=True)

    acheve = importable.importe("--max", "3")

    assert acheve.returncode == 5, acheve.stdout + acheve.stderr    # --max, pas une rupture
    assert "SÉQUENCE" not in acheve.stdout + acheve.stderr
    assert importable.etat_depot()["sequence"] == 3


# ==================================================================================================
# DOCUMENTATION — plus aucun geste `glab` prescrit
# ==================================================================================================
#: Les quatre fichiers que le critère #345 vise : les deux qui pilotent l'agent, celui qui accueille
#: un humain, celui qui présente le projet. `docs/27` n'y est PAS — c'est lui qui porte la recette
#: de relecture de l'archive, et elle doit vivre à un endroit (§11).
DOCS_SANS_GLAB = ("CLAUDE.md", "CONTRIBUTING.md", "README.md", "docs/10-workflow-git.md")

#: Un GESTE, pas une mention : `glab` suivi d'un verbe. Le mot seul reste permis — dire « `glab`
#: n'est plus un prérequis » est une information, pas une consigne — et c'est exactement la
#: distinction que la migration a créée : le client GitLab existe encore, pour l'archive et pour
#: elle seule.
MOTIF_GESTE_GLAB = re.compile(r"\bglab\s+[a-z]")


def test_le_motif_de_geste_glab_attrape_ce_qu_il_pretend() -> None:
    """Le motif est prouvé sur un échantillon fautif AVANT de balayer le dépôt.

    Un `grep` qui ne matche rien rend le même vert qu'un dépôt propre : sans cette preuve, le test
    suivant serait un ✓ sur une question jamais posée. Trois pièges à écarter, et le troisième est
    celui qui a failli passer — « Réglable » contient la suite d'octets « glab ».
    """
    fautifs = [
        "ouvrir la MR : `glab mr create --fill`",
        "vérifier l'accès : glab issue list doit lister les tickets",
        "  glab api graphql -f query=…",
    ]
    innocents = [
        "Le CLI `glab` n'est plus un prérequis du dépôt (#344).",
        "Réglable via `GL_GQL_RETRIES` (défaut 3).",          # « Réglable » contient « glab »
        "les règles de `.claude/settings.json` visaient toutes `glab`,",
    ]
    assert all(MOTIF_GESTE_GLAB.search(ligne) for ligne in fautifs)
    assert not [ligne for ligne in innocents if MOTIF_GESTE_GLAB.search(ligne)]


def test_la_documentation_ne_prescrit_plus_aucun_geste_glab() -> None:
    """#345, critère 2 : les quatre fichiers du workflow ne décrivent plus de geste `glab`.

    Le workflow quotidien passe par `gh` et par les helpers de `scripts/gitlab/lib.sh`. Ce qui
    subsiste ailleurs est délimité et voulu : `scripts/migration/` lit l'**archive** GitLab (les
    seules primitives `glab` du dépôt, regroupées dans `gitlab-lecture.sh` pour que l'exception se
    voie d'un `grep`), et `docs/27 §11` en porte la recette.
    """
    fautives = [
        f"{nom}:{numero}: {ligne.strip()}"
        for nom in DOCS_SANS_GLAB
        for numero, ligne in enumerate((RACINE / nom).read_text(encoding="utf-8").splitlines(), 1)
        if MOTIF_GESTE_GLAB.search(ligne)
    ]
    assert not fautives, "geste `glab` réapparu dans la doc du workflow :\n" + "\n".join(fautives)


def test_la_suite_datee_de_la_note_de_decision_existe() -> None:
    """`docs/27` porte la suite du 2026-08-21 : décision inverse, motifs, coût constaté.

    La note est datée et son verdict a été renversé ; sans cette suite, elle se lit comme un avis
    encore en attente d'exécution. Le contrôle porte sur les trois choses que le ticket demandait
    d'y écrire, pas sur la prose autour.
    """
    note = (RACINE / "docs" / "27-decision-gitlab-vers-github.md").read_text(encoding="utf-8")

    assert "## 12." in note
    for attendu in (
        "La décision inverse, et ce qui l'a motivée",
        "Ce qui a été importé",
        "Ce que le chantier a coûté en code",
        "Ce qu'elle a cassé",
    ):
        assert attendu in note, attendu
    # Et la suite est ATTEIGNABLE depuis l'en-tête : une section que le bandeau de tête ne nomme
    # pas se lit après §7, c'est-à-dire après la conclusion qu'elle périme.
    assert "**§12" in note
