---
description: Démarre le travail sur un ticket GitLab (branche + assignation + statut « En cours »)
argument-hint: <issue-iid>
allowed-tools: Bash(git:*), Bash(glab:*), Bash(bash:*)
---

Tu vas démarrer le travail sur le ticket GitLab d'IID `$ARGUMENTS` selon les règles de Maestro
(résumées ci-dessous — cette commande est autosuffisante ; réf. complète `docs/10-workflow-git.md`,
non chargée automatiquement, à n'ouvrir qu'en cas de doute). Suis ces étapes dans l'ordre et
arrête-toi (en expliquant pourquoi) dès qu'une vérification échoue au lieu de forcer la suite.

1. Si aucun IID n'est fourni dans `$ARGUMENTS`, demande-le à l'utilisateur avant de continuer.

2. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Si ça échoue, arrête-toi et
   demande à l'utilisateur de lancer `glab auth login`.

3. Vérifie l'état de la copie de travail avec `git status --porcelain`. S'il y a des
   changements non commités, arrête-toi et demande à l'utilisateur quoi en faire (les
   committer, les stasher, ou annuler) — ne prends pas cette décision à sa place.

4. Récupère le ticket en version **compacte** (pour réinjecter moins de contexte) :
   `bash scripts/gitlab/lib.sh issue-brief $ARGUMENTS`. Le helper ne renvoie que le titre, les
   labels et la section « Critères d'acceptation » — l'essentiel pour cadrer le travail. Le
   `glab issue view $ARGUMENTS` intégral (description complète, « Pourquoi maintenant ? »…) reste
   disponible si tu as besoin de plus de détail, par exemple pour lever une ambiguïté de type au
   point 8.

5. **Ticket parent de suivi ?** Vérifie : `bash scripts/gitlab/lib.sh subtickets $ARGUMENTS`.
   Si la commande renvoie une liste (le ticket porte une section `## Sous-tickets`), c'est un
   **parent de suivi** (convention `docs/10-workflow-git.md` §5.1) : il ne porte ni branche ni
   code — **ne le démarre pas**. À la place :
   - **Synchronise sa checklist** au passage : coche (`- [x]`) les sous-tickets au statut
     « Terminé » encore décochés — mise à jour idempotente de la description via
     `glab issue update <iid> --description "$(cat <fichier>)"`, qui ne touche que ces cases et
     ne **décoche jamais** une case déjà cochée.
   - Repère le **premier sous-ticket ouvert** dans l'**ordre de la checklist** :
     - statut « À faire » → **redirige** : reprends la procédure à l'étape 4 avec l'iid de ce
       sous-ticket comme argument (c'est lui qu'on démarre). Si le parent était encore
       « À faire », passe-le « En cours » au passage
       (`bash scripts/gitlab/lib.sh set-status <iid-parent> "En cours"`).
     - statut « En cours » ou « En revue » → **arrête-toi** : ce lot est déjà en cours de
       travail, ou sa MR attend un merge dont le lot suivant dépend (l'ordre de la checklist
       fait foi). Dis quel lot bloque et ce qu'on attend (fin du travail, ou merge de sa MR).
     - aucun sous-ticket ouvert → tous les lots sont passés : signale que le **parent est
       fermable** (toutes cases cochées, y compris le lot tests) et qu'il n'y a rien à démarrer.

6. **Sous-ticket d'un lot ?** Vérifie : `bash scripts/gitlab/lib.sh parent-of $ARGUMENTS`. Si un
   parent est trouvé, contrôle l'**ordre des lots** : dans la checklist du parent
   (`bash scripts/gitlab/lib.sh subtickets <iid-parent>`), tous les sous-tickets qui **précèdent**
   celui-ci doivent être au statut « Terminé » (mergés). Si un lot précédent est encore ouvert,
   **arrête-toi** et explique : ce lot dépend d'une MR précédente non mergée — la faire merger
   d'abord. Sinon, continue normalement (un sous-ticket se démarre comme un ticket ordinaire).

