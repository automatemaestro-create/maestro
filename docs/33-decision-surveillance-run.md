# 33 — L'orchestrateur surveille son run : note de décision

> Ticket #651. Décision datée du **2026-08-28**. Faits mesurés sur `origin/main` à `1bef04a` ;
> note rebasée sur `9754023`, qui a livré entre-temps les trois voisins qu'elle cite — #354, #647
> et #355. Aucune mesure n'en est changée : ce qui a bougé est nommé au §4.1 et au §11.
>
> **Quatre arbitrages, rendus sur quatre mesures.** ① « **Blocage** » nomme deux situations
> **opposées** — une tâche bloquée (le run *avance*, en contournant une branche morte) et un run
> suspendu (le run *n'avance pas du tout*) —, et une seule est un cas de surveillance ; le
> vocabulaire du dépôt les sépare déjà, le mot non. ② Des trois niveaux, **seul *alerter* est
> ouvert** : *voir* est #355, et *décider* est **vide après soustraction**. ③ La boucle est une
> **règle déterministe sur un réveil qui existe déjà**, et elle ne peut pas vivre chez
> l'orchestrateur — un veilleur qui meurt avec ce qu'il veille n'est pas un veilleur. ④ L'alerte
> **ne remonte pas par le chemin de #647**, et c'est la règle de #647 appliquée une troisième fois.
>
> Le titre du ticket est donc à moitié faux, et c'est le résultat : l'orchestrateur ne peut pas
> surveiller *son* run. Ce qui surveille doit lui survivre.

---

## 1. La question, et l'état du dispositif

Un run **émet** tout ce qu'il faudrait pour être surveillé. Le vocabulaire d'événements compte
**18 types** ([`events.py:84-156`](../maestro/controltower/events.py)), le blocage aval est explicite
(#43), la pause est une porte (#477), la mort d'un hôte se lit à son battement (#348), les causes
d'arrêt sont classées (#479), et depuis #571 un run **dit lui-même** qu'il attend un humain.

Et personne ne l'écoute côté machine. Le bus a **cinq** abonnés dans tout `maestro/`, et la
répartition est le fait qui compte :

| Abonné | Ce qu'il fait |
| --- | --- |
| [`app.py:732`](../maestro/controltower/app.py) — la pompe | **Le seul généraliste** : il projette, journalise et diffuse. Il ne **juge** rien |
| [`validation.py:136`](../maestro/controltower/validation.py) | attend `validation.decision` **pour sa propre tâche** |
| [`brief.py:124`](../maestro/controltower/brief.py) | attend `brief.decision` **pour son propre run** |
| [`brief.py:230`](../maestro/controltower/brief.py) | attend `brief.reponses` **pour son propre run** |
| [`hote_detache.py:1153`](../maestro/controltower/hote_detache.py) | attend annulation et pause **pour son propre run** |

Les quatre derniers sont des **rendez-vous point à point** : quelqu'un a posé une question et attend
sa réponse. Aucun ne lit le flux pour en tirer un jugement. Et le bus est **éphémère par
construction** — « pub/sub, pas de rejeu » ([`events.py:19-21`](../maestro/controltower/events.py)) —,
donc un abonné qui arrive en retard ne voit rien : c'est écrit, et c'est ce qui a obligé les trois
arbitres à s'abonner **avant** de publier (`validation.py:139-143`, `brief.py:126-131`, `:232-235`).

La question du ticket est donc : brancher un lecteur, et lui donner quoi faire.

## 2. Ce que la mesure dit — quatre faits

Quatre mesures prises sur le dépôt à `1bef04a`. Elles portent la décision, et aucune n'était acquise
avant de regarder.

**Fait 1 — il existe UN seuil temporel qui rend un verdict dans tout `maestro/`, et il répond à
l'autre question.** Le balayage des seuils du paquet en rend trois familles : un seuil de
**confiance** de routage (`SEUIL_CONFIANCE_DEFAUT = 0.6`,
[`router.py:40`](../maestro/router/router.py)), des plafonds de **dépense** et de **taille**
(`guardrails.py`, `costs.py`, `extraction.py`), et **un seul seuil de temps produisant un jugement** :
`SEUIL_ORPHELIN_S = 1800.0` ([`battement.py:100`](../maestro/controltower/battement.py)).

Or ce seuil-là ne dit pas ce qu'on lui prête, et le module le dit lui-même en gras :

> ⚠ La vitalité **n'est donc pas** ce qui distingue un run bloqué d'un run qui travaille, et c'est
> précisément le constat de #568 : pendant treize minutes d'attente d'arbitrage, le cœur battait et
> le verdict disait `vivant`, **à raison**. Ce que la vitalité répond est « son hôte est-il encore
> là ? ». — [`battement.py:137-141`](../maestro/controltower/battement.py)

**Le battement prouve que l'HÔTE est vivant, jamais que le RUN avance.** Ce sont deux questions, et
une seule a une réponse aujourd'hui.

**Fait 2 — la donnée de l'autre question est déjà là : datée, servie, affichée — et jamais jugée.**
`EtatExecution.attente_depuis` existe depuis #321
([`state.py:448`](../maestro/controltower/state.py)), il est posé et levé sur les **trois** attentes
humaines (`state.py:1248`, `:1308`, `:1442` ; levé `:1229`, `:1275`, `:1337`, `:1479`), il est
sérialisé (`state.py:563`) et il est rendu à l'écran en **huit** endroits — `PanneauBriefs.tsx:89`,
`EtatRun.tsx:287`, `ValidationBriefs.tsx:108`, `ValidationBrief.tsx:217`, `CadrageDansLeFil.tsx:330`,
`FilDeCadrage.tsx:93`, et deux clés de rendu.

**Les huit passent par `formatHeureRelative`.** Pas un seul ne le compare à quoi que ce soit.
L'écran écrit « il y a 3 heures » en gris, et c'est tout : le fait est **lisible**, il n'est pas
**opposable**. Le dépôt a payé quatre tickets pour rendre l'attente visible — #348 le battement,
#320/#321/#571 les trois statuts d'attente et leur ancienneté commune — et **aucun** pour la juger.

**Fait 3 — aucune boucle périodique du dépôt n'appelle un modèle, et le dépôt refuse déjà par écrit
un appel modèle rejoué dans un moteur qui reprend.** L'inventaire des boucles à cadence fixe donne :
le battement de l'API (`executions.py:1271-1278`, 30 s), le `CoeurRun` de chaque hôte
(`battement.py:373-375`), le heartbeat Temporal (`activities.py:199-203`), le sondage de démarrage
(`hote_detache.py:668-685`), le backoff de relance (`executor.py:953`) et le sondage MCP
(`claude.py:884`). **Aucune** ne sollicite un fournisseur. Et le refus est déjà écrit, mot pour mot,
dans le CLI :

> `--brief` n'est pas géré en mode `--durable` : l'étape de cadrage (#318) n'est pas encore une étape
> du workflow Temporal — elle serait **rejouée à chaque reprise, et payée à chaque fois**.
> — [`cli.py:340-347`](../maestro/engine/cli.py)

C'est le précédent exact du §5 : le dépôt a déjà refusé, une fois, de mettre un appel modèle
d'orchestration dans une boucle qui repasse.

**Fait 4 — le seul canal hors écran coûte un appel modèle agentique par envoi, et n'est pas branché
sur le chemin de production.** `maestro/supervision.py` (#105) ne poste pas sur Slack : il confie une
**mission de publication** à un agent outillé via `AgentRuntime.execute`
([`supervision.py:204`](../maestro/supervision.py)) — le même chemin d'exécution qu'une tâche, avec
montage MCP et tours d'outils. Il est branché sur **deux** événements seulement (fin de run
`:147-159`, validation en attente `:161-182`), son unique appelant est
[`cli.py:364-372`](../maestro/engine/cli.py) derrière `--notifier <agent>`, et il est **refusé** avec
`--queue` (`cli.py:308-315`) comme avec `--durable` (`cli.py:324-332`). Un run lancé depuis la
Control Tower — le chemin par défaut — n'en a donc **aucun** : ni `executions.py:1440` ni
`hote_detache.py:1046-1050` ne l'enveloppent.

## 3. Arbitrage ① — « blocage » nomme deux situations opposées

> **Verdict : une seule des deux est un cas de surveillance.** L'autre est déjà servie, et la
> confondre avec la première ferait construire un mécanisme pour un fait qui n'en demande aucun.

### 3.1 Les deux blocages

Le ticket demande de surveiller « blocages, pauses, agents morts ». Le premier mot en recouvre deux,
et ils vont dans des **sens contraires** :

| | une **tâche** bloquée | un **run** suspendu |
| --- | --- | --- |
| Le nom dans le code | `STATUT_BLOQUEE` ([`executor.py:76`](../maestro/engine/executor.py)) | `STATUTS_EXECUTION_EN_ATTENTE` ([`state.py:130`](../maestro/controltower/state.py)) |
| Ce qui se passe | une dépendance a échoué, la tâche ne s'exécutera pas | rien ne bouge, le moteur attend un geste humain |
| Le run, pendant ce temps | **il avance** — il contourne la branche morte et finira | **il n'avance pas du tout** |
| Est-ce terminal ? | oui, c'est un état final de tâche (`state.py:179`) | non — le run est en vol, simplement suspendu (`state.py:96`) |
| Déjà compté en direct ? | **oui** — `Progression.bloquees` ([`progression.py:121`](../maestro/controltower/progression.py)), servi sur chaque résumé (`executions.py:472`) | l'attente est **datée** mais **jamais jugée** (fait 2) |

Une tâche bloquée est un run qui **finit moins bien qu'espéré**. C'est un signal de qualité, il se
lit dans le rapport, et il est déjà compté à chaque instant. Un run suspendu est un run qui **ne
finit pas**, et c'est le seul des deux dont personne ne sait qu'il dure.

⚠ La cascade elle-même n'a pas besoin d'être instrumentée, et il faut le dire parce que le ticket
laisse la porte ouverte. Elle est **émergente** et non calculée : le point de décision est
[`loop.py:649`](../maestro/engine/loop.py) (`insatisfaites = [dep for dep in dependances if not
dep.ok]`), il n'existe **aucune fermeture transitive** — `_dependants_directs` (`loop.py:955`) existe
mais sert au handoff (`loop.py:674-677`) — et chaque descendante découvre son sort quand ses propres
dépendances se résolvent. Nommer « ce sous-arbre est condamné » demanderait de **calculer** ce que le
moteur se contente de laisser arriver. On paierait un calcul pour anticiper de quelques minutes un
compte que le résumé rend déjà.

### 3.2 Les trois mots du ticket, une fois démêlés

- **Agents morts** → c'est `vitalite`, **livré** (#348), rendu par `PanneauRunsPerdus.tsx:48-84` avec
  son geste (« Reprendre »). Rien à faire.
- **Blocages** → coupé en deux ci-dessus. La tâche bloquée est *voir* ; le run suspendu est le sujet.
- **Pauses** → **écartées, et c'est une décision.** Une pause est le seul de ces états où **quelqu'un
  a déjà décidé** : elle est posée par un geste humain explicite (`POST /api/executions/{run_id}/pause`,
  [`app.py:1393`](../maestro/controltower/app.py)), elle est servie (`EtatExecution.en_pause`,
  `state.py:480`) et l'écran en fait déjà un régime à part (groupe « En pause » d'`EtatDesRuns`).
  Alerter dessus, ce serait alerter sur l'exercice d'une commande qu'on offre.

  ⚠ Le prix est nommé plutôt que masqué : `en_pause` est un **booléen sans date** (`state.py:480`),
  donc « en pause depuis N » n'est **pas calculable** aujourd'hui, et une pause oubliée tient un hôte
  et un budget sans que rien ne le dise. C'est une porte du §10, pas un lot : la dater coûte un champ
  dans la projection, dans le résumé et dans le type TypeScript, et **aucune mesure ne dit
  aujourd'hui qu'une pause a jamais été oubliée** — alors que le run suspendu, lui, a son incident
  chiffré (§4).

## 4. Arbitrage ② — des trois niveaux, un seul est ouvert

> **Verdict : *voir* est fait (#355), *alerter* est ouvert, *décider* est vide.** L'ordre n'est pas
> un séquencement de confort : c'est ce qui reste après soustraction.

### 4.1 *Voir* — #355, et la frontière tient en une phrase

#355 est **livré** (merge `9754023`, pendant l'instruction de ce cadrage) et se déclarait
« **lecture seule, et rien d'autre** ». Son troisième critère — « une tâche bloquée et une tâche en
attente de validation humaine se distinguent d'une tâche en cours, **à l'œil**, sans ouvrir de
détail » — est exactement le niveau *voir*, et il est désormais tenu.

> **#355 rend l'attente lisible pour qui regarde. L'alerte est ce qui reste vrai quand personne ne
> regarde.**

Les deux ne se recouvrent pas et se servent l'un l'autre : la frise recevra le verdict comme une
entrée de plus, sans travail, puisqu'elle agrège déjà ce pont. Sa livraison **ne change rien à cette
décision** — elle rend le niveau *voir* acquis, ce qui rend le niveau *alerter* d'autant plus net :
tout ce qui manque désormais est le **jugement**, jamais un capteur ni un écran de plus.

### 4.2 *Alerter* — ce que ça coûte, précisément

Ce que le niveau demande, une fois le §5 admis : **une fonction pure, une constante, et un champ de
plus sur un résumé déjà servi.** Zéro appel modèle, zéro processus, zéro type d'événement, zéro
migration. Le détail est au §5.3 ; le point ici est que ce niveau est le **moins cher des trois** et
le seul qu'aucun autre ticket ne porte.

### 4.3 *Décider* — vide après soustraction, et c'est une démonstration, pas un renoncement

Le ticket range sous *décider* quatre verbes : **relancer, réassigner, replanifier, escalader**. Ils
tombent un par un, et aucun ne tombe par prudence :

| Verbe | Ce qui le retire | Preuve |
| --- | --- | --- |
| **Relancer** | déjà fait, et le refaire serait **nuisible** | `retry.py` relance tout **sauf** quatre exceptions ([`retry.py:77-94`](../maestro/engine/retry.py), classification par exclusion). Un superviseur qui relance redouble la relance transitoire — le ticket le nomme lui-même dans ses notes techniques |
| **Réassigner** | refusé par #354 §3.4 | l'affectation vient du routeur et du contrôle de capacité, relus à chaud (`loop.py:461`) ; la seule « réassignation » du produit est **cosmétique** — elle réécrit la projection (`state.py:1009-1038`) et ne rejoue aucune exécution |
| **Replanifier** | refusé par #354 §5 | le graphe ne se modifie pas en cours de run (§6). Le produit n'offre que `relancer`, qui est **un nouveau run** (`executions.py:807-819`) |
| **Escalader** | **reste** — mais ce n'est pas une décision sur le plan | c'est prévenir quelqu'un. Un routage, pas un jugement |

Il ne reste donc, sous *décider*, **aucun verbe qui demande un modèle**. Et ce n'est pas une
coïncidence de découpage : c'est la conséquence directe de #354 (le plan est figé) et de #647 (il n'y
a plus de décideur machine nulle part). Les trois cadrages du milestone convergent sur la même
frontière, chacun par son chemin.

⚠ Une objection à traiter, parce qu'elle est la seule qui tienne debout : **filtrer** les alertes ne
serait-il pas un jugement utile ? « Cette attente de 20 minutes mérite-t-elle de déranger
quelqu'un ? » Non — et le remède est plus court que la question. Une règle qui crie trop se règle en
**déplaçant son seuil**, ce qui coûte une constante ; y ajouter un modèle pour trier ses propres cris
coûte un appel par cri, et rend le déclenchement non reproductible. C'est le refus de #586
(« un LLM qui garde un LLM ») appliqué au capteur au lieu du garde-fou.

## 5. Arbitrage ③ — la forme de la boucle

> **Verdict : une règle déterministe, évaluée sur un réveil qui existe déjà, dans le processus de
> l'API — jamais chez l'orchestrateur, jamais dans l'hôte du run, jamais un modèle en veille.**

### 5.1 Pourquoi pas chez l'orchestrateur — et pourquoi le titre du ticket est à moitié faux

`Orchestrator` expose quatre membres — `__init__` (`orchestrator.py:60`), `default` (`:64`), `plan`
(`:77`), `brief` (`:95`) —, il est **sans état**, et il est consulté **deux fois, avant la boucle** :
`loop.py:851` (plan) et `loop.py:917` (brief), quand les tâches démarrent à `loop.py:684-686`. Il
survit à la planification mais n'est plus jamais rappelé, et le moteur qui le porte est une
**variable locale** partout en production (`executions.py:1444`, `hote_detache.py:1052`,
`cli.py:424`).

Le rendre réentrant serait donc déjà un chantier. Mais ce n'est pas l'obstacle : l'obstacle est que
**l'orchestrateur vit dans le run et meurt avec lui**.

> **Un veilleur qui meurt avec ce qu'il veille n'est pas un veilleur.**

Or l'un des trois cas que le ticket demande de couvrir est précisément « **agents morts** ». Une
surveillance hébergée dans le processus du run est aveugle au seul événement qu'elle ne peut pas se
permettre de rater : sa propre mort. C'est aussi ce qui écarte l'hôte détaché
(`hote_detache.py:1019-1063`), qui est pourtant le candidat le plus tentant — il tient déjà le bus,
la porte et le moteur pendant toute la durée du run. Il les tient **tant qu'il est vivant**, et c'est
exactement la clause qui le disqualifie.

### 5.2 Où elle vit : le réveil qui existe déjà

`ServiceExecutions._battre` ([`executions.py:1249-1278`](../maestro/controltower/executions.py))
tourne dans le processus de l'**API**, toutes les 30 s, et fait déjà, dans cet ordre : un ramassage
des hôtes morts (#446), puis un battement **par run en vol non terminal**. Sa docstring pose le
principe qui nous invite :

> Le **ramassage** (#446) partage ce réveil […]. Il n'a pas de tâche à lui pour la raison qui a fait
> n'en donner qu'une au cœur — **ce qui se fait à chaque période se fait sur un seul réveil**.
> — `executions.py:1265-1269`

Trois propriétés en font le bon endroit, et aucune n'appartient aux autres candidats : il **survit
aux runs** qu'il regarde ; il itère déjà **exactement** l'ensemble utile ; et il vit dans le
processus qui tient la **projection**, c'est-à-dire le seul endroit où `attente_depuis` est connu
pour tous les runs à la fois.

⚠ **Portée annoncée, comme celle de `gc` et de `reconcile-workflow`** ([docs/10 §9.2](./10-workflow-git.md)) :
la surveillance couvre les runs **connus d'une API vivante**. Un `maestro-run` en ligne de commande
sans API n'a pas de veilleur — et n'en avait pas non plus avant. Ce n'est pas une régression, mais ça
se dit : la couverture est celle du chemin de production (Control Tower), pas celle de tous les
chemins.

### 5.3 Ce que la règle rend : un verdict, frère de `vitalite`

Le dispositif existant donne le patron entier. `vitalite`
([`battement.py:119`](../maestro/controltower/battement.py)) est une **fonction pure** de
`(statut, dernier_battement, seuil)`, calculée **à la lecture** par `_avec_vitalite`
(`executions.py:475-486`) et posée sur le résumé sans toucher au reste. Le nouveau verdict est son
**frère sur l'autre question** :

```
en_souffrance(statut, attente_depuis, *, maintenant, seuil_s) -> bool
```

Ce que cette forme achète, et qui n'est pas un détail de style :

- **aucun type d'événement nouveau.** Le dépôt en porte 18, dont un — `playbook.proposition`
  (`events.py:104`) — n'a **aucun producteur** dans `maestro/`. Un dix-neuvième se justifie mal quand
  le dix-huitième ne sert pas ;
- **aucun champ de projection nouveau, aucune migration.** Le verdict est *dérivé* de données déjà
  projetées, donc il est reconstruit gratuitement au redémarrage de l'API, qui rejoue son journal
  durable (`app.py:1024-1043`) ;
- **il est testable comme une fonction**, sans horloge et sans processus — c'est déjà ainsi que
  `vitalite` est éprouvée.

**Le verdict binaire est un choix.** `vitalite` est ternaire parce qu'il a trois états de
connaissance ; ici le troisième — « il attend, mais pas depuis trop longtemps » — est **déjà porté
par le statut** (`STATUTS_EXECUTION_EN_ATTENTE`). Le reporter dans le verdict serait un second
support pour un même fait, c'est-à-dire la panne que #365 a supprimée sur le cycle de vie.

### 5.4 Le seuil, et son asymétrie **inversée**

Un seuil de silence est un choix qui se justifie. Les deux seuils généreux du dépôt — les 30 min
d'orphelinat (`battement.py:89-99`) et les 6 h de #327 — le sont pour la **même** raison, écrite aux
deux endroits : se tromper **détruit** quelque chose. Déclarer orphelin un run vivant, c'est proposer
de le reprendre depuis son cadrage ; déclarer abandonné un ticket vivant, c'est le retirer à qui
travaille dessus. D'où : « on se trompe du côté qui ne détruit rien ».

**Ici l'asymétrie est inversée, et c'est ce qui autorise un seuil serré.** Le verdict n'arrête aucun
run, n'annule rien, ne reprend rien, ne décide rien : il **trie**. Un faux positif coûte une ligne
signalée qu'on regarde et qu'on oublie. Un faux négatif coûte ce que #568 a coûté, et c'est mesuré :

> Le run est resté figé **31 % de son temps de mur** et n'a repris que par un `POST` à la main,
> pendant que cet écran affirmait « aucune validation en attente ».
> — [docs/05 §2.6](./05-interface-control-tower.md)

**Valeur retenue : `SEUIL_SOUFFRANCE_S = 900.0` — quinze minutes**, soit la moitié du seuil
d'orphelinat, parce que ses erreurs coûtent moins de la moitié. C'est un **point de départ nommé**,
pas une loi : ce qui compte est qu'il soit une constante avec un motif écrit, comme
`SEUIL_ORPHELIN_S`, et la première mesure d'usage le déplacera.

**Un seuil, pas trois.** Les trois attentes (brief, réponses, arbitrage) n'ont pas la même urgence
intuitive — un humain est censé prendre son temps sur un brief. Elles partagent pourtant le seuil,
parce que le dépôt a déjà tranché que « depuis quand attend-il ? » n'a **qu'une** réponse :

> l'ancienneté de l'attente **se pose et se lève sur cet ensemble** […] c'est une seule question,
> elle mérite une seule réponse. — `state.py:125-129` et `:445-447`

**Un horodatage illisible rend `True`, et cette règle est l'inverse de celle de `vitalite`** — qui
rend `indetermine` plutôt qu'`orphelin`. L'inversion suit l'asymétrie : là-bas, affirmer la mort sur
une donnée qu'on ne sait pas lire déclenche une reprise destructrice ; ici, « ce run est suspendu et
on ne sait même pas depuis quand » est **pire** que « suspendu depuis 20 minutes », et le signaler ne
casse rien.

### 5.5 Les trois formes refusées

**La veille modèle — refusée, et c'est la principale.** Un modèle qui relit l'état toutes les N
secondes coûte **proportionnellement à la durée du run**, pour une information qu'une comparaison de
deux horodatages rend gratuitement. Trois faits la condamnent ensemble : aucune boucle périodique du
dépôt n'appelle un fournisseur (fait 3) ; le CLI **refuse déjà** un appel modèle rejoué dans un
moteur qui reprend, et pour ce motif exact (`cli.py:340-347`) ; et le fournisseur n'est même pas
atteignable — `OrchestrationEngine` reçoit `provider` en paramètre (`loop.py:355`) et **ne le stocke
jamais** sur `self`, si bien qu'un superviseur devrait passer par `self._orchestrator._provider`,
un attribut privé.

**Un processus superviseur — refusé.** Un processus long existe déjà par run
(`hote_detache.py`), et un autre existe déjà pour tous les runs (l'API). En ajouter un troisième
serait payer un déploiement pour une comparaison de dates.

**L'humain qui regarde — refusé, parce que c'est le statu quo et qu'il est mesuré défaillant.**
C'est le seul canal actif par défaut, et il exige **un onglet ouvert** : il n'existe dans
`apps/web/` ni badge d'onglet, ni notification navigateur, ni son, ni toast — vérifié
exhaustivement. Le run de #568 a perdu 31 % de son temps devant un écran allumé.

## 6. Ce que ça fait au plan validé — la réponse est celle de #354

Le ticket demande **une seule réponse pour les deux cadrages, pas deux**. Elle est rendue par
[docs/31 §5](./31-decision-surface-ecriture-agents.md) (**#354**), et cette note la **ratifie sans la
rejouer** :

> **Le graphe du plan ne se modifie pas en cours de run.** Ni un agent, ni un superviseur, ni
> l'orchestrateur ne lui ajoutent, n'en retirent ou n'en réaffectent un nœud à chaud. Ce qui
> s'accumule pendant un run est **à côté** du graphe — des observations, en ajout seul.

Elle est vérifiée indépendamment ici, et elle tient : le plan est figé à `loop.py:622`
(`topological_order`) et publié une fois (`loop.py:877`, « c'est l'instant où le plan existe et où il
est **figé** ») ; `en_vol` n'est peuplé qu'à `loop.py:686`, dans une seule passe ; `Task` est
`frozen` (`schema.py:95`) et sans aucun champ d'état d'exécution ; et il n'existe **aucun** second
appel à `plan()`.

**Ce que cette note ajoute est la conséquence, pas la règle.** Si le graphe ne bouge pas, alors les
questions du ticket sur `task.schema.json`, l'ordre topologique et `_ensure_acyclic` **ne se posent
pas** — et surtout, *décider* perd trois de ses quatre verbes (§4.3). La surveillance ne demande donc
aucune exception à D5, et n'en demandera pas : **elle observe et signale, elle ne touche pas au
plan.** C'est la même phrase que #354 applique aux agents, appliquée au superviseur.

⚠ Une nuance héritée de #354 qu'il ne faut pas perdre : il n'existe **aucune classe `Plan`**.
L'immuabilité est celle des **nœuds**, pas du conteneur (`loop.py:611`, `:618` reconstruisent la
liste par `dataclasses.replace`). C'est suffisant pour l'invariant — on interdit d'ajouter un nœud,
pas de rebâtir la liste avant le départ — mais qui s'appuierait sur « le plan est immuable » au sens
fort trouverait une liste Python ordinaire.

## 7. Arbitrage ④ — l'articulation avec #647

> **Verdict : ce n'est PAS le même chemin de remontée que l'arbitrage — et c'est la règle de #647
> appliquée une troisième fois, pas une divergence.**

### 7.1 Ce que #647 a laissé derrière lui

[docs/31 §3](./31-decision-cran-orchestrateur.md) (**#647**) **retire** le cran `orchestrateur`, et
son §4 conclut : pas d'escalade, « parce qu'après ① il n'y a plus de milieu d'où escalader ». Le
routage à trois crans n'en a donc plus que deux — `auto` et `humain` — et **aucun canal machine sur
aucun chemin**.

La question du ticket (« l'alerte remonte-t-elle par le même chemin qu'un acte arbitré ? ») a donc
perdu son milieu en cours de route. Reste la vraie question : **une alerte a-t-elle sa place dans la
file de validations ?**

### 7.2 Non, et #647 a déjà écrit la règle

Les deux objets diffèrent sur les trois axes qui définissent le canal :

| | un **acte** arbitré (#573) | une **alerte** de surveillance |
| --- | --- | --- |
| Qui attend ? | un appelant est **bloqué** dessus (`validation.py:122-149`, attente indéfinie) | **personne** — rien n'est suspendu par l'alerte |
| Que porte la réponse ? | un **booléen** (`Validateur`, `guardrails.py:285`) | ni oui ni non — « rien », « annuler », « relever le budget », « aller voir » |
| Que se passe-t-il sans réponse ? | l'acte n'a pas lieu | **rien de plus** : le run était déjà arrêté, c'est le fait signalé |

Router une alerte dans `/api/validations` mettrait donc une carte à deux boutons devant un fait qui
n'a pas de oui/non. C'est exactement ce que #647 §5.3 a déjà écrit, pour le canal « question » :

> La file porte des **actes à décider**, et sa carte est une carte oui/non […]. Une question ouverte
> n'y a pas de geste.

Troisième objet, même règle, même conclusion. **La file de validations est pour les actes.**

### 7.3 Le chemin existe déjà, et il a déjà été choisi une fois

Il n'y a rien à inventer : le seul verdict de surveillance existant, `vitalite`, ne passe **pas** par
la file. Il est servi sur le résumé du run (`GET /api/executions`) et rendu par
[`PanneauRunsPerdus.tsx:48-84`](../apps/web/components/PanneauRunsPerdus.tsx), qui **sort** les runs
orphelins de la liste et leur attache leur geste.

> **Une alerte est un état de run rendu visible, jamais une carte à trancher.**

C'est la forme retenue, et elle a le mérite d'avoir déjà été jugée bonne sur la seule question du
même genre.

### 7.4 `supervision.py` est-il le véhicule ? Pas en l'état — et le best-effort reste

Le ticket demande si `supervision.py` porte l'alerte, et si « ce qui est best-effort aujourd'hui le
reste quand c'est le seul signal qu'un run est en peine ».

Il n'est pas le véhicule **du premier lot**, pour trois raisons mesurées (fait 4) : il coûte un appel
modèle agentique par envoi — donc la veille modèle rentrerait par la fenêtre —, il n'est branché sur
aucun chemin de la Control Tower, et il est refusé en modes `--queue` et `--durable`.

Et la réponse à la seconde question est **oui, il le reste — mais il cesse d'être le seul**, parce
que le verdict ne se conçoit pas comme une notification :

> **L'alerte est un ÉTAT, pas un message.** Une notification qui se perd est perdue ; un état est
> encore vrai quand on revient.

C'est ce qui distingue le verdict du bus : le bus est éphémère et sans rejeu (`events.py:19-21`),
donc tout ce qui n'existe que comme événement est perdu pour qui n'écoutait pas — et le sujet du
ticket est précisément que **personne n'écoute**. Un verdict dérivé de la projection est vrai à la
prochaine lecture, quelle qu'en soit l'heure. Un relais Slack pourra s'y brancher plus tard, en
**consommateur** du verdict et non en re-dérivation : il restera best-effort sans que ce soit grave,
puisqu'il ne portera plus l'information tout seul.

## 8. Ce que la trace doit porter — rien, et c'est démontrable

Le ticket demande ce que le journal doit garder : qui a détecté, sur quelle règle, ce qui a été
décidé, par qui — « au même titre que le détail rendu par `Guardrails._tranche` ».

La comparaison ne tient pas, et c'est instructif. `_tranche` trace parce qu'une **décision a été
prise** à un instant, par quelqu'un, et qu'elle n'est pas recalculable après coup. Le verdict de
surveillance est une **fonction pure d'un état durable** : à tout instant, il se recalcule depuis la
projection, elle-même reconstruite depuis le journal durable au démarrage de l'API
(`app.py:1024-1043`). Journaliser un fait recalculable, c'est se donner **deux** sources pour une
seule vérité, et la seconde se périmera.

C'est déjà le régime de `vitalite`, qui n'est tracée nulle part et ne manque à personne.

**Ce qui se trace est ce sur quoi on agit** — annuler, reprendre, relever un plafond, trancher une
validation —, et chacun de ces gestes se trace déjà par son propre événement. La chaîne reste donc
lisible de bout en bout sans une ligne de journal nouvelle : le verdict dit *pourquoi* quelqu'un a
regardé, le geste dit *ce qu'il a fait*.

⚠ Le prix, nommé : un seuil qu'on déplacerait rendrait les verdicts **passés** non reproductibles.
`vitalite` a exactement la même propriété et le dépôt l'a acceptée. La cohérence vaut mieux ici
qu'une exception qui ferait de ce verdict-ci le seul à porter son propre historique.

## 9. Le découpage

La décision **engage du code**. **Parent de suivi #736**, trois lots, milestone « Collaboration
inter-agents ». Aucun lot n'est marqué `lot::parallele` : le second consomme le champ que le premier
pose, et le troisième vient après les deux — l'arbitrage a été **rendu**, et son verdict est
« séquentiel » (#562).

| Lot | Ce qu'il fait |
| --- | --- |
| **#737** | Le verdict : `en_souffrance` (fonction pure + constante motivée), posé sur le résumé de run à côté de `vitalite`, et le contrat d'API dans docs/05 |
| **#738** | L'écran le **trie** : un run en souffrance sort de la liste, comme un orphelin sort par `PanneauRunsPerdus` |
| **#739** | Tests + doc |

**Quatre choses à ne pas défaire dans ce chantier :**

1. **le verdict est dérivé, jamais stocké** — pas de champ de projection, pas d'événement, pas de
   migration (§5.3). Le jour où on le stockera, on aura deux vérités et la seconde se périmera ;
2. **il ne décide de rien et n'agit sur rien** — c'est ce qui autorise son seuil serré (§5.4). Lui
   faire annuler, reprendre ou relancer quoi que ce soit renverserait l'asymétrie qui le justifie, et
   il faudrait alors le rendre généreux comme `SEUIL_ORPHELIN_S` ;
3. **un seul seuil pour les trois attentes** — le dépôt a déjà tranché que l'ancienneté d'attente n'a
   qu'une réponse (`state.py:445-447`) ; en écrire trois serait rouvrir cette décision par la bande ;
4. **il ne passe pas par la file de validations** (§7.2), qui porte des actes.

**Ce que le lot final doit vérifier en propre**, au-delà des tests de la fonction :

- que le verdict **survit à un redémarrage** de l'API sans rien de nouveau — c'est la preuve qu'il
  est bien dérivé, et elle se joue en rejouant le journal durable ;
- qu'un run **au travail** ne le porte jamais, si long soit-il : ce verdict juge l'attente, pas la
  durée. C'est la confusion exacte que `vitalite` a évitée (`battement.py:137-141`), et le test se
  prouve sur un run qui travaille depuis plus que le seuil ;
- qu'un verdict rendu **se lève** dès que l'attente est tranchée — y compris sur un **refus**, qui
  rend la main au moteur aussi sûrement qu'un accord (`state.py`, la règle de #571).

**Ce qui n'est pas découpé, et pourquoi.** La **datation de la pause** (§3.2) est un manque réel,
mais aucune mesure ne dit qu'une pause a jamais été oubliée, là où l'attente non jugée a son incident
chiffré. La découper maintenant serait construire le second mécanisme pour zéro cas observé après
avoir construit le premier pour un cas mesuré. Elle est au §10 comme porte, pas comme lot.

## 10. Ce qui rouvrirait la décision

Nommé d'avance, même patron que [docs/28 §8](./28-decision-frontiere-execution-run.md),
[docs/29 §10](./29-decision-run-objet-de-premier-plan.md) et les deux notes de docs/31.

1. **La datation de la pause** se rouvre à la **première pause oubliée mesurée** — un run suspendu
   plus longtemps que son travail, tenant un hôte pour rien. Le remède est alors un champ, pas un
   mécanisme : le verdict du §5.3 l'accepte en entrée sans changer de forme.
2. **Un canal hors écran** se rouvre le jour où quelqu'un manque une alerte **parce qu'aucun onglet
   n'était ouvert** — et il se branchera en **consommateur** du verdict (§7.4), jamais en
   re-dérivation. ⚠ S'il passe par `supervision.py`, son prix d'aujourd'hui est un appel modèle par
   envoi (`supervision.py:204`) : c'est ce chiffre-là qu'il faudra rouvrir en premier, pas le canal.
3. ***Décider* se rouvre** si l'un des trois verbes soustraits au §4.3 revient — c'est-à-dire, en
   pratique, si #354 §9 rouvre « créer une tâche » (plafond obligatoire **et** point d'approbation).
   Tant que le graphe est figé, il n'y a rien à décider à chaud, et la question ne se pose pas.
4. **La forme de la boucle se rouvre** si un run cesse d'être visible depuis une API vivante — un
   déploiement où la Control Tower ne tourne pas en permanence. La portée du §5.2 tomberait, et il
   faudrait alors choisir un autre hôte : la question serait à reprendre entière.

Aucune de ces quatre conditions n'est remplie au 2026-08-28.

## 11. Où cette décision est écrite ailleurs

**Le lot 1 (#737) a livré**, et cette section dit désormais où — l'énoncé « rien ne change encore dans
le code ni dans les contrats » était vrai le jour du cadrage et ne l'est plus :

- [docs/05 §2.6](./05-interface-control-tower.md) — le contrat d'API des attentes et de la vitalité,
  où le champ du §5.3 **s'est déclaré** : `en_souffrance` sur le `ResumeExecution`, à côté de
  `vitalite`, avec la table qui sépare les deux verdicts et les quatre points du §9 relus comme un
  contrat. Le §6.1 le porte dans la forme JSON, là où `vitalite` est déclarée champ par champ ;
- [docs/24 §2.4](./24-projets-locaux-et-poste-de-travail.md) — « ⚠ La **vitalité** ne peut pas tenir
  ce rôle », qui est le constat d'où part cette note, et qui reste **exact** : la vitalité ne le tient
  toujours pas, c'est son frère qui le tient.

Côté code, le verdict vit dans [`maestro/controltower/souffrance.py`](../maestro/controltower/souffrance.py)
— un module à lui plutôt qu'un ajout à `battement.py`, dont l'en-tête revendique de tenir en une
phrase et dont aucun battement n'entre dans ce jugement. Il est servi par `_avec_vitalite`
(`executions.py`) et **dit une fois** au journal de l'API par `_veiller`, troisième passager du
réveil du §5.2. Restent le tri à l'écran (**#738**) et les **tests** (**#739**), à qui revient
d'éprouver en propre la survie à un redémarrage, le run au travail qui ne porte jamais le verdict, et
l'attente levée par un refus autant que par un accord.

**Sur la numérotation, et c'est un constat, pas une prévision.** #354 et #647 ont été instruits en
parallèle de celui-ci et ont **tous deux pris `31`** — `31-decision-surface-ecriture-agents.md` et
`31-decision-cran-orchestrateur.md` cohabitent sur `main` depuis les merges `952bd60` et `6702f2b`.
Le doublon n'a été arbitré par personne : chacune des deux PR était juste seule, et git ne signale
rien puisque les noms de fichiers diffèrent.

Cette note prend donc **33** et **laisse `32` libre à dessein** — c'est le numéro qui permet de
défaire le doublon sans en créer un autre : l'une des deux notes de `31` devient `32`, et la série
redevient contiguë `31 · 32 · 33`. Prendre `32` ici aurait fermé cette porte et forcé la correction
à sauter en `34`. Le renommage est **#742**, et il ne relève pas de ce cadrage : il touche deux
documents qui ne sont pas les siens.

⚠ La leçon est plus large que le symptôme, et elle vaut pour le prochain chantier mené à plusieurs
cadrages parallèles : **le numéro d'un document se réserve au moment où l'on ouvre le ticket, pas au
moment où l'on écrit le fichier.** Trois notes instruites la même journée, trois auteurs qui lisent
le même `docs/` d'hier — la collision était certaine, et aucune revue ne pouvait l'attraper puisque
chaque PR était correcte isolément.
