"""L'aperçu d'ingestion : ce que des sources donneraient, **sans rien engager** (#319).

Le critère 2 de l'écran *composer un objectif* demande que l'extraction soit
**visible avant de lancer** — par source, lu / ignoré (avec le motif) / tronqué,
et le coût estimé. C'est ce qui rend le geste réversible **tant qu'il est
gratuit** : corriger une saisie coûte un clic, corriger douze tâches coûte douze
exécutions ([docs/24 §3.4](../../docs/24-projets-locaux-et-poste-de-travail.md)).

Or le rapport de lecture (#316) est rendu par le **lancement** (docs/05 §6.1),
c'est-à-dire par le geste payant. Il manquait donc la même lecture, jouée à vide.
Ce module est cette lecture-là, et il tient en une phrase : on **résout** comme au
lancement (#315), on **lit** comme au lancement (#316), et on **efface**.

**Un aperçu ne dépose rien**, et c'est tout son contrat. Les octets d'un fichier
déposé sont écrits dans un dossier **jetable**, lus, puis retirés dans le `finally`
— jamais dans `core/ingestion/`, jamais dans le dépôt de téléversement (#317),
jamais dans le projet de l'utilisateur. Deux raisons, et la seconde est la vraie :

- répondre « ce document vaut 4 200 tokens » ne doit **rien** laisser derrière —
  sinon regarder deux fois remplirait le disque de matière que personne ne
  réclame, et il faudrait inventer un ramassage pour un geste qui, lui, est
  gratuit ;
- l'aperçu et le lancement restent **indépendants**. Le dépôt de téléversement
  existe pour qu'une matière **survive** jusqu'au run qui la consomme ; un aperçu
  ne survit à rien. Les mêler ferait dépendre une question (« qu'est-ce que ça
  donne ? ») d'un stockage qui n'a de sens que pour la réponse suivante.

Le dossier jetable va donc dans le répertoire temporaire du système et non sous
`.maestro/` : la règle du dépôt (CLAUDE.md) réserve `.maestro/<domaine>/` à **ce
qu'un humain est invité à relire**, et personne ne relira un tampon effacé trois
lignes plus bas.

Le régime des refus est celui de la résolution, pas celui de l'extraction : une
saisie fautive est **refusée** avec son motif et son index (`SourceRefusee`, donc
un 422 motivé), là où un contenu illisible n'est qu'une ligne « ignoré » du
rapport. C'est voulu — et c'est même le cœur du critère 3 de l'écran : « rien à
lire ici » et « je refuse de lire ça » ne s'affichent jamais pareil.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import IO, Any

from maestro.config import Settings, load_settings
from maestro.engine.guardrails import GardeFousIngestion
from maestro.sources.extraction import GardeFousExtraction, RapportLecture, extraire_sources
from maestro.sources.modele import TYPE_FICHIER, Source, SourceRefusee
from maestro.sources.resolution import emplacement_ingestion, resoudre_sources

#: Le `run_id` d'un aperçu. Un aperçu n'est le run de personne, mais
#: `emplacement_ingestion` en exige un — c'est lui qui donne au fichier déposé le
#: **même** chemin relatif qu'au lancement, donc la même résolution et les mêmes
#: refus. Le nom est fixe : le dossier qui le porte, lui, est unique et jetable.
RUN_APERCU = "apercu"

#: Taille des tranches de lecture d'un flux téléversé. Les octets sont confrontés
#: aux plafonds **pendant** l'écriture (comme #317 à la réception) : un fichier à
#: moitié écrit n'est pas une source, c'est un piège — le rapport le dirait « lu »
#: et l'écran conclurait sur un document amputé.
TAILLE_TRANCHE = 64 * 1024


def apercu_sources(
    declarations: Sequence[Mapping[str, Any]] | None,
    *,
    fichiers: Sequence[tuple[str, IO[bytes]]] = (),
    garde_fous: GardeFousIngestion | None = None,
    garde_fous_extraction: GardeFousExtraction | None = None,
    settings: Settings | None = None,
    recuperer_url: Callable[[str], str] | None = None,
) -> RapportLecture:
    """Le rapport de lecture de `declarations`, **joué à vide** — rien n'est conservé.

    `declarations` est la liste des sources dans **l'ordre où l'écran les a
    composées** : c'est cet ordre qui décide de ce qui entre quand le budget de
    tokens s'épuise (#316), donc un aperçu qui le changerait mentirait sur le
    lancement qu'il annonce.

    `fichiers` porte les octets déposés, sous forme de couples `(nom, flux)`. Le
    n-ième correspond à la n-ième déclaration de type `fichier` — un multipart ne
    transporte pas de correspondance, il transporte deux listes ordonnées.
    Compter juste est donc à la charge de l'appelant, et un décompte faux est
    **refusé** (`apercu-sans-octets`) plutôt que rapproché au hasard : associer le
    mauvais document au mauvais nom produirait un rapport crédible et faux.

    Lève `SourceRefusee` (donc `ValueError`) sur une saisie refusée, exactement
    comme le lancement : mêmes plafonds (`GardeFousIngestion`), mêmes motifs,
    même index. Ce qui n'est pas refusé mais illisible ressort en ligne « ignoré »
    du rapport, l'extraction ne levant jamais.
    """
    garde_fous = garde_fous if garde_fous is not None else GardeFousIngestion()
    entrees = list(declarations or ())
    attendus = sum(1 for brut in entrees if _est_fichier(brut))
    if attendus != len(fichiers):
        raise SourceRefusee(
            "apercu-sans-octets",
            f"Aperçu incomplet : {attendus} source(s) de type fichier déclarée(s) "
            f"pour {len(fichiers)} fichier(s) reçu(s).",
        )

    # Le dossier jetable est créé **avant** la résolution : c'est lui qui sert de
    # racine d'ingestion, donc il doit exister quand `emplacement_ingestion`
    # canonicalise (`chemin_dans_racine`, #221).
    racine = Path(tempfile.mkdtemp(prefix="maestro-apercu-"))
    try:
        reglages = replace(settings or load_settings(), ingestion_dir=str(racine))
        sources = resoudre_sources(
            entrees, run_id=RUN_APERCU, garde_fous=garde_fous, settings=reglages
        )
        if fichiers:
            emplacement_ingestion(RUN_APERCU, settings=reglages, creer=True)
            _deposer(sources, fichiers, garde_fous=garde_fous)
        return extraire_sources(
            sources, garde_fous=garde_fous_extraction, recuperer_url=recuperer_url
        )
    finally:
        # `ignore_errors` : un aperçu qui n'arrive pas à faire son ménage ne doit
        # pas transformer une réponse rendue en panne. Ce qui resterait est dans
        # le répertoire temporaire du système, que l'OS ramasse.
        shutil.rmtree(racine, ignore_errors=True)


def _est_fichier(brut: Any) -> bool:
    """La déclaration `brut` annonce-t-elle un fichier téléversé ?

    Lu sur la déclaration **brute** et non sur la source résolue : le décompte a
    lieu avant la résolution, faute de quoi un aperçu mal apparié ne serait
    détecté qu'après avoir écrit sur le disque.
    """
    return isinstance(brut, Mapping) and str(brut.get("type") or "").strip().lower() == TYPE_FICHIER


def _deposer(
    sources: Sequence[Source],
    fichiers: Sequence[tuple[str, IO[bytes]]],
    *,
    garde_fous: GardeFousIngestion,
) -> None:
    """Écrit les octets déposés à l'emplacement que la résolution leur a donné.

    Les plafonds sont vérifiés **tranche par tranche** plutôt que sur la taille
    annoncée : celle-ci vient du navigateur, c'est-à-dire de l'extérieur. Un
    dépassement interrompt l'écriture et refuse — le fichier partiel s'en va avec
    le dossier jetable, dans le `finally` de l'appelant.
    """
    cibles = [
        (index, source)
        for index, source in enumerate(sources)
        if source.type == TYPE_FICHIER
    ]
    total = 0
    for (index, source), (_, flux) in zip(cibles, fichiers, strict=True):
        total += _ecrire(Path(source.chemin), flux, index=index, deja=total, garde_fous=garde_fous)


def _ecrire(
    destination: Path,
    flux: IO[bytes],
    *,
    index: int,
    deja: int,
    garde_fous: GardeFousIngestion,
) -> int:
    """Écrit `flux` dans `destination` sous les plafonds — rend le nombre d'octets écrits."""
    plafond_source = garde_fous.taille_max_source_octets
    plafond_total = garde_fous.taille_max_totale_octets
    ecrits = 0
    with destination.open("wb") as sortie:
        while True:
            tranche = flux.read(TAILLE_TRANCHE)
            if not tranche:
                return ecrits
            ecrits += len(tranche)
            if plafond_source is not None and ecrits > plafond_source:
                raise SourceRefusee(
                    "source-trop-volumineuse",
                    f"Source {index + 1} trop volumineuse : plus de {plafond_source} "
                    f"octets reçus, {plafond_source} au maximum.",
                    index=index,
                )
            if plafond_total is not None and deja + ecrits > plafond_total:
                raise SourceRefusee(
                    "ingestion-trop-volumineuse",
                    f"Sources trop volumineuses au total : plus de {plafond_total} "
                    f"octets cumulés à la source {index + 1}, {plafond_total} au maximum.",
                    index=index,
                )
            sortie.write(tranche)
