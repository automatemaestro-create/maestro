<!-- documentation: développement -->
# 56 — Maestro de nouveau face à la chaîne du dépôt : les cinq leviers remesurés, et le seuil de la routine

**Date :** 2026-11-16. **Instruite par :** #1587, au jalon go/no-go « Maestro fait mieux qu'un agent
seul » ([docs/48](./48-decision-produit-pro-exploitable.md)), dont elle fournit la pièce du critère
C4. Elle rejoue la mesure de [docs/51](./51-maestro-face-a-la-chaine-du-depot.md) (#1522) sur un
ticket neuf. Entre-temps, cinq des six mécanismes de l'écart ont été livrés.

Le même ticket du dépôt, #1592, a été traité deux fois :
- **par Maestro**, confié dans le fil depuis un clone à part, comme une personne le ferait ;
- **par la chaîne du dépôt**, de `/ticket-start` à `/ticket-ship`.

Les deux livrables ont ensuite été rejoués sur un clone propre. Un seuil, posé sur #1587 **avant**
l'épreuve, dit si la routine « Maestro sur son propre dépôt » ([docs/06](./06-roadmap.md)) peut
s'ouvrir.

**Pièces**, toutes relevées sur #1587 :
- **côté Maestro** : le run `caf51d06c14f` du 2026-11-04, son grand livre, le journal des gestes, et
  le livrable `~/Maestro/maestro-1592` à `fcb0681` ;
- **côté chaîne** : la session `chaine-1592` du même jour, la PR #1595 et son squash `c48ca249` ;
- **pour le rejeu** : la grille de #1544, au passage `20261116-103716`, et un rejeu ciblé des
  commandes de la CI (§ 3).

---

## 0. Ce qu'il établit

1. **Le seuil n'est pas tenu, et la routine reste fermée.** Une seule des quatre conditions tient,
   celle des gestes.
   - **Le livré** manque : la CI du dépôt rougirait sur le livrable de Maestro.
   - **La qualité** manque, par ses seuls commits. Maestro tient plus de critères que la chaîne, 3/4
     contre 2/4, mais aucun de ses 18 commits ne passe le hook du dépôt.
   - **La durée** manque : 8,4 fois celle de la chaîne, pour une limite de 1,5. Le coût, qui forme
     avec elle la quatrième condition, tient de son côté (0,90 fois).

   #1515, l'autre condition de la routine, n'est pas livré non plus.
2. **Cette fois, Maestro livre, mais pas dans les règles du dépôt.** Son run s'est terminé, alors
   que celui de #1522 avait échoué. Son correctif tient un critère de plus que celui de la chaîne.
   Mais son outillage fait rougir `test_audit_commandes`, le budget de `CLAUDE.md`, et 0 commit sur
   18 passe le hook `commit-msg`, contre 1 sur 1 pour la chaîne.
3. **Les leviers ont surtout fait progresser Maestro face à lui-même.** De #1522 à #1587 :
   - les gestes passent de 18 à 13, et les corrections de l'outillage de 4 à 0 ;
   - les tâches passent de 3 à 1 ;
   - le coût passe de 9,08 $ à 6,06 $ ;
   - le run se termine au lieu d'échouer.

   La durée, elle, ne bouge presque pas : 2 h 48, puis 2 h 37.
4. **L'écart de durée s'est creusé, de ×2,2 à ×8,4.**
   - La chaîne est passée de 1 h 16 à 19 min : le ticket est plus petit, et il n'y a eu ni banc ni
     `run.sh`.
   - Maestro garde ses 2 h 37. L'agent a passé environ 1 h 23 dans la suite entière, tentée six fois
     au total. S'y ajoutent trois vérifications et une recette, soit 35 min.
5. **Quatre écarts deviennent des tickets du jalon** (§ 6) :
   - **#1678** : l'outillage d'un projet importé ;
   - **#1679** : la convention de commit d'un projet importé ;
   - **#1680** : la vérification et la recette ;
   - **#1681** : la grille sur un vrai dépôt.

---

## 1. Le protocole

**Le ticket.** #1592 : au refus d'un dossier, le fil annonce une proposition qui n'est pas là, et la
cause finit par un double point. Il a été choisi le 2026-11-04 pour cinq raisons :
- il appartient au jalon courant ;
- il est petit : un module, `maestro/controltower/orchestration.py`, et ses tests ;
- il n'a pas d'écran, et il ne touche ni `.claude/` ni le moteur d'orchestration ;
- un test déterministe le vérifie ;
- aucune session ne touchait ce module.

Le choix, et les tickets écartés, sont consignés sur #1587.

**Maestro**, le 2026-11-04 de 10 h 04 à 12 h 41, à la base `005bd960` :
- **le clone** : `~/Maestro/maestro-1592`, la poussée vers `origin` désactivée ;
- **la stack** : celle du worktree de #1587, sur les ports 8087/3087 et son propre espace Redis ;
- **le fil** : la session y joue la personne avec `maestro.scenarios.gestes`, sous le compteur du
  banc (`ClientCompteur`). Elle importe le dossier, puis confie #1592 recopié tel quel. Elle accepte
  ce que Maestro recommande et ne corrige pas l'outillage.

**La chaîne**, le même jour de 13 h 34 à 13 h 53, à la même base. Le CLI `claude` du poste était
déconnecté, et `run.sh` ne partait pas. La chaîne a donc été jouée dans une session interactive
neuve de l'application de bureau, sur Opus en effort `xhigh`, avec les trois étapes de
`prompt_ticket` de `run.sh`. Trois écarts avec un run ont été nommés avant de lire le résultat :
- **la clôture va jusqu'au merge dans la session**. Les 6 min 17 s de pipeline comptent donc dans
  sa durée ;
- **le banc des scénarios**, dû, était injouable : il a été déclaré « non joué » ;
- **le mode de permission `auto`** n'a demandé aucune approbation.

**Le rejeu**, le 2026-11-16, une fois le blocage du CLI levé. La grille passe par le fournisseur du
produit, qui se connecte par le jeton du `.env`, et #1676 y a rangé aussi `run.sh`. Les critères de
l'objectif avaient été écrits le 2026-11-04, avant que les livrables ne soient vus (§ 3.1).

**L'ordre.** Maestro a joué en premier, pour que ses agents ne voient pas la PR de l'autre côté. Les
deux côtés ne se sont jamais joués en même temps.

---

## 2. Les deux côtés, chiffrés

Les coûts sont ceux des transcriptions, au tarif de [docs/50](./50-ecart-face-a-un-agent-seul.md)
§ 1.1. Côté Maestro, ce tarif retombe sur le grand livre du projet à 1 centime près.

| | Maestro | Chaîne du dépôt | Rapport |
| --- | --- | --- | --- |
| Du premier geste à la fin | **2 h 37 min 09 s** (fil 8 min 34 s, run 2 h 28 min 44 s) | **18 min 43 s**, dont 6 min 17 s de pipeline et de merge | **8,4** |
| Coût | **6,06 $** (run 4,32 $, fil 1,73 $ ; le grand livre en voit 6,05 $) | **6,74 $** | **0,90** |
| Appels et tours | 42 appels, 137 tours | 1 session, 93 réponses | |
| Cache relu · écrit · sortie | 3,8 M · 460 k · 80 k | 19,7 M · 220 k · 48 k | |
| Gestes de la personne | **13**, aucune correction | **1**, la consigne | **13** |
| Issue | **terminé** : le livrable est fusionné dans le clone, sans poussée, comme le veut le protocole | **livré** : PR #1595 mergée par `merge-mr`, #1592 fermé | |

**Les 13 gestes de Maestro** se partagent en deux groupes :
- **9 relèvent de l'import, et ne se paient qu'une fois par projet** : le message d'import,
  l'accord du projet, et les sept pièces d'outillage acceptées telles quelles ;
- **4 relèvent du ticket** :
  - le ticket confié ;
  - l'équipe validée, cinq agents dont un seul a servi ;
  - l'accord du run ;
  - un arbitrage de portée, « Écrire dans le projet maestro-1592 au fil de ce run », tranché 6 s
    après la demande.

**Où passe le temps de Maestro** (heures du poste) :

| Étape | Horaire | Durée |
| --- | --- | --- |
| Fil : import, accord du projet, sept pièces, ticket, équipe, accord du run | 10 h 04 – 10 h 13 | 8 min 34 s |
| Cadrage, brief, et un plan d'une seule tâche | 10 h 13 | 50 s |
| Correctif ; tests ciblés vus rouges (8), puis verts ; lint ; types | 10 h 14 – 10 h 19 | 5 min 10 s |
| Suite entière (`-n auto`), et commit pendant qu'elle tourne | 10 h 19 – 10 h 29 | 10 min 40 s |
| Vérification 1 : 3/6 | 10 h 30 – 10 h 37 | 7 min 41 s |
| Reprise : la suite entière deux fois (1 h 12 min 42 s), le tri de 10 échecs, la consigne réécrite et le compte rendu | 10 h 37 – 12 h 00 | 1 h 22 min 23 s |
| Vérification 2 : 4/6 | 12 h 00 – 12 h 11 | 11 min 28 s |
| Reprise : une phrase recoupée pour une expression régulière, et des tests ciblés | 12 h 11 – 12 h 25 | 13 min 46 s |
| Vérification 3 : 6/6 | 12 h 25 – 12 h 32 | 6 min 48 s |
| Arbitrage de l'écriture et fusion | 12 h 32 | 15 s |
| Recette du run : 3/6 | 12 h 32 – 12 h 41 | 8 min 53 s |

La suite entière a été tentée **six fois** sur ce chemin :
- trois fois par l'agent ;
- une fois par chacune des deux premières vérifications ;
- une fois par la recette.

Une seule tentative est allée au bout : celle de l'agent, avec 10 échecs sur 8 706 tests. La
troisième vérification a joué les seules suites du module, 1 448 tests en 55 s.

**Où passe le temps de la chaîne** : environ 12 min de session jusqu'à la PR, puis 6 min 17 s de
pipeline et de merge. Le cache relu domine le coût, puisque chaque appel relit environ
210 000 tokens de contexte.

---

## 3. Les livrables, rejoués sur un clone propre

### 3.1 La grille de #1544

La commande est la suivante :

```bash
python -m maestro.scenarios.comparatif --rejouer maestro=~/Maestro/maestro-1592 \
  --rejouer chaine=<clone à c48ca249> --base maestro=005bd960 --base chaine=005bd960 \
  --objectif objectif-1592.toml
```

L'objectif porte les choix de la personne : le venv du dépôt, pytest, `ruff check`, `mypy maestro`,
aucun formateur, GitHub Actions, et les Conventional Commits avec `Refs` ou `Closes`. Il porte aussi
les quatre critères de #1592.

| Ligne | Maestro | Chaîne |
| --- | --- | --- |
| Installation | manque : `bash scripts/setup.sh`, lu dans l'`AGENTS.md` de Maestro, sans retour en 300 s | tenu : `pip install -e ".[dev]"`, 98 s |
| Lint | non joué | tenu |
| Formatage | non joué | manque : aucune commande, alors que la personne n'a choisi aucun formateur |
| Types | non joué | tenu : `mypy maestro` |
| Tests | non joué | manque : `pytest` sur la suite entière, sans retour en 300 s |
| CI écrite | tenu | tenu |
| Convention de commit | manque : 3 sujets sur 19 en Conventional Commits | tenu, jugé sur le seul sujet |
| Outillage choisi suivi | manque : `ruff format .` est prescrit, alors qu'aucun formateur n'est choisi | tenu |
| **Lignes tenues** | **1/8** | **6/8** |
| C1 — au refus, rien n'annonce une carte | tenu | manqué : la correction « aucune carte ne suivra » s'ajoute au texte du modèle sans rien en retirer, et le test attend une annonce, puis sa correction |
| C2 — une proposition acceptée reste annoncée et posée | manqué | manqué : des deux côtés, la consigne interdit désormais au modèle de dire comment l'accepter ou la corriger |
| C3 — une seule ponctuation | tenu | tenu |
| C4 — des tests déterministes | tenu | tenu |
| **Critères tenus** | **3/4** | **2/4** |

Le juge des critères a coûté 0,37 $ par côté. Les lignes de la grille ne disent pas tout de l'écart
entre les livrables, car la grille a elle-même trois limites, reprises par **#1681** :
- **le plafond de 300 s par commande** arrête la mise en route du dépôt et sa suite entière ;
- **le formatage est compté manquant** alors que la personne l'a écarté ;
- **la convention de commit est jugée sur les sujets**, alors que le dépôt exige la référence au
  ticket dans le message.

### 3.2 Le rejeu ciblé

Chaque livrable a été cloné, puis installé dans un venv neuf par `pip install -e ".[dev]"`. On y a
ensuite joué :
- le lint et les types de la CI ;
- les suites que touchent les deux diffs : `test_naissance_projet.py`, `test_audit_commandes.py` et
  `test_documentation.py` ;
- le hook `commit-msg`, sur le message entier de chaque commit.

| Axe | Maestro | Chaîne |
| --- | --- | --- |
| Installation | ✓ 50 s | ✓ 51 s |
| Lint : `ruff check .` | ✓ | ✓ |
| Types : `mypy maestro` | ✓ 15 s | ✓ 16 s |
| Suites touchées | ✗ 230 passés, **1 échec** : `test_claude_md_tient_sous_son_budget`, avec 20 183 tokens pour un budget de 20 164 | ✓ 228 passés |
| Hook `commit-msg` | ✗ **0/18** | ✓ **1/1** |
| CI réelle | jamais jouée, puisque le clone ne pousse pas (protocole). Un échec de la suite suffit à la dire rouge | verte sur la PR #1595 |

La suite entière n'a pas été rejouée sur le livrable de Maestro, puisqu'un seul rouge suffit à
trancher la condition « Livré ». L'agent l'avait jouée en natif : 10 échecs sur 8 706, dont ce
budget.

**Ce que chaque livrable contient** :
- **Maestro**, 9 fichiers, +341 −4 :
  - le correctif, `orchestration.py` (+24 −4) : la consigne du verdict « projet » n'annonce plus de
    carte, et `_phrase_proposition_refusee` retire la ponctuation finale de la cause ;
  - ses tests, `test_naissance_projet.py` (+119) ;
  - l'outillage : `AGENTS.md` (+76), cinq skills sous `.agents/skills/` (+118), et le pont
    `@AGENTS.md` en fin de `CLAUDE.md` (+4).
- **La chaîne**, 3 fichiers, +149 −16 :
  - le correctif, `orchestration.py` : la consigne « projet » n'annonce plus de carte, une
    correction « Vérification faite, aucune carte ne suivra » suit un refus, et `_cause_en_phrase`
    ponctue la cause une seule fois ;
  - ses tests, dans la même suite (+101) ;
  - docs/05 § 2, mis à jour.

---

## 4. Les cinq leviers, chiffre contre chiffre

| Levier | #1522 (2026-10-15) | #1587 (2026-11-04) | Gain mesuré | Suite |
| --- | --- | --- | --- | --- |
| **#1542** — ce que l'agent reçoit d'un projet importé | `AGENTS.md` ne nomme pas `CLAUDE.md`, même après trois corrections. 4 corrections en mots. Fil : 20 min 05 s, 1,89 $ | `AGENTS.md` nomme `CLAUDE.md` et `apps/web/AGENTS.md`, à lire en entier. 0 correction. Fil : 8 min 34 s, 1,73 $ | −4 corrections, et 11 min 31 s de moins pour le fil. Mais les commandes de l'outillage contredisent `CLAUDE.md`, et le pont fait rougir son budget | **#1678** |
| **#1543** — la reprise après la limite d'usage | La dernière tâche est coupée par la limite de session, et le run se solde en échec à 2 h 28 | La limite n'a pas coupé le run (fenêtre de 5 h à 43 % à 10 h 25) : ni pause ni reprise | **Non exercé** : aucun gain mesuré sur ce passage | aucune (§ 4.2) |
| **#1531** — la granularité | 3 tâches : le correctif sans tests ; les tests à part (19 min 01 s, 1,64 $) ; « vérifier et commiter » (85 min 14 s, 1,86 $) | 1 tâche : le correctif et ses tests ensemble | −2 tâches et leurs juges. **Tenu** | — |
| **#1530** — la vérification une seule fois | `local.sh`, `local.sh --complet`, puis la suite entière en natif, et quatre juges : environ 60 min | Plus de tâche de recette. Mais trois vérifications (26 min), une recette (8 min 53 s, 0,30 $), et la suite entière tentée six fois | La tâche de recette disparaît (85 min, contre 9 min de recette). Aucun gain de durée sur le chemin : la vérification y passe d'environ 60 min à environ 2 h | **#1680**, et #1678 pour la suite entière |
| **#1507** — les commits | 0 commit de l'agent conforme sur 5, et 5 fusions « Maestro : … ». Une réécriture de branche demandée | 3 commits de l'agent sur 3 en Conventional Commits, mais sans `Refs #1592`. 15 commits du moteur « Maestro : … ». Aucune réécriture | −1 arbitrage de réécriture. Mais 0/18 au hook | **#1679** |

**Maestro face à lui-même**, de #1522 à #1587 :

| | #1522 | #1587 |
| --- | --- | --- |
| Du premier geste à la fin | 2 h 47 min 48 s | 2 h 37 min 09 s |
| Coût | 9,08 $ | 6,06 $ |
| Gestes | 18, dont 4 corrections et 1 réécriture | 13, aucune correction |
| Tâches | 3 | 1 |
| Issue | échec, rien de fusionné | terminé, livrable fusionné dans le clone |
| Commits au hook du dépôt | 0/5 | 0/18 |

### 4.1 #1542 — l'outillage lit enfin les instructions, mais il en reprend mal les commandes

L'`AGENTS.md` écrit à l'import renvoie à `CLAUDE.md`, et l'agent l'a lu. Il a vérifié la convention
de commit dans l'historique, et il a joué `mypy maestro` comme la CI. Les commandes de l'outillage,
en revanche, viennent des fichiers de configuration (`pyproject.toml`, `package.json`), et elles
contredisent les instructions :
- **`lancer-les-tests`** prescrit `pytest`, présenté comme « la vérification que tout agent joue
  avant de rendre son travail ». `CLAUDE.md` dit l'inverse : ne pas rejouer la suite complète. Le
  brief en a même fait un critère, et c'est d'ici que vient l'heure de suite entière ;
- **`verifier-le-style`** prescrit `ruff format .` et `mypy .`. Or le dépôt n'exige que
  `ruff check`, et sa CI joue `mypy maestro` ;
- **les commandes sont nues**, hors du venv que `CLAUDE.md` impose. La vérification de l'outillage
  les a toutes vues échouer, avec le code 127, dans une copie sans venv, et l'outillage les livre
  marquées « ⚠ Échouée ». Au premier essai, l'outillage écrivait au contraire les commandes Windows
  du poste ;
- **`mettre-en-route`** écrit deux fois `bash scripts/setup.sh`. C'est cette commande que la grille
  a lue pour l'installation (§ 3.1).

**Le pont `@AGENTS.md`**, ajouté à la fin de `CLAUDE.md` pour le client du poste (#1295), fait
passer le fichier au-dessus de son cliquet : 19 tokens de trop. La personne qui accepte la pièce ne
le sait pas. Une développeuse l'aurait refusée.

### 4.2 #1543 — non exercé

La limite d'usage n'a pas coupé le run. À sa clôture, #1543 a été prouvé sur un double du
fournisseur, puisque la limite ne se provoque pas sans brûler le quota du compte. Son gain n'est
donc mesuré sur le réel ni ici ni ailleurs.

Ce n'est pas un écart avec la chaîne, mais une pièce qui manque au critère C4 du jalon. Elle est
nommée ici pour le bilan, sans ticket.

### 4.3 #1531 — tenu

Le plan compte une tâche, `dev-python`, qui écrit le correctif et ses tests. La tâche de tests à
part et la tâche « vérifier et commiter » de #1522 ont disparu, et leurs juges avec elles.

### 4.4 #1530 — la tâche de recette disparaît, mais la vérification reste multiple

Il n'y a plus de tâche de recette au plan : la recette se joue en fin de run, en 8 min 53 s et pour
0,30 $. Mais les vérifications se sont multipliées, et deux défauts les ont allongées :
- **quand l'exécution d'un critère ne prouve rien, il est compté non tenu**.
  - « La suite existante passe » a été coupé vers 15 % à la vérification 1 et à la recette, puis
    est tombé sur une erreur de pytest-xdist à la vérification 2. Les trois fois, il a été compté
    non tenu.
  - À la vérification 2, une expression régulière cherchait une règle mot pour mot, et la source la
    coupait par un retour à la ligne. L'agent a recoupé son propre texte pour la satisfaire, ce qui
    a coûté 13 min 46 s de reprise, puis 6 min 48 s de vérification.
- **la recette contredit la vérification qui la précède**, sur le même arbre : 3/6, contre 6/6.
  Ses trois « non tenus » sont des exécutions manquées, et c'est pourtant ce chiffre que le message
  de fin donne à la personne.

### 4.5 #1507 — conforme pour l'agent, pas pour le moteur

L'agent commite lui-même, en Conventional Commits, et plus personne ne réécrit de branche. Mais
#1507 fait rédiger les commits du moteur dans la convention **choisie** à l'outillage d'un projet
neuf. À l'import, aucune convention n'est relevée, alors que `CLAUDE.md` la déclare. Les 15 commits
du moteur restent donc « Maestro : … ».

Les trois commits de l'agent ne citent pas #1592, alors que la demande le nommait, et `style` n'est
pas un type que le hook admet. Le clone n'a pas de hook, et rien ne l'a donc arrêté.

### 4.6 Le sixième mécanisme, #1515

L'isolation n'est pas livrée, et la portée a laissé passer deux arrêts de processus par PID, avec le
décideur « auto » :
- un `taskkill //PID … //T //F`, le PID choisi dans une liste de tous les `python` du poste ;
- un second, filtré sur la ligne de commande du worktree de l'agent.

Les deux fois, l'agent visait sa propre suite, et la stack a survécu : seul son choix a borné le
geste. `bash scripts/setup.sh` a été joué deux fois sans geste de la personne : par la vérification
de l'outillage, puis par la grille au rejeu. Ces pièces sont versées sur #1515.

---

## 5. Le seuil

| Condition, posée avant l'épreuve | Mesure | Verdict |
| --- | --- | --- |
| **Livré** : le livrable passe le rejeu sur clone propre et la CI du dépôt | La grille ne joue rien d'exécutable, l'installation étant coupée. Au rejeu ciblé, `test_claude_md_tient_sous_son_budget` échoue : la CI rougirait | **non tenu** |
| **Qualité** : au moins autant de critères que la chaîne, et tous les commits dans la convention | 3/4 critères contre 2/4 ; 0/18 commits au hook | **non tenu**, par les commits |
| **Gestes** : aucun geste ne corrige l'outillage ni ne réécrit une branche | Aucune correction, aucune réécriture, sur 13 gestes (§ 2) | **tenu** (*) |
| **Coût et durée** : un coût au plus 1,2 fois celui de la chaîne, une durée au plus 1,5 fois | 0,90 fois ; 8,4 fois | **non tenu**, par la durée |

(*) La règle ajoute : « seuls comptent confier le ticket, l'accord de lancement et les arbitrages que
la portée renvoie ». Lue strictement, elle laisse hors de la liste les neuf gestes de l'import, qui
ne se paient qu'une fois par projet, ainsi que la validation de l'équipe. Aucun de ces gestes ne
corrige ni ne réécrit quoi que ce soit, et ils ne changent pas le verdict.

**Verdict : le seuil n'est pas tenu.** Une condition sur quatre tient, celle des gestes. La routine
(B1, B2 et B3, docs/06) reste différée. #1515, son autre condition, n'est pas livré non plus.

**Ce qui l'en rapprocherait** :
- **#1678 et #1679** lèvent, sur ce terrain, les causes du « Livré » et de la « Qualité » : la CI et
  les commits.
- **La durée est plus loin.** Sans la suite entière, le passage de Maestro aurait duré environ
  1 h 15, soit encore environ 4 fois celui de la chaîne. Les vérifications et la recette en pèsent
  35 min (#1680), et le fil de l'import 8 min 34 s.

Sur un ticket aussi petit, la chaîne livre en 19 min, pipeline compris. Le seuil reste tel qu'il a
été posé : le déplacer après l'épreuve reviendrait à le fabriquer.

---

## 6. Les écarts qui restent

| Écart | Ticket |
| --- | --- |
| L'outillage d'un projet importé prend ses commandes dans les fichiers de configuration, contre ses instructions : la suite entière au lieu du périmètre, `ruff format .`, `mypy .`, des commandes hors du venv. Et son pont fait rougir le budget de `CLAUDE.md` | **#1678** (`prio::haute`) |
| Les commits du moteur ignorent la convention d'un projet importé, et ceux de l'agent ne citent pas le ticket | **#1679** (`prio::haute`) |
| La vérification et la recette comptent non tenu un critère qu'elles n'ont pas su exécuter, et la recette rejoue ce qui vient d'être tenu | **#1680** (`prio::moyenne`) |
| La grille arrête un vrai dépôt à 300 s par commande, compte manquant un formatage écarté, et juge la convention sur les seuls sujets | **#1681** (`prio::moyenne`) |

**Restent sans ticket**, pour les raisons suivantes :
- **#1543, non exercé** : ce n'est pas un écart avec la chaîne, mais une pièce qui manque à C4
  (§ 4.2).
- **L'équipe de cinq agents, dont un seul a servi** : elle est proposée à l'import, sur l'analyse du
  projet, et elle s'ajuste au plan ([docs/42](./42-decision-equipe-ajustee-au-plan.md)). Les agents
  inemployés n'ont rien coûté sur ce passage.
- **Le fil a dit au run que sa recherche n'avait pas trouvé le code**, alors que le ticket nommait le
  fichier. L'agent l'a trouvé en une recherche, en quelques secondes : rien de mesurable.
- **La sonde de `run.sh` prenait un CLI déconnecté pour un modèle refusé.** C'est corrigé par #1676,
  qui nomme désormais le geste de reconnexion.

---

## 7. Ce que cette mesure ne sait pas

- **Un passage de chaque côté, sur un petit ticket.** Les durées ne sont pas des médianes, et la
  chaîne n'a rien eu à découper.
- **La chaîne a joué en session interactive, pas par `run.sh`.** Son attente du pipeline, 6 min
  17 s, est comptée dans sa durée, alors qu'un run l'aurait laissée au pilote. Elle a aussi joué en
  mode de permission `auto`.
- **La chaîne n'a pas joué son banc**, faute de CLI. Son coût n'en porte donc pas la part, qui
  valait 1,74 $ à #1522.
- **La personne a tout accepté**, comme le protocole le demande. Une développeuse aurait refusé le
  pont dans `CLAUDE.md` et corrigé `lancer-les-tests`. Cela fait deux gestes de correction, et le
  livrable aurait pu passer la CI.
- **Le rejeu a eu lieu douze jours après les passages**, sur un fournisseur reconnecté. Les
  livrables, eux, n'ont pas bougé : `fcb0681` et `c48ca249`.
- **Le juge des critères est un modèle.** Son verdict sur C1, qui porte sur la correction de la
  chaîne, se discute. Il est rendu tel quel.

## 8. Où cette analyse est écrite ailleurs

- **#1587** : le choix du ticket, le protocole, la consigne du quota, les relevés des deux côtés et
  le rapport du rejeu.
- **#1678, #1679, #1680 et #1681** : les écarts.
- **#1515** : une note y verse les pièces de ce passage.
- **docs/06**, section « Maestro sur son propre dépôt » : le verdict du seuil.
- **[docs/51](./51-maestro-face-a-la-chaine-du-depot.md)** : le premier essai.
