# apps/web — Control Tower

Interface web de supervision (le poste de pilotage, docs/05) — v1 du ticket #47,
refondue en backoffice complet par #116 (« Phase 4 — Control Tower UX ») :

- **Shell applicatif de backoffice** (#117, lot 1 de la refonte UX #116) : une
  **sidebar** de navigation commune à toutes les pages (Tableau de bord · Agents ·
  Chat · Coûts & analytics · Validations · Paramètres) avec état actif
  et **version repliée en icônes** — imposée sous `lg`, au choix au-delà (le repli
  est mémorisé) ; une **barre supérieure** qui porte le titre de la page, le statut
  du flux temps réel, le coût cumulé, puis la cloche de notifications (#119), la
  bascule de thème (#118) et le menu d'aide (#122). Les pages ne rendent plus
  que leur contenu : l'état temps réel est ouvert **une fois** par le shell
  (`lib/etatGlobal.tsx`) et diffusé par contexte. Le menu est déclaré une seule
  fois (`lib/navigation.ts`) : la sidebar et le titre de page le lisent tous deux ;
- **Thème clair / sombre** (#118) avec bascule persistante dans la barre
  supérieure — clair, sombre ou **système** (le défaut, qui suit la préférence de
  l'appareil, y compris quand elle change en cours de session). Le choix vit dans
  le `localStorage` (`lib/theme.ts`), relu par un **script d'init** exécuté avant
  le premier rendu (`app/layout.tsx`) : pas de flash au chargement. Le même
  réglage est offert dans les Paramètres — c'est la même commande, pas une copie ;
- **Centre de notifications déroulant** (#119) : une cloche présente sur toutes
  les pages, dont le badge compte les validations humaines en attente ; son
  panneau permet de les **approuver / refuser sur place**, sans quitter la page
  courante, puis rappelle l'activité récente notable (`lib/evenements.ts` trie ce
  qui mérite d'être remonté, le menu fretin temps réel restant au fil d'activité) ;
- **Identité visuelle Maestro** (#120) : un monogramme « M » (`components/Logo.tsx`)
  à la place de l'emoji 🎼, décliné en favicon SVG (`app/icon.svg`) et en icônes
  binaires (`app/favicon.ico`, `app/apple-icon.png`) régénérées par
  `node scripts/build-icons.mjs` ;
- **Guide de prise en main** (#122) : une visite guidée qui se pose sur les
  éléments réels de l'interface (ancres `data-guide`), se lance d'elle-même à la
  première visite, se mène au clavier (flèches, Échap) et se relance à volonté
  depuis le menu d'aide. Son contenu vit dans `lib/guide.ts` ;
- **Assistant flottant** (#123) : un bouton en bas à droite ouvre un panneau
  d'aide sur l'outil, branché sur le fil de chat `assistance`
  (`/api/chat/assistance`) — historique persisté et réponses en temps réel comme
  l'onglet Chat d'un agent. Il ne se ferme pas au clic extérieur (on le consulte *pendant*
  qu'on agit) et le shell réserve la bande qu'il occupe pour ne masquer aucune
  action de la page ;
- **Tableau de bord épuré** (#191, lot 2 de la navigation v2 #189) : l'essentiel
  en **un écran** — ce qui attend un arbitrage, quatre **indicateurs de tête**
  (run en cours, tâches par statut, agents occupés et libres, dépense),
  **l'état des runs** (#476 — le Kanban tenait cette place jusque-là, voir plus
  bas), puis un **aperçu** de l'activité. Chaque tuile met en valeur **le
  chiffre qu'on vient y chercher** (#247) : la tuile Agents répond « combien
  travaillent, combien sont disponibles ? » et relègue le total et les agents
  désactivés en ligne de détail. Les trois panneaux de plein format qui s'y empilaient
  n'ont pas disparu, ils sont **rangés**, et chaque tuile **renvoie** vers la page
  où le détail vit maintenant (fiches d'agent → Agents, grand livre par exécution
  → Coûts & analytics). Les renvois sont résolus par le menu
  (`entreeParLibelle`) et non par un chemin en dur : ils suivent une page qui
  déménage, et **ne s'allument pas** vers une page qui n'existe pas encore. Le
  fil d'activité en a fait la démonstration : son renvoi, écrit dès #191, est
  resté éteint jusqu'à ce que #249 crée le Journal — sans une ligne de plus dans
  `FilActivite` ;
- **Journal** (#249, lot 5 de #242 ; **persisté** par #478) : le fil d'activité en
  **plein format**, avec filtres par type d'événement, par agent et par tâche,
  recherche texte et une case « Notable seulement » qui reprend le filtre de la
  cloche (`estNotableNotification`, #119 — pas de seconde logique de tri). La page
  rend le même `FilActivite` que l'aperçu, sans `limite` : une seule mise en forme
  de ligne pour les deux écrans. Le fil y était **éphémère** — les 50 derniers
  événements reçus depuis l'ouverture de la page, remis à zéro au rechargement —
  jusqu'à ce que #478 serve `GET /api/journal`, figé au contrat depuis #183 : la
  page **part désormais de l'historique persisté** (`lib/useJournal`) et le temps
  réel s'y superpose (`lib/journal`), si bien qu'un rechargement ne perd plus rien.
  Elle en montre au plus 200 (le plafond d'une page côté backend) et le dit
  au-delà ;
- **Runs** (#474, lot 2 de #472, docs/05 §2.4.1) : la liste des runs du projet
  actif, du plus récent au plus ancien — état, objectif, progression et coût. Un run
  n'était l'objet d'aucun écran : on y entrait par « Composer un objectif » et on
  n'y revenait jamais. L'écran sépare **quatre régimes** (`lib/execution.ts`) là où
  l'API n'a qu'un statut : *travaille* (badge bleu à pastille battante), *suspendu*
  (fond ambré, l'attente nommée et son ancienneté), *interrompu* (orphelin #348) et
  *soldé*. La distinction n'est pas cosmétique — une attente de décision humaine est
  restée 53 minutes indiscernable d'un run qui travaillait (#355), et la troisième
  attente, la **validation d'une tâche**, ne se lit même pas sur le run : elle
  s'apparie par les tâches. L'ordre et la progression viennent du backend (#473) :
  ni tri ni recomptage ici. Vide, l'écran **nomme le projet** et propose le geste
  qui le remplit ; API injoignable, il ne laisse que la bannière ;
- **Vue d'un run** (#475 puis #478, lots 3 et 6 de #472, docs/05 §2.4.2) :
  `/runs/<run_id>` — sa **progression** en tête, son **Kanban** au milieu, son
  **journal** au pied. Le Kanban est le composant de toujours, réutilisé et non
  réimplémenté : mêmes colonnes, mêmes cartes, même détail sur place (#251) — ce
  qui change est ce qu'on lui donne. Les tâches viennent de `?run=` (#473) et
  **jamais** d'un filtre sur celles du projet : un identifiant de tâche est partagé
  entre un run et sa relance (#349), et filtrer localement ferait disparaître de la
  vue ce qu'un successeur a repris. Le journal suit la même règle par `?run_id=`
  (`components/runs/JournalRun.tsx`) : il manquait au lot 3 faute de source, le fil
  du shell ne portant que ce qui est arrivé depuis l'ouverture de la page — donc
  rien du tout sur un run terminé la veille. Le temps réel est celui du shell —
  **aucune seconde WebSocket** : la vue se rafraîchit au *pouls* de
  `useControlTower` (`revision`), c'est-à-dire quand celui-ci vient de relire
  (`lib/useTachesRun.ts`, `lib/useJournal.ts`). Un run d'un autre projet **le dit**
  au lieu d'afficher un tableau vide qui se lirait « ce run n'a rien fait » ;
- **Vue pipeline** (#491, lot 3 de #488, docs/05 §2.4.4) : le **flux** du run à la
  place de son inventaire — un niveau du graphe (#490) par colonne, un nœud par
  boîte, une courbe orientée par dépendance, tracée en SVG à la main sur les boîtes
  mesurées (aucune dépendance de rendu de graphe : `apps/web` tient en trois
  paquets). Chaque nœud porte son agent, son état et sa **checklist qui se coche en
  direct** (#489) ; le détail complet rouvre le panneau de #251, en croisant le nœud
  avec la tâche de même identifiant. Deux choses s'y voient et nulle part ailleurs :
  ce qui **attend un humain** — teinté et immobile, quand ce qui travaille bat —, lu
  dans la file des validations et non sur la tâche (le moteur n'émet pas
  `en_attente_validation`, et la table des compartiments le rangerait dans « en
  cours ») ; et la **suite qui s'allume** quand toutes les arêtes entrantes d'un nœud
  sont franchies. Un graphe qui déborde ne se lit pas : le dessin défile **chez lui**
  et une bascule cadre sur la **branche courante**. Elle **coexiste** avec le Kanban et
  le **journal** sous une bascule à trois positions et l'ouvre par défaut (#516 : le
  journal se lisait au pied de la vue, donc sous les deux autres lectures) —
  l'arbitrage, et les options écartées, sont dans `lib/vuesRun.ts` ;
- **Tableau de bord temps réel** : état des agents (libre/occupé, tâche courante,
  compteurs, coût cumulé) et des tâches, mis à jour par WebSocket sans rechargement ;
- **État des runs** (#476, lot 4 de #472, docs/05 §2.1) : ce que le tableau de bord
  montre **à la place du Kanban**, groupé par régime — *en cours*, *suspendus*,
  *interrompus*, *soldés du jour* —, chaque run avec sa progression et le renvoi
  vers sa vue. Le renversement de #248 tient à la **portée** et non à la place : le
  Kanban rend les tâches du **projet** (#277/#281), donc ce qui court mêlé à ce qui
  est fini depuis trois jours, quand « où en est-on ? » porte sur ce qui tourne —
  un **run**. Le découpage vient de `regimeDuRun` et la ligne est la `CarteRun` de
  la liste (`components/runs/EtatRun.tsx`) : ni second tri, ni seconde mise en
  forme. Seuls les **soldés** sont bornés — au jour, puis à cinq, le reste étant
  dans la liste des runs — parce que c'est le seul groupe qui grossit sans fin ;
  le groupe *interrompus* s'ajoute aux trois du ticket parce que `regimeDuRun` en
  rend quatre et que le panneau « Runs interrompus » ne montre que les
  **récupérables** (#349) ;
- **Kanban** des tâches par statut (machine à états docs/03 §3), qui a **pris la
  place** du tableau de bord de #248 (lot 4 de #242) à #476, où il est devenu la
  **vue d'un run** (§2.4.2) : les tuiles se
  resserrent à une rangée, le tableau absorbe la hauteur restante et chaque
  colonne défile chez elle. En largeur, c'est une **largeur minimale par
  colonne** qui commande et non un nombre de colonnes : au-delà les colonnes
  s'élargissent, en dessous elles se replient en lignes au lieu d'être tassées
  de front. L'étirement est une **chaîne** — hauteur définie sur le `<body>`,
  puis `min-h-0` sur chaque maillon flex jusqu'à la liste qui défile ; elle se
  pose en entier ou pas du tout, un maillon manquant et le débordement remonte à
  la zone de contenu (`tests/kanban.test.tsx` la parcourt) ;
- **Détail d'une tâche ouvert sur place** (#251, lot 7 de la vague #242) : une
  carte qui porte une description, des étapes ou des liens utiles (#246) les
  ouvre au clic dans un **panneau** latéral — description, étapes en checklist
  avec leur avancement, liens rendus selon leur **nature** (maquette, ticket,
  dépôt) et non d'après leur domaine. La carte, elle, ne bouge pas : elle reste
  l'objet dense qu'on lit en diagonale sur cinq colonnes. Une tâche sans détail
  — le cas de **toutes** les tâches tant que #246 n'est pas livré — affiche
  exactement la carte d'avant : ni bouton, ni curseur qui promet une ouverture,
  ni cadre vide. Les URL des liens passent par le même filtre que celle du
  ticket externe (`lib/liens.ts`) ;
- **Réassignation manuelle** d'une tâche à un autre agent depuis chaque carte
  (EF-11/EF-20) — et depuis le panneau de détail (#251), pour ne pas avoir à le
  refermer quand c'est sa lecture qui fait conclure au changement d'agent ;
- **Fil d'activité** en direct (statuts, activités d'agents, messages
  inter-agents), dont les lignes **disent ce qui se passe** depuis #250 (lot 6 de
  #242) : « dev a terminé « Écrire les tests » » plutôt que « tache-42 —
  Terminée (dev) ». Une **rafale** — N transitions d'une même tâche rapprochées
  dans le temps — se replie en une seule ligne comptée (« 4 étapes ») qui se
  déplie dans l'ordre où elle s'est jouée, l'**horodatage** est relatif près du
  présent puis redevient absolu au-delà de la semaine, et le **détail brut**
  (identifiant, statut du bus, texte du moteur) reste à un clic sur toutes les
  lignes. Le tout vit dans une brique unique (`components/LigneActivite.tsx`,
  vocabulaire dans `lib/evenements.ts`) partagée par l'aperçu du tableau de
  bord, le Journal et la cloche : les trois ne peuvent pas diverger ;
- **Validations humaines** (#48, docs/05 §2.6) : les actions sensibles mettent la
  tâche en pause et apparaissent en tête de tableau de bord avec leur contexte
  (agent, tâche, action demandée, justification) — **Approuver** fait reprendre la
  tâche, **Refuser** l'annule proprement ; la décision est journalisée côté moteur ;
- **Coûts par exécution** (#58, docs/05 §2.5 — critère MVP n°6) : le grand livre
  de chaque run (`GET /api/executions/{run_id}/cout`, #57) — part de planification,
  coût par tâche (tokens entrée/sortie, coût estimé, durée) et agrégat de
  l'exécution ; chaque carte Kanban affiche aussi le coût détaillé de sa tâche.
  Le grand livre est rangé sur la page **Coûts & analytics** depuis #191, sous les
  agrégats qui le résument ;
- **Lien vers le ticket externe** (#192, lot 3 de la navigation v2 #189) : une
  tâche issue d'un ticket (#187) porte le lien vers lui sur sa carte Kanban et
  dans les deux tables de coûts. L'URL vient du flux, donc d'une source non
  fiable : un seul point de passage la valide (`lib/liens.ts`) et seuls `http` et
  `https` sont suivis — une URL de schéma inattendu s'affiche en **texte**, jamais
  en lien mort ni en `href` exécutable ;
- **Fiche agent à onglets** (#190, lot 1 de la navigation v2 #189) : l'entrée de
  menu **Agents** mène à la liste (`/agents`) et chaque agent ouvre **une** fiche
  dont les facettes tiennent en onglets — Profil, Playbook, MCP & permissions,
  Chat (`/agents/<nom>/<onglet>`). Les trois pages qui regardaient le même objet
  par trois chemins ont fusionné : `/catalogue`, `/playbooks` et `/chat/<agent>`
  sont **redirigés vers le bon onglet** (`next.config.ts`, aucun signet ne casse)
  et `?onglet=` porte l'intention jusqu'à la liste quand l'URL d'origine ne
  nommait pas d'agent. `/chat` reste au menu pour le chat **global**, non lié à un
  agent — servi pour de bon depuis #269 (voir ci-dessous). Les onglets sont
  déclarés une seule fois (`lib/agents.ts`), comme le menu l'est dans
  `lib/navigation.ts` ;
- **Éditeur de playbooks** (#77, EF-24/EF-25) : l'onglet **Playbook** d'une fiche
  agent porte son playbook versionné (#76, API `/api/playbooks`), publie une nouvelle
  version depuis un éditeur plein texte et montre l'historique, chaque version
  antérieure étant consultable et restaurable (le dépôt est append-only :
  restaurer republie, rien n'est réécrit). Une version publiée s'applique **à
  chaud** (#78, EF-26) : le moteur relit la version courante à chaque tâche —
  elle vaut pour l'exécution suivante sans redémarrage, et la version utilisée
  est tracée sur chaque résultat (`playbook_version`, journal compris) ;
- **Catalogue des agents** (#73, EF-03) : la liste `/agents` montre le catalogue
  effectif (#72, API `/api/catalogue`) — ceux du code en lecture seule, et les
  **personnalisés** qu'on y crée, puis modifie et supprime depuis l'onglet
  **Profil** de leur fiche (formulaire complet : nom, rôle, compétences,
  fournisseur/modèle, playbook). Un agent personnalisé est persisté hors du code
  et chargé par les moteurs construits ensuite ;
- **Chat par agent** (#85, lot 2 de #82) : l'onglet **Chat** d'une fiche agent
  ouvre le fil de conversation avec lui (#84, API `/api/chat`) — envoi,
  réponse de l'agent (cadrée par son playbook courant) et réception en temps
  réel par le WebSocket (`chat.message`). Le fil est persisté côté backend :
  l'historique se recharge au retour sur l'onglet ;
- **Chat global** (#269, lot 2 de #244, docs/05 §2.9) : `/chat` sert le fil avec
  l'**orchestration** (canal `orchestrateur`, #268) — poser une demande sans avoir
  à choisir d'abord à qui la poser. Trois choses à connaître avant d'y toucher.
  **Un seul composant de fil** : la mise en page conversationnelle vit dans
  `components/Conversation.tsx`, que l'onglet Chat d'un agent monte aussi
  (`components/FilChat.tsx` n'est plus que son branchement) — le **dépôt de
  sources** (#482) y a déménagé du même geste, donc le chat global en hérite sans
  une ligne à lui, ce que `lib/useSourcesComposees` annonçait déjà ; le panneau
  d'assistance flottant reste à part, c'est une carte posée par-dessus la page et
  non un écran. **La mention change de destinataire, elle ne recopie rien** :
  `@dev …` part dans le fil de `dev`, sans navigation et sans second historique —
  c'est ce qui rend « les deux ne divergent pas » vérifiable plutôt que promis.
  **Ce qu'un message a ouvert est sous sa bulle** : le rattachement vient du
  message (`run_id`/`tache_id`, persisté), le compte des tâches et les validations
  en attente de l'état temps réel, avec renvoi vers `/runs/<run_id>` et
  `/validations` — un message ordinaire ne rattache rien et ne laisse aucun cadre
  vide ;
- **Page Paramètres** (#121) : la configuration regroupée en six sections
  navigables par ancres (Général · Apparence · Agents & capacité · Fournisseurs &
  modèles · Coûts & plafonds · Notifications), avec un sous-menu qui suit le
  défilement. Le sommaire est déclaré une fois (`lib/parametres.ts`) et le
  typage rend une section sans contenu impossible à compiler. Ce qui est réglable
  l'est **vraiment** ici (la capacité des agents, #86 ; le thème et le repli de la
  sidebar) ; ce qui ne l'est pas encore dit d'où ça se règle aujourd'hui — jamais
  un lien mort ni un interrupteur sans effet ;
- **Validations** : la page `/validations` liste les demandes de #48 sorties du
  tableau de bord, en attente comme déjà tranchées.

Stack (docs/02 §5) : **Next.js + React + TypeScript + Tailwind**.

## Le langage visuel

Posé par #245 (lot 1 de #242), étendu par #533 (lot 1 de #532) qui lui a donné sa
palette sémantique, il tient en trois fichiers. Ce qui suit n'est pas
un inventaire : c'est **où décider**, pour qu'une décision de rendu n'ait plus à
se reprendre écran par écran.

### Le jeu d'icônes — `components/Icones.tsx`

Toutes les icônes du produit, et **uniquement** des SVG à `currentColor` : le
menu, les onglets de fiche agent, les types d'événement, les statuts de tâche,
les actions. Plus aucun émoji décoratif dans `components/` ni `lib/` — l'émoji
apportait avec lui sa propre graisse, sa propre couleur et un rendu différent
par plateforme, ce qui rendait toute cohérence hors d'atteinte.

Deux règles s'appliquent à l'ajout d'une icône :

- **elle passe par `Trait`** — même `viewBox`, même épaisseur, mêmes
  jointures ; une icône qui s'en écarte se voit à côté des autres ;
- **elle est décorative** (`aria-hidden`, posé par `Trait`) : elle *double* un
  libellé texte, elle ne le porte jamais seule. C'est ce que l'émoji faisait par
  endroits — « 🤖 dev » n'apprenait rien à qui ne le voyait pas ; ces lignes
  disent maintenant « Agent dev ».

### Les primitives — `components/Primitives.tsx`

Sept briques, et le `className` qu'on n'écrit plus :

| Brique | Ce qu'elle porte |
| --- | --- |
| `Carte` | la surface : bord, fond, ombre, arrondi, **densité**, **ton** |
| `Bouton` / `BoutonLien` | l'action : **variante**, **ton**, **taille**, désactivé, occupé |
| `Champ` / `ChampListe` / `ChampTexte` | la saisie : libellé lié, aide, erreur, `aria-invalid` |
| `TuileChiffre` | un chiffre de tête, son libellé, son détail, son renvoi |
| `EnTeteSection` | le titre d'une zone, son icône, ce qui l'accompagne |
| `BadgeEtat` | la pastille d'état (compte, statut, provenance, temps réel) |
| `EtatVide` | ce qui manque, et par où l'obtenir |

Une huitième vit **à côté**, dans son propre fichier :
`components/BasculeDeVues.tsx` (#539) — plusieurs lectures d'un même bloc, une à
la fois. Elle n'est pas dans `Primitives.tsx` pour la raison qui en écarte aussi
`Infobulle` : elle appelle un hook (`useId`), et ce fichier-là est partagé avec
des composants serveur.

Les briques de #245 portent leurs variants `dark:` **elles-mêmes** ; celles de
#535 n'en portent **aucun** — elles sont écrites sur les tokens de #533, qui
*sont* les deux thèmes. C'est la même promesse, une couche plus bas : aucun écran
ne peut oublier le sombre.

Le **ton** d'une `Carte` (`pleine`, `creuse`, `attention`, `attentionClaire`) est
un choix nommé, pas un `bg-*` passé en `className` : deux règles de fond dans le
même attribut ne se départagent pas par l'ordre d'écriture mais par celui de la
feuille générée — une surcharge au cas par cas est silencieusement instable. La
même règle vaut pour le ton et la variante d'un `Bouton`.

#### Le bouton — `Bouton`, `BoutonLien`

Avant #535 le produit n'avait **aucune** primitive de bouton : 92 `<button>` dans
36 fichiers, dont une vingtaine redéfinissant leur bouton plein. C'est la
primitive manquante la plus coûteuse, parce que c'est elle qui portait le
contraste fautif — `bg-emerald-600` + blanc, **3,65:1 dans les deux thèmes**
(docs/30 §3.2), à corriger dans autant d'endroits.

| Axe | Valeurs | Ce qu'il dit |
| --- | --- | --- |
| `variante` | `plein` · `contour` · `discret` | le **rang** de l'action : ce qu'on vient faire, ce qui l'accompagne, ce qui ne doit pas peser |
| `ton` | `accent` · `neutre` · `alerte` · `attention` · `info` | le **rôle**, jamais une couleur |
| `taille` | `normale` · `petite` | le formulaire, ou la ligne |
| `occupe` | booléen | l'action **est en cours** : inerte, anneau qui tourne, `aria-busy` |

`occupe` n'est pas un synonyme de `disabled` : un bouton désactivé dit qu'il n'y
a rien à faire, un bouton occupé dit qu'on attend. Les deux rendent le bouton
inerte ; un seul l'annonce.

Le **contour de focus** vit dans la primitive, pas dans l'appelant : c'est le
seul endroit d'où l'on peut promettre qu'aucune action du produit n'est invisible
au clavier (WCAG 2.2, 2.4.7).

`BoutonLien` est le même bouton quand l'action est une **navigation** : c'est un
lien — il s'ouvre dans un onglet, il se copie —, il en a seulement l'allure.

#### Le champ — `Champ`, `ChampListe`, `ChampTexte`

Trois contrôles, un seul cadre : le libellé, l'**aide** et l'**erreur**, celle-ci
posant `aria-invalid` et se rattachant à la saisie par `aria-describedby`. Avant
#535, chaque écran refaisait ses deux constantes `CLASSE_CHAMP` /
`CLASSE_LIBELLE` — et une erreur s'affichait dans un paragraphe voisin que rien
ne reliait au champ pour un lecteur d'écran.

⚠ Le libellé **entoure** le contrôle au lieu de le viser par `htmlFor`, et ce
n'est pas un détail de style : `label.control` résout un `for` par
`getElementById`, donc par **le premier** identifiant de ce nom dans le document.
Deux instances du même écran montées ensemble — ce que fait déjà
`tests/projet-cadre.test.tsx` — et la seconde perd son nom accessible en silence.
L'`id` reste obligatoire : c'est lui qui rattache l'aide et l'erreur. Il n'est
pas dérivé d'un `useId` parce que `Primitives.tsx` est partagé avec des
composants serveur, où aucun hook ne peut tourner.

#### Ce qui ne peut pas être une `<Carte>`

`classesCarte()` rend les classes de la surface **sans** la balise qui les porte.
Deux appelants seulement, et pour une raison chacun : un `<Link>` (le composant
de Next, pas une balise) et un `<button>` pleine largeur (dont le `type` et le
`disabled` ne vivent pas dans `HTMLAttributes`). Les exposer plutôt que de rendre
`Carte` polymorphe garde **une** source à la décision — c'est bien la recopie qui
disparaît, pas seulement sa forme.

### Les primitives d'accessibilité — `Infobulle`, `usePiegeDeFocus`, `useSurfaceDeroulee`

Posées par #536 (lot 4 de #532), en réponse aux trois motifs que la recherche
#471 avait mesurés en échec ([docs/30 §3.4](../../docs/30-cible-visuelle-control-tower.md)) :
piège de focus **0/3 modales**, navigation aux flèches **0/4 menus**, infobulle
accessible **0** pour 42 `title=`. Tout le reste était déjà bon — `Échap` 7/7,
restauration du focus 7/7, rôles corrects — et n'a pas été touché.

| Primitive | Ce qu'elle tient | Qui s'en sert |
| --- | --- | --- |
| `lib/usePiegeDeFocus.ts` | la tabulation reste dans la surface modale | `PanneauDetailTache`, `GuidePriseEnMain` |
| `lib/useSurfaceDeroulee.ts` | clic extérieur, `Échap`, flèches, `Home`/`End`, focus d'entrée | `MenuAide`, `BasculeTheme`, `SelecteurProjet`, `CentreNotifications` |
| `components/Infobulle.tsx` | une bulle `role="tooltip"` atteignable au clavier | ~14 emplacements, en remplacement de `title=` |

Trois choses à connaître avant d'y toucher :

- **`useSurfaceDeroulee` a remplacé quatre copies du même bloc de dix-huit
  lignes.** C'est la cause de la panne autant que sa réparation : la navigation
  aux flèches manquait aux quatre menus *à la fois* parce qu'il aurait fallu
  l'écrire quatre fois. Le hook **regarde ce que la surface contient** plutôt
  que de recevoir un drapeau — zéro entrée `menuitem` ⇒ c'est un panneau, donc
  ni flèches ni fermeture sur `Tab`. La donnée décide, pas la configuration.
- **La cloche n'est plus un `menu` mais un `dialog` non modal.** Elle déclarait
  `role="menu"` sans porter la moindre entrée `menuitem` — des sections, des
  titres, des listes et des cartes à deux boutons d'arbitrage chacune. Le motif
  ARIA exige ces entrées, l'audit du lot 5 (#537) le vérifiera, et le rôle
  promettait au lecteur d'écran une navigation aux flèches qui ne pouvait pas
  exister.
- **`AssistantFlottant` reste non modal, et n'a donc pas de piège de focus.**
  Le ticket le prévoyait (« la surface de `AssistantFlottant` **si elle le
  devient** ») ; elle ne le devient pas. Toute sa conception est de laisser
  travailler dans la page pendant qu'il est ouvert — c'est pourquoi il n'a pas
  de fermeture au clic extérieur, et un test s'appelle « reste ouvert quand on
  agit ailleurs ».

**Quand remplacer un `title=` — la règle, en une ligne :** `Infobulle` quand
l'information n'existe **nulle part ailleurs** ; `aria-label` quand l'élément est
déjà focusable ; suppression quand le `title` redouble le nom accessible ou le
texte visible.

Reste en `title=`, à dessein, une seule famille : la **forme longue d'un texte
déjà affiché** et l'**identifiant technique** posé en repli d'un nom
(`title={tache_id}` à côté de `{nom || tache_id}`). Ces `title` sont du confort
de souris posé **par ligne** de tableau et par carte du Kanban : les convertir
ajouterait un arrêt de tabulation par ligne, et la navigation clavier y perdrait
plus qu'elle n'y gagnerait. Une exception dans l'exception, `LigneActivite` :
sa date absolue vit dans un `<time>` **à l'intérieur d'un bouton**, où le
wrapper focusable de l'infobulle créerait un contrôle imbriqué dans un contrôle
— elle rejoint donc le texte accessible du bouton par un `sr-only`.

⚠ Le `className` d'`Infobulle` **remplace** son `inline` par défaut, il ne s'y
ajoute pas : deux utilitaires `display` dans la même liste se départagent par
l'ordre de la feuille Tailwind, pas par celui de la chaîne. Et ce défaut est
`inline` et non `inline-flex` parce qu'il doit être **neutre** — le wrapper
prend la place d'un `title=`, qui n'occupait aucune place, et une boîte atomique
que le `truncate` du parent ne sait plus abréger ferait déborder les tuiles de
chiffres au lieu de les finir en points de suspension.

Le fichier porte aussi une **constante** et non une brique : `CIBLE_MINIMALE`
(#537), le plancher de 24 px d'une cible interactive (WCAG 2.2 §2.5.8). Elle vaut
`min-h-6` et s'ajoute à tout lien ou bouton **en petit corps**
(`text-annexe`/`text-micro`/`text-xs`), dont la seule hauteur de ligne ne suffit
pas — c'est là que la mesure de #471 trouvait des cibles à **22 px**, deux pixels
sous la barre. Un plancher (`min-h-`) et non une hauteur (`h-`), pour qu'un
libellé qui passe à la ligne puisse grandir ; et une hauteur plutôt qu'un `py-*`,
dont l'effet dépend du pas typographique de l'élément et changerait sous lui.
Écrite ici une fois, comme le reste : elle est importée par les six composants
qui portent ce genre de lien, et gardée par `tests/a11y.test.tsx`.

### Les régions live — `RegionLive`, `lib/annonces`, `lib/useAnnonce`

Posées par #538 (lot 6 de #532), en réponse au **trou principal** de la recherche
#471 ([docs/30 §3.3](../../docs/30-cible-visuelle-control-tower.md)) : sonde du
2026-08-25 sur **10 écrans × 2 thèmes**, `aria-live` = **0 partout**. Le dépôt en
contenait pourtant un — `AssistantFlottant` —, mais son fil n'est dans le DOM que
panneau ouvert : présent dans le code, absent de l'écran. Pendant ce temps
l'interface se met à jour sans action de l'utilisateur (jusqu'à **3 WebSockets**,
rechargements coalescés à **150 ms**, horloge à **30 s**). Un écran qui bouge tout
seul et ne le dit pas est muet pour qui ne le regarde pas.

| Brique | Ce qu'elle tient |
| --- | --- |
| `lib/annonces.ts` | le **vocabulaire** : des relevés de compteurs nommés (`Mesure`), et la phrase que produit leur comparaison |
| `lib/useAnnonce.ts` | le **débit** : la fenêtre d'agrégation, et la clé qui fait réentendre une phrase répétée |
| `components/RegionLive.tsx` | les deux **nœuds accessibles** : `RegionLive` (polie, une par écran) et `RegionArbitrage` (assertive, une pour tout le shell) |

**Le débit est le vrai sujet**, et c'est lui qui commande la forme. Une région
branchée sur le flux annoncerait plusieurs fois par seconde et rendrait le lecteur
d'écran inutilisable. Un écran ne fournit donc pas des phrases mais un **relevé**,
et c'est la comparaison de deux relevés qui parle — d'où trois propriétés, aucune
accidentelle : seules les **hausses** parlent (une tâche qui change de colonne fait
baisser une colonne et monter l'autre ; dire les deux dirait deux fois le même
événement), une **rafale ne coûte qu'une phrase** (la comparaison porte sur les
deux bouts de la fenêtre et ignore le milieu), et **rien de tout cela n'est du
React**, donc tout se teste sans monter d'écran.

L'agrégation est un **étranglement à front avant** : un changement isolé s'annonce
tout de suite — attendre cinq secondes pour dire « 1 tâche terminée » quand rien
d'autre ne bouge serait une latence sans contrepartie —, tout ce qui arrive
pendant la fenêtre qui suit est dit d'un coup à la fin (`DELAI_ANNONCE_MS` = 5 s,
`DELAI_ARBITRAGE_MS` = 1 s).

**Le partage poli / assertif est une frontière de contenu, pas de ton.**
L'assertive coupe la parole : elle est réservée aux **demandes d'arbitrage
humain** — validations et briefs —, les seuls événements qui attendent une action.
Elle vit dans le shell, **une seule fois**, parce qu'une demande doit s'entendre
quel que soit l'écran ouvert. Tout le reste (tâches, runs, dépense, flux,
messages) part dans la région polie de l'écran. La réciproque compte autant et
elle est testée : les attentes humaines sont **absentes** de `mesuresDesRuns`, et
la région polie de `/validations` ne dit que ce qui a été **tranché** — les dire
des deux côtés les dirait deux fois, une fois en coupant la parole.

Les neuf écrans temps réel et ce que chacun annonce :

| Écran | Région polie | Ce qu'elle dit |
| --- | --- | --- |
| `/` | Activité du tableau de bord | tâches par colonne, runs soldés, dépense au dollar franchi |
| `/runs` | Activité des runs | un run qui démarre, un run qui se solde |
| `/runs/<id>` | Activité du run | les tâches **de ce run**, et son propre statut |
| `/couts` | Dépense du projet | le franchissement d'un dollar, jamais un rafraîchissement |
| `/validations` | Arbitrages tranchés | une décision prise (ailleurs, ou par quelqu'un d'autre) |
| `/journal` | Activité du journal | « 12 nouveaux événements », sur le fil **non filtré** |
| `/brief` | Activité des briefs | ce qui **sort** de la file — un brief approuvé fait démarrer son run |
| `/agents/<nom>/chat` | Activité du fil avec `<nom>` | le compte de messages, jamais leur contenu |
| `/chat` | Activité du fil avec l'orchestration | idem — le libellé suit le destinataire courant (#269) |

Quatre choses à ne pas défaire :

- **La clé du nœud interne n'est pas décorative.** Une région live parle sur
  *mutation*, pas sur affectation : réécrire la même chaîne ne touche pas le DOM,
  donc ne s'annonce pas — et « 1 tâche terminée » deux fenêtres d'affilée est un
  cas courant. La clé force le remplacement du nœud à chaque annonce. C'est la
  seule chose du lot qu'aucune lecture du texte rendu ne peut vérifier, d'où un
  test qui compare l'**identité** des nœuds à texte égal.
- **Rien n'est annoncé au montage**, et c'est ce qui rend la région montable à
  côté du contenu qu'elle décrit : le premier relevé sert de référence. Corollaire
  d'implémentation — une région se monte **après** le chargement de sa source
  (`!chargement`), sinon l'arrivée des données s'annoncerait comme une activité.
- **`role` et `aria-live` sont écrits tous les deux**, bien que `status` implique
  `polite`. Le rôle rend la région adressable en test ; l'attribut est ce que la
  sonde du ticket compte sur écran, et une implication n'est pas une mesure.
- **`sr-only`, jamais `hidden`** : une région masquée par `display:none` n'est pas
  annoncée du tout.

⚠ L'`aria-live` de `AssistantFlottant` **reste** et n'est pas une neuvième région
au sens ci-dessus : c'est un fil de **conversation**, où lire le contenu est le
but, et il n'est dans le DOM que quand quelqu'un a ouvert le panneau. La règle
« une région par écran » porte sur ce qu'un écran annonce **au repos**.

### Les trois places — la règle de sobriété (#539)

Les sections précédentes disent **comment** une chose se rend. Celle-ci dit
**où elle a le droit de se poser**, et c'est la seule du langage visuel qu'une
machine vérifie écran par écran.

Le tableau de bord a déjà été épuré une fois — #191 a ramené cinq panneaux de
plein format à « ce qui se lit d'un coup d'œil ». Six mois plus tard le compte
était refait. La cause n'est pas qu'on ait mal épuré : c'est qu'**aucune règle
n'a été laissée derrière**. Chaque ajout était légitime pris seul ; c'est leur
somme qui refaisait le problème, et « est-ce utile ? » ne l'arrête jamais, parce
que la réponse est toujours oui.

> **Tout ce qu'un écran affiche occupe l'une de trois places, et une seule.**
>
> 1. **Le bandeau de tête** — au plus **4 chiffres**, et rien d'autre. Un chiffre
>    y entre seulement s'il change la décision de l'utilisateur *dans la minute*.
> 2. **Le corps** — au plus **3 blocs de plein format**, plus les blocs
>    d'**arbitrage** (ceux qui demandent une décision humaine), qui ne comptent
>    pas dans le plafond **et disparaissent quand la file est vide**.
> 3. **La colonne de propriétés** — tout le reste : métadonnées, réglages,
>    historique, liens. Elle s'allonge sans plafond, parce qu'elle défile et ne
>    dispute rien au corps.
>
> **Ce qui ne tient dans aucune des trois n'est pas un bloc : c'est une ligne
> avec un renvoi**, vers l'écran dont c'est le sujet.

La règle ne dit pas « moins », elle dit **où** — et elle répond au prochain
ticket sans qu'on ait à juger (docs/30 §4.3) : « ajouter un panneau X au tableau
de bord » → le corps est plein, donc soit X remplace un bloc, soit X est un bloc
d'arbitrage, soit X est une ligne + un renvoi ; « ajouter un 5ᵉ indicateur » →
non, sauf à en retirer un ; « ce réglage doit être visible » → colonne de
propriétés.

**Comment elle se traduit dans le DOM.** Un bloc est une `<section>` ; la colonne
de propriétés est un `<aside>` ; un chiffre de tête est une `TuileChiffre`. Ce
qui n'occupe **aucune** place et ne compte donc pas : une `<nav>` — le filtre de
période de `/couts`, le sommaire de `/parametres`, la bascule de vues d'un run
règlent l'écran ou y naviguent —, et tout ce qui vit **dans** un bloc.

**Ce que le comptage refuse, et pourquoi ces bornes-là.** Le bandeau de tête n'en
est un que si **tous** ses enfants sont des chiffres : sinon, une table posée sous
une tuile sortirait du plafond. Il n'y a **qu'une** colonne de propriétés par
écran : sans cette borne, la seule place sans plafond deviendrait la sortie de
secours de toutes les autres, et emballer chaque bloc dans son `<aside>` rendrait
n'importe quel écran « conforme » sans rien épurer. Et un bloc de premier niveau
**sans nom accessible** fait rougir — c'est ce qui l'empêche d'échapper au
recensement en silence.

**Les deux réponses à un corps qui déborde**, et jamais le simple retrait d'une
information — les deux écrans qui dépassaient sont là pour servir d'exemple :

| Écran | Ce qu'il faisait | Ce qu'il fait |
| --- | --- | --- |
| `/couts` | **5 blocs** : évolution, répartition, table par tâche, table par exécution, grand livre | **3** — la répartition passe en **colonne de propriétés**, les deux tables deviennent un **second niveau** (`BasculeDeVues`) du bloc « Détail de la période », le grand livre reste à part (la période ne le borne pas) |
| `/parametres` | **7 sections** de plein format | **3 familles** (`lib/parametres.ts`), dont les sept sections deviennent les sous-parties. Les ancres, l'impression et le Ctrl+F sont intacts — c'est ce qu'un passage aux **onglets** aurait coûté |

**Ce qui la garde** : `tests/sobriete.test.tsx`, qui monte les dix écrans du menu
et compte. L'arbitrage n'y est pas déclaré, il se **prouve** : chaque écran est
monté deux fois, files pleines puis files vides, et ce qui survit aux deux est ce
que le plafond compte. Un bloc qui prétendrait arbitrer sans disparaître compte
comme les autres. Comme `contraste.test.ts` et `a11y.test.tsx`, la sonde est
**prouvée sur un échantillon fautif avant de balayer** — sans quoi un comptage
mal branché rendrait « 0 dépassement » sur une question jamais posée.

⚠ La règle plafonne des **blocs**, jamais des pixels : jsdom n'en calcule aucun,
et la géométrie reste au skill `/banc-mise-en-page` (#308). Un écran conforme
peut très bien être trop haut ; ce sont deux questions, et deux outils.

### La palette sémantique — `app/globals.css`

Posée par #533 (lot 1 de #532). Avant elle, le produit portait **1 750 couleurs
Tailwind brutes et zéro token sémantique** : chaque couleur s'écrivait deux fois
— une fois nue, une fois en `dark:` —, d'où **542 lignes** sur 59 fichiers à
tenir d'accord à la main, et le multiplicateur de coût de toute retouche
(docs/30 §2.4). Une couleur se choisit désormais **une fois**, et les deux
thèmes viennent avec elle.

| Token | Rôle |
| --- | --- |
| `surface` | ce sur quoi on lit du contenu (la page en clair, la carte en sombre) |
| `surface-creuse` | la surface **en retrait** — la plus sombre des deux, dans les deux thèmes |
| `bord` | le filet qui sépare : contour de carte, séparateur de liste |
| `bord-fort` | le bord qui **identifie un contrôle** : champ, case, contour de bouton |
| `texte` | le texte principal |
| `texte-secondaire` | le second plan — le remplaçant de `text-neutral-400` (2,58:1) |
| `survol` | le fond d'un contrôle **sous le pointeur**, quand il n'a pas d'aplat |
| `accent` | la couleur d'action |
| `info` `positif` `attention` `alerte` | les quatre tons d'état |

`accent` et les quatre états portent **trois** valeurs chacun — quatre pour ceux
qu'un bouton peut porter —, parce qu'un ton ne sert jamais à une seule chose :

| Suffixe | Où il va | Ce qu'il garantit |
| --- | --- | --- |
| *(aucun)* | l'aplat : bouton plein, pastille, bord d'état | ≥ 3:1 sur les deux surfaces |
| `-texte` | le ton **écrit** : lien, libellé, valeur | ≥ 4,5:1 sur les deux surfaces **et** sur son `-creux` |
| `-creux` | le fond teinté d'une pastille | porte son `-texte` à ≥ 4,5:1 |
| `-appui` | l'aplat **survolé** (#535) | ≥ 4,5:1 avec `sur-ton`, et **toujours plus** qu'au repos |

`-appui` s'écarte de `sur-ton` dans les deux thèmes — le pas -800 en clair, le
pas -300 en sombre —, si bien que le libellé d'un bouton ne peut que **gagner**
en contraste au survol : 7,09:1 au pire en clair contre 5,03:1 au repos, 10,33:1
au pire en sombre contre 6,92:1. Un `hover:opacity-90` ou un
`hover:brightness-110` aurait fait l'inverse, en silence. `positif` n'en a
**pas** : un aplat `positif` est un état, jamais une action, donc il n'est jamais
survolé — le trou dit quelque chose.

`survol` est le pendant pour un contrôle **sans aplat** (bouton de contour,
bouton discret, entrée de menu). Il ne pouvait pas être `surface-creuse` : en
sombre la creuse est plus sombre que la surface, ce qui aurait *creusé* le bouton
au survol au lieu de l'éclairer.

`sur-ton` est ce qui s'écrit **sur** un aplat — blanc en clair, presque noir en
sombre. Un seul token pour les cinq tons : c'est une propriété **vérifiée** de la
palette, pas une coïncidence, et cinq tokens identiques la cacheraient.

Quatre choses à savoir avant d'y toucher :

- **`bord` et `bord-fort` ne sont pas deux nuances du même gris.** Un filet
  décoratif est hors du champ de WCAG 1.4.11 et reste discret ; ce qui **borne un
  contrôle** y est soumis et tient 3:1. Les confondre donne soit des cartes
  cerclées de gris moyen, soit des champs qu'on ne voit pas.
- **`accent` et `positif` partagent la famille verte** — le bouton d'action et le
  succès. Ce sont deux **rôles**, donc deux tokens, même si leurs valeurs
  coïncident aujourd'hui ; les dissocier plus tard ne coûtera qu'une valeur.
- **Les valeurs sont écrites en hexadécimal, pas en `oklch()`.** Tailwind v4 émet
  sa propre palette en `oklch()`, qu'aucun parseur `rgb()` naïf ne lit — le piège
  a rendu de faux ratios pendant #471 (docs/30 §3.1). La source est en octets ;
  les teintes, elles, **sont** celles de Tailwind v4, converties une fois, pour
  qu'un écran tokenisé ne jure pas à côté d'un écran encore brut pendant la
  migration.
- **86 paires mesurées** (43 par thème), **0 faute** : les 72 de #533, plus les
  **14** qu'ajoutent `-appui` et `survol` (#535) — ≥ 4,89:1 pour tout ce qui est
  du texte et ≥ 3,19:1 pour un bord. Les marges les plus courtes restent celles
  de #533 : `bord-fort` sur `surface-creuse` (3,40) et `alerte-texte` sur
  `alerte-creux` (5,02). Depuis #534 cette promesse est **gardée** et non plus
  seulement vérifiée : `tests/contraste.test.ts` les rejoue à chaque pipeline
  (job `web-build`), et **refuse** une valeur qu'il ne sait pas lire au lieu de
  la sauter. Y toucher sans le lire coûte un pipeline rouge — c'est le but. Un
  token **ajouté** sans paire y rougit aussi : c'est ce qui empêche le filet de
  vieillir en instantané — et c'est ce qui a fait déclarer les sept paires de
  #535 dans la table plutôt que de les laisser vertes par construction.

Les valeurs vivent dans les blocs `:root` / `[data-theme="sombre"]` et sont
émises telles quelles ; le bloc `@theme inline` ne fait que les brancher sur les
utilitaires (`bg-surface`, `text-texte-secondaire`, `border-bord-fort`,
`bg-alerte-creux`…). C'est là qu'une sonde doit aller lire la palette — un bloc
`@theme inline` n'émet rien.

`--background` / `--foreground` sont **laissés en place** : ils portent la même
valeur que `--surface` (en clair) et `--texte`, mais la règle `body` les consomme
déjà, et les replier sur la palette est un geste de migration.

### L'échelle typographique et la densité — `app/globals.css`

Cinq pas de **texte**, nommés par leur **rôle** plutôt que par leur taille :
`text-annexe` reste juste sous le corps même si sa valeur bouge, là où `text-xs`
fige une décision de rendu dans chaque appel.

| Pas | Taille | Emploi |
| --- | --- | --- |
| `text-micro` | 0,6875 rem | horodatage, exposant — lisible, pas lu |
| `text-annexe` | 0,75 rem | détail, aide, pastille — le second plan |
| `text-corps` | 0,875 rem | le texte courant **et** les titres de section |
| `text-titre` | 1 rem | le titre d'une carte ou d'une section de plein format |
| `text-page` | 1,25 rem | **le titre d'un écran** |

`text-page` a été ajouté par #533 : le plus grand titre courant du produit était
`text-titre` à 16 px — employé **nulle part** —, si bien que **408 des 439 usages
typographiques (93 %) tenaient sur deux pas**, 0,75 et 0,875 rem. C'est la cause
mesurée du rendu « plat » (docs/30 §2.3), pas un manque de goût.

`text-chiffre` (1,5 rem) reste **hors de cette échelle** : c'est un pas
d'affichage, réservé à la valeur d'une tuile de tête. Le compter parmi les cinq
ferait croire à un sixième niveau de titre.

**Un pas, un nom.** Trois tailles étaient rendues par **deux classes chacune** :
`text-xs` (158 usages) *et* `text-annexe` (110) à 0,75 rem, `text-sm` (90) *et*
`text-corps` (50) à 0,875 rem — même corps, mais **pas la même interligne**, donc
des jumelles qui divergeaient déjà en silence. #533 a tranché : **le pas nommé
est la source de la valeur, la classe Tailwind n'en est plus qu'un alias**
(`--text-xs: var(--text-annexe)`). Les deux noms ne peuvent plus porter deux
tailles. On écrit `text-annexe` et `text-corps` ; les 248 appels jumeaux partent
avec les lots de migration.

⚠ L'**interligne** des jumelles n'est **pas** aliasée : `text-xs` garde
`calc(1 / 0.75)` et `text-sm` `calc(1.25 / 0.875)`. Les aligner changerait le
rendu de 248 appels, ce que le lot qui *pose* les tokens s'interdit.

Ces pas **s'ajoutent** à l'échelle Tailwind sans la remplacer. Le symptôme qu'il
en manque un, c'est un `text-[0.6875rem]` improvisé dans un composant — #245 en a
retiré cinq. Un pas de plus se discute dans `globals.css`, pas dans un écran.

La **densité** suit la même logique, portée par la prop `densite` de `Carte` :
`compacte` (0,625 rem) pour ce qui s'empile en nombre, `normale` (0,75 rem) par
défaut, `aeree` (1 rem) pour une section qu'on lit posément, `aucune` quand le
contenu gère son propre padding (un tableau).

Enfin, tout chiffre qui **se compare en colonne** (un tableau) ou qui **change
sous les yeux** (un compteur temps réel) porte la classe `chiffre`
(`font-variant-numeric: tabular-nums`, posée une fois dans `globals.css`) : sans
elle, le passage de « 1 » à « 8 » élargit la valeur et fait sauter la ligne
autour d'elle.

### Le rendu des montants — `lib/format.ts`

Même principe, pour ce qui se lit plutôt que pour ce qui s'habille : les
montants sont rendus **à deux décimales** (#247) par un formateur unique, que
tous les écrans appellent au lieu de reformater dans leur coin — quatre
décimales rendaient « 1,2345 $US » partout, un chiffre qu'on déchiffre au lieu
de le lire. Trois verdicts qu'on ne confond jamais :

| Rendu | Ce qu'il dit |
| --- | --- |
| `—` | rien n'a été rapporté — **inconnu n'est pas nul** |
| `0,00 $US` | zéro rapporté, une vraie mesure |
| `< 0,01 $US` | une dépense réelle, mais sous le centime |

Le troisième existe parce que le cas est **courant** sur un fournisseur local
(#113), où un appel coûte quelques dix-millièmes de dollar : arrondi à
« 0,00 $US », il ferait passer un fournisseur bon marché pour un fournisseur
gratuit. Seules les **graduations d'un axe** échappent à la règle — sur une
série de quelques millièmes, elles tomberaient toutes sur « 0,00 » et l'axe ne
dirait plus rien ; l'exception est déclarée dans ce même module, pas dans le
composant qui dessine.

## Lancer en local

1. **Backend** (API REST + WebSocket, ticket #46) — Redis du docker-compose requis
   pour le flux temps réel multi-process :

   ```bash
   docker compose -f infra/docker-compose.yml up -d
   .venv/Scripts/python.exe -m maestro.controltower.cli   # ou : maestro-api
   ```

   Côté moteur, `maestro-run --publier` (ou un worker de la file #41) alimente le
   canal d'événements ; ajouter `--validation-ui` pour router les demandes de
   validation humaine vers ce tableau de bord (#48) au lieu de la console.

2. **UI** :

   ```bash
   cd apps/web
   npm install
   npm run dev
   ```

   Puis ouvrir <http://localhost:3000>.

L'UI vise `http://localhost:8000` (l'écoute par défaut de `maestro-api`) ; pour un
backend ailleurs, définir `NEXT_PUBLIC_MAESTRO_API_URL` au lancement (variable
inlinée au build par Next.js) :

```bash
NEXT_PUBLIC_MAESTRO_API_URL=http://mon-hote:8000 npm run dev
```

## Modèle de données

Un client charge l'état courant par le REST (`/api/taches`, `/api/agents`) puis
suit les événements (`/ws/evenements`). Le backend projette chaque événement sur
son état **avant** de le diffuser : à réception d'un événement, le REST est déjà
à jour — l'UI recharge donc l'état (rechargements coalescés) au lieu de dupliquer
la projection en TypeScript (`lib/useControlTower.ts`). La connexion WebSocket se
rétablit seule et chaque reconnexion recharge l'état.

## Vérifications

```bash
npm run lint        # ESLint
npm run typecheck   # tsc --noEmit — le typage seul, sans build
npm test            # suite Vitest (une passe)
npm run test:watch  # la même, en continu pendant le développement
npm run build       # build de production (vérifie aussi le typage TS)
```

Le job CI `web-build` (`.github/workflows/ci.yml`) rejoue ces quatre contrôles —
lint, typage, tests, build — quand `apps/web/` change, et `bash scripts/ci/local.sh`
les rejoue à l'identique sur le poste avant d'ouvrir la PR.

`typecheck` fait doublon avec `next build`, qui vérifie déjà le typage : il
existe pour le **vérifier seul**, en quelques secondes au lieu d'un build
complet, et sous une forme qu'une session Claude Code peut lancer — la couche
permissions autorise `npm run …`, jamais un `./node_modules/.bin/tsc` (#236).

### Trois outils, trois questions — sans recouvrement (#308)

| Outil | Répond à | Ne voit pas |
| --- | --- | --- |
| `npm test` (Vitest + jsdom) | logique, rendu, interactions, chaînes de classes | **aucune mise en page** : jsdom ne calcule ni hauteur, ni `overflow`, ni défilement |
| skill `/verify` | le câblage API↔UI réel : WebSocket, absence de rechargement, reprise après coupure | la géométrie de la page |
| skill `/banc-mise-en-page` | **est-ce que ça tient à l'écran ?** hauteurs, défilement, débordements, points de rupture — mesurés dans un vrai navigateur | ni logique, ni temps réel, ni données |

Aucun des trois ne redouble les autres, et c'est voulu. Un test Vitest peut
exiger qu'un `min-h-0` soit présent sur **chaque** maillon de la chaîne flex
(`tests/kanban.test.tsx`, #248) ; il ne peut pas dire que la section monte à
5 198 px, jsdom ne calculant aucune mise en page. Le banc dit le pixel, et rien
d'autre : il ne remplace pas un test de non-régression, il dit **où regarder**
pour l'écrire.

Le déclencheur du banc : dès qu'un ticket porte sur des **hauteurs, du
défilement, de l'`overflow`, des éléments collants ou du responsive**. #306 — le
bas du formulaire de la porte d'entrée inatteignable — est passé au travers des
tests, du lint, du typage et de `next build` : la suite verte ne prouve rien sur
la mise en page.

### Le filet d'accessibilité (#537)

Le travail d'accessibilité du produit était déjà sérieux — **104 `aria-label` sur
48 fichiers**, 44 rôles corrects, un `<h1>` par écran et **0 saut de niveau**
(docs/30 §2.1). Ce qui manquait n'était pas de la rigueur, c'était **ce qui la
garde** : `axe-core` 4.12.1 était dans le dépôt depuis toujours — en transitif,
tiré par `eslint-plugin-jsx-a11y` — et n'avait **jamais été importé une seule
fois**. Les deux paquets sont désormais des dépendances **déclarées** : une
dépendance transitive n'est pas un contrat, et un `npm dedupe` chez quelqu'un
d'autre suffisait à faire disparaître le filet.

Quatre mécanismes, et chacun garde ce que les autres ne voient pas :

| Mécanisme | Où | Ce qu'il refuse |
| --- | --- | --- |
| `plugin:jsx-a11y/recommended` en **`error`** | `eslint.config.mjs` | les ~36 règles du preset, contre **6 en `warn`** auparavant — un `warn` ne fait pas rougir un pipeline, donc ne garde rien |
| `axe-core` sur les **10 écrans** | `tests/a11y.test.tsx` | toute violation `serious`/`critical` sur un écran monté dans son shell réel |
| `motion-reduce:` | balayage des sources, même fichier | une utilité `transition`/`animate-` écrite **sans sa garde** dans la même chaîne de classes |
| `CIBLE_MINIMALE` | rendu des 10 écrans, même fichier | un lien ou bouton **en petit corps** sans plancher de 24 px |

Trois règles à connaître avant de toucher à un écran :

1. **Toute utilité de mouvement s'écrit avec sa garde**, sur la même chaîne :
   `transition-colors motion-reduce:transition-none`,
   `animate-pulse motion-reduce:animate-none`. Le produit en comptait **19** au
   lot (15 transitions, 4 animations) et **zéro** garde.
2. **Toute cible interactive en petit corps porte `CIBLE_MINIMALE`** (voir « Les
   primitives » ci-dessus).
3. **Le `<main>` du shell est une ancre** (`ID_CONTENU_PRINCIPAL`), visée par le
   lien d'évitement qui ouvre le `Shell`. Il porte `tabindex="-1"` : sans lui,
   suivre l'ancre déplace le point d'insertion du document mais **pas le focus**,
   et la tabulation suivante repart du menu — c'est-à-dire de ce que le lien
   devait faire sauter.

⚠ **Quatre règles axe sont écartées, et aucune ne l'est par confort** —
`color-contrast`, parce que jsdom ne calcule aucune couleur et que le contraste
est gardé **mieux** ailleurs (`contraste.test.ts` mesure 36 paires par thème sur
les octets de `globals.css`, #534) ; `html-has-lang`, `html-lang-valid` et
`document-title`, parce qu'elles jugent le **document qui enveloppe** l'écran —
le `<html lang="fr">` et le `metadata.title` de `app/layout.tsx`, que le rendu
d'un composant ne monte pas. Les garder ferait rapporter une faute du **harnais**
comme une faute du produit, sur les dix écrans à la fois. Elles ne disparaissent
pas pour autant : elles changent de juge, et `tests/a11y.test.tsx` les vérifie
sur la source du layout.

⚠ **Une seule règle `jsx-a11y` est éteinte, sur deux lignes d'un seul fichier** :
`no-static-element-interactions` et `no-noninteractive-tabindex` sur le wrapper
d'`Infobulle` (#536). Les deux décrivent le défaut **inverse** de celui-là — un
élément inerte rendu *actionnable* à la main —, alors que ce wrapper n'active
rien : il rend une description **atteignable au clavier**, ce qui est le motif
ARIA du `tooltip` quand le contenu décrit n'est pas focusable, et son unique
`onKeyDown` referme la bulle sur `Échap` (WCAG 2.1 §1.4.13). L'exemption est
posée **à la ligne près et pour ces règles-là**, jamais au fichier ni à la
configuration : un `off` dans `eslint.config.mjs` ferait taire la règle sur les
48 autres fichiers, c'est-à-dire partout où elle attrape le vrai défaut.

#### Les deux exemptions de fond — arrêtées, écrites, et non découvertes plus tard

Le niveau visé est **WCAG 2.2 niveau AA sur les dix écrans**. Deux exemptions
sortent de ce périmètre. Elles ont été arrêtées par la recherche #471 (docs/30
§3.5) et sont écrites ici parce qu'une exemption qu'on ne trouve pas dans la doc
du produit est une exemption que le prochain ticket prendra pour un oubli — ou,
pire, pour un défaut à corriger dans l'urgence d'une revue.

1. **Le graphe de pipeline (`VuePipeline`) n'est pas rendu accessible nœud à
   nœud.** Aucun motif ARIA n'établit comment lire un DAG à un lecteur d'écran :
   il n'y a ni rôle pour « ce nœud a deux prédécesseurs », ni convention de
   parcours. Ce qui rend l'exemption acceptable n'est pas cette absence mais la
   **contrepartie** : le run porte une **alternative textuelle équivalente** — sa
   vue Kanban et son journal, à un onglet de là, donnent la même information sous
   une forme linéaire. L'exemption tomberait le jour où le graphe porterait une
   information qu'aucune des deux autres lectures ne donne.
2. **Le niveau AAA n'est pas visé.** Son contraste de 7:1 imposerait
   `neutral-700` au minimum pour **tout** texte secondaire, ce qui supprimerait
   la distinction primaire/secondaire dont la densité de ces écrans dépend : on
   paierait la conformité d'un niveau par la lisibilité de tous les autres. AA
   est tenu, mesuré, et gardé par `contraste.test.ts`.

⚠ Ni l'une ni l'autre n'est un blanc-seing sur son voisinage : le graphe reste
soumis au reste du filet (contraste, mouvement, taille des cibles, lint), et
« AAA non visé » ne dispense d'**aucun** critère AA.

### La suite de tests

Posée par le ticket #124 (lot final de la refonte #116, où les tests des lots 1
à 7 étaient différés — convention docs/10 §5.1), étendue par #193 à la
navigation v2 (#189, même convention). **Vitest + Testing Library** sur un DOM
`jsdom` : ces tests portent sur le comportement et le rendu, pas sur le pixel —
le bout en bout dans un vrai navigateur reste le rôle du skill `/verify`, et la
géométrie celui du skill `/banc-mise-en-page` (voir ci-dessus).

| Fichier | Ce qu'il couvre |
| --- | --- |
| `tests/navigation.test.tsx` | Le menu unique, la sidebar, la barre supérieure (#117) ; une entrée par intention et les renvois par libellé (#189) |
| `tests/theme.test.tsx` | Choix clair/sombre/système, script d'init, accord des deux contrôles (#118) |
| `tests/notifications.test.tsx` | Tri du notable, badge, décision depuis le panneau (#119) |
| `tests/identite.test.tsx` | Le monogramme et ses déclinaisons favicon/ICO/PNG (#120) |
| `tests/parametres.test.tsx` | Sommaire, ancres, préférences du poste (#121) |
| `tests/guide.test.tsx` | Déclenchement unique, étapes, sortie clavier, ancres et pages réelles (#122, #193) |
| `tests/assistant.test.tsx` | Ouverture, envoi, échec d'envoi, non-fermeture au clic extérieur (#123) |
| `tests/shell.test.tsx` | La composition : les sept lots effectivement branchés dans le cadre |
| `tests/agents.test.tsx` | La fiche agent à onglets, la liste, et la survie des chemins v1 par redirection (#190, testé en #193) |
| `tests/tableau-de-bord.test.tsx` | Le tableau de bord épuré — ce qui reste, ce qui renvoie ailleurs — et le ticket externe dans les tables de coûts (#191/#192, testés en #193) ; puis le **second niveau de `/couts`** (#539) : la vue par tâche à l'ouverture, la bascule vers la vue par exécution **sans quitter le bloc** (c'est un second niveau, pas une navigation), la répartition par agent rangée dans la colonne de propriétés, et le bloc qui s'efface quand la période n'a ni tâche ni exécution — les chiffres, eux, restent |
| `tests/ticket-externe.test.tsx` | Le filtrage d'URL et les cartes du Kanban (#192, livré avec le lot : logique critique) |
| `tests/detail-tache.test.tsx` | Le panneau de détail d'une tâche : description, étapes en checklist, liens filtrés et rendus selon leur nature, et la carte laissée intacte quand il n'y a rien à ouvrir (#251, livré avec le lot : filtrage d'URL et absence totale) |
| `tests/parametres-mcp.test.tsx` | La bibliothèque MCP face au gestionnaire de mots de passe du navigateur : cloisonnement des champs secrets et panneau oublié quand son entrée quitte les résultats (#231) |
| `tests/projets.test.tsx` | L'écran Projets : racine choisie dans l'explorateur servi par l'API (jamais saisie), refus motivé qui ne casse ni la liste ni la navigation, dossier vide distinct d'un refus (#225) |
| `tests/journal.test.tsx` | La page Journal : fil sans limite, filtres par type/agent/tâche, recherche jusque dans le détail, « notable seulement » aligné sur la cloche (#249) |
| `tests/activite.test.tsx` | Les lignes d'activité : repli des rafales, horodatage relatif, détail brut à un clic, garde des types inconnus (#250) |
| `tests/socle-visuel.test.tsx` | Le langage visuel (#245) : le jeu d'icônes (SVG à `currentColor`, toutes décoratives), les primitives et leurs deux thèmes, et **aucun émoji rendu** sur les écrans de la vague |
| `tests/kanban.test.tsx` | La section Tâches qui prend la place (#248) : colonnes de la machine à états, colonne « Autres », **chaîne d'étirement entière** et défilement rendu à chaque colonne |
| `tests/format.test.ts` | Les montants à deux décimales et leurs trois verdicts — « — », « 0,00 $US », « < 0,01 $US » —, l'exception des graduations d'axe, durées et tokens (#247) |
| `tests/projet-actif.test.tsx` | La porte d'entrée : aucun écran n'est atteint sans projet actif, le choix retenu est confronté à l'état réel, et la page demandée revient sans redirection (#279) |
| `tests/selecteur-projet.test.tsx` | Le sélecteur du shell : bascule sans quitter la page, gestion atteinte sans chemin en dur, et « Projets » sorti de la sidebar sans que son écran cesse d'être servi ni titré (#280) |
| `tests/composer.test.tsx` | Composer un objectif : dossier pris dans l'explorateur (jamais saisi), aperçu gratuit qui ne lance rien et se périme dès qu'une source change, refus posé **sur la source qu'il vise** sans perdre la saisie, et « ignoré » qui n'est pas un refus (#319) |
| `tests/fil-sources.test.tsx` | Le fil qui accepte des sources, **logique critique du lot seule** (#482, le reste différé à #485) : un fichier glissé sur la conversation part par son **identifiant de téléversement** et jamais par ses octets ni son nom — c'est ce qui garantit qu'il n'atterrit pas dans le dossier de l'utilisateur, et rien à l'écran ne le dirait s'il cessait d'être vrai ; un message fait de **sources seules** est légitime ; un **refus reste dans le fil**, sur la source qu'il vise, sans perdre ni le texte ni la matière ; le **rapport de lecture** est replié sous le message qui l'a porté et se déplie sur place, l'image y ressortant « Ignoré / `format-non-gere` » au lieu de disparaître ; et une bulle sans source reste **strictement** celle d'avant le lot |
| `tests/brief.test.tsx` | Valider le brief, **logique critique du lot seule** (#322, le reste différé à #323) : approuvé **corrigé** vs approuvé **tel quel** (`brief: null`, qui fait retenir au moteur sa propre proposition), refus qui n'emporte jamais de brief, réponses appariées **par position** aux questions (chaînes vides comprises), et le coût engagé rendu face à la décision |
| `tests/fil-cadrage.test.tsx` | Le cadrage décidé **dans le fil**, **logique critique du lot seule** (#483, le reste différé à #485) : le **canal reste le canal** — le fil rappelle `trancherBrief`/`repondreAuBrief`, donc les deux routes de #320/#321, avec le contrat entier (`brief: null` tel quel, brief corrigé sinon, jamais de brief sur un refus, une réponse par question) ; le **rang du tour et son plafond** restent en clair ; les tours joués se **déroulent** au lieu de se replier, le sans-réponse nommé ; et surtout le critère 3, seul dont l'échec est **invisible depuis l'écran qu'on regarde** — les trois surfaces qui montrent un run suspendu résolvent leur destination par le menu, donc un renvoi resté sur « Valider le brief » s'éteindrait sans un mot le jour où #484 retire l'entrée |
| `tests/runs-perdus.test.tsx` | Les runs perdus (#349, testés en #351) : **la règle avant le panneau** (`lib/execution.ts`), qui n'est proposé que sur un `orphelin` **au brief approuvé** — l'API accepte pourtant de relancer un `indetermine`, et cet écart entre *accepter* et *proposer* est le sujet ; puis le panneau, absent quand rien n'est récupérable, désarmé pendant la reprise (un double clic partirait deux fois) et rendant le refus de l'API tel quel |
| `tests/runs-liste.test.tsx` | La liste des runs (#474, testée en #480) : **le régime avant l'écran** (`regimeDuRun`), dont l'ordre de décision *est* la décision — soldé, puis interrompu, puis en pause, puis suspendu ; la `CarteRun` que **trois** écrans rendent (badge, avancement, cause d'arrêt #479, ligne de pause #477, ordres de pause et leur refus) ; **l'interruption** (#467) — `peutEtreInterrompu` sur les quatre états en vol et les trois issues, sa **divergence assumée** avec `peutEtreSuspendu` sur l'orphelin (la pause l'écarte, l'annulation non : l'API borne son attente et solde le run de toute façon), le premier clic qui n'envoie rien, la phrase de perte qui ne paraît qu'armée, le refus affiché **et** désarmé, et la rangée `GestesRun` dans ses quatre configurations ; puis l'écran dans ses quatre états, dont « vide » et « injoignable », qui ne se confondent pas |
| `tests/runs-vue.test.tsx` | La vue d'un run (#475/#478, testée en #480) : les tâches lues **avec `?run=`** et non filtrées sur `Tache.run_id` — le champ porte le *dernier* run qui les a touchées, une relance volerait celles du run repris —, la relecture au **pouls** du shell sans seconde WebSocket, les trois vides (autre projet, arrêt sur brief, API muette) et le journal persisté fusionné au direct sans doublon — atteint **par son onglet** depuis #516, avec le contrôle qu'il ne s'affiche ni sous le pipeline ni sous le Kanban |
| `tests/pipeline.test.tsx` | La vue pipeline d'un run (#491, testée en #492) en **trois étages**, parce qu'ils ne se gardent pas de la même façon : les règles hors JSX (`lib/graphe` — le backend sert tout ce qui se dessine, ce module ne porte que les trois questions qu'il ne pose pas, et l'**ordre** dans lequel elles sont posées *est* la décision ; `lib/vuesRun` — le pipeline ouvre) ; la checklist rendue (`components/EtapesTache` — **une case par étape**, le contrôle qui compte étant le dénominateur qui grandit sans que le numérateur bouge) ; puis la vue montée : le nœud en cours, l'étape qui se coche au battement suivant, l'arête qui s'allume, et l'attente humaine qui ne se lit plus « en cours » |
| `tests/etat-des-runs.test.tsx` | L'état des runs au tableau de bord (#476, testé en #480) : **l'exhaustivité de la table des groupes**, balayée sur `regimeDuRun` plutôt qu'énumérée — un régime sans groupe fait disparaître ces runs-là de l'écran, ce qui est arrivé à « en pause » entre #476 et #477 — puis le plafond des soldés et ce qu'il annonce, `soldeAujourdHui` sur ses trois entrées, et l'écran qui ne porte **aucun** geste |
| `tests/a11y.test.tsx` | Le **filet d'accessibilité** (#537) en trois étages : `axe-core` joué sur les **10 écrans** montés dans leur shell réel, verdict **0 violation `serious`/`critical`** — table d'écrans **dérivée de `MENU`**, donc une page ajoutée au menu sans cas d'audit rougit ; puis ce qu'axe ne sait pas voir — le **lien d'évitement** (premier dans l'ordre du DOM, visant un `<main>` que le focus peut atteindre), la **garde de mouvement** sur chaque utilité `transition`/`animate-` du produit, et le **plancher de 24 px** des cibles en petit corps. Comme `contraste.test.ts`, **la sonde est prouvée avant de servir** : sur un fragment fautif (image sans alternative, bouton sans nom, champ sans étiquette), puis sur un fragment sain |
| `tests/regions-live.test.tsx` | Les régions live des écrans temps réel (#538) : le **vocabulaire sans DOM** (seules les hausses parlent, un franchissement dit le total, les deux attentes humaines **absentes** du relevé des runs) ; la **présence** écran par écran, comptée sur l'attribut `aria-live` comme la sonde du ticket — une polie, zéro assertive ; le **contenu** après un événement simulé ; le **débit**, où une rafale de trois tâches ne coûte que deux phrases et douze événements du journal une seule ; et l'**assertive** avec sa réserve — unique dans le shell, muette sur une tâche terminée, et jamais redite par la région polie de l'écran qui montre l'arbitrage |
| `tests/sobriete.test.tsx` | La **règle des trois places** (#539, voir « Le langage visuel » ci-dessus) rendue opposable : les 10 écrans du menu recensés, bandeau de tête ≤ 4 chiffres, corps ≤ 3 blocs, une seule colonne de propriétés, aucun bloc anonyme. Rien n'y est **déclaré** — le bandeau se reconnaît à ses `TuileChiffre`, la colonne à sa balise `<aside>`, et l'**arbitrage se prouve** en montant chaque écran une seconde fois files vides : un bloc qui prétendrait arbitrer sans disparaître compterait comme les autres. Sonde prouvée sur un échantillon fautif avant de balayer, comme `contraste.test.ts` |
| `tests/contraste.test.ts` | Le contraste de la palette sémantique (#534) : les **36 paires légitimes par thème** de #533 mesurées en octets dans `globals.css`, au seuil 4,5:1 (texte) ou 3:1 (contour, aplat d'état) — **et la sonde prouvée avant de servir**, sur les ratios que #471 avait mesurés au navigateur puis sur une faute glissée exprès. Le contrôle qui en fait un filet plutôt qu'un instantané est le dernier : un token ajouté sans paire **rougit** au lieu d'être vert par construction |

Cinq fichiers portent l'outillage plutôt que des tests :

- `tests/setup.ts` — ce que jsdom ne fournit pas (`matchMedia`, `ResizeObserver`,
  `scrollIntoView`), la remise à zéro entre deux tests (stockage, `data-theme`,
  DOM), et le **réseau débranché** : `useControlTower`, `useChat` et la lecture
  des projets déclarés sont mockés globalement, si bien qu'aucun test n'a besoin
  de backend ni de faux serveur ;
- `tests/aides.tsx` — les fabriques du domaine (agent, événement, validation,
  message, projet, **run** depuis #480, **nœud et graphe** depuis #491 — ce
  dernier *dérivé* de ses nœuds : `niveaux` regroupe sur le `niveau` que chaque
  nœud porte déjà, rien n'y est retrié, le tri topologique appartenant au
  backend), `poserProjetActif` (le projet retenu sans
  lequel tout rendu du shell s'arrête à la porte) et `rendreAvecEtat`, qui monte
  un composant sous le **vrai** fournisseur d'état du shell avec une source temps
  réel factice ;
- `tests/ecrans.tsx` — **les dix écrans** (#537, extrait ici par #539) : quel
  composant chaque route rend, l'état partagé dans lequel on les monte
  (`peuplerEtat`, files d'arbitrage pleines, et `peuplerEtatSansArbitrage`, files
  vides mais projet toujours peuplé — tout vider ferait basculer le tableau de
  bord sur `PosteVide`, donc mesurer un autre écran), et `monterEcran`, qui monte
  sous le **vrai** `Shell` et attend que la garde du projet ouvre. `a11y` et
  `sobriete` s'en servent tous les deux : deux tables recopiées seraient le
  premier moyen qu'une suite rende un verdict sur un produit que l'autre ne monte
  plus ;
- `tests/ecrans-reseau.ts` — les fabriques de mock des mêmes écrans, **séparées**
  du fichier ci-dessus et pas par confort : `vi.mock` est hissé en tête du
  fichier de test, ses fabriques ne peuvent donc charger leurs dépendances que
  dedans (`await import(…)`) — et si ce qu'elles chargent importait les pages, le
  mock de `useAnalyticsCouts` se rappellerait lui-même par `app/couts/page` et
  rendrait un module à moitié construit ;
- `tests/axe.ts` — le branchement d'`axe-core` (#537) : le seuil du verdict
  (`serious`/`critical`), les règles écartées **avec la raison de chacune**, et
  le récit d'un échec, qui nomme la règle et le nœud plutôt que de rendre un
  nombre. Le contexte donné à axe est le **document entier** et non le conteneur
  rendu : les règles de *page* (`region`, `bypass`, `landmark-one-main`,
  `page-has-heading-one`) ne se jouent qu'à ce prix, et les passer à côté
  reviendrait à auditer des composants là où on veut auditer des écrans.

⚠ Trois pièges de ce harnais, les deux premiers apparus en écrivant les trois
suites de runs, le troisième en montant les dix écrans de #537 :

- **`chargerTaches` n'est pas mocké par `tests/setup.ts`**, contrairement à
  `chargerProjets` et `chargerJournal` — et **`chargerGrapheExecution` non plus**
  depuis #490. Un écran qui les lit — la vue d'un run — part donc sur un vrai
  `fetch` et n'affiche qu'une bannière d'erreur : il lui faut un
  `vi.mock("@/lib/api")` local. Ce mock **remplace** celui du setup, d'où le
  `importOriginal` et la reconduction des deux autres lectures ;
- **`runFactice` ne pose que les champs obligatoires** du contrat. `vitalite`,
  `progression`, `en_pause` et `cause` restent **absents** plutôt que posés à une
  valeur neutre — c'est ce que rend un backend antérieur au lot qui les a ajoutés,
  donc le cas qu'un écran doit savoir traiter ;
- **la liste des lectures non mockées est plus longue qu'on ne croit** :
  `chargerCatalogue` (Agents, Paramètres › Fournisseurs), `chargerSante`
  (Paramètres › Général), `chargerPoolMcp`/`chargerRegistreMcp` (Paramètres ›
  MCP), `chargerExplorateur`/`chargerDisponibiliteSelecteur` (Composer) et
  `chargerExecution` (Valider le brief). Un test qui rend un **écran entier**
  plutôt qu'un composant les rencontre toutes. Et `useAnalyticsCouts` (page
  Coûts) se mocke **au hook** et non à l'API, parce qu'il ouvre sa propre
  WebSocket et se reconnecte en backoff : la couper à la source laisserait la
  promesse « aucun test n'a besoin de backend » tenue par un `fetch` qui échoue
  et des minuteurs qui survivent au test.

Quelques tests méritent d'être connus parce qu'ils gardent des invariants
qu'aucun outil n'attrape — ni le lint, ni le build, ni un rendu :

- celui qui **exécute** le script d'init du thème pour le confronter au module,
  sans quoi la page clignoterait au chargement ;
- ceux qui confrontent une **liste déclarée** aux **routes réellement présentes**
  sous `app/` : les entrées du menu (`lib/navigation.ts`), les destinations des
  redirections v1 (`next.config.ts`) et les ancres `data-guide` visées par la
  visite guidée. Une page supprimée laisserait sinon une entrée de menu vers un
  404, un signet redirigé vers le vide et une étape de visite sans cible ;
- celui qui vérifie que chaque redirection v1 vise un **onglet déclaré**
  (`lib/agents.ts`). Le contrôle de route ne suffit pas ici : `[onglet]` répond à
  *n'importe quel* segment, si bien qu'une faute de frappe rendrait bien une page
  — le profil, silencieusement, au lieu de l'onglet demandé ;
- celui qui vérifie qu'après le choix du projet **rien n'a été poussé** au
  routeur (#279). La garde du shell et une redirection vers un écran de choix
  donnent le même parcours à l'écran ; seule cette assertion distingue les deux,
  et c'est elle qui garantit qu'un lien profond retrouve sa page ;
- celui qui exige qu'une page **sortie du menu** garde son titre et sa route
  (#280). Retirer « Projets » de `MENU` la rendait anonyme (« Control Tower »)
  sans rien casser d'autre : le chemin répondait encore, le lint et le build
  aussi. C'est `HORS_MENU` qui sépare « pas dans la sidebar » de « pas dans
  l'application », et seule cette paire d'assertions le garde ;
- celui qui exige que les champs secrets de la bibliothèque MCP soient enfermés
  dans un `<form>` et la recherche **dehors** (#231). Un `<input type="password">`
  sans propriétaire de formulaire est apparié par le navigateur aux champs texte
  du document : c'est ce qui remplissait la recherche d'un identifiant
  enregistré. Rien dans un rendu ne distingue ce `<form>` d'une `<div>` — seule
  cette frontière, testée pour elle-même, empêche un futur remaniement de
  ramener le bug ;
- celui qui **parcourt** la chaîne d'étirement du Kanban (#248), de la zone
  défilante d'une colonne jusqu'à la section. jsdom ne calcule aucune mise en
  page : ce qui se teste n'est pas une hauteur en pixels mais la présence de
  `min-h-0` sur **chaque** maillon flex — sans lui, le `min-height:auto` par
  défaut empêche l'élément de rétrécir sous son contenu, le débordement remonte
  à la page entière et l'ascenseur de colonne ne sert plus à rien. Un maillon
  oublié ne se voit ni au lint, ni au build, ni dans un test qui ne regarderait
  que le texte ;
- ceux qui vérifient qu'**aucun émoji n'est rendu** par les écrans de la vague
  v3 (#245). Le contrôle porte sur le **rendu**, pas sur les sources : le dépôt
  cite des émojis dans ses commentaires (« l'ancien 📁 »), et c'est ce que
  l'utilisateur voit qui est en cause. C'est ce garde-fou qui a rattrapé le
  panneau de détail (#251), écrit avant que le socle ne soit posé et qui signait
  encore ses lignes d'un 🤖 et d'un glyphe par nature de lien.
