#!/usr/bin/env bash
# bootstrap-board.sh — (re)configure les colonnes du Kanban GitLab sur les labels `workflow::*`.
# Appelé par bootstrap.sh (qui crée les labels juste avant), rejouable seul. Voir
# docs/10-workflow-git.md §3 et le ticket #208.
#
# Ce qu'il fait, dans l'ordre du flux :
#     workflow::a-faire → workflow::en-cours → workflow::en-revue → workflow::termine
# `abandonne` et `doublon` n'ont pas de colonne : ces tickets sont fermés, ils sortent du board.
#
# Deux contraintes à connaître avant d'y toucher :
#   • Sur le plan Free, un projet n'a qu'UN SEUL board. On reconfigure celui qui existe, on n'en
#     crée jamais un second — d'où la découverte du board plutôt qu'un id en dur.
#   • Les colonnes « par statut » de l'époque du champ Status natif (#12) survivent en listes
#     ORPHELINES (`"label": null`) : elles n'affichent plus rien et ne se suppriment pas toutes
#     seules. Toute liste qui ne porte pas un label du flux est donc retirée.
#
# IDEMPOTENT : une deuxième exécution ne fait rien (mêmes colonnes, mêmes positions).
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$here/lib.sh"

usage() {
  cat <<'USAGE'
Colonnes du Kanban GitLab, alignées sur les labels workflow:: (docs/10-workflow-git.md §3).

  bash scripts/gitlab/bootstrap-board.sh            # applique (idempotent)
  bash scripts/gitlab/bootstrap-board.sh --check    # diagnostic, n'écrit rien
USAGE
}

check=0
case "${1:-}" in
  --check) check=1 ;;
  -h | --help) usage; exit 0 ;;
  "") ;;
  *) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
esac

gl_require_glab || exit 1

# Les colonnes, dans l'ordre du flux. La position d'une liste = son rang ici.
FLUX="workflow::a-faire workflow::en-cours workflow::en-revue workflow::termine"

enc="$(gl_project_enc)"

