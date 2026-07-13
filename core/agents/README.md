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
  (`maestro/controltower/app.py`) — création, modification, suppression.
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
