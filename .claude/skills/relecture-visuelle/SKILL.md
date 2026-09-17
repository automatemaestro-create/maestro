---
name: relecture-visuelle
description: Regarder le rendu des écrans qu'un ticket a touchés — les deux thèmes, dans un vrai navigateur — et en rendre un jugement écrit, avant de clore
---

# La relecture visuelle

Répond à **une** question, celle qu'aucun autre maillon du dépôt ne pose :
*à quoi ça ressemble, et est-ce que ça a l'air juste ?*

Une session peut écrire une interface, voir tous ses tests verts, et n'avoir
jamais ouvert l'écran qu'elle vient de changer. C'est le trou nommé par
[docs/30 §5.1](../../../docs/30-cible-visuelle-control-tower.md) : la chaîne
visuelle sait **viser** (`/design-veille`), **tenir** (tokens, primitives) et
**garder** (contraste, a11y, sobriété, géométrie) — personne n'y **regarde**.

## Ce que ce geste apporte (et ce qu'il ne refait pas)

| Outil | Couvre | Ne voit pas |
| --- | --- | --- |
| `npm test` (Vitest + jsdom) | logique, rendu, interactions, chaînes de classes | **aucun pixel** : jsdom ne peint rien |
| `contraste.test.ts` · `a11y.test.tsx` · `sobriete.test.tsx` | les **règles** — ratios, rôles, nombre de blocs | si le résultat est **beau, lisible, cohérent** |
| `/banc-mise-en-page` | la **géométrie** — hauteurs, défilement, points de rupture | à quoi ressemble ce qui tient |
| `/verify` | le **câblage** — WebSocket, reprise, absence de rechargement | tout le reste |
| **ce geste** | le **rendu**, les deux thèmes, avec des yeux | la logique, la géométrie mesurée, les règles |

**Ne pas redoubler les quatre autres.** Une hauteur suspecte appelle
`/banc-mise-en-page`, un contraste douteux se prouve dans `contraste.test.ts`
sur les octets de `globals.css`, une WebSocket muette est l'affaire de
`/verify`. Ici, on regarde — et ce qu'on trouve, on le **nomme** ; les mesurer
est le travail d'un autre outil, souvent d'un autre ticket.

Le déclencheur : **avant de clore un ticket qui a touché une surface visible.**
Le geste dit lui-même s'il y en a une — inutile de le deviner.

⚠ **Et depuis #935 il n'est plus une règle lue** : `/ticket-finish` pose la
question à son étape 4bis, juste après le commit et **avant** le filet CI, puis
ouvre ce skill quand `--plan` rend des écrans (`docs/30 §5.5`). On ne demande
rien et on ne l'appelle pas de soi-même : *regarder* n'est pas un verdict, c'est
ce qui permet d'en rendre un — d'où un mécanisme identique en run et en
interactif, là où la veille de `/ticket-start`, elle, **propose**.

## La séquence

### 1. Demander le plan (et souvent s'arrêter là)

```bash
bash scripts/design/relecture-visuelle.sh --plan <iid>
```

