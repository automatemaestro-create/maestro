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
  action de la page. ⚠ **Ce qui répond a changé, et le contrat d'affichage avec**
  (#748, lots #763/#764/#765) : l'assistant ne reconnaissait la forme d'une phrase
  que pour servir une réponse écrite d'avance — une **table de mots-clés**, qui
  répondait avec aplomb sur un écran dès qu'un mot y traînait (« pipeline Jenkins »
  ramenait la page Runs). Elle a cessé d'être un juge : le modèle **lit la
  documentation du produit** (`docs/`, ce README) et répond à partir des seules
  sections qu'il a demandées. Trois conséquences à l'écran — les réponses portent
  un bloc **« Sources lues »**, construit à partir de ce qui lui a été passé et
  jamais recopié de sa réponse ; il **dit qu'il ne sait pas** quand la
  documentation ne porte rien, au lieu d'orienter vers l'écran le plus proche ; et
  **sans fournisseur de modèle** (la démo #65, un poste non configuré) le panneau
  répond quand même, par la table restée en **repli**, en annonçant qu'il n'a rien
  lu — une réponse sans source ne peut donc pas se faire passer pour documentée ;
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
  rend quatre et que le panneau « Runs qui n'avancent plus » ne montre que les
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
  Chat, puis **Logs** depuis #266 (`/agents/<nom>/<onglet>`). Les trois pages qui regardaient le même objet
  par trois chemins ont fusionné : `/catalogue`, `/playbooks` et `/chat/<agent>`
  sont **redirigés vers le bon onglet** (`next.config.ts`, aucun signet ne casse)
  et `?onglet=` porte l'intention jusqu'à la liste quand l'URL d'origine ne
  nommait pas d'agent. `/chat` reste au menu pour le chat **global**, non lié à un
  agent — servi pour de bon depuis #269 (voir ci-dessous). Les onglets sont
  déclarés une seule fois (`lib/agents.ts`), comme le menu l'est dans
  `lib/navigation.ts` ;
- **Les intégrations d'un agent se règlent sur sa fiche** (#263, lot 11 de #243,
  [docs/21 §3.7](../../docs/21-configuration-mcp.md)) : l'onglet **MCP &
  permissions** (`components/OngletMcpAgent.tsx`, sorti d'`EditeurAgent` à ce
  lot) liste les intégrations **actives** de l'agent en tête, distinctes de
  celles du pool restées inactives ; il monte la **bibliothèque de
  `/integrations`** — la même, importée telle quelle : en recopier une version
  allégée rejouerait #231 — derrière un bouton, et ce qu'on y ajoute est
  **activé dans la foulée** ; et il donne son issue au bloc des déclarations
  **héritées**, qui disait « à migrer vers le pool » sans qu'aucun écran ne
  migre (`POST /api/mcp/migration/{agent}`, additif et sans un secret à
  ressaisir). ⚠ Le partage avec l'écran Intégrations est celui de la **portée du
  geste** : le *retrait du pool* désactive chez tous les agents et purge les
  secrets, donc il reste là-bas ; ici un interrupteur éteint **désactive pour cet
  agent seul**, et l'écran le dit à l'endroit où on l'éteint ;
- **Une seule porte d'entrée** (#484, lot 3 de #481, docs/05 §1) : « Composer un
  objectif » (#319) et « Valider le brief » (#322) **ont quitté le menu** le
  2026-08-28, et « Chat » a pris leur place en tête — le fil sait faire ce
  qu'elles faisaient (les sources depuis #482, le cadrage depuis #483), et deux
  portes vers un même geste sont la question « laquelle ? » posée à chaque
  lancement. Même mécanique qu'au-dessus : `/composer` et `/brief` sont
  **redirigés** vers `/chat` (307, `next.config.ts`), donc aucun signet ne casse.
  Le vrai coût du lot est ailleurs — **cinq** surfaces acheminaient vers ces deux
  écrans en résolvant leur destination par le **menu** (le poste vide, la liste
  de runs vide, la file de briefs vide, la cloche, la table `ATTENTES`) ; un
  libellé retiré rend `undefined`, donc `null`, donc un bloc qui disparaît sans
  un mot. Les trois du **cadrage** avaient été déplacées d'avance par #483
  (`PAGE_DU_CADRAGE`), les deux du **lancement** l'ont été ici (`PAGE_DU_FIL`,
  `lib/navigation.ts`) : retirer une entrée de menu n'est jamais un geste
  local ;
