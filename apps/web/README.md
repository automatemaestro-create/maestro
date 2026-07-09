# apps/web — Control Tower

Interface web de supervision (le poste de pilotage, docs/05) — v1 du ticket #47 :

- **Tableau de bord temps réel** : état des agents (libre/occupé, tâche courante,
  compteurs, coût cumulé) et des tâches, mis à jour par WebSocket sans rechargement ;
- **Kanban** des tâches par statut (machine à états docs/03 §3) ;
- **Réassignation manuelle** d'une tâche à un autre agent depuis chaque carte
  (EF-11/EF-20) ;
- **Fil d'activité** en direct (statuts, activités d'agents, messages inter-agents).

Stack (docs/02 §5) : **Next.js + React + TypeScript + Tailwind**.

## Lancer en local

1. **Backend** (API REST + WebSocket, ticket #46) — Redis du docker-compose requis
   pour le flux temps réel multi-process :

   ```bash
   docker compose -f infra/docker-compose.yml up -d
   .venv/Scripts/python.exe -m maestro.controltower.cli   # ou : maestro-api
   ```

   Côté moteur, `maestro-run --publier` (ou un worker de la file #41) alimente le
   canal d'événements.

2. **UI** :

   ```bash
   cd apps/web
   npm install
   npm run dev
   ```

   Puis ouvrir <http://localhost:3000>.

L'UI vise `http://localhost:8000` (l'écoute par défaut de `maestro-api`) ; pour un
backend ailleurs, définir `NEXT_PUBLIC_MAESTRO_API_URL` au lancement (variable
inlinée au build par Next.js) :

```bash
NEXT_PUBLIC_MAESTRO_API_URL=http://mon-hote:8000 npm run dev
```

## Modèle de données

Un client charge l'état courant par le REST (`/api/taches`, `/api/agents`) puis
suit les événements (`/ws/evenements`). Le backend projette chaque événement sur
son état **avant** de le diffuser : à réception d'un événement, le REST est déjà
à jour — l'UI recharge donc l'état (rechargements coalescés) au lieu de dupliquer
la projection en TypeScript (`lib/useControlTower.ts`). La connexion WebSocket se
rétablit seule et chaque reconnexion recharge l'état.

## Vérifications

```bash
npm run lint    # ESLint
npm run build   # build de production (vérifie aussi le typage TS)
```

Le job CI `web-build` (`.gitlab-ci.yml`) rejoue ces deux commandes quand
`apps/web/` change.
