# 35 — Le poste de travail de bureau : la coque, et la disposition à trois zones

**Date :** 2026-09-11 · **Chantier :** #921 (8 lots) · **Jalon :** *L'atelier — le travail au centre,
la conversation à portée*

---

## 0. Ce que ce document décide

Trois choses, et une seule est un renversement :

1. **La coque de bureau sera Electron** — ce qui **renverse la décision D4**
   ([docs/24 §4.5](./24-projets-locaux-et-poste-de-travail.md)), qui avait retenu Tauri et écarté
   Electron nommément. §2.
2. **La Control Tower passe à trois zones** — navigation à gauche, travail au centre, conversation à
   droite. §3.
3. **Ce que le chantier ne fait pas**, écrit aussi explicitement que ce qu'il fait. §4.

Ce qu'il ne décide **pas**, et c'est délibéré : rien du socle visuel. La direction de
[docs/30 §6.1](./30-cible-visuelle-control-tower.md) — « le même produit, avec du relief » — tient.
Aucune identité nouvelle, aucune palette, aucune police.

---

## 1. D'où vient la demande

### 1.1 Parler et regarder sont deux gestes alternés, et l'interface impose de choisir

Depuis #470, le fil est la **seule porte d'entrée** du produit : on y compose un objectif, on y
répond au cadrage, on y suit ce que l'orchestration dit. C'est aussi un **écran** — `/chat` — parmi
onze. Regarder un run, c'est donc quitter la conversation ; revenir à la conversation, c'est cesser
de regarder le run.

Ces deux gestes ne sont pas successifs, ils sont **entrelacés** : on lance, on regarde, on demande,
on regarde encore. Une navigation qui les sépare fait payer cet entrelacement à chaque aller-retour.

### 1.2 Le retex du 2026-09-11 le mesure, sans l'avoir cherché

Le premier [retex utilisateur](./retex/2026-09-11-premiere-session-utilisateur.md) — poste vide →
projet → objectif → run → livrable exécuté — ne portait pas sur la disposition. Il en rapporte
pourtant quatre constats qui la visent, et un verdict qui la fonde :

> **le pipeline du run** : graphe par niveaux, dernier geste horodaté, checklist, chrono, coût par
> tâche — **la meilleure vue du produit**.

Cette vue est aujourd'hui à deux clics, derrière une liste, sur un écran qu'on ne visite pas
spontanément. Les quatre constats :

| Constat | Ce qu'il dit | Où il atterrit |
|---|---|---|
| **G1** | Un run qui se termine **ne prévient personne** et ne dit pas où est le livrable — 53 min, 12,51 $, un livrable qui fonctionne, et personne ne le sait | lot 7 |
| **G2** | La tuile « Run en cours » affiche **Aucun** pendant que la section juste dessous affiche « EN COURS 1 » | lot 6 |
| **G3** + **C8** | Pipeline « 4 tâches », Kanban **1 carte**, en-tête « 0/1 » → « 1/2 » → « 2/3 » : le **dénominateur grandit**. `/couts` compte 8 tâches pour un run qui en a 4 | lot 3 |
| **G11** | Les **quatre premières minutes** d'un run ne montrent rien, pendant que le coût monte à 2,68 $ | lot 6 |

⚠ **C'est G3 qui fixe l'ordre du chantier.** Mettre au centre une vue dont les comptes se
contredisent amplifierait le défaut sur l'écran le plus visible du produit. Le lot 3 précède donc le
lot 6, et ce n'est pas une préférence de séquencement : c'est la condition pour que la refonte soit
un progrès et non un déplacement.

---

## 2. La coque : Electron, et le renversement de D4

### 2.1 Ce que D4 avait décidé, et sur quoi

[docs/24 §4.5](./24-projets-locaux-et-poste-de-travail.md), le 2026-08-04, a classé cinq options et
retenu **« lanceur/installeur d'abord, Tauri ensuite »**. Electron y est l'option 3 :

