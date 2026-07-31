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

Trois fichiers cohabitent sous la racine (remplaçable par `MAESTRO_MCP_DIR`) :

- **`<agent>.json`** (`{"serveurs": [...]}`) — la déclaration **héritée** d'un
  agent (#104), un fichier par agent. Chaque serveur est une **commande locale**
  (`type` « stdio » : `commande` + `args` + `env`) ou un **endpoint distant**
  (« sse »/« http » : `url` + `headers`). Format et exemple : [docs/04 §6](../../docs/04-specifications-agents.md).
- **`pool.json`** (`{"integrations": [...]}`) — le **pool projet** (#130) : une
  intégration = un `id` stable + une déclaration `ServeurMcp`, **déclarée une
  fois** (secret par `${VAR}` compris), partageable entre agents.
- **`activations.json`** (`{"<agent>": ["id", …]}`) — les intégrations du pool
  **activées** par agent (#130).

- **Composition** (`maestro.agents.mcp.McpStore.lire`) : pour un agent, la
  déclaration héritée **puis** les intégrations du pool activées pour lui. Sans
  activation, le résultat est exactement la déclaration héritée — la
  **rétro-compatibilité** du #104 (le pool n'est pas lu). En cas de collision de
  `serveur.nom`, l'héritée l'emporte. Migration héritée → pool **outillée**
  (`composer_migration`/`migrer`), jamais imposée.
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
  fichier. La fiche agent de la page `/catalogue` reste la vue **lecture seule**
  de la composition (valeurs masquées).

## Déclarations en place

- [`qa.json`](./qa.json) — serveur **GitLab** (`@zereight/mcp-gitlab`, stdio via
  `npx`) pour le pilote gestion de tickets (#106) : l'agent QA lit et crée des
  tickets du backlog au fil d'un run. Restreint au toolset `issues` en mode
  `modify` (ni suppression, ni merge) ; token via `${GITLAB_TOKEN}` — voir
  [docs/16](../../docs/16-pilote-mcp-tickets-gitlab.md).
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

## Registre curé (bibliothèque recherchable, #131)

Un **fichier par agent** répond à *« quels serveurs cet agent monte-t-il ? »*
mais pas à *« quelles intégrations existe-t-il, et comment les configurer ? »*.
C'est le rôle du **registre curé** (`maestro.agents.mcp_registry`, parent #129) :
une bibliothèque de **templates** de serveurs MCP, recherchable par nom/tag,
chaque entrée portant transport, gabarit d'exécution `${VAR}`, **mode d'auth**
([docs/21](../../docs/21-configuration-mcp.md)), variables à fournir et lien de
procédure côté outil. Le seed initial (GitLab, Slack, Figma officiel) dérive des
déclarations ci-dessus.

- Un **template** (`EntreeRegistre`) est versionné et agnostique du modèle ;
  l'**instanciation** (`RegistreMcp.instancier`) le transforme en `ServeurMcp`
  montable — c'est l'unique voie template → liaison.
- **Garde-fou supply-chain** ([docs/19](../../docs/19-securite-modele-de-menace.md)) :
  *découverte ≠ installation*. Seule une entrée de l'**allowlist curée** (le seed
  `SEED`, en clair et revu en revue de code) est instanciable — jamais de
  `npx -y <pkg arbitraire>`.
- Exposé par l'API : `GET /api/mcp/registre` (liste + `?q=` recherche) et
  `GET /api/mcp/registre/{id}`. Recherche + garde-fou testés dans
  [`tests/test_mcp_registry.py`](../../tests/test_mcp_registry.py).

## Secrets — jamais en clair, chiffrés côté serveur (#132)

Contrairement aux dépôts voisins (données d'exécution non commitées), les
déclarations écrites ici (`<agent>.json`, `pool.json`) sont de la
**configuration versionnée** : elles se commitent avec le dépôt. Les **secrets
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
