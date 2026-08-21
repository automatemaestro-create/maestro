---
description: Rend une PR mergeable — résout son conflit avec origin/main, puis remet son pipeline au vert ; ne merge jamais
argument-hint: "[pr-numéro | branche]  (défaut : la branche courante)"
allowed-tools: Bash(git:*), Bash(gh:*), Bash(bash:*)
---

Tu vas **rendre une PR mergeable**, une PR à la fois. Deux choses l'en empêchent, et cette commande
les traite **toutes les deux, dans cet ordre** :

1. un **conflit avec `origin/main`** — la PR a vieilli pendant que `main` avançait ;
2. un **pipeline rouge** — le projet exige un pipeline vert pour merger (**protection de
   branche** avec `required_status_checks` ; `bash scripts/gitlab/lib.sh merge-settings` le rend
   sous le nom `pipeline_requis`).

C'est une commande de **remédiation** : elle produit des commits intermédiaires (`Refs #<iid>`)
mais ne touche **jamais** au cycle de vie (statut, PR, merge) — la PR reste telle quelle, le merge
reste humain.

**L'ordre n'est pas arbitraire** : le merge d'`origin/main` peut *lui-même* casser le pipeline (deux
changements corrects séparément, faux ensemble). Diagnostiquer le pipeline avant de résoudre le
conflit, c'est diagnostiquer un état qui n'existera plus.

Cette commande est autosuffisante (réf. complète `docs/10-workflow-git.md` §8.3, à n'ouvrir qu'en
cas de doute). Les **garde-fous** priment sur l'automatisation : suis les étapes dans l'ordre et
**arrête-toi (en expliquant pourquoi)** dès qu'un contrôle échoue, plutôt que de forcer la suite.

1. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Si ça échoue, arrête-toi et
   relaie son message : il nomme la commande d'authentification de la forge active (`gh auth login`,
).

2. Détermine la **branche cible** :
   - si `$ARGUMENTS` est un numéro de PR (`31`), lis sa branche source :
     `gh pr view <numéro> --json headRefName` (
     champ `source_branch`) ;
   - si `$ARGUMENTS` est un nom de branche, prends-le tel quel ;
   - sinon, la branche courante : `git branch --show-current`.
   Extrais l'**IID du ticket** du nom de branche (motif `<type>/<iid>-<slug>`) — il servira au
   pied `Refs #<iid>` des commits de correctif. Introuvable ? Demande-le avant de continuer.

3. **Garde-fous avant toute action** :
   - **Arbre propre requis** : `git status --porcelain`. S'il y a des changements non commités
     qui ne viennent pas de cette commande, arrête-toi et demande quoi en faire — ne mélange
     jamais une remédiation avec du travail en cours. C'est doublement vrai ici : l'étape 4 lance
     un `git merge`, qui refuserait de démarrer sur un arbre sale ou laisserait le doute sur ce
     qui a été résolu.
   - Si la branche cible n'est pas la branche courante : `git checkout <branche>` puis
     `git pull origin <branche>` (jamais de `--force`).
   - **Si la branche cible est `main`** : diagnostic **seul** (étapes 5–7), jamais de commit
     automatique — on ne committe pas sur `main`. Saute l'étape 4 (rien à merger dans elle-même)
     et propose d'ouvrir un ticket de bug à la place.

4. **Conflit avec `origin/main`** — le premier blocage :
   ```
   bash scripts/gitlab/lib.sh mr-conflict <branche> || echo "verdict=$? (3=conflit)"
   ```
   Le verdict vient de `git merge-tree --write-tree` : un **merge 3-way réel**, en lecture seule.
   Ne le remplace ni par `behind-main` — heuristique de fichiers, pessimiste, vraie presque partout
   sur `CLAUDE.md` ou `docs/10-workflow-git.md` — ni par le verdict de la forge, **asynchrone** :
   `mergeable`/`mergeStateStatus` sont calculés à la demande et rendus `UNKNOWN` tant que le calcul
   n'a pas abouti (mesure du 2026-08-07 côté GitLab : 5 des 6 MR ouvertes en `checking`/`unchecked`
   — le défaut n'était pas propre à une forge). Lis-le en complément s'il est là, ne l'attends
   jamais.

   - **`0`** → la branche se merge proprement : passe à l'étape 5.
   - **`1`** → verdict impossible (pas d'`origin/main`, histoires sans ancêtre commun). Signale-le
     et **poursuis** sur le pipeline : ce n'est pas un conflit, et l'absence de verdict ne doit pas
     bloquer la remédiation.
   - **`3`** → conflit. Résous-le **maintenant** :
     - `git merge origin/main` — **jamais `git rebase`** : réécrire une branche déjà poussée
       appellerait un force-push, barré en `deny` côté permissions (`docs/10-workflow-git.md` §6).
     - Résous chaque fichier. Une résolution de conflit est une décision de **contenu**, pas une
       opération textuelle : ne prends jamais un côté en bloc (`--ours`/`--theirs`) pour faire
       disparaître les marqueurs, et relis le résultat.
     - **Si le bon contenu n'est pas clair : `git merge --abort`**, branche laissée **intacte**,
       dis pourquoi et arrête-toi. Mieux vaut un conflit signalé qu'une résolution fausse poussée
       sous une PR en Draft que personne ne relira ligne à ligne.
     - Résolu : `git add -A`, puis commit et push comme à l'étape 9 (message par **fichier**, pied
       `Refs #<iid>`, jamais `--no-verify`, jamais `--force`).
   - Vérifie que le verdict est retombé à `0`, puis continue — sans t'arrêter là : le merge que tu
     viens de faire peut avoir cassé le pipeline, et c'est précisément ce que l'étape suivante va
     voir.

