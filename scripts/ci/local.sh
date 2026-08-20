#!/usr/bin/env bash
# Filet CI local — rejoue les jobs de .github/workflows/ci.yml sur son propre poste, AVANT le push
# (ticket #157, parent #155).
#
# Pourquoi (docs/10-workflow-git.md §8) : la CI tourne sur les runners hébergés de GitHub et son
# verdict conditionne le merge. Découvrir un échec de lint par le pipeline, c'est l'apprendre après
# un aller-retour de plusieurs minutes — facturées — pour une faute de frappe. Ce script rejoue les
# mêmes contrôles en local :
#
#   bash scripts/ci/local.sh                    # tous les jobs applicables
#   bash scripts/ci/local.sh --complet          # + la suite pytest ENTIÈRE et sa couverture
#   bash scripts/ci/local.sh --only pytest,mypy # un sous-ensemble (noms de jobs ou d'étages)
#   bash scripts/ci/local.sh --skip web-build
#   bash scripts/ci/local.sh --strict           # code non nul aussi si un job n'a pas pu être joué
#   bash scripts/ci/local.sh --list             # les jobs et leur commande
#
# PÉRIMÈTRE DE PYTEST (ticket #214) — par défaut le filet ne joue que les suites que le diff
# concerne, pas les 1100 tests du dépôt. Le raisonnement : la suite complète coûte 9 min 57 s en
# série, dont 9 pour les ~360 tests d'OUTILLAGE (ceux qui montent un dépôt git jetable et lancent
# un script shell) ; les rejouer pendant qu'on écrit du code applicatif ne dit rien de neuf. La
# suite complète reste jouée — par le pipeline de la MR, qui depuis #165 est de toute façon le
# seul verdict lu et la condition de merge (docs/10 §8). D'où deux modes :
#
#   --rapide (DÉFAUT) : pytest sur le périmètre du diff, sans seuil de couverture (un
#                       sous-ensemble ne peut pas le tenir) ; verdict annoncé PARTIEL ;
#   --complet         : la suite entière avec sa couverture et son seuil — ce que joue la CI.
#
# Le lint, lui, tourne toujours en entier : il coûte quelques secondes et c'est l'échec le plus
# bête à découvrir sur un runner facturé à la minute.
#
# Principes :
#   - LES INTERPRÉTEURS DU DÉPÔT, jamais ceux du système : le venv (.venv/) et le Node vendoré
#     (.tools/node/, épinglé par .node-version). Un `nvm use 18` ou un python global n'a donc
#     aucune prise sur le verdict — c'est tout l'intérêt d'un filet censé prédire la CI.
#   - ET LE CODE D'ICI, jamais celui d'un autre répertoire de travail : le venv est PARTAGÉ entre le
#     clone principal et ses worktrees (docs/10 §9), où il installe `maestro` en éditable pointé sur
#     le clone principal. Lancer pytest par son script console y ferait tester la branche du voisin
#     (#194) : c'est `python -m pytest`, et une sonde le vérifie avant de jouer le job.
#   - AUCUN RÉSEAU, aucune installation : le script se sert de ce que scripts/setup.sh a posé.
#     Ce qui manque est dit, avec la commande qui l'obtient — jamais installé dans le dos.
#   - MÊMES ÉTAGES QUE LA CI : lint puis test ; un étage en échec ARRÊTE le pipeline, comme
#     la CI. Le code de sortie est non nul dès le premier échec dur.
#   - UN PÉRIMÈTRE RÉDUIT SE DIT : sélectionner des tests, c'est accepter de ne pas tout savoir.
#     Le job annonce ce qu'il a joué et pourquoi, le verdict final porte la mention PARTIEL, et
#     tout ce que le script ne sait pas classer élargit à la suite entière — jamais l'inverse.
#   - UN JOURNAL QUI SE LIT : les journaux vont sous <racine-du-worktree>/.maestro/ci-local/ et les
#     chemins affichés sont RELATIFS à la racine (#234). Un job rouge ne vaut que par la raison
#     qu'il donne ; un chemin absolu hors du répertoire de travail la met hors de portée d'une
#     session autonome, qui n'a personne pour approuver sa lecture.
#   - UN JOB NON JOUABLE N'EST PAS UN ÉCHEC : outil absent ⇒ « IGNORÉ », verdict annoncé PARTIEL
#     (et bloquant avec --strict). Mieux vaut un verdict honnêtement incomplet qu'un faux vert.
#
# Les paramètres qui pourraient dériver de la CI (seuil de couverture, sévérité shellcheck, image
# du linter) sont LUS dans .github/workflows/ci.yml plutôt que recopiés ici : le filet suit le pipeline.

set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CI_YML="$RACINE/.github/workflows/ci.yml"
# JOURNAUX SOUS LA RACINE DU WORKTREE (#234), pas dans ${TMPDIR:-/tmp}. Quand un job échoue, le
# renvoi vers son journal est la seule information qui dise POURQUOI — et c'est justement celle
# qu'une session autonome (docs/10 §11) n'a pas le droit d'aller chercher : un chemin absolu hors du
# répertoire de travail demande une approbation que personne n'est là pour donner. Sur le run de
# #200, `tail /tmp/maestro-ci-local/pytest.log` a été refusé cinq fois de suite (13 refus sur
# 5 sessions) et la session a fini par abandonner sans jamais lire l'échec de ses propres tests.
# Sous la racine, le chemin s'écrit relatif et se lit sans rien demander ; `.maestro/` est gitignoré,
# donc rien n'entre dans le dépôt.
LOG_DIR_REL=".maestro/ci-local"
LOG_DIR="$RACINE/$LOG_DIR_REL"

case "$(uname -s 2>/dev/null)" in
  MINGW* | MSYS* | CYGWIN*) WINDOWS=1 ;;
  *) WINDOWS=0 ;;
esac

if [ -t 1 ]; then
  C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_B=$'\033[1m'; C_0=$'\033[0m'
else
  C_G=''; C_Y=''; C_R=''; C_B=''; C_0=''
fi

# --- Réglages repris de .github/workflows/ci.yml ------------------------------------------------------------
# Lecture tolérante : si le motif disparaît du pipeline, on retombe sur la valeur du jour plutôt
# que de refuser de tourner.
lit_ci() { # <motif-grep-E> <défaut>
  local trouve
  trouve="$(grep -oE -- "$1" "$CI_YML" 2>/dev/null | head -1)"
  printf '%s\n' "${trouve:-$2}"
}
# Les motifs incluent le NOM DE LA COMMANDE : « --severity=style » figure aussi dans un commentaire
# du pipeline (« à durcir en… »), et un motif nu y aurait attrapé la mauvaise valeur.
SHELLCHECK_SEVERITE="$(lit_ci 'shellcheck --severity=[a-z]+' 'shellcheck --severity=warning')"
SHELLCHECK_SEVERITE="${SHELLCHECK_SEVERITE##*=}"
# L'IMAGE, elle, N'EST PLUS LUE DANS LA CI et c'est volontaire (#344) : le job `shellcheck` de
# GitHub Actions se sert du shellcheck PRÉINSTALLÉ sur le runner — une image `container:` alpine y
# ferait échouer `actions/checkout`, qui exige un Node absent de cette image. Il n'y a donc plus
# rien à suivre, et le conteneur n'est qu'un REPLI local pour un poste sans shellcheck installé.
# `lit_ci` aurait rendu son défaut en silence ; le dire ici évite de croire à un miroir qui
# n'existe plus. MAESTRO_SHELLCHECK_IMAGE reste le point de réglage.
SHELLCHECK_IMAGE="${MAESTRO_SHELLCHECK_IMAGE:-koalaman/shellcheck-alpine:stable}"
# `[^#]*` entre la commande et le drapeau : la CI a gagné un `-n auto` (#214) et pourrait gagner
# autre chose. Un motif qui collait les deux mots (« pytest --cov-fail-under=… ») cessait de
# matcher ce jour-là, et le filet retombait en silence sur le seuil par défaut — un écart avec le
# pipeline qui ne se voit qu'au moment où il fait mal. La classe exclut « # » pour ne pas
# traverser un commentaire de fin de ligne.
COUVERTURE_MIN="$(lit_ci 'pytest[^#]*--cov-fail-under=[0-9]+' 'pytest --cov-fail-under=90')"
COUVERTURE_MIN="${COUVERTURE_MIN##*=}"

