#!/usr/bin/env bash
# Lancement local de la Control Tower en une commande (tickets #65, #149, #186).
#
# Nettoie d'abord les anciennes sessions (uniquement les processus qui écoutent
# sur les ports Maestro — jamais de kill large), puis démarre :
#   - l'API — port 8000 —, en MODE RÉEL par défaut (maestro.controltower.cli,
#     alias `maestro-api`) : bus Redis Pub/Sub et journal durable des événements
#     (#97), donc l'historique rendu au redémarrage. C'est la vraie orchestration
#     que le moteur alimente (`maestro-run --publier`, workers #41) ;
#   - l'UI Next.js (apps/web) pointée sur cette API — port 3000 ;
# ouvre l'UI dans le navigateur par défaut du poste (#200), et arrête l'ensemble
# dès que cette fenêtre est fermée. Le script, lui, rend la main tout de suite :
# c'est un chien de garde détaché qui attend la fermeture. Les logs vont dans un
# dossier temporaire, propre au couple de ports — deux sessions parallèles ne se
# marchent donc pas dessus.
#
# Le mode réel EXIGE Redis : il est vérifié AVANT de toucher à quoi que ce soit
# (`maestro-api --verifier-redis`, qui résout REDIS_URL et rend le geste exact
# pour le lancer). Absent, le script s'arrête en le disant — jamais de repli
# silencieux sur la démo, qui ferait prendre des données factices pour la
# réalité (#186). Ce scénario factice reste disponible, mais DEMANDÉ : `--demo`
# (app réelle sur bus mémoire + événements simulés, aucun Redis requis) — c'est
# le mode du développement front, du skill `verify` et des captures.
#
#   bash scripts/controltower/start.sh                     # (re)démarre tout + navigateur (réel)
#   bash scripts/controltower/start.sh --demo              # scénario factice, sans Redis
#   bash scripts/controltower/start.sh --no-browser        # sans navigateur ni arrêt auto
#   bash scripts/controltower/start.sh --stop              # arrête seulement (et SOLDE les runs en vol)
#   bash scripts/controltower/start.sh --diagnostic-navigateur  # dit quel navigateur serait ouvert
#
# ⚠ ARRÊTER LA CONTROL TOWER SOLDE SES RUNS (#700, docs/28 §11) — les DEUX gestes
# d'arrêt, « --stop » comme la fermeture de la fenêtre du navigateur. Depuis #441 un
# run vit dans un process détaché qui SURVIT à son API, et c'est ce qui rend
# inoffensifs le redémarrage après une modification, le crash et le SIGTERM :
# personne, là, n'a demandé d'arrêter le run. Fermer la fenêtre n'est pas de
# ceux-là — c'est le geste par lequel on quitte la Control Tower, et le chien de
# garde ci-dessous coupe déjà l'API et l'UI avec elle. Le run était la seule chose
# qui survivait à un arrêt que ce script tient pour volontaire partout ailleurs, et
# #699 a mesuré ce que cette survie coûte : sans API pour pomper le bus (Pub/Sub
# ÉPHÉMÈRE), le run continue de consommer du quota et PERD son historique — au
# retour, sa tâche finie est encore « en cours ». La survie ne le préservait plus,
# elle le rendait invisible et incorrigible (#486 le prévoyait pour « --stop » sous
# le nom d'« ombre portée » ; on le découvre valable pour la fenêtre fermée).
#
# Les deux gestes appellent donc « POST /api/extinction » AVANT de libérer les ports
# — chaque hôte détaché éteint AVEC SA DESCENDANCE, son run consigné « annulée » et
# non laissé « en cours ». Ce qu'ils ont interrompu se reprend au redémarrage par le
# bouton « Reprendre » de la Control Tower.
#
# LA LIGNE DE PARTAGE PASSE ENTRE ARRÊTER ET REDÉMARRER, jamais entre les deux
# gestes d'arrêt : l'appel vit dans ces deux branches-là et SURTOUT PAS dans
# « arreter_session », que le démarrage rejoue pour remplacer la session précédente.
# L'y mettre solderait les runs à chaque relance — le geste le plus fréquent du
# développement —, et la reprise (#349) ne repart pas de l'interruption : elle
# rejoue TOUTES les tâches depuis la synthèse du brief approuvé, et refuse net un run
# qui n'en a pas (mode « auto »), dont le travail serait perdu sans retour. Le
# redémarrage reste donc l'unique accident toléré, et sa fenêtre d'invisibilité est
# bornée par lui-même : l'API qui repart rejoue le journal, retrouve le run à
# l'écran, et l'interrompt par le BUS que le process écoute (#444) — là où une
# fenêtre fermée ne fait repartir personne.
#
# La distinction vit ICI et pas dans l'API, parce que c'est ici qu'on la connaît :
# un SIGTERM reçu par l'API ne dit pas s'il vient d'un arrêt propre ou d'un
# gestionnaire de tâches. MAESTRO_EXTINCTION=0 laisse délibérément tourner (annoncé
# au démarrage comme à l'arrêt, jamais silencieux) ; MAESTRO_EXTINCTION_DELAI
# déplace le plafond d'attente (20 s).
#
# Tout le reste est IDENTIQUE dans les deux modes : mêmes ports (donc mêmes
# dossiers de logs et profil de navigateur indexés dessus), même nettoyage des
# anciennes sessions, même chien de garde et même arrêt automatique à la
# fermeture de la fenêtre. Seul change ce qui alimente l'API.
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
# Deux dossiers, séparés par ce qu'on en fait (#234).
#
# Les JOURNAUX vont sous la racine du worktree : c'est vers eux que le script renvoie quand l'API ou
# l'UI ne répond pas, et cette raison-là doit rester lisible par une session autonome (docs/10 §11),
# qui n'a personne pour approuver l'ouverture d'un chemin absolu hors de son répertoire de travail.
# `.maestro/` est gitignoré. Ils restent INDEXÉS PAR LES PORTS (#152), une même racine pouvant
# porter deux stacks.
LOG_DIR_REL=".maestro/controltower/${PORT_API}-${PORT_UI}"
LOG_DIR="$RACINE/$LOG_DIR_REL"
# L'ÉTAT VOLATIL, lui, reste hors du dépôt : jeton de session, PID du chien de garde et profil
# jetable du navigateur ne se lisent jamais à la main, et un profil Chrome n'a rien à faire dans un
# répertoire de travail — worktree.sh place celui du MCP dans $HOME pour la même raison. Indexé par
# les ports pour le motif d'origine (#152) : deux sessions Claude Code — une par worktree — lancent
# chacune leur Control Tower, et un dossier commun leur ferait partager jeton et PID du chien de
# garde, la seconde arrêtant la première.
ETAT_DIR="${TMPDIR:-/tmp}/maestro-controltower-${PORT_API}-${PORT_UI}"
URL_UI="http://localhost:${PORT_UI}"
# Plafond d'attente du soldage des runs en vol (#486). L'API borne l'extinction de
# CHAQUE run à quelques secondes et les solde ENSEMBLE : ce délai n'est donc pas
# proportionnel au nombre de runs, et l'atteindre veut dire que l'API ne répond
# plus — auquel cas on libère les ports comme avant, sans rien attendre de plus.
DELAI_EXTINCTION="${MAESTRO_EXTINCTION_DELAI:-20}"

