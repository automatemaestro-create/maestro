"""Agents exécutants de Maestro et leur catalogue (ticket #6).

Expose la forme d'un agent (`Agent` : compétences, modèle, prompt système) et le
catalogue par défaut du POC (`DEFAULT_AGENTS`). Le routeur (`maestro.router`) s'en
sert pour l'auto-assignation ; le moteur (`maestro.engine`) pour l'exécution.

    from maestro.agents import DEFAULT_AGENTS

Le catalogue est statique au POC ; il proviendra de la base en V1 (table AGENT)
sans changer ce contrat.

Au-delà de l'identité (`Agent`), certains rôles disposent d'un **runtime** outillé —
un sous-agent du SDK qui exécute une tâche de bout en bout dans un espace isolé et
renvoie un livrable exploitable : le Développeur (`DeveloperAgent`, ticket #4) et la
Base de données (`DatabaseAgent`, ticket #5).
"""

from __future__ import annotations

from maestro.agents.catalog import DEFAULT_AGENTS, Agent
from maestro.agents.database import (
    DATABASE_TOOLS,
    DatabaseAgent,
    DatabaseOutcome,
)
from maestro.agents.developer import (
    DEVELOPER_TOOLS,
    DeveloperAgent,
    DeveloperOutcome,
)

__all__ = [
    "DATABASE_TOOLS",
    "DEFAULT_AGENTS",
    "DEVELOPER_TOOLS",
    "Agent",
    "DatabaseAgent",
    "DatabaseOutcome",
    "DeveloperAgent",
    "DeveloperOutcome",
]
