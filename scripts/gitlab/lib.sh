#!/usr/bin/env bash
# Helpers glab partagés pour le workflow de tickets Maestro (voir docs/10-workflow-git.md).
#
# Deux usages :
#   1. Sourcé depuis un autre script :   . scripts/gitlab/lib.sh ; gl_set_status 16 "En cours"
#   2. Exécuté en sous-commandes :        bash scripts/gitlab/lib.sh set-status 16 "En cours"
#
# Les commandes /ticket-* s'appuient dessus pour ne PAS coder les GID de statut en dur :
# gl_status_gid re-dérive l'ID depuis le lifecycle « Maestro » par NOM à chaque appel, donc
# le workflow survit à une recréation du lifecycle (voir docs/10-workflow-git.md §3).
#
# NB : pas de `set -e` global — ce fichier est conçu pour être sourcé sans imposer son mode
# d'erreur au script appelant. Chaque fonction renvoie un code non nul en cas d'échec.

# --- Configuration (surchageable par variables d'environnement) --------------------------------
GL_PROJECT="${GL_PROJECT:-maestro-group4345327/maestro}"
GL_GROUP="${GL_GROUP:-${GL_PROJECT%%/*}}"   # groupe = tout ce qui précède le premier "/"
GL_LIFECYCLE="${GL_LIFECYCLE:-Maestro}"      # nom du lifecycle custom portant le cycle de vie

# --- Pré-requis ---------------------------------------------------------------------------------
# Vérifie que glab est installé ET authentifié. À appeler en tête des commandes.
gl_require_glab() {
  if ! command -v glab >/dev/null 2>&1; then
    echo "glab n'est pas installé. Voir https://gitlab.com/gitlab-org/cli" >&2
    return 1
  fi
  if ! glab auth status >/dev/null 2>&1; then
    echo "glab non authentifié. Lancer d'abord : glab auth login" >&2
    return 1
  fi
}

# --- Résolution d'identifiants ------------------------------------------------------------------
# gl_workitem_gid <iid> -> imprime le GID global du work item (gid://gitlab/WorkItem/<n>).
gl_workitem_gid() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "gl_workitem_gid : iid manquant" >&2; return 2; fi
  local gid
  gid="$(glab api graphql -f query='{ project(fullPath:"'"$GL_PROJECT"'") { workItems(iids:["'"$iid"'"]) { nodes { id } } } }' 2>/dev/null \
        | grep -o 'gid://gitlab/WorkItem/[0-9]\+' | head -1)"
  if [ -z "$gid" ]; then echo "Work item #$iid introuvable dans $GL_PROJECT" >&2; return 1; fi
  printf '%s\n' "$gid"
}

# gl_status_gid <nom-de-statut> -> imprime le GID du statut, dérivé par NOM depuis le
# lifecycle "$GL_LIFECYCLE" (scopé : on isole d'abord le bloc du lifecycle Maestro pour ne pas
# confondre avec un statut homonyme d'un autre lifecycle).
gl_status_gid() {
  local name="$1"
  if [ -z "$name" ]; then echo "gl_status_gid : nom de statut manquant" >&2; return 2; fi
  local raw block pair gid
  raw="$(glab api graphql -f query='{ group(fullPath:"'"$GL_GROUP"'") { lifecycles { nodes { name statuses { id name } } } } }' 2>/dev/null)"
  if [ -z "$raw" ]; then echo "Impossible de lire les lifecycles du groupe $GL_GROUP" >&2; return 1; fi
  # Isole le bloc statuses du lifecycle Maestro : après '"name":"Maestro","statuses":[' jusqu'au ']'.
  block="${raw#*\"name\":\"$GL_LIFECYCLE\",\"statuses\":[}"
  block="${block%%]*}"
  # Cherche la paire id/name correspondante (ordre garanti par la requête : id puis name).
  pair="$(printf '%s' "$block" \
         | grep -o '"id":"gid://gitlab/WorkItems::Statuses::Custom::Status/[0-9]\+","name":"'"$name"'"' \
         | head -1)"
  gid="$(printf '%s' "$pair" | grep -o 'gid://gitlab/WorkItems::Statuses::Custom::Status/[0-9]\+' | head -1)"
  if [ -z "$gid" ]; then
    echo "Statut « $name » introuvable dans le lifecycle « $GL_LIFECYCLE »." >&2
    echo "Statuts disponibles : À faire, En cours, En revue, Terminé, Abandonné, Doublon." >&2
    return 1
  fi
  printf '%s\n' "$gid"
}

