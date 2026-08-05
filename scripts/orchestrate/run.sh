#!/usr/bin/env bash
# La boucle d'orchestration autonome : un ticket, une session Claude Code (#170, parent #167).
#
#   bash scripts/orchestrate/run.sh --dry-run     # le plan et ce qui serait fait, sans rien lancer
#   bash scripts/orchestrate/run.sh               # traite le plan, ticket par ticket
#   bash scripts/orchestrate/run.sh --max 1       # un seul ticket (le premier du plan)
#
# Chaque ticket est traité DANS SON PROPRE WORKTREE et DANS SA PROPRE SESSION : `/ticket-start` →
# implémentation → `/ticket-ship`, sans interruption. Le run produit N Merge Requests en Draft à
# relire ; il ne merge, ne ferme et ne force-push jamais.
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
# On ne lit donc pas sa prose : un ticket est réussi si, et seulement si, sa branche porte une MR
# OUVERTE et son cycle de vie est « En revue » — exactement ce que `/ticket-ship` laisse derrière
# lui. C'est vérifiable, et ça ne dépend pas de la formulation du modèle.
#
# Ce cycle de vie est porté par un LABEL SCOPÉ `workflow::*` (#207/#209), le champ Status natif de
# GitLab ayant disparu avec l'essai Ultimate du groupe. Rien à en savoir de plus ici : lib.sh rend
# toujours le LIBELLÉ (« En revue »), jamais le slug du label (« en-revue ») — c'est son contrat de
# surface, documenté en tête de scripts/gitlab/lib.sh. Les comparaisons de ce fichier portent donc
# sur les mêmes chaînes qu'avant la bascule, et le changement de stockage ne se voit pas d'ici.
#
# --- Ce qu'un échec entraîne ------------------------------------------------------------------------
# Le ticket est laissé en l'état (branche et cycle de vie « En cours »), et LES LOTS SUIVANTS DU
# MÊME PARENT sont sautés : ils partiraient d'une base incomplète. Les autres groupes du plan
# s'enchaînent normalement — une erreur à 2 h du matin ne doit pas geler le reste de la nuit.
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
#   resume.tsv        une ligne par ticket : iid, verdict, MR, durée, coût, raison
#   pid               la carte d'identité du pilote (#213) : PID, WINPID, naissance, hôte — posée au
#                     démarrage, retirée à la sortie, et seule chose qui permette de TUER un run
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
# montage du worktree, et `glab` se substitue par le PATH (lib.sh l'appelle par son nom).

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
BUDGET="${MAESTRO_ORCHESTRATE_BUDGET:-15}"
TIMEOUT_BRUT="${MAESTRO_ORCHESTRATE_TIMEOUT:-45m}"
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

usage() {
  cat <<'USAGE'
La boucle d'orchestration autonome — un ticket, une session Claude Code.

  bash scripts/orchestrate/run.sh [options]

Options :
  --dry-run            N'exécute rien : affiche le plan et ce qui serait fait.
  --resume [<run-id>]  Reprend un run qui ne s'est pas terminé : rejoue SON plan, sans le
                       recalculer. Sans argument, le run reprenable le plus récent. Les tickets
                       déjà livrés se sautent d'eux-mêmes ; celui qui était en vol au moment de la
                       coupure est repris. Se combine avec --detach.
  --detach             Relance le run dans une console indépendante et rend la main tout de suite.
                       C'est ce qui permet de démarrer un run depuis une session Claude Code : le
                       pilote reste un script shell, dans son propre processus.
  --max <n>            Nombre maximal de tickets traités (0 = tout le plan).
  --budget <usd>       Plafond de dépense par ticket (--max-budget-usd). Défaut : 15.
  --timeout <durée>    Délai maximal par ticket : 45m, 90m, 2700… Défaut : 45m.
  --modele <modèle>    Modèle des sessions. Défaut : claude-opus-5.
  --effort <niveau>    Effort de raisonnement des sessions : low, medium, high, xhigh, max.
                       Défaut : xhigh.
  --plan <fichier>     Utilise un plan déjà calculé (TSV de queue.sh) au lieu d'en calculer un.
  --milestone <titre>  Transmis à queue.sh (par défaut : la phase courante).
  --run-id <id>        Identifiant du run. Défaut : horodatage.
  --sans-kill          Ne tue pas les runs encore en cours avant de démarrer (voir plus bas).
  --tuer-les-runs      Ne fait QUE ça : tue les runs en cours, dit lesquels, et sort.
  --max-reprises <n>   Reprises maximales après limite d'usage, par ticket. Défaut : 3.
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
Le run ne merge, ne ferme et ne force-push jamais : il laisse N MR en Draft à relire.
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
    --budget) BUDGET="${2:-15}"; shift ;;
    --timeout) TIMEOUT_BRUT="${2:-45m}"; shift ;;
    --modele | --model) MODELE="${2:-claude-opus-5}"; shift ;;
    --effort) EFFORT="${2:-xhigh}"; shift ;;
    --plan) PLAN_IMPOSE="${2:-}"; shift ;;
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
  C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_B=$'\033[1m'; C_0=$'\033[0m'
