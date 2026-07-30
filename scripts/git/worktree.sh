#!/usr/bin/env bash
# Un worktree git par ticket — pour traiter deux tickets dans deux sessions Claude Code (#152).
#
# Le dépôt n'a qu'un seul répertoire de travail : deux sessions ouvertes sur le même dossier
# partagent le même HEAD, et la branche créée par l'une change les fichiers sous les pieds de
# l'autre. `git worktree` donne un second répertoire de travail sur LE MÊME dépôt (objets, refs,
# remotes et hooks partagés) avec sa propre branche empruntée — pas de second clone.
#
#   bash scripts/git/worktree.sh 152             # crée (ou complète) le worktree du ticket #152
#   bash scripts/git/worktree.sh list            # les worktrees en place, avec leurs ports
#   bash scripts/git/worktree.sh remove 152      # retire le worktree (jamais la branche)
#
# Ce que la création met en place, au-delà du `git worktree add` :
#   - la branche du ticket, résolue comme /ticket-start (lib.sh branch-for), depuis `origin/main` ;
#   - le `.env` du clone principal, recopié (il est gitignoré, donc absent du worktree) ;
#   - les artefacts lourds PARTAGÉS par lien : .venv/ et .tools/ ;
#   - les dépendances de apps/web, INSTALLÉES sur place (Turbopack rejette un node_modules lié) ;
#   - un `.claude/settings.local.json` dédié : profil de navigateur et ports Control Tower
#     PROPRES à ce worktree, sans quoi les deux sessions se disputent le verrou du profil Chrome
#     et s'arrêtent mutuellement la Control Tower.
#
# Ports dérivés du numéro de ticket (déterministes, donc stables d'une session à l'autre) :
# API 8000+<iid mod 100>, UI 3000+<iid mod 100> — le clone principal gardant 8000/3000.
#
# ⚠ RETIRER UN WORKTREE PASSE PAR `remove` — jamais par un `rm -rf` ni un `git worktree remove`
# lancé à la main : les artefacts partagés sont des JONCTIONS, qu'une suppression récursive
# traverse. Elle viderait alors le .venv et le node_modules du CLONE PRINCIPAL. `remove` délie
# d'abord, puis retire.
#
# Ce script ne supprime jamais une branche : c'est le rôle de /branch-cleanup, après confirmation
# du merge par GitLab (docs/10-workflow-git.md §6).

set -uo pipefail

ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "$(uname -s 2>/dev/null)" in
  MINGW* | MSYS* | CYGWIN*) WINDOWS=1 ;;
  *) WINDOWS=0 ;;
esac

# --- Rapport --------------------------------------------------------------------------------------
ok()     { printf '  ✓ %s\n' "$*"; }
deja()   { printf '  = %s\n' "$*"; }
ignore() { printf '  ~ %s\n' "$*"; }
erreur() { printf '  ✗ %s\n' "$*" >&2; }