# --- Actions ------------------------------------------------------------------------------------
# gl_set_status <iid> <nom-de-statut> -> pose le statut natif du ticket. Idempotent côté GitLab.
gl_set_status() {
  local iid="$1" name="$2"
  if [ -z "$iid" ] || [ -z "$name" ]; then echo "usage: gl_set_status <iid> <nom-de-statut>" >&2; return 2; fi
  local wiid sid out
  wiid="$(gl_workitem_gid "$iid")" || return 1
  sid="$(gl_status_gid "$name")"   || return 1
  out="$(glab api graphql -f query='mutation { workItemUpdate(input:{ id:"'"$wiid"'", statusWidget:{ status:"'"$sid"'" } }){ errors } }' 2>&1)"
  case "$out" in
    *'"errors":[]'*) printf 'Statut de #%s → « %s »\n' "$iid" "$name" ;;
    *) echo "Échec de la pose du statut sur #$iid : $out" >&2; return 1 ;;
  esac
}

# --- Utilitaires de nommage ---------------------------------------------------------------------
# gl_slug <titre> -> slug de branche : minuscules, accents retirés, non-alphanum -> '-',
# tirets collapsés, tronqué à 40 caractères, sans tiret de bord.
gl_slug() {
  local s="$1"
  if command -v iconv >/dev/null 2>&1; then
    # glibc TRANSLIT rend « é » -> « 'e », « è » -> « `a »… : on retire ces marques d'accent
    # (', `, ^, ~, ") pour que l'accent disparaisse au lieu de couper le mot en deux.
    s="$(printf '%s' "$s" | iconv -f UTF-8 -t ASCII//TRANSLIT 2>/dev/null | sed "s/[\`'^~\"]//g")"
  fi
  printf '%s' "$s" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -e "s/[^a-z0-9]\+/-/g" -e "s/^-\+//" -e "s/-\+$//" \
    | cut -c1-40 \
    | sed -e "s/-\+$//"
}

# gl_branch_prefix <type> -> préfixe de branche depuis un type (accepte "feature" ou
# "type::feature"). feature->feat, bug->fix, infra->chore, doc->docs.
gl_branch_prefix() {
  case "${1#type::}" in
    feature) echo feat ;;
    bug)     echo fix ;;
    infra)   echo chore ;;
    doc)     echo docs ;;
    *) echo "Type inconnu : « $1 » (attendu : feature|bug|infra|doc)" >&2; return 1 ;;
  esac
}

# --- Dispatcher (uniquement quand exécuté directement, pas quand sourcé) -------------------------
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  cmd="${1:-}"; [ "$#" -gt 0 ] && shift
  case "$cmd" in
    require)        gl_require_glab ;;
    workitem-gid)   gl_workitem_gid "$@" ;;
    status-gid)     gl_status_gid "$@" ;;
    set-status)     gl_set_status "$@" ;;
    slug)           gl_slug "$@" ;;
    branch-prefix)  gl_branch_prefix "$@" ;;
    *)
      echo "usage: bash scripts/gitlab/lib.sh <sous-commande> [args]" >&2
      echo "  require | workitem-gid <iid> | status-gid <nom> | set-status <iid> <nom> | slug <titre> | branch-prefix <type>" >&2
      exit 2 ;;
  esac
fi
