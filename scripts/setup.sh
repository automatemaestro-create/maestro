#!/usr/bin/env bash
# Mise en route d'un clone Maestro — socle local (ticket #145, parent #144).
#
# Amène un clone frais à l'état « il ne reste qu'à renseigner le .env et lancer », en une
# commande, sous Windows (Git Bash), macOS ou Linux :
#
#   bash scripts/setup.sh            # monte ce qui manque
#   bash scripts/setup.sh --check    # diagnostic seul — n'écrit RIEN
#   bash scripts/setup.sh --help     # étapes disponibles et drapeaux
#
# Principes (README § Développement, docs/10-workflow-git.md §7) :
#   - IDEMPOTENT : relancé sur une machine déjà prête, tout ressort en « DÉJÀ FAIT ».
#   - NON DESTRUCTIF : n'écrase jamais un .env existant ; .claude/settings.local.json est
#     FUSIONNÉ clé par clé, sans toucher à ce qui y est déjà posé.
#   - INSTALLE CE QUI MANQUE : un prérequis absent (python, node, npm, git, glab) est installé
#     D'OFFICE via le gestionnaire de paquets de la plateforme (winget / brew / apt), sans rien
#     demander — puis le PATH de la session est rafraîchi et l'outil re-détecté. `--no-install`
#     (ou MAESTRO_AUTO_INSTALL=0) pour s'en tenir au signalement.
#   - NON INTERACTIF : le script ne pose aucune question. Ce qui exige un humain (authentifications,
#     secrets à renseigner) sort dans la section « Reste à faire » du rapport.
#   - FRANC SUR SES LIMITES : ce qu'un script ne peut pas contourner est dit, pas masqué —
#     gestionnaire de paquets absent, élévation refusée, ou binaire installé mais encore hors PATH
#     (Windows : winget met à jour l'environnement persistant, pas le shell déjà lancé).
#   - TOUT SE DÉROULE : une étape en échec n'interrompt pas les suivantes. Le script rend un
#     rapport final et sort en code non nul si une étape DURE a échoué.
#   - AUCUN SECRET : ni lu, ni affiché, ni écrit dans un fichier versionné.
#
# Ce script couvre l'environnement local. Docker et le runner CI de projet arrivent au lot 2
# (#146) ; en attendant ils restent manuels (docs/10-workflow-git.md §8).
#
# Pas de `set -e` : les étapes doivent toutes se dérouler, chacune gère son erreur.

set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${TMPDIR:-/tmp}/maestro-setup"

# --- Configuration (surchargeable par variables d'environnement) --------------------------------
PYTHON_MIN="${MAESTRO_PYTHON_MIN:-3.11}"   # exigé par pyproject.toml (requires-python)
NODE_MIN="${MAESTRO_NODE_MIN:-20}"         # exigé par le Claude Agent SDK
ETAPES_CONNUES="prerequis venv env hooks web mcp verif"

# --- Drapeaux -----------------------------------------------------------------------------------
MODE_CHECK=0                                     # --check : diagnostic seul, aucune écriture
AUTO_INSTALL="${MAESTRO_AUTO_INSTALL:-1}"        # --no-install : ne rien installer, juste signaler
ETAPES_ONLY=""                                   # --only  : étapes à exécuter (défaut : toutes)
ETAPES_SKIP=""                                   # --skip  : étapes à sauter

usage() {
  cat <<USAGE
Mise en route d'un clone Maestro (socle local).

  bash scripts/setup.sh [options]

Options :
  --check            Diagnostic seul : rapporte ce qui manque, sans rien écrire ni installer.
  --no-install       N'installe aucun outil manquant, contente-toi de le signaler.
  --only <étapes>    N'exécute que ces étapes (séparées par des virgules).
  --skip <étapes>    Saute ces étapes (séparées par des virgules).
  -h, --help         Cette aide.

Étapes : ${ETAPES_CONNUES// /, }
  prerequis  python >= ${PYTHON_MIN}, node >= ${NODE_MIN}, npm, git, glab — installés d'office
             s'ils manquent (winget / brew / apt)
  venv       .venv/ + pip install -e ".[dev]"
  env        .env créé depuis .env.example (jamais écrasé)
  hooks      hook git commit-msg (scripts/git/install-hooks.sh)
  web        dépendances npm de apps/web
  mcp        .claude/settings.local.json (profil navigateur + serveurs MCP du dépôt)
  verif      glab auth status + maestro-check-env

Le script ne pose aucune question : ce qui exige un humain est listé en fin de rapport.
Docker et le runner CI de projet ne sont pas encore couverts (lot 2, ticket #146) :
voir docs/10-workflow-git.md §8.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --check)      MODE_CHECK=1 ;;
    --no-install) AUTO_INSTALL=0 ;;
    --only)    ETAPES_ONLY="${2:-}"; shift ;;
    --skip)    ETAPES_SKIP="${2:-}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *)         printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# --- Rapport ------------------------------------------------------------------------------------
# Les étapes appellent `rapport <statut> <étape> <détail>` pour chaque constat. Statuts :
#   OK     : l'étape a fait quelque chose et ça a marché
#   DEJA   : rien à faire, c'était déjà en place (idempotence)
#   IGNORE : étape sautée (--check, --only/--skip, prérequis absent) — sans effet sur le code retour
#   ECHEC  : l'étape a échoué ; si elle est DURE (echec_dur), le script sort en code non nul
RAPPORT=()
RESTE=()
NB_ECHECS_DURS=0

rapport() {
  local statut="$1" etape="$2" detail="$3"
  RAPPORT+=("${statut}|${etape}|${detail}")
  case "$statut" in
    OK)     printf '  ✓ %s\n' "$detail" ;;
    DEJA)   printf '  = %s\n' "$detail" ;;
    IGNORE) printf '  ~ %s\n' "$detail" ;;
    ECHEC)  printf '  ✗ %s\n' "$detail" >&2 ;;
  esac
}

