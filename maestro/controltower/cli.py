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
avec le reste de la configuration, plutôt que d'être réécrite en shell. Quand le
bus répond, il **annonce les données** que la stack verra (#1164) — son espace
Redis et ses dépôts de fichiers (`maestro.controltower.donnees.annonce`) : c'est
ainsi que la séparation entre copies de travail est dite au démarrage, sans un
appel Python de plus au lanceur. En **mode local** (`MAESTRO_PERSISTANCE=sqlite`,
#639) il n'y a aucun service à joindre : le préflight le dit, nomme le fichier du
journal, annonce les mêmes données et rend `0`.

`--etat-banc` (#1164) place la stack sur le **jeu de données du banc** de la
copie (`maestro.controltower.donnees.donnees_du_banc`) : son espace et ses
dépôts, posés sur l'environnement du process avant de servir, donc hérités par
les hôtes détachés. Avec `--verifier-redis`, c'est ce jeu-là qui est annoncé.
Rouvrir ou vider ce jeu est l'affaire de `maestro.scenarios.etat`, que le
lanceur joue entre l'arrêt de l'ancienne session et le démarrage de l'API.

Au démarrage, il **dit dans quel régime l'API sert** (#638) : durci (un jeton
sur chaque requête) ou ouvert, et les origines admises —
`maestro.controltower.acces`. La ligne part sur la sortie standard, donc dans
`api.log`, là où on vient la lire quand une requête est refusée.

`--jeton` ne démarre rien non plus : il rend le **jeton du poste** (engendré au
besoin) sur la sortie standard et le régime sur l'erreur standard. C'est par là
que `scripts/controltower/start.sh` l'obtient pour le passer au front, et c'est
ici plutôt que dans `acces` parce que ce module-là est déjà importé par le
paquet — l'exécuter comme `__main__` ferait précéder le jeton d'un
`RuntimeWarning`, en tête du seul fichier qu'on lit pour comprendre un démarrage
raté.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

# Le geste et le point de connexion vivent avec la panne de l'API (#1206) : le
# préflight et l'API en marche les disent avec les mêmes mots. Réexportés ici,
# où ils sont nés (#186).
from maestro.controltower.magasin import COMMANDE_REDIS, endpoint_lisible

_USAGE = (
    "Usage : maestro-api [--hote <adresse>] [--port <port>] [--etat-banc] | "
    "--verifier-redis [--etat-banc] | --jeton"
)

#: Écoute par défaut : locale (l'API est un backend de développement au POC).
HOTE_DEFAUT = "127.0.0.1"
PORT_DEFAUT = 8000

#: Délai du ping de `--verifier-redis`, en secondes. Un diagnostic doit trancher
#: vite : un Redis éteint refuse la connexion tout de suite, mais un hôte
#: injoignable (URL distante, VPN coupé) ferait autrement patienter le lanceur
#: le temps du time-out TCP du système.
DELAI_PING_S = 3.0


def verifier_redis() -> int:
    """Ping le bus Redis de l'API : 0 s'il répond, 1 sinon (diagnostic sur stderr).

    Quand il répond, annonce les données que la stack verra (#1164) — celles de
    l'environnement courant, donc celles du banc si `--etat-banc` les y a posées.

    ⚠ **En mode local il n'y a rien à pinguer** (`MAESTRO_PERSISTANCE=sqlite`,
    #639) : la stack ne parle à aucun service, et exiger un Redis qu'elle n'ouvre
    pas ferait refuser au lanceur un démarrage qui marche. Le préflight le dit et
    rend `0`, avec la même annonce — c'est ce qui laisse `start.sh` inchangé : il
    ne connaît aucun nom de variable, il demande, et Python résout.
    """
    # Imports locaux : `--verifier-redis` est un mode à part, et le client Redis
    # n'est pas nécessaire pour servir l'API sur un autre bus.
    from maestro.config import load_settings
    from maestro.controltower.events import REDIS_URL_DEFAUT
    from maestro.controltower.persistence import (
        SUPPORT_SQLITE,
        chemin_sqlite,
        support_persistance,
    )
    from maestro.telemetry.redact import redact_secrets

    settings = load_settings()
    if support_persistance(settings) == SUPPORT_SQLITE:
        print(
            f"Persistance locale ({SUPPORT_SQLITE}) : aucun service externe à lancer — "
            f"journal dans {chemin_sqlite(settings)}."
        )
        return _annoncer_les_donnees()

    url = settings.redis_url or REDIS_URL_DEFAUT
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
    return _annoncer_les_donnees()


def _annoncer_les_donnees() -> int:
    """Dit ce que la stack verra, et rend `0` — la fin commune des deux préflights."""
    # Import local : l'annonce résout les dépôts de fichiers, ce dont un Redis
    # injoignable n'a pas besoin.
    from maestro.controltower.donnees import annonce, donnees_de_la_stack

    print("\n".join(annonce(donnees_de_la_stack())))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Démarre le serveur de l'API Control Tower (bloquant jusqu'à l'arrêt)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-h", "--help"}:
        print(_USAGE, file=sys.stderr)
        return 0
    if "--etat-banc" in args:
        args.remove("--etat-banc")
        # Import local : seul ce mode a besoin des dépôts de fichiers.
        from maestro.controltower.donnees import donnees_du_banc, poser_sur_le_process

        poser_sur_le_process(donnees_du_banc())
    if args == ["--verifier-redis"]:
        return verifier_redis()
    if args == ["--jeton"]:
        # Import local, comme les autres modes à part : servir l'API n'a pas
        # besoin de ce chemin, et le lanceur ne veut que cette ligne-là.
        from maestro.controltower.acces import rendre_le_jeton

        return rendre_le_jeton()

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

    # Le régime d'accès, **dit avant de servir** (#638) : durci ou ouvert, et
    # les origines admises. Imprimé ici et non journalisé dans l'app — uvicorn
    # ne configure que ses propres loggers, un `logging.info` de
    # `maestro.controltower` n'atteindrait donc pas `api.log`, où cette ligne
    # est précisément ce qu'on vient lire. Résoudre la politique **engendre** le
    # jeton du poste s'il manque, si bien que le lanceur peut le donner au front
    # dès que l'API est démarrée. Une config fautive casse ici, avant le port.
    from maestro.controltower.acces import politique_depuis

    print(politique_depuis().annonce())

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