Le script dérive les écrans **du ticket** — commits de la branche *et* travail
non commité (`ecrans-touches.sh --ref HEAD --travail-en-cours`, #544/#932) —,
puis remonte des composants partagés vers les écrans qui les **affichent**. On
ne nomme aucun écran à la main : ce qui est dérivé est traçable, ce qui est
deviné ne l'est pas.

Trois réponses, et deux d'entre elles terminent le geste :

- **code `3` — aucune surface visible.** C'est une réponse, pas une panne : un
  ticket de moteur, de CI ou de doc n'a pas d'écran. On ne monte rien, on ne
  paie rien, on passe.
- **code `0` avec des écrans.** On continue.
- **une section `indéterminé`.** Des fichiers dont aucune route ne se dérive :
  la coquille de tous les écrans (`app/layout.tsx`, `globals.css`) ou un
  composant que personne n'importe encore. **Ils ne disparaissent pas du
  jugement** : ils vont à « ce que je n'ai pas pu voir », nommés un par un.
  Quand c'est le shell (`Shell.tsx`) ou la coquille, la réponse pratique est
  d'ouvrir **un** écran quelconque en plus — le changement y est, partout.

### 2. Monter la stack

```bash
bash scripts/design/relecture-visuelle.sh <iid>
```

Même plan, plus : les **ports du worktree** (jamais 8000/3000 — ils sont ceux
de la stack d'à côté), le **projet de démo** déclaré (sans projet actif, le
shell ne rend que sa porte d'entrée, #279) et `start.sh --demo --no-browser`.

`--demo` est voulu : le scénario factice **peuple** les écrans, et un poste vide
ne montre pas le rendu qu'on vient d'écrire. C'est l'inverse du choix de
`/retex-utilisateur`, qui veut précisément le poste vide d'un nouvel arrivant.

Sans option, la démo sert l'état **nominal**. Les états limites se montent par
le même geste, un état à la fois — voir l'étape 4bis.

> ⚠ `--no-browser` est dans le script et doit y rester : sans lui `start.sh`
> ouvre sa propre fenêtre et **arrête la stack quand elle se ferme** (#149) —
> l'API disparaîtrait sous le navigateur qu'on pilote.

### 3. Neutraliser ce qui s'interpose, puis poser le thème

> ⚠ **La visite guidée s'ouvre d'elle-même sur un profil neuf** (#122) et son
> voile `fixed inset-0 z-40` **absorbe les clics**. La neutraliser **avant** de
> naviguer, faute de quoi tout ce qui suit échoue sans cause lisible.

Le MCP n'a pas d'`addInitScript` : il faut **deux passes**, la première pour
poser les clés, la seconde pour regarder.

```
browser_navigate  http://localhost:<PORT_UI>/
browser_evaluate  () => {
                    localStorage.setItem("maestro.guide.vu", "1");
                    localStorage.setItem("maestro.theme", "clair");   // ou "sombre"
                    localStorage.setItem("maestro.projet.actif", "prj-demo");
                  }
```

### 4. Regarder — chaque écran, dans les deux thèmes

**Un thème à la fois, tous les écrans, puis l'autre** : le thème se pose une
fois par passe au lieu d'une fois par écran. Il est appliqué au **chargement**
(script d'init du layout), donc une navigation le prend en compte ; l'écrire
sans recharger ne change rien.

```
browser_navigate         http://localhost:<PORT_UI>/<route>
browser_take_screenshot  filename: .maestro/relecture/<iid>/<ecran>-<theme>.png
```

Puis **relire chaque capture** (outil `Read`) : c'est là que le geste a lieu.
Une capture qu'on prend sans la regarder est une galerie, pas une relecture.

> ⚠ **Le `filename` se donne en chemin RELATIF.** La racine autorisée du MCP est
> le worktree de la session, mais son contrôle compare les chemins
> **littéralement** : un `e:/…` — la forme qu'on obtient en convertissant la
> racine MSYS — est refusé « outside allowed roots » face à un `E:/…` pourtant
> identique. Le relatif rend la question sans objet. Il atterrit bien dans
> `.maestro/` du worktree (gitignoré), jamais dans `.playwright-mcp/`.

Les deux thèmes, **toujours** : le socle en porte deux, le filet de contraste
les garde tous les deux, et un écran juste en clair peut être illisible en
sombre — c'est même l'erreur la plus probable, le développement se faisant le
plus souvent dans un seul des deux.

`<ecran>` est la **clé** de l'écran (`/` → `accueil`, `/couts` → `couts`) :
`--couverture` (étape 4bis) nomme les fichiers qu'il attend.

### 4bis. Les états limites — là où le rendu casse (#978)

L'état nominal est rarement celui qui casse. C'est **une file vide** sans
explication, **une erreur** qui déborde, **un nom de 80 caractères**, **une
liste de 200 lignes**. La démo sert ces états sous un nom, et le script les
monte :

```bash
bash scripts/design/relecture-visuelle.sh <iid> --scenario <nom>
```

| État | Ce que la démo sert | Ce qu'on y cherche |
| --- | --- | --- |
| `vide` | aucun run, aucune tâche, aucune validation, aucun fil | un état vide qui **explique** et propose la suite, pas un cadre blanc |
| `erreur` | toutes les routes en 500, WebSocket refusée — sauf `/api/sante` et `/api/projets` | l'erreur **nommée**, lisible dans les deux thèmes, sans casser la mise en page |
| `charge` | 200 tâches sur 20 niveaux, 24 runs, 30 validations, 641 lignes de journal, 31 conversations dont une de 80 messages, un nom d'agent de 80 caractères, un jeton sans espace | ce qui **déborde** : colonne élargie, texte coupé sans ellipse, défilement horizontal |

Les noms sont **lus** dans `maestro/controltower/demo.py` (le plan les
annonce) : un nom que la démo ne sert pas est refusé avant la stack. Écran
« Projets » en `erreur` : rien à voir, sa seule route est épargnée pour que le
shell laisse entrer dans les autres écrans.

**Quels états ouvrir.** Ceux que la rubrique **« États à couvrir »** du ticket
nomme (section `## Rendu attendu`, #976), rapprochés des noms ci-dessus par
jugement (« contenu long » est `charge`). Quand le ticket ne les nomme pas
(section absente ou « non renseigné »), on ouvre **les trois** : un état jamais
ouvert est un état que personne ne regarde, et ce qu'on paie en échange, c'est
~18 s de redémarrage par état. Un état que la démo ne sait pas servir, par
exemple une largeur téléphone, va à « ce que je n'ai pas pu voir ».

**Comment.** Chaque état **redémarre** la stack : le scénario est celui de
l'API, qu'on ne change pas à chaud. Le `localStorage` posé à l'étape 3
survit au redémarrage (même origine), donc il n'y a qu'à renaviguer. Les
captures d'un état vont **dans son sous-dossier** :

```
browser_take_screenshot  filename: .maestro/relecture/<iid>/<etat>/<ecran>-<theme>.png
```

Les deux thèmes valent ici comme ailleurs, et on relit chaque capture avec
`Read`, comme à l'étape 4. En `erreur`, la pastille « Reconnexion… » est
**attendue** : c'est la WebSocket refusée, pas un défaut de l'écran. Et la
bannière d'erreur n'arrive qu'**après** le premier chargement, environ 2 s
mesurées sur `/runs` : capturée trop tôt, la page montre « Chargement… ». On
attend donc qu'elle soit là (`browser_wait_for` sur « a répondu ») avant de
capturer. En `charge`, tout est publié avant que l'UI ne démarre, mais **le
rendu, lui, prend du temps** : sur `/chat`, une capture à 3 s montrait un bloc
« Conversations » vide et un composeur sur la barre du haut, alors qu'à 8 s
l'écran était juste. On attend donc un **contenu** (`browser_wait_for` sur un
texte que l'écran doit afficher, par exemple « Voir les ») et jamais un délai
fixe. Un écran qui paraît cassé à la première capture se recapture avant de
devenir un constat.

**Puis compter ce qui a été vu, écran par écran** :

```bash
bash scripts/design/relecture-visuelle.sh --couverture <iid>
```

Le script croise les écrans du plan, les états et les deux thèmes avec les
captures présentes sur le disque. Il ne démarre rien et ne rend aucun verdict.
Il ne sait voir qu'une **capture**, jamais un regard : une capture qu'on n'a
pas relue ne compte pas, et c'est à toi de le tenir.

### 5. Fermer, puis rendre

> ⚠ `browser_close` **à la fin de chaque séquence**, pas seulement en fin de
> session : Chrome n'accepte qu'un consommateur par `--user-data-dir`, et une
> fenêtre oubliée bloque tout autre outil visant le même profil.

```bash
bash scripts/design/relecture-visuelle.sh --fin
```

Arrête la stack et retire le projet qu'elle avait déclaré — **y compris quand
la relecture s'est mal passée** : une stack laissée derrière tient un port pour
le ticket suivant.

## Le livrable : un jugement, pas une galerie

Écrire `.maestro/relecture/<iid>/jugement.md`, **et le reprendre dans le résumé
de la session** — le fichier vit dans un worktree que le merge fera ramasser.
Trois sections, et les trois sont obligatoires :

- **ce qui va** — en une ligne ou deux. Pas un compte rendu des captures : ce
  qu'on a vérifié et qui tient.
- **ce qui cloche** — chaque constat avec son écran, son thème, et ce qu'on en
  fait : corrigé ici, ou à ouvrir en ticket. Un constat sans suite est un
  constat perdu.
- **ce que je n'ai pas pu voir** — les `indéterminé` du plan, un écran qui n'a
  pas chargé, un état que la démo ne sert pas (une largeur téléphone) ou qu'on
  n'a pas ouvert, **nommé avec son écran**. C'est la section qui distingue un
  jugement d'un ✓ : *ne pas avoir regardé n'est pas avoir trouvé que tout va
  bien.*

Et sous les trois, **la couverture des états** : le tableau de
`--couverture <iid>` recopié tel quel, écran par écran. C'est ce qui dit
lesquels ont été vus sans qu'on ait à le reconstituer de la prose. Une
case « — » sur un état que le ticket demandait doit se retrouver, nommée, dans
« ce que je n'ai pas pu voir ».

**Puis le consigner sur le ticket** — toujours, et pas seulement quand il y a un
constat (#935) :

```bash
bash scripts/gitlab/lib.sh relecture-note <iid> .maestro/relecture/<iid>/jugement.md
```

C'est le geste qui fait survivre le jugement : le fichier vit dans un worktree
que le merge fera ramasser, et un résumé de session meurt avec sa console
(#608, #795). **Toujours**, parce que c'est ce qui sépare « regardé, rien à
signaler » de « personne n'y a pensé » — les deux se ressemblent partout
ailleurs, et c'est exactement le défaut que #935 corrige. Quand tout va, rester
bref : la brièveté est dans le contenu, jamais dans l'absence de trace.

Et si la relecture **n'a pas eu lieu** — stack qui ne démarre pas, écran qu'on
n'a pas su atteindre, geste abandonné —, écrire la raison et l'enregistrer
plutôt que de la taire :

```bash
bash scripts/gitlab/lib.sh relecture-note --raison <iid> <fichier-de-la-raison>
```

Le verbe est **idempotent** (empreinte `cksum`) : une clôture rejouée après un
pipeline rouge n'empile rien, et un jugement enrichi s'ajoute au lieu d'écraser.

## Le prix, annoncé plutôt que masqué (règle de #418)

Mesuré le 2026-09-11, worktree déjà installé, poste de référence :

| Étape | Coût |
| --- | --- |
| `--plan` | ~2 s (lecture git seule, aucune stack) |
| montage de la stack | **18 s** |
| par écran et par thème | ~4 s (une navigation, une capture) |
| par état limite (`--scenario`, #978) | ~18 s de redémarrage, puis ~4 s par écran et par thème |
| `--couverture` | ~2 s (lecture git et disque, aucune stack) |
| `--fin` | **6 s** |

Soit ~50 s pour trois écrans dans l'état nominal, hors le temps de regarder,
et ~2 min 30 avec les trois états limites. Ce sont des estimations, pas des
mesures : aucune relecture complète n'a encore été chronométrée. C'est pourquoi
`--plan` existe séparément : un ticket sans surface visible coûte deux
secondes pour l'apprendre. Un run à concurrence 3 monterait trois stacks — sur
des ports distincts, ce que `worktree.sh` garantit depuis #152.

## En session de run

**Rien ne change**, et c'est le propre de ce geste. Tout ce qui précède est
jouable : `mcp__chrome-maestro` passe déjà l'union des deux allowlists, et
`bash scripts/controltower/start.sh` comme
`bash scripts/design/relecture-visuelle.sh` sont dans celle du run (#932). Rien
ici ne demande le web : la relecture regarde **ce qu'on a écrit**, là où
`/design-veille` cherche ce que d'autres ont fait — un accès que #933 a depuis
ouvert aux deux régimes, et dont celui-ci n'a de toute façon pas besoin.

Le jugement, lui, n'a personne pour le lire à l'écran : le consigner sur le
ticket est donc la seule façon qu'il survive.
