---
description: Commit automatique + /ticket-finish enchaînés — clôture le ticket courant sans blocage manuel
argument-hint: "[issue-iid] (optionnel si le nom de la branche courante le contient déjà)"
allowed-tools: Bash(git:*), Bash(glab:*), Bash(bash:*)
---

Tu vas clôturer le ticket courant **en une seule action** : committer les changements en attente
(message généré, sans confirmation) puis enchaîner **`/ticket-finish`** (push + MR + statut
« En revue » + log du temps). C'est le pendant « zéro friction » de `/ticket-finish`, pensé pour
la boucle d'orchestration : là où `/ticket-finish` suppose un commit déjà fait et demande
confirmation avant d'en créer un, `/ticket-ship` **commite d'office** ce qui est en attente puis
délègue la suite à `/ticket-finish`.

Cette commande est autosuffisante (réf. complète `docs/10-workflow-git.md`, à n'ouvrir qu'en cas de
doute). Les **garde-fous** priment sur l'automatisation : suis les étapes dans l'ordre et
**arrête-toi (en expliquant pourquoi)** dès qu'un contrôle échoue, plutôt que de forcer la suite.

1. Détermine l'IID du ticket : utilise `$ARGUMENTS` s'il est fourni, sinon extrais-le du nom de la
   branche courante (`git branch --show-current`, motif `<type>/<iid>-<slug>`). Si aucun IID ne
   peut être déterminé, demande-le à l'utilisateur avant de continuer.

2. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Si ça échoue, arrête-toi et
   demande à l'utilisateur de lancer `glab auth login`.

3. **Garde-fou « jamais sur `main` ».** Vérifie la branche courante (`git branch --show-current`).
   Si c'est `main` (ou `master`), **arrête-toi immédiatement** : on ne committe jamais sur `main`.
   Rappelle qu'il faut démarrer un ticket (`/ticket-start <iid>`) pour obtenir une branche.

4. **Contrôle de l'arbre de travail** (`git status --porcelain`) — deux refus possibles :
   - **Arbre vide** (aucun changement en attente) : arrête-toi. Il n'y a rien à committer ; si le
     travail est déjà committé, c'est `/ticket-finish` qu'il faut lancer, pas `/ticket-ship`.
   - **Conflit en cours** (fusion/rebase non résolu : `git ls-files --unmerged` non vide, ou lignes
     `UU`/`AA`/`DD`/`AU`/`UA`/`DU`/`UD` dans `git status --porcelain`) : arrête-toi et demande à
     l'utilisateur de résoudre le conflit d'abord. Ne committe jamais un arbre en conflit.

5. **Commit automatique, sans confirmation** (choix explicite du ticket #34 : zéro blocage manuel,
   cohérent avec l'auto-estimation du temps de `/ticket-finish`). Ne demande **pas** de validation
   du message — mais **montre** ce que tu committes (transparence a posteriori) :
   - Affiche un résumé : `git diff --stat HEAD` (inclut le staged) et la liste des fichiers.
   - Stage tout ce qui est en attente : `git add -A`.
   - Rédige un message **Conventional Commits** d'après la portée réelle du diff :
     - en-tête `<type>(<scope>): <description impérative>` — `type` ∈
       `feat`/`fix`/`chore`/`docs`/`refactor`/`test`/`ci`/`build`/`perf` (cohérent avec le préfixe
       de branche : `feat/`→`feat`, `fix/`→`fix`, `chore/`→`chore`, `docs/`→`docs`), `scope`
       optionnel (module/dossier concerné) ;
     - corps optionnel (le *pourquoi*, pas le *quoi*) ;
     - pied **`Closes #<iid>`** — `/ticket-ship` est l'action terminale du cycle de dev, donc le
       commit porte `Closes` (et non `Refs`) : GitLab fermera le ticket au merge.
   - Committe directement, **hook `commit-msg` respecté** (il valide en-tête + `Closes #<iid>`) :
     ```
     git commit -F - <<'EOF'
     <type>(<scope>): <description>

     <corps optionnel>

     Closes #<iid>
     EOF
     ```
     **N'utilise jamais `--no-verify`** : si le hook refuse le message, corrige le message et
     réessaie — ne le contourne pas.

6. **Enchaîne `/ticket-finish`.** Une fois le commit créé, l'arbre est propre : invoque la commande
   **`/ticket-finish`** (sans argument — elle relira l'IID depuis la branche — ou passe `<iid>`).
   Elle prend le relais pour : push de la branche (jamais de `--force`), création/mise à jour de la
   MR en Draft avec `Closes #<iid>`, passage du **statut** à « En revue », et **log automatique du
   temps** (estimé d'après la portée du travail). **Ne ré-implémente pas ces étapes ici** :
   `/ticket-finish` en est la source unique, et son étape de commit sera sans objet (arbre déjà
   propre) — elle passera directement au push.

7. Résumé final : reprends le résumé produit par `/ticket-finish` (lien de la MR, état Draft/Ready,
   temps loggé) et préfixe-le du **commit créé** (hash court + en-tête). Rappelle que le **merge
   reste une décision humaine** — `/ticket-ship` ne merge, ni ne ferme, ni ne force-push jamais.
