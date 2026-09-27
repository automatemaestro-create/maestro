"""Le régime **en place** : un projet non versionné se remplit dans sa racine (ticket #839).

Renverse, pour le seul projet **non versionné**, l'option C de la décision D2
([docs/24 §2.4](../../docs/24-projets-locaux-et-poste-de-travail.md)) — « copie du
périmètre + diff soumis à validation ». Décision de l'utilisateur du 2026-08-30,
sur trois faits mesurés (run `cc2d8e447f83`, commentaire du 2026-08-30 sur #703) :

1. l'option C **ne livrait rien, et ne l'a jamais fait** : la copie était refermée
   par le `finally` de `maestro.agents.runtime` avant qu'un diff puisse être
   approuvé — 46 min de run, 8,80 $, **zéro fichier** dans la racine, et pas une
   ligne au journal pour le dire. Le comportement réel n'était pas « copie + diff »
   mais **travail jeté** ;
2. son filet — pouvoir annuler — est le plus faible là où il s'applique : un projet
   non versionné est le plus souvent neuf et vide (p1 l'était), il n'y a rien à
   détruire, donc rien à annuler ;
3. l'autre motif de D2, les **collisions** entre agents simultanés dans un même
   arbre, reste vrai et se traite au lieu d'être ignoré : `maestro.engine.executor`
   **sérialise** les tâches d'un même projet non versionné — une seule à la fois
   dans la racine (`_atelier_projet`, sur le verrou par projet de #705).

Un projet **versionné** ne change pas d'un caractère (`maestro.sandbox.projet` :
worktree + branche `maestro/<tâche>` + fusion, #705), et une tâche sans projet
garde son `mkdtemp()`.

Ce module porte les deux pièces du régime :

- `EspaceEnPlace` — l'espace de travail d'une tâche, qui **est** la racine : rien
  n'est copié, rien n'est retiré à la fermeture, et ce que l'agent écrit est
  visible pendant qu'il l'écrit. Son recensement (empreintes de départ, fichiers
  produits) parcourt la racine **par le périmètre** (`fichiers_du_perimetre`) :
  les chemins exclus ne sont ni relevés ni rendus — un `npm install` de l'agent
  ne fait pas entrer 40 000 fichiers au rapport de run —, et **aucun lien
  symbolique n'est suivi**, la même règle que la copie appliquée à la lecture
  d'un arbre qui n'est plus le nôtre ;
- `FrontiereEcriture` — ce que la copie garantissait par **absence**, la racine
  doit le garantir par **refus** : les outils de fichiers de l'agent sont
  confrontés à la frontière **avant chaque appel** (hook `PreToolUse` de
  `maestro.providers.claude`, celui de la politique de #110). Une écriture qui
  sort de la racine, qui passe par un lien symbolique ou que le périmètre exclut
  est **refusée avec son motif** ; l'agent le lit et poursuit sa tâche, comme
  pour un refus de politique. Une **lecture** n'est refusée que sur ce que le
  périmètre exclut — ce que la copie ne contenait pas, l'agent ne le lit pas non
  plus —, lire hors de la racine n'étant pas le sujet de ce périmètre. Une
  **recherche** est une lecture de tout l'arbre qu'elle parcourt (#1304) : un
  `Grep` qui traverserait un chemin exclu est refusé, avec les dossiers où
  chercher à la place.

La frontière est **armée par la position, jamais par une option** : `frontiere_de`
la rend si et seulement si l'espace de travail **est** la racine du projet — le
régime en place, et lui seul. Un worktree ou un `mkdtemp()` n'en reçoivent aucune,
et c'est ce qui laisse les deux autres régimes au bit près.

Ce qu'elle **ne couvre pas** est nommé plutôt que tu : `Bash` n'est pas analysé —
un shell peut écrire n'importe où, c'était déjà vrai de la copie et du worktree
(docs/24 §2.5, « `Bash` mal formé »), et c'est ce que le **mode isolé** ferme,
`maestro.sandbox.container` montant la racine avec ses masques.

⚠ `Glob`/`Grep` **ne sont plus** de ce côté-là (#1304). Ils n'étaient pas
confrontés, au motif qu'ils ne modifient rien et que la rédaction (#109,
`maestro.projets.secrets`) couvrirait ce qu'ils pourraient citer — or `Grep` rend
le **contenu** des fichiers, et un `.env` que `Read` refusait se lisait par une
recherche. Un contenu exclu ne sort désormais par aucun outil de lecture ou de
recherche ; seuls des **noms** sortent encore d'un `Glob` qui traverse la racine.

Ce trou-là a cessé d'être théorique avec #1149, qui fait de « vide le dossier du
projet » **une tâche qui agit** : le geste se fait au shell, là où la frontière ne
juge rien. Faute de pouvoir l'y armer, `consigne_espace` **nomme les exclusions à
l'agent** — la même réponse qu'à l'atelier ci-dessous, une adresse donnée plutôt
qu'un refus de plus.

⚠ Depuis #1198, cette adresse est aussi **la seule chose** qui borne le geste sur
une tâche qui porte un acte accordé (`Task.acte_accorde`) : l'appel d'exécution ne
réveille plus personne, puisque l'objectif le nommait et qu'une personne l'a
approuvé. Ce qui reste est donc entier ici — le périmètre écrit à l'agent, et les
outils de fichiers confrontés à la frontière — et c'est ce qui rend le **mode
isolé** (`maestro.sandbox.container`) plus pressant, pas moins.

`Perimetre.inclus` ne restreint rien ici : l'inclusion disait ce qu'on **montrait**
à l'agent, or l'agent est dans la racine. Seules les exclusions tiennent — et
elles tiennent à l'écriture. Le worktree ne l'appliquait pas davantage (une copie
conforme de la branche) : depuis ce lot, `inclus` ne restreint aucun espace dérivé.

L'atelier — ce que l'agent laisse **à côté** du livrable (#944)
----------------------------------------------------------------

La racine étant l'espace de travail, tout ce qu'un agent écrit pour travailler
atterrit dans le projet de l'utilisateur, au même rang que ce qu'il livre.
Mesuré sur le run du
[retex du 2026-09-11](../../docs/retex/2026-09-11-premiere-session-utilisateur.md)
(constat **G12**) : à côté des quatre fichiers du livrable, la racine portait
`_verif/`, `_verif_timer/` et `qa/` — les répertoires de travail des agents,
laissés là parce que rien ne leur disait où les mettre.

Le remède **n'est pas un ménage de fin de run** : effacer après coup, c'est
parier sur le fait qu'aucun de ces fichiers n'était voulu, et le harnais de
vérification de la QA — que le retex a rejoué lui-même — prouve que le pari
serait perdu. La question est *où* un agent écrit ce qui n'est pas le livrable,
et le dépôt l'a déjà tranchée pour lui-même (docs/10 §11.7) : **ce qu'on invite
à relire va sous `.maestro/`, ce que personne ne lit va dans le temporaire**.
Transposée au projet d'un utilisateur, elle donne un **atelier** —
`.maestro/<tâche>/` dans la racine —, et deux propriétés qui vont ensemble :

- l'agent le **connaît** : `consigne_espace` le nomme dans le message de sa
  tâche (`maestro.agents.runtime`), et le cadre d'exécution de son playbook
  (`playbooks_defaut/_cadre_outille.md`) dit la règle. Ce n'est pas un pari sur
  sa docilité mais le retrait d'une **fausse prémisse** : le cadre lui promettait
  un « répertoire de travail isolé » là où son répertoire courant est le projet
  de quelqu'un ;
- le recensement l'**ignore** : `fichiers` ne descend jamais dans `.maestro/`,
  donc un brouillon n'entre ni à l'empreinte de départ ni aux fichiers produits.
  Un atelier qui ressortirait en livrable ne ferait que déplacer le défaut du
  disque vers le rapport de run.

La frontière, elle, ne bouge pas : l'atelier est **dans** la racine et hors des
exclusions, donc il s'écrit sans rien lever. Refuser d'écrire ailleurs qu'en
atelier serait impossible — aucune règle de chemin ne distingue un livrable d'un
brouillon — et c'est bien pourquoi la réponse est une **adresse donnée**, pas un
refus de plus.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maestro.portee import PorteeProjet
from maestro.projets.modele import Perimetre, Projet
from maestro.projets.perimetre import motifs_compiles
from maestro.projets.racine import RacineRefusee, canonique, chemin_dans_racine
from maestro.sandbox.workspace import Workspace

#: Les quatre gestes qu'un outil fait sur un chemin — ce que la frontière y juge.
#:
#: - **écrire** : la frontière entière — racine, liens, exclusions ;
#: - **lire** un fichier : les liens et les exclusions — lire hors de la racine
#:   n'est pas le sujet du périmètre ;
#: - **chercher** dans un arbre (`Grep`) : c'est une lecture, et de **tout** ce
#:   que l'arbre porte — la recherche rend le contenu des fichiers, et même en
#:   ne rendant que des noms elle dit si un motif y est. L'arbre ne doit donc
#:   contenir aucun chemin exclu (#1304) ;
#: - **lister** (`Glob`) : ne rend que des **noms**, jamais un contenu. Ce qu'il
#:   vise en toutes lettres est confronté comme une lecture ; ce qu'un joker
#:   traverse ne l'est pas — ce qui en sort n'est pas ce que le périmètre protège.
ECRITURE = "écriture"
LECTURE = "lecture"
RECHERCHE = "recherche"
LISTE = "liste"


@dataclass(frozen=True)
class OutilAChemin:
    """Comment un outil touche un chemin : l'argument qui le porte, et son geste.

    `exige` dit si l'argument est obligatoire. Un argument **exigé** qui manque
    ou ne se lit pas fait refuser l'appel (#1304) : deviner « rien à confronter »
    était le trou par lequel un CLI qui renommerait l'argument aurait ouvert la
    frontière entière. Un argument facultatif absent vaut le répertoire courant
    de l'agent — la racine —, qui est ce que l'outil parcourt alors.
    """

    cle: str
    geste: str
    exige: bool = True


#: Les outils dont un argument est un chemin, et comment ils le touchent — ceux du
#: CLI Claude Code, que `maestro.agents.runtime.DEFAULT_TOOLS` monte en partie.
#: Un outil absent d'ici n'est pas confronté à la frontière : c'est pourquoi
#: `Grep` et `Glob`, montés par défaut, y sont depuis #1304 — `Grep` lisait un
#: `.env` que `Read` refusait.
#:
#: ⚠ La liste est écrite dans les noms du CLI, et c'est ce qui la rend fragile :
#: un outil nouveau qui lirait un fichier n'y serait pas. Le chantier « Maestro
#: possède ses contrats » (#1315, docs/44) la fera porter par le vocabulaire
#: d'outils de Maestro ; d'ici là, un nom de trop ne coûte rien et un nom de
#: moins rouvre une lecture — `NotebookRead`, que les CLI récents n'exposent
#: plus (`Read` lit les notebooks), y reste pour ceux qui l'exposent encore.
OUTILS_A_CHEMIN: Mapping[str, OutilAChemin] = {
    "Read": OutilAChemin("file_path", LECTURE),
    "Write": OutilAChemin("file_path", ECRITURE),
    "Edit": OutilAChemin("file_path", ECRITURE),
    "MultiEdit": OutilAChemin("file_path", ECRITURE),
    "NotebookRead": OutilAChemin("notebook_path", LECTURE),
    "NotebookEdit": OutilAChemin("notebook_path", ECRITURE),
    "Grep": OutilAChemin("path", RECHERCHE, exige=False),
    "Glob": OutilAChemin("path", LISTE, exige=False),
}

#: Ceux des outils ci-dessus qui **écrivent** : pour eux la frontière est entière
#: (racine, liens, exclusions) ; pour les autres seules les exclusions valent.
OUTILS_ECRITURE: frozenset[str] = frozenset(
    nom for nom, usage in OUTILS_A_CHEMIN.items() if usage.geste == ECRITURE
)

#: L'argument de `Glob` qui porte le motif — ce qu'il vise en toutes lettres.
CLE_MOTIF_LISTE = "pattern"

#: Les caractères qui font d'un segment de motif un joker, et non un nom.
JOKERS = frozenset("*?[{")

#: Combien de chemins un refus de recherche nomme, de chaque côté : ce qu'elle
#: atteindrait d'exclu, et où chercher à la place. Au-delà, le motif dit combien
#: il en tait — une adresse se lit, une liste de quarante entrées non.
NOMMES_MAX = 6

#: Le dossier qui porte les **ateliers** des tâches dans la racine d'un projet
#: (#944) — un sous-dossier par tâche. Le nom est celui que le dépôt s'est donné
#: pour la même question (docs/10 §11.7) : un point de tête, un seul dossier, et
#: on sait d'un coup d'œil que ce n'est pas le livrable. Jamais recensé.
DOSSIER_ATELIER = ".maestro"

#: Les noms que la **comptabilité de Maestro** occupe déjà sous `.maestro/`, et
#: qu'un atelier de tâche ne peut donc pas prendre (docs/38 §4.3, #1033).
#: `.maestro/outillage/` porte le manifeste de l'outillage généré dans le projet
#: (`maestro.outillage.detection.CHEMIN_MANIFESTE`) et le dossier `refuses/` des
#: versions qu'une régénération n'a pas écrasées. Rien n'empêchait un identifiant
#: de tâche de se réduire à `outillage` (`_slug` ne garde que `[A-Za-z0-9_-]`) :
#: les deux se seraient alors partagé le même dossier, et l'atelier — qui n'est
#: *jamais recensé* et que personne ne relit — aurait cohabité avec la seule
#: mémoire de ce que Maestro possède dans le projet.
#:
#: ⚠ Une constante littérale, et non le segment dérivé de `CHEMIN_MANIFESTE` :
#: importer `maestro.outillage` d'ici refermerait un cycle (son paquet importe
#: `FrontiereEcriture` pour écrire l'outillage). Le lien se lit dans les deux
#: sens par ce commentaire, et il n'y a qu'un nom à tenir.
ATELIERS_RESERVES: frozenset[str] = frozenset({"outillage"})

#: Ce qu'un atelier au nom réservé devient. Suffixé plutôt que refusé : le nom
#: d'une tâche n'est pas un geste de l'utilisateur, et faire échouer une tâche
#: parce que son identifiant s'est réduit à un mot déplacerait le problème sur
#: quelqu'un qui n'y peut rien.
SUFFIXE_ATELIER_RESERVE = "-tache"


def chemin_atelier(tache: str) -> str:
    """L'atelier de la tâche `tache`, relatif à la racine — `.maestro/<tâche>`.

    `tache` est déjà assaini par l'appelant (`maestro.sandbox.projet._slug`, le
    même fragment que la branche et le répertoire d'un projet versionné) : un
    identifiant vide n'arrive pas jusqu'ici. Une fonction plutôt qu'un f-string
    recopié, pour que « où est l'atelier » n'ait qu'une seule orthographe — c'est
    elle que le message de la tâche nomme et que le recensement saute.

    Un nom **réservé** (`ATELIERS_RESERVES`) est écarté ici, et nulle part
    ailleurs : c'est le seul endroit qui compose le chemin, donc le seul où la
    collision peut se produire.
    """
    if tache.strip().lower() in ATELIERS_RESERVES:
        return f"{DOSSIER_ATELIER}/{tache}{SUFFIXE_ATELIER_RESERVE}"
    return f"{DOSSIER_ATELIER}/{tache}"


def ouvre_atelier(racine: Path, atelier: str) -> None:
    """Crée l'atelier `atelier` sous `racine` — **best-effort**, jamais fatal.

    Créé plutôt que seulement nommé : un agent qui lance `node .maestro/t4/x.mjs`
    ou qui y `cd` a besoin du dossier, là où l'outil `Write` l'aurait créé au
    passage. Et s'il échoue (droits, disque), rien n'est perdu — l'écriture le
    créera, ou l'agent se rabattra sur le temporaire : ouvrir un atelier n'est
    pas une condition pour travailler.
    """
    try:
        (racine / atelier).mkdir(parents=True, exist_ok=True)
    except OSError:  # racine en lecture seule, disque plein : l'agent s'en passe
        pass


@dataclass(frozen=True)
class EspaceEnPlace(Workspace):
    """L'espace de travail qui **est** la racine d'un projet non versionné (#839).

    `perimetre` est celui du projet : c'est par lui que l'espace s'énumère.
    `atelier` est le dossier de travail de la tâche (#944), relatif à la racine :
    l'agent y range ce qui n'est pas le livrable, et le recensement ne le voit
    pas. Le reste du contrat de `Workspace` tient tel quel — `derive` relève
    l'empreinte de départ, `produced_files` rend ce qui a changé depuis.
    """

    perimetre: Perimetre = field(default_factory=Perimetre)
    atelier: str = ""

    def fichiers(self) -> Iterator[Path]:
        """Les fichiers de la racine **par le périmètre** : exclusions sautées, liens ignorés.

        L'**atelier** est sauté avec elles (#944), et le dossier entier plutôt
        que le seul de cette tâche : celui d'une tâche voisine n'est pas
        davantage le livrable de celle-ci, et les tâches d'un projet non
        versionné se succèdent dans la même racine.
        """
        exclus = motifs_compiles(self.perimetre.exclus)
        for relatif in fichiers_du_perimetre(self.path, exclus, hors=(DOSSIER_ATELIER,)):
            yield self.path / relatif

    def consigne_espace(self) -> str:
        """Ce que l'agent doit savoir de cet espace — la racine, son atelier (#944), son périmètre.

        Dit dans le message de la **tâche** et non dans le playbook du rôle : le
        régime dépend du projet, pas de l'agent, et un prompt système qui
        promettrait un atelier là où il n'y en a pas serait le défaut d'avant,
        retourné. Vide tant qu'aucun atelier n'est ouvert — l'appelant n'ajoute
        alors rien au message.

        Depuis #1149, la phrase dit aussi que le livrable peut être un **état** :
        une tâche d'action (vider, supprimer, renommer, déplacer, lancer une
        commande) ne dépose rien, et un agent à qui l'on promet que « ce que tu
        laisses est le livrable » sans cette nuance cherche un artefact à poser.
        """
        if not self.atelier:
            return ""
        return (
            "Ton répertoire courant est la **racine du projet de l'utilisateur** : "
            "ce que tu y laisses est le livrable qu'il recevra — et si ta tâche te "
            "demande d'**agir** (vider, supprimer, renommer, déplacer, lancer une "
            "commande), le livrable est l'état de cette racine après ton geste, sans "
            "rien de plus à y déposer. Range ce qui n'est "
            f"pas le livrable — brouillons, essais, harnais de vérification, notes — "
            f"dans `{self.atelier}/`, ton atelier ; ce que personne n'aura à relire "
            "va dans le répertoire temporaire du système. Rien ne sera déplacé ni "
            "effacé après toi." + self._consigne_perimetre()
        )

    def _consigne_perimetre(self) -> str:
        """Les chemins que le périmètre retire, **nommés** à l'agent (#1149).

        La frontière d'écriture les refuse déjà, mais seulement aux outils de
        fichiers : `Bash` n'est pas analysé, et ne peut pas l'être (cf. la
        docstring du module, docs/24 §2.5). Or le geste qui a rendu ce ticket
        nécessaire — « vide le dossier du projet » — se fait précisément au shell,
        où un `rm` sans discernement emporterait `.git` et `.env`. Les nommer est
        la seule garde qui vaille des deux côtés de cette frontière, et c'est la
        même réponse qu'à l'atelier de #944 : une **adresse donnée** plutôt qu'un
        refus de plus.

        Le texte est **dérivé du périmètre du projet**, jamais une liste recopiée :
        une exclusion que l'utilisateur ajoute est dite à l'agent sans une ligne de
        plus, et un périmètre sans exclusion n'ajoute rien au message.
        """
        if not self.perimetre.exclus:
            return ""
        motifs = ", ".join(f"`{motif}`" for motif in self.perimetre.exclus)
        return (
            f"\n\nCes chemins sont **hors du périmètre du projet** : {motifs}. Ils ne "
            "sont ni lus, ni écrits, ni renommés, ni supprimés — tes outils de fichiers "
            "les refusent, et une commande shell ne les atteint pas davantage. Vider ou "
            "nettoyer ce répertoire les laisse en place."
        )


def fichiers_du_perimetre(
    racine: Path,
    exclus: tuple[re.Pattern[str], ...],
    *,
    hors: tuple[str, ...] = (),
) -> Iterator[str]:
    """Les chemins relatifs (POSIX) des fichiers de `racine` que `exclus` ne retire pas.

    `hors` nomme des chemins relatifs que le parcours ne descend pas, quels que
    soient les motifs — l'atelier des tâches (#944). Distinct des exclusions à
    dessein : une exclusion du périmètre vaut aussi **à l'écriture**
    (`FrontiereEcriture`), et l'atelier est justement là pour être écrit.

    Parcours itératif (pas récursif) : un projet réel a des arborescences
    profondes et la pile de Python n'est pas le bon endroit pour en dépendre. Un
    dossier exclu n'est **même pas parcouru** — ce qui vaut autant pour la sûreté
    (rien sous `secrets/` n'est seulement lu) que pour le temps de relevé
    (`node_modules` n'est jamais énuméré). **Aucun lien symbolique n'est suivi**
    — ni fichier ni dossier : c'est le vecteur d'évasion de docs/24 §2.5, et
    `Path.rglob` le suivrait.

    C'est le parcours que la copie de #224 faisait avant ce lot, ramené à la
    seule question qui reste : *qu'y a-t-il dans la racine que l'agent ait le
    droit de voir ?*
    """
    pile = [""]
    while pile:
        relatif_dossier = pile.pop()
        base = racine / relatif_dossier if relatif_dossier else racine
        try:
            with os.scandir(base) as entrees:
                triees = sorted(entrees, key=lambda entree: entree.name)
        except OSError:  # dossier devenu illisible : sauté, jamais fatal
            continue
        for entree in triees:
            relatif = f"{relatif_dossier}/{entree.name}" if relatif_dossier else entree.name
            if entree.is_symlink() or relatif in hors or _correspond(relatif, exclus):
                continue
            if entree.is_dir():
                pile.append(relatif)
            elif entree.is_file():
                yield relatif


@dataclass(frozen=True)
class FrontiereEcriture:
    """La frontière que les outils de fichiers de l'agent ne franchissent pas (#839, EF-38).

    `racine` est **canonique** (`valider_racine`/`canonique`), `exclus` sont les
    motifs compilés du périmètre. `refus` rend le motif qui interdit un appel
    d'outil, ou `None` s'il passe — c'est la seule question que le hook lui pose.

    Trois refus, dans cet ordre, chacun avec sa phrase :

    1. **hors de la racine** — `chemin_dans_racine` (#221) résout le chemin puis
       le vérifie **sous** la racine : un `..`, un chemin absolu d'ailleurs ou un
       lien pointant dehors sortent par là. Une **lecture** hors racine n'est pas
       refusée : le périmètre borne ce qu'on écrit chez l'utilisateur, pas ce
       qu'un agent peut lire sur le poste — c'était déjà vrai de la copie ;
    2. **par un lien symbolique** — le chemin résolu diffère du chemin demandé
       (à la casse près : Windows en change). Un lien **vers l'intérieur** de la
       racine passerait le premier contrôle et ferait écrire ailleurs que là où
       l'agent croit écrire ; le critère du ticket est « aucun lien symbolique
       n'est suivi », sans distinguer où il mène ;
    3. **exclu du périmètre** — le chemin, **ou l'un de ses dossiers parents**,
       est visé par une exclusion. Le motif `node_modules` ne matche que le
       dossier, et c'est un fichier dessous que l'agent écrit : sans remonter les
       parents, l'exclusion ne vaudrait que pour le nom exact. Vaut en lecture
       comme en écriture — ce que la copie ne contenait pas, l'agent ne le lit
       ni ne l'écrit.

    Deux de plus depuis #1304, qui tiennent à l'appel plutôt qu'au chemin : une
    **entrée illisible** (l'argument de chemin manque, ou n'en est pas un) est
    refusée au lieu de passer, et une **recherche** l'est dès que l'arbre qu'elle
    parcourt contient un chemin exclu — elle en rendrait le contenu.
    """

    racine: Path
    exclus: tuple[re.Pattern[str], ...]

    @classmethod
    def pour(cls, racine: Path | str, perimetre: Perimetre) -> FrontiereEcriture:
        """La frontière de `racine` sous `perimetre` — racine canonicalisée, motifs compilés."""
        return cls(racine=canonique(racine), exclus=motifs_compiles(perimetre.exclus))

    def refus(self, outil: str, arguments: Any) -> str | None:
        """Le motif qui interdit l'appel `outil(arguments)`, ou `None` s'il passe.

        `arguments` est le `tool_input` brut du SDK. Un outil qui ne touche aucun
        chemin (`OUTILS_A_CHEMIN`) n'a rien à confronter et passe ; un outil qui
        en touche un est jugé selon son geste (`OutilAChemin`).

        ⚠ Depuis #1304, une entrée qui **ne se lit pas** — pas un objet, argument
        de chemin exigé absent, vide ou d'un autre type — est **refusée**. Elle
        passait : « deviner en ferait refuser à tort », disait la règle d'avant,
        et c'est l'inverse qui s'est révélé vrai — un CLI qui renommerait
        l'argument ouvrait la frontière entière, sans un mot. Refuser à tort se
        voit et se corrige ; laisser passer à tort ne se voit pas.
        """
        usage = OUTILS_A_CHEMIN.get(outil)
        if usage is None:
            return None
        if not isinstance(arguments, Mapping):
            return _motif_illisible(outil, "son entrée n'est pas un objet")
        brut = arguments.get(usage.cle)
        if brut is None and not usage.exige:
            brut = ""
        if not isinstance(brut, str) or (usage.exige and not brut.strip()):
            return _motif_illisible(
                outil, f"l'argument `{usage.cle}` manque ou n'est pas un chemin"
            )
        # Un chemin facultatif absent vaut le répertoire courant de l'agent : la racine.
        chemin = brut.strip() or "."
        if usage.geste == RECHERCHE:
            return self._refus_recherche(chemin)
        if usage.geste == LISTE:
            return self._refus_liste(outil, chemin, arguments.get(CLE_MOTIF_LISTE))
        return self.refus_chemin(brut, ecriture=usage.geste == ECRITURE)

    def refus_chemin(self, brut: str, *, ecriture: bool) -> str | None:
        """Le motif qui interdit d'atteindre `brut`, ou `None` — `ecriture` dit le geste."""
        return self._refus_atteinte(brut, "Écriture" if ecriture else "Lecture", ecriture=ecriture)

    def _refus_recherche(self, chemin: str) -> str | None:
        """Le motif qui interdit de chercher sous `chemin`, ou `None` (#1304).

        Une recherche **lit** : elle est d'abord confrontée comme une lecture de
        `chemin` (un fichier exclu, un dossier exclu, un lien). Puis, parce qu'elle
        lit tout ce que l'arbre porte, elle est refusée dès que cet arbre contient
        un chemin exclu — le `.env` de la racine suffit. On ne sait pas lui retirer
        ce qu'elle ne doit pas lire : l'outil est celui du CLI, et le filtrer
        dépendrait de sa façon de le faire. Le refus nomme donc **où chercher** à la
        place, ce qui, sous la même portée, ne contient rien d'exclu.

        Un chemin hors de la racine n'est pas le sujet du périmètre, sauf s'il la
        **contient** : chercher depuis le dossier parent traverse le projet entier.
        """
        motif = self._refus_atteinte(chemin, "Recherche", ecriture=False)
        if motif is not None:
            return motif
        base = self._base_de_recherche(chemin)
        if base is None:
            return None
        atteints, propres = self._exclus_atteints(base)
        if not atteints:
            return None
        return _motif_recherche(chemin, atteints, propres)

    def _refus_liste(self, outil: str, chemin: str, motif: object) -> str | None:
        """Le motif qui interdit de lister `motif` sous `chemin`, ou `None` (#1304).

        Une liste ne rend que des **noms** : ce qu'un joker traverse n'en sort pas
        plus lu. Ce qu'elle vise **en toutes lettres** — son dossier, et la partie
        de son motif qui précède le premier joker — est confronté comme une
        lecture : lister `secrets/*` ou `node_modules/**`, c'est parcourir ce que
        le périmètre retire.
        """
        if not isinstance(motif, str) or not motif.strip():
            return _motif_illisible(outil, f"l'argument `{CLE_MOTIF_LISTE}` manque ou est vide")
        refus = self._refus_atteinte(chemin, "Recherche", ecriture=False)
        if refus is not None:
            return refus
        prefixe = _prefixe_litteral(motif)
        if not prefixe:
            return None
        return self._refus_atteinte(str(Path(chemin) / prefixe), "Recherche", ecriture=False)

    def _base_de_recherche(self, chemin: str) -> str | None:
        """Ce que la recherche parcourt de la racine, relatif (POSIX) — `None` si rien.

        `""` désigne la racine entière : une recherche lancée depuis la racine, ou
        depuis un dossier qui la contient. Un fichier ne se parcourt pas — il a
        été jugé comme une lecture —, et un chemin disjoint du projet non plus.
        """
        candidat = self._candidat(chemin)
        try:
            cible = chemin_dans_racine(self.racine, candidat)
        except RacineRefusee:
            try:
                resolu = candidat.resolve()
            except OSError:  # chemin illisible : la recherche ne lira rien non plus
                return None
            return "" if _sous(self.racine, resolu) else None
        if not cible.is_dir():
            return None
        relatif = cible.relative_to(self.racine).as_posix()
        return "" if relatif == "." else relatif

    def _exclus_atteints(self, base: str) -> tuple[list[str], list[str]]:
        """Ce qu'une recherche lancée sur `base` atteindrait d'exclu, et où chercher à la place.

        Parcours du même régime que le recensement (`fichiers_du_perimetre`) —
        itératif, trié, sans descendre dans un chemin exclu —, qui **relève** les
        exclusions au lieu de les sauter. Un **lien symbolique** qui mène dans la
        racine en est aussi : on ne sait pas si l'outil le suivra, et une frontière
        qui ne suit aucun lien ne parie pas qu'il ne le fera pas. Un lien qui mène
        dehors, lui, ne lit rien du projet.

        Rend les chemins atteints (relatifs à la racine) et les entrées de `base`
        qui n'en contiennent aucun — les **adresses** que le refus donne.
        """
        atteints: list[str] = []
        souillees: set[str] = set()
        premieres: list[str] = []
        pile: list[tuple[str, str]] = [(base, "")]
        while pile:
            relatif_dossier, tete = pile.pop()
            dossier = self.racine / relatif_dossier if relatif_dossier else self.racine
            try:
                with os.scandir(dossier) as entrees:
                    triees = sorted(entrees, key=lambda entree: entree.name)
            except OSError:  # dossier devenu illisible : la recherche n'y lira rien
                continue
            for entree in triees:
                relatif = f"{relatif_dossier}/{entree.name}" if relatif_dossier else entree.name
                premiere = tete or relatif
                lien = entree.is_symlink()
                if not tete and not lien:
                    premieres.append(relatif)
                if _correspond(relatif, self.exclus) or (lien and self._lien_interieur(entree)):
                    atteints.append(relatif)
                    souillees.add(premiere)
                elif not lien and entree.is_dir():
                    pile.append((relatif, premiere))
        propres = [entree for entree in premieres if entree not in souillees]
        return sorted(atteints), propres

    def _lien_interieur(self, entree: os.DirEntry[str]) -> bool:
        """Le lien `entree` mène-t-il dans la racine ? Un lien cassé ne mène nulle part."""
        try:
            cible = Path(entree.path).resolve(strict=True)
        except (OSError, RuntimeError):  # lien cassé ou en boucle : rien à lire
            return False
        return _sous(cible, self.racine)

    def _refus_atteinte(self, brut: str, geste: str, *, ecriture: bool) -> str | None:
        """Le motif qui interdit d'atteindre `brut` pour `geste`, ou `None`."""
        candidat = self._candidat(brut)
        try:
            cible = chemin_dans_racine(self.racine, candidat)
        except RacineRefusee as refus:
            if not ecriture:
                return None
            return (
                f"{geste} refusée : {brut} sort de la racine du projet ({self.racine}) — "
                f"{refus.motif}. Un projet non versionné se remplit dans sa racine, "
                "jamais au-dessus ni à côté (EF-38)."
            )
        if not _meme_chemin(cible, candidat):
            return (
                f"{geste} refusée : {brut} passe par un lien symbolique (→ {cible}). "
                "Aucun lien n'est suivi dans la racine d'un projet — écris à "
                "l'emplacement réel, ou à un chemin qui n'en traverse pas."
            )
        relatif = cible.relative_to(self.racine).as_posix()
        if _sous_exclusion(relatif, self.exclus):
            return (
                f"{geste} refusée : {relatif} est exclu du périmètre du projet "
                "(`.git`, `node_modules`, `.env`, `**/secrets/**` et les motifs "
                "déclarés). Ces chemins ne sont ni lus ni écrits par un agent."
            )
        return None

    def _candidat(self, brut: str) -> Path:
        """Le chemin **lexical** demandé : joint à la racine s'il est relatif, `..` repliés.

        C'est lui qu'on compare au chemin résolu : la résolution suit les liens,
        le repli lexical non — leur écart est exactement un lien traversé.
        """
        chemin = Path(brut.strip()).expanduser()
        if not chemin.is_absolute():
            chemin = self.racine / chemin
        return Path(os.path.normpath(chemin))


def frontiere_de(workspace: Path | str, projet: Projet | None) -> FrontiereEcriture | None:
    """La frontière à armer sur `workspace`, ou `None` — le régime en place, et lui seul.

    Armée si et seulement si l'espace de travail **est** la racine du projet :
    c'est la définition du régime, et c'est un fait que l'appelant peut vérifier
    sans qu'on lui passe un drapeau. Un worktree (hors racine, par construction)
    et un `mkdtemp()` (sans projet) n'en reçoivent aucune — leur régime ne bouge
    pas —, et un projet non versionné dont l'espace ne serait *pas* la racine non
    plus : armer la frontière de la racine sur un autre chemin ferait refuser
    toute écriture dans l'espace où l'agent travaille.
    """
    if projet is None:
        return None
    if not _meme_chemin(canonique(workspace), canonique(projet.racine)):
        return None
    return FrontiereEcriture.pour(projet.racine, projet.perimetre)


def presents_de(racine: Path, perimetre: Perimetre) -> frozenset[str]:
    """Ce qui se trouve **déjà** dans `racine` — chemins relatifs POSIX (#1226).

    C'est l'oracle de « ce que l'agent n'a pas produit » : relevé avant qu'il ne
    travaille, il dit ce que la personne avait posé là. Les **dossiers** y entrent
    avec leurs fichiers (`src/` autant que `src/app.py`), parce qu'un `rm -rf src`
    vise le dossier ; la racine elle-même y figure sous la chaîne vide, et
    seulement si elle n'est pas vide — effacer un dossier vide ne détruit rien.

    Même parcours que le recensement (`fichiers_du_perimetre`), donc les mêmes
    bornes : les exclusions du périmètre ne sont pas descendues, l'atelier est
    sauté, aucun lien symbolique n'est suivi. Un dossier **vide** n'en ressort pas
    — un fichier seul y mène —, et c'est la même raison : il n'y a rien à perdre.

    ⚠ Ce relevé est le **second** de la tâche : `EspaceEnPlace.derive` en fait
    déjà un pour ses empreintes. Ils ne se partagent pas, et c'est un choix
    mesuré : les faire partager demanderait de faire voyager l'espace de travail
    jusqu'au fournisseur, dont le contrat ne prend qu'un chemin — chaque
    fournisseur et chaque double porteraient alors un objet dont un seul régime a
    l'usage. Le prix est un parcours de plus, borné par le périmètre (ni `.git`
    ni `node_modules`) sur un projet **non versionné**, donc petit par
    construction.
    """
    exclus = motifs_compiles(perimetre.exclus)
    presents: set[str] = set()
    for relatif in fichiers_du_perimetre(racine, exclus, hors=(DOSSIER_ATELIER,)):
        presents.add(relatif)
        morceaux = relatif.split("/")
        presents.update(
            "/".join(morceaux[: profondeur + 1]) for profondeur in range(len(morceaux) - 1)
        )
    if presents:
        presents.add("")
    return frozenset(presents)


def portee_de(workspace: Path | str, projet: Projet | None) -> PorteeProjet:
    """La portée « projet » de cette session — sa racine **est** l'espace de travail.

    Toujours rendue, dans les trois régimes, et c'est ce qui rend le cran
    `auto, portée projet` évaluable partout : ce que l'agent a sous les pieds est
    le dossier où il travaille, qu'il s'agisse de la racine d'un projet non
    versionné, d'un worktree ou d'un répertoire jetable.

    Ce qui change d'un régime à l'autre est ce qu'on **protège** : seul le régime
    en place relève ce qui s'y trouvait déjà. Un worktree et un `mkdtemp()` sont
    des copies — ce qu'on y détruit ne se perd pas, la branche et le projet sont
    ailleurs —, et y faire remonter un `rm` ferait attendre une personne pour
    rien. L'écriture en place, elle, n'a ni fusion ni diff (docs/24 §2.4) : c'est
    exactement là que la garde vaut.
    """
    racine = canonique(workspace)
    if projet is None or not _meme_chemin(racine, canonique(projet.racine)):
        return PorteeProjet(racine=racine)
    return PorteeProjet(racine=racine, presents=presents_de(racine, projet.perimetre))


def _meme_chemin(un: Path, autre: Path) -> bool:
    """`un` et `autre` désignent-ils le même chemin, à la casse près (comparaison de l'OS) ?"""
    return os.path.normcase(str(un)) == os.path.normcase(str(autre))


def _sous(chemin: Path, parent: Path) -> bool:
    """`chemin` est-il `parent` ou l'un de ses descendants, à la casse près ?"""
    enfant, ancetre = os.path.normcase(str(chemin)), os.path.normcase(str(parent))
    try:
        return os.path.commonpath([enfant, ancetre]) == ancetre
    except ValueError:  # deux disques différents : rien en commun
        return False


def _prefixe_litteral(motif: str) -> str:
    """Les segments d'un motif de liste qui précèdent le premier joker — `""` s'il commence par un.

    `src/**/*.py` vise `src`, `secrets/*` vise `secrets`, `.env` se vise
    lui-même ; `**/*.py` ne nomme rien. C'est ce que la liste **nomme**, donc ce
    que la frontière peut confronter sans deviner ce que le joker recouvrira.
    """
    segments = motif.strip().replace("\\", "/").split("/")
    litteraux: list[str] = []
    for segment in segments:
        if any(caractere in JOKERS for caractere in segment):
            break
        litteraux.append(segment)
    prefixe = "/".join(litteraux)
    # Un motif absolu garde sa racine (`/`), que la jointure a mangée.
    if motif.strip().startswith("/") and not prefixe.startswith("/"):
        prefixe = "/" + prefixe
    return prefixe.strip() if prefixe.strip("/") else ""


def _motif_illisible(outil: str, raison: str) -> str:
    """Le motif d'un outil de fichiers dont l'entrée ne se lit pas (#1304)."""
    return (
        f"Appel de `{outil}` refusé : entrée illisible pour la frontière du projet — "
        f"{raison}. Ce qu'elle ne sait pas situer, elle ne le laisse pas passer."
    )


def _motif_recherche(chemin: str, atteints: list[str], propres: list[str]) -> str:
    """Le motif d'une recherche refusée : ce qu'elle lirait d'exclu, et où chercher (#1304)."""
    exclus = _enumere(atteints)
    motif = (
        f"Recherche refusée : sous « {chemin} », elle lirait des chemins exclus du "
        f"périmètre du projet ({exclus}), et ce que le périmètre retire n'est lu par "
        "aucun outil. "
    )
    if propres:
        return motif + (
            f"Cherche dans ce qui n'en contient pas : {_enumere(propres)} — ou vise "
            "un fichier précis."
        )
    return motif + "Vise un fichier précis, ou un dossier qui n'en contient pas."


def _enumere(chemins: list[str]) -> str:
    """`chemins` en liste lisible, bornée à `NOMMES_MAX` — ce qu'elle tait est compté."""
    nommes = ", ".join(f"`{chemin}`" for chemin in chemins[:NOMMES_MAX])
    reste = len(chemins) - NOMMES_MAX
    return nommes + (f" et {reste} autre(s)" if reste > 0 else "")


def _correspond(relatif: str, motifs: tuple[re.Pattern[str], ...]) -> bool:
    """`relatif` est-il visé par l'un des motifs ?"""
    return any(motif.match(relatif) for motif in motifs)


def _sous_exclusion(relatif: str, exclus: tuple[re.Pattern[str], ...]) -> bool:
    """`relatif` est-il exclu, lui **ou l'un de ses dossiers parents** ?

    Le pendant de « un dossier exclu n'est même pas parcouru » : ce que le
    parcours ne descend pas, la frontière ne le laisse pas écrire.
    """
    morceaux = relatif.split("/")
    return any(
        _correspond("/".join(morceaux[: profondeur + 1]), exclus)
        for profondeur in range(len(morceaux))
    )
