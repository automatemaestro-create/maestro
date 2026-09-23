---
description: Démarre le travail sur un ticket (branche + assignation + état « En cours »)
argument-hint: <issue-iid>
allowed-tools: Bash(bash:*), Bash(gh:*), Bash(git:*), EnterWorktree, Skill, Agent, AskUserQuestion, Read, Grep, Glob, Write, WebSearch, WebFetch, mcp__chrome-maestro
---

Tu vas démarrer le travail sur le ticket d'IID `$ARGUMENTS` selon les règles de Maestro
(réf. complète `docs/10-workflow-git.md` §5, à n'ouvrir qu'en cas de doute). Suis ces étapes dans
l'ordre et arrête-toi (en expliquant pourquoi) dès qu'une vérification échoue au lieu de forcer la
suite. Si aucun IID n'est fourni dans `$ARGUMENTS`, demande-le à l'utilisateur avant de continuer.

1. **Préflight en un appel** : `bash scripts/gitlab/lib.sh start-brief $ARGUMENTS`. Le helper
   vérifie les pré-requis (CLI de la forge authentifié — sinon arrête-toi et relaie son message,
   qui nomme la commande à lancer : `gh auth login`) et l'arbre propre, puis imprime le brief du
   ticket (titre, labels, critères d'acceptation, et la section **`## Rendu attendu`** quand le
   ticket en porte une écrite — commentaire de gabarit retiré), la ligne `statut : … — libre / pris par …`, le
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
   - **Parent de suivi** (la sortie liste ses **lots**) : il ne porte ni branche ni code — ne le
     démarre pas, et **ne touche pas à sa description** : ses lots sont des sub-issues natives
     (#389), leur ordre et leur état viennent de la forge, et la coche est dérivée de l'état d'un
     lot (fermé = coché) depuis #390 — il n'y a plus rien à synchroniser à la main. Appuie-toi
     directement sur la section **« lots démarrables maintenant »** de la sortie : elle liste
     **tous** les lots « À faire » que rien ne bloque — pas seulement le premier, les lots marqués **« (parallèle) »** ne se bloquant pas
     entre eux (docs/10 §5.1). Démarre le **premier de cette liste** en reprenant l'étape 1 avec
     son iid, et **annonce les autres** comme prenables en parallèle par quelqu'un d'autre. **Ne
     pose pas l'état du parent à la main** : le `begin` du lot (étape 4) le passe « En cours » s'il
     était « À faire », et le saute sans écriture s'il l'est déjà (#517, docs/10 §5.1). Si la liste
     est vide, rien à démarrer : le travail est déjà en route (« En cours ») ou livré et on n'attend
     plus que des merges. **Ne propose pas de fermer le parent** — il se ferme tout seul quand son
     dernier lot se ferme (#515, docs/10 §5.1) ; un parent encore ouvert avec des lots tous fermés
     est une anomalie à signaler (`bash scripts/gitlab/lib.sh lots-ouverts <iid-parent>` le
     confirme en lecture seule), pas un geste à faire à la main.
   - **Sous-ticket** : la sortie donne le parent, le rang (« lot n/total »), le marqueur
     « parallèle » éventuel, les tests différés d'un lot né avant #1150 et le contrôle des lots
     précédents. Si elle
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
     des livrables **indépendants** deviennent des tickets sans parent ; **une** capacité qui
     dépasse une session fait du ticket le parent (sa description garde l'objectif, rien de plus),
     et les sous-tickets sont créés puis **rattachés en sub-issues** selon la convention de
     `/ticket-create` (1-3 critères chacun, mergeables seuls sur `main`, **chacun avec ses tests**
     — #1150 —, `lib.sh issue-link <parent> <lot> [--parallele]` puis `lib.sh subticket-order`),
     puis on démarre le premier lot.
     Contrairement à l'étape 6, c'est une **vraie pause** : attends la décision de l'utilisateur.
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

5. **Veille de conception — seulement pour un ticket qui décide d'un écran** (#1151, docs/40 §3).
   Si la sortie de l'étape 1 porte un bloc **`surface visible :`**, ou si tu t'apprêtes à modifier
   `apps/web/`, pose une seule question, celle du §7.2 de `/design-veille` — *ce ticket décide-t-il
   de quelque chose à l'écran, ou applique-t-il une décision déjà prise ?* —, et juges-en par son
   critère, sans le recopier :
   - **Il décide** : sa veille se joue à l'étape 7, avant ses variantes, dans les deux régimes et
     sans rien demander (#1009). Rien à faire ici.
   - **Il applique** (correctif, alignement, suite d'une direction déjà consignée) : **ni veille, ni
     question, ni veille différée**. N'ouvre pas `/design-veille`, n'appelle ni `veille-arbitre` ni
     `veille-differe`, et implémente dans le socle (docs/30, tokens et primitives, aucune identité
     nouvelle). Le bloc `surface visible :` est une détection : il reste affiché, et n'appelle plus
     de geste pour ce ticket. Sa relecture visuelle reste jouée à la clôture (`/ticket-finish`,
     étape 4bis), et c'est la session qui la juge.
   - **Bloc absent et `apps/web/` intact** : aucun écran, rien à dire.

   ⚠ Jusqu'au 2026-09-21, toute surface visible non arbitrée faisait **proposer** une veille en
   interactif (#714) et la faisait **jouer** en run (#934), et une veille non jouée se **différait**
   en ticket satellite (#795). En septembre, onze tickets « Veille de conception à jouer » ont été
   fermés, et une trentaine de finitions ont porté chacune leur veille. docs/40 §3 réserve la
   cérémonie aux tickets qui décident d'un écran, là où la qualité se décide.

6. **Résumé court, puis enchaîne immédiatement** — sur l'étape 7, puis l'implémentation : nom de la branche, titre
   du ticket, dates posées, critères d'acceptation ; si le brief porte une section **`## Rendu
   attendu`**, relaie-la — ses rubriques telles qu'écrites (question, référence, ce qui ne bouge
   pas, états à couvrir), ou le fait qu'elle est « non renseigné » (#976). C'est l'attente contre
   laquelle l'écran sera jugé : elle cadre l'implémentation au même titre que les critères, et ne
   la réécris pas à ta façon. Section absente du brief : ne dis rien. Une section « non renseigné »
   **n'est pas une pause** — dis-le en une ligne et enchaîne ; pour un sous-ticket, le parent, le rang du
   lot, ses tests différés s'il en porte (« tests différés → #<iid> » — seulement un lot né
   avant #1150 : pour lui, livrer sans tests reste prévu ; tout autre lot livre les siens) et, s'il
   y en a, les **autres lots démarrables en parallèle** (`bash
   scripts/gitlab/lib.sh startables <iid-parent>`) — de quoi permettre à quelqu'un d'autre d'en
   prendre un tout de suite. Le résumé cadre le travail, ce n'est **pas une demande de validation** : n'attends
   aucun « go » et commence tout de suite (les critères d'acceptation font foi). Ne t'arrête pour
   demander que si le ticket est réellement ambigu au point de ne pas pouvoir commencer — la forme
   d'un écran n'en est pas un cas : l'étape 7 la tranche (#1009).

   **L'implémentation suit une méthode** (#1241), la même en run et en interactif — trois gestes,
   pas une cérémonie :
   - **Lis avant d'écrire** : le code que le ticket touche et les tests qui le gardent. Ils disent
     ce qui ne doit pas bouger, et où le test du ticket s'écrit.
   - **Un bug commence par son test** : écris d'abord le test qui reproduit le défaut et vois-le
     échouer, puis corrige jusqu'à le voir passer. Un test qu'on n'a jamais vu échouer ne prouve
     pas qu'il attrape le défaut.
   - **Exerce chaque critère avant de clore** : un critère se clôt sur une preuve exercée — un test
     nommé que tu as vu passer, ou une observation sur la vraie stack —, jamais sur un fichier du
     diff (#1240). La conduite, et le banc des scénarios quand le diff touche leur chemin, vivent à
     l'étape 4ter de `/ticket-finish`, qui les consigne.

7. **Variantes — un ticket qui DÉCIDE de l'écran tranche sa forme sur pièces, puis l'implémente**
   (#979, renversé par #1009 ; chantier #972). La question est celle du §7.2 de `/design-veille` —
   *ce ticket décide-t-il de quelque chose à l'écran, ou applique-t-il une décision déjà prise ?* —,
   et son critère n'est écrit **que là** : juges-en par lui, sans le recopier. Elle se pose sur tout
   ticket qui touche un écran — bloc `surface visible :` de l'étape 1, section `## Rendu attendu`
   écrite, ou ce que tu t'apprêtes à modifier sous `apps/web/` —, que la veille ait été jouée, jugée
   inutile ou arbitrée avant toi : la veille dit *ce qu'on vise*, les variantes *laquelle de ces
   formes*. **Personne n'est attendu, dans aucun régime** (#1009) : le choix se rend **sur pièces** et
   se **consigne**, ce qui le rend contestable après coup — jamais une pause, jamais un arrêt. Aucun
   écran touché : passe sans rien dire.
   - **Un choix est déjà consigné** — un commentaire du ticket qui **commence** par
     `## Variante retenue` (`gh issue view <iid> --comments`, lu seulement quand le ticket décide) :
     le ticket **applique** désormais
     une décision prise. Implémente-la, sans reposer la question.
   - **Il applique** : pas de variantes, et enchaîne. Dis-le **en une ligne**, avec ta raison —
     c'est ton jugement, et il se lit dans ton résumé.
   - **Il décide** — en session interactive **comme en run**, et sans rien demander :
     1. **Les références d'abord.** Si aucun commentaire du ticket ne **commence** par
        `## Veille de conception`, ouvre `/design-veille <surface>` maintenant — la surface que le
        ticket retouche, que l'étape 5 l'ait détectée ou non (#928 ne portait ni `agent::design` ni
        route nommée, et il décidait). C'est elle qui va chercher les **produits professionnels
        comparables** à ce que le ticket attend et en **capture** au moins deux : sans ces captures,
        il n'y aurait rien à quoi comparer tes variantes. Ouverte d'ici, elle consigne ses partis pris
        puis pose l'arbitrage (son §7.3), dans les deux régimes.
     2. **Des brouillons sur la vraie stack, pas une maquette.** Une variante est le minimum de code
        qui rend sa direction visible — tokens et primitives du socle, aucune identité nouvelle
        (docs/30 §6.1), ni tests ni finitions. Les variantes divergent sur **ce que le ticket
        décide**, jamais sur un détail. Deux ou trois, pas une galerie ; une variante **unique** n'est
        pas un choix mais une **validation**, et se consigne comme telle. Figma sert à explorer avant
        de brouillonner, jamais de capture (docs/30 §5.1).
     3. **Rien ne reste dans l'arbre.** Un brouillon ne crée **aucun fichier** (un composant neuf se
        brouillonne dans un fichier existant), pour que `git diff` le contienne tout entier et que
        `git restore` le défasse tout entier. Écris le premier, puis monte la stack par
        `bash scripts/design/relecture-visuelle.sh <iid>` — son plan se dérive du diff, donc d'un
        brouillon présent : la **vraie stack** sur les ports du worktree — l'état du dernier passage
        du banc, dont la préparation nomme les projets à poser (#1165) —, et l'**avant** servi depuis
        `origin/main`, qui est la référence de « ce qui ne bouge pas » ; prépare le navigateur et
        attends qu'une page soit prête comme le disent les étapes 3 et 4 du skill
        `relecture-visuelle`. L'UI tourne en `next dev` : les brouillons suivants s'échangent à chaud.
        Pour chaque lettre : écris-le, capture en chemin **relatif** vers
        `.maestro/variantes/<iid>/<lettre>/<ecran>-<theme>.png` — **jamais sous `.maestro/relecture/`**,
        que `--couverture` compterait à la clôture comme un regard porté sur l'écran livré —, relis
        chaque capture (`Read`), sauve le brouillon par
        `git diff > .maestro/variantes/<iid>/<lettre>.patch`, puis `git restore` ses fichiers. Un
        thème suffit à choisir une direction ; les deux sont l'affaire de la relecture.
     4. **Avant le choix** : `bash scripts/design/relecture-visuelle.sh --fin`, `browser_close`, et
        `git status --porcelain` **vide**. Le choix se rend sur le disque, pas sur l'écran, et aucune
        variante non retenue ne doit pouvoir finir dans un commit si la session est coupée d'ici là.
     5. **Le choix est rendu par le regard neuf**, pas par toi (#980 : l'auteur des brouillons voit ce
        qu'il a voulu faire). Écris avec `Write` sa saisine, `.maestro/variantes/<iid>/saisine.md`,
        en chemins **absolus** (le sous-agent ne connaît pas ton répertoire — `pwd` te le donne) :
        pour chaque lettre, ses captures et ce qu'elle décide en une ligne ; les captures de
        référence de la veille (`.maestro/session/design-veille/`) ; l'avant ; la section
        `## Rendu attendu` du ticket (ou « non renseigné ») et ses critères d'acceptation ; les
        commentaires qui **commencent** par `## Veille de conception`, recopiés tels quels — **ni le
        code, ni le diff, ni ton raisonnement** ; et ce gabarit, à rendre rempli :
        ```
        ### Choix — #<iid>
        | variante | rendu attendu | partis pris | références | ce qui ne bouge pas |
        |---|---|---|---|---|
        | A | … | … | … | … |
        **Retenue : <lettre>** — <pourquoi, références à l'appui>
        **Écartées** : <lettre> — <pourquoi> ; …
        ```
        Puis `Agent`, `subagent_type: "regard-neuf"`, et pour prompt la phrase de la relecture, au mot
        près : « Ta saisine : <chemin> — lis-la, puis rends-la remplie. » (même repli que le skill
        `relecture-visuelle` si l'agent est introuvable, et nommé). Sa réponse va telle quelle dans
        `.maestro/variantes/<iid>/choix.md`. Il en **retient toujours une** : la saisine existe parce
        que personne d'autre ne choisira. S'il n'en retient aucune, ou répond « non vu » faute de
        pièces, complète la saisine et saisis-le **une** fois de plus ; au second refus, tranche
        toi-même sur les mêmes pièces, et dis-le dans la consignation.
     6. **Consigne le choix avant la première ligne d'implémentation** : écris avec `Write`
        `.maestro/session/variante-<iid>.md`, qui commence par `## Variante retenue` et dit laquelle,
        qui l'a retenue (le regard neuf, ou toi au second refus), celles écartées et pourquoi, et les
        **références** qui ont tranché, puis `bash scripts/gitlab/lib.sh issue-note <iid> <fichier>`.
        Consignation en échec : n'implémente pas, dis-le et réessaie. Ensuite seulement, repars du
        brouillon retenu (`git apply .maestro/variantes/<iid>/<lettre>.patch`) et implémente pour de
        bon.
     7. **Dis-le**, dans ton résumé : la variante retenue, ses captures (en liens), ce qu'elle a
        battu et sur quelles pièces. Ce n'est **pas une question** : qui veut une autre forme consigne
        une nouvelle `## Variante retenue` sur le ticket — ou en ouvre un —, et la relecture visuelle
        de `/ticket-finish` juge l'écran livré contre ce choix.

   **Ce que #1009 a renversé** (docs/30 §5.8). Jusque-là, un ticket qui décidait **attendait** une
   personne : en interactif une question, en run un `ECHEC` qui l'écartait du plan — et son premier
   run a arrêté #928 et fait sauter les quatre lots suivants de son parent. Un run traite désormais
   tous les tickets et tranche. Ce qui sépare ce choix de celui que #979 avait écarté, « la variante
   la plus proche des partis pris », est ce qu'il a sous les yeux : des références vérifiées en
   direct, des variantes rendues, un juge qui n'en est pas l'auteur, et une trace écrite avant le
   code. **Un choix sans ces pièces reste un choix fabriqué**, et ne se fait pas.

Pas de Pull Request à ce stade (aucun commit à proposer). La clôture passe par les commandes
dédiées — `/ticket-ship` (commit auto + push + PR + état) ou `/ticket-finish` (commit déjà
fait) — jamais ré-implémentée à la main : les skills en sont la source unique.
