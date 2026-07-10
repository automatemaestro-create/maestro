---
description: Démarre le travail sur un ticket GitLab (branche + assignation + statut « En cours »)
argument-hint: <issue-iid>
allowed-tools: Bash(git:*), Bash(glab:*), Bash(bash:*)
---

Tu vas démarrer le travail sur le ticket GitLab d'IID `$ARGUMENTS` selon les règles de Maestro
(réf. complète `docs/10-workflow-git.md` §5, à n'ouvrir qu'en cas de doute). Suis ces étapes dans
l'ordre et arrête-toi (en expliquant pourquoi) dès qu'une vérification échoue au lieu de forcer la
suite. Si aucun IID n'est fourni dans `$ARGUMENTS`, demande-le à l'utilisateur avant de continuer.

1. **Préflight en un appel** : `bash scripts/gitlab/lib.sh start-brief $ARGUMENTS`. Le helper
   vérifie les pré-requis (`glab` authentifié — sinon arrête-toi et demande un `glab auth login`)
   et l'arbre propre (changements non commités → arrête-toi et demande quoi en faire : committer,
   stasher ou annuler — ne décide pas à la place de l'utilisateur), puis imprime le brief du
   ticket (titre, labels, critères d'acceptation), le cas parent/sous-ticket et la branche
   proposée. Il est informatif : la décision (démarrer, rediriger, s'arrêter) reste la tienne :
   - **Parent de suivi** (la sortie liste une checklist `## Sous-tickets`) : il ne porte ni
     branche ni code — ne le démarre pas. Coche au passage (`- [x]`) les lots « Terminé » encore
     décochés dans sa description (`glab issue update <iid> --description "$(cat <fichier>)"`,
     idempotent — ne jamais décocher une case cochée). Puis selon le **premier sous-ticket
     ouvert** dans l'ordre de la checklist : « À faire » → reprends l'étape 1 avec son iid (c'est
     lui qu'on démarre ; si le parent était « À faire », passe-le « En cours » via
     `bash scripts/gitlab/lib.sh set-status <iid-parent> "En cours"`) ; « En cours »/« En revue »
     → arrête-toi et dis quel lot bloque et ce qu'on attend (fin du travail, ou merge de sa MR) ;
     aucun ouvert → signale que le parent est **fermable** (toutes cases cochées), rien à démarrer.
   - **Sous-ticket** : la sortie donne le parent, le rang (« lot n/total »), les tests différés
     éventuels et le contrôle des lots précédents. Si elle signale des lots précédents non mergés
     (⚠), arrête-toi : les faire merger d'abord. Sinon, il se démarre comme un ticket ordinaire.
   - **Ticket trop gros ?** (ni parent ni sous-ticket) : évalue la taille sur la **description
     intégrale** (`glab issue view $ARGUMENTS`, notes techniques et références croisées comprises)
     en comptant les **couches/composants distincts** touchés (moteur, backend, UI, script,
     commande, doc…) : **≥ 2 couches ⇒ découpage**, même avec 3 critères ou moins (contre-exemple
     de référence : #48), de même qu'au-delà de 3-4 critères d'acceptation. Ne l'enchaîne pas tel
     quel : propose le découpage — le ticket devient le parent (section `## Sous-tickets`), les
     sous-tickets sont créés et liés selon la convention de `/ticket-create` (1-3 critères chacun,
     mergeables seuls sur `main`, lot final « tests + doc », `lib.sh issue-link`), puis on démarre
     le premier lot. Contrairement à l'étape 4, c'est une **vraie pause** : attends la décision de
     l'utilisateur.
   - **Branche proposée sans préfixe** (label `type::` absent) : déduis le type du titre/de la
     description, ou demande à l'utilisateur si ambigu.

2. **Branche** — mets `main` à jour, purge les branches déjà mergées et crée la branche proposée,
   en **une seule commande composée** :
   ```
   git checkout main && git pull origin main && bash scripts/gitlab/lib.sh cleanup-merged && git checkout -b <branche-proposée>
   ```

3. **Démarrage groupé** : `bash scripts/gitlab/lib.sh begin $ARGUMENTS` — assignation, statut
   natif « En cours » et dates (début = aujourd'hui, échéance selon `prio::`) en une seule
   mutation. Vérifie que la commande réussit ; en cas d'échec, signale-le sans bloquer la branche
   déjà créée. Ne touche pas aux labels `type::`/`agent::`/`prio::` (triage, pas ce workflow).

4. **Résumé court, puis enchaîne immédiatement sur l'implémentation** : nom de la branche, titre
   du ticket, dates posées, critères d'acceptation ; pour un sous-ticket, le parent, le rang du
   lot et ses tests différés (« tests différés → #<iid> » : livrer sans tests est prévu, pas un
   oubli). Le résumé cadre le travail, ce n'est **pas une demande de validation** : n'attends
   aucun « go » et commence tout de suite (les critères d'acceptation font foi). Ne t'arrête pour
   demander que si le ticket est réellement ambigu au point de ne pas pouvoir commencer.

Pas de Merge Request à ce stade (aucun commit à proposer). La clôture passe par les commandes
dédiées — `/ticket-ship` (commit auto + push + MR + statut) ou `/ticket-finish` (commit déjà
fait) — jamais ré-implémentée à la main : les skills en sont la source unique.
