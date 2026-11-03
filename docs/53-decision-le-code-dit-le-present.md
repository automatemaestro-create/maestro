<!-- documentation: développement -->
# 53 — Le code dit le présent

**Date :** 2026-11-03. **Instruite par :** `/idee` (#1013), sur un plan **présenté avant toute
écriture** (#1415) et accepté par la personne tel que recommandé.
**Consignée par :** #1578, dont le corps porte l'analyse entière, mesures comprises.
**Jalon :** *Outillage de la forge* (rail outillage, échéance 2027-09-15).
**Tickets :** #1577, le cliquet de complexité ; #1525, le découpage de `app.py`, relancé ; #1468 et
#1469, mis à jour.

**Renverse :**
- [docs/49](./49-decision-le-reglement-ne-grossit-plus.md) §4, qui laissait **différé** le découpage
  de `app.py` ;
- [docs/06](./06-roadmap.md), section « Le coût d'un ticket », qui l'avait différé sur son poids en
  tokens, et section « Le règlement ne grossit plus », qui conditionnait #1525 aux chiffres de #1465.

---

## 0. Ce que ce document décide

1. **La qualité du code est un critère de découpage à part entière.** Le poids d'un module dans le
   contexte des sessions dit ce qu'il coûte à **lire**. Il ne dit pas ce qu'il coûte à **changer**.
   Un module se découpe aussi quand sa structure se relit, se teste et se merge mal. #1525 se relance
   donc sans attendre #1465.
2. **Le code dit le présent.** Une docstring ou un commentaire dit ce qui est vrai et la contrainte
   qui l'impose. Il ne raconte pas comment on y est arrivé. L'historique vit dans git, dans les
   tickets et dans `docs/`.
3. **Un pointeur reste permis, le récit ne l'est pas.** Une référence au ticket ou au document qui
   porte la raison (« (#1101) ») se garde. Ce que le code faisait avant, ce qu'un lot a renversé, ce
   qui est « comme avant ce lot » sortent.
4. **La règle s'applique au code qu'on touche**, jamais en réécriture d'ensemble. Un découpage la
   fait sur le code qu'il déplace, dans un **commit distinct** du déplacement.
5. **Aucune garde ne la juge.** Distinguer l'histoire du présent dans un texte est un jugement, et ce
   dépôt ne juge pas un texte par un lexique (#746).
6. **La complexité ne monte plus** (#1577). La règle C901 de ruff entre au seuil de l'outil. Chaque
   fonction qui le dépasse porte une dérogation nommée, et les dérogations ne peuvent que décroître.

## 1. D'où vient la demande

> *« ne trouves-tu pas que dans maestro, on a bcp de lignes dans un même fichier? est-ce que la
> qualité du code est au rendez-vous? il faudrait que tu me recommandes ce qui est préférable de
> faire »*

La recommandation présentée nommait le renversement du différé de #1525, signalé ⚠ comme tel. La
personne l'a acceptée sans réserve : « Oui, par /idee ».

## 2. Ce que dit la mesure

Relevé du 2026-11-03 sur `maestro/` (253 fichiers Python) :

| | Lignes | Part |
|---|---|---|
| Total | 141 590 | |
| Code | 63 238 | 44 % |
| Prose (docstrings et commentaires) | 59 804 | 42 % |

- **7 334 références à des tickets.** Elles ne sont pas un défaut en soi. Le défaut, c'est le récit
  qu'elles accompagnent. La route `agents` de `controltower/app.py` a 27 lignes de docstring pour une
  ligne de code. `run_agent` (`providers/claude.py`) fait 408 lignes, dont 96 de code.
- **`create_app` est une seule fonction de 5 339 lignes**, dont 2 085 de code. Elle pose les 102
  routes de l'API en closures, pour une complexité C901 de **384**. Aucun `APIRouter` n'existe dans
  le dépôt.
- **75 fonctions dépassent le seuil de C901** : 67 dans `maestro/`, 5 dans `scripts/`, 3 dans
  `tests/`. `create_app` porte à elle seule 27 % de la complexité cumulée de celles de `maestro/`.
- **Les garde-fous du dépôt sont solides** : mypy strict, ruff, couverture ≥ 90 %, `tsc`, eslint avec
  jsx-a11y, axe. Ils ne mesurent ni la complexité, ni ce que dit la prose.

## 3. Pourquoi la qualité, et pas seulement le poids

**Le différé du 2026-10-09 était juste sur son critère.** `app.py` pesait 1,6 % du contexte, parce
qu'il se lit par plages. Mais une lecture par plages masque justement ce que coûte la structure. Ce
coût est d'un autre ordre :
- **le conflit** : 5 des 29 commits de `main` depuis le 2026-10-14 touchent `app.py`. Tout lot qui
  ajoute une route modifie la même fonction ;
- **la relecture** : un domaine de l'API ne se lit pas sans les 101 routes des autres ;
- **la frontière** : une closure voit tout ce que `create_app` a défini avant elle, donc rien ne dit
  ce dont une route dépend vraiment.

**Le récit dans le code est un second support de l'historique.** C'est le défaut que le dépôt
refuse ailleurs : deux supports d'un même fait finissent par se contredire (règle du cycle de vie,
#365). L'historique a déjà son support, `git log` et `git blame`, et les tickets ou `docs/` portent
la décision. Écrit une seconde fois dans la docstring :
- **il se paie à chaque lecture**, par chaque session, comme le règlement que #965 et #1523 ont
  allégé. C'est le même principe, appliqué au code : la règle reste là où on la lit, la démonstration
  vit ailleurs ;
- **il oblige le lecteur à reconstruire le présent** à partir de couches successives : « auparavant
  », « depuis #… », « même phrase qu'avant ». Le lecteur qui veut savoir ce que fait le code doit
  traverser ce qu'il a fait ;
- **il vieillit sans se voir** : une phrase sur le passé reste vraie, mais elle cesse de dire quoi
  que ce soit à celui qui change le code.

## 4. La règle, en pratique

La docstring garde **le contrat** (ce que la fonction rend, ce qu'elle refuse) et **la contrainte**
qui impose un choix non évident. Exemple, la route `agents` réécrite au présent :

```python
"""L'état des agents : libre/occupé, tâche courante, compteurs, coût cumulé.

`projet` cadre le parc sur l'équipe de ce projet, lue sur le disque (catalogue et capacités) :
la projection repart vide à chaque redémarrage de l'API, et n'apporte que l'activité (#1101).
Les compteurs restent ceux de la projection, sans recalcul par projet : ce qu'un agent a fait
ici se lit dans `/api/taches?projet=`. Un projet sans équipe validée rend une liste vide.
Omis, la vue rend le parc entier.
"""
```

La raison du disque est gardée, puisqu'elle empêche de revenir à la projection. Le récit du bug qui
l'a imposée est parti, et `#1101` y mène.

## 5. Le cliquet de complexité

- **Le seuil est celui de ruff (10).** C'est le standard de l'outil, pas un défaut inventé par Maestro
  (docs/41). Le cliquet garde le code qu'on écrit, il ne bride pas le produit.
- **Il fige la dette du jour sans la rembourser.** Ce sont les découpages qui la remboursent : #1525,
  puis #1468 et #1469. Chaque dérogation disparaît avec la fonction qui la portait.
- **Activer RUF100 sur tout le dépôt n'est pas le chemin.** Il retirerait 221 `noqa` qui visent des
  règles non activées (BLE001, S603…) et portent une intention. Le mécanisme se choisit dans #1577.

## 6. Ce qui ne bouge pas

- **Les garde-fous existants**, inchangés.
- **Les découpages restent à comportement constant** : aucune route, aucun contrat de docs/05 §6 ne
  change, et les suites passent sans modification de leurs assertions.
- **L'ordre et les conditions de #1468 et #1469** : #1468 avant les lots du mode conception, #1469
  une fois le jalon produit courant sorti de la boucle d'exécution.
- **Les écartés de docs/06 et docs/49 §4** : on ne découpe ni `lib.sh` ni `run.sh`, et on ne réécrit
  pas le bash.
- **`docs/` garde l'historique.** Les notes de décision et la roadmap sont faites pour lui. Rien n'y
  est réécrit au présent.
- **La règle d'écriture de `CLAUDE.md` et son cliquet** (#965, #1523). La règle de ce document y
  entre en une ligne, et une démonstration en sort pour lui faire sa place.

## 7. Ce qui est écarté

- **Un plafond de lignes par fichier.** Le seuil serait arbitraire, et il punirait la prose avant le
  code.
- **Une passe d'ensemble sur les docstrings.** Un diff de 60 000 lignes de prose ne se relit pas, et
  les découpages touchent déjà le plus gros.
- **Découper les gros fichiers de tests** (`test_orchestrate.py`, 9 991 lignes) avant les modules
  qu'ils testent.
- **Une garde automatique sur les docstrings** : il faudrait un lexique du passé (#746).
- **Le cliquet côté front** : la complexité de `apps/web/` n'a pas été mesurée. La taille moyenne d'un
  fichier y est de 343 lignes, et rien ne le demande aujourd'hui.

## 8. Le présent n'est pas réécrit d'un coup

Les docstrings existantes gardent leur récit tant que personne ne touche leur code. Un renvoi ⚠ est
posé à l'endroit de chaque décision renversée. #1525 réécrit, dans sa PR, ce que docs/05 et
`apps/api/` disent de `app.py`.

## 9. Ce qui rouvrirait la décision

- **Les dérogations C901 ne décroissent pas** une fois les découpages livrés. Le cliquet figerait alors
  une dette que rien ne rembourse. Le constat se lit dans le compte des dérogations, pas sur une
  impression.
- **Une raison disparaît avec le récit**, et une régression revient parce que plus rien ne disait
  pourquoi. Dans ce cas, c'est l'application qui se corrige, pas la règle : elle demande de garder la
  contrainte.
- **La prose ne recule pas dans les fichiers touchés.** Ce serait le signe que la règle n'est pas
  lue. Cela se discute avec la personne, sans ajouter une garde qui jugerait du texte.

## 10. Où cette décision est écrite ailleurs

- **[docs/06](./06-roadmap.md)** : la section « Le code dit le présent ».
- **Un renvoi ⚠ à l'endroit de chaque décision renversée** : docs/49 §4, et dans docs/06 les sections
  « Le coût d'un ticket » et « Le règlement ne grossit plus ».
- **[`CLAUDE.md`](../CLAUDE.md)** : la règle, en une ligne, qui pointe ce document. Pour lui faire
  sa place sous le cliquet, des démonstrations en sont sorties, et chacune vit déjà ailleurs :
  - les pistes écartées du résidu `.claude/` sont en docs/10 §11.7 ;
  - les tickets qui ont fixé la borne de concurrence sont en docs/10 §11.10 ;
  - ceux de `journal.sh audit` sont en docs/10 §11.12 ;
  - `veille-differe` pour un ticket qui applique est à l'étape 5 de `/ticket-start`.

  Deux « depuis #418 » y sont aussi réécrits au présent.
