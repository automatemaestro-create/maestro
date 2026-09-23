"""Ce que l'orchestrateur a le droit de **lire** pour répondre — et rien d'autre (#1223).

Le fil répondait sur un contexte figé : des compteurs, au plus trois runs, le
détail de chaque tâche coupé à 300 caractères. Le 2026-09-22, à « comment je fais
pour tester l'animation ? » — après un run qui venait d'écrire sa doc de
lancement —, il a répondu *« le détail que j'ai ici est tronqué […] je n'ai pas la
commande exacte sous les yeux […] un README y a probablement été créé »*. La
réponse était honnête et inutile : le fichier était à deux pas, personne n'était
allé le voir.

Ce module est ce « aller voir ». Il porte **quatre lectures**, et elles sont
toutes en lecture seule :

| verbe | ce qu'il rend |
| --- | --- |
| `lister` | les entrées d'un dossier du projet |
| `chercher` | les lignes du projet qui contiennent un texte |
| `lire` | le contenu d'un fichier du projet |
| `detail` | le détail **complet** d'un run et de ses tâches, sans troncature |

## Le protocole est du texte, et c'est une décision

Les trois premières touchent le disque, la quatrième la projection ; aucune
n'écrit. Le modèle les demande **en écrivant une ligne** —
`%%LIRE%% {"outil": "lire", "chemin": "README.md"}` — que le canal exécute avant
de lui laisser rédiger sa réponse.

Un appel d'outil natif aurait été la forme la plus propre, et elle a été écartée
pour la raison que le lot 1 (#1222) avait déjà écrite en écartant la même chose :
`generate`/`generate_stream` sont **texte seul** (`tools=[]`), l'exécution
outillée passe par `run_agent`, et `run_agent` est **refusée** par le fournisseur
compatible OpenAI (`UnsupportedCapability`). Un protocole d'outils natifs aurait
donc rendu ce lot inatteignable sur un Ollama local, c'est-à-dire sur le seul
endpoint que quelqu'un fait tourner chez lui — et le risque relevé au ticket le
2026-09-23 (« sur ce fournisseur, les lectures de ce lot n'existent pas ») serait
devenu le comportement nominal. Le marqueur, lui, traverse **tous** les
fournisseurs, parce qu'il ne demande rien de plus que du texte.

C'est le pendant exact du `%%MAESTRO%%` de #1222, et pour le même motif : ce
canal parle à des fournisseurs dont le plus petit dénominateur est une chaîne de
caractères, et un contrat écrit dans cette chaîne est le seul qui vaille partout.

## Ce que ce module ne fait jamais

- **il n'écrit rien** — aucun `open` en écriture, aucun `mkdir`, aucun
  `subprocess`. Écrire dans le projet passe par un run, et ce n'est pas ici ;
- **il ne sort pas de la racine du projet** — tout chemin est résolu
  (`Path.resolve`, donc liens symboliques compris) puis vérifié contre la racine
  résolue. Un `../` , un chemin absolu, un lien vers `~/.ssh` : refusés, et le
  refus se **dit** au lieu de rendre un vide qui se lirait comme « rien trouvé » ;
- **il ne lit pas les secrets** — les motifs `exclus` du projet s'appliquent
  (`maestro.projets.perimetre.exclu_par`), et ils retirent d'office `.env` et
  `**/secrets/**` (`EXCLUS_DEFAUT`, docs/24 §2.5). Ce n'est pas une précaution
  prise ici : c'est la propriété du périmètre, héritée, comme pour l'analyse d'un
  projet (#1158) ;
- **il ne rend jamais tout** — chaque lecture est bornée (entrées, octets,
  résultats) et la borne atteinte est **écrite dans ce qui est rendu**. C'est la
  règle de `faits_des_runs` (#1157) : un lecteur qui ne sait pas qu'il lui manque
  quelque chose conclut sur ce qu'il croit connaître en entier.

Une lecture qui échoue ne lève pas : elle rend une `Lecture` qui dit pourquoi.
Le canal doit pouvoir raconter « je n'ai pas pu ouvrir ce fichier » plutôt que de
tomber — c'est l'invariant du fil depuis #686, appliqué un cran plus bas.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from maestro.projets.modele import Projet
from maestro.projets.perimetre import exclu_par

#: Le marqueur d'une demande de lecture. Même famille que le `%%MAESTRO%%` de
#: #1222 — une suite ASCII qu'aucune phrase française ne produit — et **différent
#: de lui** : les deux marqueurs vivent dans deux appels distincts, et deux
#: contrats qui partageraient leur préfixe se confondraient au premier flux coupé
#: au milieu.
MARQUEUR_LECTURE = "%%LIRE%%"

#: Les quatre verbes. Trois touchent le disque du projet, le quatrième la
#: projection ; aucun n'écrit.
OUTIL_LISTER = "lister"
OUTIL_CHERCHER = "chercher"
OUTIL_LIRE = "lire"
OUTIL_DETAIL = "detail"

OUTILS: tuple[str, ...] = (OUTIL_LISTER, OUTIL_CHERCHER, OUTIL_LIRE, OUTIL_DETAIL)

#: Combien de lectures une demande peut porter en un tour. Au-delà, le surplus
#: est **écarté en le disant** : cinq fichiers suffisent à répondre, et un modèle
#: qui en demande trente a compris le protocole comme une invitation à tout lire.
LECTURES_PAR_TOUR = 5

#: Combien d'entrées un `lister` rend. Un dossier de projet réel en porte une
#: poignée à sa racine ; la borne existe pour `node_modules` — que le périmètre
#: retire déjà — et pour les dossiers de build qu'il ne retire pas.
_ENTREES_MAX = 60

#: Combien d'octets un `lire` rend. Un README, un `package.json`, un fichier de
#: configuration tiennent dessous ; un fichier de code long est **coupé en le
#: disant**, ce qui est la seule façon honnête de ne pas payer un prompt entier
#: pour une question qui tenait dans les vingt premières lignes.
_OCTETS_MAX = 8_000

#: Combien de lignes un `chercher` rend, et sur combien de fichiers il passe.
_RESULTATS_MAX = 30
_FICHIERS_BALAYES_MAX = 2_000

#: La longueur d'un `detail`. Bien au-delà des 300 caractères de `faits_des_runs`
#: — c'est tout l'objet du verbe — mais pas sans borne : une trace Python entière
#: ferait un prompt que personne ne paie deux fois.
_DETAIL_MAX = 6_000

#: Les extensions qu'un `chercher` ouvre. Ce n'est pas une liste de langages :
#: c'est la frontière entre « du texte » et « un binaire », et elle se trompe
#: dans le sens sûr — un fichier qu'elle ne reconnaît pas n'est pas balayé, ce
#: qui coûte une recherche muette et jamais un octet illisible dans un prompt.
#: Un fichier **sans extension** (`Makefile`, `Dockerfile`, `LICENSE`) est ouvert :
#: c'est la forme la plus courante du fichier de projet qui porte une commande.
_EXTENSIONS_TEXTE = frozenset(
    {
        ".bash", ".c", ".cfg", ".cjs", ".conf", ".cpp", ".cs", ".css", ".csv",
        ".env-example", ".example", ".go", ".gradle", ".h", ".htm", ".html",
        ".ini", ".java", ".js", ".json", ".jsx", ".kt", ".less", ".lock", ".lua",
        ".md", ".mjs", ".mts", ".php", ".pl", ".properties", ".ps1", ".py",
        ".rb", ".rs", ".rst", ".scss", ".sh", ".sql", ".svelte", ".swift",
        ".toml", ".ts", ".tsx", ".txt", ".vue", ".xml", ".yaml", ".yml", ".zsh",
    }
)


@dataclass(frozen=True)
class Demande:
    """Une lecture demandée par le modèle : le verbe, et ses arguments.

    `arguments` est rendu tel que le JSON le portait, en chaînes : ce module ne
    connaît que des chemins, un motif et un identifiant de run. Un argument
    inconnu est **ignoré** plutôt que refusé — le contrat est celui du canal, pas
    une API publique, et refuser une clé de trop ferait échouer une lecture
    parfaitement exécutable.
    """

    outil: str
    arguments: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Lecture:
    """Ce qu'une lecture a produit : ce qu'on en **dit**, et ce qu'elle a **rendu**.

    `libelle` est la ligne du fil — « A lu `README.md` » —, écrite du point de vue
    de qui regarde l'orchestrateur travailler. `contenu` est ce qui entre dans le
    prompt : le texte lu, borné, ou la phrase qui dit pourquoi il n'y en a pas.

    Les deux sont toujours renseignés, y compris sur un refus : une étape qui
    n'afficherait rien laisserait croire que la lecture n'a pas eu lieu, et un
    contenu vide se lirait comme « le fichier est vide ».
    """

    libelle: str
    contenu: str


def catalogue() -> str:
    """Le bloc de prompt qui **déclare** les quatre verbes et la forme d'une demande.

    Écrit ici, avec les verbes qu'il décrit : un catalogue recopié dans le prompt
    de l'orchestration divergerait au premier verbe ajouté, et le symptôme serait
    un modèle qui demande une lecture que personne n'exécute.
    """
    return (
        "Tu peux LIRE, avant de répondre, et seulement lire :\n"
        f"- `{OUTIL_LISTER}` — les entrées d'un dossier du projet. "
        '{"outil": "lister", "chemin": "."}\n'
        f"- `{OUTIL_CHERCHER}` — les lignes du projet qui contiennent un texte. "
        '{"outil": "chercher", "motif": "npm run", "chemin": "."}\n'
        f"- `{OUTIL_LIRE}` — le contenu d'un fichier du projet. "
        '{"outil": "lire", "chemin": "README.md"}\n'
        f"- `{OUTIL_DETAIL}` — le détail COMPLET d'un run et de ses tâches, sans "
        'troncature. {"outil": "detail", "run_id": "…"}\n'
        "\n"
        f"Une demande = une ligne qui commence par {MARQUEUR_LECTURE}, suivie d'un "
        "objet JSON, et rien d'autre sur la ligne :\n"
        f'{MARQUEUR_LECTURE} {{"outil": "lire", "chemin": "README.md"}}\n'
        "\n"
        f"Au plus {LECTURES_PAR_TOUR} par tour. Les chemins sont RELATIFS à la "
        "racine du projet ; tout ce qui en sort est refusé, et les secrets "
        "(`.env`, `secrets/`) ne sont jamais lisibles."
    )


def demandes_de(texte: str) -> tuple[Demande, ...]:
    """Les demandes de lecture écrites dans `texte` — dans leur ordre d'écriture.

    Une ligne dont le premier caractère non blanc ouvre le marqueur porte une
    demande ; tout le reste est ignoré, y compris la prose dont un modèle bavard
    entoure ses lignes. Le marqueur est cherché **en tête de ligne** et non
    n'importe où : une réponse qui cite le protocole (« écris
    `%%LIRE%% {…}` ») ne doit pas déclencher une lecture.

    Une ligne mal formée — JSON illisible, verbe inconnu, objet qui n'en est pas
    un — est **sautée en silence** : elle n'a rien demandé d'exécutable, et la
    signaler au modèle coûterait un tour pour une faute de frappe. Ce qu'il ne
    reçoit pas, il le redemandera ou s'en passera.
    """
    trouvees: list[Demande] = []
    for ligne in texte.splitlines():
        nue = ligne.strip()
        if not nue.startswith(MARQUEUR_LECTURE):
            continue
        charge = nue[len(MARQUEUR_LECTURE) :].strip().strip("`")
        try:
            objet = json.loads(charge)
        except json.JSONDecodeError:
            continue
        if not isinstance(objet, Mapping):
            continue
        outil = str(objet.get("outil") or "").strip().lower()
        if outil not in OUTILS:
            continue
        trouvees.append(
            Demande(
                outil=outil,
                arguments={
                    str(cle): str(valeur)
                    for cle, valeur in objet.items()
                    if cle != "outil" and valeur is not None
                },
            )
        )
    return tuple(trouvees)


#: Résout le projet de la fenêtre — `None` quand il n'y en a pas, qu'il est
#: inconnu ou illisible. Le câblage la branche sur `ServiceProjets.entite`, seul
#: lecteur de projets de la Control Tower : ce module ne relit aucun dépôt.
ProjetDeLaFenetre = Callable[[str], Projet | None]

#: Rend le détail complet d'un run — ses tâches, leurs détails **non tronqués**.
#: `""` quand le run est inconnu. Branché sur la projection, comme
#: `faits_des_runs` : ce module ne connaît pas `ControlTowerState`.
DetailDuRun = Callable[[str], str]


class Consultations:
    """L'exécutant des quatre verbes — bornes et frontière comprises.

    Deux dépendances, et aucune des deux n'est un magasin : le projet de la
    fenêtre (`ProjetDeLaFenetre`) et le détail d'un run (`DetailDuRun`). C'est le
    même partage que `apercu_de`/`faits_des_runs` dans `orchestration` — ce
    module sait *lire*, il ne sait pas *où* les choses sont rangées —, et c'est
    ce qui le rend jouable sur un dossier temporaire sans monter ni projection ni
    dépôt de projets.

    Sans projet — aucun `projet_id`, projet inconnu, dossier disparu —, les trois
    verbes de disque rendent un refus **nommé**. Le quatrième, lui, continue de
    répondre : le détail d'un run ne dépend d'aucune racine.
    """

    def __init__(
        self, *, projet: ProjetDeLaFenetre | None = None, detail: DetailDuRun | None = None
    ) -> None:
        self._projet = projet
        self._detail = detail

    def executer(self, demande: Demande, projet_id: str | None) -> Lecture:
        """Exécute `demande` dans le projet `projet_id`, et rend ce qu'elle a produit.

        **Ne lève jamais** : tout empêchement — projet absent, chemin hors racine,
        fichier illisible, verbe sans exécutant — devient une `Lecture` qui le
        dit. Le fil doit pouvoir raconter ce qu'il n'a pas pu lire ; une exception
        ferait de l'empêchement un 502 sur la seule porte d'entrée du produit.
        """
        if demande.outil == OUTIL_DETAIL:
            return self._executer_detail(demande)
        racine = self._racine(projet_id)
        if racine is None:
            return Lecture(
                libelle="N'a pas pu regarder le projet",
                contenu=(
                    "Aucun projet ouvert dans cette fenêtre, ou son dossier est "
                    "introuvable : aucun fichier n'est lisible d'ici."
                ),
            )
        projet, base = racine
        if demande.outil == OUTIL_LISTER:
            return self._executer_lister(projet, base, demande)
        if demande.outil == OUTIL_CHERCHER:
            return self._executer_chercher(projet, base, demande)
        return self._executer_lire(projet, base, demande)

    # --- les quatre verbes -------------------------------------------------

    def _executer_lister(self, projet: Projet, base: Path, demande: Demande) -> Lecture:
        """Les entrées d'un dossier, dossiers d'abord, bornées."""
        brut = demande.arguments.get("chemin", "")
        relatif = _relatif(brut)
        # « A listé « . » » ne dit rien à qui regarde : le point est le chemin que
        # le modèle a écrit, pas ce qu'il a consulté (relevé par le regard neuf de
        # #1223). La racine se nomme, les sous-dossiers gardent leur chemin.
        libelle = f"A listé « {relatif} »" if relatif else "A listé la racine du projet"
        cible = _sous(base, brut, projet)
        if cible is None:
            return Lecture(libelle=libelle, contenu=_HORS_RACINE.format(chemin=relatif))
        if not cible.is_dir():
            return Lecture(
                libelle=libelle,
                contenu=f"« {relatif or '.'} » n'est pas un dossier du projet.",
            )
        entrees: list[str] = []
        try:
            with os.scandir(cible) as brutes:
                triees = sorted(brutes, key=lambda entree: (not entree.is_dir(), entree.name))
        except OSError as echec:
            return Lecture(libelle=libelle, contenu=f"Dossier illisible : {echec}.")
        for entree in triees:
            chemin = f"{relatif}/{entree.name}" if relatif else entree.name
            if entree.is_symlink() or _refuse(chemin, projet):
                continue
            entrees.append(f"{entree.name}/" if entree.is_dir() else entree.name)
        montrees = entrees[:_ENTREES_MAX]
        corps = "\n".join(montrees) if montrees else "(dossier vide)"
        reste = len(entrees) - len(montrees)
        if reste > 0:
            corps += f"\n… et {reste} autre(s) entrée(s), non montrées ici."
        return Lecture(libelle=libelle, contenu=corps)

    def _executer_lire(self, projet: Projet, base: Path, demande: Demande) -> Lecture:
        """Le contenu d'un fichier, borné à `_OCTETS_MAX` et coupé **en le disant**."""
        brut = demande.arguments.get("chemin", "")
        relatif = _relatif(brut)
        libelle = f"A lu « {relatif} »" if relatif else "A lu un fichier"
        cible = _sous(base, brut, projet)
        if cible is None or not relatif:
            return Lecture(libelle=libelle, contenu=_HORS_RACINE.format(chemin=relatif))
        if not cible.is_file():
            return Lecture(
                libelle=libelle, contenu=f"« {relatif} » n'est pas un fichier du projet."
            )
        try:
            octets = cible.read_bytes()
        except OSError as echec:
            return Lecture(libelle=libelle, contenu=f"Fichier illisible : {echec}.")
        texte = octets[:_OCTETS_MAX].decode("utf-8", errors="replace")
        if len(octets) > _OCTETS_MAX:
            texte += f"\n… (fichier coupé à {_OCTETS_MAX} octets sur {len(octets)})"
        return Lecture(libelle=libelle, contenu=texte)

    def _executer_chercher(self, projet: Projet, base: Path, demande: Demande) -> Lecture:
        """Les lignes du projet qui contiennent `motif` — chemin, numéro, ligne.

        La recherche est **littérale et insensible à la casse**, jamais une
        expression régulière : c'est ce qu'un modèle écrit quand il cherche une
        commande, et une regex mal formée ferait échouer une lecture au lieu de
        n'en rien rendre.
        """
        motif = demande.arguments.get("motif", "").strip()
        brut = demande.arguments.get("chemin", "")
        relatif = _relatif(brut)
        libelle = f"A cherché « {motif} »" if motif else "A cherché dans le projet"
        if not motif:
            return Lecture(libelle=libelle, contenu="Aucun motif donné : rien à chercher.")
        depart = _sous(base, brut, projet)
        if depart is None:
            return Lecture(libelle=libelle, contenu=_HORS_RACINE.format(chemin=relatif))
        besoin = re.compile(re.escape(motif), re.IGNORECASE)
        trouvees: list[str] = []
        balayes = 0
        for chemin in _fichiers_texte(depart, base, projet):
            balayes += 1
            if balayes > _FICHIERS_BALAYES_MAX:
                trouvees.append(
                    f"… balayage arrêté après {_FICHIERS_BALAYES_MAX} fichiers."
                )
                break
            try:
                contenu = chemin.read_bytes()[:_OCTETS_MAX].decode("utf-8", errors="replace")
            except OSError:
                continue
            nom = chemin.relative_to(base).as_posix()
            for numero, ligne in enumerate(contenu.splitlines(), start=1):
                if besoin.search(ligne):
                    trouvees.append(f"{nom}:{numero}: {ligne.strip()[:200]}")
                    if len(trouvees) >= _RESULTATS_MAX:
                        break
            if len(trouvees) >= _RESULTATS_MAX:
                trouvees.append(
                    f"… {_RESULTATS_MAX} résultats atteints, la suite n'est pas montrée."
                )
                break
        corps = "\n".join(trouvees) if trouvees else f"Aucune ligne ne contient « {motif} »."
        return Lecture(libelle=libelle, contenu=corps)

    def _executer_detail(self, demande: Demande) -> Lecture:
        """Le détail complet d'un run — ce que les 300 caractères de #1157 coupaient."""
        run_id = demande.arguments.get("run_id", "").strip()
        libelle = f"A relu le run « {run_id} »" if run_id else "A relu un run"
        if self._detail is None:
            return Lecture(
                libelle=libelle,
                contenu="Aucune projection branchée sur ce fil : le détail est indisponible.",
            )
        if not run_id:
            return Lecture(libelle=libelle, contenu="Aucun run nommé : rien à relire.")
        try:
            texte = self._detail(run_id)
        except Exception as echec:  # noqa: BLE001 — une lecture ne lève pas, cf. `executer`
            return Lecture(libelle=libelle, contenu=f"Détail illisible : {echec}.")
        if not texte.strip():
            return Lecture(
                libelle=libelle, contenu=f"Aucun run « {run_id} » dans la projection."
            )
        if len(texte) > _DETAIL_MAX:
            texte = f"{texte[:_DETAIL_MAX].rstrip()}\n… (détail coupé)"
        return Lecture(libelle=libelle, contenu=texte)

    # --- la frontière ------------------------------------------------------

    def _racine(self, projet_id: str | None) -> tuple[Projet, Path] | None:
        """Le projet de la fenêtre et sa racine **résolue** — `None` s'il n'y en a pas."""
        if not projet_id or self._projet is None:
            return None
        try:
            projet = self._projet(projet_id)
        except Exception:  # noqa: BLE001 — un projet illisible est un projet absent, ici
            return None
        if projet is None:
            return None
        try:
            base = projet.racine_chemin.resolve(strict=True)
        except OSError:
            return None
        return (projet, base) if base.is_dir() else None


