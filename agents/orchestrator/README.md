# agent Orchestrateur (Chef de projet)

Décompose un objectif en tâches, planifie les dépendances, assigne et synthétise.
Modèle : Opus. Voir
[`docs/04-specifications-agents.md`](../../docs/04-specifications-agents.md) (§3.1 : fiche du
rôle ; §1 : structure d'un playbook).

## Playbook

Comme les rôles exécutants, son playbook est un **document Markdown livré avec le paquet** :
[`maestro/orchestrator/playbook.md`](../../maestro/orchestrator/playbook.md) (#298), lu par
`maestro.orchestrator.prompt`. Il vit **à part** de
`maestro/agents/playbooks_defaut/` et n'entre pas dans `PLAYBOOK_DEFAUTS` : le Chef de projet
n'exécute aucune tâche, n'a pas de profil outillé, et n'est donc pas éditable depuis la page
`/playbooks` de la Control Tower.

Ce qui le distingue : il **ne pose jamais de question** — sa réponse est consommée par une
machine, personne ne la lit avant l'exécution — et ce qu'il n'écrit pas dans une tâche, l'agent
qui la reçoit ne l'aura jamais. Ce qui est irréversible, destructif ou hors périmètre ne se
planifie pas en silence : il en fait une tâche **explicite** nommant la décision qui revient à
un humain.

> **Placeholder** — l'implémentation POC vit dans le paquet Python : [`maestro/orchestrator/`](../../maestro/orchestrator/)
> (décomposition objectif → tâches) et [`maestro/engine/`](../../maestro/engine/) (boucle d'orchestration).
