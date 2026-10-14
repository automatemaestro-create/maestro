<!-- documentation: développement -->
# 50 — Où Maestro perd face à une session seule, sur p5

**Date :** 2026-10-14. **Instruite par :** #1508, premier lot du go/no-go #1510
([docs/48](./48-decision-produit-pro-exploitable.md)). **Pièces :** les passages C2 et C3 du bilan de
« Le run livre un vrai projet » (2026-10-14, rapport non commité,
`docs/bilans/le-run-livre-un-vrai-projet-…md` § 3.1).

Ce document **répartit** le temps et le coût que Maestro dépense et qu'une session seule ne dépense
pas, famille par famille. Il **chiffre** chaque levier qui réduirait l'écart, puis nomme ceux qui
deviennent des lots de #1510. Il ne tranche pas le go/no-go : c'est le banc comparatif (#1512,
#1513) qui le mesure, sur plusieurs passages.

---

## 0. Ce qu'il établit

1. **L'écart est plus large que le bilan ne le disait.** C2 a coûté **15,97 $**, pas 12,40 $, soit
   **2,0 fois** C3 (8,02 $). Il a duré **64 min 24 s** hors arrêt, soit **2,5 fois** C3 (25 min 57 s).
   Le grand livre de la Control Tower ne voit pas 4,46 $ de ces 15,97 $ (§ 1.2).
2. **L'exécution n'est pas là où Maestro perd.** Les tâches qui construisent occupent 20 min du
   chemin critique, contre 26 min pour la session seule entière, et coûtent 7,64 $ contre 8,02 $.
   **Tout l'écart est autour d'elles** : le fil d'avant le run, la vérification à trois étages, les
   attentes d'arbitrage et la chute de 10:05.
3. **En tokens, l'écart a deux causes.** +5,76 $ de cache écrit : 57 sessions démarrent à froid là
   où C3 n'en ouvre qu'une. +2,92 $ de sortie : 294 680 tokens contre 143 103. Le cache relu, lui,
   coûte 0,73 $ de **moins** que dans C3.
4. **La chaîne du dépôt l'emporte sur deux points** : la granularité (un ticket par capacité, tests
   compris, à la taille d'une session) et la vérification (une fois, au ticket). Elle l'emporte
   aussi sur la reprise d'une tâche coupée (sa session rouverte, son contexte intact). Elle fait
   jeu égal sur le plan durable.
5. **Quatre leviers deviennent des lots** de #1510 (§ 4). Cumulés aux tickets déjà ouverts, ils
   ramènent l'estimation de p5 sous le plancher de 1,3 fois. C'est une estimation : la mesure
   revient au banc.

---

## 1. Les deux côtés, recomptés

### 1.1 Les pièces et la méthode

| Pièce | Ce qu'elle donne |
| --- | --- |
| Journal d'événements de la Control Tower (`maestro.evenements:journal`, espace `commun`, Redis du poste) | 890 événements du projet `prj-7bd38806` et du run `fea601a5c629` : statuts et usage de chaque tâche, vérifications, fusions, arbitrages, plan |
| Transcriptions Claude Code de C2 (`~/.claude/projects/*pid27360*`, `*pid11564*`, `*pid18676*`, `*pid35532*`) | 57 sessions : 27 du fil (API), 18 du premier hôte du run, 3 de l'API rallumée, 9 du second hôte |
| Transcription de C3 (`…p5-seule/391254cf-….jsonl`) | 1 session, 95 réponses du modèle (135 tours au compteur du CLI) |

**Le tarif.** Une transcription porte des tokens, pas de prix. Le tarif effectif par token est
ajusté par moindres carrés sur les 15 entrées tarifées du grand livre de C2. Il donne 0,205 $ par
million de tokens de cache relu, 8,121 $ par million de cache écrit et 19,247 $ par million de
sortie. Il reproduit chaque tâche du grand livre à ±1 %, et chaque petite entrée à ±3 %. Contrôle
indépendant : appliqué à C3, il donne **8,016 $** pour 8,02 $ facturés.

