<!-- documentation: produit -->
# 46 — Un projet se conçoit en phases, comme un cahier des charges, dans un Maestro simple et visuel

**Date :** 2026-10-02. **Instruite par :** `/idee` (#1013), sur un plan **validé par la personne avant d'être écrit** (#1415). **Consignée par :** #1416.

**Jalons**, dans leur ordre :
- *Le niveau visuel — une direction choisie, un écran étalon* (2028-02-01, avancé) ;
- *Maestro simple — on s'y retrouve sans effort, sans jargon* (2028-02-04, neuf) ;
- *Le mode conception — un projet se définit comme un cahier des charges, phase par phase, et tient son plan* (2028-02-05, neuf).

**Chantiers** :
- **#1414** (le mode conception, 9 lots, #1417 à #1425) ;
- **#1409** (Maestro simple, 3 lots, #1410 à #1412) ;
- **#1124** recadré (#1125) ;
- **#1413** : le critère « simplicité » de la relecture visuelle.

**Esquisse validée :** [`docs/assets/1416/esquisse-2026-10-02.html`](./assets/1416/esquisse-2026-10-02.html).

---

## 0. Ce que ce document décide

Trois décisions tombent. Toutes sont demandées par la personne en toutes lettres (§1).

1. **La création d'un projet devient la définition de son cahier des charges, en quatre phases validées une à une** :
   - **Le produit** ;
   - **La solution** ;
   - **Le plan** ;
   - **La validation**.

   Maestro rédige chaque phase **d'un coup**, à partir de ce qu'il a compris, et la présente en **slide**. La personne la corrige sur place ou dans la conversation, puis la valide. Cela renverse [docs/43 §2.2](./43-decision-un-projet-nait-dans-la-conversation.md) : les questions posées une à une, et l'outillage écrit pièce par pièce.
2. **Maestro devient simple, et simple ne veut pas dire dépouillé.**
   - Le menu et la barre supérieure ne gardent que les intentions de la personne, dans ses mots.
   - Le détail technique passe à un geste, jamais d'office.
   - L'interface montre plutôt qu'elle n'écrit, avec une finition de produit professionnel.
   
   Les places du menu, décidées une à une (#474, #270, #249, #484), sont **rouvertes**.
3. **La direction visuelle se choisit d'abord, sur les écrans cibles.** L'écran pilote de [docs/39](./39-decision-niveau-visuel-choisi.md) n'est plus le tableau de bord d'aujourd'hui. Ce sont l'**accueil simplifié** d'un projet et une **slide « Le produit »**, dans la structure de l'esquisse validée. Le jalon « Le niveau visuel » passe **devant** les deux chantiers, qui se construisent dans la direction retenue.

Les **documents du projet** s'écrivent dans son dossier, et l'équipe d'agents s'en sert. C'est une extension de [docs/38](./38-decision-outillage-universel-du-projet.md), pas un renversement (§3).

## 1. D'où vient la demande

Le 2026-10-02, la personne :

> « Je voudrais que les étapes d'initialisation du projet soient une sorte de définition d'un cahier de charge. Je ne veux pas de questions posées petit à petit que je trouve non pratique, je préfèrerais une validation globale mais pas en une seule fois non plus. »

> « Je veux que l'interface soit une sorte de slide interactive pour chaque phase. Ne pas oublier que l'utilisateur peut apporter des modifications sur ce que tu proposes / recommandes. Du coup le chat doit être à disposition. »

> « J'aimerais que l'interface globale de Maestro soit plus simple. Pas trop technique, pas trop chargé. Que l'utilisateur sache se retrouver et sans effort. » — point qu'elle dit **très important**.

> « Maestro doit générer tous documents utiles au projet dans le répertoire du projet, et utilisables par l'équipe d'agents. »

Puis, une fois le plan présenté :

> « Attention, simple ne signifie pas design bas de gamme. Je veux un design moderne mais propre. Quelque chose de visuel plutôt que beaucoup de textes. »

Elle a validé le plan et l'esquisse avant qu'aucun ticket restant ne soit créé. Elle a aussi demandé qu'il en soit **toujours** ainsi avec `/idee` (#1415). L'analyse complète est le corps de #1416.

## 2. Ce qui est renversé

### 2.1 La création : des phases, plus des questions une à une

**Avant** ([docs/43 §2.2](./43-decision-un-projet-nait-dans-la-conversation.md), #1294, #1161) :
- un projet naît dans le fil, et l'orchestrateur y pose **« LA question qui manque, une seule »** à chaque tour (`maestro/controltower/orchestration.py`) ;
- puis il propose nom, dossier et versionnement ;
- l'outillage suit **une question puis une pièce par message**.

**Ce que la personne a dit.** Ce goutte-à-goutte est *« non pratique »*. Elle veut une validation globale, *« mais pas en une seule fois »*.

**Après** (#1414) : quatre phases, une slide chacune.

| Phase | Ce que Maestro rédige d'un coup | Ce qui en sort |
|---|---|---|
| *L'idée* (l'entrée) | La personne décrit son projet une fois, joint des documents, ou désigne un dossier existant. Ce que Maestro ignore devient une **hypothèse « à confirmer »** sur la slide, pas une question. | — |
| **Le produit** | Vision, problème, cibles, objectifs mesurables, fonctionnalités priorisées avec leurs **critères d'acceptation vérifiables**, hors périmètre, contraintes et date de livraison souhaitée | document « produit » |
| **La solution** | Choix techniques avec leur raison, environnement, données, qualité attendue, dossier, versionnement, outillage. Un projet importé la trouve **pré-remplie** par la lecture de son dossier. | projet déclaré, outillage écrit |
| **Le plan** | **Jalons datés jusqu'à la livraison**, leurs runs, l'équipe d'agents, une estimation. Un écart avec la date souhaitée est dit, jamais lissé. | document « plan » |
| **La validation** | Le cahier en une vue, sa **revue critique**, puis « Valider et lancer » le premier jalon | runs du jalon 1 |

Puis le **cahier mène le projet** :
- l'accueil suit le plan ;
- chaque jalon livré se **recette** contre le cahier ;
- le cahier se modifie sans rien perdre : ce qui dépend d'un changement passe « à revoir ».

**Pourquoi.**
- **Une validation par phase** est le pas que la personne demande, entre la question unique et le formulaire qui dit tout d'un coup.
- **Les outils professionnels comparables** ont pris le même chemin :
  - [Kiro](https://kiro.dev/docs/specs/feature-specs/) enchaîne exigences, conception et tâches, relues une à une, modifiables par le chat ou en direct ;
  - [Spec Kit](https://github.com/github/spec-kit) enchaîne *specify*, *plan* et *tasks*.
  
  Aucun des deux ne **date** son plan : la phase « Le plan » va plus loin, à la demande de la personne.
- **Le formulaire ne revient pas.** L'argument de docs/43 §2.2 tient : *un formulaire propose d'entrée des choix qu'il ne comprend pas encore*. Une slide est rédigée **après** que Maestro a compris. On y corrige une proposition, on ne remplit pas des champs.
- **Les quatre phases sont une méthode fixe, leur contenu est jugé** ([docs/41](./41-decision-maestro-juge-il-ne-bride-pas.md)). Une application web aura ses parcours dans « Le produit ». Un outil en ligne de commande n'en aura pas. Ce n'est pas un gabarit de champs.

**Ce qui change de moment sans changer de règle.** D5 (le brief validé avant décomposition, Phase 8) tient. Pour un run lancé depuis le plan, le brief **est** le jalon validé du cahier, et aucune question de clarification déjà répondue n'est reposée. Un run demandé hors plan garde le chemin actuel.

### 2.2 L'interface : simple, moderne, visuelle

**Avant.** Mesuré le 2026-10-02 :
- **le menu** compte 9 entrées, dont 5 surfaces d'expert. « Chat » double la colonne de conversation ;
- **la barre supérieure** porte 8 éléments, et son titre dit encore « Control Tower » ;
- **le tableau de bord** montre des compteurs de tâches, d'agents et de dépense ;
- **des tokens, des identifiants et des noms d'outils** s'affichent d'office ;
- **le guide de prise en main** s'ouvre sur « Bienvenue dans la Control Tower ».

**Après** (#1409) :
- **l'architecture** (#1410) : le menu ne garde que les intentions de la personne, avec la piste validée *Accueil · Conception · Avancement · Équipe · Réglages*. Ce qui en sort reste à un geste, depuis l'objet qu'il concerne, et les anciens chemins redirigent ;
- **l'accueil d'un projet** (#1411) : la livraison et sa tenue, la frise des jalons, « À décider » en cartes d'action, et « La suite » ;
- **le vocabulaire** (#1412) : le détail technique derrière « Détails », partout, et le guide qui part du projet.

**Le principe visuel**, que chaque ticket d'écran porte :
- **montrer plutôt qu'écrire** : cartes, colonnes, frises, schémas, jauges ;
- **des textes courts** : un titre, une phrase, le détail à la demande ;
- **une finition au niveau des meilleurs produits**, dans la direction retenue.

Le principe tient dans le socle : AA dans les deux thèmes, un état porté par la forme autant que par la couleur, et la règle des trois places ([docs/30 §4.1](./30-cible-visuelle-control-tower.md)). Ces règles gardent la lisibilité sans brider l'esthétique.

**Écarté** : un « mode expert » global qui basculerait toute l'interface. Il ferait deux produits à tenir et à tester. Le détail en second niveau, objet par objet, le remplace. La veille de #1410 peut le contester sur pièces.

### 2.3 Le niveau visuel : d'abord, et sur les écrans cibles

**Avant** ([docs/39](./39-decision-niveau-visuel-choisi.md), #1125) : trois directions poussées sur **le tableau de bord**. Le contenu de celui-ci ne devait pas bouger.

**Après** :
- les trois directions se poussent sur **l'accueil simplifié** et une **slide « Le produit »**, en brouillons sur la structure de l'esquisse ;
- la personne choisit, et la direction entre dans le socle (#1126) ;
- **l'accueil devient l'écran étalon** (#1411). Cela absorbe #1127, dont la clôture en doublon est proposée à la personne.

**Pourquoi.** *Simple* et *beau* se jugent ensemble. Choisir la direction après la simplification aurait montré à la personne des écrans dépouillés, qu'elle aurait jugés « bas de gamme », et qu'il aurait fallu refaire. La décision de docs/39 ne bouge pas : une direction choisie **une fois**, par une personne. Seule sa cible change.

## 3. Ce qui ne bouge pas

- **Rien ne s'écrit sans accord.** Valider une phase est l'accord de ce que la slide montre, et ce qui sera écrit reste consultable avant.
- **La frontière d'écriture** ([docs/24 §2.4](./24-projets-locaux-et-poste-de-travail.md)) et le diff à valider.
- **`AGENTS.md` seul** ([docs/43 §2.3](./43-decision-un-projet-nait-dans-la-conversation.md)), et le choix du projet au démarrage (§2.1 de docs/43).
- **Le format de [docs/38](./38-decision-outillage-universel-du-projet.md)**. Les documents du projet (un par phase validée, plus ce que le projet appelle) s'y ajoutent :
  - déclarés au manifeste et indexés par `AGENTS.md` ;
  - transmis aux agents par leur index et leur chemin, jamais par leur corps, comme les skills (#1421) ;
  - un document modifié par la personne n'est jamais écrasé sans accord (§4.2 de docs/38).
- **Les mécanismes du socle** : tokens sémantiques, primitives, trois places, AA. La frontière shell / écran ([docs/35 §3.4](./35-decision-poste-de-bureau-et-disposition.md)) tient aussi : la conversation à droite reste une zone du shell.

## 4. Le contrôle qualité

La personne invitait à en proposer un. Quatre contrôles sont retenus :

1. **Des critères d'acceptation vérifiables dès la phase « Le produit »**, chaque fonctionnalité disant à quoi on reconnaît qu'elle marche.
2. **Une revue critique avant chaque validation**, et sur l'ensemble à la fin (#1422). Elle relève :
   - les manques et les contradictions ;
   - les critères non vérifiables ;
   - les dates irréalistes et les risques.
   
   Elle **informe, ne bloque pas** : chaque point se corrige d'un geste ou s'ignore en le disant.
3. **La recette de chaque jalon livré** (#1424) : le livrable exercé contre les critères du cahier, chacun ✓ ou ✗ avec sa pièce. *Lire le code n'est pas l'exercer.*
4. **Pour Maestro lui-même** :
   - un critère « simplicité » dans la grille de la relecture visuelle (#1413), jugé par le regard neuf : l'écran se comprend sans connaître le moteur, montre plutôt qu'il n'explique, et dit la suite ;
   - le compte du menu épinglé par un test (#1410) ;
   - un `/retex-utilisateur` au bouclage du jalon.

« Technique » et « simple » se jugent **par le modèle**, jamais par une liste de mots (#746).

## 5. La place dans la file

<!-- documentation: développement -->

| Jalon | Échéance |
|---|---|
| Le run livre un vrai projet | 2028-01-30 (inchangée) |
| **Le niveau visuel** | 2028-02-05 → **2028-02-01** |
| **Maestro simple** | **2028-02-04** (neuf) |
| **Le mode conception** | **2028-02-05** (neuf) |
| Indépendant du modèle | 2028-02-04 → **2028-02-06** |
| Phase 9 — distribution | 2028-02-16 (inchangée) |

Les jalons soldés en attente de verdict (« Le fil », « Rien de figé », « Le run tient parole ») ne portent plus de ticket ouvert : leur échéance ne range rien. Le détail des rangs et de leurs raisons est dans [docs/06](./06-roadmap.md), section « Le mode conception ».

## 6. Ce qui rouvrirait la décision

- **§2.1** : une création que les phases ne savent pas conduire, par exemple un import dont la lecture dépasse ce qu'une slide tient. Le formulaire ne reviendrait pas en premier : la slide dirait ce qu'elle ne sait pas faire, avec le ticket qui le lèvera.
- **§2.2** : une mesure d'usage où le second niveau coûte plus qu'il ne rend, par exemple un geste « Détails » ouvert à chaque visite. La réponse serait un réglage de la personne, pas un retour au détail affiché d'office.
- **§2.3** : la direction retenue se rouvre par le même geste que docs/39, jamais ticket par ticket.

## 7. Où cette décision est écrite ailleurs

- [docs/06](./06-roadmap.md), section « Le mode conception » : les jalons, leur contenu et leur rang.
- Un renvoi ⚠ à l'endroit de chaque décision rouverte :
  - [docs/43 §2.2](./43-decision-un-projet-nait-dans-la-conversation.md) ;
  - [docs/39 §7](./39-decision-niveau-visuel-choisi.md) ;
  - [docs/05 §1 et §2.0.1](./05-interface-control-tower.md) ;
  - [docs/38 §3.6](./38-decision-outillage-universel-du-projet.md).
- Les documents qui décrivent l'**état présent** (docs/05, `apps/web/README.md`) sont réécrits par les lots qui changent le code, pas ici.
