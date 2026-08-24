# Guide de démarrage — Maestro

**Version :** 0.1
Objectif : lancer un **premier prototype** concret (Phase 0). Pensé pour être suivi par une développeuse / un tech lead, et compréhensible par un chef de projet.

---

## 1. Prérequis

| Élément | Pour quoi | Note |
|---------|-----------|------|
| Un **accès modèle Claude** | Faire fonctionner les agents Claude | **Deux modes au choix** (voir §2.1) : abonnement Claude Code (défaut du POC, sans clé) **ou** clé API Anthropic |
| **Python 3.11+** *ou* **Node.js 20+** | Selon l'option de langage choisie | Voir [doc 02 §1](./02-stack-technique.md) |
| **Docker** | Bac à sable d'exécution + bases locales | **Optionnel** : la CI n'en a plus besoin (#344) |
| **Git + un compte GitHub** | Versionnement, intégration code | Le dépôt du projet et sa CI sont hébergés sur GitHub |
| Le **Claude Agent SDK** | Moteur d'agents | Paquet Python ou TypeScript |

> **Rien de tout cela n'est à installer à la main** sur un clone du dépôt :
> [`scripts/setup.sh`](../scripts/setup.sh) (§2, étape 1) installe ce qui manque via le
> gestionnaire de paquets de la plateforme. Ce tableau dit **pourquoi** chaque brique est là.

> ⚠️ Vérifier la documentation officielle d'Anthropic pour les noms de paquets et commandes exacts (l'écosystème évolue vite).

---

## 2. Étapes du POC (Phase 0)

### Étape 1 — Préparer l'environnement

Sur un clone du dépôt, tout le parcours tient en **une commande**, idempotente et non
destructive — [`scripts/setup.sh`](../scripts/setup.sh) (ou, en session Claude Code, la commande
[`/setup`](../.claude/commands/setup.md), qui l'appelle et prend en charge ce qu'un script ne peut
pas faire seul) :

```bash
bash scripts/setup.sh            # monte ce qui manque
bash scripts/setup.sh --check    # diagnostic seul — n'écrit rien
```

Elle couvre les prérequis absents (Python, Node, git, `gh`), le `.venv` et ses dépendances, la
copie de `.env.example` vers `.env`, le hook git de convention de commit, les dépendances de
`apps/web` et les réglages locaux de Claude Code (profil navigateur + serveurs MCP). Il n'y a
**rien à monter côté CI** ([docs/10 §8.1](./10-workflow-git.md)). Détail des étapes et des drapeaux
(`--only`, `--skip`, `--with-infra`, `--no-install`) : [README § Développement](../README.md).

Il reste ensuite **deux gestes humains**, listés par le script sous « Reste à faire » :

1. **Renseigner le `.env`** : choisir un **mode d'authentification** (voir §2.1) et le configurer
   par variables d'environnement, jamais en dur dans le code. Vérification : `maestro-check-env`.
2. **S'authentifier auprès de Figma** (OAuth, un clic par personne via `/mcp`) — seulement si l'on
   travaille sur la couche design.

> Les bases locales (PostgreSQL / Redis / Temporal) ne sont **pas** montées par défaut : elles ne
> servent qu'aux exécutions durables et pèsent plusieurs gigaoctets d'images.
> `bash scripts/setup.sh --with-infra` quand le besoin s'en fait sentir.

#### 2.1 — Deux modes d'authentification (et leur bascule)

Maestro s'authentifie auprès de Claude de **deux façons**, sélectionnables **sans toucher au code** (variable `CLAUDE_AUTH_MODE`) :

| Mode | Quand l'utiliser | À configurer |
|------|------------------|--------------|
| **`subscription`** (défaut du POC) | On démarre sur un **abonnement Claude Code** (Pro/Max/Team) — pas de clé, pas de facturation à l'usage. | Se connecter une fois avec `claude` (login navigateur). En CI, générer un token longue durée : `claude setup-token`, puis le poser dans `CLAUDE_CODE_OAUTH_TOKEN`. |
| **`api_key`** | Facturation à l'usage via la **console Anthropic** (ex. prod, quotas séparés). | Renseigner `ANTHROPIC_API_KEY` (à récupérer sur la console Anthropic). |

**Règle de précédence** (comment le mode est choisi) :

1. Si **`CLAUDE_AUTH_MODE`** est renseigné (`subscription` ou `api_key`), il **fait foi**.
2. Sinon, déduction automatique : `ANTHROPIC_API_KEY` **présente** ⇒ mode `api_key` ; **absente** ⇒ mode `subscription`.