# Échec d'une étape DURE : rapporté et compté dans le code de sortie.
echec_dur() {
  rapport ECHEC "$1" "$2"
  NB_ECHECS_DURS=$((NB_ECHECS_DURS + 1))
}

# Action manuelle restant à la charge de l'utilisateur (auth interactive, secret à renseigner…).
reste() { RESTE+=("$1"); }

symbole() {
  case "$1" in
    OK) printf '✓' ;; DEJA) printf '=' ;; IGNORE) printf '~' ;; ECHEC) printf '✗' ;; *) printf ' ' ;;
  esac
}

# Libellé du statut, DÉJÀ complété à 9 caractères d'affichage. Le remplissage est fait ici plutôt
# que par un `%-9s` de printf, qui compte des OCTETS : « DÉJÀ FAIT » en pèse 11 pour 9 caractères,
# et la colonne partirait de travers dès qu'un accent apparaît.
libelle() {
  case "$1" in
    OK)     printf 'OK       ' ;;
    DEJA)   printf 'DÉJÀ FAIT' ;;
    IGNORE) printf 'IGNORÉ   ' ;;
    ECHEC)  printf 'ÉCHEC    ' ;;
  esac
}

# --- Utilitaires ---------------------------------------------------------------------------------
os_kind() {
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) echo windows ;;
    Darwin)               echo macos ;;
    Linux)                echo linux ;;
    *)                    echo inconnu ;;
  esac
}
OS="$(os_kind)"

# Convertit un chemin POSIX en chemin natif — les valeurs écrites dans un JSON relu par des outils
# Windows (ex. le profil du navigateur piloté par @playwright/mcp) doivent être natives.
chemin_natif() {
  if [ "$OS" = windows ] && command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
  else
    printf '%s\n' "$1"
  fi
}

# version_ok <trouvée> <minimale> : compare major.minor numériquement (donc 3.10 >= 3.9).
version_ok() {
  local trouvee="$1" minimale="$2" t_maj t_min m_maj m_min
  t_maj="${trouvee%%.*}"
  case "$trouvee" in *.*) t_min="${trouvee#*.}"; t_min="${t_min%%.*}" ;; *) t_min=0 ;; esac
  m_maj="${minimale%%.*}"
  case "$minimale" in *.*) m_min="${minimale#*.}"; m_min="${m_min%%.*}" ;; *) m_min=0 ;; esac
  t_maj="${t_maj//[!0-9]/}"; t_min="${t_min//[!0-9]/}"
  m_maj="${m_maj//[!0-9]/}"; m_min="${m_min//[!0-9]/}"
  [ -n "$t_maj" ] || return 1
  [ -n "$t_min" ] || t_min=0
  [ -n "$m_maj" ] || m_maj=0
  [ -n "$m_min" ] || m_min=0
  if [ "$t_maj" -gt "$m_maj" ]; then return 0; fi
  if [ "$t_maj" -eq "$m_maj" ] && [ "$t_min" -ge "$m_min" ]; then return 0; fi
  return 1
}

# commande_install <outil> : la commande d'installation de la plateforme courante. Le script
# installe lui-même (installe_outil) ; cette commande n'est affichée qu'en REPLI, quand il n'a pas
# pu le faire — pas de gestionnaire de paquets, élévation refusée, ou --no-install.
commande_install() {
  case "$1" in
    python)
      case "$OS" in
        windows) echo "winget install Python.Python.3.12" ;;
        macos)   echo "brew install python@3.12" ;;
        *)       echo "sudo apt install python3 python3-venv python3-pip" ;;
      esac ;;
    node|npm)
      case "$OS" in
        windows) echo "winget install OpenJS.NodeJS.LTS" ;;
        macos)   echo "brew install node" ;;
        *)       echo "voir https://github.com/nodesource/distributions (Node ${NODE_MIN}+)" ;;
      esac ;;
    git)
      case "$OS" in
        windows) echo "winget install Git.Git" ;;
        macos)   echo "brew install git" ;;
        *)       echo "sudo apt install git" ;;
      esac ;;
    glab)
      case "$OS" in
        windows) echo "winget install GLab.GLab" ;;
        macos)   echo "brew install glab" ;;
        *)       echo "voir https://gitlab.com/gitlab-org/cli#installation" ;;
      esac ;;
    *) echo "voir la documentation de $1" ;;
  esac
}

# --- Installation automatique des outils manquants ----------------------------------------------
# paquet_id <outil> : identifiant du paquet pour le gestionnaire de la plateforme. `npm` partage
# celui de `node` (npm est livré avec) — la déduplication par identifiant évite de l'installer deux
# fois. Chaîne vide = pas de paquet connu ici, on retombera sur le message de commande_install.
paquet_id() {
  case "$OS/$1" in
    windows/python) echo "Python.Python.3.12" ;;
    windows/node|windows/npm) echo "OpenJS.NodeJS.LTS" ;;
    windows/git)    echo "Git.Git" ;;
    windows/glab)   echo "GLab.GLab" ;;
    macos/python)   echo "python@3.12" ;;
    macos/node|macos/npm) echo "node" ;;
    macos/git)      echo "git" ;;
    macos/glab)     echo "glab" ;;
    linux/python)   echo "python3 python3-venv python3-pip" ;;
    linux/node|linux/npm) echo "nodejs npm" ;;
    linux/git)      echo "git" ;;
    *)              echo "" ;;   # glab n'est pas dans les dépôts apt standard
  esac
}

