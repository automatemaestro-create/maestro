---
name: control-tower
description: Démarrer (ou arrêter) la Control Tower en local — API réelle sur Redis par défaut, scénario factice en --demo — en nettoyant les anciennes sessions
---

# Lancer la Control Tower en local

Quand l'utilisateur veut **regarder** la Control Tower (« lance la control
tower », « démarre l'UI »…), passer par le script dédié — ne pas réécrire de
lanceur ad hoc :

```bash
bash scripts/controltower/start.sh
```

Le script fait tout : il **termine d'abord les anciennes sessions** (uniquement
les processus qui écoutent sur :8000/API et :3000/UI), démarre l'API, puis
l'UI Next.js (`apps/web`) pointée dessus, attend que les deux répondent, et
**ouvre une fenêtre de navigateur** sur l'UI.

**Quand la fenêtre est isolée, la fermer arrête l'API et l'UI** (#149) : un chien
de garde détaché la surveille et libère les ports dès sa disparition. Le script,
lui, rend la main tout de suite — il ne bloque pas sur le navigateur.

Ce n'est vrai que si le navigateur ouvert est un **Chromium** (fenêtre isolée) ;
avec un défaut hors Chromium (Firefox, Safari), la page s'ouvre dans la session
de l'utilisateur et l'arrêt reste manuel (#200). Ne pas trancher de tête : le
script **dit lequel des deux** sur sa dernière ligne — `arrêt : fermer la fenêtre
du navigateur (ou …--stop)` ou `arrêt : bash scripts/controltower/start.sh
--stop`. Relayer cette ligne telle quelle.

À la fin, donner à l'utilisateur l'URL : **http://localhost:3000**.

## Deux modes — le réel par défaut (#186)

|              | défaut (mode réel)                         | `--demo`                               |
| ------------ | ------------------------------------------ | -------------------------------------- |
| API          | `maestro.controltower.cli` (`maestro-api`) | `maestro.controltower.demo`            |
| Bus          | Redis Pub/Sub + journal durable (#97)      | mémoire                                |
| Données      | **la vraie orchestration**                 | scénario **factice**, qui le dit       |
| Redis        | **requis**                                 | aucun                                  |
| Au démarrage | poste **vide** tant qu'aucun run ne publie | Kanban peuplé, coûts, validation, chat |

**Ne jamais retomber en douce sur `--demo`** quand le mode réel échoue : c'est
exactement ce qui ferait prendre des données factices pour la réalité. Si Redis
manque, le script s'arrête **avant d'avoir touché à quoi que ce soit** (ni
session en place arrêtée, ni service démarré) et donne le geste exact :

```bash
docker compose -f infra/docker-compose.yml up -d redis
```

Le relayer tel quel à l'utilisateur, puis relancer. Proposer `--demo` seulement
comme **alternative annoncée** (« explorer l'UI sans Redis »), jamais comme
rattrapage silencieux. Le préflight seul, sans rien démarrer :
`.venv/Scripts/python.exe -m maestro.controltower.cli --verifier-redis`
(`.venv/bin/python` sous Unix).

`--demo` reste le bon choix pour le **développement front**, le skill `verify`
et les captures de `/milestone-presentation`.

## Remplir le poste : lancer un run

En mode réel, un premier démarrage n'affiche **rien** — c'est normal, l'UI
l'explique elle-même (`PosteVide`) au lieu d'aligner des panneaux à zéro. Ce
n'est pas une panne : une API injoignable, elle, a sa bannière d'erreur. Deux
façons de l'alimenter :

```bash
# Depuis le dépôt — --publier est ce qui pousse les événements vers l'UI
maestro-run --publier "<objectif>"

# Depuis l'API (#185) — rend le run_id tout de suite, le run part en arrière-plan
curl -X POST http://127.0.0.1:8000/api/executions \
  -H 'Content-Type: application/json' -d '{"objectif": "<objectif>"}'
```

Suivi : `GET /api/executions` (récents d'abord). Annulation :
`POST /api/executions/<run_id>/annuler` (`409` si le run est déjà soldé, `404`
s'il est inconnu). Contrat complet : doc 05 §6.1 ; usage : doc 07 §6.10 et §6.11.

## Options du script

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
- `--diagnostic-navigateur` : dit quel mode (`stack:`) et quel navigateur
  seraient pris, **sans rien démarrer ni ouvrir**.
- Navigateur : celui **par défaut du poste**, lu à chaud (#200), surchargeable via
  `MAESTRO_BROWSER`. Un Chromium ouvre une fenêtre isolée sur **profil jetable** —
  jamais `MAESTRO_CHROME_PROFILE` (celui du MCP `chrome-maestro`), qu'elle bloquerait
  (un profil Chrome n'accepte qu'un consommateur à la fois). Hors Chromium (Firefox,
  Safari), l'ouverture se fait dans la session de l'utilisateur, **sans arrêt
  automatique** — le dire plutôt que de le masquer.
- Pour une **vérification de bout en bout** pilotée au navigateur, voir le
  skill `verify` (qui réutilise ce même script pour le lancement).
