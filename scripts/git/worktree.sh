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
#     et s'arrêtent mutuellement la Control Tower ;
#   - `.maestro/session/`, l'ATELIER de la session — le seul endroit où elle puisse écrire ses
#     fichiers de travail en chemin relatif, donc les relire ensuite (#307, docs/10 §11.7).
#
# Ports dérivés du numéro de ticket (déterministes, donc stables d'une session à l'autre) :
# API 8000+<iid mod 100>, UI 3000+<iid mod 100> — le clone principal gardant 8000/3000.
#
# ⚠ RETIRER UN WORKTREE PASSE PAR `remove` — jamais par un `rm -rf` ni un `git worktree remove`
# lancé à la main : les artefacts partagés sont des JONCTIONS, qu'une suppression récursive
# traverse. Elle viderait alors le .venv et le node_modules du CLONE PRINCIPAL. `remove` délie
# d'abord, puis retire.
#
# Retirer un worktree ne supprime jamais sa branche : ni `create`, ni `remove`, ni `gc` n'y touchent.
# La seule suppression de branche du script est la purge que `ensure` délègue à `lib.sh
# cleanup-merged` (#305, docs/10 §9.5) — et elle ne porte que sur les branches dont GitLab confirme
# la PR mergée, jamais sur celle du worktree qu'on monte (docs/10-workflow-git.md §6).
#
# Le cycle de vie se REFERME tout seul (#197). Un worktree pèse ~535 Mo (dont 93 % de node_modules
# installé sur place) et #181 en a fait la voie par défaut de tout ticket : sans ramassage, un run
# /orchestrate de dix tickets laisse ~5 Go derrière lui. `gc` retire ceux dont GitLab confirme le
# travail soldé, et il est appelé d'office par `ensure` — le pendant, pour les worktrees, de ce que
# `lib.sh cleanup-merged` fait aux branches locales depuis #23. Depuis #275 il pose au passage le
# cycle de vie « Terminé » du ticket : le verdict « soldé » qu'il vient d'obtenir est exactement la
# question de la réconciliation, et le poser ici évite d'ajouter une étape de plus à `ensure`.

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
  bash scripts/git/worktree.sh gc [--check] [--auto] [--sauf <iid>] [--iid <iid>]
  bash scripts/git/worktree.sh sessions [<iid>|--tous]

