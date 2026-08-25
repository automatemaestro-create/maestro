#!/usr/bin/env bash
# Captures et démonstrations filmées de la Control Tower pour
# /milestone-presentation (#142, puis #545), en une commande.
#
# Monte une stack DE PRODUCTION dédiée aux captures, sur ses propres ports :
#   1. installe playwright-core — et ffmpeg, que le rendu vidéo exige et que
#      `-core` n'embarque pas — dans un dossier TEMPORAIRE (jamais dans le dépôt :
#      il n'a rien à faire dans le build de l'UI) — idempotent, réutilisé ensuite ;
#   2. déclare le PROJET de la démo, sans lequel l'UI ne montre que sa porte
#      d'entrée (voir plus bas) ;
#   3. démarre l'API de démo (maestro.controltower.demo) ;
#   4. construit l'UI (`next build`) et la sert (`next start`) ;
#   5. lance captures.mjs, qui photographie les pages du menu principal PUIS
#      filme les parcours de démonstration de `parcours.mjs`.
#
#   bash scripts/presentation/captures.sh --sortie <dossier> \
#        [--sans-demarrage] [--garder] [--sans-videos]
#
# Les parcours sont filmés PAR DÉFAUT : `--sans-videos` s'en passe (série plus
# courte, manifeste sans clip). Un appel `--sortie <dossier>` sans autre argument
# reste donc celui d'avant #545, enrichi de ses vidéos.
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
# Le CACHE reste hors du dépôt, à dessein : c'est une installation npm (playwright-core, puis le
# navigateur) de plusieurs centaines de Mo, partagée entre tous les clones et les worktrees, et le
# dépôt n'a pas à la porter. Il n'oriente personne vers rien à lire — juste le dossier où l'outil
# s'installe (#234).
CACHE_NODE="${TMPDIR:-/tmp}/maestro-presentation/node"
# Le dépôt de PROJETS que voit l'API de démo — hors du dépôt lui aussi, et pour
# une autre raison : c'est un état d'application, jamais quelque chose qu'on
# relit, et le poser sous `core/projets/` mêlerait un projet fictif à ceux de la
# personne qui lance la commande (#234 : ce que personne ne lit reste sous TMPDIR).
CACHE_PROJETS="${TMPDIR:-/tmp}/maestro-presentation/projets"
# Où Playwright range ce qu'il télécharge — ici son ffmpeg, et rien d'autre : les
# navigateurs ne passent pas par là, la stack pilote l'Edge de la machine par son
# `channel`. Poser la variable garde ce téléchargement dans le cache du script au
# lieu du dossier partagé du poste (~/AppData/Local/ms-playwright).
export PLAYWRIGHT_BROWSERS_PATH="${TMPDIR:-/tmp}/maestro-presentation/playwright"
# La racine du projet fictif : un dossier vide, qui n'existe que pour que l'écran
# « Projets » et le sélecteur du shell aient un chemin à afficher.
RACINE_DEMO="${TMPDIR:-/tmp}/maestro-presentation/mini-crm"
# Les JOURNAUX, eux, vont sous la racine du worktree : c'est vers eux que ce script renvoie quand
# l'API, le build ou l'UI échoue, et un chemin absolu hors du répertoire de travail met cette
# raison hors de portée d'une session autonome (docs/10 §11), qui n'a personne pour approuver sa
# lecture. `.maestro/` est gitignoré.
LOG_DIR_REL=".maestro/presentation"
LOG_DIR="$RACINE/$LOG_DIR_REL"
# Ports dédiés : on ne marche pas sur ceux de la stack de développement (8000/3000).
PORT_API="${MAESTRO_PORT_API_CAPTURES:-8010}"
PORT_UI="${MAESTRO_PORT_UI_CAPTURES:-3010}"

SORTIE=""
DEMARRER=1
GARDER=0
VIDEOS=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --sortie)         SORTIE="${2:-}"; shift 2 ;;
    --sans-demarrage) DEMARRER=0; shift ;;
    --garder)         GARDER=1; shift ;;
    --sans-videos)    VIDEOS=0; shift ;;
    *) echo "argument inconnu : $1" >&2; exit 2 ;;
  esac
done

if [ -z "$SORTIE" ]; then
  echo "usage: bash scripts/presentation/captures.sh --sortie <dossier>" \
       "[--sans-demarrage] [--garder] [--sans-videos]" >&2
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

# Le rendu vidéo passe par un ffmpeg que Playwright télécharge lui-même, et que `playwright-core`
# n'embarque pas — c'est tout ce qui sépare `-core` du paquet complet ici. Sans lui, `newContext`
# muni d'un `recordVideo` échoue à la création de la page : les captures ne s'en aperçoivent pas,
# mais AUCUN parcours ne se filme. On le pose donc avant, et son absence n'est jamais fatale — le
# tournage échouera parcours par parcours, en le disant dans le manifeste plutôt qu'en silence.
if [ "$VIDEOS" = 1 ]; then
  if ls -d "$PLAYWRIGHT_BROWSERS_PATH"/ffmpeg-* >/dev/null 2>&1; then
    echo "[captures] ffmpeg déjà présent ($PLAYWRIGHT_BROWSERS_PATH)"
  else
    echo "[captures] installation de ffmpeg (rendu vidéo) dans $PLAYWRIGHT_BROWSERS_PATH"
    if ! node "$CACHE_NODE/node_modules/playwright-core/cli.js" install ffmpeg; then
      echo "[captures] ⚠ ffmpeg indisponible — les parcours ne pourront pas être filmés" >&2
    fi
  fi
