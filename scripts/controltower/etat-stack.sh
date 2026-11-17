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
# l'inverse, et des ports illisibles dans le nom valent « vivante », comme une table des écoutes qui
# ne s'est pas lue entière.
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

# `OSTYPE` plutôt que `uname` : la question se pose à chaque `source`, et un fork y coûte cher.
case "${OSTYPE:-}" in
  msys* | cygwin*) ETAT_STACK_WINDOWS=1 ;;
  *) ETAT_STACK_WINDOWS=0 ;;
esac

# --- La table des écoutes --------------------------------------------------------------------------
# Qui écoute sur quel port : ce qui fait une stack vivante ici, et ce par quoi `start.sh` (après ses
# témoins, #1579) et `scripts/presentation/captures.sh` retrouvent le processus qui tient un port.
# La lecture vit ici seulement.
#
#   - Sous Windows, la table du système, `TCP_TABLE_OWNER_PID_LISTENER`, lue par `ecoutes.py` avec
#     le Python du dépôt. Pas `netstat` : il n'affiche une écoute que dans l'état `LISTENING`, et un
#     poste peut porter les siennes dans un état 0, qu'il tait (#1696). Quand ce lecteur ne peut pas
#     jouer (pas de venv, appel refusé), on retombe sur `netstat` et on le dit : son silence ne
#     prouve rien.
#   - Ailleurs, `/proc` (dont le conteneur du filet CI, sans `lsof`), puis `lsof`.
#
# MAESTRO_TABLE_DES_ECOUTES nomme un exécutable qui rend la table à la place du système, même format,
# un code non nul valant une lecture impossible : c'est par lui qu'un test rend la table aveugle.

#: Ce fichier se source : son dossier vient de BASH_SOURCE, sans fork.
case "${BASH_SOURCE[0]}" in
  */*) ETAT_STACK_ICI="${BASH_SOURCE[0]%/*}" ;;
  *) ETAT_STACK_ICI="." ;;
esac
ETAT_STACK_LECTEUR="$ETAT_STACK_ICI/ecoutes.py"
ETAT_STACK_PYTHON="$ETAT_STACK_ICI/../../.venv/Scripts/python.exe"

# etat_stack_ecoutes : relève la table dans ETAT_STACK_ECOUTES, une ligne `<port> <pid>` par écoute
# (`-` pour un pid qu'on ne sait pas lire). Rend 0 sur la table du système, vide comprise ; 2 sur le
# repli, qui peut ne pas voir une écoute ; 1 quand rien ne s'est lu. Hors du 0, ETAT_STACK_ECOUTES_AVIS
# dit pourquoi. Chaque appel relit, et ne parle pas : l'appelant écrit l'avis à sa façon.
etat_stack_ecoutes() {
  ETAT_STACK_ECOUTES=""
  ETAT_STACK_ECOUTES_AVIS=""
  local sortie="" code=0 lecteur
  if [ -n "${MAESTRO_TABLE_DES_ECOUTES:-}" ]; then
    lecteur="la table imposée (MAESTRO_TABLE_DES_ECOUTES)"
    sortie="$("$MAESTRO_TABLE_DES_ECOUTES" 2>&1)" || code=$?
  elif [ "$ETAT_STACK_WINDOWS" = 1 ]; then
    lecteur="la table TCP du système"
    if [ -x "$ETAT_STACK_PYTHON" ]; then
      # La cause d'un refus vient sur l'erreur standard, et rien d'autre n'y passe sur un succès.
      sortie="$("$ETAT_STACK_PYTHON" -I "$ETAT_STACK_LECTEUR" 2>&1)" || code=$?
    else
      sortie="pas de Python du dépôt (.venv)"
      code=127
    fi
  else
    _etat_stack_ecoutes_unix
    return
  fi
  if [ "$code" = 0 ]; then
    ETAT_STACK_ECOUTES="${sortie//$'\r'/}"
    return 0
  fi
  sortie="${sortie//$'\r'/}"
  ETAT_STACK_ECOUTES_AVIS="$lecteur illisible (${sortie%%$'\n'*}, code $code)"
  _etat_stack_ecoutes_netstat
}

# etat_stack_pids_du_port <port> : les pids qui écoutent sur ce port, dans ETAT_STACK_PIDS (séparés
# par une espace, sans doublon), lus par `etat_stack_ecoutes`, dont il rend le code et laisse l'avis.
# C'est par lui que `start.sh` et `scripts/presentation/captures.sh` désignent ce qu'ils arrêtent. Un
# pid que la table ne sait pas lire (`-`) n'y entre jamais : ce qu'on ne sait pas nommer ne se tue pas.
etat_stack_pids_du_port() {
  local port="$1" port_lu pid_lu code=0
  ETAT_STACK_PIDS=""
  etat_stack_ecoutes || code=$?
  while read -r port_lu pid_lu; do
    [ "$port_lu" = "$port" ] || continue
    case "$pid_lu" in '' | *[!0-9]*) continue ;; esac
    case " $ETAT_STACK_PIDS " in *" $pid_lu "*) ;; *) ETAT_STACK_PIDS="$ETAT_STACK_PIDS $pid_lu" ;; esac
  done <<<"$ETAT_STACK_ECOUTES"
  return "$code"
}

# Le repli : les lignes `LISTENING` de `netstat`. Rend 2, ou 1 sans `netstat` qui réponde.
_etat_stack_ecoutes_netstat() {
  local sortie
  if ! command -v netstat >/dev/null 2>&1 || ! sortie="$(netstat -ano 2>/dev/null)"; then
    ETAT_STACK_ECOUTES_AVIS="$ETAT_STACK_ECOUTES_AVIS, et netstat ne répond pas : aucune écoute ne se lit"
    return 1
  fi
  ETAT_STACK_ECOUTES="$(printf '%s\n' "$sortie" |
    awk '$1 == "TCP" && $4 == "LISTENING" { sub(/\r$/, "", $5); p = $2; sub(/.*:/, "", p); print p, $5 }' |
    sort -u)" || true
  ETAT_STACK_ECOUTES_AVIS="$ETAT_STACK_ECOUTES_AVIS — repli sur netstat, qui peut ne pas voir une écoute (#1579)"
  return 2
}

# Hors de Windows : `/proc`, puis `lsof`. Rend 0, ou 1 quand aucun des deux n'est là.
_etat_stack_ecoutes_unix() {
  local pids
  if [ -r /proc/net/tcp ]; then
    # Les sockets de chaque processus lisible, en un seul `find` : `/proc/<pid>/fd socket:[<inode>]`.
    # Un pid hors de portée (un autre utilisateur) reste `-`.
    pids="$(find /proc/[0-9]*/fd -maxdepth 1 -lname 'socket:\[*' -printf '%h %l\n' 2>/dev/null)" || true
    # État `0A` = LISTEN ; port en hexadécimal, inode en dixième colonne.
    ETAT_STACK_ECOUTES="$({
      printf '%s\n--\n' "$pids"
      cat /proc/net/tcp /proc/net/tcp6 2>/dev/null
    } | awk '
      function hex(s,  n, i) {
        n = 0
        for (i = 1; i <= length(s); i++) n = n * 16 + index("0123456789ABCDEF", toupper(substr(s, i, 1))) - 1
        return n
      }
      phase == 0 && $0 == "--" { phase = 1; next }
      phase == 0 { split($1, a, "/"); i = $2; gsub(/[^0-9]/, "", i); pids[i] = pids[i] " " a[3]; next }
      $4 == "0A" {
        n = split($2, b, ":"); port = hex(b[n])
        if (!($10 in pids)) { print port, "-"; next }
        m = split(pids[$10], c, " ")
        for (j = 1; j <= m; j++) print port, c[j]
      }' | sort -u)" || true
    return 0
  fi
  if command -v lsof >/dev/null 2>&1; then
    # `lsof` sort en 1 quand rien n'écoute : son code ne dit pas une lecture impossible.
    ETAT_STACK_ECOUTES="$(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null |
      awk 'NR > 1 { n = $9; sub(/.*:/, "", n); print n, $2 }' | sort -u)" || true
    return 0
  fi
  ETAT_STACK_ECOUTES_AVIS="ni /proc ni lsof sur ce poste : aucune écoute ne se lit"
  return 1
}

