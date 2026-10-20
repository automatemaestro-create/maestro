<!-- documentation: développement -->
# 51 — Maestro face à la chaîne du dépôt, sur un vrai ticket

**Date :** 2026-10-20. **Instruite par :** #1522, lot du go/no-go #1510
([docs/48](./48-decision-produit-pro-exploitable.md)). La chaîne qui développe ce dépôt y sert de
second étalon ([docs/49](./49-decision-le-reglement-ne-grossit-plus.md) § 0).

Le même ticket du dépôt a été traité deux fois :
- **par Maestro**, confié par le fil depuis un clone à part, comme une personne le ferait ;
- **par la chaîne du dépôt**, `/ticket-start` → `/ticket-ship`, dans son worktree.

Les deux livrables ont ensuite été rejoués sur un clone propre. [docs/50](./50-ecart-face-a-un-agent-seul.md)
comparait Maestro à une session seule sur p5, un projet neuf. Ce document le compare à la chaîne
du dépôt, sur un dépôt existant de 2 600 fichiers. Il ne tranche pas le go/no-go : un passage de
chaque côté n'est pas une médiane.

**Pièces** :
- côté Maestro, le run `0300f3ffdddf` (2026-10-15), ses 55 transcriptions et le journal du
  passage, tenu sur #1522 ;
- côté chaîne, le run d'orchestration `20261020-141039`, la PR #1541 et sa transcription ;
- pour le rejeu, la grille de #1512 (`python -m maestro.scenarios.comparatif --rejouer`), plus un
  rejeu ciblé du dépôt et une sonde des critères du ticket (§ 3).

---

## 0. Ce qu'il établit

1. **La chaîne livre, Maestro pas.** La chaîne a mergé #1505 en 1 h 16, sur un geste. Maestro a
   travaillé 2 h 48 sur 18 gestes. Son run s'est soldé en échec, sans rien de mergé et sans
   aucune CI jouée.
2. **Maestro coûte moins : 9,08 $ contre 12,42 $.** Ses sessions démarrent sur un contexte court.
   La session unique de la chaîne relit 24,8 M tokens de cache, et paie en plus 1,74 $ de banc.
3. **Le livrable de la chaîne est meilleur sur deux axes.** Il tient 14 critères sur 14 contre 13,
   et 1 commit sur 1 suit la convention contre 0 sur 5. Les autres axes sont égaux.
4. **La chaîne l'emporte par six mécanismes** (§ 4). Deux deviennent des lots neufs de #1510 :
   - ce que l'agent reçoit d'un projet importé ;
   - la reprise d'une tâche coupée par la limite d'usage.

   Quatre étayent des lots qui existent déjà : #1530, #1531, #1507 et #1515.
5. **La grille de #1512 juge mal un dépôt existant** (§ 3.2) : elle rend 4/8 contre 1/8, pour des
   raisons de la grille, pas des livrables. #1544 reprend ses trois limites.

---

## 1. Le protocole

**Le ticket.** #1505 : la portée renvoie à une personne un `rm` sur motif shell et une écriture
sous une variable posée dans la même commande. Il a été choisi pour quatre raisons :
- il est petit : un module, `maestro/portee.py`, et ses tests ;
- il n'a pas d'écran ;
- un test déterministe le vérifie ;
- aucune session voisine ne touchait ce module.

Le choix est consigné sur #1522.

**Maestro, le 2026-10-15**, à la base `1663369e` :
- **le clone** : `~/Maestro/maestro-1505`, sans poussée vers `origin` ;
- **la stack** : celle du worktree de #1522, sur son propre espace Redis, jamais la copie que le
  projet contient ;
- **le fil** : importer le dossier, puis le ticket recopié tel quel. La personne connaît son dépôt
  et corrige ce que Maestro propose.

**La chaîne, le 2026-10-20**, à la base `50218814` : `bash scripts/orchestrate/run.sh --ticket
1505 --sans-banc`. C'est une session `/ticket-start` → `/ticket-ship`, que le pilote merge par
`merge-mr`. `maestro/portee.py` et ses tests sont identiques entre les deux bases.

**L'ordre.** Maestro a joué en premier, pour que ses agents ne voient pas la PR de l'autre côté.
Les deux côtés ne se sont jamais joués en même temps.

---

## 2. Les deux côtés, chiffrés

