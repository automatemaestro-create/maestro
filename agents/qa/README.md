# agent QA / Testeur

Analyse du risque, tests, revue, verdict. Voir
[`docs/04-specifications-agents.md`](../../docs/04-specifications-agents.md) (§3.6 : fiche du
rôle ; §1 : structure d'un playbook).

## Playbook

Le playbook du rôle — ce que le moteur charge tant que rien n'a été publié depuis l'UI — est un
**document Markdown livré avec le paquet** (#295) :
[`maestro/agents/playbooks_defaut/qa.md`](../../maestro/agents/playbooks_defaut/qa.md). Il sert
**les deux chemins** : `PLAYBOOK_DEFAUTS` (donc l'API et l'éditeur de la Control Tower) et le
prompt système de `QA_PROFILE`. Il porte :

- la **méthode** du métier — partir du **risque** (ce qui casse le plus probablement, ce qui
  coûte le plus cher si ça casse), retenir pour chacun le niveau de test le moins cher qui
  l'attrape vraiment, **écrire ce qu'il laisse délibérément de côté**, exécuter pour de vrai et
  consigner les résultats **réels** ;
- la **hiérarchisation des défauts et le verdict** (#297) : chaque défaut porte sa sévérité —
  bloquant / majeur / mineur —, sa preuve et **la correction proposée** ; le verdict en découle
  et n'est **pas binaire** (`non conforme` s'il reste un bloquant, `conforme sous réserve` s'il
  reste un majeur, `conforme` sinon) ;
- le **régime sénior** commun à tous les rôles (`_socle.md`, #293) : la stratégie, le niveau de
  test et **la sévérité de chaque défaut** sont son jugement de métier et se tranchent sans
  demander d'accord ; le compte-rendu porte toujours « Décisions & arbitrages » et
  « Recommandations » ;
- ses **garde-fous** : il **évalue** et **ne réécrit pas** le livrable qu'on lui transmet — la
  correction se propose, l'appliquer revient au rôle producteur, même quand c'est une ligne.
  Ses **propres** tests, eux, sont son livrable et s'écrivent librement.

Une version publiée depuis la page `/playbooks` prime dessus et s'applique **à chaud** (#78).
Invariants testés : [`tests/test_playbooks_defaut.py`](../../tests/test_playbooks_defaut.py).

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
compte-rendu rend un **verdict explicite** (« conforme » / « conforme sous réserve » /
« non conforme », #297), étayé par ses constats et suivi des défauts par sévérité
décroissante. Au POC, « bloquer et renvoyer au Développeur » se matérialise par ce
verdict : la boucle n'a pas encore de rétro-boucle automatique, le verdict éclaire la
décision humaine — donc **ce qu'il n'écrit pas est perdu**.

Démo de bout en bout :

```bash
maestro-qa "Écris et exécute les tests d'une fonction de slugification (accents, ponctuation, troncature)"
maestro-qa --keep --json "…"   # conserve l'espace de travail et sort le JSON
```
