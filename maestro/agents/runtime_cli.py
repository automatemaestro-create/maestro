"""Démo en ligne de commande des rôles outillés (tickets #4, #5, factorisée par #35).

`maestro-dev "<tâche>"` et `maestro-bdd "<tâche>"` exécutent une tâche **de bout en
bout** dans un espace de travail isolé via le runtime générique (`AgentRuntime.default`,
vrai fournisseur Claude) paramétré par le profil du rôle, puis impriment le compte-rendu
et la liste des fichiers produits. `--json` imprime le résultat structuré ; `--keep`
conserve l'espace de travail et affiche son chemin (pour inspecter le livrable). Fine
couche autour de `AgentRuntime`, pour *exercer* le flux contre le vrai SDK.

Code de sortie : 0 si au moins un fichier a été produit, 1 sinon (ou en cas d'erreur de
configuration / de capacité fournisseur indisponible).
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence

from maestro.agents.database import DATABASE_PROFILE
from maestro.agents.developer import DEVELOPER_PROFILE
from maestro.agents.runtime import AgentRuntime, RoleProfile
from maestro.config import ConfigError
from maestro.providers.base import UnsupportedCapability


def _main(profile: RoleProfile, *, prog: str, tache: str, argv: Sequence[str] | None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    usage = f'Usage : {prog} [--json] [--keep] "<{tache}>"'
    if args and args[0] in {"-h", "--help"}:
        print(usage, file=sys.stderr)
        return 0

    as_json = False
    keep = False
    while args and args[0] in {"--json", "--keep"}:
        if args[0] == "--json":
            as_json = True
        else:
            keep = True
        args = args[1:]

    description = " ".join(args).strip()
    if not description:
        print(usage, file=sys.stderr)
        return 2

    try:
        runtime = AgentRuntime.default(profile)
        outcome = asyncio.run(runtime.execute(description, keep_workspace=keep))
    except ConfigError as exc:
        print(f"Configuration : {exc}", file=sys.stderr)
        return 1
    except UnsupportedCapability as exc:
        print(f"Capacité indisponible : {exc}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(outcome.synthese())
        if keep:
            print(f"Espace de travail conservé : {outcome.workspace}", file=sys.stderr)
    return 0 if outcome.a_produit else 1


def main_dev(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée de `maestro-dev` : le rôle Développeur."""
    return _main(DEVELOPER_PROFILE, prog="maestro-dev", tache="tâche de développement", argv=argv)


def main_bdd(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée de `maestro-bdd` : le rôle Base de données."""
    return _main(
        DATABASE_PROFILE, prog="maestro-bdd", tache="tâche de base de données", argv=argv
    )
