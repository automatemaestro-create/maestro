---
description: Rend une MR mergeable — résout son conflit avec origin/main, puis remet son pipeline au vert ; ne merge jamais
argument-hint: "[mr-iid | branche]  (défaut : la branche courante)"
allowed-tools: Bash(git:*), Bash(glab:*), Bash(bash:*)
---

Tu vas **rendre une MR mergeable**, une MR à la fois. Deux choses l'en empêchent, et cette commande
les traite **toutes les deux, dans cet ordre** :

1. un **conflit avec `origin/main`** — la MR a vieilli pendant que `main` avançait ;
2. un **pipeline rouge** — le projet exige un pipeline vert pour merger
   (`only_allow_merge_if_pipeline_succeeds`).

C'est une commande de **remédiation** : elle produit des commits intermédiaires (`Refs #<iid>`)
mais ne touche **jamais** au cycle de vie (statut, MR, merge) — la MR reste telle quelle, le merge
reste humain.

**L'ordre n'est pas arbitraire** : le merge d'`origin/main` peut *lui-même* casser le pipeline (deux
changements corrects séparément, faux ensemble). Diagnostiquer le pipeline avant de résoudre le
conflit, c'est diagnostiquer un état qui n'existera plus.

Cette commande est autosuffisante (réf. complète `docs/10-workflow-git.md` §8.3, à n'ouvrir qu'en
cas de doute). Les **garde-fous** priment sur l'automatisation : suis les étapes dans l'ordre et
**arrête-toi (en expliquant pourquoi)** dès qu'un contrôle échoue, plutôt que de forcer la suite.

1. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Si ça échoue, arrête-toi et
   demande à l'utilisateur de lancer `glab auth login`.

2. Détermine la **branche cible** :
   - si `$ARGUMENTS` est un IID de MR (`31`), lis sa branche source :
     `glab mr view <iid> --output json` → champ `source_branch` ;
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
   sur `CLAUDE.md` ou `docs/10-workflow-git.md` — ni par le `has_conflicts`/`detailed_merge_status`
   de GitLab, **asynchrone** (`checking`/`unchecked` sur 5 des 6 MR ouvertes mesurées le
   2026-08-07) : lis-le en complément s'il est là, ne l'attends jamais.

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
       sous une MR en Draft que personne ne relira ligne à ligne.
     - Résolu : `git add -A`, puis commit et push comme à l'étape 9 (message par **fichier**, pied
       `Refs #<iid>`, jamais `--no-verify`, jamais `--force`).
   - Vérifie que le verdict est retombé à `0`, puis continue — sans t'arrêter là : le merge que tu
     viens de faire peut avoir cassé le pipeline, et c'est précisément ce que l'étape suivante va
     voir.

