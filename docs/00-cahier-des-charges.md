# Cahier des charges — Maestro

**Version :** 0.1 (cadrage)
**Date :** 28 juin 2026
**Statut :** Brouillon de travail

---

## 1. Contexte et vision

### 1.1 Le constat

Construire un logiciel mobilise plusieurs métiers : gestion de projet, développement, base de données, infrastructure, design, qualité. Les assistants IA actuels aident métier par métier, mais ne **collaborent pas** entre eux et requièrent qu'un humain transporte le contexte de l'un à l'autre.

### 1.2 La vision

**Maestro** est une plateforme qui fait fonctionner une **équipe d'agents IA spécialisés** comme une véritable équipe produit. Vous donnez un objectif ; un agent orchestrateur le découpe en tâches, les confie automatiquement aux bons spécialistes, ceux-ci travaillent **en parallèle et de façon autonome**, et vous gardez le contrôle via une **console de supervision**.

L'utilisateur passe ainsi du rôle d'« opérateur » (qui exécute) à celui de **chef d'orchestre** (qui dirige et arbitre).

**Indépendance vis-à-vis des modèles.** Maestro n'est lié à **aucun fournisseur d'IA**. Chaque agent est **configurable** pour tourner sur le fournisseur et le modèle de son choix — Claude, mais aussi OpenAI, Google, ou des modèles **ouverts/locaux**. Le POC démarre sur Claude, mais derrière une **couche d'abstraction** qui rend l'ajout d'un autre fournisseur **déclaratif** (de la configuration, pas une refonte).

### 1.3 Proposition de valeur

- **Pour un fondateur / chef de projet :** transformer une idée en travail concret réparti, sans gérer manuellement chaque outil.
- **Pour une équipe technique :** automatiser les tâches répétitives et paralléliser le travail multi-métiers.
- **Différenciateur :** ce n'est pas *un* assistant, c'est une **équipe coordonnée**, pilotable et personnalisable — et **agnostique au fournisseur d'IA** (chaque agent sur le modèle de son choix, sans lock-in).

---

## 2. Objectifs

### 2.1 Objectifs produit

| Objectif | Description | Indicateur de succès |
|----------|-------------|----------------------|
| O1 — Spécialisation | Disposer d'agents experts par métier, chacun avec ses outils | ≥ 5 agents spécialisés opérationnels |
| O2 — Autonomie | Chaque agent mène ses tâches de bout en bout sans micro-pilotage | ≥ 70 % des tâches terminées sans intervention humaine |
| O3 — Assignation automatique | Les tâches sont routées vers le bon agent sans intervention | ≥ 90 % de routage correct (mesuré sur un jeu de test) |
| O4 — Parallélisme | Plusieurs agents travaillent simultanément | ≥ 5 agents actifs en parallèle sans collision |
| O5 — Supervision | Une interface unique pour monitorer, configurer, interagir | Toutes les actions clés réalisables depuis l'UI |
| O6 — Évolutivité des workflows | Les instructions de chaque agent évoluent sans redéploiement | Modification d'un *playbook* en < 1 min, sans redéploiement |
| O7 — Indépendance fournisseur | Chaque agent configurable avec n'importe quel fournisseur/modèle (y compris hors Anthropic), derrière une couche d'abstraction | Ajouter un fournisseur = configuration (pas de refonte) ; ≥ 1 fournisseur non-Anthropic branché en V1 |

### 2.2 Non-objectifs (hors périmètre initial)

- Remplacer entièrement une équipe humaine sans aucune supervision.
- Garantir un code « production-ready » sans relecture sur des sujets critiques.
- Livrer **dès le POC** des intégrations clé-en-main pour des dizaines de fournisseurs de modèles. L'**agnosticisme** est un objectif de premier ordre (O7) porté par une couche d'abstraction, mais le POC n'implémente concrètement que **Claude** ; les autres fournisseurs s'ajoutent ensuite par configuration.
- Marketplace publique d'agents tiers (envisageable en V2+).

---

## 3. Personas et cas d'usage

### 3.1 Personas