`ensure` est l'aiguillage de /ticket-start : il dit où la session doit travailler, en rendant
en dernière ligne « ICI <chemin> » (le répertoire courant convient déjà — cas d'orchestrate,
qui monte le worktree lui-même) ou « WORKTREE <chemin> » (worktree prêt, s'y relocaliser).

`gc` ramasse les worktrees dont le travail est SOLDÉ — PR mergée ou ticket fermé, confirmé par
la forge. Il tourne d'office au début d'`ensure` (donc de /ticket-start) : le retrait n'est pas un
geste à se rappeler. `--check` diagnostique sans rien retirer, `--auto` ne parle que s'il a
quelque chose à dire. MAESTRO_WORKTREE_GC=0 désactive le passage automatique.

Il écarte au passage les COQUILLES (#422) : les dossiers VIDES que `git worktree remove` laisse
derrière lui quand un processus tient le dossier — il en supprime le contenu, échoue dessus, et
désenregistre le worktree quand même. Personne ne les voyait (`git worktree list` ne les connaît
plus), et une coquille BLOQUAIT le remontage de son ticket. Un dossier inconnu qui porte quelque
chose est nommé, jamais touché.

Sur ce même verdict « soldé », `gc` pose le CYCLE DE VIE « Terminé » du ticket (#275) via
`lib.sh reconcile-workflow` — le merge ferme le ticket mais ne touche à aucun label, et sans ça
un ticket mergé s'affiche « En revue » jusqu'au prochain /branch-cleanup manuel. Best-effort et
jamais bloquant ; « Abandonné »/« Doublon » ne sont jamais écrasés. MAESTRO_WORKFLOW_POSE=0
l'éteint (toute autre valeur remplace l'appel — couture des tests).

Au même endroit et pour la même raison, `gc` SIGNALE les tickets « En cours » ORPHELINS (#328) via
`lib.sh reconcile-en-cours --auto` : une session morte laisse son ticket « En cours » et assigné,
donc invisible de `queue.sh` pour toujours. Purement consultatif — rien n'est repris ni reposé, la
reprise est un geste explicite qui se demande (`lib.sh reprendre-en-cours <iid>`, #329, et le
signalement le rappelle). `--sauf <iid>` écarte le ticket qu'on démarre (`ensure` le
passe). MAESTRO_EN_COURS_SIGNAL=0 l'éteint (toute autre valeur remplace l'appel — couture des tests).

`ensure` remet aussi les DÉPENDANCES du clone principal à niveau, en appelant `scripts/setup.sh`
(dérive détectée par `--derive`, réparée par `--only`) : un paquet ajouté au dépôt arrive ainsi
sans geste à se rappeler. Il signale et n'interrompt jamais un démarrage de ticket.
MAESTRO_MAJ_DEPENDANCES=0 le désactive.

`sessions` retrouve les SESSIONS Claude Code (#385 et #397, docs/10 §9.7). Claude Code range un
transcript sous le RÉPERTOIRE COURANT de la session, et son sélecteur `/resume` ne montre que celui
d'où on l'appelle : comme /ticket-start relocalise la session dans le worktree, l'historique d'un
ticket est invisible depuis le clone principal — et `gc` retire ensuite le worktree. Le verbe rend
date, nom d'onglet, titre, identifiant et la commande de reprise :

  sessions            les 10 dernières de CE dossier — ce qu'on cherche en rouvrant VS Code (#397)
  sessions --limite 0 les mêmes, sans troncature (MAESTRO_SESSIONS_LIMITE déplace le défaut)
  sessions <iid>      celles d'un ticket, worktree encore là ou non (#385)
  sessions --tous     l'inventaire : tous les tickets qui en ont

La reprise passe par l'IDENTIFIANT (`claude --resume <id>`), qui court-circuite le sélecteur. Le NOM
d'onglet vient du registre `<config>/sessions/<PID>.json`, qu'aucun redémarrage n'efface — il est
indexé par PID, donc muet sur ce qui tourne encore, mais c'est la seule source du nom. Portée : ce
que CETTE MACHINE a produit, comme `gc`.

Options de création :
  --branche <nom>   Nom de branche imposé (par défaut : résolu depuis le ticket via lib.sh).
  --ports <api:ui>  Ports Control Tower imposés (par défaut : dérivés de l'iid).
  --sans-liens      N'installe aucun lien vers .venv/.tools/node_modules — le worktree est
                    autonome, à équiper avec `bash scripts/setup.sh`.
  -h, --help        Cette aide.

Emplacement : <clone-principal>/.claude/worktrees/<iid>-<slug> — le seul endroit que le CLI
Claude Code tient pour « géré », donc le seul où EnterWorktree entre sans demander de
validation (#847). Surchargeable par MAESTRO_WORKTREE_DIR (le dossier qui accueille les
worktrees) — ailleurs, la question revient à chaque /ticket-start.
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

# base_worktrees <clone principal> : le dossier qui accueille les worktrees. Écrit UNE fois — la
# création, l'inventaire, le ramassage des coquilles (#422) et l'adressage des sessions (#385)
# désignent forcément le même endroit, et deux formules à tenir d'accord finiraient par ramasser
# ailleurs que là où l'on monte.
#
# SOUS le clone principal, dans `.claude/worktrees/`, et ce n'est pas un choix de rangement (#847) :
# depuis le CLI 2.1.206, `EnterWorktree path=…` vers un worktree situé AILLEURS déclenche une
# demande de validation qu'aucune règle `allow` ne lève — un contrôle de sûreté du CLI
# (« permission-root relocation … outside .claude/worktrees/ »), pas une permission —, si bien
# que chaque /ticket-start interactif s'arrêtait dessus, jusqu'à une heure mesurée. Le critère du
# CLI est `<racine du dépôt>/.claude/worktrees/` en chemin RÉEL (un lien symbolique est refusé), et
# c'est le seul geste qui reste : `bypassPermissions` le saute aussi, au prix de tout le `allow`
# (#791). Mesuré sur un dépôt jetable — `scripts/claude/essai-worktree-gere.py` : entrée sans
# question ici, refusée dans un dossier frère, et le garde-fou d'écriture `.claude/` (#229/#238) ne
# déborde PAS sur un tel worktree. Le dossier est gitignoré (le CLI le pose aussi dans
# `.git/info/exclude`) : le clone principal reste propre.
base_worktrees() {
  printf '%s' "${MAESTRO_WORKTREE_DIR:-$1/.claude/worktrees}"
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

# --- Coquilles laissées par un retrait (#422) -------------------------------------------------------
# `git worktree remove` supprime le CONTENU, échoue sur le DOSSIER lui-même quand un processus le
# tient (« Permission denied » sous Windows) — et va au bout de son DÉSENREGISTREMENT quand même. Il
# reste alors un dossier vide que plus rien ne revendique : ni `git worktree list`, ni `list`, ni
# `gc`, qui itèrent tous les trois dessus. Observé en direct sur #415 le 2026-08-21, après dix
# autres accumulées sans que rien ne les nomme jamais.
#
# Ce n'est pas qu'une affaire de propreté : `commande_create` refuse tout dossier déjà là qui n'est
# pas un worktree, donc une coquille BLOQUE le remontage de son ticket — `ensure` rend 1 et
# /ticket-start s'arrête, ce qui vaut un échec de plus (et la cascade des lots suivants) en run
# autonome.
#
# Le repère est le `.git` que git pose à la racine de tout worktree lié : un dossier qui n'en a pas
# n'est revendiqué par aucun. Il vaut mieux qu'une comparaison avec les chemins de
# `git worktree list` — sous Windows git répond « E:/… » là où le shell manipule « /e/… », et un
# `MAESTRO_WORKTREE_DIR` à contre-obliques ferait passer des worktrees VIVANTS pour des coquilles
# (même piège que dans `commande_remove` et `commande_gc`).
#
# Rend « <chemin><TAB>vide|porteur » par dossier inconnu. Seuls les VIDES se retirent : un dossier
# inconnu qui porte quelque chose est le travail de quelqu'un — on le nomme, on n'y touche pas.
coquilles() {
  local principal="$1" base entree chemin
  base="$(base_worktrees "$principal")"
  [ -d "$base" ] || return 0
  for entree in "$base"/*/; do
    chemin="${entree%/}"
    # `nullglob` n'est pas posé : un motif sans correspondance reste littéral, et ce test l'écarte.
    [ -d "$chemin" ] || continue
    [ -e "$chemin/.git" ] && continue
    if [ -z "$(ls -A "$chemin" 2>/dev/null)" ]; then
      printf '%s\tvide\n' "$chemin"
    else
      printf '%s\tporteur\n' "$chemin"
    fi
  done
}

# ramasse_coquilles <clone principal> <check 0|1> : retire les coquilles vides, nomme le reste.
#
# Le compte rendu part dans COQUILLES_RAPPORT et non sur stdout, et les compteurs dans
# COQUILLES_RETIREES / COQUILLES_SIGNALEES : un appelant qui capturerait la sortie par `$(…)` la
# lirait depuis un SOUS-SHELL, où les compteurs mourraient avec lui. Même dispositif que
# RETRAIT_ERREUR, et pour la même raison — un shell ne rend qu'un code de retour.
COQUILLES_RAPPORT=""
COQUILLES_RETIREES=0
COQUILLES_SIGNALEES=0
ramasse_coquilles() {
  local principal="$1" check="$2" chemin etat ligne
  COQUILLES_RAPPORT=""
  COQUILLES_RETIREES=0
  COQUILLES_SIGNALEES=0
  while IFS=$'\t' read -r chemin etat; do
    [ -n "$chemin" ] || continue
    case "$etat" in
      porteur)
        COQUILLES_SIGNALEES=$((COQUILLES_SIGNALEES + 1))
        ligne="$(alerte "dossier qu'aucun worktree ne revendique : $(chemin_natif "$chemin")")" ;;
      *)
        if [ "$check" = 1 ]; then
          COQUILLES_RETIREES=$((COQUILLES_RETIREES + 1))
          ligne="$(printf '  → coquille vide à écarter : %s' "$(chemin_natif "$chemin")")"
        elif rmdir "$chemin" 2>/dev/null; then
          COQUILLES_RETIREES=$((COQUILLES_RETIREES + 1))
          ligne="$(ok "coquille vide écartée : $(chemin_natif "$chemin")")"
        else
          COQUILLES_SIGNALEES=$((COQUILLES_SIGNALEES + 1))
          ligne="$(alerte "coquille vide impossible à retirer (dossier tenu ?) : $(chemin_natif "$chemin")")"
        fi ;;
    esac
    COQUILLES_RAPPORT="$COQUILLES_RAPPORT$ligne"$'\n'
  done <<< "$(coquilles "$principal")"
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

# --- L'atelier d'une session (#307) ------------------------------------------------------------------
# atelier_session <worktree> : l'endroit DÉSIGNÉ où une session écrit ses fichiers de travail —
# description de PR, corps de commentaire, sortie intermédiaire qu'elle veut relire (#307).
#
# Il est DANS le worktree parce que c'est la seule façon de l'atteindre EN CHEMIN RELATIF : les deux
# endroits qu'une session connaît spontanément — son répertoire temporaire et `/tmp` — sont hors du
# répertoire de travail, et tout appel qui les vise est refusé sans que personne soit là pour
# approuver. C'est la cause n°1 des refus des sessions autonomes (9 sur 12 du dernier run complet,
# docs/10 §11.7), et elle ne se réglait pas par l'allowlist : une règle de préfixe ne borne pas une
# cible. Créé plutôt que laissé au `mkdir` de chaque session : un endroit désigné qui n'existe pas
# ne se distingue pas d'une consigne.
#
# `.maestro/` est gitignoré, et le sous-dossier suit la convention de §8.5 (`.maestro/<domaine>/`).
# Rien ne l'efface : contrairement au filet CI, une note de travail vaut d'être relue au tour
# suivant, et le worktree part en entier quand le ticket est soldé (§9.2).
atelier_session() {
  mkdir -p "$1/.maestro/session" 2>/dev/null
}

# --- Ce que la relocalisation coûte à l'onglet VS Code (#424) --------------------------------------
# Claude Code range le transcript d'une session sous son RÉPERTOIRE COURANT (§9.7), et le déplace
# quand ce répertoire change : `/ticket-start` relocalise la session dans le worktree, donc le
# transcript quitte le dossier de projet du clone principal — mesuré en direct le 2026-08-22, dix
# lignes `{"type":"relocated"}` posées dans le fichier, un seul exemplaire, sous le worktree.
#
# Or un onglet VS Code se rebranche sur sa conversation en cherchant son identifiant dans la liste
# des sessions de SON dossier, worktrees exclus (`listSessions({dir, includeWorktrees:false})` de
# l'extension 2.1.238). Ne l'y trouvant plus, il ouvre une conversation NEUVE, sans un mot — c'est
# tout le symptôme, et c'est ici qu'il naît, pas au ramassage du worktree (qui, lui, ne touche pas
# aux transcripts et laisse `--resume` fonctionner).
#
# On le dit donc à l'instant du départ, à côté des ports : c'est le seul moment où quelqu'un a la
# question sous les yeux. Après coup, l'onglet vide ne rappelle rien.
#
# L'identifiant vient de `CLAUDE_CODE_SESSION_ID`, que Claude Code pose dans l'environnement de ses
# sous-processus — donc juste, et jamais deviné. Absent (appel hors session : `run.sh`, un terminal
# nu), on renvoie vers l'inventaire du ticket plutôt que d'inventer un identifiant.
session_qui_part() {
  local iid="$1" id="${CLAUDE_CODE_SESSION_ID:-}"
  printf '  ⚠ la conversation de cette session quitte le dossier courant : au prochain\n'
  printf '    démarrage de VS Code, son onglet repartira vide (un onglet ne cherche que\n'
  printf '    dans le dossier de son espace de travail, worktrees exclus — §9.7).\n'
  if [ -n "$id" ]; then
    printf '    y revenir : claude --resume %s\n' "$id"
  else
    printf '    y revenir : bash scripts/git/worktree.sh sessions %s\n' "$iid"
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
      # L'outil est demandé à lib.sh, pas écrit en dur (#341) : un message qui nomme le mauvais CLI
      # fait chercher la panne du mauvais côté. L'appel supplémentaire ne coûte que
      # sur le chemin d'échec, où l'on a déjà perdu bien plus qu'un sous-processus.
      erreur "branche introuvable pour #$iid ($(bash "$ICI/../gitlab/lib.sh" forge-cli) authentifié ?) — sinon : --branche <nom>"
      return 1
    }
  fi
  case "$branche" in
    *'<type>'*)
      erreur "ticket #$iid sans label type:: — poser le label, ou imposer --branche <nom>"
      return 1 ;;
  esac

  # 2) Emplacement : `.claude/worktrees/` du clone principal, qui regroupe tous les worktrees — le
  #    seul emplacement où le CLI entre sans demander de validation (#847, voir `base_worktrees`).
  local base dest nom
  base="$(base_worktrees "$principal")"
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
  else
    # Une COQUILLE VIDE laissée par un retrait précédent (#422) n'est pas un obstacle : elle ne
    # porte rien, et c'est le retrait de son propre worktree qui l'a laissée là. La refuser
    # revenait à barrer le remontage du ticket pour un dossier de zéro octet. Un dossier qui porte
    # QUELQUE CHOSE reste refusé — c'est le garde-fou d'origine, et il ne bouge pas.
    if [ -d "$dest" ] && [ -z "$(ls -A "$dest" 2>/dev/null)" ]; then
      if rmdir "$dest" 2>/dev/null; then
        ignore "coquille vide d'un retrait précédent écartée"
      else
        erreur "coquille vide impossible à retirer (dossier tenu par un processus ?) : $dest"
        return 1
      fi
    fi
    if [ -e "$dest" ]; then
      erreur "$dest existe déjà sans être un worktree — le retirer ou choisir un autre emplacement"
      return 1
    fi
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

  # 7) L'atelier de la session (#307), cf. `atelier_session`.
  if atelier_session "$dest"; then
    ok "atelier de session : .maestro/session/ (fichiers de travail, en chemin relatif)"
  else
    ignore ".maestro/session/ non créé — la session le fera au besoin (mkdir -p .maestro/session)"
  fi

  # 8) Les hooks git n'ont rien à installer : core.hooksPath est une configuration du dépôt (donc
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
    session_qui_part "$iid"
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
      # L'outil est demandé à lib.sh, pas écrit en dur (#341) : un message qui nomme le mauvais CLI
      # fait chercher la panne du mauvais côté. L'appel supplémentaire ne coûte que
      # sur le chemin d'échec, où l'on a déjà perdu bien plus qu'un sous-processus.
      erreur "branche introuvable pour #$iid ($(bash "$ICI/../gitlab/lib.sh" forge-cli) authentifié ?) — sinon : --branche <nom>"
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
  local sortie_sync code_sync fetch_fait=0
  if [ "${MAESTRO_SYNC_MAIN:-1}" != 0 ]; then
    sortie_sync="$(bash "$ICI/../gitlab/lib.sh" sync-main 2>&1)"
    code_sync=$?
    fetch_fait=1
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
  # suivra : c'est le seul moment où quelqu'un passe par ici à coup sûr. Best-effort et muet quand
  # il n'y a rien à dire — un ramassage qui échoue ne doit pas empêcher un ticket de démarrer. En
  # `--auto` il n'écrit que des lignes de compte rendu, jamais un verdict : le contrat « dernière
  # ligne de stdout » d'`ensure` reste tenu.
  # `--sauf "$iid"` : le ticket qu'on est en train de démarrer est repris à l'instant même, et son
  # worktree peut très bien dormir depuis la veille — le signaler orphelin serait vrai une seconde
  # et faux la suivante (#328).
  if [ "${MAESTRO_WORKTREE_GC:-1}" != 0 ]; then
    commande_gc --auto --sauf "$iid" || true
  fi

  # Purge des branches locales mergées (#23), APRÈS le ramassage ci-dessus — l'ordre n'est pas
  # cosmétique : `git branch -D` refuse une branche empruntée par un worktree, donc la branche d'un
  # ticket soldé n'est supprimable qu'une fois son worktree parti.
  #
  # Le pendant, pour les branches, de ce que les trois blocs précédents font à `main` (#205), aux
  # dépendances (#216) et aux worktrees (#197) — et le dernier de la famille à être recâblé ici
  # (#305). Il vivait dans `lib.sh start-branch`, appelé à l'étape suivante de /ticket-start, mais
  # depuis #181 la session est déjà relocalisée quand cet appel arrive : `start-branch` sort par
  # « déjà sur la branche » ou par sa voie worktree, jamais par celle qui purgeait. Plus rien ne
  # supprimait de branche sans un /branch-cleanup manuel, et 35 s'étaient accumulées.
  #
  # Best-effort et muet quand il n'y a rien à faire (`--auto`), comme les trois autres. Coûte une
  # lecture de forge par branche locale — l'ordre de grandeur du ramassage juste au-dessus, qui en
  # fait une par worktree, et c'est justement parce que la purge tourne à nouveau que ce nombre
  # reste petit. `MAESTRO_PURGE_BRANCHES=0` pour l'éteindre.
  #
  # Seul stdout est repris, réindenté au niveau du rapport (les lignes du helper sont conçues pour
  # être lues seules) ; ses abstentions partent sur stderr et y restent, comme celles de sync-main.
  #
  # `--sans-fetch` quand `sync-main` vient de tourner (#602) : son `fetch --prune` est du pruning
  # cosmétique — la décision s'appuie sur l'état de la PR côté forge, jamais sur lui — et
  # `sync-main`, quelques lignes plus haut, a déjà rafraîchi les refs. C'était ~5 s de doublon à
  # chaque /ticket-start. Le drapeau suit ce qui s'est RÉELLEMENT passé et non la valeur par défaut :
  # avec `MAESTRO_SYNC_MAIN=0`, personne n'a fetché et la purge doit le faire pour son compte.
  local sortie_purge
  local -a args_purge=(--auto)
  [ "$fetch_fait" = 1 ] && args_purge+=(--sans-fetch)
  if [ "${MAESTRO_PURGE_BRANCHES:-1}" != 0 ]; then
    sortie_purge="$(bash "$ICI/../gitlab/lib.sh" cleanup-merged "${args_purge[@]}")" || true
    [ -n "$sortie_purge" ] && printf '%s\n' "$sortie_purge" | sed 's/^ *//; s/^/  /'
  fi

  # Déjà au bon endroit ? Le test porte sur la BRANCHE du répertoire courant, jamais sur son
  # chemin : sous Windows git répond « E:/… » là où Git Bash répond « /e/… », et une comparaison
  # de chemins passerait à côté (même piège que dans `commande_remove`).
  local courante racine
  courante="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
  if [ -n "$courante" ] && [ "$courante" = "$branche" ]; then
    racine="$(git rev-parse --path-format=absolute --show-toplevel 2>/dev/null)" || racine="$(pwd)"
    # `commande_create` ne sera pas rejoué sur cette voie : l'atelier est complété ici, sans un mot.
    # Sans ça, un worktree monté avant #307 n'en aurait jamais, et la consigne du prompt renverrait
    # vers un répertoire absent — ce qui est pire qu'une consigne absente.
    atelier_session "$racine" || true
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

  # Ce que la boucle ci-dessus ne peut pas montrer : les dossiers que `git worktree list` ne
  # connaît plus (#422). Les nommer ICI est la moitié du remède — l'autre est que `gc` les écarte ;
  # sans les deux, elles ne réapparaissent qu'au moment où elles bloquent un remontage.
  local etat inconnus=""
  while IFS=$'\t' read -r chemin etat; do
    [ -n "$chemin" ] || continue
    case "$etat" in
      porteur) inconnus="$inconnus$(printf '  %-50s %s' "$chemin" "(dossier inconnu, non vide)")"$'\n' ;;
      *)       inconnus="$inconnus$(printf '  %-50s %s' "$chemin" "(coquille vide — worktree.sh gc)")"$'\n' ;;
    esac
  done <<< "$(coquilles "$principal")"
  [ -n "$inconnus" ] && printf '\nAucun worktree ne les revendique :\n\n%s' "$inconnus"
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
  elif [ "$RETRAIT_DESENREGISTRE" = 1 ]; then
    # Le worktree est parti, le dossier résiste : ce n'est pas un retrait à retenter (git ne le
    # connaît plus), c'est un dossier à supprimer quand ce qui le tient l'aura lâché.
    erreur "worktree désenregistré, mais son dossier résiste :"
    printf '%s\n' "$RETRAIT_ERREUR" >&2
    printf '  Il ne reste qu'\''une coquille vide : %s\n' "$(chemin_natif "$chemin")" >&2
    printf '  Elle sera écartée au prochain ramassage (worktree.sh gc), ou à la main.\n' >&2
    return 1
  else
    erreur "git worktree remove a échoué :"
    printf '%s\n' "$RETRAIT_ERREUR" >&2
    printf '  Les liens ont déjà été retirés : le clone principal ne risque plus rien si le\n' >&2
    printf '  dossier doit être supprimé à la main.\n' >&2
    return 1
  fi
}

# --- sessions : retrouver les transcripts d'un ticket (#385) ----------------------------------------
# Claude Code range le transcript d'une session dans un répertoire de projet INDEXÉ SUR LE RÉPERTOIRE
# COURANT (`<config>/projects/<chemin encodé>/<session-id>.jsonl`), et son sélecteur `/resume` ne
# montre que le répertoire d'où on l'appelle. Or /ticket-start relocalise la session dans le worktree
# du ticket (#181) : l'historique d'un ticket est rangé sous le chemin du WORKTREE, donc invisible
# depuis le clone principal — puis `gc` retire le worktree et l'on ne peut même plus y revenir en
# `cd`. Au constat du 2026-08-19 : 157 transcripts (183 Mo) dans 134 répertoires de projet, pour 13
# worktrees encore sur le disque.
#
# Rien n'est perdu : c'est l'ADRESSAGE qui manque, et il se DÉRIVE. L'encodage de Claude Code
# remplace `:`, `\`, `/` et l'espace par `-`, sans rien tronquer, donc le répertoire de projet d'un
# ticket est `<base des worktrees encodée>-<iid>-<slug>` — un motif sur l'iid suffit, le slug n'est
# jamais nécessaire. C'est ce qui rend AUSSI les tickets dont le worktree est parti depuis
# longtemps ; un index qu'on aurait posé au moment du ramassage ne couvrirait, lui, que les
# ramassages postérieurs à sa mise en place, et laisserait dehors les 121 déjà partis.
#
# La reprise se fait par IDENTIFIANT — `claude --resume <id>` court-circuite le sélecteur, donc son
# cloisonnement par répertoire. C'est tout ce que ce verbe a besoin de rendre.
#
# PORTÉE : les worktrees de CETTE MACHINE, comme `gc` et `reconcile-workflow`. Un transcript vit sur
# le poste qui l'a produit ; ce verbe ne va rien chercher ailleurs, et l'annonce.

# La configuration de Claude Code, où qu'elle soit — CLAUDE_CONFIG_DIR est aussi la couture par
# laquelle les tests la font pointer sur un dossier jetable. Les DEUX sources lues ici en dépendent,
# transcripts et registre : deux formules à tenir d'accord finiraient par lire des noms appartenant
# à une autre installation que les transcripts affichés.
sessions_config() {
  printf '%s' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
}

# Le répertoire de projets, où vivent les transcripts.
sessions_racine() {
  printf '%s/projects' "$(sessions_config)"
}

# La base des worktrees — CELLE de `create` (`base_worktrees`), et non une seconde formule à tenir
# d'accord : jusqu'à #847 la même expression vivait ici en copie, et déplacer la base aurait fait
# chercher les transcripts là où plus rien n'est monté.
sessions_base() {
  local principal
  principal="$(depot_principal)" || return 1
  base_worktrees "$principal"
}

# L'encodage d'un chemin par Claude Code : TOUT caractère hors `a-zA-Z0-9` devient « - » — le CLI
# fait `replace(/[^a-zA-Z0-9]/g, "-")`, lu dans son binaire et vérifié sur ce poste
# (`…-Maestro--claude-worktrees-<iid>-…` : le « . » de `.claude` y devient un tiret, comme « : »,
# « \ », « / » et l'espace — ce que l'ancienne liste de quatre caractères ne couvrait pas, #847).
# Le chemin doit être NATIF (« E:\… ») : c'est sous cette forme que la session le reçoit, donc sous
# cette forme qu'il a été encodé — l'encoder depuis le « /e/… » de Git Bash donnerait un nom qui ne
# matche rien. `LC_ALL=C` fait porter le complément sur des OCTETS : un caractère accentué (deux
# octets en UTF-8) rendrait deux tirets là où le CLI n'en met qu'un — sans objet pour les chemins
# de worktrees du dépôt, dont le slug est ASCII, et nommé plutôt que découvert.
sessions_encode() {
  printf '%s' "$(chemin_natif "$1")" | LC_ALL=C tr -c 'a-zA-Z0-9' '-'
}

# La base des worktrees ainsi encodée : le préfixe commun aux répertoires de projet des tickets.
sessions_prefixe() {
  local base
  base="$(sessions_base)" || return 1
  sessions_encode "$base"
}

# sessions_bucket_ici : le répertoire de projet du RÉPERTOIRE COURANT (#397) — le seul que le
# sélecteur `/resume` montrerait ici. C'est ce qu'interroge `sessions` sans argument : « je rouvre
# VS Code dans ce dossier, qu'est-ce que je reprends ? » se pose de partout, clone principal
# compris, là où la dérivation par iid ne couvre que les worktrees.
sessions_bucket_ici() {
  local racine motif
  racine="$(sessions_racine)"
  [ -d "$racine" ] || return 0
  motif="$(sessions_encode "$PWD")"
  find "$racine" -maxdepth 1 -type d -iname "$motif" 2>/dev/null | head -1
}

# sessions_titre <transcript> : son titre lisible — la DERNIÈRE entrée `ai-title`, le titre étant
# réévalué en cours de session. Beaucoup de sessions n'en portent aucune (trop courte, ou coupée
# avant que le titre soit posé) : on rend alors le vide, à l'appelant de replier. Un guillemet
# échappé dans le titre le tronquerait — un titre court vaut mieux qu'un `jq` en dépendance.
sessions_titre() {
  grep -o '"aiTitle":[[:space:]]*"[^"]*"' "$1" 2>/dev/null | tail -1 \
    | sed 's/^"aiTitle":[[:space:]]*"//; s/"$//'
}

# --- Le registre des sessions : « quel onglet était-ce ? » (#397) -----------------------------------
# Claude Code laisse une fiche par session sous `<config>/sessions/<PID>.json` — identifiant, dossier,
# heure de démarrage, et le NOM que l'onglet VS Code affichait. Elle est indexée par PID, donc elle ne
# dit plus rien de ce qui TOURNE une fois les processus morts ; mais ce n'est pas la question posée
# ici, et le reste ne périme pas. C'est la seule source du nom, qu'aucun transcript ne porte — et
# c'est le repère par lequel on reconnaît son onglet d'hier.
#
# sessions_texte <fichier> <clé> : la valeur texte d'une clé, dans un JSON à plat écrit sans espaces
# — la forme du registre. « name » ne matche pas « nameSource » : le motif exige le guillemet qui
# ferme la clé.
sessions_texte() {
  grep -o "\"$2\":\"[^\"]*\"" "$1" 2>/dev/null | head -1 | sed "s/^\"$2\":\"//; s/\"\$//"
}

# sessions_registre : la table « identifiant <TAB> nom », chaque identifiant une seule fois. Une
# session REPRISE garde son identifiant sous un nouveau PID (jusqu'à trois fiches pour un même id sur
# la machine de référence) : le nom retenu est celui de la fiche la plus récente, jamais le premier
# venu — c'est le dernier nom vu à l'écran qu'on cherche à reconnaître.
sessions_registre() {
  local racine f id nom debut lignes=""
  racine="$(sessions_config)/sessions"
  [ -d "$racine" ] || return 0
  for f in "$racine"/*.json; do
    [ -e "$f" ] || continue
    id="$(sessions_texte "$f" sessionId)"
    nom="$(sessions_texte "$f" name)"
    [ -n "$id" ] && [ -n "$nom" ] || continue
    debut="$(grep -o '"startedAt":[0-9]*' "$f" 2>/dev/null | head -1 | cut -d: -f2)"
    lignes="$lignes${debut:-0}"$'\t'"$id"$'\t'"$nom"$'\n'
  done
  [ -n "$lignes" ] || return 0
  printf '%s' "$lignes" | sort -rn | awk -F'\t' '!vu[$2]++ { print $2 "\t" $3 }'
}

# La table du registre, chargée une fois par invocation : un `grep` par session rendue coûterait un
# parcours du registre entier à chaque ligne affichée.
declare -A SESSIONS_NOMS=()
sessions_charge_noms() {
  local id nom
  while IFS=$'\t' read -r id nom; do
    [ -n "$id" ] && SESSIONS_NOMS["$id"]="$nom"
  done < <(sessions_registre)
}

# sessions_du_bucket <répertoire> : « <epoch><TAB><date><TAB><id><TAB><titre> » par transcript, du
# plus récent au plus ancien. La date de modification est le seul repère fiable : un transcript
# n'embarque pas sa propre fin, et la première ligne n'est pas toujours horodatée.
sessions_du_bucket() {
  local dossier="$1" f id titre epoch date lignes=""
  for f in "$dossier"/*.jsonl; do
    [ -e "$f" ] || continue
    id="$(basename "$f" .jsonl)"
    titre="$(sessions_titre "$f")"
    epoch="$(stat -c '%Y' "$f" 2>/dev/null)" || epoch=""
    date="$(stat -c '%y' "$f" 2>/dev/null | cut -c1-16)"
    # Un champ VIDE au milieu décalerait tous les suivants : `IFS=$'\t' read` traite la tabulation
    # comme un séparateur BLANC, donc deux d'affilée ne comptent que pour une (même piège que le
    # « - » du sha dans `worktree-done`). Seul le titre, dernier champ, peut rester vide.
    lignes="$lignes${epoch:-0}"$'\t'"${date:--}"$'\t'"$id"$'\t'"$titre"$'\n'
  done
  [ -n "$lignes" ] || return 0
  printf '%s' "$lignes" | sort -rn
}

# sessions_buckets [<iid>] : les répertoires de projet des worktrees, un par ligne.
#
# Le motif est joué par `find -iname` et non par le glob du shell, parce que la casse de la LETTRE
# DE LECTEUR n'est pas garantie : Claude Code encode le chemin TEL QU'IL LUI A ÉTÉ DONNÉ, sans le
# normaliser, si bien que le clone principal est rangé sous « e-- » et ses worktrees sous « E-- »
# sur cette machine. Un motif sensible à la casse en manquerait la moitié, silencieusement.
sessions_buckets() {
  local iid="${1:-}" racine prefixe motif
  racine="$(sessions_racine)"
  [ -d "$racine" ] || return 0
  prefixe="$(sessions_prefixe)" || return 1
  if [ -n "$iid" ]; then motif="$prefixe-$iid-*"; else motif="$prefixe-*"; fi
  find "$racine" -maxdepth 1 -type d -iname "$motif" 2>/dev/null | sort
}

# sessions_compte <iid> : combien de transcripts ce ticket a laissés (0 si aucun). Utilisé par `gc`,
# qui n'a besoin que du nombre — pas de la liste, qu'il n'a pas la place d'afficher.
sessions_compte() {
  local iid="$1" dossier n total=0
  while IFS= read -r dossier; do
    [ -n "$dossier" ] || continue
    n="$(find "$dossier" -maxdepth 1 -name '*.jsonl' 2>/dev/null | grep -c .)" || n=0
    total=$((total + n))
  done < <(sessions_buckets "$iid")
  printf '%s' "$total"
}

# Combien de sessions le mode par défaut imprime. Dix, parce que la question qu'il sert — « qu'est-ce
# que je reprends ici ? » — porte sur les dernières : le clone principal de référence compte 192
# transcripts, soit 390 lignes de sortie, où la conversation d'hier est aussi perdue qu'avant.
# `--limite 0` rend tout ; MAESTRO_SESSIONS_LIMITE déplace le défaut.
SESSIONS_LIMITE="${MAESTRO_SESSIONS_LIMITE:-10}"
SESSIONS_RENDUES=0
SESSIONS_TOTAL=0
declare -A SESSIONS_VUES=()

# sessions_rend_bucket <répertoire> [limite] : les sessions d'un répertoire de projet, la plus
# récente d'abord, au plus `limite` (0 = toutes). Les comptes partent dans SESSIONS_TOTAL et
# SESSIONS_RENDUES et non par la sortie standard, que l'affichage occupe déjà — et ils sont DEUX,
# parce qu'une liste tronquée qui ne dit pas ce qu'elle tait se lit comme une liste complète.
# Au-delà de la limite on compte sans imprimer : le parcours a lieu de toute façon.
#
# Un identifiant n'apparaît qu'une fois par répertoire — un transcript est un fichier, nommé par cet
# identifiant — mais peut se retrouver dans deux répertoires quand une session a été reprise
# ailleurs : SESSIONS_VUES le dédoublonne à l'échelle de l'invocation.
sessions_rend_bucket() {
  local dossier="$1" limite="${2:-0}" date id titre nom
  # L'epoch n'est là que pour trier : lu dans `_`, il n'a pas à porter de nom.
  while IFS=$'\t' read -r _ date id titre; do
    [ -n "$id" ] || continue
    [ -n "${SESSIONS_VUES[$id]:-}" ] && continue
    SESSIONS_VUES["$id"]=1
    SESSIONS_TOTAL=$((SESSIONS_TOTAL + 1))
    if [ "$limite" -gt 0 ] && [ "$SESSIONS_RENDUES" -ge "$limite" ]; then continue; fi
    SESSIONS_RENDUES=$((SESSIONS_RENDUES + 1))
    # Le nom de l'onglet, quand le registre le connaît : c'est par lui qu'on reconnaît la session
    # qu'on cherche, un titre pouvant être absent ou trompeur (il est posé en cours de route).
    nom="${SESSIONS_NOMS[$id]:-}"
    # La commande de reprise sur SA propre ligne, alignée sous le titre : elle est faite pour être
    # sélectionnée d'un coup, ce qu'une ligne mêlant date, titre et commande interdirait.
    printf '  %-16s  %s%s\n' "$date" "${nom:+[$nom] }" "${titre:-(sans titre)}"
    printf '  %-16s  claude --resume %s\n' '' "$id"
  done < <(sessions_du_bucket "$dossier")
}

# sessions_ici : le mode par défaut (#397) — les sessions du RÉPERTOIRE COURANT, celles que `/resume`
# montrerait ici et qu'un redémarrage de VS Code laisse sans adresse (l'extension ne persiste aucune
# identité de session : sa clé `sessionGroups:<hash>` est absente du `state.vscdb`, et son « Reopen
# Closed Session » travaille sur une pile en mémoire).
sessions_ici() {
  local limite="${1:-0}" bucket ailleurs
  bucket="$(sessions_bucket_ici)"

  printf '\nSessions Claude Code — %s\n' "$(chemin_natif "$PWD")"
  printf '  (ce dossier ; c'\''est tout ce que le sélecteur /resume y montre)\n\n'

  [ -n "$bucket" ] && sessions_rend_bucket "$bucket" "$limite"

  if [ "$SESSIONS_TOTAL" -eq 0 ]; then
    printf '  aucune session enregistrée pour ce dossier.\n'
  fi
  printf '\n%s session(s) ici.\n' "$SESSIONS_TOTAL"
  # Une liste bornée le DIT : sans ça, « 8 sessions » se lirait comme un inventaire complet alors
  # que le clone principal en compte 192, et la conversation cherchée serait tenue pour perdue.
  if [ "$SESSIONS_TOTAL" -gt "$SESSIONS_RENDUES" ]; then
    printf '  %s plus anciennes non listées : worktree.sh sessions --limite 0\n' \
      "$((SESSIONS_TOTAL - SESSIONS_RENDUES))"
  fi

  # Le renvoi vers les sessions de tickets : depuis le clone principal, c'est là qu'est le gros du
  # travail, et rien d'autre ne le dirait — le mode par défaut ne regarde qu'un seul répertoire.
  ailleurs="$(sessions_buckets | grep -c .)" || ailleurs=0
  if [ "${ailleurs:-0}" -gt 0 ]; then
    printf '  %s ticket(s) en ont aussi, dans leur worktree : worktree.sh sessions --tous\n' "$ailleurs"
    # La CAUSE, pas seulement le compte (#424) : ces sessions ont commencé ici et sont parties
    # avec leur worktree. C'est la même absence qui fait repartir vide l'onglet VS Code d'un
    # ticket, et sans cette ligne le renvoi se lit comme un simple « il y en a ailleurs ».
    printf '    (parties d'\''ici avec leur session — c'\''est pourquoi leur onglet repart vide)\n'
  fi
  printf '\n'
  return 0
}

# sessions_par_ticket [<iid>] : les sessions d'un ticket (#385), ou de tous les tickets sans iid.
sessions_par_ticket() {
  local iid="${1:-}" base prefixe
  base="$(sessions_base)" || { erreur "hors d'un dépôt git"; return 1; }
  prefixe="$(sessions_prefixe)" || { erreur "hors d'un dépôt git"; return 1; }

  local -a buckets=()
  local d
  while IFS= read -r d; do
    [ -n "$d" ] && buckets+=("$d")
  done < <(sessions_buckets "$iid")

  if [ "${#buckets[@]}" -eq 0 ]; then
    if [ -n "$iid" ]; then
      printf '\nAucune session pour le ticket #%s sur cette machine.\n' "$iid"
      printf '  (les transcripts d'\''un ticket vivent sur le poste qui l'\''a traité)\n\n'
    else
      printf '\nAucune session de worktree sur cette machine.\n\n'
    fi
    return 0
  fi

  printf '\nSessions Claude Code — %s\n' "$(chemin_natif "$base")"
  printf '  (worktrees de cette machine ; le sélecteur /resume ne les voit pas d'\''ailleurs)\n'

  local nom suffixe t_iid
  for d in "${buckets[@]}"; do
    nom="$(basename "$d")"
    # Le suffixe se prend à la LONGUEUR du préfixe, pas par retrait de motif : la casse du préfixe
    # peut différer de celle du répertoire (voir sessions_buckets), et « ${nom#$prefixe-} » ne
    # retirerait alors rien du tout — on afficherait le chemin encodé entier en guise d'iid.
    suffixe="${nom:$(( ${#prefixe} + 1 ))}"
    t_iid="${suffixe%%-*}"
    case "$t_iid" in ''|*[!0-9]*) continue ;; esac

    if [ -d "$base/$suffixe" ]; then
      printf '\n#%s — worktree en place\n' "$t_iid"
    else
      printf '\n#%s — worktree ramassé, transcripts conservés\n' "$t_iid"
    fi
    sessions_rend_bucket "$d"
  done

  printf '\n%s session(s).\n\n' "$SESSIONS_TOTAL"
  return 0
}

# commande_sessions [<iid>|--tous] : sans argument, les sessions de CE dossier (#397) ; avec un iid,
# celles d'un ticket (#385) ; `--tous` pour l'inventaire de tous les tickets.
#
# Le défaut a changé à #397, et le geste le plus court répond désormais à la question la plus
# fréquente : celle qu'on se pose en rouvrant VS Code, là où on est. L'inventaire, lui, se demande —
# il répond à « où sont passées mes sessions de tickets ? », qui vient plus rarement et plus tard.
commande_sessions() {
  local iid="" tous=0 limite="$SESSIONS_LIMITE"
  while [ $# -gt 0 ]; do
    case "$1" in
      -h|--help) usage; return 0 ;;
      --tous) tous=1 ;;
      --limite)
        shift
        case "${1:-}" in
          ''|*[!0-9]*) erreur "--limite attend un nombre (0 = toutes), reçu « ${1:-} »"; return 2 ;;
        esac
        limite="$1" ;;
      -*) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; return 2 ;;
      *)
        case "$1" in
          ''|*[!0-9]*) erreur "iid attendu (un nombre), reçu « $1 »"; return 2 ;;
        esac
        iid="$1" ;;
    esac
    shift
  done
  if [ -n "$iid" ] && [ "$tous" -eq 1 ]; then
    erreur "--tous porte sur tous les tickets : il ne se combine pas avec un iid"
    return 2
  fi

  local racine
  racine="$(sessions_racine)"
  if [ ! -d "$racine" ]; then
    printf '\nAucun historique de session sur cette machine (%s est absent).\n\n' "$racine"
    return 0
  fi

  sessions_charge_noms
  if [ -z "$iid" ] && [ "$tous" -eq 0 ]; then
    sessions_ici "$limite"
  else
    sessions_par_ticket "$iid"
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
#   3. `origin/main` en dernier recours — cas d'une branche JAMAIS poussée (ticket fermé sans PR),
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

# --- Qui occupe un worktree ? (#503) ----------------------------------------------------------------
# `gc` refuse depuis toujours de retirer le worktree de la SESSION COURANTE — mais « courante » veut
# dire *celle qui appelle*, pas *celles qui vivent sur la machine* : la protection est locale à
# l'appelant alors que le danger est global au poste. Un run `/orchestrate` qui démarre son ticket
# suivant joue `ensure` → `gc`, et balaie les worktrees soldés des AUTRES sessions, y compris celles
# qui sont en train de travailler dedans. Observé le 2026-08-24 : le worktree de #497 vidé sous une
# session interactive qui venait d'en merger la PR et y était restée, découverte huit minutes plus
# tard sur un « run.sh: No such file or directory » qui ne nommait pas sa cause.
#
# Le vrai défaut est l'ORDRE, et pas le ramassage : on supprime le contenu, PUIS on découvre que le
# dossier est tenu (« Permission denied » sous Windows) — et `git worktree remove` désenregistre
# quand même. La coquille de #422 est donc l'issue NORMALE de ce scénario et non un cas limite,
# alors que la même information — « quelqu'un est dedans » — était disponible AVANT.
#
# Deux sources, et il en faut deux : aucune ne voit ce que l'autre voit.
#   A. les processus dont le RÉPERTOIRE COURANT est le worktree (`/proc/<pid>/cwd`). Canonique sous
#      Linux ; sous MSYS `/proc` ne liste que les processus MSYS et ceux qu'ils ont lancés — donc le
#      `claude.exe` d'une session de run (vérifié le 2026-08-25), mais PAS celui d'un onglet VS Code,
#      que l'extension lance hors de tout shell.
#   B. le REGISTRE de Claude Code (`<config>/sessions/<PID>.json`, déjà lu par `sessions` #397), qui
#      porte le `cwd`, le `pid` et le `sessionId` de chaque session. C'est lui, et lui seul, qui voit
#      l'onglet VS Code au repos — c'est-à-dire exactement la victime de l'incident.
#
# L'asymétrie des erreurs décide du reste : un FAUX POSITIF coûte un worktree qui survit un tour de
# plus (le passage suivant le reprendra), un FAUX NÉGATIF coûte son répertoire courant à une session
# vivante. On ratisse donc large, et on ne cherche pas à démasquer les PID recyclés — le `procStart`
# du registre le permettrait, pour un gain qui ne vaut pas sa complexité du bon côté de l'asymétrie.
#
# MAESTRO_WORKTREE_OCCUPE remplace la détection par une commande qui reçoit le chemin et imprime
# l'occupant (rien = libre) : même couture que MAESTRO_WORKTREE_VERDICT. `0` l'éteint, comme
# MAESTRO_WORKFLOW_POSE — de quoi débloquer un poste où la détection se tromperait.

# pids_de_soi : la chaîne des ancêtres du processus courant, soi compris.
#
# « On ne se compte pas soi-même » généralise le refus qui existait déjà (le worktree de la session
# courante), et #519 l'a rendu nécessaire : /ticket-finish sort du worktree par `ExitWorktree` PUIS
# joue `gc --iid` depuis le clone principal — si le `claude.exe` de la session gardait le worktree
# pour répertoire courant, elle s'interdirait à elle-même le ramassage qu'elle vient d'organiser.
#
# Lecture de `/proc/<pid>/stat` sans fork, même découpage que `pilote.sh` : le nom de la commande est
# entre parenthèses et peut contenir des espaces, on coupe donc APRÈS la dernière parenthèse
# fermante. Bornée à 32 remontées — une table de processus incohérente ne doit pas faire boucler un
# ramassage. Sans `/proc` (Windows hors MSYS), on ne rend que soi, ce qui suffit : la source A n'a
# alors rien à balayer non plus.
#
# ⚠ `2>/dev/null` passe AVANT la redirection d'entrée, et l'ordre est le contenu de la ligne : c'est
# le SHELL qui écrit « No such file or directory » quand une redirection échoue, avant même que
# `read` s'exécute — la taire demande que stderr soit déjà détourné à ce moment-là. La chaîne
# s'arrête normalement sur un ppid que `/proc` ne connaît pas (sous MSYS elle atteint le pid 1, qui
# n'y figure pas), donc cet échec est le cas NOMINAL et non une anomalie à rapporter.
pids_de_soi() {
  local pid=$$ n=0 s ppid
  while [ "$n" -lt 32 ]; do
    printf ' %s' "$pid"
    read -r s 2>/dev/null <"/proc/$pid/stat" || return 0
    s="${s##*) }"                                  # « <état> <ppid> … »
    s="${s#* }"                                    # « <ppid> … »
    ppid="${s%% *}"
    case "$ppid" in '' | *[!0-9]* | 0) return 0 ;; esac
    pid="$ppid"
    n=$((n + 1))
  done
}

# occupants_proc <chemin> : les pid dont le répertoire courant EST ce worktree, hors soi-même.
#
# `[ <lien> -ef <chemin> ]` compare device+inode et non des chaînes : les trois formes du même
# dossier — « /e/… » du shell, « E:\… » natif, « E:/… » de git — y sont égales sans normalisation.
# C'est le piège de #422 rendu sans objet plutôt que traité une fois de plus. C'est aussi un test
# BUILTIN, donc sans fork : 9 ms le balayage complet contre 280 ms à coups de `readlink` (mesuré le
# 2026-08-25 sous MSYS), sur un chemin que tout /ticket-start emprunte.
#
# Portée assumée : le répertoire courant EXACT. Un processus posté dans un SOUS-dossier (`apps/web`)
# n'est pas vu — il retombe alors sur le comportement d'avant ce ticket, coquille comprise, que le
# rattrapage de #422 écarte au passage suivant. Élargir demanderait de lire le lien, donc un fork par
# processus, pour un cas qui n'est pas celui de l'incident.
#
# Deux exclusions, et il en faut deux : la chaîne des ancêtres NE SUFFIT PAS. Sous MSYS les processus
# que Claude Code lance sont réparentés sur le pid 1 (mesuré : la chaîne d'un outil `Bash` s'arrête
# avant d'atteindre le `claude.exe` de sa propre session), si bien que la session ne se reconnaîtrait
# pas elle-même. On écarte donc AUSSI le processus dont le WINPID est celui que le registre associe à
# `CLAUDE_CODE_SESSION_ID` — sans quoi une session qui vient de sortir du worktree par `ExitWorktree`
# s'interdirait le ramassage qu'elle a elle-même organisé (#519). `/proc/<pid>/winpid` est la table
# de correspondance des deux mondes, et elle se lit sans fork.
occupants_proc() {
  local chemin="$1" soi mien d pid winpid trouves=""
  [ -d /proc ] || return 0
  # `$BASHPID` est ajouté ICI et pas dans `pids_de_soi` : `$$` désigne le shell d'origine jusque dans
  # un sous-shell, or ce balayage s'exécute justement dans celui qu'ouvre `$(occupants_proc …)` — il
  # se compterait lui-même parmi les occupants, son répertoire courant étant celui de l'appelant.
  soi=" $BASHPID $(pids_de_soi) "
  charge_sessions_ouvertes
  mien="$MA_SESSION_PID"
  for d in /proc/[0-9]*; do
    pid="${d##*/}"
    case "$soi" in *" $pid "*) continue ;; esac
    if [ -n "$mien" ]; then
      if [ "$WINDOWS" = 1 ]; then
        read -r winpid 2>/dev/null <"$d/winpid" && [ "$winpid" = "$mien" ] && continue
      elif [ "$pid" = "$mien" ]; then
        continue
      fi
    fi
    [ "$d/cwd" -ef "$chemin" ] || continue
    trouves="$trouves $pid"
  done
  printf '%s' "$trouves"
}

