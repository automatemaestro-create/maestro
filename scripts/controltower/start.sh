#!/usr/bin/env bash
# Lancement local de la Control Tower en une commande (tickets #65, #149).
#
# Nettoie d'abord les anciennes sessions (uniquement les processus qui écoutent
# sur les ports Maestro — jamais de kill large), puis démarre :
#   - l'API de démo : app FastAPI réelle sur bus mémoire + scénario d'événements
#     factices publié en continu (maestro.controltower.demo) — port 8000 ;
#   - l'UI Next.js (apps/web) pointée sur cette API — port 3000 ;
# ouvre l'UI dans le navigateur par défaut du poste (#200), et arrête l'ensemble
# dès que cette fenêtre est fermée. Le script, lui, rend la main tout de suite :
# c'est un chien de garde détaché qui attend la fermeture. Les logs vont dans un
# dossier temporaire, propre au couple de ports — deux sessions parallèles ne se
# marchent donc pas dessus.
#
#   bash scripts/controltower/start.sh                     # (re)démarre tout + navigateur
#   bash scripts/controltower/start.sh --no-browser        # sans navigateur ni arrêt auto
#   bash scripts/controltower/start.sh --stop              # arrête seulement (nettoyage)
#   bash scripts/controltower/start.sh --diagnostic-navigateur  # dit quel navigateur serait ouvert
#
# Ports surchargables : MAESTRO_PORT_API (défaut 8000), MAESTRO_PORT_UI (3000). Un worktree créé
# par scripts/git/worktree.sh les reçoit d'office, dérivés du numéro de ticket (#152).
#
# Choix du navigateur (#200), dans l'ordre :
#   1. MAESTRO_BROWSER — binaire imposé (famille Chromium) ; il PRIME sur la détection.
#   2. Le navigateur PAR DÉFAUT DU POSTE, lu à chaud depuis l'association système (registre
#      Windows / xdg-settings Linux / LaunchServices macOS) — jamais figé dans le code : changer
#      le défaut du poste change celui qu'ouvre le script. Chromium ⇒ fenêtre isolée sur profil
#      jetable + arrêt auto ; hors Chromium (Firefox, Safari…) ⇒ ouverture dans CE navigateur via
#      l'ouvreur système, mais sans profil jetable ni arrêt auto — la limite est dite, pas masquée.
#   3. Défaut indétectable ⇒ repli sur un Chromium installé (comportement d'avant #200), annoncé.
# MAESTRO_BROWSER_DEFAUT court-circuite la détection OS (poste sans bureau, CI, tests) : on lui
# donne le binaire/identifiant du défaut, la classification Chromium/hors-Chromium s'applique
# ensuite comme d'habitude. « --diagnostic-navigateur » imprime le verdict sans rien ouvrir.

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

# Un Chromium INSTALLÉ sur la machine (Chrome/Edge/Chromium), premier trouvé — c'est le repli
# quand on ne sait pas lire le navigateur par défaut du poste (#200). Ses options (--user-data-dir,
# --new-window) sont celles de la fenêtre isolée. Ne consulte PAS MAESTRO_BROWSER : la priorité est
# gérée par resoudre_strategie.
chromium_installe() {
  local appdata_local candidats candidat nom chemin
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

# Famille d'un navigateur, d'après son binaire, son progId Windows, son .desktop Linux ou son
# bundle macOS — on classe par SOUS-CHAÎNE du nom, ce qui marche pour toutes ces formes à la fois.
# Un inconnu est prudemment rangé « autre » : on ne lui passera pas de drapeaux Chromium.
famille_de() {
  local base
  base="${1##*/}"
  base="$(printf '%s' "$base" | tr '[:upper:]' '[:lower:]')"
  case "$base" in
    *chrome* | *chromium* | *msedge* | *edge* | *brave* | *vivaldi* | *opera* | *thorium*)
      printf 'chromium' ;;
    *) printf 'autre' ;;
  esac
}

