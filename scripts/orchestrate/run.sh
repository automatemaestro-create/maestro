#!/usr/bin/env bash
# La boucle d'orchestration autonome : un ticket, une session Claude Code (#170, parent #167).
#
#   bash scripts/orchestrate/run.sh --dry-run     # le plan et ce qui serait fait, sans rien lancer
#   bash scripts/orchestrate/run.sh               # traite le plan, ticket par ticket
#   bash scripts/orchestrate/run.sh --max 1       # un seul ticket (le premier du plan)
#   bash scripts/orchestrate/run.sh --concurrence 3   # jusqu'à 3 tickets INDÉPENDANTS en vol
#
# Chaque ticket est traité DANS SON PROPRE WORKTREE et DANS SA PROPRE SESSION : `/ticket-start` →
# implémentation → `/ticket-ship`, sans interruption. Le run produit N Merge Requests en Draft à
# relire ; il ne ferme et ne force-push jamais, et ne merge jamais hors de `lib.sh merge-mr`, qui
# éprouve ses prérequis avant de merger (#417).
#
# --- Pourquoi un script shell, et pas une session Claude Code qui piloterait les autres ------------
# Une boucle écrite en `/loop` ou en sous-agents consommerait le MÊME QUOTA que le travail piloté :
# la limite d'usage tuerait le pilote en même temps que la session pilotée, et plus rien ne pourrait
# programmer la reprise. Un script shell ne consomme aucun quota — il peut attendre et relancer.
# (La reprise après limite d'usage elle-même est le lot suivant, #171 ; ici la boucle s'arrête sur
# l'échec d'un ticket et le consigne.)
#
# --- Le verdict d'un ticket vient de GitLab, pas du texte de la session ---------------------------
# Une session peut conclure « c'est fait » en s'étant trompée, ou échouer après avoir tout livré.
# On ne lit donc pas sa prose : un ticket est réussi si, et seulement si, sa branche porte une PR
# OUVERTE et son cycle de vie est « En revue » — exactement ce que `/ticket-ship` laisse derrière
# lui. C'est vérifiable, et ça ne dépend pas de la formulation du modèle.
#
# Ce cycle de vie est porté par le champ Status d'un projet GitHub Projects v2 (#365, chantier
# #358) — troisième support après le champ natif de GitLab et les six labels `workflow::*`. Rien à
# en savoir de plus ici : lib.sh rend toujours le LIBELLÉ (« En revue »), jamais un slug
# (« en-revue ») — c'est son contrat de surface, documenté en tête de scripts/gitlab/lib.sh. Les
# comparaisons de ce fichier n'ont bougé à aucun des trois changements de stockage.
#
# --- Ce qu'un échec entraîne ------------------------------------------------------------------------
# Le ticket est laissé en l'état (branche et cycle de vie « En cours »), et LES LOTS SUIVANTS DU
# MÊME PARENT sont sautés : ils partiraient d'une base incomplète. Les autres groupes du plan
# s'enchaînent normalement — une erreur à 2 h du matin ne doit pas geler le reste de la nuit.
#
# --- N tickets en vol dans un run (#289, parent #287) -------------------------------------------------
# `--concurrence <n>` (défaut 1) laisse partir jusqu'à `n` tickets à la fois. L'indépendance n'est pas
# devinée ici : elle est LUE dans le plan, colonne `groupe` (#288) — « deux tickets peuvent être en vol
# en même temps si leurs `parent` diffèrent, ou si leur `groupe` est identique ». Défaut 1 = le run
# d'hier, au bit près : c'est ce qui rend ce lot mergeable seul.
#
# Le découpage suit la seule ligne qui compte, celle entre ce qui est ÉTAT DU RUN et ce qui est LONG :
#   · le PILOTE (ce processus) garde tout l'état — plan, éligibilité, sauts, compteurs, `--max`,
#     cascade d'échec, montage des worktrees, verdicts GitLab, `resume.tsv`. Il est SEUL À ÉCRIRE le
#     bilan, ce qui règle par construction la question « une ligne de resume.tsv reste-t-elle
#     entière ? » : il n'y a jamais deux écrivains ;
#   · le SOUS-SHELL d'un ticket ne porte que la session Claude et ses reprises — la seule partie qui
#     dure des dizaines de minutes. Il rend un code, rien d'autre à recoller.
# Le montage du worktree reste donc côté pilote, SÉRIALISÉ : `git worktree add` prend des verrous sur
# les refs du dépôt partagé, et quelques minutes d'installation en série se noient dans une session
# qui dure une heure.
#
# Ce que #289 avait laissé en plan et qui ne l'est plus : la VUE VIVANTE, qu'il éteignait au-delà d'un
# ticket faute de pouvoir la partager, est rendue à N par #290 — le pilote dessine, une session publie.
# Ce qui reste, à `--concurrence > 1`, est le QUOTA : N sessions tirent sur la même fenêtre de 5 h, et
# la console le dit au démarrage. Le gain est en temps de mur, jamais en quota.
#
# --- Ce que N en vol change à la limite d'usage, à l'arrêt et à la reprise (#291) ----------------------
# Trois mécanismes supposaient une seule session en vol et devenaient faux à N :
#   · la LIMITE D'USAGE tombe sur toutes les sessions à quelques secondes d'intervalle. L'attente est
#     donc une ATTENTE DU RUN — un rendez-vous unique dans `<run-dir>/.limite`, où la meilleure
#     information l'emporte (une heure de reset explicite écrase un palier aveugle) — et chaque
#     session coupée est ensuite rouverte PAR SON UUID, comme avant. Voir « L'attente partagée » ;
#   · l'ARRÊT doit atteindre les N sous-shells et leurs `claude.exe` : `pilote_tue` vise désormais le
#     WINPID de CHAQUE cible, du plus profond au plus superficiel, au lieu de faire confiance au seul
#     arbre du pilote (#291, pilote.sh) ;
#   · la REPRISE d'un run coupé rejoue tous les tickets qu'il avait en main — la question est posée
#     par ticket (`reprend_en_vol`) — et à SA concurrence, relue dans le fichier `concurrence` du run
#     repris, faute de quoi un run à quatre en vol se reprendrait en séquentiel.
#
# --- Ce qu'un run fait avant son premier ticket -------------------------------------------------------
# Trois ménages, tous best-effort, tous muets quand il n'y a rien à faire et aucun fatal : `main`
# remise à niveau sur `origin/main` (#283, fast-forward seul, MAESTRO_SYNC_MAIN=0 pour l'éteindre),
# worktrees soldés ramassés (#197), vieux journaux purgés (#198). Aucun ne tourne en `--dry-run`.
#
# --- La file de merge : au fil de l'eau pendant le run, drain en fin de run (#419, parent #413) -------
# Un run laissait N PR ouvertes derrière lui, et c'est la raison d'être du chantier : il les MERGE
# désormais, sans attendre son terme.
#
# POURQUOI AU FIL DE L'EAU, ET PAS UNE SALVE FINALE. Les branches de tickets partent toutes
# d'`origin/main` au moment où `worktree.sh` les monte. Merger tôt fait donc démarrer les tickets
# suivants sur un `origin/main` qui CONTIENT DÉJÀ les précédents : les conflits ne sont pas résolus
# plus vite, ils ne sont pas fabriqués. C'est le seul des deux moments qui agisse sur la cause.
#
# CE QUE LE PILOTE FAIT, ET LUI SEUL. Même partage qu'à #289 : le pilote garde tout l'état du run,
# les sessions ne font que travailler. Aucune session ne merge, aucune n'attend un pipeline — ce
# serait du quota brûlé à ne rien faire. `merge.tsv` n'a donc qu'un écrivain, exactement comme
# `resume.tsv`, et la question de l'atomicité d'une ligne ne se pose pas.
#
# UN MERGE À LA FOIS, ET LE VERDICT RECALCULÉ APRÈS CHACUN. Un merge déplace `origin/main`, donc
# périme le verdict de conflit de TOUTES les autres PR. Une passe s'arrête donc au premier merge
# réussi : ce qui reste sera rejugé au passage suivant, jamais sur une mesure d'avant. Même raison
# que la sérialisation du montage des worktrees (#289). `sync-main` suit chaque merge — il est dans
# `merge-mr` (#415), pour la raison de #205 : un run est ce qui fait vieillir `main` le plus vite, et
# il en ouvre désormais autant qu'il en merge.
#
# LA DÉCISION N'EST PAS ICI. Elle est tout entière dans `lib.sh merge-mr` (#415), qui vérifie puis
# merge en un geste — PR ouverte non brouillon qui ferme le ticket, rien de non poussé, aucun conflit
# réel, pipeline vert SUR LA TÊTE de la PR. Le pilote ne fait que lire son code de retour et en tirer
# une conduite : `0` mergée, `3` on repassera, `4`/`5` bloquée et réparable (/mr-fix, lot 6 #420),
# `6` bloquée et c'est un geste humain. Rejouer ces contrôles ici en ferait deux endroits qui disent
# « mergeable » au lieu d'un — c'est précisément ce que #415 a supprimé.
#
# DEUX DRAINS, PARCE QU'IL Y A DEUX MOMENTS. Pendant le run le drain est NON BLOQUANT : il ne fait
# qu'appeler `merge-mr` et repart, une PR encore en pipeline restant en file. En fin de run, plus
# aucun ticket ne tourne : l'attente ne coûte que du temps de mur, donc `pipeline-wait` est autorisé
# et ce qui reste est traité dans l'ordre de `merge-order` (#416), le moins conflictuel.
#
# CE QUE STOP GARDE. Il arrête de LANCER, il n'interrompt pas un merge en cours. Il retire en
# revanche `pipeline-wait` du drain final : quelqu'un qui demande l'arrêt n'attend pas un quart
# d'heure par PR — ce qui est déjà vert est mergé, le reste est nommé et laissé en file.
#
# --- Débloquer une PR pendant le run : une session /mr-fix, deux fois au plus (#420, parent #413) ----
# Le drain sort une PR de la file sur un `4` (pipeline rouge) ou un `5` (conflit). Ces deux-là sont
# RÉPARABLES, et souvent par le run lui-même : un merge qu'il vient de faire est ce qui a mis la PR
# suivante en conflit. Les laisser là serait rendre la moitié de la promesse du chantier.
#
# POURQUOI UNE SESSION, ET PAS DU SHELL. Une résolution de conflit est une décision de CONTENU
# (#299) : celle qui se règle toute seule n'a jamais été le cas intéressant, et celle qui laisse des
# marqueurs demande de lire le code des deux côtés. Le dépôt sait déjà faire ça — c'est `/mr-fix` —
# et le pilote sait déjà ouvrir une session Claude : il en ouvre une par ticket.
#
# ELLE NE MERGE PAS, et c'est ce qui garde « un seul endroit décide qu'un merge a lieu ». Elle rend
# la PR mergeable ; le pilote la remet en file et retente `merge-mr`. `guard.sh` refuse de toute
# façon `merge-mr` et `pipeline-wait` à toute session d'un run (#419) — le prompt le dit AVANT que
# la session s'y heurte, parce que `/mr-fix` merge ce qu'il débloque depuis #418 et qu'un ordre
# contredit sans explication se contourne au lieu de se suivre.
#
# ELLE N'ATTEND AUCUN PIPELINE non plus, pour la raison qui vaut déjà pour les sessions de ticket :
# ce serait du quota brûlé à ne rien faire. Elle pousse son correctif et sort ; le pilote relira le
# verdict à la passe suivante. La boucle « corriger, attendre, recommencer » de `/mr-fix` devient
# donc celle du pilote — et c'est elle que le plafond de DEUX tentatives par PR borne.
#
# MÊME RÉGIME QUE LES SESSIONS DE TICKET, sans exception : worktree du ticket (remonté s'il a été
# ramassé), modèle et effort du run, `settings.run.json` et son hook, journal `<iid>-mrfix.*`, coût
# compté, et un CRÉNEAU de `--concurrence` occupé — toutes les sessions tirent sur le même quota, et
# une remédiation qui s'en affranchirait ferait tourner N+1 sessions là où l'on en a demandé N.
# La limite d'usage s'y applique aussi : elle se range derrière le rendez-vous unique de #291.
#
# CE QUI N'EST PAS RÉPARABLE ICI. Le `6` (geste humain) ne déclenche rien — c'est sa définition. Une
# session qui abandonne (`git merge --abort`, résolution pas claire) laisse la PR OUVERTE et INTACTE,
# et c'est un résultat, pas un échec : ce qui n'est pas résolu proprement n'est pas poussé.
#
# --- Journal --------------------------------------------------------------------------------------
# .maestro/orchestrate/<run-id>/
#   plan.tsv          le plan figé au démarrage (sortie de queue.sh)
#   <iid>.session     l'UUID de la session du ticket (clé de la reprise, #171)
#   <iid>.jsonl       le flux d'activité de la session, un événement par ligne (#176) — gzippé en
#                     `<iid>.jsonl.gz` dès le verdict rendu (#198), à relire avec zcat/zgrep
#   <iid>.json        le résultat FINAL de la session seul (coût, usage, permission_denials…)
#   <iid>.resultat.txt  le même, mais LISIBLE (#180) : verdict, coût, durée, refus, message final
#   <iid>.log         ce que la session a écrit sur stderr
#   resume.tsv        une ligne par ticket : iid, verdict, PR, durée, coût, raison
#   merge.tsv         la file de merge (#419) : iid, pr, branche, état, code, tentatives, cause,
#                     puis les deux colonnes du déblocage (#420) — sessions /mr-fix jouées, et leur
#                     coût cumulé. Écrite par le pilote SEUL, réécrite en entier à chaque changement
#                     — quelques lignes, et c'est ce qu'une reprise relit pour ne pas rejouer un
#                     merge déjà fait
#   merge.log         la sortie brute de chaque appel à `merge-mr` — la cause d'un refus y est
#                     entière, `merge.tsv` n'en gardant qu'une ligne
#   <iid>-mrfix.*     la session de déblocage d'une PR (#420) : mêmes fichiers qu'un ticket
#                     (`.jsonl`, `.json`, `.resultat.txt`, `.log`, `.session`), sous une clé qui la
#                     distingue de la session du ticket. La seconde tentative porte `-mrfix2`
#   pid               la carte d'identité du pilote (#213) : PID, WINPID, naissance, hôte — posée au
#                     démarrage, retirée à la sortie, et seule chose qui permette de TUER un run
#   concurrence       le nombre de tickets en vol de ce run (#291), relu par `--resume` pour rejouer
#                     le même run et non sa version séquentielle
#   .limite           le rendez-vous d'attente de la limite d'usage (#291) : « <fin epoch> <TAB>
#                     reset|palier <TAB> <iid> ». Une seule attente pour les N sessions en vol
#   .plafond          l'iid de la session qui a franchi le plafond des 5 h 30 (#291) — sa présence
#                     sort de l'attente les N-1 autres, qui dormiraient sinon sur une limite
#                     hebdomadaire dont le run a déjà tiré les conséquences
#
# Le journal ne s'accumule plus sans fin (#198) : au démarrage d'un run, `journal.sh gc --auto` ne
# garde que les N derniers runs et ramasse les répertoires vides — jamais le run courant, jamais un
# run qui écrit encore. Diagnostic sans écriture : `journal.sh gc --check`.
#
# Arrêt d'urgence : créer .maestro/orchestrate/STOP — testé entre deux tickets.
#
# --- Un seul run à la fois (#213) --------------------------------------------------------------------
# Démarrer (ou reprendre) commence par TUER les runs encore en vol. Deux pilotes vivants, c'est le
# même quota brûlé en double, un unique fichier STOP pour les deux, et une reprise qui rejoue le plan
# d'un run toujours en train de le jouer. Le tri s'appuie sur la carte `pid` ci-dessus : jamais sur
# un `claude.exe` trouvé au jugé — la session Claude Code interactive de l'utilisateur en est un.
# Les runs tués restent REPRENABLES : on ne touche pas à leur journal. `--sans-kill` pour s'en
# passer, `--tuer-les-runs` pour ne faire que ça.
#
# --- Coutures de test -------------------------------------------------------------------------------
# Pour que la boucle soit vérifiable sans consommer de quota, sans réseau et sans créer de vraie
# branche (#172) : MAESTRO_CLAUDE_BIN remplace le CLI, MAESTRO_ORCHESTRATE_WORKTREE remplace le
# montage du worktree, et le CLI de forge se substitue par le PATH (lib.sh l'appelle par son nom).
# MAESTRO_ORCHESTRATE_CONSOLE (#240) fait de même pour l'écran : un fichier y tient lieu de console,
# et les frames de la vue vivante s'y relisent sans pseudo-terminal.

set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$RACINE/scripts/gitlab/lib.sh"
# shellcheck source=scripts/orchestrate/pilote.sh
. "$RACINE/scripts/orchestrate/pilote.sh"

ORCH_DIR="$RACINE/.maestro/orchestrate"
STOP="$ORCH_DIR/STOP"
CLAUDE_BIN="${MAESTRO_CLAUDE_BIN:-claude}"   # surchargeable : stub dans les tests (#172)

DRY=0
DETACH=0
MAX=0
# Le nombre de tickets en vol (#289). Défaut 1 : sans l'option, le run est celui d'hier, strictement
# séquentiel. C'est la valeur qui rend ce lot mergeable seul, et elle reste le bon défaut — toutes les
# sessions tirent sur le MÊME quota d'abonnement, donc N en parallèle épuisent la fenêtre de 5 h N fois
# plus vite : le gain est en temps de mur, jamais en quota (parent #287).
CONCURRENCE="${MAESTRO_ORCHESTRATE_CONCURRENCE:-1}"
# Posé dès que quelqu'un a dit lequel il voulait — option ou variable d'environnement. C'est ce qui
# permet à `--resume` de rejouer la concurrence du run repris sans jamais écraser un choix explicite
# (#291) : « non demandé » et « demandé à 1 » ne sont pas la même chose.
CONCURRENCE_EXPLICITE=0
[ -n "${MAESTRO_ORCHESTRATE_CONCURRENCE:-}" ] && CONCURRENCE_EXPLICITE=1
# Le budget n'a plus de défaut (#286) : sans `--budget`, AUCUN `--max-budget-usd` n'est passé au
# CLI, et une session s'arrête sur son ticket, son timeout ou la limite d'usage — jamais sur un
# montant. Les 15 $/ticket d'origine étaient le garde-fou d'une boucle neuve, quand on craignait
# l'emballement ; à `claude-opus-5` + effort `xhigh` sur un gros lot, ils coupent une session EN
# PLEIN TRAVAIL, et une coupure au plafond est indiscernable d'un échec — la session meurt sans
# `/ticket-ship`, son travail reste non commité dans le worktree, et l'échec fait sauter les lots
# suivants du même parent (§11.5). Deux runs du 2026-08-06 l'ont payé au même montant exact
# (#277 et #245, 15.07 $ chacun) : 2 tickets perdus, 13 sautés en cascade, pour zéro livrable.
# Un run reste borné par ce qui borne vraiment — le fichier STOP, la limite d'usage, le plafond
# d'attente de 5 h 30 (le `--timeout` cité ici à l'origine a suivi le même sort en #326, pour la
# même raison). Le montant, lui, ne borne rien d'utile tant qu'on ne le demande pas : `--budget <usd>` et
# MAESTRO_ORCHESTRATE_BUDGET restent là pour le poser explicitement, vide ou 0 valant « pas de
# plafond » (0 est aussi la seule façon d'annuler une variable déjà posée dans l'environnement, et
# `--max-budget-usd 0` tuerait chaque session au premier jeton).
BUDGET="${MAESTRO_ORCHESTRATE_BUDGET:-}"
# Le timeout n'a plus de défaut non plus (#326) : sans `--timeout`, la session n'est enveloppée
# d'AUCUN `timeout`. C'est le raisonnement de #286 sur l'autre plafond, et il s'y transpose mot pour
# mot — 45 min étaient le garde-fou d'une boucle neuve, quand une session durait 20 min ; au régime
# épinglé par le dépôt (`claude-opus-5` + effort `xhigh`, #206/#217) elles sont devenues le premier
# tueur de sessions du run. Mesuré le 2026-08-10, run 20260810-141208 : #315 livré en 42min50 (2 min
# de marge) et #316 coupé à 45min02 — son travail était FINI et commité, le couperet est tombé
# pendant le push et l'ouverture de la PR. Le plafond n'a donc rien protégé : il a transformé un
# ticket livrable en échec, puis l'échec en sept lots sautés en cascade (§11.5), pour un seul
# livrable à 14,75 $. Un run reste borné par ce qui le borne vraiment — le fichier STOP, la limite
# d'usage, le plafond d'attente de 5 h 30, et l'humain devant la console. `--timeout <durée>` et
# MAESTRO_ORCHESTRATE_TIMEOUT restent là pour en poser un ; vide ou `0` valent « aucun », seule
# façon d'annuler une variable déjà posée dans l'environnement.
TIMEOUT_BRUT="${MAESTRO_ORCHESTRATE_TIMEOUT:-}"
# Le modèle s'épingle **en toutes lettres**, jamais par alias (#206). `--model opus` est résolu par
# le CLI, et sa cible bouge d'une version à l'autre : sur 2.1.215 elle valait encore
# `claude-opus-4-8`. Un alias fait donc décider la version installée sur le poste à la place du
# dépôt — deux machines ne traitent plus le backlog avec le même modèle, et le journal d'un run ne
# dit pas sur quoi il a tourné. `MAESTRO_ORCHESTRATE_MODELE` et `--modele` restent libres d'y
# remettre un alias, en connaissance de cause.
MODELE="${MAESTRO_ORCHESTRATE_MODELE:-claude-opus-5}"
# L'effort s'épingle pour la même raison que le modèle (#217), et il était le dernier réglage de
# session à ne pas l'être : `run.sh` ne passait AUCUN `--effort`, si bien que le niveau venait de
# `~/.claude/settings.json` du poste — donc du poste, pas du dépôt. Le mécanisme est le même que
# pour les permissions : `--settings` AJOUTE une couche au lieu de remplacer la chaîne, et
# `settings.run.json` ne redéfinissant pas `effortLevel`, c'est celui de l'utilisateur qui valait
# (cf. l'union du `allow`, constatée au run de #179). Trois dérives qu'aucune sortie ne montrait :
# un clone sans ce réglage traitait le backlog à l'effort par défaut, un `/effort` posé un jour
# changeait le régime de TOUTES les sessions autonomes, et les coûts de `resume.tsv` n'étaient plus
# comparables d'une machine à l'autre. `MAESTRO_ORCHESTRATE_EFFORT` et `--effort` restent libres
# d'en sortir, en connaissance de cause.
EFFORT="${MAESTRO_ORCHESTRATE_EFFORT:-xhigh}"
PLAN_IMPOSE=""
MILESTONE=""
RUN_ID=""
TEST_REPRISE=""
LIRE_RESULTAT=""
REPRISE=0
REPRISE_ID=""
REPRISE_DIR=""
REPRISE_AVEC_VALEUR=0
SANS_KILL=0
TUER_SEUL=0
VERBEUX="${MAESTRO_ORCHESTRATE_VERBEUX:-0}"
# La file de merge (#419). Active par défaut : c'est la raison d'être du chantier #413, et un run
# qui ne mergerait pas laisserait derrière lui exactement ce qu'on cherchait à supprimer.
# `MAESTRO_ORCHESTRATE_MERGE=0` (ou `--sans-merge`) rend le run d'avant — il ouvre ses PR et s'arrête
# là —, au même titre que MAESTRO_SYNC_MAIN=0 ou MAESTRO_PURGE_BRANCHES=0 ailleurs.
MERGE="${MAESTRO_ORCHESTRATE_MERGE:-1}"
case "$MERGE" in 0 | non | off | false) MERGE=0 ;; *) MERGE=1 ;; esac
# L'intervalle entre deux passes du drain au fil de l'eau, DANS la boucle d'attente. Un drain coûte
# une poignée d'appels réseau par PR en file : le jouer au rythme de la boucle (0,2 s) noierait
# l'API pour une réponse qui ne change pas plus vite qu'un pipeline. À chaque verdict de ticket, en
# revanche, le drain est joué SANS attendre l'intervalle — c'est le moment où quelque chose vient
# justement de changer.
MERGE_INTERVALLE_S="${MAESTRO_ORCHESTRATE_MERGE_INTERVALLE:-60}"
case "$MERGE_INTERVALLE_S" in '' | *[!0-9]*) MERGE_INTERVALLE_S=60 ;; esac
# Le déblocage d'une PR PENDANT le run (#420). Actif par défaut, pour la même raison que la file
# elle-même : une PR mise en conflit par le merge d'un ticket précédent du run est un blocage que le
# run a FABRIQUÉ, et le laisser derrière lui rendrait la moitié de la promesse du chantier #413.
# `MAESTRO_ORCHESTRATE_MRFIX=0` (ou `--sans-mrfix`) laisse la PR bloquée avec sa cause au bilan —
# exactement ce que faisait le lot 5 seul.
MRFIX="${MAESTRO_ORCHESTRATE_MRFIX:-1}"
case "$MRFIX" in 0 | non | off | false) MRFIX=0 ;; *) MRFIX=1 ;; esac
# Le plafond de tentatives PAR PR. Deux, et pas « jusqu'à ce que ça passe » : une session `/mr-fix`
# coûte un ticket entier de quota, et ce qu'elle n'a pas su débloquer deux fois demande un humain —
# insister au-delà, c'est brûler du quota sur un blocage qui ne bougera pas.
MRFIX_MAX="${MAESTRO_ORCHESTRATE_MRFIX_MAX:-2}"
case "$MRFIX_MAX" in '' | *[!0-9]*) MRFIX_MAX=2 ;; esac

usage() {
  cat <<'USAGE'
La boucle d'orchestration autonome — un ticket, une session Claude Code.

  bash scripts/orchestrate/run.sh [options]

Options :
  --dry-run            N'exécute rien : affiche le plan et ce qui serait fait.
  --resume [<run-id>]  Reprend un run qui ne s'est pas terminé : rejoue SON plan et SA concurrence,
                       sans les recalculer. Sans argument, le run reprenable le plus récent. Les
                       tickets déjà livrés se sautent d'eux-mêmes ; TOUS ceux qui étaient en vol au
                       moment de la coupure sont repris, chacun dans sa session. Se combine avec
                       --detach ; --concurrence explicite l'emporte sur celle du run repris.
  --detach             Relance le run dans une console indépendante et rend la main tout de suite.
                       C'est ce qui permet de démarrer un run depuis une session Claude Code : le
                       pilote reste un script shell, dans son propre processus.
  --max <n>            Nombre maximal de tickets traités (0 = tout le plan).
  --concurrence <n>    Nombre de tickets en vol en même temps. Défaut 1 (run séquentiel). Deux
                       tickets ne partent ensemble que si le plan les dit indépendants : parents
                       différents, ou même groupe de dépendance (colonne « groupe » de queue.sh).
                       Au-delà de 1, la vue vivante s'éteint et les sessions partagent le quota.
  --budget <usd>       Plafond de dépense par ticket (--max-budget-usd). Par défaut AUCUN : une
                       session va au bout de son ticket, de son délai s'il en a un, ou de la
                       limite d'usage. 0 (ou vide) vaut « pas de plafond ».
  --timeout <durée>    Délai maximal par ticket : 45m, 90m, 2700… Par défaut AUCUN : un délai
                       tue la session EN PLEIN TRAVAIL, sans commit ni PR, et fait sauter les
                       lots suivants du même parent. 0 (ou vide) vaut « pas de délai ».
  --modele <modèle>    Modèle des sessions. Défaut : claude-opus-5.
  --effort <niveau>    Effort de raisonnement des sessions : low, medium, high, xhigh, max.
                       Défaut : xhigh.
  --plan <fichier>     Utilise un plan déjà calculé (TSV de queue.sh) au lieu d'en calculer un.
  --milestone <titre>  Transmis à queue.sh (par défaut : la phase courante).
  --run-id <id>        Identifiant du run. Défaut : horodatage.
  --sans-merge         N'ouvre pas de file de merge : le run laisse ses PR ouvertes, comme avant
                       #419. Par défaut, une PR verte est mergée PENDANT le run (par
                       « lib.sh merge-mr », qui vérifie avant de merger) et ce qui reste est drainé
                       en fin de run. MAESTRO_ORCHESTRATE_MERGE=0 fait de même.
  --sans-mrfix         N'ouvre aucune session /mr-fix : une PR bloquée (conflit ou pipeline rouge)
                       le reste, avec sa cause au bilan. Par défaut, le run tente de la débloquer
                       PENDANT qu'il tourne, deux fois au plus par PR.
                       MAESTRO_ORCHESTRATE_MRFIX=0 fait de même.
  --sans-kill          Ne tue pas les runs encore en cours avant de démarrer (voir plus bas).
  --tuer-les-runs      Ne fait QUE ça : tue les runs en cours, dit lesquels, et sort.
  --max-reprises <n>   Reprises maximales après limite d'usage, par ticket. Défaut : 3.
  --verbeux            Diagnostic : réimprime une ligne par appel d'outil de la session, comme
                       avant #240. Désactive la vue vivante (les deux se disputeraient l'écran).
  --test-reprise <f>   Diagnostic : dit si la sortie de session <f> serait vue comme une limite
                       d'usage, et combien de temps la boucle attendrait. N'exécute rien d'autre.
  --resultat <f>       Diagnostic : relit un <iid>.json de session et l'imprime EN CLAIR (état,
                       coût, durée, refus de permission, message final). N'exécute rien d'autre.
                       Un run écrit déjà cette vue à côté, dans <iid>.resultat.txt.
  -h, --help           Cette aide.

Un seul run à la fois : démarrer ou reprendre commence par TUER les runs encore en vol (leur pilote
et la session Claude qu'il pilotait), parce que deux runs brûlent le même quota et se partagent un
unique fichier STOP. Les runs tués gardent leur journal intact et restent reprenables.

Limite d'usage : la boucle attend jusqu'au reset et reprend la même session Claude. Au-delà de
5 h 30 d'attente cumulée sur un ticket, c'est la limite hebdomadaire : le run s'arrête proprement —
et c'est « --resume » qui le rejoue plus tard. Les runs reprenables : status.sh --reprenables.

Arrêt d'urgence : créer .maestro/orchestrate/STOP (testé entre deux tickets et pendant l'attente).
Il arrête de LANCER : un merge en cours n'est pas interrompu, et le drain final se joue alors sans
attendre aucun pipeline.

File de merge : une PR verte est mergée pendant le run, sans attendre son terme — les tickets
lancés ensuite partent donc d'un origin/main qui la contient. Le merge passe TOUJOURS par
« lib.sh merge-mr », qui vérifie avant de merger (PR ouverte non brouillon qui ferme son ticket,
rien de non poussé, aucun conflit, pipeline vert sur la tête de la PR) ; le run ne ferme et ne
force-push jamais. Ce qui n'a pas pu être mergé est nommé dans le résumé, avec sa cause.

Déblocage : une PR qu'un conflit ou un pipeline rouge empêche de merger ouvre une session /mr-fix
dans le worktree de son ticket, sous le même régime que les sessions de ticket (modèle, effort,
garde-fous, journal, quota) — deux fois au plus par PR. Elle rend la PR mergeable ; c'est le pilote
qui merge, jamais elle. Au-delà, la PR reste ouverte et intacte, avec sa cause au bilan.
USAGE
}

