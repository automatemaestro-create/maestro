#!/usr/bin/env bash
# Bilan de santé (LECTURE SEULE) du setup GitLab Maestro + détection de dérive.
# N'écrit jamais rien (ni statut, ni label, ni MR) — voir docs/10-workflow-git.md.
# Réutilise scripts/gitlab/lib.sh (statut par nom, pas de GID en dur).
#
# Usage :  bash scripts/gitlab/doctor.sh [--strict]
#   --strict : code de sortie non nul aussi en présence d'avertissements (utile en CI).
# Code de sortie : 1 si un contrôle DUR échoue (auth, labels, lifecycle) ; sinon 0
#   (ou 1 avec --strict s'il reste des avertissements de dérive).
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$here/lib.sh"

strict=0
[ "${1:-}" = "--strict" ] && strict=1

if [ -t 1 ]; then
  C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_B=$'\033[1m'; C_0=$'\033[0m'
else
  C_G=''; C_Y=''; C_R=''; C_B=''; C_0=''
fi

errors=0
warns=0
ok()      { printf '  %s✓%s %s\n' "$C_G" "$C_0" "$1"; }
warn()    { printf '  %s⚠%s %s\n' "$C_Y" "$C_0" "$1"; warns=$((warns + 1)); }
err()     { printf '  %s✗%s %s\n' "$C_R" "$C_0" "$1"; errors=$((errors + 1)); }
section() { printf '\n%s%s%s\n' "$C_B" "$1" "$C_0"; }