**Le temps.** Le temps sur le **chemin critique** est celui que la personne attend. Il est découpé
segment par segment sur les horodatages des événements, et la somme des segments vaut la durée hors
arrêt. Le temps **cumulé** additionne les sessions qui tournent de front. Le temps passé dans un
outil est l'écart entre l'appel de l'outil et son résultat, lu dans la transcription. Le reste de la
session est la génération du modèle.

### 1.2 Ce que le grand livre ne voit pas

Le bilan comptait « run 11,52 $ + orchestration du fil 0,88 $ ». Ces 0,88 $ ne sont pas le fil : ce
sont le brief, le plan, deux sondes et deux bilans **du run**, que `analytics.py` range dans
`orchestration` et qui sont **déjà dans les 11,52 $**. Ce que le grand livre ne voit pas :

| Dépense hors grand livre | Montant | Pourquoi elle échappe |
| --- | --- | --- |
| Le fil d'avant le run (27 sessions) | 1,330 $ | les réponses du fil ne publient aucun usage |
| Les trois tâches coupées à 10:05, et le juge de l'une d'elles | 2,908 $ | une tâche « interrompue » ne solde rien (`_solde_le_cout`, `state.py`). `page-liste` avait pourtant publié 1,45 $ « engagés » en direct, et ce montant ne compte pas |
| Le récit du fil après la reprise (#1506) | 0,218 $ | même cause que le fil |
| **Total** | **4,456 $** | 28 % de ce que C2 a coûté |

**11,517 $ + 4,456 $ = 15,973 $**, et la somme des 57 transcriptions donne 15,968 $.

### 1.3 Les totaux

| | C2 — Maestro | C3 — session seule | Rapport |
| --- | --- | --- | --- |
| Durée hors arrêt | **64 min 24 s** (12 min 36 s de fil, 51 min 48 s de run) | **25 min 57 s** | **2,48** |
| Coût | **15,97 $** | **8,02 $** | **1,99** |
| Hors chute de 10:05 (§ 2, dernière ligne) | 55 min 30 s · 12,84 $ | — | 2,14 · 1,60 |
| Sessions | 57 | 1 | |
| Réponses du modèle | 324 | 95 | |
| Cache relu | 12 626 122 tokens · 2,583 $ | 16 169 361 tokens · 3,308 $ | 0,78 |
| Cache écrit | 949 793 tokens · 7,713 $ | 240 500 tokens · 1,953 $ | 3,95 |
| Sortie | 294 680 tokens · 5,672 $ | 143 103 tokens · 2,754 $ | 2,06 |
| Gestes de la personne | 20 avant le run, 7 arbitrages, 1 reprise | 1 message | |

---

## 2. Famille par famille

Les heures sont celles du poste, comme dans le bilan.

| Famille | C2 — chemin critique | C2 — cumulé | C2 — coût | C3 | Ce qui la fait |
| --- | --- | --- | --- | --- | --- |
| **Fil avant le run** | 12 min 36 s (09:19:53 → 09:32:29) | même | 1,330 $ | 0 s · 0 $ : les choix tiennent dans le message | 27 sessions, une par réponse : déclaration du projet (4), **9 questions d'outillage une à une** (6 min 13 s, 0,538 $), **6 pièces écrites sur accord une à une** (2 min 17 s, 0,188 $), objectif, équipe (3 playbooks en parallèle), lancement. Le modèle tourne 5 min 10 s ; la lecture et les clics de la personne, 7 min 26 s, sur 20 gestes |
| **Cadrage et brief** | 23 s | même | 0,042 $ | aucun : C3 agit 9 s après le message | 8 critères d'acceptation, 2 questions. Aucun ne porte sur Prettier, la CI ou la convention de commit (§ 3, point 1) |
| **Découpage** | 1 min 40 s | 1 min 57 s | 0,325 $ | aucun | Plan de 9 tâches et 13 enchaînements (97 s, 0,290 $). Attribution de `jeu-recettes` à un agent (17 s, 0,035 $), qui retarde son départ de 20 s |
| **Exécution par tâche** | **20 min 00 s** : `socle-projet` 6 min 54 s, `charte-ui` 8 min 45 s, `page-detail` 4 min 21 s | 51 min 33 s (8 sessions) | **7,638 $** | **25 min 52 s · 8,016 $** : toute la session | Dans les 12 sessions de tâche (les 8 menées à bien, la recette et les 3 tentatives coupées) : génération 36 min 22 s, vérification par l'agent 12 min 35 s, navigateur 10 min 10 s, installation 9 min 21 s, serveur et réseau 3 min 07 s. Chez C3 : génération 21 min 03 s, installation 2 min 13 s, vérification 1 min 21 s, serveur 38 s, navigateur 19 s |
| **Vérification et juge** | **13 min 40 s** : 2 min 29 s après chaque tâche, et la tâche `recette-finale` (11 min 11 s) | 17 min 37 s | **2,965 $** : 9 juges 1,504 $, recette 1,461 $ ; et 1,221 $ de vérification par les agents eux-mêmes, dans la famille d'exécution | ≈ 1 min 40 s dans sa session (12 appels de vérification, 5 de navigateur) · 0,93 $ | Trois étages, neuf fois : l'agent vérifie (49 appels, 755 s), Maestro rejoue et le juge tranche (36,7 s par tâche), puis une tâche de recette rejoue tout. Un juge coûte 0,17 $, dont **82 % pour son préfixe** (37 000 caractères de tâche et de pièces) |
| **Contexte relu** | — | — | cache écrit **7,713 $** (dont 3,87 $ de préfixes), cache relu 2,583 $ | cache écrit 1,953 $ (préfixe 0,159 $), cache relu 3,308 $ | Chaque session neuve écrit son préfixe en cache. Une tâche démarre sur 21 000 à 22 000 tokens : prompt de Maestro, playbook, puis la tâche (19 500 caractères, dont **15 500 de récit des tâches amont**). Le contexte moyen d'un tour reste petit (39 000 tokens, 170 000 chez C3), d'où un cache relu moins cher |
| **Attentes d'arbitrage** | **6 min 25 s** : `rm public/*.svg` 240 s (délai écoulé), accord d'écriture 48 s, `$D` de la charte 38 s, recette 59 s | 7 min 04 s | 0 $ | aucune | 7 demandes, dont 2 que la portée ne sait pas lire (#1505) et une redemandée après la reprise (#1504) |
| **Fusions** | 46 s | 1 min 52 s | 0 $ | 8 commits en 20 s | 9 fusions, de 10 à 13 s chacune après la vérification. Les 33 commits « Maestro : … » ne suivent pas la convention choisie (#1507) |
| **Parallélisme effectif** | — | 5 572 tâche-secondes sur 3 108 s actives | — | 1 | **1,79** tâche de front, ou 1,46 hors travail coupé. Le plan en laissait partir 4, et un agent 3. Le plan a 4 niveaux (socle → charte → pages → recette) : la charte seule tient 10 min 08 s les quatre pages |
| *Chute et reprise* (hors familles) | 8 min 54 s (première vague de pages coupée, puis 61 s de reprise), plus 32 min d'arrêt | 17 min 21 s de tâches coupées | 3,126 $ | — | `taskkill //IM python.exe //F` (#1502, corrigé par #1509) |
| *Conduite du run* (hors familles) | 30 s, après la fin | même | 0,542 $ | récit de fin dans la session · 0,082 $ | 2 sondes du point de contrôle, 2 bilans sur pièces |
| **Total** | **64 min 24 s** | | **15,968 $** | **25 min 57 s · 8,016 $** | |

**Lecture.**
- **Le coût de l'exécution est comparable.** 7,64 $ contre 8,02 $, mais C3 a livré plus de
  fonctions : mode cuisine, minuteurs, liste de courses, thème.
- **Ce qui s'y ajoute vient du nombre de sessions et de vérifications.** La vérification coûte
  2,97 $. Le fil coûte 1,33 $. Le découpage, le brief et la conduite coûtent 0,91 $.
- **Le temps perdu est sur le chemin.** La vérification y prend 13 min 40 s, le fil 12 min 36 s, les
  arbitrages 6 min 25 s et la chute 8 min 54 s.

**Ce qu'une tâche coûte en plus de son travail**, en moyenne sur les 9 juges et les 12 sessions de
tâche :
- **en argent, environ 0,44 $** : juge 0,167 $, vérification par l'agent 0,102 $, préfixe 0,093 $,
  récit de fin 0,045 $, installation 0,036 $ ;
- **en temps cumulé, environ 2 min 40 s** : vérification par l'agent 63 s, installation 47 s,
  vérification de Maestro 37 s, fusion 12 s, départ 3 s ;
- **sur le chemin, environ 1 min 15 s par niveau du plan** (départ, installation, vérification,
  fusion), avant le travail lui-même.

---

## 3. La chaîne de Maestro face à celle du dépôt

La chaîne qui développe ce dépôt (`/ticket-start` → `/ticket-ship`, `/orchestrate`) fait déjà ce que
Maestro promet (note de #1526 sur #1508). Point par point, sur les mêmes pièces :

| Point | Chaîne du dépôt | Maestro (C2) | Qui l'emporte | Levier |
| --- | --- | --- | --- | --- |
| **1. Ce que l'agent reçoit** | le dépôt entier, `CLAUDE.md` (conventions, formatage, CI) et le ticket avec ses critères | 21 000 à 22 000 tokens : prompt, playbook, la tâche et le récit des tâches amont (80 % du message). `AGENTS.md` est indexé, et il dit la CI « prévue », à ne créer « que si ta tâche le demande » | **le dépôt** : aucune des 9 tâches ne portait la CI, le formatage ni la convention de commit. Le brief les a perdus | #1507 (existe) ; le récit amont, ≈ 0,5 $ (§ 4.2) |
| **2. Granularité** | une capacité visible par ticket, tests compris, à la taille d'une session ; aucun lot de tests seuls (#1150). p5, qui tient en une session, serait un ticket | 9 tâches sur 4 niveaux, dont une de tests seuls (`tests-logique`) et une de recette seule (`recette-finale`) : la forme que #1150 a retirée du dépôt | **le dépôt** : environ 0,44 $ et 2 min 40 s cumulées par tâche, environ 1 min 15 s par niveau | lots A1 et A2 (§ 4.1) |
| **3. Vérification** | une fois au ticket : tests avant de pousser, CI sur la PR, merge vérifié, critères confrontés au diff | trois étages par tâche : l'agent (755 s), Maestro et son juge (10 juges, 1,69 $), puis une tâche de recette (11 min 11 s sur le chemin, 1,46 $) | **le dépôt** sur le coût et le temps. **Maestro** sur la preuve : `docs/recette.md`, 8 critères tenus, chacun avec sa mesure | lots A1 et A2 |
| **4. Plan durable** | les tickets de la forge | le plan du run : il a tenu la chute, 5 tâches gardées et 3 reprises de leur branche (#1392) | **jeu égal** sur ces pièces. La reprise a pourtant perdu un accord (#1504) et écrit un récit faux (#1506) | #1504, #1506 (existent) |
| **5. Reprise** | la même session rouverte par son uuid, contexte intact, et le ticket repris sur son plan | le run repris sur son plan, mais chaque tâche coupée repart dans une **session neuve** et rebâtit son contexte : 3 sessions à 1,80 $ et 3 juges, plus 61 s de reprise | **le dépôt** | nommé pour #1513 (§ 4.2) |

Il y a aussi les **commits**. Dans le dépôt, l'agent les écrit dans la convention, sous la garde du
hook `commit-msg`. Dans Maestro, c'est Maestro qui écrit le message à la fusion (« Maestro : … ») :
l'agent ne peut pas suivre la convention choisie. C'est #1507.

---

## 4. Les leviers

### 4.1 Ceux qui deviennent des lots de #1510

| Lot | Levier | Pièce | Gain sur le chemin | Gain cumulé | Gain en coût | Gestes |
| --- | --- | --- | --- | --- | --- | --- |
| **A1** — #1530 | **La fin du run vérifie les critères du brief en les exécutant**, et en rend la recette, sans tâche de recette | `recette-finale` : 12 min 50 s sur le chemin, 1,633 $, 34 réponses, 2 arbitrages. La vérification d'une tâche, elle, prend 29 à 53 s et coûte 0,12 à 0,24 $ | **≈ −11 min** | ≈ −11 min | **≈ −1,25 $** | −2 arbitrages |
| **A2** — #1531 | **Le plan découpe en capacités visibles à la taille d'une session, tests compris** : ni tâche de tests seuls, ni tâche de recette seule | 9 tâches sur 4 niveaux ; le surcoût fixe mesuré au § 2 | ≈ −1 min 15 s par niveau retiré | ≈ −2 min 40 s par tâche retirée | **≈ −0,44 $ par tâche retirée**. Une tâche plus grande relit plus de contexte à chaque tour (C3 : 170 000 tokens par tour, contre 39 000) : une part du gain y repart | — |
| **B** — #1532 | **Une équipe qui construit une interface web reçoit le navigateur de Maestro** (registre, #1313), au lieu d'écrire son propre banc Chrome par CDP ou puppeteer | chez C2, 26 appels, 10 min 10 s, 0,83 $. Ôtée la recherche d'images de `jeu-recettes` (2 min 11 s, 0,13 $), il reste 8 min de bancs écrits à la main pour 0,70 $, dont celui de la recette (3 min 56 s, après 73 s pour y installer puppeteer). Chez C3, qui avait un navigateur : 5 appels, 19 s, 0,29 $ | ≈ −5 min tant que la recette reste une tâche ; ≈ 0 une fois A1 et #1505 livrés | **≈ −7 min 40 s** | **≈ −0,4 $** | −2 arbitrages, nés de fichiers du banc écrits hors du projet (charte 38 s, recette 59 s) |
| **C** — #1533 | **Le fil propose l'outillage d'un bloc, cohérent pour la pile, que la personne corrige en mots**, au lieu de 9 questions une à une | 9 sessions, 6 min 13 s, 0,538 $, 9 gestes | **≈ −5 min 30 s** | même | ≈ −0,46 $ | **−8** (20 → 12) |

Les quatre lots sont rangés **après #1513**, comme le veut docs/48 (« puis les leviers »). #1530, #1532
et #1533 sont parallèles. #1531 vient en dernier, sans le marqueur, parce qu'il dépend de #1530 :
retirer la tâche de recette sans recette au niveau du run perdrait la preuve.

Le **grand livre** reçoit aussi un ticket, #1534. Ce n'est pas un levier : sans lui, le banc
comparatif (#1512) lirait un Maestro 28 % moins cher qu'il ne l'est (§ 1.2).

### 4.2 Ceux qui existent déjà, ou qui ne justifient pas un lot sur ces pièces

| Levier | Gain sur ces pièces | Suite |
| --- | --- | --- |
| La portée lit le motif `rm public/*.svg` et la variable `$D` posée dans la commande | −4 min 38 s sur le chemin, −2 gestes | **#1505** (existe) |
| L'accord d'écriture survit à la reprise | −23 s hors du chemin, −1 geste | **#1504** (existe) |
| Un agent ne tue plus les processus de Maestro ; l'hôte mort se reprend sans attendre 30 min | −8 min 54 s sur le chemin, −32 min d'arrêt, −2,91 $ | **#1509** (livré), **#1503** (existe) |
| Le récit du fil n'est écrit qu'à la vraie fin | −0,22 $ | **#1506** (existe) |
| Le livrable suit l'outillage choisi | qualité : formatage, CI, convention, typecheck sur clone neuf | **#1507** (existe) |
| Un juge plus léger : son préfixe pèse 82 % de son coût | ≤ −0,7 $ sur 10 juges | à rejuger après A2, qui retire des juges |
| Les dépendances installées une fois par run, pas par copie de tâche | ≈ −1 min 15 s sur le chemin, ≈ −7 min cumulées, ≈ 0 $ | à rejuger après A2, qui retire des copies |
| Les tâches aval reçoivent le compte rendu des tâches amont, sans leur récit de travail | ≈ −0,5 $ (15 500 caractères par tâche, relus à chaque tour) | ne justifie pas un lot seul |
| Une tâche coupée se reprend en rouvrant sa session, comme la chaîne du dépôt | au plus les 3 sessions refaites (1,80 $, 4 min 21 s sur le chemin), et seulement après une coupure | **nommé pour #1513**, dont le terrain est le projet repris le lendemain |
| Le plafond de 3 instances par agent | `page-detail` partie 1 min 43 s en retard, `page-favoris` 4 min 31 s, tous deux hors du chemin final | ne justifie pas un lot |

### 4.3 Ce que les leviers donneraient, cumulés, sur p5

L'estimation part des mesures de C2 et retire le gain de chaque levier, sans compter deux fois ce que
deux leviers retirent ensemble. Le navigateur, par exemple, ne gagne presque plus rien sur le chemin
une fois la recette et l'arbitrage de la charte retirés. La recette du run est comptée pour 1 min
45 s et 0,35 $, l'ordre d'une vérification de tâche mesurée.

| | Chemin | Coût |
| --- | --- | --- |
| C2 mesuré | 64 min 24 s | 15,97 $ |
| − chute (#1509, #1503) et récit (#1506) | 55 min 30 s | 12,84 $ |
| − A1, la recette au niveau du run | ≈ 44 min 25 s | ≈ 11,6 $ |
| − C, l'outillage d'un bloc | ≈ 38 min 50 s | ≈ 11,1 $ |
| − #1505, la portée | ≈ 34 min 15 s | ≈ 11,1 $ |
| − B, le navigateur | ≈ 34 min | ≈ 10,7 $ |
| − A2, trois tâches de moins sur les huit qui restent (les tests avec le code qu'ils testent, par exemple), et un niveau de moins | ≈ 32 min 45 s | ≈ 9,4 $ |
| **Rapport à C3** | **≈ 1,26** | **≈ 1,17** |

Le critère de p5 dans le jalon est un écart d'au plus 1,3 fois. Ces chiffres sont des **estimations**
qui additionnent des gains mesurés un à un. Seul le banc (#1512) les rend vrais ou faux, sur
plusieurs passages. La qualité, elle, attend #1507.

---

## 5. Ce que cette analyse ne sait pas

- **Un passage de chaque côté.** La durée et le coût d'un run varient d'un passage à l'autre, et la
  chute de C2 n'est pas reproduite. Les gains ne sont pas des médianes.
- **Le tarif est ajusté**, et non lu chez le fournisseur. Il reproduit le grand livre à ±1 % et C3 à
  0,05 % ; une session très petite peut s'en écarter de quelques pour cent.
- **Le coût d'un tour est rangé dans la classe de son premier outil.** Un tour qui lance deux outils
  différents est donc rangé dans une seule classe. Le partage entre « vérification », « navigateur »
  et « autre » est une approximation, pas une mesure exacte.
- **Hors chute, le chemin est un contrefactuel.** Sans la coupure, la première vague de pages aurait
  fini plus tôt que le temps que la reprise y a passé. Les 55 min 30 s sont une borne haute.
- **Le temps que la personne de C3 a mis à écrire ses choix n'est pas mesuré.** Le fil de C2 compte
  7 min 26 s de lecture et de clics, et ce qu'il demande, C3 l'a reçu tout écrit.
- **La qualité n'est pas répartie ici.** Elle est dans le bilan (§ 3.1) et dans #1507.

## 6. Où cette analyse est écrite ailleurs

- **#1508** : une note résume les chiffres et nomme les lots.
- **[docs/06](./06-roadmap.md)** : la section « Maestro fait mieux qu'un agent seul » renvoie ici.
- **#1512** : une note dit que le coût de Maestro se compte au-delà de son grand livre, tant que
  #1534 n'est pas livré.
