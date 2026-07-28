---
description: Crée un ticket GitLab bien formé (labels type::/agent::/prio:: + corps de template, statut « À faire »)
argument-hint: "<type: feature|bug|doc|infra> <titre>  (le reste peut être précisé en dialogue)"
allowed-tools: Bash(git:*), Bash(glab:*), Bash(bash:*), Read
---

Tu vas créer un **nouveau ticket** GitLab bien formé selon les règles de Maestro (résumées
ci-dessous — cette commande est autosuffisante ; réf. complète `docs/10-workflow-git.md`, non
chargée automatiquement, à n'ouvrir qu'en cas de doute). C'est le pendant amont de `/ticket-start` :
cette commande **crée** le ticket (statut « À faire », le défaut du lifecycle) mais **ne crée pas de
branche** et **n'assigne pas** — c'est le rôle de
`/ticket-start <iid>` ensuite. Arrête-toi et demande dès qu'une information nécessaire manque au
lieu d'inventer.

1. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Si ça échoue, arrête-toi et
   demande à l'utilisateur de lancer `glab auth login`.

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
     fois les sous-tickets créés). Le parent ne porte ni branche ni code ; il **ne se ferme que
     quand toutes ses cases sont cochées**, en particulier celle du lot tests final.
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
   - **Mécanique** : crée d'abord le **parent** (étapes 5 à 9, section `## Sous-tickets` encore
     vide), puis chaque **sous-ticket** (étapes 5 à 9 pour chacun), lie chaque sous-ticket au
     parent — `bash scripts/gitlab/lib.sh issue-link <iid-parent> <iid-sous-ticket>` — et termine
     en remplissant la checklist du parent (`- [ ] #<iid> — <titre>`, ou
     `- [ ] #<iid> — <titre> (parallèle)`, dans l'**ordre de réalisation**, lot tests en dernier)
     via `glab issue update <iid-parent> --description "$(cat <fichier>)"`.

   Si le besoin tient en une session, continue simplement : **ticket unique**, même s'il est
   multi-facettes — matérialise alors les facettes par une **checklist interne** dans la
   description (pas de parent ni de sous-tickets). Étapes suivantes.

5. Charge le squelette de description depuis le template correspondant et lis-le :
   `feature`→`.gitlab/issue_templates/Feature.md`, `bug`→`Bug.md`, `doc`→`Doc.md`,
   `infra`→`Infra.md`. **Retire la dernière ligne `/label ~"type::…"`** du template (le label sera
   posé via `--label` à la création ; la quick action n'est pas exécutée par l'API). Remplis les
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

8. Détermine le **milestone de phase** : celui de la phase courante, résolu par
   `bash scripts/gitlab/lib.sh current-milestone` (= le milestone **actif le plus ancien non
   soldé** — un milestone dont tous les tickets sont fermés est sauté, sa fermeture étant une
   décision humaine). Si l'utilisateur a explicitement demandé un autre milestone, respecte son
   choix. Si le helper ne retourne rien (aucun milestone actif non soldé), **omets** simplement
   l'option — ne bloque pas la création pour ça.

9. Crée le ticket (le corps multi-lignes passe par un fichier temporaire pour éviter les soucis de
   quoting) :
   ```
   glab issue create \
     --title "<titre>" \
     --label "type::<type>,agent::<rôle>,prio::<niveau>" \
     --milestone "<milestone-de-phase>" \
     --description "$(cat <fichier-de-corps>)" \
     --yes
   ```
   Ne pose **pas** de statut : « À faire » est le défaut du lifecycle à la création. N'assigne pas
   et ne crée pas de branche.

10. Termine par un résumé court : l'IID et l'URL du ticket créé, ses labels et son milestone —
   pour un découpage : le parent et chaque sous-ticket avec son rang dans la checklist. Puis la
   suite :
   - si l'utilisateur a demandé (explicitement ou par le contexte de la conversation) de
     **réaliser** le travail, enchaîne directement sur `/ticket-start <iid>` sans attendre de
     « go » (pour un découpage : sur le **premier sous-ticket** de la checklist, jamais sur le
     parent) ;
   - sinon, propose simplement `/ticket-start <iid>` pour démarrer plus tard.
