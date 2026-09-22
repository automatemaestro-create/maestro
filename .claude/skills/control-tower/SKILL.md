---
name: control-tower
description: Démarrer (ou arrêter) la Control Tower en local — la vraie stack sur Redis, vide ou sur l'état laissé par le banc — en nettoyant les anciennes sessions
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

## La vraie stack, toujours (#186, #1156)

La Control Tower se regarde sur **la vraie orchestration** : l'API réelle
(`maestro.controltower.cli`, alias `maestro-api`), son bus Redis Pub/Sub et son
journal durable (#97), qui rend l'historique au redémarrage. Il n'y a pas d'autre
stack à monter pour vérifier le produit — ni pour le travail d'écran, ni pour
`verify`, ni pour les captures —, et c'est une décision : un scénario factice
montre ce qu'on a scénarisé, pas ce que le produit fait (#1156).

**Redis est donc requis, et rien ne s'y substitue.** S'il manque, le script
s'arrête **avant d'avoir touché à quoi que ce soit** (ni session en place
arrêtée, ni service démarré) et donne le geste exact :

```bash
docker compose -f infra/docker-compose.yml up -d redis
```

Le relayer tel quel à l'utilisateur, puis relancer. Ne rien proposer d'autre à
la place : sans Redis, il n'y a pas de Control Tower à regarder. Le préflight
seul, sans rien démarrer :
`.venv/Scripts/python.exe -m maestro.controltower.cli --verifier-redis`
(`.venv/bin/python` sous Unix).

**Chaque copie de travail a ses données** (#1164) : le clone principal et chaque
worktree rangent leurs clés Redis dans leur **espace** et leurs fils et projets
sous leur `core/`. Le préflight l'annonce — espace, fils, projets — avant de
démarrer. Deux stacks lancées depuis deux copies ne se voient pas.

## Ce que l'écran montre — d'où viennent les états

| Ce qu'on veut voir | D'où il vient | Le geste |
| ------------------ | ------------- | -------- |
| **vide** | une stack **neuve** | `start.sh` dans un worktree qui n'a encore rien servi ; ailleurs, la purge (plus bas) |
| **peuplé**, **charge** (listes et textes longs) | l'**état laissé par le banc** des scénarios (#1148) | `start.sh --etat-banc` |
| **erreur** | une **vraie panne** | l'API coupée, l'UI restant servie (l'écran dit « injoignable »), ou une API qui répond en erreur (#996) |

`--etat-banc` sert, par l'API réelle, l'état que le dernier passage du banc a
laissé : rouvert à chaque démarrage **sans rien rejouer**, dans un jeu de données
à part (ni celles de la copie ni celles du poste ne sont touchées), et **son âge
est dit** — le relayer : un état ancien ne porte pas les données que les derniers
tickets ont ajoutées. Sans
passage sauvé, le lanceur le dit et s'arrête. `--etat-banc --rejouer[=S2,S4]` le
refait : banc remis à neuf, puis passage joué au premier plan contre la stack,
**avec le vrai modèle** (des dizaines de minutes, de l'ordre du dollar) — à
lancer en tâche de fond, jamais sous le plafond d'un appel, et seulement quand
l'état date ou manque. Le détail vit dans docs/40 §5 et `maestro/scenarios/etat.py`.

Un état que le réel ne sait pas produire **se nomme non couvert** : il ne se
fabrique pas.

La purge rend le poste vide sans toucher à la configuration ; elle est
**destructive** et ne se joue qu'après un « oui » explicite, la stack arrêtée
(`--check` dit d'abord ce qui partirait) :
`.venv/Scripts/python.exe -m maestro.controltower.purge [--check]`.

## Remplir le poste : lancer un run

Sur une stack neuve, un premier démarrage n'affiche **rien** — c'est normal,
l'UI l'explique elle-même (`PosteVide`) au lieu d'aligner des panneaux à zéro. Ce
n'est pas une panne : une API injoignable, elle, a sa bannière d'erreur. Pour
regarder un écran peuplé sans rien dépenser, `--etat-banc` (plus haut) ; pour
voir le produit travailler **maintenant**, deux façons de l'alimenter :

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
- Logs : `.maestro/controltower/<portAPI>-<portUI>/{api,ui}.log` en cas de souci,
  `navigateur.log` pour le chien de garde — sous la racine du worktree et affichés en
  relatif, donc lisibles sans approbation (#234). Le jeton de session, le PID du chien de
  garde et le profil jetable du navigateur restent, eux, dans le temporaire du système :
  personne ne les lit, et un profil Chrome n'a rien à faire dans un répertoire de travail.
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
