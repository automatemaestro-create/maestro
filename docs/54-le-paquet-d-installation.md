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
- les **projets** sous `%USERPROFILE%\Maestro\` (docs/24 §2.7).

Le **dossier d'installation ne porte que le paquet**. Les politiques et les serveurs livrés avec
les agents s'y lisent toujours (`resources\produit\core\`) ; ce que la personne y change s'écrit
sous `configuration\` et les recouvre, sans les toucher. Réinstaller, mettre à jour ou désinstaller
remplace ou retire ce dossier sans rien perdre de ce qui précède (§3.3.3). Seul le cache de
compilation de Python (`__pycache__`) y naît en marchant : l'interpréteur le rebâtit à la demande.

L'installation se sait installée par son réglage `MAESTRO_INSTALLATION=1`, que le paquet pose dans
`resources\produit\.env`. Sans lui — un clone du dépôt —, tout reste où il était, sous `core\`.

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
- **Les mises à jour.** Une nouvelle version s'installe par-dessus l'ancienne ; la mise à jour sans
  chaîne de développement est #644. Ce que Maestro a écrit ne vit plus sous le dossier
  d'installation et survit au remplacement (§3.3.3). Une installation 0.1.0, qui écrivait sous
  `resources\produit\core\`, voit ces données **reprises** au premier démarrage de la version
  suivante : l'installeur recopie l'ancien `core\` et l'ancien `.env` sous
  `%USERPROFILE%\.maestro\reprise\` avant d'effacer l'ancien dossier, puis le lanceur les range
  sous `configuration\`. Il le fait une seule fois, sans doublon, et sans écraser ce qui s'y trouve
  déjà : un fichier déjà présent est conservé et nommé dans son journal, une clé du `.env` déjà
  réglée n'est pas reprise.
- **Une signature de code.** L'installeur n'est pas signé : Windows SmartScreen le signale comme
  une application d'éditeur inconnu au premier lancement.

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

Les accords dont dépend l'installation — le `._pth`, les fichiers **suivis** seulement (jamais une
donnée locale de `core/`), l'emplacement du Node embarqué, le dossier du produit vu de
l'assembleur, d'electron-builder et de la coque — sont gardés par `tests/test_distribution.py`.