# installe_outil <outil> : installe sans poser de question. Codes de retour :
#   0 installé · 1 la commande d'installation a échoué · 2 pas de gestionnaire de paquets
#   3 aucun paquet connu pour cet outil sur cette plateforme
# Les prompts d'ÉLÉVATION (UAC sous Windows, mot de passe sudo) viennent du système d'exploitation
# et ne peuvent pas être supprimés depuis un script non privilégié : quand ils apparaissent ou sont
# refusés, l'installation échoue et c'est rapporté tel quel — on ne prétend pas les avoir évités.
installe_outil() {
  local outil="$1" paquet
  paquet="$(paquet_id "$outil")"
  [ -n "$paquet" ] || return 3

  case "$OS" in
    windows)
      command -v winget >/dev/null 2>&1 || return 2
      execute_journalise "install-$outil" \
        winget install --id "$paquet" --exact --silent --disable-interactivity \
                       --accept-package-agreements --accept-source-agreements \
        || return 1 ;;
    macos)
      command -v brew >/dev/null 2>&1 || return 2
      execute_journalise "install-$outil" brew install "$paquet" || return 1 ;;
    linux)
      command -v apt-get >/dev/null 2>&1 || return 2
      # sudo -n : n'installe que si l'élévation est déjà accordée (ou si on est root) — sinon le
      # script bloquerait sur une invite de mot de passe, ce qu'il ne doit jamais faire.
      if [ "$(id -u 2>/dev/null)" = 0 ]; then
        # shellcheck disable=SC2086  # $paquet porte volontairement plusieurs paquets
        execute_journalise "install-$outil" env DEBIAN_FRONTEND=noninteractive apt-get install -y $paquet || return 1
      elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        # shellcheck disable=SC2086
        execute_journalise "install-$outil" sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y $paquet || return 1
      else
        return 2
      fi ;;
    *) return 2 ;;
  esac
  return 0
}

# Rend visibles dans CETTE session les binaires qui viennent d'être installés.
# Sous Windows c'est indispensable : winget écrit dans l'environnement PERSISTANT (registre), que
# le shell déjà lancé ne relit jamais. On recompose donc le PATH depuis les valeurs Machine + User
# du registre, converties en chemins POSIX. Ailleurs, les gestionnaires installent dans des dossiers
# déjà présents dans le PATH : vider le cache de résolution de bash suffit.
rafraichir_path() {
  hash -r 2>/dev/null || true
  [ "$OS" = windows ] || return 0
  command -v powershell.exe >/dev/null 2>&1 || return 0
  command -v cygpath >/dev/null 2>&1 || return 0

  local brut chemin ajouts=""
  brut="$(powershell.exe -NoProfile -Command \
    '[Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [Environment]::GetEnvironmentVariable("PATH","User")' \
    2>/dev/null | tr -d '\r')"
  [ -n "$brut" ] || return 0

  local IFS=';'
  for chemin in $brut; do
    [ -n "$chemin" ] || continue
    chemin="$(cygpath -u "$chemin" 2>/dev/null)" || continue
    [ -n "$chemin" ] && [ -d "$chemin" ] || continue
    case ":$PATH:" in *":$chemin:"*) continue ;; esac
    ajouts="$ajouts:$chemin"
  done
  [ -n "$ajouts" ] && export PATH="$PATH$ajouts"
  hash -r 2>/dev/null || true
  return 0
}

# Exécute une commande longue en la journalisant ; imprime le chemin du log en cas d'échec.
#   execute_journalise <nom-du-log> <commande…>
execute_journalise() {
  local nom="$1"; shift
  local log="$LOG_DIR/$nom.log"
  mkdir -p "$LOG_DIR"
  if "$@" >"$log" 2>&1; then
    return 0
  fi
  printf '    (détail : %s)\n' "$log" >&2
  return 1
}

# L'interpréteur d'amorce (hors venv) : python3, python, ou le lanceur Windows `py -3`. Tableau,
# parce que `py -3` fait deux mots — et parce que le chemin du dépôt peut contenir des espaces.
PY_BOOT=()
PY_BOOT_VERSION=""
detecte_python_amorce() {
  local candidat version
  for candidat in python3 python; do
    if command -v "$candidat" >/dev/null 2>&1; then
      version="$("$candidat" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"
      if [ -n "$version" ] && version_ok "$version" "$PYTHON_MIN"; then
        PY_BOOT=("$candidat"); PY_BOOT_VERSION="$version"; return 0
      fi
    fi
  done
  if command -v py >/dev/null 2>&1; then
    version="$(py -3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"
    if [ -n "$version" ] && version_ok "$version" "$PYTHON_MIN"; then
      PY_BOOT=(py -3); PY_BOOT_VERSION="$version"; return 0
    fi
  fi
  return 1
}

# Chemin de l'interpréteur du venv du dépôt (Windows : Scripts/, sinon bin/).
python_venv() {
  if [ "$OS" = windows ]; then
    printf '%s\n' "$RACINE/.venv/Scripts/python.exe"
  else
    printf '%s\n' "$RACINE/.venv/bin/python"
  fi
}

# Renseigne PY_CMD avec le meilleur python disponible : celui du venv s'il existe, sinon l'amorce.
PY_CMD=()
detecte_python_utilisable() {
  local pv; pv="$(python_venv)"
  PY_CMD=()
  if [ -x "$pv" ]; then PY_CMD=("$pv"); return 0; fi
  if [ "${#PY_BOOT[@]}" -gt 0 ]; then PY_CMD=("${PY_BOOT[@]}"); return 0; fi
  return 1
}

# Les étapes venv/web/mcp/verif s'appuient sur des constats faits par `prerequis`. Avec
# --only/--skip cette étape peut ne pas avoir tourné : on redétecte à la demande, sinon elles
# concluent à tort qu'un outil manque (« --only mcp » rapportait « python introuvable » sur une
# machine parfaitement équipée). Idempotent et sans effet de bord.
assure_detection() {
  [ "${#PY_BOOT[@]}" -gt 0 ] || detecte_python_amorce >/dev/null 2>&1 || true
  if detecte_outil node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then NODE_PRESENT=1; fi
  if command -v glab >/dev/null 2>&1; then GLAB_PRESENT=1; fi
  return 0
}

