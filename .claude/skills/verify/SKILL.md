---
name: verify
description: Vérifier de bout en bout la Control Tower (API FastAPI + UI Next.js) en pilotant un vrai navigateur
---

# Vérifier Maestro de bout en bout

## Lancer l'ensemble (API + UI)

Ne pas réécrire de lanceur ad hoc : le script du ticket #65 fait tout
(nettoyage des anciennes sessions sur :8000/:3000, API de démo sur bus
mémoire — `maestro.controltower.demo`, app FastAPI réelle + scénario
d'événements factices en continu —, UI Next.js pointée dessus) :

```bash
bash scripts/controltower/start.sh          # UI sur :3000, API sur :8000
bash scripts/controltower/start.sh --stop   # arrêt
```

Pour un scénario **sur mesure** (autres événements, autre timing), s'inspirer
de `maestro/controltower/demo.py` : `create_app(bus=InMemoryEventBus())` sous
uvicorn programmatique, événements `EVENEMENT_*` publiés depuis une tâche
asyncio du même process, toujours via `.venv/Scripts/python.exe` (jamais le
python système). L'UI lit `NEXT_PUBLIC_MAESTRO_API_URL` au démarrage du dev
server (inlinée au build en prod).

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