fi

# --- 2. Le projet de la démo ----------------------------------------------------------------------
# Sans projet actif, le shell de la Control Tower n'affiche PAS le tableau de bord : il rend sa
# porte d'entrée, « Choisir le projet » (#279). Or le scénario de démo estampille tout ce qu'il
# publie du projet `PROJET_ID` (maestro/controltower/demo.py) et le dépôt de projets d'un clone neuf
# est vide — toute la série, captures comprises, montrerait donc la porte.
#
# On déclare ce projet ici, dans un dépôt à nous : le nom de fichier fait foi sur l'identifiant
# (maestro/projets/store.py), ce que l'API de création ne permet pas — elle en engendre un aléatoire.
# L'identifiant est LU dans demo.py plutôt que recopié : le jour où le scénario change de projet, la
# stack de captures suit sans qu'on y pense. Côté navigateur, c'est captures.mjs qui pose le choix
# dans le localStorage (MAESTRO_PROJET_DEMO) — les deux moitiés sont indissociables.
PROJET_DEMO="$(sed -n 's/^PROJET_ID *= *"\([^"]*\)".*/\1/p' "$RACINE/maestro/controltower/demo.py" | head -n 1)"
PROJET_DEMO="${PROJET_DEMO:-prj-demo}"
export MAESTRO_PROJET_DEMO="$PROJET_DEMO"

if [ "$DEMARRER" = 1 ]; then
  mkdir -p "$CACHE_PROJETS" "$RACINE_DEMO"
  # `racine` est stockée en chemin POSIX (maestro/projets/modele.py) : les contre-obliques d'un
  # TMPDIR Windows casseraient le JSON avant même de casser l'affichage.
  printf '{"id":"%s","nom":"mini-CRM (démo)","racine":"%s","origine":"existant","vcs":null}\n' \
    "$PROJET_DEMO" "${RACINE_DEMO//\\//}" >"$CACHE_PROJETS/$PROJET_DEMO.json"
  export MAESTRO_PROJETS_DIR="$CACHE_PROJETS"
  echo "[captures] projet de démo « $PROJET_DEMO » déclaré dans $CACHE_PROJETS"
fi

if [ "$VIDEOS" = 1 ]; then
  echo "[captures] parcours filmés : oui (--sans-videos pour s'en passer)"
else
  echo "[captures] parcours filmés : non (--sans-videos)"
fi

# --- 3. Stack de captures -------------------------------------------------------------------------
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

  echo "[captures] API de démo sur :${PORT_API} (log : $LOG_DIR_REL/api.log)"
  (cd "$RACINE" && nohup "$PYTHON" -m maestro.controltower.demo --port "$PORT_API" \
    >"$LOG_DIR/api.log" 2>&1 &)
  if ! attendre_http "http://127.0.0.1:${PORT_API}/api/sante" 30; then
    echo "[captures] ⚠ l'API n'a pas démarré — voir $LOG_DIR_REL/api.log" >&2
    exit 1
  fi

  echo "[captures] build de l'UI (log : $LOG_DIR_REL/build.log)"
  if ! (cd "$RACINE/apps/web" \
        && NEXT_PUBLIC_MAESTRO_API_URL="http://127.0.0.1:${PORT_API}" \
           npm run build >"$LOG_DIR/build.log" 2>&1); then
    echo "[captures] ⚠ le build de l'UI a échoué — voir $LOG_DIR_REL/build.log" >&2
    exit 1
  fi

  echo "[captures] UI sur :${PORT_UI} (log : $LOG_DIR_REL/ui.log)"
  (cd "$RACINE/apps/web" \
    && NEXT_PUBLIC_MAESTRO_API_URL="http://127.0.0.1:${PORT_API}" \
       nohup npx next start --port "$PORT_UI" >"$LOG_DIR/ui.log" 2>&1 &)
  if ! attendre_http "http://127.0.0.1:${PORT_UI}" 60; then
    echo "[captures] ⚠ l'UI n'a pas démarré — voir $LOG_DIR_REL/ui.log" >&2
    exit 1
  fi
fi

# --- 4. Captures et parcours -----------------------------------------------------------------------
ARGS_CAPTURES=(--sortie "$SORTIE" --base "http://127.0.0.1:${PORT_UI}")
[ "$VIDEOS" = 1 ] || ARGS_CAPTURES+=(--sans-videos)

MAESTRO_PLAYWRIGHT_HOME="$CACHE_NODE" \
  node "$RACINE/scripts/presentation/captures.mjs" "${ARGS_CAPTURES[@]}"
