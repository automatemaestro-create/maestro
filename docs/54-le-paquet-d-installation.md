<!-- documentation: produit -->
# 54 — Le paquet d'installation : Maestro sans Python, sans Node et sans clone

> Ticket #641, lot de #637 (Phase 9, « Maestro s'installe sur un poste »). Première plateforme :
> **Windows 10/11 x64**. macOS et Linux ne sont pas construits : l'assembleur refuse de le faire
> plutôt que de produire un paquet faux.

## 1. Ce qu'on installe

Un **installeur Windows** (`Maestro-<version>-installation-x64.exe`). Un double-clic l'installe
pour la personne qui le lance, sans droit d'administrateur, sous
`%LOCALAPPDATA%\Programs\Maestro`, avec un raccourci sur le bureau et dans le menu Démarrer. Il se
désinstalle par les Paramètres de Windows, comme toute application.

Ouvrir Maestro ouvre **sa fenêtre** : la coque démarre l'API et l'interface, les sert dans la
fenêtre, et les arrête quand on la ferme — en soldant les runs en vol (docs/35 §2, #640). Rien
d'autre n'est à installer pour que Maestro démarre : ni Python, ni Node, ni Redis, ni le dépôt.

Ce que l'installation contient :

| Partie | Ce que c'est |
|---|---|
| La coque | Electron, la fenêtre de Maestro (docs/35 §2) |
| Le Python du produit | le CPython officiel de python.org, isolé du poste, et les dépendances de Maestro |
| Le CLI `claude` | celui que le SDK d'agent embarque — le produit le trouve dans son paquet |
| Le Node du produit | il sert l'interface, construite d'avance |
| Maestro | son moteur, son API, la configuration de ses agents, sa documentation |

Ce qu'elle écrit en marchant :

- l'**état du poste** sous `%USERPROFILE%\.maestro\` — le jeton de l'API (`jeton-api`), propre à
  ce poste et créé au premier démarrage, le journal durable des runs en SQLite (le **mode local**,
  #639 : aucun service à installer) ;
- **ce que la personne y met**, sous `%USERPROFILE%\.maestro\configuration\` : les projets
  déclarés, les réglages, les secrets, les surcharges, les conversations, les playbooks, les
  permissions, les serveurs MCP ajoutés, et ses propres réglages d'environnement
  (`configuration\.env`) ;
- les **journaux du lanceur** sous `%USERPROFILE%\.maestro\lanceur\`, avec la cause d'un service
  qui serait mort au démarrage ;
- les **projets** sous `%USERPROFILE%\Maestro\` (docs/24 §2.7) ;
- l'**installeur de la version suivante**, que la mise à jour télécharge, sous
  `%LOCALAPPDATA%\maestro-updater\` : il y attend d'être posé, et part avec la désinstallation
  (§6).

Le **dossier d'installation ne porte que le paquet**. Les politiques et les serveurs livrés avec
les agents s'y lisent toujours (`resources\produit\core\`) ; ce que la personne y change s'écrit
sous `configuration\` et les recouvre, sans les toucher. Réinstaller, mettre à jour ou désinstaller
remplace ou retire ce dossier sans rien perdre de ce qui précède (§3.3.3). Seul le cache de
compilation de Python (`__pycache__`) y naît en marchant : l'interpréteur le rebâtit à la demande.

L'installation se sait installée par son réglage `MAESTRO_INSTALLATION=1`, que le paquet pose dans
`resources\produit\.env`. C'est le **seul** signal (#1633). Il range ce que la personne y met. Il
donne aussi au produit la forme que *Paramètres › Général* montre (`GET /api/installation`, docs/05
§6.4sexies), avec le geste proposé et la source de la version. Un `.git` présent ou absent n'en
décide pas : une archive du dépôt reste un clone, et une installation où un `.git` traînerait reste
une installation. Sans le réglage — un clone du dépôt —, tout reste où il était, sous `core\`. Une
autre valeur que `1` est refusée avec son motif par le lanceur et par chaque dépôt : elle ne
devient ni l'une ni l'autre forme.

Les ports par défaut sont `8000` (l'API) et `3000` (l'interface). L'un ou l'autre pris, le lanceur
prend le premier libre au-dessus et le dit dans son journal ; l'interface reçoit l'adresse de
l'API **au démarrage**, jamais au moment où le paquet a été construit.

## 2. Ce que l'installation n'apporte pas

Nommé ici plutôt que découvert à l'usage :

- **Un compte pour le modèle.** Le CLI `claude` est livré, son compte non : une session d'agent
  demande qu'on se connecte (`claude auth login`, ou une clé d'API). Le premier lancement qui le
  configure de lui-même est #642.
- **Git for Windows.** Un projet versionné en a besoin pour ses branches et ses diffs, et
  l'installation ne le pose pas. Le CLI `claude`, lui, a démarré et répondu au SDK avec Git hors
  du `PATH` (§3.3.2) ; qu'il démarre sur un poste où Git n'est installé nulle part n'est pas
  vérifié (§3.4).
- **Docker.** Le mode isolé par conteneur (docs/17) reste une option pour les postes qui l'ont. Le
  défaut d'une distribution bureau est le mode **non isolé** — sous le confinement de l'arbre des
  processus, qui est livré (`maestro-confinement`, #1279). L'isolation native par défaut est
  décidée (docs/48 §2.1) et vient avec #1515, qui la pose par les réglages de l'installation
  (`resources\produit\.env`) : ce paquet ne la change pas. Ces réglages-là sont **ceux du
  paquet** : chaque installation les remplace, et ils portent les défauts de sa version. Ce que la
  personne règle elle-même va dans `%USERPROFILE%\.maestro\configuration\.env`, qui est lu
  d'abord, l'emporte sur eux, et qu'aucune installation ne touche.
- **Une signature de code.** L'installeur n'est pas signé : Windows SmartScreen le signale comme
  une application d'éditeur inconnu au premier lancement. La mise à jour, elle, vérifie l'empreinte
  de ce qu'elle télécharge (§6.2) : elle sait que c'est l'installeur publié, pas qui l'a signé.

## 2bis. Les quatre composants : ce qui suit le paquet, et comment lire sa version

Une installation porte quatre composants dont les versions pourraient diverger (docs/24 §4.4,
§4.6). Le paquet les pose **tous les quatre ensemble**, à une seule version : celle que porte le
nom de l'installeur. Aucun n'a de mise à jour à lui : ils se mettent à jour **ensemble**, avec le
paquet (§6). Un composant peut pourtant ne pas suivre : une fenêtre de Maestro réinstallée seule devant
un produit resté à l'ancienne version, ou un CLI `claude` trouvé ailleurs que dans le paquet.
Maestro le **signale**, il ne le suppose pas (#1622).

| Composant | Suit le paquet ? | Comment lire sa version |
|---|---|---|
| **La coque et le front** | Oui. La coque est l'exécutable que le paquet installe, et le front est posé sous `resources\produit\web\` par le même assemblage que le backend : il n'a pas de version à lui. | La coque compare elle-même sa version à celle du produit qu'elle lance, à chaque démarrage (ci-dessous). Les Paramètres ne la montrent pas : l'interface ne sait pas dans quoi elle tourne (ENF-12, docs/35 §2.5). |
| **Le backend Python** | Oui. Il est posé sous `resources\produit\maestro\`, et l'assemblage écrit sa version dans `resources\produit\version.json`. | *Paramètres › Général › Version de Maestro*, que l'API sert (`GET /api/installation`, docs/05 §6.4sexies). |
| **Ses dépendances Python** | Oui. Elles sont installées à l'assemblage sous `resources\produit\runtime\python\Lib\site-packages\`, aux versions du venv où la suite passe (§3.2). | Par l'interpréteur de l'installation, paquet par paquet : `resources\produit\runtime\python\python.exe -c "import importlib.metadata as m; print(m.version('claude-agent-sdk'))"`. |
| **Le CLI `claude`** | Oui s'il est celui que le SDK embarque (`claude_agent_sdk\_bundled\claude.exe`) : il change avec la version du SDK, donc avec le paquet. Non s'il est pris sur le `PATH` du poste : il a alors son propre installeur et sa propre cadence. | *Paramètres › Général › CLI claude* : la version que le binaire dit (`claude --version`), son chemin et sa provenance. Elle se lit sur le binaire, jamais d'après celle du SDK. |

Le SDK `claude-agent-sdk` 0.2.159 embarque le CLI **2.1.281**. C'était le cas sur le paquet de §3.1
(2026-11-04), et c'est encore le cas sur le venv du dépôt le 2026-11-12.

**Ce qui se signale, et où :**

- **Une fenêtre et un produit de versions différentes.** Au démarrage, la coque lit
  `version.json` sous `resources\produit\` et le compare à sa propre version. En cas d'écart, ou si
  la version est illisible, un dialogue de la fenêtre donne les deux versions et le geste à faire :
  réinstaller Maestro, dont l'installeur pose les deux ensemble. Maestro démarre quand même :
  l'écart est un constat, pas un verrou.
- **Un CLI `claude` qui ne suit pas l'installation.** Dans *Paramètres › Général*, la ligne
  *CLI claude* le signale à l'endroit de la propriété, avec ce qu'il faut faire. Elle couvre trois
  cas : un CLI introuvable (aucun agent ne peut démarrer), un CLI pris sur le poste, et un CLI qui
  ne dit pas sa version.
- **Une API qui ne répond pas.** Les deux lignes disent « Illisible ». Une version ne s'invente
  pas.

Dans un clone du dépôt, il n'y a pas d'assemblage. La version est lue dans
`apps/desktop/package.json`, la source dont l'assemblage la tire, et le CLI embarqué est celui du
SDK installé dans `.venv`.

## 3. Ce qui a été vérifié, et comment

<!-- documentation: développement -->

### 3.1 Le poids, mesuré

Relevé le **2026-11-04** sur le paquet construit par `scripts/distribution/construire.sh`
(Electron 44.3.0, CPython 3.13.14, Node 22.23.1, `claude-agent-sdk` 0.2.159) :

| | Mesure |
|---|---|
| **Installeur** (`Maestro-0.1.0-installation-x64.exe`, NSIS compressé) | **285 Mo** (285 285 697 octets) |
| **Installé** (`win-unpacked/`) | **921 Mo** (921 081 591 octets) |
| dont la coque Electron | 385 Mo |
| dont le CLI `claude` embarqué par le SDK | 241 Mo |
| dont les autres dépendances Python | 155 Mo (dont `temporalio` 54 Mo) |
| dont `node.exe` | 87 Mo |
| dont l'interpréteur Python et sa bibliothèque standard | 23 Mo |
| dont l'interface (sortie autonome de Next) | 20 Mo |
| dont Maestro (`maestro/`, `core/`, `agents/`, `docs/`) | 11 Mo |

Ce que la mesure déplace dans docs/35 §2.3 : la coque n'est plus le poste le plus lourd du produit
installé — **le produit lui-même pèse 536 Mo**, dont près de la moitié est le CLI `claude`, qu'une
coque Tauri aurait embarqué tout autant. L'arbitrage de docs/35 §2.2 en sort renforcé : l'écart de
coque (≈ 375 Mo installés) est un tiers du paquet, pas son essentiel.

### 3.2 Un runtime embarqué, et non un gel

Le ticket nommait PyInstaller « ou équivalent ». L'équivalent retenu est le **paquet embarquable
officiel de CPython** (python.org, empreinte épinglée dans `scripts/distribution/assembler.py`),
avec les dépendances installées à côté de lui aux versions du venv où la suite passe. Rien n'est
gelé, donc rien ne casse au gel : le produit se relance lui-même par `sys.executable -m …` (le
lanceur, l'hôte détaché des runs), trouve le CLI `claude` dans le paquet du SDK, et les commandes
du produit (`maestro-confinement`, `maestro-lanceur`) sont posées à côté de l'interpréteur, là où
le produit les cherche. Le fichier `python313._pth` met l'interpréteur en **mode isolé** : il
ignore `PYTHONPATH`, `PYTHONHOME` et le registre — un Python installé sur le poste ne peut rien
lui prêter, même par accident.

### 3.3 La vérification en conditions réelles

`bash scripts/distribution/verifier.sh --poste <dossier hors du clone>` installe le paquet en
silence et joue ce que ferait quelqu'un, sur un poste dont il fabrique les conditions — aucune
machine vierge n'était disponible :

- un **profil neuf** (USERPROFILE, APPDATA, LOCALAPPDATA, TEMP sous le dossier du poste) ;
- un **PATH système seul** : ni Python, ni Node, ni Git ;
- `PYTHONHOME`, `PYTHONPATH` et `NODE_PATH` **empoisonnés** — un interpréteur qui les lirait ne
  démarrerait pas ;
- une installation **hors du clone**, dans un chemin qui porte une espace ;
- des chemins de **longueur réelle** : le poste est vu par une lettre de lecteur (`subst`), si
  bien que profil, TEMP et dossier d'installation ont la longueur qu'ils ont chez quelqu'un.

Il relève ensuite **qui sert** : chaque processus né de l'installation avec le chemin de son
exécutable. Un seul pris au poste ou au clone, et la vérification échoue. Et il **compte** les
fichiers installés contre ceux du paquet, parce que l'installeur ne le fait pas (§3.3.1).

Puis il éprouve **ce qui survit** (§3.3.3) : il déclare un projet et règle le répertoire des
projets par l'API, réinstalle par-dessus, désinstalle, réinstalle, et les cherche chaque fois.
`--ancien <installeur>` installe d'abord une version antérieure, qui écrit sous son dossier : la
version vérifiée s'installe par-dessus, et doit reprendre ce que l'autre avait écrit.
`--tenir-ouvert <secondes>` laisse Maestro ouvert après la dernière réinstallation, le temps de le
regarder de ses yeux.

#### 3.3.1 Ce que la première vérification a trouvé : l'installeur perd des fichiers en silence

La première passe, jouée sans la lettre de lecteur, a installé **3 665 fichiers de moins** que le
paquet n'en porte, et l'installeur a rendu `0`. L'API est morte au premier import
(`jsonschema_specifications/schemas` absent). La cause : l'installeur NSIS extrait le paquet sous
`%TEMP%\nsXXXX.tmp\7z-out\` avant de le recopier, et ni lui ni l'interpréteur ne passent la limite
de 260 caractères de Windows sans le réglage système `LongPathsEnabled` — qu'une installation par
utilisateur ne peut pas exiger. Le profil simulé de la vérification, rangé dans un dossier de
travail profond, avait un TEMP de 144 caractères ; un fichier au-delà de 260 n'était pas refusé,
il était perdu.

Chez quelqu'un, `%TEMP%` est `C:\Users\<compte>\AppData\Local\Temp`, et la marge existe — mais
elle est mince, et une dépendance qui ajouterait un fichier plus profond la consommerait sans
bruit. D'où une **garde à l'assemblage** (`LONGUEUR_MAX` dans `scripts/distribution/assembler.py`) :
aucun chemin du paquet ne dépasse **192 caractères** depuis le dossier d'installation — le budget
que laisse un nom de compte de 20 caractères (la limite de Windows pour un compte local). Le plus
long du paquet mesuré en fait **178** (un `.proto` de `temporalio`).

#### 3.3.2 Ce que la vérification a relevé

Passe du **2026-11-04**, sur le paquet de §3.1, **tous les constats ✓** :

| Geste | Constat |
|---|---|
| Installer (`/S`, sans droit d'administrateur) | code `0` ; **9 684 fichiers sur 9 684** posés ; 921 Mo, désinstalleur compris |
| Le Python de l'installation | démarre malgré `PYTHONHOME`/`PYTHONPATH` empoisonnés, et importe `maestro` de l'installation |
| Ouvrir Maestro | l'API et l'interface répondent en **10 s** ; la page porte l'URL de l'API et le jeton **de ce profil**, reçus au démarrage ; l'API se dit « ok » sur son journal SQLite, sans aucun service |
| Qui sert | les trois exécutables — `Maestro.exe`, `python.exe`, `node.exe` — sont ceux de l'installation ; **aucun** processus ne joue un fichier du poste ou du clone |
| Le confinement | `maestro-confinement.exe` est trouvé à côté de l'interpréteur de l'installation (#1279) |
| Le CLI `claude` | le produit résout celui du paquet du SDK ; `claude --version` rend `2.1.281` ; le SDK, joué par le Python de l'installation, **le lance lui-même** et en obtient la poignée de main du protocole (`scripts/distribution/sonde_cli.py`) — avec Git hors du `PATH` |
| Fermer la fenêtre | l'API, le front et la coque s'arrêtent en **9 s**, et leurs ports sont libérés |
| Désinstaller (`/S`) | les fichiers, les raccourcis et la clé de désinstallation partent en **13 s** |

Ce qui **reste** après la désinstallation est l'état du poste, sous `%USERPROFILE%\.maestro\` : le
jeton de l'API et le journal SQLite des runs. Il appartient à la personne, pas à l'installation —
réinstaller le retrouve (§3.3.3).

Deux défauts de la vérification elle-même ont été trouvés en route, et corrigés avant cette passe :
elle fermait le relais de son propre environnement au lieu de la fenêtre de Maestro (qu'elle
cherche maintenant parmi les processus de l'installation), et elle n'attendait pas la fin du
désinstalleur, qui se recopie sous `TEMP` et rend la main aussitôt.

#### 3.3.3 Ce qui survit à une réinstallation

Passes du **2026-11-12**, sur le paquet de cette version (9 683 fichiers, 921 Mo), le poste vierge
rangé dans le dossier de travail de la session, hors du clone. L'une part d'un poste neuf ;
l'autre (`--ancien`) d'une installation 0.1.0, l'installeur construit depuis `main` avant que la
configuration ne quitte le dossier d'installation. **Tous les constats ✓** :

| Geste | Constat |
|---|---|
| Installer la 0.1.0, déclarer le projet « Verification 1620 », régler le répertoire des projets | elle les écrit **sous son dossier** : `resources\produit\core\projets\prj-….json` et `resources\produit\core\reglages\projets.json` |
| Installer la version suivante par-dessus, l'ouvrir | au premier démarrage, sans rien redéclarer, le projet est listé et le répertoire relu ; le lanceur dit « 2 fichier(s) repris » — ces deux-là seulement : les politiques et serveurs livrés de l'ancienne version, identiques à ceux du paquet, restent dans le paquet. Le témoin `reprise.json` est posé |
| Sur un poste neuf, déclarer le projet et régler le répertoire | ils s'écrivent sous `%USERPROFILE%\.maestro\configuration\`, et rien sous le dossier d'installation |
| Ouvrir puis fermer Maestro | le dossier d'installation ne porte que les fichiers du paquet, plus **216** fichiers du cache de compilation de Python (`__pycache__`) |
| Réinstaller par-dessus | **l'installeur efface le dossier d'installation** : un témoin qu'on y avait posé a disparu. Le projet et le réglage sont retrouvés |
| Désinstaller | Maestro, ses raccourcis et sa clé partent ; `configuration\` reste, fichier pour fichier |
| Réinstaller | le projet et le réglage sont encore retrouvés |

L'effacement est le geste de l'installeur d'electron-builder, qui joue le désinstalleur de la
version en place avant de poser la nouvelle : c'est lui qui emportait, jusqu'à 0.1.0, ce que
Maestro écrivait sous `resources\produit\core\`. La copie que l'installeur sauve avant lui reste
sous `%USERPROFILE%\.maestro\reprise\`, telle que l'ancienne installation l'avait laissée.

La première passe a trouvé un défaut de la vérification elle-même : elle comptait le `README.md`
et le `.gitignore` que le paquet livre sous `core\projets\` et `core\reglages\` comme des fichiers
écrits par Maestro. Elle les confronte maintenant au paquet.

### 3.4 Ce qui n'a pas été vérifié

- **Une machine réellement vierge** (une VM Windows neuve) : le poste de référence a Python, Node et
  Git installés, que la vérification masque au lieu de les retirer. Les tests différés de la
  phase (#645) sont l'endroit où le faire.
- **Une session d'agent depuis l'installation** : elle demande un compte connecté au modèle, que le
  poste n'avait pas le jour de la mesure (CLI déconnecté). Le CLI est trouvé, se lance et répond au
  SDK ; sa conversation avec le modèle n'a pas été jouée.
- **Un poste sans Git du tout** : la vérification retire Git du `PATH`, pas du disque. Le CLI
  `claude` a démarré ainsi, mais Git restait installé à son emplacement habituel.
- **Une 0.1.0 réellement distribuée** : aucune version ne l'a encore été. L'installation reprise
  par §3.3.3 est l'installeur construit depuis `main` avant ce changement, sur le même poste.
- **La reprise de chaque dépôt sur le paquet réel** : la passe déclare un projet et règle le
  répertoire des projets. Les autres dépôts (secrets et leur clé, conversations, playbooks,
  permissions, serveurs MCP, surcharges, `.env`) suivent le même chemin, et c'est
  `tests/test_reinstallation.py` qui les éprouve, pas une installation.
- **Une mise à jour depuis le vrai canal** (§6) : aucune version n'a encore été publiée, et
  `publier.sh` ne s'est joué que contre un `gh` factice. La passe de §6.4 sert le canal en HTTP
  sur le poste. La redirection de `releases/latest/download` et le transport HTTPS de GitHub n'ont
  pas été traversés.
- **« Redémarrer maintenant » sur le poste simulé** : la relance passe par l'Explorateur, donc
  par l'environnement de la session réelle (§6.4). Elle a été vue une fois, et elle s'est ouverte
  sur le profil réel du poste. Ce que la version relancée retrouve n'a été constaté que par
  l'installation à la fermeture, qui pose la même version au même endroit (§6.5).
- **Un run en cours pendant la mise à jour** : sans compte connecté au modèle, le run lancé
  échoue avant la fermeture. Son arrêt par la mise à jour suit le chemin de la fermeture
  (« arrêté avec Maestro », puis « Reprendre »), et ce chemin n'est éprouvé ici que sans run en vol.
- **Un installeur téléchargé corrompu** : le refus d'un installeur dont l'empreinte ne correspond
  pas à `latest.yml` appartient à `electron-updater`, et n'a pas été provoqué.
- **macOS et Linux** : non construits.
- **Un dossier de profil de plus de 20 caractères** (un profil de domaine `prenom.nom.DOMAINE`, un
  `%TEMP%` redirigé ailleurs) : il entame la marge de §3.3.1. Rien ne le détecte à l'installation ;
  c'est un constat à reprendre si l'installeur devait servir des postes gérés.

## 4. Construire le paquet

<!-- documentation: développement -->

```bash
bash scripts/distribution/construire.sh
```

Trois étapes, chacune refusant de continuer sur l'échec de la précédente : le front en sortie
autonome (`MAESTRO_FRONT_AUTONOME=1`, `apps/web/next.config.ts`), le produit
(`scripts/distribution/assembler.py`), le paquet (electron-builder, réglé dans
`apps/desktop/package.json`). Tout va sous `.maestro/distribution/` (gitignoré), téléchargements
d'electron-builder compris. Le jeton de l'API n'entre **jamais** dans le front : le build se fait
sans `NEXT_PUBLIC_MAESTRO_API_JETON`, l'interface le reçoit au démarrage
(`apps/web/lib/configPoste.ts`), et l'assembleur cherche octet par octet le jeton du poste qui
construit dans le front embarqué — le trouver arrête l'assemblage.

L'assemblage écrit aussi la version du paquet à la racine du produit (`version.json`, #1622). La
formule vit dans `maestro/installation.py`, qui sait aussi la relire, et un dépôt dont
`apps/desktop/package.json` ne porte pas de version arrête l'assemblage.

Les accords dont dépend l'installation — le `._pth`, les fichiers **suivis** seulement (jamais une
donnée locale de `core/`), l'emplacement du Node embarqué, le dossier du produit vu de
l'assembleur, d'electron-builder et de la coque, le fichier de version que l'API et la coque
relisent — sont gardés par `tests/test_distribution.py` et `tests/test_coque_bureau.py`.

## 5. Le journal local d'une version à l'autre

Le journal durable des runs et l'état acquis de leurs tâches vivent dans un fichier SQLite de l'état
du poste (`%USERPROFILE%\.maestro\commun.sqlite3`). Ce fichier porte le numéro de son schéma, et
chaque version de Maestro connaît le sien (#1621). À l'ouverture, Maestro lit ce numéro avant
d'écrire quoi que ce soit, puis :

| Le fichier porte… | Ce que fait Maestro |
|---|---|
| le schéma du code | il l'ouvre tel quel |
| un schéma plus ancien | il le **sauvegarde**, puis le met à niveau par des migrations ordonnées, chacune jouée une seule fois, toutes dans une seule transaction |
| un schéma plus récent | il **refuse** de l'ouvrir et n'y touche pas : ni réécrit, ni rétrogradé. L'écran dit de mettre Maestro à jour |

**La sauvegarde** passe par l'API de sauvegarde de SQLite, jamais par une copie du fichier pendant
qu'un écrivain le tient. Elle est posée à côté du fichier, sous le nom
`commun.sqlite3-sauvegarde`, et contient le fichier tel qu'il était avant la mise à niveau. Elle se
suffit à elle-même : aucun `-wal` ne l'accompagne. **Durée de conservation : une seule par
fichier, gardée jusqu'à la mise à niveau suivante de ce fichier, qui la remplace.** Un fichier
neuf, qui ne porte encore rien, n'a pas de sauvegarde.

**Une migration qui échoue** remet le fichier tel qu'il était, depuis sa sauvegarde : même
schéma, mêmes lignes, même numéro. La restauration couvre aussi une migration qui aurait validé
une partie de son travail en route, ce que l'annulation de la transaction ne rattraperait pas.
L'API démarre quand même : elle sert la panne au lieu de l'historique, l'écran affiche la cause
et ce qu'il reste possible de faire, et le journal de `maestro-api` la consigne. Relancer Maestro
retente la mise à niveau. Réinstaller la version précédente rouvre le fichier sans perte. Si la
restauration elle-même échoue, le message nomme la sauvegarde à remettre à la place du fichier,
Maestro arrêté.

Le **mode serveur** (Redis) n'a pas de schéma à migrer : il range des listes et des hachages
dont la forme est celle des événements eux-mêmes. Il est hors de ce mécanisme.

### 5.1 Changer le schéma

<!-- documentation: développement -->

Changer le schéma, c'est ajouter une marche en fin de `MIGRATIONS`
(`maestro/controltower/persistence.py`). La n-ième marche porte le fichier à la version n. Une
marche publiée ne change plus. Une marche ne fait que des `execute` dans la transaction qu'on lui
donne : un `executescript` validerait en passant ce qui précède, et une marche qui referme la
transaction compte comme un échec. Le fichier a deux écrivains, le journal et l'état acquis
(`maestro.controltower.acquis`), et les migrations se jouent dans `ouvrir_sqlite`, quel que soit
celui qui ouvre le premier. `BEGIN IMMEDIATE` prend le verrou d'écriture avant de relire le
numéro : à deux écrivains, la mise à niveau ne passe qu'une fois. Les tests jouent sur de vrais
fichiers, avec des migrations qui échouent pour de vrai : `tests/test_schema_journal_local.py`.

## 6. La mise à jour

Une installation **se met à jour seule** (#644). Il n'y a rien à télécharger à la main, ni clone,
ni environnement à gérer.

1. **Elle cherche.** À chaque ouverture de Maestro, puis toutes les six heures tant que la fenêtre
   reste ouverte, la fenêtre demande au canal des mises à jour s'il y a plus récent que sa version.
2. **Elle télécharge**, en arrière-plan, pendant que Maestro sert. Rien ne s'arrête.
3. **Elle propose.** Un dialogue de la fenêtre annonce « Maestro *x.y.z* est prête à s'installer »,
   avec deux boutons :
   - **Redémarrer maintenant** arrête Maestro comme la fermeture de sa fenêtre, pose la nouvelle
     version (l'installeur montre sa progression), puis rouvre Maestro dessus ;
   - **Plus tard**, le défaut, ne change rien. La version s'installera, en silence, la prochaine
     fois qu'on fermera Maestro, et la fois suivante il s'ouvrira dessus. Une version déjà proposée
     ne revient pas pendant la même session.

Rien ne s'installe sans l'un de ces deux gestes : ni pendant que Maestro sert, ni pendant qu'un run
travaille.

### 6.1 Ce qui est conservé, et ce qui s'interrompt

La mise à jour remplace le **dossier d'installation**, et lui seul. Tout ce que Maestro a écrit vit
ailleurs (§1), et la nouvelle version le retrouve :

- **les projets, les réglages et le reste de la configuration**, sous
  `%USERPROFILE%\.maestro\configuration\` (§3.3.3) ;
- **le journal des runs**, dont le schéma est mis à niveau par ses migrations à l'ouverture (§5).
  Une version qui en a sauté d'autres les rattrape toutes, dans l'ordre ;
- **les données d'une 0.1.0**, qui écrivait sous `resources\produit\core\`. L'installeur recopie
  l'ancien `core\` et l'ancien `.env` sous `%USERPROFILE%\.maestro\reprise\` avant d'effacer
  l'ancien dossier, puis le lanceur les range sous `configuration\` au premier démarrage. Il le fait
  une seule fois, sans doublon, et sans écraser ce qui s'y trouve déjà : un fichier déjà présent
  est conservé et nommé dans son journal, et une clé du `.env` déjà réglée n'est pas reprise.

**Un run en cours s'arrête avec Maestro**, exactement comme à la fermeture de la fenêtre. Il est
consigné « arrêté avec Maestro », ses tâches avec lui, et il se reprend après le redémarrage par
son bouton **Reprendre**. C'est pour cela que la mise à jour attend un geste. Le dialogue dit ce
coût avant qu'on choisisse.

### 6.2 Ce que la mise à jour décide

| Question | Réponse |
|---|---|
| **Le canal** | Les publications du dépôt sur GitHub. La fenêtre lit `releases/latest/download/latest.yml` : la version, le nom de l'installeur et son empreinte. Le canal est écrit dans le paquet (`resources\app-update.yml`) |
| **La cadence** | Une vérification à l'ouverture, puis toutes les six heures. Un poste hors ligne n'est pas une panne : la vérification suivante repasse |
| **L'intégrité** | L'installeur téléchargé doit porter l'empreinte **sha512** que `latest.yml` annonce, sinon il n'est pas posé. Le canal est en HTTPS. L'installeur n'est pas signé (§2) |
| **Les versions sautées** | On va toujours à la plus récente, sans passer par les intermédiaires : les migrations du journal (§5) rattrapent l'écart |
| **Le retour en arrière** | Jamais de lui-même : la mise à jour ne propose pas une version plus ancienne, ni une préversion. Pour revenir à une version précédente, on réinstalle son installeur. Un journal qu'une version plus récente a mis à niveau est alors **refusé** (§5) : sa sauvegarde, `commun.sqlite3-sauvegarde`, porte le fichier d'avant |
| **Un clone du dépôt** | Il ne se met pas à jour par ce chemin. Un clone se met à jour par git |
| **Une mise à jour qui échoue** | Si l'arrêt n'aboutit pas, la version n'est pas posée : une stack à moitié arrêtée tient encore des fichiers du dossier que l'installeur efface. La version reste téléchargée, et le prochain lancement la repropose. Tout se lit au journal de la fenêtre, sur les lignes `[mise à jour]` |

La variable `MAESTRO_MISES_A_JOUR_FLUX` remplace le canal par une autre adresse (HTTPS, ou HTTP
vers le poste lui-même) ou l'éteint (`0`). Une valeur qui ne se lit pas **éteint** la mise à jour
en le disant, plutôt que de retomber en silence sur le canal du paquet.

### 6.3 Publier une version

<!-- documentation: développement -->

Publier est un **geste humain**, jamais celui de la CI ni d'un run : ce qui part s'installe chez
chacun. La version se décide dans une Pull Request, qui change `version` dans
`apps/desktop/package.json`. Une fois cette PR mergée, on publie depuis le clone principal, sur
`main` à jour :

```bash
bash scripts/distribution/publier.sh --essai
```

```bash
bash scripts/distribution/publier.sh
```

Le script refuse (code `4`) une branche autre que `main`, un arbre sale, un HEAD qui n'est pas
`origin/main`, et une version déjà publiée : une publication ne se remplace pas. Il construit le
paquet, vérifie que `latest.yml` annonce cette version, nomme cet installeur et porte son empreinte,
puis crée la publication `v<version>` avec ces deux fichiers (`gh release create --latest`).
`--essai` joue tout sauf l'envoi. Ses refus et son envoi sont éprouvés contre un `gh` factice par
`tests/test_distribution.py`.

Le mécanisme est `electron-updater`, à version exacte dans les dépendances de la coque. C'est un
adaptateur : il lit le canal, vérifie l'empreinte et lance l'installeur. Ce qui est posé, quand,
et ce qui s'arrête avant, se décide dans `apps/desktop/main.js` :

- l'installation à la sortie du module est éteinte (`autoInstallOnAppQuit = false`), parce
  qu'elle partirait sans attendre l'arrêt de la stack ;
- « Redémarrer maintenant » passe par `app.quit()`, donc par l'arrêt de la fermeture (`--stop`),
  et la version n'est posée qu'après lui, sur un code `0` ;
- le téléchargement différentiel est éteint, parce que `releases/latest/download` ne sert pas la
  carte de blocs de la version en place.

Le cache de l'installeur porte le nom du paquet empaqueté suivi de `-updater` : electron-builder
ne le laisse pas régler autrement. Le paquet empaqueté s'appelle donc `maestro`
(`build.extraMetadata.name`), et le désinstalleur retire `%LOCALAPPDATA%\maestro-updater\`, sauf
pendant une mise à jour : l'installeur qui joue l'ancien désinstalleur vit dans ce dossier.
Ces accords sont gardés par `tests/test_coque_bureau.py` (§ « La mise à jour »).

### 6.4 Vérifier une mise à jour

<!-- documentation: développement -->

Deux paquets du même code, sous deux versions, puis la vérification du paquet sur le plus ancien :

```bash
bash scripts/distribution/construire.sh --version 0.1.1 --flux .maestro/distribution/flux
```

```bash
bash scripts/distribution/construire.sh
```

```bash
bash scripts/distribution/verifier.sh --mise-a-jour .maestro/distribution/flux
```

`--version` donne la version au produit (`version.json`) et à la coque ensemble. `--flux` recopie
`latest.yml` et l'installeur dans le dossier du canal. La phase de mise à jour de `verifier.sh`
sert ce dossier sur le poste, puis ouvre la version installée dessus. Elle lance un run, pour que le
journal porte quelque chose, et attend que la fenêtre annonce la version suivante prête. Elle lit
alors le dialogue et presse **Plus tard** d'un clic de souris. Les boutons d'un dialogue de tâches
Windows n'exposent aucun motif UI Automation : on ne peut pas les invoquer, seulement cliquer
dessus. Elle ferme ensuite Maestro et attend que la version suivante se pose, après l'arrêt et
sans relance. Enfin elle rouvre Maestro, comme la personne le ferait, et constate ce qui tourne et
ce qui a survécu. Dans les autres phases, la mise à jour est éteinte (`MAESTRO_MISES_A_JOUR_FLUX=0`).

**Redémarrer maintenant** n'y est pas joué. L'installeur relance Maestro par l'Explorateur
(`ExecShellAsUser` d'electron-builder), donc dans l'environnement de la session Windows. Chez
quelqu'un, c'est celui d'où Maestro avait été ouvert, et rien ne change. Sur le poste simulé, c'est
celui du développeur : la version relancée s'ouvrirait sur son vrai profil, hors de la vérification.
La passe du 2026-11-12 l'a montré une fois (§6.5).

### 6.5 Ce que la vérification a relevé

<!-- documentation: développement -->

Passe du **2026-11-12**, sur deux paquets du même code : 0.1.0 installé, 0.1.1 servi par le canal
local. Le poste vierge était rangé dans le dossier de travail de la session, hors du clone.
**59 constats, tous ✓**, dont ceux de §3.3 :

| Geste | Constat |
|---|---|
| Ouvrir la 0.1.0 sur le canal | elle trouve la 0.1.1, la télécharge et l'annonce prête en moins d'une minute, sans geste |
| Le dialogue | il porte « Maestro 0.1.1 est prête à s'installer. », dit qu'« un run en cours s'arrête avec lui » et propose les deux boutons |
| Presser « Plus tard » | Maestro sert toujours, en 0.1.0 : rien ne s'installe pendant qu'il sert |
| Fermer la fenêtre | la stack s'arrête, **puis** la coque pose la 0.1.1 en silence. Le journal de la coque le dit dans cet ordre, et l'installeur a fini en **167 s**. Rien ne se relance |
| Rouvrir Maestro | la 0.1.1 répond. La coque (`Maestro.exe` 0.1.1.0), le produit (`/api/installation` : `0.1.1`) et le CLI `claude` (celui du paquet) sont ceux de la nouvelle version, et chaque processus joue un exécutable de l'installation |
| Ce que la personne avait | le projet et le répertoire des projets sont relus. Les 4 événements du journal ont la même empreinte avant et après, et le run d'avant est retrouvé |
| Désinstaller | le cache `maestro-updater` part avec Maestro |

Une première passe, le même jour, a joué **Redémarrer maintenant**. Le clic a été donné à la main :
le script cherchait des boutons UI Automation, que ce dialogue n'expose pas. La coque a arrêté la
stack, puis lancé l'installeur avec `--updated --force-run`. La 0.1.1 s'est posée, et `Maestro.exe`
0.1.1 s'est relancé de lui-même un peu plus d'une minute après le clic, avec son API et son front
de l'installation.
Il l'a fait par l'Explorateur, sur le profil réel du poste, et non sur le poste simulé (§6.4).

Le run lancé avant la mise à jour avait déjà échoué quand Maestro s'est fermé : le profil vierge
n'a aucun compte. L'arrêt d'un run **en cours** par la mise à jour n'a donc pas été vu (§3.4).