else
  C_G=''; C_Y=''; C_R=''; C_B=''; C_0=''
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
      0) printf '  %s✗%s run %s (pid %s)%s — arrêté\n' "$C_Y" "$C_0" "$id" "$pid" \
           "$([ -n "${iid:-}" ] && printf ', ticket #%s en vol' "$iid")" ;;
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
# verdict GitLab la classe ECHEC à juste titre, mais « MR "aucune", cycle de vie "À faire" » ne dit
# pas l'essentiel : le travail est-il PERDU, ou dort-il dans le worktree ? Ces deux compteurs
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

# patiente <secondes> : attend, en tranches, pour que le fichier STOP reste entendu pendant une
# attente qui peut durer des heures. Renvoie 1 si l'arrêt a été demandé.
patiente() {
  local reste="$1" tranche
  printf '  %slimite d'\''usage atteinte%s — attente de %s avant reprise (fin vers %s).\n' \
    "$C_Y" "$C_0" "$(duree_lisible "$reste")" "$(date -d "+$reste seconds" '+%H:%M' 2>/dev/null || echo '?')"
  while [ "$reste" -gt 0 ]; do
    [ -f "$STOP" ] && return 1
    tranche=60
    [ "$reste" -lt 60 ] && tranche="$reste"
    sleep "$tranche"
    reste=$((reste - tranche))
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
tronque() { # <texte> [largeur] : une ligne de progression ne doit jamais noyer la sortie.
  local s="$1" n="${2:-64}"
  if [ "${#s}" -gt "$n" ]; then printf '%s…' "${s:0:$n}"; else printf '%s' "$s"; fi
}

# imprime_outils <ligne> : une ligne « assistant » peut porter PLUSIEURS `tool_use` — on les découpe
# sur leur marqueur plutôt que d'en montrer un seul. L'extraction est volontairement approximative
# (grep, pas un parseur JSON) : c'est un fil d'activité, pas une donnée dont dépend un verdict.
imprime_outils() {
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
    printf '  · %s%s\n' "$nom" "${cible:+ $(tronque "$cible")}"
  done
}

formate_flux() { # <iid> : lit le flux sur stdin, l'archive, et en tire une ligne par action
  local iid="$1" ligne resultat="" derniere=""
  local jsonl="$RUN_DIR/$iid.jsonl"
  : >"$jsonl"
  # Un `.jsonl.gz` laissé par une tentative précédente (run rejoué sous le même run-id) doit partir
  # avec elle : deux traces du même ticket, dont une périmée, se liraient l'une pour l'autre.
  rm -f "$jsonl.gz" 2>/dev/null
  : >"$RUN_DIR/$iid.json"
  # `|| [ -n "$ligne" ]` : sans lui, un flux qui ne se termine pas par un saut de ligne perdrait sa
  # DERNIÈRE ligne — c'est-à-dire l'objet `result`, donc le coût et le verdict.
  while IFS= read -r ligne || [ -n "$ligne" ]; do
    [ -n "$ligne" ] || continue
    printf '%s\n' "$ligne" >>"$jsonl"
    derniere="$ligne"
    case "$ligne" in *'"type":"result"'*) resultat="$ligne" ;; esac
    case "$ligne" in
      *'"type":"assistant"'*'"type":"tool_use"'*) imprime_outils "$ligne" ;;
    esac
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
  if (iid != "")   ligne = ligne " — ticket #" iid
  if (titre != "") ligne = ligne " · " titre
  print ligne
  sid = chaine(brut, "session_id")
  ligne = ""
  if (run != "") ligne = "run " run
  if (sid != "") ligne = ligne (ligne != "" ? " · " : "") "session " sid
  if (ligne != "") print ligne
  print ""

  # Le verdict vient de la boucle, donc de GitLab (MR ouverte ET cycle de vie « En revue ») — jamais
  # de la prose ci-dessous, qui peut se croire réussie sans l'être. Absent quand on relit un vieux
  # fichier.
  if (verdict != "") {
    v = verdict
    if (verdict == "OK") v = "✓ OK"
    else if (verdict == "ECHEC") v = "✗ ECHEC"
    if (mr != "" && mr != "-") v = v " — MR !" mr
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

# lance_session <iid> <dest> <uuid> <mode> : une session, neuve ou reprise. En reprise, `--resume`
# rouvre la conversation interrompue — sans quoi la session repartirait de zéro et referait le
# travail déjà payé. Si la reprise échoue (session perdue), on repart à froid sur un UUID neuf :
# le prompt et /ticket-start sont idempotents, le travail déjà commité est retrouvé sur la branche.
lance_session() {
  local iid="$1" dest="$2" uuid="$3" mode="$4" code
  if [ "$mode" = "reprise" ]; then
    ( cd "$dest" && timeout "$TIMEOUT_S" "$CLAUDE_BIN" -p "$(prompt_reprise "$iid")" \
        --resume "$uuid" \
        --output-format stream-json --verbose \
        --permission-mode acceptEdits \
        --settings "$RACINE/scripts/orchestrate/settings.run.json" \
        --max-budget-usd "$BUDGET" \
        --model "$MODELE" \
        --effort "$EFFORT" </dev/null ) 2>"$RUN_DIR/$iid.log" | formate_flux "$iid"
    # Le code du CLI, pas celui du formateur : c'est lui qui dit si la session a abouti.
    code=${PIPESTATUS[0]}
    [ "$code" -eq 0 ] && return 0
    if limite_atteinte "$RUN_DIR/$iid.json" "$RUN_DIR/$iid.jsonl" "$RUN_DIR/$iid.log"; then return "$code"; fi
    printf '  reprise de session impossible — redémarrage à froid (le travail déjà commité est sur la branche).\n'
    uuid="$(genere_uuid)"
    printf '%s' "$uuid" >"$RUN_DIR/$iid.session"
  fi
  ( cd "$dest" && timeout "$TIMEOUT_S" "$CLAUDE_BIN" -p "$(prompt_ticket "$iid")" \
      --session-id "$uuid" \
      --output-format stream-json --verbose \
      --permission-mode acceptEdits \
      --settings "$RACINE/scripts/orchestrate/settings.run.json" \
      --max-budget-usd "$BUDGET" \
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
# buté sur un `glab mr create --description` multi-ligne, puis sur le `--description "$(cat …)"` par
# lequel elles essayaient de s'en sortir. D'où le renvoi vers l'outil `Write` : un fichier s'écrit
# avec lui, et c'est son CHEMIN qui entre dans la commande.
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
  refusé sans que personne soit là pour l'approuver.
- Trois formes qu'AUCUNE règle ne peut reconnaître, quelle que soit la commande qu'elles habillent
  et même si elle est autorisée : un SAUT DE LIGNE dans la commande, une SUBSTITUTION \$(…), un
  HEREDOC (« <<'EOF' »). Tiens donc chaque appel sur UNE SEULE LIGNE, et n'y fais entrer aucun
  texte long. Pour écrire un fichier — description de MR, corps de commentaire, note de travail —
  sers-toi de l'outil Write, puis donne le CHEMIN de ce fichier à la commande : jamais
  « cat > … <<'EOF' », jamais « --description "\$(cat …)" ».
- Si la branche du ticket existe déjà et porte des commits, OU si le worktree contient des
  modifications non commitées, REPRENDS ce travail au lieu de recommencer : commence par regarder
  git status et git log. Tu es peut-être la reprise d'une session interrompue, et un arbre sale
  sans aucun commit est précisément la trace qu'elle laisse.
- Ne merge jamais, ne ferme jamais une MR, ne force-push jamais — un garde-fou les refuse de
  toute façon.
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

# --- Diagnostic de la détection de limite d'usage -----------------------------------------------------
# Placé avant tout le reste : il ne demande ni glab, ni plan, ni répertoire de run — il ne fait que
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
# glab, ni plan, ni répertoire de run — juste la vue lisible d'un `<iid>.json`, sur stdout. C'est ce
# qui rattrape les runs écrits AVANT ce lot, dont le journal ne porte pas de `.resultat.txt`.
if [ -n "$LIRE_RESULTAT" ]; then
  [ -r "$LIRE_RESULTAT" ] || { printf 'run.sh : fichier illisible — %s\n' "$LIRE_RESULTAT" >&2; exit 2; }
  # L'iid se déduit du nom du fichier (« 130.json ») quand il en porte un : c'est le cas nominal.
  iid_lu="$(basename "$LIRE_RESULTAT")"; iid_lu="${iid_lu%%.*}"
  case "$iid_lu" in *[!0-9]* | '') iid_lu="" ;; esac
  vue_resultat "$LIRE_RESULTAT" "$iid_lu" "" "" "" "" "" "$(basename "$(dirname "$LIRE_RESULTAT")")"
  exit 0
fi

# Ne faire QUE tuer (#213) : ni glab, ni plan, ni répertoire de run. C'est le geste de quelqu'un qui
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
gl_require_glab || exit 1

TIMEOUT_S="$(secondes "$TIMEOUT_BRUT")" || {
  printf 'run.sh : durée invalide pour --timeout : %s (attendu 45m, 2h, 2700…)\n' "$TIMEOUT_BRUT" >&2
  exit 2
}

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
fi

[ -n "$RUN_ID" ] || RUN_ID="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="$ORCH_DIR/$RUN_ID"
mkdir -p "$RUN_DIR" || { printf 'run.sh : impossible de créer %s\n' "$RUN_DIR" >&2; exit 1; }
PLAN="$RUN_DIR/plan.tsv"
RESUME="$RUN_DIR/resume.tsv"
# Deux journaux partiels qui racontent la même liste de tickets doivent se répondre : sans ce
# fichier, rien ne dirait que celui-ci continue l'autre. `status.sh` l'affiche en en-tête.
[ "$REPRISE" = 1 ] && printf '%s\n' "$REPRISE_ID" >"$RUN_DIR/reprise-de"

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
    # La carte `pid` (#213) est dans le même cas — elle décrit le processus, pas son travail.
    case "${f##*/}" in plan.tsv | reprise-de | pid) ;; *) return 1 ;; esac
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
    printf 'bash %q' "$RACINE/scripts/orchestrate/run.sh"
    for arg in "$@"; do printf ' %q' "$arg"; done
    # `tee` garde la sortie lisible dans la fenêtre ET sur disque : une console qui se referme (ou
    # qu'on ferme) ne doit pas emporter la seule trace de ce qui s'est passé.
    printf ' 2>&1 | tee -a %q\n' "$journal"
    printf 'code=${PIPESTATUS[0]}\n'
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
  trap 'pilote_retire "$RUN_DIR"' EXIT
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
printf 'plan : %s ticket(s) · modèle %s · effort %s · budget %s $/ticket · timeout %s/ticket\n' \
  "$nb_plan" "$MODELE" "$EFFORT" "$BUDGET" "$(duree_lisible "$TIMEOUT_S")"
