"""`python -m maestro.scenarios` — le point d'entrée du banc (#1148).

Une ligne, comme `maestro.controltower.purge` : la commande du ticket est
`python -m maestro.scenarios`, donc le paquet doit être exécutable tel quel. Tout
ce qui se lit et se juge vit dans `maestro.scenarios.banc`.
"""

from maestro.scenarios.banc import main

if __name__ == "__main__":
    raise SystemExit(main())
