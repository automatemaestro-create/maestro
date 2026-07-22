#!/usr/bin/env bash
# Assure que le runner CI de projet local est EN LIGNE avant une opération qui dépend de la CI.
#
# Contexte (docs/10-workflow-git.md §8) : depuis #135 les runners partagés GitLab sont désactivés
# (`shared_runners_enabled=false`), donc `runner-local-poc` (poste Sam, exécuteur Docker Desktop)
# est l'UNIQUE cible des pipelines. S'il est hors ligne, les jobs restent `pending` et le merge
# (pipeline vert requis) est bloqué silencieusement. Ce helper, câblé dans les skills de clôture
# (/ticket-finish, /pipeline-fix, donc /ticket-ship par ricochet), le remet en ligne d'office.
#
# Idempotent : no-op si le runner est déjà `online`. Sinon : démarre Docker Desktop si le démon
# n'est pas actif, (re)démarre le conteneur du runner, puis poll jusqu'à `online`. En cas d'échec
# (démon injoignable, timeout, glab non authentifié) il RENVOIE un code non nul avec un message —
# jamais une exception bloquante : l'appelant (skill de clôture) doit enchaîner malgré tout
# (`bash scripts/gitlab/ensure-runner.sh || …`).
#
# Deux usages :
#   1. Exécuté :   bash scripts/gitlab/ensure-runner.sh
#   2. Sourcé :    . scripts/gitlab/ensure-runner.sh ; ensure_runner
#      (comme lib.sh, ce fichier n'impose pas son mode d'erreur quand il est sourcé : `set` n'est
#       activé que dans la branche d'exécution directe.)

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$here/lib.sh"

# --- Configuration (surchargeable par variables d'environnement) --------------------------------
# ID du runner de projet local (glab api runners/<id>). Documenté #135 ; surchargeable si le
# runner est ré-enregistré. MAESTRO_RUNNER_NAME ne sert qu'aux messages.
MAESTRO_RUNNER_ID="${MAESTRO_RUNNER_ID:-54385112}"
MAESTRO_RUNNER_NAME="${MAESTRO_RUNNER_NAME:-runner-local-poc}"
# Nom du conteneur Docker hébergeant le gitlab-runner.
MAESTRO_RUNNER_CONTAINER="${MAESTRO_RUNNER_CONTAINER:-gitlab-runner}"
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

# Statut courant du runner ("online"/"offline"/… ; vide si injoignable).
runner_status() {
  glab api "runners/$MAESTRO_RUNNER_ID" 2>/dev/null \
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

# S'assure que le conteneur du runner tourne (le démarre au besoin).
ensure_container() {
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$MAESTRO_RUNNER_CONTAINER"; then
    return 0
  fi
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$MAESTRO_RUNNER_CONTAINER"; then
    log "démarrage du conteneur $MAESTRO_RUNNER_CONTAINER…"
    if docker start "$MAESTRO_RUNNER_CONTAINER" >/dev/null 2>&1; then
      return 0
    fi
    fail "échec du démarrage du conteneur $MAESTRO_RUNNER_CONTAINER"
    return 1
  fi
  fail "conteneur $MAESTRO_RUNNER_CONTAINER absent — l'enregistrer d'abord (docs/10 §8)"
  return 1
}

# Point d'entrée : remet le runner en ligne si besoin. 0 = en ligne, non nul = échec propre.
ensure_runner() {
  if ! gl_require_glab >/dev/null 2>&1; then
    fail "glab non authentifié — impossible de vérifier le runner (glab auth login)"
    return 1
  fi

  if runner_is_online; then
    log "runner $MAESTRO_RUNNER_NAME déjà en ligne."
    return 0
  fi
  log "runner $MAESTRO_RUNNER_NAME hors ligne — tentative de démarrage."

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
    log "runner $MAESTRO_RUNNER_NAME en ligne."
    return 0
  fi
  fail "runner $MAESTRO_RUNNER_NAME toujours hors ligne après ${MAESTRO_RUNNER_TIMEOUT}s"
  return 1
}

# --- Exécution directe (pas quand sourcé) -------------------------------------------------------
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  set -uo pipefail
  ensure_runner
  exit $?
fi
