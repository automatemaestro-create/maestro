---
description: Diagnostique le dernier pipeline en échec d'une MR ou d'une branche et corrige automatiquement quand c'est corrigeable en local — ne merge jamais
argument-hint: "[mr-iid | branche]  (défaut : la branche courante)"
allowed-tools: Bash(git:*), Bash(glab:*), Bash(bash:*)
---

Tu vas **remettre au vert** le pipeline d'une MR ou d'une branche : diagnostiquer le dernier
pipeline en échec (jobs rouges + traces), corriger **en local** quand l'échec est corrigeable,
committer/pousser le correctif et suivre le nouveau pipeline jusqu'à son verdict. C'est une
commande de **remédiation** : elle produit des commits intermédiaires (`Refs #<iid>`) mais ne
touche **jamais** au cycle de vie (statut, MR, merge) — la MR reste telle quelle, le merge reste
humain. D'autant plus utile que le projet exige un **pipeline vert pour merger**
(`only_allow_merge_if_pipeline_succeeds`) : un pipeline rouge bloque la MR.

Cette commande est autosuffisante (réf. complète `docs/10-workflow-git.md` §8, à n'ouvrir qu'en
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
   pied `Refs #<iid>` du commit de correctif. Introuvable ? Demande-le avant de continuer.

3. **Garde-fous avant toute action** :
   - **Arbre propre requis** : `git status --porcelain`. S'il y a des changements non commités
     qui ne viennent pas de cette commande, arrête-toi et demande quoi en faire — ne mélange
     jamais un correctif de pipeline avec du travail en cours.
   - Si la branche cible n'est pas la branche courante : `git checkout <branche>` puis
     `git pull origin <branche>` (jamais de `--force`).
   - **Si la branche cible est `main`** : diagnostic **seul** (étapes 4–6), jamais de commit
     automatique — on ne committe pas sur `main` ; propose d'ouvrir un ticket de bug à la place.

4. **Diagnostic du pipeline** : `bash scripts/gitlab/lib.sh pipeline-latest <branche>` →
   `id / status / sha / url` (TSV).
   - `success` → rien à corriger : dis-le et arrête-toi.
   - `created` / `pending` / `running` → le verdict n'est pas tombé : suis-le avec
     `bash scripts/gitlab/lib.sh pipeline-wait <id>` puis reprends selon le statut final.
   - `failed` → continue.
   - Aucun pipeline pour la branche alors qu'un commit vient d'être poussé ? Déclenche-le :
     `glab ci run -b <branche>` (cas observé sur la MR 31 : un push interrompu peut ne pas
     déclencher de pipeline), puis `pipeline-wait`.

5. **Jobs rouges** : `bash scripts/gitlab/lib.sh pipeline-failed-jobs <pipeline-id>` →
   `id / name / stage / failure_reason` par job en échec.

6. **Traces** : pour chaque job rouge, `bash scripts/gitlab/lib.sh job-trace <job-id> [lignes]`
   (défaut 100). **Synthétise les lignes d'erreur utiles** (fichier:ligne, règle violée, test en
   échec) — ne recopie jamais le log brut complet dans la conversation.

7. **Classe l'échec** — deux familles, deux conduites :
   - **Corrigeable en local** (`failure_reason: script_failure` avec des erreurs de lint, de
     test, de typage ou de script dans la trace) → étape 8.
   - **Non corrigeable en local** (`runner_system_failure`, `stuck_or_timeout_failure`, secret ou
     dépendance d'infrastructure manquante, échec manifestement flaky) → **dis-le explicitement**,
     propose un simple retry si l'échec semble transitoire (`glab ci retry <pipeline-id>` puis
     `pipeline-wait`), et **arrête-toi** — n'invente jamais un correctif de code pour un problème
     d'infrastructure.

8. **Correctif local** (au plus **2 tentatives** — au-delà, arrête-toi et rends la main avec ton
   diagnostic) :
   - applique le correctif **minimal** qui répond à l'erreur de la trace ;
   - **vérifie en local avec le même contrôle que le job rouge** (miroir de `.gitlab-ci.yml` ;
     Python passe par le venv du repo — `.venv/Scripts/python.exe` sous Windows, `.venv/bin/python`
     sous Unix) :
     | Job CI | Contrôle local |
     |---|---|
     | `shellcheck` | `shellcheck --severity=warning scripts/**/*.sh` (normaliser les fins de ligne CRLF avant, comme la CI qui checkout en LF) |
     | `python-lint` | `<venv-python> -m ruff check .` |
     | `pytest` | `<venv-python> -m pytest` |
     | `mypy` | `<venv-python> -m mypy maestro` |
   - committe en **commit intermédiaire** — pied **`Refs #<iid>`**, pas `Closes` (la MR porte déjà
     le `Closes`), hook `commit-msg` respecté, **jamais `--no-verify`** :
     ```
     git commit -F - <<'EOF'
     fix(<scope>): <description du correctif>

     Refs #<iid>
     EOF
     ```
   - pousse (jamais de `--force`) : `git push origin <branche>`. Si le push reste bloqué sur une
     demande d'identifiants, relance avec
     `GIT_TERMINAL_PROMPT=0 git -c credential.helper='' -c credential.helper='!glab auth git-credential' push origin <branche>`.

9. **Vérifie qu'un nouveau pipeline démarre** : `bash scripts/gitlab/lib.sh pipeline-latest
   <branche>` — son `sha` doit être celui de `git rev-parse HEAD`. S'il ne démarre pas (~30 s),
   déclenche-le : `glab ci run -b <branche>`.

10. **Suis le verdict** : `bash scripts/gitlab/lib.sh pipeline-wait <nouveau-pipeline-id>`
    (statut final imprimé ; code 0 = success).
    - `success` → étape 11.
    - `failed` et tentatives < 2 → reboucle à l'étape 5 sur le nouveau pipeline.
    - sinon → arrête-toi : rends ton diagnostic et laisse la main à l'utilisateur.

11. **Résumé final** : verdict (✅ vert / ❌ rouge), lien du pipeline, jobs concernés, correctif
    appliqué le cas échéant (hash court + en-tête du commit), nombre de tentatives. Rappelle que
    la MR n'a pas été modifiée et que **le merge reste une décision humaine**.

N'exécute **aucune** action de cycle de vie : ni `glab mr merge`/`close`/`approve`/`update`, ni
`set-status`, ni création de MR (c'est le rôle de `/ticket-finish`). Jamais de force-push, jamais
de `--no-verify`, jamais de commit sur `main`. En cas de doute, abstiens-toi et demande.
