---
description: Commit automatique + /ticket-finish enchaînés — clôture le ticket courant sans blocage manuel
argument-hint: "[issue-iid] (optionnel si le nom de la branche courante le contient déjà)"
allowed-tools: Bash(git:*), Bash(gh:*), Bash(bash:*)
---

Tu vas clôturer le ticket courant **en une seule action** : committer les changements en attente
(message généré, sans confirmation) puis enchaîner **`/ticket-finish`** (push + PR prête + état
« En revue » + log du temps + **merge**). C'est le pendant « zéro friction » de `/ticket-finish`,
pensé pour la boucle d'orchestration : là où `/ticket-finish` suppose un commit déjà fait et demande
confirmation avant d'en créer un, `/ticket-ship` **commite d'office** ce qui est en attente puis
délègue la suite à `/ticket-finish`.

⚠ **Depuis #418 (chantier #413), « clore » veut dire « merger ».** La chaîne va jusqu'au bout, ce
qui a un prix en temps de mur : le pipeline naît **après** la PR et tourne 2-4 min, donc la commande
ne rend plus la main dans la seconde qui suit le commit. L'attente est **bornée** (15 min) et
**annoncée** pendant qu'elle dure. Elle n'est pas non plus une promesse : un pipeline rouge ou un
conflit laisse la PR **ouverte** et le ticket **« En revue »**, et c'est un état normal — jamais un
✅ global.