usage() {
  cat <<'USAGE'
Un worktree git par ticket — deux tickets, deux sessions, un seul dépôt.

  bash scripts/git/worktree.sh [create] <iid> [options]
  bash scripts/git/worktree.sh ensure <iid> [--branche <nom>]
  bash scripts/git/worktree.sh list
  bash scripts/git/worktree.sh remove <iid|chemin> [--force]

`ensure` est l'aiguillage de /ticket-start : il dit où la session doit travailler, en rendant
en dernière ligne « ICI <chemin> » (le répertoire courant convient déjà — cas d'orchestrate,
qui monte le worktree lui-même) ou « WORKTREE <chemin> » (worktree prêt, s'y relocaliser).

Options de création :
  --branche <nom>   Nom de branche imposé (par défaut : résolu depuis le ticket via glab).
  --ports <api:ui>  Ports Control Tower imposés (par défaut : dérivés de l'iid).
  --sans-liens      N'installe aucun lien vers .venv/.tools/node_modules — le worktree est
                    autonome, à équiper avec `bash scripts/setup.sh`.
  -h, --help        Cette aide.

Emplacement : <parent-du-dépôt>/maestro-worktrees/<iid>-<slug>, surchargeable par
MAESTRO_WORKTREE_DIR (le dossier qui accueille les worktrees).
USAGE
}

# --- Repères du dépôt ------------------------------------------------------------------------------
# Racine du clone PRINCIPAL, quel que soit l'endroit d'où l'on appelle (worktree lié compris) :
# le répertoire git commun est partagé par tous les worktrees, son parent est le clone principal.
depot_principal() {
  local commun
  commun="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
  if [ -z "$commun" ]; then
    commun="$(git rev-parse --git-common-dir 2>/dev/null)" || return 1
    commun="$(cd "$commun" 2>/dev/null && pwd)" || return 1
  fi
  [ -n "$commun" ] || return 1
  dirname "$commun"
}

# worktree_de_branche <branche> : chemin du worktree qui a CETTE branche empruntée, s'il existe.
#
# git refuse la même branche dans deux worktrees — c'est un verrou d'exclusion mutuelle utile (deux
# sessions sur le même ticket deviennent impossibles), mais son message brut ne dit pas OÙ elle est
# prise, ce qui laisse l'appelant sans recours. On le lui dit.
worktree_de_branche() {
  local branche="$1" courant="" ligne
  while IFS= read -r ligne; do
    case "$ligne" in
      worktree\ *) courant="${ligne#worktree }" ;;
      "branch refs/heads/$branche") printf '%s' "$courant"; return 0 ;;
    esac
  done < <(git worktree list --porcelain 2>/dev/null)
  return 1
}

# Chemin natif (Windows) — ce qui part dans un JSON relu par des outils Windows doit être natif.
chemin_natif() {
  if [ "$WINDOWS" = 1 ] && command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
  else
    printf '%s\n' "$1"
  fi
}

# --- Ports & profil --------------------------------------------------------------------------------
# Décalage déterministe tiré de l'iid. Un multiple de 100 retomberait sur 8000/3000, les ports du
# clone principal : on le renvoie alors sur 100.
decalage_ports() {
  local iid="$1" decalage
  decalage=$((iid % 100))
  [ "$decalage" -eq 0 ] && decalage=100
  printf '%s' "$decalage"
}

# --- Liens vers les artefacts lourds ---------------------------------------------------------------
# .venv/, .tools/ et node_modules/ pèsent lourd et ne dépendent pas de la branche : on les PARTAGE
# avec le clone principal plutôt que de les réinstaller. Sous Windows, une JONCTION (mklink /J) —
# contrairement à un lien symbolique, elle ne demande aucun droit administrateur.
#
# Le venv partagé porte `maestro` en mode éditable POINTÉ SUR LE CLONE PRINCIPAL. Le finder
# correspondant passe APRÈS le PathFinder de Python, donc le `maestro/` d'ici l'emporte — mais
# seulement si la racine du worktree est dans `sys.path`, et c'est LE LANCEUR qui l'y met, pas le
# répertoire d'où l'on lance : `python -m …` ajoute le répertoire courant, un point d'entrée console
# (`maestro-run`, `pytest`…) ajoute le dossier du script. Lancé par son script console depuis un
# worktree, pytest testait ainsi le code du clone principal (#194) — voir docs/10 §9.
# Règle : depuis un worktree, TOUJOURS `python -m`.
lier() {
  local src="$1" dst="$2" src_w dst_w
  [ -e "$src" ] || return 2      # rien à partager
  [ -e "$dst" ] && return 3      # déjà en place
  mkdir -p "$(dirname "$dst")" 2>/dev/null || return 1
  if [ "$WINDOWS" = 1 ]; then
    command -v cygpath >/dev/null 2>&1 || return 1
    src_w="$(cygpath -w "$src")"
    dst_w="$(cygpath -w "$dst")"
    # Arguments SÉPARÉS, et `//J` plutôt que `/J` : Git Bash convertit `//J` en `/J` et laisse
    # les chemins natifs tranquilles. Passer toute la commande en une seule chaîne à `cmd //c`
    # échoue dès que les chemins portent une espace (« Projects Solutions »).
    cmd //c mklink //J "$dst_w" "$src_w" >/dev/null 2>&1 || return 1
  else
    ln -s "$src" "$dst" || return 1
  fi
  return 0
}

