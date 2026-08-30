# Configuration MCP — ce qui dépend du client, ce qui dépend de l'outil (ticket #126)

**Version :** 0.3 — §3 mis à jour après livraison du parent #129 (pool projet,
bibliothèque curée, secrets chiffrés, écriture depuis la Control Tower), puis du
parent #673 : la bibliothèque a **trois sources** et une **porte d'admission**
(§3.5, §3.6), et le §3.4 est renversé sur les deux points que la fédération a
rendus faux.

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
  mode d'auth. **Garde-fou supply-chain**
  ([docs/19 §2.3](./19-securite-modele-de-menace.md)) : seule une entrée de
  l'**allowlist** est instanciable — *découverte ≠ installation*. ⚠ Depuis #673
  la bibliothèque est plus large que l'allowlist (§3.5) : celle-ci est le seed
  curé **plus** ce qu'un geste humain y a admis (§3.6).
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

> ⚠ **Cette section décrit le dispositif de #271, où la bibliothèque n'avait
> qu'une source et où l'allowlist *était* le registre. Ce n'est plus vrai** : le
> parent #673 lui a donné une seconde source — le miroir du registre MCP officiel
> — et une **porte d'admission**. Ce qui suit reste exact de la **source curée**,
> qui n'a pas bougé d'une ligne, et sa **règle de curation** est toujours en
> vigueur (elle est citée depuis le code). Les deux énoncés que la fédération a
> renversés sont corrigés sur place et signalés `↯`. La vue d'ensemble des trois
> sources est au **[§3.5](#35-la-bibliothèque-fédérée-673)**, la porte
> d'admission au **[§3.6](#36-la-porte-dadmission-678-parent-673)**.

Le registre de #131 tenait en **quatre** entrées — les pilotes déjà versionnés
dans `core/mcp/`. Suffisant pour prouver le mécanisme, trop étroit pour ce que
la bibliothèque promet : rien n'y mettait en avant ce que l'écosystème utilise
réellement, et on ne pouvait donc **rien y découvrir**. #271 l'élargit à une
trentaine d'intégrations, sans toucher au garde-fou.

**Ce qui est curé, et comment.** ↯ Cette liste-ci reste **curée** : chaque entrée
est écrite à la main, relue en revue de code et versionnée. Elle n'est **plus la
seule source** de la bibliothèque — un annuaire distant *est* branché depuis
#673, moissonné dans un miroir local (§3.5) —, et c'est le seul mot de ce
paragraphe que la fédération a renversé : *jamais moissonnée* était vrai de la
bibliothèque entière, il ne l'est plus que de cette source-ci. La `PROVENANCE`
(module `mcp_registry`) porte les sources et la **date de revue**, servies par
`GET /api/mcp/registre/provenance` et **affichées** au pied de la bibliothèque —
un registre curé qui ne dit pas d'où il vient demande une confiance qu'il ne
justifie pas. Cette date se met à jour **dans le même commit** que la liste :
une date qui ne bouge pas quand la liste bouge atteste une fraîcheur que
personne n'a vérifiée. Depuis #677 elle a deux sœurs — `ProvenanceDecouverte` et
la provenance des admises —, rendues **à côté** et jamais fondues dans la
sienne : une liste curée se date par sa revue humaine, un miroir par son dernier
rafraîchissement, une admission par le geste qui l'a posée.

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
voie template → liaison, et `POST /api/mcp/pool` refuse toujours un id hors
allowlist. Figurer dans la bibliothèque ne configure rien — le parcours reste
bibliothèque → pool (geste humain, secret saisi) → activation par agent. Un
registre trois fois plus grand rend ce point **plus** important, pas moins :
c'est la découverte qui s'élargit, jamais l'installation.

> ↯ **« L'allowlist *est* le registre » a cessé d'être vrai avec #677/#678**, et
> c'est le renversement le plus facile à mal lire du chantier. Le garde-fou n'est
> pas levé — il est devenu **exact**. Jusque-là l'allowlist portait deux rôles
> (« ce qu'on connaît » et « ce qu'on autorise ») ; elle n'en garde qu'un.
> L'allowlist est désormais **cette liste-ci, plus ce qu'un geste humain y a
> admis** (§3.6), et la bibliothèque est strictement plus large qu'elle. C'est
> pourquoi une entrée porte deux champs qui ne disent pas la même chose : `curee`
> répond à « montable ? », `source` à « d'où ça vient ? ».

**Couverture** : [tests/test_mcp_registry.py](../tests/test_mcp_registry.py) côté
registre — les pilotes historiques vérifiés par **inclusion** et non par égalité
(la liste exacte était le contrat tant que le registre tenait en quatre entrées ;
elle rendrait le test faux à chaque intégration ajoutée, c'est-à-dire chaque fois
qu'il a le plus de raisons d'être joué), le tri par palier puis par nom, la
provenance qui ne masque aucune entrée, et le garde-fou d'allowlist inchangé ;
`apps/web/tests/integrations-bibliotheque.test.tsx` côté écran — la provenance
affichée, le panneau de chaque intégration, et la recherche sans résultat qui rend
une piste plutôt qu'un cul-de-sac. ⚠ Ce fichier **a suivi son sujet** : #270 a
sorti la bibliothèque des Paramètres et l'a renommé en conséquence
(`parametres-mcp.test.tsx` avant lui), ses scénarios inchangés. Le **pool projet**
du même écran est couvert à côté, par `apps/web/tests/integrations-pool.test.tsx`
(#273) — dont les quatre modes d'auth du §2 ci-dessus, `sans_secret` compris.

### 3.5 La bibliothèque fédérée (#673)

Le parent #673 donne une **seconde source** à la bibliothèque. Il ne lève pas le
garde-fou : il lui rend son seul rôle. Ce qui est moissonné, ce qui est curé et
ce qu'un humain autorise deviennent trois choses distinctes, et c'est cette
distinction — pas le nombre d'entrées — qui est le contenu du chantier.

**Ce qui est moissonné.** Le **registre MCP officiel**
(`registry.modelcontextprotocol.io`, porté par Anthropic, GitHub, Microsoft et
PulseMCP) est un catalogue de métadonnées en API publique non authentifiée, dont
la documentation prévoit explicitement notre cas d'usage : un *aggregator* qui
moissonne, persiste chez lui, et filtre selon sa propre politique. Mesuré le
2026-08-28 : **25 333 serveurs**, `limit` plafonné à 100, pagination par
`cursor`, ~1,3 s l'aller — soit **254 pages et ~10 minutes** pour un moissonnage
complet.

Deux mesures ont décidé du dessin, et il faut les connaître avant d'y toucher :

1. **`search` amont ne porte que sur le nom** (`feature flag` y rend zéro
   résultat sur 25 000 entrées), là où `RegistreMcp.rechercher` couvre nom,
   éditeur, tags et description, accents repliés. On ne délègue donc **pas** la
   recherche : *on moissonne chez lui, on cherche chez nous*.
2. **Le registre est en préversion** et annonce « no uptime or data durability
   guarantees ». Le miroir local (`maestro.agents.mcp_amont`, sous
   `core/mcp-amont/`) n'est donc pas une optimisation de latence : c'est la seule
   façon d'adosser un écran à cette source. `MiroirAmont.rafraichir` **ne lève
   jamais** — sur panne il rend sa cause et laisse le miroir précédent **intact**.

Le miroir garde le `server.json` **verbatim** ; `status` est le seul champ mutable
de l'amont — une entrée `deprecated` y **reste** avec son statut (signalée, pas
cachée), une entrée `deleted` en **sort** (modération : spam, malware, illégal).

**Ce qui est traduit, et ce qui est refusé en le nommant.**
`maestro.agents.mcp_traduction` convertit un `server.json` en entrée de
bibliothèque, ou rend un **refus nommé** — jamais une entrée à moitié déclarée :
`remotes[]` → transport `http`/`sse`, `packages[]` → `stdio` à version épinglée,
`environmentVariables[].isSecret`/`isRequired` → `VariableSecret`. Le refus le
plus important est celui que la règle de curation du §3.4 explique déjà :
`maestro.agents.mcp.resolus` ne substitue les `${VAR}` que dans `env` et
`headers`, jamais dans `args`, donc une entrée dont une variable vit en **argv**
(`packageArguments`/`runtimeArguments`) est **non traduisible** — refusée sous le
motif `variable_en_argv` plutôt que servie trouée. Ce n'est pas une hypothèse :
le corpus capturé en porte **trois cas réels** (voir la couverture ci-dessous).

**Ce qui est curé** n'a pas bougé (§3.4) — et reste le seul endroit où une entrée
est écrite à la main, relue en revue de code et versionnée avec le dépôt.

**Ce que la porte d'admission décide** : tout le reste. Une entrée découverte est
**visible, cherchable, jamais montable** ; elle n'entre dans l'allowlist que par
un geste humain tracé, décrit au §3.6. Le registre officiel vérifie la
**propriété du namespace** de l'éditeur (`io.github.<user>` par OAuth GitHub,
`com.exemple` par preuve DNS/HTTP) et **rien de plus** : il dit « ce serveur
existe », jamais « ce serveur est sûr » ([docs/19
§2.3](./19-securite-modele-de-menace.md)).

