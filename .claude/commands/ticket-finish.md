---
description: Termine le travail sur le ticket courant (push + MR + état « En revue »)
argument-hint: "[issue-iid] (optionnel si le nom de la branche courante le contient déjà)"
allowed-tools: Bash(git:*), Bash(glab:*), Bash(bash:*)
---

Tu vas clôturer le cycle de développement de la branche courante selon les règles de Maestro
(résumées ci-dessous — cette commande est autosuffisante ; réf. complète `docs/10-workflow-git.md`,
non chargée automatiquement, à n'ouvrir qu'en cas de doute). Arrête-toi et demande confirmation
avant toute action qui modifie l'état partagé (push, création/mise à jour de MR) si un point n'est
pas clair.

1. Détermine l'IID du ticket : utilise `$ARGUMENTS` s'il est fourni, sinon extrais-le du nom
   de la branche courante (`git branch --show-current`, motif `<type>/<iid>-<slug>`). Si
   aucun IID ne peut être déterminé, demande-le à l'utilisateur.

2. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require` ; arrête-toi si non authentifié.

3. **Garde-fou de clôture : cette session traite-t-elle bien ce ticket ?** À plusieurs, rien
   n'empêchait jusqu'ici un `/ticket-finish <iid>` lancé depuis la branche d'un *autre* ticket de
   basculer ce ticket-là « En revue », d'y poser une MR et le temps d'un travail qui n'est pas le
   sien. Ce contrôle vient **avant toute écriture** (commit, push, MR, état, temps) :
   ```
   bash scripts/gitlab/lib.sh close-guard <iid> || verdict=$?
   ```
   Le helper est **consultatif** — il n'écrit rien — mais son verdict, lui, est **bloquant** ici :
   - `0` → cohérent (la branche porte bien ce ticket, qui n'appartient pas à quelqu'un d'autre) :
     poursuis sans rien dire de plus.
   - `3` → **arrête-toi** : la branche courante porte un **autre** ticket. Dis lequel, et propose
     soit de clôturer *ce* ticket-là, soit de revenir sur la branche du ticket visé
     (`bash scripts/gitlab/lib.sh branch-for <iid>`).
   - `4` → **arrête-toi** : le ticket est assigné à **quelqu'un d'autre**. Nomme la personne : le
     clôturer à sa place lui poserait une MR et un temps qu'elle n'a pas demandés.
   - `5` → **arrête-toi** : la branche ne porte aucun iid (`main`, nom hors convention), donc la
     cohérence est invérifiable — et sur `main` il n'y a de toute façon rien à clôturer.
   - `1` → verdict **partiel** (ticket illisible, GitLab injoignable) : le contrôle local est
     passé, **signale-le** et poursuis.
   Un refus (`3`/`4`/`5`) n'est **franchissable que sur demande explicite** de l'utilisateur — par
   exemple la reprise assumée d'un ticket laissé en plan par quelqu'un qui a lâché le sujet. Dans
   ce cas seulement, continue et mentionne-le dans le résumé final. Jamais de contournement
   silencieux.

4. Regarde `git status --porcelain`. S'il reste des changements non commités :
   - montre un résumé (`git diff --stat`),
   - propose un message de commit **Conventional Commits** : en-tête `<type>(<scope>): <résumé impératif>`
     (types : `feat`/`fix`/`chore`/`docs`/`refactor`/`test`/`ci`/`build`/`perf` ; `scope` optionnel)
     et pied `Refs #<iid>` (le hook `commit-msg` refuse tout message hors convention ; détail
     `docs/10-workflow-git.md` §2),
   - demande confirmation à l'utilisateur avant de committer.
   Le message passe par un **fichier** (écrit avec l'outil `Write`, dans ton scratchpad de session)
   puis `git commit -F <fichier>` — jamais `-m` sur plusieurs lignes ni `-m "$(…)"` : même refus
   que pour la description de MR (#233).
   Ne commite jamais silencieusement sans montrer ce qui va être committé.

5. **Filet CI local** — avant de pousser, rejoue en local ce que le pipeline de la MR jouera. Ne
   cherche pas toi-même quel outil s'applique : `scripts/ci/local.sh` est la **source unique** des
   contrôles locaux (#214, `docs/10-workflow-git.md` §8.4), il lit les jobs dans `.gitlab-ci.yml`
   et déduit du diff ce qui les concerne.
   ```
   bash scripts/ci/local.sh
   ```
   Par défaut `pytest` ne joue que les **suites concernées par le diff**, sans seuil de couverture
   (verdict annoncé PARTIEL) : ~40 s au lieu de ~10 min. **Ne le passe pas en `--complet`** et
   n'invente aucune autre recette — le verdict complet est celui du pipeline de la MR (#165), pas
   le tien.
   **Best-effort, jamais bloquant** : un outil absent rend son job `IGNORÉ`, et un job rouge écrit
   son journal sous `.maestro/ci-local/<job>.log` (chemin relatif, cité par le script). Si l'échec
   vient de ton diff et se corrige en une passe, corrige-le et reprends à l'étape 4 ; sinon
   **signale-le dans le résumé final** et poursuis la clôture — c'est la MR qui portera le verdict.

6. **Avant de pousser, regarde si la branche a pris du retard sur `origin/main`** — à plusieurs,
   `CLAUDE.md`, `docs/10-workflow-git.md` et `scripts/gitlab/lib.sh` sont touchés par presque tous
   les tickets, et sans ce contrôle le conflit n'apparaît que dans l'UI GitLab, après coup :
   ```
   bash scripts/gitlab/lib.sh behind-main || echo "verdict=$? (3=en retard, 4=+conflit probable)"
   ```
   Le helper est **consultatif** : il n'écrit rien et ne rebase jamais, et son code de retour
   n'interrompt donc pas la clôture (`0` à jour, `3` en retard sans fichier commun, `4` en retard
   **avec conflit probable** — les fichiers modifiés des deux côtés sont listés). **Ne rebase
   jamais de toi-même** : un rebase réécrit
   l'historique d'une branche déjà poussée et appellerait un force-push, interdit par les
   garde-fous (`docs/10-workflow-git.md` §6). Selon le constat :
   - `0` → rien à dire, poursuis.
   - `3` → **signale-le** dans le résumé final (« n commits de retard, rebase serein possible »)
     et poursuis la clôture : GitLab mergera sans difficulté.
   - `4` → **signale-le et propose le rebase à l'utilisateur** (`git fetch origin main && git
     rebase origin/main`), en nommant les fichiers concernés. La clôture **n'est pas bloquée** :
     s'il ne se prononce pas, pousse quand même et laisse la mention dans le résumé — c'est le
     relecteur ou l'auteur qui tranchera, la MR affichant le conflit.

7. **Assure le runner CI local en ligne** : les runners partagés étant
   désactivés (#135), le runner de projet local est l'unique cible ; s'il est hors ligne, le
   pipeline déclenché par le push resterait `pending` et bloquerait le merge (pipeline vert
   requis). Lance le helper idempotent — **son échec n'interrompt jamais la clôture**, il est
   seulement signalé (voir `docs/10-workflow-git.md` §8) :
   ```
   bash scripts/gitlab/ensure-runner.sh || echo "⚠ runner local non démarré — clôture poursuivie, à surveiller sur la MR"
   ```
   **Ramasse au passage les restes du pipeline précédent** (#166) : un runner tué en cours de job
   laisse ses conteneurs éphémères derrière lui, et personne ne les enlève. Même statut que
   ci-dessus — **best-effort, jamais bloquant**, et silencieux quand il n'y a rien à faire. Il
   n'est **pas** conditionné à ce qui précède : le ménage est local à la machine, il vaut aussi
   quand c'est le runner partagé qui sert la CI.
   ```
   bash scripts/gitlab/clean-runner-containers.sh || echo "⚠ ménage des conteneurs CI incomplet — clôture poursuivie"
   ```
   Puis pousse la branche : `git push -u origin <nom-de-la-branche>` — **écris le nom lu à
   l'étape 1**, jamais `$(git branch --show-current)` : la couche permissions ne sait matcher
   aucune **substitution de commande**, et refuserait un `git push` par ailleurs autorisé (#233).
   Ne fais jamais de `--force` ici — si le push est rejeté, arrête-toi et explique pourquoi plutôt
   que de forcer.
   Si le push **reste bloqué** sur une demande d'identifiants (typique sous Windows avec Git
   Credential Manager), relance-le en forçant `glab` comme credential helper :
   `GIT_TERMINAL_PROMPT=0 git -c credential.helper='' -c credential.helper='!glab auth git-credential' push -u origin <nom-de-la-branche>`
   (ce repli garde un **préfixe de variable d'environnement**, immatchable lui aussi — c'est le
   domaine de #235, pas de ce lot : s'il est refusé, signale-le au lieu d'inventer une variante).

8. Évalue la **checklist de definition of done** de la MR (les quatre cases du template
   `.gitlab/merge_request_templates/Default.md`) : pour chacune, détermine si tu peux la cocher
   (`- [x]`) parce que tu l'as **effectivement vérifiée**, ou si elle reste vide (`- [ ]`). La
   checklist est un constat, pas un formulaire — et le merge reste une décision humaine.
   - **Conventions de branche/commit** : nom de branche au motif `<type>/<iid>-<slug>` et messages
     de `git log main..HEAD` conformes (Conventional Commits + `Refs`/`Closes #<iid>`). Le hook
     `commit-msg` les valide déjà, mais un `--no-verify` a pu passer : re-vérifie rapidement.
   - **Tests ajoutés/mis à jour si applicable** : juge d'après le diff de la branche
     (`git diff main...HEAD --stat`) — des tests touchés avec le code, ou un diff sans surface à
     tester (doc, config, prompts…) → coche ; du code applicatif sans test associé → laisse vide.
   - **Documentation mise à jour si applicable** : même logique, d'après le diff.
   - **Pipeline CI verte (si configurée)** : ne coche que si le dernier pipeline est **réellement
     réussi** au moment de la vérification (`bash scripts/gitlab/lib.sh pipeline-latest <branche>`,
     qui retrouve aussi le pipeline porté par la MR). En cours, échoué ou absent → laisse vide.
     **Une case vide est le cas NORMAL ici** : la CI ne se déclenche qu'à partir de la MR (#165,
     docs/10 §8), donc à la première clôture d'un ticket **aucun pipeline n'existe encore** à ce
     stade — il naîtra de l'étape 8. Le relecteur verra le verdict sur la MR ; n'attends pas.

9. **Crée (ou mets à jour) la MR — la description passe toujours par un FICHIER.** Jamais de
   description sur la ligne de commande : elle fait par nature plusieurs lignes, la couche
   permissions découpe une commande sur ses sauts de ligne et la refuse, puis refuse aussi les deux
   replis naturels (`--description "$(cat …)"`, `D="$(cat …)"; … "$D"`) — aucune règle ne peut
   matcher une **substitution de commande**. C'est ce qui a fait tomber 8 sessions autonomes sur 16
   (#233), et toujours ici, sur la **dernière action du ticket** : tout est commité, rien ne le
   déclare. Le fichier n'est pas un contournement, c'est la forme normale (#232).

   1. **Une MR ouverte existe-t-elle déjà pour cette branche ?**
      ```
      bash scripts/gitlab/lib.sh mr-iid
      ```
      (sans argument : la branche courante ; code 1 + message si aucune MR ouverte).
   2. **Prépare le fichier de description**, dans ton répertoire de scratchpad de session (ce n'est
      pas un livrable, il n'a rien à faire dans le worktree). **Écris-le avec l'outil `Write`** —
      pas avec `cat`/`echo`/un heredoc, qui rejoueraient exactement le problème que cette étape
      évite.
      - **Aucune MR** : contenu neuf — `Closes #<iid>`, une ligne vide, puis la section
        `## Checklist` **telle qu'évaluée à l'étape 8** (chaque case en `[x]` ou `[ ]` selon le
        constat réel) :
        ```
        Closes #<iid>

        ## Checklist
        - [x] Respecte les conventions de branche/commit (docs/10-workflow-git.md)
        - [ ] Tests ajoutés/mis à jour si applicable
        - [x] Documentation mise à jour si applicable
        - [ ] Pipeline CI verte (si configurée)
        ```
      - **MR déjà ouverte** : pars de l'**existant**, la mise à jour remplaçant la description
        entière. Relis-la **via le helper** — `bash scripts/gitlab/lib.sh get-mr-description <mr> >
        <fichier>` — puis édite le fichier de façon **idempotente** : modifie **uniquement** l'état
        des cases de la section `## Checklist` (jamais le reste, notamment le `Closes #<iid>`),
        coche celles vérifiées à l'étape 8, et **ne décoche jamais** une case déjà cochée (un
        humain a pu la cocher). Si la section `## Checklist` manque, ajoute-la en fin de
        description. Si rien ne change, passe directement au point 4. N'improvise **jamais** une
        lecture du type `glab mr view --output json | python` : elle corrompt l'UTF-8 en mojibake
        (« â€” » au lieu de « — ») — voir #141.
   3. **Un seul appel, plat et court**, dans les deux cas :
      ```
      bash scripts/gitlab/lib.sh create-mr <iid> <fichier>
      ```
      Le helper ouvre la MR en **Draft** vers `main` (`--remove-source-branch`), **titre lu depuis
      le ticket**, description lue depuis le fichier, et imprime son URL. Il est **idempotent** :
      si une MR ouverte existe déjà pour la branche, il met sa description à jour au lieu
      d'échouer.
   4. **Si la MR existait déjà et qu'elle est en Draft** : demande à l'utilisateur si le travail
      est réellement terminé et prêt pour revue ; si oui, `glab mr update <mr> --ready`. Si elle
      n'est **plus en Draft**, ne fais rien de plus sur la MR. Une MR **fraîchement créée** reste
      en Draft : c'est voulu, le passage en « prête » est un geste explicite.

10. **Ne pose aucun relecteur sur la MR** (#196) — la désignation d'un relecteur est un **geste
   humain**, jamais automatique : n'appelle pas `lib.sh set-reviewer` et n'utilise pas
   `glab mr update --reviewer`. Le helper reste disponible pour une pose explicite, sur demande.
   La revue reste **best-effort** — l'approbation n'est pas obligatoire
   (`approvals_before_merge=0`) et le merge reste une décision humaine ; la visibilité des MR en
   attente est portée par la **file de revue** en tête de `/backlog` (la plus ancienne d'abord).

11. Fais passer l'**état** du ticket à « En revue » (le cycle de vie est porté par les labels
   `workflow::*` — voir `docs/10-workflow-git.md` §3) :
   ```
   bash scripts/gitlab/lib.sh set-workflow <iid> "En revue"
   ```
   Le helper résout le work item depuis l'iid et **dérive les GID des six labels par nom** (pas de
   GID en dur), puis ajoute la cible et **retire les cinq autres dans le même appel** — l'exclusion
   mutuelle des labels scopés est Premium, donc rien ne l'assurerait à notre place. Vérifie que la
   commande réussit. Ne touche pas aux labels `agent::*` / `prio::*` / `type::*`.

12. Renseigne le **temps passé** — **estimé automatiquement, sans demander de confirmation** (voir
   `docs/10-workflow-git.md` §3.3) :
   - Vérifie d'abord ce qui est déjà loggé : `bash scripts/gitlab/lib.sh get-time-spent <iid>`
     (secondes). Si le résultat **n'est pas `0`**, du temps a déjà été enregistré — n'en rajoute
     pas (idempotence : ne double pas un cycle déjà loggé) et passe à la suite.
   - Sinon, **estime toi-même l'effort** d'après la **portée réelle du travail** de la branche
     (ampleur du diff, nombre et nature des commits, ce que tu as fait durant la session) — pas le
     temps calendaire écoulé, qui n'est qu'un plafond peu fidèle. Traduis-la en une durée au format
     GitLab (`30m`, `1h`, `2h 30m`, `1d`…).
   - Logge-la directement, sans question :
     ```
     bash scripts/gitlab/lib.sh log-time <iid> "<durée estimée>" "Cycle de dev (start->finish)"
     ```
   - Indique dans le résumé final la durée estimée et loggée (transparence a posteriori).

13. Termine par un résumé : lien de la MR, état (Draft/Ready), le **verdict du filet CI local**
   s'il n'était pas vert (étape 5 — quel job, et pourquoi tu as poussé quand même), le **retard
   éventuel sur `origin/main`** relevé à l'étape 6 (et le rebase proposé si un conflit est probable), les cases
   de la checklist cochées et celles restées vides
   (avec un mot sur pourquoi), le temps loggé le cas échéant, et rappelle que le merge reste une
   action humaine (personne — pas même toi — ne doit merger automatiquement). Si un refus du
   garde-fou de l'étape 3 a été **franchi sur demande explicite**, dis-le en tête du résumé (quel
   motif, et qui l'a demandé).
