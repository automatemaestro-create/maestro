#!/usr/bin/env bash
# Captures de la Control Tower pour /milestone-presentation (#142), en une commande.
#
# Monte une stack DE PRODUCTION dédiée aux captures, sur ses propres ports :
#   1. installe playwright-core dans un dossier TEMPORAIRE (jamais dans le dépôt :
#      il n'a rien à faire dans le build de l'UI) — idempotent, réutilisé ensuite ;
#   2. démarre l'API de démo (maestro.controltower.demo) ;
#   3. construit l'UI (`next build`) et la sert (`next start`) ;
#   4. lance captures.mjs, qui photographie les pages du menu principal.
#
#   bash scripts/presentation/captures.sh --sortie <dossier> [--sans-demarrage] [--garder]
#
# Pourquoi un build de production et pas le `npm run dev` de scripts/controltower/start.sh :
# en mode dev, Next ouvre une WebSocket de rechargement à chaud (`/_next/webpack-hmr`) dont la
# poignée de main échoue dans le navigateur headless (ERR_INVALID_HTTP_RESPONSE) ; le bootstrap
# client reste bloqué là, les composants ne s'hydratent jamais et toutes les captures montrent
# « Reconnexion… / Chargement de l'état… ». Le build de production n'a pas cette socket — et
# c'est de toute façon l'application telle qu'on la présente qu'on veut photographier.
#
# `next build` écrit dans apps/web/.next, que le serveur de dev utilise aussi : la stack de
# développement éventuellement en cours est donc arrêtée d'abord (scripts/controltower/start.sh
# --stop), pour ne pas construire sous ses pieds.
#
# Par défaut la stack de captures est ARRÊTÉE en sortie (--garder la laisse tourner).
# Le code de retour dit si des captures utilisables ont été produites : l'appelant retombe
# alors sur une présentation sans visuels plutôt que d'échouer.

set -euo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CACHE_NODE="${TMPDIR:-/tmp}/maestro-presentation/node"
LOG_DIR="${TMPDIR:-/tmp}/maestro-presentation/logs"
# Ports dédiés : on ne marche pas sur ceux de la stack de développement (8000/3000).
PORT_API="${MAESTRO_PORT_API_CAPTURES:-8010}"
PORT_UI="${MAESTRO_PORT_UI_CAPTURES:-3010}"

SORTIE=""
DEMARRER=1
GARDER=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --sortie)         SORTIE="${2:-}"; shift 2 ;;
    --sans-demarrage) DEMARRER=0; shift ;;
    --garder)         GARDER=1; shift ;;
    *) echo "argument inconnu : $1" >&2; exit 2 ;;
  esac
done

if [ -z "$SORTIE" ]; then
  echo "usage: bash scripts/presentation/captures.sh --sortie <dossier> [--sans-demarrage] [--garder]" >&2
  exit 2
fi

mkdir -p "$SORTIE" "$LOG_DIR"

case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) WINDOWS=1 ;;
  *) WINDOWS=0 ;;
esac

# Les PID qui ÉCOUTENT sur un port (même repérage que scripts/controltower/start.sh).
pids_sur_port() {
  port="$1"
  if [ "$WINDOWS" = 1 ]; then
    netstat -ano 2>/dev/null \
      | awk -v port="$port" '$1 == "TCP" && $4 == "LISTENING" && $2 ~ (":" port "$") {print $5}' \
      | sort -u
  else
    lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null | sort -u || true
  fi
}

liberer_port() {
  port="$1"; quoi="$2"
  for pid in $(pids_sur_port "$port"); do
    echo "[captures] ancienne session ${quoi} sur :${port} (PID ${pid}) — arrêt"
    if [ "$WINDOWS" = 1 ]; then
      taskkill //F //PID "$pid" >/dev/null 2>&1 || true
    else
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
}

attendre_http() {
  url="$1"; delai="$2"; essais=0
  until curl -s -o /dev/null --max-time 2 "$url"; do
    essais=$((essais + 1))
    if [ "$essais" -ge "$delai" ]; then
      return 1
    fi
    sleep 1
  done
}