# Convertit un chemin Windows (C:\a\b) en chemin POSIX (/c/a/b) — via cygpath si présent.
vers_posix() {
  local p="$1"
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -u "$p" 2>/dev/null && return 0
  fi
  p="${p//\\//}"
  case "$p" in
    [A-Za-z]:/*) printf '/%s/%s' "$(printf '%s' "${p%%:*}" | tr '[:upper:]' '[:lower:]')" "${p#*:/}" ;;
    *) printf '%s' "$p" ;;
  esac
}

# Un jeton (chemin, nom court ou .desktop) → un exécutable invocable, ou 1. Sert au montage de la
# fenêtre isolée quand le défaut est Chromium.
chemin_executable() {
  local t="$1" base chemin
  [ -n "$t" ] || return 1
  if [ -x "$t" ]; then
    printf '%s' "$t"
    return 0
  fi
  base="${t##*/}"
  base="${base%.desktop}"
  chemin="$(command -v "$base" 2>/dev/null || true)"
  [ -n "$chemin" ] || return 1
  printf '%s' "$chemin"
}

# Le navigateur par défaut du poste, lu à chaud depuis l'association système « https ».
# Requête registre HKCU\...\UrlAssociations\https\UserChoice → progId → commande d'ouverture.
PS_DEFAUT='$ErrorActionPreference="SilentlyContinue"; $p=(Get-ItemProperty "HKCU:\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice").ProgId; if(-not $p){exit}; Write-Output $p; $c=(Get-ItemProperty "Registry::HKEY_CLASSES_ROOT\$p\shell\open\command")."(default)"; if($c){Write-Output $c}'

_defaut_os_windows() {
  local sortie progid cmd exe posix
  sortie="$(powershell -NoProfile -NonInteractive -Command "$PS_DEFAUT" 2>/dev/null | tr -d '\r')"
  progid="$(printf '%s\n' "$sortie" | sed -n '1p')"
  cmd="$(printf '%s\n' "$sortie" | sed -n '2p')"
  [ -n "$progid$cmd" ] || return 1
  # Extrait l'exécutable de la commande (« "C:\...\chrome.exe" --single-argument %1 »).
  case "$cmd" in
    \"*) exe="${cmd#\"}"; exe="${exe%%\"*}" ;;
    *) exe="${cmd%% *}" ;;
  esac
  if [ -n "$exe" ]; then
    posix="$(vers_posix "$exe")"
    printf '%s' "$posix"
    return 0
  fi
  # Faute d'exécutable lisible, le progId (ChromeHTML, FirefoxURL…) suffit à classer la famille.
  printf '%s' "$progid"
}

# Le binaire déclaré par un fichier .desktop (ligne Exec), les codes de champ %u/%U retirés.
_binaire_du_desktop() {
  local nom="$1" dir f ligne bin tok
  for dir in "${XDG_DATA_HOME:-$HOME/.local/share}/applications" \
    /usr/local/share/applications /usr/share/applications \
    /var/lib/flatpak/exports/share/applications \
    /var/lib/snapd/desktop/applications; do
    f="$dir/$nom"
    [ -f "$f" ] || continue
    ligne="$(grep -m1 '^Exec=' "$f" 2>/dev/null)"
    ligne="${ligne#Exec=}"
    bin="${ligne%% *}"
    [ -n "$bin" ] || continue
    # « Exec=env VAR=val binaire %u » : sauter env et les affectations pour retrouver le binaire.
    if [ "${bin##*/}" = "env" ]; then
      for tok in $ligne; do
        case "$tok" in env | *=*) continue ;; *) bin="$tok"; break ;; esac
      done
    fi
    printf '%s' "$bin"
    return 0
  done
  return 1
}

_defaut_os_linux() {
  local nom bin
  nom="$(xdg-settings get default-web-browser 2>/dev/null | head -1)"
  [ -n "$nom" ] || nom="$(xdg-mime query default x-scheme-handler/https 2>/dev/null | head -1)"
  [ -n "$nom" ] || return 1
  bin="$(_binaire_du_desktop "$nom" 2>/dev/null || true)"
  if [ -n "$bin" ]; then
    printf '%s' "$bin"
  else
    printf '%s' "$nom"
  fi
}

