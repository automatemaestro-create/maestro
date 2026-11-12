#!/usr/bin/env bash
# Vérifie le PAQUET D'INSTALLATION sur un poste sans Python, sans Node et sans clone (#641).
#
#   bash scripts/distribution/verifier.sh [--poste <dossier>] [--installeur <exe>] [--ancien <exe>]
#                                         [--tenir-ouvert <secondes>] [--mise-a-jour <dossier>]
#
# Le poste n'a pas de machine vierge à offrir : ce script en fabrique les conditions, et chacune
# est un fait qu'on peut contester, pas une supposition.
#
#   - un PROFIL NEUF : USERPROFILE, APPDATA, LOCALAPPDATA, TEMP pointent sous `<poste>/profil/` —
#     ni `~/.maestro/`, ni jeton, ni réglage de ce poste ne sont visibles ;
#   - un PATH SYSTÈME SEUL (System32, Windows, Wbem, PowerShell) : ni Python, ni Node, ni Git ;
#   - des variables EMPOISONNÉES : PYTHONHOME, PYTHONPATH, NODE_PATH désignent un dossier qui
#     n'existe pas — un interpréteur qui les lirait ne démarrerait pas ;
#   - une installation HORS DU CLONE (`--poste`, par défaut sous `.maestro/session/`, à placer
#     ailleurs pour la vérification qui compte), dans un chemin qui porte une ESPACE ;
#   - des CHEMINS DE LONGUEUR RÉELLE : le poste est vu par une lettre de lecteur (`subst`, défait
#     à la sortie), si bien que profil, TEMP et dossier d'installation ont la longueur qu'ils ont
#     chez quelqu'un (`X:\profil\AppData\Local\Temp`, comme `C:\Users\<nom>\AppData\Local\Temp`).
#     Sans elle, le dossier de travail de la vérification allonge chaque chemin d'une centaine de
#     caractères, et l'installeur, qui ne passe pas la limite de 260 de Windows, perd des fichiers
#     qu'il n'aurait pas perdus ailleurs (constaté le 2026-11-04, `scripts/distribution/assembler.py`,
#     `LONGUEUR_MAX`).
#
# Puis il joue ce que ferait quelqu'un : installer (en silence, `/S`), ouvrir Maestro, attendre que
# l'API et l'interface répondent, fermer la fenêtre, désinstaller. Entre les deux, il relève QUI
# sert : chaque processus né de l'installation avec le chemin de son exécutable — un seul pris au
# poste ou au clone, et la vérification échoue.
#
# Et ce que la personne y a mis SURVIT (#1620) : on déclare un projet et on pose un réglage par
# l'API, on constate qu'ils vivent sous l'état du poste et que le dossier d'installation ne porte
# que le paquet ; puis on réinstalle par-dessus — l'effacement du dossier par l'installeur se
# constate sur un témoin qu'on y pose —, on désinstalle, on réinstalle, et chaque fois on les
# retrouve. `--ancien <exe>` installe d'abord une version antérieure, qui écrit sous son dossier :
# la version vérifiée s'installe par-dessus, et ce que l'ancienne avait écrit doit être repris.
# `--tenir-ouvert <s>` laisse Maestro ouvert, après la dernière réinstallation, le temps de le
# regarder de ses yeux (`http://127.0.0.1:<port-ui>/`) ; poser `<sortie>/fermer` abrège l'attente.
#
# Et elle SE MET À JOUR (#644) : `--mise-a-jour <dossier>` désigne un canal — `latest.yml` et
# l'installeur d'une version PLUS RÉCENTE (`construire.sh --version <x.y.z> --flux <dossier>`). Le
# script le sert sur le poste, ouvre la version vérifiée sur lui, lance un run pour que le journal
# ait de quoi être retrouvé, et attend qu'elle annonce la suivante prête. Il presse « Plus tard »
# dans son dialogue, d'un clic de souris, ferme Maestro, et attend que la version suivante se pose
# après l'arrêt, sans rien relancer. Il rouvre alors Maestro et regarde la version de chaque
# composant, et ce que la personne avait : projet, réglage, journal et run. « Redémarrer
# maintenant » n'y est pas joué : l'installeur relance par l'Explorateur, hors du poste simulé
# (docs/54 §6.4). Ailleurs, la mise à jour est ÉTEINTE (`MAESTRO_MISES_A_JOUR_FLUX=0`) : les
# autres étapes ne doivent rien à un canal.
#
# Le CLI `claude` : le SDK le trouve dans son paquet (`claude_agent_sdk/_bundled/`), et le produit
# aussi (`maestro/providers/claude.py`, `_commande_du_cli`). `sonde_cli.py` le fait lancer par le
# SDK, avec l'interpréteur de l'installation, jusqu'à la poignée de main du protocole — sans rien
# demander au modèle, et sur un profil qui n'a aucun compte pour le faire.
#
# Sortie : `.maestro/distribution/verification/` — `rapport.txt` (une ligne par constat, ✓ ou ✗),
# `processus.tsv`, et les journaux de la coque et du lanceur.
#
# Codes : 0 tout constat ✓ · 1 un constat ✗ · 2 usage · 3 plateforme non prise en charge.
set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Le canal local (`canal.sh`) s'arrête par la carte et l'arrêt de `pilote.sh` (#1644).
# shellcheck disable=SC1091  # le lint appelle shellcheck fichier par fichier (#285) : la source
# n'est pas sur sa ligne de commande, donc `source=` ne serait pas suivi de toute façon.
. "$RACINE/scripts/orchestrate/pilote.sh"
# shellcheck disable=SC1091  # même raison que ci-dessus
. "$RACINE/scripts/distribution/canal.sh"
POSTE="$RACINE/.maestro/session/poste vierge"
INSTALLEUR=""
ANCIEN=""
TENIR_OUVERT=0
PORT_API=8041
PORT_UI=3041
FLUX_DOSSIER=""
PORT_FLUX=8042
# Le canal que la coque interroge : éteint (`0`) hors de la phase de mise à jour.
FLUX_COQUE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --poste) POSTE="$2/poste vierge"; shift 2 ;;
    --installeur) INSTALLEUR="$2"; shift 2 ;;
    --ancien) ANCIEN="$2"; shift 2 ;;
    --tenir-ouvert) TENIR_OUVERT="$2"; shift 2 ;;
    --mise-a-jour) FLUX_DOSSIER="$2"; shift 2 ;;
    --port-api) PORT_API="$2"; shift 2 ;;
    --port-ui) PORT_UI="$2"; shift 2 ;;
    --port-flux) PORT_FLUX="$2"; shift 2 ;;
    -h | --help) sed -n '2,4p' "$0"; exit 0 ;;
    *) echo "[verification] argument inconnu : $1" >&2; exit 2 ;;
  esac
done

case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) ;;
  *) echo "[verification] Windows seulement (#641)." >&2; exit 3 ;;
esac

PAQUET="$RACINE/.maestro/distribution/paquet"
if [ -z "$INSTALLEUR" ]; then
  INSTALLEUR="$(find "$PAQUET" -maxdepth 1 -name 'Maestro-*-installation-x64.exe' 2>/dev/null | head -n 1)"
fi
if [ -z "$INSTALLEUR" ] || [ ! -f "$INSTALLEUR" ]; then
  echo "[verification] aucun installeur — bash scripts/distribution/construire.sh" >&2
  exit 2
fi
if [ -n "$ANCIEN" ] && [ ! -f "$ANCIEN" ]; then
  echo "[verification] installeur de la version antérieure introuvable : $ANCIEN" >&2
  exit 2