En mode `api_key`, `ANTHROPIC_API_KEY` est **obligatoire** (sinon l'environnement est signalé incomplet). En mode `subscription`, Maestro **neutralise toute clé/bearer** présent dans l'environnement pour garantir que c'est bien l'abonnement (ou `CLAUDE_CODE_OAUTH_TOKEN`) qui est utilisé.

> Vérification rapide : `maestro-check-env` affiche le mode retenu et confirme que l'authentification est prête, **sans** appel réseau ni affichage de secret.

### Étape 2 — Créer l'orchestrateur
- Un script qui prend un objectif en langage naturel.
- Il appelle Claude (modèle puissant) avec un **prompt système d'orchestrateur** (voir playbook du Chef de projet, [doc 04](./04-specifications-agents.md)).
- Sortie attendue : une **liste de tâches structurées** (titre, description, compétences, format de sortie, dépendances) — par exemple en JSON.

### Étape 3 — Créer deux agents workers
- Deux agents (ex. **Développeur** et **BDD**) en tant que **sous-agents** du SDK, chacun avec son prompt et ses outils (système de fichiers, exécution de code).
- Chaque agent reçoit **une** tâche et produit un résultat dans un fichier.

### Étape 4 — Boucler l'orchestration
- L'orchestrateur assigne chaque tâche au bon agent (au début, un simple `if` sur les compétences suffit).
- Exécuter les tâches indépendantes **en parallèle** (asynchrone).
- Récupérer les résultats et les **synthétiser**.

### Étape 5 — Observer
- Logger chaque étape (entrée, sortie, outils, tokens, coût).
- Brancher **Langfuse** pour des traces visuelles et l'évaluation des exécutions — disponible, purement configuratif (voir §2.2).

**Résultat attendu :** taper un objectif → obtenir des tâches → voir 2 agents produire un résultat exploitable.

#### 2.2 — Observabilité Langfuse (optionnelle — traces #81, évaluation #80)

L'intégration Langfuse est **purement configurative** : aucune option CLI, aucun changement de code. Elle se pilote par trois variables d'environnement (gabarit dans `.env.example`) :

| Variable | Rôle |
|----------|------|
| `LANGFUSE_PUBLIC_KEY` | Clé publique du projet Langfuse (Settings → API Keys). |
| `LANGFUSE_SECRET_KEY` | Clé secrète du même projet. Jamais commitée ni logguée. |
| `LANGFUSE_HOST` | Hôte de l'instance. Défaut : `https://cloud.langfuse.com` — pointez votre instance auto-hébergée le cas échéant. |

Avec les **deux clés** renseignées, chaque exécution (`maestro-run`, `maestro-demo`) produit :

- sa **trace** (une par run, id = `run_id` — le même que dans le rapport, le journal #8 et la Control Tower) : une observation par étape, les appels modèle en *générations* avec tokens et coûts au format natif (#55), le reste (validation humaine, messages inter-agents, blocages) en *spans* ;
- ses **scores d'évaluation** en fin de run (#80) : `run-reussi` (booléen — 1 si toutes les tâches sont terminées) et `taux-reussite` (0..1 — part des tâches réussies), posés sur la trace et exploitables dans Langfuse pour filtrer, agréger et comparer les exécutions.

**Mode dégradé sans Langfuse** : sans les deux clés, rien n'est branché — aucun export, aucun score, fonctionnement strictement identique. Et un Langfuse configuré mais **injoignable** ne fait jamais échouer une exécution : l'échec d'envoi est signalé (politique des handlers logging) puis avalé.

En mode file (`--queue`, #41), l'export s'active côté **orchestrateur** seulement — le résultat de chaque tâche, usage des workers compris, y est déjà consigné ; activer aussi les workers compterait chaque tâche deux fois.

---

## 3. Squelette du projet (état réel)

**Tout le code du POC vit dans le paquet Python `maestro/`.** Les dossiers `agents/` et
`core/` à la racine, hérités du cadrage initial, sont des **placeholders** : chacun contient
un README qui renvoie vers le module réel du paquet (ex. `agents/developer/` → `maestro/agents/`,
`core/router/` → `maestro/router/`).

```
(racine du dépôt)
├── maestro/            # 📦 Paquet Python du POC — le code vit ici
│   ├── agents/         #   Catalogue des rôles + runtime outillé (Développeur, BDD…)
│   ├── engine/         #   Boucle d'orchestration + garde-fous (CLI maestro-run)
│   ├── orchestrator/   #   Décomposition objectif → tâches structurées
│   ├── providers/      #   Abstraction fournisseur (ModelProvider, Claude au POC)
│   ├── queue/          #   File de tâches Celery + Redis, workers parallèles (Phase 1)
│   ├── router/         #   Auto-assignation des tâches aux agents
│   ├── sandbox/        #   Espace de travail isolé par tâche
│   ├── telemetry/      #   Journal des étapes, comptabilité de coût par tâche, secrets expurgés
│   ├── check_env.py    #   Vérification d'environnement (maestro-check-env)
│   ├── config.py       #   Modes d'authentification (§2.1)
│   └── demo.py         #   Démo de bout en bout (voir doc 11)
├── tests/              # Tests pytest du paquet
├── apps/
│   ├── api/            # Backend FastAPI (placeholder — Phase 1)
│   └── web/            # Control Tower Next.js (placeholder — Phase 1)
├── agents/             # Placeholders par rôle (README de renvoi vers maestro/agents/)
├── core/               # playbooks/ : stockage versionné des playbooks (#76, §6.2) ; le reste : README de renvoi
├── packages/
│   └── shared/         # Types & schémas partagés (task.schema.json)
├── infra/
│   ├── docker-compose.yml
│   └── migrations/
├── scripts/            # Outillage GitLab (lib.sh, doctor.sh…) + hooks git
└── docs/               # Cette documentation
```

---

## 4. Bonnes pratiques dès le départ

1. **Déléguer précisément.** Toujours fournir à un agent : objectif, format de sortie, outils à utiliser, limites. C'est ce qui évite doublons et oublis.
2. **Commencer simple.** Pas de framework lourd avant d'en avoir besoin ; l'Agent SDK natif suffit pour le POC.
3. **Isoler.** Une branche Git par tâche, un conteneur par exécution.
4. **Plafonner.** Mettre tout de suite un plafond de dépense (budget de l'exécution, adossé à la comptabilité par tâche — #56) et un time-out par tâche. Sur un fournisseur qui ne rapporte pas de coût, le plafond en USD n'a aucune prise : armer alors un **plafond en tokens** (`--plafond-tokens`, #113), toujours opérant — la synthèse dit quel contrôle a réellement tenu.
5. **Tracer.** Logger coûts et étapes dès le premier jour — le coût est comptabilisé **par tâche** et agrégé par exécution (#55, critère MVP n°6).
6. **Garder l'humain dans la boucle.** Les actions sensibles attendent une validation, même au POC.
7. **Modulariser.** Chaque agent et chaque outil doit être remplaçable sans tout casser.

---

## 5. Pièges fréquents à éviter

- **Sur-ingénierie initiale** : vouloir tout l'écosystème (Temporal, LangGraph, micro-VM) avant d'avoir prouvé le cœur.
- **Tâches floues** : un agent mal briefé part dans la mauvaise direction.
- **Coûts non suivis** : sans plafond ni trace, la facture peut surprendre.
- **Secrets dans les prompts/logs** : à proscrire absolument.
- **Agents qui se marchent dessus** : sans isolation, deux agents modifient le même fichier.

---

## 6. Prochaines étapes après le POC

Passer à la **Phase 1 (MVP)** : introduire la file de tâches, le parallélisme à l'échelle, la Control Tower v1 et le human-in-the-loop. Voir la [roadmap](./06-roadmap.md).

### 6.1 — File de tâches et workers parallèles (disponible — ticket #41)

Première brique Phase 1 en place : les tâches de l'orchestrateur peuvent partir dans une
**file Celery + Redis** et être consommées par des **workers séparés** (plusieurs agents
réellement en parallèle, hors du process de l'orchestrateur). Trois terminaux :

```bash
# 1. Redis local (broker + backend de résultats — instance mutualisée, cf. infra/)
docker compose -f infra/docker-compose.yml up -d redis

# 2. Un worker par agent souhaité en parallèle (sous Windows : --pool=solo)
celery -A maestro.queue worker --pool=solo -n agent1@%h
celery -A maestro.queue worker --pool=solo -n agent2@%h   # 2e terminal

# 3. La boucle d'orchestration, branchée sur la file
maestro-run --queue "Créer une API de gestion de tâches"
```

Le statut et le résultat de chaque tâche (livrable, erreur, fichiers produits, usage)
remontent à l'orchestrateur via le backend de résultats ; la synthèse indique le worker
qui a exécuté chaque tâche. Les garde-fous (#9) s'appliquent **côté worker**
(`maestro.queue.worker.configurer_worker`) — une tâche sensible y est refusée par défaut,
et un plafond de dépense configuré là ne voit que la tâche courante (le journal du worker
est reconstruit à chaque message), pas l'exécution entière (#56).
Détails : [`maestro/queue/`](../maestro/queue/) et [`core/queue/README.md`](../core/queue/README.md).

### 6.2 — Playbooks versionnés, appliqués à chaud (disponible — tickets #76 à #78)

Les instructions de chaque agent (son **playbook**, [doc 04 §1](./04-specifications-agents.md))
sont sorties du code : un stockage versionné **append-only** (`core/playbooks/`, racine
remplaçable par `MAESTRO_PLAYBOOKS_DIR`) porte l'historique complet, consultable et
restaurable — restaurer republie une version passée, rien n'est réécrit (EF-24/EF-25).
L'édition passe par la page `/playbooks` de la Control Tower (#77) ou par l'API
(`GET/PUT /api/playbooks/{agent}`, `POST /api/playbooks/{agent}/restaurer`).

L'application est **à chaud** (#78, EF-26) : le moteur — et chaque worker de la file —
relit la version courante **à chaque tâche** ; une version publiée vaut pour l'exécution
suivante, sans redéploiement ni redémarrage (les workers doivent voir le même stockage
que l'API). La version utilisée est **tracée** sur chaque tâche (`playbook_version` sur
le résultat, au journal #8 et dans les métadonnées Langfuse) ; un agent jamais édité
garde exactement son prompt du code.
Détails : [`core/playbooks/README.md`](../core/playbooks/README.md).

### 6.3 — Agents personnalisés : le catalogue devient dynamique (disponible — tickets #72/#73)

Le catalogue d'agents n'est plus figé au code (EF-03) : un **agent personnalisé** se
définit entièrement hors du code — nom, rôle, compétences (tags de routage), playbook
(son prompt système d'exécution), fournisseur/modèle — et se persiste dans un dépôt
dédié (`core/agents/`, un fichier `<nom>.json` par agent, racine remplaçable par
`MAESTRO_AGENTS_DIR`). La création passe par la page `/catalogue` de la Control
Tower (#73) ou par l'API (`GET/POST /api/catalogue`,
`GET/PUT/DELETE /api/catalogue/{nom}`).

Le **catalogue effectif** d'une exécution assemble les agents par défaut du code
(inchangés, prioritaires à score de routage égal) puis les personnalisés du dépôt :
un agent créé est **routable et exécutable** comme un agent du code — la tâche qui
porte ses compétences lui est assignée, et il produit son livrable cadré par son
playbook et son modèle. Le chargement se fait **au câblage** (construction du moteur,
premier message d'un worker, démarrage de l'API) : un agent créé vaut pour les moteurs
construits ensuite ; workers et API doivent voir le même stockage. Les agents par
défaut restent définis par le code : leur fiche est en lecture seule dans le catalogue
(403 en modification/suppression), seul leur playbook s'édite — via `/api/playbooks`
(§6.2). Au POC, le champ `fournisseur` est déclaratif (le moteur exécute sur le
fournisseur configuré, `MAESTRO_PROVIDER`) et un agent personnalisé exécute par le
chemin texte (pas de runtime outillé).
Détails : [`core/agents/README.md`](../core/agents/README.md).

### 6.4 — Chat utilisateur ↔ agent (disponible — tickets #84/#85)

Chaque agent du catalogue — par défaut ou personnalisé (§6.3) — se **contacte
directement** depuis la Control Tower (EF-19) : la page `/chat` de l'UI (#85) ouvre un
fil de conversation par agent, ou par l'API (`GET /api/chat/{agent}` relit le fil,
`POST /api/chat/{agent}/messages` envoie un message et rend la paire message/réponse).
La réponse est produite par le **fournisseur configuré** (`MAESTRO_PROVIDER`), cadrée
par le **playbook courant** de l'agent (§6.2 : la version éditée fait foi, rechargée à
chaque message) et par un cadre de conversation explicite — c'est un échange direct,
pas une tâche à livrer.

Le fil est **persisté** (`core/chat/`, un fichier JSONL append-only par agent, racine
remplaçable par `MAESTRO_CHAT_DIR`) : l'historique se recharge au retour sur la page,
et survit à un redémarrage de l'API. Chaque message — envoi comme réponse — transite
par la **messagerie inter-agents** (#44, la boîte de l'agent) et part en événement
`chat.message` sur le WebSocket `/ws/evenements` : les clients temps réel voient le
message utilisateur dès l'envoi, puis la réponse quand elle tombe. Si la réponse ne
peut pas être produite (fournisseur en échec), l'API répond 502 mais le message
utilisateur reste acquis — relancer ne perd pas le fil. La démo locale
(`scripts/controltower/start.sh --demo`, #65 ; mode explicite depuis #186, §6.10) répond
en **scripté** sur un fil éphémère : aucun modèle appelé, rien d'écrit dans `core/chat/`.
Détails : [`core/chat/README.md`](../core/chat/README.md).

### 6.5 — Contrôle de capacité : activer/désactiver, instances (disponible — ticket #86)

La capacité de chaque agent se pilote depuis les **fiches agents** de la Control Tower
(EF-21) : un bouton active/désactive l'agent, des boutons **+ / −** ajustent son nombre
d'instances — ou par l'API (`POST /api/agents/{nom}/capacite`, corps `{"actif": bool,
"instances": int}`, chaque champ optionnel). Le réglage est **persisté**
(`core/capacite/`, un fichier `<nom>.json` par agent réglé, racine remplaçable par
`MAESTRO_CAPACITE_DIR`) et relu **à chaud** à chaque tâche, comme les playbooks (§6.2) :
il vaut pour la tâche suivante, sans redémarrage — workers et API doivent voir le même
stockage.

L'effet est **réel** sur l'exécution : un agent **désactivé** est écarté des candidats
du routage — la tâche va au meilleur agent restant, ou part en repli « à assigner » —
et refuse aussi la réassignation manuelle (422) ; le plafond d'**instances** borne ses
exécutions simultanées — une tâche routée vers un agent au complet attend qu'un créneau
se libère (par défaut : une instance par agent). L'état de capacité est **reflété en
temps réel** sur les fiches (événement `agent.capacite` sur le WebSocket). Limite POC :
la jauge d'instances est par process (pas encore de coordination inter-workers, EF-16).
Détails : [`core/capacite/README.md`](../core/capacite/README.md).

### 6.6 — Scalabilité horizontale : run de charge multi-instances (disponible — ticket #100)

Le nombre d'instances (§6.5) est un **vrai parallélisme par agent** : le moteur exécute
en même temps jusqu'à N tâches d'un même agent, où N est son plafond d'instances — un
agent désactivé reste à 0 (repli « à assigner »). Le **plafond global** du run reste
prioritaire (plafond transverse) : `maestro-run --parallele <n>` le pose depuis la CLI,
la capacité par agent s'applique en dessous. Sans le flag, seul le plafond par agent
joue.

Run de charge de démonstration — plusieurs tâches indépendantes pour un même agent :

```bash
# 1. Monter l'agent visé à 2 instances : fiches agents de l'UI (boutons + / −),
#    ou l'API de la Control Tower si elle tourne (§6.5)
curl -X POST http://localhost:8000/api/agents/bdd/capacite \
  -H "Content-Type: application/json" -d '{"instances": 2}'

# 2. Lancer le run de charge, plafond global posé pour ménager le fournisseur
#    (les aléas croissent avec la concurrence — docs/13 §4.3 ; la relance #91
#    est armée par défaut)
maestro-run --parallele 2 --publier "Écrire quatre requêtes SQL d'analyse \
indépendantes (ventes par mois, par région, par produit, par client)"
```

Dans la Control Tower, la fiche de l'agent porte alors **plusieurs tâches à la fois**
(« n en parallèle ») et ne repasse « Libre » qu'à l'issue de la dernière ; coûts,
journal et grand livre restent attribués **par tâche**, sans mélange entre instances.
Le scénario complet est rejoué sur fournisseurs factices dans
[`tests/test_scalabilite.py`](../tests/test_scalabilite.py) (parallélisme réel borné à
N, agent désactivé à 0, priorité du plafond global, comptabilité et temps réel par
tâche). Limite POC inchangée : la jauge est **par process** (en distribué, chaque
worker borne les siennes — la coordination inter-workers viendra avec la persistance
partagée, EF-16).

### 6.7 — Serveurs MCP par agent : capacités externes déclarées (disponible — tickets #104 à #106)

Un agent peut se voir brancher des **capacités externes** (Slack, gestion de tickets,
Figma, cloud…) via le **Model Context Protocol**, sans coder de connecteur ad hoc : on
**déclare** les serveurs MCP d'un agent, le moteur les **monte** sur ses exécutions
outillées. Changer d'outil (Linear plutôt que GitLab, par exemple) = changer la
déclaration, pas le code.

**Déclarer un serveur sur un agent** — un fichier par agent dans `core/mcp/`
(`<agent>.json`, racine remplaçable par `MAESTRO_MCP_DIR`), de la forme
`{"serveurs": [...]}` où chaque serveur est une **commande locale** (`type` « stdio » :
`commande` + `args` + `env`) ou un **endpoint distant** (« sse »/« http » : `url` +
`headers`) — format détaillé : [doc 04 §6](./04-specifications-agents.md) :

```jsonc
// core/mcp/qa.json — l'agent qa reçoit un serveur de gestion de tickets GitLab
{
  "serveurs": [
    {
      "nom": "gitlab",
      "type": "stdio",
      "commande": "npx",
      "args": ["-y", "@zereight/mcp-gitlab"],
      "env": { "GITLAB_PERSONAL_ACCESS_TOKEN": "${GITLAB_TOKEN}" }
    }
  ]
}
```

La déclaration est **validée à la lecture** et relue **à chaud** à chaque tâche, comme
les playbooks (§6.2) : une déclaration ajoutée ou corrigée vaut pour la tâche suivante,
sans redémarrage ; une déclaration invalide est un **échec de tâche propre** (cause
exacte consignée, l'agent n'exécute pas), jamais un montage à moitié. Les serveurs
n'équipent que les **exécutions outillées** — le chemin texte n'expose aucun outil, MCP
compris. Côté fournisseur Claude, la session est **verrouillée sur la seule liste
déclarée** (`strict_mcp_config` — aucune config MCP ambiante) et le premier tour du
modèle n'est envoyé qu'une fois tous les serveurs déclarés **connectés** : un serveur
en échec (démarrage, authentification) ou jamais connecté lève une erreur propre
(`McpServerUnavailable`, serveur et cause nommés) **avant** tout appel modèle — jamais
relancée (ENF-06 : configuration ou secret à corriger). Les fiches agents de la page
`/catalogue` affichent les serveurs déclarés (lecture seule).

**Gestion des secrets** — les déclarations sont de la **configuration versionnée** :
les tokens n'y figurent **jamais en clair**. Les valeurs d'`env`/`headers` portent des
références `${VARIABLE}` résolues depuis l'environnement (`.env`, cf.
[`.env.example`](../.env.example)) au moment du montage — les valeurs effectives ne
vivent qu'en mémoire ; une variable absente rend le serveur indisponible (échec propre
avant tout appel). La forme publique (API/UI) **masque** toute valeur littérale et les
journaux caviardent les motifs de tokens connus (`xoxb-`, `glpat-`…).

**Pilotes disponibles** — trois intégrations réelles servent de référence :

- **Slack** ([doc 15](./15-pilote-mcp-slack.md), ticket #105) : l'agent `devops`
  (serveur déclaré dans [`core/mcp/devops.json`](../core/mcp/devops.json), token via
  `${SLACK_BOT_TOKEN}`) poste les **notifications de supervision** d'un run — fin de
  run, validation humaine en attente — via `maestro-run --notifier devops` ;
- **Tickets de la forge** (ticket #106, passé à GitHub par #412) : l'agent `qa`
  (serveur déclaré dans `core/mcp/qa.json`, token via `${GITHUB_TOKEN}`, périmètre
  borné par les **scopes du jeton** — Issues et Pull requests du seul dépôt du
  projet ; serveur **optionnel**, omis du montage sans token) **lit et crée des
  tickets** pendant un run réel. Le pilote d'origine visait GitLab : son rapport
  [doc 16](./16-pilote-mcp-tickets-gitlab.md) décrit cet état-là, au passé —
  décision et procédure du jeton dans [`core/mcp/README.md`](../core/mcp/README.md) ;
- **Figma** ([doc 20](./20-pilote-mcp-figma.md), tickets #115/#125/#128) : l'agent
  `designer` (serveur MCP **officiel** Figma déclaré dans `core/mcp/designer.json`,
  token OAuth fourni par l'humain via `${FIGMA_OAUTH_TOKEN}`, serveur **optionnel** —
  omis du montage sans token) **crée et lit des éléments** d'un fichier Figma.

Le mode d'obtention du secret varie d'un outil à l'autre (token statique,
appairage, OAuth verrouillé) : grille complète dans [doc 21](./21-configuration-mcp.md).

Le socle est rejoué sans réseau dans [`tests/test_mcp.py`](../tests/test_mcp.py)
(déclarations et validation à la lecture, résolution des secrets, montage par le
moteur, application à chaud, échecs propres, couture SDK) et le volet catalogue dans
[`tests/test_controltower.py`](../tests/test_controltower.py). Détails :
[`core/mcp/README.md`](../core/mcp/README.md).

### 6.8 — Workflows durables : reprise sur panne et persistance (disponible — tickets #94 à #97)

Première brique de la **Phase 3 « Durabilité & scalabilité »** ([roadmap](./06-roadmap.md)) :
les runs peuvent devenir **durables** en s'exécutant sur **Temporal**. Un run interrompu
(CLI tuée, worker qui tombe, API redémarrée) **reprend où il en était sans repayer
l'amont** — les tâches déjà réussies ne sont pas ré-exécutées, leur résultat vient de
l'historique du workflow. Le modèle : **un run = un workflow, une tâche = une activité**
(le workflow orchestre — planification, dépendances/handoffs, blocages — et délègue tout
l'I/O, dont les appels modèle, à des activités, condition du déterminisme que Temporal
exige). Le mode est **opt-in** : sans le drapeau, l'exécution en process reste le défaut.

**Mise en route** — Temporal tourne en local via le docker-compose du dépôt (serveur de
développement tout-en-un, UI web comprise, état persisté en SQLite dans un volume) :

```bash
# 1. Le serveur Temporal (gRPC localhost:7233, UI http://localhost:8233)
docker compose -f infra/docker-compose.yml up -d temporal

# 2. Un run durable — un worker est embarqué dans ce process, le temps du run
maestro-run --durable "Créer une API de gestion de tâches"
```

En mode embarqué, garde-fous (plafond, time-out, validation console), relance
automatique (#91) et publication temps réel (#46) opèrent **comme en local** : les
activités partagent le logger de trace du process. Pour un déploiement réel, on fait
plutôt tourner un **worker persistant** à côté (à l'image d'un worker Celery) :

```bash
python -m maestro.durable.worker   # sert la file « maestro-durable » jusqu'à Ctrl-C
```

**Reprise sur panne** — le run vit côté Temporal, pas dans le process qui le suit. Si ce
process disparaît (CLI tuée, API redémarrée), on rattache un process neuf par le seul
`run_id` (celui qu'affichent le journal, la trace et la Control Tower) :

```bash
maestro-run --reprendre <run_id>   # implique --durable ; l'objectif est déjà dans le run
```

La reprise **interroge l'état acquis** du run (planification et tâches abouties),
**consigne la reprise** (césure visible sur la trace et le fil temps réel, avec sa raison
et ce qui ne sera pas repayé), puis attend l'issue. Si c'est le **worker** qui est tombé,
aucune commande n'est nécessaire : le run repart de lui-même dès qu'un worker revient sur
la file. Les deux couches de relance **composent proprement** : l'aléa **fournisseur**
reste du ressort de la relance applicative (#91), qui vit dans l'activité ; l'aléa
**d'infrastructure** (perte du worker) est du seul ressort de Temporal — les échecs
applicatifs déterministes (plan invalide) ne sont jamais rejoués.

**Persistance de l'état Control Tower** (#97) — l'état du poste de pilotage (exécutions,
grands livres, analytics, tâches, agents, validations) est projeté depuis un **flux
d'événements**. Un **journal durable** (`EventLog`) consigne chaque événement et le
**rejoue au démarrage** de l'API : l'état survit désormais au redémarrage du process
(auparavant, seuls les artefacts JSON du moteur subsistaient). En production, le journal
est une **liste Redis** (event sourcing, sur l'instance déjà mutualisée avec la file, le
bus et les boîtes) ; en test et mono-process, un journal mémoire.

**Limites connues (POC).** Le mode durable est **opt-in** et le moteur en process reste le
défaut — il ne doit jamais régresser pendant ce chantier. `--durable` n'est **pas encore
combinable** avec `--queue` (frontières d'exécution exclusives), ni avec `--messagerie` /
`--validation-ui` / `--notifier` / `--parallele` (en durable, dépendances et handoffs
passent par le workflow et la validation humaine par la console) : ces transports Redis et
le plafond global viendront dans un lot ultérieur. ⚠ **Ces refus ne sont pas seulement un
lot en retard** : le mode durable et l'arrêt humain sur brief — défaut de la Control Tower —
sont mutuellement exclusifs par construction, et lever le verrou suppose de réécrire les
attentes humaines en signaux de workflow. C'est instruit et chiffré par
[doc 28](./28-decision-frontiere-execution-run.md) (#350), qui **écarte Temporal pour
l'instant** au profit d'un hôte de run détaché et nomme ce qui rouvrirait la question. Le
journal des événements n'a **pas de
rétention bornée** (la liste croît avec l'historique, pour préserver l'historique complet)
et la bascule vers PostgreSQL (entités RUN/TASK, [doc 03](./03-modele-de-donnees.md)) avec
sa politique de rétention viendra ensuite substituer un stockage requêtable au rejeu
intégral.

Tout est rejoué **sans serveur Temporal réel ni appel modèle** dans
[`tests/test_durable.py`](../tests/test_durable.py) : l'**environnement de test Temporal**
(serveur *time-skipping*, binaire téléchargé une fois puis mis en cache) et des
fournisseurs factices couvrent l'exécution workflow + activités, le blocage aval, la
reprise sans repayer l'amont et la planification invalide ; la persistance de l'état
Control Tower est couverte dans
[`tests/test_controltower.py`](../tests/test_controltower.py). Détails :
[`maestro/durable/`](../maestro/durable/) et [`infra/README.md`](../infra/README.md).

### 6.9 — Auto-amélioration des playbooks : proposer une révision depuis les échecs (disponible — ticket #111)

Deuxième brique de la **Phase 3** ([roadmap](./06-roadmap.md)) : après un run en échec, une
**analyse déclenchée à la demande** relit les échecs consignés d'un agent (journal #8 → pont #46)
et fait rédiger, par la couche fournisseur, une **version révisée de son playbook**. Le
résultat est une **proposition en brouillon** (provenance « proposition », §6.2) — jamais
la version courante, **jamais chargée par le moteur** tant qu'un humain ne l'a pas appliquée.

```bash
# Déclencher l'analyse des échecs d'un agent sur un run donné (un appel modèle)
curl -X POST http://localhost:8000/api/playbooks/developpeur/propositions \
     -H 'Content-Type: application/json' -d '{"run_id": "<run_id>"}'
```

Les propositions apparaissent **en tête de l'historique** de l'éditeur de playbook (page
`/playbooks`), avec leur justification : **Appliquer** publie le contenu candidat comme
version courante — donc chargée à chaud dès la tâche suivante (#78) — et **Rejeter** retire
le brouillon sans toucher à la version courante. Le déclenchement reste **manuel par
prudence sur le coût** : une analyse = un appel modèle, à réserver aux échecs qu'on soupçonne
de venir du playbook. Boucle complète, garde-fous et limites :
[doc 22](./22-auto-amelioration-playbooks.md) ; tests sur fournisseurs factices :
[`tests/test_auto_amelioration.py`](../tests/test_auto_amelioration.py).

### 6.10 — Lancer la Control Tower en local : mode réel par défaut (ticket #186)

Le lancement local tient en une commande, qui démarre l'API, l'UI Next.js, ouvre le
navigateur et arrête tout à la fermeture de la fenêtre (#149, #200) :

```bash
# Mode RÉEL (défaut) : maestro-api sur Redis, journal durable des événements (§6.8)
bash scripts/controltower/start.sh

# Mode DÉMO : scénario factice sur bus mémoire, aucun Redis requis
bash scripts/controltower/start.sh --demo
```

**Le mode réel est le défaut** depuis #186. La simulation a longtemps été le seul moyen de
« regarder l'UI vivre » ; elle n'a plus à l'être, et surtout un utilisateur qui découvre le
produit ne doit pas prendre des données factices pour la réalité. Ce que cela change :

- **Redis est une dépendance dure, vérifiée avant tout** (`maestro-api --verifier-redis`,
  qui résout `REDIS_URL` comme le fait l'API elle-même). Absent, le script s'arrête en
  donnant la commande exacte — `docker compose -f infra/docker-compose.yml up -d redis` —
  et **ne retombe jamais en douce sur la démo** : un repli silencieux est précisément ce qui
  ferait confondre les deux mondes. Le contrôle a lieu **avant** l'arrêt de la session en
  place : un Redis manquant n'aura pas au passage coupé une Control Tower qui tournait.
- **Le poste de pilotage démarre vide, et le dit.** Sans run, il n'y a ni tâche, ni
  événement, ni validation : l'UI affiche alors quoi faire (lancer
  `maestro-run --publier "<objectif>"`, ou repasser en `--demo`) au lieu d'aligner des
  panneaux à zéro qui feraient croire à une panne. Une API **injoignable**, elle, reste
  signalée par sa bannière d'erreur — l'écran vide *connecté* et l'écran vide *muet* ne se
  diagnostiquent pas pareil.
- **L'historique survit au redémarrage** : en mode réel, les événements passent par le
  journal durable de #97 (liste Redis) et sont rejoués à l'ouverture de l'API.

Le mode `--demo` reste le bon choix pour le **développement front**, le skill `/verify` et
les captures de `/milestone-presentation` : app réelle, mêmes endpoints, mais bus mémoire et
scénario simulé — Kanban peuplé, coûts par tâche, une validation laissée en attente,
pulsation périodique, chat scripté sur un fil éphémère (§6.4). Tout le reste est **identique
dans les deux modes** : ports (`MAESTRO_PORT_API`/`MAESTRO_PORT_UI`, dédiés par worktree),
dossier de logs, nettoyage des sessions précédentes, chien de garde du navigateur et
`--stop`. Détail d'usage : skill [`control-tower`](../.claude/skills/control-tower/SKILL.md).

### 6.11 — Lancer, suivre et annuler un run (tickets #185, #187)

En mode réel, le poste démarre vide : **c'est un run qui le remplit**. Deux voies, qui
alimentent la même projection — le suivi ne distingue pas leur origine.

**Depuis le dépôt** (ce que propose l'écran vide) :

```bash
maestro-run --publier "Prototyper un mini-CRM"
```

`--publier` est ce qui pousse les événements vers la Control Tower ; sans lui, le run
se déroule mais le poste reste vide. Les autres drapeaux du moteur restent
disponibles (`--parallele` §6.1, `--durable` §6.8).

**Depuis la Control Tower**, par l'API (contrat figé : [doc 05 §6.1](./05-interface-control-tower.md)) :

```bash
# Lancer : 202 + le run_id, rendu AVANT que le run ne produise quoi que ce soit
curl -X POST http://127.0.0.1:8000/api/executions \
  -H 'Content-Type: application/json' \
  -d '{"objectif": "Prototyper un mini-CRM", "plafond_cout_usd": 5.0}'

curl http://127.0.0.1:8000/api/executions              # suivre : les runs, récents d'abord
curl -X POST http://127.0.0.1:8000/api/executions/<run_id>/annuler   # annuler
```

Trois choses à savoir :

- **Le lancement ne bloque pas.** Le run part en arrière-plan et son `run_id` est rendu
  tout de suite : c'est ce qui permet de l'afficher « en cours » puis de le suivre par le
  flux temps réel, sans attendre la fin. Une erreur survenue en arrière-plan (fournisseur
  injoignable…) devient le **statut du run**, pas un 500 — la requête de lancement, elle,
  est déjà partie.
- **Les garde-fous se posent au lancement** : `plafond_cout_usd`, `plafond_tokens`,
  `timeout_tache_s`, `parallelisme`. Absents (`null`), le moteur garde ses défauts ; hors
  bornes ou objectif vide, la requête est refusée en **422** et **aucun run ne part**.
- **Un run terminé n'est plus annulable** : `409` plutôt qu'une annulation de façade
  (`404` si le `run_id` est inconnu).

Un run peut porter une **référence de ticket externe** (`{"ticket": {"id": "#42", "url": "…"}}`,
#187) : elle voyage du plan jusqu'à la projection, ressort par le REST et **survit au rejeu**
du journal durable — la carte garde donc son lien après un redémarrage de l'API.

Couverture : [`tests/test_executions.py`](../tests/test_executions.py) (routes et référence de
ticket, sur l'app réelle en bus mémoire, moteur remplacé par un double) et
[`tests/test_controltower_mode_reel.py`](../tests/test_controltower_mode_reel.py) (invariants du
lanceur : mode réel par défaut, `--demo` explicite, diagnostic quand Redis manque).
