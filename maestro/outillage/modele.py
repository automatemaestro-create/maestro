"""Les formes de l'analyse d'un projet et de l'outillage qu'elle recommande (#1030).

Inerte, au patron de [`maestro.projets.modele`](../projets/modele.py) : ce module
**décrit et sérialise**, il ne touche pas au disque. Ce qui l'y confronte est
ailleurs — `maestro.outillage.analyse` (parcourir, constater) et
`maestro.outillage.recommandation` (en déduire l'outillage). C'est ce qui rend
les deux testables sur des constats fabriqués, sans projet réel.

Trois familles d'objets, et la frontière entre les deux premières est la chose à
ne pas confondre :

- **les bornes et le parcours** (`Bornes`, `Parcours`) — ce que l'analyse s'est
  autorisé à lire, et ce qu'elle a effectivement vu. Les bornes voyagent
  **dans la réponse**, parce qu'une analyse qui s'est arrêtée à 20 000 fichiers
  et une analyse complète ne disent pas la même chose du projet, et que rien ne
  permettrait de les distinguer si le plafond restait dans le code ;
- **les constats** (`Constats`) — ce qui a été **lu dans le projet**. Chacun
  porte le chemin qui le prouve. Un constat sans son chemin serait une
  affirmation, et c'est précisément ce que le ticket refuse ;
- **la recommandation** (`Recommandation`, `Entree`, `Ecarte`) — ce que Maestro
  propose de générer. Chaque entrée porte sa `raison` et sa `justification` (le
  chemin du projet d'où elle sort), et son `etat` dit si le projet la porte
  **déjà** — reconnue, jamais dupliquée.

Et, depuis #1158, **la lecture** (`Lecture`, `Refus`, `ConstatEcarte`) : ce que
le modèle a ouvert du projet et ce qu'il en a tiré. Elle est la **provenance** des
constats que les tables ne connaissaient pas — un constat du modèle n'entre dans
`Constats` qu'avec le fichier qu'il a lu pour le dire.

`Analyse.source_manifeste()` rend le fragment `source` du manifeste de
[docs/38 §4.1](../../docs/38-decision-outillage-universel-du-projet.md) : c'est
le seul endroit où la forme de ce fragment est écrite côté analyse, et c'est
elle que #1033 recopiera tel quel. Deux orthographes de ce fragment finiraient
par diverger, et c'est la traçabilité d'un outillage généré qu'on perdrait.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from maestro.projets.modele import Vcs

#: Version de la forme servie. Elle voyage dans la réponse au même titre que
#: `manifeste: 1` (docs/38 §4.1) : un consommateur — #1031, #1033, l'écran de
#: #1034 — doit pouvoir dire « je ne sais pas lire cette forme-là » plutôt que
#: de lire de travers une forme qui a changé.
VERSION_ANALYSE = 1

#: Préfixe des identifiants d'analyse (`ana-3c9` dans l'exemple de docs/38 §4.1).
#: Même entropie que les identifiants de projet — 8 hex d'un `uuid4` tronqué —
#: pour la même raison : c'est la **référence** qu'un manifeste garde, et deux
#: analyses qui la partageraient rendraient un outillage inexplicable.
PREFIXE_ID = "ana-"

#: Les usages qu'une commande peut servir, dans l'ordre où ils se présentent à
#: quelqu'un qui arrive sur un projet. Cet ordre est celui du rendu : il n'a pas
#: à être trié à l'affichage, et deux analyses du même projet rendent la même
#: liste.
USAGES: tuple[str, ...] = (
    "installer",
    "construire",
    "tester",
    "lint",
    "formater",
    "types",
    "demarrer",
)

#: Ce qu'un constat de commande dit de sa provenance. `declaree` : le projet
#: l'écrit (un script de `package.json`, une cible de `Makefile`) ; `convention` :
#: c'est la commande de l'outil détecté (`cargo test` pour un `Cargo.toml`), que
#: le projet n'écrit nulle part. La distinction est la seule honnête : une
#: convention peut être fausse sur un projet qui fait autrement, et la recopier
#: sans le dire ferait passer une supposition pour une lecture.
ORIGINES_COMMANDE: frozenset[str] = frozenset({"declaree", "convention"})

#: L'état d'une entrée recommandée. `deja-present` est celui qui compte : c'est
#: la forme que prend « ce que le projet porte déjà est reconnu plutôt que
#: dupliqué » — l'entrée reste dans la réponse, avec le chemin de ce qui existe,
#: au lieu d'en disparaître.
ETATS_ENTREE: frozenset[str] = frozenset({"a-generer", "a-completer", "deja-present"})

#: Ce qu'est devenue la **lecture du projet par le modèle** (#1158). `lue` : le
#: modèle a conclu, et ses constats ont été confrontés à ce qu'il a lu ;
#: `inachevee` : il n'a pas conclu dans les tours servis ; `indisponible` : il
#: n'a pas répondu (fournisseur injoignable ou absent, réponse vide ou hors
#: contrat). Dans les deux derniers cas l'analyse **reste** celle des indices —
#: elle ne s'efface pas derrière un appel manqué, et elle le dit.
ETATS_LECTURE: frozenset[str] = frozenset({"lue", "inachevee", "indisponible"})


def nouvel_id() -> str:
    """Un identifiant d'analyse neuf, de la forme `ana-<8 hex>`."""
    return f"{PREFIXE_ID}{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class Bornes:
    """Ce que l'analyse s'autorise à lire — **explicitement**, et dans la réponse.

    `ignores` sont des **noms de dossiers** que le parcours ne descend jamais :
    un `node_modules` ou un `target` coûterait à lui seul tout le budget de
    fichiers et n'apprendrait rien du projet. Ils s'ajoutent aux motifs du
    périmètre déclaré du projet (`Perimetre.exclus`, qui retire déjà `.env` et
    `**/secrets/**`) : l'analyse ne lit donc jamais les deux gisements de
    secrets de docs/24 §2.5.

    `octets_par_fichier_max` ne borne que les fichiers **nommés** que l'analyse
    ouvre (les manifestes, la CI, les conventions) : le parcours, lui, ne lit
    aucun contenu — il compte des extensions et retient des chemins.

    Les quatre dernières bornent la **lecture par le modèle** (#1158), qui est
    une autre question : ce que le modèle lit entre dans son prompt, et c'est la
    taille d'un prompt qu'elles tiennent, pas celle d'un manifeste qu'on analyse.
    `lectures_max` compte tout ce qui lui est servi du disque (un dossier listé,
    un fichier lu), `octets_par_lecture_max` coupe un fichier lu — la coupure
    lui est dite, et à l'appelant aussi —, `entrees_par_liste_max` coupe un
    dossier listé, `tours_max` le nombre d'échanges avant qu'il doive conclure.
    """

    fichiers_max: int = 20_000
    profondeur_max: int = 8
    octets_par_fichier_max: int = 256 * 1024
    ignores: tuple[str, ...] = ()
    lectures_max: int = 24
    octets_par_lecture_max: int = 12_000
    entrees_par_liste_max: int = 200
    tours_max: int = 6

    def to_dict(self) -> dict[str, Any]:
        """Les bornes en JSON, `lecture_seule`/`execution` compris.

        Les deux derniers champs ne sont pas des réglages : ce sont les deux
        promesses du ticket rendues **lisibles par l'appelant**. Elles sont
        constantes parce qu'elles ne sont pas négociables — aucun appel ne peut
        les changer, et une analyse qui exécuterait quoi que ce soit du projet
        ne serait pas une analyse bornée différemment, ce serait autre chose.
        Elles valent pour la lecture par le modèle comme pour le parcours : le
        modèle ne reçoit que deux verbes, et aucun n'exécute.
        """
        return {
            "fichiers_max": self.fichiers_max,
            "profondeur_max": self.profondeur_max,
            "octets_par_fichier_max": self.octets_par_fichier_max,
            "ignores": list(self.ignores),
            "lectures_max": self.lectures_max,
            "octets_par_lecture_max": self.octets_par_lecture_max,
            "entrees_par_liste_max": self.entrees_par_liste_max,
            "tours_max": self.tours_max,
            "lecture_seule": True,
            "execution": "aucune",
        }