# --- Combien de workers pytest (#285) -------------------------------------------------------------
# `-n auto` demande UN WORKER PAR CŒUR LOGIQUE : 16 sur le poste de mesure. La contrainte n'est pas
# le CPU, c'est la MÉMOIRE — un worker de cette suite pèse ~130 Mo (il monte des dépôts git jetables
# et attend des processus), soit ~2 Go à seize pour ~1,8 Go de RAM libre. Le poste pagine, et le
# parallélisme se mange lui-même : mesuré sur tests/test_worktree.py, `-n auto` et `-n 8` sont à
# ÉGALITÉ (74 s), pour deux fois moins de workers, de mémoire et de processus.
#
# Le plafond est un MINIMUM avec le nombre de cœurs, jamais un forçage : sur une machine qui en a
# moins, rien ne change (4 cœurs ⇒ `-n 4`, ce que `-n auto` aurait donné). C'est ce qui permet de le
# poser sans distinguer les plateformes — le gain est mesuré sous Windows, où créer un processus
# coûte ~50 ms, mais le raisonnement mémoire, lui, n'a rien de propre à Windows. Le job CI garde
# `-n auto` : il tourne dans un conteneur dédié où la mémoire n'est pas le facteur limitant
# (1 min 53 s au lieu de ~10 min, docs/10 §8.4).
#
# MAESTRO_PYTEST_WORKERS relève ou abaisse le plafond, pour un poste qui n'a pas ce profil-là.
PYTEST_WORKERS_PLAFOND="${MAESTRO_PYTEST_WORKERS:-8}"
case "$PYTEST_WORKERS_PLAFOND" in
  '' | 0 | *[!0-9]*) PYTEST_WORKERS_PLAFOND=8 ;;
esac

# Le nombre de cœurs, ou le plafond lui-même si personne ne sait le dire — se tromper vers le haut
# retombe sur le plafond, qui est précisément la valeur sûre.
coeurs_logiques() {
  local n
  n="$(nproc 2>/dev/null)" || n="${NUMBER_OF_PROCESSORS:-}"
  case "$n" in
    '' | 0 | *[!0-9]*) printf '%s\n' "$PYTEST_WORKERS_PLAFOND" ;;
    *) printf '%s\n' "$n" ;;
  esac
}

workers_pytest() {
  local coeurs
  coeurs="$(coeurs_logiques)"
  if [ "$coeurs" -lt "$PYTEST_WORKERS_PLAFOND" ]; then
    printf '%s\n' "$coeurs"
  else
    printf '%s\n' "$PYTEST_WORKERS_PLAFOND"
  fi
}

# --- Jobs, par étage (mêmes noms que .github/workflows/ci.yml) ----------------------------------------------
ETAGE_LINT="shellcheck python-lint"
ETAGE_TEST="pytest mypy web-build"
JOBS_CONNUS="$ETAGE_LINT $ETAGE_TEST"

JOBS_ONLY=""
JOBS_SKIP=""
STRICT=0
#: « rapide » = pytest sur le périmètre du diff (défaut, #214) ; « complet » = la suite entière.
MODE_PYTEST=rapide

