# agent DevOps

CI/CD, infrastructure, déploiement. Voir `docs/04-specifications-agents.md` (§3.4 : fiche
et playbook du rôle, conformes au gabarit du §1).

## Runtime (ticket #67)

Au-delà de son identité dans le catalogue (`maestro.agents.catalog`), le DevOps dispose
d'un **runtime outillé** — un sous-agent du Claude Agent SDK qui traite une tâche
d'infrastructure **de bout en bout** (écrire la configuration → la valider localement →
préparer le déploiement) dans un **espace de travail isolé** :

- `maestro.agents.runtime.AgentRuntime` — le runtime **générique** (#35), paramétré
  par le profil du rôle (`maestro.agents.devops.DEVOPS_PROFILE`) ; il orchestre
  l'exécution et capture le livrable (`AgentOutcome` : compte-rendu + fichiers produits :
  configuration de pipeline, Dockerfile, scripts, runbook).
- `maestro.sandbox` — l'**isolation** : un répertoire temporaire dédié par tâche
  (niveau système de fichiers au POC ; conteneur Docker par tâche prévu ensuite).
- `maestro.providers.ModelProvider.run_agent` — la capacité d'exécution outillée à la
  frontière fournisseur (native de l'Agent SDK côté Claude), optionnelle et agnostique.

La boucle d'orchestration (`maestro.engine`) route les tâches assignées à `devops` vers
ce runtime : les fichiers produits remontent dans le `RunReport`. Si le fournisseur n'a
pas d'exécution outillée, le rôle retombe sur son livrable texte.

**Particularité du rôle** (docs/04 §3.4) : **tout déploiement passe par une validation
humaine**, et les plafonds de ressources sont respectés. Au POC, l'agent ne touche à
aucun environnement réel : un « déploiement » se matérialise en fichiers (configuration,
scripts, runbook) et le compte-rendu liste explicitement ce qui reste soumis à
validation humaine avant toute application réelle.

Démo de bout en bout :

```bash
maestro-devops "Écris le pipeline CI qui lint et teste l'application à chaque push, avec cache des dépendances"
maestro-devops --keep --json "…"   # conserve l'espace de travail et sort le JSON
```
