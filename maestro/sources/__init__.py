"""Les sources d'un objectif — matière d'entrée déclarée, résolue, lue et bornée.

Socle de la **Phase 8** ([docs/24 §3](../../docs/24-projets-locaux-et-poste-de-travail.md),
parent #314) : un lancement ne portait qu'un objectif texte, il porte désormais
ce que cet objectif embarque — fichiers téléversés, dossier de références, URL
(EF-39, entité SOURCE de [docs/03](../../docs/03-modele-de-donnees.md)).

Cinq modules, cinq responsabilités qui ne se mélangent pas :

- `modele` — la **forme** (`Source`, `SourceRefusee`), module feuille, relue
  sans jamais être rejugée : un journal durable rejoué ne se refuse pas ;
- `resolution` (#315) — ce qu'une déclaration **devient** face au disque, au
  réseau et aux plafonds du run, et ce qui la fait refuser **avec son motif** ;
- `extraction` (#316) — ce qu'une source **dit** : tout ramené au Markdown, avec
  son rapport de lecture et son coût en tokens, et encadré comme **donnée** avant
  d'entrer dans un contexte ;
- `televersement` (#317) — où des octets reçus **attendent** leur run, et comment
  ils lui sont rattachés. Un navigateur ne livre pas de chemin, il livre des
  octets : c'est le seul moyen qu'une source `fichier` en désigne de vrais ;
- `apercu` (#319) — ce que des sources **donneraient**, joué à vide : la même
  lecture, rendue avant le lancement et sans rien conserver, pour que composer un
  objectif reste réversible tant que c'est gratuit.

Les deux régimes sont opposés à dessein : la résolution **refuse** (une saisie se
corrige), l'extraction **ignore ou tronque en le disant** (un contenu n'est pas
encore connu de qui l'a joint). Le téléversement suit celui de la résolution : ce
qui dépasse un plafond est refusé **pendant** la réception, jamais tronqué.

Les routes qui les servent — `POST /api/sources` et `POST /api/executions` — sont
au [§6.8 et au §6.1 de docs/05](../../docs/05-interface-control-tower.md).
"""

from maestro.sources.apercu import RUN_APERCU, apercu_sources
from maestro.sources.extraction import (
    ETAT_IGNORE,
    ETAT_LU,
    ETAT_TRONQUE,
    ETATS,
    EXTENSIONS_CONVERTIES,
    EXTENSIONS_TEXTE,
    GardeFousExtraction,
    Lecture,
    RapportLecture,
    contexte_markdown,
    estimer_tokens,
    extraire_sources,
)
from maestro.sources.modele import (
    LONGUEUR_MAX_NOM,
    TYPE_DOSSIER,
    TYPE_FICHIER,
    TYPE_URL,
    TYPES_SOURCE,
    Source,
    SourceRefusee,
    sources_depuis,
    sources_en_liste,
)
from maestro.sources.resolution import (
    ID_RUN,
    LONGUEUR_MAX_URL,
    emplacement_ingestion,
    nom_de_fichier,
    racine_ingestion,
    resoudre_sources,
)
from maestro.sources.televersement import (
    DOSSIER_TELEVERSEMENTS,
    ID_TELEVERSEMENT,
    DepotTeleversements,
    Televersement,
    declarer_televersements,
)

__all__ = [
    "DOSSIER_TELEVERSEMENTS",
    "ETATS",
    "ETAT_IGNORE",
    "ETAT_LU",
    "ETAT_TRONQUE",
    "EXTENSIONS_CONVERTIES",
    "EXTENSIONS_TEXTE",
    "ID_RUN",
    "ID_TELEVERSEMENT",
    "LONGUEUR_MAX_NOM",
    "LONGUEUR_MAX_URL",
    "RUN_APERCU",
    "TYPES_SOURCE",
    "TYPE_DOSSIER",
    "TYPE_FICHIER",
    "TYPE_URL",
    "DepotTeleversements",
    "GardeFousExtraction",
    "Lecture",
    "RapportLecture",
    "Source",
    "SourceRefusee",
    "Televersement",
    "apercu_sources",
    "contexte_markdown",
    "declarer_televersements",
    "emplacement_ingestion",
    "estimer_tokens",
    "extraire_sources",
    "nom_de_fichier",
    "racine_ingestion",
    "resoudre_sources",
    "sources_depuis",
    "sources_en_liste",
]
