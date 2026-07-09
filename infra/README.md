# Infrastructure locale — Maestro (Phase 0)

Bases de données locales **optionnelles** pour le développement : PostgreSQL
(données / traces) et Redis (file de tâches, pub/sub). Cf.
[docs/02 §4](../docs/02-stack-technique.md).

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

Ces valeurs correspondent aux variables `DATABASE_URL` / `REDIS_URL` de
[`.env.example`](../.env.example). Ce sont des identifiants de **développement
local uniquement** — aucun secret de production ici.

Redis sert de **broker et backend de résultats** à la file de tâches
([`maestro/queue/`](../maestro/queue/), ticket #41) — la même instance portera
le pub/sub temps réel (tickets suivants).