# Étape demandée ? (respecte --only et --skip)
etape_demandee() {
  local etape="$1"
  if [ -n "$ETAPES_ONLY" ] && [[ ",${ETAPES_ONLY//[[:space:]]/}," != *",$etape,"* ]]; then return 1; fi
  if [ -n "$ETAPES_SKIP" ] && [[ ",${ETAPES_SKIP//[[:space:]]/}," == *",$etape,"* ]]; then return 1; fi
  return 0
}

# ================================================================================================
# Étapes
# ================================================================================================

# --- 1. Prérequis : détecter, installer ce qui manque, re-détecter --------------------------------
# `python` et `git` sont DURS (sans eux les étapes suivantes n'ont rien à faire) ; `node`/`npm` ne
# bloquent que apps/web, et `glab` que le workflow de tickets.
OUTILS_REQUIS="python node npm git glab"
PREREQUIS_DURS_OK=1
NB_OUTILS_MANQUANTS=0
NODE_PRESENT=0
GLAB_PRESENT=0

outil_est_dur() { case "$1" in python|git) return 0 ;; *) return 1 ;; esac; }

# Version minimale exigée pour un outil (vide s'il n'y a pas de plancher).
minimum_pour() {
  case "$1" in python) printf '%s' "$PYTHON_MIN" ;; node) printf '%s' "$NODE_MIN" ;; *) printf '—' ;; esac
}

# detecte_outil <outil> : imprime la version trouvée et renvoie
#   0 = présent et à la version minimale · 1 = absent · 3 = présent mais TROP ANCIEN.
# La distinction compte : « absent » et « trop ancien » n'appellent ni le même diagnostic ni le
# même remède, et les confondre produit des messages faux (cf. Node 18 des dépôts Debian stable,
# installé sans erreur mais sous le minimum exigé).
detecte_outil() {
  local version
  case "$1" in
    python)
      if detecte_python_amorce; then printf '%s (%s)\n' "$PY_BOOT_VERSION" "${PY_BOOT[*]}"; return 0; fi
      command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1 || return 1
      version="$( { python3 -V || python -V; } 2>&1 | cut -d' ' -f2)"
      printf '%s\n' "${version:-inconnue}"
      return 3 ;;
    node)
      command -v node >/dev/null 2>&1 || return 1
      version="$(node -v 2>/dev/null)"; version="${version#v}"
      printf '%s\n' "$version"
      version_ok "$version" "$NODE_MIN" || return 3 ;;
    npm)
      command -v npm >/dev/null 2>&1 || return 1
      npm -v 2>/dev/null ;;
    git)
      command -v git >/dev/null 2>&1 || return 1
      git --version 2>/dev/null | cut -d' ' -f3 ;;
    glab)
      command -v glab >/dev/null 2>&1 || return 1
      glab --version 2>/dev/null | head -1 | cut -d' ' -f2 ;;
    *) return 1 ;;
  esac
}

etape_prerequis() {
  local outil version paquet code detail=""
  local manquants=() installes=()
  local paquets_traites=" "
  declare -A raison=()   # outil -> pourquoi il manque encore, si on n'a pas su le poser

  # 1) État initial. « Trop ancien » compte comme manquant : on tente la mise à niveau par la même
  # voie que l'installation.
  for outil in $OUTILS_REQUIS; do
    version="$(detecte_outil "$outil")"; code=$?
    case "$code" in
      0) printf '  ✓ %s %s\n' "$outil" "$version" ;;
      3) printf '  ~ %s %s présent, sous le minimum %s — mise à niveau tentée\n' \
           "$outil" "$version" "$(minimum_pour "$outil")"
         manquants+=("$outil") ;;
      *) manquants+=("$outil") ;;
    esac
  done

  # 2) Installation d'office de ce qui manque (sauf --check / --no-install).
  for outil in "${manquants[@]}"; do
    if [ "$MODE_CHECK" = 1 ]; then
      raison["$outil"]="absent — serait installé automatiquement (--check : rien fait)"
      continue
    fi
    if [ "$AUTO_INSTALL" != 1 ]; then
      raison["$outil"]="installation désactivée (--no-install) → $(commande_install "$outil")"
      continue
    fi
    # npm est livré avec node : un seul paquet pour les deux, on ne l'installe pas deux fois.
    paquet="$(paquet_id "$outil")"
    if [ -n "$paquet" ] && [ "$paquets_traites" != "${paquets_traites/ $paquet /}" ]; then
      continue
    fi
    printf '  … installation de %s — peut prendre plusieurs minutes\n' "$outil"
    installe_outil "$outil"; code=$?
    [ -n "$paquet" ] && paquets_traites="$paquets_traites$paquet "
    case "$code" in
      0) installes+=("$outil") ;;
      2) raison["$outil"]="pas de gestionnaire de paquets utilisable ici → $(commande_install "$outil")" ;;
      3) raison["$outil"]="aucun paquet connu pour cette plateforme → $(commande_install "$outil")" ;;
      *) raison["$outil"]="l'installation a échoué → $(commande_install "$outil")" ;;
    esac
  done
  # Les binaires fraîchement installés ne sont pas encore visibles de ce shell : on recharge.
  [ "${#installes[@]}" -gt 0 ] && rafraichir_path

  # 3) État final — c'est lui qui fait foi, pas le fait d'avoir lancé une installation.
  manquants=()
  for outil in $OUTILS_REQUIS; do
    version="$(detecte_outil "$outil")"; code=$?
    if [ "$code" = 0 ]; then
      case " ${installes[*]-} " in
        *" $outil "*) printf '  ✓ %s %s (installé à l'\''instant)\n' "$outil" "$version" ;;
      esac
      case "$outil" in node) NODE_PRESENT=1 ;; glab) GLAB_PRESENT=1 ;; esac
      continue
    fi
    manquants+=("$outil")
    outil_est_dur "$outil" && PREREQUIS_DURS_OK=0
    if [ "$code" = 3 ]; then
      # Présent mais sous le minimum : le dire tel quel. Le gestionnaire de paquets de la
      # plateforme ne propose pas toujours mieux (Debian stable plafonne Node à 18) — c'est une
      # limite de la distribution, pas un ratage du script, et ça se règle par une autre source.
      raison["$outil"]="version $version trouvée, minimum requis $(minimum_pour "$outil") → $(commande_install "$outil")"
    else
      # Installé à l'instant mais toujours introuvable : le PATH du terminal courant est en retard.
      case " ${installes[*]-} " in
        *" $outil "*) raison["$outil"]="installé, mais pas encore dans le PATH — rouvre le terminal et relance" ;;
      esac
    fi
    if [ "$MODE_CHECK" = 1 ]; then
      printf '  ~ %s : %s\n' "$outil" "${raison[$outil]:-absent}"
    else
      printf '  ✗ %s : %s\n' "$outil" "${raison[$outil]:-absent}" >&2
    fi
    reste "$outil — ${raison[$outil]:-à installer : $(commande_install "$outil")}"
  done
  # npm suit node : sans lui, apps/web ne peut rien installer.
  command -v npm >/dev/null 2>&1 || NODE_PRESENT=0

  # detecte_outil est appelée en SUBSTITUTION DE COMMANDE, donc dans un sous-shell : le PY_BOOT
  # qu'elle renseigne meurt avec lui. On re-détecte ici, dans le shell courant, sinon les étapes
  # venv et mcp se croient sans interpréteur — le script annonçait « prérequis : tous présents »
  # puis sautait le venv pour « python introuvable », et se déclarait prêt malgré tout.
  detecte_python_amorce || true

  # 4) Rapport.
  NB_OUTILS_MANQUANTS="${#manquants[@]}"
  [ "${#installes[@]}" -gt 0 ] && detail=" — installé(s) : ${installes[*]}"
  if [ "${#manquants[@]}" -eq 0 ]; then
    rapport OK prerequis "prérequis : tous présents${detail}"
  elif [ "$PREREQUIS_DURS_OK" = 0 ]; then
    echec_dur prerequis "prérequis : ${manquants[*]} toujours absent(s)${detail}"
  else
    rapport IGNORE prerequis "prérequis : ${manquants[*]} absent(s), non bloquants${detail}"
  fi
}

