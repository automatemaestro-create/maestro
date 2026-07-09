# 🎼 Maestro — Plateforme d'orchestration d'agents IA autonomes

> Une « entreprise logicielle » virtuelle : une équipe d'agents IA spécialisés (chef de projet, devops, base de données, designer, développeur, QA…) qui se répartissent le travail automatiquement, travaillent en parallèle et de façon autonome, et que vous pilotez depuis une console de contrôle (la *Control Tower*).

Propulsé par le **Claude Agent SDK** d'Anthropic.

---

## En une phrase

Maestro transforme un objectif (« construis-moi cette fonctionnalité ») en un ensemble de tickets, les **assigne automatiquement** au bon agent spécialisé, fait **travailler plusieurs agents en parallèle**, et vous donne une **interface web** pour tout superviser, configurer et reprendre la main quand c'est nécessaire.

## Pourquoi « Maestro » ?

Un chef d'orchestre (*maestro*) ne joue d'aucun instrument pendant le concert : il coordonne des spécialistes pour produire une œuvre cohérente. C'est exactement le rôle de l'agent orchestrateur au cœur de la plateforme.

---

## 📚 Documentation

La documentation complète se trouve dans le dossier [`docs/`](./docs). Ordre de lecture conseillé :

| # | Document | Pour quoi faire |
|---|----------|-----------------|
| 00 | [Cahier des charges](./docs/00-cahier-des-charges.md) | La vision, les objectifs, ce que le produit doit faire (et ne pas faire) |
| 01 | [Architecture technique](./docs/01-architecture-technique.md) | Comment c'est construit : composants, flux, schémas |
| 02 | [Stack technique & outils](./docs/02-stack-technique.md) | Les outils recommandés, pourquoi, et les alternatives |
| 03 | [Modèle de données](./docs/03-modele-de-donnees.md) | Les entités (agents, tâches, runs…) et leurs relations |
| 04 | [Spécifications des agents](./docs/04-specifications-agents.md) | Le rôle, les outils et le « playbook » de chaque agent |
| 05 | [Interface — Control Tower](./docs/05-interface-control-tower.md) | Les écrans, fonctionnalités et parcours utilisateur |
| 06 | [Roadmap](./docs/06-roadmap.md) | Le plan de route, du POC à la V2 |
| 07 | [Guide de démarrage](./docs/07-guide-de-demarrage.md) | Comment lancer un premier prototype concrètement |
| 08 | [Glossaire](./docs/08-glossaire.md) | Le vocabulaire du projet |
| 09 | [Exemple concret & coûts](./docs/09-exemple-chiffre.md) | Un projet déroulé : nombre d'agents, durée, budget (abonnement 20 $ vs API) |
| 10 | [Workflow Git & tickets](./docs/10-workflow-git.md) | Convention de branches/commits, cycle de vie d'un ticket, commandes `/ticket-start`, `/ticket-finish`, `/branch-cleanup` |
| 11 | [Démo de bout en bout du POC](./docs/11-demo-poc.md) | Lancer `maestro-demo`, le parcours objectif → tâches → agents → fichiers, et la validation du critère de sortie Phase 0 |

