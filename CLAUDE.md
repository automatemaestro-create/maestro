# Maestro — Instructions pour Claude Code

Monorepo en phase de cadrage (Phase 0 — POC, voir [docs/06-roadmap.md](./docs/06-roadmap.md)). Squelette de dossiers en place (`apps/`, `core/`, `agents/`, `packages/`, `infra/`), pas encore de code applicatif. CI minimale en place : `.gitlab-ci.yml` lint les scripts shell (shellcheck) — un pipeline vert est requis avant merge.

## Règles Git obligatoires

- **Jamais de commit direct sur `main`.** Toujours travailler sur une branche de ticket.
- **Un ticket GitLab = une branche = une Merge Request.**
- Nommage de branche : `<type>/<iid>-<slug>` (ex. `feat/6-boucle-orchestration`). Le `type` vient du label `type::*` du ticket (`feature`→`feat`, `bug`→`fix`, `infra`→`chore`, `doc`→`docs`).
- Convention de commit : Conventional Commits + `Refs #<iid>` sur les commits intermédiaires, `Closes #<iid>` sur le commit final ou la description de la MR.
- **La clôture du travail passe par les skills dédiés** : `/ticket-ship` (commit auto + push + MR + statut) quand le travail est terminé, ou `/ticket-finish` si le commit est déjà fait. Ne jamais ré-implémenter ce cycle à la main (`git commit`/`git push`/`glab mr create` directs hors de ces commandes) — les skills en sont la source unique.
- **Découpage en sous-tickets & tests différés** ([docs/10 §5.1](./docs/10-workflow-git.md)) : un ticket = ~1 session de travail. Au-delà (plusieurs couches, >3-4 critères d'acceptation) : **parent de suivi** (section `## Sous-tickets`, checklist ordonnée ; il ne se ferme que toutes cases cochées, fermeture humaine/orchestrateur) + **sous-tickets liés** (1-3 critères chacun, chacun mergeable seul sur `main` sans casser l'existant, description commençant par `Sous-ticket de #<parent>`). Les **tests sont différés au lot final « tests + doc »** ; un lot intermédiaire n'en porte que si sa logique est critique et référence « Tests différés → #<iid> ». Helpers : `lib.sh issue-link`/`parent-of`/`subtickets`.
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