**Trois sources, une allowlist** — le tableau qu'il faut avoir en tête :

| Source | D'où elle vient | Montable ? | `curee` | `source` |
|---|---|---|---|---|
| **curée** | `SEED`, en code, relu en revue | oui | `true` | `curee` |
| **admise** | l'amont, **plus** un geste humain tracé | oui | `true` | `admise` |
| **découverte** | le miroir de l'amont, seul | **non** | `false` | `decouverte` |

⚠ Le point de lecture délicat est là, et il n'y en a qu'un : une entrée
**admise** est `curee: true` tout en venant de l'amont. Le booléen répond à
« montable ? » — c'est **lui** que `POST /api/mcp/pool` regarde —, la source à
« d'où ça vient ? ». Les lire à l'envers fermerait la porte à ce qu'un humain
vient d'ouvrir, ou pire, ouvrirait celle d'une découverte que personne n'a
admise.

**Ce qui ne moissonne pas.** `mcp_federation.federer` *lit* le miroir déjà sur le
disque et ne déclenche jamais de rafraîchissement : dix minutes de réseau sur le
chemin d'une requête d'écran seraient une régression qu'aucun cache ne rattrape.
Le rafraîchissement est le geste d'une boucle de fond
(`MiroirAmont.rafraichir_si_perime`, périodicité ≥ 1 h comme la doc d'amont le
demande). La composition est mémoïsée sur l'**empreinte** des deux fichiers
(miroir et journal d'admissions) — une écriture la fait tomber à l'instant même
où elle mentirait.

**Ce qui ne tombe jamais.** Un miroir absent, illisible ou vide, une entrée
intraduisible, un amont injoignable : tout retombe sur la bibliothèque curée,
avec la cause en clair. Une seule exception, et elle est nommée — un **journal
d'admissions illisible** retire de l'allowlist tout ce qu'il autorisait, donc sa
cause est **rendue** (`Federation.cause_admissions`) et non seulement
journalisée : perdre une découverte est un affichage en moins, perdre une
admission est un serveur qui ne monte plus.

**Couverture** (#680, le lot 6 du parent — les cinq autres ont différé leurs
tests ici) :

| suite | ce qu'elle garde |
|---|---|
| [tests/test_mcp_amont.py](../tests/test_mcp_amont.py) | le client et le miroir : pagination par `cursor`, incrément `updated_since`, `deleted` qui **sort** du miroir, amont injoignable qui laisse le miroir précédent **intact aux octets près** |
| [tests/test_mcp_traduction.py](../tests/test_mcp_traduction.py) | la traduction, sur des documents écrits à la main **et** sur un corpus capturé (voir ci-dessous) |
| [tests/test_mcp_federation.py](../tests/test_mcp_federation.py) | les trois sources, la porte d'admission, la veille, et le **garde-fou aux deux bouts** |
| [apps/web/tests/integrations-bibliotheque.test.tsx](../apps/web/tests/integrations-bibliotheque.test.tsx) | l'écran : la troisième source, les signaux d'amont, les états réels de la provenance |

⚠ **Aucun test ne parle au registre en direct**, et ce n'est pas une précaution
de style : une suite adossée à un service en préversion rend un rouge qui
n'apprend rien, et elle le rend le jour où l'on a le moins envie d'enquêter.
L'amont est joué par un transport en mémoire qui n'ouvre aucune socket, et les
échantillons sont **capturés puis versionnés** dans
[tests/fixtures/mcp_amont/](../tests/fixtures/mcp_amont/) — 62 enveloppes
verbatim, avec le script qui les recapture et le tableau de ce qu'elles couvrent.
Un test qui vérifie « l'amont n'est pas appelé » compte les **appels**, jamais
une durée.

⚠ **Le corpus a renversé une mesure du parent, et il faut la corriger ici :
#673 annonçait « les deux versions de schéma en circulation », il y en a
cinq.** Capturées le 2026-08-28 sur 4 029 entrées : `2025-12-11`, `2025-10-17`,
`2025-09-29`, `2025-07-09`, `2025-09-16`. `SCHEMAS_CONNUS` n'en déclare que deux,
donc **14 des 62 entrées** du corpus portent l'avertissement « schéma amont
inconnu » — et se traduisent quand même. C'est exactement ce que le module
promet (un millésime hors table est **signalé**, jamais refusé), et c'est ce qui
empêche la fédération de tomber le jour où l'amont publie un schéma de plus.
Élargir `SCHEMAS_CONNUS` ferait taire l'avertissement ; ce n'est pas la même
chose que le rendre inutile.

⚠ **Le refus `variable_en_argv` est prouvé sur un échantillon fautif avant qu'on
conclue de son absence.** Le corpus porte **trois entrées réelles** dont une
variable vit en argv ; le test vérifie d'abord qu'elles sont refusées *pour cette
cause* (en montrant que les mêmes documents, arguments retirés, passent), puis
seulement ensuite balaie les entrées traduites pour établir qu'aucune ne laisse
de gabarit dans ses `args`. L'ordre inverse rendrait un ✓ sur une question jamais
posée — et un corpus recapturé plus étroit désarmerait le balayage en silence,
raison pour laquelle un test garde la **couverture du corpus lui-même**.

### 3.6 La porte d'admission (#678, parent #673)

#271 avait élargi le registre de 4 à 29 entrées **sans lever le garde-fou**, et
en nommait déjà la limite : une liste figée ne découvre rien. Le parent #673 y
branche le **registre MCP officiel** (`registry.modelcontextprotocol.io`,
25 333 serveurs mesurés le 2026-08-28) par un miroir local (#675), une traduction
`server.json` → entrée de bibliothèque (#676) et une bibliothèque à deux sources
(#677). Ce §-ci porte la pièce qui manquait, et qui tient la promesse : **la
porte**.

**Le problème que la porte résout, en une phrase.** L'allowlist portait deux
rôles — « ce qu'on connaît » et « ce qu'on autorise » —, et c'est ce qui rendait
la découverte impossible sans affaiblir l'installation. L'admission les sépare :
le garde-fou de [docs/19](./19-securite-modele-de-menace.md) n'est pas levé, il
devient **exact**.

| `source` | d'où | `curee` | montable |
|---|---|---|---|
| `curee` | `SEED`, écrit à la main, relu en revue de code | `true` | oui |
| `admise` | le registre officiel, **plus** un geste humain tracé | `true` | oui |
| `decouverte` | le registre officiel seul | `false` | **non** |

⚠ `curee` (le booléen) répond à « **montable ?** » — c'est lui que le garde-fou
lit —, `source` à « **d'où ça vient ?** ». Une admise est donc `curee: true` et
`source: "admise"`, sans contradiction ; et le filtre `source=curee` rend le
**seed seul**, parce qu'un écran qui montre la provenance doit séparer ce qui a
été relu en revue de code de ce qu'un clic a promu hier.

**Ce que le registre officiel prouve, et ce qu'il ne prouve pas.** Il vérifie la
**propriété du namespace** de l'éditeur (`io.github.<user>` par OAuth GitHub,
`com.exemple` par preuve DNS/HTTP) et rien de plus : aucun scan, aucune caution.
Il dit « ce serveur existe », jamais « ce serveur est sûr ». Ce qu'il change est
la **qualité de la matière** : la règle de curation du §3.4 interdit d'écrire un
`npx -y <paquet>` **de mémoire**, et un identifiant lu dans un enregistrement
d'éditeur au namespace vérifié, à version épinglée, n'est pas de la mémoire — le
motif de la règle cesse de s'appliquer sans que la règle s'affaiblisse.

**Trois décisions, une par critère du ticket.**

1. **L'admission fige l'entrée traduite.** Ce que la bibliothèque sert d'une
   admise vient de l'enregistrement, pas du miroir d'aujourd'hui. Sans ce
   figement, une nouvelle version amont changerait ce qu'on monte sans que
   personne l'ait admis : l'admission autoriserait une version et en monterait
   une autre. Promouvoir une version plus récente est un **nouveau geste**.
2. **Rien ne disparaît en silence, et rien n'est retiré d'office.** Une admise
   dont l'amont passe `deprecated`, `deleted`, ou qui sort du miroir, reste
   servie **avec son signal** (`SignalAmont`, quatre genres). Retirer d'office
   casserait un serveur monté sans le dire ; la décision appartient à qui a
   admis. C'est le partage habituel de ce dépôt : ce qui est automatique est la
   **détection**, jamais le verdict. ⚠ Un **miroir vide ne produit aucun
   signal** — sans ce garde-fou, un poste qui n'a jamais moissonné déclarerait
   « disparue de l'amont » toutes ses admissions d'un coup.
3. **Une révocation ne s'oublie pas et ne démonte rien.** L'admission révoquée
   reste au journal, ce qui permet au refus d'instanciation de **nommer** ce qui
   s'est passé au lieu de rendre le refus d'un id inconnu ; et un serveur déjà
   dans le pool y **reste**, avec son `alerte`. Couper un run en cours pour
   appliquer une décision d'allowlist serait un remède pire que le mal : ce qui
   est promis est « jamais sans le dire », pas « jamais sans casser ».

**Le refus nomme le geste qui manque.** `RegistreMcp.instancier` et
`POST /api/mcp/pool` distinguent trois causes là où il n'y en avait qu'une
(« hors allowlist ») : une entrée **découverte** attend une admission — et la
phrase dit où —, une entrée **révoquée** dit qui l'a retirée, quand et pourquoi,
un id **inconnu** n'attend rien. La phrase vit à **un seul endroit**
(`cause_non_instanciable`) et les deux appelants la relaient : deux formulations
pour un même refus finiraient par se contredire. ⚠ La révocation est cherchée
**avant** la découverte, parce qu'une entrée révoquée redevient une découverte —
les deux causes sont vraies à la fois, et « personne ne l'a admise » est exact et
trompeur sur une entrée qu'on a admise puis retirée.

**Le contrat d'API** (figé ici, l'écran l'a suivi au lot #679) :

| route | ce qu'elle fait |
|---|---|
| `GET /api/mcp/admissions` | le journal : actives, révoquées, `signaux` d'amont, `politique` qui garde la porte |
| `POST /api/mcp/admissions` | **admet** (`registre_id`, `par`, `note`) → l'entrée telle que la bibliothèque la sert ensuite. 404 inconnue · 409 déjà curée / supprimée chez l'amont / refusée par la politique |
| `POST /api/mcp/admissions/{id}/revocation` | **révoque** (`par`, `motif`) → l'admission + ce qui **reste monté** dans le pool. 404 jamais admise · 409 déjà révoquée |
| `GET /api/mcp/registre?source=` | `toutes` (défaut) · `curee` · `admise` · `decouverte` — 422 sur une valeur inconnue |
| `GET /api/mcp/registre/provenance` | **trois** provenances ; `total_curees`/`total_admises`/`total_decouvertes` |
| `GET /api/mcp/pool` | chaque intégration porte désormais `source`, `admission`, `signaux` et `alerte` |

⚠ Un `POST …/revocation` et non un `DELETE …/{id}` : **rien n'est effacé** (le
journal garde tout), et le geste porte un corps — l'auteur, le motif — qu'un
`DELETE` transporte mal.

**Le point d'extension d'entreprise.** L'admission est l'endroit où une
organisation veut glisser sa revue, son scan, son refus par éditeur : le contrat
est un **callable** d'une ligne (`Candidature` → `VerdictPolitique`), injectable
au service ou désigné par `MAESTRO_MCP_ADMISSION_POLITIQUE` (`module:attribut`).
Le défaut accepte tout — le geste humain **est** la politique par défaut, et en
inventer une plus stricte ici reviendrait à décider à la place de gens qu'on ne
connaît pas. La politique passe **en dernier**, après les contrôles
d'admissibilité (entrée inconnue, déjà curée, `deleted` chez l'amont, gabarit qui
ne se monterait pas) : elle répond à « fait-on confiance ? », jamais à « cela
existe-t-il ? ». Une valeur illisible **échoue au démarrage** au lieu de retomber
en silence sur le défaut.

**Où vivent les admissions.** Dans `core/mcp/admissions.json`, à côté du pool et
des activations (`MAESTRO_MCP_DIR`) : c'est une **donnée d'installation**, pas du
code — le `SEED` reste le socle relu en revue de code, et l'admission ne le
touche pas. Un journal illisible **retire de l'allowlist** tout ce qu'il
autorisait, et la cause remonte jusqu'à l'écran (`cause_admissions`) : perdre une
découverte est un affichage en moins, perdre une admission est un serveur qui ne
monte plus.

**Couverture** : voir le §3.5 ci-dessus — les six lots du parent sont couverts
ensemble par le lot 6 (#680), la porte d'admission par
[tests/test_mcp_federation.py](../tests/test_mcp_federation.py) et son garde-fou
par les deux bouts (`instancier` **et** `POST /api/mcp/pool`).

### 3.7 Tout se règle depuis la fiche de l'agent (#263, lot 11/15 de #243)

L'activation par agent était **déjà en écriture** depuis #133 : chaque
intégration du pool porte un interrupteur sur la fiche. Ce qui manquait n'était
pas un mécanisme mais **le reste du geste** — et les trois manques étaient du
même genre : l'écran montrait un état sans donner le moyen de le changer.

| ce qu'on voulait faire | avant #263 | depuis |
|---|---|---|
| voir ce dont l'agent dispose | une liste plate d'interrupteurs, à déplier un par un | **actives en tête**, disponibles ensuite, comptées |
| ajouter une intégration absente du pool | sortir vers `/integrations`, chercher, configurer, revenir activer | la bibliothèque **sur place**, et l'ajout **active dans la foulée** |
| traiter une déclaration héritée | un bloc « lecture seule (à migrer vers le pool) » qu'aucun écran ne migrait | **un bouton**, `POST /api/mcp/migration/{agent}` |

**Le partage entre les deux écrans est celui de la portée du geste**, et c'est ce
qui décide ce que la fiche n'a pas le droit de faire. L'écran **Intégrations**
règle le *projet* : ajouter au pool, reconfigurer un secret, **retirer du pool**
— ce dernier désactive chez tous les agents et purge les secrets, donc il n'a pas
sa place sur la page d'un agent, où son effet dépasserait ce qu'on croit régler.
La **fiche** règle *cet agent* : activer, désactiver, migrer ses héritées. Elle
ajoute au pool aussi, et c'est la seule exception — équiper un agent d'une
intégration qui n'existe pas encore est **une** intention, pas deux, et la scinder
était exactement le détour qu'on supprime.

⚠ **Le corollaire doit être écrit à l'écran** : éteindre un interrupteur
**désactive pour cet agent seul**, l'intégration reste au pool avec son secret.
C'est la question qu'un interrupteur pose forcément — « si je l'éteins, est-ce
que je perds la configuration ? » — et la laisser sans réponse fait hésiter sur
le seul geste qui ne coûte rien.

⚠ **La bibliothèque montée sur la fiche est celle de `/integrations`, importée
telle quelle** (`components/integrations/BibliothequeMcp`). En recopier une
version allégée rejouerait #231 (le `<form>` qui borne la détection du
gestionnaire de mots de passe, le panneau oublié quand son entrée quitte les
résultats) et ferait deux vérités sur ce qui est montable. Elle est **repliée
derrière un bouton** : on n'arrive pas sur cet onglet pour chercher une
intégration, on y arrive pour voir ce que l'agent a.

⚠ **Aucune seconde porte sur les secrets.** Le secret se saisit une fois, chiffré
côté serveur, et n'est jamais réémis (§3.2) : la fiche en montre l'**état**
(« secret à configurer ») et renvoie vers l'écran qui le pose.

**La migration, et les quatre choses qu'elle a coûtées.** `McpStore.migrer`
existait depuis #130 — testé, documenté, et **sans aucun appelant** : ni route, ni
CLI, ni écran. Le rebrancher tel quel était impossible, et c'est le premier point.

1. **`migrer` remplace, `migrer_agent` ajoute.** `composer_migration` compose le
   pool à partir des *seuls* fichiers hérités, et `ecrire_pool` est un
   remplacement intégral : sur un projet dont le pool est vivant, migrer un agent
   aurait effacé tout ce que la Control Tower y avait ajouté. `migrer_agent(agent)`
   greffe au lieu d'écrire par-dessus, et laisse les activations des autres agents
   en place.
2. **Le fichier part, et c'est le contenu du geste.** Tant qu'il est là, l'héritée
   reste **autoritaire** à la lecture (`McpStore.lire`, collision de `serveur.nom`)
   : une migration qui le laisserait ne changerait rien à ce qui est monté, et le
   bloc « hérités » resterait affiché. Elle serait invisible.
3. **L'id vient de la bibliothèque quand elle décrit exactement le serveur.**
   `figma-officiel` et `slack` s'en tirent seuls (leur nom de serveur *est* l'id de
   leur entrée), mais `forge` — le serveur GitHub de `qa.json` — entrerait au pool
   sous un id inconnu du registre, donc `curee: false`, sans mode d'auth et **sous
   une alerte**, pour une opération parfaitement saine. L'API rapproche donc chaque
   serveur d'une entrée de l'allowlist par **égalité stricte** de la déclaration
   instanciée *sous le nom du serveur d'origine* — jamais une ressemblance, qui
   donnerait à une intégration la fiche et les secrets d'une autre — et **conserve
   le nom** : c'est le préfixe d'outils (`mcp__<nom>__…`) que les playbooks
   emploient déjà, le renommer changerait le comportement de l'agent au milieu
   d'une migration qui promet de ne rien changer.
