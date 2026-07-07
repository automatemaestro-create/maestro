# Workflow Git & tickets — Maestro

**Version :** 0.3
Objectif : que chaque ticket GitLab soit traité de façon prévisible — même branche, même convention de commit, même cycle de vie — que ce soit un humain ou un agent Claude Code qui l'exécute.

> Le **cycle de vie** d'un ticket est porté par le **champ Status natif** de GitLab (lifecycle
> custom « Maestro », voir §3), pas par des labels. Les labels restants (`type::`, `agent::`,
> `prio::`) servent à la **catégorisation** (nature, rôle, priorité), pas au suivi d'avancement.
> Ne pas réinventer de labels `workflow::`/`status::` : le suivi passe par le champ Status.

---

## 1. Modèle de branches

**Trunk-based léger.** Pas de branche `develop`, pas de branches `release/*`. `main` est toujours stable et déployable.

```
main ──●──●──────●──────────●──●──▶
        \        \          \
         feat/12-…  fix/15-…  chore/8-…
              (courte durée de vie, supprimée après merge)
```

Règles :

1. **Jamais de commit direct sur `main`.** Tout changement passe par une branche + une Merge Request (MR).
2. **Une branche = un ticket = une MR.** Pas de branche fourre-tout multi-tickets.
3. Une branche part toujours de `main` à jour (`git pull origin main` avant `git checkout -b`).
4. Une branche est **courte** : quelques heures à quelques jours. Si un ticket prend plus longtemps, il est probablement trop gros — le redécouper.
5. La branche est supprimée (locale + distante) dès que la MR est mergée.

### Nommage des branches

```
<type>/<iid>-<slug>
```

- `iid` : l'ID du ticket GitLab (le numéro affiché dans l'issue, ex. `#12` → `12`).
- `slug` : le titre du ticket en minuscules, sans accents, mots séparés par `-`, tronqué à ~40 caractères.
- `type` : dérivé du label `type::*` du ticket (voir §3).

| Label du ticket | Préfixe de branche | Exemple |
|---|---|---|
| `type::feature` | `feat` | `feat/6-boucle-orchestration` |
| `type::bug` | `fix` | `fix/15-session-expiree-trop-tot` |
| `type::infra` | `chore` | `chore/8-journalisation-couts` |
| `type::doc` | `docs` | `docs/10-guide-contribution` |

La commande [`/ticket-start`](../.claude/commands/ticket-start.md) applique cette règle automatiquement.

---

## 2. Convention de commit

