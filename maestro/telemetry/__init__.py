"""Télémétrie de Maestro : journalisation des étapes et des coûts (ticket #8).

Quatre briques, assemblées par la boucle d'orchestration (`maestro.engine`) :

- `StepUsage` + `collect_usage`/`report_usage` : la mesure d'usage d'un appel
  modèle (tokens, coût, durée, outils) et son canal de remontée par contexte —
  les fournisseurs signalent, l'appelant récolte, sans changer la signature de
  `ModelProvider` ;
- `redact_secrets` : rédaction des secrets avant toute journalisation ;
- `RunJournal`/`StepRecord` : le journal structuré d'une exécution — une ligne
  JSON par étape sur le logger `maestro.trace` — base préparant l'intégration
  future de Langfuse ;
- `RunCost`/`TaskCost` : la comptabilité par tâche d'une exécution (#55) — le
  journal réorganisé en grand livre : une entrée par tâche (tokens, coût,
  durée), l'agrégat du run, aucun prix évalué ici (tarification côté
  `ModelProvider`, #32).
"""

from __future__ import annotations

from maestro.telemetry.costs import ETAPE_PLANIFICATION, RunCost, TaskCost
from maestro.telemetry.journal import LOGGER_NAME, RunJournal, StepRecord
from maestro.telemetry.redact import MARQUEUR_SECRET, redact_secrets
from maestro.telemetry.usage import (
    PlafondDepenseDepasse,
    StepUsage,
    UsageCollector,
    collect_usage,
    report_usage,
)

__all__ = [
    "ETAPE_PLANIFICATION",
    "LOGGER_NAME",
    "MARQUEUR_SECRET",
    "PlafondDepenseDepasse",
    "RunCost",
    "RunJournal",
    "StepRecord",
    "StepUsage",
    "TaskCost",
    "UsageCollector",
    "collect_usage",
    "redact_secrets",
    "report_usage",
]
