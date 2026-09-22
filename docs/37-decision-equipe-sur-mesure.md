# 37 — L'équipe sur mesure : chaque projet s'outille et recrute ses agents

**Date :** 2026-09-19. **Instruite par :** `/idee` (#1013). **Consignée par :** #1044.
**Jalon :** *L'équipe sur mesure — chaque projet s'outille et recrute ses agents* (échéance 2028-01-05).
**Chantiers :** #1022 (répertoire des projets), #1019 (question et arbitrage autonome), #1020 (outillage universel), #1021 (équipe sur mesure).

---

## 0. Ce que ce document décide

Quatre évolutions, demandées ensemble le 2026-09-19 :

1. **Un répertoire commun des projets.** Un projet neuf y naît. Le réglage est rempli par défaut et modifiable. Un projet existant se choisit toujours en parcourant soi-même (§4.7).
2. **L'outillage d'abord.** La première étape de tout projet est la génération de son outillage : commandes, skills et scripts, dans des **formats ouverts** qu'un autre agent que Claude sait exécuter. Sur un projet existant, l'outillage est recommandé par une analyse ; sur un projet neuf, il découle des choix de l'utilisateur.
3. **Aucun agent figé.** Un projet naît **sans agent**. L'analyse du projet propose son équipe (rôles, nombre, instances, playbooks, skills et autorisations), l'utilisateur la valide, et elle est créée **dans le projet**.
4. **Autonomie sous arbitrage.** Tout agent peut **demander** à l'utilisateur à tout moment, et **tranche seul** ce qui ne requiert pas d'humain, **en le consignant**.

Deux de ces points **renversent** une décision écrite (§2). La personne a demandé explicitement les deux renversements : aucun n'a été tranché à sa place.

## 1. D'où vient la demande

Voici la demande, dans ses mots :
- « à la création ou import d'un projet, aucun agent défini encore, c'est-à-dire supprimer les agents qu'on a figés avant. Je veux que les agents soient créés directement selon le projet / besoin » ;
- « tu pourras à tout moment demander l'arbitrage de l'utilisateur, les validations pour une action / recommandation / etc. Tu n'es pas silencieux mais tu n'es pas non plus dépendant de l'humain […] chaque agent devrait pouvoir agir ainsi ».

L'analyse complète, avec ce qui existait, les standards vérifiés et chaque arbitrage, est le corps de #1044.

## 2. Ce qui est renversé

### 2.1 Les agents cessent d'être une ressource du poste

**Avant.** [docs/05 §2.0](./05-interface-control-tower.md) rangeait le parc d'agents, le catalogue et les playbooks parmi ce qui **reste global**, avec cette raison : « les partager entre projets est l'intérêt d'en avoir ». La Phase 2 de [docs/06](./06-roadmap.md) livrait « **les 6 agents** par défaut », et [docs/04 §2](./04-specifications-agents.md) en tenait le catalogue.

**Après.** Un agent **appartient à un projet** : sa définition, son playbook, ses autorisations, ses serveurs MCP et sa capacité y sont rangés (#1038). Le catalogue figé devient un catalogue de **gabarits de rôle** que l'analyse d'équipe consulte (#1039) et qu'aucun projet n'instancie d'office (#1042).

> ⚠ **Renversé en partie le 2026-09-21** ([docs/41](./41-decision-maestro-juge-il-ne-bride-pas.md), #1159). L'analyse d'équipe ne choisit plus parmi cinq gabarits **par des règles fixes** (un designer sur des fichiers `.css`, le rôle données sur des fichiers `.sql`…). Le modèle propose les rôles dont le projet a besoin, quels qu'ils soient, et la personne en ajoute un avec ses mots. Les gabarits restent une matière, comme le §7 l'avait prévu. L'appartenance d'un agent à son projet ne bouge pas.

**Livré par #1038** (docs/05 §2.0, §2.3, §6.0quater) : deux niveaux par dépôt — la racine pour les gabarits, `_projets/<projet_id>/` pour un projet —, l'API cadrée par `?projet=`, l'exécution qui lit ceux du projet de la tâche, et une **reprise** idempotente qui rattache sans perte ce qu'un poste portait déjà. Une règle et une exception nommée : *le projet recouvre le gabarit, et ce qu'il ne règle pas il l'hérite* — sauf l'**existence** d'un agent, qui ne s'hérite pas (un agent rangé à la racine est un gabarit, pas un membre d'équipe). Sans ce repli, ranger les autorisations par projet aurait fait d'un projet neuf un projet « tout permis ».

**Pourquoi.** La raison de 2.0 supposait qu'un bon agent vaut pour tous les projets. La demande pose le contraire : l'équipe se **dérive** du projet, jusqu'au nombre d'instances. Un agent partagé entre un projet Python et un projet mobile porterait les skills et les autorisations de l'un chez l'autre.

**Ce qui est gardé.** Les playbooks « senior » des agents figés ne sont pas jetés : ils deviennent la matière des gabarits. C'est un arbitrage (§4.1), et il se défait en un lot si l'on n'en veut pas.

**Livré par #1042.** `catalogue()` ne rend plus que les agents de son dépôt : un projet créé ou importé n'en a **aucun**, et `GET /api/catalogue?projet=<id>` comme `GET /api/agents?projet=<id>` le disent en rendant une liste vide. Trois conséquences qui se tiennent ensemble :
- **une équipe vide n'est pas une omission** : le routeur distingue « je n'ai pas d'équipe à te donner » (hors projet) de « ce projet n'a personne », et une tâche du second part en repli « à assigner » au lieu d'aller aux rôles du code ;
- **les noms des gabarits restent réservés**, pour une raison neuve : le paquet livre un playbook sous chacun d'eux, qui masquerait celui d'une fiche de projet homonyme (`playbook_outille`) — c'est aussi pourquoi les rôles proposés portent d'autres noms (`dev` descend de `developpeur`) ;
- **une surcharge (#259) règle désormais un gabarit**, plus un agent : elle se lit par `gabarits_du_code()` et n'atteint l'exécution que par le catalogue de câblage d'un run **hors projet** (`catalogue_hors_projet`) — dans un projet, ce qu'on règle est la fiche d'un agent de l'équipe, dont la définition *est* le réglage.

**Corrigé par #1101** (réserve C4 du bouclage, docs/05 §2.3). Ce vide est l'état **normal** du catalogue de la racine, donc aussi celui de la projection d'état de l'API, qui en part. `GET /api/agents?projet=<id>` s'y adossait : elle filtrait la projection sur les noms de l'équipe, si bien qu'une équipe validée disparaissait de l'écran au redémarrage suivant, et portait entre-temps une instance là où son projet en avait rangé deux. Elle **dérive** désormais son parc du catalogue du projet et de ses capacités, la projection n'y apportant que l'activité. Le critère « types, nombre et **instances** dérivés de l'analyse » n'était donc pas faux sur le disque ni en exécution — c'était la vue qui ne le montrait pas.

Hors de tout projet, un moteur travaille encore avec les gabarits : `maestro-run` lancé sans `--projet` n'en a pas. Ce n'est pas une exception au principe, c'est son complément — il n'y a pas d'équipe où chercher. `maestro-run --projet <id>` dit dans quel projet travailler, et prend alors son équipe, y compris pour l'agent de `--notifier`.

### 2.2 « Personne ne répond en cours de tâche » tombe

**Avant.** Le socle des playbooks (`maestro/agents/playbooks_defaut/_socle.md`, [docs/04 §1.2](./04-specifications-agents.md)) portait trois volets, et deux d'entre eux **restent** :
- ce que l'agent décide seul (le réversible) ;
- ce qu'il remonte (l'irréversible, le hors-périmètre).

Sa règle centrale, elle, disparaît : *une hypothèse énoncée vaut mieux qu'une question posée — personne ne répond en cours de tâche*.

**Après.** Un agent **pose une question** quand la décision requiert un humain : un acte irréversible, un coût ou une portée qui dépasse le brief, un choix produit à deux issues défendables. Sa tâche est suspendue jusqu'à la réponse ou jusqu'à une borne (#1023). La question arrive dans le fil (#1025). Tout le reste se **tranche seul, et se consigne** (#1024), puis se lit dans la vue du run (#1026).

**Pourquoi.** La règle était juste tant qu'aucun canal n'existait. Or ce canal a été **perdu entre deux cadrages** : [docs/31 §3.1](./31-decision-surface-ecriture-agents.md) le renvoyait à #647, [docs/32 §5](./32-decision-cran-orchestrateur.md) à #354. Les deux tickets se sont fermés, et il n'a jamais été construit. Les pièces de suspension existent déjà (`BornesArbitrage`, `CreditArbitrage` de #584, `MemoireArbitrage`) : docs/32 §5.3 les avait nommées pour ce cas.

## 3. Ce qui ne bouge pas

- **[docs/32](./32-decision-cran-orchestrateur.md) : aucune IA ne juge l'appel d'outil d'une autre IA.** L'autonomie demandée porte sur les **décisions de travail**, jamais sur la couche de permissions, qui reste déclarée par une personne, outil par outil (#716).
- **EF-08 / ENF-04 : sans réponse, un acte soumis à validation est refusé.** Une question sans réponse fait continuer l'agent sur une hypothèse écrite. Un acte, lui, ne passe jamais faute de réponse. La question ne contourne pas la validation : ce sont deux canaux (docs/32 §5.3).
- **[docs/34 §4.6](./34-decision-agent-cli-tiers-acp.md) : la configuration ambiante reste fermée.** L'outillage généré dans le projet est **transmis explicitement** à nos agents (#1032), dans la limite de ce que le manifeste déclare. Aucun fichier du projet ne s'impose au runtime. ⚠ [docs/38 §5.3](./38-decision-outillage-universel-du-projet.md) a mesuré que la porte n'est fermée que sur les serveurs MCP : `setting_sources` n'est passé nulle part, donc les réglages et les `CLAUDE.md` du projet sont chargés. Rien ne s'en ressentait tant que Maestro n'écrivait pas ces fichiers — c'est ce jalon qui rend le trou atteignable, et #1032 qui le referme.
- **[docs/24 §2.4](./24-projets-locaux-et-poste-de-travail.md) : le régime d'écriture dans le projet.** La génération de l'outillage suit ce régime comme toute écriture : fusion sous accord si le projet est versionné, écriture en place sinon (#1033).
- **[docs/31 §3.5](./31-decision-surface-ecriture-agents.md) : un agent ne recrute pas pendant un run.** Une tâche qu'aucun rôle ne sait prendre est **signalée** (#1041). Recruter reste un geste validé hors du run.
- **D5 : le brief est validé avant la décomposition.** L'équipe ne se forme pas dans un run, elle se forme à la création du projet.

## 4. Les arbitrages rendus par `/idee`, à contredire au besoin

1. **Les agents figés deviennent des gabarits de rôle**, pas des agents (§2.1). La demande est tenue à la lettre, puisque aucun projet ne naît avec un agent, et la matière n'est pas perdue.
2. **L'orchestrateur n'est pas un membre de l'équipe.** Il est Maestro, présent dans tout projet, et c'est lui qui recrute.
3. **Les autorisations sont proposées par l'analyse, puis validées par l'utilisateur** (#1039, #1040). Chaque permission `auto` est nommée avec sa raison. Une permission `auto` reste donc une décision humaine prise à l'avance ; seule sa **rédaction** devient automatique.
4. **Une question échue n'arrête pas l'agent.** Il continue sur l'hypothèse qu'il avait annoncée en posant la question, et l'hypothèse est écrite. « Pas dépendant de l'humain » vaut pour les choix, jamais pour les actes (§3).
5. **Le format de l'outillage : `AGENTS.md` et Agent Skills.** Vérifiés le 2026-09-19 :
   - [AGENTS.md](https://agents.md/) est porté par l'Agentic AI Foundation (Linux Foundation) et lu par plus de 20 agents ;
   - [Agent Skills](https://agentskills.io/specification) (`SKILL.md`, `scripts/`, `references/`, `assets/`) est pris en charge par Claude Code, Codex, Gemini CLI, Copilot, Cursor, Mistral Vibe, OpenHands, Goose, etc.

   **L'emplacement des skills n'est fixé par aucune spécification.** Il se tranche dans #1029, une fois vérifié où chaque client cherche les siens, et `AGENTS.md` le désigne.

   ✅ **Tranché** le 2026-09-20 par [docs/38](./38-decision-outillage-universel-du-projet.md) (#1029) : les skills vont dans `.agents/skills/`, le seul chemin projet lu par plus d'un client — trois sur quatre. Aucun chemin n'est lu par les quatre : `AGENTS.md` désigne le dossier pour le quatrième, et deux ponts d'une ligne (`CLAUDE.md`, `GEMINI.md` contenant `@AGENTS.md`) le joignent, parce qu'`AGENTS.md` seul ne les atteint pas tous. Aucun fichier de commande n'est généré, faute de format commun.
6. **L'étape d'outillage est première et proposée d'office, mais reportable** (#1034). Importer un projet pour le regarder ne doit pas imposer une génération. Un projet non outillé le **dit**.
7. **Le répertoire proposé par défaut** est un dossier `Maestro` sous le dossier personnel, créé à la première utilisation (#1022). La racine nue du dossier personnel reste refusée par `valider_racine`, alors qu'un sous-dossier est admis.

## 5. Le découpage

Le jalon compte un ticket isolé et trois chantiers. Ils sont dans l'ordre de leurs dépendances : la question vient d'abord, parce que l'outillage d'un projet neuf et l'équipe proposée **se demandent** ; l'outillage vient avant l'équipe, parce que l'équipe **branche** ses skills.

Les lots marqués ∥ portent `lot::parallele`.

**#1022 — Répertoire des projets.** Ticket isolé.

**#1019 — Question et arbitrage autonome** (5 lots) :

| Lot | Ticket | Contenu |
| --- | --- | --- |
| 1 ∥ | #1023 | Question libre et suspension |
| 2 ∥ | #1024 | Socle et décisions consignées |
| 3 | #1025 | La question dans le fil |
| 4 ∥ | #1026 | Les décisions dans la vue du run |
| 5 | #1027 | Tests + doc |

**#1020 — Outillage universel** (7 lots) :

| Lot | Ticket | Contenu |
| --- | --- | --- |
| 1 | #1029 | Format |
| 2 ∥ | #1030 | Analyse d'un projet existant |
| 3 ∥ | #1031 | Choix d'un projet neuf |
| 4 ∥ | #1032 | Les agents lisent l'outillage |
| 5 | #1033 | Génération |
| 6 | #1034 | Parcours de création |
| 7 | #1035 | Tests + doc |

**#1021 — Équipe sur mesure** (7 lots) :

| Lot | Ticket | Contenu |
| --- | --- | --- |
| 1 ∥ | #1037 | Runtime outillé depuis la fiche |
| 2 ∥ | #1038 | Agents par projet |
| 3 | #1039 | Proposition d'équipe |
| 4 | #1040 | Validation et création |
| 5 ∥ | #1041 | Routage sur l'équipe |
| 6 | #1042 | Un projet naît sans agent |
| 7 | #1043 | Tests + doc |

### 5.1 Ce que le chantier #1021 a livré, et ce qui le garde (#1043)

Les lots 1 à 6 ont livré **sans tests**, par la convention de découpage ([docs/10 §5.1](./10-workflow-git.md)) : ils sont différés au lot final, qui les écrit d'un bloc. Ce que chaque suite tient, dans l'ordre du chantier :

| Ce qui est gardé | Où |
| --- | --- |
| Le **runtime outillé se dérive de la fiche** — métier de la fiche, cadre du code quand il en déclare un, cadre générique sinon, et le **cadre d'exécution ajouté** au playbook d'une fiche : sans lui l'agent répondrait en texte avec des outils dans les mains | [`tests/test_fiche_outillee.py`](../tests/test_fiche_outillee.py) |
| Le **rangement par projet** des six dépôts, le repli du gabarit là où il a un sens, l'**exception** de l'existence qui ne s'hérite pas, et la **reprise** des agents globaux — sans perte, idempotente, et qui dit ce qu'elle a fait | [`tests/test_rangement_projet.py`](../tests/test_rangement_projet.py) |
| La **proposition** : rien n'est créé (aucun module du paquet n'ouvre un fichier en écriture), rien sans son endroit, ce qui n'est pas proposé est nommé — l'orchestrateur compris —, et **chaque autorisation porte sa raison**, le cran `auto` en premier (§4.3). Depuis #1102, le **playbook et l'autorisation sont d'accord** : l'`intention` d'où #257 écrit le playbook porte le cran proposé pour l'outil d'exécution ([docs/38 §5.5](./38-decision-outillage-universel-du-projet.md)) | [`tests/test_equipe_proposition.py`](../tests/test_equipe_proposition.py) |
| La **création validée** : tout est vérifié avant la première écriture et un seul refus n'écrit rien, ce qui est créé est ce qui a été montré, l'écriture va **dans le projet** — et déclarer un projet n'instancie **aucun** agent | [`tests/test_equipe_creation.py`](../tests/test_equipe_creation.py) |
| Le **routage sur l'équipe** : une règle pour ses deux lecteurs, le catalogue remplacé pour l'appel, l'équipe **vide** qui n'est pas une omission, ce que personne ne couvre nommé en **poste**, et le signal qui redit que **le recrutement se fait hors du run** (§3.5) | [`tests/test_equipe_routage.py`](../tests/test_equipe_routage.py) |

**« Un projet naît sans agent » est gardé à trois niveaux**, parce qu'il se défait à trois endroits : le **dépôt** (l'existence ne s'hérite pas du gabarit, donc déclarer un projet n'écrit aucune fiche), le **catalogue effectif** (`ConfigurationAgents.catalogue()` rend `()` sur un projet neuf, et exactement l'équipe validée après coup), et le **routage** (une équipe `()` fait attendre la tâche au lieu de la router sur les gabarits). Les trois ensemble : une tâche d'un projet qui n'a recruté personne reste « à assigner », et un run **hors projet** continue de travailler sur le câblage.

**Et il n'y reste pas faute d'y avoir repassé** (#1146). L'équipe ne se proposait que dans le parcours de création, qu'un projet antérieur — ou dont l'étape a été passée — ne revoit jamais : une demande de travail y ouvrait un run qui payait cadrage et plan puis échouait au routage. Le fil de l'orchestration compte désormais l'équipe du projet (la règle du routage, `catalogue_du_projet`) avant de proposer un run. Quand il n'y a personne, il propose l'équipe, la crée sur validation par la voie de #1040, puis repropose le travail. C'est l'orchestrateur qui recrute (§4.2), jamais sans validation, jamais pendant un run (§3.5). Gardé par [`tests/test_chat_global.py`](../tests/test_chat_global.py) (section ⑪) et [`tests/test_projet_outille_http.py`](../tests/test_projet_outille_http.py) (section ⑧).

**Différé, et pourquoi.** L'**exécution outillée par un fournisseur non-Anthropic dans Maestro** reste hors du jalon. Le format de l'outillage est universel, et un autre agent le lit sur le poste de l'utilisateur. Mais dans Maestro, seuls les modèles Claude ont des outils : `openai_compat.py` ne fait que du texte. C'est l'objectif O7 ([docs/00](./00-cahier-des-charges.md)), un chantier à lui seul, noté dans [docs/06](./06-roadmap.md) « Au-delà ».

## 6. La place dans la file

Sur le rail produit, l'échéance d'un jalon **est** son rang (`current-milestone` trie par `DUE_DATE`) :

| Jalon | Échéance |
| --- | --- |
| L'atelier | 2027-11-09 |
| **L'équipe sur mesure** | **2028-01-05** |
| Avant l'installeur — les réserves levées *(ajouté le 2026-09-21)* | 2028-01-26 |
| Phase 9 | **2028-02-16** (était 2027-11-10) |

- **Après « L'atelier »**, qui est en cours (#921) et qu'on ne double pas.
- **Devant la Phase 9**, pour l'argument de la Phase 9 elle-même : on n'empaquette pas une cible mouvante ([docs/24 §4.8](./24-projets-locaux-et-poste-de-travail.md)). La création d'un projet et le modèle d'agents changent ici : ils doivent changer **avant** l'installeur, pas pendant. Le même argument avait fait passer « L'atelier » devant la Phase 9 ([docs/35 §2](./35-decision-poste-de-bureau-et-disposition.md)).

> ⚠ **Un jalon s'est inséré le 2026-09-21** (#1113) : « Avant l'installeur — les réserves levées ». Il réunit les réserves des verdicts de ce jalon (R3 #1101, R4 #1102, R7 #1104, et #1105) et celles de « L'atelier », qu'on avait rangées en Phase 9 derrière l'installeur. Le même argument le place devant la Phase 9. Ce qui est écrit ici ne change pas : ce jalon-ci reste devant la Phase 9, et son échéance n'a pas bougé. Voir [docs/06](./06-roadmap.md), section « Avant l'installeur ».

## 7. Ce qui rouvrirait la décision

- **Un agent partagé entre projets** redevient défendable si plusieurs projets réels demandent la **même** équipe au mot près. On outillerait alors le **partage d'un gabarit**, pas le retour d'un agent global.
- **Le canal de question** se rouvre s'il noie la personne. La mesure est le nombre de questions par run et la part de questions échues. Le remède serait une borne par agent, pas le retour à « personne ne répond ».
- **Les gabarits** se retirent si l'analyse d'équipe s'en passe mieux, ce qui se mesure sur les équipes proposées.

## 8. Où cette décision est écrite ailleurs

Un renvoi ⚠ vers cette note est posé à l'endroit de chaque décision renversée ou rendue fausse :
- [docs/04 §1.2, §2 (le catalogue **est** devenu celui des gabarits, #1043) et §4](./04-specifications-agents.md) ;
- [docs/05 §2.0 et §6.19](./05-interface-control-tower.md) ;
- [docs/06, Phase 2](./06-roadmap.md) ;
- [docs/00 EF-03 et EF-21](./00-cahier-des-charges.md) ;
- [docs/24 §2.3 et §6](./24-projets-locaux-et-poste-de-travail.md) ;
- [docs/32 §5](./32-decision-cran-orchestrateur.md) ;
- la section « Les agents (par défaut) » du [README](../README.md).

Ces documents décrivaient l'état **d'avant** : le lot final (#1043) a réécrit ceux que le chantier #1021 rend faux — docs/04 §2 et §4, docs/05 §2.0 et §6.19, docs/00 EF-01, EF-02, EF-03 et EF-21. Les autres renvois restent des avertissements : ils disent que l'état change là où le code n'a pas encore bougé.
