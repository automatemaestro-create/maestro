---
description: Traite le backlog en autonomie — un ticket, un worktree, une session Claude Code, de /ticket-start à /ticket-ship
argument-hint: "[--dry-run | --status | --resume [<run-id>] | --milestone <titre> | --max <n>] (aucun argument = lance un run)"
allowed-tools: Bash(bash:*), Bash(git:*), Bash(glab:*), Bash(cat:*), Bash(ls:*), AskUserQuestion
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

0. **Sur quoi va porter le run ?** *Avant tout le reste*, trois lectures — hors ligne pour la
   première, en lecture seule pour les trois — qui préparent les **seules** questions que cette
   commande pose (au point 3) :
   ```
   bash scripts/orchestrate/status.sh --reprenables    # un run inachevé traîne-t-il ?
   bash scripts/orchestrate/queue.sh  --milestones     # quels milestones ont du travail ?
   bash scripts/orchestrate/queue.sh  --orphelins      # des tickets qu'une session morte a laissés ?
   ```
   - **`--reprenables`** — sortie vide (le cas courant) : rien à reprendre, n'en parle pas. Une ou
     plusieurs lignes : un run précédent n'a pas fini son plan. TSV — `run-id`, `état`
     (`interrompu` / `termine` / `en-cours`), `tickets restants`, `début` (epoch), `silence`
     (secondes depuis la dernière écriture), `ticket en vol` (vide s'il n'y en a pas) ; le
     **dernier** de la liste est le plus récent, c'est le candidat.
   - **`--milestones`** — TSV (en-tête `#` à ignorer) : `titre`, `courant` (0/1), `à faire et
     libres`, `ouverts`, `échéance`. Les candidats sont les lignes dont `à faire` **> 0** ; celle
     à `courant = 1` est le défaut historique. Le compte est **indicatif** sur un point : un parent
     de suivi y compte pour un, alors que le run traitera ses lots.
   - **`--orphelins`** — sortie vide (le cas courant) : n'en parle pas. Une ou plusieurs lignes :
     ce sont des tickets **« En cours » dont plus personne ne s'occupe** (#329) — une session morte
     (délai, pilote tué, console fermée, session interactive laissée en plan) les y a laissés, et
     « En cours » **et** assigné est exactement ce que `queue.sh` écarte : ils n'entreront dans
     **aucun** plan tant que personne ne les reprend, alors que leur worktree porte parfois des
     milliers de lignes commitées et jamais poussées. TSV — `iid`, `reprises` (combien de fois ce
     ticket a déjà été rendu prenable), `plafond` (`atteint` = **ne le propose pas**), `run`
     d'origine et son `verdict` (`-` s'il n'y a jamais eu de run : session interactive), `détail`
     (depuis quand son worktree est muet, et où il est), `titre`.

1. **Montre le plan** de ce qui partirait par défaut : `bash scripts/orchestrate/run.sh --dry-run`
   (lecture seule, aucun quota). Il imprime l'ordre de traitement figé, ce qui serait fait pour
   chaque ticket, et les garde-fous. **S'il y a un candidat à la reprise, montre SON plan** —
   `--resume <id> --dry-run` — et pas celui d'un run neuf : c'est sur celui-là que portera la
   décision.
