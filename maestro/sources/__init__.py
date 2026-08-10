"""Les sources d'un objectif — matière d'entrée déclarée, résolue et bornée (#315).

Socle de la **Phase 8** ([docs/24 §3](../../docs/24-projets-locaux-et-poste-de-travail.md),
parent #314) : un lancement ne portait qu'un objectif texte, il porte désormais
ce que cet objectif embarque — fichiers téléversés, dossier de références, URL
(EF-39, entité SOURCE de [docs/03](../../docs/03-modele-de-donnees.md)).

Deux modules, deux responsabilités qui ne se mélangent pas :

- `modele` — la **forme** (`Source`, `SourceRefusee`), module feuille, relue
  sans jamais être rejugée : un journal durable rejoué ne se refuse pas ;
- `resolution` — ce qu'une déclaration **devient** face au disque, au réseau et
  aux plafonds du run, et ce qui la fait refuser **avec son motif**.

Ce lot ne lit aucun contenu : il déclare, résout et refuse. L'extraction vers le
Markdown est le lot suivant (#316), le téléversement par l'API le lot #317.
"""

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
    "ID_RUN",
    "LONGUEUR_MAX_NOM",
    "LONGUEUR_MAX_URL",
    "TYPES_SOURCE",
    "TYPE_DOSSIER",
    "TYPE_FICHIER",
    "TYPE_URL",
    "Source",
    "SourceRefusee",
    "emplacement_ingestion",
    "racine_ingestion",
    "resoudre_sources",
    "sources_depuis",
    "sources_en_liste",
]
