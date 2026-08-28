# 34 — Brancher un agent CLI tiers comme exécuteur de tâche (ACP) : note de décision

> Ticket #356. Décision datée du **2026-08-28**. Faits mesurés sur `origin/main` à `bc837c2`.
> Milestone « Collaboration inter-agents », comme les trois cadrages voisins (#354, #647, #651).
>
> **Quatre arbitrages, et le premier renverse la question.** ① Le cas réel n'est pas à imaginer :
> **nous branchons déjà un agent CLI tiers comme exécuteur de tâche** — `scripts/orchestrate/`
> pilote des sessions Claude Code depuis #167, *sans protocole*, pour **8 292 lignes** de pilote,
> **14 161** de tests et **1 996** de documentation. Le chiffrage se lit là. ② Ce qu'on perd n'est
> pas « nos garde-fous » en bloc, et la ligne de fracture est nette : **ce qui s'observe sur le
> disque ou dans le process hôte survit** (espace de travail, livrable, journal, durée) ; **ce qui
> s'observe dans le flux de messages ou les hooks du SDK ne survit pas** (coût, refus au vol,
> arbitrage, activité). Deux surprises dans le détail : le **plafond de tours n'a rien à perdre**
> (il vaut `None` partout), et le **bac à sable est portable en principe mais pas en l'état** — le
> binaire y est en dur. ③ **Ni `ModelProvider` ni `TaskExecutor`** : le premier ferait *mentir* une
> interface, le second est une abstraction de **lieu** et non d'**autorité**. ④ **Brancher sous
> condition** — et aucune condition n'est remplie au 2026-08-28. Aucun lot de code n'est posé, et
> c'est le résultat, pas une dérobade.

---

## 1. La question, et pourquoi elle n'est pas hypothétique