2. **Contrôle l'état de départ**, et dis ce qui cloche plutôt que de lancer quand même :
   - `bash scripts/gitlab/lib.sh require` — sinon `glab auth login` ;
   - `bash scripts/orchestrate/guard.sh --check` — le garde-fou ne doit pas avoir dérivé des
     règles `deny` du dépôt ; s'il sort en 1, **arrête-toi**, c'est la seule couche qui protège une
     boucle sans surveillance ;
   - `bash scripts/gitlab/ensure-runner.sh` — sans runner, chaque MR produite restera `pending` et
     rien ne pourra être mergé au matin ;
   - `git status --porcelain` sur le clone principal : un arbre sale n'empêche pas le run (chaque
     ticket a son worktree) mais mérite d'être signalé.
   - **rien à faire pour `main`** : le run la remet lui-même à niveau sur `origin/main` avant son
     premier ticket (#283 — fetch + fast-forward via `lib.sh sync-main`, docs/10 §9.3). Ne propose
     donc ici ni `git pull`, ni rebase, ni quoi que ce soit à la main. S'il **s'abstient** (`main`
     divergent, répertoire porteur sale), il le dit dans la console : c'est alors une décision
     humaine à prendre à froid, jamais un blocage du run.
3. **Demande le feu vert, puis lance.** Un run crée des branches, committe, pousse et ouvre N Merge
   Requests : c'est une action visible de l'extérieur, elle se confirme — jamais au fil de l'eau.

   **Un seul moment de question**, en un seul appel à `AskUserQuestion` : le feu vert **est** le
   choix, n'y ajoute pas de confirmation par-dessus. Selon ce que le point 0 a trouvé, cet appel
   porte une à trois questions :

   **(a) Reprendre ou repartir de zéro ?** — seulement s'il y a un candidat à la reprise :
   - **Reprendre le run `<id>`** (à recommander en premier) — son plan est rejoué tel quel, les
     tickets livrés depuis se sautent d'eux-mêmes, et le ticket **en vol** à la coupure est repris
     avec sa session. Mets dans la description ce que la ligne TSV t'a appris : combien de tickets
     restent, depuis quand le run est silencieux, et le ticket en vol s'il y en a un.
   - **Démarrer un nouveau run** — l'ordre est recalculé sur le backlog d'aujourd'hui. C'est le bon
     choix si le plan a vieilli (priorités changées, tickets ajoutés depuis).
   - **Ne rien lancer** — s'en tenir au plan affiché.

   **(b) Quel milestone ?** — seulement pour un run **neuf**, et seulement si le choix est **réel** :
   au moins **deux** milestones à `à faire > 0` au point 0. Un seul candidat ne se demande pas, il
   s'**annonce** (« le run portera sur *Phase N*, seule phase active avec des tickets à faire ») ;
   aucun candidat, dis-le et ne lance rien. Quand la question se pose : le milestone `courant = 1`
   en premier et recommandé, les autres ensuite, chacun avec **son nombre de tickets à faire** en
   description. Si la question (a) est posée en même temps, précise dans l'intitulé que ce choix ne
   vaut **que** pour un run neuf — une reprise rejoue le plan de son run, milestone compris.

   **(c) Reprendre des tickets orphelins ?** — seulement si le point 0 en a listé dont le `plafond`
   n'est **pas** `atteint`, et seulement pour un run **neuf** : le plan d'une reprise est figé, un
   ticket rendu prenable maintenant n'y entrerait pas (dis-le si le cas se présente, et propose
   alors un run neuf). Question à **choix multiple**, une option par orphelin (au-delà de trois,
   prends les **plus anciennement muets** et annonce le reste d'une ligne) — et **ne coche rien par
   défaut** : le filtre d'anti-collision de `queue.sh` est ce qui protège le travail des autres, il
   reste le défaut et ne se contourne que sur un « oui » explicite. Mets dans la description ce que
   la ligne TSV t'a appris : depuis quand le worktree est muet, le run et le verdict qui l'ont
   laissé là (ou « aucun run » — session interactive), et les reprises déjà faites. Dis aussi ce que
   la reprise **ne fait pas** : elle n'écrit que dans GitLab (cycle de vie « À faire », assignation
   retirée) — worktree, branche, commits non poussés et travail non commité restent **intacts**, et
   la session qui prendra le ticket les y retrouvera.
   Un orphelin à `plafond = atteint` ne se propose **pas** : nomme-le en une ligne (« déjà repris N
   fois, il retombe à chaque run — `bash scripts/gitlab/lib.sh reprises <iid>` pour sa trace, et
   `reprendre-en-cours --force <iid>` pour insister ») et passe. C'est ce qui empêche un ticket
   cassé de brûler une session à chaque run.

   **Dis ce qui va être arrêté.** Lancer ou reprendre commence par **tuer les runs encore en vol**
   (#213, docs/10 §11.9) : `bash scripts/orchestrate/status.sh --list` marque d'un `● en cours`
   ceux qui tournent. S'il y en a, nomme-les dans la question — c'est une conséquence du feu vert,
   pas une surprise à découvrir dans la console — en disant ce que ça coûte (la session en cours
   est interrompue, son travail non commité reste dans le worktree de son ticket) et ce que ça ne
   coûte pas (le journal est intact, le run reste reprenable). `--sans-kill` existe pour les
   laisser cohabiter, mais ne le propose pas de toi-même : deux runs brûlent le même quota.

   Deux nuances à porter, pas à taire : un candidat d'état `en-cours` **sans carte de pilote**
   (journal d'avant #213) est un run que rien ne prouve mort — l'état s'y déduit du silence —,
   dis-le, et si le silence est court, propose d'abord `bash scripts/orchestrate/status.sh
   --run-id <id>`. Et reprendre ne **fusionne** rien : le journal du run repris reste intact, le
   nouveau porte un fichier `reprise-de` qui dit de qui il est la suite.

   Une fois le go donné, **les orphelins retenus se reprennent AVANT le lancement** — dans cet
   ordre, sans quoi le run figerait son plan sur un backlog où le ticket est encore « En cours » et
   assigné, donc écarté :
   ```
   bash scripts/gitlab/lib.sh reprendre-en-cours <iid> [<iid>…]
   ```
   Un seul appel pour tous. Il refuse tout seul ce qui ne doit pas être repris (ticket redevenu
   vivant entre-temps, plafond atteint) et sort en **3** : ce n'est pas une panne, relaie ce qu'il
   dit et lance le run quand même. Chaque reprise laisse sa trace — un commentaire sur le ticket et
   une ligne dans `.maestro/orchestrate/reprises.tsv` — et rappelle où dort le travail conservé.

   Vient ensuite le lancement — et si le milestone retenu n'est pas celui dont le plan a été montré
   au point 1, **montre d'abord le sien** (`--dry-run --milestone "<titre>"`, gratuit) :
   ```
   bash scripts/orchestrate/run.sh --detach                                   # run neuf
   bash scripts/orchestrate/run.sh --detach --milestone "<titre>"             # ... sur ce milestone
   bash scripts/orchestrate/run.sh --resume <id> --detach                     # reprise du run <id>
   ```
   **Passe `--milestone` explicitement dès que la question (b) a été posée**, même pour le
   milestone courant : le run est ainsi épinglé sur ce que l'utilisateur a choisi, et non sur une
   phase courante qui peut basculer d'ici son démarrage. Une reprise, elle, ne prend **jamais**
   `--milestone` — son plan est déjà figé.
   Il ouvre une console indépendante, imprime le run-id, le journal et la commande de reprise, et
   rend la main immédiatement. Rappelle les options utiles, qui se combinent avec `--detach` :
   `--max <n>` pour borner le run, `--modele <modèle>`, `--effort <niveau>` (`low`…`max`). Ces deux
   derniers ont un **défaut épinglé par le dépôt** — `claude-opus-5` et `xhigh` (#206, #217) — et la
   ligne `plan :` les annonce : ne les passe que si l'utilisateur demande explicitement un autre
   régime, et dis lequel s'il le fait. `--budget <usd>` (#286) et `--timeout <durée>` (#326)
   existent aussi, mais **ne les propose pas** : aucun des deux ne s'applique par défaut, et
   atteints ils coupent la session en plein travail — sans commit ni MR, comptée en échec, lots
   suivants du parent sabordés. Ne les passe que si l'utilisateur le demande, et dis-le alors.
   Puis le **suivi** — `bash scripts/orchestrate/status.sh --watch` (où en est le run, depuis
   n'importe quel terminal) ou `tail -f .maestro/orchestrate/<run-id>/run.log` (la
   sortie brute de la console) — et l'**arrêt d'urgence** : `touch .maestro/orchestrate/STOP` — pris
   en compte entre deux tickets **et pendant une attente** de reprise.

   **Dis ce que la console montre** (#176) : elle n'est pas muette entre le début d'un ticket et son
   verdict, elle égrène **une ligne compacte par action** de la session (`· Edit core/models/mcp.py`,
   `· Bash pytest -q`) — le flux brut restant dans `<run-id>/<iid>.jsonl` (gzippé en `.jsonl.gz`
   dès le verdict rendu, #198 — `zcat`/`zgrep` pour le relire), et `<iid>.json` ne portant que le
   résultat final (coût, verdict), doublé d'un `<iid>.resultat.txt` **lisible** (#180). C'est ce qui
   distingue « ça travaille » de « c'est planté » quand on a la fenêtre sous les yeux ;
   `status.sh` couvre le cas contraire.

   **Dis la réserve, sans la noyer** : la console ne dépend plus de ta session, mais rien ne
   garantit qu'elle survive à un parent qui enfermerait ses descendants (job object Windows). Le
   filet existe — le plan reste sur disque et `/orchestrate --resume` le rejoue, en reprenant même
   le ticket qui était en vol. Si l'utilisateur veut la certitude plutôt que le filet, donne-lui
   la commande **sans** `--detach` à lancer dans son propre terminal Git Bash laissé ouvert : c'est
   le seul montage qui ne dépende d'aucun processus tiers.
4. **Dis ce que le run produira** : N Merge Requests **en Draft** à relire, une par ticket. Le run
   ne merge, ne ferme et ne force-push **jamais** — le merge reste une décision humaine.

### `--dry-run` — juste voir le plan

Lance `bash scripts/orchestrate/run.sh --dry-run` et commente le plan : combien de tickets, quels
groupes de lots, ce qui a été écarté et pourquoi (`bash scripts/orchestrate/queue.sh --check` donne
le détail des écartés — parents de suivi, tickets assignés, statuts autres que « À faire »).
Rien n'est lancé, aucun répertoire de run n'est laissé derrière.

Le plan porte par défaut sur la **phase courante**. Pour en voir un autre, ajoute
`--milestone "<titre>"` — et `bash scripts/orchestrate/queue.sh --milestones` dit lesquels ont du
travail (titre, courant, à faire et libres, ouverts, échéance).

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

1. **Commente** ce qui a échoué et pourquoi — `<run-id>/<iid>.resultat.txt` porte le détail **en
   clair** (#180 : état de session, durée, coût, refus de permission, message final ; `<iid>.log`
   ne garde que stderr, et `<iid>.json` est minifié) — et
   ce qui a été sauté (lot dépendant d'un échec, ou ticket pris entre-temps). Une raison en
   **« session terminée sans clôture, N fichier(s) non commité(s) »** (#178) est un échec
   **rattrapable** : la session a produit puis rendu la main sans clore, le travail est intact dans
   le worktree du ticket (`../maestro-worktrees/<iid>-<slug>`, que la console du run a imprimé) et
   se termine par une session ouverte là, jusqu'à `/ticket-ship` — surtout pas en repartant de
   zéro. Une raison en **« sans rien produire (worktree propre) »** est l'inverse : il n'y a rien à
   récupérer, le ticket se relance tel quel.
2. **« pilote vivant (pid …) »** est une certitude, pas une déduction (#213) : le run tourne, même
   s'il est silencieux depuis vingt minutes. En revanche, si l'en-tête annonce **« en cours ? —
   rien d'écrit depuis … »**, c'est qu'aucune carte de pilote n'est exploitable (journal d'avant
   #213) : dis franchement que l'état se lit alors sur la date des dernières écritures du run et de
   son worktree. Une session qui réfléchit longuement et une session morte y laissent la même
   trace ; ce qui tranche, c'est le **flux d'activité** — `tail -f
   .maestro/orchestrate/<run-id>/run.log` sur un run détaché, ou `<run-id>/<iid>.jsonl` pour le
   détail brut de la session en cours.
3. Si des tickets ont réussi, enchaîne sur la **file de revue** :
   `bash scripts/gitlab/lib.sh review-queue` — c'est là que le travail du run attend un humain.
4. **Ne rappelle aucun ménage de worktrees** : ils sont ramassés d'office dès que GitLab confirme
   leur MR mergée (`worktree.sh gc`, câblé dans `/ticket-start`, `/branch-cleanup` et au début de
   chaque run — docs/10 §9.2). La seule chose à relayer, c'est une **alerte** de `gc` : un worktree
   conservé parce qu'il porte du travail non sauvegardé.

### `--resume [<run-id>]` — reprendre un run interrompu

Un run coupé (console fermée, machine éteinte, limite hebdomadaire, `--max` atteint) laisse son
`plan.tsv` intact. On le rejoue **sur le même plan**, ce qui évite de recalculer un ordre
entre-temps périmé :

```
bash scripts/orchestrate/run.sh --resume <run-id> --detach
bash scripts/orchestrate/run.sh --resume --detach          # le run reprenable le plus récent
```

**L'utilisateur n'a aucun run-id à retenir** : sans argument, le script prend le plus récent des
runs reprenables (`status.sh --reprenables`, la même source qu'au point 0). S'il en donne un,
passe-le tel quel — un chemin de journal collé au lieu de l'id est accepté aussi.

Ce que la reprise fait, et qu'il faut savoir dire :
- **Les tickets déjà livrés se sautent d'eux-mêmes** : la boucle relit leur statut GitLab avant de
  les prendre, et n'en prend aucun qui ne soit plus « À faire ».
- **Le ticket qui était en vol est repris**, pas sauté — c'est la seule exception à la règle
  ci-dessus, et elle est étroite : il faut que le run repris ait laissé sa session sans verdict.
  Sa session Claude est **rouverte** avec son uuid (le contexte déjà payé est conservé) ; si elle
  n'est plus reprenable, la boucle repart à froid, le travail commité étant sur la branche.
- Un ticket « En cours » que le run repris **n'avait pas** en main appartient à quelqu'un d'autre :
  il reste sauté.
- Le journal est **neuf**. Celui du run repris n'est jamais réécrit ; le nouveau porte un fichier
  `reprise-de` avec l'id de son prédécesseur, et `status.sh` l'affiche en en-tête.

Avant de lancer, dis combien de tickets restent — `bash scripts/orchestrate/status.sh --run-id
<run-id>` l'imprime, et `--list` retrouve l'id (les runs reprenables y sont marqués `↻`, ceux qui
tournent encore d'un `● en cours`).

**Une reprise tue, elle aussi, ce qui tourne encore** (#213) : c'est le même geste qu'au démarrage
d'un run neuf, et il vaut y compris quand la cible de la reprise **est** le run en vol — le tuer
puis rejouer son plan est précisément ce qu'on veut. Annonce-le avant de lancer.

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
- Un ticket « En cours » **abandonné par sa session** n'est jamais repris d'office : le run ne prend
  que des tickets « À faire » et libres, et ce filtre ne bouge pas. Le run les **signale** au
  démarrage (`worktree.sh gc`, #328) et cette commande les **propose** (question (c)) ; les rendre
  prenables est un geste, `lib.sh reprendre-en-cours`, borné à **2 reprises** par ticket (#329).
- Au-delà de **5 h 30** d'attente cumulée sur un ticket, la boucle conclut à la limite
  **hebdomadaire** et s'arrête proprement : seules les fenêtres de moins de 5 h sont attendues.