usage() {
  cat <<USAGE
Filet CI local — rejoue les jobs de .github/workflows/ci.yml avant de pousser.

  bash scripts/ci/local.sh [options]

Options :
  --rapide        (défaut) pytest ne joue que les suites concernées par le diff, sans seuil
                  de couverture ; le verdict est annoncé PARTIEL.
  --complet       pytest joue la suite ENTIÈRE avec sa couverture et son seuil — ce que fera
                  le pipeline de la MR.
  --only <jobs>   N'exécute que ces jobs (séparés par des virgules). Les noms d'étages
                  « lint » et « test » sont acceptés et développés en leurs jobs.
  --skip <jobs>   Saute ces jobs (mêmes noms).
  --strict        Code de sortie non nul aussi si un job n'a pas pu être joué (outil absent).
  --list          Affiche les jobs, leur étage et leur commande, puis sort.
  -h, --help      Cette aide.

Jobs : ${JOBS_CONNUS// /, }
  lint : ${ETAGE_LINT// /, }
  test : ${ETAGE_TEST// /, }

Le lint tourne toujours en entier. Le périmètre de pytest se déduit du diff avec origin/main,
travail non commité compris :
  maestro/**          les suites applicatives (celles qui ne pilotent aucun script du dépôt)
  scripts/**, .claude/**, .github/**  les suites qui NOMMENT le fichier modifié
  tests/test_*.py     elles-mêmes
  conftest.py, pyproject.toml, ou tout chemin non classé   la suite entière
  apps/web/**, docs/**, *.md   aucune suite pytest (web-build couvre le front)

web-build ne tourne que si apps/web (ou .github/workflows/ci.yml) change par rapport à origin/main —
même règle que le pipeline. « --only web-build » le force.

pytest tourne sur min(cœurs, $PYTEST_WORKERS_PLAFOND) workers : au-delà, c'est la mémoire qui
borne, pas les cœurs (#285). MAESTRO_PYTEST_WORKERS déplace ce plafond.
USAGE
}

liste_jobs() {
  printf 'Jobs rejoués par le filet local (source : %s)\n\n' ".github/workflows/ci.yml"
  printf '  %-12s %-6s %s\n' JOB ÉTAGE COMMANDE
  printf '  %-12s %-6s %s\n' shellcheck   lint "shellcheck --severity=$SHELLCHECK_SEVERITE <script>, un appel par fichier de scripts/"
  printf '  %-12s %-6s %s\n' python-lint  lint "ruff check ."
  if [ "$MODE_PYTEST" = complet ]; then
    printf '  %-12s %-6s %s\n' pytest     test "python -m pytest -n $(workers_pytest) --cov=maestro --cov-fail-under=$COUVERTURE_MIN"
  else
    printf '  %-12s %-6s %s\n' pytest     test "python -m pytest <suites déduites du diff>  (--complet : toute la suite + couverture)"
  fi
  printf '  %-12s %-6s %s\n' mypy         test "mypy maestro"
  printf '  %-12s %-6s %s\n' web-build    test "npm run lint && npm run typecheck && npm test && npm run build (dans apps/web)"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --only) JOBS_ONLY="${2:-}"; shift ;;
    --skip) JOBS_SKIP="${2:-}"; shift ;;
    --strict) STRICT=1 ;;
    --rapide) MODE_PYTEST=rapide ;;
    --complet) MODE_PYTEST=complet ;;
    --list) liste_jobs; exit 0 ;;
    -h | --help) usage; exit 0 ;;
    *) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# Développe les alias d'étage (« lint », « test ») en noms de jobs, pour que --only lint marche.
developpe_etages() {
  local liste="$1" item sortie=""
  for item in ${liste//,/ }; do
    case "$item" in
      lint) sortie="$sortie $ETAGE_LINT" ;;
      test) sortie="$sortie $ETAGE_TEST" ;;
      *) sortie="$sortie $item" ;;
    esac
  done
  printf '%s\n' "${sortie# }"
}
JOBS_ONLY="$(developpe_etages "$JOBS_ONLY")"
JOBS_SKIP="$(developpe_etages "$JOBS_SKIP")"

# Un nom inconnu dans --only/--skip est une faute de frappe, pas un job : le dire tout de suite
# plutôt que de rendre un « tout vert » qui n'a rien joué.
for demande in $JOBS_ONLY $JOBS_SKIP; do
  case " $JOBS_CONNUS " in
    *" $demande "*) ;;
    *) printf 'Job inconnu : %s\n\nJobs disponibles : %s\n' "$demande" "$JOBS_CONNUS" >&2; exit 2 ;;
  esac
done

job_demande() {
  local job="$1"
  if [ -n "$JOBS_ONLY" ] && [[ " $JOBS_ONLY " != *" $job "* ]]; then return 1; fi
  if [ -n "$JOBS_SKIP" ] && [[ " $JOBS_SKIP " == *" $job "* ]]; then return 1; fi
  return 0
}

# --- Interpréteurs du dépôt -----------------------------------------------------------------------
# venv_bin <nom> : l'exécutable du venv du dépôt (Scripts/*.exe sous Windows, bin/* ailleurs).
# Renvoie 1 s'il n'y est pas — on ne retombe JAMAIS sur le python du système, dont les dépendances
# ne sont pas celles du projet (CLAUDE.md, « Environnement Python »).
venv_bin() {
  local exe
  if [ "$WINDOWS" = 1 ]; then exe="$RACINE/.venv/Scripts/$1.exe"; else exe="$RACINE/.venv/bin/$1"; fi
  [ -x "$exe" ] || return 1
  printf '%s\n' "$exe"
}

# node_bindir : le dossier du Node VENDORÉ (.tools/node/v<pin>), version épinglée par
# .node-version. Absent ⇒ 1 : le node du système est hors sujet ici (#153).
node_bindir() {
  local pin racine_node
  pin="$(tr -d ' \t\r\n' < "$RACINE/.node-version" 2>/dev/null)"
  pin="${pin#v}"
  [ -n "$pin" ] || return 1
  racine_node="$RACINE/.tools/node/v$pin"
  if [ "$WINDOWS" = 1 ]; then
    [ -x "$racine_node/node.exe" ] || return 1
    printf '%s\n' "$racine_node"
  else
    [ -x "$racine_node/bin/node" ] || return 1
    printf '%s/bin\n' "$racine_node"
  fi
}

chemin_natif() {
  if [ "$WINDOWS" = 1 ] && command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s\n' "$1"; fi
}

# --- Ce que la branche apporte ---------------------------------------------------------------------
# Les chemins (relatifs à la racine) que la branche modifie par rapport à origin/main, TRAVAIL NON
# COMMITÉ COMPRIS : c'est ce qui partira au push, donc ce sur quoi le pipeline se prononcera. Sert
# au périmètre de pytest (#214) comme à celui de web-build.
fichiers_modifies() {
  local base
  base="$(git -C "$RACINE" merge-base origin/main HEAD 2>/dev/null)"
  {
    [ -n "$base" ] && git -C "$RACINE" diff --name-only "$base" -- 2>/dev/null
    git -C "$RACINE" status --porcelain --untracked-files=all 2>/dev/null | cut -c4-
  } | tr -d '"' | grep -v '^[[:space:]]*$' | sort -u
}

# --- Exécution d'un job ----------------------------------------------------------------------------
# Chaque job_* renvoie : 0 vert · 1 ÉCHEC dur · 2 non jouable (outil absent) · 3 hors périmètre.
# Il pose DETAIL (une ligne, affichée à côté du statut) ; sa sortie complète va dans $JOURNAL,
# affichée seulement en cas d'échec — c'est là qu'on en a besoin.
DETAIL=""
JOURNAL=""

execute() { ( cd "$RACINE" && "$@" ) >"$JOURNAL" 2>&1; }

# Dernière ligne non vide du journal, pour résumer un job en une ligne quand il n'a rien de mieux.
derniere_ligne() {
  grep -v '^[[:space:]]*$' "$JOURNAL" 2>/dev/null | tail -1 | cut -c1-100
}

# Nombre de lignes du journal correspondant au motif. `grep -c` sort en 1 quand il n'en trouve
# aucune : sans ce garde-fou, un « || echo ? » collerait un point d'interrogation derrière le 0.
compte_lignes() {
  grep -cE -- "$1" "$JOURNAL" 2>/dev/null | head -1
}

# --- Job shellcheck --------------------------------------------------------------------------------
# UN APPEL PAR FICHIER, et non un appel unique portant les 21 scripts (#285). shellcheck est
# SUPERLINÉAIRE en nombre de fichiers reçus d'un coup — mesuré sur ce dépôt, dans le même
# conteneur : 1 fichier 1 s · 6 fichiers 3 s · 11 fichiers 16 s · 21 fichiers 38 s, contre 14 s
# pour vingt-et-un appels d'un fichier. Le job passe de ~53 s à ~16 s.
#
# On agrège les deux choses qui font le verdict : les SORTIES, concaténées dans le même journal, et
# les CODES DE RETOUR — non nul dès qu'un fichier en rend un, jamais celui du dernier.
#
# ⚠ CE DÉCOUPAGE N'EST PAS NEUTRE, et c'est pourquoi le PIPELINE A ÉTÉ DÉCOUPÉ AVEC LUI
# (.github/workflows/ci.yml, même boucle). Un `# shellcheck source=…` n'est suivi que si le fichier sourcé est
# LUI AUSSI sur la ligne de commande — ou si `-x` est passé. L'appel groupé les portait tous, donc
# plus, et les vérifications qui raisonnent sur la portée des variables changent d'avis : une
# variable posée par un script et lue seulement par une fonction du fichier qu'il source ressort en
# SC2034, le couplage étant désormais déclaré sur place par un `# shellcheck disable=` commenté. Le
# sens de l'écart est toujours le même : moins de contexte, donc PLUS de remarques, jamais moins —
# un faux rouge possible, jamais un faux vert.
#
# `-x` rétablirait le lien depuis le disque, mais rend le découpage inutile : il fait ré-analyser
# `lib.sh` par chacun des huit scripts qui la sourcent, et le job repasse à 34 s (mesuré). Les deux
# côtés découpés à l'identique valent mieux qu'un filet plus strict que la CI qu'il prédit.
image_docker_disponible() { docker image inspect "$1" >/dev/null 2>&1; }

# La boucle telle que le `sh` du conteneur la déroulera : « <sévérité> <fichier>… » en arguments.
# Écrite en POSIX (pas de `local`, pas de tableau) — l'image n'a pas forcément bash.
BOUCLE_SHELLCHECK='severite="$1"; shift; code=0
for fichier in "$@"; do shellcheck "--severity=$severite" "$fichier" || code=1; done
exit "$code"'

conseil_shellcheck() {
  case "$WINDOWS" in
    1) printf 'winget install koalaman.shellcheck (ou docker pull %s)' "$SHELLCHECK_IMAGE" ;;
    *) printf 'brew install shellcheck / apt install shellcheck (ou docker pull %s)' "$SHELLCHECK_IMAGE" ;;
  esac
}

job_shellcheck() {
  local fichiers nb tmp fichier cible code
  fichiers="$(cd "$RACINE" && find scripts -type f -name '*.sh' | sort)"
  if [ -z "$fichiers" ]; then
    DETAIL="aucun script *.sh à analyser"
    return 0
  fi
  nb="$(printf '%s\n' "$fichiers" | wc -l | tr -d ' ')"

  # Miroir des scripts en fins de ligne LF. La CI checkout en LF ; une copie de travail Windows en
  # CRLF déclenche des SC1017 qui n'existent pas côté CI (docs/10 §8) — le filet mentirait dans
  # les deux sens. On analyse donc ce que la CI verra, pas ce que le disque contient.
  #
  # Ce miroir-ci RESTE dans le temporaire du système, contrairement aux journaux (#234) : il n'est
  # jamais montré à personne — la sortie de shellcheck désigne les fichiers du dépôt en relatif, et
  # le miroir est effacé avant même le verdict. Il n'oriente donc aucune session vers un chemin
  # hors du worktree. Le déplacer sous la racine changerait en revanche le point de montage du repli
  # docker (`-v "$tmp:/mnt"`) de la partition temporaire vers celle du dépôt : un risque de partage
  # de volume, sans rien à y gagner.
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/maestro-ci-shellcheck.XXXXXX" 2>/dev/null)" || {
    DETAIL="impossible de créer un dossier temporaire"
    return 2
  }
  while IFS= read -r fichier; do
    [ -n "$fichier" ] || continue
    cible="$tmp/$fichier"
    mkdir -p "$(dirname "$cible")"
    tr -d '\r' <"$RACINE/$fichier" >"$cible"
  done <<EOF
$fichiers
EOF

  # Les chemins restent relatifs (cd dans le miroir) : la sortie désigne les fichiers du dépôt.
  # $fichiers est volontairement non quoté — une liste d'arguments, comme dans .github/workflows/ci.yml.
  if command -v shellcheck >/dev/null 2>&1; then
    # Un sous-shell pour TOUTE la boucle, pas un par fichier : sous Windows chaque `fork` coûte
    # ~50 ms, et le job en fait déjà un par appel à shellcheck.
    (
      cd "$tmp" || exit 2
      code=0
      for fichier in $fichiers; do
        shellcheck "--severity=$SHELLCHECK_SEVERITE" "$fichier" || code=1
      done
      exit "$code"
    ) >"$JOURNAL" 2>&1
    code=$?
  elif image_docker_disponible "$SHELLCHECK_IMAGE"; then
    # Repli sans réseau : l'image de la CI, mais seulement si elle est DÉJÀ sur la machine — le
    # filet ne télécharge rien. MSYS_NO_PATHCONV empêche Git Bash de réécrire « /mnt » en chemin
    # Windows avant que docker ne le voie.
    #
    # UN SEUL CONTENEUR, la boucle est à l'intérieur (#285). Démarrer le conteneur coûte ~2 s :
    # un `docker run` par fichier rendrait au ménage ce que la boucle fait gagner (21 × 2 s = 42 s,
    # soit plus que les 38 s de l'appel unique qu'on remplace). C'est le `sh -c` de l'image qui
    # déroule la boucle — d'où un script POSIX, sans `local` ni tableau.
    MSYS_NO_PATHCONV=1 docker run --rm -v "$(chemin_natif "$tmp"):/mnt" -w /mnt \
      "$SHELLCHECK_IMAGE" sh -c "$BOUCLE_SHELLCHECK" shellcheck \
      "$SHELLCHECK_SEVERITE" $fichiers >"$JOURNAL" 2>&1
    code=$?
  else
    rm -rf "$tmp"
    DETAIL="shellcheck absent, et l'image $SHELLCHECK_IMAGE n'est pas sur la machine — $(conseil_shellcheck)"
    return 2
  fi
  rm -rf "$tmp"

  if [ "$code" -ne 0 ]; then
    # Une remarque = une ligne « SCxxxx (severité) ». Le pied de page « For more information »
    # reprend les mêmes codes sous la forme « SCxxxx -- » : la parenthèse les écarte.
    DETAIL="$nb script(s) — $(compte_lignes 'SC[0-9]+ \(') remarque(s) de sévérité $SHELLCHECK_SEVERITE+"
    return 1
  fi
  DETAIL="$nb script(s) analysé(s), rien à signaler (sévérité $SHELLCHECK_SEVERITE+)"
  return 0
}

# --- Jobs Python -----------------------------------------------------------------------------------
job_ruff() {
  local exe trouve
  exe="$(venv_bin ruff)" || {
    DETAIL="ruff absent du venv du dépôt — bash scripts/setup.sh --only venv"
    return 2
  }
  if execute "$exe" check .; then
    DETAIL="$(derniere_ligne)"
    return 0
  fi
  # ruff termine par « Found N errors » : on reprend son propre décompte plutôt que de recompter
  # des lignes, dont le format a déjà changé d'une version à l'autre.
  trouve="$(grep -oE 'Found [0-9]+ errors?' "$JOURNAL" 2>/dev/null | tail -1)"
  DETAIL="ruff : ${trouve#Found }"
  [ -n "$trouve" ] || DETAIL="ruff : erreur(s), voir le journal"
  return 1
}

# Où `import maestro` se résout POUR LE LANCEUR ET LE RÉPERTOIRE du job — la question qui décide si
# le verdict de pytest est digne de foi (#194). Le `.venv` est partagé par jonction entre le clone
# principal et ses worktrees (docs/10 §9) et y installe `maestro` en éditable POINTÉ SUR LE CLONE
# PRINCIPAL : un lanceur qui n'ajoute pas le répertoire courant à `sys.path` importe alors le code
# d'une AUTRE branche. La sonde tourne dans le même répertoire et par le même python que le job
# — c'est ce qui la rend fidèle — et compare côté Python, où les chemins n'ont pas à traverser la
# conversion MSYS.
sonde_maestro() { # <python> → « ICI » | « AILLEURS <chemin> » | « ABSENT <erreur> »
  ( cd "$RACINE" && "$1" - <<'PY' 2>/dev/null
import os

attendu = os.path.join(os.getcwd(), "maestro")
try:
    import maestro
except Exception as erreur:  # large à dessein : tout import raté rend le job non jouable
    print("ABSENT", erreur)
else:
    paquet = os.path.dirname(os.path.realpath(maestro.__file__))
    memes = os.path.normcase(paquet) == os.path.normcase(os.path.realpath(attendu))
    print("ICI" if memes else "AILLEURS " + paquet)
PY
  )
}

# --- Périmètre de pytest (#214) --------------------------------------------------------------------
# Toutes les suites du dépôt, et celles dites d'OUTILLAGE — DÉDUITES, jamais listées à la main : une
# suite est « outillage » si elle NOMME un script du dépôt (`scripts/**/*.sh`), ce qui est le cas de
# celles qui montent un dépôt jetable pour piloter `lib.sh`, `run.sh`, `setup.sh`… Deux propriétés
# tiennent à cette dérivation : une suite d'outillage nouvelle se classe toute seule, et une suite
# qui ne nomme aucun script est APPLICATIVE par défaut — donc jouée dès que `maestro/` bouge, jamais
# sautée en silence. Ce sont aussi, mesuré, les seules lentes : ~360 tests pour 9 des 10 minutes de
# la suite, parce qu'elles attendent des processus.
suites_toutes() { (cd "$RACINE" && find tests -maxdepth 1 -name 'test_*.py' 2>/dev/null | sort); }

# Même ancrage que `suites_nommant` ci-dessous, et pour une raison plus forte (#372) : ici le sens
# de l'erreur est le DANGEREUX. L'applicatif est le complément de cet ensemble, donc une suite
# classée « outillage » à tort en sort — et n'est plus jouée quand `maestro/**` bouge, sans que
# rien ne le dise. C'est exactement le « jamais sautée en silence » de §8.4 pris à revers.
# Aujourd'hui aucune suite n'est dans ce cas (9 avant, 9 après, vérifié sur les 62 suites) : le
# changement est un no-op, et c'est la latence qu'il retire, pas un bug qu'il corrige.
suites_outillage() {
  local motifs=() script suites=() base
  mapfile -t suites < <(suites_toutes)
  [ "${#suites[@]}" -gt 0 ] || return 0
  while IFS= read -r script; do
    [ -n "$script" ] || continue
    base="$(basename "$script")"
    motifs+=(-e "(^|[^A-Za-z0-9_])$(printf '%s' "$base" | sed 's,[][^$.*+?(){}|\\],\\&,g')")
  done < <(cd "$RACINE" && find scripts -type f -name '*.sh' 2>/dev/null)
  [ "${#motifs[@]}" -gt 0 ] || return 0
  (cd "$RACINE" && grep -lE "${motifs[@]}" "${suites[@]}" 2>/dev/null | sort)
}

# Les suites qui citent <chaîne> — le nom du fichier modifié. Ces tests-là invoquent le script ou
# lisent le fichier par son chemin : le nommer est le lien le plus direct qu'on puisse observer
# sans exécuter quoi que ce soit.
#: `suites_nommant <nom> [ancre]` — les suites qui citent ce nom.
#:
#: Le second argument choisit la RIGUEUR, et les deux essais de `classe_par_nom` n'en veulent pas
#: la même (#372) :
#:
#:   - un NOM DE FICHIER se cherche ancré à gauche sur un non-mot. Sans ancrage, `lib.sh` matche
#:     le `hashlib.sha256` de tests/test_setup.py — et tirait donc cette suite entière dans le
#:     périmètre de TOUT diff touchant scripts/gitlab/lib.sh, c'est-à-dire le diff le plus courant
#:     du dépôt. Vérifié exhaustivement avant d'ancrer, sur les 462 noms du dépôt croisés avec les
#:     mots des 62 suites : `hashlib.sh` est le seul faux positif, et l'ancrage ne fait perdre
#:     AUCUNE correspondance réelle (les tests citent leurs scripts en « scripts/gitlab/lib.sh »
#:     ou « lib.sh », toujours précédés d'un `/`, d'un guillemet ou d'un blanc).
#:
#:   - un CHEMIN DE DOSSIER se cherche sans ancrage, et l'ancrage n'y changerait rien. #372
#:     l'avait laissé souple pour une raison qui a cessé d'exister entre-temps : le repli
#:     cherchait alors le nom NU du dossier, et l'ancrer aurait fait perdre des suites sur des
#:     noms courts et fréquents (`api`, `app`, `brief`, `gitlab` en perdaient chacun une à
#:     quatre). #375 a remplacé ce nom nu par le chemin AVEC son séparateur — `scripts/gitlab/`
#:     et non `gitlab` —, et un chemin est toujours cité précédé d'un `/`, d'un guillemet ou
#:     d'un blanc. Re-mesuré sur le dépôt du jour, 82 dossiers imbriqués croisés avec les
#:     62 suites : **0 écart** entre les deux formes. Le `-F` reste donc par économie et non
#:     par nécessité, et la souplesse d'hier n'est plus un argument à invoquer ici.
suites_nommant() {
  local suites=() motif
  mapfile -t suites < <(suites_toutes)
  [ "${#suites[@]}" -gt 0 ] || return 0
  if [ "${2:-}" = ancre ]; then
    # Le nom est une donnée, pas un motif : ses métacaractères ERE sont échappés avant usage.
    motif="$(printf '%s' "$1" | sed 's,[][^$.*+?(){}|\\],\\&,g')"
    (cd "$RACINE" && grep -lE -- "(^|[^A-Za-z0-9_])$motif" "${suites[@]}" 2>/dev/null | sort)
  else
    (cd "$RACINE" && grep -lF -- "$1" "${suites[@]}" 2>/dev/null | sort)
  fi
}

#: Résultat de `calcule_perimetre` : les suites à jouer (vide = aucune), pourquoi, et deux drapeaux.
PERIMETRE_SUITES=""
PERIMETRE_MOTIF=""
PERIMETRE_TOUT=0
PERIMETRE_OUTILLAGE=0
#: Les raisons du périmètre, par famille : ce qui a tout élargi, ce qui a sélectionné des suites,
#: ce qui n'a aucun effet sur pytest. Séparées, parce qu'on n'affiche que la famille qui explique
#: le verdict rendu. `CHOISIES` accumule les suites retenues.
RAISONS_TOUT=""
RAISONS_CHOIX=""
RAISONS_SANS=""
CHOISIES=""
#: Posé par le job pytest quand il n'a joué QU'UNE PARTIE de la suite — c'est ce que le verdict
#: final doit dire, sous peine de laisser croire à un vert qui vaut celui du pipeline.
PERIMETRE_REDUIT=0
#: Le job pytest a-t-il été joué ? Un étage lint rouge, un `--skip pytest` ou un venv incomplet
#: l'écartent : la mise en garde sur le périmètre n'aurait alors rien à qualifier.
PYTEST_JOUE=0
#: Posé par la sonde quand pytest-xdist manque : le job tourne quand même, en série, et le dit.
XDIST_ABSENT=0
#: Dérive des dépendances (#216), en TSV « <étape><TAB><raison> » — voir `derive_dependances`.
DERIVE_LIGNES=""

# Déduit du diff les suites à jouer. Règle d'or : on n'élargit jamais en silence, mais on ne
# rétrécit jamais sur une supposition — tout fichier dont personne ne parle ramène la suite entière.
# Une raison, sans doublon, dans <variable> — le séparateur « | » est rendu en « , » à l'affichage.
# Sans dédoublonnage, un diff de dix fichiers de prose répétait dix fois la même explication et
# noyait la seule ligne que l'on lit vraiment.
ajoute_raison() { # <nom-de-variable> <raison>
  local courant="${!1}"
  case "|$courant|" in
    *"|$2|"*) return 0 ;;
  esac
  printf -v "$1" '%s' "$courant|$2"
}

rend_raisons() { printf '%s' "${1#|}" | tr '|' '
' | paste -sd, - | sed 's/,/, /g'; }

# La règle du NOM, appliquée à un fichier : les suites qui le citent, ou la suite entière si
# personne ne le cite. Pose ses résultats dans CHOISIES / PERIMETRE_TOUT (variables de l'appelant).
classe_par_nom() { # <chemin>
  local base dossier nommant
  base="$(basename "$1")"
  nommant="$(suites_nommant "$base" ancre)"
  if [ -n "$nommant" ]; then
    CHOISIES="$CHOISIES$nommant"$'
'
    ajoute_raison RAISONS_CHOIX "$base"
    return 0
  fi
  # Second essai, par le DOSSIER : une suite qui relit tout un répertoire le désigne par son
  # CHEMIN, jamais par celui de ses fichiers — `test_collaboration` parcourt
  # `.claude/commands/*.md` (#196) sans citer aucun prompt en particulier. Repli seulement :
  # quand le nom du fichier a répondu, il est plus précis.
  #
  # Le chemin AVEC son séparateur, et jamais le nom nu du dossier (#375) : cherché en sous-chaîne,
  # un nom court et courant en français matche la prose de n'importe quelle suite. Mesuré sur main
  # au 2026-08-18 — `migration` → 10 suites dont aucune ne teste `scripts/migration/`, `github` →
  # `test_cycle_de_vie`, et `ci` → 59 suites sur 61, « ci » étant une sous-chaîne d'« ici », de
  # « précis », de « spécifique »… Le repli REMPLAÇANT l'élargissement, ces suites tirées au sort
  # ne coûtaient pas du temps (elles sont rapides) mais un FAUX VERT annoncé avec un motif
  # crédible : « périmètre : 10 suite(s) (migration/) » sur un script que rien ne teste.
  #
  # Un dossier de PREMIER niveau est écarté pour la même raison : `scripts/` est une sous-chaîne
  # de tout `scripts/gitlab/lib.sh` cité quelque part (10 suites mesurées), il ne désigne aucun
  # répertoire en particulier. Rien de crédible ⇒ on s'abstient, donc on élargit — le sens de
  # dérive du filet, jamais l'inverse.
  dossier="$(dirname "$1")"
  nommant=""
  case "$dossier" in
    */*) nommant="$(suites_nommant "$dossier/")" ;;
  esac
  if [ -z "$nommant" ]; then
    PERIMETRE_TOUT=1
    ajoute_raison RAISONS_TOUT "aucune suite ne nomme $base"
  else
    CHOISIES="$CHOISIES$nommant"$'
'
    ajoute_raison RAISONS_CHOIX "$dossier/"
  fi
}

calcule_perimetre() {
  PERIMETRE_SUITES=""
  PERIMETRE_MOTIF=""
  PERIMETRE_TOUT=0
  PERIMETRE_OUTILLAGE=0
  RAISONS_TOUT=""
  RAISONS_CHOIX=""
  RAISONS_SANS=""
  CHOISIES=""

  local modifies outillage applicatives fichier
  modifies="$(fichiers_modifies)"
  if [ -z "$modifies" ]; then
    PERIMETRE_MOTIF="rien de modifié par rapport à origin/main"
    return 0
  fi

  outillage="$(suites_outillage)"
  applicatives="$(comm -23 <(suites_toutes) <(printf '%s
' "$outillage" | grep -v '^$' || true))"

  while IFS= read -r fichier; do
    [ -n "$fichier" ] || continue
    case "$fichier" in
      # Le conftest et les dépendances valent pour toute la suite ; leur périmètre, c'est tout.
      tests/conftest.py | pyproject.toml | .node-version | tests/*/*)
        PERIMETRE_TOUT=1
        ajoute_raison RAISONS_TOUT "$fichier (transverse)"
        ;;
      tests/test_*.py)
        CHOISIES="$CHOISIES$fichier"$'
'
        ajoute_raison RAISONS_CHOIX "suite modifiée"
        ;;
      tests/*)
        PERIMETRE_TOUT=1
        ajoute_raison RAISONS_TOUT "$fichier (transverse)"
        ;;
      # Le couplage à l'intérieur de `maestro/` est réel et invisible d'ici (un module de
      # télémétrie touché casse le moteur sans que son test le nomme) : on ne raffine donc PAS
      # par module — toutes les suites applicatives, 40 s, et aucun faux négatif.
      maestro/*)
        CHOISIES="$CHOISIES$applicatives"$'
'
        ajoute_raison RAISONS_CHOIX "maestro/** modifié"
        ;;
      # Prose et front : aucune suite pytest ne les lit (web-build couvre apps/web).
      docs/* | apps/web/*)
        ajoute_raison RAISONS_SANS "${fichier%%/*}/ (sans effet sur pytest)"
        ;;
      # Les chemins IMBRIQUÉS passent par la règle du nom, y compris les .md :
      # `.claude/commands/*.md` est lu par test_collaboration (#196). Seule la prose de la RACINE
      # (README, CLAUDE.md…) est sans effet — d'où l'ordre de ces deux motifs.
      */*)
        classe_par_nom "$fichier"
        ;;
      *.md)
        ajoute_raison RAISONS_SANS "prose (sans effet sur pytest)"
        ;;
      *)
        classe_par_nom "$fichier"
        ;;
    esac
  done <<EOF