5. **Diagnostic du pipeline** : `bash scripts/gitlab/lib.sh pipeline-latest <branche>` →
   `id / status / sha / url` (TSV). Depuis #165 la CI ne tourne **que sur les MR** : le pipeline
   d'une branche est celui de sa MR, et c'est le helper qui va le chercher là — ne le remplace pas
   par un `glab ci status`/`glab ci view <branche>`, qui ne voit que les pipelines de branche.
   Si l'étape 4 a poussé un commit de résolution, c'est le pipeline **de ce commit** qui fait foi :
   son `sha` doit être celui de `git rev-parse HEAD` (même contrôle qu'à l'étape 10).
   - `success` → plus rien à corriger côté pipeline : va au résumé (étape 12).
   - `created` / `pending` / `running` → le verdict n'est pas tombé. Un pipeline **`pending`
     durable** est le symptôme classique d'un **runner local hors ligne** (runners partagés coupés,
     #135) : ce n'est **pas** un problème de code (ne diagnostique pas les jobs). Assure d'abord le
     runner en ligne — **l'échec du helper n'interrompt pas la remédiation**, il est signalé (voir
     `docs/10-workflow-git.md` §8) :
     ```
     bash scripts/gitlab/ensure-runner.sh || echo "⚠ runner local non démarré — remédiation poursuivie"
     bash scripts/gitlab/clean-runner-containers.sh || echo "⚠ ménage des conteneurs CI incomplet — remédiation poursuivie"
     ```
     Le second ramasse les conteneurs de jobs laissés par un runner tué en cours de route (#166) —
     best-effort et silencieux quand il n'y a rien à faire. Puis suis le verdict avec
     `bash scripts/gitlab/lib.sh pipeline-wait <id>` et reprends selon le statut final.
   - `failed` → continue.
   - Aucun pipeline alors qu'un commit vient d'être poussé ? **Vérifie d'abord qu'une MR est
     ouverte** sur la branche : sans MR, il est normal qu'il n'y ait rien (la CI ne se déclenche
     plus au push — #165) et la suite est `/ticket-ship`, pas un déclenchement forcé. MR ouverte et
     toujours rien ? Déclenche manuellement : `glab ci run -b <branche>` (autorisé par les règles
     `workflow` ; cas observé sur la MR 31 : un push interrompu peut ne pas déclencher de
     pipeline), puis `pipeline-wait`.

6. **Jobs rouges** : `bash scripts/gitlab/lib.sh pipeline-failed-jobs <pipeline-id>` →
   `id / name / stage / failure_reason` par job en échec.

7. **Traces** : pour chaque job rouge, `bash scripts/gitlab/lib.sh job-trace <job-id> [lignes]`
   (défaut 100). **Synthétise les lignes d'erreur utiles** (fichier:ligne, règle violée, test en
   échec) — ne recopie jamais le log brut complet dans la conversation.

8. **Classe l'échec** — deux familles, deux conduites :
   - **Corrigeable en local** (`failure_reason: script_failure` avec des erreurs de lint, de
     test, de typage ou de script dans la trace) → étape 9.
   - **Non corrigeable en local** (`runner_system_failure`, `stuck_or_timeout_failure`, secret ou
     dépendance d'infrastructure manquante, échec manifestement flaky) → **dis-le explicitement**,
     propose un simple retry si l'échec semble transitoire (`glab ci retry <pipeline-id>` puis
     `pipeline-wait`), et **arrête-toi** — n'invente jamais un correctif de code pour un problème
     d'infrastructure.

9. **Correctif local** (au plus **2 tentatives** — au-delà, arrête-toi et rends la main avec ton
   diagnostic) :
   - applique le correctif **minimal** qui répond à l'erreur de la trace ;
   - **vérifie en local avec le même contrôle que le job rouge** (miroir de `.gitlab-ci.yml` ;
     Python passe par le venv du repo — `.venv/Scripts/python.exe` sous Windows, `.venv/bin/python`
     sous Unix) :
     | Job CI | Contrôle local |
     |---|---|
     | `shellcheck` | `shellcheck --severity=warning scripts/**/*.sh` (normaliser les fins de ligne CRLF avant, comme la CI qui checkout en LF) |
     | `python-lint` | `<venv-python> -m ruff check .` |
     | `pytest` | `<venv-python> -m pytest -n auto` (ou `… -m pytest tests/test_<suite>.py` pour reproduire le seul test rouge de la trace) |
     | `mypy` | `<venv-python> -m mypy maestro` |
   - committe en **commit intermédiaire** — pied **`Refs #<iid>`**, pas `Closes` (la MR porte déjà
     le `Closes`), hook `commit-msg` respecté, **jamais `--no-verify`**. Le message passe par un
     **fichier** : écris-le avec l'outil `Write` dans ton scratchpad de session — jamais un
     heredoc, jamais `-m "$(…)"`, la couche permissions découpant une commande sur ses sauts de
     ligne et ne matchant aucune substitution (#233). Puis :
     ```
     git commit -F <fichier>
     ```
   - pousse (jamais de `--force`) : `git push origin <branche>`. Si le push reste bloqué sur une
     demande d'identifiants, relance avec
     `GIT_TERMINAL_PROMPT=0 git -c credential.helper='' -c credential.helper='!glab auth git-credential' push origin <branche>`.

10. **Vérifie qu'un nouveau pipeline démarre** : `bash scripts/gitlab/lib.sh pipeline-latest
    <branche>` — son `sha` doit être celui de `git rev-parse HEAD`. Un push sur la branche source
    d'une MR **ouverte** relance bien la CI (#165) ; s'il ne démarre pas (~30 s), déclenche-le :
    `glab ci run -b <branche>`.

11. **Suis le verdict** : `bash scripts/gitlab/lib.sh pipeline-wait <nouveau-pipeline-id>`
    (statut final imprimé ; code 0 = success).
    - `success` → étape 12.
    - `failed` et tentatives < 2 → reboucle à l'étape 6 sur le nouveau pipeline.
    - sinon → arrête-toi : rends ton diagnostic et laisse la main à l'utilisateur.

12. **Résumé final — les deux blocages, séparément.** N'annonce **jamais** un ✅ global sur la foi
    du seul pipeline : une MR au pipeline vert mais en conflit reste non mergeable, et c'est
    exactement le faux verdict que cette commande existe pour supprimer.
    | Blocage | À rapporter |
    |---|---|
    | **Conflit** | ✅ aucun / ✅ résolu (fichiers + hash du commit de merge) / ❌ conflit laissé en place (fichiers + pourquoi la résolution n'était pas claire) |
    | **Pipeline** | ✅ vert / ❌ rouge (lien, jobs concernés, correctif appliqué le cas échéant, nombre de tentatives) |
    Conclus par l'**aptitude au merge** qui en découle — mergeable, ou ce qu'il reste à faire et par
    qui. Rappelle que la MR n'a pas été modifiée et que **le merge reste une décision humaine**.

N'exécute **aucune** action de cycle de vie : ni `glab mr merge`/`close`/`approve`/`update`, ni
`set-workflow`, ni création de MR (c'est le rôle de `/ticket-finish`). Jamais de force-push, jamais
de `--no-verify`, jamais de commit sur `main`. En cas de doute, abstiens-toi et demande.