- **Samyen — Fondateur / chef de projet (utilisateur principal).** Pilote le produit, n'a pas besoin d'écrire le code lui-même, veut superviser et arbitrer.
- **La développeuse / le tech lead.** Intègre Maestro à un dépôt existant, configure les agents, valide les actions sensibles.
- **L'agent (utilisateur « non humain »).** Consomme des tâches, produit des résultats, demande des validations.

### 3.2 Cas d'usage principaux

1. **De l'idée aux tickets.** « Ajoute l'authentification par e-mail. » → l'agent Chef de projet crée 5 tickets (schéma BDD, endpoints, UI, tests, déploiement) avec leurs dépendances.
2. **Assignation et exécution automatiques.** Chaque ticket part vers l'agent compétent ; ceux sans dépendance démarrent immédiatement en parallèle.
3. **Supervision en direct.** Le fondateur voit, sur un tableau Kanban temps réel, qui fait quoi, où en est chaque tâche, le coût cumulé.
4. **Intervention humaine.** Une action sensible (déploiement) attend une validation ; le fondateur approuve ou refuse depuis l'UI.
5. **Personnalisation d'un agent.** On modifie le *playbook* du Designer pour qu'il respecte une charte graphique précise ; le changement s'applique à la tâche suivante.
6. **Discussion directe.** On ouvre un chat avec l'agent DevOps pour lui demander d'expliquer un choix ou de corriger le tir.
7. **Pilotage de la capacité.** On augmente le nombre d'instances de l'agent Développeur pour absorber une charge importante.
8. **Initier un projet sur son poste.** L'utilisateur désigne un dossier de son disque (vide, ou dépôt existant) ; les agents y travaillent réellement — les livrables atterrissent dans *son* projet, pas dans un dossier de sortie à recopier à la main. *(Proposé par [docs/24 §2](./24-projets-locaux-et-poste-de-travail.md), décision D1 en attente.)*
9. **Partir d'un document.** L'utilisateur joint un cahier des charges (`.docx`, `.pdf`, Markdown) ou un dossier de références plutôt que de tout retaper ; le Chef de projet en tire un **brief structuré**, pose ses questions, et ne décompose qu'une fois le brief validé. *(Proposé par [docs/24 §3](./24-projets-locaux-et-poste-de-travail.md), décision D5 en attente.)*

---

## 4. Exigences fonctionnelles

> Convention : **DOIT** = indispensable au MVP · **DEVRAIT** = important V1 · **POURRAIT** = souhaitable V2+.

### 4.1 Agents spécialisés

- **EF-01 (DOIT)** — Le système fournit des agents préconfigurés : Chef de projet, Développeur, Base de données, DevOps, Designer, QA.
- **EF-02 (DOIT)** — Chaque agent possède un rôle, un jeu d'outils et un *playbook* (workflow d'instructions).
- **EF-03 (DEVRAIT)** — L'utilisateur peut créer un nouvel agent et le configurer entièrement.
- **EF-04 (DOIT)** — Chaque agent déclare ses **capacités** (tags/compétences) servant au routage.

### 4.2 Autonomie

