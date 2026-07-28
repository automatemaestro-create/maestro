#!/usr/bin/env bash
# Lancement local de la Control Tower en une commande (tickets #65, #149).
#
# Nettoie d'abord les anciennes sessions (uniquement les processus qui écoutent
# sur les ports Maestro — jamais de kill large), puis démarre :
#   - l'API de démo : app FastAPI réelle sur bus mémoire + scénario d'événements
#     factices publié en continu (maestro.controltower.demo) — port 8000 ;
#   - l'UI Next.js (apps/web) pointée sur cette API — port 3000 ;
# ouvre une fenêtre de navigateur sur l'UI, et arrête l'ensemble dès que cette
# fenêtre est fermée. Le script, lui, rend la main tout de suite : c'est un chien
# de garde détaché qui attend la fermeture. Les logs vont dans un dossier temporaire,
# propre au couple de ports — deux sessions parallèles ne se marchent donc pas dessus.
#
#   bash scripts/controltower/start.sh               # (re)démarre tout + navigateur
#   bash scripts/controltower/start.sh --no-browser  # sans navigateur ni arrêt auto
#   bash scripts/controltower/start.sh --stop        # arrête seulement (nettoyage)
#
# Ports surchargables : MAESTRO_PORT_API (défaut 8000), MAESTRO_PORT_UI (3000). Un worktree créé
# par scripts/git/worktree.sh les reçoit d'office, dérivés du numéro de ticket (#152).
# Navigateur surchargeable : MAESTRO_BROWSER (binaire de la famille Chromium).

set -euo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$RACINE/scripts/controltower/start.sh"
PORT_API="${MAESTRO_PORT_API:-8000}"
PORT_UI="${MAESTRO_PORT_UI:-3000}"
# Dossier de travail INDEXÉ PAR LES PORTS (#152) : deux sessions Claude Code — une par worktree —
# lancent chacune leur Control Tower sur ses propres ports. Un dossier commun leur ferait partager
# le jeton de session et le PID du chien de garde, et la seconde arrêterait la première.
LOG_DIR="${TMPDIR:-/tmp}/maestro-controltower-${PORT_API}-${PORT_UI}"
URL_UI="http://localhost:${PORT_UI}"

# Trace de la session courante. Le chien de garde ne nettoie que si le jeton qu'il
# surveille est toujours celui du fichier : un `--stop` manuel ou un relancement
# l'invalide, ce qui l'empêche d'arrêter une session qui n'est plus la sienne.
FICHIER_SESSION="$LOG_DIR/session"
FICHIER_CHIEN="$LOG_DIR/chien-de-garde.pid"

# Profil jetable de la fenêtre ouverte par le script. Son nom sert aussi de
# marqueur : c'est en cherchant les processus dont la ligne de commande le
# contient qu'on sait si la fenêtre est encore ouverte (voir navigateur_actif).
# Les ports en font partie, pour la même raison que LOG_DIR : la recherche se fait
# par SOUS-CHAÎNE, donc un marqueur commun à deux sessions ferait prendre à l'une
# la fenêtre de l'autre pour la sienne — et fermer_navigateur tuerait la mauvaise.
MARQUEUR_NAVIGATEUR="maestro-profil-navigateur-${PORT_API}-${PORT_UI}"
PROFIL_NAVIGATEUR="$LOG_DIR/$MARQUEUR_NAVIGATEUR"

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

# Termine un processus lancé par ce script (PID bash consigné dans un fichier —
# les PID relevés par netstat, eux, passent par liberer_port).
terminer_processus() {
  pid="$1"
  quoi="$2"
  [ -n "$pid" ] || return 0
  kill -0 "$pid" 2>/dev/null || return 0
  echo "[nettoyage] ${quoi} de la session précédente (PID ${pid}) — arrêt"
  kill "$pid" 2>/dev/null || true
  for _ in 1 2 3; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 1
  done
  kill -9 "$pid" 2>/dev/null || true
}