> **Écarté sauf besoin précis** : ~150 Mo contre ~10, sans avantage ici — l'UI n'a besoin d'aucune
> API exotique.

L'argument est **un seul**, et c'est le poids. La seconde moitié — « sans avantage ici » — est une
constatation d'époque, pas une mesure.

### 2.2 Ce qui a changé, et qui n'était pas connu alors

**Le besoin précis est arrivé**, et c'est le chantier #930 qui le fait naître : une session doit
pouvoir **regarder le rendu qu'elle vient d'écrire**, en run comme en interactif. Or
`mcp__chrome-maestro`, Playwright et le job `web-build` ciblent tous **Chromium**.

- Avec **Electron**, le moteur que la session vérifie **est** le moteur que l'utilisateur exécute.
- Avec **Tauri**, l'utilisateur exécute WebView2 sous Windows, WKWebView sous macOS, WebKitGTK sous
  Linux — trois moteurs, dont aucun n'est celui que le filet regarde.

C'est exactement la leçon de **#333** transposée : le job `pytest` tournait dans une image sans git,
285 tests étaient sautés, et personne ne le voyait — *un garde-fou qui saute est plus dangereux
qu'un garde-fou absent*. Un filet visuel qui vérifie un moteur que personne ne livre est de la même
famille.

**Et le second argument affaiblit le premier** : docs/24 §4.6 écrit lui-même que « le coût caché, ce
n'est pas l'UI, c'est le backend Python ». Un sidecar Python empaqueté pèse le même poids quelle que
soit la coque. L'écart de 140 Mo est donc un écart **absolu** réel et un écart **relatif** bien plus
faible une fois le backend embarqué.

### 2.3 Le prix, et il est assumé

| | Electron | Tauri |
|---|---|---|
| Poids de la coque | ~150 Mo | ~10 Mo |
| Moteur de rendu | Chromium embarqué, **identique partout** | WebView du système, **trois moteurs** |
| Chaîne de construction | Node — **déjà** un prérequis (`.node-version`, `.tools/node/`) | Rust — **à ajouter** |
| Empreinte mémoire | plus élevée | plus faible |

⚠ **Les deux chiffres de poids sont repris de docs/24 et n'ont pas été re-mesurés** (§6). Le lot 2
les mesurera sur la coque réelle ; s'ils s'écartent, c'est l'arbitrage qu'il faudra relire, pas le
chiffre qu'il faudra corriger en silence.

Ce qu'on accepte : **un installeur plus lourd et une empreinte mémoire plus élevée**, contre un
moteur unique, une chaîne de construction déjà présente, et un filet visuel qui regarde ce qui est
livré. Sur un produit qui s'installe sur un poste de développement — le persona de docs/24 — ce
n'est pas le poids qui décide.

### 2.4 Les options écartées

