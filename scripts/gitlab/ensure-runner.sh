#!/usr/bin/env bash
# Assure qu'un runner CI de projet est EN LIGNE avant une opération qui dépend de la CI.
#
# Contexte (docs/10-workflow-git.md §8) : depuis #135 les runners partagés GitLab sont désactivés
# (`shared_runners_enabled=false`), donc les runners de PROJET du dépôt sont l'UNIQUE cible des
# pipelines. Si aucun n'est en ligne, les jobs restent `pending` et le merge (pipeline vert requis)
# est bloqué silencieusement. Ce helper, câblé dans les skills de clôture (/ticket-finish,
# /pipeline-fix, donc /ticket-ship par ricochet), remet la CI en ligne d'office.
#
# PLUSIEURS RUNNERS (#158) : le projet en compte désormais potentiellement plusieurs — un runner
# PARTAGÉ hébergé sur une machine qui reste allumée, plus le runner LOCAL de chaque poste en
# secours. N'importe lequel prend les jobs (tous non-taggés), donc le helper est un no-op DÈS QU'AU
# MOINS UN est `online` : inutile de réveiller Docker sur un portable quand la CI est déjà servie.
# Ce n'est que si AUCUN n'est en ligne qu'il monte celui de CETTE machine — résolu via
# `MAESTRO_RUNNER_ID`, puis les réglages locaux, puis l'API (voir runner_id).
#
# Idempotent. Quand il doit agir : démarre Docker Desktop si le démon n'est pas actif, (re)démarre
# le conteneur du runner, puis poll jusqu'à `online`. En cas d'échec (démon injoignable, timeout,
# glab non authentifié) il RENVOIE un code non nul avec un message — jamais une exception
# bloquante : l'appelant (skill de clôture) doit enchaîner malgré tout
# (`bash scripts/gitlab/ensure-runner.sh || …`).
#
# `--strict` (ou MAESTRO_RUNNER_STRICT=1) cible le runner de CETTE machine et ignore les autres :
# c'est ce que veut la mise en route (setup-runner.sh), qui rend compte du poste courant, pas de
# l'état global de la CI.
#
# Le runner n'est pas CRÉÉ ici : sa création (côté GitLab + conteneur) est le rôle de
# scripts/gitlab/setup-runner.sh, appelé par le parcours de mise en route (#146).
#
# Deux usages :
#   1. Exécuté :   bash scripts/gitlab/ensure-runner.sh [--strict]
#   2. Sourcé :    . scripts/gitlab/ensure-runner.sh ; ensure_runner [--strict]
#      (comme lib.sh, ce fichier n'impose pas son mode d'erreur quand il est sourcé : `set` n'est
#       activé que dans la branche d'exécution directe.)

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
racine="$(cd "$here/../.." && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$here/lib.sh"

# --- Configuration (surchargeable par variables d'environnement) --------------------------------
# Id du runner de projet. VOLONTAIREMENT SANS DÉFAUT CODÉ EN DUR (#146) : un id est propre à une
# machine, celui d'un poste est faux sur tous les autres. Il est résolu à l'exécution par
# runner_id() — variable d'environnement, puis réglages locaux, puis API GitLab.
MAESTRO_RUNNER_ID="${MAESTRO_RUNNER_ID:-}"
MAESTRO_RUNNER_NAME="${MAESTRO_RUNNER_NAME:-runner de projet}"   # ne sert qu'aux messages
# 1 = ne considérer QUE le runner de cette machine (voir --strict dans l'en-tête).
MAESTRO_RUNNER_STRICT="${MAESTRO_RUNNER_STRICT:-0}"
# Nom du conteneur Docker hébergeant le gitlab-runner.
MAESTRO_RUNNER_CONTAINER="${MAESTRO_RUNNER_CONTAINER:-gitlab-runner}"
# Réglages locaux de Claude Code (non versionnés) où setup-runner.sh persiste l'id du runner.
MAESTRO_LOCAL_SETTINGS="${MAESTRO_LOCAL_SETTINGS:-$racine/.claude/settings.local.json}"
# Chemin de l'exécutable Docker Desktop (Windows), lancé via Start-Process si le démon est éteint.
MAESTRO_DOCKER_DESKTOP="${MAESTRO_DOCKER_DESKTOP:-C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe}"
# Fenêtres de polling (secondes).
MAESTRO_DOCKER_TIMEOUT="${MAESTRO_DOCKER_TIMEOUT:-120}"   # attente du démon Docker
MAESTRO_RUNNER_TIMEOUT="${MAESTRO_RUNNER_TIMEOUT:-180}"   # attente du passage `online`
MAESTRO_RUNNER_POLL="${MAESTRO_RUNNER_POLL:-5}"           # intervalle de polling

