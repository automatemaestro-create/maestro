#!/usr/bin/env bash
# Ouvre Maestro dans une FENÊTRE NATIVE (ticket #923, docs/35 §2).
#
#   bash scripts/controltower/desktop.sh            # fenêtre + stack locale réelle (Redis)
#   bash scripts/controltower/desktop.sh --demo     # idem, sur le scénario factice
#
# C'est la commande unique du premier critère d'acceptation : elle ouvre la fenêtre, et fermer
# cette fenêtre arrête l'API comme l'UI. Elle ne démarre ni n'arrête rien elle-même — c'est la
# coque (`apps/desktop/main.js`) qui appelle `start.sh --no-browser` puis `start.sh --stop`, parce
# que la fenêtre doit exister PENDANT le démarrage : une stack qu'on ne pourrait pas arrêter tant
# qu'elle n'a pas fini de démarrer serait exactement ce que le critère interdit.
#
# Ce script ne fait donc que ce qu'une coque ne peut pas faire pour elle-même :
#   1. lui donner le NODE DU DÉPÔT (.node-version → .tools/node/), jamais celui du poste ;
#   2. s'assurer qu'Electron est là — binaire compris, voir `electron_pret` ;
#   3. lui désigner le BASH qui jouera `start.sh` (sous Windows, `bash` nu peut être celui de WSL).
#
# ⚠ CE N'EST PAS L'EMPAQUETAGE. Ce que « L'atelier » livre est une coque de DÉVELOPPEMENT, qui sert
# la stack locale du dépôt (docs/35 §2.5) : l'installeur sans Python ni Node reste #641, en Phase 9.
# Les prérequis sont donc ceux du dépôt — venv, Redis en mode réel, `setup.sh` passé.

set -euo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COQUE="$RACINE/apps/desktop"

# Electron pèse lourd (voir docs/35 §2.3 pour la mesure) et n'est requis que par ce script : il est
# donc installé À LA DEMANDE, au premier lancement, et pas par `setup.sh`. Un clone qui n'ouvrira
# jamais la fenêtre ne paie rien ; celui qui la veut paie une fois, en le sachant.
TAILLE_ANNONCEE="≈ 158 Mo à télécharger, ≈ 370 Mo sur le disque"

usage() {
  sed -n '2,5p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

case "${1:-}" in
  -h | --help)
    usage
    exit 0
    ;;
esac

# --- Node du dépôt ---------------------------------------------------------------------------
# Même résolution que `scripts/mcp/playwright-mcp.mjs` et `setup.sh` : la version vient du dépôt,
# pas du gestionnaire du poste (un `nvm use 18` ne doit rien casser). Absent, on laisse le Node du
# PATH tenter sa chance et on le DIT — mieux vaut un lancement qui échoue en nommant sa cause
# qu'un script qui refuse sur un clone par ailleurs fonctionnel.
NODE_PIN="$(tr -d ' \r\n' <"$RACINE/.node-version" 2>/dev/null || true)"
NODE_PIN="${NODE_PIN#v}"
if [ -n "$NODE_PIN" ]; then
  if [ "$(uname -s)" = "Linux" ] || [ "$(uname -s)" = "Darwin" ]; then
    NODE_BIN="$RACINE/.tools/node/v$NODE_PIN/bin"
  else
    NODE_BIN="$RACINE/.tools/node/v$NODE_PIN"
  fi
  if [ -d "$NODE_BIN" ]; then
    export PATH="$NODE_BIN:$PATH"
  else
    echo "[coque] Node v$NODE_PIN absent de .tools/node/ — on tente avec celui du PATH." >&2
    echo "[coque] le provisionner : bash scripts/setup.sh --only node" >&2
  fi
fi

if ! command -v node >/dev/null 2>&1; then
  echo "Node introuvable — lancer : bash scripts/setup.sh --only node" >&2
  exit 1
fi

# --- Electron -------------------------------------------------------------------------------
# La présence de `node_modules/electron/` ne prouve RIEN : le paquet npm ne contient que quelques
# fichiers JS, et c'est son script de post-installation qui télécharge le runtime sous `dist/`.
# Ce post-install ne tourne pas toujours (mesuré sur le poste de référence au 2026-09-11 : `npm
# install` a rendu « added 13 packages » sans jamais créer `dist/`, laissant un paquet inerte).
# On vérifie donc LE BINAIRE, seule chose qu'on va exécuter, et jamais le dossier qui le contient.
electron_pret() {
  [ -d "$COQUE/node_modules/electron/dist" ] && [ -s "$COQUE/node_modules/electron/path.txt" ]
}

if ! electron_pret; then
  if [ ! -d "$COQUE/node_modules/electron" ]; then
    echo "[coque] première ouverture : installation d'Electron ($TAILLE_ANNONCEE)…"
    (cd "$COQUE" && npm install --no-audit --no-fund)
  fi
  # Le paquet est là mais son runtime manque : on rejoue le post-install pour lui seul, plutôt
  # que de réinstaller l'arbre entier — c'est la réparation exacte du cas décrit ci-dessus.
  if ! electron_pret; then
    echo "[coque] runtime Electron manquant — récupération ($TAILLE_ANNONCEE)…"
    (cd "$COQUE" && node node_modules/electron/install.js)
  fi
  if ! electron_pret; then
    echo "Electron n'a pas pu être installé dans apps/desktop — voir la sortie ci-dessus." >&2
    exit 1
  fi
fi

# --- Le bash qui jouera start.sh ---------------------------------------------------------------
# `main.js` lance `start.sh` par un `spawn` : sous Windows, `bash` nu peut se résoudre en bash WSL,
# qui ne voit ni le même système de fichiers ni le venv du dépôt. On lui passe donc CELUI QUI NOUS
# EXÉCUTE, converti en chemin natif quand il le faut — Node, lui, est un programme Windows et ne
# sait pas ouvrir « /usr/bin/bash ».
BASH_POUR_COQUE="${BASH:-$(command -v bash)}"
if command -v cygpath >/dev/null 2>&1; then
  BASH_POUR_COQUE="$(cygpath -w "$BASH_POUR_COQUE")"
fi
export MAESTRO_BASH="$BASH_POUR_COQUE"

# --- ELECTRON_RUN_AS_NODE : le piège du terminal de VS Code -------------------------------------
# Posée, elle fait démarrer Electron en NODE PUR : pas de fenêtre, et `require('electron')` rend le
# chemin du binaire au lieu du module — donc `app` vaut `undefined` et la coque meurt sur un
# « Cannot read properties of undefined », qui ne nomme évidemment pas sa cause.
#
# Or VS Code EST une application Electron, et il pose cette variable pour les processus qu'il
# lance : tout terminal intégré en hérite (mesuré le 2026-09-11 sur le poste de référence). Le
# lancement le plus probable de ce script est donc précisément celui qui échouait.
#
# On la retire, et elle SEULE : les autres variables de l'environnement hôte (`VSCODE_*`) ne sont
# lues que par VS Code lui-même, et nettoyer ce qu'on n'a pas compris est une autre panne.
unset ELECTRON_RUN_AS_NODE

cd "$COQUE"
exec ./node_modules/.bin/electron . "$@"
