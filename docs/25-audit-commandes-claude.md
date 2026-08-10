# 25 — Audit des commandes Claude Code : économie de tokens et efficacité

> Ticket #304. Audit daté du **2026-08-07**, sur `origin/main` à `7a64a21`.
> **Ce document ne modifie aucune commande** : il mesure, localise et propose. Les corrections
> sont listées en §7 sous forme de tickets à créer.

## 1. Périmètre et méthode

Auditées : les **12 commandes** de [`.claude/commands/`](../.claude/commands/) et les **2 skills**
de [`.claude/skills/`](../.claude/skills/). [`CLAUDE.md`](../CLAUDE.md) et
[`docs/10-workflow-git.md`](./10-workflow-git.md) ne sont regardés que comme **sources de redite**
avec les commandes — mais CLAUDE.md se retrouve en tête des constats, parce qu'il est le seul
fichier du lot chargé **inconditionnellement, à chaque session**.

Trois mesures, toutes rejouables :

| Mesure | Méthode |
|---|---|
| Taille & tokens | octets réels ; tokens **estimés** par deux heuristiques indépendantes (`car/3,2` et `mots×1,75`), moyennées, fourchette donnée |
| Redite littérale | *shingles* de 8 mots partagés par ≥ 2 fichiers, puis couverture réelle en positions de mots |
| Coût d'exécution | taille réelle des charges utiles `glab` mesurée sur le projet (`wc -c`) |

⚠ **Les tokens sont des estimations**, pas des comptes de tokenizer : l'accès modèle du POC passe
par l'abonnement, sans clé API, donc sans endpoint `count_tokens`. Sur du Markdown français les deux
heuristiques s'écartent de ~10 % ; les ordres de grandeur sont fiables, pas les unités.

## 2. Le chiffre qui domine : le coût d'entrée d'une session

Avant d'écrire la première ligne de code, une session de ticket ordinaire a déjà chargé :

| Élément | Chargé | Tokens ~ |
|---|---|---|
| `CLAUDE.md` | **toujours** | **16 910** |
| `/ticket-start` | à l'invocation | 3 006 |
| `/ticket-ship` | à l'invocation | 2 340 |
| `/ticket-finish` (enchaîné par `ship`) | à l'invocation | 4 324 |
| **Total instructions d'un cycle** | | **≈ 26 600** |

**CLAUDE.md pèse 64 % de ce total**, et il est le seul poste que personne ne choisit de payer.
Les 12 commandes + 2 skills réunies pèsent 113 Ko / ~32 900 tokens, mais **à la demande** : une
session n'en charge jamais plus de deux ou trois.

Découpe de CLAUDE.md :

| Section | Octets | Tokens ~ | Part |
|---|---:|---:|---:|
| **Outillage requis** | 42 270 | **13 209** | **72,1 %** |
| Commandes disponibles | 9 299 | 2 906 | 15,9 % |
| Règles Git obligatoires | 2 671 | 835 | 4,6 % |
| Garde-fous | 1 869 | 584 | 3,2 % |
| Cycle de vie & labels | 1 607 | 502 | 2,7 % |
| (préambule) | 720 | 225 | 1,2 % |

Et à l'intérieur, **un seul paragraphe** — « Boucle d'orchestration autonome : `scripts/orchestrate/` » —
pèse **15 782 octets, ~4 930 tokens**. C'est plus que n'importe quelle commande du dépôt, y compris
`/orchestrate` elle-même (5 260 tokens). Il est payé par **toutes** les sessions, alors qu'il ne
sert qu'à celles qui lancent un run — c'est-à-dire presque aucune, un run étant piloté par un script
shell et non par une session.

> **Ordre de grandeur** : ~4 930 tokens × chaque session. Sur un run `/orchestrate` de 15 tickets,
> qui ouvre 15 sessions, c'est ~74 000 tokens dépensés à réexpliquer à chaque session le
> fonctionnement de la boucle qui vient de la lancer.

## 3. Tableau par fichier

