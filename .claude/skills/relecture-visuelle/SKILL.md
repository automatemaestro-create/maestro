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
de la stack d'à côté) et la **vraie stack** — l'API réelle sur Redis, l'UI, sans
navigateur (`start.sh --etat-banc --no-browser`).

**Ce qu'elle sert, par défaut, est l'état `peuple`** : l'état réel que le
dernier passage du banc des scénarios a laissé (#1148), rouvert par l'API
réelle sans rien rejouer (#1164). Plus de scénario factice (#1165, docs/41 §4) :
il montrait ce qu'on avait scénarisé, pas ce que le produit fait. Le
lanceur dit **l'âge** de cet état et ce qu'il contient ; il se sert sur un jeu
de données à part (`<espace>.banc`), jamais sur les données de ta copie.

- **Son âge ne se corrige pas d'office.** Un passage se rejoue à la demande —
  `bash scripts/controltower/start.sh --etat-banc --rejouer`, vrai modèle, des
  dizaines de minutes —, jamais au détour d'une relecture. Un écran que l'état
  n'atteint pas va à « ce que je n'ai pas pu voir », avec l'âge.
- **Aucun état sur le poste** : le script le dit, nomme ce geste et s'arrête sur
  cet état ; les autres restent montables.

**Le projet actif vient de l'API.** Sans lui, le shell ne rend que sa porte
d'entrée (#279). La préparation liste les projets que la stack sert, chacun avec
son nombre de runs — l'état du banc en porte un par scénario joué : pose celui
dont l'écran a quelque chose à montrer (étape 3).

Les autres états se montent par le même geste, un état à la fois — voir
l'étape 4bis.

**Puis l'avant** (#977) : une **seconde stack**, servie depuis un worktree
**détaché sur `origin/main`** (`<iid>.avant`, monté par `worktree.sh avant`),
sur les ports de l'après **+ 200**, et dans **le même état** — rouvert du même
passage du banc. Un état que le lanceur d'`origin/main` ne sait pas servir n'a
pas d'avant, et le script le dit. Le worktree du ticket n'est
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
                    localStorage.setItem("maestro.projet.actif", "<id nommé par la préparation>");
                  }
```

> ⚠ **Une fois PAR ORIGINE.** L'après (`localhost:<PORT_UI>`) et l'avant
> (`localhost:<PORT_UI + 200>`) sont deux origines, donc deux `localStorage` :
> des clés posées sur l'une n'existent pas sur l'autre, et l'avant s'ouvrirait
> sur la visite guidée, dans le mauvais thème, sans projet. **L'identifiant peut
> différer d'une origine à l'autre** : l'état du banc porte les mêmes projets des
> deux côtés (« [avant] les mêmes »), mais le projet neuf de l'état `vide` est
> déclaré par chaque API, et la préparation nomme les deux.

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

> ⚠ **On compare la mise en page et le rendu, jamais les valeurs.** Les deux
> stacks servent le même état, mais pas au même instant : un âge relatif
> (« il y a 4 j »), un identifiant de projet ou une horloge diffèrent de l'avant
> à l'après sans que le ticket y soit pour rien.

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

### 4bis. Les autres états — là où le rendu casse (#978, sur la vraie stack depuis #1165)

L'état peuplé est rarement celui qui casse. C'est **une file vide** sans
explication, **une panne** qui déborde, **une liste longue**. Chacun vient de la
**vraie stack** — jamais d'un scénario factice —, et le script le monte :

```bash
bash scripts/design/relecture-visuelle.sh <iid> --etat <nom>
```

| État | Ce que la vraie stack sert | Ce qu'on y cherche |
| --- | --- | --- |
| `peuple` | le défaut : l'état du dernier passage du banc, rouvert par l'API réelle — ses runs, ses fils, ses projets, et la **charge** qu'il a laissée | le rendu de ce que le produit a vraiment fait ; ce qui **déborde** de ce qu'il contient |
| `vide` | une **stack neuve** (`start.sh --etat-neuf`), puis un **projet neuf** déclaré par l'API — rien n'y a été lancé | un état vide qui **explique** et propose la suite, pas un cadre blanc |
| `injoignable` | l'API **coupée** sous la stack montée (`start.sh --couper-api`), l'UI encore servie — la vraie panne (#996) | la panne **nommée**, lisible dans les deux thèmes, sans casser la mise en page |

**Et ce que la vraie stack ne produit pas se nomme, jamais ne s'imite.** Le plan,
la couverture et la saisine les portent avec leur raison ; demandés par
`--etat`, ils sont refusés avec elle :

| État non couvert | Pourquoi |
| --- | --- |
| `erreur` | une API qui **répond en erreur** (500) : mesuré le 2026-09-22, son magasin (Redis) coupé, au démarrage comme en route, la vraie API dit « ok » et sert des listes vides — aucune lecture d'écran ne rend 500 (ce silence est #1206) |
| `charge` | au-delà de ce que le passage du banc a laissé (des centaines de lignes, des noms de 80 caractères) : rien n'est gonflé |

Ils vont à « ce que je n'ai pas pu voir », nommés — le pied de `--couverture`
les porte, et c'est lui que le jugement recopie. Une largeur téléphone, ou tout
état qu'aucun geste ne sert, y va de même.

**Quels états ouvrir.** Ceux que la rubrique **« États à couvrir »** du ticket
nomme (section `## Rendu attendu`, #976), rapprochés des noms ci-dessus par
jugement (« aucune donnée » est `vide`, « API en panne » est `injoignable`,
« contenu long » est la charge de `peuple` — ou un non couvert, s'il faut plus
que ce que le passage a laissé). Quand le ticket ne les nomme pas (section
absente ou « non renseigné »), on ouvre **les trois** : un état jamais ouvert
est un état que personne ne regarde.

**Comment.** `peuple` et `vide` **redémarrent** la stack : ce sont des données
que l'API rejoue à son démarrage. Le `localStorage` posé à l'étape 3 survit au
redémarrage (même origine) — **sauf le projet actif de `vide`**, qui est neuf :
pose l'identifiant que la préparation vient de nommer. Les captures d'un état
vont **dans son sous-dossier** :

```
browser_take_screenshot  filename: .maestro/relecture/<iid>/<etat>/<ecran>-<theme>.png
browser_take_screenshot  filename: .maestro/relecture/<iid>/<etat>/<ecran>-<theme>-avant.png
```

L'avant suit l'état : la préparation le relance dans le même, ou dit que le
lanceur d'`origin/main` ne sait pas le servir — il n'y a alors que l'après.

**`injoignable` ne monte rien : il coupe.** Monte d'abord `peuple` (ou `vide`),
**ouvre un écran**, puis joue `--etat injoignable` : l'API des deux stacks tombe
net, l'UI reste servie, rien n'est soldé. L'écran ouvert passe en
« Reconnexion… » — attendue : c'est la WebSocket coupée —, puis **passe d'un
écran à l'autre par le menu** (`browser_click` sur son lien) : chacun montre la
bannière « API injoignable — rien n'a répondu » (vérifié le 2026-09-22 sur
`/couts`). **Jamais par l'URL** : une navigation recharge la page, et le shell,
sans API pour confirmer son projet, reste sur sa porte « Choisir le projet » —
qui montre sa propre panne, à capturer **une** fois. Remonter un état
(`relecture-visuelle.sh <iid>`) rétablit l'API.

