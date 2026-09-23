# 42 — L'équipe s'ajuste au plan : proposée pendant le run, jamais recrutée sans accord

**Date :** 2026-09-23. **Instruite par :** `/idee` (#1013). **Consignée par :** #1229.
**Jalon :** *Le fil, un vrai interlocuteur — il sait tout, répond en direct, ne dérange que pour trancher* (neuf, échéance 2028-01-31).
**Tickets :**
- **#1221**, le fil de l'orchestrateur devient un interlocuteur. Lots, dans l'ordre : #1222, #1223, #1224, #1225.
- **#1226**, les validations Bash sur le propre travail d'un agent proposé.
- **#1227**, l'équipe confrontée au plan après la décomposition : c'est lui que cette note concerne.
- **#1228**, trancher une validation depuis toute vue d'un run.

---

## 0. Ce que ce document décide

**Une seule décision tombe.** Après la décomposition d'un run, l'orchestrateur **confronte l'équipe au plan**. Quand le plan appelle un rôle que l'équipe n'a pas, il le **propose** dans le fil. La personne l'accepte, et le rôle est recruté **pendant le run**, avant l'exécution. Ou elle le décline, et le run continue avec l'équipe qu'il a. Cela renverse les deux dernières puces de [docs/37 §3](./37-decision-equipe-sur-mesure.md) (§2).

La personne l'a demandé en toutes lettres (§1). Aucun renversement n'a été tranché à sa place.

**Deux choses ne sont pas renversées**, et la note le dit parce qu'on pourrait le croire (§4) :
- les validations Bash que la personne ne veut plus trancher relèvent d'une **ligne de politique**, que [docs/32 §8](./32-decision-cran-orchestrateur.md) prévoyait. Aucun décideur nouveau n'est introduit ;
- le streaming du fil de l'orchestrateur n'est pas une décision à défaire : c'est un **état décrit à tort** comme livré ([docs/05 §2.9](./05-interface-control-tower.md)).

## 1. D'où vient la demande

Le 2026-09-22, la personne reprend Maestro sur le projet `p1`, vidé la veille. L'orchestrateur propose l'équipe que l'analyse appelle, un seul développeur, et elle la valide. Puis elle demande *« une petite animation du logo Maestro »*. Le run `96d0c3482649` se décompose en quatre tâches : un logo stylisé, un script d'animation, un test de lancement, la documentation de la commande. Les quatre vont au développeur.

> « Pour autant, aucune modification de l'équipe n'a été suggérée, du coup un seul agent développeur a tout fait. »

Dans le même retour, la personne relève quatre autres points (§4, et la roadmap) :
- de nombreuses validations Bash à trancher ;
- un fil *« trop robotique et non informationnel »*, qui répond « regardez la doc » ;
- un streaming qui ne marche pas ;
- aucun moyen de trancher depuis la vue d'un run.

Elle déclare le tout prioritaire. L'analyse complète, avec le journal du run, la carte du code et chaque arbitrage, est le corps de #1229.

## 2. Ce qui est renversé

**Avant.** [docs/37 §3](./37-decision-equipe-sur-mesure.md), deux puces :
- *« un agent ne recrute pas pendant un run. Une tâche qu'aucun rôle ne sait prendre est signalée (#1041). Recruter reste un geste validé hors du run. »* ;
- *« D5 : le brief est validé avant la décomposition. L'équipe ne se forme pas dans un run, elle se forme à la création du projet. »*

Le code les applique à la lettre :
- la décomposition ne planifie qu'avec les compétences présentes (`maestro/orchestrator/playbook.md` l. 207-231 : *« tu n'emploies que les compétences de l'équipe… tu ne recrutes personne »*) ;
- un rôle manquant n'est que journalisé (`maestro/equipe/manque.py` : *« le recrutement se fait hors du run »*).

Un plan taillé à l'équipe ne manque donc **jamais** de rôle : le cas n'arrive pas.

**Après.** La décomposition planifie **pour le besoin** : une tâche nomme la compétence qu'elle demande, même quand l'équipe ne l'a pas. Entre la décomposition et l'exécution, l'orchestrateur confronte l'équipe au plan. Un écart devient une **proposition dans le fil**, portée par la carte d'équipe de #1146 : le rôle, sa raison, les tâches qu'il prendrait, son playbook généré. Accepter recrute en un geste, et l'exécution part avec l'équipe complétée. Décliner la laisse partir avec l'équipe qu'elle a, et le fil le dit.

**Pourquoi.**
- **La règle protégeait la validation, pas l'absence de recrutement.** Recruter reste un geste validé par la personne. Ce qui change est le **moment** où on le lui propose : celui où le besoin devient visible. À la création du projet, personne ne savait que ce projet ferait un jour une animation de logo.
- **Une règle juste isolément, un résultat bridé.** docs/37 §3 voulait que l'équipe se forme avant le travail. Couplée à une décomposition qui ne voit que l'équipe, elle faisait taire le besoin au lieu de le montrer. C'est exactement ce que [docs/41](./41-decision-maestro-juge-il-ne-bride-pas.md) écarte : *Maestro juge, il ne bride pas*.
- **#1181 avait déjà ouvert la porte**, pour un rôle absent découvert en cours d'exécution, sans l'écrire en décision. Cette note la couvre pour les deux moments : #1227 à la décomposition, #1181 en cours d'exécution. Il n'y a qu'un chemin de recrutement (`recruter`, `EquipeDansLeFil`).

## 3. Ce qui ne bouge pas

- **Rien n'est recruté sans accord** (#1040). Sans réponse, **rien n'est recruté** : le run continue avec l'équipe qu'il a et le dit, comme une question échue continue sur son hypothèse ([docs/37 §4.4](./37-decision-equipe-sur-mesure.md)). Un recrutement par défaut serait une dépense et une autorisation que personne n'a décidées.
- **Un agent ne recrute jamais** ([docs/31 §3.5](./31-decision-surface-ecriture-agents.md)). C'est l'orchestrateur qui propose, et la personne qui décide. La surface d'écriture des agents est inchangée.
- **D5 : le brief est validé avant la décomposition.** La proposition vient après, et ne rouvre pas le brief.
- **Les autorisations d'un rôle recruté** sont proposées avec leur raison et validées avec lui ([docs/37 §4.3](./37-decision-equipe-sur-mesure.md)), exactement comme à la création du projet.
- **[docs/32](./32-decision-cran-orchestrateur.md) : aucune IA ne juge l'appel d'outil d'une autre IA.** Rien dans cette note ne touche la couche de permissions.

## 4. Ce qui n'est pas renversé, et pourquoi on pourrait le croire

### 4.1 Les validations Bash : la mesure que docs/32 attendait

Pendant le run `96d0c3482649`, la personne a tranché **14 validations Bash**, et les a **toutes approuvées**. Elles portaient sur :
- `mkdir` ;
- `python` sur les fichiers que l'agent venait d'écrire ;
- `pytest` ;
- le ménage des `__pycache__` que ses exécutions avaient produits.

Aucune ne sortait du projet. La frontière d'écriture, elle, a refusé deux écritures hors racine. La cause est `_cran_execution` (`maestro/equipe/proposition.py`) : il propose `Bash: ask, humain` à un rôle dès qu'aucune commande de ce rôle n'a été lue dans le projet, donc **toujours** sur un projet neuf.

[docs/32 §8](./32-decision-cran-orchestrateur.md), porte 1, attendait cette mesure et en donnait d'avance le sens : *« un taux d'approbation proche de 1 ne dit pas “il faut une machine pour trancher” — il dit que ces actes-là méritaient `auto`, ce qui coûte une ligne de politique »*.

C'est le remède de #1226 :
- une autorisation d'exécuter **dans le projet**, proposée avec sa raison à la validation de l'équipe ;
- et, si le verdict dépend des arguments, une **portée** (porte 2), jamais un modèle qui juge l'appel.

Le décideur ne change pas. La personne continue de trancher ce qui **sort** du projet, et ce qui détruit ce qu'elle y a posé.

### 4.2 Le streaming : un état décrit à tort

[docs/05 §2.9](./05-interface-control-tower.md) décrit « La réponse s'écrit en direct (#695) » comme livré pour les trois surfaces de fil. Le transport l'est. Mais l'orchestrateur rend un JSON `{verdict, objectif, reponse}` qu'il attend en entier (`maestro/controltower/orchestration.py`, `_juger`), puis l'écrit **en un seul incrément**. Seul le chat direct avec un agent streame. Aucune décision n'est à défaire : un état est à rétablir (#1222), et le document le signale d'ici là.

## 5. Les arbitrages rendus par `/idee`, à contredire au besoin

1. **Le renversement est tenu pour voulu.** La personne a dit attendre une proposition d'équipe après la décomposition, pas avant le run. Aucune question ne lui a été posée.
2. **Sans réponse, le run continue avec l'équipe actuelle** (§3). L'autre issue, attendre la personne comme on attend un brief, bloquerait un run pour une amélioration, alors que le plan reste exécutable par l'équipe qu'il a.
3. **Un moment, pas deux mécanismes.** #1227 (à la décomposition) et #1181 (en cours d'exécution) partagent la carte et le chemin de recrutement.
4. **Bash sans renversement** (§4.1) : la mesure est celle de la porte 1 de docs/32, et son remède est celui qu'elle nommait.

## 6. Ce qui rouvrirait la décision

- **Des propositions qui noient la personne.** On le mesure par le nombre de propositions d'équipe par run et la part déclinée. Le remède serait un seuil (ne proposer que ce que le plan ne peut pas faire sans), pas le retour à « hors du run ».
- **Un recrutement accepté qui ne sert pas.** Un rôle recruté pendant un run, puis sans tâche, dirait que la décomposition surestime le besoin. Le remède est dans la décomposition, pas dans la règle.

## 7. Où cette décision est écrite ailleurs

Un renvoi ⚠ vers cette note est posé à l'endroit de chaque décision renversée, et de chaque état décrit à tort :
- [docs/37 §3](./37-decision-equipe-sur-mesure.md), les deux puces renversées ;
- [docs/05 §2.9](./05-interface-control-tower.md), « La réponse s'écrit en direct » ;
- [docs/32 §8](./32-decision-cran-orchestrateur.md), porte 1 : la mesure existe désormais ;
- [docs/06](./06-roadmap.md), la section du jalon.

Le code qui écrit encore la règle d'avant se réécrit avec **#1227** : `maestro/equipe/manque.py`, `maestro/orchestrator/playbook.md` et le prompt de décomposition. Ce n'est pas le rôle de cette note.
