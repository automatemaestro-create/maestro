"""Les gestes de fichiers que plusieurs modules doivent orthographier pareil.

Un seul pour l'instant, et il aura coûté deux tickets : **supprimer un arbre pour
de bon**, objets Git en lecture seule compris.

`shutil.rmtree(path, ignore_errors=True)` ne suffit pas, et le défaut ne se voit
que sous Windows : Git écrit les objets de `.git/objects/` **en lecture seule**,
et `os.remove` y refuse un fichier sans droit d'écriture là où POSIX ne regarde
que le droit sur le dossier qui le contient. `ignore_errors=True` avale le refus,
si bien que l'arbre survit **amputé** — c'est le « seul enfant survivant est
`.git` » relevé le 2026-08-28 sur #568, et c'est la coquille que #707 avait déjà
rencontrée sur un dépôt qu'un `pre-commit` venait de faire échouer.

Le remède est le même des deux côtés, d'où ce module : rendre le droit d'écriture
à **chaque fichier** avant de supprimer, puis laisser `rmtree` finir. Il vit ici
et non dans l'un des deux appelants parce qu'ils ne peuvent pas s'importer l'un
l'autre — `maestro.sandbox` dépend déjà de `maestro.projets` — et parce qu'un
geste recopié des deux côtés d'une frontière est ce que #830 a vu diverger.

⚠ **Les fichiers, jamais les dossiers.** `stat.S_IWRITE` vaut `0o200` : posé sur
un dossier sous POSIX, il lui retirerait le droit de traversée dont la
suppression a précisément besoin. Sous Windows le bit lecture seule d'un dossier
n'empêche de toute façon rien, et sous POSIX c'est le dossier **parent** qui
décide — dans les deux régimes, ne toucher qu'aux fichiers est le geste complet.

Best-effort comme avant : ce qui résiste encore est laissé, et le verdict rendu
dit seulement si le chemin a disparu — l'appelant vit le plus souvent dans un
`finally`, où lever masquerait l'exception qui a réellement condamné la tâche.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path


def retirer_arbre(chemin: Path) -> bool:
    """Supprime `chemin` et tout ce qu'il contient — lecture seule comprise (#707, #992).

    Rend `True` si plus rien ne subsiste à cette adresse (y compris quand il n'y
    avait déjà rien), `False` si quelque chose a résisté. Ne lève jamais.
    """
    for dossier, _sous_dossiers, fichiers in os.walk(chemin):
        for nom in fichiers:
            try:
                os.chmod(Path(dossier) / nom, stat.S_IWRITE)
            except OSError:
                continue
    shutil.rmtree(chemin, ignore_errors=True)
    try:
        return not chemin.exists()
    except OSError:  # pragma: no cover - chemin devenu illisible
        return False