# Les arguments d'origine, gardés tels quels : `--detach` les repasse au run détaché, à l'exception
# de `--detach` lui-même (sans quoi la console relancerait une console, indéfiniment).
ARGS_ORIG=("$@")

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --detach | --detache | --détaché) DETACH=1 ;;
    --max) MAX="${2:-0}"; shift ;;
    --concurrence | --concurrency) CONCURRENCE="${2:-1}"; CONCURRENCE_EXPLICITE=1; shift ;;
    --budget) BUDGET="${2:-}"; shift ;;
    --timeout) TIMEOUT_BRUT="${2:-}"; shift ;;
    --modele | --model) MODELE="${2:-claude-opus-5}"; shift ;;
    --effort) EFFORT="${2:-xhigh}"; shift ;;
    --plan) PLAN_IMPOSE="${2:-}"; shift ;;
    --sans-merge) MERGE=0 ;;
    --sans-mrfix) MRFIX=0 ;;
    # La valeur est FACULTATIVE (« --resume » seul = le run reprenable le plus récent) : on ne
    # consomme l'argument suivant que s'il n'est pas lui-même une option, sans quoi
    # « --resume --detach » avalerait le mode de lancement.
    --resume | --reprendre)
      REPRISE=1
      case "${2:-}" in
        '' | -*) ;;
        *) REPRISE_ID="$2"; REPRISE_AVEC_VALEUR=1; shift ;;
      esac
      ;;
    --milestone) MILESTONE="${2:-}"; shift ;;
    --run-id) RUN_ID="${2:-}"; shift ;;
    # Un run en tue d'autres par défaut (#213) : ces deux options sont les seules façons d'en
    # sortir — ne rien tuer, ou ne faire que ça.
    --sans-kill | --no-kill) SANS_KILL=1 ;;
    --tuer-les-runs | --kill-runs) TUER_SEUL=1 ;;
    --max-reprises) MAESTRO_ORCHESTRATE_MAX_REPRISES="${2:-3}"; shift ;;
    --verbeux | --verbose) VERBEUX=1 ;;
    # Diagnostic de la détection de limite d'usage sur une sortie de session capturée : c'est ce qui
    # rend la reprise vérifiable sans attendre de vraiment taper la limite.
    --test-reprise) TEST_REPRISE="${2:-}"; shift ;;
    # Même esprit : relire à l'œil un résultat de session déjà capturé, sans rien lancer (#180).
    --resultat | --résultat) LIRE_RESULTAT="${2:-}"; shift ;;
    -h | --help) usage; exit 0 ;;
    *) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# L'effort est un ENSEMBLE FERMÉ de cinq niveaux, là où un nom de modèle est une chaîne ouverte
# (d'où l'absence de contrôle équivalent sur `--modele`) : une faute de frappe se voit donc, et il
# vaut mieux la voir ici qu'au premier ticket. Le CLI refuserait la valeur à CHAQUE session, et le
# run brûlerait son plan en échecs identiques avant que personne ne lise la cause.
case "$EFFORT" in
  low | medium | high | xhigh | max) ;;
  *)
    printf 'run.sh : effort inconnu « %s » — attendu low, medium, high, xhigh ou max.\n' "$EFFORT" >&2
    exit 2
    ;;
esac

# Le plafond de dépense ne devient une option de session que s'il a été DEMANDÉ (#286) : c'est
# `OPT_BUDGET` — vide par défaut — qui part au CLI, jamais `--max-budget-usd ""`. `0` y vaut « pas
# de plafond », seule façon d'annuler une variable d'environnement déjà posée, et le repli évite
# surtout qu'un `--max-budget-usd 0` parte tuer chaque session avant son premier outil. Un montant
# illisible est refusé ici pour la même raison que l'effort juste au-dessus : le CLI le refuserait à
# CHAQUE session et le run brûlerait son plan en échecs jumeaux.
case "$BUDGET" in
  '' | 0 | 0.0 | 0.00) BUDGET='' ;;
  *[!0-9.]*)
    printf 'run.sh : budget invalide « %s » — attendu un montant en dollars (ex. 20), ou 0 pour aucun plafond.\n' "$BUDGET" >&2
    exit 2
    ;;
esac
OPT_BUDGET=()
[ -n "$BUDGET" ] && OPT_BUDGET=(--max-budget-usd "$BUDGET")

# La concurrence est refusée ici pour la même raison que l'effort et le budget : un réglage illisible
# ne doit pas se découvrir au premier ticket. `0` n'y vaut PAS « pas de limite » — contrairement au
# budget, dont il annule un plafond, il désignerait ici zéro créneau, donc un run qui ne lance rien.
case "$CONCURRENCE" in
  '') CONCURRENCE=1 ;;
  *[!0-9]* | 0)
    printf 'run.sh : concurrence invalide « %s » — attendu un entier ≥ 1 (1 = run séquentiel).\n' \
      "$CONCURRENCE" >&2
    exit 2
    ;;
esac

# `--detach` avec `--dry-run` n'aurait rien à détacher : le plan s'affiche en une seconde, et une
# console qui se refermerait aussitôt ne le montrerait à personne. On reste en direct, en lecture
# seule — et sans laisser de répertoire de run derrière soi.
if [ "$DETACH" = 1 ] && [ "$DRY" = 1 ]; then
  printf 'run.sh : --detach sans effet avec --dry-run — le plan s'\''affiche ici, rien n'\''est lancé.\n' >&2
  DETACH=0
fi

# `--detach` fait passer la sortie par `tee` : stdout n'est plus un terminal, et le run détaché
# perdrait ses couleurs alors qu'il s'affiche bel et bien dans une fenêtre. Le lanceur pose donc ce
# marqueur — et décolore le journal en fin de run, les codes n'ayant de sens que devant un écran.
if [ -t 1 ] || [ "${MAESTRO_ORCHESTRATE_COULEUR:-0}" = 1 ]; then
  C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_B=$'\033[1m'; C_D=$'\033[2m'; C_0=$'\033[0m'
else
  C_G=''; C_Y=''; C_R=''; C_B=''; C_D=''; C_0=''
fi

# --- Utilitaires ------------------------------------------------------------------------------------
# secondes <durée> : « 45m » -> 2700, « 2h » -> 7200, « 900 » -> 900. Un format inconnu vaut mieux
# refusé tout de suite qu'interprété de travers — un timeout faux tue des sessions valides.
secondes() {
  local d="$1"
  case "$d" in
    *[0-9]s) printf '%s' "${d%s}" ;;
    *[0-9]m) printf '%s' "$(( ${d%m} * 60 ))" ;;
    *[0-9]h) printf '%s' "$(( ${d%h} * 3600 ))" ;;
    *[!0-9]*) return 1 ;;
    *) printf '%s' "$d" ;;
  esac
}

# Les attentes de reprise se comptent en heures : au-delà, « 1501min59 » ne se lit plus.
duree_lisible() {
  local s="$1"
  if [ "$s" -lt 60 ]; then printf '%ds' "$s"
  elif [ "$s" -lt 3600 ]; then printf '%dmin%02d' $((s / 60)) $((s % 60))
  else printf '%dh%02d' $((s / 3600)) $(((s % 3600) / 60)); fi
}

# arrondi_cout <valeur> : le coût, à deux décimales. `total_cost_usd` sort du CLI en flottant brut
# (« 10.686978499999995 ») : les quinze chiffres n'apprennent rien de plus que les deux premiers et
# débordent de toutes les colonnes. `LC_ALL=C` n'est pas décoratif — sous une locale française,
# printf rendrait « 10,69 », que `status.sh` additionne ensuite en awk (et lirait 10).
arrondi_cout() {
  local v="${1:-0}"
  [ -n "$v" ] || v=0
  # Une valeur qui n'est pas un nombre (champ absent, « ? ») est rendue telle quelle plutôt que
  # transformée en 0,00 : mieux vaut un affichage bizarre qu'un coût inventé.
  case "$v" in
    *[!0-9.eE+-]*) printf '%s' "$v"; return 0 ;;
  esac
  LC_ALL=C printf '%.2f' "$v" 2>/dev/null || printf '%s' "$v"
}

# --- La vue vivante d'un run (#240) -------------------------------------------------------------------
# Ce que la console d'un run doit montrer, c'est l'AVANCEMENT DU PLAN — pas la trace des appels
# d'outils. Le flot d'une ligne par `tool_use` (#176) avait sorti la console du mutisme, mais il
# défile trop vite pour être lu, et un nom d'outil sans son résultat n'apprend rien : il a remplacé
# « on ne sait rien » par « on ne voit rien ». On garde donc UNE ligne d'action — la dernière,
# réécrite sur place — et on rend, autour, la checklist du plan.
#
# --- Deux sorties, pour que `run.log` reste lisible ---------------------------------------------------
# Piège central : la sortie d'un run N'EST PAS un terminal. Le lanceur de `--detach` fait
# « … 2>&1 | tee -a run.log » — stdout est un TUBE (c'est déjà toute la raison d'être de
# MAESTRO_ORCHESTRATE_COULEUR, juste au-dessus). Redessiner sur stdout déverserait dans `run.log` une
# frame par rafraîchissement, que le `sed` final ne nettoierait même pas : il ne retire que les
# séquences SGR « …m », pas les déplacements de curseur.
#
# D'où deux chemins, et un seul écrivain à la fois :
#   · stdout    la trace permanente — en-tête de ticket, battements, verdicts. Va dans `run.log`.
#   · $VUE_FD   les frames redessinées, vers la CONSOLE seule. Jamais dans `run.log`.
# Le lanceur ouvre ce descripteur AVANT le tube (`exec 4>&1` : la fenêtre) et le passe par
# MAESTRO_ORCHESTRATE_CONSOLE_FD ; hors détachement, un stdout de terminal fait l'affaire. Sans
# console (détachement Unix, CI, tests), aucune frame n'est émise : la vue retombe en plein texte,
# une impression par changement d'état. Le même mécanisme la rend TESTABLE sans pseudo-terminal —
# il suffit d'ouvrir ce descripteur sur un fichier et d'y relire les frames.
#
# --- Le chrono demande une horloge, pas un événement --------------------------------------------------
# La boucle est bloquée sur la lecture du flux de la session : rien ne peut y faire avancer un
# compteur. C'est `read -t` qui sert d'horloge — un tour toutes les 0,2 s, qu'une ligne soit arrivée
# ou non. Piège à connaître : sur expiration, bash AFFECTE quand même ce qu'il a déjà lu de la ligne
# en cours. D'où le tampon `partiel` de `formate_flux`, sans lequel un objet JSON coupé par une
# expiration serait écrit en DEUX lignes dans `<iid>.jsonl` — le fichier dont dépendent le coût, le
# verdict et la détection de limite d'usage.
#
# --- Un bloc qui tient en place, et rien d'autre à l'écran (#284) -------------------------------------
# Trois choses faisaient de la console détachée un mur défilant plutôt qu'un tableau de bord, et
# chacune se corrige ici :
#
#  1. LA FRAME NE SE TERMINE PLUS PAR UN SAUT DE LIGNE. Un `\n` écrit sur la DERNIÈRE rangée de la
#     fenêtre fait défiler le tampon d'une ligne — et le bloc vit précisément en bas de l'écran. À
#     cinq images par seconde, c'était cinq lignes par seconde poussées dans l'historique : l'écran
#     paraissait stable, l'ascenseur se remplissait de copies du même bloc. Le curseur reste donc
#     SUR la dernière ligne du bloc, et le repositionnement vaut « hauteur - 1 ».
#  2. LE CURSEUR EST CACHÉ tant que la vue tient l'écran. Redessiner, c'est le faire sauter d'un
#     bout à l'autre du bloc plusieurs fois par seconde : c'est ce mouvement, plus que le texte, qui
#     donnait à la console son air agité.
#  3. LE BATTEMENT NE S'IMPRIME PLUS À L'ÉCRAN. Il est fait pour `run.log`, où il est la seule trace
#     d'une session qui dure — mais à l'écran il ajoutait une ligne par minute SOUS un bloc qui dit
#     déjà la même chose en plus frais, et forçait un redessin « à neuf » qui laissait le bloc
#     précédent derrière lui. Il part maintenant vers le journal seul (`trace_journal`).
#
# Et le redessin ne se fait plus qu'une fois par seconde : rien de ce que la frame montre ne change
# plus vite que ça (le chrono compte les secondes), et chaque frame coûte une poignée de forks.
#
# --- Un repère qui se recalcule, au lieu de se cumuler (#325) ------------------------------------------
# Ce qui précède laissait un défaut de conception : le bloc était repositionné À PARTIR DU CURSEUR,
# de « hauteur - 1 » rangées vers le haut. Le repère était donc CUMULATIF — juste tant que rien
# n'ajoute une rangée qu'on n'a pas comptée, et faux pour toujours dès qu'une l'ajoute. Une seule
# ligne repliée suffit : la frame suivante remonte trop peu, laisse la première ligne du bloc
# derrière elle, et recommence à chaque redessin. Constaté en production sur le run du 2026-08-10,
# où le ticket courant s'affichait TROIS fois — deux décalages, jamais rattrapés.
#
# Trois changements, du plus profond au plus superficiel :
#
#  1. LE REPÈRE EST ANCRÉ SUR LE BAS DE LA FENÊTRE dès qu'on sait que le bloc y touche (`vue_ancre` :
#     « ESC[999B » puis la remontée). Le déplacement vers le bas est BORNÉ PAR LE TERMINAL, donc la
#     position ne dépend plus d'aucun compte à nous et se recalcule à neuf à chaque frame. Une
#     désynchronisation coûte au plus une frame, là où elle coûtait une copie par seconde. Passé le
#     premier écran, c'est le régime de tout le reste du run.
#  2. LA TAILLE DE LA CONSOLE EST RELUE en cours de run (`vue_mesure`, toutes les VUE_MESURE_S
#     secondes) et non figée à l'ouverture. La figer, c'était parier qu'une fenêtre ne change pas de
#     taille en cinq heures — et une largeur périmée fabrique précisément le repli du point 1.
#  3. LA LARGEUR SE MESURE EN COLONNES AFFICHÉES (`colonnes`), et non en `${#s}`, qui compte des
#     octets sous une locale C et des caractères sous UTF-8 : le bloc n'a pas la même largeur d'un
#     poste à l'autre, et c'est encore le même repli qui en sort.
SPIN='|/-\'                       # ASCII à dessein : la console Windows par défaut (conhost +
                                  # Consolas) n'a pas les glyphes braille des jolis rouets.
VUE_FD=""                         # descripteur des frames ; vide = pas de vue vivante
VUE_CURSEUR=0                     # 1 = curseur caché par nous, donc à rendre en sortant
VUE_LARGEUR=100                   # colonnes de la console, relues en cours de run (#325)
VUE_HAUTEUR=40                    # rangées, idem : un bloc plus haut qu'elles ferait défiler (#290)
VUE_MESURE_S="${MAESTRO_ORCHESTRATE_MESURE:-2}"   # période de RELECTURE de la taille, en secondes
case "$VUE_MESURE_S" in '' | *[!0-9]*) VUE_MESURE_S=2 ;; esac
VUE_MESURE_A=-1                   # `SECONDS` de la dernière mesure
VUE_TICK=0.2                      # période de LECTURE du flux, en secondes
VUE_BATTEMENT_S="${MAESTRO_ORCHESTRATE_BATTEMENT:-60}"   # trace de journal pendant une session
VUE_GABARIT=43                    # largeur du gabarit ASCII de `vue_ligne`, titre exclu
VUE_SPIN=0                        # position dans SPIN — avancée par le pilote, à chaque frame
# Où est le curseur dans la fenêtre, en BORNE INFÉRIEURE (#325) : on ne sait pas ce que la console
# portait avant nous, ni ce que `tee` y a écrit avant la première frame, donc on part de la rangée 1
# et on ne compte que nos propres déplacements. Sous-estimer est sans conséquence — on reste
# simplement un peu plus longtemps dans le régime relatif ; sur-estimer, non, d'où le choix de ne
# jamais compter une rangée qu'on n'est pas sûr d'avoir consommée. Dès que la borne atteint la
# hauteur de la fenêtre, le curseur y est vraiment : le bloc touche le bas, et c'est là que
# `vue_ancre` prend le relais.
VUE_ROW=1
# Le chrono affiché est celui du TICKET, pas de la tentative en cours. Une limite d'usage rend la
# main puis relance une session : son processus repart à zéro, alors que le ticket, lui, dure depuis
# le début — c'est la durée qu'on suit d'un bout à l'autre, et celle que le verdict consignera.
VUE_DEBUT_TICKET=$SECONDS
# L'état de reprise, affiché comme action tant que la session rouverte n'a rien fait d'autre : sans
# lui, la vue d'un ticket repris est indiscernable de celle d'un ticket qui démarre.
VUE_REPRISE=""

# Le journal du run, quand le lanceur nous en a ouvert un descripteur (#284). Sans lui, la trace
# permanente n'a qu'un chemin — stdout, donc `tee`, donc la CONSOLE. C'est ce détour qu'il évite
# pour les lignes qui n'ont rien à faire à l'écran, et il donne au passage à celles qui doivent y
# être un ÉCRIVAIN UNIQUE : `tee` est un autre processus, et rien ne garantit qu'il écrira sa ligne
# avant la frame qu'on dessine juste après — une frame arrivée trop tôt compte ses lignes depuis le
# mauvais endroit, et le bloc se dédouble.
TRACE_FD="${MAESTRO_ORCHESTRATE_TRACE_FD:-}"
if [ -n "$TRACE_FD" ] && ! { : >&"$TRACE_FD"; } 2>/dev/null; then TRACE_FD=""; fi

vue_active() { [ -n "$VUE_FD" ]; }

# `VUE_ROW` suit nos déplacements et RIEN d'autre. Il sature à la hauteur de la fenêtre : une fois
# le bas atteint, tout ce qu'on écrit fait défiler et le curseur y reste — c'est le seul état dont
# on ait besoin d'être certain.
vue_avance() { VUE_ROW=$((VUE_ROW + ${1:-0})); [ "$VUE_ROW" -gt "$VUE_HAUTEUR" ] && VUE_ROW="$VUE_HAUTEUR"; return 0; }
vue_recule() { VUE_ROW=$((VUE_ROW - ${1:-0})); [ "$VUE_ROW" -lt 1 ] && VUE_ROW=1; return 0; }
vue_colle_au_bas() { [ "$VUE_ROW" -ge "$VUE_HAUTEUR" ]; }

# vue_ancre <hauteur> : pose le curseur sur la PREMIÈRE rangée d'un bloc de <hauteur> rangées collé
# au bas de la fenêtre — et c'est le cœur du correctif de #325.
#
# « ESC[999B » descend jusqu'à la dernière rangée : le déplacement est BORNÉ PAR LE TERMINAL, donc
# la position obtenue ne dépend d'aucune mesure de notre part. C'est ce qui distingue ce repère de
# `vue_remonte`, qui compte à partir de là où le curseur a été laissé : une seule rangée gagnée en
# route — une ligne repliée parce que la fenêtre a rétréci, une écriture qu'on n'a pas vue — et
# toutes les frames suivantes remontent trop peu, abandonnant une copie de la première ligne du bloc
# à CHAQUE redessin. Ancré sur le bas, le repère se recalcule à neuf : une désynchronisation coûte
# au plus une frame, jamais une copie de plus par seconde.
#
# Le prix est de savoir que le bloc touche VRAIMENT le bas (`vue_colle_au_bas`) : sinon on le
# collerait à la fenêtre en laissant un trou sous le journal. C'est le seul rôle de `VUE_ROW`, et
# une hauteur mal lue par `tput` n'y fait courir qu'un trou passager — jamais un bloc posé au milieu
# de l'écran, ce qu'une position calculée à partir de `VUE_HAUTEUR` aurait produit.
vue_ancre() {
  if [ "${1:-0}" -gt 1 ]; then printf '\033[999B\033[%sF' "$(($1 - 1))"; else printf '\033[999B\r'; fi
}

# De qui parle une ligne (#289). À plusieurs tickets en vol, les sous-shells écrivent tous dans le
# même journal et rien ne dirait à qui appartient un « ✓ PR #99 ouverte » : chaque ligne de ticket
# porte donc son numéro. À un seul en vol — le défaut — le préfixe est VIDE, et la sortie est celle
# d'avant ce lot à l'octet près. Posé par le pilote autour de chaque ticket, et hérité par son
# sous-shell ; les trois fonctions ci-dessous sont les seules à l'appliquer.
PREFIXE_TICKET=""

# trace_journal <format> [args…] : une ligne pour le JOURNAL SEUL — l'écran l'a déjà, en mieux.
# Cas d'usage : le battement. Repli sur stdout quand aucun fd dédié n'existe, SAUF si stdout est
# lui-même l'écran (run.sh lancé à la main dans un terminal), où le bloc vivant la rend redondante.
trace_journal() {
  local ligne; printf -v ligne "$@"; ligne="$PREFIXE_TICKET$ligne"
  if [ -n "$TRACE_FD" ]; then
    printf '%s' "$ligne" >&"$TRACE_FD"
  elif ! vue_active || [ "$VUE_FD" != 1 ]; then
    printf '%s' "$ligne"
  fi
  return 0
}

# trace <format> [args…] : une ligne PERMANENTE imprimée alors que le bloc vivant tient l'écran.
# Le bloc est retiré d'abord, puis la ligne est écrite PAR NOUS sur la console (jamais par `tee`,
# cf. TRACE_FD) et, séparément, dans le journal. À réserver aux endroits où une frame suit de près :
# ailleurs, `dit` suffit et garde la ligne dans `run.log` par le chemin habituel.
trace() {
  local ligne; printf -v ligne "$@"; ligne="$PREFIXE_TICKET$ligne"
  vue_efface
  if vue_active && [ "$VUE_FD" != 1 ]; then printf '%s' "$ligne" >&"$VUE_FD"; fi
  if [ -n "$TRACE_FD" ]; then printf '%s' "$ligne" >&"$TRACE_FD"; else printf '%s' "$ligne"; fi
  # Ce que la ligne a fait descendre le curseur, compté sur ses seuls sauts de ligne (#325) : une
  # ligne plus large que la console en consomme une de plus, mais l'ignorer SOUS-estime, et c'est le
  # sens que `VUE_ROW` doit garder. Fork-free — `trace` est sur le chemin de chaque ligne permanente.
  local sauts="${ligne//[!$'\n']/}"
  vue_avance "${#sauts}"
  return 0
}

# dit <format> [args…] : la ligne permanente ordinaire d'un ticket — un `printf` qui sait de quel
# ticket il parle. À réserver à ce qui appartient à UN ticket ; ce qui appartient au run (résumé,
# ménages) reste un `printf` nu, aucun numéro n'ayant de sens devant.
#
# Tant qu'une vue tient l'écran, la ligne n'y va PAS directement : elle est mise en FILE (#290). Le
# chemin d'avant — stdout, donc `tee`, donc la console — est celui qui dédouble le bloc, et il le
# ferait désormais depuis N sous-shells à la fois. Le pilote la reprend dans `vue_purge` et l'écrit
# lui-même, ce qui lui rend l'écrivain unique de #284. Sans vue, rien à protéger : on imprime.
dit() {
  local ligne; printf -v ligne "$@"; ligne="$PREFIXE_TICKET$ligne"
  if vue_active; then
    printf '%s' "$ligne" >>"$RUN_DIR/.console"
  else
    printf '%s' "$ligne"
  fi
  return 0
}

# vue_purge : vide la file dans l'écran et dans le journal, une ligne à la fois. Appelée par le
# PILOTE seul, entre deux frames.
#
# Le descripteur de lecture reste OUVERT d'un appel à l'autre (`VUE_FILE_FD`) : le rouvrir ferait
# relire depuis le début, et le suivre par `tail` demanderait un processus de plus à surveiller. Sur
# un fichier ordinaire, une lecture arrivée au bout rend 1 sans consommer ; les lignes ajoutées
# depuis sont lues au passage suivant. Le tampon `partiel` couvre le seul cas où une ligne serait
# lue à moitié — même piège que `formate_flux`, et `read` affecte ce qu'il a lu avant d'échouer.
VUE_FILE_FD=""
VUE_FILE_PARTIEL=""
vue_purge() {
  vue_active || return 0
  if [ -z "$VUE_FILE_FD" ]; then
    : >>"$RUN_DIR/.console" || return 0
    exec 8<"$RUN_DIR/.console" || return 0
    VUE_FILE_FD=8
  fi
  # Le préfixe est déjà DANS la ligne — `dit` l'y a mis avant de la mettre en file. Le neutraliser
  # ici est ce qui évite un « #290 #290 … » que rien d'autre ne rattraperait.
  local ligne PREFIXE_TICKET=""
  while IFS= read -r ligne <&8; do
    trace '%s\n' "$VUE_FILE_PARTIEL$ligne"
    VUE_FILE_PARTIEL=""
  done
  # Ce que `read` a lu sans trouver de fin de ligne : gardé pour le prochain passage.
  VUE_FILE_PARTIEL="$VUE_FILE_PARTIEL$ligne"
  return 0
}

# vue_ouvre : choisit le descripteur des frames. Le mode verbeux n'en veut aucun — les deux se
# disputeraient l'écran, et c'est justement quand on lit chaque ligne qu'on ne veut rien qui bouge.
#
# --- Un seul dessinateur, N tickets dessinés (#290) ---------------------------------------------------
# #289 avait éteint la vue au-delà d'un ticket en vol, et son diagnostic était juste : le bloc était
# dessiné DEPUIS LE SOUS-SHELL de la session, et sa hauteur vivait dans un fichier que N sous-shells
# auraient réécrit l'un sur l'autre — pas une vue dégradée, un écran corrompu, chaque frame comptant
# ses lignes depuis le mauvais endroit.
#
# Ce lot ne partage donc pas l'écran entre N écrivains : il le retire à tous sauf un. Le PILOTE
# dessine — c'est le seul processus qui sache combien de tickets sont en vol et où ils en sont —, et
# une session ne fait plus que PUBLIER son état dans `<iid>.vue`. Trois conséquences, toutes des
# simplifications :
#   · la hauteur redevient une VARIABLE : un seul processus l'écrit et la lit, le fichier disparaît ;
#   · les lignes permanentes d'une session ne passent plus par `tee` (qui les faisait arriver au
#     milieu d'une frame) mais par la file de `dit`, que le pilote vide entre deux frames ;
#   · le paramètre `frais` de `vue_dessine` disparaît — plus personne n'écrit sous le bloc.
#
# Ce qui reste vrai à un ticket en vol l'est à N : à `--concurrence 1`, le bloc a exactement la même
# forme qu'avant ce lot, à ceci près que c'est le pilote qui le dessine.
vue_ouvre() {
  VUE_FD=""
  if [ "$VERBEUX" != 1 ]; then
    local fd="${MAESTRO_ORCHESTRATE_CONSOLE_FD:-}"
    # Couture de test : MAESTRO_ORCHESTRATE_CONSOLE désigne un FICHIER qui tient lieu de console.
    # C'est ce qui rend les frames vérifiables sans pseudo-terminal — on les relit, tout simplement.
    if [ -n "${MAESTRO_ORCHESTRATE_CONSOLE:-}" ] && exec 9>>"$MAESTRO_ORCHESTRATE_CONSOLE"; then
      VUE_FD=9
    elif [ -n "$fd" ] && { : >&"$fd"; } 2>/dev/null; then
      VUE_FD="$fd"
    elif [ -t 1 ]; then
      VUE_FD=1
    fi
  fi
  # Sans console, personne ne regarde en direct : un tour toutes les 2 s suffit — et sous MSYS, un
  # fork de moins par seconde n'est pas un détail.
  vue_active || VUE_TICK=2

  vue_mesure || true
  VUE_MESURE_A="$SECONDS"

  # Le curseur n'a rien à montrer sous un bloc qu'on réécrit : il ne fait que sauter. On le cache
  # tant que la vue tient l'écran — et `vue_ferme`, câblé sur la sortie du script, le rend. Une
  # console laissée ouverte après le run ne doit pas rester sans curseur.
  if vue_active; then printf '\033[?25l' >&"$VUE_FD"; VUE_CURSEUR=1; fi
  return 0
}

# vue_mesure : (re)lit la taille de la console. Rend 0 quand elle a CHANGÉ depuis la mesure d'avant.
#
# La HAUTEUR compte autant que la largeur (#290) : à N tickets en vol, le bloc gagne une ligne
# d'action par ticket, et un bloc plus haut que la fenêtre ferait défiler l'écran — le défaut que
# #284 avait supprimé par l'autre bout ; `vue_dessine` s'y tient en masquant des lignes.
#
# Elle est appelée à l'ouverture PUIS toutes les VUE_MESURE_S secondes (#325). La mesurer une fois
# pour toutes, c'était parier qu'une fenêtre ne change pas de taille en cinq heures : une largeur
# périmée fait replier des lignes que le calcul des colonnes croit sûres, et un repli est exactement
# ce qui décale le bloc d'une rangée. Deux forks toutes les deux secondes, à comparer aux dizaines
# que coûte déjà une frame.
vue_mesure() {
  local n avant_l="$VUE_LARGEUR" avant_h="$VUE_HAUTEUR"

  n="${MAESTRO_ORCHESTRATE_LARGEUR:-}"
  case "$n" in '' | *[!0-9]*) n="$(tput cols 2>/dev/null)" || n='' ;; esac
  case "$n" in '' | *[!0-9]*) n=100 ;; esac
  [ "$n" -lt 60 ] && n=100
  VUE_LARGEUR="$n"

  n="${MAESTRO_ORCHESTRATE_HAUTEUR:-}"
  case "$n" in '' | *[!0-9]*) n="$(tput lines 2>/dev/null)" || n='' ;; esac
  case "$n" in '' | *[!0-9]*) n=40 ;; esac
  [ "$n" -lt 10 ] && n=40
  VUE_HAUTEUR="$n"

  # Le curseur ne peut pas être plus bas que la fenêtre. Rétrécie, elle a fait défiler son contenu et
  # le curseur est sur sa dernière rangée ; agrandie, elle a ajouté des rangées SOUS lui. Ramener la
  # borne à la nouvelle hauteur reste juste dans les deux cas.
  [ "$VUE_ROW" -gt "$VUE_HAUTEUR" ] && VUE_ROW="$VUE_HAUTEUR"

  [ "$VUE_LARGEUR" = "$avant_l" ] && [ "$VUE_HAUTEUR" = "$avant_h" ] && return 1
  return 0
}

# vue_ferme : rend le curseur. Idempotent, et appelé aussi bien à la fin de la boucle que par le
# trap de sortie — un Ctrl-C ou un `exit` d'erreur ne doit pas laisser la console amputée.
# Pas de « 2>/dev/null » ici : la garde ci-dessus suffit — VUE_CURSEUR ne vaut 1 que si `vue_ouvre`
# a bel et bien écrit sur ce descripteur —, et une seconde redirection se disputerait stderr avec
# celle du descripteur quand VUE_FD vaut 2 (SC2261).
vue_ferme() {
  [ "$VUE_CURSEUR" = 1 ] || return 0
  VUE_CURSEUR=0
  printf '\033[?25h' >&"$VUE_FD"
  return 0
}

# vue_ligne <marqueur> <rang> <iid> <durée> <coût> <mr> <titre> : une ligne de checklist.
# Tout ce qui est à largeur fixe est ASCII et passe AVANT le titre — `printf` compte en OCTETS, et un
# « %-40s » sur un titre accentué décalerait toute la colonne suivante. Le titre, lui, est tronqué
# à la construction : une ligne plus large que la console serait repliée par le terminal, et le
# redessin suivant remonterait d'une ligne de trop — le bloc se dédoublerait à chaque frame.
vue_ligne() {
  printf '  %s %2s. #%-5s %8s %9s %-8s %s' \
    "$1" "$2" "$3" "$4" "$5" "$6" "$(tronque "$7" $((VUE_LARGEUR - VUE_GABARIT - 3)))"
}

# La hauteur de la dernière frame est une simple VARIABLE (#290). Elle a vécu dans un fichier tant
# que la frame était dessinée depuis le sous-shell de la session, dont les affectations sont perdues
# au retour — or c'est le pilote qui doit effacer le bloc avant d'imprimer un verdict. Depuis que le
# pilote est le seul à dessiner, l'écrivain et le lecteur sont le même processus : le fichier n'a
# plus d'objet, et avec lui partent deux accès disque par frame.
VUE_HAUT=0

# vue_remonte <hauteur> : la séquence qui ramène le curseur au HAUT du bloc. Le curseur est laissé
# sur la DERNIÈRE ligne du bloc (la frame ne se termine pas par un saut de ligne, cf. plus haut),
# donc on remonte de « hauteur - 1 ». Un bloc d'une seule ligne se contente d'un retour chariot :
# « ESC[0F » vaut « ESC[1F » pour la plupart des terminaux, qui remonteraient d'une ligne de trop.
#
# Repère RELATIF, donc cumulatif : il compte à partir de là où le curseur a été laissé, et une
# rangée gagnée en route ne se rattrape jamais (#325). Il ne sert plus que tant qu'on ignore où l'on
# est dans la fenêtre ; passé le premier écran, c'est `vue_ancre` qui pose le repère.
vue_remonte() {
  if [ "${1:-0}" -gt 1 ]; then printf '\033[%sF' "$(($1 - 1))"; else printf '\r'; fi
}

