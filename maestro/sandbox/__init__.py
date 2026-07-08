"""Isolation d'exécution de Maestro — l'espace de travail jetable par tâche (ticket #4).

Expose la frontière d'isolation que voient les agents exécutants :

    from maestro.sandbox import isolated_workspace

    with isolated_workspace() as ws:
        ...  # l'agent produit ses fichiers dans ws.path
        livrables = ws.produced_files()

Au POC, l'isolation est *au niveau du système de fichiers* (un répertoire temporaire
dédié) ; elle pourra s'adosser à un conteneur Docker par tâche sans changer ce contrat
(voir `maestro.sandbox.workspace`).
"""

from __future__ import annotations

from maestro.sandbox.workspace import ProducedFile, Workspace, isolated_workspace

__all__ = [
    "ProducedFile",
    "Workspace",
    "isolated_workspace",
]