# delier <chemin> : retire un lien posé par `lier`, SANS TOUCHER À SA CIBLE. Codes : 0 délié,
# 2 ce n'est pas un lien (on n'y touche pas), 1 échec.
#
# Indispensable avant tout retrait de worktree : une suppression récursive (`git worktree remove`
# comme un `rm -rf`) DESCEND DANS LA JONCTION et détruit le contenu visé — c'est-à-dire le `.venv`
# et le `node_modules` du clone principal. Sous Windows, `rmdir` sans `/S` retire la jonction seule ;
# et Git Bash présentant les jonctions comme des liens symboliques, `[ -L ]` sert de test partout.
delier() {
  local chemin="$1"
  [ -L "$chemin" ] || return 2
  if [ "$WINDOWS" = 1 ]; then
    command -v cygpath >/dev/null 2>&1 || return 1
    cmd //c rmdir "$(cygpath -w "$chemin")" >/dev/null 2>&1 || return 1
  else
    rm -f "$chemin" || return 1
  fi
  return 0
}

# --- Dépendances de l'UI ---------------------------------------------------------------------------
# Seul artefact lourd qui ne se partage pas (voir l'étape 5) : il s'installe, et l'installation est
# déléguée à scripts/setup.sh — source unique du parcours de mise en route (CLAUDE.md), qui sait
# déjà choisir entre `npm ci` et `npm install` et ne refait rien si tout est à jour.
installe_web() {
  local worktree="$1" setup="$1/scripts/setup.sh"

  if [ ! -f "$setup" ]; then
    ignore "apps/web : scripts/setup.sh introuvable dans le worktree — dépendances non installées"
    return 0
  fi
  if [ -d "$worktree/apps/web/node_modules" ] && [ ! -L "$worktree/apps/web/node_modules" ]; then
    deja "apps/web : dépendances déjà installées"
    return 0
  fi
  # Une jonction héritée d'une version antérieure du script : elle empêcherait l'installation.
  delier "$worktree/apps/web/node_modules"

  printf '  … installation des dépendances de apps/web (Turbopack refuse un node_modules lié)\n'
  if ( cd "$worktree" && bash "$setup" --only web >/dev/null 2>&1 ); then
    ok "apps/web : dépendances installées dans le worktree"
  else
    ignore "apps/web : installation en échec — la relancer sur place (bash scripts/setup.sh --only web)"
  fi
}

