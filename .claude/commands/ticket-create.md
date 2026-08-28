---
description: Crée un ticket bien formé (labels type::/agent::/prio:: + corps de template, état « À faire », item du projet)
argument-hint: "<type: feature|bug|doc|infra> <titre>  (le reste peut être précisé en dialogue)"
allowed-tools: Bash(git:*), Bash(gh:*), Bash(bash:*), Read
---

Tu vas créer un **nouveau ticket** bien formé selon les règles de Maestro (résumées
ci-dessous — cette commande est autosuffisante ; réf. complète `docs/10-workflow-git.md`, non
chargée automatiquement, à n'ouvrir qu'en cas de doute). C'est le pendant amont de `/ticket-start` :
cette commande **crée** le ticket (état « À faire », posé dans le champ Status par `project-add`) mais
**ne crée pas de branche** et **n'assigne pas** — c'est le rôle de
`/ticket-start <iid>` ensuite. Arrête-toi et demande dès qu'une information nécessaire manque au
lieu d'inventer.

1. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Si ça échoue, arrête-toi et
   relaie son message : il nomme déjà la commande d'authentification de la **forge active**
   (`gh auth login`). Ne devine pas
   l'outil — `bash scripts/gitlab/lib.sh forge-cli` le dit en un mot.

2. Détermine le **type** du ticket depuis `$ARGUMENTS` (`feature`, `bug`, `doc` ou `infra`). S'il
   n'est pas explicite, déduis-le du titre/de l'intention et **confirme-le** avec l'utilisateur.
   Le type fixe le label `type::<type>` et le template d'issue.

3. Détermine le **titre** depuis `$ARGUMENTS`. S'il est absent ou vague, demande-le.

