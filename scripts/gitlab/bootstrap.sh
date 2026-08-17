#!/usr/bin/env bash
# Prépare le dépôt du projet pour le workflow décrit dans docs/10-workflow-git.md.
# Idempotent : recrée le schéma de labels réel du projet (agent::/prio::/workflow::/type::)
# s'il manque, sans toucher à ce qui existe déjà. À exécuter après `glab auth login` (ou
# `gh auth login`), depuis la racine du dépôt (ou sur un nouveau projet qui doit reproduire ce
# schéma).
#
# LES DEUX FORGES (#341, chantier #335) — `MAESTRO_FORGE=github bash scripts/gitlab/bootstrap.sh`
# provisionne le dépôt GitHub. Ce que le commutateur couvre, et ce qu'il ne couvre pas :
#
#   • LES LABELS, oui : ce sont les 19 mêmes noms des deux côtés, et c'est la seule partie dont
#     l'outillage dépend vraiment — sans `workflow::*`, `lib.sh set-workflow` refuse de poser quoi
#     que ce soit, donc /ticket-start ne démarre plus rien.
#   • LE BOARD KANBAN, non : c'est une notion GitLab, et le projet n'utilise pas Projects v2 (le
#     suivi maison du lot 4 a remplacé le seul usage qu'on en aurait eu).
#   • LES GARDE-FOUS DE MERGE, non : leur pendant GitHub est la protection de branche, qui a son
#     propre script (`scripts/github/protect-main.sh`, #338) parce qu'elle n'est PAS posée
#     aujourd'hui — la protection de branche n'existe pas sur un dépôt privé d'un compte Free
#     (docs/10 §8.8, décision du 2026-08-14). Reproduire ici un PUT qui échouerait à chaque fois
#     n'apprendrait rien à personne ; nommer le script qui explique pourquoi, si.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$here/lib.sh"

FORGE="$(gl_forge)" || exit 1
gl_require_glab || exit 1
echo "Forge « $FORGE » — dépôt $(gl_depot_courant)"
echo

existing_labels="$(gl_labels 2>/dev/null || true)"

