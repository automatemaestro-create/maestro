---
description: Synthétise une Merge Request (état, pipeline, threads, diff) pour éclairer la décision de merge humaine — ne merge jamais
argument-hint: "<mr-iid | branche>  (défaut : la MR de la branche courante)"
allowed-tools: Bash(git:*), Bash(glab:*), Bash(bash:*)
---

Commande **de supervision, en lecture seule** : tu synthétises **une** Merge Request pour aider
l'humain à décider s'il la merge. **Tu ne merges, ne fermes, n'approuves et ne modifies jamais**
une MR ou un ticket — même si tout est vert, la décision de merge reste humaine (garde-fou détaillé
dans `docs/10-workflow-git.md` §6, non chargé automatiquement ; cette commande est autosuffisante,
n'ouvre le doc qu'en cas de doute).

1. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Arrête-toi si non authentifié.

2. Détermine la MR cible :
   - si `$ARGUMENTS` est fourni, c'est un IID de MR (`5`) ou un nom de branche ;
   - sinon, utilise la branche courante : `git branch --show-current`.
   Si aucune MR ne correspond, dis-le et arrête-toi (ne crée pas de MR — c'est le rôle de
   `/ticket-finish`).

3. Récupère la MR en JSON : `glab mr view <cible> --output json`. Exploite notamment :
   - identité : `iid`, `title`, `author`, `source_branch`, `target_branch` ;
   - état : `state` (`opened`/`merged`/`closed`), caractère brouillon (`draft`/`work_in_progress`) ;
   - aptitude au merge : `detailed_merge_status` (ou `merge_status`), `has_conflicts`,
     `blocking_discussions_resolved` (threads bloquants résolus ?), approbations si présentes ;
   - lien ticket : cherche `Closes #<iid>` dans `description` et signale-le ;
   - volume : `changes_count`.

4. État du pipeline : lis le pipeline de tête depuis le JSON (`pipeline`/`head_pipeline` → `status`)
   s'il est présent ; sinon, `glab ci status` (ou `glab ci view <source_branch>`). Rapporte
   `success`/`failed`/`running`/absent, sans le faire échouer si aucun pipeline n'est configuré (le
   monorepo n'a pas encore de CI — `docs/10-workflow-git.md` §8).

5. Résumé des changements : par défaut, ne réinjecte qu'un **résumé** du diff (fichiers touchés +
   volume), **pas le diff détaillé**, qui gonfle inutilement le contexte. `glab mr diff` n'expose
   pas de `--stat` natif dans cette version ; dérive le résumé localement en pipant le diff brut
   dans `git apply` :
   - stat lisible (fichiers + `+`/`-` par fichier + total) : `glab mr diff <cible> --raw | git apply --stat` ;
   - format machine (`ajouts  retraits  fichier` par ligne) : `glab mr diff <cible> --raw | git apply --numstat`.

   Ne récupère le **diff détaillé** (`glab mr diff <cible>`) que sur **demande explicite** de
   l'utilisateur, ou si un point précis exige de lire les lignes modifiées — et même alors, cible le
   fichier concerné et résume, ne recopie pas tout.

6. Threads non résolus : si `blocking_discussions_resolved` vaut `false`, signale-le. Pour le détail
   éventuel, tu peux lire les discussions en **lecture seule** via
   `glab api "projects/:id/merge_requests/<iid>/discussions"` (compte les threads `resolvable` non
   `resolved`) — sans jamais y répondre ni les résoudre.

7. Rends un **compte rendu Markdown** :
   - en-tête : `!<iid> — <titre>` · état (Draft/Ready, opened/merged) · `<source> → <target>` · auteur ;
   - **Aptitude au merge** : mergeable ? conflits ? threads bloquants ? pipeline ? ticket lié ?
     (une ligne par point, ✅/⚠️/❌) ;
   - **Changements** : N fichiers, résumé qualitatif ;
   - **Points d'attention pour la revue** : ce qu'un relecteur humain devrait vérifier — **pas** une
     décision de merge.
   Termine en rappelant explicitement que **le merge est une action humaine** : tu ne le fais pas,
   et tu proposes, si l'utilisateur le décide, de le faire lui-même dans l'UI GitLab ou via
   `glab mr merge` **de sa propre main**.

N'exécute **aucune** commande d'écriture : ni `glab mr merge`/`close`/`approve`/`update`, ni
`glab issue update`/`set-status`, ni `git push`. En cas de doute, abstiens-toi et demande.
