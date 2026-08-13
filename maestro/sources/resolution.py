"""Résolution des sources d'un objectif et garde-fous d'ingestion (ticket #315, EF-39).

Le pendant actif de `maestro.sources.modele` : ce qu'une source **déclarée**
devient une fois confrontée au disque, au réseau et aux plafonds du run. Trois
résolutions, une par type, et un principe commun — **un refus porte son motif**,
jamais un silence ni un `False` (même contrat que `RacineRefusee`, #221) :

- **dossier** — il passe par `valider_racine` (#221), la validation de racine
  déjà écrite : canonicalisation (`..` écrasés, liens suivis) puis confrontation
  à la liste des **racines interdites** — racine de disque, dossier utilisateur
  nu, `.ssh`/`AppData`, dossiers système, dépôt de Maestro. Un dossier de
  références est un chemin du disque de l'utilisateur : il n'y a aucune raison
  qu'il soit moins contrôlé qu'une racine de projet (EF-38), et deux garde-fous
  écrits séparément divergeraient. Il ressort **en lecture seule**, forcé : une
  référence n'est pas un projet ;
- **url** — `http(s)` et rien d'autre, en réutilisant `SCHEMAS_URL`
  (`maestro.references`, #187) plutôt qu'une seconde liste : un `file://` lirait
  le disque par une porte que personne n'a contrôlée, un `javascript:` n'est pas
  une adresse ;
- **fichier** — il n'atterrit **jamais** dans le projet de l'utilisateur, mais
  dans un **emplacement d'ingestion propre au run** (`core/ingestion/<run_id>/`,
  ou `MAESTRO_INGESTION_DIR`). La raison est la même que celle qui interdit aux
  agents d'écrire dans la racine (EF-36) : une matière téléversée est une
  **entrée non fiable** ([docs/19 §2](../../docs/19-securite-modele-de-menace.md)),
  et la mêler aux fichiers du projet la rendrait indiscernable de ce que
  l'utilisateur a écrit lui-même.

Par-dessus, les **plafonds d'ingestion** (`GardeFousIngestion`, ENF-07) : taille
par source, taille totale, nombre de sources. Ils vivent avec les autres limites
du run, dans `maestro.engine.guardrails`, et pas ici — un troisième endroit où
lire « ce que ce run s'autorise » serait un endroit de plus à oublier.

**Rien n'est lu, rien n'est écrit.** Ce lot déclare, résout et refuse ; il ne
copie aucun octet et ne crée aucun dossier tant qu'on ne le lui demande pas
(`emplacement_ingestion(..., creer=True)`, que le téléversement de #317
appellera). C'est ce qui le rend mergeable seul : un lancement sans `sources` se
comporte exactement comme avant.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from maestro.config import Settings, load_settings
from maestro.engine.guardrails import GardeFousIngestion
from maestro.projets.racine import RacineRefusee, chemin_dans_racine, valider_racine
from maestro.references import SCHEMAS_URL
from maestro.sources.modele import (
    LONGUEUR_MAX_NOM,
    TYPE_DOSSIER,
    TYPE_FICHIER,
    TYPE_URL,
    TYPES_SOURCE,
    Source,
    SourceRefusee,
)

#: Identifiant de run admissible comme **nom de dossier** d'ingestion. Même
#: raison d'être que `ID_PROJET` (#222) : le `run_id` vient d'ailleurs
#: (`uuid4().hex[:12]` aujourd'hui, une reprise de journal demain) et il sert de
#: segment de chemin — rien de non conforme ne doit pouvoir remonter d'un cran.
ID_RUN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*\Z")

#: Longueur au-delà de laquelle un `run_id` n'en est plus un (même borne que les
#: autres identifiants du projet : `maestro.appartenance.LONGUEUR_MAX_ID`).
LONGUEUR_MAX_ID_RUN = 64

#: Longueur maximale d'une URL de source. La borne pratique des navigateurs et
#: des serveurs ; au-delà, ce n'est plus une adresse qu'on a collée.
LONGUEUR_MAX_URL = 2048

#: Caractères qu'un nom de fichier téléversé ne peut pas porter. Les séparateurs
#: y sont (un nom n'est pas un chemin), et avec eux les interdits de Windows —
#: dont `:`, qui ouvrirait un **flux de données alterné** NTFS
#: (`note.md:cache`), c'est-à-dire un contenu invisible d'un listing.
_CARACTERES_INTERDITS_NOM = frozenset('/\\<>:"|?*')


def racine_ingestion(settings: Settings | None = None) -> Path:
    """La racine du stockage d'ingestion : `MAESTRO_INGESTION_DIR`, sinon `core/ingestion/`.

    Même patron que les autres dépôts du POC (`maestro.projets.store`,
    `maestro.agents.store`) : un dossier du dépôt par défaut, déplaçable par
    l'environnement pour qui range ses données ailleurs. Rien n'est créé ici —
    la racine peut ne pas exister tant qu'aucun fichier n'a été téléversé.
    """
    settings = settings or load_settings()
    if settings.ingestion_dir:
        return Path(settings.ingestion_dir)
    return Path(__file__).resolve().parents[2] / "core" / "ingestion"


def emplacement_ingestion(
    run_id: str,
    *,
    settings: Settings | None = None,
    racine_projet: Path | str | None = None,
    creer: bool = False,
) -> Path:
    """L'emplacement d'ingestion **propre au run** `run_id` — un dossier par run.

    Un dossier par run et non un dossier commun : c'est ce qui permet de dire
    « cette matière appartient à cette exécution », de la retrouver dans une
    trace et de la ramasser sans toucher à celle d'un run voisin.

    `racine_projet` fait tenir la promesse du critère — un fichier téléversé est
    stocké **hors de la racine du projet**. Le contrôle est actif : il ne suffit
    pas que le défaut soit hors projet, encore faut-il qu'un `MAESTRO_INGESTION_DIR`
    posé à l'intérieur d'un projet soit refusé plutôt qu'obéi.

    `creer=True` crée le dossier (ce que fera le téléversement, #317) ; par
    défaut la fonction ne fait que **calculer**, ce lot n'écrivant rien.
    """
    identifiant = str(run_id or "").strip()
    if not identifiant or len(identifiant) > LONGUEUR_MAX_ID_RUN or not ID_RUN.match(identifiant):
        raise SourceRefusee(
            "run-invalide",
            f"Identifiant de run inutilisable comme dossier d'ingestion : {run_id!r}.",
        )
    racine = racine_ingestion(settings)
    # `chemin_dans_racine` (#221) plutôt qu'un `racine / identifiant` : le motif
    # ci-dessus borne déjà l'identifiant, mais la seconde moitié d'EF-38 est
    # écrite une fois pour toutes et se relit — un jour où le motif changerait,
    # c'est elle qui tiendrait encore.
    emplacement = chemin_dans_racine(racine, identifiant)
    if racine_projet is not None:
        _refuser_dans_le_projet(emplacement, racine_projet)
    if creer:
        emplacement.mkdir(parents=True, exist_ok=True)
    return emplacement


def resoudre_sources(
    bruts: Any,
    *,
    run_id: str,
    garde_fous: GardeFousIngestion | None = None,
    racine_projet: Path | str | None = None,
    settings: Settings | None = None,
) -> tuple[Source, ...]:
    """Les sources de `bruts`, **résolues et bornées** — `()` s'il n'y en a aucune.

    L'ordre est celui de la saisie : une liste de pièces jointes se relit dans
    l'ordre où elle a été composée. Lève `SourceRefusee` — donc `ValueError`, ce
    dont la route de lancement fait déjà un 422 — à la **première** source
    fautive, en la situant par son index : refuser en bloc à la fin obligerait à
    tout relire pour savoir quoi retirer.

    Les plafonds sont ceux de `garde_fous` (`GardeFousIngestion()` par défaut,
    donc actifs). Le nombre est vérifié **avant** toute résolution : cinq cents
    sources se refusent sans toucher au disque cinq cents fois.
    """
    if bruts is None:
        return ()
    if not isinstance(bruts, Sequence) or isinstance(bruts, str | bytes):
        raise SourceRefusee(
            "sources-illisibles",
            f"Sources illisibles : une liste était attendue, reçu {type(bruts).__name__}.",
        )
    entrees = tuple(bruts)
    if not entrees:
        return ()

    garde_fous = garde_fous if garde_fous is not None else GardeFousIngestion()
    plafond_nombre = garde_fous.nb_max_sources
    if plafond_nombre is not None and len(entrees) > plafond_nombre:
        raise SourceRefusee(
            "trop-de-sources",
            f"Trop de sources : {len(entrees)} déclarées, {plafond_nombre} au maximum.",
        )

    resolues: list[Source] = []
    total = 0
    for index, brut in enumerate(entrees):
        source = _resoudre_une(
            brut,
            index=index,
            run_id=run_id,
            garde_fous=garde_fous,
            racine_projet=racine_projet,
            settings=settings,
        )
        if source.taille is not None:
            total += source.taille
            plafond_total = garde_fous.taille_max_totale_octets
            if plafond_total is not None and total > plafond_total:
                raise SourceRefusee(
                    "ingestion-trop-volumineuse",
                    f"Sources trop volumineuses au total : {total} octets cumulés "
                    f"à la source {index + 1}, {plafond_total} au maximum.",
                    index=index,
                )
        resolues.append(source)
    return tuple(resolues)


def _resoudre_une(
    brut: Any,
    *,
    index: int,
    run_id: str,
    garde_fous: GardeFousIngestion,
    racine_projet: Path | str | None,
    settings: Settings | None,
) -> Source:
    """Une source résolue selon son type, ou `SourceRefusee` motivée."""
    data = brut.to_dict() if isinstance(brut, Source) else brut
    if not isinstance(data, Mapping):
        raise SourceRefusee(
            "source-illisible",
            f"Source {index + 1} illisible : un objet était attendu, "
            f"reçu {type(brut).__name__}.",
            index=index,
        )
    type_source = str(data.get("type") or "").strip().lower()
    if type_source not in TYPES_SOURCE:
        raise SourceRefusee(
            "type-inconnu",
            f"Source {index + 1} de type inconnu : {type_source or '(vide)'!r} — "
            f"attendu {', '.join(TYPES_SOURCE)}.",
            index=index,
        )
    nom = " ".join(str(data.get("nom") or "").split())
    if type_source == TYPE_URL:
        return _resoudre_url(data, index=index, nom=nom)
    if type_source == TYPE_DOSSIER:
        return _resoudre_dossier(data, index=index, nom=nom)
    return _resoudre_fichier(
        data,
        index=index,
        nom=nom,
        run_id=run_id,
        garde_fous=garde_fous,
        racine_projet=racine_projet,
        settings=settings,
    )


def _resoudre_url(data: Mapping[str, Any], *, index: int, nom: str) -> Source:
    """Une source `url` : `http(s)` et rien d'autre, sans espace, bornée."""
    valeur = str(data.get("valeur") or data.get("chemin") or "").strip()
    if not valeur:
        raise SourceRefusee(
            "url-absente", f"Source {index + 1} : URL manquante.", index=index
        )
    if len(valeur) > LONGUEUR_MAX_URL:
        raise SourceRefusee(
            "url-trop-longue",
            f"Source {index + 1} : URL de {len(valeur)} caractères, "
            f"{LONGUEUR_MAX_URL} au maximum.",
            index=index,
        )
    # Même filtre que les liens du contrat #183 (`maestro.references`) : un
    # schéma hors `http(s)` n'est pas une adresse à aller lire — `file://`
    # ouvrirait le disque, `javascript:` n'ouvre rien du tout.
    if not valeur.lower().startswith(SCHEMAS_URL) or any(c.isspace() for c in valeur):
        raise SourceRefusee(
            "url-non-suivable",
            f"Source {index + 1} refusée : {valeur!r} n'est pas une URL "
            f"{' ou '.join(s.rstrip(':/') for s in SCHEMAS_URL)}.",
            index=index,
        )
    return Source(type=TYPE_URL, nom=nom, valeur=valeur, lecture_seule=True)


def _resoudre_dossier(data: Mapping[str, Any], *, index: int, nom: str) -> Source:
    """Une source `dossier` : validée comme une racine de projet, en lecture seule."""
    brut = str(data.get("chemin") or data.get("valeur") or "").strip()
    if not brut:
        raise SourceRefusee(
            "chemin-absent", f"Source {index + 1} : chemin de dossier manquant.", index=index
        )
    try:
        resolu = valider_racine(brut)
    except RacineRefusee as exc:
        # Le motif de la validation de racine est **conservé** tel quel
        # (`chemin-sensible`, `racine-de-disque`, `dossier-absent`…) : c'est un
        # code stable que l'UI sait déjà traduire (docs/05 §2.7), et le
        # renommer ferait diverger deux vocabulaires pour un même refus.
        raise SourceRefusee(exc.motif, f"Source {index + 1} : {exc}", index=index) from exc
    return Source(
        type=TYPE_DOSSIER,
        nom=nom or resolu.name,
        chemin=str(resolu),
        # Forcée, jamais lue depuis la déclaration : un dossier de références en
        # écriture serait un projet déguisé, qui n'aurait passé aucun des
        # contrôles de la Phase 7 (docs/03, entité SOURCE).
        lecture_seule=True,
    )


def _resoudre_fichier(
    data: Mapping[str, Any],
    *,
    index: int,
    nom: str,
    run_id: str,
    garde_fous: GardeFousIngestion,
    racine_projet: Path | str | None,
    settings: Settings | None,
) -> Source:
    """Une source `fichier` : nom sûr, taille connue et bornée, destination hors projet."""
    nom_fichier = nom_de_fichier(nom or str(data.get("chemin") or ""), index=index)
    taille = _taille(data.get("taille"), index=index, garde_fous=garde_fous)
    emplacement = emplacement_ingestion(
        run_id, settings=settings, racine_projet=racine_projet
    )
    # Second filet : le nom vient d'être assaini, mais c'est `chemin_dans_racine`
    # (#221) qui **prouve** que la destination reste sous l'emplacement du run.
    destination = chemin_dans_racine(emplacement, nom_fichier)
    return Source(
        type=TYPE_FICHIER,
        nom=nom_fichier,
        chemin=str(destination),
        taille=taille,
        lecture_seule=True,
    )


def nom_de_fichier(brut: str, *, index: int = 0) -> str:
    """Le nom de fichier assaini d'une source téléversée, ou `SourceRefusee`.

    Un nom, pas un chemin : ni séparateur, ni `..`, ni caractère interdit. Le
    nom d'un fichier téléversé vient du navigateur de l'utilisateur, c'est-à-dire
    de l'extérieur — c'est le vecteur classique de la traversée de répertoire.

    Public depuis #317 : le téléversement (`maestro.sources.televersement`)
    assainit le nom **à la réception**, là où la résolution l'assainit à la
    déclaration. Deux assainisseurs écrits séparément divergeraient, et c'est
    celui des deux qui oublie une classe de caractères qui ferait la faille.
    """
    nom = " ".join(str(brut or "").split())
    if not nom:
        raise SourceRefusee(
            "nom-absent", f"Source {index + 1} : nom de fichier manquant.", index=index
        )
    if len(nom) > LONGUEUR_MAX_NOM:
        raise SourceRefusee(
            "nom-trop-long",
            f"Source {index + 1} : nom de {len(nom)} caractères, "
            f"{LONGUEUR_MAX_NOM} au maximum.",
            index=index,
        )
    if nom in {".", ".."} or set(nom) & _CARACTERES_INTERDITS_NOM:
        raise SourceRefusee(
            "nom-invalide",
            f"Source {index + 1} refusée : {nom!r} est un chemin, pas un nom de fichier.",
            index=index,
        )
    return nom


def _taille(brut: Any, *, index: int, garde_fous: GardeFousIngestion) -> int:
    """La taille en octets d'un fichier téléversé, plafond appliqué.

    **Exigée**, et c'est le point : une taille inconnue est une taille non
    plafonnable, donc un plafond contournable en omettant le champ. Qui téléverse
    a les octets sous la main — il sait les compter.
    """
    if brut is None:
        raise SourceRefusee(
            "taille-absente",
            f"Source {index + 1} : taille manquante — une source non mesurée "
            "ne peut pas être plafonnée.",
            index=index,
        )
    if isinstance(brut, bool) or not isinstance(brut, int) or brut < 0:
        raise SourceRefusee(
            "taille-invalide",
            f"Source {index + 1} : taille {brut!r} — un nombre d'octets positif était attendu.",
            index=index,
        )
    plafond = garde_fous.taille_max_source_octets
    if plafond is not None and brut > plafond:
        raise SourceRefusee(
            "source-trop-volumineuse",
            f"Source {index + 1} trop volumineuse : {brut} octets, {plafond} au maximum.",
            index=index,
        )
    return brut


def _refuser_dans_le_projet(emplacement: Path, racine_projet: Path | str) -> None:
    """Refuse un emplacement d'ingestion situé **dans** la racine du projet.

    Le contrôle est écrit à l'envers de son habitude : `chemin_dans_racine`
    (#221) sert d'ordinaire à vérifier qu'un chemin **reste** sous une racine, et
    c'est ici son succès qui vaut refus — un emplacement d'ingestion valide au
    sens du projet est un emplacement d'ingestion **dans** le projet. L'écrire
    ainsi évite de recopier la comparaison de chemins canonicalisés, et donc de
    la voir diverger.
    """
    try:
        chemin_dans_racine(racine_projet, emplacement)
    except RacineRefusee:
        return  # hors du projet : c'est exactement ce qu'on veut.
    raise SourceRefusee(
        "ingestion-dans-le-projet",
        f"Emplacement d'ingestion refusé : {emplacement} est dans le projet "
        f"({racine_projet}) — une matière téléversée ne se mêle pas à ses fichiers.",
    )
