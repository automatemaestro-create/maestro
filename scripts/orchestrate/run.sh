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
DETACH=0
MAX=0
BUDGET="${MAESTRO_ORCHESTRATE_BUDGET:-15}"
TIMEOUT_BRUT="${MAESTRO_ORCHESTRATE_TIMEOUT:-45m}"
MODELE="${MAESTRO_ORCHESTRATE_MODELE:-opus}"
PLAN_IMPOSE=""
MILESTONE=""
RUN_ID=""
TEST_REPRISE=""

usage() {
  cat <<'USAGE'
La boucle d'orchestration autonome — un ticket, une session Claude Code.

  bash scripts/orchestrate/run.sh [options]

Options :
  --dry-run            N'exécute rien : affiche le plan et ce qui serait fait.
  --detach             Relance le run dans une console indépendante et rend la main tout de suite.
                       C'est ce qui permet de démarrer un run depuis une session Claude Code : le
                       pilote reste un script shell, dans son propre processus.
  --max <n>            Nombre maximal de tickets traités (0 = tout le plan).
  --budget <usd>       Plafond de dépense par ticket (--max-budget-usd). Défaut : 15.
  --timeout <durée>    Délai maximal par ticket : 45m, 90m, 2700… Défaut : 45m.
  --modele <alias>     Modèle des sessions. Défaut : opus.
  --plan <fichier>     Utilise un plan déjà calculé (TSV de queue.sh) au lieu d'en calculer un.
  --milestone <titre>  Transmis à queue.sh (par défaut : la phase courante).
  --run-id <id>        Identifiant du run. Défaut : horodatage.
  --max-reprises <n>   Reprises maximales après limite d'usage, par ticket. Défaut : 3.
  --test-reprise <f>   Diagnostic : dit si la sortie de session <f> serait vue comme une limite
                       d'usage, et combien de temps la boucle attendrait. N'exécute rien d'autre.
  -h, --help           Cette aide.

Limite d'usage : la boucle attend jusqu'au reset et REPREND la même session (--resume). Au-delà de
5 h 30 d'attente cumulée sur un ticket, c'est la limite hebdomadaire : le run s'arrête proprement.

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
    --modele | --model) MODELE="${2:-opus}"; shift ;;
    --plan) PLAN_IMPOSE="${2:-}"; shift ;;
    --milestone) MILESTONE="${2:-}"; shift ;;
    --run-id) RUN_ID="${2:-}"; shift ;;
    --max-reprises) MAESTRO_ORCHESTRATE_MAX_REPRISES="${2:-3}"; shift ;;
    # Diagnostic de la détection de limite d'usage sur une sortie de session capturée : c'est ce qui
    # rend la reprise vérifiable sans attendre de vraiment taper la limite.
    --test-reprise) TEST_REPRISE="${2:-}"; shift ;;
    -h | --help) usage; exit 0 ;;
    *) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

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

# limite_atteinte <fichier…> : 0 si l'un des fichiers porte la marque d'une limite d'usage.
limite_atteinte() {
  grep -qiE 'usage limit reached|rate.?limit|too many requests|"?api_error_status"?[[:space:]]*:?[[:space:]]*"?429|credit balance' "$@" 2>/dev/null
}

# reset_epoch <fichier…> : l'instant de reset en secondes Unix, si l'un des fichiers l'expose.
# Trois écritures rencontrées : « usage limit reached|<epoch> », un champ « …reset…: <epoch> » (en
# secondes ou en millisecondes), et un horodatage ISO 8601. Rien si aucune n'est présente.
reset_epoch() {
  local brut

  brut="$(grep -ohE 'usage limit reached\|[0-9]{10,13}' "$@" 2>/dev/null | head -1 | grep -oE '[0-9]{10,13}')"
  [ -z "$brut" ] && brut="$(grep -ohiE '"[a-z_]*reset[a-z_]*"[[:space:]]*:[[:space:]]*"?[0-9]{10,13}' "$@" 2>/dev/null | head -1 | grep -oE '[0-9]{10,13}$')"
  if [ -n "$brut" ]; then
    # 13 chiffres = millisecondes. Sans cette conversion, l'attente serait ~1 000 fois trop longue.
    [ "${#brut}" -ge 13 ] && brut="${brut%???}"
    printf '%s' "$brut"
    return 0
  fi

  local iso
  iso="$(grep -ohiE '"[a-z_]*reset[a-z_]*"[[:space:]]*:[[:space:]]*"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]+' "$@" 2>/dev/null |
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

