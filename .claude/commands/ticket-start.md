---
description: Démarre le travail sur un ticket (branche + assignation + état « En cours »)
argument-hint: <issue-iid>
allowed-tools: Bash(bash:*), Bash(gh:*), Bash(git:*), EnterWorktree, Skill, Agent, AskUserQuestion, Read, Grep, Glob, Write, WebSearch, WebFetch, mcp__chrome-maestro
---

Tu vas démarrer le travail sur le ticket d'IID `$ARGUMENTS` selon les règles de Maestro (la raison
de chaque étape : `docs/10-workflow-git.md` §5 et §6.1, à n'ouvrir qu'en cas de doute). Suis les
étapes dans l'ordre et arrête-toi, en disant pourquoi, dès qu'une vérification échoue. Sans IID dans
`$ARGUMENTS`, demande-le avant de continuer.

1. **Préflight en un appel** : `bash scripts/gitlab/lib.sh start-brief $ARGUMENTS`. Il vérifie la
   forge (non authentifiée : arrête-toi et relaie son message, qui nomme `gh auth login`) et l'arbre,
   puis imprime le brief — titre, labels, critères, la section **`## Rendu attendu`** si le ticket
   en porte une écrite —, la ligne `statut : … — libre / pris par …`, le cas parent/sous-ticket et
   la branche proposée. Il informe, la décision reste la tienne :
   - **Changements non commités** : reporte la décision à l'étape 2. Worktree monté (`WORKTREE`) :
     ils restent intacts dans le répertoire qu'on quitte — signale-les et continue. Verdict `ICI` :
     arrête-toi et demande quoi en faire (committer, stasher, annuler), sans trancher à sa place.
   - **Ticket déjà pris** (`⚠ déjà pris par <username>` : « En cours » et assigné à un autre) :
     **arrête-toi** — `begin` remplacerait ses assignés. Dis qui l'a pris et oriente vers un ticket
     libre (`/backlog`). Ne le reprends que sur **demande explicite**.
   - **Parent de suivi** (la sortie liste ses **lots**) : ni branche ni code — ne le démarre pas et
     **ne touche pas à sa description** (lots en sub-issues natives, coche dérivée de leur état).
     Démarre le **premier** des « lots démarrables maintenant » en reprenant l'étape 1 avec son iid,
     et annonce les autres comme prenables en parallèle. N'en pose pas l'état : le `begin` du lot le
     passe « En cours ». Liste vide : rien à démarrer. **Ne propose pas de fermer le parent**, il se
     ferme avec son dernier lot ; un parent ouvert aux lots tous fermés est une anomalie à signaler
     (`bash scripts/gitlab/lib.sh lots-ouverts <iid-parent>` le confirme).
   - **Sous-ticket** : la sortie donne le parent, le rang (« lot n/total »), le marqueur
     « parallèle », les tests différés d'un lot né avant #1150 et le contrôle des lots précédents.
     Un lot précédent ⚠ non livré (« À faire » ou « En cours ») : arrête-toi, il passe d'abord. Ne
     bloquent pas : un lot précédent « En revue », ni un lot « (parallèle) » quand le lot visé l'est
     aussi.
   - **Ticket trop gros ?** Juge la **charge estimée** sur la description intégrale (`bash
     scripts/gitlab/lib.sh issue-raw $ARGUMENTS`). Les couches touchées sont un **signal**, jamais un
     déclencheur : ne propose le découpage qu'au-delà de **~1 session** (plusieurs couches
     substantielles, plus de 3-4 critères, des livrables indépendants). Au-delà : des livrables
     indépendants font des tickets sans parent ; **une** capacité qui dépasse une session fait du
     ticket le parent, et ses lots — 1-3 critères chacun, mergeables seuls, **chacun avec ses
     tests** (#1150) — se créent selon `/ticket-create` et se rattachent (`lib.sh issue-link
     <parent> <lot> [--parallele]`, puis `lib.sh subticket-order`), puis on démarre le premier
     lot. C'est une **vraie pause** : attends la décision.
   - **Branche sans préfixe** (pas de label `type::`) : déduis le type, ou demande s'il est ambigu.

2. **Worktree du ticket** (docs/10 §9) — le clone principal reste sur `main` :
   ```
   bash scripts/git/worktree.sh ensure $ARGUMENTS
   ```
   Son verdict est **la dernière ligne** :
   - **`WORKTREE <chemin>`** → relocalise la session avec **`EnterWorktree`** (`path` = ce chemin),
     puis continue de là. Cas nominal.
   - **`ICI <chemin>`** → le répertoire courant est le bon : **n'appelle pas `EnterWorktree`** (c'est
     ce qui laisse `run.sh`, qui monte lui-même le worktree, fonctionner).
   - **échec** (branche empruntée ailleurs, ticket sans `type::`) → arrête-toi et rapporte le message.

   `ensure` ramasse au passage les worktrees soldés, purge les branches mergées et remet les
   dépendances du clone principal à niveau ; il se tait quand il n'y a rien. Relaie au résumé un
   worktree conservé (travail non sauvegardé), une branche retenue, ou une mise à niveau annoncée
   ou en échec. ⚠ La relocalisation ne déplace **pas le bloc `env`** : ports de la Control Tower et
   profil de navigateur restent ceux du clone principal — `ensure` affiche ceux du worktree, à
   passer explicitement si le ticket démarre la stack ou pilote le navigateur.

3. **Branche**, source unique du placement : `bash scripts/gitlab/lib.sh start-branch
   <branche-proposée>` — en général sans effet après l'étape 2 ; il couvre le cas `ICI`.

4. **Démarrage groupé** : `bash scripts/gitlab/lib.sh begin $ARGUMENTS` — « En cours »,
   assignation, dates (début aujourd'hui, échéance selon `prio::`). Un échec se signale sans bloquer
   la branche créée. Ne touche pas aux labels `type::`/`agent::`/`prio::`.

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
     nouvelle). Le bloc `surface visible :` reste affiché, sans geste. Sa relecture visuelle est
     jouée à la clôture, jugée par la session.
   - **Bloc absent et `apps/web/` intact** : aucun écran, rien à dire.

6. **Résumé court, puis enchaîne immédiatement** — sur l'étape 7, puis l'implémentation : branche,
   titre, dates posées, critères d'acceptation ; si le brief porte une section **`## Rendu
   attendu`**, relaie-la telle qu'écrite (question, référence, ce qui ne bouge pas, états à couvrir),
   ou dis qu'elle est « non renseigné » (#976). C'est l'attente contre laquelle l'écran sera jugé ;
   ne la réécris pas à ta façon. Section absente : ne dis rien. « Non renseigné » **n'est pas une
   pause** — dis-le en une ligne et enchaîne. Pour un sous-ticket : le parent, le rang du lot, ses
   tests différés s'il en porte (« tests différés → #<iid> » — seulement un lot né avant #1150 ;
   tout autre lot livre les siens) et les **autres lots démarrables en parallèle** (`bash
   scripts/gitlab/lib.sh startables <iid-parent>`). Le résumé cadre, ce n'est **pas une demande de
   validation** : n'attends aucun « go », les critères d'acceptation font foi. Ne demande que si le
   ticket est ambigu au point de ne pas pouvoir commencer — la forme d'un écran n'en est pas un
   cas : l'étape 7 la tranche (#1009).

   **L'implémentation suit une méthode** (#1241), la même en run et en interactif :
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
   (#1009, docs/30 §5.8). La question est celle du §7.2 de `/design-veille`, et son critère n'est
   écrit **que là** : juges-en par lui. Elle se pose sur tout ticket qui touche un écran — bloc
   `surface visible :`, section `## Rendu attendu` écrite, ou ce que tu t'apprêtes à modifier sous
   `apps/web/` —, que la veille ait été jouée, jugée inutile ou arbitrée avant toi. **Personne n'est
   attendu, dans aucun régime** (#1009) : le choix se rend **sur pièces** et se **consigne**, donc se
   conteste après coup — jamais une pause, jamais un arrêt. Aucun écran touché : passe sans rien
   dire.
   - **Un choix est déjà consigné** — un commentaire qui **commence** par `## Variante retenue`
     (`gh issue view <iid> --comments`, lu seulement quand le ticket décide) : le ticket applique
     désormais une décision prise. Implémente-la, sans reposer la question.
   - **Il applique** : pas de variantes, et enchaîne. Dis-le **en une ligne**, avec ta raison.
   - **Il décide** — en session interactive **comme en run**, et sans rien demander :
     1. **Les références d'abord.** Si aucun commentaire du ticket ne **commence** par
        `## Veille de conception`, ouvre `/design-veille <surface>` maintenant — la surface que le
        ticket retouche, que l'étape 5 l'ait détectée ou non. Elle va chercher les **produits
        professionnels comparables** et en **capture** au moins deux, sans quoi il n'y aurait rien
        à quoi comparer tes variantes ; ouverte d'ici, elle consigne ses partis pris puis pose
        l'arbitrage (son §7.3).
     2. **Des brouillons sur la vraie stack, pas une maquette.** Une variante est le minimum de code
        qui rend sa direction visible — tokens et primitives du socle, aucune identité nouvelle
        (docs/30 §6.1), ni tests ni finitions. Elles divergent sur **ce que le ticket décide**,
        jamais sur un détail. Deux ou trois, pas une galerie ; une variante **unique** n'est pas un
        choix mais une **validation**, et se consigne comme telle. Figma explore, il ne capture pas.
     3. **Rien ne reste dans l'arbre.** Un brouillon ne crée **aucun fichier** (un composant neuf se
        brouillonne dans un fichier existant) : `git diff` le contient tout entier, `git restore` le
        défait. Écris le premier, puis monte la stack par
        `bash scripts/design/relecture-visuelle.sh <iid> --regime decide` — la vraie stack sur les
        ports du worktree, et l'**avant** servi depuis `origin/main`, référence de « ce qui ne bouge
        pas ». Prépare le navigateur et attends qu'une page soit prête (étapes 3 et 4 du skill
        `relecture-visuelle`) ; `next dev` échange les brouillons suivants à chaud. Pour chaque
        lettre : écris-le, capture en chemin **relatif** vers
        `.maestro/variantes/<iid>/<lettre>/<ecran>-<theme>.png` — **jamais sous `.maestro/relecture/`**,
        que `--couverture` compterait comme un regard porté sur l'écran livré —, relis chaque
        capture (`Read`), sauve le brouillon par `git diff > .maestro/variantes/<iid>/<lettre>.patch`,
        puis `git restore` ses fichiers. Un thème suffit à choisir une direction.
     4. **Avant le choix** : `bash scripts/design/relecture-visuelle.sh --fin`, `browser_close`, et
        `git status --porcelain` **vide** — aucune variante non retenue ne doit pouvoir finir dans
        un commit si la session est coupée.
     5. **Le choix est rendu par le regard neuf**, pas par toi (#980). Écris avec `Write` sa
        saisine, `.maestro/variantes/<iid>/saisine.md`, en chemins **absolus** (`pwd`) : pour chaque
        lettre, ses captures et ce qu'elle décide en une ligne ; les captures de référence de la
        veille (`.maestro/session/design-veille/`) ; l'avant ; la section `## Rendu attendu` (ou
        « non renseigné ») et les critères ; les commentaires qui **commencent** par
        `## Veille de conception`, tels quels — **ni le code, ni le diff, ni ton raisonnement** ; et
        ce gabarit, à rendre rempli :
        ```
        ### Choix — #<iid>
        | variante | rendu attendu | partis pris | références | ce qui ne bouge pas |
        |---|---|---|---|---|
        | A | … | … | … | … |
        **Retenue : <lettre>** — <pourquoi, références à l'appui>
        **Écartées** : <lettre> — <pourquoi> ; …
        ```
        Puis `Agent`, `subagent_type: "regard-neuf"`, et pour prompt, au mot près : « Ta saisine :
        <chemin> — lis-la, puis rends-la remplie. » (repli du skill `relecture-visuelle` si l'agent
        est introuvable, nommé). Sa réponse va telle quelle dans `.maestro/variantes/<iid>/choix.md`.
        Il en **retient toujours une**. S'il n'en retient aucune, ou répond « non vu » faute de
        pièces, complète la saisine et saisis-le **une** fois de plus ; au second refus, tranche
        toi-même sur les mêmes pièces, et dis-le dans la consignation.
     6. **Consigne le choix avant la première ligne d'implémentation** : écris avec `Write`
        `.maestro/session/variante-<iid>.md`, qui commence par `## Variante retenue` et dit
        laquelle, qui l'a retenue, celles écartées et pourquoi, et les **références** qui ont
        tranché, puis `bash scripts/gitlab/lib.sh issue-note <iid> <fichier>`. Consignation en
        échec : n'implémente pas, dis-le et réessaie. Ensuite seulement, repars du brouillon retenu
        (`git apply .maestro/variantes/<iid>/<lettre>.patch`) et implémente pour de bon.
     7. **Dis-le** dans ton résumé : la variante retenue, ses captures (en liens), ce qu'elle a
        battu et sur quelles pièces. Ce n'est **pas une question** : qui veut une autre forme
        consigne une nouvelle `## Variante retenue`, et la relecture visuelle de la clôture juge
        l'écran livré contre ce choix.

   **Ce que #1009 a renversé** — un ticket qui décidait attendait une personne (question en
   interactif, `ECHEC` en run) — se lit en docs/30 §5.8. **Un choix sans pièces reste un choix
   fabriqué**, et ne se fait pas.

Pas de Pull Request à ce stade (aucun commit à proposer). La clôture passe par `/ticket-ship`
(commit auto + push + PR + état) ou `/ticket-finish` (commit déjà fait), jamais ré-implémentée à la
main : les skills en sont la source unique.
