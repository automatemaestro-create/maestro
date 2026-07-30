# Workflow Git & tickets — Maestro

**Version :** 0.5
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
4. Une branche est **courte** : quelques heures à quelques jours. Si un ticket prend plus longtemps, il est probablement trop gros — le redécouper (§5.1).
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
  `/ticket-start` passe par `begin <iid>`, qui groupe ce même statut avec l'assignation et les
  dates en une seule mutation multi-widgets (§5).
- **Inspecter les GIDs manuellement** (les GIDs de la table ci-dessus n'ont plus besoin d'être
  recopiés — `lib.sh` les redécouvre — mais pour vérifier) :
  `glab api graphql -f query='{ group(fullPath:"maestro-group4345327") { lifecycles { nodes { name statuses { id name } } } } }'`.
- **Reproduire le lifecycle sur un nouveau projet** :
  [`bash scripts/gitlab/bootstrap-lifecycle.sh`](../scripts/gitlab/bootstrap-lifecycle.sh) recrée le
  lifecycle « Maestro » (6 statuts + attache aux types Issue/Task) via `lifecycleCreate` /
  `lifecycleAttachWorkItemType`. **Idempotent** (ne fait rien si « Maestro » existe déjà) et
  **dry-run par défaut** (imprime les mutations ; `--apply` pour créer, réservé à un projet vierge).

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
| **Date de début** | `/ticket-start` | = jour du démarrage (aujourd'hui). Conservée si déjà posée. | `lib.sh begin <iid>` (groupé, §5) ; unitaire : `start-dates <iid>` |
| **Échéance** (due date) | `/ticket-start` | = début + délai dérivé de `prio::` : `haute` → 2 j, `moyenne` → 5 j, `basse` → 10 j (défaut `moyenne`). | `lib.sh begin <iid>` (groupé, §5) ; unitaire : `start-dates <iid>` |
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

### 3.4 Milestone de phase — posé à la création

Chaque ticket est rattaché au **milestone de la phase de la roadmap** pendant laquelle il est
réalisé (« Phase 0 — POC », « Phase 1 — MVP »… — voir [docs/06-roadmap.md](./06-roadmap.md)), pour
que la vue par milestone reflète l'avancement réel de chaque phase.

- **À la création** : `/ticket-create` pose le milestone de la **phase courante**, résolu par
  `bash scripts/gitlab/lib.sh current-milestone` = le **milestone actif le plus ancien non soldé**
  (ayant au moins un ticket ouvert, ou aucun ticket si la phase n'est pas entamée). La règle est
  volontairement **indépendante des dates prévisionnelles** des milestones : le réel peut être en
  avance sur elles. Un milestone explicitement demandé par l'utilisateur prime ; sans milestone
  actif non soldé, l'option est simplement omise.
- **Fin de phase** : un milestone actif dont tous les tickets sont fermés est **sauté** par le
  helper ; sa **fermeture** reste une **décision humaine** (jalon go/no-go de la roadmap) — aucune
  commande ne ferme un milestone. `doctor.sh` (§7) signale les milestones actifs entièrement
  soldés à fermer, ainsi que les tickets ouverts sans milestone.

---

## 4. Templates GitLab

- **Issues** (`.gitlab/issue_templates/`) : `Feature.md`, `Bug.md`, `Doc.md`, `Infra.md` — un
  par valeur de `type::*`. Posent la structure attendue (contexte, critères d'acceptation) et
  appliquent le bon `type::*` via une quick action `/label`. Le statut **« À faire »** est le
  défaut du lifecycle à la création (rien à poser). Le label `agent::*` est à ajouter
  manuellement au triage (aucun template ne peut deviner quel agent est concerné).
- **Merge Request** (`.gitlab/merge_request_templates/Default.md`) : checklist de definition
  of done + rappel `Closes #`. La checklist est un **constat, pas un formulaire** :
  `/ticket-finish` coche lui-même les cases qu'il a **effectivement vérifiées** (conventions de
  branche/commit, tests et doc jugés d'après le diff, pipeline verte constatée via `lib.sh
  pipeline-latest`) et laisse vides les autres — notamment « Pipeline CI verte », qui est
  **normalement vide au premier passage** : la CI ne démarrant qu'avec la MR (§8), le pipeline
  naît après le constat. En cas de re-exécution, il remet la checklist à jour dans la
  description de la MR sans toucher au reste (idempotent) et **ne décoche jamais** une case déjà
  cochée (elle peut venir d'un humain). Les cases restées vides sont l'affaire du relecteur.

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
   le **statut** à `En cours`. Le chemin nominal tient en deux helpers et un bloc git (refonte
   ticket #60, pour réduire la cérémonie et le contexte réinjecté à chaque démarrage) :
   - **`lib.sh start-brief <iid>`** — tout le préflight en un appel et **une seule lecture du
     ticket** (un unique `glab issue view`, rejoué pour toutes les projections) : pré-requis
     (`glab` authentifié), arbre propre, brief compact (titre/labels/critères — l'essentiel pour
     cadrer, la description intégrale reste disponible via `glab issue view` en cas de doute),
     détection parent de suivi / sous-ticket (rang de lot, tests différés, contrôle des lots
     précédents — §5.1) et branche proposée (préfixe dérivé du label `type::` §1, slug du titre).
     Le helper est **informatif** : les avertissements sont dans sa sortie, la décision — démarrer,
     rediriger, s'arrêter, proposer un découpage — reste à l'agent.
   - **Bloc git en une commande composée** : `git checkout main && git pull origin main &&
     lib.sh cleanup-merged && git checkout -b <branche>`. Comme il met `main` à jour au passage,
     il en profite pour **purger automatiquement les branches locales déjà mergées**
     (`cleanup-merged`, même garde-fou que `/branch-cleanup` : uniquement celles dont GitLab
     confirme la MR `merged`, §6 ; s'il n'y a rien à nettoyer, aucun effet).
   - **`lib.sh begin <iid>`** — assignation (username auto-résolu via `glab api user`, parsé en
     shell pur — pas de dépendance à `jq`/`python`, et couvert par l'allowlist §7.1 pour ne pas
     déclencher de prompt), statut « En cours » (GID dérivé par nom, §3.1) et dates début/échéance
     (§3.3) en **une seule mutation** `workItemUpdate` multi-widgets. Les sous-commandes unitaires
     (`current-user`, `set-status`, `start-dates`…) restent disponibles pour les autres commandes
     et les cas hors nominal.
   Une fois le cadrage résumé, l'agent **enchaîne directement sur l'implémentation** — le résumé
   n'est pas une pause d'autorisation, aucun « go » n'est attendu.
3. Développement sur la branche (commits `Refs #<iid>`).
4. **`/ticket-finish`** — pousse la branche, ouvre (ou passe en "Ready") la MR avec
   `Closes #<iid>`, **coche dans sa checklist les cases qu'il a pu vérifier** (§4), passe le
   **statut** à `En revue`.
   - **Raccourci « zéro friction » : [`/ticket-ship`](../.claude/commands/ticket-ship.md).** Quand
     le travail est terminé mais **pas encore committé**, `/ticket-ship` enchaîne **en une seule
     action** : il **commite d'office** les changements en attente (message Conventional Commits
     généré + `Closes #<iid>`, **sans confirmation** — même parti pris que l'auto-estimation du temps,
     §3.3) puis **délègue à `/ticket-finish`** (source unique du push/MR/statut/temps ; son étape de
     commit est alors sans objet, l'arbre étant propre). Il **refuse** si l'arbre est **vide** (rien à
     committer → utiliser `/ticket-finish`) ou **en conflit**, et **jamais sur `main`**. Le hook
     `commit-msg` (§2) reste appliqué — pas de `--no-verify`. Pensé pour la **boucle d'orchestration**
     (ticket #34) : moins d'allers-retours manuels à chaque clôture.
   - **Garde-fou commun aux deux** : avant la moindre écriture, elles vérifient que le ticket visé
     est bien celui de la session — iid cohérent avec la branche courante, ticket non assigné à
     quelqu'un d'autre (`lib.sh close-guard`, §6). Sinon elles s'arrêtent en nommant le motif ;
     seule une demande explicite de l'utilisateur permet de passer outre.
   - Dans les deux cas, la clôture passe par **ces commandes et rien d'autre** : ne pas
     ré-implémenter le cycle à la main (`git commit`/`git push`/`glab mr create` ad hoc) — elles en
     sont la source unique (ticket #37).
5. **Revue + merge** — **toujours une action humaine** (voir garde-fous, §6). Le merge fait
   trois choses **automatiquement** : il **ferme** le ticket (via `Closes #`), **passe son
   statut à `Terminé`** (la fermeture pose le statut « done » du lifecycle) et **supprime la
   branche distante** (case « Delete source branch », pré-cochée — décochable au merge si on
   veut garder la branche).
6. **`/branch-cleanup`** — comme GitLab a déjà géré le distant et le statut (étape 5), cette
   commande ne fait plus que le **ménage local** : supprime la branche **locale** mergée et
   remet `main` à jour. Ce ménage est en grande partie **automatisé** : `/ticket-start` lance
   `cleanup-merged` à chaque démarrage de ticket (étape 2), donc les branches mergées disparaissent
   d'elles-mêmes au fil de l'eau. `/branch-cleanup` reste utile pour un nettoyage **à la demande**
   (ex. sans démarrer de nouveau ticket) ou pour supprimer aussi la branche distante si la case
   « Delete source branch » avait été décochée au merge.

### 5.1 Découpage en sous-tickets — besoins trop gros & tests différés

Un ticket doit tenir en **~1 session de travail** (§1, règle 4) — chaque session `/ticket-start`
reste ainsi légère en contexte. L'évaluation de taille se fait en **charge estimée**, sur la
**description intégrale** — notes techniques et références croisées comprises, pas seulement le
nombre de critères d'acceptation. Les **couches/composants distincts** touchés (moteur, backend,
UI, script, commande, doc…) sont un **signal d'alerte** qui oblige à estimer finement, **pas un
déclencheur automatique** (recalibrage ticket #63 : le découpage a un coût fixe par lot — cycle
branche/MR/pipeline/merge complet, session repartant à froid — à ne payer que s'il évite une
session qui déborde). Étalon : le **#48** n'affichait que 3 critères mais ses notes techniques
annonçaient trois couches substantielles (moteur/file #41, backend Control Tower #46, UI #47) —
il aurait dû être découpé (correctif ticket #54) ; à l'inverse, un script + sa doc tiennent en
une session et restent un **ticket unique**, au besoin avec une **checklist interne** dans sa
description (pas de parent ni de sous-tickets). Au-delà d'une session (plusieurs couches
substantielles, plus de 3-4 critères d'acceptation, plusieurs livrables indépendants), le besoin
est porté par un **ticket parent de suivi** + des **sous-tickets** (introduit par le ticket #53) :

- **Parent de suivi** — pas de branche, pas de code, pas de MR. Sa description porte l'objectif
  global et une section `## Sous-tickets` : la checklist **ordonnée** (ordre de réalisation) des
  lots, au format `- [ ] #<iid> — <titre>`. Il reste ouvert tant que toutes les cases ne sont pas
  cochées — **en particulier celle du lot tests final** — et sa fermeture est une **décision
  humaine/orchestrateur** (pas de MR → pas de `Closes #` automatique). Les cases sont cochées au
  fil de l'eau par les commandes (synchronisation idempotente : cocher les lots « Terminé »,
  jamais décocher).
- **Sous-tickets** — un lot = ~1 session, **1 à 3 critères d'acceptation**, et surtout chaque lot
  est **mergeable directement sur `main` sans casser l'existant** (code additif ou inoffensif tant
  que les lots suivants manquent). La description de chaque sous-ticket **commence par**
  `Sous-ticket de #<parent> — lot <n>/<total>.` (marqueur parsé par `lib.sh parent-of`), et le
  sous-ticket est **lié** au parent (issue link « relates to », posé par
  `lib.sh issue-link <parent> <sous-iid>`). C'est cette propriété (lots additifs, branchés depuis
  `main`) qui permet d'**enchaîner les lots sans attendre le merge** du précédent : un lot
  « En revue » (MR ouverte) ne bloque pas le suivant, seul un lot encore « À faire » ou
  « En cours » l'arrête (recalibrage ticket #63).
- **Tests différés** — les tests sont un **sous-ticket dédié**, par défaut le **lot final
  « tests + doc »**. Les lots intermédiaires n'embarquent des tests que si leur logique est
  critique, et portent la mention « Tests différés → #<iid-du-lot-tests> » — livrer un lot
  intermédiaire sans tests est donc **prévu**, pas un oubli (la case « Tests » de la checklist de
  MR reste vide, le relecteur sait pourquoi).
- **Lots parallélisables** (ticket #160) — la sérialisation des lots protège les vraies
  dépendances, mais elle est souvent **artificielle** : les lots sont déjà additifs et mergeables
  seuls sur `main`, et deux personnes se bloquent alors mutuellement pour rien. Un lot dont le
  titre dans la checklist du parent se termine par **`(parallèle)`** déclare qu'il **ne dépend pas
  des autres lots marqués qui le précèdent** :

  ```markdown
  ## Sous-tickets

  - [x] #157 — Filet CI local + contrôle doctor du runner
  - [ ] #158 — Runner CI partagé toujours en ligne (parallèle)
  - [ ] #159 — Anti-collision sur les tickets (parallèle)
  - [ ] #156 — Tests + doc du chantier
  ```

  La règle de blocage appliquée par `lib.sh start-brief` est alors : **un lot précédent non livré
  (ni « Terminé » ni « En revue ») bloque, sauf si le lot visé *et* ce lot précédent sont tous
  deux marqués.** Trois conséquences :
  - #158 et #159 sont **démarrables en même temps** par deux personnes, quel que soit l'état de
    l'autre ;
  - un lot **non marqué** reste barré par tout ce qui le précède — le lot final **« tests + doc »
    n'est donc jamais marqué**, et attend bien l'ensemble des lots ;
  - un lot non marqué en milieu de checklist (#157 ci-dessus) fait **barrière** : les lots
    parallèles qui le suivent l'attendent, ce qui permet d'exprimer un socle commun.

  Le marqueur est **facultatif** : sans lui, le comportement séquentiel d'origine est conservé.
  `lib.sh subtickets` l'expose dans une colonne `par` (`∥`/`-`) et `lib.sh startables <parent>`
  liste directement les lots « À faire » que rien ne bloque — c'est ce que `/ticket-start` affiche
  sur un parent (tous les lots démarrables, plus seulement le premier) et ce que `/ticket-ship`
  annonce après un lot.

Comportement des commandes (helpers `lib.sh` : `issue-link`, `parent-of`, `subtickets`) :

| Commande | Besoin/ticket trop gros | Ticket parent | Sous-ticket |
|---|---|---|---|
| `/ticket-create` | crée le parent **+** les sous-tickets liés (checklist ordonnée, marqueur `(parallèle)` sur les lots indépendants, lot tests en dernier) | — | — |
| `/ticket-start` | **propose le découpage** au lieu d'enchaîner (vraie pause) | affiche **tous les lots démarrables** (`lib.sh startables`) et **redirige** vers le premier (en synchronisant la checklist) ; **rien à démarrer** ⇒ parent fermable si tout est « Terminé », sinon le travail est en route (« En cours ») ou livré et on n'attend plus que des merges | vérifie que les lots **précédents** de la checklist sont livrés (« Terminé » ou « En revue » — une MR en attente de merge ne bloque pas), **hors lots marqués `(parallèle)` quand le lot visé l'est aussi** ; sinon s'arrête |
| `/ticket-ship` | — | — | **annonce les lots démarrables** dès maintenant sans attendre le merge — plusieurs si des lots sont parallèles — (ou que le parent est fermable si c'était le dernier), et coche les lots terminés dans la checklist du parent |

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

**Remédiation CI.** [`/pipeline-fix`](../.claude/commands/pipeline-fix.md) `[mr|branche]` — quand
le pipeline d'une MR est rouge : diagnostique les jobs en échec (traces synthétisées via les
helpers `lib.sh pipeline-*`), **corrige en local** quand c'est corrigeable (lint/test/typage),
committe (`Refs #<iid>`), pousse et suit le nouveau pipeline jusqu'au verdict (2 tentatives max ;
re-déclenchement `glab ci run` si le push n'a pas déclenché de pipeline). Un échec d'infrastructure
(runner, secret, flaky) est signalé tel quel — au plus un `glab ci retry`, jamais de correctif
inventé. Elle écrit des **commits**, mais jamais le cycle de vie : ni statut, ni MR, ni merge (§6),
ni commit sur `main` (voir §8).

Détail des commandes : [`.claude/commands/`](../.claude/commands/).

---

## 6. Garde-fous

Cohérent avec le principe « autonomie sous supervision » du projet (voir [README](../README.md)) :

- **Aucune commande n'effectue de merge ou de fermeture de MR automatiquement.** La revue et le merge restent une décision humaine.
- **Aucun force-push** sur une branche déjà poussée.
- **Aucun rebase automatique.** Le retard d'une branche sur `origin/main` est *signalé*, jamais
  rattrapé d'office : `bash scripts/gitlab/lib.sh behind-main [branche]` imprime le nombre de
  commits de retard, les fichiers modifiés **des deux côtés** depuis la base commune (le « conflit
  probable ») et la commande de rebase — sans rien écrire. `/ticket-finish` l'appelle **avant le
  push**, en `… behind-main || echo "verdict=$?"` : son code de retour est *lu*, jamais bloquant —
  la clôture se poursuit et le constat remonte dans le résumé final. De sorte que le conflit
  n'apparaisse plus seulement dans l'UI GitLab, après coup. Motif du refus d'automatiser : un
  rebase réécrit l'historique d'une branche déjà poussée et appellerait le force-push interdit
  ci-dessus. Codes de retour, pour un appelant scripté — `0` à jour, `3` en retard sans fichier
  commun, `4` en retard **avec** conflit probable, `2` usage, `1` état illisible. L'heuristique est
  volontairement grossière (git seul tranche vraiment) : elle vise les **fichiers aimants**
  touchés par presque tous les tickets — `CLAUDE.md`, ce document, `scripts/gitlab/lib.sh`.
- **Aucune clôture d'un ticket que la session ne traite pas.** `/ticket-finish` et `/ticket-ship`
  vérifient, **avant toute écriture** (commit, push, MR, statut, temps), que le ticket
  visé est bien celui de la session : `bash scripts/gitlab/lib.sh close-guard <iid> [branche]`.
  C'est le pendant en *sortie* de l'anti-collision d'entrée de `/ticket-start` (`issue-taken`, §5) —
  sans lui, un `/ticket-finish 158` lancé depuis `chore/163-…` faisait basculer **#158** « En
  revue », y accrochait la MR de la branche de #163 et le temps d'un travail qui
  n'était pas le sien ; via `/ticket-ship`, le commit généré portait en plus un `Closes #158` qui
  aurait fermé le ticket d'un autre au merge. Deux contrôles, de force très inégale :
  - **cohérence iid ↔ branche courante** (motif `<type>/<iid>-<slug>`, §1) — purement local, donc
    toujours disponible : c'est le contrôle **fort**, la branche étant le seul témoin fiable de ce
    que la session travaille réellement ;
  - **propriété du ticket** (assignés, via `issue-owner`) — contrôle **faible** tant que l'équipe
    partage un même compte `glab` (le bot `MaestroAgents`, cf. `GL_BOT_USERS`) : il n'attrape que
    les tickets assignés à une **personne nommée**, jamais deux sessions du même compte.

  Codes de retour : `0` cohérent, `3` la branche porte un **autre** ticket, `4` ticket assigné à
  quelqu'un d'autre, `5` branche sans iid (`main`, nom hors convention) — cohérence invérifiable,
  `1` ticket illisible (verdict **partiel** : le contrôle local est passé, on signale et on
  poursuit), `2` usage ; priorité `3 > 4 > 5`. Une lecture dégradée ne vaut **pas** « ticket
  libre » : `issue-owner` échoue franchement quand le projet est illisible (`"project":null`)
  plutôt que de rendre des champs vides, que l'appelant lirait comme un feu vert. Le helper reste
  **consultatif** — il n'écrit rien, ce sont les commandes qui refusent — et le refus est
  **franchissable sur demande explicite** de l'utilisateur (reprise assumée d'un ticket laissé en
  plan par quelqu'un qui a lâché le sujet), **jamais en silence** : il est alors rappelé dans le
  résumé de clôture.
- Une branche (locale ou distante) n'est supprimée que si **GitLab confirme que sa MR est à l'état `merged`**. C'est la garantie qui protège d'une perte de travail — plus forte que l'ancêtre git.
- Vu cette confirmation, la suppression locale utilise `git branch -D` : le projet merge en **squash**, donc `git branch -d` refuserait la branche (sa pointe n'est pas un ancêtre du commit squashé). N'employer `-D` **que** sur une branche dont le merge est confirmé par GitLab.
- **La suppression de la branche source est portée par la MR elle-même.** `/ticket-finish` crée la
  MR avec `--remove-source-branch` : la case « Supprimer la branche source » est cochée d'office,
  et c'est **GitLab** qui supprime la branche **distante** au merge. Le drapeau est posé sur la MR
  plutôt que hérité du seul défaut projet `remove_source_branch_after_merge=true` — lui aussi
  provisionné par [`bootstrap.sh`](../scripts/gitlab/bootstrap.sh), mais en *best-effort* (l'échec
  du PUT est avalé), donc insuffisant comme unique garantie ; sa dérive est désormais signalée par
  [`doctor.sh`](../scripts/gitlab/doctor.sh) (§6). Côté local, rien ne change pour
  `/branch-cleanup` : il supprime la branche **locale** et tolère une branche distante déjà
  supprimée.
- **La revue est *best-effort*, pas bloquante.** À plusieurs, personne ne sait spontanément ce qui
  attend qui : le projet garde donc `approvals_before_merge=0` (une approbation obligatoire
  recréerait une dépendance entre personnes, et le merge resterait de toute façon humain) et joue
  sur la **visibilité** — arbitrage du chantier #155.
  - **Aucun relecteur n'est posé automatiquement** (#196). `/ticket-finish` l'a fait un temps
    (#161) ; ce n'est plus le cas : désigner un relecteur attribue une MR à quelqu'un qui ne l'a
    pas demandé, alors que la file de revue donne déjà le signal « cette MR attend quelqu'un ». La
    **visibilité** suffit donc, et la désignation redevient un **geste humain explicite**.
  - Le helper reste **outillé pour cette pose manuelle** :
    `bash scripts/gitlab/lib.sh set-reviewer [mr|branche] [username]` choisit, à défaut d'un nom
    donné, un **membre humain du projet distinct de l'auteur**, résolu via l'API des membres —
    **aucun nom en dur** ; les comptes d'automatisation sont écartés par la variable `GL_BOT_USERS`
    (défaut `MaestroAgents` : ce compte est un utilisateur GitLab ordinaire, `User.bot` y vaut
    `false`, l'API seule ne suffit donc pas à l'exclure). La désignation **tourne** entre les
    candidats (graine = iid de la MR : même MR → même relecteur, MR différentes → charge répartie)
    et elle est **idempotente** : un relecteur déjà posé n'est **jamais** remplacé. Sur un projet à
    une seule personne, il n'y a pas de candidat et le helper échoue proprement (code `1`). Aucune
    commande du workflow ne l'appelle — c'est un outil, plus une étape.
  - `/backlog` affiche la **file de revue** en tête (`bash scripts/gitlab/lib.sh review-queue`) :
    MR ouvertes **la plus ancienne d'abord**, avec `age_j` (l'ancienneté, c'est elle qui déclenche
    la relecture), l'état `draft`/`ready`, le statut du pipeline, l'auteur et le relecteur s'il en
    a été posé un à la main (colonne à « - » sinon, cas désormais normal). C'est **elle seule** qui
    porte le signal de revue.
- **Une MR au pipeline rouge n'est pas mergeable.** Le réglage projet
  `only_allow_merge_if_pipeline_succeeds=true` (complété par `allow_merge_on_skipped_pipeline=false`)
  fait appliquer par **GitLab lui-même** la règle « pipeline vert avant merge » (§8) : le bouton de
  merge reste grisé tant que le pipeline échoue ou est sauté. Provisionné par
  [`bootstrap.sh`](../scripts/gitlab/bootstrap.sh) (PUT idempotent), surveillé par
  [`doctor.sh`](../scripts/gitlab/doctor.sh) (dérive signalée si le réglage retombe).

**Adossement à la couche permissions (Claude Code).** Ces garde-fous ne reposent pas que sur les
consignes des commandes : ils sont aussi **filtrés par l'allowlist** [`.claude/settings.json`](../.claude/settings.json)
(§7.1). Elle **autorise sans prompt** les commandes git/`glab` **non destructrices** du workflow (pour
que `/ticket-ship` s'enchaîne sans blocage), pose en **`deny`** les actions que les garde-fous
interdisent (**force-push** `git push --force`/`-f`/`--force-with-lease`, **`glab mr merge`**,
**`glab mr close`**) et en **`ask`** (confirmation explicite, jamais silencieuse) les actions
sensibles hors chemin nominal (`git commit --no-verify`, `git reset --hard`, `git clean`,
`glab issue close`). C'est un **filet de sécurité complémentaire** au jugement de l'agent, pas un
remplacement : le matching est par préfixe (une variante d'ordre de drapeaux peut y échapper), donc
la consigne « jamais de force-push / merge / close auto » reste la règle première.

---

## 7. Prérequis

> **Tout ce qui suit est monté par une commande** sur un clone frais :
> [`bash scripts/setup.sh`](../scripts/setup.sh) — ou [`/setup`](../.claude/commands/setup.md) en
> session Claude Code. Il installe `glab`, active le hook `commit-msg`, s'authentifie depuis le
> `GITLAB_TOKEN` du `.env` et crée le runner CI de la machine (§8). Ce qui est détaillé ici est le
> **quoi et le pourquoi**, plus une check-list à dérouler à la main.

> **Un humain qui arrive lit [`CONTRIBUTING.md`](../CONTRIBUTING.md)**, pas ce document. Ce
> fichier-ci est exhaustif (et `CLAUDE.md` est écrit pour l'agent) : `CONTRIBUTING.md` tient en une
> page le chemin `setup.sh` → ticket libre via `/backlog` → `/ticket-start` → `/ticket-ship`, dit
> qui relit et qui merge, et renvoie ici pour le détail. C'est **le seul point d'entrée à
> connaître** ; tout ce qu'il affirme est une redite volontaire de ce document, jamais une règle
> nouvelle.

- [`glab`](https://gitlab.com/gitlab-org/cli) installé et authentifié : `glab auth login`
  (automatique via `scripts/setup.sh` si le `.env` porte un `GITLAB_TOKEN` — le jeton passe par
  stdin, jamais par une ligne de commande).
- Vérifier l'accès : `glab issue list` doit lister les tickets du projet.
- Les commandes `/ticket-*` et `/backlog` s'appuient sur le helper
  [`scripts/gitlab/lib.sh`](../scripts/gitlab/lib.sh) (bash), qui factorise les appels glab
  (résolution work-item, statut par nom, **listing du backlog** avec statut natif, slug, préfixe de
  branche, **sous-tickets** — `issue-link`/`parent-of`/`subtickets`, §5.1 —, **démarrage de
  ticket** — `start-brief`/`begin`, §5 —, **retard sur `origin/main`** — `behind-main`, §6 — et
  **garde-fou de clôture** — `close-guard`/`branch-iid`, §6).
  Il est **sourçable**
  (`. scripts/gitlab/lib.sh`) et **exécutable en sous-commandes**
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
  nettoyer, réglages de merge « pipeline vert » ou « suppression de la branche source » retombés — §6). Code de sortie non nul si un contrôle dur échoue (`--strict` pour échouer aussi sur les
  dérives — utile en CI).
- **Hooks git** : posés par `scripts/setup.sh` (étape `hooks`), qui délègue à
  `bash scripts/git/install-hooks.sh` — lançable seul, une fois par clone. Active le hook
  [`commit-msg`](../scripts/git/hooks/commit-msg) qui valide la convention de commit (§2). Pose
  `core.hooksPath` ; désactivation : `git config --unset core.hooksPath`.
- **Windows / Git Credential Manager** : si un `git push`/`pull` reste bloqué sur une demande
  d'identifiants, forcer `glab` comme credential helper le temps de la commande —
  `git -c credential.helper='!glab auth git-credential' push -u origin <branche>`.

### 7.1 Permissions Claude Code (allowlist)

Pour que les commandes du workflow — en particulier [`/ticket-ship`](../.claude/commands/ticket-ship.md) —
s'enchaînent **sans prompt de permission répété**, le dépôt versionne une allowlist
[`.claude/settings.json`](../.claude/settings.json) (partagée par toute l'équipe ; les surcharges
personnelles vont dans `.claude/settings.local.json`, non versionné).

| Catégorie | Effet | Contenu (préfixes de commande) |
|---|---|---|
| **`allow`** | exécuté sans prompt | Lectures/écritures **non destructrices** du workflow : `git status`/`diff`/`log`/`show`/`branch`/`checkout`/`fetch`/`pull`/`add`/`commit`/`push`/`rev-parse`/`ls-files` ; `glab` `auth status`, `api user`/`graphql`, `issue` view/list/update/note, `mr` view/list/create/update ; `bash scripts/gitlab/lib.sh`, `… doctor.sh`, `… git/install-hooks.sh`. |
| **`ask`** | confirmation explicite (jamais silencieux) | `git commit --no-verify` (le bypass du hook reste possible mais **volontaire**), `git reset --hard`, `git clean`, `glab issue close`. |
| **`deny`** | bloqué | Ce que les garde-fous (§6) interdisent : `git push --force` / `-f` / `--force-with-lease`, `glab mr merge`, `glab mr close`. |

- **`git commit`/`push` en `allow`** couvrent le chemin nominal de `/ticket-ship` et `/ticket-finish` ;
  le hook `commit-msg` (§2) s'applique toujours (le commit passe par lui), et le push non forcé est
  la seule forme autorisée — les variantes `--force` sont en `deny`.
- **Précédence** : `deny` > `ask` > `allow`. Le matching est **par préfixe** : robuste pour les formes
  canoniques, mais une variante à l'ordre de drapeaux inhabituel peut y échapper — d'où le rappel du §6
  que la **consigne** (jamais de force-push/merge/close auto) reste la garantie première, l'allowlist
  n'étant qu'un filet.
- **Régénérer / auditer** : le fichier est du JSON simple ; toute évolution de l'allowlist est une
  **décision humaine** (un agent ne s'auto-accorde pas de permissions — l'écriture de ce fichier par
  Claude Code est d'ailleurs interceptée et demande validation).
- **Réglages machine (`.claude/settings.local.json`, non versionné)** : rien ne les annonçait, d'où
  le gabarit versionné [`.claude/settings.local.example.json`](../.claude/settings.local.example.json)
  — les clés attendues avec des **valeurs neutres**, et **aucun secret** (un jeton n'a pas sa place
  dans un fichier suivi ; `CLAUDE_CODE_OAUTH_TOKEN` est recopié depuis le `.env` par `setup.sh`, il
  ne s'écrit jamais à la main ici). Clés couvertes : `env.MAESTRO_CHROME_PROFILE` (profil du
  navigateur piloté par `chrome-maestro`), `env.MAESTRO_RUNNER_ID` (runner CI de cette machine,
  §8.1), `enabledMcpjsonServers` (approbation des serveurs de `.mcp.json`) et `permissions.allow`
  (surcharges personnelles). C'est une **référence à lire, pas un fichier à recopier** : le vrai
  `settings.local.json` est écrit et fusionné clé par clé par l'étape `mcp` de `setup.sh`, qui
  renvoie vers le gabarit en `--check`. Les worktrees y ajoutent `MAESTRO_PORT_API`/
  `MAESTRO_PORT_UI`, posés par `worktree.sh` (§9) — pas à la main.

### 7.2 Traçabilité des demandes — hook `UserPromptSubmit`

**Principe.** Aucune demande de travail ne doit rester hors de GitLab : chaque prompt utilisateur
se consigne dans un ticket, **sans confirmation** (même parti pris d'autonomie que le reste du
workflow). Un hook Claude Code [`UserPromptSubmit`](https://docs.claude.com/en/docs/claude-code/hooks)
injecte le rappel à chaque prompt.

**Règle appliquée par l'agent** — à réception d'un prompt :

| Cas | Action |
|---|---|
| La demande **relève du ticket en cours ou d'un ticket existant** du backlog | **Mettre à jour ce ticket** : commentaire (`glab issue note -m`), et **description** si le périmètre change. |
| La demande **est nouvelle** (aucun ticket ne la couvre) | **Créer un ticket** via [`/ticket-create`](../.claude/commands/ticket-create.md) (labels `type::`/`agent::`/`prio::`, milestone courant §3.4). |
| **Exceptions — rien à tracer** | Les commandes `/ticket-*` elles-mêmes (déjà tracées par le workflow) et les **échanges purement conversationnels** sans travail demandé. |

**Mécanisme (versionné, fonctionne sur tout clone).** Deux fichiers portés par le dépôt :

- [`.claude/settings.json`](../.claude/settings.json) déclare le hook `UserPromptSubmit` (bloc
  `hooks`) : il exécute `cat "$CLAUDE_PROJECT_DIR/.claude/hooks/maestro-demande-ticket.json"`.
  `$CLAUDE_PROJECT_DIR` est fourni par Claude Code (racine du dépôt) — le chemin est donc **relatif
  au clone**, sans dépendance à un emplacement machine.
- [`.claude/hooks/maestro-demande-ticket.json`](../.claude/hooks/maestro-demande-ticket.json) est le
  **payload** : un JSON `UserPromptSubmit` (`suppressOutput: true` + `hookSpecificOutput.additionalContext`)
  dont le `cat` sur stdout devient le rappel injecté dans le contexte de l'agent.

Comme l'allowlist (§7.1), ce hook est **partagé par l'équipe** ; toute surcharge personnelle (ex.
désactiver le rappel en local) passe par `.claude/settings.local.json`, non versionné.

### 7.3 Secrets partagés — `[perso]` / `[partagé]` et `env-pull.sh`

**Le problème.** À plusieurs, la moitié d'un `.env` n'appartient à personne en particulier : les
clés Langfuse, le bot Slack et les endpoints sont les mêmes pour toute l'équipe. Les faire circuler
à la demande (« tu peux me renvoyer le token ? ») coûte un aller-retour à chaque arrivant et laisse
des secrets traîner dans les canaux de discussion (#162).

**L'arbitrage.** Chaque clé de [`.env.example`](../.env.example) porte un **marqueur**, sur la
ligne de commentaire qui l'introduit — et qui vaut **jusqu'au marqueur suivant** :

| Marqueur | Ce que c'est | Où vit la valeur |
|---|---|---|
| `# [perso]` | jeton nominatif, chemin de machine, service local | **chez vous** — le seul geste manuel qui reste sur un clone frais |
| `# [partagé]` | secret du projet, endpoint, identifiants d'espace de travail | **variables CI/CD du projet GitLab** (masquées, réservées aux membres) |

```bash
bash scripts/env-pull.sh              # complète le .env avec les clés partagées qui manquent
bash scripts/env-pull.sh --check      # diagnostic seul — n'écrit rien, ne lit aucune valeur
bash scripts/env-pull.sh --manquantes # juste les noms à compléter (ce qu'interroge setup.sh)
```

Quatre promesses, que [`tests/test_env_pull.py`](../tests/test_env_pull.py) épingle :

- **le gabarit fait foi** — la liste des clés partagées est *lue* dans `.env.example`, jamais
  recopiée dans le script : annoter une nouvelle clé là-bas suffit ;
- **non destructif** — une clé déjà renseignée n'est **jamais** écrasée, même si la variable CI/CD
  dit autre chose, et les clés `[perso]` ne sont pas même regardées ;
- **aucune valeur imprimée** — la sortie ne porte que des *noms* de clés et des comptes ; les
  valeurs ne traversent ni l'affichage ni un argument de commande (lisible par tout processus de la
  machine), seulement des fichiers temporaires en 0600 effacés en sortie ;
- **franc sur ce qu'il ne peut pas** — une clé partagée absente des variables du projet est dite
  comme telle, avec la commande qui la publie. Rien n'est deviné.

**Publier une valeur partagée** est un geste de **mainteneur**, une fois par clé :

```bash
glab variable set LANGFUSE_SECRET_KEY --masked < valeur.txt
```

---

## 8. Intégration continue (CI)

Le pipeline [`.gitlab-ci.yml`](../.gitlab-ci.yml) a deux étages : `lint` — `shellcheck`
(sévérité `warning`, scripts `scripts/**/*.sh`) et `python-lint` (ruff) — puis `test` — `pytest`
(suite du dépôt, avec **couverture** pytest-cov : taux remonté dans GitLab via la clé `coverage:`
du job, échec sous `--cov-fail-under=90`), `mypy` (typage strict de `maestro/`) et `web-build`
(l'UI Control Tower). Les jobs Python partagent un **cache pip** (clé sur `pyproject.toml`) qui
accélère le `before_script` d'un run à l'autre. Un **pipeline vert est la condition de passage
`En revue` → merge**.

Le **front** (`apps/web`) a son propre job, `web-build`, qui enchaîne `npm run lint` (ESLint),
`npm test` (la suite **Vitest** de l'interface, #124) puis `npm run build` (`next build`, qui
vérifie aussi le typage TypeScript). Les trois tiennent dans **un seul** job parce que
l'installation des dépendances (`npm ci`) pèse bien plus que les contrôles eux-mêmes : la refaire
deux fois de plus n'apprendrait rien et occuperait d'autant le runner de l'équipe (§8.1) ; l'ordre
va du plus rapide au plus lent, pour que le verdict tombe tôt quand il est rouge. Le job ne se
déclenche que si `apps/web/**` (ou `.gitlab-ci.yml`) change — un pipeline purement Python reste
rapide — et son cache npm porte sur le lockfile versionné.

Les **scripts shell** ne sont pas seulement lintés : le parcours de mise en route
([`scripts/setup.sh`](../scripts/setup.sh), §7) a sa propre suite pytest
[`tests/test_setup.py`](../tests/test_setup.py) (#147), qui monte un **dépôt jetable** dans un
répertoire temporaire et y lance le script pour vérifier ses invariants — `--check` n'écrit rien,
deuxième passage entièrement en `DÉJÀ FAIT`, `.env` et `settings.local.json` jamais écrasés (le
second est fusionné clé par clé), rapport complet et code de sortie non nul sur échec dur. Les
étapes réseau / Docker (`venv`, `web`, `runner`, `infra`, `verif`) y sont **neutralisées** par
`--skip` : c'est la décision du script qui est testée, jamais l'installation elle-même — la suite
tourne donc en CI sans démon Docker ni accès réseau.

**Aucun test n'a besoin d'un backend, et [`tests/conftest.py`](../tests/conftest.py) l'impose**
(#195) — le pendant Python du réseau débranché d'office côté UI
([`apps/web/tests/setup.ts`](../apps/web/tests/setup.ts)). Le garde-fou vide les **clés Langfuse**
de l'environnement du processus de test et fait **échouer le test** qui laisse un
`LangfuseExportHandler` sur le logger global `maestro.trace`. Les deux moitiés comptent : sans la
première, un poste dont l'intégration Langfuse est opérationnelle joue la même suite pour le même
verdict en **17 min 51 s au lieu de 7 min 08 s** (`activer_export_langfuse()` est appelée par
chaque point d'entrée, le handler survit au test qui l'a déclenché, et chaque ligne journalisée
ensuite part en POST **synchrone** de 10 s de plafond — vers le vrai projet Langfuse, qu'elle
pollue au passage) ; sans la seconde, la prochaine fuite du même genre repasserait inaperçue,
puisqu'elle ne se manifeste que par de la lenteur. C'est aussi ce qui rend le filet CI local
([`scripts/ci/local.sh`](../scripts/ci/local.sh), ci-dessous) comparable au job qu'il prédit : le
runner, lui, n'a jamais eu de clés Langfuse dans son environnement.

**Quand un pipeline se déclenche ?** **Uniquement sur les Merge Requests** (#165). Le bloc
`workflow: rules:` de [`.gitlab-ci.yml`](../.gitlab-ci.yml) ne laisse passer que
`$CI_PIPELINE_SOURCE == "merge_request_event"` — la **création** d'une MR, puis chaque **push sur
sa branche source** tant qu'elle est ouverte — et les déclenchements **manuels** (`web`, le bouton
« Run pipeline » ; `api`, `glab ci run -b <branche>`, le repli de `/pipeline-fix`). Tout le reste
tombe en `when: never` : un push sur une branche **sans MR**, le push sur **`main` après le
merge**, les tags. Avant ces règles, une même branche payait **trois** pipelines — pendant le
développement, à la clôture du ticket, puis sur `main` une fois mergée — pour un seul verdict
réellement lu, celui qui conditionne le merge ; sur le runner unique de l'équipe (§8.1), c'est
autant d'attente pour les autres. Trois conséquences pratiques :

- **Vérifier son travail avant la MR est un geste local** :
  [`scripts/ci/local.sh`](../scripts/ci/local.sh) rejoue les mêmes jobs sur le poste (#157) — c'est
  lui qui remplace les pipelines de branche, et il ne dépend d'aucun runner. Depuis un **worktree**,
  il teste bien le code d'ici : voir §9 pour le piège d'import que cela suppose d'éviter (#194).
- **Le pipeline d'une MR est « détaché »** : sa ref est `refs/merge-requests/<iid>/head`, pas le
  nom de la branche. `glab ci status` / `glab ci view <branche>` ne le voient donc **pas** ;
  `lib.sh pipeline-latest <branche>` si — il se rabat sur les pipelines de la MR quand la ref n'en
  porte aucun —, et c'est lui qu'utilisent `/pipeline-fix`, `/ticket-finish` et `/mr-review`. La
  file de revue (`lib.sh review-queue`) lit `headPipeline` en GraphQL : elle n'est pas concernée.
- **La case « Pipeline CI verte » de la MR est vide au premier passage**, et c'est normal :
  `/ticket-finish` pousse **puis** ouvre la MR, donc le pipeline naît *après* le constat (§6).

Le garde-fou de merge est inchangé : `only_allow_merge_if_pipeline_succeeds` regarde le pipeline
de **tête de la MR**, qui existe toujours — une MR sans pipeline vert reste non mergeable.

**Où tournent les pipelines ?** Sur les **runners de projet** du dépôt (exécuteur Docker), quel que
soit le pipeline. Les **runners partagés**
GitLab sont **désactivés** au niveau projet (`shared_runners_enabled=false`, posé par
[`bootstrap.sh`](../scripts/gitlab/bootstrap.sh)) : leur quota de minutes CI étant durablement
épuisé, un job non-taggé qui y atterrissait retombait en `ci_quota_exceeded` (jobs « not
started »). Aucun `tags:` n'est nécessaire dans [`.gitlab-ci.yml`](../.gitlab-ci.yml) — les runners
de projet acceptent les jobs non-taggés (`run_untagged`) et sont l'unique cible. **Contrepartie
opérationnelle** : au moins un runner doit être **en ligne** (Docker démarré + conteneur du runner
actif) ; sinon les jobs restent **`pending`** (et non plus `ci_quota_exceeded`), et le merge — qui
exige un pipeline vert — reste bloqué.

### 8.1 Deux types de runner — partagé (permanent) et locaux (secours)

À plusieurs sur des clones distincts (#155), un unique runner sur le poste d'une personne fait
d'elle un **point de blocage** : personne ne peut merger quand sa machine est éteinte. Le projet
distingue donc deux rôles, avec la **même** mécanique et le même script (#158) :

| | **Partagé** — `runner-partage-<machine>` | **Local** — `maestro-<machine>` |
|---|---|---|
| Où | une machine qui **reste allumée** (serveur, poste dédié) | le poste de chacun |
| Rôle | sert la CI de l'équipe en permanence | **secours** quand le partagé est indisponible |
| Montage | `bash scripts/gitlab/setup-runner.sh --partage` | `bash scripts/gitlab/setup-runner.sh` (via `/setup`) |
| Jobs simultanés | `concurrent ≥ 2` — deux personnes ne font pas la queue | valeur par défaut du runner |

Les deux acceptent les jobs **non-taggés** : n'importe lequel prend n'importe quel job, rien à
déclarer dans [`.gitlab-ci.yml`](../.gitlab-ci.yml). C'est ce qui rend le secours automatique — si
le runner permanent tombe, le runner local d'un poste allumé prend le relais sans changer une
ligne de configuration.

**Monter le runner partagé** (une fois, sur la machine qui reste en ligne) :

```bash
bash scripts/gitlab/setup-runner.sh --partage
```

Prérequis : **Docker**, un jeton `glab` portant la portée **`create_runner`**, et — le seul qui ne
se vérifie pas par script — une **machine qui ne s'éteint pas** (veille comprise : un runner
endormi est un runner hors ligne). Le conteneur est monté en `--restart always`, il revient donc
seul après un redémarrage.

Le script **refuse de détourner** un runner local existant : si la machine héberge déjà un
conteneur `gitlab-runner` enregistré sous `maestro-<machine>`, il s'arrête en indiquant comment
monter le partagé **à côté**, dans un conteneur et un volume distincts :

```bash
MAESTRO_RUNNER_CONTAINER=gitlab-runner-partage \
MAESTRO_RUNNER_VOLUME=gitlab-runner-partage-config \
bash scripts/gitlab/setup-runner.sh --partage
```

`concurrent` est un réglage **global** du démon `gitlab-runner`, absent des options de
`gitlab-runner register` : le script l'écrit dans `config.toml` puis redémarre le conteneur. C'est
idempotent — une valeur déjà suffisante n'est pas touchée (surcharge : `MAESTRO_RUNNER_CONCURRENT`).

**Créer le runner d'une nouvelle machine** est le rôle de
[`scripts/gitlab/setup-runner.sh`](../scripts/gitlab/setup-runner.sh) (#146), appelé par l'étape
`runner` de [`scripts/setup.sh`](../scripts/setup.sh) — donc par la commande
[`/setup`](../.claude/commands/setup.md). Il installe et démarre Docker si besoin, puis, si aucun
conteneur `gitlab-runner` n'existe sur la machine : crée le runner côté GitLab
(`POST /user/runners`, type projet, jobs non-taggés), monte le conteneur, l'enregistre, et attend
son passage `online`. Il est **idempotent** (un runner déjà monté ⇒ simple remise en ligne) et
n'expose jamais le **jeton** d'enregistrement : il transite par l'environnement du conteneur, pas
par une ligne de commande. Le **jeton `glab` doit porter la portée `create_runner`** pour que la
création aboutisse.

L'**id du runner** est propre à chaque machine : il est persisté dans le bloc `env` de
`.claude/settings.local.json` (non versionné). `ensure-runner.sh` le résout dans cet ordre —
variable d'environnement `MAESTRO_RUNNER_ID`, puis ce fichier, puis **découverte par l'API** (les
runners de projet du dépôt ; s'il y en a plusieurs, celui dont la description porte le nom de la
machine — le motif `maestro-<machine>` du runner local prime, les deux descriptions portant le nom
de la machine sur l'hôte du runner partagé). Plus aucun id n'est codé en dur : l'ancien défaut
`54385112`, propre au poste d'origine, était faux sur tout autre clone.

La mise en ligne est **automatisée** par le helper idempotent
[`scripts/gitlab/ensure-runner.sh`](../scripts/gitlab/ensure-runner.sh). Comme n'importe quel
runner de projet prend n'importe quel job, il est **no-op dès qu'au moins un est `online`** — le
runner partagé qui tient la CI dispense de réveiller Docker sur un portable. Ce n'est que si
**aucun** n'est en ligne qu'il monte celui de **cette** machine : démarrage de Docker Desktop (si
le démon est éteint), puis du conteneur `gitlab-runner`, et polling jusqu'à `online`.
`--strict` (ou `MAESTRO_RUNNER_STRICT=1`) restreint au runner de la machine courante en ignorant
les autres — c'est ce qu'utilise la mise en route, qui rend compte du poste et non de l'état global
de la CI. Il **échoue proprement** (code non nul + message) sans
jamais lever d'exception bloquante, et il est **paramétrable par variables d'environnement**
(`MAESTRO_RUNNER_ID`, `MAESTRO_RUNNER_CONTAINER`, `MAESTRO_DOCKER_DESKTOP`, les fenêtres de
polling — voir l'en-tête du script). Il est **câblé dans les skills de clôture avant le push /
avant l'attente du verdict** — [`/ticket-finish`](../.claude/commands/ticket-finish.md) et
[`/pipeline-fix`](../.claude/commands/pipeline-fix.md), donc `/ticket-ship` par ricochet —, appelé
en `bash scripts/gitlab/ensure-runner.sh || …` : **son échec n'interrompt pas la clôture**, il est
seulement signalé. Un **hook global** (sur tout `git push`) a été écarté : il se déclencherait sur
des push sans rapport et bloquerait le push le temps du démarrage de Docker. S'assurer que le
runner est en ligne **en amont de chaque MR** reste donc intégré au flux de clôture
(`/ticket-finish`, `/ticket-ship`, `/pipeline-fix`), désormais sans geste manuel — et d'autant
plus au bon endroit depuis #165, la MR étant le **seul** moment où un pipeline démarre.

### 8.2 Ménage des conteneurs de jobs sur la machine du runner

L'exécuteur `docker` du runner crée **deux conteneurs éphémères par job** —
`runner-<jeton>-project-<id>-concurrent-<n>-<hash>-predefined` (clone, cache, artefacts) et
`…-build` (le script du job), plus un `…-svc-<n>` par service — et les supprime en fin de job.
**Sauf quand il est tué en cours de route** (Docker Desktop arrêté, poste éteint, job annulé) : le
ménage n'a alors jamais lieu et les conteneurs restent `Exited` indéfiniment. Le constat qui a
motivé #166 : 8 résidus pour ~1,5 Go sur un poste, issus de deux pipelines interrompus à une
semaine d'intervalle, plus 16 volumes de cache répartis sur 4 enregistrements successifs du
runner. Rien ne le signale, et ça grossit en silence sur la machine de chaque personne qui héberge
un runner.

[`scripts/gitlab/clean-runner-containers.sh`](../scripts/gitlab/clean-runner-containers.sh) s'en
charge, **câblé à côté de `ensure-runner.sh`** dans [`/ticket-finish`](../.claude/commands/ticket-finish.md)
et [`/pipeline-fix`](../.claude/commands/pipeline-fix.md) (donc `/ticket-ship` par ricochet) :
préparer la CI avant la MR est aussi le bon moment pour ramasser les restes du pipeline précédent.
Appelé en `|| …`, **son échec n'interrompt jamais la clôture**, et il est **silencieux quand il n'y
a rien à faire**. Contrairement à `ensure-runner.sh`, il n'est **pas** court-circuité quand le
runner partagé tient la CI : le ménage est local à la machine.

**Jamais `docker container prune` ni `docker system prune`.** Sur un poste de développement, ils
détruiraient les conteneurs arrêtés des **autres** projets (bases de données, n8n, stacks compose
au repos). La suppression se fait exclusivement par `docker rm`, conteneur par conteneur, sur une
liste filtrée par nom — et **trois garde-fous** valident chaque candidat, parce qu'un conteneur
`Exited` n'est pas forcément un déchet :

1. **État `exited`** — un job en cours d'exécution est `running`, écarté d'office.
2. **Job encore vivant** — le conteneur `-predefined` **sort (code 0) pendant que le job continue**
   dans `-build` ; le supprimer casserait l'envoi des artefacts. Les conteneurs sont donc regroupés
   par job (le préfixe jusqu'au hash) et **tout le groupe est épargné** dès qu'un de ses conteneurs
   tourne encore. C'est le garde-fou qui compte vraiment.
3. **Ancienneté** — au cas où le second conteneur du job ne serait pas encore créé (quelques
   secondes entre les étapes), rien n'est effacé avant `MAESTRO_CLEAN_AGE_MIN` minutes (10 par
   défaut). Une date de fin illisible vaut « trop récent » : on s'abstient.

Les **volumes de cache** (`runner-<hash>-cache-…`, un jeu par enregistrement du runner) ne sont
**jamais supprimés automatiquement** — ceux de l'enregistrement courant accélèrent les jobs. Le
script se contente de les **signaler** quand il en trouve plusieurs jeux ; leur purge est un geste
explicite, `--volumes`, qui conserve le jeu le plus récent (l'enregistrement courant) et s'appuie
sur le refus natif de `docker volume rm` pour un volume monté. `--check` donne le diagnostic sans
rien supprimer. Côté **permissions** ([`.claude/settings.json`](../.claude/settings.json)), seules
les deux formes non destructives sont pré-autorisées — l'appel nu (celui des skills) et `--check` ;
`--volumes` demande une confirmation, comme il se doit pour un geste explicite. Invariants testés par
[`tests/test_clean_runner_containers.py`](../tests/test_clean_runner_containers.py), sur un faux
CLI `docker` — ni réseau, ni Docker, ni conteneur réel.

### 8.3 Pipeline rouge — remédiation

**Pipeline rouge ?** La remédiation passe par
[`/pipeline-fix`](../.claude/commands/pipeline-fix.md) (voir §5) : diagnostic des jobs en échec,
correctif local quand c'est corrigeable, commit `Refs #<iid>` poussé sur la branche, suivi du
nouveau pipeline. Les briques réutilisables vivent dans `lib.sh` : `pipeline-latest <ref>`,
`pipeline-status <id>`, `pipeline-failed-jobs <id>`, `job-trace <job-id> [lignes]`,
`pipeline-wait <id> [timeout]` (parsing shell pur, comme le reste du fichier). Reproduire les
contrôles en local avant de pousser : mêmes commandes que les jobs (ruff/pytest/mypy via le venv
du repo ; shellcheck sur des fins de ligne LF — la CI checkout en LF, une copie Windows CRLF
produit des faux SC1017).

## 9. Deux tickets en parallèle — un worktree par session

Un clone n'a qu'**un seul répertoire de travail** : deux sessions Claude Code ouvertes dessus
partagent le même `HEAD`, et la branche créée par l'une change les fichiers sous les pieds de
l'autre. Pour traiter **deux tickets en même temps**, on ouvre un **worktree git** par ticket —
un second répertoire de travail sur *le même dépôt* (objets, refs, remotes et configuration
partagés), avec sa propre branche empruntée. Pas de second clone, pas de re-fetch (#152).

```bash
bash scripts/git/worktree.sh 152          # crée (ou complète) le worktree du ticket #152
bash scripts/git/worktree.sh list         # les worktrees en place, avec leurs ports
bash scripts/git/worktree.sh remove 152   # retire le worktree — jamais la branche
bash scripts/git/worktree.sh gc           # ramasse ceux dont le travail est soldé (§9.2)
```

Le script fait plus qu'un `git worktree add` : il résout la branche comme
[`/ticket-start`](../.claude/commands/ticket-start.md) (`lib.sh branch-for`) et la crée depuis
`origin/main`, recopie le `.env` (gitignoré, donc absent du worktree), **partage par lien**
`.venv/` et `.tools/` (jonction sous Windows : aucun droit administrateur), **installe** les
dépendances de `apps/web` et écrit un `.claude/settings.local.json` dédié.

### 9.1 Monté d'office par `/ticket-start` (#181)

Ces trois commandes restent disponibles, mais **on n'a plus à y penser** : `/ticket-start` monte
lui-même le worktree du ticket, et le **clone principal ne change plus jamais de branche**. On peut
donc y rester sur `main` — lire le code de référence, préparer un autre sujet, relire une MR —
pendant qu'un ticket est en cours ailleurs. Le parallélisme devient le régime par défaut au lieu
d'une option à se rappeler.

L'aiguillage tient dans une sous-commande, appelée à l'étape 2 de `/ticket-start` :

```bash
bash scripts/git/worktree.sh ensure <iid>
```

Elle monte le worktree si besoin et rend son verdict **en dernière ligne de stdout**, pour que
l'appelant n'ait pas à interpréter le rapport humain qui la précède :

| Verdict | Situation | Ce que fait `/ticket-start` |
|---|---|---|
| `WORKTREE <chemin>` | clone principal sur `main` ; ou worktree d'un **autre** ticket | **relocalise la session** dans ce chemin (outil `EnterWorktree`), puis continue |
| `ICI <chemin>` | on est **déjà** sur la branche du ticket | ne relocalise rien et continue sur place |

> **Les deux gestes de cette étape sont autorisés sans confirmation** (#199) :
> [`.claude/settings.json`](../.claude/settings.json) porte `EnterWorktree` et les verbes non
> destructifs de `worktree.sh` (`ensure`, `list`, `gc`) dans son `allow`. Sans eux, `/ticket-start`
> s'interrompait deux fois de suite sur son seul geste automatique, alors que la commande venait
> d'être demandée explicitement et que le verdict `WORKTREE` vient du **script**, pas d'une décision
> d'agent — ce qui contredisait la règle « le résumé de cadrage n'est pas une pause d'autorisation »
> (§5). Ce n'est pas un élargissement du régime de permissions : monter un répertoire de travail et
> s'y placer n'écrit rien côté GitLab, ne supprime aucune branche et ne force-pushe rien. Deux
> pièges appris au passage : le `allowed-tools:` du frontmatter d'une commande **ne vaut pas
> permission** (`/ticket-start` déclarait déjà `EnterWorktree` et la question restait posée), et
> `EnterWorktree` s'écrit **nu, sans spécificateur** — comme `Skill` (§11.7), il ne déclare pas de
> `ruleContentField`, donc une règle paramétrée ne matcherait jamais rien. `worktree.sh remove`
> reste **hors** de l'`allow` : son `--force` passe outre les changements non commités, c'est
> exactement le genre de geste qui mérite une confirmation.

Le cas `ICI` n'est pas un détail de confort : c'est lui qui garde
[`scripts/orchestrate/run.sh`](../scripts/orchestrate/run.sh) intact (§11). La boucle autonome monte
elle-même le worktree avant d'y lancer sa session ; un second worktree y serait une régression
franche. Le clone principal **déjà** sur la branche du ticket rend `ICI` lui aussi — c'est une
reprise de travail en cours, et l'en déloger de force serait gratuit et risqué. Le nouveau régime
s'installe ticket par ticket, sans migration.

> ⚠ **La relocalisation déplace le répertoire de travail, pas le bloc `env`.** Mesuré sur #181 :
> `EnterWorktree` ne réévalue que les caches liés au CWD (sections du prompt système, mémoire,
> plans) ; `MAESTRO_PORT_API`/`_UI` et `MAESTRO_CHROME_PROFILE` sont résolus au **démarrage** de la
> session et gardent donc les valeurs du clone principal. Une session relocalisée est isolée côté
> **fichiers**, pas côté **ports Control Tower ni profil de navigateur** — exactement la collision
> que le tableau ci-dessous cherche à éviter. `ensure` **affiche** les valeurs propres au worktree :
> pour un ticket qui démarre la stack ou pilote le navigateur, les passer explicitement, ou ouvrir
> une **session neuve** sur le worktree (elle, chargera son `settings.local.json`). Pour tous les
> autres — script, backend, doc — la relocalisation est complète.

Conséquence de bord : `start-brief` (étape 1) ne **refuse** plus un arbre de travail non propre, il
le **signale**. Puisque le travail part dans un autre répertoire, des changements non commités dans
celui-ci restent intacts et hors du chemin ; les refuser bloquerait le démarrage pour une saleté
sans rapport avec le ticket. La décision revient à la commande, seule à connaître le verdict :
bloquante sur `ICI`, anodine sur `WORKTREE`.

Pourquoi `node_modules` s'installe au lieu d'être partagé, lui : **Turbopack refuse un
`node_modules` lié** (« Symlink `[project]/node_modules` is invalid, it points out of the
filesystem root ») et l'UI ne démarre alors pas du tout. C'est le seul artefact dupliqué ;
l'installation est déléguée à `scripts/setup.sh --only web`, source unique du parcours (§7).

**Ce qui doit différer d'une session à l'autre** — et que le script pose d'office :

| Ressource | Pourquoi | Valeur dans le worktree |
|---|---|---|
| Ports Control Tower | l'API et l'UI de la seconde session tueraient celles de la première | `MAESTRO_PORT_API` = 8000 + (iid mod 100), `MAESTRO_PORT_UI` = 3000 + (iid mod 100) |
| Profil du navigateur MCP | Chrome n'accepte **qu'un consommateur par profil** (verrou ProcessSingleton) | `MAESTRO_CHROME_PROFILE` = `~/.maestro/chrome-profile-<iid>` |

Le reste suit tout seul : les **hooks git** sont une configuration du dépôt (`core.hooksPath`,
partagée par tous les worktrees) dont le chemin est **relatif**, donc résolu depuis la racine du
worktree courant ; `glab` fonctionne depuis n'importe quel worktree.

**Ce que le workflow adapte.** `main` ne peut être emprunté que par **un seul** worktree à la
fois : tout `git checkout main` échoue ailleurs que dans le clone principal. D'où
`lib.sh start-branch <branche>`, appelé par `/ticket-start` : dans le clone principal il met
`main` à jour et purge les branches mergées ; dans un worktree il branche directement sur
`origin/main`, et ne fait rien si la branche est déjà celle du worktree. De même,
[`/branch-cleanup`](../.claude/commands/branch-cleanup.md) ne bascule pas sur `main` depuis un
worktree. La fin de vie du worktree, elle, ne demande **aucun geste** : elle est ramassée d'office
(§9.2). La **branche n'est jamais supprimée par ce script** : cela reste le rôle de
`/branch-cleanup`, après confirmation du merge par GitLab (§6).

> ⚠ **Ne jamais retirer un worktree à la main** (`rm -rf`, ou `git worktree remove` lancé
> directement). Les artefacts partagés sont des **jonctions**, qu'une suppression récursive
> **traverse** : elle vide alors le `.venv` et le `node_modules` du **clone principal**. Et comme
> ces dossiers sont gitignorés, git ne les voit pas comme « fichiers non suivis » et ne s'arrête
> pas — la commande réussit, les dégâts sont silencieux (constaté sur #152, d'où le test de
> régression `test_remove_ne_vide_pas_les_artefacts_du_clone_principal`). `worktree.sh remove`
> **délie d'abord, retire ensuite**, et refuse tout worktree porteur de changements non commités.
> En cas de dégât : `.venv/Scripts/python.exe -m pip install --force-reinstall -e ".[dev]"`
> (une réinstallation simple ne suffit pas — les métadonnées des paquets amputés sont intactes,
> donc pip les croit installés).

### 9.2 Ramassés d'office quand le travail est soldé (#197)

Ouvrir un worktree était automatique depuis #181, le refermer restait un **geste manuel** — donc un
geste que personne ne faisait. Constat du 2026-07-30 : **9 worktrees, 9 tickets fermés et MR
mergées**, soit 100 % de déchets. Le coût n'est pas la duplication du dépôt (`.git` est partagé,
`.venv`/`.tools` le sont par jonction) mais le `node_modules` **installé sur place** — 498 Mo des
535 Mo d'un worktree, 93 %. À l'échelle d'un run `/orchestrate` de dix tickets, ~5 Go en silence.

```bash
bash scripts/git/worktree.sh gc            # ramasse ce qui est soldé
bash scripts/git/worktree.sh gc --check    # dit ce qu'il retirerait, sans rien toucher
```

C'est le **symétrique de `cleanup-merged`** (#23), qui purge les branches locales mergées au
démarrage d'un ticket. Même principe, même garde-fou : la fin du travail est **confirmée par
GitLab**, jamais déduite du nom de la branche.

**Ce qui déclenche le retrait** (`lib.sh worktree-done <iid> <branche>`, une lecture dans le cas
nominal) : la **MR de la branche est mergée**, ou le **ticket est fermé** (réalisé, abandonné,
doublon). Tout le reste est conservé — y compris un verdict **inconnu** (glab absent, hors ligne,
ticket illisible) : ne rien savoir n'autorise rien.

**Trois refus**, dans cet ordre :

| Refus | Pourquoi |
|---|---|
| le worktree de la **session courante** | on ne se retire pas le sol sous les pieds |
| un worktree porteur de **travail non sauvegardé** | signalé, jamais supprimé en silence — mieux vaut 535 Mo de trop qu'un commit perdu |
| un verdict autre que « fini » | le nom `chore/152-…` n'est pas une preuve de merge (§6) |

Le second refus a une subtilité qui décide de tout : **le projet merge en squash**. Les commits
d'une branche mergée ne sont donc jamais des ancêtres de `main`, et GitLab supprime la branche
distante au merge — un « ai-je des commits en avance ? » mesuré sur `origin/main..HEAD` compterait
le travail de *tout* worktree mergé et ferait refuser chaque candidat, rendant le ramassage inutile.
La bonne question n'est pas « suis-je en avance ? » mais **« ce que je porte est-il sur le
serveur ? »**, posée dans cet ordre :

1. **HEAD est-il un ancêtre d'`origin/main`** ? Alors tout est là-bas, quelle que soit l'histoire de
   la branche — y compris une branche **re-créée depuis `main`** après son merge, dont le sha de
   merge a divergé ;
2. sinon `origin/<branche>`, s'il existe encore (branche poussée, MR pas encore mergée, ou case de
   suppression décochée) ;
3. sinon le **sha de merge** rendu par `worktree-done` — la tête de la branche source au moment du
   merge, seule trace locale de ce qui est parti ;
4. sinon `origin/main`, cas de la branche **jamais poussée** (ticket fermé sans MR), où ses commits
   locaux sont précisément le travail à ne pas perdre.

**Où c'est câblé** — nulle part une commande dédiée :

| Moment | Appel | Pourquoi là |
|---|---|---|
| `/ticket-start` | `worktree.sh ensure` → `gc --auto` | le seul point de passage garanti d'un ticket ; muet quand il n'y a rien à faire |
| [`/branch-cleanup`](../.claude/commands/branch-cleanup.md) | `worktree.sh gc`, **avant** la purge des branches | le merge vient d'être confirmé, c'est le moment le plus précoce |
| `scripts/orchestrate/run.sh` | `gc --auto` au démarrage du run | c'est là que l'accumulation fait le plus mal, sans personne devant |

L'ordre dans `/branch-cleanup` n'est pas cosmétique : `git branch -D` **refuse** une branche
empruntée par un worktree (« checked out at … »). Sans ramassage préalable, les branches des
worktrees soldés étaient comptées « conservées » et restaient indéfiniment — les deux ménages se
bloquaient l'un l'autre.

`gc` ne supprime **aucune branche** et n'écrit **rien** dans GitLab. Le retrait passe par la même
séquence que `remove` — délier, puis retirer (le garde-fou de #152 ci-dessus, écrit une seule fois
dans le script). `MAESTRO_WORKTREE_GC=0` désactive le passage automatique ; les tests, eux, imposent
le verdict par `MAESTRO_WORKTREE_VERDICT` et tournent donc sans réseau ni glab
([`test_worktree.py`](../tests/test_worktree.py)).

**Limites assumées.**

- Le **runner CI est unique** (§8) : les pipelines des deux MR se **sérialisent**. Plus lent,
  jamais bloquant.
- Le venv partagé porte `maestro` en **mode éditable pointé sur le clone principal** — un `.pth`
  qui installe un finder vers `<clone principal>/maestro`. Ce finder passe *après* le `PathFinder`
  de Python : le `maestro/` d'ici l'emporte, **mais seulement si la racine du worktree est dans
  `sys.path`**. Et ce qui l'y met n'est pas le répertoire d'où l'on lance — c'est **le lanceur**.

  | Lancement, depuis la racine du worktree | ce que Python ajoute à `sys.path` | `import maestro` |
  |---|---|---|
  | `.venv/Scripts/python.exe -m pytest` (ou `-c`, ou stdin) | le **répertoire courant** | le **worktree** ✅ |
  | `.venv/Scripts/pytest.exe` — script console | le dossier du **script** (`.venv/Scripts`) | le **clone principal** ❌ |

  Les **points d'entrée console** (`maestro-run`, `maestro-demo`, `pytest`, `ruff`…) tombent tous
  dans la seconde ligne. **Toujours passer par `python -m`** depuis un worktree.

  Mesuré sur #194, sur la même sonde et le même commit — la troisième ligne est le contrefactuel
  qui isole la cause, puisqu'elle ne change *que* `sys.path` :

  | Lanceur | `import maestro` résout vers |
  |---|---|
  | `pytest.exe` | `E:\…\Maestro\maestro` (clone principal) |
  | `PYTHONPATH=<worktree> pytest.exe` | `E:\…\194-…\maestro` (worktree) |
  | `python.exe -m pytest` | `E:\…\194-…\maestro` (worktree) |

  Attention à ne pas confondre avec `sys.path[0]`, qui vaut ici le dossier des tests dans les trois
  cas : pytest l'y insère lui-même. Ce qui décide, c'est la **présence de la racine du worktree
  quelque part** dans `sys.path` — pas la tête de liste.

  > ⚠ **Ce piège a produit un faux verdict de CI locale pendant tout #181 (#194).**
  > `scripts/ci/local.sh` lançait `pytest` par son script console : les tests du worktree
  > s'exécutaient contre le `maestro/` **de la branche sortie dans le clone principal**, pendant que
  > `--cov=maestro` — résolu, lui, comme un chemin relatif au répertoire courant — instrumentait la
  > copie d'ici. Les deux ne coïncidant sur rien, la couverture tombait à **0 %** et le seuil rendait
  > un `ÉCHEC` là où la CI, sur le même commit, était verte. Le faux rouge n'était que la partie
  > visible : le **faux vert** est le vrai danger — un correctif cassé sort vert si le code fautif
  > n'a jamais été chargé. Mesuré sur #195 : `local.sh --only pytest` rendait *1 failed, couverture
  > 0 %* quand `python -m pytest` sur le même commit rendait *940 passed, 94,14 %*, identique à la
  > CI. Depuis, `job_pytest` lance `python -m pytest` **et** sonde d'abord où `import maestro` se
  > résout : s'il pointe ailleurs, le job est annoncé `IGNORÉ` avec sa raison et **n'entre pas dans
  > le verdict** — un filet qui ment est pire que pas de filet.
  >
  > Les autres jobs ne sont **pas** concernés, et c'est vérifié : `ruff` et `mypy` reçoivent des
  > **chemins** relatifs à la racine du worktree et analysent donc bien les fichiers d'ici (contrôlé
  > en glissant une erreur de typage dans un fichier n'existant que dans le worktree — `mypy maestro`
  > la signale). Seul l'**import à l'exécution** est en cause.
- Si la branche modifie `pyproject.toml` ou `apps/web/package.json`, les dépendances partagées ne
  correspondent plus : créer le worktree avec `--sans-liens`, puis l'équiper avec
  `bash scripts/setup.sh`.

---

## 10. Travail à plusieurs — plusieurs personnes, plusieurs clones

Le workflow décrit plus haut a d'abord été réglé pour **une personne, un clone**. Passer à
plusieurs ne change aucune règle : cela **rend coûteux** ce qui n'était qu'inconfortable. Cette
section est la **synthèse** du chantier #155 — elle n'introduit rien, elle dit où chaque mécanisme
est traité et pourquoi il existe.

| Ce qui casse à plusieurs | Le mécanisme | Où |
|---|---|---|
| Deux sessions sur le même clone se marchent dessus (`HEAD` partagé) | un **worktree par session** — ports Control Tower et profil de navigateur dédiés | §9 |
| Deux personnes démarrent le **même ticket** ; `begin` remplace les assignés et le retire à son propriétaire | **anti-collision** : `start-brief` dit « libre » ou « ⚠ déjà pris par … », `/backlog` sépare les tickets libres | §5 |
| Une session clôture un ticket **qui n'est pas le sien** (MR et temps posés à la place d'un autre) | **garde-fou de clôture** : `close-guard` compare l'iid visé à la branche courante *et* aux assignés | §6 |
| Les lots d'un parent s'attendent en file alors qu'ils sont indépendants | marqueur **`(parallèle)`** dans la checklist ; `startables` liste **tous** les lots prenables | §5.1 |
| Une branche vieillit pendant qu'`origin/main` avance ; le conflit se découvre au merge | **alerte de retard** avant le push : `behind-main` (commits de retard + fichiers modifiés des deux côtés) | §6 |
| Une MR ouverte n'est relue par personne, faute de savoir qu'elle attend | **revue best-effort outillée** : **file de revue** en tête de `/backlog`, la plus ancienne d'abord (aucun relecteur posé d'office, #196 ; `set-reviewer` reste là pour une pose manuelle) | §6 |
| La CI dépend du poste d'**une** personne : elle éteint sa machine, l'équipe ne merge plus | **runner partagé permanent** (`--partage`, machine toujours allumée), les runners locaux en secours | §8.1 |
| Un échec de lint occupe le runner de quelqu'un d'autre pour une faute de frappe | **filet CI local** : `bash scripts/ci/local.sh` rejoue les jobs du pipeline avant le push | §8 |
| La moitié du `.env` circule à la main, de canal en canal | marqueurs **`[perso]` / `[partagé]`** + `env-pull.sh`, qui complète sans jamais écraser | §7.3 |

**Rien n'est bloquant.** Aucun de ces mécanismes n'interdit quoi que ce soit : ils *disent*, et la
décision reste humaine. `behind-main` et `close-guard` rendent un **code de retour lu, jamais
fatal** (`… || verdict=$?`) ; la revue n'exige **aucune approbation** — c'est la visibilité qui la
déclenche ; et **aucun relecteur n'est désigné d'office** (#196), la pose restant un geste humain
outillé par `set-reviewer`. Les seuls refus durs restent ceux des garde-fous de
§6 : pas de merge automatique, pas de force-push, pas de suppression de branche non mergée.

**Deux personnes, une machine chacune : le parcours.**

1. `bash scripts/setup.sh` puis `bash scripts/env-pull.sh` — le poste est équipé, les secrets
   partagés arrivent des variables CI/CD (§7.3).
2. `/backlog` → prendre un ticket de la section **Libres** (§5) ; s'il est marqué `(parallèle)`
   dans un parent, quelqu'un d'autre peut prendre le lot voisin en même temps (§5.1).
3. `/ticket-start <iid>` — s'arrête si le ticket est déjà pris ; sinon branche, statut, dates.
4. `bash scripts/ci/local.sh` avant de pousser (§8).
5. `/ticket-ship` — retard sur `origin/main` signalé, garde-fou de clôture, MR (sans relecteur
   désigné : c'est la file de revue qui appelle un relecteur, §6).
6. La MR apparaît en tête de `/backlog` chez tout le monde jusqu'à son merge — **décision humaine**
   (§6).

> **Tests.** Ces comportements sont couverts par [`tests/test_collaboration.py`](../tests/test_collaboration.py)
> (helpers `lib.sh` + contrôle runner de `doctor.sh`), [`tests/test_env_pull.py`](../tests/test_env_pull.py)
> et [`tests/test_ci_local.py`](../tests/test_ci_local.py) — même parti pris que
> [`test_setup.py`](../tests/test_setup.py) et [`test_worktree.py`](../tests/test_worktree.py) :
> dépôt jetable, **ni réseau ni Docker ni compte GitLab** (un `glab` factice répond depuis une
> fixture et journalise les appels), on teste la **décision** des scripts et non l'API.

## 11. Traitement autonome du backlog — la boucle d'orchestration

Traiter un ticket demande d'ordinaire une présence du début à la fin : ouvrir une session,
`/ticket-start`, laisser faire, `/ticket-ship`, recommencer. Quand le backlog contient une suite de
lots entièrement décrits, c'est du travail séquentiel qui n'attend qu'un pilote. La boucle
d'orchestration (`scripts/orchestrate/`, parent #167) le déroule **sans supervision** : un ticket =
**un worktree** = **une session Claude Code**, de `/ticket-start` à `/ticket-ship`, avec **reprise
automatique** quand la limite d'usage de 5 h tombe au milieu.

```bash
bash scripts/orchestrate/queue.sh --check   # l'ordre de traitement, et ce qui a été écarté
bash scripts/orchestrate/run.sh --dry-run   # le plan et ce qui serait fait — rien n'est lancé
bash scripts/orchestrate/run.sh             # le run, dans un terminal laissé ouvert
bash scripts/orchestrate/run.sh --detach    # idem, dans une console indépendante — rend la main
bash scripts/orchestrate/status.sh --watch  # où en est le run, depuis n'importe quel terminal
touch .maestro/orchestrate/STOP             # arrêt d'urgence
```

La commande [`/orchestrate`](../.claude/commands/orchestrate.md) prépare, explique et relit un run ;
**le script en reste la source unique**.

### 11.1 Pourquoi le pilote est un script shell

Une boucle écrite en `/loop` ou en sous-agents consommerait le **même quota** que le travail
piloté : la limite d'usage tuerait le pilote en même temps que la session pilotée, et plus rien ne
pourrait programmer la reprise. Un script shell ne consomme aucun quota — il peut attendre et
relancer. C'est la raison d'être de tout le découpage qui suit.

Corollaire pratique : **un run se lance hors de Claude Code**, dans un terminal Git Bash laissé
ouvert (il survit à la fermeture de Claude Code, pas à celle du terminal).

**Le lancer depuis une session reste possible — `--detach` (#173).** La contrainte porte sur ce que
le pilote *est*, pas sur qui appuie sur le bouton : `--detach` relance le script dans une **console
indépendante**, puis rend la main tout de suite. Le pilote y est bien un shell dans son propre
processus — ni une session Claude Code, ni un travail d'arrière-plan suspendu à une session, qui
mourrait avec elle. Ce qui l'en distingue en pratique :

| | Pilote |
|---|---|
| `/loop`, sous-agents | ✗ consomme le quota du travail piloté, meurt avec la limite d'usage |
| arrière-plan d'une session | ✗ ne consomme rien, mais s'arrête quand la session s'arrête |
| `--detach` | ✓ shell détaché — survit à la session dans le cas courant |
| terminal ouvert par la personne | ✓ shell, ne dépend d'aucun processus tiers |

`--detach` écrit un **lanceur** (`<run-id>/lancer.sh`) que la console se contente d'exécuter — les
guillemets imbriqués sous `cmd /c start` sont un nid à erreurs, et un lanceur sur disque est lisible
et rejouable à la main. Il **ne calcule pas le plan** : c'est le run détaché qui le fige, avec le
`--run-id` qu'on lui impose (deux calculs risqueraient de diverger). La sortie passe par `tee` dans
`<run-id>/run.log`, pour qu'une fenêtre fermée n'emporte pas la seule trace de ce qui s'est passé —
la fenêtre gardant ses **couleurs**, que `tee` lui ferait perdre, et le journal en étant débarrassé
en fin de run (il se relit plus tard, souvent par un outil).
Combiné à `--dry-run`, il n'a rien à détacher : le plan s'affiche en direct, en lecture seule.

**Ce qu'il ne garantit pas**, et qu'il annonce lui-même : la console ne dépend plus du shell
appelant, mais rien n'assure qu'elle survive à un parent qui enfermerait ses descendants (*job
object* Windows). Le filet est le plan sur disque — `--plan <run-id>/plan.tsv` le rejoue, les
tickets déjà livrés étant sautés d'eux-mêmes (§11.4). Qui veut la certitude plutôt que le filet
lance la commande **sans** `--detach` dans son propre terminal.

### 11.2 L'ordre, figé une fois — `queue.sh`

Le plan est calculé **au démarrage** et ne bouge plus : deux appels sur le même backlog rendent le
même plan, et un run reste reproductible même si le backlog évolue pendant qu'il tourne.

| Règle | Pourquoi |
|---|---|
| Seuls les tickets **« À faire » et non assignés** du **milestone courant** | un ticket assigné est le travail de quelqu'un (§5) ; un autre milestone n'est pas la phase en cours |
| Les **parents de suivi sont écartés**, remplacés par leurs lots **dans l'ordre de la checklist** | un parent ne porte ni branche ni code (§5.1) ; c'est la checklist qui encode les dépendances |
| Les lots d'un même parent restent **contigus**, le parent héritant de leur priorité maximale | s'intercaler ferait partir le lot suivant d'un `origin/main` qui a bougé pour rien |
| Le reste trié par `prio::` puis iid croissant | pour que l'ordre soit reproductible |

`--check` ajoute sur stderr le détail des **écartés avec leur raison** : sans lui, une absence est
indistinguable d'un bug.

### 11.3 Un ticket, une session — `run.sh`

Pour chaque ticket : `scripts/git/worktree.sh <iid>` (§9) monte son répertoire de travail et ses
ports, puis une session dédiée est lancée en mode `-p`, avec un `--session-id` fixe — la clé de la
reprise.

**La console dit ce que la session fabrique (#176).** En `--output-format json`, le CLI n'écrit
qu'à la fin : entre la ligne `[n/N] #<iid> — …` et le verdict, la console restait muette jusqu'à
45 minutes, et rien ne distinguait « ça travaille » de « c'est planté ». La session tourne donc en
**`--output-format stream-json --verbose`** — un objet JSON par ligne, au fil de l'eau — dont
`run.sh` tire **une ligne compacte par action** :

```
  · Read docs/21-configuration-mcp.md
  · Edit core/models/mcp.py
  · Bash pytest -q
```

**Deux fichiers, et c'est ce partage qui rend le mode sûr** : le flux brut va dans `<iid>.jsonl`, et
`<iid>.json` ne reçoit **que l'objet `result` final**. Le coût, le verdict et la détection de limite
d'usage lisent ce dernier, or ils prennent la **première** occurrence d'une clé — y déverser tout le
flux ferait rapporter le coût d'un événement intermédiaire, une régression silencieuse. Si aucun
`result` n'est passé (CLI plus ancien, flux coupé), la dernière ligne en tient lieu. Les **deux**
invocations sont concernées, session neuve *et* reprise `--resume`.

**Le verdict vient de GitLab, pas de la prose de la session.** Un ticket est réussi si, et seulement
si, sa branche porte une **MR ouverte** *et* que son statut natif est **« En revue »** — exactement
ce que `/ticket-ship` laisse derrière lui. Une session peut conclure « c'est fait » en s'étant
trompée, ou échouer après avoir tout livré.

**Une session qui croit faire une pause termine le ticket (#178).** En mode `-p`, **la fin du tour
est la fin du processus** : une session qui rend la main sur « j'attends la fin du run de couverture,
je poursuivrai dès le verdict » ne sera jamais réveillée. Le CLI sort en `end_turn` / `success` /
**code 0**, indiscernable d'une session qui a réellement fini. C'est le mode d'échec le plus coûteux
observé sur le premier run réel — il ne perd pas un ticket, il perd **la file derrière lui** (1
livré, 1 échec, 3 lots sautés). Deux réponses :

- **le prompt** interdit désormais d'attendre un **résultat** autant qu'une **validation** : un
  résultat manquant s'obtient **en avant-plan**, sinon on tranche sans lui en le disant, sinon on
  sort sur `ORCHESTRATE: ECHEC`. Sa consigne de reprise couvre aussi le **travail non commité** —
  un arbre sale sans aucun commit est précisément la trace qu'une session perdue laisse, et la
  version précédente, qui ne parlait que de commits, ne la déclenchait pas ;
- **la boucle regarde le worktree** avant de consigner l'échec, et distingue deux situations que
  « MR "aucune", statut "À faire" » confondait : `session terminée sans clôture, 5 fichier(s) non
  commité(s)` (rattrapable — le travail est là, la console dit où) contre `session terminée sans
  rien produire (worktree propre)` (à refaire). Les commits d'avance sur `origin/main` comptent au
  même titre : une session peut s'être arrêtée juste avant `/ticket-ship`.

**Sur échec**, le ticket est laissé en l'état et **les lots suivants du même parent sont sautés**
(ils partiraient d'une base incomplète) ; les autres groupes s'enchaînent — une erreur à 2 h du
matin ne doit pas geler le reste de la nuit. Un ticket **pris par quelqu'un d'autre** entre le calcul
du plan et son tour est sauté, pas volé : son statut est relu juste avant de le prendre.

Garde-fous : `--max <n>` (compte les tickets **tentés**, pour qu'une panne systématique n'épuise pas
le plan), `--budget <usd>` par ticket, `--timeout <durée>` par ticket, et le fichier
`.maestro/orchestrate/STOP`, pris en compte entre deux tickets **et pendant une attente**.

Journal, sous `.maestro/orchestrate/<run-id>/` : `plan.tsv` (le plan figé), `<iid>.session`
(l'UUID), `<iid>.jsonl` (le flux d'activité complet), `<iid>.json` (le seul résultat final — coût,
`permission_denials`), `<iid>.log` (stderr), et `resume.tsv` (une ligne par ticket : verdict, MR,
durée, coût, raison). Un run lancé avec `--detach` y ajoute `lancer.sh` (ce qui a été lancé) et
`run.log` (toute la sortie de la console, flux d'activité compris).

**Ce journal ne s'accumule plus sans fin** (#198). Rien ne le nettoyait : `run.sh` crée un
répertoire **par lancement** et ses deux `rm -rf` sont des renoncements (lancement détaché en
échec, `--dry-run`), pas un ménage. Indolore tant que le journal ne portait que des logs — 41 Ko
pour un run entier — mais le `<iid>.jsonl` de #176 est le flux `stream-json` **brut** d'une session,
non tronqué : c'est lui qui décide désormais de la croissance. Deux gestes, portés par
`scripts/orchestrate/journal.sh` et déclenchés **au démarrage de chaque run** :

- **rétention** — seuls les **N runs les plus récents** sont conservés
  (`MAESTRO_ORCHESTRATE_JOURNAL_RUNS`, défaut 10) ; les répertoires **vides** que laissent les
  sorties précoces (plan vide, `queue.sh` en échec) sont ramassés ;
- **compaction** — le `<iid>.jsonl` d'un ticket **terminé** est gzippé en `<iid>.jsonl.gz`, à
  relire avec `zcat`/`zgrep`. Jamais avant le verdict : tant que le ticket tourne, la détection de
  limite d'usage relit ce flux **entier** à chaque tentative (§11.4).

```bash
bash scripts/orchestrate/journal.sh gc --check   # ce qui partirait, sans rien écrire
bash scripts/orchestrate/journal.sh gc           # le ménage, à la main
```

**Rien n'est retiré sous les pieds d'un run** : ni celui qui fait le ménage, ni un run dont la
dernière écriture date de moins de `MAESTRO_ORCHESTRATE_SILENCE` (défaut 900 s). Faute de PID,
l'activité se **déduit** ici comme dans `status.sh` (§11.5), et le doute profite au journal. Le
ménage est **best-effort** de bout en bout — son échec ne fait jamais échouer un run —, et
`MAESTRO_ORCHESTRATE_JOURNAL_GC=0` le désactive.

### 11.4 La limite d'usage est une pause, pas un échec

Trois filets de détection, parce que la forme exacte du signal en mode `-p` n'est pas contractuelle
(les marqueurs sont ceux du classifieur d'erreurs du CLI : `usage limit reached`, `rate limited`,
`429`, `credit balance`) :

1. **heure de reset explicite** — epoch, millisecondes ou ISO 8601 → attente jusqu'au reset + 2 min ;
2. **le message sans heure de reset** → paliers de 15 min ;
3. **rien de tout cela** → ce n'est pas une limite, c'est un échec ordinaire : aucune reprise.

Un reset **déjà passé** retombe sur le palier, sinon la boucle relancerait aussitôt sur la même
limite. La reprise se fait en **`--resume <uuid>`** : la conversation repart avec le travail déjà
fait dans son contexte. Si la session est perdue, **redémarrage à froid** — le prompt et
`/ticket-start` sont idempotents, et le travail commité est sur la branche.

Au-delà de **5 h 30** d'attente cumulée sur un ticket, ce n'est plus une fenêtre de 5 h mais
l'**hebdomadaire** : le run s'arrête proprement plutôt que de dormir des jours. `--max-reprises`
(3 par défaut) borne les tentatives.

`bash scripts/orchestrate/run.sh --test-reprise <fichier.json>` rejoue ce jugement sur une sortie de
session capturée — c'est ce qui rend la reprise vérifiable **sans attendre de vraiment taper la
limite**.

### 11.5 Savoir où en est un run — `status.sh`

La console d'un run répond très bien à « où ça en est ? » — depuis #176 elle égrène même chaque
action — mais seulement tant qu'on l'a sous les yeux. Fenêtre fermée, autre poste, run lancé la
veille : il ne restait que le répertoire du run, et la seule façon de trancher entre « ça travaille »
et « c'est planté » était d'aller regarder à la main les *mtimes* d'un worktree.
`scripts/orchestrate/status.sh` (#177) fait cette lecture une fois pour toutes :

```bash
bash scripts/orchestrate/status.sh                     # le run le plus récent, une fois
bash scripts/orchestrate/status.sh --watch [sec]       # ... et rafraîchi tant qu'il tourne
bash scripts/orchestrate/status.sh --run-id <id>       # un run précis   (--list les énumère)
bash scripts/orchestrate/status.sh --no-gitlab         # hors ligne : tout sauf l'état GitLab
```

En une sortie : l'état du run, le **ticket en cours** et son temps écoulé, les **commits et fichiers
modifiés de son worktree**, sa **dernière activité**, son **état GitLab** (statut, MR), le **reste du
plan** et le **bilan des traités** (verdict, MR, durée, coût). Le script est en **lecture seule** —
il n'écrit ni dans le run, ni dans le dépôt, ni dans GitLab, et ne touche pas à `run.sh` (bash relit
un script au fil de son exécution : un run en cours doit pouvoir être observé sans risque).

Deux partis pris valent d'être connus :

- **Le worktree est le meilleur signal de progression.** Pendant une session, `<iid>.json` reste
  vide — le CLI n'écrit son résultat qu'à la fin. Ce qui dit vraiment que ça avance, ce sont les
  commits et les fichiers modifiés du worktree du ticket, lus avec git, en local.
- **« En cours » se déduit, il ne se lit pas.** `run.sh` n'écrit pas de PID : le ticket en cours est
  le premier du plan qui a un `<iid>.session` sans ligne dans `resume.tsv`. Un run tué au milieu
  laisse exactement la même trace qu'un run qui travaille — d'où la ligne **activité**, qui date la
  dernière écriture (répertoire du run *et* index git du worktree) et bascule l'en-tête en
  « en cours ? » au-delà de 15 min de silence (`MAESTRO_ORCHESTRATE_SILENCE`). C'est une déduction
  présentée comme telle, pas un verdict.

**Aucun run en cours est un cas normal**, pas une erreur : le script le dit et sort en 0.
[`/orchestrate --status`](../.claude/commands/orchestrate.md) s'appuie dessus plutôt que de
recomposer la lecture au coup par coup.

### 11.6 Les garde-fous d'une session sans humain

Deux couches, parce qu'une seule tomberait :

- `scripts/orchestrate/settings.run.json`, passé au CLI par `--settings` avec
  `--permission-mode acceptEdits` : il **conserve les règles `deny` du dépôt** (§6) — ce que
  `--dangerously-skip-permissions` neutraliserait — et n'ajoute au `allow` que ce dont une session
  de dev a besoin sans personne devant l'écran. Une commande hors liste n'est pas un blocage : elle
  est refusée et **tracée dans `permission_denials`**, ce qui sert à compléter la liste plutôt qu'à
  l'élargir à l'aveugle — la boucle de rétroaction est décrite en §11.7.
- `scripts/orchestrate/guard.sh`, branché en hook **`PreToolUse`** : il refuse **en dur, quel que
  soit le mode de permission**, force-push, `glab mr merge`/`close`, `glab ci delete`,
  `git reset --hard`, `git commit --no-verify` et **tout commit sur `main`**.
  `guard.sh --check` vérifie que la copie des `deny` n'a pas dérivé du dépôt **et** que le hook
  refuse bien chacune d'elles — sans quoi la seconde couche donnerait une fausse sécurité.

**Ce qu'un run ne fait jamais** : merger, fermer une MR, force-pusher, fermer un parent de suivi, ou
retirer le worktree d'un ticket qu'il vient de traiter — la branche y vit jusqu'au merge. Le
ramassage de son **démarrage** (§9.2) ne touche que les worktrees dont GitLab confirme le travail
soldé, donc jamais ceux du run en cours. Un run produit **N Merge Requests en Draft à relire** : le
merge reste une décision humaine (§6), et la file de revue les remonte (§10).

### 11.7 Après un run : instruire les refus de permission

L'`allow` de `settings.run.json` se complète **à partir des refus observés**, jamais à l'aveugle.
Chaque session laisse ce qu'elle n'a pas pu faire dans `permission_denials`, à la fin de son
`<iid>.json` :

```bash
# Coup d'œil : combien de refus, sur quels outils (le .json est minifié — #180 lui ajoutera une vue lisible)
grep -o '"tool_name":"[^"]*"' .maestro/orchestrate/<run-id>/<iid>.json | sort | uniq -c

# La liste, en clair. PYTHONIOENCODING est indispensable sous Windows : sans lui, une commande
# refusée contenant un accent fait tomber le print en UnicodeEncodeError (stdout en cp1252). La
# variable doit porter sur PYTHON — devant un `glab … |`, bash ne la propage pas au pipeline.
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe - <<'PY'
import json, pathlib
p = pathlib.Path(".maestro/orchestrate/<run-id>/<iid>.json")
for r in json.loads(p.read_text(encoding="utf-8"))["permission_denials"]:
    print("-", r["tool_name"], "—", r["tool_input"].get("command", r["tool_input"]))
PY
```

Un refus **ne bloque pas le run** : sans humain pour approuver, l'appel est simplement refusé et la
session se débrouille. C'est précisément le problème — **il se paie deux fois** : en tours et en
dollars quand la session contourne, en run perdu quand elle ne peut pas. Sur le premier run réel
(`20260729-132807`, **17 refus**), les deux sessions se sont vu refuser `/ticket-start` : celle de
#130 a refait le cycle **à la main** — ce que CLAUDE.md interdit, les skills en étant la source
unique — pour 100 tours, 34 min et 10,69 $ ; celle de #131 a fini sans même poser le statut du
ticket, le refus des attentes actives lui ayant par ailleurs coûté le run (§11.3, #178).

L'instruction se fait **au cas par cas** : la bonne question n'est pas « faut-il autoriser ? » mais
« qu'est-ce qui a été refusé au juste ? ». Trois issues, et deux d'entre elles ne touchent pas à la
liste :

| Verdict | Geste |
| --- | --- |
| La commande relève de la liste | l'ajouter au `allow` de `settings.run.json` |
| C'est une **forme d'appel** qu'aucune règle de préfixe ne peut reconnaître | corriger `prompt_ticket` (`run.sh`), pas la liste |
| Le refus est **mérité** | le laisser, et écrire pourquoi dans le `$comment` du fichier |

Deux pièges de lecture, découverts à ce prix, sans lesquels on instruit à côté :

- **Une commande composée vaut son maillon le plus faible.** Le CLI la découpe sur `&&`, `;` et `|`
  et exige que **chaque** morceau soit autorisé. Le refus de `grep -E "…" <fichier> | tail -8` ne
  tenait qu'à `grep` — `tail` était autorisé, et rien d'autre n'était en cause. **Dix des dix-sept
  refus** portaient de même un `cd "<worktree>" &&` en tête, que les sessions mettent **par
  habitude alors que leur répertoire courant est déjà ce worktree** ; deux ne tenaient qu'à lui, les
  huit autres y ajoutant un second morceau hors liste (`echo`, `printf`, `grep`, `sed`…).
- **L'`allow` est l'union de `settings.run.json` et de `.claude/settings.json`**, là où le `deny`
  est recopié à dessein (§11.6). Une commande « allowlistée dans le dépôt mais refusée en run »
  n'existe donc pas : c'est son emballage qui a changé. Preuve du run : la MR !149 a bien été
  ouverte par un `glab mr create` que **seul** `.claude/settings.json` autorise. On ne recopie pas
  pour autant les verbes git/glab du dépôt — une copie de l'`allow` dériverait en silence, là où
  `guard.sh --check` veille sur celle du `deny`.

Une règle ne prend pas non plus toujours de spécificateur : **`Skill` s'autorise nu**, le tool ne
déclarant pas de `ruleContentField` (`Skill(ticket-start)` ne matcherait rien), là où `Bash` expose
`command` et `Write` `file_path`.

Ce que la passe #179 a donné sur ces 17 refus : **11 levés** par six règles (`Skill`, puis `cd`,
`echo`, `printf`, `grep`, `sed` — du décor de pipeline, sans pouvoir propre, mais qui faisait tomber
des commandes déjà autorisées) ; **2 relèvent de la forme d'appel** et sont traités par le prompt
(préfixe `PYTHONPATH=…` devant l'interpréteur, chemin absolu là où la règle borne un chemin
relatif) ; **4 restent refusés à dessein** — les deux attentes actives (`for … sleep 6`,
`until [ -s … ]; do sleep 3; done`) parce que les autoriser rouvrirait le mode d'échec que #178
ferme, `jobs` pour la même raison, et `bash <script hors du dépôt>` qui serait du code arbitraire.

> **Tests.** [`tests/test_orchestrate.py`](../tests/test_orchestrate.py) — même parti pris que le
> reste : dépôt jetable, **ni réseau, ni quota, ni écriture GitLab**. Un `glab` factice répond
> depuis des fixtures (et **journalise ses appels**, ce qui rend vérifiable une promesse comme
> `--no-gitlab`), `MAESTRO_CLAUDE_BIN` remplace le CLI, `MAESTRO_ORCHESTRATE_WORKTREE` le montage
> de worktree et `MAESTRO_ORCHESTRATE_SPAWN` l'ouverture de console, si bien qu'aucune branche,
> aucune session ni aucune fenêtre réelles ne sont créées. Le **flux stream-json** se joue par un
> bouchon qui émet plusieurs événements, dont un coût leurre en tête — la régression que §11.3
> décrit. `status.sh` se teste sur des répertoires de run **écrits à la main** (c'est le seul moyen
> de poser un run interrompu ou muet) dont les dates de modification sont vieillies, et sur un vrai
> petit dépôt git local pour le volet worktree.