# vue_efface : retire le bloc de l'écran. À appeler avant toute impression permanente, sans quoi la
# ligne atterrirait au milieu d'une frame et fausserait le compte de lignes des suivantes.
# Le repère est celui de `vue_dessine` — ancré sur le bas quand le bloc y est, relatif sinon : les
# deux doivent viser la même rangée, sans quoi l'effacement mordrait sur le journal ou laisserait
# une tranche du bloc derrière lui.
vue_efface() {
  vue_active || return 0
  [ "$VUE_HAUT" -gt 0 ] || return 0
  local tete
  if vue_colle_au_bas; then tete="$(vue_ancre "$VUE_HAUT")"; else tete="$(vue_remonte "$VUE_HAUT")"; fi
  printf '%s\033[J' "$tete" >&"$VUE_FD"
  vue_recule $((VUE_HAUT - 1))
  VUE_HAUT=0
  return 0
}

# --- L'état vivant d'un ticket, publié par sa session (#290) ------------------------------------------
# Une session ne dessine plus : elle écrit UNE LIGNE dans `<iid>.vue` — « <marqueur><TAB><action> » —
# et le pilote la relit à chaque frame. C'est tout le contrat, et il tient dans un fichier PAR TICKET
# précisément pour qu'aucun sous-shell n'ait à s'accorder avec un autre.
#
# Le marqueur n'est jamais vide (« . » = ordinaire, le pilote y met son rouet ; « = » = en pause).
# Un champ vide en tête serait FUSIONNÉ par `read` avec le suivant — le tab est un blanc IFS —, et
# l'action se lirait alors comme un marqueur. Même piège que celui documenté dans `status.sh`.
#
# Le CHRONO n'y est délibérément pas : le pilote le connaît mieux que la session (`P_DEBUT`), et il
# vaut pour le TICKET, donc à travers ses reprises — une valeur publiée par une session repartie de
# zéro le ferait reculer à chaque limite d'usage, ce que #240 s'était donné pour but d'éviter.
vue_publie() { # <iid> <marqueur> <action>
  printf '%s\t%s\n' "$2" "$3" >"$RUN_DIR/$1.vue" 2>/dev/null || true
  return 0
}

VUE_ETAT_MARQUE=""; VUE_ETAT_ACTION=""
vue_lit_etat() { # <iid> : pose VUE_ETAT_MARQUE / VUE_ETAT_ACTION. Par `read`, donc sans fork —
                 # la lecture a lieu une fois par ticket en vol et par frame.
  VUE_ETAT_MARQUE=""; VUE_ETAT_ACTION=""
  [ -r "$RUN_DIR/$1.vue" ] || return 0
  IFS=$'\t' read -r VUE_ETAT_MARQUE VUE_ETAT_ACTION <"$RUN_DIR/$1.vue" || true
  return 0
}

# vue_recompose : la partie STATIQUE du bloc — une ligne toute faite par entrée du plan. Les tickets
# EN VOL n'en ont pas : leur ligne se réécrit à chaque frame, c'est tout leur objet.
#
# Recalculée aux seuls moments où elle change — un ticket qui part, un ticket qui est soldé —, et non
# à chaque frame : c'est ce qui permet au redessin de ne forker que pour les lignes vivantes. Et
# `resume.tsv` est lu UNE FOIS par recomposition, là où #240 l'`awk`-ait une fois par ligne de plan :
# à N tickets en vol la recomposition arrive N fois plus souvent, et le compte de forks aussi.
declare -A VUE_BILAN=()
VUE_STATIQUE=()
vue_recompose() {
  local i iid verdict mr duree cout marque
  VUE_BILAN=()
  if [ -f "$RESUME" ]; then
    while IFS=$'\t' read -r iid verdict mr duree cout _; do
      case "$iid" in '#'* | '') continue ;; esac
      VUE_BILAN["$iid"]="$verdict|$mr|$duree|$cout"
    done <"$RESUME"
  fi

  VUE_STATIQUE=()
  for ((i = 0; i < NB_ENTREES; i++)); do
    iid="${P_IID[$i]}"
    verdict=''; mr=''; duree=''; cout=''
    # « | » et non un tab : `read` préserve les champs vides d'un séparateur qui n'est pas un blanc.
    [ -n "${VUE_BILAN[$iid]:-}" ] && IFS='|' read -r verdict mr duree cout <<<"${VUE_BILAN[$iid]}"
    case "${verdict:-}" in
      # Un ticket livré porte DEUX états depuis #419 — livré, puis mergé —, et le second se lit dans
      # le marqueur plutôt que dans une colonne de plus : `vue_ligne` est le seul champ que `printf`
      # ne padde pas, donc le seul où un glyphe non-ASCII ne décale pas la colonne suivante (le
      # gabarit compte des octets). Quatre états, un caractère : ✓ livré, PR en file · ⇈ mergée ·
      # ⚠ merge bloqué — le ticket reste livré, c'est sa PR qui attend un geste (le résumé la
      # nomme) · ⟳ une session /mr-fix est en train de la débloquer (#420).
      OK)
        case "${MERGE_ETAT[$iid]:-}" in
          mergee)    marque="$C_G⇈$C_0" ;;
          bloquee)   marque="$C_Y⚠$C_0" ;;
          deblocage) marque="$C_Y⟳$C_0" ;;
          *)         marque="$C_G✓$C_0" ;;
        esac ;;
      ECHEC) marque="$C_R✗$C_0" ;;
      SAUTE) marque="$C_Y~$C_0" ;;
      *)     marque=' '; mr=''; duree=''; cout='' ;;
    esac
    case "${duree:-}" in '' | *[!0-9]*) duree='' ;; *) duree="$(duree_lisible "$duree")" ;; esac
    case "${cout:-}" in '' | 0 | 0.00) cout='' ;; *) cout="$(arrondi_cout "$cout") \$" ;; esac
    case "${mr:-}" in '' | '-') mr='' ;; *) mr="PR #$mr" ;; esac
    VUE_STATIQUE[$i]="$(vue_ligne "$marque" "${P_RANG[$i]}" "$iid" "$duree" "$cout" "$mr" "${P_TITRE[$i]}")"
  done
  return 0
}

# vue_dessine : une frame — le plan dans son ordre, les tickets en vol en gras avec leur chrono et
# leur ligne d'action, le pied du run.
#
# Le bloc entier part en UN SEUL `printf` : deux écritures laisseraient voir un demi-bloc. Chaque
# ligne se termine par « ESC[K » (efface jusqu'au bout) pour qu'une ligne qui raccourcit ne laisse
# pas la traîne de la précédente ; la DERNIÈRE n'a pas de saut de ligne, un « \n » sur la rangée du
# bas faisant défiler le tampon (#284).
#
# Deux choses que #240 n'avait pas à traiter et que N tickets en vol imposent :
#   · la hauteur VARIE d'une frame à l'autre (un ticket qui se solde rend sa ligne d'action). Le bloc
#     se termine donc par « ESC[J », qui efface ce qu'un bloc plus haut avait laissé sous lui ;
#   · le bloc peut ne plus TENIR dans la fenêtre. Plutôt que de déborder — donc de faire défiler, donc
#     de se dédoubler à la frame suivante —, on masque des lignes déjà jouées et on le DIT.
vue_dessine() {
  vue_active || return 0
  local corps="" n=0 i iid marque ecoule fin=$'\033[K\n'
  local n_vol=0 a_masquer=0 reste_a_masquer=0 note=0

  for ((i = 0; i < NB_ENTREES; i++)); do
    [ "${P_ETAT[$i]}" = vol ] && n_vol=$((n_vol + 1))
  done

  # Le budget : une ligne par entrée du plan, une de plus par ticket en vol (son action), une pour le
  # pied. La ligne de note remplace celles qu'elle masque, d'où le « + 1 ».
  local besoin=$((NB_ENTREES + n_vol + 1)) budget=$((VUE_HAUTEUR - 1))
  if [ "$besoin" -gt "$budget" ]; then
    a_masquer=$((besoin - budget + 1))
    reste_a_masquer="$a_masquer"
  fi

  VUE_SPIN=$(((VUE_SPIN + 1) % ${#SPIN}))
  for ((i = 0; i < NB_ENTREES; i++)); do
    iid="${P_IID[$i]}"
    if [ "${P_ETAT[$i]}" != vol ] && [ "$reste_a_masquer" -gt 0 ]; then
      reste_a_masquer=$((reste_a_masquer - 1))
      if [ "$note" = 0 ]; then
        note=1
        # Bornée comme les autres (#325) : c'est la seule ligne du bloc dont le texte est fixe et
        # plus long que la console la plus étroite qu'on accepte (60 colonnes).
        corps+="$C_D$(tronque "$(printf '  … %s ligne(s) masquée(s) — la fenêtre est trop courte pour tout le plan' \
          "$a_masquer")" $((VUE_LARGEUR - 1)))$C_0$fin"
        n=$((n + 1))
      fi
      continue
    fi

    if [ "${P_ETAT[$i]}" = vol ]; then
      vue_lit_etat "$iid"
      # Une session en pause ne tourne pas : son marqueur est fixe, et c'est ce qui distingue à
      # l'œil une attente de limite d'usage d'une session qui travaille.
      marque="${SPIN:$VUE_SPIN:1}"
      [ "$VUE_ETAT_MARQUE" = '=' ] && marque='='
      ecoule=$((SECONDS - P_DEBUT[i]))
      corps+="$C_B$(vue_ligne "$marque" "${P_RANG[$i]}" "$iid" "$(duree_lisible "$ecoule")" '' '' \
        "${P_TITRE[$i]}")$C_0$fin"; n=$((n + 1))
      corps+="$C_D$(printf '       %s' \
        "${VUE_ETAT_ACTION:+· $(tronque "$VUE_ETAT_ACTION" $((VUE_LARGEUR - 11)))}")$C_0$fin"
      n=$((n + 1))
      continue
    fi

    corps+="${VUE_STATIQUE[$i]:-}$fin"; n=$((n + 1))
  done

  # Le pied ferme le bloc : « ESC[K » sans saut de ligne, le curseur reste sur cette ligne-là, puis
  # « ESC[J » nettoie ce qu'une frame plus haute aurait laissé. Il n'est pas borné par un `tronque` :
  # ses champs sont tous des compteurs, il plafonne à une soixantaine de colonnes — sous la console
  # la plus étroite qu'on accepte — et une coupe tomberait au milieu d'un code de couleur. C'est
  # l'invariant de largeur du bloc, vérifié sur TOUTES les lignes de TOUTES les frames, qui le tient.
  #
  # « reste » se compte sur ce qui n'est NI soldé NI en vol, et non sur la position du dernier ticket
  # lancé : à N en vol, les tickets ne se prennent plus dans l'ordre, et `nb_plan - POSITION` disait
  # la position d'un autre. Les sautés sont dans les compteurs, donc comptés — c'était déjà le cas,
  # et ça reste vrai maintenant que la soustraction porte sur eux.
  corps+="$(printf '  run %s%s · %s✓ %s%s · %s✗ %s%s · %s~ %s%s · reste %s' \
    "$(duree_lisible "$((SECONDS - RUN_DEBUT_S))")" \
    "$([ "$CONCURRENCE" -gt 1 ] && printf ' · %s en vol' "$n_vol")" \
    "$C_G" "$NB_OK" "$C_0" "$C_R" "$NB_ECHEC" "$C_0" "$C_Y" "$NB_SAUTE" "$C_0" \
    "$((nb_plan - NB_OK - NB_ECHEC - NB_SAUTE - n_vol))")"$'\033[K\033[J'; n=$((n + 1))

  # Où poser la première rangée (#325). Deux régimes, et c'est le premier qui porte le correctif :
  # dès que le bloc touche le bas de la fenêtre — l'état de tout un run passé le premier écran — on
  # s'y ancre par `vue_ancre`, dont le repère est fourni par le terminal et se recalcule à neuf à
  # chaque frame. Tant qu'on n'en est pas sûr, on remonte depuis le curseur comme avant.
  if vue_colle_au_bas; then
    printf '%s%s' "$(vue_ancre "$n")" "$corps" >&"$VUE_FD"
    VUE_ROW="$VUE_HAUTEUR"
  elif [ "$VUE_HAUT" -gt 0 ]; then
    printf '%s%s' "$(vue_remonte "$VUE_HAUT")" "$corps" >&"$VUE_FD"
    vue_recule $((VUE_HAUT - 1)); vue_avance $((n - 1))
  else
    printf '%s' "$corps" >&"$VUE_FD"
    vue_avance $((n - 1))
  fi
  VUE_HAUT="$n"
  return 0
}

# vue_texte : la même checklist, en PLEIN TEXTE et sans animation — sur stdout, donc dans `run.log`
# et partout où rien ne peut être redessiné (détachement Unix, CI, tests). Imprimée une fois par
# ticket : c'est elle qui porte l'avancement quand il n'y a pas de console. Les tickets en vol y
# portent le même « > » qu'avant ce lot ; il y en a simplement N.
vue_texte() {
  local i
  for ((i = 0; i < NB_ENTREES; i++)); do
    if [ "${P_ETAT[$i]}" = vol ]; then
      vue_ligne '>' "${P_RANG[$i]}" "${P_IID[$i]}" '' '' '' "${P_TITRE[$i]}"; printf '\n'
    else
      printf '%s\n' "${VUE_STATIQUE[$i]:-}"
    fi
  done
  return 0
}

# champ_json <fichier> <clé> : la valeur SCALAIRE d'une clé de premier niveau. Suffisant pour les
# champs qu'on lit ici (nombres, énumérés) ; on ne cherche pas à parser `result`, qui est de la
# prose et n'entre dans aucun verdict.
champ_json() {
  grep -o "\"$2\"[[:space:]]*:[[:space:]]*\(\"[^\"]*\"\|[^,}]*\)" "$1" 2>/dev/null |
    head -1 | sed 's/^[^:]*:[[:space:]]*//; s/^"//; s/"$//'
}

genere_uuid() {
  if command -v uuidgen >/dev/null 2>&1; then uuidgen | tr 'A-Z' 'a-z'; return 0; fi
  od -An -tx1 -N16 /dev/urandom 2>/dev/null | tr -d ' \n' | awk '
    { printf "%s-%s-4%s-a%s-%s\n", substr($0,1,8), substr($0,9,4), substr($0,14,3), substr($0,18,3), substr($0,21,12) }'
}

# uuid_du_ticket <iid> : l'UUID de session du ticket — généré une fois, puis RELU. C'est le fichier,
# et non un calcul, qui garantit la stabilité : la reprise après limite d'usage (#171) doit
# retrouver exactement la session interrompue, y compris depuis un autre processus.
uuid_du_ticket() {
  local f="$RUN_DIR/$1.session"
  [ -s "$f" ] || genere_uuid >"$f"
  cat "$f"
}

# reprend_en_vol <iid> : 0 si ce ticket est celui que le run REPRIS avait en main quand il a été
# coupé — témoin de session présent dans son journal, et aucune ligne de bilan à son nom.
#
# C'est la seule exception au filtre « À faire » de la boucle, et elle est étroite à dessein.
# Sans elle, une reprise laisse derrière elle la victime même de l'interruption : `/ticket-start` a
# posé « En cours » sur ce ticket, donc la relecture du cycle de vie l'écarte comme s'il appartenait
# à quelqu'un d'autre — alors que son worktree et son travail non commité nous attendent. Les autres
# états (« En revue », « Terminé », pris par une session voisine) restent sautés comme avant.
#
# La question est posée POUR CHAQUE TICKET du plan, jamais une seule fois pour le run (#291) : un run
# concurrent coupé en avait N en main, chacun avec son témoin de session et son uuid, et ils sont donc
# TOUS repris — c'est la concurrence relue du run repris (fichier `concurrence`) qui décide ensuite
# combien repartent ensemble. Le seul « En cours » qui reste sauté est celui que le run repris n'avait
# pas en main : celui-là est le ticket de quelqu'un d'autre.
reprend_en_vol() {
  [ "$REPRISE" = 1 ] || return 1
  [ -s "$REPRISE_DIR/$1.session" ] || return 1
  # Pas de bilan à son nom = la coupure l'a pris en vol. `!` sur l'awk : il sort 0 quand il TROUVE
  # la ligne, et un resume.tsv absent (run coupé très tôt) vaut « aucun verdict », pas une erreur.
  ! awk -F'\t' -v iid="$1" '$1 !~ /^#/ && $1 == iid { trouve = 1 } END { exit !trouve }' \
    "$REPRISE_DIR/resume.tsv" 2>/dev/null
}

# prepare_worktree <iid> <branche> <journal> : monte le worktree du ticket et IMPRIME SON CHEMIN.
# Le chemin est demandé à git plutôt que recalculé depuis la convention de nommage de worktree.sh —
# deux formules qui divergeraient se remarqueraient trop tard.
# MAESTRO_ORCHESTRATE_WORKTREE remplace toute l'étape par une commande qui reçoit « <iid> <branche> »
# et imprime un chemin : c'est la couture par laquelle les tests font tourner la boucle sans créer
# de vrai worktree ni de vraie branche (#172).
prepare_worktree() {
  if [ -n "${MAESTRO_ORCHESTRATE_WORKTREE:-}" ]; then
    "$MAESTRO_ORCHESTRATE_WORKTREE" "$1" "$2" 2>>"$3"
    return $?
  fi
  bash "$RACINE/scripts/git/worktree.sh" "$1" >>"$3" 2>&1 </dev/null || return 1
  git -C "$RACINE" worktree list --porcelain 2>/dev/null | awk -v cible="branch refs/heads/$2" '
    /^worktree / { w = substr($0, 10) }
    $0 == cible { print w; exit }'
}

arret_demande() {
  [ -f "$STOP" ] || return 1
  printf '\n%sArrêt demandé%s — le fichier %s est présent. Run interrompu proprement.\n' "$C_Y" "$C_0" "$STOP"
  return 0
}

# tue_les_runs_en_vol [<run-id à épargner>] : arrête tout run dont le pilote tourne encore, et dit
# lesquels. Rend le nombre de runs qu'il a fallu tuer (0 = personne, le cas courant).
#
# Muet quand il n'y a rien à tuer : c'est l'état normal, et une ligne « aucun run en cours » avant
# chaque run n'apprendrait rien à personne.
#
# Ce qui est tué l'est SANS SOMMATION, et c'est voulu. La sortie propre existe déjà — le fichier
# STOP — mais elle n'est lue qu'entre deux tickets : attendre qu'un run la voie, c'est attendre la
# fin de la session en cours, jusqu'à 45 minutes. Or on est ici parce que quelqu'un veut lancer
# maintenant. La brutalité se paie en travail non commité dans le worktree du ticket en vol ; elle
# ne se paie PAS en travail perdu, le journal du run tué restant intact et rejouable (`--resume`),
# ce que le rapport dit à chaque fois.
tue_les_runs_en_vol() {
  local exclu="${1:-}" id pid iid code n=0
  while IFS=$'\t' read -r id pid iid; do
    [ -n "${id:-}" ] || continue
    if [ "$n" -eq 0 ]; then
      printf '\n%sUn seul run à la fois%s — arrêt de ce qui tourne encore :\n' "$C_Y" "$C_0"
    fi
    n=$((n + 1))
    pilote_tue "$ORCH_DIR/$id"; code=$?
    case "$code" in
      # Les tickets en vol sont nommés TOUS (#291) : un run concurrent en interrompt N, et chacun
      # laisse son worktree derrière lui. La liste vient en virgules de `pilote_tickets_en_vol` ;
      # la substitution en remet le « # » devant chaque numéro.
      0) printf '  %s✗%s run %s (pid %s)%s — arrêté\n' "$C_Y" "$C_0" "$id" "$pid" \
           "$([ -n "${iid:-}" ] && printf ', ticket(s) #%s en vol' "${iid//,/, #}")" ;;
      1) printf '  = run %s (pid %s) — terminé de lui-même entre-temps\n' "$id" "$pid" ;;
      # Ni SIGKILL ni taskkill n'en sont venus à bout : le dire vaut mieux que laisser croire que la
      # place est nette. On démarre quand même — refuser bloquerait sur une cause que l'utilisateur
      # ne peut pas lever d'ici.
      *) printf '  %s⚠%s run %s (pid %s) — TOUJOURS VIVANT malgré l'\''arrêt, deux runs vont cohabiter\n' \
           "$C_R" "$C_0" "$id" "$pid" ;;
    esac
  done <<< "$(pilotes_vivants "$ORCH_DIR" "$exclu")"

  if [ "$n" -gt 0 ]; then
    printf '  Journaux intacts : ces runs restent reprenables (run.sh --resume <id>).\n'
    printf '  Ce qu'\''une session interrompue avait commencé dort dans son worktree — status.sh --run-id <id>.\n\n'
  fi
  return "$n"
}

# travail_en_attente <dest> : « <fichiers non commités> <commits hors origin/main> » du worktree.
#
# Une session peut sortir en code 0 sans avoir rien clos (#178) — elle croyait faire une pause. Le
# verdict de la forge la classe ECHEC à juste titre, mais « PR "aucune", cycle de vie "À faire" » ne
# dit pas l'essentiel : le travail est-il PERDU, ou dort-il dans le worktree ? Ces deux compteurs
# tranchent, et la différence est actionnable — un worktree qui porte du travail se rattrape par
# une session ciblée sur la seule clôture, un worktree vide est à refaire.
#
# Lecture seule et sans réseau : `git status` local, et les commits comptés contre `origin/main`
# SEULEMENT si la référence existe (dans un dépôt qui n'a pas de distant, ne rien dire vaut mieux
# que compter toute l'histoire). Un `dest` qui n'est pas un dépôt git rend « 0 0 » sans bruit.
travail_en_attente() {
  local dest="$1" modifs commits=0
  modifs="$(git -C "$dest" status --porcelain 2>/dev/null | grep -c .)" || modifs=0
  if git -C "$dest" rev-parse --verify -q origin/main >/dev/null 2>&1; then
    commits="$(git -C "$dest" rev-list --count origin/main..HEAD 2>/dev/null)" || commits=0
  fi
  printf '%s %s' "${modifs:-0}" "${commits:-0}"
}

# --- Limite d'usage : détecter, attendre, reprendre (#171) --------------------------------------------
# La limite de 5 h n'est pas un échec du ticket, c'est une pause. Un script shell ne consomme aucun
# quota : il peut dormir jusqu'au reset et reprendre LA MÊME session, avec le travail déjà fait dans
# son contexte. C'est tout l'intérêt d'avoir mis le pilote hors de Claude Code.
#
# Trois filets, parce que la forme exacte du signal en mode `-p` n'est pas contractuelle et a déjà
# changé d'une version à l'autre. Les marqueurs viennent du classifieur d'erreurs du CLI lui-même
# (« usage limit reached », « rate limited », « 529 », « credit balance too low ») :
#   1. une heure de reset explicite (epoch, ISO 8601, ou le « …|<epoch> » historique) -> on dort
#      jusqu'à reset + MARGE_REPRISE_S ;
#   2. le message sans heure de reset -> paliers de PALIER_REPRISE_S ;
#   3. rien de tout cela -> ce n'est pas une limite, c'est un échec ordinaire.
MARGE_REPRISE_S="${MAESTRO_ORCHESTRATE_MARGE:-120}"
PALIER_REPRISE_S="${MAESTRO_ORCHESTRATE_PALIER:-900}"
PLAFOND_ATTENTE_S="${MAESTRO_ORCHESTRATE_PLAFOND:-19800}"   # 5 h 30 : au-delà, c'est l'hebdomadaire
MAX_REPRISES="${MAESTRO_ORCHESTRATE_MAX_REPRISES:-3}"
PLAFOND_ATTEINT=0

# --- Ce qui n'est PAS un signal de limite (#203) ------------------------------------------------
# Le CLI ouvre CHAQUE session par un événement d'information qui rapporte la fenêtre de 5 h en
# cours — présent que la limite soit atteinte ou non, et jusque dans une session qui ira au bout :
#
#   {"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":<epoch>,…}}
#
# Depuis que le flux brut est écrit dans `<iid>.jsonl` (#176) et que ce fichier est grepé au même
# titre que le résultat, cette ligne faisait matcher `rate.?limit` et livrait son `resetsAt` à
# `reset_epoch` : une session sortie en SUCCÈS partait dormir jusqu'au prochain reset, son verdict
# GitLab n'était jamais lu, et le ticket pourtant livré était consigné en échec.
#
# On écarte donc ces lignes avant toute recherche — sauf celles qui portent un vrai refus,
# `"status":"rejected"`. Le motif exige le guillemet ouvrant : sans lui, `"overageStatus":"rejected"`
# (une AUTRE clé du même objet, « rejected » dès que l'org interdit le dépassement) sauverait la
# ligne et rendrait le filtre inopérant sur le cas exact qui l'a motivé.
#
# Le filtre porte sur la LIGNE, pas sur le fichier : un `.jsonl` est un événement par ligne, et une
# vraie limite arrive dans un autre événement, conservé tel quel.
flux_utile() {
  local f
  local -a lisibles=()
  for f in "$@"; do [ -f "$f" ] && lisibles+=("$f"); done
  [ "${#lisibles[@]}" -gt 0 ] || return 0
  LC_ALL=C awk '
    /"type"[[:space:]]*:[[:space:]]*"rate_limit_event"/ {
      if ($0 !~ /"status"[[:space:]]*:[[:space:]]*"rejected"/) next
    }
    { print }
  ' "${lisibles[@]}" 2>/dev/null
}

# limite_atteinte <fichier…> : 0 si l'un des fichiers porte la marque d'une limite d'usage.
limite_atteinte() {
  local n
  # `grep -c` et non `-q` : sous `pipefail`, un `-q` fermerait le tube dès la première
  # correspondance, et le SIGPIPE du filtre en amont deviendrait le code de retour du pipeline —
  # une VRAIE limite ressortirait alors en « pas de limite ». On compte, donc on lit tout.
  n="$(flux_utile "$@" | grep -ciE 'usage limit reached|rate.?limit|too many requests|"?api_error_status"?[[:space:]]*:?[[:space:]]*"?429|credit balance')" || n=0
  [ "${n:-0}" -gt 0 ]
}

# reset_epoch <fichier…> : l'instant de reset en secondes Unix, si l'un des fichiers l'expose.
# Trois écritures rencontrées : « usage limit reached|<epoch> », un champ « …reset…: <epoch> » (en
# secondes ou en millisecondes), et un horodatage ISO 8601. Rien si aucune n'est présente.
# Lit le même flux filtré que `limite_atteinte` : le `resetsAt` d'un événement d'information annonce
# la fin de la fenêtre courante, pas une attente à tenir.
reset_epoch() {
  local brut

  brut="$(flux_utile "$@" | grep -oE 'usage limit reached\|[0-9]{10,13}' | head -1 | grep -oE '[0-9]{10,13}')"
  [ -z "$brut" ] && brut="$(flux_utile "$@" | grep -oiE '"[a-z_]*reset[a-z_]*"[[:space:]]*:[[:space:]]*"?[0-9]{10,13}' | head -1 | grep -oE '[0-9]{10,13}$')"
  if [ -n "$brut" ]; then
    # 13 chiffres = millisecondes. Sans cette conversion, l'attente serait ~1 000 fois trop longue.
    [ "${#brut}" -ge 13 ] && brut="${brut%???}"
    printf '%s' "$brut"
    return 0
  fi

  local iso
  iso="$(flux_utile "$@" | grep -oiE '"[a-z_]*reset[a-z_]*"[[:space:]]*:[[:space:]]*"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]+' |
    head -1 | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]+')"
  [ -n "$iso" ] || return 1
  date -u -d "${iso}Z" +%s 2>/dev/null || return 1
}

# delai_avant_reprise <json> <log> : imprime le nombre de secondes à attendre et renvoie 0 si une
# limite d'usage est en cause, 1 sinon (échec ordinaire — pas de reprise).
delai_avant_reprise() {
  limite_atteinte "$@" || return 1
  local epoch maintenant delai
  if epoch="$(reset_epoch "$@")" && [ -n "$epoch" ]; then
    maintenant="$(date +%s)"
    delai=$((epoch - maintenant + MARGE_REPRISE_S))
    # Un reset déjà passé (horloge décalée, en-tête périmé) ne doit pas produire une attente nulle
    # qui relancerait en boucle sur la même limite : on retombe sur le palier.
    [ "$delai" -lt 60 ] && delai="$PALIER_REPRISE_S"
    printf '%s' "$delai"
    return 0
  fi
  printf '%s' "$PALIER_REPRISE_S"
  return 0
}

# --- L'attente partagée : une limite d'usage, un seul sommeil (#291) ----------------------------
# À un ticket en vol la question ne se posait pas. À N, la limite tombe sur TOUTES les sessions à
# quelques secondes d'intervalle et chacune décidait de son attente dans son coin — ce qui n'est pas
# seulement redondant, c'est faux :
#
#   · le flux d'une session porte l'heure de reset, celui d'une autre ne la porte pas (la forme du
#     signal n'est pas contractuelle, cf. les trois filets ci-dessus). La seconde retombait sur le
#     palier de 15 min, se réveillait AVANT le reset, brûlait une reprise pour rien, recommençait, et
#     sortait en échec au bout de MAX_REPRISES pendant que la première, mieux informée, repartait
#     tranquillement. Deux tickets du même run, deux sorts opposés, sur une information que l'une
#     avait et que rien ne transmettait à l'autre ;
#   · le plafond de 5 h 30 ne bornait que la session qui l'atteignait : les N-1 autres continuaient de
#     dormir sur une limite hebdomadaire dont le run avait déjà tiré les conséquences.
#
# D'où un point de rendez-vous unique dans le journal du run, `<run-dir>/.limite` :
#
#   <fin d'attente, en epoch><TAB><source : reset|palier><TAB><iid qui l'a posée>
#
# La MEILLEURE information l'emporte, jamais la plus récente : une heure de reset explicite écrase un
# palier aveugle, un palier n'écrase jamais un reset. C'est ce qui fait profiter tout le run de ce
# qu'une seule session a vu — et c'est l'inverse d'une simple synchronisation, qui aurait aligné les
# réveils sans corriger celui qui était trop tôt.
#
# Pas de verrou : `flock` n'existe pas sous MSYS, et la création exclusive (`set -C`) est le seul
# atome portable disponible ici. Elle suffit à désigner UN annonceur ; la mise à jour d'une attente
# déjà ouverte, elle, est un lire-comparer-écrire qui peut théoriquement perdre une écriture. La
# conséquence est bornée et se répare d'elle-même — au pire une session se réveille sur un palier au
# lieu d'un reset, retrouve la limite et se remet en attente —, là où un verrou coûterait un fichier à
# nettoyer et un cas « verrou périmé » à trancher.
LIM_FIN=0; LIM_SOURCE=""; LIM_IID=""

# limite_lit <fichier> : pose LIM_FIN / LIM_SOURCE / LIM_IID. Rend 1 si aucune attente n'y est en
# cours — fichier absent, illisible, ou fin déjà passée : le vestige d'une attente finie n'est pas
# une attente, et une deuxième limite dans le même run doit pouvoir rouvrir la sienne.
limite_lit() {
  local fin source iid
  # Le fichier est testé AVANT d'être ouvert, et le `2>/dev/null` de la ligne suivante n'y suffirait
  # pas : les redirections sont appliquées de gauche à droite, donc l'échec de `<"$1"` sur un
  # fichier absent — le cas NORMAL, aucune limite en cours — part sur stderr avant que la
  # redirection ne le couvre. Une ligne de bruit dans `run.log` par appel, et il y en a désormais
  # plusieurs par seconde (#420 interroge la limite avant chaque relance de déblocage).
  [ -r "$1" ] || return 1
  IFS=$'\t' read -r fin source iid <"$1" 2>/dev/null || return 1
  case "${fin:-}" in '' | *[!0-9]*) return 1 ;; esac
  [ "$fin" -gt "$(date +%s)" ] || return 1
  LIM_FIN="$fin"; LIM_SOURCE="${source:-palier}"; LIM_IID="${iid:-?}"
  return 0
}

# limite_ecrit <fichier> <fin> <source> <iid> : pose le rendez-vous. Par un temporaire puis un `mv`,
# pour qu'un lecteur ne tombe jamais sur une ligne à moitié écrite.
limite_ecrit() {
  local tmp="$1.$$"
  printf '%s\t%s\t%s\n' "$2" "$3" "$4" >"$tmp" 2>/dev/null || return 1
  mv -f "$tmp" "$1" 2>/dev/null || { rm -f "$tmp" 2>/dev/null; return 1; }
  return 0
}