# --- Réglages Claude Code du worktree --------------------------------------------------------------
# Au premier passage : copie du settings.local.json du clone principal (on hérite ainsi de
# l'approbation des serveurs MCP et des permissions locales). Ensuite, c'est le fichier DU WORKTREE
# qui sert de base — ce qu'on y a ajouté depuis n'est jamais écrasé, comme dans scripts/setup.sh.
# Dans les deux cas on impose les trois valeurs qui DOIVENT différer d'une session à l'autre :
# profil du navigateur (Chrome n'accepte qu'un consommateur par profil) et ports de la Control Tower.
reglages_claude() {
  local principal="$1" worktree="$2" profil="$3" port_api="$4" port_ui="$5"
  local source="$principal/.claude/settings.local.json"
  local cible="$worktree/.claude/settings.local.json"
  local py="" candidat

  for candidat in "$principal/.venv/Scripts/python.exe" "$principal/.venv/bin/python" python3 python; do
    if [ -x "$candidat" ] || command -v "$candidat" >/dev/null 2>&1; then py="$candidat"; break; fi
  done
  if [ -z "$py" ]; then
    ignore "réglages Claude Code : python introuvable — poser à la main dans $cible :"
    printf '      MAESTRO_CHROME_PROFILE=%s, MAESTRO_PORT_API=%s, MAESTRO_PORT_UI=%s\n' \
      "$profil" "$port_api" "$port_ui"
    return 0
  fi

  mkdir -p "$worktree/.claude"
  # Lecture/écriture explicitement en UTF-8, par fichiers et argv : aucun pipe vers stdin, donc
  # aucune prise avec l'encodage du terminal (le piège cp1252 de Windows — cf. CLAUDE.md).
  if PYTHONIOENCODING=utf-8 "$py" - "$source" "$cible" "$profil" "$port_api" "$port_ui" <<'PY'
import json, os, sys

source, cible, profil, port_api, port_ui = sys.argv[1:6]

# Le worktree déjà équipé fait foi sur le clone principal : on ne réimporte pas des réglages
# par-dessus ceux que cette session s'est donnés.
base = cible if os.path.exists(cible) else source

reglages = {}
if os.path.exists(base):
    try:
        with open(base, encoding="utf-8") as f:
            reglages = json.load(f) or {}
    except (OSError, ValueError):
        reglages = {}

env = reglages.get("env")
if not isinstance(env, dict):
    env = {}
    reglages["env"] = env

# Ces trois-là sont IMPOSÉES : hériter des valeurs du clone principal ferait justement se
# télescoper les deux sessions (même profil de navigateur, mêmes ports).
env["MAESTRO_CHROME_PROFILE"] = profil
env["MAESTRO_PORT_API"] = port_api
env["MAESTRO_PORT_UI"] = port_ui

os.makedirs(os.path.dirname(cible) or ".", exist_ok=True)
with open(cible, "w", encoding="utf-8") as f:
    json.dump(reglages, f, indent=2, ensure_ascii=False)
    f.write("\n")
PY
  then
    ok "réglages Claude Code : profil de navigateur dédié + ports $port_api/$port_ui"
  else
    erreur "réglages Claude Code : écriture de $cible impossible"
    return 1
  fi
}

