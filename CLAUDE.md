# Maestro — Instructions pour Claude Code

Monorepo en phase de cadrage (Phase 0 — POC, voir [docs/06-roadmap.md](./docs/06-roadmap.md)). Squelette de dossiers en place (`apps/`, `core/`, `agents/`, `packages/`, `infra/`), pas encore de code applicatif ni de CI.

## Règles Git obligatoires

- **Jamais de commit direct sur `main`.** Toujours travailler sur une branche de ticket.
- **Un ticket GitLab = une branche = une Merge Request.**
- Nommage de branche : `<type>/<iid>-<slug>` (ex. `feat/12-endpoint-login`). Le `type` vient du label `type::*` du ticket (`feature`→`feat`, `bug`→`fix`, `chore`→`chore`, `docs`→`docs`).
- Convention de commit : Conventional Commits + `Refs #<iid>` sur les commits intermédiaires, `Closes #<iid>` sur le commit final ou la description de la MR.
- Détail complet : [docs/10-workflow-git.md](./docs/10-workflow-git.md).

## Commandes disponibles

- `/ticket-start <iid>` — crée la branche à partir du ticket, l'assigne, passe le label en `status::in-progress`.
- `/ticket-finish` — pousse la branche, ouvre/met à jour la MR (`Closes #<iid>`), passe le label en `status::review`.
- `/branch-cleanup` — après merge d'une MR : supprime la branche locale + distante, revient sur `main` à jour.

## Outillage requis

Ces commandes utilisent le CLI `glab` (authentifié via `glab auth login`) pour lire/écrire les issues et MR GitLab. Si `glab auth status` échoue, arrêter et demander à l'utilisateur de s'authentifier plutôt que de continuer sans.

## Garde-fous (autonomie sous supervision)

- Ne jamais merger ou fermer une MR automatiquement — le merge est toujours une décision humaine.
- Ne jamais force-push une branche déjà poussée.
- Supprimer une branche locale avec `git branch -d` (jamais `-D`), et une branche distante seulement si `glab` confirme que sa MR est `merged`.
- Avant `/ticket-start`, vérifier qu'il n'y a pas de changements non commités sur la branche courante ; sinon s'arrêter et demander quoi en faire.
