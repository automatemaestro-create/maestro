# 27 — Migration GitLab → GitHub : note de décision

> ## ⚠ Verdict renversé — la migration a eu lieu
>
> **Cette note recommandait de NE PAS migrer les tickets (§7). L'utilisateur a tranché dans
> l'autre sens le 2026-08-14**, et le chantier #335 l'a exécutée : les tickets vivent sur
> **GitHub** ([`automatemaestro-create/maestro`](https://github.com/automatemaestro-create/maestro)),
> et le projet GitLab est **archivé en lecture seule** depuis le **2026-08-17** (#343).
>
> **Ce qui reste valable ici, ce sont les MESURES** — coût CI, volume de time tracking, nombre
> d'invocations `glab`, contrainte de numérotation : elles ont borné le chantier et sont
> reprises telles quelles par #335. **Ce qui est périmé, ce sont les conclusions** : §7
> (« non à la migration ») et la « prochaine étape recommandée » qui en découle.
>
> Ne pas relire §7 comme une consigne. Pour l'état d'aujourd'hui : **§11, le rôle d'archive**.

> Ticket #331. Décision datée du **2026-08-14**, sur `origin/main` à `1b30bb1`.
> Suite de #332 (miroir + CI Actions en double), qui a mesuré la traduction des jobs et laissé
> **trois questions ouvertes** — toutes tranchées ici, §2 et §3.
>
> **Verdict d'alors : non à la migration des tickets. Oui à instruire le basculement de la CI
> seule**, qui est une autre question, plus petite, et qui penche nettement (§7).

## 1. La question, et pourquoi elle se découpe

Trois moteurs, dans l'ordre donné par l'utilisateur : la **CI**, l'**intégration Claude Code**,
et **plus tard la visibilité**.

Le cadrage initial les traitait comme un bloc — « faut-il migrer ? » — parce que les minutes
Actions ne sont illimitées qu'en dépôt public, ce qui semblait river le moteur CI au moteur
visibilité. La mesure défait ce nœud : **le dépassement du quota gratuit coûte ~9 $/mois** (§2),
pas un passage en public. Les trois moteurs redeviennent donc indépendants, et c'est le résultat
le plus structurant de cette note :

| Moteur | Exige de migrer… | Verdict |
|---|---|---|
| CI | `.gitlab-ci.yml` — **1 fichier** | penche vers GitHub (§7) |
| Intégration Claude | les **MR** | non tranché, gain réel mais non chiffrable |
| Visibilité | tout, + audit d'historique | prématuré |

**Rien n'oblige à déplacer les tickets pour régler la CI.** Le couplage supposé était un couplage
de tarification, et il ne tient pas.

## 2. Le coût de la CI, mesuré deux fois

### 2.1 Méthode — et pourquoi pas la mesure directe

#332 a fait tourner les deux CI sur le même code, mais son chiffre de consommation ne pouvait pas
servir tel quel : le miroir ne crée pas de PR, donc son workflow part sur `push` et tourne **plus
souvent** que la CI GitLab, limitée aux `merge_request_event` depuis #165. Il mesurait un régime
qui n'existera pas.

D'où deux mesures indépendantes, l'une reconstruite, l'autre directe :

| # | Méthode | Ce qu'elle vaut |
|---|---|---|
| **A** | Les **123 pipelines GitLab réels** du régime « MR uniquement » (depuis le 2026-08-03), leurs durées job par job, auxquelles on applique la règle de facturation GitHub | le bon régime, la mauvaise machine |
| **B** | Les **runs GitHub réels** du miroir, horodatages `started_at`/`completed_at` par job | la bonne machine, le mauvais régime |

Règle de facturation appliquée dans les deux cas : GitHub arrondit **chaque job** à la minute
supérieure et facture le **temps cumulé**, jamais le temps de mur — les jobs parallèles coûtent
chacun leur minute.

### 2.2 Les deux mesures convergent

**Méthode A** — 123 pipelines, 542 jobs :

| Job | Fréquence | Durée moyenne |
|---|---|---|
| `shellcheck` | 100 % | 49 s |
| `python-lint` | 100 % | 36 s |
| `mypy` | 99 % | 148 s |
| `pytest` | 99 % | 196 s |
| `web-build` | **41 %** | 159 s |

- temps de job réel : **8,17 min/pipeline**
- facturé GitHub : **10,15 min/pipeline** (+24 % d'arrondi)

**Méthode B** — run vert `1b30bb1` du miroir, 6 jobs : 7 s, 11 s, 14 s, 42 s, 110 s, 198 s →
arrondis à 1+1+1+1+2+4 = **10 min facturées**.

**10,15 contre 10.** Les deux méthodes tombent au même endroit, par des chemins qui ne partagent
aucune donnée — et elles se compensent : les jobs GitHub sont individuellement ~2× plus rapides
(§2.4), mais il y en a 6 au lieu de 4,4, dont le portier `perimetre` qui facture une minute pleine
pour 7 secondes.

### 2.3 Le multiplicateur, et la facture

Cadence réelle depuis le passage au régime « MR uniquement » (2026-08-03) : **123 pipelines en
12 jours**, soit ~10,3/jour → **~308/mois**. La variance hebdomadaire est forte (S32 : 13,4/jour ;
S33 : 5,8/jour), les runs `/orchestrate` créant des rafales — fourchette honnête : **250-350/mois**.

| | minutes/mois |
|---|---|
| consommation estimée (308 × 10,15) | **~3 130** |
| quota gratuit, dépôt **privé** | 2 000 |
| dépassement | ~1 130 |
| **coût, à 0,008 $/min (Linux 2 cœurs)** | **~9 $/mois** |
| coût en dépôt **public** | **0** (illimité) |

⚠ Les tarifs (2 000 min, 0,008 $/min) sont ceux **publiés par GitHub**, pas une mesure : le
compte du miroir n'expose pas son API de facturation (403 sur `settings/billing/actions` avec un
PAT à portée restreinte). Le quota est par **compte**, partagé entre tous ses dépôts privés.

### 2.4 Ce que la mesure dit du runner

L'estimation de départ — « `pytest` risque de décevoir sur un runner hébergé à 2 vCPU » — reste
démentie, et l'écart va dans l'autre sens sur le reste : `shellcheck` 28→15 s, `python-lint`
25→7 s, `mypy` 89→37 s. Le runner de l'équipe est une machine qui fait autre chose en même temps.

## 3. Les trois questions laissées ouvertes par #332

### 3.1 `billable = 0` — un champ non alimenté, pas une gratuité

`GET /actions/runs/:id/timing` rend `billable.UBUNTU.total_ms = 0` **et** `duration_ms = 0` pour
chacun des 6 jobs, alors que le même objet rend `run_duration_ms: 202000` correctement, que
`jobs: 6` est juste, et que l'API `/jobs` du même run donne des horodatages complets totalisant
382 s.

**Le champ n'est pas renseigné ; il ne rapporte pas une gratuité.** Le dépôt est privé
(`visibility=private`, vérifié), donc ses minutes sont décomptées. La conséquence pratique est
qu'il ne faut pas chercher le coût dans `/timing` : la source utilisable est
`started_at`/`completed_at` par job — c'est ce qu'utilise la méthode B, et elle concorde avec A.

Cette question était réputée « changer l'ordre de grandeur du coût ». **Elle ne le change pas** :
elle était un artefact d'API.

### 3.2 Le multiplicateur — ~308 pipelines/mois

Répondu en §2.3. Un point de méthode mérite d'être retenu : la moyenne sur tout l'historique
(477 pipelines, 367/mois) **surestime** le régime actuel, parce qu'elle englobe l'époque où chaque
push de branche et chaque merge sur `main` déclenchaient un pipeline. Le basculement est net et
daté — depuis la semaine 32, **100 % des pipelines sont des `merge_request_event`** :

| Semaine | `main` | branche | MR |
|---|---|---|---|
| S28 | 49 | 54 | 4 |
| S30 | 26 | 31 | 9 |
| S31 | 17 | 20 | 48 |
| **S32** | 0 | 0 | **94** |
| **S33** | 0 | 0 | **29** |

### 3.3 Le surcoût des pipelines rouges — l'objection s'effondre

C'est la question qui appelait « un échantillon réel plutôt qu'un cas fabriqué », et le réel
contredit le fabriqué.

#332 mesurait qu'un pipeline rouge coûte ~25 s côté GitLab (l'étage `test` ne démarre pas quand
`lint` échoue) contre ~10 min côté GitHub (pas d'étages, six jobs partis en parallèle, tous
payés) — un facteur 24. Mais ce cas avait été **provoqué par une erreur `ruff` volontaire**,
c'est-à-dire dans l'étage `lint`, le seul endroit où le gating protège.

Sur les 7 pipelines rouges réels de l'échantillon :

| Job en échec | Étage | Nombre |
|---|---|---|
| `pytest` | test | 5 |
| `web-build` | test | 1 |
| `python-lint` | **lint** | **1** |

**6 échecs sur 7 tombent dans l'étage `test`**, où `lint` est déjà passé et déjà payé. Un seul
pipeline de l'échantillon a vu des jobs `skipped` — exactement le cas fabriqué. Les rouges réels
coûtent **10,4 min** de temps de job côté GitLab (contre 8,0 pour les verts : ils sont plus chers,
pas moins, `pytest` tournant plus longtemps quand il échoue).

Le surcoût GitHub sur les rouges est donc **marginal**, et l'ajout de `needs:` — proposé comme
remède — n'aurait porté que sur 1 pipeline sur 123.

## 4. Ce que la bascule supprimerait

**1 146 lignes** d'outillage de runner, et la fragilité qui va avec :

| Fichier | Lignes |
|---|---|
| `scripts/gitlab/setup-runner.sh` | 545 |
| `scripts/gitlab/ensure-runner.sh` | 302 |
| `scripts/gitlab/clean-runner-containers.sh` | 299 |

Plus la contrainte qui les a fait naître : les runners partagés GitLab sont coupés (#135), un
pipeline vert conditionne le merge, donc **une machine de l'équipe doit rester allumée** en
permanence, et son extinction bloque tout le monde. C'est le vrai coût de GitLab aujourd'hui — et
il ne se lit pas sur une facture.

## 5. Ce qu'elle casserait sans équivalent

Trois mécanismes ont été mis en cause. Ils ne pèsent pas le même poids, et la mesure les sépare
nettement.

| Mécanisme | Usage réel | Équivalent GitHub |
|---|---|---|
| **Time tracking** | **263/330 tickets (80 %)**, 603 h cumulées | **aucun** |
| **Dates début/échéance** | **263/330 tickets (80 %)** | champs Projects v2 seulement |
| Lecture des variables CI (`env-pull.sh`) | **0 variable publiée** | secrets *write-only* |
| Labels `workflow::*` | 330/330, exactement 1 par ticket | natif |
| Milestones | 13 | natif |

**Le time tracking et les dates sont le vrai coût** : utilisés sur 80 % des tickets, et GitHub
n'offre rien pour le premier. `/ticket-finish` estime et loggue le temps automatiquement à chaque
clôture — la fonctionnalité disparaîtrait, pas seulement l'historique.

**`env-pull.sh`, en revanche, était une fausse objection.** Le magasin de variables CI/CD du projet
est **vide** (`GET /projects/:id/variables` → HTTP 200, `[]`) : les 7 clés partagées renseignées
dans les `.env` y sont arrivées autrement. Le mécanisme est écrit, testé
([`tests/test_env_pull.py`](../tests/test_env_pull.py)) et documenté, mais **il ne distribue aucun
secret aujourd'hui**. La perte serait potentielle, pas actuelle — et pour les clés non secrètes
(`MAESTRO_PROVIDER`, `MAESTRO_MODEL`, `OPENAI_BASE_URL`), les **variables** Actions sont lisibles
par API et conviendraient ; seuls les secrets vrais resteraient sans domicile.

Bon à savoir sur le point positif : les **labels `workflow::*` ne portent aucune dérive** — les
330 tickets en ont exactement un, jamais zéro ni deux. L'exclusion mutuelle tenue à la main
(`set-workflow`) fonctionne, et ce dispositif se transposerait tel quel.

## 6. La numérotation — tranchée : import, pas repartir de zéro

GitHub partage **une seule séquence** entre issues et PR. L'état à importer :

- **330 tickets**, **271 MR** → 601 objets pour une séquence unique
- **270 commits** de `origin/main` (sur 548) portent un `Refs #<n>` ou `Closes #<n>`
- ces références couvrent la plage **#2 à #333**

L'option « repartir de zéro » (25 tickets ouverts seulement) est **écartée**, et pas pour son coût
de ressaisie — pour ce qu'elle fait à l'historique. Les 270 commits ne deviendraient pas des liens
morts : ils deviendraient des liens **faux**. Sur GitHub, `Refs #123` dans un message de commit est
rendu comme un lien vers l'objet numéro 123 du dépôt — qui serait alors une PR ou un ticket sans
aucun rapport. Un lien mort se repère ; un lien plausible et faux, non. C'est le pire des deux
mondes, et il est irréversible une fois l'historique poussé.

L'import « tickets d'abord, dans l'ordre, trous comblés » préserve `#2`…`#333` à condition que le
dépôt cible n'ait consommé **aucun numéro**. Le miroir ne crée ni ticket ni PR (il ne réplique que
branches et tags) et le dépôt a un jour d'existence, donc la condition tient aujourd'hui — mais
elle n'a **pas pu être vérifiée par API** : le PAT du compte propriétaire est à portée restreinte
et rend 403 sur `/issues` comme sur `/pulls`. À revérifier avant tout import, c'est un prérequis
dur et à sens unique.

## 7. Décision

**Non à la migration des tickets, maintenant.** Le coût est concentré exactement là où GitHub
n'offre rien (time tracking et dates, 80 % des tickets), la numérotation impose un import à sens
unique dont le prérequis n'est pas vérifié, et les 320 invocations `glab` à réécrire (§8) servent
les tickets et les MR — pas la CI.

**Mais le moteur CI, lui, ne demande pas cette migration**, et c'est ce que la mesure a changé :

- il coûterait **~9 $/mois** (§2.3), pas un passage en dépôt public ;
- il supprimerait **1 146 lignes** d'outillage et la machine toujours allumée (§4) ;
- il tourne sur des runners **~2× plus rapides** (§2.4) ;
- et il ne touche **qu'un seul fichier** du dépôt, `.gitlab-ci.yml`.

Ce qui reste à lever est un point précis, à tester et non à raisonner : GitLab conditionne le merge
à un pipeline vert (`only_allow_merge_if_pipeline_succeeds`), réglage qui regarde **ses** pipelines.
Si une Action peut reposter son verdict via l'API de statuts de commit GitLab
(`POST /projects/:id/statuses/:sha`) et que ce statut satisfait le garde-fou, alors la CI bascule
sans que rien d'autre bouge : tickets, MR, time tracking, dates et `glab` restent en place.

**Prochaine étape recommandée** — une expérience du même format que #332, réversible et bornée :
vérifier que le statut reposté satisfait le garde-fou de merge sur **une MR de test**. Si oui, le
basculement CI devient un ticket d'infrastructure ordinaire. Si non, on reste sur le runner
partagé et la question CI se referme avec la migration.

## 8. Coût de réécriture, s'il fallait quand même migrer

**320 invocations `glab <sous-commande>` dans 42 fichiers** (508 occurrences du mot dans
57 fichiers, prose comprise) :

| Famille | Invocations | Fichiers |
|---|---|---|
| `scripts/` | 150 | 16 |
| `.claude/` | 67 | 14 |
| `docs/` | 43 | 3 |
| `tests/` | 34 | 5 |
| `CLAUDE.md` | 19 | 1 |
| autres (`CONTRIBUTING.md`, `.gitlab-ci.yml`, `.env.example`) | 7 | 3 |

Deux choses à en retenir.

**Le produit n'est pas concerné** : `maestro/` et `apps/` comptent **zéro** invocation. Le couplage
est entièrement dans l'outillage, la documentation et les prompts.

**Mais la doc et les prompts sont la moitié du travail** — 129 invocations sur 320 vivent dans
`.claude/`, `docs/` et `CLAUDE.md`, plus 22 règles de permission dans
[`.claude/settings.json`](../.claude/settings.json). La couche d'abstraction `lib.sh` existe (71
occurrences y sont concentrées) mais elle fuit : une commande `/mr-fix` ou `/mr-review` décrit `glab`
en toutes lettres, et c'est cette prose qui *est* la spécification. Réécrire `lib.sh` ne suffirait
pas.

## 9. Ce qui rouvrirait la question

Un seul de ces éléments suffit :

1. **Le passage en dépôt public devient un objectif** — la CI devient gratuite et illimitée, et le
   moteur visibilité paie le moteur CI. Prérequis inchangés : audit de l'historique pour les
   secrets, et arbitrage sur la publication de 330 tickets rédigés en français pour un usage
   interne.
2. **GitHub acquiert un suivi du temps passé** sur les issues, ou l'estimation automatique de
   `/ticket-finish` cesse d'être jugée utile. C'est la perte la plus nette (80 % des tickets,
   603 h), et elle est structurelle, pas historique.
3. **La cadence de CI double durablement** (~600 pipelines/mois) — le dépassement approcherait
   ~40 $/mois et l'arbitrage économique changerait de camp.
4. **L'expérience de §7 échoue** et la CI ne peut pas basculer seule : le moteur CI redevient alors
   solidaire de la migration complète, et c'est la migration qu'il faut réexaminer, avec ce
   document comme point de départ.

## 10. Action indépendante de la décision

Le miroir et sa CI en double **continuent de tourner** et consomment des minutes réelles : 12 runs
observés entre le 2026-08-13 07:09 et le 2026-08-14 08:58 (~26 h), à ~10 min facturées l'unité.
À ce rythme la CI en double dépasserait **à elle seule** le quota gratuit du compte — pour un
verdict que personne ne lit, puisqu'elle ne conditionne aucun merge.

⚠ Cette fenêtre est courte et contient le trafic **délibéré** de l'expérience #332 (commits poussés
pour comparer les deux CI) ; elle majore donc le rythme de croisière. Ce qui ne dépend pas de la
fenêtre, en revanche : le workflow se déclenche sur `push`, et le miroir réplique **toutes** les
branches — il tournera donc plus souvent que la CI GitLab, limitée aux MR, aussi longtemps qu'il
restera en l'état.