# limite_partagee <iid> <délai> <source> : inscrit ce ticket dans l'attente du run et imprime
# « <fin retenue, en epoch> <iid de l'annonceur> ». Rend 0 si c'est LUI qui vient d'ouvrir l'attente
# — à lui, alors, de l'annoncer —, 1 s'il rejoint celle d'un autre.
limite_partagee() {
  local iid="$1" origine="$3" fin f
  fin=$(( $(date +%s) + $2 ))
  f="$RUN_DIR/.limite"

  # Création exclusive : si elle réussit, personne n'attendait, et ce ticket est l'annonceur.
  if (set -C; printf '%s\t%s\t%s\n' "$fin" "$origine" "$iid" >"$f") 2>/dev/null; then
    printf '%s %s' "$fin" "$iid"; return 0
  fi

  # Le fichier existe mais aucune attente n'y est en cours : c'est le vestige d'une limite déjà
  # purgée. On le remplace et on redevient l'annonceur — la deuxième limite d'un run mérite sa ligne.
  if ! limite_lit "$f"; then
    limite_ecrit "$f" "$fin" "$origine" "$iid"
    printf '%s %s' "$fin" "$iid"; return 0
  fi

  # Une attente est en cours : on l'améliore si on en sait plus, on l'adopte sinon. L'iid conservé
  # est celui de l'OUVREUR et non le nôtre — ce champ dit qui a annoncé l'attente, pas qui l'a
  # ajustée en dernier, et c'est ce nom que les suivants reprennent en s'y rangeant. Sans cela, une
  # session qui ne fait qu'allonger le rendez-vous de trois secondes s'annoncerait comme l'ayant
  # ouvert — en se nommant elle-même dans « rejoint l'attente ouverte par… ».
  if [ "$origine" = reset ] && [ "$LIM_SOURCE" != reset ]; then
    limite_ecrit "$f" "$fin" "$origine" "$LIM_IID" && LIM_FIN="$fin"
  elif [ "$origine" = "$LIM_SOURCE" ] && [ "$fin" -gt "$LIM_FIN" ]; then
    limite_ecrit "$f" "$fin" "$origine" "$LIM_IID" && LIM_FIN="$fin"
  fi
  printf '%s %s' "$LIM_FIN" "$LIM_IID"
  return 1
}

# limite_en_cours : 0 si le run est présentement en attente d'une limite d'usage. Lu par le PILOTE,
# qui s'en sert pour ne pas jeter un ticket neuf dans une fenêtre déjà fermée.
limite_en_cours() { limite_lit "$RUN_DIR/.limite"; }

# source_de_limite <fichier…> : « reset » si le flux expose une heure de reset, « palier » sinon.
# C'est la qualité de l'information, et c'est elle qui départage deux sessions au rendez-vous.
source_de_limite() {
  if reset_epoch "$@" >/dev/null 2>&1; then printf 'reset'; else printf 'palier'; fi
}

# patiente <iid> <fin d'attente, en epoch> <origine : reset|palier> : attend le rendez-vous, en
# tranches, pour que le fichier STOP reste entendu pendant une attente qui peut durer des heures.
#   0  l'attente est finie   1  arrêt demandé (STOP)   2  limite hebdomadaire déclarée par une autre
#
# La fin est RELUE à chaque tranche, et c'est la MEILLEURE information qui l'emporte — la même règle
# qu'à la publication, sans quoi le rendez-vous porterait une vérité que personne ne suivrait :
#   · une heure de reset publiée par une autre session remplace un palier aveugle, MÊME PLUS TÔT :
#     un palier de 15 min n'est pas une promesse, c'est un aveu d'ignorance, et attendre au-delà du
#     reset ne coûte rien d'autre que du temps de mur — mais en coûte pour rien ;
#   · à source égale, on ne fait que RALLONGER : se réveiller avant le reset brûlerait une reprise
#     sur une limite toujours en cours, et c'est exactement le cas qui faisait échouer, à N, la
#     session la moins bien informée pendant que sa voisine repartait.
#
# La relecture a lieu une fois par tranche, donc jusqu'à une minute après la publication. À l'échelle
# d'une fenêtre de 5 h c'est du bruit, et raccourcir la tranche coûterait des réveils pour rien.
patiente() {
  local iid="$1" fin="$2" origine="${3:-palier}" reste tranche affichee=-1 libelle='?'
  # L'attente est un état du ticket comme un autre : sa ligne reste au bloc, marquée d'une pause et
  # décomptée — sans quoi la console paraît figée pendant les heures que dure une limite d'usage. Le
  # marqueur « = » est ce qui la distingue à l'œil d'une session qui travaille : le pilote n'y met
  # pas son rouet.
  while :; do
    [ -f "$STOP" ] && return 1
    [ -f "$RUN_DIR/.plafond" ] && return 2
    if limite_lit "$RUN_DIR/.limite"; then
      if [ "$LIM_SOURCE" = reset ] && [ "$origine" != reset ]; then
        fin="$LIM_FIN"; origine=reset
      elif [ "$LIM_FIN" -gt "$fin" ]; then
        fin="$LIM_FIN"
      fi
    fi
    if [ "$fin" != "$affichee" ]; then
      affichee="$fin"
      libelle="$(date -d "@$fin" '+%H:%M' 2>/dev/null || echo '?')"
    fi
    reste=$(( fin - $(date +%s) ))
    [ "$reste" -lt 0 ] && reste=0
    # L'état est publié AVANT de décider s'il reste à dormir, donc au moins une fois par attente.
    # Publié après, une attente déjà échue — un rendez-vous à quelques secondes, le temps que la
    # session y arrive — n'en produirait aucun, et l'écran passerait de la session à sa reprise sans
    # jamais dire pourquoi il s'est arrêté entre les deux.
    #
    # `vue_publie` et non `vue_dessine` (#290) : depuis N tickets en vol, une session ne dessine
    # plus — elle publie sa ligne, le pilote la reprend à la frame suivante. La colonne « durée »
    # reste celle du TICKET, c'est elle qu'on suit d'un bout à l'autre ; le temps d'attente, lui, est
    # dit en clair dans le détail.
    vue_publie "$iid" '=' \
      "en attente de la fin de la limite d'usage — reprise vers $libelle (dans $(duree_lisible "$reste"))"
    [ "$reste" -gt 0 ] || break
    tranche=60
    [ "$reste" -lt 60 ] && tranche="$reste"
    sleep "$tranche"
  done
  return 0
}

# --- Le flux d'activité d'une session (#176) ----------------------------------------------------------
# `--output-format stream-json` fait émettre au CLI un objet JSON PAR LIGNE, au fil de l'eau, là où
# `json` n'en écrivait qu'un seul À LA FIN : c'est ce qui permet à la console de dire ce que la
# session fabrique, au lieu de rester muette jusqu'à 45 minutes sur un ticket.
#
# Le flux brut va dans `<iid>.jsonl`. `<iid>.json`, lui, ne reçoit QUE l'objet `result` final — le
# verdict, le coût et la détection de limite d'usage le lisent, et `champ_json` prend la PREMIÈRE
# occurrence d'une clé : y déverser tout le flux ferait rapporter le coût d'un événement
# intermédiaire, une régression silencieuse. Repli sur la dernière ligne si aucun `result` n'est
# passé — un CLI plus ancien, ou un bouchon de test qui n'émet qu'un objet.
# colonnes <texte> : la largeur AFFICHÉE d'une chaîne, en colonnes de terminal.
#
# `${#s}` ne peut pas y répondre seul : il compte des CARACTÈRES sous une locale UTF-8 et des OCTETS
# sous C — « modèle » y pèse 6 ou 7 selon le poste (#325). Un bloc dont la largeur dépend de la
# locale, c'est une ligne qui tient sur une machine et se replie sur la voisine, et un repli est
# précisément ce qui décale le bloc d'une rangée. On mesure donc en octets, couleurs retirées, moins
# les octets de continuation UTF-8 : ce qui reste est le nombre de caractères, partout, sans fork.
#
# `local LC_ALL=C` suffit à basculer bash en mode octet le temps de la fonction (l'affectation
# déclenche un `setlocale`, et la locale d'origine revient au retour) — c'est ce qui rend la
# soustraction des continuations possible même sous une locale UTF-8.
colonnes() {
  local LC_ALL=C s="$1"
  s="${s//"$C_0"/}"; s="${s//"$C_B"/}"; s="${s//"$C_D"/}"
  s="${s//"$C_G"/}"; s="${s//"$C_R"/}"; s="${s//"$C_Y"/}"
  s="${s//[$'\200'-$'\277']/}"
  printf '%s' "${#s}"
}

tronque() { # <texte> [colonnes] : une ligne de progression ne doit jamais noyer la sortie.
  local s="$1" n="${2:-64}"
  # Mesure en colonnes, coupe en `${s:0:n}` : sous une locale octet la coupe rend MOINS que `n`
  # colonnes, jamais plus — le sens qui compte ici, une ligne trop courte n'ayant aucun effet là où
  # une ligne trop longue se replie.
  if [ "$(colonnes "$s")" -gt "$n" ]; then printf '%s…' "${s:0:$n}"; else printf '%s' "$s"; fi
}

# outils_de <ligne> : les appels d'outils d'un événement « assistant », un « <nom> <cible> » par
# ligne. Une ligne peut en porter PLUSIEURS — on les découpe sur leur marqueur plutôt que d'en
# montrer un seul. L'extraction est volontairement approximative (grep, pas un parseur JSON) : c'est
# un fil d'activité, pas une donnée dont dépend un verdict.
outils_de() {
  local reste="$1" nom cible
  while :; do
    case "$reste" in
      *'"type":"tool_use"'*) reste="${reste#*\"type\":\"tool_use\"}" ;;
      *) break ;;
    esac
    nom="$(printf '%s' "$reste" | grep -o '"name":"[^"]*"' | head -1 | cut -d'"' -f4)"
    [ -n "$nom" ] || continue
    cible="$(printf '%s' "$reste" |
      grep -o '"\(file_path\|command\|pattern\|path\|url\|description\)":"[^"]*"' |
      head -1 | cut -d'"' -f4)"
    # Les chemins absolus du worktree mangeraient la ligne pour ne rien apprendre à personne.
    cible="${cible#"$RACINE/"}"
    printf '%s%s\n' "$nom" "${cible:+ $(tronque "$cible")}"
  done
}

# formate_flux <iid> : lit le flux sur stdin, l'archive, et PUBLIE l'état du ticket (#240, #290).
#
# La boucle bat au rythme de `read -t` (voir la section « vue vivante ») : un tour toutes les
# VUE_TICK secondes, qu'une ligne soit arrivée ou non — c'est ce qui garde le battement régulier
# quand la session réfléchit en silence. Trois sorties, bien distinctes :
#   · `<iid>.jsonl`  le flux brut, intégral et inchangé — la matière de tout diagnostic ;
#   · `<iid>.vue`    l'action en cours, que le pilote reprend dans sa frame (#290) ;
#   · stdout         le battement (une ligne par minute) et, en `--verbeux` seulement, le flot
#                    d'une ligne par appel d'outil d'avant #240.
#
# Le chrono et le rouet ne sont plus ici : ils appartiennent au dessin, donc au pilote. Ce qui reste
# à cette boucle, c'est de dire CE QUE LA SESSION FAIT — la seule chose qu'elle soit seule à savoir.
formate_flux() {
  local iid="$1" ligne resultat="" derniere="" partiel="" code
  local jsonl="$RUN_DIR/$iid.jsonl"
  : >"$jsonl"
  # Un `.jsonl.gz` laissé par une tentative précédente (run rejoué sous le même run-id) doit partir
  # avec elle : deux traces du même ticket, dont une périmée, se liraient l'une pour l'autre.
  rm -f "$jsonl.gz" 2>/dev/null
  : >"$RUN_DIR/$iid.json"

  local ecoule=0 action="$VUE_REPRISE" publiee='-' outils o
  local dernier_battement=0

  # Publié d'entrée : sans quoi la première frame d'un ticket repris serait indiscernable de celle
  # d'un ticket qui démarre — c'est tout l'objet de `VUE_REPRISE`.
  vue_publie "$iid" '.' "$action"

  while :; do
    ligne=""
    if IFS= read -r -t "$VUE_TICK" ligne; then
      ligne="$partiel$ligne"; partiel=""
    else
      code=$?
      # > 128 = expiration du délai : simple battement d'horloge. Ce que `read` a déjà lu de la
      # ligne en cours est mis de côté — le recoller est ce qui garde `<iid>.jsonl` fidèle.
      if [ "$code" -gt 128 ]; then
        partiel="$partiel$ligne"
        ligne=""
      else
        # Fin de flux. `$partiel$ligne` : sans lui, un flux qui ne se termine pas par un saut de
        # ligne perdrait sa DERNIÈRE ligne — c'est-à-dire l'objet `result`, donc le coût et le
        # verdict.
        ligne="$partiel$ligne"; partiel=""
        if [ -n "$ligne" ]; then
          printf '%s\n' "$ligne" >>"$jsonl"
          derniere="$ligne"
          case "$ligne" in *'"type":"result"'*) resultat="$ligne" ;; esac
        fi
        break
      fi
    fi

    if [ -n "$ligne" ]; then
      printf '%s\n' "$ligne" >>"$jsonl"
      derniere="$ligne"
      case "$ligne" in *'"type":"result"'*) resultat="$ligne" ;; esac
      case "$ligne" in
        *'"type":"assistant"'*'"type":"tool_use"'*)
          outils="$(outils_de "$ligne")"
          if [ -n "$outils" ]; then
            # La DERNIÈRE : la ligne d'action dit ce que la session fait maintenant, pas ce qu'elle
            # a fait il y a trois événements.
            action="${outils##*$'\n'}"
            if [ "$VERBEUX" = 1 ]; then
              # `dit` et non `printf` : à N sessions en vol, le flot des trois tickets s'entrelace
              # dans le même journal, et un nom d'outil sans son ticket n'apprend rien du tout.
              while IFS= read -r o; do [ -n "$o" ] && dit '  · %s\n' "$o"; done <<<"$outils"
            fi
          fi
          ;;
      esac
    fi

    ecoule=$((SECONDS - VUE_DEBUT_TICKET))

    # Le battement : une ligne par minute DANS LE JOURNAL. C'est ce qui reste de l'activité d'une
    # session quand on relit `run.log` — mais à l'écran il n'apprend rien que le bloc ne dise déjà,
    # en plus frais, et il coûtait cher : une ligne poussée sous le bloc chaque minute, plus un
    # redessin « à neuf » qui laissait le bloc précédent derrière lui (#284).
    if [ "$VUE_BATTEMENT_S" -gt 0 ] && [ $((ecoule - dernier_battement)) -ge "$VUE_BATTEMENT_S" ]; then
      dernier_battement="$ecoule"
      trace_journal '  … %s%s\n' "$(duree_lisible "$ecoule")" "${action:+ · $(tronque "$action")}"
    fi

    # On ne publie que sur CHANGEMENT d'action : le chrono, lui, avance chez le pilote, qui redessine
    # à la seconde sans que rien n'ait à le lui dire. Republier à l'identique cinq fois par seconde
    # ne ferait que réécrire un fichier pour y remettre le même texte.
    if [ "$action" != "$publiee" ]; then
      publiee="$action"
      vue_publie "$iid" '.' "$action"
    fi
  done

  [ -n "$resultat" ] || resultat="$derniere"
  [ -n "$resultat" ] && printf '%s\n' "$resultat" >"$RUN_DIR/$iid.json"
  return 0
}

# compacte_flux <iid> : le flux brut d'un ticket TERMINÉ n'a plus de lecteur — le coût, le verdict et
# la détection de limite d'usage lisent `<iid>.json`, qui ne porte que l'objet `result`. Une fois le
# verdict rendu on le gzippe donc : la matière de diagnostic reste (`zcat`, `zgrep`), le volume part.
# JAMAIS avant : tant que le ticket tourne, `delai_avant_reprise` relit le `.jsonl` entier à chaque
# tentative, et le compacter sous ses pieds ferait passer une pause pour un échec. Best-effort — un
# gzip absent ou en échec ne coûte que de la place.
compacte_flux() {
  local jsonl="$RUN_DIR/$1.jsonl"
  [ -s "$jsonl" ] || return 0
  command -v gzip >/dev/null 2>&1 || return 0
  gzip -f "$jsonl" 2>/dev/null || true
  return 0
}

# --- Le résultat d'une session, EN CLAIR (#180) -------------------------------------------------------
# `<iid>.json` est le premier fichier qu'on ouvre après un échec — et il est écrit en UNE SEULE LIGNE
# minifiée : 3,3 ko pour un ticket, 13 ko pour un autre. Le post-mortem du run 20260729-132807 a
# demandé un script Python pour en tirer le message final et la liste des refus.
#
# On ne le remplace pas : il reste brut, byte-transparent, et c'est lui que `champ_json`,
# `limite_atteinte` et `reset_epoch` grepent — le toucher casserait le verdict, le coût et la
# détection de limite d'usage. On écrit LA MÊME MATIÈRE À CÔTÉ, en clair, dans `<iid>.resultat.txt`.
#
# La lecture du JSON est faite en awk, sans dépendance à `jq` (que personne n'a garanti sur la
# machine d'un run) et sans Python (le pilote est un script shell, il le reste). Elle est
# volontairement minimale : les clés de PREMIER NIVEAU d'un objet `result`, pas un parseur général.
# Elle sait en revanche lire une chaîne ÉCHAPPÉE — le message final tient sur une ligne, ses retours
# à la ligne y sont des « \n » littéraux, et c'est justement ce qui le rend illisible tel quel.
AWK_RESULTAT=$(cat <<'AWK'
# desechappe(s) : rend une chaîne JSON telle qu'on la lit. « \uXXXX » est laissé tel quel : le CLI
# est en Node, dont JSON.stringify n'échappe pas l'UTF-8 — les accents arrivent en clair.
function desechappe(s,   out, i, c, n) {
  out = ""; n = length(s)
  for (i = 1; i <= n; i++) {
    c = substr(s, i, 1)
    if (c != "\\") { out = out c; continue }
    i++
    c = substr(s, i, 1)
    if (c == "n") out = out "\n"
    else if (c == "t") out = out "\t"
    else if (c == "r" || c == "b" || c == "f") out = out ""
    else if (c == "u") { out = out substr(s, i - 1, 6); i += 4 }
    else out = out c
  }
  return out
}

# chaine_a(s, p) : la chaîne qui commence au caractère p (le premier APRÈS le guillemet ouvrant),
# rendue encore échappée. Un guillemet précédé d'un antislash ne ferme pas la chaîne.
function chaine_a(s, p,   i, c, n, out) {
  out = ""; n = length(s)
  for (i = p; i <= n; i++) {
    c = substr(s, i, 1)
    if (c == "\\") { out = out c substr(s, i + 1, 1); i++; continue }
    if (c == "\"") break
    out = out c
  }
  return out
}

# chaine(s, cle) : la valeur texte d'une clé. Chercher « "cle": » ne peut pas se tromper de cible en
# tombant sur la prose : dans une chaîne JSON, tout guillemet est échappé.
function chaine(s, cle) {
  if (!match(s, "\"" cle "\"[ \t]*:[ \t]*\"")) return ""
  return desechappe(chaine_a(s, RSTART + RLENGTH))
}

# scalaire(s, cle) : la valeur d'une clé non textuelle (nombre, booléen).
function scalaire(s, cle,   v) {
  if (!match(s, "\"" cle "\"[ \t]*:[ \t]*")) return ""
  v = substr(s, RSTART + RLENGTH)
  sub(/[,}].*$/, "", v)
  return v
}

# tableau(s, cle) : le CONTENU du tableau d'une clé, crochets exclus. Compte les niveaux, en sachant
# ignorer ce qui est dans une chaîne — une commande refusée contient volontiers un « } ».
function tableau(s, cle,   i, n, c, prof, dans, esc, out) {
  if (!match(s, "\"" cle "\"[ \t]*:[ \t]*\\[")) return ""
  n = length(s); prof = 1; dans = 0; esc = 0; out = ""
  for (i = RSTART + RLENGTH; i <= n; i++) {
    c = substr(s, i, 1)
    if (esc) { esc = 0; out = out c; continue }
    if (dans) {
      if (c == "\\") esc = 1
      else if (c == "\"") dans = 0
      out = out c
      continue
    }
    if (c == "\"") { dans = 1; out = out c; continue }
    if (c == "[" || c == "{") prof++
    else if (c == "]" || c == "}") { prof--; if (prof == 0) break }
    out = out c
  }
  return out
}

# tronque(s, n) : n colonnes au plus. Le comptage se fait en CARACTÈRES, jamais en octets — couper
# une séquence UTF-8 en deux laisserait un « ï¿½ » en bout de ligne, sur une commande accentuée.
function tronque(s, n,   i, l, c, taille) {
  if (largeur(s) <= n) return s
  l = 0; i = 1
  while (i <= length(s) && l < n) {
    c = substr(s, i, 1)
    taille = 1
    if (match(c, /[\300-\337]/)) taille = 2
    else if (match(c, /[\340-\357]/)) taille = 3
    else if (match(c, /[\360-\367]/)) taille = 4
    i += taille; l++
  }
  return substr(s, 1, i - 1) "…"
}

function duree_ms(ms,   s) {
  if (ms == "" || ms + 0 <= 0) return ""
  s = int(ms / 1000)
  if (s < 60) return s "s"
  if (s < 3600) return sprintf("%dmin%02d", s / 60, s % 60)
  return sprintf("%dh%02d", s / 3600, (s % 3600) / 60)
}

# largeur(s) : le nombre de COLONNES d'un libellé. `length()` compte des octets (on tourne en
# LC_ALL=C, pour le point décimal du coût) : sans retirer les octets de continuation UTF-8, « durée »
# en pèserait 6 et décalerait sa ligne d'une colonne vers la gauche.
function largeur(s,   t) { t = s; return length(t) - gsub(/[\200-\277]/, "", t) }

function champ(nom, valeur,   n) {
  n = 12 - largeur(nom)
  if (n < 1) n = 1
  printf "  %s%*s%s\n", nom, n, "", valeur
}

{ brut = brut $0 }

END {
  ligne = "Résultat de session"
  # « ticket » seulement quand c'en est un : depuis #420 une clé de journal peut désigner une
  # session de DÉBLOCAGE (`<iid>-mrfix`), et l'annoncer comme un ticket ferait chercher un ticket
  # de ce numéro-là. Le titre, juste après, dit alors de quelle PR il s'agit.
  if (iid ~ /^[0-9]+$/) ligne = ligne " — ticket #" iid
  else if (iid != "")   ligne = ligne " — #" iid
  if (titre != "") ligne = ligne " · " titre
  print ligne
  sid = chaine(brut, "session_id")
  ligne = ""
  if (run != "") ligne = "run " run
  if (sid != "") ligne = ligne (ligne != "" ? " · " : "") "session " sid
  if (ligne != "") print ligne
  print ""

  # Le verdict vient de la boucle, donc de la forge (PR ouverte ET cycle de vie « En revue ») —
  # jamais de la prose ci-dessous, qui peut se croire réussie sans l'être. Absent quand on relit un
  # vieux fichier.
  if (verdict != "") {
    v = verdict
    if (verdict == "OK") v = "✓ OK"
    else if (verdict == "ECHEC") v = "✗ ECHEC"
    if (mr != "" && mr != "-") v = v " — PR #" mr
    if (raison != "" && raison != "-") v = v " — " raison
    champ("verdict", v)
  }

  if (brut == "") {
    champ("session", "aucun résultat final — la session est morte sans rendre la main")
    print ""
    print "Le CLI n'écrit son objet `result` qu'à la toute fin : un fichier vide dit un timeout, un"
    print "crash, ou un poste éteint. Il ne reste que le flux d'activité et la sortie d'erreur —"
    print "  zcat <run>/" (iid != "" ? iid : "<iid>") ".jsonl.gz | tail -20      (ou le .jsonl s'il n'est pas encore compacté)"
    print "  cat  <run>/" (iid != "" ? iid : "<iid>") ".log"
    exit
  }

  etat = chaine(brut, "subtype")
  if (etat == "") etat = "?"
  if (scalaire(brut, "is_error") == "true") etat = etat " · EN ERREUR"
  arret = chaine(brut, "stop_reason")
  if (arret != "") etat = etat " · " arret
  tours = scalaire(brut, "num_turns")
  if (tours != "") etat = etat " · " tours " tours"
  champ("session", etat)

  d = duree_ms(scalaire(brut, "duration_ms"))
  if (d == "" && duree != "" && duree + 0 > 0) d = duree_ms(duree * 1000)
  if (d != "") {
    api = duree_ms(scalaire(brut, "duration_api_ms"))
    champ("durée", d (api != "" ? " (dont " api " d'API)" : ""))
  }

  cout = scalaire(brut, "total_cost_usd")
  if (cout != "") champ("coût", sprintf("%.2f $", cout + 0))

  # Les refus de permission : ce qu'on vient chercher en premier après un run décevant (§11.7). Un
  # refus ne bloque pas la session — il se paie en tours et en dollars quand elle contourne, en run
  # perdu quand elle ne peut pas. D'où le compte par outil, en tête, avant le détail.
  nb = 0
  contenu = tableau(brut, "permission_denials")
  if (contenu != "") {
    parts = split(contenu, morceaux, /"tool_name"[ \t]*:[ \t]*/)
    for (k = 2; k <= parts; k++) {
      m = morceaux[k]
      if (substr(m, 1, 1) != "\"") continue
      nb++
      noms[nb] = desechappe(chaine_a(m, 2))
      compte[noms[nb]]++
      cible = ""
      if (match(m, /"(command|skill|file_path|pattern|path|url|description)"[ \t]*:[ \t]*"/))
        cible = desechappe(chaine_a(m, RSTART + RLENGTH))
      gsub(/\n/, " ", cible)
      cibles[nb] = cible
    }
  }
  if (nb == 0) {
    champ("refus", "aucun")
  } else {
    detail = ""
    for (nom in compte) detail = detail (detail != "" ? ", " : "") nom " " compte[nom]
    champ("refus", nb " — " detail)
  }

  if (nb > 0) {
    print ""
    print "── Refus de permission (" nb ")"
    for (k = 1; k <= nb; k++)
      printf "  - %s%s\n", noms[k], (cibles[k] != "" ? " — " tronque(cibles[k], 110) : "")
    print ""
    print "  Les instruire au cas par cas : docs/10-workflow-git.md §11.7. Une commande composée vaut"
    print "  son maillon le plus faible, et un « cd » de confort en tête suffit à faire refuser le reste."
  }

  print ""
  print "── Message final"
  msg = chaine(brut, "result")
  print (msg != "" ? msg : "  (aucun — la session n'a rien rendu)")
}
AWK
)

# vue_resultat <json> [iid] [titre] [verdict] [mr] [duree_s] [raison] [run-id] : la vue lisible, sur
# stdout. `LC_ALL=C` pour le point décimal du coût, comme dans `arrondi_cout`.
vue_resultat() {
  local json="${1:-}"
  [ -f "$json" ] || json=/dev/null
  LC_ALL=C awk -v iid="${2:-}" -v titre="${3:-}" -v verdict="${4:-}" -v mr="${5:-}" \
    -v duree="${6:-}" -v raison="${7:-}" -v run="${8:-}" "$AWK_RESULTAT" "$json"
}

# ecrit_resultat <iid> <titre> <verdict> <mr> <duree_s> <raison> : la même vue, à côté du JSON.
# Best-effort de bout en bout : un awk absent ou fâché ne doit pas changer le sort d'un ticket qui
# vient d'être livré — ce fichier est un confort de lecture, pas une donnée du run.
ecrit_resultat() {
  local iid="$1"
  vue_resultat "$RUN_DIR/$iid.json" "$iid" "$2" "$3" "$4" "$5" "$6" "$RUN_ID" \
    >"$RUN_DIR/$iid.resultat.txt" 2>/dev/null || true
  return 0
}

# lance_session <clé> <dest> <uuid> <mode> [<tâche>] [<cible>] : une session, neuve ou reprise. En
# reprise, `--resume` rouvre la conversation interrompue — sans quoi la session repartirait de zéro
# et referait le travail déjà payé. Si la reprise échoue (session perdue), on repart à froid sur un
# UUID neuf : le prompt et /ticket-start sont idempotents, le travail déjà commité est retrouvé sur
# la branche.
#
# La CLÉ nomme les fichiers de journal ; la TÂCHE dit quel prompt écrire et la CIBLE sur quoi. Pour
# un ticket les trois se confondent (l'iid), et c'est pourquoi les deux derniers paramètres ont un
# défaut : le déblocage d'une PR (#420) est la seule tâche à les dissocier — clé `<iid>-mrfix`,
# tâche `mrfix`, cible le numéro de la PR.
lance_session() {
  local iid="$1" dest="$2" uuid="$3" mode="$4" tache="${5:-ticket}" cible="${6:-$1}" code
  if [ "$mode" = "reprise" ]; then
    ( cd "$dest" && ${OPT_TIMEOUT[@]+"${OPT_TIMEOUT[@]}"} "$CLAUDE_BIN" -p "$(prompt_reprise_de "$tache" "$cible")" \
        --resume "$uuid" \
        --output-format stream-json --verbose \
        --permission-mode acceptEdits \
        --settings "$RACINE/scripts/orchestrate/settings.run.json" \
        ${OPT_BUDGET+"${OPT_BUDGET[@]}"} \
        --model "$MODELE" \
        --effort "$EFFORT" </dev/null ) 2>"$RUN_DIR/$iid.log" | formate_flux "$iid"
    # Le code du CLI, pas celui du formateur : c'est lui qui dit si la session a abouti.
    code=${PIPESTATUS[0]}
    [ "$code" -eq 0 ] && return 0
    if limite_atteinte "$RUN_DIR/$iid.json" "$RUN_DIR/$iid.jsonl" "$RUN_DIR/$iid.log"; then return "$code"; fi
    dit '  reprise de session impossible — redémarrage à froid (le travail déjà commité est sur la branche).\n'
    uuid="$(genere_uuid)"
    printf '%s' "$uuid" >"$RUN_DIR/$iid.session"
  fi
  ( cd "$dest" && ${OPT_TIMEOUT[@]+"${OPT_TIMEOUT[@]}"} "$CLAUDE_BIN" -p "$(prompt_de "$tache" "$cible")" \
      --session-id "$uuid" \
      --output-format stream-json --verbose \
      --permission-mode acceptEdits \
      --settings "$RACINE/scripts/orchestrate/settings.run.json" \
      ${OPT_BUDGET+"${OPT_BUDGET[@]}"} \
      --model "$MODELE" \
      --effort "$EFFORT" </dev/null ) 2>"$RUN_DIR/$iid.log" | formate_flux "$iid"
  return "${PIPESTATUS[0]}"
}

# --- Le prompt d'une session ------------------------------------------------------------------------
# Écrit pour être IDEMPOTENT : une session relancée sur un ticket déjà entamé doit reprendre, pas
# recommencer. C'est ce qui rend une reprise après interruption (#171) sans danger.
#
# Il interdit deux formes d'attente, et la seconde a coûté un run entier (#178). Attendre une
# VALIDATION était déjà exclu — personne ne lira une question. Attendre un RÉSULTAT ne l'était pas,
# et une session a rendu la main sur « j'attends la fin du run de couverture (notification
# automatique) » : en mode `-p`, la fin du tour est la fin du processus, aucune notification ne
# viendra jamais. Le CLI sort en `end_turn`, `success`, code 0 — indiscernable d'une session qui a
# vraiment fini. Le ticket est resté « À faire » avec son travail non commité, et les lots suivants
# de son parent ont été sautés.
#
# Il dit aussi la FORME des appels shell (#179), parce qu'elle se paie en refus silencieux : onze des
# dix-sept refus du premier run ne venaient pas d'un geste interdit mais d'un emballage que
# l'allowlist ne reconnaissait plus — un `cd "<worktree>" &&` inutile en tête (la session y est déjà),
# un chemin absolu là où la règle borne un chemin relatif, un `echo` de confort en fin de chaîne. Une
# commande chaînée n'est autorisée que si CHACUN de ses morceaux l'est.
#
# Onze runs plus tard (#235, parent #232 : 83 refus sur 16 sessions), il nomme aussi les trois formes
# qu'AUCUNE règle ne peut matcher, quelle que soit la commande qu'elles habillent — saut de ligne,
# substitution `$(…)`, heredoc. Elles ne se devinent pas depuis un refus, qui ne dit pas ce qui a
# manqué, et la plus coûteuse tombe sur la DERNIÈRE action du ticket : huit sessions sur seize ont
# buté sur une création de PR à description multi-ligne, puis sur le `--body "$(cat …)"` par
# lequel elles essayaient de s'en sortir. D'où le renvoi vers l'outil `Write` : un fichier s'écrit
# avec lui, et c'est son CHEMIN qui entre dans la commande.
#
# Onze runs de plus encore (#307), et la NATURE du refus a changé sans que le compte baisse : les
# sept commandes les plus refusées sont désormais toutes dans l'`allow`, et ce qui les fait tomber
# est la CIBLE — 9 refus sur 12 du dernier run complet sont des échappées de chemin. Le prompt ne
# pouvait pas s'en tenir à « reste en relatif » : une session écrit forcément des fichiers de travail
# quelque part, et les deux endroits qu'elle connaît (son répertoire temporaire, `/tmp`) sont hors du
# répertoire de travail. Il lui en DÉSIGNE donc un dans son worktree — `.maestro/session/`, monté par
# `worktree.sh` —, sans quoi la consigne n'aurait fait qu'interdire. Il donne au passage la seule
# forme qui pose une variable sans tomber : `env VAR=… <commande>`, un préfixe nu n'étant matchable
# par aucune règle (§11.7).
prompt_ticket() {
  cat <<PROMPT
Tu traites intégralement le ticket GitLab #$1 de ce dépôt, seul et sans supervision humaine.

1. Lance la commande /ticket-start $1.
2. Implémente tous les critères d'acceptation du ticket.
3. Clôture avec /ticket-ship.

Règles de ce run autonome :
- N'attends AUCUNE validation : personne ne lira une question. Le résumé de cadrage de
  /ticket-start n'est pas une pause. Si un choix se présente, tranche, et dis dans le résumé
  final ce que tu as tranché et pourquoi.
- N'attends AUCUN RÉSULTAT différé non plus, et ne rends JAMAIS la main en annonçant que tu
  reprendras « dès que » quelque chose sera prêt (tâche de fond, suite de tests, pipeline,
  notification). Ce processus s'arrête à la fin de ton tour : rien ne te réveillera, et le ticket
  serait perdu avec son travail. Un résultat qui te manque s'obtient EN AVANT-PLAN (lance la
  commande et attends-la dans le même tour), sinon tranche sans lui en le disant, sinon sors sur
  ORCHESTRATE: ECHEC. Ne lance rien en arrière-plan dont tu aurais besoin ensuite.
- Tes commandes passent une allowlist, et une commande chaînée n'est autorisée que si CHACUN de
  ses morceaux l'est : préfère un appel par commande à une longue chaîne « && », qu'un seul
  maillon inattendu fait refuser en entier. Tu es déjà DANS le worktree du ticket : inutile de
  commencer par « cd », et appelle les scripts du dépôt en chemin RELATIF (« bash
  scripts/gitlab/lib.sh … ») sans préfixe de variable d'environnement devant l'interpréteur —
  sous ces deux formes-là, la règle qui autorise la commande ne la reconnaît plus et l'appel est
  refusé sans que personne soit là pour l'approuver. Pour poser quand même une variable, écris
  « env VAR=valeur <commande> » : cette forme-là est autorisée, « VAR=valeur <commande> » non.
- TOUT CHEMIN ABSOLU est refusé, même vers ton propre worktree : c'est la cause n°1 des refus
  (9 sur 12 du dernier run). Tes fichiers de travail — description de PR, corps de commentaire,
  sortie intermédiaire que tu veux relire — s'écrivent dans « .maestro/session/ », qui existe déjà
  dans ton worktree et est gitignoré. N'écris ni dans « /tmp », ni dans le répertoire temporaire
  de la session : ils sont hors du répertoire de travail, donc illisibles pour toi ensuite.
- Trois formes qu'AUCUNE règle ne peut reconnaître, quelle que soit la commande qu'elles habillent
  et même si elle est autorisée : un SAUT DE LIGNE dans la commande, une SUBSTITUTION \$(…), un
  HEREDOC (« <<'EOF' »). Tiens donc chaque appel sur UNE SEULE LIGNE, et n'y fais entrer aucun
  texte long. Pour écrire un fichier — description de PR, corps de commentaire, note de travail —
  sers-toi de l'outil Write, puis donne le CHEMIN de ce fichier à la commande : jamais
  « cat > … <<'EOF' », jamais « --description "\$(cat …)" ».
- Si la branche du ticket existe déjà et porte des commits, OU si le worktree contient des
  modifications non commitées, REPRENDS ce travail au lieu de recommencer : commence par regarder
  git status et git log. Tu es peut-être la reprise d'une session interrompue, et un arbre sale
  sans aucun commit est précisément la trace qu'elle laisse.
- Ta clôture s'ARRÊTE à la PR : une PR ouverte, prête (pas un brouillon), et le ticket « En
  revue ». C'est exactement le verdict que ce run lit de toi. N'attends AUCUN pipeline et ne
  merge pas — ni « gh pr merge », ni « lib.sh merge-mr », ni « lib.sh pipeline-wait », qu'un
  garde-fou refuse de toute façon ici. C'est le pilote qui merge, hors de ta session : il
  sérialise les merges et n'attend rien sur ton quota. Si /ticket-ship s'y heurte, ton ticket
  n'est pas en échec pour autant — il est fini, dis-le et sors.
- Ne ferme jamais une PR, ne force-push jamais — un garde-fou les refuse aussi.
- Si tu ne peux pas terminer, écris en TOUTE DERNIÈRE LIGNE : ORCHESTRATE: ECHEC <raison courte>.
PROMPT
}

