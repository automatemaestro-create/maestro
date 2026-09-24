---
name: relecture-visuelle
description: Regarder le rendu des écrans qu'un ticket a touchés — les deux thèmes, dans un vrai navigateur — et en rendre un jugement écrit, avant de clore
---

# La relecture visuelle

Répond à **une** question : *à quoi ça ressemble, et est-ce que ça a l'air juste ?* Les autres
outils ne regardent pas : `npm test` (jsdom) ne peint aucun pixel, les filets de contraste, d'a11y
et de sobriété jugent des **règles**, `/banc-mise-en-page` la **géométrie**, `/verify` le
**câblage**. **Ne les redouble pas** : une hauteur suspecte, un contraste douteux, une WebSocket
muette se **nomment** ici et se mesurent là-bas.

`/ticket-finish` ouvre ce skill à son étape 4bis quand `--plan` rend des écrans, avant le filet CI ;
on ne l'appelle pas de soi-même, et on ne demande rien — à l'identique en run et en interactif. Le
pourquoi de chaque geste, ses mesures et son histoire vivent en
[docs/30 §5.5 à §5.8](../../../docs/30-cible-visuelle-control-tower.md).

## La séquence

### 1. Demander le plan (et souvent s'arrêter là)

```bash
bash scripts/design/relecture-visuelle.sh --plan <iid>
```

Le script dérive les écrans **du ticket** — commits de la branche et travail non commité —, puis
remonte des composants partagés vers les écrans qui les affichent. On ne nomme aucun écran à la main.

- **code `3` — aucune surface visible** : une réponse, pas une panne. On ne monte rien, on passe.
- **code `0` avec des écrans** : on continue. Chaque écran porte sa ligne **`avant :`** — l'URL où
  `origin/main` le sert, ou **`écran NOUVEAU`**, qui n'a pas d'avant et qu'on ne va pas chercher.
- **une section `indéterminé`** : des fichiers sans route dérivable (la coquille, un composant que
  personne n'importe). Ils vont à « ce que je n'ai pas pu voir », un par un ; si c'est le shell ou la
  coquille, ouvre **un** écran quelconque en plus.

**Le plan dit aussi le régime** (#1243), et d'où il vient — il décide de tout ce qui suit :

| Régime | Le ticket… | Ce qu'on regarde | Qui juge |
| --- | --- | --- | --- |
| `decide` | **décide** d'un écran (critère du §7.2 de `/design-veille`) | l'**avant et l'après**, les **trois** états, les deux thèmes | le **regard neuf** (étape 5) |
| `applique` | **applique** une décision déjà prise | l'**après seul** — aucune seconde stack —, les **états qu'il nomme** (le défaut sinon), les deux thèmes | **la session** (étape 5) |

Le script lit un **acte**, jamais le texte : une décision consignée sur le ticket (un commentaire
qui commence par `## Veille de conception` ou `## Variante retenue`) → `decide`, aucune →
`applique`, un ticket illisible → `decide`. **Tu peux l'imposer** (`--regime decide|applique`, sur
tout appel sauf `--fin`) quand ton jugement diffère de l'acte ; la préparation le consigne, et la
suite le relit.

### 2. Monter la stack

```bash
bash scripts/design/relecture-visuelle.sh <iid>
```

Même plan, plus les **ports du worktree** (jamais 8000/3000) et la **vraie stack** — l'API réelle sur
Redis et l'UI, sans navigateur (`start.sh --etat-banc --no-browser`). Par défaut, l'état `peuple` :
l'état réel du dernier passage du banc, rouvert sans rien rejouer, sur un jeu de données à part. Le
lanceur dit son **âge** ; il ne se rafraîchit pas d'office (`start.sh --etat-banc --rejouer` est un
geste à part, vrai modèle, des dizaines de minutes). Sans état sur le poste, le script le dit et
nomme ce geste.

**Le projet actif vient de l'API** : la préparation liste les projets servis, chacun avec son nombre
de runs — pose celui dont l'écran a quelque chose à montrer (étape 3).

**Puis l'avant — en régime `decide` seulement** : une seconde stack, servie d'un worktree détaché
sur `origin/main` (`<iid>.avant`), sur les ports de l'après **+ 200**, dans **le même état**. Le
worktree du ticket n'est jamais touché. Best-effort : un avant en échec se dit et va à « ce que je
n'ai pas pu voir » (`MAESTRO_RELECTURE_AVANT=0` l'éteint). En `applique`, aucun avant n'est monté.

