#!/usr/bin/env bash
# Prépare le dépôt du projet pour le workflow décrit dans docs/10-workflow-git.md.
# Idempotent : recrée le schéma de labels réel du projet (type::/agent::/prio::) s'il manque, sans
# toucher à ce qui existe déjà. À exécuter après `gh auth login`, depuis la racine du dépôt (ou sur
# un nouveau dépôt qui doit reproduire ce schéma).
#
# CE QUE CE SCRIPT COUVRE — LES LABELS DE CATÉGORISATION, ET RIEN D'AUTRE.
#
# Ce qu'il ne couvre pas, et pourquoi ce n'est pas un oubli :
#
#   • LE CYCLE DE VIE — il ne vit plus dans un label depuis #365 mais dans le champ Status d'un
#     projet Projects v2, monté par `scripts/github/bootstrap-project.sh` (voir plus bas).
#   • LE BOARD KANBAN — notion GitLab, partie avec l'outillage GitLab (#344). Son pendant est le
#     projet Projects v2, dont les colonnes SONT les options du champ Status.
#   • LES GARDE-FOUS DE MERGE — leur pendant GitHub est la protection de branche, qui a son propre
#     script (`scripts/github/protect-main.sh`, #338) parce qu'elle n'est PAS posée aujourd'hui : la
#     protection de branche n'existe pas sur un dépôt privé d'un compte Free (docs/10 §8.8, décision
#     du 2026-08-14). Reproduire ici un PUT qui échouerait à chaque fois n'apprendrait rien à
#     personne ; nommer le script qui explique pourquoi, si.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$here/lib.sh"

gl_require || exit 1
echo "Dépôt $(gl_depot_courant)"
echo

existing_labels="$(gl_labels 2>/dev/null || true)"

create_label() {
  local name="$1" color="$2" description="$3"
  if printf '%s\n' "$existing_labels" | grep -qx "$name"; then
    echo "label déjà présent, on saute: $name"
    return
  fi
  # `gh label create` attend la couleur SANS dièse — le passer tel quel fait échouer l'appel avec
  # un « invalid color ». Les couleurs ci-dessous gardent leur `#`, la forme dans laquelle elles ont
  # été relevées du board d'origine : une seule table de couleurs, et le dièse retiré au dernier
  # moment.
  gh label create "$name" --repo "$GL_GH_REPO" --color "${color#\#}" --description "$description"
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

# ⚠ PAS DE LABELS DE CYCLE DE VIE ICI, ET CE N'EST PAS UN OUBLI (#365, chantier #358). L'état d'un
# ticket (À faire / En cours / En revue / Terminé / Abandonné / Doublon) a été porté de #207 à #364
# par six labels scopés `workflow::*` — faute de mieux, GitLab Free ayant perdu le champ Status
# natif à la fin de l'essai Ultimate. Il vit désormais dans le champ **Status** d'un projet GitHub
# Projects v2, que monte `scripts/github/bootstrap-project.sh` : un champ à valeur unique, là où
# l'exclusion mutuelle des six labels était à notre charge. Les recréer ici rendrait à un dépôt neuf
# un second support que plus rien ne lit — et le premier symptôme de deux supports est un ticket qui
# porte deux états.

# prio:: — tri du backlog
create_label "prio::haute"   "#cc0033" "À traiter en priorité"
create_label "prio::moyenne" "#ec9d00" "Priorité normale"
create_label "prio::basse"   "#388e3c" "Peut attendre"

# lot:: — ce qu'on sait du DÉCOUPAGE d'un parent de suivi, et non du ticket lui-même (#562).
# `lot::arbitre` dit que la question « quels lots sont parallélisables ? » a été POSÉE sur ce parent,
# quelle qu'ait été la réponse. Sans lui, un parent dont la réponse juste est « aucun » serait
# indiscernable d'un parent que personne n'a arbitré, et proposé à chaque run (docs/10 §5.1).
create_label "lot::arbitre"  "#0e8a16" "Parent de suivi : lots parallélisables arbitrés"

echo "Labels prêts."

echo
cat <<'GITHUB'
Reste à monter, hors de portée de ce script et c'est délibéré :
  · cycle de vie (champ Status)     → bash scripts/github/bootstrap-project.sh
    Sans lui, aucun ticket ne peut porter d'état : /ticket-start ne démarre plus rien.
  · protection de branche sur main  → bash scripts/github/protect-main.sh   (écrit, non joué :
    la protection n'existe pas sur un dépôt privé d'un compte Free — docs/10 §8.8)
  · delete_branch_on_merge          → réglage du dépôt, qu'aucun script ne pose. GitHub n'a pas
    d'équivalent par PR du --remove-source-branch de GitLab : rien dans le cycle d'un ticket ne le
    remplace, et sans lui AUCUNE branche distante n'est supprimée au merge (22 accumulées sur ce
    dépôt-ci avant le 2026-08-19, #384). C'est un appel, et un seul :
      gh api -X PATCH repos/<owner>/<dépôt> -F delete_branch_on_merge=true
Le diagnostic de ces trois points est dans : bash scripts/gitlab/doctor.sh
(qui, pour le dernier, imprime la commande déjà substituée sur le dépôt courant)
GITHUB
echo

echo "Bootstrap terminé."
