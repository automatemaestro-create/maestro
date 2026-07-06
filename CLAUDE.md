# Maestro — Instructions pour Claude Code

Monorepo en phase de cadrage (Phase 0 — POC, voir [docs/06-roadmap.md](./docs/06-roadmap.md)). Squelette de dossiers en place (`apps/`, `core/`, `agents/`, `packages/`, `infra/`), pas encore de code applicatif ni de CI.

## Règles Git obligatoires

- **Jamais de commit direct sur `main`.** Toujours travailler sur une branche de ticket.
- **Un ticket GitLab = une branche = une Merge Request.**
- Nommage de branche : `<type>/<iid>-<slug>` (ex. `feat/6-boucle-orchestration`). Le `type` vient du label `type::*` du ticket (`feature`→`feat`, `bug`→`fix`, `infra`→`chore`, `doc`→`docs`).
- Convention de commit : Conventional Commits + `Refs #<iid>` sur les commits intermédiaires, `Closes #<iid>` sur le commit final ou la description de la MR.
- Détail complet : [docs/10-workflow-git.md](./docs/10-workflow-git.md).

## Statut & labels GitLab (déjà en place — ne pas en réinventer d'autres)

Le **cycle de vie** d'un ticket est porté par le **champ Status natif** de GitLab (lifecycle
custom « Maestro » : À faire / En cours / En revue / Terminé / Abandonné / Doublon), **pas** par
des labels — voir [docs/10 §3](./docs/10-workflow-git.md). Les commandes `/ticket-*` le posent
via `glab api graphql` (`workItemUpdate` → `statusWidget`).

Les **labels** ne servent qu'à catégoriser : **`type::`** (`feature`/`bug`/`doc`/`infra`),
**`agent::`** (`dev`/`bdd`/`devops`/`design`/`qa`/`orchestrateur`) et **`prio::`**
(`haute`/`moyenne`/`basse`) — tous en français. Ne pas recréer de `workflow::*` (remplacé par le
champ Status), ni de `status::*`/`priority::*`/`type::docs`/`type::chore` en anglais.

## Commandes disponibles

- `/ticket-create <type> <titre>` — crée un ticket bien formé (corps de template + labels `type::`/`agent::`/`prio::`), statut `À faire` par défaut. Ne crée pas de branche (c'est le rôle de `/ticket-start`).
- `/ticket-start <iid>` — crée la branche à partir du ticket, l'assigne, passe le **statut** à `En cours`.
- `/ticket-finish` — pousse la branche, ouvre/met à jour la MR (`Closes #<iid>`), passe le **statut** à `En revue`.
- `/branch-cleanup` — après merge d'une MR : supprime la branche locale + distante, revient sur `main` à jour, pose le **statut** `Terminé`.
- `/ticket-abandon <iid> [doublon]` — clôt un ticket sans le réaliser : statut `Abandonné` (won't-do) ou `Doublon`, raison consignée, ticket fermé.

Commandes de **supervision** (lecture seule — n'écrivent jamais : ni statut, ni MR, ni merge) :

- `/backlog [opened|all]` — vue d'ensemble du backlog groupée par **statut natif**, avec `agent::`/`prio::` et ce qui attend une revue / est prêt à merger.
- `/mr-review <mr|branche>` — synthèse d'une MR (état, aptitude au merge, pipeline, threads, diff) pour éclairer la **décision de merge humaine**. Ne merge jamais.

## Outillage requis

Ces commandes utilisent le CLI `glab` (authentifié via `glab auth login`) pour lire/écrire les issues et MR GitLab. Si `glab auth status` échoue, arrêter et demander à l'utilisateur de s'authentifier plutôt que de continuer sans.

Elles s'appuient sur le helper bash `scripts/gitlab/lib.sh`, qui factorise les appels `glab` (résolution du work-item, pose du **statut par nom** — pas de GID en dur —, slug et préfixe de branche). Sourçable (`. scripts/gitlab/lib.sh`) ou en sous-commandes (`bash scripts/gitlab/lib.sh set-status <iid> "En cours"`).

## Garde-fous (autonomie sous supervision)

- Ne jamais merger ou fermer une MR automatiquement — le merge est toujours une décision humaine.
- Ne jamais force-push une branche déjà poussée.
- Ne supprimer une branche (locale ou distante) que si `glab` confirme que sa MR est `merged`. Une fois cette confirmation acquise, la suppression locale se fait avec `git branch -D` (le projet merge en **squash**, donc `-d` refuserait la branche à tort) — jamais `-D` sur une branche dont le merge n'est pas confirmé par GitLab.
- Avant `/ticket-start`, vérifier qu'il n'y a pas de changements non commités sur la branche courante ; sinon s'arrêter et demander quoi en faire.