Les coûts viennent des transcriptions, au tarif ajusté de [docs/50](./50-ecart-face-a-un-agent-seul.md)
§ 1.1. Ce tarif reproduit la facture du CLI de la chaîne à 0,4 % près (10,673 $ pour 10,63 $).

| | Maestro | Chaîne du dépôt | Rapport |
| --- | --- | --- | --- |
| Du premier geste à la fin | **2 h 47 min 48 s** (fil 20 min 05 s, run 2 h 27 min 43 s) | **1 h 15 min 59 s** (session 68 min 52 s, CI et merge 4 min 47 s) | **2,2** |
| Coût | **9,08 $** (fil 1,89 $, run 7,19 $ ; le grand livre en voyait 7,17 $) | **12,42 $** (session 10,67 $, banc S6 1,74 $) | **0,73** |
| Sessions | 55 (38 du fil, 17 du run) | 1, plus le banc | |
| Réponses du modèle | 172 | 98 | |
| Cache relu · écrit · sortie | 9,7 M · 562 k · 132 k | 24,8 M · 342 k · 147 k | |
| Gestes de la personne | **18** : 6 messages et 12 clics, dont 2 arbitrages | **1** : la commande de lancement | **18** |
| Issue | **échec** : la dernière tâche est coupée par la limite de session du compte (§ 4.2) | **livré** : PR #1541 mergée, CI verte sur ses 6 contrôles, #1505 fermé | |

**Où passe le temps de Maestro** :

| Étape | Durée | Coût |
| --- | --- | --- |
| Fil | 20 min | 1,89 $ |
| Correctif (`dev-python`) | 27 min 41 s, dont ≈ 13 min de `local.sh` | 3,03 $ |
| Juge, fusion et accord d'écriture | 6 min 23 s | |
| Tests (`testeur-pytest`), dans une tâche à part | 19 min 01 s | 1,64 $ |
| « Vérifier et commiter » | 85 min 14 s en deux tentatives | 1,86 $ |
| Cadrage, plan et quatre juges | | 0,66 $ |

Le fil, lui, se décompose ainsi :
- **l'outillage** : 7 pièces proposées, 4 corrections en mots, 4 min 39 s de revérification ;
- **l'équipe** : 6 agents ;
- **l'accord** de lancement.

La seconde tentative de « vérifier et commiter » rejoue toute la suite en natif sous Windows,
pendant ≈ 50 min, après un `local.sh --complet` déjà vert.

**Où passe le temps de la chaîne** :
- ≈ 25 min d'implémentation ;
- ≈ 44 min de vérification : `local.sh` 10 min, le banc S6 ≈ 17 min (le changement touche ce que
  les agents peuvent faire, `/ticket-finish` 4ter), le filet rejoué 7 min 30 s et une suite
  rejouée seule 75 s.

---

## 3. Les livrables, rejoués sur un clone propre

### 3.1 Le rejeu du dépôt

Chaque livrable est cloné, installé dans un venv neuf, puis rejoué avec les commandes de la CI du
dépôt. Le diff est pris contre sa base : `1663369e` pour Maestro, le parent de `de9564ce` pour la
chaîne. La sonde rejoue 14 cas, avec la même portée des deux côtés :
- les deux commandes réelles du run `fea601a5c629`, relues dans le journal d'événements de C2 ;
- les variantes des trois comportements attendus.

Elle a été écrite avant de voir les livrables. Sur la base, elle en tient 9.

| Axe | Maestro | Chaîne |
| --- | --- | --- |
| Installation : `pip install -e ".[dev]"` | ✓ 119 s | ✓ 66 s |
| Lint : `ruff check .` | ✓ | ✓ |
| Formatage : `ruff format --check` sur les fichiers touchés | hors convention (*) | hors convention (*) |
| Types : `mypy maestro` | ✓ | ✓ |
| Tests ciblés : suites de la portée et tests ajoutés | ✓ 389 passés | ✓ 370 passés |
| CI : suite entière dans le conteneur, couverture ≥ 90 % | ✓ 8 240 passés | 4 rouges sous charge (« Cannot allocate memory »), verts rejoués seuls ; CI réelle verte sur la PR |
| Convention de commit, au hook `commit-msg` | ✗ **0/5**, plus 5 fusions « Maestro : … » | ✓ **1/1** |
| Critères de #1505 (sonde, 14 cas) | **13/14** : la commande réelle `$D/cadre-$n.html`, où `n=$(…)`, revient encore à une personne | **14/14** |
| Livré dans le dépôt | non | oui |

