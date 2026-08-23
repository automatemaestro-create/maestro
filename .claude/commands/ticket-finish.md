---
description: Termine le travail sur le ticket courant (push + PR prête + état « En revue » + merge)
argument-hint: "[issue-iid] (optionnel si le nom de la branche courante le contient déjà)"
allowed-tools: Bash(git:*), Bash(gh:*), Bash(bash:*)
---

Tu vas clôturer le cycle de développement de la branche courante selon les règles de Maestro
(résumées ci-dessous — cette commande est autosuffisante ; réf. complète `docs/10-workflow-git.md`,
non chargée automatiquement, à n'ouvrir qu'en cas de doute). Arrête-toi et demande confirmation
avant toute action qui modifie l'état partagé (push, création/mise à jour de PR, merge) si un point
n'est pas clair.

⚠ **Depuis #418 (chantier #413), cette commande va jusqu'au merge** : elle passe la PR en « prête »,
**attend le pipeline** puis appelle `merge-mr`. Deux conséquences à assumer plutôt qu'à masquer —
elle ne rend plus la main dans la seconde (l'attente est bornée à 15 min, annoncée pendant qu'elle
dure), et **la revue avant merge disparaît de fait** (docs/10 §6). Ce qui disparaît est l'attente
d'un humain pour *vérifier*, pas la vérification : les quatre prérequis vivent dans `merge-mr`
(#415), et **aucun merge non vérifié** n'a lieu (#417).

1. Détermine l'IID du ticket : utilise `$ARGUMENTS` s'il est fourni, sinon extrais-le du nom
   de la branche courante (`git branch --show-current`, motif `<type>/<iid>-<slug>`). Si
   aucun IID ne peut être déterminé, demande-le à l'utilisateur.

2. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require` ; arrête-toi si non authentifié.

3. **Garde-fou de clôture : cette session traite-t-elle bien ce ticket ?** À plusieurs, rien
   n'empêchait jusqu'ici un `/ticket-finish <iid>` lancé depuis la branche d'un *autre* ticket de
   basculer ce ticket-là « En revue », d'y poser une PR et le temps d'un travail qui n'est pas le
   sien. Ce contrôle vient **avant toute écriture** (commit, push, PR, état, temps) :
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
     clôturer à sa place lui poserait une PR et un temps qu'elle n'a pas demandés.
   - `5` → **arrête-toi** : la branche ne porte aucun iid (`main`, nom hors convention), donc la
     cohérence est invérifiable — et sur `main` il n'y a de toute façon rien à clôturer.
   - `1` → verdict **partiel** (ticket illisible, forge injoignable) : le contrôle local est
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
   que pour la description de PR (#233).
   Ne commite jamais silencieusement sans montrer ce qui va être committé.

5. **Filet CI local** — avant de pousser, rejoue en local ce que le pipeline de la PR jouera. Ne
   cherche pas toi-même quel outil s'applique : `scripts/ci/local.sh` est la **source unique** des
   contrôles locaux (#214, `docs/10-workflow-git.md` §8.4), il lit les jobs dans `.gitlab-ci.yml`
   et déduit du diff ce qui les concerne.
   ```
   bash scripts/ci/local.sh
   ```
   Par défaut `pytest` ne joue que les **suites concernées par le diff**, sans seuil de couverture
   (verdict annoncé PARTIEL) : ~40 s au lieu de ~10 min. **Ne le passe pas en `--complet`** et
   n'invente aucune autre recette — le verdict complet est celui du pipeline de la PR (#165), pas
   le tien.
   **Best-effort, jamais bloquant** : un outil absent rend son job `IGNORÉ`, et un job rouge écrit
   son journal sous `.maestro/ci-local/<job>.log` (chemin relatif, cité par le script). Si l'échec
   vient de ton diff et se corrige en une passe, corrige-le et reprends à l'étape 4 ; sinon
   **signale-le dans le résumé final** et poursuis la clôture — c'est la PR qui portera le verdict.

6. **Avant de pousser, regarde si la branche a pris du retard sur `origin/main`** — à plusieurs,
   `CLAUDE.md`, `docs/10-workflow-git.md` et `scripts/gitlab/lib.sh` sont touchés par presque tous
   les tickets, et sans ce contrôle le conflit n'apparaît que dans l'UI de la forge, après coup :
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
     et poursuis la clôture : la forge mergera sans difficulté.
   - `4` → **signale-le et propose le rebase à l'utilisateur** (`git fetch origin main && git
     rebase origin/main`), en nommant les fichiers concernés. La clôture **n'est pas bloquée** :
     s'il ne se prononce pas, pousse quand même et laisse la mention dans le résumé — c'est le
     relecteur ou l'auteur qui tranchera, la PR affichant le conflit.

7. **Pousse la branche.** Il n'y a plus rien à allumer avant : la CI tourne sur les exécutants
   hébergés de GitHub, et l'outillage de runner de projet — trois scripts, 1 146 lignes, et la
   machine qu'il fallait laisser allumée — est parti avec la CI GitLab (#344, docs/10 §8).
   `git push -u origin <nom-de-la-branche>` : `git push -u origin <nom-de-la-branche>` — **écris le nom lu à
   l'étape 1**, jamais `$(git branch --show-current)` : la couche permissions ne sait matcher
   aucune **substitution de commande**, et refuserait un `git push` par ailleurs autorisé (#233).
   Ne fais jamais de `--force` ici — si le push est rejeté, arrête-toi et explique pourquoi plutôt
   que de forcer.
   Si le push **reste bloqué** sur une demande d'identifiants (typique sous Windows avec Git
   Credential Manager), relance-le en forçant `gh` comme credential helper :
   `GIT_TERMINAL_PROMPT=0 git -c credential.helper='' -c credential.helper='!gh auth git-credential' push -u origin <nom-de-la-branche>`
   (ce repli garde un **préfixe de variable d'environnement**, immatchable lui aussi — c'est le
   domaine de #235, pas de ce lot : s'il est refusé, signale-le au lieu d'inventer une variante).

8. Évalue la **checklist de definition of done** de la PR (les quatre cases du gabarit
   `.github/pull_request_template.md` ; les quatre lignes sont de toute façon reproduites au
   point 9.2) : pour chacune, détermine si tu peux la cocher
   (`- [x]`) parce que tu l'as **effectivement vérifiée**, ou si elle reste vide (`- [ ]`). La
   checklist est un constat, pas un formulaire — et ce qui garde le merge n'est pas une case cochée
   mais les prérequis éprouvés par `merge-mr` : **aucun merge non vérifié** (#417, chantier #413).
   - **Conventions de branche/commit** : nom de branche au motif `<type>/<iid>-<slug>` et messages
     de `git log main..HEAD` conformes (Conventional Commits + `Refs`/`Closes #<iid>`). Le hook
     `commit-msg` les valide déjà, mais un `--no-verify` a pu passer : re-vérifie rapidement.
   - **Tests ajoutés/mis à jour si applicable** : juge d'après le diff de la branche
     (`git diff main...HEAD --stat`) — des tests touchés avec le code, ou un diff sans surface à
     tester (doc, config, prompts…) → coche ; du code applicatif sans test associé → laisse vide.
   - **Documentation mise à jour si applicable** : même logique, d'après le diff.
   - **Pipeline CI verte (si configurée)** : ne coche que si le dernier pipeline est **réellement
     réussi** au moment de la vérification (`bash scripts/gitlab/lib.sh pipeline-latest <branche>`,
     qui retrouve aussi le pipeline porté par la PR). En cours, échoué ou absent → laisse vide.
     **Une case vide est le cas NORMAL ici** : la CI ne se déclenche qu'à partir de la PR (#165 sur
     GitLab, `on: pull_request` sur GitHub — docs/10 §8), donc à la première clôture d'un ticket
     **aucun pipeline n'existe encore** à ce stade — il naîtra de l'étape 9. N'attends pas **ici** :
     l'attente a désormais sa propre étape (13), et ce qui garde le merge n'est de toute façon pas
     cette case mais le verdict que `merge-mr` éprouvera **sur la tête de la PR**.