# processus_vivant <pid> : ce numéro désigne-t-il encore quelque chose ?
#
# Deux mondes sous Windows (même constat que `pilote.sh`) : le registre enregistre le PID WINDOWS
# d'un `claude.exe` natif, que `kill -0` n'atteint pas et que `/proc` ne liste pas — pire, il
# répondrait sur un processus MSYS sans rapport qui porterait le même numéro. `ps -W` est la seule
# vue des deux mondes, son WINPID est en 4e colonne, et il se lit UNE fois par invocation (84 ms pour
# 392 lignes sur le poste de référence).
WINPIDS_VIVANTS=""
processus_vivant() {
  local pid="$1"
  case "$pid" in '' | *[!0-9]*) return 1 ;; esac
  if [ "$WINDOWS" = 1 ]; then
    [ -n "$WINPIDS_VIVANTS" ] ||
      WINPIDS_VIVANTS=" $(ps -W 2>/dev/null | awk 'NR > 1 { printf "%s ", $4 }') "
    case "$WINPIDS_VIVANTS" in *" $pid "*) return 0 ;; *) return 1 ;; esac
  fi
  # Ailleurs l'entrée de /proc suffit et ne coûte rien ; `kill -0`, gardé en repli, refuserait le
  # processus d'un AUTRE utilisateur (EPERM) qui est pourtant bien vivant.
  [ -d "/proc/$pid" ] && return 0
  kill -0 "$pid" 2>/dev/null
}