- **Tauri** (D4) — écartée pour §2.2. Le ticket **#643 « Enveloppe Tauri » est abandonné** et
  remplacé par le lot 2 (#923). Il n'est pas fermé en silence : son abandon nomme ce document.
- **Rester au navigateur** — c'est l'option 0 de docs/24, toujours « insuffisante à terme ». Le retex
  y ajoute **G6** : le produit « parle le dépôt » à son utilisateur,
  `bash scripts/controltower/start.sh` affiché sur la page d'accueil.
- **PWA / raccourci de navigateur** — non traitée par docs/24, écartée ici : elle ne donne ni
  l'ouverture de l'explorateur de fichiers (lot 7, **G1**), ni le glisser-déposer avec chemin réel,
  ni le cycle de vie du backend. C'est-à-dire précisément les trois capacités qui justifient une
  fenêtre.
- **Réécriture native** — écartée par D4, et l'argument tient sans changement : elle jetterait les
  Phases 4 et 6.

### 2.5 Ce que la coque ne change pas

**D3 tient** : le bureau est une **enveloppe**, pas la finalité. Le mode web/serveur reste de premier
ordre — un backend distant reste servi par l'explorateur d'API (#223).

**ENF-12 tient** : *aucun embranchement de code applicatif*. Le front servi dans la fenêtre est celui
du mode web, les différences se limitant à des réglages. Le lot 2 en fait un critère opposable : pas
de `if (electron)` dans `apps/web/**`.

**L'ordre de D4 tient** : lanceur (#640), installeur (#641), *puis* enveloppe. Ce chantier livre une
coque **de développement** — elle sert la stack locale. L'empaquetage sans Python ni Node reste en
Phase 9. On n'empaquette pas une cible mouvante, et la disposition bouge ici.

---

## 3. La disposition à trois zones

### 3.1 Les trois zones

```
┌──────────┬────────────────────────────────┬──────────────┐
│          │  barre supérieure              │              │
│   NAV    ├────────────────────────────────┤ CONVERSATION │
│          │                                │              │
│ (gauche) │       LE TRAVAIL (centre)      │   (droite)   │
│          │                                │              │
└──────────┴────────────────────────────────┴──────────────┘
     ↑                    ↑                        ↑
  existe            existe (contenu)          zone neuve
```

La zone de gauche et la barre supérieure ne bougent pas : elles sont le shell de #117. Ce qui change
est qu'une **troisième zone** apparaît, et que le centre reçoit par défaut ce qui mérite d'être
regardé.

### 3.2 Ce qui va au centre : le run qui tourne

Quand un run est en cours, son **pipeline** occupe le centre dès l'arrivée — sans geste de
navigation. C'est le verdict du retex (§1.2) appliqué : la meilleure vue du produit cesse d'être à
deux clics.

`VuePipeline` **existe** et est **déjà** la vue par défaut d'un run (`lib/vuesRun`, #491). Le lot 6
la *remonte* ; il ne la réécrit pas. Les quatre lectures d'un run — pipeline, Kanban, frise, journal
— et leur ordre restent ce que #491 et #516 ont tranché.

⚠ **« Aucun run en cours » reste un état normal.** Le poste vide (`PosteVide`) et son renvoi vers le
fil ne sont pas à défaire : un écran qui ne sait dire que « ça tourne » ment la moitié du temps.

### 3.3 Ce qui va à droite : la conversation

Le fil devient **permanent et repliable**, disponible depuis n'importe quel écran. Trois propriétés
en font le livrable, et la troisième est celle qui coûte :

1. **Un seul composant, deux emplacements** — le fil du panneau et celui de `/chat` sont le même.
   Deux implémentations divergeraient, et c'est la panne que #365 a supprimée sur le cycle de vie :
   deux supports pour un seul fait.
2. **`/chat` reste servi**, et reste la **même** conversation — pas une seconde.
3. **Changer d'écran ne perd ni le fil, ni un message en cours de saisie.**

⚠ Le **composeur** est une surface déjà travaillée par quatre veilles (#724, #866, #873, #899) et
porte des décisions récentes — repli sous `sm` par le wrap (#918), `interactive-widget` (#919). Il se
**replace**, il ne se refait pas.

### 3.4 La frontière shell / écran — le point de vigilance

La **règle des trois places** ([docs/30 §4](./30-cible-visuelle-control-tower.md)) borne ce qu'un
**écran** peut occuper : bandeau de tête ≤ 4 chiffres, corps ≤ 3 blocs de plein format, **une**
colonne de propriétés sans plafond. Elle est comptée par `apps/web/tests/sobriete.test.tsx` sur les
dix écrans du menu.

Une zone du **shell** n'est pas un bloc de plus dans l'**écran**. Mais rien, aujourd'hui, ne porte
cette distinction dans le code — et #539 a écrit pourquoi elle doit y être portée :

> il n'y a **qu'une** colonne de propriétés par écran, faute de quoi la seule place sans plafond
> deviendrait la sortie de secours des deux autres.

Une troisième zone du shell rendue comme une `<aside>` serait exactement cette sortie de secours : un
écran plein pourrait y ranger son quatrième bloc. **Deux choses à ne pas défaire**, et le lot 8 en
fait son livrable :

- la frontière est portée par le **code**, jamais par une convention — une règle qu'aucune machine ne
  vérifie ne tient pas (docs/30 §3.6) ;
- on ne relève **jamais** `BLOCS_MAX` pour faire passer la refonte. Si le test rougit, c'est qu'il
  pose la bonne question.

---

## 4. Ce que ce chantier ne fait pas

Écrit ici pour que personne n'ait à le deviner :

- **Aucune identité visuelle nouvelle** — ni palette, ni police, ni arrondis de marque. Direction
  docs/30 §6.1, inchangée.
- **Aucun écran nouveau.** Le tableau de bord épuré de #191 tient ; ce qui est mis au centre
  *remplace*, il ne s'ajoute pas.
- **Pas d'installeur, pas de mises à jour, pas de premier lancement** : #641, #644 et #642 restent en
  Phase 9.
- **Pas de durcissement du mode local** (#638) ni de persistance SQLite (#639) : même phase.
- **Pas la moitié du retex.** Les constats hors des surfaces refondues — coquilles (C1-C4, C7),
  visite guidée (G7), registre de langue (C5, C6), format téléphone (G8), amorces du chat (G9),
  cadrage sans affordance (G10), racine de projet salie (G12) — et **G4/G5, déjà décrits dans #568**
  — partent en tickets libres. Les mêler ici ferait un jalon dont on ne saurait pas ce qu'il a
  bouclé.

---

## 5. Le découpage — 8 lots

| # | Lot | Ticket | ∥ |
|---|---|---|---|
| 1 | Cadrage écrit *(ce document)* | #922 | |
| 2 | Coque Electron — une fenêtre sert la stack locale | #923 | ∥ |
| 3 | Les comptes d'un run disent la même chose partout | #924 | ∥ |
| 4 | Le shell porte une troisième zone, **sans rien dedans** | #925 | ∥ |
| 5 | La conversation occupe la colonne de droite | #926 | |
| 6 | Le centre montre le run qui tourne | #927 | |
| 7 | Un run qui se termine l'annonce, et remet son livrable | #928 | |
| 8 | Tests + doc — dont **la frontière shell / écran** | #929 | |

**L'ordre porte deux décisions**, et aucune n'est un séquencement de confort :

- **le lot 3 avant le lot 6** — §1.2 ;
- **le lot 4 avant le lot 5** — la zone d'abord vide, puis son contenu. C'est ce qui rend le lot 4
  mergeable seul : une colonne repliée par défaut ne change aucun écran.

Les lots 2, 3 et 4 sont **indépendants** : ils ne se touchent pas. Le lot 2 l'est particulièrement —
ENF-12 fait de la coque un objet extérieur au front.

---

## 6. Ce qui n'a pas été vérifié

Par honnêteté de méthode, comme docs/30 §7 :

- **Les deux chiffres de poids** (~150 Mo / ~10 Mo) sont **repris de docs/24 §4.5** et n'ont été
  re-mesurés ni sur une coque réelle, ni sur ce front. Le lot 2 les mesure.
- **L'empreinte mémoire** d'Electron sur ce produit : non mesurée. Estimée d'après la nature du
  moteur, pas d'après un relevé.
- **Le coût d'un sidecar Python empaqueté** : jamais mesuré dans ce dépôt. L'argument de §2.2
  s'appuie sur l'affirmation de docs/24 §4.6, non sur un chiffre.
- **Le comportement de `sobriete.test.tsx` face à une troisième zone** : la lecture du fichier montre
  qu'il compte `section, aside` dans l'écran rendu, mais **le cas n'a pas été joué**. C'est le lot 4
  qui le saura, et §3.4 dit dans quel sens trancher s'il rougit.
- **Le coût de démarrage d'une stack en session de run** (chantier #930, lot #932) : inconnu. Il
  décide si la relecture visuelle se joue à chaque ticket d'interface ou sur demande.