fi
# Le canal de `--mise-a-jour` : ce qu'il annonce, et l'installeur qu'il nomme, doivent y être.
VERSION_SUIVANTE=""
if [ -n "$FLUX_DOSSIER" ]; then
  if [ ! -f "$FLUX_DOSSIER/latest.yml" ]; then
    echo "[verification] aucun latest.yml sous $FLUX_DOSSIER — construire.sh --version <x.y.z> --flux <dossier>" >&2
    exit 2
  fi
  VERSION_SUIVANTE="$(sed -n 's/^version: *//p' "$FLUX_DOSSIER/latest.yml" | tr -d '\r' | head -n 1)"
  nom_suivant="$(sed -n 's/^path: *//p' "$FLUX_DOSSIER/latest.yml" | tr -d '\r' | head -n 1)"
  if [ -z "$VERSION_SUIVANTE" ] || [ ! -f "$FLUX_DOSSIER/$nom_suivant" ]; then
    echo "[verification] le canal $FLUX_DOSSIER n'annonce pas une version et son installeur" >&2
    exit 2
  fi
fi

TRACE="$RACINE/.maestro/distribution/verification"
rm -rf "$TRACE" "$POSTE"
mkdir -p "$TRACE" "$POSTE/profil/AppData/Roaming" "$POSTE/profil/AppData/Local/Temp"
RAPPORT="$TRACE/rapport.txt"
: >"$RAPPORT"
ECHECS=0

constat() { # constat <0|1> <texte>
  if [ "$1" = 0 ]; then
    printf '✓ %s\n' "$2" | tee -a "$RAPPORT"
  else
    printf '✗ %s\n' "$2" | tee -a "$RAPPORT"
    ECHECS=$((ECHECS + 1))
  fi
}

# `contient <texte> <aiguille>`, casse ignorée. Pas `grep -iF` : celui de Git Bash s'interrompt
# (SIGABRT) sur une aiguille qui porte des barres obliques inverses, et rend un faux ✗.
contient() {
  local texte="${1,,}" aiguille="${2,,}"
  [ -n "$aiguille" ] && [[ "$texte" == *"$aiguille"* ]]
}

# --- le poste, vu par une lettre de lecteur -----------------------------------------------------
LECTEUR=""
for lettre in P Q R S T U V W X Y; do
  if [ ! -d "/${lettre,,}/" ]; then
    LECTEUR="$lettre:"
    break
  fi
done
if [ -z "$LECTEUR" ]; then
  echo "[verification] aucune lettre de lecteur libre (P à Y) pour le poste vierge" >&2
  exit 2
fi
if ! env MSYS2_ARG_CONV_EXCL='*' subst "$LECTEUR" "$(cygpath -w "$POSTE")"; then
  echo "[verification] subst $LECTEUR refusé" >&2
  exit 2
fi
# À la sortie, même interrompue : la lettre de lecteur défaite, et le canal local arrêté s'il est
# encore en vol.
defaire_le_lecteur() {
  canal_en_vol "$TRACE" && canal_arreter "$PORT_FLUX" "$TRACE" >/dev/null 2>&1
  env MSYS2_ARG_CONV_EXCL='*' subst "$LECTEUR" /D >/dev/null 2>&1 || true
}
trap defaire_le_lecteur EXIT

# Trois écritures du même dossier : `INSTALL` (le vrai chemin, pour les fichiers), `U_INSTALL` et
# `W_INSTALL` (par la lettre de lecteur, pour ce qu'on lance et ce qu'on passe à Windows).
PROFIL="$POSTE/profil"
INSTALL="$POSTE/Mes Programmes/Maestro"
U_INSTALL="/${LECTEUR:0:1}"
U_INSTALL="${U_INSTALL,,}/Mes Programmes/Maestro"
W_PROFIL="$LECTEUR\\profil"
W_INSTALL="$LECTEUR\\Mes Programmes\\Maestro"
# Le même dossier sous son vrai chemin : `Path.resolve()` défait la lettre de lecteur, et ce que le
# produit lance depuis une racine résolue porte ce chemin-là. L'un et l'autre sont l'installation.
W_INSTALL_REEL="$(cygpath -w "$INSTALL")"
W_PROFIL_REEL="$(cygpath -w "$PROFIL")"
W_RACINE="$(cygpath -w "$RACINE")"
PRODUIT="$INSTALL/resources/produit"
PYTHON_EMBARQUE="$U_INSTALL/resources/produit/runtime/python/python.exe"

dans_l_installation() { contient "$1" "$W_INSTALL\\" || contient "$1" "$W_INSTALL_REEL\\"; }

# Le poste vierge : l'environnement que l'installation trouve chez quelqu'un qui n'a rien.
vierge() {
  env -i \
    SystemRoot="${SYSTEMROOT:-C:\\Windows}" windir="${WINDIR:-C:\\Windows}" \
    SystemDrive="${SYSTEMDRIVE:-C:}" ComSpec="${COMSPEC:-C:\\Windows\\System32\\cmd.exe}" \
    PATHEXT="${PATHEXT:-.COM;.EXE;.BAT;.CMD}" OS="${OS:-Windows_NT}" \
    PROCESSOR_ARCHITECTURE="${PROCESSOR_ARCHITECTURE:-AMD64}" \
    NUMBER_OF_PROCESSORS="${NUMBER_OF_PROCESSORS:-4}" \
    USERNAME="${USERNAME:-utilisateur}" COMPUTERNAME="${COMPUTERNAME:-poste}" \
    USERPROFILE="$W_PROFIL" HOMEDRIVE="$LECTEUR" HOMEPATH="\\profil" \
    APPDATA="$W_PROFIL\\AppData\\Roaming" LOCALAPPDATA="$W_PROFIL\\AppData\\Local" \
    TEMP="$W_PROFIL\\AppData\\Local\\Temp" TMP="$W_PROFIL\\AppData\\Local\\Temp" \
    PATH="/c/Windows/System32:/c/Windows:/c/Windows/System32/Wbem:/c/Windows/System32/WindowsPowerShell/v1.0" \
    PYTHONHOME='C:\nulle-part' PYTHONPATH='C:\nulle-part' NODE_PATH='C:\nulle-part' \
    MAESTRO_PORT_API="$PORT_API" MAESTRO_PORT_UI="$PORT_UI" \
    MAESTRO_MISES_A_JOUR_FLUX="$FLUX_COQUE" \
    MSYS2_ARG_CONV_EXCL='*' \
    "$@"
}

PS='/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'