create_label() {
  local name="$1" color="$2" description="$3"
  if printf '%s\n' "$existing_labels" | grep -qx "$name"; then
    echo "label déjà présent, on saute: $name"
    return
  fi
  if [ "$FORGE" = github ]; then
    # `gh label create` attend la couleur SANS dièse — le passer tel quel fait échouer l'appel avec
    # un « invalid color ». Les couleurs ci-dessous gardent leur `#` : c'est la forme qu'attend glab,
    # et une seule table de couleurs vaut mieux que deux qui divergeront.
    gh label create "$name" --repo "$GL_GH_REPO" --color "${color#\#}" --description "$description"
  else
    glab label create --name "$name" --color "$color" --description "$description"
  fi
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

# workflow:: — cycle de vie du ticket (À faire / En cours / En revue / Terminé / Abandonné /
# Doublon), voir docs/10-workflow-git.md §3.
#
# Oui, c'est un retour en arrière, et il est assumé : le cycle de vie a été porté un temps par le
# champ **Status natif** de GitLab (lifecycle custom « Maestro », #12/#13). Ce champ est une
# fonctionnalité Premium, perdue avec la fin de l'essai Ultimate du groupe le 2026-08-02 — et la
# donnée de statut avec lui (#207). Sur le plan Free, les labels sont le seul mécanisme disponible.
# Si quelqu'un relit ceci en se disant qu'il faudrait « revenir au statut natif », c'est une
# question d'abonnement, pas d'outillage.
#
# ⚠ L'exclusion mutuelle des labels scopés est elle aussi Premium : sur Free, le « :: » n'est que
# cosmétique et rien n'empêche un ticket de porter `workflow::a-faire` ET `workflow::en-revue`.
# L'invariant « exactement un workflow:: par ticket » est donc tenu par l'OUTILLAGE (toute pose
# retire les autres) et sa dérive surveillée par doctor.sh — jamais par GitLab.
#
# Noms sans accent (comme type::/agent::/prio::) : un label accentué devrait être ré-encodé dans
# chaque chemin `glab api` et à la création des listes de board. Couleurs reprises telles quelles
# des statuts du lifecycle, pour que le board reste lisible à l'identique.
create_label "workflow::a-faire"   "#737278" "Pas encore commencé"
create_label "workflow::en-cours"  "#1f75cb" "Quelqu'un travaille dessus"
create_label "workflow::en-revue"  "#e67e22" "MR ouverte, en attente de relecture et de merge"
create_label "workflow::termine"   "#108548" "Livré et mergé"
create_label "workflow::abandonne" "#DD2B0E" "Ne sera pas réalisé (won't do)"
create_label "workflow::doublon"   "#DD2B0E" "Déjà couvert par un autre ticket"

# prio:: — tri du backlog
create_label "prio::haute"   "#cc0033" "À traiter en priorité"
create_label "prio::moyenne" "#ec9d00" "Priorité normale"
create_label "prio::basse"   "#388e3c" "Peut attendre"

echo "Labels prêts."

# Colonnes du Kanban — script voisin, appelé ici pour que `bootstrap.sh` reste le point d'entrée
# unique du provisionnement. Il est à part parce qu'il parle à une autre API que les labels (boards
# REST) et qu'on veut pouvoir le rejouer seul après un remaniement du board.
echo
if [ "$FORGE" = github ]; then
  echo "Board Kanban : sans objet sur GitHub (pas de board GitLab ; Projects v2 non utilisé) — étape sautée."
else
  bash "$here/bootstrap-board.sh"
fi
echo

# Réglages projet recommandés (best-effort : selon le tier GitLab, certains champs
# peuvent être ignorés silencieusement par l'API) :
#   - remove_source_branch_after_merge / squash_option : hygiène de merge (squash + ménage).
#     Ce PUT étant best-effort, le défaut projet ne suffit pas comme garantie : /ticket-finish pose
#     aussi `--remove-source-branch` sur chaque MR, et doctor.sh surveille la dérive du réglage ;
#   - only_allow_merge_if_pipeline_succeeds=true + allow_merge_on_skipped_pipeline=false :
#     GitLab fait respecter lui-même « pipeline vert avant merge » (docs/10-workflow-git.md §6) —
#     une MR au pipeline rouge (ou sauté) n'est pas mergeable. Dérive surveillée par doctor.sh.
#   - shared_runners_enabled=false : les pipelines tournent sur le **runner de projet local**
#     par défaut (docs/10-workflow-git.md §8). Les runners partagés GitLab, au quota de minutes
#     durablement épuisé, sont coupés — sinon un job non-taggé pouvait y atterrir et retomber en
#     `ci_quota_exceeded`. Contrepartie : le runner local doit être en ligne, sinon les jobs
#     restent `pending` (et le merge, qui exige un pipeline vert, est bloqué).
# PUT idempotent : ré-exécution sans effet ni erreur.
#
# Côté GITHUB ces cinq champs n'ont pas de pendant qu'on puisse poser ici : la protection de branche
# a son script (et sa décision, docs/10 §8.8), et `delete_branch_on_merge` demande un droit
# d'administration du dépôt que le jeton du projet n'a pas (#336). On NOMME donc le reste à faire au
# lieu de l'appliquer à moitié — un provisionnement qui échoue en silence est ce qui a fait croire
# pendant des mois que le board était configuré (#207).
if [ "$FORGE" = github ]; then
  cat <<'GITHUB'
Réglages du dépôt (GitHub) — hors de portée de ce script, et c'est délibéré :
  · protection de branche sur main  → bash scripts/github/protect-main.sh   (écrit, non joué :
    la protection n'existe pas sur un dépôt privé d'un compte Free — docs/10 §8.8)
  · delete_branch_on_merge          → réglage du dépôt, demande un jeton à droit d'administration
Le diagnostic de ces deux points est dans : MAESTRO_FORGE=github bash scripts/gitlab/doctor.sh
GITHUB
else
  project_path="$(glab repo view --output json 2>/dev/null | grep -o '"path_with_namespace":"[^"]*"' | cut -d'"' -f4 || true)"
  if [ -n "$project_path" ]; then
    glab api "projects/$(printf '%s' "$project_path" | sed 's/\//%2F/g')" \
      -X PUT \
      -F remove_source_branch_after_merge=true \
      -F squash_option=default_on \
      -F only_allow_merge_if_pipeline_succeeds=true \
      -F allow_merge_on_skipped_pipeline=false \
      -F shared_runners_enabled=false \
      >/dev/null 2>&1 || echo "Réglages projet non appliqués (droits insuffisants ou champ non supporté) — à faire manuellement dans Settings > Merge requests / CI-CD > Runners."
  fi
fi

echo "Bootstrap terminé."
