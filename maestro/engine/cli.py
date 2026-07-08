"""Démo en ligne de commande de la boucle d'orchestration (ticket #6).

`maestro-run "<objectif>"` déroule la boucle complète (objectif → tâches → agents →
agrégat) et imprime la **synthèse** Markdown ; `--json` imprime plutôt le rapport
structuré. Fine couche autour de `OrchestrationEngine.default` : elle sert à
*exercer* le flux de bout en bout contre le vrai fournisseur Claude.

Code de sortie : 0 si toutes les tâches réussissent, 1 si au moins une échoue (ou
en cas d'erreur de configuration / planification).
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence

from maestro.config import ConfigError
from maestro.engine.loop import OrchestrationEngine
from maestro.orchestrator.errors import OrchestratorError


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    usage = 'Usage : maestro-run [--json] "<objectif en langage naturel>"'
    if args and args[0] in {"-h", "--help"}:
        print(usage, file=sys.stderr)
        return 0

    as_json = False
    if args and args[0] == "--json":
        as_json = True
        args = args[1:]

    objective = " ".join(args).strip()
    if not objective:
        print(usage, file=sys.stderr)
        return 2

    try:
        engine = OrchestrationEngine.default()
        report = asyncio.run(engine.run(objective))
    except ConfigError as exc:
        print(f"Configuration : {exc}", file=sys.stderr)
        return 1
    except OrchestratorError as exc:
        print(f"Orchestration : {exc}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.synthese())
    return 0 if not report.echouees else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
