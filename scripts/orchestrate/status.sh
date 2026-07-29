#!/usr/bin/env bash
# Où en est un run d'orchestration — depuis n'importe quel terminal (#177, parent #174).
#
#   bash scripts/orchestrate/status.sh                  # le run le plus récent, une fois
#   bash scripts/orchestrate/status.sh --watch          # ... et rafraîchi tant qu'il tourne
#   bash scripts/orchestrate/status.sh --run-id 20260729-132807
#   bash scripts/orchestrate/status.sh --list           # les runs connus
#
# --- Pourquoi une commande, et pas la console du run -------------------------------------------------
# La console d'un run (`run.sh --detach`) répond très bien à « où ça en est ? »... tant qu'on l'a
# sous les yeux. Fenêtre fermée, autre poste, run lancé la veille : il ne reste que le répertoire du
# run sur le disque, et la seule façon de savoir si une session travaille ou est plantée était
# d'aller regarder à la main les mtimes de son worktree. Ce script fait cette lecture, une fois pour
# toutes, et la complète par ce que GitLab sait.
#
# --- Lecture seule, et sans prise sur le run ---------------------------------------------------------
# Il n'écrit RIEN : ni dans le répertoire du run, ni dans le dépôt, ni dans GitLab. Il ne touche pas
# non plus à `run.sh` — un run en cours doit pouvoir être observé sans risquer de le perturber (bash
# relit un script au fil de son exécution). Tout ce qu'il affiche est déduit de fichiers déjà là :
#
#   plan.tsv        le plan figé au démarrage : rang, iid, parent, prio, titre
#   resume.tsv      une ligne par ticket TERMINÉ : iid, verdict, mr, duree_s, cout_usd, raison
#   <iid>.session   présent dès que le ticket est pris en main -> c'est le ticket en cours
#   <iid>.*         tout le reste (jsonl, json, log, worktree.log) — sert de témoin d'activité,
#                   par sa date de modification
#   run.log         la sortie de la console, pour un run détaché
#
# --- « En cours » se déduit, il ne se lit pas ---------------------------------------------------------
# `run.sh` n'écrit pas de PID et ne dit pas « je suis vivant » : le ticket en cours est donc le
# premier du plan qui a des fichiers de session SANS ligne de bilan. Un run tué au milieu laisse
# exactement la même trace qu'un run qui travaille — d'où la ligne « activité », qui date la dernière
# écriture (répertoire du run ET worktree du ticket) et signale le silence au-delà de
# MAESTRO_ORCHESTRATE_SILENCE. C'est une déduction honnête, pas une certitude : elle est présentée
# comme telle plutôt que maquillée en verdict.
#
# --- Le signal de progression le plus fiable : le worktree --------------------------------------------
# Pendant une session, `<iid>.json` reste vide (le CLI n'écrit son résultat qu'à la fin) : ce qui dit
# vraiment que ça avance, ce sont les commits et les fichiers modifiés dans le worktree du ticket.
# On les lit avec git, en local, sans réseau.

set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$RACINE/scripts/gitlab/lib.sh"

ORCH_DIR="$RACINE/.maestro/orchestrate"
STOP="$ORCH_DIR/STOP"

RUN_ID=""
WATCH=0
INTERVALLE=20
LISTE=0
SANS_GITLAB=0
SEUIL_SILENCE="${MAESTRO_ORCHESTRATE_SILENCE:-900}"   # 15 min sans une écriture = ça mérite d'être dit

