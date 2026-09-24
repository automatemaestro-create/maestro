"""Le banc des **scénarios de référence** — le produit fait-il ce qu'on lui demande ? (#1148)

    .venv/Scripts/python.exe -m maestro.scenarios [--scenario S1[,S3]] [--delai <s>]
                                                  [--nettoyer] [--liste]

Six scénarios joués de bout en bout contre la **vraie stack** et le **vrai
modèle**, par la porte d'entrée réelle — le fil de l'orchestrateur, proposition
puis accord —, avec un verdict, un coût, une durée et un `run_id` par scénario
(docs/40 §5). Il n'est **pas en CI** : un passage coûte du vrai modèle.

Le détail vit dans les modules, chacun sur une question :

- `banc` — le déroulé, le rejeu d'un rouge non déterministe, la ligne de commande ;
- `scenarios` — les scénarios et leurs oracles ;
- `api` — la porte d'entrée : l'API de la Control Tower, en HTTP ;
- `projets` — les projets jetables, et ce qu'on y lit après un run ;
- `juge` — l'oracle de S4, rendu par un modèle et jamais par un lexique (#746) ;
- `rapport` — `.maestro/scenarios/<horodatage>/`, en Markdown et en JSON ;
- `modele` — les objets inertes du verdict.
"""

from maestro.scenarios.banc import (
    CODE_API_MUETTE,
    CODE_ROUGE,
    CODE_USAGE,
    CODE_VERT,
    jouer,
    main,
)
from maestro.scenarios.modele import (
    VERDICT_ROUGE,
    VERDICT_VERT,
    Rapport,
    Resultat,
)
from maestro.scenarios.scenarios import SCENARIOS, Contexte, Scenario

__all__ = [
    "CODE_API_MUETTE",
    "CODE_ROUGE",
    "CODE_USAGE",
    "CODE_VERT",
    "SCENARIOS",
    "VERDICT_ROUGE",
    "VERDICT_VERT",
    "Contexte",
    "Rapport",
    "Resultat",
    "Scenario",
    "jouer",
    "main",
]
