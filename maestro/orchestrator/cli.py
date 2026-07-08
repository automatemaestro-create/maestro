"""Démo en ligne de commande de l'orchestrateur (ticket #3).

`maestro-orchestrate "<objectif>"` → imprime le plan de tâches en JSON. Fine
couche autour de `Orchestrator.default` : elle sert à *exercer* le flux de bout en
bout (objectif → tâches JSON validées) contre le vrai fournisseur Claude.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence

from maestro.config import ConfigError
from maestro.orchestrator.errors import OrchestratorError
from maestro.orchestrator.orchestrator import Orchestrator


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    usage = 'Usage : maestro-orchestrate "<objectif en langage naturel>"'
    if args and args[0] in {"-h", "--help"}:
        print(usage, file=sys.stderr)
        return 0
    if not args:
        print(usage, file=sys.stderr)
        return 2

    objective = " ".join(args).strip()
    if not objective:
        print("Objectif vide.", file=sys.stderr)
        return 2

    try:
        orchestrator = Orchestrator.default()
        tasks = asyncio.run(orchestrator.plan(objective))
    except ConfigError as exc:
        print(f"Configuration : {exc}", file=sys.stderr)
        return 1
    except OrchestratorError as exc:
        print(f"Orchestration : {exc}", file=sys.stderr)
        return 1

    payload = [task.to_dict() for task in tasks]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
