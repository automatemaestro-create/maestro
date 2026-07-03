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
   Pour chacune :
   - trouve sa MR avec `glab mr view <branche> --output json` (échec de la commande = aucune
     MR trouvée) ;
   - si aucune MR n'est trouvée, laisse la branche telle quelle (pas de suppression sans MR
     identifiée) ;
   - inspecte le champ `state` du JSON retourné ; s'il n'est pas exactement `merged`, laisse
     la branche telle quelle ;
   - si `state` vaut `merged`, ajoute la branche à la liste des candidates au nettoyage.

4. S'il y a des candidates :
   - si l'une d'elles est la branche courante, bascule d'abord dessus vers `main` ;
   - `git checkout main && git pull origin main` ;
   - pour chaque candidate : `git branch -d <branche>` (jamais `-D` — si Git refuse car la
     branche n'est pas fusionnée localement, c'est le signal que quelque chose ne colle pas ;
     arrête-toi et signale-le au lieu de forcer) ;
   - si la branche distante existe encore (GitLab ne l'a pas supprimée automatiquement au
     merge) : `git push origin --delete <branche>`.

5. Si aucune candidate n'est trouvée, contente-toi de remettre `main` à jour
   (`git checkout main && git pull origin main`) et dis-le.

6. Termine par un résumé des branches supprimées (locale/distante) et de celles laissées de
   côté avec la raison (pas de MR, MR pas encore mergée).
