---
description: Où est passé le temps d'un run — rapport délégué à journal.sh, jugement sur les coûts attendus, tickets de correction proposés
argument-hint: "[<run-id> | --tous]  (défaut : le dernier run)"
allowed-tools: Bash(bash:*), Bash(gh:*)
---

Commande **de supervision, en lecture seule** : tu rends le rapport de temps d'un run
d'orchestration, tu le **juges**, et tu **proposes** les tickets de correction qu'il appelle. Tu
n'écris rien — ni cycle de vie, ni PR, ni merge, ni ticket sans le « go » de l'utilisateur.
Réf. complète `docs/10-workflow-git.md` §11.12, non chargée automatiquement : cette commande est
autosuffisante, n'ouvre le doc qu'en cas de doute.

**Ce que l'audit répond** : *où est passé le temps de ce run ?* — la part du mur passée sous outil,
le poids de chaque outil et de chaque forme de commande, le pré-vol payé à chaque ticket, le temps
mort, les commandes rejouées à l'identique.
**Ce qu'il ne répond pas** : *où en est le run ?* (`status.sh`, l'instant — §11.5), *que lui a-t-on
refusé ?* (`journal.sh refus`, les permissions — §11.7), *que garde-t-on du journal ?*
(`journal.sh gc`, la rétention — **il écrit, ne l'appelle jamais ici**). Et surtout : **il mesure,
il ne corrige pas** — c'est la portée que le chantier #495 s'est donnée, chaque remède étant son
propre ticket.

1. **Le rapport, par délégation** — sans argument, le **dernier run** qui porte un flux (c'est la
   question qu'on pose neuf fois sur dix ; un run **en cours** se lit comme un autre) :
   ```
   bash scripts/orchestrate/journal.sh audit $ARGUMENTS
   bash scripts/orchestrate/journal.sh refus $ARGUMENTS
   ```
   `$ARGUMENTS` vaut un `<run-id>`, `--tous` (tout le journal, pour la tendance), ou rien.
   Un run-id inconnu fait sortir le verbe en `2` **en nommant les runs présents** : relaie-le et
   arrête-toi, n'essaie pas de deviner lequel était visé.

   ⚠ **N'analyse pas le flux toi-même et ne recopie jamais leur recette.** Ces deux verbes portent
   l'appariement des appels, le désescapage, les seuils et le classement des refus ; une analyse
   réécrite dans un prompt fige l'outil au jour où elle a été écrite — c'est exactement ce que #310
   a retiré de `/mr-fix` et de `/ticket-finish`, et `tests/test_ci_local.py` garde qu'aucun bloc de
   code de `.claude/**` n'en réintroduise une. Tu n'ouvres ni `<iid>.jsonl`, ni `<iid>.json`.

2. **Rends les chiffres tels que le verbe les imprime.** Cite sans les reformater les trois
   tableaux qui portent le verdict — « Où passe le temps, ticket par ticket », « Par outil » et
   « Bash, par forme de commande ». Résume le reste (palmarès, pré-vol, temps mort, commandes
   rejouées, appels sans retour) en n'en gardant que ce que ton jugement va utiliser : recopier
   quatre-vingts lignes n'apprend rien de plus que le terminal où elles viennent de s'afficher.

3. **Le jugement — c'est ce que tu ajoutes aux deux verbes.** Sépare, poste par poste, le coût
   **attendu** de celui qui ne l'est pas. Sans cette séparation un rapport dirait « Bash est lent »
   et enverrait chercher l'économie du mauvais côté.

   Sont des coûts **attendus**, à nommer comme tels et non à corriger :
   - le **filet CI** (`bash scripts/ci/local.sh`) — plusieurs minutes par appel : c'est le prix du
     verdict avant push, et il est pris une fois par ticket au minimum ;
   - un **`Agent`** de recherche — quelques minutes : du travail délégué qui aurait coûté plus cher
     tenu en ligne ;
   - le **pré-vol** de `/ticket-start` (`worktree.sh ensure`, `lib.sh start-brief` /
     `start-branch` / `begin`) — payé une fois **par ticket**, donc N fois par run : le total
     compte, le détail dit lequel des quatre temps pèse ;
   - un **temps mort** de plusieurs heures : c'est une limite d'usage, pas du gras. L'audit mesure
     les trous, il ne les départage pas — quelques minutes sont de la réflexion, quelques heures un
     reset. Ne compte jamais l'un pour l'autre.

   Méritent en revanche un examen :
   - une **commande rejouée à l'identique** — soit une reprise après échec, soit du temps qui
     n'apprend rien ; le total de la section le chiffre d'un coup ;
   - une **forme** dont le cumul rivalise avec le filet CI **sans en être un** : beaucoup d'appels
     courts font un premier poste que personne ne voit appel par appel (mesure du 2026-08-24 :
     `lib.sh` à 22,9 min en 93 invocations de 14,8 s, contre 16,5 min de filet CI en 6 appels) ;
   - une **part sous outil** très basse ou très haute au regard des autres tickets du même run
     (31 % à 63 % sur le run de référence) : un run lent n'a pas toujours la même maladie, et
     c'est cette colonne qui dit laquelle ;
   - des **appels restés sans retour** : la session s'est arrêtée pendant. Croise-les avec les
     verdicts du run avant d'en conclure quoi que ce soit.

   Les **refus** se jugent avec leur famille, jamais sur leur nombre : un « blocage dur `.claude/` »
   ou un « refus voulu » n'appelle **aucun** geste, et lui écrire une règle serait du travail perdu.

4. **Propose les tickets de correction — ne les crée pas.** Un ticket par cause, avec son titre,
   le chiffre qui le motive et ce qu'il faudrait mesurer pour le déclarer réglé. Avant de proposer,
   vérifie qu'un ticket équivalent n'existe pas déjà — `lib.sh backlog-table all`, fermés compris :
   un doublon coûte plus cher que l'oubli (#151). Puis arrête-toi. Ne les crée que
   sur un « go » explicite de l'utilisateur, et alors **par `/ticket-create`**, jamais à la main —
   c'est lui qui pose le corps, les labels et le milestone.

5. **Termine en une ligne sur ce que tu n'as pas fait** : tu as mesuré, tu n'as rien corrigé, et
   aucun ticket n'a été ouvert sans qu'on te le demande.

N'exécute **aucune** commande d'écriture : ni `journal.sh gc`, ni `gh issue create`/`edit`, ni
`lib.sh set-workflow`, ni `git`. En cas de doute, abstiens-toi et demande.
