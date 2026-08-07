# agent DevOps

CI/CD, infrastructure, préparation des déploiements. Voir
[`docs/04-specifications-agents.md`](../../docs/04-specifications-agents.md) (§3.4 : fiche du
rôle ; §1 : structure d'un playbook).

## Playbook

Le playbook du rôle — ce que le moteur charge tant que rien n'a été publié depuis l'UI — est un
**document Markdown livré avec le paquet** (#295) :
[`maestro/agents/playbooks_defaut/devops.md`](../../maestro/agents/playbooks_defaut/devops.md).
Il sert **les deux chemins** : `PLAYBOOK_DEFAUTS` (donc l'API et l'éditeur de la Control Tower)
et le prompt système de `DEVOPS_PROFILE`. Il porte :

- la **méthode** du métier — cadrer l'environnement cible en écrivant ses hypothèses quand rien
  ne les donne, écrire l'infrastructure **comme du code** (versions épinglées, rien qui dépende
  de l'état d'une machine, aucun secret en clair), valider à blanc ce qui peut l'être, puis
  produire un **runbook étape par étape** avec sa vérification et son plan de retour arrière ;
- le **régime sénior** commun à tous les rôles (`_socle.md`, #293) : l'outillage et la topologie
  se tranchent sans demander d'accord ; le compte-rendu porte toujours « Décisions &
  arbitrages » et « Recommandations » ;
- ses **garde-fous** : il **ne déploie jamais** vers un environnement réel et ne modifie aucune
  infrastructure existante — **le runbook et le plan de retour arrière *sont* le livrable**,
  c'est un humain qui les exécute. Choisir une topologie est réversible ; toucher à un
  environnement réel ne l'est pas. Aucun processus persistant ni service à l'écoute ne survit à
  la tâche.

Une version publiée depuis la page `/playbooks` prime dessus et s'applique **à chaud** (#78).
Invariants testés : [`tests/test_playbooks_defaut.py`](../../tests/test_playbooks_defaut.py).

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

**Particularité du rôle** (docs/04 §3.4) : l'agent **ne déploie pas** — il *prépare* le
déploiement, et les plafonds de ressources sont respectés. Il ne touche à aucun
environnement réel : un « déploiement » se matérialise en fichiers (configuration,
scripts, runbook, plan de retour arrière) et le compte-rendu liste explicitement ce qui
reste soumis à validation humaine avant toute application réelle.

Démo de bout en bout :

```bash
maestro-devops "Écris le pipeline CI qui lint et teste l'application à chaque push, avec cache des dépendances"
maestro-devops --keep --json "…"   # conserve l'espace de travail et sort le JSON
```
