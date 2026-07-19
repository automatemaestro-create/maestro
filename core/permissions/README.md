# Politiques de permissions par agent (ticket #110)

Un fichier par agent — `<agent>.json` — déclare sa politique **allow/deny par
outil**, appliquée à l'exécution par le moteur et les workers (relue **à
chaud** à chaque tâche, comme les playbooks) :

```json
{
  "allow": [],
  "deny": ["Bash", "mcp__slack__chat_delete"]
}
```

- Les entrées désignent un **outil intégré** (`Read`, `Write`, `Edit`, `Glob`,
  `Grep`, `Bash`), un **serveur MCP entier** (`mcp__<serveur>` — il n'est
  alors jamais monté, ses secrets jamais résolus) ou un **outil MCP précis**
  (`mcp__<serveur>__<outil>`).
- **`deny` l'emporte toujours** ; `allow` vide = tout ce que le profil du rôle
  expose est permis ; `allow` non vide = liste fermée, tout le reste est
  refusé.
- Pas de fichier = pas de politique = comportement historique (les outils du
  profil, tous les serveurs MCP déclarés).

À l'exécution, un appel refusé produit un **refus propre** : l'agent reçoit le
motif et poursuit sa tâche (le run n'est jamais condamné), la violation est
tracée au journal (étape `<tâche>:refus-outil`) et au fil temps réel de la
Control Tower. La fiche agent du catalogue (UI) affiche la politique
effective, en lecture seule.

Ce dossier est **versionné** avec le dépôt (aucun secret n'y figure). Racine
remplaçable via `MAESTRO_PERMISSIONS_DIR` (cf. `.env.example`). Contrat et
sémantique : `maestro/agents/permissions.py`.
