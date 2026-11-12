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
- les **journaux du lanceur** sous le dossier d'installation (`resources\produit\.maestro\lanceur\`),
  avec la cause d'un service qui serait mort au démarrage ;
- les **projets** sous `%USERPROFILE%\Maestro\` (docs/24 §2.7).

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
  (`resources\produit\.env`) : ce paquet ne la change pas.
- **Les mises à jour.** Une nouvelle version s'installe par-dessus l'ancienne ; la mise à jour sans
  chaîne de développement est #644. ⚠ La configuration que Maestro écrit sous son dossier
  d'installation (`resources\produit\core\`) appartient à cette installation : c'est la question
  que #644 doit trancher avant qu'une mise à jour ne la remplace.
- **Une signature de code.** L'installeur n'est pas signé : Windows SmartScreen le signale comme
  une application d'éditeur inconnu au premier lancement.

## 2bis. Les quatre composants : ce qui suit le paquet, et comment lire sa version

Une installation porte quatre composants dont les versions pourraient diverger (docs/24 §4.4,
§4.6). Le paquet les pose **tous les quatre ensemble**, à une seule version : celle que porte le
nom de l'installeur. Aucun n'a de mise à jour à lui aujourd'hui (la mise à jour automatique est
#644). Un composant peut pourtant ne pas suivre : une fenêtre de Maestro réinstallée seule devant
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
réinstaller le retrouve.

Deux défauts de la vérification elle-même ont été trouvés en route, et corrigés avant cette passe :
elle fermait le relais de son propre environnement au lieu de la fenêtre de Maestro (qu'elle
cherche maintenant parmi les processus de l'installation), et elle n'attendait pas la fin du
désinstalleur, qui se recopie sous `TEMP` et rend la main aussitôt.

### 3.4 Ce qui n'a pas été vérifié

- **Une machine réellement vierge** (une VM Windows neuve) : le poste de référence a Python, Node et
  Git installés, que la vérification masque au lieu de les retirer. Les tests différés de la
  phase (#645) sont l'endroit où le faire.
- **Une session d'agent depuis l'installation** : elle demande un compte connecté au modèle, que le
  poste n'avait pas le jour de la mesure (CLI déconnecté). Le CLI est trouvé, se lance et répond au
  SDK ; sa conversation avec le modèle n'a pas été jouée.
- **Un poste sans Git du tout** : la vérification retire Git du `PATH`, pas du disque. Le CLI
  `claude` a démarré ainsi, mais Git restait installé à son emplacement habituel.
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