#: Ce qu'on dit d'un chemin qu'on refuse. **Une seule phrase pour les trois
#: refus** — hors racine, secret, lien symbolique — et c'est voulu : distinguer
#: « ce fichier existe mais il est exclu » de « ce fichier n'existe pas »
#: apprendrait au lecteur ce que la frontière est là pour taire.
_HORS_RACINE = (
    "« {chemin} » n'est pas lisible : les lectures sont bornées au dossier du "
    "projet, secrets exclus."
)


#: Ce qui fait d'un chemin un chemin **absolu**, sur les trois OS et quel que
#: soit celui où tourne la Control Tower : une barre de tête (`/etc/passwd`), une
#: lettre de lecteur (`C:/Windows`), un chemin UNC (`//serveur/partage`).
#:
#: ⚠ La question ne se pose **pas** à `Path` : sous Linux, `Path("C:/Windows")`
#: est un chemin *relatif*, et sous Windows `Path("/etc/passwd")` l'est presque.
#: Un `/tmp/secret` demandé à une Control Tower Windows serait alors relu comme
#: `tmp/secret` **sous la racine du projet** — refusé parce qu'il n'existe pas,
#: donc refusé par accident, avec une phrase qui parle d'autre chose. Le verdict
#: se prend sur le texte de la demande, avant toute résolution.
_ABSOLU = re.compile(r"^(/|[A-Za-z]:[/\\])")


