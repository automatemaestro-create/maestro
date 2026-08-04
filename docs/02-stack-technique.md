# Stack technique & outils — Maestro

**Version :** 0.1
**Principe directeur :** commencer **simple et composable** (recommandation d'Anthropic), puis ajouter de la robustesse seulement là où c'est mesurablement utile. Chaque choix ci-dessous est une **recommandation argumentée**, avec ses alternatives.

---

## 1. Vue synthétique de la stack recommandée

| Domaine | Choix recommandé | Alternatives sérieuses |
|---------|------------------|------------------------|
| **Abstraction fournisseur** | **Couche « model gateway »** (interface `ModelProvider`, style **LiteLLM**) — choix fournisseur + modèle par agent | Proxy LiteLLM, ou runtime agnostique (LangGraph, Pydantic AI) |
| **Moteur d'agents** | **Claude Agent SDK** (Python *ou* TypeScript) — runtime des agents **Claude** derrière l'abstraction | LangGraph, CrewAI, AutoGen/AG2 |
| **Modèles** | **POC : Claude** — Opus (orchestrateur), Sonnet (workers), Haiku (routage/classif.) | **Tout fournisseur via la couche** : OpenAI, Google, modèles ouverts/locaux |
| **Orchestration de flux** | Agent SDK natif → **LangGraph** si flux d'état complexes | CrewAI (rôles), AutoGen (conversations) |
| **File de tâches / durabilité** | **Temporal** (durable) ou Celery/BullMQ + Redis (simple) | RabbitMQ, AWS SQS |
| **Backend / API** | **FastAPI** (Python) | NestJS / Fastify (Node) |
| **Temps réel (UI)** | WebSocket (FastAPI) + **Redis Pub/Sub** | Server-Sent Events, Supabase Realtime |
| **Communication inter-agents** | **Redis Pub/Sub + boîtes aux lettres**, protocole **A2A** | NATS, RabbitMQ, gRPC |
| **Base de données** | **PostgreSQL** | — |
| **Mémoire vectorielle** | **pgvector** (extension Postgres) | Qdrant, Weaviate |
| **Cache / file / pub-sub** | **Redis** | — |
| **Frontend** | **Next.js + React + TypeScript + Tailwind + shadcn/ui** | Remix, SvelteKit |
| **Observabilité LLM** | **Langfuse** (open source, auto-hébergeable) | LangSmith (intégré LangChain) |
| **Isolation d'exécution** | **Docker** par tâche (→ micro-VM si besoin) | E2B, Firecracker, gVisor |
| **CI/CD & versionnement** | **Git + GitLab + GitLab CI** (choix effectif du projet) | GitHub + GitHub Actions |
| **Conteneurisation / déploiement** | **Docker Compose** (dev) → **Kubernetes** (échelle) | Fly.io, Render, ECS |
| **Authentification** | **Clerk** ou **Auth.js** | Supabase Auth, Keycloak |
| **Extraction de documents** *(Phase 8)* | **markitdown** (unifié) ou `python-docx` + `pypdf` — tout ramené à du Markdown | Unstructured, Tika |
| **Empaquetage bureau** *(Phase 9 — D4)* | **Lanceur/installeur d'abord**, puis **Tauri** (WebView système, backend Python en *sidecar*) | Electron (~150 Mo contre ~10, sans gain ici) |
| **Persistance selon le mode** *(Phase 9 — D3)* | **SQLite** en local/bureau, **PostgreSQL** en serveur — derrière **une seule** couche d'accès | Postgres embarqué, DuckDB |

> **Décision de langage.** Deux options cohérentes :
> - **Option A — Python-centrée (recommandée pour démarrer) :** agents + backend en Python (Agent SDK Python + FastAPI), front en TypeScript. L'écosystème IA/données est le plus riche en Python.
> - **Option B — Tout-TypeScript :** Agent SDK TS + NestJS + Next.js. Avantage : un seul langage de bout en bout, idéal si l'équipe est full-JS.

---

## 2. Moteur d'agents & abstraction fournisseur

> 🔑 **Principe d'agnosticisme (O7 / ENF-11).** Le moteur d'agents est placé **derrière une couche d'abstraction fournisseur** (interface `ModelProvider` / « model gateway », style **LiteLLM**) : la config de chaque agent porte `fournisseur + modèle + credentials`. Le **POC n'implémente que le fournisseur Claude** (via l'Agent SDK), mais l'interface permet d'ajouter OpenAI, Google ou des modèles ouverts/locaux **par configuration, sans refonte**. On ne se lie donc jamais durement à un fournisseur unique.

**Deux stratégies pour les agents non-Claude**, selon le besoin (à trancher hors POC) :

- **Proxy de modèle** (ex. **LiteLLM**) devant le runtime : on garde un runtime unique et on route l'appel modèle vers le fournisseur choisi. Simple, mais on perd une partie des atouts *natifs* de l'Agent SDK (sous-agents, MCP) pour les modèles non-Claude.
- **Runtime agnostique** (ex. **LangGraph**, **Pydantic AI**) pour les agents dont le fournisseur n'est pas Claude, l'Agent SDK restant le runtime optimal des agents Claude.

### Pourquoi le Claude Agent SDK comme premier fournisseur

Le **Claude Agent SDK** est la bibliothèque d'Anthropic pour construire des agents en production, bâtie sur le même « harnais » que Claude Code. Il offre nativement ce dont Maestro a besoin **pour les agents Claude** :

- **Sous-agents (subagents)** : un agent « lead » délègue à des spécialistes ayant leur propre modèle, prompt et outils — exactement le pattern orchestrateur-workers.
- **Exécution parallèle** : les sous-agents travaillent en parallèle avec un **contexte isolé**, sur un système de fichiers partagé, et remontent leurs résultats au lead.
- **MCP (Model Context Protocol)** : standard pour brancher des outils/intégrations (Git, BDD, Slack…).
- **Sessions** : persistance du contexte d'une exécution.
- Disponible en **Python** et **TypeScript**.

> Anthropic recommande de **démarrer avec les API directement** et des patterns simples : beaucoup de besoins se codent en quelques dizaines de lignes. On n'introduit un framework d'orchestration lourd que si la complexité le justifie.

### Quand ajouter un framework d'orchestration ?

| Framework | Modèle | Quand l'envisager |
|-----------|--------|-------------------|
| **LangGraph** | Graphe d'états avec arêtes conditionnelles, *checkpointing*, *human-in-the-loop* | Flux longs, à états, où l'on veut reprise, rejouabilité et le plus de maturité production. C'est le plus *battle-tested*. |
| **CrewAI** | « Équipages » basés sur des rôles | Prototypage rapide d'équipes d'agents ; courbe d'apprentissage la plus faible (~20 lignes pour démarrer). |
| **AutoGen / AG2** | Conversations de groupe (GroupChat) | Quand les agents doivent **débattre**/converger en plusieurs tours. Attention : coûteux (chaque tour = un appel LLM avec tout l'historique). |

**Recommandation Maestro :** Agent SDK natif pour le MVP → **LangGraph** si/quand on a besoin de flux d'états durables et rejouables. Garder l'architecture **modulaire** pour pouvoir changer sans tout refondre.

---

## 3. Files de tâches & parallélisme

Le parallélisme et la fiabilité reposent sur une **file de tâches**.

- **Temporal** *(recommandé pour la robustesse)* : moteur de **workflows durables**. Reprise automatique sur panne, relances, gestion de tâches longues — précieux pour des agents autonomes qui tournent longtemps. Courbe d'apprentissage plus élevée.
- **Celery + Redis** *(Python, simple)* ou **BullMQ + Redis** *(Node, simple)* : très accessibles pour démarrer, suffisants pour un MVP.

**Recommandation :** démarrer avec Celery/BullMQ + Redis ; migrer vers Temporal quand la durabilité des longues exécutions devient critique.

---

## 4. Backend, données et temps réel

- **FastAPI** (Python) : asynchrone, support WebSocket natif, excellent pour exposer l'API et le flux temps réel. (Alternative Node : NestJS.)
- **PostgreSQL** : stockage principal (agents, tâches, runs, configs, playbooks). Robuste, transactionnel, relationnel — adapté au graphe de dépendances et au versionnement.
- **pgvector** : extension Postgres pour la **mémoire vectorielle** (RAG) sans ajouter une base dédiée au départ. (Alternative à fort volume : Qdrant.)
- **Redis** : triple usage — **cache**, **file** (avec Celery/BullMQ) et **pub/sub** pour le temps réel.
- **WebSocket** : pousse les événements (changement de statut, demande de validation) vers l'UI.

---

## 5. Frontend (Control Tower)

- **Next.js + React + TypeScript** : framework web mûr, rendu rapide, écosystème riche.
- **Tailwind CSS + shadcn/ui** : composants accessibles et personnalisables, design cohérent rapidement.
- **dnd-kit** : glisser-déposer pour le tableau Kanban des tâches.
- **Recharts** : graphiques (coûts, débit, taux de réussite).
- **Client WebSocket** : mises à jour temps réel.

---

## 6. Observabilité

- **Langfuse** *(recommandé)* : plateforme open source (licence MIT, auto-hébergeable). Couvre le **traçage** multi-tours, le **versionnement de prompts** avec playground, l'**évaluation**, le suivi des **coûts** et de la **latence**. Idéal pour la souveraineté des données et le multi-framework. Surcoût d'instrumentation modéré (~15 %).
- **LangSmith** *(alternative)* : observabilité de LangChain, surcoût quasi nul, intégration automatique si l'on adopte l'écosystème LangChain.

**Recommandation :** **Langfuse** pour l'auto-hébergement et l'indépendance vis-à-vis du framework.

---

## 7. Sécurité et isolation

- **Docker par tâche** : chaque exécution d'agent dans un conteneur jetable, permissions scopées.
- **Micro-VM (E2B / Firecracker / gVisor)** : isolation renforcée si les agents exécutent du code arbitraire à grande échelle.
- **Branche Git par tâche** : évite les collisions sur les fichiers entre agents parallèles.
- **Gestion des secrets** : coffre (ex. variables chiffrées, Vault) ; jamais de secret dans les prompts ni les logs.
- **Plafonds** : budget par tâche/jour, time-outs, liste d'actions interdites.

---

### 7.1 Ce que le projet local change *(retenu — [docs/24 §2.5](./24-projets-locaux-et-poste-de-travail.md), **Phase 7**)*

Ouvrir un projet de l'utilisateur **déplace la frontière** posée ci-dessus : le contrat du mode
isolé énumère aujourd'hui « aucun autre chemin de l'hôte monté » ([docs/17 §3](./17-isolation-execution.md)).
Trois ajustements suivent, sans rien retirer de l'existant :

- un **second montage** (la racine du projet, ou le répertoire de travail de la tâche) ;
- une **racine canonicalisée** avec liste de racines interdites — la « branche Git par tâche »
  cesse d'être un principe pour devenir le mécanisme réel (§7 ci-dessus) ;
- l'**égress non filtré** (limite connue de [docs/19 §5](./19-securite-modele-de-menace.md))
  devient plus gênant : le code de l'utilisateur est désormais à portée d'un `git push` mal
  intentionné.

En **distribution bureau**, le mode isolé suppose Docker sur le poste : il redevient une option
pour postes équipés, le filet nominal étant alors le **périmètre du projet**.

---

## 8. Intégrations : MCP (outils) et A2A (agents)

Deux protocoles complémentaires :

> 🔑 **MCP relie les agents aux _outils_. A2A relie les agents _entre eux_.**

Le **MCP (Model Context Protocol)** standardise le branchement d'outils aux agents. Serveurs MCP utiles :

- **GitLab / Git** : lecture/écriture de code, MR (merge requests), revues.
- **Base de données** : exécution de migrations et requêtes (agent BDD).
- **CI/CD & cloud** : déclenchement de pipelines, déploiements (agent DevOps).
- **Design** : Figma (agent Designer).
- **Communication** : Slack / e-mail (notifications, validations).
- **Gestion de projet** : Linear / Jira (synchronisation des tickets).

Le **protocole A2A (Agent-to-Agent)**, introduit par Google, standardise la **communication entre agents** (bâti sur HTTP / JSON-RPC / SSE). Ses éléments clés : **Agent Card** (capacités et point d'accès d'un agent), **Task** (unité de travail avec cycle de vie) et **Message** (échange). Au démarrage, une **messagerie interne** (Redis Pub/Sub + boîtes aux lettres) suffit ; adopter A2A devient pertinent dès qu'on veut **interopérer** avec des agents externes ou hétérogènes.

---

## 9. Estimation de coûts (ordres de grandeur)

| Poste | Nature | Remarque |
|-------|--------|----------|
| Appels modèles (Claude) | Variable, à l'usage (tokens) | **Principal poste.** Optimiser : modèle léger par défaut, plafonds, cache de prompts. |
| Hébergement (BDD, Redis, app) | Fixe mensuel | Modeste au début (un serveur ou un PaaS suffit). |
| Observabilité (Langfuse) | Gratuit si auto-hébergé | Coût d'infra uniquement. |
| Bac à sable (conteneurs) | Variable | Dépend du volume d'exécutions. |

> Le **suivi des coûts en temps réel** (via Langfuse + plafonds applicatifs) est une exigence de premier ordre, pas une option.

**Repère concret.** Une fonctionnalité complète (ex. authentification e-mail : 6 agents, ~30 min) revient à **≈ 7 à 12 $ en API** (≈ 4–5 $ avec cache de prompts). Sur un **abonnement Pro à 20 $/mois**, ce n'est pas facturé au token mais cela consomme une part du budget d'usage partagé (fenêtre de 5 h + plafond hebdo). Pour la production en continu, on passe à l'**API au token** (ou au plan **Max**). Déroulé complet : voir [doc 09 — Exemple concret & coûts](./09-exemple-chiffre.md).

**Tarifs API (mi-2026, par M de tokens) :** Haiku 1 $/5 $ · Sonnet 3 $/15 $ · Opus 5 $/25 $ (entrée/sortie) ; lots −50 %, cache de prompts −90 % sur l'entrée mise en cache.

---

## 10. Récapitulatif des décisions

1. **Couche d'abstraction fournisseur** (« model gateway ») en frontière : choix `fournisseur + modèle` par agent. Le **Claude Agent SDK** est le runtime des agents **Claude** (câblé pour le POC), pattern **orchestrateur-workers** natif ; les autres fournisseurs s'ajoutent par configuration.
2. **Python/FastAPI** pour backend+agents (ou tout-TypeScript selon l'équipe).
3. **PostgreSQL + pgvector + Redis** pour données, mémoire et file.
4. **Next.js** pour la Control Tower, temps réel via WebSocket.
5. **Langfuse** pour l'observabilité.
6. **Docker** + **branche Git par tâche** pour l'isolation.
7. **MCP** pour toutes les intégrations.
8. Rester **modulaire** : on doit pouvoir remplacer un agent, un outil, un **fournisseur de modèle** ou le framework d'orchestration sans tout refondre.
