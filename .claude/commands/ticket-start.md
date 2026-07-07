---
description: Démarre le travail sur un ticket GitLab (branche + assignation + statut « En cours »)
argument-hint: <issue-iid>
allowed-tools: Bash(git:*), Bash(glab:*), Bash(bash:*)
---

Tu vas démarrer le travail sur le ticket GitLab d'IID `$ARGUMENTS` selon les règles de
@docs/10-workflow-git.md. Suis ces étapes dans l'ordre et arrête-toi (en expliquant pourquoi)
dès qu'une vérification échoue au lieu de forcer la suite.

1. Si aucun IID n'est fourni dans `$ARGUMENTS`, demande-le à l'utilisateur avant de continuer.

2. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Si ça échoue, arrête-toi et
   demande à l'utilisateur de lancer `glab auth login`.

3. Vérifie l'état de la copie de travail avec `git status --porcelain`. S'il y a des
   changements non commités, arrête-toi et demande à l'utilisateur quoi en faire (les
   committer, les stasher, ou annuler) — ne prends pas cette décision à sa place.

4. Récupère le ticket : `glab issue view $ARGUMENTS`. Note le titre, la description, les
   labels et les critères d'acceptation.

5. Détermine le préfixe de branche à partir du label `type::*` du ticket, via le helper :
   `bash scripts/gitlab/lib.sh branch-prefix <valeur-type>` (`feature`→`feat`, `bug`→`fix`,
   `infra`→`chore`, `doc`→`docs`). Si aucun label `type::*` n'est présent, déduis le type du
   titre/de la description, ou demande à l'utilisateur si ambigu.

6. Construis le slug avec le helper : `bash scripts/gitlab/lib.sh slug "<titre du ticket>"`
   (minuscules, accents retirés, non-alphanumérique → `-`, tronqué à ~40 caractères). Le nom de
   branche est `<type>/<iid>-<slug>`.

7. Mets `main` à jour, **nettoie au passage les branches déjà mergées**, puis crée la branche :
   ```
   git checkout main
   git pull origin main
   bash scripts/gitlab/lib.sh cleanup-merged
   git checkout -b <type>/<iid>-<slug>
   ```
   `cleanup-merged` est le pendant **automatique** de `/branch-cleanup` : maintenant qu'on est sur
   `main` à jour et que l'arbre est propre, il supprime les branches **locales** (hors `main` et hors
   branche courante) dont **GitLab confirme la MR `merged`** — et rien d'autre (garde-fou
   @docs/10-workflow-git.md §6). S'il n'y a rien à nettoyer, il le dit et n'a aucun effet.
   `/branch-cleanup` reste disponible pour un nettoyage explicite hors démarrage de ticket.

8. Assigne le ticket et fais passer son **Status natif** à « En cours ». Le cycle de vie est
   porté par le champ **Status** de GitLab (lifecycle « Maestro »), pas par des labels — voir
   @docs/10-workflow-git.md §3.
   - Assignation : récupère ton username (`glab api user` → champ `username`), puis
     `glab issue update $ARGUMENTS --assignee <username>`.
   - Statut : `bash scripts/gitlab/lib.sh set-status $ARGUMENTS "En cours"`. Le helper résout le
     work item depuis l'iid et **dérive le GID du statut par nom** depuis le lifecycle « Maestro »
     (pas de GID en dur, robuste à une recréation du lifecycle). Vérifie que la commande réussit.
     Ne touche pas aux labels `agent::*` / `prio::*` / `type::*` (ils relèvent du triage, pas de ce
     workflow).

9. Renseigne les **dates** du ticket (voir @docs/10-workflow-git.md §3.3) :
   ```
   bash scripts/gitlab/lib.sh start-dates $ARGUMENTS
   ```
   Le helper pose la **date de début = aujourd'hui** (conservée telle quelle si le ticket en avait
   déjà une — ré-exécution sûre) et l'**échéance = début + un délai dérivé du label `prio::`**
   (`haute` → 2 j, `moyenne` → 5 j, `basse` → 10 j ; défaut `moyenne` si absent). Vérifie que la
   commande réussit ; en cas d'échec, signale-le mais ne bloque pas la création de branche déjà
   faite.

10. Termine par un résumé court : nom de la branche créée, titre du ticket, les dates posées
   (début / échéance), et la liste des critères d'acceptation trouvés dans la description — pour
   cadrer le travail qui commence.

Ne crée pas encore de Merge Request à ce stade (il n'y a pas encore de commit à proposer) —
c'est le rôle de `/ticket-finish`.
