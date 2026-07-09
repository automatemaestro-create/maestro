# apps/api — Backend Control Tower

API backend du poste de pilotage (ticket #46) : expose l'**état courant** de
l'orchestration (REST) et **pousse les événements en temps réel** (WebSocket).
Stack : **FastAPI + WebSocket + Redis Pub/Sub** (voir `docs/02-stack-technique.md` §4 ;
interface consommatrice : `docs/05-interface-control-tower.md`).

Le code vit dans le paquet [`maestro/controltower`](../../maestro/controltower)
(couvert par la CI : pytest + mypy strict) ; ce dossier documente l'app déployable.

## Démarrer

```bash
docker compose -f infra/docker-compose.yml up -d redis   # le bus Pub/Sub
maestro-api                                              # http://127.0.0.1:8000
# équivalent : uvicorn --factory maestro.controltower.app:create_default_app
```

Côté producteur, les événements sont **sourcés depuis la télémétrie** (#8) :

```bash
maestro-run --publier "<objectif>"   # publie chaque étape du journal sur Redis
```

## Endpoints

| Méthode | Chemin | Rôle |
|---|---|---|
| GET | `/api/sante` | vitalité du service |
| GET | `/api/taches` | tâches : statut, agent assigné, coût (source du Kanban) |
| GET | `/api/agents` | agents : libre/occupé, tâche courante, compteurs, coût cumulé |
| GET | `/api/executions/{run_id}` | détail d'une exécution : trace et coût agrégé |
| POST | `/api/taches/{id}/reassigner` | réassignation manuelle `{"agent": "..."}` (Kanban) |
| WS | `/ws/evenements` | flux d'événements JSON |

Types d'événements diffusés : `tache.statut`, `tache.reassignation`,
`agent.activite`, `message.inter_agents` (forme : `Event.to_dict`).

## Tests

```bash
.venv/Scripts/python.exe -m pytest tests/test_controltower.py   # REST + WebSocket, sans Redis
```
