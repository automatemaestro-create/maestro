"""Démo en ligne de commande de l'agent Développeur (ticket #4).

`maestro-dev "<tâche>"` exécute une tâche de développement **de bout en bout** dans un
espace de travail isolé via `DeveloperAgent.default` (vrai fournisseur Claude), puis
imprime le compte-rendu et la liste des fichiers produits. `--json` imprime le résultat
structuré ; `--keep` conserve l'espace de travail et affiche son chemin (pour inspecter
le livrable). Fine couche autour de `DeveloperAgent`, pour *exercer* le flux contre le
vrai SDK.

Code de sortie : 0 si au moins un fichier a été produit, 1 sinon (ou en cas d'erreur de
configuration / de capacité fournisseur indisponible).
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence

from maestro.agents.developer import DeveloperAgent
from maestro.config import ConfigError
from maestro.providers.base import UnsupportedCapability


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    usage = 'Usage : maestro-dev [--json] [--keep] "<tâche de développement>"'
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
        agent = DeveloperAgent.default()
        outcome = asyncio.run(agent.execute(description, keep_workspace=keep))
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
