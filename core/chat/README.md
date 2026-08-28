# core/chat — Fils de chat utilisateur ↔ agent

Persistance des **fils de conversation** entre un utilisateur et un agent
depuis la Control Tower (tickets #82/#84) : chaque échange envoyé via l'API
est consigné ici et le fil se relit par agent.

## Fonctionnement

- Un fichier par **conversation** (append-only, une ligne JSON par message —
  auteur « utilisateur » ou l'agent, contenu, horodatage, et depuis #268 le
  `run_id`/`tache_id` que la réponse a **ouverts**, vides le reste du temps).
  Un fil est une **suite de conversations** depuis #694 : la première, nommée
  `origine`, est stockée là où le fil l'a toujours été — `<agent>.jsonl` —, les
  suivantes sous `<agent>/<id>.jsonl`, l'identifiant portant son instant
  d'ouverture (`20260828t143012-9f3a2b`).
  ⚠ **Un fichier écrit avant #694 n'a rien à faire pour devenir une
  conversation** : il n'est ni déplacé, ni réécrit, ni relu autrement — c'est le
  chemin qui fait foi, et une ligne sans champ `conversation` vient forcément du
  fichier historique. Une installation qui n'ouvre jamais de seconde conversation
  écrit exactement les mêmes octets qu'avant. Les métadonnées d'une conversation
  (titre, dates, nombre de messages) sont **dérivées** des messages, jamais
  tenues dans un fichier annexe : un tel fichier manquerait précisément aux fils
  d'avant le lot.
- Conversations par HTTP : `GET /api/chat/{agent}/conversations` les liste (la
  plus récente d'abord, jamais vide — un agent a toujours son `origine`),
  `POST` en ouvre une neuve (`201`, idempotent tant que rien n'a été dit), et
  `?conversation=<id>` cible un fil précis sur les lectures comme sur les envois.
- Deux fils ne sont pas ceux d'un agent du catalogue, et portent les **noms
  réservés** correspondants : `assistance.jsonl` (le canal d'aide, #123) et
  `orchestrateur.jsonl` (le fil global, #268 — on y parle à l'orchestration,
  qui peut y ouvrir un run).
- Lecture/écriture par le code : `maestro.controltower.chat.ChatStore` ; par
  HTTP : les endpoints `/api/chat` de l'API Control Tower
  (`maestro/controltower/app.py`) — `POST /api/chat/{agent}/messages` envoie
  et persiste la paire message/réponse, `GET /api/chat/{agent}` relit le fil,
  `GET /api/chat/{agent}/flux` rend la même réponse en SSE, au fur et à mesure
  (#268 : un canal, valable pour les trois fils), et `POST /api/chat/{agent}/flux`
  fait de même pour un message qui embarque des **sources** — une URL ne pouvant
  pas les déclarer (#692). Depuis #695 c'est par là que **la Control Tower**
  parle à un fil : `POST …/messages` reste servi, mais l'écran n'a qu'une façon
  d'envoyer, et la réponse s'y écrit sous les yeux.
- `POST /api/chat/{agent}/flux/{echange}/arret` **arrête** une génération en vol
  (#695) — le seul geste qui l'annule, une déconnexion la laissant s'achever
  (#268). Ce qui a été produit avant l'arrêt est persisté ici comme réponse : un
  fil peut donc porter une réponse **plus courte** que ce que l'agent aurait
  écrit, et c'est une réponse comme une autre, pas une ligne à moitié écrite.
- Chaque message est aussi diffusé en événement `chat.message` sur le bus
  (#46) — le WebSocket `/ws/evenements` porte le fil en temps réel — et
  transite par la messagerie inter-agents (#44, boîte de l'agent).
- Racine remplaçable par `MAESTRO_CHAT_DIR` (cf. `.env.example`) ; la démo
  locale (#65) utilise un répertoire temporaire et n'écrit rien ici.

Les fils écrits ici sont des **données d'exécution** : elles ne sont pas
commitées (voir `.gitignore`). En V1, ce stockage passera en base (entité
`AGENT_MESSAGE`, docs/03) sans changer le contrat.

Tests (#83) : le canal lui-même (persistance, répondeurs, flux d'un envoi)
est couvert par `tests/test_chat.py` ; son exposition HTTP (REST `/api/chat`
+ WebSocket `chat.message`) par `tests/test_controltower.py` (section ⑧) ; et
ce que le chantier « chat global pleine page » y a ajouté (#690, lot 8 #698)
par `tests/test_chat_pleine_page.py` — le flux qui porte ses sources, les
incréments dont la concaténation **est** le message final, et les conversations,
`origine` comprise.
Mode d'emploi : [guide de démarrage §6.4](../../docs/07-guide-de-demarrage.md).
