"""Télémétrie de Maestro : journalisation des étapes et des coûts (ticket #8).

Six briques, assemblées par la boucle d'orchestration (`maestro.engine`) :

- `StepUsage` + `collect_usage`/`report_usage` : la mesure d'usage d'un appel
  modèle (tokens, coût, durée, outils) et son canal de remontée par contexte —
  les fournisseurs signalent, l'appelant récolte, sans changer la signature de
  `ModelProvider` ;
- `redact_secrets` : rédaction des secrets avant toute journalisation —
  `enregistre_secret` (#109) y ajoute à chaud les secrets réellement servis
  (coffre par agent, références `${VAR}` résolues) ;
- `RunJournal`/`StepRecord` : le journal structuré d'une exécution — une ligne
  JSON par étape sur le logger `maestro.trace` — base préparant l'intégration
  future de Langfuse ;
- `RunCost`/`TaskCost` : la comptabilité par tâche d'une exécution (#55) — le
  journal réorganisé en grand livre : une entrée par tâche (tokens, coût,
  durée), l'agrégat du run, aucun prix évalué ici (tarification côté
  `ModelProvider`, #32) ;
- `PlafondDepense` : le plafond de dépense (#9) adossé à ce grand livre (#56) —
  consulté par le collecteur à chaque mesure, il relit la comptabilité de
  l'exécution (source unique du coût, aucun compteur parallèle) et lève
  `PlafondDepenseDepasse` au dépassement ;
- `activer_export_langfuse` : l'exporteur Langfuse du journal (#81) — posé en
  handler sur le logger `maestro.trace`, il mue chaque exécution en trace
  Langfuse (une observation par étape, tokens et coûts #55 au format natif) ;
  purement configuratif, no-op sans clés Langfuse dans l'environnement.
  `evaluer_run_langfuse` (#80) complète l'export en fin de run : les scores
  d'évaluation dérivés du journal (réussite globale, taux de tâches réussies)
  partent sur la trace de l'exécution — même bascule, même résilience.
"""

from __future__ import annotations

from maestro.telemetry.costs import (
    ETAPE_PLANIFICATION,
    PlafondDepense,
    PlafondDepenseDepasse,
    RunCost,
    TaskCost,
)
from maestro.telemetry.journal import LOGGER_NAME, RunJournal, StepRecord
from maestro.telemetry.langfuse import (
    activer_export_langfuse,
    evaluer_run_langfuse,
    scores_depuis_journal,
)
from maestro.telemetry.redact import MARQUEUR_SECRET, enregistre_secret, redact_secrets
from maestro.telemetry.usage import (
    StepUsage,
    UsageCollector,
    collect_usage,
    report_usage,
)

__all__ = [
    "ETAPE_PLANIFICATION",
    "LOGGER_NAME",
    "MARQUEUR_SECRET",
    "PlafondDepense",
    "PlafondDepenseDepasse",
    "RunCost",
    "RunJournal",
    "StepRecord",
    "StepUsage",
    "TaskCost",
    "UsageCollector",
    "activer_export_langfuse",
    "collect_usage",
    "enregistre_secret",
    "evaluer_run_langfuse",
    "redact_secrets",
    "report_usage",
    "scores_depuis_journal",
]
