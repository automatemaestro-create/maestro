# core/chat — Fils de chat utilisateur ↔ agent

Persistance des **fils de conversation** entre un utilisateur et un agent
depuis la Control Tower (tickets #82/#84) : chaque échange envoyé via l'API
est consigné ici et le fil se relit par agent.

## Fonctionnement

- Un fichier par agent : `<agent>.jsonl` (append-only, une ligne JSON par
  message — auteur « utilisateur » ou l'agent, contenu, horodatage).
- Lecture/écriture par le code : `maestro.controltower.chat.ChatStore` ; par
  HTTP : les endpoints `/api/chat` de l'API Control Tower
  (`maestro/controltower/app.py`) — `POST /api/chat/{agent}/messages` envoie
  et persiste la paire message/réponse, `GET /api/chat/{agent}` relit le fil.
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
+ WebSocket `chat.message`) par `tests/test_controltower.py` (section ⑧).
Mode d'emploi : [guide de démarrage §6.4](../../docs/07-guide-de-demarrage.md).
