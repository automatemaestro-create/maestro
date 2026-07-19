# Infrastructure locale — Maestro

Services locaux **optionnels** pour le développement : PostgreSQL
(données / traces), Redis (file de tâches, pub/sub) et Temporal (workflows
durables, Phase 3). Cf. [docs/02 §4](../docs/02-stack-technique.md).

## Démarrer / arrêter

```bash
docker compose -f infra/docker-compose.yml up -d      # démarre en arrière-plan
docker compose -f infra/docker-compose.yml ps         # statut
docker compose -f infra/docker-compose.yml down       # arrête (conserve les données)
docker compose -f infra/docker-compose.yml down -v    # arrête + supprime le volume
```

## Connexion

| Service    | URL de connexion                                      | Identifiants      |
|------------|-------------------------------------------------------|-------------------|
| PostgreSQL | `postgresql://maestro:maestro@localhost:5432/maestro` | maestro / maestro |
| Redis      | `redis://localhost:6379/0`                            | —                 |
| Temporal   | `localhost:7233` (gRPC) — UI : <http://localhost:8233> | —                |

Ces valeurs correspondent aux variables `DATABASE_URL` / `REDIS_URL` /
`TEMPORAL_ADDRESS` de [`.env.example`](../.env.example). Ce sont des identifiants
de **développement local uniquement** — aucun secret de production ici.

Redis sert de **broker et backend de résultats** à la file de tâches
([`maestro/queue/`](../maestro/queue/), ticket #41) — la même instance portera
le pub/sub temps réel (tickets suivants).

## Image du mode isolé (ticket #108)

[`sandbox/Dockerfile`](./sandbox/Dockerfile) définit l'**image dédiée** du mode
isolé : quand `MAESTRO_ISOLATION=conteneur` est posé dans le `.env`, chaque
exécution outillée d'agent tourne dans un conteneur durci jetable construit sur
cette image (CLI Claude Code + outillage minimal, utilisateur non-root). À
construire une fois :

```bash
docker build -t maestro-sandbox:latest infra/sandbox
```

Accès accordés, activation et limites :
[docs/17-isolation-execution.md](../docs/17-isolation-execution.md).

## Temporal (workflows durables — ticket #94)

Le service `temporal` démarre un serveur **Temporal de développement** tout-en-un
(`temporal server start-dev`) : le serveur gRPC écoute sur `localhost:7233`
(l'adresse utilisée par le SDK Python `temporalio`, surchargée par
`TEMPORAL_ADDRESS`) et l'**UI web** sur <http://localhost:8233>. L'état est
persisté en SQLite dans le volume `temporal-data` : les workflows survivent au
redémarrage du conteneur. Vérification rapide : `maestro-temporal-demo`
(workflow + worker hello-world, [`maestro/temporal_demo.py`](../maestro/temporal_demo.py))
doit imprimer `Bonjour, Maestro !` et l'exécution apparaît dans l'UI. C'est le
socle de la migration du moteur d'exécution vers des workflows durables
(tickets #95 et #96, parent #92).
