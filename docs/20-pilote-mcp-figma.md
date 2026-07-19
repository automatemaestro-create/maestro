# Pilote MCP Figma — l'agent designer crée dans un fichier (ticket #115)

**Version :** 0.1
Troisième pilote concret du socle MCP (#104, parent #101) : l'agent **designer**,
équipé d'un serveur MCP « Talk to Figma », **crée des éléments dans un fichier
Figma en temps réel** (frames, formes, textes — visibles dans le fichier pendant
le run) et lit le contenu existant pour s'y adapter. Cette page consigne le
choix du serveur, l'architecture d'appairage, les exigences sur les secrets et
la démonstration réelle.

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
      }
    }
  ]
}
```

Conformément au contrat du socle (#104, [docs/04 §6](./04-specifications-agents.md)),
le serveur est monté **à chaud** sur chaque exécution outillée de l'agent, et
une variable absente rend le serveur indisponible : sans appairage Figma
(`FIGMA_CHANNEL` vide), la tâche échoue proprement **avant tout appel modèle**.

La référence `${FIGMA_CHANNEL}` joue un double rôle : elle **conditionne le
montage** à un appairage effectif, et elle **enregistre le canal au registre de
rédaction** au moment de la résolution (#109) — le canal est masqué
(`[secret masqué]`) partout où il réapparaîtrait en sortie.

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
- Le serveur est monté sur **toute exécution outillée** du designer : une tâche
  de design sans besoin Figma échouera au montage si `FIGMA_CHANNEL` est
  absent — retirer la déclaration (ou renseigner le canal) selon la campagne.
- Le relais demande `bun` (script du dépôt amont) ; le serveur MCP lui-même
  tourne sous node via `npx`.
- Sécurité locale : le relais écoute sur `localhost:3055` sans autre
  authentification que le canal — ne pas l'exposer au-delà du poste.
