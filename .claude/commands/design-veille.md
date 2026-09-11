---
description: Cherche comment les produits comparables rendent une surface donnée, puis en tire des partis pris tenables dans le socle — avant d'écrire une ligne d'interface
argument-hint: "<surface>  (un écran, un composant ou un motif : « la carte d'un run », « /couts », « la barre d'avancement »)"
allowed-tools: WebSearch, WebFetch, Read, Grep, Glob, Write, Bash(bash:*), Bash(git:*), mcp__chrome-maestro
---

Commande **de recherche** : pour la surface `$ARGUMENTS`, tu vas chercher dehors comment les
produits comparables la rendent aujourd'hui, puis tu en tires **3 à 5 partis pris applicables**,
chacun rattaché à sa référence et **tenable dans le socle du dépôt**. Tu n'écris ni code, ni
ticket : tu rends une décision, et tu proposes la suite.

Elle se joue **des deux côtés** — session interactive, et depuis #934 **session de run**. Le régime
n'y change que sur deux points, et le §7 les porte tous les deux : **qui choisit la surface**, et
**ce qu'on fait de la décision** une fois rendue. La méthode des §1 à §6, elle, est la même : ce qui
n'est pas vérifié n'est pas cité, le socle se relève avant la recherche, aucune identité nouvelle.

Si `$ARGUMENTS` est vide, demande la surface — **en session de run, personne ne répondra : dérive-la
(§7)**. « Le design de la Control Tower » n'en est pas une : il faut un écran (`/couts`), un
composant (`CarteRun`) ou un motif (« la barre d'avancement d'un run »). Une veille sans objet rend
une galerie ; une veille sur une surface rend une décision.

## Ce que cette commande est, et ce qu'elle n'est pas

Le dépôt a déjà fait ce travail **une fois** : le banc de #471
([docs/30 §1](../../docs/30-cible-visuelle-control-tower.md)) a capturé quatre produits en direct et
en a tiré les trois manques du produit — hiérarchie typographique franche, état porté par la
**forme** autant que par la couleur, place fixe pour les métadonnées. Ce banc n'était rejouable par
personne : il vit en prose, daté, et rien ne le refait pour la surface qu'on retouche aujourd'hui.

Cette commande est **ce geste-là, à l'échelle d'une surface**. Elle vient donc **en tête** de la
chaîne d'outillage de docs/30 §5.1 : les tokens, les primitives, les tests et `banc-mise-en-page`
**gardent** ce qu'on a tenu — aucun d'eux ne dit ce qu'on vise. Elle ne les remplace pas et ne les
rejoue pas : elle ne mesure ni contraste, ni géométrie, ni accessibilité.

⚠ **Elle ne cherche pas un style.** Le verdict du banc de #471 est écrit et il tient : *« Aucun des
quatre ne doit son niveau à une identité graphique forte. Il n'y a pas de style à aller chercher —
il y a un socle à tenir. »* La direction retenue (docs/30 §6.1) est « **le même produit, avec du
relief** ». Une veille qui reviendrait avec une palette neuve, une police de marque ou un parti
esthétique se serait trompée de question.

## 1. Cadrer la surface, avant de sortir

- **Où elle vit** : trouve le ou les fichiers (`apps/web/components/**`, `apps/web/app/**`) et
  **quels écrans la montent**. Une brique partagée — `CarteRun` est rendue par trois écrans — se
  juge sur les trois, jamais sur celui d'où vient la demande.
- **Ce qu'elle doit dire**, en une phrase : la question à laquelle un coup d'œil doit répondre.
  C'est elle qui tranchera plus bas ; sans elle, tout ce qu'on trouvera dehors paraîtra bon à
  prendre.
- **Ce qui ne va pas aujourd'hui**, en faits : ce que le ticket rapporte, ce qu'une capture montre,
  ce que le code fait. Si la surface est visible en local, regarde-la — la stack de démo se monte
  par le skill `control-tower` (`--demo`), sur les ports que `worktree.sh ensure` a annoncés pour ce
  worktree.

## 2. Relever le socle — **avant** d'aller chercher, jamais après

C'est l'ordre qui fait la différence entre une veille et une galerie : ce qu'on relève ici est la
**contrainte** que les références devront passer, et non un filtre appliqué à des idées auxquelles
on s'est déjà attaché.

