# core/mcp — Serveurs MCP : pool projet, activation par agent, déclarations héritées

Dépôt des **configurations de serveurs MCP** (ticket #104, parent #101) : un
agent peut se voir brancher des capacités externes (Slack, gestion de tickets,
Figma, cloud…) via le Model Context Protocol — le moteur monte les serveurs
configurés sur ses exécutions outillées, sans connecteur ad hoc. Depuis le
parent **#129**, deux modèles cohabitent sous cette racine : la **déclaration
héritée** (un fichier par agent, #104) et le **pool projet + activation par
agent** (#130), configurable **depuis la Control Tower** et alimenté par une
**bibliothèque curée** (#131) ; `McpStore.lire(agent)` **compose** les deux.

## Fonctionnement

Quatre fichiers cohabitent sous la racine (remplaçable par `MAESTRO_MCP_DIR`) :

- **`<agent>.json`** (`{"serveurs": [...]}`) — la déclaration **héritée** d'un
  agent (#104), un fichier par agent. Chaque serveur est une **commande locale**
  (`type` « stdio » : `commande` + `args` + `env`) ou un **endpoint distant**
  (« sse »/« http » : `url` + `headers`). Format et exemple : [docs/04 §6](../../docs/04-specifications-agents.md).
- **`pool.json`** (`{"integrations": [...]}`) — le **pool projet** (#130) : une
  intégration = un `id` stable + une déclaration `ServeurMcp`, **déclarée une
  fois** (secret par `${VAR}` compris), partageable entre agents.
- **`activations.json`** (`{"<agent>": ["id", …]}`) — les intégrations du pool
  **activées** par agent (#130).
- **`admissions.json`** (`{"admissions": [...]}`) — le **journal des admissions**
  (#678, parent #673) : les entrées du registre MCP officiel qu'un humain a fait
  entrer dans l'allowlist, chacune **figée** à la version admise, avec sa source
  (nom amont, version, éditeur, dépôt, horodatage du miroir) et le geste (qui,
  quand, pourquoi). Écrit par la Control Tower
  (`maestro.agents.mcp_admission.MagasinAdmissions`) — c'est une **donnée
  d'installation**, comme le pool, pas du code relu en revue comme le seed de
  `maestro/agents/mcp_registry.py`.

- **Composition** (`maestro.agents.mcp.McpStore.lire`) : pour un agent, la
  déclaration héritée **puis** les intégrations du pool activées pour lui. Sans
  activation, le résultat est exactement la déclaration héritée — la
  **rétro-compatibilité** du #104 (le pool n'est pas lu). En cas de collision de
  `serveur.nom`, l'héritée l'emporte. Migration héritée → pool **outillée**,
  jamais imposée — et **deux outils**, qui ne visent pas la même situation :
  - `composer_migration`/`migrer` (#130) composent **tout le dépôt d'un coup**,
    et le pool qu'ils écrivent *est* le résultat : c'est un **remplacement
    intégral**, donc l'outil d'un projet qui n'a encore rien au pool (sur un pool
    vivant, il effacerait ce que la Control Tower y a ajouté depuis) ;
  - `migrer_agent(agent)` (#263) traite **un agent** et **ajoute** : les
    intégrations déjà là restent, celles de l'agent s'y greffent — mutualisées
    quand leur déclaration stockée est identique à une intégration existante —,
    ses activations s'ajoutent aux siennes sans écraser celles des autres, et son
    `<agent>.json` part. C'est ce que joue `POST /api/mcp/migration/{agent}`,
    donc le bouton « Migrer vers le pool projet » de la fiche d'un agent.
    L'API y rapproche au passage chaque serveur d'une entrée de la
    **bibliothèque** dont l'instanciation lui est *exactement* égale, et l'inscrit
    sous l'**id de cette entrée** en gardant son nom de montage : sans quoi le
    serveur `forge` de `qa.json` entrerait au pool hors bibliothèque, sans mode
    d'auth et sous une alerte, alors qu'il *est* l'entrée `github`.
    **Aucun secret n'est redemandé** — une déclaration héritée porte déjà ses
    références `${VAR}`, résolues au montage comme avant.
- **Validé à la lecture** : une source invalide (déclaration, pool, activation
  vers une intégration absente du pool) est refusée avec sa cause exacte —
  échec de tâche propre, jamais un montage à moitié.
- Effet à l'exécution (`maestro/engine/executor.py`, relu **à chaud** à chaque
  tâche, comme les playbooks #78) : les serveurs sont montés par la **couche
  SDK** (`ModelProvider.run_agent`) sur les exécutions **outillées** de
  l'agent — le chemin texte n'expose aucun outil, MCP compris. Un serveur
  indisponible produit une erreur propre et tracée, **jamais relancée**
  (docs/04 §6.3).
- **Écriture** : le pool et les activations sont **écrivables**
  (`ecrire_pool`/`ecrire_activations`, atomiques et versionnés) — la Control
  Tower en devient la source (#133), en remplacement de l'édition manuelle du
  fichier. Deux écrans s'en partagent la charge depuis #263, et la frontière est
  celle de la **portée du geste** : l'écran **Intégrations** règle le *projet*
  (ajouter au pool, reconfigurer un secret, retirer du pool — ce qui désactive
  partout), l'onglet **MCP & permissions** d'un agent règle *cet agent* (activer,
  désactiver, migrer ses déclarations héritées) — et il ajoute au pool aussi,
  parce qu'équiper un agent d'une intégration qui n'existe pas encore est une
  seule intention et non deux. Un interrupteur éteint sur une fiche **ne retire
  rien du pool**, et l'écran le dit là où le geste se fait.

## Déclarations en place

- [`qa.json`](./qa.json) — serveur **de forge** pour le pilote gestion de
  tickets (#106) : l'agent QA lit et crée des tickets du backlog au fil d'un
  run. Depuis **#412** c'est **GitHub** (serveur MCP officiel de GitHub, endpoint
  distant `https://api.githubcopilot.com/mcp/`, http), et non plus GitLab —
  voir « La forge du produit » ci-dessous. Token via `${GITHUB_TOKEN}`.
  La déclaration d'origine (GitLab, `@zereight/mcp-gitlab` en stdio via `npx`,
  toolset `issues` en mode `modify`) est celle que décrit
  [docs/16](../../docs/16-pilote-mcp-tickets-gitlab.md), rapport **daté** du
  pilote #106 : elle s'y lit au passé.
- [`devops.json`](./devops.json) — serveur **Slack** (pilote #105) : l'agent
  `devops` poste les notifications de supervision d'un run (fin de run,
  validation humaine en attente) via `maestro-run --notifier devops`.
  Configuration et démo : [docs/15](../../docs/15-pilote-mcp-slack.md).
- [`designer.json`](./designer.json) — serveur **MCP officiel Figma**
  (`https://mcp.figma.com/mcp`, http, bascule #128 après le pilote #115 et
  l'évaluation #125) : l'agent designer crée et lit des éléments d'un fichier
  Figma (`use_figma`, `get_design_context`, `create_new_file`…). Monté
  seulement quand un humain a fourni un token OAuth (`${FIGMA_OAUTH_TOKEN}`,
  scope `mcp:connect`) — Maestro ne mène **aucune authentification
  automatique** ; le serveur est **optionnel** (`"optionnel": true`) : sans
  token, il est omis du montage, sans échec. L'ancien mode « Talk to Figma »
  (pilote #115 : plugin compagnon + relais WebSocket + canal d'appairage) est
  retiré de la configuration active — trace historique et repli documenté dans
  [docs/20](../../docs/20-pilote-mcp-figma.md).

## La forge du produit : un défaut, et un catalogue (#412)

La bascule #343/#344 a porté **l'outillage** du dépôt sur GitHub ; le **produit**
disait encore GitLab. La question n'était pas le mot mais le **rôle**, et il a
deux réponses parce qu'il y a deux objets :

- **`qa.json` est un défaut**, pas un catalogue : c'est ce que l'agent QA monte
  réellement à chaque exécution outillée. Un défaut du produit **suit la forge du
  projet** — il est donc passé à **GitHub**.
- **Le registre curé est une bibliothèque** (#131) : il répond à *« quelles
  intégrations existe-t-il ? »*, jamais à *« laquelle ce projet utilise-t-il ? »*.
  Il porte donc **GitHub et GitLab côte à côte**, et c'est l'entrée **GitHub** qui
  y manquait. Retirer `gitlab` aurait été la vraie dérive : le seed **est**
  l'allowlist du dépôt (garde-fou supply-chain ci-dessous), donc l'en sortir
  **interdirait** de monter un serveur GitLab — alors qu'un projet outillé par
  Maestro n'est pas forcément sur la forge du nôtre. ⚠ Depuis #678 l'allowlist ne
  se réduit plus au seed (« Trois sources, une allowlist » ci-dessous), mais
  l'argument tient : une admission est un **geste**, pas un filet.

Deux conséquences à connaître :

- **Le serveur s'appelle `forge`, plus `gitlab`.** Le nom d'une liaison est le
  préfixe de ses outils (`mcp__forge__…`) : le figer sur une marque, c'est
  reprogrammer les playbooks le jour où la forge change. Aucun playbook ne
  référençait `mcp__gitlab__…` — les seules occurrences sont dans docs/16, qui
  raconte la démo #106 au passé.
- **La voie est `optionnel: true`** (canal #125). `${GITHUB_TOKEN}` est une clé
  **neuve** : l'outillage du dépôt s'authentifie par le CLI `gh`, pas par le
  `.env`, donc aucun poste ne la porte. Non optionnelle, la bascule ferait
  **échouer** toute exécution outillée du QA au premier run, sur une forge
  correcte. Sans jeton, la voie est simplement omise du montage.

`${GITLAB_TOKEN}` **reste déclarée** dans `.env.example` et n'est pas orpheline :
elle sert l'entrée `gitlab` du registre, et elle est la seule voie de lecture de
l'**archive** GitLab ([docs/27 §11](../../docs/27-decision-gitlab-vers-github.md)).
Elle a seulement cessé d'être le défaut.

### Obtention du token GitHub

Un **PAT à portée restreinte** (*fine-grained*), créé par un humain dans
*Settings > Developer settings > Personal access tokens* : dépôt **unique** (celui
du projet), permissions **Issues** et **Pull requests** en lecture/écriture, rien
d'autre. Le jeton est **nominatif** — les écritures portent votre nom.

C'est ici une différence avec le pilote GitLab, et elle est voulue : là-bas le
périmètre était borné par la **configuration du serveur**
(`GITLAB_TOOLSETS=issues`, `GITLAB_PERMISSION_MODE=modify`), donc par une valeur
qu'une déclaration versionnée peut se voir changer. Ici il est borné par les
**scopes du jeton**, côté GitHub — ce qu'aucune modification du dépôt ne peut
élargir.

La valeur elle-même ne vit pas dans le fichier : `${GITHUB_TOKEN}` est résolue au
montage depuis le coffre chiffré de l'agent (#132, section « Secrets » ci-dessous).

## Registre curé (bibliothèque recherchable, #131)

Un **fichier par agent** répond à *« quels serveurs cet agent monte-t-il ? »*
mais pas à *« quelles intégrations existe-t-il, et comment les configurer ? »*.
C'est le rôle du **registre curé** (`maestro.agents.mcp_registry`, parent #129) :
une bibliothèque de **templates** de serveurs MCP, recherchable par nom/tag,
chaque entrée portant transport, gabarit d'exécution `${VAR}`, **mode d'auth**
([docs/21](../../docs/21-configuration-mcp.md)), variables à fournir et lien de
procédure côté outil. Le seed (GitHub, GitLab, Slack, Figma officiel) dérive des
déclarations ci-dessus — **GitLab excepté**, qui n'équipe plus aucun agent et n'y
figure qu'au titre de la bibliothèque (#412).

- Un **template** (`EntreeRegistre`) est versionné et agnostique du modèle ;
  l'**instanciation** (`RegistreMcp.instancier`) le transforme en `ServeurMcp`
  montable — c'est l'unique voie template → liaison.
- **Garde-fou supply-chain** ([docs/19](../../docs/19-securite-modele-de-menace.md)) :
  *découverte ≠ installation*. Seule une entrée de l'**allowlist** est
  instanciable — jamais de `npx -y <pkg arbitraire>`.
- Exposé par l'API : `GET /api/mcp/registre` (liste + `?q=` recherche) et
  `GET /api/mcp/registre/{id}`. Recherche + garde-fou testés dans
  [`tests/test_mcp_registry.py`](../../tests/test_mcp_registry.py).

### Trois sources, une allowlist (#677, #678)

Depuis le parent #673 la bibliothèque **découvre** dans le registre MCP officiel
au lieu d'une liste figée, sans qu'une entrée découverte devienne pour autant
montable. Trois sources, et la table dit tout :

| `source` | d'où | `curee` | montable |
|---|---|---|---|
| `curee` | `SEED`, écrit à la main, relu en revue de code | `true` | oui |
| `admise` | le registre officiel, **plus** un geste humain tracé | `true` | oui |
| `decouverte` | le registre officiel seul (miroir `core/mcp-amont/`) | `false` | **non** |

⚠ `curee` (le booléen) et `source` (les trois valeurs) ne répondent pas à la même
question : le booléen dit « **montable ?** » — c'est lui que le garde-fou lit —,
la source dit « **d'où ça vient ?** ». Une admise est donc `curee: true` et
`source: "admise"`, sans contradiction.

La **porte d'admission** (`maestro.agents.mcp_admission`, #678) est le geste qui
fait passer de la troisième ligne à la deuxième :

- `POST /api/mcp/admissions` **admet** — enregistre l'entrée traduite **figée**
  avec sa source et le geste. Le figement est le cœur : une nouvelle version
  amont ne change **pas** la version admise, elle produit un signal ;
- `POST /api/mcp/admissions/{id}/revocation` **révoque** — l'entrée sort de
  l'allowlist, l'admission reste au journal (c'est ce qui permet aux refus de la
  nommer), et **rien n'est démonté** : un serveur déjà dans le pool y reste, avec
  son `alerte` ;
- `GET /api/mcp/admissions` rend le journal, les **signaux** que l'amont a émis
  depuis (`deprecated`, `deleted`, disparition, version plus récente) et la
  **politique** qui garde la porte (`MAESTRO_MCP_ADMISSION_POLITIQUE` — le point
  où une organisation branche sa revue ou son scan ; par défaut, le geste humain
  suffit) ;
- `POST /api/mcp/pool` refuse une découverte en **nommant le geste manquant**, et
  une entrée révoquée en disant qui l'a retirée et quand.

Rien n'est jamais retiré en silence, et rien n'est jamais retiré d'office : ce
qui est automatique est la **détection** de l'écart, jamais le verdict.

## Secrets — jamais en clair, chiffrés côté serveur (#132)

Contrairement aux dépôts voisins (données d'exécution non commitées), les
déclarations écrites ici (`<agent>.json`, `pool.json`, `admissions.json`) sont de
la **configuration versionnée** : elles se commitent avec le dépôt — et pour
`admissions.json` c'est le point même du dispositif, un journal d'autorisations
que l'équipe doit pouvoir relire. Les **secrets
n'y figurent jamais en clair** — les valeurs d'`env`/`headers` référencent
l'environnement (`${VARIABLE}`), résolu au montage. Depuis #132 (parent #102),
la valeur elle-même vit dans le **coffre de l'agent**
(`maestro.agents.secrets.SecretStore`, dépôt voisin `core/secrets/`, gitignoré),
**chiffré au repos** (Fernet), résolu dans ce coffre seul — un agent ne voit que
ses propres secrets. Trois modes d'auth ([docs/21 §3.2](../../docs/21-configuration-mcp.md)) :
token statique chiffré, valeur d'appairage éphémère, token OAuth importé
expirable.

## Tests

- Socle #104 (validation à la lecture, résolution des secrets, montage par le
  moteur, application à chaud, échecs propres, couture SDK) rejoué sans réseau
  dans [`tests/test_mcp.py`](../../tests/test_mcp.py) ; volet catalogue
  (lecture seule, valeurs masquées, **composition pool ∩ activation**) dans
  [`tests/test_controltower.py`](../../tests/test_controltower.py).
- Parent #129 (sans réseau) : pool ∩ activation + rétro-compat
  [`tests/test_mcp_pool.py`](../../tests/test_mcp_pool.py) (#130), registre +
  recherche + garde-fou [`tests/test_mcp_registry.py`](../../tests/test_mcp_registry.py)
  (#131), secrets chiffrés + 3 parcours
  [`tests/test_secrets_chiffrement.py`](../../tests/test_secrets_chiffrement.py)
  (#132), et le **parcours de bout en bout** (bibliothèque → pool → activation →
  coffre chiffré → composition → montage)
  [`tests/test_mcp_config.py`](../../tests/test_mcp_config.py) (#134).

Guide : [docs/07 §6.7](../../docs/07-guide-de-demarrage.md).

Classification des **modes d'authentification** de ces serveurs (token statique
/ appairage sans token / OAuth verrouillé) et pré-requis pour leur
configuration depuis la Control Tower : [docs/21](../../docs/21-configuration-mcp.md) (#126).