usage() {
  cat <<'USAGE'
Où en est un run d'orchestration — hors de la console qui l'a lancé.

  bash scripts/orchestrate/status.sh [options]

Options :
  --run-id <id>     Run à inspecter. Défaut : le plus récent.
  --watch [sec]     Rafraîchit tant que le run tourne (défaut : toutes les 20 s), puis rend la
                    main sur l'état final. Le run suivi est fixé au démarrage.
  --list            Liste les runs connus, du plus ancien au plus récent, et s'arrête.
  --no-gitlab       N'interroge pas GitLab (hors ligne, ou pour aller vite) : tout le reste,
                    plan et worktree compris, est lu en local.
  -h, --help        Cette aide.

Lecture seule : n'écrit ni dans le run, ni dans le dépôt, ni dans GitLab. Aucun run en cours est
un cas NORMAL, pas une erreur — le script le dit et sort en 0.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --run-id) RUN_ID="${2:-}"; shift ;;
    --watch | --suivre)
      WATCH=1
      # La valeur est facultative : « --watch » seul garde l'intervalle par défaut, « --watch 5 »
      # le règle. On ne consomme l'argument suivant que s'il est bien un nombre — sans quoi
      # « --watch --no-gitlab » avalerait l'option d'après.
      case "${2:-}" in
        '' | *[!0-9]*) ;;
        *) INTERVALLE="$2"; shift ;;
      esac
      ;;
    --list | --liste) LISTE=1 ;;
    --no-gitlab | --sans-gitlab) SANS_GITLAB=1 ;;
    -h | --help) usage; exit 0 ;;
    *) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# Une valeur d'environnement fantaisiste ne doit pas faire planter une commande de diagnostic :
# on retombe sur le défaut plutôt que d'échouer au premier test arithmétique.
[ "$INTERVALLE" -ge 1 ] 2>/dev/null || INTERVALLE=20
[ "$SEUIL_SILENCE" -ge 0 ] 2>/dev/null || SEUIL_SILENCE=900

if [ -t 1 ]; then
  C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_B=$'\033[1m'; C_D=$'\033[2m'; C_0=$'\033[0m'
else
  C_G=''; C_Y=''; C_R=''; C_B=''; C_D=''; C_0=''
fi

# --- Utilitaires ---------------------------------------------------------------------------------

# mtime <fichier> : date de dernière modification en secondes Unix. GNU (`stat -c`) puis BSD
# (`stat -f`) puis `date -r` : le script doit tourner aussi bien sous Git Bash que sur un serveur.
mtime() {
  stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || date -r "$1" +%s 2>/dev/null
}

duree_lisible() {
  local s="${1:-0}"
  [ "$s" -ge 0 ] 2>/dev/null || s=0
  if [ "$s" -lt 60 ]; then printf '%ds' "$s"
  elif [ "$s" -lt 3600 ]; then printf '%dmin%02d' $((s / 60)) $((s % 60))
  else printf '%dh%02d' $((s / 3600)) $(((s % 3600) / 60)); fi
}

horodatage() { date -d "@$1" '+%Y-%m-%d %H:%M' 2>/dev/null || printf '?'; }

# Les commandes proposées se copient depuis la racine du dépôt : un chemin absolu y ajouterait des
# espaces (« /e/Projects Solutions/… ») qu'il faudrait échapper à la main.
relatif() { printf '%s' "${1#"$RACINE"/}"; }

titre_section() { printf '\n%s── %s%s\n' "$C_B" "$*" "$C_0"; }

# plus_recent <fichier…> : la plus récente des dates de modification, ou rien si aucun fichier
# lisible. Les arguments inexistants sont ignorés — l'appelant passe des globs qui peuvent ne rien
# matcher : un `<iid>.jsonl` n'existe qu'une fois la session lancée.
plus_recent() {
  local f t max=""
  for f in "$@"; do
    [ -e "$f" ] || continue
    t="$(mtime "$f")" || continue
    [ -n "$t" ] || continue
    if [ -z "$max" ] || [ "$t" -gt "$max" ]; then max="$t"; fi
  done
  [ -n "$max" ] || return 1
  printf '%s' "$max"
}

