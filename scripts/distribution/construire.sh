#!/usr/bin/env bash
# Construit le PAQUET D'INSTALLATION de Maestro pour Windows (#641, lot de #637).
#
#   bash scripts/distribution/construire.sh            # front, produit, paquet
#   bash scripts/distribution/construire.sh --produit  # s'arrête au produit assemblé
#   bash scripts/distribution/construire.sh --version <x.y.z> [--flux <dossier>]
#
# `--version` construit le paquet sous une autre version que celle de apps/desktop/package.json —
# le produit (`version.json`) et la coque ensemble : deux paquets du même code, pour vérifier
# qu'une installation passe de l'un à l'autre (#644, `verifier.sh --mise-a-jour`). Une version qui
# se publie se change dans package.json, par une Pull Request (docs/54 §6.3).
# `--flux <dossier>` y recopie ce que le canal des mises à jour sert pour ce paquet : `latest.yml`
# et l'installeur qu'il nomme.
#
# Trois étapes, dans cet ordre, chacune refusant de continuer sur l'échec de la précédente :
#
#   1. le FRONT en sortie autonome (`MAESTRO_FRONT_AUTONOME=1`, apps/web/next.config.ts) — un
#      `server.js` qu'un Node seul sert, sans `node_modules` à installer ;
#   2. le PRODUIT (`scripts/distribution/assembler.py`) — le code suivi, le CPython embarquable
#      officiel et ses dépendances, le Node du dépôt, le front, les réglages de la distribution ;
#   3. le PAQUET (electron-builder, réglé dans apps/desktop/package.json) — la coque, avec le
#      produit sous `resources/produit/`, en installeur NSIS par utilisateur (aucun droit
#      d'administrateur demandé).
#
# Tout ce qu'il produit va sous `.maestro/distribution/` (gitignoré) : `produit/`, `paquet/` (dont
# l'installeur `Maestro-<version>-installation-x64.exe` et sa forme dépliée `win-unpacked/`), et
# `cache/` — l'archive Python, et les téléchargements d'electron-builder (Electron, NSIS), qu'on
# garde ici plutôt que dans le profil du poste.
#
# Windows seulement : c'est la première plateforme (le ticket en choisit une), et la seule dont
# l'assembleur sait poser le runtime. Le dire plutôt que produire un paquet faux ailleurs.
#
# Le jeton de l'API n'est JAMAIS inscrit dans le front : `NEXT_PUBLIC_MAESTRO_API_JETON` et
# `NEXT_PUBLIC_MAESTRO_API_URL` sont retirés de l'environnement du build, puisque l'un et l'autre
# sont propres au poste qui sert le produit et lui arrivent à son démarrage
# (apps/web/lib/configPoste.ts). L'assembleur le vérifie octet par octet.
#
# Codes : 0 construit · 1 échec d'une étape · 2 usage · 3 plateforme non prise en charge.
set -euo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SORTIE="$RACINE/.maestro/distribution"
JUSQU_AU_PRODUIT=0
VERSION=""
FLUX=""

while [ $# -gt 0 ]; do
  case "$1" in
    --produit) JUSQU_AU_PRODUIT=1; shift ;;
    --version | --flux)
      if [ $# -lt 2 ]; then
        echo "[distribution] $1 attend une valeur" >&2
        exit 2
      fi
      if [ "$1" = --version ]; then VERSION="$2"; else FLUX="$2"; fi
      shift 2
      ;;
    -h | --help)
      sed -n '2,15p' "$0"
      exit 0
      ;;
    *)
      echo "[distribution] argument inconnu : $1" >&2
      exit 2
      ;;
  esac
done

# Une version que le canal compare : trois nombres, comme celle de package.json.
if [ -n "$VERSION" ] && ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "[distribution] --version attend trois nombres (x.y.z) : « $VERSION »" >&2
  exit 2
fi
if [ -n "$FLUX" ] && [ "$JUSQU_AU_PRODUIT" = 1 ]; then
  echo "[distribution] --flux recopie l'installeur : il ne va pas avec --produit" >&2
  exit 2
fi

case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) ;;
  *)
    echo "[distribution] le paquet ne se construit que sous Windows pour l'instant (#641)." >&2
    exit 3
    ;;
esac

# --- le Node du dépôt, celui que le paquet embarquera ---------------------------------------------
# Pas de repli sur le Node du poste : c'est CE fichier qui part chez l'utilisateur, et sa version est
# celle que `.node-version` épingle (la même résolution que scripts/controltower/desktop.sh).
NODE_PIN="$(tr -d ' \r\n' <"$RACINE/.node-version")"
NODE_PIN="${NODE_PIN#v}"
NODE_DIR="$RACINE/.tools/node/v$NODE_PIN"
if [ ! -x "$NODE_DIR/node.exe" ]; then
  echo "[distribution] Node v$NODE_PIN absent de .tools/node/ — bash scripts/setup.sh --only node" >&2
  exit 1