| Fichier | Octets | Lignes | Tokens ~ | Redite littérale | Refs `#NNN` |
|---|---:|---:|---:|---:|---:|
| `orchestrate.md` | 17 641 | 254 | 5 260 | 0,7 % | 16 |
| `ticket-finish.md` | 14 943 | 214 | 4 324 | 15,3 % | 10 |
| `ticket-start.md` | 10 287 | 132 | 3 006 | 11,2 % | 5 |
| `ticket-create.md` | 8 432 | 117 | 2 393 | 7,5 % | 1 |
| `setup.md` | 8 270 | 114 | 2 383 | 0,7 % | 1 |
| `pipeline-fix.md` | 8 248 | 121 | 2 364 | **20,5 %** | 6 |
| `ticket-ship.md` | 8 221 | 114 | 2 340 | **28,6 %** | 3 |
| `milestone-presentation.md` | 6 379 | 99 | 1 808 | 3,5 % | 0 |
| `backlog.md` | 6 361 | 83 | 1 872 | 9,0 % | 1 |
| `branch-cleanup.md` | 5 855 | 88 | 1 743 | 12,1 % | 2 |
| `control-tower/SKILL.md` | 5 760 | 107 | 1 638 | 0 % | 7 |
| `verify/SKILL.md` | 5 382 | 111 | 1 586 | 0 % | 11 |
| `mr-review.md` | 4 172 | 64 | 1 195 | 17,2 % | 0 |
| `ticket-abandon.md` | 3 472 | 61 | 1 013 | 19,4 % | 0 |
| **Total** | **113 423** | **1 579** | **32 925** | **10,2 %** | 63 |

## 4. Constats — coût d'entrée (taille et redite)

### 4.1 — `/branch-cleanup` : ~43 000 tokens d'exécution pour extraire 40 mots ⚠ le plus gros gisement

L'étape 3 prescrit, **branche locale par branche locale** :

> trouve sa MR avec `glab mr view <branche> --output json` […] inspecte le champ `state` du JSON retourné

Mesuré sur ce dépôt : **41 branches locales** (soit 39 candidates, hors `main` et branche courante),
et `glab mr view <x> --output json` rend **3 502 octets**. Soit ~137 Ko, **~43 000 tokens**
réinjectés en contexte pour en tirer 39 fois un seul mot (`merged` / `opened` / `closed`).

C'est **25 fois le texte de la commande elle-même** (1 743 tokens), et cela se répète à chaque
`/branch-cleanup`. Deux remplacements existent **déjà dans le dépôt** :

- `bash scripts/gitlab/lib.sh mr-state <branche>` → un mot (7 octets au lieu de 3 502, **facteur 500**) ;
- mieux, `bash scripts/gitlab/lib.sh cleanup-merged` → fait **toute** la boucle en shell et n'imprime
  qu'une ligne de bilan. Son propre commentaire d'en-tête le dit :
  « *c'est le pendant non-interactif de `/branch-cleanup`* », avec le même garde-fou
  (suppression **seulement** si GitLab confirme `merged`).

La commande réimplémente donc en prose un helper qui existe — exactement ce que CLAUDE.md interdit
partout ailleurs (« le script en est la source unique »). Trois choses que `cleanup-merged` ne fait
**pas** et qu'il faudra garder autour de lui : basculer sur `main` quand la branche courante est
candidate, supprimer la branche **distante** restée là, et poser le cycle de vie « Terminé ».

### 4.2 — Redite littérale : 10,2 % du corpus, concentrée sur 4 fichiers

1 745 mots sur 17 045 sont **littéralement** dupliqués d'un fichier à l'autre (shingles de 8 mots —
c'est donc un **plancher**, la paraphrase n'est pas comptée).

| Fichier | Part littéralement dupliquée |
|---|---:|
| `ticket-ship.md` | **28,6 %** |
| `pipeline-fix.md` | **20,5 %** |
| `ticket-abandon.md` | 19,4 % |
| `mr-review.md` | 17,2 % |
| `ticket-finish.md` | 15,3 % |

Les passages, du plus long au plus court :

