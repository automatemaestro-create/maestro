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
l'UI Next.js (`apps/web`) pointée dessus, attend que les deux répondent, et
**ouvre une fenêtre de navigateur** sur l'UI.

**Fermer cette fenêtre arrête l'API et l'UI** (#149) : un chien de garde
détaché la surveille et libère les ports dès sa disparition. Le script, lui,
rend la main tout de suite — il ne bloque pas sur le navigateur. Il n'y a donc
plus rien à arrêter à la main dans le cas courant : le dire à l'utilisateur
plutôt que de promettre un `--stop`.

À la fin, donner à l'utilisateur l'URL : **http://localhost:3000**.

- Arrêt : fermer la fenêtre du navigateur, ou
  `bash scripts/controltower/start.sh --stop` (qui ferme aussi cette fenêtre).
- `--no-browser` : démarre sans ouvrir de fenêtre — donc **sans arrêt
  automatique**. À utiliser quand on pilote soi-même un navigateur (skill
  `verify`) ou qu'on veut juste la stack en tâche de fond.
- Logs : `${TMPDIR:-/tmp}/maestro-controltower-<portAPI>-<portUI>/{api,ui}.log` en cas
  de souci, `navigateur.log` pour le chien de garde.
- Ports surchargables : `MAESTRO_PORT_API`, `MAESTRO_PORT_UI` — et **tout est indexé
  dessus** (dossier de logs, jeton de session, profil de la fenêtre), de sorte que deux
  sessions parallèles (un worktree par ticket, docs/10 §9) ne s'arrêtent pas l'une
  l'autre. Un worktree créé par `scripts/git/worktree.sh` reçoit ses ports d'office.
- Navigateur : Edge/Chrome/Chromium détecté automatiquement, surchargeable via
  `MAESTRO_BROWSER`. La fenêtre utilise un **profil jetable** dédié — jamais
  `MAESTRO_CHROME_PROFILE` (celui du MCP `chrome-maestro`), qu'elle bloquerait
  (un profil Chrome n'accepte qu'un consommateur à la fois). Sans navigateur
  trouvé, le script se contente d'afficher l'URL.
- Le scénario est **factice** (run `demo-live`) : pour brancher une vraie
  orchestration (Redis + moteur), c'est `maestro-api` (`maestro/controltower/cli.py`).
- Pour une **vérification de bout en bout** pilotée au navigateur, voir le
  skill `verify` (qui réutilise ce même script pour le lancement).