# Les processus nés de l'installation : tout ce dont l'exécutable OU la ligne de commande nomme son
# dossier, avec le chemin de l'exécutable. Une ligne de commande qui nomme l'installation mais un
# exécutable pris ailleurs (un Python du poste qui jouerait le code installé) est la fuite qu'on
# cherche.
# Le relevé ne se compte pas lui-même : sa propre ligne de commande nomme l'installation, et celle
# de `env.exe` qui le lance aussi — d'où l'exclusion de ce qui porte `Get-CimInstance`.
releve_processus() {
  env MSYS2_ARG_CONV_EXCL='*' "$PS" -NoProfile -NonInteractive -Command "
    \$dossiers = @('$W_INSTALL', '$W_INSTALL_REEL')
    Get-CimInstance Win32_Process | Where-Object {
      \$p = \$_
      -not (\$p.CommandLine -and \$p.CommandLine.Contains('Get-CimInstance')) -and
      (\$dossiers | Where-Object {
        (\$p.ExecutablePath -and \$p.ExecutablePath.StartsWith(\$_, 'OrdinalIgnoreCase')) -or
        (\$p.CommandLine -and \$p.CommandLine.IndexOf(\$_, [StringComparison]::OrdinalIgnoreCase) -ge 0)
      })
    } | ForEach-Object { \"{0}\`t{1}\`t{2}\`t{3}\" -f \$_.ProcessId, \$_.ParentProcessId, \$_.Name, \$_.ExecutablePath }
  " | tr -d '\r'
}

sonde() { # sonde <url> [jeton] → code HTTP
  if [ -n "${2:-}" ]; then
    curl -s -o /dev/null -w '%{http_code}' --max-time 5 -H "Authorization: Bearer $2" "$1"
  else
    curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1"
  fi
}

JETON_FICHIER="$PROFIL/.maestro/jeton-api"
# Ce que la personne écrit, sous l'état du poste d'une installation (`maestro.emplacements`).
CONFIGURATION="$PROFIL/.maestro/configuration"

# `installer <exe> <nom>` : l'installation silencieuse, dans le dossier du poste vierge. L'installeur
# rend la main une fois tout posé, ancienne version désinstallée comprise.
installer() {
  vierge "$1" /S "/D=$W_INSTALL" >"$TRACE/installation-$2.log" 2>&1
}

# `ouvrir <nom>` : ouvre Maestro et attend que l'API et l'interface répondent (240 s au plus). Pose
# PID_COQUE, jeton, api et ui ; rend 0 quand les deux répondent.
ouvrir() {
  vierge "$U_INSTALL/Maestro.exe" >"$TRACE/coque-$1.log" 2>&1 &
  PID_COQUE=$!
  api=000
  ui=000
  jeton=""
  for _ in $(seq 1 120); do
    [ -z "$jeton" ] && [ -f "$JETON_FICHIER" ] && jeton="$(tr -d '\r\n' <"$JETON_FICHIER")"
    if [ -n "$jeton" ]; then
      api="$(sonde "http://127.0.0.1:$PORT_API/api/sante" "$jeton")"
    fi
    ui="$(sonde "http://127.0.0.1:$PORT_UI/")"
    [ "$api" = 200 ] && [ "$ui" = 200 ] && return 0
    kill -0 "$PID_COQUE" 2>/dev/null || return 1
    sleep 2
  done
  return 1
}

# La fenêtre est le Maestro.exe dont le parent n'en est pas un : les autres (rendu, GPU) sont ses
# enfants. `$!` ne la désigne pas — c'est le relais MSYS de `env -i`, sans fenêtre à fermer.
fenetre() { # fenetre <processus.tsv>
  awk -F'\t' '$3 == "Maestro.exe" { parent[$1] = $2 }
    END { for (p in parent) if (!(parent[p] in parent)) print p }' "$1" | head -n 1
}

# `fermer <nom>` : ferme la fenêtre comme le bouton de sa barre de titre (`taskkill` sans `/F`),
# puis attend que plus rien de l'installation ne tourne. Pose `restants` ; rend 0 quand c'est fait.
fermer() {
  releve_processus >"$TRACE/processus-$1.tsv"
  local pid
  pid="$(fenetre "$TRACE/processus-$1.tsv")"
  [ -n "$pid" ] && env MSYS2_ARG_CONV_EXCL='*' taskkill /PID "$pid" >>"$TRACE/arret.log" 2>&1
  restants=""
  for _ in $(seq 1 60); do
    restants="$(releve_processus)"
    [ -z "$restants" ] && return 0
    sleep 2
  done
  return 1
}

# `desinstaller <nom>` : le désinstalleur se recopie sous TEMP et rend la main aussitôt ; on attend
# qu'il ait fini — le dossier vidé, raccourcis compris —, sans quoi la lettre de lecteur, défaite à
# la sortie, l'interromprait au milieu.
desinstaller() {
  vierge "$U_INSTALL/Uninstall Maestro.exe" /S >"$TRACE/desinstallation-$1.log" 2>&1
  for _ in $(seq 1 120); do
    [ -z "$(find "$INSTALL" -type f 2>/dev/null | head -n 1)" ] &&
      [ -z "$(find "$PROFIL" -iname 'Maestro.lnk' 2>/dev/null | head -n 1)" ] && return 0
    sleep 2
  done
  return 1
}

# `appel <méthode> <chemin> [corps JSON]` : l'API de l'installation, avec le jeton du profil.
appel() {
  local corps=()
  [ -n "${3:-}" ] && corps=(-H 'Content-Type: application/json' --data "$3")
  curl -s --max-time 30 -X "$1" -H "Authorization: Bearer $jeton" "${corps[@]}" \
    "http://127.0.0.1:$PORT_API$2"
}

# Ce que la personne déclare et règle, puis cherche après chaque réinstallation (#1620) : un
# projet sous le répertoire des projets, et un répertoire des projets réglé ailleurs que le défaut.
# En barres obliques : le chemin traverse le JSON sans échappement, et Windows le lit tel quel.
NOM_PROJET="Verification 1620"
RACINE_PROJET="${LECTEUR}/profil/Maestro/verification-1620"
REPERTOIRE_REGLE="${LECTEUR}/profil/Projets-1620"

declarer() { # declarer <nom> : déclare le projet et pose le réglage ; rend 0 si l'API les prend
  local projet reglage
  projet="$(appel POST /api/projets \
    "{\"nom\": \"$NOM_PROJET\", \"racine\": \"$RACINE_PROJET\", \"origine\": \"nouveau\"}")"
  reglage="$(appel PUT /api/projets/repertoire "{\"chemin\": \"$REPERTOIRE_REGLE\"}")"
  printf '%s\n%s\n' "$projet" "$reglage" >"$TRACE/declaration-$1.json"
  contient "$projet" "\"$NOM_PROJET\"" && contient "$reglage" "projets-1620"
}

retrouver() { # retrouver <nom> : le projet est-il listé, et le réglage relu ?
  local projets repertoire
  projets="$(appel GET /api/projets)"
  repertoire="$(appel GET /api/projets/repertoire)"
  printf '%s\n%s\n' "$projets" "$repertoire" >"$TRACE/retrouve-$1.json"
  # Le JSON de l'API est compact (`"par_defaut":false`) ; on ôte les espaces pour ne pas en dépendre.
  repertoire="${repertoire// /}"
  contient "$projets" "\"$NOM_PROJET\"" && contient "$repertoire" "projets-1620" &&
    contient "$repertoire" '"par_defaut":false'
}

# `sous_la_configuration` : les fichiers que la personne a écrits sous l'état du poste, en clair.
sous_la_configuration() {
  find "$CONFIGURATION/projets" "$CONFIGURATION/reglages" -type f -name '*.json' 2>/dev/null |
    sed "s|^$PROFIL/||" | sort | tr '\n' ' '
}

# Ce que le lanceur a dit de la reprise, dans ses journaux (ligne `[reprise]`).
dit_de_la_reprise() {
  cat "$PROFIL/.maestro/lanceur/"*.log "$TRACE/coque-$1.log" 2>/dev/null | tr -d '\r' |
    grep -F '[reprise]' | head -n 5
}

# Les fichiers sous le dossier d'installation que le paquet n'a pas posés. Le cache de compilation
# de Python (`__pycache__/*.pyc`) est compté à part : l'interpréteur le rebâtit à la demande, et le
# perdre à une réinstallation ne perd rien.
etrangers() {
  local attendus poses
  attendus="$(cd "$PAQUET/win-unpacked" && find . -type f | sort)"
  poses="$(cd "$INSTALL" && find . -type f ! -name 'Uninstall Maestro.exe' | sort)"
  comm -13 <(printf '%s\n' "$attendus") <(printf '%s\n' "$poses")
}

# --- la mise à jour (#644) -------------------------------------------------------------------------
# La version que le produit installé dit de lui-même (`version.json`, écrit à l'assemblage).
version_du_produit() {
  sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$PRODUIT/version.json" 2>/dev/null | tr -d '\r' | head -n 1
}

# La version de la coque, celle que l'empaquetage a inscrite dans `Maestro.exe`.
version_de_la_coque() {
  env MSYS2_ARG_CONV_EXCL='*' "$PS" -NoProfile -NonInteractive -Command \
    "(Get-Item -LiteralPath '$W_INSTALL\\Maestro.exe').VersionInfo.ProductVersion" 2>/dev/null | tr -d '\r'
}

# `lancer_un_run` : un run dans le projet déclaré, par l'API, pour que le journal porte quelque
# chose de la personne. Le profil vierge n'a aucun compte : le run échoue au premier appel du
# modèle, et c'est ce qu'il consigne qui doit traverser la mise à jour. Rend son identifiant.
lancer_un_run() {
  local id reponse
  appel GET /api/projets >"$TRACE/projets-mise-a-jour.json"
  id="$(vierge "$PYTHON_EMBARQUE" -c 'import json, sys
projets = json.load(open(sys.argv[1], encoding="utf-8"))
print(next((p["id"] for p in projets if p.get("nom") == sys.argv[2]), ""))' \
    "$(cygpath -w "$TRACE/projets-mise-a-jour.json")" "$NOM_PROJET" 2>>"$TRACE/run-mise-a-jour.err" | tr -d '\r')"
  reponse="$(appel POST /api/executions \
    "{\"objectif\": \"Ecrire un LISEZMOI.md qui presente le projet.\", \"projet_id\": \"$id\"}")"
  printf '%s\n' "$reponse" >"$TRACE/run-mise-a-jour.json"
  sed -n 's/.*"run_id": *"\([^"]*\)".*/\1/p' <<<"$reponse" | head -n 1
}

# Ce que porte le journal local, lu par l'interpréteur de l'installation (`sonde_journal.py`).
journal() { # journal [<rang>]
  vierge "$PYTHON_EMBARQUE" "$(cygpath -w "$RACINE/scripts/distribution/sonde_journal.py")" "$@" \
    </dev/null 2>&1 | tr -d '\r'
}

# `attendre_la_mise_a_jour <journal de la coque>` : la coque annonce la version suivante prête
# (`[mise à jour] prête : <version>`, apps/desktop/main.js). Rend 0 quand c'est dit.
attendre_la_mise_a_jour() {
  for _ in $(seq 1 150); do
    tr -d '\r' <"$1" 2>/dev/null | grep -qF "[mise à jour] prête : $VERSION_SUIVANTE" && return 0
    kill -0 "$PID_COQUE" 2>/dev/null || return 1
    sleep 2
  done
  return 1
}

# `presser <pid de la fenêtre> <début du nom du bouton>` : le dialogue de la coque, lu puis pressé
# d'un clic de souris, comme la personne le ferait. C'est un dialogue de tâches Windows (classe
# `#32770`, sous la fenêtre) : ses boutons (`CCPushButton`) n'exposent aucun motif UI Automation, si
# bien qu'on ne peut pas les « invoquer », seulement cliquer dessus. Le dialogue passe devant, le
# curseur va au centre du bouton, clique, puis revient où il était. Écrit les textes du dialogue
# (`texte : …`), puis `presse : <bouton>` quand le dialogue s'est fermé sous le clic. Le script est
# en ASCII, et ses guillemets doubles sont des `[char]34` : il traverse la ligne de commande.
presser() {
  env MSYS2_ARG_CONV_EXCL='*' "$PS" -NoProfile -NonInteractive -Command "
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms
    \$dll = '[DllImport(' + [char]34 + 'user32.dll' + [char]34 + ')] public static extern '
    Add-Type -Namespace Verification -Name Souris -MemberDefinition (
      \$dll + 'bool SetProcessDPIAware(); ' +
      \$dll + 'bool SetForegroundWindow(IntPtr h); ' +
      \$dll + 'bool SetCursorPos(int x, int y); ' +
      \$dll + 'void mouse_event(uint f, uint x, uint y, uint d, UIntPtr e);')
    [void][Verification.Souris]::SetProcessDPIAware()
    \$A = [System.Windows.Automation.AutomationElement]
    \$tous = [System.Windows.Automation.Condition]::TrueCondition
    \$duProcessus = New-Object System.Windows.Automation.PropertyCondition(\$A::ProcessIdProperty, $1)
    \$dialogue = New-Object System.Windows.Automation.PropertyCondition(\$A::ClassNameProperty, '#32770')
    for (\$i = 0; \$i -lt 120; \$i++) {
      foreach (\$haut in \$A::RootElement.FindAll('Children', \$duProcessus)) {
        foreach (\$d in @(\$haut) + @(\$haut.FindAll('Descendants', \$dialogue))) {
          if (\$d.Current.ClassName -ne '#32770') { continue }
          \$elements = @(\$d.FindAll('Descendants', \$tous))
          \$bouton = \$elements | Where-Object {
            \$_.Current.ClassName -eq 'CCPushButton' -and \$_.Current.Name -like '$2'
          } | Select-Object -First 1
          if (-not \$bouton) { continue }
          \$elements | ForEach-Object { \$_.Current.Name } | Where-Object { \$_ } | ForEach-Object { 'texte : ' + \$_ }
          \$nom = \$bouton.Current.Name
          \$r = \$bouton.Current.BoundingRectangle
          \$avant = [System.Windows.Forms.Cursor]::Position
          [void][Verification.Souris]::SetForegroundWindow([IntPtr]\$d.Current.NativeWindowHandle)
          Start-Sleep -Milliseconds 300
          [void][Verification.Souris]::SetCursorPos([int](\$r.X + \$r.Width / 2), [int](\$r.Y + \$r.Height / 2))
          [Verification.Souris]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero)
          [Verification.Souris]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero)
          Start-Sleep -Milliseconds 200
          [System.Windows.Forms.Cursor]::Position = \$avant
          for (\$j = 0; \$j -lt 20; \$j++) {
            Start-Sleep -Milliseconds 250
            if (@(\$haut.FindAll('Descendants', \$dialogue)).Count -eq 0) { 'presse : ' + \$nom; exit 0 }
          }
          'clic sans effet : ' + \$nom
          exit 1
        }
      }
      Start-Sleep -Milliseconds 500
    }
    'aucun dialogue de mise a jour'
    exit 1
  " | tr -d '\r'
}