# La fenêtre ouverte par le script est-elle encore là ?
#
# On ne peut pas se fier au PID lancé : sur Windows, le premier msedge.exe/chrome.exe
# se relance et rend la main aussitôt (le PID meurt alors que la fenêtre reste
# ouverte). On cherche donc les processus dont la ligne de commande porte le
# marqueur du profil dédié — vrai tant qu'un processus du navigateur l'utilise,
# faux dès la dernière fenêtre fermée.
navigateur_actif() {
  [ -n "$NOM_NAVIGATEUR" ] || return 1
  if [ "$WINDOWS" = 1 ]; then
    compte="$(powershell -NoProfile -NonInteractive -Command "$PS_COMPTER" 2>/dev/null | tr -d '\r' | tail -1)"
    case "$compte" in
      '' | 0) return 1 ;;
      *) return 0 ;;
    esac
  else
    pgrep -f "$MARQUEUR_NAVIGATEUR" >/dev/null 2>&1
  fi
}

# Ferme la fenêtre ouverte par le script (et elle seule : le filtre porte sur le
# profil dédié, jamais sur le nom du navigateur — les fenêtres personnelles de
# l'utilisateur ne sont pas concernées).
fermer_navigateur() {
  navigateur_actif || return 0
  echo "[nettoyage] fenêtre du navigateur de la session précédente — fermeture"
  if [ "$WINDOWS" = 1 ]; then
    powershell -NoProfile -NonInteractive -Command "$PS_TERMINER" >/dev/null 2>&1 || true
  else
    pkill -f "$MARQUEUR_NAVIGATEUR" >/dev/null 2>&1 || true
  fi
}

# Arrêt complet de la session en place : chien de garde d'abord (il ne doit pas
# se réveiller pendant qu'on remonte la stack et confondre la nouvelle session
# avec l'ancienne), puis la fenêtre qu'il surveillait, puis les ports.
arreter_session() {
  if [ -f "$FICHIER_CHIEN" ]; then
    terminer_processus "$(cat "$FICHIER_CHIEN" 2>/dev/null || true)" "chien de garde"
  fi
  rm -f "$FICHIER_SESSION" "$FICHIER_CHIEN"
  fermer_navigateur
  liberer_port "$PORT_API" "API"
  liberer_port "$PORT_UI" "UI"
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

# Chemin d'un navigateur de la famille Chromium (Chrome/Edge/Chromium) : ce sont
# ses options qu'on passe plus bas (--user-data-dir, --new-window).
navigateur_choisi() {
  if [ -n "${MAESTRO_BROWSER:-}" ]; then
    if [ -x "$MAESTRO_BROWSER" ]; then
      printf '%s' "$MAESTRO_BROWSER"
      return 0
    fi
    echo "[navigateur] MAESTRO_BROWSER introuvable ou non exécutable : $MAESTRO_BROWSER" >&2
    return 1
  fi
  if [ "$WINDOWS" = 1 ]; then
    appdata_local="${LOCALAPPDATA:-}"
    candidats="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe
/c/Program Files/Microsoft/Edge/Application/msedge.exe
/c/Program Files/Google/Chrome/Application/chrome.exe
/c/Program Files (x86)/Google/Chrome/Application/chrome.exe
${appdata_local//\\//}/Google/Chrome/Application/chrome.exe"
  else
    candidats="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge
/Applications/Chromium.app/Contents/MacOS/Chromium"
  fi
  while IFS= read -r candidat; do
    [ -n "$candidat" ] || continue
    if [ -x "$candidat" ]; then
      printf '%s' "$candidat"
      return 0
    fi
  done <<EOF
$candidats
EOF
  for nom in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge microsoft-edge-stable; do
    chemin="$(command -v "$nom" 2>/dev/null || true)"
    if [ -n "$chemin" ]; then
      printf '%s' "$chemin"
      return 0
    fi
  done
  return 1
}

# Nom du binaire du navigateur, puis les requêtes PowerShell qui s'en servent.
# Restreindre la recherche à ce binaire n'est pas cosmétique : sans ce filtre, la
# requête se compterait elle-même (sa propre ligne de commande contient le
# marqueur), et la fenêtre ne serait jamais vue comme fermée. Les quotes internes
# sont simples côté PowerShell, et les `$_` sont échappés : c'est du PowerShell,
# pas du shell.
NOM_NAVIGATEUR=""
if chemin_navigateur="$(navigateur_choisi 2>/dev/null)"; then
  NOM_NAVIGATEUR="$(basename "$chemin_navigateur")"
fi
PS_SELECTION="Get-CimInstance Win32_Process | Where-Object { \$_.Name -eq '$NOM_NAVIGATEUR' -and \$_.CommandLine -like '*$MARQUEUR_NAVIGATEUR*' }"
PS_COMPTER="($PS_SELECTION | Measure-Object).Count"
PS_TERMINER="$PS_SELECTION | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }"

MODE="demarrer"
JETON_SURVEILLE=""
NAVIGATEUR_AUTO=1
while [ $# -gt 0 ]; do
  case "$1" in
    --stop) MODE="arreter" ;;
    --no-browser | --sans-navigateur) NAVIGATEUR_AUTO=0 ;;
    # Usage interne : le chien de garde se relance via ce même script.
    --chien-de-garde)
      MODE="surveiller"
      JETON_SURVEILLE="${2:-}"
      shift
      ;;
    *)
      echo "Option inconnue : $1" >&2
      exit 2
      ;;
  esac
  shift
