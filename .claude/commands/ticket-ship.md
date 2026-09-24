---
description: Commit automatique + /ticket-finish enchaînés — clôture le ticket courant sans blocage manuel
argument-hint: "[issue-iid] (optionnel si le nom de la branche courante le contient déjà)"
allowed-tools: Bash(bash:*), Bash(git:*), Bash(gh:*), Bash(mkdir:*), Bash(.venv/Scripts/python.exe:*), Bash(.venv/bin/python:*), ExitWorktree, Skill, AskUserQuestion, Read, Edit, Write
---

Tu clôtures le ticket courant **en une action** : tu commites d'office ce qui est en attente, puis tu
enchaînes **`/ticket-finish`**, qui va jusqu'au **merge** (#418). Les garde-fous priment sur
l'automatisation : arrête-toi, en disant pourquoi, dès qu'un contrôle échoue. La raison de chaque
étape vit dans [docs/10 §6.1](../../docs/10-workflow-git.md) (#1245).

La chaîne attend un pipeline, borné et **annoncé** : 2-4 min, jusqu'à 30 s'il n'est pas encore né.
Un pipeline rouge ou un conflit est d'abord réparé par `/mr-fix`, deux fois au plus (#460), et
l'attente s'allonge d'autant. Ce qui n'est pas débloqué laisse la PR ouverte et le ticket « En
revue » : un état normal, jamais un ✅ global. En run, l'attente et le merge sont au pilote.

1. **L'IID** : `$ARGUMENTS`, sinon la branche (`<type>/<iid>-<slug>`), sinon demande-le.

2. `bash scripts/gitlab/lib.sh require` — s'il échoue, arrête-toi et relaie son message.

3. **Jamais sur `main`** : sur `main` (ou `master`), arrête-toi — il faut d'abord `/ticket-start
   <iid>`.

4. **Garde-fou de clôture, avant le commit** — le message portera `Closes #<iid>`, et un iid
   étranger fermerait le ticket d'un autre au merge :
   ```
   bash scripts/gitlab/lib.sh close-guard <iid> || verdict=$?
   ```
   `0` poursuis · `3` la branche porte un **autre** ticket (dis lequel, propose de shipper celui-là
   ou de revenir sur la bonne branche, `lib.sh branch-for <iid>`)
   · `4` assigné à **quelqu'un d'autre** (nomme-le) · `5` branche sans iid · `1` verdict partiel :
   signale-le et poursuis. Sur `3`/`4`/`5`, arrête-toi : un refus ne se franchit que **sur demande
   explicite**, rappelée au résumé. `/ticket-finish` rejoue ce contrôle, sans effet de bord.

5. **L'arbre** (`git status --porcelain`) — deux refus : **vide** (rien à committer : c'est
   `/ticket-finish` qu'il faut lancer) ; **en conflit** (`git ls-files --unmerged` non vide, ou
   `UU`/`AA`/`DD`/`AU`/`UA`/`DU`/`UD`) : demande de le résoudre d'abord.

6. **Commit, sans confirmation** (#34), mais **montré** : `git diff --stat HEAD` et la liste des
   fichiers, puis `git add -A`. Message Conventional Commits d'après la portée réelle du diff :
   `<type>(<scope>): <description impérative>` (type cohérent avec le préfixe de branche), corps
   optionnel (le pourquoi), pied **`Closes #<iid>`** — GitHub fermera le ticket au merge. Le message
   s'écrit avec `Write` dans `.maestro/session/` (`mkdir -p .maestro/session` s'il manque), en chemin
   relatif — ni le scratchpad de session ni `/tmp`, ni heredoc, ni `-m "$(…)"` :
   ```
   git commit -F <fichier>
   ```
   **Jamais `--no-verify`** : un message refusé par le hook se corrige.

7. **Enchaîne `/ticket-finish`** (sans argument, ou `<iid>`). Il prend tout le reste : push, PR
   (`Closes #<iid>`, sans checklist), PR levée en « prête », « En revue », temps mesuré, attente du
   pipeline et merge par `merge-mr`, et, sur un pipeline rouge ou un conflit, le déblocage par
   `/mr-fix`. **Ne ré-implémente aucune de ces étapes ici** — ni le merge, ni le déblocage.
   Il hérite aussi, **sans une ligne à elle** ici, de trois gestes à ne pas rejouer : la
   **relecture visuelle** quand le diff touche un écran (#935), la confrontation des **critères
   d'acceptation au diff** (#968) — un critère non tenu ne bloque pas le merge —, et, sur un merge
   réussi, le retour au **clone principal** après le ramassage du worktree (#519). Lis son
   **verdict de merge** : il ouvre ton résumé et décide de l'étape 8.

8. **Sous-ticket ?** `bash scripts/gitlab/lib.sh parent-of <iid>`. Si un parent est trouvé :
   `bash scripts/gitlab/lib.sh startables <iid-parent>` donne les lots ouverts que rien ne bloque.
   Annonce-les démarrables maintenant (`/ticket-start <iid-suivant>`), prenables en parallèle s'il
   y en a plusieurs. Si le lot shippé était le **dernier ouvert**, annonce le parent fermé (merge
   fait) ou à fermer au merge : sa fermeture suit celle du dernier lot, par l'événement (#515). **Tu
   ne fermes rien** et n'écris rien sur le parent ; le verdict du merge fait foi, jamais la table des
   lots, que le workflow met à jour en différé. Ne propose aucun geste manuel : un parent encore
   ouvert quelques instants plus tard dit que l'événement n'est pas passé, et `lib.sh ferme-parent
   <iid-du-lot>` en est le rattrapage.

9. **Résumé** : le **commit créé** (hash court et en-tête), puis celui de `/ticket-finish` —
   **verdict du merge en tête** (mergé, ou la cause telle que `merge-mr` l'a rendue), l'**issue du
   déblocage** sur sa propre ligne (⊘ non tenté · ✅ abouti · ❌ sans succès), le lien de la PR, le
   temps loggé ; sur un merge réussi, le ramassage ou sa cause, et le fait que la session travaille
   désormais depuis le **clone principal**. Pour un sous-ticket, l'annonce de l'étape 8. **Jamais de ✅ global** : une
   PR restée ouverte est **inachevée**. `/ticket-ship` ne ferme ni ne force-push jamais, et ne merge
   jamais hors de `merge-mr` : **aucun merge non vérifié** (#417).
