# 36 — Outillage du design : l'inventaire, et sur quelle preuve

> Lot 1/6 de **#930** — *Outillage du design : viser, regarder, tenir*.
> Mesures du **2026-09-11**, poste de référence, CLI 2.1.267.

[docs/30 §7](./30-cible-visuelle-control-tower.md) laissait une question ouverte depuis le
2026-08-25 — « **Le skill `dataviz` : non lisible depuis cette session** » — et personne ne l'avait
rouverte. Ce document la ferme, et fait le tour des autres.

## 0. La méthode, et ce qu'elle interdit

**Ce qui n'est pas vérifié n'est pas cité** (règle de #471, reprise par `/design-veille`). Un
inventaire qui liste des outils sans dire lequel a été *ouvert* ne vaut pas mieux que la question
ouverte qu'il prétend fermer — il la déplace. Chaque entrée ci-dessous porte donc, dans l'ordre :
un **verdict**, la **preuve** qui le fonde, et le **geste exact** qui la rejoue.

Deux conséquences assumées :

- **Ce qui n'a pas pu être ouvert est nommé comme non vérifié**, avec la raison — §6. Un « non
  vérifié » écrit vaut mieux qu'un verdict plausible : c'est le second qui a laissé la question de
  docs/30 §7 dormir jusqu'ici.
- **Un outil qui échoue à l'épreuve est un résultat**, pas une mesure ratée. L'échec de
  `mcp__chrome` (§3.2) est la moitié la plus instructive de cet inventaire.

⚠ **Aucune identité nouvelle.** La direction de [docs/30 §6.1](./30-cible-visuelle-control-tower.md)
est « le même produit, avec du relief ». Un outil qui pousse une palette, une police ou un parti
esthétique se juge sur ce critère **avant** tout autre — et c'est ce qui écarte une partie de
`artifact-design` (§3.3), dont la posture est explicitement l'inverse.

## 1. Ce que l'inventaire a trouvé en chemin : la couleur des données est orpheline

Le constat n'était pas dans le ticket. Il est sorti de l'épreuve de `dataviz`, et c'est lui qui
donne leur portée aux verdicts qui suivent.

**Mesuré sur l'écran rendu** (Control Tower en démo, `getComputedStyle` sur `/couts`) :

| Élément | Couleur rendue | Écrit où |
|---|---|---|
| Colonne du graphique d'évolution | `rgb(42, 120, 214)` = `#2a78d6` | `components/GraphiqueEvolutionCout.tsx:196` — `fill-[#2a78d6] dark:fill-[#3987e5]` |
| Barre de « Répartition par agent » | `rgb(42, 120, 214)` = `#2a78d6` | `app/couts/page.tsx` — `bg-[#2a78d6] dark:bg-[#3987e5]` |
| `--accent` | `#007a55` (vert) | `app/globals.css:153` |
| `--info` | `#0069a8` (bleu sky-700) | `app/globals.css:158` |

La couleur qui porte **toutes les données chiffrées du produit** n'est donc **aucun token** : ni
l'accent, qui est vert, ni `info`, qui est un autre bleu. Elle est écrite **deux fois à la main**,
dans deux fichiers, et les deux moitiés n'ont rien qui les tiendrait d'accord.

**Elle est pourtant bonne**, et c'est ce qui rend le défaut durable : passée au validateur de
`dataviz` sur les surfaces réelles du produit, elle passe **tous les contrôles dans les deux
thèmes** (`#2a78d6` sur `#ffffff`, `#3987e5` sur `#171717` — bande de clarté, plancher de chroma,
contraste ≥ 3:1). Rien ne rougit, rien ne se voit, et personne n'a de raison d'y toucher.

Le défaut n'est donc **pas sa valeur, c'est son statut** : hors palette, donc **hors de tout
filet** — `contraste.test.ts` lit les octets de `globals.css` et ne la voit pas ; `couleurs.test.ts`
la **compte** (13 paires pour le graphique, 19 pour `app/couts/page.tsx`, sur 678 au dépôt) mais ne
juge jamais une valeur ; `socle-visuel.test.tsx` juge les primitives.

⚠ **Le dépôt l'avait déjà nommé, en trois mots** : `couleurs.test.ts:390` porte, en commentaire de
la ligne du graphique, **`// manque : serie`**. Ce que cet inventaire ajoute n'est pas le constat,
c'est sa **mesure** — et le fait que le manque a désormais un outil en face (§4).

## 2. Ce qui garde déjà la couleur, et la question qu'aucun des quatre ne pose

Avant d'adopter quoi que ce soit, il faut savoir ce qui est déjà tenu — sans quoi on rachète un
filet qu'on a.

| Filet | Ce qu'il juge | Ce qu'il ne juge pas |
|---|---|---|
| `apps/web/tests/contraste.test.ts` (#534) | le **contraste WCAG** des tokens, 98 paires, 2 thèmes | tout ce qui n'est pas dans `globals.css` |
| `apps/web/tests/couleurs.test.ts` (#895) | l'**emploi** : toute paire brute `x` + `dark:x`, compte exact, 678 | la **valeur** de ce qu'il compte |
| `apps/web/tests/a11y.test.tsx` + `axe.ts` (#537) | `axe-core` 4.12.1 sur **10 écrans**, verdict `serious`/`critical` | la couleur (jsdom n'en calcule aucune) |
| `apps/web/tests/socle-visuel.test.tsx` (#245) | les promesses des **primitives** | les écrans |

**Aucun des quatre ne mesure la séparation perceptuelle entre deux teintes.** C'est une question
distincte du contraste — le contraste demande « peut-on lire ceci sur ce fond ? », la séparation
demande « peut-on distinguer ces deux séries l'une de l'autre, y compris sans voir toutes les
couleurs ? ». Le produit n'ayant aujourd'hui aucun graphe multi-séries, le trou n'a encore rien
coûté. Il se paie au premier.

## 3. L'inventaire

### 3.1 `dataviz` (skill du harnais) — **ADOPTÉ comme référence et comme validateur**

**La question de docs/30 §7 est fermée : il est lisible.** Ouvert le 2026-09-11 depuis cette
session, lu en entier, et — ce qui compte davantage — **exécuté**.

Il porte un validateur autonome, `scripts/validate_palette.js` : du Node sans dépendance, hors
ligne, qui rend six contrôles (bande de clarté, plancher de chroma, séparation CVD deutan/tritan en
ΔE OKLab, plancher de vision normale, contraste sur la surface). Il prend la **vraie** surface en
argument, donc il juge le produit et non un décor.

**Ce qu'il a mesuré sur le produit**, tons d'état pris comme palette de séries
(`accent`, `info`, `attention`, `alerte`, `provenance`), `--pairs all` :

| Régime | Verdict | Ce qui tombe |
|---|---|---|
| Clair, surface `#ffffff`, 5 tons | **FAIL** | `alerte`↔`attention` : ΔE **10,4** en vision normale (plancher 15), **4,4** en deutan |
| Sombre, surface `#171717`, 5 tons | **FAIL ×2** | 4 tons sur 5 hors bande de clarté ; `alerte`↔`accent` à ΔE **2,3** en deutan |
| Clair, 4 tons (sans `attention`) | **PASS** | mais WARN CVD 6,9 — légal *seulement* avec encodage secondaire |

**Lecture.** Ceci ne condamne **pas** l'usage actuel des tons d'état : le produit porte l'état par
**forme + couleur, jamais par la couleur seule** (docs/30 §6.1 p. 3), et un badge qui dit son état
en toutes lettres ne dépend d'aucun ΔE. Ce que la mesure condamne est l'idée — la seule qui se
présenterait naturellement le jour d'un graphe multi-séries — de **réemployer ces tons comme
séries**, là où la couleur porterait l'identité seule. Le produit **n'a pas de palette
catégorielle**, et ses tons d'état n'en tiennent pas lieu : c'est mesuré, dans les deux thèmes, et
le sombre est le pire des deux.

**Ce qu'il ne nous apprend pas**, et il faut le dire : sa méthode rejoint sur plusieurs points ce
que le graphique fait **déjà** bien — série unique sans légende (le titre du panneau nomme la
mesure), table de données dépliable sous le dessin, `tabular-nums`, infobulle gagnée au survol
**et** au clavier, axe du temps rendu honnête par le comblement des seaux vides. `dataviz` valide
l'existant plus qu'il ne le corrige.

**Ce qu'il apporte réellement**, et que rien au dépôt ne porte : *la question du §2*, sous forme
exécutable.

- **Verdict** : adopté — comme **référence de méthode** pour toute nouvelle représentation de
  données, et comme **validateur** à jouer sur toute palette de séries avant de l'écrire.
- **Preuve** : les trois régimes du tableau ci-dessus, rejouables.
- **Le geste** :

  ```bash
  node "<base du skill>/scripts/validate_palette.js" "#hex,#hex,…" \
       --mode light --surface "#ffffff" --pairs all
  ```

- **Sa palette par défaut ne nous concerne pas** : le skill est écrit pour qu'on lui substitue
  celle du produit (« *swap that file's values for your brand's* »). C'est ce qui le rend
  compatible avec les tokens plutôt que concurrent — et conforme à « aucune identité nouvelle ».

### 3.2 Chrome DevTools MCP (`mcp__chrome`) — **PAS ADOPTÉ en l'état ; l'échec est la preuve**

Le ticket demandait de vérifier d'abord s'il est déclaré. **Il ne l'est pas.**

- `.mcp.json` du dépôt déclare **`chrome-maestro`** et **`figma-officiel`**, et rien d'autre.
- `claude mcp get chrome` rend : **`Scope: User config (available in all your projects)`**,
  `npx -y chrome-devtools-mcp@latest --viewport 1440x900 …`, **✔ Connected**.

Il est donc joignable **sur ce poste et nulle part ailleurs** : absent de tout autre clone, et
absent des deux allowlists d'une session de run. Trois choses s'ensuivent, et chacune est une
décision, pas un détail :

1. **Version non épinglée.** `@latest` à chaque lancement, quand le dépôt épingle son Node
   (`.node-version`) et relance `@playwright/mcp` avec **ce** Node-là précisément pour que le poste
   ne décide pas (`scripts/mcp/playwright-mcp.mjs`). Adopter cet outil sans l'épingler
   réintroduirait, dans l'outillage visuel, ce que le dépôt a retiré du reste.
2. **Un second navigateur piloté, avec son propre profil et son propre verrou** —
   `<cache utilisateur>/chrome-devtools-mcp/chrome-profile`. C'est exactement le piège que
   `CLAUDE.md` documente déjà pour `chrome-maestro` (un seul consommateur par `--user-data-dir`),
   mais **en double** : deux profils, deux verrous, aucun des deux ne sachant que l'autre existe.
3. **Il a échoué à l'épreuve, et ne s'est pas repris.** Premier appel (`new_page` sur
   `http://localhost:3000/couts`) → `Protocol error (Browser.setContentsSize): Restore window to
   normal state before setting content size`. Tout appel suivant, `list_pages` compris →
   `The browser is already running for …chrome-profile. Use --isolated to run multiple browser
   instances.` Un `lockfile` de 0 octet est apparu dans le profil à l'horodatage de l'essai.
   **La reprise n'a pas été tentée** : elle demandait soit de tuer un processus Chrome au jugé —
   ce que le dépôt s'interdit partout ailleurs (« rien n'est tué au jugé », #213) et qui risquait
   le navigateur de l'utilisateur —, soit d'effacer le verrou d'un profil **hors dépôt**. Aucun
   des deux ne se fait pour un inventaire.

**Et le candidat intéressant n'est pas celui que le ticket nommait.** `lighthouse_audit` annonce
lui-même « **This excludes performance** ». Or sa catégorie *accessibilité* **est** `axe-core` — le
moteur que le dépôt branche déjà (4.12.1, 10 écrans), et qu'il branche **plus finement** : `axe.ts`
tranche par **impact** (`serious`/`critical`), ce qu'un score Lighthouse ne sait pas faire. Sur
l'accessibilité, Lighthouse rendrait donc un chiffre moins utile que le verdict qu'on a. Restent le
SEO — **sans objet** pour une application locale sans index — et les *best practices*.

Ce qui manque vraiment au dépôt, côté navigateur, est la **performance** — et elle vit dans
`performance_start_trace`, pas dans `lighthouse_audit`. Le ticket visait le mauvais outil du bon
serveur.

- **Verdict** : pas adopté en l'état. L'ajouter à `.mcp.json` est **une décision** — elle suppose
  au minimum une version épinglée et `--isolated`, et elle met un second navigateur piloté sur le
  poste. Elle a son ticket (§5, **C**).
- **Non vérifié, et nommé comme tel** : ce qu'un rapport Lighthouse dit **réellement** de la
  Control Tower. Voir §6.

### 3.3 `artifact-design` (skill du harnais) — **ÉCARTÉ comme outil ; une ligne retenue**

Ouvert et confronté au dépôt, point par point. Ses fondamentaux transposables sont **déjà tenus**,
et mieux gardés qu'il ne les formule :

| Ce qu'il prescrit | Où le dépôt le tient déjà |
|---|---|
| « Honor what's already there » — le système du projet avant ses propres choix | la règle même de docs/30 §6.1 |
| « Not everything is a card » | docs/30 §3.6, et les 18 recopies de carte reprises au lot 3 |
| « Encode state in form as well as number » | docs/30 §6.1 p. 3 — état par forme **et** couleur |
| « Semantic color … is separate from the accent hue » | **déjà tranché, et mieux** — voir ci-dessous |

Le dernier point mérite d'être détaillé, parce qu'il ressemble à une trouvaille et n'en est pas
une. `--accent` et `--positif` sont **la même valeur** dans les deux thèmes (`#007a55` en clair,
`#00bc7d` en sombre), ce que le skill nommerait comme une faute. `globals.css` l'a écrit avant lui,
avec la nuance qui manque au skill :

> « `accent` et `positif` partagent la famille : ce sont deux **rôles** — une action, un succès —,
> donc deux tokens, même si leurs valeurs coïncident aujourd'hui. Les dissocier plus tard ne
> coûtera alors qu'une valeur. »

La structure est juste ; seule la valeur coïncide. Le skill n'a pas cette distinction.

**Ce qui reste est spécifique aux artifacts publiés** et ne se transpose pas : CSP et hôtes de
scripts autorisés, `cdnjs`, Google Fonts, `window.claude`, le `<title>` de galerie, la boucle
« write, look once, publish ».

⚠ **Et il porte un risque qu'il faut nommer.** Sa posture est « *design lead at a small studio…
giving every client a visual identity* », « *take one real aesthetic risk* » — l'**inverse exact**
de la direction du dépôt. Le charger dans une session qui travaille la Control Tower, c'est y
introduire une pression vers l'identité nouvelle, précisément ce que docs/30 §6.1 refuse.

- **Verdict** : écarté comme outil de travail sur la Control Tower.
- **La ligne retenue** : « *Draw charts to the scale — chart text takes its color from the theme
  tokens so it reads in both themes* ». C'est vrai du produit **et non tenu** : le texte du
  graphique est en `fill-neutral-500 dark:fill-neutral-400`, écrit à la main. Cette ligne rejoint
  §3.4 et le ticket **A** du §5.

### 3.4 `artifact-diagramming` (skill du harnais) — **ÉCARTÉ comme outil ; une technique retenue**

Court, et plus utile que le précédent parce qu'il parle de SVG écrit à la main — ce que le produit
fait dans ses deux surfaces graphiques.

**La technique qui vaut la lecture** : « *Theme with `currentColor`* ». Un SVG dont les traits et le
texte sont en `currentColor` hérite du premier plan de la page et **n'a aucun `dark:` à écrire**.
C'est le remède exact au résidu du §1.

**Mesure** — et elle est plus intéressante que la technique :

| Fichier | `currentColor` |
|---|---|
| `components/Icones.tsx` | 3 |
| `components/Logo.tsx`, `Primitives.tsx`, `BarreLaterale.tsx` | présent |
| `components/GraphiqueEvolutionCout.tsx` | **0** |
| `components/runs/VuePipeline.tsx` | **0** |

Le dépôt **connaît et emploie** `currentColor` — mais dans aucun de ses deux SVG **porteurs de
données**, c'est-à-dire précisément les deux fichiers où les `dark:` écrits à la main s'accumulent.
Le skill n'apporte donc pas une technique inconnue : il **désigne un endroit où le dépôt n'applique
pas sa propre pratique**.

Sur le reste il est en retrait : son « `role="img"` + `aria-label` » est ce que #537 a
explicitement **écarté** pour le graphique, avec sa raison ARIA (les descendants d'un `role="img"`
sont présentationnels par la spécification, or le graphe contient des cibles atteignables au
clavier — d'où `role="group"`). Et il est écrit pour des diagrammes **statiques**, pas pour des
composants React pilotés par la donnée.

- **Verdict** : écarté comme outil ; `currentColor` retenu comme technique, à appliquer dans le
  ticket **A** du §5.

### 3.5 Figma (`mcp__figma-officiel`) — **le verdict de docs/30 §6.2 TIENT, vérifié**

Le ticket demandait de vérifier si le compte a changé. **Il n'a pas changé.** `whoami`, en direct
le 2026-09-11 :

```text
handle : maestro
plan   : « L'équipe de Maestro Automate » · tier: starter · seat: View
```

Trois conséquences, aucune déduite de mémoire : **`starter`** ⇒ pas d'organisation, donc aucune
bibliothèque d'organisation ; **siège `View`** ⇒ aucune édition ; **Code Connect** exige
Organization/Enterprise ⇒ toujours refusé.

- **Verdict** : inchangé — écarté **comme source** du design system, gardé comme outil
  d'exploration. C'est la citation de docs/30 §6.2, désormais **datée et vérifiée** plutôt que
  recopiée.
- **Ce que ça implique**, et docs/30 §5 le disait déjà : rien ne relie une cible Figma au code,
  donc **ce qui tient le niveau sur tous les écrans est un test, pas une maquette**. C'est la
  raison pour laquelle l'inventaire penche partout vers des filets exécutables.

### 3.6 `chrome-maestro` (`.mcp.json`, déjà retenu) — **confirmé par l'usage**

Il n'était pas dans la liste des candidats — il est l'outil **en place**, et les lots suivants de
#930 s'appuient dessus. Il a donc été éprouvé au passage, et il répond : navigation, capture pleine
page, `browser_evaluate` (c'est lui qui a rendu les mesures du §1), fermeture propre.

Deux choses constatées ici, et utiles au lot 2 (#932) :

- **Ses racines d'écriture sont celles du répertoire courant.** Depuis un worktree, il écrit dans
  ce worktree — un chemin absolu hors racines est refusé (« *outside allowed roots* »), et la
  **casse du lecteur compte** (`e:/…` refusé, `E:/…` accepté). Un nom **relatif** est le geste sûr.
- **La démo d'un worktree rend « Choisir le projet »**, pas le tableau de bord : le scénario
  estampille tout du projet `prj-demo` (`maestro/controltower/demo.py`) et le dépôt de projets d'un
  clone neuf est vide. Les deux moitiés sont indissociables — déclarer `core/projets/prj-demo.json`
  (gitignoré) **et** poser `maestro.projet.actif` dans le `localStorage`, exactement ce que fait
  `scripts/presentation/captures.sh`.

## 4. Ce qu'on adopte maintenant

**Trois décisions, qui ne demandent pas de code** — elles sont prises par ce document :

1. **`dataviz` est la référence pour toute représentation de données**, et son validateur est le
   geste à jouer **avant** d'écrire une palette de séries — sur la surface réelle du produit, pas
   sur la sienne. Sa palette par défaut n'est jamais reprise : le skill est conçu pour qu'on lui
   substitue celle du produit, et « aucune identité nouvelle » l'exige.
2. **Les tons d'état ne serviront pas de palette de séries** — c'est mesuré (§3.1), dans les deux
   thèmes, et le sombre est le pire des deux. Le jour où un graphe multi-séries arrive, il lui faut
   une palette **catégorielle** à lui.
3. **`artifact-design` et `artifact-diagramming` ne sont pas des outils de ce dépôt.** Ce qu'on en
   retient tient en une ligne chacun, reprise au ticket **A** : le texte d'un graphe porte les
   tokens du thème ; un SVG se thème par `currentColor`.

## 5. Ce qui reste un ticket

Nommés, non créés — ce lot rend un inventaire, pas un backlog.

| # | Ce qu'il fait | Fondé sur |
|---|---|---|
| **A** | **Un token `--serie`** (deux thèmes, validé) et les deux emplois de `#2a78d6` repliés dessus ; le texte et les grilles du graphique passés aux tokens et à `currentColor`. Fait tomber 13 + 19 paires du résidu. | §1, §3.3, §3.4 — et `couleurs.test.ts:390`, `// manque : serie` |
| **B** | **Le contrôle de séparation** que le §2 identifie comme absent : la règle de `dataviz` portée dans la suite du dépôt, à côté de `contraste.test.ts`, pour que la question se pose sans qu'on y pense. | §2, §3.1 |
| **C** | **Arbitrer `mcp__chrome`** : l'ajouter à `.mcp.json` avec une **version épinglée** et `--isolated`, ou l'écarter par écrit. Le vrai objet est `performance_start_trace`, pas `lighthouse_audit`. | §3.2 |

Le ticket **A** est le seul des trois qui se voit à l'écran ; **B** est celui qui évite qu'il se
refasse ; **C** est une décision d'outillage, indépendante des deux autres.

## 6. Ce qui n'a pas été vérifié

Par honnêteté de méthode, et pour que le prochain tour sache où reprendre :

- **Ce qu'un rapport Lighthouse dit réellement de la Control Tower.** L'outil s'est bloqué lui-même
  au premier appel (§3.2) et la reprise aurait demandé un geste que le dépôt s'interdit. Ce qui est
  établi est son **régime** (non déclaré, non épinglé, second profil, second verrou) et le
  **recouvrement** de sa catégorie accessibilité avec `axe-core` — pas le contenu de son rapport.
- **`performance_start_trace`** n'a pas été essayé du tout : il est derrière le même verrou.
- **Les écrans mesurés l'ont été en mode démo**, donc partiellement peuplés — un seul seau dans la
  série d'évolution. Les constats du §1 portent sur des **valeurs de couleur**, que le peuplement
  ne change pas ; ils ne portent ni sur la densité, ni sur les collisions d'étiquettes.
- **Aucun graphe multi-séries n'existe** aujourd'hui dans le produit : tout ce que le §3.1 dit de
  la palette catégorielle est une mesure sur des tons **pris comme s'ils** en étaient une, pas
  l'observation d'un défaut à l'écran.
