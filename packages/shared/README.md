# packages/shared — Types & schémas partagés

Types et schémas communs à l'API, aux agents et au core.

## Schéma de tâche — `schemas/task.schema.json`

Contrat **partagé** décrivant une **tâche** telle que produite par l'orchestrateur
(Chef de projet, [docs/04](../../docs/04-specifications-agents.md)) à partir d'un
objectif, puis consommée par le routeur et les agents exécutants. C'est une
[JSON Schema](https://json-schema.org/) (draft 2020-12) — langage-agnostique et
versionnable — alignée sur l'entité `TASK` du
[modèle de données](../../docs/03-modele-de-donnees.md).

### Champs

| Champ | Type | Requis | Rôle |
|-------|------|:-----:|------|
| `id` | `string` (slug `^[a-z0-9]+(?:-[a-z0-9]+)*$`) | ✅ | Identifiant stable dans le plan ; référent des dépendances. |
| `titre` | `string` (1–120) | ✅ | Intitulé court et actionnable. |
| `description` | `string` (≥ 1) | ✅ | Objectif, périmètre et limites — assez précis pour déléguer sans ambiguïté. |
| `competences_requises` | `string[]` (≥ 1, uniques) | ✅ | Tags de compétences (base du routage / auto-assignation). |
| `format_sortie` | `string` (≥ 1) | ✅ | Le livrable attendu et sa forme (clé d'une bonne délégation). |
| `dependances` | `string[]` (ids, uniques) | — | Ids des tâches prérequises (même plan, **acyclique**). Vide par défaut. |

`additionalProperties` est **interdit** : une tâche bien formée n'expose que ces champs.

### Plan

Un **plan** est un **tableau JSON** de tâches conformes à ce schéma. Au-delà de la
validité de chaque tâche, un plan est *cohérent* quand : les `id` sont uniques,
chaque `dependances` référence un `id` existant du plan, et le graphe de
dépendances est **sans cycle**. L'orchestrateur vise **2 à 3 tâches** par objectif
(cf. critères d'acceptation du ticket #3). Ces règles inter-tâches (non exprimables
en JSON Schema seul) sont vérifiées côté Python par `maestro.orchestrator.validate_plan`.

### Validation

- **Python** : `maestro.orchestrator.validate_task` / `validate_plan` (validateur
  `jsonschema` chargé depuis ce fichier — source unique de vérité).
- **Autre langage** : charger `schemas/task.schema.json` dans n'importe quel
  validateur JSON Schema draft 2020-12.
