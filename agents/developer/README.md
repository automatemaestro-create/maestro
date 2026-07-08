# agent Développeur

Écrit et modifie le code (endpoints, intégration front/back). Voir `docs/04-specifications-agents.md`.

## Runtime (ticket #4, factorisé par #35)

Au-delà de son identité dans le catalogue (`maestro.agents.catalog`), le Développeur
dispose d'un **runtime outillé** — un sous-agent du Claude Agent SDK qui exécute une
tâche de développement **de bout en bout** (comprendre → écrire du code → produire des
fichiers) dans un **espace de travail isolé** :

- `maestro.agents.runtime.AgentRuntime` — le runtime **générique** (#35), paramétré
  par le profil du rôle (`maestro.agents.developer.DEVELOPER_PROFILE`) ; il orchestre
  l'exécution et capture le livrable (`AgentOutcome` : compte-rendu + fichiers produits).
- `maestro.sandbox` — l'**isolation** : un répertoire temporaire dédié par tâche
  (niveau système de fichiers au POC ; conteneur Docker par tâche prévu ensuite).
- `maestro.providers.ModelProvider.run_agent` — la capacité d'exécution outillée à la
  frontière fournisseur (native de l'Agent SDK côté Claude), optionnelle et agnostique.

Depuis #35, la boucle d'orchestration (`maestro.engine`) route les tâches assignées à
`developpeur` vers ce runtime : les fichiers produits remontent dans le `RunReport`.
Si le fournisseur n'a pas d'exécution outillée, le rôle retombe sur son livrable texte.

Démo de bout en bout :

```bash
maestro-dev "Écris une petite calculatrice en ligne de commande en Python"
maestro-dev --keep --json "…"   # conserve l'espace de travail et sort le JSON
```
