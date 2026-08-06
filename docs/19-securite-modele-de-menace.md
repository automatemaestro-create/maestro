# Renforcement sécurité — modèle de menace, activation, vérification (parent #102)

**Version :** 0.1
Page de synthèse du chantier **renforcement sécurité** (#102), livré en quatre
lots : isolation d'exécution (#108, [docs/17](./17-isolation-execution.md)),
secrets par agent (#109, [docs/18](./18-secrets-par-agent.md)), permissions par
agent et par outil (#110, [core/permissions/README](../core/permissions/README.md))
et tests + doc (#107, cette page). Elle consigne le **modèle de menace** commun
aux trois mécanismes, leur activation, la **vérification** (tests automatisés +
procédure manuelle du mode isolé) et les limites connues consolidées.

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
| Épuisement des ressources du poste | boucle, compilation, fork bomb | Plafonds du conteneur : 256 pids, 2 Go, 2 CPU ; time-out par tâche (#64) et plafond de tours (garde-fous moteur, hors chantier) | #108 |
| Vol de secrets d'un autre agent | agent compromis résolvant `${VAR}` d'autrui | Coffre **par agent** : la résolution MCP ne lit que le coffre de l'agent exécutant ; secret absent = serveur indisponible (échec propre), même si la variable existe dans le process | #109 |
| Fuite de secret en sortie | agent citant son token dans un livrable, trace, rapport | Registre de rédaction : toute valeur **servie** est masquée (`[secret masqué]`) à la consignation — le journal alimentant Langfuse, le pont Control Tower et les rapports, le masquage suit partout | #109 (socle #8) |
| Lecture de l'environnement hôte depuis le conteneur | code exécuté dans le conteneur | Environnement minimal : seules les 3 variables d'auth fournisseur entrent (`ENV_TRANSMISES`) ; les secrets MCP voyagent résolus en mémoire, jamais dans l'environnement du conteneur | #108/#109 |
| Action interdite via un outil | appel d'outil intégré ou MCP hors mandat | Politique **allow/deny par agent et par outil** : outils refusés retirés de la session, serveur MCP refusé jamais monté (secrets jamais résolus), le reste refusé **au vol** (hook PreToolUse) avec motif — violation tracée (`:refus-outil`), jamais fatale au run | #110 |
| Config MCP ambiante montée à l'insu | config utilisateur/projet/plugin du CLI | `strict_mcp_config` : la session ne monte que la liste déclarée de l'agent | #104 |
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
| **Prompt injection par le contenu lu** | `README`, commentaire, dépendance, **document téléversé** ([docs/24 §3.4](./24-projets-locaux-et-poste-de-travail.md)) | Contenu traité comme **donnée, jamais comme consigne** (prompts systèmes) ; actions sensibles maintenues derrière la validation, ce qui borne les dégâts |

La décision **D1** de [docs/24 §8](./24-projets-locaux-et-poste-de-travail.md) a été rendue le
2026-08-04 (#218) et la **Phase 7 a livré** : ce tableau décrit le modèle de menace **en
vigueur**, et non plus ce qui l'attend. Où chaque contre-mesure vit dans le code :

- **travail hors de la racine** — `maestro.sandbox.projet` (#224) dérive l'espace de travail :
  worktree Git sur la branche `maestro/<tâche>` si le projet est versionné, copie de son
  périmètre sinon. La racine elle-même n'est jamais le répertoire de travail d'un agent (EF-36),
  ni un montage du conteneur en mode isolé (#226, [docs/17 §3](./17-isolation-execution.md)) ;
- **application sous validation humaine** — `maestro.controltower.validation.appliquer_sous_validation`
  (#227, EF-37) soumet « appliquer ce travail ? » au **même** validateur que les autres actions
  sensibles (EF-08), diff en pièce jointe. Sur refus, rien n'est écrit et le travail reste
  consultable ; sans validateur, l'application est refusée (fail-safe des garde-fous, #9) ;
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

## 3. Activation (récapitulatif)

Chaque mécanisme est **opt-in** et détaillé dans sa page ; l'ensemble tient
dans le `.env` et les dépôts `core/` :

| Mécanisme | Activation | Défaut |
|---|---|---|
| Mode isolé (#108) | `MAESTRO_ISOLATION=conteneur` (+ image construite : `docker build -t maestro-sandbox:latest infra/sandbox`) — [docs/17 §4](./17-isolation-execution.md) | exécution sur l'hôte |
| Coffre par agent (#109) | écrire le **premier** `core/secrets/<agent>.json` (bascule pour **tous** les agents) — [docs/18 §3](./18-secrets-par-agent.md) | environnement du process |
| Permissions (#110) | écrire `core/permissions/<agent>.json` (`{"allow": [...], "deny": [...]}`) — [README](../core/permissions/README.md) | tout permis (outils du profil) |

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
  `maestro/sandbox/` changent.
