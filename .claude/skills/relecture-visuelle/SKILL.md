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
| **ce geste** | le **rendu**, les deux thèmes, avant et après, jugé par un **regard neuf** sur une **grille fixe** | la logique, la géométrie mesurée, les règles |

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
- **code `0` avec des écrans.** On continue. Chaque écran porte sa ligne
  **`avant :`** (#977) — l'URL où `origin/main` le sert, ou **`écran NOUVEAU`**
  quand il n'existe pas encore sur `origin/main`. Un écran nouveau n'a pas
  d'avant, et on ne va pas le chercher : il serait capturé sur une 404.
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

**Puis l'avant** (#977) : une **seconde stack**, servie depuis un worktree
**détaché sur `origin/main`** (`<iid>.avant`, monté par `worktree.sh avant`),
sur les ports de l'après **+ 200**, et dans **le même état** — un état
qu'`origin/main` ne déclare pas n'a pas d'avant. Le worktree du ticket n'est
jamais touché pour l'obtenir — c'est la raison de cette voie, écrite en
`docs/30 §5.6`. L'avant est **best-effort** : `origin/main` introuvable, montage
ou stack en échec, et le script le dit — l'après reste prêt, et l'avant manquant
va à « ce que je n'ai pas pu voir ». `MAESTRO_RELECTURE_AVANT=0` l'éteint.

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

> ⚠ **Une fois PAR ORIGINE.** L'après (`localhost:<PORT_UI>`) et l'avant
> (`localhost:<PORT_UI + 200>`) sont deux origines, donc deux `localStorage` :
> des clés posées sur l'une n'existent pas sur l'autre, et l'avant s'ouvrirait
> sur la visite guidée, dans le mauvais thème, sans projet.

### 4. Regarder — chaque écran, dans les deux thèmes

**Un thème à la fois, tous les écrans, puis l'autre** : le thème se pose une
fois par passe au lieu d'une fois par écran. Il est appliqué au **chargement**
(script d'init du layout), donc une navigation le prend en compte ; l'écrire
sans recharger ne change rien.

```
browser_navigate         http://localhost:<PORT_UI>/<route>
browser_take_screenshot  filename: .maestro/relecture/<iid>/<ecran>-<theme>.png
browser_navigate         <URL de la ligne « avant : » du plan>
browser_take_screenshot  filename: .maestro/relecture/<iid>/<ecran>-<theme>-avant.png
```

**L'après et l'avant côte à côte**, même écran, même thème — c'est la paire qui
se juge, pas chaque image seule : ce qui a changé, et si le changement a abîmé
ce qui allait. Un écran **nouveau** n'a que son après ; le dire dans le jugement.

> ⚠ **Attendre que CHAQUE page soit prête avant de la capturer.** Les deux
> stacks ne compilent pas au même moment, et une paire dont un côté est encore
> sur « Chargement… » montre une différence qui n'en est pas une (mesuré au
> cadrage de #977 : un avant chargé contre un après vide, sur le même écran).
> Le signal est celui de `scripts/presentation/captures.mjs` (#830) : le `<main
> id="contenu-principal">` du shell présent, ni « Reconnexion… » ni
> « Chargement » à l'écran — à attendre par un `browser_evaluate` qui interroge
> la page jusqu'à ce qu'il tienne.

> ⚠ **Les chiffres de la démo ne se comparent pas.** Le scénario factice
> avance avec le temps, et les deux stacks n'ont pas démarré ensemble : coût,
> tokens ou nombre d'appels diffèrent entre l'avant et l'après sans que le
> ticket y soit pour rien. On compare la **mise en page et le rendu**, jamais
> les valeurs.

Puis **relire chaque capture** (outil `Read`) — pour vérifier qu'elle **montre
l'écran** : prête, pas sur « Chargement… », pas sur la visite guidée, le bon
thème. Une capture ratée se reprend ici, avant de devenir un « non vu » plus
loin. Le **jugement**, lui, n'est plus le tien : il est rendu à l'étape 5 par un
regard qui n'a pas écrit l'écran.

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
| `decomposition` | une **phase**, pas un état : un run qui travaille 4 minutes **sans publier une seule tâche** — journal qui avance, coût qui monte —, puis son plan d'un coup et ses quatre tâches ; rejoué en boucle (#1109) | que l'écran **dise** que le run décompose au lieu de montrer un vide, et que la **transition** se voie : le compte de tâches bascule une fois, de 0 au total |

Les noms sont **lus** dans `maestro/controltower/demo.py` (le plan les
annonce) : un nom que la démo ne sert pas est refusé avant la stack. Écran
« Projets » en `erreur` : rien à voir, sa seule route est épargnée pour que le
shell laisse entrer dans les autres écrans. `decomposition` est le seul nom qui
**passe** : ce qu'on y capture dépend du moment, et le journal de l'API (que
`start.sh` nomme au démarrage) annonce chaque passage.

**Quels états ouvrir.** Ceux que la rubrique **« États à couvrir »** du ticket
nomme (section `## Rendu attendu`, #976), rapprochés des noms ci-dessus par
jugement (« contenu long » est `charge`). Quand le ticket ne les nomme pas
(section absente ou « non renseigné »), on ouvre **les trois états limites** —
`vide`, `erreur`, `charge` : un état jamais ouvert est un état que personne ne
regarde, et ce qu'on paie en échange, c'est ~18 s de redémarrage par état.
`decomposition` n'entre pas dans ce défaut : c'est une **phase**, elle ne
s'ouvre que si le ticket la nomme ou si l'écran montre le **début** d'un run,
et chaque passage coûte 4 minutes et laisse un run soldé derrière lui. Un état que la démo ne sait pas servir, par
exemple une largeur téléphone, va à « ce que je n'ai pas pu voir ».

**Comment.** Chaque état **redémarre** la stack : le scénario est celui de
l'API, qu'on ne change pas à chaud. Le `localStorage` posé à l'étape 3
survit au redémarrage (même origine), donc il n'y a qu'à renaviguer. Les
captures d'un état vont **dans son sous-dossier** :

```
browser_take_screenshot  filename: .maestro/relecture/<iid>/<etat>/<ecran>-<theme>.png
browser_take_screenshot  filename: .maestro/relecture/<iid>/<etat>/<ecran>-<theme>-avant.png
```

L'avant suit l'état : la préparation le redémarre dans le même scénario, ou
dit qu'`origin/main` ne sert pas cet état — il n'y a alors que l'après.

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

### 5. Le regard neuf — c'est lui qui juge (#980)

**L'auteur voit ce qu'il a voulu faire ; il faut quelqu'un qui voie ce qu'il a
produit.** Le jugement est rendu par le sous-agent `regard-neuf`
(`.claude/agents/regard-neuf.md`, outil `Read` seul), qui ne reçoit que trois
choses : les **captures** avant/après, le **rendu attendu** du ticket (#976) et
les **décisions déjà prises** à l'écran — partis pris d'une veille, variante
retenue (#979). **Ni le code, ni le diff, ni ton raisonnement.**

D'abord la saisine, qui les réunit — les stacks peuvent rester montées, elle ne
lit que le disque et le ticket :

```bash
bash scripts/design/relecture-visuelle.sh --saisine <iid>
```

Elle écrit `.maestro/relecture/<iid>/saisine.md` : les paires capturées en
chemins absolus, la section « Rendu attendu » du ticket, les commentaires qui
**commencent** par `## Veille de conception` ou `## Variante retenue`, la
**grille** (`scripts/design/grille-relecture.tsv`, seul endroit où elle
s'écrit) et le gabarit à remplir. Sa dernière ligne est `SAISINE <chemin>`.
Une veille consignée **avant** l'ancre — un commentaire de partis pris qui ne
commence pas par elle — s'ajoute par `--partis-pris <fichier>`, où tu la
recopies **telle quelle**, sans un mot de plus. Un ticket illisible ne bloque
pas : la saisine le dit, et les rubriques iront à « non vu ».

Puis le sous-agent, et **son prompt est cette phrase, au mot près** — un mot de
contexte en plus serait ce que ce geste retire :

```
Agent  subagent_type: "regard-neuf"
       description:   "Regard neuf sur #<iid>"
       prompt:        "Ta saisine : <chemin de la ligne SAISINE> — lis-la, puis rends-la remplie."
```

Sa réponse va **telle quelle** dans `.maestro/relecture/<iid>/regard.md` (outil
`Write`) : les trois sections `### Regard neuf — …`, que tu ne retouches pas.

> ⚠ **Un agent de projet se charge au démarrage de la session.** Une session
> ouverte avant que `.claude/agents/regard-neuf.md` existe répond « Agent type
> 'regard-neuf' not found » — c'est arrivé à la session qui l'a écrit. Dans ce
> cas seulement, `subagent_type: "general-purpose"` et pour prompt
> « Suis la consigne de `<racine>/.claude/agents/regard-neuf.md` (ignore son
> en-tête), puis : ta saisine : <chemin> — lis-la, puis rends-la remplie. » —
> et **nomme ce repli** dans le jugement : le sous-agent avait alors d'autres
> outils que `Read`.

**Ce que tu fais des ✗.** Chacun va dans « ce qui cloche », avec sa suite :
**corrigé ici** (puis nouvelles captures, nouvelle saisine, nouveau regard —
le précédent ne jugeait pas cet écran-là), **ticket à ouvrir**, ou **contesté
sur pièces**. Un regard neuf peut voir ce qui n'y est pas — mesuré à l'essai de
#980 : un chiffre « teinté de rouge » en sombre, blanc sur la capture et sans
couleur dans le code. Le contester est permis ; le **retirer de la grille**
ne l'est pas, et la pièce se nomme (la capture relue, la ligne de code) — *tu
es l'auteur, et « ce n'est pas ce que je voulais faire » n'est pas une pièce.*

### 6. Fermer, puis rendre

> ⚠ `browser_close` **à la fin de chaque séquence**, pas seulement en fin de
> session : Chrome n'accepte qu'un consommateur par `--user-data-dir`, et une
> fenêtre oubliée bloque tout autre outil visant le même profil.

```bash
bash scripts/design/relecture-visuelle.sh --fin
```

Arrête **les deux** stacks, retire le projet déclaré et le worktree de l'avant —
**y compris quand la relecture s'est mal passée** : une stack laissée derrière
tient un port pour le ticket suivant, et un avant oublié pèse ~500 Mo. S'il en
reste un malgré tout (session coupée avant `--fin`), le montage d'avant suivant
sur ce poste le ramasse dès que son ticket n'a plus de worktree.

## Le livrable : un jugement, pas une galerie

Écrire `.maestro/relecture/<iid>/jugement.md`, **et le reprendre dans le résumé
de la session** — le fichier vit dans un worktree que le merge fera ramasser.
Il **commence par le regard neuf**, recopié de `regard.md` au caractère près —
la grille, puis la confrontation au rendu attendu et aux décisions prises —, et
c'est la grille qui fait foi : `relecture-note` refuse (`5`) un jugement dont
une ligne manque ou reste sans réponse ✓, ✗ ou « non vu ».

Suivent tes trois sections, et les trois sont obligatoires :

- **ce qui va** — en une ligne ou deux. Pas un compte rendu des captures : ce
  que la grille a vu et qui tient.
- **ce qui cloche** — **chaque ✗ de la grille**, avec son écran, son thème, et ce
  qu'on en fait : corrigé ici, à ouvrir en ticket, ou contesté sur pièces
  (étape 5). Un constat sans suite est un constat perdu, et un ✗ passé sous
  silence est un jugement réécrit.
- **ce que je n'ai pas pu voir** — les « non vu » de la grille, les
  `indéterminé` du plan, un écran qui n'a pas chargé, un état que la démo ne
  sert pas (une largeur téléphone) ou qu'on n'a pas ouvert, **nommé avec son
  écran**, un **avant indisponible** avec la cause que le script a donnée.
  C'est la section qui distingue un jugement d'un ✓ : *ne pas avoir regardé
  n'est pas avoir trouvé que tout va bien.*

Et sous les trois, **la couverture des états** : le tableau de
`--couverture <iid>` recopié tel quel, écran par écran. C'est ce qui dit
lesquels ont été vus sans qu'on ait à le reconstituer de la prose. Une
case « — » sur un état que le ticket demandait doit se retrouver, nommée, dans
« ce que je n'ai pas pu voir ».

**Puis la planche**, pour qu'une personne **voie** ce que le texte juge — `gh` ne
sait pas joindre une image à un commentaire, et le jugement consigné reste du
texte :

```bash
bash scripts/design/relecture-visuelle.sh --planche <iid>
```

Elle écrit `.maestro/relecture/<iid>/planche.html` : un fichier **autonome**
(captures en `data:`, deux thèmes, visionneuse — la mécanique de
`scripts/presentation/build.py`, reprise par import), le jugement en tête, puis
chaque état et chaque écran avec l'avant et l'après côte à côte. Rien n'est
envoyé à la forge. Elle est **recopiée dans le clone principal**, au même
chemin : `/ticket-finish` ramasse le worktree juste après le merge, avant son
résumé, et une planche laissée là serait un lien mort. **Nomme dans le résumé**
de la session le chemin de la dernière ligne, `PLANCHE <chemin>` — c'est cette
copie-là. Elle se rejoue après une correction comme la saisine, et un plafond de
taille la borne (`MAESTRO_RELECTURE_PLANCHE_MAX`, 25 Mio) — une capture écartée
y est nommée.

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
Il **garde la grille** : un jugement sans elle est refusé (`5`) avant toute
lecture de la forge, et la liste des lignes manquantes est imprimée. La réponse
n'est jamais de les remplir toi-même — c'est rejouer le regard neuf. Une
`--raison` n'en porte pas : rien n'a été regardé.

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

**Ce que l'avant ajoute** (#977), mesuré le 2026-09-17 sur le même poste, avec
un écran existant et un écran nouveau :

| Étape | Avant #977 | Avec l'avant |
| --- | --- | --- |
| `--plan` | ~2 s | **3,2 s** (lecture des pages d'`origin/main`) |
| préparation, premier montage | ~18 s | **57 s** — dont 38 à 48 s pour l'avant (worktree ~2 s, `npm ci` ~33 s, stack ~9 s) |
| préparation rejouée, avant déjà là | ~18 s | **40 s** — l'avant en 22 s, sans réinstaller |
| par écran existant et par thème | ~4 s | ~8 s (la paire) |
| `--fin` | 6 s | **17,6 s** (deux stacks, puis le retrait de l'avant) |

Soit **~50 s de plus** par relecture, plus ~4 s par écran et par thème, et
~500 Mo de disque le temps de la relecture. `MAESTRO_RELECTURE_AVANT=0` les
économise, au prix de juger l'après sans référence.

**Ce que le regard neuf ajoute** (#980), mesuré le 2026-09-17 sur un écran,
nominal, les deux thèmes avec leur avant (4 captures), en session `claude -p`
sous le régime de run :

| Étape | Coût |
| --- | --- |
| `--saisine` | **6,6 s** (le plan, plus un aller vers la forge) |
| sous-agent `regard-neuf` | **105 s et 1,29 $** (Opus, session appelante comprise) — il lit la saisine puis chaque capture |
| `--planche` | **3,3 s**, **329 Ko** pour 4 captures (~80 Kio chacune en `data:`) |

Le sous-agent coûte au nombre de captures qu'il ouvre : c'est le poste qui
grandit avec les états limites, et c'est pourquoi il n'y a **qu'un** regard par
relecture, pas un par écran.

## En session de run

**Rien ne change**, et c'est le propre de ce geste. Tout ce qui précède est
jouable : `mcp__chrome-maestro` passe déjà l'union des deux allowlists, et
`bash scripts/controltower/start.sh` comme
`bash scripts/design/relecture-visuelle.sh` sont dans celle du run (#932). Rien
ici ne demande le web : la relecture regarde **ce qu'on a écrit**, là où
`/design-veille` cherche ce que d'autres ont fait — un accès que #933 a depuis
ouvert aux deux régimes, et dont celui-ci n'a de toute façon pas besoin.

**L'outil `Agent` non plus n'est soumis à aucune règle** : essayé le
2026-09-17 sous `settings.run.json` et `--permission-mode acceptEdits`, une
session a appelé le sous-agent `regard-neuf`, qui a lu la saisine et ses quatre
captures — **zéro refus**. Il n'y avait donc rien à instruire (docs/10 §11.7).
Une session de run démarre sur le worktree : l'agent de projet y est chargé.

Le jugement, lui, n'a personne pour le lire à l'écran : le consigner sur le
ticket est donc la seule façon qu'il survive — et la planche, que personne
n'ouvrira pendant le run, reste nommée dans le résumé pour qui reprendra.
