#!/usr/bin/env bash
# Prépare le projet GitLab pour le workflow décrit dans docs/10-workflow-git.md.
# Idempotent : recrée le schéma de labels réel du projet (agent::/prio::/workflow::/type::)
# s'il manque, sans toucher à ce qui existe déjà. À exécuter après `glab auth login`,
# depuis la racine du dépôt (ou sur un nouveau projet qui doit reproduire ce schéma).
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

# type:: — nature du ticket, détermine le préfixe de branche (docs/10-workflow-git.md §1)
create_label "type::feature" "#1f75cb" "Nouvelle fonctionnalité"
create_label "type::bug"     "#cc0033" "Comportement incorrect à corriger"
create_label "type::doc"     "#767676" "Documentation"
create_label "type::infra"   "#6699cc" "Infrastructure, configuration, environnement"

# agent:: — quel agent/rôle Maestro traite ce ticket (voir README.md)
create_label "agent::orchestrateur" "#6b4fbb" "Chef de projet / orchestrateur"
create_label "agent::dev"           "#1aaa55" "Développeur"
create_label "agent::bdd"           "#dd5f53" "Base de données"
create_label "agent::devops"        "#e67e22" "DevOps"
create_label "agent::design"        "#cd5b91" "Designer"
create_label "agent::qa"            "#009966" "QA / Testeur"

# workflow:: — cycle de vie du ticket (posé/mis à jour par les commandes /ticket-*)
create_label "workflow::à faire"  "#8e8e8e" "Pas encore démarré"
create_label "workflow::en cours" "#1f75cb" "En cours de développement"
create_label "workflow::en revue" "#e67e22" "MR ouverte, en attente de revue"
create_label "workflow::terminé"  "#1aaa55" "Ticket terminé et mergé"

# prio:: — tri du backlog
create_label "prio::haute"   "#cc0033" "À traiter en priorité"
create_label "prio::moyenne" "#ec9d00" "Priorité normale"
create_label "prio::basse"   "#388e3c" "Peut attendre"

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