# Le prompt de reprise s'adresse à une conversation QUI A DÉJÀ SON CONTEXTE : inutile de lui
# réexpliquer le ticket, il faut au contraire éviter qu'elle recommence ce qu'elle a fait. Il sert
# deux coupures que rien ne distingue vues d'ici — la limite d'usage (#171) et le run repris en vol
# (#204) — d'où une formulation qui ne présume pas de la cause.
prompt_reprise() {
  cat <<PROMPT
Reprends exactement là où tu t'es arrêté sur le ticket #$1 : la session a été interrompue (limite
d'usage, ou run coupé), pas par une erreur. Ne recommence rien de ce qui est déjà fait — regarde
d'abord l'état de la branche (git status, git log) avant d'agir. Termine l'implémentation puis clôture avec
/ticket-ship. Toujours aucune validation humaine à attendre, et aucun résultat différé non plus :
ce processus s'arrête à la fin de ton tour, ne rends pas la main en annonçant que tu reprendras
plus tard — obtiens ce qui te manque en avant-plan, tranche sans lui, ou sors sur
ORCHESTRATE: ECHEC.
PROMPT
}

# --- Le prompt d'une session de déblocage (#420) ------------------------------------------------------
# Il reprend mot pour mot les règles de forme du prompt de ticket — allowlist, chemins relatifs,
# atelier `.maestro/session/`, les trois formes immatchables — parce que ces refus-là ne dépendent
# pas de la tâche : c'est la même couche de permissions, dans le même worktree, avec le même hook.
#
# Ce qu'il ajoute est ce qui distingue une REMÉDIATION d'un ticket, et les deux points tiennent à ce
# que la commande `/mr-fix` fait de plus depuis #418 : elle merge ce qu'elle débloque, et elle
# attend des pipelines. Ni l'un ni l'autre n'a lieu d'être ici — le pilote merge, le pilote attend —
# et `guard.sh` refuse les deux gestes. Le dire AVANT, plutôt que de laisser la session s'y heurter :
# un ordre contredit sans explication se contourne, un ordre expliqué se suit. La session vaut donc
# les étapes 1 à 11 de la commande, jamais la 12.
prompt_mrfix() {
  cat <<PROMPT
Tu débloques la Pull Request #$1 de ce dépôt, seul et sans supervision humaine. Tu es dans le
worktree de son ticket, sur sa branche : il n'y a rien à monter ni à sortir.

1. Lance la commande /mr-fix $1.
2. Va jusqu'à ce que la PR soit mergeable : conflit avec origin/main d'abord, pipeline rouge
   ensuite — cet ordre-là et pas l'autre, le merge d'origin/main pouvant lui-même casser le
   pipeline.

Règles de ce run autonome :
- NE MERGE PAS, c'est la seule étape de /mr-fix qui ne t'appartient pas : ni « gh pr merge », ni
  « bash scripts/gitlab/lib.sh merge-mr », ni « lib.sh pipeline-wait » — un garde-fou refuse les
  trois ici. C'est le PILOTE qui merge, hors de ta session : il sérialise les merges et n'attend
  aucun pipeline sur ton quota. Ton travail s'arrête quand la PR est mergeable, et c'est un
  résultat complet, pas une clôture manquée.
- N'ATTENDS AUCUN PIPELINE, et ne rends jamais la main en annonçant que tu reprendras « dès que »
  le verdict sera tombé : ce processus s'arrête à la fin de ton tour, rien ne te réveillera. Pousse
  ton correctif et sors. Le pilote relira le verdict et rouvrira une session si la PR est encore
  bloquée — tu n'as droit qu'à deux passages en tout, alors sers-toi du filet local pour vérifier
  ton correctif en avant-plan : bash scripts/ci/local.sh --only <job>.
- N'attends AUCUNE validation non plus : personne ne lira une question. Si un choix se présente,
  tranche, et dis dans ton résumé final ce que tu as tranché et pourquoi.
- UNE RÉSOLUTION QUI N'EST PAS CLAIRE NE SE POUSSE PAS : git merge --abort, branche laissée
  intacte, et dis pourquoi. Une PR qui attend coûte infiniment moins qu'une résolution fausse
  partie dans main. Ne prends jamais un côté en bloc (--ours/--theirs) pour faire disparaître des
  marqueurs, et ne fabrique jamais un correctif de code pour une panne d'infrastructure.
- Jamais de rebase (il appellerait un force-push, refusé), jamais de force-push, jamais de
  fermeture de PR, jamais de commit sur main.
- Tes commandes passent une allowlist, et une commande chaînée n'est autorisée que si CHACUN de
  ses morceaux l'est : préfère un appel par commande à une longue chaîne « && ». Tu es déjà DANS
  le worktree : inutile de commencer par « cd », et appelle les scripts du dépôt en chemin RELATIF
  (« bash scripts/gitlab/lib.sh … ») sans préfixe de variable devant l'interpréteur. Pour poser
  quand même une variable : « env VAR=valeur <commande> ».
- TOUT CHEMIN ABSOLU est refusé, même vers ton propre worktree. Tes fichiers de travail — message
  de commit, note intermédiaire — s'écrivent dans « .maestro/session/ », qui existe déjà ici et
  est gitignoré ; ni « /tmp », ni le répertoire temporaire de la session.
- Trois formes qu'AUCUNE règle ne peut reconnaître, même autour d'une commande autorisée : un SAUT
  DE LIGNE dans la commande, une SUBSTITUTION \$(…), un HEREDOC. Tiens chaque appel sur UNE SEULE
  ligne. Pour un message de commit, écris le fichier avec l'outil Write puis « git commit -F
  <chemin> » — jamais « -m "\$(cat …)" ».
- Si tu ne peux pas débloquer cette PR, écris en TOUTE DERNIÈRE LIGNE :
  ORCHESTRATE: ECHEC <raison courte>. La laisser ouverte et intacte est une fin acceptable.
PROMPT
}

# Le prompt de reprise d'un déblocage : même raison d'être que celui d'un ticket (#171) — la
# conversation a déjà son contexte, il ne faut surtout pas qu'elle recommence, et la coupure
# (limite d'usage, run coupé) ne se distingue pas d'ici.
prompt_mrfix_reprise() {
  cat <<PROMPT
Reprends exactement là où tu t'es arrêté sur le déblocage de la PR #$1 : la session a été
interrompue (limite d'usage, ou run coupé), pas par une erreur. Regarde d'abord l'état de la
branche (git status, git log) — un merge en cours s'y voit — avant d'agir. Rappel : tu ne merges
pas la PR (c'est le pilote), tu n'attends aucun pipeline, et une résolution qui n'est pas claire
s'abandonne par git merge --abort plutôt que de se pousser. Si tu ne peux pas terminer, sors sur
ORCHESTRATE: ECHEC <raison courte>.
PROMPT
}

# prompt_de <tâche> <cible> / prompt_reprise_de <tâche> <cible> : quel prompt pour quelle tâche. Un
# seul mécanisme de session sert les deux — un ticket à traiter, une PR à débloquer —, et c'est ce
# qui donne à la seconde le régime de la première sans en recopier une ligne.
prompt_de() {
  case "$1" in mrfix) prompt_mrfix "$2" ;; *) prompt_ticket "$2" ;; esac
}

prompt_reprise_de() {
  case "$1" in mrfix) prompt_mrfix_reprise "$2" ;; *) prompt_reprise "$2" ;; esac
}

# --- Diagnostic de la détection de limite d'usage -----------------------------------------------------
# Placé avant tout le reste : il ne demande ni forge, ni plan, ni répertoire de run — il ne fait que
# rejouer le jugement de la boucle sur une sortie de session déjà capturée.
if [ -n "$TEST_REPRISE" ]; then
  [ -r "$TEST_REPRISE" ] || { printf 'run.sh : fichier illisible — %s\n' "$TEST_REPRISE" >&2; exit 2; }
  if delai="$(delai_avant_reprise "$TEST_REPRISE" "$TEST_REPRISE")"; then
    epoch="$(reset_epoch "$TEST_REPRISE" "$TEST_REPRISE")" || epoch=""
    printf 'LIMITE D'\''USAGE détectée — attente de %s (%s s)\n' "$(duree_lisible "$delai")" "$delai"
    if [ -n "$epoch" ]; then
      printf '  reset annoncé : %s (epoch %s) + %s s de marge\n' \
        "$(date -d "@$epoch" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo '?')" "$epoch" "$MARGE_REPRISE_S"
    else
      printf '  aucune heure de reset exposée — palier de %s\n' "$(duree_lisible "$PALIER_REPRISE_S")"
    fi
    [ "$delai" -gt "$PLAFOND_ATTENTE_S" ] &&
      printf '  ⚠ au-delà du plafond de %s : traité comme une limite hebdomadaire, le run s'\''arrêterait.\n' \
        "$(duree_lisible "$PLAFOND_ATTENTE_S")"
    exit 0
  fi
  printf 'PAS UNE LIMITE D'\''USAGE — échec ordinaire, aucune reprise ne serait tentée.\n'
  exit 1
fi

# Relire un résultat de session déjà capturé (#180). Même place et même esprit que ci-dessus : ni
# forge, ni plan, ni répertoire de run — juste la vue lisible d'un `<iid>.json`, sur stdout. C'est ce
# qui rattrape les runs écrits AVANT ce lot, dont le journal ne porte pas de `.resultat.txt`.
if [ -n "$LIRE_RESULTAT" ]; then
  [ -r "$LIRE_RESULTAT" ] || { printf 'run.sh : fichier illisible — %s\n' "$LIRE_RESULTAT" >&2; exit 2; }
  # L'iid se déduit du nom du fichier (« 130.json ») quand il en porte un : c'est le cas nominal.
  iid_lu="$(basename "$LIRE_RESULTAT")"; iid_lu="${iid_lu%%.*}"
  case "$iid_lu" in *[!0-9]* | '') iid_lu="" ;; esac
  vue_resultat "$LIRE_RESULTAT" "$iid_lu" "" "" "" "" "" "$(basename "$(dirname "$LIRE_RESULTAT")")"
  exit 0
fi

# Ne faire QUE tuer (#213) : ni forge, ni plan, ni répertoire de run. C'est le geste de quelqu'un qui
# veut la place nette sans lancer quoi que ce soit — et la couture par laquelle les tests vérifient
# l'arrêt sans dérouler un run entier.
if [ "$TUER_SEUL" = 1 ]; then
  # La fonction rend le NOMBRE de runs arrêtés : elle « réussit » donc quand elle n'a rien eu à
  # faire, et c'est le seul cas où il reste quelque chose à dire (elle est muette pour le reste).
  if tue_les_runs_en_vol; then
    printf 'Aucun run en cours — rien à arrêter.\n'
  fi
  exit 0
fi

# --- Préflight ---------------------------------------------------------------------------------------
gl_require || exit 1

# Le délai ne devient une enveloppe de session que s'il a été DEMANDÉ (#326), sur la mécanique exacte
# du budget : c'est `OPT_TIMEOUT` — vide par défaut — qui préfixe le CLI, jamais `timeout 0`, qui
# tuerait chaque session à l'instant même (le pendant du `--max-budget-usd 0` évité en #286).
# `TIMEOUT_S` à 0 est la sentinelle « aucun délai », et elle vaut aussi pour l'échéance que le pilote
# se donne sur un sous-shell (cf. `lance_ticket`).
case "$TIMEOUT_BRUT" in
  '' | 0 | 0s | 0m | 0h) TIMEOUT_S=0 ;;
  *)
    TIMEOUT_S="$(secondes "$TIMEOUT_BRUT")" || {
      printf 'run.sh : durée invalide pour --timeout : %s (attendu 45m, 2h, 2700…, ou 0 pour aucun délai)\n' "$TIMEOUT_BRUT" >&2
      exit 2
    }
    ;;
esac
OPT_TIMEOUT=()
[ "$TIMEOUT_S" -gt 0 ] && OPT_TIMEOUT=(timeout "$TIMEOUT_S")

if [ "$DRY" = 0 ] && ! command -v "$CLAUDE_BIN" >/dev/null 2>&1; then
  printf 'run.sh : « %s » introuvable — le CLI Claude Code est nécessaire pour lancer les sessions.\n' "$CLAUDE_BIN" >&2
  exit 1
fi

if arret_demande; then exit 0; fi

# --- La place nette : un seul run à la fois (#213) ----------------------------------------------------
# AVANT la résolution de `--resume`, et l'ordre n'est pas indifférent : `status.sh --reprenables`
# écarte les runs qui écrivent encore, donc un run tué juste après aurait été ignoré par
# « --resume » sans argument — celui-là même qu'on vient d'interrompre, et le plus probablement visé.
# Tué d'abord, il redevient candidat immédiatement : `status.sh` lit la carte `pid`, et un pilote
# mort ne se cache plus derrière son silence récent.
#
# `--dry-run` n'y passe pas : il n'exécute rien, il n'a donc aucune place à faire.
if [ "$DRY" = 0 ] && [ "$SANS_KILL" = 0 ]; then
  tue_les_runs_en_vol "$RUN_ID"
elif [ "$SANS_KILL" = 1 ] && [ "$DRY" = 0 ]; then
  printf '%s--sans-kill%s : les runs en cours sont laissés en place — deux pilotes peuvent cohabiter.\n' \
    "$C_Y" "$C_0"
fi

# --- Reprise d'un run qui ne s'est pas terminé (#204) -------------------------------------------------
# Reprendre, c'est REJOUER LE PLAN d'un run interrompu — pas en recalculer un. Le backlog a pu bouger
# entre-temps (un ticket pris à la main, un lot ajouté, une priorité changée) et un ordre recalculé
# n'aurait plus grand-chose à voir avec celui qu'on croit reprendre. Le plan est figé une fois, au
# départ ; la relecture du statut de chaque ticket, elle, suffit à écarter ce qui a été livré depuis.
#
# Le journal, lui, est NEUF : `resume.tsv` s'écrit en tête de run, donc rejouer dans le répertoire du
# run repris effacerait son bilan. Le lien entre les deux tient dans le fichier `reprise-de`.
#
# La résolution a lieu ICI, avant la création du répertoire et avant `--detach` : une reprise qui ne
# désigne rien doit le dire tout de suite, pas dans une console qui s'ouvre pour se refermer.
if [ "$REPRISE" = 1 ]; then
  if [ -n "$PLAN_IMPOSE" ]; then
    printf 'run.sh : --resume et --plan désignent tous deux le plan à jouer — n'\''en garder qu'\''un.\n' >&2
    exit 2
  fi
  # Tolérant au copier-coller : le chemin d'un journal vaut son run-id.
  [ -n "$REPRISE_ID" ] && REPRISE_ID="$(basename "${REPRISE_ID%/}")"
  if [ -z "$REPRISE_ID" ]; then
    # Le choix du run est délégué à `status.sh --reprenables`, source unique de « qu'est-ce qui est
    # reprenable ? » : le plus récent est le dernier de sa liste, triée du plus ancien au plus récent.
    REPRISE_ID="$(bash "$RACINE/scripts/orchestrate/status.sh" --reprenables 2>/dev/null | tail -1 | cut -f1)"
    if [ -z "$REPRISE_ID" ]; then
      printf 'run.sh : aucun run à reprendre — les plans connus ont tous rendu leur verdict.\n' >&2
      printf '  les runs connus     bash scripts/orchestrate/status.sh --list\n' >&2
      printf '  un run neuf         bash scripts/orchestrate/run.sh --detach\n' >&2
      exit 1
    fi
  fi
  REPRISE_DIR="$ORCH_DIR/$REPRISE_ID"
  if [ ! -r "$REPRISE_DIR/plan.tsv" ]; then
    printf 'run.sh : le run « %s » n'\''a pas de plan lisible — %s\n' "$REPRISE_ID" "$REPRISE_DIR/plan.tsv" >&2
    printf '  les runs connus     bash scripts/orchestrate/status.sh --list\n' >&2
    exit 1
  fi
  PLAN_IMPOSE="$REPRISE_DIR/plan.tsv"
  # Rejouer un run DANS son propre répertoire écraserait le bilan qu'on prétend justement
  # préserver : `resume.tsv` s'écrit en tête de run, et le plan se recopierait sur lui-même.
  if [ -n "$RUN_ID" ] && [ "$RUN_ID" = "$REPRISE_ID" ]; then
    printf 'run.sh : --run-id %s est le run repris lui-même — son bilan serait écrasé.\n' "$RUN_ID" >&2
    printf '  une reprise écrit dans un journal NEUF : laisser --run-id de côté, ou en choisir un autre.\n' >&2
    exit 2
  fi

  # La concurrence est un trait DU RUN, pas de la ligne de commande qui le rejoue (#291). Un run coupé
  # alors qu'il avait quatre tickets en main se reprend à quatre : sans cela, `/orchestrate --resume`,
  # qui ne passe aucune option, le rejouerait en séquentiel — les tickets repris seraient bien tous
  # traités, mais un par un, et la reprise ne serait plus le même run. Même raison que le plan figé :
  # reprendre, c'est rejouer ce qui a été interrompu, pas en recalculer une version d'aujourd'hui.
  #
  # Un choix explicite l'emporte, lui : `--resume --concurrence 1` reste la façon de dérouler en
  # séquentiel un run qui tournait à N (pour l'observer, ou parce que le quota est serré).
  if [ "$CONCURRENCE_EXPLICITE" = 0 ] && [ -r "$REPRISE_DIR/concurrence" ]; then
    read -r conc_reprise <"$REPRISE_DIR/concurrence" 2>/dev/null || conc_reprise=""
    case "${conc_reprise:-}" in
      '' | *[!0-9]* | 0) ;;
      *) CONCURRENCE="$conc_reprise" ;;
    esac
  fi
fi

[ -n "$RUN_ID" ] || RUN_ID="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="$ORCH_DIR/$RUN_ID"
mkdir -p "$RUN_DIR" || { printf 'run.sh : impossible de créer %s\n' "$RUN_DIR" >&2; exit 1; }
PLAN="$RUN_DIR/plan.tsv"
RESUME="$RUN_DIR/resume.tsv"
# Deux journaux partiels qui racontent la même liste de tickets doivent se répondre : sans ce
# fichier, rien ne dirait que celui-ci continue l'autre. `status.sh` l'affiche en en-tête.
[ "$REPRISE" = 1 ] && printf '%s\n' "$REPRISE_ID" >"$RUN_DIR/reprise-de"
# La concurrence du run, laissée en clair : c'est ce qu'une reprise relit pour rejouer LE MÊME run et
# non sa version séquentielle (#291). Un fichier d'une ligne plutôt qu'une colonne de plus au plan —
# le plan décrit les tickets, jamais le régime du pilote, et une reprise joue le plan d'un autre run.
printf '%s\n' "$CONCURRENCE" >"$RUN_DIR/concurrence"
# La file de merge du run (#419). `merge.tsv` est le fichier qu'une reprise relit — voir plus bas,
# où elle est rechargée : ce qui a déjà été mergé ne l'est pas deux fois.
MERGE_TSV="$RUN_DIR/merge.tsv"
MERGE_LOG="$RUN_DIR/merge.log"

# renonce_au_run : retire le répertoire du run quand il ne s'y est RIEN passé (#180). Le `mkdir -p`
# ci-dessus a lieu avant de savoir s'il y aura seulement quelque chose à traiter : un backlog vide,
# un `queue.sh` en échec, et il reste un dossier horodaté qui ne porte qu'un plan sans ligne. Quatre
# de ces vestiges traînaient dans `.maestro/orchestrate/` — ce que #198 ne ramasse pas, son critère
# étant le répertoire strictement vide.
#
# Prudent par construction : il refuse dès qu'un autre fichier est là (une session, un bilan, un
# lanceur), donc il ne peut pas emporter un journal qui a servi — y compris dans le cas tordu où
# `--plan` désignerait le plan du run qu'on est en train d'écrire. Rend 1 s'il n'a rien retiré.
renonce_au_run() {
  local f
  for f in "$RUN_DIR"/* "$RUN_DIR"/.[!.]*; do
    [ -e "$f" ] || continue
    # `reprise-de` est posé avec le répertoire, avant qu'on sache s'il y aura quelque chose à
    # traiter : le compter comme une trace de travail retiendrait le vestige d'une reprise à vide.
    # La carte `pid` (#213) est dans le même cas — elle décrit le processus, pas son travail, tout
    # comme la file de la vue vivante (#290), qui décrit l'écran, et `concurrence` (#291), qui décrit
    # le régime du pilote.
    case "${f##*/}" in plan.tsv | reprise-de | pid | concurrence | .console) ;; *) return 1 ;; esac
  done
  rm -rf "$RUN_DIR" 2>/dev/null || return 1
  return 0
}

# --- Lancement détaché (#173) -------------------------------------------------------------------------
# `--detach` relance CE script, sans `--detach`, dans une console qui n'appartient plus au processus
# courant, puis rend la main. C'est ce qui permet à une session Claude Code de démarrer un run : le
# pilote reste un script shell dans SON PROPRE processus — il ne consomme aucun quota et n'est pas
# suspendu à la session, donc la limite d'usage ne l'emporte pas avec elle (cf. l'en-tête).
#
# Le plan n'est PAS calculé ici : c'est le run détaché qui le fige, une fois, avec le `--run-id`
# qu'on lui impose. Deux calculs (un ici, un là-bas) risqueraient de diverger.
#
# La commande n'est pas passée en ligne au shell de la console — les guillemets imbriqués sous
# `cmd /c start` sont un nid à erreurs. On écrit un lanceur dans le répertoire du run, que la console
# se contente d'exécuter : ce qui part est lisible, et rejouable tel quel à la main.
detacher() {
  local lanceur="$RUN_DIR/lancer.sh" journal="$RUN_DIR/run.log" arg
  {
    printf '#!/usr/bin/env bash\n'
    printf '# Lanceur du run %s, écrit par « run.sh --detach ». Rejouable tel quel.\n' "$RUN_ID"
    printf 'cd %q || exit 1\n' "$RACINE"
    # La fenêtre est bien un écran : le run doit y garder ses couleurs, que `tee` lui ferait perdre.
    printf 'export MAESTRO_ORCHESTRATE_COULEUR=1\n'
    # …et un écran où l'on peut redessiner (#240). Le descripteur 4 est ouvert ICI, AVANT le tube :
    # il désigne donc la fenêtre elle-même, là où le stdout du run n'est plus qu'un tube vers `tee`.
    # C'est par lui que passent les frames de la vue vivante — et c'est ce qui garde `run.log` propre,
    # le journal ne recevant que la trace permanente (le `sed` final ne retire que les séquences de
    # couleur, pas les déplacements de curseur qu'un redessin sur stdout y aurait déversés).
    printf 'exec 4>&1\n'
    printf 'export MAESTRO_ORCHESTRATE_CONSOLE_FD=4\n'
    # …et un journal joignable SANS passer par `tee` (#284). Deux usages, tous deux impossibles
    # autrement : y déposer une ligne qui n'a rien à faire à l'écran (le battement d'une session,
    # que le bloc vivant dit déjà en mieux), et écrire soi-même sur la console une ligne qui doit y
    # être — `tee` est un autre processus, et rien ne garantit qu'il écrira avant la frame suivante.
    # Deux écrivains sur le même fichier, mais tous deux en O_APPEND et ligne à ligne : `tee` vide
    # son tampon à chaque lecture, l'ordre du journal suit donc celui du run.
    printf 'exec 5>>%q\n' "$journal"
    printf 'export MAESTRO_ORCHESTRATE_TRACE_FD=5\n'
    printf 'bash %q' "$RACINE/scripts/orchestrate/run.sh"
    for arg in "$@"; do printf ' %q' "$arg"; done
    # `tee` garde la sortie lisible dans la fenêtre ET sur disque : une console qui se referme (ou
    # qu'on ferme) ne doit pas emporter la seule trace de ce qui s'est passé.
    printf ' 2>&1 | tee -a %q\n' "$journal"
    printf 'code=${PIPESTATUS[0]}\n'
    # Filet de dernier recours pour le curseur (#284) : `run.sh` le rend lui-même par un trap, mais
    # un trap ne s'exécute pas sur un SIGKILL — et c'est exactement ainsi qu'un run est arrêté par
    # un autre (§11.9). La fenêtre, elle, survit à son run : elle ne doit pas rester sans curseur.
    printf 'printf "\\033[?25h" >&4 2>/dev/null\n'
    # Le journal, lui, se relit plus tard et souvent par un outil : on l'y décolore une fois, à la
    # fin. Pendant le run il porte les codes, ce qu'un `tail -f` vers un terminal rend correctement.
    printf 'sed -i '\''s/\\x1b\\[[0-9;]*m//g'\'' %q 2>/dev/null\n' "$journal"
    printf 'printf "\\n--- run %s terminé (code %%s) ---\\n" "$code"\n' "$RUN_ID"
    # Sans pause, la fenêtre se refermerait sur le résumé sans laisser le lire. Pas de pause quand
    # l'entrée n'est pas un terminal : détaché sous Unix, le lanceur y resterait indéfiniment.
    printf '[ -t 0 ] && { printf "Entrée pour fermer cette fenêtre. "; read -r _; }\n'
    printf 'exit "$code"\n'
  } >"$lanceur" || return 1
  chmod +x "$lanceur" 2>/dev/null

  # Couture de test (#173) : la commande reçoit le chemin du lanceur au lieu qu'une vraie console
  # s'ouvre — c'est ce qui rend `--detach` vérifiable sans fenêtre ni quota.
  if [ -n "${MAESTRO_ORCHESTRATE_SPAWN:-}" ]; then
    "$MAESTRO_ORCHESTRATE_SPAWN" "$lanceur"
    return $?
  fi

  case "$(uname -s 2>/dev/null)" in
    MINGW* | MSYS* | CYGWIN*)
      # `start` ouvre une console détenue par l'explorateur, pas par ce shell. Le premier argument
      # est le TITRE de la fenêtre, pas la commande : l'omettre ferait passer le chemin de bash pour
      # un titre. Et `//c`, pas `/c` — MSYS convertirait « /c » en chemin de fichier.
      local bash_exe
      bash_exe="$(cygpath -w "$(command -v bash)" 2>/dev/null)" || return 1
      cmd //c start "Maestro - run $RUN_ID" "$bash_exe" "$lanceur"
      ;;
    *)
      # Pas de fenêtre à ouvrir ici : « détaché » veut dire hors du groupe de processus courant, la
      # sortie restant lisible dans run.log. `setsid` quand il existe, sinon `nohup`.
      if command -v setsid >/dev/null 2>&1; then
        setsid nohup bash "$lanceur" >/dev/null 2>&1 </dev/null &
      else
        nohup bash "$lanceur" >/dev/null 2>&1 </dev/null &
      fi
      ;;
  esac
}