| À relever | Où | Ce que ça interdit |
| --- | --- | --- |
| Palette sémantique | `apps/web/app/globals.css`, bloc `@theme inline` | toute couleur hors `surface` / `bord` / `texte` / `accent` / `info` / `positif` / `attention` / `alerte` et leurs `-texte` / `-creux` / `-appui` |
| Échelle typographique | même fichier, bloc `@theme` | tout pas hors `micro` / `annexe` / `corps` / `titre` / `page` (`chiffre` est réservé aux tuiles de tête) |
| Primitives | `apps/web/components/Primitives.tsx` | refaire à la main une carte, un bouton, un champ, un badge, une tuile — c'est la recopie que docs/30 §2.2 a mesurée : 18 cartes et 26 boutons |
| Les trois places | docs/30 §4, compté par `apps/web/tests/sobriete.test.tsx` | un bloc de plein format de plus, un 5ᵉ chiffre de tête |
| Le filet a11y | `apps/web/tests/a11y.test.tsx`, `contraste.test.ts` | l'état porté par la **couleur seule**, une animation sans `motion-reduce:`, une cible sous 24 px |

Relève aussi les **exemptions déjà assumées** (docs/30 §3.5 : le graphe de pipeline nœud à nœud, le
niveau AAA) — elles ne sont ni des oublis à corriger, ni un blanc-seing sur leur voisinage.

## 3. Aller chercher — et **prouver** ce qu'on rapporte

Cherche **3 à 5 produits comparables** qui résolvent la même question. Deux garde-fous, dans cet
ordre :