# --- create ----------------------------------------------------------------------------------------
commande_create() {
  local iid="" branche="" ports="" sans_liens=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --branche) branche="${2:-}"; shift ;;
      --ports)   ports="${2:-}"; shift ;;
      --sans-liens) sans_liens=1 ;;
      -h|--help) usage; return 0 ;;
      -*) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; return 2 ;;
      *)  iid="$1" ;;
    esac
    shift
  done

  case "$iid" in
    '' ) echo "usage: bash scripts/git/worktree.sh <iid>" >&2; return 2 ;;
    *[!0-9]*) echo "IID de ticket attendu (nombre) : « $iid »" >&2; return 2 ;;
  esac

  local principal
  principal="$(depot_principal)" || { erreur "hors d'un dépôt git"; return 1; }

  # 1) Branche — même règle que /ticket-start (préfixe du label type:: + slug du titre).
  if [ -z "$branche" ]; then
    branche="$(bash "$ICI/../gitlab/lib.sh" branch-for "$iid")" || {
      erreur "branche introuvable pour #$iid (glab authentifié ?) — sinon : --branche <nom>"
      return 1
    }
  fi
  case "$branche" in
    *'<type>'*)
      erreur "ticket #$iid sans label type:: — poser le label, ou imposer --branche <nom>"
      return 1 ;;
  esac

  # 2) Emplacement : un dossier frère du dépôt, qui regroupe tous les worktrees.
  local base dest nom
  base="${MAESTRO_WORKTREE_DIR:-$(dirname "$principal")/maestro-worktrees}"
  nom="${branche#*/}"                      # « chore/152-slug » -> « 152-slug »
  dest="$base/$nom"

  printf '\nWorktree du ticket #%s — %s\n\n' "$iid" "$branche"

  # 3) Le worktree lui-même. Trois cas : déjà là (on complète), branche déjà locale (on l'emprunte),
  #    branche à créer depuis origin/main. Le repère « déjà là » est le `.git` FICHIER que git pose
  #    à la racine d'un worktree lié — pas une comparaison de chemins, que les formats mêlés de
  #    Windows (E:/… côté git, /e/… côté Git Bash) rendraient fausse.
  local sortie
  if [ -f "$dest/.git" ]; then
    deja "worktree déjà en place : $dest"
  elif [ -e "$dest" ]; then
    erreur "$dest existe déjà sans être un worktree — le retirer ou choisir un autre emplacement"
    return 1
  else
    mkdir -p "$base" 2>/dev/null
    GIT_TERMINAL_PROMPT=0 git -C "$principal" fetch origin main >/dev/null 2>&1
    if git -C "$principal" show-ref --verify --quiet "refs/heads/$branche"; then
      # Le cas d'échec le plus fréquent, et le seul que git explique mal : la branche est déjà
      # empruntée ailleurs. On nomme l'emprunteur plutôt que de laisser deviner.
      local emprunteur
      if emprunteur="$(cd "$principal" && worktree_de_branche "$branche")" && [ -n "$emprunteur" ]; then
        erreur "branche « $branche » déjà empruntée par le worktree :"
        printf '      %s\n' "$(chemin_natif "$emprunteur")" >&2
        printf '  Y ouvrir la session, ou retirer ce worktree (worktree.sh remove %s).\n' "$iid" >&2
        return 1
      fi
      if ! sortie="$(git -C "$principal" worktree add "$dest" "$branche" 2>&1)"; then
        erreur "git worktree add a échoué (branche « $branche » déjà empruntée par un autre worktree ?)"
        printf '%s\n' "$sortie" >&2
        return 1
      fi
      ok "worktree créé sur la branche existante $branche"
    else
      if ! sortie="$(git -C "$principal" worktree add "$dest" -b "$branche" origin/main 2>&1)"; then
        erreur "git worktree add -b « $branche » a échoué"
        printf '%s\n' "$sortie" >&2
        return 1
      fi
      ok "worktree créé, branche $branche depuis origin/main"
    fi
  fi
  dest="$(cd "$dest" && pwd)"
  # Exposé à `ensure`, qui a besoin du chemin retenu sans avoir à le recalculer.
  WORKTREE_DEST="$dest"

  # 4) .env — gitignoré, donc absent du worktree ; jamais écrasé s'il a déjà été adapté.
  if [ -f "$dest/.env" ]; then
    deja ".env déjà présent (préservé)"
  elif [ -f "$principal/.env" ]; then
    cp "$principal/.env" "$dest/.env" && ok ".env recopié depuis le clone principal" \
      || erreur ".env : copie impossible"
  else
    ignore ".env absent du clone principal — lancer bash scripts/setup.sh dans le worktree"
  fi

  # 5) Artefacts lourds. Deux régimes, pour une raison technique et non de goût :
    #  - .venv et .tools se PARTAGENT par lien (rien ne les lit autrement que par leur chemin) ;
    #  - apps/web/node_modules s'INSTALLE, parce que Turbopack refuse net un node_modules lié
    #    (« Symlink [project]/node_modules is invalid, it points out of the filesystem root ») et
    #    que l'UI ne démarre alors pas du tout.
  if [ "$sans_liens" = 1 ]; then
    ignore "liens non posés (--sans-liens) — équiper le worktree : bash scripts/setup.sh"
  else
    local cible code
    for cible in .venv .tools; do
      lier "$principal/$cible" "$dest/$cible"; code=$?
      case "$code" in
        0) ok "$cible partagé avec le clone principal" ;;
        2) ignore "$cible absent du clone principal — rien à partager" ;;
        3) deja "$cible déjà en place" ;;
        *) ignore "$cible : lien impossible — à installer dans le worktree (bash scripts/setup.sh)" ;;
      esac
    done
    installe_web "$dest"
  fi

  # 6) Réglages Claude Code propres à cette session.
  local decalage port_api port_ui profil
  if [ -n "$ports" ]; then
    port_api="${ports%%:*}"; port_ui="${ports##*:}"
    if [ -z "$port_api" ] || [ -z "$port_ui" ] || [ "$port_api" = "$ports" ]; then
      erreur "--ports attend <api>:<ui> (ex. 8052:3052)"
      return 2
    fi
  else
    decalage="$(decalage_ports "$iid")"
    port_api=$((8000 + decalage))
    port_ui=$((3000 + decalage))
  fi
  profil="$(chemin_natif "${HOME}/.maestro/chrome-profile-$iid")"
  reglages_claude "$principal" "$dest" "$profil" "$port_api" "$port_ui"

  # 7) Les hooks git n'ont rien à installer : core.hooksPath est une configuration du dépôt (donc
  #    partagée par tous les worktrees) et son chemin est relatif — il se résout depuis la racine
  #    du worktree courant.
  local hooks
  hooks="$(git -C "$dest" config core.hooksPath 2>/dev/null)"
  if [ -n "$hooks" ]; then
    deja "hooks git actifs (core.hooksPath = $hooks, partagé par le dépôt)"
  else
    ignore "hooks git non configurés — bash scripts/git/install-hooks.sh"
  fi

  # Chemin natif : c'est un dossier que l'utilisateur va ouvrir dans un outil Windows, pas dans
  # Git Bash — « E:\… » lui parle, « /e/… » non.
  if [ "${VIA_ENSURE:-0}" = 1 ]; then
    # Appelé par `ensure` : la session VA se relocaliser ici, il n'y a pas de seconde session à
    # ouvrir ni de /ticket-start à relancer. En revanche il faut dire les ports et le profil — une
    # session relocalisée en cours de route ne les hérite PAS (mesuré sur #181 : EnterWorktree ne
    # réévalue que les caches liés au CWD, le bloc `env` est résolu au démarrage de la session).
    printf '\nWorktree prêt : %s\n' "$(chemin_natif "$dest")"
    printf '  Control Tower de ce worktree : http://localhost:%s (API :%s)\n' "$port_ui" "$port_api"
    printf '  profil de navigateur dédié   : %s\n' "$profil"
    printf '  ⚠ non hérités par une session relocalisée (bloc env résolu au démarrage) :\n'
    printf '    les passer explicitement pour démarrer la stack ou piloter le navigateur.\n'
  else
    printf '\nPrêt. Ouvrir une seconde session Claude Code sur :\n\n  %s\n\n' "$(chemin_natif "$dest")"
    printf 'Control Tower de cette session : http://localhost:%s (API :%s).\n' "$port_ui" "$port_api"
    printf 'Le ticket reste à démarrer depuis cette session : /ticket-start %s\n' "$iid"
  fi
}