- **EF-05 (DOIT)** — Un agent exécute une tâche de bout en bout (analyse → action → résultat) sans pilotage pas-à-pas.
- **EF-06 (DOIT)** — Un agent peut utiliser des outils (lecture/écriture de fichiers, exécution de code, appels d'API via MCP).
- **EF-07 (DEVRAIT)** — Un agent peut **créer des sous-tâches** ou solliciter un autre agent.
- **EF-08 (DOIT)** — Un agent demande une **validation humaine** pour les actions classées sensibles.

### 4.3 Assignation automatique des tâches

- **EF-09 (DOIT)** — À la création d'une tâche, le système identifie l'agent le plus pertinent (compétences + charge actuelle).
- **EF-10 (DEVRAIT)** — Le routage combine correspondance de capacités et classification par un modèle léger.
- **EF-11 (DOIT)** — L'utilisateur peut **réassigner manuellement** une tâche à un autre agent.
- **EF-12 (DEVRAIT)** — Le système gère les **dépendances** entre tâches (une tâche ne démarre que si ses prérequis sont terminés).

### 4.4 Parallélisme

- **EF-13 (DOIT)** — Plusieurs agents s'exécutent simultanément sur des tâches indépendantes.
- **EF-14 (DOIT)** — Chaque exécution dispose d'un **contexte isolé** (pas de fuite entre agents).
- **EF-15 (DEVRAIT)** — Le système prévient/résout les **collisions** sur une ressource partagée (ex. même fichier).
- **EF-16 (POURRAIT)** — Mise à l'échelle horizontale : plusieurs instances d'un même agent.

### 4.5 Communication et coordination inter-agents

- **EF-31 (DOIT)** — Les agents partagent un **état commun** (liste de tâches + espace de travail) servant de *tableau noir* de coordination ; les tâches dépendantes se **débloquent automatiquement** à l'achèvement de leurs prérequis.
- **EF-32 (DOIT)** — Un agent peut envoyer un **message direct** à un autre agent (messagerie point à point) pour poser une question, passer un relais (*handoff*) ou notifier un résultat — sans repasser systématiquement par l'orchestrateur.
- **EF-33 (DEVRAIT)** — Les échanges inter-agents suivent un **protocole standardisé** (de type **A2A**) décrivant les capacités, les tâches et les messages, pour l'interopérabilité.
- **EF-34 (DOIT)** — Tous les messages inter-agents sont **tracés** et visibles dans la supervision ; des **garde-fous** limitent les boucles d'échange et les coûts.

### 4.6 Interface de supervision (Control Tower)

- **EF-17 (DOIT)** — **Monitorer** en temps réel les agents (statut, tâche en cours) et les tâches (tableau Kanban).
- **EF-18 (DOIT)** — **Personnaliser** un agent : nom, rôle, prompt système, outils, *playbook*, modèle.
- **EF-19 (DOIT)** — **Interagir** : ouvrir une conversation avec un agent.
- **EF-20 (DOIT)** — **Assigner / réassigner** une tâche à un agent.
- **EF-21 (DEVRAIT)** — **Contrôler la capacité** : activer/désactiver un agent, ajuster le nombre d'instances.
- **EF-22 (DEVRAIT)** — Visualiser pour chaque exécution la **trace** détaillée (étapes, outils, coût, durée).
- **EF-23 (POURRAIT)** — Tableau de bord analytique (coûts, débit, taux de réussite).

### 4.7 Workflows / playbooks évolutifs

- **EF-24 (DOIT)** — Chaque agent suit un *playbook* (étapes/instructions) modifiable.
- **EF-25 (DOIT)** — Les *playbooks* sont **versionnés** (historique, retour arrière).
- **EF-26 (DEVRAIT)** — Une modification s'applique sans redéploiement du système.
- **EF-27 (POURRAIT)** — L'agent **propose** des améliorations de son *playbook* à partir de ses échecs passés (auto-réflexion).

### 4.8 Intégrations

- **EF-28 (DOIT)** — Connexion à un dépôt de code (forge Git — GitLab pour ce projet) et à un système de fichiers de travail.
- **EF-29 (DEVRAIT)** — Intégrations via **MCP** (Model Context Protocol) : Git, CI/CD, base de données, Slack, etc.
- **EF-30 (POURRAIT)** — Intégration d'outils de design (ex. Figma) et de gestion (Linear/Jira).

### 4.9 Projet local et espace de travail *(proposé — [docs/24 §2](./24-projets-locaux-et-poste-de-travail.md))*

> Ces exigences sont **proposées**, pas encore arrêtées : elles dépendent des décisions D1 et D2
> de [docs/24 §8](./24-projets-locaux-et-poste-de-travail.md). Elles comblent un trou constaté :
> EF-28 n'est aujourd'hui satisfaite que par le dépôt de Maestro lui-même, et l'espace de travail
> d'une tâche est un répertoire temporaire **détruit en fin d'exécution**.

- **EF-35 (DOIT)** — Un **projet** désigne une **racine sur le disque de l'utilisateur** (dossier neuf à créer, ou dépôt existant), avec son périmètre d'inclusion/exclusion. Tâches, exécutions et coûts s'y rattachent.
- **EF-36 (DOIT)** — Les agents **ne travaillent jamais directement** dans la racine du projet : chaque tâche opère dans un espace dérivé (branche/worktree Git si le projet est versionné, copie sinon).
- **EF-37 (DOIT)** — L'**application des modifications** dans le projet de l'utilisateur est une **action sensible** : elle passe par la validation humaine (EF-08), diff à l'appui.
- **EF-38 (DOIT)** — Le système **refuse** une racine hors périmètre autorisé (racine de disque, dossier utilisateur nu, chemins sensibles) et empêche toute écriture au-dessus de la racine déclarée.

### 4.10 Composition de l'objectif : sources et brief *(proposé — [docs/24 §3](./24-projets-locaux-et-poste-de-travail.md))*

- **EF-39 (DEVRAIT)** — Un objectif peut porter des **sources** : fichiers téléversés (`.md`, `.txt`, `.docx`, `.pdf`), dossier de références en lecture seule, URL. Elles sont ramenées à un format texte unique avant d'entrer dans le contexte.
- **EF-40 (DEVRAIT)** — Avant toute décomposition, l'orchestrateur produit un **brief structuré** (objectif, périmètre, hors-périmètre, contraintes, critères d'acceptation, hypothèses), **peut poser des questions de clarification**, et le soumet à validation humaine.