# Trace de la session courante. Le chien de garde ne nettoie que si le jeton qu'il
# surveille est toujours celui du fichier : un `--stop` manuel ou un relancement
# l'invalide, ce qui l'empêche d'arrêter une session qui n'est plus la sienne.
FICHIER_SESSION="$ETAT_DIR/session"
FICHIER_CHIEN="$ETAT_DIR/chien-de-garde.pid"

# Profil jetable de la fenêtre ouverte par le script. Son nom sert aussi de
# marqueur : c'est en cherchant les processus dont la ligne de commande le
# contient qu'on sait si la fenêtre est encore ouverte (voir navigateur_actif).
# Les ports en font partie, pour la même raison que LOG_DIR : la recherche se fait
# par SOUS-CHAÎNE, donc un marqueur commun à deux sessions ferait prendre à l'une
# la fenêtre de l'autre pour la sienne — et fermer_navigateur tuerait la mauvaise.
MARQUEUR_NAVIGATEUR="maestro-profil-navigateur-${PORT_API}-${PORT_UI}"
PROFIL_NAVIGATEUR="$ETAT_DIR/$MARQUEUR_NAVIGATEUR"

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

# Solde les runs en vol — l'arrêt VOLONTAIRE, et lui seul (#486, #700, docs/28 §11).
#
# Appelée AVANT qu'on tue l'API : c'est elle qui tient les hôtes détachés et sait les
# éteindre avec leur descendance. Deux appelants, et deux seulement — la branche
# « --stop » et le chien de garde qui constate la fenêtre fermée : les deux façons
# d'arrêter la Control Tower. Jamais depuis arreter_session, que le DÉMARRAGE rejoue
# pour remplacer la session précédente — l'y mettre solderait les runs à chaque
# relance, c'est-à-dire fabriquerait l'accident qu'on protège.
#
# Best-effort de bout en bout : une API qui ne répond pas (déjà arrêtée, ou dont la
# fenêtre vient d'être fermée) n'empêche pas d'arrêter le reste. On le DIT, en
# revanche : un run qui continue sans écran pour le suivre est précisément ce que
# ce ticket supprime, et le taire ferait chercher plus tard d'où vient la dépense.
solder_les_runs() {
  reponse=""
  runs=""
  if [ "${MAESTRO_EXTINCTION:-1}" = "0" ]; then
    echo "[extinction] désactivée (MAESTRO_EXTINCTION=0) — les runs en vol continuent sans écran pour les suivre"
    return 0
  fi
  reponse="$(curl -s -X POST --max-time "$DELAI_EXTINCTION" \
    "http://127.0.0.1:${PORT_API}/api/extinction" 2>/dev/null || true)"
  if [ -z "$reponse" ]; then
    echo "[extinction] l'API ne répond pas sur :${PORT_API} — rien à solder par ici"
    return 0
  fi
  # Les identifiants, extraits du JSON sans jq (pas un prérequis du dépôt) : la
  # réponse est une liste de résumés, et « run_id » n'y apparaît qu'une fois par run.
  runs="$(printf '%s' "$reponse" \
    | grep -o '"run_id"[[:space:]]*:[[:space:]]*"[^"]*"' \
    | sed 's/^.*"\([^"]*\)"$/\1/' || true)"
  if [ -z "$runs" ]; then
    echo "[extinction] aucun run en vol"
    return 0
  fi
  for run in $runs; do
    echo "[extinction] run ${run} interrompu — reprenable au redémarrage (bouton « Reprendre »)"
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
# Ce qui alimente l'API : « reel » (maestro-api sur Redis) par défaut depuis
# #186, « demo » sur demande explicite (bus mémoire + scénario factice).
STACK="reel"
while [ $# -gt 0 ]; do
  case "$1" in
    --stop) MODE="arreter" ;;
    --demo | --demonstration) STACK="demo" ;;
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
  printf 'stack: %s\n' "$STACK"
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
  mkdir -p "$LOG_DIR" "$ETAT_DIR"
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
  # Le jeton part d'abord : à partir d'ici la session n'est plus à personne, et un
  # « --stop » lancé pendant l'extinction ne se disputera pas la fenêtre avec nous.
  rm -f "$FICHIER_SESSION" "$FICHIER_CHIEN"
  # Fermer la fenêtre EST un arrêt de la Control Tower (#700) : on solde ses runs
  # comme « --stop », et AVANT de libérer les ports — après, l'API qui tient les
  # hôtes détachés n'existe plus. C'est le second des deux appelants, et le dernier.
  solder_les_runs
  liberer_port "$PORT_API" "API"
  liberer_port "$PORT_UI" "UI"
  exit 0
