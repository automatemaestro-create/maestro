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
   `--modele <alias>`. Puis le **suivi** (`tail -f .maestro/orchestrate/<run-id>/run.log`) et
   l'**arrêt d'urgence** : `touch .maestro/orchestrate/STOP` — pris en compte entre deux tickets
   **et pendant une attente** de reprise.

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

1. Trouve le run le plus récent : `ls -1 .maestro/orchestrate/ | grep -v STOP | sort | tail -1`.
   Pour un run lancé avec `--detach`, `run.log` porte toute la sortie de la console — c'est là qu'on
   lit ce qui s'est passé quand la fenêtre a été fermée.
2. Lis son bilan : `cat .maestro/orchestrate/<run-id>/resume.tsv` (colonnes
   `iid / verdict / mr / duree_s / cout_usd / raison`) et compare-le à `plan.tsv` pour dire ce qui
   reste à traiter.
3. Résume : tickets réussis (avec le lien de leur MR), en échec (avec la raison et le chemin du
   journal `<iid>.log`), sautés (et pourquoi — lot dépendant d'un échec, ou ticket pris entre-temps).
4. Si des tickets ont réussi, enchaîne sur la **file de revue** :
   `bash scripts/gitlab/lib.sh review-queue` — c'est là que le travail du run attend un humain.
5. Rappelle les **worktrees à retirer** une fois leurs MR mergées :
   `bash scripts/git/worktree.sh remove <iid>` — jamais avant le merge, la branche y vit.

### `--resume <run-id>` — reprendre un run interrompu

Un run coupé (terminal fermé, machine éteinte, limite hebdomadaire) laisse son `plan.tsv` intact.
On le rejoue **sur le même plan**, ce qui évite de recalculer un ordre entre-temps périmé — les
tickets déjà livrés seront sautés d'eux-mêmes, la boucle relisant leur statut avant de les prendre :
```
bash scripts/orchestrate/run.sh --plan .maestro/orchestrate/<run-id>/plan.tsv
```
Vérifie d'abord que le fichier existe, et dis combien de tickets du plan restent à traiter d'après
`resume.tsv`.

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