- **Éditeur de playbooks** (#77, EF-24/EF-25) : l'onglet **Playbook** d'une fiche
  agent porte son playbook versionné (#76, API `/api/playbooks`), publie une nouvelle
  version depuis un éditeur plein texte et montre l'historique, chaque version
  antérieure étant consultable et restaurable (le dépôt est append-only :
  restaurer republie, rien n'est réécrit). Une version publiée s'applique **à
  chaud** (#78, EF-26) : le moteur relit la version courante à chaque tâche —
  elle vaut pour l'exécution suivante sans redémarrage, et la version utilisée
  est tracée sur chaque résultat (`playbook_version`, journal compris) ;
- **Rédaction assistée du playbook** (#261, lot 9 de #243, onglet **Playbook**) :
  l'éditeur aide à écrire, à deux échelles et sans jamais publier. En cours de
  frappe, il propose des **complétions** — structures de section et tournures que
  les playbooks du dépôt ont en commun (`lib/completionsPlaybook`, servies par
  `GET /api/playbooks/lexique`) : `Tab` accepte, `Échap` ou la frappe suivante
  ignore, `↑`/`↓` choisissent. À la demande, un bouton **Assistant** fait réécrire
  le brouillon par le modèle (`POST /api/playbooks/{agent}/redaction`), avec une
  consigne libre facultative, et le rend en **différentiel** ligne à ligne
  (`lib/diff`) avant toute application. Quatre choses portent le lot. Les
  complétions sont **locales et déterministes**, jamais un appel modèle par
  frappe : une proposition qui arrive une seconde trop tard déplace le curseur de
  quelqu'un qui a déjà continué, et se facturerait au caractère tapé — le modèle
  intervient à l'autre bout, sur un geste explicite. Ce qu'elles proposent est
  **dérivé du dépôt et jamais recopié** (`maestro.agents.lexique_playbook` relit
  les documents livrés) : une constante côté front mentirait au premier playbook
  modifié, sans que rien ne le signale ; le seuil est la **récurrence** — présent
  dans au moins deux playbooks —, si bien qu'une singularité d'un rôle n'est pas
  diffusée aux autres. **Rien n'est publié** : accepter une complétion ou
  appliquer une réécriture ne touche que la zone d'édition, la version en vigueur
  ne bougeant que par « Publier » — et c'est pour cela que l'assistance ne passe
  **pas** par les propositions stockées de #111/#140, dont l'application *publie*
  une version. Enfin `Entrée` n'est jamais capturée par la liste de complétions :
  dans une zone de texte elle insère un saut de ligne, et la voler à quelqu'un qui
  rédige coûterait plus que l'aide n'apporte ;
- **Catalogue des agents** (#73, EF-03) : la liste `/agents` montre le catalogue
  effectif (#72, API `/api/catalogue`) — ceux du code, et les **personnalisés**
  qu'on y crée, puis modifie et supprime depuis l'onglet **Profil** de leur fiche
  (nom, rôle, compétences, fournisseur/modèle/effort). Un agent personnalisé est
  persisté hors du code et chargé par les moteurs construits ensuite. Depuis #487
  les champs
  **fournisseur** et **modèle** ne sont plus deux cases vides : ils proposent ce
  qui existe (API `/api/fournisseurs`), en distinguant deux colonnes qui ne se
  confondent jamais — *supporté par Maestro* vient du **registre du code**
  (`maestro/providers/registry.py`), *présent ici* de la **sonde du poste**
  (`maestro/poste.py`) : un CLI d'agent résolu sur le `PATH`, un serveur de
  modèles local qui répond (Ollama et ses modèles, #113), une clé de fournisseur
  dans l'environnement. Les deux colonnes voyagent sur **une seule ligne par
  fournisseur** et sur **une seule route** : la gamme annoncée et les niveaux
  d'effort admis (#253) y sont les champs `modeles`/`modeles_libres`, ce que la
  sonde a vu le champ `modeles_ici` — ouvrir une seconde route pour l'autre
  moitié recréerait la double source que ce dispositif existe pour éviter.
  Trois choses à ne pas défaire. La sonde est **gratuite et
  sans effet de bord** — elle n'exécute aucun binaire, ne joint que la boucle
  locale, n'écrit rien, et un poste nu rend une liste vide sans erreur. Le champ
  **modèle** reste en **saisie libre** (`<input list=…>` et non `<select>`)
  **tant que le fournisseur l'admet** (`modeles_libres`) : la sonde **suggère,
  elle ne restreint pas**, `OpenAICompatProvider.supports` acceptant tout nom non
  vide — un `<select>` rendrait insaisissable ce que le catalogue ignore. ⚠ Cette
  liberté-là n'a **jamais valu pour le fournisseur**, et #255 l'a tranché : le
  registre est **exhaustif**, un nom qui n'y figure pas ne s'exécute pas, si bien
  que la saisie libre n'y offrait que la faute de frappe (voir le paragraphe
  suivant). Et un outil trouvé ici que Maestro ne sait pas piloter est
  **montré sans être proposé**
  ([docs/34](../../docs/34-decision-agent-cli-tiers-acp.md)) : le taire ferait
  croire qu'il n'est pas là, le proposer serait le seul vrai mensonge de cet
  écran. Ce que la sonde ne peut pas savoir est écrit **sous les champs** et
  rattaché à eux (`aria-describedby`) plutôt que deviné — la validité d'une clé,
  la version d'un binaire, et le fait que le `PATH` du process qui sert l'API
  n'est pas celui de votre terminal, si bien qu'une **absence n'est pas un
  constat** ;
- **Le formulaire d'agent en listes liées** (#255, lot 3 de #243) : quatre champs
  qui étaient quatre chaînes indépendantes deviennent une **chaîne de
  dépendances**, pour qu'on ne puisse plus composer une configuration qui
  n'existe pas. Le **rôle** se choisit dans une liste *alimentée* par les rôles
  des agents du catalogue (`/api/catalogue`, seule source — les rôles ne sont
  déclarés nulle part ailleurs), la **saisie libre restant possible** pour un
  rôle inédit : c'est une `<datalist>`, jamais un menu fermé. Le **fournisseur**
  vient **avant** le modèle et devient un `<select>` alimenté par le registre,
  augmenté de l'option explicite « **défaut de l'exécution** » — un agent sans
  fournisseur ni modèle propre suit `MAESTRO_PROVIDER`/`MAESTRO_MODEL`, et c'est
  un **défaut légitime** qu'il fallait offrir plutôt que laisser deviner. Le
  **modèle** n'offre alors que **les siens** — la gamme annoncée du fournisseur
  choisi, plus ce que la sonde a vu **pour lui** —, et sa forme suit le contrat :
  `<select>` fermé si `modeles_libres` est faux, champ libre sinon. L'**effort**
  n'apparaît que si le modèle en **admet**, sur sa valeur par défaut (« défaut du
  fournisseur », c'est-à-dire `effort: null`), et disparaît sinon. Quatre choses à
  ne pas défaire. Changer de fournisseur **invalide visiblement** un modèle
  devenu impossible — vidé *et* annoncé dans une région `role="status"` : le
  laisser en place était le défaut à corriger, le vider en silence en serait un
  autre. Une valeur stockée que le registre ne connaît plus reste
  **représentable** (option « inconnu du registre ») : sans elle, ouvrir une
  fiche réécrirait sa définition au premier enregistrement — une perte de données
  déguisée en menu. Rien n'est jugé **tant que le catalogue n'est pas arrivé**
  (ni modèle vidé, ni effort retiré), faute de quoi le premier rendu d'une fiche
  effacerait ses réglages avant toute question. Et le front **ne valide pas
  l'effort à l'écriture** : il reprend `ModelProvider.efforts_admis` — un modèle
  **hors gamme** n'annonce rien, donc pas de sélecteur — pendant que l'exécution
  reste seule à trancher (`effort_admis`), un catalogue qui bouge ne devant pas
  invalider une définition écrite hier ;
- **Génération assistée d'une définition** (#257, lot 5 de #243, écran
  `/agents/nouveau`) : une **intention en une phrase** et un bouton « Générer »
  proposent la définition complète — rôle, compétences, playbook, et
  fournisseur/modèle suggérés (API `POST /api/catalogue/generation`). Trois
  propriétés portent le lot. **Rien n'est enregistré** : la proposition remplit
  les champs du formulaire ci-dessus, comme une saisie, et l'agent naît du
  `POST /api/catalogue` ordinaire — c'est le principe des propositions de
  playbook (#111/#140), une suggestion n'est pas une version. Elle est donc
  **modifiable mot à mot**, **régénérable**, et **abandonnable** — abandonner rend
  au formulaire ce qu'il portait avant la proposition, l'intention restant en
  place. Le fournisseur et le modèle proposés sont **reconfrontés au registre**
  côté backend avant de revenir : un nom que Maestro ne saurait pas résoudre est
  écarté et le champ retombe sur « défaut de l'exécution », jamais rempli d'un
  nom plausible. C'est la règle du tiret précédent — le registre est exhaustif —
  tenue une seconde fois, là où c'est un **modèle** qui écrit : sans elle, la
  chaîne de listes liées de #255 serait contournée par la seule entrée qui ne
  passe pas par elle. Et un **échec** (quota, réseau, fournisseur muet, réponse
  hors contrat) laisse le formulaire **intact** et le dit : l'écriture des champs
  n'a lieu qu'après une réponse complète ;
- **Un playbook s'écrit à un seul endroit, et un agent du code se règle sans
  être cloné** (#259, lot 7 de #243) — deux relevés de revue sur l'onglet Profil,
  et une même racine : *la même valeur à deux endroits*.

  **Le champ Playbook quitte le Profil.** Il y vivait alors que l'onglet Playbook
  existe depuis #190 : deux chemins d'écriture pour la même valeur, dont un
  aveugle au versionnement et à l'historique — on pouvait écraser une version
  publiée sans jamais voir qu'elle existait. Il ne subsiste qu'**à la création**,
  où l'agent n'a pas encore d'onglet où aller ; partout ailleurs un **renvoi**
  vers l'onglet Playbook prend sa place. Retirer le champ sans dire où sa valeur
  s'écrit désormais aurait supprimé le doublon *et* le chemin. ⚠ Cela imposait
  d'abord que l'onglet **existe pour tout le monde** : `/api/playbooks` ne
  connaissait que les cinq rôles du code (`PLAYBOOK_DEFAUTS`) et **404-ait sur un
  agent personnalisé**, dont le champ du Profil était donc irremplaçable. Ce
  n'était pas une extension du moteur mais son rattrapage :
  `LocalExecutor._playbook_courant` lit `PlaybookStore.lire(agent)` sans regarder
  d'où vient l'agent — une version publiée pour un agent personnalisé
  s'appliquait **déjà**, elle n'était simplement pas publiable. Le playbook de sa
  définition (#72) joue désormais le rôle que le document Markdown joue pour un
  rôle du code : l'**origine**, celle qui vaut tant que rien n'a été publié, et
  que `source: "defaut" | "stockage"` distingue. Le Profil continue de renvoyer
  ce playbook tel quel dans son `PUT` — la définition est remplacée en entier, ne
  pas le renvoyer l'effacerait — mais il ne l'édite plus.

  **Un agent du code accepte une surcharge.** Sa fiche était entièrement en
  lecture seule, si bien que changer son modèle — un besoin courant — n'avait
  qu'un contournement : le **dupliquer** en agent personnalisé, c'est-à-dire
  recopier son playbook pour ne toucher qu'un réglage, après quoi les deux
  exemplaires divergent en silence et la copie cesse de suivre le code. D'où le
  **troisième état** du catalogue, « du code, **surchargé** »
  (`AGENT_SOURCE_SURCHARGE`, API `PUT`/`DELETE /api/catalogue/{nom}/reglages`,
  dépôt `core/surcharges/`) : l'identité reste au code — rôle, compétences,
  playbook en suivent les évolutions —, seuls les trois réglages de modèle se
  posent. Cinq choses à ne pas défaire. Ce qui n'est **pas** surchargé est
  **marqué « hérité du code »** avec la valeur que le code lui donne (`herite`,
  `reglages_du_code`), et c'est le **serveur** qui tranche : une valeur affichée
  peut venir du code *ou* avoir été surchargée à l'identique, et la recalculer à
  l'écran rendrait les deux indiscernables. Une surcharge **s'annule, elle ne
  supprime pas** — l'agent reste au catalogue —, et la **suppression demeure
  réservée aux personnalisés** : `DELETE /api/catalogue/{nom}` refuse un agent du
  code en 403, `DELETE …/reglages` refuse un personnalisé pour la raison
  symétrique (sa définition *est* son réglage, un second chemin d'écriture serait
  le doublon qu'on vient de supprimer côté playbook). Le corps du `PUT` est
  l'**intégrale et pas un diff** : un réglage absent retourne au code, si bien
  que tout vider revient à annuler — et le dépôt ne stocke jamais une surcharge
  vide, faute de quoi « surchargé avec rien » existerait à côté de « du code »,
  deux états indiscernables dont l'un afficherait pourtant l'agent comme modifié.
  Les trois `<select>` sont **ceux de #255** (`ChampsDuModele`, extrait plutôt que
  recopié) : la chaîne fournisseur → modèle → effort, son invalidation et son
  résumé du poste valent ici sans une ligne de plus, et deux chaînes à tenir
  d'accord auraient défait ce que #255 venait d'unifier. Enfin `MAESTRO_MODEL`
  **prime** sur une surcharge de modèle, comme il prime sur celui d'un agent
  personnalisé — c'est une bascule globale — mais ne touche pas à l'effort ;
- **Les permissions d'un agent s'éditent** (#262, lot 10 de #243, onglet **MCP &
  permissions**) : la politique allow/ask/deny que le moteur applique à
  l'exécution se règle depuis la fiche (`PUT /api/permissions/<agent>`, source
  `core/permissions/<agent>.json`), là où il fallait éditer le fichier à la main
  puis relancer. `allow` et `deny` sont deux `ChampJetons` (#256) — la brique
  était là, il n'y avait qu'à s'en servir — nourris par les outils **réellement
  exposés** à cet agent, servis avec la fiche (`permissions_outils` : ceux de son
  profil, les verbes du serveur `maestro`, ses serveurs MCP montés). Cinq choses
  portent le lot. Chaque geste **écrit**, sans bouton « Enregistrer » et comme
  les interrupteurs MCP juste au-dessus, l'état local ne bougeant qu'**après**
  l'accord de l'API : une entrée refusée s'efface d'elle-même en laissant à
  l'écran le motif du dépôt, qui **nomme la liste et l'entrée** — un « politique
  refusée » de notre cru n'apprendrait rien. On **suggère sans restreindre**
  (règle de #256 et des champs de #487) : un outil MCP précis se désigne à la
  frappe, et ce que rien d'exposé n'explique est *signalé* — jamais refusé, un
  serveur désactivé depuis et une faute de frappe se ressemblant ici. La règle de
  portée qui décide de ce signalement vit dans `lib/permissions.ts` et non dans
  le JSX : un préfixe qui ne vaut qu'aux frontières `__` ne se voit ni au lint,
  ni au typage, ni à l'écran (`mcp__slack` ne dit rien de `mcp__slackbot`), et
  c'est le pendant exact de `_correspond` côté moteur. Une politique **invalide**
  reste diagnostiquée comme avant — et se **corrige d'ici** : elle n'est
  appliquée à rien tant qu'elle est illisible, l'écriture ne relit pas ce qu'elle
  remplace, donc « Repartir d'une politique vide » débloque l'écran là où un
  aller-retour échouerait sur le fichier même qu'on répare. Enfin `ask`
  s'**affiche mais ne s'édite pas** : une entrée arbitrée porte **qui la tranche**
  (#586), un cran qui se pose à froid — l'ajouter à moitié la ferait retomber en
  silence sur le défaut, qui est le plus fermé des deux. ⚠ Au passage, la section
  **rendait `ask` comme une liste** (`entrees.length`, `entrees.map`) alors que
  `PolitiqueOutils.to_dict` l'émet en **objet** depuis #586 — donc une
  `TypeError` au rendu dès qu'un agent avait une politique, `ask` vide comprise,
  puisque l'objet est toujours servi. Le type le disait `string[]`, ce qui l'a
  rendu invisible au typage ; il dit désormais `Record<string, string>` ;
- **Chat par agent** (#85, lot 2 de #82) : l'onglet **Chat** d'une fiche agent
  ouvre le fil de conversation avec lui (#84, API `/api/chat`) — envoi,
  réponse de l'agent (cadrée par son playbook courant) et réception en temps
  réel par le WebSocket (`chat.message`). Le fil est persisté côté backend :
  l'historique se recharge au retour sur l'onglet ;
- **Logs par agent** (#266, lot 14 de #243) : l'onglet **Logs** d'une fiche agent
  montre ce qu'il fait et ce qu'il a fait — le direct **et** l'historique
  persisté, **groupés par tâche**, la tâche la plus récemment active en tête et un
  groupe « Hors tâche » pour ce qui n'en relève pas (planification, capacité).
  Jusque-là l'activité d'un agent ne se lisait que dans le fil global du tableau
  de bord, tous agents confondus, et disparaissait au rechargement. Trois choses à
  connaître. **Le filtre par agent est servi par l'API** (`GET
  /api/journal?agent=…`, filtre déjà au contrat #183) et jamais appliqué après
  coup : une page de journal est plafonnée à 200 entrées, donc refiltrer une page
  du projet entier ne montrerait d'un agent discret que le silence des autres —
  même raisonnement qu'en #478 pour le `run_id`. **La ligne n'est pas réécrite** :
  `FilActivite` rend ici ce qu'il rend au tableau de bord, sur `/journal` et dans
  la vue d'un run, donc les résumés lisibles de #250 et le dépli qui rend les
  identifiants ; il gagne seulement un `niveau`, pour être une sous-partie (`h3`)
  sous le titre commun. **Le « niveau » est la famille d'une ligne, pas une
  sévérité de plus** (`lib/evenements`, `NIVEAUX_LOG`) : *erreur*, *refus*,
  *décision*, *info* — c'est-à-dire exactement les quatre choses que le ticket
  demande de couvrir, si bien que « qu'est-ce qu'on lui a refusé ? » s'isole d'un
  choix. Une échelle « erreur / avertissement / info » aurait été le réflexe et ne
  permettait justement pas cette question-là ; la sévérité ne sert plus qu'à
  **ordonner** la liste, dérivée du fil comme toutes les autres (#249 : aucune
  option morte). Le **renvoi vers la tâche** mène à son run (`hrefRun`, éteint
  tant que la page n'existe pas) : il n'y a pas de route par tâche dans la Control
  Tower, une tâche s'ouvre en panneau dans la vue de son run ;
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
  ⚠ **L'orchestration est retirée du parc, pas ajoutée à côté** (#671,
  `lib/orchestration.destinatairesDuFil`) : `GET /api/agents` la **contient**, la
  projection rendant les acteurs vus au journal — la réserve de `NOMS_RESERVES`
  interdit qu'un agent *personnalisé* prenne ce nom, elle ne promet pas que le
  parc n'en porte aucun. La mettre en tête puis concaténer le parc entier donnait
  deux entrées pour un seul fil, sous la même clé React ; invisible en `--demo` et
  en test, dont les parcs n'ont jamais porté l'orchestrateur, donc **le parc monté
  par les tests prend désormais la forme du mode réel**.
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

Les cinq `IconeRole*` (#258) sont le seul groupe **choisi par une donnée** : la
liste des agents pose sur chaque carte l'icône du **rôle** plutôt que celle de
l'agent, qui répétait d'une carte à l'autre la seule chose qu'elles ont en
commun. La table qui les associe vit dans `lib/vueAgents.ts` et elle est
**fermée** — les cinq libellés de `maestro/agents/catalog.py`, et rien d'autre.
Le rôle d'un agent personnalisé est du texte libre : en déduire une icône
reviendrait à juger du texte au lexique, ce que ce dépôt s'interdit (#746), et
une icône fausse est pire qu'une générique — elle affirme. L'inconnu retombe donc
sur `IconeAgent`, qui reste vraie.

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

Deux autres vivent **à côté**, chacune dans son fichier — elles appellent des
hooks (`useId`, `useState`), et `Primitives.tsx` est partagé avec des composants
serveur, où aucun hook ne peut tourner (la raison qui en écarte aussi
`Infobulle`) :

| Brique | Ce qu'elle porte |
| --- | --- |
| `BasculeDeVues` (#539) | plusieurs lectures d'un même bloc, une à la fois |
| `ChampJetons` (#256) | une valeur qui est une **liste de mots** : jetons retirables, vocabulaire proposé, mot inconnu signalé |

`ChampJetons` complète la famille des champs, et sa différence avec eux est le
sujet du ticket qui l'a fait naître : il porte un **avertissement** en plus de
l'aide — annoncé avec le champ comme l'est une erreur, mais **sans**
`aria-invalid`, parce que la valeur passe. Elle est seulement inhabituelle, et
poser `aria-invalid` sur ce qu'on accepte annoncerait un refus qui n'arrivera
pas. Ses jetons vivent **hors du `<label>`** : dedans, leur texte entrerait dans
le nom accessible du contrôle (« Compétences react retirer css retirer »).

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

**Et la troisième réponse — « une ligne avec un renvoi » — a son exemple depuis
#272** : le panneau des validations. Il empilait **toute** la file sur le tableau
de bord, ce qui refaisait l'écran Validations à l'intérieur d'un aperçu ; il rend
désormais la demande **la plus ancienne** — entière et décidable sur place, c'est
elle qui retient un moteur depuis le plus longtemps — puis une ligne « N autres
demandes attendent leur tour » et un renvoi vers la page. Le prix est assumé et
se dit : depuis le tableau de bord on ne tranche plus que la plus urgente. Ce qui
ne change **pas** d'une surface à l'autre est la **carte** — `CarteValidation`,
montée par `PanneauValidations` (aperçu) comme par `FileValidations` (plein
format), mêmes champs dans le même ordre : ce qu'on lit pour trancher ne doit pas
dépendre de l'écran d'où l'on vient, et c'était le cas quand trois rendus
divergents décrivaient la même demande (le panneau, la page, la cloche).

**Ce qui la garde** : `tests/sobriete.test.tsx`, qui monte les écrans du menu
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
| `axe-core` sur les **écrans du menu** | `tests/a11y.test.tsx` | toute violation `serious`/`critical` sur un écran monté dans son shell réel |
| `motion-reduce:` | balayage des sources, même fichier | une utilité `transition`/`animate-` écrite **sans sa garde** dans la même chaîne de classes |
| `CIBLE_MINIMALE` | rendu des écrans du menu, même fichier | un lien ou bouton **en petit corps** sans plancher de 24 px |

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
comme une faute du produit, sur tous les écrans à la fois. Elles ne disparaissent
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

Le niveau visé est **WCAG 2.2 niveau AA sur les écrans du menu**. Deux exemptions
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
| `tests/navigation.test.tsx` | Le menu unique, la sidebar, la barre supérieure (#117) ; une entrée par intention et les renvois par libellé (#189) ; la **porte unique** (#484, **logique critique du lot seule**, le reste différé à #485) — les deux entrées parties, les deux chemins encore servis en 307, aucune entrée de menu parmi les sources de redirection, et le libellé du fil qui **résout** (un `undefined` y éteindrait cinq renvois sans un mot) |
| `tests/theme.test.tsx` | Choix clair/sombre/système, script d'init, accord des deux contrôles (#118) |
| `tests/notifications.test.tsx` | Tri du notable, badge, décision depuis le panneau (#119) |
| `tests/identite.test.tsx` | Le monogramme et ses déclinaisons favicon/ICO/PNG (#120) |
| `tests/parametres.test.tsx` | Sommaire, ancres, préférences du poste (#121) |
| `tests/guide.test.tsx` | Déclenchement unique, étapes, sortie clavier, ancres et pages réelles (#122, #193) |
| `tests/assistant.test.tsx` | Ouverture, envoi, échec d'envoi, non-fermeture au clic extérieur (#123) |
| `tests/shell.test.tsx` | La composition : les sept lots effectivement branchés dans le cadre |
| `tests/agents.test.tsx` | La fiche agent à onglets, la liste, et la survie des chemins v1 par redirection (#190, testé en #193) |
| `tests/agent-mcp.test.tsx` | L'onglet **MCP & permissions** (#263) : les deux groupes séparés (actives en tête), la phrase qui dit qu'éteindre un interrupteur **ne retire pas du pool**, la migration des déclarations héritées, et l'ajout depuis la fiche **qui active dans la foulée**. La couverture complète revient au lot 15 de #243 — ce fichier est là parce qu'**aucun test ne montait cet écran**, ni celui-ci ni ses ancêtres dans `EditeurAgent`, alors qu'il écrit dans le pool projet. Il a déjà payé : le compte rendu de migration vivait dans le bloc des héritées, c'est-à-dire **dans ce que la migration supprime** — on cliquait, tout s'évanouissait sans un mot |
| `tests/tableau-de-bord.test.tsx` | Le tableau de bord épuré — ce qui reste, ce qui renvoie ailleurs — et le ticket externe dans les tables de coûts (#191/#192, testés en #193) ; puis le **second niveau de `/couts`** (#539) : la vue par tâche à l'ouverture, la bascule vers la vue par exécution **sans quitter le bloc** (c'est un second niveau, pas une navigation), la répartition par agent rangée dans la colonne de propriétés, et le bloc qui s'efface quand la période n'a ni tâche ni exécution — les chiffres, eux, restent |
| `tests/ticket-externe.test.tsx` | Le filtrage d'URL et les cartes du Kanban (#192, livré avec le lot : logique critique) |
| `tests/detail-tache.test.tsx` | Le panneau de détail d'une tâche : description, étapes en checklist, liens filtrés et rendus selon leur nature, et la carte laissée intacte quand il n'y a rien à ouvrir (#251, livré avec le lot : filtrage d'URL et absence totale) |
| `tests/integrations-bibliotheque.test.tsx` | La bibliothèque MCP face au gestionnaire de mots de passe du navigateur : cloisonnement des champs secrets et panneau oublié quand son entrée quitte les résultats (#231), puis la bibliothèque élargie — provenance, éditeur, pistes d'une recherche infructueuse (#271). Le fichier a suivi son sujet en #270 (`parametres-mcp.test.tsx` avant lui) — les scénarios de #231 y sont inchangés, et c'est le but : un déménagement qui aurait « rangé » la structure au passage rejouerait le bug |
| `tests/integrations.test.tsx` | L'écran **Intégrations** (#270) à son plus mince — l'entrée de menu à sa place, les blocs montés **peuplés**, « qui utilise quoi » et son lien vers la fiche, l'ancre `/parametres#mcp` rattrapée en `replace` et les autres ancres laissées tranquilles. Il garde surtout le **drain de `monterEcran`** : sans lui, tout écran chargeant en différé était audité sur son « Chargement… », donc `a11y` et `sobriete` restaient vertes **et muettes**. Le reste du comportement de l'écran revient au lot 6 (#273) |
| `tests/projets.test.tsx` | L'écran Projets : racine choisie dans l'explorateur servi par l'API (jamais saisie), refus motivé qui ne casse ni la liste ni la navigation, dossier vide distinct d'un refus (#225) |
| `tests/journal.test.tsx` | La page Journal : fil sans limite, filtres par type/agent/tâche, recherche jusque dans le détail, « notable seulement » aligné sur la cloche (#249) |
| `tests/activite.test.tsx` | Les lignes d'activité : repli des rafales, horodatage relatif, détail brut à un clic, garde des types inconnus (#250) |
| `tests/socle-visuel.test.tsx` | Le langage visuel (#245) : le jeu d'icônes (SVG à `currentColor`, toutes décoratives), les primitives et leurs deux thèmes, et **aucun émoji rendu** sur les écrans de la vague |
| `tests/kanban.test.tsx` | La section Tâches qui prend la place (#248) : colonnes de la machine à états, colonne « Autres », **chaîne d'étirement entière** et défilement rendu à chaque colonne |
| `tests/format.test.ts` | Les montants à deux décimales et leurs trois verdicts — « — », « 0,00 $US », « < 0,01 $US » —, l'exception des graduations d'axe, durées et tokens (#247) |
| `tests/projet-actif.test.tsx` | La porte d'entrée : aucun écran n'est atteint sans projet actif, le choix retenu est confronté à l'état réel, et la page demandée revient sans redirection (#279) |
| `tests/selecteur-projet.test.tsx` | Le sélecteur du shell : bascule sans quitter la page, gestion atteinte sans chemin en dur, et « Projets » sorti de la sidebar sans que son écran cesse d'être servi ni titré (#280) |
| `tests/composer.test.tsx` | Composer un objectif : dossier pris dans l'explorateur (jamais saisi), aperçu gratuit qui ne lance rien et se périme dès qu'une source change, refus posé **sur la source qu'il vise** sans perdre la saisie, et « ignoré » qui n'est pas un refus (#319) |
| `tests/fil-sources.test.tsx` | Le fil qui accepte des sources (#482, **complété par #485**) : un fichier glissé sur la conversation part par son **identifiant de téléversement** et jamais par ses octets ni son nom — c'est ce qui garantit qu'il n'atterrit pas dans le dossier de l'utilisateur, et rien à l'écran ne le dirait s'il cessait d'être vrai ; un message fait de **sources seules** est légitime ; un **refus reste dans le fil**, sur la source qu'il vise, sans perdre ni le texte ni la matière ; le **rapport de lecture** est replié sous le message qui l'a porté et se déplie sur place, l'image y ressortant « Ignoré / `format-non-gere` » au lieu de disparaître ; et une bulle sans source reste **strictement** celle d'avant le lot. #485 y ajoute les **deux autres types** que le titre du lot nomme — un dossier pris dans l'explorateur (jamais saisi) et une adresse, ni l'un ni l'autre ne passant par le téléversement —, le **cycle de la composition** (retirer avant l'envoi sans décaler les identifiants, vidée par un succès, conservée par un échec) et le refus **sans index**, qui n'a pas de ligne où se poser et se rend une seule fois sous la saisie |
| `tests/integrations-pool.test.tsx` | Le **pool projet** de l'écran Intégrations (#270, testé en #273 — la part que son propre lot avait différée ici) : le renversement du catalogue (`usageDuPool`, rangé *par agent* côté API), les quatre modes d'auth, les quatre états du bloc, le retrait et son échec. Ce qui s'y joue vraiment est la **troisième** réponse de « qui l'utilise » : un catalogue muet ne s'écrit **jamais** « aucun agent » — le rendre ainsi ferait retirer une intégration en croyant qu'elle ne sert à rien, c'est-à-dire se tromper sur la question même que l'écran pose |
| `tests/chat-global.test.tsx` | Le **chat global** (#268/#269, testé en #273) : `mentionEnTete` et ses quatre décisions, toutes du même ordre — ne rien faire dans le doute, une mention mal reconnue détournant un message vers le mauvais fil ; puis l'écran, où ce qui est observé est **le canal demandé** à `useChat` (`canauxDemandes`, même dessin que `porteesDemandees` de #281) et non le texte rendu, seule façon de prouver que `@dev` **change de destinataire au lieu de recopier** — le raccourci inverse donnerait deux historiques d'une même conversation, désaccordés dès le premier rechargement, sans que rien à l'écran ne le montre ; enfin « Ouvert depuis ce fil », qui **lit** les `run_id` des messages et ne déduit jamais un run de ce qui a tourné pendant qu'on regardait. ⚠ Depuis #671 son parc porte **l'orchestrateur**, la forme que sert le mode réel (`GET /api/agents` rend les acteurs du journal) : le parc d'avant était celui de `--demo`, si bien que l'écran n'était jamais éprouvé sur ses vraies données — `destinatairesDuFil` est couvert à part, et deux tests d'écran nomment le doublon, dont un qui ne juge que lui pour qu'il survive à une réécriture de l'autre. ⚠ Depuis #683 il garde aussi le **projet qui part avec le message** (`projetsDuFil`) et ce que l'appel REST porte (`diffuserMessageChat`, `fetch` bouchonné) : sans ce rattachement, le run que l'orchestration ouvre ne relève d'aucun projet, donc n'entre dans la liste d'aucun et refuse de s'ouvrir en détail — pendant que le fil l'annonce en cours. Un test dit aussi ce que l'appel **ne** porte pas sans projet : pas de `projet_id: null`, l'appel d'avant le lot à l'octet près — et **par où il passe** (#695) : le flux, seule façon dont le navigateur parle à un fil depuis que `useChat` le consomme. ⚠ Depuis #695 il porte enfin le **direct à l'écran** : la bulle qui se remplit, « … répond… » réduit à l'avant-premier-mot, la réponse figée qui se dit incomplète, et l'arrêt offert **à la place** de l'envoi |
| `tests/chat-direct.test.tsx` | La **couture flux → fil** (#695) — le seul fichier à jouer le **vrai** `useChat`, `tests/setup.ts` le remplaçant partout ailleurs par un fil immobile (`vi.unmock`) : un double est exactement ce qu'il faut pour juger un écran, et exactement ce qui empêche de juger le hook. Trois invariants, tous silencieux quand ils cassent — la réponse s'écrit **et ne se dédouble pas** (la même paire arrive par le flux puis par le fil que le `chat.message` du WebSocket fait recharger, et la fusion écarte le doublon) ; un flux **cassé** ne perd ni le message utilisateur ni la portion reçue, et le lève en `ErreurReponse`, ce qui dit à l'écran de **ne pas** remettre le brouillon dans la saisie ; la réponse **figée** s'efface dès qu'une vraie réponse au même message rejoint le fil — sans quoi la garantie précédente tomberait précisément dans le cas où le backend achève sa production malgré la coupure (#268). Le reste de la couverture du chat global pleine page a été soldé au lot 8, ci-dessous |
| `tests/fil-lisible.test.tsx` | **Le fil se lit** (#697) — la seule exception que la règle des lots prévoit pour la logique critique : un analyseur Markdown écrit à la main qui traite du texte produit par un **modèle**. Deux propriétés qui ne se rattrapent pas après coup : rien de ce qu'un modèle écrit ne devient du **balisage** (`lib/markdown` rend un arbre de données, jamais une chaîne de HTML — il n'y a donc rien à assainir et aucun `dangerouslySetInnerHTML` à écrire ; un lien `javascript:` est refusé et laissé lisible ; un titre de message ne rejoint jamais le plan du document), et les **écarts à CommonMark sont des décisions** et non des trous — `_` n'emphase pas (`run_id` traverse chaque réponse), une emphase ne franchit pas la fin de ligne, les listes sont plates. Plus `lib/journees` : deux instants du même jour local sous la même journée, un horodatage illisible qui n'en ouvre aucune, « Aujourd'hui »/« Hier » seulement quand l'horloge a démarré |
| `tests/chat-pleine-page.test.tsx` | **Ce que le chantier a retiré** (#690, lot 8 #698) — la moitié navigateur, et la plus difficile à garder : rien à l'écran ne nomme une absence, si bien que le test ne peut qu'affirmer qu'elle est là. Quatre sujets : le fil **sans ascenseur à lui** (aucun `overflow-y`/`max-h` ni sur le `<ol>` ni au-dessus, `flex-1` présent, composeur `sticky`) ; l'**état nominal qui ne se dit plus**, « ni une fois ni deux » — seule la coupure reste dite ; les **conversations à l'écran** (#696 : l'ordre servi jamais retrié ici, `aria-current` sur celle qu'on lit et elle seule, le nom d'un fil vierge, les deux gestes) ; et le **fil qui n'exécute rien** (#697 vu du fil et non du module — que la bulle d'agent, la réponse **en cours** et le message de l'utilisateur passent tous par le bon rendu ; un `dangerouslySetInnerHTML` réintroduit dans une bulle ne ferait rougir aucun test du module). ⚠ Chaque sonde **prouve son motif sur un échantillon fautif** avant de conclure (méthode de #534/#537/#539) : la boîte de `60vh` d'avant #691 y est reconnue, le badge y est vu quand il est affiché, un fragment actif y est repéré. ⚠ **Aucune géométrie** (#308) : ce qui s'observe est le contrat de mise en page *tel qu'il est écrit*, jamais son effet — l'effet est le rôle de `/banc-mise-en-page` |
| `tests/validations.test.tsx` | L'écran qui **se décide vite** (#272, testé en #273) : l'ordre de la file (la plus ancienne d'abord, une demande sans horodatage en queue — elle n'a pas d'âge à faire valoir), `formatAttente` et ses paliers (« depuis » et non « il y a »), ce qu'on lit avant de trancher (l'**acte** en tête quand il y en a un, #581), et les gestes — approuver, refuser sec, refuser motivé. Deux garanties qui ne se voient pas à la relecture du composant : le motif **refermé est effacé** (« sans motif » doit vouloir dire sans motif, sinon un texte que plus personne n'a sous les yeux part au journal du run), et la **clé par `tache_id`**, prouvée en retirant la tête de file pendant qu'un motif est en cours de frappe — sans elle il s'attacherait à la demande suivante |
| `tests/brief.test.tsx` | Valider le brief, **logique critique du lot seule** (#322, le reste différé à #323) : approuvé **corrigé** vs approuvé **tel quel** (`brief: null`, qui fait retenir au moteur sa propre proposition), refus qui n'emporte jamais de brief, réponses appariées **par position** aux questions (chaînes vides comprises), et le coût engagé rendu face à la décision |
| `tests/fil-cadrage.test.tsx` | Le cadrage décidé **dans le fil** (#483 ; ce que #485 y ajoute est **côté moteur**, `tests/test_brief.py` ⑦ — D5 mesurée pendant l'attente et le bus refermé qui fait échouer le run, deux garanties qu'aucun écran ne montre) : le **canal reste le canal** — le fil rappelle `trancherBrief`/`repondreAuBrief`, donc les deux routes de #320/#321, avec le contrat entier (`brief: null` tel quel, brief corrigé sinon, jamais de brief sur un refus, une réponse par question) ; le **rang du tour et son plafond** restent en clair ; les tours joués se **déroulent** au lieu de se replier, le sans-réponse nommé ; et surtout le critère 3, seul dont l'échec est **invisible depuis l'écran qu'on regarde** — les trois surfaces qui montrent un run suspendu résolvent leur destination par le menu, donc un renvoi resté sur « Valider le brief » s'éteindrait sans un mot le jour où #484 retire l'entrée |
| `tests/runs-immobiles.test.tsx` | Les runs que **plus rien ne fait avancer**, et leurs **deux familles** (#349/#351, étendu par #738/#739 — le fichier s'appelait `runs-perdus.test.tsx` jusqu'au changement de nom du composant). Côté **hôte muet** : **la règle avant le panneau** (`lib/execution.ts`), qui n'est proposé que sur un `orphelin` **au brief approuvé** — l'API accepte pourtant de relancer un `indetermine`, et cet écart entre *accepter* et *proposer* est le sujet ; puis le panneau, absent quand rien n'est récupérable, désarmé pendant la reprise (un double clic partirait deux fois) et rendant le refus de l'API tel quel. Côté **personne n'a répondu** : le verdict `en_souffrance` est **lu**, jamais recalculé ici (un run sans le champ n'est pas signalé — le seuil et ses écarts vivent dans `souffrance.py`), le tri écarte l'orphelin et le run en pause, aucun oui/non n'est proposé mais un renvoi vers le run, et la carte **nomme ce que le run attend** — les trois attentes tirées de la table `ATTENTES`, confrontées à `causeDAttente` pour qu'une quatrième ne tombe pas en silence sur le repli. Deux détails que la relecture du composant ne montre pas : l'ancienneté est dite **à l'oreille** (`sr-only`, le chrono étant un glyphe) et disparaît entière quand le backend n'en donne pas — plutôt qu'un repli inventé sur une carte qui existe pour ne plus rien affirmer de faux —, et le compte **par famille** ne paraît qu'en face de l'autre |
| `tests/runs-liste.test.tsx` | La liste des runs (#474, testée en #480) : **le régime avant l'écran** (`regimeDuRun`), dont l'ordre de décision *est* la décision — soldé, puis interrompu, puis en pause, puis suspendu ; la `CarteRun` que **trois** écrans rendent (badge, avancement, cause d'arrêt #479, ligne de pause #477, ordres de pause et leur refus) ; **l'interruption** (#467) — `peutEtreInterrompu` sur les quatre états en vol et les trois issues, sa **divergence assumée** avec `peutEtreSuspendu` sur l'orphelin (la pause l'écarte, l'annulation non : l'API borne son attente et solde le run de toute façon), le premier clic qui n'envoie rien, la phrase de perte qui ne paraît qu'armée, le refus affiché **et** désarmé, et la rangée `GestesRun` dans ses quatre configurations ; puis l'écran dans ses quatre états, dont « vide » et « injoignable », qui ne se confondent pas |
| `tests/runs-vue.test.tsx` | La vue d'un run (#475/#478, testée en #480) : les tâches lues **avec `?run=`** et non filtrées sur `Tache.run_id` — le champ porte le *dernier* run qui les a touchées, une relance volerait celles du run repris —, la relecture au **pouls** du shell sans seconde WebSocket, les trois vides (autre projet, arrêt sur brief, API muette) et le journal persisté fusionné au direct sans doublon — atteint **par son onglet** depuis #516, avec le contrôle qu'il ne s'affiche ni sous le pipeline ni sous le Kanban |
| `tests/pipeline.test.tsx` | La vue pipeline d'un run (#491, testée en #492) en **trois étages**, parce qu'ils ne se gardent pas de la même façon : les règles hors JSX (`lib/graphe` — le backend sert tout ce qui se dessine, ce module ne porte que les trois questions qu'il ne pose pas, et l'**ordre** dans lequel elles sont posées *est* la décision ; `lib/vuesRun` — le pipeline ouvre) ; la checklist rendue (`components/EtapesTache` — **une case par étape**, le contrôle qui compte étant le dénominateur qui grandit sans que le numérateur bouge) ; puis la vue montée : le nœud en cours, l'étape qui se coche au battement suivant, l'arête qui s'allume, et l'attente humaine qui ne se lit plus « en cours » |
| `tests/frise.test.tsx` | La **frise d'activité** d'un run (#355) : les deux flux — statuts de tâche et messages inter-agents — sur une même chronologie, et les trois états que le ticket demande de distinguer **à l'œil** (en cours, attente humaine, bloquée), nommés côte à côte par une légende parce que « bloquée » et « en attente d'un humain » se ressemblent en ceci qu'aucune des deux n'avance. Deux contrôles y portent tout le poids et ne se voient pas à la relecture du composant : le rangement est prouvé par l'**indice de cellule** et non par la présence du texte — un `getByText` dirait seulement que l'entrée est quelque part, pas qu'elle est dans la colonne de son agent —, et le front **n'invente aucun ordre**, éprouvé en lui servant une frise à l'envers, qu'il rend telle quelle : le tri appartient à l'agrégat (§6.13), et une seconde règle de tri finirait par contredire la première. S'y ajoutent le couloir de **repli** avec son explication (« Sans agent » se lirait comme un défaut d'affichage, alors que c'est le couloir des tâches jamais routées), l'invariant « **aucune entrée perdue** » vérifié ligne par ligne, et la borne annoncée au lieu d'être subie. Un dernier contrôle est une **déclaration** et non une mesure, sur le patron de la colonne collante de `/couts` : le tableau garde son débordement **chez lui** (`overflow-x-auto` + `min-w-max`, deux utilitaires qui n'ont de sens qu'ensemble), faute de quoi un run à six agents pousserait le corps de la page — jsdom ne mesure aucune largeur, et le pixel appartient à `/banc-mise-en-page` |
| `tests/etat-des-runs.test.tsx` | L'état des runs au tableau de bord (#476, testé en #480) : **l'exhaustivité de la table des groupes**, balayée sur `regimeDuRun` plutôt qu'énumérée — un régime sans groupe fait disparaître ces runs-là de l'écran, ce qui est arrivé à « en pause » entre #476 et #477 — puis le plafond des soldés et ce qu'il annonce, `soldeAujourdHui` sur ses trois entrées, et l'écran qui ne porte **aucun** geste |
| `tests/a11y.test.tsx` | Le **filet d'accessibilité** (#537) en trois étages : `axe-core` joué sur les **écrans du menu** montés dans leur shell réel, verdict **0 violation `serious`/`critical`** — table d'écrans **dérivée de `MENU`**, donc une page ajoutée au menu sans cas d'audit rougit ; puis ce qu'axe ne sait pas voir — le **lien d'évitement** (premier dans l'ordre du DOM, visant un `<main>` que le focus peut atteindre), la **garde de mouvement** sur chaque utilité `transition`/`animate-` du produit, et le **plancher de 24 px** des cibles en petit corps. Comme `contraste.test.ts`, **la sonde est prouvée avant de servir** : sur un fragment fautif (image sans alternative, bouton sans nom, champ sans étiquette), puis sur un fragment sain |
| `tests/regions-live.test.tsx` | Les régions live des écrans temps réel (#538) : le **vocabulaire sans DOM** (seules les hausses parlent, un franchissement dit le total, les deux attentes humaines **absentes** du relevé des runs) ; la **présence** écran par écran, comptée sur l'attribut `aria-live` comme la sonde du ticket — une polie, zéro assertive ; le **contenu** après un événement simulé ; le **débit**, où une rafale de trois tâches ne coûte que deux phrases et douze événements du journal une seule ; et l'**assertive** avec sa réserve — unique dans le shell, muette sur une tâche terminée, et jamais redite par la région polie de l'écran qui montre l'arbitrage |
| `tests/sobriete.test.tsx` | La **règle des trois places** (#539, voir « Le langage visuel » ci-dessus) rendue opposable : les écrans du menu recensés, bandeau de tête ≤ 4 chiffres, corps ≤ 3 blocs, une seule colonne de propriétés, aucun bloc anonyme. Rien n'y est **déclaré** — le bandeau se reconnaît à ses `TuileChiffre`, la colonne à sa balise `<aside>`, et l'**arbitrage se prouve** en montant chaque écran une seconde fois files vides : un bloc qui prétendrait arbitrer sans disparaître compterait comme les autres. Sonde prouvée sur un échantillon fautif avant de balayer, comme `contraste.test.ts` |
| `tests/contraste.test.ts` | Le contraste de la palette sémantique (#534) : les **36 paires légitimes par thème** de #533 mesurées en octets dans `globals.css`, au seuil 4,5:1 (texte) ou 3:1 (contour, aplat d'état) — **et la sonde prouvée avant de servir**, sur les ratios que #471 avait mesurés au navigateur puis sur une faute glissée exprès. Le contrôle qui en fait un filet plutôt qu'un instantané est le dernier : un token ajouté sans paire **rougit** au lieu d'être vert par construction |
| `tests/hydratation.test.ts` | Ce que le layout racine **tolère du dehors** (#730) : les deux `suppressHydrationWarning`, celui de `<html>` (le `data-theme` que `SCRIPT_INIT_THEME` corrige, #118) et celui de `<body>` (les attributs qu'une extension y pose avant l'hydratation — Grammarly, LastPass…). Ils ont l'air d'un doublon et n'en sont pas : déplacer l'un sur l'autre, le geste qu'on fait en croyant simplifier, ramène l'un des deux écarts. La sonde lit les **octets du layout**, et ce n'est pas ici un pis-aller mais le seul filet possible — le symptôme exige un navigateur, un rendu serveur à hydrater et une extension installée, donc ni jsdom ni la CI ne le verront jamais revenir. Comme `contraste.test.ts`, elle est **prouvée avant de servir**, sur un échantillon fautif qui porte le piège : la prose du layout nomme `<body>` *avant* la balise, si bien qu'une recherche naïve rougirait un fichier correct |

Cinq fichiers portent l'outillage plutôt que des tests :

- `tests/setup.ts` — ce que jsdom ne fournit pas (`matchMedia`, `ResizeObserver`,
  `scrollIntoView`), la remise à zéro entre deux tests (stockage, `data-theme`,
  DOM), et le **réseau débranché** : `useControlTower`, `useChat` et la lecture
  des projets déclarés sont mockés globalement, si bien qu'aucun test n'a besoin
  de backend ni de faux serveur. Les deux hooks **notent ce qu'on leur demande**
  au passage — la portée d'une lecture (`porteesDemandees`, #281), le canal d'un
  fil (`canauxDemandes`, #273) et le **projet** qui part avec lui
  (`projetsDuFil`, #683) — parce que c'est là qu'est la promesse dans les trois
  cas : « aucun écran ne montre autre chose que le projet actif », « une mention
  change de destinataire » et « un run dicté au fil appartient au projet de la
  fenêtre » ne s'observent qu'au **paramètre** de l'appel, le contenu rendu
  venant du `poser…` correspondant quoi qu'il arrive ;
- `tests/aides.tsx` — les fabriques du domaine (agent, événement, validation,
  message, projet, **run** depuis #480, **nœud et graphe** depuis #491 — ce
  dernier *dérivé* de ses nœuds : `niveaux` regroupe sur le `niveau` que chaque
  nœud porte déjà, rien n'y est retrié, le tri topologique appartenant au
  backend), `poserProjetActif` (le projet retenu sans
  lequel tout rendu du shell s'arrête à la porte) et `rendreAvecEtat`, qui monte
  un composant sous le **vrai** fournisseur d'état du shell avec une source temps
  réel factice ;
- `tests/ecrans.tsx` — **les écrans du menu** (#537, extrait ici par #539) : quel
  composant chaque route rend, l'état partagé dans lequel on les monte
  (`peuplerEtat`, files d'arbitrage pleines, et `peuplerEtatSansArbitrage`, files
  vides mais projet toujours peuplé — tout vider ferait basculer le tableau de
  bord sur `PosteVide`, donc mesurer un autre écran), et `monterEcran`, qui monte
  sous le **vrai** `Shell`, attend que la garde du projet ouvre **puis laisse
  passer le tick de chargement différé**. `a11y` et `sobriete` s'en servent tous
  les deux : deux tables recopiées seraient le premier moyen qu'une suite rende
  un verdict sur un produit que l'autre ne monte plus. Leur nombre n'est écrit
  nulle part — la table est confrontée à `MENU` des deux côtés, donc c'est `MENU`
  qui fait foi (ils ont été dix de #537 à #270) ;

  ⚠ **La seconde attente a manqué jusqu'à #270**, et son absence coûtait la
  moitié de ce que les deux sondes prétendent mesurer : le `h1` de la barre
  supérieure vient du **menu** et non des données, donc il est là au premier
  rendu et `findByRole` rendait la main avant qu'aucun écran chargeant en
  différé n'ait reçu quoi que ce soit. Mesuré en ajoutant `/integrations` :
  l'écran était audité sur « Chargement des intégrations… » — un écran vide n'a
  presque pas de balises, donc axe n'y trouvait rien et le comptage n'y voyait
  aucun bloc. Un vert qui ne parle que du vide, exactement ce que `peuplerEtat`
  existe pour éviter ;
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
suites de runs, le troisième en montant les écrans du menu (#537) :

- **`chargerTaches` n'est pas mocké par `tests/setup.ts`**, contrairement à
  `chargerProjets` et `chargerJournal` — et **ni `chargerGrapheExecution`**
  (depuis #490) **ni `chargerFriseExecution`** (depuis #355) ne le sont non plus.
  Un écran qui les lit — la vue d'un run — part donc sur un vrai `fetch` et
  n'affiche qu'une bannière d'erreur : il lui faut un `vi.mock("@/lib/api")`
  local. Ce mock **remplace** celui du setup, d'où le `importOriginal` et la
  reconduction des deux autres lectures. Aucune des deux n'a sa place dans
  `tests/ecrans-reseau.ts` : elles ne servent que `/runs/<id>`, qui n'est pas un
  écran du menu — les mocks de ce fichier ne couvrent que ce que les dix écrans
  rencontrent ;
- **`runFactice` ne pose que les champs obligatoires** du contrat. `vitalite`,
  `progression`, `en_pause` et `cause` restent **absents** plutôt que posés à une
  valeur neutre — c'est ce que rend un backend antérieur au lot qui les a ajoutés,
  donc le cas qu'un écran doit savoir traiter ;
- **la liste des lectures non mockées est plus longue qu'on ne croit** :
  `chargerSante` (Paramètres › Général),
  `chargerPoolMcp`/`chargerRegistreMcp` (Paramètres ›
  MCP), `chargerExplorateur`/`chargerDisponibiliteSelecteur` (Composer) et
  `chargerExecution` (Valider le brief). Un test qui rend un **écran entier**
  plutôt qu'un composant les rencontre toutes. Et `useAnalyticsCouts` (page
  Coûts) se mocke **au hook** et non à l'API, parce qu'il ouvre sa propre
  WebSocket et se reconnecte en backoff : la couper à la source laisserait la
  promesse « aucun test n'a besoin de backend » tenue par un `fetch` qui échoue
  et des minuteurs qui survivent au test. `chargerCatalogue` a **quitté cette
  liste** avec #255 : le formulaire d'agent y lit désormais les rôles connus,
  donc *tout* test le montant partait sur un vrai `fetch`. Son défaut dans
  `setup.ts` est un catalogue **vide** — comme `poserProjets`/`poserJournal`, et
  contrairement à `CATALOGUE_POSTE_NU` : un poste nu garde une gamme (le registre
  ne dépend pas de la machine), là où zéro agent est un état ordinaire.

Quelques tests méritent d'être connus parce qu'ils gardent des invariants
qu'aucun outil n'attrape — ni le lint, ni le build, ni un rendu :

- celui qui **exécute** le script d'init du thème pour le confronter au module,
  sans quoi la page clignoterait au chargement ;
- ceux qui confrontent une **liste déclarée** aux **routes réellement présentes**
  sous `app/` : les entrées du menu (`lib/navigation.ts`), les destinations des
  redirections v1 (`next.config.ts`) et les ancres `data-guide` visées par la
  visite guidée. Une page supprimée laisserait sinon une entrée de menu vers un
  404, un signet redirigé vers le vide et une étape de visite sans cible ;
- ceux de la **porte unique** (#484) : qu'aucune entrée de menu ne porte plus
  `/composer` ni `/brief`, que les deux chemins soient **encore servis** en 307
  (jamais 308 — un 308 est mis en cache pour de bon), qu'aucune entrée de menu ne
  figure parmi les **sources** de redirection (une entrée qui se redirige
  elle-même est un aller simple, le piège de #190), et que le libellé du fil
  **résolve** — c'est cette dernière qui garde les cinq renvois, dont l'échec
  serait `null` et donc silencieux ;
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
  encore ses lignes d'un 🤖 et d'un glyphe par nature de lien ;
- ceux qui jouent la **transition** des listes liées du formulaire d'agent
  (`agent-listes-liees.test.tsx`, #255). L'invalidation d'un modèle devenu
  impossible ne s'observe **pas sur un rendu figé** : il faut choisir un
  fournisseur, saisir un modèle, puis en changer — c'est le seul moyen de
  distinguer « vidé » de « jamais rempli », et « annoncé » de « vidé en
  silence ». Le double de catalogue y porte à dessein **deux gammes
  dissemblables** : un modèle sans effort à côté d'un modèle qui s'y règle (sans
  quoi « le sélecteur suit le modèle » serait indiscernable de « il suit le
  fournisseur »), et une gamme **fermée** (`modeles_libres: false`) qu'aucun
  fournisseur du registre n'a aujourd'hui — la seule façon d'empêcher cette
  branche de mourir sans qu'on s'en aperçoive le jour où l'un le deviendra.