if [ "$DETACH" = 1 ]; then
  args_enfant=()
  saute_valeur=0
  for a in ${ARGS_ORIG+"${ARGS_ORIG[@]}"}; do
    if [ "$saute_valeur" = 1 ]; then saute_valeur=0; continue; fi
    case "$a" in
      --detach | --detache | --détaché) continue ;;
      # Retiré ici, réimposé juste après : le lanceur doit porter le run-id une fois, pas deux.
      --run-id) saute_valeur=1; continue ;;
      # Même traitement, pour la même raison : « --resume » sans valeur a été résolu en un run-id
      # précis, et le lanceur doit porter CE run-là. Le relancer non résolu le ferait rechoisir dans
      # une liste qui aura changé — le run qu'on vient de créer y figurerait, entre autres.
      --resume | --reprendre) saute_valeur="$REPRISE_AVEC_VALEUR"; continue ;;
    esac
    args_enfant+=("$a")
  done
  # Le run-id est imposé : sans lui, le run détaché en tirerait un autre de l'horodatage et on
  # annoncerait un journal qui ne serait jamais écrit. Une valeur déjà passée par l'appelant est
  # reprise telle quelle — c'est celle qui a servi à créer RUN_DIR.
  args_enfant+=(--run-id "$RUN_ID")
  [ "$REPRISE" = 1 ] && args_enfant+=(--resume "$REPRISE_ID")

  if ! detacher ${args_enfant+"${args_enfant[@]}"}; then
    printf 'run.sh : le lancement détaché a échoué — le run n'\''a pas démarré.\n' >&2
    rm -rf "$RUN_DIR"
    exit 1
  fi

  printf '\n%sRun %s lancé dans une console détachée.%s\n' "$C_B" "$RUN_ID" "$C_0"
  [ "$REPRISE" = 1 ] && printf '  reprise    du run %s (son plan, rejoué)\n' "$REPRISE_ID"
  printf '  journal    %s\n' "$RUN_DIR"
  printf '  sortie     %s/run.log\n' "$RUN_DIR"
  printf '  suivre     tail -f %s/run.log\n' "$RUN_DIR"
  printf '  arrêter    touch %s\n' "$STOP"
  printf '  reprendre  bash scripts/orchestrate/run.sh --resume %s\n' "$RUN_ID"
  printf '\n%sCe que ce mode ne garantit pas%s : la console ne dépend plus de ce shell, mais rien\n' "$C_Y" "$C_0"
  printf 'n'\''assure qu'\''elle survive à un parent qui enfermerait ses descendants (job object Windows).\n'
  printf 'Si le run s'\''arrête avec lui, le plan reste : la commande « reprendre » le rejoue, les tickets\n'
  printf 'déjà livrés étant sautés d'\''eux-mêmes.\n'
  exit 0
fi

# --- La carte du pilote (#213) ------------------------------------------------------------------------
# ICI et pas plus haut : au-dessus, en mode détaché, c'est le processus APPELANT qui passait — sa
# carte serait périmée à la seconde où il rend la main, et le prochain run croirait avoir un mort à
# tuer. À partir de cette ligne, le processus courant EST le run.
#
# Le retrait passe par un trap : une sortie normale, un `exit` d'erreur ou un Ctrl-C laissent la
# place nette. Un SIGKILL, lui, n'exécute aucun trap — d'où la vérification d'identité côté
# `pilote_vivant`, qui est la vraie garantie. Une carte périmée ne fait jamais tuer personne.
if [ "$DRY" = 0 ]; then
  pilote_ecrit "$RUN_DIR" || printf 'run.sh : carte du pilote non écrite — ce run ne pourra pas être arrêté par un autre.\n' >&2
  # `vue_ferme` d'abord : le curseur caché par la vue doit revenir quoi qu'il arrive — sortie
  # normale, `exit` d'erreur ou Ctrl-C —, sans quoi la console reste amputée après le run.
  # `vue_purge` d'abord : ce que les sessions ont mis en file n'existe QUE là tant que le pilote ne
  # l'a pas repris (#290) — ni à l'écran, ni dans `run.log`. Une sortie d'erreur ou un Ctrl-C ne doit
  # pas emporter la dernière ligne d'un ticket avec lui.
  trap 'vue_purge; vue_ferme; pilote_retire "$RUN_DIR"' EXIT
fi

# --- Le plan, figé une fois --------------------------------------------------------------------------
if [ -n "$PLAN_IMPOSE" ]; then
  # `-r` et non `-f` : on ne fait que lire ce plan, et le refuser parce qu'il n'est pas un fichier
  # ORDINAIRE écarterait un tube ou une substitution de processus, qui conviennent très bien.
  [ -r "$PLAN_IMPOSE" ] || { printf 'run.sh : plan illisible ou introuvable — %s\n' "$PLAN_IMPOSE" >&2; exit 1; }
  cp "$PLAN_IMPOSE" "$PLAN"
else
  queue="$RACINE/scripts/orchestrate/queue.sh"
  [ -x "$queue" ] || [ -f "$queue" ] || {
    printf 'run.sh : %s absent — il porte le calcul de l'\''ordre (#168).\n' "$queue" >&2
    exit 1
  }
  if [ -n "$MILESTONE" ]; then
    bash "$queue" --milestone "$MILESTONE" >"$PLAN" || { renonce_au_run; exit 1; }
  else
    bash "$queue" >"$PLAN" || { renonce_au_run; exit 1; }
  fi
fi

nb_plan="$(grep -cv '^#' "$PLAN")"
printf '\n%sBoucle d'\''orchestration%s — run %s\n' "$C_B" "$C_0" "$RUN_ID"
[ "$REPRISE" = 1 ] && printf 'reprise du run %s — son plan, rejoué tel quel\n' "$REPRISE_ID"
# Les deux plafonds de session sont ANNONCÉS dans les deux sens (#286 pour le budget, #326 pour le
# délai) : « illimité » et « sans délai » sont des choix, pas des oublis, et relire un run doit dire
# lequel des deux régimes s'appliquait — un ticket coupé au plafond ne se distingue d'un échec de
# session que par cette ligne.
#
# Le DÉPÔT y figure pour la même raison (#341) : c'est là que les N PR vont s'ouvrir, et rien
# d'autre dans le journal ne le dirait.
printf 'dépôt : %s (%s)\n' "$(gl_forge_nom)" "$(gl_depot_courant)"
printf 'plan : %s ticket(s) · modèle %s · effort %s · %s · %s · %s\n' \
  "$nb_plan" "$MODELE" "$EFFORT" \
  "$([ -n "$BUDGET" ] && printf 'budget %s $/ticket' "$BUDGET" || printf 'budget illimité')" \
  "$([ "$TIMEOUT_S" -gt 0 ] && printf 'timeout %s/ticket' "$(duree_lisible "$TIMEOUT_S")" || printf 'sans délai')" \
  "$([ "$CONCURRENCE" -gt 1 ] && printf '%s en vol' "$CONCURRENCE" || printf 'séquentiel')"
printf 'journal : %s\n\n' "$RUN_DIR"

if [ "$nb_plan" -eq 0 ]; then
  printf 'Rien à traiter : le plan est vide.\n'
  renonce_au_run && printf 'Aucun journal laissé derrière : il n'\''aurait porté que ce plan vide.\n'
  exit 0
fi

grep -v '^#' "$PLAN" | while IFS=$'\t' read -r rang iid parent prio groupe titre; do
  printf '  %2s. #%-4s %-8s %s%s\n' "$rang" "$iid" "$prio" "$titre" \
    "$([ "$parent" != "-" ] && printf ' (lot de #%s)' "$parent")"
done
printf '\n'

if [ "$DRY" = 1 ]; then
  printf 'Mode --dry-run : rien n'\''a été lancé — « main » elle-même reste où elle est (#283 : un\n'
  printf 'vrai run l'\''avance d'\''abord sur origin/main, fetch + fast-forward, lib.sh sync-main).\n\n'
  printf 'Chaque ticket aurait été traité ainsi —\n'
  printf '  1. worktree dédié     bash scripts/git/worktree.sh <iid>\n'
  printf '  2. session dédiée     %s -p … --session-id <uuid> --settings scripts/orchestrate/settings.run.json\n' "$CLAUDE_BIN"
  printf '                        --permission-mode acceptEdits --model %s --effort %s%s\n' \
    "$MODELE" "$EFFORT" \
    "$([ -n "$BUDGET" ] && printf ' --max-budget-usd %s' "$BUDGET")"
  printf '  3. verdict            PR ouverte ET cycle de vie « En revue » (lu dans %s, pas dans la sortie)\n' "$(gl_forge_nom)"
  printf '  4. limite d'\''usage    attente jusqu'\''au reset, puis réouverture de la même session Claude\n'
  printf '  5. sur échec          lots suivants du même parent sautés, run poursuivi\n'
  printf '  6. run coupé          « run.sh --resume » rejoue CE plan et CETTE concurrence, tous les\n'
  printf '                        tickets en vol compris\n'
  # Le régime de concurrence est annoncé dans les deux sens, comme le budget juste au-dessus :
  # « séquentiel » est un choix, pas un oubli, et c'est le réglage qui change le plus ce qu'on
  # verra à l'écran.
  if [ "$CONCURRENCE" -gt 1 ]; then
    printf '  7. concurrence        jusqu'\''à %s tickets en vol — deux ne partent ensemble que si le plan\n' "$CONCURRENCE"
    printf '                        les dit indépendants (parents différents, ou même « groupe »)\n'
  else
    printf '  7. concurrence        1 — run séquentiel (--concurrence <n> pour en mener plusieurs)\n'
  fi
  rm -rf "$RUN_DIR"
  exit 0
fi

printf '# iid\tverdict\tmr\tduree_s\tcout_usd\traison\n' >"$RESUME"

# `main` remise à niveau avant de commencer (#283) : fetch + fast-forward, par le helper qui porte
# déjà ce geste (#205) — jamais un `git pull` réimplémenté ici.
#
# Ce n'est pas le code produit qui prenait du retard : chaque worktree part d'`origin/main`, que
# `worktree.sh` fetch juste avant de créer la branche. C'est la ref LOCALE `refs/heads/main`, que
# plus personne ne visite depuis #181 — et qu'un run fait vieillir plus vite que tout le reste,
# puisqu'il ouvre N PR destinées à être mergées. Elle n'avançait jusqu'ici qu'à l'intérieur d'une
# session (le /ticket-start du ticket, via `worktree.sh ensure`), donc pas du tout quand le run part
# sur un plan vide, saute tous ses tickets ou échoue avant le premier — le cas d'une nuit où c'est
# la seule chose qui tourne.
#
# AVANT le ramassage des worktrees, et pas après : celui-ci mesure le travail non sauvegardé contre
# `origin/main` (§9.2), et c'est le fetch de `sync-main` qui rend cette mesure juste.
#
# Best-effort comme les deux ménages qui suivent : muet quand `main` est déjà à jour, abstentions
# (main divergent, répertoire porteur sale) relayées telles quelles sur stderr, et JAMAIS fatales —
# une `main` locale en retard n'est pas une raison de ne pas traiter le backlog. Même interrupteur
# que /ticket-start : MAESTRO_SYNC_MAIN=0.
if [ "${MAESTRO_SYNC_MAIN:-1}" != 0 ]; then
  sortie_sync="$(bash "$RACINE/scripts/gitlab/lib.sh" sync-main 2>&1 </dev/null)"
  code_sync=$?
  if [ -n "$sortie_sync" ]; then
    if [ "$code_sync" -eq 0 ]; then
      printf '%s\n\n' "$sortie_sync"
    else
      printf '%s\n' "$sortie_sync" | sed 's/^/  /' >&2
    fi
  fi
fi

# Ramassage des worktrees soldés avant de commencer (#197). C'est ici que l'accumulation fait le plus
# mal : un worktree pèse ~535 Mo et ce run va en monter un par ticket, sans personne devant pour
# faire le ménage. Best-effort et muet quand il n'y a rien à retirer ; un ramassage impossible (gh
# hors ligne) ne doit pas empêcher un run de partir.
#
# Le même appel SIGNALE au passage les tickets « En cours » que plus personne ne mène (#328) : c'est
# précisément ici qu'un run en fabrique — une session coupée laisse son ticket « En cours » et
# assigné, donc écarté par `queue.sh` du plan de tous les runs suivants. Consultatif : le run ne
# reprend rien de lui-même (ce sera #329), il le dit dans sa console et dans son journal.
bash "$RACINE/scripts/git/worktree.sh" gc --auto </dev/null || true

# Ménage du journal, même esprit et même moment (#198) : sans lui, `.maestro/orchestrate/` ne fait
# que grossir — rien n'y a jamais rien supprimé. Le run COURANT est nommé explicitement pour n'être
# jamais candidat, et `|| true` vaut engagement : un ménage impossible ne fait pas échouer un run.
# L'ordre compte : le plan a DÉJÀ été copié dans ce run (plus haut), donc rejouer le plan d'un vieux
# run reste sans danger même si la rétention emporte le répertoire dont il sort.
if [ "${MAESTRO_ORCHESTRATE_JOURNAL_GC:-1}" != 0 ]; then
  bash "$RACINE/scripts/orchestrate/journal.sh" gc --auto --courant "$RUN_ID" </dev/null || true
fi

# --- La boucle ----------------------------------------------------------------------------------------
NB_OK=0
NB_ECHEC=0
NB_SAUTE=0
TRAITES=0
POSITION=0
PARENTS_ECHOUES=""
WORKTREES=""
# Non vide = plus aucun ticket ne part (#289). Quatre causes, toutes déjà là avant ce lot : le fichier
# STOP, le plafond `--max`, et les deux sorties d'urgence d'une session (limite hebdomadaire, arrêt
# demandé pendant l'attente) qui faisaient un `break 2`. Un `break` ne suffit plus : les tickets encore
# en vol tiennent un worktree et une session, il faut les laisser finir — on cesse de lancer, on vide,
# puis on rend le résumé.
ARRET_LANCEMENT=""
# L'origine du chrono du run, lue par le pied de la vue vivante (#240). `SECONDS` plutôt que `date` :
# la frame se redessine plusieurs fois par seconde, et sous MSYS un fork y coûterait plus cher que
# tout le reste du dessin.
RUN_DEBUT_S=$SECONDS
vue_ouvre

# Le coût est arrondi ICI, à l'unique endroit qui écrit le bilan : `status.sh` le relit tel quel, et
# une colonne à quinze décimales (« 10.686978499999995 ») ne dit rien de plus qu'à deux.
#
# Le PILOTE est le seul à l'appeler (#289), et c'est ce qui rend la ligne entière sans le moindre
# verrou : à N tickets en vol, un `printf >>` partagé par N sous-shells poserait la question de son
# atomicité — et la réponse dépendrait de la plateforme, MSYS émulant O_APPEND. Aucun sous-shell
# n'écrit ici : ils ne portent que la session, dont le pilote lit le résultat au retour.
consigne() { # <iid> <verdict> <mr> <duree> <cout> <raison>
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$(arrondi_cout "${5:-0}")" "$6" >>"$RESUME"
}

# --- La file de merge (#419, parent #413) -------------------------------------------------------------
# Le raisonnement est en tête de fichier ; ici, la mécanique. Un ticket jugé LIVRÉ entre en file ; le
# drain appelle `merge-mr` et ne fait que lire son code — la décision de merger vit là-bas et nulle
# part ailleurs (#415).
#
# Deux structures, et l'une n'est que la projection de l'autre : les tableaux `Q_*` portent la file
# dans l'ordre d'entrée, `MERGE_ETAT` en donne l'état par iid — c'est ce que la vue vivante lit, une
# fois par ligne et par recomposition, sans avoir à parcourir la file.
#
# `Q_VU` est l'horloge de chaque entrée, et c'est elle qui rend le drain « non bloquant » au sens
# où il faut l'entendre : une passe n'examine que les PR qu'elle n'a pas vues depuis l'intervalle,
# donc elle ne coûte RIEN la plupart du temps et ne fige jamais l'écran pour une réponse qui ne
# change pas plus vite qu'un pipeline. Une entrée neuve porte -1 : elle est examinée tout de suite.
# `Q_MRFIX` et `Q_COUT` sont au déblocage (#420) ce que `Q_ESSAIS` est au merge : le nombre de
# sessions `/mr-fix` jouées sur cette PR — ce que le plafond de deux borne — et ce qu'elles ont
# coûté. Le coût vit ICI et non dans `resume.tsv` : ce fichier-là a une ligne PAR TICKET, et tout ce
# qui le lit en dépend (le bilan de `status.sh`, la vue, et `reprend_en_vol`, qui déduit d'une ligne
# absente qu'un ticket était en vol à la coupure). Une ligne de plus au nom d'un ticket y ferait
# compter un traité de plus et, pour `reprend_en_vol`, mentirait sur ce que la coupure a interrompu.
Q_IID=(); Q_PR=(); Q_BRANCHE=(); Q_ETAT=(); Q_CODE=(); Q_ESSAIS=(); Q_RAISON=(); Q_VU=()
Q_MRFIX=(); Q_COUT=()
declare -A MERGE_ETAT=()

# merge_ecrit : la file, en entier, à chaque changement. Réécrire plutôt que d'ajouter parce qu'une
# ligne CHANGE d'état (attente → mergée) et qu'un journal en append demanderait de savoir laquelle
# des lignes d'un même iid fait foi. Le fichier tient en quelques lignes, l'écrivain est unique
# (le pilote, comme pour `resume.tsv`), et la reprise n'a qu'à le lire tel quel.
merge_ecrit() {
  [ -n "$MERGE_TSV" ] || return 0
  local i
  # Les deux colonnes du déblocage viennent APRÈS la cause, et pas avant : `cause` est le seul champ
  # de texte libre de la ligne, donc le seul qu'on ne veut pas voir bouger de place — un lecteur
  # d'avant #420 (`status.sh`, la reprise) lit ses sept champs et ignore la suite.
  {
    printf '# iid\tpr\tbranche\tetat\tcode\tessais\tcause\tmrfix\tcout\n'
    for ((i = 0; i < ${#Q_IID[@]}; i++)); do
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "${Q_IID[$i]}" "${Q_PR[$i]}" "${Q_BRANCHE[$i]}" \
        "${Q_ETAT[$i]}" "${Q_CODE[$i]}" "${Q_ESSAIS[$i]}" "${Q_RAISON[$i]}" \
        "${Q_MRFIX[$i]}" "${Q_COUT[$i]}"
    done
  } >"$MERGE_TSV.tmp" 2>/dev/null || return 0
  mv -f "$MERGE_TSV.tmp" "$MERGE_TSV" 2>/dev/null || true
  return 0
}

merge_index() { # <iid> -> l'indice dans la file, rien si elle ne le porte pas
  local i
  for ((i = 0; i < ${#Q_IID[@]}; i++)); do
    [ "${Q_IID[$i]}" = "$1" ] && { printf '%s' "$i"; return 0; }
  done
  return 1
}

# merge_enfile <iid> <pr> <branche> [<état> <cause> <mrfix> <coût>] : inscrit une PR dans la file.
# Idempotent — un ticket déjà en file (reprise, second verdict) n'y entre pas deux fois.
merge_enfile() { # <iid> <pr> <branche> [<état>] [<cause>] [<mrfix>] [<coût>]
  [ "$MERGE" = 1 ] || return 0
  local iid="$1" pr="$2" branche="$3" etat="${4:-attente}" cause="${5:--}"
  local mrfix="${6:-0}" cout="${7:-0}"
  case "$mrfix" in '' | *[!0-9]*) mrfix=0 ;; esac
  case "$cout" in '' | -) cout=0 ;; esac
  # Sans PR il n'y a rien à merger : un ticket livré sans PR n'existe pas (c'est le verdict qui le
  # dit), et une entrée sans numéro ferait échouer `merge-mr` pour une raison qui n'est pas la sienne.
  case "$pr" in '' | '-') return 0 ;; esac
  [ -n "$branche" ] || return 0
  merge_index "$iid" >/dev/null && return 0
  Q_IID+=("$iid"); Q_PR+=("$pr"); Q_BRANCHE+=("$branche")
  Q_ETAT+=("$etat"); Q_CODE+=('-'); Q_ESSAIS+=(0); Q_RAISON+=("$cause"); Q_VU+=(-1)
  Q_MRFIX+=("$mrfix"); Q_COUT+=("$cout")
  MERGE_ETAT["$iid"]="$etat"
  merge_ecrit
  return 0
}

# merge_cause <sortie de merge-mr> : la ligne qui porte la cause, réduite à ce qui l'explique.
# `merge-mr` préfixe ses refus de « ✗ »/« ⏳ » puis nomme la PR et sa branche avant un « : » — tout
# cela est déjà dans la file, seule la fin apprend quelque chose. Le premier « : » de la ligne est
# le bon : ni le nom d'une branche de ticket ni un numéro de PR n'en portent (une URL, si — elle
# arrive après).
merge_cause() {
  printf '%s\n' "$1" | grep -m1 -e '^✗' -e '^⏳' |
    sed 's/^[^:]*: *//; s/[[:space:]]\{1,\}/ /g; s/[[:space:]]*$//' | cut -c1-140
}

# merge_tente <index> [attendre] : UN appel à `merge-mr`, et la conduite que son code impose.
# Rend le code de `merge-mr` — 0 = mergée.
#
# `attendre` n'est vrai qu'au drain final : plus aucun ticket ne tourne, donc l'attente ne coûte que
# du temps de mur. Pendant le run elle coûterait un pilote qui ne moissonne plus, donc des sessions
# finies qui gardent leur créneau.
merge_tente() { # <index> [attendre]
  local i="$1" attendre="${2:-0}"
  local iid="${Q_IID[$i]}" branche="${Q_BRANCHE[$i]}" pr="${Q_PR[$i]}"
  local sortie code=0 cause
  Q_VU[$i]=$SECONDS
  if [ "$attendre" = 1 ]; then
    # Le verdict de `pipeline-wait` n'est pas lu : il ne juge pas la mergeabilité (il ne compare
    # même pas les sha — §8.3), il ne fait que rendre la main quand il y a quelque chose à décider.
    # C'est `merge-mr`, juste après, qui tranche, et lui seul.
    sortie="$(gl_pipeline_wait "$branche" 2>&1)" || true
    { printf -- '--- #%s (%s) : attente du pipeline\n' "$iid" "$branche"
      printf '%s\n' "$sortie"; } >>"$MERGE_LOG" 2>/dev/null || true
  fi
  sortie="$(gl_merge_mr "$branche" 2>&1)" || code=$?
  Q_ESSAIS[$i]=$((Q_ESSAIS[i] + 1))
  Q_CODE[$i]="$code"
  { printf -- '--- #%s (PR #%s, %s) : merge-mr a rendu %s\n' "$iid" "$pr" "$branche" "$code"
    printf '%s\n' "$sortie"; } >>"$MERGE_LOG" 2>/dev/null || true
  cause="$(merge_cause "$sortie")"
  case "$code" in
    0) Q_ETAT[$i]=mergee;  Q_RAISON[$i]='-' ;;
    # « pas encore rendu » (en cours, absent, ou périmé) : la seule réponse qui laisse en file.
    3) Q_ETAT[$i]=attente; Q_RAISON[$i]="${cause:-verdict de pipeline pas encore rendu}" ;;
    # 4 et 5 sont réparables — c'est `mrfix_relance` (#420) qui s'en saisit, en ouvrant une session
    # `/mr-fix` ; 6 est un geste humain, et rien ne le tente. Les trois sortent de la file : ni un
    # pipeline rouge ni un conflit ne se défont tout seuls, et y repasser à chaque passe coûterait
    # des appels pour reconfirmer ce qu'on sait déjà. Une PR débloquée y REVIENT (`attente`), et
    # c'est le seul chemin de retour.
    4) Q_ETAT[$i]=bloquee; Q_RAISON[$i]="${cause:-pipeline rouge}" ;;
    5) Q_ETAT[$i]=bloquee; Q_RAISON[$i]="${cause:-conflit avec origin/main}" ;;
    *) Q_ETAT[$i]=bloquee; Q_RAISON[$i]="${cause:-merge-mr a rendu $code}" ;;
  esac
  MERGE_ETAT["$iid"]="${Q_ETAT[$i]}"
  merge_ecrit
  return "$code"
}

# merge_annonce <index> : la ligne permanente d'un verdict de merge. Muette sur « attente » — un
# drain qui répète « pas encore » à chaque passe est un drain qu'on cesse de lire.
merge_annonce() { # <index>
  # Deux `local` et non un : dans un seul, `$i` désignerait encore la variable de l'appelant (SC2318).
  local i="$1"
  local iid="${Q_IID[$i]}" pr="${Q_PR[$i]}"
  case "${Q_ETAT[$i]}" in
    mergee)  dit '  %s⇈%s PR #%s mergée — #%s est livré et fermé.\n' "$C_G" "$C_0" "$pr" "$iid" ;;
    bloquee) dit '  %s⚠%s PR #%s (#%s) non mergée — %s\n' "$C_Y" "$C_0" "$pr" "$iid" "${Q_RAISON[$i]}" ;;
  esac
  return 0
}

# merge_ordre <indices…> : les mêmes indices, dans l'ordre le MOINS conflictuel (#416). Réservé au
# drain final — pendant le run l'ordre est celui d'entrée, et recalculer un graphe en n(n-1)/2
# `merge-tree` à chaque passe coûterait plus que ce qu'il ferait gagner. En dessous de deux entrées
# il n'y a pas d'ordre à calculer : on ne paie pas la lecture de la file de revue pour l'apprendre.
merge_ordre() {
  local -a idx=("$@")
  [ "${#idx[@]}" -gt 1 ] || { printf '%s\n' "${idx[@]}"; return 0; }
  local -a branches=()
  local i b rang ordonnees=""
  for i in "${idx[@]}"; do branches+=("${Q_BRANCHE[$i]}"); done
  local table
  table="$(gl_merge_order "${branches[@]}" 2>/dev/null)" || table=""
  if [ -z "$table" ]; then printf '%s\n' "${idx[@]}"; return 0; fi
  while IFS=$'\t' read -r rang b _; do
    case "$rang" in '#'* | '') continue ;; esac
    for i in "${idx[@]}"; do
      [ "${Q_BRANCHE[$i]}" = "$b" ] && { ordonnees="$ordonnees $i"; break; }
    done
  done <<<"$table"
  # Ce que `merge-order` a écarté (branche introuvable, hors convention) reste à traiter : le rendre
  # en queue vaut mieux que de le perdre — un ordre est une préférence, pas un filtre.
  for i in "${idx[@]}"; do
    case " $ordonnees " in *" $i "*) ;; *) ordonnees="$ordonnees $i" ;; esac
  done
  printf '%s\n' $ordonnees
  return 0
}

# merge_draine : une passe du drain AU FIL DE L'EAU. Elle s'arrête au premier merge réussi — un merge
# déplace `origin/main` et périme le verdict de conflit de toutes les autres PR, qui seront donc
# rejugées à la passe suivante et jamais sur une mesure d'avant.
merge_draine() {
  [ "$MERGE" = 1 ] || return 0
  local i
  for ((i = 0; i < ${#Q_IID[@]}; i++)); do
    [ "${Q_ETAT[$i]}" = attente ] || continue
    [ "${Q_VU[$i]}" -lt 0 ] || [ $((SECONDS - Q_VU[i])) -ge "$MERGE_INTERVALLE_S" ] || continue
    if merge_tente "$i"; then
      merge_annonce "$i"
      return 0
    fi
    [ "${Q_ETAT[$i]}" = bloquee ] && merge_annonce "$i"
  done
  return 0
}

# --- Débloquer une PR pendant le run : la session /mr-fix (#420, parent #413) --------------------------
# Le raisonnement est en tête de fichier ; ici, la mécanique. Elle est CELLE D'UN TICKET, et c'est
# tout son intérêt : `prepare_worktree` monte (ou retrouve) le worktree, `joue_session` porte les
# reprises après limite d'usage et le rendez-vous partagé de #291, `lance_session` passe le régime
# du run. Ce lot n'ajoute qu'une clé de journal, un prompt et un témoin — pas une seconde façon de
# faire tourner une session Claude, qui divergerait de la première au premier réglage ajouté.
MRFIX_EN_VOL=""     # les indices de la file dont une session de déblocage tourne
MRFIX_CLE=()        # par indice de file : la clé de journal de la session en cours
MRFIX_DEBUT=()      # par indice de file : SECONDS au lancement, pour la durée rendue au bilan
MRFIX_SESSIONS=0    # combien de sessions de déblocage ce run a ouvertes, tous tickets confondus

# mrfix_somme <a> <b> : deux montants en dollars, additionnés à deux décimales. `awk` parce que le
# shell ne sait pas additionner des flottants ; par l'ENVIRONNEMENT et non par `-v`, qui interprète
# les échappements (#340) — la règle vaut même quand on croit ne passer que des nombres, un champ
# JSON absent pouvant rendre tout autre chose.
mrfix_somme() { # <a> <b>
  MRFIX_A="${1:-0}" MRFIX_B="${2:-0}" LC_ALL=C awk 'BEGIN {
    printf "%.2f", (ENVIRON["MRFIX_A"] + 0) + (ENVIRON["MRFIX_B"] + 0) }' 2>/dev/null ||
    printf '%s' "${1:-0}"
}

# mrfix_cle <index> : la clé de journal de la PROCHAINE session de cet indice. La première porte le
# nom simple (`<iid>-mrfix`), les suivantes leur rang — sans quoi la seconde tentative écraserait le
# `.json` et le `.resultat.txt` de la première, c'est-à-dire précisément ce qu'on ira relire pour
# comprendre pourquoi la première n'a pas suffi.
mrfix_cle() { # <index>
  local i="$1"
  local n=$((Q_MRFIX[i] + 1))
  [ "$n" -le 1 ] && { printf '%s-mrfix' "${Q_IID[$i]}"; return 0; }
  printf '%s-mrfix%s' "${Q_IID[$i]}" "$n"
}

# mrfix_eligible <index> : cette PR peut-elle être débloquée maintenant ? Trois conditions, et la
# dernière est le plafond du ticket.
mrfix_eligible() { # <index>
  local i="$1"
  [ "$MRFIX" = 1 ] || return 1
  [ "${Q_ETAT[$i]}" = bloquee ] || return 1
  # 4 = pipeline rouge, 5 = conflit : les deux blocages que `/mr-fix` sait traiter, dans cet ordre.
  # Le 6 est un geste humain PAR DÉFINITION (#415) — lui envoyer une session, ce serait payer une
  # session entière pour qu'elle reconfirme qu'elle ne peut rien.
  case "${Q_CODE[$i]}" in 4 | 5) ;; *) return 1 ;; esac
  [ "${Q_MRFIX[$i]}" -lt "$MRFIX_MAX" ] || return 1
  return 0
}

# mrfix_lance <index> : ouvre la session, en SOUS-SHELL et avec témoin — exactement comme un ticket
# (#289), et pour la même raison : le pilote doit continuer à moissonner, à drainer et à tenir
# l'écran pendant qu'elle travaille. Rend 0 si une session est partie (un créneau est pris).
mrfix_lance() { # <index>
  local i="$1"
  local iid="${Q_IID[$i]}" pr="${Q_PR[$i]}" branche="${Q_BRANCHE[$i]}"
  local dest cle uuid temoin

  # Le worktree du ticket, remonté s'il a été ramassé entre-temps (`gc` passé par là, run repris) —
  # par le même chemin que les tickets, jamais un `git worktree add` réimplémenté ici. Idempotent :
  # sur un worktree déjà en place, `worktree.sh` le dit et rend son chemin.
  dest="$(prepare_worktree "$iid" "$branche" "$RUN_DIR/$iid-mrfix.worktree.log")"
  if [ -z "$dest" ] || [ ! -d "$dest" ]; then
    # Le compteur avance quand même : sans lui, un worktree qu'on ne sait pas monter serait retenté
    # à chaque passe du drain, indéfiniment et sans jamais rien apprendre de neuf.
    Q_MRFIX[$i]=$((Q_MRFIX[i] + 1))
    merge_ecrit
    dit '  %s⚠%s PR #%s (#%s) — worktree non monté, déblocage impossible (voir %s)\n' \
      "$C_Y" "$C_0" "$pr" "$iid" "$RUN_DIR/$iid-mrfix.worktree.log"
    return 1
  fi

  cle="$(mrfix_cle "$i")"
  uuid="$(uuid_du_ticket "$cle")"
  temoin="$RUN_DIR/$cle.fini"
  rm -f "$temoin" 2>/dev/null

  Q_MRFIX[$i]=$((Q_MRFIX[i] + 1))
  Q_ETAT[$i]=deblocage
  MERGE_ETAT["$iid"]=deblocage
  MRFIX_CLE[$i]="$cle"
  MRFIX_DEBUT[$i]=$SECONDS
  MRFIX_SESSIONS=$((MRFIX_SESSIONS + 1))
  merge_ecrit
  dit '  %s⟳%s PR #%s (#%s) — session /mr-fix %s/%s : %s\n' \
    "$C_Y" "$C_0" "$pr" "$iid" "${Q_MRFIX[$i]}" "$MRFIX_MAX" "${Q_RAISON[$i]}"

  (
    code=1
    # Le préfixe est posé ICI et non sur toute la fonction : les lignes de `mrfix_lance` nomment
    # déjà la PR et son ticket, celles de la session (attente d'une limite d'usage, reprise) non —
    # et à N sessions en vol, rien ne dirait de laquelle elles viennent.
    [ "$CONCURRENCE" -gt 1 ] && PREFIXE_TICKET="#$iid "
    # Mêmes guillemets simples et même raison qu'au lancement d'un ticket : `$code` doit s'évaluer
    # AU MOMENT du trap, `$temoin` étant un local visible d'ici.
    trap 'printf "%s\n" "$code" >"$temoin"' EXIT
    joue_session "$cle" "$dest" "$uuid" neuf mrfix "$pr"
    code=$?
  ) &
  MRFIX_EN_VOL="$MRFIX_EN_VOL $i"
  vue_recompose
  return 0
}