# --- 2. venv : .venv/ + installation éditable du paquet et de son extra `dev` --------------------
# Idempotence : un témoin .venv/.maestro-setup-stamp est posé après installation ; on ne réinstalle
# que s'il manque ou si pyproject.toml a bougé depuis (dépendances modifiées).
etape_venv() {
  local pv temoin a_creer=0 a_installer=0
  pv="$(python_venv)"
  temoin="$RACINE/.venv/.maestro-setup-stamp"

  if [ "${#PY_BOOT[@]}" -eq 0 ] && [ ! -x "$pv" ]; then
    rapport IGNORE venv "venv : python introuvable, étape sautée"
    return 0
  fi

  [ -x "$pv" ] || a_creer=1
  if [ ! -f "$temoin" ] || [ "$RACINE/pyproject.toml" -nt "$temoin" ]; then a_installer=1; fi

  if [ "$a_creer" = 0 ] && [ "$a_installer" = 0 ]; then
    rapport DEJA venv "venv : .venv/ à jour (pyproject.toml inchangé depuis l'installation)"
    return 0
  fi

  if [ "$MODE_CHECK" = 1 ]; then
    if [ "$a_creer" = 1 ]; then
      rapport IGNORE venv 'venv : .venv/ à créer puis pip install -e ".[dev]" (--check : rien écrit)'
    elif [ ! -f "$temoin" ]; then
      rapport IGNORE venv "venv : .venv/ présent mais jamais installé par ce script (--check : rien écrit)"
    else
      rapport IGNORE venv "venv : dépendances à réinstaller, pyproject.toml a changé (--check : rien écrit)"
    fi
    return 0
  fi

  if [ "$a_creer" = 1 ]; then
    printf '  … création du venv (.venv/)\n'
    if ! execute_journalise venv-create "${PY_BOOT[@]}" -m venv "$RACINE/.venv"; then
      echec_dur venv "venv : création de .venv/ impossible"
      return 0
    fi
    execute_journalise venv-pip "$pv" -m pip install --upgrade pip || true
  fi

  printf '  … installation des dépendances (pip install -e ".[dev]") — peut prendre une minute\n'
  if ! ( cd "$RACINE" && execute_journalise venv-install "$pv" -m pip install -e ".[dev]" ); then
    echec_dur venv 'venv : pip install -e ".[dev]" a échoué'
    return 0
  fi
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "$temoin" 2>/dev/null || : > "$temoin"
  rapport OK venv "venv : .venv/ prêt (paquet éditable + extra dev installés)"
}

