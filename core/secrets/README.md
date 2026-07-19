# core/secrets — Coffre local des secrets par agent

Coffre des **secrets par agent** (ticket #109, parent #102) : les tokens des
intégrations MCP (Slack #105, GitLab #106…) sortent de l'environnement global
du process — chaque agent ne voit que **ses** secrets.

**Rien d'autre que ce README n'est versionné ici** (voir `.gitignore`) : un
coffre contient des secrets réels, il reste local au poste ou au worker.

## Fonctionnement

- Un fichier par agent : `<agent>.json`, de la forme
  `{"secrets": {"VARIABLE": "valeur"}}` — les noms de variables sont ceux que
  référencent les déclarations MCP de l'agent (`${VARIABLE}`,
  [core/mcp/README.md](../mcp/README.md)).
- **Opt-in par provisionnement** : tant qu'aucun coffre `<agent>.json`
  n'existe ici (ce README ne compte pas), la résolution des références
  `${VAR}` garde l'environnement du process (comportement historique #104).
  Dès le **premier coffre écrit**, le scoping est **strict pour tous les
  agents** : chacun ne résout que dans son coffre — un secret absent rend le
  serveur indisponible (échec propre), même si la variable traîne dans le
  shell. Migrez donc **tous** les agents à intégrations d'un coup.
- **Masquage automatique** : toute valeur servie par un coffre (ou résolue via
  `${VAR}`) est enregistrée au registre de rédaction
  (`maestro.telemetry.redact`) — masquée si elle réapparaît dans un journal,
  une trace Langfuse, un rapport ou un ticket.
- Relu **à chaud** à chaque tâche (comme les déclarations MCP) : tourner un
  secret vaut pour la tâche suivante, sans redémarrage.
- Racine remplaçable par `MAESTRO_SECRETS_DIR` (cf. `.env.example`). En mode
  distribué, workers et moteur doivent voir le même stockage.

## Exemple

`core/secrets/qa.json` (l'agent QA seul peut résoudre `${GITLAB_TOKEN}`) :

```json
{"secrets": {"GITLAB_TOKEN": "glpat-…"}}
```

Détail : [docs/18-secrets-par-agent.md](../../docs/18-secrets-par-agent.md).
Tests (#107, lot final du parent #102) : différés.
