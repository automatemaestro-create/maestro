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

## Schéma de brief — `schemas/brief.schema.json`

Contrat **partagé** décrivant le **brief structuré** que le Chef de projet rédige à
partir d'un objectif et de ses sources extraites, **avant** toute décomposition en
tâches (EF-40, [docs/24 §3.3](../../docs/24-projets-locaux-et-poste-de-travail.md)).
Même patron que le schéma de tâche : JSON Schema draft 2020-12, langage-agnostique,
`additionalProperties` interdit.

Le brief est le point de contrôle le plus rentable du produit : corriger un brief
coûte un message, corriger douze tâches coûte douze exécutions
([docs/09](../../docs/09-exemple-chiffre.md)). Il est présenté à l'humain avant
toute exécution payante — c'est le geste qui fait passer l'utilisateur
d'« opérateur » à « chef d'orchestre ».

### Champs

| Champ | Type | Requis | Rôle |
|-------|------|:-----:|------|
| `objectif` | `string` (≥ 1) | ✅ | L'intention **reformulée**, en une à trois phrases. |
| `perimetre` | `string[]` (≥ 1, uniques) | ✅ | Ce qui est dans le sujet. |
| `hors_perimetre` | `string[]` (uniques) | ✅ | Ce qui est explicitement **dehors**. Peut être vide. |
| `contraintes` | `string[]` (uniques) | — | Technique, délai, budget, conformité, existant à préserver. |
| `criteres_acceptation` | `string[]` (≥ 1, uniques) | ✅ | À quoi on saura que c'est fait — observables et vérifiables. |
| `hypotheses` | `string[]` (uniques) | — | Ce qui a été tranché seul là où l'objectif était muet. |
| `questions` | `string[]` (uniques) | — | Les zones d'ombre que le Chef de projet ne tranche **pas** seul. |

Deux choix de ce tableau ne se déduisent pas de l'énoncé et méritent d'être dits.

**`hors_perimetre` est requis mais admis vide.** Une liste vide dit « j'ai regardé,
il n'y a rien à exclure » ; une clé absente dit « je n'y ai pas pensé » — ce sont
deux briefs différents, et c'est la section qui empêche la dérive de périmètre. Les
trois autres champs facultatifs (`contraintes`, `hypotheses`, `questions`) retombent,
eux, sur `[]` : un modèle qui n'a rien à y mettre ne doit pas faire échouer le brief
entier.

**Une `question` est une chaîne, pas un objet à identifiant.** Le brief est
**régénéré en entier** à chaque tour d'aller-retour de clarification (#321) : une
question n'a donc pas d'identité stable d'une version à l'autre, et lui donner un
`id` laisserait croire le contraire. Les réponses s'adressent au brief **stocké**
d'un run, dont la liste de questions est figée entre sa publication et sa réponse.

### Validation

- **Python** : `maestro.orchestrator.validate_brief` (validateur `jsonschema`
  chargé depuis ce fichier — source unique de vérité), et `Brief.from_dict` pour la
  forme Python immuable. `Orchestrator.brief(objectif, sources_extraites)` enchaîne
  appel modèle, extraction tolérante et validation.
- **Autre langage** : charger `schemas/brief.schema.json` dans n'importe quel
  validateur JSON Schema draft 2020-12.
