# Retex — première session utilisateur-testeur : du poste vide au minuteur qui tourne

*2026-09-11 · ticket #854 · première exécution de `/retex-utilisateur` (#853)*

Ce rapport est ce qu'une personne qui **découvre Maestro** a vu en se servant de la Control Tower
réelle par sa seule interface, du poste vide jusqu'au livrable exécuté. Il ne juge pas le code : il
juge ce que le produit **montre et permet** aujourd'hui. Les captures citées sont dans
[`2026-09-11-premiere-session-utilisateur/`](./2026-09-11-premiere-session-utilisateur/).

---

## 1. Contexte

| | |
|---|---|
| Version | `origin/main` à **`a024c32`** (2026-09-11) |
| Stack | **mode réel** (jamais `--demo`) — API `127.0.0.1:8000`, UI `localhost:3000`, Redis `infra-redis-1` |
| Navigateur | Chrome piloté au MCP `chrome-maestro`, 1440×900 puis 420×860 et 420×1400 |
| Fournisseur | défaut du poste, agents en `claude-sonnet-5` (tous « Hérité », lu à l'écran Paramètres) |
| Poste de départ | **vide**, constaté à l'écran (capture `00-poste-vide.png`) |

Le poste a été vidé avant d'ouvrir le navigateur, par le protocole de #853 : `start.sh --stop`, puis
`purge --check --projets` (relevé : **597 événements** au journal, 0 battement, 0 tâche, 0
conversation, 0 projet déclaré), puis la purge réelle après accord explicite, puis `start.sh
--no-browser`.

⚠ **Une réserve de méthode, à connaître avant de lire la suite.** La session tournait dans le
worktree du ticket : les dossiers de données lus par la stack (`core/chat`, `core/projets`,
`core/ingestion`) étaient donc **ceux du worktree**, neufs, et la purge n'a réellement effacé que le
**journal Redis**, qui lui est partagé. Le poste était bien vide à l'écran — c'est ce qui compte
pour un retex — mais le geste de purge n'a été éprouvé à fond que sur un seul de ses cinq postes.

---

## 2. Le parcours, écran par écran

### 2.0 La porte d'entrée — « Choisir le projet » (`PosteVide`)

Sans projet déclaré, l'application **ne montre ni menu ni shell** : elle s'ouvre sur une page unique
qui explique ce qu'est un projet (« une **racine sur le disque** : tout ce que la Control Tower
montre lui appartient ») et propose d'en déclarer un. C'est un bon choix : on ne peut pas se perdre.

Le formulaire est soigné — deux origines (*Dossier existant* / *Nouveau dossier*), périmètre inclus
/ exclus avec des défauts, et un **explorateur de dossiers servi par le backend** : liste des
racines (`C:/`, `E:/`, dossier utilisateur), annotation « dépôt Git » sur les dossiers versionnés,
bouton « Parcourir sur mon poste… » et **champ « Aller à un chemin absolu »**. Rien à redire, sauf
un détail qui compte pour qui pilote au clavier : la déclaration a pris **quatre gestes** et n'a
jamais eu besoin du sélecteur natif.

Deux défauts de texte, vus ici et sur `/projets` :

- « Un projet, c'est une racine sur le **disqueet** ce qu'elle expose aux agents » — une espace
  manquante après un `<strong>` (capture `13-projets.png`).
- « Le dossier se choisit dans l'explorateur servi par le backend — **jamais en tapant un chemin** »
  — la phrase **contredit l'écran**, qui offre justement un champ « Aller à un chemin absolu ».

Et un artefact de purge : parce que le navigateur se souvenait du projet précédent, l'écran s'est
ouvert sur une alerte « Le projet ouvert la dernière fois (prj-demo) n'est plus déclaré » avec
`motif : projet-inconnu` — un identifiant technique rendu tel quel à l'utilisateur.

### 2.1 `/` — Tableau de bord

