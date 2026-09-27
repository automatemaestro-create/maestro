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
si le projet est versionné, et depuis #839 **la racine elle-même** sinon
(`maestro.sandbox.en_place`) :

    from maestro.sandbox import espace_de_travail

    with espace_de_travail(projet, tache_id="t1") as ws:
        ...  # l'agent voit le projet — dans sa racine s'il n'est pas versionné

`projet=None` retombe sur `isolated_workspace` : une tâche sans `projet_id` garde
le répertoire jetable d'avant, au caractère près.

Ce répertoire jetable **ne laisse plus rien derrière lui** depuis #992
(`maestro.sandbox.ramassage`) : il naît sous une racine que Python et le Bash de
l'agent résolvent au même endroit, il porte le pid de son process dans son nom,
il est supprimé pour de bon (objets Git en lecture seule compris), et ce qu'un
process tué a laissé est **ramassé** au démarrage de l'hôte suivant — jamais
l'espace d'une tâche vivante, jamais un worktree porteur de travail non commité.

    from maestro.sandbox import ramasser

    ramasser()  # best-effort, muet quand il n'y a rien à retirer

Ce que la **session** d'un agent lance ne lui survit pas non plus depuis #1279
(`maestro.sandbox.confinement`) : hors mode isolé, le fournisseur lance son CLI par
le lanceur `maestro-confinement`, qui le range dans un arbre de processus
(`maestro.sandbox.arbre` — Job Object, groupe) et l'arrête tout entier à la clôture
de la tâche, en nommant ce qui a résisté. Voir docs/17 §6.
"""

from __future__ import annotations

from maestro.sandbox.container import IsolationConfig
from maestro.sandbox.en_place import (
    DOSSIER_ATELIER,
    EspaceEnPlace,
    FrontiereEcriture,
    chemin_atelier,
    frontiere_de,
)
from maestro.sandbox.projet import (
    PREFIXE_BRANCHE,
    EspaceProjetIndisponible,
    branche_de_tache,
    espace_de_travail,
)
from maestro.sandbox.ramassage import Ramassage, racine_des_espaces, ramasser
from maestro.sandbox.workspace import ProducedFile, Workspace, isolated_workspace

__all__ = [
    "DOSSIER_ATELIER",
    "PREFIXE_BRANCHE",
    "EspaceEnPlace",
    "EspaceProjetIndisponible",
    "FrontiereEcriture",
    "IsolationConfig",
    "ProducedFile",
    "Ramassage",
    "Workspace",
    "branche_de_tache",
    "chemin_atelier",
    "espace_de_travail",
    "frontiere_de",
    "isolated_workspace",
    "racine_des_espaces",
    "ramasser",
]
