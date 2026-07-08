"""Agents exécutants de Maestro et leur catalogue (ticket #6).

Expose la forme d'un agent (`Agent` : compétences, modèle, prompt système) et le
catalogue par défaut du POC (`DEFAULT_AGENTS`). Le routeur (`maestro.router`) s'en
sert pour l'auto-assignation ; le moteur (`maestro.engine`) pour l'exécution.

    from maestro.agents import DEFAULT_AGENTS

Le catalogue est statique au POC ; il proviendra de la base en V1 (table AGENT)
sans changer ce contrat.
"""

from __future__ import annotations

from maestro.agents.catalog import DEFAULT_AGENTS, Agent

__all__ = [
    "DEFAULT_AGENTS",
    "Agent",
]