Elle a produit ce qu'on lui demandait (les mesures de #332 et de cette note) et un résultat
inattendu qui valait le détour : les 16 tests d'outillage que la CI GitLab n'a jamais joués, traités
depuis par #333. **Restreindre ses déclencheurs ou l'éteindre est à décider maintenant**, que la
migration se fasse ou non — et c'est un geste réversible dans les deux sens.

---

## 11. L'archive GitLab — ce qu'on y trouve encore, ce qu'on n'y trouve plus

> Écrit à la bascule (#343, lot 8 de #335), le **2026-08-17**. C'est la section à lire pour savoir
> où chercher quelque chose d'avant la migration.

Le projet [`maestro-group4345327/maestro`](https://gitlab.com/maestro-group4345327/maestro) est
**archivé** : lecture seule, au sens de GitLab — on peut tout consulter, rien écrire. Ni ticket, ni
commentaire, ni MR, ni pipeline. Ce n'est pas un réglage de permissions qu'on pourrait contourner
avec les bons droits : le projet entier refuse les écritures.

### Ce qu'on y trouve encore — et nulle part ailleurs

| Ce qui reste sur GitLab | Volume | Pourquoi ça n'a pas suivi |
|---|---|---|
| **Les Merge Requests**, avec leurs diffs, fils de discussion, approbations et verdicts de pipeline | **281** (280 mergées) | Rejouer une MR demande sa branche d'origine, supprimée au merge (squash). Sans elle, une PR GitHub n'aurait ni diff ni contexte — on aurait recréé des coquilles vides (§3 du parent #335) |
| **Le time tracking natif** — `/spend`, relevés horodatés, totaux par ticket | **629 h** sur **273** tickets | GitHub n'a **aucun** équivalent natif (§5). L'historique a été **importé** sous forme maison (commentaire `maestro:suivi:v1`), mais les relevés natifs, eux, restent ici |
| **Les dates de début et d'échéance** natives | **273** tickets | Même raison : importées en commentaire, l'objet natif reste ici |
| **L'historique des pipelines GitLab CI** et leurs traces de jobs | ~3 ans de runs | Non transférable : les logs appartiennent à l'instance |
| **Les notes système** — « added ~label », « mentioned in issue #328 », changements d'assigné | ~7 300 | Journal d'activité de l'outil qu'on quitte, délibérément non repris (§ « ce qui n'est pas rejoué » de `scripts/migration/import-github.sh`). Les **156 commentaires humains**, eux, ont tous suivi |

### Ce qu'on n'y trouve plus

- **Le backlog vivant.** Les tickets sont sur GitHub, plage **`#1` → `#356`** préservée au numéro
  près — un `Refs #123` de l'historique git y pointe vers le bon objet. Les 4 iid supprimés côté
  GitLab (19, 20, 201, 241) y sont des **bouche-trous** fermés, étiquetés `import::bouche-trou`.
  Les tickets GitLab correspondants existent toujours, mais **ils ne bougeront plus** : c'est la
  copie GitHub qui est à jour.
- **La CI.** GitHub Actions est en autorité depuis #338 et conditionne le merge. `.gitlab-ci.yml`
  et les 1 146 lignes d'outillage de runner partent avec #344.
- **Le miroir.** Il poussait GitLab → GitHub ; il a été arrêté **avant** la bascule d'`origin`,
  sans quoi il aurait continué d'écraser des branches sur le dépôt devenu source de vérité.
- **Le workflow.** `MAESTRO_FORGE` vaut **`github`** sans qu'on la pose.

### Comment on relit l'archive — et pourquoi pas avec `lib.sh`

Par l'**UI web** de GitLab, ou en ligne de commande avec un `--repo` **explicite** :

```bash
glab mr list   --repo maestro-group4345327/maestro
glab issue view 218 --repo maestro-group4345327/maestro
```

Le `--repo` est **obligatoire** : `glab` déduit normalement le projet des remotes du dépôt, et
`origin` pointe désormais sur github.com — sans lui, il répond « None of the git remotes configured
for this repository point to a known GitLab host ».

C'est aussi pourquoi `MAESTRO_FORGE=gitlab bash scripts/gitlab/lib.sh <verbe>` **ne relit pas
l'archive**, contrairement à ce qu'on pourrait attendre du commutateur : ses verbes passent par ces
mêmes sous-commandes, sans `--repo`. Le rendre capable de lire l'archive demanderait de propager ce
drapeau dans une douzaine de verbes dont la moitié sont des **écritures** — refusées de toute façon
par un projet archivé — et que **#344 supprime**. On ne l'a donc pas fait : le commutateur `gitlab`
ne sert plus qu'aux **suites de tests**, qui montent un `glab` factice dans un dépôt jetable où
aucun remote réel n'intervient.

### Où regarde-t-on, en pratique

| Question | Où |
|---|---|
| « Comment cette fonctionnalité a-t-elle été revue ? » (avant le 2026-08-17) | GitLab, la MR |
| « Combien de temps a coûté #218 ? » | GitHub — commentaire `maestro:suivi:v1` du ticket. GitLab pour le relevé natif d'origine |
| « De quoi parle #123 ? » | GitHub, `#123` |
| N'importe quoi après le 2026-08-17 | GitHub, toujours |
