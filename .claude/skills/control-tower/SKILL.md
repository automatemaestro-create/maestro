---
name: control-tower
description: Lancer Maestro dans sa fenêtre de bureau (coque Electron), ou la Control Tower dans un onglet pour le mode web et les vérifications pilotées — la vraie stack sur Redis, vide ou sur l'état laissé par le banc —, et l'arrêter
---

# Lancer Maestro en local

## Le geste par défaut : la fenêtre de bureau (#923, #1273)

Quand l'utilisateur veut **se servir** de Maestro (« lance Maestro », « lance la
control tower », « démarre l'UI »…), ouvrir sa **fenêtre native** — ni onglet de
navigateur, ni lanceur ad hoc :

```bash
bash scripts/controltower/desktop.sh
```

**À jouer en tâche de fond** (Bash `run_in_background: true`) : `desktop.sh` se
termine par un `exec` d'Electron et ne rend la main qu'à la **fermeture de la
fenêtre** — au premier plan, il tiendrait l'appel jusqu'à son plafond.

- **La ligne `[coque] Maestro — UI :<port> · API :<port>` confirme l'ouverture**
  et nomme la stack à laquelle la fenêtre est attachée : la relayer (lire la
  sortie de la tâche de fond quelques secondes après le lancement). La sortie de
  `start.sh` suit, relayée par la coque ; un démarrage en échec s'affiche aussi
  dans la fenêtre, sur son écran d'attente.
- **Fermer la fenêtre arrête la stack** — API et UI, runs en vol soldés : la coque
  joue `start.sh --stop` avant de sortir. Rien d'autre à faire.
- `[coque] Maestro est déjà ouvert sur cette stack (UI :<port> · API :<port>) — …`,
  sans la ligne `[coque] Maestro — UI…` : **rien ne s'est ouvert**, une fenêtre sert
  déjà ces ports. Le verrou d'instance unique vaut pour **la stack** — son couple de
  ports —, plus pour le poste (#1287) : une copie lancée sur les ports que
  `worktree.sh ensure` lui a donnés s'ouvre à côté de celle du clone principal. La
  fenêtre en place revient au premier plan ; si elle sert une autre copie, le dire :
  ouvrir celle-ci sur les mêmes ports demande de fermer l'autre, ce qui arrête sa stack.
- Premier lancement : Electron s'installe à la demande, **taille annoncée** avant
  de télécharger (docs/35 §2.3).
- Ports : la coque lit `MAESTRO_PORT_API` et `MAESTRO_PORT_UI`. Une session
  relocalisée dans un worktree ne les hérite pas : passer ceux que
  `worktree.sh ensure` a annoncés,
  `env MAESTRO_PORT_API=<api> MAESTRO_PORT_UI=<ui> bash scripts/controltower/desktop.sh`.

Rien n'est à réimplémenter : la coque (`apps/desktop/main.js`) joue
`start.sh --no-browser` au démarrage et `start.sh --stop` à la fermeture, et
`desktop.sh` lui donne le Node du dépôt, Electron et le bash natif (docs/35 §2).
`python -m maestro.lanceur` (#640) n'est **pas** le geste d'un clone de
développement : il sert un front construit, pour le produit installé (#641).

## Le mode web et les vérifications : `start.sh`

`start.sh` seul ouvre la Control Tower **dans un onglet du navigateur** : c'est le
**mode web**, de premier ordre (D3, docs/35), mais plus le geste proposé par
défaut. On y vient quand on la veut dans un navigateur, et pour les
**vérifications pilotées** : `--no-browser` pour le skill `verify` et les
captures, et les états de la section plus bas (`--etat-banc`, `--etat-neuf`,
`--couper-api`), que la fenêtre ne relaie pas — elle sert toujours les données de
la copie (#1168).

```bash
bash scripts/controltower/start.sh
```

Le script fait tout : il **termine d'abord les anciennes sessions** (uniquement
les processus qui écoutent sur :8000/API et :3000/UI), démarre l'API, puis
l'UI Next.js (`apps/web`) pointée dessus, attend que les deux répondent, et
**ouvre un onglet de navigateur** sur l'UI.

**Quand la fenêtre du navigateur est isolée, la fermer arrête l'API et l'UI**
(#149) : un chien de garde détaché la surveille et libère les ports dès sa
disparition. Le script, lui, rend la main tout de suite — il ne bloque pas sur le
navigateur.

Ce n'est vrai que si le navigateur ouvert est un **Chromium** (fenêtre isolée) ;
avec un défaut hors Chromium (Firefox, Safari), la page s'ouvre dans la session
de l'utilisateur et l'arrêt reste manuel (#200). Ne pas trancher de tête : le
script **dit lequel des deux** sur sa dernière ligne — `arrêt : fermer la fenêtre
du navigateur (ou …--stop)` ou `arrêt : bash scripts/controltower/start.sh
--stop`. Relayer cette ligne telle quelle.

En mode web, donner à la fin l'URL : **http://localhost:3000** (le port de l'UI
de la copie).

## La vraie stack, toujours (#186, #1156)

La Control Tower se regarde sur **la vraie orchestration** : l'API réelle
(`maestro.controltower.cli`, alias `maestro-api`), son bus Redis Pub/Sub et son
journal durable (#97), qui rend l'historique au redémarrage. Il n'y a pas d'autre
stack à monter pour vérifier le produit — ni pour le travail d'écran, ni pour
`verify`, ni pour les captures —, et c'est une décision : un scénario factice
montre ce qu'on a scénarisé, pas ce que le produit fait (#1156).

**Redis est donc requis, et rien ne s'y substitue** — pour la fenêtre comme pour
l'onglet. S'il manque, `start.sh` s'arrête **avant d'avoir touché à quoi que ce
soit** (ni session en place arrêtée, ni service démarré) et donne le geste exact,
que la coque relaie aussi dans sa fenêtre :

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
| **vide** | une stack **neuve** | `start.sh --etat-neuf` (le banc de la copie remis à neuf, #1165) ; les données de la copie elles-mêmes, par la purge (plus bas) |
| **peuplé**, **charge** (listes et textes longs) | l'**état laissé par le banc** des scénarios (#1148) | `start.sh --etat-banc` |
| **erreur** | une **vraie panne** | `start.sh --couper-api` : l'API seule tombe, l'UI reste servie (l'écran dit « injoignable ») ; ce n'est pas un arrêt, rien n'est soldé |

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

## Options de `start.sh`

- Arrêt : fermer la fenêtre du navigateur, ou
  `bash scripts/controltower/start.sh --stop` (qui ferme aussi cette fenêtre).
- Une fenêtre de bureau ouverte sert déjà ces ports : `start.sh` y **remplacerait
  sa stack** (il termine d'abord les sessions en place). Pour vérifier à côté, le
  faire depuis une autre copie, ou fermer la fenêtre d'abord.
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