$modifies
EOF

  if [ "$PERIMETRE_TOUT" = 1 ]; then
    # Quand le périmètre s'élargit à tout, seule la CAUSE de l'élargissement éclaire : citer au
    # passage les fichiers de prose du même diff noierait la seule ligne qu'on lit vraiment.
    PERIMETRE_MOTIF="$(rend_raisons "$RAISONS_TOUT")"
    PERIMETRE_SUITES="$(suites_toutes)"
    PERIMETRE_OUTILLAGE=1
    return 0
  fi
  PERIMETRE_SUITES="$(printf '%s
' "$CHOISIES" | grep -v '^$' | sort -u || true)"
  if [ -n "$PERIMETRE_SUITES" ]; then
    PERIMETRE_MOTIF="$(rend_raisons "$RAISONS_CHOIX")"
  else
    PERIMETRE_MOTIF="$(rend_raisons "$RAISONS_SANS")"
  fi
  # Le parallélisme ne paye que sur les suites d'outillage : ~5,5 s de démarrage des workers, à
  # comparer aux 1,5 s de tests/test_engine.py en série. Elles seules valent qu'on les paye.
  if [ -n "$outillage" ] && [ -n "$PERIMETRE_SUITES" ] &&
    printf '%s
' "$PERIMETRE_SUITES" | grep -qxF -f <(printf '%s
' "$outillage"); then
    PERIMETRE_OUTILLAGE=1
  fi
}