# --- ensure -----------------------------------------------------------------------------------------
# « Où doit travailler la session qui démarre le ticket #<iid> ? » — l'aiguillage appelé par
# /ticket-start (#181), pour que le clone principal cesse de changer de branche à chaque ticket.
#
# Rend UNE ligne de verdict, en DERNIER sur stdout, pour que l'appelant n'ait pas à interpréter le
# rapport humain qui la précède :
#
#   ICI <chemin>        le répertoire courant est déjà le bon endroit — rien n'a été monté
#   WORKTREE <chemin>   le worktree du ticket est prêt : la session doit s'y relocaliser
#
# Les trois situations d'appel (docs/10 §9) retombent sur ces deux verdicts :
#   - clone principal sur `main`             -> WORKTREE : le cas nominal, celui que le ticket vise ;
#   - worktree DÉJÀ sur la branche du ticket -> ICI : c'est le cas de `orchestrate/run.sh`, qui monte
#     lui-même le worktree avant d'y lancer la session. Il ne doit surtout pas en naître un second ;
#   - worktree d'un AUTRE ticket             -> WORKTREE : l'emplacement se résout depuis le clone
#     principal (`depot_principal`), il reste donc correct.
#
# Le clone principal DÉJÀ sur la branche du ticket rend « ICI » lui aussi : c'est une reprise de
# travail en cours, et l'en déloger serait gratuit et risqué. Le nouveau régime s'installe ticket
# par ticket, sans migration.
commande_ensure() {
  local iid="" branche=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --branche) branche="${2:-}"; shift ;;
      -h|--help) usage; return 0 ;;
      -*) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; return 2 ;;
      *)  iid="$1" ;;
    esac
    shift
  done

  case "$iid" in
    '') echo "usage: bash scripts/git/worktree.sh ensure <iid>" >&2; return 2 ;;
    *[!0-9]*) echo "IID de ticket attendu (nombre) : « $iid »" >&2; return 2 ;;
  esac

  if [ -z "$branche" ]; then
    branche="$(bash "$ICI/../gitlab/lib.sh" branch-for "$iid")" || {
      erreur "branche introuvable pour #$iid (glab authentifié ?) — sinon : --branche <nom>"
      return 1
    }
  fi
  case "$branche" in
    *'<type>'*)
      erreur "ticket #$iid sans label type:: — poser le label, ou imposer --branche <nom>"
      return 1 ;;
  esac

  # Déjà au bon endroit ? Le test porte sur la BRANCHE du répertoire courant, jamais sur son
  # chemin : sous Windows git répond « E:/… » là où Git Bash répond « /e/… », et une comparaison
  # de chemins passerait à côté (même piège que dans `commande_remove`).
  local courante racine
  courante="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
  if [ -n "$courante" ] && [ "$courante" = "$branche" ]; then
    racine="$(git rev-parse --path-format=absolute --show-toplevel 2>/dev/null)" || racine="$(pwd)"
    # Chemin NATIF : ce verdict est consommé par l'outil de relocalisation de session côté
    # Windows, pas par Git Bash. Un « /tmp/… » de MSYS y serait résolu en « E:\tmp\… ».
    printf 'ICI %s\n' "$(chemin_natif "$racine")"
    return 0
  fi

  # Affectation explicite plutôt que préfixe `VIA_ENSURE=1 commande_create …` : devant un APPEL DE
  # FONCTION, la persistance de l'affectation dépend du mode POSIX du shell. On ne parie pas.
  local code
  WORKTREE_DEST=""
  VIA_ENSURE=1
  commande_create "$iid" --branche "$branche"
  code=$?
  VIA_ENSURE=0
  [ "$code" -eq 0 ] || return "$code"
  if [ -z "$WORKTREE_DEST" ]; then
    erreur "worktree monté mais chemin introuvable — état inattendu, ne pas relocaliser la session"
    return 1
  fi
  printf 'WORKTREE %s\n' "$(chemin_natif "$WORKTREE_DEST")"
}

