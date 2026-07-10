#!/usr/bin/env bash
# Lancement local de la Control Tower en une commande (ticket #65).
#
# Nettoie d'abord les anciennes sessions (uniquement les processus qui écoutent
# sur les ports Maestro — jamais de kill large), puis démarre :
#   - l'API de démo : app FastAPI réelle sur bus mémoire + scénario d'événements
#     factices publié en continu (maestro.controltower.demo) — port 8000 ;
#   - l'UI Next.js (apps/web) pointée sur cette API — port 3000 ;
# et affiche l'URL à ouvrir. Les logs vont dans un dossier temporaire.
#
#   bash scripts/controltower/start.sh          # (re)démarre tout
#   bash scripts/controltower/start.sh --stop   # arrête seulement (nettoyage)
#
# Ports surchargables : MAESTRO_PORT_API (défaut 8000), MAESTRO_PORT_UI (3000).

set -euo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PORT_API="${MAESTRO_PORT_API:-8000}"
PORT_UI="${MAESTRO_PORT_UI:-3000}"
LOG_DIR="${TMPDIR:-/tmp}/maestro-controltower"

# Windows (Git Bash) ou Unix : le repérage des PID par port et le kill diffèrent.
case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) WINDOWS=1 ;;
  *) WINDOWS=0 ;;
esac

# Les PID qui ÉCOUTENT sur le port donné (rien d'autre : ni clients, ni autres ports).
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

# Termine les processus qui écoutent sur le port (ancienne session à remplacer).
liberer_port() {
  port="$1"
  quoi="$2"
  pids="$(pids_sur_port "$port")"
  if [ -z "$pids" ]; then
    echo "[nettoyage] rien n'écoute sur :${port} (${quoi})"
    return 0
  fi
  for pid in $pids; do
    echo "[nettoyage] ancienne session ${quoi} sur :${port} (PID ${pid}) — arrêt"
    if [ "$WINDOWS" = 1 ]; then
      taskkill //F //PID "$pid" >/dev/null 2>&1 || true
    else
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
}

# Attend qu'une URL réponde en HTTP (1 essai/s, délai max en secondes).
attendre_http() {
  url="$1"
  delai="$2"
  essais=0
  until curl -s -o /dev/null --max-time 2 "$url"; do
    essais=$((essais + 1))
    if [ "$essais" -ge "$delai" ]; then
      return 1
    fi
    sleep 1
  done
}

liberer_port "$PORT_API" "API"
liberer_port "$PORT_UI" "UI"

if [ "${1:-}" = "--stop" ]; then
  echo "Control Tower arrêtée."
  exit 0
fi

mkdir -p "$LOG_DIR"
cd "$RACINE" || exit 1

# Toujours le python du venv (les dépendances ne sont que là — cf. CLAUDE.md).
if [ "$WINDOWS" = 1 ]; then
  PYTHON="$RACINE/.venv/Scripts/python.exe"
else
  PYTHON="$RACINE/.venv/bin/python"
fi
if [ ! -x "$PYTHON" ]; then
  echo "Python du venv introuvable : $PYTHON (créer le venv et installer les deps)" >&2
  exit 1
fi

echo "[api] démarrage sur :${PORT_API} (log : $LOG_DIR/api.log)"
nohup "$PYTHON" -m maestro.controltower.demo --port "$PORT_API" \
  >"$LOG_DIR/api.log" 2>&1 &
if ! attendre_http "http://127.0.0.1:${PORT_API}/api/sante" 20; then
  echo "L'API ne répond pas sur :${PORT_API} — voir $LOG_DIR/api.log" >&2
  exit 1
fi

echo "[ui] démarrage sur :${PORT_UI} (log : $LOG_DIR/ui.log)"
cd "$RACINE/apps/web" || exit 1
NEXT_PUBLIC_MAESTRO_API_URL="http://127.0.0.1:${PORT_API}" PORT="$PORT_UI" \
  nohup npm run dev >"$LOG_DIR/ui.log" 2>&1 &
cd "$RACINE" || exit 1
# Next.js (turbopack) peut mettre un moment à compiler la première page.
if ! attendre_http "http://127.0.0.1:${PORT_UI}" 60; then
  echo "L'UI ne répond pas sur :${PORT_UI} — voir $LOG_DIR/ui.log" >&2
  exit 1
fi

echo
echo "Control Tower prête : http://localhost:${PORT_UI}"
echo "  API : http://127.0.0.1:${PORT_API}  ·  logs : $LOG_DIR/"
echo "  arrêt : bash scripts/controltower/start.sh --stop"