# `-n <n>` sur un venv sans pytest-xdist sort en erreur d'arguments : un ROUGE qui ne parle
# pas du code. Le venv d'un clone antérieur à #214 est dans ce cas tant que
# `setup.sh --only venv` n'a pas rejoué `pip install -e ".[dev]"`. Comme pour tout ce qui
# manque ici : on ne l'installe pas dans le dos, on s'en passe et on le dit — le remède est
# porté par le bloc « Dépendances en retard » du résumé, qui couvre tous les cas (#216).
xdist_disponible() { # <python>
  if ( cd "$RACINE" && "$1" -c "import xdist" ) >/dev/null 2>&1; then
    return 0
  fi
  XDIST_ABSENT=1
  return 1
}

# Dérive des dépendances (#216) — la question que `pytest-xdist absent` posait pour un seul paquet :
# ce clone a-t-il pris ce que le dépôt a ajouté depuis sa mise en route ? La réponse est demandée à
# `setup.sh --derive`, qui la détient déjà (pyproject.toml, package-lock.json, .node-version) et ne
# fait pour ça ni réseau ni écriture. Le filet, lui, SIGNALE : installer dans le dos de qui lance un
# contrôle, c'est changer l'environnement dont il attend un verdict (docs/10 §8.4).
derive_dependances() {
  local setup="$RACINE/scripts/setup.sh" lignes code
  [ -f "$setup" ] || return 0
  lignes="$(bash "$setup" --derive 2>/dev/null)"
  code=$?
  # 3 = dérive ; 0 = à jour ; autre = sonde indisponible — dans les deux derniers cas, rien à dire.
  [ "$code" -eq 3 ] && DERIVE_LIGNES="$lignes"
  return 0
}

