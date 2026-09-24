---
description: Termine le travail sur le ticket courant (push + PR prête + état « En revue » + merge)
argument-hint: "[issue-iid] (optionnel si le nom de la branche courante le contient déjà)"
allowed-tools: Bash(bash:*), Bash(git:*), Bash(gh:*), Bash(mkdir:*), Bash(.venv/Scripts/python.exe:*), Bash(.venv/bin/python:*), ExitWorktree, Skill, AskUserQuestion, Read, Edit, Write
---

Tu clôtures le ticket de la branche courante, **jusqu'au merge**. Cette commande porte la règle et
l'ordre des gestes ; la raison de chaque étape vit dans la doc, indexée par étape en
[docs/10 §6.1](../../docs/10-workflow-git.md) (#1245). Avant une écriture partagée (push, PR,
merge), arrête-toi et demande si un point n'est pas clair.

1. **L'IID** : `$ARGUMENTS`, sinon la branche (`<type>/<iid>-<slug>`), sinon demande-le.

2. `bash scripts/gitlab/lib.sh require` — arrête-toi si la forge n'est pas authentifiée.

3. **Garde-fou de clôture**, avant toute écriture :
   ```
   bash scripts/gitlab/lib.sh close-guard <iid> || verdict=$?
   ```
   `0` poursuis · `1` partiel : signale-le et poursuis. Sinon arrête-toi : `3` la branche porte un
   **autre** ticket (propose de clôturer celui-là, ou `lib.sh branch-for <iid>`) · `4` ticket
   assigné à **quelqu'un d'autre**, nomme-le · `5` branche sans iid. Un refus ne se franchit que
   **sur demande explicite**, rappelée en tête du résumé.

4. **Le reste à committer.** Arbre sale : montre `git diff --stat`, propose un message Conventional
   Commits (pied `Refs #<iid>`, docs/10 §2) et **demande confirmation**. Message écrit avec `Write`
   dans `.maestro/session/`, puis `git commit -F .maestro/session/<fichier>` en chemin relatif — ni
   le scratchpad de session ni `/tmp`, jamais `-m` multi-ligne ni `$(…)`.

