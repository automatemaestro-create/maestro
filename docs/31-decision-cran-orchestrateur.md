# 31 — Le cran « orchestrateur » de l'arbitrage : note de décision

> Ticket #647. Décision datée du **2026-08-28**, sur `origin/main` à `1bef04a`.
>
> **Trois arbitrages, rendus sur trois mesures.** ① Le cran `orchestrateur` est **retiré**, pas
> branché : il recouvre deux choses dont l'une *est* `auto` et dont l'autre est un LLM qui garde un
> LLM — ce que #586 a déjà refusé, pour moins de pouvoir que ce qu'on lui donnerait ici. ② **Pas
> d'escalade** vers l'humain, et il faut dire pourquoi : ce n'est **pas** une question de sûreté —
> l'escalade allait dans le sens sûr —, c'est qu'après ① il n'y a plus de milieu d'où escalader.
> ③ Le **canal « question »** est écarté de l'arbitrage et **renvoyé à #354**, avec la frontière
> écrite : l'arbitrage tranche des **actes**, #321 est la question posée **avant** la décomposition,
> et tout ce qu'un agent **écrit pendant sa tâche** appartient à #354.
>
> ① renverse un lot livré (#586). La note dit ce que le retrait coûte, et ce qu'il **referme** :
> le trou de trace ouvert par ce même lot (§6).

---

## 1. La question, et l'état du dispositif

Le chantier #573 a déplacé le déclencheur de l'arbitrage du **texte de la tâche** vers l'**acte**,
puis #586 a posé **qui tranche** : trois crans dans la politique, à froid — `auto`, `orchestrateur`,
`humain`, défaut `humain`.

Le cran du milieu n'est branché sur rien. `Guardrails.orchestrateur` existe
(`maestro/engine/guardrails.py:332`), le routage existe (`demande_validation`, l. 394-397), le
fail-safe existe (`_tranche`, l. 420-421) — et **aucun appelant de production ne fournit le canal**.
Un acte classé `orchestrateur` rend donc aujourd'hui, invariablement :

```
(False, "aucun orchestrateur configuré — refus par défaut")
```

C'est le pire des trois états possibles : la politique **promet une décision** et rend un refus. Un
auteur qui écrit `{"ask": {"Bash": "orchestrateur"}}` obtient l'inverse de son intention, et **rien
ne l'en avertit** — le cran est parfaitement valide au chargement (`_ask_validee`,
`permissions.py:535`), il n'échoue qu'à l'exécution, une fois par acte, dans un motif que seul
l'agent lit.

La question du ticket est donc : brancher, ou retirer.

## 2. Ce que la mesure dit — trois faits

Trois mesures ont été prises sur le dépôt à `1bef04a`. Elles portent la décision, et aucune n'était
acquise avant de regarder.

**Fait 1 — le cran a une population de zéro.** `core/permissions/` ne contient qu'**un** fichier de
politique, `designer.json`, écrit pour le pilote MCP Figma :

```json
{ "allow": [], "deny": [] }
```

**Aucune entrée `ask` n'existe dans le dépôt.** Pas une. Aucun outil n'est donc suspendu, aucune
`DemandeValidation` d'acte n'est composée, et **aucun décideur n'est jamais consulté** — ni
`orchestrateur`, ni `humain`, ni `auto`. La chaîne livrée par #580/#581/#583/#584/#586 est complète
et **dormante**. Brancher le cran du milieu, ce serait câbler un canal pour une population de zéro.

**Fait 2 — le canal n'existe nulle part ailleurs qu'en test.** `orchestrateur=` apparaît **10 fois
dans le dépôt, toutes dans `tests/`** (`test_guardrails.py` ×7, `test_arbitrage_acte.py`,
`test_permissions.py` ×2). Les cinq sites de production qui montent un `Guardrails`
(`controltower/executions.py:1436`, `controltower/hote_detache.py:1046`, `engine/cli.py:378`,
`demo.py:286`, `controltower/validation.py:258`) ne passent que plafonds, délai et `validateur`.

