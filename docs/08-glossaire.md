# Glossaire — Maestro

Vocabulaire du projet, par ordre alphabétique.

**A2A (Agent-to-Agent)** — Protocole standard (introduit par Google) de communication directe entre agents, bâti sur HTTP/JSON-RPC/SSE. Éléments clés : Agent Card, Task, Message. Complémentaire de MCP — A2A relie les agents entre eux, MCP relie les agents aux outils.

**Agent** — Une entité IA spécialisée (basée sur un modèle d'IA — Claude par défaut au POC, mais **le fournisseur est configurable par agent**) avec un rôle, des outils et un playbook, capable d'exécuter des tâches de façon autonome.

**Auto-assignation (routage)** — Mécanisme qui attribue automatiquement une tâche au bon agent, selon ses compétences déclarées et un classifieur léger.

**Bac à sable (sandbox)** — Environnement isolé (conteneur ou micro-VM) dans lequel un agent exécute du code sans risque pour le reste du système.

**Blackboard (tableau noir)** — Modèle de coordination où les agents communiquent via un **état partagé** (liste de tâches + espace de travail) qu'ils lisent et écrivent, plutôt que par messages directs. Canal principal de communication inter-agents dans Maestro.

**Claude Agent SDK** — Bibliothèque d'Anthropic pour construire des agents en production, bâtie sur le même harnais que Claude Code. Fournit sous-agents, sessions, MCP et exécution parallèle.

**Control Tower** — L'interface web de Maestro : poste de pilotage pour superviser, configurer et interagir avec les agents.

**Couche d'abstraction modèle (model gateway)** — Interface (`ModelProvider`, style **LiteLLM**) qui isole le moteur d'agents du fournisseur d'IA : la config d'un agent porte `fournisseur + modèle + credentials`. Elle rend Maestro **agnostique** — le POC ne câble que Claude, mais OpenAI, Google ou des modèles ouverts/locaux s'ajoutent par configuration, sans refonte (voir O7 / ENF-11).

**Dépendance (de tâche)** — Relation indiquant qu'une tâche ne peut démarrer qu'après l'achèvement d'une autre.

**File de tâches (queue)** — Mécanisme qui stocke les tâches à exécuter et les distribue à des workers ; permet le parallélisme, les relances et la durabilité.

**Handoff (passage de relais)** — Quand un agent confie une tâche ou sous-tâche à un autre et lui transmet le contexte nécessaire, sans repasser par l'orchestrateur.

**Human-in-the-loop (HITL)** — Décision humaine insérée dans le flux automatique sur une action sensible. Le déclencheur est l'**acte** — un appel d'outil classé `ask` par la politique de permissions de l'agent, suspendu au vol par le hook `PreToolUse` — et non le texte de la tâche ([docs/04 §1.4bis](./04-specifications-agents.md)). Qui tranche est posé dans la politique : `auto` ou `humain` (le défaut) — un troisième cran, `orchestrateur`, a été **retiré** par #715 faute d'avoir jamais eu de canal ([docs/31](./31-decision-cran-orchestrateur.md), décision #647).

**LangGraph / CrewAI / AutoGen** — Frameworks d'orchestration multi-agents. Respectivement : graphe d'états (le plus mûr en production), équipages par rôles (le plus simple), conversations de groupe (débats entre agents).

**Langfuse** — Plateforme open source d'observabilité LLM (traces, coûts, évaluation, versionnement de prompts), auto-hébergeable.

**Mailbox (boîte aux lettres)** — File de messages propre à chaque agent, qui permet la messagerie directe point à point entre agents.

**MCP (Model Context Protocol)** — Standard pour connecter des outils et intégrations (Git, base de données, Slack…) aux agents.

**Modèle (Opus / Sonnet / Haiku)** — Les modèles Claude, **défaut du POC** : Opus pour les tâches complexes (orchestrateur), Sonnet pour les workers, Haiku pour les tâches simples et le routage. Le **fournisseur est configurable par agent** via la couche d'abstraction modèle — d'autres modèles (OpenAI, Google, ouverts/locaux) sont utilisables.

**Orchestrateur (Conductor)** — L'agent « chef » qui décompose un objectif en tâches, les délègue et synthétise les résultats. Incarné par l'agent Chef de projet.

**Orchestrateur-workers** — Pattern où un agent central décompose dynamiquement un travail et le délègue à des agents spécialisés travaillant en parallèle, puis agrège leurs résultats.

**Playbook** — Le workflow d'un agent : la suite d'étapes/instructions qu'il suit. Versionné et modifiable depuis l'UI sans redéploiement.

**Pub/sub (publication-abonnement)** — Mode de diffusion où un agent publie un événement et où les agents abonnés le reçoivent, sans couplage direct entre émetteur et destinataires.

**Run (exécution)** — Une exécution concrète d'une tâche par un agent, avec sa trace, ses tokens, son coût et sa durée.

**Tâche / Ticket** — Une unité de travail assignable à un agent, avec un objectif, un format de sortie attendu, des compétences requises et des critères de « terminé ».

**Temporal** — Moteur de workflows durables : reprise automatique sur panne, relances, gestion de tâches longues. Envisagé pour la robustesse en V2.

**Trace** — L'enregistrement détaillé des étapes d'un run (appels d'outils, entrées/sorties, erreurs), base de l'observabilité.

**Worker** — Un processus qui consomme la file de tâches et fait tourner une instance d'agent. Plusieurs workers = plusieurs agents en parallèle.
