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
#   bash scripts/git/worktree.sh gc              # ramasse ceux dont le travail est soldé
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
#
# Le cycle de vie se REFERME tout seul (#197). Un worktree pèse ~535 Mo (dont 93 % de node_modules
# installé sur place) et #181 en a fait la voie par défaut de tout ticket : sans ramassage, un run
# /orchestrate de dix tickets laisse ~5 Go derrière lui. `gc` retire ceux dont GitLab confirme le
# travail soldé, et il est appelé d'office par `ensure` — le pendant, pour les worktrees, de ce que
# `lib.sh cleanup-merged` fait aux branches locales depuis #23.

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
alerte() { printf '  ⚠ %s\n' "$*"; }
erreur() { printf '  ✗ %s\n' "$*" >&2; }

usage() {
  cat <<'USAGE'
Un worktree git par ticket — deux tickets, deux sessions, un seul dépôt.

  bash scripts/git/worktree.sh [create] <iid> [options]
  bash scripts/git/worktree.sh ensure <iid> [--branche <nom>]
  bash scripts/git/worktree.sh list
  bash scripts/git/worktree.sh remove <iid|chemin> [--force]
  bash scripts/git/worktree.sh gc [--check] [--auto]

`ensure` est l'aiguillage de /ticket-start : il dit où la session doit travailler, en rendant
en dernière ligne « ICI <chemin> » (le répertoire courant convient déjà — cas d'orchestrate,
qui monte le worktree lui-même) ou « WORKTREE <chemin> » (worktree prêt, s'y relocaliser).

`gc` ramasse les worktrees dont le travail est SOLDÉ — MR mergée ou ticket fermé, confirmé par
glab. Il tourne d'office au début d'`ensure` (donc de /ticket-start) : le retrait n'est pas un
geste à se rappeler. `--check` diagnostique sans rien retirer, `--auto` ne parle que s'il a
quelque chose à dire. MAESTRO_WORKTREE_GC=0 désactive le passage automatique.

`ensure` remet aussi les DÉPENDANCES du clone principal à niveau, en appelant `scripts/setup.sh`
(dérive détectée par `--derive`, réparée par `--only`) : un paquet ajouté au dépôt arrive ainsi
sans geste à se rappeler. Il signale et n'interrompt jamais un démarrage de ticket.
MAESTRO_MAJ_DEPENDANCES=0 le désactive.

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

# --- Mise à niveau des dépendances (#216) -------------------------------------------------------------
# Un clone existant ne prend pas tout seul les dépendances ajoutées au dépôt : une entrée de
# `pyproject.toml`, un paquet de `apps/web/package-lock.json`, une version de `.node-version`. La CI
# les prend à chaque pipeline, un clone neuf à son `/setup` — un clone déjà monté, jamais, jusqu'à ce
# que quelqu'un rejoue `setup.sh` de sa propre initiative.
#
# Aucun événement local ne s'y prête (même leçon que `sync-main`, #205 : le merge a lieu sur GitLab,
# et la mise à jour de `main` passe tantôt par `git merge --ff-only`, tantôt par `git update-ref` —
# qui ne déclenche aucun hook). Le déclencheur est donc ce point de passage obligé : tout
# /ticket-start passe par `ensure`, manuel comme autonome.
#
# Trois règles, dans l'ordre d'importance :
#   1. RIEN N'EST RÉIMPLÉMENTÉ : la détection comme la réparation sont celles de setup.sh
#      (`--derive` puis `--only`). Ni pip ni npm ne sont appelés ici.
#   2. C'EST LE CLONE PRINCIPAL qu'on remet à niveau : `.venv/` et `.tools/` y vivent, partagés par
#      lien avec tous les worktrees (docs/10 §9), et l'installation éditable de `maestro` doit
#      continuer d'y pointer (#194). Le `node_modules` du worktree, lui, est installé à sa création.
#   3. ÇA NE BLOQUE JAMAIS un démarrage de ticket — même statut que `sync-main` : ça signale.
# MAESTRO_MAJ_DEPENDANCES=0 la désactive.
maj_dependances() {
  local principal setup lignes code etape raison etapes="" sortie
  [ "${MAESTRO_MAJ_DEPENDANCES:-1}" != 0 ] || return 0
  principal="$(depot_principal)" || return 0
  setup="$principal/scripts/setup.sh"
  [ -f "$setup" ] || return 0

  lignes="$(bash "$setup" --derive 2>/dev/null)"
  code=$?
  # 0 = à jour (on se tait) ; 3 = dérive ; autre = sonde indisponible, ce n'est pas un sujet ici.
  [ "$code" -eq 3 ] || return 0

  while IFS="$(printf '\t')" read -r etape raison; do
    [ -n "$etape" ] || continue
    case ",$etapes," in
      *",$etape,"*) ;;
      *) etapes="${etapes:+$etapes,}$etape" ;;
    esac
    alerte "dépendances en retard — $raison"
  done <<EOF
$lignes
EOF
  [ -n "$etapes" ] || return 0

  # Annoncé AVANT de lancer : un `pip install` ou un `npm ci` laisse la console muette une minute.
  printf '  … mise à niveau du clone principal (bash scripts/setup.sh --only %s)\n' "$etapes"
  if sortie="$(bash "$setup" --only "$etapes" 2>&1)"; then
    ok "dépendances à niveau ($etapes)"
  else
    alerte "mise à niveau des dépendances en échec — le ticket démarre quand même, rattrapage : (cd \"$principal\" && bash scripts/setup.sh --only $etapes)"
    [ -z "$sortie" ] || printf '%s\n' "$sortie" | tail -3 | sed 's/^/    /' >&2
  fi
  return 0
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

  # Mise à jour de `main` dans le clone principal (#205), en fast-forward seulement. Même raison
  # d'être ici que le ramassage ci-dessous : depuis #181 la session travaille dans un worktree, donc
  # plus personne ne repasse par `main` — c'est ce point de passage-ci, qu'emprunte tout
  # /ticket-start (manuel comme autonome), qui la remet à niveau. Best-effort et muet quand elle est
  # déjà à jour ; ses abstentions (arbre porteur sale, divergence) sont relayées telles quelles et
  # n'empêchent jamais un ticket de démarrer.
  local sortie_sync code_sync
  if [ "${MAESTRO_SYNC_MAIN:-1}" != 0 ]; then
    sortie_sync="$(bash "$ICI/../gitlab/lib.sh" sync-main 2>&1)"
    code_sync=$?
    if [ -n "$sortie_sync" ]; then
      if [ "$code_sync" -eq 0 ]; then
        ok "$sortie_sync"
      else
        printf '%s\n' "$sortie_sync" | sed 's/^/  /' >&2
      fi
    fi
  fi

  # Dépendances du dépôt (#216), APRÈS `sync-main` : c'est lui qui vient de faire entrer dans le
  # clone principal le `pyproject.toml` ou le `package-lock.json` d'où naît la dérive.
  maj_dependances

  # Ramassage des worktrees soldés (#197), AVANT de monter celui-ci et quel que soit le verdict qui
  # suivra : c'est le seul moment où quelqu'un passe par ici à coup sûr, et le pendant exact du
  # `cleanup-merged` que `start-branch` fait aux branches juste après. Best-effort et muet quand il
  # n'y a rien à dire — un ramassage qui échoue ne doit pas empêcher un ticket de démarrer. En
  # `--auto` il n'écrit que des lignes de compte rendu, jamais un verdict : le contrat « dernière
  # ligne de stdout » d'`ensure` reste tenu.
  if [ "${MAESTRO_WORKTREE_GC:-1}" != 0 ]; then
    commande_gc --auto || true
  fi

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

  if retire_worktree "$chemin" "$force" 1; then
    ok "worktree retiré : $chemin"
    printf '\nLa branche, elle, est intacte — sa suppression passe par /branch-cleanup, après\n'
    printf 'confirmation du merge par GitLab.\n'
  else
    erreur "git worktree remove a échoué :"
    printf '%s\n' "$RETRAIT_ERREUR" >&2
    printf '  Les liens ont déjà été retirés : le clone principal ne risque plus rien si le\n' >&2
    printf '  dossier doit être supprimé à la main.\n' >&2
    return 1
  fi
}