printf 'journal : %s\n\n' "$RUN_DIR"

if [ "$nb_plan" -eq 0 ]; then
  printf 'Rien à traiter : le plan est vide.\n'
  renonce_au_run && printf 'Aucun journal laissé derrière : il n'\''aurait porté que ce plan vide.\n'
  exit 0
fi

grep -v '^#' "$PLAN" | while IFS=$'\t' read -r rang iid parent prio titre; do
  printf '  %2s. #%-4s %-8s %s%s\n' "$rang" "$iid" "$prio" "$titre" \
    "$([ "$parent" != "-" ] && printf ' (lot de #%s)' "$parent")"
done
printf '\n'

if [ "$DRY" = 1 ]; then
  printf 'Mode --dry-run : rien n'\''a été lancé. Chaque ticket aurait été traité ainsi —\n'
  printf '  1. worktree dédié     bash scripts/git/worktree.sh <iid>\n'
  printf '  2. session dédiée     %s -p … --session-id <uuid> --settings scripts/orchestrate/settings.run.json\n' "$CLAUDE_BIN"
  printf '                        --permission-mode acceptEdits --model %s --effort %s --max-budget-usd %s\n' "$MODELE" "$EFFORT" "$BUDGET"
  printf '  3. verdict            MR ouverte ET cycle de vie « En revue » (lu dans GitLab, pas dans la sortie)\n'
  printf '  4. limite d'\''usage    attente jusqu'\''au reset, puis réouverture de la même session Claude\n'
  printf '  5. sur échec          lots suivants du même parent sautés, run poursuivi\n'
  printf '  6. run coupé          « run.sh --resume » rejoue CE plan, le ticket en vol compris\n'
  rm -rf "$RUN_DIR"
  exit 0