job_pytest() {
  local exe couverture tests sonde args=() suite nb=0 workers
  # pytest est lancé par `python -m`, mais c'est bien la présence du SCRIPT CONSOLE qui dit s'il est
  # installé dans le venv du dépôt : `python -m pytest` sur un venv sans pytest sortirait en 1, donc
  # en ÉCHEC, là où « outil absent » doit rendre IGNORÉ (même traitement que ruff et mypy).
  venv_bin pytest >/dev/null || {
    DETAIL="pytest absent du venv du dépôt — bash scripts/setup.sh --only venv"
    return 2
  }
  exe="$(venv_bin python)" || {
    DETAIL="python absent du venv du dépôt — bash scripts/setup.sh --only venv"
    return 2
  }
  # Un job qui ne teste pas le code d'ici ne rend NI vert NI rouge : les deux mentiraient. Le rouge
  # se voit (couverture 0 %) ; le vert, lui, passe inaperçu — un correctif cassé sort vert parce que
  # le code fautif n'a jamais été chargé. On le dit, et le verdict n'en tient pas compte.
  sonde="$(sonde_maestro "$exe")"
  case "$sonde" in
    ICI) ;;
    AILLEURS*)
      DETAIL="couverture et tests non mesurables ici : « import maestro » résout vers ${sonde#AILLEURS } au lieu du dépôt courant (venv partagé, docs/10 §9)"
      return 2
      ;;
    ABSENT*)
      DETAIL="le paquet maestro ne s'importe pas (${sonde#ABSENT }) — bash scripts/setup.sh --only venv"
      return 2
      ;;
    *)
      DETAIL="impossible de vérifier quel paquet maestro serait testé (sonde muette) — le verdict serait sans valeur"
      return 2
      ;;
  esac
  # Un seul calcul pour les trois branches qui suivent — il fork (`nproc`), autant ne le faire
  # qu'une fois.
  workers="$(workers_pytest)"
  if [ "$MODE_PYTEST" = complet ]; then
    # Ce que joue la CI, au drapeau de parallélisme près : suite entière, couverture, seuil.
    args=(--cov=maestro "--cov-fail-under=$COUVERTURE_MIN")
    xdist_disponible "$exe" && args=(-n "$workers" "${args[@]}")
    PERIMETRE_MOTIF="--complet"
  else
    calcule_perimetre
    if [ "$PERIMETRE_TOUT" = 1 ]; then
      # Le périmètre couvre tout : autant le dire ainsi et laisser pytest collecter lui-même.
      xdist_disponible "$exe" && args=(-n "$workers")
    elif [ -z "$PERIMETRE_SUITES" ]; then
      # Rien de testable n'a bougé (prose, front). Ni vert ni rouge : hors périmètre, comme
      # web-build quand apps/web n'a pas changé.
      DETAIL="aucune suite concernée — $PERIMETRE_MOTIF"
      PERIMETRE_REDUIT=1
      PYTEST_JOUE=1
      return 3
    else
      if [ "$PERIMETRE_OUTILLAGE" = 1 ] && xdist_disponible "$exe"; then args=(-n "$workers"); fi
      while IFS= read -r suite; do
        [ -n "$suite" ] || continue
        args+=("$suite")
        nb=$((nb + 1))
      done <<EOF