### 4.11 Distribution et installation *(proposé — [docs/24 §4](./24-projets-locaux-et-poste-de-travail.md))*

- **EF-41 (DEVRAIT)** — Le produit s'installe et se lance **sans chaîne de développement** (ni clone Git, ni gestion manuelle d'environnement) ; le premier lancement guide le choix du fournisseur, des accès et du premier projet.
- **EF-42 (POURRAIT)** — Une **enveloppe de bureau** embarque la Control Tower et le backend local. Elle **n'est pas une variante du produit** : le mode web/serveur multi-utilisateurs reste de premier ordre, les deux partagent front et backend (décisions D3/D4).

---

## 5. Exigences non fonctionnelles

| Réf | Catégorie | Exigence |
|-----|-----------|----------|
| ENF-01 | **Performance** | Mises à jour de l'UI en temps quasi réel (< 2 s de latence perçue). |
| ENF-02 | **Scalabilité** | Architecture permettant de passer de quelques agents à plusieurs dizaines via files de tâches et workers. |
| ENF-03 | **Sécurité** | Exécution du code des agents en **bac à sable** (conteneurs isolés) ; permissions scopées par agent. |
| ENF-04 | **Sûreté / contrôle** | Garde-fous : validation humaine, liste d'actions interdites, plafond de dépense par tâche/jour. |
| ENF-05 | **Observabilité** | Traçage complet (entrées, sorties, outils, tokens, coût) de chaque exécution. |
| ENF-06 | **Fiabilité** | Reprise sur erreur : une tâche échouée est relancée ou re-routée ; pas de perte de travail (workflows durables). |
| ENF-07 | **Coût** | Suivi et limitation des dépenses ; choix du **fournisseur et du modèle** par agent (modèle léger — voire local — pour les tâches simples). |
| ENF-08 | **Modularité** | Ajout/remplacement d'un agent, d'un outil ou d'un **fournisseur de modèle** sans refonte. |
| ENF-09 | **Confidentialité** | Données et secrets chiffrés ; possibilité d'auto-hébergement de l'observabilité. |
| ENF-10 | **Expérience** | Interface **multilingue** (français par défaut, autres langues activables via internationalisation / i18n), claire et compréhensible par un profil non technique. |
| ENF-11 | **Agnosticisme modèle** | Le moteur d'agents est isolé derrière une **couche d'abstraction fournisseur** : la config d'un agent porte `fournisseur + modèle + credentials`. Aucun couplage dur à un fournisseur unique. |
| ENF-12 | **Installabilité** *(proposé, [docs/24 §4](./24-projets-locaux-et-poste-de-travail.md))* | Le mode de distribution (local/bureau *vs* serveur) ne change que des **réglages** — persistance, authentification, isolation — jamais le code applicatif : un seul front, un seul backend. |
| ENF-13 | **Intégrité du projet de l'utilisateur** *(proposé, [docs/24 §2.5](./24-projets-locaux-et-poste-de-travail.md))* | Aucun travail d'agent n'atteint le projet sans passer par une validation humaine ; un projet versionné garde son retour arrière natif. Le contenu lu dans un projet est une **donnée**, jamais une consigne. |

