"""Isolation d'exécution de Maestro — l'espace de travail jetable par tâche (ticket #4).

Expose la frontière d'isolation que voient les agents exécutants :

    from maestro.sandbox import isolated_workspace

    with isolated_workspace() as ws:
        ...  # l'agent produit ses fichiers dans ws.path
        livrables = ws.produced_files()

Par défaut, l'isolation est *au niveau du système de fichiers* (un répertoire
temporaire dédié). Le **mode isolé** opt-in (#108, `MAESTRO_ISOLATION=conteneur`)
la renforce d'un conteneur Docker durci par exécution outillée — sans changer ce
contrat : `IsolationConfig` (`maestro.sandbox.container`) porte les réglages, le
fournisseur fait le branchement. Voir docs/17-isolation-execution.md.
"""

from __future__ import annotations

from maestro.sandbox.container import IsolationConfig
from maestro.sandbox.workspace import ProducedFile, Workspace, isolated_workspace

__all__ = [
    "IsolationConfig",
    "ProducedFile",
    "Workspace",
    "isolated_workspace",
]