# --- list ------------------------------------------------------------------------------------------
commande_list() {
  local principal chemin branche nom iid decalage
  principal="$(depot_principal)" || { erreur "hors d'un dépôt git"; return 1; }

  printf '\nWorktrees de %s\n\n' "$principal"
  while IFS= read -r ligne; do
    case "$ligne" in
      worktree\ *) chemin="${ligne#worktree }" ;;
      branch\ *)
        branche="${ligne#branch refs/heads/}"
        nom="${branche#*/}"
        iid="${nom%%-*}"
        if [ "$chemin" = "$principal" ]; then
          printf '  %-50s %s\n' "$chemin" "[$branche] — ports 8000/3000"
        else
          case "$iid" in
            ''|*[!0-9]*) printf '  %-50s %s\n' "$chemin" "[$branche]" ;;
            *)
              decalage="$(decalage_ports "$iid")"
              printf '  %-50s %s\n' "$chemin" "[$branche] — ports $((8000 + decalage))/$((3000 + decalage))" ;;
          esac
        fi ;;
      detached) printf '  %-50s %s\n' "$chemin" "(HEAD détaché)" ;;
    esac
  done < <(git -C "$principal" worktree list --porcelain 2>/dev/null)
  printf '\n'
}

# --- remove ----------------------------------------------------------------------------------------
commande_remove() {
  local cible="" force=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --force) force=1 ;;
      -h|--help) usage; return 0 ;;
      -*) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; return 2 ;;
      *)  cible="$1" ;;
    esac
    shift
  done
  [ -n "$cible" ] || { echo "usage: bash scripts/git/worktree.sh remove <iid|chemin>" >&2; return 2; }

  local principal chemin=""
  principal="$(depot_principal)" || { erreur "hors d'un dépôt git"; return 1; }

  if [ -d "$cible" ]; then
    # Par git, et non par `cd`+`pwd` : la comparaison avec le clone principal (juste en dessous)
    # doit porter sur des chemins du MÊME format — sous Windows, git répond « C:/… » là où Git
    # Bash répond « /c/… », et le garde-fou passerait à côté.
    chemin="$(git -C "$cible" rev-parse --path-format=absolute --show-toplevel 2>/dev/null)"
    [ -n "$chemin" ] || chemin="$(cd "$cible" && pwd)"
  else
    # Un iid : on retrouve le worktree dont la branche porte ce numéro. Deux précautions :
    #   - le clone principal est ÉCARTÉ — sa branche porte souvent le même iid (on ouvre un
    #     worktree depuis le ticket sur lequel on travaille), et il serait trouvé en premier ;
    #   - le chemin n'est retenu QU'EN CAS de correspondance : un worktree en HEAD détaché n'émet
    #     pas de ligne `branch`, et garder le dernier chemin vu ferait retirer le mauvais.
    local courant="" nom
    while IFS= read -r ligne; do
      case "$ligne" in
        worktree\ *) courant="${ligne#worktree }" ;;
        branch\ refs/heads/*)
          [ "$courant" = "$principal" ] && continue
          nom="${ligne#branch refs/heads/}"
          nom="${nom#*/}"
          case "$nom" in
            "$cible"|"$cible"-*) chemin="$courant"; break ;;
          esac ;;
      esac
    done < <(git -C "$principal" worktree list --porcelain 2>/dev/null)
  fi

  if [ -z "$chemin" ] || [ "$chemin" = "$principal" ]; then
    erreur "aucun worktree trouvé pour « $cible » (le clone principal ne se retire pas)"
    return 1
  fi

  # Refus AVANT de toucher à quoi que ce soit : un worktree au travail ne se retire pas par
  # surprise (et on ne veut pas l'avoir délié pour rien).
  if [ "$force" != 1 ] && [ -n "$(git -C "$chemin" status --porcelain 2>/dev/null)" ]; then
    erreur "changements non commités dans $chemin — les committer ou les annuler, sinon --force"
    return 1
  fi

  # Les liens PARTENT EN PREMIER : sans ça, `git worktree remove` descend dans les jonctions et
  # vide le .venv et le node_modules du clone principal (constaté, #152).
  local artefact
  for artefact in .venv .tools apps/web/node_modules; do
    if delier "$chemin/$artefact"; then
      ok "$artefact délié — la cible du clone principal reste intacte"
    fi
  done

  local args=(worktree remove) sortie
  [ "$force" = 1 ] && args+=(--force)
  if sortie="$(git -C "$principal" "${args[@]}" "$chemin" 2>&1)"; then
    ok "worktree retiré : $chemin"
    printf '\nLa branche, elle, est intacte — sa suppression passe par /branch-cleanup, après\n'
    printf 'confirmation du merge par GitLab.\n'
  else
    # La cause est dans la sortie de git, et elle varie : « Filename too long » sur un
    # node_modules profond, worktree verrouillé, fichiers restants… la montrer plutôt que de la
    # deviner. Les liens ayant déjà été retirés, une reprise à la main ne risque plus rien.
    erreur "git worktree remove a échoué :"
    printf '%s\n' "$sortie" >&2
    printf '  Les liens ont déjà été retirés : le clone principal ne risque plus rien si le\n' >&2
    printf '  dossier doit être supprimé à la main.\n' >&2
    return 1
  fi
}

# --- Aiguillage ------------------------------------------------------------------------------------
cmd="${1:-}"
[ "$#" -gt 0 ] && shift
case "$cmd" in
  create)      commande_create "$@" ;;
  ensure)      commande_ensure "$@" ;;
  list)        commande_list "$@" ;;
  remove)      commande_remove "$@" ;;
  -h|--help|'') usage ;;
  # Raccourci : un iid nu vaut `create <iid>`.
  *[!0-9]*)    printf 'Sous-commande inconnue : %s\n\n' "$cmd" >&2; usage >&2; exit 2 ;;
  *)           commande_create "$cmd" "$@" ;;
esac