---

## 6. Contraintes et hypothèses

- **Modèles d'IA :** l'architecture est **agnostique au fournisseur** — chaque agent choisit son fournisseur + modèle derrière une **couche d'abstraction** (« model gateway »). Le **POC démarre sur la famille Claude** (via le **Claude Agent SDK** : orchestration, sous-agents, MCP natifs), qui reste le runtime des agents Claude ; les autres fournisseurs (OpenAI, Google, modèles ouverts/locaux) s'ajoutent par configuration, **sans refonte**.
- **Coût des modèles :** facturation à l'usage (tokens) — le suivi des coûts est une exigence de premier ordre.
- **Maturité de l'écosystème :** l'orchestration multi-agents évolue vite ; privilégier des **patterns simples et composables** plutôt que des frameworks lourds, et concevoir pour le changement.
- **Supervision humaine :** indispensable pour les actions à fort impact (le but est l'assistance augmentée, pas l'absence totale de contrôle).
- **Sécurité d'exécution :** les agents écrivent et exécutent du code → bac à sable obligatoire.

---

## 7. Risques et mitigations

| Risque | Impact | Mitigation |
|--------|--------|-----------|
| Agents qui dupliquent le travail ou laissent des trous | Moyen | Tâches très précisément décrites par l'orchestrateur (objectif, format de sortie, périmètre). |
| Coûts qui dérapent | Élevé | Plafonds par tâche/jour, modèle léger par défaut, alertes de budget. |
| Action destructrice d'un agent | Élevé | Bac à sable, permissions scopées, validation humaine, liste d'actions interdites. |
| Sur-ingénierie initiale | Moyen | Commencer par le pattern orchestrateur-workers natif ; complexifier seulement si mesurablement utile. |
| Collisions sur ressources partagées | Moyen | Verrous/locks sur ressources, branches Git par tâche, file de tâches. |
| Dépendance forte à un fournisseur | Élevé | **Couche d'abstraction modèle de premier ordre** (choix fournisseur + modèle par agent, ENF-11/O7) ; outils standard (MCP). |
| **Perte de travail dans le projet de l'utilisateur** *(proposé)* | Élevé | Travail hors de la racine (branche/copie), application sous validation humaine (EF-36/EF-37), périmètre déclaré et racines interdites (EF-38). |
| **Prompt injection par le contenu lu** (code du projet, document téléversé) *(proposé)* | Moyen | Contenu traité comme donnée et non comme consigne ; actions sensibles maintenues derrière la validation ; politique d'outils par agent. |
| **Empaquetage d'une cible mouvante** *(proposé)* | Moyen | Le bureau vient **après** le projet local et l'ingestion ; d'abord un lanceur/installeur, l'enveloppe native ensuite ([docs/24 §4.8](./24-projets-locaux-et-poste-de-travail.md)). |

---

## 8. Critères d'acceptation du MVP

Le MVP est considéré comme réussi si, sur un projet de démonstration :

1. Un objectif saisi en langage naturel génère automatiquement un ensemble de tickets cohérents.
2. Au moins **3 agents différents** exécutent des tâches, dont **2 en parallèle**.
3. Le routage automatique assigne correctement au moins **9 tickets sur 10** d'un jeu de test.
4. Le tableau de bord affiche en temps réel l'état des agents et des tâches.
5. Une action sensible déclenche une demande de validation traitée depuis l'UI.
6. Le coût total de l'exécution est visible et traçable par tâche.
7. Au moins **un échange inter-agent** (handoff ou notification débloquant une tâche) est observable durant l'exécution.

---

## 9. Suite

Les choix techniques détaillés, le modèle de données, les spécifications de chaque agent et de l'interface, ainsi que la roadmap, sont décrits dans les documents suivants du dossier `docs/`.