# --- 1. Prérequis -------------------------------------------------------------------------------
section "1. Prérequis"
if gl_require_glab 2>/dev/null; then
  user="$(glab api user 2>/dev/null | grep -o '"username":"[^"]*"' | head -1 | cut -d'"' -f4)"
  ok "glab installé et authentifié (${user:-?})"
else
  err "glab absent ou non authentifié — lancer : glab auth login"
  section "Résumé"
  printf '  Bilan interrompu : authentification requise.\n'
  exit 1
fi

# --- 2. Labels de catégorisation ----------------------------------------------------------------
section "2. Labels de catégorisation (§3.2)"
existing_labels="$(glab label list --output json 2>/dev/null | grep -o '"name":"[^"]*"' | cut -d'"' -f4)"
expected_labels="type::feature type::bug type::doc type::infra \
agent::orchestrateur agent::dev agent::bdd agent::devops agent::design agent::qa \
prio::haute prio::moyenne prio::basse"
missing=""
for l in $expected_labels; do
  printf '%s\n' "$existing_labels" | grep -qx "$l" || missing="$missing $l"
done
if [ -z "$missing" ]; then
  ok "familles type::/agent::/prio:: complètes (13 labels)"
else
  err "labels manquants :$missing → relancer scripts/gitlab/bootstrap.sh"
fi

# --- 3. Lifecycle « Maestro » -------------------------------------------------------------------
# Une SEULE lecture GraphQL (avec retry, via gl_graphql_read) pour les 6 statuts : on lit le
# lifecycle une fois puis on vérifie chaque nom contre ce résultat. Évite le faux « incohérent »
# que 6 appels indépendants pouvaient déclencher dès qu'un seul retombait vide (réponse muette
# intermittente de l'API).
section "3. Lifecycle « $GL_LIFECYCLE » (statuts résolvables par nom)"
lifecycle_raw="$(gl_graphql_read '{ group(fullPath:"'"$GL_GROUP"'") { lifecycles { nodes { name statuses { id name } } } } }')"
if [ -z "$lifecycle_raw" ]; then
  err "lecture du lifecycle « $GL_LIFECYCLE » impossible (API GraphQL muette après retries) → réessayer"
elif ! printf '%s' "$lifecycle_raw" | grep -q '"name":"'"$GL_LIFECYCLE"'"'; then
  err "lifecycle « $GL_LIFECYCLE » introuvable dans le groupe $GL_GROUP → voir docs/10-workflow-git.md §3 (re-création)"
else
  # Isole le bloc statuses du lifecycle Maestro (même logique que gl_status_gid) pour ne pas
  # confondre avec un statut homonyme d'un autre lifecycle.
  lifecycle_block="${lifecycle_raw#*\"name\":\"$GL_LIFECYCLE\",\"statuses\":[}"
  lifecycle_block="${lifecycle_block%%]*}"
  missing_status=""
  for s in "À faire" "En cours" "En revue" "Terminé" "Abandonné" "Doublon"; do
    printf '%s' "$lifecycle_block" | grep -q '"name":"'"$s"'"' || missing_status="${missing_status:+$missing_status, }$s"
  done
  if [ -z "$missing_status" ]; then
    ok "6 statuts résolus par nom (1 appel) — set-status opérationnel (aucun GID en dur)"
  else
    err "statut(s) non résolu(s) dans le lifecycle « $GL_LIFECYCLE » : $missing_status → voir docs/10-workflow-git.md §3"
  fi
fi

# --- 4. Dérive statut ↔ réalité -----------------------------------------------------------------
section "4. Dérive statut ↔ réalité"

# helper local : iid des work items d'un état GitLab (opened/closed) portant un statut donné
iids_with_status() { # $1=opened|closed  $2=nom-statut
  gl_backlog "$1" | sed 's/{"iid":/\n{"iid":/g' \
    | grep -F '"status":{"name":"'"$2"'"}' \
    | grep -o '"iid":"[0-9]*"' | grep -o '[0-9]*'
}

# 4a. Tickets « En revue » ouverts : une MR ouverte est-elle rattachée ?
revue_iids="$(iids_with_status opened "En revue")"
open_mr_branches="$(glab mr list --output json 2>/dev/null | grep -o '"source_branch":"[^"]*"' | cut -d'"' -f4)"
if [ -z "$revue_iids" ]; then
  ok "aucun ticket « En revue » en attente"
else
  for iid in $revue_iids; do
    if printf '%s\n' "$open_mr_branches" | grep -q "/$iid-"; then
      ok "#$iid « En revue » ↔ MR ouverte"
    else
      warn "#$iid « En revue » sans MR ouverte rattachée (statut resté après merge/close ?)"
    fi
  done
fi

# 4b. Tickets fermés dont le statut est resté « actif »
stuck_iids="$(gl_backlog closed | sed 's/{"iid":/\n{"iid":/g' \
  | grep -E '"status":\{"name":"(À faire|En cours|En revue)"\}' \
  | grep -o '"iid":"[0-9]*"' | grep -o '[0-9]*')"
if [ -z "$stuck_iids" ]; then
  ok "aucun ticket fermé au statut encore actif"
else
  for iid in $stuck_iids; do
    warn "#$iid est fermé mais son statut est encore « actif » (attendu : Terminé/Abandonné/Doublon)"
  done
fi

# --- 5. Ménage des branches locales -------------------------------------------------------------
section "5. Ménage des branches locales"
if git rev-parse --git-dir >/dev/null 2>&1; then
  cleanup_found=0
  while IFS= read -r b; do
    [ -z "$b" ] && continue
    st="$(glab mr view "$b" --output json 2>/dev/null | grep -o '"state":"[^"]*"' | head -1 | cut -d'"' -f4)"
    if [ "$st" = merged ]; then
      warn "branche locale « $b » : MR mergée → à nettoyer avec /branch-cleanup"
      cleanup_found=1
    fi
  done <<EOF
$(git branch --format='%(refname:short)' | grep -v '^main$')
EOF
  [ "$cleanup_found" = 0 ] && ok "aucune branche locale mergée en attente de nettoyage"
else
  warn "hors dépôt git — contrôle des branches locales ignoré"
fi

# --- 6. Réglages de merge du projet ---------------------------------------------------------------
# Dérive si le projet n'exige plus un pipeline vert pour merger (posé par bootstrap.sh, voir
# docs/10-workflow-git.md §6). Lecture REST directe — l'encodage du chemin projet est inline
# pour rester autosuffisant. `allow_merge_on_skipped_pipeline` peut revenir `null` selon le
# tier : seul `false` explicite vaut ✓.
section "6. Réglages de merge du projet"
proj_raw="$(glab api "projects/$(printf '%s' "$GL_PROJECT" | sed 's,/,%2F,g')" 2>/dev/null)"
if [ -z "$proj_raw" ]; then
  warn "réglages du projet illisibles (API muette) — contrôle ignoré"
else
  if printf '%s' "$proj_raw" | grep -q '"only_allow_merge_if_pipeline_succeeds":true'; then
    ok "only_allow_merge_if_pipeline_succeeds=true — pipeline vert requis pour merger"
  else
    warn "only_allow_merge_if_pipeline_succeeds ≠ true : une MR au pipeline rouge est mergeable → relancer scripts/gitlab/bootstrap.sh"
  fi
  if printf '%s' "$proj_raw" | grep -q '"allow_merge_on_skipped_pipeline":false'; then
    ok "allow_merge_on_skipped_pipeline=false — un pipeline sauté ne permet pas de merger"
  else
    warn "allow_merge_on_skipped_pipeline ≠ false : un pipeline sauté permettrait de merger → relancer scripts/gitlab/bootstrap.sh"
  fi
fi

# --- Résumé -------------------------------------------------------------------------------------
section "Résumé"
printf '  %d erreur(s), %d avertissement(s)\n' "$errors" "$warns"
if [ "$errors" -gt 0 ]; then exit 1; fi
if [ "$strict" = 1 ] && [ "$warns" -gt 0 ]; then exit 1; fi
exit 0
