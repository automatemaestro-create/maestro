---
description: Nettoie les branches de tickets déjà mergées et revient sur main à jour
allowed-tools: Bash(git:*), Bash(glab:*)
---

Nettoie les branches locales/distantes correspondant à des Merge Requests déjà mergées,
selon les garde-fous de @docs/10-workflow-git.md §6. Ne supprime **jamais** une branche dont
le statut de merge n'est pas confirmé par GitLab.

1. `glab auth status` — arrête-toi si non authentifié.

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
   - pour chaque candidate : `git branch -d <branche>` (jamais `-D` — si Git refuse car la
     branche n'est pas fusionnée localement, c'est le signal que quelque chose ne colle pas ;
     arrête-toi et signale-le au lieu de forcer) ;
   - si la branche distante existe encore (GitLab ne l'a pas supprimée automatiquement au
     merge) : `git push origin --delete <branche>` (si un `git pull`/`push` reste bloqué sur
     une demande d'identifiants — Windows + Git Credential Manager — relance-le en forçant
     `glab` : `git -c credential.helper='!glab auth git-credential' <commande>`) ;
   - pose l'état terminal **Status natif « Terminé »** sur le ticket associé (le cycle de vie
     est porté par le champ Status, pas par des labels — voir @docs/10-workflow-git.md §3).
     Résous l'ID global du work item depuis l'iid, puis pose le statut :
     ```
     glab api graphql -f query='{ project(fullPath:"maestro-group4345327/maestro") { workItems(iids:["<iid>"]) { nodes { id } } } }'
     glab api graphql -f query='mutation { workItemUpdate(input:{ id:"<work-item-gid>", statusWidget:{ status:"gid://gitlab/WorkItems::Statuses::Custom::Status/1020452" } }){ errors } }'
     ```
     `…/Custom::Status/1020452` = « Terminé » du lifecycle « Maestro ». Le merge a déjà dû
     fermer l'issue via `Closes #<iid>` — poser le statut ne rouvre/ferme rien, il reflète
     juste l'aboutissement.

5. Si aucune candidate n'est trouvée, contente-toi de remettre `main` à jour
   (`git checkout main && git pull origin main`) et dis-le.

6. Termine par un résumé des branches supprimées (locale/distante) et de celles laissées de
   côté avec la raison (pas de MR, MR pas encore mergée).
