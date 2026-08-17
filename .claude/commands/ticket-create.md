---
description: Crée un ticket bien formé (labels type::/agent::/prio:: + corps de template, état « À faire »)
argument-hint: "<type: feature|bug|doc|infra> <titre>  (le reste peut être précisé en dialogue)"
allowed-tools: Bash(git:*), Bash(gh:*), Bash(bash:*), Read
---

Tu vas créer un **nouveau ticket** bien formé selon les règles de Maestro (résumées
ci-dessous — cette commande est autosuffisante ; réf. complète `docs/10-workflow-git.md`, non
chargée automatiquement, à n'ouvrir qu'en cas de doute). C'est le pendant amont de `/ticket-start` :
cette commande **crée** le ticket (état « À faire », posé par le label `workflow::a-faire`) mais
**ne crée pas de branche** et **n'assigne pas** — c'est le rôle de
`/ticket-start <iid>` ensuite. Arrête-toi et demande dès qu'une information nécessaire manque au
lieu d'inventer.

1. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Si ça échoue, arrête-toi et
   relaie son message : il nomme déjà la commande d'authentification de la **forge active**
   (`gh auth login` sur GitHub, `glab auth login` tant que le dépôt est sur GitLab). Ne devine pas
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
     via `bash scripts/gitlab/lib.sh set-description <iid-parent> <fichier>` — jamais un
     `--description "$(cat …)"`, que la couche permissions refuse (substitution `$(…)`) et qui
     n'existe pas des deux côtés.

   Si le besoin tient en une session, continue simplement : **ticket unique**, même s'il est
   multi-facettes — matérialise alors les facettes par une **checklist interne** dans la
   description (pas de parent ni de sous-tickets). Étapes suivantes.

5. Charge le squelette de description depuis le template correspondant et lis-le :
   `feature`→`.gitlab/issue_templates/Feature.md`, `bug`→`Bug.md`, `doc`→`Doc.md`,
   `infra`→`Infra.md`. Ces gabarits se lisent comme des **fichiers** — leur chemin ne dépend
   d'aucune forge, et leur éventuel déménagement vers `.github/` appartient au retrait de
   l'outillage GitLab (#344). **Retire la dernière ligne `/label ~"type::…"`** du template (le label
   sera posé via `--label` à la création ; la quick action n'est pas exécutée par l'API). Remplis les
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

9. Crée le ticket. Le corps multi-lignes passe **par un fichier**, jamais sur la ligne de commande :
   la couche permissions découpe un appel sur ses sauts de ligne et ne matche aucune substitution
   `$(…)`, si bien qu'un `--body "$(cat …)"` serait refusé alors même que `gh issue create` est
   autorisé (docs/10 §11.7). C'est `--body-file` qui porte le corps :
   ```
   gh issue create \
     --title "<titre>" \
     --label "type::<type>,agent::<rôle>,prio::<niveau>,workflow::a-faire" \
     --milestone "<milestone-de-phase>" \
     --body-file <fichier-de-corps>
   ```
   ⚠ Tant que le dépôt n'est pas basculé (#343), c'est le CLI de la **forge active** qui crée le
   ticket : vérifie-la (`bash scripts/gitlab/lib.sh forge-cli`) et, si elle rend `glab`, le geste
   équivalent est `glab issue create --title … --label … --milestone … --description "$(cat …)" --yes`.
   Créer le ticket sur la mauvaise forge est le seul geste de cette commande qui échoue **en
   silence** — il réussit, ailleurs.
   Le `workflow::a-faire` n'est pas décoratif et **ne s'ajoute pas après coup** : le cycle de vie
   étant porté par des labels (docs/10 §3), plus aucun défaut ne le pose à la création — un ticket
   créé sans lui n'a **aucun** état, ce que `doctor.sh` signale comme une dérive. Le poser dans le
   même `--label` évite un second appel, et c'est le seul cas du workflow où l'état ne passe pas par
   `set-workflow` : il n'y a encore rien à retirer. N'assigne pas et ne crée pas de branche.

10. Termine par un résumé court : l'IID et l'URL du ticket créé, ses labels et son milestone —
   pour un découpage : le parent et chaque sous-ticket avec son rang dans la checklist. Puis la
   suite :
   - si l'utilisateur a demandé (explicitement ou par le contexte de la conversation) de
     **réaliser** le travail, enchaîne directement sur `/ticket-start <iid>` sans attendre de
     « go » (pour un découpage : sur le **premier sous-ticket** de la checklist, jamais sur le
     parent) ;
   - sinon, propose simplement `/ticket-start <iid>` pour démarrer plus tard.
