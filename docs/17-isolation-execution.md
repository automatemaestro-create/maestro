# Isolation d'exécution des agents — mode isolé en conteneur durci (ticket #108)

**Version :** 0.1
Premier lot du renforcement sécurité (#102) : en **mode isolé** — opt-in,
activé par configuration — chaque exécution outillée d'agent tourne dans un
**conteneur Docker durci** jetable au lieu de s'exécuter avec les droits du
process hôte. Cette page consigne la décision technique (conteneur vs micro-VM),
l'architecture, les **accès accordés** au conteneur (le contrat du mode), son
activation et ses limites. Le mode **non isolé reste le défaut** à ce stade.

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
| **Système de fichiers** | `/workspace` : l'espace de travail de la tâche, monté en lecture-écriture — c'est là que l'agent produit ses livrables. Sans projet, c'est le répertoire **jetable** créé vide (`maestro.sandbox.workspace`) ; rattachée à un projet (#224), la tâche y voit l'espace **dérivé** de ce projet — worktree Git sur la branche `maestro/<tâche>`, ou copie de son périmètre. Deux tmpfs jetables : `/tmp` (512 Mo) et `/home/agent` (1 Go — état du CLI, caches npm/npx) | tout le reste : racine de l'image en **lecture seule** (`--read-only`), aucun autre chemin de l'hôte monté — **la racine du projet en particulier n'est jamais montée** (#226, EF-36 : vérifié deux fois, au câblage du protocole puis au dernier mètre avant `docker run`), et les chemins **exclus** du périmètre (`.env`, `**/secrets/**`…) sont recouverts d'un montage vide en lecture seule |
| **Réseau** | sortant seul (`bridge`, défaut) : nécessaire pour joindre l'API du fournisseur et les serveurs MCP distants ; `none` le coupe entièrement (diagnostic) | tout entrant (aucun port publié), réseau de l'hôte |
| **Environnement** | 3 variables d'authentification : `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` (telles que posées par le fournisseur, y compris vides — la neutralisation de #30 est préservée) | tout le reste de l'environnement hôte. Les secrets MCP (#104) ne transitent pas par l'environnement du conteneur : résolus en mémoire côté hôte, ils voyagent dans la config MCP passée en argument au CLI |
| **Privilèges** | utilisateur non-root `agent` (uid 10001) | toutes les capabilities (`--cap-drop ALL`), l'escalade (`no-new-privileges`) |
| **Ressources** | 256 pids, 2 Go de mémoire, 2 CPU (plafonds fixes du POC) | au-delà : le conteneur est tué, l'échec consigné comme les autres |

## 4. Activation (opt-in)

1. **Construire l'image dédiée** (une fois, Docker démarré) :

   ```bash
   docker build -t maestro-sandbox:latest infra/sandbox
   ```

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
- **Tests** (#107) : câblage, commande durcie et shim couverts par
  `tests/test_isolation.py` ; le lancement **réel** d'un conteneur exige Docker,
  absent des runners CI — procédure de smoke test manuelle dans
  [docs/19 §4](./19-securite-modele-de-menace.md).