4bis. **Le rendu a-t-il été regardé ?**
   ```
   bash scripts/design/relecture-visuelle.sh --plan <iid>
   ```
   - **code `3` — aucune surface visible** : ne le mentionne pas, n'appelle aucun verbe, passe à
     l'étape 5. L'abstention nominale est muette.
   - **code `0`** : joue le skill `relecture-visuelle` (outil `Skill`), source unique de la
     séquence, sans la recopier. Le plan dit le régime et qui juge (#1243) : le regard neuf pour un
     ticket qui **décide** d'un écran, la session pour tout autre.

   **On ne demande pas, on joue**, à l'identique en run et en interactif. Puis consigne, toujours :
   ```
   bash scripts/gitlab/lib.sh relecture-note <iid> .maestro/relecture/<iid>/jugement.md
   ```
   ou, si la relecture n'a pas eu lieu, sa raison (écrite avec `Write`) :
   ```
   bash scripts/gitlab/lib.sh relecture-note --raison <iid> <fichier-de-la-raison>
   ```
   Refus avant toute écriture : `4` fichier absent ou vide, `3` iid inconnu, `5` jugement sans sa
   **grille** entière — réparé en rejouant le regard neuf du skill pour un ticket qui décide d'un
   écran, en complétant ta propre grille pour tout autre. Un `1` (forge muette) ne bloque pas.
   Nomme au résumé la ligne `PLANCHE <chemin>`. Un constat corrigeable ici se corrige, puis reprends
   à l'étape 4 ; un constat qui appelle son propre ticket se nomme dans le jugement, avec sa suite
   — ouvrir le ticket reste une décision, pas un effet de bord de la clôture.

4ter. **Le ticket fait-il ce qu'il disait ?**
   ```
   bash scripts/gitlab/lib.sh criteres <iid>
   ```
   - **code `0`** : les critères, `C1`, `C2`… mot pour mot (un bug sans critères : « Comportement
     attendu » vaut `C1`). **Exerce chacun** (#1240) : un critère se clôt sur une preuve exercée,
     plus sur un fichier du diff. Deux preuves valent : **un test nommé qui passe**, joué et vu
     passer (la pièce donne son nom et son verdict) ; **une observation sur la vraie stack**, API ou
     UI de `start.sh` ou banc des scénarios (la pièce nomme le run, le passage ou la capture).

     **Le banc est dû** quand la question l'annonce (`# banc  à jouer`) : joue **avant de pousser**
     les scénarios dont ton changement est sur le chemin (docs/40 §5), puis `start.sh --stop` :
     ```
     bash scripts/controltower/start.sh --etat-banc --rejouer=<S…> --no-browser
     ```
     Son verdict entre par une ligne **Banc** : `vert`/`rouge` avec son passage, ou `non joué` avec
     sa raison. **Un banc injouable est nommé, jamais compté vert** ; un rouge ne se rejoue pas
     jusqu'au vert.

     Constat écrit avec `Write` dans `.maestro/session/criteres-<iid>.md`, une ligne par critère :
     ```
     | Critère | Réponse | Pièce |
     |---|---|---|
     | C1 | ✓ tenu | `tests/test_x.py::test_rend_0` passé |
     | C2 | ✗ non tenu | écrit dans `docs/…`, rien ne l'exerce |
     | C3 | hors diff | décision consignée sur le ticket |
     | Banc | vert | passage 20260923-154349 (S2) |
     ```
     ✓ : la pièce **nomme sa preuve exercée**. ✗ : rien ne l'a exercé, ou l'exercice a échoué, dis
     pourquoi. hors diff : dis ce qui le montre. Puis :
     ```
     bash scripts/gitlab/lib.sh criteres-note <iid> .maestro/session/criteres-<iid>.md
     ```
   - **code `3` — aucun critère écrit** : Signale-le par `bash scripts/gitlab/lib.sh
     criteres-note --aucun <iid>`, puis au résumé. N'écris pas les critères toi-même, ni ne le
     propose : écrits à la clôture, ils seraient taillés sur le livré.
   - **code `1`** : signale-le et poursuis.

   **Un critère non tenu est nommé, jamais coché.** `criteres-note` refuse (`5`) un ✓ sans preuve
   exercée ou sur un passage non vert, un banc dû sans ligne **Banc** : on répare en exerçant, ou en
   disant ✗ ou hors diff, jamais en cochant pour passer. Il vérifie qu'un test **existe**, pas qu'il
   passe : le dire vert sans l'avoir vu passer est un ✓ fabriqué. Son `1` (forge muette) ne bloque
   pas : signale-le. **Un ✗ n'empêche pas le merge** : corrige ce qui se corrige ici, puis reprends
   à l'étape 4 ; ce qui dépasse le ticket se consigne ✗ avec sa suite, et se nomme au résumé —
   ouvrir un ticket reste une décision. **On ne demande pas, on joue**, à l'identique en run et
   en interactif, banc dû compris ; seul le choix du scénario reste un jugement, et se consigne.

5. **Filet CI local**, source unique des contrôles locaux :
   ```
   bash scripts/ci/local.sh
   ```
   Périmètre du diff : ni `--complet` ni autre recette, le verdict complet est celui de la PR.
   `Verdict : VERT (déjà rendu)` est un vert : poursuis sans `--rejouer`. Jamais bloquant : un rouge
   de ton diff corrigeable en une passe se corrige (reprends à l'étape 4), sinon il se signale au
   résumé (journal `.maestro/ci-local/<job>.log`) et tu poursuis.

6. **Retard sur `origin/main`**, consultatif :
   ```
   bash scripts/gitlab/lib.sh behind-main || echo "verdict=$? (3=en retard, 4=+conflit probable)"
   ```
   `3` : au résumé. `4` : au résumé avec les fichiers, et propose le rebase à l'utilisateur ; sans
   réponse, pousse quand même. **Ne rebase jamais de toi-même** (force-push, docs/10 §6).

7. **Pousse la branche.** `git push -u origin <nom-de-la-branche>`, nom lu à l'étape 1, jamais une
   substitution `$(…)`. Jamais `--force` : un push rejeté, arrête-toi et explique pourquoi. Bloqué
   sur des identifiants (Windows) : `GIT_TERMINAL_PROMPT=0 git -c credential.helper='' -c
   credential.helper='!gh auth git-credential' push -u origin <nom-de-la-branche>` ; ce repli
   refusé (préfixe de variable, #235), signale-le, sans variante inventée.

8. **La description de la PR s'écrit dans un fichier**, jamais sur la ligne de commande.
   1. PR déjà ouverte ? `bash scripts/gitlab/lib.sh mr-iid` (code 1 si aucune).
   2. Fichier écrit avec `Write` dans `.maestro/session/`, en chemin relatif — ni le scratchpad de
      session ni `/tmp`, ni heredoc. Aucune PR : `Closes #<iid>`, une ligne vide, puis ce que la PR
      change et pourquoi. PR ouverte : relis sa description par `lib.sh get-mr-description <mr> >
      <fichier>` (jamais `gh pr view | python`, mojibake) et n'y ajoute que ce que 9.3 demande —
      `create-mr` la remplace entière ; rien à ajouter, passe à 9.2.

   **La PR ne porte pas de checklist** (#1244).

9. **Crée (ou mets à jour) la PR.**
   1. Un appel, idempotent (PR en Draft, titre lu sur le ticket) :
      ```
      bash scripts/gitlab/lib.sh create-mr <iid> <fichier>
      ```
   2. **Lève le brouillon, sans demander** — lancer `/ticket-finish`, c'est déclarer le travail
      fini ; sans effet sur une PR prête, et pas une promesse : `merge-mr` juge.
      ```
      gh pr ready <numéro>
      ```
   3. **Une écriture sous `.claude/` t'a été refusée ?** (#608) Deux gestes, jamais l'un sans
      l'autre. **Rends** le correctif intégral dans la description, sous la section
      `## Reste à appliquer à la main` (avant et après complets, fichier par fichier, avant 9.1).
      **Consigne-le** dans un ticket de reprise, qui survit au merge — correctif écrit avec `Write`
      dans `.maestro/session/`, puis :
      ```
      bash scripts/gitlab/lib.sh reste-claude <iid-du-ticket> <chemin-du-fichier>
      ```
      Nomme ce ticket au résumé. Ne contourne jamais le blocage (ni redirection, ni `cp`, ni
      script tiers).

10. **Aucun relecteur** : n'appelle ni `lib.sh set-reviewer` ni `gh pr edit --add-reviewer` (#196).

11. **État « En revue »**, dans le champ Status (docs/10 §3) ; vérifie qu'il réussit :
   ```
   bash scripts/gitlab/lib.sh set-workflow <iid> "En revue"
   ```

12. Renseigne le **temps passé**, mesuré, jamais estimé, sans demander (#1244) :
   ```
   bash scripts/gitlab/lib.sh log-time-mesure <iid>
   ```
   Il compte les tours des sessions du ticket et logge ce qui ne l'est pas, sa source au libellé.
   Code `3` : rien de loggé, c'est la bonne issue — aucune durée à la main. Recopie ses lignes.

13. **Attends le pipeline, merge, débloque ce qui est réparable** (#418, #460). Placée après « En
   revue » : une session qui meurt pendant l'attente laisse un ticket lisible.

   1. **Annonce l'attente** (« 2-4 min, 15 s'il tourne, 30 s'il n'est pas né »), puis :
      ```
      bash scripts/gitlab/lib.sh pipeline-wait <branche> || verdict=$?
      ```
      Ses codes formulent, ils ne décident pas : enchaîne sur `merge-mr` **dans tous les cas**. Un
      `6` (pas encore né) : note la durée ; le `gh workflow run` qu'il imprime vérifierait la
      branche, pas sa ref de merge — nomme-le au résumé, ne le joue pas (#595).
   2. **Merge** — jamais `gh pr merge`, en `deny` et dans `guard.sh` (#417) :
      ```
      bash scripts/gitlab/lib.sh merge-mr <iid> || verdict=$?
      ```
      - `0` → **mergé**. Le ticket se ferme par son `Closes`, « Terminé » vient du workflow `issues:
        closed` : ne pose rien. Worktree et branche partent à l'**étape 14**.
      - `3` → verdict pas encore rendu (run en cours, absent, périmé).
        **Repasse une fois, pas plus** : `pipeline-wait` puis `merge-mr`. Toujours `3` : PR
        ouverte, « En revue », dis-le — quelqu'un repassera, ou le drain d'un run (#419). Si
        `merge-mr` dit le pipeline « pas encore né », nomme-le ainsi, avec sa durée et le geste
        `gh workflow run` disponible, non posé. **N'enchaîne jamais sur `/mr-fix` ici** : un
        pipeline pas né n'a rien à réparer.
      - `4` → **pipeline rouge** · `5` → **conflit avec `origin/main`** → **enchaîne sur `/mr-fix
        <numéro>`, sans demander**, sans rien corriger toi-même (13.3).
      - `6` → **anomalie** (PR absente, fermée, brouillon, sans `Closes`, commits non poussés) :
        nomme-la, ne la contourne pas — c'est un geste humain. Ce n'est pas le `6` de
        `pipeline-wait`.
      - `7` → **déjà mergée**, par un autre : comme un `0`, étape 14 comprise, mais dis « déjà
        mergée » (#593).
      - `1`/`2` → outil ou usage : signale-le, ne merge pas.
   3. **Le déblocage, sur `4` et `5` seulement.** `/mr-fix` résout, puis merge ce qu'il débloque.
      - **Annonce l'attente** : « je lance `/mr-fix` : nouvelle attente de pipeline ».
      - **Ne repasse pas `merge-mr` derrière lui** : son étape 12 l'appelle déjà, et rendrait `7`
        (un `6` fabriqué avant #593).
      - **Deux tentatives au plus**, la seconde seulement si la première a fait bouger la PR —
        rejouer sur un état inchangé est un abandon, et le résumé le dit ainsi. Ce plafond est le
        tien : `MAESTRO_ORCHESTRATE_MRFIX_MAX` borne les sessions d'un run (#420).
      - Au-delà, ou si `/mr-fix` s'arrête avant son merge : PR ouverte, « En revue », cause rendue.

      ⚠ **En run autonome, n'enchaîne rien** : `guard.sh` refuse `pipeline-wait` et `merge-mr`, et
      ce refus est la fin normale de ta clôture. Le déblocage y appartient au **pilote** (#420).
   4. **Jamais** de force-push, de PR fermée, de cycle de vie reposé, ni de relance en boucle. Un
      refus de merge n'est pas un échec du ticket : c'est un état normal.

14. **Ramasse le worktree et la branche — sur `0` seulement** (et `7` ; #519). Ce ménage ne change
   jamais le verdict du merge. **N'entreprends rien sur `3`/`4`/`5`/`6`** : le travail vit encore
   dans ce worktree.
   1. **Sors du worktree** : `ExitWorktree`, `action: "keep"`. **Jamais `action: "remove"`**, qui
      court-circuiterait les garde-fous du ramassage. Aucune session de worktree active (verdict
      `ICI`) : reste où tu es et joue 14.2.
   2. **Retire le worktree, puis purge la branche**, dans cet ordre (`git branch -D` refuse une
      branche encore empruntée) :
      ```
      bash scripts/git/worktree.sh gc --iid <iid>
      bash scripts/gitlab/lib.sh cleanup-merged --auto <branche>
      ```
      Une abstention ne se contourne pas : `gc` garde exprès un travail **non sauvegardé**.
   3. Rends-en compte : ce qui a été retiré, ou la cause nommée par `gc`.

   ⚠ **En run autonome, elle ne se joue jamais** : `guard.sh` refuse le merge, et le pilote ramasse
   après le sien (#438).

15. Termine par un résumé : en tête, un refus de l'étape 3 **franchi sur demande** (lequel, qui) ;
   puis le **verdict du merge**, l'**issue du déblocage** sur sa propre ligne, le **ramassage**, le
   lien de la PR, le filet CI s'il n'était pas vert (quel job, pourquoi tu as poussé quand même),
   les signalements (`1` d'un verbe), le retard sur `origin/main`, le **temps** loggé
   (ou pourquoi rien), le **ticket de reprise** de 9.3, la planche, et la **confrontation des
   critères** sur sa ligne (`n ✓ · n ✗ · n hors diff`, chaque critère ✗ nommé, le banc s'il était
   dû, ou « aucun critère — signalé sur le ticket »).

   | Issue | À rapporter |
   |---|---|
   | **Mergé** (`0`, d'emblée ou après déblocage) | « PR #N mergée — #<iid> fermé, « Terminé » posé par le workflow » ; worktree et branche retirés, ou la cause de `gc` ; la session est dans le **clone principal** |
   | **Déjà mergé** (`7`) | « PR #N **déjà mergée** » ; ramassage comme pour un `0` |
   | **Non mergé** (`3`-`6`) | la cause rendue par `merge-mr`, l'état laissé (PR prête, « En revue ») et la suite : repasser sur `3`, le geste humain sur `6`, ce que le déblocage n'a pas levé sur `4`/`5` |
   | **Déblocage** | ⊘ **non tenté** (verdict non réparable, ou run) · ✅ **abouti** (ce qui a été réparé, tentatives) · ❌ **sans succès** (où il s'est arrêté, tentatives) |

   **Jamais de ✅ global** : une PR restée ouverte laisse la clôture **inachevée**, dis-le de ce mot.
   **« Non tenté » et « refusé »** ne se confondent pas : l'un est ton abandon, l'autre un verdict
   sur la PR. Ce qui merge, ou refuse, est `merge-mr` et ses quatre prérequis, jamais toi : **aucun
   merge non vérifié** (#417).
