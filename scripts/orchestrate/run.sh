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
# OUVERTE et son statut natif est « En revue » — exactement ce que `/ticket-ship` laisse derrière
# lui. C'est vérifiable, et ça ne dépend pas de la formulation du modèle.
#
# --- Ce qu'un échec entraîne ------------------------------------------------------------------------
# Le ticket est laissé en l'état (branche et statut « En cours »), et LES LOTS SUIVANTS DU MÊME
# PARENT sont sautés : ils partiraient d'une base incomplète. Les autres groupes du plan
# s'enchaînent normalement — une erreur à 2 h du matin ne doit pas geler le reste de la nuit.
#
# --- Journal --------------------------------------------------------------------------------------
# .maestro/orchestrate/<run-id>/
#   plan.tsv          le plan figé au démarrage (sortie de queue.sh)
#   <iid>.session     l'UUID de la session du ticket (clé de la reprise, #171)
#   <iid>.json        le résultat brut de la session (coût, usage, permission_denials…)
#   <iid>.log         ce que la session a écrit sur stderr
#   resume.tsv        une ligne par ticket : iid, verdict, MR, durée, coût, raison
#
# Arrêt d'urgence : créer .maestro/orchestrate/STOP — testé entre deux tickets.
#
# --- Coutures de test -------------------------------------------------------------------------------
# Pour que la boucle soit vérifiable sans consommer de quota, sans réseau et sans créer de vraie
# branche (#172) : MAESTRO_CLAUDE_BIN remplace le CLI, MAESTRO_ORCHESTRATE_WORKTREE remplace le
# montage du worktree, et `glab` se substitue par le PATH (lib.sh l'appelle par son nom).

set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$RACINE/scripts/gitlab/lib.sh"

ORCH_DIR="$RACINE/.maestro/orchestrate"
STOP="$ORCH_DIR/STOP"
CLAUDE_BIN="${MAESTRO_CLAUDE_BIN:-claude}"   # surchargeable : stub dans les tests (#172)

DRY=0
MAX=0
BUDGET="${MAESTRO_ORCHESTRATE_BUDGET:-15}"
TIMEOUT_BRUT="${MAESTRO_ORCHESTRATE_TIMEOUT:-45m}"
MODELE="${MAESTRO_ORCHESTRATE_MODELE:-opus}"
PLAN_IMPOSE=""
MILESTONE=""
RUN_ID=""

usage() {
  cat <<'USAGE'
La boucle d'orchestration autonome — un ticket, une session Claude Code.

  bash scripts/orchestrate/run.sh [options]

Options :
  --dry-run            N'exécute rien : affiche le plan et ce qui serait fait.
  --max <n>            Nombre maximal de tickets traités (0 = tout le plan).
  --budget <usd>       Plafond de dépense par ticket (--max-budget-usd). Défaut : 15.
  --timeout <durée>    Délai maximal par ticket : 45m, 90m, 2700… Défaut : 45m.
  --modele <alias>     Modèle des sessions. Défaut : opus.
  --plan <fichier>     Utilise un plan déjà calculé (TSV de queue.sh) au lieu d'en calculer un.
  --milestone <titre>  Transmis à queue.sh (par défaut : la phase courante).
  --run-id <id>        Identifiant du run. Défaut : horodatage.
  -h, --help           Cette aide.

Arrêt d'urgence : créer .maestro/orchestrate/STOP (testé entre deux tickets).
Le run ne merge, ne ferme et ne force-push jamais : il laisse N MR en Draft à relire.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --max) MAX="${2:-0}"; shift ;;
    --budget) BUDGET="${2:-15}"; shift ;;
    --timeout) TIMEOUT_BRUT="${2:-45m}"; shift ;;
    --modele | --model) MODELE="${2:-opus}"; shift ;;
    --plan) PLAN_IMPOSE="${2:-}"; shift ;;
    --milestone) MILESTONE="${2:-}"; shift ;;
    --run-id) RUN_ID="${2:-}"; shift ;;
    -h | --help) usage; exit 0 ;;
    *) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [ -t 1 ]; then
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

