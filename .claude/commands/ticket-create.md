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

4. Charge le squelette de description depuis le template correspondant et lis-le :
   `feature`→`.gitlab/issue_templates/Feature.md`, `bug`→`Bug.md`, `doc`→`Doc.md`,
   `infra`→`Infra.md`. **Retire la dernière ligne `/label ~"type::…"`** du template (le label sera
   posé via `--label` à la création ; la quick action n'est pas exécutée par l'API). Remplis les
   sections que tu peux à partir de ce que l'utilisateur a donné (contexte, objectif, critères
   d'acceptation). Ne fabrique pas de critères d'acceptation : si l'utilisateur ne les a pas
   fournis, laisse les cases `- [ ]` vides ou demande-les.

5. Détermine les labels de catégorisation (voir `docs/10-workflow-git.md` §3.2) :
   - `type::<type>` — **obligatoire**, déduit de l'étape 2.
   - `agent::<rôle>` — quel agent Maestro traitera le ticket
     (`dev`/`bdd`/`devops`/`design`/`qa`/`orchestrateur`). S'il ne se déduit pas clairement du
     contenu du ticket, **demande-le** à l'utilisateur ; s'il est évident (ex. un ticket sur le
     workflow d'orchestration → `orchestrateur`), pose-le et signale ton choix dans le résumé.
   - `prio::<niveau>` — `haute`/`moyenne`/`basse`. Par défaut `prio::moyenne` si non précisé.

6. Montre un récapitulatif **avant** création (titre, type, labels, corps). Si la création du
   ticket a été **explicitement demandée** par l'utilisateur (ou enchaînée par une commande ou une
   boucle d'orchestration amont), ce récapitulatif est **informatif, pas bloquant** : crée
   directement, sans attendre de validation. Ne demande confirmation **que** s'il a fallu deviner
   une information structurante (type ambigu, doute sur l'intention, contenu largement inventé).

7. Crée le ticket (le corps multi-lignes passe par un fichier temporaire pour éviter les soucis de
   quoting) :
   ```
   glab issue create \
     --title "<titre>" \
     --label "type::<type>,agent::<rôle>,prio::<niveau>" \
     --description "$(cat <fichier-de-corps>)" \
     --yes
   ```
   Ne pose **pas** de statut : « À faire » est le défaut du lifecycle à la création. N'assigne pas
   et ne crée pas de branche.

8. Termine par un résumé court : l'IID et l'URL du ticket créé, ses labels. Puis la suite :
   - si l'utilisateur a demandé (explicitement ou par le contexte de la conversation) de
     **réaliser** le travail, enchaîne directement sur `/ticket-start <iid>` sans attendre de
     « go » ;
   - sinon, propose simplement `/ticket-start <iid>` pour démarrer plus tard.
