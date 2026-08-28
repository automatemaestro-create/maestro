---
description: Cherche comment les produits comparables rendent une surface donnée, puis en tire des partis pris tenables dans le socle — avant d'écrire une ligne d'interface
argument-hint: "<surface>  (un écran, un composant ou un motif : « la carte d'un run », « /couts », « la barre d'avancement »)"
allowed-tools: WebSearch, WebFetch, Read, Grep, Glob, Bash(bash:*), Bash(git:*), mcp__chrome-maestro
---

Commande **de recherche, en lecture seule** : pour la surface `$ARGUMENTS`, tu vas chercher dehors
comment les produits comparables la rendent aujourd'hui, puis tu en tires **3 à 5 partis pris
applicables**, chacun rattaché à sa référence et **tenable dans le socle du dépôt**. Tu n'écris ni
code, ni ticket, ni commentaire de forge : tu rends une décision, et tu proposes la suite.

Si `$ARGUMENTS` est vide, demande la surface. « Le design de la Control Tower » n'en est pas une :
il faut un écran (`/couts`), un composant (`CarteRun`) ou un motif (« la barre d'avancement d'un
run »). Une veille sans objet rend une galerie ; une veille sur une surface rend une décision.

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
par la suite, que tu **proposes sans la faire** :

- consigner la décision sur le ticket en cours :
  `bash scripts/gitlab/lib.sh issue-note <iid> <fichier>` — le texte voyage par un **fichier**,
  jamais sur la ligne de commande (la couche permissions découpe sur les sauts de ligne) ;
- ou, si la surface n'a pas de ticket, `/ticket-create` ;
- si des partis pris dépassent le lot en cours, **un ticket par ligne** plutôt qu'un élargissement
  du périmètre courant ;
- et, une fois le code écrit, ce qui **garde** : `npm test` dans `apps/web`, puis le skill
  `banc-mise-en-page` dès que la retouche porte sur des hauteurs, du défilement ou du responsive.

## Ce que tu ne fais jamais ici

- **Écrire du code**, ouvrir une PR, poser un label, changer l'état d'un ticket.
- **Citer une référence de mémoire.** Non vérifiée, elle est nommée comme telle ou elle n'y est pas.
- **Proposer une identité nouvelle** — palette, police, arrondis de marque (docs/30 §6.1).
- **Rejouer les mesures des autres outils** : le contraste est un test (`contraste.test.ts`), la
  géométrie un banc (`banc-mise-en-page`), l'accessibilité un filet (`a11y.test.tsx`). Renvoie-y ;
  ne les refais pas à l'œil.
