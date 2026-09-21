# 39 — Le niveau visuel se choisit une fois, par une personne

**Date :** 2026-09-21. **Instruite par :** `/idee` (#1013). **Consignée par :** #1134.
**Jalon :** *Le niveau visuel — une direction choisie, un écran étalon* (échéance 2028-02-05).
**Chantiers :** #1124 (produit : la direction et l'écran étalon), #1129 (outillage : bibliothèque de
références, veille et regard neuf qui partent de l'étalon, regard de la personne par jalon).

---

## 0. Ce que ce document décide

Une seule chose, et c'est un renversement : **le niveau visuel de la Control Tower n'est plus fermé**.
Il se choisit **une fois, par une personne**, entre des directions poussées rendues sur un écran
réel. Ce choix devient ensuite la référence contre laquelle tout le reste se mesure.

Ce qui est renversé est la direction de [docs/30 §6.1](./30-cible-visuelle-control-tower.md) : « Pas
de nouvelle identité — le même produit, avec du relief ». Avec elle tombe son verdict de banc, au
§1.6 : « il n'y a pas de style à aller chercher — il y a un socle à tenir ». La personne a demandé ce
renversement : elle a accepté par un « go » une recommandation qui le nommait en toutes lettres
(§1). Il n'a pas été tranché à sa place.

## 1. D'où vient la demande

Voici la demande, dans ses mots, le 2026-09-21 :

> « J'ai l'impression que pour l'aspect visuel tu vas souvent voir VS Code, GitHub, GitLab, etc.
> Est-ce que tu penses que c'est vraiment pratique ? Tu ne pourrais pas recommander une approche,
> un process qui produit un design vraiment satisfaisant ? »

L'impression est juste, et elle se mesure. Sur les 21 notes `## Veille de conception` et
`## Variante retenue` consignées sur les tickets #925 à #1107, les mentions de produits (liens
compris) se répartissent ainsi :

| Produit | Mentions |
| --- | --- |
| GitHub (Actions, Discussions, docs) | ~120 |
| VS Code | 46 |
| Vercel | 42 |
| Grafana | 37 |
| GitLab | 21 |
| Linear | 5 |
| Cursor | 5 |

Et ce qui est cité est souvent une **page de documentation lue** (`WebFetch`), pas une interface
**vue**. La veille de #1040 en est l'exemple le plus net : ses trois références sont une doc AWS IAM,
une PR Renovate en markdown et une doc GitHub. Vercel et Backstage y sont écartés parce que le profil
Chrome était tenu par une autre session.

L'analyse complète, avec l'existant, les renversements et chaque arbitrage, est le corps de #1134.

## 2. Pourquoi le processus plafonnait

Trois causes, qui se renforcent :

1. **On cite ce qui est accessible, pas ce qui est le mieux fait.** La règle « ce qui n'est pas
   vérifié n'est pas cité » (#471) est saine, et elle reste. Mais pour une session sans compte,
   « vérifiable » veut dire « public et sans connexion ». Temporal et Langfuse ont été écartés du banc
   de #471 pour cette seule raison. Le produit le plus proche fonctionnellement pèse donc moins que le
   plus accessible.
2. **La veille était conçue pour ne pas chercher de niveau visuel.** `/design-veille` prend « le
   mécanisme, jamais l'apparence », et refuse « une palette neuve, une police de marque ou un parti
   esthétique » comme une erreur de question. C'était cohérent avec docs/30 §6.1. Le plafond d'un
   tel processus est la médiane des outils de développeurs : un rendu correct, sobre et plat. C'est
   exactement ce que la personne constate.