**Fait 3 — la trace du décideur s'arrête à la frontière de l'API.** #586 a écrit « *qui a tranché se
lit, il ne se déduit pas* », et c'est vrai du journal (le nom d'étape porte le cran,
`executor.py:1082`) et de l'API (`EtatValidation.to_dict`, `state.py:393`). Ce n'est **pas** vrai de
l'écran : `decideur` apparaît **zéro fois dans tout `apps/web/`** — absent du type
(`lib/types.ts:263-278`), jamais lu par `PanneauValidations.tsx`. La docstring qui justifie le champ
(`state.py:347-353`) dit pourtant : « *une demande en attente d'orchestrateur et une demande en
attente d'une personne portent le même statut et n'appellent pas le même geste* ». Le geste est
distinct ; l'écran ne le distingue pas. On y revient au §6 — c'est le trou que le retrait referme.

## 3. Arbitrage ① — le cran `orchestrateur` est retiré

> **Verdict : le cran est retiré de la politique, du routage et du garde-fou.** Il n'est pas branché,
> et il ne reste pas routé sans canal.

### 3.1 Les deux choses que le cran recouvre — et aucune n'a besoin de lui

Le ticket pose trois candidats pour « par quoi l'orchestrateur tranche » : une **passe modèle**, une
**règle déterministe** (la politique étendue), ou l'**agent orchestrateur existant**. Ils se
ramènent à deux, et le troisième n'échappe pas à l'alternative — c'est la passe modèle avec un
prompt de rôle.

**a) Ce qui se décide à froid *est* `auto`.** Une règle déterministe posée dans la politique est une
décision **déjà prise**, par l'auteur de la politique, au moment où il l'a écrite. L'appeler
« l'orchestrateur tranche » nomme un décideur là où il n'y a qu'une règle et un `if`. Or `auto`
c'est exactement cela : *personne n'est sollicité, l'appel passe, et il est tracé*. Le cran du
milieu n'ajouterait à `auto` qu'un canal à câbler et un fail-safe à tenir, pour la même décision.

Une objection tient debout et il faut la traiter : une règle **évaluée à chaud sur les arguments**
(`rm -rf {chemin}` — le chemin est-il dans l'espace de travail ?) n'est pas `auto`, qui juge le
**nom de l'outil** avant de voir les arguments. C'est vrai, et c'est un vrai manque. Mais ce n'est
pas un **décideur** : c'est une **portée** sur l'entrée de politique, évaluée au même endroit et par
le même code que le cran. La question du ticket — « le cran seul suffit-il, ou faut-il une portée ? »
— se répond donc ainsi : **le manque est la portée, et la portée ne demande aucun canal**. Elle est
nommée au §8, sans être découpée, faute de population à servir (fait 1).

**b) Ce qui se décide à chaud, au jugement, est un LLM qui garde un LLM.** C'est la passe modèle, et
c'est très précisément ce que #586 a écarté :

> « et si l'orchestrateur répondait lui-même ? » ne peut pas être une décision de LLM prise au vol :
> elle ne serait ni traçable ni testable, et le garde-fou reviendrait à faire garder un LLM par un
> LLM. — `maestro/decideur.py`, l. 9-12

⚠ Et l'écart joue **contre** le branchement, pas pour lui. #586 refusait de laisser un LLM choisir
**quel cran** s'applique. Lui faire rendre le **verdict** est strictement **plus** de pouvoir :
choisir le cran laissait encore une personne au bout sur le cran `humain`, rendre le verdict n'en
laisse aucune. On invoquerait donc #586 pour faire, en plus grave, ce que #586 a interdit.

### 3.2 Ce que coûterait le câblage — trois prix, tous structurels

Aucun des trois n'est cosmétique, et aucun ne se paie une seule fois.