fi

# ── Préflight ────────────────────────────────────────────────────────────────
# AVANT le nettoyage : ce qui manque ici empêche de démarrer, et il n'y a aucune
# raison d'avoir arrêté la session en place (peut-être en train de servir) pour
# le découvrir ensuite.

# Toujours le python du venv (les dépendances ne sont que là — cf. CLAUDE.md).
if [ "$WINDOWS" = 1 ]; then
  PYTHON="$RACINE/.venv/Scripts/python.exe"
else
  PYTHON="$RACINE/.venv/bin/python"
fi
if [ "$MODE" = "demarrer" ] && [ ! -x "$PYTHON" ]; then
  echo "Python du venv introuvable : $PYTHON (créer le venv et installer les deps)" >&2
  exit 1
fi

# Mode réel : Redis est une dépendance dure. On la vérifie plutôt que de la
# supposer — et on ne retombe PAS sur la démo, qui donnerait des données
# factices pour la réalité. Le diagnostic (URL résolue, geste exact) vient du
# CLI de l'API, seul endroit où REDIS_URL est résolue.
if [ "$MODE" = "demarrer" ] && [ "$STACK" = "reel" ]; then
  if ! (cd "$RACINE" && "$PYTHON" -m maestro.controltower.cli --verifier-redis); then
    echo >&2
    echo "Mode réel impossible sans Redis — rien n'a été démarré ni arrêté." >&2
    echo "  · lancer Redis (ci-dessus), puis relancer cette commande ;" >&2
    echo "  · ou explorer l'UI sur un scénario factice, sans Redis :" >&2
    echo "      bash scripts/controltower/start.sh --demo" >&2
    exit 1
  fi
