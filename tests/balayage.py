"""Le parcours des fichiers du dépôt, partagé par les balayages des suites (#1546).

Ce module n'est pas une suite. Plusieurs suites balaient `scripts/`, `.claude/`, `docs/` ou `tests/`
à la recherche d'un usage interdit — un identifiant figé, un label mort, un geste nu —, et le
faisaient chacune par `Path.rglob`, qui descend partout. Trois sortes de répertoires n'y sont
pourtant pas le dépôt :

- **`.claude/worktrees/`** : depuis #847, les worktrees des tickets y sont montés — des copies
  ENTIÈRES du dépôt, chacune avec son `node_modules`. Les traverser, c'est juger le code d'une
  AUTRE branche à l'aune de celle-ci (un fichier fautif posé dans un worktree voisin faisait
  rougir six balayages de cette branche), et lire des dizaines de milliers de fichiers. Dans le
  conteneur du filet, où le dépôt est monté à travers le partage de fichiers de Docker Desktop, la
  suite témoin a tourné plus de 20 min, un cœur à 100 % et 3,1 Go (mesuré le 2026-10-20, poste à
  10 worktrees) ; en CI, sur un clone neuf, rien ne se voyait.
- **`__pycache__`** : du bytecode, jamais une source. Python l'écrit sous `<nom>.pyc.<pid>` avant
  de le renommer, et un worker xdist tué en laisse derrière lui : seul le RÉPERTOIRE le désigne
  sûrement, pas le suffixe (#345).
- **`node_modules`** : des dépendances installées, pas du code du dépôt.

Ils sont écartés AVANT d'y descendre — `os.walk`, dont on élague la liste des sous-répertoires — et
non filtrés après : un filtre posé derrière `rglob` rendrait la même liste en payant la descente
entière, et c'est la descente qui coûtait. Les SUFFIXES (images, archives…) restent l'affaire de
chaque balayage : ce qu'il ne sait pas lire dépend de ce qu'il cherche.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

#: Écartés où qu'ils soient.
ECARTES_PARTOUT = frozenset({"__pycache__", "node_modules"})
#: Écarté seulement sous `.claude/` : un autre `worktrees` peut très bien être du dépôt.
WORKTREES = ("worktrees", ".claude")


def fichiers_du_depot(*dossiers: Path) -> Iterator[Path]:
    """Les fichiers de `dossiers`, dans un ordre stable, sans ce qui n'est pas le dépôt."""
    for dossier in dossiers:
        for courant, sous, fichiers in os.walk(dossier):
            parent = Path(courant)
            sous[:] = sorted(
                nom for nom in sous
                if nom not in ECARTES_PARTOUT and (nom, parent.name) != WORKTREES
            )
            for nom in sorted(fichiers):
                yield parent / nom
