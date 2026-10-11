"""La sonde de la carte du banc (#1463) — voir `maestro.scenarios.sonde.sitecustomize`.

Ce dossier est **posé sur `PYTHONPATH`** par `python -m maestro.scenarios.carte --preparer`,
pour qu'un module nommé `sitecustomize` s'y trouve : Python le charge au démarrage de
chaque processus. Le paquet n'existe que pour que le reste du dépôt lise ses
constantes (`maestro.scenarios.carte`) sans les recopier.
"""
