#!/usr/bin/env bash
# L'ÉTAT DE LA STACK D'UNE COPIE DE TRAVAIL — où il vit, et qui le retire (#1456, chantier #1454).
#
# Ce fichier ne se lance pas : il se SOURCE. Deux lecteurs, une seule écriture du chemin et du
# verdict « stack vivante » :
#
#   scripts/controltower/start.sh   y range l'état de sa stack, et ramasse à chaque démarrage
#   scripts/git/worktree.sh gc      retire l'état d'une copie avec son worktree
#
# CE QU'IL PORTE. Le jeton de session, le pid du chien de garde et le profil jetable de la fenêtre
# isolée (`start.sh`) : rien qu'on lise à la main, rien qui vaille quoi que ce soit une fois la stack
# éteinte. Les journaux, eux, restent sous la racine de la copie (`.maestro/controltower/`, #234).
#
# OÙ. `${TMPDIR:-/tmp}/maestro/controltower-<api>-<ui>` : sous la racine du jetable que déclare
# `maestro/emplacements.py` (`NOM_JETABLE`, #1455), indexé par les ports pour le motif d'origine —
# deux stacks d'une même machine ne partagent ni jeton ni chien de garde (#152). Jusqu'à #1456,
# `${TMPDIR:-/tmp}/maestro-controltower-<api>-<ui>`, à même le répertoire temporaire : 117 dossiers
# le 2026-10-08, un par paire de ports montée depuis septembre, aucun jamais retiré.
#
# QUI LE RETIRE. Pas le ramassage des hôtes (`maestro/sandbox/ramassage.py`) : il juge un jetable par
# le pid de son nom ou par son âge, et une stack inactive depuis six heures n'en est pas moins
# vivante — lui retirer son jeton ferait perdre à son chien de garde la session qu'il surveille, donc
# l'arrêt automatique à la fermeture de la fenêtre. Ce ramassage-là l'écarte
# (`FAMILLES_TIERCES_JETABLES`), et l'état part avec SA COPIE :
#   - `start.sh` écrit dans le témoin `copie` le chemin de la copie qui a démarré la stack ;
#   - `etat_stack_ramasser` retire un dossier dont cette copie a disparu — `worktree.sh gc` le joue
#     après ses retraits, `start.sh` à chaque démarrage, ce qui rattrape aussi un worktree retiré
#     hors de `gc` ;
#   - `etat_stack_ramasser --ancien` retire en plus les dossiers de l'ancien emplacement.
# Un dossier sans témoin n'est à personne qu'on sache nommer : il reste.
#
# « STACK VIVANTE » : le pid de son chien de garde vit, ou un de ses deux ports écoute. C'est la seule
# garde, et elle passe avant tout le reste : un pid recyclé fait conserver un dossier mort, jamais
# l'inverse, et des ports illisibles dans le nom valent « vivante ».
#
# Best-effort de bout en bout : rien ne lève, ce qui résiste attend le passage suivant.
# MAESTRO_RAMASSAGE_ETAT_STACK=0 l'éteint.

#: La racine du jetable, telle que le Bash la nomme. `${TMPDIR:-/tmp}` est ce que l'agent et ce
#: script appellent `/tmp` : sous Git Bash, le répertoire temporaire du profil (montage `usertemp`
#: de MSYS) — le même que `maestro.emplacements.racine_jetable()` résout côté Python.
ETAT_STACK_RACINE="${TMPDIR:-/tmp}/maestro"

#: Le préfixe d'un dossier d'état sous cette racine ; `start.sh` y ajoute `<api>-<ui>`.
ETAT_STACK_PREFIXE="controltower-"

#: L'ancien emplacement (avant #1456) et le préfixe qu'il portait.
ETAT_STACK_ANCIENNE_RACINE="${TMPDIR:-/tmp}"
ETAT_STACK_ANCIEN_PREFIXE="maestro-controltower-"

#: Le pid du chien de garde (écrit par `start.sh`) et le témoin de la copie qui a démarré la stack.
ETAT_STACK_CHIEN="chien-de-garde.pid"
ETAT_STACK_TEMOIN="copie"

# Sous Windows, les ports à l'écoute se lisent par `netstat` ; ailleurs par `/proc` ou `lsof`.
# `OSTYPE` plutôt que `uname` : la question se pose à chaque `source`, et un fork y coûte cher.
case "${OSTYPE:-}" in
  msys* | cygwin*) ETAT_STACK_WINDOWS=1 ;;
  *) ETAT_STACK_WINDOWS=0 ;;
esac

# Les ports à l'écoute, relevés UNE FOIS par passage — `netstat` coûte une seconde sous Windows, et un
# passage peut juger une centaine de dossiers. Encadrés d'espaces, pour une recherche par motif.
ETAT_STACK_PORTS=""
ETAT_STACK_PORTS_LUS=0