# --- 3. .env : copie du gabarit, JAMAIS d'écrasement ---------------------------------------------
# Si le .env existe déjà, on se contente de signaler les clés présentes dans le gabarit et absentes
# du .env (dérive de gabarit). Aucune VALEUR n'est lue ni affichée — uniquement des noms de clés.
etape_env() {
  local gabarit="$RACINE/.env.example" cible="$RACINE/.env" cles_gabarit cles_env absentes

  if [ ! -f "$gabarit" ]; then
    echec_dur env "env : .env.example introuvable"
    return 0
  fi

  if [ ! -f "$cible" ]; then
    if [ "$MODE_CHECK" = 1 ]; then
      rapport IGNORE env "env : .env à créer depuis .env.example (--check : rien écrit)"
    elif cp "$gabarit" "$cible"; then
      rapport OK env ".env créé depuis .env.example — à renseigner"
    else
      echec_dur env "env : copie de .env.example vers .env impossible"
      return 0
    fi
    reste "Renseigner .env — mode d'authentification Claude (CLAUDE_AUTH_MODE : subscription ou api_key), cf. docs/07-guide-de-demarrage.md §2.1"
    return 0
  fi

  cles_gabarit="$(grep -oE '^[A-Z][A-Z0-9_]*=' "$gabarit" 2>/dev/null | tr -d '=' | sort -u)"
  cles_env="$(grep -oE '^[A-Z][A-Z0-9_]*=' "$cible" 2>/dev/null | tr -d '=' | sort -u)"
  absentes="$(comm -23 \
      <(printf '%s\n' "$cles_gabarit" | grep -v '^$') \
      <(printf '%s\n' "$cles_env" | grep -v '^$') \
    | tr '\n' ' ')"
  absentes="${absentes% }"

  if [ -n "$absentes" ]; then
    rapport DEJA env ".env présent (préservé) — clés du gabarit absentes : $absentes"
    reste "Compléter .env avec les clés apparues depuis : $absentes"
  else
    rapport DEJA env ".env présent (préservé) et aligné sur le gabarit"
  fi
}

# --- 4. Hooks git : délégation au script dédié (déjà idempotent) ---------------------------------
etape_hooks() {
  local attendu="scripts/git/hooks" actuel
  actuel="$(git -C "$RACINE" config core.hooksPath 2>/dev/null)"

  if [ "$actuel" = "$attendu" ]; then
    rapport DEJA hooks "hooks git : commit-msg déjà actif (core.hooksPath = $attendu)"
    return 0
  fi
  if [ "$MODE_CHECK" = 1 ]; then
    rapport IGNORE hooks "hooks git : à activer via scripts/git/install-hooks.sh (--check : rien écrit)"
    return 0
  fi
  if execute_journalise hooks bash "$RACINE/scripts/git/install-hooks.sh"; then
    rapport OK hooks "hooks git : commit-msg activé (core.hooksPath = $attendu)"
  else
    echec_dur hooks "hooks git : install-hooks.sh a échoué"
  fi
}

# --- 5. apps/web : dépendances npm de la Control Tower -------------------------------------------
# Réinstalle si node_modules/ est absent ou plus ancien que le lockfile.
etape_web() {
  local web="$RACINE/apps/web" lock ok=1

  if [ ! -f "$web/package.json" ]; then
    rapport IGNORE web "apps/web : pas de package.json, étape sautée"
    return 0
  fi
  if [ "$NODE_PRESENT" = 0 ]; then
    rapport IGNORE web "apps/web : node/npm absent, étape sautée"
    return 0
  fi

  lock="$web/package-lock.json"
  if [ -d "$web/node_modules" ] && { [ ! -f "$lock" ] || [ ! "$lock" -nt "$web/node_modules" ]; }; then
    rapport DEJA web "apps/web : dépendances npm à jour"
    return 0
  fi
  if [ "$MODE_CHECK" = 1 ]; then
    rapport IGNORE web "apps/web : dépendances npm à installer (--check : rien écrit)"
    return 0
  fi

  printf '  … installation des dépendances npm de apps/web — peut prendre une minute\n'
  if [ -f "$lock" ]; then
    ( cd "$web" && execute_journalise web-npm-ci npm ci ) || ok=0
  else
    ok=0
  fi
  if [ "$ok" = 0 ]; then
    # Repli : pas de lockfile, ou `npm ci` refusé (lockfile désynchronisé du package.json).
    if ! ( cd "$web" && execute_journalise web-npm-install npm install ); then
      echec_dur web "apps/web : installation npm impossible"
      return 0
    fi
  fi
  rapport OK web "apps/web : dépendances npm installées"
}

