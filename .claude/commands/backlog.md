---
description: Vue d'ensemble du backlog GitLab groupée par statut natif (+ ce qui attend une revue / est prêt à merger)
argument-hint: "[opened|all]  (défaut : opened — les tickets ouverts)"
allowed-tools: Bash(git:*), Bash(glab:*), Bash(bash:*)
---

Commande **de supervision, en lecture seule** : tu produis un tableau de bord du backlog pour aider
au pilotage (avant que la Control Tower n'existe). Tu **ne modifies rien** — ni statut, ni label,
ni assignation, ni MR. Suis le cycle de vie décrit dans @docs/10-workflow-git.md §3/§5.

1. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Arrête-toi si non authentifié.

2. Récupère le backlog avec le statut natif via le helper :
   `bash scripts/gitlab/lib.sh backlog <state>` où `<state>` vaut `opened` (défaut, si `$ARGUMENTS`
   est vide) ou `all` (si l'utilisateur passe `all`). Le JSON renvoyé contient, par work item :
   `iid`, `title`, le **statut natif** (`widgets[].status.name`), les labels
   (`widgets[].labels.nodes[].title` → familles `type::`/`agent::`/`prio::`) et les assignés
   (`widgets[].assignees.nodes[].username`). Les widgets sans donnée sont des `{}` à ignorer.

3. Récupère les Merge Requests ouvertes pour enrichir la vue « prêt à merger » :
   `glab mr list --output json`. Pour chaque MR, note `iid`, `title`, `state`, le caractère brouillon
   (`draft`/`work_in_progress`) et `source_branch`. Rattache une MR à son ticket via l'`iid` extrait
   du nom de branche (`<type>/<iid>-<slug>`).

4. Rends un **compte rendu Markdown** clair, groupé par **statut natif**, dans cet ordre (le plus
   actionnable d'abord) :
   1. **🔍 En revue** — action humaine attendue (merge). Pour chaque ticket, affiche
      `#<iid> — <titre>` puis, si une MR est rattachée : son état (Draft/Ready) et un lien
      `!<iid-mr>`. C'est la section « prêt à merger / attend une revue ».
   2. **🛠 En cours**
   3. **📋 À faire**
   4. Les autres statuts éventuels (`Terminé`/`Abandonné`/`Doublon`) **uniquement** si `all` a été
      demandé.
   Pour chaque ticket d'une section, montre sur une ligne : `#<iid>`, le titre, le label `agent::`,
   le `prio::`, et l'assigné s'il y en a un. Trie chaque section par `prio::` (haute → moyenne →
   basse) puis par iid.

5. Termine par une **synthèse chiffrée** : nombre de tickets par statut, et un rappel des actions
   suggérées (ex. « 2 en revue à merger », « 3 à faire non assignés »). N'invente pas de chiffres :
   ne compte que ce que le JSON contient. Rappelle que le merge reste une décision humaine et
   propose `/mr-review <mr>` pour inspecter une MR précise avant de merger.

Ne lance aucune commande d'écriture (`glab issue update`, `mr merge`, `set-status`, `git push`…) :
cette commande observe, elle n'agit pas.
