# agent Base de données

Schéma, migrations, requêtes. Voir `docs/04-specifications-agents.md`.

## Runtime (ticket #5, factorisé par #35)

Au-delà de son identité dans le catalogue (`maestro.agents.catalog`), la Base de données
dispose d'un **runtime outillé** — un sous-agent du Claude Agent SDK qui traite une tâche
BDD **de bout en bout** (concevoir un schéma → écrire des migrations → optimiser des
requêtes → produire des fichiers) dans un **espace de travail isolé** :

- `maestro.agents.runtime.AgentRuntime` — le runtime **générique** (#35), paramétré par
  le profil du rôle (`maestro.agents.database.DATABASE_PROFILE`) ; il orchestre
  l'exécution et capture le livrable (`AgentOutcome` : compte-rendu + fichiers produits :
  schéma, migrations, requêtes).
- `maestro.sandbox` — l'**isolation** : un répertoire temporaire dédié par tâche (niveau
  système de fichiers au POC ; conteneur Docker par tâche prévu ensuite).
- `maestro.providers.ModelProvider.run_agent` — la capacité d'exécution outillée à la
  frontière fournisseur (native de l'Agent SDK côté Claude), optionnelle et agnostique.

Depuis #35, la boucle d'orchestration (`maestro.engine`) route les tâches assignées à
`bdd` vers ce runtime : les fichiers produits remontent dans le `RunReport`. Si le
fournisseur n'a pas d'exécution outillée, le rôle retombe sur son livrable texte.

**Garde-fou du rôle** (docs/04 §2) : l'agent ne cible jamais une base réelle ou de
production — toute vérification se fait contre une base jetable créée dans l'espace de
travail (ex. un SQLite local). Toute opération destructive (DROP, perte de données) est
signalée dans le compte-rendu comme nécessitant une **validation humaine** ; l'agent la
propose, il ne l'applique pas.

Démo de bout en bout :

```bash
maestro-bdd "Conçois le schéma d'un blog (auteurs, articles, commentaires) et la migration initiale"
maestro-bdd --keep --json "…"   # conserve l'espace de travail et sort le JSON
```