# `installeur_en_vol` : les processus de l'installeur de la mise à jour — celui que la coque a lancé
# depuis son cache, et l'ancien désinstalleur qu'il joue, recopié sous le TEMP du poste. L'un et
# l'autre jouent un exécutable du profil, sous la lettre de lecteur ou sous son vrai chemin. Rien
# n'est imprimé quand il a fini.
installeur_en_vol() {
  env MSYS2_ARG_CONV_EXCL='*' "$PS" -NoProfile -NonInteractive -Command "
    \$dossiers = @('$W_PROFIL\\AppData\\Local\\maestro-updater', '$W_PROFIL\\AppData\\Local\\Temp',
      '$W_PROFIL_REEL\\AppData\\Local\\maestro-updater', '$W_PROFIL_REEL\\AppData\\Local\\Temp')
    Get-CimInstance Win32_Process | Where-Object {
      \$p = \$_
      \$p.ExecutablePath -and (\$dossiers | Where-Object { \$p.ExecutablePath.StartsWith(\$_, 'OrdinalIgnoreCase') })
    } | ForEach-Object { \$_.Name + ' (' + \$_.ProcessId + ')' }
  " | tr -d '\r' | tr '\n' ' '
}

echo "[verification] installeur : ${INSTALLEUR#"$RACINE"/}"
echo "[verification] poste vierge : $LECTEUR → $(cygpath -w "$POSTE")"