Les deux thèmes valent ici comme ailleurs, et on relit chaque capture avec
`Read`, comme à l'étape 4. La bannière de panne n'arrive qu'**après** la requête
en échec : on attend qu'elle soit là (`browser_wait_for` sur « injoignable »)
avant de capturer, jamais un délai fixe. De même dans l'état peuplé, **le
rendu prend du temps** : on attend un **contenu** que l'écran doit afficher. Un
écran qui paraît cassé à la première capture se recapture avant de devenir un
constat.

**Puis compter ce qui a été vu, écran par écran** :

```bash
bash scripts/design/relecture-visuelle.sh --couverture <iid>
```

Le script croise les écrans du plan, les états et les deux thèmes avec les
captures présentes sur le disque. Il ne démarre rien et ne rend aucun verdict.
Il ne sait voir qu'une **capture**, jamais un regard : une capture qu'on n'a
pas relue ne compte pas, et c'est à toi de le tenir.

### 5. Qui juge : le regard neuf pour un ticket qui décide, la session sinon (#980, #1151)

**Un ticket qui décide d'un écran** (critère du §7.2 de `/design-veille`) est jugé
par le regard neuf, comme ci-dessous. **Tout autre ticket** — correctif,
alignement, suite d'une direction consignée — est jugé par **la session
elle-même** (#1151, docs/40 §3) : joue la saisine comme ci-dessous, puis remplis
toi-même son gabarit dans `.maestro/relecture/<iid>/regard.md`, sous le titre
`### Regard de la session — #<iid>`, **ligne à ligne sur la grille**, et dis
d'emblée que c'est la session qui a jugé. La grille ne change pas, `relecture-note`
la garde de la même façon, et un ✗ se traite de même (corrigé, ticket, contesté
sur pièces). Ce qui change est le prix : ni sous-agent, ni 105 s, ni 1,29 $ par
ticket de finition.

**Pour un ticket qui décide, l'auteur voit ce qu'il a voulu faire ; il faut
quelqu'un qui voie ce qu'il a produit.** Le jugement est rendu par le sous-agent `regard-neuf`
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

Arrête **les deux** stacks, retire le dossier du projet neuf et le worktree de l'avant —
**y compris quand la relecture s'est mal passée** : une stack laissée derrière
tient un port pour le ticket suivant, et un avant oublié pèse ~500 Mo. S'il en
reste un malgré tout (session coupée avant `--fin`), le montage d'avant suivant
sur ce poste le ramasse dès que son ticket n'a plus de worktree.

## Le livrable : un jugement, pas une galerie

Écrire `.maestro/relecture/<iid>/jugement.md`, **et le reprendre dans le résumé
de la session** — le fichier vit dans un worktree que le merge fera ramasser.
Il **commence par le regard** — neuf, ou de la session (étape 5) —, recopié de `regard.md` au caractère près —
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
  `indéterminé` du plan, un écran qui n'a pas chargé, **chaque état non
  couvert** avec sa raison (le pied de `--couverture`), un état qu'aucun geste
  ne sert (une largeur téléphone) ou qu'on n'a pas ouvert, **nommé avec son
  écran**, un **avant indisponible** avec la cause que le script a donnée, et
  l'**âge** de l'état du banc quand il n'atteint pas l'écran touché.
  C'est la section qui distingue un jugement d'un ✓ : *ne pas avoir regardé
  n'est pas avoir trouvé que tout va bien.*

Et sous les trois, **la couverture des états** : la sortie de
`--couverture <iid>` recopiée telle quelle, écran par écran, **son pied
compris** — les états non couverts et leur raison. C'est ce qui dit lesquels
ont été vus, et lesquels ne pouvaient pas l'être, sans qu'on ait à le
reconstituer de la prose. Une case « — » sur un état que le ticket demandait
doit se retrouver, nommée, dans « ce que je n'ai pas pu voir ».

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
lecture de la forge, et la liste des lignes manquantes est imprimée. Pour un
ticket qui décide d'un écran, la réponse n'est jamais de les remplir toi-même —
c'est rejouer le regard neuf ; pour tout autre, c'est compléter ta grille. Une
`--raison` n'en porte pas : rien n'a été regardé.

## Le prix, annoncé plutôt que masqué (règle de #418)

Mesuré le 2026-09-11, worktree déjà installé, poste de référence :

| Étape | Coût |
| --- | --- |
| `--plan` | ~2 s (lecture git seule, aucune stack) |
| montage de la stack | **18 s** |
| par écran et par thème | ~4 s (une navigation, une capture) |
| par autre état (`--etat`, #1165) | un redémarrage des deux stacks, puis ~4 s par écran et par thème ; `injoignable` ne redémarre rien (une coupure, ~2 s) |
| `--couverture` | ~2 s (lecture git et disque, aucune stack) |
| `--fin` | **6 s** |

Soit ~50 s pour trois écrans dans l'état par défaut, hors le temps de regarder.
Ce sont des estimations, pas des mesures : aucune relecture complète n'a encore
été chronométrée. **Sur la vraie stack** (#1165), mesuré le 2026-09-22 sur un
écran, poste de référence : préparation de l'état `peuple` avec un avant monté
pour la première fois **95 s** (dont l'avant 61 s), passage à `vide` **69 s**
(l'avant refusé par un `origin/main` d'avant #1165), `--fin` **27 s** — le
retrait du projet neuf et de l'avant compris. C'est pourquoi
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
dans l'état par défaut, les deux thèmes avec leur avant (4 captures), en session `claude -p`
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
