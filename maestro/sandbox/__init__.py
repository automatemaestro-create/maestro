"""Isolation d'exécution de Maestro — l'espace de travail d'une tâche (ticket #4).

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

Depuis #224 cet espace peut être **dérivé d'un projet** de l'utilisateur au lieu
d'être vide (EF-36, docs/24 §2.4) — worktree Git sur une branche `maestro/<tâche>`
si le projet est versionné, copie du périmètre sinon :

    from maestro.sandbox import espace_de_travail

    with espace_de_travail(projet, tache_id="t1") as ws:
        ...  # l'agent voit le projet, sans jamais écrire dans sa racine

`projet=None` retombe sur `isolated_workspace` : une tâche sans `projet_id` garde
le répertoire jetable d'avant, au caractère près.
"""

from __future__ import annotations

from maestro.sandbox.container import IsolationConfig
from maestro.sandbox.projet import (
    PREFIXE_BRANCHE,
    EspaceProjetIndisponible,
    branche_de_tache,
    espace_de_travail,
)
from maestro.sandbox.workspace import ProducedFile, Workspace, isolated_workspace

__all__ = [
    "PREFIXE_BRANCHE",
    "EspaceProjetIndisponible",
    "IsolationConfig",
    "ProducedFile",
    "Workspace",
    "branche_de_tache",
    "espace_de_travail",
    "isolated_workspace",
]