| Passage | Longueur | Fichiers porteurs |
|---|---:|---|
| Préambule « autosuffisante / réf. complète docs/10 / garde-fous priment » | 119 mots | 6 |
| Prélude « vérifie les pré-requis `lib.sh require` » | 66 mots | 8 |
| Étapes 1-2 « détermine l'IID depuis `$ARGUMENTS` ou la branche » | 57 mots | `ticket-finish` ↔ `ticket-ship` |
| Aller-retour description via helpers + avertissement mojibake #141 | 45 mots | `ticket-start` ↔ `ticket-ship` |
| Message de commit par **fichier** (#233) | 44 mots | `ticket-ship` ↔ `pipeline-fix` |
| Table des verdicts `close-guard` (0/3/4/5/1) | ~30 mots ×3 | `ticket-finish` ↔ `ticket-ship` |
| « retire les cinq autres `workflow::` — exclusion mutuelle Premium » | 31 mots | 4 |

Redites **conceptuelles** (formulations variables, même contenu), comptées sur 12 commandes :

| Notion | Fichiers |
|---|---:|
| Renvoi à `docs/10-workflow-git.md` | 11/12 |
| Prélude `lib.sh require` | 10/12 |
| « cette commande est autosuffisante » | 8/12 |
| « à n'ouvrir qu'en cas de doute » | 7/12 |
| « le merge reste une décision humaine » | 6/12 |
| Exclusion mutuelle des labels = Premium | 4/12 |
| Repli credential-helper `glab` (Windows) | 3/12 |
| Règle #233 (texte long → fichier) | 3/12 |
| Règle #141 (mojibake `glab \| python`) | 3/12 |

### 4.3 — La convention de découpage est écrite **trois fois**

`ticket-create.md` §4 (39 lignes), `ticket-start.md` §1 (les cas parent / sous-ticket / « trop
gros ? », ~35 lignes) et le bullet « Découpage en sous-tickets & tests différés » de CLAUDE.md
disent la même chose : seuil d'une session, parent de suivi, 1-3 critères, lots additifs, marqueur
`(parallèle)`, lot final « tests + doc ». ~1 100 tokens de redondance, dont ~450 payés à chaque
session via CLAUDE.md.

### 4.4 — `/orchestrate` : la commande et CLAUDE.md se paraphrasent

`orchestrate.md` (5 260 tk) et le paragraphe orchestration de CLAUDE.md (~4 930 tk) couvrent le même
terrain — pilote = script shell, `--detach`, reprise, garde-fous, journal, `status.sh`. La détection
littérale ne les rapproche pas (0,7 %) parce que la formulation diffère partout : c'est une redite
**sémantique**, la plus coûteuse et la moins visible.

## 5. Constats — efficacité d'exécution

### 5.1 — Charges utiles brutes là où un helper projette déjà

| Commande | Appel prescrit | Octets | Alternative existante | Octets |
|---|---|---:|---|---:|
| `ticket-abandon` §4 | `glab issue view <iid>` | 3 126 | `lib.sh issue-brief <iid>` | **802** |
| `ticket-abandon` §7 | `glab issue view <iid> --output json` | 5 737 | `lib.sh issue-owner` pour le label (le `state: closed` reste à lire, mais pas via le JSON complet) | **22** + 1 lecture ciblée |
| `ticket-start` §1 | `glab issue view $ARGUMENTS` | 3 126 | déjà rendu par `start-brief` (appelé à l'étape 1) | 0 |
| `branch-cleanup` §3 | `glab mr view --output json` **× 39** | 136 578 | `lib.sh cleanup-merged` | ~60 |

`ticket-start` §1 est le cas le plus net : la commande vient d'appeler `start-brief`, qui **imprime
déjà** le brief du ticket, puis rappelle un `glab issue view` complet pour juger de la taille.

### 5.2 — `scripts/ci/local.sh` n'est cité par **aucun** fichier `.claude/`

Le filet CI local (43 Ko, source unique depuis #214, connaît les 5 jobs de `.gitlab-ci.yml` et cadre
`pytest` sur le diff) est **absent** de `.claude/commands/` comme de `.claude/skills/`. Deux
commandes en réimplémentent une partie, et divergent :

- **`pipeline-fix.md` §8** porte sa propre table de miroirs locaux (`shellcheck`, `ruff`, `pytest -n
  auto`, `mypy`). Elle prescrit **`pytest -n auto`, la suite entière** — ce que CLAUDE.md interdit
  explicitement pendant le développement (#214 : ~10 min contre ~40 s en périmètre réduit).
  `bash scripts/ci/local.sh --only pytest` rendrait le même service sans la divergence.
- **`ticket-finish.md` §5** invente une heuristique de détection (« si un outil de lint/test est
  détecté dans le dossier concerné… ») là où le dépôt a un script qui sait déjà répondre.

### 5.3 — `ticket-finish.md` §5 décrit un dépôt qui n'existe plus

> « S'il n'y en a pas (**probable tant que le monorepo est un squelette sans code**), dis-le
> simplement et continue. »

Le dépôt porte aujourd'hui `maestro/` (12 sous-paquets), `apps/api`, `apps/web` et **54 suites de
tests**. L'instruction oriente l'agent vers la mauvaise conclusion à chaque clôture.

### 5.4 — Instructions bloquantes en mode autonome

**13 instructions** de type « demande confirmation / attends la décision » sont réparties sur 6
commandes, toutes exécutées **sans humain** par `/orchestrate` :

| Commande | Occurrences |
|---|---:|
| `ticket-finish` | 4 |
| `ticket-start` | 3 |
| `ticket-create`, `ticket-ship` | 2 chacune |
| `pipeline-fix`, `ticket-abandon` | 1 chacune |

`run.sh` compense au niveau de son prompt (« interdit d'attendre un résultat autant qu'une
validation », #178), mais la compensation est **externe** aux commandes : elle doit gagner contre
leur texte à chaque fois. Le cas le plus exposé est **`ticket-finish.md` §9.4** :

> Si la MR existait déjà et qu'elle est en Draft : **demande à l'utilisateur** si le travail est
> réellement terminé […]

Ce chemin est atteignable dans un run (deuxième `/ticket-ship` sur un ticket dont la MR existe
déjà), et #178 a établi qu'en mode `-p` une session qui croit attendre **termine le ticket** en
code 0 — indiscernable d'un succès. Aucune commande ne porte de branche « personne n'est là ».

### 5.5 — `allowed-tools:` est inerte, et faux

Les 12 commandes déclarent un `allowed-tools:`. CLAUDE.md note déjà qu'il **ne vaut pas
permission** (#199). Cette session a vérifié qu'il ne **restreint** pas davantage : `/ticket-create`
déclare `Bash(git:*), Bash(glab:*), Bash(bash:*), Read` — sans `Write` — et l'outil `Write` y a
fonctionné sans obstacle. Le champ est donc de la **documentation pure**, et elle est inexacte :

| Commande | Outil prescrit dans le corps | Déclaré ? |
|---|---|---|
| `ticket-finish` §4/§9.2 | `Write` | ✗ |
| `ticket-ship` §6 | `Write` | ✗ |
| `ticket-ship` §7 | `Skill` (invoque `/ticket-finish`) | ✗ |
| `pipeline-fix` §8 | `Write` | ✗ |
| `ticket-create` §9 | `Write` (fichier de corps) | ✗ |
| `milestone-presentation` §6 | `Write` (JSON) | ✗ |

Un champ inerte qui ment coûte peu en tokens (~40 par commande) mais **égare** : il a déjà fait
croire que `/ticket-start` autorisait `EnterWorktree`, alors que c'est `.claude/settings.json` qui
tranche (#199).

## 6. Ce qui est bien fait — à ne pas casser

- **#28 a tenu.** Aucune référence `@docs/…` ne subsiste : plus rien n'est inliné automatiquement.
  Le gisement de #28 est refermé, celui-ci est ailleurs.
- **`backlog.md` §2** documente sa propre économie : « cette projection réinjecte beaucoup moins que
  le JSON imbriqué », et laisse le JSON brut disponible en repli. **C'est le modèle à généraliser** —
  c'est exactement ce qui manque à `branch-cleanup` §3.
- **`mr-review.md` §5** refuse par défaut le diff détaillé (« gonfle inutilement le contexte ») et
  dérive un `--stat` local.
- **`pipeline-fix.md` §6** : « synthétise les lignes d'erreur utiles — ne recopie jamais le log brut
  complet ».
- **`setup.md` et `orchestrate.md`** sont les deux fichiers à ~0 % de redite littérale : ils
  délèguent réellement à leur script au lieu de le paraphraser.

## 7. Recommandations, classées par gain

**Mécaniques** — pas de perte d'information, exécutables sans arbitrage :

| # | Action | Gain estimé | Fichier |
|---|---|---:|---|
| M1 | `/branch-cleanup` §3+§5 : passer par `lib.sh cleanup-merged` (et `mr-state` pour le reste), en gardant les trois cas qu'il ne couvre pas | **~43 000 tk / invocation** | `branch-cleanup.md` |
| M2 | Remplacer `glab issue view` par `issue-brief`, et supprimer le `glab issue view` redondant de `ticket-start` §1 | ~2 300 tk / invocation | `ticket-abandon.md`, `ticket-start.md` |
| M3 | Renvoyer `pipeline-fix` §8 et `ticket-finish` §5 vers `scripts/ci/local.sh` au lieu de deux recettes maison | ~300 tk + supprime une divergence avec #214 | `pipeline-fix.md`, `ticket-finish.md` |
| M4 | Supprimer la phrase « monorepo est un squelette sans code » | correction factuelle | `ticket-finish.md` §5 |
| M5 | Corriger ou retirer les `allowed-tools:` | ~500 tk au total | 12 commandes |

> **M1 est traité** par **#309** :
> `/branch-cleanup` délègue sa boucle à `lib.sh cleanup-merged` et ne garde que les trois fonctions
> qu'il ne couvre pas ([docs/10 §9.5](./10-workflow-git.md)). Les mesures ci-dessus restent celles
> de l'instantané du 2026-08-07.

**Décision humaine** — il y a un arbitrage réel, la prose a une valeur que le token ne mesure pas :

| # | Question posée | Enjeu |
|---|---|---|
| H1 | Sortir de CLAUDE.md le paragraphe « Boucle d'orchestration autonome » (~4 930 tk) vers `docs/10 §11`, en ne gardant qu'un pointeur | **~4 930 tk × chaque session** (~74 k sur un run de 15 tickets). Contre : c'est là que sont consignés les « ne pas défaire ce choix » du chantier — hors contexte, une session peut les redécouvrir à ses frais |
| H2 | Même question pour la section « Outillage requis » entière (13 209 tk, 72 % de CLAUDE.md) | Le plus gros gisement du dépôt. Même contrepartie, à plus grande échelle |
| H3 | Factoriser les 7 passages littéraux de §4.2 — préambule, prélude `require`, verdicts `close-guard`, règles #233/#141 | ~1 700 tk sur le corpus, mais **casse l'autosuffisance** revendiquée par 8 commandes sur 12. Un fichier commun serait rechargé à chaque invocation : le gain n'existe que si la factorisation **supprime**, pas si elle déplace |
| H4 | Écrire la convention de découpage une seule fois (§4.3) | ~1 100 tk, dont ~450 par session |
| H5 | Donner aux commandes une branche « aucun humain présent » plutôt que de compenser dans le prompt de `run.sh` (§5.4) | Fiabilité des runs autonomes, pas des tokens. `ticket-finish` §9.4 est le cas à traiter en premier |

**Le fond de H1/H2/H3** : ce dépôt paie délibérément des tokens pour de la justification historique —
**144 références `#NNN`** dans le corpus audité, dont **81 dans le seul CLAUDE.md**. Ce n'est pas un défaut
d'écriture — c'est ce qui empêche qu'une session redéfasse une décision payée cher. L'audit chiffre
la facture ; il ne tranche pas. La seule chose qu'il recommande sans réserve, c'est de **ne pas
répartir cette facture sur les sessions qui n'en tirent rien** : une session qui corrige un
composant React n'a aucun usage des 4 930 tokens de la boucle d'orchestration.

## 8. Rejouer les mesures

Les trois scripts d'analyse (tailles/tokens, redites, densité) ont été écrits dans le scratchpad de
la session d'audit et ne sont pas versionnés — ils tiennent en une cinquantaine de lignes chacun.
Les commandes de vérification directe, elles, sont reproductibles telles quelles :

```bash
wc -c .claude/commands/*.md .claude/skills/*/SKILL.md CLAUDE.md   # tailles
grep -rn "@docs\|@CLAUDE" .claude/                                 # non-régression #28
git branch --format='%(refname:short)' | wc -l                     # ampleur du constat 4.1
glab mr view <iid> --output json | wc -c                           # charge utile d'une MR
bash scripts/gitlab/lib.sh issue-brief <iid> | wc -c               # projection équivalente
```
