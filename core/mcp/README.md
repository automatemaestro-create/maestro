# core/mcp — Serveurs MCP déclarés par agent

Dépôt des **déclarations de serveurs MCP** par agent (ticket #104, parent
#101) : un agent peut se voir brancher des capacités externes (Slack, gestion
de tickets, Figma, cloud…) via le Model Context Protocol — le moteur monte les
serveurs déclarés sur ses exécutions outillées, sans connecteur ad hoc.

## Fonctionnement

- Un fichier par agent : `<agent>.json`, de la forme `{"serveurs": [...]}` —
  chaque serveur est une **commande locale** (`type` « stdio » : `commande` +
  `args` + `env`) ou un **endpoint distant** (« sse »/« http » : `url` +
  `headers`). Format détaillé et exemple : [docs/04 §6](../../docs/04-specifications-agents.md).
- **Validé à la lecture** (`maestro.agents.mcp.McpStore.lire`) : une
  déclaration invalide est refusée avec sa cause exacte — échec de tâche
  propre, jamais un montage à moitié.
- Effet à l'exécution (`maestro/engine/executor.py`, relu **à chaud** à chaque
  tâche, comme les playbooks #78) : les serveurs sont montés par la **couche
  SDK** (`ModelProvider.run_agent`) sur les exécutions **outillées** de
  l'agent — le chemin texte n'expose aucun outil, MCP compris. Un serveur
  indisponible produit une erreur propre et tracée, **jamais relancée**
  (docs/04 §6.3).
- Affichage : fiche agent de la page `/catalogue` (lecture seule à ce lot).
- Racine remplaçable par `MAESTRO_MCP_DIR` (cf. `.env.example`).

Contrairement aux dépôts voisins (données d'exécution non commitées), les
déclarations écrites ici sont de la **configuration versionnée** : elles se
commitent avec le dépôt. Les **secrets n'y figurent jamais en clair** — les
valeurs d'`env`/`headers` référencent l'environnement (`${VARIABLE}`),
résolu au moment du montage (anticipe le chantier sécurité #102).

Tests (#103, lot final du parent #101) : différés — voir le ticket.
