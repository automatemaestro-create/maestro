# Spécifications des agents — Maestro

**Version :** 0.1
Ce document décrit chaque agent par défaut : son **rôle**, ses **compétences** (pour l'auto-assignation), ses **outils**, et un exemple de **playbook**.

---

## 1. Qu'est-ce qu'un *playbook* ?

Un **playbook** est le **workflow d'un agent** : la liste d'étapes/instructions qu'il suit pour accomplir ses tâches. C'est un document structuré (Markdown), **versionné** et **modifiable depuis l'UI sans redéploiement** (exigences EF-24 à EF-26).

Structure type d'un playbook :

```markdown
# Playbook — <Nom de l'agent>
## Mission
<ce que l'agent doit accomplir, en une phrase>
## Entrées attendues
<ce que la tâche doit fournir>
## Étapes
1. ...
2. ...
## Critères de "terminé" (Definition of Done)
- ...
## Garde-fous
- Actions nécessitant une validation humaine : ...
- Actions interdites : ...
## Format de sortie
<structure du livrable remis>
```

> **Bonne délégation = bons résultats.** Chaque tâche transmise à un agent doit préciser : objectif, format de sortie, outils/sources à utiliser, limites. C'est la clé pour éviter doublons et oublis.

---

## 2. Catalogue des agents par défaut

| Agent | Rôle | Compétences (tags) | Modèle conseillé (défaut POC — Claude) |
|-------|------|--------------------|------------------|
| 🧭 Chef de projet | Orchestration, découpage, priorisation | `planning`, `routing`, `synthesis` | Opus |
| 💻 Développeur | Code applicatif | `backend`, `frontend`, `api`, `refactor` | Sonnet |
| 🗄️ Base de données | Schéma, migrations, requêtes | `sql`, `schema`, `migration`, `data` | Sonnet |
| ⚙️ DevOps | CI/CD, infra, déploiement | `ci-cd`, `infra`, `deploy`, `docker` | Sonnet |
| 🎨 Designer | UI/UX, maquettes, design system | `ui`, `ux`, `design-system`, `figma` | Sonnet |
| 🧪 QA / Testeur | Tests, validation, revue | `tests`, `e2e`, `review`, `qa` | Sonnet (ou Haiku pour checks simples) |

> **Le fournisseur est configurable par agent** (voir §4 et [stack §2](./02-stack-technique.md)). Les modèles ci-dessus sont le **défaut Claude du POC** ; on peut affecter à chaque agent un autre fournisseur/modèle (OpenAI, Google, ouvert/local) **sans changer son rôle ni son playbook** — c'est l'objet de la couche d'abstraction.

> Le **routage** (doc 01 §3.2) s'appuie sur ces tags + un classifieur léger pour les cas ambigus.

---

## 3. Fiches détaillées

### 3.1 🧭 Chef de projet (orchestrateur)

- **Mission :** transformer un objectif en tickets bien définis, établir les dépendances, suivre l'avancement, synthétiser les résultats.
- **Outils :** lecture du dépôt et de la doc, création/édition de tâches, accès à l'état des autres agents.
- **Particularité :** c'est lui qui **délègue** ; il ne code pas. Il produit des tâches avec objectif + format de sortie + limites.

**Exemple de playbook :**

```markdown
# Playbook — Chef de projet
## Mission
Découper un objectif en tickets exécutables et coordonner leur réalisation.
## Étapes
1. Reformuler l'objectif et lister les livrables attendus.
2. Identifier les domaines concernés (bdd, backend, ui, infra, tests).
3. Créer un ticket par livrable avec : titre, description, format de sortie,
   compétences requises, critères de "terminé".
4. Établir les dépendances entre tickets.
5. Déclencher l'assignation automatique.
6. Suivre l'avancement ; relancer ou re-router les tâches en échec.
7. À la fin, synthétiser les résultats en un récapitulatif pour l'utilisateur.
## Critères de "terminé"
- Tous les tickets ont un agent et un format de sortie clairs.
- Les dépendances sont cohérentes (pas de cycle).
## Garde-fous
- Ne jamais lancer plus de N tickets en parallèle sans accord (plafond configurable).
## Format de sortie
Liste de tickets + graphe de dépendances + résumé.
```

### 3.2 💻 Développeur

- **Mission :** implémenter et modifier le code.
- **Outils :** système de fichiers (branche Git dédiée), exécution de code/tests, Git/GitLab (commits, MR).
- **Garde-fous :** travaille sur une branche ; ouvre une MR ; ne fusionne pas sans validation/QA.

```markdown
# Playbook — Développeur
## Étapes
1. Créer une branche `task/<id>`.
2. Lire le contexte (ticket, fichiers concernés, conventions du repo).
3. Implémenter la modification par petits incréments.
4. Lancer les tests locaux ; corriger jusqu'au vert.
5. Committer avec un message clair ; ouvrir une Merge Request.
## Critères de "terminé"
- Le code compile, les tests passent, la MR est ouverte et décrite.
## Garde-fous
- Validation humaine : fusion en branche principale, suppression de fichiers massifs.
## Format de sortie
Lien de MR + résumé des changements + résultats de tests.
```

### 3.3 🗄️ Base de données

- **Mission :** concevoir le schéma, écrire les migrations, optimiser les requêtes.
- **Outils :** MCP base de données (environnement de dev/staging), génération de migrations.
- **Garde-fous :** **toute migration destructive** (drop, alter de colonne avec perte) requiert une validation humaine ; jamais directement en production.

### 3.4 ⚙️ DevOps

- **Mission :** pipelines CI/CD, infrastructure, déploiements.
- **Outils :** GitLab CI, Docker, MCP cloud/infra.
- **Garde-fous :** **tout déploiement** (surtout en production) passe par une validation humaine ; respect des plafonds de ressources.

### 3.5 🎨 Designer

- **Mission :** proposer des écrans, maquettes et composants conformes à une charte.
- **Outils :** MCP Figma, génération de specs UI, design tokens.
- **Garde-fous :** respecte le design system existant ; propose, ne remplace pas la charte sans accord.

### 3.6 🧪 QA / Testeur

- **Mission :** écrire et exécuter les tests, valider les livrables, faire la revue.
- **Outils :** frameworks de test, exécution e2e, lecture de PR.
- **Particularité :** peut **bloquer** une tâche jugée non conforme et la renvoyer au Développeur.

---

## 4. Créer un agent personnalisé

Depuis la Control Tower, l'utilisateur peut créer un agent en définissant :

1. **Nom & rôle** (ex. « Rédacteur technique »).
2. **Prompt système** (identité, ton, contraintes).
3. **Compétences/tags** (pour le routage).
4. **Outils** à lui lier (avec permissions scopées).
5. **Fournisseur + modèle** (selon complexité, coût, souveraineté) — Claude, OpenAI, Google, modèle ouvert/local… via la **couche d'abstraction** ; par défaut, le Claude du POC.
6. **Playbook** initial (workflow), ensuite versionné.

---

## 5. Coordination et communication entre agents

Les agents ne se contentent pas de travailler en parallèle : ils **communiquent**. Trois canaux complémentaires (détaillés dans [l'architecture §4](./01-architecture-technique.md)) :

- **Tableau noir partagé (canal principal) :** la liste de tâches et l'espace de travail (fichiers, dépôt Git) constituent un état partagé que tous les agents lisent et écrivent ; les tâches dépendantes se débloquent automatiquement (EF-31).
- **Messagerie directe (point à point) :** via une boîte aux lettres + un bus pub/sub, un agent envoie un message ciblé à un autre — sans passer par l'orchestrateur (EF-32).
- **Protocole A2A :** les échanges sont structurés selon un standard inter-agents (Agent Card, Task, Message), complémentaire de MCP (EF-33). **MCP relie les agents aux outils ; A2A relie les agents entre eux.**

Modes de coordination concrets :

- **Délégation descendante :** le Chef de projet crée les tâches et fixe les dépendances.
- **Handoff latéral :** un agent passe le relais à un autre (ex. le Développeur demande une migration à l'agent BDD), puis continue ou attend la réponse — EF-07/EF-32.
- **Requête–réponse :** un agent interroge un pair (ex. Dev → Designer pour une spec d'écran).
- **Notification / diffusion :** un agent publie un événement (« schéma prêt ») que les abonnés consomment.
- **Remontée :** chaque résultat revient à l'orchestrateur, qui synthétise et arbitre les conflits.

Garde-fous : chaque message est **tracé** (EF-34), des **plafonds de tours** évitent les boucles infinies, et l'on privilégie l'**état partagé** + des messages **ciblés** pour maîtriser les coûts. L'**isolation** des contextes (EF-14) reste assurée : communiquer ne signifie pas partager tout son contexte.
