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

**Vous venez contribuer ?** Commencez par [`CONTRIBUTING.md`](./CONTRIBUTING.md) : du clone à la
première Merge Request en une page (mise en route, prendre un ticket, conventions, `/ticket-ship`,
qui relit et qui merge).

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
| 12 | [Démo de bout en bout du MVP](./docs/12-demo-mvp.md) | Rejouer la démo supervisée (Control Tower, validation humaine, coûts), la vérification des 7 critères du MVP et le verdict go/no-go de fin de Phase 1 |
| 13 | [Démo V1 : un projet réel de bout en bout](./docs/13-demo-v1.md) | Le projet « Dépensio » mené par l'équipe complète (validation UI, chat, capacité, analytics), le rapport de coûts des 6 runs et le verdict go/no-go de fin de Phase 2 |
| 23 | [Démo V2 : fiabilité et durabilité](./docs/23-demo-v2.md) | « Dépensio » rejoué en 6/6 avec relance automatique, une reprise de run durable chiffrée (0 $ de re-paiement de l'amont) et le verdict go/no-go de fin de Phase 3 (LangGraph vs Agent SDK) |
| 24 | [Projets locaux & poste de travail](./docs/24-projets-locaux-et-poste-de-travail.md) | **Cadrage du cap d'après** : travailler dans un vrai projet du disque, composer un objectif à partir de documents, et la question « application de bureau ou web ? » — options, recommandations et les 7 décisions en attente |

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

### Mise en route : une commande

Sur un clone frais — Windows (Git Bash), macOS ou Linux :

```bash
bash scripts/setup.sh
```

[`scripts/setup.sh`](./scripts/setup.sh) est la **source unique** du parcours de mise en route. Il
installe les **prérequis** manquants (Python 3.11+, Node.js 20+, git, [`glab`](https://gitlab.com/gitlab-org/cli) —
via winget / brew / apt), crée le **`.venv`** et y installe le paquet en éditable (`pip install -e ".[dev]"`),
copie **`.env.example` vers `.env`**, active le **hook git** `commit-msg`, installe les dépendances
**npm de `apps/web`**, complète **`.claude/settings.local.json`** (profil navigateur + serveurs MCP du
dépôt) et monte le **runner CI de cette machine** (Docker + [`setup-runner.sh`](./scripts/gitlab/setup-runner.sh)).
Ce runner-là est le **secours** : la CI de l'équipe est servie par un **runner partagé** monté une
fois sur une machine qui reste allumée (`setup-runner.sh --partage`) — sans aucun runner en ligne,
les pipelines de MR restent `pending` ([docs/10 §8.1](./docs/10-workflow-git.md)).

Il est **idempotent** (relancé sur une machine prête, tout ressort en `DÉJÀ FAIT`) et **non
destructif** : un `.env` existant n'est **jamais** écrasé, et `settings.local.json` est **fusionné
clé par clé**. Il ne pose **aucune question** — ce qui exige un humain (authentifications, secrets à
renseigner) sort dans la section « Reste à faire » de son rapport final.

| Variante | Effet |
|---|---|
| `bash scripts/setup.sh --check` | Diagnostic seul : dit ce qui manque, **n'écrit rien** |
| `bash scripts/setup.sh --no-install` | N'installe aucun outil, se contente de le signaler |
| `bash scripts/setup.sh --only <étapes>` · `--skip <étapes>` | Rejoue / saute des étapes : `prerequis`, `venv`, `env`, `hooks`, `web`, `mcp`, `runner`, `infra`, `verif` |
| `bash scripts/setup.sh --with-infra` | Démarre en plus les bases locales PostgreSQL / Redis / Temporal (`infra/`) |

Dans une session Claude Code, la commande [`/setup`](./.claude/commands/setup.md) lance ce même
script et **prend en charge le « Reste à faire »** : authentification Figma, accompagnement du
remplissage du `.env`, diagnostic d'une étape en échec.

**Le geste qui reste : renseigner le `.env`** (jamais commité). Deux modes d'authentification
Claude, sélectionnables via `CLAUDE_AUTH_MODE` :

- **`subscription`** (défaut du POC, **sans clé**) — se connecter une fois via `claude` ; en CI,
  poser `CLAUDE_CODE_OAUTH_TOKEN` obtenu par `claude setup-token` ;
- **`api_key`** — renseigner `ANTHROPIC_API_KEY` (console Anthropic).

Précédence : `CLAUDE_AUTH_MODE` fait foi ; sinon clé présente ⇒ `api_key`, sinon `subscription`.
Détails : [docs/07 §2.1](./docs/07-guide-de-demarrage.md). Puis vérifier : `maestro-check-env`.

<details>
<summary>Repli : les mêmes étapes à la main</summary>

Utile pour comprendre ce que fait le script, ou sur une machine où il ne peut pas aboutir
(pas de gestionnaire de paquets, élévation refusée). Le chemin nominal reste `scripts/setup.sh`.

```bash
# 1. Environnement virtuel + installation (éditable, avec outils dev)
python -m venv .venv
source .venv/bin/activate            # Windows : .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 2. Secrets : copier le gabarit et choisir un mode d'auth (le .env n'est jamais commité)
cp .env.example .env                 # Windows : Copy-Item .env.example .env

# 3. Vérifier que tout est prêt (SDK importable + mode d'auth configuré)
maestro-check-env

# 4. Hook git de convention de commit (une fois par clone)
bash scripts/git/install-hooks.sh

# 5. Dépendances de l'UI Control Tower
cd apps/web && npm ci && cd ../..

# 6. Runner CI de cette machine (Docker requis) — secours quand le runner partagé est éteint
bash scripts/gitlab/setup-runner.sh

# 6 bis. À FAIRE UNE FOIS POUR L'ÉQUIPE, sur une machine qui reste allumée : le runner partagé,
#        celui qui permet de merger quand tous les postes sont éteints (docs/10 §8.1)
bash scripts/gitlab/setup-runner.sh --partage

# 7. (optionnel) Bases locales PostgreSQL + Redis + Temporal — voir infra/README.md
docker compose -f infra/docker-compose.yml up -d
```

</details>

### Mise en route côté Claude Code

Le dépôt embarque sa propre configuration de l'outil : un clone récupère
automatiquement les commandes `/ticket-*`, les skills, les permissions et les
serveurs MCP, sans rien réinstaller.

| Ce que le clone reprend | Où c'est versionné |
|---|---|
| Commandes `/ticket-create`, `/ticket-start`, `/ticket-ship`, `/backlog`, `/mr-review`, `/pipeline-fix`, `/branch-cleanup`, `/milestone-presentation` | [`.claude/commands/`](./.claude/commands/) |
| Skills `control-tower` et `verify` | [`.claude/skills/`](./.claude/skills/) |
| Permissions (allow / ask / deny) et hook de traçabilité des demandes | [`.claude/settings.json`](./.claude/settings.json), [`.claude/hooks/`](./.claude/hooks/) |
| Serveurs MCP `chrome-maestro` (navigateur) et `figma-officiel` | [`.mcp.json`](./.mcp.json) |

Ce qui touche à des **chemins locaux** est posé par `scripts/setup.sh` (étape `mcp`), qui
complète `.claude/settings.local.json` — non versionné — sans écraser ce qui s'y trouve déjà :

1. **Approbation des serveurs MCP du dépôt.** `enabledMcpjsonServers` **est** le registre
   d'approbation de Claude Code : le script y inscrit les serveurs déclarés dans `.mcp.json`,
   il n'y a donc plus de « Pending approval » à lever à la main. Le corollaire vaut d'être
   dit : un `.mcp.json` est du **code exécutable**, il se relit avant d'être approuvé — la
   revue se fait donc à la MR, pas au premier lancement.
2. **Profil du navigateur.** `chrome-maestro` pilote Chrome via `@playwright/mcp` ; le script
   pose `MAESTRO_CHROME_PROFILE` sur `~/.maestro/chrome-profile` s'il n'est pas déjà défini.
   Pour réutiliser des sessions déjà ouvertes, pointez un profil **dédié** — jamais le profil
   Chrome principal, dont le pilotage est refusé depuis Chrome 136 — via le `.env`, qui **fait
   foi** (le script recopie la valeur à chaque passage, ce qui fait qu'une rotation se propage) :

   ```dotenv
   MAESTRO_CHROME_PROFILE=C:\Users\<vous>\.maestro\chrome-profile
   ```

   Ce profil n'accepte **qu'un consommateur à la fois** (verrou ProcessSingleton
   de Chrome) : la fenêtre pilotée par le MCP se ferme dès que la séquence est
   terminée, sans quoi elle bloque tout autre outil visant le même dossier.

Un seul geste reste **manuel, par personne**, parce qu'il exige une authentification
individuelle : **s'authentifier auprès de Figma.** `figma-officiel` est un serveur HTTP en
OAuth — chaque personne s'y connecte avec son propre compte, via `/mcp` dans une session
interactive, un clic mis en cache ensuite. Rien à committer.

> Deux couches de MCP coexistent dans ce dépôt, à ne pas confondre :
> [`.mcp.json`](./.mcp.json) équipe **Claude Code**, l'outil avec lequel on
> développe ; [`core/mcp/<agent>.json`](./core/mcp/README.md) équipe les **agents
> Maestro**, le produit. Voir [docs/21](./docs/21-configuration-mcp.md).

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
