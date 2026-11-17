<!-- documentation: produit -->
# Isolation d'exécution des agents — mode isolé en conteneur durci (ticket #108)

**Version :** 0.1
Premier lot du renforcement sécurité (#102) : en **mode isolé** — opt-in,
activé par configuration — chaque exécution outillée d'agent tourne dans un
**conteneur Docker durci** jetable au lieu de s'exécuter avec les droits du
process hôte. Cette page consigne la décision technique (conteneur vs micro-VM),
l'architecture, les **accès accordés** au conteneur (le contrat du mode), son
activation et ses limites. Ce mode reste une **option plus forte, jamais exigée**.

> **Le défaut sous Windows est l'intégrité basse** (§7, #1515). #1514 l'a choisie sur pièces
> ([docs/48 §2.1.1](./48-decision-produit-pro-exploitable.md)) : sans aucun réglage, le CLI d'un
> agent naît abaissé. Il n'arrête aucun processus de la personne, et n'écrit que dans son projet
> et son établi. La portée reste l'arbitrage, mais elle n'est plus la seule barrière. Ailleurs que
> sous Windows, le défaut est l'exécution sur l'hôte, confinée (§6).

> **Pourquoi** : les agents exécutent du code (outils `Bash`, fichiers produits,
> serveurs MCP stdio) avec les droits du process qui les héberge. L'ouverture
> MCP (#101) et le multi-instances (#100) élargissent cette exposition : un
> serveur MCP tiers ou du code produit défaillant ne doit pas pouvoir toucher
> au poste. Tests : `tests/test_isolation.py` (#107) ; modèle de menace :
> [docs/19](./19-securite-modele-de-menace.md).

---

## 1. Décision : conteneur durci (micro-VM écartée pour l'instant)

Le ticket demandait de trancher **conteneur durci vs micro-VM**
(gVisor/Firecracker) selon l'environnement cible :

- **Cible actuelle : le poste de développement Windows.** Docker Desktop y
  exécute les conteneurs dans une **VM utilitaire WSL2** — le conteneur durci y
  bénéficie donc *déjà* d'une frontière de niveau VM avec l'hôte Windows, en
  plus de ses propres namespaces ;
- **gVisor et Firecracker sont Linux seulement** : inapplicables sur le poste
  cible. Ils restent la piste naturelle du déploiement **serveur Linux**
  (remplacer le runtime du démon par `runsc`, ou une micro-VM par exécution) —
  à réévaluer à ce moment-là, sans changer le contrat décrit ici : seule la
  commande de lancement (`maestro/sandbox/container.py`) changerait.

Le sandboxing natif de l'Agent SDK (`ClaudeAgentOptions.sandbox`) a aussi été
écarté : macOS/Linux seulement, donc inopérant sur la cible.

## 2. Architecture : le shim `cli_path`

L'Agent SDK exécute le CLI Claude Code en sous-processus et lui parle en stdio.
Le mode isolé s'insère à cette couture, sans toucher au reste de la chaîne :

```
hôte                                          conteneur (jetable, --rm)
────────────────────────────────────────     ─────────────────────────────────
moteur (executor) → runtime → fournisseur     image maestro-sandbox
    │  ClaudeAgentOptions(cli_path=shim,      ┌───────────────────────────┐
    ▼                     env=MAESTRO_SANDBOX_*)  │ claude (CLI)          │
Agent SDK ──spawn──▶ maestro-sandbox-shim ──▶ │  ├─ outils (Bash, Write…) │
    ▲                 (docker run durci)      │  ├─ serveurs MCP stdio    │
    └────────────────── stdio ───────────────▶│  └─ code produit          │
                                              └───────────────────────────┘
                                                /workspace ⇄ workspace hôte
```

- Le fournisseur (`maestro/providers/claude.py`) passe `cli_path=maestro-sandbox-shim`
  au SDK et pose le protocole `MAESTRO_SANDBOX_*` (image, réseau, workspace)
  sur le sous-processus ;
- le **shim** (`maestro/sandbox/shim.py`, point d'entrée `maestro-sandbox-shim`)
  traduit ce protocole en `docker run` durci (`maestro/sandbox/container.py`)
  et relaie stdio et code de sortie tels quels — vu du SDK, rien n'a changé ;
- **restent sur l'hôte** : le moteur, ses garde-fous (#9), la télémétrie et le
  journal (#8), la relance (#91), la résolution des playbooks (#78) et des
  déclarations MCP (#104). **Passent dans le conteneur** : le CLI, ses outils,
  les serveurs MCP stdio qu'il lance (`npx` fourni par l'image) et le code
  produit par l'agent ;
- un conteneur **par exécution outillée**, détruit à la fin (`--rm`) : aucun
  état ne survit ni ne se partage entre deux tâches.

Le chemin **texte** (`generate`, rôles sans runtime outillé) n'est pas isolé :
il n'expose **aucun outil** — ni fichier, ni shell, ni MCP — c'est son contrat
(docs/04 §6). L'isolation cible ce qui exécute.

## 3. Accès accordés au conteneur (le contrat)

Tout ce qui entre ou sort est énuméré ici — et nulle part ailleurs
(`maestro/sandbox/container.py` en est la source dans le code) :

| Accès | Accordé | Refusé |
|---|---|---|
| **Système de fichiers** | `/workspace` : l'espace de travail de la tâche, monté en lecture-écriture — c'est là que l'agent produit ses livrables. Sans projet, c'est le répertoire **jetable** créé vide (`maestro.sandbox.workspace`) ; rattachée à un projet (#224), la tâche y voit l'espace **dérivé** de ce projet — worktree Git sur la branche `maestro/<tâche>` si le projet est versionné, ou, pour un projet **non versionné**, **la racine elle-même** montée **avec ses masques** (#839, D2 révisée — [docs/24 §2.4](./24-projets-locaux-et-poste-de-travail.md)). Projet versionné : son **dépôt du conteneur** (#1602), monté en lecture-écriture sur `/maestro/depot`, et `/workspace/.git` recouvert en lecture seule d'un pointeur `gitdir: /maestro/depot` — l'agent y committe et y branche (§3.1). Deux tmpfs jetables : `/tmp` (512 Mo) et `/home/agent` (1 Go — état du CLI, caches npm/npx) | tout le reste : racine de l'image en **lecture seule** (`--read-only`), aucun autre chemin de l'hôte monté — **la racine d'un projet versionné en particulier n'est jamais montée**, ni son `.git` (#226, EF-36 : vérifié deux fois, au câblage du protocole puis au dernier mètre avant `docker run`, qui refuse aussi un dépôt du conteneur posé dans la racine ; depuis #839 la restriction vaut pour le projet **versionné**, la racine d'un projet non versionné étant son espace de travail), et les chemins **exclus** du périmètre (`.env`, `**/secrets/**`…) sont recouverts d'un montage vide en lecture seule |
| **Réseau** | sortant seul (`bridge`, défaut) : nécessaire pour joindre l'API du fournisseur et les serveurs MCP distants ; `none` le coupe entièrement (diagnostic) | tout entrant (aucun port publié), réseau de l'hôte |
| **Environnement** | 3 variables d'authentification : `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` (telles que posées par le fournisseur, y compris vides — la neutralisation de #30 est préservée). S'y ajoutent deux déclarations que la commande pose elle-même, sans rien prendre à l'hôte : `/workspace` est sûr pour git (`safe.directory`, portée « commande », #1599) — le montage d'un dossier de Windows appartient à root dans le conteneur, et git refuserait sans elle d'y committer —, et l'identité de Maestro pour les commits de l'agent (`GIT_AUTHOR_*`/`GIT_COMMITTER_*`, #1507) | tout le reste de l'environnement hôte. Pour git, tout autre dépôt du conteneur : seul le point de montage est déclaré, jamais `*`. Les secrets MCP (#104) ne transitent pas par l'environnement du conteneur : résolus en mémoire côté hôte, ils voyagent dans la config MCP passée en argument au CLI |
| **Privilèges** | utilisateur non-root `agent` (uid 10001) | toutes les capabilities (`--cap-drop ALL`), l'escalade (`no-new-privileges`) |
| **Ressources** | 256 pids, 2 Go de mémoire, 2 CPU (plafonds fixes du POC) | au-delà : le conteneur est tué, l'échec consigné comme les autres |

### 3.1 Le dépôt du conteneur d'un projet versionné (#1602)

L'espace d'un projet versionné est un worktree hors de la racine : son `.git` est un pointeur vers
le gitdir du worktree, **dans** la racine, que le conteneur ne voit pas. Lui monter ce gitdir et
les objets du dépôt lui ferait écrire le `.git` de la personne — un hook ou une config posés là
s'exécuteraient sur l'hôte au prochain git — et lui ouvrirait tout l'historique, que le périmètre
exclut parce qu'il porte des secrets. Un clone autonome dans l'espace aurait le même défaut pour
chaque git de l'hôte joué dans l'espace. Le conteneur reçoit donc un dépôt **à lui**
(`maestro/sandbox/depot_conteneur.py`), sur trois règles :

- **Le git de l'hôte ne lit jamais un dépôt que le conteneur a pu écrire.** Le dépôt est construit
  par l'hôte à chaque câblage de session (`env_sandbox`), à côté de l'espace, avant tout conteneur,
  et le précédent est effacé. Le worktree et le gitdir de l'hôte ne sont ni montés ni modifiés.
- **Ce que le périmètre exclut n'y entre pas, même par git.** Le dépôt ne porte que la tête de
  `maestro/<tâche>` (superficiel), ses arbres et les blobs **non exclus** (partiel, vers un distant
  inexistant). Son index se construit sans lire un blob, les chemins exclus y sont marqués
  `skip-worktree` : le masque vide d'un `.env` versionné ne se committe pas comme un effacement. Il
  reprend les réglages de rendu du dépôt de la personne (`core.autocrlf`, `core.eol`,
  `core.filemode`, `core.symlinks`, `core.ignorecase`), sans quoi chaque fichier extrait par un
  poste Windows paraîtrait modifié.
- **Le rapatriement passe par `upload-pack` hors de l'hôte.** À la fin de la session, le
  fournisseur (pas le shim : le SDK ne laisse au CLI que quelques secondes pour finir) rapatrie
  `maestro/<tâche>`, et elle seule, par un `git fetch` en avance rapide, objets vérifiés
  (`transfer.fsckObjects`). Le côté serveur joue dans un **conteneur jetable** par le transport
  `ext::` : dépôt monté en lecture seule, sans réseau, sans capacité — ce que la documentation de
  git recommande pour servir un dépôt non fiable. L'index de l'hôte est ensuite réaligné, l'arbre
  de travail reste tel que l'agent l'a laissé. Un refus ne casse pas la session : il se consigne
  au journal, et le contenu de l'espace est porté sur la branche par la sauvegarde du démontage.

## 4. Activation (opt-in)

1. **Construire l'image dédiée** (Docker démarré, dans Git Bash) :

   ```bash
   docker build --build-arg CLAUDE_CODE_VERSION=$(.venv/Scripts/python.exe -m maestro.sandbox.image_cli) -t maestro-sandbox:latest infra/sandbox
   ```

   L'image installe le CLI **de la version que le SDK de l'hôte embarque** (#1312) : le shim
   relaie au CLI du conteneur les arguments du SDK de l'hôte, et les deux doivent être de la même
   version. Sans cette version, la construction s'arrête. L'image le dit par son label
   `maestro.claude-cli`, que le journal de chaque run lit (« Versions du fournisseur ») : une image
   construite pour un autre SDK, ou avant ce label, y est dite **à reconstruire**. Après une montée
   du SDK, on la reconstruit ([docs/44 §7](./44-decision-maestro-possede-ses-contrats.md)).

2. **Configurer l'authentification par variable** : l'état de connexion du
   poste (`claude` connecté) n'est **jamais monté** dans le conteneur. Il faut
   `ANTHROPIC_API_KEY` (mode api_key) ou `CLAUDE_CODE_OAUTH_TOKEN` (mode
   subscription, token généré par `claude setup-token`) dans le `.env` ;

3. **Activer le mode** dans le `.env` :

   ```bash
   MAESTRO_ISOLATION=conteneur
   # optionnel : MAESTRO_ISOLATION_IMAGE=maestro-sandbox:latest
   # optionnel : MAESTRO_ISOLATION_RESEAU=bridge   # ou none (diagnostic)
   ```

Toutes les entrées habituelles en profitent sans autre geste — `maestro-run`,
`maestro-dev`/`maestro-bdd`/…, la file de tâches (#41 : la variable se pose sur
l'environnement du **worker**) — puisque le branchement vit dans la fabrique du
fournisseur. Une config bancale (mode inconnu, réseau invalide, shim
introuvable) casse **au câblage** avec une erreur explicite ; Docker arrêté ou
image absente se constatent au lancement et remontent en **échec de tâche**
consigné au journal, comme les autres échecs.

> ⚠ **Le projet de l'utilisateur est le seul endroit du contrat que la Phase 7 a déplacé** (#226,
> décision D1 rendue le 2026-08-04, #218 — cadrage
> [docs/24 §2.5](./24-projets-locaux-et-poste-de-travail.md)). Jusque-là, `/workspace` était un
> répertoire jetable créé vide : le conteneur ne touchait **aucun** chemin de l'hôte porteur de
> données. Le « second montage » annoncé par le cadrage se matérialise **à la place** de ce
> répertoire, jamais en plus : l'espace dérivé *est* l'espace de travail de la tâche, et monter
> aussi la racine reviendrait à monter deux fois le même arbre — en plus large. Trois invariants
> tiennent cette ligne, et ils sont vérifiés plutôt que supposés : la **racine du projet n'est
> jamais montée** (refus au câblage du protocole et au dernier mètre avant `docker run`, la seule
> porte que rien ne contourne), les **chemins exclus** du périmètre sont masqués jusque dans le
> conteneur — le worktree d'un projet versionné, lui, porte bien un `.env` ou un `secrets/`
> **versionnés**, là où la copie d'un projet non versionné les écarte d'office — et un périmètre
> qui excède ce que le conteneur sait masquer est un **refus franc**, pas un montage à moitié.
> Rien du reste du tableau ne bouge. Le projet de l'utilisateur devient au passage un **actif à
> protéger** au même titre que le poste hôte : voir
> [docs/19 §2.1](./19-securite-modele-de-menace.md).
>
> ⚠ **Depuis #839, « la racine n'est jamais montée » ne vaut plus que pour un projet
> versionné.** Un projet **non versionné** travaille **dans sa racine** (régime en place,
> [docs/24 §2.4](./24-projets-locaux-et-poste-de-travail.md), `maestro/sandbox/en_place.py`) :
> son espace de travail *est* la racine, elle est donc montée sur `/workspace` — **avec ses
> masques**, ce qui fait du conteneur l'endroit où les exclusions du périmètre deviennent une
> clôture dure (sur l'hôte, la frontière d'écriture ne confronte que les outils de fichiers de
> l'agent, jamais `Bash`). `MAESTRO_SANDBOX_PROJET` n'est alors pas transmise : il n'y a rien à
> refuser au dernier mètre. La révision de la doctrine est le lot #707.

## 5. Limites connues (assumées au POC)

- **L'égress n'est pas filtré par domaine** : `bridge` laisse sortir vers tout
  Internet (il faut au minimum l'API du fournisseur). Le filtrage fin (proxy
  filtrant, allowlist de domaines) est une évolution naturelle — la politique
  par agent/outil arrive avec #110 ;
- **Serveurs MCP `localhost`** : dans le conteneur, `localhost` n'est plus le
  poste — un serveur MCP local (ex. Ollama sur `localhost:11434`) doit être
  déclaré via `host.docker.internal` ;
- **Démarrage à froid plus lent** : lancement du conteneur par exécution, et
  premier `npx` des serveurs MCP stdio re-téléchargé à chaque fois (home en
  tmpfs, jetable par conception) — le délai de connexion MCP borné (#104)
  s'applique inchangé ;
- **Arrêt brutal** : si le process hôte est tué net, un conteneur peut survivre
  jusqu'à la fermeture de son stdin (`--rm` le nettoie alors). Le time-out par
  tâche (#64) reste la borne nominale ;
- **Dépôt du conteneur** (§3.1) : les **noms** des fichiers exclus restent visibles dans les
  arbres, comme par les masques, et leur **empreinte** aussi — elle permet de confirmer une valeur
  devinée d'un `.env` versionné, pas de la lire. Une branche créée par l'agent autre que
  `maestro/<tâche>` reste dans le conteneur ; un historique réécrit (`--amend` de la tête, `reset`)
  n'est pas rapatrié comme commits, son contenu l'est par la sauvegarde du démontage. Les hooks du
  projet ne jouent pas pour les commits faits dans le conteneur, et Git LFS n'y est pas géré. La
  construction coûte de l'ordre de 3 s sur un dépôt d'un millier de fichiers, plus un conteneur
  jetable par rapatriement ;
- **Tests** (#107) : câblage, commande durcie et shim couverts par
  `tests/test_isolation.py` ; le lancement **réel** d'un conteneur exige Docker,
  absent des runners CI — procédure de smoke test manuelle dans
  [docs/19 §4](./19-securite-modele-de-menace.md) ;
- **en distribution bureau, le défaut ne change pas** — il reste le mode non
  isolé, et le contrat du §3 ci-dessus est intact : la coque (#923,
  [docs/35 §2](./35-decision-poste-de-bureau-et-disposition.md)) ne le touche
  pas. Ce que cela **implique** est écrit une fois et une seule, dans
  [docs/19 §2.4](./19-securite-modele-de-menace.md) : le filet y est alors le
  **périmètre du projet**, pas le conteneur ([docs/24 §4.6](./24-projets-locaux-et-poste-de-travail.md)).

> ⚠ **Ce dernier point ne vaut plus** ([docs/48 §2.1](./48-decision-produit-pro-exploitable.md),
> #1518). En distribution bureau, le défaut est l'**intégrité basse** (§7), et l'installeur la pose
> en toutes lettres (`MAESTRO_ISOLATION=integrite-basse`, #1515). Le conteneur reste une option.
> - **#1514 l'a joué une fois contre un vrai démon Docker**, par la commande de `commande_docker`
>   puis le vrai shim. Il ferme les trois menaces du bilan.
> - **git y accepte le workspace** : le montage venu de Windows appartient à root et non à
>   l'utilisateur `agent`, et la commande déclare `/workspace` seul sûr pour git (§3, #1599).
> - **git y trouve un dépôt pour un projet versionné** : le dépôt du conteneur (§3.1, #1602), où
>   l'agent committe et branche, et dont la branche de la tâche revient sur l'hôte.
> - L'hôte ne joint pas un serveur lancé dedans, et l'image n'a pas de navigateur.

## 6. Hors mode isolé : la session d'un agent est confinée (#1279)

En mode isolé, le conteneur jetable emporte avec lui tout ce que le CLI y a lancé.
Hors conteneur, qu'il soit abaissé (§7) ou non, rien ne le faisait : le run `3fe501fc0878`
(2026-09-24) a laissé tourner un Edge headless que l'agent avait lancé en arrière-plan
pour vérifier sa maquette, **son port de débogage ouvert** à tout processus du poste,
rattaché à un `bash.exe` déjà mort. Le SDK ferme **son** sous-processus, le CLI ; ce que
le CLI a lancé n'est l'enfant de personne.

**La règle.** Ce qu'un agent lance pendant une tâche ne survit pas à cette tâche : à la
clôture de sa session — succès, échec, relance (chaque tentative est une session),
annulation —, tout ce qui en est né est arrêté, et le journal du run le dit (étape
`<tâche>:processus`) : ce qui lui survivait et a été arrêté, ce qui a **résisté**, nommé
avec son pid, ou une session qui n'a pas pu être confinée. Pendant la tâche, l'agent
lance ce qu'il veut sous sa politique : c'est un garde-fou, pas une bride
([docs/41](./41-decision-maestro-juge-il-ne-bride-pas.md)).

**La couture est la même qu'au §2.** Le SDK n'expose ni le processus ni le pid du CLI, et
lire ses internes serait dépendre d'un outil tiers ([docs/44](./44-decision-maestro-possede-ses-contrats.md)).
Le fournisseur passe donc en `cli_path` le **lanceur** `maestro-confinement`
(`maestro/sandbox/confinement.py`), qui lance le vrai CLI — celui que le SDK aurait
pris : l'embarqué, sinon celui du PATH — avec les flux du SDK hérités tels quels, dans
un arbre de processus (`maestro/sandbox/arbre.py`) :

| Plateforme | L'arbre | Ce qui s'en échappe, et comment on le rattrape |
|---|---|---|
| Windows | un **Job Object** « tué à la fermeture », où le CLI naît suspendu | un **alias d'application** (le `python` du Microsoft Store) fait naître son processus hors du job — mesuré ; le **veilleur** tient une poignée sur chaque membre, voit ses enfants nés dehors et les **adopte** dans un job à lui — jamais avant qu'ils aient commencé à vivre (ci-dessous) |
| Linux | le **groupe** de processus du CLI | un `setsid` ; le lanceur se déclare *subreaper* : l'orphelin lui revient, il est arrêté comme descendant |
| macOS | le groupe de processus | un `setsid` reste hors d'atteinte |

À la fin de la session, le lanceur arrête l'arbre et écrit son relevé, que le
fournisseur lit une fois la session fermée. Si le SDK doit le terminer (le CLI ne sort
pas dans son délai de grâce), le signal est intercepté sous POSIX ; sous Windows, le job
« tué à la fermeture » emporte l'arbre quand le système ferme la poignée du lanceur
mort — sans relevé, et rien n'est alors affirmé.

**Un évadé qui n'a pas fini de naître n'est jamais touché** (#1367). Tous les `python`
du Store d'un poste partagent **un seul conteneur AppX**, quelle que soit la copie de
travail qui les a lancés. L'activation du paquet fait naître le processus suspendu,
l'ajoute au conteneur puis le relâche ; le ranger dans un job ou le tuer pendant ces
quelques millisecondes fait échouer l'activation, et le système détruit alors le
conteneur : tous les `python` du poste meurent à la même seconde — la panne du
2026-09-27, reproduite sur le poste. Le veilleur attend donc qu'un évadé suspendu soit
relâché pour l'adopter ; suspendu, il ne lance rien. Celui que son créateur, tué entre
sa naissance et sa relâche (Git Bash fait naître ses enfants suspendus), ne relâchera
plus est achevé une fois l'échéance passée ; tant que son créateur vit, il n'est
jamais touché. Le détail et les mesures sont dans l'en-tête de `maestro/sandbox/arbre.py`.

**Ce qui n'est pas confiné, et le dit** : un poste dont le paquet n'a pas été réinstallé
(lanceur absent) ou sans CLI trouvable fait tourner la session comme avant, avec une
ligne « session non confinée » au journal. Reste hors d'atteinte, sous Windows, un
processus né d'un évadé mort avant d'avoir été vu : plus rien ne le relie à l'arbre.
Tests : `tests/test_confinement.py` — la chaîne réelle (fournisseur, transport du SDK,
lanceur, faux CLI) en succès, échec et annulation, le `sleep &` de `bash`, l'alias du
Store, le lanceur terminé en pleine session, et la ligne du journal ;
`tests/test_arbre.py` — un évadé suspendu n'est ni adopté ni tué, il l'est une fois
relâché, et le reste d'un créateur mort est achevé.

## 7. L'intégrité basse : le défaut sous Windows (#1515)

La portée lit le **texte** d'une commande. Un script que l'agent lance ne lui en dit rien :
`bash nettoyer.sh`, s'il contient `taskkill //IM python.exe //F`, passe la portée et arrête Maestro
(#1502). Ce qui borne le script est donc le **poste**. Le CLI de l'agent naît sous le jeton de la
personne, **abaissé à l'intégrité basse**. C'est le mécanisme que #1514 a choisi sur pièces
([docs/48 §2.1.1](./48-decision-produit-pro-exploitable.md)). Il n'exige rien du poste : ni
élévation, ni Docker, ni compte.

**Le réglage** est `MAESTRO_ISOLATION` (ENF-12) :

| Valeur | Ce que fait l'agent |
|---|---|
| vide | le défaut du poste : `integrite-basse` sous Windows, `hote` ailleurs |
| `integrite-basse` | son CLI naît abaissé. L'installeur pose cette valeur en toutes lettres. Ailleurs que sous Windows, elle est refusée au câblage, jamais remplacée par une autre |
| `hote` | il tourne au niveau de la personne, confiné (§6) |
| `conteneur` | le mode isolé (§1 à §5) |

**Le mécanisme.** Le lanceur `maestro-confinement` (§6) reste au niveau de la personne, et le job
qui borne la vie de l'arbre reste avec lui. Seul le CLI naît abaissé
(`maestro/sandbox/low_integrity.py`) : suspendu, rangé dans son job, puis relâché. Tout ce qu'il
lance naît abaissé à son tour. Si le système refuse l'abaissement, la session échoue avec sa
cause : un CLI ne tourne jamais au niveau de la personne en se croyant abaissé.

**Ce que l'agent écrit reçoit une étiquette basse héritée** (`maestro/sandbox/low_session.py`) :
- son **espace de travail** ;
- le **dépôt Git** dans lequel écrit le worktree d'un projet versionné (objets, références) ;
- le **dossier du CLI de Maestro**, `~/.maestro/claude` ;
- son **établi** : un dossier propre à la session, retiré avec elle. `TEMP`, `TMP` et `TMPDIR` y
  mènent, si bien qu'un `mktemp -d` y crée un dossier que la portée reconnaît. `APPDATA` et
  `LOCALAPPDATA` y mènent aussi : ce qu'un outil écrit dans le profil (un verrou, un cache) y
  atterrit.

Poser une étiquette demande le droit `WRITE_OWNER`, que « Modifier » ne donne pas. Sur un disque où
la personne n'a que « Modifier », elle s'accorde d'abord le contrôle total sur le dossier, sans
élévation. Une étiquette posée reste en place, et un dossier déjà étiqueté n'est pas reparcouru.

**Le dossier du CLI de Maestro.** À l'intégrité basse, le CLI ne peut pas écrire dans `~/.claude`.
Or la reprise d'une session d'une tâche à l'autre (#1513) relit les transcriptions du CLI, et un
jeton rafraîchi doit pouvoir se réécrire. **Toutes** les sessions Claude de Maestro font donc
tourner le CLI dans `~/.maestro/claude` (`CLAUDE_CONFIG_DIR`) : les agents, le Chef de projet,
l'assistant et la sonde. Ce dossier ne voit pas la connexion du CLI de la personne. Maestro se
connecte donc une fois par poste, par l'une de ces voies :
- `maestro-connexion` (ou `python -m maestro.providers.claude_login`), qui joue `claude auth
  login` dans ce dossier ;
- un `CLAUDE_CODE_OAUTH_TOKEN` ;
- une clé d'API.

`maestro-connexion --etat` dit si Maestro est connecté. Le `~/.claude` de la personne n'est pas
touché.

**Git for Windows est copié.** À l'intégrité basse, son runtime meurt s'il tourne depuis son propre
chemin : le répertoire d'objets nommés qui porte ce chemin existe déjà au niveau de la personne, et
un processus bas ne peut pas l'ouvrir. Depuis un autre chemin, il crée le sien.

Maestro copie donc l'installation entière sous `~/.maestro/outils/git-<empreinte>`, une fois par
version. La copie passe en tête du `PATH`, et le CLI la reçoit par `CLAUDE_CODE_GIT_BASH_PATH`.
Ainsi, le `git` que l'agent lance et le `sh` de ses hooks sont ceux de la copie : le Git d'origine
mourrait dès qu'un hook appelle `sh`. La copie ne porte aucune étiquette, si bien que l'agent ne
peut pas modifier ses propres outils. La copie d'une version antérieure est retirée dès qu'aucun
processus ne la tient plus.

**Un chemin écrit en toutes lettres mène aussi à un runtime qui vit** (#1708). Un réglage de la
personne peut nommer le Git d'origine sans passer par le `PATH` : le `script-shell` de npm réglé
sur `C:\Program Files\Git\bin\bash.exe`, un éditeur, une commande ssh. L'environnement de l'agent
porte donc la variable `CYGWIN_TESTING` (`low_session.RUNTIME_APART`). Le runtime Cygwin, celui
de MSYS2 et de Git for Windows, la lit : il range alors ses objets nommés dans un répertoire à
part, suffixé par sa date de construction. Quel que soit le chemin qui y mène, un runtime que
l'agent fait naître ne crée donc ni ne rejoint jamais le répertoire de la personne. Maestro ne
reconnaît ni l'outil, ni le réglage, ni l'endroit où ce réglage vit. Un MSYS2 que la copie ne
couvre pas en profite aussi.

Le runtime destine cette variable à ses propres tests, et sa documentation ne la décrit pas. Sa
seule autre conséquence est de tolérer qu'un parent porte une autre version du runtime. Si une
version future la retirait, les tests réels ci-dessous échoueraient sur le poste.

**Ce que Maestro ferme à la lecture.** Une étiquette moyenne « pas de lecture vers le haut »
(`NO_READ_UP`) rend illisible à l'agent ce qui lui permettrait d'agir en Maestro :
- le jeton de l'API ;
- le coffre des secrets ;
- les `.env` que Maestro charge.

Pour la personne, rien ne change. L'étiquette est reposée à chaque session, parce qu'un fichier
réécrit ne la garde pas.

Maestro charge ses `.env` dans son propre environnement : leurs valeurs y sont, même si les
fichiers sont fermés. Le lanceur retient donc au CLI abaissé les variables `MAESTRO_*`, ainsi que
toutes celles que ces fichiers déclarent (un jeton de forge, un jeton Slack, la clé Langfuse).
Seul l'espace du fournisseur (`ANTHROPIC_*`, `CLAUDE_*`) passe, parce que c'est par lui que le CLI
joint le modèle.

**La vérification de l'outillage joue sous la même borne** (#1691). Maestro joue lui-même les
commandes qu'un outillage écrit, avant de les écrire, dans une copie du projet ou un clone neuf de sa
branche ([docs/38 §4.1](./38-decision-outillage-universel-du-projet.md)). La copie protège la racine,
pas le poste : le `setup.sh` de Maestro pose une fonction dans le profil PowerShell, dont Windows
résout le chemin sans lire `HOME`. Chaque commande joue donc sous la borne des agents du poste
(`maestro.sandbox.verification.borne_du_poste`) :

| Régime des agents | La vérification |
|---|---|
| `integrite-basse` | Abaissée, sous l'établi d'une session (la copie étiquetée basse, la copie de Git, `TEMP` et `APPDATA` redirigés vers l'établi, `LOCALAPPDATA` vers le cache du projet, l'espace de noms de Maestro et ce que ses `.env` déclarent retenus) |
| `conteneur`, sous Windows | Abaissée de même : elle joue sur le poste, et c'est la borne que le poste a |
| `conteneur`, ailleurs | Pas jouée, et sa raison le dit : rien ne joue plus large que les agents |
| `hote` | Au niveau de la personne, comme ses agents |

Une borne que le système refuse (une étiquette, l'absence de Git for Windows) rend la commande
« à vérifier » avec sa raison. Elle n'est jamais rejouée au niveau de la personne. Ce que la borne ne
couvre pas est ce qu'elle ne couvre pas pour un agent, listé ci-dessous.

**Un cache par projet pour la vérification** (#1709). L'établi part avec la vérification. Si
`LOCALAPPDATA` y menait, ce que les outils y rangent (le cache de npm, par exemple) partirait avec
lui, et chaque vérification retéléchargerait tout. La vérification reçoit donc, à la place, le
`LOCALAPPDATA` de **son projet**, gardé sous `~/.maestro/verification-caches/`
(`maestro/sandbox/verification_cache.py`) :
- **un dossier par projet, jamais partagé** : ce qu'une vérification y laisse, la suivante le relit,
  et peut l'exécuter. Partagé, il ferait passer dans le projet B ce que la vérification du projet A
  y a laissé. Par projet, il n'ouvre aucun canal nouveau : les agents d'un projet peuvent déjà
  déposer dans son arbre ce que la vérification suivante copie. La personne l'a tranché le
  2026-11-17 ;
- **le même projet** est reconnu à son **dépôt Git** quand l'arbre vérifié est un dépôt ou un
  worktree (Git le nomme), sinon à sa racine. Une génération écrit l'outillage dans un worktree
  neuf à chaque fois : c'est le dépôt qui lui fait retrouver le cache de la racine. Une racine qui
  vit dans le dépôt d'un autre n'est pas ce dépôt, et n'en partage pas le cache ;
- **une vérification à la fois** tient le cache de son projet, par un verrou que le système rend à
  la mort de son détenteur. Deux vérifications du même projet peuvent se chevaucher, puisque le
  créneau ne sérialise que les services. La seconde joue alors avec le `LOCALAPPDATA` vide de son
  établi : plus lente, jamais fausse. Ce que ce dossier contient est inconnu de Maestro, et le trier
  par outil reviendrait à reconnaître un outil par son nom ([docs/44](./44-decision-maestro-possede-ses-contrats.md)) ;
  attendre, c'était entamer des délais qui ne bougent pas ;
- **une rétention** : chaque ouverture retire les caches qu'aucune vérification n'a rouverts depuis
  quatorze jours, et n'en garde pas plus de huit, les plus récents. Ce sont des décisions, pas des
  mesures. Un cache qu'une autre vérification tient n'est jamais retiré.
  `python -m maestro.menage` montre le dossier.

`APPDATA`, où vivent les réglages (`pip.ini`, `.npmrc`), reste à l'établi et part avec lui. Le cache
n'épargne que ce qu'un outil range **là où la variable le mène** : npm suit `LOCALAPPDATA`, et son
`npm-cache` y survit. pip, lui, demande le dossier à Windows, qui lui rend le `AppData\Local` de la
personne : un processus bas n'y écrit pas, et pip joue sans cache, donc `pip install` retélécharge
tout à chaque vérification (mesuré le 2026-11-17). `setup.sh` télécharge Node par `curl` dans
`.tools/node/` de la copie, et un clone neuf le retélécharge, comme il recrée son `.venv` et ses
`node_modules`.

Sur le dépôt de Maestro, cloné à part puis vérifié deux fois de suite sur le poste de référence
(2026-11-17, #1709), l'étape `shell` de `setup.sh` voit son écriture du profil refusée, et le
profil garde son empreinte. Chaque passage rend un verdict joué, « échouée » sur cette seule étape :
268 s à froid, puis 232 s avec le cache du projet (253 Mo, celui de npm). Le cache fait gagner une
demi-minute. La marge qui reste sous les 300 s est mince, parce que Node, le `.venv` et ses
dépendances pip se retéléchargent à chaque passage. Un passage à froid joué pendant la vérification
d'une autre session n'a pas rendu la main dans les 300 s, et est resté « à vérifier ».

**Ce qui est mesuré sur le poste** (`tests/test_low_integrity.py` et
`tests/test_verification_low_integrity.py`, sous Windows, marqueur `integrite_basse`) :
- `taskkill /IM` contre un programme témoin de la personne, lancé directement ou par un script :
  l'agent ne le trouve pas, et le témoin vit ;
- la commande de #1502 (`taskkill //IM python.exe //F`), lancée directement ou par un script :
  aucun `python.exe` de la personne n'est arrêté. Cette commande ne se joue qu'après deux preuves
  sur le même poste : l'enfant tourne bien à l'intégrité basse, et le témoin a survécu ;
- écrire dans l'espace, l'établi et le dossier du CLI réussit ; écrire à côté échoue, et lire un
  fichier fermé échoue aussi ;
- un runtime du Git d'origine tourne au niveau de la personne, et l'agent lance le bash de ce Git
  par son chemin complet : le bash répond. Un `npm install` dont le `script-shell` est réglé sur ce
  bash joue son `postinstall` ;
- le lanceur abaisse le CLI et lui retire les variables `MAESTRO_*` et celles qu'il doit retenir.
  Le reste passe au filtre des variables nommées (§8) ;
- une commande de vérification dont le script ajoute une ligne à un profil hors de la copie est
  jouée et échoue sur l'écriture refusée, dans une copie comme dans un clone neuf. Le profil garde
  son contenu et sa date, sans restauration. La commande voisine, qui n'écrit que dans sa copie et
  son établi, est vérifiée ; elle ne reçoit ni `MAESTRO_*` ni ce qui est retenu ;
- une commande de vérification qui remplit son cache sous `LOCALAPPDATA`, comme un gestionnaire de
  paquets, l'écrit à l'intégrité basse. La vérification suivante du même projet le trouve, celle
  d'un autre projet non, dans une copie comme dans un clone neuf.

**Ce que l'isolation ne couvre pas.** L'agent en est informé sous « La machine »
(`maestro.machine.LIGNE_INTEGRITE_BASSE`), et voici la liste :
- **la lecture** : l'agent lit ce que la personne lit, hors ce que Maestro ferme. Cela comprend
  les identifiants du CLI de la personne, `~/.ssh`, le jeton du CLI GitHub et le `.env` d'un autre
  clone. Le profil de la personne n'est pas touché : y poser une étiquette est une décision à
  prendre avec elle ;
- **ce qui est étiqueté bas** : tout processus bas du poste peut y écrire. Deux agents qui
  tournent en même temps écrivent l'un chez l'autre, et l'un peut arrêter les processus de
  l'autre. Les étiquettes restent après la session ;
- **le réseau**, bouclage compris : l'agent joint ce qui écoute sur `127.0.0.1`, comme l'API de
  Maestro, un Redis ou le port de débogage d'un navigateur ;
- **l'environnement** : l'agent ne reçoit que les variables nommées (§8), mais celles-là, il les
  lit. Une clé du fournisseur passée au CLI lui est lisible, comme dans le conteneur ;
- **un cache par session pour un agent** : ce qu'un outil range dans le profil (le cache de `npx`,
  par exemple) part avec l'établi de sa session, et se retélécharge à la suivante ;
- **le cache d'un projet que la vérification garde** (#1709), deux risques acceptés avec la
  décision du 2026-11-17 :
  - un processus bas d'un **autre projet** peut écrire dans ce dossier, puisqu'il est étiqueté bas,
    et la vérification suivante du projet relit ce qu'il y a laissé, qu'une commande peut
    exécuter. « Par projet » garantit que Maestro ne fait pas passer un projet dans l'autre, pas
    qu'un agent hostile d'un autre projet ne puisse pas y écrire ;
  - ce qu'une commande y laisse **persiste** jusqu'à la rétention, cache ou état d'un outil, et
    tout agent du poste peut le lire ;
- **la vérification d'une tâche** (`maestro/engine/verification.py`) : ses contrôles tournent au
  niveau de la personne. Ce sont des commandes que le vérificateur écrit, et que la portée ne juge
  pas ;
- **les autres systèmes** : ailleurs que sous Windows, rien n'est abaissé. Le défaut y est `hote`,
  et le conteneur reste l'option qui isole.

**La portée reste l'arbitre.** Un arrêt par nom ou par motif revient toujours à une personne
(`tests/test_portee.py::test_tuer_python_par_son_nom_revient_a_une_personne`), que le poste l'eût
refusé ou non. Le poste borne ce que le texte d'une commande ne dit pas ; il ne remplace pas le
jugement.

## 8. L'environnement d'une session : les variables nommées seules (#1312)

Une session d'agent ne reçoit du poste que les variables que quelqu'un a **nommées**. Une variable
ambiante que personne n'a nommée — un jeton de forge, la clé d'un autre service, une variable de
Maestro — n'y entre pas, à tous les niveaux d'intégrité.

**Ce qui nomme une variable** (`maestro/sandbox/session_env.py`) :
- le **socle** de toute session (`BASE_NAMES`) : ce sans quoi un programme ne démarre pas, ne
  trouve ni le profil ni le temporaire (`PATH`, `SYSTEMROOT`, `USERPROFILE`, `HOME`, `TEMP`…), la
  langue (`LANG`, `LC_*`), et les réglages réseau du poste (mandataire, certificats) ;
- le **fournisseur** : ce qu'il pose lui-même (l'authentification, l'identité Git des commits de
  l'agent, l'établi d'une session basse), ce dont son CLI a besoin (`CLAUDE_CODE_GIT_BASH_PATH`),
  et les variables de l'espace du CLI (`ANTHROPIC_*`, `CLAUDE_*`) que les `.env` de Maestro
  déclarent — c'est ainsi qu'on désigne une passerelle au CLI ;
- la **personne**, par `MAESTRO_AGENT_ENV` : des noms séparés par des virgules, un `*` final pour
  une famille (`JAVA_HOME,GRADLE_*`). C'est le geste pour un outil du projet qui a besoin d'une
  variable du poste.

**Comment.** Le SDK transmet toujours au CLI l'environnement entier de son processus, et n'offre
aucun moyen d'en retirer une variable. Le filtre est donc posé au lanceur du confinement (§6), qui
fait naître le CLI. Le fournisseur lui passe les variables ambiantes non nommées, chacune avec
l'**empreinte** de sa valeur, jamais la valeur, et le lanceur retire celles qui arrivent
inchangées. Ce que le SDK pose en chemin (son protocole, le dossier courant) a changé de valeur et
reste : Maestro n'a pas à connaître les variables internes du SDK
([docs/44](./44-decision-maestro-possede-ses-contrats.md)). La sonde du point de contrôle passe par
le même lanceur, sous le même filtre.

**Ce que ce filtre ne couvre pas :**
- le **mode isolé** a sa propre liste blanche, plus étroite : les variables d'authentification
  seules (§3) ;
- une session **non confinée** — lanceur absent, CLI introuvable — reçoit l'environnement entier,
  et la ligne « session non confinée » du journal le dit ;
- les **chemins texte** (`generate`) : ils n'exposent aucun outil, rien n'y lit l'environnement ;
- les **vérifications** que Maestro joue lui-même (§7) : elles tournent dans son environnement.

**Prouvé** : `tests/test_session_env.py` (la règle, le lanceur joué pour de vrai, ce que le
fournisseur nomme) ; sur le vrai CLI, la suite de contrat
([docs/44 §7](./44-decision-maestro-possede-ses-contrats.md)) — une variable ambiante non nommée ne
sort pas du shell de l'agent, la même nommée en sort.
