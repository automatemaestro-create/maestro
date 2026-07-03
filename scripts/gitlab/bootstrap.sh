#!/usr/bin/env bash
# Prépare le projet GitLab pour le workflow décrit dans docs/10-workflow-git.md.
# À exécuter une fois, après `glab auth login`, depuis la racine du dépôt.
set -euo pipefail

if ! command -v glab >/dev/null 2>&1; then
  echo "glab n'est pas installé. Voir https://gitlab.com/gitlab-org/cli" >&2
  exit 1
fi

if ! glab auth status >/dev/null 2>&1; then
  echo "Non authentifié. Lancer d'abord: glab auth login" >&2
  exit 1
fi

existing_labels="$(glab label list --output json 2>/dev/null | grep -o '"name":"[^"]*"' | cut -d'"' -f4 || true)"

create_label() {
  local name="$1" color="$2" description="$3"
  if printf '%s\n' "$existing_labels" | grep -qx "$name"; then
    echo "label déjà présent, on saute: $name"
    return
  fi
  glab label create --name "$name" --color "$color" --description "$description"
}

# type:: — nature du ticket, détermine le préfixe de branche
create_label "type::feature" "#428BCA" "Nouvelle fonctionnalité"
create_label "type::bug"     "#D9534F" "Comportement incorrect à corriger"
create_label "type::chore"   "#8E8E8E" "Tâche technique (config, dépendances, nettoyage)"
create_label "type::docs"    "#5BC0DE" "Documentation"

# status:: — cycle de vie du ticket (posé/mis à jour par les commandes /ticket-*)
create_label "status::todo"        "#EDEDED" "Pas encore démarré"
create_label "status::in-progress" "#F0AD4E" "En cours de développement"
create_label "status::review"      "#FF8C00" "MR ouverte, en attente de revue"

# priority:: — tri du backlog
create_label "priority::high"   "#B60205" "À traiter en priorité"
create_label "priority::medium" "#FBCA04" "Priorité normale"
create_label "priority::low"    "#0E8A16" "Peut attendre"

echo "Labels prêts."

# Réglages projet recommandés (best-effort : selon le tier GitLab, certains champs
# peuvent être ignorés silencieusement par l'API).
project_path="$(glab repo view --output json 2>/dev/null | grep -o '"path_with_namespace":"[^"]*"' | cut -d'"' -f4 || true)"
if [ -n "$project_path" ]; then
  glab api "projects/$(printf '%s' "$project_path" | sed 's/\//%2F/g')" \
    -X PUT \
    -F remove_source_branch_after_merge=true \
    -F squash_option=default_on \
    >/dev/null 2>&1 || echo "Réglages projet non appliqués (droits insuffisants ou champ non supporté) — à faire manuellement dans Settings > Merge requests."
fi

echo "Bootstrap terminé."