# --- 0. une version antérieure, qui écrit sous son dossier (--ancien) --------------------------
if [ -n "$ANCIEN" ]; then
  echo "[verification] version antérieure : $ANCIEN"
  installer "$ANCIEN" ancienne
  ouvrir ancienne
  constat "$([ "$api" = 200 ] && [ "$ui" = 200 ] && echo 0 || echo 1)" \
    "la version antérieure s'ouvre (API HTTP $api, interface HTTP $ui)"
  constat "$(declarer ancienne && echo 0 || echo 1)" \
    "la version antérieure déclare le projet et pose le réglage"
  ecrits="$(find "$PRODUIT/core/projets" "$PRODUIT/core/reglages" -name '*.json' 2>/dev/null |
    sed "s|^$PRODUIT/||" | tr '\n' ' ')"
  constat "$([ -n "$ecrits" ] && echo 0 || echo 1)" \
    "la version antérieure les écrit sous son dossier d'installation : ${ecrits:-rien}"
  fermer ancienne
  mkdir -p "$TRACE/ancienne"
  cp "$PRODUIT/.maestro/lanceur/"*.log "$TRACE/ancienne/" 2>/dev/null || true
fi

# --- 1. installer ------------------------------------------------------------------------------
installer "$INSTALLEUR" premiere
code_installation=$?
cp "$TRACE/installation-premiere.log" "$TRACE/installation.log" 2>/dev/null || true
constat "$([ "$code_installation" = 0 ] && [ -f "$INSTALL/Maestro.exe" ] && echo 0 || echo 1)" \
  "installation silencieuse dans « $W_INSTALL » (code $code_installation)"
if [ ! -f "$INSTALL/Maestro.exe" ]; then
  echo "[verification] rien d'installé — arrêt" >&2
  exit 1
fi
# L'installeur rend 0 même quand il a perdu des fichiers : on compte.
attendus="$(cd "$PAQUET/win-unpacked" && find . -type f | sort)"
poses="$(cd "$INSTALL" && find . -type f ! -name 'Uninstall Maestro.exe' | sort)"
manquants="$(comm -23 <(printf '%s\n' "$attendus") <(printf '%s\n' "$poses"))"
nombre_manquants="$(printf '%s' "$manquants" | grep -c . || true)"
printf '%s\n' "$manquants" >"$TRACE/fichiers-manquants.txt"
constat "$([ "$nombre_manquants" = 0 ] && echo 0 || echo 1)" \
  "chaque fichier du paquet est installé ($(printf '%s\n' "$attendus" | grep -c .) attendus, $nombre_manquants manquants)"
octets_installes="$(du -sb "$INSTALL" | cut -f1)"
echo "poids installé : $((octets_installes / 1000000)) Mo ($octets_installes octets)" | tee -a "$RAPPORT"
# Le canal que la coque installée interroge : celui que l'empaquetage a écrit (#644).
canal_du_paquet="$(tr -d '\r' <"$INSTALL/resources/app-update.yml" 2>/dev/null)"
constat "$(contient "$canal_du_paquet" 'provider: generic' &&
  contient "$canal_du_paquet" 'github.com/automatemaestro-create/maestro/releases/latest/download' &&
  contient "$canal_du_paquet" 'updaterCacheDirName: maestro-updater' && echo 0 || echo 1)" \
  "le paquet nomme son canal de mises à jour (resources/app-update.yml) : $(printf '%s' "$canal_du_paquet" | tr '\n' ' ')"

# --- 2. le runtime embarqué ignore le poste ------------------------------------------------------
sortie_python="$(vierge "$PYTHON_EMBARQUE" -c 'import sys, maestro; print(sys.prefix); print(maestro.__file__)' 2>&1 | tr -d '\r')"
echo "$sortie_python" >"$TRACE/python.txt"
constat "$(dans_l_installation "$sortie_python" && echo 0 || echo 1)" \
  "le Python embarqué démarre malgré PYTHONHOME/PYTHONPATH empoisonnés, et importe maestro de l'installation"

# --- 3. ouvrir Maestro -------------------------------------------------------------------------
debut=$SECONDS
ouvrir premiere
cp "$TRACE/coque-premiere.log" "$TRACE/coque.log" 2>/dev/null || true
echo "démarrage : $((SECONDS - debut)) s jusqu'à l'API et l'interface" | tee -a "$RAPPORT"
constat "$([ "$api" = 200 ] && echo 0 || echo 1)" "l'API répond sur :$PORT_API (/api/sante, avec le jeton du profil neuf) — HTTP $api"
constat "$([ "$ui" = 200 ] && echo 0 || echo 1)" "l'interface répond sur :$PORT_UI — HTTP $ui"
constat "$(contient "$(tr -d '\r' <"$TRACE/coque-premiere.log")" "[mise à jour] éteinte — MAESTRO_MISES_A_JOUR_FLUX=0" && echo 0 || echo 1)" \
  "la mise à jour se tait quand on l'éteint (MAESTRO_MISES_A_JOUR_FLUX=0) : aucune étape ne doit rien au canal"

if [ "$ui" = 200 ]; then
  accueil="$(curl -s --max-time 30 "http://127.0.0.1:$PORT_UI/")"
  printf '%s' "$accueil" >"$TRACE/accueil.html"
  constat "$(contient "$accueil" "__MAESTRO_POSTE__" && contient "$accueil" "http://127.0.0.1:$PORT_API" && echo 0 || echo 1)" \
    "la page porte la configuration du poste (l'URL de l'API, donnée au démarrage)"
  constat "$(contient "$accueil" "$jeton" && echo 0 || echo 1)" \
    "la page porte le jeton de CE profil, reçu au démarrage et non au build"
fi
if [ "$api" = 200 ]; then
  curl -s --max-time 10 -H "Authorization: Bearer $jeton" "http://127.0.0.1:$PORT_API/api/sante" >"$TRACE/sante.json"
  constat "$(contient "$(cat "$TRACE/sante.json")" '"ok"' && echo 0 || echo 1)" \
    "l'API se dit « ok » : son magasin (mode local, SQLite) répond — $(head -c 300 "$TRACE/sante.json")"
fi
cp "$PROFIL/.maestro/lanceur/"*.log "$TRACE/" 2>/dev/null || true
cp "$PROFIL/.maestro/lanceur/session.json" "$TRACE/" 2>/dev/null || true

# --- 3 bis. ce que la personne y met vit sous l'état du poste (#1620) ---------------------------
if [ -n "$ANCIEN" ]; then
  reprise_dite="$(dit_de_la_reprise premiere)"
  printf 'le lanceur, au premier démarrage :\n%s\n' "${reprise_dite:-(rien dit de la reprise)}" |
    tee -a "$RAPPORT"
  constat "$(retrouver reprise && echo 0 || echo 1)" \
    "ce que la version antérieure avait écrit sous son dossier est repris au premier démarrage : le projet « $NOM_PROJET » et le répertoire des projets réglé"
  constat "$([ -f "$PROFIL/.maestro/reprise/reprise.json" ] && echo 0 || echo 1)" \
    "la reprise laisse son témoin (~/.maestro/reprise/reprise.json) : elle ne se rejouera pas"
else
  constat "$(declarer premiere && echo 0 || echo 1)" \
    "on déclare le projet « $NOM_PROJET » et on règle le répertoire des projets, par l'API"
fi
ranges="$(sous_la_configuration)"
constat "$([ -n "$(find "$CONFIGURATION/projets" -name '*.json' 2>/dev/null)" ] &&
  [ -n "$(find "$CONFIGURATION/reglages" -name '*.json' 2>/dev/null)" ] && echo 0 || echo 1)" \
  "le projet et le réglage vivent sous l'état du poste : ${ranges:-rien}"
# Contre le paquet : `core/projets/` et `core/reglages/` y portent leur README et leur .gitignore.
sous_le_paquet="$(etrangers | sed 's|^\./||' |
  grep -E '^resources/produit/(core/(projets|reglages)/|\.maestro/)' | head -n 5 | tr '\n' ' ' || true)"
constat "$([ -z "$sous_le_paquet" ] && echo 0 || echo 1)" \
  "rien n'en est écrit sous le dossier d'installation${sous_le_paquet:+ — trouvés : $sous_le_paquet}"

