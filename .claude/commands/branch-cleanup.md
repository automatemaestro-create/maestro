---
description: Nettoie les branches de tickets déjà mergées et revient sur main à jour
allowed-tools: Bash(git:*), Bash(glab:*), Bash(bash:*)
---

Nettoie les branches **locales** de tickets déjà mergés, **ramasse les worktrees devenus inutiles**
et remet `main` à jour. Ne supprime **jamais** une branche dont le statut de merge n'est pas
confirmé par GitLab (garde-fou détaillé dans `docs/10-workflow-git.md` §6, non chargé
automatiquement — à n'ouvrir qu'en cas de doute ; cette commande est autosuffisante).

> **La boucle est déjà écrite en shell** — `lib.sh cleanup-merged` (étape 4), dont l'en-tête dit
> qu'il est « le pendant non-interactif de `/branch-cleanup` », même garde-fou compris. Ne la
> réimplémente pas ici : un `glab mr view <branche> --output json` par branche réinjecte ~3 500
> octets pour en tirer un mot, soit **~43 000 tokens** sur ce dépôt à chaque invocation (#309,
> audit #304 §4.1). Les autres étapes ne font que ce que le helper ne fait **pas** : basculer sur
> `main`, supprimer la branche **distante** et poser le cycle de vie.

> Au merge, GitLab supprime déjà la branche **distante** (case « Delete source branch »,
> pré-cochée) et **ferme** le ticket via `Closes #`. Mais il ne pose plus son état : depuis que le
> cycle de vie est porté par les **labels `workflow::*`** (docs/10 §3), plus rien ne bascule un
> ticket sur « Terminé » à sa fermeture. Cette commande couvre donc ta copie **locale**, plus ce
> que GitLab ne fait pas.

1. `bash scripts/gitlab/lib.sh require` — arrête-toi si glab absent ou non authentifié.

2. **Ramasse les worktrees soldés, avant toute suppression de branche** :
   ```
   bash scripts/git/worktree.sh gc
   ```
   L'ordre n'est pas cosmétique : `git branch -D` **refuse** une branche empruntée par un worktree,
   donc sans ce passage les branches des worktrees soldés resteraient là indéfiniment. `gc` ne
   retire que les worktrees dont `glab` confirme la MR mergée ou le ticket fermé, **jamais** celui
   de la session courante ni un worktree porteur de travail non sauvegardé — qu'il **signale** au
   lieu de le supprimer (docs/10 §9.2) : relaie ses alertes dans ton résumé, et `--check` d'abord
   si tu veux voir avant d'agir. Il ne supprime **aucune** branche : c'est l'étape 4. Il pose en
   revanche le cycle de vie « Terminé » des tickets qu'il solde (#275) — l'étape 6 en devient
   idempotente sans devenir inutile : le worktree du ticket qu'on vient de merger peut ne pas
   exister ici, ou être celui de la session courante, que `gc` ne touche jamais.

3. **Branche courante mergée ?** `cleanup-merged` ne touche jamais à la branche sur laquelle est
   posé le clone principal — on ne supprime pas une branche sous ses propres pieds. Si la branche
   courante (`git branch --show-current`) n'est pas `main`, demande son état en **un mot** :
   ```
   bash scripts/gitlab/lib.sh mr-state <branche-courante>
   ```
   S'il vaut `merged` **et que tu es dans le clone principal** (repère : à la racine d'un worktree,
   `.git` est un **fichier**, pas un dossier), bascule : `git checkout main`. Dans un worktree, ne
   bascule **pas** — `main` y est emprunté par le clone principal et le `checkout` échouerait ; la
   branche sera signalée « empruntée par un worktree » à l'étape 4 et partira depuis ailleurs.

4. **La purge**, garde-fou compris :
   ```
   bash scripts/gitlab/lib.sh cleanup-merged
   ```
   Le helper ne retient que les branches dont GitLab confirme la MR `merged` et les supprime par
   `git branch -D` — forcé, mais **sûr et nécessaire** ici : le merge est confirmé (garantie plus
   forte que l'ancêtre git) et le projet merge en **squash**, donc `-d` les refuserait à tort. Il
   vise le **clone principal** d'où qu'on l'appelle et **s'abstient en le disant** si l'arbre y est
   sale. Son bilan tient en quelques lignes :
   - `supprimée : <branche> (MR merged)` — partie ;
   - `⚠ conservée : <branche> (MR merged, …)` — mergée mais retenue par un worktree ou par git ;
   - le décompte final, dont les branches laissées de côté (aucune MR, ou MR pas encore mergée).

   Ces deux premières lignes sont les branches **mergées** : ce sont elles, et elles seules, que
   l'étape 6 traite. Leur iid est le nombre de leur nom (`<type>/<iid>-<slug>` ;
   `bash scripts/gitlab/lib.sh branch-iid <branche>` en cas de doute).

5. **`main` à jour** :
   ```
   bash scripts/gitlab/lib.sh sync-main
   ```
   Le helper (#205) avance `refs/heads/main` du **clone principal** sur `origin/main`, en
   **fast-forward seulement**, d'où qu'on l'appelle — depuis un worktree il pose la ref sans
   toucher au moindre fichier, là où un `git checkout main` échouerait. Il **s'abstient en le
   disant** (arbre porteur sale, `main` divergent) : relaie son message, ce n'est jamais bloquant.

6. **Ce que le helper ne fait pas**, sur les seules branches mergées de l'étape 4 (s'il n'y en a
   aucune, passe au résumé) :
   - **branche distante** — GitLab l'a normalement supprimée au merge ; celles qui restent (case
     décochée) se voient d'un coup, `cleanup-merged` venant de rafraîchir les refs de suivi :
     ```
     git branch -r --list origin/<branche-1> origin/<branche-2>
     ```
     Supprime **celles qui s'affichent**, une par une : `git push origin --delete <branche>`. Si un
     `git push` reste bloqué sur une demande d'identifiants (Windows + Git Credential Manager),
     relance-le en forçant `glab` :
     `git -c credential.helper='!glab auth git-credential' push origin --delete <branche>`.
   - **cycle de vie** — un seul appel, tous les iid à la suite :
     ```
     bash scripts/gitlab/lib.sh reconcile-workflow <iid-1> <iid-2>
     ```
     Il pose `workflow::termine` en retirant les cinq autres dans le **même** appel — l'exclusion
     mutuelle des labels scopés étant Premium, rien ne l'assurerait à notre place (docs/10 §3) —,
     **saute** ce qui est déjà « Terminé » et n'écrase **jamais** un « Abandonné »/« Doublon ».
     Idempotent : sans effet sur ce que l'étape 2 a déjà posé.

7. **Résumé** : branches supprimées (locale / distante) et celles laissées de côté avec la raison,
   tickets passés à « Terminé », worktrees retirés à l'étape 2 et ceux que `gc` a conservés en
   signalant du travail non sauvegardé.