1. **Commence par ce qui est déjà au banc.** docs/30 §1 en tient quatre — GitHub Actions (liste de
   runs, détail d'un run), Grafana, Linear, Cursor. S'ils répondent à la surface, reprends-les : le
   banc gagne à se creuser plutôt qu'à s'allonger. Va chercher ailleurs quand la surface sort de
   leur champ — une visualisation de coûts, un fil de conversation, un éditeur d'agent.
2. **Ce qui n'est pas vérifié n'est pas cité.** C'est la règle de #471, qui a écarté Temporal et
   Langfuse — les deux références fonctionnellement les plus proches — faute d'UI publique
   capturable, plutôt que de les décrire de mémoire. **Au moins deux références sont vérifiées en
   direct** : une page lue (`WebFetch`) ou une capture prise
   (`mcp__chrome-maestro__browser_navigate` puis `browser_take_screenshot`). Une piste non
   vérifiable se **nomme comme telle** en une ligne, et ne porte aucun parti pris.

Range les captures dans l'atelier de session, en chemin relatif :
`.maestro/session/design-veille/<surface>-<reference>.png`. C'est la règle de docs/10 §11.7 — ce
qu'on invite à regarder va sous `.maestro/` (gitignoré), jamais dans `/tmp`, qu'une session ne peut
pas relire sans un chemin absolu.

**Ferme la fenêtre du navigateur (`browser_close`) dès la séquence terminée**, et pas seulement en
fin de session : Chrome n'accepte qu'un seul consommateur à la fois sur un profil, et une fenêtre
laissée ouverte bloque l'outil suivant.

## 4. Prendre / laisser, référence par référence

Pour chacune, deux listes courtes — c'est la forme du §1 de docs/30, et c'est elle qui rend un banc
utilisable plutôt qu'admiratif :

- **Ce qu'on lui prend** : le mécanisme, jamais l'apparence. « L'état porte une forme, pas seulement
  une couleur » se transpose ; « le vert de GitHub » ne se transpose pas.
- **Ce qu'on lui laisse**, avec sa raison. Une référence dont on ne laisse rien n'a pas été
  regardée : elle sert un autre produit, avec d'autres contraintes — thème unique, densité
  d'exploration, typographie de marque, page d'accueil.

## 5. Confronter au socle — et **refuser ici**, pas en revue

Reprends chaque « ce qu'on prend » et passe-le au tableau du §2. Trois issues, et la troisième est
la plus utile :

- **Il tient tel quel** → il devient un parti pris.
- **Il tient une fois traduit** dans les tokens et les primitives existants → il devient un parti
  pris, dans sa forme traduite. C'est le cas courant, et c'est là que la veille travaille.
- **Il ne tient pas** → il est **refusé ici, avec sa raison**, et n'apparaît pas dans la décision.
  Une proposition hors palette, hors échelle, ou qui ajoute un bloc à un écran déjà plein n'est pas
  un arbitrage à remettre à la relecture : c'est ici qu'elle se tranche, sans quoi elle reviendra en
  ✗ de CI ou en recopie de plus.

Si le **même** refus revient sur plusieurs surfaces, ce n'est plus un refus mais un **manque du
socle** : dis-le, et propose le ticket (une primitive, un token, un pas de plus). Ne l'ajoute pas au
passage — c'est ainsi qu'on obtient les 5 rayons, 6 ombres et 13 tailles de police de docs/30 §2.3.

## 6. Rendre la décision — courte

**3 à 5 partis pris**, pas davantage. Une veille qui en rend douze n'a rien tranché, et aucun lot
n'en appliquera douze. Chacun tient en une ligne et porte trois choses :

> **&lt;le parti pris&gt;** — d'après *&lt;référence&gt;*. Concrètement : `<le geste dans le code>`.

Termine par ce que la veille **n'a pas** regardé — par honnêteté de méthode, comme docs/30 §7 — puis
par la suite, que tu **proposes sans la faire**. ⚠ **En session de run, le premier point ci-dessous
n'est pas une proposition : tu le fais** (§7.3), parce que personne ne lira une proposition.

- consigner la décision sur le ticket en cours :
  `bash scripts/gitlab/lib.sh issue-note <iid> <fichier>` — le texte voyage par un **fichier**,
  jamais sur la ligne de commande (la couche permissions découpe sur les sauts de ligne) ;
- ou, si la surface n'a pas de ticket, `/ticket-create` ;
- si des partis pris dépassent le lot en cours, **un ticket par ligne** plutôt qu'un élargissement
  du périmètre courant ;
- et, une fois le code écrit, ce qui **garde** : `npm test` dans `apps/web`, puis le skill
  `banc-mise-en-page` dès que la retouche porte sur des hauteurs, du défilement ou du responsive.

## 7. En session de run — qui choisit la surface, et ce qu'on fait de la décision

Une session de run n'a **personne** pour répondre. Deux points changent, et rien d'autre : la
méthode des §1 à §6 tient telle quelle.

### 7.1 La surface, tu la dérives

`/ticket-start` a imprimé un bloc `surface visible :` — c'est le signal (`lib.sh touche-surface`,
#714), et il nomme le **motif qui a parlé** (`agent::design`, ou une route de `apps/web/app/`) avec
le nombre de lignes du ticket qui l'ont déclenché. Prends de là **la surface que le ticket
retouche**, jamais le champ du motif : un ticket qui porte `agent::design` et parle de la carte d'un
run a pour surface « la carte d'un run », pas « la Control Tower ». S'il en touche plusieurs et
qu'elles posent la même question, elles font **une** veille ; sinon, prends celle que le ticket
décide vraiment et **nomme l'autre** au §6, parmi ce que la veille n'a pas regardé.

### 7.2 Jouer ou ne pas jouer — c'est toi qui juges, et le critère tient en une question

Le bloc `surface visible :` est une **détection**, jamais un verdict : c'est le partage de #562,
#612 et #714, et il ne bouge pas ici. Ce qui change est **qui rend le verdict** — en interactif une
personne, en run toi. La question :

> **Ce ticket décide-t-il de quelque chose à l'écran, ou applique-t-il une décision déjà prise ?**

- **Il décide** → joue la veille. Un écran ou un composant neuf, un motif d'affichage à inventer, un
  changement dans *la façon* dont une information est rendue, un ticket dont les critères disent
  l'intention (« rendre lisible d'un coup d'œil ») sans dire la forme.
- **Il applique** → ne la joue pas (§7.4). Remplacer une couleur brute par son token, corriger un
  débordement à 400 px, renommer, réparer un test, déplacer du code, appliquer un parti pris qu'une
  veille antérieure a déjà rendu : le ticket dit déjà quoi faire, il n'y a rien à chercher dehors.

⚠ **L'asymétrie des deux erreurs est écrite, et elle penche.** Jouer pour rien coûte du quota et un
commentaire de trop — borné, et visible. Ne pas jouer quand il fallait laisse un écran de plus écrit
sans référence, et c'est le défaut que ce chantier corrige (mesure du 2026-08-30 : **13 surfaces
visibles sur 76 tickets livrés par un run, zéro arbitrée**). Le ticket de veille (#795) borne cette
seconde erreur sans l'annuler — la question survit, mais l'écran est déjà écrit. **Dans le doute,
joue** ; et le doute est rare, un ticket qui dit quoi faire le disant en toutes lettres.

### 7.3 Si tu l'as jouée : consigne d'abord, arbitre ensuite

C'est ici, et seulement ici, que la commande cesse d'être en lecture seule — c'est la décision de
#934 :

1. **Consigne les partis pris sur le ticket** — écris-les avec l'outil `Write` dans
   `.maestro/session/`, puis `bash scripts/gitlab/lib.sh issue-note <iid> <fichier>` (le texte voyage
   par un **fichier**, jamais sur la ligne de commande). En interactif le §6 *propose* ce geste ; ici
   tu le **fais**, parce que personne ne lira une proposition et que le ticket se ferme au merge dans
   l'heure — la leçon de #608 et de #795, appliquée à l'objet qu'elles protègent.
2. **Puis enregistre l'arbitrage** — `bash scripts/gitlab/lib.sh veille-arbitre <iid>`.

**L'ordre n'est pas cosmétique** : l'inverse laisserait, si la consignation échoue, un label qui
ferme la question **sans sa trace** — `veille::arbitree` ne dit pas ce qui a été décidé, seulement
que la question a été posée. Fais les deux **dès que la décision est rendue, avant d'implémenter** :
si la session s'arrête ensuite (limite d'usage, échec), la veille est déjà sauvée, et c'est
exactement ce qu'on lui demande.