⚠ **En run autonome, cette attente se paie sur le quota du run** : une session pilotée par
`/orchestrate` reste ouverte 2-4 min sans rien faire. Le lot 5 du même chantier (#419) part de
l'hypothèse inverse — « aucune session ne merge, aucune n'attend un pipeline », le pilote tenant sa
propre file de merge — et c'est **lui** qui possède le prompt des sessions de run, donc lui qui
arbitre. Les deux mécanismes ne se marchent pas dessus pour autant : une PR mergée ici n'entre
jamais dans la file du pilote (elle n'est plus « En revue »), et une PR laissée ouverte y entre
normalement. Ne te dispense pas de l'attente de ton propre chef.

Cette commande est autosuffisante (réf. complète `docs/10-workflow-git.md`, à n'ouvrir qu'en cas de
doute). Les **garde-fous** priment sur l'automatisation : suis les étapes dans l'ordre et
**arrête-toi (en expliquant pourquoi)** dès qu'un contrôle échoue, plutôt que de forcer la suite.

1. Détermine l'IID du ticket : utilise `$ARGUMENTS` s'il est fourni, sinon extrais-le du nom de la
   branche courante (`git branch --show-current`, motif `<type>/<iid>-<slug>`). Si aucun IID ne
   peut être déterminé, demande-le à l'utilisateur avant de continuer.

2. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Si ça échoue, arrête-toi et
   relaie son message : il nomme la commande d'authentification de la forge active (`gh auth login`,
).

3. **Garde-fou « jamais sur `main` ».** Vérifie la branche courante (`git branch --show-current`).
   Si c'est `main` (ou `master`), **arrête-toi immédiatement** : on ne committe jamais sur `main`.
   Rappelle qu'il faut démarrer un ticket (`/ticket-start <iid>`) pour obtenir une branche.

4. **Garde-fou de clôture : ce ticket est-il bien celui de la session ?** Le contrôle vient **avant
   le commit**, et pas seulement avant le push : le message généré à l'étape 6 porte
   `Closes #<iid>`, donc un iid étranger ferait **fermer le ticket d'un autre** au merge.
   ```
   bash scripts/gitlab/lib.sh close-guard <iid> || verdict=$?
   ```
   Le helper n'écrit rien ; son verdict, lui, **arrête la commande** :
   - `0` → cohérent, poursuis.
   - `3` → la branche courante porte un **autre** ticket : **arrête-toi**, dis lequel, et propose
     de shipper *celui-là* ou de revenir sur la branche du ticket visé
     (`bash scripts/gitlab/lib.sh branch-for <iid>`).
   - `4` → le ticket est assigné à **quelqu'un d'autre** : **arrête-toi** et nomme la personne.
   - `5` → branche sans iid (nom hors convention) : **arrête-toi**, la cohérence est invérifiable
     (le cas `main` est déjà refusé à l'étape 3).
   - `1` → verdict **partiel** (ticket illisible) : le contrôle local est passé, signale-le et
     poursuis.
   Un refus n'est **franchissable que sur demande explicite** de l'utilisateur (reprise assumée
   d'un ticket laissé en plan), jamais en silence — et il est alors rappelé dans le résumé final.
   `/ticket-finish` rejouera ce même contrôle à son étape 3 : c'est voulu (il est sans effet de
   bord et reste ainsi autosuffisant quand on l'appelle seul).

5. **Contrôle de l'arbre de travail** (`git status --porcelain`) — deux refus possibles :
   - **Arbre vide** (aucun changement en attente) : arrête-toi. Il n'y a rien à committer ; si le
     travail est déjà committé, c'est `/ticket-finish` qu'il faut lancer, pas `/ticket-ship`.
   - **Conflit en cours** (fusion/rebase non résolu : `git ls-files --unmerged` non vide, ou lignes
     `UU`/`AA`/`DD`/`AU`/`UA`/`DU`/`UD` dans `git status --porcelain`) : arrête-toi et demande à
     l'utilisateur de résoudre le conflit d'abord. Ne committe jamais un arbre en conflit.

6. **Commit automatique, sans confirmation** (choix explicite du ticket #34 : zéro blocage manuel,
   cohérent avec l'auto-estimation du temps de `/ticket-finish`). Ne demande **pas** de validation
   du message — mais **montre** ce que tu committes (transparence a posteriori) :
   - Affiche un résumé : `git diff --stat HEAD` (inclut le staged) et la liste des fichiers.
   - Stage tout ce qui est en attente : `git add -A`.
   - Rédige un message **Conventional Commits** d'après la portée réelle du diff :
     - en-tête `<type>(<scope>): <description impérative>` — `type` ∈
       `feat`/`fix`/`chore`/`docs`/`refactor`/`test`/`ci`/`build`/`perf` (cohérent avec le préfixe
       de branche : `feat/`→`feat`, `fix/`→`fix`, `chore/`→`chore`, `docs/`→`docs`), `scope`
       optionnel (module/dossier concerné) ;
     - corps optionnel (le *pourquoi*, pas le *quoi*) ;
     - pied **`Closes #<iid>`** — `/ticket-ship` est l'action terminale du cycle de dev, donc le
       commit porte `Closes` (et non `Refs`) : GitLab fermera le ticket au merge.
   - Committe directement, **hook `commit-msg` respecté** (il valide en-tête + `Closes #<iid>`).
     Le message passe par un **fichier** : écris-le avec l'outil `Write` dans ton scratchpad de
     session — jamais un heredoc, jamais `-m "$(…)"`, la couche permissions découpant une commande
     sur ses sauts de ligne et ne matchant aucune substitution (#233). Puis :
     ```
     git commit -F <fichier>
     ```
     **N'utilise jamais `--no-verify`** : si le hook refuse le message, corrige le message et
     réessaie — ne le contourne pas.

7. **Enchaîne `/ticket-finish`.** Une fois le commit créé, l'arbre est propre : invoque la commande
   **`/ticket-finish`** (sans argument — elle relira l'IID depuis la branche — ou passe `<iid>`).
   Elle prend le relais pour : push de la branche (jamais de `--force`), création/mise à jour de la
   PR avec `Closes #<iid>` et sa **checklist cochée sur ce qui est vérifié** (conventions,
   tests/doc d'après le diff, pipeline verte), **passage de la PR en « prête »** (`gh pr ready`, sans
   demander : une PR qu'on s'apprête à merger n'est pas un brouillon), passage de l'**état** à
   « En revue », **log automatique du temps** (estimé d'après la portée du travail), puis
   l'**attente du pipeline et le merge** par `merge-mr` (#418). **Ne ré-implémente aucune de ces
   étapes ici** — surtout pas le merge : `/ticket-finish` en est la source unique, et son étape de
   commit sera sans objet (arbre déjà propre), elle passera directement au push.
   **Lis son verdict de merge** : c'est lui qui ouvre ton résumé (étape 9), et c'est aussi lui qui
   dit, à l'étape 8, si le lot que tu viens de shipper est mergé ou seulement « En revue ».

8. **Sous-ticket d'un parent de suivi ?** Vérifie : `bash scripts/gitlab/lib.sh parent-of <iid>`.
   Si un parent est trouvé (convention `docs/10-workflow-git.md` §5.1), prépare l'**annonce de la
   suite** pour le résumé final :
   - Liste les lots : `bash scripts/gitlab/lib.sh subtickets <iid-parent>`. Profites-en pour
     **cocher** dans la checklist du parent les lots à l'état « Terminé » encore décochés. Le lot
     que tu viens de shipper en fait partie **s'il a été mergé** (verdict `0` à l'étape 7) : le
     workflow `issues: closed` (#377) est asynchrone, donc `subtickets` peut encore le rendre
     « En revue » quelques secondes après le merge — coche-le sur le **verdict du merge**, qui est
     l'information fraîche, et jamais l'inverse (un lot **non** mergé ne se coche pas).
     **Relis et réécris la description uniquement via les helpers** :
     `bash scripts/gitlab/lib.sh get-description <iid-parent> > <fichier>`, tu édites le fichier,
     puis `bash scripts/gitlab/lib.sh set-description <iid-parent> <fichier>`. N'improvise
     **jamais** une lecture du type `gh issue view --json body | python` : elle corrompt
     l'UTF-8 en mojibake (« â€” » au lieu de « — ») et l'a déjà repoussé dans un parent (#141).
     Mise à jour idempotente : ne **décoche jamais** une case déjà cochée.
   - Demande les lots ouverts que rien ne bloque :
     `bash scripts/gitlab/lib.sh startables <iid-parent>` (les lots marqués « (parallèle) » ne se
     bloquent pas entre eux — docs/10 §5.1). S'il en reste, annonce-les **démarrables dès
     maintenant** — « prochain lot : `/ticket-start <iid-suivant>` (rien à attendre : le lot shippé
     est mergé, ou au pire « En revue », et les lots sont mergeables seuls depuis `main`) » — et,
     s'il y en a plusieurs, précise qu'ils sont **prenables en parallèle** par d'autres personnes.
   - Si le lot shippé est le **dernier encore ouvert**, annonce le parent **fermable** — dès
     maintenant si le merge a eu lieu (toutes les cases cochées, y compris le lot tests), après le
     merge sinon. Sa fermeture reste une décision humaine/orchestrateur : `/ticket-ship` ne ferme
     rien, pas même le parent — merger la PR d'un lot n'est pas fermer son parent.

9. Résumé final : reprends le résumé produit par `/ticket-finish` — **verdict du merge en tête**
   (mergé, ou la cause **telle que `merge-mr` l'a rendue** et la suite qu'elle appelle), lien de la
   PR, temps loggé — et préfixe-le du **commit créé** (hash court + en-tête). Pour un sous-ticket,
   ajoute l'annonce de l'étape 8 (prochain lot démarrable dès maintenant, ou parent fermable).
   **Jamais de ✅ global** : un ticket dont la PR est restée ouverte sur un pipeline rouge n'est pas
   « shippé avec une réserve », il est **inachevé**, et le résumé doit le dire avec ce mot-là — un
   verdict qui masque son blocage est exactement ce que #303 a supprimé ailleurs.
   Rappelle qu'**aucun merge non vérifié** n'a lieu (#417, chantier #413) : `/ticket-ship` ne ferme
   ni ne force-push jamais une PR, et ne merge **jamais hors de `merge-mr`**, qui éprouve ses quatre
   prérequis avant de merger.
