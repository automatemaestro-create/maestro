---
description: Synthétise une Pull Request (état, pipeline, threads, diff) pour éclairer la décision de merge humaine — ne merge jamais
argument-hint: "<pr-numéro | branche>  (défaut : la PR de la branche courante)"
allowed-tools: Bash(git:*), Bash(gh:*), Bash(bash:*)
---

Commande **de supervision, en lecture seule** : tu synthétises **une** Pull Request pour aider
l'humain à décider s'il la merge. **Tu ne merges, ne fermes, n'approuves et ne modifies jamais**
une PR ou un ticket — même si tout est vert, la décision de merge reste humaine (garde-fou détaillé
dans `docs/10-workflow-git.md` §6, non chargé automatiquement ; cette commande est autosuffisante,
n'ouvre le doc qu'en cas de doute).

1. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Arrête-toi si non authentifié.

2. Détermine la PR cible :
   - si `$ARGUMENTS` est fourni, c'est un numéro de PR (`5`) ou un nom de branche ;
   - sinon, utilise la branche courante : `git branch --show-current`.
   `bash scripts/gitlab/lib.sh mr-iid [branche]` rend le numéro de la PR ouverte d'une branche des
   deux côtés de la bascule (le verbe garde son nom `mr-iid` : c'est le contrat de `lib.sh`,
   normalisé vers le vocabulaire GitLab, cf. son en-tête).
   Si aucune PR ne correspond, dis-le et arrête-toi (n'en crée pas — c'est le rôle de
   `/ticket-finish`).

3. Récupère la PR en JSON :
   ```
   gh pr view <cible> --json number,title,author,headRefName,baseRefName,state,isDraft,mergeable,mergeStateStatus,reviewDecision,body,changedFiles,additions,deletions,closingIssuesReferences,statusCheckRollup
   ```
   Exploite notamment :
   - identité : `number`, `title`, `author.login`, `headRefName`, `baseRefName` ;
   - état : `state` (`OPEN`/`MERGED`/`CLOSED`), caractère brouillon (`isDraft`) ;
   - aptitude au merge : `mergeable` (`MERGEABLE`/`CONFLICTING`/`UNKNOWN`) et `mergeStateStatus`
     (`CLEAN`, `BLOCKED`, `BEHIND`, `DIRTY`, `UNSTABLE`…), `reviewDecision` s'il est renseigné ;
   - lien ticket : `closingIssuesReferences` le donne **nativement** (c'est le `Closes #<iid>` de la
     description, résolu par la forge) ; à défaut, cherche `Closes #<iid>` dans `body` ;
   - volume : `changedFiles`, `additions`, `deletions`.

   ⚠ `mergeable` est calculé **à la demande** : GitHub rend `UNKNOWN` tant que le calcul n'a pas
   abouti — c'est un « je ne sais pas encore », jamais un « pas de conflit ». Ne l'attends pas ;
   pour un verdict ferme, `bash scripts/gitlab/lib.sh mr-conflict <branche>` joue un merge 3-way
   réel en lecture seule (`0` = propre, `3` = conflit), et c'est ce que fait `/mr-fix`.

4. État du pipeline : `statusCheckRollup` du JSON ci-dessus porte les contrôles de la PR — c'est la
   source qui fait foi, la CI ne se déclenchant que sur les PR (`on: pull_request`,
   `docs/10-workflow-git.md` §8). S'il est vide, `bash scripts/gitlab/lib.sh pipeline-latest
   <headRefName>` plutôt qu'un `gh run list --branch` : ce dernier **verrait** bien le run (un run
   `pull_request` porte la branche source), mais il rend `status` et `conclusion` séparément là où
   le helper recompose le vocabulaire unique attendu ici. Rapporte
   `success`/`failed`/`running`/absent, sans faire échouer la synthèse si aucun pipeline n'a encore
   tourné.

5. Résumé des changements : par défaut, ne réinjecte qu'un **résumé** du diff (fichiers touchés +
   volume), **pas le diff détaillé**, qui gonfle inutilement le contexte. Deux voies, dans cet
   ordre de préférence :
   - le JSON suffit le plus souvent — `changedFiles`/`additions`/`deletions`, et `gh pr view <cible>
     --json files` donne le détail par fichier sans rapatrier une seule ligne de code ;
   - sinon, dérive-le du patch : `gh pr diff <cible> --patch | git apply --stat` (lisible) ou
     `| git apply --numstat` (machine) ; `gh pr diff <cible> --name-only` pour les seuls noms.

   Ne récupère le **diff détaillé** (`gh pr diff <cible>`) que sur **demande explicite** de
   l'utilisateur, ou si un point précis exige de lire les lignes modifiées — et même alors, cible le
   fichier concerné et résume, ne recopie pas tout.

6. Threads non résolus : GitHub ne les expose pas dans `gh pr view --json` (il n'y a pas de champ
   `reviewThreads`). Pour les compter, une lecture **GraphQL** en lecture seule :
   ```
   gh api graphql -f query='{ repository(owner:"automatemaestro-create", name:"maestro") { pullRequest(number: <numéro>) { reviewThreads(first: 50) { nodes { isResolved } } } } }'
   ```
   Compte les `isResolved: false` et signale-les — sans jamais y répondre ni les résoudre. Un échec
   de cette lecture ne fait pas échouer la synthèse : dis simplement que le décompte n'a pas pu
   être obtenu.
   Le dépôt est écrit **en toutes lettres** parce qu'il ne peut pas encore être déduit : `gh` le
   lirait normalement du remote, or `origin` pointe sur GitLab jusqu'à la bascule (#343). C'est la
   valeur par défaut de `MAESTRO_GITHUB_REPO` — si cette variable est posée à autre chose, prends
   la sienne (`owner` avant le `/`, `name` après) plutôt que celle-ci.

7. Rends un **compte rendu Markdown** :
   - en-tête : `#<numéro> — <titre>` · état (Draft/Ready, open/merged) · `<source> → <cible>` · auteur ;
   - **Aptitude au merge** : mergeable ? conflits ? threads bloquants ? pipeline ? ticket lié ?
     (une ligne par point, ✅/⚠️/❌) ;
   - **Changements** : N fichiers, résumé qualitatif ;
   - **Points d'attention pour la revue** : ce qu'un relecteur humain devrait vérifier — **pas** une
     décision de merge.
   Termine en rappelant explicitement que **le merge est une action humaine** : tu ne le fais pas,
   et tu proposes, si l'utilisateur le décide, de le faire lui-même dans l'UI de la forge ou via
   `gh pr merge` **de sa propre main** — la commande t'est refusée en `deny`, à lui non.

N'exécute **aucune** commande d'écriture : ni `gh pr merge`/`close`/`review`/`edit` (ni leurs
), ni `gh issue edit`/`set-workflow`, ni `git push`. En cas de doute,
abstiens-toi et demande.
