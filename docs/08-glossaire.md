<!-- documentation: produit -->
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

**Human-in-the-loop (HITL)** — Décision humaine insérée dans le flux automatique sur une action sensible. Le déclencheur est l'**acte** — un appel d'outil classé `ask` par la politique de permissions de l'agent, suspendu au vol par le hook `PreToolUse` — et non le texte de la tâche ([docs/04 §1.4bis](./04-specifications-agents.md)). Qui tranche est posé dans la politique : `auto` ou `humain` (le défaut) — un troisième cran, `orchestrateur`, a été **retiré** par #715 faute d'avoir jamais eu de canal ([docs/32](./32-decision-cran-orchestrateur.md), décision #647).

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

## Les noms dans le code

<!-- documentation: développement -->

Le code de Maestro s'écrit en anglais ([docs/55](./55-decision-le-code-s-ecrit-en-anglais.md), chantier #1649). Ce chapitre donne **un nom anglais, et un seul,** à chaque concept du métier que le code nomme. Il fait foi pour tous les lots de traduction et pour tout code neuf.

**Comment s'en servir :**
- **Un nom du métier vient d'ici, jamais d'une traduction à la volée.** Un terme absent entre dans ce chapitre, dans la PR qui l'emploie, avant d'être employé.
- **Les mots courants** (lire, chemin, texte, liste…) se traduisent sans entrée.
- **Un mot français à deux sens a deux entrées** : reprise, rédaction, pièce, dépôt.
- **Le nom dans le code n'est pas la valeur qui voyage.** Une route, une clé JSON ou Redis, un fichier d'état ou une valeur enregistrée garde sa valeur. La constante qui la porte prend le nom d'ici (docs/55 §4, règle 3).
- **L'écran ne change pas.** La personne lit toujours « jalon », « fil », « Chef de projet ».

### Projet et travail

| Terme du métier | Nom dans le code | Ce qu'il désigne |
|---|---|---|
| projet | `project` | ce que la personne déclare et sur quoi travaille l'équipe |
| racine (d'un projet) | `root` | le dossier du projet sur le poste |
| périmètre (d'un projet) | `perimeter` | les motifs de chemins qu'un projet inclut ou exclut |
| appartenance | `ownership` | le rattachement d'un travail à un projet (`projet_id` sur le fil) |
| objectif | `goal` | ce que la personne demande à un run |
| brief | `brief` | l'objectif structuré, validé avant la décomposition |
| cadrage | `framing` | les échanges qui précisent l'objectif avant le brief |
| clarification | `clarification` | une question posée pour cadrer |
| tâche | `task` | l'unité de travail assignée à un agent |
| référence de ticket | `ticket_reference` | le ticket externe qu'une tâche porte |
| étape | `step` | un moment d'une tâche ou d'un run, tel que le journal le consigne |
| tentative | `attempt` | un essai d'exécution d'une tâche |
| livrable | `deliverable` | ce qu'un run produit dans le projet |
| application (du travail) | `apply` | le report du travail d'une tâche dans le projet |
| versionnement | `versioning` | faire d'une racine non versionnée un dépôt git |
| dépôt (git) | `repository` | un dépôt de versions |
| jalon | `milestone` | une étape datée du plan d'un projet |
| cahier des charges | `specification` | le document du mode conception, phase par phase |
| mode conception | `design_mode` | la définition d'un projet en phases validées |
| phase | `phase` | une partie du cahier des charges |
| naissance (d'un projet) | `inception` | un projet qui naît dans la conversation |

### Équipe et agents

| Terme du métier | Nom dans le code | Ce qu'il désigne |
|---|---|---|
| agent | `agent` | une entité IA avec un rôle, des outils et un playbook |
| rôle | `role` | ce qu'un agent fait dans l'équipe |
| gabarit de rôle | `role_template` | un rôle que Maestro sait proposer |
| développeur, base de données, designer, devops, qa | `developer`, `database`, `designer`, `devops`, `qa` | les rôles. Les valeurs enregistrées (`developpeur`, `bdd`…) ne changent pas |
| Chef de projet, orchestrateur | `orchestrator` | l'agent qui décompose, délègue et synthétise. L'écran garde « Chef de projet » |
| orchestration | `orchestration` | la boucle objectif → tâches → agents → livrable |
| équipe | `team` | les agents d'un projet |
| composition (de l'équipe) | `composition` | l'équipe que le modèle compose pour le besoin réel |
| proposition | `proposal` | ce que Maestro propose et que la personne valide |
| renfort | `reinforcement` | compléter l'équipe avant d'exécuter le plan |
| recrutement | `recruitment` | la demande d'un rôle qui manque |
| manque | `gap` | le rôle qui manquerait pour prendre une tâche ou un plan |
| fiche (d'agent) | `definition` | la définition persistée d'un agent du catalogue |
| profil (de rôle) | `profile` | le paramétrage du runtime d'un rôle |
| catalogue | `catalog` | les définitions d'agents disponibles |
| capacité | `capacity` | activer, désactiver, plafond d'instances |
| compétence | `competency` | ce qu'un agent sait faire, pour le routage. `skill` est réservé aux skills d'outillage |
| playbook | `playbook` | le workflow d'un agent |
| routage, assignation | `routing`, `assignment` | l'attribution d'une tâche à un agent |
| classifieur | `classifier` | le modèle rapide qui tranche une assignation ambiguë |
| départage | `tiebreak` | trancher entre des candidats que les compétences ne séparent pas |
| repli (« à assigner ») | `fallback` | une tâche laissée à l'assignation manuelle plutôt que mal routée |
| au plus proche | `closest` | confier une tâche au rôle qui s'approche le plus du métier qui manque |

### Conversation et décisions humaines

| Terme du métier | Nom dans le code | Ce qu'il désigne |
|---|---|---|
| fil | `chat` | la conversation de la personne avec un agent ou avec l'orchestration. Doublet `fil` / `chat` tranché |
| fil global | `orchestration_chat` | le fil de l'orchestration, tous projets |
| conversation | `conversation` | un échange suivi dans le fil |
| geste | `action` | ce que la personne fait depuis une carte ou un écran (pause, reprise, accepter…) |
| acte | `tool_call` | ce qu'un agent s'apprête à commettre : l'outil et ses arguments |
| arbitrage, validation humaine | `approval` | la décision humaine sur un acte ou une tâche sensible |
| arbitre | `arbiter` | le composant qui pose une question à la personne et attend sa réponse |
| décideur | `decider` | qui tranche un acte : `auto` ou `humain`. Ces valeurs ne changent pas |
| décision (d'un agent) | `decision` | ce qu'un agent tranche seul |
| décision humaine | `human_decision` | ce que la personne a tranché, avec ce qu'elle a dit de plus |
| délibération | `deliberation` | le temps d'une décision humaine, et ce qu'elle rend au délai |
| accord (étendu) | `grant` | « oui, et ne me redemande plus pour cet outil » |
| attente, règlement | `pending_item`, `resolution` | ce qui attend quelqu'un pendant un run, et son règlement |
| question (d'un agent) | `question` | une question libre posée à la personne |
| blocage | `blocker` | ce qu'un agent déclare l'empêcher d'avancer |
| prérequis | `prerequisite` | ce qui manque à une tâche pour avancer |
| mot à un pair | `peer_message` | un message adressé d'un agent à un autre |
| boîte aux lettres | `mailbox` | la file de messages d'un agent |
| diffusion | `broadcast` | un message à tous les agents |
| passage de relais | `handoff` | une fin de tâche qui débloque l'aval |
| consultation | `lookup` | ce que l'orchestrateur a le droit de lire pour répondre |
| assistance | `support` | le canal d'aide de la personne, adossé à la documentation |

### Sûreté et permissions

| Terme du métier | Nom dans le code | Ce qu'il désigne |
|---|---|---|
| politique (de permissions) | `policy` | les règles `allow` / `ask` / `deny` d'un agent |
| portée | `scope` | « agir dans son projet n'est pas agir dehors » |
| frontière d'écriture | `write_boundary` | ce qu'un agent peut écrire |
| outil | `tool` | ce qu'un agent appelle pour agir |
| verbe (de Maestro) | `verb` | un outil que Maestro possède : checklist, décision, blocage, question… |
| point de contrôle | `tool_gate` | l'examen de chaque appel d'outil, fermé par défaut. À ne pas confondre avec `checkpoint`, l'état acquis |
| bac à sable | `sandbox` | l'exécution isolée |
| régime (d'exécution) | `mode` | comment une tâche s'exécute : en place, isolée, en conteneur |
| en place | `in_place` | un projet non versionné rempli dans sa racine |
| confinement | `containment` | ce qu'un agent lance ne survit pas à sa tâche |
| espace de travail | `workspace` | le dossier jetable d'une tâche |
| secret, coffre | `secret`, `vault` | une valeur sensible et son stockage chiffré |
| rédaction (masquer) | `redaction` | les secrets masqués avant journalisation |
| garde-fou (d'un run) | `guardrails` | les plafonds posés sur ce qu'un run ingère, lit ou dépense |
| chiffrement | `encryption` | le chiffrement au repos |

### Exécution d'un run

| Terme du métier | Nom dans le code | Ce qu'il désigne |
|---|---|---|
| run, exécution | `run` | une exécution d'orchestration, d'un objectif à son livrable (`run_id`). Doublet `exécution` / `run` tranché : `executions.py` devient `runs.py`, la route `/api/executions` attend le lot du contrat |
| plan (d'un run) | `plan` | les nœuds et arêtes d'un run |
| planification | `planning` | l'étape qui découpe l'objectif en tâches |
| consigner (au journal) | `record` | écrire une étape au journal d'un run |
| graphe | `graph` | le plan tel que l'écran le dessine |
| hôte (de run) | `host` | ce qui porte un run : en process ou détaché |
| reprise (d'un run) | `resume` | repartir après une pause, une coupure ou une limite |
| reprise (d'une configuration) | `migration` | relire ce qu'une version antérieure a écrit |
| état acquis | `checkpoint` | ce qu'un run a payé, et qu'une reprise ne refait pas |
| pause, annulation, relance | `pause`, `cancellation`, `retry` | les gestes sur un run. `retry` désigne aussi la relance automatique d'un échec transitoire |
| rattrapage | `recovery` | une tâche en échec, reprise par le Chef de projet |
| battement | `heartbeat` | le signal qui dit qu'un run vit encore |
| signe de vie | `liveness` | ce qui montre qu'une tâche travaille |
| souffrance | `stall` | le verdict d'un run suspendu trop longtemps |
| bornes | `bounds` | ce qui arrête un run |
| plafond (de dépense) | `spending_cap` | la dépense au-delà de laquelle un run se suspend et demande |
| limite d'usage | `usage_limit` | la limite du fournisseur, qui met un run en pause |
| délai | `timeout` | le temps au-delà duquel on arrête |
| cadence | `cadence` | pourquoi les tâches passent une à une |
| progression | `progress` | l'avancement d'un run par statut de tâche |
| frise | `timeline` | ce que les agents font et se disent, dans l'ordre |
| activité (d'un agent) | `agent_activity` | ce qu'un agent fait pendant qu'il le fait. À distinguer des activités Temporal (`activities`, nom imposé par la bibliothèque) |
| vérification | `verification` | les critères d'une tâche, vérifiés en les exécutant |
| recette | `acceptance` | les critères du brief, vérifiés sur le livrable |
| juge, verdict | `judge`, `verdict` | le modèle qui juge sur pièces, et ce qu'il rend |
| constat | `finding` | un fait relevé sur pièces |
| pièce (preuve) | `evidence` | ce sur quoi un verdict s'appuie |
| récit (de fin) | `recap` | ce qui a été produit, et comment l'essayer |
| bilan (d'un run) | `assessment` | le bilan d'un run terminé, rendu sur pièces |
| cause (d'arrêt) | `stop_cause` | pourquoi un run s'est arrêté |
| échec, panne, extinction | `failure`, `outage`, `shutdown` | une tâche qui échoue, un service qui ne répond pas, un processus qui s'éteint |
| statut, état | `status`, `state` | l'étiquette d'une tâche ou d'un run, et l'état d'un objet. La clé JSON `statut` attend le lot du contrat |

### Coût et observabilité

| Terme du métier | Nom dans le code | Ce qu'il désigne |
|---|---|---|
| coût | `cost` | ce qu'un appel, une tâche ou un run a coûté. Doublet `costs` / `couts` tranché |
| dépense | `spend` | ce qui entre au grand livre, run ou fil |
| grand livre | `ledger` | la comptabilité de coût d'une exécution |
| estimation | `estimate` | l'ordre de grandeur de ce qu'un run proposé coûterait |
| usage | `usage` | tokens, coût, durée et outils d'un appel modèle |
| événement | `event` | ce qui est poussé en temps réel aux écrans. Doublet `events` / `evenements` tranché |
| magasin (d'événements) | `event_store` | où les événements de l'API sont gardés |
| journal | `journal` | le journal structuré d'exécution, une ligne par étape |
| trace | `trace` | l'enregistrement détaillé d'un run |
| jeton (d'API) | `api_token` | le jeton du mode local durci. « tokens » désigne ceux d'un modèle |

### Outillage, sources et fournisseurs

| Terme du métier | Nom dans le code | Ce qu'il désigne |
|---|---|---|
| outillage | `tooling` | ce que Maestro écrit dans un projet pour ses agents |
| pièce (d'outillage) | `item` | un élément de l'outillage, proposé un par un |
| analyse, exploration | `analysis`, `exploration` | comprendre un projet existant en le lisant |
| questionnaire | `questionnaire` | ce que Maestro comprend d'un projet neuf, et ce qu'il demande encore |
| recommandation | `recommendation` | l'outillage recommandé, avec sa raison |
| rédaction (écrire) | `drafting` | de l'outillage recommandé au texte écrit dans le projet |
| correction | `correction` | l'outillage corrigé en langage naturel |
| skill | `skill` | un skill d'agent (SKILL.md) écrit par l'outillage |
| client (d'agents) | `agent_client` | un client installé sur le poste (Claude Code, Codex…) |
| source | `source` | un document que la personne joint à un objectif |
| ingestion, extraction | `ingestion`, `extraction` | des sources au Markdown que l'agent lit |
| aperçu | `preview` | ce que des sources donneraient, sans rien engager |
| téléversement | `upload` | des octets reçus, puis rattachés à un run |
| lecture (d'une source) | `reading` | ce que l'extraction a tiré d'une source : son état, son Markdown, son coût en tokens |
| refus (d'une saisie) | `rejected` | une saisie refusée avec son motif, que la personne peut corriger |
| motif (d'un refus) | `reason` | le code court et stable qui dit pourquoi une saisie est refusée ou une source ignorée |
| motif (de chemin) | `pattern` | une forme de chemin qu'un périmètre inclut ou exclut |
| fournisseur | `provider` | l'adaptateur d'un modèle |
| fournisseur par défaut | `default_provider` | celui du poste, choisi dans les Paramètres |
| famille (de modèles) | `model_family` | Opus, Sonnet, Haiku… et la dernière version de chacune |
| bibliothèque MCP | `mcp_library` | les serveurs MCP qu'on peut admettre |
| registre (officiel) | `registry` | le registre MCP officiel |
| amont, miroir | `upstream`, `mirror` | le registre distant, et sa copie locale |
| admission | `admission` | la porte humaine d'un serveur MCP |
| pool, activation | `pool`, `activation` | les serveurs d'un projet, et ceux qu'un agent monte |

### Poste et stack

| Terme du métier | Nom dans le code | Ce qu'il désigne |
|---|---|---|
| poste | `workstation` | la machine de la personne |
| machine | `machine` | les faits de la machine que Maestro constate |
| sonde, éprouver | `probe` | relever l'état du poste, ou essayer un fournisseur pour de vrai |
| emplacements | `locations` | les trois racines que Maestro pose sur le poste |
| espace (de données) | `namespace` | les clés Redis et dépôts d'une copie de travail |
| atelier | `workshop` | un dossier jetable (atelier de session, atelier d'un passage) |
| ménage | `cleanup` | montrer puis purger ce que Maestro a posé sur le poste |
| purge | `purge` | vider l'état d'exécution |
| ramassage | `sweep` | retirer les espaces jetables que personne n'a retirés |
| dépôt (de fichiers) | `store` | un stockage persistant (projets, agents, playbooks) |
| réglages, paramètres | `settings` | la configuration de la personne |
| accueil | `onboarding` | le premier lancement d'un poste |
| lanceur | `launcher` | ce qui démarre et arrête le produit installé |
| installation | `installation` | ce que l'installation fait tourner |
| sélecteur (de dossier) | `folder_picker` | le sélecteur natif du poste |

### Banc et exercices

| Terme du métier | Nom dans le code | Ce qu'il désigne |
|---|---|---|
| banc | `bench` | la vraie stack qu'on exerce |
| exercice | `exercise` | ce qu'une session construit pour vérifier ce qu'elle vient d'implémenter |
| passage | `trial` | une exécution sur la vraie stack |
| épreuve | `check` | ce qu'un passage vérifie, avec son verdict |
| scénario | `scenario` | un parcours de référence |
| comparatif | `comparison` | un même objectif sur Maestro et sur un agent seul |
| grille | `rubric` | la grille d'un livrable rejoué |
| couverture (des critères) | `criteria_coverage` | les critères d'un livrable, jugés sur pièces |

### Doublets tranchés

| Aujourd'hui | Nom retenu | Ce qui suit |
|---|---|---|
| `controltower/events.py`, `routes/evenements.py` | `events` | `routes/evenements.py` devient `routes/events.py` au lot de `controltower`. Le chemin du WebSocket attend le lot du contrat |
| `telemetry/costs.py`, `routes/couts.py` | `costs` | `routes/couts.py` devient `routes/costs.py` |
| `agents/catalog.py`, `routes/catalogue.py` | `catalog` | `routes/catalogue.py` devient `routes/catalog.py` |
| exécution, run (`/api/executions/{run_id}`) | `run` | `executions.py` devient `runs.py`. La route attend le lot du contrat |
| fil, chat | `chat` | `chat.py` garde son nom, et l'écran garde « fil » |