$PERIMETRE_SUITES
EOF
      PERIMETRE_REDUIT=1
    fi
  fi
  # `python -m` et non le script console `pytest` : lui seul ajoute le répertoire courant à
  # `sys.path` (le script console, lui, y met le dossier du script — #194). Sans ça, dans un
  # worktree, les tests s'exécutent contre le `maestro/` du clone principal pendant que
  # `--cov=maestro` instrumente celui d'ici — d'où une couverture à 0 %, et surtout un verdict qui
  # ne porte pas sur la branche qu'on s'apprête à pousser.
  PYTEST_JOUE=1
  execute "$exe" -m pytest "${args[@]}"
  local code=$?
  couverture="$(grep -E '^TOTAL' "$JOURNAL" 2>/dev/null | tail -1 | awk '{print $NF}')"
  tests="$(grep -oE '[0-9]+ (passed|failed|error)[^=]*' "$JOURNAL" 2>/dev/null | tail -1 | sed 's/[[:space:]]*$//')"
  DETAIL="${tests:-$(derniere_ligne)}${couverture:+ — couverture $couverture (seuil $COUVERTURE_MIN %)}"
  if [ "$nb" -gt 0 ]; then
    DETAIL="$DETAIL — périmètre : $nb suite(s) ($PERIMETRE_MOTIF)"
  elif [ "$MODE_PYTEST" != complet ]; then
    DETAIL="$DETAIL — périmètre : toute la suite ($PERIMETRE_MOTIF)"
  fi
  if [ "$XDIST_ABSENT" = 1 ]; then
    DETAIL="$DETAIL — en série (pytest-xdist absent du venv)"
  fi
  [ "$code" -eq 0 ] || return 1
  return 0
}

job_mypy() {
  local exe
  exe="$(venv_bin mypy)" || {
    DETAIL="mypy absent du venv du dépôt — bash scripts/setup.sh --only venv"
    return 2
  }
  if execute "$exe" maestro; then
    DETAIL="$(derniere_ligne)"
    return 0
  fi
  DETAIL="$(compte_lignes ': error: ') erreur(s) de typage"
  return 1
}

# --- Job web-build ---------------------------------------------------------------------------------
# Le décompte de Vitest, lu sur SA ligne de résumé (« Tests  N failed | M passed »). On vise
# « Tests » et non « Test Files » — qui compte des FICHIERS —, ni le détail par fichier
# (« (1 test | 1 failed) »), qui porte les mêmes mots et arrive plus tôt dans le journal : un
# simple `grep -oE '[0-9]+ failed' | head -1` rapporterait le mauvais nombre.
resume_vitest() { # <failed|passed>
  grep -E '^[[:space:]]*Tests[[:space:]]' "$JOURNAL" 2>/dev/null | grep -oE "[0-9]+ $1"
}

# Le pipeline ne joue ce job que si apps/web (ou .github/workflows/ci.yml) change : même règle ici, évaluée
# sur ce que la branche apporte à origin/main, travail non commité compris — c'est ce qui partira
# au push. « --only web-build » passe outre (on l'a demandé explicitement).
web_concerne() {
  case " $JOBS_ONLY " in *" web-build "*) return 0 ;; esac
  fichiers_modifies | grep -qE '^(apps/web/|\.github/workflows/)'
}

job_web() {
  local bindir
  if ! web_concerne; then
    DETAIL="apps/web inchangé par rapport à origin/main — le pipeline ne le joue pas non plus"
    return 3
  fi
  bindir="$(node_bindir)" || {
    DETAIL="Node du dépôt absent (.tools/node, cf. .node-version) — bash scripts/setup.sh --only node"
    return 2
  }
  [ -d "$RACINE/apps/web/node_modules" ] || {
    DETAIL="dépendances de apps/web absentes — bash scripts/setup.sh --only web"
    return 2
  }
  # `npm ci` de la CI n'est pas rejoué : il retélécharge le monde alors que setup.sh a déjà posé
  # node_modules depuis le même lockfile. On enchaîne lint, typage, tests puis build, comme le job.
  PATH="$bindir:$PATH" execute npm --prefix apps/web run lint || {
    DETAIL="eslint : erreur(s) — voir le journal"
    return 1
  }
  # Le typage passe AVANT vitest et next build : il coûte quelques secondes là où ils en coûtent
  # des dizaines, et rend l'erreur en clair au lieu d'un `next build` rouge en fin de course. C'est
  # aussi ce qui garde le script `typecheck` vivant : exposé pour qu'une session vérifie TypeScript
  # par `npm run typecheck` — la seule forme que la couche permissions autorise (#236) —, il
  # pourrirait sans personne pour le jouer.
  PATH="$bindir:$PATH" execute npm --prefix apps/web run typecheck || {
    DETAIL="tsc --noEmit : erreur(s) de typage — voir le journal"
    return 1
  }
  # NO_COLOR : sans lui, Vitest colore son résumé et les codes ANSI s'intercalent dans la ligne
  # qu'on lit juste après — elle ne commencerait même plus par « Tests ».
  PATH="$bindir:$PATH" NO_COLOR=1 execute npm --prefix apps/web test || {
    DETAIL="vitest : $(resume_vitest failed | head -1)"
    [ "$DETAIL" != "vitest : " ] || DETAIL="vitest : échec(s), voir le journal"
    return 1
  }
  # Le total des tests, pour que le job dise ce qu'il a joué et non seulement qu'il est vert.
  local tests
  tests="$(resume_vitest passed | head -1)"
  PATH="$bindir:$PATH" execute npm --prefix apps/web run build || {
    DETAIL="next build : échec (le typage TypeScript y est vérifié)"
    return 1
  }
  DETAIL="eslint + tsc + vitest (${tests:-?}) + next build verts"
  return 0
}

lance_job() {
  case "$1" in
    shellcheck) job_shellcheck ;;
    python-lint) job_ruff ;;
    pytest) job_pytest ;;
    mypy) job_mypy ;;
    web-build) job_web ;;
    *) DETAIL="job inconnu"; return 2 ;;
  esac
}

# --- Déroulé -----------------------------------------------------------------------------------------
RESULTATS=()
NB_ECHECS=0
NB_IGNORES=0
NB_JOUES=0

symbole() {
  case "$1" in
    OK) printf '%s✓%s' "$C_G" "$C_0" ;;
    ECHEC) printf '%s✗%s' "$C_R" "$C_0" ;;
    *) printf '%s~%s' "$C_Y" "$C_0" ;;
  esac
}

# Libellés complétés à la main (et non par %-12s) : printf compte des OCTETS, et « IGNORÉ » en
# pèse 7 pour 6 caractères — la colonne partirait de travers dès le premier accent.
libelle() {
  case "$1" in
    OK) printf 'OK           ' ;;
    ECHEC) printf 'ÉCHEC        ' ;;
    IGNORE) printf 'IGNORÉ       ' ;;
    SAUTE) printf 'SAUTÉ        ' ;;
    HORS) printf 'HORS PÉRIM.  ' ;;
    NONJOUE) printf 'NON JOUÉ     ' ;;
  esac
}

enregistre() { RESULTATS+=("$1|$2|$3"); }

duree_lisible() {
  local s="$1"
  if [ "$s" -lt 60 ]; then printf '%d s' "$s"; else printf '%d min %02d s' $((s / 60)) $((s % 60)); fi
}