Puis implémente en appliquant tes propres partis pris, et nomme-les dans ton résumé final.

### 7.4 Si tu ne l'as pas jouée : n'écris rien ici, et dis-le

**N'appelle ni `veille-arbitre`, ni `veille-differe`, ni `issue-note`.** Sors en disant que la veille
n'avait pas d'objet sur ce ticket, et **pourquoi** en une ligne : c'est la session qui reprendra la
main, et le régime de #795 est le sien — son constat s'écrit **après** l'implémentation, puisqu'il
nomme ce qu'elle a décidé à l'écran faute de référence. Ici, ce constat n'existe pas encore.

⚠ **Pourquoi `veille-arbitre` est refusé dans ce cas-là, alors qu'il est posé dans l'autre.** En
interactif, le « non » d'une personne *est* un jugement — elle connaît le contexte, on lui a demandé,
elle a répondu ; le label enregistre sa réponse. En run, un « non » qui ne vient de personne est une
**abstention**, et une abstention ne ferme pas une question : la poser d'office serait le « marquer
d'office » que #562 a écarté nommément. Ce qui autorise l'enregistrement n'est donc pas *qui* a joué,
c'est **qu'un jugement ait été rendu et écrit** — d'où une propriété qui se vérifie : *en run, tout
`veille::arbitree` est adossé à un commentaire de partis pris sur son ticket.*

## Ce que tu ne fais jamais ici

- **Écrire du code**, ouvrir une PR, changer l'état d'un ticket, créer un ticket. Poser un label non
  plus — à **une** exception, `veille::arbitree` en session de run, et seulement après avoir consigné
  les partis pris (§7.3). Ce qui dépasse le lot en cours se **nomme** au §6 ; en run, cela vaut aussi
  pour les tickets qu'on aurait ouverts — les nommer laisse la décision à quelqu'un, les ouvrir
  d'office remplit le backlog de tickets que personne ne fermera.
- **Citer une référence de mémoire.** Non vérifiée, elle est nommée comme telle ou elle n'y est pas.
- **Proposer une identité nouvelle** — palette, police, arrondis de marque (docs/30 §6.1).
- **Rejouer les mesures des autres outils** : le contraste est un test (`contraste.test.ts`), la
  géométrie un banc (`banc-mise-en-page`), l'accessibilité un filet (`a11y.test.tsx`). Renvoie-y ;
  ne les refais pas à l'œil.
