"""Moteur d'orchestration de Maestro — la boucle du POC (ticket #6).

Relie l'orchestrateur (#3), le routeur (#6) et les agents (#6) en une boucle qui
transforme un objectif en résultats agrégés :

    from maestro.engine import OrchestrationEngine

    engine = OrchestrationEngine.default()          # fournisseur Claude (POC)
    report = await engine.run("Créer une API de gestion de tâches")
    print(report.synthese())                        # agrégat Markdown

Le moteur ne dépend que de la couche d'abstraction fournisseur (`ModelProvider`) :
il reste agnostique du fournisseur, exactement comme l'orchestrateur.

Chaque tâche s'exécute sous **garde-fous** (#9, `maestro.engine.guardrails`) :
plafond de dépense, time-out, et validation humaine des actions sensibles —
configurables via `Guardrails` injecté au moteur.

Les échecs **transitoires** (aléa fournisseur : erreur immédiate, crash du
sous-processus SDK) sont **relancés automatiquement** (#91, ENF-06,
`maestro.engine.retry`) : politique `PolitiqueRelance` (tentatives, backoff),
armée par défaut sur `OrchestrationEngine.default()`. Les échecs non
transitoires (time-out ferme, plafonds, refus de validation) ne le sont jamais.

L'exécution d'une tâche passe par la frontière injectable `TaskExecutor` (#41) :
`LocalExecutor` en process par défaut, ou `maestro.queue.CeleryExecutor` pour
distribuer les tâches à des workers via la file Celery + Redis.

Les dépendances du plan forment un **DAG** validé en amont (#43, cycles rejetés à
`validate_plan`) : une tâche n'atteint l'exécuteur que lorsque toutes ses
dépendances sont terminées, et l'échec d'une dépendance **bloque** son aval
(statut `STATUT_BLOQUEE`, aucune exécution orpheline).

Avant de décomposer, la boucle sait **s'arrêter sur le brief** (#320, décision D5,
`maestro.engine.brief`) : `run(..., mode_brief=...)` décompose l'objectif brut
(`sans`, le défaut), le brief rédigé sans attendre personne (`auto`) ou le brief
**approuvé par un humain** (`humain`, via un `ArbitreBrief` injecté au moteur) —
et dans ce dernier cas aucune tâche n'est créée tant que rien n'est tranché.
"""

from __future__ import annotations

from maestro.engine.brief import (
    MODE_BRIEF_AUTO,
    MODE_BRIEF_HUMAIN,
    MODE_BRIEF_SANS,
    MODES_BRIEF,
    ArbitreBrief,
    BriefRefuse,
    DecisionBrief,
    DemandeBrief,
)
from maestro.engine.executor import (
    STATUT_BLOQUEE,
    STATUT_ECHEC,
    STATUT_EN_COURS,
    STATUT_TERMINEE,
    LocalExecutor,
    TaskExecutor,
    TaskResult,
)
from maestro.engine.guardrails import (
    MOTS_SENSIBLES,
    DemandeValidation,
    Guardrails,
)
from maestro.engine.loop import (
    OrchestrationEngine,
    RunReport,
)
from maestro.engine.retry import PolitiqueRelance

__all__ = [
    "ArbitreBrief",
    "BriefRefuse",
    "DecisionBrief",
    "DemandeBrief",
    "DemandeValidation",
    "Guardrails",
    "LocalExecutor",
    "MODES_BRIEF",
    "MODE_BRIEF_AUTO",
    "MODE_BRIEF_HUMAIN",
    "MODE_BRIEF_SANS",
    "MOTS_SENSIBLES",
    "OrchestrationEngine",
    "PolitiqueRelance",
    "RunReport",
    "STATUT_BLOQUEE",
    "STATUT_ECHEC",
    "STATUT_EN_COURS",
    "STATUT_TERMINEE",
    "TaskExecutor",
    "TaskResult",
]