# --- gc : ramasser les worktrees soldés (#197) ------------------------------------------------------
# travail_non_sauvegarde <chemin> <branche> [sha] -> « <fichiers non commités> <commits non poussés> ».
#
# Le garde-fou du ramassage : un worktree ne se retire que si ce qu'il porte est ailleurs. Les
# fichiers non commités sont immédiats ; les commits demandent de choisir la BONNE référence, et
# c'est là que la naïveté coûte cher — le projet mergeant en SQUASH, `origin/main..HEAD` compte les
# commits de TOUTE branche mergée et ferait refuser chaque candidat. Par ordre de fiabilité :
#   0. HEAD est un ANCÊTRE d'`origin/main` : tout ce qui est ici est déjà sur le serveur, quelle que
#      soit l'histoire de la branche. C'est la formulation exacte de la question posée, et elle règle
#      au passage la branche RECRÉÉE depuis `main` après son merge (son sha de merge a divergé, et le
#      comparer ferait compter comme « non poussés » des commits qui sont sur `main`) ;
#   1. `origin/<branche>` s'il existe encore (branche poussée, pas encore mergée ou suppression
#      décochée au merge) — la référence exacte de ce que le serveur a reçu ;
#   2. le <sha> de merge rendu par `lib.sh worktree-done` : la tête de la branche source au moment du
#      merge, la seule trace locale de ce qui est parti quand GitLab a supprimé la branche distante ;
#   3. `origin/main` en dernier recours — cas d'une branche JAMAIS poussée (ticket fermé sans MR),
#      où ses commits locaux sont précisément le travail non sauvegardé.
# Aucune référence trouvable (dépôt sans distant) : on rend « 0 », et c'est le seul cas où le
# compteur peut mentir par défaut ; les fichiers non commités, eux, restent comptés.
travail_non_sauvegarde() {
  local chemin="$1" branche="$2" sha="${3:-}" modifs commits=0 base=""
  modifs="$(git -C "$chemin" status --porcelain 2>/dev/null | grep -c .)" || modifs=0
  if git -C "$chemin" merge-base --is-ancestor HEAD refs/remotes/origin/main >/dev/null 2>&1; then
    printf '%s 0' "${modifs:-0}"
    return 0
  fi
  if git -C "$chemin" rev-parse --verify -q "refs/remotes/origin/$branche" >/dev/null 2>&1; then
    base="refs/remotes/origin/$branche"
  elif [ -n "$sha" ] && git -C "$chemin" cat-file -e "$sha^{commit}" >/dev/null 2>&1; then
    base="$sha"
  elif git -C "$chemin" rev-parse --verify -q refs/remotes/origin/main >/dev/null 2>&1; then
    base="refs/remotes/origin/main"
  fi
  [ -n "$base" ] && commits="$(git -C "$chemin" rev-list --count "$base..HEAD" 2>/dev/null)"
  printf '%s %s' "${modifs:-0}" "${commits:-0}"
}