@dataclass(frozen=True)
class Parcours:
    """Ce que l'analyse a effectivement vu, et ce qu'elle n'a **pas** vu.

    `troncatures` nomme les bornes atteintes (`fichiers-max`, `profondeur-max`,
    `fichier-trop-gros`) : une analyse tronquée qui ne le dirait pas se lirait
    comme un projet plus petit qu'il n'est, et c'est le genre d'erreur qu'on ne
    voit jamais — il manque des constats, pas des messages.

    `extensions` compte **toutes** les extensions vues, et pas seulement celles
    que `LANGAGE_PAR_EXTENSION` connaît (#1158) : c'est l'indice qui dit au
    modèle, avant qu'il ouvre quoi que ce soit, qu'il y a douze `.csproj` ou un
    `gleam.toml` dans ce projet — la table ne décide plus de ce qui se voit.
    Les plus fréquentes d'abord, plafonnées (`EXTENSIONS_MAX`).
    """

    fichiers_vus: int = 0
    dossiers_vus: int = 0
    profondeur_atteinte: int = 0
    troncatures: tuple[str, ...] = ()
    ignores_rencontres: tuple[str, ...] = ()
    extensions: tuple[tuple[str, int], ...] = ()

    @property
    def tronque(self) -> bool:
        """L'analyse s'est-elle arrêtée avant d'avoir tout vu ?"""
        return bool(self.troncatures)

    def fichiers_d_extension(self, extension: str) -> int:
        """Le nombre de fichiers vus portant `extension` (`.cs`), 0 si aucun."""
        voulue = extension.lower()
        return next((compte for ext, compte in self.extensions if ext == voulue), 0)

    def to_dict(self) -> dict[str, Any]:
        """Le parcours en JSON."""
        return {
            "fichiers_vus": self.fichiers_vus,
            "dossiers_vus": self.dossiers_vus,
            "profondeur_atteinte": self.profondeur_atteinte,
            "tronque": self.tronque,
            "troncatures": list(self.troncatures),
            "ignores_rencontres": list(self.ignores_rencontres),
            "extensions": [
                {"extension": extension, "fichiers": compte}
                for extension, compte in self.extensions
            ],
        }