4. **Il restait une troisième cause à l'`alerte` du pool**, qui n'en nommait que
   deux (retirée du seed, admission illisible). Une intégration migrée que la
   bibliothèque ne décrit pas arrive par ce chemin sans que rien n'ait disparu :
   la phrase la nomme, faute de quoi l'écran ferait lire un incident.

**Aucun secret n'est demandé ni redemandé** par la migration : une déclaration
héritée porte déjà ses références `${VAR}`, résolues au montage exactement comme
avant. C'est ce qui en fait un geste et non un formulaire.

**Le contrat d'API** :

| route | ce qu'elle fait |
|---|---|
| `POST /api/mcp/migration/{agent}` | migre `core/mcp/{agent}.json` vers le pool → `{ajoutees, reprises, activations, fichier_retire}`. 404 agent hors catalogue · 422 rien à migrer ou source invalide (rien n'est écrit) |
| `GET /api/catalogue/{nom}` | porte `mcp_herites` : les serveurs de la **seule** déclaration héritée |

⚠ `ajoutees` et `reprises` sont **deux listes et non un total** : la seconde
porte ce que le pool avait déjà sous la même déclaration — le partage entre
agents que le pool existe pour permettre. Les fondre ferait annoncer « 2
intégrations ajoutées au projet » à une migration qui n'en a créé qu'une.

⚠ `mcp_herites` est **servi et non déduit**. L'écran le calculait en retranchant
des serveurs montés ceux des intégrations activées, rapprochés par leur **nom** :
une intégration renommée à l'ajout au pool ne concorde plus, et l'héritée
réapparaît comme un serveur de plus. La question « qu'y a-t-il dans le fichier ? »
se pose au fichier.

**Couverture.** Les états de bord de l'écran sont **différés au lot 15** du
parent #243. Les quatre gestes du ticket, eux, sont livrés avec lui
([apps/web/tests/agent-mcp.test.tsx](../apps/web/tests/agent-mcp.test.tsx)) : la
règle de docs/10 §5.1 le permet quand la logique est critique, et elle l'est —
**aucun test ne montait cet écran**, ni celui-ci ni ses ancêtres dans
`EditeurAgent`, alors qu'il écrit dans le pool projet et dans les activations.
Il a d'ailleurs payé immédiatement : le compte rendu de migration vivait dans le
bloc des déclarations héritées, c'est-à-dire **dans ce que la migration
supprime** — une migration réussie recharge la fiche, remonte la section, et le
bloc disparaît avec son message. On cliquait, tout s'évanouissait, et rien ne
disait ce qui venait de se passer. Un compte rendu ne peut pas vivre dans ce que
le geste efface : il est remonté au composant qui survit au rechargement.

---

*Références : [docs/15](./15-pilote-mcp-slack.md) (Slack), [docs/16](./16-pilote-mcp-tickets-gitlab.md) (GitLab), [docs/20](./20-pilote-mcp-figma.md) (Figma, dont §6 pour la voie officielle), [core/mcp/README.md](../core/mcp/README.md) (socle des déclarations).*
