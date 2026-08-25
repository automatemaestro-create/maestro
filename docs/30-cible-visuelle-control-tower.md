# 30 — La cible visuelle de la Control Tower, et l'outillage qui la tient

> Version 0.1 — recherche du ticket **#471**, mesures du **2026-08-25**.
> **Cette note ne modifie aucun écran.** Elle rend une recommandation instruite et le découpage
> qui en découle. Toute retouche visuelle sort de son périmètre.

Origine : la revue d'usage du 2026-08-05 relevait un rendu « brouillon » qui revient écran après
écran — c'est ce qui a lancé la vague « Control Tower v3 » et son premier lot, le langage visuel
(#245, [docs/06 §Vague front](./06-roadmap.md)). Un an de lots plus tard la demande revient plus
large : non plus « harmoniser ce qu'on a » mais **aller chercher un niveau**, et le tenir **partout
de la même façon**.

**Tout ce qui est chiffré ici a été mesuré**, pas estimé : les comptages viennent de `ripgrep` sur
le dépôt, les contrastes d'une sonde jouée dans un vrai navigateur sur la stack montée en local, et
les verdicts d'outillage d'appels réels dont la réponse est citée. Là où une mesure n'a pas pu être
prise, c'est écrit.

---

## 0. Ce que la recherche a renversé

Trois prémisses du ticket sont **fausses**, et la recommandation en dépend :

| Prémisse #471 | Ce que la mesure dit |
|---|---|
| « `/figma-code-connect` … exactement la mécanique du même niveau partout » | **Code Connect est refusé sur ce compte.** Réponse du serveur : *« You need a Dev or Full seat on an Organization or Enterprise plan to use Code Connect. »* Le plan est `starter`, le siège `View`. |
| « Skill `design` — canvas multi-artboards » | **Ce skill n'existe pas**, ni dans `.claude/skills/`, ni dans les skills de la session. |
| « le tableau de bord … six panneaux : briefs, validations, runs interrompus, indicateurs, **Kanban**, activité » | Le **nombre** est bon, la **liste** non : le Kanban a quitté le tableau de bord il y a moins de 48 h (#476, commit `93a8099`), remplacé par `EtatDesRuns`. `Kanban.tsx` n'est plus importé par aucun fichier de `app/`. |

Et une quatrième, qui n'était pas dans le ticket mais qui commande le chantier : **le sujet n'est
pas l'esthétique**. Les captures de §1 le montrent — la Control Tower n'est pas laide. Elle est
**plate** : tout y a le même poids. Le défaut mesurable n'est pas un manque de goût, c'est un
manque de **hiérarchie** et un **socle non tenu** (§2).

---

## 1. Banc de références

Quatre produits comparables, **capturés en direct le 2026-08-25** dans un vrai navigateur — aucune
image marketing, aucune description de seconde main.

⚠ **Deux candidats ont été écartés faute de preuve** : **Temporal** (l'orchestrateur le plus proche
fonctionnellement) et **Langfuse** (déjà dans la stack Maestro) n'exposent aucune UI publique
capturable — `cloud.langfuse.com` redirige vers l'authentification, `temporal.io/product` ne publie
aucune capture de son interface. Les citer sur mémoire aurait été précisément le « listé de
confiance » que le ticket refuse.

### 1.1 GitHub Actions — la liste de runs

![Liste de runs GitHub Actions](./assets/471/ref-github-actions.png)

C'est **le pendant exact de `/runs`**, et le plus instructif du banc parce que c'est l'outil que le
projet utilise déjà.

**Ce qu'on lui prend :**
- **L'état porte une forme, pas seulement une couleur** — ✓ plein, ◉ cerclé, ⊘ barré, ! triangle.
  Un daltonien lit la liste. Nos `BadgeEtat` se distinguent aujourd'hui par la **teinte seule**.
- **Le rythme à deux lignes** : titre en gras, puis une ligne grise qui porte *tout* le reste
  (workflow, numéro, auteur). Aucun bloc, aucune bordure entre les runs — la séparation est un
  simple filet et de l'espace.
- **Les métadonnées vont à droite, alignées en colonnes** (déclenchement, durée). L'œil descend une
  colonne au lieu de relire chaque ligne.
- **Les filtres sont une barre d'en-tête de liste**, pas un panneau.

**Ce qu'on lui laisse :** le double niveau de navigation (nav du dépôt + nav des workflows) — nous
avons déjà une barre latérale, en ajouter une seconde rejouerait la densité que #191 a retirée.

### 1.2 GitHub Actions — le détail d'un run

![Détail d'un run GitHub Actions](./assets/471/ref-actions-run.png)

Le pendant de **`VuePipeline`** (978 lignes, notre plus gros composant).

**Ce qu'on lui prend :**
- **L'en-tête de run est une ligne de faits**, pas des cartes : `Déclenché par … · Statut · Durée ·
  Artefacts`. Quatre libellés gris, quatre valeurs sous eux. Nous en faisons des `TuileChiffre`.
- **La liste des jobs à gauche est la table des matières et la barre de progression à la fois** —
  un seul objet répond à « où en est-on ? » et « où aller ? ».
- **Le graphe est offert, pas imposé** : il est *sous* le résumé. Notre onglet Pipeline ouvre
  d'emblée sur le graphe.

**Ce qu'on lui laisse :** le graphe illisible à cette taille (visible sur la capture) — la preuve
qu'un DAG de plus de ~20 nœuds n'est pas une vue de premier niveau.

### 1.3 Grafana — la densité de données

![Tableau de bord Grafana](./assets/471/ref-grafana.png)

**Ce qu'on lui prend :**
- **La hiérarchie typographique est franche** : titre de page à ~30 px contre un corps à 14 px.
  Notre plus grand titre courant est à **16 px** (`text-titre`… qui n'est employé **nulle part**),
  et 268 usages du produit tiennent sur **un seul pas** (0,75 rem).
- **Le panneau est l'unité de composition** : un titre, un corps, une bordure discrète, la même
  partout. C'est ce que `Carte` veut être et que 18 recopies contournent (§2.2).
- **La barre d'actions est contextuelle au contenu** (plage de temps, rafraîchir, partager), placée
  au-dessus du contenu et non dans le châssis global.

**Ce qu'on lui laisse :** la barre latérale à 10 entrées dépliables et la densité de chrome — c'est
un outil d'exploration, la Control Tower est un poste de surveillance.

### 1.4 Linear — la sobriété, et l'agent comme citoyen

![Linear](./assets/471/ref-linear.png)

**Ce qu'on lui prend :**
- **Trois colonnes à rôles fixes** : navigation / contenu / propriétés. Les métadonnées d'un objet
  vivent dans une colonne dédiée, jamais mêlées au contenu. C'est la réponse structurelle à notre
  problème de sobriété (§4) : *un écran ne grossit pas, sa colonne de propriétés s'allonge*.
- **Le panneau d'agent est flottant et rétractable**, avec son modèle affiché (`Opus 5`) et son état
  en clair (« Thinking… »). Notre `AssistantFlottant` a la bonne forme — il lui manque de dire quel
  modèle répond et ce qu'il fait.
- **Le contraste est assumé** : texte quasi blanc sur fond quasi noir, gris réservé au secondaire.

**Ce qu'on lui laisse :** la typographie de marque à 64 px (c'est une page d'accueil) et le parti
pris tout-sombre — nous devons tenir **deux** thèmes.

### 1.5 Cursor — la file de travaux d'agents

![Cursor](./assets/471/ref-cursor.png)

La référence la plus directe pour ce que Maestro **est** : plusieurs agents qui travaillent, dont
on suit l'avancement.

**Ce qu'on lui prend :**
- **La file est groupée par état d'attente humaine** : « EN COURS 1 » / « **PRÊT POUR REVUE 4** ».
  Le second groupe est un appel à l'action, pas un statut. C'est exactement notre file de
  validations — mais chez nous elle est un panneau du tableau de bord, pas la colonne de gauche.
- **Chaque ligne porte son coût** : durée (`10m`, `45m`) et **delta de lignes** (`+135 -21`). Deux
  chiffres qui disent l'ampleur d'un travail sans l'ouvrir.
- **L'état de l'agent est une phrase**, pas un badge : « Terminé. Les polices sont préchargées… ».

**Ce qu'on lui laisse :** l'esthétique de marque (fond crème, image d'illustration) et la fenêtre
CLI superposée.

### 1.6 Ce que le banc dit, en une ligne

Les quatre convergent sur trois choses que nous n'avons pas : **une hiérarchie typographique
franche**, **l'état porté par la forme autant que par la couleur**, et **une place fixe pour les
métadonnées** au lieu de blocs qui s'ajoutent.

Aucun des quatre ne doit son niveau à une identité graphique forte. **Il n'y a pas de style à aller
chercher** — il y a un socle à tenir.

---

## 2. L'état actuel, mesuré

### 2.1 Ce qui est déjà bon, et qu'il ne faut pas défaire

Le socle de #245 **existe et tient** :

| Mesure | Valeur |
|---|---|
| `aria-label` | **104** occurrences sur **48** fichiers — le nommage est fait, et bien fait |
| Icônes | **665 lignes** dans `Icones.tsx`, toutes en `currentColor`, toutes `aria-hidden` |
| Rôles ARIA | **44** occurrences, 12 valeurs distinctes, toutes correctes |
| `<h1>` par écran | **1**, sur les **10** écrans mesurés, **0 saut de niveau** |
| Échappement du focus | `Escape` **géré dans les 7** surfaces flottantes (3 modales, 4 menus) |
| Restauration du focus | **7/7** — y compris déléguée à l'appelant pour `PanneauDetailTache` |
| Tests | **583 cas** sur 31 fichiers, dont `socle-visuel.test.tsx` qui garde les deux thèmes |

**Le travail d'accessibilité déjà fait est sérieux.** Ce qui manque n'est pas de la rigueur, c'est
un **filet** : rien ne garde ces acquis, et le trou est ailleurs (§3).

### 2.2 Le socle est contourné plus souvent qu'il n'est utilisé

`Primitives.tsx` exporte 6 briques, importées par 30 fichiers. Mais :

| Rôle | Primitive | Contournement mesuré |
|---|---|---|
| Carte | `Carte` (4 tons × 4 densités) | **18 recopies** littérales de `rounded-lg border border-neutral-200 bg-white` dans **12 fichiers** |
| Bouton | **aucune** | **92 `<button>`** dans **36 fichiers** ; **26 fichiers** redéfinissent leur bouton plein |
| Modale | **aucune** | 3 surfaces flottantes refaites à la main |
| Champ | **aucune** | `app/journal/page.tsx` déclare ses propres `CLASSE_CHAMP` / `CLASSE_LIBELLE` |

**Il n'y a ni `components/ui/` ni `components/common/`** — les 61 composants sont rangés par domaine
métier. La primitive manquante la plus coûteuse est le **bouton** : c'est elle qui porte le
contraste fautif de §3.2, dans 26 endroits à corriger un par un.

### 2.3 La dispersion visuelle, en nombres

| Propriété | Variantes distinctes | Détail |
|---|---|---|
| Rayon | **5** | `rounded-md` 99 · `rounded-full` 38 · `rounded-lg` 27 · `rounded-t-md` 2 · `rounded-xl` 1 |
| Ombre | **6** | `shadow-sm` 28 · `shadow-lg` 5 · `shadow-2xl` 3 · `shadow` 3 · `shadow-md` 1 · 1 en ligne |
| Padding de conteneur | **8** | `p-4` 14 · `p-3` 13 · `p-2` 9 · `p-1.5` 3 · `p-1` 2 · `p-2.5` · `p-5` · `p-0.5` |
| Padding de contrôle | **8 paires** | `px-3 py-1.5` 44 · `px-3 py-2` 31 · `px-2 py-1` 15 · … |
| Taille de police | **13** | 6 Tailwind (255) + 4 tokens (176) + 3 valeurs arbitraires (8) |

`Carte` impose `rounded-lg` — c'est le **3ᵉ** rayon par fréquence, employé 3,7 × moins que
`rounded-md`. Elle nomme 3 densités : **5 des 8 paddings sont hors barème**.

**Le pire est la typographie** : trois tailles sont rendues par **deux classes chacune** —
`text-xs` (158) *et* `text-annexe` (110) valent 0,75 rem ; `text-sm` (90) *et* `text-corps` (50)
valent 0,875 rem. Et `text-titre` (1 rem), déclaré, n'est employé **nulle part**. D'où le rendu
plat du §1.3 : **408 des 439 usages typographiques (93 %) tiennent sur deux pas** — 0,75 rem et
0,875 rem —, sans titre intermédiaire.

### 2.4 Les couleurs ne sont pas tokenisées

**1 750 occurrences** de classes Tailwind brutes ; **0 occurrence** de classe sémantique
(`bg-surface`, `text-muted`…). Les deux seuls tokens de couleur (`--background`, `--foreground`) ne
sont consommés que par la règle `body`.

Conséquence directe : **542 lignes portant un `dark:`** sur 59 fichiers — chaque couleur est écrite
deux fois, à la main, partout où la primitive n'est pas utilisée. **C'est le multiplicateur de coût
de toute la refonte** : changer une couleur, aujourd'hui, c'est éditer deux valeurs dans N fichiers.

---

## 3. Cible d'accessibilité

### 3.1 Méthode

Une sonde a été écrite et jouée **dans le navigateur, sur la stack montée en local** (mode démo,
ports 8071/3071), sur **10 écrans × 2 thèmes**. Elle calcule le contraste par **lecture de pixel sur
un canvas** — Tailwind v4 émet ses couleurs en `oklch()`, qu'aucun parseur `rgb()` naïf ne lit ; une
première version a rendu des ratios faux, corrigée avant d'être exploitée. Chaque classe est
**validée contre un témoin** avant d'être comptée : trois classes (`text-neutral-300`,
`text-sky-600`, `bg-neutral-800`) **n'existent pas nues dans le CSS généré** (elles ne sont écrites
qu'en variante `dark:`) et sont déclarées non mesurables plutôt que comptées à tort.

### 3.2 Contraste — 10 paires fautives sur 19 mesurées

| Paire | Ratio | AA texte (4,5) | Poids dans le code |
|---|---:|:---:|---|
| `text-neutral-400` / `bg-neutral-100` | **2,37** | ✗ | — |
| `text-neutral-400` / `bg-neutral-50` | **2,48** | ✗ | — |
| **`text-neutral-400` / `bg-white`** | **2,58** | ✗ | **230 occurrences — la classe n°1 du produit** |
| `text-neutral-600` / fond sombre `#0a0a0a` | **2,53** | ✗ | libellés de la barre latérale, **tous les écrans** |
| `text-white` / `bg-amber-600` | **3,20** | ✗ | bouton « attention » |
| **`text-white` / `bg-emerald-600`** | **3,65** | ✗ | **bouton d'action primaire, 18 fichiers** |
| `text-neutral-500` / `bg-neutral-900` | **3,78** | ✗ | secondaire en thème sombre |
| `text-white` / `bg-sky-600` | **4,02** | ✗ | bouton « info » |
| `text-neutral-500` / `bg-neutral-100` | **4,35** | ✗ | secondaire sur fond gris |
| `text-rose-600` / `bg-white` | 4,53 | ✓ *de justesse* | erreurs |

Les seules paires confortables sont `neutral-600/700/900` sur clair et `neutral-100` sur sombre.

**Trois faits à retenir :**
1. `text-neutral-400` **échoue même le seuil 3:1** — celui du texte *large*. C'est la couleur de
   texte la plus employée du produit.
2. Le **bouton d'action primaire** échoue dans les **deux** thèmes (3,65 des deux côtés).
3. En thème sombre, le **libellé de navigation** est à 2,53 — présent sur chaque écran.

⚠ Ces chiffres sont un **plancher** : les écrans étaient peu peuplés (le scénario de démo n'est pas
rattaché à un projet), donc une partie du texte `neutral-400`/`500` n'était pas rendue.

### 3.3 Le trou principal : `aria-live` = 0 sur les 10 écrans

Le ticket annonçait « 1 ». La mesure sur écran est **plus dure** : l'unique `aria-live` du dépôt est
dans `AssistantFlottant.tsx:170`, sur le fil de l'assistant — **il n'est déployé sur aucun écran au
repos**. Résultat : **0 région live sur les 10 écrans mesurés, dans les deux thèmes.**

Or la Control Tower reçoit ses mises à jour **sans action de l'utilisateur** : jusqu'à **3
WebSockets simultanées** sur `/couts`, coalescées à 150 ms, plus une horloge à 30 s qui rafraîchit
tous les horodatages relatifs. Les tâches changent de colonne, les coûts montent, les validations
arrivent — **rien n'est annoncé**. Aucun `aria-label`, si soigné soit-il, ne compense ça.

### 3.4 Le reste

| Point | Mesure | Verdict |
|---|---|---|
| `prefers-reduced-motion` / `motion-reduce` | **0 / 0** pour 19 lignes de `transition`/`duration` et 4 `animate-pulse` | à corriger, coût faible |
| Piège de focus | **0** — la chaîne `"Tab"` n'apparaît **nulle part** dans `apps/web` | 3 modales sans piège |
| Navigation au clavier dans les menus | **0** `ArrowUp`/`ArrowDown`/`Home`/`End` dans les 4 menus ; `aria-activedescendant` : 0 | 4 menus non conformes au motif `menu` qu'ils déclarent |
| Lien d'évitement | **0** | 10 entrées de nav à traverser sur chaque écran |
| Motif onglets ARIA | **0** `role="tab"` — les 4 barres d'onglets sont des `<nav>` de liens | acceptable (ce sont de vraies navigations), à assumer par écrit |
| Infobulles | `title=` natif, **42 occurrences / 23 fichiers** ; `role="tooltip"` : 0 | inaccessible au clavier et au tactile |
| Cibles < 24 px (WCAG 2.2, 2.5.8) | quelques liens de renvoi à **22 px** de haut | écart de 2 px, correction triviale |
| Lint `jsx-a11y` | **6 règles sur ~36, toutes en `warn`** — celles de `next/core-web-vitals`, jamais le preset `recommended` | ne garde presque rien |
| Test a11y automatisé | **0** — `axe-core` 4.12.1 est présent **en transitif** (via `eslint-plugin-jsx-a11y`) mais **jamais importé** | le filet manque |
| Dépendance a11y (Radix, Headless UI, react-aria) | **aucune** | chaque piège est à réimplémenter |

### 3.5 La cible, chiffrée et datée

**Niveau visé : WCAG 2.2 niveau AA sur les 10 écrans**, sauf deux exemptions nommées.

| Critère | Aujourd'hui | Cible | Comment on le garde |
|---|---|---|---|
| Contraste texte (1.4.3) | **10 paires fautives / 19** | **0**, mesuré dans les 2 thèmes | test `contraste.test.ts` sur la palette |
| Contraste UI (1.4.11) | non mesuré | ≥ 3:1 bordures et états | idem |
| Annonces temps réel (4.1.3) | **0 région live** | **1 région `polite` par écran temps réel** + `assertive` pour les demandes d'arbitrage | test rendu |
| Mouvement (2.3.3) | **0** garde | `motion-reduce:` sur les 19 transitions et 4 animations | règle de lint |
| Piège de focus (2.1.2) | **0/3 modales** | **3/3** | test clavier |
| Menus au clavier (ARIA APG) | **0/4** | **4/4** flèches + `Home`/`End` | test clavier |
| Cibles (2.5.8) | quelques 22 px | **≥ 24 px** partout | sonde de mise en page |
| Lint | 6 règles en `warn` | **`plugin:jsx-a11y/recommended` en `error`** | CI |
| Audit automatisé | aucun | **`vitest-axe` sur les 10 écrans, 0 violation `serious`/`critical`** | job `web-build` |

**Deux exemptions assumées, à écrire dans la note plutôt qu'à découvrir plus tard :**
1. **Le graphe de pipeline** (`VuePipeline`) n'est pas rendu accessible nœud à nœud ; il porte une
   **alternative textuelle équivalente** (la vue Kanban et le journal du run donnent la même
   information). Reproduire un DAG au lecteur d'écran n'a pas de motif ARIA établi.
2. **Le niveau AAA n'est pas visé.** Le contraste 7:1 imposerait `neutral-700` minimum pour tout
   texte secondaire et supprimerait la distinction primaire/secondaire dont la densité dépend.

### 3.6 Trancher : primitives accessibles, ou checklist ?

Le ticket demande de trancher, et de chiffrer plutôt que de supposer.

**Recommandation : adopter une base de primitives accessibles, mais seulement pour les 3 motifs qui
échouent — modale, menu, infobulle. Pas de migration générale.**

Les faits qui portent la décision :

- **Ce qui est fait main marche déjà**, sauf trois motifs : `Escape` 7/7, restauration du focus 7/7,
  rôles corrects. Migrer l'ensemble détruirait du travail qui tient.
- **Ce qui échoue, échoue là où le motif ARIA est le plus dur** : piège de focus (0/3), navigation
  aux flèches (0/4), infobulle accessible (0). Ce sont exactement les trois que les bibliothèques
  résolvent, et les trois qu'on réimplémente mal.
- **Le coût de l'ajout est réel** : `apps/web` a **3 dépendances de production**. En ajouter est un
  choix, pas un détail — mais `@radix-ui/react-dialog` + `react-dropdown-menu` + `react-tooltip`
  pèsent ~30 ko et n'imposent **aucun style** (headless), donc n'entrent pas en conflit avec le
  socle #245.
- **L'alternative « checklist » a déjà été essayée et a échoué** : le dépôt a une doc de langage
  visuel détaillée (`apps/web/README.md`), et 18 recopies de carte et 26 boutons refaits sont passés
  quand même. Une checklist qu'aucune machine ne vérifie ne tient pas — c'est la leçon de #306.

Chiffrage : **3 composants à remplacer** (`PanneauDetailTache`, `GuidePriseEnMain`, les 4 menus qui
partagent un patron identique) — **1 session**, tests compris.

#### Révision du 2026-08-25 (#536) — la décision tient, la bibliothèque non

Le lot 4 a implémenté les trois motifs. **La moitié de cette recommandation a été suivie, l'autre
retournée**, et c'est la seconde qu'il faut lire ici :

- **Tenu : « primitives plutôt que checklist ».** C'était le fond de l'arbitrage, et il est acquis —
  trois primitives partagées ([`lib/usePiegeDeFocus.ts`](../apps/web/lib/usePiegeDeFocus.ts),
  [`lib/useSurfaceDeroulee.ts`](../apps/web/lib/useSurfaceDeroulee.ts),
  [`components/Infobulle.tsx`](../apps/web/components/Infobulle.tsx)) portent désormais ce que sept
  surfaces réimplémentaient chacune de son côté. Le hook de surface a remplacé **quatre copies du
  même bloc de dix-huit lignes** : c'est cette duplication, et non un oubli, qui expliquait que la
  navigation aux flèches manque aux quatre menus à la fois.
- **Retourné : Radix.** `@radix-ui/react-dialog` + `react-dropdown-menu` + `react-tooltip` n'ont pas
  été ajoutés, et `apps/web` tient toujours en **trois dépendances de production**.

Ce qui a fait changer d'avis est ce que cette recherche n'avait pas regardé : elle a **compté des
motifs** (occurrences de `"Tab"`, de `ArrowUp`, de `title=`) sans ouvrir les composants. Ouverts,
**trois des sept surfaces ne sont pas la forme que la bibliothèque sait servir** :

| Surface | Ce qu'elle est | Ce que Radix en aurait fait |
|---|---|---|
| `GuidePriseEnMain` | une **surbrillance** qui suit un élément de la page, mesurée en `requestAnimationFrame` et amenée à l'écran par `scrollIntoView` | `Dialog` modal monte `react-remove-scroll`, qui **verrouille le défilement du corps** — le mécanisme même de la visite |
| `CentreNotifications` | un **panneau** : sections, titres, listes, cartes à deux boutons d'arbitrage | `DropdownMenu` attend des `DropdownMenuItem` ; le panneau n'a aucune entrée de menu |
| `AssistantFlottant` | **non modal par conception** (aucune fermeture au clic extérieur, la page reste utilisable) | `Dialog` non modal n'apporte aucun piège de focus : la dépendance pour rien |

Restaient quatre surfaces où Radix tombait juste — mais pour elles, le portail et le DOM de la
bibliothèque réécrivaient **sept fichiers de tests** dans un lot dont les tests sont explicitement
différés (#537, #539). On aurait payé une dépendance et une suite à reprendre pour livrer sur 4/7 ce
qu'un hook partagé livre sur 7/7.

**Ce que la recommandation avait raison de refuser reste refusé.** L'objection à la checklist —
« une checklist qu'aucune machine ne vérifie ne tient pas » — ne visait pas l'absence de
bibliothèque mais la **recopie** : 18 cartes et 26 boutons refaits à la main. Une primitive partagée
la supprime exactement comme une bibliothèque le ferait, et l'audit `vitest-axe` du lot 5 (#537) est
la machine qui vérifie. Ce qui aurait rouvert le défaut serait de réécrire ces motifs surface par
surface — ce que ce lot a précisément fermé.

⚠ **Ne pas relire cette révision comme « pas de bibliothèque, jamais ».** Elle porte sur ces trois
motifs et sur ces sept surfaces, mesurés. Un besoin dont la forme correspond à ce qu'une
bibliothèque sert — un vrai `DropdownMenu`, un positionnement flottant à collisions — se rejugera
sur ses propres faits.

---

## 4. Critère de sobriété opposable

Le ticket demande une règle **opposable à un ticket futur**, pas un principe. La voici.

### 4.1 La règle des trois places

> **Tout ce qu'un écran affiche occupe l'une de trois places, et une seule.**
>
> 1. **Le bandeau de tête** — au plus **4 chiffres**, et rien d'autre. Un chiffre y entre seulement
>    s'il change la décision de l'utilisateur *dans la minute*.
> 2. **Le corps** — au plus **3 blocs de plein format**, plus les blocs d'**arbitrage** (ceux qui
>    demandent une décision humaine), qui ne comptent pas dans le plafond **et disparaissent quand
>    la file est vide**.
> 3. **La colonne de propriétés** — tout le reste : métadonnées, réglages, historique, liens. Elle
>    s'allonge sans plafond, parce qu'elle défile et ne dispute rien au corps.
>
> **Ce qui ne tient dans aucune des trois n'est pas un bloc : c'est une ligne avec un renvoi**, vers
> l'écran dont c'est le sujet.

### 4.2 Pourquoi elle est opposable

Elle se vérifie **par un comptage**, donc par un test — pas par un jugement :

| Écran | Chiffres de tête | Blocs de corps | Verdict |
|---|---:|---:|---|
| Tableau de bord | 4 | **2** + 3 d'arbitrage | conforme |
| `/couts` | 4 | **5** | **dépasse de 2** |
| `/parametres` | 0 | **7 sections** | **dépasse de 4** |
| `/journal` | 0 | 3 | conforme |
| `/runs/[runId]` | 0 | 2 + onglets | conforme |

Le tableau de bord est **déjà conforme** — c'est l'acquis de #191 qu'il fallait protéger, et #476
n'y a pas touché. Les deux dépassements sont `/couts` et `/parametres`, tous deux écrans
d'exploration : la règle leur donne une réponse (une colonne de propriétés, ou un second niveau)
plutôt qu'un interdit.

### 4.3 Ce qu'elle répond au prochain ticket

- « Ajouter un panneau X au tableau de bord » → **le corps est plein**. Soit X remplace un bloc
  existant, soit X est un bloc d'arbitrage (et disparaît à vide), soit X est **une ligne + un
  renvoi**. La question n'est plus « est-ce utile ? » (ça l'est toujours) mais « **quelle place ?** ».
- « Ajouter un 5ᵉ indicateur » → **non**, sauf à en retirer un. Quatre est un plafond, pas une cible.
- « Ce réglage doit être visible » → colonne de propriétés.

C'est la formulation qui manquait à #191 : il a **épuré une fois** sans laisser de règle, et six
mois plus tard le compte était refait. La règle ne dit pas « moins », elle dit **où**.

---

## 5. Inventaire de l'outillage — éprouvé

Chaque ligne a été **appelée**. La réponse est citée.

| Outil | Éprouvé comment | Verdict |
|---|---|---|
| **MCP `figma-officiel`** | `whoami` → `handle: maestro`, plan **starter**, siège **View**. `create_new_file` → **201, fichier créé** | **Écriture opérationnelle.** Design-to-code et code-to-design ponctuels : oui |
| **Figma — Code Connect** | `list_file_components_for_code_connect` → **refus** : *« You need a Dev or Full seat on an Organization or Enterprise plan »* | **INDISPONIBLE.** La mécanique « design system relié au code » du ticket ne peut pas être achetée avec ce plan |
| **Figma — bibliothèques d'équipe** | `get_libraries` → 8 bibliothèques, **toutes `source: community`** (Material 3, Simple Design System, Apple) ; `libraries_available_to_add` : **liste vide** | **Aucune bibliothèque d'organisation publiable.** Un design system Figma partagé n'est pas tenable ici |
| **MCP `chrome-maestro`** | navigation, `resize`, `evaluate`, `take_screenshot`, `close` — **tous joués**, 5 captures produites | **Opérationnel.** C'est le pilier de l'outillage |
| **`mcp__chrome` (DevTools, `lighthouse_audit`)** | appel → **refusé** : non déclaré dans `.mcp.json`, absent de l'allowlist | **Hors de portée d'une session autonome.** Le seul audit a11y clé en main du poste est inutilisable telle quelle |
| **Stack locale** | `start.sh --demo --no-browser` sur 8071/3071 → **prête**, puis `--stop` → arrêtée | **Opérationnel** |
| **`curl`** | appel → **refusé par l'allowlist** | Toute interrogation d'API passe par `browser_evaluate` |
| **Skill `banc-mise-en-page`** | lu intégralement (238 l. + sonde 198 l.), non rejoué ici | **Le garde-fou de la refonte.** Mesure ce qu'aucun autre outil ne voit : géométrie, débordements, inatteignables. **Ne voit ni couleur, ni contraste, ni ARIA** |
| **Skill `verify`** | lu (117 l.) | Câblage temps réel. Ne regarde ni la géométrie ni le style |
| **Vitest + jsdom** | 583 cas / 31 fichiers | Logique et rendu. **Ne calcule aucune mise en page** ; `socle-visuel.test.tsx` garde les thèmes mais **aucune valeur** de rayon/ombre/couleur, volontairement |
| **`scripts/presentation/captures.sh`** | lu ; **non rejoué** | ⚠ **Défaut probable relevé** : il pose `maestro.theme` mais **jamais `maestro.projet.actif`** — or depuis #279 aucun écran n'est atteint sans projet actif, et le mode démo n'en déclare aucun. **Les captures de présentation tombent vraisemblablement toutes sur la porte d'entrée.** Confirmé indirectement : la stack montée ici, sans projet, n'affichait que `PosteVide`. Sa `MENU_REPLI` est en outre périmée (`/catalogue`, `/playbooks`) |
| **Skill `dataviz`** | **non lisible** — `~/.claude/skills/` est hors des répertoires autorisés | Connu par sa description seule. Utile pour `/couts`, à éprouver au lot concerné |
| **Skill `design`** | **n'existe pas** | Prémisse du ticket, corrigée |

### 5.1 Lequel tient la promesse « le même niveau partout » ?

**Aucun outil de maquette ne la tient. Seul un test la tient.**

C'est le renversement principal de cette recherche. Le ticket cherchait l'outillage du côté de Figma
— or Figma est ici **amputé de la seule fonction qui relierait la maquette au code** (Code Connect,
refusé) et **ne peut pas publier de bibliothèque partagée** (plan starter). Un fichier Figma resterait
donc ce que le ticket redoute lui-même : *« une image dans trois mois »*.

Ce qui tient un niveau sur tous les écrans, dans ce dépôt, c'est ce qui **échoue la CI quand il
n'est pas tenu**. Le dépôt le sait déjà — c'est toute la leçon de #306 (« la suite verte ne prouve
rien sur la mise en page »), qui a produit `banc-mise-en-page` plutôt qu'une consigne.

**La chaîne recommandée, du plus contraignant au moins :**

1. **Les tokens** (source unique en CSS) — ce qui n'est pas dans la palette n'existe pas.
2. **Les primitives** (bouton, champ, modale, menu) — ce qui n'est pas une primitive se voit.
3. **Les tests** : contraste sur la palette, `vitest-axe` sur les 10 écrans, lint `jsx-a11y` en
   `error`, comptage de sobriété. **C'est la seule couche qui refuse un merge.**
4. **`banc-mise-en-page`** au moment des lots de mise en page.
5. **Figma**, en appoint : explorer une direction sur 2-3 écrans avant de coder. **Jamais comme
   source de vérité** — le lien mécanique vers le code n'existe pas sur ce plan.

---

## 6. Recommandation

### 6.1 Direction visuelle retenue — « le même produit, avec du relief »

**Pas de nouvelle identité.** Le socle #245 est bon et récent ; le refaire coûterait des sessions
pour un gain nul sur le problème mesuré. La direction est de **donner du relief à ce qui existe** :

1. **Une hiérarchie typographique à 5 pas réellement utilisés**, dont un **titre de page à 20-24 px**
   qui manque aujourd'hui (§2.3). Retirer les doublons `text-xs`/`text-annexe`.
2. **Une palette sémantique tokenisée** (`surface`, `bord`, `texte`, `texte-secondaire`, `accent`,
   4 tons d'état), **conforme AA par construction et dans les deux thèmes** — ce qui supprime du
   même geste les 542 `dark:` écrits à la main.
3. **L'état porté par forme + couleur**, jamais par la couleur seule (§1.1).
4. **Trois places, un plafond** (§4) — la sobriété devient structurelle.
5. **Un jeu de primitives complet** : le bouton, le champ, la modale et le menu qui manquent.

### 6.2 Outillage retenu

- **Retenu** : les tests comme garde-fou (contraste, `vitest-axe`, lint `jsx-a11y` en `error`),
  `banc-mise-en-page`, `chrome-maestro`, `@radix-ui` pour les 3 motifs qui échouent.
- **Écarté** : le design system Figma comme source — **Code Connect refusé, aucune bibliothèque
  d'organisation** (§5). Figma reste un outil d'exploration.
- **À réparer** : `captures.sh` (projet actif manquant, §5).

### 6.3 Découpage du chantier — parent + 7 lots, 7 sessions

Chaque lot est mergeable seul sur `main` sans casser l'existant. Les tests sont différés au lot
final **sauf** ceux qui *sont* le livrable du lot (lots 2 et 5).

| # | Lot | Sessions | Dépend de |
|---|---|---:|---|
| 1 | **Palette sémantique et échelle typographique** — tokens CSS, 5 pas, doublons retirés, aucun écran retouché | 1 | — |
| 2 | **Le test de contraste** — la palette du lot 1 vérifiée AA dans les 2 thèmes, en CI | 1 | 1 |
| 3 | **Primitives manquantes** — `Bouton`, `Champ`, + reprise des 18 recopies de carte *(parallèle)* | 1 | 1 |
| 4 | **Modale et menu accessibles** — piège de focus, flèches, infobulle sur 3 modales + 4 menus *(parallèle)* — livré par primitives du dépôt, sans Radix (voir la révision du 2026-08-25 en §3.6) | 1 | 1 |
| 5 | **Le filet a11y** — `vitest-axe` sur les 10 écrans, `jsx-a11y/recommended` en `error`, `motion-reduce`, lien d'évitement | 1 | 3, 4 |
| 6 | **Les régions live** — 1 `polite` par écran temps réel, `assertive` pour l'arbitrage, + le test qui les garde | 1 | 5 |
| 7 | **Les trois places** — application de la règle de sobriété à `/couts` et `/parametres`, + doc et tests du chantier | 1 | tous |

**Total : 7 sessions.** Les lots 3 et 4 sont marqués `(parallèle)` : ils ne se touchent pas.

**L'ordre porte la décision** : les tokens d'abord (lot 1), leur test tout de suite (lot 2) — sans
quoi le lot 1 se défait au premier écran retouché. Le filet (lot 5) avant les régions live (lot 6),
parce qu'une région live sans test est exactement l'`aria-live` unique d'aujourd'hui : présente dans
le code, absente de l'écran.

**Ce qui n'est pas dans le chantier**, et c'est délibéré : aucune refonte d'écran, aucun changement
de navigation, aucune identité nouvelle. Le chantier rend le socle **tenu**. Ce qu'on en fait
ensuite est un autre sujet.

---

## 7. Ce qui n'a pas été vérifié

Par honnêteté de méthode :

- **Les captures de présentation** (`captures.sh`) : défaut déduit par lecture croisée, **non
  reproduit** — le script n'a pas été rejoué (il rebuild l'UI en production, plusieurs minutes).
- **Le contraste sur écrans peuplés** : le scénario de démo n'étant pas rattaché à un projet, les
  écrans mesurés étaient partiellement vides. Les chiffres de §3.2 sont un plancher.
- **Le skill `dataviz`** : non lisible depuis cette session.
- **Le coût de `@radix-ui`** en poids de bundle : estimé d'après la documentation des paquets, non
  mesuré par un build.
- **Un dossier a été créé hors du dépôt** pour la mesure — `E:/Projects Solutions/maestro-demo-471`,
  vide, supprimable sans conséquence. Le projet correspondant (`core/projets/prj-01fbb83e.json`) a,
  lui, été retiré.