**Le fournisseur de modèle n'est disponible à aucun des quatre sites de run.** Il est résolu
**à l'intérieur** d'`OrchestrationEngine.default` (`engine/loop.py:502-506`). Brancher une passe
modèle demanderait soit d'instancier un fournisseur à part sur chaque site, soit de faire passer le
fournisseur par `FabriqueMoteur` (`executions.py:234`) — c'est-à-dire d'ouvrir la frontière que
cette fabrique existe pour tenir.

**Un canal-closure ne traverse ni Celery ni Temporal.** `queue/worker.py:113-134` et
`durable/activities.py:115-140` configurent les garde-fous par **variable globale de processus**,
et seuls les **plafonds** sont sérialisés dans l'argument de workflow (`durable/engine.py:135-136`).
C'est déjà la limite du `validateur`, et le CLI l'assume en **refusant** les combinaisons
(`cli.py:301-315` : `--validation-ui` + `--queue`, `--notifier` + `--queue`). Un second canal, c'est
un second jeu de refus à écrire et à tenir d'accord avec le premier.

**L'appel serait sur le chemin critique d'un hook borné.** L'attente effective d'un arbitrage vaut
`min(240, 300 − 5) = 235 s` (`providers/arbitrage.py`, `BornesArbitrage.attente_effective`), et tout
le dispositif de #583 tient à un invariant : *notre attente reste strictement sous la borne que nous
annonçons au runtime*, pour que **nous** répondions à l'échéance et jamais le CLI. Y placer un appel
modèle, c'est placer une latence non bornée — dépendante d'un fournisseur, d'un réseau, d'un quota —
dans la seule fenêtre du système qui ne doit jamais être dépassée. Le fail-safe ne casserait pas
(l'expiration rend un `deny` motivé), mais le cran du milieu deviendrait le cran qui **expire**.

### 3.3 Pourquoi retirer, et pas simplement laisser en place

Trois états étaient possibles : brancher, laisser, retirer. Laisser est le pire, et c'est l'état
actuel — la politique promet une décision et rend un refus (§1). Un cran qui documente une capacité
qu'il n'a pas est plus coûteux qu'un cran absent : il se pose dans un fichier versionné, il se relit
comme une option offerte, et il se découvre faux à l'exécution.

Le retrait rend au dispositif la propriété qu'il annonce : **ce que la politique offre, le
garde-fou le sert**.

### 3.4 Ce qui survit — et le prix du retrait, nommé

**#586 reste vrai pour moitié.** Il écrit de l'orchestrateur : « ce qu'il peut, il le peut **seul** :
refuser, et répondre à une demande d'information ». La **seconde** moitié est vivante et branchée —
c'est le canal `brief.questions` / `brief.reponses` (#321), dont il est l'acteur
(`controltower/brief.py:76`). Seule la **première** — trancher un acte — n'a jamais eu de canal. On
retire la moitié morte ; la vivante ne bouge pas.

**Le prix est un lien, et il faut l'écrire là où il vivait.** #586 a délibérément noué l'acteur du
brief et le cran de décision :

```python
ACTEUR_ORCHESTRATEUR = str(Decideur.ORCHESTRATEUR)   # maestro/decideur.py:86
ACTEUR_BRIEF = ACTEUR_ORCHESTRATEUR                  # maestro/controltower/brief.py:76
```

avec ce motif : « *c'est le même acteur que le cran de décision `orchestrateur` […] deux constantes
littérales laisseraient croire à deux acteurs qui se ressemblent* ». Retirer le membre d'énumération
**délie les deux**. La **valeur ne bouge pas** (`"orchestrateur"`, au caractère près), donc **aucune
migration de donnée** — c'est le *lien* qui disparaît, et le retrait doit le dire à l'endroit où il
vivait plutôt que le laisser se perdre en commentaire supprimé.

**L'asymétrie écriture/relecture tient le retrait toute seule**, et c'est ce qui le rend sûr sans une
ligne de migration. Elle est déjà la règle du dispositif :

- **en écriture**, une politique qui dit `"orchestrateur"` échoue **franchement** au chargement
  (`_ask_validee`, `permissions.py:535-541`, qui nomme les crans admis) — une politique qu'on est en
  train de charger peut encore être corrigée ;
- **en relecture**, un événement déjà émis qui porte `"orchestrateur"` se relit `humain`
  (`decideur_depuis`, `decideur.py:105-108`) — un événement déjà émis, non.

Le repli tolérant envoie donc les anciens événements vers le cran **le plus fermé**. C'est le sens
sûr, et il était déjà écrit.

## 4. Arbitrage ② — pas d'escalade, et **pas** pour raison de sûreté

> **Verdict : pas d'escalade orchestrateur → humain.** Non parce qu'elle serait dangereuse — elle ne
> l'était pas —, mais parce qu'après ① il n'y a plus de milieu d'où escalader.

### 4.1 La chose à ne pas se raconter

Il faut l'écrire avant les trois positions, sans quoi la décision sera relue de travers : **l'escalade
orchestrateur → humain n'a jamais menacé EF-08/ENF-04.** L'invariant a un **sens** — *la machine ne
peut pas approuver ce qui revient à une personne*. L'escalade va dans l'**autre** sens : elle ajoute
une personne là où la machine hésitait. Elle est **monotone vers plus d'autorité**, donc
structurellement sûre.

Ranger son refus sous « c'était risqué » interdirait demain, pour une mauvaise raison, le seul
mouvement du dispositif qui soit sûr par construction.

### 4.2 Les trois positions du ticket, instruites séparément

**Pas d'escalade (un saut) — retenue.** Elle devient une **conséquence** du retrait plutôt qu'un
choix : avec deux crans, `auto` ne consulte personne et `humain` consulte une personne. Il n'existe
plus d'intermédiaire.

**Escalade sur abstention explicite — écartée, et elle l'aurait été même en gardant le cran.** Elle
demandait un **troisième retour** du canal, distinct d'approuve/refuse. Deux prix : le contrat
`Validateur` est `Callable[[DemandeValidation], bool | Awaitable[bool]]` (`guardrails.py:285`) et
sert les **deux** portes — l'élargir pour un besoin qu'une seule a le fait payer aux deux ; et un
« je ne sais pas » est **le plus difficile à tester des trois retours**, parce qu'approuver et
refuser se vérifient sur l'acte quand l'abstention ne se vérifie que sur l'aveu de qui s'abstient.
Un garde-fou dont un tiers des issues n'est pas vérifiable n'est pas un tiers moins sûr, il est
sûr **jusqu'à** cette issue.

**Escalade sur panne du canal — écartée, et elle l'aurait été même en gardant le cran.** Deux raisons.
Le fail-safe cesserait d'être **inconditionnel** : « canal en panne ⇒ refus » deviendrait « ⇒
peut-être », et c'est la propriété que `_tranche` existe pour tenir en un seul endroit
(`guardrails.py:409-412` : « deux copies de ce fail-safe seraient deux endroits où l'oublier »).
Et surtout, **les deux canaux partagent le bus** : `ValidateurControlTower` passe par `EventBus`
(`validation.py:136-144`), et tout canal orchestrateur adossé à la Control Tower ferait de même. Une
panne qui coupe l'un coupe l'autre — l'escalade dépenserait un aller-retour pour aboutir au même
refus, à l'intérieur d'une fenêtre de 235 s.

### 4.3 La démonstration : l'invariant tient par le routage, et il en sort **plus fort**

Après retrait, `Guardrails.demande_validation` a deux issues et une seule branche :

- `auto` → `(True, DETAIL_AUTO)`, **sans appeler aucun canal** ;
- tout le reste → `_tranche(self.validateur, …)`.

Il n'existe alors **aucun canal machine sur aucun chemin**. L'invariant « l'orchestrateur ne peut
jamais approuver un acte classé `humain` » n'a plus besoin d'être tenu par l'absence d'une branche :
**il n'y a plus d'orchestrateur**. Une propriété qu'on ne peut pas violer faute de sujet est plus
forte qu'une propriété tenue par un routage correct — c'est le même déplacement que #586 avait fait
en retirant le canal du chemin plutôt qu'en lui refusant l'approbation.

⚠ **Ce que le retrait ne protège pas, et qu'il ne faut pas confondre.** `auto` reste une approbation
que personne ne prononce à l'instant de l'acte. La différence est entière et elle est le cœur de
#586 : elle est posée **à froid, par écrit, versionnée avec le dépôt**. C'est une **décision humaine
différée**, pas une décision machine. Lire `auto` comme « la machine approuve » défairait #586 aussi
sûrement que brancher le cran du milieu.

## 5. Arbitrage ③ — le canal « question » appartient à #354

> **Verdict : la question est écartée de l'arbitrage et renvoyée à #354**, avec la frontière écrite.
> Elle n'est pas refusée : elle change de cadrage.

### 5.1 Quatre canaux, trois axes, aucun recouvrement

| | qui demande | ce que la réponse porte | ce qui est suspendu |
| --- | --- | --- | --- |
| **arbitrage sur l'acte** (#573) | nous (la politique) — ou l'agent (#582) | un **booléen** | l'**appel d'outil** |
| **clarifications du brief** (#321) | l'**orchestrateur** | du **texte** (un brief corrigé) | le **run**, avant décomposition |
| **« déclarer un blocage »** (#354) | l'**agent** | **rien** — c'est une émission | rien |
| **canal « question »** (candidat) | l'**agent** | du **texte** | l'**appel**, pendant la tâche |

La règle d'appartenance tient en une ligne : **l'arbitrage tranche des ACTES — un oui/non sur ce que
l'agent va faire ; #321 est la question posée AVANT la décomposition, par l'orchestrateur ; tout ce
qu'un agent ÉCRIT ou DEMANDE pendant sa tâche appartient à #354.**

### 5.2 Le précédent qui tranche, et pourquoi le refaire à l'envers serait le défaire

Le candidat ne peut pas vivre dans l'arbitrage, et pas seulement par répartition de tickets : le
canal de l'arbitrage porte un **`bool`**, sur ses trois contrats — `Validateur`,
`Arbitre` (`arbitrage.py:129`), `ArbitreActe` (`arbitrage.py:195`). Y faire voyager du texte
demanderait de les élargir tous les trois.

Or c'est **exactement** la question que #320 a déjà tranchée, dans l'autre sens, en donnant au brief
un canal à lui :

> Détourner le validateur pour ça aurait demandé de faire voyager un brief dans un canal fait pour un
> oui/non. — `maestro/controltower/brief.py`, l. 15-19

Le précédent est écrit et il est bon. Faire entrer une question textuelle dans le canal booléen
serait le défaire.

### 5.3 Ce que #354 doit en retenir — pour que les deux cadrages se répondent

#354 instruit « déclarer un blocage » parmi ses verbes candidats. Le canal « question » lui
ressemble au point de **fusionner** si personne ne les sépare, et ils ne doivent pas :

- **« déclarer un blocage » n'attend rien** — l'agent émet, puis continue ou s'arrête ;
- **« poser une question » attend une réponse et suspend l'agent** — donc coûte une attente bornée,
  une mémoire de décision, et une place chez la personne.

Un seul verbe pour les deux rendrait le blocage **suspensif** (donc cher, sur le verbe le plus
additif de la liste) ou la question **non suspensive** (donc sans réponse, c'est-à-dire pas une
question). Si #354 retient le second, il hérite de trois pièces déjà écrites plutôt que de les
refaire : les bornes d'attente (`BornesArbitrage`), le crédit de délai (`CreditArbitrage`, #584 —
*le temps d'arbitrage n'appartient pas à la tâche*) et la mémoire d'une réponse tardive
(`MemoireArbitrage`). Ce sont des pièces de **suspension**, pas d'arbitrage : elles ne supposent
nulle part que la réponse soit un booléen.

**Où ça arriverait chez la personne**, si #354 retient le verbe : **pas** dans `/api/validations`. La
file porte des **actes à décider**, et sa carte est une carte oui/non (`PanneauValidations.tsx`,
`CarteValidation` l. 249-400, avec `ArgumentsActe` et le refus motivé). Une question ouverte n'y a
pas de geste. Le **chat** est le candidat — #483 a déjà posé que « brief, clarifications et
validation se décident dans le fil » —, mais c'est une décision de #354 et elle n'est pas prise ici.

## 6. Ce que la trace porte — et le trou que le retrait **referme**

Le ticket demande ce qu'un routage à deux sauts devrait laisser au journal. Il n'y a pas de second
saut, donc rien à ajouter. Mais la mesure du §2 (fait 3) a sorti un trou **existant**, et il vaut
d'être suivi jusqu'au bout parce que sa conclusion est contre-intuitive.

`decideur` est servi par l'API (`state.py:393`) et **absent de tout `apps/web/`**. Le motif du champ
(`state.py:347-353`) est qu'« une demande en attente d'orchestrateur et une demande en attente d'une
personne […] n'appellent pas le même geste ».

**Le retrait referme ce trou au lieu de l'ouvrir.** Après ①, il ne reste que `auto` et `humain` — et
un acte `auto` **n'atteint jamais la file** : le hook le court-circuite (`claude.py:717-726`), il
trace et rend `{}` sans composer de `DemandeValidation`. Donc **aucune validation en attente ne peut
porter autre chose que `humain`**, et la distinction que l'écran ne sait pas faire n'a plus d'objet.
Le champ devient constant sur la file.

> **Conséquence : aucun ticket d'UI n'est ouvert.** Le manque était réel ; il disparaît avec sa cause.
> Ouvrir un ticket pour afficher un champ constant serait payer #586 une seconde fois.

⚠ **Et le champ ne se retire pas pour autant** — c'est le piège de ce raisonnement, et le §7 en fait
un point à ne pas défaire. `Event.decideur` et `EtatValidation.decideur` portent de la **donnée
durable** : le journal d'événements est rejoué, et des événements déjà émis portent la chaîne
`"orchestrateur"`. Retirer le champ perdrait cette donnée à la relecture. Ce qui se retire est le
**routage** — l'énumération, le canal, la branche —, jamais la mémoire de ce qui a été décidé.

## 7. Le découpage

La décision **engage du code** : le retrait. Un lot, sous le seuil du parent de suivi — une couche
cohérente (la chaîne d'arbitrage), un travail de **retranchement** et de docstrings, pas trois
couches substantielles.

**Lot unique — retirer le cran `orchestrateur`** (#715) :

- `maestro/decideur.py` — le membre `ORCHESTRATEUR` part ; `ACTEUR_ORCHESTRATEUR` **reste**, en
  littéral, avec le motif du délien écrit sur place (§3.4) ;
- `maestro/engine/guardrails.py` — le champ `orchestrateur` et la branche de `demande_validation`
  partent. `_tranche` **reste** extrait : il porte le fail-safe, et le remettre en ligne pour une
  seule porte serait défaire la raison de son extraction ;
- `maestro/agents/permissions.py` + `core/permissions/README.md` — l'ensemble admissible se réduit
  **tout seul** (`tuple(Decideur)`), il n'y a que la documentation à reprendre ;
- `maestro/controltower/brief.py`, `maestro/providers/claude.py` — la source de `ACTEUR_BRIEF` et le
  commentaire « les deux autres crans » ;
- `docs/01 §5`, `docs/08`, `core/permissions/README.md` — les trois endroits qui énoncent les trois
  crans (déjà pointés vers cette note, cf. §9) ;
- `tests/` — `test_guardrails.py`, `test_arbitrage_acte.py`, `test_permissions.py`.

**Quatre choses à ne pas défaire dans ce lot :**

1. **`Event.decideur` et `EtatValidation.decideur` restent** — donnée durable, événements rejoués
   (§6). Ce qui part est le routage, jamais la mémoire ;
2. **l'asymétrie écriture/relecture reste** — une politique qui dit `"orchestrateur"` doit échouer
   **franchement**, un événement qui le porte doit se relire `humain`. C'est déjà le comportement, il
   suffit de ne pas l'attendrir « pour compatibilité » ;
3. **`ACTEUR_ORCHESTRATEUR` garde sa valeur au caractère près** — `"orchestrateur"`. Le renommer
   ferait une migration là où il n'y en a aucune ;
4. **`auto` n'est pas touché** — et surtout n'hérite de rien du cran retiré. Le cran du milieu ne
   « devient » pas `auto` : les actes qui auraient dû lui revenir remontent au **défaut**, `humain`.

**Ce qui n'est pas découpé, et pourquoi.** La **portée par argument** (§3.1a) est le vrai manque du
dispositif, mais elle n'a pas plus de population que le cran qu'on retire (fait 1). La découper
maintenant serait construire la seconde chose pour zéro acte après avoir refusé la première pour zéro
acte. Elle est nommée au §8 comme porte, pas comme lot.

**Ticket voisin ouvert, qui n'est pas le découpage de cette décision** — #716, *armer la politique*.
Le fait 1 est un constat indépendant : la chaîne #573 est livrée et **dormante**, faute d'une seule
entrée `ask` dans le dépôt. C'est ce qui manque pour que quoi que ce soit soit jamais arbitré, et
c'est aussi ce qui rouvrirait la présente décision (§8).

## 8. Ce qui rouvrirait la décision

Trois portes, dans l'ordre de vraisemblance. Chacune est un **fait à mesurer**, pas une opinion.

**Porte 1 — une population, et une file qui déborde.** Si #716 arme la politique et que les actes
classés `ask` deviennent assez nombreux pour que la file `/api/validations` soit une charge réelle
pour une personne. C'est la seule porte qui justifierait un décideur intermédiaire, et elle se mesure
sans ambiguïté : demandes en attente, temps de séjour, taux d'approbation. ⚠ Un **taux d'approbation
proche de 1** ne dit pas « il faut une machine pour trancher » — il dit que ces actes-là méritaient
`auto`, ce qui coûte une ligne de politique et aucun canal.

**Porte 2 — un acte dont le verdict dépend des arguments.** Le cas `rm -rf {chemin}`. La réponse
n'est alors **pas** ce cran-ci mais une **portée** sur l'entrée de politique (§3.1a) : déterministe,
testable, évaluée là où le cran l'est déjà, sans canal ni fournisseur ni traversée de frontière
distribuée. Cette porte rouvre la *portée*, pas le *décideur*.

**Porte 3 — un jugement contextuel dont on accepte le régime.** Un acte dont la légitimité dépend du
**plan**, que la politique ne connaît pas (« ce `rm` est-il dans le périmètre de la tâche T3 ? »).
C'est la seule porte qui rouvrirait vraiment une passe modèle — et elle demande d'assumer par écrit
ce que le §3.1b refuse : un garde-fou gardé par un LLM. Elle ne se franchit pas sans renverser #586
explicitement, comme cette note renverse #586 explicitement.

Aucune des trois n'est ouverte aujourd'hui, et la première ne peut pas l'être avant #716.

## 9. Où cette décision est écrite ailleurs

Trois endroits énoncent les trois crans et **ne changent pas encore** — le retrait est #715, la
décision est cette note, et faire dire à la documentation un état que le code n'a pas serait le
défaut inverse de celui qu'on répare. Chacun reçoit donc un **pointeur** vers ici, pour qu'un lot
voisin ne bâtisse pas sur un cran décidé mort :

- [docs/01 §5](./01-architecture-technique.md) — modèle d'autonomie et de contrôle, item « human-in-the-loop » ;
- [docs/08](./08-glossaire.md) — l'entrée HITL ;
- [`core/permissions/README.md`](../core/permissions/README.md) — le contrat du fichier de politique.