fi

printf '# iid\tverdict\tmr\tduree_s\tcout_usd\traison\n' >"$RESUME"

# Ramassage des worktrees soldés avant de commencer (#197). C'est ici que l'accumulation fait le plus
# mal : un worktree pèse ~535 Mo et ce run va en monter un par ticket, sans personne devant pour
# faire le ménage. Best-effort et muet quand il n'y a rien à retirer ; un ramassage impossible (glab
# hors ligne) ne doit pas empêcher un run de partir.
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

# Le coût est arrondi ICI, à l'unique endroit qui écrit le bilan : `status.sh` le relit tel quel, et
# une colonne à quinze décimales (« 10.686978499999995 ») ne dit rien de plus qu'à deux.
consigne() { # <iid> <verdict> <mr> <duree> <cout> <raison>
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$(arrondi_cout "${5:-0}")" "$6" >>"$RESUME"
}

# Le plan est lu sur le DESCRIPTEUR 3, pas sur stdin : `claude`, `glab` et `worktree.sh` sont lancés
# dans cette boucle et hériteraient de son entrée standard — l'un d'eux consommerait le plan, et le
# run s'arrêterait après un ticket sans rien dire.
while IFS=$'\t' read -r -u 3 rang iid parent prio titre; do
  [ -n "${iid:-}" ] || continue
  case "$rang" in '#'*) continue ;; esac

  # La POSITION dans le plan, comptée sur toutes les lignes lues — sautées comprises. C'est elle et
  # non `TRAITES` qui s'affiche : `TRAITES` compte les tickets TENTÉS (il borne `--max`, plus bas),
  # or une reprise saute tout ce qui a été livré depuis, si bien que le premier ticket réellement
  # traité s'annonçait « [1/6] » alors que le plan en était à son quatrième (#230). On ne se sert
  # pas non plus du champ `rang` du plan : un `--plan` réduit à un sous-ensemble le donnerait
  # décalé de son propre total (« [4/3] »), `nb_plan` étant compté sur ce fichier-là.
  POSITION=$((POSITION + 1))

  if arret_demande; then break; fi
  if [ "$MAX" -gt 0 ] && [ "$TRAITES" -ge "$MAX" ]; then
    printf '%sPlafond --max %s atteint%s — le reste du plan est laissé pour un prochain run.\n' "$C_Y" "$MAX" "$C_0"
    break
  fi

  # Un lot dont un prédécesseur du même parent a échoué partirait d'une base incomplète.
  case " $PARENTS_ECHOUES " in
    *" $parent "*)
      printf '  ~ #%-4s sauté — un lot précédent de #%s a échoué\n' "$iid" "$parent"
      consigne "$iid" SAUTE - 0 0 "lot précédent de #$parent en échec"
      NB_SAUTE=$((NB_SAUTE + 1))
      continue
      ;;
  esac

  # Le plan est figé, l'état du backlog non : quelqu'un a pu prendre le ticket entre-temps. Le
  # relire coûte un appel et évite de retirer son travail à une autre session (docs/10 §5).
  # L'exception — le ticket que le run repris avait en main — est justement celui dont le « En
  # cours » vient de nous : le reprendre ne prend le travail de personne.
  en_vol=0
  statut_actuel="$(gl_issue_owner "$iid" 2>/dev/null | cut -f1)"
  if [ "$statut_actuel" = "En cours" ] && reprend_en_vol "$iid"; then
    en_vol=1
    printf '  %s↻%s #%-4s repris en vol — le run %s l'\''avait en main à la coupure\n' \
      "$C_Y" "$C_0" "$iid" "$REPRISE_ID"
  elif [ "$statut_actuel" != "À faire" ]; then
    printf '  ~ #%-4s sauté — cycle de vie « %s » (le plan datait)\n' "$iid" "${statut_actuel:-?}"
    consigne "$iid" SAUTE - 0 0 "cycle de vie « ${statut_actuel:-?} » au moment de le prendre"
    NB_SAUTE=$((NB_SAUTE + 1))
    continue
  fi

  # À partir d'ici le ticket est TENTÉ : il compte pour --max, même si l'échec survient avant la
  # session. Sans quoi une panne systématique (worktree, branche) épuiserait tout le plan alors que
  # l'utilisateur avait justement borné le run pour limiter la casse. Un ticket sauté, lui, ne coûte
  # rien et ne compte pas.
  TRAITES=$((TRAITES + 1))

  branche="$(gl_branch_for "$iid" 2>/dev/null)"
  if [ -z "$branche" ]; then
    printf '  %s✗%s #%-4s branche introuvable (label type:: absent ?)\n' "$C_R" "$C_0" "$iid"
    consigne "$iid" ECHEC - 0 0 "nom de branche non résolu"
    NB_ECHEC=$((NB_ECHEC + 1))
    [ "$parent" != "-" ] && PARENTS_ECHOUES="$PARENTS_ECHOUES $parent"
    continue
  fi

  printf '%s[%s/%s] #%s — %s%s\n' "$C_B" "$POSITION" "$nb_plan" "$iid" "$titre" "$C_0"

  # 1. Le worktree : un répertoire de travail et des ports par ticket (docs/10 §9), pour que le
  #    clone principal reste utilisable pendant que le run tourne.
  dest="$(prepare_worktree "$iid" "$branche" "$RUN_DIR/$iid.worktree.log")"
  if [ -z "$dest" ] || [ ! -d "$dest" ]; then
    printf '  %s✗%s worktree de « %s » non monté — voir %s\n' "$C_R" "$C_0" "$branche" "$RUN_DIR/$iid.worktree.log"
    consigne "$iid" ECHEC - 0 0 "worktree non monté"
    NB_ECHEC=$((NB_ECHEC + 1))
    [ "$parent" != "-" ] && PARENTS_ECHOUES="$PARENTS_ECHOUES $parent"
    continue
  fi
  WORKTREES="$WORKTREES $iid"
  printf '  worktree : %s\n' "$dest"

  # 2. La session dédiée, avec reprise automatique si la limite d'usage tombe au milieu (#171).
  #    Un ticket repris en vol rouvre LA SESSION de la coupure : son uuid est recopié du journal
  #    repris AVANT `uuid_du_ticket`, qui en générerait un neuf sinon — et repartir à froid ferait
  #    repayer un contexte déjà constitué. Si elle n'est plus reprenable, `lance_session` redémarre
  #    tout seul à froid : le prompt est idempotent et le travail commité est sur la branche.
  mode=neuf
  if [ "$en_vol" = 1 ] && cp "$REPRISE_DIR/$iid.session" "$RUN_DIR/$iid.session" 2>/dev/null; then
    mode=reprise
    printf '  session de la coupure rouverte (%s)\n' "$(cat "$RUN_DIR/$iid.session")"
  fi
  uuid="$(uuid_du_ticket "$iid")"
  debut=$SECONDS
  attente_cumulee=0
  tentative=0
  reprises=0
  cout=0

  while :; do
    tentative=$((tentative + 1))
    lance_session "$iid" "$dest" "$uuid" "$mode"
    code=$?
    cout="$(champ_json "$RUN_DIR/$iid.json" total_cost_usd)"

    if [ "$code" -eq 124 ]; then
      printf '  %s✗%s session interrompue au bout de %s (timeout)\n' "$C_R" "$C_0" "$(duree_lisible "$TIMEOUT_S")"
      break
    fi

    # Une session sortie en 0 est allée au bout de son tour : rien ne l'a coupée, et il n'y a rien à
    # reprendre. On passe droit au verdict GitLab. Sans ce garde-fou, tout faux positif de la
    # détection renvoyait en attente un ticket DÉJÀ LIVRÉ, sans jamais lire ce verdict (#203).
    if [ "$code" -eq 0 ]; then
      break
    fi

    # Une limite d'usage n'est pas un échec du ticket : c'est une pause. On attend, puis on reprend
    # LA MÊME session — le travail déjà fait reste dans son contexte.
    if ! delai="$(delai_avant_reprise "$RUN_DIR/$iid.json" "$RUN_DIR/$iid.jsonl" "$RUN_DIR/$iid.log")"; then
      break
    fi

    if [ "$reprises" -ge "$MAX_REPRISES" ]; then
      printf '  %s✗%s limite d'\''usage encore atteinte après %s reprise(s) — on passe au ticket suivant.\n' \
        "$C_R" "$C_0" "$reprises"
      break
    fi

    attente_cumulee=$((attente_cumulee + delai))
    if [ "$attente_cumulee" -gt "$PLAFOND_ATTENTE_S" ]; then
      printf '\n%sLimite hebdomadaire%s — %s d'\''attente cumulée sur #%s dépassent le plafond de %s.\n' \
        "$C_Y" "$C_0" "$(duree_lisible "$attente_cumulee")" "$iid" "$(duree_lisible "$PLAFOND_ATTENTE_S")"
      printf 'Ce n'\''est plus une fenêtre de 5 h : le run s'\''arrête proprement, à relancer plus tard.\n'
      consigne "$iid" ECHEC - "$((SECONDS - debut))" "${cout:-0}" "limite hebdomadaire (attente > $(duree_lisible "$PLAFOND_ATTENTE_S"))"
      # Ces deux sorties quittent la boucle entière : sans cet appel, les seuls tickets à ne pas
      # avoir de vue lisible seraient ceux qu'on ira justement relire (§11.7).
      ecrit_resultat "$iid" "$titre" ECHEC - "$((SECONDS - debut))" "limite hebdomadaire"
      NB_ECHEC=$((NB_ECHEC + 1))
      PLAFOND_ATTEINT=1
      break 2
    fi

    if ! patiente "$delai"; then
      printf '  arrêt demandé pendant l'\''attente — run interrompu.\n'
      consigne "$iid" ECHEC - "$((SECONDS - debut))" "${cout:-0}" "arrêt demandé pendant l'attente de reprise"
      ecrit_resultat "$iid" "$titre" ECHEC - "$((SECONDS - debut))" "arrêt demandé pendant l'attente de reprise"
      NB_ECHEC=$((NB_ECHEC + 1))
      break 2
    fi

    reprises=$((reprises + 1))
    mode=reprise
    printf '  reprise %s/%s de la session #%s…\n' "$reprises" "$MAX_REPRISES" "$iid"
  done
  duree=$((SECONDS - debut))
  [ "$reprises" -eq 0 ] || printf '  (%s reprise(s) après limite d'\''usage, %s d'\''attente)\n' \
    "$reprises" "$(duree_lisible "$attente_cumulee")"

  # 3. Le verdict, lu dans GitLab.
  etat_mr="$(gl_mr_state "$branche" 2>/dev/null)"
  statut="$(gl_issue_owner "$iid" 2>/dev/null | cut -f1)"
  mr="$(gl_mr_iid "$branche" 2>/dev/null)"
  if [ "$etat_mr" = "opened" ] && [ "$statut" = "En revue" ]; then
    printf '  %s✓%s MR !%s ouverte, ticket « En revue » — %s, %s $\n' \
      "$C_G" "$C_0" "${mr:-?}" "$(duree_lisible "$duree")" "$(arrondi_cout "${cout:-?}")"
    consigne "$iid" OK "${mr:--}" "$duree" "${cout:-0}" -
    # La raison dit sur quoi repose le verdict : la MR est déjà nommée juste avant, l'état non.
    ecrit_resultat "$iid" "$titre" OK "${mr:--}" "$duree" "ticket « En revue »"
    NB_OK=$((NB_OK + 1))
  else
    raison="MR « ${etat_mr:-aucune} », cycle de vie « ${statut:-?} »"
    # Ce que la session a laissé derrière elle : c'est cela qui dit si l'échec est rattrapable.
    reste="$(travail_en_attente "$dest")"
    n_modifs="${reste%% *}"; n_modifs="${n_modifs:-0}"
    n_commits="${reste##* }"; n_commits="${n_commits:-0}"
    detail=""
    [ "$n_modifs" -gt 0 ] && detail="$n_modifs fichier(s) non commité(s)"
    # « sur la branche » et non « sans MR » : l'état de la MR est déjà dit juste après, et il
    # arrive qu'elle existe sans que le statut ait suivi.
    [ "$n_commits" -gt 0 ] && detail="${detail:+$detail, }$n_commits commit(s) sur la branche"
    if [ -n "$detail" ]; then
      raison="session terminée sans clôture, $detail — $raison"
    else
      raison="session terminée sans rien produire (worktree propre) — $raison"
    fi
    [ "$code" -eq 124 ] && raison="timeout — $raison"
    printf '  %s✗%s %s — journal : %s\n' "$C_R" "$C_0" "$raison" "$RUN_DIR/$iid.resultat.txt"
    [ -n "$detail" ] &&
      printf '    le travail est conservé dans %s — à reprendre, pas à refaire.\n' "$dest"
    consigne "$iid" ECHEC "${mr:--}" "$duree" "${cout:-0}" "$raison"
    ecrit_resultat "$iid" "$titre" ECHEC "${mr:--}" "$duree" "$raison"
    NB_ECHEC=$((NB_ECHEC + 1))
    [ "$parent" != "-" ] && PARENTS_ECHOUES="$PARENTS_ECHOUES $parent"
  fi
  # Le verdict est rendu : plus personne ne relira le flux brut de ce ticket (#198). Après le
  # `consigne`, et dans les deux branches — un échec est justement ce qu'on ira relire, en `.gz`.
  compacte_flux "$iid"
  printf '\n'
done 3< <(grep -v '^#' "$PLAN")

# --- Résumé --------------------------------------------------------------------------------------------
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
  # Rien à faire : le ramassage (#197) les retirera de lui-même dès que GitLab confirmera leur MR
  # mergée — au prochain /ticket-start, au prochain /branch-cleanup ou au prochain run. On les liste
  # quand même : c'est là que dort le travail si une session a échoué sans clôturer.
  printf '\n  Worktrees montés (retirés d'\''office quand leur MR sera mergée — docs/10 §9.2) :\n'
  for i in $WORKTREES; do printf '    #%s\n' "$i"; done
fi
printf '\n  Le merge reste une décision humaine : ce run n'\''a rien mergé ni fermé.\n'
printf '  File de revue : bash scripts/gitlab/lib.sh review-queue\n\n'

[ "$NB_ECHEC" -eq 0 ] || exit 1
exit 0
