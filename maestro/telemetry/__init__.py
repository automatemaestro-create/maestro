"""Télémétrie de Maestro : journalisation des étapes et des coûts (ticket #8).

Trois briques, assemblées par la boucle d'orchestration (`maestro.engine`) :

- `StepUsage` + `collect_usage`/`report_usage` : la mesure d'usage d'un appel
  modèle (tokens, coût, durée, outils) et son canal de remontée par contexte —
  les fournisseurs signalent, l'appelant récolte, sans changer la signature de
  `ModelProvider` ;
- `redact_secrets` : rédaction des secrets avant toute journalisation ;
- `RunJournal`/`StepRecord` : le journal structuré d'une exécution — une ligne
  JSON par étape sur le logger `maestro.trace` — base préparant l'intégration
  future de Langfuse.
"""

from __future__ import annotations

from maestro.telemetry.journal import LOGGER_NAME, RunJournal, StepRecord
from maestro.telemetry.redact import MARQUEUR_SECRET, redact_secrets
from maestro.telemetry.usage import (
    StepUsage,
    UsageCollector,
    collect_usage,
    report_usage,
)

__all__ = [
    "LOGGER_NAME",
    "MARQUEUR_SECRET",
    "RunJournal",
    "StepRecord",
    "StepUsage",
    "UsageCollector",
    "collect_usage",
    "redact_secrets",
    "report_usage",
]