# --- 6. Claude Code : .claude/settings.local.json (profil navigateur + serveurs MCP) -------------
# Fusion clé par clé : ce qui est déjà posé n'est jamais remplacé. Le fichier n'est pas versionné
# (.gitignore) et ne porte que des chemins machine — aucun secret. L'approbation effective des
# serveurs MCP et l'OAuth Figma restent des gestes interactifs, rappelés dans « Reste à faire ».
etape_mcp() {
  local cible="$RACINE/.claude/settings.local.json" profil sortie etat detail

  if ! detecte_python_utilisable; then
    rapport IGNORE mcp "Claude Code : python introuvable, fusion de settings.local.json sautée"
    return 0
  fi

  profil="$(chemin_natif "${MAESTRO_CHROME_PROFILE:-$HOME/.maestro/chrome-profile}")"

  # La fusion est en Python (c'est du JSON) mais lit/écrit explicitement en UTF-8 : elle ne dépend
  # pas de l'encodage du terminal — le piège Windows cp1252 rappelé dans CLAUDE.md ne s'applique
  # qu'aux pipes vers stdin, ici tout passe par des fichiers et argv.
  # PYTHONIOENCODING est posé sur PYTHON lui-même (pas devant un `glab | python` : le shell ne le
  # propagerait pas au bout du pipeline) — sans lui, Windows encode stdout en cp1252 et le rapport
  # ressort en mojibake (« d?j? align? »). Cf. CLAUDE.md § Outillage requis.
  sortie="$(PYTHONIOENCODING=utf-8 "${PY_CMD[@]}" - \
      "$cible" "$RACINE/.mcp.json" "$profil" "$MODE_CHECK" "$RACINE/.env" <<'PY' 2>&1
import json, os, sys

cible, mcp_json, profil, mode_check = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "1"
chemin_env = sys.argv[5]

# Variables PILOTÉES PAR LE .env : Claude Code ne lit pas ce fichier (cf. .env.example), donc les
# valeurs dont ses serveurs MCP ont besoin sont recopiées ici. Ce sont des valeurs DÉRIVÉES : une
# rotation dans le .env doit se propager, sinon un token renouvelé ne prendrait jamais effet.
# C'est la seule exception à la règle « ne jamais écraser une clé déjà posée » — et elle ne vaut
# que pour ces noms-là, quand le .env porte une valeur non vide. Toute autre clé du bloc `env`,
# ou celles-ci quand le .env est vide, restent intactes.
PILOTEES_PAR_ENV = ("MAESTRO_CHROME_PROFILE", "CLAUDE_CODE_OAUTH_TOKEN")

def lire_env(chemin):
    """Extrait CLE=valeur d'un .env, sans l'exécuter (un `source` exécuterait du code arbitraire)."""
    valeurs = {}
    if not os.path.exists(chemin):
        return valeurs
    try:
        with open(chemin, encoding="utf-8") as f:
            for ligne in f:
                ligne = ligne.strip()
                if not ligne or ligne.startswith("#") or "=" not in ligne:
                    continue
                cle, _, valeur = ligne.partition("=")
                cle = cle.strip()
                valeur = valeur.strip().strip('"').strip("'")
                if cle and valeur:
                    valeurs[cle] = valeur
    except OSError:
        pass
    return valeurs

depuis_env = lire_env(chemin_env)

reglages = {}
if os.path.exists(cible):
    try:
        with open(cible, encoding="utf-8") as f:
            reglages = json.load(f) or {}
    except (OSError, ValueError) as exc:
        print("ERREUR|%s illisible (%s) — laissé intact" % (cible, exc))
        raise SystemExit(0)

changements = []

env = reglages.setdefault("env", {})
if not isinstance(env, dict):
    print("ERREUR|la clé 'env' n'est pas un objet — laissé intact")
    raise SystemExit(0)

# Synchronisation depuis le .env. On ne compare et n'annonce que des NOMS de clés : aucune valeur
# n'est imprimée, un secret ne doit jamais transiter par la sortie du script.
for cle in PILOTEES_PAR_ENV:
    voulue = depuis_env.get(cle)
    if voulue is None:
        continue
    if env.get(cle) != voulue:
        env[cle] = voulue
        changements.append(cle + " (depuis .env)")

# Profil du navigateur : à défaut de valeur dans le .env, on pose le défaut de la machine.
if "MAESTRO_CHROME_PROFILE" not in env:
    env["MAESTRO_CHROME_PROFILE"] = profil
    changements.append("MAESTRO_CHROME_PROFILE")

serveurs = []
if os.path.exists(mcp_json):
    try:
        with open(mcp_json, encoding="utf-8") as f:
            serveurs = sorted((json.load(f) or {}).get("mcpServers", {}))
    except (OSError, ValueError):
        serveurs = []

actifs = reglages.setdefault("enabledMcpjsonServers", [])
if not isinstance(actifs, list):
    print("ERREUR|la clé 'enabledMcpjsonServers' n'est pas une liste — laissé intact")
    raise SystemExit(0)
for nom in serveurs:
    if nom not in actifs:
        actifs.append(nom)
        changements.append(nom)

if not changements:
    print("INCHANGE|déjà aligné (profil navigateur + %d serveur(s) MCP)" % len(serveurs))
elif mode_check:
    print("A_FAIRE|" + ", ".join(changements))
else:
    os.makedirs(os.path.dirname(cible) or ".", exist_ok=True)
    with open(cible, "w", encoding="utf-8") as f:
        json.dump(reglages, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("MODIFIE|" + ", ".join(changements))
PY
  )"

  etat="${sortie%%|*}"
  detail="${sortie#*|}"
  case "$etat" in
    INCHANGE) rapport DEJA mcp "Claude Code : settings.local.json $detail" ;;
    A_FAIRE)  rapport IGNORE mcp "Claude Code : settings.local.json à compléter ($detail) (--check : rien écrit)" ;;
    MODIFIE)  rapport OK mcp "Claude Code : settings.local.json complété ($detail)" ;;
    *)        echec_dur mcp "Claude Code : fusion de settings.local.json impossible — ${sortie:-sortie vide}" ;;
  esac

  # Pas de « approuver les serveurs MCP » ici : `enabledMcpjsonServers`, que l'on vient d'écrire,
  # EST le registre d'approbation de Claude Code. L'annoncer comme un geste manuel serait faux.
  #
  # Figma reste en revanche une authentification interactive, et c'est délibéré : le serveur
  # `figma-officiel` de .mcp.json est en OAuth, un clic dans le navigateur mis en cache ensuite.
  # Le remplacer par `Authorization: Bearer ${FIGMA_OAUTH_TOKEN}` rendrait la mise en route PLUS
  # lourde — ce token s'obtient « via un client approuvé par Figma » (docs/20 §287) — et casserait
  # le serveur pour qui n'en a pas, un Bearer vide échouant là où l'OAuth fonctionne. Le
  # FIGMA_OAUTH_TOKEN du .env sert la couche produit (core/mcp/designer.json), où aucun humain
  # n'est là pour cliquer.
  reste "Figma : s'authentifier via /mcp dans une session Claude Code interactive (OAuth, un clic, par personne)"
}

# --- Authentification GitLab sans geste manuel ----------------------------------------------------
# valeur_env <clé> : lit une valeur du .env SANS le sourcer (un `source` exécuterait le fichier).
# La valeur n'est jamais imprimée par l'appelant — elle ne sert qu'à alimenter un stdin.
valeur_env() {
  local cle="$1" ligne
  [ -f "$RACINE/.env" ] || return 1
  ligne="$(grep -m1 -E "^[[:space:]]*${cle}=" "$RACINE/.env" 2>/dev/null)" || return 1
  ligne="${ligne#*=}"
  ligne="${ligne%\"}"; ligne="${ligne#\"}"
  ligne="${ligne%\'}"; ligne="${ligne#\'}"
  [ -n "$ligne" ] || return 1
  printf '%s' "$ligne"
}

