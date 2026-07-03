---
description: Démarre le travail sur un ticket GitLab (branche + assignation + label in-progress)
argument-hint: <issue-iid>
allowed-tools: Bash(git:*), Bash(glab:*)
---

Tu vas démarrer le travail sur le ticket GitLab d'IID `$ARGUMENTS` selon les règles de
@docs/10-workflow-git.md. Suis ces étapes dans l'ordre et arrête-toi (en expliquant pourquoi)
dès qu'une vérification échoue au lieu de forcer la suite.

1. Si aucun IID n'est fourni dans `$ARGUMENTS`, demande-le à l'utilisateur avant de continuer.

2. Vérifie l'authentification : `glab auth status`. Si ça échoue, arrête-toi et demande à
   l'utilisateur de lancer `glab auth login`.

3. Vérifie l'état de la copie de travail avec `git status --porcelain`. S'il y a des
   changements non commités, arrête-toi et demande à l'utilisateur quoi en faire (les
   committer, les stasher, ou annuler) — ne prends pas cette décision à sa place.

4. Récupère le ticket : `glab issue view $ARGUMENTS`. Note le titre, la description, les
   labels et les critères d'acceptation.

5. Détermine le préfixe de branche à partir du label `type::*` du ticket :
   `type::feature`→`feat`, `type::bug`→`fix`, `type::chore`→`chore`, `type::docs`→`docs`.
   Si aucun label `type::*` n'est présent, déduis le type du titre/de la description, ou
   demande à l'utilisateur si ambigu.

6. Construis le slug : titre en minuscules, accents retirés, tout ce qui n'est pas
   alphanumérique remplacé par `-`, tirets multiples collapsés, tronqué à ~40 caractères,
   sans tiret de fin. Le nom de branche est `<type>/<iid>-<slug>`.

7. Mets `main` à jour puis crée la branche :
   ```
   git checkout main
   git pull origin main
   git checkout -b <type>/<iid>-<slug>
   ```

8. Récupère ton identifiant GitLab (`glab api user` → champ `username`) et assigne le
   ticket à toi-même tout en faisant avancer le statut :
   ```
   glab issue update $ARGUMENTS --assignee <username> --label "status::in-progress" --unlabel "status::todo"
   ```
   Si le label `status::todo` n'existe pas encore sur le ticket, ignore l'échec du
   `--unlabel` (ce n'est pas bloquant).

9. Termine par un résumé court : nom de la branche créée, titre du ticket, et la liste des
   critères d'acceptation trouvés dans la description — pour cadrer le travail qui commence.

Ne crée pas encore de Merge Request à ce stade (il n'y a pas encore de commit à proposer) —
c'est le rôle de `/ticket-finish`.
