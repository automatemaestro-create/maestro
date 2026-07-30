#!/usr/bin/env bash
# Filet CI local — rejoue les jobs de .gitlab-ci.yml sur son propre poste, AVANT le push
# (ticket #157, parent #155).
#
# Pourquoi (docs/10-workflow-git.md §8) : les runners partagés sont coupés, la CI tourne sur le
# runner de projet d'UNE machine, et le merge exige un pipeline vert. Découvrir un échec de lint
# par le pipeline, à plusieurs, c'est donc le découvrir sur la machine de quelqu'un d'autre — et
# occuper son runner pour une faute de frappe. Ce script rejoue les mêmes contrôles en local :
#
#   bash scripts/ci/local.sh                    # tous les jobs applicables
#   bash scripts/ci/local.sh --only pytest,mypy # un sous-ensemble (noms de jobs ou d'étages)
#   bash scripts/ci/local.sh --skip web-build
#   bash scripts/ci/local.sh --strict           # code non nul aussi si un job n'a pas pu être joué
#   bash scripts/ci/local.sh --list             # les jobs et leur commande
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
#     GitLab. Le code de sortie est non nul dès le premier échec dur.
#   - UN JOB NON JOUABLE N'EST PAS UN ÉCHEC : outil absent ⇒ « IGNORÉ », verdict annoncé PARTIEL
#     (et bloquant avec --strict). Mieux vaut un verdict honnêtement incomplet qu'un faux vert.
#
# Les paramètres qui pourraient dériver de la CI (seuil de couverture, sévérité shellcheck, image
# du linter) sont LUS dans .gitlab-ci.yml plutôt que recopiés ici : le filet suit le pipeline.

set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CI_YML="$RACINE/.gitlab-ci.yml"
LOG_DIR="${TMPDIR:-/tmp}/maestro-ci-local"

case "$(uname -s 2>/dev/null)" in
  MINGW* | MSYS* | CYGWIN*) WINDOWS=1 ;;
  *) WINDOWS=0 ;;
esac

if [ -t 1 ]; then
  C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_B=$'\033[1m'; C_0=$'\033[0m'
else
  C_G=''; C_Y=''; C_R=''; C_B=''; C_0=''
fi

# --- Réglages repris de .gitlab-ci.yml ------------------------------------------------------------
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
SHELLCHECK_IMAGE="${MAESTRO_SHELLCHECK_IMAGE:-$(lit_ci 'koalaman/shellcheck-alpine:[A-Za-z0-9._-]+' 'koalaman/shellcheck-alpine:stable')}"
COUVERTURE_MIN="$(lit_ci 'pytest --cov=maestro --cov-fail-under=[0-9]+' 'pytest --cov=maestro --cov-fail-under=90')"
COUVERTURE_MIN="${COUVERTURE_MIN##*=}"

# --- Jobs, par étage (mêmes noms que .gitlab-ci.yml) ----------------------------------------------
ETAGE_LINT="shellcheck python-lint"
ETAGE_TEST="pytest mypy web-build"
JOBS_CONNUS="$ETAGE_LINT $ETAGE_TEST"

JOBS_ONLY=""
JOBS_SKIP=""
STRICT=0

