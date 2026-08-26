# Politiques de permissions par agent (ticket #110)

Un fichier par agent — `<agent>.json` — déclare sa politique **allow/ask/deny
par outil**, appliquée à l'exécution par le moteur et les workers (relue **à
chaud** à chaque tâche, comme les playbooks) :

```json
{
  "allow": [],
  "ask": ["mcp__slack__send_message"],
  "deny": ["Bash", "mcp__slack__chat_delete"]
}
```

- Les entrées des trois listes ont la même forme : un **outil intégré**
  (`Read`, `Write`, `Edit`, `Glob`, `Grep`, `Bash`), un **serveur MCP entier**
  (`mcp__<serveur>` — refusé en entier, il n'est alors jamais monté et ses
  secrets jamais résolus) ou un **outil MCP précis** (`mcp__<serveur>__<outil>`).
- **`deny` l'emporte sur `ask`, qui l'emporte sur `allow`** (#580) ; `allow`
  vide = tout ce que le profil du rôle expose est permis ; `allow` non vide =
  liste fermée, tout le reste est refusé — sauf ce que `ask` cite, qui est
  **arbitré et non refusé**, sans quoi fermer sa liste `allow` suffirait à
  rendre le cran du milieu lettre morte.
- Un outil cité en **`ask`** n'est pas interdit : il reste **monté** sur la
  session (un outil retiré avant l'ouverture n'atteindrait jamais le point de
  contrôle censé le suspendre), et son appel est soumis à un arbitrage humain.
- Liste absente = liste vide, `ask` comprise : un fichier écrit avant #580 se
  relit sous le régime d'hier.
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