Vide, l'écran est une vraie page d'accueil : il dit ce qui n'est **pas** une panne, ce qui
apparaîtra ici, et offre deux portes (« Lancer une orchestration » → *Ouvrir le chat* ; « Juste
explorer l'interface »). C'est la meilleure page vide du produit.

Deux fuites du dépôt dans le produit (capture `02-tableau-de-bord-vide.png`) :

- la seconde porte propose **une commande shell** — `bash scripts/controltower/start.sh --demo`,
  « à relancer **depuis le dépôt** » — à quelqu'un qui n'a pas de dépôt ;
- « Tout se passe dans le fil **(#481)** » : un numéro de ticket interne, dans le produit.

En cours de run, cet écran **se contredit sur lui-même** — voir §5, c'est le constat le plus visible
du retex.

### 2.2 `/chat` — Chat

L'écran est bien pensé : le fil au centre, et à droite *Conversations*, *Parler à* (les cinq agents
+ l'orchestration, adressables par `@nom`), *Cadrage en attente*, *Ouvert depuis ce fil*. Le message
d'accueil est juste (« Rien ne part sans votre accord ») et il a été tenu.

Deux choses accrochent :

- **Les amorces proposées parlent de Maestro, pas de votre projet** : « Pagine les projets »,
  « Corrige le tri Kanban ». Sur un projet qui est un minuteur Pomodoro, ce sont des suggestions
  hors-sujet — elles décrivent le backlog de l'outil, pas le travail de l'utilisateur. Même chose
  dans le panneau *Ouvert depuis ce fil* : « Dites le travail à faire — *« ajoute la pagination à la
  liste des projets »* ».
- À 1440×900, la conversation vide laisse **les deux tiers hauts de l'écran blancs**, le message
  d'accueil étant collé au composeur.

### 2.3 `/runs` — Runs

Vide : une phrase juste, un lien « Ouvrir le chat ». Rien à signaler. Comme sur les autres écrans
vides, le bloc occupe le haut et laisse ~700 px de blanc.

### 2.4 `/agents` — Agents

Cinq agents (`bdd`, `designer`, `developpeur`, `devops`, `qa`), tous « Libre », origine « du code »,
avec recherche et quatre filtres. La fiche d'un agent a cinq onglets (Profil, Playbook, MCP &
permissions, Chat, Logs) — bien découpé.

Sur la fiche `developpeur` (capture `05-agents.png` pour la liste) : le paragraphe explicatif sous
« Sur ce poste : claude (Claude Code) » est **très technique** pour un écran produit (« les CLI sont
résolus sur le `PATH` du process qui sert l'API… ») et il porte une **phrase recollée** : « *peut
être installé sur la machine seuls la boucle locale et l'environnement de ce process sont
regardés* » — il manque une ponctuation entre deux phrases.

### 2.5 `/integrations` — Intégrations

Trois chiffres en tête (pool projet, agents équipés, secrets à revoir), le pool du projet, puis une
bibliothèque recherchable (GitHub, Playwright, Slack, Atlassian, Context7…) avec un badge de mode
d'authentification par entrée. Clair et utile.

Une fuite de plus : la fiche Playwright dit « C'est le serveur derrière `chrome-maestro` **dans ce
dépôt** » — le catalogue d'un produit parle du dépôt de son éditeur (capture `07-integrations.png`).

### 2.6 `/couts` — Coûts & analytics

Quatre chiffres de tête, un sélecteur de période, deux blocs. Sobre et lisible. Après le run il rend
exactement ce qu'on lui demande (§3).

Coquille typographique, partagée avec `/journal` et `/projets` : « Rien encore sur Minuteur
Pomodoro**:** aucune exécution… » — il manque l'espace avant le deux-points, alors que le reste du
produit l'écrit correctement (« Période : », « Coût cumulé : »).

### 2.7 `/validations` — Validations

Vide au départ, et **vide à la fin** : aucune demande d'arbitrage n'a été faite de tout le run. Voir
§4 (O2) — ce n'est pas un défaut de l'écran, c'est un fait sur la boucle.

### 2.8 `/journal` — Journal

Recherche, trois filtres, une case « Notable seulement (ce que remonte la cloche) », un compteur
d'événements. L'en-tête explique bien la différence entre le journal persisté et le temps réel.

### 2.9 `/parametres` — Paramètres

Trois familles (*Le poste*, *L'exécution*, *La dépense*) avec un sommaire latéral : la meilleure
organisation du produit. On y trouve l'URL de l'API, l'état du flux temps réel, le thème, les
notifications, le tableau fournisseur/modèle par agent et la dépense cumulée.

Deux points :

- **Le plafond de dépense n'est pas réglable depuis l'interface** : « Il se fixe au lancement d'une
  exécution (option `--plafond-cout`, **#56**) » (capture `12-parametres-fournisseurs.png`). Pour un
  produit dont un run coûte plus de douze dollars, c'est le réglage qui manque le plus — et c'est
  encore un numéro de ticket montré à l'utilisateur.
- « Flux temps réel » affiche l'URL WebSocket brute et cite **(#281)**. Troisième numéro de ticket
  dans le produit, après #481 et #56.

### 2.10 `/projets` — Projets (hors menu, par le sélecteur)

La carte du projet porte ses badges (*Nouveau dossier*, *Non versionné*), sa racine, son périmètre,
ses dates et trois actions (*Modifier*, *Mettre sous Git*, *Supprimer*). « Mettre sous Git » est une
bonne idée, bien placée.

### 2.11 Les gestes transverses

- **Sélecteur de projet** : présent dans la barre supérieure sur toutes les pages, avec la racine en
  infobulle. Sans reproche.
- **Thème** : trois choix (Clair / Sombre / Système), appliqué immédiatement, persistant. Le thème
  sombre est **complet et lisible** (capture `15-…` non retenue, rendu vérifié sur le tableau de
  bord) — rien de bancal.
- **Notifications** : panneau en deux sections (*À valider*, *Activité récente*). Voir §5 : il est
  resté muet du début à la fin.
- **Visite guidée** : 7 étapes, échappable, relançable depuis l'aide. Deux défauts de fond
  (capture `16-visite-guidee.png`) : l'étape « La navigation » énumère « tableau de bord, agents,
  chat, coûts & analytics, validations et paramètres » et **oublie Runs, Intégrations et Journal** ;
  et surtout la visite **ne dit jamais comment lancer quoi que ce soit** — elle se termine sur « À
  vous de jouer » sans avoir nommé le chat, qui est pourtant la seule porte d'entrée depuis #470.
- **Assistant** (flottant) : répond juste — il renvoie au Chat. Mais il **tutoie** (« Ce que je peux
  te dire ») là où tout le produit vouvoie, il cite comme sources des **fichiers du dépôt**
  (`docs/07-guide-de-demarrage.md`, `docs/00-cahier-des-charges.md`…) que l'utilisateur ne peut pas
  ouvrir, il demande à l'utilisateur de lui « fournir l'extrait correspondant de la documentation »,
  et son composeur n'a ni la forme ni la couleur de celui du chat (bouton **bleu** « Envoyer » quand
  la couleur d'action du produit est verte) — capture `19-assistant-reponse.png`.

### 2.12 Au format téléphone (420 px)

À **420×860**, `/chat` s'ouvre **sur le bas de la pile** : les trois panneaux latéraux occupent tout
l'écran, et le composeur — le seul endroit où l'on donne un objectif — est **hors champ**, au-dessus
(capture `22-chat-420-bas.png`). Ni la touche `Fin` ni le défilement de page ne le ramènent. À
**420×1400**, l'ordre est correct (conversation et composeur en tête, panneaux dessous) : ce n'est
donc pas l'empilement qui est faux, c'est la position de défilement initiale. Au passage, seules
**2 des 4 amorces** sont atteignables à cette largeur.

---

## 3. Le run

### Ce que j'ai demandé, et pourquoi

> « Je voudrais un minuteur Pomodoro qui tourne dans le navigateur : une page web autonome (HTML +
> CSS + JavaScript, sans dépendance ni étape de build) qu'on ouvre en double-cliquant sur
> `index.html`. Il faut un compte à rebours de 25 minutes bien lisible, un bouton Démarrer/Pause, un
> bouton Réinitialiser, le passage automatique à une pause de 5 minutes à la fin d'une séance puis
> retour au travail, un compteur des séances terminées, et un signal visible et sonore à chaque
> changement de phase. Soigne l'apparence : ça doit être agréable à regarder et utilisable au
> clavier. »

**Pourquoi celui-là** : il fallait un livrable *avec une interface* que l'on puisse **exécuter et
juger sans rien installer**. Une page autonome remplit les trois conditions — elle a un vrai écran,
elle a un **comportement** observable (un compte à rebours qui tourne, une bascule de phase, un
compteur qui s'incrémente) plutôt qu'un simple formulaire, et elle n'exige ni dépendance, ni build,
ni environnement : le verdict « ça marche ou pas » ne peut donc pas être brouillé par un problème
d'installation.

### Le cadrage

L'orchestration a répondu en 15 s par une reformulation fidèle et une question : « **Je lance ?** ».
J'ai répondu « Oui, lance. » et le run est parti. Deux remarques :

- **La question n'a pas d'affordance** : pas de bouton *Lancer* / *Modifier*, et le panneau
  « **Cadrage en attente** » — l'endroit exact où l'on s'attend à trouver la demande — est resté
  affiché « Aucun cadrage en attente » **pendant** que la question était posée, puis après le
  lancement. Il faut deviner qu'on répond au clavier.
- Le message d'ouverture annonce « statut « **en_cours** » » : l'identifiant technique, pas le
  libellé.

### Le déroulé

| | |
|---|---|
| Run | `296e6fc9f042` |
| Durée | **53 min** (12:38:01 → 13:31, lue à l'écran) |
| Coût | **12,51 $US** (lu à l'écran Coûts) |
| Tokens | **14 064 104** (13 815 542 entrée · 248 562 sortie), 6 appels modèle |
| Plan | **4 tâches · 3 enchaînements · 3 niveaux · jusqu'à 2 de front** |
| Arbitrages demandés | **0** |

| # | Tâche | Agent | Étapes | Coût | Durée affichée |
|---|---|---|---|---|---|
| 1 | Coquille visuelle (HTML + CSS) | **designer** | 7/7 | 3,04 $ | 13 min 44 s |
| 2 | Module sonore embarqué (Web Audio API) | **developpeur** | 6/6 | 1,63 $ | 24 min 17 s |
| 3 | Logique du minuteur, clavier et intégration | **developpeur** | 6/6 | 1,90 $ | 10 min 21 s |
| 4 | Vérification des critères d'acceptation | **qa** | 14/15 | 3,25 $ | 13 min 46 s |

Répartition lue à l'écran Coûts : `developpeur` 28 % · `qa` 26 % · `designer` 24 % ·
**`orchestrateur` 21 %** — un cinquième de la dépense part dans la planification, avant qu'une seule
ligne ne soit écrite.

**Les quatre premières minutes ne montrent rien.** De 12:38 à 12:42, l'écran du run affiche « Aucune
tâche », le journal du run porte deux lignes, et rien ne dit qu'une décomposition est en cours. Le
coût, lui, monte (1,24 $ à une minute, 2,68 $ à cinq) : la seule chose qui bouge est la facture.

**Puis le pipeline devient excellent.** Le graphe par niveaux, avec l'agent, le dernier geste
horodaté, la checklist (`2/7`, `5/7`…) et le chrono par tâche, est la meilleure vue du produit — de
loin. C'est là, et seulement là, qu'on comprend ce qui se passe. Le run offre quatre lectures
(Pipeline, Kanban, Frise, Journal) ; je les ai toutes ouvertes en cours de route — la **frise**
(capture `34-run-frise.png`, prise à la 5ᵉ minute) rendait alors « 1 entrée · 1 couloir », ce qui
était juste à cet instant : une seule tâche avait démarré. Je ne l'ai pas rouverte en fin de run,
donc **ce retex ne dit rien de sa granularité finale**. L'état final du run est capturé dans
`44-run-54min.png`.

### Le livrable : **il fonctionne**

Quatre fichiers dans la racine du projet (`index.html`, `style.css`, `audio.js`, `timer.js`), plus
`DESIGN.md` et un rapport de vérification. Ouvert et **utilisé** (capture `46-livrable-initial.png`) :

- l'écran s'affiche : badge « Travail », **25:00** en grands chiffres, *Démarrer* / *Réinitialiser*,
  « Séances terminées 0 » — soigné, centré, lisible ;
- **Démarrer** lance le décompte (24:44 après 16 s) et le bouton devient **Pause** ;
- **Pause** fige le compteur (vérifié stable sur 5 s), **Réinitialiser** ramène à 25:00 ;
- **au clavier** : `Tab` atteint *Démarrer*, `Espace` le déclenche.

Je n'ai pas pu observer la bascule de phase en séance (il aurait fallu attendre 25 minutes). Plutôt
que de croire le rapport de la QA sur parole, **j'ai rejoué son harnais moi-même** (`node
qa/verif.mjs`, Chrome réel en CDP, vrais événements d'entrée, horloge accélérée) :

> **17 vérifications, 17 OK, 0 échec** — dont la bascule automatique Travail → Pause `05:00` →
> Travail, l'incrément du compteur à la fin du Travail **et pas** à la fin de la Pause, les appels
> sonores dans l'ordre `["pause","travail"]`, l'`AudioContext` passé à `running`, la touche `R`,
> l'absence de double activation à l'Espace, **aucune requête hors `file://`** et **aucune erreur
> console**.

**Verdict : l'objectif est tenu.** L'interface démarre, fait ce qui était demandé, s'ouvre bien en
double-cliquant sur `index.html` (les 4 requêtes du harnais sont toutes en `file://`), et la qualité
du travail des agents — en particulier la rigueur de la QA, qui écrit ce qu'elle **n'a pas** pu
tester et pourquoi — est au-dessus de ce qu'on attendrait.

Deux réserves sur le contenu du dossier livré : la tâche 4 est marquée **Terminée à 14/15**, et la
racine du projet contient les **dossiers de travail des agents** (`_verif/`, `_verif_timer/`,
`qa/`), laissés en place à côté du livrable.

### Et à la fin, personne ne vous dit rien

C'est le point qui pèse le plus lourd de tout ce retex.

- Le **fil de conversation** — d'où le run est parti — ne dit **jamais** qu'il est fini. Son dernier
  message date de 12:38, et la carte du run y affiche encore « **4 tâches ouvertes** » une heure
  après le lancement, six minutes après la fin du run (visible dans `49-chat-question-fin.png`).
- La **cloche** dit « *Rien de notable pour l'instant* » après 53 minutes et 12,51 $
  (capture `48-cloche-fin-run.png`).
- **Rien, nulle part, ne dit où est le livrable.** Aucun chemin, aucun lien, aucun « voici
  `index.html` ». Il faut se souvenir de la racine qu'on a déclarée une heure plus tôt.

Interrogée dans le chat — « Où en est mon minuteur ? Et où est le fichier que je dois ouvrir ? » —
l'orchestration répond (capture `49-chat-question-fin.png`) :

> « *il n'y a plus aucun run en cours […] **Je n'ai pas la main pour inspecter son verdict final ni
> le contenu produit** : regarde le tableau de bord Maestro (ou `/orchestrate --status`) […] et le
> chemin du livrable (ton `index.html`) y sera indiqué.* »

Trois défauts dans une seule réponse : l'orchestration **ne sait pas** ce que son propre run a
produit ; elle renvoie vers **`/orchestrate --status`**, une commande du workflow de développement
qui n'existe pas pour un utilisateur ; et elle affirme que le chemin du livrable est indiqué au
tableau de bord, ce qui est **faux**.

---

## 4. L'objectif de Maestro, confronté

> « Vous donnez un objectif ; un agent orchestrateur le découpe en tâches, les confie
> automatiquement aux bons spécialistes, ceux-ci travaillent en parallèle et de façon autonome, et
> vous gardez le contrôle via une console de supervision. L'utilisateur passe du rôle d'opérateur à
> celui de **chef d'orchestre**. » — [docs/00 §1.2](../00-cahier-des-charges.md)

**Tenu dans son cœur, manqué à ses deux bouts.** Un objectif en une phrase a produit un logiciel qui
marche, sans qu'aucune ligne ne soit écrite à la main : c'est la promesse, et elle est tenue. Mais
le chef d'orchestre **ne sait pas ce que fait son orchestre pendant qu'elle joue** (quatre minutes
de silence, puis des compteurs qui se contredisent), et **on ne lui remet pas la partition à la
fin** (ni annonce, ni chemin, ni lien).

| | Ce que le run a montré |
|---|---|
| **O1 — Spécialisation** (≥ 5 agents) | **Tenu.** 5 agents au catalogue, **3 rôles distincts** réellement employés et **pertinents** : `designer` pour la coquille visuelle, `developpeur` pour l'audio puis le moteur, `qa` pour la vérification. Le découpage lui-même est bon (coquille → audio → intégration → vérification, avec un contrat d'IDs entre les deux premiers). |
| **O2 — Autonomie** (≥ 70 % sans intervention) | **Tenu, et au-delà de la cible : 100 %.** 4 tâches sur 4 terminées sans la moindre intervention, **zéro demande d'arbitrage**. ⚠ Ce chiffre ne se lit pas comme un succès sans réserve : le pipeline affiche « *appel de l'outil 'Bash' laissé passer — décideur « auto », personne n'a été sollicité* », c'est-à-dire que **rien n'est jamais escaladé** parce que le régime de permissions le dit — et non parce que rien ne le méritait. L'utilisateur n'a choisi ce régime nulle part. |
| **O3 — Assignation automatique** (≥ 90 % de routage correct) | **Tenu sur ce run** : 4 assignations, 4 pertinentes. Le Kanban offre en plus un « Réassigner à… » par tâche. À nuancer : 4 cas ne mesurent pas un taux, et les tâches non démarrées affichent « Agent non assigné » — le routage est décidé au démarrage, pas au plan. |
| **O4 — Parallélisme** (≥ 5 agents en parallèle) | **Non observé.** Le plan annonce « jusqu'à **2 de front** » et les deux tâches de niveau 1 étaient indépendantes — mais elles se sont exécutées **l'une après l'autre** : la tâche 2 n'a démarré qu'à la 18ᵉ minute, quand la tâche 1 s'est soldée. À aucun instant deux agents n'ont travaillé ensemble. Sur un plan à 4 tâches et 3 niveaux, c'est ~14 minutes de mur perdues. |
| *O5 — Supervision* (« toutes les actions clés depuis l'UI ») | **Manqué sur un point nommé par le produit lui-même** : le plafond de dépense n'est réglable qu'en ligne de commande. On peut lancer, mettre en pause et interrompre un run depuis l'UI — mais pas le borner. |

---

## 5. Constats classés

Chaque constat est confronté au backlog : *déjà couvert par #n*, *trou*, ou *renverse une décision
écrite*. Le backlog ouvert ne compte que **16 tickets** au moment du retex.

### Bloquant

> Rien n'a empêché d'aller au bout. **Aucun constat bloquant.** Le parcours complet — poste vide →
> projet → objectif → run → livrable exécuté — s'est fait sans contournement et sans commande.

### Gênant

| # | Constat | Preuve | Backlog |
|---|---|---|---|
| **G1** | **Un run qui se termine ne prévient personne, et ne dit pas où est le livrable.** Ni le fil (dernier message : le lancement), ni la cloche (« Rien de notable »), ni le tableau de bord ne signalent la fin ; aucun écran ne donne le chemin du résultat. Interrogée, l'orchestration dit ne pas avoir la main pour l'inspecter. | `48-cloche-fin-run.png`, `49-chat-question-fin.png` | **Trou.** Rien dans le backlog ouvert ni fermé. C'est le dernier mètre de « du brief au livrable ». |
| **G2** | **Le tableau de bord se contredit sur le même écran** : la tuile de tête « Run en cours » affiche **Aucun** (« aucune tâche connue ») pendant que la section « État des runs » juste dessous affiche « **EN COURS 1** » avec le run qui tourne. Au même moment, « Activité en direct » dit « aucun événement reçu » alors que le journal du run en porte deux. | `30-dashboard-run.png` | **Voisin de #568** (« *le poste de tête désigne un run annulé la veille comme run en cours* ») — **même tuile, symptôme inverse** ; #568 est ouvert et non arbitré. |
| **G3** | **Le Kanban et le compteur du run ne comptent pas les mêmes tâches que le pipeline.** Pipeline : « 4 tâches ». Kanban : **1 carte** (les tâches « À faire » non assignées n'ont aucune colonne). En-tête : « 0/1 soldée », puis « 1/2 », « 2/3 », « 3/4 » — le **dénominateur grandit**, si bien que la barre de progression est presque pleine quand le run est à moitié fait. | `33-run-kanban.png`, `41-run-34min.png` | **Trou.** #834 (vues du run) est fermé et portait la *fraîcheur*, pas la *cohérence des comptes*. |
| **G4** | **La durée affichée d'une tâche terminée inclut son attente.** Tâche 2 : **24 min 17 s** annoncées, alors qu'elle était « À faire, agent non assigné » jusqu'à la 18ᵉ minute et que le run entier a duré 53 min — au plus ~11 min de travail réel. | `41-run-34min.png` | **Déjà couvert par #568** (« *21 min 17 s annoncées pour 8 min 05 s de travail* ») — **reproduit à l'identique**, deux semaines et un chantier plus tard. |
| **G5** | **Le plafond de dépense n'est pas réglable depuis l'interface** — le produit le dit lui-même et renvoie à une option de ligne de commande. Un run coûte ici 12,51 $ sans borne possible. | `12-parametres-fournisseurs.png` | **Déjà couvert par #568** (« *le Composer n'expose aucun des cinq garde-fous que l'API accepte* ») — ouvert, non arbitré. |
| **G6** | **Le produit parle le dépôt à son utilisateur** : une commande shell sur la page d'accueil (`bash scripts/controltower/start.sh --demo`, « depuis le dépôt »), trois numéros de tickets internes à l'écran (**#481**, **#281**, **#56**), un catalogue qui dit « dans ce dépôt », un assistant qui cite `docs/*.md` comme sources, et une orchestration qui renvoie à **`/orchestrate --status`**. | `02-…`, `07-…`, `12-…`, `19-…`, `49-…` | **Trou.** |
| **G7** | **La visite guidée n'apprend pas à se servir du produit** : elle omet Runs, Intégrations et Journal dans sa description de la navigation, et surtout **ne nomme jamais le chat**, seule porte d'entrée depuis #470. On finit la visite sans savoir démarrer. | `16-visite-guidee.png` | **Trou.** #122 (qui l'a créée) est fermé ; elle n'a pas suivi les écrans ajoutés depuis. |
| **G8** | **Au format téléphone, le composeur du chat est hors champ** : à 420×860, `/chat` s'ouvre sur le bas de la pile de panneaux et ni le clavier ni le défilement de page ne ramènent le champ de saisie. | `22-chat-420-bas.png` | **Trou.** |
| **G9** | **Les amorces du chat proposent le backlog de Maestro, pas le travail de l'utilisateur** (« Corrige le tri Kanban », « Pagine les projets », « ajoute la pagination à la liste des projets ») — sur un projet qui est un minuteur. | `03-chat-vide.png` | **Trou.** #916 a réglé leur *calibrage*, jamais leur *contenu*. |
| **G10** | **Le cadrage se demande sans affordance** : l'orchestration écrit « Je lance ? » sans bouton, et le panneau « Cadrage en attente » affirme « Aucun cadrage en attente » au moment même où la question est posée. | `24-…`, `25-…` (non retenues) | **Trou** — voisin de **#568 B3** (les demandes d'arbitrage n'atteignent pas l'écran), mais ici c'est le *cadrage*, pas la *validation*. |
| **G11** | **Les quatre premières minutes du run ne montrent rien** : « Aucune tâche », journal à deux lignes, aucun indice qu'une décomposition est en cours — pendant que le coût monte à 2,68 $. | `26-…`, `29-…` (non retenues) | **Voisin de #834** (fermé), qui traitait le rafraîchissement des vues *d'un run décomposé*, pas la **phase de planification**. |
| **G12** | **La racine du projet contient les dossiers de travail des agents** (`_verif/`, `_verif_timer/`, `qa/`) à côté du livrable ; et la tâche 4 est marquée **Terminée à 14/15**. | livrable | **Trou.** |

### Cosmétique

| # | Constat | Backlog |
|---|---|---|
| **C1** | « racine sur le **disqueet** ce qu'elle expose » — espace manquante (`/projets`). | Trou |
| **C2** | « Le dossier se choisit dans l'explorateur — **jamais en tapant un chemin** » : la phrase contredit le champ « Aller à un chemin absolu » du même écran. | Trou |
| **C3** | « Rien encore sur X**:** aucune… » — espace manquante avant le deux-points, sur `/couts`, `/journal` et `/projets` (une seule chaîne partagée). | Trou |
| **C4** | Fiche agent : deux phrases recollées sans ponctuation (« *peut être installé sur la machine seuls la boucle locale…* »). | Trou |
| **C5** | **Registre de langue incohérent** : l'assistant et l'orchestration **tutoient** (« Je te propose », « Ce que je peux te dire »), tout le reste du produit **vouvoie** — y compris le message d'accueil du chat, deux lignes au-dessus. | Trou |
| **C6** | **L'assistant n'utilise pas le socle visuel** : composeur différent de celui du chat, bouton « Envoyer » **bleu** quand l'action primaire du produit est verte. | Trou |
| **C7** | Statuts techniques rendus tels quels : « statut « **en_cours** » » dans le chat, `motif : projet-inconnu` sur la page de choix du projet. | Trou |
| **C8** | **L'écran Coûts compte 8 tâches pour un run qui en a 4** : quatre lignes internes `coquille-ui:fusion`, `module-audio:fusion`, `moteur-minuteur:fusion`, `verification-qa:fusion` y figurent comme des tâches, sans agent ni coût ni durée. | Trou |
| **C9** | La durée écoulée d'un run **ne s'anime pas** : elle ne bouge qu'au rechargement ou à l'arrivée d'un événement — sur l'écran où l'on attend. | Voisin de #834 (fermé) |
| **C10** | La checklist d'une tâche **change de dénominateur** en cours de route (`0/4` → `2/7`) et peut se figer longtemps (tâche 4 : `2/12` pendant 9 minutes de travail visible). | Trou |
| **C11** | Le journal du run **mélange horodatage absolu et relatif** dans la même liste (« 12:39:27 » et « il y a 2 min »). | Trou |
| **C12** | Le titre de la carte « Nouveau projet » n'expose **aucun texte accessible** (`<h3>` vide dans l'arbre d'accessibilité) ; le nom vient du `<form>`. | Trou |
| **C13** | Le tableau de bord compte « **6 agent(s) du poste** » quand `/agents` en liste **5** : l'orchestrateur est compté ici, absent là — et il apparaît pourtant comme un agent dans la répartition des coûts (21 %). | Trou |

### Ce qui va bien, et qui doit rester

À mettre en face, parce que la liste ci-dessus est plus longue que ce qu'elle mesure :

- **la qualité du travail des agents** — comme dans #568, ce n'est pas le maillon faible : le
  découpage est juste, le contrat d'IDs entre tâches est explicite, et la QA a écrit noir sur blanc
  ce qu'elle n'a **pas** pu tester et pourquoi ;
- **le pipeline du run** : graphe par niveaux, dernier geste horodaté, checklist, chrono, coût par
  tâche — la meilleure vue du produit ;
- **la page de choix du projet et son explorateur de dossiers**, qui font un travail difficile
  (désigner une racine sur le disque, depuis un navigateur) sans jamais exiger de coller un chemin ;
- **les pages vides**, qui expliquent ce qui n'est pas une panne au lieu d'afficher un vide ;
- **Paramètres en trois familles**, le **thème sombre**, la **sobriété générale** : aucun écran n'est
  surchargé, et la règle des trois places se voit ;
- **B1 de #568 est corrigé** : le travail **rejoint** la racine du projet. C'était le défaut le plus
  grave de la revue précédente ; il ne se reproduit pas.

---

## 6. Proposition — à trancher par un humain, rien n'a été créé

**Un milestone** : **« Le dernier mètre : rendre le run lisible et son résultat remis »** — rail
*produit*, à placer **avant** la Phase 9 (on n'empaquette pas un produit qui ne dit pas à son
utilisateur que son travail est fini).

| Ticket proposé | `type::` | `prio::` | Couvre |
|---|---|---|---|
| Un run qui se termine l'annonce, et remet son livrable (fil + cloche + chemin ouvrable) | `feature` | haute | G1 |
| Les comptes du run disent la même chose partout : pipeline, Kanban, en-tête, barre de progression | `bug` | haute | G3, C8 |
| La durée d'une tâche est son temps de travail, pas son temps d'attente | `bug` | haute | G4 *(déjà décrit dans #568 — à rattacher plutôt qu'à recréer)* |
| La tuile « Run en cours » du tableau de bord dit ce que la page dit en dessous | `bug` | haute | G2 |
| Borner la dépense et les tours au lancement, depuis le chat | `feature` | haute | G5 *(recoupe #568)* |
| Le cadrage se répond d'un bouton, et « Cadrage en attente » le montre | `feature` | moyenne | G10 |
| La phase de planification se voit pendant qu'elle dure | `feature` | moyenne | G11 |
| Le produit cesse de parler le dépôt : ni commande shell, ni numéro de ticket, ni `docs/*.md` à l'écran | `bug` | moyenne | G6 |
| La visite guidée mène au premier run (et connaît les neuf entrées du menu) | `bug` | moyenne | G7 |
| Le chat tient à l'écran d'un téléphone : le composeur d'abord | `bug` | moyenne | G8 |
| Les amorces du chat proposent le travail de l'utilisateur, pas celui de Maestro | `feature` | moyenne | G9 |
| Un run laisse la racine du projet propre | `bug` | basse | G12 |
| Un seul registre de langue, un seul socle visuel pour l'assistant | `bug` | basse | C5, C6 |
| Coquilles et libellés techniques à l'écran (lot unique) | `bug` | basse | C1–C4, C7, C9–C12 |

**Trois arbitrages que ce retex appelle, et qu'il ne rend pas :**

1. **O4 est-il tenu ?** Le plan sait dire « 2 de front », la boucle ne l'exécute pas. Est-ce un
   réglage, un défaut, ou une décision assumée ? La cible de docs/00 est « ≥ 5 agents en parallèle ».
2. **Le régime de permissions « auto » est-il le bon défaut ?** Il donne 100 % d'autonomie — et
   personne ne l'a choisi. Un utilisateur devrait-il pouvoir dire ce qu'il veut arbitrer ?
3. **#568 doit-il être repris ou remplacé ?** Deux de ses défauts secondaires se reproduisent à
   l'identique aujourd'hui (G4, G5) et ses quatre arbitrages ne sont toujours pas rendus. Le
   milestone proposé ici est l'occasion de les trancher, ou #568 restera ouvert un mois de plus.

---

## 7. Ce que ce retex n'est pas

Ni un bilan de jalon (`/milestone-bilan`, qui exerce des **critères de sortie** et propose un
verdict), ni une vérification de câblage (`/verify`), ni un banc de mise en page
(`/banc-mise-en-page`) : ces trois-là savent ce qu'ils cherchent — celui-ci a regardé avec les yeux
de quelqu'un qui ne savait pas.
