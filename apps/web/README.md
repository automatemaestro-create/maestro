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
  (run en cours, tâches par statut, agents occupés et libres, dépense), le
  Kanban, puis un
  **aperçu** de l'activité. Les trois panneaux de plein format qui s'y empilaient
  n'ont pas disparu, ils sont **rangés**, et chaque tuile **renvoie** vers la page
  où le détail vit maintenant (fiches d'agent → Agents, grand livre par exécution
  → Coûts & analytics). Les renvois sont résolus par le menu
  (`entreeParLibelle`) et non par un chemin en dur : ils suivent une page qui
  déménage, et **ne s'allument pas** vers une page qui n'existe pas encore. Le
  fil d'activité en a fait la démonstration : son renvoi, écrit dès #191, est
  resté éteint jusqu'à ce que #249 crée le Journal — sans une ligne de plus dans
  `FilActivite` ;
- **Journal** (#249, lot 5 de #242) : le fil d'activité en **plein format**, avec
  filtres par type d'événement, par agent et par tâche, recherche texte et une
  case « Notable seulement » qui reprend le filtre de la cloche
  (`estNotableNotification`, #119 — pas de seconde logique de tri). La page rend
  le même `FilActivite` que l'aperçu, sans `limite` : une seule mise en forme de
  ligne pour les deux écrans. Le fil y est **éphémère** — les 50 derniers
  événements reçus depuis l'ouverture de la page, remis à zéro au rechargement —
  et l'écran le dit : le journal persisté et requêtable (`GET /api/journal`)
  existe côté contrat (#183) mais n'est pas encore servi par le backend ;
- **Tableau de bord temps réel** : état des agents (libre/occupé, tâche courante,
  compteurs, coût cumulé) et des tâches, mis à jour par WebSocket sans rechargement ;
- **Kanban** des tâches par statut (machine à états docs/03 §3) ;
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
- **Fil d'activité** en direct (statuts, activités d'agents, messages inter-agents) ;
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
  agent (chantier « Chat » de la Phase 6). Les onglets sont déclarés une seule
  fois (`lib/agents.ts`), comme le menu l'est dans `lib/navigation.ts` ;
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

Posé par #245 (lot 1 de #242), il tient en trois fichiers. Ce qui suit n'est pas
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

Cinq briques, et le `className` qu'on n'écrit plus :

| Brique | Ce qu'elle porte |
| --- | --- |
| `Carte` | la surface : bord, fond, ombre, arrondi, **densité**, **ton** |
| `TuileChiffre` | un chiffre de tête, son libellé, son détail, son renvoi |
| `EnTeteSection` | le titre d'une zone, son icône, ce qui l'accompagne |
| `BadgeEtat` | la pastille d'état (compte, statut, provenance, temps réel) |
| `EtatVide` | ce qui manque, et par où l'obtenir |

Chaque brique porte ses variants `dark:` **elle-même** : c'est la seule façon de
garantir qu'aucun écran n'oublie le thème sombre, et le point sur lequel les
classes recopiées divergeaient le plus.

Le **ton** d'une `Carte` (`pleine`, `creuse`, `attention`, `attentionClaire`) est
un choix nommé, pas un `bg-*` passé en `className` : deux règles de fond dans le
même attribut ne se départagent pas par l'ordre d'écriture mais par celui de la
feuille générée — une surcharge au cas par cas est silencieusement instable.

### L'échelle typographique et la densité — `app/globals.css`

Cinq pas, nommés par leur **rôle** plutôt que par leur taille : `text-annexe`
reste juste sous le corps même si sa valeur bouge, là où `text-xs` fige une
décision de rendu dans chaque appel.

| Pas | Taille | Emploi |
| --- | --- | --- |
| `text-micro` | 0,6875 rem | horodatage, exposant — lisible, pas lu |
| `text-annexe` | 0,75 rem | détail, aide, pastille — le second plan |
| `text-corps` | 0,875 rem | le texte courant **et** les titres de section |
| `text-titre` | 1 rem | le titre d'un écran ou d'une carte de plein format |
| `text-chiffre` | 1,5 rem | la valeur d'une tuile de tête, et elle seule |

Ces pas **s'ajoutent** à l'échelle Tailwind sans la remplacer ; c'est celle-ci
que le produit emploie. Le symptôme qu'il en manque un, c'est un
`text-[0.6875rem]` improvisé dans un composant — le lot en a retiré cinq. Un pas
de plus se discute dans `globals.css`, pas dans un écran.

La **densité** suit la même logique, portée par la prop `densite` de `Carte` :
`compacte` (0,625 rem) pour ce qui s'empile en nombre, `normale` (0,75 rem) par
défaut, `aeree` (1 rem) pour une section qu'on lit posément, `aucune` quand le
contenu gère son propre padding (un tableau).

Enfin, tout chiffre qui **se compare en colonne** (un tableau) ou qui **change
sous les yeux** (un compteur temps réel) porte la classe `chiffre`
(`font-variant-numeric: tabular-nums`, posée une fois dans `globals.css`) : sans
elle, le passage de « 1 » à « 8 » élargit la valeur et fait sauter la ligne
autour d'elle.

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

Le job CI `web-build` (`.gitlab-ci.yml`) rejoue ces quatre contrôles — lint,
typage, tests, build — quand `apps/web/` change, et `bash scripts/ci/local.sh`
les rejoue à l'identique sur le poste avant d'ouvrir la MR.

`typecheck` fait doublon avec `next build`, qui vérifie déjà le typage : il
existe pour le **vérifier seul**, en quelques secondes au lieu d'un build
complet, et sous une forme qu'une session Claude Code peut lancer — la couche
permissions autorise `npm run …`, jamais un `./node_modules/.bin/tsc` (#236).

### La suite de tests

Posée par le ticket #124 (lot final de la refonte #116, où les tests des lots 1
à 7 étaient différés — convention docs/10 §5.1), étendue par #193 à la
navigation v2 (#189, même convention). **Vitest + Testing Library** sur un DOM
`jsdom` : ces tests portent sur le comportement et le rendu, pas sur le pixel —
le bout en bout dans un vrai navigateur reste le rôle du skill `/verify`.

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
| `tests/tableau-de-bord.test.tsx` | Le tableau de bord épuré — ce qui reste, ce qui renvoie ailleurs — et le ticket externe dans les tables de coûts (#191/#192, testés en #193) |
| `tests/ticket-externe.test.tsx` | Le filtrage d'URL et les cartes du Kanban (#192, livré avec le lot : logique critique) |
| `tests/detail-tache.test.tsx` | Le panneau de détail d'une tâche : description, étapes en checklist, liens filtrés et rendus selon leur nature, et la carte laissée intacte quand il n'y a rien à ouvrir (#251, livré avec le lot : filtrage d'URL et absence totale) |
| `tests/parametres-mcp.test.tsx` | La bibliothèque MCP face au gestionnaire de mots de passe du navigateur : cloisonnement des champs secrets et panneau oublié quand son entrée quitte les résultats (#231) |
| `tests/projets.test.tsx` | L'écran Projets : racine choisie dans l'explorateur servi par l'API (jamais saisie), refus motivé qui ne casse ni la liste ni la navigation, dossier vide distinct d'un refus (#225) |
| `tests/projet-actif.test.tsx` | La porte d'entrée : aucun écran n'est atteint sans projet actif, le choix retenu est confronté à l'état réel, et la page demandée revient sans redirection (#279) |
| `tests/selecteur-projet.test.tsx` | Le sélecteur du shell : bascule sans quitter la page, gestion atteinte sans chemin en dur, et « Projets » sorti de la sidebar sans que son écran cesse d'être servi ni titré (#280) |

Deux fichiers portent l'outillage plutôt que des tests :

- `tests/setup.ts` — ce que jsdom ne fournit pas (`matchMedia`, `ResizeObserver`,
  `scrollIntoView`), la remise à zéro entre deux tests (stockage, `data-theme`,
  DOM), et le **réseau débranché** : `useControlTower`, `useChat` et la lecture
  des projets déclarés sont mockés globalement, si bien qu'aucun test n'a besoin
  de backend ni de faux serveur ;
- `tests/aides.tsx` — les fabriques du domaine (agent, événement, validation,
  message, projet), `poserProjetActif` (le projet retenu sans lequel tout rendu
  du shell s'arrête à la porte) et `rendreAvecEtat`, qui monte un composant sous
  le **vrai** fournisseur d'état du shell avec une source temps réel factice.

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
  ramener le bug.
