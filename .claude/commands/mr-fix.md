---
description: Rend une PR mergeable puis la merge — conflit avec origin/main, puis pipeline rouge ; aucun merge non vérifié
argument-hint: "[pr-numéro | branche]  (défaut : la branche courante)"
allowed-tools: Bash(bash:*), Bash(git:*), Bash(gh:*), Bash(mkdir:*), Bash(.venv/Scripts/python.exe:*), Bash(.venv/bin/python:*), Read, Edit, Write
---

Tu rends **une** PR mergeable, puis tu la merges par `merge-mr` (#415, #418), jamais autrement.
Deux blocages, traités **dans cet ordre** : un **conflit avec `origin/main`**, puis un **pipeline
rouge** (le merge exige un pipeline vert, `lib.sh merge-settings` le rend `pipeline_requis`).
L'ordre est la décision : merger `origin/main` peut lui-même casser le pipeline, et diagnostiquer
avant, c'est juger un état qui n'existera plus.

Tu ne produis que des commits intermédiaires (`Refs #<iid>`) et ne touches à **rien d'autre** du
cycle de vie. **Une résolution qui n'est pas claire ne se pousse pas, et donc ne se merge pas** :
tout arrêt de cette commande est un arrêt avant le merge. Arrête-toi, en disant pourquoi, dès
qu'un contrôle échoue. La raison de chaque étape vit dans
[docs/10 §8.3](../../docs/10-workflow-git.md) et §6.1 (#1245).

1. `bash scripts/gitlab/lib.sh require` — s'il échoue, arrête-toi et relaie son message.

2. **La branche cible** : un numéro de PR → sa branche (`gh pr view <numéro> --json
   headRefName`) ; un nom de branche → tel quel ; rien → `git branch --show-current`. Tires-en
   l'IID (`<type>/<iid>-<slug>`) pour le pied `Refs #<iid>` ; introuvable, demande-le.

3. **Garde-fous avant toute action** :
   - **Arbre propre requis** (`git status --porcelain`) : des changements qui ne viennent pas de
     cette commande, arrête-toi et demande — l'étape 4 lance un `git merge`.
   - Autre branche que la courante : `git checkout <branche>` puis `git pull origin <branche>`.
   - **Branche `main`** : diagnostic **seul** (étapes 5 à 7), ni commit, ni étape 4, ni étape 12 ;
     propose un ticket de bug.

4. **Conflit avec `origin/main`** :
   ```
   bash scripts/gitlab/lib.sh mr-conflict <branche> || echo "verdict=$? (3=conflit)"
   ```
   Un merge 3-way réel (`git merge-tree --write-tree`), en lecture seule ; ni `behind-main` ni
   l'indicateur asynchrone de la forge ne le remplacent.
   - **`0`** : passe à l'étape 5.
   - **`1`** : verdict impossible (pas d'`origin/main`, aucun ancêtre commun) — signale-le et
     poursuis sur le pipeline.
   - **`3`** : résous maintenant, par `git merge origin/main` — **jamais `git rebase`**, qui
     appellerait un force-push. Chaque fichier est une décision de contenu : jamais un côté en bloc
     (`--ours`/`--theirs`) pour effacer les marqueurs, et relis le résultat. **Pas clair : `git
     merge --abort`**, branche
     intacte, dis pourquoi et arrête-toi **sans merger**. Résolu : `git add -A`, commit et push
     comme à l'étape 9, puis vérifie que le verdict retombe à `0` et continue — ce merge a pu
     casser le pipeline.

5. **Le pipeline** : `bash scripts/gitlab/lib.sh pipeline-latest <branche>` → `id / status / sha /
   url`. La CI ne tourne que sur les PR ; le helper recompose le statut, ne le remplace par aucun
   appel direct. Après un commit poussé, son `sha` doit être `git rev-parse HEAD`.
   - `success` → merge (étape 12).
   - `created` / `pending` / `running` → `bash scripts/gitlab/lib.sh pipeline-wait <id>`, puis
     reprends sur le statut final. Rien à allumer : les exécutants sont ceux de la forge.
   - `failed` → continue.
   - Aucun pipeline après un push : sans PR ouverte c'est normal (la suite est `/ticket-ship`) ;
     PR ouverte et rien, déclenche : `gh run rerun <run-id>` s'il y a un run à rejouer, sinon
     `gh workflow run ci.yml --ref <branche>`, puis `pipeline-wait`. ⚠ Un run lancé ainsi
     diagnostique, il ne satisfait pas le contrôle requis : c'est un nouveau push qui le fera.

6. **Jobs rouges** : `bash scripts/gitlab/lib.sh pipeline-failed-jobs <pipeline-id>` → `id / name /
   stage / failure_reason`.

7. **Traces** : `bash scripts/gitlab/lib.sh job-trace <job-id> [lignes]` (défaut 100). Synthétise
   les lignes d'erreur utiles (fichier:ligne, règle, test en échec), jamais le log brut.

8. **Classe l'échec** — la trace tranche, `failure_reason` ne porte que le nom de l'étape :
   - **corrigeable en local** (lint, test, typage, script) → étape 9 ;
   - **non corrigeable en local** (installation, checkout, secret ou infrastructure, expiration,
     flaky) → **dis-le**, propose au plus un retry (`gh run rerun --failed <run-id>`, puis
     `pipeline-wait`), et arrête-toi **sans merger**. Jamais de correctif de code pour une panne
     d'infrastructure.

9. **Correctif local**, **deux tentatives au plus** :
   - le correctif **minimal** qui répond à la trace ;
   - rejoue le job rouge par le filet, `scripts/ci/local.sh`, source unique des contrôles locaux
     (docs/10 §8.4) — journal sous `.maestro/ci-local/<job>.log` :
     ```
     bash scripts/ci/local.sh --only <job>
     ```
     `<job>` est le nom rendu à l'étape 6 (`lint` et `test` désignent un étage entier). **Ne rejoue
     pas la suite entière**, ni `--complet` ni un `pytest -n auto` à la main. Pour reboucler sur le
     seul test rouge, vise-le selon sa famille (docs/10 §8.4bis) — une suite d'**outillage** (elle
     nomme un script du dépôt) dans le conteneur :
     ```
     bash scripts/ci/pytest.sh tests/test_<suite>.py -q
     ```
     une suite **applicative** au venv du poste, `<venv-python> -m pytest tests/test_<suite>.py`.
   - commit **intermédiaire**, pied **`Refs #<iid>`** (la PR porte le `Closes`), jamais
     `--no-verify`. Message écrit avec `Write` dans `.maestro/session/` (`mkdir -p .maestro/session`
     s'il manque), en chemin relatif — ni le scratchpad de session ni `/tmp`, ni heredoc, ni `-m
     "$(…)"` :
     ```
     git commit -F <fichier>
     ```
   - push, jamais `--force` : `git push origin <branche>` ; bloqué sur des identifiants,
     `GIT_TERMINAL_PROMPT=0 git -c credential.helper='' -c credential.helper='!gh auth git-credential' push origin <branche>`.

10. **Un nouveau pipeline démarre-t-il ?** `lib.sh pipeline-latest <branche>`, dont le `sha` doit être
    `git rev-parse HEAD`. Rien après ~30 s : déclenche comme à l'étape 5.

11. **Suis le verdict** : `bash scripts/gitlab/lib.sh pipeline-wait <nouveau-pipeline-id>`.
    `success` → étape 12 ; `failed` avec moins de deux tentatives → retour à l'étape 6 ; sinon,
    arrête-toi **sans merger** et rends ton diagnostic.

12. **Merge** — on n'arrive ici que par le haut : un arrêt des étapes 3 à 11 sort de la commande
    sans passer par elle, et ne rien merger y est le résultat, pas un oubli.
    ```
    bash scripts/gitlab/lib.sh merge-mr <branche> || verdict=$?
    ```
    Jamais `gh pr merge` : le geste nu reste en `deny` et dans `guard.sh`, qui jugent la commande
    lancée (#417). `merge-mr` ré-éprouve ses quatre prérequis, car la PR et `origin/main` ont pu
    bouger pendant l'attente.
    - `0` → **mergée** : le ticket se ferme par son `Closes`, « Terminé » vient du workflow. Ne pose
      rien.
    - `3` → verdict pas encore rendu, ou périmé : **repasse une fois, pas plus** (`pipeline-wait
      <branche>` puis `merge-mr <branche>`) ; toujours `3`, laisse la PR ouverte et dis-le.
    - `4`/`5` → le blocage est revenu (rouge à nouveau, ou `main` a bougé) : tes tentatives sont
      consommées, rends le constat. Relancer `/mr-fix` est une décision, pas une boucle.
    - `6` → **anomalie** (PR absente, fermée, **brouillon**, sans `Closes`, commits non poussés) :
      nomme-la et n'y touche pas — surtout, **ne lève pas le brouillon** : c'est le geste de
      `/ticket-finish`, pas d'une remédiation.
    - `7` → **déjà mergée**, par un autre : ni échec ni refus ; dis « déjà mergée », pas « mergée ».
    - `1`/`2` → outil ou usage : signale-le, ne merge pas.

13. **Résumé — trois issues, séparément**, jamais un ✅ global sur la foi du seul pipeline :
    | Blocage | À rapporter |
    |---|---|
    | **Conflit** | ✅ aucun / ✅ résolu (fichiers, hash du commit de merge) / ❌ laissé en place (fichiers, pourquoi ce n'était pas clair) |
    | **Pipeline** | ✅ vert / ❌ rouge (lien, jobs, correctif appliqué, tentatives) |
    | **Merge** | ✅ mergée (PR, ticket fermé par son `Closes`) / ✅ **déjà mergée** (`7`), pas de ton fait / ❌ **refusée** — la cause rendue par `merge-mr` / ⊘ **non tenté** — l'arrêt qui a précédé l'étape 12 |

    « Non tenté » et « refusé » ne se confondent pas : l'un est ton abandon, l'autre un verdict sur
    la PR. Conclus par ce qui reste à faire, et par qui. Ce qui merge est `merge-mr`, jamais toi.

**Hors de l'étape 12, aucune action de cycle de vie** : ni `gh pr merge`/`close`/`review`/`edit`, ni
`gh pr ready`, ni `set-workflow`, ni création de PR (c'est `/ticket-finish`). Jamais de
force-push, de `--no-verify`, ni de commit sur `main`. Dans le doute, abstiens-toi et demande.