> ⚠ `--no-browser` doit rester dans le script : sans lui `start.sh` ouvre sa propre fenêtre et
> arrête la stack quand elle se ferme (#149).

### 3. Neutraliser ce qui s'interpose, puis poser le thème

> ⚠ **La visite guidée s'ouvre d'elle-même sur un profil neuf** (#122) et son voile **absorbe les
> clics** : la neutraliser **avant** de naviguer.

Le MCP n'a pas d'`addInitScript` : **deux passes**, la première pose les clés, la seconde regarde.

```
browser_navigate  http://localhost:<PORT_UI>/
browser_evaluate  () => {
                    localStorage.setItem("maestro.guide.vu", "1");
                    localStorage.setItem("maestro.theme", "clair");   // ou "sombre"
                    localStorage.setItem("maestro.projet.actif", "<id nommé par la préparation>");
                  }
```

> ⚠ **Une fois PAR ORIGINE** : l'après (`<PORT_UI>`) et l'avant (`<PORT_UI + 200>`) ont chacun leur
> `localStorage`. L'identifiant du projet peut différer d'une origine à l'autre (le projet neuf de
> `vide`) : la préparation nomme les deux.

### 4. Regarder — chaque écran, dans les deux thèmes

**Un thème à la fois, tous les écrans, puis l'autre** ; le thème s'applique au chargement, donc
après une navigation.

```
browser_navigate         http://localhost:<PORT_UI>/<route>
browser_take_screenshot  filename: .maestro/relecture/<iid>/<ecran>-<theme>.png
browser_navigate         <URL de la ligne « avant : » du plan>
browser_take_screenshot  filename: .maestro/relecture/<iid>/<ecran>-<theme>-avant.png
```

L'après et l'avant se jugent **en paire**, même écran, même thème : ce qui a changé, et si le
changement a abîmé ce qui allait. Un écran nouveau n'a que son après. En régime **`applique`**, les
deux lignes `-avant` ne se jouent pas : chaque capture se juge seule, et l'avant non monté va à « ce
que je n'ai pas pu voir ».

> ⚠ **Attendre que CHAQUE page soit prête avant de la capturer** : le `<main
> id="contenu-principal">` du shell présent, ni « Reconnexion… » ni « Chargement » à l'écran — par
> un `browser_evaluate` qui interroge la page jusqu'à ce qu'il tienne, jamais un délai fixe.

> ⚠ **On compare la mise en page et le rendu, jamais les valeurs** : un âge relatif, un identifiant
> ou une horloge diffèrent de l'avant à l'après sans que le ticket y soit pour rien.

> ⚠ **Le `filename` se donne en chemin RELATIF** : le contrôle des racines du MCP compare les chemins
> littéralement (`e:/…` refusé face à `E:/…`). Le relatif atterrit dans `.maestro/`, gitignoré.

Puis **relis chaque capture** (`Read`) : prête, ni « Chargement… » ni visite guidée, le bon thème.
Une capture ratée se reprend ici. Les deux thèmes, **toujours** : l'erreur la plus probable est dans
celui où l'on ne développe pas. `<ecran>` est la **clé** de l'écran (`/` → `accueil`, `/couts` →
`couts`), celle que `--couverture` attend.

### 4bis. Les autres états — là où le rendu casse

Une file vide, une panne, une liste longue : chacun vient de la **vraie stack**, jamais d'un
scénario factice, et le script le monte :

```bash
bash scripts/design/relecture-visuelle.sh <iid> --etat <nom>
```

| État | Ce que la vraie stack sert | Ce qu'on y cherche |
| --- | --- | --- |
| `peuple` | le défaut : l'état du dernier passage du banc — ses runs, ses fils, ses projets, et la **charge** qu'il a laissée | le rendu de ce que le produit a vraiment fait ; ce qui **déborde** |
| `vide` | une **stack neuve** (`start.sh --etat-neuf`), puis un **projet neuf** déclaré par l'API | un état vide qui **explique** et propose la suite, pas un cadre blanc |
| `injoignable` | l'API **coupée** sous la stack montée (`start.sh --couper-api`), l'UI encore servie | la panne **nommée**, lisible dans les deux thèmes, sans casser la mise en page |

**Ce que la vraie stack ne produit pas se nomme, jamais ne s'imite** ; demandé par `--etat`, il est
refusé avec sa raison :

| État non couvert | Pourquoi |
| --- | --- |
| `erreur` | une API qui **répond en erreur** : depuis #1206 la vraie stack la produit (magasin coupé, lectures refusées en 503), mais le script ne la monte pas encore |
| `charge` | au-delà de ce que le passage du banc a laissé, rien n'est gonflé |

Ils vont à « ce que je n'ai pas pu voir », nommés — une largeur téléphone de même.

**Quels états ouvrir** : ceux que la rubrique « États à couvrir » du ticket nomme (section
`## Rendu attendu`), rapprochés par jugement (« aucune donnée » est `vide`, « API en panne » est
`injoignable`, « contenu long » la charge de `peuple`). Sans rubrique : en régime **`decide`**, **les
trois** ; en régime **`applique`** (#1243), **le défaut seul**. Le script compte les états que tu as
**montés** ou capturés, et nomme les autres « non demandés ».

**Comment.** `peuple` et `vide` **redémarrent** la stack ; le `localStorage` survit (même origine),
**sauf le projet actif de `vide`**, neuf : pose l'identifiant nommé. Les captures d'un état vont dans
son sous-dossier :

```
browser_take_screenshot  filename: .maestro/relecture/<iid>/<etat>/<ecran>-<theme>.png
browser_take_screenshot  filename: .maestro/relecture/<iid>/<etat>/<ecran>-<theme>-avant.png
```

**`injoignable` ne monte rien, il coupe** : monte d'abord `peuple` (ou `vide`), **ouvre un écran**,
puis joue `--etat injoignable`. L'écran passe en « Reconnexion… » ; passe d'un écran à l'autre **par
le menu** (`browser_click`), chacun montre la bannière « API injoignable ». **Jamais par l'URL** : un
rechargement laisse le shell sur sa porte « Choisir le projet », à capturer **une** fois. Attends la
bannière (`browser_wait_for` sur « injoignable »), et dans l'état peuplé un **contenu** attendu. Un
écran qui paraît cassé se recapture avant de devenir un constat. Remonter un état rétablit l'API.

**Puis compte ce qui a été vu**, écran par écran :

```bash
bash scripts/design/relecture-visuelle.sh --couverture <iid>
```

Il croise écrans, états et thèmes avec les captures sur le disque, sans rien démarrer ni juger. Il
voit une capture, jamais un regard : une capture non relue ne compte pas, à toi de le tenir.

### 5. Qui juge : le regard neuf pour un ticket qui décide, la session sinon (#980, #1151)

**Le juge suit le régime.** D'abord la saisine — les stacks peuvent rester montées :

```bash
bash scripts/design/relecture-visuelle.sh --saisine <iid>
```

Elle écrit `.maestro/relecture/<iid>/saisine.md` : les captures en chemins absolus, la section
« Rendu attendu », les commentaires qui **commencent** par `## Veille de conception` ou `## Variante
retenue`, la **grille** (`scripts/design/grille-relecture.tsv`, seul endroit où elle s'écrit) et le
gabarit. Sa dernière ligne est `SAISINE <chemin>`. Une veille consignée **avant** l'ancre s'ajoute
par `--partis-pris <fichier>`, recopiée telle quelle.

En régime **`applique`**, **la session juge** : remplis toi-même le gabarit dans
`.maestro/relecture/<iid>/regard.md`, **sous les titres qu'elle te donne** (`### Regard de la
session — …`), ligne à ligne sur la grille. Ce titre dit, sur le ticket, que la session a jugé
l'après seul : ne le renomme pas.

En régime **`decide`**, le sous-agent `regard-neuf` (outil `Read` seul) : il ne reçoit que les
captures, le rendu attendu et les décisions prises — **ni le code, ni le diff, ni ton raisonnement**
—, et **son prompt est cette phrase, au mot près** :

```
Agent  subagent_type: "regard-neuf"
       description:   "Regard neuf sur #<iid>"
       prompt:        "Ta saisine : <chemin de la ligne SAISINE> — lis-la, puis rends-la remplie."
```

Sa réponse va **telle quelle** dans `.maestro/relecture/<iid>/regard.md` (`Write`) : les sections
`### Regard neuf — …`, que tu ne retouches pas.

> ⚠ **Un agent de projet se charge au démarrage de la session** : une session ouverte avant
> `.claude/agents/regard-neuf.md` répond « Agent type 'regard-neuf' not found ». Dans ce cas
> seulement, `subagent_type: "general-purpose"` et pour prompt « Suis la consigne de
> `<racine>/.claude/agents/regard-neuf.md` (ignore son en-tête), puis : ta saisine : <chemin> —
> lis-la, puis rends-la remplie. » — et **nomme ce repli** dans le jugement.

**Ce que tu fais des ✗**, dans « ce qui cloche », chacun avec sa suite : **corrigé ici** (puis
nouvelles captures, nouvelle saisine, nouveau regard), **ticket à ouvrir**, ou **contesté sur
pièces** (la capture relue, la ligne de code) — jamais retiré de la grille. *Tu es l'auteur : « ce
n'est pas ce que je voulais faire » n'est pas une pièce.*

### 6. Fermer, puis rendre

> ⚠ `browser_close` **à la fin de chaque séquence** : Chrome n'accepte qu'un consommateur par
> profil.

```bash
bash scripts/design/relecture-visuelle.sh --fin
```

Arrête les stacks, retire le projet neuf et le worktree de l'avant — **y compris après une relecture
ratée** : une stack oubliée tient un port, un avant oublié pèse ~500 Mo.

## Le livrable : un jugement, pas une galerie

Écris `.maestro/relecture/<iid>/jugement.md`, et reprends-le dans le résumé de la session. Il
**commence par le regard** — neuf, ou de la session —, recopié de `regard.md` au caractère près : la
grille fait foi, et `relecture-note` refuse (`5`) un jugement dont une ligne manque ou reste sans
réponse ✓, ✗ ou « non vu ». Suivent trois sections, obligatoires :

- **ce qui va** — une ligne ou deux : ce que la grille a vu et qui tient ;
- **ce qui cloche** — **chaque ✗**, avec son écran, son thème et sa suite ; un ✗ passé sous silence
  est un jugement réécrit ;
- **ce que je n'ai pas pu voir** — les « non vu », les `indéterminé`, un écran qui n'a pas chargé,
  chaque état non couvert avec sa raison, un état non ouvert **avec son écran**, un avant
  indisponible avec sa cause, l'**âge** de l'état quand il n'atteint pas l'écran ; en régime
  **`applique`**, l'avant non monté et les états « non demandés ». *Ne pas avoir regardé n'est pas
  avoir trouvé que tout va bien.*

Puis **la couverture** : la sortie de `--couverture <iid>` recopiée telle quelle, **pied compris**.

**Puis la planche**, pour qu'une personne **voie** ce que le texte juge :

```bash
bash scripts/design/relecture-visuelle.sh --planche <iid>
```

`.maestro/relecture/<iid>/planche.html`, autonome (captures en `data:`, plafond
`MAESTRO_RELECTURE_PLANCHE_MAX`, 25 Mio), recopiée **dans le clone principal** au même chemin — le
worktree sera ramassé après le merge. Rien ne part vers la forge. **Nomme au résumé** la dernière
ligne, `PLANCHE <chemin>`.

**Puis consigne-le sur le ticket — toujours**, même quand tout va : c'est ce qui sépare « regardé,
rien à signaler » de « personne n'y a pensé » (#935).

```bash
bash scripts/gitlab/lib.sh relecture-note <iid> .maestro/relecture/<iid>/jugement.md
```

Relecture qui n'a pas eu lieu (stack qui ne démarre pas, écran inatteignable) : écris sa raison et
enregistre-la, plutôt que de la taire :

```bash
bash scripts/gitlab/lib.sh relecture-note --raison <iid> <fichier-de-la-raison>
```

Le verbe est idempotent, et **garde la grille** : un jugement sans elle est refusé (`5`) avant toute
lecture de la forge. Pour un ticket qui décide d'un écran, on répare en rejouant le regard neuf ;
pour tout autre, en complétant sa grille. Une `--raison` n'en porte pas.

## Le prix, et le run

Le prix s'annonce plutôt qu'il ne se masque : `--plan` ~2 s, une stack ~20 à 30 s, ~4 s par écran
et par thème, `--fin` quelques secondes ; en régime `decide`, l'avant (~1 min au premier montage),
chaque autre état (~1 min) et le regard neuf (~2 min, ~1,3 $). Mesures datées : docs/30 §5.6 et §5.8.

En session de run, **rien ne change** : `mcp__chrome-maestro`, `start.sh` et
`relecture-visuelle.sh` sont dans son allowlist, l'outil `Agent` n'est soumis à aucune règle, et
rien ici ne demande le web. Le jugement consigné sur le ticket est ce qui en survit.