log()  { printf '  %s\n' "$*" >&2; }
fail() { printf 'ensure-runner: %s\n' "$*" >&2; }

os_kind() {
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) echo windows ;;
    Darwin)               echo macos ;;
    Linux)                echo linux ;;
    *)                    echo unknown ;;
  esac
}

# Nom de la machine — sert à distinguer les runners quand le projet en compte plusieurs (un par
# poste). `hostname` manque sur certains conteneurs minimaux, d'où les replis.
machine_nom() {
  local nom
  nom="$(hostname 2>/dev/null)"
  [ -n "$nom" ] || nom="${COMPUTERNAME:-${HOSTNAME:-machine}}"
  printf '%s\n' "$nom"
}

# --- Résolution de l'id du runner ---------------------------------------------------------------
# Trois sources, de la plus explicite à la plus déduite. Aucune n'est codée en dur : le même clone
# doit fonctionner sur n'importe quel poste (#146).

# 1. Réglages locaux : env.MAESTRO_RUNNER_ID de .claude/settings.local.json, posé par
#    setup-runner.sh. Lecture en shell pur (grep/sed) comme le reste des helpers — la clé et sa
#    valeur sont des chiffres, aucun risque d'échappement JSON à interpréter.
runner_id_persiste() {
  local id
  [ -f "$MAESTRO_LOCAL_SETTINGS" ] || return 1
  id="$(tr -d ' \t\r\n' < "$MAESTRO_LOCAL_SETTINGS" \
        | grep -o '"MAESTRO_RUNNER_ID":"[0-9]\+"' | head -1 | grep -o '[0-9]\+')"
  [ -n "$id" ] || return 1
  printf '%s\n' "$id"
}

