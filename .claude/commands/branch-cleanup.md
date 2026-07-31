---
description: Nettoie les branches de tickets déjà mergées et revient sur main à jour
allowed-tools: Bash(git:*), Bash(glab:*), Bash(bash:*)
---

Nettoie les branches **locales** de tickets déjà mergés, **ramasse les worktrees devenus inutiles**
et remet `main` à jour. Ne supprime
**jamais** une branche dont le statut de merge n'est pas confirmé par GitLab (garde-fou détaillé
dans `docs/10-workflow-git.md` §6, non chargé automatiquement — à n'ouvrir qu'en cas de doute ;
cette commande est autosuffisante).

> Au merge, GitLab fait déjà le reste automatiquement : suppression de la branche **distante**
> (case « Delete source branch », pré-cochée) et passage du ticket au statut **Terminé** (la
> fermeture via `Closes #` pose le statut « done » du lifecycle). Cette commande ne couvre donc
> que ce que GitLab ne peut pas toucher : ta copie **locale**.

1. `bash scripts/gitlab/lib.sh require` — arrête-toi si glab absent ou non authentifié.

2. `git fetch --prune origin` pour rafraîchir l'état des branches distantes.

3. Liste les branches locales autres que `main` (`git branch --format='%(refname:short)'`).
   Pour chacune (le nom suit `<type>/<iid>-<slug>`, donc `<iid>` s'extrait du nom) :
   - trouve sa MR avec `glab mr view <branche> --output json` (échec de la commande = aucune
     MR trouvée) ;
   - si aucune MR n'est trouvée, laisse la branche telle quelle (pas de suppression sans MR
     identifiée) ;
   - inspecte le champ `state` du JSON retourné ; s'il n'est pas exactement `merged`, laisse
     la branche telle quelle ;
   - si `state` vaut `merged`, ajoute la branche (et l'`iid` extrait de son nom) à la liste des
     candidates au nettoyage.

> **Dans un worktree** (`git worktree`, docs/10 §9) : ne bascule pas sur `main` — il est emprunté
> par le clone principal et `git checkout main` y échoue. La mise à jour de `main`, elle, reste
> possible : `lib.sh sync-main` (étape 5) travaille sur le clone principal quel que soit l'endroit
> d'où on l'appelle. Repère : à la racine d'un worktree, `.git` est un fichier, pas un dossier.

4. **Avant de supprimer quoi que ce soit**, ramasse les worktrees dont le travail est soldé :
   ```
   bash scripts/git/worktree.sh gc
   ```
   Cet ordre n'est pas cosmétique : `git branch -D` **refuse** une branche empruntée par un
   worktree (« checked out at … »), donc sans ce passage les candidates de l'étape 3 resteraient
   là sans que rien ne le dise. `gc` retire uniquement les worktrees dont `glab` confirme la MR
   mergée ou le ticket fermé, **jamais** celui de la session courante ni un worktree porteur de
   travail non sauvegardé — qu'il signale au lieu de le supprimer (docs/10 §9.2). Il ne supprime
   aucune branche : c'est l'étape suivante qui s'en charge. Relaie ses éventuelles alertes dans
   ton résumé final, et `--check` d'abord si tu veux voir avant d'agir.

5. S'il y a des candidates :
   - si l'une d'elles est la branche courante **et que tu es dans le clone principal**, bascule
     d'abord sur `main` (`git checkout main`) — on ne supprime pas une branche sous ses propres
     pieds ;
   - remets `main` à jour : `bash scripts/gitlab/lib.sh sync-main`. Le helper (#205) avance
     `refs/heads/main` du **clone principal** sur `origin/main`, en **fast-forward seulement**,
     d'où qu'on l'appelle — depuis un worktree il pose la ref sans toucher au moindre fichier, là
     où un `git checkout main` échouerait. Il **s'abstient en le disant** si le répertoire porteur
     de `main` a des changements non commités ou si `main` a divergé : relaie son message, ce
     n'est jamais bloquant ;
   - pour chaque candidate : `git branch -D <branche>`. Le `-D` (forcé) est **sûr ici** parce que
     GitLab a déjà confirmé la MR comme `merged` (étape 3) — garantie plus forte que l'ancêtre
     git, et **nécessaire** car le projet merge en **squash** : la pointe de la branche n'est pas
     un ancêtre du commit squashé sur `main`, donc `git branch -d` la refuserait à tort. N'utilise
     `-D` **que** sur une branche dont le merge est confirmé par GitLab — jamais autrement ;
   - normalement GitLab a déjà supprimé la branche **distante** au merge ; si elle existe encore
     (case décochée au merge) : `git push origin --delete <branche>` (si un `git pull`/`push`
     reste bloqué sur une demande d'identifiants — Windows + Git Credential Manager — relance-le
     en forçant `glab` : `git -c credential.helper='!glab auth git-credential' <commande>`) ;
   - le **statut** du ticket est déjà `Terminé` (posé automatiquement à la fermeture par le
     merge) — rien à faire ; tu peux le vérifier si besoin, mais ne le repose pas.

6. Si aucune candidate n'est trouvée, contente-toi de remettre `main` à jour
   (`bash scripts/gitlab/lib.sh sync-main`) et dis-le — le helper est muet quand elle l'est déjà.

7. Termine par un résumé des branches supprimées (locale/distante) et de celles laissées de
   côté avec la raison (pas de MR, MR pas encore mergée), suivi des **worktrees retirés** à
   l'étape 4 et de ceux que `gc` a conservés en signalant du travail non sauvegardé.
