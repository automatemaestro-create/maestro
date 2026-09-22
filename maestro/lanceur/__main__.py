"""`python -m maestro.lanceur` — le même point d'entrée que `maestro-lanceur`.

Deux chemins parce qu'il y a deux mondes : une installation pose l'exécutable déclaré
dans `pyproject.toml`, un clone appelle le module. Un seul `main`, et aucun geste ici.
"""

from __future__ import annotations

from maestro.lanceur.cli import main

raise SystemExit(main())