joue_etage() {
  local etage="$1" jobs="$2" job code debut
  local entete_affichee=0
  for job in $jobs; do
    if ! job_demande "$job"; then
      enregistre "$job" SAUTE "demandé hors périmètre par --only/--skip"
      continue
    fi
    if [ "$entete_affichee" = 0 ]; then
      printf '%s[%s]%s\n' "$C_B" "$etage" "$C_0"
      entete_affichee=1
    fi
    JOURNAL="$LOG_DIR/$job.log"
    JOURNAL_REL="$LOG_DIR_REL/$job.log"
    DETAIL=""
    debut=$SECONDS
    lance_job "$job"
    code=$?
    case "$code" in
      0)
        printf '  %s %-12s %s  (%s)\n' "$(symbole OK)" "$job" "$DETAIL" "$(duree_lisible $((SECONDS - debut)))"
        enregistre "$job" OK "$DETAIL"
        NB_JOUES=$((NB_JOUES + 1))
        ;;
      1)
        printf '  %s %-12s %s  (%s)\n' "$(symbole ECHEC)" "$job" "$DETAIL" "$(duree_lisible $((SECONDS - debut)))"
        # Chemin RELATIF à la racine : tel quel, il se lit depuis le répertoire de travail — c'est
        # tout l'objet de #234. L'afficher en absolu suffirait à rendre la suite illisible.
        printf '%s\n' "    ─── journal : $JOURNAL_REL"
        sed -n '1,40p' "$JOURNAL" 2>/dev/null | sed 's/^/    /'
        [ "$(wc -l <"$JOURNAL" 2>/dev/null || echo 0)" -gt 40 ] && printf '    … (suite dans %s)\n' "$JOURNAL_REL"
        enregistre "$job" ECHEC "$DETAIL"
        NB_ECHECS=$((NB_ECHECS + 1))
        NB_JOUES=$((NB_JOUES + 1))
        ;;
      2)
        printf '  %s %-12s %s\n' "$(symbole IGNORE)" "$job" "$DETAIL"
        enregistre "$job" IGNORE "$DETAIL"
        NB_IGNORES=$((NB_IGNORES + 1))
        ;;
      3)
        printf '  %s %-12s %s\n' "$(symbole IGNORE)" "$job" "$DETAIL"
        enregistre "$job" HORS "$DETAIL"
        ;;
    esac
  done
  [ "$entete_affichee" = 1 ] && printf '\n'
  return 0
}

# Table rase à chaque lancement (#234) : dans /tmp le système faisait le ménage, sous la racine
# personne ne le ferait — les journaux s'y accumuleraient. Et un `pytest.log` d'hier laissé à côté
# d'un run qui n'a pas joué pytest est pire qu'absent : il ment sur ce qui vient d'être vérifié.
rm -rf "$LOG_DIR"
mkdir -p "$LOG_DIR"
branche="$(git -C "$RACINE" rev-parse --abbrev-ref HEAD 2>/dev/null)"
printf '\n%sFilet CI local%s — %s\n' "$C_B" "$C_0" "$RACINE"
printf 'branche : %s · les mêmes contrôles que .github/workflows/ci.yml, avec le venv et le Node du dépôt\n' "${branche:-?}"
if [ "$MODE_PYTEST" = complet ]; then
  printf 'pytest  : suite entière + couverture (--complet)\n\n'
else
  printf 'pytest  : périmètre du diff (--complet pour la suite entière et sa couverture)\n\n'
fi

joue_etage lint "$ETAGE_LINT"

if [ "$NB_ECHECS" -gt 0 ]; then
  # Comme la CI : l'étage test ne démarre pas si le lint est rouge. Pour le forcer malgré tout,
  # --only test.
  for job in $ETAGE_TEST; do
    if job_demande "$job"; then
      enregistre "$job" NONJOUE "étage lint en échec (comme la CI) — forcer : --only test"
    else
      enregistre "$job" SAUTE "demandé hors périmètre par --only/--skip"
    fi
  done
else
  joue_etage test "$ETAGE_TEST"
fi

# --- Résumé -------------------------------------------------------------------------------------
printf '%sRésumé%s\n' "$C_B" "$C_0"
if [ "${#RESULTATS[@]}" -eq 0 ]; then
  printf '  (aucun job joué)\n'
fi
for ligne in ${RESULTATS[@]+"${RESULTATS[@]}"}; do
  statut="${ligne#*|}"
  job="${ligne%%|*}"
  detail="${statut#*|}"
  statut="${statut%%|*}"
  printf '  %s %-12s %s  %s\n' "$(symbole "$statut")" "$job" "$(libelle "$statut")" "$detail"
done

printf '\n'
# Ce que le filet n'a PAS vérifié, dit avant le verdict — c'est la contrepartie assumée du mode
# rapide (#214), et elle ne doit pas se lire entre les lignes.
if [ "$MODE_PYTEST" != complet ] && [ "$PYTEST_JOUE" = 1 ]; then
  if [ "$PERIMETRE_REDUIT" = 1 ]; then
    printf '%sPérimètre réduit%s — pytest a joué les seules suites concernées par le diff, sans seuil\n' "$C_Y" "$C_0"
    printf 'de couverture. La suite entière : « --complet » ici, ou le pipeline de la MR (docs/10 §8).\n\n'
  else
    printf '%sCouverture non vérifiée%s — le seuil de %s %% est appliqué en « --complet » et en CI.\n\n' \
      "$C_Y" "$C_0" "$COUVERTURE_MIN"
  fi
fi
# Ce que ce clone n'a pas pris du dépôt (#216) : dit ici, jamais installé — c'est la contrepartie du
# principe « aucune installation dans le dos ». Le cas historique (pytest-xdist absent, #214) en est
# devenu un parmi d'autres : il rejoint le bloc au lieu d'avoir son message à lui.
derive_dependances
if [ -n "$DERIVE_LIGNES" ] || [ "$XDIST_ABSENT" = 1 ]; then
  etapes=""
  printf '%sDépendances en retard%s — ce clone n'\''a pas pris ce que le dépôt a ajouté depuis sa\n' \
    "$C_Y" "$C_0"
  printf 'mise en route :\n'
  while IFS="$(printf '\t')" read -r etape raison; do
    [ -n "$etape" ] || continue
    case ",$etapes," in *",$etape,"*) ;; *) etapes="${etapes:+$etapes,}$etape" ;; esac
    printf '  · %s : %s\n' "$etape" "$raison"
  done <<EOF
$DERIVE_LIGNES
EOF
  if [ "$XDIST_ABSENT" = 1 ]; then
    printf '  · venv : pytest-xdist absent — la suite a tourné en série (#214)\n'
    case ",$etapes," in *",venv,"*) ;; *) etapes="${etapes:+$etapes,}venv" ;; esac
  fi
  printf 'Rattrapage : bash scripts/setup.sh --only %s — rien n'\''est installé dans le dos ici.\n\n' "$etapes"
fi

if [ "$NB_ECHECS" -gt 0 ]; then
  printf '%sVerdict : ÉCHEC%s — %d job(s) rouge(s). La CI rendrait le même verdict : corriger avant de pousser.\n' \
    "$C_R" "$C_0" "$NB_ECHECS"
  exit 1
fi
if [ "$NB_IGNORES" -gt 0 ]; then
  printf '%sVerdict : VERT (partiel)%s — %d job(s) non joué(s), voir ci-dessus. Le pipeline, lui, les jouera.\n' \
    "$C_Y" "$C_0" "$NB_IGNORES"
  [ "$STRICT" = 1 ] && exit 1
  exit 0
fi
if [ "$NB_JOUES" -eq 0 ]; then
  # Ne jamais annoncer « vert » quand rien n'a tourné : un --only trop restrictif rendrait sinon
  # un feu vert qui ne repose sur aucun contrôle.
  printf '%sVerdict : AUCUN JOB JOUÉ%s — élargir --only/--skip (jobs : %s).\n' "$C_Y" "$C_0" "$JOBS_CONNUS"
  exit 0
fi
printf '%sVerdict : VERT%s — les contrôles de la CI passent en local.\n' "$C_G" "$C_0"
exit 0