@dataclass(frozen=True)
class Piece:
    """Un fichier (ou dossier) du projet qui **prouve** un constat.

    La même forme pour la CI, les conventions écrites, l'outillage déjà présent
    et les scripts constatés : ce sont quatre questions différentes posées au
    même objet — *quel fichier, à quel endroit, dans quel rôle*. Trois formes
    séparées auraient fini par diverger sur le nom du champ de chemin.

    `chemin` est **relatif à la racine**, en POSIX : c'est la forme qui traverse
    le JSON sans échappement et qui ne révèle pas l'arborescence du poste.
    """

    nom: str
    chemin: str
    role: str = ""

    def to_dict(self) -> dict[str, Any]:
        """La pièce en JSON."""
        return {"nom": self.nom, "chemin": self.chemin, "role": self.role}


@dataclass(frozen=True)
class Langage:
    """Un langage constaté, sa part des fichiers vus et un exemple.

    `part` est calculée sur les fichiers **de code** vus, pas sur tous les
    fichiers : un dépôt de documentation avec trois scripts ne doit pas rendre
    « Shell : 2 % ». `exemple` est là pour que la part ne soit pas un chiffre
    sans preuve.
    """

    nom: str
    fichiers: int
    part: float
    exemple: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Le langage en JSON."""
        return {
            "nom": self.nom,
            "fichiers": self.fichiers,
            "part": self.part,
            "exemple": self.exemple,
        }


@dataclass(frozen=True)
class Gestionnaire:
    """Un gestionnaire de paquets constaté : ce qui le prouve, et son verrou.

    `verrou` (`package-lock.json`, `uv.lock`, `poetry.lock`…) n'est pas un
    détail : c'est lui qui décide *quelle* commande d'installation est juste —
    `npm ci` avec un verrou, `npm install` sans.
    """

    nom: str
    chemin: str
    verrou: str | None = None
    installer: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Le gestionnaire en JSON."""
        return {
            "nom": self.nom,
            "chemin": self.chemin,
            "verrou": self.verrou,
            "installer": self.installer,
        }


