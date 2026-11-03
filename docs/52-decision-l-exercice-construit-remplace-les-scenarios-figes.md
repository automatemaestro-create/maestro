<!-- documentation: développement -->
# 52 — L'exercice construit pour ce qu'on vient d'implémenter remplace les scénarios figés

**Date :** 2026-11-03. **Instruite par :** `/idee` (#1013), sur un plan **présenté avant toute
écriture** (#1415) et validé par la personne tel que recommandé, avec ses trois réponses.
**Consignée par :** #1574, dont le corps porte l'analyse entière.
**Jalon :** *Outillage de la forge* (rail outillage, échéance 2027-09-15).
**Tickets :** #1568, parent de suivi, et ses lots #1569 → #1570 ∥ #1571 → #1572. #1311 et #1422 mis
à jour ; #1467 abandonné.

**Renverse :**
- [docs/40](./40-decision-rythme-et-scenarios-de-reference.md) §0.2 et §5 : des scénarios de
  référence, joués avec le vrai modèle, conditionnent le bouclage d'un jalon produit (#1148, #1152) ;
- [docs/47](./47-decision-le-banc-se-paie-une-fois-par-lot.md) en entier : le banc entier une fois
  par lot sur `main` (#1462), et le banc dû au ticket dérivé de la carte (#1463, #1464) ;
- [docs/49](./49-decision-le-reglement-ne-grossit-plus.md) §5, qui comptait « le banc par lot »
  parmi ce qui ne bouge pas.

---

## 0. Ce que ce document décide

1. **Plus de scénarios figés.** S1 à S12 cessent d'être le mécanisme de vérification sur le réel, au
   ticket comme au bilan d'un jalon. Ils se retirent avec ce qui servait à les choisir (#1572).
2. **Quand une vérification sur le réel est recommandée, la session construit l'exercice.** Il
   comprend le prompt à donner à Maestro, ce qu'il faut semer autour, et ce qui le rendra vert. Il
   est taillé pour exercer **ce qui vient d'être implémenté**, et il est tiré de critères écrits avant
   lui : ceux du ticket à la clôture (#1570), ceux du jalon au bilan (#1571).
3. **L'oracle s'écrit avant de jouer.** Le relevé de l'exercice porte le prompt et ce qui le rendra
   vert **avant** le run, puis les faits observés. Le verdict se rend contre cet oracle.
4. **Le banc rejoué sur `main` à la fin de chaque run se retire, sans remplaçant.** C'est la réponse
   de la personne. Le pilote est un script, jamais une session : il ne sait rien construire.
5. **L'ordre des lots est la décision**, sur le patron de docs/47 §0.4 : l'outil d'exercice d'abord
   (#1569), puis l'exercice au ticket et au bilan (#1570 ∥ #1571), puis le retrait (#1572).

## 1. D'où vient la demande

> *« je trouve que les scénarios de référence ralentissent beaucoup le développement. je voudrais
> que, s'il est recommandé de tester Maestro sur un scénario, tu construises le scénario ou le prompt
> à donner à Maestro pour tester sa capacité et surtout tester l'implémentation faite. je ne veux pas
> de scénarios figés. »*

Le plan présenté nommait chaque renversement. La personne a répondu à trois questions :
- le banc par lot est **retiré sans remplaçant** ;
- le banc comparatif **garde** ses objectifs `p5` et `club` ;
- #1467 est **abandonné**.

## 2. Ce que coûte le banc figé

| Où | Ce qui se joue | Prix mesuré |
|---|---|---|
| Au ticket | les scénarios dont la carte croise le diff, quand le changement touche ce que le modèle reçoit ou fait (#1464) | S2 ≈ 0,4 $. S12 coûte ≈ 26 min et 3 $, et a été joué six fois en un jour le 2026-10-08 (docs/47 §2) |
| Par lot | le banc **entier** sur `main`, après le drain final d'un run (#1462) | ≈ 1 h 25 et 13,5 $ par run |
| Au bilan d'un jalon produit | le banc **entier**, un rouge interdit tout GO (#1152) | 89 à 94 min et 12 à 14 $ par passage (bouclages du 2026-10-07 et du 2026-10-14) |

Le code suit le même chemin : `maestro/scenarios/` fait 13 300 lignes, dont `scenarios.py` (3 724
lignes, les douze scénarios). La carte, sa sonde, le croisement, l'historique et le banc sur `main`
existent pour choisir un scénario figé ou le rejouer. Leurs suites font environ 11 000 lignes.

## 3. Pourquoi l'exercice construit

- **Un scénario rejoue un parcours générique, pas ce qu'on vient d'écrire.** S12 construit une
  application web entière pour vérifier, entre autres, ce qu'un ticket a touché en trois fonctions.
  La session qui a implémenté sait où frapper. Un exercice taillé au changement (un projet semé
  minimal, un message) coûte une fraction du scénario qu'il remplace.
- **On construisait déjà des exercices, à la main.** Quand aucun scénario ne couvrait le cas (#1298,
  #1381, #1390, #1183), des sessions ont écrit des scripts jetables qui détournaient les aides
  privées de `scenarios.py`. Elles relisaient pour cela 3 724 lignes, se heurtaient aux mêmes pièges
  (encodage, lanceurs en fond, `PYTHONPATH` du worktree), et `criteres-note` refusait de citer ces
  exercices comme passage. Le besoin était là. Il lui manquait un outil et une place dans le flux.
- **Une liste de parcours est un catalogue fermé.** [docs/41](./41-decision-maestro-juge-il-ne-bride-pas.md)
  l'écarte comme mécanisme principal du produit, et la même raison vaut pour sa vérification.
  Douze scénarios disent ce qu'on avait pensé à vérifier en septembre, pas ce que le ticket du jour
  change.
- **La raison de docs/40 tient, et l'exercice la sert mieux.** Les défauts #1197, #1198, #1205 et
  #1212 se sont trouvés **en se servant** du produit, et la relecture de code n'y voyait rien
  (#969). L'exercice se sert du produit, par sa porte d'entrée réelle, au moment où le défaut vient
  d'être écrit.

## 4. Le mécanisme

Chaque lot livre une pièce, avec ses tests et sa doc. Ce document en dit la cible ; c'est le lot qui
réécrit le présent.

1. **#1569 — l'outil d'exercice.** Ce sont des verbes publics, une commande chacun
   (`python -m maestro.scenarios.exercice`) :
   - semer un projet, ou le faire naître dans le fil ;
   - dire un message au fil ;
   - répondre en mots, d'après les choix de la personne que la session a écrits ;
   - arbitrer les demandes de validation selon une consigne ;
   - suivre un run jusqu'à son issue ou un moment nommé ;
   - relever les faits : cartes, journal, validations, coût du grand livre, fichiers du projet ;
   - sauver l'état que `start.sh --etat-banc` rouvre.

   Chaque exercice laisse un relevé sous `.maestro/exercices/<id>/`. La mécanique est **tirée** des
   aides du banc, pas réécrite. Le comparatif et la grille l'importent de là.
2. **#1570 — au ticket.** L'exercice est dû quand le changement touche ce que le modèle reçoit ou
   fait, ou une mécanique que seule la vraie stack montre. C'est un jugement, et il se consigne.
   L'exercice se tire des critères du ticket. `/ticket-finish` (4ter) et le prompt de run disent la
   règle, et `criteres-note` prend une ligne **Exercice** à la place de la ligne **Banc**.
3. **#1571 — au bilan d'un jalon produit.** Les exercices se tirent des critères de sortie du jalon,
   écrits par la personne au cadrage. Le bilan n'est pas l'auteur des tickets, donc son regard n'est
   pas celui de qui a codé.
4. **#1572 — le retrait.** Partent :
   - S1 à S12 et leur exécutant ;
   - la carte et sa sonde, le croisement, l'historique ;
   - le banc sur `main` (`sur_main.py`, `banc-main.sh`, sa section de `run.sh` et de `status.sh`) ;
   - `start.sh --rejouer` et `GL_BANC_*` ;
   - leurs suites.

   `start.sh --etat-banc` rouvre l'état du dernier exercice sauvé, et `CLAUDE.md` maigrit.

## 5. Ce qui ne bouge pas

- **Le produit se vérifie sur le réel**, avec la vraie stack et le vrai modèle, par le fil
  (`CLAUDE.md`, docs/41). Seuls changent **qui écrit l'épreuve**, et sur quoi elle porte.
- **La preuve exercée** (#1240, C1) : un ✓ nomme un test qui passe, ou une observation sur la vraie
  stack. Un exercice joué en est une, citée par son relevé et son run.
- **Un exercice injouable n'est jamais compté vert.** Il est nommé, avec sa raison.
- **Un rouge ne se rejoue pas jusqu'au vert.**
- **Au bilan d'un jalon produit, une épreuve rouge interdit le GO** (#1152). L'exercice construit
  prend la place du scénario dans cette règle, et un exercice injouable y vaut abstention.
- **Le banc comparatif** (#1512, #1513), avec ses objectifs `p5` et `club`. C'est l'instrument du
  go/no-go « Maestro fait mieux qu'un agent seul » ([docs/48](./48-decision-produit-pro-exploitable.md)).
  Il se joue à la demande, jamais dans le flux d'un ticket, et comparer d'une mesure à l'autre
  demande le même objectif. Il accepte déjà un objectif écrit pour l'occasion (#1522).
- **Le merge vérifié** (`merge-mr`, #417), le filet CI et la relecture visuelle.

## 6. Ce qu'on accepte de payer autrement

- **Une régression entre deux tickets se voit plus tard.** Sans banc par lot, une mécanique cassée par
  un merge n'est vue que par l'exercice d'un ticket suivant qui la traverse, ou au bilan du jalon.
  C'était le reproche de docs/40 §2 à l'ancien flux. Ce qui le borne : chaque mécanique garde ses
  tests, et le bilan rejoue sur le réel les critères du jalon.
- **L'exercice écrit par qui a codé peut être taillé pour passer.** Trois choses le bornent :
  - il se tire de critères écrits avant lui ;
  - son oracle est consigné avant de jouer ;
  - il passe par la porte d'entrée réelle, comme une personne.

  Un regard neuf pour l'écrire a été écarté : la personne veut une épreuve qui vise
  l'implémentation, donc écrite par qui la connaît.
- **Il n'y a plus de série longue par scénario** (taux de réussite, dernier vert sur `main`). Elle
  n'avait de sens que sur des épreuves figées.

## 7. Le présent n'est pas réécrit avant les lots

Jusqu'à #1570 et #1571, `/ticket-finish` (4ter), le prompt de run, `/milestone-bilan` (5c),
docs/10 §6 et §3.4 gardent la règle des scénarios. Jusqu'à #1572, le banc par lot tourne encore en
fin de run. Retirer un filet avant d'avoir posé celui qui le remplace ôterait la vérification sur le
réel sans rien mettre à la place. Un renvoi ⚠ est posé à l'endroit de chaque décision renversée et
de chaque texte au présent qu'elle rend faux. Chaque lot réécrit, dans sa PR, le texte qu'il change.

## 8. Ce qui rouvrirait la décision

- **Une régression trouvée au bilan aurait été vue plus tôt par un passage complet**, et ce cas
  revient. C'est le prix du §6. Il se mesure sur les réserves des bilans, pas sur une impression.
- **Les exercices construits se ressemblent tous**, d'un ticket à l'autre. Ce serait le signe d'un
  parcours de référence qui se cherche. Il se discute alors avec la personne, et ne se réinstalle
  pas en silence.

## 9. Où cette décision est écrite ailleurs

- **[docs/06](./06-roadmap.md)** : la section « Plus de scénarios figés ».
- **Un renvoi ⚠ à l'endroit de chaque décision renversée** : docs/40 §0 et §5, docs/47 §0,
  docs/49 §5, et dans docs/06 les sections « Les scénarios de référence » et « Le coût d'un ticket ».
- **Un renvoi ⚠ dans chaque texte au présent que la décision rend faux**, vers le lot qui le
  réécrira : docs/10 §6 et §11.13, et les documents qui citent `python -m maestro.scenarios` ou
  `start.sh --rejouer`.
