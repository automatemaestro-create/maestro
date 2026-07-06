---
description: Termine le travail sur le ticket courant (push + MR + statut « En revue »)
argument-hint: "[issue-iid] (optionnel si le nom de la branche courante le contient déjà)"
allowed-tools: Bash(git:*), Bash(glab:*), Bash(bash:*)
---

Tu vas clôturer le cycle de développement de la branche courante selon les règles de
@docs/10-workflow-git.md. Arrête-toi et demande confirmation avant toute action qui modifie
l'état partagé (push, création/mise à jour de MR) si un point n'est pas clair.

1. Détermine l'IID du ticket : utilise `$ARGUMENTS` s'il est fourni, sinon extrais-le du nom
   de la branche courante (`git branch --show-current`, motif `<type>/<iid>-<slug>`). Si
   aucun IID ne peut être déterminé, demande-le à l'utilisateur.

2. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require` ; arrête-toi si non authentifié.

3. Regarde `git status --porcelain`. S'il reste des changements non commités :
   - montre un résumé (`git diff --stat`),
   - propose un message de commit au format Conventional Commits avec `Refs #<iid>` en pied
     (voir @docs/10-workflow-git.md §2),
   - demande confirmation à l'utilisateur avant de committer.
   Ne commite jamais silencieusement sans montrer ce qui va être committé.

4. Best-effort : si un outil de lint/test est détecté dans le dossier concerné (ex.
   `package.json` avec un script `lint`/`test`, `pyproject.toml`...), propose de l'exécuter et
   rapporte le résultat. S'il n'y en a pas (probable tant que le monorepo est un squelette
   sans code), dis-le simplement et continue.

5. Pousse la branche : `git push -u origin $(git branch --show-current)`. Ne fais jamais de
   `--force` ici — si le push est rejeté, arrête-toi et explique pourquoi plutôt que de forcer.
   Si le push **reste bloqué** sur une demande d'identifiants (typique sous Windows avec Git
   Credential Manager), relance-le en forçant `glab` comme credential helper :
   `GIT_TERMINAL_PROMPT=0 git -c credential.helper='' -c credential.helper='!glab auth git-credential' push -u origin $(git branch --show-current)`.

6. Vérifie si une MR existe déjà pour cette branche avec
   `glab mr view $(git branch --show-current) --output json`. Si la commande échoue, aucune
   MR n'existe encore. Si elle réussit, inspecte le JSON retourné (champs `state` —
   `opened`/`closed`/`merged` — et `draft`) plutôt que de parser une sortie texte.
   - **Si elle n'existe pas** : crée-la en Draft, liée au ticket :
     ```
     glab mr create --draft --target-branch main \
       --title "<titre du ticket>" \
       --description "Closes #<iid>

     ## Checklist
     - [ ] Respecte les conventions de branche/commit (docs/10-workflow-git.md)
     - [ ] Tests ajoutés/mis à jour si applicable
     - [ ] Documentation mise à jour si applicable
     - [ ] Pipeline CI verte (si configurée)"
     ```
   - **Si elle existe déjà et est en Draft** : demande à l'utilisateur si le travail est
     réellement terminé et prêt pour revue ; si oui, `glab mr update <mr> --ready`.
   - **Si elle existe déjà et n'est plus en Draft** : ne rien faire de plus sur la MR.

7. Fais passer le **Status natif** du ticket à « En revue » (le cycle de vie est porté par le
   champ Status, pas par des labels — voir @docs/10-workflow-git.md §3) :
   ```
   bash scripts/gitlab/lib.sh set-status <iid> "En revue"
   ```
   Le helper résout le work item depuis l'iid et **dérive le GID du statut par nom** depuis le
   lifecycle « Maestro » (pas de GID en dur). Vérifie que la commande réussit. Ne touche pas aux
   labels `agent::*` / `prio::*` / `type::*`.

8. Renseigne le **temps passé** (voir @docs/10-workflow-git.md §3.3). Le temps est **proposé, pas
   loggé en silence** — cohérent avec les garde-fous « montrer avant d'agir » :
   - Récupère la date de début : `bash scripts/gitlab/lib.sh get-start-date <iid>`. Si elle est
     vide (ticket démarré sans `/ticket-start`), demande à l'utilisateur combien de temps loggé
     plutôt que d'inventer.
   - Calcule le temps écoulé : `bash scripts/gitlab/lib.sh elapsed-days <date-début>` (jours
     calendaires depuis le début).
   - Vérifie ce qui est déjà loggé : `bash scripts/gitlab/lib.sh get-time-spent <iid>` (secondes).
     Si le résultat n'est pas `0`, du temps a déjà été enregistré — **signale-le et demande** avant
     d'en ajouter, pour ne pas doubler.
   - **Propose** une durée au format GitLab (`2h`, `1h 30m`, `1d`…). Le nombre de jours écoulés est
     un plafond calendaire (nuits/week-ends compris), pas l'effort réel : suggère une valeur
     plausible et **laisse l'utilisateur confirmer ou ajuster**. S'il valide, logge-la :
     ```
     bash scripts/gitlab/lib.sh log-time <iid> "<durée validée>" "Cycle de dev (start->finish)"
     ```
   - Si l'utilisateur ne veut rien logger, n'appelle pas `log-time` et passe à la suite.

9. Termine par un résumé : lien de la MR, état (Draft/Ready), le temps loggé le cas échéant, et
   rappelle que le merge reste une action humaine (personne — pas même toi — ne doit merger
   automatiquement).