- `/ticket-create <type> <titre>` — crée un ticket bien formé (corps de template + labels `type::`/`agent::`/`prio::`), statut `À faire` par défaut. **Évalue la taille** : au-delà d'~1 session, crée un **parent de suivi + sous-tickets liés** (découpage, docs/10 §5.1). Ne crée pas de branche (c'est le rôle de `/ticket-start`).
- `/ticket-start <iid>` — crée la branche à partir du ticket, l'assigne, passe le **statut** à `En cours`, pose les **dates** (début = aujourd'hui, échéance = début + délai selon `prio::`), et **purge au passage les branches locales déjà mergées** (`cleanup-merged`, même garde-fou que `/branch-cleanup`). Puis **enchaîne directement sur l'implémentation** : le résumé de cadrage n'est pas une pause d'autorisation, aucun « go » n'est attendu. Cas particuliers (docs/10 §5.1) : sur un ticket **trop gros**, propose le découpage au lieu d'enchaîner ; sur un **parent de suivi**, redirige vers le premier lot ouvert « À faire » (et s'arrête si le lot en cours attend un merge) ; sur un **sous-ticket**, vérifie que les lots précédents sont mergés.
- `/ticket-finish` — pousse la branche, ouvre/met à jour la MR (`Closes #<iid>`), **coche dans la checklist de la MR les cases qu'il a effectivement vérifiées** (conventions, tests/doc d'après le diff, pipeline verte — mise à jour idempotente, ne décoche jamais une case cochée par un humain), passe le **statut** à `En revue`, et **estime automatiquement le temps passé** (jugement de l'agent sur la portée du travail) puis le loggue, sans confirmation.
- `/ticket-ship` — **clôture zéro friction** : depuis un ticket en cours, **commite d'office** les changements en attente (message Conventional Commits généré + `Closes #<iid>`, sans confirmation) puis enchaîne `/ticket-finish`. Refuse si l'arbre est vide ou en conflit, et jamais sur `main`. Pour un **sous-ticket**, annonce le prochain lot du parent à démarrer après merge (ou que le parent est fermable si c'était le dernier) et synchronise la checklist du parent. À utiliser quand le travail est terminé et qu'on veut clore en une seule action ; `/ticket-finish` reste le choix si le commit est déjà fait.
- `/pipeline-fix [mr|branche]` — **remédiation CI** : diagnostique le dernier pipeline en échec (jobs rouges + traces via `lib.sh`), corrige en local quand c'est corrigeable (lint/test/typage), committe (`Refs #<iid>`), pousse, re-déclenche le pipeline si besoin (`glab ci run`) et suit le verdict (2 tentatives max). Échec d'infra (runner, secret, flaky) : le dit et propose au plus `glab ci retry`. Ne touche jamais au cycle de vie (ni statut, ni MR, ni merge) et jamais de commit sur `main`.
- `/branch-cleanup` — après merge d'une MR : supprime la branche locale + distante, revient sur `main` à jour, pose le **statut** `Terminé`.
- `/ticket-abandon <iid> [doublon]` — clôt un ticket sans le réaliser : statut `Abandonné` (won't-do) ou `Doublon`, raison consignée, ticket fermé.

Commandes de **supervision** (lecture seule — n'écrivent jamais : ni statut, ni MR, ni merge) :

- `/backlog [opened|all]` — vue d'ensemble du backlog groupée par **statut natif**, avec `agent::`/`prio::` et ce qui attend une revue / est prêt à merger.
- `/mr-review <mr|branche>` — synthèse d'une MR (état, aptitude au merge, pipeline, threads, diff) pour éclairer la **décision de merge humaine**. Ne merge jamais.

## Outillage requis

**Environnement Python : toujours utiliser le venv du repo (`.venv/`).** Les dépendances (`claude-agent-sdk`, `pytest`…) n'y sont installées que là — pas dans le `python` système. Lancer toute commande Python via cet interpréteur : sous Windows `.venv/Scripts/python.exe` (ex. `.venv/Scripts/python.exe -m pytest`), sous Unix `.venv/bin/python`. Avec le `python` système, la collecte des tests échoue dès l'import (`ModuleNotFoundError: No module named 'claude_agent_sdk'`).

Ces commandes utilisent le CLI `glab` (authentifié via `glab auth login`) pour lire/écrire les issues et MR GitLab. Si `glab auth status` échoue, arrêter et demander à l'utilisateur de s'authentifier plutôt que de continuer sans.

Elles s'appuient sur le helper bash `scripts/gitlab/lib.sh`, qui factorise les appels `glab` (résolution du work-item, pose du **statut par nom** — pas de GID en dur —, **dates & time tracking** via `start-dates`/`log-time`, slug, préfixe de branche, listing du backlog, **sous-tickets** via `issue-link`/`parent-of`/`subtickets`). Sourçable (`. scripts/gitlab/lib.sh`) ou en sous-commandes (`bash scripts/gitlab/lib.sh set-status <iid> "En cours"`).

Bilan de santé (lecture seule) : `bash scripts/gitlab/doctor.sh` vérifie auth/labels/lifecycle et **détecte les dérives** statut↔MR (ticket « En revue » sans MR, ticket fermé au statut encore actif, branche locale mergée à nettoyer, réglage de merge « pipeline vert requis » retombé).

Hooks git : `bash scripts/git/install-hooks.sh` (une fois par clone) active le hook `commit-msg` qui valide la convention de commit (Conventional Commits + `Refs`/`Closes #<iid>`). Bypass ponctuel : `git commit --no-verify`.

Permissions (allowlist) : [`.claude/settings.json`](./.claude/settings.json) (versionné, partagé) **autorise sans prompt** les commandes git/`glab` non destructrices du workflow (`git status`/`diff`/`add`/`commit`/`push`/`pull`, `glab issue`/`mr` view/create/update, `glab ci` list/status/trace/run/retry, `glab api graphql`, `bash scripts/gitlab/lib.sh`…) pour que `/ticket-ship` et les autres commandes s'enchaînent sans blocage. Les actions destructrices restent barrées côté permissions, en écho aux garde-fous : **`deny`** sur les force-push (`git push --force`/`-f`/`--force-with-lease`), sur `glab mr merge`/`mr close` et sur `glab ci delete` ; **`ask`** (confirmation explicite, jamais silencieux) sur `git commit --no-verify`, `git reset --hard`, `git clean`, `glab issue close`. Les surcharges personnelles vont dans `.claude/settings.local.json` (non versionné).

Provisionnement d'un nouveau projet : `bash scripts/gitlab/bootstrap.sh` (labels + réglages de merge, dont **pipeline vert requis pour merger** — `only_allow_merge_if_pipeline_succeeds`) puis `bash scripts/gitlab/bootstrap-lifecycle.sh` (lifecycle « Maestro » — idempotent, dry-run par défaut, `--apply` pour créer sur un projet vierge).

## Garde-fous (autonomie sous supervision)

- Ne jamais merger ou fermer une MR automatiquement — le merge est toujours une décision humaine.
- Ne jamais force-push une branche déjà poussée.
- Ne supprimer une branche (locale ou distante) que si `glab` confirme que sa MR est `merged`. Une fois cette confirmation acquise, la suppression locale se fait avec `git branch -D` (le projet merge en **squash**, donc `-d` refuserait la branche à tort) — jamais `-D` sur une branche dont le merge n'est pas confirmé par GitLab.
- Avant `/ticket-start`, vérifier qu'il n'y a pas de changements non commités sur la branche courante ; sinon s'arrêter et demander quoi en faire.
- `/ticket-ship` ne committe **jamais sur `main`** et refuse un arbre **vide ou en conflit** ; comme toutes les commandes, il ne merge, ne ferme, ni ne force-push jamais. Ces règles sont désormais aussi **adossées à la couche permissions** ([`.claude/settings.json`](./.claude/settings.json) : `deny` sur force-push et `glab mr merge`/`close`) — un filet de sécurité, pas un remplacement du jugement de l'agent.
