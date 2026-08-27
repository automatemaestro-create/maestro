---
description: Termine le travail sur le ticket courant (push + PR prête + état « En revue » + merge)
argument-hint: "[issue-iid] (optionnel si le nom de la branche courante le contient déjà)"
allowed-tools: Bash(git:*), Bash(gh:*), Bash(bash:*), ExitWorktree
---

Tu vas clôturer le cycle de développement de la branche courante selon les règles de Maestro
(résumées ci-dessous — cette commande est autosuffisante ; réf. complète `docs/10-workflow-git.md`,
non chargée automatiquement, à n'ouvrir qu'en cas de doute). Arrête-toi et demande confirmation
avant toute action qui modifie l'état partagé (push, création/mise à jour de PR, merge) si un point
n'est pas clair.

⚠ **Depuis #418 (chantier #413), cette commande va jusqu'au merge** : elle passe la PR en « prête »,
**attend le pipeline** puis appelle `merge-mr`. Deux conséquences à assumer plutôt qu'à masquer —
elle ne rend plus la main dans la seconde (l'attente est bornée — 15 min pour un run qui tourne,
**jusqu'à 30 min** quand le run n'est pas encore né (#595, docs/10 §8.9) — et annoncée pendant
qu'elle dure), et **la revue avant merge disparaît de fait** (docs/10 §6). Ce qui disparaît est l'attente
d'un humain pour *vérifier*, pas la vérification : les quatre prérequis vivent dans `merge-mr`
(#415), et **aucun merge non vérifié** n'a lieu (#417).

⚠ **Et depuis #460, un refus pour une cause RÉPARABLE se répare au lieu de se signaler** : sur un
pipeline rouge (`4`) ou un conflit avec `origin/main` (`5`), la commande enchaîne d'elle-même sur
`/mr-fix`, **deux fois au plus** — exactement ce qu'un run autonome fait d'office depuis #420, où
la même cause était traitée par le pilote pendant qu'ici on se contentait de la proposer à un
humain. L'attente s'allonge d'autant, et se dit plutôt que de se masquer (étape 13.3).

⚠ **Et depuis #519, un merge réussi emporte son worktree** : la commande sort du worktree du ticket
(`ExitWorktree`) puis retire worktree et branche locale, comme le pilote d'un run le fait depuis
#438 — le ménage n'attend plus le prochain `/ticket-start` (étape 14). Conséquence à connaître : la
session **finit dans le clone principal**, pas là où elle a travaillé. C'est le but.

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

13. **Attends le pipeline, merge — et débloque ce qui est réparable** (#418 puis #460, chantier
   #413) — c'est ici que la clôture se termine vraiment. L'étape a été placée **après** l'état « En revue » et le log du temps, et pas
   avant : l'attente dure quelques minutes, et une session qui meurt pendant ce créneau doit
   laisser un ticket **lisible** (poussé, PR ouverte et prête, « En revue ») plutôt qu'un ticket
   resté « En cours » que plus personne ne réclame — c'est le mode de panne de #327.

   1. **Annonce l'attente, puis attends.** La CI ne se déclenche qu'à partir de la PR (#165) et tu
      viens tout juste de la pousser : le run **naît après** la PR, donc aucun verdict n'est encore
      rendu à cet instant.
      ```
      bash scripts/gitlab/lib.sh pipeline-wait <branche> || verdict=$?
      ```
      **Dis-le avant de lancer l'appel** — « pipeline attendu : 2-4 min en régime normal, jusqu'à
      15 min s'il tourne, jusqu'à 30 min s'il n'est pas encore né » : une commande qui ne rend pas
      la main pendant trois minutes sans avoir prévenu passe pour bloquée. `pipeline-wait` **n'écrit
      nulle part, ne relance rien et ne juge rien** (#416) ; ses codes (`0` vert, `3` verdict
      terminal non vert, `4` plafond atteint, `5` aucun pipeline et aucun n'est dû, `6` **pas encore
      né alors qu'une PR le rend dû**) servent à **formuler**, jamais à décider. Enchaîne sur
      `merge-mr` **dans tous les cas** : deux endroits qui disent « mergeable » valent moins qu'un,
      et c'est `merge-mr` qui tranche.

      ⚠ **Le `6` est le verdict de #595, et il n'est pas un échec.** Le 2026-08-26, l'événement
      `pull_request` a mis 18 à 20 min à déclencher la CI sur trois PR consécutives (docs/10 §8.9) —
      rien de rouge, rien en conflit, juste un run qui n'était pas encore né. Le verbe attend
      désormais jusqu'à 30 min dans ce cas précis, donc il aboutit tout seul ; s'il rend quand même
      `6`, **note la durée** pour le résumé (étape 15) et enchaîne sur `merge-mr`, qui rendra `3`.
      Le remède manuel que le verbe imprime — `gh workflow run ci.yml --ref <branche>` — n'est
      **pas** à jouer d'office : il vérifie `refs/heads/<branche>` et non la ref de merge de la PR,
      donc il substituerait en silence une vérification plus faible à celle qu'on attendait.
      Mentionne-le dans le résumé comme le geste disponible, sans le poser.
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
        (`delete_branch_on_merge`, #384) ; la branche locale et le worktree, eux, partent à
        l'**étape 14** — que seul ce verdict déclenche, avec le `7` (#519, #593).
      - `3` → le verdict n'est **pas encore rendu** : run en cours, absent, ou **périmé** — un vert
        porté par un commit antérieur au tien. C'est `merge-mr` qui compare les sha et rend ce `3`
        (docs/10 §6). `pipeline-wait`, lui, **sait depuis #595 quel run il attend** dès qu'une PR
        est ouverte : il écarte celui de la push précédente au lieu d'en faire un `0`, donc la
        reprise ci-dessous **attend enfin quelque chose** — avant, les deux appels rendaient le même
        verdict pour la même raison, sans qu'une seconde se soit écoulée. **Repasse une fois,
        pas plus** — `pipeline-wait <branche>` puis `merge-mr <iid>` à nouveau. Toujours `3` :
        laisse la PR **ouverte**, le ticket **« En revue »**, et dis-le — quelqu'un repassera, ou
        le drain de fin de run (#419).

        ⚠ **N'enchaîne JAMAIS sur `/mr-fix` ici** (#595), même quand l'attente s'éternise : une PR
        dont le pipeline **n'existe pas encore** n'a ni conflit ni job rouge, donc rien que `/mr-fix`
        sache réparer — les deux tentatives seraient consommées pour rien. `merge-mr` dit « pipeline
        pas encore né » dans ses deux formes (aucun run pour la branche, ou dernier run sur un sha
        antérieur) : dans les deux cas le run de la tête vient, et le résumé le **nomme** au lieu de
        parler d'« attente » — avec sa durée, et en disant que le geste disponible est un
        `gh workflow run ci.yml --ref <branche>` que tu n'as pas posé.
      - `4` → **pipeline rouge** · `5` → **conflit avec `origin/main`** → **enchaîne sur `/mr-fix
        <numéro>`, sans demander** (#460). Ne corrige rien toi-même : réparer un pipeline ou
        résoudre un conflit est un métier à part, et c'est le sien — invoque la commande, elle est
        autosuffisante. Ces deux causes sont les seules **réparables** des sept, et un run autonome
        les fait réparer d'office depuis #420 : laisser la clôture interactive *proposer* ce que le
        pilote *fait* traitait la même cause de deux façons selon l'appelant. Le détail est à
        l'étape 13.3.
      - `6` → **anomalie** : PR absente, fermée sans merge, encore brouillon, sans `Closes`, ou
        commits non poussés. **Nomme-la telle que le helper l'a rendue**, et ne la contourne pas —
        ni un `gh pr ready` « au cas où », ni un push de rattrapage, ni un merge par un autre
        chemin. Un `6` dit qu'une hypothèse de la clôture est fausse : le remède est de la regarder.
        ⚠ **Ce n'est pas le `6` de 13.1** : les deux tables partagent leurs chiffres (#595), et
        celui de `pipeline-wait` dit « pas encore né », ce qui n'est pas une anomalie. Lis le code
        dans la table du verbe qui vient de le rendre.
      - `7` → **la PR était déjà mergée** — quelqu'un d'autre l'a fait passer dans `main` pendant
        que tu travaillais (une session voisine, un `/mr-fix`, le drain d'un run). Ce n'est **pas
        une anomalie et il n'y a rien à faire** : le ticket est fermé par son `Closes`, son état
        passe « Terminé » par le workflow `issues: closed`. Traite-le comme un `0` **à un mot
        près** — le ménage de l'**étape 14** a lieu (la branche et le worktree sont tout aussi
        inutiles), mais le résumé dit « déjà mergée » et non « mergée » : t'attribuer un merge que
        tu n'as pas fait est ce qui rendrait le compte rendu faux (#593).
      - `1`/`2` → prérequis outil manquant / usage : signale-le, ne merge pas.
   3. **Le déblocage — sur `4` et `5` seulement** (#460). `/mr-fix` traite les deux blocages dans
      l'ordre qui est le sien (conflit d'abord, pipeline ensuite) et, depuis #418, **merge ce qu'il
      vient de débloquer**. Quatre choses à tenir :
      - **Annonce l'attente avant de lancer.** `/mr-fix` attend un pipeline à son tour, qui
        s'ajoute à celle de 13.1 : dis-le — « pipeline rouge, je lance `/mr-fix` : nouvelle
        attente de pipeline ». #418 a choisi d'annoncer cette attente plutôt que de la masquer ;
        elle s'allonge ici, la règle ne change pas.
      - **Ne repasse pas `merge-mr` derrière lui.** Son étape 12 *est* l'appel à `merge-mr`, donc
        son verdict de merge est le tien — le relire n'ajouterait aucune vérification : sur une PR
        qu'il vient de merger, `merge-mr` rend `7` (déjà mergée), c'est-à-dire un appel qui ne peut
        rien apprendre. Depuis #593 il ne fabrique plus de fausse anomalie — il rendait `6` —, mais
        la règle ne change pas pour autant : la question a déjà été posée et tranchée.
      - **Deux tentatives au plus**, et la seconde n'est due que si la première a **fait bouger la
        PR** — correctif poussé, conflit résolu, run relancé. Rejouer la commande sur un état
        inchangé ne peut rendre que le même verdict : c'est un abandon, pas une seconde tentative,
        et le résumé le dit ainsi. Ce plafond est **le tien**, écrit ici : la variable
        `MAESTRO_ORCHESTRATE_MRFIX_MAX` borne les sessions qu'un **run** ouvre (#420) et ne se lit
        pas depuis une clôture interactive — deux plafonds de même valeur, jamais le même réglage.
      - **Au-delà — ou sur un arrêt de `/mr-fix` avant son merge** (résolution pas claire abandonnée
        par `git merge --abort`, échec d'infrastructure, tentatives internes épuisées) : la PR reste
        **ouverte**, le ticket **« En revue »**, et tu rends la cause. C'est un état normal, pas un
        échec.

      ⚠ **En run autonome, n'enchaîne rien.** Une session de run n'atteint jamais ce verdict : dès
      13.1, `guard.sh` refuse `pipeline-wait` (et `merge-mr`), et ce refus **est** la fin normale de
      ta clôture — PR ouverte et prête, ticket « En revue », rien d'autre à faire. Le déblocage y
      appartient au **pilote**, qui ouvre lui-même les sessions `/mr-fix` (#420) : en lancer une
      d'ici ferait tourner deux remédiations sur la même PR, et attendrait un pipeline sur le quota
      du run — les deux choses que ce garde-fou existe pour empêcher.

   4. **Ce qui ne bouge dans aucun de ces cas** : tu ne force-pushes pas, tu ne fermes pas la PR,
      tu ne repasses pas le cycle de vie et tu ne relances rien en boucle — le déblocage de 13.3
      est **borné à deux tentatives**, et c'est la seule relance prévue. Un refus de merge n'est
      **pas** un échec du ticket — le travail est poussé, la PR est ouverte et prête, le ticket est
      « En revue ». C'est un état normal, et il a un nom.

14. **Ramasse le worktree et la branche — sur `0` seulement** (#519, docs/10 §9.2). Le merge vient
   de rendre ce worktree inutile, et cette session est la première à le savoir : le ménage n'attend
   plus le prochain `/ticket-start` ni un `/branch-cleanup` explicite. Il vient **après** le verdict
   du merge et ne le change jamais — si l'un de ces gestes échoue, un ticket mergé reste un ticket
   mergé, et l'échec se dit au lieu de devenir une réserve sur le merge.

   **N'entreprends rien sur `3`/`4`/`5`/`6`** : la PR est encore ouverte, donc le travail vit encore
   dans ce worktree. Passe directement au résumé.

   1. **Sors du worktree** avec l'outil **`ExitWorktree`**, `action: "keep"` — il te ramène au clone
      principal, la position d'où le pilote d'un run ramasse depuis #438, et c'est tout ce qu'on lui
      demande. **Jamais `action: "remove"`**, pour deux raisons indépendantes : le tool ne retire que
      les worktrees qu'`EnterWorktree` a *créés* dans la session, or celui-ci a été créé par
      `worktree.sh create` et seulement *rejoint* ; et même s'il le pouvait, il court-circuiterait
      tous les garde-fous du ramassage — confirmation du merge par la forge (#197), mesure du travail non
      sauvegardé contre le sha de merge (#438), pose de « Terminé » (#275), rattrapage des coquilles
      (#422). **On sort du worktree avec `ExitWorktree`, on nettoie avec les verbes du dépôt.**
      S'il répond qu'aucune session de worktree n'est active — la session n'y est pas *entrée* par
      `EnterWorktree`, elle y a démarré (verdict `ICI` de `/ticket-start`) —, **n'insiste pas** :
      reste où tu es, joue quand même 14.2, et relaie ce que `gc` répondra.
   2. **Retire le worktree, puis purge la branche** — les deux verbes du pilote, **dans cet ordre**,
      qui est le seul point non négociable ici (`git branch -D` refuse une branche encore empruntée
      par un worktree, #305) :
      ```
      bash scripts/git/worktree.sh gc --iid <iid>
      bash scripts/gitlab/lib.sh cleanup-merged --auto <branche>
      ```
      Si l'un des deux s'abstient, **n'invente aucun contournement** : la garde qui compte est celle
      de `gc`, et un worktree porteur de travail **non sauvegardé** est gardé exprès — un merge dit
      ce qui est parti sur `origin/main`, jamais ce qui est resté sur le disque. Ne la double pas
      d'une vérification à toi : deux formules qui divergeraient se remarqueraient trop tard, et
      c'est la garde qui perdrait.
   3. **Rends-en compte dans le résumé** : ce qui a été retiré, ou la **cause que `gc` a nommée** en
      s'abstenant. Une abstention n'est pas un échec de la clôture — c'est un travail que personne
      n'attend plus là, et ce résumé est le dernier endroit où l'information atteint quelqu'un.

   ⚠ **En run autonome, cette étape ne se joue jamais** : `guard.sh` refuse `pipeline-wait` et
   `merge-mr` dès 13.1, donc le verdict `0` n'y est pas atteint. C'est le **pilote** qui ramasse
   après son propre merge (#438) — les deux ramassages sont exclusifs par construction, et il n'y a
   aucun drapeau à tenir d'accord.

15. Termine par un résumé : **le verdict du merge en tête** (table ci-dessous), l'**issue du
   déblocage** si l'étape 13.3 a joué — sur sa **propre ligne**, jamais fondue dans celle du
   merge —, ce que le **ramassage** de l'étape 14 a retiré (ou la cause de son abstention), le
   lien de la PR, le **verdict du filet CI local** s'il n'était pas vert (étape 5 — quel job,
   et pourquoi tu as poussé quand même), le **retard éventuel sur `origin/main`** relevé à
   l'étape 6 (et le rebase proposé si un conflit est probable), les cases de la checklist
   cochées et celles restées vides (avec un mot sur pourquoi), et le temps loggé le cas
   échéant. Si un refus du garde-fou de l'étape 3 a été **franchi sur demande explicite**,
   dis-le en tête du résumé (quel motif, et qui l'a demandé).

   **Jamais de ✅ global.** Une clôture dont la PR est restée ouverte sur un pipeline rouge n'est
   pas « terminée avec une réserve » : elle est **inachevée**, et le dire avec ce mot-là est tout ce
   qui sépare ce résumé du faux verdict que #303 a supprimé ailleurs.
   | Issue | À rapporter |
   |---|---|
   | **Mergé** (`0`, du premier appel **ou** au terme du déblocage) | « PR #N mergée (squash) — #<iid> fermé, état « Terminé » posé par le workflow `issues: closed` » ; puis le **ramassage** de l'étape 14 — worktree et branche locale retirés, ou la cause que `gc` a nommée en s'abstenant (travail non sauvegardé : gardé, c'est voulu) —, et le fait que la session travaille désormais depuis le **clone principal** (#519) |
   | **Déblocage** (étape 13.3) | ⊘ **non tenté** — le verdict n'était pas réparable (`3`/`6`/`1`/`2`), ou un run autonome l'interdisait · ✅ **tenté et abouti** — ce que `/mr-fix` a réparé (conflit résolu, job remis au vert) et le nombre de tentatives · ❌ **tenté sans succès** — sur quel arrêt `/mr-fix` s'est arrêté, et combien de tentatives ont été consommées |
   | **Déjà mergé** (`7`) | « PR #N **déjà mergée** — dans `main` sans que ce soit mon fait » : le ticket est fermé et son état posé comme pour un `0`, le **ramassage** de l'étape 14 a lieu de même, et le mot « déjà » est ce qui empêche le résumé de s'attribuer un merge qu'il n'a pas commis (#593) |
   | **Non mergé** (`3`/`4`/`5`/`6`) | la **cause telle que `merge-mr` l'a rendue** (jamais reformulée en « il faudra revoir ça »), l'**état laissé** — PR **ouverte** et prête, ticket **« En revue »** — et la **suite** : repasser plus tard sur `3`, le geste humain nommé sur `6`, et sur `4`/`5` ce que le déblocage n'a pas su lever |

   **« Non tenté » et « refusé » ne se disent pas du même mot** — c'est la distinction que #303 a
   établie pour `/mr-fix`, et elle vaut ici mot pour mot : le premier est la conséquence de **ton**
   abandon (ou d'un verdict qui n'appelait aucune réparation), le second est un verdict sur **la
   PR**. Les confondre ferait chercher un problème de PR là où il y a une remédiation inachevée.

   Rappelle enfin qu'**aucun merge non vérifié** n'a lieu (#417) : ce qui a mergé — ou refusé de
   merger — est `bash scripts/gitlab/lib.sh merge-mr <iid>` et ses quatre prérequis, jamais toi.
   Cela reste vrai **après un déblocage** : ce qui merge alors est le `merge-mr` de l'étape 12 de
   `/mr-fix`, avec les mêmes quatre prérequis, jamais `/mr-fix` lui-même.