(*) Le dépôt n'est pas formaté par `ruff format`. La vérification échoue déjà sur ces fichiers à
la base, et `CLAUDE.md` n'exige que `ruff check`.

**Ce que chaque livrable contient** :
- **Maestro**, 5 fichiers, +774 −5 :
  - le correctif : `portee.py`, +247 −5 ;
  - une suite de tests neuve : `tests/test_portee_rm_motif.py`, +415 ;
  - l'outillage : `AGENTS.md` et deux skills, +112.

  Le commit conforme que l'agent a fini par écrire (`bd327480`) est resté sur une branche jamais
  fusionnée.
- **La chaîne**, 4 fichiers, +666 −89 :
  - le correctif ;
  - les tests, dans la suite existante `tests/test_portee.py` ;
  - une fixture tirée d'un vrai passage ;
  - la décision mise à jour dans docs/32.

### 3.2 La grille de #1512

Commande : `python -m maestro.scenarios.comparatif --rejouer maestro=<clone> --rejouer
chaine=<clone> --objectif <objectif de #1505>`. Elle a joué les axes que nomme #1522 et rendu
**4/8 pour Maestro, 1/8 pour la chaîne**. L'écart vient de la grille, pas des livrables.

- **Les commandes.** La grille les lit dans ce que le livrable déclare.
  - Maestro : l'`AGENTS.md` qu'il a écrit porte les chemins Windows du poste
    (`.venv/Scripts/python.exe`). Installation, lint et types sont donc tenus.
  - La chaîne : `CLAUDE.md` donne les deux variantes, et le lecteur a retenu la variante Unix
    (`.venv/bin/python`). L'installation sort en 127, et rien d'autre n'est joué.
  - Des deux côtés, `pytest` sur la suite entière dépasse le plafond de 300 s par commande.
- **La CI écrite** est jugée sur une liste de fichiers suivis tronquée, où
  `.github/workflows/ci.yml` n'apparaît pas. Elle manque donc des deux côtés, à tort.
- **La convention de commit** est jugée sur les derniers commits **du dépôt**, pas sur ceux du
  livrable. Chez la chaîne, ce sont des titres de squash d'autres tickets qui la font manquer.

Ces trois limites sont reprises par #1544. Sur un dépôt existant, la grille ne mesure pas encore le
livrable d'un ticket. Le rejeu du § 3.1 en tient lieu ici.

---

## 4. Ce qui fait l'écart, mécanisme par mécanisme

| Mécanisme | Chaîne du dépôt | Maestro, sur ces pièces | Gain pour Maestro | Suite |
| --- | --- | --- | --- | --- |
| **1. Ce que l'agent reçoit** | `CLAUDE.md` à chaque tour : conventions, filet, commits | Ses agents ne chargent jamais `CLAUDE.md` (`sans_reglages_du_poste`, `maestro/providers/claude.py`). L'`AGENTS.md` écrit à l'import prend ses conventions dans une liste fermée (§ 4.1), et ne nomme pas `CLAUDE.md` même après trois corrections. Le fil répond : « un projet lu garde ce qu'on y a constaté » | −4 gestes. Les conventions du dépôt arrivent aux agents sans que la personne les porte dans sa demande | **lot neuf #1542** |
| **2. Reprise après la limite d'usage** | Le pilote attend le reset et rouvre la même session (#171) | La dernière tâche, coupée par « session limit », est classée « tâches en échec », une cause non relançable (§ 4.2). Le run s'arrête à 2 h 28, le commit conforme non fusionné | Le run aboutit au lieu d'échouer. Pas de relance, qui referait au moins la dernière tâche (85 min, 1,86 $) | **lot neuf #1543** |
| **3. Granularité** | Un ticket = une capacité, tests compris, une session | Trois tâches : le correctif sans tests, les tests à part (19 min, 1,64 $), puis « vérifier et commiter » (85 min, 1,86 $) | La tâche de tests et ses deux juges, et la quasi-totalité de la tâche finale | **#1531** (existe) |
| **4. Vérification une fois** | `local.sh` une fois, un scénario du banc, la CI de la PR | `local.sh` dans la tâche 1, puis `local.sh --complet` et toute la suite en natif dans la tâche 3, plus quatre juges | ≈ 60 min sur le chemin | **#1530** (existe) |
| **5. Commits** | L'agent commite dans la convention, sous le hook `commit-msg` | 10 commits « Maestro : … ». L'agent l'a vu, a demandé l'accord de réécrire sa branche et a produit `bd327480`, jamais fusionné | Convention tenue | **#1507** (existe) |
| **6. Scripts du dépôt** | `setup.sh` est un geste de développeur, jamais joué par une session de run | La vérification de l'outillage a joué `bash scripts/setup.sh` sans arbitrage, et un agent `setup.sh --only venv`. Le skill qui le prescrivait n'a pas pu être corrigé en mots | Rien n'est écrit hors du projet | **#1515** (existe) |

