"""La chaîne d'ingestion en un geste : déclarer, résoudre, rattacher (ticket #482).

Trois modules savaient chacun une moitié du chemin — `televersement` complète un
renvoi `{"type": "fichier", "id": …}`, `resolution` canonicalise et refuse,
`televersement` encore copie les octets là où la résolution vient de dire qu'ils
vont — mais **personne ne portait l'enchaînement**. Il vivait en privé dans
`ServiceExecutions._composer`, ce qui allait tant qu'un lancement était le seul
geste capable de porter des sources.

Le fil de chat en est un second (#482, lot 1 de #481) : un message peut
désormais porter des fichiers, un dossier, une adresse. Le critère est explicite
— « résolues par la chaîne d'ingestion **existante, jamais par une seconde** » —
et c'est cette phrase que ce module rend vraie. Recopier les trois temps dans
`ServiceChat` aurait donné deux chaînes qui se ressemblent, et c'est **celle des
deux qui oublie un plafond** qui aurait fait la faille : la leçon est déjà
écrite dans `nom_de_fichier` (#317), assainisseur unique de la résolution et du
téléversement précisément parce que « deux assainisseurs écrits séparément
divergeraient ».

Un module à part plutôt qu'une fonction de plus dans `resolution` : la
composition a besoin du **dépôt** de téléversements, or `televersement` importe
déjà `resolution`. L'y mettre inverserait la dépendance ; ici, les deux sont en
amont et rien ne boucle.

Ce module n'invente **aucun garde-fou** et n'en assouplit aucun : il appelle,
dans l'ordre, ce que #315 et #317 ont écrit. Les refus qui en sortent sont les
leurs, avec leur motif et leur index.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from maestro.engine.guardrails import GardeFousIngestion
from maestro.sources.modele import Source
from maestro.sources.resolution import resoudre_sources
from maestro.sources.televersement import DepotTeleversements, declarer_televersements


def composer_sources(
    bruts: Sequence[Mapping[str, Any] | Source] | None,
    *,
    cle: str,
    depot: DepotTeleversements,
    garde_fous: GardeFousIngestion | None = None,
    racine_projet: Path | str | None = None,
) -> tuple[Source, ...]:
    """La matière de `bruts`, résolue et ses octets rattachés — `()` s'il n'y en a pas.

    Trois temps, et leur ordre est le contenu de la fonction (il vient de
    `ServiceExecutions._composer`, #317, dont elle est l'extraction) :

    1. **Compléter** — un `{"type": "fichier", "id": …}` ne dit ni nom ni taille ;
       le dépôt les lui donne, et ce sont ceux des **octets reçus**, pas ceux
       qu'un client annonce. Sans quoi le plafond par source se contournerait en
       déclarant douze octets.
    2. **Résoudre** (#315) — canonicalisation, racines interdites, plafonds. C'est
       le seul endroit qui refuse, et il refuse **avant** toute écriture : rien de
       ce qui suit ne doit laisser un appelant à moitié inscrit.
    3. **Rattacher** — la résolution vient de calculer *où* la matière doit être ;
       les octets y sont copiés. Cet ordre est obligé : l'emplacement d'ingestion
       dépend de la `cle`, qui n'existe pas au téléversement.

    `cle` nomme l'**emplacement d'ingestion** — un dossier par acte, et non un
    dossier commun : c'est ce qui permet de dire « cette matière appartient à ce
    run / à ce message », de la retrouver dans une trace et de la ramasser sans
    toucher à celle du voisin. Un `run_id` pour un lancement, l'identifiant du
    message pour un fil ; `resolution.ID_RUN` en borne la forme, la fonction n'en
    connaît pas la provenance.

    Une source déclarée **sans** `id` traverse les trois temps sans octets : elle
    ressortira `ignore` / `source-absente` au rapport de lecture, ce qui est
    exactement ce qu'on veut qu'elle dise.

    Lève `SourceRefusee` (donc `ValueError`) — les routes en font déjà un 422
    motivé, index compris.
    """
    declarees, identifiants = declarer_televersements(bruts, depot=depot)
    matiere = resoudre_sources(
        declarees, run_id=cle, garde_fous=garde_fous, racine_projet=racine_projet
    )
    for source, identifiant in zip(matiere, identifiants, strict=False):
        if identifiant:
            depot.rattacher(identifiant, Path(source.chemin))
    return matiere
