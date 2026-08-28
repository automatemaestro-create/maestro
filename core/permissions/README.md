# Politiques de permissions par agent (ticket #110)

Un fichier par agent — `<agent>.json` — déclare sa politique **allow/ask/deny
par outil**, appliquée à l'exécution par le moteur et les workers (relue **à
chaud** à chaque tâche, comme les playbooks) :

```json
{
  "allow": [],
  "ask": {
    "mcp__slack__send_message": "humain",
    "Bash": "orchestrateur",
    "Grep": "auto"
  },
  "deny": ["mcp__slack__chat_delete"]
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
  contrôle censé le suspendre), et son appel est soumis à un arbitrage.
- **Qui arbitre est dans la politique** (#586) : `ask` s'écrit alors en objet
  `{"<outil>": "<décideur>"}`, avec trois crans et un seul défaut —
  - `auto` : personne n'est sollicité, l'appel passe. Il est **tracé** quand
    même (journal + fil temps réel), et c'est toute la différence avec un
    `allow`, qui passe en silence : le cran de ce qu'on veut voir sans vouloir
    l'arrêter. N'ayant besoin d'aucun décideur, il ne dépend d'aucun canal ;
  - `orchestrateur` : la machine tranche seule, sans réveiller personne. Elle
    peut refuser ; elle ne peut approuver **que** ce cran-ci.
    ⚠ **Ne pas s'en servir : ce cran est décidé mort** ([docs/31](../../docs/31-decision-cran-orchestrateur.md),
    #647). Il n'a **jamais eu de canal** — `Guardrails(orchestrateur=…)` n'est passé nulle
    part en production —, si bien qu'une entrée qui le pose rend aujourd'hui
    « aucun orchestrateur configuré — refus par défaut » : la politique promet une
    décision et rend un refus, sans que rien n'avertisse au chargement. Son retrait
    est #715 ; d'ici là, deux crans seulement — `auto` ou `humain` ;
  - `humain` : une personne, et personne d'autre. C'est le **défaut** — un cran
    non précisé escalade, il ne s'auto-approuve pas. L'orchestrateur n'est
    jamais consulté sur ces actes-là, donc son avis ne peut pas y devenir une
    approbation (EF-08/ENF-04 : refuser est le défaut sûr, approuver ne l'est
    jamais).

  Fail-safe porte par porte : pas de canal pour le cran demandé, ou canal en
  panne ⇒ **refus**. Un décideur inconnu dans le fichier est refusé avec sa
  cause — un garde-fou ne s'applique jamais à moitié.
- Liste absente = liste vide, `ask` comprise : un fichier écrit avant #580 se
  relit sous le régime d'hier. `ask` écrite en **liste** (`["Bash"]`, la forme
  d'avant #586) reste admise et vaut `humain` partout ; la relecture accepte les
  deux formes, l'écriture n'en produit qu'une — l'objet.
- Pas de fichier = pas de politique = comportement historique (les outils du
  profil, tous les serveurs MCP déclarés).

À l'exécution, un appel refusé produit un **refus propre** : l'agent reçoit le
motif et poursuit sa tâche (le run n'est jamais condamné), la violation est
tracée au journal (étape `<tâche>:refus-outil`) et au fil temps réel de la
Control Tower. La fiche agent du catalogue (UI) affiche la politique
effective, en lecture seule.

Un appel **arbitré** laisse la même trace, sous un statut à lui
(`arbitrage_outil`) : approuvé, refusé ou encore en attente, ce n'est pas un
refus. L'étape en nomme le **décideur** — « Outil arbitré (orchestrateur) » —,
et le tient de cette politique-ci et non du texte du motif : qui a tranché se
lit, il ne se déduit pas.

Ce dossier est **versionné** avec le dépôt (aucun secret n'y figure). Racine
remplaçable via `MAESTRO_PERMISSIONS_DIR` (cf. `.env.example`). Contrat et
sémantique : `maestro/agents/permissions.py` et `maestro/decideur.py`.