# mrfix_moissonne : ramasse les sessions de déblocage finies. Rend 0 dès qu'au moins une l'a été —
# même contrat que `moissonne`, et pour la même raison : c'est ce qui redonne la main au reste.
#
# Le verdict de la session n'est PAS lu dans sa prose, ni même dans son code de sortie : la PR
# retourne EN ATTENTE quoi qu'elle ait fait, et c'est `merge-mr` qui tranchera au prochain passage.
# Même règle que pour un ticket (#203, #415) — la seule chose qu'une session sait dire est ce
# qu'elle a tenté, jamais si la PR est mergeable. Une session qui a échoué coûte donc un appel de
# plus ; le plafond de deux borne ce que cette générosité peut coûter.
mrfix_moissonne() {
  local i reste="" pris=0 code cle cout duree verdict
  for i in $MRFIX_EN_VOL; do
    cle="${MRFIX_CLE[$i]}"
    if [ -s "$RUN_DIR/$cle.fini" ]; then
      read -r code <"$RUN_DIR/$cle.fini" || code=1
      case "${code:-}" in '' | *[!0-9]*) code=1 ;; esac
      rm -f "$RUN_DIR/$cle.fini" 2>/dev/null
    else
      reste="$reste $i"
      continue
    fi
    duree=$((SECONDS - MRFIX_DEBUT[i]))
    cout="$(champ_json "$RUN_DIR/$cle.json" total_cost_usd)"
    Q_COUT[$i]="$(mrfix_somme "${Q_COUT[$i]}" "${cout:-0}")"
    Q_ETAT[$i]=attente
    MERGE_ETAT["${Q_IID[$i]}"]=attente
    # À réexaminer TOUT DE SUITE : `Q_VU` est l'horloge du drain, et quelque chose vient justement
    # de changer sur cette PR — l'y laisser attendre l'intervalle serait attendre pour rien.
    Q_VU[$i]=-1
    merge_ecrit
    verdict=OK
    [ "$code" -eq 0 ] || verdict=ECHEC
    ecrit_resultat "$cle" "déblocage de la PR #${Q_PR[$i]} (#${Q_IID[$i]})" "$verdict" \
      "${Q_PR[$i]}" "$duree" "session de déblocage — le verdict de merge est rendu par merge-mr"
    compacte_flux "$cle"
    # Les deux sorties d'urgence d'une session arrêtent le RUN, et pas seulement ce qu'elle faisait :
    # exactement comme dans `juge_ticket`, dont c'est le pendant. Sans elles, un run dont le quota
    # hebdomadaire est épuisé continuerait de lancer des tickets qui mourraient à leur première
    # requête — la session de déblocage aurait appris la nouvelle et ne l'aurait dite à personne.
    if [ "$code" -eq 3 ]; then
      dit '\n%sLimite hebdomadaire%s — déclarée pendant le déblocage de la PR #%s.\n' \
        "$C_Y" "$C_0" "${Q_PR[$i]}"
      PLAFOND_ATTEINT=1
      ARRET_LANCEMENT="limite hebdomadaire"
    elif [ "$code" -eq 2 ]; then
      ARRET_LANCEMENT="arrêt demandé"
    fi
    if [ "$code" -eq 0 ]; then
      dit '  %s↩%s PR #%s (#%s) — session /mr-fix finie en %s, %s $ : on retente le merge.\n' \
        "$C_B" "$C_0" "${Q_PR[$i]}" "${Q_IID[$i]}" "$(duree_lisible "$duree")" \
        "$(arrondi_cout "${cout:-0}")"
    else
      # Ce n'est pas un verdict sur la PR : la session a pu abandonner proprement (résolution pas
      # claire, panne d'infrastructure), ce qui est un RÉSULTAT et laisse la branche intacte. Le
      # merge qui suit dira l'état réel, et c'est lui qui compte.
      dit '  %s⚠%s PR #%s (#%s) — session /mr-fix sortie en %s (journal : %s) : on retente quand même le merge.\n' \
        "$C_Y" "$C_0" "${Q_PR[$i]}" "${Q_IID[$i]}" "$code" "$RUN_DIR/$cle.resultat.txt"
    fi
    pris=1
  done
  MRFIX_EN_VOL="$reste"
  # Seulement si quelque chose a bougé : cette fonction est appelée cinq fois par seconde dans la
  # boucle d'attente, et `vue_recompose` relit `resume.tsv` et recompose TOUTES les lignes du plan.
  [ "$pris" = 1 ] || return 1
  vue_recompose
  return 0
}

