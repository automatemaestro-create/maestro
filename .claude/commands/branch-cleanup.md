---
description: Nettoie les branches de tickets déjà mergées et revient sur main à jour
allowed-tools: Bash(git:*), Bash(glab:*), Bash(bash:*)
---

Nettoie les branches **locales** de tickets déjà mergés et remet `main` à jour, selon les
garde-fous de @docs/10-workflow-git.md §6. Ne supprime **jamais** une branche dont le statut de
merge n'est pas confirmé par GitLab.

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

4. S'il y a des candidates :
   - si l'une d'elles est la branche courante, bascule d'abord dessus vers `main` ;
   - `git checkout main && git pull origin main` ;
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

5. Si aucune candidate n'est trouvée, contente-toi de remettre `main` à jour
   (`git checkout main && git pull origin main`) et dis-le.

6. Termine par un résumé des branches supprimées (locale/distante) et de celles laissées de
   côté avec la raison (pas de MR, MR pas encore mergée).