# Inventaire des runners de PROJET du dépôt, une ligne « <id>|<statut>|<description> » chacun.
# Source unique du parsing (la découverte d'id et le contrôle « un runner est-il en ligne ? » en
# dépendent tous les deux). La réponse arrive sur une seule ligne : on la redécoupe sur la frontière
# entre deux runners (`},{"id":`) pour rester en shell pur, puis on lit chaque champ dans SON
# enregistrement — indispensable depuis qu'ils sont plusieurs, un `grep -o` global mélangerait les
# statuts. Le premier `"id"` d'un enregistrement est bien celui du runner : celui de l'objet
# imbriqué `created_by` vient après.
runners_projet() {
  local raw ligne id statut desc
  raw="$(glab api "projects/$(gl_project_enc)/runners?type=project_type&per_page=100" 2>/dev/null)"
  [ -n "$raw" ] || return 1
  # `printf '%s\n'` et pas `'%s'` : la réponse de glab n'a pas de fin de ligne finale, et un
  # `read` sur une dernière ligne non terminée renvoie non nul — le corps de boucle serait sauté.
  # Le saut de ligne du remplacement sed est écrit en dur (« \ » suivi d'une vraie fin de ligne) :
  # `\n` côté remplacement est une extension GNU que le sed de macOS ne comprend pas.
  printf '%s\n' "$raw" \
    | sed 's/},{"id":/}\
{"id":/g' \
    | while IFS= read -r ligne; do
        id="$(printf '%s' "$ligne" | grep -o '"id":[0-9]\+' | head -1 | grep -o '[0-9]\+')"
        [ -n "$id" ] || continue
        statut="$(printf '%s' "$ligne" | grep -o '"status":"[^"]*"' | head -1 | cut -d'"' -f4)"
        desc="$(printf '%s' "$ligne" | grep -o '"description":"[^"]*"' | head -1 | cut -d'"' -f4)"
        printf '%s|%s|%s\n' "$id" "${statut:-inconnu}" "$desc"
      done
}

# Premier runner de projet EN LIGNE, quelle que soit la machine qui l'héberge : « <id>|<description> »
# sur la sortie standard, code non nul si aucun. C'est le contrôle qui rend ensure_runner no-op
# quand le runner partagé fait déjà le travail (#158).
runner_online_quelconque() {
  local ligne
  ligne="$(runners_projet | grep '|online|' | head -1)"
  [ -n "$ligne" ] || return 1
  printf '%s|%s\n' "${ligne%%|*}" "${ligne#*|online|}"
}

# 2. API GitLab : les runners de PROJET déclarés sur le dépôt. Un seul → c'est le nôtre. Plusieurs
#    (le runner partagé + un par poste) → celui dont la description porte le nom de cette machine,
#    convention posée par setup-runner.sh. On préfère le motif du runner LOCAL (`maestro-<machine>`)
#    au simple nom de machine : sur l'hôte du runner partagé, les deux descriptions le contiennent.
runner_id_decouvert() {
  local entrees id
  entrees="$(runners_projet)" || return 1
  [ -n "$entrees" ] || return 1

  if [ "$(printf '%s\n' "$entrees" | wc -l)" -eq 1 ]; then
    printf '%s\n' "${entrees%%|*}"
    return 0
  fi
  id="$(printf '%s\n' "$entrees" | grep -F "maestro-$(machine_nom)" | head -1 | cut -d'|' -f1)"
  [ -n "$id" ] || id="$(printf '%s\n' "$entrees" | grep -F "$(machine_nom)" | head -1 | cut -d'|' -f1)"
  if [ -z "$id" ]; then
    fail "plusieurs runners de projet et aucun au nom de cette machine ($(machine_nom)) — préciser MAESTRO_RUNNER_ID, ou monter le runner de ce poste (bash scripts/gitlab/setup-runner.sh)"
    return 1
  fi
  printf '%s\n' "$id"
}

# runner_id -> id du runner de cette machine. Mémoïse dans MAESTRO_RUNNER_ID (la résolution par
# l'API coûte un appel réseau, et ensure_runner interroge le statut en boucle).
runner_id() {
  local id
  if [ -n "$MAESTRO_RUNNER_ID" ]; then printf '%s\n' "$MAESTRO_RUNNER_ID"; return 0; fi
  if id="$(runner_id_persiste)" || id="$(runner_id_decouvert)"; then
    MAESTRO_RUNNER_ID="$id"
    printf '%s\n' "$id"
    return 0
  fi
  return 1
}

# Statut courant du runner ("online"/"offline"/… ; vide si injoignable).
runner_status() {
  local id
  id="$(runner_id)" || return 1
  glab api "runners/$id" 2>/dev/null \
    | grep -o '"status":"[^"]*"' | head -1 | cut -d'"' -f4
}
runner_is_online() { [ "$(runner_status)" = "online" ]; }

# Démon Docker joignable ?
docker_is_up() { command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }

# Démarre l'application Docker Desktop (best-effort, spécifique à l'OS).
start_docker_desktop() {
  case "$(os_kind)" in
    windows)
      if command -v powershell.exe >/dev/null 2>&1; then
        powershell.exe -NoProfile -Command "Start-Process -FilePath '$MAESTRO_DOCKER_DESKTOP'" >/dev/null 2>&1
      else
        fail "powershell.exe introuvable — démarrer Docker Desktop manuellement"
        return 1
      fi ;;
    macos)
      open -a Docker >/dev/null 2>&1 ;;
    linux)
      systemctl --user start docker-desktop >/dev/null 2>&1 ;;
    *)
      fail "OS non reconnu — démarrer Docker Desktop manuellement"
      return 1 ;;
  esac
}

