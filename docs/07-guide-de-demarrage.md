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
| **Git + un compte GitHub** | Versionnement, intégration code | — |
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
- Brancher **Langfuse** plus tard pour des traces visuelles.

**Résultat attendu :** taper un objectif → obtenir des tâches → voir 2 agents produire un résultat exploitable.

---

## 3. Squelette de projet conseillé

```
maestro/
├── apps/
│   ├── api/            # Backend FastAPI (ou NestJS)
│   └── web/            # Control Tower (Next.js)
├── agents/
│   ├── orchestrator/   # Agent Chef de projet
│   ├── developer/
│   ├── database/
│   ├── devops/
│   ├── designer/
│   └── qa/
├── core/
│   ├── router/         # Auto-assignation
│   ├── queue/          # File de tâches & workers
│   ├── playbooks/      # Playbooks versionnés (Markdown)
│   └── sandbox/        # Isolation d'exécution
├── packages/
│   └── shared/         # Types & schémas partagés
├── infra/
│   ├── docker-compose.yml
│   └── migrations/
└── docs/               # Cette documentation
```

---

## 4. Bonnes pratiques dès le départ

1. **Déléguer précisément.** Toujours fournir à un agent : objectif, format de sortie, outils à utiliser, limites. C'est ce qui évite doublons et oublis.
2. **Commencer simple.** Pas de framework lourd avant d'en avoir besoin ; l'Agent SDK natif suffit pour le POC.
3. **Isoler.** Une branche Git par tâche, un conteneur par exécution.
4. **Plafonner.** Mettre tout de suite un plafond de dépense et un time-out par tâche.
5. **Tracer.** Logger coûts et étapes dès le premier jour.
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
