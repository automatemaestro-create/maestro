<!-- documentation: produit -->
# 45 — Une tâche n'est « Terminée » qu'une fois vérifiée en l'exécutant

**Date :** 2026-09-27. **Ticket :** #1177. **Jalon :** *Le run tient parole — il vérifie, se rattrape, et le fil agit*.

**Renverse :** « au POC, pas de rétro-boucle automatique » — le verdict de la QA éclairait une décision humaine et ne changeait rien au run ([docs/04 §3.6](./04-specifications-agents.md)). Maestro n'est pas un POC ([docs/41](./41-decision-maestro-juge-il-ne-bride-pas.md)).

---

## 0. Ce que ce document décide

1. **Une tâche n'est « Terminée » qu'une fois ses critères vérifiés en l'exécutant.** Le planificateur écrit des *critères de réussite* dans chaque tâche ; ils sont désormais **confrontés au livrable**, et non plus seulement écrits.
2. **Un critère qui ne tient pas revient à l'agent de la tâche, preuve à l'appui**, dans la limite du budget du run et jamais d'un nombre de tentatives figé.
3. **Le verdict « non conforme » de la QA entre dans la même boucle** : le livrable jugé non conforme repart à son rôle producteur, puis la QA le rejuge.

## 1. D'où vient la demande

Une tâche était verte **dès que son agent rendait quelque chose** (`maestro/engine/executor.py` : `if sortie or fichiers:` suffisait). Rien ne confrontait les critères au résultat, et le « non conforme » de la QA ne changeait rien. La personne voyait donc des tâches vertes dont les tests échouaient, ou dont le critère n'était pas tenu, et ne pouvait pas se fier au vert. C'est l'inverse d'un produit professionnel. Le constat vient du balayage « rien de figé » du moteur, le 2026-09-21.

## 2. Le mécanisme