Le ticket demande si Maestro doit pouvoir dire « cette tâche-là, c'est Claude Code qui la fait », en
héritant de l'outillage d'un agent qu'on n'a pas écrit, par le protocole qu'AionUi utilise pour en
piloter une vingtaine — ACP (#352).

La question paraît prospective. Elle ne l'est pas, et c'est le premier fait de cette note : **le
dépôt exécute déjà des agents CLI tiers comme exécuteurs de tâche.** Il le fait dans
`scripts/orchestrate/`, qui prend un ticket, monte un worktree, ouvre une session Claude Code et lit
son verdict. Le ticket le dit lui-même — « la frontière entre les deux mondes est un accident
d'histoire plus qu'une décision » — mais il en tire une conclusion trop faible. Ce n'est pas
seulement une frontière mal placée : c'est **une intégration déjà payée, dont on peut lire la
facture**.

C'est ce qui rend le critère 2 du ticket — chiffrer sur un cas réel — jouable sans écrire une ligne
de code. Le cas réel existe, il a des journaux, et il a tourné hier.

## 2. Ce qu'ACP exige d'un client

> ⚠ **Portée de cette section, et ce qui n'a pas pu être vérifié.** Ce cadrage a été instruit en
> session autonome, où `WebSearch`/`WebFetch` ne sont dans aucune des deux allowlists
> ([docs/10 §11.7](./10-workflow-git.md)). La spécification
> (`agentclientprotocol.com/protocol/v1/schema`) **n'a donc pas été relue en direct**. Les sources
> effectivement lues sont la note de veille #352
> ([`docs/presentations/veille-aionui-2026-08-17.html`](./presentations/veille-aionui-2026-08-17.html)
> — seule mention d'ACP de tout le dépôt, 7 occurrences) et notre propre code. Ce qui suit est une
> **description à recouper**, pas une lecture de la norme, et le §9 en fait une condition explicite
> de tout code. La règle de #471 s'applique : ce qui n'est pas vérifié ne se cite pas comme vérifié.

ACP place le client (nous) et l'agent (le CLI tiers) de part et d'autre d'un lien **JSON-RPC sur
stdio** : le client lance le processus agent et lui parle sur son entrée/sortie standard. Quatre
exigences comptent pour la suite.

**2.1 Le cycle de session.** La note #352 relève quatre méthodes — `session/new`, `session/load`,
`session/resume`, `session/prompt` (l. 594-595). La session est l'unité : elle s'ouvre sur un
répertoire de travail, reçoit des prompts, et vit tant que le processus vit.

⚠ **Un écart à recouper en premier** : `session/load` et `session/resume` font doublon dans cette
liste. Ma lecture est que la reprise passe par `session/load`, `session/resume` étant une variante
de backend plutôt qu'une méthode du protocole — mais c'est exactement le détail qu'on ne tranche
pas de mémoire, et il décide de la forme de la reprise.

**2.2 La reprise.** C'est le point faible du protocole, et la note le documente : la reprise est « en
trois variantes selon le backend » (l. 1137), chaque agent la servant à sa façon. Un client qui veut
reprendre doit donc savoir **de quel agent** il parle — précisément ce qu'un protocole est censé lui
épargner.

**2.3 La déclaration des serveurs MCP.** Les serveurs sont déclarés **à la création de la session**,
et la conséquence est écrite noir sur blanc dans les PRD d'AionUi :

> la liste des outils MCP est figée à la création de session, elle n'est pas rechargeable à chaud
> […] ils *tuent et recréent le processus agent* — en gardant la conversation identique et en
> s'appuyant sur le `resume` de chaque backend. […] **C'est une contrainte du protocole, pas un
> contournement, et elle vaut pour n'importe qui voudrait injecter un MCP après coup.**
> — #352, l. 879-886

Un serveur MCP se déclare comme **quelque chose de lançable ou de joignable** : une commande, ou une
adresse. Retenir ceci pour le §4.3 — c'est là que tout se joue.

**2.4 La remontée des appels d'outil.** L'agent notifie le client au fil de l'eau (pensée, appel
d'outil, résultat) et lui demande la permission pour ce qu'il juge sensible. La remontée est donc
**déclarative** : le client voit ce que l'agent veut bien lui dire, quand il veut bien le lui dire.
C'est une différence de nature avec une boucle qu'on tient soi-même, et c'est la racine du §4.4.

**2.5 Ce qu'ACP ne couvre pas.** La note est explicite : « chaque agent range ses skills ailleurs, et
**ACP ne dit rien du sujet** » (l. 914). AionUi y répond par une colonne en base,
`native_skills_dirs`, remplie **agent par agent**. Un protocole qui laisse dehors la livraison des
compétences laisse dehors une part du travail d'intégration.

## 3. Le cas réel, chiffré : `scripts/orchestrate/`

`scripts/orchestrate/` est une intégration d'agent CLI tiers comme exécuteur de tâche. Elle n'utilise
pas ACP — elle le remplace, à la main, pour **un seul** agent.

### 3.1 Ce qu'elle pèse

| Poste | Lignes | Attribution |
| --- | ---: | --- |
| `scripts/orchestrate/` (7 fichiers) | **8 292** | l'intégration elle-même |
| `scripts/git/worktree.sh` | 1 970 | induit — un worktree par session ([docs/10 §9](./10-workflow-git.md)) |
| `tests/test_orchestrate.py` | 7 839 | ce qui la garde |
| `tests/test_worktree.py` | 2 796 | ce qui la garde |
| `tests/test_reste_claude.py` | 658 | ce qui garde une de ses pannes (§3.3) |
| [docs/10 §11](./10-workflow-git.md) | 1 996 | ce qui explique les contrats non spécifiés |
| **Total attribuable** | **≈ 23 551** | |

Détail de l'intégration seule : `run.sh` 4 442 · `journal.sh` 1 567 · `status.sh` 809 · `queue.sh`
648 · `pilote.sh` 338 · `guard.sh` 248 · `settings.run.json` 240.

⚠ `scripts/gitlab/lib.sh` (7 837 lignes) **n'est pas attribué** : c'est de l'outillage de forge, il
existerait sans aucun agent tiers. Compter large ici rendrait le chiffre spectaculaire et faux.

Un indice de densité vaut le détour : dans `settings.run.json`, **172 lignes de commentaire pour 47
règles** (39 `allow`, 8 `deny`). Ce rapport de 4 pour 1 *est* la trace de l'absence de protocole —
on documente ce qu'aucun contrat ne porte.

**Ce que ce nombre dit exactement.** 8 292 lignes, ce n'est pas le prix d'ACP : c'est le prix de son
**absence**, pour **un** agent. Un protocole rendrait une partie de ces lignes inutiles (cycle de
session, reprise, remontée d'outils). Il n'en rendrait aucune inutile côté garde-fous, worktree ou
file de merge, qui sont notre métier et pas celui de l'agent.

### 3.2 Ce qu'elle a dû réimplémenter faute de protocole — et pourquoi c'est fragile

Chacun de ces mécanismes existe parce qu'aucun contrat ne le portait. Deux d'entre eux portent, en
commentaire, **l'argument le plus fort en faveur d'un protocole** :

> Trois filets, parce que **la forme exacte du signal en mode `-p` n'est pas contractuelle et a déjà
> changé d'une version à l'autre**. — [`run.sh:1282-1288`](../scripts/orchestrate/run.sh)

> L'ordre des clés du CLI **a changé** (`{"type":"tool_result","tool_use_id":…}` contre
> `{"tool_use_id":…,"type":"tool_result"}`), donc l'ancrage se fait sur la clé qui porte l'id.
> — [`journal.sh:1013-1020`](../scripts/orchestrate/journal.sh)

**Nous dépendons d'une surface qui n'est pas un contrat, et qui a déjà bougé deux fois.** C'est
exactement le manque qu'un protocole comble, et il faut le porter au crédit d'ACP (§5).

Le reste de ce qui a été réimplémenté : la **reprise** (uuid fabriqué et persisté par le pilote,
`run.sh:1153-1170` ; rendez-vous partagé `.limite` entre sessions, `run.sh:1378-1540` ; plafond
d'attente 5 h 30, `run.sh:1291`) ; la **détection de limite d'usage** (trois filets, plus un filtre
anti-faux-positif sans lequel une session **sortie en succès** partait dormir jusqu'au reset,
`run.sh:1313-1324`) ; l'**extraction du coût et du verdict** par un mini-parseur JSON **en awk**
(`run.sh:1764-1975`, « sans dépendance à `jq`, que personne n'a garanti sur la machine d'un run »),
doublé d'un second pour les refus (`journal.sh:355-460`) ; les **permissions** en deux couches
redondantes à dessein, la seconde parce que « la couche `permissions` tombe avec
`--dangerously-skip-permissions`, le hook non » (`guard.sh:4-8`).

Et le **verdict d'un ticket n'est pas lu dans la sortie de l'agent** : il est pris dans la forge (PR
ouverte *et* cycle de vie « En revue »). Ce que l'agent dit de son propre travail n'a pas été jugé
fiable.

### 3.3 Ce que ça coûte, et ce que ça a coûté

**Le régime nominal**, run `20260827-224641` (10 tickets, 10 livrés, sortie propre) :

| | |
| --- | ---: |
| Coût du run | **290,93 $** |
| Coût moyen par ticket | **29,09 $** |
| Durée de session moyenne | **35 min** |
| Temps de session cumulé | 5 h 53 |

Deux runs plus récents concordent : `20260828-135502` (3 livrés, **75,43 $**) et `20260828-162904`
(5 livrés, **69,37 $**). ⚠ Le premier des deux est celui qui a produit **les trois cadrages voisins**
de cette note — #647 à 15,90 $, #354 à 20,44 $, #355 à 39,09 $. Cette note-ci est écrite par le
même dispositif : c'est le cas réel, et nous sommes dedans.

**Les pannes**, toutes de la même famille — *l'agent tiers ne rend pas ce qu'on croyait, et rien ne
le dit* :

| Panne | Mesure | Source |
| --- | --- | --- |
| Plafond de dépense | #277 et #245 **coupés au même montant exact (15,07 $)**, 16 et 24 fichiers non commités, **13 lots sautés** en cascade, **zéro livrable** | #286, [docs/10 §11.3](./10-workflow-git.md) |
| Plafond de temps | **#316 coupé à 45 min 02** alors que son travail était fini et commité (2 047 lignes) — couperet tombé pendant le `git push` —, puis **7 lots sautés**, pour un run à 14,75 $ et un seul livrable | #326, [docs/10 §11.3](./10-workflow-git.md) |
| Remontée d'outils | **715 appels sur 2 979 (24 %)** rendus amputés, dont 278 sous la seule chaîne « `cd \` » ; des appels de 3,9 s lus comme un seul appel de plusieurs minutes | #496 |
| Permissions | **88 refus sur 36 sessions** ; 51 % trous d'allowlist, 40 % échappées de chemin ; trois maillons en portent 64 % (`for` 19, `curl` 5, `python -` 5) | [docs/10 §11.7](./10-workflow-git.md) |
| Interdit propre à l'agent | run `20260827-094044` : 3 tickets, **2 résidus** sous `.claude/` rendus dans des PR mergées en vingt minutes, encore en place le lendemain | #608 |

La dernière est la plus instructive pour ACP : l'agent tiers **ne pouvait pas** écrire là où on le
lui demandait — garde-fou de son propre CLI, en amont de notre allowlist —, il l'a dit dans sa PR, et
personne ne l'a lu. Un agent tiers a **ses** interdits, qui ne sont pas les nôtres, qu'aucun
protocole ne déclare, et qu'on découvre à l'usage. Sa détection amont plafonne à **63 % de précision
et 68 % de rappel**, avec un trou irréductible de **32 %** ([docs/10 §11.2](./10-workflow-git.md)).

Et la leçon transversale, mesurée **deux fois** : **un plafond qu'on impose de l'extérieur à un agent
dont on ne tient pas la boucle ne borne pas le travail — il le détruit en cours de route.** Les deux
plafonds ont été retirés (#286, #326).

## 4. Ce que Maestro perd — garantie par garantie

Le ticket suppose que « nos garde-fous ne s'appliquent plus ». C'est trop grossier : elles ne tombent
pas ensemble, et la ligne de partage est **nette et lisible dans le code**.

> **Ce qui s'observe sur le disque ou dans le process hôte survit. Ce qui s'observe dans le flux de
> messages ou les hooks de l'Agent SDK ne survit pas.**

| Garantie | Où elle vit | Survie |
| --- | --- | --- |
| Capture du livrable (`ProducedFile`) | [`sandbox/workspace.py:35`](../maestro/sandbox/workspace.py), `:79` | **Oui** |
| Espace de travail dérivé, jamais la racine | [`sandbox/projet.py:93`](../maestro/sandbox/projet.py), `:177` | **Oui** |
| Nettoyage même sur exception | `sandbox/workspace.py:147` | **Oui** |
| Rédaction des secrets du projet | [`agents/runtime.py:307`](../maestro/agents/runtime.py) | **Oui** |
| Journal, échec consigné jamais levé | [`engine/executor.py:256`](../maestro/engine/executor.py), `:452` | **Oui** |
| Durée horloge de la tâche | `engine/executor.py:446` | **Oui** |
| Cause d'un CLI mort (stderr borné) | [`providers/base.py:118`](../maestro/providers/base.py), `:184` | **Oui**, à recâbler |
| Bac à sable durci | [`sandbox/container.py:232`](../maestro/sandbox/container.py) | **Partielle** — §4.2 |
| Time-out ferme de la tâche | `engine/executor.py:556`, `:643` | **Partielle** — §4.5 |
| Relance des échecs transitoires | `engine/executor.py:896` | **Partielle** |
| Filtrage des outils au montage | [`agents/permissions.py:334`](../maestro/agents/permissions.py) | **Partielle** |
| **Plafond de dépense et de tokens** | [`telemetry/costs.py:215`](../maestro/telemetry/costs.py) + `usage.py:245` | **Non** — §4.4 |
| **Refus d'outil au vol, arbitrage `ask`** | [`providers/claude.py:570`](../maestro/providers/claude.py), monté `:435` | **Non** — §4.4 |
| **Canal d'arbitrage (surface d'écriture)** | `providers/claude.py:554`, monté `:419` | **Non** — §4.3 |
| **Verrou de configuration MCP ambiante** | `providers/claude.py:432` | **Non** — §4.6 |
| Activité temps réel, checklist | `providers/claude.py:916`, `:951-965` | **Non** |
| Plafond de tours | `agents/runtime.py:90` → `claude.py:430` | **Sans objet** — §4.1 |

### 4.1 La première surprise : il n'y a pas de plafond de tours à perdre

Le ticket cite le plafond de tours par rôle en tête des garanties menacées. **Il est vide.**

`PLAFOND_TOURS_DEFAUT = None` ([`providers/base.py:44`](../maestro/providers/base.py)), et **aucun
profil du dépôt n'en déclare** (`agents/runtime.py:75-78`). C'est un choix documenté (#494) : « une
borne posée "au cas où" tue en plein travail un run qui allait aboutir » — la même leçon que #286 et
#326 au §3.3, tirée trois fois.

La garantie est donc **vivante côté mécanisme, vide côté valeur**. Ce qu'un exécuteur ACP ferait
perdre ici est le *pouvoir* d'en poser un, jamais un plafond en vigueur.

### 4.2 La seconde surprise : le bac à sable, portable en principe, pas en l'état

Notre isolation **n'entoure pas** l'exécution, elle **substitue le binaire** — un shim passé en
`cli_path`, qui relaie vers `docker run` :

> le SDK croit lancer le CLI, le shim lance à la place le conteneur durci […] **Le flux stdio du
> protocole SDK ↔ CLI traverse tel quel** (`docker run --interactive` hérite des descripteurs du
> shim), et le code de sortie du CLI remonte inchangé.
> — [`sandbox/shim.py:1-13`](../maestro/sandbox/shim.py)

Or ACP est **exactement** cette frontière : un processus CLI, du JSON-RPC sur stdio. Le durcissement
lui-même (`--read-only`, `--cap-drop ALL`, `--network none`, `--pids-limit 256`, tmpfs, masquage des
chemins hors périmètre) ne sait **rien** de Claude — il est générique.

⚠ **Mais il ne lancera qu'un binaire nommé `claude`**, dans une image qui contient Claude Code :

> `return [*commande, image, "claude", *arguments]` — [`sandbox/container.py:301`](../maestro/sandbox/container.py)

Verdict honnête : **le confinement est le moins cher à conserver de toutes nos garanties — et il
n'est pas gratuit.** Il demande de paramétrer image et commande, pas de le repenser. Personne ne
l'aurait parié en lisant le ticket, qui range le bac à sable parmi les pertes.

⚠ Et ce qui survit est le **confinement**, jamais la connaissance de ce qui se passe dedans. Le bac
à sable dit où l'agent peut aller, pas ce qu'il a fait.

### 4.3 Ce qui tombe, et la décision était déjà écrite : le canal in-process

Notre surface d'écriture pour les agents est un **serveur MCP in-process** :

> un serveur SDK est servi **en process** par le SDK lui-même (`type: "sdk"`, déclaré au CLI dès
> l'initialisation) : **il n'a rien à connecter**.
> — [`providers/claude.py:381-385`](../maestro/providers/claude.py)

Un serveur qui n'a rien à connecter est un serveur **auquel on ne peut pas se connecter depuis un
autre processus**. ACP ne sait déclarer que du lançable ou du joignable (§2.3). **Un agent branché
par ACP ne verrait donc littéralement pas notre canal** — ni `demander_arbitrage`, ni les verbes que
#718 à #720 vont y ajouter.

Ce n'était pas une conjecture : [docs/31 §9.4](./31-decision-surface-ecriture-agents.md) l'avait
nommé comme condition de réouverture, en désignant ce ticket-ci.

> **La question posée là-bas est tranchée ici, et dans le même sens.** Exposer le canal une seconde
> fois — en serveur stdio ou HTTP, pour qu'un agent externe l'atteigne — serait **le second
> support** que [docs/31 §4](./31-decision-surface-ecriture-agents.md) refuse : « une des deux
> surfaces échapperait à la politique de permissions, qui ne sait désigner qu'un outil ». Le refus
> tient ici mot pour mot.

⚠ **Et ACP rend une partie de ce qu'il retire**, ce qu'il faut dire pour être juste : le protocole
porte lui-même une demande de permission de l'agent vers le client. Un agent ACP peut donc lever la
main — mais par **son** canal, avec **sa** granularité, sur les actes que **lui** juge sensibles.
C'est comparable en intention, jamais en gouvernance : notre politique #110 désigne des outils, la
sienne désigne ce qu'elle veut.

### 4.4 Ce qui devient déclaratif — et le dépôt l'a déjà payé une fois

Coût, tours, outils cessent d'être **constatés** pour devenir **rapportés**. La nuance a l'air mince.
Elle a été mesurée :

> **Coût non rapporté** : le dialecte chat completions ne porte pas de prix — `cout_usd` reste
> `null` (« inconnu », distinct de 0). Conséquence mesurée : **le plafond de dépense (1 $) était
> armé mais sans prise** — seuls les tokens sont comptés. Ticketisé → **#113**.
> — [docs/14 §4.2](./14-run-fournisseur-non-anthropic.md)

Le précédent exact, sur un vrai run : en branchant un exécutant qui ne rapporte pas comme nous, un
garde-fou est resté **en place, actif, et sans effet**. Personne ne l'a désarmé — il a cessé de
mordre, en silence.

⚠ **Et pour un agent ACP, c'est un cran pire que dans le cas Ollama.** Là-bas, les tokens étaient
encore comptés, donc le plafond en tokens gardait prise. Ici, `report_usage` est un appel
**volontaire du fournisseur** — « hors collecteur, le signalement est sans effet »
([`telemetry/usage.py:6-7`](../maestro/telemetry/usage.py), `:245`). Un agent qui n'expose ni coût ni
tokens fait tomber **les deux** plafonds : `UsageCollector.add` n'est jamais appelé,
`PlafondDepense.verifie` jamais consulté, et le run dépense sans borne.

Ce qui survit dans tous les cas : la **durée horloge**, posée par le moteur lui-même
(`engine/executor.py:446-451`). C'est la seule mesure qui ne dépend d'aucun fournisseur — et c'est
peu.

**Le refus au vol tombe entièrement.** `_hook_permissions` ([`providers/claude.py:570`](../maestro/providers/claude.py),
monté `:435`) intercepte **chaque** appel d'outil avant l'acte, et sa docstring dit ce qu'il est :
« le **seul point de contrôle restant** » sous `bypassPermissions`. Il repose intégralement sur le
mécanisme de hooks de l'Agent SDK. Avec lui tombe l'arbitrage `ask` — dont le fail-safe (« sans canal
câblé, un outil `ask` est refusé, jamais approuvé ») et les bornes d'attente sont calées sur le
comportement de timeout du CLI de Claude Code, à refaire de zéro pour un autre agent.

⚠ **Le livrable, lui, reste capturable**, et c'est la garantie la plus robuste de tout l'inventaire :
`Workspace.produced_files()` est une **différence d'état du système de fichiers observée après
coup** par le process hôte (`sandbox/workspace.py:79-94`, appelé `runtime.py:333`). Elle ne demande
au fournisseur ni protocole, ni callback, ni format — juste d'avoir écrit dans le répertoire qu'on
lui a désigné. Une réserve à connaître : rien n'y distingue *ce que l'agent a produit* de *ce qu'il a
écrit en passant* — un CLI tiers qui range son état de session dans son répertoire de travail
polluerait le livrable, là où le mode isolé de Claude le renvoie vers un tmpfs
(`sandbox/container.py:281`).

### 4.5 Un process qu'on ne sait pas arrêter

L'échéance de tâche est ferme **côté moteur** : l'échec est consigné à l'heure, sans rien exiger de
la tâche (`engine/executor.py:643`). L'**arrêt effectif du processus tiers**, lui, ne l'est pas —
`_annule_ou_detache` est « un vœu, jamais une attente », avec 5 s de grâce
(`engine/executor.py:1443`, `:161`), et docs/17 le confirme côté conteneur : « si le process hôte est
tué net, un conteneur peut survivre jusqu'à la fermeture de son stdin ».

**Un agent tiers peut donc rester orphelin, en train de travailler et de dépenser, hors de toute
comptabilité.** Combiné au §4.4 — aucun plafond armé —, c'est le risque le plus concret du
branchement, et le seul qui puisse coûter de l'argent après la fin du run.

### 4.6 La perte qu'on n'attendait pas : la configuration ambiante

`strict_mcp_config=True` ([`providers/claude.py:432`](../maestro/providers/claude.py)) « verrouille la
session sur **cette seule liste** — aucune configuration MCP ambiante (utilisateur, projet, plugin)
n'est jamais chargée ».

Un agent CLI tiers chargera **sa** configuration ambiante : ses serveurs MCP globaux, ses settings
utilisateur, ses plugins. Nous ne saurions ni ce qu'il monte, ni ce que ça lui donne le droit de
faire. C'est une porte que ce seul drapeau ferme aujourd'hui, et qu'aucune ligne d'ACP ne referme.

## 5. Ce que Maestro gagne — et sur quelles tâches

Deux gains, et il faut les séparer parce qu'ils n'ont pas la même solidité.

**Gain 1 — l'outillage qu'on n'a pas écrit.** Un agent CLI tiers apporte son édition de fichiers, son
parcours de dépôt, son cache, ses skills, sa reprise. Le dépôt en offre la mesure la plus honnête qui
soit, parce qu'il a les deux côtés :

| | Notre runtime (`LocalExecutor`) | Claude Code piloté (`scripts/orchestrate/`) |
| --- | --- | --- |
| Ce qu'il produit | un livrable de tâche, capturé | **des PR mergées sur `main`** |
| Exécution outillée hors Anthropic | **non** — « `run_agent` refusé → repli texte » ([docs/14 §4.1](./14-run-fournisseur-non-anthropic.md)) | sans objet |
| Coût observé | ~1-5 $ par run de démo | **29,09 $ par ticket livré** (§3.3) |

**Gain 2 — un contrat là où il n'y en a pas, et c'est le plus solide.** Le §3.2 l'a montré en
commentaire de notre propre code : la forme du signal de limite d'usage « n'est pas contractuelle et
a déjà changé d'une version à l'autre », et l'ordre des clés JSON a bougé lui aussi. **Nous payons
déjà, aujourd'hui, l'absence de protocole** — en filets multiples, en parseurs awk et en tests de
régression sur une surface que personne ne nous garantit. C'est le seul argument *pour* ACP que la
mesure soutient sans réserve, et il ne dit pas « branchons des agents tiers » : il dit « le jour où
nous en branchons, un protocole vaut mieux que de la rétro-ingénierie ».

**La question utile n'est donc pas « lequel est meilleur »**, mais *sur quelle tâche l'écart justifie
le prix du §3*. La réponse tient en une ligne : **sur les tâches qui demandent de naviguer un dépôt
existant et d'y produire un changement cohérent** — ce que notre runtime ne fait pas aujourd'hui, et
ce que `scripts/orchestrate/` fait tous les jours.

Sur les tâches du produit — rédiger, extraire, analyser, produire un livrable dans un espace de
travail dérivé — notre runtime fait déjà l'affaire, avec nos garde-fous, notre mesure et notre canal.
Y brancher un agent tiers coûterait le §4 pour un gain que personne n'a mesuré.

## 6. Où ça se brancherait — et pourquoi les deux points évidents sont faux

Le ticket propose trois emplacements. Les deux premiers sont ceux qu'on choisirait spontanément.

**O1 — un `ModelProvider` de plus : refusé, parce que l'interface mentirait.** C'est pourtant *le*
point d'extension prévu — `run_agent` est déclaré optionnel et lève `UnsupportedCapability` par
défaut ([`providers/base.py:445`](../maestro/providers/base.py)). Mais sa signature est un **contrat
de douze canaux** (`politique`, `on_refus`, `on_arbitrage`, `on_activite`, `on_etapes`,
`plafond_tours`…) dont un fournisseur tiers peut n'honorer **aucun** sans que le moteur s'en
aperçoive : la signature garantit la *possibilité* de ces garanties, jamais leur présence. Un
adaptateur ACP **typerait** et **mentirait** — tout l'amont continuerait de croire que ce qu'il passe
s'applique. C'est la panne du §4.4 promue au rang d'architecture : *armé, et sans prise*, partout et
pour toujours.

⚠ Le repli texte aggrave le cas : sans `run_agent`, un fournisseur retombe proprement sur un livrable
texte (`engine/executor.py:1419-1425`) — la dégradation est **visible**. Un adaptateur ACP qui
implémente `run_agent` en ignorant ses canaux produit la dégradation **invisible**. Mieux vaut le
refus franc que la demi-mesure silencieuse.

**O2 — un `TaskExecutor` de plus : refusé, parce que ce n'est pas ce que cette abstraction
abstrait.** Le grain est le bon (« cette tâche-là, c'est Claude Code qui la fait ») et c'est ce qui
rend l'erreur tentante. Mais les deux implémentations existantes disent ce que le contrat signifie :

- `LocalExecutor` exécute dans le process, en assemblant router, garde-fous, permissions, MCP,
  secrets, playbooks, capacités et projets ([`engine/executor.py:271-344`](../maestro/engine/executor.py)) ;
- `CeleryExecutor` **ne contourne rien** : le worker **reconstruit un `LocalExecutor`**
  ([`queue/worker.py:177`](../maestro/queue/worker.py)) et exécute « via le même chemin que la boucle
  locale » ([`queue/__init__.py:14`](../maestro/queue/__init__.py)).

> **`TaskExecutor` abstrait le LIEU d'exécution, jamais l'AUTORITÉ qui s'y applique.** Ses deux
> implémentations convergent sur le même assemblage de garanties ; elles ne diffèrent que par la
> machine.

Un exécuteur ACP serait le **premier** à différer au second sens. Il satisferait le contrat à la
lettre — `execute(task, deps, journal) -> TaskResult` — en rendant **inerte** tout ce que la boucle
croit avoir monté, sans qu'une ligne soit retirée.

⚠ Et le code montre que la marche est déjà glissante : **injecter un exécuteur fait déjà ignorer les
dépôts relus à chaud** (playbooks, capacités, MCP, projets, relance —
[`engine/loop.py:404-408`](../maestro/engine/loop.py)), parce qu'« en distribué, chaque worker câble
les siens ». Un exécuteur ACP hériterait de cette exemption **sans avoir de worker qui recâble quoi
que ce soit**. La perte serait silencieuse et déjà à moitié autorisée.

**O3 — un troisième point : retenu, et sa forme est contrainte par ce qui précède.** Si l'on branche
un jour, ce doit être là où la substitution est **déclarée** et non déduite — c'est-à-dire au niveau
du **rôle**, seul objet qui porte déjà « voici comment cet agent travaille ». Un rôle exécuté par un
agent externe est un rôle dont on sait, **en le lisant**, que le refus au vol et les plafonds ne
s'appliquent pas, parce que le profil le dit au lieu de les porter en vain.

⚠ Ce §6 **ne dessine pas le contrat**, et c'est délibéré : il élimine deux emplacements sur des faits
de code, et nomme la contrainte du troisième. Le dessiner supposerait la spécification lue (§2), qui
ne l'a pas été.

## 7. Le prix de l'hétérogénéité

La note #352 le range dans « à ne pas importer » :

> **L'hétérogénéité comme produit.** Brancher vingt agents veut dire hériter de vingt comportements
> de permissions, de vingt conventions de skills et de vingt façons de reprendre une session. Leur
> documentation en porte la trace, agent par agent. — #352, l. 1137

Nous en avons **notre propre mesure**, sur **un seul** agent : le §3.3. Et l'exemple le plus net est
l'interdit d'écriture sous `.claude/`, propre à ce CLI-là, qui a coûté #229, #238, #608, #610, #611,
#612 et une suite de tests dédiée — pour une détection qui plafonne à 63 % de précision avec un trou
irréductible de 32 %.

**Le prix de l'hétérogénéité n'est donc pas à estimer : il est à multiplier.** Un agent nous a coûté
ces six tickets. Le protocole en absorberait le cycle de session ; il n'absorberait ni les interdits
propres, ni les conventions de skills (§2.5), ni les trois variantes de reprise (§2.2), ni la
configuration ambiante que chacun charge (§4.6).

## 8. Le principe qu'ils appliquent et que nous n'appliquons pas

AionUi fait passer son propre moteur par le protocole public, sans voie privilégiée (#352,
l. 596-599). Le ticket demande si `maestro.agents.runtime` devrait en faire autant.

**Non — et la raison n'est pas la paresse : le principe ne dit pas la même chose chez eux et chez
nous.** Leur unité d'abstraction est le **processus agent** ; se plier au protocole public leur coûte
peu, leur moteur étant déjà un CLI. La nôtre est le **fournisseur de modèle** : notre runtime n'est
pas un processus, c'est une boucle in-process qui monte les serveurs MCP, applique la politique
d'outils, intercepte chaque appel avant l'acte et mesure l'usage. Le faire passer par ACP
reviendrait à **faire sortir de notre processus les garanties du §4 pour les faire rentrer par un
protocole qui n'en transporte que des versions déclaratives.** Ce serait payer l'agnosticisme en le
perdant.

⚠ La leçon reste bonne, à un cran de moins : **si nous ouvrons un jour, notre runtime ne doit pas
avoir de voie privilégiée dans le point d'extension du §6.** C'est une contrainte sur *ce point-là*,
pas un appel à nous plier à ACP.

## 9. La décision

> **Brancher sous condition — et aucune des conditions n'est remplie au 2026-08-28.**
> Aucun code n'est engagé. La porte est décrite, ses gonds sont nommés, elle reste fermée.

**Pourquoi pas « brancher ».** Le §5 ne trouve aucune tâche **du produit** où l'écart justifie le prix
du §3 : les tâches où un agent tiers est décisif sont les tâches de dépôt, et celles-là sont déjà
servies — hors du produit, mais servies. Brancher maintenant, ce serait payer le §4 et le §7 pour
récupérer dans le produit une capacité qu'on a déjà à côté, sans qu'aucun besoin ne l'ait demandée.

**Pourquoi pas « ne pas brancher ».** Un refus sec serait faux sur trois points mesurés. D'abord
parce que nous le faisons déjà (§1) : refuser « par principe » un branchement qu'on pratique tous les
jours est intenable. Ensuite parce que le coût d'entrée est **plus bas qu'annoncé** du côté qu'on
croyait le plus cher — le confinement se paramètre (§4.2), et il n'y a aucun plafond de tours à
perdre (§4.1). Enfin parce que le §5 établit un gain réel et indépendant de toute ouverture : **nous
payons déjà l'absence de contrat**, sur une surface qui a bougé deux fois.

**Ce que « sous condition » veut dire précisément.** Trois conditions, cumulatives :

1. **Un besoin réel, nommé par une tâche du produit** que notre runtime fait mal — pas une envie
   d'ouvrir. C'est la condition du ticket lui-même, et elle reste juste.
2. **La spécification ACP relue** en session interactive (§2), avec au minimum l'arbitrage
   `session/load` vs `session/resume` et la forme exacte de la déclaration MCP tranchés sur la
   norme.
3. **Une réponse écrite à trois questions du §4**, qui sont les seules à ne pas avoir de solution
   connue : le **canal** (§4.3 — soit l'agent s'en passe, soit on rouvre
   [docs/31 §4](./31-decision-surface-ecriture-agents.md)), le **plafond** (§4.4 — un exécutant qui
   ne rapporte rien n'est pas bornable) et le **process orphelin** (§4.5).

**Les raisons du refus des autres positions sont consignées** : §6 pour les emplacements, §8 pour le
principe, §9 pour les deux positions extrêmes.

## 10. Le découpage — et pourquoi il n'y en a pas

Le critère 3 du ticket demande « le découpage posé **si** la décision engage du code ». **Elle n'en
engage pas** : aucun parent de suivi, aucun lot.

C'est un écart assumé avec les trois cadrages voisins du même milestone — #354 → #717, #647 → #715,
#651 → #736 — qui ont tous engagé du code. La différence est que chacun répondait à un manque
mesuré. Ici la mesure dit l'inverse : **le besoin est déjà servi, hors du produit** (§5).

**Ce qui a été envisagé puis écarté, pour qu'on n'ait pas à le re-débattre :**

- **un POC d'adaptateur ACP** — écarté : un POC dont on n'a ni la spécification (§2) ni le cas
  d'usage (§5) mesurerait sa propre faisabilité, jamais son utilité ;
- **rapprocher `scripts/orchestrate/` du produit** — écarté ici, **et sans préjuger** : c'est un
  chantier réel, mais ce n'est pas celui de ce ticket. Il relève de la frontière d'exécution
  ([docs/28](./28-decision-frontiere-execution-run.md)), pas d'ACP, et le confondre avec un
  branchement de protocole ferait porter à ACP le mérite d'un déménagement ;
- **exposer le canal d'arbitrage en serveur externe** — écarté, refus déjà écrit
  ([docs/31 §4](./31-decision-surface-ecriture-agents.md), repris au §4.3) ;
- **poser un plafond de tours par défaut** « pour être prêt » — écarté, c'est #494 à l'envers, et le
  §3.3 montre deux fois ce que coûte une borne posée au cas où.

⚠ **Le seul geste qui aurait pu être posé aujourd'hui, et pourquoi il ne l'est pas.** On pourrait
figer l'invariant du §6 dans le code — interdire qu'un `TaskExecutor` rende inertes les garanties
assemblées, ce que `loop.py:404-408` autorise déjà à moitié. Ce serait un garde-fou contre un
branchement que personne ne prépare, sur un point d'extension dont la forme n'est pas arrêtée. Il est
au §11 comme condition, pas comme lot : le poser maintenant, ce serait construire le garde-fou avant
la route.

## 11. Ce qui rouvrirait la décision

Nommé d'avance, même patron que [docs/28 §8](./28-decision-frontiere-execution-run.md) et
[docs/33 §10](./33-decision-surveillance-run.md).

1. **Une tâche du produit que notre runtime fait mal**, nommée et mesurée — la condition 1 du §9. Le
   signal est précis : une tâche qui échoue *parce que* l'exécution manque d'outillage de dépôt, pas
   parce que le modèle est faible (l'écart de [docs/14 §3](./14-run-fournisseur-non-anthropic.md) est
   un écart de modèle, et il ne compte pas).
2. **Une demande d'ouverture** — quelqu'un veut brancher *son* agent. La décision change alors de
   nature : ce n'est plus « gagne-t-on quelque chose », c'est « qu'exigeons-nous d'un exécutant
   tiers ». Le §4 devient la liste des exigences, telle quelle.
3. **ACP cesse d'être déclaratif sur l'usage** — si le protocole normalise la remontée du coût et des
   tours, le §4.4 tombe, et avec lui la moitié du prix. C'est le changement externe le plus
   susceptible d'arriver sans nous.
4. **L'exécution outillée multi-fournisseurs est livrée** — aujourd'hui elle « reste pour la suite »
   ([docs/04](./04-specifications-agents.md) : « le champ `fournisseur` est déclaratif, le moteur
   exécute sur `MAESTRO_PROVIDER` »). Elle déplacerait la frontière du §5 : notre runtime saurait
   faire ailleurs ce qu'il ne sait faire que chez Anthropic, et le gain d'un agent tiers se
   réduirait d'autant.
5. **La surface du CLI de Claude Code casse une troisième fois** (§3.2). Deux ruptures sont un
   incident ; une troisième ferait du gain 2 du §5 un besoin, indépendamment de toute ouverture — et
   c'est alors `scripts/orchestrate/` qui voudrait un protocole, pas le produit.

Aucune de ces cinq conditions n'est remplie au 2026-08-28.

## 12. Où cette décision est écrite ailleurs

- [docs/31 §9](./31-decision-surface-ecriture-agents.md) — les deux conditions de réouverture qui
  **nomment ce ticket**. La seconde (le canal) est tranchée ici, §4.3 ; la première (l'identité
  d'instance) **reste ouverte** et n'est pas de ce ressort : elle suppose un exécuteur tiers
  *persistant*, que le §9 ne branche pas.
- [docs/28 §7](./28-decision-frontiere-execution-run.md) — ce que la veille AionUi apporte à la
  frontière d'exécution ; le §10 dit pourquoi le rapprochement de `scripts/orchestrate/` relève de
  là et non d'ici.
- [docs/14 §4.2](./14-run-fournisseur-non-anthropic.md) — « armé mais sans prise » (#113), le
  précédent mesuré du §4.4.
- [docs/presentations/veille-aionui-2026-08-17.html](./presentations/veille-aionui-2026-08-17.html)
  — la source, et la seule mention d'ACP du dépôt avant cette note.

**Sur la numérotation.** Cette note prend **34**. `32` est **laissé libre à dessein** par
[docs/33 §11](./33-decision-surveillance-run.md), qui le réserve à la résolution du doublon de `31`
(ticket #742) ; le prendre ici aurait fermé cette porte. La règle qu'il énonce est appliquée : le
numéro se réserve en ouvrant le ticket, pas en écrivant le fichier.
