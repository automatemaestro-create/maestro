---
description: Traite le backlog en autonomie — un ticket, un worktree, une session Claude Code, de /ticket-start à /ticket-ship
argument-hint: "[--dry-run | --status | --resume <run-id> | --max <n>] (aucun argument = lance un run)"
allowed-tools: Bash(bash:*), Bash(git:*), Bash(glab:*), Bash(cat:*), Bash(ls:*)
---

Tu vas piloter la **boucle d'orchestration autonome** (`docs/10-workflow-git.md` §10) : elle traite
les tickets « À faire » du milestone courant **un par un**, chacun dans **son propre worktree** et
**sa propre session Claude Code**, de `/ticket-start` à `/ticket-ship`, sans interruption — et
**reprend toute seule** quand la limite d'usage de 5 h tombe au milieu.

**Le script est la source unique.** `scripts/orchestrate/` porte toute la mécanique ; cette commande
ne fait que la lancer, l'expliquer et lire son journal. Ne réimplémente **jamais** une étape à la
main (calcul de l'ordre, montage de worktree, lancement de session, verdict) : la boucle en est le
seul endroit.

**Ce que le pilote doit être — et ce que tu peux lancer.** Le pilote d'un run est **toujours un
script shell**, jamais une session Claude Code : une boucle écrite en `/loop` ou en sous-agents
consommerait le même quota que le travail piloté, la limite d'usage les tuerait ensemble, et plus
personne ne programmerait la reprise. Cela n'interdit pas de **démarrer** un run depuis ici :
`--detach` (#173) relance le script dans une **console indépendante** et rend la main tout de suite
— le pilote y est bien un shell, dans son propre processus. Ce qui reste exclu, c'est de lancer un
run **en arrière-plan de ta session** (il mourrait avec elle), et de réimplémenter la boucle
toi-même.

## Selon `$ARGUMENTS`

### Aucun argument, ou `--max <n>` — préparer et faire lancer un run

1. **Montre le plan** : `bash scripts/orchestrate/run.sh --dry-run` (lecture seule, aucun quota).
   Il imprime l'ordre de traitement figé, ce qui serait fait pour chaque ticket, et les garde-fous.
2. **Contrôle l'état de départ**, et dis ce qui cloche plutôt que de lancer quand même :
   - `bash scripts/gitlab/lib.sh require` — sinon `glab auth login` ;
   - `bash scripts/orchestrate/guard.sh --check` — le garde-fou ne doit pas avoir dérivé des
     règles `deny` du dépôt ; s'il sort en 1, **arrête-toi**, c'est la seule couche qui protège une
     boucle sans surveillance ;
   - `bash scripts/gitlab/ensure-runner.sh` — sans runner, chaque MR produite restera `pending` et
     rien ne pourra être mergé au matin ;
   - `git status --porcelain` sur le clone principal : un arbre sale n'empêche pas le run (chaque
     ticket a son worktree) mais mérite d'être signalé.
3. **Demande le feu vert, puis lance.** Un run crée des branches, committe, pousse et ouvre N Merge
   Requests : c'est une action visible de l'extérieur, elle se confirme — jamais au fil de l'eau.
   Une fois le go donné :
   ```
   bash scripts/orchestrate/run.sh --detach
   ```
   Il ouvre une console indépendante, imprime le run-id, le journal et la commande de reprise, et
   rend la main immédiatement. Rappelle les options utiles, qui se combinent avec `--detach` :
   `--max <n>` pour borner le run, `--budget <usd>` par ticket, `--timeout <durée>` par ticket,
   `--modele <alias>`. Puis le **suivi** — `bash scripts/orchestrate/status.sh --watch` (où en est
   le run, depuis n'importe quel terminal) ou `tail -f .maestro/orchestrate/<run-id>/run.log` (la
   sortie brute de la console) — et l'**arrêt d'urgence** : `touch .maestro/orchestrate/STOP` — pris
   en compte entre deux tickets **et pendant une attente** de reprise.

   **Dis ce que la console montre** (#176) : elle n'est pas muette entre le début d'un ticket et son
   verdict, elle égrène **une ligne compacte par action** de la session (`· Edit core/models/mcp.py`,
   `· Bash pytest -q`) — le flux brut restant dans `<run-id>/<iid>.jsonl` (gzippé en `.jsonl.gz`
   dès le verdict rendu, #198 — `zcat`/`zgrep` pour le relire), et `<iid>.json` ne portant que le
   résultat final (coût, verdict). C'est ce qui distingue « ça travaille » de « c'est planté »
   quand on a la fenêtre sous les yeux ; `status.sh` couvre le cas contraire.

   **Dis la réserve, sans la noyer** : la console ne dépend plus de ta session, mais rien ne
   garantit qu'elle survive à un parent qui enfermerait ses descendants (job object Windows). Le
   filet existe — le plan reste sur disque, `--plan <run-id>/plan.tsv` le rejoue et les tickets déjà
   livrés sont sautés d'eux-mêmes. Si l'utilisateur veut la certitude plutôt que le filet, donne-lui
   la commande **sans** `--detach` à lancer dans son propre terminal Git Bash laissé ouvert : c'est
   le seul montage qui ne dépende d'aucun processus tiers.
4. **Dis ce que le run produira** : N Merge Requests **en Draft** à relire, une par ticket. Le run
   ne merge, ne ferme et ne force-push **jamais** — le merge reste une décision humaine.

### `--dry-run` — juste voir le plan

Lance `bash scripts/orchestrate/run.sh --dry-run` et commente le plan : combien de tickets, quels
groupes de lots, ce qui a été écarté et pourquoi (`bash scripts/orchestrate/queue.sh --check` donne
le détail des écartés — parents de suivi, tickets assignés, statuts autres que « À faire »).
Rien n'est lancé, aucun répertoire de run n'est laissé derrière.

### `--status` — où en est le dernier run

**Une seule commande porte toute la lecture** (`status.sh`, #177) — ne recompose jamais à la main
ce qu'elle dit déjà (choix du run, plan restant, bilan, worktree, état GitLab) :

```
bash scripts/orchestrate/status.sh
```

Elle prend le run le plus récent et imprime en une passe : l'état du run et son heure de départ, le
**ticket en cours** avec son temps écoulé, les **commits et fichiers modifiés de son worktree**, sa
**dernière activité**, son **état GitLab** (statut du ticket, MR ouverte), le **reste du plan** et le
**bilan des traités** (verdict, MR, durée, coût). Options utiles : `--run-id <id>` pour un run
précis, `--list` pour les runs connus, `--watch [sec]` pour rafraîchir tant que le run tourne,
`--no-gitlab` hors ligne. **Aucun run n'est pas une erreur** : le script le dit et sort en 0.

Ensuite seulement, apporte ce que la sortie ne dit pas :

1. **Commente** ce qui a échoué et pourquoi — le journal `<run-id>/<iid>.log` porte le détail — et
   ce qui a été sauté (lot dépendant d'un échec, ou ticket pris entre-temps). Une raison en
   **« session terminée sans clôture, N fichier(s) non commité(s) »** (#178) est un échec
   **rattrapable** : la session a produit puis rendu la main sans clore, le travail est intact dans
   le worktree du ticket (`../maestro-worktrees/<iid>-<slug>`, que la console du run a imprimé) et
   se termine par une session ouverte là, jusqu'à `/ticket-ship` — surtout pas en repartant de
   zéro. Une raison en **« sans rien produire (worktree propre) »** est l'inverse : il n'y a rien à
   récupérer, le ticket se relance tel quel.
2. Si l'en-tête annonce **« en cours ? — rien d'écrit depuis … »**, dis franchement que c'est une
   **déduction** : `run.sh` n'écrit pas de PID, l'état se lit sur la date des dernières écritures du
   run et de son worktree. Une session qui réfléchit longuement et une session morte laissent la
   même trace ; ce qui tranche, c'est le **flux d'activité** — `tail -f
   .maestro/orchestrate/<run-id>/run.log` sur un run détaché, ou `<run-id>/<iid>.jsonl` pour le
   détail brut de la session en cours.
3. Si des tickets ont réussi, enchaîne sur la **file de revue** :
   `bash scripts/gitlab/lib.sh review-queue` — c'est là que le travail du run attend un humain.
4. **Ne rappelle aucun ménage de worktrees** : ils sont ramassés d'office dès que GitLab confirme
   leur MR mergée (`worktree.sh gc`, câblé dans `/ticket-start`, `/branch-cleanup` et au début de
   chaque run — docs/10 §9.2). La seule chose à relayer, c'est une **alerte** de `gc` : un worktree
   conservé parce qu'il porte du travail non sauvegardé.

### `--resume <run-id>` — reprendre un run interrompu

Un run coupé (terminal fermé, machine éteinte, limite hebdomadaire) laisse son `plan.tsv` intact.
On le rejoue **sur le même plan**, ce qui évite de recalculer un ordre entre-temps périmé — les
tickets déjà livrés seront sautés d'eux-mêmes, la boucle relisant leur statut avant de les prendre :
```
bash scripts/orchestrate/run.sh --plan .maestro/orchestrate/<run-id>/plan.tsv
```
Vérifie d'abord que le fichier existe, et dis combien de tickets du plan restent à traiter — c'est
exactement ce que `bash scripts/orchestrate/status.sh --run-id <run-id>` imprime (et
`--list` retrouve le run-id si l'utilisateur ne l'a pas sous la main).

## Diagnostic d'une reprise après limite d'usage

Si l'utilisateur doute de la détection de limite sur une session donnée, rejoue le jugement de la
boucle sur sa sortie capturée — sans rien relancer :

```
bash scripts/orchestrate/run.sh --test-reprise .maestro/orchestrate/<run-id>/<iid>.json
```

Il dit si la sortie serait vue comme une limite d'usage, l'heure de reset détectée et le temps que
la boucle attendrait — ou qu'il s'agit d'un échec ordinaire, sans reprise.

## Garde-fous à rappeler

- Le run **ne merge, ne ferme et ne force-push jamais** ; `guard.sh` le refuse en dur, quel que soit
  le mode de permission de la session.
- Un run **ne retire aucun worktree** : la branche y vit jusqu'au merge.
- Un ticket **pris par quelqu'un d'autre** entre le calcul du plan et son tour est sauté, pas volé.
- Au-delà de **5 h 30** d'attente cumulée sur un ticket, la boucle conclut à la limite
  **hebdomadaire** et s'arrête proprement : seules les fenêtres de moins de 5 h sont attendues.