arreter_stack() {
  if [ "$DEMARRER" = 1 ] && [ "$GARDER" = 0 ]; then
    liberer_port "$PORT_UI" "UI"
    liberer_port "$PORT_API" "API"
  fi
}
trap arreter_stack EXIT

# --- 1. playwright-core, hors du dépôt ----------------------------------------------------------
# `npm install` dans un dossier temporaire, avec son propre package.json : sans lui, npm
# remonterait jusqu'à celui du dépôt.
if [ ! -d "$CACHE_NODE/node_modules/playwright-core" ]; then
  echo "[captures] installation de playwright-core dans $CACHE_NODE"
  mkdir -p "$CACHE_NODE"
  if [ ! -f "$CACHE_NODE/package.json" ]; then
    printf '{"name":"maestro-presentation-captures","private":true}\n' >"$CACHE_NODE/package.json"
  fi
  if ! npm --prefix "$CACHE_NODE" install playwright-core --no-audit --no-fund --silent; then
    echo "[captures] ⚠ installation de playwright-core impossible — pas de visuels" >&2
    exit 1
  fi
else
  echo "[captures] playwright-core déjà présent ($CACHE_NODE)"
fi

# --- 2. Stack de captures -------------------------------------------------------------------------
if [ "$DEMARRER" = 1 ]; then
  # La stack de dev partage apps/web/.next avec le build : on la range avant de construire.
  bash "$RACINE/scripts/controltower/start.sh" --stop >/dev/null 2>&1 || true
  liberer_port "$PORT_API" "API"
  liberer_port "$PORT_UI" "UI"

  if [ "$WINDOWS" = 1 ]; then
    PYTHON="$RACINE/.venv/Scripts/python.exe"
  else
    PYTHON="$RACINE/.venv/bin/python"
  fi
  if [ ! -x "$PYTHON" ]; then
    echo "[captures] ⚠ python du venv introuvable ($PYTHON) — pas de visuels" >&2
    exit 1
  fi

  echo "[captures] API de démo sur :${PORT_API} (log : $LOG_DIR/api.log)"
  (cd "$RACINE" && nohup "$PYTHON" -m maestro.controltower.demo --port "$PORT_API" \
    >"$LOG_DIR/api.log" 2>&1 &)
  if ! attendre_http "http://127.0.0.1:${PORT_API}/api/sante" 30; then
    echo "[captures] ⚠ l'API n'a pas démarré — voir $LOG_DIR/api.log" >&2
    exit 1
  fi

  echo "[captures] build de l'UI (log : $LOG_DIR/build.log)"
  if ! (cd "$RACINE/apps/web" \
        && NEXT_PUBLIC_MAESTRO_API_URL="http://127.0.0.1:${PORT_API}" \
           npm run build >"$LOG_DIR/build.log" 2>&1); then
    echo "[captures] ⚠ le build de l'UI a échoué — voir $LOG_DIR/build.log" >&2
    exit 1
  fi

  echo "[captures] UI sur :${PORT_UI} (log : $LOG_DIR/ui.log)"
  (cd "$RACINE/apps/web" \
    && NEXT_PUBLIC_MAESTRO_API_URL="http://127.0.0.1:${PORT_API}" \
       nohup npx next start --port "$PORT_UI" >"$LOG_DIR/ui.log" 2>&1 &)
  if ! attendre_http "http://127.0.0.1:${PORT_UI}" 60; then
    echo "[captures] ⚠ l'UI n'a pas démarré — voir $LOG_DIR/ui.log" >&2
    exit 1
  fi
fi

# --- 3. Captures ---------------------------------------------------------------------------------
MAESTRO_PLAYWRIGHT_HOME="$CACHE_NODE" \
  node "$RACINE/scripts/presentation/captures.mjs" \
  --sortie "$SORTIE" --base "http://127.0.0.1:${PORT_UI}"