usage() {
  cat <<USAGE
Filet CI local — rejoue les jobs de .gitlab-ci.yml avant de pousser.

  bash scripts/ci/local.sh [options]

Options :
  --only <jobs>   N'exécute que ces jobs (séparés par des virgules). Les noms d'étages
                  « lint » et « test » sont acceptés et développés en leurs jobs.
  --skip <jobs>   Saute ces jobs (mêmes noms).
  --strict        Code de sortie non nul aussi si un job n'a pas pu être joué (outil absent).
  --list          Affiche les jobs, leur étage et leur commande, puis sort.
  -h, --help      Cette aide.

Jobs : ${JOBS_CONNUS// /, }
  lint : ${ETAGE_LINT// /, }
  test : ${ETAGE_TEST// /, }

web-build ne tourne que si apps/web (ou .gitlab-ci.yml) change par rapport à origin/main —
même règle que le pipeline. « --only web-build » le force.
USAGE
}

liste_jobs() {
  printf 'Jobs rejoués par le filet local (source : %s)\n\n' ".gitlab-ci.yml"
  printf '  %-12s %-6s %s\n' JOB ÉTAGE COMMANDE
  printf '  %-12s %-6s %s\n' shellcheck   lint "shellcheck --severity=$SHELLCHECK_SEVERITE \$(find scripts -name '*.sh')"
  printf '  %-12s %-6s %s\n' python-lint  lint "ruff check ."
  printf '  %-12s %-6s %s\n' pytest       test "python -m pytest --cov=maestro --cov-fail-under=$COUVERTURE_MIN"
  printf '  %-12s %-6s %s\n' mypy         test "mypy maestro"
  printf '  %-12s %-6s %s\n' web-build    test "npm run lint && npm test && npm run build (dans apps/web)"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --only) JOBS_ONLY="${2:-}"; shift ;;
    --skip) JOBS_SKIP="${2:-}"; shift ;;
    --strict) STRICT=1 ;;
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
image_docker_disponible() { docker image inspect "$1" >/dev/null 2>&1; }

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
  # CRLF déclenche des SC1017 qui n'existent pas côté GitLab (docs/10 §8) — le filet mentirait dans
  # les deux sens. On analyse donc ce que la CI verra, pas ce que le disque contient.
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
  # $fichiers est volontairement non quoté — une liste d'arguments, comme dans .gitlab-ci.yml.
  if command -v shellcheck >/dev/null 2>&1; then
    ( cd "$tmp" && shellcheck "--severity=$SHELLCHECK_SEVERITE" $fichiers ) >"$JOURNAL" 2>&1
    code=$?
  elif image_docker_disponible "$SHELLCHECK_IMAGE"; then
    # Repli sans réseau : l'image de la CI, mais seulement si elle est DÉJÀ sur la machine — le
    # filet ne télécharge rien. MSYS_NO_PATHCONV empêche Git Bash de réécrire « /mnt » en chemin
    # Windows avant que docker ne le voie.
    MSYS_NO_PATHCONV=1 docker run --rm -v "$(chemin_natif "$tmp"):/mnt" -w /mnt \
      "$SHELLCHECK_IMAGE" shellcheck "--severity=$SHELLCHECK_SEVERITE" $fichiers >"$JOURNAL" 2>&1
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

job_pytest() {
  local exe couverture tests sonde
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
  # `python -m` et non le script console `pytest` : lui seul ajoute le répertoire courant à
  # `sys.path` (le script console, lui, y met le dossier du script — #194). Sans ça, dans un
  # worktree, les tests s'exécutent contre le `maestro/` du clone principal pendant que
  # `--cov=maestro` instrumente celui d'ici — d'où une couverture à 0 %, et surtout un verdict qui
  # ne porte pas sur la branche qu'on s'apprête à pousser.
  execute "$exe" -m pytest --cov=maestro "--cov-fail-under=$COUVERTURE_MIN"
  local code=$?
  couverture="$(grep -E '^TOTAL' "$JOURNAL" 2>/dev/null | tail -1 | awk '{print $NF}')"
  tests="$(grep -oE '[0-9]+ (passed|failed|error)[^=]*' "$JOURNAL" 2>/dev/null | tail -1 | sed 's/[[:space:]]*$//')"
  DETAIL="${tests:-$(derniere_ligne)}${couverture:+ — couverture $couverture (seuil $COUVERTURE_MIN %)}"
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

# Le pipeline ne joue ce job que si apps/web (ou .gitlab-ci.yml) change : même règle ici, évaluée
# sur ce que la branche apporte à origin/main, travail non commité compris — c'est ce qui partira
# au push. « --only web-build » passe outre (on l'a demandé explicitement).
web_concerne() {
  local base modifs
  case " $JOBS_ONLY " in *" web-build "*) return 0 ;; esac
  base="$(git -C "$RACINE" merge-base origin/main HEAD 2>/dev/null)"
  modifs="$(
    [ -n "$base" ] && git -C "$RACINE" diff --name-only "$base" -- 2>/dev/null
    git -C "$RACINE" status --porcelain --untracked-files=all 2>/dev/null | cut -c4-
  )"
  printf '%s\n' "$modifs" | grep -qE '^"?(apps/web/|\.gitlab-ci\.yml$)'
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
  # node_modules depuis le même lockfile. On enchaîne lint, tests puis build, comme le job.
  PATH="$bindir:$PATH" execute npm --prefix apps/web run lint || {
    DETAIL="eslint : erreur(s) — voir le journal"
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
  DETAIL="eslint + vitest (${tests:-?}) + next build verts"
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
        printf '%s\n' "    ─── journal : $JOURNAL"
        sed -n '1,40p' "$JOURNAL" 2>/dev/null | sed 's/^/    /'
        [ "$(wc -l <"$JOURNAL" 2>/dev/null || echo 0)" -gt 40 ] && printf '    … (suite dans %s)\n' "$JOURNAL"
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

mkdir -p "$LOG_DIR"
branche="$(git -C "$RACINE" rev-parse --abbrev-ref HEAD 2>/dev/null)"
printf '\n%sFilet CI local%s — %s\n' "$C_B" "$C_0" "$RACINE"
printf 'branche : %s · les mêmes contrôles que .gitlab-ci.yml, avec le venv et le Node du dépôt\n\n' "${branche:-?}"

joue_etage lint "$ETAGE_LINT"

if [ "$NB_ECHECS" -gt 0 ]; then
  # Comme GitLab : l'étage test ne démarre pas si le lint est rouge. Pour le forcer malgré tout,
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
