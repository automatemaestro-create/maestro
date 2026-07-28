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
  vérifient, **avant toute écriture** (commit, push, MR, statut, relecteur, temps), que le ticket
  visé est bien celui de la session : `bash scripts/gitlab/lib.sh close-guard <iid> [branche]`.
  C'est le pendant en *sortie* de l'anti-collision d'entrée de `/ticket-start` (`issue-taken`, §5) —
  sans lui, un `/ticket-finish 158` lancé depuis `chore/163-…` faisait basculer **#158** « En
  revue », y accrochait la MR de la branche de #163, un relecteur et le temps d'un travail qui
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
  - `/ticket-finish` **pose un relecteur** sur la MR : `bash scripts/gitlab/lib.sh set-reviewer`
    choisit un **membre humain du projet distinct de l'auteur**, résolu via l'API des membres —
    **aucun nom en dur** ; les comptes d'automatisation sont écartés par la variable `GL_BOT_USERS`
    (défaut `MaestroAgents` : ce compte est un utilisateur GitLab ordinaire, `User.bot` y vaut
    `false`, l'API seule ne suffit donc pas à l'exclure). La désignation **tourne** entre les
    candidats (graine = iid de la MR : même MR → même relecteur, MR différentes → charge répartie)
    et elle est **idempotente** : un relecteur déjà posé — par un humain ou par un passage
    précédent — n'est **jamais** remplacé. Best-effort jusqu'au bout : sur un projet à une seule
    personne, il n'y a pas de candidat et la clôture se poursuit sans relecteur.
  - `/backlog` affiche la **file de revue** en tête (`bash scripts/gitlab/lib.sh review-queue`) :
    MR ouvertes **la plus ancienne d'abord**, avec `age_j` (l'ancienneté, c'est elle qui déclenche
    la relecture), l'état `draft`/`ready`, le statut du pipeline, l'auteur et le relecteur.
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

---

## 8. Intégration continue (CI)

Le pipeline [`.gitlab-ci.yml`](../.gitlab-ci.yml) a deux étages : `lint` — `shellcheck`
(sévérité `warning`, scripts `scripts/**/*.sh`) et `python-lint` (ruff) — puis `test` — `pytest`
(suite du dépôt, avec **couverture** pytest-cov : taux remonté dans GitLab via la clé `coverage:`
du job, échec sous `--cov-fail-under=90`) et `mypy` (typage strict de `maestro/`). Les jobs Python
partagent un **cache pip** (clé sur `pyproject.toml`) qui accélère le `before_script` d'un run à
l'autre. Un **pipeline vert est la condition de passage `En revue` → merge**.

Les **scripts shell** ne sont pas seulement lintés : le parcours de mise en route
([`scripts/setup.sh`](../scripts/setup.sh), §7) a sa propre suite pytest
[`tests/test_setup.py`](../tests/test_setup.py) (#147), qui monte un **dépôt jetable** dans un
répertoire temporaire et y lance le script pour vérifier ses invariants — `--check` n'écrit rien,
deuxième passage entièrement en `DÉJÀ FAIT`, `.env` et `settings.local.json` jamais écrasés (le
second est fusionné clé par clé), rapport complet et code de sortie non nul sur échec dur. Les
étapes réseau / Docker (`venv`, `web`, `runner`, `infra`, `verif`) y sont **neutralisées** par
`--skip` : c'est la décision du script qui est testée, jamais l'installation elle-même — la suite
tourne donc en CI sans démon Docker ni accès réseau.

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
  lui qui remplace les pipelines de branche, et il ne dépend d'aucun runner.
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

### 8.2 Pipeline rouge — remédiation

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
```

Le script fait plus qu'un `git worktree add` : il résout la branche comme
[`/ticket-start`](../.claude/commands/ticket-start.md) (`lib.sh branch-for`) et la crée depuis
`origin/main`, recopie le `.env` (gitignoré, donc absent du worktree), **partage par lien**
`.venv/` et `.tools/` (jonction sous Windows : aucun droit administrateur), **installe** les
dépendances de `apps/web` et écrit un `.claude/settings.local.json` dédié. Il ne reste qu'à ouvrir
une session Claude Code sur le dossier créé et à y lancer `/ticket-start <iid>`.

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
worktree — la fin de vie de celui-ci passe par `worktree.sh remove <iid>`, depuis le clone
principal, une fois la MR mergée. La **branche n'est jamais supprimée par ce script** : cela reste
le rôle de `/branch-cleanup`, après confirmation du merge par GitLab (§6).

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

**Limites assumées.**

- Le **runner CI est unique** (§8) : les pipelines des deux MR se **sérialisent**. Plus lent,
  jamais bloquant.
- Le venv partagé porte `maestro` en **mode éditable pointé sur le clone principal**. Le finder
  correspondant passe *après* le `PathFinder` de Python : le `maestro/` du répertoire courant
  l'emporte donc, **tant que les commandes sont lancées depuis la racine du worktree** (c'est le
  cas de `.venv/Scripts/python.exe -m pytest`). Les **points d'entrée console** (`maestro-run`,
  `maestro-demo`…) restent, eux, pointés sur le clone principal.
- Si la branche modifie `pyproject.toml` ou `apps/web/package.json`, les dépendances partagées ne
  correspondent plus : créer le worktree avec `--sans-liens`, puis l'équiper avec
  `bash scripts/setup.sh`.