# mrfix_relance : lance UNE session de déblocage si les conditions sont réunies. Une seule, pour la
# même raison qu'une passe du drain s'arrête au premier merge : ce qui vient de changer périme ce
# qu'on savait des autres entrées.
#
# Les trois refus qui précèdent l'éligibilité sont ceux du lancement d'un ticket, à l'identique :
# l'arrêt demandé (STOP arrête de LANCER), l'attente d'une limite d'usage en cours (ouvrir une
# session dans cette fenêtre, c'est brûler une reprise pour rien) et le créneau libre. Rend 0 si une
# session est partie.
mrfix_relance() {
  [ "$MRFIX" = 1 ] || return 1
  [ "$MERGE" = 1 ] || return 1
  [ -n "$ARRET_LANCEMENT" ] && return 1
  arret_demande >/dev/null 2>&1 && return 1
  limite_en_cours && return 1
  compte_creneaux
  [ "$CRENEAUX_PRIS" -ge "$CONCURRENCE" ] && return 1
  local i
  for ((i = 0; i < ${#Q_IID[@]}; i++)); do
    mrfix_eligible "$i" || continue
    mrfix_lance "$i" && return 0
  done
  return 1
}

# mrfix_attend : bloque jusqu'à ce que plus aucune session de déblocage ne tourne. Réservé au drain
# FINAL, où plus aucun ticket n'est en vol : ailleurs, bloquer le pilote reviendrait à cesser de
# moissonner et à laisser l'écran figé le temps d'une session entière.
mrfix_attend() {
  while [ -n "$MRFIX_EN_VOL" ]; do
    mrfix_moissonne && continue
    sleep "$ORDO_TICK"
  done
  return 0
}

# merge_draine_final : ce qui reste, une fois le plan épuisé. Deux différences avec la passe
# ordinaire, et une seule raison pour les deux — plus aucun ticket ne tourne :
#   · `pipeline-wait` est autorisé (l'attente ne coûte que du temps de mur), sauf si l'arrêt a été
#     demandé : qui demande STOP n'attend pas un quart d'heure par PR ;
#   · l'ordre est celui de `merge-order` (#416), recalculé après chaque merge parce que le merge
#     qu'on vient de faire a changé le graphe.
# Borné par un plafond global : une PR dont le pipeline ne rendra jamais rien ne doit pas retenir un
# run toute la nuit. Ce qui reste est nommé dans le résumé, avec sa cause.
MERGE_PLAFOND_S="${MAESTRO_ORCHESTRATE_MERGE_PLAFOND:-3600}"
case "$MERGE_PLAFOND_S" in '' | *[!0-9]*) MERGE_PLAFOND_S=3600 ;; esac
merge_draine_final() {
  [ "$MERGE" = 1 ] || return 0
  [ "${#Q_IID[@]}" -gt 0 ] || return 0
  local i restants reparables attendre=1 debut=$SECONDS progres
  [ -n "$ARRET_LANCEMENT" ] && attendre=0

  restants=""
  for ((i = 0; i < ${#Q_IID[@]}; i++)); do
    [ "${Q_ETAT[$i]}" = attente ] && restants="$restants $i"
    mrfix_eligible "$i" && restants="$restants $i"
  done
  [ -n "$restants" ] || return 0

  printf '\n%sDrain de la file de merge%s — %s PR en attente%s.\n' \
    "$C_B" "$C_0" "$(printf '%s' "$restants" | wc -w | tr -d ' ')" \
    "$([ "$attendre" = 1 ] && printf ', pipeline attendu' || printf ', sans attendre de pipeline (arrêt demandé)')"

  while :; do
    restants=""
    for ((i = 0; i < ${#Q_IID[@]}; i++)); do
      [ "${Q_ETAT[$i]}" = attente ] && restants="$restants $i"
    done
    # Une PR bloquée mais RÉPARABLE n'est pas « ce qui reste à merger » — c'est ce qui reste à
    # débloquer, et le drain final est le meilleur moment pour le faire : plus aucun ticket ne
    # tourne, donc la session ne prend le créneau de personne et rien ne se dispute l'écran (#420).
    reparables=""
    for ((i = 0; i < ${#Q_IID[@]}; i++)); do
      mrfix_eligible "$i" && reparables="$reparables $i"
    done
    [ -n "$restants$reparables" ] || break
    # STOP est relu ici aussi. Il n'interrompt toujours pas un merge en cours — la passe en cours va
    # à son terme —, mais il retire l'ATTENTE : qui demande l'arrêt pendant un drain n'attend pas un
    # quart d'heure de pipeline par PR. Ce qui est déjà vert part quand même, le reste est nommé.
    if [ "$attendre" = 1 ] && arret_demande; then
      printf '  %s⏹%s arrêt demandé — le drain finit sans attendre de pipeline.\n' "$C_Y" "$C_0"
      attendre=0
    fi
    # Le plafond se lit ENTRE deux passes, et il n'interrompt donc ni un merge ni une session de
    # déblocage en cours — même règle que STOP juste au-dessus. Une session `/mr-fix` peut à elle
    # seule le dépasser ; l'interrompre au milieu d'une résolution de conflit laisserait un merge
    # en cours dans le worktree pour n'économiser que du temps de mur.
    if [ $((SECONDS - debut)) -ge "$MERGE_PLAFOND_S" ]; then
      printf '  %s⚠%s plafond du drain atteint (%s) — le reste est laissé en file.\n' \
        "$C_Y" "$C_0" "$(duree_lisible "$MERGE_PLAFOND_S")"
      break
    fi
    progres=0
    # shellcheck disable=SC2046  # des indices numériques : le découpage de mots est voulu
    for i in $(merge_ordre $restants); do
      if merge_tente "$i" "$attendre"; then
        merge_annonce "$i"
        progres=1
        break
      fi
      [ "${Q_ETAT[$i]}" = bloquee ] && merge_annonce "$i"
    done
    # Rien n'a bougé côté merge : c'est le moment de débloquer, et pas avant — une PR qui se merge
    # telle quelle ne vaut pas une session. `mrfix_relance` refait le tri (les états ont pu changer
    # à l'instant) et pose les mêmes refus qu'au lancement d'un ticket, STOP compris : après un
    # arrêt demandé, plus aucune session ne part et le bilan nommera ce qui reste bloqué.
    if [ "$progres" = 0 ] && mrfix_relance; then
      mrfix_attend
      progres=1
    fi
    # Aucune PR n'a bougé : ce qui reste attend un pipeline qui n'est pas venu. Y repasser
    # rejouerait la même attente sur les mêmes PR, sans qu'aucune information nouvelle soit arrivée.
    [ "$progres" = 1 ] || break
  done
  return 0
}

# merge_bilan : ce que le run a fait de ses PR, ticket par ticket. Le résumé s'arrêtait à « PR
# ouverte », ce qui ne veut plus dire « travail livré » : une PR ouverte est désormais un état
# transitoire, et ne pas dire lesquelles le sont restées serait rendre un ✅ pour un travail à
# finir.
merge_bilan() {
  [ "${#Q_IID[@]}" -gt 0 ] || return 0
  local i n_m=0 n_a=0 n_b=0 cout_mrfix=0 essais
  for ((i = 0; i < ${#Q_IID[@]}; i++)); do
    case "${Q_ETAT[$i]}" in
      mergee) n_m=$((n_m + 1)) ;;
      attente) n_a=$((n_a + 1)) ;;
      *) n_b=$((n_b + 1)) ;;
    esac
    cout_mrfix="$(mrfix_somme "$cout_mrfix" "${Q_COUT[$i]}")"
  done
  printf '\n  Merges : %s%s mergée(s)%s · %s%s en attente%s · %s%s bloquée(s)%s\n' \
    "$C_G" "$n_m" "$C_0" "$C_Y" "$n_a" "$C_0" "$C_R" "$n_b" "$C_0"
  for ((i = 0; i < ${#Q_IID[@]}; i++)); do
    # Ce que le déblocage a coûté se dit SUR LA LIGNE de la PR, et pas seulement en total : c'est là
    # qu'on lit si une PR a mangé deux sessions, et le total seul ne le dirait pas.
    essais=''
    [ "${Q_MRFIX[$i]}" -gt 0 ] &&
      essais="$(printf ' [%s session(s) /mr-fix, %s $]' "${Q_MRFIX[$i]}" "$(arrondi_cout "${Q_COUT[$i]}")")"
    case "${Q_ETAT[$i]}" in
      mergee)  printf '    %s✓%s #%-5s PR #%-5s mergée%s\n' \
                 "$C_G" "$C_0" "${Q_IID[$i]}" "${Q_PR[$i]}" "$essais" ;;
      attente) printf '    %s⏳%s #%-5s PR #%-5s en attente — %s%s\n' \
                 "$C_Y" "$C_0" "${Q_IID[$i]}" "${Q_PR[$i]}" "${Q_RAISON[$i]}" "$essais" ;;
      *)       printf '    %s✗%s #%-5s PR #%-5s bloquée — %s%s%s\n' \
                 "$C_R" "$C_0" "${Q_IID[$i]}" "${Q_PR[$i]}" "${Q_RAISON[$i]}" "$essais" \
                 "$([ "${Q_MRFIX[$i]}" -ge "$MRFIX_MAX" ] && printf ' — plafond de %s tentative(s) atteint' "$MRFIX_MAX")" ;;
    esac
  done
  # Le coût des sessions de déblocage ne se cache pas dans le total des tickets : il est rendu ICI,
  # à part, parce qu'il ne se compte pas au même endroit (voir `Q_COUT`) et qu'il répond à une autre
  # question — ce que le run a payé pour réparer ce qu'il avait lui-même bloqué.
  [ "$MRFIX_SESSIONS" -gt 0 ] &&
    printf '    déblocages : %s session(s) /mr-fix, %s $ au total\n' \
      "$MRFIX_SESSIONS" "$(arrondi_cout "$cout_mrfix")"
  [ "$n_b" -gt 0 ] && printf '    à débloquer, une PR à la fois : /mr-fix <pr>\n'
  printf '    détail des appels : %s\n' "$MERGE_LOG"
  return 0
}

# La file du run REPRIS, rechargée (#419, #204). Sans elle, une reprise ne mergerait jamais ce que
# le run coupé avait livré : ces tickets-là sont « En revue », donc `remplit_les_creneaux` les saute
# (« le plan datait »), donc `juge_ticket` ne les inscrit pas — ils sortiraient du run par la porte
# de derrière, PR ouverte et personne pour la merger.
#
# Ce qui était MERGÉ le reste : on le recharge tel quel, et il n'est jamais rejoué. Ce qui ne
# l'était pas — en attente comme bloqué — revient EN ATTENTE : entre les deux runs, un pipeline a pu
# rendre son verdict et un /mr-fix a pu passer. Un `merge-mr` de plus est le prix d'une question
# qu'on ne peut pas trancher sans la poser ; le garder bloqué serait trancher sur une mesure d'hier.
#
# Le compte de sessions `/mr-fix` (#420) SUIT, lui, et c'est le seul champ qu'une reprise ne remet
# pas à zéro : il compte ce qu'on a déjà dépensé à débloquer cette PR-là, et le plafond de deux
# n'aurait plus de sens si une reprise le rendait. C'est le raisonnement du plafond de reprises de
# #327 — un compteur qu'un redémarrage remet à zéro est un compteur qui n'existe pas.
if [ "$MERGE" = 1 ] && [ "$REPRISE" = 1 ] && [ -r "$REPRISE_DIR/merge.tsv" ]; then
  while IFS=$'\t' read -r m_iid m_pr m_branche m_etat _ _ m_cause m_mrfix m_cout; do
    case "$m_iid" in '#'* | '') continue ;; esac
    [ -n "${m_branche:-}" ] || continue
    case "${m_etat:-}" in
      mergee) merge_enfile "$m_iid" "$m_pr" "$m_branche" mergee '-' "${m_mrfix:-0}" "${m_cout:-0}" ;;
      *)      merge_enfile "$m_iid" "$m_pr" "$m_branche" attente "${m_cause:--}" \
                "${m_mrfix:-0}" "${m_cout:-0}" ;;
    esac
  done <"$REPRISE_DIR/merge.tsv"
  if [ "${#Q_IID[@]}" -gt 0 ]; then
    printf '%sFile de merge reprise%s de %s : %s PR, dont %s déjà mergée(s).\n\n' \
      "$C_B" "$C_0" "$REPRISE_ID" "${#Q_IID[@]}" \
      "$(printf '%s\n' "${Q_ETAT[@]}" | grep -c '^mergee$')"
  fi
fi

# --- Le plan, en mémoire (#289) -----------------------------------------------------------------------
# La boucle lisait le plan ligne à ligne sur le descripteur 3. Un ordonnanceur ne peut pas : quand un
# créneau se libère, il doit reprendre le prochain ticket ÉLIGIBLE, donc revenir sur des lignes qu'il a
# déjà vues et laissées de côté. Le plan tient en quelques dizaines de lignes : on le charge, et le
# descripteur 3 disparaît avec son piège (les enfants en héritaient, et l'un d'eux aurait pu le lire).
P_IID=(); P_PARENT=(); P_GROUPE=(); P_TITRE=(); P_ETAT=(); P_ECHEANCE=(); P_RANG=()
P_BRANCHE=(); P_DEST=(); P_DEBUT=(); P_REPRIS=()
while IFS=$'\t' read -r rang iid parent _ groupe titre; do
  case "$rang" in '#'*) continue ;; esac
  [ -n "${iid:-}" ] || continue
  P_IID+=("$iid"); P_PARENT+=("$parent"); P_GROUPE+=("$groupe"); P_TITRE+=("$titre")
  # Le `rang` du plan est gardé (#290) : la vue en a besoin pour toutes ses lignes à chaque frame,
  # et le relire du fichier — ce que faisait `vue_prepare` — n'est plus tenable quand la
  # recomposition suit les départs et les verdicts de N tickets au lieu d'un.
  P_RANG+=("$rang")
  P_ETAT+=(attente); P_ECHEANCE+=(0); P_REPRIS+=(0)
  P_BRANCHE+=(""); P_DEST+=(""); P_DEBUT+=(0)
done < <(grep -v '^#' "$PLAN")
NB_ENTREES=${#P_IID[@]}

# La colonne `groupe` (#288) n'existe que dans les plans écrits depuis ce lot-là. Un plan ANTÉRIEUR,
# rejoué par `--resume`, en a cinq : son titre se lit alors dans `groupe`, et l'indépendance ne s'y
# lit nulle part. On compte donc les colonnes de sa première ligne de DONNÉES — l'en-tête peut manquer
# à un plan écrit à la main — et on retombe sur le run séquentiel, seul régime sûr : deviner
# l'indépendance à partir de titres ferait partir ensemble deux lots qui se suivent.
if [ "$CONCURRENCE" -gt 1 ]; then
  colonnes_plan="$(grep -v '^#' "$PLAN" | head -1 | awk -F'\t' '{ print NF }')"
  case "${colonnes_plan:-}" in '' | *[!0-9]*) colonnes_plan=0 ;; esac
  if [ "$colonnes_plan" -lt 6 ]; then
    printf '%s⚠%s ce plan est antérieur à la colonne « groupe » (#288) : rien n'\''y dit ce qui est\n' \
      "$C_Y" "$C_0"
    printf '  indépendant. Concurrence ramenée à 1 — le run reste séquentiel.\n\n'
    CONCURRENCE=1
  fi
fi

if [ "$CONCURRENCE" -gt 1 ]; then
  printf '%s%s tickets en vol%s — deux ne partent ensemble que si le plan les dit indépendants.\n' \
    "$C_B" "$CONCURRENCE" "$C_0"
  # La limite qui reste du chantier, dite une fois plutôt que découverte à l'écran. Celle de la vue
  # est levée (#290 rend les N tickets en vol) et celle de l'attente aussi (#291 la partage) : ce que
  # le quota partagé coûte est le seul point qu'aucun lot ne rattrape — le mur est divisé, jamais la
  # fenêtre.
  printf '  · toutes les sessions tirent sur le MÊME quota : la fenêtre de 5 h part %s fois plus vite,\n' \
    "$CONCURRENCE"
  printf '    Une limite d'\''usage ne se paie qu'\''une fois — attente partagée, puis chaque session\n'
  printf '    coupée est rouverte par son uuid (#291).\n\n'
fi

# --- L'indépendance, lue dans le plan (#289) ----------------------------------------------------------
# Rien n'est recalculé ici : la règle est celle que `queue.sh` a figée dans la colonne `groupe` (#288,
# docs/10 §11.2), et la reformuler à chaud exposerait les deux à diverger — c'est précisément ce que
# #288 s'était donné pour but d'éviter en la figeant dans le plan.
#
#   deux tickets sont indépendants si leurs `parent` DIFFÈRENT, ou si leur `groupe` est IDENTIQUE.
#
# Les deux moitiés comptent. Un ticket hors lot porte `parent = -` comme tous les autres tickets hors
# lot : la première moitié ne les départage pas, c'est leur `groupe` commun (« - ») qui les rend
# indépendants entre eux. Et deux lots d'un même parent ne partent ensemble que dans la même vague.
independants() { # <index a> <index b>
  [ "${P_PARENT[$1]}" != "${P_PARENT[$2]}" ] && return 0
  [ "${P_GROUPE[$1]}" = "${P_GROUPE[$2]}" ] && return 0
  return 1
}

# eligible <index> : 0 si ce ticket peut partir MAINTENANT, c'est-à-dire s'il est indépendant de tout
# ce qui est déjà en vol. À un seul créneau la question ne se pose pas — rien n'est en vol quand elle
# se pose —, la fonction rend 0 sans rien parcourir et le run séquentiel ne paie pas l'ordonnanceur.
eligible() { # <index>
  local j
  for j in $EN_VOL; do
    independants "$1" "$j" || return 1
  done
  return 0
}

# --- La partie longue d'un ticket : sa session (#289) -------------------------------------------------
# C'est LA SEULE chose qui part dans un sous-shell — parce que c'est la seule qui dure. Tout ce qui
# touche à l'état du run (compteurs, cascade, bilan) reste au pilote, qui n'a donc rien à recoller au
# retour : un code suffit.
#
#   0  la session a rendu la main — le verdict est à lire dans GitLab
#   1  idem, mais interrompue par le timeout — le pilote le dira dans la raison
#   2  arrêt demandé (fichier STOP) pendant l'attente d'une limite d'usage
#   3  plafond d'attente dépassé : c'est la limite hebdomadaire, le run s'arrête
#
# 2 et 3 remplacent les `break 2` d'avant ce lot : un `break` depuis un sous-shell ne sortirait que de
# lui, et il n'y a plus une boucle à quitter mais N tickets à laisser finir.
joue_session() { # <clé> <dest> <uuid> <mode> [<tâche>] [<cible>]
  local iid="$1" dest="$2" uuid="$3" mode="$4" tache="${5:-ticket}" cible="${6:-$1}"
  local reprises=0 attente_cumulee=0 code delai
  local origine rendez_vous fin annonceur ouvreur attendu

  while :; do
    lance_session "$iid" "$dest" "$uuid" "$mode" "$tache" "$cible"
    code=$?
    # Plus rien à effacer ici : une session n'écrit plus à l'écran (#290). Ses lignes permanentes
    # passent par la file de `dit`, que le pilote vide entre deux frames — c'est lui qui retire le
    # bloc avant de les imprimer, et lui seul.

    # 124 n'est un timeout que si l'on en a posé un (#326) : sans `OPT_TIMEOUT`, ce code vient du CLI
    # lui-même et n'a pas à être traduit en « session interrompue au bout de 0s » — il suit alors la
    # voie ordinaire d'un échec de session.
    if [ "$code" -eq 124 ] && [ "$TIMEOUT_S" -gt 0 ]; then
      dit '  %s✗%s session interrompue au bout de %s (timeout)\n' \
        "$C_R" "$C_0" "$(duree_lisible "$TIMEOUT_S")"
      bilan_des_reprises "$reprises" "$attente_cumulee"
      return 1
    fi

    # Une session sortie en 0 est allée au bout de son tour : rien ne l'a coupée, et il n'y a rien à
    # reprendre. On passe droit au verdict GitLab. Sans ce garde-fou, tout faux positif de la
    # détection renvoyait en attente un ticket DÉJÀ LIVRÉ, sans jamais lire ce verdict (#203).
    if [ "$code" -eq 0 ]; then
      bilan_des_reprises "$reprises" "$attente_cumulee"
      return 0
    fi

    # Une limite d'usage n'est pas un échec du ticket : c'est une pause. On attend, puis on reprend
    # LA MÊME session — le travail déjà fait reste dans son contexte.
    if ! delai="$(delai_avant_reprise "$RUN_DIR/$iid.json" "$RUN_DIR/$iid.jsonl" "$RUN_DIR/$iid.log")"; then
      bilan_des_reprises "$reprises" "$attente_cumulee"
      return 0
    fi

    if [ "$reprises" -ge "$MAX_REPRISES" ]; then
      dit '  %s✗%s limite d'\''usage encore atteinte après %s reprise(s) — on passe au ticket suivant.\n' \
        "$C_R" "$C_0" "$reprises"
      bilan_des_reprises "$reprises" "$attente_cumulee"
      return 0
    fi

    # L'attente est celle DU RUN et non de ce ticket (#291) : la limite tombe sur toutes les sessions
    # en vol, et c'est la meilleure information disponible — celle qui porte une heure de reset — qui
    # doit valoir pour toutes. Le délai retenu est donc celui du rendez-vous, pas celui qu'on vient de
    # calculer : rejoindre une attente déjà entamée, c'est n'en payer que ce qu'il en reste.
    origine="$(source_de_limite "$RUN_DIR/$iid.json" "$RUN_DIR/$iid.jsonl" "$RUN_DIR/$iid.log")"
    ouvreur=0
    rendez_vous="$(limite_partagee "$iid" "$delai" "$origine")" || ouvreur=$?
    fin="${rendez_vous%% *}"; annonceur="${rendez_vous##* }"
    delai=$(( fin - $(date +%s) ))
    [ "$delai" -lt 0 ] && delai=0
    # `dit` et non `trace` (#292) : #291 avait choisi `trace` pour la raison juste d'alors — la frame
    # suit immédiatement, et une ligne passée par `tee` pouvait arriver après elle et dédoubler le
    # bloc pour toute la durée de l'attente. Depuis #290 la prémisse a changé deux fois : `dit` ne
    # passe plus par `tee` (il met en FILE, que le pilote vide entre deux frames), et `trace` — qui
    # écrit sur l'écran — est devenu le geste qu'un sous-shell ne doit plus faire. Il le faisait sans
    # protection : `vue_efface` s'appuie sur `VUE_HAUT`, désormais une variable du PILOTE, dont la
    # session n'a qu'une copie figée au fork ; la ligne s'écrivait donc SOUS un bloc qu'on n'avait pas
    # retiré, et la frame suivante comptait ses rangées depuis le mauvais endroit.
    if [ "$ouvreur" = 0 ]; then
      dit '  %slimite d'\''usage atteinte%s — attente de %s avant reprise (fin vers %s).\n' \
        "$C_Y" "$C_0" "$(duree_lisible "$delai")" \
        "$(date -d "@$fin" '+%H:%M' 2>/dev/null || echo '?')"
    else
      dit '  %slimite d'\''usage atteinte%s — rejoint l'\''attente du run ouverte par #%s (reprise vers %s).\n' \
        "$C_Y" "$C_0" "$annonceur" "$(date -d "@$fin" '+%H:%M' 2>/dev/null || echo '?')"
    fi

    attente_cumulee=$((attente_cumulee + delai))
    if [ "$attente_cumulee" -gt "$PLAFOND_ATTENTE_S" ]; then
      # Le plafond est celui du RUN : la session qui l'atteint la première le déclare pour tout le
      # monde. Sans ce témoin, les N-1 autres continueraient de dormir des heures sur une limite
      # hebdomadaire dont le run a déjà tiré les conséquences — et le pilote les attendrait.
      printf '%s\n' "$iid" >"$RUN_DIR/.plafond" 2>/dev/null || true
      dit '\n%sLimite hebdomadaire%s — %s d'\''attente cumulée sur #%s dépassent le plafond de %s.\n' \
        "$C_Y" "$C_0" "$(duree_lisible "$attente_cumulee")" "$iid" "$(duree_lisible "$PLAFOND_ATTENTE_S")"
      dit 'Ce n'\''est plus une fenêtre de 5 h : le run s'\''arrête proprement, à relancer plus tard.\n'
      bilan_des_reprises "$reprises" "$attente_cumulee"
      return 3
    fi

    attendu=0
    patiente "$iid" "$fin" "$origine" || attendu=$?
    if [ "$attendu" = 1 ]; then
      dit '  arrêt demandé pendant l'\''attente — run interrompu.\n'
      bilan_des_reprises "$reprises" "$attente_cumulee"
      return 2
    fi
    if [ "$attendu" = 2 ]; then
      dit '  limite hebdomadaire déclarée sur #%s — cette session s'\''arrête aussi.\n' \
        "$(cat "$RUN_DIR/.plafond" 2>/dev/null || printf '?')"
      bilan_des_reprises "$reprises" "$attente_cumulee"
      return 3
    fi

    reprises=$((reprises + 1))
    mode=reprise
    # La ligne va dans `run.log` ; la vue, elle, garde l'état sous les yeux tant que la session
    # rouverte n'a pas appelé son premier outil — l'écran ne doit pas laisser croire à un départ.
    VUE_REPRISE="reprise $reprises/$MAX_REPRISES après limite d'usage"
    dit '  reprise %s/%s de la session #%s…\n' "$reprises" "$MAX_REPRISES" "$iid"
  done
}

# Le décompte des reprises est dit par le ticket lui-même : le pilote ne le connaît pas, et c'est une
# information sur la session, pas sur le run.
bilan_des_reprises() { # <reprises> <attente cumulée>
  [ "${1:-0}" -eq 0 ] && return 0
  dit '  (%s reprise(s) après limite d'\''usage, %s d'\''attente)\n' "$1" "$(duree_lisible "$2")"
  return 0
}

# --- Lancer un ticket ---------------------------------------------------------------------------------
# Tout le pré-vol est ICI, dans le pilote, et volontairement SÉRIALISÉ : résolution de la branche,
# montage du worktree, uuid de session. Deux raisons, pas une —
#   · `git worktree add` écrit dans le dépôt partagé (refs, `.git/worktrees`) et prend ses verrous.
#     N montages simultanés sur le même clone, c'est un « cannot lock ref » au hasard ; et les
#     quelques minutes d'installation se noient de toute façon dans une session qui en dure soixante ;
#   · les échecs de pré-vol (branche introuvable, worktree non monté) comptent pour `--max` et
#     nourrissent la cascade : les garder là où vivent les compteurs évite tout aller-retour.
#
# Rend 0 si un ticket est parti (un créneau est pris), 1 sinon — il a alors DÉJÀ son verdict.
lance_ticket() { # <index>
  local i="$1"
  # Deux `local` et non un : dans un seul, `$i` désignerait encore la variable de l'appelant — ici
  # elle vaut la même chose, ce qui rendrait le jour où elle ne la vaudrait plus très difficile à voir.
  local iid="${P_IID[$i]}" titre="${P_TITRE[$i]}"
  local branche dest mode uuid temoin PREFIXE_TICKET=""
  [ "$CONCURRENCE" -gt 1 ] && PREFIXE_TICKET="#$iid "

  branche="$(gl_branch_for "$iid" 2>/dev/null)"
  if [ -z "$branche" ]; then
    dit '  %s✗%s #%-4s branche introuvable (label type:: absent ?)\n' "$C_R" "$C_0" "$iid"
    solde_ticket "$i" ECHEC - 0 0 "nom de branche non résolu"
    return 1
  fi

  # `[position/total]` : la POSITION DANS LE PLAN (#230), et non un compteur qui avance — à N en vol
  # les tickets ne se prennent plus dans l'ordre, et un compteur dirait la position d'un autre. Le
  # cumul du run, lui, est au pied du bloc, où il vaut pour tous les tickets à la fois.
  dit '%s[%s/%s] #%s — %s%s\n' "$C_B" "$POSITION" "$nb_plan" "$iid" "$titre" "$C_0"

  # 1. Le worktree : un répertoire de travail et des ports par ticket (docs/10 §9), pour que le
  #    clone principal reste utilisable pendant que le run tourne.
  dest="$(prepare_worktree "$iid" "$branche" "$RUN_DIR/$iid.worktree.log")"
  if [ -z "$dest" ] || [ ! -d "$dest" ]; then
    dit '  %s✗%s worktree de « %s » non monté — voir %s\n' \
      "$C_R" "$C_0" "$branche" "$RUN_DIR/$iid.worktree.log"
    solde_ticket "$i" ECHEC - 0 0 "worktree non monté"
    return 1
  fi
  WORKTREES="$WORKTREES $iid"
  dit '  worktree : %s\n' "$dest"

  # 2. La session dédiée, avec reprise automatique si la limite d'usage tombe au milieu (#171).
  #    Un ticket repris en vol rouvre LA SESSION de la coupure : son uuid est recopié du journal
  #    repris AVANT `uuid_du_ticket`, qui en générerait un neuf sinon — et repartir à froid ferait
  #    repayer un contexte déjà constitué. Si elle n'est plus reprenable, `lance_session` redémarre
  #    tout seul à froid : le prompt est idempotent et le travail commité est sur la branche.
  mode=neuf
  if [ "${P_REPRIS[$i]}" = 1 ] && cp "$REPRISE_DIR/$iid.session" "$RUN_DIR/$iid.session" 2>/dev/null; then
    mode=reprise
    dit '  session de la coupure rouverte (%s)\n' "$(cat "$RUN_DIR/$iid.session")"
  fi
  uuid="$(uuid_du_ticket "$iid")"

  P_BRANCHE[$i]="$branche"
  P_DEST[$i]="$dest"
  P_DEBUT[$i]=$SECONDS
  # Le chrono du BATTEMENT suit le ticket : posé ici, il est capté par le sous-shell au fork et
  # traverse les reprises de session. Celui de la vue, lui, est `P_DEBUT` — le pilote le lit dans le
  # tableau, donc sans dépendre d'une variable que le ticket suivant écraserait (#290).
  VUE_DEBUT_TICKET=$SECONDS
  VUE_REPRISE=""
  # En vol AVANT la recomposition : c'est `P_ETAT` qui dit à la vue quelles lignes sont vivantes.
  P_ETAT[$i]=vol

  # La checklist du plan, recalculée à chaque départ et à chaque verdict (#240) : les verdicts déjà
  # rendus viennent de `resume.tsv`, le reste du plan des tableaux chargés plus haut. En plein texte
  # sur stdout SEULEMENT quand rien ne peut être redessiné : avec une console, le bloc vivant la
  # porte déjà, et l'imprimer en double ferait défiler deux fois la même chose. Ce que `run.log`
  # garde alors, ce sont l'en-tête du ticket, les battements et le verdict — la trace permanente,
  # qui suffit à relire un run.
  vue_recompose
  vue_active || vue_texte

  # Le sous-shell rend son code par un TÉMOIN, pas par `wait` : bash ne sait pas dire « lequel de mes
  # enfants vient de finir » de façon portable (`wait -n -p` demande bash 5.1), et un `kill -0`
  # réussit encore sur un zombie. Le fichier, lui, répond aux deux questions à la fois — qui, et avec
  # quel code — et c'est déjà le canal de tout le reste du script entre sous-shells (`<iid>.vue`,
  # `.session`, `.json`). Écrit par un trap EXIT : une session tuée par un signal doit rendre la main
  # au pilote, pas le laisser attendre un témoin qui ne viendra jamais.
  temoin="$RUN_DIR/$iid.fini"
  rm -f "$temoin" 2>/dev/null
  (
    code=1
    # Guillemets SIMPLES : `$code` doit s'évaluer AU MOMENT du trap, pas à sa pose. `$temoin` est un
    # local de cette fonction, donc visible ici — le nommer plutôt que l'interpoler évite de recoller
    # un chemin dans une chaîne entre guillemets, où une apostrophe le couperait en deux.
    trap 'printf "%s\n" "$code" >"$temoin"' EXIT
    joue_session "$iid" "$dest" "$uuid" "$mode"
    code=$?
  ) &
  # Le PID n'est délibérément pas gardé : rien ne le lirait. `wait <pid>` bloquerait sur un enfant
  # encore vivant, et `kill -0` réussit sur un zombie — le témoin répond aux deux questions que le
  # PID ne sait pas trancher, et le `wait` final récolte tout le monde d'un coup.
  # Le garde-fou anti-blocage : un sous-shell emporté par un SIGKILL n'exécute aucun trap, ne laisse
  # donc aucun témoin, et le pilote l'attendrait indéfiniment. Passé le temps qu'un ticket peut
  # légitimement prendre — ses sessions successives, plus tout ce qu'une limite d'usage l'autorise à
  # attendre, plus une marge — sa place est reprise et il est compté en échec. Jamais atteint en
  # régime normal : le `timeout` de chaque session en est très loin.
  # Sans délai de session (#326), cette échéance n'a plus de quoi se calculer — et AUCUN plafond de
  # remplacement n'est inventé ici : en poser un « raisonnable » recréerait exactement le défaut
  # qu'on vient de supprimer, un ticket tué en plein travail, mais côté pilote et sans même une
  # raison lisible. 0 vaut donc « pas d'échéance » (cf. `moissonne`), et le blocage qu'elle couvrait
  # redevient ce qu'il était avant d'être outillé : un run qu'on arrête par STOP ou par Ctrl-C.
  if [ "$TIMEOUT_S" -gt 0 ]; then
    P_ECHEANCE[$i]=$((SECONDS + TIMEOUT_S * (MAX_REPRISES + 1) + PLAFOND_ATTENTE_S + 600))
  else
    P_ECHEANCE[$i]=0
  fi
  EN_VOL="$EN_VOL $i"
  return 0
}

# --- Solder un ticket ---------------------------------------------------------------------------------
# L'unique endroit qui écrit une ligne de bilan, incrémente un compteur et nourrit la cascade. Qu'il
# ait échoué au pré-vol, rendu son verdict ou été sauté, un ticket passe par ici — c'est ce qui garde
# `resume.tsv`, `--max` et la cascade justes quel que soit le nombre de tickets en vol.
solde_ticket() { # <index> <verdict> <mr> <duree> <cout> <raison>
  local i="$1" verdict="$2"
  consigne "${P_IID[$i]}" "$verdict" "$3" "$4" "$5" "$6"
  P_ETAT[$i]=fini
  case "$verdict" in
    OK) NB_OK=$((NB_OK + 1)) ;;
    SAUTE) NB_SAUTE=$((NB_SAUTE + 1)) ;;
    *)
      NB_ECHEC=$((NB_ECHEC + 1))
      # La cascade se décide DÉSORMAIS À LA FIN d'un ticket, et non à son tour de boucle (#289) :
      # avec N en vol, le tour de boucle d'un lot peut arriver avant le verdict de son prédécesseur.
      # Ce qui est déjà parti n'est pas rappelé — le plan l'avait déclaré indépendant, donc de la même
      # vague ; ce qui n'est pas parti sera sauté au moment de le lancer.
      [ "${P_PARENT[$i]}" != "-" ] && PARENTS_ECHOUES="$PARENTS_ECHOUES ${P_PARENT[$i]}"
      ;;
  esac
  # La ligne de ce ticket vient de changer de nature — d'un chrono vivant à un verdict figé. La
  # recomposer ICI, à l'unique endroit qui écrit le bilan, est ce qui garde le bloc juste sans que
  # chaque appelant ait à y penser (#290).
  vue_recompose
  return 0
}

# --- Le verdict d'un ticket qui vient de finir --------------------------------------------------------
# Lu dans la forge (PR ouverte ET cycle de vie « En revue »), jamais dans la prose de la session.
juge_ticket() { # <index> <code rendu par le sous-shell>
  local i="$1" code="$2"
  local iid="${P_IID[$i]}" dest="${P_DEST[$i]}" branche="${P_BRANCHE[$i]}"
  local duree=$((SECONDS - P_DEBUT[i]))
  local cout etat_mr statut mr raison reste n_modifs n_commits detail PREFIXE_TICKET=""
  [ "$CONCURRENCE" -gt 1 ] && PREFIXE_TICKET="#$iid "

  cout="$(champ_json "$RUN_DIR/$iid.json" total_cost_usd)"

  # Les deux sorties d'urgence d'une session arrêtent le RUN et pas seulement ce ticket : elles ne
  # passent donc pas par le verdict GitLab, qu'aucune session n'a eu le loisir de poser.
  if [ "$code" -eq 3 ]; then
    raison="limite hebdomadaire (attente > $(duree_lisible "$PLAFOND_ATTENTE_S"))"
    solde_ticket "$i" ECHEC - "$duree" "${cout:-0}" "$raison"
    # Sans cet appel, les seuls tickets à ne pas avoir de vue lisible seraient ceux qu'on ira
    # justement relire (§11.7).
    ecrit_resultat "$iid" "${P_TITRE[$i]}" ECHEC - "$duree" "limite hebdomadaire"
    PLAFOND_ATTEINT=1
    ARRET_LANCEMENT="limite hebdomadaire"
    return 0
  fi
  if [ "$code" -eq 2 ]; then
    raison="arrêt demandé pendant l'attente de reprise"
    solde_ticket "$i" ECHEC - "$duree" "${cout:-0}" "$raison"
    ecrit_resultat "$iid" "${P_TITRE[$i]}" ECHEC - "$duree" "$raison"
    ARRET_LANCEMENT="arrêt demandé"
    return 0
  fi

  etat_mr="$(gl_mr_state "$branche" 2>/dev/null)"
  statut="$(gl_issue_owner "$iid" 2>/dev/null | cut -f1)"
  mr="$(gl_mr_iid "$branche" 2>/dev/null)"
  if [ "$etat_mr" = "opened" ] && [ "$statut" = "En revue" ]; then
    dit '  %s✓%s PR #%s ouverte, ticket « En revue » — %s, %s $\n' \
      "$C_G" "$C_0" "${mr:-?}" "$(duree_lisible "$duree")" "$(arrondi_cout "${cout:-?}")"
    solde_ticket "$i" OK "${mr:--}" "$duree" "${cout:-0}" -
    # La raison dit sur quoi repose le verdict : la PR est déjà nommée juste avant, l'état non.
    ecrit_resultat "$iid" "${P_TITRE[$i]}" OK "${mr:--}" "$duree" "ticket « En revue »"
    # Le ticket est livré : sa PR entre en file, et c'est le pilote qui la mergera (#419). Après le
    # `solde_ticket`, donc après la recomposition de la vue — la ligne du ticket porte alors son
    # état de merge dès la frame suivante.
    merge_enfile "$iid" "${mr:--}" "$branche"
    vue_recompose
  elif [ "$etat_mr" = "merged" ]; then
    # Une PR MERGÉE est le verdict le plus fort qui soit : le travail est dans `main`, et le ticket
    # est fermé par son « Closes ». Le cas ne devrait pas se produire dans un run — `guard.sh`
    # refuse `merge-mr` aux sessions depuis #419 —, mais le tenir pour un échec ferait sauter en
    # cascade tous les lots suivants du parent d'un ticket LIVRÉ. Un garde-fou qui se trompe de
    # sens coûte plus cher que celui qui manque.
    dit '  %s⇈%s PR #%s déjà mergée — %s, %s $\n' \
      "$C_G" "$C_0" "${mr:-?}" "$(duree_lisible "$duree")" "$(arrondi_cout "${cout:-?}")"
    solde_ticket "$i" OK "${mr:--}" "$duree" "${cout:-0}" "PR mergée hors du pilote"
    ecrit_resultat "$iid" "${P_TITRE[$i]}" OK "${mr:--}" "$duree" "PR mergée"
    merge_enfile "$iid" "${mr:--}" "$branche" mergee '-'
    vue_recompose
  else
    raison="PR « ${etat_mr:-aucune} », cycle de vie « ${statut:-?} »"
    # Ce que la session a laissé derrière elle : c'est cela qui dit si l'échec est rattrapable.
    reste="$(travail_en_attente "$dest")"
    n_modifs="${reste%% *}"; n_modifs="${n_modifs:-0}"
    n_commits="${reste##* }"; n_commits="${n_commits:-0}"
    detail=""
    [ "$n_modifs" -gt 0 ] && detail="$n_modifs fichier(s) non commité(s)"
    # « sur la branche » et non « sans PR » : l'état de la PR est déjà dit juste après, et il
    # arrive qu'elle existe sans que le statut ait suivi.
    [ "$n_commits" -gt 0 ] && detail="${detail:+$detail, }$n_commits commit(s) sur la branche"
    if [ -n "$detail" ]; then
      raison="session terminée sans clôture, $detail — $raison"
    else
      raison="session terminée sans rien produire (worktree propre) — $raison"
    fi
    # Le sous-shell rend 1 pour le seul cas où le CLI est sorti en 124 : le timeout, qui explique
    # tout le reste de la ligne et se dit donc en tête.
    [ "$code" -eq 1 ] && raison="timeout — $raison"
    dit '  %s✗%s %s — journal : %s\n' "$C_R" "$C_0" "$raison" "$RUN_DIR/$iid.resultat.txt"
    [ -n "$detail" ] &&
      dit '    le travail est conservé dans %s — à reprendre, pas à refaire.\n' "$dest"
    solde_ticket "$i" ECHEC "${mr:--}" "$duree" "${cout:-0}" "$raison"
    ecrit_resultat "$iid" "${P_TITRE[$i]}" ECHEC "${mr:--}" "$duree" "$raison"
  fi
  # Le verdict est rendu : plus personne ne relira le flux brut de ce ticket (#198). Après le
  # `consigne`, et dans les deux branches — un échec est justement ce qu'on ira relire, en `.gz`.
  compacte_flux "$iid"
  return 0
}

# --- L'ordonnanceur (#289) ----------------------------------------------------------------------------
# Deux gestes en alternance, jusqu'à ce qu'il ne reste ni ticket à lancer ni ticket en vol : remplir
# les créneaux libres avec le prochain ticket ÉLIGIBLE du plan, puis attendre qu'un s'en libère. À
# `--concurrence 1` cela se réduit exactement au run d'avant — un ticket, son verdict, le suivant.
EN_VOL=""
ORDO_TICK=0.2   # la même horloge que la vue vivante : rien ici ne change plus vite

# compte_creneaux : combien de sessions Claude sont en vol — tickets ET déblocages confondus
# (#420) —, posé dans `CRENEAUX_PRIS`. Une variable et non une sortie de commande : la fonction est
# appelée dans la boucle de remplissage, où un fork n'aurait rien à faire.
#
# Les deux ensembles se comptent ENSEMBLE parce qu'ils tirent sur le même quota : une remédiation
# qui s'affranchirait de `--concurrence` ferait tourner N+1 sessions là où l'on en a demandé N.
CRENEAUX_PRIS=0
compte_creneaux() {
  local j
  CRENEAUX_PRIS=0
  for j in $EN_VOL $MRFIX_EN_VOL; do CRENEAUX_PRIS=$((CRENEAUX_PRIS + 1)); done
  return 0
}

# vue_tick : ce que le pilote fait entre deux moissons — vider la file des lignes permanentes, puis
# redessiner s'il y a lieu (#290). Aucun appel réseau, aucune lecture de découverte : c'est ce qui la
# distingue de `status.sh` et ce qui permet de l'appeler cinq fois par seconde.
#
# On redessine à la SECONDE, ou tout de suite quand l'action d'un ticket change. Rien de ce que la
# frame montre ne bouge plus vite que ça (le chrono compte les secondes), et chaque frame coûte une
# poignée de forks : à cinq images par seconde, la console passait son temps à se réécrire pour y
# remettre le même texte. La signature comparée porte sur TOUS les tickets en vol : un seul d'entre
# eux qui change d'outil suffit à redessiner, et c'est ce qui rattrape un état bref — une attente de
# limite d'usage qui commence, une session qui rouvre.
VUE_DESSINEE_A=-1
VUE_DESSINEE_ETAT=""
vue_tick() {
  vue_active || return 0
  vue_purge
  # Une ligne permanente vient d'être imprimée : le bloc a été retiré pour la laisser passer, il faut
  # le remettre sans attendre la seconde suivante.
  [ "$VUE_HAUT" -eq 0 ] && VUE_DESSINEE_A=-1

  # La taille de la console est relue en cours de run, pas figée au démarrage (#325). Un changement
  # invalide d'un coup les largeurs de colonnes ET la checklist déjà composée : on recompose et on
  # redessine sans attendre la seconde suivante.
  if [ $((SECONDS - VUE_MESURE_A)) -ge "$VUE_MESURE_S" ]; then
    VUE_MESURE_A="$SECONDS"
    if vue_mesure; then vue_recompose; VUE_DESSINEE_A=-1; fi
  fi

  local i etat=""
  for i in $EN_VOL; do
    vue_lit_etat "${P_IID[$i]}"
    etat+="${P_IID[$i]}|$VUE_ETAT_MARQUE|$VUE_ETAT_ACTION"$'\n'
  done
  if [ "$SECONDS" != "$VUE_DESSINEE_A" ] || [ "$etat" != "$VUE_DESSINEE_ETAT" ]; then
    VUE_DESSINEE_A="$SECONDS"
    VUE_DESSINEE_ETAT="$etat"
    vue_dessine
  fi
  return 0
}

# remplit_les_creneaux : lance ce qui peut l'être, dans l'ordre du plan. Balaye TOUT le plan à chaque
# passage et non la seule ligne suivante — c'est là qu'un créneau qui se libère va chercher le prochain
# ticket éligible plutôt que le prochain tout court.
remplit_les_creneaux() {
  local i iid parent statut
  while :; do
    [ -n "$ARRET_LANCEMENT" ] && return 0
    # Les sessions de déblocage comptent dans les créneaux (#420) : voir `compte_creneaux`.
    compte_creneaux
    [ "$CRENEAUX_PRIS" -ge "$CONCURRENCE" ] && return 0

    # Une limite d'usage est en cours et des sessions l'attendent (#291) : jeter un ticket neuf dans
    # cette fenêtre, c'est ouvrir une session qui échouera à sa première requête, brûlera une reprise
    # et rejoindra la même attente — après avoir consommé un montage de worktree et une lecture
    # GitLab pour rien. On attend, comme les autres.
    #
    # Seulement si quelque chose est EN VOL, et la condition n'est pas décorative : sans elle, un
    # rendez-vous encore ouvert alors que plus personne ne l'attend ferait sortir le pilote de sa
    # boucle sur un `EN_VOL` vide, et le reste du plan finirait le run sans une ligne de bilan.
    # Une session de déblocage compte ici comme un ticket : elle aussi attend le rendez-vous, et
    # elle aussi finira par ramener le pilote dans sa boucle (#420).
    if [ -n "$EN_VOL$MRFIX_EN_VOL" ] && limite_en_cours; then return 0; fi

    # Le fichier STOP est relu avant chaque lancement, comme il l'était avant chaque tour de boucle.
    if arret_demande; then ARRET_LANCEMENT="arrêt demandé"; return 0; fi
    if [ "$MAX" -gt 0 ] && [ "$TRAITES" -ge "$MAX" ]; then
      dit '%sPlafond --max %s atteint%s — le reste du plan est laissé pour un prochain run.\n' \
        "$C_Y" "$MAX" "$C_0"
      ARRET_LANCEMENT="plafond --max"
      return 0
    fi

    for ((i = 0; i < NB_ENTREES; i++)); do
      [ "${P_ETAT[$i]}" = attente ] || continue
      eligible "$i" || continue
      iid="${P_IID[$i]}"; parent="${P_PARENT[$i]}"

      # La POSITION dans le plan, sautés compris. C'est elle et non `TRAITES` qui s'affiche :
      # `TRAITES` compte les tickets TENTÉS (il borne `--max`), or une reprise saute tout ce qui a
      # été livré depuis, si bien que le premier ticket réellement traité s'annonçait « [1/6] » alors
      # que le plan en était à son quatrième (#230). C'est l'INDICE dans le plan, et non un compteur
      # qui avance : à N en vol les tickets ne se prennent plus dans l'ordre, et un compteur dirait
      # la position d'un autre. On ne se sert pas non plus du champ `rang` du plan : un `--plan`
      # réduit à un sous-ensemble le donnerait décalé de son propre total (« [4/3] »), `nb_plan`
      # étant compté sur ce fichier-là.
      POSITION=$((i + 1))

      # Un lot dont un prédécesseur du même parent a échoué partirait d'une base incomplète.
      case " $PARENTS_ECHOUES " in
        *" $parent "*)
          dit '  ~ #%-4s sauté — un lot précédent de #%s a échoué\n' "$iid" "$parent"
          solde_ticket "$i" SAUTE - 0 0 "lot précédent de #$parent en échec"
          continue 2
          ;;
      esac

      # Le plan est figé, l'état du backlog non : quelqu'un a pu prendre le ticket entre-temps. Le
      # relire coûte un appel et évite de retirer son travail à une autre session (docs/10 §5).
      # L'exception — le ticket que le run repris avait en main — est justement celui dont le « En
      # cours » vient de nous : le reprendre ne prend le travail de personne.
      P_REPRIS[$i]=0
      statut="$(gl_issue_owner "$iid" 2>/dev/null | cut -f1)"
      if [ "$statut" = "En cours" ] && reprend_en_vol "$iid"; then
        P_REPRIS[$i]=1
        dit '  %s↻%s #%-4s repris en vol — le run %s l'\''avait en main à la coupure\n' \
          "$C_Y" "$C_0" "$iid" "$REPRISE_ID"
      elif [ "$statut" != "À faire" ]; then
        dit '  ~ #%-4s sauté — cycle de vie « %s » (le plan datait)\n' "$iid" "${statut:-?}"
        solde_ticket "$i" SAUTE - 0 0 "cycle de vie « ${statut:-?} » au moment de le prendre"
        continue 2
      fi

      # À partir d'ici le ticket est TENTÉ : il compte pour --max, même si l'échec survient avant la
      # session. Sans quoi une panne systématique (worktree, branche) épuiserait tout le plan alors
      # que l'utilisateur avait justement borné le run pour limiter la casse. Un ticket sauté, lui,
      # ne coûte rien et ne compte pas.
      TRAITES=$((TRAITES + 1))
      lance_ticket "$i"
      # Une frame TOUT DE SUITE (#290) : remplir N créneaux prend le temps de N montages de worktree
      # et de N lectures GitLab, pendant lesquelles l'écran resterait sur l'image d'avant — celle où
      # ce ticket n'était pas encore parti. C'est aussi le seul endroit qui redessine quand les
      # sessions se soldent aussi vite qu'on les lance : la boucle d'attente, elle, ne tourne pas.
      vue_tick
      continue 2
    done
    # Le plan a été balayé sans qu'un ticket parte : soit tout est fini, soit ce qui reste attend
    # qu'un créneau se libère pour cause de dépendance.
    return 0
  done
}

# moissonne : ramasse les tickets dont le témoin est là, rend leur verdict et libère leur créneau.
# Rend 0 dès qu'au moins un a été ramassé — c'est ce qui redonne la main au remplissage.
moissonne() {
  local i reste="" pris=0 code
  for i in $EN_VOL; do
    if [ -s "$RUN_DIR/${P_IID[$i]}.fini" ]; then
      read -r code <"$RUN_DIR/${P_IID[$i]}.fini" || code=1
      case "${code:-}" in '' | *[!0-9]*) code=1 ;; esac
      rm -f "$RUN_DIR/${P_IID[$i]}.fini" 2>/dev/null
    elif [ "${P_ECHEANCE[$i]}" -gt 0 ] && [ "$SECONDS" -gt "${P_ECHEANCE[$i]}" ]; then
      dit '  %s✗%s #%-4s session disparue sans rendre de code — créneau repris.\n' \
        "$C_R" "$C_0" "${P_IID[$i]}"
      code=1
    else
      reste="$reste $i"
      continue
    fi
    # Rien à retirer d'`EN_VOL` ici : c'est `P_ETAT` que la vue lit, et `solde_ticket` le passe à
    # `fini` avant qu'aucune frame ne soit redessinée. `EN_VOL` suit à la fin de la boucle.
    juge_ticket "$i" "$code"
    dit '\n'
    pris=1
  done
  EN_VOL="$reste"
  [ "$pris" = 1 ]
}

while :; do
  # Le drain AVANT le remplissage, et ce n'est pas un détail d'ordonnancement : `worktree.sh` monte
  # la branche du prochain ticket depuis `origin/main`, que `sync-main` vient d'avancer si un merge
  # a eu lieu. Merger d'abord, c'est faire partir les tickets suivants d'un `main` qui contient les
  # précédents — le conflit n'est pas résolu plus vite, il n'est pas fabriqué (#419).
  merge_draine
  # Puis le déblocage de ce que le drain vient de sortir de la file (#420). APRÈS le drain, pour la
  # même raison que le drain passe avant le remplissage : une PR qui se merge telle quelle ne vaut
  # pas une session, et le seul moyen de le savoir est d'avoir essayé. AVANT le remplissage, parce
  # qu'un créneau vaut mieux à une PR bloquée — qui retient déjà un ticket livré — qu'à un ticket
  # neuf qui, lui, attendra sans rien retenir.
  mrfix_relance
  remplit_les_creneaux
  [ -n "$EN_VOL$MRFIX_EN_VOL" ] || break
  # Une frame par tour, AVANT d'attendre quoi que ce soit : `moissonne` peut réussir à chaque appel
  # (des sessions qui se soldent aussi vite qu'on les lance), auquel cas la boucle d'attente ci-dessous
  # ne tourne jamais — et c'était elle, seule, qui dessinait. Le bloc restait alors vide tout le run.
  vue_tick
  # On attend qu'un créneau se libère. `sleep` et non `wait` : `wait` rendrait la main sur n'importe
  # lequel des enfants sans dire lequel, et il faudrait de toute façon relire les témoins. Un tour
  # toutes les 0,2 s ne coûte rien à côté d'une session qui dure des dizaines de minutes — et c'est
  # dans ce tour que le pilote tient l'écran (#290), la seule chose qu'il ait à faire en attendant.
  # Le drain vit AUSSI dans cette boucle, et c'est même là qu'il gagne le plus : à `--concurrence 1`
  # elle est tout ce qui tourne pendant qu'une session travaille une heure. Il ne coûte rien tant
  # qu'aucune entrée n'est due (`Q_VU`), et une passe due tient en quelques appels — le bloc s'y
  # fige le temps d'un `merge-mr`, ce qu'un merge annoncé juste après explique.
  # `moissonne || mrfix_moissonne` : les deux ensembles se ramassent ici, et il FAUT les deux — un
  # run dont il ne reste qu'une session de déblocage en vol a un `EN_VOL` vide, donc `moissonne` ne
  # rendrait jamais 0 et la boucle tournerait sans fin. Le `||` court-circuite dans le bon sens :
  # ramasser un ticket rend la main tout de suite au remplissage, qui est l'urgence.
  until moissonne || mrfix_moissonne; do
    vue_tick; merge_draine; mrfix_relance; sleep "$ORDO_TICK"
  done
done

# Les sous-shells sont tous sortis — leur témoin l'a dit. Ce `wait` ne fait que les récolter, pour
# qu'aucun zombie ne survive au pilote.
wait 2>/dev/null || true

# --- Résumé --------------------------------------------------------------------------------------------
# Plus une frame ne sera dessinée : le bloc part et le curseur revient AVANT le résumé, qui reprend
# le chemin ordinaire (stdout → `tee` → console et journal). Rien ne se dispute plus l'écran.
#
# La file est vidée d'abord (#290) : le verdict du dernier ticket y a été mis pendant que la boucle
# tournait encore, et la boucle vient de sortir sans repasser par `vue_tick`. Sans cet appel, la
# dernière ligne du run n'existerait nulle part — ni à l'écran, ni dans `run.log`.
vue_purge
vue_efface
vue_ferme

# Le drain final, VUE FERMÉE (#419). Il peut durer — `pipeline-wait` attend jusqu'à quinze minutes
# par PR — et rien ne redessinera plus : ses lignes prennent donc le chemin ordinaire, celui du
# résumé. C'est aussi ce qui le rend lisible dans `run.log` : un drain qui aurait tourné pendant que
# le bloc tenait l'écran y aurait laissé ses lignes entrelacées avec les frames.
merge_draine_final

printf '%sRésumé du run %s%s\n' "$C_B" "$RUN_ID" "$C_0"
printf '  %s✓%s %s réussi(s) · %s✗%s %s en échec · %s~%s %s sauté(s)\n' \
  "$C_G" "$C_0" "$NB_OK" "$C_R" "$C_0" "$NB_ECHEC" "$C_Y" "$C_0" "$NB_SAUTE"
printf '  journal : %s\n' "$RUN_DIR"
# Le seul moment où quelqu'un lit ce run est celui-ci : c'est donc ici que l'invitation à instruire
# les refus a une chance d'être suivie (#235). Sans elle, la boucle de rétroaction de §11.7 ne part
# que si on y pense — et onze runs ont montré que non.
printf '  refus de permission : bash scripts/orchestrate/journal.sh refus %s\n' "$RUN_ID"
if [ "$PLAFOND_ATTEINT" = 1 ]; then
  printf '\n  %sRun arrêté sur une limite hebdomadaire%s — le reste du plan est intact.\n' "$C_Y" "$C_0"
  printf '  Le rejouer plus tard, sans recalculer l'\''ordre : /orchestrate --resume %s\n' "$RUN_ID"
  printf '  (hors Claude Code : bash scripts/orchestrate/run.sh --resume %s)\n' "$RUN_ID"
fi
if [ -n "$WORKTREES" ]; then
  # Rien à faire : le ramassage (#197) les retirera de lui-même dès que la forge confirmera leur PR
  # mergée — au prochain /ticket-start, au prochain /branch-cleanup ou au prochain run. On les liste
  # quand même : c'est là que dort le travail si une session a échoué sans clôturer.
  printf '\n  Worktrees montés (retirés d'\''office quand leur PR sera mergée — docs/10 §9.2) :\n'
  for i in $WORKTREES; do printf '    #%s\n' "$i"; done
fi
merge_bilan
# « Aucun merge non vérifié » n'a jamais voulu dire « aucun merge » — depuis #417 c'est l'inverse,
# et le run merge désormais lui-même. Ce qui reste vrai, et qui est la seule chose à dire ici :
# chaque merge est passé par `merge-mr`, qui vérifie avant de merger, et rien n'a été fermé.
if [ "$MERGE" = 1 ]; then
  printf '\n  Aucun merge non vérifié : tout est passé par « lib.sh merge-mr ». Aucune PR fermée.\n'
else
  printf '\n  File de merge désactivée : ce run n'\''a rien mergé ni fermé (--sans-merge, ou MAESTRO_ORCHESTRATE_MERGE=0).\n'
fi
printf '  File de revue : bash scripts/gitlab/lib.sh review-queue\n\n'

[ "$NB_ECHEC" -eq 0 ] || exit 1
exit 0