7. **Ticket trop gros ?** S'il n'est ni un parent ni un sous-ticket, évalue sa taille sur la
   **description intégrale**, pas sur le seul `issue-brief` : lis `glab issue view $ARGUMENTS`
   — notes techniques et références croisées (tickets, docs, composants cités) comprises — et
   **compte les couches/composants distincts** touchés (moteur, backend, UI, script, commande,
   doc…). Le nombre de critères d'acceptation ne suffit pas : **dès 2 couches distinctes,
   propose le découpage**, même avec 3 critères ou moins (contre-exemple de référence : le #48,
   3 critères seulement mais moteur + backend + UI — enchaîné à tort). Si le ticket dépasse
   ainsi ~1 session de travail (≥ 2 couches touchées, ou plus de 3-4 critères d'acceptation),
   **ne l'enchaîne pas tel quel** : propose à l'utilisateur de le
   **découper** — le ticket courant devient le parent (section `## Sous-tickets` ajoutée à sa
   description), les sous-tickets sont créés et liés selon la convention de `/ticket-create`
   (1 à 3 critères chacun, chacun mergeable seul sur `main`, lot final « tests + doc »,
   `lib.sh issue-link <parent> <sous-iid>`), puis on démarre le premier lot. Contrairement au
   résumé de l'étape 13, c'est une **vraie pause** : attends la décision de l'utilisateur.

8. Détermine le préfixe de branche à partir du label `type::*` du ticket, via le helper :
   `bash scripts/gitlab/lib.sh branch-prefix <valeur-type>` (`feature`→`feat`, `bug`→`fix`,
   `infra`→`chore`, `doc`→`docs`). Si aucun label `type::*` n'est présent, déduis le type du
   titre/de la description, ou demande à l'utilisateur si ambigu.

9. Construis le slug avec le helper : `bash scripts/gitlab/lib.sh slug "<titre du ticket>"`
   (minuscules, accents retirés, non-alphanumérique → `-`, tronqué à ~40 caractères). Le nom de
   branche est `<type>/<iid>-<slug>`.

10. Mets `main` à jour, **nettoie au passage les branches déjà mergées**, puis crée la branche :
   ```
   git checkout main
   git pull origin main
   bash scripts/gitlab/lib.sh cleanup-merged
   git checkout -b <type>/<iid>-<slug>
   ```
   `cleanup-merged` est le pendant **automatique** de `/branch-cleanup` : maintenant qu'on est sur
   `main` à jour et que l'arbre est propre, il supprime les branches **locales** (hors `main` et hors
   branche courante) dont **GitLab confirme la MR `merged`** — et rien d'autre (garde-fou
   `docs/10-workflow-git.md` §6). S'il n'y a rien à nettoyer, il le dit et n'a aucun effet.
   `/branch-cleanup` reste disponible pour un nettoyage explicite hors démarrage de ticket.

11. Assigne le ticket et fais passer son **Status natif** à « En cours ». Le cycle de vie est
   porté par le champ **Status** de GitLab (lifecycle « Maestro »), pas par des labels — voir
   `docs/10-workflow-git.md` §3.
   - Assignation : récupère ton username via le helper
     `bash scripts/gitlab/lib.sh current-user` (il parse `glab api user` en shell pur — pas de
     dépendance à `jq`/`python`, et c'est couvert par l'allowlist pour ne pas déclencher de prompt),
     puis `glab issue update $ARGUMENTS --assignee <username>`.
   - Statut : `bash scripts/gitlab/lib.sh set-status $ARGUMENTS "En cours"`. Le helper résout le
     work item depuis l'iid et **dérive le GID du statut par nom** depuis le lifecycle « Maestro »
     (pas de GID en dur, robuste à une recréation du lifecycle). Vérifie que la commande réussit.
     Ne touche pas aux labels `agent::*` / `prio::*` / `type::*` (ils relèvent du triage, pas de ce
     workflow).

12. Renseigne les **dates** du ticket (voir `docs/10-workflow-git.md` §3.3) :
   ```
   bash scripts/gitlab/lib.sh start-dates $ARGUMENTS
   ```
   Le helper pose la **date de début = aujourd'hui** (conservée telle quelle si le ticket en avait
   déjà une — ré-exécution sûre) et l'**échéance = début + un délai dérivé du label `prio::`**
   (`haute` → 2 j, `moyenne` → 5 j, `basse` → 10 j ; défaut `moyenne` si absent). Vérifie que la
   commande réussit ; en cas d'échec, signale-le mais ne bloque pas la création de branche déjà
   faite.

13. Produis un résumé court : nom de la branche créée, titre du ticket, les dates posées
   (début / échéance), et la liste des critères d'acceptation trouvés dans la description — pour
   cadrer le travail qui commence. Pour un **sous-ticket**, mentionne aussi le parent et le rang
   du lot (« lot n/total de #<parent> »), et rappelle si ses tests sont **différés** vers le lot
   tests final (« tests différés → #<iid> » : ne pas s'étonner de livrer sans tests, c'est prévu).

14. **Enchaîne immédiatement sur l'implémentation.** Le résumé de l'étape 13 cadre le travail,
   ce n'est **pas une demande de validation** : n'attends aucun « go » de l'utilisateur et
   commence tout de suite à réaliser le ticket (les critères d'acceptation font foi). Ne
   t'arrête pour demander que si le ticket est réellement ambigu au point de ne pas pouvoir
   commencer.

Ne crée pas encore de Merge Request à ce stade (il n'y a pas encore de commit à proposer). La
**clôture** du cycle passe par les commandes dédiées : **`/ticket-ship`** (commit automatique +
push + MR + statut) une fois le travail terminé, ou `/ticket-finish` si le commit est déjà fait.
N'improvise jamais ce cycle à la main (`git commit`/`git push`/`glab mr create` directs hors de
ces commandes) : les skills en sont la source unique.