def _relatif(brut: str) -> str:
    """Le chemin demandé, ramené à une forme relative POSIX — pour l'affichage.

    Il ne décide de rien : c'est `_sous` qui refuse. Ici on nettoie ce qui se
    **montre** dans le libellé de l'étape.
    """
    nettoye = brut.strip().replace("\\", "/").strip()
    while nettoye.startswith("./"):
        nettoye = nettoye[2:]
    nettoye = nettoye.strip("/")
    # « . » **est** la racine : le rendre tel quel ferait dire « A listé « . » » à
    # une étape du fil, c'est-à-dire le chemin que le modèle a écrit plutôt que ce
    # qu'il a consulté. La racine est la chaîne vide, partout dans ce module.
    return "" if nettoye == "." else nettoye


def _sous(base: Path, brut: str, projet: Projet) -> Path | None:
    """La cible de `brut` sous `base`, ou `None` si la frontière la refuse.

    Trois refus, une seule réponse — c'est l'unique porte par laquelle un chemin
    demandé devient un chemin ouvert :

    - **absolu** (`_ABSOLU`) : refusé sur le texte, avant toute résolution ;
    - **hors racine** : la résolution est **réelle** (`Path.resolve`), donc elle
      suit les liens symboliques — un lien vers `~/.ssh` résout dehors et tombe
      ici. C'est le vecteur d'évasion de docs/24 §2.5, fermé par une comparaison
      plutôt que par une liste de cas ;
    - **exclu du périmètre** : `.env`, `secrets/`, `.git`… (`EXCLUS_DEFAUT`).
    """
    if _ABSOLU.match(brut.strip().replace("\\", "/")):
        return None
    relatif = _relatif(brut)
    if _refuse(relatif, projet):
        return None
    try:
        cible = (base / relatif).resolve()
    except OSError:
        return None
    return cible if cible == base or cible.is_relative_to(base) else None


