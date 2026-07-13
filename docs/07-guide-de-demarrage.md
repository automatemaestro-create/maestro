# Guide de démarrage — Maestro

**Version :** 0.1
Objectif : lancer un **premier prototype** concret (Phase 0). Pensé pour être suivi par une développeuse / un tech lead, et compréhensible par un chef de projet.

---

## 1. Prérequis

| Élément | Pour quoi | Note |
|---------|-----------|------|
| Un **accès modèle Claude** | Faire fonctionner les agents Claude | **Deux modes au choix** (voir §2.1) : abonnement Claude Code (défaut du POC, sans clé) **ou** clé API Anthropic |
| **Python 3.11+** *ou* **Node.js 20+** | Selon l'option de langage choisie | Voir [doc 02 §1](./02-stack-technique.md) |
| **Docker** | Bac à sable d'exécution + bases locales | Docker Desktop suffit pour démarrer |
| **Git + un compte GitLab** | Versionnement, intégration code | Le dépôt du projet et sa CI sont hébergés sur GitLab |
| Le **Claude Agent SDK** | Moteur d'agents | Paquet Python ou TypeScript |

> ⚠️ Vérifier la documentation officielle d'Anthropic pour les noms de paquets et commandes exacts (l'écosystème évolue vite).

---

## 2. Étapes du POC (Phase 0)

### Étape 1 — Préparer l'environnement
1. Créer le dépôt Git du projet.
2. Installer le Claude Agent SDK (Python ou TypeScript).
3. Choisir un **mode d'authentification** (voir §2.1) et le configurer via des **variables d'environnement** (jamais en dur dans le code) : copier `.env.example` vers `.env` et renseigner selon le mode.
4. Lancer une base locale via Docker (PostgreSQL + Redis) — optionnel au tout début.

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
├── core/               # Placeholders router/queue/playbooks/sandbox (README de renvoi)
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
4. **Plafonner.** Mettre tout de suite un plafond de dépense (budget de l'exécution, adossé à la comptabilité par tâche — #56) et un time-out par tâche.
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