[Conventional Commits](https://www.conventionalcommits.org/), avec référence au ticket en pied de message :

```
<type>(<scope>): <description courte à l'impératif>

<corps optionnel — le pourquoi, pas le quoi>

Refs #<iid>
```

- `type` : `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `ci`, `build`, `perf`.
- `scope` : optionnel, le dossier/module concerné (`api`, `web`, `router`, `queue`…).
- Le **dernier commit de la branche** (ou la description de la MR) contient `Closes #<iid>` plutôt que `Refs #<iid>` — GitLab ferme alors le ticket automatiquement au merge.

Exemple :

```
feat(api): ajouter l'endpoint POST /login

Valide les identifiants et retourne un JWT.

Closes #12
```

**Vérification automatique.** Un hook git [`commit-msg`](../scripts/git/hooks/commit-msg) (versionné,
activé par `bash scripts/git/install-hooks.sh`) refuse localement tout message hors convention :
en-tête Conventional Commits **et** présence de `Refs #<iid>` ou `Closes #<iid>`. Exemptions :
merge / revert / `fixup!` / `squash!`. Bypass ponctuel : `git commit --no-verify`.

---

## 3. Statut natif (cycle de vie) & labels

### 3.1 Le champ Status — le cycle de vie

L'avancement d'un ticket est porté par le **champ Status natif** de GitLab (work items), via un
lifecycle custom **« Maestro »** attaché aux types *Issue* et *Task*. Six statuts :

| Statut | Catégorie | Rôle | GID (`gid://gitlab/WorkItems::Statuses::Custom::Status/`) |
|---|---|---|---|
| **À faire** | `to_do` | défaut à l'ouverture d'un ticket | `…/1020449` |
| **En cours** | `in_progress` | posé par [`/ticket-start`](../.claude/commands/ticket-start.md) | `…/1020450` |
| **En revue** | `in_progress` | posé par [`/ticket-finish`](../.claude/commands/ticket-finish.md) (MR ouverte) | `…/1020451` |
| **Terminé** | `done` | posé automatiquement au merge (fermeture via `Closes #`) | `…/1020452` |
| **Abandonné** | `canceled` | posé par [`/ticket-abandon`](../.claude/commands/ticket-abandon.md) (won't-do) | `…/1020453` |
| **Doublon** | `canceled` | posé par [`/ticket-abandon <iid> doublon`](../.claude/commands/ticket-abandon.md) | `…/1020454` |

Lifecycle : `gid://gitlab/WorkItems::Statuses::Custom::Lifecycle/1003066`.

- Le champ Status **n'est pas un label** : il s'affiche dans le panneau *Status* du ticket et
  alimente les boards « par statut », mais n'apparaît pas comme pastille dans les listes d'issues.
- Un seul statut actif à la fois. `À faire` est automatique à la création (défaut du lifecycle) —
  rien à poser à l'ouverture.
- Les commandes `/ticket-*` posent le statut via le helper partagé
  [`scripts/gitlab/lib.sh`](../scripts/gitlab/lib.sh) (`set-status <iid> <nom>`), qui résout l'ID
  du work item depuis l'iid **et dérive le GID du statut par son nom** dans le lifecycle « Maestro »
  — d'où l'absence de GID en dur dans les commandes, et la robustesse à une recréation du
  lifecycle. Sous le capot : mutation `workItemUpdate` → `statusWidget`, après résolution de l'iid
  via `{ project(fullPath:"maestro-group4345327/maestro") { workItems(iids:["<iid>"]) { nodes { id } } } }`.
- **Inspecter les GIDs manuellement** (les GIDs de la table ci-dessus n'ont plus besoin d'être
  recopiés — `lib.sh` les redécouvre — mais pour vérifier) :
  `glab api graphql -f query='{ group(fullPath:"maestro-group4345327") { lifecycles { nodes { name statuses { id name } } } } }'`.

### 3.2 Les labels — catégorisation (hors cycle de vie)

Trois familles de labels **scoped** (`::` = une seule valeur active par famille), pour trier le
backlog — **pas** pour suivre l'avancement (c'est le rôle du champ Status) :

| Famille | Valeurs | Usage |
|---|---|---|
| `type::` | `feature`, `bug`, `doc`, `infra` | Nature du ticket → détermine le préfixe de branche (§1) |
| `agent::` | `dev`, `bdd`, `devops`, `design`, `qa`, `orchestrateur` | Rôle/agent Maestro qui traite le ticket (voir [README](../README.md)) |
| `prio::` | `haute`, `moyenne`, `basse` | Urgence, pour le tri du backlog |

Ces labels sont créés (idempotent) via [`scripts/gitlab/bootstrap.sh`](../scripts/gitlab/bootstrap.sh),
et **ne sont pas touchés** par les commandes `/ticket-*` : ils relèvent du triage (à la création),
pas du cycle Git.

> **Historique.** Le suivi d'avancement reposait auparavant sur une famille de labels `workflow::`
> (`à faire`/`en cours`/`en revue`/`terminé`). Elle a été remplacée par le champ Status natif
> (migration ticket #12) : le natif apporte l'état « En revue » que les statuts système n'avaient
> pas, et évite d'avoir deux mécanismes à tenir synchronisés.

### 3.3 Dates & time tracking — renseignés automatiquement

Les champs natifs **Dates** (widget *Start and due date*) et **Time tracking** du ticket sont
remplis automatiquement le long du cycle de vie, pour donner une vue de charge et de délai sans
saisie manuelle. Comme le statut, tout passe par la mutation `workItemUpdate` via le helper
[`scripts/gitlab/lib.sh`](../scripts/gitlab/lib.sh) — pas de GID en dur.

| Champ | Quand | Comment | Commande / helper |
|---|---|---|---|
| **Date de début** | `/ticket-start` | = jour du démarrage (aujourd'hui). Conservée si déjà posée. | `lib.sh start-dates <iid>` |
| **Échéance** (due date) | `/ticket-start` | = début + délai dérivé de `prio::` : `haute` → 2 j, `moyenne` → 5 j, `basse` → 10 j (défaut `moyenne`). | `lib.sh start-dates <iid>` |
| **Temps passé** | `/ticket-finish` | **estimé automatiquement par l'agent** d'après la portée du travail (diff, commits, contexte) et loggé directement, sans confirmation. | `lib.sh log-time` (`get-time-spent` pour l'idempotence) |

- **Délais d'échéance ajustables** : surcharger `GL_DUE_DELAY_HAUTE` / `GL_DUE_DELAY_MOYENNE` /
  `GL_DUE_DELAY_BASSE` (en jours) dans l'environnement.
- **Temps passé estimé par l'agent, pas mesuré** : le temps calendaire écoulé (`elapsed-days`) vaut
  0 le jour même et n'est de toute façon pas de l'effort net. `/ticket-finish` demande donc à l'agent
  qui clôt le ticket d'**estimer l'effort d'après la portée du travail** (diff, commits, contexte de
  session) et de le logger **directement, sans confirmation** (choix explicite : pas de prompt à
  chaque clôture). Le helper `elapsed-days` reste disponible comme repère.
- **Pas d'estimation prévisionnelle (`timeEstimate`)** : seul le temps *passé* est renseigné ; le
  champ Estimation reste disponible à la main dans GitLab si besoin.
- **Idempotence** : ré-exécuter `/ticket-start` garde la date de début d'origine (ne la réinitialise
  pas à aujourd'hui) et se contente de recalculer l'échéance. `/ticket-finish` vérifie le temps déjà
  loggé (`get-time-spent`) et **n'en rajoute pas** si un cycle est déjà enregistré, pour ne pas doubler.

---

## 4. Templates GitLab

- **Issues** (`.gitlab/issue_templates/`) : `Feature.md`, `Bug.md`, `Doc.md`, `Infra.md` — un
  par valeur de `type::*`. Posent la structure attendue (contexte, critères d'acceptation) et
  appliquent le bon `type::*` via une quick action `/label`. Le statut **« À faire »** est le
  défaut du lifecycle à la création (rien à poser). Le label `agent::*` est à ajouter
  manuellement au triage (aucun template ne peut deviner quel agent est concerné).
- **Merge Request** (`.gitlab/merge_request_templates/Default.md`) : checklist de definition
  of done + rappel `Closes #`.

---

## 5. Cycle de vie d'un ticket

```
/ticket-create ──▶ À faire ──/ticket-start──▶ En cours ──/ticket-finish──▶ En revue ──(merge humain)──▶ Terminé
                      │                            │                             │
                      └────────────────/ticket-abandon────────────────┘                    /branch-cleanup
                                   (Abandonné / Doublon)
```

(les noms ci-dessus sont les **statuts** du champ Status natif, §3)

0. **`/ticket-create <type> <titre>`** — crée un ticket bien formé (corps de template, labels
   `type::`/`agent::`/`prio::`), statut `À faire` par défaut. Ne crée pas de branche.
1. **À faire** — le ticket existe (statut par défaut à la création), personne ne travaille dessus.
2. **`/ticket-start <iid>`** — crée/checkout la branche, assigne le ticket à l'exécutant, passe
   le **statut** à `En cours`.
3. Développement sur la branche (commits `Refs #<iid>`).
4. **`/ticket-finish`** — pousse la branche, ouvre (ou passe en "Ready") la MR avec
   `Closes #<iid>`, passe le **statut** à `En revue`.
5. **Revue + merge** — **toujours une action humaine** (voir garde-fous, §6). Le merge fait
   trois choses **automatiquement** : il **ferme** le ticket (via `Closes #`), **passe son
   statut à `Terminé`** (la fermeture pose le statut « done » du lifecycle) et **supprime la
   branche distante** (case « Delete source branch », pré-cochée — décochable au merge si on
   veut garder la branche).
6. **`/branch-cleanup`** — comme GitLab a déjà géré le distant et le statut (étape 5), cette
   commande ne fait plus que le **ménage local** : supprime la branche **locale** mergée et
   remet `main` à jour.

**Voie « non réalisé ».** À tout moment (depuis `À faire`, `En cours` ou `En revue`), un ticket
peut être clos sans être réalisé avec **`/ticket-abandon <iid> [doublon]`** : statut `Abandonné`
(won't-do) ou `Doublon` (catégorie `canceled`), raison consignée en commentaire, ticket fermé.

**Supervision (lecture seule).** Deux commandes n'écrivent rien et servent à piloter, en attendant
la Control Tower (Phase 1) :

- [`/backlog`](../.claude/commands/backlog.md) `[opened|all]` — vue d'ensemble du backlog groupée
  par **statut natif** (§3.1), avec `agent::`/`prio::` et la mise en avant de ce qui **attend une
  revue / est prêt à merger**. S'appuie sur `lib.sh backlog` (requête canonique du backlog).
- [`/mr-review`](../.claude/commands/mr-review.md) `<mr|branche>` — synthèse d'une MR (aptitude au
  merge, pipeline, threads bloquants, résumé du diff) pour **éclairer la décision de merge humaine**.
  Conforme au garde-fou §6 : elle **ne merge, ne ferme, ni n'approuve jamais**.

Détail des commandes : [`.claude/commands/`](../.claude/commands/).

---

## 6. Garde-fous

Cohérent avec le principe « autonomie sous supervision » du projet (voir [README](../README.md)) :

- **Aucune commande n'effectue de merge ou de fermeture de MR automatiquement.** La revue et le merge restent une décision humaine.
- **Aucun force-push** sur une branche déjà poussée.
- Une branche (locale ou distante) n'est supprimée que si **GitLab confirme que sa MR est à l'état `merged`**. C'est la garantie qui protège d'une perte de travail — plus forte que l'ancêtre git.
- Vu cette confirmation, la suppression locale utilise `git branch -D` : le projet merge en **squash**, donc `git branch -d` refuserait la branche (sa pointe n'est pas un ancêtre du commit squashé). N'employer `-D` **que** sur une branche dont le merge est confirmé par GitLab.

---

## 7. Prérequis

- [`glab`](https://gitlab.com/gitlab-org/cli) installé et authentifié : `glab auth login`.
- Vérifier l'accès : `glab issue list` doit lister les tickets du projet.
- Les commandes `/ticket-*` et `/backlog` s'appuient sur le helper
  [`scripts/gitlab/lib.sh`](../scripts/gitlab/lib.sh) (bash), qui factorise les appels glab
  (résolution work-item, statut par nom, **listing du backlog** avec statut natif, slug, préfixe de
  branche). Il est **sourçable** (`. scripts/gitlab/lib.sh`) et **exécutable en sous-commandes**
  (`bash scripts/gitlab/lib.sh set-status <iid> "En cours"`, `… backlog opened`) — pratique pour les
  futurs scripts et agents. Vérif rapide : `bash scripts/gitlab/lib.sh require`.
  - **Robustesse des lectures** : toutes les **lectures** GraphQL passent par `gl_graphql_read`, qui
    **ré-essaie sur réponse vide** (l'endpoint GraphQL de GitLab hoquette par intermittence). Réglable
    via `GL_GQL_RETRIES` (défaut 3) et `GL_GQL_RETRY_DELAY` (défaut 1 s). Les **mutations** (statut,
    dates, temps) gardent un appel direct — pas de retry, pour ne pas risquer une double application
    (ex. un timelog additif).
- **Bilan de santé** : [`bash scripts/gitlab/doctor.sh`](../scripts/gitlab/doctor.sh) (lecture seule)
  vérifie auth, labels, statuts du lifecycle résolvables par nom, et **détecte les dérives**
  (ticket « En revue » sans MR, ticket fermé au statut encore actif, branche locale mergée à
  nettoyer). Code de sortie non nul si un contrôle dur échoue (`--strict` pour échouer aussi sur les
  dérives — utile en CI).
- **Hooks git** : `bash scripts/git/install-hooks.sh` (une fois par clone) active le hook
  [`commit-msg`](../scripts/git/hooks/commit-msg) qui valide la convention de commit (§2). Pose
  `core.hooksPath` ; désactivation : `git config --unset core.hooksPath`.
- **Windows / Git Credential Manager** : si un `git push`/`pull` reste bloqué sur une demande
  d'identifiants, forcer `glab` comme credential helper le temps de la commande —
  `git -c credential.helper='!glab auth git-credential' push -u origin <branche>`.

---

## 8. Intégration continue (CI)

Un pipeline minimal existe : [`.gitlab-ci.yml`](../.gitlab-ci.yml) — stage `lint`, job `shellcheck`
qui passe [shellcheck](https://www.shellcheck.net/) (sévérité `warning`) sur les scripts
`scripts/**/*.sh` (`lib.sh`, `doctor.sh`, `bootstrap.sh`), aujourd'hui le seul code exécutable du
dépôt. Un **pipeline vert est la condition de passage `En revue` → merge**.

Périmètre volontairement restreint à la Phase 0 : **pas encore** de job de test applicatif — le
monorepo reste un squelette. Quand du code apparaît dans `apps/`, `core/` ou `packages/`, étendre
le pipeline avec les lint/test correspondants (et durcir la sévérité shellcheck vers `style` une
fois les scripts stabilisés).