Les versions **Word (.docx)** prêtes à partager sont dans `deliverables/` — ce dossier est **hors dépôt** (versionné sur le Drive de l'équipe), pas dans Git.

---

## 🧩 Les agents (par défaut)

| Agent | Rôle | Exemples de tâches |
|-------|------|--------------------|
| 🧭 **Chef de projet** | Décompose les objectifs en tickets, priorise, assigne | « Découper l'epic en 6 tickets, définir les dépendances » |
| 💻 **Développeur** | Écrit et modifie le code | « Implémenter l'endpoint `/login` » |
| 🗄️ **Base de données** | Schéma, migrations, requêtes | « Ajouter la table `sessions` + migration » |
| ⚙️ **DevOps** | CI/CD, infra, déploiement | « Configurer le pipeline GitLab CI » |
| 🎨 **Designer** | UI/UX, maquettes, design system | « Proposer l'écran de connexion » |
| 🧪 **QA / Testeur** | Tests, validation, revue | « Écrire les tests e2e du parcours d'inscription » |

> Ces agents sont **entièrement configurables** depuis l'interface, et vous pouvez en **créer de nouveaux**.

---

## ⭐ Principes directeurs

1. **Commencer simple.** On démarre avec le pattern *orchestrateur-workers* natif du Claude Agent SDK, sans sur-ingénierie. On ajoute de la complexité seulement quand elle apporte une valeur mesurable. *(Recommandation explicite d'Anthropic.)*
2. **Conception modulaire.** Chaque agent est une brique indépendante et remplaçable.
3. **Autonomie sous supervision.** Les agents sont autonomes, mais les actions sensibles (déploiement, changement de schéma, dépense) passent par une **validation humaine**.
4. **Tout est observable.** Chaque action d'agent est tracée, chiffrée en coût, et rejouable.

---

## 🛠️ Développement (Phase 0)

Stack **Python 3.11+** (option A du [doc stack](./docs/02-stack-technique.md)), moteur d'agents = **Claude Agent SDK**.

**Prérequis :** Python 3.11+, Node.js 20+ (requis par le Claude Agent SDK), Docker (bases locales optionnelles), et un **accès modèle Claude** — au choix un **abonnement Claude Code** (défaut du POC, sans clé) ou une **clé API Anthropic** (voir ci-dessous).

```bash
# 1. Environnement virtuel + installation (éditable, avec outils dev)
python -m venv .venv
source .venv/bin/activate            # Windows : .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 2. Secrets : copier le gabarit et choisir un mode d'auth (le .env n'est jamais commité)
cp .env.example .env                 # Windows : Copy-Item .env.example .env
#   Deux modes d'authentification Claude, sélectionnables via CLAUDE_AUTH_MODE :
#     • subscription (défaut) : abonnement Claude Code, SANS clé — se connecter via `claude`
#       (ou, en CI, poser CLAUDE_CODE_OAUTH_TOKEN via `claude setup-token`).
#     • api_key : renseigner ANTHROPIC_API_KEY (console Anthropic).
#   Précédence : CLAUDE_AUTH_MODE fait foi ; sinon clé présente ⇒ api_key, sinon subscription.
#   Détails : docs/07-guide-de-demarrage.md §2.1.

# 3. Vérifier que tout est prêt (SDK importable + mode d'auth configuré)
maestro-check-env

# 4. (optionnel) Bases locales PostgreSQL + Redis — voir infra/README.md
docker compose -f infra/docker-compose.yml up -d
```

**Essayer l'orchestrateur** (Chef de projet — objectif → tâches JSON) :

```bash
maestro-orchestrate "Créer une petite API REST de gestion de tâches avec sa base"
```

Il découpe l'objectif en 3 à 5 tâches structurées (titre, description, compétences
requises, format de sortie, dépendances), validées contre le schéma partagé
[`packages/shared/schemas/task.schema.json`](./packages/shared/README.md).

**Dérouler la boucle d'orchestration complète** (objectif → tâches → assignation
automatique aux bons agents → exécution → synthèse agrégée) :

```bash
maestro-run "Créer une petite API REST de gestion de tâches avec sa base"
```

Le moteur planifie, **assigne chaque tâche à l'agent le plus compétent** (règles de
compétences), exécute les tâches dans l'ordre de leurs dépendances en transmettant
les résultats intermédiaires, puis imprime la **synthèse** (`--json` pour le rapport
structuré). Aperçu de l'implémentation : `maestro/agents/` (catalogue + compétences),
`maestro/router/` (assignation), `maestro/engine/` (boucle + agrégation).

Qualité : `ruff check .` (lint, lancé aussi en CI) · `pytest` (tests) · `mypy maestro` (types).

---

## 🚦 Statut

Projet en phase de **cadrage**. Voir la [roadmap](./docs/06-roadmap.md) pour les prochaines étapes.

---

*Document généré comme base de démarrage. Tous les choix techniques sont des recommandations argumentées, à valider lors du POC.*