def _refuse(relatif: str, projet: Projet) -> bool:
    """Le périmètre du projet retire-t-il ce chemin ? (`.env`, `secrets/`, `.git`…)"""
    return bool(relatif) and exclu_par(relatif, projet.perimetre)


def _fichiers_texte(depart: Path, base: Path, projet: Projet) -> Sequence[Path]:
    """Les fichiers texte sous `depart`, périmètre appliqué, ordre déterministe.

    Parcours itératif et trié, qui **ne descend jamais** dans un chemin exclu —
    la règle de `maestro.projets.perimetre`, tenue ici parce que descendre dans un
    `node_modules` pour n'en rien rendre coûterait la recherche entière.
    """
    fichiers: list[Path] = []
    pile = [depart]
    while pile:
        courant = pile.pop()
        try:
            with os.scandir(courant) as entrees:
                triees = sorted(entrees, key=lambda entree: entree.name)
        except OSError:
            continue
        for entree in triees:
            if entree.is_symlink():
                continue
            chemin = Path(entree.path)
            relatif = chemin.relative_to(base).as_posix()
            if _refuse(relatif, projet):
                continue
            if entree.is_dir():
                pile.append(chemin)
            elif chemin.suffix.lower() in _EXTENSIONS_TEXTE or not chemin.suffix:
                fichiers.append(chemin)
    return sorted(fichiers)
