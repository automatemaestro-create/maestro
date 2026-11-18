#!/usr/bin/env bash
# Prépare la preuve d'une installation réelle dans le Bac à sable Windows (#645, docs/54 §3.5).
#
#   bash scripts/distribution/bac-a-sable.sh [--installeur <chemin>]
#
# Le Bac à sable est une machine Windows vierge et jetable : ni Python, ni Node, ni Git, ni compte
# du modèle. C'est ce que la vérification du poste (verifier.sh) ne fait que simuler en masquant
# les outils du poste. Ce script pose sous .maestro/distribution/bac-a-sable/ :
#
#   - l'installeur à éprouver (par défaut, celui de .maestro/distribution/paquet/) ;
#   - preuve.ps1, que le Bac à sable joue à son ouverture : il relève le poste vierge, installe
#     en silence, ouvre Maestro, puis note toutes les 10 s la version servie, les runs et les
#     exécutables qui servent ;
#   - maestro.wsb, qui partage ce dossier avec le Bac à sable (C:\preuve) et y lance preuve.ps1.
#
# Il ne lance rien. Ouvrir maestro.wsb (double-clic) et y faire les gestes de A-VOUS.txt revient
# à la personne, comme activer le Bac à sable (commande d'administrateur, puis redémarrage).
# Le relevé revient sous .maestro/distribution/bac-a-sable/releve-<date>-<heure>/.
#
# Codes : 0 prêt · 2 usage · 4 refus (aucun installeur, ou plus d'un à départager).
set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PAQUET="$RACINE/.maestro/distribution/paquet"
DOSSIER="$RACINE/.maestro/distribution/bac-a-sable"
INSTALLEUR=""

while [ $# -gt 0 ]; do
  case "$1" in
    --installeur)
      if [ $# -lt 2 ]; then
        echo "[bac-à-sable] --installeur attend un chemin" >&2
        exit 2
      fi
      INSTALLEUR="$2"
      shift 2
      ;;
    -h | --help) sed -n '2,4p' "$0"; exit 0 ;;
    *) echo "[bac-à-sable] argument inconnu : $1" >&2; exit 2 ;;
  esac
done

refus() {
  echo "[bac-à-sable] refusé : $1" >&2
  exit 4
}

if [ -z "$INSTALLEUR" ]; then
  trouves="$(find "$PAQUET" -maxdepth 1 -name 'Maestro-*-installation-x64.exe' 2>/dev/null)"
  [ -n "$trouves" ] ||
    refus "aucun installeur sous ${PAQUET#"$RACINE"/} — bash scripts/distribution/construire.sh"
  [ "$(printf '%s\n' "$trouves" | wc -l)" -eq 1 ] ||
    refus "plusieurs installeurs sous ${PAQUET#"$RACINE"/} — nommez-le par --installeur"
  INSTALLEUR="$trouves"
fi
[ -f "$INSTALLEUR" ] || refus "l'installeur « $INSTALLEUR » n'existe pas"
case "$(basename "$INSTALLEUR")" in
  Maestro-*-installation-x64.exe) ;;
  *) refus "preuve.ps1 cherche un Maestro-<version>-installation-x64.exe, pas « $(basename "$INSTALLEUR") »" ;;
esac

mkdir -p "$DOSSIER" || exit 1
# Un seul installeur dans le dossier partagé : preuve.ps1 prend le premier qu'il trouve.
find "$DOSSIER" -maxdepth 1 -name 'Maestro-*-installation-x64.exe' ! -name "$(basename "$INSTALLEUR")" \
  -exec rm -f {} +
cp "$INSTALLEUR" "$DOSSIER/" || exit 1
cp "$RACINE/scripts/distribution/bac-a-sable/preuve.ps1" "$DOSSIER/" || exit 1

# Le Bac à sable lit un chemin Windows. Sous Git Bash, `pwd -W` le rend en barres obliques.
hote="$(cd "$DOSSIER" && { pwd -W 2>/dev/null || pwd; })"
hote="${hote//\//\\}"
hote="${hote//&/&amp;}"
cat >"$DOSSIER/maestro.wsb" <<EOF
<Configuration>
  <!-- Une machine Windows vierge et jetable. Ce dossier y est C:\\preuve : l'installeur et
       preuve.ps1 y entrent, le releve en revient. Ecrit par scripts/distribution/bac-a-sable.sh
       (ASCII, comme preuve.ps1). -->
  <MappedFolders>
    <MappedFolder>
      <HostFolder>$hote</HostFolder>
      <SandboxFolder>C:\\preuve</SandboxFolder>
      <ReadOnly>false</ReadOnly>
    </MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\\preuve\\preuve.ps1</Command>
  </LogonCommand>
  <Networking>Enable</Networking>
  <MemoryInMB>8192</MemoryInMB>
  <ClipboardRedirection>Enable</ClipboardRedirection>
</Configuration>
EOF

echo "[bac-à-sable] prêt : ${DOSSIER#"$RACINE"/}/maestro.wsb — $(basename "$INSTALLEUR")"
if command -v WindowsSandbox.exe >/dev/null 2>&1; then
  echo "[bac-à-sable] ouvrez maestro.wsb (double-clic), puis suivez A-VOUS.txt dans le Bac à sable"
else
  echo "[bac-à-sable] le Bac à sable n'est pas activé sur ce poste. Dans un PowerShell administrateur :"
  echo "  Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM -All"
  echo "  puis redémarrer le poste, et ouvrir maestro.wsb"
fi