# --- 4. qui sert ---------------------------------------------------------------------------------
releve_processus >"$TRACE/processus.tsv"
cat "$TRACE/processus.tsv" >>"$RAPPORT"
dehors=""
while IFS=$'\t' read -r pid _ppid nom chemin; do
  [ -z "$pid" ] && continue
  [ "$nom" = "conhost.exe" ] && continue
  dans_l_installation "$chemin" || dehors="$dehors $nom ($chemin)"
done <"$TRACE/processus.tsv"
# (Le complément se compose en deux temps : une apostrophe dans `${var:+…}` entre guillemets
# n'est pas lue comme un caractère.)
hors_d_elle=""
[ -n "$dehors" ] && hors_d_elle=" — hors d'elle :$dehors"
constat "$([ -s "$TRACE/processus.tsv" ] && [ -z "$dehors" ] && echo 0 || echo 1)" \
  "chaque processus de l'installation joue un exécutable de l'installation$hors_d_elle"
for attendu in Maestro.exe python.exe node.exe; do
  constat "$(awk -F'\t' -v nom="$attendu" '$3 == nom { trouve = 1 } END { exit !trouve }' "$TRACE/processus.tsv" && echo 0 || echo 1)" \
    "$attendu tourne depuis l'installation"
done
constat "$(contient "$(cat "$TRACE/processus.tsv")" "$W_RACINE" && echo 1 || echo 0)" \
  "aucun processus ne joue un fichier du clone"
PID_FENETRE="$(fenetre "$TRACE/processus.tsv")"

# --- 5. le CLI claude, depuis l'installation ---------------------------------------------------
# Le lanceur du confinement (#1279) d'abord : sans lui, une session d'agent tourne non confinée.
confinement="$(vierge "$PYTHON_EMBARQUE" -c 'from maestro.sandbox.confinement import chemin_lanceur; print(chemin_lanceur() or "")' 2>&1 | tr -d '\r')"
constat "$(dans_l_installation "$confinement" && echo 0 || echo 1)" \
  "le lanceur du confinement est celui de l'installation (${confinement:-aucun})"
cli="$(vierge "$PYTHON_EMBARQUE" -c 'from maestro.providers.claude import _commande_du_cli; c = _commande_du_cli(); print(c[0] if c else "")' 2>"$TRACE/cli-resolution.err" | tr -d '\r')"
constat "$(dans_l_installation "$cli" && echo 0 || echo 1)" \
  "le CLI que le produit lance est celui que l'installation embarque (${cli:-aucun})"
if [ -n "$cli" ]; then
  version="$(vierge "$(cygpath -u "$cli")" --version </dev/null 2>&1 | tr -d '\r' | head -n 3)"
  constat "$([[ "$version" =~ [0-9]+\.[0-9]+\.[0-9]+ ]] && echo 0 || echo 1)" \
    "le CLI embarqué se lance sur le poste vierge (--version : $version)"
fi
# Le SDK lance le CLI lui-même, jusqu'à la poignée de main : aucune question au modèle.
sonde_sdk="$(cd "$PROFIL" && vierge "$PYTHON_EMBARQUE" "$(cygpath -w "$RACINE/scripts/distribution/sonde_cli.py")" </dev/null 2>&1 | tr -d '\r')"
code_sonde=$?
printf '%s\n' "$sonde_sdk" >"$TRACE/sonde-cli.txt"
printf 'sonde du SDK :\n%s\n' "$sonde_sdk" >>"$RAPPORT"
constat "$(contient "$sonde_sdk" "poignée de main : rendue" && echo 0 || echo 1)" \
  "le SDK lance le CLI embarqué et en obtient la poignée de main (code $code_sonde) — $(printf '%s' "$sonde_sdk" | tail -n 1)"

# --- 6. fermer la fenêtre ----------------------------------------------------------------------
# Sans `/F` : `taskkill` demande à la fenêtre de se fermer, comme le bouton de sa barre de titre.
constat "$([ -n "$PID_FENETRE" ] && echo 0 || echo 1)" "la fenêtre de Maestro est trouvée (PID ${PID_FENETRE:-aucun})"
if [ -n "$PID_FENETRE" ]; then
  env MSYS2_ARG_CONV_EXCL='*' taskkill /PID "$PID_FENETRE" >>"$TRACE/arret.log" 2>&1
fi
debut_arret=$SECONDS
restants=""
for _ in $(seq 1 60); do
  restants="$(releve_processus)"
  [ -z "$restants" ] && break
  sleep 2
done
constat "$([ -z "$restants" ] && echo 0 || echo 1)" \
  "fermer la fenêtre arrête l'API, le front et la coque, en $((SECONDS - debut_arret)) s${restants:+ — restent : $restants}"
constat "$([ "$(sonde "http://127.0.0.1:$PORT_UI/")" = 000 ] && [ "$(sonde "http://127.0.0.1:$PORT_API/api/sante")" = 000 ] && echo 0 || echo 1)" \
  "les ports :$PORT_API et :$PORT_UI sont libérés"
cp "$PROFIL/.maestro/lanceur/"*.log "$TRACE/" 2>/dev/null || true

# Le dossier d'installation, une fois Maestro passé dessus : le paquet, et rien d'autre (#1620).
etrangers >"$TRACE/fichiers-etrangers.txt"
caches="$(grep -c '/__pycache__/' "$TRACE/fichiers-etrangers.txt" || true)"
autres="$(grep -v '/__pycache__/' "$TRACE/fichiers-etrangers.txt" | grep . | sed 's|^\./||' || true)"
constat "$([ -z "$autres" ] && echo 0 || echo 1)" \
  "le dossier d'installation ne porte que les fichiers du paquet ($caches fichier(s) du cache de compilation de Python à part)${autres:+ — étrangers : $(printf '%s\n' "$autres" | head -n 8 | tr '\n' ' ')}"

# --- 7. réinstaller par-dessus ---------------------------------------------------------------
# Un témoin posé dans le dossier d'installation dit ce que l'installeur en fait : on constate
# l'effacement, on ne le suppose pas.
TEMOIN="$INSTALL/temoin-verification.txt"
echo "posé par scripts/distribution/verifier.sh avant la réinstallation" >"$TEMOIN"
installer "$INSTALLEUR" par-dessus
code_par_dessus=$?
constat "$([ "$code_par_dessus" = 0 ] && [ -f "$INSTALL/Maestro.exe" ] && echo 0 || echo 1)" \
  "réinstaller par-dessus l'installation en place (code $code_par_dessus)"
if [ -f "$TEMOIN" ]; then
  echo "réinstaller par-dessus n'efface pas le dossier d'installation : le témoin qu'on y a posé est resté" |
    tee -a "$RAPPORT"
else
  echo "réinstaller par-dessus efface le dossier d'installation : le témoin qu'on y a posé a disparu" |
    tee -a "$RAPPORT"
fi
ouvrir par-dessus
constat "$([ "$api" = 200 ] && [ "$ui" = 200 ] && echo 0 || echo 1)" \
  "la version réinstallée s'ouvre (API HTTP $api, interface HTTP $ui)"
constat "$(retrouver par-dessus && echo 0 || echo 1)" \
  "après la réinstallation par-dessus, le projet « $NOM_PROJET » et le réglage sont retrouvés"
fermer par-dessus
constat "$([ -z "$restants" ] && echo 0 || echo 1)" "la version réinstallée se ferme${restants:+ — restent : $restants}"