# retire_worktree <chemin> <force 0|1> <verbeux 0|1> : la séquence de retrait, écrite UNE FOIS —
# délier PUIS retirer. L'ordre est le garde-fou de #152 : `git worktree remove` descend dans les
# jonctions et viderait le .venv et le node_modules du CLONE PRINCIPAL. Rend 0 si le worktree est
# parti ; sinon 1, la sortie de git étant laissée dans RETRAIT_ERREUR (sa cause varie : « Filename
# too long » sur un node_modules profond, worktree verrouillé, dossier occupé par un shell…).
RETRAIT_ERREUR=""
retire_worktree() {
  local chemin="$1" force="$2" verbeux="$3" principal artefact sortie
  local args=(worktree remove)
  principal="$(depot_principal)" || return 1
  RETRAIT_ERREUR=""
  for artefact in .venv .tools apps/web/node_modules; do
    if delier "$chemin/$artefact" && [ "$verbeux" = 1 ]; then
      ok "$artefact délié — la cible du clone principal reste intacte"
    fi
  done
  [ "$force" = 1 ] && args+=(--force)
  if sortie="$(git -C "$principal" "${args[@]}" "$chemin" 2>&1)"; then
    return 0
  fi
  RETRAIT_ERREUR="$sortie"
  return 1
}

