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

3. Regarde `git status --porcelain`. S'il reste des changements non commités :
   - montre un résumé (`git diff --stat`),
   - propose un message de commit **Conventional Commits** : en-tête `<type>(<scope>): <résumé impératif>`
     (types : `feat`/`fix`/`chore`/`docs`/`refactor`/`test`/`ci`/`build`/`perf` ; `scope` optionnel)
     et pied `Refs #<iid>` (le hook `commit-msg` refuse tout message hors convention ; détail
     `docs/10-workflow-git.md` §2),
   - demande confirmation à l'utilisateur avant de committer.
   Ne commite jamais silencieusement sans montrer ce qui va être committé.

4. Best-effort : si un outil de lint/test est détecté dans le dossier concerné (ex.
   `package.json` avec un script `lint`/`test`, `pyproject.toml`...), propose de l'exécuter et
   rapporte le résultat. S'il n'y en a pas (probable tant que le monorepo est un squelette
   sans code), dis-le simplement et continue.

5. **Avant de pousser, assure le runner CI local en ligne** : les runners partagés étant
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

6. Évalue la **checklist de definition of done** de la MR (les quatre cases du template
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

7. Vérifie si une MR existe déjà pour cette branche avec
   `glab mr view $(git branch --show-current) --output json`. Si la commande échoue, aucune
   MR n'existe encore. Si elle réussit, inspecte le JSON retourné (champs `state` —
   `opened`/`closed`/`merged` —, `draft` et `description`) plutôt que de parser une sortie texte.
   - **Si elle n'existe pas** : crée-la en Draft, liée au ticket, avec la checklist **telle
     qu'évaluée à l'étape 6** (chaque case en `[x]` ou `[ ]` selon le constat) :
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
     (exemple : remplace chaque `[x]`/`[ ]` par le résultat réel de l'étape 6)
   - **Si elle existe déjà** : commence par remettre sa checklist à jour, de façon **idempotente** —
     modifie **uniquement** l'état des cases de la section `## Checklist` (jamais le reste,
     notamment le `Closes #<iid>`) : coche les cases vérifiées à l'étape 6, et **ne décoche
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

8. Fais passer le **Status natif** du ticket à « En revue » (le cycle de vie est porté par le
   champ Status, pas par des labels — voir `docs/10-workflow-git.md` §3) :
   ```
   bash scripts/gitlab/lib.sh set-status <iid> "En revue"
   ```
   Le helper résout le work item depuis l'iid et **dérive le GID du statut par nom** depuis le
   lifecycle « Maestro » (pas de GID en dur). Vérifie que la commande réussit. Ne touche pas aux
   labels `agent::*` / `prio::*` / `type::*`.

9. Renseigne le **temps passé** — **estimé automatiquement, sans demander de confirmation** (voir
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

10. Termine par un résumé : lien de la MR, état (Draft/Ready), les cases de la checklist cochées
   et celles restées vides (avec un mot sur pourquoi), le temps loggé le cas échéant, et rappelle
   que le merge reste une action humaine (personne — pas même toi — ne doit merger
   automatiquement).