**Le modèle propose, l'exécution tranche.** C'est la leçon déjà appliquée à l'outillage d'un projet (#1160, [docs/38](./38-decision-outillage-universel-du-projet.md)). Elle s'applique ici au livrable de chaque tâche (`maestro/engine/verification.py`).

1. **Un vérificateur**, appel modèle distinct de l'agent qui a produit, traduit les critères de la tâche en **contrôles** :
   - un critère qui se constate en exécutant devient une **commande** bash, jouée dans l'espace de travail de l'agent. Son **code de retour** tranche, jamais le texte de sa sortie ([docs/44](./44-decision-maestro-possede-ses-contrats.md)) ;
   - un critère qui ne se constate qu'en lisant devient une **lecture**, que le vérificateur juge sur le livrable, preuve à l'appui.
2. Les contrôles sont établis **une fois**, à la première livraison, puis **rejoués à l'identique** : l'agent est jugé sur ce qu'on lui a dit.
3. Une livraison dont un critère ne tient pas **revient à l'agent**, dans le même espace de travail où ses fichiers l'attendent. Elle porte la commande, son code, la fin de sa sortie, ou ce que la lecture n'a pas trouvé.
4. **Le vérificateur se relit avant de renvoyer qui que ce soit.** Une commande que Maestro ne sait pas lire (une substitution `$(…)`, une redirection d'entrée) est **réécrite**. Une commande qui n'a pas tenu est **contre-expertisée**, à chaque livraison : le défaut est-il dans le livrable, ou dans le contrôle ? Seul un contrôle qui ne constate pas son critère se réécrit, jamais pour qu'il passe, et le compte-rendu de l'agent, qui peut contester, est lu sans faire foi. Mesuré au banc (S6, 2026-09-27) : « aucun fichier ajouté hors du logo » lu comme « le logo seul dans le dossier », sur un projet qui contenait déjà trois fichiers.
5. **On ne conforme jamais le projet à un contrôle.** Dans ce même passage, l'agent de QA a déplacé les fichiers du projet pour faire tenir le contrôle faux. Le retour à l'agent l'interdit en toutes lettres : ne rien supprimer, déplacer ni modifier de ce qui existait avant la tâche pour faire tenir un contrôle, et dire en quoi il se trompe.
6. Chaque vérification est **consignée** (étape `<tâche>:verification`). Le fil la dit, et le **détail de la tâche** montre la dernière, contrôle par contrôle ([docs/05](./05-interface-control-tower.md)).

**Où l'on joue :** dans l'espace de travail de la tâche (répertoire jetable, worktree de la branche `maestro/<tâche>`, racine d'un projet non versionné). C'est l'environnement que l'agent a construit, donc le seul où « les tests passent » veuille dire quelque chose. Deux gardes, les mêmes que pour un agent : la **portée « projet »** (une commande qui sort du dossier n'est pas jouée, elle est dite) et un **délai par commande**, qui est un garde-fou et pas un verdict. Un livrable **texte**, qui n'a pas d'espace de travail, ne se vérifie que par des lectures.

## 3. Ce qui arrête la boucle

Pas un nombre de tentatives : un **fait**.

| Fait | Issue |
| --- | --- |
| Tous les critères tiennent | **Terminée**, la seule sortie verte |
| La correction ne fait tenir **aucun critère de plus** que la meilleure livraison précédente | **Échec motivé** : l'agent avait la preuve et n'en a rien tiré |
| Le **budget du run** est atteint pendant une correction | **Échec motivé** : la cause d'arrêt, suivie des dernières preuves |
| Un contrôle n'a **pas pu être joué** (portée, pas de bash) ou le vérificateur est **illisible** | **Échec motivé** : ce qui n'est pas vérifié n'est pas vérifié, et l'agent n'y peut rien |

Le nombre de critères est fini et chaque tour doit en gagner un, donc la boucle **finit toujours**, budget posé ou non. Un échec motivé dit combien de critères tiennent, après combien de livraisons, pourquoi la boucle s'est arrêtée, et chaque preuve. **Ce n'est jamais un vert.**

## 4. La QA dans la même boucle

Le vérificateur d'une tâche qui dépend d'autres tâches dit aussi si son livrable **rend un verdict « non conforme »** sur l'une d'elles. C'est le modèle qui le lit, jamais un lexique (#746). La boucle du run (`maestro/engine/loop.py`) enchaîne alors trois gestes :

1. le renvoi est **consigné** sur la tâche productrice, avec les défauts de la QA en constat ;
2. la tâche productrice est **réexécutée par son rôle**, sa description suivie des défauts bloquants et de leurs preuves ;
3. la QA **rejuge**, sur le livrable refait.

La QA **évalue et ne réécrit toujours pas** le livrable d'un autre rôle : elle le **renvoie**. La boucle s'arrête quand la QA juge conforme, quand le budget refuse une exécution, quand un producteur échoue sa propre reprise, ou quand une reprise ne lève **aucun défaut bloquant de plus**. Dans ce dernier cas, la tâche productrice finit en échec motivé, la revue en motif.

**Limites dites :**
- une tâche qui dépendait du livrable renvoyé **sans passer par la QA** a pu partir sur sa première version. La boucle ne rejoue que la paire producteur → QA, et le cas courant, une QA en bout de chaîne, n'en a pas ;
- le **mode durable** (Temporal) vérifie chaque livraison, mais son workflow ordonnance ses activités lui-même : le renvoi par la QA ne vit que dans la boucle en process, file Celery comprise (`TaskResult.renvois` traverse la file).

## 5. Ce qui ne bouge pas

- **L'arbitrage des actes** : un contrôle hors de la portée n'est pas joué, et il n'est pas soumis à une personne non plus.
- **La frontière d'écriture** : les contrôles constatent et ne modifient rien, et c'est le prompt du vérificateur qui l'exige.
- **Les états d'une tâche** ([docs/03 §3](./03-modele-de-donnees.md)) : aucun statut nouveau. La vérification et sa preuve s'y **ajoutent**.
- **« La QA évalue, elle ne réécrit pas »** : elle renvoie.

## 6. Ce que cela coûte

Chaque tâche paie **un appel modèle de plus**, celui qui établit ses contrôles, puis l'exécution de ses commandes. Une livraison corrigée repaie une session d'agent, et les lectures seulement sont rejugées par le modèle. Tout cela tombe **dans la mesure de la tâche**, et le plafond de dépense du run le borne comme le reste. Le moteur des vrais runs arme la vérification par défaut (`OrchestrationEngine.default`), comme la relance des aléas. `verification=False` l'éteint, et c'est un choix qu'on fait en le disant.