# --- 8. désinstaller ---------------------------------------------------------------------------
if [ -f "$INSTALL/Uninstall Maestro.exe" ]; then
  debut_desinstallation=$SECONDS
  desinstaller premiere
  cp "$TRACE/desinstallation-premiere.log" "$TRACE/desinstallation.log" 2>/dev/null || true
  reste="$(find "$INSTALL" -type f 2>/dev/null | sed "s|^$INSTALL/||" | head -n 5 | tr '\n' ' ')"
  constat "$([ -z "$reste" ] && echo 0 || echo 1)" \
    "la désinstallation silencieuse retire Maestro, en $((SECONDS - debut_desinstallation)) s${reste:+ — restent : $reste}"
  raccourcis="$(find "$PROFIL" -iname 'Maestro.lnk' 2>/dev/null | sed "s|^$PROFIL/||" | tr '\n' ' ')"
  constat "$([ -z "$raccourcis" ] && echo 0 || echo 1)" \
    "les raccourcis (bureau, menu Démarrer) partent avec elle${raccourcis:+ — restent : $raccourcis}"
  cle="$(env MSYS2_ARG_CONV_EXCL='*' reg query 'HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall' /s /f "$W_INSTALL" 2>/dev/null | grep -c 'DisplayName' || true)"
  constat "$([ "$cle" = 0 ] && echo 0 || echo 1)" \
    "la clé de désinstallation (HKCU) part avec elle"
else
  constat 1 "aucun désinstalleur sous l'installation"
fi
etat_poste="$(find "$PROFIL/.maestro" -maxdepth 1 2>/dev/null | sed "s|^$PROFIL/||" | tr '\n' ' ')"
echo "l'état du poste reste sous le profil : ${etat_poste:-rien}" | tee -a "$RAPPORT"
ranges_apres="$(sous_la_configuration)"
constat "$([ -n "$ranges_apres" ] && [ "$ranges_apres" = "$ranges" ] && echo 0 || echo 1)" \
  "la désinstallation laisse ce que la personne a écrit sous l'état du poste : ${ranges_apres:-rien}"

# --- 9. réinstaller après la désinstallation -------------------------------------------------
installer "$INSTALLEUR" apres-desinstallation
code_reinstallation=$?
constat "$([ "$code_reinstallation" = 0 ] && [ -f "$INSTALL/Maestro.exe" ] && echo 0 || echo 1)" \
  "réinstaller après la désinstallation (code $code_reinstallation)"
ouvrir apres-desinstallation
constat "$([ "$api" = 200 ] && [ "$ui" = 200 ] && echo 0 || echo 1)" \
  "la version réinstallée s'ouvre (API HTTP $api, interface HTTP $ui)"
constat "$(retrouver apres-desinstallation && echo 0 || echo 1)" \
  "après désinstallation et réinstallation, le projet « $NOM_PROJET » et le réglage sont encore retrouvés"
if [ "$TENIR_OUVERT" -gt 0 ] 2>/dev/null; then
  echo "[verification] Maestro reste ouvert $TENIR_OUVERT s : http://127.0.0.1:$PORT_UI/ — ${TRACE#"$RACINE"/}/fermer l'abrège"
  fin_attente=$((SECONDS + TENIR_OUVERT))
  while [ "$SECONDS" -lt "$fin_attente" ] && [ ! -e "$TRACE/fermer" ]; do sleep 2; done
fi
fermer apres-desinstallation
constat "$([ -z "$restants" ] && echo 0 || echo 1)" "elle se ferme${restants:+ — restent : $restants}"

