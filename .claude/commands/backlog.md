---
description: Vue d'ensemble du backlog groupée par état du cycle de vie (+ ce qui attend une revue / est prêt à merger)
argument-hint: "[opened|all]  (défaut : opened — les tickets ouverts)"
allowed-tools: Bash(git:*), Bash(gh:*), Bash(bash:*)
---

Commande **de supervision, en lecture seule** : tu produis un tableau de bord du backlog pour aider
au pilotage (avant que la Control Tower n'existe). Tu **ne modifies rien** — ni état, ni label,
ni assignation, ni PR. Le cycle de vie (champ Status du projet) est résumé ci-dessous — cette commande est
autosuffisante ; réf. complète `docs/10-workflow-git.md` §3/§5, non chargée automatiquement, à
n'ouvrir qu'en cas de doute.

1. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Arrête-toi si non authentifié.

2. Récupère le backlog (état du cycle de vie compris) en **table plate compacte** via le helper :
   `bash scripts/gitlab/lib.sh backlog-table <state>` où `<state>` vaut `opened` (défaut, si
   `$ARGUMENTS` est vide) ou `all` (si l'utilisateur passe `all`). Sortie **TSV** : une ligne
   d'en-tête préfixée `#` (à ignorer) puis une ligne par ticket, colonnes séparées par TABULATION :
   `iid`, `statut` (le **libellé** du cycle de vie — « À faire », « En revue »… — lu dans le champ
   Status du projet, jamais un slug ; `-` si le ticket n'a pas d'état, cf. étape 4), `prio`,
   `agent`, `assigne`, `titre`. Les valeurs
   `prio`/`agent` sont le suffixe nu du label (`moyenne`, `devops`, familles `prio::`/`agent::`) ;
   un champ `prio`/`agent`/`assigne` absent vaut `-`. Cette projection réinjecte beaucoup moins que
   le JSON imbriqué ; le JSON brut complet reste disponible via
   `bash scripts/gitlab/lib.sh backlog <state>` si tu as besoin d'un détail non projeté.

   La colonne `assigne` porte l'**anti-collision du travail à plusieurs** : `-` = ticket **libre**,
   sinon le ticket est **pris** par cette personne. Un ticket est **libre à prendre** quand il est
   à la fois à l'état « À faire » **et** sans assigné ; un « À faire » déjà assigné est réservé
   (quelqu'un se l'est attribué sans l'avoir démarré), et un « En cours » assigné est en travail —
   `/ticket-start` refuse de le démarrer.

3. Récupère la **file de revue** — les PR ouvertes en attente de relecture :
   `bash scripts/gitlab/lib.sh review-queue`. Sortie **TSV**, une ligne d'en-tête préfixée `#` (à
   ignorer) puis une ligne par PR, **la plus ancienne d'abord** : `mr` (le numéro — le verbe et ses
   colonnes gardent le vocabulaire GitLab, c'est le contrat de `lib.sh`), `age_j` (jours écoulés
   depuis la création), `etat` (`draft`/`ready`), `pipeline` (statut du dernier pipeline, `-` si
   aucun), `auteur`, `relecteur` (CSV des relecteurs posés, `-` si personne), `branche`, `titre`.
   Rattache une PR à son ticket via l'`iid` extrait du nom de branche (`<type>/<iid>-<slug>`).

4. Rends un **compte rendu Markdown** clair. Commence par le bloc **⏳ PR en attente de revue**,
   **en tête de sortie** : c'est lui qui déclenche la relecture. Une ligne par PR de la file, dans
   l'ordre rendu (la plus ancienne d'abord) : `PR #<numéro>` — titre, ticket `#<iid>`, **ancienneté**
   (`ouverte depuis <age_j> j`), état `draft`/`ready`, pipeline, et le relecteur s'il en a été posé
   un (`à relire par @<relecteur>`, sinon **« aucun relecteur »** — ce n'est **pas** une anomalie :
   depuis #196 aucune commande n'en pose, c'est donc le cas normal, et la PR n'en attend pas moins
   une relecture). Écris toujours **`PR #<numéro>`** en toutes lettres : sur GitHub, issues et PR
   partagent la notation `#<n>` (là où GitLab distinguait `#` et `!`), donc seul le préfixe évite de
   confondre un ticket et une PR. Mets en évidence les PR les plus **anciennes** (celles qui
   traînent) et celles au **pipeline rouge** (non mergeables en l'état). La revue est
   **best-effort** : c'est cette file qui appelle un relecteur, aucune approbation n'est obligatoire
   et ce que le merge exige n'est pas un avis mais les prérequis de `merge-mr` — **aucun merge non
   vérifié** (#417, chantier #413).
   Enchaîne ensuite sur le backlog groupé par **état**, dans cet ordre (le plus
   actionnable d'abord) :
   1. **🔍 En revue** — action humaine attendue (merge). Pour chaque ticket, affiche
      `#<iid> — <titre>` puis, si une PR est rattachée : son état (Draft/Ready) et un lien
      `PR #<numéro>`. C'est la section « prêt à merger / attend une revue » — vue côté **tickets**,
      là où le bloc ⏳ de tête est la vue côté **PR** ; ne répète pas ici l'ancienneté ni le
      relecteur. Signale un ticket « En revue » **sans PR** (dérive — cf. `doctor.sh`).
   2. **🛠 En cours** — c'est ici que se lit qui travaille sur quoi : mets l'assigné en évidence
      (`pris par @<assigne>`), et signale les tickets « En cours » **sans** assigné, anomalie à
      corriger (état posé à la main ou assignation perdue).
   3. **📋 À faire**, en deux sous-groupes dans cet ordre : **🆓 Libres** (aucun assigné — les
      tickets qu'on peut prendre tout de suite, c'est le geste quotidien de répartition) puis
      **🔒 Réservés** (`pris par @<assigne>` — déjà attribués, ne pas les prendre sans en parler).
   4. Les autres états éventuels (`Terminé`/`Abandonné`/`Doublon`) **uniquement** si `all` a été
      demandé.
   5. **⚠ Sans état** — les tickets dont la colonne `statut` vaut `-`. L'état vit sur l'**item de
      projet** et non sur l'issue (docs/10 §3), donc deux causes se cachent derrière ce `-` : le
      ticket est **hors du projet**, ou son Status est **vide**. Dans les deux cas il n'est sur
      aucune colonne et sort de tous les comptes — `queue.sh` ne le verra jamais. Signale-les au
      lieu de les taire, et renvoie vers `bash scripts/gitlab/doctor.sh`, qui distingue les deux
      causes ; la réparation est `bash scripts/gitlab/lib.sh project-add <iid> "<état>"`.
   Pour chaque ticket d'une section, montre sur une ligne : `#<iid>`, le titre, le label `agent::`,
   le `prio::`, et l'**appartenance** — `pris par @<assigne>` ou `libre`. Trie chaque section par
   `prio::` (haute → moyenne → basse) puis par iid.

5. Termine par une **synthèse chiffrée** : nombre de tickets par état, **combien sont libres**
   (« À faire » sans assigné), la répartition des « En cours » par personne, ainsi que le nombre de
   **PR en attente de revue** et l'ancienneté de la plus vieille, plus un rappel des actions
   suggérées (ex. « 2 PR à relire, la plus ancienne depuis 4 j », « 3 tickets libres à prendre »).
   N'invente pas de chiffres : ne compte que ce que la table contient. Rappelle qu'**aucun merge
   non vérifié** n'a lieu — un merge passe par `bash scripts/gitlab/lib.sh merge-mr <iid>`, qui
   éprouve ses prérequis — et propose `/mr-review <numéro>` pour inspecter une PR précise avant
   de merger.

Ne lance aucune commande d'écriture (`gh issue edit`, `gh pr merge`, `set-workflow`, `git push`…) :
cette commande observe, elle n'agit pas.