_defaut_os_macos() {
  local plist bundle app
  plist="$HOME/Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
  [ -f "$plist" ] || return 1
  # Le handler du schéma http, extrait du bloc { … } qui le déclare (ordre des clés variable).
  bundle="$(defaults read com.apple.LaunchServices/com.apple.launchservices.secure LSHandlers 2>/dev/null | awk '
    /\{/ { blk = ""; inblk = 1 }
    inblk { blk = blk $0 }
    /\}/ {
      if (inblk && blk ~ /LSHandlerURLScheme = https?;/ && match(blk, /LSHandlerRoleAll = "?[^";]+/)) {
        s = substr(blk, RSTART, RLENGTH); sub(/LSHandlerRoleAll = "?/, "", s); print s
      }
      inblk = 0
    }' | head -1)"
  [ -n "$bundle" ] || return 1
  case "$bundle" in
    com.google.chrome) app="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ;;
    com.microsoft.edgemac) app="/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" ;;
    com.brave.browser) app="/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" ;;
    org.chromium.chromium) app="/Applications/Chromium.app/Contents/MacOS/Chromium" ;;
    *) app="" ;;
  esac
  if [ -n "$app" ] && [ -x "$app" ]; then
    printf '%s' "$app"
  else
    printf '%s' "$bundle"
  fi
}

# Le navigateur par défaut du poste (jeton : chemin, nom court, .desktop ou bundle). MAESTRO_BROWSER_DEFAUT
# court-circuite la détection OS (poste sans bureau, CI, tests).
navigateur_defaut_os() {
  if [ -n "${MAESTRO_BROWSER_DEFAUT:-}" ]; then
    printf '%s' "$MAESTRO_BROWSER_DEFAUT"
    return 0
  fi
  if [ "$WINDOWS" = 1 ]; then
    _defaut_os_windows
    return
  fi
  case "$(uname -s 2>/dev/null)" in
    Darwin) _defaut_os_macos ;;
    *) _defaut_os_linux ;;
  esac
}

# La commande d'ouverture « dans le navigateur par défaut » du poste (repli hors Chromium).
ouvreur_systeme() {
  if [ "$WINDOWS" = 1 ]; then
    printf 'windows'
    return 0
  fi
  case "$(uname -s 2>/dev/null)" in
    Darwin) command -v open >/dev/null 2>&1 && { printf 'open'; return 0; } ;;
    *) command -v xdg-open >/dev/null 2>&1 && { printf 'xdg-open'; return 0; } ;;
  esac
  return 1
}

# Ouvre une URL dans le navigateur par défaut du poste (sans profil jetable ni arrêt auto).
ouvrir_dans_defaut() {
  local url="$1"
  if [ "$WINDOWS" = 1 ]; then
    powershell -NoProfile -NonInteractive -Command "Start-Process '$url'" >/dev/null 2>&1 && return 0
    cmd //c start "" "$url" >/dev/null 2>&1 && return 0
    return 1
  fi
  case "$(uname -s 2>/dev/null)" in
    Darwin) open "$url" >/dev/null 2>&1 ;;
    *) xdg-open "$url" >/dev/null 2>&1 ;;
  esac
}