# Boucle d'attente générique : <timeout> <intervalle> <commande…> ; renvoie 0 dès qu'elle réussit.
wait_until() {
  local timeout="$1" interval="$2"; shift 2
  local waited=0
  while [ "$waited" -lt "$timeout" ]; do
    if "$@"; then return 0; fi
    sleep "$interval"
    waited=$((waited + interval))
  done
  return 1
}

# Le conteneur du runner existe-t-il sur cette machine (démarré ou non) ?
conteneur_existe() {
  docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$MAESTRO_RUNNER_CONTAINER"
}

# S'assure que le conteneur du runner tourne (le démarre au besoin).
ensure_container() {
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$MAESTRO_RUNNER_CONTAINER"; then
    return 0
  fi
  if conteneur_existe; then
    log "démarrage du conteneur $MAESTRO_RUNNER_CONTAINER…"
    if docker start "$MAESTRO_RUNNER_CONTAINER" >/dev/null 2>&1; then
      return 0
    fi
    fail "échec du démarrage du conteneur $MAESTRO_RUNNER_CONTAINER"
    return 1
  fi
  fail "conteneur $MAESTRO_RUNNER_CONTAINER absent — le créer : bash scripts/gitlab/setup-runner.sh"
  return 1
}

# Point d'entrée : remet un runner en ligne si besoin. 0 = CI servie, non nul = échec propre.
#   ensure_runner            n'importe quel runner de projet en ligne suffit (défaut)
#   ensure_runner --strict   seul celui de CETTE machine compte
ensure_runner() {
  local id strict="$MAESTRO_RUNNER_STRICT" deja
  case "${1:-}" in
    --strict) strict=1 ;;
    "")       ;;
    *)        fail "option inconnue : $1 (attendu : --strict)"; return 2 ;;
  esac

  if ! gl_require_glab >/dev/null 2>&1; then
    fail "glab non authentifié — impossible de vérifier le runner (glab auth login)"
    return 1
  fi

  # Un autre runner du projet (typiquement le runner partagé toujours allumé) tient déjà la CI :
  # ne rien démarrer ici — c'est tout l'intérêt d'en avoir un permanent (#158).
  if [ "$strict" != 1 ] && deja="$(runner_online_quelconque)"; then
    log "runner de projet #${deja%%|*} déjà en ligne (${deja#*|}) — rien à démarrer."
    return 0
  fi

  if ! id="$(runner_id)"; then
    fail "aucun runner de projet trouvé pour ce clone — le créer : bash scripts/gitlab/setup-runner.sh"
    return 1
  fi
  MAESTRO_RUNNER_ID="$id"

  if runner_is_online; then
    log "$MAESTRO_RUNNER_NAME #$id déjà en ligne."
    return 0
  fi
  log "$MAESTRO_RUNNER_NAME #$id hors ligne — tentative de démarrage."

  # 1. Démon Docker.
  if ! docker_is_up; then
    if ! command -v docker >/dev/null 2>&1; then
      fail "docker introuvable — installer/démarrer Docker Desktop manuellement"
      return 1
    fi
    log "démarrage de Docker Desktop…"
    start_docker_desktop || true
    if ! wait_until "$MAESTRO_DOCKER_TIMEOUT" "$MAESTRO_RUNNER_POLL" docker_is_up; then
      fail "démon Docker toujours injoignable après ${MAESTRO_DOCKER_TIMEOUT}s"
      return 1
    fi
    log "démon Docker actif."
  fi

  # 2. Conteneur du runner.
  ensure_container || return 1

  # 3. Attente du passage `online`.
  if wait_until "$MAESTRO_RUNNER_TIMEOUT" "$MAESTRO_RUNNER_POLL" runner_is_online; then
    log "$MAESTRO_RUNNER_NAME #$id en ligne."
    return 0
  fi
  fail "$MAESTRO_RUNNER_NAME #$id toujours hors ligne après ${MAESTRO_RUNNER_TIMEOUT}s"
  return 1
}

# --- Exécution directe (pas quand sourcé) -------------------------------------------------------
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  set -uo pipefail
  ensure_runner "$@"
  exit $?
fi
