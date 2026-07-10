---
name: control-tower
description: Démarrer (ou arrêter) la Control Tower en local — API de démo + UI Next.js — en nettoyant les anciennes sessions
---

# Lancer la Control Tower en local

Quand l'utilisateur veut **regarder** la Control Tower (« lance la control
tower », « démarre l'UI »…), passer par le script dédié — ne pas réécrire de
lanceur ad hoc :

```bash
bash scripts/controltower/start.sh
```

Le script fait tout : il **termine d'abord les anciennes sessions** (uniquement
les processus qui écoutent sur :8000/API et :3000/UI), démarre l'API de démo
(`maestro.controltower.demo` : app FastAPI réelle sur bus mémoire + scénario
d'événements factices publié en continu — Kanban peuplé, coûts par tâche, une
validation humaine laissée en attente, pulsation QA toutes les ~20 s), puis
l'UI Next.js (`apps/web`) pointée dessus, et attend que les deux répondent.

À la fin, donner à l'utilisateur l'URL : **http://localhost:3000**.

- Arrêt : `bash scripts/controltower/start.sh --stop` (nettoyage seul).
- Logs : `${TMPDIR:-/tmp}/maestro-controltower/{api,ui}.log` en cas de souci.
- Ports surchargables : `MAESTRO_PORT_API`, `MAESTRO_PORT_UI`.
- Le scénario est **factice** (run `demo-live`) : pour brancher une vraie
  orchestration (Redis + moteur), c'est `maestro-api` (`maestro/controltower/cli.py`).
- Pour une **vérification de bout en bout** pilotée au navigateur, voir le
  skill `verify` (qui réutilise ce même script pour le lancement).