9. **Crée (ou mets à jour) la PR — la description passe toujours par un FICHIER.** Jamais de
   description sur la ligne de commande : elle fait par nature plusieurs lignes, la couche
   permissions découpe une commande sur ses sauts de ligne et la refuse, puis refuse aussi les deux
   replis naturels (`--body "$(cat …)"`, `D="$(cat …)"; … "$D"`) — aucune règle ne peut
   matcher une **substitution de commande**. C'est ce qui a fait tomber 8 sessions autonomes sur 16
   (#233), et toujours ici, sur la **dernière action du ticket** : tout est commité, rien ne le
   déclare. Le fichier n'est pas un contournement, c'est la forme normale (#232).

   1. **Une PR ouverte existe-t-elle déjà pour cette branche ?**
      ```
      bash scripts/gitlab/lib.sh mr-iid
      ```
      (sans argument : la branche courante ; code 1 + message si aucune PR ouverte). Le verbe garde
      son nom `mr-iid` des deux côtés — c'est le **contrat de `lib.sh`**, normalisé vers le
      vocabulaire GitLab pour que ses appelants ne bougent pas (cf. son en-tête) : seul le mot
      change dans les prompts, jamais le nom d'un verbe.
   2. **Prépare le fichier de description**, dans ton répertoire de scratchpad de session (ce n'est
      pas un livrable, il n'a rien à faire dans le worktree). **Écris-le avec l'outil `Write`** —
      pas avec `cat`/`echo`/un heredoc, qui rejoueraient exactement le problème que cette étape
      évite.
      - **Aucune PR** : contenu neuf — `Closes #<iid>`, une ligne vide, puis la section
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
      - **PR déjà ouverte** : pars de l'**existant**, la mise à jour remplaçant la description
        entière. Relis-la **via le helper** — `bash scripts/gitlab/lib.sh get-mr-description <mr> >
        <fichier>` — puis édite le fichier de façon **idempotente** : modifie **uniquement** l'état
        des cases de la section `## Checklist` (jamais le reste, notamment le `Closes #<iid>`),
        coche celles vérifiées à l'étape 8, et **ne décoche jamais** une case déjà cochée (un
        humain a pu la cocher). Si la section `## Checklist` manque, ajoute-la en fin de
        description. Si rien ne change, passe directement au point 4. N'improvise **jamais** une
        lecture du type `gh pr view --json body | python` : elle corrompt l'UTF-8 en mojibake
        (« â€” » au lieu de « — ») — voir #141.
   3. **Un seul appel, plat et court**, dans les deux cas :
      ```
      bash scripts/gitlab/lib.sh create-mr <iid> <fichier>
      ```
      Le helper ouvre la PR en **Draft** vers `main`, **titre lu depuis le ticket**, description
      lue depuis le fichier, et imprime son URL. Il est **idempotent** : si une PR ouverte existe
      déjà pour la branche, il met sa description à jour au lieu d'échouer. La suppression de la
      branche source au merge est un **réglage du dépôt** des deux côtés (`doctor.sh` le vérifie),
      pas une option de cet appel.
   4. **Passe la PR en « prête » — sans demander.** `create-mr` l'ouvre en Draft ; c'est ici
      qu'on la lève :
      ```
      gh pr ready <numéro>
      ```
      La question qui se posait ici — « le travail est-il réellement prêt pour la revue ? » — a
      disparu avec #418 : la commande s'apprête à **merger** cette PR à l'étape 13, et une PR qu'on
      merge n'est pas un brouillon. Ce n'est pas la question qui a été escamotée, c'est sa réponse
      qui est devenue certaine — lancer `/ticket-finish`, c'est déclarer le travail fini.
      L'appel est **sans effet sur une PR déjà prête** : un « already ready for review » est un
      constat, pas un échec. Et ce n'est **pas** une promesse de merge : le brouillon n'est qu'**un**
      des quatre prérequis de `merge-mr`, qui refusera toujours une PR levée mais rouge, en conflit,
      ou qui ne ferme pas son ticket (#415).

10. **Ne pose aucun relecteur sur la PR** (#196) — la désignation d'un relecteur est un **geste
   humain**, jamais automatique : n'appelle pas `lib.sh set-reviewer` et n'utilise pas
   `gh pr edit --add-reviewer`. Le helper reste disponible pour une pose explicite, sur demande.
   La revue reste **best-effort** — aucune approbation n'est exigée pour merger, ce que le merge
   exige étant les prérequis de `merge-mr` et non un avis (**aucun merge non vérifié**, #417) ; la
   visibilité des PR en attente est portée par la **file de revue** en tête de `/backlog` (la plus
   ancienne d'abord).

11. Fais passer l'**état** du ticket à « En revue » (le cycle de vie est porté par le champ Status
   du projet — voir `docs/10-workflow-git.md` §3) :
   ```
   bash scripts/gitlab/lib.sh set-workflow <iid> "En revue"
   ```
   Le helper résout le work item depuis l'iid et **dérive les GID des six labels par nom** (pas de
   GID en dur), puis ajoute la cible et **retire les cinq autres dans le même appel** — l'exclusion
   mutuelle des labels scopés est Premium, donc rien ne l'assurerait à notre place. Vérifie que la
   commande réussit. Ne touche pas aux labels `agent::*` / `prio::*` / `type::*`.

12. Renseigne le **temps passé** — **estimé automatiquement, sans demander de confirmation** (voir
   `docs/10-workflow-git.md` §3.3) :
   - Vérifie d'abord ce qui est déjà loggé **depuis la bascule sur GitHub** :
     `bash scripts/gitlab/lib.sh get-time-spent <iid> --hors-import` (secondes). Si le résultat
     **n'est pas `0`**, du temps a déjà été enregistré — n'en rajoute pas (idempotence : ne double
     pas un cycle déjà loggé) et passe à la suite. ⚠ `--hors-import` et non le total : sur un
     ticket importé de GitLab, l'historique repris par la jointure de #400 fait répondre « oui »
     au total avant qu'aucune session n'ait travaillé dessus, et le garde-fou avalerait alors en
     silence le temps de celle qui termine le ticket.
   - Sinon, **estime toi-même l'effort** d'après la **portée réelle du travail** de la branche
     (ampleur du diff, nombre et nature des commits, ce que tu as fait durant la session) — pas le
     temps calendaire écoulé, qui n'est qu'un plafond peu fidèle. Traduis-la en une durée au format
     attendu par le helper (`30m`, `1h`, `2h 30m`, `1d`…), identique des deux côtés — sur GitHub
     c'est le **suivi maison** de `lib.sh` qui l'enregistre, la forge n'ayant pas de temps passé
     natif (docs/27 §5).
   - Logge-la directement, sans question :
     ```
     bash scripts/gitlab/lib.sh log-time <iid> "<durée estimée>" "Cycle de dev (start->finish)"
     ```
   - Indique dans le résumé final la durée estimée et loggée (transparence a posteriori).

13. **Attends le pipeline, puis merge** (#418, chantier #413) — c'est ici que la clôture se
   termine vraiment. L'étape a été placée **après** l'état « En revue » et le log du temps, et pas
   avant : l'attente dure quelques minutes, et une session qui meurt pendant ce créneau doit
   laisser un ticket **lisible** (poussé, PR ouverte et prête, « En revue ») plutôt qu'un ticket
   resté « En cours » que plus personne ne réclame — c'est le mode de panne de #327.

   1. **Annonce l'attente, puis attends.** La CI ne se déclenche qu'à partir de la PR (#165) et tu
      viens tout juste de la pousser : le run **naît après** la PR, donc aucun verdict n'est encore
      rendu à cet instant.
      ```
      bash scripts/gitlab/lib.sh pipeline-wait <branche> || verdict=$?
      ```
      **Dis-le avant de lancer l'appel** — « pipeline en cours, attente bornée à 15 min » : c'est
      2-4 min en régime normal, mais une commande qui ne rend pas la main pendant trois minutes
      sans avoir prévenu passe pour bloquée. `pipeline-wait` **n'écrit nulle part, ne relance rien
      et ne juge rien** (#416) ; ses codes (`0` vert, `3` verdict terminal non vert, `4` plafond
      atteint, `5` aucun pipeline) servent à **formuler**, jamais à décider. Enchaîne sur `merge-mr`
      **dans tous les cas** : deux endroits qui disent « mergeable » valent moins qu'un, et c'est
      `merge-mr` qui tranche.
   2. **Merge** :
      ```
      bash scripts/gitlab/lib.sh merge-mr <iid> || verdict=$?
      ```
      Jamais `gh pr merge` : le geste **nu** reste en `deny` côté permissions **et** dans
      `guard.sh`, et ce n'est pas une contradiction — ces filets jugent le **texte de la commande
      que tu lances**, pas ce qu'un script appelle en interne (#417). Le code de retour décide de
      la suite, et lui seul :
      - `0` → **mergé** (squash). Le ticket se ferme par son `Closes`, et son état passe
        « Terminé » tout seul via le workflow `issues: closed` (#377) : **ne pose rien**, ne
        repasse pas `set-workflow`, ne ferme rien à la main. La branche distante part avec le merge
        (`delete_branch_on_merge`, #384) ; la locale et le worktree sont du ressort de
        `/branch-cleanup`.
      - `3` → le verdict n'est **pas encore rendu** : run en cours, absent, ou **périmé** — un vert
        porté par un commit antérieur au tien. Ce dernier cas est le plus fréquent, et il est
        normal : `pipeline-wait` ne compare pas les sha (il le dit lui-même), donc il a pu rendre
        `0` sur le run précédent de la branche pendant que le tien démarrait. **Repasse une fois,
        pas plus** — `pipeline-wait <branche>` puis `merge-mr <iid>` à nouveau. Toujours `3` :
        laisse la PR **ouverte**, le ticket **« En revue »**, et dis-le — quelqu'un repassera, ou
        le drain de fin de run (#419).
      - `4` → **pipeline rouge** → PR ouverte, ticket « En revue », propose `/mr-fix <numéro>`.
        Ne corrige rien ici : réparer un pipeline est un métier à part, et c'est le sien.
      - `5` → **conflit avec `origin/main`** → idem ; `/mr-fix` le résout par un merge, jamais par
        un rebase.
      - `6` → **anomalie** : PR absente, fermée, encore brouillon, sans `Closes`, ou commits non
        poussés. **Nomme-la telle que le helper l'a rendue**, et ne la contourne pas — ni un
        `gh pr ready` « au cas où », ni un push de rattrapage, ni un merge par un autre chemin. Un
        `6` dit qu'une hypothèse de la clôture est fausse : le remède est de la regarder.
      - `1`/`2` → prérequis outil manquant / usage : signale-le, ne merge pas.
   3. **Ce qui ne bouge dans aucun de ces cas** : tu ne force-pushes pas, tu ne fermes pas la PR,
      tu ne repasses pas le cycle de vie et tu ne relances pas la commande en boucle. Un refus de
      merge n'est **pas** un échec du ticket — le travail est poussé, la PR est ouverte et prête,
      le ticket est « En revue ». C'est un état normal, et il a un nom.

14. Termine par un résumé : **le verdict du merge en tête** (table ci-dessous), le lien de la PR, le
   **verdict du filet CI local** s'il n'était pas vert (étape 5 — quel job, et pourquoi tu as poussé
   quand même), le **retard éventuel sur `origin/main`** relevé à l'étape 6 (et le rebase proposé si
   un conflit est probable), les cases de la checklist cochées et celles restées vides (avec un mot
   sur pourquoi), et le temps loggé le cas échéant. Si un refus du garde-fou de l'étape 3 a été
   **franchi sur demande explicite**, dis-le en tête du résumé (quel motif, et qui l'a demandé).

   **Jamais de ✅ global.** Une clôture dont la PR est restée ouverte sur un pipeline rouge n'est
   pas « terminée avec une réserve » : elle est **inachevée**, et le dire avec ce mot-là est tout ce
   qui sépare ce résumé du faux verdict que #303 a supprimé ailleurs.
   | Issue | À rapporter |
   |---|---|
   | **Mergé** (`0`) | « PR #N mergée (squash) — #<iid> fermé, état « Terminé » posé par le workflow `issues: closed` » ; rappelle que la branche locale et le worktree partent avec `/branch-cleanup` |
   | **Non mergé** (`3`/`4`/`5`/`6`) | la **cause telle que `merge-mr` l'a rendue** (jamais reformulée en « il faudra revoir ça »), l'**état laissé** — PR **ouverte** et prête, ticket **« En revue »** — et la **suite** : `/mr-fix <numéro>` sur `4`/`5`, repasser plus tard sur `3`, le geste humain nommé sur `6` |

   Rappelle enfin qu'**aucun merge non vérifié** n'a lieu (#417) : ce qui a mergé — ou refusé de
   merger — est `bash scripts/gitlab/lib.sh merge-mr <iid>` et ses quatre prérequis, jamais toi.
