"""Les sources d'un objectif — matière d'entrée déclarée, résolue, lue et bornée.

Socle de la **Phase 8** ([docs/24 §3](../../docs/24-projets-locaux-et-poste-de-travail.md),
parent #314) : un lancement ne portait qu'un objectif texte, il porte désormais
ce que cet objectif embarque — fichiers téléversés, dossier de références, URL
(EF-39, entité SOURCE de [docs/03](../../docs/03-modele-de-donnees.md)).

Trois modules, trois responsabilités qui ne se mélangent pas :

- `modele` — la **forme** (`Source`, `SourceRefusee`), module feuille, relue
  sans jamais être rejugée : un journal durable rejoué ne se refuse pas ;
- `resolution` (#315) — ce qu'une déclaration **devient** face au disque, au
  réseau et aux plafonds du run, et ce qui la fait refuser **avec son motif** ;
- `extraction` (#316) — ce qu'une source **dit** : tout ramené au Markdown, avec
  son rapport de lecture et son coût en tokens, et encadré comme **donnée** avant
  d'entrer dans un contexte.

Les deux régimes sont opposés à dessein : la résolution **refuse** (une saisie se
corrige), l'extraction **ignore ou tronque en le disant** (un contenu n'est pas
encore connu de qui l'a joint).

Le téléversement par l'API est le lot #317.
"""

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
    racine_ingestion,
    resoudre_sources,
)

__all__ = [
    "ETATS",
    "ETAT_IGNORE",
    "ETAT_LU",
    "ETAT_TRONQUE",
    "EXTENSIONS_CONVERTIES",
    "EXTENSIONS_TEXTE",
    "ID_RUN",
    "LONGUEUR_MAX_NOM",
    "LONGUEUR_MAX_URL",
    "TYPES_SOURCE",
    "TYPE_DOSSIER",
    "TYPE_FICHIER",
    "TYPE_URL",
    "GardeFousExtraction",
    "Lecture",
    "RapportLecture",
    "Source",
    "SourceRefusee",
    "contexte_markdown",
    "emplacement_ingestion",
    "estimer_tokens",
    "extraire_sources",
    "racine_ingestion",
    "resoudre_sources",
    "sources_depuis",
    "sources_en_liste",
]