# lance_session <iid> <dest> <uuid> <mode> : une session, neuve ou reprise. En reprise, `--resume`
# rouvre la conversation interrompue — sans quoi la session repartirait de zéro et referait le
# travail déjà payé. Si la reprise échoue (session perdue), on repart à froid sur un UUID neuf :
# le prompt et /ticket-start sont idempotents, le travail déjà commité est retrouvé sur la branche.
lance_session() {
  local iid="$1" dest="$2" uuid="$3" mode="$4" code
  if [ "$mode" = "reprise" ]; then
    ( cd "$dest" && timeout "$TIMEOUT_S" "$CLAUDE_BIN" -p "$(prompt_reprise "$iid")" \
        --resume "$uuid" \
        --output-format json \
        --permission-mode acceptEdits \
        --settings "$RACINE/scripts/orchestrate/settings.run.json" \
        --max-budget-usd "$BUDGET" \
        --model "$MODELE" </dev/null ) >"$RUN_DIR/$iid.json" 2>"$RUN_DIR/$iid.log"
    code=$?
    [ "$code" -eq 0 ] && return 0
    if limite_atteinte "$RUN_DIR/$iid.json" "$RUN_DIR/$iid.log"; then return "$code"; fi
    printf '  reprise de session impossible — redémarrage à froid (le travail déjà commité est sur la branche).\n'
    uuid="$(genere_uuid)"
    printf '%s' "$uuid" >"$RUN_DIR/$iid.session"
  fi
  ( cd "$dest" && timeout "$TIMEOUT_S" "$CLAUDE_BIN" -p "$(prompt_ticket "$iid")" \
      --session-id "$uuid" \
      --output-format json \
      --permission-mode acceptEdits \
      --settings "$RACINE/scripts/orchestrate/settings.run.json" \
      --max-budget-usd "$BUDGET" \
      --model "$MODELE" </dev/null ) >"$RUN_DIR/$iid.json" 2>"$RUN_DIR/$iid.log"
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

# Le prompt de reprise s'adresse à une conversation QUI A DÉJÀ SON CONTEXTE : inutile de lui
# réexpliquer le ticket, il faut au contraire éviter qu'elle recommence ce qu'elle a fait.
prompt_reprise() {
  cat <<PROMPT
Reprends exactement là où tu t'es arrêté sur le ticket #$1 : la session a été interrompue par la
limite d'usage, pas par une erreur. Ne recommence rien de ce qui est déjà fait — regarde d'abord
l'état de la branche (git status, git log) avant d'agir. Termine l'implémentation puis clôture avec
/ticket-ship. Toujours aucune validation humaine à attendre.
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
    esac
    args_enfant+=("$a")
  done
  # Le run-id est imposé : sans lui, le run détaché en tirerait un autre de l'horodatage et on
  # annoncerait un journal qui ne serait jamais écrit. Une valeur déjà passée par l'appelant est
  # reprise telle quelle — c'est celle qui a servi à créer RUN_DIR.
  args_enfant+=(--run-id "$RUN_ID")

  if ! detacher ${args_enfant+"${args_enfant[@]}"}; then
    printf 'run.sh : le lancement détaché a échoué — le run n'\''a pas démarré.\n' >&2
    rm -rf "$RUN_DIR"
    exit 1
  fi

  printf '\n%sRun %s lancé dans une console détachée.%s\n' "$C_B" "$RUN_ID" "$C_0"
  printf '  journal    %s\n' "$RUN_DIR"
  printf '  sortie     %s/run.log\n' "$RUN_DIR"
  printf '  suivre     tail -f %s/run.log\n' "$RUN_DIR"
  printf '  arrêter    touch %s\n' "$STOP"
  printf '  reprendre  bash scripts/orchestrate/run.sh --plan %s/plan.tsv\n' "$RUN_DIR"
  printf '\n%sCe que ce mode ne garantit pas%s : la console ne dépend plus de ce shell, mais rien\n' "$C_Y" "$C_0"
  printf 'n'\''assure qu'\''elle survive à un parent qui enfermerait ses descendants (job object Windows).\n'
  printf 'Si le run s'\''arrête avec lui, le plan reste : la commande « reprendre » le rejoue, les tickets\n'
  printf 'déjà livrés étant sautés d'\''eux-mêmes.\n'
  exit 0
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
  printf '  4. limite d'\''usage    attente jusqu'\''au reset, puis reprise de la même session (--resume)\n'
  printf '  5. sur échec          lots suivants du même parent sautés, run poursuivi\n'
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

  # 2. La session dédiée, avec reprise automatique si la limite d'usage tombe au milieu (#171).
  uuid="$(uuid_du_ticket "$iid")"
  debut=$SECONDS
  attente_cumulee=0
  tentative=0
  reprises=0
  mode=neuf
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

    # Une limite d'usage n'est pas un échec du ticket : c'est une pause. On attend, puis on reprend
    # LA MÊME session — le travail déjà fait reste dans son contexte.
    if ! delai="$(delai_avant_reprise "$RUN_DIR/$iid.json" "$RUN_DIR/$iid.log")"; then
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
      NB_ECHEC=$((NB_ECHEC + 1))
      PLAFOND_ATTEINT=1
      break 2
    fi

    if ! patiente "$delai"; then
      printf '  arrêt demandé pendant l'\''attente — run interrompu.\n'
      consigne "$iid" ECHEC - "$((SECONDS - debut))" "${cout:-0}" "arrêt demandé pendant l'attente de reprise"
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
if [ "$PLAFOND_ATTEINT" = 1 ]; then
  printf '\n  %sRun arrêté sur une limite hebdomadaire%s — le reste du plan est intact.\n' "$C_Y" "$C_0"
  printf '  Relancer plus tard reprendra là où on en est : bash scripts/orchestrate/run.sh\n'
fi
if [ -n "$WORKTREES" ]; then
  printf '\n  Worktrees montés — à retirer APRÈS le merge de leur MR (jamais avant : la branche y vit) :\n'
  for i in $WORKTREES; do printf '    bash scripts/git/worktree.sh remove %s\n' "$i"; done
fi
printf '\n  Le merge reste une décision humaine : ce run n'\''a rien mergé ni fermé.\n'
printf '  File de revue : bash scripts/gitlab/lib.sh review-queue\n\n'

[ "$NB_ECHEC" -eq 0 ] || exit 1
exit 0
