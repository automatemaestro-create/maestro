# 43 — Un projet naît dans la conversation : on le choisit au démarrage, son outillage s'y construit, et `AGENTS.md` suffit

**Date :** 2026-09-24. **Instruite par :** `/idee` (#1013). **Consignée par :** #1300.
**Jalons :** *Rien de figé — Maestro comprend le projet, propose, vérifie* (2028-02-02) et *Le run tient parole — il vérifie, se rattrape, et le fil agit* (2028-02-03). Aucune échéance n'a bougé.
**Tickets :**
- **#1293** : au démarrage, Maestro s'ouvre sur le choix du projet, dans l'atelier (§2.1) ;
- **#1294** : un projet naît dans la conversation. C'est le lot 4/7 de #1155 (§2.2) ;
- **#1161**, recadré : l'outillage se construit dans la conversation, pièce par pièce (§2.2) ;
- **#1295** : un projet n'a qu'un `AGENTS.md` (§2.3) ;
- **#1290, #1291, #1292, #1297, #1298, #1299** : les défauts et capacités du même retour, qui ne sont pas des décisions (§4).

---

## 0. Ce que ce document décide

**Trois décisions tombent, toutes demandées par la personne en toutes lettres (§1).**

1. **Le démarrage.** Chaque démarrage de Maestro arrive sur le **choix du projet**, avec « Reprendre *le dernier projet* » en tête. La colonne de conversation y est ouverte. Cela renverse [docs/05 §2.0.1](./05-interface-control-tower.md) : le projet actif n'y est plus *« relu au démarrage »*.
2. **La création.** Un projet **naît dans la conversation**. « Nouveau projet » ouvre le fil, et l'orchestrateur y comprend ce que la personne veut faire, pose les questions que *ce* projet appelle et propose ses choix. L'outillage s'écrit **au fur et à mesure**, chaque pièce sur accord. Le formulaire en trois étapes quitte le chemin de création. Cela renverse [docs/37 §4 point 6](./37-decision-equipe-sur-mesure.md), l'outillage en étape de formulaire, et la ligne de [docs/38 §8](./38-decision-outillage-universel-du-projet.md) qui l'applique (#1034).
3. **Le fichier d'instructions.** Un projet a **un seul fichier d'instructions, `AGENTS.md`**. Un pont (`CLAUDE.md`, `GEMINI.md` ou le réglage équivalent) ne s'écrit que pour un client **que la personne utilise** et qui **ne lit pas** `AGENTS.md` nativement à sa version. Cela renverse [docs/38 §0 et §3.2](./38-decision-outillage-universel-du-projet.md) : les deux ponts n'y sont plus écrits d'office.

Aucun renversement n'a été tranché à la place de la personne. Le reste du retour (§4) décrit des défauts et des capacités absentes, pas des décisions.

## 1. D'où vient la demande

Le 2026-09-24, la personne crée le projet `p3` et y lance plusieurs runs : refaire la maquette et le site vitrine d'un kombucha. Elle rend dix constats, tous notés KO, avec des captures. Trois portent sur les décisions de cette note :

> « Au démarrage, Maestro s'est encore ouvert sur l'ancien Control Tower. […] Au démarrage, on devrait arriver à la page choix de projet ou création. »

> « J'ai créé un nouveau projet p3, on m'a posé plusieurs questions sur la direction du projet ; elles ne m'ont pas paru pertinentes. C'est très mécanique. Moi, j'aurais préféré que le démarrage d'un projet commence dans le chat, en interactif : le chat me pose des questions, me propose des choix, et au fur et à mesure on peut générer l'outillage. Donc une conversation intelligente, pas des choix proposés au début. »

> « L'outillage : AGENT.md, CLAUDE.md, GEMINI.md, pourquoi ? On a toujours CLAUDE.md et GEMINI.md ? Pas possible d'universaliser sans reprendre leur md ? »

L'analyse complète est le corps de #1300 : la carte du code, l'état réel des runs lu par l'API, chaque arbitrage.

## 2. Ce qui est renversé

### 2.1 Le démarrage : le choix du projet, à chaque fois

**Avant.** [docs/05 §2.0.1](./05-interface-control-tower.md) (#279, #280) : le projet actif est *« retenu d'une visite à l'autre, relu au démarrage »*. La porte d'entrée n'apparaît que sans projet retenu. De son côté, la colonne de conversation garde le dernier geste de la personne **indéfiniment** (`apps/web/lib/preferences.ts`). Un seul « fermer » ancien suffit à la tenir fermée à chaque démarrage.

**Ce que la personne a vu.** Le profil de la coque retenait `maestro.projet.actif`, et `maestro.conversation.ouverte = "0"`, écrit avant le 22/09. Maestro s'ouvrait donc sur le tableau de bord, sans la conversation : c'est la Control Tower d'avant l'atelier, sous un titre qui disait encore « Control Tower ». Le code servi était pourtant à jour. *« Encore »* : chaque démarrage le rejouait.

**Après** (#1293) :
- chaque démarrage arrive sur le choix du projet. « Reprendre *le dernier projet* » est en tête, et un seul geste suffit ;
- la colonne de conversation est ouverte au démarrage. La fermer vaut pour la session ;
- la fenêtre s'appelle « Maestro ».

**Pourquoi.**
- La conversation est désormais la porte d'entrée d'un projet (§2.2). Un démarrage qui la cache et qui saute le choix contredit ce que l'atelier promet.
- « Reprendre » en tête garde ce que #279 voulait protéger, retrouver son travail sans chercher. Il le fait au prix d'un geste, en échange d'un démarrage qui dit où l'on est.

### 2.2 La création : dans la conversation, l'outillage pièce par pièce

**Avant.**
- [docs/37](./37-decision-equipe-sur-mesure.md), principe 2 : *« La première étape de tout projet est la génération de son outillage […] sur un projet neuf, il découle des choix de l'utilisateur. »*
- [docs/37 §4 point 6](./37-decision-equipe-sur-mesure.md) : *« L'étape d'outillage est première et proposée d'office, mais reportable. »*
- [docs/38 §8](./38-decision-outillage-universel-du-projet.md), ligne #1034 : l'étape d'outillage du parcours de création (`EtapeOutillage.tsx`).

Dans le code, créer un projet est un **formulaire en trois étapes** : `FormulaireProjet`, puis `EtapeOutillage`, puis `EtapeEquipe`. Les questions d'outillage sont six options écrites en dur (`CATALOGUE`, `maestro/outillage/questionnaire.py`), sans appel au modèle. L'orchestrateur n'a aucun verbe pour créer un projet.

**Après.**
- **#1294.** « Nouveau projet » ouvre la conversation. L'orchestrateur comprend ce que la personne veut faire, avec ses mots. Il pose au plus les questions qui manquent, puis propose un nom, un dossier et le versionnement local, chacun avec sa raison. Il **déclare le projet sur accord**. Un dossier existant s'importe par la même conversation, lu et compris (#1158).
- **#1161, recadré.** L'outillage se construit dans la même conversation, **pièce par pièce**. Chaque pièce est proposée, montrée (diff), puis écrite sur accord. Une correction en langage naturel est prise, puis revérifiée par l'exécution (#1160).
- Le formulaire à étapes quitte le chemin de création. La page `/projets` garde la gestion.
- Le moteur de #1147 (questions générées par le modèle, réponse libre) est la pièce que ces deux lots réutilisent. Seule sa **surface** change : une carte d'étape de formulaire devient une conversation.

**Pourquoi.**
- **Un formulaire propose d'entrée des choix qu'il ne comprend pas encore.** C'est ce que la personne appelle *« mécanique »*. Même générées par le modèle, les questions d'une étape de formulaire arrivent en bloc, avant que Maestro ait compris le projet.
- **La conversation est l'endroit où Maestro comprend.** Il y a déjà l'orchestrateur, la carte de proposition (#1146), la lecture du projet (#1223) et l'accord qui précède toute écriture. Y créer le projet réunit la compréhension et la proposition au même endroit.
- **« Au fur et à mesure » rend chaque écriture lisible.** Une pièce proposée au moment où la question se pose s'accepte ou se corrige en connaissance de cause. Une liste à cocher en fin de formulaire se valide en bloc.
- C'est [docs/41](./41-decision-maestro-juge-il-ne-bride-pas.md) appliqué au premier contact avec le produit : *Maestro juge, il ne bride pas*.

### 2.3 Le fichier d'instructions : `AGENTS.md` suffit

**Avant.** [docs/38 §0 et §3.2](./38-decision-outillage-universel-du-projet.md) : à côté d'`AGENTS.md`, Maestro écrit **toujours** deux ponts d'une ligne, `CLAUDE.md` et `GEMINI.md`, contenant `@AGENTS.md`. Ils sont cochés d'office. Leur justification tenait au tableau de §2.2 : sans eux, l'outillage était invisible pour Gemini CLI et pour Claude Code avant sa v2.1.277.

**Ce que la personne a vu.** Trois fichiers à la racine d'un projet dont elle ne sait pas pourquoi. Rien n'était recopié : les ponts font 10 octets. Mais rien ne disait non plus à qui ils servent. Et **les agents de Maestro ne les lisent pas** : ils reçoivent `AGENTS.md` explicitement, et les ponts sont exclus de leur contexte (`maestro/outillage/contexte.py`).

**Ce qui a changé dehors** (vérifié le 2026-09-24) :
- [Claude Code](https://code.claude.com/docs/en/memory) lit `AGENTS.md` nativement depuis la v2.1.277, quand aucun `CLAUDE.md` ne le masque. Un pont `CLAUDE.md` ne sert plus qu'aux versions antérieures. Écrit sans raison, il **masque** même la lecture native : c'est le `CLAUDE.md` qui est lu, qui importe `AGENTS.md`.
- [Gemini CLI](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/gemini-md.md) lit toujours `GEMINI.md` par défaut, et `AGENTS.md` par `context.fileName`.

**Après** (#1295) :
- un projet a **`AGENTS.md` seul**, et ses skills ;
- un pont ne s'écrit que pour un client **que la personne utilise** (dit dans la conversation, ou trouvé sur le poste avec sa version) et qui **ne lit pas `AGENTS.md`** à cette version. Il prend alors la forme la plus légère **vérifiée**. Pour Gemini CLI, #1295 l'a retranché sur pièce : c'est `GEMINI.md` d'une ligne, pas `.gemini/settings.json` ([docs/38 §3.2](./38-decision-outillage-universel-du-projet.md)) ;
- Maestro dit pourquoi il n'y a qu'un fichier, ou pourquoi il ajoute un pont. « J'utilise aussi Gemini » l'ajoute ;
- un projet importé qui a déjà ses ponts les garde. Maestro ne retire jamais ce qu'il n'a pas écrit (§4.2 de docs/38).

**Pourquoi.** Un pont vers un client que personne n'utilise est une **supposition**, au sens du critère C2 de « Rien de figé ». Deux ponts d'office, c'est une liste fermée de deux clients appliquée à tout projet. Le principe de docs/38 (**un pont, jamais une copie**) ne bouge pas. Ce qui change, c'est **quand** un pont est justifié.

## 3. Ce qui ne bouge pas

- **Rien ne s'écrit sans accord.** Ni le projet, ni une pièce d'outillage, ni un pont, ni le versionnement. La conversation propose, la personne accepte.
- **La frontière d'écriture** ([docs/24 §2.4](./24-projets-locaux-et-poste-de-travail.md)) et le diff à valider.
- **Le format arrêté par docs/38** (§3 à §5) : `AGENTS.md`, `.agents/skills/`, le manifeste, *un pont jamais une copie*, et la configuration ambiante fermée au runtime de Maestro (§5).
- **Le `recommander` commun aux deux chemins**, neuf et importé. La règle du pont y vit une fois.
- **Changer de projet dans une session se fait au shell** (#280). Seul le **démarrage** change.
- **Le versionnement est proposé, jamais imposé.** Un projet non versionné reste un projet de plein droit.

## 4. Ce qui n'est pas une décision

Le même retour porte des constats qui ne renversent rien. Ce sont des défauts ou des capacités absentes, rangés dans la roadmap ([docs/06](./06-roadmap.md)) :
- **#1290.** Un run fini restait « En cours » dans sa vue, et le fil montrait la carte du run d'avant. L'événement de fin partait **sans projet**, et la diffusion par projet l'écartait.
- **#1291.** Les checklists restaient à 0/N. Le CLI embarqué par le SDK ne monte plus `TodoWrite` par défaut. Il le remplace par `TaskCreate`/`TaskUpdate`, que Maestro ignorait.
- **#1292.** Le jeton d'API s'écrivait en clair dans le journal d'accès. C'est un garde-fou « secrets » qui ne tenait pas.
- **#1297.** Les flèches du pipeline n'avaient pas de légende. Une flèche est une **dépendance** déclarée par le plan, pas un lien logique ni une possibilité de parallélisme.
- **#1298 et #1299.** Aucun parallélisme n'était observé. Les plans étaient linéaires, `p3` n'était pas versionné, et un agent ne prenait qu'une tâche à la fois.
  - **Le garde-fou de #839 ne bouge pas.** Un projet non versionné ne prend qu'une tâche à la fois, parce que deux agents y écriraient dans le même arbre. Le remède est de **proposer** de versionner le projet, pas d'ôter la garde.
  - **`INSTANCES_DEFAUT = 1` n'est pas une décision écrite.** C'est un défaut que #1299 dérive du plan, sur un projet versionné seulement.

**Le gel du rail outillage** ([docs/40](./40-decision-rythme-et-scenarios-de-reference.md)) est maintenu jusqu'au 2026-10-12. La personne demandait de le lever si c'était recommandé, et ce ne l'est pas : les dix constats sont du produit, et aucun ticket d'outillage ouvert n'y répond. Le gel ne vise que l'outillage **de la forge**. L'outillage que Maestro écrit dans un projet est du produit, et n'a jamais été gelé.

## 5. Ce qui rouvrirait la décision

- **§2.1.** Une mesure d'usage montrant que « Reprendre » en tête coûte plus qu'il ne rend : par exemple, une personne qui travaille toujours sur le même projet et que le choix ralentit à chaque démarrage. La réponse serait alors un réglage de la personne, pas un retour au défaut de #279.
- **§2.2.** Une création que la conversation ne sait pas conduire, par exemple l'import d'un gros monorepo dont la lecture dépasse ce qu'un échange tient. Le formulaire n'y reviendrait pas en premier : le fil dirait ce qu'il ne sait pas faire, avec le ticket qui le lèvera.
- **§2.3.** Un client que la personne utilise, qui ne lit ni `AGENTS.md` ni un pont d'une ligne. Ou Gemini CLI qui lit `AGENTS.md` par défaut : le pont Gemini disparaîtrait alors comme le pont Claude. Ces faits se **revérifient** (docs/38 §7), ils ne se supposent pas.