# debut_du_run <run-id> <run-dir> : l'instant de départ. Le run-id EST un horodatage quand il n'a
# pas été imposé à la main ; sinon on retombe sur la date du plan, écrit en tout premier.
debut_du_run() {
  local id="$1" dir="$2" t
  case "$id" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9])
      t="$(date -d "${id:0:4}-${id:4:2}-${id:6:2} ${id:9:2}:${id:11:2}:${id:13:2}" +%s 2>/dev/null)"
      [ -n "$t" ] && { printf '%s' "$t"; return 0; }
      ;;
  esac
  plus_recent "$dir/plan.tsv" "$dir"
}

# runs : les répertoires de run, du plus ancien au plus récent (l'horodatage du nom suffit à trier,
# et un run-id imposé se range où son nom le place — c'est sans conséquence, on ne s'en sert que
# pour choisir « le plus récent » par défaut).
runs() {
  local d
  [ -d "$ORCH_DIR" ] || return 1
  for d in "$ORCH_DIR"/*/; do
    [ -d "$d" ] || continue
    printf '%s\n' "$(basename "$d")"
  done | sort
}

# --- Lecture du plan et du bilan -------------------------------------------------------------------

# champ_plan <run-dir> <iid> <n> : la n-ième colonne de la ligne du ticket dans le plan.
champ_plan() {
  awk -F'\t' -v iid="$2" -v n="$3" '$1 !~ /^#/ && $2 == iid { print $n; exit }' "$1/plan.tsv" 2>/dev/null
}

# traite <run-dir> <iid> : 0 si le ticket a déjà sa ligne de bilan (verdict rendu).
traite() {
  awk -F'\t' -v iid="$2" '$1 !~ /^#/ && $1 == iid { trouve = 1 } END { exit !trouve }' \
    "$1/resume.tsv" 2>/dev/null
}

# ticket_en_cours <run-dir> : l'iid du ticket pris en main mais sans verdict — au plus un, la boucle
# étant séquentielle. Le témoin est `<iid>.session`, écrit juste avant le lancement de la session.
ticket_en_cours() {
  local dir="$1" iid
  [ -f "$dir/plan.tsv" ] || return 1
  while IFS=$'\t' read -r _ iid _ _ _; do
    [ -n "${iid:-}" ] || continue
    [ -e "$dir/$iid.session" ] || continue
    traite "$dir" "$iid" && continue
    printf '%s' "$iid"
    return 0
  done < <(grep -v '^#' "$dir/plan.tsv" 2>/dev/null)
  return 1
}

# --- Le dépôt : branche, worktree, avancement --------------------------------------------------------

# branche_du_iid <iid> : la branche locale du ticket, d'après la convention <type>/<iid>-<slug>.
# Lu dans git plutôt que demandé à GitLab : c'est gratuit, hors ligne, et ça marche encore quand le
# label type:: a changé depuis.
branche_du_iid() {
  git -C "$RACINE" for-each-ref --format='%(refname:short)' refs/heads 2>/dev/null |
    awk -v iid="$1" 'index($0, "/" iid "-") || $0 ~ ("/" iid "$") { print; exit }'
}

# worktree_de_branche <branche> : le répertoire de travail qui emprunte cette branche, s'il existe.
# Demandé à git, comme le fait `run.sh` — deux formules de nommage qui divergeraient se
# remarqueraient trop tard.
worktree_de_branche() {
  git -C "$RACINE" worktree list --porcelain 2>/dev/null | awk -v cible="branch refs/heads/$1" '
    /^worktree / { w = substr($0, 10) }
    $0 == cible { print w; exit }'
}

# activite_du_ticket <run-dir> <iid> : la plus récente écriture attribuable à ce ticket, côté run
# (`<iid>.jsonl`, le flux de la session posé par #176, puis `.json`, `.log`, `.session`) ET worktree
# (l'index git, touché à chaque `git add`/`status` de la session). C'est le seul moyen de distinguer
# une session qui travaille d'une session morte : `<iid>.json` reste vide jusqu'à la toute fin.
activite_du_ticket() {
  local dir="$1" iid="$2" branche wt index t a
  a="$(plus_recent "$dir/$iid".* 2>/dev/null)" || a=""
  branche="$(branche_du_iid "$iid")"
  wt=""
  [ -n "$branche" ] && wt="$(worktree_de_branche "$branche")"
  if [ -n "$wt" ] && [ -d "$wt" ]; then
    index="$(git -C "$wt" rev-parse --git-path index 2>/dev/null)"
    # `--git-path` rend un chemin ABSOLU pour un worktree lié, mais RELATIF (« .git/index ») pour
    # un répertoire de travail principal : sans cette reprise, il serait résolu depuis le dossier
    # d'où l'on a lancé la commande, et l'activité passerait pour nulle.
    case "$index" in /* | ?:[/\\]*) ;; *) [ -n "$index" ] && index="$wt/$index" ;; esac
    if [ -n "$index" ] && t="$(plus_recent "$index" 2>/dev/null)"; then
      if [ -z "$a" ] || [ "$t" -gt "$a" ]; then a="$t"; fi
    fi
  fi
  [ -n "$a" ] || return 1
  printf '%s' "$a"
}

# --- GitLab (facultatif) ------------------------------------------------------------------------------
# Une lecture par cycle au plus, et jamais plus souvent qu'une minute : en `--watch` à 20 s, on
# n'inonde pas l'API pour un statut qui bouge deux fois par heure.
GITLAB_OK=0
GL_DERNIERE=0
GL_CACHE=""
GL_CACHE_IID=""

if [ "$SANS_GITLAB" = 0 ] && gl_require_glab 2>/dev/null; then GITLAB_OK=1; fi

# etat_gitlab <iid> <branche> : « statut <TAB> etat-mr <TAB> iid-mr », depuis le cache si la dernière
# lecture est récente. Rend une chaîne vide si GitLab n'est pas interrogeable — l'appelant le dit.
etat_gitlab() {
  local iid="$1" branche="$2" maintenant statut etat mr
  [ "$GITLAB_OK" = 1 ] || return 1
  maintenant="$(date +%s)"
  # Le cache porte l'iid : en `--watch`, la boucle passe au ticket suivant sans prévenir, et un
  # cache indexé sur le seul temps afficherait pendant une minute le statut du ticket précédent.
  if [ -n "$GL_CACHE" ] && [ "$GL_CACHE_IID" = "$iid" ] && [ $((maintenant - GL_DERNIERE)) -lt 60 ]; then
    printf '%s' "$GL_CACHE"
    return 0
  fi
  statut="$(gl_issue_owner "$iid" 2>/dev/null | cut -f1)"
  etat=""
  mr=""
  if [ -n "$branche" ]; then
    etat="$(gl_mr_state "$branche" 2>/dev/null)"
    [ "$etat" = "opened" ] && mr="$(gl_mr_iid "$branche" 2>/dev/null)"
  fi
  GL_CACHE="$(printf '%s\t%s\t%s' "$statut" "$etat" "$mr")"
  GL_CACHE_IID="$iid"
  GL_DERNIERE="$maintenant"
  printf '%s' "$GL_CACHE"
}

# --- Affichage -----------------------------------------------------------------------------------------

# ETAT est posé par affiche_run : c'est lui qui décide si `--watch` reboucle.
ETAT=""

affiche_ticket_en_cours() { # <run-dir> <iid>
  local dir="$1" iid="$2" titre branche wt debut age activite silence n corps statut etat_mr mr_iid gl

  titre="$(champ_plan "$dir" "$iid" 5)"
  titre_section "En cours — #$iid · ${titre:-?}"

  debut="$(plus_recent "$dir/$iid.session" "$dir/$iid.worktree.log" 2>/dev/null)" || debut=""
  if [ -n "$debut" ]; then
    age=$(( $(date +%s) - debut ))
    printf '   depuis     %s (démarré à %s)\n' "$(duree_lisible "$age")" "$(date -d "@$debut" '+%H:%M' 2>/dev/null || printf '?')"
  fi

  branche="$(branche_du_iid "$iid")"
  wt=""
  [ -n "$branche" ] && wt="$(worktree_de_branche "$branche")"

  if [ -z "$branche" ]; then
    printf '   branche    %saucune branche locale pour #%s — worktree pas encore monté%s\n' "$C_D" "$iid" "$C_0"
  elif [ -z "$wt" ] || [ ! -d "$wt" ]; then
    printf '   branche    %s %s(worktree non monté)%s\n' "$branche" "$C_D" "$C_0"
  else
    printf '   worktree   %s %s[%s]%s\n' "$wt" "$C_D" "$branche" "$C_0"

    # Les commits d'abord : c'est le signal le plus net qu'une session avance vraiment.
    if git -C "$wt" rev-parse --verify --quiet origin/main >/dev/null 2>&1; then
      n="$(git -C "$wt" rev-list --count origin/main..HEAD 2>/dev/null)"
      if [ "${n:-0}" -gt 0 ]; then
        printf '   commits    %s en avance sur origin/main\n' "$n"
        git -C "$wt" log --oneline -3 origin/main..HEAD 2>/dev/null |
          while IFS= read -r ligne; do printf '              %s%s%s\n' "$C_D" "$ligne" "$C_0"; done
      else
        printf '   commits    %saucun encore%s\n' "$C_D" "$C_0"
      fi
    fi

    # Puis les fichiers en attente : une session qui édite sans avoir encore commité.
    corps="$(git -C "$wt" status --porcelain 2>/dev/null)"
    if [ -n "$corps" ]; then
      n="$(printf '%s\n' "$corps" | grep -c '')"
      printf '   fichiers   %s modifié(s) : %s\n' "$n" \
        "$(printf '%s\n' "$corps" | awk '{ $1 = ""; sub(/^ /, ""); print }' | head -3 | paste -sd', ' -)"
      [ "$n" -gt 3 ] && printf '              %s… et %s autre(s)%s\n' "$C_D" "$((n - 3))" "$C_0"
    else
      printf '   fichiers   %saucune modification en attente%s\n' "$C_D" "$C_0"
    fi
  fi

  # L'activité : la plus récente écriture, côté run comme côté worktree. C'est ce qui distingue une
  # session qui réfléchit d'une session morte — sans certitude, mais avec une date.
  activite="$(activite_du_ticket "$dir" "$iid")" || activite=""
  if [ -n "$activite" ]; then
    silence=$(( $(date +%s) - activite ))
    if [ "$silence" -ge "$SEUIL_SILENCE" ]; then
      printf '   activité   %s⚠ rien d'\''écrit depuis %s — la session est peut-être bloquée ou morte%s\n' \
        "$C_Y" "$(duree_lisible "$silence")" "$C_0"
    else
      printf '   activité   il y a %s\n' "$(duree_lisible "$silence")"
    fi
  fi

  # GitLab en dernier : c'est ce que la boucle regardera pour rendre son verdict (MR ouverte ET
  # statut « En revue »), donc le voir bouger, c'est voir le ticket toucher au but.
  if gl="$(etat_gitlab "$iid" "$branche")"; then
    IFS=$'\t' read -r statut etat_mr mr_iid <<< "$gl"
    if [ "$etat_mr" = "opened" ]; then
      printf '   GitLab     ticket « %s » · MR !%s ouverte\n' "${statut:-?}" "${mr_iid:-?}"
    else
      printf '   GitLab     ticket « %s » · %s\n' "${statut:-?}" \
        "$([ -n "$etat_mr" ] && printf 'MR « %s »' "$etat_mr" || printf 'aucune MR')"
    fi
  elif [ "$SANS_GITLAB" = 1 ]; then
    printf '   GitLab     %snon interrogé (--no-gitlab)%s\n' "$C_D" "$C_0"
  else
    printf '   GitLab     %sinjoignable — glab absent ou non authentifié%s\n' "$C_D" "$C_0"
  fi
}

affiche_reste() { # <run-dir> <iid-en-cours>
  local dir="$1" courant="$2" rang iid titre lignes=""
  while IFS=$'\t' read -r rang iid _ _ titre; do
    [ -n "${iid:-}" ] || continue
    [ "$iid" = "$courant" ] && continue
    traite "$dir" "$iid" && continue
    lignes="$lignes$(printf '   %2s. #%-5s %s' "$rang" "$iid" "$titre")"$'\n'
  done < <(grep -v '^#' "$dir/plan.tsv" 2>/dev/null)
  [ -n "$lignes" ] || return 0
  titre_section "Reste au plan ($(printf '%s' "$lignes" | grep -c ''))"
  printf '%s' "$lignes"
}

affiche_bilan() { # <run-dir>
  local dir="$1" iid verdict mr duree cout raison totaux nb_ok nb_ko nb_saute somme_d somme_c
  [ -s "$dir/resume.tsv" ] || return 0
  totaux="$(awk -F'\t' '$1 !~ /^#/ {
      if ($2 == "OK") ok++; else if ($2 == "ECHEC") ko++; else saute++
      d += $4; c += $5
    } END { printf "%d\t%d\t%d\t%d\t%.2f", ok, ko, saute, d, c }' "$dir/resume.tsv")"
  IFS=$'\t' read -r nb_ok nb_ko nb_saute somme_d somme_c <<< "$totaux"
  [ "$((nb_ok + nb_ko + nb_saute))" -gt 0 ] || return 0

  titre_section "Traités ($((nb_ok + nb_ko + nb_saute))) — ${C_G}✓${C_0}${C_B} $nb_ok${C_0} · ${C_R}✗${C_0}${C_B} $nb_ko${C_0} · ${C_Y}~${C_0}${C_B} $nb_saute${C_0} · $(duree_lisible "$somme_d") · $somme_c \$"
  while IFS=$'\t' read -r iid verdict mr duree cout raison; do
    case "$iid" in '#'* | '') continue ;; esac
    case "$verdict" in
      OK)    printf '   %s✓%s #%-5s MR !%-5s %-8s %6.2f $\n' "$C_G" "$C_0" "$iid" "${mr:--}" "$(duree_lisible "${duree:-0}")" "${cout:-0}" ;;
      ECHEC) printf '   %s✗%s #%-5s %-9s %-8s %6.2f $  %s\n' "$C_R" "$C_0" "$iid" "${mr:--}" "$(duree_lisible "${duree:-0}")" "${cout:-0}" "${raison:--}" ;;
      *)     printf '   %s~%s #%-5s %-9s %-8s %6s     %s\n' "$C_Y" "$C_0" "$iid" "${mr:--}" "-" "-" "${raison:--}" ;;
    esac
  done < "$dir/resume.tsv"
}

# affiche_run <run-id> : tout l'écran, et pose ETAT (en-cours / termine / interrompu / vide).
affiche_run() {
  # `dir` sur sa propre ligne : bash développe TOUS les mots d'un `local` avant de créer les
  # variables, donc « local id="$1" dir="$ORCH_DIR/$id" » lirait l'`id` de l'appelant (inexistant
  # ici, et `set -u` le refuse).
  local id="$1" nb_plan nb_traites courant debut age activite code libelle silence
  local dir="$ORCH_DIR/$id"

  nb_plan=0
  [ -f "$dir/plan.tsv" ] && nb_plan="$(grep -cv '^#' "$dir/plan.tsv" 2>/dev/null)"
  nb_traites=0
  [ -f "$dir/resume.tsv" ] && nb_traites="$(grep -cv '^#' "$dir/resume.tsv" 2>/dev/null)"
  courant="$(ticket_en_cours "$dir")" || courant=""

  # L'état du run se déduit, faute de PID : le ticket en cours d'abord, le bilan complet ensuite, et
  # à défaut la fraîcheur des écritures. Un run tué au milieu laisse la même trace qu'un run qui
  # travaille — c'est la ligne « activité » qui tranche, et elle le dit sans prétendre à la
  # certitude.
  activite="$(plus_recent "$dir"/* 2>/dev/null)" || activite=""
  code=""
  if [ -f "$dir/run.log" ]; then
    code="$(grep -oE -- '--- run .* terminé \(code [0-9]+\)' "$dir/run.log" 2>/dev/null | tail -1 | grep -oE '[0-9]+\)$' | tr -d ')')"
  fi

  if [ "$nb_plan" -eq 0 ]; then ETAT=vide
  elif [ -n "$code" ]; then ETAT=termine
  elif [ -n "$courant" ]; then ETAT=en-cours
  elif [ "$nb_traites" -ge "$nb_plan" ]; then ETAT=termine
  elif [ -n "$activite" ] && [ $(( $(date +%s) - activite )) -lt 300 ]; then ETAT=en-cours
  else ETAT=interrompu
  fi

  # Un run « en cours » dont plus rien ne bouge est le cas qu'on doit repérer d'un coup d'œil : sans
  # PID à interroger, c'est la seule chose qui distingue une session qui travaille d'une session
  # morte, et l'en-tête doit le porter autant que la ligne « activité ».
  silence=""
  if [ "$ETAT" = "en-cours" ] && [ -n "$courant" ]; then
    silence="$(activite_du_ticket "$dir" "$courant")" || silence=""
    if [ -n "$silence" ]; then
      silence=$(( $(date +%s) - silence ))
      [ "$silence" -ge "$SEUIL_SILENCE" ] || silence=""
    fi
  fi

  case "$ETAT" in
    en-cours)   libelle="${C_G}en cours${C_0}"
                [ -n "$silence" ] && libelle="${C_Y}en cours ?${C_0} — rien d'écrit depuis $(duree_lisible "$silence")" ;;
    termine)    libelle="terminé${code:+ (code $code)}" ;;
    interrompu) libelle="${C_Y}interrompu${C_0} — plus rien n'a été écrit depuis $(duree_lisible "$(( $(date +%s) - ${activite:-0} ))")" ;;
    vide)       libelle="${C_D}sans plan${C_0} — répertoire créé, plan jamais écrit" ;;
  esac

  printf '%sRun %s%s — %s\n' "$C_B" "$id" "$C_0" "$libelle"
  printf '   journal    %s\n' "$dir"
  debut="$(debut_du_run "$id" "$dir")" || debut=""
  if [ -n "$debut" ]; then
    age=$(( $(date +%s) - debut ))
    printf '   démarré    %s (il y a %s)\n' "$(horodatage "$debut")" "$(duree_lisible "$age")"
  fi
  if [ "$nb_plan" -gt 0 ]; then
    printf '   plan       %s ticket(s) · %s traité(s)%s · %s à venir\n' \
      "$nb_plan" "$nb_traites" "$([ -n "$courant" ] && printf ' · 1 en cours')" \
      "$((nb_plan - nb_traites - $([ -n "$courant" ] && printf 1 || printf 0)))"
  fi
  [ -f "$STOP" ] && printf '   %s⚠ arrêt demandé%s — %s est présent : le run s'\''arrêtera entre deux tickets.\n' \
    "$C_Y" "$C_0" "$(relatif "$STOP")"

  if [ -n "$courant" ]; then
    affiche_ticket_en_cours "$dir" "$courant"
  elif [ "$ETAT" = "en-cours" ]; then
    titre_section "En cours — aucun ticket pris en main (entre deux tickets)"
  fi

  [ "$nb_plan" -gt 0 ] && affiche_reste "$dir" "$courant"
  affiche_bilan "$dir"

  titre_section "Suite"
  [ -f "$dir/run.log" ] && printf '   sortie     tail -f %s/run.log\n' "$(relatif "$dir")"
  case "$ETAT" in
    en-cours)
      printf '   suivre     bash scripts/orchestrate/status.sh --watch\n'
      printf '   arrêter    touch %s\n' "$(relatif "$STOP")"
      ;;
    interrompu)
      printf '   reprendre  bash scripts/orchestrate/run.sh --plan %s/plan.tsv\n' "$(relatif "$dir")"
      printf '              (les tickets déjà livrés sont sautés d'\''eux-mêmes)\n'
      ;;
    termine)
      printf '   revue      bash scripts/gitlab/lib.sh review-queue\n'
      printf '   ménage     bash scripts/git/worktree.sh remove <iid>  %s(après le merge, jamais avant)%s\n' "$C_D" "$C_0"
      ;;
  esac
  printf '\n'
}

# --- Point d'entrée -----------------------------------------------------------------------------------

if [ "$LISTE" = 1 ]; then
  liste="$(runs)" || liste=""
  if [ -z "$liste" ]; then
    printf 'Aucun run dans %s.\n' "$ORCH_DIR"
    exit 0
  fi
  printf '%sRuns connus%s — %s\n\n' "$C_B" "$C_0" "$ORCH_DIR"
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    dir="$ORCH_DIR/$id"
    np=0; [ -f "$dir/plan.tsv" ] && np="$(grep -cv '^#' "$dir/plan.tsv" 2>/dev/null)"
    nt=0; [ -f "$dir/resume.tsv" ] && nt="$(grep -cv '^#' "$dir/resume.tsv" 2>/dev/null)"
    d="$(debut_du_run "$id" "$dir")" || d=""
    printf '  %-18s %-17s %2s ticket(s) · %s traité(s)\n' "$id" "$([ -n "$d" ] && horodatage "$d")" "$np" "$nt"
  done <<< "$liste"
  printf '\nDétail : bash scripts/orchestrate/status.sh --run-id <id>\n'
  exit 0
fi

if [ -n "$RUN_ID" ]; then
  if [ ! -d "$ORCH_DIR/$RUN_ID" ]; then
    printf 'status.sh : aucun run « %s » dans %s.\n' "$RUN_ID" "$ORCH_DIR" >&2
    printf 'Les runs connus : bash scripts/orchestrate/status.sh --list\n' >&2
    exit 1
  fi
else
  RUN_ID="$(runs | tail -1)"
  # Pas de run du tout : c'est un état NORMAL (personne n'a encore lancé la boucle), pas une panne.
  if [ -z "$RUN_ID" ]; then
    printf 'Aucun run d'\''orchestration à ce jour (%s est vide ou absent).\n' "$ORCH_DIR"
    printf 'En lancer un : bash scripts/orchestrate/run.sh --dry-run   puis   --detach\n'
    exit 0
  fi
fi

if [ "$WATCH" = 0 ]; then
  affiche_run "$RUN_ID"
  exit 0
fi

# --- Suivi en boucle ------------------------------------------------------------------------------------
# On reste sur LE MÊME run que celui choisi au démarrage : suivre « le plus récent » ferait sauter
# l'affichage d'un run à l'autre dès qu'un second démarre, ce qui n'est jamais ce qu'on veut quand on
# regarde une console de suivi. Et on s'arrête dès que le run ne tourne plus : une boucle qui
# rafraîchit indéfiniment un run terminé n'apprend plus rien.
while :; do
  [ -t 1 ] && printf '\033[H\033[2J'
  printf '%s%s%s  ·  rafraîchi toutes les %s s  ·  Ctrl-C pour rendre la main\n\n' \
    "$C_D" "$(date '+%H:%M:%S')" "$C_0" "$INTERVALLE"
  affiche_run "$RUN_ID"
  [ "$ETAT" = "en-cours" ] || break
  sleep "$INTERVALLE"
done
exit 0