# Résout, une fois, COMMENT ouvrir l'UI. Pose STRAT_MODE / STRAT_FAMILLE / STRAT_CIBLE / STRAT_SOURCE
# / STRAT_MSG. STRAT_MODE :
#   isole  → fenêtre Chromium sur profil jetable + chien de garde (STRAT_CIBLE = exécutable) ;
#   defaut → ouverture dans le navigateur par défaut via l'ouvreur système, sans arrêt auto ;
#   aucun  → rien d'ouvrable automatiquement, l'utilisateur ouvre l'URL à la main.
STRAT_MODE=""
STRAT_FAMILLE=""
STRAT_CIBLE=""
STRAT_SOURCE=""
STRAT_MSG=""
resoudre_strategie() {
  local defaut famille exe
  STRAT_MODE=""; STRAT_FAMILLE=""; STRAT_CIBLE=""; STRAT_SOURCE=""; STRAT_MSG=""

  # 1) MAESTRO_BROWSER prime toujours — fenêtre isolée Chromium forcée.
  if [ -n "${MAESTRO_BROWSER:-}" ]; then
    if [ -x "$MAESTRO_BROWSER" ]; then
      STRAT_MODE="isole"; STRAT_FAMILLE="chromium"; STRAT_CIBLE="$MAESTRO_BROWSER"
      STRAT_SOURCE="MAESTRO_BROWSER"
      STRAT_MSG="navigateur imposé par MAESTRO_BROWSER : $(basename "$MAESTRO_BROWSER")"
      return 0
    fi
    STRAT_MODE="aucun"; STRAT_FAMILLE="introuvable"; STRAT_SOURCE="MAESTRO_BROWSER"
    STRAT_MSG="MAESTRO_BROWSER introuvable ou non exécutable : $MAESTRO_BROWSER"
    return 0
  fi

  # 2) Le navigateur par défaut du poste, lu à chaud.
  defaut="$(navigateur_defaut_os 2>/dev/null || true)"
  if [ -n "$defaut" ]; then
    famille="$(famille_de "$defaut")"
    if [ "$famille" = "chromium" ]; then
      if exe="$(chemin_executable "$defaut")"; then
        STRAT_MODE="isole"; STRAT_FAMILLE="chromium"; STRAT_CIBLE="$exe"
        STRAT_SOURCE="defaut-os"
        STRAT_MSG="navigateur par défaut du poste : $(basename "$exe")"
        return 0
      fi
      if exe="$(chromium_installe)"; then
        STRAT_MODE="isole"; STRAT_FAMILLE="chromium"; STRAT_CIBLE="$exe"
        STRAT_SOURCE="repli-chromium"
        STRAT_MSG="défaut Chromium ($defaut) sans binaire localisable — repli sur $(basename "$exe")"
        return 0
      fi
      # Chromium par défaut mais aucun binaire trouvable : on tombe sur l'ouvreur système plus bas.
    else
      # Défaut hors Chromium (Firefox, Safari…) : on l'ouvre TEL QUEL, la limite dite (critère #200).
      STRAT_MODE="defaut"; STRAT_FAMILLE="autre"; STRAT_CIBLE="$defaut"
      STRAT_SOURCE="defaut-os"
      STRAT_MSG="navigateur par défaut hors famille Chromium ($(basename "${defaut%.desktop}")) — ouverture dans votre session, sans profil jetable ni arrêt automatique"
      return 0
    fi
  fi

  # 3) Défaut indétectable (ou Chromium sans binaire) → un Chromium installé, s'il y en a un.
  if exe="$(chromium_installe)"; then
    STRAT_MODE="isole"; STRAT_FAMILLE="chromium"; STRAT_CIBLE="$exe"
    STRAT_SOURCE="repli-chromium"
    STRAT_MSG="navigateur par défaut indétectable — repli sur un Chromium installé : $(basename "$exe")"
    return 0
  fi

  # 4) Aucun Chromium : ouvrir dans le défaut via l'ouvreur système, sinon renoncer.
  if ouvreur_systeme >/dev/null 2>&1; then
    STRAT_MODE="defaut"; STRAT_FAMILLE="autre"; STRAT_SOURCE="repli-ouvreur"
    STRAT_MSG="aucun Chromium trouvé — ouverture dans le navigateur par défaut du poste (sans arrêt automatique)"
    return 0
  fi
  STRAT_MODE="aucun"; STRAT_FAMILLE="introuvable"; STRAT_SOURCE="aucun"
  STRAT_MSG="aucun navigateur trouvé — ouvrir l'URL à la main"
  return 0
}

MODE="demarrer"
JETON_SURVEILLE=""
NAVIGATEUR_AUTO=1
while [ $# -gt 0 ]; do
  case "$1" in
    --stop) MODE="arreter" ;;
    --no-browser | --sans-navigateur) NAVIGATEUR_AUTO=0 ;;
    # Dit quel navigateur serait ouvert, et comment, sans rien démarrer ni ouvrir.
    --diagnostic-navigateur) MODE="diagnostic" ;;
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

# Stratégie navigateur résolue UNE FOIS, à chaud (association système + MAESTRO_BROWSER[_DEFAUT]).
# Le chien de garde, relancé via ce même script, la recalcule à l'identique (env et poste inchangés).
resoudre_strategie

# Diagnostic : imprime le verdict (et le profil/marqueur, propres aux ports — l'indépendance de deux
# sessions parallèles, #200/#152) sans démarrer la stack ni ouvrir de fenêtre.
if [ "$MODE" = "diagnostic" ]; then
  printf 'famille: %s\n' "$STRAT_FAMILLE"
  printf 'mode: %s\n' "$STRAT_MODE"
  printf 'source: %s\n' "$STRAT_SOURCE"
  printf 'cible: %s\n' "$STRAT_CIBLE"
  printf 'profil: %s\n' "$PROFIL_NAVIGATEUR"
  printf 'marqueur: %s\n' "$MARQUEUR_NAVIGATEUR"
  printf 'message: %s\n' "$STRAT_MSG"
  exit 0