# Les ports à l'écoute, relevés UNE FOIS par passage — un passage peut juger une centaine de dossiers.
# Encadrés d'espaces, pour une recherche par motif. ETAT_STACK_PORTS_CODE garde le code de la lecture,
# ETAT_STACK_PORTS_AVIS son avis : une lecture qui peut ne pas tout voir ne fait mourir aucune stack.
ETAT_STACK_PORTS=""
ETAT_STACK_PORTS_LUS=0
ETAT_STACK_PORTS_CODE=0
ETAT_STACK_PORTS_AVIS=""

etat_stack_releve_ports() {
  [ "$ETAT_STACK_PORTS_LUS" = 1 ] && return 0
  ETAT_STACK_PORTS_LUS=1
  local ports="" port _pid code=0
  etat_stack_ecoutes || code=$?
  while read -r port _pid; do
    [ -n "$port" ] && ports="$ports$port "
  done <<<"$ETAT_STACK_ECOUTES"
  ETAT_STACK_PORTS=" $ports "
  ETAT_STACK_PORTS_CODE=$code
  ETAT_STACK_PORTS_AVIS="$ETAT_STACK_ECOUTES_AVIS"
}

# etat_stack_vivante <dossier> : 0 si la stack de ce dossier d'état vit encore — son chien de garde,
# ou un de ses deux ports, lus dans le nom (`…controltower-<api>-<ui>`). Une table des écoutes qui
# n'a pas pu se lire entière ne la dit jamais éteinte.
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
  [ "$ETAT_STACK_PORTS_CODE" = 0 ] || return 0
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
# `ETAT_STACK_RESISTANTS` et `ETAT_STACK_CONSERVES` disent ce qui s'est passé, et
# `ETAT_STACK_PORTS_AVIS` une table des écoutes qui ne s'est pas lue entière — à l'appelant de
# l'écrire à sa façon. Chaque passage relit la table, une fois.
etat_stack_ramasser() {
  ETAT_STACK_RETIRES=0
  ETAT_STACK_ANCIENS=0
  ETAT_STACK_RESISTANTS=0
  ETAT_STACK_CONSERVES=0
  ETAT_STACK_PORTS_LUS=0
  ETAT_STACK_PORTS_CODE=0
  # shellcheck disable=SC2034  # lu par les appelants (`start.sh`, `worktree.sh gc`)
  ETAT_STACK_PORTS_AVIS=""
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
