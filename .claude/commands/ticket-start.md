---
description: Démarre le travail sur un ticket (branche + assignation + état « En cours »)
argument-hint: <issue-iid>
allowed-tools: Bash(git:*), Bash(gh:*), Bash(bash:*), EnterWorktree
---

Tu vas démarrer le travail sur le ticket d'IID `$ARGUMENTS` selon les règles de Maestro
(réf. complète `docs/10-workflow-git.md` §5, à n'ouvrir qu'en cas de doute). Suis ces étapes dans
l'ordre et arrête-toi (en expliquant pourquoi) dès qu'une vérification échoue au lieu de forcer la
suite. Si aucun IID n'est fourni dans `$ARGUMENTS`, demande-le à l'utilisateur avant de continuer.

1. **Préflight en un appel** : `bash scripts/gitlab/lib.sh start-brief $ARGUMENTS`. Le helper
   vérifie les pré-requis (CLI de la forge authentifié — sinon arrête-toi et relaie son message,
   qui nomme la commande à lancer : `gh auth login`) et l'arbre propre, puis imprime le brief du
   ticket (titre, labels, critères d'acceptation), la ligne `statut : … — libre / pris par …`, le
   cas parent/sous-ticket et la branche proposée. Il est informatif : la décision (démarrer,
   rediriger, s'arrêter) reste la tienne :
   - **Changements non commités** : ne tranche pas ici, le ticket va travailler ailleurs. Note-les
     et **reporte la décision à l'étape 2** : si un worktree est monté (`WORKTREE`), ils restent
     dans le répertoire qu'on quitte, intacts et hors du chemin — signale-les et continue. Si le
     verdict est `ICI`, on travaillerait **dans** cet arbre : arrête-toi alors et demande quoi en
     faire (committer, stasher, annuler) — ne décide pas à la place de l'utilisateur.
   - **Ticket déjà pris** (la sortie porte `⚠ déjà pris par <username>` — état « En cours »
     assigné à quelqu'un d'autre) : **arrête-toi**. Quelqu'un travaille dessus et l'étape 4
     (`begin`) **remplace** la liste des assignés : le démarrer lui retirerait son ticket en
     silence. Dis qui l'a pris et oriente vers un ticket libre (`/backlog`, section « Libres »).
     Ne le reprends que sur **demande explicite** de l'utilisateur (la personne a lâché le sujet,
     ticket resté « En cours » à l'abandon) — dans ce cas seulement, enchaîne les étapes 2 à 5.
   - **Parent de suivi** (la sortie liste une checklist `## Sous-tickets`) : il ne porte ni
     branche ni code — ne le démarre pas. Coche au passage (`- [x]`) les lots « Terminé » encore
     décochés dans sa description. **Relis et réécris la description uniquement via les helpers**
     (`bash scripts/gitlab/lib.sh get-description <iid> > <fichier>`, tu édites le fichier, puis
     `bash scripts/gitlab/lib.sh set-description <iid> <fichier>`) : n'improvise **jamais** une
     lecture du type `gh issue view --json body | python`, qui corrompt l'UTF-8 en mojibake
     (« â€” » au lieu de « — ») et l'a déjà repoussé dans un parent — voir #141. Mise à jour
     idempotente : ne jamais décocher une case cochée. Puis appuie-toi sur la section **« lots
     démarrables maintenant »** de la sortie : elle liste **tous** les lots « À faire » que rien ne
     bloque — pas seulement le premier, les lots marqués **« (parallèle) »** ne se bloquant pas
     entre eux (docs/10 §5.1). Démarre le **premier de cette liste** en reprenant l'étape 1 avec
     son iid, et **annonce les autres** comme prenables en parallèle par quelqu'un d'autre (si le
     parent était « À faire », passe-le « En cours » via
     `bash scripts/gitlab/lib.sh set-workflow <iid-parent> "En cours"`). Si la liste est vide, rien à
     démarrer : parent **fermable** si tout est « Terminé » (toutes cases cochées), sinon le
     travail est déjà en route (« En cours ») ou livré et on n'attend plus que des merges.
   - **Sous-ticket** : la sortie donne le parent, le rang (« lot n/total »), le marqueur
     « parallèle » éventuel, les tests différés et le contrôle des lots précédents. Si elle
     signale des lots précédents non livrés (⚠ — encore « À faire » ou « En cours »), arrête-toi :
     les terminer d'abord. Ne bloquent **pas** : un lot précédent « En revue » (PR ouverte pas
     encore mergée — les lots sont additifs et la branche part de `main`), ni un lot précédent
     marqué « (parallèle) » quand le lot visé l'est aussi (ils sont indépendants par déclaration).
     Sinon, il se démarre comme un ticket ordinaire.
   - **Ticket trop gros ?** (ni parent ni sous-ticket) : évalue la **charge estimée** sur la
     **description intégrale** (`bash scripts/gitlab/lib.sh issue-raw $ARGUMENTS`, notes techniques
     et références croisées comprises). Les **couches/composants distincts** touchés (moteur, backend, UI,
     script, commande, doc…) sont un **signal** qui oblige à estimer finement, pas un déclencheur
     automatique : ne propose le découpage que si le travail **dépasse ~1 session** — plusieurs
     couches substantielles (étalon : #48, moteur + backend + UI), plus de 3-4 critères
     d'acceptation, ou des livrables indépendants. Un besoin multi-facettes qui tient en une
     session (ex. un script + sa doc) se démarre tel quel, au besoin avec une checklist interne
     dans sa description. Au-delà du seuil, ne l'enchaîne pas tel quel : propose le découpage —
     le ticket devient le parent (section `## Sous-tickets`), les sous-tickets sont créés et liés
     selon la convention de `/ticket-create` (1-3 critères chacun, mergeables seuls sur `main`,
     lot final « tests + doc », `lib.sh issue-link`), puis on démarre le premier lot.
     Contrairement à l'étape 5, c'est une **vraie pause** : attends la décision de l'utilisateur.
   - **Branche proposée sans préfixe** (label `type::` absent) : déduis le type du titre/de la
     description, ou demande à l'utilisateur si ambigu.

2. **Worktree du ticket** — le travail ne se fait plus dans le répertoire courant : chaque ticket a
   le sien (docs/10 §9), pour que le clone principal reste sur `main` et disponible.
   ```
   bash scripts/git/worktree.sh ensure $ARGUMENTS
   ```
   La commande monte le worktree si besoin (branche depuis `origin/main`, `.env`, artefacts
   partagés, ports et profil de navigateur dédiés) et rend son verdict **en dernière ligne** :
   - **`WORKTREE <chemin>`** → relocalise la session avec l'outil **`EnterWorktree`** en lui
     passant ce `<chemin>` en `path`, puis continue les étapes suivantes **depuis là**. C'est le
     cas nominal, depuis le clone principal.
   - **`ICI <chemin>`** → le répertoire courant est déjà le bon (worktree du ticket, ou reprise
     d'un travail en cours dans le clone principal). **N'appelle pas `EnterWorktree`** — c'est ce
     qui permet à `scripts/orchestrate/run.sh`, qui monte lui-même le worktree avant d'y lancer la
     session, de continuer à fonctionner sans changement.
   - **échec** (branche déjà empruntée par un autre worktree, ticket sans label `type::`) →
     arrête-toi et rapporte le message, qui nomme la cause.

   Au passage, `ensure` **ramasse les worktrees dont le travail est soldé** (PR mergée ou ticket
   fermé, confirmé par la forge — #197, docs/10 §9.2), puis **purge les branches locales déjà
   mergées** (#305, docs/10 §9.5 — dans cet ordre, `git branch -D` refusant une branche empruntée
   par un worktree ; même garde-fou que `/branch-cleanup` : uniquement celles dont la forge confirme
   la PR `merged`). Muets quand il n'y a rien à faire ; s'il **signale** un worktree conservé parce
   qu'il porte du travail non sauvegardé, ou une branche mergée retenue par un worktree, relaie-le
   dans ton résumé — c'est du travail que personne n'attend plus là.

   Il **remet aussi les dépendances du clone principal à niveau** quand le dépôt en a ajouté
   (#216, docs/10 §9.4) — en appelant `scripts/setup.sh`, jamais `pip`/`npm` à la main. Muet quand
   il n'y a rien à prendre ; s'il annonce une mise à niveau, ou qu'elle échoue (elle ne bloque
   jamais un démarrage), relaie-le dans ton résumé.

   ⚠ La relocalisation déplace le répertoire de travail, **pas le bloc `env`** : une session
   relocalisée garde les ports Control Tower et le profil de navigateur du clone principal
   (mesuré sur #181 — `EnterWorktree` ne réévalue que les caches liés au CWD). `ensure` affiche
   les valeurs propres au worktree : si le ticket démarre la Control Tower ou pilote le
   navigateur, passe-les explicitement, ou ouvre une session neuve sur le worktree (qui, elle,
   chargera son `settings.local.json`).

3. **Branche** — un seul appel, qui met `main` à jour et crée (ou rejoint) la branche proposée :
   ```
   bash scripts/gitlab/lib.sh start-branch <branche-proposée>
   ```
   Après l'étape 2 c'est en général sans effet (la branche est déjà celle du worktree) — l'appel
   est conservé parce qu'il reste la source unique du placement sur la branche et qu'il couvre le
   cas `ICI` dans le clone principal. Le helper s'adapte au répertoire de travail (docs/10 §9) :
   dans le clone principal il passe par `main` ; dans un **worktree** il branche directement sur
   `origin/main` (`main` y est déjà emprunté par le clone principal, un `git checkout main`
   échouerait) et ne fait rien si la branche est déjà celle du worktree.

4. **Démarrage groupé** : `bash scripts/gitlab/lib.sh begin $ARGUMENTS` — état « En cours » (posé
   dans le champ Status du projet), assignation et dates (début = aujourd'hui, échéance selon
   `prio::`) en un seul appel. Vérifie que la commande réussit ; en cas d'échec, signale-le sans
   bloquer la branche déjà créée. Ne touche pas aux labels `type::`/`agent::`/`prio::` (triage, pas
   ce workflow).

5. **Résumé court, puis enchaîne immédiatement sur l'implémentation** : nom de la branche, titre
   du ticket, dates posées, critères d'acceptation ; pour un sous-ticket, le parent, le rang du
   lot, ses tests différés (« tests différés → #<iid> » : livrer sans tests est prévu, pas un
   oubli) et, s'il y en a, les **autres lots démarrables en parallèle** (`bash
   scripts/gitlab/lib.sh startables <iid-parent>`) — de quoi permettre à quelqu'un d'autre d'en
   prendre un tout de suite. Le résumé cadre le travail, ce n'est **pas une demande de validation** : n'attends
   aucun « go » et commence tout de suite (les critères d'acceptation font foi). Ne t'arrête pour
   demander que si le ticket est réellement ambigu au point de ne pas pouvoir commencer.

Pas de Pull Request à ce stade (aucun commit à proposer). La clôture passe par les commandes
dédiées — `/ticket-ship` (commit auto + push + PR + état) ou `/ticket-finish` (commit déjà
fait) — jamais ré-implémentée à la main : les skills en sont la source unique.
