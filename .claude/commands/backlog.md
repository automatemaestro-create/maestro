---
description: Vue d'ensemble du backlog GitLab groupée par statut natif (+ ce qui attend une revue / est prêt à merger)
argument-hint: "[opened|all]  (défaut : opened — les tickets ouverts)"
allowed-tools: Bash(git:*), Bash(glab:*), Bash(bash:*)
---

Commande **de supervision, en lecture seule** : tu produis un tableau de bord du backlog pour aider
au pilotage (avant que la Control Tower n'existe). Tu **ne modifies rien** — ni statut, ni label,
ni assignation, ni MR. Le cycle de vie (statut natif) est résumé ci-dessous — cette commande est
autosuffisante ; réf. complète `docs/10-workflow-git.md` §3/§5, non chargée automatiquement, à
n'ouvrir qu'en cas de doute.

1. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Arrête-toi si non authentifié.

2. Récupère le backlog (statut natif compris) en **table plate compacte** via le helper :
   `bash scripts/gitlab/lib.sh backlog-table <state>` où `<state>` vaut `opened` (défaut, si
   `$ARGUMENTS` est vide) ou `all` (si l'utilisateur passe `all`). Sortie **TSV** : une ligne
   d'en-tête préfixée `#` (à ignorer) puis une ligne par ticket, colonnes séparées par TABULATION :
   `iid`, `statut` (**statut natif**), `prio`, `agent`, `assigne`, `titre`. Les valeurs
   `prio`/`agent` sont le suffixe nu du label (`moyenne`, `devops`, familles `prio::`/`agent::`) ;
   un champ `prio`/`agent`/`assigne` absent vaut `-`. Cette projection réinjecte beaucoup moins que
   le JSON imbriqué ; le JSON brut complet reste disponible via
   `bash scripts/gitlab/lib.sh backlog <state>` si tu as besoin d'un détail non projeté.

   La colonne `assigne` porte l'**anti-collision du travail à plusieurs** : `-` = ticket **libre**,
   sinon le ticket est **pris** par cette personne. Un ticket est **libre à prendre** quand il est
   à la fois au statut « À faire » **et** sans assigné ; un « À faire » déjà assigné est réservé
   (quelqu'un se l'est attribué sans l'avoir démarré), et un « En cours » assigné est en travail —
   `/ticket-start` refuse de le démarrer.

3. Récupère la **file de revue** — les MR ouvertes en attente de relecture :
   `bash scripts/gitlab/lib.sh review-queue`. Sortie **TSV**, une ligne d'en-tête préfixée `#` (à
   ignorer) puis une ligne par MR, **la plus ancienne d'abord** : `mr`, `age_j` (jours écoulés
   depuis la création), `etat` (`draft`/`ready`), `pipeline` (statut du dernier pipeline, `-` si
   aucun), `auteur`, `relecteur` (CSV des relecteurs posés, `-` si personne), `branche`, `titre`.
   Rattache une MR à son ticket via l'`iid` extrait du nom de branche (`<type>/<iid>-<slug>`).

4. Rends un **compte rendu Markdown** clair. Commence par le bloc **⏳ MR en attente de revue**,
   **en tête de sortie** : c'est lui qui déclenche la relecture. Une ligne par MR de la file, dans
   l'ordre rendu (la plus ancienne d'abord) : `!<mr>` — titre, `#<iid>` du ticket, **ancienneté**
   (`ouverte depuis <age_j> j`), état `draft`/`ready`, pipeline, et le relecteur (`à relire par
   @<relecteur>`, ou **« aucun relecteur »** — anomalie à signaler, `/ticket-finish` en pose un).
   Mets en évidence les MR les plus **anciennes** (celles qui traînent) et celles au **pipeline
   rouge** (non mergeables en l'état). La revue est **best-effort** : un relecteur désigné n'est pas
   une approbation obligatoire, et le merge reste une décision humaine.
   Enchaîne ensuite sur le backlog groupé par **statut natif**, dans cet ordre (le plus
   actionnable d'abord) :
   1. **🔍 En revue** — action humaine attendue (merge). Pour chaque ticket, affiche
      `#<iid> — <titre>` puis, si une MR est rattachée : son état (Draft/Ready) et un lien
      `!<iid-mr>`. C'est la section « prêt à merger / attend une revue » — vue côté **tickets**,
      là où le bloc ⏳ de tête est la vue côté **MR** ; ne répète pas ici l'ancienneté ni le
      relecteur. Signale un ticket « En revue » **sans MR** (dérive — cf. `doctor.sh`).
   2. **🛠 En cours** — c'est ici que se lit qui travaille sur quoi : mets l'assigné en évidence
      (`pris par @<assigne>`), et signale les tickets « En cours » **sans** assigné, anomalie à
      corriger (statut posé à la main ou assignation perdue).
   3. **📋 À faire**, en deux sous-groupes dans cet ordre : **🆓 Libres** (aucun assigné — les
      tickets qu'on peut prendre tout de suite, c'est le geste quotidien de répartition) puis
      **🔒 Réservés** (`pris par @<assigne>` — déjà attribués, ne pas les prendre sans en parler).
   4. Les autres statuts éventuels (`Terminé`/`Abandonné`/`Doublon`) **uniquement** si `all` a été
      demandé.
   Pour chaque ticket d'une section, montre sur une ligne : `#<iid>`, le titre, le label `agent::`,
   le `prio::`, et l'**appartenance** — `pris par @<assigne>` ou `libre`. Trie chaque section par
   `prio::` (haute → moyenne → basse) puis par iid.

5. Termine par une **synthèse chiffrée** : nombre de tickets par statut, **combien sont libres**
   (« À faire » sans assigné), la répartition des « En cours » par personne, ainsi que le nombre de
   **MR en attente de revue** et l'ancienneté de la plus vieille, plus un rappel des actions
   suggérées (ex. « 2 MR à relire, la plus ancienne depuis 4 j », « 3 tickets libres à prendre »).
   N'invente pas de chiffres : ne compte que ce que la table contient. Rappelle que le merge reste une décision
   humaine et propose `/mr-review <mr>` pour inspecter une MR précise avant de merger.

Ne lance aucune commande d'écriture (`glab issue update`, `mr merge`, `set-status`, `git push`…) :
cette commande observe, elle n'agit pas.
