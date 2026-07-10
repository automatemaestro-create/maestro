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

Human-in-the-loop (#48) : avec `--validation-ui`, les tâches sensibles se
mettent en pause et attendent la décision prise depuis l'UI (pas de time-out) :

```bash
maestro-run --publier --validation-ui "<objectif>"
```

## Endpoints

| Méthode | Chemin | Rôle |
|---|---|---|
| GET | `/api/sante` | vitalité du service |
| GET | `/api/taches` | tâches : statut, agent assigné, coût détaillé — tokens, durée (source du Kanban) |
| GET | `/api/agents` | agents : libre/occupé, tâche courante, compteurs, coût cumulé |
| GET | `/api/executions/{run_id}` | détail d'une exécution : trace et coût agrégé |
| GET | `/api/executions/{run_id}/cout` | grand livre du run (#57, critère MVP n°6) : coût par tâche (tokens entrée/sortie, coût estimé, durée), part de planification et agrégat |
| POST | `/api/taches/{id}/reassigner` | réassignation manuelle `{"agent": "..."}` (Kanban) |
| GET | `/api/validations` | demandes de validation humaine : contexte, statut, décision (#48) |
| POST | `/api/validations/{tache_id}/decision` | décision humaine `{"approuve": true|false}` — 409 si déjà tranchée |
| WS | `/ws/evenements` | flux d'événements JSON |

Types d'événements diffusés : `tache.statut`, `tache.reassignation`,
`agent.activite`, `message.inter_agents`, `validation.demande`,
`validation.decision` (forme : `Event.to_dict`).

## Tests

```bash
.venv/Scripts/python.exe -m pytest tests/test_controltower.py   # REST + WebSocket, sans Redis
```