etat_stack_releve_ports() {
  [ "$ETAT_STACK_PORTS_LUS" = 1 ] && return 0
  ETAT_STACK_PORTS_LUS=1
  local ports="" hex f
  if [ "$ETAT_STACK_WINDOWS" = 1 ]; then
    ports="$(netstat -ano 2>/dev/null |
      awk '$1 == "TCP" && $4 == "LISTENING" { sub(/.*:/, "", $2); print $2 }' | sort -u | tr '\n' ' ')" ||
      true
  elif [ -r /proc/net/tcp ]; then
    # Linux (dont le conteneur du filet CI, sans `lsof`) : état `0A` = LISTEN, port en hexadécimal.
    for f in /proc/net/tcp /proc/net/tcp6; do
      [ -r "$f" ] || continue
      while IFS= read -r hex; do
        [ -n "$hex" ] && ports="$ports$((16#$hex)) "
      done < <(awk '$4 == "0A" { n = split($2, a, ":"); print a[n] }' "$f")
    done
  elif command -v lsof >/dev/null 2>&1; then
    ports="$(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null |
      awk 'NR > 1 { n = $9; sub(/.*:/, "", n); print n }' | sort -u | tr '\n' ' ')" || true
  fi
  ETAT_STACK_PORTS=" $ports "
}

# etat_stack_vivante <dossier> : 0 si la stack de ce dossier d'état vit encore — son chien de garde,
# ou un de ses deux ports, lus dans le nom (`…controltower-<api>-<ui>`).
etat_stack_vivante() {
  local dossier="$1" nom couple api ui pid=""
  if [ -f "$dossier/$ETAT_STACK_CHIEN" ]; then
    read -r pid <"$dossier/$ETAT_STACK_CHIEN" 2>/dev/null || true
    case "$pid" in
      '' | *[!0-9]*) ;;
      *) kill -0 "$pid" 2>/dev/null && return 0 ;;
    esac
  fi
  nom="${dossier##*/}"
  couple="${nom##*controltower-}"
  api="${couple%%-*}"
  ui="${couple#*-}"
  case "$api" in '' | *[!0-9]*) return 0 ;; esac
  case "$ui" in '' | *[!0-9]*) return 0 ;; esac
  etat_stack_releve_ports
  case "$ETAT_STACK_PORTS" in
    *" $api "* | *" $ui "*) return 0 ;;
  esac
  return 1
}

# _etat_stack_retirer <dossier> : retire un dossier dont la stack ne vit plus. 0 s'il est parti.
_etat_stack_retirer() {
  local dossier="$1"
  if etat_stack_vivante "$dossier"; then
    ETAT_STACK_CONSERVES=$((ETAT_STACK_CONSERVES + 1))
    return 1
  fi
  rm -rf "$dossier" 2>/dev/null
  if [ -e "$dossier" ]; then
    # Un profil de navigateur encore tenu, sous Windows : il partira au passage suivant.
    ETAT_STACK_RESISTANTS=$((ETAT_STACK_RESISTANTS + 1))
    return 1
  fi
  ETAT_STACK_RETIRES=$((ETAT_STACK_RETIRES + 1))
  return 0
}

# etat_stack_ramasser [--ancien] : retire l'état des stacks éteintes dont la copie a disparu ; avec
# `--ancien`, aussi tout dossier de l'ancien emplacement dont la stack est éteinte. Ne parle pas : les
# compteurs `ETAT_STACK_RETIRES` (dont `ETAT_STACK_ANCIENS` à l'ancien emplacement),
# `ETAT_STACK_RESISTANTS` et `ETAT_STACK_CONSERVES` disent ce qui s'est passé, à l'appelant de
# l'écrire à sa façon.
etat_stack_ramasser() {
  ETAT_STACK_RETIRES=0
  ETAT_STACK_ANCIENS=0
  ETAT_STACK_RESISTANTS=0
  ETAT_STACK_CONSERVES=0
  [ "${MAESTRO_RAMASSAGE_ETAT_STACK:-}" = 0 ] && return 0
  local dossier copie
  for dossier in "$ETAT_STACK_RACINE/$ETAT_STACK_PREFIXE"*; do
    [ -d "$dossier" ] && [ -f "$dossier/$ETAT_STACK_TEMOIN" ] || continue
    copie=""
    read -r copie <"$dossier/$ETAT_STACK_TEMOIN" 2>/dev/null || true
    [ -n "$copie" ] && [ ! -d "$copie" ] || continue
    _etat_stack_retirer "$dossier" || true
  done
  [ "${1:-}" = "--ancien" ] || return 0
  for dossier in "$ETAT_STACK_ANCIENNE_RACINE/$ETAT_STACK_ANCIEN_PREFIXE"*; do
    [ -d "$dossier" ] || continue
    _etat_stack_retirer "$dossier" && ETAT_STACK_ANCIENS=$((ETAT_STACK_ANCIENS + 1))
  done
  return 0
}
