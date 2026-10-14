<!-- documentation: développement -->
# 49 — Le règlement ne grossit plus

**Date :** 2026-10-14. **Instruite par :** `/idee` (#1013), sur un plan **validé par la personne
avant d'être écrit** (#1415). Elle a validé le plan tel que recommandé, le cliquet, le plafond de
15 % et les abandons proposés.
**Consignée par :** #1526, dont le corps porte l'analyse entière.

**Tickets** :
- **#1523** : le budget de `CLAUDE.md` devient un cliquet ;
- **#1524** : la part de l'outillage se dit et se plafonne (né assigné, après #1517) ;
- **#1522** : Maestro traite un vrai ticket de son propre dépôt, en face de `/ticket-start` (lot
  parallèle du go/no-go #1510) ;
- **#1508** : une note y ajoute la chaîne de développement du dépôt comme second étalon ;
- **#1466** passe en `prio::haute` ; **#1525** porte le rejugement du découpage de `app.py`, en
  `basse`.

---

## 0. Ce que ce document décide

1. **Le budget de `CLAUDE.md` devient un cliquet.** Il vaut le coût du jour et ne remonte plus. Une
   règle qui entre en fait sortir une, ou sort sa démonstration vers `docs/`.
2. **La part de l'outillage est plafonnée à 15 % des tickets fermés sur 30 jours.** Au-delà, un
   ticket d'outillage neuf demande un arbitrage écrit. Le plafond est **une décision, pas une
   mesure**, comme la borne de concurrence de 3 (#626).
3. **La chaîne qui développe ce dépôt devient un second étalon** du go/no-go « Maestro fait mieux
   qu'un agent seul » ([docs/48](./48-decision-produit-pro-exploitable.md)).
4. **Le backlog d'outillage est trié** : huit tickets sont abandonnés, et #1378 est absorbé par
   #1526.

## 1. D'où vient la demande

La personne a demandé si le mode de travail du dépôt (commandes, scripts, skills, règles) est adapté
à l'objectif de Maestro, et le plus efficace. La réponse, instruite sur pièces : **le socle tient**,
mais le règlement **grossit encore** après avoir été déclaré trop lourd.

| Mesure (2026-10-14) | Valeur |
| --- | --- |
| `CLAUDE.md` | 5 Ko le 2026-07-07, 210 Ko le 2026-08-30, 51 Ko le 2026-09-20 (#965), **59 Ko** aujourd'hui ; 182 tickets cités |
| `docs/10-workflow-git.md` | 620 Ko le 2026-09-21 ([docs/40 §1](./40-decision-rythme-et-scenarios-de-reference.md)), **664 Ko** aujourd'hui |
| Part des tickets d'outillage | 23 % des 158 tickets fermés en septembre (docs/40 §1), **24 %** (14 sur 59) depuis la levée du gel du 2026-09-29 |
| Outillage en lignes | ~40 000 lignes de scripts, dont 35 000 de bash ; ~58 000 lignes de tests d'outillage, contre ~110 000 applicatives |

docs/40 §2.2 avait nommé la cause : chaque incident produit une règle, un test de garde et un
paragraphe de doc. Le gel de septembre l'a suspendue, il ne l'a pas changée. À sa levée, la part est
revenue d'elle-même. Et le budget de 25 000 tokens posé par #965 laissait 9 000 tokens de marge à
`CLAUDE.md`, ramené ce jour-là à 15 830 tokens. Il en pèse 20 169 aujourd'hui, au compteur du dépôt
(`estimer_tokens`) : la marge se remplit.

## 2. Ce qui est renversé

### 2.1 Le budget de `CLAUDE.md` ne se relève plus

> **Livré par #1523** le 2026-10-14. Le budget vaut 20 164 tokens : #1523 a payé sa propre phrase
> en sortant de `CLAUDE.md` trois rappels de renversements que `docs/` portait déjà. La garde est
> décrite en [docs/25 §9.4](./25-audit-commandes-claude.md).

**Renverse** [docs/25 §9.4](./25-audit-commandes-claude.md), décision de #965 : « un `CLAUDE.md`
qui dépasse est une décision : on relève le budget en connaissant le coût, ou on sort la
démonstration vers `docs/` ». La seconde moitié reste. La première tombe.

**Pourquoi.**
- **Un budget relevable n'a pas tenu.** `CLAUDE.md` est passé de 15 830 à 20 169 tokens en moins
  d'un mois, sous un budget qui n'a jamais rougi.
- **Ce fichier est relu à chaque tour de chaque session**, et le contexte relu est le premier poste
  de coût d'un ticket (docs/06, section « Le coût d'un ticket »).
- **Un cliquet oblige à choisir** : une règle neuve en fait sortir une autre, ou sa démonstration.
  C'est la seule forme qui empêche le règlement de croître plus vite que ce qu'il encadre.

**Le mécanisme** (#1523) : `BUDGET_CLAUDE_MD_TOKENS` vaut le coût mesuré à la livraison, et une
garde fait rougir toute hausse de la constante. Elle la confronte à `BUDGETS_LIVRES_CLAUDE_MD`, le
registre des budgets livrés, qui ne fait que descendre.

## 3. Ce qui s'ajoute sans rien renverser

- **Le plafond de la part d'outillage** (#1524). Il succède au gel de
  [docs/40 §6](./40-decision-rythme-et-scenarios-de-reference.md), arrivé à son terme le
  2026-09-29 : un gel s'arrête, un plafond qui se dit au moment de créer un ticket tient.
  - **Où il se dit** : `/etat-des-lieux` montre la part des tickets d'outillage fermés sur 30 jours.
    `/ticket-create`, sur le rail outillage et au-delà de 15 %, demande un arbitrage écrit
    (`--arbitre "<raison>"`), sur le patron de #1517.
  - **Ce qui passe** : une panne qui bloque réellement le travail, en le disant.
  - **Ce qui ne passe pas** : le plafond ne se déplace que sur un arbitrage écrit, jamais au jugé.
- **Un second étalon pour le go/no-go.** La chaîne qui développe ce dépôt (`/ticket-start` →
  `/ticket-ship`, `/orchestrate`) fait déjà ce que Maestro promet : un plan durable dans la forge,
  une session capable par capacité, un worktree, un merge vérifié, une reprise. Elle a livré environ
  990 tickets en trois mois et demi.
  - **#1508** confronte la chaîne de Maestro à la sienne, point par point (note du 2026-10-14).
  - **#1522** fait traiter un vrai ticket du dépôt par Maestro et par `/ticket-start`, et compare
    les deux livrables sur la grille de #1512.
- **Le contexte d'une session de run** : #1466 passe en `haute`. Il est le moins cher à livrer, et le
  contexte de départ pèse 77 000 tokens, dont les plugins du poste.

## 4. Ce qui est écarté, sur mesure

- **Vérifier par lot plutôt qu'au ticket.** Pour le banc, c'est déjà fait
  ([docs/47](./47-decision-le-banc-se-paie-une-fois-par-lot.md), #1462 et #1464). Pour la relecture
  visuelle, la mesure contredit l'idée : sur les 22 tickets fermés depuis le 2026-09-26 avec une note
  de relecture, elle a rendu **environ 17 ✗, sur 11 tickets**, dont un défaut fonctionnel (#1294).
  La déplacer au lot les ferait trouver plus tard, et chacun coûterait un ticket de correction.
- **Le découpage de `app.py`**, différé le 2026-10-09 (docs/06, section « Le coût d'un ticket » :
  lu 81 fois, 1,6 % du poids), le reste. #1525 porte son rejugement, conditionné aux chiffres de
  #1465.
- **Réécrire le bash.** Ce serait encore du travail d'outillage. On ne l'étend plus, et on ne le
  réécrit pas.
- **Un troisième bras « chaîne de développement » dans le banc comparatif #1512.** Un passage coûte
  environ 60 $ sur p5, et 150 à 250 $ sur le projet long. #1522 donne la comparaison sur un vrai
  ticket pour le prix d'un run.

## 5. Ce qui ne bouge pas

- **La relecture visuelle à la clôture** ([docs/30 §5.5](./30-cible-visuelle-control-tower.md), #935).
- **Le merge vérifié**, en un seul endroit (`merge-mr`, #417).
- **Le banc par lot** ([docs/47](./47-decision-le-banc-se-paie-une-fois-par-lot.md)).
- **La règle d'écriture de `CLAUDE.md`** : la règle s'y écrit, jamais sa démonstration (#965).
- **Le budget des textes de clôture** (#1245), qui reste relevable. L'étendre au cliquet serait une
  autre décision.
- **La limite de quatre jalons produit en chantier et l'ancre** ([docs/48](./48-decision-produit-pro-exploitable.md)
  §3, #1517).

## 6. Ce qui rouvrirait la décision

- **§2.1** : une règle indispensable qui ne trouverait rien à faire sortir. Le cliquet ne se relève
  pas en silence : on renverse cette note, par écrit, en disant ce que la règle neuve vaut de plus
  que celles qu'elle aurait remplacées.
- **§3, le plafond** : une suite d'arbitrages écrits qui le dépassent pour de bonnes raisons. C'est
  le signe qu'il est mal placé, et il se rediscute avec la personne.
- **§4, la relecture** : si la relecture au ticket cesse de trouver, mesurée de la même façon sur
  les notes de relecture, sa place se rejuge.

## 7. Où cette décision est écrite ailleurs

- **[docs/06](./06-roadmap.md)** : la section « Le règlement ne grossit plus ».
- **Un renvoi ⚠ à l'endroit de chaque décision renversée ou prolongée** :
  - [docs/25 §9.4](./25-audit-commandes-claude.md), « Quand une garde rougit », devenu le texte
    présent avec #1523 ;
  - [docs/40 §6](./40-decision-rythme-et-scenarios-de-reference.md), le gel, levé le 2026-09-29.
- **Le présent** de `CLAUDE.md` et de docs/25 §9.4 a été réécrit par #1523. Celui de docs/10 §3.4 le
  sera par #1524, quand le code changera, pas ici.
