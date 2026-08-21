---
description: Nettoie les branches de tickets déjà mergées et revient sur main à jour
allowed-tools: Bash(git:*), Bash(gh:*), Bash(bash:*)
---

Nettoie les branches **locales** de tickets déjà mergés, **ramasse les worktrees devenus inutiles**
et remet `main` à jour. Ne supprime **jamais** une branche dont le statut de merge n'est pas
confirmé par la forge (garde-fou détaillé dans `docs/10-workflow-git.md` §6, non chargé
automatiquement — à n'ouvrir qu'en cas de doute ; cette commande est autosuffisante).

> **La boucle est déjà écrite en shell** — `lib.sh cleanup-merged` (étape 4), dont l'en-tête dit
> qu'il est « le pendant non-interactif de `/branch-cleanup` », même garde-fou compris. Ne la
> réimplémente pas ici : un `gh pr view <branche> --json …` par branche réinjecte des milliers
> d'octets pour en tirer un mot — la version GitLab de cette boucle coûtait **~43 000 tokens** sur
> ce dépôt à chaque invocation (#309, audit #304 §4.1). Les autres étapes ne font que ce que le
> helper ne fait **pas** : basculer sur `main`, supprimer la branche **distante** et poser le cycle
> de vie.

> Au merge, la forge supprime déjà la branche **distante** (« Delete source branch » pré-cochée sur
> GitLab, `delete_branch_on_merge` sur GitHub — `lib.sh merge-settings` rend le réglage des deux
> côtés) et **ferme** le ticket via `Closes #`. Mais elle ne pose pas son état : le cycle de vie
> vivant dans le **champ Status** d'un projet Projects v2 (docs/10 §3), rien côté forge ne bascule
> un ticket sur « Terminé » à sa fermeture. Cette commande couvre donc ta copie **locale**, plus ce
> que la forge ne fait pas.

1. `bash scripts/gitlab/lib.sh require` — arrête-toi si le CLI de la forge est absent ou non
   authentifié (son message nomme lequel).

2. **Ramasse les worktrees soldés, avant toute suppression de branche** :
   ```
   bash scripts/git/worktree.sh gc
   ```
   L'ordre n'est pas cosmétique : `git branch -D` **refuse** une branche empruntée par un worktree,
   donc sans ce passage les branches des worktrees soldés resteraient là indéfiniment. `gc` ne
   retire que les worktrees dont la forge confirme la PR mergée ou le ticket fermé, **jamais** celui
   de la session courante ni un worktree porteur de travail non sauvegardé — qu'il **signale** au
   lieu de le supprimer (docs/10 §9.2) : relaie ses alertes dans ton résumé, et `--check` d'abord
   si tu veux voir avant d'agir. Il ne supprime **aucune** branche : c'est l'étape 4. Il pose en
   revanche le cycle de vie « Terminé » des tickets qu'il solde (#275) — l'étape 6 en devient
   idempotente sans devenir inutile : le worktree du ticket qu'on vient de merger peut ne pas
   exister ici, ou être celui de la session courante, que `gc` ne touche jamais.

   Au même passage, `gc` **signale les tickets « En cours » dont plus personne ne s'occupe** (#328) :
   une session morte — délai, pilote tué, console fermée, session interactive laissée en plan — laisse
   son ticket « En cours » **et** assigné, c'est-à-dire exactement ce que `queue.sh` écarte, donc
   invisible pour toujours. Le bloc « Tickets « En cours » dont plus personne ne s'occupe » est
   **consultatif** : rien n'est repris ni reposé ici (la reprise est le geste explicite de #329).
   Relaie-le dans ton résumé tel quel, sans rien décider — et n'y touche pas au motif que le ticket
   te paraît fini : un « orphelin » est une **déduction** de fraîcheur, bornée aux worktrees de cette
   machine. `bash scripts/gitlab/lib.sh reconcile-en-cours` en donne le détail, verdict par verdict
   (vivant / orphelin / hors de portée, avec sa source).

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
   Le helper ne retient que les branches dont la forge confirme la PR `merged` et les supprime par
   `git branch -D` — forcé, mais **sûr et nécessaire** ici : le merge est confirmé (garantie plus
   forte que l'ancêtre git) et le projet merge en **squash**, donc `-d` les refuserait à tort. Il
   vise le **clone principal** d'où qu'on l'appelle et **s'abstient en le disant** si l'arbre y est
   sale. Son bilan tient en quelques lignes :
   - `supprimée : <branche> (PR merged)` — partie ;
   - `⚠ conservée : <branche> (PR merged, …)` — mergée mais retenue par un worktree ou par git ;
   - le décompte final, dont les branches laissées de côté (aucune PR, ou PR pas encore mergée).

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
   - **branche distante** — la forge l'a normalement supprimée au merge ; celles qui restent
     (réglage décoché) se voient d'un coup, `cleanup-merged` venant de rafraîchir les refs de
     suivi :
     ```
     git branch -r --list origin/<branche-1> origin/<branche-2>
     ```
     Supprime **celles qui s'affichent**, une par une : `git push origin --delete <branche>`. Si un
     `git push` reste bloqué sur une demande d'identifiants (Windows + Git Credential Manager),
     relance-le en forçant le CLI de la forge (`!gh auth git-credential`, ou
) :
     `git -c credential.helper='!gh auth git-credential' push origin --delete <branche>`.
   - **cycle de vie** — un seul appel, tous les iid à la suite :
     ```
     bash scripts/gitlab/lib.sh reconcile-workflow <iid-1> <iid-2>
     ```
     Il pose « Terminé » dans le champ Status — à valeur unique, donc rien à retirer (docs/10 §3) —,
     **saute** ce qui est déjà « Terminé » et n'écrase **jamais** un « Abandonné »/« Doublon ».
     Idempotent : sans effet sur ce que l'étape 2 a déjà posé.

7. **Résumé** : branches supprimées (locale / distante) et celles laissées de côté avec la raison,
   tickets passés à « Terminé », worktrees retirés à l'étape 2 et ceux que `gc` a conservés en
   signalant du travail non sauvegardé, et — s'il y en a — les **tickets « En cours » orphelins**
   qu'il a signalés, à rendre tels quels : c'est un constat, pas une liste de choses à faire.