fi

# ── Nettoyage puis démarrage ─────────────────────────────────────────────────
# L'extinction des runs vient AVANT le nettoyage (qui tue l'API, seule à tenir les
# hôtes) et **seulement** sur « --stop » : un démarrage rejoue arreter_session pour
# remplacer la session précédente, et c'est le seul accident que #441 protège encore
# (#700 — la fermeture de la fenêtre, elle, solde depuis le chien de garde).
if [ "$MODE" = "arreter" ]; then
  solder_les_runs
fi

arreter_session

if [ "$MODE" = "arreter" ]; then
  echo "Control Tower arrêtée."
  exit 0
fi

mkdir -p "$LOG_DIR" "$ETAT_DIR"
cd "$RACINE" || exit 1

if [ "$STACK" = "demo" ]; then
  echo "[api] démarrage sur :${PORT_API} — mode démo, scénario factice (log : $LOG_DIR_REL/api.log)"
  nohup "$PYTHON" -m maestro.controltower.demo --port "$PORT_API" \
    >"$LOG_DIR/api.log" 2>&1 &
else
  echo "[api] démarrage sur :${PORT_API} — mode réel sur Redis (log : $LOG_DIR_REL/api.log)"
  nohup "$PYTHON" -m maestro.controltower.cli --port "$PORT_API" \
    >"$LOG_DIR/api.log" 2>&1 &
fi
if ! attendre_http "http://127.0.0.1:${PORT_API}/api/sante" 20; then
  echo "L'API ne répond pas sur :${PORT_API} — voir $LOG_DIR_REL/api.log" >&2
  exit 1
fi

echo "[ui] démarrage sur :${PORT_UI} (log : $LOG_DIR_REL/ui.log)"
cd "$RACINE/apps/web" || exit 1
NEXT_PUBLIC_MAESTRO_API_URL="http://127.0.0.1:${PORT_API}" PORT="$PORT_UI" \
  nohup npm run dev >"$LOG_DIR/ui.log" 2>&1 &
cd "$RACINE" || exit 1
# Next.js (turbopack) peut mettre un moment à compiler la première page.
if ! attendre_http "http://127.0.0.1:${PORT_UI}" 60; then
  echo "L'UI ne répond pas sur :${PORT_UI} — voir $LOG_DIR_REL/ui.log" >&2
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
if [ "$STACK" = "demo" ]; then
  echo "Control Tower prête (mode démo — scénario FACTICE) : $URL_UI"
else
  echo "Control Tower prête (mode réel) : $URL_UI"
  # Sans run, le poste de pilotage est vide — l'UI l'explique elle-même (#186),
  # on redit ici le geste depuis le terminal.
  echo "  alimenter : maestro-run --publier \"<objectif>\"  (le poste reste vide tant qu'aucun run ne publie)"
fi
echo "  API : http://127.0.0.1:${PORT_API}  ·  logs : $LOG_DIR_REL/"
if [ "$ARRET_AUTO" = 1 ]; then
  echo "  arrêt : fermer la fenêtre du navigateur (ou bash scripts/controltower/start.sh --stop)"
else
  echo "  arrêt : bash scripts/controltower/start.sh --stop"
fi
# Ce que l'arrêt fera des runs en vol se dit AU DÉMARRAGE, pas seulement au moment
# où il l'a fait (#700) : c'est ici qu'on part travailler en sachant ce que fermer
# la fenêtre emportera, et c'est ici que l'option se présente. Fermée par le chien
# de garde, la Control Tower nomme ce qu'elle solde dans navigateur.log — le
# terminal, lui, a déjà rendu la main.
if [ "${MAESTRO_EXTINCTION:-1}" = "0" ]; then
  echo "  ⚠ runs en vol : MAESTRO_EXTINCTION=0 — l'arrêt ne les soldera PAS, ils continueront sans écran pour les suivre"
elif [ "$ARRET_AUTO" = 1 ]; then
  echo "  runs en vol : soldés par l'arrêt (fermeture comprise), reprenables au redémarrage — trace : $LOG_DIR_REL/navigateur.log ; MAESTRO_EXTINCTION=0 pour laisser tourner"
else
  echo "  runs en vol : soldés par l'arrêt, reprenables au redémarrage (MAESTRO_EXTINCTION=0 pour laisser tourner)"
fi
