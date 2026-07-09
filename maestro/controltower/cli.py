"""Point d'entrée du backend Control Tower : `maestro-api` (ticket #46).

Fine couche autour d'uvicorn : sert l'app FastAPI de production
(`create_default_app`, bus Redis Pub/Sub configuré via `REDIS_URL`). Redis
lancé (infra/docker-compose.yml) est requis pour le flux temps réel ; côté
moteur, `maestro-run --publier` (ou un worker #41) alimente le canal.

Équivalent direct : `uvicorn --factory maestro.controltower.app:create_default_app`.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

_USAGE = "Usage : maestro-api [--hote <adresse>] [--port <port>]"

#: Écoute par défaut : locale (l'API est un backend de développement au POC).
HOTE_DEFAUT = "127.0.0.1"
PORT_DEFAUT = 8000


def main(argv: Sequence[str] | None = None) -> int:
    """Démarre le serveur de l'API Control Tower (bloquant jusqu'à l'arrêt)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-h", "--help"}:
        print(_USAGE, file=sys.stderr)
        return 0

    hote = HOTE_DEFAUT
    port = PORT_DEFAUT
    while args:
        flag = args.pop(0)
        if flag == "--hote" and args:
            hote = args.pop(0)
        elif flag == "--port" and args:
            brut = args.pop(0)
            try:
                port = int(brut)
            except ValueError:
                print(f"--port attend un entier (reçu : {brut!r}).", file=sys.stderr)
                return 2
        else:
            print(_USAGE, file=sys.stderr)
            return 2

    # Import local : le CLI est le seul module à dépendre du serveur uvicorn.
    import uvicorn

    uvicorn.run(
        "maestro.controltower.app:create_default_app",
        factory=True,
        host=hote,
        port=port,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
