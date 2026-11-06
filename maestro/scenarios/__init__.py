"""Maestro joué sur la **vraie stack**, par sa porte d'entrée réelle (docs/52).

Rien ici n'est en CI : un passage coûte du vrai modèle. Deux usages, et aucun parcours
figé (#1568) :

- `exercice` — l'exercice qu'une session **construit** pour ce qu'elle vient
  d'implémenter : une commande par geste, l'oracle écrit avant de jouer, un relevé sous
  `.maestro/exercices/<id>/` (`python -m maestro.scenarios.exercice`, #1569) ;
- `comparatif` — le banc comparatif : un même objectif (`objectifs/*.toml`) joué sur
  Maestro et sur un agent seul, en plusieurs passages, côte à côte
  (`python -m maestro.scenarios.comparatif`, #1512).

Ce qu'ils partagent, chacun sur une question :

- `api` — la porte d'entrée : l'API de la Control Tower, en HTTP ;
- `gestes` — les gestes d'une personne sur la vraie stack (#1569) ;
- `projets` — les ateliers jetables des passages, leur rétention, et ce qu'on lit dans
  le dépôt d'un projet après un run ;
- `redemarrage` — couper puis rallumer l'API qui sert le banc (#1408) ;
- `juge` — ce qui se juge sur une phrase, rendu par un modèle et jamais par un
  lexique (#746) ;
- `grille` et `couverture` — un livrable rejoué sur un clone propre, ligne par ligne,
  et les critères de l'objectif qu'il couvre (#1512) ;
- `etat` — l'état qu'un exercice a sauvé, que `start.sh --etat-banc` rouvre (#1164) ;
- `modele` — les objets inertes du verdict.
"""

from maestro.scenarios.gestes import Contexte
from maestro.scenarios.modele import (
    VERDICT_ROUGE,
    VERDICT_VERT,
    Rapport,
    Resultat,
)

__all__ = [
    "VERDICT_ROUGE",
    "VERDICT_VERT",
    "Contexte",
    "Rapport",
    "Resultat",
]
