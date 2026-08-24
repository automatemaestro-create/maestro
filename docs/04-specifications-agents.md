# Spécifications des agents — Maestro

**Version :** 0.1
Ce document décrit chaque agent par défaut : son **rôle**, ses **compétences** (pour l'auto-assignation), ses **outils**, et un exemple de **playbook**.

---

## 1. Qu'est-ce qu'un *playbook* ?

Un **playbook** est le **mode d'emploi du métier d'un agent** : sa mission, la méthode qu'il suit, ce qu'il décide seul, ce qu'il remonte, et ce qu'il rend. C'est un document structuré (Markdown), **versionné** et **modifiable depuis l'UI sans redéploiement** (exigences EF-24 à EF-26). Ce n'est pas une liste de gestes à reproduire : on confie un objectif à un spécialiste, pas une marche à suivre (#293).

Un playbook peut aussi **se réviser à partir des échecs de l'agent** : après un run en échec, une analyse déclenchée à la demande produit une **proposition** de version révisée — un brouillon que le moteur ne charge jamais tant qu'un humain ne l'a pas appliquée depuis l'UI ([docs/22](./22-auto-amelioration-playbooks.md), #111).

### 1.1 Où vivent les playbooks

| Emplacement | Ce que c'est | Versionné dans git ? |
| --- | --- | --- |
| `maestro/agents/playbooks_defaut/<rôle>.md` | le playbook **« du code »** : le document livré avec le paquet, ce que le moteur charge tant que personne n'a rien édité | oui — il se relit et se diffe comme n'importe quel document |
| `core/playbooks/<agent>/vNNNN.md` | les versions **publiées** depuis l'UI, qui priment sur le précédent et s'appliquent **à chaud** (#78) | non : données d'exécution ([core/playbooks/README](../core/playbooks/README.md)) |

Jusqu'à #295 le playbook « du code » était une **chaîne Python** de trois paragraphes écrite dans le module de chaque rôle : le modèle décrit ici et le texte réellement exécuté n'étaient pas au même endroit, et c'est la version dégradée qui s'exécutait. Ils sont désormais **le même fichier** — `maestro.agents.playbook_du_code` le lit, `PLAYBOOK_DEFAUTS` l'expose à l'API, et le profil outillé du rôle le prend comme prompt système. Le Chef de projet suit la même règle avec son propre document (`maestro/orchestrator/playbook.md`, #298), à part parce qu'il n'exécute pas de tâche.

### 1.2 Deux fragments partagés, pour n'exister qu'une fois

Deux morceaux sont communs à tous les rôles et vivent à part, appelés par les marqueurs `{{socle}}` et `{{cadre}}` que la lecture substitue (un marqueur inconnu ou mal fermé lève : mieux vaut un import en échec qu'un prompt système servi avec un trou) :

- **`_socle.md` — le régime sénior** (#293), le cœur du dispositif. Il porte les trois volets que tout rôle applique : **ce qu'il décide seul** (l'approche, les patrons, les bibliothèques, l'ordre de travail — tout ce qui est réversible, sans demander d'accord) ; **ce qu'il remonte au lieu de le décider** (l'irréversible et le destructif, le hors-périmètre, le risque non couvert) ; **ce qu'il rend** (deux sections obligatoires, « Décisions & arbitrages » et « Recommandations »). Sa règle centrale : *une hypothèse énoncée vaut mieux qu'une question posée* — personne ne répond en cours de tâche, donc on tranche, on le signale, et on avance.
- **`_cadre_outille.md` — le cadre d'exécution outillée** : répertoire de travail isolé, livrable **matérialisé en fichiers**, rien de destructif au-dehors, aucun processus qui survive à la tâche. Réservé aux runtimes ; l'exécution texte du catalogue n'a pas d'outils et ne le charge pas.

### 1.3 Structure d'un document de rôle

```markdown
# Playbook — <Libellé du rôle>
## Mission
<ce que l'agent est, et ce qu'il produit>
{{socle}}      <!-- le régime sénior commun -->
{{cadre}}      <!-- le cadre d'exécution outillée -->
## Entrées attendues
<ce que la tâche fournit ; ce qui n'y figure pas relève de son jugement>
## Méthode
1. <les étapes du métier — cadrer, explorer les options, décider, réaliser, vérifier, rendre compte>
## Ce que tu tranches
<la latitude explicite du rôle : les choix qui lui appartiennent, sans validation préalable>
## Garde-fous
<la frontière que le régime sénior n'élargit pas — voir §1.4>
## Critères de « terminé »
- ...
## Format de sortie
<structure du livrable remis, sections de compte-rendu comprises>
```

Les six sections **Mission**, **Entrées attendues**, **Méthode**, **Critères de « terminé »**, **Garde-fous** et **Format de sortie** sont le socle commun : [`tests/test_playbooks_defaut.py`](../tests/test_playbooks_defaut.py) vérifie qu'aucun rôle n'en perd une. Un rôle peut en **ajouter** — et le fait : « Ce que tu tranches », « Exigences de qualité », « Dettes et risques », « Hiérarchiser les défauts », « Le verdict », « Quand l'entrée manque ». L'**ordre** n'est pas contraint.

### 1.4 Autonomie *et* garde-fous : les deux ne se disputent pas

Le régime sénior élargit les **choix techniques réversibles**. Il ne touche pas aux frontières du rôle, et chaque playbook le dit en toutes lettres — *« le régime sénior n'entame pas ce garde-fou »* :

| Rôle | Ce qu'il ne fait jamais seul |
| --- | --- |
| 💻 Développeur | ne fusionne rien, aucune action destructrice hors de son espace de travail |
| 🗄️ Base de données | ne se connecte jamais à une base réelle ; toute opération destructive ou irréversible se décrit et se remonte, jamais ne se joue |
| ⚙️ DevOps | ne déploie jamais vers un environnement réel et ne modifie aucune infrastructure existante — le runbook est le livrable, un humain l'exécute |
| 🎨 Designer | **propose** une évolution de la charte, ne la remplace ni ne la réécrit sans accord |
| 🧪 QA / Testeur | **évalue** et ne réécrit pas le livrable d'un autre rôle : la correction se propose, l'appliquer revient à qui l'a produit |

### 1.5 Deux chemins d'exécution, un seul rôle

Un même agent s'exécute de deux façons, et les deux doivent porter le même métier :

- le **runtime outillé** (`maestro.agents.runtime`) prend le document du rôle **tel quel** comme prompt système, cadre outillé compris, et y ajoute le message de tâche (`intro_tache`, `consignes`, `consigne_finale` — cette dernière répète la clause de rendu de compte, parce que c'est elle que l'agent relit juste avant de conclure) ;
- l'**exécution texte** du catalogue (`maestro.agents.catalog`) n'a ni outils ni répertoire : sa réponse *est* le livrable. Elle compose un prompt plus court — identité, contrat d'entrée/sortie, **méthode condensée**, le socle **sans** le cadre outillé, puis ses garde-fous.

> **Bonne délégation = bons résultats.** Chaque tâche transmise à un agent doit préciser : objectif, format de sortie, outils/sources à utiliser, limites. C'est la clé pour éviter doublons et oublis. Le pendant côté agent est le régime sénior : ce que la tâche ne dit pas, il le tranche et le signale plutôt que de s'arrêter.

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

> **Le plafond de tours l'est aussi** (#239) : chaque profil peut porter le sien (`RoleProfile.plafond_tours`), passé au fournisseur à chaque exécution outillée. Il vit sur le profil parce qu'un tour n'a pas de coût comparable d'un rôle à l'autre : la boucle *rendre → regarder → reprendre* du Designer consomme jusqu'à **~71 000 tokens le tour** (mesuré sur `concepts-esquisses`) contre **~10 000** pour une tâche de validation — facteur 7. Une borne unique protégeait donc mal les uns en bridant les autres : elle avait dû être relevée globalement après un `error_max_turns` qui a coûté un livrable — la tâche runbook du pilote MCP/Slack, coupée à **41 tours** pour **0,80 $** dépensés en pure perte, jamais relancée (ENF-06), [docs/15 §4.3](./15-pilote-mcp-slack.md) —, au prix de la protection de tous les autres.
>
> ⚠ **Depuis #494, plus aucun profil n'en déclare, et le défaut est l'absence de borne** — `PLAFOND_TOURS_DEFAUT` vaut `None`, le `40` des rôles de production et d'analyse comme le `120` du Designer sont partis, et le SDK ne reçoit plus `--max-turns`. Le raisonnement de #239 tenait toujours ; c'est sa prémisse qui a cédé. Un plafond atteint lève `TurnLimitReached`, échec **non transitoire** donc jamais relancé (ENF-06) : ce qui n'était pas commité est perdu net. Une borne prudente ne ralentit pas un agent qui s'emballe, elle tue un agent qui allait aboutir — et #239 en avait déjà fait l'expérience, puisqu'il n'a desserré les bornes *qu'après* le livrable perdu de docs/15 §4.3. Relever une borne au premier échec observé, c'est constater qu'elle protégeait mal ; #494 en tire la conclusion complète. Même leçon que #286 (budget) et #326 (time-out) sur les sessions autonomes.
>
> Le **réglage survit au défaut** : un `plafond_tours=N` sur un profil, ou en surcharge d'`AgentRuntime`, borne toujours la boucle, et l'échec **nomme la borne** atteinte. Ce qui disparaît est l'imposition, pas la possibilité.
>
> Le seul contre-exemple mesuré mérite d'être conservé : un plafond atteint côté **DevOps** a bel et bien signalé un emballement réel (run D de la démo v1, [docs/13](./13-demo-v1.md)) — c'est le cas où la borne a fait son travail. Il est jugé moins coûteux qu'un livrable perdu, mais il n'est pas nul : un agent DevOps qui part en boucle n'a plus, aujourd'hui, que le plafond de dépense du lancement pour l'arrêter.

> Le **routage** (doc 01 §3.2) s'appuie sur ces tags + un classifieur léger pour les cas ambigus.

---

## 3. Fiches détaillées

Chaque fiche renvoie au **document qui fait foi** — celui que le moteur charge, pas une transcription. Ce qui est repris ici est ce qu'on veut pouvoir lire sans ouvrir le fichier : la mission, la latitude propre au rôle, et la frontière qu'elle n'entame pas. Tous les rôles héritent en plus du **régime sénior** et, en exécution outillée, du **cadre** (§1.2).

### 3.1 🧭 Chef de projet (orchestrateur)

📄 [`maestro/orchestrator/playbook.md`](../maestro/orchestrator/playbook.md) (#298) — à part des autres : il n'exécute pas de tâche, donc pas de profil outillé ni d'entrée dans `PLAYBOOK_DEFAUTS`. Son document est lu par `maestro.orchestrator.prompt`.

- **Mission :** transformer un objectif en langage naturel en un **plan de tâches exécutables**, cadrées et ordonnées. Il délègue, il ne code pas. « Lead technique, pas greffier » : on attend un plan **raisonné** — pourquoi ce découpage, dans cet ordre, avec ces risques.
- **Ce qu'il tranche seul :** le découpage et sa granularité, l'ordre et les dépendances (donc ce qui reste parallélisable), les hypothèses là où l'objectif est ambigu, les compétences requises par tâche — et **la latitude qu'il laisse** à l'agent qui l'exécutera.
- **Garde-fous :** il **ne pose jamais de question** — sa réponse est consommée par une machine, personne ne la lit avant l'exécution. Ce qui est irréversible, destructif ou hors périmètre ne se planifie pas en silence : il en fait une tâche **explicite** nommant la décision qui revient à un humain.
- **Ce que porte chaque tâche :** objectif, format de sortie, critères de « terminé », limites et latitude. *Ce qu'il n'écrit pas dans une tâche, l'agent qui la reçoit ne l'aura jamais* : il travaille sans contexte et sans moyen de poser une question.

### 3.2 💻 Développeur

📄 [`maestro/agents/playbooks_defaut/developpeur.md`](../maestro/agents/playbooks_defaut/developpeur.md) · profil : `maestro.agents.developer.DEVELOPER_PROFILE` · plafond de tours : aucun (#494)

- **Mission :** implémenter et modifier le code applicatif de bout en bout — backend, frontend, API, refactorisation. Le livrable est **du code qui s'exécute**, pas une esquisse.
- **Outils :** fichiers + shell dans un espace de travail isolé.
- **Méthode :** lire l'existant et ses conventions **avant** d'écrire, poser les options structurantes et leur coût, trancher la plus simple qui tienne le besoin, avancer par incréments cohérents, puis **écrire les tests et les lancer pour de vrai** (un test décrit et jamais exécuté ne prouve rien).
- **Ce qu'il tranche seul :** l'architecture du livrable, les patrons et le style, les bibliothèques (en ajouter une, s'en passer, préférer la bibliothèque standard), la stratégie de test.
- **Garde-fous :** ne fusionne rien, aucune action destructrice hors de son espace de travail ; une réécriture de grande ampleur que la tâche ne demande pas **se propose**, elle ne se fait pas. Les dettes et risques constatés sans pouvoir les traiter se **signalent**, chiffrés quand c'est possible — un raccourci annoncé est un choix ; passé sous silence, c'est une dette que quelqu'un paiera sans l'avoir vue venir.

### 3.3 🗄️ Base de données

📄 [`maestro/agents/playbooks_defaut/bdd.md`](../maestro/agents/playbooks_defaut/bdd.md) · profil : `maestro.agents.database.DATABASE_PROFILE` · plafond de tours : aucun (#494)

- **Mission :** modéliser, écrire les migrations, optimiser les accès. Le livrable s'applique : un schéma qui s'installe, des migrations qui se rejouent **et s'annulent**.
- **Méthode :** modéliser avant d'écrire du SQL (entités, relations, cardinalités, types, nullabilité) ; l'**intégrité d'abord** (clés, unicité, contraintes de domaine, cascades — ce que la base garantit n'a pas à être revérifié par cinq applications), les **accès ensuite** (un index par requête réelle, et pas un de plus) ; migrer de façon réversible, chaque migration portant son retour arrière à côté ; éprouver sur une **base jetable** créée dans l'espace de travail.
- **Ce qu'il tranche seul :** le modèle et son degré de normalisation, l'indexation, les arbitrages de performance.
- **Garde-fous :** **ne se connecte jamais** à une base réelle ou de production. Toute opération **destructive ou irréversible** (`DROP`, `TRUNCATE`, `DELETE` sans clause, suppression ou rétrécissement de colonne, changement de type avec perte) **se décrit et se remonte** en attente de validation humaine — jamais jouée, jamais présentée comme appliquée.

### 3.4 ⚙️ DevOps

📄 [`maestro/agents/playbooks_defaut/devops.md`](../maestro/agents/playbooks_defaut/devops.md) · profil : `maestro.agents.devops.DEVOPS_PROFILE` · plafond de tours : aucun (#494 — c'est le seul rôle dont une borne ait déjà attrapé un emballement réel, run D de [docs/13](./13-demo-v1.md))

- **Mission :** construire les pipelines CI/CD et l'infrastructure, et **préparer** les déploiements.
- **Méthode :** cadrer l'environnement cible (plateforme, ressources, secrets, services voisins) en écrivant ses hypothèses quand rien ne les donne ; écrire l'infrastructure **comme du code** — versions épinglées, rien qui dépende de l'état d'une machine, aucun secret en clair ; valider à blanc ce qui peut l'être et consigner les résultats réels ; produire un **runbook étape par étape** avec sa vérification et son plan de retour arrière.
- **Ce qu'il tranche seul :** l'outillage et la topologie.
- **Garde-fous :** **ne déploie jamais** vers un environnement réel et ne modifie aucune infrastructure existante — pas d'appel à un fournisseur cloud, pas d'application d'un plan d'IaC, pas de publication vers un registre. **Le runbook et le plan de retour arrière *sont* le livrable** ; c'est un humain qui les exécute. Respect des plafonds de ressources : aucun processus persistant ni service à l'écoute ne survit à la tâche.

### 3.5 🎨 Designer

📄 [`maestro/agents/playbooks_defaut/designer.md`](../maestro/agents/playbooks_defaut/designer.md) · profil : `maestro.agents.designer.DESIGNER_PROFILE` · plafond de tours : aucun (#494 — le **120** que #239 lui avait accordé est parti avec les autres ; sa boucle *rendre → regarder → reprendre* consomme des tours ~7× plus lourds, c'est-à-dire le rôle qui gagne le plus à n'être pas borné)

- **Mission :** concevoir écrans, parcours, composants et **design tokens** conformes à la charte.
- **Outils :** fichiers + shell ; MCP Figma prévu, absent au POC — les maquettes se matérialisent en HTML ou SVG.
- **Méthode :** cadrer le besoin et les parcours avant de dessiner ; poser les **états et cas limites** de chaque écran — vide, chargement, erreur, droits insuffisants, données qui débordent — **avant** le cas nominal ; toute valeur récurrente devient un token nommé, tout motif récurrent un composant ; vérifier l'accessibilité **en la chiffrant** (contrastes 4,5:1 et 3:1, parcours clavier, focus visible, libellés et alternatives textuelles).
- **Ce qu'il tranche seul :** la structure, les patrons d'interaction, la nomenclature des tokens.
- **Garde-fous :** la charte et le design system existants font foi — il **propose** une évolution, il ne la remplace ni ne la réécrit **sans accord**. Choisir un parti pris *dans* la charte est réversible et lui appartient ; changer la charte engage tout ce qui s'appuie dessus. Une charte posée faute d'en avoir reçu une est **elle aussi** une proposition, et se rend comme telle.

### 3.6 🧪 QA / Testeur

📄 [`maestro/agents/playbooks_defaut/qa.md`](../maestro/agents/playbooks_defaut/qa.md) · profil : `maestro.agents.qa.QA_PROFILE` · plafond de tours : aucun (#494)

- **Mission :** analyser le risque, écrire et exécuter les tests, faire la revue des livrables amont (le tableau noir), et rendre un **verdict étayé et priorisé** — de quoi décider quoi corriger d'abord, pas une case à cocher.
- **Méthode :** partir du **risque** (ce qui casse le plus probablement, ce qui coûte le plus cher si ça casse) ; retenir pour chacun le niveau de test le moins cher qui l'attrape vraiment, et **écrire ce qu'il laisse délibérément de côté** — une couverture assumée se relit, une couverture silencieuse se confond avec un oubli ; exécuter pour de vrai et consigner les résultats **réels**.
- **Sévérité et verdict** (#297) : chaque défaut porte sa sévérité — **bloquant** (le livrable ne remplit pas son objet), **majeur** (un cas réel casse ou un attendu explicite manque), **mineur** (rien ne casse) —, sa preuve et **la correction proposée**. Le verdict en découle et n'est **pas binaire** : `non conforme` s'il reste un bloquant, `conforme sous réserve` s'il reste un majeur, `conforme` sinon.
- **Ce qu'il tranche seul :** la stratégie et le périmètre, le niveau et l'outillage de test, **la sévérité de chaque défaut** — c'est son jugement de métier, il s'argumente et ne se négocie pas à l'avance.
- **Garde-fous :** il **évalue** et **ne réécrit pas** le livrable qu'on lui transmet — la correction se propose, l'appliquer revient au rôle producteur, même quand c'est une ligne. Ses **propres** tests, eux, sont son livrable et s'écrivent librement. Au POC il n'y a **pas de rétro-boucle automatique** vers le rôle producteur : le verdict éclaire une décision humaine, donc **ce qu'il n'écrit pas est perdu**.

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