**Où Maestro l'emporte** :
- **le coût**, de 27 % ;
- **l'outillage**, qui écrit pour le poste les commandes que `CLAUDE.md` laisse en deux variantes.

Sur ces pièces, ces deux points ne compensent ni la durée (×2,2), ni les gestes (×18), ni le
livrable qui n'est pas livré.

### 4.1 La liste fermée des conventions

Après la première correction, Maestro a revérifié ses commandes pendant 4 min 39 s : il a
réinstallé le venv, puis joué `npm ci` et le build Next, alors que le front était déclaré hors
sujet. Ses trois corrections suivantes n'ont pas eu l'effet demandé :

| Demande de la personne | Ce que la pièce est devenue |
| --- | --- |
| « nommer `CLAUDE.md` en tête des conventions » | inchangée, deux fois |
| « retirer `bash scripts/setup.sh` de ce skill » | inchangée, le fil ayant répondu « Vous demandez… » |
| « pas de `ruff format` » | prise |

La liste des conventions d'`AGENTS.md` (`.editorconfig`, `CONTRIBUTING.md`, `LICENSE`,
`README.md`, `docs`) est un relevé. Le principe de [docs/41](./41-decision-maestro-juge-il-ne-bride-pas.md)
l'écarte : aucun catalogue fermé ne sert de mécanisme principal.

### 4.2 La limite d'usage lue par le texte

`maestro/controltower/causes.py` reconnaît la limite d'usage à un motif dans le message de
l'erreur. Le motif est repris de `run.sh`, mais `run.sh` lit aussi l'événement typé du flux.
« You've hit your session limit » ne correspond pas à ce motif, et la tâche tombe donc en
« tâches en échec » au lieu de « limite d'usage ». La seconde cause figure dans
`CAUSES_RELANCABLES`, la première non. Lire une panne dans son texte est ce que
[docs/44](./44-decision-maestro-possede-ses-contrats.md) écarte. #1529 pose la même question
pour le banc.

---

## 5. Ce que cette mesure ne sait pas

- **Un passage de chaque côté, à cinq jours d'écart.** Le 2026-10-15, trois sessions sœurs
  jouaient leurs propres runs sur le poste. Le 2026-10-20, il était plus calme. Les durées ne sont
  pas des médianes.
- **La chaîne a lu ce que Maestro ne pouvait pas lire.** Sa session a cherché dans les
  transcriptions du poste (`~/.claude/projects`) les commandes réelles du run de C2. Les agents de
  Maestro sont bornés à leur projet, et c'est voulu. Le 14/14 contre 13/14 en dépend en partie.
- **Les chiffres de Maestro sont des bornes basses.** Son run a été coupé avant la fusion du commit
  conforme : sa durée et son coût réels jusqu'à une issue manquent.
- **La personne connaît le dépôt.** Les corrections d'outillage sont celles d'une développeuse. Une
  personne qui ne code pas aurait accepté l'outillage tel quel, avec `setup.sh` prescrit aux
  agents.
- **Le banc S6** que la chaîne a joué est compté dans son coût. Le tarif de C2 le chiffre au grand
  livre de son passage, qui compte le fil depuis #1537.

## 6. Un constat sur la chaîne elle-même

Sur `main`, une douzaine des soixante derniers titres de squash ne suivent pas la convention, par
exemple les PR #1528 et #1537. `merge-mr` reprend le titre de la PR, et ce titre est celui du
ticket, une phrase. Les commits de branche, eux, passent le hook. C'est une question d'outillage,
dont la part est plafonnée (docs/49 § 0) : elle est laissée à l'arbitrage, sans ticket ouvert ici.

## 7. Où cette analyse est écrite ailleurs

- **#1522** : le rapport, le choix du ticket et le journal des gestes.
- **#1542** et **#1543** : les deux lots neufs de #1510.
- **#1507, #1515, #1530, #1531** : une note y verse les pièces de ce passage.
- **#1544** : les limites de la grille de #1512 sur un dépôt existant.
