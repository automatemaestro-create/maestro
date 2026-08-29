# Pilote MCP Figma — l'agent designer crée dans un fichier (ticket #115)

**Version :** 0.3 — bascule sur le serveur MCP officiel (§7, ticket #128)
Troisième pilote concret du socle MCP (#104, parent #101) : l'agent **designer**,
équipé d'un serveur MCP « Talk to Figma », **crée des éléments dans un fichier
Figma en temps réel** (frames, formes, textes — visibles dans le fichier pendant
le run) et lit le contenu existant pour s'y adapter. Cette page consigne le
choix du serveur, l'architecture d'appairage, les exigences sur les secrets et
la démonstration réelle.

> **⚠ Bascule (ticket #128, 2026-07-20)** : la configuration active de l'agent
> designer est désormais le **serveur MCP officiel Figma seul** (§6, bascule
> §7) — plus aucune configuration active ne référence
> `cursor-talk-to-figma-mcp` ni `FIGMA_CHANNEL`. Les sections §1 à §5 (pilote
> « Talk to Figma », #115) sont conservées telles quelles en **trace
> historique** et servent de **repli documenté** (§7.2) si aucun token
> officiel n'est disponible.

> **Principe** : aucun connecteur Figma dans Maestro. C'est l'agent `designer`
> du catalogue, équipé par sa **déclaration MCP versionnée**
> ([core/mcp/designer.json](../core/mcp/designer.json)), qui crée et lit les
> éléments via ses outils `mcp__figma__…` — le moteur lui confie une mission,
> jamais un appel d'API. Le profil du rôle (`maestro/agents/designer.py`) n'a
> pas changé d'une ligne : le pilote est purement configuratif.

---

## 1. Choix du serveur : « Talk to Figma » (plutôt que le MCP officiel Figma)

Le critère du ticket — **créer** des éléments dans un fichier pendant le run —
écarte la plupart des options au moment du pilote (2026-07) :

- **MCP officiel distant** (`mcp.figma.com/mcp`) : sait désormais écrire sur le
  canvas, mais l'authentification est **OAuth interactif uniquement** — pas de
  Personal Access Token accepté (confirmé par Figma sur le forum), donc pas de
  montage headless par le moteur au sens du socle #104.
- **MCP officiel desktop** : réservé à des besoins organisation/enterprise, et
  lié à l'app desktop.
- **Serveurs communautaires REST** (PAT) : l'API REST Figma ne crée pas de
  nœuds de design — lecture seule pour l'essentiel.
- **[`cursor-talk-to-figma-mcp`](https://www.npmjs.com/package/cursor-talk-to-figma-mcp)**
  (retenu, v0.3.5 épinglée) : serveur stdio (via `npx`, node suffit) + **plugin
  Figma communautaire** + petit relais WebSocket local. C'est la voie éprouvée
  pour la création temps réel : ~40 outils dont `create_frame`,
  `create_rectangle`, `create_text`, `get_document_info`, `read_my_design`…
  Fonctionne avec un compte Figma gratuit, dans le navigateur.

### 1.1 Architecture d'appairage

```
agent designer ──(stdio)── serveur MCP ──(ws://localhost:3055)── relais ──(ws)── plugin Figma
                                                                            (dans le fichier ouvert)
```

- Le **relais WebSocket** (script `socket.ts` du dépôt du serveur, lancé avec
  `bun`) écoute sur `localhost:3055` et met en relation serveur MCP et plugin
  par **canal** ;
- Le **plugin compagnon** (« Cursor Talk to Figma MCP Plugin », plugin
  communautaire) tourne dans le fichier Figma ouvert et exécute les commandes
  avec l'API plugin — c'est lui qui crée réellement les nœuds, **avec la
  session Figma de l'utilisateur** (aucun token d'API n'existe dans ce
  montage) ;
- Le **canal d'appairage** généré par le plugin à chaque session est le seul
  secret : qui le connaît peut piloter le fichier ouvert via le relais. L'agent
  le rejoint en début de mission (`join_channel`).

## 2. Configuration

### 2.1 Déclaration MCP de l'agent (versionnée)

[core/mcp/designer.json](../core/mcp/designer.json) équipe l'agent `designer` :

```json
{
  "serveurs": [
    {
      "nom": "figma",
      "type": "stdio",
      "commande": "npx",
      "args": ["-y", "cursor-talk-to-figma-mcp@0.3.5"],
      "env": {
        "FIGMA_CHANNEL": "${FIGMA_CHANNEL}"
      },
      "optionnel": true
    },
    {
      "nom": "figma-officiel",
      "type": "http",
      "url": "https://mcp.figma.com/mcp",
      "headers": {
        "Authorization": "Bearer ${FIGMA_OAUTH_TOKEN}"
      },
      "optionnel": true
    }
  ]
}
```

Conformément au contrat du socle (#104, [docs/04 §6](./04-specifications-agents.md)),
les serveurs sont montés **à chaud** sur chaque exécution outillée de l'agent.
Les deux voies sont déclarées **optionnelles** (`"optionnel": true`, notion
introduite au #125) : un serveur optionnel dont le secret n'est pas fourni est
**omis du montage** au lieu de faire échouer la tâche — seule la voie
configurée (canal d'appairage **ou** token OAuth du serveur officiel, §6) est
montée, et une tâche de design sans besoin Figma s'exécute sans outils Figma.
(Au pilote #115 initial, le serveur communautaire était requis : sans canal, la
tâche échouait avant tout appel modèle — sémantique remplacée par l'omission.)

La référence `${FIGMA_CHANNEL}` joue un double rôle : elle **conditionne le
montage** à un appairage effectif, et elle **enregistre le canal au registre de
rédaction** au moment de la résolution (#109) — le canal est masqué
(`[secret masqué]`) partout où il réapparaîtrait en sortie. `${FIGMA_OAUTH_TOKEN}`
suit exactement le même régime (§6).

### 2.2 Politique de permissions (versionnée)

[core/permissions/designer.json](../core/permissions/designer.json) barre les
outils de **suppression** du serveur (#110) :

```json
{
  "allow": [],
  "deny": ["mcp__figma__delete_node", "mcp__figma__delete_multiple_nodes"]
}
```

C'est l'écho, côté permissions, du garde-fou du rôle (docs/04 §3.5 : « il
propose, il ne remplace pas la charte ») : l'agent peut créer et lire dans le
fichier, il ne peut **rien y détruire** — un appel de suppression serait refusé
au vol, tracé (`:refus-outil`), sans condamner le run.

### 2.3 Côté Maestro (`.env`)

```bash
FIGMA_CHANNEL=…   # canal affiché par le plugin — jamais committé ni loggué
```

Renouvelé à chaque session d'appairage (le plugin en génère un nouveau) ; le
[coffre par agent](./18-secrets-par-agent.md) (`core/secrets/designer.json`)
est utilisable à l'identique si l'on préfère scoper le canal au seul designer.

### 2.4 Mise en route (avant le run)

1. **Relais** : `bun socket.ts` (script du dépôt
   [sonnylazuardi/cursor-talk-to-figma-mcp](https://github.com/sonnylazuardi/cursor-talk-to-figma-mcp),
   port 3055) ;
2. **Plugin** : dans le fichier Figma ouvert (navigateur ou desktop), lancer le
   plugin communautaire « Talk To Figma MCP Plugin », se connecter au
   relais — il affiche le **canal**. Dans Chrome ≥ 141, accorder à
   `figma.com` la permission **« Accès au réseau local »** (Local Network
   Access) : sans elle, la connexion du plugin vers `ws://localhost:3055`
   reste indéfiniment en attente (constat de la démo §4) ;
3. **Canal** : reporter la valeur dans `FIGMA_CHANNEL` (`.env` ou coffre) ;
4. Lancer le run (§3) — le serveur MCP est monté par le moteur, `npx` suffit
   (le paquet tourne sous node ; seul le relais demande `bun`).

## 3. Le canal ne transite jamais en clair (critère du ticket)

Mêmes exigences que les pilotes Slack (#105) et GitLab (#106) — défense en
profondeur :

1. **Déclaration versionnée sans secret** : `core/mcp/designer.json` ne porte
   que la référence `${FIGMA_CHANNEL}`, résolue au montage (#104) — la valeur
   n'existe qu'en mémoire ; l'API/UI de la fiche agent masque toute valeur
   littérale.
2. **Aucun token d'API dans la boucle** : le plugin agit avec la session Figma
   de l'utilisateur — il n'y a littéralement **aucun token Figma** à protéger,
   ni dans les playbooks, ni dans l'environnement.
3. **Journaux et restitution expurgés** ([maestro/telemetry/redact.py](../maestro/telemetry/redact.py)) :
   `FIGMA_CHANNEL` est dans la liste des variables sensibles, et la valeur
   résolue au montage est de toute façon enregistrée au registre des secrets
   servis (#109) — masquée du journal du run, du fil temps réel Control Tower
   et des traces Langfuse, y compris quand la mission qui la cite est
   consignée. Ce pilote a étendu la même passe à la **restitution du CLI**
   (`maestro-run`, rapport `--json` comme synthèse) : la première démo a montré
   que l'agent peut citer le canal dans son livrable — désormais expurgé là
   aussi ([maestro/engine/cli.py](../maestro/engine/cli.py)).

Vérifié sans réseau par [tests/test_mcp.py](../tests/test_mcp.py) (section
« ⑥ Pilote Figma ») : déclaration du dépôt sans littéral, canal résolu expurgé
des sorties, serveur indisponible sans canal, suppressions barrées par la
politique.

## 4. Démonstration réelle (2026-07-19)

Run réel mené de bout en bout — même consignation sur pièces que
[docs/15](./15-pilote-mcp-slack.md) et [docs/16](./16-pilote-mcp-tickets-gitlab.md).

### 4.1 Dispositif

| Élément | Valeur |
|---------|--------|
| Fichier Figma | Brouillon « Sans titre » (compte Figma gratuit, Figma **navigateur** — pas d'app desktop) |
| Serveur MCP | `cursor-talk-to-figma-mcp` 0.3.5 (stdio via `npx`), déclaré dans [core/mcp/designer.json](../core/mcp/designer.json) |
| Relais | `socket.ts` du dépôt amont, lancé avec `bun` local, port 3055 |
| Plugin | « Talk To Figma MCP Plugin » (plugin communautaire, ~68 k utilisateurs), canal d'appairage via `${FIGMA_CHANNEL}` |
| Agent | `designer` (runtime outillé, Claude Sonnet), serveur monté à chaud, politique [core/permissions/designer.json](../core/permissions/designer.json) active |
| Garde-fous | `--plafond-cout 3`, `--timeout 600`, relances par défaut |
| `run_id` | `b671e9251c3c` |

Avant le run, le fichier contenait deux éléments « existants » (un rectangle
« zone charte » et une note de charte texte) que l'agent devait lire et
respecter.

### 4.2 Les critères du ticket, exercés en réel (vérifiés)

Le plan produit **une** tâche (`design-figma-pilote-115`, compétences `ui` +
`figma`), routée sur l'agent `designer`. Son exécution (1 appel modèle,
23 tours, 85,7 s, ~0,36 $) enchaîne 10 outils MCP Figma (`join_channel`,
`get_document_info`, `get_nodes_info`, `create_frame`, `create_rectangle`,
`create_text`, `set_fill_color`, `set_stroke_color`, `set_corner_radius`,
`get_node_info`) plus `Write` pour le livrable :

1. **Lecture de l'existant et adaptation** — `get_document_info` en début de
   mission : les deux éléments existants sont repérés, laissés intacts, et le
   nouveau contenu placé à droite pour ne rien recouvrir (vérifié sur pièces :
   positions et le re-`get_document_info` final, page à exactement
   2 + 1 enfants).
2. **Création en temps réel** — le frame « Maestro — pilote 115 » (720×460)
   et ses 7 nœuds (en-tête bleu, titre « Maestro Control Tower », sous-titre,
   deux cartes avec textes) sont apparus **dans le fichier pendant le run**,
   visibles au fil des commandes du plugin (nœuds `3:4` à `3:11`). L'agent a
   ensuite **relu** le document (`get_document_info`/`get_node_info`) pour
   vérifier la persistance réelle avant de livrer — vérification recoupée côté
   humain à l'écran (capture conservée en pièce de session).

Bilan du run : **1/1 tâche réussie**, 2 appels modèle, 333 319 tokens,
coût 0,4548 $, durée 99,0 s.

### 4.3 Lecture critique

- **Canal vérifié absent des journaux** : 0 occurrence de la valeur de
  `FIGMA_CHANNEL` dans la trace (`--trace`), 2 marqueurs `[secret masqué]` à la
  place (la mission qui le cite est consignée expurgée) — conjonction des
  verrous du §3. La première restitution `--json` citait en revanche le canal
  (objectif + écho de l'agent dans son livrable) : corrigé dans la foulée, la
  restitution passe désormais par la même rédaction que la trace (test dédié
  dans `tests/test_cli_smoke.py`).
- **Purement configuratif** : aucun code moteur nécessaire au montage — une
  déclaration versionnée + une politique de permissions + un objectif de run.
  Comme le pilote GitLab (#106), le routage par compétences a suffi.
- **Chrome ≥ 141 (Local Network Access)** : le plugin (servi par `figma.com`)
  ne peut plus joindre `ws://localhost:3055` sans la permission « Accès au
  réseau local » — la connexion reste en attente, sans erreur visible. À
  accorder une fois au premier appairage (§2.4).
- **L'appairage est la vraie dépendance** : relais + plugin ouverts pendant
  toute la durée du run — le plugin est l'exécutant réel (session Figma de
  l'utilisateur, aucun token d'API dans la boucle).

## 5. Limites connues (POC)

- Le montage exige le **trio relais + plugin + canal** : le plugin doit rester
  ouvert dans le fichier pendant le run (c'est lui l'exécutant réel). Plugin
  fermé ou relais éteint = outils en échec propre.
- Le **canal est éphémère** : régénéré à chaque session du plugin, à reporter
  dans `FIGMA_CHANNEL` avant chaque campagne de runs.
- Les serveurs Figma sont **optionnels** depuis le #125 : une tâche de design
  sans `FIGMA_CHANNEL` ni `FIGMA_OAUTH_TOKEN` s'exécute **sans outils Figma**
  (plus d'échec au montage). Revers : une mission Figma lancée sans secret
  n'échoue plus d'emblée — l'agent produira un livrable local sans toucher au
  fichier ; vérifier l'appairage/le token avant une campagne Figma.
- Le relais demande `bun` (script du dépôt amont) ; le serveur MCP lui-même
  tourne sous node via `npx`.
- Sécurité locale : le relais écoute sur `localhost:3055` sans autre
  authentification que le canal — ne pas l'exposer au-delà du poste.

## 6. Variante serveur MCP officiel Figma (ticket #125)

Le serveur MCP **officiel** (`https://mcp.figma.com/mcp`, écriture sur le
canvas incluse) est plus simple que le pont communautaire — ni relais, ni
plugin, ni canal éphémère — mais son accès est verrouillé (constats du
2026-07-19, §1) : **OAuth uniquement** (scope `mcp:connect` — un PAT est
refusé, `Bearer` comme `X-Figma-Token` → 401) et **enregistrement dynamique de
client fermé** (`POST /v1/oauth/mcp/register` → 403, réservé aux clients
approuvés : Claude Code, Cursor, VS Code…).

**Décision : implémenté en variante, authentification humaine.** La voie
officielle est **branchée** dans la déclaration versionnée du designer
(`figma-officiel`, §2.1) mais Maestro **ne mène aucune authentification
automatique** — pas de flux OAuth embarqué, pas de réutilisation du token
stocké par le CLI Claude (couplage au fournisseur contraire à O7/ENF-11).
L'obtention du token est une **action humaine** :

1. l'utilisateur obtient un access token OAuth valide (scope `mcp:connect`)
   via un client approuvé par Figma — ou plus tard via un enregistrement de
   client propre à Maestro si Figma l'ouvre ;
2. il le fournit à Maestro : `FIGMA_OAUTH_TOKEN` dans `.env`, ou dans le
   [coffre du designer](./18-secrets-par-agent.md) (`core/secrets/designer.json`)
   pour le scoper au seul agent ; à terme, la **Control Tower** portera la
   saisie/rotation de ce secret (même canal que le coffre #109) ;
3. au montage, la référence `${FIGMA_OAUTH_TOKEN}` est résolue et envoyée en
   `Authorization: Bearer` — token enregistré au registre de rédaction (#109),
   masqué partout en sortie. Sans token, le serveur est **omis** (serveur
   optionnel, §2.1) : rien n'échoue, la voie officielle n'existe simplement
   pas encore.

Côté moteur, la voie est un endpoint `http` **standard** du socle #104
(déclaration → résolution → config SDK `url` + `headers`) : aucun couplage à un
fournisseur de modèle, la bascule reste purement configurative.

**Démonstration réelle (2026-07-19, `run_id 953bfdc079fb`)** — la voie
officielle est **validée de bout en bout**, écriture comprise, dès qu'un token
est fourni. Run headless (`maestro-run`, `FIGMA_CHANNEL` neutralisé pour ne
monter que la voie officielle) : 4/4 tâches réussies, 5 appels modèle,
~1,16 $, 489 s. L'agent designer a enchaîné `whoami`, `create_new_file`
(nouveau fichier, clé consignée au compte-rendu), `use_figma` (frame
« Maestro — test 125 » 720×460 : bandeau, titre, sous-titre, carte — 7 nœuds),
`get_screenshot`, puis une relecture indépendante `get_metadata` +
`get_design_context` : **7/7 éléments présents et conformes**, contrastes
WCAG AA vérifiés. **Aucun refus d'écriture** malgré le siège `View`/tier
`starter` — le gating par plan ne s'est pas manifesté sur ce parcours.
Confirmation opérationnelle des atouts attendus : ni relais, ni plugin, ni
canal — le token seul suffit.

**Seule contrainte opérationnelle restante : la durée de vie du token.** Le
refresh n'est **pas** géré par Maestro — token expiré = serveur qui refuse la
connexion (échec propre au montage), à renouveler côté humain (re-copier le
token rafraîchi par le CLI), jusqu'à la prise en charge par la Control Tower.

**Sondes au premier token fourni (2026-07-19)** — deux constats sur pièces :

1. **PAT refusé, OAuth seul passe.** Un PAT `figd_` valide et multi-scopes
   (file_content, file_comments, library…, vérifié contre l'API REST) reste
   refusé par le serveur MCP — `initialize` → 401 dans les deux en-têtes
   (`Authorization: Bearer` → « figd_ tokens must be passed via X-Figma-Token » ;
   `X-Figma-Token` → « Unauthorized », `WWW-Authenticate: … scope="mcp:connect"`).
   **Aucun scope de PAT n'ouvre le MCP officiel.**
2. **Relevé des capacités** avec un access token OAuth `mcp:connect` (obtenu
   par l'humain via l'authentification interactive du CLI Claude, client
   approuvé, puis reporté dans `FIGMA_OAUTH_TOKEN`) : `initialize` → 200
   (« Figma MCP Server » 1.0.0, capacités `prompts`/`resources`/`tools`, sans
   `Mcp-Session-Id`) et `tools/list` → **26 outils**. Lecture/design-to-code :
   `get_design_context` (outil primaire), `get_metadata`, `get_variable_defs`,
   `get_screenshot`, `get_libraries`, `search_design_system`,
   `download_assets`, suite Code Connect, shaders… **Écriture sur le canvas
   confirmée au catalogue** : `use_figma` (« create, edit, generate, or sync
   any design » — outil large, création **et** édition), `generate_figma_design`
   (import URL/HTML), `generate_diagram`, `create_new_file`, `upload_assets`.
   `whoami` situe le compte : plan **tier `starter`** (gratuit), siège
   **`View`** — pas d'en-tête `X-Figma-Plan-Tier` au handshake, le gating
   s'exprime donc à l'appel : l'écriture réelle avec ce siège reste à exercer
   lors d'un premier run designer.

**Permissions — limite structurelle relevée** : contrairement au pont
communautaire (~40 outils granulaires, `delete_node` isolable et barré), le
serveur officiel n'expose **aucun outil de suppression dédié** — l'édition
passe par le seul `use_figma`, insécable. Le garde-fou « il ne détruit rien »
ne peut donc pas s'y exprimer à la granularité de l'outil : soit on laisse
`use_figma` (le garde-fou repose alors sur le prompt du rôle, docs/04 §3.5),
soit on le barre en `deny` pour un usage **lecture seule** du serveur officiel
(design-to-code) en gardant la création au pont communautaire.

⚠ **Ce choix en deux termes a une troisième issue depuis #716**, et elle
n'existait pas quand ce relevé a été écrit : le cran du milieu. `use_figma` — et
les quatre autres écritures confirmées au catalogue ci-dessus — sont classés
`ask` / `humain` dans [`core/permissions/designer.json`](../core/permissions/designer.json),
le reste du serveur `ask` / `auto`. L'édition insécable du canvas d'équipe est
donc **vue avant de partir**, sans que le designer perde son outil primaire ni
que le garde-fou se réduise au prompt du rôle. Le raisonnement entrée par entrée
est dans [le README du dossier](../core/permissions/README.md).

Les deux voies sont **démontrées en réel** (§4 pour le pont communautaire,
ci-dessus pour l'officielle). Elles ont d'abord coexisté en serveurs
optionnels (#125) ; depuis le ticket #128, la voie officielle est la **seule
configuration active** (§7) — le pont communautaire reste le repli documenté
sans token (§7.2). La config `figma-officiel` posée en portée locale du CLI
(`~/.claude.json`) sert à l'**authentification humaine** (obtention/refresh du
token) mais n'est **pas** utilisée par les runs Maestro — la retirer via
`claude mcp remove figma-officiel` est sans effet sur cette variante (hors
renouvellement du token).

## 7. Bascule sur la voie officielle (ticket #128)

L'évaluation #125 ayant validé la voie token de bout en bout (§6, écriture
comprise), la configuration active bascule sur le **serveur officiel seul** —
plus simple (ni relais, ni plugin, ni canal éphémère) et d'une granularité
design-to-code supérieure.

### 7.1 Ce qui change dans la configuration active

- **[core/mcp/designer.json](../core/mcp/designer.json)** ne déclare plus que
  `figma-officiel` (endpoint `http`, `Authorization: Bearer
  ${FIGMA_OAUTH_TOKEN}`, `"optionnel": true` — sans token, le serveur est omis
  du montage, une tâche de design sans Figma ne casse pas) ;
- **`.env.example`** ne porte plus que `FIGMA_OAUTH_TOKEN` (`FIGMA_CHANNEL`
  retiré) ; côté rédaction, `FIGMA_CHANNEL` sort de la liste des variables
  sensibles de [maestro/telemetry/redact.py](../maestro/telemetry/redact.py) —
  le token officiel y reste, et tout secret résolu au montage passe de toute
  façon par le registre des secrets servis (#109) ;
- **[core/permissions/designer.json](../core/permissions/designer.json)**
  revue avec les outils du serveur officiel : les `deny` de l'ancien serveur
  (`mcp__figma__delete_node`…) n'ont plus d'objet, et le serveur officiel
  n'expose **aucun outil de suppression dédié** (limite structurelle relevée
  au §6 : l'édition passe par le seul `use_figma`, insécable). La politique
  reste en place, vide — le garde-fou « il propose, il ne remplace pas »
  (docs/04 §3.5) est porté par le prompt du rôle. *(Vide jusqu'à #716, qui l'a
  armée du cran du milieu — voir la note du §6 : le prompt du rôle n'est plus
  seul.)* ;
- les tests ancrés sur l'ancien mode
  ([tests/test_mcp.py](../tests/test_mcp.py), section ⑥) sont réécrits :
  déclaration officielle seule et sans secret en clair, token expurgé, serveur
  omis sans token, politique revue, et **aucune configuration active** ne
  référençant `cursor-talk-to-figma-mcp` ni `FIGMA_CHANNEL`.

Le relais `socket.ts` et le plugin compagnon n'ont jamais été versionnés dans
le repo : leur retrait est purement documentaire (les instructions de
lancement restent au §2.4, trace historique).

### 7.2 Repli « Talk to Figma » (sans token officiel)

La contrainte de la voie officielle demeure (§6) : le token n'est émis qu'à
des **clients OAuth pré-approuvés** (enregistrement dynamique fermé), il est
**emprunté** à un client approuvé et **renouvelé à la main** (refresh non géré
par Maestro). Si aucun token n'est disponible, le pont communautaire reste la
voie 100 % headless et agnostique : re-déclarer le serveur `figma` dans
`core/mcp/designer.json` (déclaration du §2.1, `"optionnel": true` conseillé),
re-renseigner `FIGMA_CHANNEL` (et le réintégrer aux variables sensibles de
`redact.py` — le registre #109 masque de toute façon la valeur résolue), puis
suivre la mise en route du §2.4 (relais + plugin + canal).
