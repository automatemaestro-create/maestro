# Renforcement sécurité — modèle de menace, activation, vérification (parent #102)

**Version :** 0.1
Page de synthèse du chantier **renforcement sécurité** (#102), livré en quatre
lots : isolation d'exécution (#108, [docs/17](./17-isolation-execution.md)),
secrets par agent (#109, [docs/18](./18-secrets-par-agent.md)), permissions par
agent et par outil (#110, [core/permissions/README](../core/permissions/README.md))
et tests + doc (#107, cette page). Elle consigne le **modèle de menace** commun
aux trois mécanismes, leur activation, la **vérification** (tests automatisés +
procédure manuelle du mode isolé) et les limites connues consolidées.

Elle a grandi avec le produit, et chaque élargissement porte sa date : le **projet
local** de l'utilisateur (§2.1), les **sources** d'un objectif (§2.2), la
**bibliothèque MCP** (§2.3) et la **fenêtre de bureau** (§2.4) — celle-ci étant le
premier endroit où la frontière n'est plus tenue par un navigateur tiers, mais par
notre code.

> **Pourquoi** : les agents exécutent du code (`Bash`, fichiers produits,
> serveurs MCP stdio) et manipulent des tokens d'intégration. L'ouverture MCP
> (#101) et le multi-instances (#100) élargissent la surface : ce chantier
> borne ce qu'un agent — ou ce qu'il exécute — peut toucher, voir et faire.

---

## 1. Actifs à protéger

- **Le poste hôte** (fichiers hors workspace, environnement, credentials du
  poste) — les agents ne doivent pas pouvoir en sortir de leur espace de tâche ;
- **les secrets d'intégration** (tokens Slack, GitLab… des serveurs MCP) — un
  agent ne doit voir que les siens, et aucun ne doit fuir en sortie ;
- **les systèmes externes** (canaux Slack, tickets, API) — un agent ne doit y
  faire que ce que sa politique d'outils lui permet ;
- **l'observabilité elle-même** (journal, traces Langfuse #81, fil temps réel
  Control Tower #98, rapports) — elle doit voir les violations sans devenir un
  canal de fuite.

## 2. Modèle de menace et contre-mesures

Attaquant considéré : un **agent défaillant ou manipulé** (prompt injection via
une tâche, un livrable de dépendance ou un contenu ramené par un outil), un
**serveur MCP tiers compromis**, ou du **code produit** exécuté par l'agent.
L'opérateur humain et le poste lui-même sont réputés de confiance (POC).

| Menace | Vecteur | Contre-mesure | Lot |
|---|---|---|---|
| Évasion du workspace : lecture/écriture de fichiers de l'hôte | `Bash`, code produit, serveur MCP stdio | Mode isolé : CLI et tout ce qu'il lance dans un conteneur durci jetable — seul le workspace de la tâche est monté, racine en lecture seule, non-root, `--cap-drop ALL`, `no-new-privileges` | #108 |
| Épuisement des ressources du poste | boucle, compilation, fork bomb | Plafonds du conteneur : 256 pids, 2 Go, 2 CPU ; time-out par tâche (#64). ⚠ Le **plafond de tours ne compte plus** parmi les parades : #494 lui a retiré son défaut, aucun agent n'est plus borné sauf `plafond_tours` posé explicitement. Ce qui reste opposable à une boucle est donc ce que le conteneur borne — et, hors mode isolé, seuls le time-out par tâche et un plafond de dépense armé au lancement | #108 |
| Vol de secrets d'un autre agent | agent compromis résolvant `${VAR}` d'autrui | Coffre **par agent** : la résolution MCP ne lit que le coffre de l'agent exécutant ; secret absent = serveur indisponible (échec propre), même si la variable existe dans le process | #109 |
| Fuite de secret en sortie | agent citant son token dans un livrable, trace, rapport | Registre de rédaction : toute valeur **servie** est masquée (`[secret masqué]`) à la consignation — le journal alimentant Langfuse, le pont Control Tower et les rapports, le masquage suit partout | #109 (socle #8) |
| Lecture de l'environnement hôte depuis le conteneur | code exécuté dans le conteneur | Environnement minimal : seules les 3 variables d'auth fournisseur entrent (`ENV_TRANSMISES`) ; les secrets MCP voyagent résolus en mémoire, jamais dans l'environnement du conteneur | #108/#109 |
| Action interdite via un outil | appel d'outil intégré ou MCP hors mandat | Politique **allow/deny par agent et par outil** : outils refusés retirés de la session, serveur MCP refusé jamais monté (secrets jamais résolus), le reste refusé **au vol** (hook PreToolUse) avec motif — violation tracée (`:refus-outil`), jamais fatale au run | #110 |
| Config MCP ambiante montée à l'insu | config utilisateur/projet/plugin du CLI | `strict_mcp_config` : la session ne monte que la liste déclarée de l'agent | #104 |
| **Serveur MCP arbitraire monté depuis un catalogue** | entrée de bibliothèque non curée, paquet typosquatté, annuaire tiers | **Allowlist** : `RegistreMcp.instancier` est l'unique voie template → liaison et n'accepte que l'allowlist ; `POST /api/mcp/pool` refuse avant elle. *Découverte ≠ installation* — **§2.3** | #131/#271/#678 |
| Secret en clair dans le dépôt Git | déclaration MCP ou politique versionnée | Déclarations à références `${VAR}` seulement, littéraux masqués (`•••`) dans la forme publique ; `core/secrets/*` gitignoré ; politiques sans secret par construction | #104/#109 |

Les trois mécanismes sont **cumulatifs et indépendants** : chacun s'active
seul, la défense en profondeur vient de leur empilement (une politique d'outils
limite ce que l'agent *demande*, l'isolation limite ce que le code *fait*, le
coffre limite ce que chacun *voit*).

### 2.1 Ce que l'ouverture aux projets locaux ajoute *(en vigueur — [docs/24 §2.5](./24-projets-locaux-et-poste-de-travail.md), **Phase 7** livrée)*

Le modèle ci-dessus reposait sur une hypothèse forte : **les agents n'ont rien à faire hors de
leur workspace jetable**. La Phase 7 la lève — un projet de l'utilisateur, désigné par sa
racine, est lisible et modifiable. L'actif « poste hôte » (§1) a donc un voisin : **le
projet de l'utilisateur**, avec quatre menaces propres :

| Menace | Vecteur | Contre-mesure |
|---|---|---|
| Destruction du travail de l'utilisateur | agent défaillant, `Bash` mal formé, code produit | Travail hors de la racine (branche/worktree ou copie) ; **application sous validation humaine** (EF-37) ; retour arrière natif si le projet est versionné |
| Évasion par la racine déclarée | `../..`, lien symbolique, chemin absolu | Racine **canonicalisée**, écriture refusée au-dessus ; **liste de racines interdites** (racine de disque, dossier utilisateur nu, `.ssh`, `AppData`, le dépôt Maestro) |
| Exfiltration du code de l'utilisateur | `git push` vers un distant tiers, appel réseau d'un `Bash` permis | Politique d'outils par agent (#110) ; l'**égress non filtré** (§5) devient nettement plus gênant qu'aujourd'hui |
| **Prompt injection par le contenu lu** | `README`, commentaire, dépendance, **document téléversé** ([docs/24 §3.4](./24-projets-locaux-et-poste-de-travail.md)) | Contenu traité comme **donnée, jamais comme consigne** (prompts systèmes) ; actions sensibles maintenues derrière la validation, ce qui borne les dégâts. **Élargi et outillé en Phase 8 — §2.2** |

La décision **D1** de [docs/24 §8](./24-projets-locaux-et-poste-de-travail.md) a été rendue le
2026-08-04 (#218) et la **Phase 7 a livré** : ce tableau décrit le modèle de menace **en
vigueur**, et non plus ce qui l'attend. Où chaque contre-mesure vit dans le code :

- **travail hors de la racine** — `maestro.sandbox.projet` (#224) dérive l'espace de travail :
  worktree Git sur la branche `maestro/<tâche>` si le projet est versionné, **fusionnée dans la
  base dès que la tâche est soldée** (#705). La racine d'un projet **versionné** n'est jamais le
  répertoire de travail d'un agent (EF-36), ni un montage du conteneur en mode isolé (#226,
  [docs/17 §3](./17-isolation-execution.md)). Un projet **non versionné** se remplit **en place**
  (#839, D2 révisée — [docs/24 §2.4](./24-projets-locaux-et-poste-de-travail.md)) : une tâche à
  la fois, derrière la frontière d'écriture de `maestro.sandbox.en_place` (hors racine, lien
  symbolique, exclusion du périmètre — refusés avec leur motif), et en mode isolé sa racine est
  montée **avec ses masques** ;
- **application sous accord humain** — `maestro.controltower.validation.appliquer_sous_validation`
  (#227, EF-37) soumet « appliquer ce travail ? » au **même** validateur que les autres actions
  sensibles (EF-08), diff en pièce jointe ; depuis #706 la fusion continue d'un run passe par ce
  validateur **une fois par run et par projet**, à la première fusion
  (`LocalExecutor._accord_de_fusion`). Sur refus, rien n'est écrit et le travail reste
  consultable sur sa branche ; sans validateur, l'application est refusée (fail-safe des
  garde-fous, #9) ;
- **racine canonicalisée et racines interdites** — `maestro.projets.racine` (#221) : `..` écrasés
  et liens résolus **avant** toute comparaison, refus **motivé** (jamais un `False` muet), et
  `chemin_dans_racine` par où passe toute écriture. La même frontière borne l'explorateur de
  l'API (#223), pour qu'une zone interdite à la déclaration ne devienne pas lisible par ailleurs ;
- **exclusions du périmètre** — les gisements de secrets (`.env`, `**/secrets/**`, `.git`,
  `node_modules`) sont écartés de l'espace de travail et masqués dans le conteneur, et
  `maestro.projets.secrets` fait couvrir par la rédaction (#109) les valeurs lues dans le projet
  de l'utilisateur — pas seulement celles de Maestro.

Deux réserves demeurent, inchangées : l'**égress n'est toujours pas filtré par domaine** (§5) —
la Phase 7 rend cette limite nettement plus gênante sans la traiter —, et le verdict de
`chemin_dans_racine` porte sur l'état du disque **au moment de l'appel** (TOCTOU) : refermer
cette fenêtre revient à qui *ouvre* le fichier, pas à qui calcule le chemin.

### 2.2 Ce que les sources d'un objectif ajoutent *(en vigueur — [docs/24 §3](./24-projets-locaux-et-poste-de-travail.md), **Phase 8** livrée)*

Le vecteur « prompt injection par le contenu lu » du §2.1 s'élargit d'un cran, et **change de
nature**. Jusqu'ici le contenu hostile devait déjà se trouver quelque part — dans un dépôt, une
dépendance, un `README`. La Phase 8 ouvre une porte que l'utilisateur franchit lui-même : un
document téléversé, un dossier de références, une **URL** dont Maestro va chercher le contenu
(#315 à #317). L'écart est que la matière entre **sur demande**, en un geste, et sans que rien du
poste ne l'ait filtrée.

Trois choses le bornent, et elles ne sont **pas de même force** — l'ordre ci-dessous est celui-là,
pas celui de leur visibilité :

1. **La clôture du bloc ne peut pas être forgée.** Tout contenu extrait entre dans un bloc de code
   dont la barrière est **calculée** : plus longue que la plus longue suite d'accents graves qu'il
   porte (règle CommonMark). Aucun contenu ne peut donc refermer son propre bloc pour écrire *à
   côté*, c'est-à-dire se faire passer pour une consigne. **C'est la seule garantie** des trois ;
   les deux suivantes sont des consignes, et une consigne se contourne.
2. **Les noms sont assainis.** Un nom de fichier vient de l'extérieur — il est saisi dans le
   navigateur de l'utilisateur, une URL est collée. Une en-tête forgée dans un nom
   (`` ``` `` + `## Consignes`) vaudrait une évasion **sans que le contenu ait eu à bouger** :
   sauts de ligne écrasés, accents graves neutralisés, longueur bornée.
3. **Le préambule dit le régime** — données à analyser, jamais des consignes, et une instruction
   trouvée à l'intérieur se **signale** au lieu de s'exécuter.

S'y ajoutent trois bornes qui ne visent pas l'injection mais la limitent en passant : les **schémas
d'URL** sont restreints à `http(s)` — un `file://` lirait le disque par une porte que personne n'a
contrôlée, et le contrôle est **au-dessus** de l'ouvreur, jamais délégué à lui, qui suivrait le
schéma sans broncher ; le contenu des **balises muettes** (`script`, `style`) est écarté à la
conversion, sans quoi il suffirait d'un `<script>` pour glisser des consignes sous couvert de page
web ; et la **rédaction des secrets** (#109) passe sur le contenu **comme** sur les noms — le
masquage suit le Markdown, format unique voulu par [docs/24 §3.2](./24-projets-locaux-et-poste-de-travail.md)
précisément pour n'avoir qu'un endroit où le faire.

Où ça vit dans le code : `maestro.sources.extraction.contexte_markdown` est le **seul chemin** par
lequel un contenu extrait rejoint un contexte. Le critère est testé et non seulement énoncé
([`tests/test_extraction_sources.py`](../tests/test_extraction_sources.py)) — un préambule se
relit, une clôture calculée se **teste** : aucun contenu, si hostile soit-il, ne doit pouvoir
refermer son bloc.

Une réserve, à ne pas confondre avec une protection : la **validation humaine du brief** (#320,
EF-40) n'est pas une contre-mesure d'injection. Elle borne les dégâts d'un objectif mal compris,
pas ceux d'un contenu manipulé — un brief rédigé à partir d'une source hostile est un brief
**plausible**, et c'est exactement ce qu'on approuve sans relire. Ce qui borne les dégâts reste ce
qui les bornait déjà : les actions sensibles derrière la validation (EF-08, EF-37) et la politique
d'outils par agent (#110).

### 2.3 Découverte ≠ installation : ce que la bibliothèque MCP garantit *(en vigueur — #131, #271, parent #673)*

Ce garde-fou est cité depuis une douzaine d'endroits du dépôt sous la forme
« voir docs/19 » ; il n'y était pas écrit. Le voici, avec ce que la **fédération**
du parent #673 y change — et ce qu'elle n'y change pas.

**La règle, en une phrase.** Un serveur MCP n'est montable que s'il appartient à
l'**allowlist**. `RegistreMcp.instancier` en est l'unique voie (template →
liaison) et `POST /api/mcp/pool` refuse **avant** elle, avec la même phrase, pour
que deux formulations d'un même refus ne divergent pas. Figurer dans la
bibliothèque ne configure rien : le parcours reste bibliothèque → pool (geste
humain, secret saisi) → activation par agent.

**Ce que la fédération change.** Jusqu'à #673 l'allowlist portait deux rôles —
« ce qu'on connaît » et « ce qu'on autorise » —, et la découverte se limitait donc
à 29 entrées écrites à la main. Depuis, la bibliothèque **découvre** dans un
miroir du registre MCP officiel ([docs/21
§3.5](./21-configuration-mcp.md)) : des milliers d'entrées, visibles et
cherchables, **dont aucune n'est montable**. Le garde-fou n'est pas levé, il
devient **exact** — l'allowlist ne garde qu'un rôle, autoriser.

**Ce que le registre officiel prouve, et ce qu'il ne prouve pas.** C'est le point
que ce paragraphe existe pour dire, parce qu'il est facile de lire un catalogue
officiel comme une caution :

- il **vérifie la propriété du namespace** de l'éditeur — `io.github.<compte>` par
  OAuth GitHub, `com.exemple` par preuve DNS ou HTTP sur le domaine —, et il
  publie une **version épinglée** avec l'enregistrement ;
- il **ne vérifie pas la sûreté** : aucun scan, aucun audit, aucune caution sur
  le code. Il dit « ce serveur existe », **jamais** « ce serveur est sûr ». Sa
  modération retire *a posteriori* (spam, malware, illégal), ce qui est autre
  chose qu'un contrôle *a priori*.

La seconde question reste donc entièrement la nôtre, et c'est la **porte
d'admission** ([docs/21 §3.6](./21-configuration-mcp.md)) qui y répond : un geste
humain tracé (qui, quand, quelle version, quelle source) fait entrer une entrée
découverte dans l'allowlist, en **figeant** la version admise. Une organisation
qui veut y brancher sa revue ou son scan le fait par la politique d'admission,
qui est le seul point d'extension prévu — le défaut du dépôt accepte tout,
c'est-à-dire que le geste humain **est** la politique par défaut.

**Ce que la fédération ne change pas — et pourquoi la règle de curation tient
toujours.** [docs/21 §3.4](./21-configuration-mcp.md) interdit d'écrire un
`npx -y <paquet>` **de mémoire** : c'est une invitation au typosquatting dans une
allowlist. Le motif ne disparaît pas, il **cesse de s'appliquer** quand
l'identifiant de paquet ne vient plus d'une mémoire mais d'un enregistrement
d'éditeur au namespace vérifié, à version épinglée — et l'admission en garde la
source pour qu'on puisse toujours revenir la vérifier. Fédérer n'affaiblit pas la
curation : c'est ce qui en fait, pour la première fois, une curation **sourcée**.

**Trois garde-fous de détail, qui sont des décisions et non des effets de bord :**

- une entrée **`deleted` chez l'amont** (retirée par la modération) **sort** du
  miroir et n'est jamais admissible ; une entrée `deprecated` reste visible,
  **signalée** ;
- une entrée dont une variable vit en **argv** est refusée — `maestro.agents.mcp.resolus`
  ne substitue les `${VAR}` que dans `env` et `headers`, donc elle démarrerait sur
  la chaîne littérale : un refus nommé plutôt qu'un serveur monté de travers ;
- une **révocation ne démonte rien**. L'entrée sort de l'allowlist (elle n'est
  plus instanciable, et le refus **nomme** la révocation), mais un serveur déjà
  dans le pool y reste, avec son alerte — couper un run en cours pour appliquer
  une décision d'allowlist serait un remède pire que le mal. Ce qui est promis
  est « jamais sans le dire », pas « jamais sans casser ».

**Limite assumée.** L'admission autorise un **gabarit**, pas un comportement :
rien ici n'inspecte ce que le serveur fait une fois monté. Ce qui borne cela est
ailleurs et n'a pas bougé — la politique d'outils par agent (#110), le coffre par
agent (#109) et l'isolation d'exécution (#108).

### 2.4 Ce que la fenêtre de bureau ajoute *(en vigueur — [docs/35 §2](./35-decision-poste-de-bureau-et-disposition.md), chantier #921)*

Ce modèle a été écrit pour un backend servi à un **navigateur**, et jusqu'à #923 il n'avait
aucune raison de nommer une fenêtre : celle qui affichait la Control Tower appartenait à
quelqu'un d'autre — Chrome, Edge, Firefox. Ce tiers appliquait sa politique d'origine, son bac à
sable de rendu, ses règles de téléchargement et d'ouverture de protocole. Nous en étions les
bénéficiaires **sans l'avoir écrit**.

La coque de bureau déplace cette frontière, et le dire franchement est le premier travail de
cette section : **le tiers de confiance, c'est nous maintenant**. `apps/desktop/main.js` est un
processus Node complet, lancé avec les droits de l'utilisateur, qui démarre la stack locale et
affiche ce qu'elle sert. Ce qui sépare la page du poste n'est plus la politique d'un navigateur
tiers : c'est ce que ce fichier-là accepte de faire.

**Aucun actif nouveau pour autant** (§1) : la fenêtre n'ouvre aucune donnée que le poste hôte et
le projet de l'utilisateur (§2.1) ne portaient déjà. Ce qu'elle ajoute, ce sont des **surfaces**
sur ces actifs, et un attaquant qui n'était pas dans la liste du §2 — **du code exécuté dans la
page**. Ce n'est pas une hypothèse d'école : la page affiche du contenu de projet, des livrables
de run et des sources ingérées, c'est-à-dire exactement la matière que §2.1 et §2.2 tiennent pour
hostile par défaut.

#### La surface, ligne à ligne

| Surface | Ce qui la borne | Ce qui reste assumé |
|---|---|---|
| **Processus principal** — Node complet, droits de l'utilisateur (`apps/desktop/main.js`) | Il n'exécute **qu'un** programme, `scripts/controltower/start.sh`, jamais une commande venue de la page ; ce qu'il lui transmet est une **liste blanche** d'options (`--demo`), le reste de son `argv` — celui d'Electron — étant écarté ; l'interpréteur est celui que le lanceur lui passe (`MAESTRO_BASH`), pas un `bash` nu qui sous Windows tomberait sur WSL ; une seconde instance sort immédiatement (`requestSingleInstanceLock`) | Ce processus **a tous les droits de l'utilisateur**, et rien ne l'en prive : ni conteneur, ni jeton, ni politique d'outils. C'est lui la frontière — il ne peut pas être derrière elle. Le lanceur est joué depuis la racine du dépôt (`cwd`) et hérite de l'environnement de la coque tel quel |
| **Injection dans la page** — `executeJavaScript` (écran d'attente) | La seule charge injectée est un `JSON.stringify({ etat, message })` : un **littéral**, jamais du code ; la page ne l'affiche que par `textContent` (`attente.html`), et l'appel devient sans effet dès que l'UI a pris la place de l'écran d'attente | Ce message porte la **sortie de `start.sh`** — du texte produit par le poste, pas par nous. Ce qui le borne n'est donc pas la confiance qu'on lui fait, ce sont la sérialisation et le `textContent` : les deux doivent le rester |
| **Préchargement — le pont** (`apps/desktop/preload.js`) | `sandbox: true` et `contextIsolation: true` : le préchargement n'a **pas** accès à Node (ni `fs`, ni `child_process`, ni `shell`), il ne peut donc pas donner ce qu'il n'a pas. Ce qu'il expose est **trois fonctions nommées par ce qu'elles font**, jamais `ipcRenderer` ni un `invoke(canal, …)` générique, qui rouvrirait tout le pont derrière un nom neutre | Ces trois verbes sont atteignables par **tout** ce qui s'exécute dans la page — y compris un script qu'un contenu affiché aurait réussi à y faire entrer. Ce qui les borne n'est donc pas **qui** appelle, mais ce que le processus principal accepte de faire (lignes suivantes). Le pont est attaché à la **fenêtre**, pas à une origine : l'écran d'attente (`attente.html`, chargé en `file://`) le porte aussi |
| **IPC** — deux canaux | `ipcMain.handle` sur `maestro:ouvrir-dossier` et `maestro:choisir-dossier`, **nommés**, et rien d'autre ; chaque argument est **revalidé** côté processus principal, sans faire confiance au typage du preload | L'**émetteur n'est pas vérifié** (`senderFrame`). Ce qui rend cela tenable est une hypothèse à quatre termes — une seule fenêtre, une seule origine, aucun `<webview>`, aucune iframe tierce dans le front — et non un contrôle. Elle tombe le jour où l'un des quatre change : c'est alors qu'il faudra filtrer l'émetteur |
| **Navigation et ouverture de fenêtres** | `will-navigate` n'autorise que les deux noms de l'hôte local **sur le port de l'UI** ; `setWindowOpenHandler` rend `deny` sans exception — jamais de seconde fenêtre de coque ; ce qui est refusé part au navigateur du système **si et seulement si** son schéma est `http(s)`, tout autre étant refusé net | Une URL externe en `http(s)` s'ouvre **sans confirmation**, et rien ne distingue un clic d'une navigation déclenchée par du script. C'est un canal de sortie *visible* (une fenêtre s'ouvre) mais réel — une URL porte ce qu'on met dans sa requête —, et il relève de la même limite que l'**égress non filtré** (§5) |
| **Contenu distant affiché** — aucun | La fenêtre ne charge que l'origine locale ; `webviewTag: false` ; le contenu extérieur qui entre dans le produit (document, URL — §2.2) entre par l'**API**, converti en Markdown, et le front ne pose aucun HTML brut (`apps/web/lib/markdown.ts`). Il n'est donc jamais *rendu* comme une page | **Aucune CSP** n'est posée sur l'origine locale : la fenêtre n'en pose pas plus que l'onglet, et lui en poser une ferait diverger les deux régimes sans qu'ENF-12 l'appelle. Une ressource distante demandée par la page (police, image) se charge — dans la fenêtre comme dans l'onglet |
| **Ouvrir un chemin** (#928) — `shell.openPath` | Refusé par défaut, puis trois gardes : chaîne non vide, chemin **absolu**, **existant**, et **répertoire**. La dernière est celle qui porte la menace — `openPath` sur un `.exe`, un `.bat` ou un `.lnk` l'**exécuterait** avec les droits de l'utilisateur. Le pont n'ouvre donc que des répertoires, ce qui suffit au livrable d'un run et ne lance rien | **N'importe quel** répertoire du poste, pas seulement celui d'un projet déclaré : la coque ne connaît pas les racines interdites, et c'est voulu (porte unique, ci-dessous). La garde a la même fenêtre **TOCTOU** que `chemin_dans_racine` (§2.1) — entre le contrôle et l'ouverture, un lien peut changer. Ce qu'on accepte ici et nulle part ailleurs, c'est que le dégât borné soit « un dossier s'affiche dans l'explorateur » |
| **Désigner un dossier** (#938) — dialogue natif, dossier déposé | Les deux ne font que **rendre une chaîne**. Le dialogue est **modal** sur la fenêtre, donc il ne s'empile pas — là où celui du backend a dû se donner un verrou, N requêtes HTTP pouvant empiler N fenêtres (#278) ; `webUtils.getPathForFile` s'exécute dans le rendu et rend `null` pour ce qui ne vient pas du disque | La coque lit ainsi le chemin de n'importe quel dossier, **y compris une racine interdite** — et c'est correct : lire un chemin n'est pas y accéder. Le refus arrive à la **porte**, avec son motif (§2.1) |

#### Les réglages de sûreté, rattachés à la menace qu'ils traitent

#923 en nomme **trois** dans son critère d'acceptation — `nodeIntegration` désactivé,
`contextIsolation` activé, origine locale seule — et en pose deux de plus au passage. Ils sont
justes ; ce sont des **critères d'acceptation**, pas un modèle de menace, et une liste de bonnes
pratiques dit ce qu'on configure, jamais **contre quoi**. Chacun a sa menace, et chacun laisse
quelque chose derrière lui :

| Réglage (`main.js`) | La menace qu'il traite | Ce qu'il ne traite pas |
|---|---|---|
| `nodeIntegration: false` | Du code exécuté dans la page obtiendrait `require('fs')` et `child_process` : lecture et écriture du poste avec les droits de l'utilisateur, **sans passer par aucun pont** | Ce que le pont expose volontairement. Le réglage borne l'**implicite**, jamais l'explicite |
| `contextIsolation: true` | Le monde du préchargement et celui de la page partageraient leurs prototypes : un script de la page pourrait **remplacer** ce dont le pont se sert et détourner un appel légitime | Une fonction exposée reste **appelable** : le réglage garantit qu'elle n'est pas remplaçable, pas qu'elle est réservée |
| `sandbox: true` *(posé par #923, hors de son critère ; **maintenu** par #928 quand le pont est né)* | Un préchargement non sandboxé garde Node : sa seule existence remettrait dans le processus de rendu ce que `nodeIntegration: false` venait d'en retirer | Rien de plus — c'est lui qui rend les deux précédents cohérents une fois qu'un pont existe, et c'est pourquoi l'ouvrir « pour faire passer » une capacité les annulerait tous les trois |
| `webviewTag: false` *(idem, hors critère)* | Une balise `<webview>` créerait un contenu embarqué avec **ses propres** réglages, hors de ceux-ci : c'est la faille par le bas d'une politique de fenêtre | L'iframe ordinaire, que rien n'interdit côté coque. Qu'il n'y en ait aucune est une propriété du **front**, pas de la fenêtre |
| **origine locale seule** (navigation) | Une page distante chargée **dans** la fenêtre s'exécuterait devant le pont, avec la même adresse que le produit | Ce que la page locale, elle, va chercher (ligne « contenu distant » ci-dessus) |

#### La porte unique : un chemin qui entre par la fenêtre est jugé au même endroit que les autres

**C'est une propriété du modèle, pas la note d'un lot** — #938 l'écrit pour lui-même, elle vaut
pour tout ce qui viendra ensuite. Un chemin peut désormais entrer par cinq portes : saisi au
clavier, choisi dans l'explorateur servi par l'API (#223), rendu par le dialogue que le backend
ouvre (#278), rendu par le dialogue de la fenêtre (#938), lu sur un dossier déposé (#938). Toutes
aboutissent à `valider_racine` **côté backend** (EF-38, #221) — canonicalisation, racines
interdites, refus motivé — et à lui seul.

La coque n'en applique **aucune**, et ne doit jamais en appliquer : deux formules à tenir d'accord
ne restent pas d'accord, et c'est la garde qui perdrait. Le corollaire est ce qui rend la règle
utilisable pour la capacité suivante, celle que personne n'a encore écrite :

- une capacité qui **rend** un chemin n'ajoute **aucune garde** à écrire. Elle allonge la liste
  des portes ; la porte, elle, est ailleurs ;
- une capacité qui **agit** sur un chemin (#928) en ajoute une — et cette garde se juge sur **ce
  que l'action peut faire**, jamais sur la provenance du chemin. C'est pourquoi la seule garde qui
  compte vraiment dans `ouvrirDossier` est « c'est un répertoire » : les deux autres évitent une
  erreur, celle-là évite une exécution.

#### L'isolation d'exécution en distribution bureau : le défaut est le mode non isolé

[docs/24 §4.6](./24-projets-locaux-et-poste-de-travail.md) l'avait écrit au cadrage, et **rien
ici ne le change** : une application qu'on double-clique ne peut pas exiger Docker sur le poste.
En distribution bureau, `MAESTRO_ISOLATION` reste **non posé** par défaut, le mode isolé
([docs/17](./17-isolation-execution.md)) demeure une option pour postes équipés, et le contrat du
conteneur ([docs/17 §3](./17-isolation-execution.md)) ne bouge pas d'une ligne — la coque ne le
touche pas.

Ce qui change, c'est **qui est devant l'écran**. Ne pas avoir Docker se constatait jusqu'ici sur
un poste de développement, par quelqu'un qui avait lancé la stack à la main et lu l'avertissement.
La fenêtre ouvre le produit à quelqu'un qui n'a pas de terminal — le persona de docs/24 —, et la
question « qu'est-ce qui protège le poste quand le conteneur n'est pas là ? » cesse d'être
théorique. La réponse, en toutes lettres :

**Le filet du mode bureau est le périmètre du projet, pas le conteneur** (docs/24 §4.6, et §2.1
ci-dessus pour le détail) : racine canonicalisée et racines interdites (EF-38), exclusions du
périmètre, frontière d'écriture de `maestro.sandbox.en_place` sur un projet non versionné, travail
hors de la racine et fusion sous accord humain sur un projet versionné (EF-36, EF-37).

Et le **prix** de cette réponse, qui ne se lit nulle part d'un seul tenant : ces gardes vivent
**sur l'hôte**, où elles ne confrontent que les **outils de fichiers** de l'agent — jamais ce
qu'un `Bash` fait ([docs/17 §4](./17-isolation-execution.md), encart #839). En mode isolé les
exclusions deviennent une clôture dure, parce que c'est le conteneur qui les porte ; hors mode
isolé — donc **par défaut en distribution bureau** — un `Bash` permis n'est borné que par la
**politique d'outils par agent** (#110) et par le time-out de la tâche (#64).

Il n'y a pas de troisième filet à inventer ici, et en suggérer un serait pire que de se taire :
refermer cela, c'est activer l'isolation sur un poste qui peut la porter, ou refuser `Bash` à
l'agent, ou ne pas lancer d'agent sur un poste où l'on n'accepte ni l'un ni l'autre. Ce document
ne dit pas que le régime est confortable ; il dit que **c'est le régime**.

#### Ce que le modèle attend du mode local durci (#638)

L'API de la Control Tower n'a **aucune authentification** et accepte toutes les origines. Ce n'est
pas la fenêtre qui creuse ce trou — il est là depuis que l'API existe, il est nommé comme bloquant
par [docs/24 §6](./24-projets-locaux-et-poste-de-travail.md) (point 3), et #638 le traite en
Phase 9. **Il ne se refait pas ici** ; deux choses s'en disent, qu'on ne voit bien qu'en regardant
la fenêtre :

- **la fenêtre n'aggrave pas ce trou et n'y donne pas accès.** Un programme du poste qui parle à
  l'API locale obtient l'API — déjà de quoi lancer un run sur le disque —, il n'obtient **pas** le
  pont : celui-ci vit dans le processus de rendu, derrière `contextBridge`, et rien du réseau n'y
  arrive. Confondre les deux surfaces ferait attendre de #638 une protection qu'il n'apporte pas ;
- **ce que #638 fermera, et ce qu'il ne fermera pas.** Un jeton et une liste d'origines ferment
  l'accès *depuis l'extérieur de la page* — une autre page du navigateur, un autre programme du
  poste. Ils ne ferment rien *à l'intérieur* : du code qui s'exécute dans la fenêtre a le jeton,
  l'origine et le pont. Ce qui borne celui-là est écrit plus haut — ce que le processus principal
  accepte de faire, et le fait que la page ne charge que l'origine locale.

**ENF-12 n'est pas en cause dans cette section** : elle décrit une surface, elle ne la change pas.
Aucun embranchement de code applicatif n'existe dans `apps/web/**` — le front teste une
**capacité** (`apps/web/lib/poste.ts`), jamais sa plateforme, et c'est ce qui fait que la même
page, servie dans un onglet, répond « non » et prend l'autre chemin.

## 3. Activation (récapitulatif)

Chaque mécanisme est **opt-in** et détaillé dans sa page ; l'ensemble tient
dans le `.env` et les dépôts `core/` :

| Mécanisme | Activation | Défaut |
|---|---|---|
| Mode isolé (#108) | `MAESTRO_ISOLATION=conteneur` (+ image construite : `docker build -t maestro-sandbox:latest infra/sandbox`) — [docs/17 §4](./17-isolation-execution.md) | exécution sur l'hôte |
| Coffre par agent (#109) | écrire le **premier** `core/secrets/<agent>.json` (bascule pour **tous** les agents) — [docs/18 §3](./18-secrets-par-agent.md) | environnement du process |
| Permissions (#110, #580) | écrire `core/permissions/<agent>.json` (`{"allow": [...], "ask": {"<outil>": "<décideur>"}, "deny": [...]}`) — **ou l'onglet MCP & permissions de la fiche agent** depuis #262, qui écrit le même fichier (`PUT /api/permissions/<agent>`, mêmes règles de validation) — [README](../core/permissions/README.md) | tout permis (outils du profil) |

Racines remplaçables (`MAESTRO_ISOLATION_*`, `MAESTRO_SECRETS_DIR`,
`MAESTRO_PERMISSIONS_DIR`) : cf. `.env.example`. En distribué (#41), moteur et
workers doivent voir les mêmes dépôts. Une config bancale casse **au câblage**
avec sa cause (mode inconnu, réseau invalide, shim introuvable) ; une politique
ou un coffre invalides sont des **échecs de tâche propres**, jamais appliqués à
moitié.

## 4. Vérification

### Tests automatisés (ce lot)

Aucun réseau, aucun démon Docker, aucun vrai fournisseur — la CI les exécute
sur `python:3.11-slim` :

- **`tests/test_permissions.py`** : sémantique allow/deny (deny prime, liste
  fermée, préfixes aux frontières `__`), validation du dépôt à la lecture,
  outils refusés retirés de la session, serveur MCP refusé jamais monté
  (secrets jamais résolus), **violation tracée** au journal (`:refus-outil`)
  sans condamner le run, application à chaud, hook PreToolUse (refus motivé,
  traçage en échec avalé) ;
- **`tests/test_secrets.py`** : validation du coffre, bascule opt-in au premier
  coffre, **scoping strict** (le non-détenteur perd le serveur même si la
  variable existe dans le process), masquage de toute valeur servie —
  jusqu'au test de bout en bout : un agent qui cite son token dans son
  compte-rendu n'atteint jamais le journal en clair ;
- **`tests/test_isolation.py`** : validation de la config au câblage, commande
  `docker run` durcie (montages, réseau, privilèges, plafonds, environnement
  minimal), smoke test du shim (protocole absent → sortie 2 ; nominal →
  commande lancée, arguments relayés, code de sortie remonté), câblage
  fournisseur (`cli_path` + protocole `MAESTRO_SANDBOX_*`).

Compléments existants : `tests/test_mcp.py` (références `${VAR}`, littéraux
masqués, `strict_mcp_config`), `tests/test_telemetry.py` et
`tests/test_engine.py` (rédaction des valeurs d'environnement et motifs de
clés au journal).

### Smoke test manuel du mode isolé (Docker requis)

Le lancement **réel** d'un conteneur exige un démon Docker, absent des runners
CI (jobs sur image `python:3.11-slim`) : le démarrage effectif se vérifie
manuellement, sur un poste avec Docker Desktop démarré —

1. construire l'image : `docker build -t maestro-sandbox:latest infra/sandbox` ;
2. renseigner l'auth par variable dans le `.env` (`ANTHROPIC_API_KEY` ou
   `CLAUDE_CODE_OAUTH_TOKEN` — l'état de connexion du poste n'est jamais monté)
   et `MAESTRO_ISOLATION=conteneur` ;
3. lancer une tâche outillée courte, par ex.
   `maestro-dev "Écris un fichier hello.txt contenant bonjour"` ;
4. vérifier : la tâche livre son fichier ; pendant l'exécution, `docker ps`
   montre un conteneur `maestro-sandbox` ; après, `docker ps -a` n'en garde
   aucun (`--rm`) ;
5. contre-épreuve (échec propre attendu) : arrêter Docker et relancer — la
   tâche échoue, cause consignée au journal, le run n'emporte pas le moteur.

## 5. Limites connues (consolidées)

Chaque page de lot garde le détail ; l'essentiel, assumé au POC :

- **Égress non filtré par domaine** en mode isolé (`bridge` sort partout — il
  faut au minimum l'API du fournisseur) ; le filtrage fin reste une évolution
  (docs/17 §5). La politique #110 borne les *outils*, pas les destinations
  réseau d'un `Bash` permis ;
- **le mode non isolé reste le défaut** : sans `MAESTRO_ISOLATION`, l'isolation
  se limite au workspace jetable et à la restriction d'outils ;
- **coffre en clair sur disque** (fichier local hors Git) — Vault/SOPS en V1
  sans changer le contrat (docs/18 §4) ; les clés fournisseur restent portées
  par la config du process, pas par le coffre ;
- **micro-VM (gVisor/Firecracker) non retenue** sur le poste Windows (Docker
  Desktop fournit déjà la frontière WSL2) — piste réévaluée pour un déploiement
  serveur Linux (docs/17 §1) ;
- **la rédaction est par valeur exacte ou motif** : un secret *transformé* par
  l'agent (base64, découpé) échapperait au masquage — c'est la politique
  d'outils et le scoping qui réduisent ce risque à la source ;
- **smoke test conteneur hors CI** (démon Docker indisponible sur les runners) :
  procédure manuelle ci-dessus, à rejouer quand `infra/sandbox/` ou
  `maestro/sandbox/` changent ;
- **la coque de bureau n'est pas isolée, et ne peut pas l'être** (§2.4) : son
  processus principal lance la stack avec les droits de l'utilisateur, parce que
  c'est lui qui porte la frontière de la fenêtre. En distribution bureau le défaut
  reste le **mode non isolé**, le filet est le **périmètre du projet**, et un
  `Bash` permis n'y est borné que par la politique d'outils (#110) ;
- **le pont de la fenêtre n'authentifie pas son appelant** (§2.4) : ses deux canaux
  IPC servent tout ce qui s'exécute dans la page. Ce qui rend cela tenable est une
  hypothèse à quatre termes — une fenêtre, une origine, pas de `<webview>`, pas
  d'iframe tierce — et non un contrôle ;
- **l'API locale n'a ni jeton ni liste d'origines** (#638, Phase 9) : indépendant
  de la fenêtre, mais c'est en distribution bureau que ce défaut cesse d'être une
  commodité de développement (§2.4).