# commande_gc [--check] [--auto] : retire les worktrees dont GitLab confirme le travail soldé.
#
# Trois refus, dans cet ordre, parce qu'ils ne coûtent pas la même chose :
#   - le worktree de la SESSION COURANTE, jamais candidat (on ne se retire pas le sol sous les pieds) ;
#   - un worktree porteur de TRAVAIL NON SAUVEGARDÉ : signalé, jamais supprimé en silence. C'est
#     l'inverse du confort : mieux vaut 535 Mo de trop qu'un commit perdu ;
#   - un verdict autre que « fini » — y compris « inconnu » (glab muet, hors ligne). Ne rien savoir
#     n'autorise rien : le nom de la branche ne sert jamais de preuve de merge (docs/10 §6).
#
# Deux temps volontaires : on INVENTORIE d'abord, on décide ensuite. `git worktree remove` réécrit la
# liste que l'on parcourrait.
#
# MAESTRO_WORKTREE_VERDICT remplace l'interrogation de GitLab par une commande qui reçoit
# « <iid> <branche> » et imprime la ligne de verdict : c'est la couture par laquelle les tests font
# tourner le ramassage sans réseau ni glab (même dispositif que MAESTRO_ORCHESTRATE_WORKTREE, #172).
commande_gc() {
  local check=0 auto=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --check) check=1 ;;
      --auto)  auto=1 ;;
      -h|--help) usage; return 0 ;;
      *) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; return 2 ;;
    esac
    shift
  done

  local principal courant
  principal="$(depot_principal)" || { erreur "hors d'un dépôt git"; return 1; }
  # Format de git (« E:/… ») des deux côtés : Git Bash répondrait « /e/… » et la comparaison de
  # chemins passerait à côté, exactement comme dans commande_remove.
  courant="$(git rev-parse --path-format=absolute --show-toplevel 2>/dev/null)"

  # Un couple « chemin<TAB>branche » par ligne : le chemin peut porter des espaces (« Projects
  # Solutions »), la tabulation ne risque rien.
  local ligne chemin="" paires=""
  while IFS= read -r ligne; do
    case "$ligne" in
      worktree\ *) chemin="${ligne#worktree }" ;;
      branch\ refs/heads/*)
        [ "$chemin" = "$principal" ] && continue
        paires="$paires$chemin"$'\t'"${ligne#branch refs/heads/}"$'\n' ;;
    esac
  done < <(git -C "$principal" worktree list --porcelain 2>/dev/null)

  if [ -z "$paires" ]; then
    [ "$auto" = 1 ] || printf '\nAucun worktree en place — rien à ramasser.\n\n'
    return 0
  fi

  [ "$auto" = 1 ] || printf '\nRamassage des worktrees de %s\n\n' "$principal"

  local branche nom iid brut verdict sha raison reste n_modifs n_commits detail
  local retires=0 gardes=0 signales=0 echecs=0 rapport=""
  while IFS=$'\t' read -r chemin branche; do
    [ -n "$chemin" ] || continue
    nom="${branche#*/}"
    iid="${nom%%-*}"

    if [ -n "$courant" ] && [ "$chemin" = "$courant" ]; then
      gardes=$((gardes + 1))
      [ "$auto" = 1 ] || rapport="$rapport$(ignore "$branche — session courante, jamais ramassée")"$'\n'
      continue
    fi

    case "$iid" in
      ''|*[!0-9]*)
        gardes=$((gardes + 1))
        [ "$auto" = 1 ] || rapport="$rapport$(ignore "$branche — nom hors convention, aucun ticket à interroger")"$'\n'
        continue ;;
    esac

    if [ -n "${MAESTRO_WORKTREE_VERDICT:-}" ]; then
      brut="$("$MAESTRO_WORKTREE_VERDICT" "$iid" "$branche" 2>/dev/null)"
    else
      brut="$(bash "$ICI/../gitlab/lib.sh" worktree-done "$iid" "$branche" 2>/dev/null)"
    fi
    # « - » marque un sha absent : dans un TSV lu par `read`, la tabulation est un séparateur BLANC
    # (deux d'affilée comptent pour une seule), donc un champ vide décalerait la raison dans le sha.
    IFS=$'\t' read -r verdict sha raison <<< "$brut"
    [ "$sha" = "-" ] && sha=""

    if [ "$verdict" != "fini" ]; then
      gardes=$((gardes + 1))
      [ "$auto" = 1 ] || rapport="$rapport$(deja "#$iid conservé — ${raison:-verdict indisponible}")"$'\n'
      continue
    fi

    reste="$(travail_non_sauvegarde "$chemin" "$branche" "$sha")"
    n_modifs="${reste%% *}"; n_commits="${reste##* }"
    detail=""
    [ "${n_modifs:-0}" -gt 0 ] && detail="$n_modifs fichier(s) non commité(s)"
    [ "${n_commits:-0}" -gt 0 ] && detail="${detail:+$detail, }$n_commits commit(s) non poussé(s)"
    if [ -n "$detail" ]; then
      # Toujours dit, même en --auto : c'est le seul cas où se taire ferait perdre du travail.
      signales=$((signales + 1))
      gardes=$((gardes + 1))
      rapport="$rapport$(alerte "#$iid conservé — $detail dans $(chemin_natif "$chemin")")"$'\n'
      continue
    fi

    if [ "$check" = 1 ]; then
      retires=$((retires + 1))
      rapport="$rapport$(printf '  → #%s à retirer — %s' "$iid" "$raison")"$'\n'
      continue
    fi

    if retire_worktree "$chemin" 0 0; then
      retires=$((retires + 1))
      rapport="$rapport$(ok "#$iid retiré — $raison")"$'\n'
    else
      # Construit à la main : `erreur` écrit sur stderr, la ligne sortirait donc du rapport (et hors
      # de son ordre). Ici tout le compte rendu part sur stdout, en un bloc.
      echecs=$((echecs + 1))
      rapport="$rapport$(printf '  ✗ #%s non retiré : %s' "$iid" "$(printf '%s' "$RETRAIT_ERREUR" | head -1)")"$'\n'
    fi
  done <<< "$paires"

  # En --auto (appelé par `ensure`, donc par /ticket-start) on ne parle que s'il y a quelque chose à
  # dire : un retrait, un travail en danger, un échec. Le silence est le cas normal.
  if [ "$auto" = 1 ] && [ "$retires" -eq 0 ] && [ "$signales" -eq 0 ] && [ "$echecs" -eq 0 ]; then
    return 0
  fi
  [ "$auto" = 1 ] && printf '\n'
  printf '%s' "$rapport"
  if [ "$check" = 1 ]; then
    printf 'Ramassage (--check) : %s à retirer, %s conservé(s) — rien n'\''a été touché.\n' "$retires" "$gardes"
  else
    printf 'Ramassage des worktrees : %s retiré(s), %s conservé(s).\n' "$retires" "$gardes"
  fi
  [ "$auto" = 1 ] && printf '\n'
  return 0
}

# --- Aiguillage ------------------------------------------------------------------------------------
cmd="${1:-}"
[ "$#" -gt 0 ] && shift
case "$cmd" in
  create)      commande_create "$@" ;;
  ensure)      commande_ensure "$@" ;;
  list)        commande_list "$@" ;;
  remove)      commande_remove "$@" ;;
  gc)          commande_gc "$@" ;;
  -h|--help|'') usage ;;
  # Raccourci : un iid nu vaut `create <iid>`.
  *[!0-9]*)    printf 'Sous-commande inconnue : %s\n\n' "$cmd" >&2; usage >&2; exit 2 ;;
  *)           commande_create "$cmd" "$@" ;;
esac
