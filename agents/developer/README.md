# agent Développeur

Écrit et modifie le code (endpoints, intégration front/back). Voir
[`docs/04-specifications-agents.md`](../../docs/04-specifications-agents.md) (§3.2 : fiche du
rôle ; §1 : structure d'un playbook).

## Playbook

Le playbook du rôle — ce que le moteur charge tant que rien n'a été publié depuis l'UI — est un
**document Markdown livré avec le paquet** (#295) :
[`maestro/agents/playbooks_defaut/developpeur.md`](../../maestro/agents/playbooks_defaut/developpeur.md).
Il sert **les deux chemins** : `PLAYBOOK_DEFAUTS` (donc l'API et l'éditeur de la Control Tower)
et le prompt système de `DEVELOPER_PROFILE`. Il porte :

- la **méthode** du métier — lire l'existant et ses conventions avant d'écrire, poser les
  options et leur coût, trancher la plus simple qui tienne le besoin, avancer par incréments,
  puis écrire les tests **et les lancer** ;
- le **régime sénior** commun à tous les rôles (`_socle.md`, #293) : l'architecture, les
  patrons et les bibliothèques se tranchent sans demander d'accord ; l'irréversible remonte ;
  le compte-rendu porte toujours « Décisions & arbitrages » et « Recommandations » ;
- ses **garde-fous** : il ne fusionne rien, n'entreprend aucune action destructrice hors de son
  espace de travail, et **propose** une réécriture de grande ampleur au lieu de la faire.

Une version publiée depuis la page `/playbooks` prime dessus et s'applique **à chaud** (#78).
Invariants testés : [`tests/test_playbooks_defaut.py`](../../tests/test_playbooks_defaut.py).

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