# Hôte GitLab déduit du remote `origin` (défaut gitlab.com) — pas de valeur codée en dur, le dépôt
# peut vivre sur une instance auto-hébergée.
hote_gitlab() {
  local url
  url="$(git -C "$RACINE" remote get-url origin 2>/dev/null)" || { echo "gitlab.com"; return 0; }
  case "$url" in
    *://*) url="${url#*://}"; url="${url#*@}"; printf '%s\n' "${url%%/*}" ;;
    *@*:*) url="${url#*@}"; printf '%s\n' "${url%%:*}" ;;
    *)     echo "gitlab.com" ;;
  esac
}

# Authentifie glab à partir de GITLAB_TOKEN du .env. Le token passe par STDIN, jamais par argv :
# une ligne de commande est lisible par tout processus de la machine (ps/Gestionnaire des tâches).
# Renvoie 0 si glab est authentifié à la sortie.
authentifie_glab() {
  local token hote
  token="$(valeur_env GITLAB_TOKEN)" || return 1
  hote="$(hote_gitlab)"
  printf '  … authentification glab depuis GITLAB_TOKEN (%s)\n' "$hote"
  # La sortie est jetée : glab y réaffiche parfois des éléments de configuration.
  printf '%s' "$token" | glab auth login --hostname "$hote" --stdin >/dev/null 2>&1 || return 1
  glab auth status >/dev/null 2>&1
}

# --- 7. Vérification finale ----------------------------------------------------------------------
# Étape SOUPLE : sur un clone frais le .env n'est pas encore renseigné, un échec ici est un
# renseignement, pas une faute — il ne fait pas sortir le script en erreur.
etape_verif() {
  local pv; pv="$(python_venv)"

  if [ "$GLAB_PRESENT" = 1 ]; then
    if glab auth status >/dev/null 2>&1; then
      rapport OK verif "glab authentifié"
    elif [ "$MODE_CHECK" = 1 ] && valeur_env GITLAB_TOKEN >/dev/null 2>&1; then
      # --check n'écrit rien, pas même une configuration glab.
      rapport IGNORE verif "glab : s'authentifierait depuis GITLAB_TOKEN du .env (--check : rien fait)"
    elif authentifie_glab; then
      rapport OK verif "glab authentifié depuis GITLAB_TOKEN du .env"
    else
      rapport IGNORE verif "glab installé mais non authentifié"
      reste "S'authentifier auprès de GitLab : glab auth login (ou renseigner GITLAB_TOKEN dans le .env)"
    fi
  else
    rapport IGNORE verif "glab absent — workflow de tickets indisponible"
  fi

  if [ ! -x "$pv" ]; then
    rapport IGNORE verif "maestro-check-env : venv absent, vérification sautée"
    return 0
  fi
  if ( cd "$RACINE" && "$pv" -m maestro.check_env >/dev/null 2>&1 ); then
    rapport OK verif "maestro-check-env : environnement prêt"
  else
    rapport IGNORE verif "maestro-check-env : pas encore vert"
    reste "Rejouer la vérification une fois le .env renseigné : \"$pv\" -m maestro.check_env"
  fi
}

# ================================================================================================
# Déroulé
# ================================================================================================
printf '\nMise en route de Maestro — %s\n' "$RACINE"
if [ "$MODE_CHECK" = 1 ]; then
  printf 'Mode --check : diagnostic seul, aucun fichier ne sera écrit.\n'
fi
printf '\n'

for etape in $ETAPES_CONNUES; do
  if ! etape_demandee "$etape"; then
    rapport IGNORE "$etape" "$etape : sautée (--only/--skip)"
    continue
  fi
  printf '[%s]\n' "$etape"
  [ "$etape" = prerequis ] || assure_detection
  "etape_$etape"
  printf '\n'
done

# --- Rapport final -------------------------------------------------------------------------------
printf 'Rapport\n'
printf -- '-------\n'
if [ "${#RAPPORT[@]}" -gt 0 ]; then
  for ligne in "${RAPPORT[@]}"; do
    statut="${ligne%%|*}"
    suite="${ligne#*|}"
    etape="${suite%%|*}"
    detail="${suite#*|}"
    printf '  %s %-10s %s  %s\n' "$(symbole "$statut")" "$etape" "$(libelle "$statut")" "$detail"
  done
else
  printf '  (aucune étape exécutée)\n'
fi

if [ "${#RESTE[@]}" -gt 0 ]; then
  printf '\nReste à faire (gestes manuels — authentifications, secrets)\n'
  printf -- '-----------------------------------------------------------\n'
  for item in "${RESTE[@]}"; do
    printf '  • %s\n' "$item"
  done
fi

printf '\nNon couvert par ce script (lot 2, ticket #146) : Docker et le runner CI de projet —\n'
printf 'voir docs/10-workflow-git.md §8 en attendant.\n'

if [ "$NB_ECHECS_DURS" -gt 0 ]; then
  printf '\n%d étape(s) en échec. Corriger puis relancer : bash scripts/setup.sh\n' "$NB_ECHECS_DURS" >&2
  exit 1
fi

if [ "$MODE_CHECK" = 1 ]; then
  printf '\nDiagnostic terminé. Pour appliquer : bash scripts/setup.sh\n'
elif [ "$NB_OUTILS_MANQUANTS" -gt 0 ]; then
  # Aucune étape dure n'a échoué, mais tout n'est pas là : ne pas annoncer une machine prête.
  printf '\nEnvironnement local monté, mais %d outil(s) manque(nt) encore — voir « Reste à faire »\n' \
    "$NB_OUTILS_MANQUANTS"
  printf 'ci-dessus. Les étapes qui en dépendent ont été sautées.\n'
else
  printf '\nEnvironnement local prêt. Lancer la Control Tower : bash scripts/controltower/start.sh\n'
fi
exit 0
