---
description: Démarre le travail sur un ticket (branche + assignation + état « En cours »)
argument-hint: <issue-iid>
allowed-tools: Bash(bash:*), Bash(gh:*), Bash(git:*), EnterWorktree, Skill, AskUserQuestion, Read, Grep, Glob, Write, WebSearch, WebFetch, mcp__chrome-maestro
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
     le ticket devient le parent (sa description garde l'objectif, rien de plus), les sous-tickets
     sont créés puis **rattachés en sub-issues** selon la convention de `/ticket-create` (1-3
     critères chacun, mergeables seuls sur `main`, lot final « tests + doc »,
     `lib.sh issue-link <parent> <lot> [--parallele]` puis `lib.sh subticket-order`), puis on
     démarre le premier lot.
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

5. **Veille de conception — la détection est automatique, le verdict jamais** (#714, #934,
   `docs/30 §5.2` et `§5.4`). Si et seulement si la sortie de l'étape 1 porte un bloc
   **`surface visible :`**, ce ticket touche un écran de la Control Tower et la question
   « qu'est-ce qu'on vise ? » n'a jamais été tranchée dessus. Alors, **et seulement alors** — et
   les deux régimes diffèrent sur **qui rend le verdict**, jamais sur le fait qu'il en faille un :
   - **En session interactive, demande** — une phrase, un « oui » explicite : jouer
     `/design-veille <surface>` avant d'écrire l'interface, ou passer. Ne la lance **jamais**
     d'office : une veille coûte des
     recherches web, des captures et du quota, et la jouer sur un correctif sans enjeu visuel
     serait du gaspillage. C'est le partage de #562 et #612 — ce qui est automatique est la
     **détection du manque**, jamais le verdict. Comme le découpage d'un ticket trop gros
     (étape 1), et contrairement au résumé de l'étape 6, c'est une **vraie pause** : attends la
     réponse.
   - **En session interactive, enregistre la réponse, quelle qu'elle soit** —
     `bash scripts/gitlab/lib.sh veille-arbitre $ARGUMENTS` — dès que la personne a tranché, que
     la veille ait été **faite** ou **jugée inutile**. Sans cet enregistrement, « inutile ici »
     est indiscernable de « personne n'y a pensé » et la question reviendra à chaque démarrage,
     jusqu'à ce qu'on cesse de la lire. N'enregistre **rien** tant que personne n'a répondu : un
     arbitrage posé d'office ferme la question sans que personne l'ait jugée. ⚠ Ce « quelle qu'elle
     soit » est **propre à l'interactif**, et c'est ce que #934 a tranché : ici le « non » vient
     d'une personne qui connaît le contexte, donc c'est un **jugement** ; en run il ne viendrait de
     personne, donc c'est une **abstention**, et une abstention ne s'enregistre pas.
   - **En session autonome** (run `/orchestrate`), il n'y a personne à qui demander — mais la
     veille s'y **joue** depuis #934 (`docs/30 §5.4`), l'accès web ayant été ouvert par #933. Ce
     qui disparaît est la **question**, pas le geste : n'attends aucun « oui », **ouvre
     `/design-veille <surface>`** en dérivant la surface du bloc `surface visible :`, et laisse la
     commande trancher — son §7.2 porte le critère (*ce ticket décide-t-il de quelque chose à
     l'écran, ou applique-t-il une décision déjà prise ?*), et il n'est écrit qu'à cet
     endroit-là. N'enregistre **rien toi-même** : si elle joue la veille, elle consigne ses partis
     pris sur le ticket puis pose l'arbitrage (§7.3) ; si elle s'abstient, elle n'écrit rien et te
     le dit.
   - **En session autonome, si la veille ne s'est pas jouée**, la question se **diffère** au lieu
     de se perdre (#795, `docs/30 §5.3`) — ce chemin ne se referme pas, il devient **rare** : une
     fois le ticket implémenté, écris avec l'outil `Write` un constat qui nomme la **surface**
     touchée et ce que tu as décidé à l'écran faute de référence, puis `bash
     scripts/gitlab/lib.sh veille-differe <iid> <fichier>`. Le ticket de veille naît **assigné** —
     donc hors des plans d'un run — et il **survit** à la fermeture du tien, ce qu'un résumé de fin
     de session ne fait pas : mesuré le 2026-08-30, sur 76 tickets livrés par un run, 13 touchaient
     une surface visible et **aucun** n'a été arbitré. Nomme-le quand même dans ton résumé final.
     N'appelle **jamais** `veille-arbitre` dans ce cas : un « non » qui ne vient de personne est une
     abstention, pas un jugement (#562).
   - **Bloc absent** : il n'y a rien à demander — soit le ticket ne touche aucune surface visible,
     soit l'arbitrage est déjà enregistré. Ne le mentionne pas, n'appelle pas le verbe, passe.

6. **Résumé court, puis enchaîne immédiatement** — sur l'étape 7, puis l'implémentation : nom de la branche, titre
   du ticket, dates posées, critères d'acceptation ; si le brief porte une section **`## Rendu
   attendu`**, relaie-la — ses rubriques telles qu'écrites (question, référence, ce qui ne bouge
   pas, états à couvrir), ou le fait qu'elle est « non renseigné » (#976). C'est l'attente contre
   laquelle l'écran sera jugé : elle cadre l'implémentation au même titre que les critères, et ne
   la réécris pas à ta façon. Section absente du brief : ne dis rien. Une section « non renseigné »
   **n'est pas une pause** — dis-le en une ligne et enchaîne ; pour un sous-ticket, le parent, le rang du
   lot, ses tests différés (« tests différés → #<iid> » : livrer sans tests est prévu, pas un
   oubli) et, s'il y en a, les **autres lots démarrables en parallèle** (`bash
   scripts/gitlab/lib.sh startables <iid-parent>`) — de quoi permettre à quelqu'un d'autre d'en
   prendre un tout de suite. Le résumé cadre le travail, ce n'est **pas une demande de validation** : n'attends
   aucun « go » et commence tout de suite (les critères d'acceptation font foi). Ne t'arrête pour
   demander que si le ticket est réellement ambigu au point de ne pas pouvoir commencer — ou que
   l'étape 7 te l'impose.

7. **Variantes — un ticket qui DÉCIDE de l'écran montre ses variantes, puis attend le choix** (#979,
   chantier #972). La question est celle du §7.2 de `/design-veille` — *ce ticket décide-t-il de
   quelque chose à l'écran, ou applique-t-il une décision déjà prise ?* —, et son critère n'est écrit
   **que là** : juges-en par lui, sans le recopier. Elle se pose sur tout ticket qui touche un écran —
   bloc `surface visible :` de l'étape 1, section `## Rendu attendu` écrite, ou ce que tu t'apprêtes à
   modifier sous `apps/web/` —, que la veille ait été jouée, jugée inutile ou arbitrée avant toi : la
   veille dit *ce qu'on vise*, les variantes *laquelle de ces formes*. Ce qui est automatique est la
   **détection**, jamais le choix (#562, #714). Aucun écran touché : passe sans rien dire.
   - **Un choix est déjà consigné** — un commentaire du ticket qui **commence** par
     `## Variante retenue` (`gh issue view <iid> --comments`, lu seulement quand le ticket décide) :
     le ticket **applique** désormais
     une décision prise. Implémente-la, sans reposer la question.
   - **Il applique** : pas de variantes, et enchaîne. En session interactive, dis-le **en une
     ligne**, avec ta raison — c'est ton jugement, et la personne le renverse en demandant les
     variantes.
   - **Il décide, en session interactive** : montre **2 ou 3 variantes rendues**, puis attends. Comme
     le découpage (étape 1) et la veille (étape 5), c'est une **vraie pause**.
     1. **Des brouillons sur la vraie stack, pas une maquette.** Une variante est le minimum de code
        qui rend sa direction visible — tokens et primitives du socle, aucune identité nouvelle
        (docs/30 §6.1), ni tests ni finitions. Les variantes divergent sur **ce que le ticket
        décide**, jamais sur un détail. Deux ou trois, pas une galerie ; une variante **unique** n'est
        pas un choix mais une **validation**, et se présente comme telle. Figma sert à explorer avant
        de brouillonner, jamais de capture (docs/30 §5.1).
     2. **Rien ne reste dans l'arbre.** Un brouillon ne crée **aucun fichier** (un composant neuf se
        brouillonne dans un fichier existant), pour que `git diff` le contienne tout entier et que
        `git restore` le défasse tout entier. Écris le premier, puis monte la stack par
        `bash scripts/design/relecture-visuelle.sh <iid>` — son plan se dérive du diff, donc d'un
        brouillon présent : ports du worktree, projet de démo, et l'**avant** servi depuis
        `origin/main`, qui est la référence de « ce qui ne bouge pas » ; prépare le navigateur et
        attends qu'une page soit prête comme le disent les étapes 3 et 4 du skill
        `relecture-visuelle`. L'UI tourne en `next dev` : les brouillons suivants s'échangent à chaud.
        Pour chaque lettre : écris-le, capture en chemin **relatif** vers
        `.maestro/variantes/<iid>/<lettre>/<ecran>-<theme>.png` — **jamais sous `.maestro/relecture/`**,
        que `--couverture` compterait à la clôture comme un regard porté sur l'écran livré —, relis
        chaque capture (`Read`), sauve le brouillon par
        `git diff > .maestro/variantes/<iid>/<lettre>.patch`, puis `git restore` ses fichiers. Un
        thème suffit à choisir une direction ; les deux sont l'affaire de la relecture.
     3. **Avant de poser la question** : `bash scripts/design/relecture-visuelle.sh --fin`,
        `browser_close`, et `git status --porcelain` **vide**. La réponse peut venir le lendemain, et
        la session être coupée d'ici là : aucune variante non choisie ne doit pouvoir finir dans un
        commit, et aucune stack ne doit tenir un port.
     4. **Présente**, variante par variante : ses captures (en liens), ce qu'elle décide en une
        ligne, sa confrontation au **rendu attendu** rubrique par rubrique — la question a-t-elle sa
        réponse d'un coup d'œil, la référence est-elle tenue, ce qui ne bouge pas a-t-il bougé (contre
        l'avant) — puis aux **partis pris de la veille** quand un commentaire du ticket en porte :
        lesquels elle tient, lesquels elle plie. Section « non renseigné » ou absente : dis-le, et
        confronte aux critères. Recommande-en une en le disant — une recommandation n'est pas un
        choix —, puis demande (`AskUserQuestion`, une option par variante).
     5. **Consigne le choix avant la première ligne d'implémentation** : écris avec `Write`
        `.maestro/session/variante-<iid>.md`, qui commence par `## Variante retenue` et dit laquelle
        (ou la direction que la personne a décrite à la place : c'est un choix aussi), celles écartées
        et pourquoi — dans ses mots quand elle en a donné —, puis
        `bash scripts/gitlab/lib.sh issue-note <iid> <fichier>`. Consignation en échec :
        n'implémente pas, dis-le et réessaie. Ensuite seulement, repars du brouillon retenu
        (`git apply .maestro/variantes/<iid>/<lettre>.patch`) et implémente pour de bon.
   - **Il décide, en session autonome** (run `/orchestrate`) : personne ne choisira, et **tu ne
     choisis pas à sa place**. Implémenter la variante la plus proche des partis pris et ouvrir la
     question après coup fabriquerait un choix que personne n'a fait, déjà parti dans `main` quand
     quelqu'un le lirait. Ne produis **aucune variante** — personne ne les regardera, et `gh` ne joint
     pas d'image à un ticket —, n'écris **aucune ligne** d'implémentation, et écarte le ticket du run :
     1. écris avec `Write`, dans `.maestro/session/`, ce que le ticket décide à l'écran, les partis
        pris de la veille à confronter s'il y en a, et qu'il **attend un choix** ; puis
        `bash scripts/gitlab/lib.sh issue-note <iid> <fichier>` ;
     2. **puis** `bash scripts/gitlab/lib.sh set-workflow <iid> "À faire"`, en **gardant
        l'assignation** posée à l'étape 4 : « À faire » **et** assigné est la protection qui tient un
        ticket hors des plans de run (#621), et c'est une personne qui le reprendra par
        `/ticket-start` — cette étape lui montrera les variantes ;
     3. termine sur `ORCHESTRATE: ECHEC choix de variante attendu`. Les lots suivants du parent seront
        sautés, et c'est juste : ils bâtiraient sur un écran que personne n'a choisi.

     L'ordre est celui de #934 : la trace, **puis** la protection — un ticket écarté sans trace ne
     dirait pas pourquoi. Aucun ticket à part : `veille-differe` en ouvre un parce que le ticket
     source se ferme au merge, or celui-ci ne se ferme pas, et la question se repose d'elle-même au
     prochain `/ticket-start` (le critère de #795 : *se repose-t-elle d'elle-même ?*).

Pas de Pull Request à ce stade (aucun commit à proposer). La clôture passe par les commandes
dédiées — `/ticket-ship` (commit auto + push + PR + état) ou `/ticket-finish` (commit déjà
fait) — jamais ré-implémentée à la main : les skills en sont la source unique.
