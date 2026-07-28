---
description: Termine le travail sur le ticket courant (push + MR + statut « En revue »)
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
   basculer ce ticket-là « En revue », d'y poser une MR, un relecteur et le temps d'un travail qui
   n'est pas le sien. Ce contrôle vient **avant toute écriture** (commit, push, MR, statut,
   relecteur, temps) :
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
     clôturer à sa place lui poserait une MR, un relecteur et un temps qu'elle n'a pas demandés.
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
   Ne commite jamais silencieusement sans montrer ce qui va être committé.

5. Best-effort : si un outil de lint/test est détecté dans le dossier concerné (ex.
   `package.json` avec un script `lint`/`test`, `pyproject.toml`...), propose de l'exécuter et
   rapporte le résultat. S'il n'y en a pas (probable tant que le monorepo est un squelette
   sans code), dis-le simplement et continue.

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
   Puis pousse la branche : `git push -u origin $(git branch --show-current)`. Ne fais jamais de
   `--force` ici — si le push est rejeté, arrête-toi et explique pourquoi plutôt que de forcer.
   Si le push **reste bloqué** sur une demande d'identifiants (typique sous Windows avec Git
   Credential Manager), relance-le en forçant `glab` comme credential helper :
   `GIT_TERMINAL_PROMPT=0 git -c credential.helper='' -c credential.helper='!glab auth git-credential' push -u origin $(git branch --show-current)`.

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
   - **Pipeline CI verte (si configurée)** : ne coche que si le dernier pipeline de la branche est
     **réellement réussi** au moment de la vérification (`glab ci status`, ou le pipeline remonté
     par la MR). En cours, échoué ou absent → laisse vide (juste après le push, le pipeline vient
     souvent de démarrer : une case vide ici est normale, le relecteur verra le verdict sur la MR).

9. Vérifie si une MR existe déjà pour cette branche avec
   `glab mr view $(git branch --show-current) --output json`. Si la commande échoue, aucune
   MR n'existe encore. Si elle réussit, inspecte le JSON retourné (champs `state` —
   `opened`/`closed`/`merged` —, `draft` et `description`) plutôt que de parser une sortie texte.
   - **Si elle n'existe pas** : crée-la en Draft, liée au ticket, avec la checklist **telle
     qu'évaluée à l'étape 8** (chaque case en `[x]` ou `[ ]` selon le constat) :
     ```
     glab mr create --draft --target-branch main --remove-source-branch \
       --title "<titre du ticket>" \
       --description "Closes #<iid>

     ## Checklist
     - [x] Respecte les conventions de branche/commit (docs/10-workflow-git.md)
     - [ ] Tests ajoutés/mis à jour si applicable
     - [x] Documentation mise à jour si applicable
     - [ ] Pipeline CI verte (si configurée)"
     ```
     (exemple : remplace chaque `[x]`/`[ ]` par le résultat réel de l'étape 8)
   - **Si elle existe déjà** : commence par remettre sa checklist à jour, de façon **idempotente** —
     modifie **uniquement** l'état des cases de la section `## Checklist` (jamais le reste,
     notamment le `Closes #<iid>`) : coche les cases vérifiées à l'étape 8, et **ne décoche
     jamais** une case déjà cochée (un humain a pu la cocher). Si la section `## Checklist`
     manque, ajoute-la en fin de description. Si rien ne change, ne fais pas d'update.
     **Relis et réécris la description uniquement via les helpers** :
     `bash scripts/gitlab/lib.sh get-mr-description <mr> > <fichier>`, tu édites le fichier, puis
     `bash scripts/gitlab/lib.sh set-mr-description <mr> <fichier>`. N'improvise **jamais** une
     lecture du type `glab mr view --output json | python` : elle corrompt l'UTF-8 en mojibake
     (« â€” » au lieu de « — ») — voir #141.
     - Ensuite, si elle est **en Draft** : demande à l'utilisateur si le travail est réellement
       terminé et prêt pour revue ; si oui, `glab mr update <mr> --ready`.
     - Si elle n'est **plus en Draft** : ne rien faire de plus sur la MR.

10. **Pose un relecteur sur la MR** — dans les deux cas (créée à l'instant ou déjà existante), une
   fois la MR en place :
   ```
   bash scripts/gitlab/lib.sh set-reviewer || echo "⚠ relecteur non posé — clôture poursuivie"
   ```
   Sans argument, le helper vise la MR ouverte de la branche courante ; il choisit un **membre
   humain du projet distinct de l'auteur** (résolu via l'API des membres — jamais un nom en dur,
   jamais le compte d'automatisation) et **ne remplace jamais** un relecteur déjà posé (idempotent :
   un relecteur choisi à la main est conservé). La revue reste **best-effort** — l'approbation n'est
   pas obligatoire (`approvals_before_merge=0`) et le merge reste une décision humaine : **son échec
   n'interrompt jamais la clôture** (ex. projet à une seule personne : aucun candidat), il est
   seulement signalé. Le relecteur désigné remonte ensuite dans la file de revue de `/backlog`.

11. Fais passer le **Status natif** du ticket à « En revue » (le cycle de vie est porté par le
   champ Status, pas par des labels — voir `docs/10-workflow-git.md` §3) :
   ```
   bash scripts/gitlab/lib.sh set-status <iid> "En revue"
   ```
   Le helper résout le work item depuis l'iid et **dérive le GID du statut par nom** depuis le
   lifecycle « Maestro » (pas de GID en dur). Vérifie que la commande réussit. Ne touche pas aux
   labels `agent::*` / `prio::*` / `type::*`.

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

13. Termine par un résumé : lien de la MR, état (Draft/Ready), le **relecteur posé** (ou pourquoi
   il n'y en a pas), le **retard éventuel sur `origin/main`** relevé à l'étape 6 (et le rebase
   proposé si un conflit est probable), les cases de la checklist cochées et celles restées vides
   (avec un mot sur pourquoi), le temps loggé le cas échéant, et rappelle que le merge reste une
   action humaine (personne — pas même toi — ne doit merger automatiquement). Si un refus du
   garde-fou de l'étape 3 a été **franchi sur demande explicite**, dis-le en tête du résumé (quel
   motif, et qui l'a demandé).
