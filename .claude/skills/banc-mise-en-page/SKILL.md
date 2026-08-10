---
name: banc-mise-en-page
description: Vérifier qu'une page de la Control Tower tient à l'écran — hauteurs, défilement, débordements, points de rupture — en la mesurant dans un vrai navigateur
---

# Banc de mise en page

Répond à **une** question, celle que la suite de tests ne peut pas poser :
*est-ce que ça tient à l'écran ?* Pour une page donnée, le banc rend des
**captures aux points de rupture**, la liste des **débordements horizontaux** et
celle des **éléments qu'aucun défilement ne peut ramener**.

## Ce que ce banc apporte (et ce qu'il ne refait pas)

| Outil | Couvre | Ne voit pas |
| --- | --- | --- |
| `npm test` (Vitest + jsdom, #124) | logique, rendu, interactions, chaînes de classes | **aucune mise en page** : jsdom ne calcule ni hauteur, ni `overflow`, ni défilement |
| `/verify` | le câblage API↔UI réel : WebSocket, absence de rechargement, reprise après coupure | la géométrie de la page |
| **ce banc** | hauteurs, défilement, `overflow`, points de rupture — **mesurés** dans un vrai navigateur | tout le reste : ni logique, ni temps réel, ni données |

**Ne pas redoubler les deux autres.** Un test Vitest peut affirmer qu'un
`min-h-0` est bien sur chaque maillon de la chaîne flex (c'est ce que fait
`tests/kanban.test.tsx` pour #248) ; il ne peut pas dire que la section fait
5 198 px. Ce banc dit le pixel, et rien d'autre. Inversement, quand la question
est « la WebSocket se rebranche-t-elle ? », c'est `/verify`.

Le déclencheur : **dès qu'un ticket porte sur des hauteurs, du défilement, de
l'`overflow`, des éléments collants ou du responsive, passer ce banc avant de
conclure.** Une suite verte ne prouve rien sur la mise en page — #306 (le bas du
formulaire de la porte d'entrée inatteignable) est passé au travers de 237 tests,
du lint, du typage et de `next build`.

## La séquence

Cinq étapes, dans cet ordre. Les trois pièges connus **sont** les étapes 1, 2
et 5 : les sauter coûte une séquence entière, sans que la cause soit lisible.

### 1. Mettre une page sous le curseur

Deux voies, selon qu'on veut la page réelle ou un fragment isolé.

**a. La page réelle** — le cas normal, et le seul qui vaille pour un verdict.

```bash
bash scripts/controltower/start.sh --demo --no-browser
```

`--no-browser` est indispensable : sans lui le script ouvre sa propre fenêtre et
**arrête la stack dès qu'elle est fermée** (#149), ce qui couperait l'API sous le
navigateur qu'on pilote. Dans un **worktree**, viser les ports dédiés — et une
session relocalisée par `/ticket-start` n'hérite **pas** du bloc `env` du
worktree (docs/10 §9.1), donc les passer à la main :

```bash
MAESTRO_PORT_API=8008 MAESTRO_PORT_UI=3008 bash scripts/controltower/start.sh --demo --no-browser
```

Le mode **dev** suffit ici, contrairement à ce que fait
`scripts/presentation/captures.sh` : sa contrainte de production vient du
navigateur **headless**, où la WebSocket de rechargement à chaud de Next échoue
et bloque l'hydratation. Le MCP `chrome-maestro`, lui, pilote un Chrome **avec
fenêtre** : les pages s'hydratent normalement (vérifié sur #308).

**b. Le harnais `about:blank`** — quand la stack n'est pas nécessaire : isoler un
fragment, rejouer une chaîne de hauteurs, comparer deux CSS.

> ⚠ **Piège 1 — le harnais se monte dans `about:blank`, jamais dans un fichier.**
> Les deux voies naturelles sont fermées : le MCP `chrome-maestro` **bloque le
> protocole `file:`**, et `python -m http.server` **n'est pas dans l'allowlist**
> (donc irrecevable en session autonome — personne pour approuver).

```
browser_navigate  about:blank
browser_evaluate  () => { document.head.innerHTML = `<style>…</style>`;
                          document.body.innerHTML = `…`; }
```

Reproduire la chaîne **depuis `<html>`**, pas seulement le composant : le défaut
est presque toujours un ancêtre. Les deux règles du dépôt à recopier telles
quelles, faute de quoi le harnais ment (`app/layout.tsx`) :

```css
html { height: 100% }                                  /* `h-full` */
body { height: 100%; display: flex; flex-direction: column; overflow: hidden }
```

Les déclarations que Tailwind vient de compiler se relisent dans
`apps/web/.next/static/chunks/*.css` après un `npm run build`.

### 2. Ouvrir la page en neutralisant ce qui s'interpose

> ⚠ **Piège 2 — neutraliser la visite guidée AVANT le `goto`.** Sur un profil
> neuf elle s'ouvre d'elle-même (#122) et son voile `fixed inset-0 z-40`
> **absorbe les clics** : tout ce qui suit échoue sans que la cause soit lisible.

Le MCP n'a **pas** d'`addInitScript` (contrairement à `/verify`, qui pilote
playwright-core en direct). D'où **deux passes** — la première pour poser les
clés, la seconde pour mesurer :

Les exemples ci-dessous gardent les ports du worktree (`3008`/`8008`) ; hors
worktree, ce sont `3000` et `8000`.

```
browser_navigate  http://localhost:3008/            ← 1re passe : on ne mesure rien
browser_evaluate  () => {
                    localStorage.setItem("maestro.guide.vu", "1");     // la visite se croit vue
                    localStorage.setItem("maestro.theme", "clair");    // capture reproductible
                    localStorage.setItem("maestro.projet.actif", "<id>");
                  }
browser_navigate  http://localhost:3008/<page>      ← 2e passe : c'est celle qu'on mesure
```

`maestro.projet.actif` n'est pas un confort : depuis #279 **aucun écran n'est
atteint sans projet actif**, et un identifiant inconnu renvoie à la porte
d'entrée. En mode `--demo` aucun projet n'est déclaré — en déclarer un, sur un
dossier **hors du dépôt** (le dépôt de Maestro se refuse lui-même) et hors
`AppData` (chemin sensible refusé) :

```bash
mkdir -p .maestro/banc
# Séparateurs **en avant**, et par un fichier : un `\` de chemin Windows passé en
# ligne devient une échappée JSON invalide, et un `/tmp/…` de Git Bash n'est pas
# le même chemin pour le curl de Windows (il y lit `C:\tmp`).
printf '{"nom":"Banc","racine":"D:/un/dossier/quelconque"}\n' > .maestro/banc/projet.json
curl -s -X POST http://127.0.0.1:8008/api/projets \
  -H 'Content-Type: application/json' -d @.maestro/banc/projet.json
# … puis, le banc passé :
curl -s -X DELETE http://127.0.0.1:8008/api/projets/<id>
```

Il laisse un `core/projets/<id>.json` dans le répertoire de travail — gitignoré,
mais à retirer par le `DELETE` ci-dessus plutôt qu'à laisser traîner.

Quand c'est **la visite guidée elle-même** qu'on mesure, c'est l'inverse : ne pas
poser la clé, ou la retirer et recharger.

### 3. Mesurer et capturer à chaque point de rupture

Poser la sonde : passer le contenu de [`sonde.js`](sonde.js) **tel quel** en
`function` de `browser_evaluate`. Le premier appel installe `window.__banc` et
rend son relevé ; aux largeurs suivantes, `() => window.__banc()` suffit. Une
**navigation** efface la sonde (contexte neuf) — un `browser_resize`, non.

À chaque point de rupture : `browser_resize`, puis `() => window.__banc()`, puis
`browser_take_screenshot`.

| Fenêtre | Ce qu'elle vise |
| --- | --- |
| 375 × 667 | mobile — sidebar repliée, colonnes empilées |
| 768 × 800 | `md` — le seuil où la sidebar réapparaît |
| 1024 × 700 | `lg` |
| 1280 × 800 | `xl` — le poste de travail habituel |
| 1536 × 900 | `2xl` — la borne de `max-w-screen-2xl` |
| **1280 × 500** | **la fenêtre courte** — c'est elle qui attrape #306, et aucune autre |

Les cinq premières sont les points de rupture de Tailwind v4 (défauts : 640 /
768 / 1024 / 1280 / 1536 — le dépôt ne les redéfinit pas). La sixième n'est pas
une largeur : **un défaut de hauteur ne se voit qu'à hauteur réduite**, et c'est
la classe de bug la plus coûteuse puisqu'elle rend une action inatteignable.

Deux états à balayer plutôt qu'un, quand la page vit dans le shell : la zone de
contenu est un **`@container`** (`Shell.tsx`), donc ses `@md:` basculent sur la
largeur de la **zone** et non sur celle de la fenêtre. Replier la sidebar élargit
la zone sans toucher à la fenêtre — mêmes 1280 px, mise en page différente :

```
browser_evaluate  () => localStorage.setItem("maestro.sidebar.repliee", "1")
```

> ⚠ **Les captures vont sous `.maestro/banc/`**, jamais à la racine. Un
> `filename` sans dossier (`banc-375.png`) atterrit **à la racine du répertoire
> de travail** — pas dans `.playwright-mcp/` — où `/ticket-ship` le commiterait
> avec le reste. `.maestro/` est gitignoré et c'est là que va, par convention du
> dépôt, ce qu'un outil invite à relire.

```
browser_take_screenshot  filename: .maestro/banc/<page>-1280x500.png
```

### 4. Rendre le constat

Le rapport de la sonde, à chaque fenêtre :

- `inatteignables` — **le verdict principal**. Chaque entrée est une commande
  (bouton, lien, champ) qu'aucun défilement n'amène sous les yeux : l'élément,
  son libellé, le conteneur qui le rogne, son `overflow` et de combien de pixels
  il est hors champ. Une liste non vide est un bug, pas une nuance.
- `rogneurs` — **la cause** dont le point précédent donne les symptômes : le
  conteneur en `overflow: hidden` dont le contenu dépasse, et de combien.
- `debordement_horizontal` — ce qui sort de la fenêtre à droite.
- `page.document_defile` / `page.hauteur_document_px` — le symptôme de #248 : une
  page qui **s'allonge** au lieu de tenir.

`document_defile: false` n'est **pas** un défaut en soi : depuis #248 le document
ne défile plus, c'est la colonne de contenu du shell qui s'en charge. C'est bien
pourquoi la sonde remonte la **chaîne des ancêtres** au lieu de regarder le
document — un élément peut être parfaitement atteignable dans une page qui ne
défile pas, et inatteignable dans une page qui défile.

Trois choses que la sonde **ne signale pas**, à dessein — les rétablir noierait
le rapport (mesuré sur `/parametres`, où elles donnaient 3 fausses alertes) :
une table large dans son `overflow-x-auto` (le patron du dépôt), un titre en
`truncate` (il rogne à dessein et le montre par ses points de suspension), et un
élément `position: fixed` (le défilement ne l'emporte pas).

### 5. Fermer la fenêtre

> ⚠ **Piège 3 — `browser_close` en sortie, à la fin de *chaque* séquence** et pas
> seulement en fin de session. Chrome n'accepte **qu'un seul consommateur à la
> fois** sur un `--user-data-dir` (verrou ProcessSingleton) : une fenêtre laissée
> ouverte bloque tout autre outil pointant le même profil. Si une étape suivante
> en a besoin, elle rouvrira.

Puis rendre la stack et le projet de test :

```bash
bash scripts/controltower/start.sh --stop
```

## Vérifier que le banc voit encore

Un banc qui ne rapporte rien est indiscernable d'un banc cassé. Pour le lever de
doute — casser la page **dans le navigateur**, sans toucher au code :

```
browser_evaluate  () => {
  const c = [...document.querySelectorAll("*")].find(
    (e) => /auto|scroll/.test(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight + 1);
  c.style.overflowY = "hidden";              // on recrée le défaut de #306
  const r = window.__banc();
  c.style.overflowY = "";                    // et on rend la page intacte
  return r;
}
```

Sur la porte d'entrée en 1280 × 500, ce geste fait remonter « Déclarer le
projet » et « Annuler » à 198 px hors champ, sous un conteneur qui coupe 279 px.
S'il ne remonte rien, c'est la sonde qu'il faut réparer, pas la page.