done

# ── Chien de garde ───────────────────────────────────────────────────────────
# Ouvre la fenêtre, attend sa fermeture, puis arrête la stack. Tourne détaché :
# c'est ce qui permet au script principal de rendre la main immédiatement.
if [ "$MODE" = "surveiller" ]; then
  mkdir -p "$LOG_DIR"
  if ! navigateur="$(navigateur_choisi)"; then
    exit 1
  fi
  echo "[chien de garde] session ${JETON_SURVEILLE} — ouverture de $URL_UI"
  # Profil jetable et dédié, pour deux raisons : sans lui la commande délègue à
  # l'instance du navigateur déjà ouverte et rend la main aussitôt (plus rien à
  # surveiller) ; et un profil Chrome n'accepte qu'un seul consommateur à la fois
  # (verrou ProcessSingleton), donc réutiliser MAESTRO_CHROME_PROFILE — celui du
  # MCP chrome-maestro — bloquerait l'un ou l'autre.
  "$navigateur" \
    --user-data-dir="$PROFIL_NAVIGATEUR" \
    --no-first-run \
    --no-default-browser-check \
    --new-window "$URL_UI" >/dev/null 2>&1 &

  # Laisser la fenêtre apparaître avant de surveiller sa disparition.
  apparue=0
  for _ in $(seq 1 30); do
    if navigateur_actif; then
      apparue=1
      break
    fi
    sleep 1
  done
  if [ "$apparue" = 0 ]; then
    echo "[chien de garde] la fenêtre ne s'est pas ouverte — arrêt automatique abandonné"
    rm -f "$FICHIER_SESSION" "$FICHIER_CHIEN"
    exit 1
  fi
  echo "[chien de garde] fenêtre ouverte — arrêt automatique armé"
  while navigateur_actif; do
    sleep 3
  done

  jeton_courant="$(cat "$FICHIER_SESSION" 2>/dev/null || true)"
  if [ "$jeton_courant" != "$JETON_SURVEILLE" ]; then
    echo "[chien de garde] session déjà remplacée ou arrêtée — rien à faire"
    exit 0
  fi
  echo "[chien de garde] fenêtre fermée — arrêt de la Control Tower"
  rm -f "$FICHIER_SESSION" "$FICHIER_CHIEN"
  liberer_port "$PORT_API" "API"
  liberer_port "$PORT_UI" "UI"
  exit 0
fi

# ── Nettoyage puis démarrage ─────────────────────────────────────────────────
arreter_session

if [ "$MODE" = "arreter" ]; then
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

ARRET_AUTO=0
if [ "$NAVIGATEUR_AUTO" = 1 ]; then
  if navigateur="$(navigateur_choisi)"; then
    JETON="$$-$(date +%s)"
    printf '%s' "$JETON" >"$FICHIER_SESSION"
    nohup bash "$SCRIPT" --chien-de-garde "$JETON" \
      >>"$LOG_DIR/navigateur.log" 2>&1 &
    echo "$!" >"$FICHIER_CHIEN"
    ARRET_AUTO=1
    echo "[navigateur] $(basename "$navigateur") — fenêtre sur $URL_UI"
  else
    echo "[navigateur] aucun navigateur de la famille Chromium trouvé — ouvrir $URL_UI à la main"
    echo "[navigateur] (arrêt automatique désactivé ; MAESTRO_BROWSER pour désigner un binaire)"
  fi
fi

echo
echo "Control Tower prête : $URL_UI"
echo "  API : http://127.0.0.1:${PORT_API}  ·  logs : $LOG_DIR/"
if [ "$ARRET_AUTO" = 1 ]; then
  echo "  arrêt : fermer la fenêtre du navigateur (ou bash scripts/controltower/start.sh --stop)"
else
  echo "  arrêt : bash scripts/controltower/start.sh --stop"
fi