fi
export PATH="$NODE_DIR:$PATH"

PYTHON="$RACINE/.venv/Scripts/python.exe"
if [ ! -x "$PYTHON" ]; then
  echo "[distribution] venv du dépôt absent — bash scripts/setup.sh --only venv" >&2
  exit 1
fi

mkdir -p "$SORTIE/cache"

# --- 1. le front, en sortie autonome --------------------------------------------------------------
echo "[front] build autonome (apps/web)"
(
  cd "$RACINE/apps/web"
  env -u NEXT_PUBLIC_MAESTRO_API_JETON -u NEXT_PUBLIC_MAESTRO_API_URL \
    MAESTRO_FRONT_AUTONOME=1 NEXT_TELEMETRY_DISABLED=1 npm run build
) || {
  echo "[front] build en échec" >&2
  exit 1
}

# --- 2. le produit ---------------------------------------------------------------------------------
echo "[produit] assemblage sous .maestro/distribution/produit"
"$PYTHON" "$RACINE/scripts/distribution/assembler.py" \
  --sortie "$SORTIE/produit" \
  --front "$RACINE/apps/web/.next/standalone" \
  --node "$NODE_DIR/node.exe" \
  --cache "$SORTIE/cache" \
  --rapport "$SORTIE/poids-produit.json" \
  ${VERSION:+--version "$VERSION"} || exit 1

if [ "$JUSQU_AU_PRODUIT" = 1 ]; then
  echo "[distribution] produit assemblé : .maestro/distribution/produit"
  exit 0
fi

# --- 3. le paquet ----------------------------------------------------------------------------------
COQUE="$RACINE/apps/desktop"
if [ ! -x "$COQUE/node_modules/.bin/electron-builder" ]; then
  (cd "$COQUE" && npm ci --no-audit --no-fund) || exit 1
fi
rm -rf "$SORTIE/paquet"
echo "[paquet] electron-builder (NSIS, x64)"
(
  cd "$COQUE"
  # Les téléchargements d'electron-builder restent sous le dépôt, pas dans le profil du poste.
  # ELECTRON_RUN_AS_NODE ferait jouer Electron en Node pur (le piège noté dans desktop.sh).
  env -u ELECTRON_RUN_AS_NODE \
    ELECTRON_CACHE="$SORTIE/cache/electron" \
    ELECTRON_BUILDER_CACHE="$SORTIE/cache/electron-builder" \
    CSC_IDENTITY_AUTO_DISCOVERY=false \
    ./node_modules/.bin/electron-builder --win nsis --x64 --publish never \
    ${VERSION:+"-c.extraMetadata.version=$VERSION"}
) || {
  echo "[paquet] electron-builder en échec" >&2
  exit 1
}

# --- le poids réel, mesuré sur ce qui est produit --------------------------------------------------
INSTALLEUR="$(find "$SORTIE/paquet" -maxdepth 1 -name 'Maestro-*-installation-x64.exe' | head -n 1)"
if [ -z "$INSTALLEUR" ]; then
  echo "[paquet] aucun installeur sous .maestro/distribution/paquet" >&2
  exit 1
fi
octets_installeur="$(wc -c <"$INSTALLEUR" | tr -d ' ')"
octets_deplie="$(du -sb "$SORTIE/paquet/win-unpacked" | cut -f1)"
echo "[poids] installeur : $((octets_installeur / 1000000)) Mo ($octets_installeur octets) — ${INSTALLEUR#"$RACINE"/}"
echo "[poids] installé   : $((octets_deplie / 1000000)) Mo ($octets_deplie octets) — win-unpacked"

# --- ce que le canal des mises à jour sert (#644) ------------------------------------------------
# `latest.yml` nomme l'installeur et son empreinte : c'est ce que la coque installée lit d'abord.
# Un paquet sans lui ne se publierait pas (`publier.sh` le refuse) : le dire ici, où il naît.
if [ ! -f "$SORTIE/paquet/latest.yml" ]; then
  echo "[paquet] latest.yml absent — le canal des mises à jour n'aurait rien à servir" >&2
  exit 1
fi
if [ -n "$FLUX" ]; then
  mkdir -p "$FLUX"
  cp "$SORTIE/paquet/latest.yml" "$INSTALLEUR" "$FLUX/"
  echo "[flux] latest.yml et $(basename "$INSTALLEUR") recopiés sous $FLUX"
fi