# fiche_champ <contenu> <clé> : la valeur d'une clé du registre, rendue dans FICHE_VALEUR.
#
# Elle part dans une globale et non sur stdout : quatre `$(…)` par fiche coûteraient quatre forks
# (~19 ms pièce sous MSYS) sur un chemin joué à chaque /ticket-start — même raison que RETRAIT_ERREUR
# et COQUILLES_RAPPORT, qui ne rendent rien non plus.
#
# Le registre écrit un JSON à plat et sans espaces : `"clé":"valeur"` ou `"clé":123`. Le motif exige
# le guillemet qui OUVRE la clé, sans quoi `sessionId` matcherait dans `bridgeSessionId`. Une valeur
# portant un `"` échappé serait tronquée : Windows l'interdit dans un chemin, et `sessions_texte` vit
# déjà avec la même limite.
FICHE_VALEUR=""
fiche_champ() {
  local s="$1" cle="$2" v
  FICHE_VALEUR=""
  case "$s" in *"\"$cle\":"*) ;; *) return 1 ;; esac
  v="${s#*\"$cle\":}"
  v="${v# }"                                       # tolère un JSON espacé, que le registre n'écrit pas
  case "$v" in
    '"'*)
      v="${v#\"}"; v="${v%%\"*}"
      v="${v//\\\\/\\}" ;;                         # un chemin Windows voyage en « \\ » dans le JSON
    *) v="${v%%,*}"; v="${v%%\}*}" ;;
  esac
  FICHE_VALEUR="$v"
}