duree_lisible() {
  local s="$1"
  if [ "$s" -lt 60 ]; then printf '%ds' "$s"; else printf '%dmin%02d' $((s / 60)) $((s % 60)); fi
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

# --- Le prompt d'une session ------------------------------------------------------------------------
# Écrit pour être IDEMPOTENT : une session relancée sur un ticket déjà entamé doit reprendre, pas
# recommencer. C'est ce qui rend une reprise après interruption (#171) sans danger.
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
- Si la branche du ticket existe déjà et porte des commits, REPRENDS ce travail au lieu de
  recommencer : tu es peut-être la reprise d'une session interrompue.
- Ne merge jamais, ne ferme jamais une MR, ne force-push jamais — un garde-fou les refuse de
  toute façon.
- Si tu ne peux pas terminer, écris en TOUTE DERNIÈRE LIGNE : ORCHESTRATE: ECHEC <raison courte>.
PROMPT
}

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

[ -n "$RUN_ID" ] || RUN_ID="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="$ORCH_DIR/$RUN_ID"
mkdir -p "$RUN_DIR" || { printf 'run.sh : impossible de créer %s\n' "$RUN_DIR" >&2; exit 1; }
PLAN="$RUN_DIR/plan.tsv"
RESUME="$RUN_DIR/resume.tsv"

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
    bash "$queue" --milestone "$MILESTONE" >"$PLAN" || exit 1
  else
    bash "$queue" >"$PLAN" || exit 1
  fi
fi

nb_plan="$(grep -cv '^#' "$PLAN")"
printf '\n%sBoucle d'\''orchestration%s — run %s\n' "$C_B" "$C_0" "$RUN_ID"
printf 'plan : %s ticket(s) · modèle %s · budget %s $/ticket · timeout %s/ticket\n' \
  "$nb_plan" "$MODELE" "$BUDGET" "$(duree_lisible "$TIMEOUT_S")"
printf 'journal : %s\n\n' "$RUN_DIR"

if [ "$nb_plan" -eq 0 ]; then
  printf 'Rien à traiter : le plan est vide.\n'
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
  printf '                        --permission-mode acceptEdits --model %s --max-budget-usd %s\n' "$MODELE" "$BUDGET"
  printf '  3. verdict            MR ouverte ET statut « En revue » (lu dans GitLab, pas dans la sortie)\n'
  printf '  4. sur échec          lots suivants du même parent sautés, run poursuivi\n'
  rm -rf "$RUN_DIR"
  exit 0
fi

printf '# iid\tverdict\tmr\tduree_s\tcout_usd\traison\n' >"$RESUME"

# --- La boucle ----------------------------------------------------------------------------------------
NB_OK=0
NB_ECHEC=0
NB_SAUTE=0
TRAITES=0
PARENTS_ECHOUES=""
WORKTREES=""

consigne() { # <iid> <verdict> <mr> <duree> <cout> <raison>
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" "$6" >>"$RESUME"
}

