"""Point d'entrée du backend Control Tower : `maestro-api` (ticket #46).

Fine couche autour d'uvicorn : sert l'app FastAPI de production
(`create_default_app`, bus Redis Pub/Sub configuré via `REDIS_URL`). Redis
lancé (infra/docker-compose.yml) est requis pour le flux temps réel ; côté
moteur, `maestro-run --publier` (ou un worker #41) alimente le canal.

Équivalent direct : `uvicorn --factory maestro.controltower.app:create_default_app`.

`--verifier-redis` (ticket #186) ne démarre rien : il dit si ce bus répond, et
sinon le geste exact pour le lancer. C'est le **préflight** du lanceur local
(`scripts/controltower/start.sh`), qui démarre le mode réel par défaut : la
résolution de l'URL (`REDIS_URL` du `.env`, sinon l'instance locale) vit ici,
avec le reste de la configuration, plutôt que d'être réécrite en shell.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from urllib.parse import urlsplit, urlunsplit

_USAGE = "Usage : maestro-api [--hote <adresse>] [--port <port>] [--verifier-redis]"

#: Écoute par défaut : locale (l'API est un backend de développement au POC).
HOTE_DEFAUT = "127.0.0.1"
PORT_DEFAUT = 8000

#: Délai du ping de `--verifier-redis`, en secondes. Un diagnostic doit trancher
#: vite : un Redis éteint refuse la connexion tout de suite, mais un hôte
#: injoignable (URL distante, VPN coupé) ferait autrement patienter le lanceur
#: le temps du time-out TCP du système.
DELAI_PING_S = 3.0

#: Le geste exact quand le bus manque — l'instance déjà mutualisée avec la file
#: de tâches et les boîtes aux lettres (infra/docker-compose.yml).
COMMANDE_REDIS = "docker compose -f infra/docker-compose.yml up -d redis"


def endpoint_lisible(url: str) -> str:
    """L'URL Redis privée de ses identifiants — un diagnostic ne publie pas de mot de passe.

    `REDIS_URL` peut porter un `user:motdepasse@` (elle est d'ailleurs listée
    parmi les variables sensibles de `maestro.telemetry.redact`) ; le lanceur,
    lui, a besoin de dire **où** il a frappé. On ne garde donc que le point de
    connexion : schéma, hôte, port, base.
    """
    parties = urlsplit(url)
    hote = parties.hostname or ""
    if ":" in hote:  # IPv6 : les crochets font partie de l'écriture
        hote = f"[{hote}]"
    if parties.port:
        hote = f"{hote}:{parties.port}"
    return urlunsplit((parties.scheme, hote, parties.path, "", ""))


def verifier_redis() -> int:
    """Ping le bus Redis de l'API : 0 s'il répond, 1 sinon (diagnostic sur stderr)."""
    # Imports locaux : `--verifier-redis` est un mode à part, et le client Redis
    # n'est pas nécessaire pour servir l'API sur un autre bus.
    from maestro.config import load_settings
    from maestro.controltower.events import REDIS_URL_DEFAUT
    from maestro.telemetry.redact import redact_secrets

    url = load_settings().redis_url or REDIS_URL_DEFAUT
    lisible = endpoint_lisible(url)
    try:
        import redis
    except ImportError:  # pragma: no cover - le client vient avec celery[redis]
        print(
            "Client Redis absent du venv — réinstaller les dépendances "
            "(bash scripts/setup.sh --only python).",
            file=sys.stderr,
        )
        return 1

    client = redis.Redis.from_url(
        url, socket_connect_timeout=DELAI_PING_S, socket_timeout=DELAI_PING_S
    )
    try:
        client.ping()
    except Exception as erreur:  # noqa: BLE001 - tout échec vaut « injoignable »
        detail = redact_secrets(str(erreur)) or type(erreur).__name__
        print(f"Redis injoignable sur {lisible} : {detail}", file=sys.stderr)
        print(f"Le lancer : {COMMANDE_REDIS}", file=sys.stderr)
        return 1
    finally:
        client.close()

    print(f"Redis joignable sur {lisible}.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Démarre le serveur de l'API Control Tower (bloquant jusqu'à l'arrêt)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-h", "--help"}:
        print(_USAGE, file=sys.stderr)
        return 0
    if args == ["--verifier-redis"]:
        return verifier_redis()

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
