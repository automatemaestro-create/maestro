# Configuration MCP — ce qui dépend du client, ce qui dépend de l'outil (ticket #126)

**Version :** 0.2 — §3 mis à jour après livraison du parent #129 (pool projet,
bibliothèque curée, secrets chiffrés, écriture depuis la Control Tower).

> ⚠ **Revue datée (juillet 2026).** Ce qu'elle *classe* — les trois modes
> d'authentification — n'a pas bougé et reste le contrat. Ce qu'elle *situe* a
> bougé une fois : la **forge du produit** est passée de GitLab à **GitHub** le
> 2026-08-24 (#412), et §2 le dit ligne par ligne. Partout ailleurs dans cette
> page, « GitLab » est un **exemple d'outil émetteur de PAT** — vrai hier comme
> aujourd'hui, et indépendant de la forge que ce projet-ci utilise.
Revue transverse des intégrations MCP en place (GitLab #106, Slack #105,
Figma #115/#125) sous un angle mis au jour par l'évaluation du serveur MCP
officiel Figma : dans une configuration MCP, **tout ne se décide pas au même
endroit**. Cette page classe chaque intégration par mode d'authentification et
en tire les pré-requis pour la cible produit — **configurer les serveurs MCP
depuis la Control Tower, pour n'importe quel fournisseur de modèle** — désormais
réalisée par le parent #129 (§3.3).

> **Principe** : le protocole MCP est standardisé, mais la responsabilité se
> partage. La **déclaration du serveur** (où et comment le brancher) appartient
> au **client** MCP — pour Maestro : le fichier hérité `core/mcp/<agent>.json`
> (#104) ou, depuis #129, le **pool projet** configurable depuis la Control
> Tower (§3.3). L'**émission du secret** (procédure, scopes, durée de vie)
> appartient à l'**outil** — GitLab émet ses PAT, Slack ses tokens de bot,
> Figma ses tokens OAuth. L'**OAuth** est le cas hybride : le token est émis par
> l'outil mais négocié et détenu **par client** — et l'outil peut restreindre
> quels clients ont ce droit.

---

## 1. La grille : trois lieux de décision

| Ce qui se décide… | …dépend de | Exemples |
| --- | --- | --- |
| Déclaration du serveur (fichier, syntaxe, transport) | du **client** MCP | Claude Code : `.mcp.json` ; Cursor : `.cursor/mcp.json` ; **Maestro : `core/mcp/<agent>.json`** (format [docs/04 §6](./04-specifications-agents.md)) |
| Émission du secret (procédure, scopes, durée de vie, révocation) | de l'**outil** | PAT créé dans GitLab, token de bot installé dans l'admin Slack, token OAuth émis par Figma |
| Négociation OAuth (consentement, détention du token) | des **deux** | le token est émis par l'outil, mais chaque client obtient le sien — un token OAuth ne se partage pas entre clients comme un PAT |

Conséquence pratique : un serveur MCP donné (celui de Figma, de GitLab, de
Slack) est utilisable depuis n'importe quel client — l'investissement est côté
outil, pas côté client. Mais le **mode d'auth** de l'outil détermine ce qu'un
client autonome comme Maestro peut faire seul, d'où la classification qui suit.

## 2. Classification des intégrations en place

| Intégration | Déclaration | Mode d'auth | Obtention | Durée de vie |
| --- | --- | --- | --- | --- |
| **Forge — GitHub** (#412, *depuis le 2026-08-24*) | [core/mcp/qa.json](../core/mcp/qa.json) → `${GITHUB_TOKEN}` | **Token statique saisissable** | PAT *fine-grained* créé dans l'UI GitHub par un humain, borné au dépôt du projet (Issues + Pull requests) — procédure dans [core/mcp/README.md](../core/mcp/README.md#obtention-du-token-github) | Durable (expiration choisie à la création, révocable) |
| **Forge — GitLab** (#106, *défaut du produit jusqu'au 2026-08-24*) | ~~core/mcp/qa.json~~ → entrée `gitlab` du **registre curé** → `${GITLAB_TOKEN}` | **Token statique saisissable** | PAT (`glpat-…`, scope `api`) créé dans l'UI GitLab par un humain ([docs/16 §2.3](./16-pilote-mcp-tickets-gitlab.md)) | Durable (expiration choisie à la création, révocable) ; la démo a utilisé le token OAuth du CLI `glab`, qui expire en ~2 h — le PAT est la forme recommandée |
| **Slack** (#105) | [core/mcp/devops.json](../core/mcp/devops.json) → `${SLACK_BOT_TOKEN}` | **Token statique saisissable** | Bot User OAuth Token (`xoxb-…`) obtenu en installant l'app sur le workspace, scopes `chat:write` + `channels:read` ([docs/15 §2](./15-pilote-mcp-slack.md)) | N'expire pas par défaut (révoqué en désinstallant l'app) |
| **Figma — pont communautaire** (#115) | [core/mcp/designer.json](../core/mcp/designer.json) → `${FIGMA_CHANNEL}` | **Sans token** (appairage) | Canal WebSocket affiché par le plugin compagnon dans Figma ; l'agent agit avec la session Figma de l'utilisateur | Le temps de la session du plugin — aucun token d'API n'existe |
| **Figma — serveur officiel** (#125, en revue) | designer.json (variante) → `${FIGMA_OAUTH_TOKEN}` | **OAuth verrouillé — clients pré-approuvés seulement** | Token `mcp:connect` émis par Figma **uniquement** à l'issue d'un flux OAuth mené par un client approuvé (Claude Code…) ; l'humain le recopie dans l'environnement de Maestro ([docs/20 §6](./20-pilote-mcp-figma.md)) | Court ; refresh **non** géré par Maestro — token expiré = serveur refusé au montage, renouvellement humain |

Deux constats sur pièces (sondes du 2026-07-19, détail [docs/20 §6](./20-pilote-mcp-figma.md)) fondent la
dernière ligne : un PAT Figma `figd_` valide est refusé (401) quels que soient
ses scopes — **aucun token créable à la main dans l'UI Figma n'ouvre le MCP
officiel** — et l'enregistrement dynamique de client OAuth est fermé
(`POST /v1/oauth/mcp/register` → 403). Maestro ne peut donc ni faire créer le
token par l'utilisateur, ni mener son propre flux OAuth : il **importe** le
token d'un client approuvé.

### 2.1 Le quatrième mode : `sans_secret` (#271)

L'élargissement de la bibliothèque curée (#271, [§3.4](#34-la-bibliothèque-élargie-271))
a ajouté une quatrième valeur à `MODES_AUTH` : **`sans_secret`**.

⚠ **La classification ci-dessus n'a pas bougé pour autant, et il faut lire
pourquoi avant d'en ajouter une cinquième.** Les trois modes classent *comment
un secret s'obtient* — question qui n'a **aucun objet** quand le serveur n'en
demande aucun. `sans_secret` est donc le **cas dégénéré** de la classification,
pas une extension de sa règle : rien à émettre, rien à saisir, rien à stocker,
rien à faire expirer. Un serveur y entre parce qu'il tourne en local
(`fetch`, `memory`, Playwright…) ou parce que son endpoint est **public**
(documentation Cloudflare, DeepWiki).

Trois choses à ne pas défaire :

- il est porté par **`mode_auth`** et non par un booléen à côté, pour que l'UI
  n'ait **qu'un** champ à regarder pour choisir son formulaire — le sien
  n'ayant, précisément, aucun champ ;
- l'invariant « toute entrée déclare au moins une variable » ne vaut donc
  **pas** ici, et c'est délibéré : l'y forcer reviendrait à inventer une
  variable pour satisfaire un test, et surtout à **fermer la bibliothèque aux
  utilitaires locaux**, qui sont parmi les serveurs les plus utilisés de
  l'écosystème. Sans ce mode, le critère « couvrir les plus utilisés » était
  inatteignable ;
- il élargit ce que `maestro.agents.secrets` **accepte** sans que rien ne l'y
  amène : une entrée `sans_secret` ne déclare aucune variable, donc aucun
  secret n'est jamais enregistré sous ce mode.

## 3. Cible Control Tower : configurer les MCP pour tout modèle

Objectif produit (consigné au ticket #126) : la configuration des serveurs MCP
doit à terme se faire **depuis la Control Tower**, quel que soit le fournisseur
de modèle de l'agent. La revue relève ce qui le permet déjà et ce qu'il faudra
construire.

### 3.1 Acquis — rien à ajuster dans les configs actuelles

- **Indépendance du fournisseur de modèle** : les déclarations
  `core/mcp/*.json` ne contiennent rien de spécifique à un fournisseur — c'est
  du MCP standard (commande stdio ou endpoint http + `${VAR}`), monté par la
  couche SDK du provider sur les exécutions outillées ([docs/04 §6.3](./04-specifications-agents.md)).
  Changer le modèle d'un agent ne change pas sa déclaration MCP.
- **Aucun secret en clair** : toutes les déclarations référencent
  l'environnement (`${VARIABLE}`), résolu au montage ; les valeurs sont
  expurgées des sorties (registre de rédaction). La séparation
  « déclaration versionnée / secret dans l'environnement » est exactement la
  coupure dont une UI de configuration a besoin.
- **Serveurs optionnels** (#125) : une voie dont le secret manque est omise du
  montage sans échec — une UI peut donc proposer des intégrations non encore
  configurées sans casser les runs.

### 3.2 Les trois parcours de saisie dans l'UI

La classification du §2 impose à la page de configuration de distinguer
**trois parcours**, un par mode d'auth (tous trois livrés par #132/#133, §3.3)
— plus un **quatrième cas qui n'est pas un parcours** : `sans_secret` (§2.1),
où le formulaire ne porte aucun champ et où « ajouter au pool » suffit :

1. **Secret statique saisissable** (GitLab, Slack, la plupart des PAT) : un
   champ secret + un lien vers la procédure de création côté outil. Stockage
   chiffré côté serveur (chantier sécurité #102), jamais dans la déclaration.
2. **Appairage sans token** (pont Figma communautaire) : un champ non secret
   (canal) à renouveler à chaque session de plugin — l'UI doit le présenter
   comme éphémère, pas comme un secret à conserver.
3. **Token importé et expirable** (OAuth verrouillé, Figma officiel) : un
   champ d'import + un **état de validité visible** (token expiré = serveur
   refusé au montage) et une procédure guidée de renouvellement. Tant que
   Maestro n'est pas un client OAuth approuvé par l'outil, ce parcours reste
   dépendant d'un client tiers ; si l'outil ouvre un jour l'enregistrement de
   clients, ce mode peut migrer vers un vrai flux OAuth intégré (consentement
   dans le navigateur, refresh géré par la Control Tower).

### 3.3 Réalisation — le parent #129

La cible du §3 est portée par le parent **#129** (« Bibliothèque de serveurs MCP
configurables depuis la Control Tower »), en cinq lots dont le socle est en
place. L'architecture livrée, détaillée dans [docs/04 §6.4](./04-specifications-agents.md) :

- **Pool projet + activation par agent** (#130) : une intégration est déclarée
  **une fois** dans le pool (`pool.json`), le secret saisi une fois, puis
  **activée** par agent (`activations.json`). `McpStore.lire(agent)` **compose**
  la déclaration héritée (§3.1, `core/mcp/<agent>.json`) et les intégrations du
  pool activées ; **rétro-compatible** (sans activation, le comportement du #104
  est strictement préservé) et migration outillée (`migrer`).
- **Bibliothèque curée** (#131) : un registre de templates recherchable
  (`GET /api/mcp/registre`), chaque entrée guidant sa configuration selon son
  mode d'auth. **Garde-fou supply-chain** ([docs/19](./19-securite-modele-de-menace.md)) :
  seule une entrée de l'allowlist curée est instanciable — *découverte ≠
  installation*.
- **Secrets chiffrés côté serveur** (#132, parent #102) : le token vit dans le
  **coffre de l'agent**, chiffré au repos (Fernet), résolu au montage dans ce
  coffre seul (un agent ne voit que les siens). Les **trois parcours** du §3.2
  y sont implémentés (`SecretStore.enregistrer`/`renouveler`, état lisible sans
  déchiffrer).
- **Écriture depuis la Control Tower** (#133) : la Control Tower devient la
  **source en écriture** de cette config (page Paramètres #121 — bibliothèque,
  configuration, activation par agent), en remplacement de l'édition manuelle du
  fichier. La fiche agent du catalogue reste la vue lecture seule de la
  composition.

Tests sans réseau (#134) : composition et rétro-compat, registre et garde-fou,
secrets chiffrés et 3 parcours, et le **parcours de bout en bout** bibliothèque →
pool → activation → coffre → montage ([tests/test_mcp_config.py](../tests/test_mcp_config.py)).

### 3.4 La bibliothèque élargie (#271)

Le registre de #131 tenait en **quatre** entrées — les pilotes déjà versionnés
dans `core/mcp/`. Suffisant pour prouver le mécanisme, trop étroit pour ce que
la bibliothèque promet : rien n'y mettait en avant ce que l'écosystème utilise
réellement, et on ne pouvait donc **rien y découvrir**. #271 l'élargit à une
trentaine d'intégrations, sans toucher au garde-fou.

**Ce qui est curé, et comment.** Le registre reste **curé, jamais moissonné** :
aucun annuaire distant n'est branché, chaque entrée est écrite à la main, relue
en revue de code et versionnée. La `PROVENANCE` (module `mcp_registry`) porte
les sources et la **date de revue**, servies par
`GET /api/mcp/registre/provenance` et **affichées** au pied de la bibliothèque —
un registre curé qui ne dit pas d'où il vient demande une confiance qu'il ne
justifie pas. Cette date se met à jour **dans le même commit** que la liste :
une date qui ne bouge pas quand la liste bouge atteste une fraîcheur que
personne n'a vérifiée.

**La règle de curation, qui est une règle de sécurité.** Un gabarit qu'on ne
sait pas écrire **exactement** n'entre pas. Écrire `npx -y <paquet>` de mémoire,
c'est écrire une invitation au *typosquatting* dans une allowlist — l'inverse
exact de ce que [docs/19](./19-securite-modele-de-menace.md) protège. En
pratique cela privilégie les **endpoints HTTP officiels** (rien à exécuter,
l'URL est vérifiable) et les paquets attestés par une source. Deux serveurs de
référence très utilisés sont **écartés** pour cette raison, et c'est une limite
à connaître plutôt qu'un oubli : `filesystem` et `postgres` prennent leur
paramètre (racine, chaîne de connexion) **en argv**, or `maestro.agents.mcp.resolus`
ne résout les `${VAR}` que dans `env` et `headers` — une variable en argv ne
serait jamais remplacée, et le serveur démarrerait sur la chaîne littérale.

**Ce que l'élargissement ajoute au contrat d'une entrée** : `editeur` (qui
publie — c'est ce qui distingue un serveur officiel d'un pont communautaire, et
la recherche porte dessus) et `popularite`, un **palier** d'usage à quatre
valeurs (`USAGE_*`) qui met les plus courants en tête. Des paliers et non un
rang : les annuaires publics s'accordent sur l'ordre de grandeur, pas sur un
classement, et un palier n'oblige jamais à renuméroter ses voisins. À palier
égal l'ordre est **alphabétique** — stable, sans faux gagnant.

**Et la recherche ne rend plus un cul-de-sac** : sans résultat, l'écran propose
les **tags** des intégrations les plus courantes (cliquables) et le retour à la
liste entière, plutôt que de répéter que la requête ne donne rien.

**Le garde-fou, lui, n'a pas bougé d'une ligne** : `instancier` reste l'unique
voie template → liaison, l'allowlist *est* le registre, et `POST /api/mcp/pool`
refuse toujours un id inconnu. Figurer dans la bibliothèque ne configure rien —
le parcours reste bibliothèque → pool (geste humain, secret saisi) → activation
par agent. Un registre trois fois plus grand rend ce point **plus** important,
pas moins : c'est la découverte qui s'élargit, jamais l'installation.

**Couverture** : [tests/test_mcp_registry.py](../tests/test_mcp_registry.py) côté
registre — les pilotes historiques vérifiés par **inclusion** et non par égalité
(la liste exacte était le contrat tant que le registre tenait en quatre entrées ;
elle rendrait le test faux à chaque intégration ajoutée, c'est-à-dire chaque fois
qu'il a le plus de raisons d'être joué), le tri par palier puis par nom, la
provenance qui ne masque aucune entrée, et le garde-fou d'allowlist inchangé ;
`apps/web/tests/parametres-mcp.test.tsx` côté écran — la provenance affichée, le
panneau de chaque intégration, et la recherche sans résultat qui rend une piste
plutôt qu'un cul-de-sac. ⚠ Ce fichier **suit son sujet** : #270 sort la
bibliothèque des Paramètres et le renomme en conséquence, ses scénarios inchangés.
Le reste du comportement de l'**écran** Intégrations est suivi par **#663** — il
n'a pas pu être écrit avec les autres, sa PR n'étant pas encore mergée quand le
lot 6 s'est joué.

---

*Références : [docs/15](./15-pilote-mcp-slack.md) (Slack), [docs/16](./16-pilote-mcp-tickets-gitlab.md) (GitLab), [docs/20](./20-pilote-mcp-figma.md) (Figma, dont §6 pour la voie officielle), [core/mcp/README.md](../core/mcp/README.md) (socle des déclarations).*
