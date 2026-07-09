# agent QA / Testeur

Tests, validation, revue. Voir `docs/04-specifications-agents.md` (§3.6 : fiche et
playbook du rôle, conformes au gabarit du §1).

## Runtime (ticket #45)

Au-delà de son identité dans le catalogue (`maestro.agents.catalog`), le QA dispose
d'un **runtime outillé** — un sous-agent du Claude Agent SDK qui traite une tâche de
qualité **de bout en bout** (écrire les tests → les exécuter → faire la revue →
rendre un verdict) dans un **espace de travail isolé** :

- `maestro.agents.runtime.AgentRuntime` — le runtime **générique** (#35), paramétré
  par le profil du rôle (`maestro.agents.qa.QA_PROFILE`) ; il orchestre l'exécution et
  capture le livrable (`AgentOutcome` : compte-rendu + fichiers produits : tests,
  rapport de revue).
- `maestro.sandbox` — l'**isolation** : un répertoire temporaire dédié par tâche
  (niveau système de fichiers au POC ; conteneur Docker par tâche prévu ensuite).
- `maestro.providers.ModelProvider.run_agent` — la capacité d'exécution outillée à la
  frontière fournisseur (native de l'Agent SDK côté Claude), optionnelle et agnostique.

La boucle d'orchestration (`maestro.engine`) route les tâches assignées à `qa` vers ce
runtime : les fichiers produits remontent dans le `RunReport`. Si le fournisseur n'a
pas d'exécution outillée, le rôle retombe sur son livrable texte.

**Particularité du rôle** (docs/04 §3.6) : le QA **évalue** les livrables des tâches
dont il dépend (transmis via le tableau noir), il ne les réécrit pas — son
compte-rendu rend un **verdict explicite** (« conforme » / « non conforme »), étayé
par ses constats. Au POC, « bloquer et renvoyer au Développeur » se matérialise par ce
verdict : la boucle n'a pas encore de rétro-boucle automatique, le verdict éclaire la
décision humaine.

Démo de bout en bout :

```bash
maestro-qa "Écris et exécute les tests d'une fonction de slugification (accents, ponctuation, troncature)"
maestro-qa --keep --json "…"   # conserve l'espace de travail et sort le JSON
```
