# core/agents — Agents personnalisés

Dépôt des définitions d'**agents personnalisés** (EF-03, tickets #70/#72) : le
catalogue d'agents n'est plus figé au code, un agent se définit (nom, rôle,
playbook, compétences, fournisseur/modèle), se persiste ici et devient routable
et exécutable comme un agent par défaut.

## Fonctionnement

- Un fichier par agent : `<nom>.json` (la définition intégrale, horodatée).
- Le **catalogue effectif** d'une exécution est l'assemblage
  `maestro.agents.catalogue()` : les agents par défaut du code
  (`maestro/agents/catalog.py`, inchangés et prioritaires au routage), puis les
  personnalisés de ce dépôt — un dépôt vide reproduit exactement le catalogue
  d'origine.
- Lecture/écriture par le code : `maestro.agents.store.AgentStore` ; par HTTP :
  les endpoints `/api/catalogue` de l'API Control Tower
  (`maestro/controltower/app.py`) — création, modification, suppression ; depuis
  l'UI : la page `/catalogue` de la Control Tower (#73, `apps/web`).
- Racine remplaçable par `MAESTRO_AGENTS_DIR` (cf. `.env.example`).

Les définitions écrites ici sont des **données d'exécution** : elles ne sont pas
commitées (voir `.gitignore`). Le chargement se fait **au câblage** : moteurs
(`OrchestrationEngine.default`), workers (premier message du process) et API
Control Tower assemblent le catalogue effectif à leur construction — un agent
créé vaut pour les moteurs construits ensuite ; workers et API doivent voir le
même stockage au POC (fichiers partagés). Sans runtime outillé, un agent
personnalisé exécute par le chemin texte, cadré par son playbook et son modèle.
En V1, ce stockage passera en base (entité `AGENT`, docs/03) sans changer le
contrat.

Tests (#71) : `tests/test_agent_store.py` (dépôt, catalogue effectif, routage,
exécution) et `tests/test_controltower.py` §⑦ (API `/api/catalogue`).

## Rangement par projet (#1038)

Depuis [docs/37 §2.1](../../docs/37-decision-equipe-sur-mesure.md), **un agent
appartient à un projet**. Ce dossier porte donc deux niveaux :

- **la racine** — les **gabarits de rôle**, ce qui vaut hors de tout projet et ce
  que l'analyse d'équipe consultera (#1039) ;
- **`_projets/<projet_id>/`** — la même arborescence, pour un projet. Le tiret bas
  le met hors d'atteinte d'un nom d'agent, qui commence par `[a-z0-9]`.

L'API sert ces deux niveaux par `?projet=<id>` (omis : les gabarits), et
l'exécution lit ceux du projet de la tâche. Code :
`maestro/agents/rangement.py` (la règle), `maestro/agents/configuration.py`
(les six dépôts d'un bloc).

**⚠ Seul dépôt à n'hériter de rien** : ce qu'il stocke décide de l'**existence**
d'un agent, et l'appartenance n'a pas de défaut sensé là où un réglage en a un.
Une définition rangée à la racine est un *gabarit*, pas un membre de l'équipe d'un
projet — un agent d'un projet n'apparaît donc dans aucun autre.

**Reprise.** Ce qu'un poste portait ici avant #1038 est rattaché au projet qui
l'utilise au démarrage de l'API — idempotente, sans rien supprimer, et elle dit
ce qu'elle a fait. À la main : `python -m maestro.agents.reprise [--check]
[--projet <id>]` (`MAESTRO_REPRISE_AGENTS=0` pour s'en passer).