5. **Diagnostic du pipeline** : `bash scripts/gitlab/lib.sh pipeline-latest <branche>` →
   `id / status / sha / url` (TSV). Depuis #165 la CI ne tourne **que sur les PR** (`on:
   pull_request` côté GitHub) : le pipeline d'une branche est celui de sa PR, et c'est le helper qui
   va le chercher là. Ne le remplace par aucun appel direct : un run `pull_request` porte bien la
   branche source, donc `gh run list --branch` le **verrait** — ce qu'il ne rend pas, c'est un
   verdict comparable. Actions sépare `status` (en cours) de `conclusion` (issue) là où le
   vocabulaire de ces étapes n'a qu'un état, et c'est le helper qui les recompose en la valeur
   unique dont toutes les étapes suivantes dépendent (`success`/`failed`/`pending`…).
   Si l'étape 4 a poussé un commit de résolution, c'est le pipeline **de ce commit** qui fait foi :
   son `sha` doit être celui de `git rev-parse HEAD` (même contrôle qu'à l'étape 10).
   - `success` → plus rien à corriger côté pipeline : va au résumé (étape 12).
   - `created` / `pending` / `running` → le verdict n'est pas encore tombé : suis-le avec
     `bash scripts/gitlab/lib.sh pipeline-wait <id>` et reprends selon le statut final. Il n'y a
     **rien à allumer** en attendant — les exécutants sont fournis par la forge. Un `pending`
     durable était, du temps de la CI GitLab, le symptôme d'un runner de projet hors ligne, et
     appelait deux helpers avant tout diagnostic ; cet outillage est parti avec elle (#344,
     docs/10 §8), et avec lui la machine qu'il fallait laisser allumée.
   - `failed` → continue.
   - Aucun pipeline alors qu'un commit vient d'être poussé ? **Vérifie d'abord qu'une PR est
     ouverte** sur la branche : sans PR, il est normal qu'il n'y ait rien (la CI ne se déclenche
     plus au push — #165) et la suite est `/ticket-ship`, pas un déclenchement forcé. PR ouverte et
     toujours rien ? Déclenche manuellement (cas observé du temps de GitLab, sur la MR 31 : un
     push interrompu peut ne pas déclencher de pipeline), puis `pipeline-wait` :
     - `gh run rerun <run-id>` s'il existe une exécution à rejouer, sinon
       `gh workflow run ci.yml --ref <branche>` : le `workflow_dispatch` de
       [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) est conservé exactement pour ça.
       ⚠ Une exécution lancée par `workflow_dispatch` n'est **pas** rattachée à la PR : elle
       diagnostique, elle ne satisfait pas le contrôle requis par la protection de branche — c'est
       un nouveau push qui le fera.

6. **Jobs rouges** : `bash scripts/gitlab/lib.sh pipeline-failed-jobs <pipeline-id>` →
   `id / name / stage / failure_reason` par job en échec.

7. **Traces** : pour chaque job rouge, `bash scripts/gitlab/lib.sh job-trace <job-id> [lignes]`
   (défaut 100). **Synthétise les lignes d'erreur utiles** (fichier:ligne, règle violée, test en
   échec) — ne recopie jamais le log brut complet dans la conversation.

8. **Classe l'échec** — deux familles, deux conduites. La colonne `failure_reason` porte ici le
   **nom de l'étape** qui a échoué (Actions n'a pas de motif normalisé) : c'est la trace qui
   tranche, pas un code.
   - **Corrigeable en local** (erreurs de lint, de test, de typage ou de script dans la trace)
     → étape 9.
   - **Non corrigeable en local** (étape d'installation ou de checkout rouge, dépendance
     d'infrastructure ou secret manquant, expiration, échec manifestement flaky) → **dis-le
     explicitement**,
     propose un simple retry si l'échec semble transitoire (`gh run rerun --failed <run-id>`,
     puis `pipeline-wait`), et **arrête-toi** — n'invente
     jamais un correctif de code pour un problème d'infrastructure.

9. **Correctif local** (au plus **2 tentatives** — au-delà, arrête-toi et rends la main avec ton
   diagnostic) :
   - applique le correctif **minimal** qui répond à l'erreur de la trace ;
   - **rejoue le job rouge en local avec le filet CI**, `scripts/ci/local.sh` — **source unique**
     des contrôles locaux depuis #214 (`docs/10-workflow-git.md` §8.4). Ne réinvente pas ses
     commandes : il lit les jobs dans `.gitlab-ci.yml` (donc il suit le pipeline quand celui-ci
     change), passe par le venv du repo, analyse un miroir **LF** pour `shellcheck` (une copie
     Windows CRLF inventerait des SC1017 que la CI ne verra jamais) et écrit le journal de chaque
     job sous `.maestro/ci-local/<job>.log`, en chemin **relatif** — lisible depuis le worktree, y
     compris en session autonome (#234, §8.5) :
     ```
     bash scripts/ci/local.sh --only <job>
     ```
     `<job>` est le nom rendu par l'étape 6 (`shellcheck`, `python-lint`, `pytest`, `mypy`,
     `web-build` ; `lint` et `test` désignent l'étage entier). **Ne rejoue pas la suite entière**
     — ni `--complet`, ni un `pytest -n auto` à la main : le filet cadre `pytest` sur le périmètre
     du diff (~40 s au lieu de ~10 min), et c'est ici, en plein diagnostic d'un pipeline rouge, que
     la différence se paye. Pour reboucler sur le seul test rouge de la trace, vise-le directement :
     `<venv-python> -m pytest tests/test_<suite>.py` (`.venv/Scripts/python.exe` sous Windows,
     `.venv/bin/python` sous Unix). Le verdict complet, lui, reste celui du pipeline de la PR.
   - committe en **commit intermédiaire** — pied **`Refs #<iid>`**, pas `Closes` (la PR porte déjà
     le `Closes`), hook `commit-msg` respecté, **jamais `--no-verify`**. Le message passe par un
     **fichier** : écris-le avec l'outil `Write` dans ton scratchpad de session — jamais un
     heredoc, jamais `-m "$(…)"`, la couche permissions découpant une commande sur ses sauts de
     ligne et ne matchant aucune substitution (#233). Puis :
     ```
     git commit -F <fichier>
     ```
   - pousse (jamais de `--force`) : `git push origin <branche>`. Si le push reste bloqué sur une
     demande d'identifiants, relance avec `gh` en credential helper
     (`!gh auth git-credential`) :
     `GIT_TERMINAL_PROMPT=0 git -c credential.helper='' -c credential.helper='!gh auth git-credential' push origin <branche>`.

10. **Vérifie qu'un nouveau pipeline démarre** : `bash scripts/gitlab/lib.sh pipeline-latest
    <branche>` — son `sha` doit être celui de `git rev-parse HEAD`. Un push sur la branche source
    d'une PR **ouverte** relance bien la CI (#165 ; `synchronize` côté GitHub) ; s'il ne démarre pas
    (~30 s), déclenche-le comme à l'étape 5.

11. **Suis le verdict** : `bash scripts/gitlab/lib.sh pipeline-wait <nouveau-pipeline-id>`
    (statut final imprimé ; code 0 = success).
    - `success` → étape 12.
    - `failed` et tentatives < 2 → reboucle à l'étape 6 sur le nouveau pipeline.
    - sinon → arrête-toi : rends ton diagnostic et laisse la main à l'utilisateur.

12. **Résumé final — les deux blocages, séparément.** N'annonce **jamais** un ✅ global sur la foi
    du seul pipeline : une PR au pipeline vert mais en conflit reste non mergeable, et c'est
    exactement le faux verdict que cette commande existe pour supprimer.
    | Blocage | À rapporter |
    |---|---|
    | **Conflit** | ✅ aucun / ✅ résolu (fichiers + hash du commit de merge) / ❌ conflit laissé en place (fichiers + pourquoi la résolution n'était pas claire) |
    | **Pipeline** | ✅ vert / ❌ rouge (lien, jobs concernés, correctif appliqué le cas échéant, nombre de tentatives) |
    Conclus par l'**aptitude au merge** qui en découle — mergeable, ou ce qu'il reste à faire et par
    qui. Rappelle que la PR n'a pas été modifiée et que **le merge reste une décision humaine**.

N'exécute **aucune** action de cycle de vie : ni `gh pr merge`/`close`/`review`/`edit`, ni
`set-workflow`, ni création de PR (c'est le rôle de `/ticket-finish`). `merge` et `close` sont
d'ailleurs en **`deny`** — la règle n'est pas une politesse.
Jamais de force-push, jamais de `--no-verify`, jamais de commit sur `main`. En cas de doute,
abstiens-toi et demande.