@dataclass(frozen=True)
class Commande:
    """Une commande du projet, avec l'endroit du projet qui la justifie.

    `chemin` + `extrait` disent **où** elle a été lue (`package.json` →
    `scripts.test`), `origine` **comment** (cf. `ORIGINES_COMMANDE`). Une
    commande sans ces trois champs serait invérifiable, et c'est ce qui
    distingue une analyse d'une supposition bien tournée.
    """

    usage: str
    commande: str
    chemin: str
    extrait: str = ""
    origine: str = "declaree"

    def to_dict(self) -> dict[str, Any]:
        """La commande en JSON."""
        return {
            "usage": self.usage,
            "commande": self.commande,
            "chemin": self.chemin,
            "extrait": self.extrait,
            "origine": self.origine,
        }


@dataclass(frozen=True)
class Forge:
    """La forge du projet, constatée — jamais demandée à l'appelant.

    `distant` est l'URL telle qu'elle est écrite dans le dépôt ; `chemin` dit
    d'où elle vient (`.git/config`, ou le marqueur de CI qui l'a trahie).
    """

    nom: str
    distant: str | None = None
    chemin: str = ""

    def to_dict(self) -> dict[str, Any]:
        """La forge en JSON."""
        return {"nom": self.nom, "distant": self.distant, "chemin": self.chemin}