4. **Évalue la taille du besoin** (convention de découpage, `docs/10-workflow-git.md` §5.1). Un
   ticket doit tenir en **~1 session de travail**, et la taille se juge en **charge estimée** sur
   **toute la matière du besoin** — notes techniques et références croisées (tickets, docs,
   composants cités) comprises, pas seulement le nombre de critères d'acceptation. Les
   **couches/composants distincts** touchés (moteur, backend, UI, script, commande, doc…) sont un
   **signal** qui oblige à estimer finement, pas un déclencheur automatique : un script + sa doc
   tiennent en une session (ticket unique) ; le #48 — moteur + backend + UI, trois couches
   substantielles — non. Si le besoin **dépasse ~1 session** — plusieurs couches substantielles,
   plus de 3-4 critères d'acceptation, ou plusieurs livrables indépendants — ne crée pas un
   ticket monolithique : crée un **ticket parent de suivi + des sous-tickets liés** :
   - **Parent de suivi** : un ticket du type du besoin dont la description porte l'objectif
     global et une section `## Sous-tickets` — la checklist **ordonnée** des lots (remplie une
     fois les sous-tickets créés). Le parent ne porte ni branche ni code ; il reste ouvert tant
     qu'un lot l'est — lot tests final compris — et **se ferme tout seul** quand le dernier se
     ferme (#515, docs/10 §5.1). Rien à prévoir pour ça : c'est l'événement `issues: closed` qui
     le fait, pas la checklist.
   - **Sous-tickets** : un par lot d'~1 session, **1 à 3 critères d'acceptation chacun**, et
     surtout chaque lot **mergeable directement sur `main` sans casser l'existant** (code additif
     ou inoffensif tant que les lots suivants manquent). La description de chaque sous-ticket
     **commence par** `Sous-ticket de #<iid-parent> — lot <n>/<total>.` (marqueur détecté par
     `lib.sh parent-of`).
   - **Tests différés** : les tests sont un **sous-ticket dédié** — par défaut le **lot final
     « tests + doc »**. Les lots intermédiaires n'embarquent des tests que si leur logique est
     critique, et portent la mention « Tests différés → #<iid-du-lot-tests> ».
   - **Lots parallélisables** : suffixe le titre du lot dans la checklist du parent par
     **`(parallèle)`** quand il **ne dépend pas** des lots parallèles qui le précèdent — c'est le
     cas courant, les lots étant déjà additifs et mergeables seuls sur `main`. `/ticket-start` ne
     bloque alors plus ces lots entre eux : deux personnes peuvent les prendre en même temps. Le
     marqueur est **facultatif** ; un lot **sans** marqueur reste barré tant que tout ce qui le
     précède n'est pas livré — c'est ce qu'on veut pour le **lot final « tests + doc »** (jamais
     marqué) et pour un lot socle dont les suivants dépendent réellement.

     **La question se pose toujours, et sa réponse s'enregistre** (#562) : une fois la checklist
     remplie, enregistre l'arbitrage sur le **parent** — `bash scripts/gitlab/lib.sh arbitre
     <iid-parent>`, qui pose le label `lot::arbitre` — **quel qu'ait été ton verdict**, y compris
     « aucun lot n'est parallélisable ». Sans ce label, « aucun marqueur » est ambigu : il veut dire indifféremment
     « les lots dépendent vraiment les uns des autres » ou « personne n'y a pensé », et
     `/orchestrate` reproposera l'arbitrage à chaque run. Le poser ici est ce qui fait que le manque
     ne se reproduit pas — 16 des 42 parents du dépôt n'ont jamais été arbitrés faute de ce geste.
   - **Mécanique** : crée d'abord le **parent** (étapes 5 à 9, section `## Sous-tickets` encore
     vide), puis chaque **sous-ticket** (étapes 5 à 9 pour chacun), lie chaque sous-ticket au
     parent — `bash scripts/gitlab/lib.sh issue-link <iid-parent> <iid-sous-ticket>` — et termine
     en remplissant la checklist du parent (`- [ ] #<iid> — <titre>`, ou
     `- [ ] #<iid> — <titre> (parallèle)`, dans l'**ordre de réalisation**, lot tests en dernier)
     via `bash scripts/gitlab/lib.sh set-description <iid-parent> <fichier>` — jamais un
     `--description "$(cat …)"`, que la couche permissions refuse (substitution `$(…)`) et qui
     n'existe pas des deux côtés.

   Si le besoin tient en une session, continue simplement : **ticket unique**, même s'il est
   multi-facettes — matérialise alors les facettes par une **checklist interne** dans la
   description (pas de parent ni de sous-tickets). Étapes suivantes.

5. Charge le squelette de description depuis le template correspondant et lis-le :
   `feature`→`.github/ISSUE_TEMPLATE/feature.md`, `bug`→`bug.md`, `doc`→`doc.md`,
   `infra`→`infra.md`. Ces gabarits se lisent comme des **fichiers**. **Retire l'en-tête YAML**
   (le bloc entre les deux `---`) : il sert l'UI web de GitHub, où il nomme le gabarit et pose ses
   labels par défaut, et n'a rien à faire dans le corps d'un ticket créé en ligne de commande — les
   labels y sont posés par `--label`. Remplis les
   sections que tu peux à partir de ce que l'utilisateur a donné (contexte, objectif, critères
   d'acceptation). Ne fabrique pas de critères d'acceptation : si l'utilisateur ne les a pas
   fournis, laisse les cases `- [ ]` vides ou demande-les.

6. Détermine les labels de catégorisation (voir `docs/10-workflow-git.md` §3.2) :
   - `type::<type>` — **obligatoire**, déduit de l'étape 2.
   - `agent::<rôle>` — quel agent Maestro traitera le ticket
     (`dev`/`bdd`/`devops`/`design`/`qa`/`orchestrateur`). S'il ne se déduit pas clairement du
     contenu du ticket, **demande-le** à l'utilisateur ; s'il est évident (ex. un ticket sur le
     workflow d'orchestration → `orchestrateur`), pose-le et signale ton choix dans le résumé.
   - `prio::<niveau>` — `haute`/`moyenne`/`basse`. Par défaut `prio::moyenne` si non précisé.

7. Montre un récapitulatif **avant** création (titre, type, labels, milestone, corps — pour un
   découpage : le parent et la liste ordonnée des lots). Si la création du
   ticket a été **explicitement demandée** par l'utilisateur (ou enchaînée par une commande ou une
   boucle d'orchestration amont), ce récapitulatif est **informatif, pas bloquant** : crée
   directement, sans attendre de validation. Ne demande confirmation **que** s'il a fallu deviner
   une information structurante (type ambigu, doute sur l'intention, contenu largement inventé).

8. Détermine le **rail**, puis le **milestone**.

   **Le rail d'abord** (#617, docs/10 §3.4) : ce ticket touche-t-il l'**outillage de la forge** —
   workflow git, `scripts/**`, `.claude/**`, `.github/**`, filet CI, worktrees, orchestration — ou
   le **produit Maestro** (moteur, API, Control Tower, agents) ? C'est un **jugement**, au même
   titre que le choix d'`agent::` à l'étape 6, et il ne se dérive **pas** des labels : mesuré sur
   113 tickets classés par les fichiers que leurs commits ont touchés, le meilleur critère de
   labels plafonne à **91 %**, et il se trompe **systématiquement** sur le lot final « tests + doc »
   d'un chantier d'outillage (`agent::qa`, cf. #345/#363/#366/#414) et sur l'outillage de
   présentation (`agent::dev`, cf. #544→#547). Deux repères pour trancher :
   - **Un lot hérite du rail de son PARENT**, toujours — un « tests + doc » d'un chantier
     d'outillage est de l'outillage, quels que soient ses labels (vérifié : 8 lots sur 8 sont du
     rail de leur parent). Lis le rail du parent avec
     `bash scripts/gitlab/lib.sh milestone-rail "<son milestone>"`.
   - Dans le doute, demande — se tromper de rail range le ticket dans un backlog que personne ne
     regarde pour ce sujet.

   **Puis le milestone** : `bash scripts/gitlab/lib.sh current-milestone <rail>` avec `produit` ou
   `outillage` (= le milestone **actif le plus ancien de ce rail qui porte encore un ticket
   ouvert**). Deux milestones sont sautés, et le helper **nomme chaque saut sur stderr** avec sa
   cause (#619) : le **soldé** (N fermés / N total — la phase est finie, sa fermeture est une
   décision humaine) et le **vide** (0 / 0 — la phase n'est pas découpée, parfois **à dessein** :
   la Phase 9 « reste un contenant vide à dessein », docs/06-roadmap.md). **Relaie ce que stderr
   dit** dans le résumé de l'étape 10, au même titre que le rail : c'est ce qui explique pourquoi
   le milestone retenu n'est pas celui qu'on attendait.

   **Signale le rail retenu dans le résumé de l'étape 10** : c'est un jugement, il doit pouvoir
   être contredit. Si l'utilisateur a explicitement demandé un autre milestone, respecte son choix
   — c'est aussi le seul moyen de ranger le **premier** ticket d'une phase neuve dans son milestone
   encore vide, et c'est le prix assumé du saut ci-dessus. Si le helper ne retourne rien (tous les
   milestones du rail sont soldés ou vides), **omets** simplement l'option — ne bloque pas la
   création pour ça.

9. Crée le ticket. Le corps multi-lignes passe **par un fichier**, jamais sur la ligne de commande :
   la couche permissions découpe un appel sur ses sauts de ligne et ne matche aucune substitution
   `$(…)`, si bien qu'un `--body "$(cat …)"` serait refusé alors même que `gh issue create` est
   autorisé (docs/10 §11.7). C'est `--body-file` qui porte le corps :
   ```
   gh issue create \
     --title "<titre>" \
     --label "type::<type>,agent::<rôle>,prio::<niveau>" \
     --milestone "<milestone-de-phase>" \
     --body-file <fichier-de-corps>
   ```
   **Aucun label d'état ici** : les six `workflow::*` ont été retirés par #365 (docs/10 §3), le
   cycle de vie vivant désormais dans le champ Status que l'étape suivante pose. Le ticket sort donc
   de cet appel **sans état** — c'est normal, et c'est pourquoi l'étape suivante n'est pas
   optionnelle. N'assigne pas et ne crée pas de branche.

   Puis, **dans la foulée et sans attendre**, fais du ticket un **item du projet** :
   ```
   bash scripts/gitlab/lib.sh project-add <iid>
   ```
   **C'est cet appel qui donne son état au ticket, et c'est le seul** (chantier #358, docs/10 §3).
   Le champ **Status** de GitHub Projects v2 vit sur l'**item de projet** et non sur l'issue : un
   ticket absent du projet n'a donc **aucun** état — invisible de `queue.sh`, rendu « - » par
   `/backlog`, et rien à l'écran ne le distingue d'un ticket hors du filtre. La commande pose
   « À faire » par défaut ; elle est idempotente, donc la rejouer après un échec ne duplique rien.

   Son échec **ne défait pas la création**, mais il ne se range pas au rang des détails : le ticket
   existe avec ses labels et **sans état**, et depuis le retrait des labels (#365) plus rien ne
   rattrape ça — il n'y a pas de donnée de secours. Rejoue `project-add <iid>` tout de suite ; s'il
   échoue encore, dis-le franchement dans le résumé de l'étape 10 — un ticket sans état ne remonte
   dans aucune vue et personne ne le retrouvera.

10. Termine par un résumé court : l'IID et l'URL du ticket créé, ses labels et son milestone —
   pour un découpage : le parent et chaque sous-ticket avec son rang dans la checklist. Mentionne
   `project-add` **seulement s'il a échoué** : c'est le cas nominal qui n'a pas à occuper le
   résumé, et l'anomalie qui doit s'y voir — un ticket sans état est à traiter, pas à consigner.
   Puis la suite :
   - si l'utilisateur a demandé (explicitement ou par le contexte de la conversation) de
     **réaliser** le travail, enchaîne directement sur `/ticket-start <iid>` sans attendre de
     « go » (pour un découpage : sur le **premier sous-ticket** de la checklist, jamais sur le
     parent) ;
   - sinon, propose simplement `/ticket-start <iid>` pour démarrer plus tard.
