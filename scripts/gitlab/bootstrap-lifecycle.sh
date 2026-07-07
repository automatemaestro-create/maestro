#!/usr/bin/env bash
# bootstrap-lifecycle.sh — (re)crée le lifecycle natif « Maestro » (champ Status) de façon
# reproductible, et l'attache aux types de work item Issue et Task. Complète bootstrap.sh, qui
# ne gère que les labels. Voir docs/10-workflow-git.md §3.
#
# Deux garde-fous, car ça touche une config live via une API work-items avancée :
#   • IDEMPOTENT : si le lifecycle « Maestro » existe déjà, on ne fait RIEN (rapport seul).
#   • DRY-RUN par défaut : sans --apply, on imprime les mutations qui seraient exécutées, sans
#     écrire. --apply n'a de sens que sur un projet VIERGE (lifecycle absent).
#
# Usage :
#   bash scripts/gitlab/bootstrap-lifecycle.sh            # vérifie + (si absent) dry-run
#   bash scripts/gitlab/bootstrap-lifecycle.sh --apply    # crée réellement (projet vierge only)
#
# Les mutations sont schéma-correctes (vérifiées par introspection GraphQL). Le chemin de création
# réel n'est pas testable sur le projet courant (lifecycle déjà présent).
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$here/lib.sh"

apply=0
[ "${1:-}" = "--apply" ] && apply=1

gl_require_glab || exit 1

# Définition des 6 statuts (nom | catégorie enum | couleur), fidèle au lifecycle en place.
# L'ordre fixe les index par défaut ci-dessous.
statuses_gql='[
    { name: "À faire",   category: TO_DO,       color: "#737278" },
    { name: "En cours",  category: IN_PROGRESS, color: "#1f75cb" },
    { name: "En revue",  category: IN_PROGRESS, color: "#e67e22" },
    { name: "Terminé",   category: DONE,        color: "#108548" },
    { name: "Abandonné", category: CANCELED,    color: "#DD2B0E" },
    { name: "Doublon",   category: CANCELED,    color: "#DD2B0E" }
  ]'
# Index (0-based) des statuts par défaut : ouverture=À faire, fermeture=Terminé, doublon=Doublon.
default_open=0
default_closed=3
default_duplicate=5

# --- 1. Le lifecycle existe-t-il déjà ? (idempotence) -------------------------------------------
existing="$(gl_graphql_read '{ group(fullPath:"'"$GL_GROUP"'") { lifecycles { nodes { name statuses { name category } } } } }')"
if printf '%s' "$existing" | grep -q '"name":"'"$GL_LIFECYCLE"'"'; then
  echo "✓ Lifecycle « $GL_LIFECYCLE » déjà en place dans le groupe $GL_GROUP — rien à faire (idempotent)."
  # Isole le bloc du lifecycle Maestro pour lister ses statuts.
  block="${existing#*\"name\":\"$GL_LIFECYCLE\",\"statuses\":[}"
  block="${block%%]*}"
  printf '%s' "$block" | grep -oE '"name":"[^"]+","category":"[^"]*"' \
    | sed -E 's/"name":"([^"]+)","category":"([^"]*)"/  - \1 (\2)/'
  exit 0
fi

# --- 2. Absent : construire la mutation de création --------------------------------------------
create_gql='mutation {
  lifecycleCreate(input: {
    namespacePath: "'"$GL_GROUP"'"
    name: "'"$GL_LIFECYCLE"'"
    statuses: '"$statuses_gql"'
    defaultOpenStatusIndex: '"$default_open"'
    defaultClosedStatusIndex: '"$default_closed"'
    defaultDuplicateStatusIndex: '"$default_duplicate"'
  }) { errors lifecycle { id name } }
}'

if [ "$apply" = 0 ]; then
  echo "Lifecycle « $GL_LIFECYCLE » absent du groupe $GL_GROUP."
  echo "DRY-RUN — mutations qui seraient exécutées (relancer avec --apply pour créer) :"
  echo
  echo "# 1) Création du lifecycle + ses 6 statuts"
  printf '%s\n' "$create_gql"
  echo
  echo "# 2) Attache aux types Issue et Task (lifecycleId issu de l'étape 1) :"
  echo "mutation { lifecycleAttachWorkItemType(input: {"
  echo "  namespacePath: \"$GL_GROUP\", lifecycleId: \"<gid-lifecycle>\","
  echo "  workItemTypeId: \"<gid-type-Issue|Task>\" }) { errors } }"
  exit 0
fi

# --- 3. --apply : créer réellement (projet vierge) ---------------------------------------------
echo "Création du lifecycle « $GL_LIFECYCLE » dans $GL_GROUP…"
out="$(glab api graphql -f query="$create_gql" 2>&1)"
case "$out" in
  *'"errors":[]'*) : ;;
  *) echo "Échec de lifecycleCreate : $out" >&2; exit 1 ;;
esac
lifecycle_id="$(printf '%s' "$out" | grep -oE 'gid://gitlab/WorkItems::Statuses::Custom::Lifecycle/[0-9]+' | head -1)"
if [ -z "$lifecycle_id" ]; then
  echo "lifecycleCreate n'a pas renvoyé d'ID de lifecycle : $out" >&2; exit 1
fi
echo "  lifecycle créé : $lifecycle_id"

# Résout les GID des types Issue et Task (globaux mais résolus par nom pour la portabilité).
types_json="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { workItemTypes { nodes { id name } } } }')"
for wit in Issue Task; do
  type_gid="$(printf '%s' "$types_json" | grep -oE '"id":"gid://gitlab/WorkItems::Type/[0-9]+","name":"'"$wit"'"' | grep -oE 'gid://gitlab/WorkItems::Type/[0-9]+' | head -1)"
  if [ -z "$type_gid" ]; then echo "Type « $wit » introuvable — attache ignorée." >&2; continue; fi
  att="$(glab api graphql -f query='mutation { lifecycleAttachWorkItemType(input: { namespacePath:"'"$GL_GROUP"'", lifecycleId:"'"$lifecycle_id"'", workItemTypeId:"'"$type_gid"'" }) { errors } }' 2>&1)"
  case "$att" in
    *'"errors":[]'*) echo "  attaché au type $wit ($type_gid)" ;;
    *) echo "  échec de l'attache au type $wit : $att" >&2 ;;
  esac
done

echo "✓ Lifecycle « $GL_LIFECYCLE » créé et attaché. Vérifie avec : bash scripts/gitlab/doctor.sh"
