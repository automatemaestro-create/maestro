---
name: verify
description: Vérifier de bout en bout la Control Tower (API FastAPI + UI Next.js) en pilotant un vrai navigateur
---

# Vérifier Maestro de bout en bout

## Backend Control Tower (API REST + WebSocket)

Pas besoin de Redis ni de Docker : l'app FastAPI réelle tourne sur le bus
mémoire (`InMemoryEventBus`) — mêmes endpoints que la production, seul le
transport change. Lancer un petit script qui appelle
`maestro.controltower.app.create_app(bus=...)` sous uvicorn
(`uvicorn.Config`/`Server` programmatiques) et publie un scénario d'événements
(`Event`, types `EVENEMENT_*` de `maestro.controltower.events`) sur le bus
depuis une tâche asyncio du même process. Toujours utiliser
`.venv/Scripts/python.exe` (jamais le python système).

## UI (apps/web, Next.js)

```bash
cd apps/web
NEXT_PUBLIC_MAESTRO_API_URL=http://127.0.0.1:<port-api> npm run dev   # port 3000
```

La variable est lue au démarrage du dev server (inlinée au build en prod).

## Piloter le navigateur

Pas de navigateur Playwright téléchargé : `playwright-core` (npm, ~3 Mo) +
Edge déjà installé sur la machine —
`chromium.launch({ channel: "msedge", headless: true })`. Installer
`playwright-core` dans le scratchpad, pas dans le repo.

Flux qui valent la peine d'être pilotés :

- badge « Temps réel connecté » (WebSocket ouverte) ;
- apparition/évolution des cartes Kanban pendant que le scénario publie ;
- réassignation via le `<select>` d'une carte → la carte change d'agent et le
  fil d'activité affiche « réassignée à … » ;
- validation humaine (#48) : publier un `validation.demande` → le panneau
  « Validations en attente » apparaît en tête avec le contexte (agent, tâche,
  action, motif) ; cliquer **Approuver**/**Refuser** → la carte disparaît et un
  abonné du bus reçoit le `validation.decision` (c'est lui qui libère le
  moteur en pause) ;
- absence de rechargement : poser `window.__marqueur = 42` après le goto et
  vérifier qu'il est intact à la fin ;
- coupure/reprise : tuer le process backend → badge « Reconnexion… » ; le
  relancer → badge revient (backoff ≤ 10 s) et l'état se recharge.

## Pièges

- Le lint React (`react-hooks/set-state-in-effect`) interdit un setState
  synchrone dans un effet : passer par un `setTimeout` (cf.
  `lib/useControlTower.ts`).
- `docker ps` échoue si Docker Desktop est arrêté — inutile pour cette
  vérification, ne pas le démarrer pour ça.
