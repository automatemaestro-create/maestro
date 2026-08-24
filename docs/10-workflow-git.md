# Workflow Git & tickets — Maestro

**Version :** 0.6
Objectif : que chaque ticket soit traité de façon prévisible — même branche, même convention de commit, même cycle de vie — que ce soit un humain ou un agent Claude Code qui l'exécute.

> ## La forge est **GitHub** depuis le 2026-08-17
>
> Tickets, Pull Requests et CI vivent sur
> [`automatemaestro-create/maestro`](https://github.com/automatemaestro-create/maestro) — bascule
> #343, lot 8 du chantier #335. Le commutateur `MAESTRO_FORGE` est parti avec la branche GitLab
> (#344) : il n'y a plus de backend à choisir.
>
> Le projet **GitLab est archivé en lecture seule**. Il reste l'**archive** des 281 Merge Requests
> d'avant la bascule et du time tracking natif (629 h) : ce qu'on y trouve encore, ce qu'on n'y
> trouve plus et **comment le relire** — l'UI web, ou le client GitLab installé à la main — sont
> écrits en [docs/27 §11](./27-decision-gitlab-vers-github.md), qui en est la source unique.
>
> ⚠ **Plus aucun geste `glab` n'est prescrit ici** (#345) : les commandes du workflow passent
> toutes par `gh` ou par les helpers de `scripts/gitlab/lib.sh`, et
> [`tests/test_migration.py`](../tests/test_migration.py) garde qu'aucune n'y revienne. L'outillage
> GitLab lui-même — `.gitlab-ci.yml` et les 1 146 lignes de runner — a été **retiré** par #344.

> ## Le cycle de vie est porté par le **champ Status**, et par lui seul
>
> L'avancement d'un ticket (À faire / En cours / En revue / Terminé / Abandonné / Doublon) vit dans
> le champ **Status** du projet GitHub Projects v2 — bascule #364 le 2026-08-19, détail en **§3.8**.
> Les six labels **`workflow::*`** qui l'ont porté de #207 à #364, et le commutateur
> `MAESTRO_CYCLE` qui choisissait entre les deux, ont été **retirés par #365** : il n'y a plus rien
> à poser, ni de retour arrière à une variable près.
>
> Ce que ça change au quotidien : rien dans le vocabulaire (mêmes libellés, mêmes commandes), et une
> panne nouvelle à connaître — le Status vit sur l'**item de projet**, donc **un ticket hors du
> projet n'a aucun état** (§3.7, §3.8). Ce que le dispositif promet est **gardé** par
> [`tests/test_cycle_de_vie.py`](../tests/test_cycle_de_vie.py) (**§3.9**), y compris les trois
> promesses de la forme « le dépôt ne contient plus … », qui sont des `grep`.
>
> Les autres labels (`type::`, `agent::`, `prio::`) servent à la **catégorisation** (nature, rôle,
> priorité), pas au suivi d'avancement. Ne pas réinventer de famille `status::`.

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

1. **Jamais de commit direct sur `main`.** Tout changement passe par une branche + une Pull Request (PR).
2. **Une branche = un ticket = une PR.** Pas de branche fourre-tout multi-tickets.
3. Une branche part toujours de `main` à jour (`git pull origin main` avant `git checkout -b`).
4. Une branche est **courte** : quelques heures à quelques jours. Si un ticket prend plus longtemps, il est probablement trop gros — le redécouper (§5.1).
5. La branche est supprimée (locale + distante) dès que la PR est mergée.

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
- Le **dernier commit de la branche** (ou la description de la PR) contient `Closes #<iid>` plutôt que `Refs #<iid>` — la forge ferme alors le ticket automatiquement au merge.

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

## 3. Cycle de vie (champ Status) & labels de catégorisation

### 3.1 Le vocabulaire du cycle de vie — six valeurs, deux formes

Six valeurs, dans l'ordre du flux, et c'est **le seul vocabulaire** du cycle de vie : il n'a pas
bougé en changeant trois fois de support (champ natif GitLab → labels `workflow::*` → champ Status
de Projects v2), et c'est ce qui a permis à chaque bascule de ne toucher **aucune** commande
`/ticket-*`.

| Libellé | Slug | Posé par |
|---|---|---|
| **À faire** | `a-faire` | [`/ticket-create`](../.claude/commands/ticket-create.md), via `project-add` (§3.7) |
| **En cours** | `en-cours` | [`/ticket-start`](../.claude/commands/ticket-start.md) |
| **En revue** | `en-revue` | [`/ticket-finish`](../.claude/commands/ticket-finish.md) (PR ouverte) |
| **Terminé** | `termine` | [`/branch-cleanup`](../.claude/commands/branch-cleanup.md) et le ramassage des worktrees (§9.2) |
| **Abandonné** | `abandonne` | [`/ticket-abandon`](../.claude/commands/ticket-abandon.md) (won't-do) |
| **Doublon** | `doublon` | [`/ticket-abandon <iid> doublon`](../.claude/commands/ticket-abandon.md) |

**Deux formes, une règle.** Le **libellé** (« En cours ») est la *surface* : le vocabulaire de la
doc, des commandes, et **exactement** le nom des six options du champ Status. Le **slug**
(`en-cours`) est une forme ASCII d'entrée, héritée du temps où il était le suffixe d'un label. En
**sortie**, `lib.sh` rend toujours le libellé — le slug ne sort jamais du helper ; en **entrée**,
les deux sont acceptés. Écrire en libellé reste la forme canonique.

- Les commandes `/ticket-*` posent le cycle de vie via le helper partagé
  [`scripts/gitlab/lib.sh`](../scripts/gitlab/lib.sh) (`set-workflow <iid> <valeur>`), qui résout
  par leur **nom** le projet, son champ Status et l'option visée — d'où l'absence d'identifiant en
  dur, ici comme ailleurs (§3.5). `/ticket-start` passe par `begin <iid>`, qui groupe cette pose
  avec l'assignation et les dates (§5).
- Un ticket **fermé** garde son état : la fermeture ne pose rien toute seule. C'est
  `/branch-cleanup` — et, depuis #275, le ramassage des worktrees (§9.2) — qui passe le ticket à
  « Terminé » après le merge. `doctor.sh` signale la dérive (ticket fermé encore « En cours »,
  ticket « En revue » sans PR, ticket sans état).

#### ⚠ Il n'y a plus qu'un seul support, et c'est le sujet du chantier #358

De #207 à #364, le cycle de vie a été porté par six **labels scopés `workflow::*`**, et ce n'était
pas un choix : le **champ Status natif** de GitLab (lifecycle custom « Maestro », #12/#13) est une
fonctionnalité **Premium**, perdue avec la fin de l'essai Ultimate du groupe le **2026-08-02** — et
la donnée de statut avec elle. Sur GitLab Free, les labels étaient le seul mécanisme disponible.

Le prix de ce détour tenait en une phrase : **l'exclusion mutuelle était à notre charge**. Le `::`
d'un label scopé n'est cosmétique que sur Free, donc rien n'empêchait un ticket de porter
`workflow::a-faire` **et** `workflow::en-revue` ; toute pose devait ajouter la cible **et retirer
les cinq autres dans le même appel**. C'était la régression la plus probable du dispositif, et la
seule invisible à l'œil nu sur une ligne de backlog — les lectures rendaient alors le premier label
rencontré, donc un état plausible et arbitraire.

**Un champ à valeur unique rend cette classe de bug impossible par construction**, et c'est tout le
gain du chantier #358. La migration vers GitHub (#335/#343) a changé la réponse et pas la question —
Projects v2 offre le champ gratuitement, dépôt privé compris. Le renversement de #207 n'était donc
pas une hésitation : il tenait à ce qui était disponible, et c'est cela qui a bougé.

**#365 a retiré les six labels et le code qui les portait** : le backend labels de `lib.sh`, le
commutateur `MAESTRO_CYCLE`, la pose du `workflow::a-faire` à la création, les six labels du dépôt
et leur provisionnement par `bootstrap.sh`. Deux conséquences à connaître :

- **Il n'y a plus de retour arrière à une variable près.** Tant que les labels étaient là,
  rebasculer coûtait un `MAESTRO_CYCLE=labels` ; il coûterait désormais une migration. Ne pas
  réintroduire un second backend « au cas où » — le premier symptôme de deux supports est un ticket
  qui porte deux états.
- **La panne a changé de forme, elle n'a pas disparu.** Le Status vit sur l'**item de projet** :
  un ticket hors du projet n'a aucun état (§3.7), ce qui est l'équivalent exact du « 0 label » de
  l'ère précédente, en plus silencieux. C'est ce que `doctor.sh` traque (§3.5) et ce que
  `project-add` répare.

### 3.2 Les labels de catégorisation — `type::`, `agent::`, `prio::`

Trois autres familles de labels **scoped**, pour trier le backlog — **pas** pour suivre
l'avancement (c'est le rôle du champ Status, §3.1) :

| Famille | Valeurs | Usage |
|---|---|---|
| `type::` | `feature`, `bug`, `doc`, `infra` | Nature du ticket → détermine le préfixe de branche (§1) |
| `agent::` | `dev`, `bdd`, `devops`, `design`, `qa`, `orchestrateur` | Rôle/agent Maestro qui traite le ticket (voir [README](../README.md)) |
| `prio::` | `haute`, `moyenne`, `basse` | Urgence, pour le tri du backlog |

Ces labels sont créés (idempotent) via [`scripts/gitlab/bootstrap.sh`](../scripts/gitlab/bootstrap.sh)
— qui ne provisionne plus qu'eux depuis #365, le cycle de vie ayant son propre script
([`bootstrap-project.sh`](../scripts/github/bootstrap-project.sh), §3.5) — et **ne sont pas touchés**
par les commandes `/ticket-*` : ils relèvent du triage (à la création), pas du cycle Git.

### 3.3 Dates & time tracking — renseignés automatiquement

Les champs natifs **Dates** (widget *Start and due date*) et **Time tracking** du ticket sont
remplis automatiquement le long du cycle de vie, pour donner une vue de charge et de délai sans
saisie manuelle. Comme le statut, tout passe par la mutation `workItemUpdate` via le helper
[`scripts/gitlab/lib.sh`](../scripts/gitlab/lib.sh) — pas de GID en dur.

| Champ | Quand | Comment | Commande / helper |
|---|---|---|---|
| **Date de début** | `/ticket-start` | = jour du démarrage (aujourd'hui). Conservée si déjà posée. | `lib.sh begin <iid>` (groupé, §5) ; unitaire : `start-dates <iid>` |
| **Échéance** (due date) | `/ticket-start` | = début + délai dérivé de `prio::` : `haute` → 2 j, `moyenne` → 5 j, `basse` → 10 j (défaut `moyenne`). | `lib.sh begin <iid>` (groupé, §5) ; unitaire : `start-dates <iid>` |
| **Temps passé** | `/ticket-finish` | **estimé automatiquement par l'agent** d'après la portée du travail (diff, commits, contexte) et loggé directement, sans confirmation. | `lib.sh log-time` (`get-time-spent <iid> --hors-import` pour l'idempotence) |

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
  loggé (`get-time-spent <iid> --hors-import`) et **n'en rajoute pas** si un cycle est déjà
  enregistré, pour ne pas doubler. ⚠ **`--hors-import` et non le total**, depuis que #400 rapatrie
  l'historique de temps des tickets importés de GitLab (docs/27 §12.4) : leur total n'est jamais nul,
  même avant qu'une session ait travaillé dessus, et le garde-fou avalerait sinon en silence le temps
  de celle qui termine le ticket. Un historique repris n'est pas un cycle de dev déjà loggé.
- **Temps d'un ticket importé** : le suivi maison de `lib.sh` et le commentaire de métadonnées de
  l'import (`maestro:meta v1`) sont deux formats distincts, et c'est la **lecture** qui les joint —
  le commentaire d'import reste l'archive, jamais réécrit ; le temps qu'il porte est recopié dans le
  suivi comme une entrée ordinaire, à la première lecture, et posé au format courant à la première
  écriture. Détail dans l'en-tête de [`scripts/gitlab/lib.sh`](../scripts/gitlab/lib.sh) (section
  « SUIVI MAISON »).

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

### 3.5 Socle Projects v2 — le champ Status qui porte le cycle de vie

Le cycle de vie décrit en §3.1 est porté par six labels **parce que GitLab Free ne proposait rien
d'autre** (#207). GitHub Projects v2 offre un champ **Status** natif, gratuit y compris en dépôt
privé : le chantier **#358** en fait l'autorité unique et supprime les six labels. Ce paragraphe ne
décrit que le **socle** (#359) ; le dispositif complet est documenté par le lot final #366.

**Le projet et son champ.** `bash scripts/github/bootstrap-project.sh` monte le projet et pose son
champ Status aux six valeurs du cycle de vie, dans l'ordre du flux, avec les couleurs et les
descriptions des six labels d'aujourd'hui — le vocabulaire ne change pas en changeant de support.
Idempotent, aucune écriture en `--check`, codes de retour alignés sur `protect-main.sh` (0 conforme
ou posé, 3 non conforme, 1 pré-requis manquant). Le titre du projet (`Maestro`) est une **clé** : le
script s'en sert pour se rejouer sans rien créer en double, et `lib.sh` s'en servira pour résoudre
le projet — **aucun ID de projet, de champ ni d'option n'est figé dans le dépôt**, exactement comme
aucun GID de label ne l'est aujourd'hui.

**Ce que `doctor.sh` en contrôle (#363).** Le changement d'autorité ne supprime pas la panne, il la
**déplace**, et le bilan de santé change donc de questions en même temps que de support. Deux
disparaissent : « ticket portant 0 ou ≥ 2 labels `workflow::` » — un champ à valeur unique rend le
« ≥ 2 » impossible **par construction**, ce qui est précisément le gain du chantier — et « le projet
n'utilise pas Projects v2 », qui n'est plus vrai. Trois naissent, et elles sont plus silencieuses
que celle qu'elles remplacent :

| Dérive | Où | Ce qu'elle coûte | Ce qui la répare |
|---|---|---|---|
| **Ticket ouvert hors projet** | §4c | aucun état, et **rien à l'écran ne le distingue d'un ticket filtré** | `lib.sh project-add <iid> "<état>"`, **ticket par ticket** |
| **Item sans Status** | §4c | présent dans le projet, colonne vide : un état que personne n'a voulu | `lib.sh set-workflow <iid> "<état>"` |
| **Option du champ manquante, en trop, ou dans le désordre** | §3 | une valeur que `set-workflow` ne pourra jamais poser ; un septième état que rien ne gouverne ; des colonnes qui ne suivent plus le flux | `bootstrap-project.sh` (`--check` d'abord) |

Trois choix à connaître avant d'y toucher. La **réparation d'un ticket hors projet se nomme ticket
par ticket**, et c'est un choix et non une lacune : le backfill en masse est parti avec les labels
(#365) parce qu'il dérivait le Status du label courant et de rien d'autre — sans eux, un verbe
d'ensemble poserait un état **par défaut** sur des tickets anciens, c'est-à-dire inventerait la
donnée qu'on cherche. La **seconde colonne** de `lib.sh
status-derives` est une **cause** et non le nombre de `workflow-derives` : le nombre n'aurait plus
rien à compter, et les deux causes appellent deux gestes différents — les fondre sous un « 0 »
commun rendrait le diagnostic vrai et inutilisable. Enfin la **borne `first: 100`**, qui est celle de
tout `lib.sh`, **se dit ici** (ligne d'en-tête `#examines <examinés> <ouverts>`, rendue en ⚠ quand
elle est atteinte) : dans le fichier dont le métier est de détecter les dérives, une borne franchie
en silence produit exactement le défaut qu'a corrigé #341 — un ✓ sur une question posée à moitié.

⚠ **Un piège à connaître avant d'écrire la moindre lecture en `--jq`**, mesuré le 2026-08-18 sur un
dépôt inexistant : quand GraphQL rend un bloc `errors`, **`gh api` ignore le `--jq`** et recrache le
JSON brut sur la sortie standard (son message partant sur stderr, que `gh_graphql_read` jette). La
branche « erreur » d'un programme jq **ne s'exécute donc jamais dans le cas qu'elle est censée
couvrir** : la réponse arrive non filtrée, l'`awk` qui la projette n'y reconnaît aucune de ses clés,
et le verbe rend « aucune dérive » avec un code 0. D'où `st_erreur_graphql`, qui teste la **forme**
(un TSV ne commence jamais par `{`, une réponse JSON toujours) et non le contenu ; les branches
« erreur » des programmes jq restent en seconde ligne, pour le cas — permis par le schéma — d'un
`data` nul **sans** bloc `errors`, qui est celui d'un `repositoryOwner` inconnu.

Une limite assumée, à ne pas lire comme un oubli : **§3 ne relit pas le champ pour son compte**. Les
options viennent de `pj_resoudre` (#361), qui les a déjà lues et mémorisées pour le reste du bilan.
Ce lot en avait écrit une seconde lecture (`st_options`), les deux ayant été menés **en parallèle**
et chacun ignorant l'autre ; le doublon est tombé à la fusion. Deux lectures de la même donnée, ce
sont deux formulations à tenir d'accord — et une seule des deux qu'on penserait à corriger.

**Le pré-requis, et pourquoi ce n'est pas une case à cocher.** Le compte du projet s'authentifie par
un jeton **fine-grained** (`github_pat_…`), et un tel jeton **ne peut pas** écrire dans un projet
appartenant à un compte : la liste des « Account permissions » d'un jeton fine-grained **ne contient
aucune entrée « Projects »** — GitHub n'en propose qu'au niveau **organisation**. Chercher à
l'accorder est une impasse, pas un oubli de manipulation.

Il faut donc un jeton porteur du **scope `project`** (« read/write access to user and organization
projects »), qui n'existe que sur les jetons **classiques** et les jetons **OAuth**. Deux voies :

| Voie | Geste | Remarque |
|---|---|---|
| **OAuth par `gh`** (recommandé) | `gh auth login --hostname github.com --scopes project` | rien à stocker ni à faire expirer ; à lancer avec le `GH_CONFIG_DIR` du projet (§7.4) pour ne pas toucher les autres comptes de la machine |
| **Jeton classique** | cocher `project` (+ `repo` pour le dépôt privé) sur https://github.com/settings/tokens, puis `gh auth login --with-token` | secret long à gérer et à renouveler |

Une troisième voie existe et n'a pas été retenue : transférer dépôt et projet à une **organisation**,
dont les projets sont couverts par un jeton fine-grained. Elle règle le sujet du jeton en en ouvrant
un autre (migration du dépôt, comptes, facturation).

Deux pièges à connaître :

- **La lecture passe déjà.** Lister les projets du compte répond normalement (liste vide) avec le
  jeton fine-grained ; seule une **écriture** révèle le refus (`FORBIDDEN — Resource not accessible
  by personal access token`). Un diagnostic qui se contenterait de lire conclurait donc à tort que
  tout va bien, et c'est pourquoi le script tente une écriture réelle plutôt que de sonder.
- **`gh auth status` n'imprime aucun scope pour un jeton fine-grained.** Le blocage est donc
  invisible en dehors de l'erreur ci-dessus — là où un jeton classique ou OAuth affiche ses scopes
  et laisse voir `project` manquant d'un coup d'œil.

**Les sept verbes du cycle de vie (#360, bascule #364, commutateur retiré par #365).** `lib.sh` lit
et écrit ce champ, et rien d'autre : les quatre **unitaires** (`set-workflow`, `issue-owner`,
`begin`, `liberer-ticket`) et les trois lectures **d'ensemble** (`backlog-table`,
`milestone-issues`, `workflow-derives`, #362 — §3.6). Ils ont porté un temps le commutateur
`MAESTRO_CYCLE=status|labels` qui choisissait leur backend ; il n'en reste qu'une délégation d'une
ligne vers `st_*`, la couture restant là où les trois backends successifs se sont greffés. Deux
détails structurants : le Status vit sur l'**item de projet**, si bien qu'un ticket absent du projet
n'a **aucun état** (l'écriture le refuse en le disant ; le peuplement est §3.7, sa détection #363) ;
et **plus aucun label n'est touché** — les six `workflow::*` ont disparu du dépôt avec #365. Détail
complet et tests : #366.

---

### 3.6 Les lectures d'ensemble sur le Status, et ce qu'elles coûtent

Le §3.5 a porté l'**unité** — lire et écrire l'état d'**un** ticket. Celui-ci porte l'**ensemble**,
où était la charge du chantier : quatre consommateurs ont changé de source.

| Consommateur | Ce qu'il demande |
|---|---|
| `/backlog` | tous les tickets ouverts, groupés par état |
| `queue.sh` → `/orchestrate` | les « À faire » **et libres** d'un jalon, dans l'ordre |
| `doctor.sh` | les tickets sans état (dérive 4c) |
| `reconcile-workflow` → `worktree.sh gc`, `/branch-cleanup` | les fermés dont l'état est resté actif |

**Aucun des quatre n'a changé d'une ligne**, et c'est un résultat, pas une chance : aucun ne parle au
réseau. Tous lisent la colonne `statut` de **deux tables plates** — `backlog-table` et
`milestone-issues`, les primitives inventoriées en tête de `scripts/gitlab/lib.sh`. Basculer ces deux
producteurs (plus `workflow-derives`, qui compte au lieu de projeter) bascule **sept** appelants d'un
coup, `subtickets`, `startables` et `reconcile-en-cours` compris.

**La méthode est un recouvrement, pas une réécriture.** Le JSON des tickets reste la source de *qui
existe* ; une carte `iid → Status`, paginée sur les items du projet, dit *quel état*. Le contraire —
lister les tickets **depuis** les items — ferait disparaître de `/backlog` tout ticket hors projet,
c'est-à-dire exactement ceux qu'on veut voir signalés. Un ticket hors projet sort donc « - », ce que
rendait déjà, au caractère près, un ticket à 0 label du cycle de vie.

**Un verbe n'est délibérément pas recouvert** : `backlog` (le JSON brut), dont le contrat est de
rendre la réponse de la forge *telle quelle*. Conséquence à connaître : on n'y lit **aucun état**,
l'état ne vivant pas sur l'issue. Qui veut l'état lit la table.

#### Le coût, mesuré — et pourquoi il y a un cache

C'était le seul risque technique identifié du chantier. Mesuré le 2026-08-18 contre un projet de
mesure jetable peuplé des 367 tickets du dépôt (le peuplement du vrai projet est #361), médianes sur
3 exécutions **alternées** entre les deux modes :

| | `labels` | `status` |
|---|---|---|
| `queue.sh --milestone` (plan complet) | 23,6 s | 35,8 s — **+12,2 s (1,51×)** |
| appels à l'API sur ce plan | 11 | 16 — **+5** |
| la carte seule (366 items, pages de 100) | — | 12,7 s |

**Le coût n'est pas dans le nombre d'appels mais dans leur prix unitaire.** Une page de 100 items de
Projects v2 coûte **2,06 s**, contre **1,52 s** pour une page de tickets — et le champ n'y est pour
rien : la même page *sans* `fieldValueByName` coûte 2,18 s. Cinq appels (une résolution du projet par
son titre, puis quatre pages) font donc 12,7 s à eux seuls.

**D'où le cache**, que ce paragraphe justifie plutôt qu'il ne l'annonce : `queue.sh` demandait
**deux** cartes identiques, une par table. La carte est donc mémorisée pour la durée du **processus**
(13 appels et 26,6 s pour deux tables sans elle ; 8 appels et 15,1 s avec). Trois choses à en savoir
avant d'y toucher :

- **Elle se remplit dans le shell appelant, jamais par substitution.** `carte="$(…)"` s'exécute dans
  un sous-shell où l'affectation meurt avec lui — écrit ainsi, le cache mesurait exactement zéro
  gain. Les verbes appellent `st_carte_charge` puis lisent la variable ; `queue.sh` demande ses deux
  tables par **redirection**, donc dans un seul et même shell.
- **Elle est oubliée à l'écriture** (`st_set_workflow`), ce qui règle la péremption par construction
  au lieu de la confier à un raisonnement appelant par appelant.
- **Elle ne s'étend pas aux verbes unitaires.** `gl_issue_owner` est appelé par `run.sh` pendant des
  heures, sur des tickets dont l'état change entre deux appels : c'est le contraire de ce cas-ci.
  `MAESTRO_CYCLE_MEMO=0` éteint le cache.

Reste **+12 s au démarrage d'un run**, qui les absorbe (le plan se calcule une fois, la boucle dure
des heures), et une croissance d'environ **2,6 s par tranche de 100 tickets** — à re-mesurer au
moment de la bascule, le dépôt en comptant 367 aujourd'hui.

#### Ce que ça laisse aux lots suivants

- **#363** — la dérive change de *nature* : un champ à valeur unique rend « ≥ 2 états » impossible
  **par construction** (c'est le gain du chantier, et une dérive de moins à traquer). Il ne reste que
  « 0 », qui recouvre désormais **deux** causes à distinguer, « hors projet » et « Status vide ».
  `workflow-derives` porte le portage ; le diagnostic est là-bas.
- **#364** — la bascule du défaut, et ce lot lui a trouvé son prérequis. Rejouée contre le **vrai**
  projet une fois #361 passé, la comparaison rendait 7 consommateurs sur 9 en écart nul et
  **2 tickets divergents sur 100** — « En revue » côté labels, « En cours » côté Status. Aucun
  rapport avec les lectures : tant que le défaut valait `labels`, `/ticket-finish` appelait
  `set-workflow`, qui n'écrivait **que le label**, si bien que le champ Status vieillissait à chaque
  changement d'état. La dérive était donc **structurelle et repartait d'elle-même**, ce qu'un simple
  rattrapage ne règle pas : #361 a peuplé l'**appartenance** au projet, la bascule demandait en plus
  une **resynchronisation des valeurs** juste avant de basculer. Elle a été faite le jour J
  (`project-backfill --realigner`, §3.8), et le sens de la dérive s'est inversé avec l'autorité :
  ce sont les **labels** qui vieillissent désormais.

### 3.7 Peuplement du projet — tout ticket est un item

Le socle de §3.5 monte un projet **vide**. Or le Status vit sur l'**item de projet** et non sur
l'issue : un ticket qui n'est pas dans le projet n'a **aucun état**, et aucune requête de cycle de
vie ne le voit — **en plus silencieux** qu'un ticket sans état, puisque rien à l'écran ne le
distingue d'un ticket absent du filtre. #361 a traité cette panne **avant qu'elle existe**.

**`lib.sh project-add <iid> [valeur]`** — le seul verbe de peuplement. `/ticket-create` l'appelle
dans la foulée de la création, et c'est **cet appel qui donne son état au ticket** : rien côté forge
n'en pose par défaut, et depuis le retrait des labels (#365) il n'y a plus de donnée de secours.
Défaut « À faire », idempotent (`addProjectV2ItemById` rend l'item existant au lieu d'échouer), et
son échec ne défait pas la création — le ticket existe, on le signale et on rejoue. C'est aussi la
**réparation** d'un ticket que `doctor.sh` signale hors projet ou sans état.

Trois décisions à ne pas défaire :

- **Le projet se résout par ÉGALITÉ de titre, jamais par recherche.** `projectsV2(query:)` est une
  recherche **floue** côté GitHub, alors que le titre du projet est une **clé** (§3.5) : la
  comparaison se fait donc dans le shell, en égalité stricte. Ce n'est pas une précaution
  théorique — le compte porte déjà un second projet nommé `Maestro-mesure-362`, et un préfixe aurait
  suffi à peupler le mauvais.
- **Peupler le projet n'est pas décider du cycle de vie.** C'est poser la **donnée de plus** sans
  laquelle il n'y aurait rien où l'écrire, et c'est ce qui a permis à ce lot de précéder la bascule
  de #364 — sans quoi celle-ci aurait trouvé un projet vide et autant de tickets sans état. C'est
  aussi pourquoi ce verbe n'a jamais été derrière le commutateur, du temps où il en existait un.
- **Le backfill est parti avec les labels (#365), et ce n'est pas un oubli.**
  `lib.sh project-backfill` ajoutait au projet les tickets existants et posait leur Status
  **d'après leur label `workflow::` courant**, et de rien d'autre : la bonne source tant que le
  label faisait foi, une photo périmée après la bascule, et plus aucune source après leur retrait.
  Son dernier usage légitime — `--realigner`, pour préparer un retour sur les labels — a disparu
  avec le retour lui-même. Ce qui restait de lui sans les labels (« ce ticket est-il un item ? »)
  est une question de **détection**, que #363 a donnée à `doctor.sh` ; la réparation est
  `project-add`, ticket par ticket. Ne pas le réécrire « en masse » : un verbe qui poserait un état
  par défaut sur des tickets anciens **inventerait** la donnée qu'on vient de perdre.


### 3.8 La bascule — le champ Status fait autorité (#364)

Le **2026-08-19**, `MAESTRO_CYCLE` est passée à **`status`** par défaut : sans aucune variable
d'environnement, tout le workflow — `/ticket-create`, `/ticket-start`, `/ticket-ship`, `/backlog`,
`/orchestrate`, `doctor.sh` — lit et écrit le champ **Status** du projet Projects v2. C'est le lot 6
du chantier #358, minuscule en diff (`${MAESTRO_CYCLE:-labels}` → `:-status}`) et le seul qui change
quelque chose pour tout le monde d'un coup — exactement comme #343 l'avait fait pour la forge.

**La bascule est un changement de défaut, pas de code.** Les deux backends étaient complets depuis
#360 (l'unité) et #362 (les lectures d'ensemble) ; il ne restait qu'à décider lequel répond quand
personne ne demande rien. C'est aussi pourquoi **aucune commande `.claude/` n'a changé de geste** :
le vocabulaire (§3.1) est commun aux deux backends, si bien qu'un `set-workflow <iid> "En revue"`
écrit désormais ailleurs sans que son appelant le sache. Une seule a été retouchée, et sur sa prose
seulement — `/ticket-create`, parce que la bascule y change ce qu'un échec de `project-add` veut
dire (voir plus bas).

#### Le prérequis, et pourquoi il n'était pas une formalité

**Le backend qui ne fait pas autorité est celui qui dérive.** Tant que le défaut valait `labels`,
`/ticket-finish` appelait `set-workflow`, qui n'écrivait **que le label** — le Status vieillissait
donc à chaque changement d'état, structurellement (§3.6). Basculer sans resynchroniser aurait
promu une photo périmée au rang d'autorité.

Constat juste avant la bascule, sur 369 tickets : **5 divergents** (#360, #361, #362, #363, #364 —
tous les lots du chantier, ceux dont l'état avait le plus bougé) et **2 absents** du projet (#375,
#377, créés depuis le backfill). Réalignement en un geste, puis écart nul :

```bash
bash scripts/gitlab/lib.sh project-backfill --check       # la liste, sans écriture
bash scripts/gitlab/lib.sh project-backfill --realigner   # Status ← labels
```

Restent **4 tickets sans état**, et ce n'est pas une dérive : `#19`, `#20`, `#201` et `#241` sont
les **bouche-trous d'import** (label `import::bouche-trou`, fermés, numéros réservés par #340 pour
aligner la numérotation GitHub sur GitLab). Ils n'ont jamais eu de cycle de vie et n'en auront pas.

#### La parité, rejouée le jour même

Le critère n'était pas « ça marche » mais « ça rend la même chose ». Les quatre lectures, jouées
dans les deux modes contre le vrai backlog, **au caractère près** (`diff` sur la sortie brute) :

| Lecture | Écart |
|---|---|
| `backlog-table opened` (→ `/backlog`) | **nul** (44 tickets) |
| `milestone-issues` du jalon courant | **nul** (24 tickets) |
| `queue.sh` — le plan complet (→ `/orchestrate`) | **nul** (6 tickets) |
| `workflow-derives` (→ `doctor.sh`) | **nul** (aucune dérive des deux côtés) |

**Coût re-mesuré** sur `queue.sh`, comme le demandait §3.6 : **36,3 s** en `labels` contre
**52,7 s** en `status`, soit **+16,4 s (1,45×)** sur 369 tickets — l'écart prédit par la mesure de
#362 (1,51× sur 367 tickets), absorbé par un démarrage de run qui dure ensuite des heures.

#### Le retour arrière a existé un lot, puis il a été retiré (#365)

`MAESTRO_CYCLE=labels` restaurait le comportement d'avant, **intégralement** — c'est ce qui rendait
la bascule réversible, et c'est pourquoi les labels n'ont été retirés qu'au lot suivant. **#365 les
a retirés**, avec le commutateur et le backend qui les servait : revenir coûterait désormais une
migration, pas un `export`.

Le raisonnement qui l'a permis, et qu'il faut garder en tête avant d'imaginer un jour un second
support : **l'`export` restaurait le code, pas les données.** Dès la bascule, plus personne ne
mettait les labels à jour — ils étaient figés à l'état où elle les avait laissés. Un retour durable
aurait donc demandé une resynchronisation **en sens inverse** (Status → labels) qu'aucun verbe ne
faisait. Le retour arrière était réel pour un dépannage d'une heure, théorique au-delà ; c'est ce
qui a permis de le retirer un lot plus tard sans rien perdre.

Corollaire qui n'a pas changé : `lib.sh backlog` (le JSON brut) rend la réponse de la forge telle
quelle. **On n'y lit aucun état** — il ne vit pas sur l'issue. Qui veut l'état lit la table
(`backlog-table`) ou `issue-owner`.

#### Ce que les tests en ont retenu

Les suites d'outillage montent un dépôt jetable avec un `gh` factice. Le temps de la bascule,
`tests/conftest.py` **épinglait** `MAESTRO_CYCLE=labels` — cinquième garde-fou, et le seul qui
posait une valeur au lieu de vider — parce que le double ne savait pas répondre aux requêtes
GraphQL de Projects v2. #365 a retiré l'épinglage avec le commutateur, et **porté les doubles** :
les lectures du backend Status passant par `gh api graphql --jq`, où le programme jq fait tout
l'aplatissement, les fixtures rendent désormais le résultat **déjà aplati** (des lignes `clé<TAB>…`
copiées des en-têtes de `st_jq_contexte` et `st_jq_items`). Un double qui rendrait du JSON là
traverserait le filtre en silence et le verbe lirait zéro ligne — « ticket sans état », c'est-à-dire
un feu vert sur une question jamais posée.

Ce qui est parti avec les labels : `tests/test_cycle_de_vie.py`, dont le sujet **était** l'exclusion
mutuelle des six labels — la classe de bug que le champ à valeur unique rend impossible par
construction. Son pendant sur le champ Status a été écrit au lot #366, sous le même nom et sur un
autre sujet : **§3.9**.

#### Ce que la bascule laisse ouvert

**Un ticket ouvert depuis l'interface web n'a plus d'état.** Les gabarits `.github/ISSUE_TEMPLATE/`
posaient `workflow::a-faire` par leur en-tête YAML (§4) — c'était la réponse de #344 à la dérive
« zéro label », et elle ne valait déjà plus rien depuis la bascule : un en-tête de gabarit sait
poser un label, pas ajouter l'issue à un projet. #365 a retiré ce label des quatre gabarits, où il
ne faisait plus que **masquer** la dérive qu'il ne réglait pas. Le ticket naît donc **hors projet**,
sans Status, invisible de `queue.sh` et rendu « - » par `/backlog`. Trois choses à en savoir :

- `/ticket-create` n'est **pas** concerné : il appelle `project-add` dans la foulée de la création
  (#361), et c'est la voie normale. Son prompt a toutefois été retouché — l'échec de `project-add`
  y était traité comme bénin *parce que* rien ne le lisait ; il laisse désormais un ticket sans
  état, et depuis #365 plus rien ne le rattrape, donc il se rejoue tout de suite au lieu de finir
  en note de bas de résumé.
- La dérive est **rattrapable en un geste** (`lib.sh project-add <iid> "<état>"`, qui ajoute et
  pose l'état) et **détectée** — c'est le sujet de #363.
- La supprimer à la source demanderait un mécanisme côté projet (workflow d'auto-ajout de Projects
  v2, ou une GitHub Action sur `issues: opened`), du même ordre que #377. Rien n'a été improvisé ici :
  la bascule constate, elle n'invente pas un dispositif que personne n'a arbitré.

### 3.9 Ce qui garde le dispositif — [`tests/test_cycle_de_vie.py`](../tests/test_cycle_de_vie.py) (#366)

Lot final du chantier #358 : les sept lots précédents ont différé leurs tests ici (§5.1), et ce
module est l'endroit où le dispositif rend des comptes. Il monte un **dépôt jetable** avec un `gh`
factice — **ni réseau, ni compte de forge, ni projet réel** (§8), les mutations Projects v2 se
testant sur un double.

| Lot | Ce que le module garde |
|---|---|
| #359 | les six options du champ **dans l'ordre du flux** ; l'idempotence du monteur ; son garde-fou sur un projet peuplé ; le projet retrouvé par **égalité de titre** |
| #360 | l'aller-retour libellé → option → libellé sur les **six** états ; l'asymétrie écriture/lecture ; l'ordre de `begin` ; **aucun identifiant figé** |
| #361 | `project-add` rejouable, la valeur **nommée** et jamais devinée, l'appel dans la foulée de la création, le demi-échec qui se dit |
| #362 | le recouvrement des tables par la carte, un ticket hors projet rendu « - » **sans disparaître**, la mémoire et sa péremption, le tube qui ne masque pas d'échec |
| #363 | les six options validées, l'option manquante nommée, le ticket sans état nommé — et **aucune dérive** sur un dépôt sain |
| #364 | le support est le champ Status sans qu'aucun réglage soit à poser |
| #365 | plus aucun label `workflow::` ni commutateur `MAESTRO_CYCLE` — prouvé par `grep` sur le dépôt |

**Trois contrôles sont des `grep` sur le dépôt**, et c'est délibéré : « aucun identifiant en dur »,
« plus aucun label du cycle de vie » et « plus aucun commutateur » sont des promesses sur ce que le
dépôt **ne contient pas**, qu'aucun cas de test ne peut attester. Chacun **prouve son motif** sur un
échantillon fautif écrit à côté, avant de balayer : un `grep` qui ne trouve rien parce qu'il ne
cherche rien est le pire des ✓ — c'est la panne de #341, dans un autre fichier.

Deux **exclusions** en découlent, et aucune n'est un trou. `scripts/migration/` nomme les six labels
parce qu'il lit l'**archive GitLab**, où ils ont réellement porté l'état : leur interdire ce nom
reviendrait à leur interdire de lire ce qu'ils exportent. Et les fichiers qui **racontent** le
retrait — l'en-tête de `lib.sh`, `tests/conftest.py`, la présente section — mentionnent
`MAESTRO_CYCLE` au passé ; les compter pour des résurrections rendrait le contrôle insatisfaisable
autrement qu'en effaçant la mémoire du chantier. Le motif cherche donc un **usage** (`${MAESTRO_CYCLE…}`,
`MAESTRO_CYCLE=…`), jamais une mention.

**Deux lignes du cadrage de #366 étaient caduques à son ouverture**, et le module le dit plutôt que
de les jouer : « le retour arrière `MAESTRO_CYCLE=labels` prouvé » et « le backfill rejouable,
reprenable » ont été écrits avant #364/#365, qui ont retiré l'un et l'autre (§3.8). Prouver un
retour arrière qui n'existe plus reviendrait à le réintroduire ; ce qui est gardé à la place est que
le support est **unique**, et que le peuplement se fait **à l'unité**.

**Le module est écrit pour survivre au merge de #363**, qui ne l'était pas quand il a été écrit.
Ce lot refait §4c sur une lecture **centrée ticket** — une requête qui sait dire d'un ticket qu'il
n'est dans **aucun** projet, là où `main` interroge la carte du projet — et sépare en deux causes
(« hors projet » / « Status vide ») ce que `main` fond en un message. Les assertions portent donc
sur ce que les deux versions garantissent **également** : le ticket est nommé, le geste de
réparation est nommé, un ticket qui a un état ne l'est pas. Épingler la formulation ferait échouer
le contrôle sur un merge qui ne change rien à ce qu'il garde.

**Et l'intersection est mesurée, pas supposée** : le module a été joué des deux côtés — `main` tel
quel, puis `main` + la branche de #363 restaurée (`git restore --source=<branche> --worktree
scripts/gitlab/lib.sh scripts/gitlab/doctor.sh`) —, **67/67 dans les deux cas**. C'est la méthode à
reprendre pour tout lot final dont un frère est encore « En revue » : le double sert alors les
**deux** lectures, chacune prenant la règle qui lui répond. Ce que #363 ajoute en propre — les deux
causes distinguées par leur message, l'option en trop, les options dans le désordre, ses deux gardes
`st_erreur_graphql` — se couvre **avec lui**, dans ce même module.

**Le harnais est partagé, et il en a été sorti** : dépôt jetable, `gh` factice et fabriques de
réponses vivent dans [`tests/harnais_forge.py`](../tests/harnais_forge.py), d'où
`test_collaboration.py` les importe aussi. Le recopier aurait fait deux `gh` factices à tenir
d'accord, et le premier symptôme de deux doubles est une suite verte sur une forme de réponse que
l'autre a corrigée depuis. Un point à connaître avant d'y toucher : la fixture `depot` est une
**fabrique** (`monte_depot`) que chaque suite enrobe en deux lignes — importer une fixture la met en
collision avec le paramètre `depot` de chaque test aux yeux de ruff (112 `F811` sur la seule
`test_collaboration.py`).

---

## 4. Gabarits de tickets et de Pull Request

- **Tickets** (`.github/ISSUE_TEMPLATE/`) : `feature.md`, `bug.md`, `doc.md`, `infra.md` — un par
  valeur de `type::*`. Posent la structure attendue (contexte, critères d'acceptation) et, par leur
  **en-tête YAML**, les labels par défaut du gabarit : `type::*` et `prio::moyenne`. Ils y ont porté
  un temps `workflow::a-faire` — la réponse de #344 à la dérive « zéro label » —, retiré par #365
  avec les six labels : un en-tête de gabarit sait poser un label, pas ajouter l'issue à un projet,
  si bien qu'il ne faisait plus que **masquer** la dérive qu'il ne réglait pas (§3.8).
  `/ticket-create`, lui, retire cet en-tête et pose les labels par `--label` — une création en ligne
  de commande n'a que faire d'un bloc qui sert l'UI web. Le label `agent::*` reste à ajouter
  manuellement au triage (aucun gabarit ne peut deviner quel agent est concerné).
- **Pull Request** (`.github/pull_request_template.md`) : checklist de definition
  of done + rappel `Closes #`. La checklist est un **constat, pas un formulaire** :
  `/ticket-finish` coche lui-même les cases qu'il a **effectivement vérifiées** (conventions de
  branche/commit, tests et doc jugés d'après le diff, pipeline verte constatée via `lib.sh
  pipeline-latest`) et laisse vides les autres — notamment « Pipeline CI verte », qui est
  **normalement vide au premier passage** : la CI ne démarrant qu'avec la PR (§8), le pipeline
  naît après le constat. En cas de re-exécution, il remet la checklist à jour dans la
  description de la PR sans toucher au reste (idempotent) et **ne décoche jamais** une case déjà
  cochée (elle peut venir d'un humain). Les cases restées vides sont l'affaire du relecteur.

---

## 5. Cycle de vie d'un ticket

```
/ticket-create ──▶ À faire ──/ticket-start──▶ En cours ──/ticket-finish──▶ En revue ──(merge-mr)──▶ Terminé
                      │                            │                             │
                      └────────────────/ticket-abandon────────────────┘         (refus : la PR
                                   (Abandonné / Doublon)                         reste ouverte ici)
```

⚠ **La flèche du merge n'attend plus personne** (#418, §6) : `/ticket-finish` — donc aussi
`/ticket-ship` — attend le pipeline puis appelle `merge-mr`, et le passage à « Terminé » est posé
par le workflow `issues: closed` sur le `Closes` de la PR (§9.2), sans geste local. « En revue »
n'est donc plus une salle d'attente mais un **état de passage**, où le ticket ne s'attarde que si
`merge-mr` a refusé — et alors sa PR reste **ouverte**, avec sa cause.

(les noms ci-dessus sont les **libellés** du cycle de vie, portés par le champ Status, §3.1)

0. **`/ticket-create <type> <titre>`** — crée un ticket bien formé (corps de template, labels
   `type::`/`agent::`/`prio::`), cycle de vie `À faire` posé dans le même appel. Ne crée pas de
   branche.
1. **À faire** — le ticket existe et porte cet état, personne ne travaille dessus.
2. **`/ticket-start <iid>`** — crée/checkout la branche, assigne le ticket à l'exécutant, passe
   le **cycle de vie** à `En cours`. Le chemin nominal tient en deux helpers et un bloc git (refonte
   ticket #60, pour réduire la cérémonie et le contexte réinjecté à chaque démarrage) :
   - **`lib.sh start-brief <iid>`** — tout le préflight en un appel et **une seule lecture du
     ticket** (une unique requête GraphQL, rejouée pour toutes les projections) : pré-requis
     (`gh` authentifié), arbre propre, brief compact (titre/labels/critères — l'essentiel pour
     cadrer, la description intégrale reste disponible via `lib.sh issue-raw <iid>` en cas de doute),
     détection parent de suivi / sous-ticket (rang de lot, tests différés, contrôle des lots
     précédents — §5.1) et branche proposée (préfixe dérivé du label `type::` §1, slug du titre).
     Le helper est **informatif** : les avertissements sont dans sa sortie, la décision — démarrer,
     rediriger, s'arrêter, proposer un découpage — reste à l'agent.
   - **`worktree.sh ensure <iid>`** — monte le worktree du ticket et dit où la session doit
     travailler (§9.1). C'est le **point de passage obligé** du démarrage, et c'est à ce titre
     qu'il remet le dépôt à niveau au passage, sans qu'aucun geste soit à retenir : `main` avancée
     sur `origin/main` (§9.3), dépendances ajoutées au dépôt (§9.4), worktrees soldés ramassés
     (§9.2) et **branches locales déjà mergées purgées** (§9.5 — même garde-fou que
     `/branch-cleanup` : uniquement celles dont la forge confirme la PR `merged`, §6). Il **signale**
     au même passage les tickets « En cours » que plus personne ne mène (§9.6) — consultatif : la
     reprise est un geste explicite, et le ticket qu'on est en train de démarrer en est écarté. Les
     cinq sont best-effort et muets quand il n'y a rien à faire ; aucun ne bloque un démarrage.
   - **`lib.sh start-branch <branche>`** — place le dépôt sur la branche de travail. Après
     `ensure` c'est en général sans effet (la branche est déjà celle du worktree) ; il reste la
     source unique du placement et couvre le cas d'une reprise dans le clone principal.
   - **`lib.sh begin <iid>`** — assignation (username auto-résolu via `gh api user`, parsé en
     shell pur — pas de dépendance à `jq`/`python`, et couvert par l'allowlist §7.1 pour ne pas
     déclencher de prompt), cycle de vie « En cours » (posé dans le champ **Status** de l'item de
     projet, §3.1) et dates début/échéance (§3.3), groupés en un seul appel. Les sous-commandes
     unitaires (`current-user`, `set-workflow`, `start-dates`…) restent disponibles pour les
     autres commandes et les cas hors nominal.
   Une fois le cadrage résumé, l'agent **enchaîne directement sur l'implémentation** — le résumé
   n'est pas une pause d'autorisation, aucun « go » n'est attendu.
3. Développement sur la branche (commits `Refs #<iid>`).
4. **`/ticket-finish`** — pousse la branche, ouvre (ou passe en "Ready") la PR avec
   `Closes #<iid>`, **coche dans sa checklist les cases qu'il a pu vérifier** (§4), passe le
   **statut** à `En revue`.
   - **Raccourci « zéro friction » : [`/ticket-ship`](../.claude/commands/ticket-ship.md).** Quand
     le travail est terminé mais **pas encore committé**, `/ticket-ship` enchaîne **en une seule
     action** : il **commite d'office** les changements en attente (message Conventional Commits
     généré + `Closes #<iid>`, **sans confirmation** — même parti pris que l'auto-estimation du temps,
     §3.3) puis **délègue à `/ticket-finish`** (source unique du push/PR/statut/temps ; son étape de
     commit est alors sans objet, l'arbre étant propre). Il **refuse** si l'arbre est **vide** (rien à
     committer → utiliser `/ticket-finish`) ou **en conflit**, et **jamais sur `main`**. Le hook
     `commit-msg` (§2) reste appliqué — pas de `--no-verify`. Pensé pour la **boucle d'orchestration**
     (ticket #34) : moins d'allers-retours manuels à chaque clôture.
   - **Garde-fou commun aux deux** : avant la moindre écriture, elles vérifient que le ticket visé
     est bien celui de la session — iid cohérent avec la branche courante, ticket non assigné à
     quelqu'un d'autre (`lib.sh close-guard`, §6). Sinon elles s'arrêtent en nommant le motif ;
     seule une demande explicite de l'utilisateur permet de passer outre.
   - Dans les deux cas, la clôture passe par **ces commandes et rien d'autre** : ne pas
     ré-implémenter le cycle à la main (`git commit`/`git push`/`gh pr create` ad hoc) — elles en
     sont la source unique (ticket #37).
5. **Revue + merge** — **toujours une action humaine** (voir garde-fous, §6). Le merge fait
   deux choses **automatiquement** : il **ferme** le ticket (via `Closes #`) et **supprime la
   branche distante** (case « Delete source branch », pré-cochée — décochable au merge si on
   veut garder la branche). Il **ne touche pas au cycle de vie** : depuis #207 c'est un label, et
   la fermeture d'une issue n'en pose aucun — le ticket reste donc affiché « En revue » jusqu'à
   l'étape 6. Cet écart transitoire est normal ; c'est sa persistance que `doctor.sh` signale
   (ticket fermé au cycle de vie encore actif).
6. **`/branch-cleanup`** — GitLab a géré le distant (étape 5), cette commande fait le **ménage
   local** et **pose le cycle de vie `Terminé`** : supprime la branche **locale** mergée (par
   `lib.sh cleanup-merged`, le même helper que l'automatisme — §9.5) et
   remet `main` à jour. Ce ménage est en grande partie **automatisé** : `worktree.sh ensure`, qu'
   appelle tout `/ticket-start` (étape 2), purge les branches mergées et ramasse les worktrees
   soldés, qui disparaissent donc d'eux-mêmes au fil de l'eau (§9.2, §9.5 — l'automatisme a
   longtemps été accroché à `start-branch`, où il était devenu injoignable : voir §9.5).
   `/branch-cleanup` reste utile pour un nettoyage **à la demande**
   (ex. sans démarrer de nouveau ticket) ou pour supprimer aussi la branche distante si la case
   « Delete source branch » avait été décochée au merge.

### 5.1 Découpage en sous-tickets — besoins trop gros & tests différés

Un ticket doit tenir en **~1 session de travail** (§1, règle 4) — chaque session `/ticket-start`
reste ainsi légère en contexte. L'évaluation de taille se fait en **charge estimée**, sur la
**description intégrale** — notes techniques et références croisées comprises, pas seulement le
nombre de critères d'acceptation. Les **couches/composants distincts** touchés (moteur, backend,
UI, script, commande, doc…) sont un **signal d'alerte** qui oblige à estimer finement, **pas un
déclencheur automatique** (recalibrage ticket #63 : le découpage a un coût fixe par lot — cycle
branche/PR/pipeline/merge complet, session repartant à froid — à ne payer que s'il évite une
session qui déborde). Étalon : le **#48** n'affichait que 3 critères mais ses notes techniques
annonçaient trois couches substantielles (moteur/file #41, backend Control Tower #46, UI #47) —
il aurait dû être découpé (correctif ticket #54) ; à l'inverse, un script + sa doc tiennent en
une session et restent un **ticket unique**, au besoin avec une **checklist interne** dans sa
description (pas de parent ni de sous-tickets). Au-delà d'une session (plusieurs couches
substantielles, plus de 3-4 critères d'acceptation, plusieurs livrables indépendants), le besoin
est porté par un **ticket parent de suivi** + des **sous-tickets** (introduit par le ticket #53) :

- **Parent de suivi** — pas de branche, pas de code, pas de PR. Sa description porte l'objectif
  global et une section `## Sous-tickets` : la checklist **ordonnée** (ordre de réalisation) des
  lots, au format `- [ ] #<iid> — <titre>`. Il reste ouvert tant que toutes les cases ne sont pas
  cochées — **en particulier celle du lot tests final** — et sa fermeture est une **décision
  humaine/orchestrateur** (pas de PR → pas de `Closes #` automatique). Les cases sont cochées au
  fil de l'eau par les commandes (synchronisation idempotente : cocher les lots « Terminé »,
  jamais décocher).
- **Sous-tickets** — un lot = ~1 session, **1 à 3 critères d'acceptation**, et surtout chaque lot
  est **mergeable directement sur `main` sans casser l'existant** (code additif ou inoffensif tant
  que les lots suivants manquent). La description de chaque sous-ticket **commence par**
  `Sous-ticket de #<parent> — lot <n>/<total>.` (marqueur parsé par `lib.sh parent-of`), et le
  sous-ticket est **lié** au parent (issue link « relates to », posé par
  `lib.sh issue-link <parent> <sous-iid>`). C'est cette propriété (lots additifs, branchés depuis
  `main`) qui permet d'**enchaîner les lots sans attendre le merge** du précédent : un lot
  « En revue » (PR ouverte) ne bloque pas le suivant, seul un lot encore « À faire » ou
  « En cours » l'arrête (recalibrage ticket #63).
- **Tests différés** — les tests sont un **sous-ticket dédié**, par défaut le **lot final
  « tests + doc »**. Les lots intermédiaires n'embarquent des tests que si leur logique est
  critique, et portent la mention « Tests différés → #<iid-du-lot-tests> » — livrer un lot
  intermédiaire sans tests est donc **prévu**, pas un oubli (la case « Tests » de la checklist de
  PR reste vide, le relecteur sait pourquoi).
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
| `/ticket-start` | **propose le découpage** au lieu d'enchaîner (vraie pause) | affiche **tous les lots démarrables** (`lib.sh startables`) et **redirige** vers le premier (en synchronisant la checklist) ; **rien à démarrer** ⇒ parent fermable si tout est « Terminé », sinon le travail est en route (« En cours ») ou livré et on n'attend plus que des merges | vérifie que les lots **précédents** de la checklist sont livrés (« Terminé » ou « En revue » — une PR en attente de merge ne bloque pas), **hors lots marqués `(parallèle)` quand le lot visé l'est aussi** ; sinon s'arrête |
| `/ticket-ship` | — | — | **annonce les lots démarrables** dès maintenant sans attendre le merge — plusieurs si des lots sont parallèles — (ou que le parent est fermable si c'était le dernier), et coche les lots terminés dans la checklist du parent |

**Voie « non réalisé ».** À tout moment (depuis `À faire`, `En cours` ou `En revue`), un ticket
peut être clos sans être réalisé avec **`/ticket-abandon <iid> [doublon]`** : statut `Abandonné`
(won't-do) ou `Doublon` (catégorie `canceled`), raison consignée en commentaire, ticket fermé.

**Supervision (lecture seule).** Deux commandes n'écrivent rien et servent à piloter, en attendant
la Control Tower (Phase 1) :

- [`/backlog`](../.claude/commands/backlog.md) `[opened|all]` — vue d'ensemble du backlog groupée
  par **cycle de vie** (§3.1), avec `agent::`/`prio::` et la mise en avant de ce qui **attend une
  revue / est prêt à merger**. S'appuie sur `lib.sh backlog` (requête canonique du backlog).
- [`/mr-review`](../.claude/commands/mr-review.md) `<mr|branche>` — synthèse d'une PR (aptitude au
  merge, pipeline, threads bloquants, résumé du diff) pour **éclairer un relecteur humain**. Depuis
  #418 la revue est un geste d'**après-merge** (§6), donc la commande éclaire le plus souvent une PR
  **déjà dans `main`** ; elle reste utile avant merge sur une PR qu'on reprend à la main. Conforme au
  garde-fou §6 : elle **ne merge, ne ferme, ni n'approuve jamais** — le chemin de merge est
  `lib.sh merge-mr`, et c'est le seul.

**Remédiation d'une PR.** [`/mr-fix`](../.claude/commands/mr-fix.md) `[mr|branche]` — quand une PR
n'est pas mergeable, pour l'une **ou l'autre** des deux raisons possibles. D'abord le **conflit avec
`origin/main`** (`lib.sh mr-conflict`), résolu par merge et jamais par rebase ; puis le **pipeline
rouge** : diagnostic des jobs en échec (traces synthétisées via les
helpers `lib.sh pipeline-*`), **correctif en local** quand c'est corrigeable (lint/test/typage),
commit (`Refs #<iid>`), push et suivi du nouveau pipeline jusqu'au verdict (2 tentatives max ;
re-déclenchement `gh run rerun <run-id>`, ou `gh workflow run ci.yml --ref <branche>` si le push
n'a pas déclenché d'exécution). Un échec d'infrastructure (secret manquant, flaky) est signalé tel
quel — au plus un `gh run rerun --failed <run-id>`, jamais de correctif
inventé. Elle écrit des **commits**, mais jamais le cycle de vie : ni statut, ni PR, ni merge (§6),
ni commit sur `main` (voir §8).

Détail des commandes : [`.claude/commands/`](../.claude/commands/).

---

## 6. Garde-fous

Cohérent avec le principe « autonomie sous supervision » du projet (voir [README](../README.md)) :

- **Aucun merge NON VÉRIFIÉ** (#417, chantier #413). C'est un **renversement assumé** du garde-fou
  qui tenait cette place depuis l'origine — « aucune commande ne merge, le merge est une décision
  humaine » —, et il porte sur un seul mot : ce qui disparaît n'est pas la vérification, c'est
  **l'attente d'un humain pour la faire**. Les prérequis n'ont pas sauté, ils ont changé de gardien.

  Ce gardien est **`bash scripts/gitlab/lib.sh merge-mr <iid|branche>`** (#415), **seul chemin de
  merge du dépôt**. Il refuse de merger tant que les **quatre prérequis** ne sont pas réunis, et
  rend **une cause par code** — c'est sur ce code que le pilote (§11) décide entre « repasser plus
  tard », « faire réparer » et « laisser à un humain » :

  | # | Prérequis | Ce qu'il empêche | Refus |
  |---|---|---|---|
  | 1 | une PR **ouverte**, **non brouillon**, qui **ferme le ticket** | un merge qui laisserait le ticket ouvert **et sans état** — plus personne ne le poserait, le workflow `issues: closed` (§9.2) n'ayant aucun événement à écouter | `6` — geste humain |
  | 2 | rien de **non poussé** sur la branche | merger **moins que ce qui existe**, la seule perte que rien ne rattrape | `6` |
  | 3 | **aucun conflit réel** avec `origin/main` — verdict `git merge-tree --write-tree`, jamais l'heuristique de `behind-main` ni le champ asynchrone de la forge (§8.3) | un merge qui casserait `main` | `5` → `/mr-fix` |
  | 4 | un **pipeline vert**, et vert **sur la tête de la PR** | le merge au rouge, et le faux vert d'un **run périmé** (le cas nominal juste après un push : le run précédent est fini, le nouveau n'a pas démarré) | `4` rouge → `/mr-fix` · `3` pas encore rendu → repasser |

  Le merge est un **squash**, et la branche distante part avec (`delete_branch_on_merge`, plus bas).
  `--check` rend le même verdict **sans rien écrire**. Le PUT porte le `sha` vérifié, si bien qu'une
  tête qui bouge entre le contrôle et le merge le fait **échouer** au lieu de passer en silence.

  ⚠ **`gh pr merge` reste refusé** — par la couche permissions **et** par `guard.sh` (§11.6) — et ce
  n'est pas une contradiction. Ces deux filets jugent le **texte de la commande qu'une session
  lance**, jamais ce qu'un script appelle en interne : le geste **nu** demeure donc impossible
  pendant que le geste **vérifié** passe. Lever le `deny` mettrait un merge au rouge à un `gh` près,
  pour zéro gain — aucun appelant légitime n'a besoin de la commande nue, `merge-mr` passant par
  l'API REST. ⚠ **L'auto-merge natif de GitHub n'est pas davantage une option** : `gh pr merge
  --auto` ne tient ses promesses que derrière une protection de branche, qui n'existe pas sur ce
  plan (§8.8) — activé tel quel, il mergerait **immédiatement**, pipeline rouge compris.

  **Deux conséquences, à ne pas enterrer** — elles ne sont pas des effets de bord, ce sont les
  décisions de cadrage du chantier (utilisateur, 2026-08-21) :

  - ⚠ **La revue avant merge disparaît de fait, et devient un geste d'APRÈS-MERGE.** Les PR ne sont
    plus ouvertes en Draft jusqu'à ce que quelqu'un les relise : elles entrent dans `main` dès
    qu'elles sont vertes. Ce qui disparaît est l'attente d'un humain pour **vérifier**, pas la
    vérification — qui vit tout entière dans le tableau ci-dessus. La file de revue de `/backlog` et
    `/mr-review` gardent leur usage et perdent leur place dans le cycle : on relit ce qui est déjà
    dans `main`, et un problème trouvé se corrige par un ticket, plus par un blocage de PR.
  - ⚠ **Ceci renverse « pas d'attente pipeline à la clôture ».** `/ticket-finish` attend désormais le
    verdict (~2-4 min, borné à 15 min par `pipeline-wait`) **avant** de merger, et `/ticket-ship`
    avec lui : ces commandes ne rendent plus la main dans la seconde. L'ancienne règle disait qu'une
    attente de pipeline en fin de ticket était du temps perdu — elle avait raison tant que le merge
    venait plus tard et de quelqu'un d'autre. Elle est **fausse depuis #418**, et on l'écrit plutôt
    que de laisser croire à un oubli.

  Restent **interdits, nommément** : merger **hors de ce chemin**, **fermer une PR**
  (`gh pr close`), **force-pusher**, et **rebaser** une branche poussée — un rebase appellerait un
  force-push. Un refus de `merge-mr` laisse la PR **ouverte** et le ticket **« En revue »** : c'est
  un état normal, pas un échec, et le résumé rend le merge **ou sa cause de refus**, jamais un ✅
  global.
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
  vérifient, **avant toute écriture** (commit, push, PR, statut, temps), que le ticket
  visé est bien celui de la session : `bash scripts/gitlab/lib.sh close-guard <iid> [branche]`.
  C'est le pendant en *sortie* de l'anti-collision d'entrée de `/ticket-start` (`issue-taken`, §5) —
  sans lui, un `/ticket-finish 158` lancé depuis `chore/163-…` faisait basculer **#158** « En
  revue », y accrochait la PR de la branche de #163 et le temps d'un travail qui
  n'était pas le sien ; via `/ticket-ship`, le commit généré portait en plus un `Closes #158` qui
  aurait fermé le ticket d'un autre au merge. Deux contrôles, de force très inégale :
  - **cohérence iid ↔ branche courante** (motif `<type>/<iid>-<slug>`, §1) — purement local, donc
    toujours disponible : c'est le contrôle **fort**, la branche étant le seul témoin fiable de ce
    que la session travaille réellement ;
  - **propriété du ticket** (assignés, via `issue-owner`) — contrôle **faible** tant que l'équipe
    partage un même compte de forge (le bot `MaestroAgents`, cf. `GL_BOT_USERS`) : il n'attrape que
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
- Une branche (locale ou distante) n'est supprimée que si **la forge confirme que sa PR est à l'état `merged`**. C'est la garantie qui protège d'une perte de travail — plus forte que l'ancêtre git.
- Vu cette confirmation, la suppression locale utilise `git branch -D` : le projet merge en **squash**, donc `git branch -d` refuserait la branche (sa pointe n'est pas un ancêtre du commit squashé). N'employer `-D` **que** sur une branche dont le merge est confirmé par la forge.
- **La suppression de la branche distante est un réglage du DÉPÔT, pas une option de la PR.** C'est
  le point où GitHub ne ressemble pas à GitLab, et l'écart s'est payé : côté GitLab le drapeau
  `--remove-source-branch` était posé **sur chaque MR** par `/ticket-finish`, donc la garantie
  voyageait avec la MR ; côté GitHub il n'y a aucun équivalent par PR — un seul réglage de dépôt,
  `delete_branch_on_merge`, vaut pour toutes. Il est **posé à `true` depuis le 2026-08-19** (#384) ;
  il ne l'était pas depuis la bascule (#343), et comme rien dans le cycle d'un ticket ne le
  remplaçait, **22 branches distantes s'y sont accumulées** pour zéro PR ouverte. Ni
  [`bootstrap.sh`](../scripts/gitlab/bootstrap.sh) ni `/ticket-finish` ne le posent — c'est un
  réglage de dépôt, il se (re)pose d'un appel et d'un seul :
  ```
  gh api -X PATCH repos/<owner>/<dépôt> -F delete_branch_on_merge=true
  ```
  Sa dérive est signalée par [`doctor.sh`](../scripts/gitlab/doctor.sh) (§6), qui nomme cette
  commande. Côté local, rien ne change pour `/branch-cleanup` : il supprime la branche **locale** et
  tolère une branche distante déjà supprimée.
- **Les branches d'avant la bascule sont hors de portée de cette garantie, et se traitent
  autrement.** Une branche mergée sur GitLab n'a pas de PR côté GitHub : la forge interrogée ne peut
  ni confirmer ni infirmer, et l'archive de migration
  ([`export-gitlab.sh`](../scripts/migration/export-gitlab.sh)) ne rattrape rien — elle a exporté
  les **tickets**, jamais les PR. Le contenu ne tranche pas davantage : un `git merge-tree` de la
  branche contre `main` rend un conflit sur presque toutes, ce qui ne dit **rien** de leur merge
  (conflit fantôme d'après squash, §9.5) mais seulement que `main` a bougé depuis. La règle
  appliquée au stock de #384, à rejouer telle quelle si le cas se représente : la branche est
  **archivée en tag** `archive/pre-github/<branche>` poussé sur `origin` **avant** toute
  suppression, ce qui garde ses commits joignables pour toujours, puis supprimée. L'archivage n'est
  pas une précaution de forme — c'est ce qui remplace la confirmation manquante : on ne prouve plus
  que la branche est mergée, on rend sa suppression sans conséquence.
- **Le stock de 22 s'est réparti en trois classes, et c'est la classe qui décide** (#384,
  2026-08-19 — au terme du rattrapage, `origin` ne porte plus que `main`). **Cinq** avaient une PR
  GitHub `merged` : supprimées sous la règle ordinaire, sans archive. **Seize** dataient d'avant la
  bascule (#290, #317, #321→#323, #331→#342, #333) : archivées puis supprimées, comme ci-dessus —
  le ticket en annonçait 14 sur une première mesure, son propre inventaire en nommait bien seize.
  La **vingt-deuxième**, `docs/353`, est la seule où le **contenu** tranche, et dans le bon sens :
  son unique commit porte un fichier au **blob identique** à celui de `main` (même sha), donc la
  branche n'apporte rien — l'exact inverse d'un `merge-tree` en conflit, qui lui ne prouve jamais
  rien. Elle est archivée quand même, sous un préfixe qui **nomme sa raison**
  (`archive/absorbee/<branche>`) plutôt que d'emprunter celle des seize : l'invariant à tenir n'est
  pas « d'où vient la branche » mais « aucune ne part sans que la forge la confirme **ou** qu'un tag
  la garde joignable ». Reste une dérive qui n'est pas d'hygiène de branches, laissée hors
  périmètre : l'issue **#353 est encore ouverte** alors que son livrable est sur `main`.
- **La revue est *best-effort*, pas bloquante — et depuis #413 elle est d'APRÈS-MERGE.** À
  plusieurs, personne ne sait spontanément ce qui attend qui : le projet garde donc
  `approvals_before_merge=0` et joue sur la **visibilité** — arbitrage du chantier #155. L'argument
  d'origine (« une approbation obligatoire recréerait une dépendance entre personnes ») tient
  toujours ; celui qui l'accompagnait — « et le merge resterait de toute façon humain » — est
  **faux depuis #418** : une approbation obligatoire ne ralentirait plus un humain, elle
  **bloquerait le merge automatique**, ce qui en fait un choix plus lourd qu'avant et non plus
  léger. Ce qu'exige le merge vit dans `merge-mr` (premier point de cette section), pas dans une
  approbation.
  - **Aucun relecteur n'est posé automatiquement** (#196). `/ticket-finish` l'a fait un temps
    (#161) ; ce n'est plus le cas : désigner un relecteur attribue une PR à quelqu'un qui ne l'a
    pas demandé, alors que la file de revue donne déjà le signal « cette PR attend quelqu'un ». La
    **visibilité** suffit donc, et la désignation redevient un **geste humain explicite**.
  - Le helper reste **outillé pour cette pose manuelle** :
    `bash scripts/gitlab/lib.sh set-reviewer [mr|branche] [username]` choisit, à défaut d'un nom
    donné, un **membre humain du projet distinct de l'auteur**, résolu via l'API des membres —
    **aucun nom en dur** ; les comptes d'automatisation sont écartés par la variable `GL_BOT_USERS`
    (défaut `MaestroAgents` : ce compte est un utilisateur GitLab ordinaire, `User.bot` y vaut
    `false`, l'API seule ne suffit donc pas à l'exclure). La désignation **tourne** entre les
    candidats (graine = iid de la PR : même PR → même relecteur, PR différentes → charge répartie)
    et elle est **idempotente** : un relecteur déjà posé n'est **jamais** remplacé. Sur un projet à
    une seule personne, il n'y a pas de candidat et le helper échoue proprement (code `1`). Aucune
    commande du workflow ne l'appelle — c'est un outil, plus une étape.
  - `/backlog` affiche la **file de revue** en tête (`bash scripts/gitlab/lib.sh review-queue`) :
    PR ouvertes **la plus ancienne d'abord**, avec `age_j` (l'ancienneté, c'est elle qui déclenche
    la relecture), l'état `draft`/`ready`, le statut du pipeline, l'auteur et le relecteur s'il en
    a été posé un à la main (colonne à « - » sinon, cas désormais normal). C'est **elle seule** qui
    porte le signal de revue.
- **Une PR au pipeline rouge n'est pas mergeable — et c'est NOUS qui le tenons, pas la forge.** Du
  temps de GitLab, le réglage projet `only_allow_merge_if_pipeline_succeeds=true` (complété par
  `allow_merge_on_skipped_pipeline=false`) faisait appliquer la règle par **GitLab lui-même** : le
  bouton de merge restait grisé. **Ce réglage n'a pas d'équivalent joué côté GitHub** — son pendant
  est la protection de branche, indisponible sur un dépôt privé d'un compte Free (§8.8, mesuré le
  2026-08-14). La règle n'a pas changé, son gardien si : c'est le **quatrième prérequis de
  `merge-mr`** (premier point de cette section), qui compare en plus le sha du run à la **tête de la
  PR** — ce que la protection de branche, elle, n'aurait pas fait.

**Adossement à la couche permissions (Claude Code).** Ces garde-fous ne reposent pas que sur les
consignes des commandes : ils sont aussi **filtrés par l'allowlist** [`.claude/settings.json`](../.claude/settings.json)
(§7.1). Elle **autorise sans prompt** les commandes git/`gh` **non destructrices** du workflow (pour
que `/ticket-ship` s'enchaîne sans blocage), pose en **`deny`** les actions que les garde-fous
interdisent (**force-push** `git push --force`/`-f`/`--force-with-lease`, **`gh pr merge`**,
**`gh pr close`**, **`gh run delete`**) et en **`ask`** (confirmation explicite, jamais silencieuse)
les actions sensibles hors chemin nominal (`git commit --no-verify`, `git reset --hard`,
`git clean`, `gh issue close`). C'est un **filet de sécurité complémentaire** au jugement de l'agent, pas un
remplacement : le matching est par préfixe (une variante d'ordre de drapeaux peut y échapper), donc
la consigne reste la règle première — « jamais de force-push, jamais de fermeture de PR, et **aucun
merge non vérifié** ». Le `deny` sur `gh pr merge` n'a pas bougé avec #413 et n'a pas à bouger : il
barre le geste **nu**, que plus personne n'a de raison de lancer.

---

## 7. Prérequis

> **Tout ce qui suit est monté par une commande** sur un clone frais :
> [`bash scripts/setup.sh`](../scripts/setup.sh) — ou [`/setup`](../.claude/commands/setup.md) en
> session Claude Code. Il installe `gh`, active le hook `commit-msg`, crée le `.venv` et le `.env`
> et pose les réglages Claude Code. Il ne monte **plus de runner CI** : la CI tourne sur les
> exécutants hébergés de GitHub (§8.1, #344). Ce qui est détaillé ici est le **quoi et le
> pourquoi**, plus une check-list à dérouler à la main.

> **Un humain qui arrive lit [`CONTRIBUTING.md`](../CONTRIBUTING.md)**, pas ce document. Ce
> fichier-ci est exhaustif (et `CLAUDE.md` est écrit pour l'agent) : `CONTRIBUTING.md` tient en une
> page le chemin `setup.sh` → ticket libre via `/backlog` → `/ticket-start` → `/ticket-ship`, dit
> qui relit et qui merge, et renvoie ici pour le détail. C'est **le seul point d'entrée à
> connaître** ; tout ce qu'il affirme est une redite volontaire de ce document, jamais une règle
> nouvelle.

- [`gh`](https://cli.github.com/) installé et authentifié : `gh auth login` (proposé par
  `scripts/setup.sh`, qui n'automatise pas l'authentification — elle est interactive et son jeton
  ne transite jamais par une ligne de commande ; §7.4 pour l'isolement par projet).
- Vérifier l'accès : `gh issue list` doit lister les tickets du dépôt.
- Les commandes `/ticket-*` et `/backlog` s'appuient sur le helper
  [`scripts/gitlab/lib.sh`](../scripts/gitlab/lib.sh) (bash), qui factorise les appels `gh`
  (résolution work-item, **cycle de vie** par nom — `set-workflow`, §3.1 —, **listing du backlog**, slug, préfixe de
  branche, **sous-tickets** — `issue-link`/`parent-of`/`subtickets`, §5.1 —, **démarrage de
  ticket** — `start-brief`/`begin`, §5 —, **retard sur `origin/main`** — `behind-main`, §6 — et
  **garde-fou de clôture** — `close-guard`/`branch-iid`, §6).
  Il est **sourçable**
  (`. scripts/gitlab/lib.sh`) et **exécutable en sous-commandes**
  (`bash scripts/gitlab/lib.sh set-workflow <iid> "En cours"`, `… backlog opened`) — pratique pour les
  futurs scripts et agents. Vérif rapide : `bash scripts/gitlab/lib.sh require`.
  - **Robustesse des lectures** : toutes les **lectures** GraphQL passent par `gl_graphql_read`, qui
    **ré-essaie sur réponse vide** (l'endpoint GraphQL de la forge hoquette par intermittence). Réglable
    via `GL_GQL_RETRIES` (défaut 3) et `GL_GQL_RETRY_DELAY` (défaut 1 s). Les **mutations** (cycle
    de vie, dates, temps) gardent un appel direct — pas de retry, pour ne pas risquer une double application
    (ex. un timelog additif).
- **Bilan de santé** : [`bash scripts/gitlab/doctor.sh`](../scripts/gitlab/doctor.sh) (lecture seule)
  vérifie auth, labels de catégorisation, options du champ Status, et **détecte les dérives**
  (ticket « En revue » sans PR, ticket fermé au statut encore actif, branche locale mergée à
  nettoyer, réglages de merge « pipeline vert » ou « suppression de la branche source » retombés — §6). Code de sortie non nul si un contrôle dur échoue (`--strict` pour échouer aussi sur les
  dérives — utile en CI).
- **Hooks git** : posés par `scripts/setup.sh` (étape `hooks`), qui délègue à
  `bash scripts/git/install-hooks.sh` — lançable seul, une fois par clone. Active le hook
  [`commit-msg`](../scripts/git/hooks/commit-msg) qui valide la convention de commit (§2). Pose
  `core.hooksPath` ; désactivation : `git config --unset core.hooksPath`.
- **Windows / Git Credential Manager** : si un `git push`/`pull` reste bloqué sur une demande
  d'identifiants, forcer `gh` comme credential helper le temps de la commande —
  `git -c credential.helper='!gh auth git-credential' push -u origin <branche>`.

### 7.1 Permissions Claude Code (allowlist)

Pour que les commandes du workflow — en particulier [`/ticket-ship`](../.claude/commands/ticket-ship.md) —
s'enchaînent **sans prompt de permission répété**, le dépôt versionne une allowlist
[`.claude/settings.json`](../.claude/settings.json) (partagée par toute l'équipe ; les surcharges
personnelles vont dans `.claude/settings.local.json`, non versionné).

| Catégorie | Effet | Contenu (préfixes de commande) |
|---|---|---|
| **`allow`** | exécuté sans prompt | Lectures/écritures **non destructrices** du workflow : `git status`/`diff`/`log`/`show`/`branch`/`checkout`/`fetch`/`pull`/`add`/`commit`/`push`/`rev-parse`/`ls-files` ; `gh` `auth status`, `api user`/`graphql`, `issue` view/list/create/edit/comment, `pr` view/list/create/edit/diff/ready, `run` list/view/rerun, `workflow` list/view/run ; `bash scripts/gitlab/lib.sh`, `… doctor.sh`, `… git/install-hooks.sh`. |
| **`ask`** | confirmation explicite (jamais silencieux) | `git commit --no-verify` (le bypass du hook reste possible mais **volontaire**), `git reset --hard`, `git clean`, `gh issue close`. |
| **`deny`** | bloqué | Ce que les garde-fous (§6) interdisent : `git push --force` / `-f` / `--force-with-lease`, `gh pr merge`, `gh pr close`, `gh run delete`. |

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
  navigateur piloté par `chrome-maestro`), `env.GH_CONFIG_DIR` (compte GitHub propre à ce projet,
  §7.4),
  `enabledMcpjsonServers` (approbation des serveurs de `.mcp.json`) et `permissions.allow`
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
| La demande **relève du ticket en cours ou d'un ticket existant** du backlog | **Mettre à jour ce ticket** : commentaire (`lib.sh issue-note <iid> <fichier>`), et **description** (`lib.sh set-description`) si le périmètre change. Par les helpers, jamais par un `gh` direct : le texte long voyage par un **fichier** (§11.7). |
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
| `# [partagé]` | secret du projet, endpoint, identifiants d'espace de travail | **variables Actions du dépôt GitHub** (`GET /repos/:dépôt/actions/variables`, réservées aux membres) |

```bash
bash scripts/env-pull.sh              # complète le .env avec les clés partagées qui manquent
bash scripts/env-pull.sh --check      # diagnostic seul — n'écrit rien, ne lit aucune valeur
bash scripts/env-pull.sh --manquantes # juste les noms à compléter (ce qu'interroge setup.sh)
```

Quatre promesses, que [`tests/test_env_pull.py`](../tests/test_env_pull.py) épingle :

- **le gabarit fait foi** — la liste des clés partagées est *lue* dans `.env.example`, jamais
  recopiée dans le script : annoter une nouvelle clé là-bas suffit ;
- **non destructif** — une clé déjà renseignée n'est **jamais** écrasée, même si la variable Actions
  dit autre chose, et les clés `[perso]` ne sont pas même regardées ;
- **aucune valeur imprimée** — la sortie ne porte que des *noms* de clés et des comptes ; les
  valeurs ne traversent ni l'affichage ni un argument de commande (lisible par tout processus de la
  machine), seulement des fichiers temporaires en 0600 effacés en sortie ;
- **franc sur ce qu'il ne peut pas** — une clé partagée absente des variables du projet est dite
  comme telle, avec la commande qui la publie. Rien n'est deviné.

**Publier une valeur partagée** est un geste de **mainteneur**, une fois par clé :

```bash
gh variable set LANGFUSE_HOST --body "https://cloud.langfuse.com"
```

⚠ Une **variable** Actions se relit ; un **secret** Actions (`gh secret set`) est *write-only* et
`env-pull.sh` ne pourra donc jamais le rendre. Ce qui doit atterrir dans un `.env` de clone passe
par une variable — la distinction n'existait pas côté GitLab, où une variable masquée se relisait
par l'API. Détail : [docs/27 §5](./27-decision-gitlab-vers-github.md).

### 7.4 Un compte GitHub par projet — `GH_CONFIG_DIR`

**Le problème.** `gh` sait stocker **plusieurs comptes** sur `github.com` — le poste de référence
en porte trois — mais n'en garde **qu'un actif**, et cet actif vit dans un fichier unique
(`$XDG_CONFIG_HOME/gh/hosts.yml`, `%AppData%\GitHub CLI\hosts.yml` sous Windows). `gh auth switch`
est donc une **variable globale** : elle bascule tous les terminaux et tous les dépôts de la machine
d'un coup. Deux projets ouverts en parallèle se la disputent, et le perdant est celui qui ne regarde
pas — typiquement un run `/orchestrate --detach` (§11) qui hérite du compte posé par la dernière
commande tapée ailleurs, et travaille sous une identité que personne n'a choisie pour lui.

**La réponse : un config dir par projet.** `GH_CONFIG_DIR` désigne le dossier où `gh` range sa
configuration — donc **son propre `hosts.yml`, donc son propre compte actif**, et `gh auth switch`
redevient une opération locale à un projet. Les **jetons** restent dans le trousseau du système et
sont partagés entre les dossiers ; c'est le `hosts.yml` de chacun qui décide qui est actif — d'où un
`gh auth login` à rejouer **une fois par dossier**, un config dir neuf ne connaissant encore
personne (`gh auth status` y répond « You are not logged into any GitHub hosts »).

```bash
mkdir -p ~/.config/gh-<projet>                            # le dossier, vide au départ
env GH_CONFIG_DIR=~/.config/gh-<projet> gh auth login     # une fois, interactif
env GH_CONFIG_DIR=~/.config/gh-<projet> gh auth status    # vérification
```

**Où la poser côté Maestro** : le bloc `env` de `.claude/settings.local.json` — non versionné, comme
tout chemin de machine —, dont le gabarit
[`.claude/settings.local.example.json`](../.claude/settings.local.example.json) documente la clé avec
une **valeur neutre** (§7.1) :

```json
{ "env": { "GH_CONFIG_DIR": "C:\\Users\\<vous>\\.config\\gh-maestro" } }
```

Détail qui tombe juste : **les worktrees héritent du bon compte sans rien faire**, et par les deux
chemins possibles. Une session **relocalisée** (§9.1) change de répertoire de travail **mais pas de
bloc `env`** — elle garde donc celui du clone principal. Une session **ouverte directement** sur un
worktree lit, elle, le `settings.local.json` *du worktree* — que `worktree.sh` recopie du clone
principal au premier passage, en n'imposant que les trois valeurs qui **doivent** différer d'une
session à l'autre (profil de navigateur, ports Control Tower, §9). `GH_CONFIG_DIR` n'en fait pas
partie : deux worktrees du même projet parlent bien au même compte, ce qui est l'intention.

Rien à provisionner dans `setup.sh` : le chemin est machine et le choix du compte est un geste
humain (`gh auth login` interactif) — même traitement que `MAESTRO_CHROME_PROFILE`, à ceci près que
l'étape `mcp` ne lit **jamais** le gabarit (elle fusionne depuis `.mcp.json` et le `.env`), donc
documenter la clé n'écrit rien nulle part.

**Deux variantes écartées :**

| Variante | Pourquoi non |
|---|---|
| `gh auth switch` seul | État **global**, cf. ci-dessus. Reste l'outil normal *à l'intérieur* d'un config dir. |
| `GH_TOKEN` | Court-circuite tout le reste, mais met le **secret en clair** dans un fichier de configuration et rend `gh auth status`/`gh auth switch` inopérants (gh ne rapporte plus qu'un jeton d'environnement). À réserver à la **CI**. |

**Deux limites, à connaître sous peine de fausse sécurité** — le config dir ne décide que de ce que
`gh` fait en son nom propre :

- **L'auteur des commits n'en vient pas.** Il vient de `git config user.name`/`user.email`, dont le
  global du poste porte le compte principal. Un projet qui doit commiter sous une autre identité la
  pose **en local**, dans le dépôt : `git config --local user.name …` et `… user.email …`.
- **L'authentification de `git push` en HTTPS non plus.** `gh auth setup-git` installe un credential
  helper **global** (`credential.https://github.com.helper` dans le `.gitconfig` du poste), qui
  résout vers le compte actif du config dir **ambiant** : correct depuis une session Claude Code qui
  exporte `GH_CONFIG_DIR`, **faux depuis un terminal nu**, où c'est le config dir par défaut qui
  répond. Dans le doute, forcer le helper le temps de la commande — même geste qu'en §7 :
  `git -c credential.helper='!gh auth git-credential' push …`.

---

## 8. Intégration continue (CI)

Le pipeline [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) porte six jobs :
`shellcheck` (sévérité `warning`, scripts `scripts/**/*.sh`, **un appel par fichier** — §8.4),
`python-lint` (ruff), `pytest` (suite du dépôt **en parallèle**, `-n auto`, #214 — 1 min 53 s au
lieu de ~10, §8.4, avec **couverture** pytest-cov : taux remonté dans le résumé du run, échec sous
`--cov-fail-under=90`), `mypy` (typage strict de `maestro/`), `web-build` (l'UI Control Tower) et
`perimetre`, un job-portier sans équivalent GitLab (§8.8). Les jobs Python partagent le **cache
pip** natif de `setup-python` (clé sur `pyproject.toml`). Un **pipeline vert est la condition de
passage `En revue` → merge**.

Le **front** (`apps/web`) a son propre job, `web-build`, qui enchaîne `npm run lint` (ESLint),
`npm run typecheck` (`tsc --noEmit`, #236), `npm test` (la suite **Vitest** de l'interface, #124)
puis `npm run build` (`next build`, qui vérifie aussi le typage TypeScript). Le `typecheck` fait
donc doublon avec le build : il existe pour rendre le typage vérifiable **seul**, en quelques
secondes, et sous une forme qu'une session Claude Code peut lancer — la couche permissions
autorise `npm run …`, jamais un `./node_modules/.bin/tsc` (#236) ; le jouer en CI et dans le filet
local est ce qui l'empêche de pourrir. Les quatre tiennent dans **un seul** job parce que
l'installation des dépendances (`npm ci`) pèse bien plus que les contrôles eux-mêmes : la refaire
trois fois de plus n'apprendrait rien et se facturerait d'autant (§8.1) ; l'ordre va du plus
rapide au plus lent, pour que le verdict tombe tôt quand il est rouge. Le job ne se déclenche que
si `apps/web/**` (ou `.github/workflows/**`) change — un pipeline purement Python reste rapide — et
son cache npm porte sur le lockfile versionné.

Les **scripts shell** ne sont pas seulement lintés : le parcours de mise en route
([`scripts/setup.sh`](../scripts/setup.sh), §7) a sa propre suite pytest
[`tests/test_setup.py`](../tests/test_setup.py) (#147), qui monte un **dépôt jetable** dans un
répertoire temporaire et y lance le script pour vérifier ses invariants — `--check` n'écrit rien,
deuxième passage entièrement en `DÉJÀ FAIT`, `.env` et `settings.local.json` jamais écrasés (le
second est fusionné clé par clé), rapport complet et code de sortie non nul sur échec dur. Les
étapes réseau / Docker (`venv`, `web`, `infra`, `verif`) y sont **neutralisées** par
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

Le même conftest neutralise, pour la même raison, **`MAESTRO_ORCHESTRATE_COULEUR`** (#236). Posée
dans le bloc `env` d'un `.claude/settings.local.json` — c'est ce qui garde les couleurs de
`run.sh --detach` dans la console qu'il ouvre (§11) —, elle fuit dans l'environnement de toute
session de ce poste, donc des sous-processus de `tests/test_orchestrate.py`, dont la sortie
capturée ressort truffée de codes ANSI : `test_sans_le_marqueur_la_sortie_reste_sans_couleur`
échoue **en local seulement**, la CI restant verte. Quatre sessions ont rouvert la même enquête sur
cette fausse alerte avant qu'on la tarisse à la source. La règle générale, dont Langfuse et la
couleur ne sont que deux cas : **le verdict de la suite ne dépend pas du poste qui la joue** — ce
qu'un `.env` ou un `settings.local.json` pose dans l'environnement se neutralise dans le conftest,
pas dans le fichier du poste (non versionné, le prochain clone le reposerait).

**Quand un pipeline se déclenche ?** **Uniquement sur les Pull Requests** (#165, puis #338). Le
bloc `on:` du workflow ne porte que `pull_request` — l'**ouverture** d'une PR, puis chaque **push
sur sa branche source** tant qu'elle est ouverte — et `workflow_dispatch`, le déclenchement
**manuel** (bouton « Run workflow », ou `gh workflow run ci.yml --ref <branche>`, le repli de
`/mr-fix`). Tout le reste ne déclenche rien : un push sur une branche **sans PR**, le push sur
**`main` après le merge**, les tags. Avant cette règle, une même branche payait **trois** pipelines
— pendant le développement, à la clôture du ticket, puis sur `main` une fois mergée — pour un seul
verdict réellement lu, celui qui conditionne le merge. Trois conséquences pratiques :

- **Vérifier son travail avant la PR est un geste local** :
  [`scripts/ci/local.sh`](../scripts/ci/local.sh) rejoue les mêmes jobs sur le poste (#157) — c'est
  lui qui remplace les pipelines de branche, et il ne dépend d'aucun runner. Depuis un **worktree**,
  il teste bien le code d'ici : voir §9 pour le piège d'import que cela suppose d'éviter (#194).
  Depuis #214 il ne joue par défaut que les **suites concernées par le diff** (§8.4) : la suite
  complète, c'est `--complet`, ou le pipeline de cette PR.
- **Le verdict d'un run se lit en DEUX champs** : Actions sépare `status` (en cours) de
  `conclusion` (issue), là où l'outillage raisonne sur une seule valeur
  (`success`/`failed`/`pending`…). `lib.sh pipeline-latest <branche>` les recompose, et c'est lui
  qu'utilisent `/mr-fix`, `/ticket-finish` et `/mr-review` — jamais un `gh run list` direct, qui
  verrait bien le run mais pas un verdict comparable. La file de revue (`lib.sh review-queue`) lit
  le `statusCheckRollup` du dernier commit en GraphQL : elle n'est pas concernée.
- **La case « Pipeline CI verte » de la PR est vide au premier passage**, et c'est normal :
  `/ticket-finish` pousse **puis** ouvre la PR, donc le pipeline naît *après* le constat (§6).

Le garde-fou de merge **de la forge** n'est, lui, **pas posé**, et c'est une décision documentée :
la protection de branche n'existe pas sur un dépôt privé d'un compte Free (§8.8). Les six verdicts
se lisent sur la PR — et la règle « pas de merge au rouge » est tenue **par nous**, dans
`lib.sh merge-mr`, quatrième prérequis (§6).

### 8.1 Aucun runner à tenir allumé (#344)

Les pipelines tournent sur les **exécutants hébergés** de GitHub (`ubuntu-latest`). Il n'y a rien à
installer, rien à démarrer, personne à attendre — et c'est le gain que la note de décision chiffrait
sans pouvoir le facturer ([docs/27 §4](./27-decision-gitlab-vers-github.md)).

Ce qui a disparu avec la CI GitLab, et qu'il faut avoir vu une fois pour comprendre le prix payé :
les runners partagés GitLab étaient **coupés** (quota de minutes durablement épuisé, #135), donc les
jobs tournaient sur des **runners de projet** montés à la main sur des postes ; un pipeline vert
conditionnant le merge, **une machine de l'équipe devait rester allumée en permanence**, et son
extinction bloquait tout le monde — les jobs restant `pending` sans qu'aucun message ne le dise.
Trois scripts servaient uniquement à tenir cette contrainte, **1 146 lignes** au total
(`setup-runner.sh` pour monter le runner, `ensure-runner.sh` pour le rallumer avant chaque MR,
`clean-runner-containers.sh` pour ramasser les conteneurs qu'un runner tué en cours de job laissait
derrière lui, ~1,5 Go constatés). Tous trois sont supprimés, avec l'étape `runner` de `setup.sh`,
les appels dans `/ticket-finish` et `/mr-fix`, et les deux sections de `doctor.sh` qui les
surveillaient.

**Conséquence pratique** : un pipeline qui n'avance pas n'est plus un runner à réveiller. Docker ne
sert plus qu'aux **bases locales** optionnelles (`infra/`, derrière `--with-infra`), et une mise en
route n'en a plus besoin du tout.

> Les sous-sections **8.2** (ménage des conteneurs de jobs) a disparu avec eux. La numérotation des
> suivantes n'a **pas** été resserrée : elle est citée dans `CLAUDE.md`, dans les prompts et dans
> les tests, et la faire glisser pour combler un trou coûterait plus qu'elle ne rapporte.

### 8.3 PR non mergeable — remédiation (`/mr-fix`, anciennement `/pipeline-fix`)

**Deux choses empêchent de merger une PR**, et [`/mr-fix`](../.claude/commands/mr-fix.md) (voir §5)
les traite toutes les deux, une PR à la fois : le **conflit avec `origin/main`** et le **pipeline
rouge**. La commande s'appelait `/pipeline-fix` jusqu'à #303, où elle n'en traitait qu'une — un
pipeline remis au vert sur une PR en conflit annonçait un ✅ trompeur.

**L'ordre est le contenu de la décision** : le conflit d'abord. Le merge d'`origin/main` peut
*lui-même* casser le pipeline — deux changements corrects séparément, faux ensemble —, donc
diagnostiquer le pipeline avant, c'est diagnostiquer un état qui n'existera plus.

**1. Le conflit** — `lib.sh mr-conflict [branche]` rend le verdict : `0` se merge proprement,
`3` conflit (fichiers listés), `1` verdict impossible, `2` usage. Lecture seule, sans checkout ni
index touché, donc jouable sur une branche qu'on ne sort pas — c'est le cas d'usage réel, juger la
branche d'une PR depuis le clone principal.

Le verdict vient de `git merge-tree --write-tree`, un **merge 3-way réel**, et non des deux sources
qui existaient déjà — ni l'une ni l'autre ne pouvait porter cette décision :

| Source | Ce qu'elle répond | Pourquoi elle ne suffit pas |
|---|---|---|
| `lib.sh behind-main` | ces fichiers sont modifiés **des deux côtés** | Heuristique **pessimiste** : vraie presque partout sur les fichiers aimants du dépôt (`CLAUDE.md`, ce fichier-ci, `lib.sh`), où les deux côtés éditent des régions disjointes. Répond en plus **avant le push**, quand le conflit naît des merges qui suivent. |
| `has_conflicts` / `detailed_merge_status` (GitLab) | GitLab a-t-il vu un conflit | **Asynchrone** : 5 MR ouvertes sur 6 répondaient `checking`/`unchecked` à la mesure du 2026-08-07. Se lit en complément, jamais en l'attendant. |

La résolution est un **`git merge origin/main`, jamais un rebase** : réécrire une branche déjà
poussée appellerait un force-push, barré en `deny` (§6). Et une résolution qui n'est pas claire
**ne se pousse pas** — `git merge --abort`, branche intacte, constat rendu : mieux vaut un conflit
signalé qu'une résolution fausse mergée sans que personne ne l'ait relue ligne à ligne. ⚠ Cet
argument **s'est renforcé** avec #413 : il valait déjà sous une PR en Draft que personne ne relisait
en pratique ; depuis que le merge n'attend plus personne, la relecture ne peut plus arriver *par
hasard* avant `main`. Une PR qui attend coûte infiniment moins qu'une résolution fausse mergée.

**2. Le pipeline** — diagnostic des jobs en échec, correctif local quand c'est corrigeable, commit
`Refs #<iid>` poussé sur la branche, suivi du nouveau pipeline. Les briques réutilisables vivent
dans `lib.sh` : `pipeline-latest <ref>`, `pipeline-status <id>`, `pipeline-failed-jobs <id>`,
`job-trace <job-id> [lignes]`, `pipeline-wait <ref|run-id> [--timeout <s>]` (parsing shell pur,
comme le reste du fichier ; la forme `<id> <timeout-s>` que cette commande emploie reste acceptée
— #416 lui a ajouté la cible **ref** et les codes `4`/`5`, elle ne lui en a rien retiré). Le job
rouge se rejoue en local par le **filet CI** — `bash scripts/ci/local.sh --only
<job>` —, jamais par une recette recopiée à côté : le filet lit les jobs dans le workflow,
passe par le venv du repo, analyse un miroir LF pour shellcheck (la CI checkout en LF, une copie
Windows CRLF produit des faux SC1017) et cadre `pytest` sur le périmètre du diff (§8.4) — la suite
entière, ~10 min, se payerait ici en plein diagnostic.

**3. Le merge** — depuis #418, `/mr-fix` **merge ce qu'il vient de débloquer**, par
`bash scripts/gitlab/lib.sh merge-mr` et **jamais** `gh pr merge` (§6). Débloquer sans merger
laisserait la PR exactement là où on l'a trouvée, à attendre quelqu'un : c'est le geste que le
chantier #413 supprime. Il ne touche à **rien d'autre** du cycle de vie — ni champ Status, ni
création de PR, ni `gh pr ready` : lever un brouillon ferait déclarer terminé, par une commande de
remédiation, un travail qu'elle n'a pas fait (#415).

Et il ne merge **rien** quand il a dû abandonner — branche `main`, conflit non résolu, échec
d'infrastructure, deux tentatives épuisées : ces cas sortent de la commande **sans passer par le
merge**. Le résumé le dit alors d'un autre mot, et la nuance est le contenu du bilan : **« non
tenté »** est la conséquence d'un abandon de la remédiation, **« refusé »** est un verdict de
`merge-mr` sur la PR. Les confondre ferait chercher un problème de PR là où il y a une remédiation
inachevée.

⚠ **Dans un run autonome, `/mr-fix` ne merge pas** : le merge appartient au **pilote** (§11), qui
sérialise les merges et attend les pipelines hors du quota des sessions. `guard.sh` y refuse en dur
`lib.sh merge-mr` **et** `lib.sh pipeline-wait`, et le message le dit — une session de déblocage
s'arrête à la PR **rendue mergeable**, ce qui est le verdict complet que le run attend d'elle.

**Le résumé rend les blocages séparément**, jamais un verdict global : une PR au pipeline vert
mais en conflit reste non mergeable, et une PR débloquée n'est pas une PR mergée.

⚠ **`git merge-tree` rend `128`** — pas `1` — quand le merge est impossible à évaluer (histoires
sans ancêtre commun). Le confondre avec le `1` d'un conflit enverrait la commande résoudre un merge
qui ne peut pas avoir lieu ; `mr-conflict` distingue les deux et rend `1`, que la commande traite
comme « poursuis sur le pipeline », pas comme un conflit.

### 8.4 Boucle courte en local, suite complète au pipeline (#214)

**La suite complète ne se rejoue plus pendant le développement.** Elle est jouée par le pipeline
de la PR, qui depuis #165 est de toute façon le **seul verdict complet** et la condition de merge.
En local, le filet ne joue que ce que le diff concerne.

Ce que coûtait l'ancien réflexe, mesuré sur le dépôt (1102 tests, poste 16 cœurs) :

| Ce qu'on joue | Durée |
|---|---|
| Suite complète, en série | **9 min 57 s** |
| Suite complète, `-n auto` | **2 min 34 s** |
| Suite complète, `-n auto` + couverture (le job CI) | **1 min 53 s** |
| Les 773 tests **hors outillage** | **46 s** |
| Une suite ciblée (`tests/test_engine.py`, 30 tests) | **1,5 s** |

Le diagnostic n'est pas « les tests sont lents » : ce sont les **~360 tests d'outillage** — ceux
qui montent un dépôt git jetable et lancent un script shell ou un worker celery — qui pèsent 9 des
10 minutes. Ils portent sur `scripts/`, pas sur `maestro/` : les rejouer pendant qu'on écrit du
code applicatif n'apprend rien. Deux leviers en découlent.

**1. `pytest-xdist` partout.** `-n auto` dans le job CI, `-n min(cœurs, 8)` dans le filet local
(#285, levier 3 ci-dessous) : ces tests attendent des processus, pas du CPU, et se parallélisent
donc presque idéalement. Aucun test n'est sauté, aucun risque de faux vert — c'est le levier
gratuit, et il profite d'abord au pipeline (1 min 53 s au lieu de ~10). Il ne paye pas en dessous
d'une certaine taille : démarrer les workers coûte ~5,5 s, quand une suite applicative ciblée
tourne en 1,5 s. Le filet ne le passe donc que lorsqu'une suite d'outillage est dans le périmètre —
et **jamais sur un venv qui n'a pas `pytest-xdist`** (clone antérieur à #214) : il sonde, joue en
série et le dit, plutôt que de rendre un rouge qui ne parle pas du code. Le rattrapage est
`bash scripts/setup.sh --only venv`.

Ce cas-là n'en est plus qu'un parmi d'autres : depuis #216, le filet demande à
`setup.sh --derive` **tout ce que ce clone n'a pas pris** du dépôt (dépendances Python, paquets
npm, version de Node) et le dit avant son verdict, dans un bloc « Dépendances en retard » qui porte
la commande de rattrapage. Il **signale sans installer** — c'est le principe ci-dessus : changer
l'environnement de quelqu'un qui attend un verdict, ce serait lui rendre le verdict d'un autre
environnement. La réparation, elle, se déclenche d'office ailleurs : au démarrage d'un ticket
(§9.4).

**2. Un périmètre déduit du diff**, dans [`scripts/ci/local.sh`](../scripts/ci/local.sh) :

```bash
bash scripts/ci/local.sh              # défaut : lint complet + pytest sur le périmètre du diff
bash scripts/ci/local.sh --complet    # la suite entière + la couverture — ce que fera la CI
```

Chaque job écrit son journal sous **`.maestro/ci-local/<job>.log`** — sous la racine du worktree,
et c'est ce **chemin relatif** que le script affiche quand un job rougit, extrait des 40 premières
lignes à l'appui. Il vivait jusqu'à #234 dans `${TMPDIR:-/tmp}`, d'où le renvoi vers un absolu hors
du répertoire de travail : lisible d'un clic en session interactive, hors de portée en session
autonome. Le pourquoi et la règle générale qui en découle sont en §8.5. Table rase à chaque
lancement — un journal d'hier à côté d'un run qui n'a pas joué ce job-là mentirait.

Le périmètre se calcule sur `origin/main..HEAD` **plus le travail non commité** (c'est ce qui
partira au push), fichier par fichier :

| Ce qui change | Ce qui se joue |
|---|---|
| `maestro/**` | toutes les suites **applicatives** |
| `scripts/**`, `.claude/**`, `.gitlab*`, `.env.example`… | les suites qui **nomment** le fichier — à défaut, celles qui nomment le **chemin de son dossier** (ci-dessous) |
| `tests/test_*.py` | elles-mêmes |
| `tests/conftest.py`, `pyproject.toml`, `.node-version` | la suite entière |
| `docs/**`, `apps/web/**`, prose de la racine | aucune suite pytest (`web-build` couvre le front) |
| **tout le reste** | la suite entière |

Ces points de conception valent d'être compris avant d'y toucher.

**Le repli par le dossier cherche un chemin, jamais un nom nu (#375).** La règle du nom a un repli :
un fichier que personne ne cite hérite des suites qui nomment son **dossier** — une suite qui relit
tout un répertoire le désigne ainsi, jamais par le nom de ses fichiers (`test_collaboration`
parcourt `.claude/commands/*.md` sans citer un seul prompt, #196). Ce repli cherchait le **nom nu**
du dossier, en sous-chaîne ; sur des noms courts et courants en français, il matche la prose de
n'importe quelle suite. Mesuré sur `main` au 2026-08-18 : `migration` → **10 suites**, dont aucune
ne teste `scripts/migration/` ; `github` → `test_cycle_de_vie` ; `workflows` → `test_durable` ; et
`ci` aurait ramené **59 suites sur 61**, « ci » étant une sous-chaîne d'« ici », de « précis », de
« spécifique »…

Le coût n'était pas le **temps** — ces suites-là sont applicatives et rapides — mais la
**couverture** : le repli **remplace** l'élargissement, donc un fichier que personne ne nomme
repartait avec dix suites tirées au sort au lieu de la suite entière, sous un motif crédible
(« périmètre : 10 suite(s) (migration/) »). Un faux vert **motivé**, exactement ce que le filet
s'interdit en tête de fichier. Le défaut existait depuis #196 mais restait rare ; les cinq scripts
arrivés avec la migration GitHub — que **rien ne nomme** — en ont fait le cas courant.

Deux conséquences de la correction, dans le même sens de dérive. Le repli compare désormais le
**chemin avec son séparateur** (`scripts/migration/`, `.claude/commands/`), ce qui laisse le cas
#196 intact — mêmes suites — et ramène chacun des cas ci-dessus à **une** suite, celle qui nomme
vraiment le répertoire. Et un dossier de **premier niveau** est écarté pour la raison qui a motivé
tout le reste : `scripts/` est une sous-chaîne de tout `scripts/gitlab/lib.sh` cité quelque part
(10 suites mesurées), il ne désigne aucun répertoire en particulier. Quand rien de crédible ne
répond, le repli **s'abstient** — donc on élargit.

Reste une question que la correction ne traite pas et qui n'est pas la sienne : les cinq scripts de
la migration n'ont **aucun test**. Aucun périmètre, si large soit-il, n'invente une couverture qui
n'existe pas.

**Les suites d'outillage sont déduites, jamais listées.** Une suite est « outillage » si elle
**nomme un script du dépôt** (`tests/test_orchestrate.py` cite `run.sh`, `test_collaboration.py`
cite `lib.sh`…). La dérivation donne exactement celles qu'on attend, et elle a deux propriétés
qu'une liste écrite à la main n'aurait pas : une suite d'outillage nouvelle se classe toute seule
— `tests/test_cycle_de_vie.py` (#366, §3.9) l'a vérifié le jour où elle est née —, et une suite qui
ne nomme aucun script est **applicative par défaut**, donc jouée dès que `maestro/` bouge, jamais
sautée en silence.

**Un fichier de `tests/` qui n'est pas une suite retombe sur « la suite entière ».** Le premier est
[`tests/harnais_forge.py`](../tests/harnais_forge.py) (#366) : un harnais partagé, sans un seul test.
Il ne matche pas `tests/test_*.py`, donc le classement le range dans « tout le reste » — et c'est le
bon verdict par accident heureux plutôt que par règle : deux suites en dépendent, un tri plus fin
devrait les retrouver, et se tromper ici rendrait vert un filet qui n'a pas regardé le code changé.

**On n'affine pas à l'intérieur de `maestro/`.** Sélectionner par module supposerait de lire le
graphe d'imports : le couplage y est réel et invisible d'une recherche textuelle (un module de
télémétrie touché casse le moteur sans que le test du moteur le nomme). Toutes les suites
applicatives, 40 s, aucun faux négatif — le gain qu'apporterait un tri plus fin ne vaut pas le
risque de rendre un vert qui n'a pas regardé le code fautif.

**La contrepartie est assumée et dite.** Jouer moins en local, c'est découvrir plus de rouges dans
le pipeline, sur le runner partagé de l'équipe (§8.1). Elle est bornée : le **lint tourne toujours
en entier** (quelques secondes, et c'est l'échec le plus bête à faire découvrir à quelqu'un
d'autre), les tests du code touché aussi, et `/mr-fix` traite le reste. Le filet, lui, ne
laisse jamais croire à un vert qu'il n'a pas mérité : le job dit ce qu'il a joué et pourquoi, le
verdict porte la mention **« Périmètre réduit »**, et le seuil de couverture — qu'un
sous-ensemble ne peut pas tenir — n'est appliqué qu'en `--complet` et en CI. Le sens de dérive est
toujours le même : **ce que le script ne sait pas classer élargit le périmètre**, il ne le
rétrécit pas.

**Le filet est la source unique, et les prompts y renvoient (#310).** Deux commandes vérifiaient en
local sans jamais le citer : [`/mr-fix`](../.claude/commands/mr-fix.md) portait sa propre **table de
miroirs** (`shellcheck`, `ruff`, `pytest -n auto`, `mypy`) et
[`/ticket-finish`](../.claude/commands/ticket-finish.md) une **heuristique de détection** (« si un
outil de lint/test est détecté dans le dossier concerné… »). Elles renvoient désormais l'une à
`bash scripts/ci/local.sh --only <job>`, l'autre à `bash scripts/ci/local.sh` avant le push. Ce
n'est pas qu'une économie de mots :

- la table prescrivait `pytest -n auto` **sur la suite entière**, c'est-à-dire l'inverse de cette
  section — et un prompt est ce que la session lit **en dernier**, donc c'est lui qui l'emporte sur
  la règle générale. Le coût, ~10 min au lieu de ~40 s, se payait en plein diagnostic d'un pipeline
  rouge ;
- une recette recopiée **fige** les jobs du workflow au jour où elle a été écrite, quand le
  filet, lui, les y **lit** (§8.4, levier 2) — la table ignorait déjà le découpage par fichier de
  shellcheck (levier 3) et le miroir LF.

Le garde-fou est dans [`tests/test_ci_local.py`](../tests/test_ci_local.py) : aucun **bloc de code**
ni **cellule de tableau** de `.claude/**` ne joue `pytest`/`ruff`/`mypy`/`shellcheck` hors du filet —
la prose, elle, garde le droit de nommer une forme pour la proscrire (même parti pris que les tests
de prompts de #196 et #233, §11.7). Seule échappatoire : viser **la** suite rouge
(`tests/test_<suite>.py`), la boucle courte de cette section. `Bash(bash scripts/ci/local.sh:*)` est
autorisé **des deux côtés** — [`.claude/settings.json`](../.claude/settings.json) pour la session
interactive, [`settings.run.json`](../scripts/orchestrate/settings.run.json) pour l'autonome :
prescrire une commande sans l'autoriser, c'est fabriquer un refus par ticket (§11.7).

**3. Un lancement qui coûtait 7 min 24 s (#285).** Le mode rapide était bien actif — périmètre
réduit à 3 suites — et c'est justement là que le bât blessait : le travail récent du dépôt est
massivement dans `scripts/**`, donc le diff sélectionnait exactement les suites d'**outillage** que
le levier 2 voulait éviter. Décomposition mesurée, poste Windows 16 cœurs, arbre propre :

| job | avant | après | |
|---|---|---|---|
| shellcheck | 53 s | **16 s** | 21 scripts, via le repli Docker |
| python-lint | 0 s | 0 s | ruff |
| pytest | 6 min 28 s | inchangé | 268 tests, 3 suites — moitié moins de processus pour le même temps (voir plus bas) |
| mypy | 1 s | 1 s | |

**shellcheck est superlinéaire en taille totale reçue d'un coup** : dans le même conteneur,
1 fichier 1 s · 6 fichiers 3 s · 11 fichiers 16 s · 21 fichiers **38 s**, contre **12 s** pour
vingt-et-un appels d'un fichier. Le démarrage du conteneur ne pèse que 2 s, et le montage bind
n'est pas en cause (36 s avec ou sans copie interne). D'où **un appel par fichier**, la boucle
restant **dans** le conteneur — un `docker run` par fichier rendrait au démarrage (21 × 2 s) plus
que la boucle ne fait gagner.

Le découpage **n'est pas neutre**, et c'est le point à comprendre avant d'y toucher : shellcheck ne
suit un `# shellcheck source=…` que si le fichier sourcé est **lui aussi sur la ligne de commande**
(ou si `-x` est passé). L'appel groupé liait donc les scripts entre eux **sans le dire**, et le
découpage a fait apparaître un SC2034 sur une variable qu'un script posait pour une fonction du
fichier qu'il sourçait (les deux ont depuis été supprimés avec l'outillage runner, §8.1). Trois
conséquences :

- **Le pipeline est découpé de la même façon**
  ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)). Découper
  d'un seul côté ferait du filet un contrôle **plus strict que la CI qu'il prédit** : rouge en
  local, vert en pipeline, sur des remarques que rien n'explique. Le sens de l'écart est toujours
  le même — moins de contexte, donc **plus** de remarques, jamais moins : un faux rouge est
  possible, un faux vert ne l'est pas. Il reste que les deux verdicts doivent coïncider, et
  `tests/test_ci_local.py` l'épingle sur les fichiers versionnés. Le pipeline y gagne au passage
  les mêmes 35 s → 12 s.
- **`-x` a été écarté par la mesure**, alors qu'il rétablirait le lien depuis le disque : il fait
  ré-analyser `lib.sh` par chacun des huit scripts qui la sourcent, et le job repasse à **34 s** —
  le découpage ne sert plus à rien.
- **Un couplage entre scripts se déclare désormais sur place**, par un `# shellcheck disable=`
  commenté. C'est un gain propre : la dépendance se lit là où elle vit, au lieu de tenir à la
  forme de l'invocation.

**Côté pytest, la contrainte est la mémoire, pas les cœurs.** `-n auto` demande un worker par cœur
logique — 16 ici. Un worker de cette suite pèse ~130 Mo, soit ~2 Go pour ~1,8 Go de RAM libre : le
poste pagine, et les deux réglages finissent **à égalité** (74 s chacun sur `tests/test_worktree.py`
à la mesure du ticket ; 48 s contre 52 s à la vérification, l'écart change de signe avec la charge
du poste). Le filet **plafonne** donc à `min(cœurs, 8)`. C'est un plafond et non un forçage : une
machine à 4 cœurs garde `-n 4`, ce que `-n auto` lui donnait déjà — d'où un réglage posé sans
distinguer les plateformes, et un petit runner qui ne se retrouve jamais avec plus de workers
qu'avant. `MAESTRO_PYTEST_WORKERS` déplace le plafond ; le **job CI garde `-n auto`**, il tourne
dans un conteneur dédié où la mémoire n'est pas le facteur limitant.

Ce plafond ne rend pas les 6 min 28 s de pytest : il en supprime la moitié des processus pour le
même temps. Ce qui les explique reste le coût unitaire d'un test d'outillage — **créer un processus
sous Windows coûte ~50 ms** (contre 2-3 ms sous Linux), et chaque test lance un script shell qui
forke des centaines de fois. Le profil des durées est plat : du 1er test le plus lent (59,6 s) au
25e (33,8 s). La vraie réponse reste celle du levier 2 — **viser directement la suite** pendant
l'itération, le filet complet avant le push. ⚠ *Viser* la suite ne dit pas encore **où** la jouer :
c'est le levier 4 qui tranche, et la réponse dépend de la famille de suite (§8.4bis).

**4. Et le levier qui les dépasse tous : pytest joue dans un conteneur Linux (#372).** Les trois
leviers ci-dessus optimisent le régime ; celui-ci en change. Le filet était retombé à **~15 min** sur
un diff touchant `scripts/**` — le cas le plus courant du dépôt — et l'enquête a trouvé quatre
causes, dont une qui domine les autres d'un ordre de grandeur.

Les suites d'outillage sont faites à **100 % de sous-processus shell** : `test_orchestrate` en lance
plus de deux cents à elle seule, sans compter les forks internes de `run.sh`, `lib.sh` et `awk`.
Leur durée est donc une fonction quasi linéaire du prix d'un `CreateProcess` — et ce prix diffère de
**trois ordres de grandeur** entre les deux plateformes. Mesuré le 2026-08-21, dos à dos, même
machine, même `-n 8`, **aucune ligne de test modifiée** :

| | Windows / MSYS | Conteneur Linux | |
|---|---|---|---|
| `bash -c 'exit 0'` | ~800 ms | **< 1 ms** | |
| `tests/test_worktree.py` (97 tests) | 424 s | **13,5 s** | ×31 |
| les 6 suites du périmètre `scripts/**` | 14 min 33 (547 tests) | **52,9 s** (599) | |
| la suite **ENTIÈRE** | injouable en boucle courte | **1 min 51** (2 142) | |

Ce n'est pas propre à MSYS — `cmd //c exit` coûte 842 ms et `git --version` 584 ms sur la même
machine —, et ce n'est pas une constante : le même poste mesurait **96 ms** trois jours plus tôt. Ce
qui a changé entre les deux, c'est son état (mémoire *committed* à 46 365 Mo pour une limite de
48 033, soit 96,5 %). **Tant que le filet joue sous MSYS, sa durée reste indexée sur l'état du
poste** — un onglet de navigateur de plus la fait bouger, et aucun réglage du dépôt n'y peut rien.
C'est ce qui distingue ce levier des trois précédents : il ne rend pas seulement le filet plus
rapide, il le rend **prévisible**.

**Le second gain n'est pas de vitesse, et c'est le plus important** : le filet joue désormais sur
**l'OS du verdict**. §8.7 raconte comment 285 tests d'outillage n'avaient jamais tourné ailleurs que
sous Windows, et comment le premier runner Linux muni de git en a trouvé **16 rouges d'un coup**,
dont un bug de production. Cette classe d'écart ne se voyait **qu'au merge** : le filet local ne
pouvait structurellement pas l'attraper.

```bash
bash scripts/ci/local.sh                 # conteneur ; démon éteint → Docker Desktop est DÉMARRÉ
bash scripts/ci/local.sh --conteneur     # exigé : ÉCHOUE au lieu de retomber
bash scripts/ci/local.sh --natif         # l'ancien régime
```

Les points de conception, à comprendre avant d'y toucher :

- **Le repli natif est annoncé, deux fois** — sur la ligne du job et dans un bloc avant le verdict.
  C'est la raison d'être du mécanisme : un filet qui retomberait en silence rendrait un vert de
  quinze minutes en se faisant passer pour un vert d'une minute, et surtout un vert qui n'a pas vu
  ce que la CI verra. `--conteneur` **échoue** au lieu de retomber, pour les contextes où personne
  ne lit la sortie.
- **Mais un démon éteint n'est pas un poste sans Docker (#425).** Le repli ci-dessus a été écrit
  pour le *poste sans Docker* ; celui qu'on rencontre tous les jours est le *démon éteint* — Docker
  Desktop installé, simplement pas démarré, typiquement après un redémarrage. Il empruntait le même
  chemin et payait le même prix, alors qu'il se répare en une commande : le filet **démarre donc
  Docker Desktop lui-même** avant de conclure au repli. Ce qui sépare les deux cas est la présence
  du plugin CLI **`docker desktop`**, livré *avec* Docker Desktop — un critère qui ne devine aucun
  chemin d'installation et ne distingue aucune plateforme. Quatre points de conception :
  - **Le démarrage est annoncé avant d'être tenté**, comme la construction d'image et pour la même
    raison : une attente muette d'une demi-minute passe pour un blocage. Mesuré le 2026-08-22 sur
    le poste de référence, démon froid : `docker desktop start` rend la main en **35 s**, et
    `docker version` répond dans la foulée. Le plafond par défaut est à 180 s.
  - **Sans le plugin, rien n'est tenté** — ni commande, ni délai. Le poste réellement sans Docker
    garde le repli à l'identique, ce pour quoi il a été écrit. Un démarrage qui **échoue** nomme sa
    cause et retombe en natif : ce filet rend un verdict, et un natif annoncé vaut mieux qu'un job
    qui refuse de jouer.
  - **La sonde reste gratuite.** `--list` doit dire la commande *réellement* jouée (#194) : il
    annonce donc « conteneur Linux — après démarrage de Docker Desktop » **sans rien démarrer**.
    Les deux moitiés comptent — annoncer « natif » trahirait le contrat, démarrer pour pouvoir
    l'annoncer trahirait le fait qu'une sonde ne coûte rien. Constater la présence du plugin
    tranche les deux.
  - **La décision vit dans la plomberie partagée**, donc `pytest.sh` en hérite sans une ligne
    (§8.4bis) — deux implémentations à tenir d'accord seraient le premier moyen pour qu'un lanceur
    cesse d'exécuter ce que le filet prédit.

  `MAESTRO_DOCKER_DEMARRAGE=0` éteint la tentative, `MAESTRO_DOCKER_DEMARRAGE_DELAI` déplace le
  plafond.
- **L'étiquette de l'image porte l'empreinte de `pyproject.toml` et de
  [`scripts/ci/pytest.Dockerfile`](../scripts/ci/pytest.Dockerfile).** Une dépendance ajoutée au
  dépôt change l'étiquette, donc l'image manque, donc elle est reconstruite : personne n'a à s'en
  souvenir, et une image périmée ne peut pas rendre un vert sur des dépendances qu'elle n'a pas.
- **Et la précédente est ramassée** (#463). Le corollaire du point ci-dessus manquait : une image
  neuve ne *remplace* pas l'ancienne, elle s'ajoute à côté. Mesuré le 2026-08-24, trois jours après
  #372 : deux `maestro-pytest` côte à côte, dont une périmée, à **835 Mo de couches propres**
  chacune (la base `python:3.11`, 1,595 Go, est partagée — le coût unitaire est donc très inférieur
  aux 2,43 Go qu'affiche `docker images`). Or `pyproject.toml` et le Dockerfile ont bougé **31 fois
  en 3 mois**, soit ~8,6 Go par mois que rien ne ramassait.

  ⚠ **Ce qui rend l'affaire sérieuse n'est pas le disque, c'est le cliquet.** `docker_data.vhdx`
  **n'est pas sparse** (vérifié) : il grandit et ne rétrécit jamais de lui-même. Chaque Go qui y
  entre est pris sur le disque **définitivement**, y compris après suppression de l'image —
  récupérer vraiment demande un **compactage explicite**, Docker arrêté, qui n'est pas automatisé
  ici (il touche le stockage de toute la machine, pas le seul dépôt). Autrement dit : **supprimer
  après coup ne rend rien, seul le fait de ne pas entrer protège.** C'est pourquoi le ramassage est
  *préventif* — accroché à la construction, l'instant où la précédente devient périmée, même parti
  pris qu'en #438 où le pilote ramasse sur le **verdict** du merge et non dans sa boucle — et non
  un ménage périodique. Le chemin nominal (image déjà là) sort avant, donc une itération ordinaire
  ne paie pas un appel docker de plus.

  Trois choix à ne pas défaire : le ciblage passe par `docker images "$PYTEST_IMAGE_NOM"`, qui ne
  **peut** rien rendre d'autre — jamais de `docker system prune`, qui emporterait l'image
  **courante** (elle n'est « active » que le temps d'un conteneur) et ferait payer une
  reconstruction complète, l'inverse exact du but ; la courante est gardée par comparaison
  **exacte** et jamais par un tri par date, « la plus récente » et « celle dont l'empreinte est
  courante » divergeant dès qu'on revient sur une branche antérieure ; et le tout est
  **best-effort**, un `rmi` refusé étant laissé là plutôt que de transformer une question de ménage
  en verdict rouge. `MAESTRO_PYTEST_GC=0` l'éteint ; `bash scripts/ci/pytest.sh --gc` fait le
  rattrapage à la demande — et, lui, **dit** quand il n'a rien à retirer ou qu'il est éteint, là où
  le ramassage automatique reste muet.
- **Le dépôt est monté, jamais copié** — c'est le code de la branche, travail non commité compris.
  Et **jamais à la racine** : monté à `/w`, le parent du dépôt est `/`, et
  `test_projets.py::test_depot_maestro_refuse` rend `racine-de-disque` au lieu de
  `au-dessus-du-depot-maestro`. Le montage est à `/maestro/depot`. Trouvé par les tests.
- **L'image part de `python:3.11` PLEINE et sans identité git globale** — les deux moitiés
  indissociables de #333 (§8.7). L'image pleine porte git ; l'installer par `apt-get` mettrait les
  miroirs Debian sur le chemin critique de chaque construction (le pipeline de !269 est mort
  dessus). Et poser un `user.name` global remasquerait exactement le bug que #332 a trouvé.
- **L'image installe un paquet vide, puis en efface les sources.** `pip install -e ".[dev]"` a
  besoin d'un paquet pour résoudre la liste des dépendances : on lui en donne un vide, puis on
  supprime son répertoire — l'éditable pointe alors vers rien. Après quoi `import maestro`
  n'a plus qu'une source possible — le dépôt monté, désigné par `PYTHONPATH`. Elle ne le
  **désinstalle pas** : la première version le faisait, et la suite entière l'a refusée —
  `[project.scripts]` déclare une dizaine de points d'entrée que la désinstallation emporte, dont
  le `maestro-sandbox-shim` que `tests/test_isolation.py` exige à côté de l'interpréteur. La question de #194
  (« quel paquet `maestro` est testé ? ») y devient **sans objet** plutôt que résolue : la sonde
  n'est jouée que dans le régime natif, où le venv partagé par jonction la rend nécessaire.
- **`-n auto` dans le conteneur, comme la CI.** Le plafond `min(cœurs, 8)` du levier 3 est un fait
  sur la **mémoire du poste Windows**, jamais un fait sur pytest — re-mesuré là-bas sur les six
  suites du périmètre (598 tests) : `-n 4` 177 s · `-n 8` 63 s · `-n 16` 56 s · **`-n auto` 46 s**,
  tous verts. Sous Windows, `-n 16` faisait *pire* que `-n 8` (11 min 37) **et** rougissait quatre
  tests de la vue console par saturation. Le plafond reste donc au régime natif, et disparaît de
  l'autre. `MAESTRO_PYTEST_WORKERS` le déplace des deux côtés.

Réglages : `MAESTRO_PYTEST_REGIME=auto|conteneur|natif`, `MAESTRO_PYTEST_IMAGE` pour le nom de
l'image, `MAESTRO_DOCKER_DEMARRAGE=0` et `MAESTRO_DOCKER_DEMARRAGE_DELAI` pour le démarrage
automatique du démon (#425, ci-dessus). Garde-fous dans
[`tests/test_ci_local.py`](../tests/test_ci_local.py), qui n'ouvre **aucun** conteneur : c'est un
shim `docker` qui répond, et ce sont les *décisions* du script qu'on lit dans son journal. Depuis
#425 ce shim **tient une séquence** (un témoin fait répondre `version` une fois `desktop start`
passé) — sans mémoire, un double ne peut pas distinguer « le démon ne répond pas » de « le démon ne
répond pas *encore* », qui est précisément ce qu'on vient observer. Le `docker` neutralisé de la fixture y est devenu le garde-fou central — sans lui,
chaque test monterait un vrai conteneur sur son dépôt jetable, où les shims du `PATH` ne franchissent
pas la frontière.

Les trois autres causes des 15 min, pour mémoire, et parce qu'elles restent vraies : les tests
ajoutés depuis #285 sont d'une autre échelle (les 25 plus lents du périmètre lui sont tous
postérieurs) ; `lib.sh` avait gagné **trois forks au chargement** (`GL_ICI` par
`$(cd "$(dirname …)" && pwd)`, pour une variable utile à trois lignes), corrigé en résolution sans
fork — marginal d'un chargement 61,9 ms → 12,5 ms ; et shellcheck a doublé parce que `lib.sh` a
doublé. Elles se partagent désormais une **minorité** du temps.

**Le levier qui n'est pas dans le dépôt : shellcheck natif.** `winget install koalaman.shellcheck`
supprime le repli Docker — donc les ~2 s de conteneur. ⚠ Son second argument est **caduc depuis #372** :
il valait « et avec lui le besoin de Docker Desktop pendant le filet, et ses ~500 Mo de
`vmmemWSL` », or le job pytest a désormais besoin du démon — et le troque contre un facteur
quinze. Rien dans le dépôt ne peut le faire à la place de qui installe ; le filet, lui, le rappelle déjà quand
shellcheck manque (« `winget install koalaman.shellcheck` (ou `docker pull …`) »).

**Ce que la mesure a écarté** — consigné pour que personne ne le réessaie :

| Piste | Verdict |
|---|---|
| Exclusions Defender sur le dépôt | **Aucun effet.** Le coût est celui de `CreateProcess` lui-même (~60 ms, y compris pour `cmd.exe`), qu'aucune exclusion de chemin ne couvre. |
| EDR (FortiClient) | **Hors de cause** : installé mais à l'arrêt. |
| Disque | **Hors de cause** : NVMe local. |
| Régler le problème **sous Windows** | **Écarté par #372** : le coût de `CreateProcess` n'est pas réglable depuis le dépôt, et il varie avec l'état du poste (96 ms le 2026-08-18, ~800 ms le 2026-08-21, même machine). On change d'OS plutôt que de l'optimiser. |
| Montage bind du conteneur shellcheck | **Hors de cause** : 36 s avec ou sans copie interne. |
| `-x` pour garder le lien entre fichiers | **Écarté** : 34 s, le découpage ne rapporte plus rien (ci-dessus). |


### 8.4bis Itérer sur une suite : le lanceur, et où jouer selon la famille (#405)

#372 a mis le job pytest du filet dans un conteneur Linux, mais **le régime n'était joignable que
par `local.sh`**, dont le périmètre est déduit du diff. Viser une suite pendant l'itération
retombait donc sur un `python -m pytest` **natif** — et c'est là que l'écart coûte le plus cher.
Mesuré le 2026-08-21 sur `tests/test_cycle_de_vie.py`, même poste, même suite :

| Régime | Durée |
|---|---|
| natif Windows (`.venv/Scripts/python.exe -m pytest`) | **~8 min** |
| conteneur, sans xdist | 1 min 51 |
| conteneur, `-n auto` | **21 s** (×18) |

D'où un point d'entrée qui prend des arguments pytest **arbitraires** :

```bash
bash scripts/ci/pytest.sh tests/test_cycle_de_vie.py -q
bash scripts/ci/pytest.sh tests/test_worktree.py -k ensure -x
bash scripts/ci/pytest.sh tests/test_engine.py::test_boucle --natif
```

**Où jouer, par famille de suite.** Le conteneur n'est pas un régime universellement meilleur, et
c'est la nuance que la doc taisait :

| Famille | Reconnue à | Où | Pourquoi |
|---|---|---|---|
| **Outillage** | elle **nomme un script** du dépôt (`worktree.sh`, `lib.sh`, `run.sh`…) | **conteneur** | 100 % de sous-processus shell ; ~800 ms par fork sous MSYS contre < 1 ms sous Linux |
| **Applicative** | elle ne pilote aucun script (`test_engine.py`, `test_projets.py`…) | **natif**, indifféremment | 6,3 s en natif, et le conteneur coûte ~6 s de démarrage + montage : le gain est nul |

C'est **la même dérivation que le périmètre du filet** (§8.4, levier 1) — une suite est
« outillage » si elle nomme un script du dépôt —, et ce n'est pas un hasard : les deux répondent à
la même question, « cette suite passe-t-elle son temps à forker ? ».

**Ce que le lanceur n'est pas : un verdict.** Il ne calcule aucun périmètre, n'applique aucun seuil
de couverture, ne rejoue pas le lint. Avant de pousser, c'est toujours `bash scripts/ci/local.sh`.
Les deux se distinguent par la question posée, pas par la vitesse : le filet demande « est-ce que ça
passe ? », le lanceur « qu'est-ce que ça donne, là, maintenant ».

Les points de conception, à comprendre avant d'y toucher :

- **Utilisable en session autonome comme en interactive** (#436). Le lanceur est allowlisté des
  **deux** côtés — [`.claude/settings.json`](../.claude/settings.json) et
  [`scripts/orchestrate/settings.run.json`](../scripts/orchestrate/settings.run.json) — au même
  titre que le filet, et pour la même raison : c'est un verbe qui ne lit ni n'écrit la forge et ne
  touche à aucune branche. Sans règle, il était **refusé** en run — sans personne pour approuver
  (§11.7) — et coûtait un prompt par appel en interactif, si bien qu'il n'a **jamais** été invoqué
  dans un journal de run (constat du 2026-08-23 : zéro invocation, contre 5 à 10 `python -m pytest`
  natifs par ticket). Les prompts de session (`prompt_ticket`, `prompt_mrfix`) le nomment **avec sa
  règle de choix**, les deux branches ensemble : autoriser une commande que personne ne nomme ne la
  fait pas exister, et n'en dire que la moitié enverrait les suites applicatives dans un conteneur
  qui les ralentit. C'est la leçon de #310 prise dans l'autre sens — là-bas un prompt prescrivait
  une recette que le filet portait déjà, ici il taisait un outil que rien d'autre n'annonce.
- **La plomberie est PARTAGÉE, pas recopiée**
  ([`scripts/ci/pytest-regime.sh`](../scripts/ci/pytest-regime.sh), sourcé par les deux). Régime,
  empreinte de l'image, point de montage, identité git, workers, garde-fous du venv (#194) : une
  seule implémentation. Deux copies auraient divergé, et un filet qui ne prédit plus ce que le
  lanceur exécute ramène exactement la phrase qu'on essaie de supprimer — « ça passe chez moi ».
  `tests/test_ci_local.py` l'épingle : chaque fonction de la plomberie doit être définie **une
  seule fois**, et dans la bibliothèque.
- **Le parallélisme est ajouté d'office DANS LE CONTENEUR, et jamais en natif.** Dans le conteneur
  c'est l'essentiel du gain (1 min 51 → 21 s) et c'est le drapeau même de la CI. En natif, c'est la
  règle déjà écrite pour le filet — démarrer les workers coûte ~5,5 s, soit plus qu'une suite
  applicative ciblée —, et elle a été **mesurée sur ce lanceur** : `tests/test_engine.py` fait 6,3 s
  en série contre **37,5 s à `-n 8`**. Le parallélisme d'office rendait donc six fois plus lent le
  cas même qu'il devait servir ; une suite d'outillage assez grosse pour rentabiliser des workers
  est de toute façon celle qu'il faut jouer *ailleurs*.
- **Jamais contre un choix explicite.** Un `-n`/`--numprocesses`/`-p no:xdist` déjà passé l'emporte,
  et trois arguments disent qu'on veut **regarder** tourner — `--pdb`, `-s`, `--capture=no` — que
  xdist vide de leur sens en capturant et entrelaçant la sortie par worker. À noter : pytest **ne
  s'en plaint pas** (vérifié, `-n auto --pdb` passe sans broncher), il les rend seulement inutiles —
  le pire des deux, puisque rien ne le signale.
- **Le natif subi et le natif voulu ne se disent pas pareil.** Un repli se crie et nomme sa cause ;
  un `--natif` demandé s'annonce sobrement. Avertir d'un facteur vingt celui qui a raison de jouer
  en natif (suite applicative) est du bruit qui apprend à ne plus lire les avertissements.
- **Il dit toujours où il a joué**, avant de jouer et non après : sur une suite d'outillage l'écart
  est d'un facteur vingt, donc qui lit « NATIF » sait tout de suite qu'il a le temps d'aller
  chercher un café, et pourquoi. `--conteneur` **échoue** au lieu de retomber, comme pour le filet.
- **Il ne redirige rien.** `pytest_conteneur` a perdu sa redirection vers le journal, qui est passée
  aux appelants : le filet l'envoie dans son journal (il rend un verdict, la trace ne sert qu'en cas
  d'échec), le lanceur la laisse à l'écran (sur vingt secondes, voir les points défiler *est*
  l'information, et une trace qu'il faut aller chercher dans un fichier est une trace qu'on ne lit
  pas).
- **Sans argument, il rend l'aide** au lieu de collecter toute la suite : une collecte surprise se
  paie en minutes. Qui veut tout jouer le demande (`pytest.sh tests/`) ou passe par le filet
  (`local.sh --complet`), dont c'est le métier.
- **Pas de TTY dans le conteneur.** `docker run -it` est refusé depuis un Git Bash (« the input
  device is not a TTY »), donc pytest y voit un tube et éteint la couleur. Le lanceur la rallume par
  `--color=yes` quand **sa** sortie est un terminal — l'argument passe avant ceux de l'appelant, si
  bien qu'un `--color=no` explicite gagne quand même.

### 8.5 Un journal se lit là où on travaille (#234)

Un job rouge ne vaut que par la **raison** qu'il donne, et cette raison est dans son journal. Le
filet écrivait le sien dans `${TMPDIR:-/tmp}/maestro-ci-local/` et renvoyait vers ce chemin
**absolu, hors du répertoire de travail** — que le CLI refuse d'ouvrir sans approbation. En session
interactive c'est un clic ; en session autonome (§11) il n'y a personne pour le donner. Le flux du
run de #200 montre le contraste à l'état pur : `tail -5` sur le scratchpad de la session **passe**,
`tail -60 /tmp/maestro-ci-local/pytest.log` est **refusé**. La session a essayé cinq variantes
(`grep -nE`, `grep | tail`, `awk`, `tail`) puis a abandonné — elle n'a jamais su pourquoi ses tests
échouaient. **13 refus sur 5 sessions.** C'est le seul refus qui prive d'une **information** plutôt
que d'un geste : les autres se contournent, celui-là rend aveugle sur son propre verdict.

D'où la règle, qui vaut pour tout script du dépôt :

> **Ce qu'un script invite à lire s'écrit sous la racine du worktree**, dans `.maestro/<domaine>/`
> (gitignoré), et **le chemin affiché est relatif à cette racine**. Ce que personne ne lit —
> brouillons de calcul, profils jetables, caches d'installation — reste dans le temporaire du
> système.

Le partage se fait sur *qui lit*, pas sur *qui écrit* :

| Écrit sous la racine (`.maestro/…`) | Reste dans `${TMPDIR:-/tmp}` |
| --- | --- |
| `ci-local/<job>.log` — filet CI (§8.4) | miroir LF de shellcheck : effacé avant le verdict, jamais montré |
| `setup/<étape>.log` — `setup.sh`, cité en cas d'échec | fichiers de `env-pull.sh` : ils portent des **valeurs de secrets** |
| `controltower/<api>-<ui>/{api,ui,navigateur}.log` | jeton de session, PID du chien de garde, profil jetable du navigateur |
| `presentation/{api,build,ui}.log` | cache npm des captures : des centaines de Mo, partagés entre clones |
| `orchestrate/<run-id>/` — déjà le cas depuis #167 | brouillon de calcul de `queue.sh` |
| `session/` — l'atelier d'une session dans son worktree (#307, §11.7) | règles d'allowlist relues par `journal.sh refus` : un calcul, jamais ouvert |

La règle vaut aussi pour ce qu'une **session** écrit, et pas seulement pour ce qu'un script écrit à
son intention (#307) : son répertoire temporaire et `/tmp` sont hors du répertoire de travail, donc
un fichier qu'elle y dépose lui devient illisible au tour suivant. D'où `.maestro/session/`, monté
par `worktree.sh` dans chaque worktree — §11.7.

Deux points à ne pas défaire :

- **Le filet CI fait table rase à chaque lancement.** Dans `/tmp`, le système faisait le ménage ;
  sous la racine, personne ne le ferait. Et un `pytest.log` de la veille laissé à côté d'un run qui
  n'a pas joué pytest est pire qu'absent — il ment sur ce qui vient d'être vérifié.
- **L'audit se refait par recherche.** `grep -rn "TMPDIR\|/tmp" scripts/` doit ne rendre que des
  cas de la colonne de droite, et **chacun porte en commentaire la raison** de son maintien : la
  vérification est ainsi une relecture, pas une réenquête. Le cas de `setup.sh` n'est pas
  théorique — `/ticket-start` l'appelle pour rattraper une dérive de dépendances (§9.4), et c'est
  une session sans humain qui en lit l'échec.

### 8.6 Une seconde CI, en miroir sur GitHub — l'expérience de #332

> **Expérience close.** Elle a rendu ses mesures (`docs/27`), la migration totale est décidée
> (#335), et #338 a donné à `.github/workflows/ci.yml` son déclencheur définitif : lire **§8.8**
> pour l'état actuel. Cette section reste pour ce qu'elle explique — la traduction job à job, les
> deux jetons opposés — mais **son premier point n'est plus vrai** : le déclencheur n'est plus
> `push`.

Le cadrage #331 instruit une migration vers GitHub, poussée par trois moteurs : la CI, l'intégration
Claude Code et, plus tard, la visibilité. Deux de ses inconnues ne se répondent pas sur le papier —
**ce que coûte réellement un pipeline en minutes-job**, et **si la traduction des cinq jobs rend le
même verdict**. D'où #332 : un dépôt GitHub **privé** alimenté par un **miroir push** depuis GitLab,
sur lequel `.github/workflows/ci.yml` rejoue les mêmes jobs, **en double et sans autorité**.

> ⚠ **Pendant l'expérience**, le verdict qui conditionnait un merge restait celui de
> `.gitlab-ci.yml` : la CI miroir ne bloquait rien, n'était requise nulle part, et se supprimait
> avec le dépôt qui la porte si #331 concluait « non ». C'est ce point que #338 renverse (§8.8).

Quatre choix structurent le fichier, et chacun répond à une contrainte qui n'existe pas côté GitLab :

- **`on: push`, jamais `on: pull_request`** *(remplacé par #338 — voir §8.8)*. Un miroir push ne
  réplique que des branches et des tags : il ne crée aucune pull request, donc `pull_request` ne se
  déclencherait littéralement jamais. Conséquence à connaître avant de lire les chiffres — côté GitLab un pipeline ne part que
  sur `merge_request_event` (§8, #165), donc **les deux cadences ne sont pas comparables**. C'est
  sans importance : la mesure cherche le **coût d'un pipeline**, à multiplier ensuite par le nombre
  de pipelines que GitLab joue réellement. Compter les runs GitHub répondrait à une question que
  personne ne pose.
- **Un job `perimetre` sans équivalent GitLab.** GitLab attache `rules: changes:` à un job ;
  GitHub n'offre `paths:` qu'au niveau du **workflow entier**, ce qui sauterait aussi les jobs
  Python. Le portier calcule le périmètre en `git diff` nu — pas d'action tierce pour trois lignes —
  et `web-build` porte un `if:`. Un job sauté par `if:` est **rapporté** (« skipped »), donc
  compatible avec une branch protection ; c'est le piège inverse qu'on évite — un check requis dont
  le **workflow** ne se déclenche pas n'est jamais rapporté, et la PR reste bloquée pour toujours.
  Sans objet pendant l'expérience — mais c'était la cible, et c'est devenu effectif avec #338 (§8.8).
- **Le shellcheck préinstallé du runner, pas l'image de GitLab.** `koalaman/shellcheck-alpine` en
  `container:` ferait échouer `actions/checkout` : une action JavaScript exige un Node dans le
  conteneur, que l'image alpine n'a pas. La version est imprimée des deux côtés — une dérive de
  verdict se lira dans les logs, et c'est une des choses que l'expérience doit relever.
- **La clé `coverage:` n'a pas d'équivalent** et n'en avait pas besoin : le garde-fou est
  `--cov-fail-under=90`, qui fait déjà échouer le job. Seul l'**affichage** du taux est relogé, dans
  le résumé du run.

Le PAT GitHub vit **côté GitLab**, dans Settings › Repository › Mirroring repositories : c'est
GitLab qui pousse, donc GitLab qui s'authentifie — un identifiant vit chez le **client**, jamais
chez le serveur qui l'a émis. Fine-grained, limité au dépôt miroir, `Contents` **et `Workflows`** en
écriture : sans le second, GitHub refuse tout push touchant `.github/workflows/`, c'est-à-dire
exactement le fichier que l'expérience installe. Il **expire** obligatoirement, et le miroir
s'arrêtera alors en silence — l'erreur n'apparaît que dans cette même page GitLab.

**Un second token, en lecture seule, pour que la session lise les runs.** Le premier ne sert qu'au
miroir et ne quitte jamais GitLab ; il ne donne à la session aucun accès à GitHub. Or la mesure
attendue par #331 — minutes-job par pipeline, durée de `pytest`, écarts de verdict — ne vit que
côté GitHub, et les **minutes facturées** se lisent à
`/repos/{owner}/{repo}/actions/runs/{id}/timing` (champ `billable`), la durée de mur d'un run
n'étant pas ce qui est décompté. D'où un token fine-grained limité au dépôt miroir,
`Actions: Read` seul, posé par `gh auth login` — geste de la personne, au même titre que
l'authentification de la forge d'alors, la session ne manipulant jamais le secret en clair.

Les deux tokens sont **opposés par construction** et ne peuvent pas se substituer : celui du miroir
**écrit** et vit **chez GitLab** ; celui-ci **lit** et vit **sur le poste**. Ce n'est pas un cumul
de droits, c'est une séparation.

Côté permissions, `gh` était alors **absent de l'allowlist** — les règles de `.claude/settings.json`
visaient toutes `glab`, si bien que chaque `gh run list` aurait demandé une approbation (§11.7 : la
classe de trou que #307 a mesurée). Six règles en lecture s'y ajoutent, dont un `gh api` **borné au
chemin `actions/` du seul dépôt miroir** plutôt qu'ouvert : une règle est un préfixe de commande, et
la borner au chemin est ici la façon la moins large de couvrir `/timing`. La vraie limite reste le
token lui-même, qui ne sait pas écrire.
### 8.7 Git est une dépendance de la suite, pas un confort (#333)

Le job `pytest` tourne dans `python:3.11-slim`, **qui n'a pas git**. Or sept modules d'outillage
montent chacun un vrai dépôt jetable et sont gardés par
`skipif(shutil.which("git") is None)` : **285 tests y étaient sautés à chaque pipeline**, depuis
toujours. Le compte rendu ne le disait nulle part — un job qui saute 285 tests et un job qui les
joue tous rendent le même « vert ».

La conséquence n'est pas restée théorique, et c'est **l'expérience de §8.6 qui l'a révélée** : ces
tests n'ont jamais tourné **que sur des postes de développement**, c'est-à-dire **sous Windows** ;
le premier runner Linux muni de git à les jouer — le miroir GitHub de #332 — en a trouvé **16
rouges d'un coup**, dont un bug de production. Un garde-fou qui saute est plus dangereux qu'un
garde-fou absent : il rend le même vert que la vérification qu'il remplace. À noter pour la
décision de #331 : la CI miroir n'a **aucune autorité**, donc ce qu'elle a trouvé serait reperdu
le jour où elle s'arrête — c'est bien côté GitLab que le trou devait se boucher.

Deux moitiés, et il faut les deux :

- **git est présent** dans le job `pytest`. Il l'est aujourd'hui **par le runner hébergé**, qui
  s'en sert pour le checkout : sur GitHub Actions il n'y a rien à faire, et le seul geste qui le
  reperdrait serait de renvoyer le job dans un `container:` — c'est-à-dire de refaire exactement
  l'image slim d'où venait le défaut. Le remède du temps de la CI GitLab, lui, valait d'être
  raconté : l'**image pleine** `python:3.11` au lieu de `-slim`. L'obtenir par un `apt-get install
  git` au lancement a été essayé et **retiré** : ça met une dépendance réseau sur les miroirs Debian
  dans **chaque** pipeline, donc sur le chemin critique du merge (un pipeline vert est exigé), et le
  pipeline de !269 est mort dessus — « Unable to connect to deb.debian.org », exit 100, avant même
  que pytest démarre. **Un remède qui coûte une panne récurrente à ceux qui mergent n'est
  pas un remède.**
  Aucune **identité** git n'y est posée globalement, et c'est délibéré : le code qui écrit dans le
  dépôt de l'utilisateur porte la sienne par `-c` (`maestro/projets/application.py`). En fournir
  une au runner masquerait à nouveau le défaut que #333 a corrigé — une fusion qui n'échouait que
  sur les machines sans `~/.gitconfig`, c'est-à-dire nulle part où quelqu'un regardait.
- **son absence en CI est une erreur**, pas un saut : `tests/conftest.py` refuse de jouer la suite
  quand git manque **et** qu'une variable de CI est posée (`CI`, `GITLAB_CI`, `GITHUB_ACTIONS`).
  Sur un poste sans git, le `skipif` de chaque module reste la bonne réponse — il dit « cette
  machine ne peut pas répondre » ; en CI le même saut dit « rien n'a été vérifié » avec les mots de
  « tout va bien ». Ce contrôle ne remet rien au vert : il fait qu'un futur retrait de git se voie
  **tout de suite**, au lieu de rendre 285 tests invisibles pendant des mois. La liste de variables
  couvre les deux CI de §8.6, sans quoi le miroir GitHub aurait gardé l'angle mort qu'il a servi à
  découvrir.

Ce qu'il reste à savoir, et qui borne la portée : ces tests-là ne tournent toujours **que sur
Linux en CI et sur Windows en local**. Les écarts entre plateformes ne se voient donc qu'au
croisement des deux, et une différence de comportement d'un outil (`ln -s` contre `mklink /J`,
§9) ne se manifeste que du côté qui la joue. Un banc jetable rejouant la suite dans l'image du
pipeline, avec git, est ce qui a permis de trancher les cinq causes de #333 — c'est reproductible
en quelques lignes de Docker et ça n'a pas besoin d'être versionné.

### 8.8 La CI GitHub en autorité — déclencheur et protection de `main` (#338)

L'expérience de §8.6 a rendu son verdict, la migration est décidée (#335), et
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) cesse d'être un miroir dont personne ne
lit le verdict. Le ticket visait deux choses — le **déclencheur** et la **protection de `main`** — et
n'en a obtenu qu'une : le déclencheur est en place, la protection est **écartée faute d'exister sur
ce plan**. Le reste de la section explique les deux, plus un piège dans lequel il ne faut tomber
qu'une fois, de préférence en le lisant plutôt qu'en le vivant.

> Pendant la migration, `.gitlab-ci.yml` est resté en place et a fait foi pour les MR GitLab ; les
> deux CI ont coexisté jusqu'à la bascule d'`origin` (#343). La CI GitLab et les 1 146 lignes
> d'outillage runner ont été **retirées** par #344 : ce workflow est désormais le seul verdict.

**Le déclencheur : `pull_request`.** Le `on: push` de #332 n'était pas un choix mais une contrainte
du miroir — un miroir push ne réplique que des branches et des tags, il ne crée aucune pull request,
donc `pull_request` ne se serait littéralement jamais déclenché. La contrainte tombe le jour où la
CI fait autorité : ce qu'on veut vérifier est ce qu'on s'apprête à merger. `pull_request` est
l'équivalent exact du `merge_request_event` de GitLab (§8, #165) — ni au push d'une branche sans PR,
ni sur `main` après le merge — et c'est aussi ce qui **réduit** la consommation du dépôt, le miroir
déclenchant jusque-là un run à chaque push pour un verdict que personne ne lisait (`docs/27` §10).
`workflow_dispatch` est conservé : c'est le seul moyen de rejouer la CI hors PR. Aucun filtre
`branches:` — une PR est par définition un candidat au merge —, et les `types:` par défaut couvrent
le cycle de vie d'une PR Maestro, **Draft compris** : une PR en brouillon déclenche bien le
workflow. C'était indispensable tant que `/ticket-finish` ouvrait en Draft et n'en sortait pas ;
depuis #418 la commande passe la PR en **prête** avant de merger (§6), mais le Draft reste sur le
chemin — la PR est ouverte en brouillon puis levée, et sans les `types:` par défaut le pipeline ne
naîtrait qu'à la levée, donc trop tard pour être attendu.

**La protection de `main` : écartée, et c'est une décision.** Le pendant de
`only_allow_merge_if_pipeline_succeeds` (§8.1) **n'est pas en place**, parce qu'il n'est pas
disponible : mesuré le 2026-08-14 sur `/rulesets` et `/rules/branches/main`, deux endpoints en
**lecture seule** qui répondent « *Upgrade to GitHub Pro or make this repository public to enable
this feature* ». La protection de branche, rulesets compris, **n'existe pas sur un dépôt privé d'un
compte GitHub Free** — et le compte propriétaire `automatemaestro-create` est un compte personnel au
plan Free, tandis que #335 a arbitré le dépôt **privé**. Les deux issues possibles ont été
présentées et **toutes deux refusées** (utilisateur, 2026-08-14) : GitHub Pro (~4 $/mois) et le
passage en public (qui aurait renversé l'arbitrage de visibilité de #335).

> ⚠ **Aucun garde-fou de la FORGE n'empêche de merger une PR au rouge sur ce dépôt.** Ce n'est pas
> un oubli ni un blocage à lever : c'est le régime choisi.

⚠ **Mais il y en a un, et c'est le nôtre** (#415/#417, chantier #413). Ce paragraphe a dit « aucun
garde-fou technique » jusqu'à ce chantier, et c'est devenu faux : ce que la protection de branche
aurait tenu — « pas de merge tant que les checks ne sont pas verts » — est désormais tenu par
**`lib.sh merge-mr`**, qui refuse un pipeline rouge (code `4`) et va plus loin que la protection en
exigeant un vert porté par la **tête de la PR** et non par un run antérieur (§6). La correction
compte parce qu'elle a une conséquence pratique : `protect-main.sh` n'est plus le seul recours si un
merge au rouge se produit, et l'arbitrage des 4 $/mois se pose autrement.

⚠ **Et parce que ce garde-fou n'est PAS dans la forge, il ne protège que ce qui passe par lui.** Une
protection de branche est opposable à tout le monde — un clic dans l'interface web, un `gh pr merge`
lancé à la main, une intégration tierce. `merge-mr` ne l'est qu'aux chemins du dépôt : c'est
pourquoi `gh pr merge` reste en `deny` côté permissions **et** dans `guard.sh` (§6), et pourquoi la
question du plan payant n'est pas close par ce chantier — elle est seulement moins urgente. La
différence entre les deux régimes ne s'annule pas, elle se déplace : de « rien ne tient la règle » à
« la règle est tenue **pour les sessions**, jamais pour un humain pressé devant l'interface web ».

Ce que le régime laisse par ailleurs, et qui n'est pas rien : les six verdicts sont **rapportés sur
la PR** et restent lisibles. La CI rejoint ainsi le régime que ce dépôt applique à la **revue**
(§6) — aucune approbation obligatoire, aucun relecteur posé d'office, c'est la **visibilité** qui
déclenche le geste. La règle « on ne merge pas au rouge » n'a jamais changé ; ce qui a changé deux
fois, c'est son gardien : la forge, puis personne, puis nous.

**Le réglage reste écrit et rejouable**, dans
[`scripts/github/protect-main.sh`](../scripts/github/protect-main.sh) — **source unique**, au même
titre que `bootstrap.sh` côté GitLab, et pour la même raison : un réglage cliqué dans une interface
n'est ni relisible, ni rejouable, ni attribuable six mois plus tard. L'écrire sans le jouer est
délibéré — c'est ce qui rend la décision ci-dessus **réversible en une commande** le jour où le plan
change, au lieu d'une enquête à refaire. Idempotent, `--check` pour un diagnostic sans écriture,
code `3` quand le dépôt a répondu mais que le réglage manque (par opposition à `1`, qui dit que
l'outil manque).

```bash
bash scripts/github/protect-main.sh --check    # ce qui est requis aujourd'hui — répond 3
bash scripts/github/protect-main.sh            # poserait les six checks (bloqué par le plan)
```

Un **second** obstacle attend derrière le premier, et le script les nomme séparément parce qu'ils
n'ont pas le même remède — GitHub rend le même `403` dans les deux cas : le PAT fine-grained du
projet n'a pas la permission `Administration` sur le dépôt (GitHub la nomme dans son en-tête,
`administration=read` pour lire et `write` pour poser), à régler dans les réglages du jeton, qui vit
dans le `GH_CONFIG_DIR` du projet (§7.4). Lever le plan sans lever le jeton ne suffirait donc pas.

**Le piège : un check requis qui n'est jamais rapporté bloque la PR pour toujours.** GitLab attache
un filtre de chemins à **un job** (`web-build` ne tourne que si `apps/web/**` change) ; GitHub
n'offre `paths:` qu'au niveau du **workflow entier**. Mettre le filtre là sauterait aussi les jobs
Python — et surtout, un check requis dont le *workflow* ne se déclenche pas n'est pas « sauté » : il
est **absent**, en attente d'un verdict qui n'arrivera jamais, et aucun clic ne débloque la PR. D'où
le job-portier `perimetre`, qui calcule le périmètre en `git diff` nu et expose une sortie, et le
`if:` que porte `web-build` : un job sauté par `if:` est **rapporté** (conclusion `skipped`), ce qui
satisfait une protection là où un workflow non déclenché ne la satisfait jamais. La règle qui en
découle vaut pour toute évolution du fichier : **jamais de `paths:`/`paths-ignore:` sur le bloc
`on:`**, et jamais de renommage d'un job sans le répercuter dans la liste `CHECKS` du script — les
deux produisent la même PR indéfiniment non mergeable.

Sans protection posée, rien de tout cela ne bloque aujourd'hui — et c'est précisément pourquoi la
structure est gardée telle quelle : elle est ce qui permet de poser la protection plus tard **sans
rien réécrire**, et le prix à payer pour l'oublier serait une PR bloquée découverte le jour où on
l'active, sous la pression, plutôt qu'ici.

La base du diff vient de l'**événement** (`github.event.pull_request.base.sha`) et non d'un nom de
branche : sur `pull_request`, le dépôt est checkouté sur le merge commit (`refs/pull/N/merge`) et
`github.ref_name` vaut « N/merge », si bien qu'une comparaison à « main » par son nom ne matcherait
plus jamais rien.

**Deux réglages du script qui ne sont pas des facilités**, à connaître avant de le jouer un jour :

| Réglage | Valeur | Pourquoi |
|---|---|---|
| `strict` (branche à jour avant merge) | `false` | Fidélité à GitLab, qui n'active que `only_allow_merge_if_pipeline_succeeds`. À `true`, il faudrait ramener `main` dans **chaque** PR avant de merger, alors que le rattrapage d'une branche en retard est ici jugé au cas par cas (§8.3). |
| `enforce_admins` | `false` | **C'est ce qui laisserait vivre le miroir.** Jusqu'à #343, `main` du dépôt cible est alimentée par le miroir push depuis GitLab, qui pousse **directement** sur la branche ; une branche protégée refuse les pushes directs, sauf aux administrateurs quand ce champ est faux — et le compte du miroir est le propriétaire. Poser la protection avec `enforce_admins: true` **casserait le miroir**, silencieusement et du côté GitLab, où l'erreur ne s'affiche que dans la page de configuration du miroir (§8.6). Le passer à `true` est un geste délibéré, le jour où plus rien ne pousse sur `main` en dehors des merges de PR. |

**Enfin, une contrainte de calendrier, pas d'outillage** : le déclencheur `pull_request` n'a **pas
été vérifié sur une vraie PR**, et ne pouvait pas l'être. Une PR de test consommerait un numéro de
la séquence issues/PR du dépôt cible — or l'import des 330 tickets (#340) exige que la plage
`#2`→`#333` soit intacte, et #336 l'écrit noir sur blanc : aucune issue ni PR avant l'import, **y
compris à titre d'essai**. Une seule PR brûlerait `#1` ; en tester les deux cas (`web-build` joué
**et** sauté) en demande deux, donc décalerait toute la plage — irréversiblement, GitHub ne
rendant jamais un numéro consommé. La recette attend donc #340 (décision utilisateur,
2026-08-14) : ce n'est pas un test négligé, c'est un test dont le coût est à sens unique.

**Ce qu'il restera à vérifier ce jour-là**, et qui n'est vérifiable que là : que les six jobs
partent bien sur `pull_request`, que `perimetre` calcule le bon périmètre depuis
`base.sha` (c'est le seul point du fichier qui n'a jamais tourné sous cet événement), et que
`web-build` ressort `skipped` — et non absent — sur une PR hors périmètre.

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
bash scripts/git/worktree.sh sessions 152 # retrouve les sessions Claude Code du ticket (§9.7)
```

Le script fait plus qu'un `git worktree add` : il résout la branche comme
[`/ticket-start`](../.claude/commands/ticket-start.md) (`lib.sh branch-for`) et la crée depuis
`origin/main`, recopie le `.env` (gitignoré, donc absent du worktree), **partage par lien**
`.venv/` et `.tools/` (jonction sous Windows : aucun droit administrateur), **installe** les
dépendances de `apps/web`, écrit un `.claude/settings.local.json` dédié et monte
**`.maestro/session/`**, l'atelier où la session écrit ses fichiers de travail — le seul endroit
qu'elle puisse atteindre en chemin relatif (#307, §11.7).

⚠ **Ce partage par lien impose une contrainte au `.gitignore`, et elle n'est pas décorative**
(#333) : un motif terminé par `/` ne matche **que des répertoires**. Or le lien vers `.venv` n'est
un répertoire que sous **Windows** (jonction `mklink /J`) ; sous Linux c'est un `ln -s`, que git
voit comme un **fichier**. `.venv/` et `.tools/` laissaient donc `?? .venv` et `?? .tools` dans
tout worktree monté sous Linux — un worktree **sale dès sa création**. Ça n'a l'air de rien, et
ça désarmait tout le cycle de vie : « travail non sauvegardé » se mesure par
`git status --porcelain`, et c'est ce qui fait **refuser** un retrait (§9.2) — à juste titre, mieux
vaut 535 Mo de trop qu'un commit perdu. `remove` comme `gc` devenaient donc inopérants pour
toujours, et onze tests tombaient en aval sans qu'aucun ne nomme la cause. Les deux motifs
s'écrivent **sans barre oblique finale**, et
[`tests/test_worktree.py`](../tests/test_worktree.py) épingle désormais l'invariant directement :
*un worktree fraîchement monté est propre*.

### 9.1 Monté d'office par `/ticket-start` (#181)

Ces trois commandes restent disponibles, mais **on n'a plus à y penser** : `/ticket-start` monte
lui-même le worktree du ticket, et le **clone principal ne change plus jamais de branche**. On peut
donc y rester sur `main` — lire le code de référence, préparer un autre sujet, relire une PR —
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
worktree courant ; `gh` fonctionne depuis n'importe quel worktree.

**Ce que le workflow adapte.** `main` ne peut être emprunté que par **un seul** worktree à la
fois : tout `git checkout main` échoue ailleurs que dans le clone principal. D'où
`lib.sh start-branch <branche>`, appelé par `/ticket-start` : dans le clone principal il met
`main` à jour ; dans un worktree il branche directement sur `origin/main`, et ne fait rien si la
branche est déjà celle du worktree. (Il a purgé les branches mergées jusqu'à #305, où l'appel a
été déplacé dans `ensure` : §9.5.) De même,
[`/branch-cleanup`](../.claude/commands/branch-cleanup.md) ne bascule pas sur `main` depuis un
worktree. La fin de vie du worktree, elle, ne demande **aucun geste** : elle est ramassée d'office
(§9.2). **Retirer un worktree ne supprime jamais sa branche** — `create`, `remove` et `gc` n'y
touchent pas : la suppression est une décision qui appartient à `cleanup-merged` (§9.5) et à
`/branch-cleanup`, et n'a lieu que sur confirmation du merge par GitLab (§6).

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
bash scripts/git/worktree.sh gc              # ramasse ce qui est soldé
bash scripts/git/worktree.sh gc --check      # dit ce qu'il retirerait, sans rien toucher
bash scripts/git/worktree.sh gc --iid 436    # ciblé : ce worktree-là, et rien d'autre (#438)
```

**Le mode ciblé, et le quatrième déclencheur** (#438). `--iid` restreint les **candidats** à un
ticket : ni balayage des coquilles, ni signalement des orphelins, ni lecture du backlog. C'est ce
qui rend le ramassage jouable **après chaque merge** du drain d'un run (§11.11) — un balayage
complet y coûterait une lecture de forge par worktree **et** par PR mergée, soit N² sur un run de
N tickets, dans la boucle précisément conçue pour ne rien coûter la plupart du temps. Cibler dit
**qui est candidat**, jamais ce qu'on s'autorise sur lui : les trois refus ci-dessous ne bougent
pas, et le premier des trois est celui que le merge donnerait le plus envie de lever (« la PR est
mergée, que reste-t-il à sauver ? ») — or un merge dit ce qui est parti sur `origin/main`, jamais
ce qui est resté sur le disque.

⚠ **Le pilote d'un run l'a, une session non** : `gc` refuse par construction de retirer le worktree
de la **session courante**, et une session qui merge par `/ticket-finish` est justement dedans. Le
pilote, lui, se tient dehors — c'est pourquoi le quatrième déclencheur vit dans
`scripts/orchestrate/run.sh` (`merge_ramasse`) et non dans `lib.sh merge-mr`, où il aurait servi
tout le monde d'un coup mais aurait fait dépendre `lib.sh` de `worktree.sh`, qui en dépend déjà. En
clôture interactive, worktree et branche **restent** donc, et `/ticket-finish` le **dit** au lieu de
le taire : ils partiront au prochain `/ticket-start` ou avec `/branch-cleanup`.

C'est le **symétrique de `cleanup-merged`** (#23, §9.5), qui purge les branches locales mergées au
démarrage d'un ticket — et qui, dans `ensure`, tourne **juste après** ce ramassage : `git branch -D`
refuse une branche empruntée par un worktree. Même principe, même garde-fou : la fin du travail est
**confirmée par GitLab**, jamais déduite du nom de la branche.

**Ce qui déclenche le retrait** (`lib.sh worktree-done <iid> <branche>`, une lecture dans le cas
nominal) : la **PR de la branche est mergée**, ou le **ticket est fermé** (réalisé, abandonné,
doublon). Tout le reste est conservé — y compris un verdict **inconnu** (`gh` absent, hors ligne,
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
2. sinon `origin/<branche>`, s'il existe encore (branche poussée, PR pas encore mergée, ou case de
   suppression décochée) ;
3. sinon le **sha de merge** rendu par `worktree-done` — la tête de la branche source au moment du
   merge, seule trace locale de ce qui est parti ;
4. sinon `origin/main`, cas de la branche **jamais poussée** (ticket fermé sans PR), où ses commits
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

`gc` ne supprime **aucune branche**. Le retrait passe par la même séquence que `remove` — délier,
puis retirer (le garde-fou de #152 ci-dessus, écrit une seule fois dans le script).
`MAESTRO_WORKTREE_GC=0` désactive le passage automatique ; les tests, eux, imposent le verdict par
`MAESTRO_WORKTREE_VERDICT` et tournent donc sans réseau ni `gh`
([`test_worktree.py`](../tests/test_worktree.py)).

#### Les coquilles que le retrait laisse derrière lui (#422)

`git worktree remove` supprime le **contenu**, échoue sur le **dossier** lui-même quand un processus
le tient (« Permission denied » sous Windows) — et va au bout de son **désenregistrement quand
même**. Le worktree quitte `git worktree list`, sa branche redevient supprimable (`cleanup-merged`
l'emporte dans la foulée), et il reste un dossier **vide** que plus rien ne revendique.

Observé en direct le 2026-08-21 sur #415, au démarrage même de ce ticket, après **dix** autres
accumulées sans que rien ne les ait jamais nommées : `maestro-worktrees/` portait **12 dossiers pour
2 worktrees réels**. Ni `git worktree list`, ni `worktree.sh list`, ni `gc` ne pouvaient les voir —
les trois itèrent sur la même liste, celle dont ces dossiers sont justement sortis.

Et ce qui tient le dossier n'a rien d'exotique : trois `bash.exe` vivaient encore dans celui de
#415, c'est-à-dire **la session qui y travaillait, toujours ouverte**. C'est la situation nominale —
le ramassage a lieu dans une session **voisine** (`/ticket-start`, `/branch-cleanup`, démarrage d'un
run), au moment où la PR vient d'être mergée, donc souvent avant que la session d'origine soit
fermée. L'accumulation était systématique, pas accidentelle.

Et ce n'est pas qu'une affaire de propreté : `create` refusait **tout** dossier déjà présent qui
n'est pas un worktree, donc une coquille **bloquait le remontage de son ticket** — `ensure` rendait
`1` et `/ticket-start` s'arrêtait là, ce qui vaut en run autonome un échec de plus et la cascade des
lots suivants du parent (§11.10). Un défaut latent qui attendait qu'un ticket soit repris.

Trois réponses, du plus profond au plus superficiel :

1. **Le retrait rattrape.** Après un `git worktree remove` en échec, si le `.git` du worktree a
   disparu, ce n'est plus un retrait à retenter — git ne le connaît plus : le dossier vide est
   retiré (`rmdir`), suivi d'un `git worktree prune` pour le cas symétrique (dossier parti, entrée
   d'administration restée, qui ferait refuser le remontage de la même branche).
2. **Une coquille ne bloque plus.** `create` traite un dossier **vide** comme un emplacement libre.
   Un dossier qui **porte quelque chose** reste refusé — c'est le garde-fou d'origine, il ne bouge
   pas.
3. **Ce qui reste est visible.** `list` nomme les dossiers qu'aucun worktree ne revendique, et `gc`
   **écarte les vides** — y compris quand il ne reste plus un seul worktree enregistré, cas où elles
   sont le plus probables. Un dossier inconnu **non vide** est nommé, jamais touché : vide, c'est un
   déchet ; porteur, c'est le travail de quelqu'un.

Deux choix à ne pas défaire. Le repère est le **`.git` que git pose à la racine de tout worktree
lié**, jamais une comparaison avec les chemins de `git worktree list` : sous Windows git répond
« E:/… » là où le shell manipule « /e/… », et un `MAESTRO_WORKTREE_DIR` à contre-obliques ferait
passer des worktrees **vivants** pour des coquilles (même piège que dans `remove` et `gc`, §9). Et
le message d'échec **distingue les deux échecs** — « désenregistré, son dossier résiste » plutôt que
« non retiré » : dire l'inverse de ce qui vient de se passer est précisément ce qui a laissé onze
coquilles s'accumuler derrière autant de lignes rouges.

#### Le cycle de vie posé sur le même verdict (#275)

Le merge **ferme** le ticket (`Closes #<iid>`) mais ne touche à **aucun label**. Depuis #207, seul
`/branch-cleanup` — un geste manuel — posait « Terminé » : entre le merge et cette commande, un
ticket livré s'affichait « En revue » sur le board et dans `/backlog`, indéfiniment si personne ne
la lançait. `doctor.sh` **diagnostiquait** déjà la dérive (« ticket fermé mais son état est encore
actif ») sans jamais la réparer — 22 tickets concernés au moment d'écrire ces lignes.

La réparation se greffe **ici**, et pas ailleurs, pour une raison simple : `fini` — PR mergée ou
ticket fermé — est **exactement** la question que pose la réconciliation, et `gc` en a déjà la
réponse en main. Aucune lecture de découverte en plus, aucune étape ajoutée à `ensure` (qui en porte
déjà quatre : #181, #197, #205, #216), et les **trois points de passage du tableau ci-dessus** en
héritent d'un coup.

```bash
bash scripts/gitlab/lib.sh reconcile-workflow           # balaie tout le backlog fermé
bash scripts/gitlab/lib.sh reconcile-workflow --check   # dit ce qu'il poserait, sans écrire
bash scripts/gitlab/lib.sh reconcile-workflow 152 153   # cible — ce qu'appelle `gc`
```

**La règle, et son seul piège** : on ne pose que sur un cycle de vie **actif** (« À faire » / « En
cours » / « En revue ») ou **absent**. Un ticket « Abandonné » ou « Doublon » n'est **jamais**
écrasé — il est fermé lui aussi, donc `worktree-done` rend « fini » pour lui exactement comme pour
un ticket livré. Sans ce filtre, ramasser le worktree d'un ticket abandonné le déclarerait
« Terminé » : une dérive réparée en en créant une autre, sans retour possible puisque rien dans le
ticket ne dirait qu'il a été abandonné.

Trois choix à ne pas défaire :

- la pose a lieu **avant** le garde-fou du travail non sauvegardé et **indépendamment du retrait** —
  le cycle de vie suit le verdict de la forge, pas la propreté d'un répertoire local ni le succès d'un
  `rm`. Les lier ferait qu'un fichier oublié dans un worktree laisserait son ticket « En revue »
  pour toujours ;
- elle est **best-effort et muette en cas d'échec** (`gh` absent, hors ligne), au même titre que
  `sync-main` (§9.3) : elle n'empêche jamais un ticket de démarrer ni un run de continuer ;
- elle passe par `set-workflow`, donc **les cinq autres labels partent dans le même appel** — une
  pose qui écrirait son propre `addLabelIds` laisserait le ticket à deux états (§3.1).

`--check` n'écrit rien, et `MAESTRO_WORKFLOW_POSE=0` éteint la pose (toute autre valeur remplace
l'appel — c'est la couture par laquelle les tests l'observent sans réseau, comme
`MAESTRO_WORKTREE_VERDICT`). Ce défaut est **éteint dans les tests**, et c'est un garde-fou : sans
lui, un test qui rallume `gc` appellerait le vrai réconciliateur avec des iid de fixture et poserait
« Terminé » sur les vrais tickets du projet.

**Limite levée depuis #377** — elle a tenu de #275 au 2026-08-20, et il faut la connaître pour
comprendre ce que la sous-section suivante ajoute : la couverture était celle des **worktrees vus
par cette machine**. Un ticket mergé depuis le clone de quelqu'un d'autre n'était pas corrigé ici,
et le board restait faux **au repos** — un ticket mergé vendredi soir s'affichait « En revue » lundi
matin si personne n'avait démarré de ticket entre-temps. Le balayage sans argument rattrapait à la
demande, et `doctor.sh` le nommait quand il détectait la dérive.

Ce paragraphe écartait aussi la voie qui l'aurait levée — « pas un job post-merge, qui obligerait à
rouvrir un pipeline sur `main` (§8) » —, et l'objection était **propre à GitLab**, dont la CI ne se
déclenchait que sur les MR. Elle est tombée avec la forge.

#### La pose à l'événement — un workflow GitHub Actions sur `issues: closed` (#377)

**Ce qui déclenche est désormais la fermeture du ticket elle-même**, quels que soient l'auteur du
merge et la machine d'où il vient — y compris un merge fait depuis l'interface web, que rien ne
voyait. Constat du 2026-08-19, celui qui a ouvert le ticket : trois tickets mergés la veille
(#360, #361, #362) affichaient encore « En revue », aucun `/ticket-start` n'étant passé depuis.

| Fichier | Rôle |
|---|---|
| [`.github/workflows/cycle-de-vie.yml`](../.github/workflows/cycle-de-vie.yml) | déclenche et transmet — `on: issues: [closed]`, un checkout, un appel |
| [`scripts/github/ticket-ferme.sh`](../scripts/github/ticket-ferme.sh) | **décide** : filtre, délègue, ou s'abstient en le disant |
| `lib.sh reconcile-workflow <iid>` | **pose**, inchangé — le verbe de #275 ci-dessus |

La décision est dans le **script** et non dans un `if:` du YAML, pour une raison qui n'est pas de
style : un `if:` ne se rejoue ni en local ni dans la suite pytest, si bien que le filtre y serait un
second exemplaire qu'aucun test ne joue. Le script, lui, tourne sur le dépôt jetable de
[`tests/test_cycle_de_vie.py`](../tests/test_cycle_de_vie.py), `gh` factice compris.

`on: issues:` est un workflow **événementiel** et non un pipeline de push : il ne rouvre rien sur
`main`, ne relance aucun test, et ne coûte que les quelques secondes d'un checkout. Seul `closed`
est écouté — `reopened` n'est pas son symétrique et n'a rien à faire là : rendre son état à un
ticket rouvert demanderait de savoir lequel il portait avant, et « À faire » serait une valeur
inventée.

**Deux barrières devant « Abandonné »/« Doublon », et chacune arrête ce que l'autre ne voit pas.**
Écraser l'état d'un ticket abandonné est la dérive « sans retour possible » nommée plus haut :

1. **la raison de fermeture**, dans le script — liste **blanche** sur `completed`, et non exclusion
   de `not_planned` : GitHub a ajouté `duplicate` à l'énumération sans rien demander, et une liste
   noire aurait laissé passer chaque valeur suivante ;
2. **l'état courant**, dans `reconcile-workflow` — qui saute « Abandonné », « Doublon » et
   « Terminé ».

> ⚠ **Ce bloc n'a été une défense en profondeur qu'à partir de #388** — avant lui, une seule couche
> était active devant un abandon, et ce n'était pas celle qu'on lisait en premier.
> `/ticket-abandon` fermait par un `gh issue close <iid>` **nu**, donc GitHub y mettait
> `state_reason: completed` comme sur n'importe quel merge : la barrière n°1 laissait **entrer**
> tout abandon, et seule la n°2 l'arrêtait. Depuis #388, l'étape 7 ferme par
> `gh issue close <iid> --reason "not planned"` — les **deux** variantes, doublon compris —, si bien
> que la n°1 s'abstient **avant même de lire l'état**, et que l'issue cesse au passage de s'afficher
> « Completed » sur GitHub.
>
> **Chacune est désormais le filet de l'autre, sur un geste que l'autre ne couvre pas** :
> la **n°1** attrape le ticket fermé « as not planned » depuis l'interface web sans qu'aucun état
> ait été posé ; la **n°2** attrape son symétrique — un ticket déjà « Abandonné » refermé « as
> completed » à la main —, et elle tient parce que la commande pose l'état (étape 6) **avant** de
> fermer (étape 7), si bien qu'il est déjà là quand le script lit. Le corollaire vaut d'être
> retenu : ce n'est **pas** l'ordre de la commande qui protège l'abandon, c'est sa raison de
> fermeture ; l'ordre ne sert plus qu'au cas manuel.

**Le coût, nommé : un secret de dépôt.** `GITHUB_TOKEN` ne peut **pas** écrire dans un Projects v2
appartenant à un compte utilisateur — le blocage est le **type** de jeton et non une permission
(#359, §3.5). Le workflow lit donc `secrets.MAESTRO_PROJECT_TOKEN`, un jeton **classique** ou OAuth
à portée `project`, et c'est le seul geste manuel du dispositif :

```bash
gh secret set MAESTRO_PROJECT_TOKEN --repo <owner>/<dépôt>   # colle le jeton, il n'est plus relisible
```

**Best-effort, et ce que ça veut dire ici** — trois comportements, trois raisons :

| Situation | Ce qui se passe | Pourquoi |
|---|---|---|
| secret **absent** | abstention **annoncée**, run vert | c'est l'état du dépôt tant que personne n'a posé le secret ; un run rouge par ticket fermé ne dirait rien de plus que le premier |
| fermeture sans livraison | abstention annoncée, **aucune lecture** | le journal reste lisible s'il ne parle que de ce qui le concerne |
| pose **en échec** | code 1, run **rouge** | « best-effort » ne veut pas dire « vert quoi qu'il arrive » : le run rouge est la seule visibilité possible, et rien n'en dépend — ce workflow ne conditionne aucun merge et n'entre dans aucune protection de branche |

**Le workflow natif de Projects v2 est disponible et disqualifié** — le projet en porte six, dont
`Item closed`, tous désactivés. Deux faits l'écartent, mesurés le 2026-08-19 : il **écraserait**
« Abandonné »/« Doublon » par construction (`/ticket-abandon` pose l'état *puis* ferme, donc
l'automatisation passerait après notre écriture ; le natif n'offre aucune condition sur le
`state_reason` ni sur la valeur déjà présente), et il n'est **ni provisionnable ni vérifiable** —
la seule mutation exposée par l'API GraphQL est `deleteProjectV2Workflow`, donc rien que
`bootstrap-project.sh` puisse poser, et le type `ProjectV2Workflow` expose `enabled` mais **pas la
valeur cible** : `doctor.sh` pourrait dire « c'est allumé », jamais « ça pointe sur Terminé ».

**`worktree.sh gc` garde son rôle**, désormais comme **filet** et non comme seul mécanisme : il
rattrape ce qu'un secret absent, un jeton périmé ou un incident GitHub aurait laissé passer, et il
reste le seul à poser l'état d'un ticket **fermé sans que le workflow ait pu tourner**. Le balayage
`reconcile-workflow` sans argument, lui, ne sert plus qu'au rattrapage d'un arriéré.

#### Limites assumées d'un worktree partagé

Ce bloc ne se rattache à aucune des deux sous-sections ci-dessus : ce sont les limites de **§9**,
c'est-à-dire de faire tourner deux tickets sur un même dépôt. Le titre est là parce que deux
sous-sections se sont intercalées entre lui et le corps du §9 (#275, puis #377), au point qu'on
pouvait le lire comme la suite de la dernière.

- Les pipelines des deux PR tournent **en parallèle** : ils se **sérialisaient** du temps du
  runner unique, parti avec la CI GitLab (§8.1, #344) — plus lent, jamais bloquant.
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

### 9.3 `main` remis à jour d'office après chaque merge (#205)

Troisième automatisme de la même famille : `cleanup-merged` purge les **branches** mergées (#23,
§9.5), `gc` ramasse les **worktrees** soldés (§9.2), `sync-main` remet la branche **`main` locale**
à niveau. Le premier des trois s'est d'ailleurs cassé de la façon décrite ici, et pour la même
raison — voir §9.5.

Le retard est une conséquence directe de §9.1. Avant #181, `/ticket-start` passait par
`git checkout main && git pull origin main` dans le clone principal — la mise à jour était un effet
de bord du démarrage. Depuis que la session se relocalise dans un worktree, c'est l'autre branche de
`gl_start_branch` qui gagne (celle qui part directement d'`origin/main`), et **plus rien ne fait
avancer `refs/heads/main`** hors d'un `/branch-cleanup` explicite. Constaté à l'ouverture de #205 :
6 commits de retard sur le clone principal.

**Deux références, une seule en cause** — la distinction décide de la gravité :

| Référence | État | Qui la rafraîchit |
|---|---|---|
| `origin/main` (remote-tracking) | à jour | le `fetch` de `gl_start_branch`, `gl_cleanup_merged`, `worktree.sh` |
| `refs/heads/main` (branche locale) | **en retard** | plus personne depuis #181 |

Chaque worktree de ticket part d'`origin/main` (`git worktree add -b <branche> origin/main`) : **le
code produit n'a jamais été en cause**. Ce qui était périmé, c'est ce qu'on *lit* sur le clone
principal — l'IDE, `git log`, un diff local.

```bash
bash scripts/gitlab/lib.sh sync-main            # avance main, en fast-forward seulement
bash scripts/gitlab/lib.sh sync-main --check    # dit ce qu'il ferait, sans rien écrire
```

**Il n'y a aucun événement local à écouter** : le merge a lieu sur GitLab, et aucun hook git ne se
déclenche à ce moment-là (`post-merge` ne réagit qu'à un merge ou un pull *local*). D'où un câblage
aux **points de passage obligés** plutôt qu'un déclencheur qui n'existe pas :

| Où | Quand |
|---|---|
| `worktree.sh ensure` | à chaque `/ticket-start`, manuel comme autonome — donc à chaque ticket d'un run `/orchestrate` |
| `/branch-cleanup` | après un merge, à la place du `git checkout main && git pull origin main` d'avant |
| `orchestrate/run.sh` | au **démarrage** d'un run, avant son premier ticket (#283, §11.3) |

La troisième ligne n'est pas un doublon de la première. Un run est ce qui fait vieillir `main` le
plus vite — il ouvre N PR destinées à être mergées —, et la mise à jour par `ensure` a lieu *dans*
une session : elle ne joue donc pas du tout quand le run part sur un plan vide, saute tous ses
tickets ou échoue avant le premier. Sur une nuit où le run est la seule chose qui tourne, c'est
précisément le cas où personne ne repassera derrière. Elle a lieu **avant** le ramassage des
worktrees (§9.2), qui mesure le travail non sauvegardé contre `origin/main` : c'est le `fetch` de
`sync-main` qui rend cette mesure juste. Best-effort au même titre que les deux ménages qui la
suivent — `MAESTRO_SYNC_MAIN=0` l'éteint, un `--dry-run` ne la joue pas.

**Deux façons d'avancer la ref**, selon que `main` est empruntée ou non par un répertoire de
travail. Si personne ne l'a en HEAD, la ref se pose seule (`update-ref`) — aucun fichier touché, ce
qui rend l'appel valide depuis un worktree, là où un `git checkout main` échouerait. Si un
répertoire la porte (le cas normal : le clone principal), il faut un `merge --ff-only` **dans ce
répertoire**, sans quoi son index resterait sur l'ancien arbre et tout le delta apparaîtrait en
« supprimé/modifié ».

**Il s'abstient plutôt que de forcer**, comme `behind-main` (§6) et `gc` (§9.2) : ça *dit*, ça ne
casse pas. Jamais de `reset --hard`, jamais de non-fast-forward.

| Code | Situation | Ce qu'il fait |
|---|---|---|
| `0` | à jour, ou mise à jour faite | muet quand il n'y a rien à faire |
| `3` | `main` local **divergent** | s'abstient — un commit non poussé, l'écraser serait une perte |
| `4` | répertoire porteur de `main` **sale** | s'abstient et nomme le répertoire |
| `1` | hors dépôt git, `origin/main` absent | s'abstient |

Un code non nul n'est **pas fatal** pour l'appelant : une abstention n'empêche jamais un ticket de
démarrer ni un run de continuer. Couvert par [`test_worktree.py`](../tests/test_worktree.py) — mise
à jour depuis un worktree, ref posée sans répertoire de travail, les deux abstentions, idempotence,
et le fait qu'`ensure` démarre le ticket même quand `main` ne peut pas suivre.

### 9.4 Les dépendances ajoutées au dépôt suivent d'elles-mêmes (#216)

Quatrième automatisme de la même famille, et la même leçon appliquée à autre chose que des refs :
un **paquet ajouté au dépôt** n'arrive pas tout seul dans un clone déjà monté. Une entrée de
`pyproject.toml`, un paquet de `apps/web/package-lock.json`, une version de `.node-version` : la CI
les prend à chaque pipeline (`pip install -e ".[dev]"` en `before_script`), un clone neuf à son
`/setup` — un clone existant, **jamais**, tant que personne n'y rejoue `setup.sh` de sa propre
initiative.

Le cas qui a ouvert le ticket est bénin et sert d'avertissement : #214 ajoute `pytest-xdist` à
l'extra `dev`, et un clone d'avant garde une boucle de test en série — `local.sh` le dit, donc on
le voit. Le prochain ne sera pas dit : un module applicatif manquant fait échouer la suite locale
**à l'import**, sans que la cause saute aux yeux.

**Rien n'était à écrire, seulement à câbler.** `setup.sh` sait détecter la dérive *et* la réparer
depuis toujours — c'est ainsi que ses étapes décident si elles ont quelque chose à faire :

| Étape | Ce qu'elle compare | Ce qu'elle rejoue |
|---|---|---|
| `venv` | `pyproject.toml` contre le témoin `.venv/.maestro-setup-stamp` | `pip install -e ".[dev]"` |
| `web`  | `apps/web/package-lock.json` contre `apps/web/node_modules` | `npm ci` |
| `node` | `.node-version` contre `node -v` du Node vendoré | provisionnement de `.tools/node/` |

Ce qui manquait, c'était de l'**exposer** — d'où un mode dédié, lisible par un script, sans réseau
ni écriture :

```bash
bash scripts/setup.sh --derive      # « <étape><TAB><raison> » par ligne ; 0 = à jour, 3 = dérive
```

Les prédicats sont partagés avec les étapes elles-mêmes : **ce que le déclencheur détecte est
exactement ce que la réparation traite**, et les deux ne peuvent pas diverger.

**Aucun hook git ne pouvait porter ça** — même raison qu'en §9.3, et c'est ce qui rend le câblage
non négociable : le merge a lieu sur GitLab, et la mise à jour de `main` passe tantôt par
`git merge --ff-only`, tantôt par `git update-ref` (§9.3), qui ne déclenche **rien**. Un
`post-merge`/`post-checkout` ne se serait donc déclenché qu'une fois sur deux. D'où, là encore, les
points de passage obligés :

| Où | Ce qu'il fait |
|---|---|
| `worktree.sh ensure` — donc **tout `/ticket-start`**, manuel comme autonome | détecte (`--derive`), annonce, **répare** (`setup.sh --only <étapes>`) |
| `scripts/ci/local.sh` | détecte et **signale seulement** — bloc « Dépendances en retard » avant le verdict (§8.4) |

Trois règles, dans l'ordre d'importance :

1. **Rien n'est réimplémenté.** Ni `pip` ni `npm` n'est appelé hors de `setup.sh` : `worktree.sh`
   demande la dérive et déclenche la réparation, il ne l'écrit pas.
2. **C'est le clone principal qu'on remet à niveau.** `.venv/` et `.tools/` y vivent, partagés par
   lien avec tous les worktrees (§9) — et l'installation éditable de `maestro` doit continuer d'y
   pointer (#194). Le `node_modules` d'un worktree, lui, est installé à sa création. Corollaire
   pour `--derive` : dans un worktree, la dérive du **venv** s'évalue sur le clone principal. Le
   `pyproject.toml` d'ici date de la création du worktree, comme tous ses fichiers ; le comparer au
   témoin partagé crierait à la dérive à perpétuité, à chaque démarrage de ticket.
3. **Ça ne bloque jamais un démarrage.** Même statut que `sync-main` : une mise à niveau en échec
   est signalée, avec la commande de rattrapage, et le ticket part quand même.

`MAESTRO_MAJ_DEPENDANCES=0` désactive le passage automatique. Couvert par
[`test_setup.py`](../tests/test_setup.py) (dérive détectée / absente / réparée, et le fait que
`--derive` n'écrit rien) et [`test_worktree.py`](../tests/test_worktree.py) (le câblage : qui
appelle quoi, depuis un worktree comme depuis le clone principal, et l'échec non bloquant) —
dépôts jetables, sans réseau ni vrai `pip`/`npm`.

### 9.5 Les branches mergées repurgées d'office (#305)

Le cinquième membre de la famille — et le seul qui existait **avant** les autres. `cleanup-merged`
purge les branches locales mergées depuis #23 ; ce qui a cassé, c'est son **déclencheur**.

Il vivait dans `lib.sh start-branch`, sur la voie « clone principal + branche à créer ». Depuis
#181, `/ticket-start` appelle `worktree.sh ensure` **avant** `start-branch` et relocalise la
session : l'appel arrive donc toujours depuis le worktree du ticket, où `start-branch` sort par
« déjà sur la branche » ou par sa voie worktree — **jamais** par celle qui purgeait. Le clone
principal, lui, ne change plus jamais de branche. Constat du 2026-08-07 : **35 branches locales
mergées** accumulées, la plus ancienne remontant à #220.

C'est la panne de §9.3 à l'identique, sur un autre objet, et pour la même raison : **le point de
passage a bougé et l'automatisme est resté**. La différence tient à ce qu'on en voit — un `main` en
retard se lit dans l'IDE, une branche morte de plus ne se remarque pas.

| | Avant #305 | Après |
|---|---|---|
| Déclencheur automatique | `lib.sh start-branch` (injoignable depuis #181) | `worktree.sh ensure`, comme §9.2/§9.3/§9.4 — **plus le merge d'un run** depuis #438 |
| À la demande | `/branch-cleanup` | `/branch-cleanup` (inchangé) |

Depuis #438, `cleanup-merged` accepte des **branches nommées** — `cleanup-merged --auto <branche>` —
et n'examine alors que celles-là. Pendant du `--iid` de §9.2, pour la même raison et avec le même
principe : nommer restreint, sans rien relâcher. `mr-state` reste interrogé pour chaque branche
visée — ce n'est pas parce que l'appelant vient de merger qu'on cesse de demander à la forge (§6).

L'ordre dans `ensure` **n'est pas cosmétique** : la purge passe **après** le ramassage des
worktrees (§9.2), parce que `git branch -D` refuse une branche empruntée par un worktree. La
branche d'un ticket soldé n'est donc supprimable qu'une fois son worktree parti — c'est le même
ordre que dans `/branch-cleanup`, et il est épinglé par un test.

**Un refus qui ne se voyait pas.** Quand `git branch -D` échoue, l'échec n'incrémentait **aucun**
des deux compteurs : la branche sortait du compte rendu sans un mot, et le bilan annonçait moins de
branches qu'il n'en avait examinées (3 sur 41 lors de la purge de rattrapage). Elle est désormais
comptée à part et **nommée**, avec le worktree qui la retient :

```
  ⚠ conservée : feat/251-… (PR merged, empruntée par le worktree E:/…/maestro-worktrees/251-…)
Nettoyage des branches : 32 supprimée(s), 6 conservée(s), 3 mergée(s) mais empruntée(s) par un worktree.
```

Deux autres choix à connaître avant d'y toucher :

1. **Un seul point d'appel automatique.** La purge a été **retirée** de `start-branch` plutôt que
   laissée en double. Un second déclencheur inatteignable est exactement ce qui a rendu la panne
   invisible : le code était là, la doc le décrivait, et plus rien ne l'exécutait.
2. **Le helper vise le clone principal**, d'où qu'on l'appelle — comme `sync-main` et `gc`. Les
   refs sont pourtant partagées par tous les worktrees, donc la liste des branches serait la même
   de partout ; ce qui change, c'est ce sur quoi portent ses garde-fous. L'arbre regardé est celui
   du clone principal, normalement propre et sur `main`, et non celui d'un worktree en plein
   travail — qui ferait sauter la purge en silence à chaque reprise de session.

```bash
bash scripts/gitlab/lib.sh cleanup-merged           # purge et rend son bilan
bash scripts/gitlab/lib.sh cleanup-merged --auto    # muet s'il n'y a ni suppression ni refus
```

Le mode `--auto` est celui que câble `ensure`, au même titre que `gc --auto` : sans lui, chaque
`/ticket-start` s'ouvrirait sur un inventaire dont personne n'a besoin. Le coût est d'une lecture
`gh` par branche locale — l'ordre de grandeur du ramassage juste avant, qui en fait une par
worktree, et c'est justement parce que la purge tourne à nouveau que ce nombre reste petit.

`MAESTRO_PURGE_BRANCHES=0` désactive le passage automatique. Couvert par
[`test_worktree.py`](../tests/test_worktree.py) (le câblage, l'ordre vis-à-vis du ramassage, le
compte rendu d'une branche retenue, l'abstention sur arbre sale et le fait que `start-branch` ne
purge plus) — dépôt jetable, sans réseau ni `gh`.

#### `/branch-cleanup` appelle ce même helper (#309)

Jusque-là la commande **réimplémentait la boucle en prose** — un
`gh pr view <branche> --json …` par branche locale, dont la charge utile réinjectée pèse
~3 500 octets pour en tirer **un mot** (`merged`/`opened`/`closed`) : ~43 000 tokens par invocation
sur ce dépôt, soit 25 fois le texte de la commande elle-même (audit
[#304](25-audit-commandes-claude.md) §4.1, recommandation M1 — le plus gros gisement du lot). Elle
s'appuie désormais sur `cleanup-merged`, et ne garde autour de lui que les **trois fonctions qu'il
ne couvre pas** :

| Fonction | Pourquoi le helper ne la fait pas |
|---|---|
| Basculer sur `main` | il ne change **jamais** de branche : il **saute** celle du clone principal au lieu de la supprimer sous ses propres pieds. La commande demande donc son état à `mr-state` — un mot, facteur 500 sur le JSON — et bascule si elle est mergée (jamais depuis un worktree, §9.1) |
| Supprimer la branche **distante** | il n'écrit rien côté serveur. Le cas ne se présente que si la case « Delete source branch » a été décochée au merge (§6) ; les branches restantes se lisent d'un coup dans les refs de suivi, que le helper vient de rafraîchir |
| Poser « Terminé » | il n'écrit rien côté GitLab non plus. La commande le fait par `reconcile-workflow`, qui saute les tickets déjà finaux et n'écrase jamais « Abandonné »/« Doublon » (§9.2) |

Le gain n'est pas que du contexte : le garde-fou « suppression **seulement** si la forge confirme
`merged` » n'a plus qu'**une** implémentation. Deux, dont une en prose, c'est une divergence en
attente — le jour où la règle change, le prompt ne suit pas. Le raccordement est épinglé par
[`test_collaboration.py`](../tests/test_collaboration.py), qui relit le prompt (délégation présente,
plus aucune lecture de PR prescrite, trois fonctions toujours nommées) — même parti pris que les tests
#196 et #233 : une règle qui vit dans un prompt ne se garde que par une lecture de ce prompt.

### 9.6 Un ticket abandonné par sa session redevient prenable (#327)

Le sixième membre de la famille, et celui qui réconcilie non pas un **répertoire** mais un
**ticket**. Les cinq précédents remettent à niveau ce qu'une machine a laissé traîner ; celui-ci
ramène dans le champ de vision un ticket que plus rien ne pouvait y ramener.

**Le mécanisme de la perte.** Un ticket entre en « En cours » — et s'assigne — à `/ticket-start`.
Il n'en sort que par `/ticket-ship`/`/ticket-finish` (« En revue ») ou `/ticket-abandon`. La
**troisième sortie est l'absence de sortie** : une session qui meurt y laisse son ticket pour
toujours. Or « En cours » **et assigné** est exactement le filtre d'anti-collision de `queue.sh`
(§11.2) — **la règle qui protège le travail vivant cache définitivement le travail mort**. Constat
du 2026-08-11 : **#316**, un commit de 2047 lignes jamais poussé, dont la chute avait en plus sauté
les 7 lots suivants de son parent ; **#325**, 396 lignes non commitées dans son worktree.

**Le renversement.** Ne pas demander « ce run a-t-il échoué ? » mais **« quelqu'un s'occupe-t-il
encore de ce ticket ? »**. Toute la valeur du dispositif tient là : la première question n'a de
réponse que pour les tickets morts *dans un run*, et laisse passer les deux modes de mort les plus
fréquents. La seconde se pose au **worktree**, qui existe dans les trois cas :

| Mode de mort | Ce qu'il laisse dans le journal | Ce que `--resume` en fait |
|---|---|---|
| Run soldé en échec (#316) | une ligne de bilan `✗ ECHEC` | **rien** : `reprend_en_vol` exige l'absence de bilan, et le run est « terminé » donc pas même reprenable |
| Pilote tué (`taskkill //F`) | un témoin de session, **aucun verdict** (aucun trap ne s'exécute) | le rattrape — mais seulement si l'on reprend **ce run-là** |
| Session interactive laissée en plan (#325) | **rien du tout** | rien : ni journal ni témoin |

**Deux verbes, et l'asymétrie entre eux est le sujet.**

```bash
bash scripts/gitlab/lib.sh reconcile-en-cours              # le détail, verdict par verdict
bash scripts/gitlab/lib.sh reprendre-en-cours <iid>…       # le geste : « À faire » + libéré
bash scripts/gitlab/lib.sh reprises [<iid>]                # la trace, avant d'insister
```

`reconcile-en-cours` **signale** — lecture seule intégrale, aucun label, aucune assignation, aucun
worktree touché. Il rend trois verdicts :

| Verdict | Source | Quand |
|---|---|---|
| **vivant** | la **carte du pilote** (#213) quand elle est là, sinon la fraîcheur du worktree | un processus vérifiable tient le ticket, ou le worktree a été écrit il y a moins de 6 h |
| **orphelin** | déduction, **annoncée comme telle** | worktree présent ici, muet depuis plus de 6 h, aucun pilote |
| **hors de portée** | — | aucun worktree sur cette machine : ne rien savoir n'autorise rien |

Le seuil de 6 h (`MAESTRO_ORPHELIN_SEUIL`) est **généreux à dessein**, et c'est ce qu'il protège
qui le fixe : une session qui épuise la limite d'usage dort jusqu'à son reset sans rien écrire, et
`run.sh` l'attend jusqu'à 5 h 30 (§11.4). Plus court, il déclarerait abandonné un ticket dont la
session attend légitimement. **La carte du pilote ne prouve jamais que la vie** : elle survit à un
`taskkill //F`, donc une carte dont le processus est mort ne sauve pas le ticket — c'est la
déduction qui reprend la main.

`reprendre-en-cours` **rend prenable**, et « prenable » est une **conjonction**, parce que le filtre
de `queue.sh` en est une : « À faire » **ET** libre. D'où une **seule mutation** qui pose le cycle
de vie et vide la liste des assignés — deux appels laisseraient un intervalle où le ticket est dans
un état que personne n'a voulu. Le verdict n'est jamais redéduit ici : il est **demandé** au verbe
ci-dessus, seul à savoir départager. « vivant » et « hors de portée » ferment la porte ; `--force`
la rouvre, **jamais en silence**, pour qui sait quelque chose que la machine ignore.

**Ce que la reprise ne touche pas — et c'est tout son intérêt.** Elle n'écrit **que** dans GitLab.
Worktree, branche, commits non poussés, fichiers non commités : intacts, et `worktree.sh ensure`
les retrouve au démarrage suivant. C'est exactement ce qu'on veut des 2047 lignes de #316 — un
verbe qui « nettoierait » au passage détruirait ce qu'il est censé sauver.

**Le bornage.** Un ticket qui retombe à chaque run brûlerait une session entière à chaque fois.
La reprise est donc plafonnée à **2 par ticket** (`MAESTRO_REPRISES_MAX`), au-delà desquelles elle
se demande par `--force`. Le compteur vit dans `.maestro/orchestrate/reprises.tsv` — **à côté** des
répertoires de run et non dedans, parce qu'une reprise est une propriété du **ticket** (celle de
#325 n'a jamais eu de run du tout) et que le ménage du journal (§11) ne balaie que les
`<run-id>/`. Un plafond qu'un ménage remet à zéro tous les dix runs est un plafond qui n'existe pas.

**Où ça se voit**, sans avoir à taper quoi que ce soit :

| Point de passage | Ce qu'il montre |
|---|---|
| `worktree.sh gc`, donc `/ticket-start`, `/branch-cleanup` et le démarrage d'un run | le bloc « Tickets « En cours » dont plus personne ne s'occupe » — **muet quand il n'y a rien**, comme `gc --auto` et `cleanup-merged --auto` |
| `doctor.sh`, section 4d | la quatrième dérive du cycle de vie, et la seule qui demande de regarder un disque plutôt que GitLab |
| `queue.sh --orphelins` | ceux du **milestone visé**, avec leur run d'origine et leur plafond — lus **à côté** du plan, jamais dedans |
| `/orchestrate` | les propose au feu vert, un par un ; le « oui » explicite remplace le contournement du filtre |

Le signalement est greffé sur **`gc`** et non sur `ensure`, pour la raison exacte qui y a fait
greffer la pose du cycle de vie (§9.2) : les **trois** points de passage en héritent d'un coup, là
où un câblage par point de passage en ferait trois à garder d'accord — et un déclencheur qui cesse
d'être atteint ne se remarque pas (§9.5). Il est **consultatif de bout en bout** : rien ne se
reprend d'office. Ce n'est pas de la prudence de façade — « orphelin » est une **déduction**, et
reprendre le ticket d'une session vivante le lui retire (le run suivant l'assigne à quelqu'un
d'autre), là où rater un orphelin ne coûte qu'un tour de boucle.

**Portée**, comme `gc` et `reconcile-workflow` : les worktrees de **cette machine**. Un ticket
travaillé sur le clone de quelqu'un d'autre est « hors de portée », jamais orphelin. Le board reste
donc faux **au repos** — ce n'est pas une régression, mais ça se dit.

`MAESTRO_EN_COURS_SIGNAL=0` éteint le signalement (`MAESTRO_WORKTREE_GC=0` l'éteint par voie de
conséquence, `gc` en étant le porteur). Couvert par
[`test_collaboration.py`](../tests/test_collaboration.py) — les trois modes de mort joués l'un
après l'autre jusqu'à la reprise, l'empreinte du worktree identique avant/après, le plafond qui
survit au ménage du journal et ne se contourne pas depuis un worktree, la dérive `doctor.sh` —,
[`test_worktree.py`](../tests/test_worktree.py) (le câblage sur `gc`, son mutisme, `--sauf`) et
[`test_orchestrate.py`](../tests/test_orchestrate.py) (`queue.sh --orphelins`, `journal.sh
origine`). Dépôt jetable, sans réseau ni `gh`.

### 9.7 L'historique d'une session reste adressable (#385, #397)

Claude Code range le transcript d'une session dans un répertoire de projet **indexé sur le
répertoire courant** — `<config>/projects/<chemin encodé>/<session-id>.jsonl` — et son sélecteur
`/resume` ne montre **que** celui d'où on l'appelle. Or `/ticket-start` relocalise la session dans
le worktree du ticket (§9.1) : l'historique d'un ticket est donc rangé sous le chemin du
**worktree**, invisible depuis le clone principal. Puis `gc` retire le worktree (§9.2), et l'on ne
peut même plus y revenir en `cd`.

Constat du 2026-08-19 sur le clone de référence : **157 transcripts** (183 Mo) répartis dans **134
répertoires de projet**, pour **13 worktrees** encore sur le disque. Autrement dit, l'essentiel du
travail de ticket était devenu inatteignable — sans que rien, nulle part, ne le signale.

**Rien n'est perdu : c'est l'adressage qui manquait, et il se dérive.** L'encodage de Claude Code
remplace `:`, `\`, `/` et l'espace par `-`, sans rien tronquer ; le répertoire de projet d'un
ticket est donc `<base des worktrees encodée>-<iid>-<slug>`, qu'un motif sur le seul **iid**
retrouve — le slug n'est jamais nécessaire.

```bash
bash scripts/git/worktree.sh sessions        # les 10 dernières de CE dossier (#397)
bash scripts/git/worktree.sh sessions 340    # les sessions du ticket #340
bash scripts/git/worktree.sh sessions --tous # l'inventaire, tous tickets confondus
```

```
#340 — worktree ramassé, transcripts conservés
  2026-08-17 13:58  Import des 330 tickets sur GitHub
                    claude --resume fe63adac-7f25-479c-81b8-b256a9b1d813
```

La reprise passe par l'**identifiant** : `claude --resume <id>` court-circuite le sélecteur, donc
son cloisonnement par répertoire. C'est tout ce que le verbe a besoin de rendre.

Trois choix à ne pas défaire :

- **Dériver, jamais indexer.** Poser un index au moment du ramassage était le réflexe, et c'était
  le mauvais : il n'aurait couvert que les ramassages postérieurs à sa mise en place, laissant
  dehors les **121 worktrees déjà partis** — et il aurait ajouté un état de plus à tenir d'accord
  avec la réalité.
- **La base des worktrees vient de `create`**, jamais d'un `maestro-worktrees` figé dans le verbe :
  `MAESTRO_WORKTREE_DIR` déplace les worktrees, donc l'encodage, donc ce qu'il faut chercher. Une
  formule recopiée répondrait juste sur une machine et **vide** sur les autres, silence
  indiscernable de « ce ticket n'a pas de session ».
- **Le motif ignore la casse.** Claude Code encode le chemin **tel qu'il lui a été donné**, sans le
  normaliser : sur la machine de référence le clone principal est rangé sous `e--` et ses worktrees
  sous `E--`. Un motif sensible à la casse en manquerait la moitié, sans un mot.

`gc` **nomme les sessions avant de retirer** un worktree — c'est l'instant exact où l'information
quitte l'écran ; après coup, plus rien ne rappelle qu'il y avait un historique ni par quoi le
rouvrir. Le retrait ne les efface pas (un transcript vit sous `<config>/projects/`, jamais dans le
worktree), il coupe seulement le chemin qui les montrait :

```
✓ #340 retiré — PR #268 mergée — 2 session(s) conservée(s) : worktree.sh sessions 340
```

**Portée**, comme `gc` et `reconcile-workflow` : les worktrees de **cette machine**. Un transcript
vit sur le poste qui l'a produit ; le verbe ne va rien chercher ailleurs, et l'annonce plutôt que
de laisser confondre « pas ici » avec « nulle part ».

#### Après un redémarrage de VS Code : le dossier courant (#397)

La dérivation ci-dessus ne couvre que les **worktrees**, et la question la plus fréquente ne se pose
pas par iid : elle se pose **là où l'on est**, en rouvrant VS Code — dont l'onglet Claude Code
revient vide et sans nom. Le clone principal, d'où l'on travaille le plus souvent, en était exclu :
191 transcripts, aucun moyen de nommer celui d'hier.

C'est pourquoi **`sessions` sans argument regarde désormais le répertoire courant** — l'inventaire de
#385 n'a pas disparu, il se demande (`--tous`). Le geste le plus court répond à la question la plus
fréquente ; celle de l'inventaire (« où sont passées mes sessions de tickets ? ») vient plus rarement
et plus tard.

```
Sessions Claude Code — E:\Projects Solutions\Maestro
  (ce dossier ; c'est tout ce que le sélecteur /resume y montre)

  2026-08-20 18:05  [maestro-80] Ordre d'exécution milestone 14
                    claude --resume 22bd3795-79d9-4273-a27f-74dc60e2d927

191 session(s) ici.
  181 plus anciennes non listées : worktree.sh sessions --limite 0
  137 ticket(s) en ont aussi, dans leur worktree : worktree.sh sessions --tous
```

Trois choix, là encore :

- **Le nom d'onglet vient du registre**, `<config>/sessions/<PID>.json` — la seule source qui le
  porte, aucun transcript ne le connaissant. C'est le repère par lequel on reconnaît sa session : un
  titre est posé en cours de route, parfois jamais, alors que le nom est celui qu'on avait sous les
  yeux. Une session **reprise** garde son identifiant sous un nouveau PID (jusqu'à trois fiches pour
  un même id), donc le nom retenu est celui de la fiche **la plus récente**.
- **La liste est bornée à 10, et le dit.** Tout rendre reperdait la conversation d'hier dans 390
  lignes ; en rendre 10 sans le dire ferait passer une troncature pour un inventaire, et conclure
  qu'une session n'existe plus. `--limite 0` rend tout, `MAESTRO_SESSIONS_LIMITE` déplace le défaut.
- **« Ce dossier » veut dire ce dossier.** Les sessions de worktrees sont **annoncées** en pied,
  jamais mêlées à la liste : les y mêler proposerait une reprise dans un répertoire qui n'est pas
  celui de la session.

#### Pourquoi l'onglet d'un ticket repart vide — et pas celui du clone principal (#424)

**La cause tient en une phrase : le transcript suit le répertoire courant, et un onglet ne cherche
que dans le sien.** `/ticket-start` relocalise la session dans le worktree (§9.1), donc son
transcript **quitte** le dossier de projet de l'espace de travail ; au redémarrage, l'onglet ne l'y
trouve plus et ouvre une conversation **neuve**, sans un mot.

```bash
claude --resume <id>   # y revenir : marche depuis n'importe quel dossier, worktree ramassé ou non
```

⚠ **Et #397 s'était trompé en concluant « hors de portée ».** L'onglet **sait** se rebrancher : ce
qu'il ne sait pas, c'est chercher ailleurs que dans son dossier. Les deux moitiés, mesurées le
2026-08-22 sur l'extension 2.1.238 :

- **La restauration fonctionne, et on l'a vue faire.** VS Code persiste chaque onglet Claude avec
  `{"isFullEditor":…,"sessionID":"<uuid>"}` dans `memento/workbench.parts.editor`, et
  `deserializeWebviewPanel` rebranche l'onglet sur cet identifiant. Preuve : le transcript de
  l'onglet « Ordre de traitement milestone 14 » porte comme dernier horodatage interne
  `2026-08-21T16:30`, et son fichier a été réécrit le **2026-08-22 à 19:54:56** — à la seconde du
  redémarrage, sans qu'un seul message nouveau y soit ajouté. La piste `sessionGroups:<hash>`
  explorée par #397 n'était donc pas la bonne : l'absence de cette clé ne prouvait rien.
- **Mais la recherche est bornée au dossier.** Le rebranchement n'aboutit que si l'identifiant se
  retrouve dans `listSessions({dir: <espace de travail>, includeWorktrees: false})` — worktrees
  **exclus** en toutes lettres. Sinon l'onglet appelle `createSession()`.

Et le transcript, lui, est bien parti. Mesuré **en direct sur la session qui a écrit ces lignes** :
démarrée dans le clone principal, relocalisée par `/ticket-start 424`, son fichier n'existe plus
qu'une fois — sous le dossier de projet du worktree — et porte dix lignes
`{"type":"relocated","relocatedCwd":"…"}`. 112 transcripts sont dans ce cas depuis #181.

⚠ **Le ramassage du worktree n'y est pour rien**, et c'était l'autre hypothèse : `gc` retire un
répertoire de travail, jamais un transcript (`<config>/projects/…` lui survit), et `claude --resume`
a été joué depuis un **autre worktree** sur une session du clone principal — il l'a retrouvée et
**replacée dans son cwd d'origine**. C'est pourquoi la commande de reprise ci-dessus n'a aucune
condition : ce qui manque à l'onglet ne manque pas au CLI.

Conséquence pratique : **un onglet ouvert sur le clone principal se rouvre sur sa conversation**
(c'est le cas courant, et il marche) ; **un onglet passé par `/ticket-start` ne le fait pas** — il
faut son identifiant. `ensure` le donne au moment du départ (ci-dessous) ; après coup, `worktree.sh
sessions <iid>` le retrouve. Nommer une session au lancement (`claude -n "<nom>"`) aide en
complément.

⚠ **Sans rapport avec VS Code, mais c'est de la vraie perte** : Claude Code supprime périodiquement
les transcripts de plus de **30 jours** (`<config>/.last-cleanup`) — au constat du 2026-08-22, le
plus ancien du clone principal datait du 2026-07-22, jour pour jour. `cleanupPeriodDays` déplace ce
seuil pour qui veut garder plus loin.

#### Le signalement tombe au départ, pas après coup (#424)

La perte a lieu à la **relocalisation**, et c'est le seul instant où quelqu'un a la question sous
les yeux — après, l'onglet vide ne rappelle rien. `ensure` le dit donc là, à côté des ports :

```
  ⚠ la conversation de cette session quitte le dossier courant : au prochain
    démarrage de VS Code, son onglet repartira vide (un onglet ne cherche que
    dans le dossier de son espace de travail, worktrees exclus — §9.7).
    y revenir : claude --resume 74b811f3-2707-4b14-b04c-115b3ec622a9
```

Deux choix à ne pas défaire :

- **L'identifiant vient de `CLAUDE_CODE_SESSION_ID`**, que Claude Code pose dans l'environnement de
  ses sous-processus — donc juste, jamais deviné. **Hors d'une session** (`orchestrate/run.sh`, un
  terminal nu) la variable est absente : le message renvoie alors vers `worktree.sh sessions <iid>`
  plutôt que d'inventer un identifiant, qui enverrait rejouer une reprise inexistante.
- **Rien n'est dit sur le verdict `ICI`** : la session y est déjà au bon endroit, rien ne quitte
  quoi que ce soit. Avertir quand même apprendrait à ne plus lire l'avertissement.

Le pied de `sessions` porte la même cause, pour qui arrive après coup : `137 ticket(s) en ont aussi,
dans leur worktree` est suivi de *(parties d'ici avec leur session — c'est pourquoi leur onglet
repart vide)*. Le compte seul se lisait comme un simple « il y en a ailleurs ».

⚠ **Et #385 avait conclu « pas de notre ressort » sur une prémisse fausse** : que le registre, étant
indexé par PID, serait « périmé dès que les processus meurent ». Indexé par PID, oui ; périmé, non —
il porte le `cwd`, le `sessionId` et le nom, et il **survit** (36 fiches sur le poste de référence, en
majorité de processus morts). Le PID ne périme qu'une question qu'on ne pose pas ici : « cette
session tourne-t-elle encore ? ».

Couvert par [`test_worktree.py`](../tests/test_worktree.py) : la dérivation avec et sans worktree
sur le disque, le repli d'un transcript sans titre, le dernier titre qui l'emporte, l'ordre
antichronologique, `MAESTRO_WORKTREE_DIR` respecté, la casse ignorée, et la mention portée par `gc`
— y compris son silence quand il n'y a aucune session à nommer ; puis, pour #397, le dossier courant
hors worktree, les sessions d'ailleurs qui n'y entrent pas, le nom tiré du registre et la fiche la
plus récente qui l'emporte, le repli sans registre, `CLAUDE_CONFIG_DIR` suivi par le registre comme
par les transcripts, la troncature annoncée et son échappatoire, et un identifiant rendu une seule
fois ; puis, pour #424, l'avertissement de départ avec son identifiant tiré de l'environnement, son
repli hors session, son silence sur le verdict `ICI`, et la cause portée par le pied de `sessions`.

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
| Une session clôture un ticket **qui n'est pas le sien** (PR et temps posés à la place d'un autre) | **garde-fou de clôture** : `close-guard` compare l'iid visé à la branche courante *et* aux assignés | §6 |
| Les lots d'un parent s'attendent en file alors qu'ils sont indépendants | marqueur **`(parallèle)`** dans la checklist ; `startables` liste **tous** les lots prenables | §5.1 |
| Une branche vieillit pendant qu'`origin/main` avance ; le conflit se découvre au merge | **alerte de retard** avant le push : `behind-main` (commits de retard + fichiers modifiés des deux côtés) | §6 |
| Une PR ouverte n'est relue par personne, faute de savoir qu'elle attend | **revue best-effort outillée** : **file de revue** en tête de `/backlog`, la plus ancienne d'abord (aucun relecteur posé d'office, #196 ; `set-reviewer` reste là pour une pose manuelle) | §6 |
| Une session meurt sur un ticket : il reste « En cours » et assigné, donc **invisible de tous** — travail compris | **détection + reprise** : `reconcile-en-cours` signale d'office, `reprendre-en-cours` le rend prenable sans toucher au worktree | §9.6 |
| La CI dépend du poste d'**une** personne : elle éteint sa machine, l'équipe ne merge plus | **runner partagé permanent** (`--partage`, machine toujours allumée), les runners locaux en secours | §8.1 |
| Un échec de lint occupe le runner de quelqu'un d'autre pour une faute de frappe | **filet CI local** : `bash scripts/ci/local.sh` rejoue les jobs du pipeline avant le push | §8 |
| La moitié du `.env` circule à la main, de canal en canal | marqueurs **`[perso]` / `[partagé]`** + `env-pull.sh`, qui complète sans jamais écraser | §7.3 |

**Rien n'est bloquant.** Aucun de ces mécanismes n'interdit quoi que ce soit : ils *disent*, et la
décision reste humaine. `behind-main` et `close-guard` rendent un **code de retour lu, jamais
fatal** (`… || verdict=$?`) ; la revue n'exige **aucune approbation** — c'est la visibilité qui la
déclenche ; et **aucun relecteur n'est désigné d'office** (#196), la pose restant un geste humain
outillé par `set-reviewer`. Les seuls refus durs restent ceux des garde-fous de
§6 : **aucun merge non vérifié**, pas de force-push, pas de suppression de branche non mergée. ⚠ Ce
premier refus disait « pas de merge automatique » jusqu'au chantier #413 : le merge **est** devenu
automatique, ce qui ne l'a pas rendu moins gardé — les quatre prérequis de `merge-mr` sont, eux,
bloquants au sens plein, seuls de tout ce tableau.

**Deux personnes, une machine chacune : le parcours.**

1. `bash scripts/setup.sh` puis `bash scripts/env-pull.sh` — le poste est équipé, les secrets
   partagés arrivent des variables CI/CD (§7.3).
2. `/backlog` → prendre un ticket de la section **Libres** (§5) ; s'il est marqué `(parallèle)`
   dans un parent, quelqu'un d'autre peut prendre le lot voisin en même temps (§5.1).
3. `/ticket-start <iid>` — s'arrête si le ticket est déjà pris ; sinon branche, statut, dates.
4. `bash scripts/ci/local.sh` avant de pousser (§8).
5. `/ticket-ship` — retard sur `origin/main` signalé, garde-fou de clôture, PR (sans relecteur
   désigné : c'est la file de revue qui appelle un relecteur, §6), **puis attente du pipeline et
   merge** par `lib.sh merge-mr` (§6). Compter quelques minutes : la commande ne rend plus la main
   dans la seconde.
6. Le plus souvent il n'y a **rien à faire ensuite** — la PR est mergée, le ticket fermé par son
   `Closes` et passé « Terminé » par le workflow `issues: closed` (§9.2). Si `merge-mr` a refusé, la
   PR reste **ouverte** et le ticket **« En revue »** : elle apparaît alors en tête de `/backlog`
   chez tout le monde, avec sa cause, et se débloque par `/mr-fix` (§8.3). La **revue**, elle, est
   devenue un geste d'après-merge (§6).

> **Tests.** Ces comportements sont couverts par [`tests/test_collaboration.py`](../tests/test_collaboration.py)
> (helpers `lib.sh` + contrôle runner de `doctor.sh`), [`tests/test_env_pull.py`](../tests/test_env_pull.py)
> et [`tests/test_ci_local.py`](../tests/test_ci_local.py) — même parti pris que
> [`test_setup.py`](../tests/test_setup.py) et [`test_worktree.py`](../tests/test_worktree.py) :
> dépôt jetable, **ni réseau ni compte de forge** (un `gh` factice répond depuis une
> fixture et journalise les appels, cf. [`tests/harnais_forge.py`](../tests/harnais_forge.py)),
> on teste la **décision** des scripts et non l'API.

## 11. Traitement autonome du backlog — la boucle d'orchestration

Traiter un ticket demande d'ordinaire une présence du début à la fin : ouvrir une session,
`/ticket-start`, laisser faire, `/ticket-ship`, recommencer. Quand le backlog contient une suite de
lots entièrement décrits, c'est du travail séquentiel qui n'attend qu'un pilote. La boucle
d'orchestration (`scripts/orchestrate/`, parent #167) le déroule **sans supervision** : un ticket =
**un worktree** = **une session Claude Code**, de `/ticket-start` à `/ticket-ship`, avec **reprise
automatique** quand la limite d'usage de 5 h tombe au milieu.

Depuis #419 il va jusqu'au bout : un run se solde **tout mergé** — PR fermées, `main` avancée,
conflits résolus au passage — au lieu de laisser N PR ouvertes à reprendre après coup. Le pilote
tient pour cela une **file de merge**, drainée au fil de l'eau puis en fin de run (§11.11).

```bash
bash scripts/orchestrate/queue.sh --check   # l'ordre de traitement, et ce qui a été écarté
bash scripts/orchestrate/queue.sh --milestones  # sur quel milestone lancer un run (§11.2)
bash scripts/orchestrate/run.sh --dry-run   # le plan et ce qui serait fait — rien n'est lancé
bash scripts/orchestrate/run.sh             # le run, dans un terminal laissé ouvert
bash scripts/orchestrate/run.sh --detach    # idem, dans une console indépendante — rend la main
bash scripts/orchestrate/run.sh --resume    # reprend un run qui ne s'est pas terminé (§11.8)
bash scripts/orchestrate/run.sh --concurrence 3  # jusqu'à 3 tickets INDÉPENDANTS en vol (§11.10)
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
object* Windows). Le filet est le plan sur disque — `--resume <run-id>` le rejoue, les tickets déjà
livrés étant sautés d'eux-mêmes et celui qui était en vol repris (§11.8). Qui veut la certitude
plutôt que le filet lance la commande **sans** `--detach` dans son propre terminal.

### 11.2 L'ordre, figé une fois — `queue.sh`

Le plan est calculé **au démarrage** et ne bouge plus : deux appels sur le même backlog rendent le
même plan, et un run reste reproductible même si le backlog évolue pendant qu'il tourne.

| Règle | Pourquoi |
|---|---|
| Seuls les tickets **« À faire » et non assignés** du **milestone courant** | un ticket assigné est le travail de quelqu'un (§5) ; un autre milestone n'est pas la phase en cours |
| Les **parents de suivi sont écartés**, remplacés par leurs lots **dans l'ordre de la checklist** | un parent ne porte ni branche ni code (§5.1) ; c'est la checklist qui encode les dépendances |
| Les lots d'un même parent restent **contigus**, le parent héritant de leur priorité maximale | s'intercaler ferait partir le lot suivant d'un `origin/main` qui a bougé pour rien |
| Le reste trié par `prio::` puis iid croissant | pour que l'ordre soit reproductible |
| Chaque ticket porte son **groupe de dépendance** (colonne `groupe`) | l'ordre dit ce qui passe après quoi, jamais ce qui pourrait partir **en même temps** |

`--check` ajoute sur stderr le détail des **écartés avec leur raison** — sans lui, une absence est
indistinguable d'un bug — et, pour la même raison, les **groupes obtenus** : une colonne de plus dans
le plan ne dit pas d'elle-même ce qu'elle a conclu.

**Le plan déclare ce qui est parallélisable** (#288). Sortie TSV : `rang`, `iid`, `parent`, `prio`,
`groupe`, `titre` — `groupe` vient **avant `titre`** parce que le titre est le champ absorbant d'un
`read`, et s'y ferait avaler. La règle de lecture tient en une phrase :

> Deux tickets peuvent être en vol en même temps si leurs **`parent` diffèrent**, ou si leur
> **`groupe` est identique**.

Le groupe est la **vague** du lot dans la chaîne de son parent — `<parent>.<n>` pour un lot, `-` pour
un ticket qui n'en est pas un (tous mutuellement indépendants, comme ils l'étaient déjà). Une suite
maximale de lots **consécutifs** marqués `(parallèle)` (§5.1) forme une vague ; un lot non marqué
forme la sienne et sert de **barrière**. La vague se compte sur toute la checklist, lots déjà livrés
compris : c'est une propriété de la checklist, pas du plan.

Pourquoi une vague plutôt que le marqueur recopié tel quel : la règle « même parent et tous deux
marqués » n'est **pas une relation d'équivalence** — deux lots marqués sont indépendants entre eux
mais dépendants d'un lot non marqué du même parent, si bien qu'aucune étiquette ne peut la porter. La
vague tranche dans le sens **sûr**, celui de §5.1 (« un lot non marqué reste barré par tout ce qui le
précède ») ; deux lots marqués séparés par un lot non marqué tombent donc dans deux vagues, leur
indépendance de principe étant de toute façon sans effet — la barrière qui les sépare les ordonne
déjà.

C'est la seule source d'indépendance du run : `run.sh --concurrence <n>` la **lit** et ne recalcule
rien (§11.10). Sans l'option, il la lit et l'ignore — le run reste séquentiel.

**Le milestone, lui, se choisit** (#204). La phase courante reste le défaut — c'est presque toujours
le bon — mais plusieurs milestones actifs peuvent porter du travail en même temps, et le run partait
sur la phase courante **en silence**. `--milestones` dit sur quoi le choix porte :

```bash
bash scripts/orchestrate/queue.sh --milestones      # titre, courant, à faire et libres, ouverts, échéance
bash scripts/orchestrate/queue.sh --milestone "Phase 5 — Socle réel (backend)"
```

Seuls les milestones **actifs** y figurent (un milestone fermé est une phase soldée), et la colonne
qui décide est **`à faire`** — les tickets « À faire » **et libres**, le filtre du tableau ci-dessus,
et non les tickets ouverts : proposer un milestone dont tout est déjà assigné mènerait à un plan
vide. Le compte est **indicatif** sur un point : un parent de suivi y compte pour un, là où le run
traitera ses lots. [`/orchestrate`](../.claude/commands/orchestrate.md) s'en sert pour **poser la
question** avant un run neuf — et ne la pose que si le choix est réel : un seul candidat s'annonce
sans rien demander.

### 11.3 Un ticket, une session — `run.sh`

**Avant le premier ticket, trois ménages** — tous best-effort, tous muets quand il n'y a rien à
faire, aucun fatal, et aucun joué en `--dry-run` : `main` remise à niveau sur `origin/main` (#283,
§9.3 — `MAESTRO_SYNC_MAIN=0` l'éteint), worktrees soldés ramassés (§9.2), vieux journaux purgés
(plus bas). Même justification pour les trois : un run tourne la nuit, personne n'est derrière, et
ce qui n'est pas fait là ne le sera pas. L'ordre compte pour les deux premiers — le ramassage
mesure le travail non sauvegardé contre `origin/main`, que le `fetch` de `sync-main` vient de
rafraîchir.

Pour chaque ticket : `scripts/git/worktree.sh <iid>` (§9) monte son répertoire de travail et ses
ports, puis une session dédiée est lancée en mode `-p`, avec un `--session-id` fixe — la clé de la
reprise. Un ticket à la fois par défaut ; `--concurrence <n>` en met plusieurs en vol, à la seule
condition que le plan les déclare indépendants (§11.10).

**Le régime d'une session est épinglé par le dépôt, pas par le poste.** Trois réglages décident de
ce que vaut le travail autonome — les deux premiers passés **en toutes lettres** au CLI, le
troisième n'y étant passé que si on le demande :

| réglage | défaut | surcharge |
| --- | --- | --- |
| modèle (#206) | `claude-opus-5` | `--modele`, `MAESTRO_ORCHESTRATE_MODELE` |
| effort (#217) | `xhigh` | `--effort`, `MAESTRO_ORCHESTRATE_EFFORT` |
| plafond de dépense (#286) | **aucun** | `--budget`, `MAESTRO_ORCHESTRATE_BUDGET` |

Le motif est le même dans les deux cas : **un réglage qu'on ne passe pas est un réglage que la
machine choisit**, et aucune sortie de run ne le montre. Pour le modèle, c'était l'alias `opus`,
résolu par le CLI vers une cible mobile (`claude-opus-4-8` encore en 2.1.215). Pour l'effort,
c'était `~/.claude/settings.json` : `run.sh` ne passait aucun `--effort`, et comme `--settings`
**ajoute** une couche au lieu de remplacer la chaîne — le même mécanisme que l'union du `allow`
(§11.7) —, `settings.run.json` ne redéfinissant pas `effortLevel`, c'est le niveau de l'utilisateur
qui s'appliquait. Trois dérives invisibles en découlaient : un clone sans ce réglage traitait le
backlog à l'effort par défaut, un `/effort` posé un jour changeait le régime de **toutes** les
sessions autonomes, et les coûts de `resume.tsv` n'étaient plus comparables d'une machine à l'autre.

D'où deux conséquences pratiques : le niveau est **annoncé dans la ligne `plan :`** à côté du modèle,
du budget et du timeout (donc dans `run.log` et dans `--dry-run` — relire un run dit sur quoi il a
tourné), et un niveau inconnu est **refusé avant le premier ticket**. L'effort étant un ensemble
fermé de cinq valeurs — `low`, `medium`, `high`, `xhigh`, `max` — contrairement à un nom de modèle
qui est une chaîne ouverte, une faute de frappe se détecte : sans ce contrôle, le CLI refuserait la
valeur à **chaque** session et le run brûlerait son plan en échecs jumeaux.

**Le plafond de dépense, lui, ne s'applique plus par défaut (#286).** `run.sh` passait
`--max-budget-usd 15` à chaque session — le garde-fou d'une boucle neuve, quand on craignait
l'emballement. Il coûte aujourd'hui plus qu'il ne protège : une session qui touche le plafond est
**tuée en plein travail**, elle sort sans commit et sans PR, et la boucle la compte en **échec** —
ce qui saborde du même coup les lots suivants de son parent (§11.5). Les deux runs du 2026-08-06
l'ont payé au même montant exact — #277 et #245 coupés à 15.07 $, 16 et 24 fichiers laissés non
commités dans leur worktree, 13 lots sautés en cascade derrière eux —, pour zéro livrable. Un run
reste borné par ce qui le borne vraiment : le fichier `STOP`, la limite d'usage et le plafond
d'attente de 5 h 30 (le `--timeout` par ticket figurait ici jusqu'à #326, qui l'a retiré du défaut
pour la raison exacte décrite juste en dessous) ; le montant, lui, ne borne rien d'utile tant qu'on
ne le demande pas. `--budget <usd>` et
`MAESTRO_ORCHESTRATE_BUDGET` restent là pour en **poser** un — `0` (ou vide) valant « aucun », seule
façon d'annuler une variable déjà posée dans l'environnement, et le repli qui évite surtout qu'un
`--max-budget-usd 0` parte tuer chaque session avant son premier outil. Le régime effectif est
**annoncé dans les deux sens**, dans la ligne `plan :` (« budget illimité » ou « budget N $/ticket »)
comme dans l'aperçu de `--dry-run` : illimité est un choix, pas un oubli, et c'est cette ligne qui
distingue plus tard un ticket coupé au plafond d'un échec de session.

**Le délai par ticket a suivi le même chemin (#326)** — et ce n'est pas une coïncidence de forme :
c'est le second plafond de session, et il tuait de la même façon. `run.sh` enveloppait chaque
session d'un `timeout 45m`. Quarante-cinq minutes étaient larges quand une session durait vingt
minutes ; au régime épinglé par le dépôt (`claude-opus-5` + effort `xhigh`, #206/#217), elles sont
devenues le premier tueur de sessions du run. Le run `20260810-141208` du 2026-08-10 en donne la
mesure : **#315 livré en 42 min 50** — deux minutes de marge — et **#316 coupé à 45 min 02**, alors
que son travail était **fini et commité** (2 047 lignes, dont 695 de tests) et que le couperet est
tombé pendant le `git push` et l'ouverture de la PR. Le plafond n'a donc rien protégé : il a
transformé un ticket livrable en échec, et l'échec en **sept lots sautés** en cascade (§11.5), pour
un seul livrable à 14,75 $.

Le mécanisme est celui du budget, mot pour mot : sans `--timeout`, aucune enveloppe `timeout` n'est
posée ; `--timeout <durée>` et `MAESTRO_ORCHESTRATE_TIMEOUT` restent là pour en poser une, `0` (ou
vide) valant « aucun délai » — seule façon d'annuler une variable déjà posée, et le repli qui évite
qu'un `timeout 0` parte tuer chaque session à l'instant même. Le régime est **annoncé dans les deux
sens** dans la ligne `plan :` (« sans délai » ou « timeout 1h30/ticket »).

Une conséquence à connaître avant d'y toucher : le pilote se donnait une **échéance** par ticket en
vol (`P_ECHEANCE`), calculée à partir du délai de session, pour reprendre le créneau d'un sous-shell
emporté par un SIGKILL — qui n'exécute aucun trap et ne laisse donc aucun témoin. Sans délai, elle
n'a plus de quoi se calculer, et **aucun plafond de remplacement n'a été inventé** : en poser un
« raisonnable » recréerait exactement le défaut qu'on vient de supprimer, un ticket tué en plein
travail, mais côté pilote et sans même une raison lisible. Ce blocage-là redevient donc ce qu'il
était avant d'être outillé : un run qu'on arrête par `STOP` ou par Ctrl-C.

**La console dit ce que la session fabrique (#176) — et depuis #240, où en est le plan.** En
`--output-format json`, le CLI n'écrit qu'à la fin : entre la ligne `[n/N] #<iid> — …` et le verdict,
la console restait muette jusqu'à 45 minutes, et rien ne distinguait « ça travaille » de « c'est
planté ». La session tourne donc en **`--output-format stream-json --verbose`** — un objet JSON par
ligne, au fil de l'eau. #176 en tirait **une ligne par appel d'outil** (`· Read …`, `· Edit …`,
`· Bash …`). Ce flot a rempli son office, mais il a remplacé « on ne sait rien » par « on ne voit
rien » : plusieurs lignes par minute pendant 45 min et par ticket, un nom d'outil sans son résultat
n'apprend rien, et **l'information qu'on cherche vraiment avait disparu de l'écran** — où en est le
plan, quel ticket tourne, depuis combien de temps. Elle existait pourtant, mais **ailleurs** : dans
`status.sh`, c'est-à-dire dans un autre terminal que le seul qu'on regarde.

**Ce que la console rend, c'est donc la checklist du plan**, mise à jour au fil du run :

```
  ✓  1. #237      4 min    1.20 $ PR #312  Compteur [n/N] faux en reprise de run
  /  2. #240     12 min                    Console d'un run : une checklist vivante
       · Edit scripts/orchestrate/run.sh
     3. #284                               Écran Projets dans la Control Tower
  run 16 min · ✓ 1 · ✗ 0 · ~ 0 · reste 1
```

Un ticket en vol porte un **rouet** et un **chrono**, plus **une seule** ligne d'action — le dernier
outil appelé, réécrit sur place. Les tickets déjà jugés portent leur marque (`✓` livré, `✗` échec,
`~` sauté), leur PR, leur durée et leur coût, lus dans `resume.tsv` ; le pied donne le cumul du run.
L'attente d'une limite d'usage (§11.4) est un état comme un autre : la ligne reste au bloc, marquée
`=` et décomptée jusqu'à l'heure de reprise, au lieu de paraître figée pendant des heures. Le flot
d'outils de #176 reste disponible pour le diagnostic — **`--verbeux`**, ou
`MAESTRO_ORCHESTRATE_VERBEUX=1` — et **désactive alors la vue** : les deux se disputeraient l'écran,
et c'est justement quand on lit chaque ligne qu'on ne veut rien qui bouge.

**À N tickets en vol, il y a N lignes vivantes (#290).** `--concurrence` (§11.10) fait tourner
plusieurs sessions à la fois ; chacune a son rouet, son chrono et son action :

```
  ✓  1. #288      6 min    0.90 $ PR #401  queue.sh : le plan déclare ce qui est parallélisable
  /  2. #290     12 min                    Console et status.sh : rendre compte de N en vol
       · Edit scripts/orchestrate/run.sh
  \  3. #291      4 min                    Limite d'usage avec N sessions en vol
       · Bash .venv/Scripts/python.exe -m pytest tests/test_orchestrate.py
     4. #292                               Tests + doc de l'orchestration concurrente
  run 18 min · 2 en vol · ✓ 1 · ✗ 0 · ~ 0 · reste 1
```

Ce que ce lot a changé n'est pas le dessin mais **qui dessine**. #289 avait éteint la vue au-delà d'un
ticket, et son diagnostic était juste : le bloc était dessiné **depuis le sous-shell de la session**,
et sa hauteur vivait dans un fichier que N sous-shells auraient réécrit l'un sur l'autre — pas une vue
dégradée, un écran corrompu, chaque frame comptant ses lignes depuis le mauvais endroit. La réponse
n'est pas de partager l'écran entre N écrivains mais de **le retirer à tous sauf un** :

| | avant #290 | depuis #290 |
| --- | --- | --- |
| qui dessine | le sous-shell de la session courante | le **pilote**, seul |
| ce que fait une session | dessine, efface, réimprime | **publie** son action dans `<iid>.vue` |
| la hauteur du bloc | un fichier partagé (`.vue-hauteur`) | une **variable** — un seul processus la lit et l'écrit |
| une ligne permanente d'un ticket | stdout → `tee` → écran, en course avec la frame suivante | une **file** que le pilote vide entre deux frames |

Trois points à connaître avant d'y toucher :

- **le chrono n'est pas publié par la session, il est calculé par le pilote.** Il vaut pour le
  **ticket**, donc à travers ses reprises (§11.4) : une valeur publiée par une session repartie de
  zéro le ferait reculer à chaque limite d'usage ;
- **la hauteur du bloc varie** maintenant d'une frame à l'autre — un ticket qui se solde rend sa
  ligne d'action. Le bloc se termine donc par `ESC[J`, qui efface ce qu'une frame plus haute avait
  laissé sous lui, et il **se borne à la fenêtre** : plutôt que de déborder (donc de faire défiler,
  donc de se dédoubler à la frame suivante), il masque des lignes déjà jouées **et le dit** ;
- **une frame est dessinée à chaque lancement de ticket**, pas seulement dans la boucle d'attente.
  Remplir N créneaux prend le temps de N montages de worktree et de N lectures GitLab, et surtout la
  boucle d'attente **ne tourne pas** quand les sessions se soldent aussi vite qu'on les lance : le
  bloc restait alors vide tout le run — c'est le défaut qu'a révélé le premier essai à trois
  tickets, invisible à `--concurrence 1`.

**Le compteur du pied dit ce qu'il reste, pas où on en est.** `reste` valait `nb_plan - POSITION`,
c'est-à-dire la position du dernier ticket lancé : à N en vol les tickets ne se prennent plus dans
l'ordre, et cette soustraction désignait un autre ticket que celui qu'on croyait. Il se compte
désormais sur ce qui n'est **ni soldé ni en vol** — `plan − (✓ + ✗ + ~) − en vol` —, ce qui le rend
juste quel que soit l'ordre, **sautés compris**. Le `[n/N]` de l'en-tête d'un ticket, lui, ne change
pas : il dit la **position dans le plan** (#230), la seule chose qu'il ait jamais dite.

**Un bloc qui tient en place, et rien d'autre à l'écran (#284).** La vue de #240 était juste ; ce qui
ne l'était pas, c'est **ce qui l'entourait**. Trois défauts la noyaient, et deux d'entre eux étaient
invisibles à la relecture de `run.log` — ce qui explique qu'ils aient tenu :

| ce qu'on voyait | la cause | ce qui a changé |
| --- | --- | --- |
| l'historique se remplit de copies du bloc | la frame se terminait par un **saut de ligne**, et un `\n` écrit sur la rangée du bas fait **défiler le tampon** — cinq fois par seconde | la dernière ligne du bloc n'a plus de `\n` : le curseur y reste, et le repositionnement vaut `hauteur - 1` |
| le curseur saute sans arrêt | il est déplacé d'un bout à l'autre du bloc à chaque frame | il est **caché** tant que la vue tient l'écran, rendu par `vue_ferme` (sortie normale, erreur ou Ctrl-C) |
| une ligne `… 12min00 · Bash …` s'accumule sous le bloc | le **battement** partait sur stdout, donc par `tee`, donc à l'écran — et forçait un redessin « à neuf » qui laissait le bloc précédent derrière lui | il part vers le **journal seul** ; l'écran a déjà l'information dans le bloc, en plus frais |

Le redessin ne se fait plus qu'**une fois par seconde** (rien de ce que la frame montre ne bouge plus
vite : le chrono compte les secondes), et chaque frame coûte une poignée de forks — à cinq images par
seconde, la console passait son temps à se réécrire pour afficher le même texte.

Quatre points à connaître avant d'y toucher :

- **la sortie d'un run n'est pas un terminal.** Le lanceur de `--detach` fait `… 2>&1 | tee -a
  run.log` : stdout est un **tube** — c'est déjà toute la raison d'être de
  `MAESTRO_ORCHESTRATE_COULEUR`. Y redessiner déverserait une frame par rafraîchissement dans
  `run.log`, que le `sed` final ne nettoierait même pas (il ne retire que les séquences de couleur,
  pas les déplacements de curseur). D'où **deux sorties** : stdout garde la trace permanente
  (en-tête de ticket, verdicts), et les frames partent sur un **descripteur dédié**, que le lanceur
  ouvre avant le tube (`exec 4>&1`, passé par `MAESTRO_ORCHESTRATE_CONSOLE_FD`). Sans console —
  détachement Unix, CI, tests — aucune frame n'est émise : la vue **retombe en plein texte**, une
  impression par ticket, sans animation ;
- **le lanceur ouvre aussi le journal (`exec 5>>run.log`, `MAESTRO_ORCHESTRATE_TRACE_FD`, #284).**
  Deux usages, tous deux impossibles autrement. Y déposer une ligne qui n'a **rien à faire à
  l'écran** — le battement — sans passer par `tee`. Et écrire **soi-même** sur la console une ligne
  qui doit y être : `tee` est un **autre processus**, et rien ne garantit qu'il écrira sa ligne avant
  la frame qu'on dessine juste après ; une frame arrivée trop tôt compte ses lignes depuis le mauvais
  endroit, et le bloc **se dédouble**. Un écrivain unique par écran, donc — c'est ce que fait la
  fonction `trace`, à réserver aux endroits où une frame suit de près (l'entrée en attente de limite
  d'usage) ; ailleurs un `printf` ordinaire suffit. Les deux écrivains du **fichier**, eux,
  coexistent sans risque : O_APPEND et ligne à ligne, `tee` vidant son tampon à chaque lecture ;
- **le chrono demande une horloge, pas un événement.** La boucle est bloquée sur la lecture du flux :
  rien n'y ferait avancer un compteur. C'est `read -t` qui bat la mesure — un tour toutes les 0,2 s,
  qu'une ligne soit arrivée ou non —, ce qui évite tout processus d'affichage séparé (§11.9 n'a donc
  rien de plus à tuer). À ne pas confondre avec la cadence de **redessin**, qui est d'une frame par
  seconde (ou tout de suite sur un changement d'action) : lire le flux vite et réécrire l'écran
  lentement sont deux besoins distincts. Corollaire à ne pas défaire : sur expiration, `read`
  **affecte quand même**
  ce qu'il a déjà lu de la ligne en cours, d'où le tampon qui la recolle — sans lui, un objet JSON
  coupé par une expiration s'écrirait en **deux lignes** dans `<iid>.jsonl`, le fichier dont
  dépendent le coût, le verdict et la détection de limite d'usage ;
- **aucun appel réseau dans la boucle de redessin.** La vue ne lit que `plan.tsv` et `resume.tsv`,
  déjà écrits — c'est ce qui la distingue de `status.sh`, qui interroge GitLab et reste la vue
  « depuis un autre terminal » (§11.5).

**Un repère qui se recalcule, au lieu de se cumuler (#325).** Ce qui précède laissait un défaut de
conception : le bloc était repositionné **à partir du curseur**, de `hauteur - 1` rangées vers le
haut. Le repère était donc **cumulatif** — juste tant que rien n'ajoute une rangée qu'on n'a pas
comptée, et faux **pour toujours** dès qu'une l'ajoute. Une seule ligne repliée suffit : la frame
suivante remonte trop peu, abandonne la première ligne du bloc derrière elle, et **recommence à
chaque redessin**. Constaté en production sur le run du 2026-08-10, où le ticket courant s'affichait
**trois fois** — deux décalages, jamais rattrapés, et chaque copie définitive.

Trois changements, du plus profond au plus superficiel :

- **le repère est ancré sur le bas de la fenêtre** dès qu'on sait que le bloc y touche (`vue_ancre` :
  `ESC[999B` puis la remontée). Le déplacement vers le bas est **borné par le terminal**, donc la
  position obtenue ne dépend plus d'aucun compte à nous et **se recalcule à neuf à chaque frame** :
  une désynchronisation coûte au plus **une frame fausse**, là où elle coûtait une copie par seconde.
  Passé le premier écran, c'est le régime de tout le reste du run ;
- **la taille de la console est relue en cours de run** (`vue_mesure`, toutes les deux secondes) et
  non figée à l'ouverture. La figer, c'était parier qu'une fenêtre ne change pas de taille en cinq
  heures — et une largeur périmée fabrique précisément le repli du point précédent. Un changement
  déclenche un **redessin complet** ;
- **la largeur se mesure en colonnes affichées** (`colonnes`) et non en `${#s}`, qui compte des
  **octets** sous une locale C et des **caractères** sous UTF-8. Le bloc n'avait donc pas la même
  largeur d'un poste à l'autre, et la machine qui comptait des octets repliait ses lignes — encore
  le même défaut, par une troisième porte. L'ellipse de `tronque` est comprise dans la borne.

Deux points à connaître avant d'y toucher. `VUE_ROW`, qui répond à la seule question « le bloc
touche-t-il le bas ? », est une **borne inférieure** assumée : on ne sait pas ce que la console
portait avant nous, donc on part de la rangée 1 et on ne compte que **ses propres déplacements**,
sans jamais compter une rangée qu'on n'est pas sûr d'avoir consommée. **Sous-estimer** ne coûte que
de rester un peu plus longtemps dans le régime relatif ; **sur-estimer** collerait le bloc au bas de
l'écran en laissant un trou sous le journal — c'est le sens qu'il faut lui garder. Et
`vue_mesure` tient une hauteur inférieure à **10** pour aberrante et lui substitue 40 : un test qui
veut voir le régime ancré doit rester **au-dessus de ce plancher**, sinon il mesure la fenêtre par
défaut sans que rien ne le dise.

**Deux fichiers, et c'est ce partage qui rend le mode sûr** : le flux brut va dans `<iid>.jsonl`, et
`<iid>.json` ne reçoit **que l'objet `result` final**. Le coût, le verdict et la détection de limite
d'usage lisent ce dernier, or ils prennent la **première** occurrence d'une clé — y déverser tout le
flux ferait rapporter le coût d'un événement intermédiaire, une régression silencieuse. Si aucun
`result` n'est passé (CLI plus ancien, flux coupé), la dernière ligne en tient lieu. Les **deux**
invocations sont concernées, session neuve *et* reprise `--resume`.

**Le verdict vient de GitLab, pas de la prose de la session.** Un ticket est réussi si, et seulement
si, sa branche porte une **PR ouverte** *et* que son cycle de vie est **« En revue »** — exactement
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
  « PR "aucune", statut "À faire" » confondait : `session terminée sans clôture, 5 fichier(s) non
  commité(s)` (rattrapable — le travail est là, la console dit où) contre `session terminée sans
  rien produire (worktree propre)` (à refaire). Les commits d'avance sur `origin/main` comptent au
  même titre : une session peut s'être arrêtée juste avant `/ticket-ship`.

**Sur échec**, le ticket est laissé en l'état et **les lots suivants du même parent sont sautés**
(ils partiraient d'une base incomplète) ; les autres groupes s'enchaînent — une erreur à 2 h du
matin ne doit pas geler le reste de la nuit. Un ticket **pris par quelqu'un d'autre** entre le calcul
du plan et son tour est sauté, pas volé : son statut est relu juste avant de le prendre.

Garde-fous : `--max <n>` (compte les tickets **tentés**, pour qu'une panne systématique n'épuise pas
le plan) et le fichier `.maestro/orchestrate/STOP`, pris en compte entre deux tickets **et pendant
une attente**. `--budget <usd>` (#286) et `--timeout <durée>` (#326) en sont aussi, mais **sur
demande seulement** et pour la même raison : atteints, ils coupent la session en plein travail, se
comptent en échec, et cascadent sur les lots suivants du même parent — ce sont les deux seuls de
ces garde-fous qui détruisent du travail au lieu d'en borner l'ampleur. Voir §11.3.

Journal, sous `.maestro/orchestrate/<run-id>/` : `plan.tsv` (le plan figé), `<iid>.session`
(l'UUID), `<iid>.jsonl` (le flux d'activité complet), `<iid>.json` (le seul résultat final — coût,
`permission_denials`), `<iid>.resultat.txt` (le même, **en clair**), `<iid>.log` (stderr), et
`resume.tsv` (une ligne par ticket : verdict, PR, durée, coût, raison) et `pid` (la carte du
pilote — §11.9 —, présente le temps du run). Un run lancé avec `--detach` y ajoute `lancer.sh` (ce
qui a été lancé) et `run.log` (toute la sortie de la console, flux d'activité compris) ; un run de
**reprise** (§11.8), `reprise-de` — l'id du run dont il continue le plan.

**Après un run, le résultat d'une session se lit à l'œil nu** (#180). `<iid>.json` est le premier
fichier qu'on ouvre après un échec, et il est écrit sur **une seule ligne minifiée** — 13 Ko d'une
traite pour un ticket : le post-mortem du run `20260729-132807` a demandé un script Python pour en
tirer le message final et la liste des refus. Ce fichier ne change pas pour autant — il reste brut
et byte-transparent, parce que c'est **lui** que grepent le verdict, le coût et la détection de
limite d'usage (§11.4). La même matière est écrite **à côté**, en clair, dans
`<iid>.resultat.txt` : verdict GitLab, état de session, durée, coût, **refus de permission** (comptés
par outil puis détaillés, §11.7) et **message final désescapé**. Une session morte sans rendre la
main — le cas le plus opaque — le dit et renvoie au flux et à la sortie d'erreur, plutôt que de
laisser une vue vide. La lecture est faite en `awk`, sans `jq` ni Python : le pilote est un script
shell, il le reste. Pour un journal écrit **avant** ce lot, ou pour relire depuis un autre poste :

```bash
bash scripts/orchestrate/run.sh --resultat .maestro/orchestrate/<run-id>/<iid>.json
```

Le **coût** y est arrondi à deux décimales, comme dans `resume.tsv` et dans la console :
`total_cost_usd` sort du CLI en flottant brut (`10.686978499999995`), qui n'apprend rien de plus que
`10.69` et déborde de toutes les colonnes.

**Ce journal ne s'accumule plus sans fin** (#198). Rien ne le nettoyait : `run.sh` crée un
répertoire **par lancement** et ses deux `rm -rf` sont des renoncements (lancement détaché en
échec, `--dry-run`), pas un ménage. Indolore tant que le journal ne portait que des logs — 41 Ko
pour un run entier — mais le `<iid>.jsonl` de #176 est le flux `stream-json` **brut** d'une session,
non tronqué : c'est lui qui décide désormais de la croissance. Deux gestes, portés par
`scripts/orchestrate/journal.sh` et déclenchés **au démarrage de chaque run** :

- **rétention** — seuls les **N runs les plus récents** sont conservés
  (`MAESTRO_ORCHESTRATE_JOURNAL_RUNS`, défaut 10) ; les répertoires **vides** que laissent les
  sorties précoces sont ramassés. Ces sorties-là n'en laissent d'ailleurs plus (#180) : un backlog
  vide ou un `queue.sh` en échec **renonce à son run** au lieu d'abandonner un dossier horodaté qui
  ne porte qu'un plan sans ligne — quatre de ces vestiges traînaient, qu'aucune rétention ne
  ramassait puisqu'ils n'étaient pas strictement vides. Le renoncement s'arrête net dès qu'un autre
  fichier est là : un journal qui a servi n'est jamais emporté ;
- **compaction** — le `<iid>.jsonl` d'un ticket **terminé** est gzippé en `<iid>.jsonl.gz`, à
  relire avec `zcat`/`zgrep`. Jamais avant le verdict : tant que le ticket tourne, la détection de
  limite d'usage relit ce flux **entier** à chaque tentative (§11.4).

```bash
bash scripts/orchestrate/journal.sh gc --check   # ce qui partirait, sans rien écrire
bash scripts/orchestrate/journal.sh gc           # le ménage, à la main
```

**Rien n'est retiré sous les pieds d'un run** : ni celui qui fait le ménage, ni un run dont la
dernière écriture date de moins de `MAESTRO_ORCHESTRATE_SILENCE` (défaut 900 s). L'activité se
**déduit** ici du silence — le ménage n'a pas besoin de la carte du pilote (§11.9) pour être
prudent, et le doute profite au journal. Le
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

Deux garde-fous encadrent ces filets, tous deux payés par un faux positif (#203) qui a endormi un
run **après** un ticket livré, sans que son verdict GitLab soit jamais lu :

- **Une session sortie en 0 n'est jamais une pause.** Elle est allée au bout de son tour : rien ne
  l'a coupée, il n'y a rien à reprendre, on passe droit au verdict. La détection ne se consulte que
  sur une session en **échec**.
- **La télémétrie du flux n'est pas un refus.** Le CLI ouvre *chaque* session par un
  `{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":…}}` qui rapporte la
  fenêtre de 5 h en cours — présent que la limite soit atteinte ou non. Depuis que le flux brut est
  écrit dans `<iid>.jsonl` (§11.3) et grepé au même titre que le résultat, il faisait matcher
  `rate limited` et livrait son `resetsAt` comme heure d'attente. Ces lignes sont donc **écartées
  avant toute recherche**, sauf celles qui portent un vrai refus (`"status":"rejected"` — à ne pas
  confondre avec `"overageStatus":"rejected"`, une autre clé du même objet).

Au-delà de **5 h 30** d'attente cumulée sur un ticket, ce n'est plus une fenêtre de 5 h mais
l'**hebdomadaire** : le run s'arrête proprement plutôt que de dormir des jours. `--max-reprises`
(3 par défaut) borne les tentatives.

`bash scripts/orchestrate/run.sh --test-reprise <fichier.json>` rejoue ce jugement sur une sortie de
session capturée — c'est ce qui rend la reprise vérifiable **sans attendre de vraiment taper la
limite**.

#### À N sessions en vol, l'attente est celle du **run** (#291)

À un ticket la question ne se posait pas. À N (§11.10), la limite tombe sur **toutes** les sessions à
quelques secondes d'intervalle, et les laisser décider chacune dans son coin n'est pas seulement
redondant — c'est **faux** :

* le flux d'une session porte l'heure de reset, celui d'une autre ne la porte pas (la forme du signal
  n'est pas contractuelle, cf. les trois filets ci-dessus). La seconde retombait sur le palier de
  15 min, se réveillait **avant** le reset, brûlait une reprise pour rien, recommençait, et sortait en
  échec au bout de `--max-reprises` — pendant que sa voisine, mieux informée, repartait tranquillement.
  Deux tickets du même run, deux sorts opposés, sur une information que l'une avait et que **rien ne
  transmettait** à l'autre ;
* le plafond de 5 h 30 ne bornait que la session qui l'atteignait : les N-1 autres continuaient de
  dormir sur une limite hebdomadaire dont le run avait déjà tiré les conséquences.

D'où un **point de rendez-vous unique** dans le journal du run, `<run-dir>/.limite` — la fin d'attente
en epoch, sa **source** (`reset` explicite ou `palier` aveugle), et l'iid de la session qui l'a
**ouvert**. La règle tient en une phrase, et elle vaut à la publication comme à la relecture :

> La **meilleure** information l'emporte, jamais la plus récente : une heure de reset explicite
> remplace un palier aveugle **même si elle est plus tôt** ; à source égale, on ne fait que
> **rallonger**.

Les deux moitiés comptent. Un palier n'est pas une promesse mais un aveu d'ignorance : lui préférer un
reset connu, quitte à raccourcir, c'est cesser d'attendre pour rien. Et à source égale, se réveiller
plus tôt que le voisin, c'est brûler une reprise sur une limite toujours en cours — précisément le cas
qui faisait échouer la session la moins bien informée. La fin est **relue à chaque tranche** (une par
minute) : c'est ainsi qu'une session partie sur un palier profite du reset qu'une autre a publié
depuis.

Ce que le rendez-vous ne change pas : **chaque session coupée est rouverte par son propre uuid**. Une
attente partagée, N reprises distinctes — le contexte déjà payé de chaque ticket reste le sien.

Deux conséquences côté pilote :

* **on ne lance rien pendant une attente en cours.** Un créneau qui se libère jetterait une session
  neuve dans une fenêtre déjà fermée : elle échouerait à sa première requête, brûlerait une reprise et
  rejoindrait la même attente, après avoir consommé un montage de worktree et une lecture GitLab pour
  rien. Le garde-fou ne vaut que si **quelque chose est en vol** — sinon le pilote sortirait de sa
  boucle sur une liste vide et le reste du plan finirait le run sans une ligne de bilan ;
* **le plafond des 5 h 30 est déclaré une fois pour tout le monde** (`<run-dir>/.plafond`, qui porte
  l'iid du déclarant). Les autres sessions le voient à leur tranche suivante et s'arrêtent aussi.

Pas de verrou : `flock` n'existe pas sous MSYS, et la **création exclusive** (`set -C`) est le seul
atome portable disponible — elle suffit à désigner **un** annonceur. La mise à jour d'une attente déjà
ouverte reste un lire-comparer-écrire qui peut théoriquement perdre une écriture ; la conséquence est
bornée et se répare d'elle-même (au pire une session se réveille sur un palier, retrouve la limite et
se remet en attente), là où un verrou coûterait un fichier à nettoyer et un cas « verrou périmé » à
trancher.

### 11.5 Savoir où en est un run — `status.sh`

La console d'un run répond très bien à « où ça en est ? » — depuis #240 elle y répond même très
directement, en tenant la checklist du plan à jour — mais seulement tant qu'on l'a sous les yeux.
Fenêtre fermée, autre poste, run lancé la
veille : il ne restait que le répertoire du run, et la seule façon de trancher entre « ça travaille »
et « c'est planté » était d'aller regarder à la main les *mtimes* d'un worktree.
`scripts/orchestrate/status.sh` (#177) fait cette lecture une fois pour toutes :

```bash
bash scripts/orchestrate/status.sh                     # le run le plus récent, une fois
bash scripts/orchestrate/status.sh --watch [sec]       # ... et rafraîchi tant qu'il tourne
bash scripts/orchestrate/status.sh --run-id <id>       # un run précis   (--list les énumère)
bash scripts/orchestrate/status.sh --no-gitlab         # hors ligne : tout sauf l'état GitLab
bash scripts/orchestrate/status.sh --reprenables       # ce qui peut être repris, en TSV (§11.8)
```

En une sortie : l'état du run, **les tickets en cours** — une section chacun — avec leur temps écoulé,
les **commits et fichiers modifiés de leur worktree**, leur **dernière activité**, leur **état de
la forge** (statut, PR), puis le **reste du plan** et le **bilan des traités** (verdict, PR, durée,
coût). Il y en a N depuis `--concurrence` (§11.10), et l'écran les rend **tous** (#290) : n'en montrer
qu'un serait pire que de n'en montrer aucun — les autres tiennent un worktree et une session sans que
rien ne le dise. Trois conséquences : le compteur « à venir » retranche **tous** les tickets en vol
(retrancher 1 en disait N−1 de trop), le **silence** se mesure sur le **plus récent** d'entre eux — un
run est vivant dès qu'une de ses sessions écrit encore —, et le cache GitLab est indexé **par
ticket**, faute de quoi le ticket suivant le chasserait à chaque tour de `--watch`. Le script est en
**lecture seule** —
il n'écrit ni dans le run, ni dans le dépôt, ni dans GitLab, et ne touche pas à `run.sh` (bash relit
un script au fil de son exécution : un run en cours doit pouvoir être observé sans risque).

Deux partis pris valent d'être connus :

- **Le worktree est le meilleur signal de progression.** Pendant une session, `<iid>.json` reste
  vide — le CLI n'écrit son résultat qu'à la fin. Ce qui dit vraiment que ça avance, ce sont les
  commits et les fichiers modifiés du worktree du ticket, lus avec git, en local.
- **« En cours » se lit quand la carte est là, se déduit sinon.** Depuis #213 (§11.9) un run laisse
  dans son journal la carte d'identité de son pilote (`pid`) : `status.sh` répond alors par oui ou
  par non — « pilote vivant (pid 1234) » —, et un pilote mort requalifie le run en **interrompu**
  sans attendre qu'un silence s'installe. Les **tickets** en cours, eux, restent déduits : ceux du
  plan qui ont un `<iid>.session` sans ligne dans `resume.tsv`. Sans carte exploitable —
  journal d'avant #213, ou run tué par SIGKILL, dont la carte survit à son processus — on retombe
  sur la ligne **activité**, qui date la dernière écriture (répertoire du run *et* index git du
  worktree) et bascule l'en-tête en « en cours ? » au-delà de 15 min de silence
  (`MAESTRO_ORCHESTRATE_SILENCE`) : une déduction présentée comme telle, pas un verdict. Lecture et
  déduction, prises une fois pour toutes, servent de source unique à `--reprenables` et donc à
  `run.sh --resume` (§11.8) : deux formules qui divergeraient se remarqueraient trop tard.

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
  soit le mode de permission**, force-push, `gh pr merge`/`pr close`, `gh run delete`,
  `git reset --hard`, `git commit --no-verify` et **tout commit sur `main`**.
  `guard.sh --check` vérifie que la copie des `deny` n'a pas dérivé du dépôt **et** que le hook
  refuse bien chacune d'elles — sans quoi la seconde couche donnerait une fausse sécurité.
- **Deux refus de plus, et ils ne visent pas `gh`** (#419/#420) : `lib.sh merge-mr` et
  `lib.sh pipeline-wait` sont barrés **dans une session de run**. Ce n'est pas une contradiction
  avec §6 — c'est exactement le chemin que #417 a rendu légitime *ailleurs* : dans un run, le merge
  appartient au **pilote** (§11.11), qui sérialise les merges et attend les pipelines **hors du
  quota des sessions**. Le message de refus le dit, parce qu'un refus est lu par un modèle : lui
  laisser croire que le merge est interdit l'enverrait chercher un contournement, alors qu'il n'a
  simplement plus rien à faire.

**Ce qu'un run ne fait jamais** : fermer une PR, force-pusher, fermer un parent de suivi, ou
retirer le worktree d'un ticket qu'il vient de traiter — la branche y vit jusqu'au merge. Le
ramassage de son **démarrage** (§9.2) ne touche que les worktrees dont la forge confirme le travail
soldé, donc jamais ceux du run en cours.

⚠ **« Merger » a quitté cette liste** (#419). Jusqu'à ce chantier, un run produisait **N PR en Draft
à relire** et s'arrêtait là ; il **merge désormais lui-même**, mais jamais hors de `merge-mr` et
jamais depuis une session — voir §11.11.

### 11.7 Après un run : instruire les refus de permission

L'`allow` de `settings.run.json` se complète **à partir des refus observés**, jamais à l'aveugle.
Chaque session laisse ce qu'elle n'a pas pu faire dans `permission_denials`, à la fin de son
`<iid>.json` — et depuis #180 la liste se lit **en clair**, sans script, dans la vue que le run
écrit à côté (§11.3) :

```bash
cat .maestro/orchestrate/<run-id>/<iid>.resultat.txt      # refus comptés par outil, puis détaillés

# Pour un journal antérieur à #180, qui n'en porte pas :
bash scripts/orchestrate/run.sh --resultat .maestro/orchestrate/<run-id>/<iid>.json
```

Ça, c'est **une** session. La question qu'on se pose après un run est l'autre — « qu'est-ce qui a
été refusé, **en tout** ? » —, et y répondre demandait de dépouiller 16 JSON à la main. C'est ce
qu'a coûté l'analyse fondant #232, et ce qu'aucune instruction ne fera deux fois. Depuis #235 c'est
une commande, que le run **rappelle lui-même** dans son résumé de fin :

```bash
bash scripts/orchestrate/journal.sh refus            # le dernier run qui porte un résultat
bash scripts/orchestrate/journal.sh refus <run-id>   # un run précis
bash scripts/orchestrate/journal.sh refus --tous     # tout le journal, pour la tendance
```

Elle agrège les `permission_denials` **par outil**, puis **par commande** — en découpant chaque
chaîne comme le CLI la découpe, si bien que chaque maillon compte pour lui-même —, avec la
provenance (`#130 ×2, #131`) et un exemple. Lecture seule, en `awk`, sans `jq` ni Python. Trois
choses n'apparaissent qu'à ce niveau-là : le **poids** d'une forme (six refus `env` sur cinq
sessions ne se voient pas un par un), le **maillon** réellement fautif d'une commande composée, et
les refus que **rien dans le dépôt ne lèvera**, comptés à part pour qu'on n'aille pas leur écrire
une règle inutile.

**Combien et de quoi ne dit pas pourquoi** (#307). Des compteurs seuls ont laissé lire chaque refus
comme un trou d'allowlist — le gisement que #232 avait pourtant fini d'exploiter, si bien que le
sujet passait pour clos pendant que le compte, lui, ne baissait pas. La sortie s'ouvre donc
désormais sur un **classement**, et il range chaque refus dans **une seule** famille, choisie sur le
**geste** qu'elle appelle :

| Famille | Ce qui la caractérise | Le geste |
| --- | --- | --- |
| **Trou d'allowlist** | un maillon qu'aucune règle ne couvre | `settings.run.json` |
| **Échappée de chemin** | tous les maillons couverts, mais la cible sort du répertoire de travail | `prompt_ticket`, jamais la liste — une règle de **préfixe** ne borne pas une cible |
| **Blocage dur `.claude/`** | refus du CLI, en amont de la liste (#229, mesuré par #238) | rien : le ticket se traite en session interactive |
| **Refus voulu (`ask`/`deny`)** | une règle du dépôt le demande, personne ne peut approuver | rien : c'est le contrat de la règle |
| **Forme immatchable** | saut de ligne, `$(…)`, heredoc — quoi qu'elle habille | l'outil `Write`, puis le **chemin** (tableau ci-dessous) |

Trois choix de méthode, sans lesquels le chiffre ne voudrait rien dire :

- **Les règles sont lues là où elles vivent** — `settings.run.json` **∪** `.claude/settings.json`,
  puisque c'est le régime réel d'une session (l'union, plus bas). Le classement ne peut donc pas se
  périmer en silence, ce qui était le défaut de la lecture manuelle qu'il remplace. Corollaire à
  connaître : ce sont les règles **d'aujourd'hui**, donc sur un vieux run un refus « inclassé » dit
  le plus souvent « déjà instruit depuis ». **C'est le dernier run qui se lit pour agir.**
- **L'ordre de décision est le contenu du classement** : `.claude/`, refus voulu, trou d'allowlist,
  échappée de chemin, forme. On ne conclut à l'échappée que si **rien d'autre** n'explique le refus
  — ce qui rend la thèse de #307 plus difficile à établir, pas plus facile.
- **Un maillon découvert qui porte un chemin absolu n'est pas un trou.** Sa forme relative, elle,
  serait couverte (`.venv/Scripts/python.exe …`, `bash scripts/…`), et aucune règle de préfixe ne
  pourra jamais borner un absolu. Le compter comme un trou enverrait élargir la liste pour rien.

Ce que la mesure du 2026-08-09 a rendu, sur le dernier run complet (`20260807-105815`, 12 refus sur
7 sessions) : **9 échappées de chemin (75 %)**, 2 trous d'allowlist, 1 forme. Sur les onze runs du
journal (58 refus sur 24 sessions) : 29 échappées, 14 trous, 7 inclassés, 4 blocages `.claude/`,
3 formes, 1 refus voulu. Autrement dit : les sept commandes les plus refusées (`echo`, `cd`, `tail`,
`cat`, `head`, `grep`, `sed`) sont **toutes dans l'`allow`** — la liste n'est plus le bon endroit où
chercher.

**L'atelier de session** (#307) est la réponse à la moitié évitable de ces échappées. Une session
écrit forcément des fichiers de travail quelque part — description de PR, corps de commentaire,
sortie intermédiaire à relire —, et les deux endroits qu'elle connaît spontanément sont son
répertoire temporaire et `/tmp`, tous deux **hors du répertoire de travail**. Le prompt ne pouvait
donc pas s'en tenir à « reste en relatif » : interdire sans désigner ne fait que déplacer le refus.
`worktree.sh` monte donc **`.maestro/session/`** dans chaque worktree (gitignoré, convention de
§8.5), `prompt_ticket` le nomme, et rien ne l'efface — contrairement au filet CI, une note de
travail vaut d'être relue au tour suivant, et le worktree part en entier quand le ticket est soldé
(§9.2). Même raison pour `journal.sh` lui-même, qui résout désormais le journal vers le **clone
principal** d'où qu'on le lise : sans ça, lire ses propres refus depuis un worktree demandait un
chemin absolu, et l'outil de mesure produisait le refus qu'il mesure.

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
| Le refus vient du **CLI**, pas de la liste (écriture sous `.claude/`) | ne rien changer : le ticket se traite en session **interactive** (ci-dessous) |

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
  ouverte par une création de PR que **seul** `.claude/settings.json` autorise. On ne recopie pas
  pour autant les verbes git/`gh` du dépôt — une copie de l'`allow` dériverait en silence, là où
  `guard.sh --check` veille sur celle du `deny`.

Une règle ne prend pas non plus toujours de spécificateur : **`Skill` s'autorise nu**, le tool ne
déclarant pas de `ruleContentField` (`Skill(ticket-start)` ne matcherait rien), là où `Bash` expose
`command` et `Write` `file_path`.

**Cinq formes qu'aucune règle ne peut reconnaître** (#235, #307). Elles ne dépendent pas de la
commande qu'elles habillent : celle-ci a beau être allowlistée, l'appel tombe. C'est la deuxième
ligne du tableau ci-dessus, et de loin la plus fréquente — le geste est dans la **forme**, donc dans
`prompt_ticket` ou dans ce que le dépôt dicte à la session, jamais dans l'`allow`.

| Forme | Pourquoi rien ne la matche | Le geste |
| --- | --- | --- |
| **Saut de ligne** dans la commande | le CLI découpe dessus et exige chaque morceau ; une `--description` de PR en porte par nature | écrire le texte avec l'outil `Write`, passer son **chemin** |
| **Substitution** `$(…)` ou `` `…` `` | la règle matche du texte, pas le résultat d'une exécution — `--description "$(cat f)"` n'est jamais reconnu | idem : le chemin, pas le contenu |
| **Heredoc** `<<'EOF'` | même cause que le saut de ligne, plus le corps qui suit | l'outil `Write`, jamais `cat > … <<'EOF'` |
| **Chemin absolu hors du worktree** | les règles bornent des chemins **relatifs** ; l'absolu sort de la borne et demande une approbation | rester en relatif depuis le worktree, et y écrire ses fichiers de travail (`.maestro/session/`) — et n'y envoyer personne (§8.5) |
| **Préfixe de variable** `VAR=… <commande>` | une règle est un préfixe de **commande**, or la commande commence par la variable | `env VAR=… <commande>`, que `Bash(env:*)` couvre déjà |

Cette dernière ligne est le seul **vrai** trou d'allowlist qu'aient laissé les onze runs suivant
#232 (2 refus, #188 et #290, dont 1 encore sur le dernier run complet), et #307 l'a **écarté** de la
liste plutôt qu'ajouté : la seule règle qui le matcherait devrait porter la **valeur** en dur
(`Bash(REDIS_URL=redis://127.0.0.1:6399/0 .venv/…:*)`), ne couvrirait que celle-là, se périmerait au
premier port changé et ne se généraliserait pas — la variable suivante sera une autre. Le geste
existe déjà et ne coûte rien : `env` est allowlisté depuis #235, avec la réserve qui l'accompagne
(`env VAR=x <commande>` porte une commande arbitraire ; ce qui la retient est `guard.sh`, qui juge
le **texte entier** de l'appel).

Les trois premières sont la cause n°1 de #232 : **huit sessions sur seize** ont buté sur un
`gh pr create --body` multi-ligne, puis sur le `"$(cat …)"` par lequel elles essayaient de
s'en sortir — **sur la dernière action du ticket**, tout commité et rien pour le déclarer. Deux
remèdes s'y répondent, et il faut les deux : `prompt_ticket` **nomme** les formes et renvoie vers
`Write` (une session ne peut pas les deviner d'un refus, qui ne dit jamais ce qui a manqué), et le
dépôt cesse de les **dicter** — `lib.sh create-mr <iid> <fichier>` / `issue-note <iid> <fichier>`
font voyager le texte long par un **fichier**, l'appel restant plat et court (#233). Le `$(cat …)`
survit, mais à l'**intérieur** du script, où aucune permission ne s'applique : c'est déjà le parti
pris de `set-description` / `set-mr-description`, dont ce sont les pendants à la création. La
quatrième relève de §8.5 — un journal ne s'écrit pas là où personne n'a le droit de le lire.

Ce que la passe #179 a donné sur ces 17 refus : **11 levés** par six règles (`Skill`, puis `cd`,
`echo`, `printf`, `grep`, `sed` — du décor de pipeline, sans pouvoir propre, mais qui faisait tomber
des commandes déjà autorisées) ; **2 relèvent de la forme d'appel** et sont traités par le prompt
(préfixe `PYTHONPATH=…` devant l'interpréteur, chemin absolu là où la règle borne un chemin
relatif) ; **4 restent refusés à dessein** — les deux attentes actives (`for … sleep 6`,
`until [ -s … ]; do sleep 3; done`) parce que les autoriser rouvrirait le mode d'échec que #178
ferme, `jobs` pour la même raison, et `bash <script hors du dépôt>` qui serait du code arbitraire.

**Un refus qui ne s'instruit pas : écrire sous `.claude/`** (#229, **mesuré** par #238). Le run
`20260804-142402` a vu la session de #188 se faire refuser un `Write` **puis** un `Edit` sur
`.claude/skills/control-tower/SKILL.md` — la mise à jour que son critère 4 demandait. Ce refus-là
ne relève d'aucune des trois lignes ci-dessus, et surtout **aucune règle ne le lèvera** : rien dans
le dépôt ne le produit. `settings.run.json` autorise `Write` et `Edit` **nus**, le run tourne en
`--permission-mode acceptEdits`, `guard.sh` ne juge que les appels `Bash` et sort en 0 pour tout le
reste (délibérément — §11.6), et ni `.claude/settings.json` ni `settings.local.json` ne portent de
`deny`/`ask` sur ces outils. Le blocage vient du **CLI Claude Code lui-même** : `.claude/` est la
surface de configuration de l'agent — permissions, hooks, skills, commandes —, et y écrire exige
une approbation humaine explicite qu'aucun `allow` ni `acceptEdits` ne remplace. En `-p`, personne
n'est là pour l'accorder. C'est le garde-fou qui empêche une boucle sans surveillance de réécrire
ses propres permissions : on ne cherche pas à le contourner.

#229 concluait cela **par déduction**, à partir des règles que le dépôt porte — `Write` et `Edit`
**nus**. La déduction laissait un trou, et #238 est allé le boucher : une règle à **chemin
explicite** (`Edit(.claude/skills/**)`) n'avait jamais été essayée, alors que la lecture du binaire
du CLI suggérait qu'une telle règle est consultée **avant** le garde-fou. Le banc d'essai est dans
le dépôt et se rejoue — quelques minutes, ~0,15 $ :

```bash
.venv/Scripts/python.exe scripts/claude/essai-ecriture-claude.py
```

Une session `claude -p` **jetable par variante**, dans un projet **hors du dépôt** (donc sans
`.claude/settings.json`, sans hooks, sans `CLAUDE.md` de Maestro pour brouiller la mesure), au
régime exact d'un run : `-p`, `--permission-mode acceptEdits`, `--settings <json>`. Chaque session
reçoit la même consigne — d'abord un fichier **témoin** hors `.claude/`, sans lequel la mesure ne
vaudrait rien, puis les écritures visées. Verdict du 2026-08-05 :

| Ce qu'autorise le `allow` | Écriture sous `.claude/skills/` |
| --- | --- |
| `Write`, `Edit` **nus** — l'état du dépôt | **refusée** (reproduit #229) |
| + `Write(.claude/skills/**)`, `Edit(.claude/skills/**)` | **refusée** |
| + les mêmes en **chemin absolu** | **refusée** |
| `Bash(cp:*)`, cible écrite en clair dans la commande | **refusée** |
| `Bash(bash appliquer.sh:*)`, cible en argument du script | *passe* — voir « le repli » plus bas |

Le témoin est écrit dans les cinq cas : c'est bien le garde-fou qui parle, pas une session inerte.
**La conclusion de #229 tient donc, et pour une raison plus forte qu'elle ne le supposait** : le
garde-fou n'est pas un défaut de matching qu'une règle mieux écrite comblerait, il est **en amont**
du `allow` et il **déborde les outils de fichier** — un `cp` dont le CLI sait lire la cible tombe
comme un `Write`. Détail de lecture qui compte pour la suite : ce blocage-là ressort en **erreur
d'outil** (« Claude requested permissions to write to … ») et **n'apparaît pas** dans
`permission_denials`, là où celui de `Write`/`Edit` y figure.

Trois conséquences pratiques :

- **Ne pas confondre avec un trou d'allowlist.** Ajouter `Write(.claude/**)` à `settings.run.json`
  ne changerait rien — c'est mesuré, plus déduit : la couche qui refuse est en amont de la liste. Le
  seul symptôme visible est le `permission_denials`, exactement comme un refus instruisible : c'est
  le chemin du `file_path` qui les distingue.
- **Une session qui le rencontre rend le contenu, elle ne contourne pas.** Un `printf … > <fichier>`
  passerait peut-être la liste, et ce serait précisément l'échec. #188 a fait ce qu'il fallait :
  contenu de remplacement intégral dans la description de sa PR, section « Reste à appliquer à la
  main », plus un commentaire sur le ticket. Un humain l'applique ensuite en session interactive.
- **Mieux vaut ne pas l'y envoyer.** Un ticket dont un critère touche `.claude/**` se traite en
  session interactive dès le départ ; le mettre dans le périmètre d'un run autonome, c'est en
  garantir la part manquante. `queue.sh` ne le détecte pas — c'est au rédacteur du ticket de le
  dire, comme #229 le fait dans ses notes.

**Le repli, étudié et écarté** (#238). Le verdict étant négatif, restait à examiner un script du
dépôt — `scripts/claude/appliquer.sh <source> <cible>`, borné à `skills`/`commands`/`agents` et
allowlisté — qui rendrait le geste explicite et auditable au lieu de le laisser à un humain. La
dernière ligne du tableau règle la question de faisabilité : **il fonctionnerait**. Il n'est pas
retenu, et c'est bien la mesure qui permet de le dire plutôt que de le supposer :

- **il ne passerait que parce que le CLI ne voit pas à travers un script.** L'avant-dernière ligne
  est décisive : le même geste écrit en `cp` est bloqué. Le garde-fou couvre donc ce qu'il sait
  lire, et `appliquer.sh` ne réussirait qu'en le lui cachant. C'est exactement le contournement que
  ce paragraphe s'interdit, à ceci près qu'il serait versionné ;
- **le gain est petit, la perte serait large.** Trois tickets en cinq semaines ont buté dessus
  (#200, #186, #188) ; en face saute ce qui empêche une boucle sans surveillance de réécrire les
  instructions que la boucle suivante exécutera. De ce point de vue `.claude/skills/**` n'est pas
  moins sensible que `settings*.json`, que le ticket excluait pourtant d'emblée : un skill est du
  prompt, et `/ticket-ship` en est un ;
- **le confinement invoqué ne couvre pas le bon moment.** Worktree dédié, PR ouverte à relire, merge
  différé valent pour ce qui est *relu* ; rien ne garantit qu'un skill réécrit dans le worktree ne
  soit pas relu par la session qui vient de l'écrire, avant qu'aucun humain n'ait vu le diff. ⚠ Cet
  argument s'est **renforcé** avec #413 : le merge n'attend plus personne, donc le dernier moment où
  un humain aurait pu voir le diff avant `main` a disparu du chemin nominal.

Ce qui reste à faire est donc inchangé — rendre le contenu dans la PR (#188). Ce qui pourrait encore
être gagné est **en amont** : ne pas envoyer un tel ticket dans un run, ce que `queue.sh` ne détecte
toujours pas.

### 11.8 Reprendre un run qui ne s'est pas terminé — `--resume`

Un run s'interrompt de plusieurs façons, et aucune n'est rare : console fermée, machine éteinte,
limite **hebdomadaire** (§11.4), `--max` atteint, `STOP` posé. Dans tous les cas le `plan.tsv` reste
sur disque — c'est le filet. On le rejoue **tel quel**, sans recalculer l'ordre : le backlog a pu
bouger depuis (ticket pris à la main, lot ajouté, priorité changée) et un plan recalculé n'aurait
plus grand-chose à voir avec celui qu'on croit reprendre.

```bash
bash scripts/orchestrate/status.sh --reprenables      # ce qui peut être repris, en TSV
bash scripts/orchestrate/run.sh --resume <run-id>     # rejoue SON plan
bash scripts/orchestrate/run.sh --resume --detach     # le plus récent, en console indépendante
```

**Personne n'a de run-id à retenir** : sans argument, `--resume` prend le plus récent des runs
reprenables, et [`/orchestrate`](../.claude/commands/orchestrate.md) **propose la reprise de
lui-même** au lancement — le choix « reprendre / nouveau run » remplace alors le simple feu vert
(#204). `status.sh --list` marque les runs reprenables d'un `↻`.

Ce qu'un run reprenable est, exactement : il reste des tickets **sans verdict** à son plan, **et**
il ne tourne plus. Le second point se **lit** dans la carte du pilote quand elle est là (§11.9) : un
run tué redevient reprenable **à la seconde même**, sans quoi celui qu'un démarrage vient
d'interrompre resterait invisible un quart d'heure, écarté pour cause d'écritures trop fraîches.
Sans carte — journal d'avant #213 —, un run tué **en plein ticket** garderait le visage d'un run qui
travaille pour toujours (son témoin de session est là, personne n'a écrit de code de sortie) : il
est alors écarté sur le **silence** (`MAESTRO_ORCHESTRATE_SILENCE`), et la colonne le dit au lieu de
trancher à sa place — l'appelant prévient, il n'affirme pas.

Trois choses à savoir sur ce que la reprise fait du plan :

- **Les tickets déjà livrés se sautent d'eux-mêmes.** La boucle relit le statut GitLab de chaque
  ticket avant de le prendre et n'en prend aucun qui ne soit plus « À faire » — le même contrôle
  anti-collision qu'un run ordinaire (§5).
- **Les tickets qui étaient en vol sont repris** — tous, pas le premier (#291) : la question est posée
  **par ticket**, et un run concurrent coupé en avait N en main. C'est la seule exception à la règle
  ci-dessus. `/ticket-start` leur a posé « En cours » : sans exception, une reprise laisserait derrière
  elle les victimes mêmes de l'interruption, avec leurs worktrees et leur travail non commité.
  L'exception est étroite — il faut que le run repris ait laissé **leur** témoin de session **sans
  ligne de bilan** — et chaque session Claude est **rouverte** avec **son** uuid, comme après une
  limite d'usage (§11.4) : le contexte déjà payé est conservé, et si une session est perdue la boucle
  repart à froid sur celle-là seulement. Un ticket « En cours » que le run repris n'avait **pas** en
  main appartient à quelqu'un d'autre : il reste sauté.
- **La concurrence est rejouée, elle aussi.** Un run coupé alors qu'il avait quatre tickets en main se
  reprend à quatre : le régime est lu dans le fichier `concurrence` du run repris, écrit à son
  démarrage. Sans cela, [`/orchestrate --resume`](../.claude/commands/orchestrate.md) — qui ne passe
  aucune option — le rejouerait en **séquentiel** : les tickets repris seraient bien tous traités, mais
  un par un, et la reprise ne serait plus le même run. Même raison que le plan figé. Un
  `--concurrence` explicite l'emporte, ce qui reste la façon de dérouler à la main, en séquentiel, un
  run qui tournait à N.
- **Le journal est neuf.** `resume.tsv` s'écrit en tête de run, donc rejouer dans le répertoire du
  run repris effacerait son bilan. Le nouveau run porte un fichier `reprise-de` avec l'id de son
  prédécesseur, et `status.sh` l'affiche en en-tête : deux journaux partiels qui racontent la même
  liste de tickets doivent se répondre.
- **Le compteur `[n/N]` dit la position dans le plan**, pas le rang du traitement (#230). Il se
  compte sur **toutes** les lignes du plan, sautées comprises : reprendre un plan de six dont trois
  sont livrés annonce le suivant `[4/6]` et se termine bien sur `[6/6]`. Compté sur les tickets
  **tentés** — ce que fait toujours `--max`, à dessein, un ticket sauté ne coûtant rien —,
  l'affichage repartait à `[1/6]`. Le champ `rang` du plan ne conviendrait pas davantage : un
  `--plan` réduit à un sous-ensemble le donnerait décalé de son propre total (`[4/3]`), `N` étant
  compté sur ce fichier-là.

> **Tests.** [`tests/test_orchestrate.py`](../tests/test_orchestrate.py) — même parti pris que le
> reste : dépôt jetable, **ni réseau, ni quota, ni écriture côté forge**. Un `gh` factice répond
> depuis des fixtures (et **journalise ses appels**, ce qui rend vérifiable une promesse comme
> `--no-forge`, dont l'alias historique `--no-gitlab` est joué lui aussi),
> `MAESTRO_CLAUDE_BIN` remplace le CLI, `MAESTRO_ORCHESTRATE_WORKTREE` le montage
> de worktree et `MAESTRO_ORCHESTRATE_SPAWN` l'ouverture de console, si bien qu'aucune branche,
> aucune session ni aucune fenêtre réelles ne sont créées. Le **flux stream-json** se joue par un
> bouchon qui émet plusieurs événements, dont un coût leurre en tête — la régression que §11.3
> décrit. `status.sh` se teste sur des répertoires de run **écrits à la main** (c'est le seul moyen
> de poser un run interrompu ou muet) dont les dates de modification sont vieillies, et sur un vrai
> petit dépôt git local pour le volet worktree. Seule exception à la règle « rien de réel » : les
> tests de §11.9 lancent de **vrais processus** et les tuent pour de bon — vérifier qu'un arrêt
> arrête ne se simule pas. Depuis #291 ce n'est plus un `sleep` seul mais un **pilote, ses N
> sous-shells et, sous chacun, un petit-fils qui bat** : c'est ce battement qu'on observe, un
> `kill -0` répondant encore « vivant » à un processus tué mais pas encore ramassé. Le volet Windows
> — le `taskkill` par WINPID, seul chemin jusqu'aux `claude.exe` natifs — ne s'y teste pas ; ce que
> ces tests couvrent est la **récursion** de l'arrêt, jamais éprouvée au-delà du pilote lui-même
> avant qu'un run puisse avoir N enfants.

### 11.9 Un seul run à la fois — la carte du pilote et l'arrêt des runs en vol (#213)

**Démarrer ou reprendre un run commence par tuer ceux qui tournent encore.** Deux pilotes vivants,
c'est le même quota brûlé en double, un unique fichier `STOP` pour les deux, et une reprise qui
rejoue le plan d'un run toujours en train de le jouer. Le cas n'avait rien d'exceptionnel : on
relance parce que le précédent « a l'air fini », et il ne l'était pas.

```bash
bash scripts/orchestrate/run.sh --tuer-les-runs   # ne fait QUE ça : arrête, dit lesquels, sort
bash scripts/orchestrate/run.sh --sans-kill       # l'échappatoire : laisser cohabiter, en le disant
```

**La carte du pilote.** Un run pose au démarrage un fichier `<run-id>/pid` — PID, WINPID, naissance
du processus, hôte — et le retire en partant (`trap`, donc aussi sur `exit` d'erreur ou Ctrl-C).
C'est la brique qui manquait : `status.sh` *déduisait* l'activité de la fraîcheur des écritures,
faute de mieux, et une déduction suffit à **dire** qu'un run semble mort, jamais à en **tuer** un.
`scripts/orchestrate/pilote.sh` est le seul endroit qui sait l'écrire, la relire et s'en servir —
`run.sh` et `status.sh` s'y branchent tous les deux.

**Ce qui n'est jamais tué.** Uniquement les PID des cartes, et leurs descendants : jamais un
`claude.exe` trouvé au jugé — la session Claude Code interactive de l'utilisateur en est un. Un
numéro se recycle, et un run tué par SIGKILL laisse sa carte derrière lui (aucun trap ne survit à
un SIGKILL) : ce sont les **témoins** de la carte — **naissance** du processus (en ticks depuis le
démarrage de la machine) et **WINPID** sous MSYS — qui démasquent un PID réattribué. Il suffit
qu'**un seul concorde** pour reconnaître le processus ; il faut que **tous** les témoins comparables
divergent pour conclure au recyclage, et un témoin enregistré devenu illisible fait s'abstenir.

⚠ **La naissance ne peut pas condamner seule** (#456), et c'est ce qui a changé. Son échelle — les
ticks depuis le boot — **peut se décaler pendant un run** : mesuré le 2026-08-24 sur le run
`20260824-094229`, carte à `834570974` contre `834568417` relus pour le même processus jamais
redémarré, soit 2,557 s d'écart (`CLK_TCK=1000`), stable sur deux heures, pendant que le pilote
travaillait. Deux mesures encadrent le fait : la carte **ne diverge pas à l'écriture**, et la valeur
relue est cohérente avec `/proc/uptime` **après** coup — c'est donc l'échelle qui bouge, pas la carte
qui serait née fausse. Un témoin de plus ne relâche pas la protection contre le recyclage, il la
renforce : il faudrait qu'un processus recycle **à la fois** le numéro MSYS et le winpid de l'ancien.

⚠ **Et « dans le doute, ne pas tuer » ne vaut pas pour ce verdict-là.** L'asymétrie est juste pour
l'arrêt, qui vise un processus ; elle est fausse pour la **vivacité**, que `pilotes_vivants`
interroge pour savoir *qui* tuer — un pilote déclaré mort à tort n'est pas tué, donc deux runs
cohabitent, ce que ce mécanisme existe précisément pour empêcher. Et le « doublon signalé » ne l'est
pas : l'arrêt est muet quand il n'a rien à tuer. La protection ne vient donc pas de la prudence du
verdict, elle vient du **nombre de témoins**.

**Sous Windows, deux mondes de processus.** Le pilote est un `bash.exe` MSYS, la session un
`claude.exe` natif que `/proc` ne liste pas et que `kill` n'atteint pas. D'où le WINPID enregistré
à côté et le `taskkill //T //F`, qui descend l'arbre côté Windows — sans lui, la session survivrait
à son pilote, rattachée à personne et toujours en train de consommer du quota.

**À N sessions en vol, un seul `//T` sur le pilote ne suffit plus** (#291). `taskkill` construit
l'arbre à partir des liens parent→enfant que Windows connaît **à cet instant**, or les N sessions d'un
run concurrent pendent chacune d'un sous-shell : il suffit qu'un maillon intermédiaire soit déjà sorti
— une session qui rend la main pendant qu'on tue — pour que son `claude.exe` ne soit plus rattaché à
rien d'atteignable depuis le pilote. `pilote_tue` vise donc **chaque cible par son propre WINPID**,
**du plus profond au plus superficiel** : tuer un parent avant ses enfants est exactement ce qui
fabrique l'orphelin qu'on cherche à éviter. Le coût est un `taskkill` par sous-shell, payé une fois,
au moment d'arrêter un run.

**Le rapport nomme *tous* les tickets interrompus**, pas le premier : chacun laisse son worktree
derrière lui, et c'est précisément ce que ce rapport existe pour dire.

**`STOP` garde à N le sens qu'il avait à 1** : il arrête de **lancer**, il ne tue personne. Les N
sessions déjà parties vont au bout — c'est ce qui fait qu'il ne coûte aucun travail non commité, et ce
qui le distingue de `--tuer-les-runs`. Une session qui **attend** une limite d'usage, elle, le voit à
sa tranche suivante (§11.4) et rend la main.

**L'arrêt est sans sommation, et c'est voulu.** La sortie propre existe — le fichier `STOP` — mais
elle n'est lue qu'entre deux tickets : l'attendre, c'est attendre la fin de la session en cours —
une heure, parfois plus, et depuis #326 sans borne posée d'avance. Or on est là parce que
quelqu'un veut lancer maintenant. Ce que ça coûte est borné
et dit à chaque fois : le journal du run tué reste **intact**, donc **reprenable**
(`run.sh --resume <id>`), et ce qu'une session avait commencé sans le committer dort dans le
worktree de son ticket (`status.sh --run-id <id>`). Ce qui reste à la charge de l'humain, c'est le
ticket resté « En cours » côté GitLab — comme après n'importe quelle interruption (§11.8).

**L'ordre compte** : on tue *avant* de résoudre `--resume`. `status.sh --reprenables` écarte les
runs qui écrivent encore ; un run tué juste après aurait donc été ignoré par un `--resume` sans
argument — celui-là même qu'on vient d'interrompre, et le plus probablement visé. Tué d'abord, il
redevient candidat immédiatement, la carte l'emportant sur la fraîcheur de ses dernières écritures.

### 11.10 N tickets en vol dans un run — `--concurrence` (chantier #287)

Un run traitait le plan **un ticket à la fois**. `--concurrence <n>` (défaut **1**) en laisse partir
jusqu'à `n` — même run, même pilote, jamais N runs (§11.9 reste entier). Ce qui borne le parallélisme
n'est pas décidé ici : le plan le **déclare** (§11.2, colonne `groupe`), et la boucle ne fait que le
lire.

> Deux tickets peuvent être en vol en même temps si leurs **`parent` diffèrent**, ou si leur
> **`groupe` est identique**.

Ne pas recalculer la règle ici est le point : elle vit dans `queue.sh`, elle est figée avec le plan,
et deux formulations finiraient par diverger — c'est exactement ce que #288 s'était donné pour but
d'éviter en publiant une colonne plutôt qu'un marqueur.

**Défaut 1, et ce n'est pas de la prudence d'ingénieur.** Toutes les sessions tirent sur le **même
quota d'abonnement** : N en parallèle épuisent la fenêtre de 5 h N fois plus vite. Le gain est en
**temps de mur**, jamais en quota — et c'est aussi ce qui rend le lot mergeable seul, un run sans
l'option étant celui d'avant, au bit près.

**Le créneau qui se libère prend le prochain ticket *éligible*, pas le suivant.** L'ordonnanceur
balaye **tout** le plan à chaque passage : un lot barré par son prédécesseur est enjambé, et c'est le
ticket d'un autre parent, plus loin dans le plan, qui comble la place. Un plan `#501, #502 (même
parent), #601` à deux créneaux fait donc partir `#501` **avec `#601`**, pas avec `#502`.

**Ce qui part dans un sous-shell, et ce qui n'y part pas.** La ligne de partage est celle entre l'état
du run et ce qui dure :

| au pilote (séquentiel) | dans le sous-shell du ticket |
| --- | --- |
| plan, éligibilité, sauts, `--max`, cascade, compteurs | la session Claude et ses reprises |
| montage du worktree, résolution de la branche, uuid | — |
| verdict GitLab, `resume.tsv`, `<iid>.resultat.txt` | — |

Trois conséquences, toutes voulues :

* **`resume.tsv` n'a qu'un seul écrivain.** La question « une ligne reste-t-elle entière quand N
  processus écrivent en `>>` ? » ne se pose pas : aucun sous-shell n'écrit le bilan. Le pilote le
  fait, à l'unique endroit qui incrémente aussi les compteurs et nourrit la cascade.
* **Le montage des worktrees est sérialisé.** `git worktree add` écrit dans le dépôt partagé et prend
  ses verrous sur les refs ; N montages simultanés sur le même clone, c'est un « cannot lock ref » au
  hasard. Le coût est réel — mesuré ~1 s de pré-vol par ticket sous MSYS, quelques minutes avec
  l'installation d'un vrai worktree — et il se noie dans une session qui dure une heure.
* **Le sous-shell rend son code par un témoin** (`<iid>.fini`), pas par `wait` : bash ne sait pas dire
  de façon portable *lequel* de ses enfants vient de finir (`wait -n -p` demande bash 5.1) et un
  `kill -0` réussit encore sur un zombie. Le fichier répond aux deux questions à la fois. Il est écrit
  par un trap `EXIT` — une session tuée par un signal doit rendre la main au pilote, pas le laisser
  attendre un témoin qui ne viendra jamais. Reste le SIGKILL, qui n'exécute aucun trap : passé le
  temps qu'un ticket peut légitimement prendre, sa place est reprise et il est compté en échec.

**La cascade d'échec se décide maintenant à la fin d'un ticket**, plus à son tour de boucle : avec N
en vol, le tour d'un lot peut arriver avant le verdict de son prédécesseur. Un lot déjà parti n'est
jamais rappelé — le plan l'avait déclaré indépendant, donc de la même vague ; un lot pas encore parti
est sauté au moment de le lancer, comme avant.

**Ce que `--concurrence > 1` ne divise pas, et que la console annonce.** Le chantier #287 est
livré ; il reste **une** limite, de nature différente des autres puisqu'aucun lot ne pouvait la
lever, et le run la dit au démarrage plutôt que de la laisser découvrir :

* toutes les sessions tirent sur le **même quota d'abonnement**. N en vol épuisent la fenêtre de 5 h
  N fois plus vite : le gain est en **temps de mur**, jamais en quota. Ce que #291 en rattrape, c'est
  seulement de ne la payer **qu'une fois** — attente partagée, puis chaque session coupée rouverte
  par son uuid — et non de l'éviter.

**Ce qui a suivi le passage à N** (#290 et #291), documenté là où chaque mécanisme vit : la **vue
vivante** n'est plus éteinte, elle rend les N tickets en vol et c'est le pilote qui la dessine
(§11.3), la **limite d'usage** est devenue une attente **du run** et non de chaque session (§11.4),
l'**arrêt** descend l'arbre complet et nomme les N tickets qu'il interrompt (§11.9), et la **reprise**
rejoue tous les tickets en vol *et* la concurrence du run coupé (§11.8). Le fil commun est le même :
ce qui était vrai d'un seul ticket devient, à N, une propriété du run.

Chaque ligne de ticket porte au passage **son numéro** dans le journal — sans quoi rien ne dirait,
dans une trace entrelacée, à qui appartient un « ✓ PR #99 ouverte ».

**Un plan d'avant #288 retombe en séquentiel.** Rejoué par `--resume`, il n'a que cinq colonnes et son
titre se lit là où on attend le groupe : rien n'y dit ce qui est indépendant. Le nombre de colonnes de
sa première ligne de **données** — l'en-tête peut manquer à un plan écrit à la main — suffit à le
reconnaître, et la concurrence est ramenée à 1 en le disant. Deviner l'indépendance à partir de titres
ferait partir ensemble deux lots qui se suivent.

`--concurrence 0` est **refusé**, comme un effort inconnu ou un budget illisible (§11.3) : zéro
créneau, c'est un run qui ne lance rien. Contrairement au budget, où `0` annule un plafond, il n'y a
ici rien à annuler — `1` est déjà le régime sans option. `MAESTRO_ORCHESTRATE_CONCURRENCE` pose la
même valeur par l'environnement.

**Ce qui est éprouvé, et comment** (#292). Toute la mécanique ci-dessus est couverte par
[`tests/test_orchestrate.py`](../tests/test_orchestrate.py), dans le décor habituel du fichier — dépôt
jetable, ni réseau, ni quota, ni écriture GitLab. Une contrainte s'y ajoute, propre à la concurrence :
**ce qui doit être simultané l'est par une barrière, jamais par un `sleep`**. Chaque session bouchon
signale son arrivée puis attend celle des autres, et les sessions tiennent elles-mêmes le compte du
**pic de simultanéité**. C'est ce qui rend les deux verdicts symétriques et non ambigus : un pic de 2
prouve que deux tickets ont bien été en vol **au même instant** (un run séquentiel se bloquerait sur
la barrière), et un pic de 1 prouve que deux tickets liés ne l'ont **jamais** été — là où une lecture
d'après coup ne distingue pas « jamais ensemble » de « ensemble mais trop vite pour être vu ».

**Et la mesure obéit à la même règle que ce qu'elle mesure** (#313). Le pic était d'abord tenu par un
compteur **partagé** — un fichier lu, incrémenté, réécrit à l'entrée de chaque session, décrémenté à
sa sortie. Deux commandes pour un lire-modifier-écrire : deux sessions qui arrivent ensemble lisent la
même valeur, écrivent la même, et une incrémentation disparaît. Ce que la barrière rend **probable**,
puisque c'est précisément son rôle de les faire arriver ensemble — d'où un pic plafonnant sous le
nombre réel de sessions en vol, un `assert '2' == '3'` sous `-n auto`, vert dès que la machine est au
repos. Le coût n'était pas dans ce test : le filet CI local et le pipeline jouant en parallèle, il
rougissait **n'importe quelle PR** touchant `scripts/orchestrate/**`, sans rapport avec son contenu.
Le compteur partagé a donc disparu : chaque session pose **son** marqueur d'entrée, compte les
marqueurs présents et écrit **son** relevé dans **son** fichier, le maximum étant pris après coup côté
Python (`_pic`). Aucun fichier n'a deux écrivains, donc aucune course ne peut fausser la mesure, et le
relevé reste un **pic** et non un compte final : il est pris juste après une arrivée, l'instant où le
maximum d'un ensemble d'intervalles est toujours atteint — et **avant** de signaler la sienne, pour
qu'aucune des sessions déjà là n'ait pu repartir. (Le passage dans `vus.txt` reste, lui, un `>>`
partagé : une ligne courte ouverte en `O_APPEND` est indivisible, là où un lire-modifier-écrire
réparti sur deux commandes ne peut jamais l'être.)

La même correction ferme la **seconde** voie vers le même symptôme, qu'un compteur juste n'aurait pas
suffi à écarter : la barrière renonçait au bout de 15 s, quand le montage des worktrees est sérialisé
et qu'une machine chargée peut lancer la dernière session après que les premières ont abandonné — pic
légitimement bas, test rouge, produit correct. Le garde-fou passe à 45 s (il ne coûte rien tant que la
barrière se lève) et une session qui renonce le **dit** : `_pic` refuse alors de rendre un chiffre et
nomme les sessions concernées, plutôt que de laisser une mesure non concluante passer pour un verdict
sur le code. Ce délai reste borné par le `timeout` du sous-processus, qui doit couvrir N sessions y
renonçant l'une après l'autre — sans quoi une vraie régression de l'ordonnanceur sortirait en
`TimeoutExpired`, le seul échec de ce fichier qui ne dise pas ce qu'il a constaté.

Deux morceaux sont restés **dans leur lot** plutôt que de rejoindre celui-ci, pour la seule raison qui
vaille — ils ne se simulent pas : l'**arrêt** de N sessions (#291, de vrais processus qu'on tue, dont
on observe le *battement* et non un `kill -0` qui répond encore « vivant » à un zombie) et l'**attente
partagée** d'une limite d'usage (#291, deux sessions qui doivent se ranger derrière le même
rendez-vous — deux attentes séparées ont exactement la même allure à l'écran).

Écrire ces tests a fait tomber un défaut qu'aucun des quatre lots ne pouvait voir seul, parce qu'il
naît de leur **rencontre** : #291 annonçait l'attente d'une limite d'usage par `trace` — écrire sur
l'écran —, ce qui était juste à sa date et ne l'était plus une fois #290 mergé. Une session ne
dessine plus, et `trace` s'appuie sur la hauteur du bloc, devenue une variable **du pilote** dont un
sous-shell n'a qu'une copie figée au fork : rien n'était retiré, la ligne tombait sous un bloc
toujours affiché, et la frame suivante remontait d'une hauteur qui ne correspondait plus à rien.
L'annonce passe désormais par la **file** (`dit`), comme toute ligne permanente d'une session depuis
#290. À retenir pour la suite : l'invariant de hauteur de #284 **ne suffit pas** à voir ce défaut —
la hauteur annoncée reste juste, c'est ce qui s'est glissé entre deux frames qui ne l'est pas. Ce qui
le voit : *tout ce que la vue écrit se termine par `ESC[J`*.

Le reste est ici, lot par lot : les **vagues** rendues par `queue.sh` — dont le cas « même parent, un
seul lot marqué », qui doit rester séquentiel ; l'**ordonnancement** — deux indépendants qui partent
vraiment ensemble, deux liés qui ne le font jamais, le créneau libéré qui saute au prochain
*éligible*, `resume.tsv` intact sous N verdicts, `--max` et cascade justes, le plan à cinq colonnes qui
retombe en séquentiel ; la **vue** — l'invariant qui tient tout le reste, *ce qu'une frame annonce
remonter est ce que la précédente a écrit* (une rangée d'écart et le bloc se recopie dans l'historique
cinq fois par seconde), la ligne d'action de chaque ticket en vol, le compteur du pied ; et la
**reprise** — tous les tickets en vol repris, le « En cours » d'une session voisine toujours sauté, la
concurrence du run coupé rejouée sauf choix explicite.


### 11.11 La file de merge du run — au fil de l'eau, puis le drain final (chantier #413)

Un run **merge ce qu'il livre**. Jusqu'à #419 il s'arrêtait à N Pull Requests ouvertes, à reprendre
plus tard une par une ; il tient désormais une **file de merge** et la vide au fur et à mesure. Ce
qui disparaît n'est pas la vérification — elle vit tout entière dans `lib.sh merge-mr` (§6) — mais
l'attente d'un humain pour la faire.

**Le pilote merge, jamais une session.** C'est le partage qui gouverne tout le reste, et le même
qu'à #289 : le pilote garde ce qui dure (le plan, l'éligibilité, `resume.tsv`), les sessions ne font
que travailler. Trois raisons, dont une seule suffirait :

- **le quota.** Attendre un pipeline coûte 2 à 4 minutes de session pour ne rien faire — et une
  session, c'est du quota. Le pilote, lui, attend hors quota ;
- **la sérialisation.** À N tickets en vol, N sessions qui mergent en parallèle **périment
  mutuellement leur verdict de conflit** : chacune a mesuré son `origin/main` avant les autres ;
- **l'ordre.** Choisir *quelle* PR merger d'abord demande de voir toutes les PR à la fois, ce qu'une
  session ne voit pas.

D'où les deux refus de `guard.sh` propres au run (§11.6) : une session de ticket s'arrête à la PR
ouverte + « En revue », une session de déblocage à la PR **rendue mergeable**.

**Et le pilote ramasse ce que son merge rend inutile** (#438). Sur le verdict `0` de `merge-mr`, et
là seulement, `merge_ramasse` retire le worktree du ticket puis purge sa branche locale — `gc --iid`
**puis** `cleanup-merged <branche>`, dans cet ordre (§9.5 : `git branch -D` refuse une branche
empruntée). C'est le **quatrième déclencheur** du ramassage, et il manquait pour une raison de
calendrier : les trois autres — `ensure`, `/branch-cleanup`, démarrage d'un run — sont tous
*antérieurs* au merge, ce qui était juste tant qu'un humain mergeait plus tard. Depuis #418/#419 un
run de huit tickets se terminait tout mergé **et** avec huit worktrees (~535 Mo pièce) que rien ne
ramassait avant le run suivant. Trois choix à ne pas défaire : le geste est **ciblé** (§9.2 — un
balayage par PR mergée coûterait N² lectures de forge) ; il est **best-effort et muet**, au même
titre que le `sync-main` de `merge-mr` (ni le merge, ni le drain, ni le run n'échouent parce qu'un
répertoire résiste) ; et il est accroché **au verdict, pas à la boucle** — les deux drains passent
par `merge_tente`, donc il n'existe aucun instant où « mergé » est vrai sans que le ménage ait été
tenté. `MAESTRO_ORCHESTRATE_RAMASSAGE=0` l'éteint.

**Pourquoi au fil de l'eau plutôt qu'en salve.** C'est le renversement du constat de #299 : à la fin
d'un run, les PR ne sont pas en conflit avec `main` (toutes les branches partent du même
`origin/main`) mais **entre elles** — un conflit sans côté à résoudre… *tant que rien n'est mergé*.
C'est le premier merge qui donne un côté au suivant. Merger tôt fait donc deux choses à la fois : un
ticket lancé plus tard part d'un `origin/main` qui contient déjà les précédents (donc le conflit
n'existe jamais), et ceux qui restent deviennent résolubles pendant le run plutôt qu'après.

**Deux drains, parce qu'il y a deux moments.**

| | Pendant le run | En fin de run |
|---|---|---|
| Déclenchement | dans la boucle d'attente, une passe toutes les `MAESTRO_ORCHESTRATE_MERGE_INTERVALLE` s (60) | le plan épuisé, plus aucun ticket en vol |
| Attente de pipeline | **non** — le pilote doit continuer à moissonner et à tenir l'écran | **oui** (`pipeline-wait`), sauf si l'arrêt a été demandé |
| Ordre | celui d'entrée | `lib.sh merge-order` (voir plus bas), **recalculé après chaque merge** |
| Bornes | — | plafond global `MAESTRO_ORCHESTRATE_MERGE_PLAFOND` (3600 s) |

⚠ **Une passe s'arrête au PREMIER merge réussi**, et ce n'est pas une économie : un merge déplace
`origin/main` et **périme le verdict de conflit de toutes les autres PR**. Les juger dans la même
passe reviendrait à les juger sur une mesure d'avant. Elles sont donc rejugées à la passe suivante,
et jamais autrement.

**L'ordre du drain final : `bash scripts/gitlab/lib.sh merge-order [<branche>…]`** (#416), repris du
cadrage de #299 — le ticket a été abandonné, son analyse ne l'est pas. Il construit le graphe des
conflits **entre PR** (une arête par paire qui ne se merge pas proprement, mesurée par
`git merge-tree --write-tree`, en lecture seule) et rend les branches par **degré croissant** : une
PR sans voisine d'abord, une PR carrefour en dernier. Le modèle de coût qui le justifie tient en une
phrase — une PR ne paie **qu'une** résolution quel que soit le nombre de voisines mergées avant elle
(un seul `git merge origin/main` les absorbe toutes) —, donc le coût d'un ordre est le nombre de PR
ayant au moins une voisine mergée avant elles. Une PR carrefour mergée en premier force **chacune**
de ses voisines à payer ; mergée en dernier, elle ne paie qu'une fois. Sur la mesure du 2026-08-07
(6 PR, 5 arêtes, un carrefour) : **2 résolutions par degré croissant contre 4** en commençant par le
carrefour. ⚠ C'est une **heuristique et non un optimum** — l'ordre optimal est un ensemble
indépendant maximum, NP-difficile — et il ne faut ni le promettre ni le chercher : à l'échelle d'une
dizaine de PR l'écart est au plus d'une résolution, et le tri par degré se relit. Il n'est appelé
qu'au drain **final** : recalculer un graphe en n(n-1)/2 `merge-tree` à chaque passe coûterait plus
que ce qu'il ferait gagner.

**Ce que le code de `merge-mr` décide**, et lui seul — le drain ne fait que lire ce code (§6) :

| Code | État en file | Suite |
|---|---|---|
| `0` | `mergee` | le ticket se ferme par son `Closes`, le workflow `issues: closed` pose « Terminé » (§9.2) |
| `3` | `attente` | verdict pas encore rendu (en cours, absent, ou **périmé**) — la seule réponse qui laisse en file |
| `4` / `5` | `bloquee` | pipeline rouge / conflit — **réparables**, d'où une session de déblocage |
| autre | `bloquee` | geste humain : rien ne le tente, la PR est nommée au bilan avec sa cause |

Un `4` ou un `5` **sort de la file** : ni un pipeline rouge ni un conflit ne se défont tout seuls, et
y repasser à chaque passe coûterait des appels pour reconfirmer ce qu'on sait déjà. Une PR débloquée
y **revient** (`attente`), et c'est le seul chemin de retour.

**Débloquer pendant le run : une sous-session `/mr-fix`, deux au plus** (#420). Une PR `bloquee` et
réparable reçoit une **session Claude**, montée exactement comme un ticket — même worktree, même
reprise après limite d'usage, même régime — parce que réimplémenter une seconde façon de faire
tourner une session la ferait diverger de la première au premier réglage ajouté. Quatre points :

- **le plafond est de 2** (`MAESTRO_ORCHESTRATE_MRFIX_MAX`), et il est là pour une PR que *rien* ne
  peut réparer : sans lui, un secret manquant consommerait des sessions jusqu'à la fin du run, sur
  le quota du travail restant. Les sessions successives portent leur rang dans le journal
  (`<iid>-mrfix`, `<iid>-mrfix2`), sans quoi la seconde écraserait ce qu'on ira lire pour comprendre
  pourquoi la première n'a pas suffi ;
- **le verdict de la session n'est pas lu** — ni sa prose, ni son code de sortie. La PR retourne en
  `attente` quoi qu'elle ait fait, et c'est `merge-mr` qui tranche au passage suivant. Même règle
  que pour un ticket (§11.3, « le verdict vient de la forge, pas de la prose de la session ») : la
  seule chose qu'une session sache dire est ce qu'elle a *tenté*.
  Une session qui a échoué coûte donc un appel de plus ; le plafond borne ce que cette générosité
  peut coûter ;
- **elle prend un créneau**, comme un ticket : mêmes refus au lancement (arrêt demandé, attente
  d'une limite d'usage en cours, concurrence pleine) ;
- **au drain final elle est le dernier recours** : on ne l'ouvre que si *aucune* PR n'a bougé — une
  PR qui se merge telle quelle ne vaut pas une session.

`--sans-merge` rend le run d'avant le chantier (PR laissées ouvertes) ; `--sans-mrfix` garde le merge
et retire le déblocage. Le régime effectif est **annoncé dans la ligne `plan :`** : « aucune PR
mergée » et « aucune PR mergeable » ne doivent pas se confondre.

**Le journal.** `merge.tsv` porte la file en entier — `iid`, `pr`, `branche`, `etat`, `code`,
`essais`, `cause`, puis `mrfix` et `cout` — réécrit à chaque changement plutôt qu'ajouté en fin de
fichier : une ligne **change** d'état, et un journal en append demanderait de savoir laquelle fait
foi. Les deux colonnes du déblocage viennent **après** `cause`, le seul champ de texte libre : un
lecteur d'avant #420 lit ses sept champs et ignore la suite. `merge.log` garde la sortie brute de
chaque appel. `status.sh` en rend une section (§11.5) — sans elle, un run occupé à drainer
ressemblerait à une panne : tout le plan traité, plus rien en vol, et pourtant le pilote tourne.

⚠ **Le coût des sessions de déblocage vit dans `merge.tsv`, pas dans `resume.tsv`.** Ce dernier a une
ligne **par ticket**, et tout ce qui le lit en dépend — le bilan de `status.sh`, la vue, et
`reprend_en_vol`, qui déduit d'une ligne absente qu'un ticket était en vol à la coupure. Une ligne de
plus au nom d'un ticket y ferait compter un traité de plus, et mentirait sur ce que la coupure a
interrompu.

**La reprise relit la file** (§11.8) : ce qui était mergé le reste, le reste revient en file. Sans ce
rechargement, les tickets livrés par le run coupé sortiraient du run par la porte de derrière — ils
sont « En revue », donc sautés au moment de les prendre, donc jamais inscrits.

**STOP arrête de lancer, pas de merger.** Un merge en cours va à son terme, et le drain final se joue
alors **sans attendre de pipeline** : qui demande l'arrêt n'attend pas un quart d'heure par PR. Ce
qui est déjà vert part quand même, le reste est nommé au bilan. Même règle pour le plafond du drain,
relu **entre** deux passes : il n'interrompt ni un merge ni une session de déblocage en cours —
couper une résolution de conflit au milieu n'économiserait que du temps de mur.

> **Tests.** [`tests/test_merge_automatique.py`](../tests/test_merge_automatique.py) garde les
> verbes (les quatre prérequis un par un, les quatre codes de `pipeline-wait`, l'ordre de
> `merge-order` sur le graphe de #299, le `deny` et son message) ;
> [`tests/test_orchestrate.py`](../tests/test_orchestrate.py) garde le pilote — l'entrée en file, le
> merge qui aboutit, la **sérialisation** (mesurée par une barrière et des relevés par écrivain,
> jamais par un `sleep` ni un compteur partagé — #292, puis #313), la seconde PR rejugée après le
> premier merge, le plafond de deux déblocages, et la reprise qui ne rejoue pas un merge fait.