3. **Personne ne portait le goût.** La veille se joue ticket par ticket, sans direction d'ensemble.
   Le regard neuf (#980) juge les variantes contre ces mêmes références. Depuis #1009, aucune
   personne ne choisit plus la variante. Les garde-fous (tokens, sobriété, accessibilité) disent si
   un écran est **conforme**, jamais s'il est **réussi**.

## 3. Ce qui est renversé

### 3.1 Les valeurs du socle se rouvrent, une fois

**Avant.** [docs/30 §6.1](./30-cible-visuelle-control-tower.md) retenait « le même produit, avec du
relief ». Le socle de #245 était « bon et récent », le refaire « coûterait des sessions pour un gain
nul sur le problème mesuré ». Le problème mesuré était la **hiérarchie**, pas l'esthétique.

**Après.** Les **valeurs** du socle se rouvrent : police, échelle typographique, rythme, rayons,
ombres, espacements, usage des tons. Elles le font **une fois**, par un lot qui rend trois
directions poussées, dont une audacieuse, sur le tableau de bord dans son shell (#1125). **La
personne choisit ou compose** la sienne. Le choix est figé dans le socle (#1126), puis dans un
**écran étalon** (#1127).

**Pourquoi.** Le verdict de #471 était juste pour sa question : ce qui manquait alors était un socle
**tenu**. Le chantier #973 l'a tenu, et les sondes le gardent. La question suivante n'a pas de
réponse mesurable : un écran conforme peut être plat. Elle revient donc à une personne, et à elle
seule.

### 3.2 La veille part de l'étalon, puis d'une bibliothèque choisie

**Avant.** `/design-veille` « commence par ce qui est déjà au banc » (docs/30 §1 : GitHub Actions,
Grafana, Linear, Cursor), puis va chercher ce qui est public.

**Après.** Voici l'ordre, porté par #1131 :
1. l'**écran étalon** ;
2. la **bibliothèque de références** du même type de surface (#1130) : 20 à 30 captures
   choisies par une personne, y compris derrière une connexion ;
3. le dehors, **seulement** pour un motif d'interaction qu'aucun des deux ne couvre.

La saisine du regard neuf porte l'étalon et les références. Le regard de la personne, rendu
par jalon, y entre comme **précédent** (#1132).

## 4. Ce qui ne bouge pas

- **Les mécanismes du socle.** Tokens **sémantiques** (les noms, pas les valeurs), primitives, règle
  des trois places (docs/30 §4), état porté par la forme autant que par la couleur, AA dans les deux
  thèmes, cible de 24 px, frontière shell / écran (docs/35 §3.4). Une direction qui en casse un se
  refuse au lot qui la choisit (#1125), pas en revue.
- **« Aucune identité nouvelle », à l'échelle d'un ticket.** Un ticket n'invente toujours pas sa
  palette ni sa police. Ce que la règle désigne change : c'est désormais **la direction retenue et
  son étalon**, et plus le socle de #245.
- **La marque** : le logo et le nom de #120.
- **« Ce qui n'est pas vérifié n'est pas cité. »** La bibliothèque le rend tenable pour les produits
  derrière une connexion, puisqu'une capture versée par une personne **est** vérifiée.
- **#1009 est nuancé, pas renversé.** Un run tranche toujours seul l'écran d'un ticket qui en décide.
  Ce qui change est **contre quoi** il tranche. La personne ne revient qu'à deux endroits :
  - le **lot qui choisit la direction** (#1125), joué en interactif et tenu hors des runs par
    l'assignation (#621) ;
  - la **planche d'un jalon** (#1132), qui ne bloque rien.
- **Le partage de #562, #612 et #714** : ce qui est automatique est la détection du manque, jamais
  le verdict.

## 5. Les arbitrages rendus par `/idee`, à contredire au besoin

1. **L'écran pilote est le tableau de bord**, dans son shell, colonne de conversation ouverte. C'est
   le premier écran vu. Il réunit le plus de familles du socle (tuiles, cartes, liste, badges,
   pipeline) et les trois zones du shell, donc une direction choisie sur lui se propage le plus
   loin. `/chat` vit déjà dans la colonne de droite du shell.
2. **Deux chantiers sur deux rails** : la direction change le produit (#1124), le processus change
   l'outillage de la forge (#1129). Un lot hérite du rail de son parent : un chantier mêlé aurait
   rangé l'un des deux dans un backlog qu'on ne regarde pas pour ce sujet.
3. **La bibliothèque vit dans le dépôt**, sous `docs/design/references/`, et pas sous `.maestro/`.
   Les sessions de run et les worktrees doivent la lire, et `docs/assets/471/` en est le précédent.
   Le dépôt étant **public** (#734), une capture ne porte **aucune donnée personnelle** : elle est
   recadrée ou floutée, et versée par un geste humain.
4. **Les lots de propagation aux autres écrans sont différés** jusqu'au choix. Leur nombre dépend de
   la direction retenue : les créer maintenant serait les deviner. Ils se rangent avant le lot
   « tests + doc » de #1124.
5. **#1125, #1131 et #1132 naissent assignés.** Le premier attend une personne, les deux autres
   écrivent sous `.claude/`, ce qui est bloqué en run (#229).

## 6. Le découpage

Aucun lot n'est marqué `lot::parallele` à l'intérieur d'un chantier. Les deux chantiers, eux, sont
indépendants : #1130 peut se remplir pendant que #1125 se prépare.

**#1124 — Le niveau visuel** (rail produit, 4 lots, plus les lots de propagation à venir) :

| Lot | Ticket | Contenu |
| --- | --- | --- |
| 1 | #1125 | Trois directions poussées sur le tableau de bord, la personne choisit |
| 2 | #1126 | La direction entre dans le socle : tokens, primitives, catalogue |
| 3 | #1127 | Le tableau de bord devient l'écran étalon |
| 4 | #1128 | Tests + doc |

**#1129 — Le goût a un répondant** (rail outillage, jalon « Outillage de la forge », 4 lots) :

| Lot | Ticket | Contenu |
| --- | --- | --- |
| 1 | #1130 | Bibliothèque de références, versionnée et rangée par type de surface |
| 2 | #1131 | La veille et le regard neuf partent de l'étalon et de la bibliothèque |
| 3 | #1132 | Le regard de la personne par jalon, verdicts en précédents |
| 4 | #1133 | Tests + doc |

#1132 s'appuie sur #1060 (un ✗ du regard neuf ouvre un ticket de suite) : deux sources de ✗, un seul
verbe de suite.

## 7. La place dans la file

Sur le rail produit, l'échéance d'un jalon **est** son rang :

| Jalon | Échéance |
| --- | --- |
| « L'équipe sur mesure » (soldé, verdict rendu) | 2028-01-05 |
| « Avant l'installeur — les réserves levées » | 2028-01-26 |
| **« Le niveau visuel »** | **2028-02-05** |
| Phase 9 | 2028-02-16 (inchangée) |

- **Derrière « Avant l'installeur ».** Ses réserves sont des corrections indépendantes de la
  direction. Placé devant, ce jalon deviendrait le jalon courant alors que son premier lot attend une
  personne : un run n'y trouverait rien à prendre.
- **Devant la Phase 9**, pour l'argument de la Phase 9 elle-même : on n'empaquette pas une cible
  mouvante ([docs/24 §4.8](./24-projets-locaux-et-poste-de-travail.md)). Le premier lancement (#642)
  et l'installeur (#641) montreraient un niveau visuel sur le point de changer. C'est la quatrième
  fois que cet argument range un jalon devant elle.
- **Aucune autre échéance n'a bougé**, et aucune priorité existante non plus.
- **#1125 n'attend pas son tour.** Joué en interactif, il se démarre à la main dès maintenant, en
  parallèle des runs.

## 8. Ce qui rouvrirait la décision

- **La direction retenue elle-même** se rouvre par le même geste, jamais ticket par ticket : un
  nouveau lot de directions, choisi par une personne. Un ticket qui s'écarte de l'étalon « parce que
  c'est mieux ici » refait le défaut que cette note corrige.
- **La bibliothèque** se retire si elle ne sert pas. La mesure est la part des veilles postérieures
  à #1131 qui la citent, contre celles qui repartent au dehors.
- **Le regard par jalon** s'allège s'il n'est pas rendu. Un jalon bouclé sans planche annotée est un
  fait à nommer au bilan, pas un blocage.

## 9. Où cette décision est écrite ailleurs

Un renvoi ⚠ vers cette note est posé à l'endroit de chaque décision renversée ou rendue fausse :
- [docs/30 §1.6 et §6.1](./30-cible-visuelle-control-tower.md) : les deux décisions renversées ;
- [docs/30 §5.1](./30-cible-visuelle-control-tower.md) : le maillon 0, dont les règles citent le
  verdict du banc ;
- [docs/35 §0](./35-decision-poste-de-bureau-et-disposition.md) : il disait que §6.1 « tient » ;
- [docs/36 §0 et §3.3](./36-outillage-du-design.md) : le critère qui écartait `artifact-design`.

**Ce qui décrit l'état présent et que les lots réécriront**, sans être réécrit ici :

| Texte | Ce qu'il dit aujourd'hui | Réécrit par |
| --- | --- | --- |
| `.claude/commands/design-veille.md` | « Commence par ce qui est déjà au banc », « Elle ne cherche pas un style », « Proposer une identité nouvelle » | #1131 |
| `.claude/commands/ticket-start.md`, étape 7 | variantes « tokens et primitives du socle, aucune identité nouvelle (docs/30 §6.1) » | #1131 |
| `scripts/orchestrate/run.sh`, prompt de session | « aucune identité nouvelle » rapporté au socle de docs/30 | #1131 |
| `CLAUDE.md`, ligne `/design-veille` | « aucune identité nouvelle n'est cherchée » | #1131 |
| docs/30 §5 (régime de la veille) | la veille « ne rouvre pas » le verdict du banc | #1133 |

Jusqu'à leur réécriture, ces textes restent justes **pour un ticket ordinaire** : il applique une
direction qu'il n'a pas choisie. Ils ne sont faux que pour le lot qui la choisit, #1125.

Les mentions **historiques** de §6.1 ne se réécrivent pas : ce sont les raisons d'un choix passé,
pas un état présent. Il s'agit du refus du rayon en gélule dans `apps/web/README.md`, et des veilles
journalisées au docs/30 §5.7.
