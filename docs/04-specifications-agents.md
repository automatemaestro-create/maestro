# Spécifications des agents — Maestro

**Version :** 0.1
Ce document décrit chaque agent par défaut : son **rôle**, ses **compétences** (pour l'auto-assignation), ses **outils**, et un exemple de **playbook**.

---

## 1. Qu'est-ce qu'un *playbook* ?

Un **playbook** est le **workflow d'un agent** : la liste d'étapes/instructions qu'il suit pour accomplir ses tâches. C'est un document structuré (Markdown), **versionné** et **modifiable depuis l'UI sans redéploiement** (exigences EF-24 à EF-26).

Un playbook peut aussi **se réviser à partir des échecs de l'agent** : après un run en échec, une analyse déclenchée à la demande produit une **proposition** de version révisée — un brouillon que le moteur ne charge jamais tant qu'un humain ne l'a pas appliquée depuis l'UI ([docs/22](./22-auto-amelioration-playbooks.md), #111).

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

```markdown
# Playbook — DevOps
## Mission
Construire les pipelines CI/CD et l'infrastructure, préparer les déploiements.
## Entrées attendues
La tâche d'infrastructure (objectif + format de sortie) et, le cas échéant, les livrables
des tâches dont elle dépend (le code à conteneuriser, le schéma à déployer…).
## Étapes
1. Lire la tâche et les livrables transmis par les tâches amont.
2. Écrire la configuration dans l'espace de travail (pipeline, Dockerfile, scripts, IaC).
3. Valider localement ce qui peut l'être (syntaxe, exécution à blanc) ; consigner les
   résultats réels.
4. Préparer le déploiement en fichiers : runbook, plan de rollback — sans l'exécuter.
5. Rendre un compte-rendu listant ce qui requiert une validation humaine.
## Critères de "terminé" (Definition of Done)
- La configuration existe en fichiers, validée localement quand c'est possible ; ce qui
  requiert une validation humaine est explicitement listé.
## Garde-fous
- Actions nécessitant une validation humaine : tout déploiement (surtout en production),
  toute modification d'une infrastructure existante.
- Actions interdites : déployer vers un environnement réel depuis l'espace de travail ;
  dépasser les plafonds de ressources (processus persistant, service à l'écoute) ; sortir
  de son espace de travail.
## Format de sortie
Configuration (pipeline, Dockerfile, scripts, runbook) en fichiers + compte-rendu listant
les validations humaines requises avant toute application réelle.
```

### 3.5 🎨 Designer

- **Mission :** proposer des écrans, maquettes et composants conformes à une charte.
- **Outils :** MCP Figma, génération de specs UI, design tokens.
- **Garde-fous :** respecte le design system existant ; propose, ne remplace pas la charte sans accord.

```markdown
# Playbook — Designer
## Mission
Proposer des écrans, maquettes et composants conformes à la charte.
## Entrées attendues
La tâche de design (objectif + format de sortie), la charte / le design system quand ils
existent, et le cas échéant les livrables des tâches dont elle dépend.
## Étapes
1. Lire la tâche, la charte et les livrables transmis par les tâches amont.
2. Cadrer le besoin : parcours, écrans, composants et états à couvrir.
3. Produire le livrable en fichiers dans l'espace de travail : spécifications d'écran,
   maquettes/wireframes (HTML ou SVG au POC), design tokens, guide de composants.
4. Vérifier la conformité à la charte et l'accessibilité (contrastes, navigation
   clavier, libellés).
5. Rendre un compte-rendu listant les partis pris et toute évolution de charte proposée.
## Critères de "terminé" (Definition of Done)
- Le livrable existe en fichiers, conforme à la charte ; les partis pris et propositions
  d'évolution sont explicitement signalés.
## Garde-fous
- Actions nécessitant une validation humaine : toute évolution de la charte ou du design
  system (l'agent propose, il ne remplace pas sans accord).
- Actions interdites : réécrire la charte existante de sa propre initiative ; sortir de
  son espace de travail.
## Format de sortie
Spécifications d'écran, maquettes/wireframes, design tokens (fichiers) + compte-rendu
listant les partis pris et les propositions soumises à accord.
```

### 3.6 🧪 QA / Testeur

- **Mission :** écrire et exécuter les tests, valider les livrables, faire la revue.
- **Outils :** frameworks de test, exécution e2e, lecture de PR.
- **Particularité :** peut **bloquer** une tâche jugée non conforme et la renvoyer au Développeur.

```markdown
# Playbook — QA / Testeur
## Mission
Vérifier la qualité des livrables : écrire et exécuter les tests, valider, faire la revue.
## Entrées attendues
La tâche à vérifier (objectif + format de sortie) et les livrables des tâches dont elle
dépend (le tableau noir) — c'est la matière de la revue.
## Étapes
1. Lire la tâche et les livrables transmis par les tâches amont.
2. Écrire les tests (unitaires, intégration, e2e selon la tâche) dans l'espace de travail.
3. Exécuter ce qui peut l'être ; consigner les résultats réels.
4. Faire la revue du livrable : conformité au format de sortie attendu, défauts, manques.
5. Rendre un rapport avec un verdict explicite : conforme / non conforme.
## Critères de "terminé" (Definition of Done)
- Les tests et le rapport de revue existent en fichiers ; le verdict est explicite et étayé.
## Garde-fous
- Actions nécessitant une validation humaine : aucune en propre — le verdict « non conforme »
  éclaire la décision humaine (au POC, pas de rétro-boucle automatique vers le Développeur).
- Actions interdites : corriger soi-même le livrable évalué (la correction revient au rôle
  producteur) ; sortir de son espace de travail.
## Format de sortie
Suite de tests + rapport de revue (fichiers) et compte-rendu avec verdict
conforme / non conforme ; en cas de non-conformité, la liste précise de ce qui bloque.
```

---

## 4. Créer un agent personnalisé

Depuis la Control Tower, l'utilisateur peut créer un agent en définissant :

1. **Nom & rôle** (ex. « Rédacteur technique »).
2. **Prompt système** (identité, ton, contraintes).
3. **Compétences/tags** (pour le routage).
4. **Outils** à lui lier (avec permissions scopées).
5. **Fournisseur + modèle** (selon complexité, coût, souveraineté) — Claude, OpenAI, Google, modèle ouvert/local… via la **couche d'abstraction** ; par défaut, le Claude du POC.
6. **Playbook** initial (workflow), ensuite versionné.

**Disponible au POC** (EF-03, tickets #70/#72/#73) : la définition — nom, rôle,
compétences, playbook (qui sert de prompt système d'exécution), fournisseur/modèle —
se crée depuis la page `/catalogue` de la Control Tower ou l'API `/api/catalogue`,
et se **persiste hors du code** (`core/agents/<nom>.json`, racine remplaçable par
`MAESTRO_AGENTS_DIR`). Le catalogue effectif d'une exécution assemble les agents par
défaut du code puis les personnalisés : un agent créé est **routable et exécutable**
par les moteurs construits ensuite ([guide de démarrage §6.3](./07-guide-de-demarrage.md)).
Restent pour la suite : la **liaison d'outils** scopés (au POC, un agent personnalisé
exécute par le chemin texte, sans runtime outillé) et l'**exécution multi-fournisseurs**
(le champ `fournisseur` est déclaratif, le moteur exécute sur `MAESTRO_PROVIDER`).

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

---

## 6. Serveurs MCP par agent

Le **Model Context Protocol** relie les agents aux outils externes (Slack, gestion de tickets, Figma, cloud…) sans connecteur ad hoc — complémentaire d'A2A (§5). Depuis le ticket #104 (parent #101), chaque agent peut **déclarer des serveurs MCP**, montés par le moteur sur ses exécutions. Le parent #129 a fait évoluer le **modèle de configuration** : le fichier par agent reste la forme héritée (§6.1), mais la source recommandée est désormais un **pool projet + activation par agent**, configurable **depuis la Control Tower** et alimenté par une **bibliothèque curée** (§6.4).

### 6.1 Déclaration héritée (un fichier par agent, versionnée)

Un fichier JSON par agent — `core/mcp/<agent>.json` (racine remplaçable par `MAESTRO_MCP_DIR`), **versionné avec le dépôt Git** et **validé à la lecture** (une déclaration invalide est refusée avec sa cause exacte, jamais montée à moitié) :

```json
{
  "serveurs": [
    {
      "nom": "gitlab",
      "type": "stdio",
      "commande": "npx",
      "args": ["-y", "@zereight/mcp-gitlab"],
      "env": { "GITLAB_PERSONAL_ACCESS_TOKEN": "${GITLAB_TOKEN}" }
    },
    {
      "nom": "slack",
      "type": "http",
      "url": "https://mcp.example.com/slack",
      "headers": { "Authorization": "Bearer ${SLACK_MCP_TOKEN}" }
    }
  ]
}
```

Deux formes, verrouillées sur leur `type` : une **commande locale** (`stdio` : `commande` + `args` + `env`) ou un **endpoint distant** (`sse`/`http` : `url` + `headers`). Le `nom` (slug `[a-z0-9_-]`) préfixe les outils exposés à l'agent (`mcp__<nom>__<outil>`). Un serveur peut se déclarer **`"optionnel": true`** (#125) : si l'une de ses références `${VAR}` ne se résout pas au montage, il est **omis** (la tâche s'exécute sans lui) au lieu de la faire échouer — le canal des capacités qui ne s'activent que lorsqu'un humain a fourni le secret (ex. le serveur officiel Figma du designer, [docs/20 §6](./20-pilote-mcp-figma.md)).

**Secrets — jamais en clair** (anticipe le chantier sécurité #102) : les valeurs d'`env`/`headers` portent des références `${VARIABLE}` résolues depuis l'environnement **au moment du montage** — la valeur effective n'existe qu'en mémoire, jamais dans le fichier versionné. L'API/UI masque d'ailleurs toute valeur littérale (seules les références `${VAR}` restent lisibles).

### 6.2 Montage à l'exécution

Le moteur relit la déclaration **à chaud à chaque tâche** (comme les playbooks, #78) et confie la liste à la **couche SDK** (`ModelProvider.run_agent(mcp_serveurs=…)`) : aucune logique d'agent n'appelle un fournisseur en direct, la traduction vers le format natif (Agent SDK pour Claude) vit dans la couche fournisseur. La session est **verrouillée sur les serveurs déclarés** : aucune configuration MCP ambiante (utilisateur, projet, plugin) n'est jamais chargée — permissions scopées (docs/02 §7). Elle est aussi **retenue jusqu'à la connexion des serveurs** (statut sondé, délai borné à 60 s) : le CLI enregistre les outils MCP après son ouverture de session, et sans ce sas le premier tour du modèle partirait sans eux — l'agent conclurait amputé de ses capacités (constat du pilote #105, corrigé dans la couche fournisseur).

Les serveurs n'équipent que les **exécutions outillées** : le chemin texte (`generate` — agents sans runtime outillé, ou repli d'un fournisseur texte-seul) n'expose aucun outil, MCP compris.

### 6.3 Serveur indisponible — comportement garanti

Une tâche dont un serveur MCP déclaré ne peut pas être monté **échoue proprement, avant le travail de l'agent** — plutôt que de le laisser produire un livrable amputé de ses capacités :

- **déclaration invalide** (JSON illisible, type inconnu, forme ambiguë…) : refusée à la lecture, échec de tâche avec la cause exacte ;
- **référence `${VAR}` sans variable d'environnement** : serveur « non montable », échec avant tout appel modèle — sauf serveur déclaré **optionnel** (#125), alors simplement omis du montage ;
- **serveur injoignable** (démarrage/connexion en échec, authentification requise, ou jamais connecté avant l'échéance du sas de connexion) : échec avec le serveur et la cause nommés (`McpServerUnavailable`), avant tout appel modèle.

Dans les trois cas l'erreur est **tracée** comme tout échec de tâche (journal du run, fil temps réel de la Control Tower, Langfuse) et **jamais relancée** (ENF-06) : la cause est déterministe — corriger la déclaration, le secret ou le serveur, la tâche suivante repart à chaud.

**Disponible au POC** (#104, lot 1/4 du parent #101) : déclaration validée, montage sur les exécutions outillées, affichage lecture seule sur la fiche agent (page `/catalogue`). **Pilotes livrés** : Slack (#105) — l'agent `devops` poste les notifications de supervision d'un run via `maestro-run --notifier devops` ([docs/15](./15-pilote-mcp-slack.md)) ; gestion de tickets GitLab (#106) — l'agent `qa` lit et crée des tickets du backlog ([docs/16](./16-pilote-mcp-tickets-gitlab.md)) ; Figma (#115, basculé sur le serveur MCP **officiel** par #128 — token OAuth fourni par l'humain) — l'agent `designer` crée et lit des éléments d'un fichier Figma ([docs/20](./20-pilote-mcp-figma.md)). Tests du socle et des pilotes : #103, [tests/test_mcp.py](../tests/test_mcp.py).

### 6.4 Pool projet, bibliothèque et configuration depuis la Control Tower

Un fichier par agent (§6.1) répond à *« quels serveurs cet agent monte-t-il ? »* mais duplique la config quand deux agents partagent une intégration (le même Figma déclaré deux fois, secret compris), reste inaccessible à un non-technicien et n'offre **aucune découverte** des intégrations disponibles. Le parent #129 lève ces trois limites — configurer les MCP **depuis la Control Tower, pour tout fournisseur de modèle** — en gardant le contrat de montage (§6.2/§6.3) inchangé.

**Pool projet + activation par agent** (#130). Une intégration (`IntegrationMcp` : un `id` stable + une déclaration `ServeurMcp`) est déclarée **une seule fois** dans le *pool projet* (`pool.json`), puis **activée** pour un ou plusieurs agents (`activations.json`, `{"<agent>": ["id", …]}`). Le secret n'est saisi qu'une fois ; deux agents partagent la même intégration sans dupliquer ni la déclaration ni le token. `McpStore.lire(agent)` ne lit plus un fichier isolé mais **compose** deux sources : la déclaration héritée de l'agent (§6.1), **puis** les intégrations du pool activées pour lui.

- **Rétro-compatibilité stricte** : sans activation, `lire(agent)` renvoie exactement la déclaration héritée — le pool n'est même pas lu, les runs qui s'appuient sur `core/mcp/<agent>.json` ne changent pas. En cas de collision de `serveur.nom` entre l'héritée et une intégration activée, **l'héritée l'emporte** (la source qu'un run utilise déjà reste autoritaire).
- **Migration outillée, jamais imposée** : `composer_migration`/`migrer` dérivent le pool + les activations des fichiers hérités (serveurs identiques mutualisés en une intégration partagée), à persister quand on le décide ; le retrait des fichiers hérités est explicite. Les deux formes cohabitent tant qu'il n'a pas lieu.
- **Validation à la lecture** (comme le socle) : une activation qui pointe une intégration absente du pool est refusée à la lecture avec sa cause — jamais un montage à moitié.

**Bibliothèque (registre curé)** (#131). Un *registre* de **templates** de serveurs MCP recherchable par nom/tag (`maestro.agents.mcp_registry`) répond à *« quelles intégrations existe-t-il, et comment les configurer ? »*. Chaque entrée porte transport, gabarit d'exécution `${VAR}` (jamais de secret), **mode d'auth** ([docs/21](./21-configuration-mcp.md)), variables à fournir et lien de procédure côté outil. **Garde-fou supply-chain** ([docs/19](./19-securite-modele-de-menace.md)) : *découverte ≠ installation* — seule une entrée de l'**allowlist curée** est instanciable (`RegistreMcp.instancier`, l'unique voie template → liaison), jamais un `npx -y <pkg arbitraire>`. Exposé par `GET /api/mcp/registre` (liste + `?q=`) et `GET /api/mcp/registre/{id}`.

**Secrets chiffrés côté serveur** (#132, parent #102). Le secret d'une intégration n'est **jamais** dans la déclaration versionnée (`${VAR}`, §6.1) : il vit dans le **coffre de l'agent** (`maestro.agents.secrets.SecretStore`), **chiffré au repos** (Fernet, clé `MAESTRO_SECRETS_KEY` ou clé locale). La résolution des `${VAR}` au montage (`resolus`) se fait dans **ce coffre seulement** — un agent ne voit que ses propres secrets. Trois **modes d'auth** ([docs/21 §3.2](./21-configuration-mcp.md)) : token statique chiffré, valeur d'appairage éphémère non secrète, token OAuth importé **expirable** (échu → serveur refusé au montage s'il est requis, omis s'il est `optionnel` #125 ; renouvellement humain).

**Écriture depuis la Control Tower** (#133). La Control Tower devient la **source en écriture** de cette configuration, en remplacement de l'édition manuelle du fichier : le store (`ecrire_pool`/`ecrire_activations`, écritures atomiques et versionnées) et le coffre (`enregistrer`/`renouveler`/`supprimer`) portent le contrat backend ; la page Paramètres (#121) porte l'UI — bibliothèque, configuration d'une intégration et activation par agent. La fiche agent du catalogue reste la vue **lecture seule** de la composition (`mcp_serveurs`, valeurs masquées), désormais nourrie par `lire(agent)` (pool ∩ activation compris).

Tests (sans réseau) : composition et rétro-compat [tests/test_mcp_pool.py](../tests/test_mcp_pool.py) (#130), registre + recherche + garde-fou [tests/test_mcp_registry.py](../tests/test_mcp_registry.py) (#131), secrets chiffrés + 3 parcours [tests/test_secrets_chiffrement.py](../tests/test_secrets_chiffrement.py) (#132), **parcours de bout en bout** (bibliothèque → pool → activation → coffre chiffré → composition → montage) [tests/test_mcp_config.py](../tests/test_mcp_config.py) et composition vue par l'API [tests/test_controltower.py](../tests/test_controltower.py) (#134).