# charge_sessions_ouvertes : les sessions Claude Code encore vivantes, « <cwd><TAB><pid><TAB><nom> ».
#
# Lue UNE fois par invocation et non par worktree : le registre est indépendant du candidat qu'on
# examine, et le relire à chaque ligne rendrait le coût quadratique.
#
# On s'écarte soi-même par `CLAUDE_CODE_SESSION_ID` (#424) : c'est le pendant, pour cette source, du
# refus « session courante » de `gc`, et c'est ce qui laisse #519 ramasser le worktree dont la
# session vient de sortir par `ExitWorktree` — sa fiche peut encore le nommer. Le même passage note
# au passage MON pid (MA_SESSION_PID), dont la source A a besoin pour la même raison : le registre
# est le seul endroit qui relie une session à son processus.
SESSIONS_OUVERTES=()
SESSIONS_OUVERTES_LUES=0
MA_SESSION_PID=""
charge_sessions_ouvertes() {
  local racine f contenu cwd pid nom
  [ "$SESSIONS_OUVERTES_LUES" = 0 ] || return 0
  SESSIONS_OUVERTES_LUES=1
  racine="$(sessions_config)/sessions"
  [ -d "$racine" ] || return 0
  for f in "$racine"/*.json; do
    [ -e "$f" ] || continue
    # ⚠ Le `|| [ -n "$contenu" ]` n'est pas une précaution : une fiche est du JSON d'UNE ligne SANS
    # saut final, et `read` rend 1 sur une fin de fichier — même après avoir affecté la variable.
    # Sans lui, le registre entier est lu comme vide et la source B ne voit jamais personne.
    contenu=""
    read -r contenu 2>/dev/null <"$f" || [ -n "$contenu" ] || continue
    fiche_champ "$contenu" pid || continue
    pid="$FICHE_VALEUR"
    if fiche_champ "$contenu" sessionId && [ -n "$FICHE_VALEUR" ] &&
       [ "$FICHE_VALEUR" = "${CLAUDE_CODE_SESSION_ID:-}" ]; then
      MA_SESSION_PID="$pid"
      continue
    fi
    fiche_champ "$contenu" cwd || continue
    cwd="$FICHE_VALEUR"
    [ -n "$cwd" ] || continue
    processus_vivant "$pid" || continue
    fiche_champ "$contenu" name || FICHE_VALEUR=""
    nom="$FICHE_VALEUR"
    SESSIONS_OUVERTES+=("$cwd"$'\t'"$pid"$'\t'"$nom")
  done
}

# occupe_par <chemin> : 0 si quelque chose de vivant occupe ce worktree, et OCCUPANT le nomme.
#
# Rend son verdict par une globale, comme RETRAIT_ERREUR : un appelant qui le capturerait par `$(…)`
# le lirait depuis un sous-shell, où le cache de `ps -W` et celui du registre mourraient avec lui —
# soit un balayage complet des processus par worktree examiné.
OCCUPANT=""
occupe_par() {
  local chemin="$1" pids entree cwd pid nom desc=""
  OCCUPANT=""
  [ "${MAESTRO_WORKTREE_OCCUPE:-}" != 0 ] || return 1
  if [ -n "${MAESTRO_WORKTREE_OCCUPE:-}" ]; then
    OCCUPANT="$("$MAESTRO_WORKTREE_OCCUPE" "$chemin" 2>/dev/null)" || OCCUPANT=""
    [ -n "$OCCUPANT" ]
    return
  fi

  # Le registre EN PREMIER, et l'ordre compte : `occupants_proc` s'exécute dans un sous-shell (il
  # rend sa liste sur stdout), donc tout ce qu'il chargerait mourrait avec lui — MA_SESSION_PID
  # compris, dont il a besoin pour ne pas compter la session appelante parmi les occupants.
  charge_sessions_ouvertes

  pids="$(occupants_proc "$chemin")"
  if [ -n "$pids" ]; then
    # `occupants_proc` rend « <espace>pid[ pid…] » : deux espaces valent deux processus.
    case "$pids" in
      ' '*' '*) desc="des processus vivants (pid$pids)" ;;
      *) desc="un processus vivant (pid$pids)" ;;
    esac
  fi

  for entree in ${SESSIONS_OUVERTES[@]+"${SESSIONS_OUVERTES[@]}"}; do
    IFS=$'\t' read -r cwd pid nom <<< "$entree"
    [ -n "$cwd" ] && [ "$cwd" -ef "$chemin" ] || continue
    desc="${desc:+$desc, }la session ${nom:+« $nom » }(pid $pid)"
  done

  OCCUPANT="$desc"
  [ -n "$OCCUPANT" ]
}

# retire_worktree <chemin> <force 0|1> <verbeux 0|1> : la séquence de retrait, écrite UNE FOIS —
# délier PUIS retirer. L'ordre est le garde-fou de #152 : `git worktree remove` descend dans les
# jonctions et viderait le .venv et le node_modules du CLONE PRINCIPAL. Rend 0 si le worktree est
# parti ; sinon 1, la sortie de git étant laissée dans RETRAIT_ERREUR (sa cause varie : « Filename
# too long » sur un node_modules profond, worktree verrouillé, dossier occupé par un shell…).
#
# En échec, RETRAIT_DESENREGISTRE dit LEQUEL des deux échecs : `0`, le worktree est intact et le
# retrait reste à faire ; `1`, git l'a bel et bien désenregistré et seul le dossier résiste — dire
# « non retiré » dans ce cas-là, c'est annoncer l'inverse de ce qui vient de se passer, et c'est ce
# qui a laissé onze coquilles s'accumuler derrière autant de lignes rouges (#422).
RETRAIT_ERREUR=""
RETRAIT_DESENREGISTRE=0
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
  RETRAIT_DESENREGISTRE=0
  if sortie="$(git -C "$principal" "${args[@]}" "$chemin" 2>&1)"; then
    return 0
  fi
  RETRAIT_ERREUR="$sortie"

  # git a échoué — mais sur QUOI ? Il supprime le contenu d'abord, et quand un processus tient le
  # DOSSIER (« Permission denied » sous Windows) il va au bout de son désenregistrement quand même :
  # le worktree quitte `git worktree list`, sa branche redevient supprimable, et il ne reste qu'une
  # coquille vide que plus rien ne revendique (#422, observé sur #415). Le `.git` d'un worktree lié
  # est le repère : parti, il ne s'agit plus d'un retrait à retenter mais d'un dossier à balayer.
  if [ ! -e "$chemin" ]; then
    return 0                                    # dossier parti malgré le message : rien à rattraper
  fi
  if [ ! -e "$chemin/.git" ]; then
    RETRAIT_DESENREGISTRE=1
    if [ -z "$(ls -A "$chemin" 2>/dev/null)" ] && rmdir "$chemin" 2>/dev/null; then
      # `prune` pour le cas symétrique : le dossier avait disparu mais l'entrée d'administration
      # restait, et `git worktree add` refuserait alors de remonter la même branche ici.
      git -C "$principal" worktree prune >/dev/null 2>&1
      return 0
    fi
  fi
  return 1
}

# orphelins_en_cours [<iid à écarter>] : les tickets « En cours » dont plus personne ne s'occupe,
# tels que `lib.sh reconcile-en-cours --auto` les rend (rien du tout quand il n'y a rien à dire).
#
# Greffé sur `gc` et non sur `ensure` (#328), pour la raison exacte qui a fait greffer la pose du
# cycle de vie au même endroit (#275) : les TROIS points de passage de `gc` — `ensure`, donc tout
# /ticket-start ; /branch-cleanup ; le démarrage d'un run — en héritent d'un coup, là où un câblage
# par point de passage en ferait trois à garder d'accord. La question n'est pas celle de `gc` (« ce
# worktree a-t-il encore une raison d'exister ? ») mais elle se pose aux mêmes moments, et c'est le
# moment qui compte : un ticket « En cours » et assigné est invisible de `queue.sh`, donc rien ne le
# ramènerait jamais dans le champ de vision.
#
# Purement CONSULTATIF, comme tout ce que rend ce verbe : aucun label posé, aucune assignation
# touchée, aucun worktree retiré — la reprise est le geste explicite de `lib.sh reprendre-en-cours`
# (#329), que le signalement nomme lui-même. Best-effort : un échec
# (gh absent, hors ligne, dépôt jetable sans journal d'orchestration) rend le silence et n'empêche
# ni un ticket de démarrer ni un run de continuer.
#
# `<iid à écarter>` est celui que l'appelant est en train de démarrer : le signaler orphelin serait
# vrai une seconde et faux la suivante. MAESTRO_EN_COURS_SIGNAL remplace l'appel (0 = éteint) —
# couture des tests, comme MAESTRO_WORKTREE_VERDICT et MAESTRO_WORKFLOW_POSE. MAESTRO_WORKTREE_GC=0
# l'éteint aussi, par voie de conséquence : `gc` est son porteur, et ne pas passer par `gc` c'est ne
# pas passer par lui.
orphelins_en_cours() {
  local sauf="${1:-}"
  local -a args=(--auto)
  [ -n "$sauf" ] && args+=(--sauf "$sauf")
  [ "${MAESTRO_EN_COURS_SIGNAL:-}" != 0 ] || return 0
  if [ -n "${MAESTRO_EN_COURS_SIGNAL:-}" ]; then
    "$MAESTRO_EN_COURS_SIGNAL" "${args[@]}" 2>/dev/null || true
  else
    bash "$ICI/../gitlab/lib.sh" reconcile-en-cours "${args[@]}" 2>/dev/null || true
  fi
}

# commande_gc [--check] [--auto] [--sauf <iid>] [--iid <iid>] : retire les worktrees dont GitLab
# confirme le travail soldé, et signale les tickets « En cours » que plus personne ne mène (#328).
#
# `--iid <iid>` en fait un ramassage CIBLÉ (#438) : ce worktree-là, et rien d'autre — ni balayage
# des coquilles, ni signalement des orphelins, ni lecture du backlog. C'est ce qui le rend jouable
# APRÈS CHAQUE MERGE du drain d'un run, là où le balayage complet coûterait une lecture de forge par
# worktree et par PR mergée. Les trois refus ci-dessous, eux, ne bougent pas : cibler dit QUI est
# candidat, jamais ce qu'on s'autorise sur lui.
#
# Trois refus, dans cet ordre, parce qu'ils ne coûtent pas la même chose :
#   - le worktree de la SESSION COURANTE, jamais candidat (on ne se retire pas le sol sous les pieds) ;
#   - un worktree porteur de TRAVAIL NON SAUVEGARDÉ : signalé, jamais supprimé en silence. C'est
#     l'inverse du confort : mieux vaut 535 Mo de trop qu'un commit perdu ;
#   - un verdict autre que « fini » — y compris « inconnu » (forge muette, hors ligne). Ne rien savoir
#     n'autorise rien : le nom de la branche ne sert jamais de preuve de merge (docs/10 §6).
#
# Deux temps volontaires : on INVENTORIE d'abord, on décide ensuite. `git worktree remove` réécrit la
# liste que l'on parcourrait.
#
# MAESTRO_WORKTREE_VERDICT remplace l'interrogation de GitLab par une commande qui reçoit
# « <iid> <branche> » et imprime la ligne de verdict : c'est la couture par laquelle les tests font
# tourner le ramassage sans réseau ni CLI de forge (même dispositif que MAESTRO_ORCHESTRATE_WORKTREE, #172).
commande_gc() {
  local check=0 auto=0 sauf="" cible=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --check) check=1 ;;
      --auto)  auto=1 ;;
      --sauf)  sauf="${2:-}"; shift ;;
      --iid)   cible="${2:-}"; shift ;;
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
  local ligne chemin="" paires="" b_ligne b_ligne_iid
  while IFS= read -r ligne; do
    case "$ligne" in
      worktree\ *) chemin="${ligne#worktree }" ;;
      branch\ refs/heads/*)
        [ "$chemin" = "$principal" ] && continue
        b_ligne="${ligne#branch refs/heads/}"
        # `--iid` (#438) : le ramassage d'UN worktree, celui du ticket qu'on vient de merger. Même
        # dérivation de l'iid que la boucle principale plus bas — une seconde formule finirait par
        # ne plus désigner le même worktree que celle qui décide.
        if [ -n "$cible" ]; then
          b_ligne_iid="${b_ligne#*/}"
          [ "${b_ligne_iid%%-*}" = "$cible" ] || continue
        fi
        paires="$paires$chemin"$'\t'"$b_ligne"$'\n' ;;
    esac
  done < <(git -C "$principal" worktree list --porcelain 2>/dev/null)

  # Les coquilles (#422) sont balayées AVANT tout le reste, et quoi qu'il arrive : elles sont
  # précisément ce que `git worktree list` ne connaît plus, donc rien de ce qui suit ne les
  # rencontrerait. Le cas « plus aucun worktree en place » est même celui où elles sont le plus
  # probables — elles restent quand tout le reste est parti.
  #
  # Sauf en mode ciblé (#438), qui répond à UNE question et n'en pose pas d'autre : un balayage est
  # un geste de passage obligé (`ensure`, `/branch-cleanup`, démarrage d'un run), pas quelque chose
  # qu'on rejoue après chaque merge d'un drain. Une coquille laissée là attendra le prochain
  # balayage — c'est déjà ce qu'elle faisait avant ce ticket.
  [ -n "$cible" ] || ramasse_coquilles "$principal" "$check"

  if [ -z "$paires" ]; then
    # Aucun worktree ici : le signalement des orphelins n'aurait rien à déduire non plus (sa portée
    # EST celle des worktrees de cette machine), mais on l'appelle quand même — c'est lui qui décide
    # de sa portée, pas cette branche-ci, et un jour où il saura conclure sans worktree il n'aura
    # pas à se souvenir qu'un `return 0` l'avait court-circuité.
    # En mode ciblé (#438), « aucun worktree » veut dire « celui de ce ticket n'est pas ici » — un
    # non-événement, et pas une occasion d'aller inventorier le backlog.
    [ -z "$cible" ] || return 0
    local seuls_orphelins
    seuls_orphelins="$(orphelins_en_cours "$sauf")"
    if [ -n "$COQUILLES_RAPPORT" ] || [ -n "$seuls_orphelins" ]; then
      printf '\n'
      [ -n "$COQUILLES_RAPPORT" ] && printf '%s' "$COQUILLES_RAPPORT"
      [ -n "$seuls_orphelins" ] &&
        printf '\nTickets « En cours » dont plus personne ne s'\''occupe :\n%s\n' "$seuls_orphelins"
      [ "$auto" = 1 ] && printf '\n'
      return 0
    fi
    [ "$auto" = 1 ] || printf '\nAucun worktree en place — rien à ramasser.\n\n'
    return 0
  fi

  [ "$auto" = 1 ] || printf '\nRamassage des worktrees de %s\n\n' "$principal"

  # LES VERDICTS SONT DEMANDÉS EN UNE FOIS, AVANT LA BOUCLE (#602, docs/10 §9.8). Ils l'étaient un
  # par un, et chaque appel était un sous-processus complet — chargement de lib.sh, vérification du
  # jeton, puis jusqu'à deux allers vers la forge. À 2,5 s l'aller, c'est le prix qui grandit avec le
  # nombre de worktrees, donc avec la longueur du run qui vient de les laisser là : 14 worktrees
  # coûtaient 28 allers pour une question qui en demande deux.
  #
  # La COUTURE DES TESTS NE BOUGE PAS : `MAESTRO_WORKTREE_VERDICT` reste appelée une fois par paire,
  # avec les mêmes arguments et dans le même ordre. C'est la seule chose que le regroupement ne doit
  # pas emporter — ces tests-là gardent quatre garde-fous (#197, #503, #275, #327), et un test qui
  # change de forme en même temps que le code qu'il garde ne garde plus rien.
  local -a paires_lot=()
  local p_chemin p_branche p_nom p_iid
  while IFS=$'\t' read -r p_chemin p_branche; do
    [ -n "$p_chemin" ] || continue
    p_nom="${p_branche#*/}"; p_iid="${p_nom%%-*}"
    case "$p_iid" in ''|*[!0-9]*) continue ;; esac
    [ -n "$courant" ] && [ "$p_chemin" = "$courant" ] && continue
    paires_lot+=("$p_iid:$p_branche")
  done <<< "$paires"

  local VERDICTS_LOT=""
  if [ "${#paires_lot[@]}" -gt 0 ] && [ -z "${MAESTRO_WORKTREE_VERDICT:-}" ]; then
    VERDICTS_LOT="$(bash "$ICI/../gitlab/lib.sh" worktree-done-lot "${paires_lot[@]}" 2>/dev/null)" || VERDICTS_LOT=""
  fi

  local branche nom iid brut verdict sha raison reste n_modifs n_commits detail cycle pose
  local n_sessions note
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
      # La lecture groupée d'avant la boucle. Un iid absent de sa réponse rend une ligne vide, ce
      # que la suite lit déjà comme « verdict indisponible » — donc « je n'y touche pas ».
      brut="$(printf '%s\n' "$VERDICTS_LOT" | ST_IID="$iid" awk -F'\t' '$1 == ENVIRON["ST_IID"] { print $2 "\t" $3 "\t" $4; exit }')"
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

    # Cycle de vie du ticket (#275, docs/10 §9.2) : « fini » — PR mergée ou ticket fermé — est
    # EXACTEMENT la question que pose la réconciliation. On la greffe donc ici, sur un verdict déjà
    # rendu, plutôt que d'ajouter une cinquième étape à `ensure` après #181/#197/#205/#216 : aucune
    # lecture de découverte en plus, et les trois points de passage de `gc` (ensure — donc tout
    # /ticket-start —, /branch-cleanup, démarrage d'un run) en héritent d'un coup.
    # Deux choix à ne pas défaire :
    #   • AVANT le garde-fou du travail non sauvegardé et indépendamment du retrait — le cycle de vie
    #     suit le verdict de GitLab, pas la propreté d'un répertoire local ni le succès d'un `rm` ;
    #   • BEST-EFFORT et muet en cas d'échec (gh absent, hors ligne) : ce ramassage ne doit jamais
    #     empêcher un ticket de démarrer ni un run de continuer (même statut que `sync-main`).
    # C'est `reconcile-workflow` qui refuse d'écraser un « Abandonné »/« Doublon » — fermés eux
    # aussi, donc « fini » eux aussi. MAESTRO_WORKFLOW_POSE remplace l'appel (0 = éteint) : c'est la
    # couture par laquelle les tests l'observent sans réseau, comme MAESTRO_WORKTREE_VERDICT.
    cycle=""
    if [ "$check" = 0 ] && [ "${MAESTRO_WORKFLOW_POSE:-}" != 0 ]; then
      if [ -n "${MAESTRO_WORKFLOW_POSE:-}" ]; then
        pose="$("$MAESTRO_WORKFLOW_POSE" "$iid" 2>/dev/null)" || pose=""
      else
        pose="$(bash "$ICI/../gitlab/lib.sh" reconcile-workflow "$iid" 2>/dev/null)" || pose=""
      fi
      # Une sortie non vide = quelque chose a été posé ; le silence couvre aussi bien « déjà à un
      # état final » que l'échec, et dans les deux cas il n'y a rien à annoncer.
      [ -n "$pose" ] && cycle=" — cycle de vie → Terminé"
    fi
    # Porté par la raison : elle habille la ligne du retrait comme celle du --check, sans dupliquer
    # la concaténation à chaque issue. Le cas « conservé » ci-dessous l'ajoute pour son compte.
    raison="$raison$cycle"

    reste="$(travail_non_sauvegarde "$chemin" "$branche" "$sha")"
    n_modifs="${reste%% *}"; n_commits="${reste##* }"
    detail=""
    [ "${n_modifs:-0}" -gt 0 ] && detail="$n_modifs fichier(s) non commité(s)"
    [ "${n_commits:-0}" -gt 0 ] && detail="${detail:+$detail, }$n_commits commit(s) non poussé(s)"
    if [ -n "$detail" ]; then
      # Toujours dit, même en --auto : c'est le seul cas où se taire ferait perdre du travail.
      signales=$((signales + 1))
      gardes=$((gardes + 1))
      rapport="$rapport$(alerte "#$iid conservé — $detail dans $(chemin_natif "$chemin")$cycle")"$'\n'
      continue
    fi

    # Quelqu'un est-il DEDANS ? (#503) Le retrait d'un worktree soldé qu'une AUTRE session occupe lui
    # retire son répertoire courant sans un mot, et laisse une coquille de #422 derrière lui : le
    # défaut était de le découvrir après avoir supprimé le contenu, alors que la question se pose
    # avant. Elle se pose donc ici, en dernier — APRÈS le garde-fou du travail non sauvegardé, qui
    # garde le mot de la fin quand les deux valent : il nomme ce qui pourrait se PERDRE, là où
    # celui-ci ne nomme que ce qu'on n'a pas à toucher, et rien ne se perd dans les deux cas.
    if occupe_par "$chemin"; then
      # Silencieux en --auto, comme les autres « conservé » : un worktree occupé est un état normal
      # et non une anomalie, et le dire à chaque /ticket-start ferait une ligne par session en vol.
      gardes=$((gardes + 1))
      [ "$auto" = 1 ] || rapport="$rapport$(deja "#$iid conservé — occupé par $OCCUPANT")"$'\n'
      continue
    fi

    # Les sessions du ticket (#385, docs/10 §9.7). Le retrait ne les efface PAS — un transcript vit
    # sous `<config>/projects/`, jamais dans le worktree — mais il coupe le seul chemin par lequel le
    # sélecteur `/resume` les montrait, et c'est ICI qu'il faut le dire : une fois le worktree parti,
    # plus rien à l'écran ne rappellera qu'il y avait un historique, ni par quoi le rouvrir.
    n_sessions="$(sessions_compte "$iid")"
    note=""
    [ "${n_sessions:-0}" -gt 0 ] && note=" — $n_sessions session(s) conservée(s) : worktree.sh sessions $iid"

    if [ "$check" = 1 ]; then
      retires=$((retires + 1))
      rapport="$rapport$(printf '  → #%s à retirer — %s%s' "$iid" "$raison" "$note")"$'\n'
      continue
    fi

    if retire_worktree "$chemin" 0 0; then
      retires=$((retires + 1))
      rapport="$rapport$(ok "#$iid retiré — $raison$note")"$'\n'
    else
      # Construit à la main : `erreur` écrit sur stderr, la ligne sortirait donc du rapport (et hors
      # de son ordre). Ici tout le compte rendu part sur stdout, en un bloc.
      echecs=$((echecs + 1))
      if [ "$RETRAIT_DESENREGISTRE" = 1 ]; then
        # « non retiré » serait faux : git ne le connaît plus. Ce qui reste est une coquille, et
        # c'est le prochain passage ici qui l'écartera (#422).
        rapport="$rapport$(printf '  ⚠ #%s désenregistré, son dossier résiste : %s' \
          "$iid" "$(chemin_natif "$chemin")")"$'\n'
      else
        rapport="$rapport$(printf '  ✗ #%s non retiré : %s' "$iid" "$(printf '%s' "$RETRAIT_ERREUR" | head -1)")"$'\n'
      fi
    fi
  done <<< "$paires"

  # Le signalement des orphelins (#328) est indépendant de ce qui précède : il a sa propre question,
  # sa propre portée et son propre silence. Il est demandé ICI, une fois le ramassage joué, pour que
  # le compte rendu garde l'ordre « ce que j'ai fait, puis ce que je constate ».
  #
  # Muet en mode ciblé (#438), pour la raison qui vaut déjà aux coquilles, et une de plus : ce
  # signalement lit TOUT le backlog en cours, donc le rejouer après chaque merge d'un drain coûterait
  # un balayage de forge par PR mergée — précisément ce que le mode ciblé existe pour éviter.
  local orphelins=""
  [ -n "$cible" ] || orphelins="$(orphelins_en_cours "$sauf")"

  # En --auto (appelé par `ensure`, donc par /ticket-start) on ne parle que s'il y a quelque chose à
  # dire : un retrait, un travail en danger, un échec. Le silence est le cas normal — et un orphelin
  # signalé suffit à rompre ce silence, même quand le ramassage n'a rien à raconter.
  # Une coquille écartée compte pour « quelque chose à dire » : c'est un retrait de plus, et se
  # taire dessus est exactement ce qui les a laissées s'accumuler à onze (#422).
  local muet_ramassage=0
  if [ "$auto" = 1 ] && [ "$retires" -eq 0 ] && [ "$signales" -eq 0 ] && [ "$echecs" -eq 0 ] &&
     [ "$COQUILLES_RETIREES" -eq 0 ] && [ "$COQUILLES_SIGNALEES" -eq 0 ]; then
    muet_ramassage=1
    [ -z "$orphelins" ] && return 0
  fi
  [ "$auto" = 1 ] && printf '\n'
  if [ "$muet_ramassage" = 0 ]; then
    printf '%s%s' "$rapport" "$COQUILLES_RAPPORT"
    local appoint=""
    [ "$((COQUILLES_RETIREES + COQUILLES_SIGNALEES))" -gt 0 ] &&
      appoint="$(printf ', %s coquille(s)' "$((COQUILLES_RETIREES + COQUILLES_SIGNALEES))")"
    if [ "$check" = 1 ]; then
      printf 'Ramassage (--check) : %s à retirer, %s conservé(s)%s — rien n'\''a été touché.\n' \
        "$retires" "$gardes" "$appoint"
    else
      printf 'Ramassage des worktrees : %s retiré(s), %s conservé(s)%s.\n' \
        "$retires" "$gardes" "$appoint"
    fi
  fi
  if [ -n "$orphelins" ]; then
    printf '\nTickets « En cours » dont plus personne ne s'\''occupe :\n%s\n' "$orphelins"
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
  sessions)    commande_sessions "$@" ;;
  -h|--help|'') usage ;;
  # Raccourci : un iid nu vaut `create <iid>`.
  *[!0-9]*)    printf 'Sous-commande inconnue : %s\n\n' "$cmd" >&2; usage >&2; exit 2 ;;
  *)           commande_create "$cmd" "$@" ;;
esac
