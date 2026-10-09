<!-- documentation: développement -->
# 47 — Le banc se paie une fois par lot, et au ticket seulement sur son chemin

**Date :** 2026-10-09. **Instruite par :** `/idee` (#1013). **Consignée par :** #1470.
**Jalon :** *Outillage de la forge* (rail outillage, échéance 2027-09-15).
**Tickets :** #1460, parent de suivi, et ses lots #1461 → #1462 → #1464, avec #1463 en parallèle.
Nés de la même mesure, sans parent : #1465 à #1469 ([docs/06](./06-roadmap.md)).

**Renverse en partie :** #1240, critère C2 — *« un ticket qui touche le chemin d'un scénario de
référence le joue avant de pousser »* —, et le déclencheur qui l'exprime : le préfixe
`GL_BANC_CHEMINS` (`maestro/ core/ packages/shared/`) de [docs/10 §6](./10-workflow-git.md).

---

## 0. Ce que ce document décide

1. **Le banc entier se joue une fois par lot**, sur `main` intégrée, par le pilote, après le drain
   final (#1462). C'est ce passage qui attrape une mécanique cassée par un merge.
2. **Au ticket, le banc n'est plus dû que quand le changement touche ce que le modèle reçoit ou
   fait.** Il se joue alors sur le scénario **le moins cher** dont la carte croise le diff (#1463,
   #1464). Sinon, le constat le dit : « non joué : mécanique couverte par <tests>, banc par lot
   sur main ».
3. **Le préfixe du chemin cesse d'être le déclencheur.** La carte de ce que chaque scénario exécute
   le remplace (#1463).
4. **Le filet par lot se pose avant qu'on retire celui du ticket.** L'ordre des lots est la
   décision : l'historique (#1461), puis le banc par lot (#1462), puis le déclencheur dérivé
   (#1464). La carte (#1463) ne dépend de rien et part en parallèle.

Trois réponses ont été proposées à la personne le 2026-10-09 : le banc par lot avec le chemin
dérivé, le chemin dérivé seul, ou plus aucun banc au ticket. Elle a été prévenue que chacune
renversait une décision livrée, et elle a retenu la première.

## 1. D'où vient la demande

> *« il semblerait que le traitement des tickets est devenu coûteux en terme de token. je suppose
> que c'est parce qu'on rejoue à chaque fois tous les scénarios. est-ce vraiment la meilleure
> approche pour avancer intelligemment et améliorer le système au long terme ? »*

La fenêtre de 5 h se vidait vite. Le 2026-10-08, elle était à 97 % en ~80 min, avec trois sessions
en vol. La supposition de départ, que tout le banc se rejouait à chaque ticket, a été **mesurée
avant d'être crue**.

## 2. Ce que la mesure a dit

La mesure porte sur les flux `.maestro/orchestrate/<run>/*.jsonl` des runs `20260926-212547`,
`20260927-105454` et `20261008-080648`. L'usage y est relevé message par message, puis imputé à la
famille d'outils appelée. Le poids d'un résultat d'outil vaut sa taille multipliée par le nombre de
tours restants : c'est ce qu'il fait relire.

| Constat | Mesure |
|---|---|
| Scénarios joués par ticket | 1 à 3, pas les 12. Mais S12 (≈ 26 min, 3 $) a été joué ~6 fois le 08/10 par #1399, #1400 et #1401, trois tickets du même jalon |
| Part du banc dans la session Claude Code | 7,6 % des tokens le 08/10, de 2 à 16 % selon le ticket ; 1,5 à 1,9 % fin septembre |
| Part du banc côté produit | même abonnement. Un passage complet coûte ≈ 13,5 $ et dure ≈ 1 h 25 ; le banc pèse ≈ 20 % d'un run en tout (estimation) |
| Ce qui domine | le contexte relu à chaque tour : médiane de 200 à 375k tokens, pics à 535k (08/10) et 965k (27/09) |
| D'où vient ce contexte | la lecture de code : 82 % du poids des résultats d'outils le 08/10, 64 % fin septembre |

La supposition était donc fausse dans sa lettre : les sessions ne rejouent pas tout le banc. Elle
était juste sur un point, que la mesure a fait voir : **une même preuve se paie plusieurs fois**.
S12 a été rejoué six fois en une journée, sur trois copies de travail, pour trois tickets du même
jalon.

Le banc n'est pas le premier poste de coût. Le contexte relu domine, et il se règle par des tickets,
pas par une décision : l'audit des tokens (#1465), une session de run qui ne charge que ce que le
dépôt déclare (#1466), les gros modules découpés (#1467 à #1469). Le banc est le seul poste dont le
coût tient à une **règle écrite**. C'est pourquoi lui seul a sa note.

## 3. Pourquoi #1240 C2 ne tient plus à ce prix

#1240 avait une raison, et elle tient toujours. #1197, #1198, #1205 et #1212 ont été trouvés au
bouclage, un à deux jours après le merge de ce qu'ils corrigeaient. Ces bugs se trouvent **en se
servant** du produit, et la relecture de code n'y voyait rien (#969). Ce qui a changé, ce sont les
deux hypothèses qui rendaient la règle bon marché.

- **Le prix.** #1240 le disait « connu et assumé » : S2 à 0,41 $ et 1 min 25 s, quand le banc
  s'arrêtait à S1–S4. Le banc compte aujourd'hui **douze scénarios**, dont S12 (une application web
  qui survit à une extinction), à ≈ 26 min et 3 $. Un passage complet coûte ≈ 13,5 $.
- **Le déclencheur.** Le préfixe `maestro/ core/ packages/shared/` couvre tout le produit : **tout
  ticket du produit est « sur le chemin »**. La détection ne distingue plus rien. Tout repose sur
  le jugement de la session, qui choisit son scénario seule, sans savoir ce qu'il exécute, et donc
  large par prudence.

Et la série qui permettrait d'en juger, personne ne la lit. Il y a un rapport par passage, sans le
sha de `main` qu'il a joué, et ceux des worktrees disparaissent avec eux. Sur les 13 passages du
clone principal, les taux de réussite vont de 100 % (S4) à 40 % (S9), et S12 est à 66 % sur trois
passages. Aucune règle ne pouvait s'appuyer sur ces chiffres.

## 4. Le mécanisme

Chaque lot livre une pièce, avec ses tests et sa doc. Ce document en dit la cible ; c'est le lot
qui réécrit le présent.

1. **#1461 — chaque passage du banc entre dans un historique** : taux de réussite, coût et durée
   par scénario, sha de `main` joué, copie de travail et ticket. Il est rangé sous `~/.maestro/`
   (#1454), donc il survit au retrait du worktree. Une commande le lit, et les 13 rapports existants
   y entrent. #1457 garde, de son côté, les rapports que cet historique lit.
2. **#1462 — le pilote joue le banc entier une fois par lot**, sur `main` intégrée, après le drain
   final. Il le joue dans une copie dédiée sur `origin/main`, pour que la stack de la personne reste
   libre (une stack par copie, #1164). Il s'abstient en le disant si `main` n'a pas bougé sur le
   chemin depuis le dernier passage complet. Un rouge nomme les PR mergées depuis le dernier vert du
   scénario. Une limite d'usage vaut abstention, jamais vert. Une option et une variable l'éteignent,
   et un script le joue hors run.
3. **#1463 — chaque passage dit quel code chaque scénario a exécuté** : les fonctions Python et les
   fichiers lus (playbooks, `core/`). C'est la **carte** qui remplace le préfixe. La granularité et
   le moyen se tranchent dans le lot.
4. **#1464 — le banc dû au ticket se dérive de la carte.** `criteres` annonce les scénarios dont la
   carte croise le diff, le moins cher en tête (coût médian tiré de l'historique), et rien quand
   aucun ne croise. S12 n'est proposé que s'il est le seul à couvrir. `/ticket-finish` (étape 4ter),
   le prompt de run et docs/10 §6 changent avec lui. Il écrit sous `.claude/`, donc il est né
   assigné et se traite en interactif (docs/10 §11.7).

**La détection et le jugement restent séparés**, comme sous #1240. La carte dit **quels** scénarios
le diff traverse. Si le changement touche ce que le modèle reçoit ou fait, c'est la session qui le
juge, et elle le consigne. Aucune liste de fichiers « sensibles » ne remplace ce jugement
([docs/41](./41-decision-maestro-juge-il-ne-bride-pas.md)).

## 5. Ce qui ne bouge pas

- **La preuve exercée** (#1240, C1). Un ✓ nomme un test qui passe, ou une observation sur la vraie
  stack : le passage du banc, le run ou la capture. Seul change **quand** le banc est dû au ticket,
  pas ce qu'est une preuve.
- **Un banc injouable n'est jamais compté vert.** Il est nommé au constat (`| Banc | non joué |
  <raison> |`). Au banc par lot, une limite d'usage vaut abstention.
- **Le bouclage d'un jalon produit se joue sur les scénarios** (#1152). `/milestone-bilan` joue le
  banc, un rouge interdit tout `GO`, et un banc injouable est une abstention.
- **Les scénarios restent hors CI** ([docs/40 §5](./40-decision-rythme-et-scenarios-de-reference.md)).
  Un passage coûte du vrai modèle, et un rouge marqué se rejoue une fois.
- **Un ✗ est nommé, jamais coché, et ne bloque pas le merge.** Le merge vérifié (`merge-mr`, #417)
  et le pipeline de la PR ne changent pas.
- **Le choix du scénario se consigne**, joué ou non, avec sa raison.

## 6. Ce qu'on accepte de payer autrement

- **Un défaut de mécanique se voit plus tard.** Un défaut du type #1198 ne serait plus vu avant le
  merge, mais quelques heures plus tard, en fin de run, et `main` peut rester rouge entre-temps.
  C'est le prix du choix. #1462 le borne : il rattache un rouge aux PR mergées depuis le dernier
  vert.
- **Le banc par lot coûte ≈ 13,5 $ et ≈ 1 h 25 par run**, sur le même quota. Il remplace les
  passages répétés au ticket : le 08/10, ≈ 6 × 3 $ pour S12 seul.
- **La carte d'une stack multi-processus** (l'API, l'hôte détaché) est un risque technique. #1463
  le porte. Tant qu'elle n'existe pas, le préfixe reste le déclencheur.
- **Un ticket traité hors run n'a pas de pilote pour jouer le banc par lot.** Le script de #1462 le
  joue à la main, sur la même copie dédiée.

## 7. Le présent n'est pas réécrit avant les lots

Jusqu'à #1464, docs/10 §6, `/ticket-finish` (étape 4ter) et le prompt de run gardent la règle de
#1240. Retirer le banc du ticket **avant** que le banc par lot existe ôterait un filet sans en poser
un autre. Un renvoi ⚠ est posé à l'endroit de la décision, en
[docs/10 §6](./10-workflow-git.md) et en
[docs/40 §6](./40-decision-rythme-et-scenarios-de-reference.md). Chaque lot réécrit le texte qu'il
rend faux, dans sa PR.

## 8. Ce qui rouvrirait la décision

- **L'historique montre qu'un défaut trouvé par le banc par lot coûte plus cher que le banc au
  ticket qui l'aurait attrapé** : `main` rouge sur plusieurs runs, ou un rouge que #1462 ne sait
  rattacher à aucune PR. L'historique de #1461 et l'audit des tokens de #1465 sont les pièces de
  cette mesure, pas une impression.
- **La carte ne se construit pas** (#1463). Le banc par lot tient, mais le banc au ticket reste
  déclenché par le préfixe, et la question du chemin se rouvre.