# --- 10. la mise à jour (--mise-a-jour, #644) ----------------------------------------------------
# La version installée — celle qu'on vérifie — s'ouvre sur un canal qui annonce la suivante. Elle la
# télécharge seule et propose de l'installer. On répond « Plus tard » comme la personne le ferait, puis
# on ferme Maestro : la version suivante doit se poser à la fermeture, après l'arrêt de la stack, sans
# rien relancer. La personne rouvre alors Maestro, et on regarde ce qui tourne et ce qui a survécu.
#
# « Redémarrer maintenant » ne se joue pas ici. L'installeur relance Maestro par l'Explorateur
# (`ExecShellAsUser` d'electron-builder), donc dans l'environnement de la session Windows et non
# dans celui du poste simulé : joué d'ici, il rouvrirait Maestro sur le vrai profil (docs/54 §6.4).
CACHE_DES_MISES_A_JOUR="$PROFIL/AppData/Local/maestro-updater"
if [ -n "$FLUX_DOSSIER" ]; then
  version_avant="$(version_du_produit)"
  echo "[verification] mise à jour : $version_avant → $VERSION_SUIVANTE (canal ${FLUX_DOSSIER#"$RACINE"/})"
  # Le canal sur le poste et sur l'hôte local seul, servi par le venv du dépôt (`canal.sh`).
  canal_servir "$RACINE/.venv/Scripts/python.exe" "$PORT_FLUX" "$(cygpath -w "$FLUX_DOSSIER")" "$TRACE"
  servi=$?
  refus=""
  [ "$servi" = 3 ] && refus=" — refusé : le port répondait déjà, un autre serveur (celui d'une vérification interrompue ?) l'occupe"
  constat "$([ "$servi" = 0 ] && echo 0 || echo 1)" \
    "le canal local sert latest.yml (version $VERSION_SUIVANTE) sur http://127.0.0.1:$PORT_FLUX/$refus"
  FLUX_COQUE="http://127.0.0.1:$PORT_FLUX/"
  ouvrir mise-a-jour
  FLUX_COQUE=0
  constat "$([ "$api" = 200 ] && [ "$ui" = 200 ] && echo 0 || echo 1)" \
    "la version $version_avant s'ouvre sur ce canal (API HTTP $api, interface HTTP $ui)"
  run_id="$(lancer_un_run)"
  constat "$([ -n "$run_id" ] && echo 0 || echo 1)" \
    "un run est lancé dans le projet « $NOM_PROJET », pour que le journal porte ce que la personne a fait (${run_id:-refusé : $(head -c 300 "$TRACE/run-mise-a-jour.json")})"
  attendre_la_mise_a_jour "$TRACE/coque-mise-a-jour.log"
  pret=$?
  constat "$pret" "la coque trouve $VERSION_SUIVANTE, la télécharge et l'annonce prête — $(tr -d '\r' <"$TRACE/coque-mise-a-jour.log" | grep -F '[mise à jour]' | tail -n 1)"
  releve_processus >"$TRACE/processus-mise-a-jour.tsv"
  PID_FENETRE="$(fenetre "$TRACE/processus-mise-a-jour.tsv")"
  if [ "$pret" = 0 ] && [ -n "$PID_FENETRE" ]; then
    presse="$(presser "$PID_FENETRE" 'Plus*')"
    printf 'dialogue de la mise à jour :\n%s\n' "$presse" | tee -a "$RAPPORT"
    constat "$(contient "$presse" "texte : Maestro $VERSION_SUIVANTE est prête à s'installer." &&
      contient "$presse" "texte : Redémarrer maintenant" &&
      contient "$presse" "presse : Plus tard" && echo 0 || echo 1)" \
      "la coque propose $VERSION_SUIVANTE (« Redémarrer maintenant » ou « Plus tard »), et « Plus tard » est pressé d'un clic"
    constat "$(contient "$presse" "un run en cours s'arrête avec lui" && echo 0 || echo 1)" \
      "le dialogue dit, avant le choix, qu'un run en cours s'arrête avec Maestro"
    # « Plus tard » ne change rien : Maestro sert toujours, à la même version.
    sleep 3
    api="$(sonde "http://127.0.0.1:$PORT_API/api/sante" "$jeton")"
    constat "$([ "$api" = 200 ] && [ "$(version_du_produit)" = "$version_avant" ] &&
      ! tr -d '\r' <"$TRACE/coque-mise-a-jour.log" | grep -qF 'redémarrage demandé' && echo 0 || echo 1)" \
      "« Plus tard » n'interrompt rien : Maestro sert toujours (API HTTP $api), à la version $(version_du_produit)"
    # Le journal d'avant, relu juste avant la fermeture : ce qui est consigné à ce moment doit se
    # retrouver tel quel ensuite.
    journal_avant="$(journal)"
    printf 'journal avant la mise à jour :\n%s\n' "$journal_avant" | tee -a "$RAPPORT"
    # Fermer la fenêtre : la stack s'arrête, puis la coque pose la version prête, en silence.
    debut_maj=$SECONDS
    env MSYS2_ARG_CONV_EXCL='*' taskkill /PID "$PID_FENETRE" >>"$TRACE/arret.log" 2>&1
    en_vol=""
    for _ in $(seq 1 300); do
      sleep 2
      en_vol="$(installeur_en_vol)"
      [ -z "$en_vol" ] && [ "$(version_du_produit)" = "$VERSION_SUIVANTE" ] &&
        ! kill -0 "$PID_COQUE" 2>/dev/null && break
    done
    encore=""
    [ -n "$en_vol" ] && encore=" — l'installeur tourne encore : $en_vol"
    constat "$([ -z "$en_vol" ] && [ "$(version_du_produit)" = "$VERSION_SUIVANTE" ] && echo 0 || echo 1)" \
      "fermer Maestro pose $VERSION_SUIVANTE, en silence, en $((SECONDS - debut_maj)) s (version.json : $(version_du_produit))$encore"
    coque_log="$(tr -d '\r' <"$TRACE/coque-mise-a-jour.log")"
    l_arret="$(grep -nF 'Maestro est arrêté' <<<"$coque_log" | tail -n 1 | cut -d: -f1)"
    l_pose="$(grep -nF "[mise à jour] installation de $VERSION_SUIVANTE, en silence" <<<"$coque_log" | head -n 1 | cut -d: -f1)"
    constat "$([ -n "$l_arret" ] && [ -n "$l_pose" ] && [ "$l_arret" -lt "$l_pose" ] && echo 0 || echo 1)" \
      "la version ne se pose qu'après l'arrêt de la stack (journal de la coque : arrêt ligne ${l_arret:-absente}, pose ligne ${l_pose:-absente})"
    sleep 10
    relance="$(releve_processus | cut -f3 | tr '\n' ' ')"
    constat "$([ -z "$relance" ] && echo 0 || echo 1)" \
      "à la fermeture, rien ne se relance : la personne a fermé Maestro${relance:+ — tournent : $relance}"
    # La personne rouvre Maestro, sur la version posée.
    ouvrir apres-mise-a-jour
    constat "$([ "$api" = 200 ] && [ "$ui" = 200 ] && echo 0 || echo 1)" \
      "Maestro se rouvre sur $VERSION_SUIVANTE, sans clone ni geste de plus (API HTTP $api, interface HTTP $ui)"
    # Les quatre composants (#1622) : la coque, le produit (front et backend, posés ensemble), et le
    # CLI que le produit lance — tous de l'installation, à la nouvelle version.
    coque_apres="$(version_de_la_coque)"
    constat "$(contient "$coque_apres" "$VERSION_SUIVANTE" && echo 0 || echo 1)" \
      "la coque est à $VERSION_SUIVANTE (Maestro.exe : ${coque_apres:-illisible})"
    appel GET /api/installation >"$TRACE/installation-apres.json"
    installation_apres="$(tr -d '\r' <"$TRACE/installation-apres.json")"
    constat "$(contient "${installation_apres// /}" "\"produit\":{\"version\":\"$VERSION_SUIVANTE\"" && echo 0 || echo 1)" \
      "le produit se dit à $VERSION_SUIVANTE (/api/installation, l'écran Paramètres)"
    constat "$(contient "${installation_apres// /}" '"provenance":"paquet"' && echo 0 || echo 1)" \
      "le CLI claude lancé est celui du paquet, donc de la nouvelle version — $(head -c 400 "$TRACE/installation-apres.json")"
    releve_processus >"$TRACE/processus-apres.tsv"
    dehors=""
    while IFS=$'\t' read -r pid _ppid nom chemin; do
      [ -z "$pid" ] && continue
      [ "$nom" = "conhost.exe" ] && continue
      dans_l_installation "$chemin" || dehors="$dehors $nom ($chemin)"
    done <"$TRACE/processus-apres.tsv"
    hors_d_elle=""
    [ -n "$dehors" ] && hors_d_elle=" — hors d'elle :$dehors"
    constat "$([ -s "$TRACE/processus-apres.tsv" ] && [ -z "$dehors" ] && echo 0 || echo 1)" \
      "après la mise à jour, chaque processus joue un exécutable de l'installation$hors_d_elle"
    # Ce que la personne avait : son projet, son réglage, son journal et son run.
    constat "$(retrouver apres-mise-a-jour && echo 0 || echo 1)" \
      "après la mise à jour, le projet « $NOM_PROJET » et le réglage sont retrouvés"
    dernier="$(sed -n 's/.* dernier=\([0-9]*\) .*/\1/p' <<<"$journal_avant" | head -n 1)"
    journal_apres="$(journal "${dernier:-0}")"
    printf 'journal après la mise à jour (relu jusqu au rang %s) :\n%s\n' "${dernier:-0}" "$journal_apres" | tee -a "$RAPPORT"
    empreinte_avant="$(sed -n 's/.* empreinte=\([0-9a-f]*\).*/\1/p' <<<"$journal_avant" | head -n 1)"
    empreinte_apres="$(sed -n 's/.* empreinte=\([0-9a-f]*\).*/\1/p' <<<"$journal_apres" | head -n 1)"
    constat "$([ "${dernier:-0}" -gt 0 ] && [ -n "$empreinte_avant" ] && [ "$empreinte_avant" = "$empreinte_apres" ] && echo 0 || echo 1)" \
      "le journal traverse la mise à jour : ses ${dernier:-0} premier(s) événement(s) sont relus à l'identique"
    if [ -n "$run_id" ]; then
      run_apres="$(appel GET "/api/executions/$run_id")"
      printf '%s\n' "$run_apres" >"$TRACE/run-apres.json"
      constat "$(contient "$run_apres" "\"$run_id\"" && echo 0 || echo 1)" \
        "le run d'avant la mise à jour est retrouvé — $(head -c 300 "$TRACE/run-apres.json")"
    fi
    cache_pose="$(find "$CACHE_DES_MISES_A_JOUR" -type f 2>/dev/null | sed "s|^$PROFIL/||" | tr '\n' ' ')"
    echo "cache des mises à jour : ${cache_pose:-vide}" | tee -a "$RAPPORT"
    cp "$PROFIL/.maestro/lanceur/"*.log "$TRACE/" 2>/dev/null || true
    fermer apres-mise-a-jour
    constat "$([ -z "$restants" ] && echo 0 || echo 1)" "la version mise à jour se ferme${restants:+ — restent : $restants}"
  else
    constat 1 "rien à presser : aucune version annoncée prête, ou aucune fenêtre (PID ${PID_FENETRE:-aucun})"
    fermer mise-a-jour
  fi
  if [ "$servi" != 3 ]; then
    canal_arreter "$PORT_FLUX" "$TRACE"
    constat "$?" "le canal local est arrêté : plus rien ne répond sur http://127.0.0.1:$PORT_FLUX/"
  fi
fi

desinstaller finale
reste="$(find "$INSTALL" -type f 2>/dev/null | sed "s|^$INSTALL/||" | head -n 5 | tr '\n' ' ')"
constat "$([ -z "$reste" ] && echo 0 || echo 1)" \
  "la dernière désinstallation laisse le poste sans Maestro${reste:+ — restent : $reste}"
if [ -n "$FLUX_DOSSIER" ]; then
  constat "$([ ! -e "$CACHE_DES_MISES_A_JOUR" ] && echo 0 || echo 1)" \
    "la désinstallation emporte le cache des mises à jour (AppData/Local/maestro-updater)"
fi

echo
echo "[verification] $ECHECS constat(s) ✗ — rapport : ${RAPPORT#"$RACINE"/}"
[ "$ECHECS" = 0 ]
