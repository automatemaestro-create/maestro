"""L'appartenance d'un travail à un projet — `projet_id` (ticket #222, EF-35).

Le lot 1 (#221) a posé l'entité Projet ; il manquait le fil qui relie un
**travail** à un projet. `projet_id` est ce fil : porté par la tâche
(`Task`, `StepRecord`, `EtatTache`) et par l'exécution (`EtatExecution`), il
traverse modèle → journal → événements → projection → vues, exactement comme la
référence de ticket externe de #187 (`maestro.references`) — une **donnée
transportée**, jamais une intégration.

Le champ est **optionnel** : `None` est le comportement d'avant ce lot. Un run
sans projet se déroule à l'identique, un consommateur qui ignore la clé n'est
pas cassé, et une vue qui filtre par projet ne voit tout simplement pas les
travaux sans projet. C'est ce qui rend ce lot mergeable seul, avant que l'API
(#223), l'espace de travail (#224) et l'écran Projets (#225) ne l'alimentent.

Ce module est une **feuille** — il n'importe rien de `maestro` — pour la même
raison que `maestro.references` : le chemin d'un `projet_id` traverse des
couches qui ne peuvent pas dépendre les unes des autres (le journal
`maestro.telemetry` le transporte, les événements `maestro.controltower.events`
le diffusent, et `controltower` importe déjà `telemetry`). C'est pourquoi
`ID_PROJET` est **défini ici** et seulement ré-exporté par
`maestro.projets.modele`, où il vivait depuis #221 : le dépôt de projets
(`maestro.projets.store`) et le journal partagent ainsi le même contrat
d'identifiant sans que l'un ait à importer l'autre.
"""

from __future__ import annotations

import re
from typing import Any

#: Identifiant de projet admissible comme nom de fichier (#221) — slug sûr, sans
#: séparateur ni point, ce qui interdit toute traversée de chemin depuis un
#: identifiant venu de l'API ou du flux. `\Z` et non `$` : `$` accepte un retour
#: à la ligne final, et donc un nom de fichier qui en porte un.
#: Source unique du motif : `maestro.projets.modele` le ré-exporte (cf. docstring).
ID_PROJET = re.compile(r"^[a-z0-9][a-z0-9_-]*\Z")

#: Longueur au-delà de laquelle un identifiant n'est plus un identifiant : il
#: trahit un collage accidentel. Même borne que l'identifiant de ticket externe
#: (`maestro.references.LONGUEUR_MAX_ID`) et que les slugs du schéma partagé.
LONGUEUR_MAX_ID = 64


def projet_id_valide(valeur: Any) -> str | None:
    """Le `projet_id` **normalisé** de ce qui arrive de l'extérieur — None s'il n'en est pas.

    Le pendant de `ReferenceTicket.depuis` (#187) pour l'appartenance : tout ce
    qui entre — corps de requête, ligne de journal relue, événement JSON d'un
    autre process — passe par ici avant d'être porté. Rend None sur une valeur
    absente, vide, non textuelle, trop longue, ou qui ne respecte pas
    `ID_PROJET` : un identifiant douteux vaut « aucun projet ».

    Ne lève jamais et ne refuse jamais le travail : l'appartenance à un projet
    est une donnée de rattachement, pas une donnée dont dépend l'exécution —
    faire échouer un run parce qu'un identifiant est mal formé coûterait plus
    que de le dégrader. Le refus **à la saisie**, lui, appartient à l'API des
    projets (#223), qui sait dire *pourquoi*.

    C'est aussi un garde-fou : l'identifiant sert de nom de fichier au dépôt de
    projets (#221), donc rien de non conforme ne doit pouvoir voyager jusque-là.
    """
    if not isinstance(valeur, str):
        return None
    identifiant = valeur.strip()
    if not identifiant or len(identifiant) > LONGUEUR_MAX_ID:
        return None
    return identifiant if ID_PROJET.match(identifiant) else None