# Projette la réponse `lists` (ndjson : une liste par ligne) en TSV « id <TAB> label <TAB> position ».
# Le reste du script ne manipule plus de JSON. Une liste orpheline (`"label":null`) rend « - ».
projette_listes() {
  awk '
    /^\{/ {
      match($0, /^\{"id":[0-9]+/); id = substr($0, 7, RLENGTH - 6)
      pos = "-"
      if (match($0, /"position":[0-9]+/)) pos = substr($0, RSTART + 11, RLENGTH - 11)
      nom = "-"
      if (match($0, /"label":\{/)) {
        reste = substr($0, RSTART)
        if (match(reste, /"name":"[^"]*"/)) nom = substr(reste, RSTART + 8, RLENGTH - 9)
      }
      printf "%s\t%s\t%s\n", id, nom, pos
    }
  '
}

lit_listes() {
  glab api "projects/$enc/boards/$board_id/lists" --output ndjson 2>/dev/null | projette_listes
}

# --- Le board (unique sur Free) -----------------------------------------------------------------
# `--output ndjson` rend un board par ligne : l'objet embarque le projet entier, on ne veut que
# l'`id` de tête. Sans ndjson il faudrait découper un JSON imbriqué de plusieurs Ko.
board_id="$(glab api "projects/$enc/boards" --output ndjson 2>/dev/null | grep -o '^{"id":[0-9]\+' | head -1 | grep -o '[0-9]\+')"
if [ -z "$board_id" ]; then
  echo "Aucun board trouvé sur $GL_PROJECT — le créer une fois dans l'UI (Plan > Boards), puis relancer." >&2
  exit 1
fi
echo "Colonnes du Kanban — board #$board_id de $GL_PROJECT"

# --- Les labels du flux doivent exister ---------------------------------------------------------
# id ↔ nom, en une lecture. Un label absent est une erreur d'ordonnancement (bootstrap.sh les crée
# juste avant) : on le dit plutôt que de poser une colonne vide.
labels_json="$(glab api "projects/$enc/labels?per_page=100" --paginate --output ndjson 2>/dev/null)"
label_id() { # <nom> -> id numérique, vide si absent
  printf '%s\n' "$labels_json" \
    | grep -F "\"name\":\"$1\"" \
    | grep -o '^{"id":[0-9]\+' | head -1 | grep -o '[0-9]\+'
}

manquants=""
for nom in $FLUX; do
  [ -n "$(label_id "$nom")" ] || manquants="$manquants $nom"
done
if [ -n "$manquants" ]; then
  echo "Labels absents du projet :$manquants" >&2
  echo "Les créer d'abord : bash scripts/gitlab/bootstrap.sh" >&2
  exit 1
fi

# --- 1. Retirer les listes qui ne sont pas des colonnes du flux ---------------------------------
while IFS=$'\t' read -r lid nom _; do
  [ -n "$lid" ] || continue
  case " $FLUX " in *" $nom "*) continue ;; esac
  motif="colonne hors flux ($nom)"
  [ "$nom" = "-" ] && motif="liste orpheline, héritée des colonnes par statut"
  if [ "$check" = 1 ]; then
    echo "  [check] supprimerait la liste $lid — $motif"
  elif glab api --method DELETE "projects/$enc/boards/$board_id/lists/$lid" >/dev/null 2>&1; then
    echo "  ✓ liste $lid supprimée — $motif"
  else
    echo "  ✗ échec de la suppression de la liste $lid ($motif)" >&2
    exit 1
  fi
done < <(lit_listes)

# --- 2. Créer les colonnes manquantes, dans l'ordre du flux -------------------------------------
# Une liste créée est ajoutée EN FIN de board : créer dans l'ordre du flux suffit à obtenir le bon
# ordre sur un board vierge. L'étape 3 rattrape les cas où l'ordre a dérivé.
presentes="$(lit_listes | cut -f2)"
for nom in $FLUX; do
  if printf '%s\n' "$presentes" | grep -qx -- "$nom"; then
    echo "  = colonne déjà présente : $nom"
  elif [ "$check" = 1 ]; then
    echo "  [check] créerait la colonne $nom"
  else
    out="$(glab api --method POST "projects/$enc/boards/$board_id/lists" -F "label_id=$(label_id "$nom")" 2>&1)"
    case "$out" in
      *'"id":'*) echo "  ✓ colonne créée : $nom" ;;
      *) echo "  ✗ échec de la création de la colonne $nom : $out" >&2; exit 1 ;;
    esac
  fi
done

# En --check on s'arrête ici : les positions se calculeraient sur des listes qui n'existent pas.
if [ "$check" = 1 ]; then
  echo "(diagnostic seul — rien n'a été écrit)"
  exit 0
fi

# --- 3. Remettre les colonnes dans l'ordre du flux ----------------------------------------------
# Relecture après création : les ids des nouvelles listes ne sont pas dans la première lecture.
listes="$(lit_listes)"
rang=0
for nom in $FLUX; do
  lid=""; pos=""
  while IFS=$'\t' read -r l n p; do
    if [ "$n" = "$nom" ]; then lid="$l"; pos="$p"; break; fi
  done <<EOF
$listes
EOF
  if [ -z "$lid" ]; then
    echo "  ✗ colonne $nom introuvable après création" >&2
    exit 1
  fi
  if [ "$pos" = "$rang" ]; then
    echo "  = $nom en position $rang"
  elif glab api --method PUT "projects/$enc/boards/$board_id/lists/$lid" -F "position=$rang" >/dev/null 2>&1; then
    echo "  ✓ $nom déplacée en position $rang (était $pos)"
  else
    echo "  ✗ échec du repositionnement de $nom" >&2
    exit 1
  fi
  rang=$((rang + 1))
done

echo "Colonnes prêtes : ${FLUX// / → }"