# Le plan est lu sur le DESCRIPTEUR 3, pas sur stdin : `claude`, `glab` et `worktree.sh` sont lancés
# dans cette boucle et hériteraient de son entrée standard — l'un d'eux consommerait le plan, et le
# run s'arrêterait après un ticket sans rien dire.
while IFS=$'\t' read -r -u 3 rang iid parent prio titre; do
  [ -n "${iid:-}" ] || continue
  case "$rang" in '#'*) continue ;; esac

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
  statut_actuel="$(gl_issue_owner "$iid" 2>/dev/null | cut -f1)"
  if [ "$statut_actuel" != "À faire" ]; then
    printf '  ~ #%-4s sauté — statut « %s » (le plan datait)\n' "$iid" "${statut_actuel:-?}"
    consigne "$iid" SAUTE - 0 0 "statut « ${statut_actuel:-?} » au moment de le prendre"
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

  printf '%s[%s/%s] #%s — %s%s\n' "$C_B" "$TRAITES" "$nb_plan" "$iid" "$titre" "$C_0"

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

  # 2. La session dédiée. `--session-id` fixe est ce qui rendra la reprise possible (#171).
  uuid="$(uuid_du_ticket "$iid")"
  debut=$SECONDS
  ( cd "$dest" && timeout "$TIMEOUT_S" "$CLAUDE_BIN" -p "$(prompt_ticket "$iid")" \
      --session-id "$uuid" \
      --output-format json \
      --permission-mode acceptEdits \
      --settings "$RACINE/scripts/orchestrate/settings.run.json" \
      --max-budget-usd "$BUDGET" \
      --model "$MODELE" </dev/null ) >"$RUN_DIR/$iid.json" 2>"$RUN_DIR/$iid.log"
  code=$?
  duree=$((SECONDS - debut))
  cout="$(champ_json "$RUN_DIR/$iid.json" total_cost_usd)"

  if [ "$code" -eq 124 ]; then
    printf '  %s✗%s session interrompue au bout de %s (timeout)\n' "$C_R" "$C_0" "$(duree_lisible "$TIMEOUT_S")"
  fi

  # 3. Le verdict, lu dans GitLab.
  etat_mr="$(gl_mr_state "$branche" 2>/dev/null)"
  statut="$(gl_issue_owner "$iid" 2>/dev/null | cut -f1)"
  mr="$(gl_mr_iid "$branche" 2>/dev/null)"
  if [ "$etat_mr" = "opened" ] && [ "$statut" = "En revue" ]; then
    printf '  %s✓%s MR !%s ouverte, ticket « En revue » — %s, %s $\n' \
      "$C_G" "$C_0" "${mr:-?}" "$(duree_lisible "$duree")" "${cout:-?}"
    consigne "$iid" OK "${mr:--}" "$duree" "${cout:-0}" -
    NB_OK=$((NB_OK + 1))
  else
    raison="MR « ${etat_mr:-aucune} », statut « ${statut:-?} »"
    [ "$code" -eq 124 ] && raison="timeout — $raison"
    printf '  %s✗%s %s — journal : %s\n' "$C_R" "$C_0" "$raison" "$RUN_DIR/$iid.log"
    consigne "$iid" ECHEC "${mr:--}" "$duree" "${cout:-0}" "$raison"
    NB_ECHEC=$((NB_ECHEC + 1))
    [ "$parent" != "-" ] && PARENTS_ECHOUES="$PARENTS_ECHOUES $parent"
  fi
  printf '\n'
done 3< <(grep -v '^#' "$PLAN")

# --- Résumé --------------------------------------------------------------------------------------------
printf '%sRésumé du run %s%s\n' "$C_B" "$RUN_ID" "$C_0"
printf '  %s✓%s %s réussi(s) · %s✗%s %s en échec · %s~%s %s sauté(s)\n' \
  "$C_G" "$C_0" "$NB_OK" "$C_R" "$C_0" "$NB_ECHEC" "$C_Y" "$C_0" "$NB_SAUTE"
printf '  journal : %s\n' "$RUN_DIR"
if [ -n "$WORKTREES" ]; then
  printf '\n  Worktrees montés — à retirer APRÈS le merge de leur MR (jamais avant : la branche y vit) :\n'
  for i in $WORKTREES; do printf '    bash scripts/git/worktree.sh remove %s\n' "$i"; done
fi
printf '\n  Le merge reste une décision humaine : ce run n'\''a rien mergé ni fermé.\n'
printf '  File de revue : bash scripts/gitlab/lib.sh review-queue\n\n'

[ "$NB_ECHEC" -eq 0 ] || exit 1
exit 0