fi

# Nom du binaire de la fenêtre ISOLÉE (mode « isole » seulement), puis les requêtes PowerShell qui
# s'en servent. Restreindre la recherche à ce binaire n'est pas cosmétique : sans ce filtre, la
# requête se compterait elle-même (sa propre ligne de commande contient le marqueur), et la fenêtre
# ne serait jamais vue comme fermée. Les quotes internes sont simples côté PowerShell, et les `$_`
# sont échappés : c'est du PowerShell, pas du shell.
NOM_NAVIGATEUR=""
if [ "$STRAT_MODE" = "isole" ] && [ -n "$STRAT_CIBLE" ]; then
  NOM_NAVIGATEUR="$(basename "$STRAT_CIBLE")"
fi
PS_SELECTION="Get-CimInstance Win32_Process | Where-Object { \$_.Name -eq '$NOM_NAVIGATEUR' -and \$_.CommandLine -like '*$MARQUEUR_NAVIGATEUR*' }"
PS_COMPTER="($PS_SELECTION | Measure-Object).Count"
PS_TERMINER="$PS_SELECTION | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }"

# ── Chien de garde ───────────────────────────────────────────────────────────
# Ouvre la fenêtre, attend sa fermeture, puis arrête la stack. Tourne détaché :
# c'est ce qui permet au script principal de rendre la main immédiatement.
if [ "$MODE" = "surveiller" ]; then
  mkdir -p "$LOG_DIR"
  # Le chien de garde ne surveille QUE la fenêtre isolée (mode « isole ») : hors de ce cas, il n'y
  # a pas de fenêtre à nous, donc rien à arrêter automatiquement.
  if [ "$STRAT_MODE" != "isole" ] || [ -z "$STRAT_CIBLE" ]; then
    echo "[chien de garde] pas de fenêtre isolée à surveiller (mode $STRAT_MODE) — abandon"
    rm -f "$FICHIER_SESSION" "$FICHIER_CHIEN"
    exit 1
  fi
  navigateur="$STRAT_CIBLE"
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
  case "$STRAT_MODE" in
    isole)
      # Fenêtre isolée (profil jetable) + chien de garde qui l'arrête à sa fermeture (#149).
      JETON="$$-$(date +%s)"
      printf '%s' "$JETON" >"$FICHIER_SESSION"
      nohup bash "$SCRIPT" --chien-de-garde "$JETON" \
        >>"$LOG_DIR/navigateur.log" 2>&1 &
      echo "$!" >"$FICHIER_CHIEN"
      ARRET_AUTO=1
      echo "[navigateur] $(basename "$STRAT_CIBLE") (défaut du poste) — fenêtre isolée sur $URL_UI, arrêt auto à la fermeture"
      ;;
    defaut)
      # Défaut hors Chromium : on l'ouvre tel quel, la limite dite plutôt que masquée (#200).
      echo "[navigateur] $STRAT_MSG"
      if ouvrir_dans_defaut "$URL_UI"; then
        echo "[navigateur] ouvert dans le navigateur par défaut — l'arrêter à la main : bash scripts/controltower/start.sh --stop"
      else
        echo "[navigateur] échec de l'ouverture automatique — ouvrir $URL_UI à la main"
      fi
      ;;
    *)
      echo "[navigateur] $STRAT_MSG"
      echo "[navigateur] (arrêt automatique indisponible ; MAESTRO_BROWSER pour désigner un binaire Chromium)"
      ;;
  esac
fi

echo
echo "Control Tower prête : $URL_UI"
echo "  API : http://127.0.0.1:${PORT_API}  ·  logs : $LOG_DIR/"
if [ "$ARRET_AUTO" = 1 ]; then
  echo "  arrêt : fermer la fenêtre du navigateur (ou bash scripts/controltower/start.sh --stop)"
else
  echo "  arrêt : bash scripts/controltower/start.sh --stop"
fi