@dataclass(frozen=True)
class DossierScripts:
    """Le dossier de scripts du projet — **constaté**, jamais inventé (docs/38 §3.4).

    `constate` est faux quand le projet n'en a aucun : `chemin` porte alors le
    défaut (`scripts`), et c'est ce que `AGENTS.md` nommerait. Un projet
    existant a ses habitudes (`bin/`, `tools/`), et une analyse qui les ignore
    produit un doublon de plus, pas un outillage.

    `scripts` liste ce que le dossier porte **déjà**, à un seul niveau : c'est
    cette liste qui permet à la recommandation d'appeler un script existant au
    lieu de réécrire sa commande.
    """

    chemin: str = "scripts"
    constate: bool = False
    scripts: tuple[Piece, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Le dossier de scripts en JSON."""
        return {
            "chemin": self.chemin,
            "constate": self.constate,
            "scripts": [piece.to_dict() for piece in self.scripts],
        }


@dataclass(frozen=True)
class Constats:
    """Tout ce que l'analyse a **lu** dans le projet — la matière de la recommandation."""

    langages: tuple[Langage, ...] = ()
    gestionnaires: tuple[Gestionnaire, ...] = ()
    commandes: tuple[Commande, ...] = ()
    ci: tuple[Piece, ...] = ()
    forge: Forge | None = None
    vcs: Vcs | None = None
    conventions: tuple[Piece, ...] = ()
    dossier_scripts: DossierScripts = field(default_factory=DossierScripts)
    outillage_present: tuple[Piece, ...] = ()

    def commande_de(self, usage: str) -> Commande | None:
        """La première commande constatée pour `usage`, `None` s'il n'y en a aucune.

        « La première » est un choix : les commandes sont rangées dans l'ordre
        où elles ont été lues, et une commande **déclarée** par le projet est
        toujours lue avant la convention de son gestionnaire.
        """
        return next((c for c in self.commandes if c.usage == usage), None)

    def to_dict(self) -> dict[str, Any]:
        """Les constats en JSON."""
        return {
            "langages": [langage.to_dict() for langage in self.langages],
            "gestionnaires": [g.to_dict() for g in self.gestionnaires],
            "commandes": [commande.to_dict() for commande in self.commandes],
            "ci": [piece.to_dict() for piece in self.ci],
            "forge": self.forge.to_dict() if self.forge is not None else None,
            "vcs": self.vcs.to_dict() if self.vcs is not None else None,
            "conventions": [piece.to_dict() for piece in self.conventions],
            "dossier_scripts": self.dossier_scripts.to_dict(),
            "outillage_present": [piece.to_dict() for piece in self.outillage_present],
        }


@dataclass(frozen=True)
class Entree:
    """Une pièce d'outillage recommandée : quoi, où, pourquoi, et d'où ça sort.

    `type` suit docs/38 : `instructions` (`AGENTS.md`), `pont` (`CLAUDE.md`,
    `GEMINI.md`), `skill` (`.agents/skills/<nom>/`), `script`.

    `justification` est **l'endroit du projet** qui la justifie — le fichier
    lu, pas une phrase. C'est ce que le ticket demande, et c'est aussi ce qui
    rend une recommandation contestable : on peut ouvrir le fichier et vérifier.

    `etat` dit ce qu'il reste à faire. `deja-present` **garde** l'entrée dans la
    réponse : la reconnaissance de l'existant est une information, pas un
    silence — sans elle, un `AGENTS.md` déjà écrit et un `AGENTS.md` jamais
    envisagé se ressembleraient.
    """

    type: str
    nom: str
    chemin: str
    etat: str
    raison: str
    justification: Piece | None = None
    commandes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """L'entrée en JSON."""
        return {
            "type": self.type,
            "nom": self.nom,
            "chemin": self.chemin,
            "etat": self.etat,
            "raison": self.raison,
            "justification": (
                self.justification.to_dict() if self.justification is not None else None
            ),
            "commandes": list(self.commandes),
        }


@dataclass(frozen=True)
class Ecarte:
    """Ce que l'analyse a **choisi de ne pas** recommander, avec sa raison.

    Deux familles, et les deux sont utiles : ce que docs/38 écarte par décision
    (les commandes, §3.5) et ce que le projet ne justifie pas (aucune commande
    de test constatée). Sans cette liste, « pas de skill de tests » se lirait
    comme un oubli de Maestro plutôt que comme un fait du projet.
    """

    type: str
    nom: str
    raison: str

    def to_dict(self) -> dict[str, Any]:
        """L'écarté en JSON."""
        return {"type": self.type, "nom": self.nom, "raison": self.raison}


@dataclass(frozen=True)
class Recommandation:
    """L'outillage recommandé : ce qui s'écrit, et ce qui a été écarté."""

    entrees: tuple[Entree, ...] = ()
    ecartes: tuple[Ecarte, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """La recommandation en JSON."""
        return {
            "entrees": [entree.to_dict() for entree in self.entrees],
            "ecartes": [ecarte.to_dict() for ecarte in self.ecartes],
        }


@dataclass(frozen=True)
class Refus:
    """Une demande de lecture du modèle que l'explorateur n'a **pas** servie (#1158).

    `motif` est un code court (`hors-perimetre`, `hors-racine`, `lien-symbolique`,
    `dossier-ignore`, `introuvable`, `pas-un-fichier`, `pas-un-dossier`,
    `lectures-max`) : c'est la trace qu'un `.env` a été **demandé** et **refusé**,
    ce qui se lit autrement qu'un `.env` jamais demandé — et les deux autrement
    qu'un `.env` lu.
    """

    demande: str
    chemin: str
    motif: str

    def to_dict(self) -> dict[str, Any]:
        """Le refus en JSON."""
        return {"demande": self.demande, "chemin": self.chemin, "motif": self.motif}


@dataclass(frozen=True)
class ConstatEcarte:
    """Un constat que le modèle a rendu et que la confrontation au disque a écarté (#1158).

    `ligne` est ce que le modèle a écrit, telle quelle ; `raison` dit ce qui lui
    manquait — le plus souvent, un fichier cité qu'il n'avait pas lu. Un constat
    écarté reste **dans la réponse** : il dit ce que le modèle croyait, et
    pourquoi Maestro ne l'a pas retenu.
    """

    ligne: str
    raison: str

    def to_dict(self) -> dict[str, Any]:
        """Le constat écarté en JSON."""
        return {"ligne": self.ligne, "raison": self.raison}


@dataclass(frozen=True)
class Lecture:
    """La lecture du projet **par le modèle** — ce qu'il a ouvert, et ce qui en est resté (#1158).

    C'est la **provenance** des constats qui ne sortent pas des tables : ce que
    `retenus` porte a été proposé par le modèle, puis confronté à ce qu'il a
    effectivement lu (`lus`) — un constat qui cite un fichier non lu sort dans
    `ecartes`, jamais dans les constats. Tout le reste de `Analyse.constats`
    vient des tables, qui ne sont plus que des indices.

    Les bornes de la lecture sont celles de `Bornes` ; celles qui ont **mordu**
    sont nommées dans `troncatures` (`fichier-tronque`, `liste-tronquee`,
    `lectures-max`, `tours-max`), et les chemins coupés dans `tronques`. Une
    lecture tronquée qui ne le dirait pas se lirait comme un projet mieux lu
    qu'il ne l'a été.

    `refus` garde chaque demande que l'explorateur n'a pas servie : c'est là que
    se lit qu'un `.env` demandé n'a pas été ouvert.
    """

    etat: str
    motif: str = ""
    tours: int = 0
    lus: tuple[str, ...] = ()
    listes: tuple[str, ...] = ()
    refus: tuple[Refus, ...] = ()
    troncatures: tuple[str, ...] = ()
    tronques: tuple[str, ...] = ()
    retenus: Constats = field(default_factory=Constats)
    ecartes: tuple[ConstatEcarte, ...] = ()

    @property
    def tronque(self) -> bool:
        """La lecture s'est-elle arrêtée, ou a-t-elle coupé un fichier, avant d'avoir tout vu ?"""
        return bool(self.troncatures)

    def to_dict(self) -> dict[str, Any]:
        """La lecture en JSON — `retenus` réduit aux constats que le modèle peut rendre."""
        return {
            "etat": self.etat,
            "motif": self.motif,
            "tours": self.tours,
            "lus": list(self.lus),
            "listes": list(self.listes),
            "refus": [refus.to_dict() for refus in self.refus],
            "tronque": self.tronque,
            "troncatures": list(self.troncatures),
            "tronques": list(self.tronques),
            "retenus": {
                "langages": [langage.to_dict() for langage in self.retenus.langages],
                "gestionnaires": [g.to_dict() for g in self.retenus.gestionnaires],
                "commandes": [commande.to_dict() for commande in self.retenus.commandes],
                "ci": [piece.to_dict() for piece in self.retenus.ci],
            },
            "ecartes": [ecarte.to_dict() for ecarte in self.ecartes],
        }


@dataclass(frozen=True)
class Analyse:
    """L'analyse d'un projet existant et l'outillage qu'elle recommande (#1030).

    `resume` est la phrase que le manifeste garde (`source.resume`, docs/38
    §4.1) : elle tient en une ligne parce qu'elle est destinée à être relue six
    mois plus tard, à côté d'un outillage dont on se demande d'où il sort.

    `lecture` (#1158) est `None` tant que le modèle n'a pas été convié — c'est
    le cas de `analyser`, qui ne rend que les **indices** des tables — et porte
    la lecture du projet par le modèle sinon, y compris quand elle a échoué.
    """

    id: str
    projet_id: str
    racine: str
    faite_le: str
    resume: str = ""
    bornes: Bornes = field(default_factory=Bornes)
    parcours: Parcours = field(default_factory=Parcours)
    constats: Constats = field(default_factory=Constats)
    recommandation: Recommandation = field(default_factory=Recommandation)
    lecture: Lecture | None = None

    def source_manifeste(self) -> dict[str, Any]:
        """Le fragment `source` du manifeste d'outillage (docs/38 §4.1).

        Écrit **ici** et nulle part ailleurs : c'est #1033 qui le posera dans
        `.maestro/outillage/manifeste.json`, et une seconde formulation du même
        fragment finirait par diverger de celle-ci — on lirait alors un
        outillage sans savoir quelle analyse l'a recommandé.
        """
        return {
            "type": "analyse",
            "projet_id": self.projet_id,
            "reference": self.id,
            "resume": self.resume,
        }

    def to_dict(self) -> dict[str, Any]:
        """L'analyse entière en JSON — la forme servie par l'API."""
        return {
            "analyse": VERSION_ANALYSE,
            "id": self.id,
            "projet_id": self.projet_id,
            "racine": self.racine,
            "faite_le": self.faite_le,
            "resume": self.resume,
            "bornes": self.bornes.to_dict(),
            "parcours": self.parcours.to_dict(),
            "constats": self.constats.to_dict(),
            "recommandation": self.recommandation.to_dict(),
            "lecture": self.lecture.to_dict() if self.lecture is not None else None,
            "source_manifeste": self.source_manifeste(),
        }
