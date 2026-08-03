#!/usr/bin/env bash
# Bilan de santé (LECTURE SEULE) du setup GitLab Maestro + détection de dérive.
# N'écrit jamais rien (ni état, ni label, ni MR) — voir docs/10-workflow-git.md.
# Réutilise scripts/gitlab/lib.sh (cycle de vie par nom de label, pas de GID en dur).
#
# Usage :  bash scripts/gitlab/doctor.sh [--strict]
#   --strict : code de sortie non nul aussi en présence d'avertissements (utile en CI).
# Code de sortie : 1 si un contrôle DUR échoue (auth, labels de catégorisation, labels de cycle
#   de vie) ; sinon 0
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

# --- 3. Labels de cycle de vie « workflow:: » ----------------------------------------------------
# Depuis #209 le cycle de vie n'est plus le champ Status natif (lifecycle custom « Maestro »,
# Premium, disparu avec l'essai Ultimate) mais des labels scopés `workflow::*` — voir le contrat de
# surface en tête de lib.sh. Une SEULE lecture (gl_workflow_gids, avec retry) pour les six, comme
# la section le faisait pour les six statuts : on évite le faux « incohérent » que six appels
# indépendants déclenchaient dès qu'un seul retombait vide.
section "3. Cycle de vie (labels $GL_WORKFLOW_SCOPE::* et colonnes du Kanban)"
workflow_gids="$(gl_workflow_gids 2>/dev/null)"
if [ -z "$workflow_gids" ]; then
  err "aucun label « $GL_WORKFLOW_SCOPE::* » lisible dans $GL_PROJECT → relancer scripts/gitlab/bootstrap.sh"
else
  missing_workflow=""
  for s in a-faire en-cours en-revue termine abandonne doublon; do
    printf '%s\n' "$workflow_gids" | cut -f1 | grep -qx "$s" \
      || missing_workflow="${missing_workflow:+$missing_workflow, }$GL_WORKFLOW_SCOPE::$s"
  done
  if [ -z "$missing_workflow" ]; then
    ok "6 labels de cycle de vie résolus par nom (1 appel) — set-workflow opérationnel (aucun GID en dur)"
  else
    err "label(s) de cycle de vie manquant(s) : $missing_workflow → relancer scripts/gitlab/bootstrap.sh"
  fi
fi

# Les colonnes du Kanban, posées par bootstrap-board.sh sur ces mêmes labels : sans elles les
# tickets existent mais le board ne montre rien, ce qui était le symptôme visible de #207. Le board
# est UNIQUE sur le plan Free, on le découvre donc plutôt que de figer un id. Deux dérives à
# attraper — une colonne du flux manquante, et une liste qui n'en fait pas partie (dont les listes
# ORPHELINES `"label":null` héritées des colonnes par statut, qui n'affichent plus rien et ne
# partent pas toutes seules). L'ORDRE, lui, n'est pas contrôlé ici : il est cosmétique, et
# bootstrap-board.sh le rétablit — le signaler ferait du bruit sans enjeu.
board_flux="$GL_WORKFLOW_SCOPE::a-faire $GL_WORKFLOW_SCOPE::en-cours $GL_WORKFLOW_SCOPE::en-revue $GL_WORKFLOW_SCOPE::termine"
board_id="$(glab api "projects/$(gl_project_enc)/boards" --output ndjson 2>/dev/null | grep -o '^{"id":[0-9]\+' | head -1 | grep -o '[0-9]\+')"
if [ -z "$board_id" ]; then
  warn "aucun board Kanban sur $GL_PROJECT — le créer une fois dans l'UI (Plan > Boards), puis : bash scripts/gitlab/bootstrap-board.sh"
else
  # Une liste par ligne (ndjson) ; « - » pour une liste orpheline, dont l'objet `label` est null.
  board_listes="$(glab api "projects/$(gl_project_enc)/boards/$board_id/lists" --output ndjson 2>/dev/null | awk '
    /^\{/ {
      nom = "-"
      if (match($0, /"label":\{/)) {
        reste = substr($0, RSTART)
        if (match(reste, /"name":"[^"]*"/)) nom = substr(reste, RSTART + 8, RLENGTH - 9)
      }
      print nom
    }
  ')"
  if [ -z "$board_listes" ]; then
    warn "colonnes du board #$board_id illisibles (API muette) — contrôle ignoré"
  else
    manquantes=""
    for nom in $board_flux; do
      printf '%s\n' "$board_listes" | grep -qx -- "$nom" || manquantes="${manquantes:+$manquantes, }$nom"
    done
    intruses=""
    while IFS= read -r nom; do
      [ -z "$nom" ] && continue
      case " $board_flux " in *" $nom "*) continue ;; esac
      [ "$nom" = "-" ] && nom="liste orpheline (label supprimé)"
      intruses="${intruses:+$intruses, }$nom"
    done <<EOF
$board_listes
EOF
    if [ -z "$manquantes" ] && [ -z "$intruses" ]; then
      ok "board #$board_id : 4 colonnes du flux (à-faire → en-cours → en-revue → terminé)"
    else
      [ -n "$manquantes" ] && warn "board #$board_id : colonne(s) manquante(s) : $manquantes → bash scripts/gitlab/bootstrap-board.sh"
      [ -n "$intruses" ]   && warn "board #$board_id : liste(s) hors flux : $intruses → bash scripts/gitlab/bootstrap-board.sh"
    fi
  fi
fi

# --- 4. Dérive cycle de vie ↔ réalité -----------------------------------------------------------
section "4. Dérive cycle de vie ↔ réalité"

# Les deux backlogs, lus UNE fois chacun : les trois contrôles ci-dessous s'en servent et
# gl_backlog n'a pas de cache — une lecture par contrôle multiplierait les allers-retours d'un
# bilan qui en fait déjà beaucoup. Découpés en un nœud par ligne dès ici, forme sur laquelle
# travaillent aussi bien grep (4a, 4b) que awk (4c).
backlog_opened="$(gl_backlog opened | sed 's/{"iid":/\n{"iid":/g')"
backlog_closed="$(gl_backlog closed | sed 's/{"iid":/\n{"iid":/g')"

# helper local : iid des work items d'un backlog déjà lu portant un cycle de vie donné.
# Le cycle de vie est un LABEL depuis #209 : on filtre sur `workflow::<slug>` et non plus sur le
# widget de statut. L'argument reste le LIBELLÉ (« En revue »), converti ici par gl_workflow_slug —
# c'est le contrat de surface de lib.sh, le slug ne circule pas dans les appelants.
iids_with_workflow() { # $1=backlog découpé  $2=libellé de cycle de vie
  local slug
  slug="$(gl_workflow_slug "$2")" || return 1
  printf '%s\n' "$1" \
    | grep -F '"'"$GL_WORKFLOW_SCOPE"'::'"$slug"'"' \
    | grep -o '"iid":"[0-9]*"' | grep -o '[0-9]*'
}

# 4a. Tickets « En revue » ouverts : une MR ouverte est-elle rattachée ?
revue_iids="$(iids_with_workflow "$backlog_opened" "En revue")"
open_mr_branches="$(glab mr list --output json 2>/dev/null | grep -o '"source_branch":"[^"]*"' | cut -d'"' -f4)"
if [ -z "$revue_iids" ]; then
  ok "aucun ticket « En revue » en attente"
else
  for iid in $revue_iids; do
    if printf '%s\n' "$open_mr_branches" | grep -q "/$iid-"; then
      ok "#$iid « En revue » ↔ MR ouverte"
    else
      warn "#$iid « En revue » sans MR ouverte rattachée (état resté après merge/close ?)"
    fi
  done
fi

# 4b. Tickets fermés dont le cycle de vie est resté « actif »
stuck_iids="$(printf '%s\n' "$backlog_closed" \
  | grep -E '"'"$GL_WORKFLOW_SCOPE"'::(a-faire|en-cours|en-revue)"' \
  | grep -o '"iid":"[0-9]*"' | grep -o '[0-9]*')"
if [ -z "$stuck_iids" ]; then
  ok "aucun ticket fermé à l'état encore actif"
else
  for iid in $stuck_iids; do
    warn "#$iid est fermé mais son état est encore « actif » (attendu : Terminé/Abandonné/Doublon)"
  done
fi

# 4c. L'invariant « exactement un workflow:: par ticket ouvert ».
# C'est LA dérive propre au dispositif par labels, et rien d'autre ne l'attrape : l'exclusion
# mutuelle des labels scopés est une fonctionnalité Premium, donc sur Free le « :: » n'est que
# cosmétique et rien n'empêche un ticket de porter deux valeurs à la fois (docs/10 §3, #207). Deux
# cas, de causes opposées :
#   • 0 label  → ticket échappé à la migration, ou créé depuis l'UI GitLab (qui ne connaît pas
#                notre convention) : il n'est sur AUCUNE colonne du Kanban et sort de tous les
#                comptes (`queue.sh` ne le verra pas, `/backlog` le rendra « - ») ;
#   • ≥ 2      → pose partielle : un ajout sans le retrait des autres. Les lectures rendent alors
#                le PREMIER label rencontré (cf. gl_awk_workflow), donc un état plausible mais
#                arbitraire — le plus pernicieux des deux, puisque rien ne dépasse à l'affichage.
# Aucune lecture supplémentaire (le backlog ouvert est déjà en main) ; comptage des labels du scope
# nœud par nœud, en awk pur comme le reste du fichier.
wf_derives="$(printf '%s\n' "$backlog_opened" | awk -v WF_SCOPE="$GL_WORKFLOW_SCOPE" '
  /^\{"iid":"/ {
    match($0, /"iid":"[0-9]+/); iid = substr($0, RSTART + 7, RLENGTH - 7)
    n = 0; reste = $0; motif = "\"" WF_SCOPE "::[a-z-]+\""
    while (match(reste, motif)) { n++; reste = substr(reste, RSTART + RLENGTH) }
    if (n != 1) printf "%s\t%d\n", iid, n
  }
')"
if [ -z "$wf_derives" ]; then
  ok "tous les tickets ouverts portent exactement un label $GL_WORKFLOW_SCOPE::*"
else
  while IFS=$'\t' read -r iid n; do
    [ -z "$iid" ] && continue
    if [ "$n" = 0 ]; then
      warn "#$iid ouvert sans label $GL_WORKFLOW_SCOPE::* — hors du Kanban et de tous les comptes → poser : bash scripts/gitlab/lib.sh set-workflow $iid \"<état>\""
    else
      warn "#$iid ouvert porte $n labels $GL_WORKFLOW_SCOPE::* (un seul attendu) — les lectures en rendent un au hasard → reposer le bon : bash scripts/gitlab/lib.sh set-workflow $iid \"<état>\""
    fi
  done <<EOF
$wf_derives
EOF
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
# Dérive si le projet n'exige plus un pipeline vert pour merger, ou ne supprime plus la branche
# source au merge (tous posés par bootstrap.sh, voir docs/10-workflow-git.md §6). Lecture REST
# directe — l'encodage du chemin projet est inline pour rester autosuffisant. Ces champs peuvent
# revenir `null` selon le tier : seule la valeur explicite attendue vaut ✓.
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
  if printf '%s' "$proj_raw" | grep -q '"remove_source_branch_after_merge":true'; then
    ok "remove_source_branch_after_merge=true — la branche source est supprimée au merge"
  else
    warn "remove_source_branch_after_merge ≠ true : les branches distantes s'accumuleraient après merge → relancer scripts/gitlab/bootstrap.sh"
  fi
fi

# --- 7. Runner CI de projet -----------------------------------------------------------------------
# Première cause de MR bloquée (#157) : les runners partagés sont désactivés
# (`shared_runners_enabled=false`, #135) et le merge exige un pipeline vert — si aucun runner de
# PROJET n'est en ligne, les jobs restent `pending` et personne ne merge, sans qu'aucun message ne
# le dise. Contrôle SOUPLE (avertissement) : le runner de la machine peut être légitimement éteint
# pendant qu'on code ; ce qui compte est de savoir qu'il faudra le rallumer avant la MR.
# Une seule lecture REST : /projects/:id/runners porte déjà `status` pour chaque runner.
section "7. Runner CI de projet (§8)"
runners_raw="$(glab api "projects/$(gl_project_enc)/runners?type=project_type&per_page=100" 2>/dev/null)"
if [ -z "$runners_raw" ]; then
  warn "runners de projet illisibles (API muette) — contrôle ignoré"
elif [ "$runners_raw" = "[]" ]; then
  warn "aucun runner de projet déclaré : les pipelines resteront « pending » (runners partagés désactivés)"
  warn "  → en créer un sur cette machine : bash scripts/gitlab/setup-runner.sh"
else
  # Un runner par ligne. `"id":<n>,"description":"…"` n'apparaît que sur les runners : l'objet
  # imbriqué `created_by` a bien un `id`, mais suivi de `"username"` (même repère que
  # ensure-runner.sh). `status` se lit ensuite dans la ligne, sans confusion possible avec
  # `job_execution_status`, dont le nom ne contient pas la séquence `"status":`.
  runners_lignes="$(printf '%s' "$runners_raw" | sed 's/},{"id":/}\n{"id":/g')"
  runners_en_ligne="$(printf '%s\n' "$runners_lignes" | grep -F '"status":"online"')"
  if [ -n "$runners_en_ligne" ]; then
    while IFS= read -r ligne; do
      [ -z "$ligne" ] && continue
      rid="$(printf '%s' "$ligne" | grep -o '"id":[0-9]\+' | head -1 | grep -o '[0-9]\+')"
      rdesc="$(printf '%s' "$ligne" | grep -o '"description":"[^"]*"' | head -1 | cut -d'"' -f4)"
      ok "runner de projet en ligne : ${rdesc:-sans description} (#${rid:-?})"
    done <<EOF
$runners_en_ligne
EOF
  else
    hors_ligne=""
    while IFS= read -r ligne; do
      [ -z "$ligne" ] && continue
      rdesc="$(printf '%s' "$ligne" | grep -o '"description":"[^"]*"' | head -1 | cut -d'"' -f4)"
      rstat="$(printf '%s' "$ligne" | grep -o '"status":"[^"]*"' | head -1 | cut -d'"' -f4)"
      hors_ligne="${hors_ligne:+$hors_ligne, }${rdesc:-?} (${rstat:-?})"
    done <<EOF
$runners_lignes
EOF
    warn "aucun runner de projet en ligne — les jobs resteront « pending » et le merge sera bloqué [$hors_ligne]"
    warn "  → rallumer celui de cette machine : bash scripts/gitlab/ensure-runner.sh"
  fi
fi

# --- 8. Milestones de phase -----------------------------------------------------------------------
# Dérives autour du milestone de phase (docs/10-workflow-git.md §3.4) : un ticket OUVERT sans
# milestone (l'outillage pose la phase courante à la création — lib.sh current-milestone) ; un
# milestone actif ENTIÈREMENT SOLDÉ (la phase est finie : sa fermeture — décision humaine, jamais
# faite ici — est à faire pour que la phase suivante devienne la courante).
section "8. Milestones de phase"
ms_raw="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { milestones(state: active, sort: DUE_DATE_ASC, first: 20) { nodes { title stats { totalIssuesCount closedIssuesCount } } } } }')"
if [ -z "$ms_raw" ]; then
  warn "milestones illisibles (API muette) — contrôle ignoré"
else
  soldes="$(printf '%s' "$ms_raw" | awk '
    {
      n = split($0, parts, /\{"title":"/)
      for (i = 2; i <= n; i++) {
        node = parts[i]
        t = node; sub(/".*$/, "", t)
        total = 0; closed = -1
        if (match(node, /"totalIssuesCount":[0-9]+/))  { m = substr(node, RSTART, RLENGTH); sub(/.*:/, "", m); total = m + 0 }
        if (match(node, /"closedIssuesCount":[0-9]+/)) { m = substr(node, RSTART, RLENGTH); sub(/.*:/, "", m); closed = m + 0 }
        if (total > 0 && closed == total) print t
      }
    }
  ')"
  if [ -z "$soldes" ]; then
    ok "aucun milestone actif entièrement soldé"
  else
    while IFS= read -r t; do
      [ -z "$t" ] && continue
      warn "milestone « $t » actif mais entièrement soldé → à fermer (décision humaine) pour que la phase suivante devienne la courante"
    done <<EOF
$soldes
EOF
  fi
  courant="$(gl_current_milestone 2>/dev/null)"
  if [ -n "$courant" ]; then
    ok "phase courante : « $courant » (milestone posé par /ticket-create sur les nouveaux tickets)"
  else
    warn "aucun milestone actif non soldé — /ticket-create créera les prochains tickets sans milestone"
  fi
fi

wi_raw="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { workItems(state: opened, first: 100) { nodes { iid widgets { ... on WorkItemWidgetMilestone { milestone { title } } } } } } }' 2>/dev/null)"
if [ -z "$wi_raw" ]; then
  warn "tickets ouverts illisibles (API muette) — contrôle des milestones manquants ignoré"
else
  nomiles="$(printf '%s' "$wi_raw" | sed 's/{"iid":/\n{"iid":/g' \
    | awk '/^\{"iid":"/ && !/"milestone":\{"title":"/ { match($0, /"iid":"[0-9]+/); print substr($0, RSTART + 7, RLENGTH - 7) }')"
  if [ -z "$nomiles" ]; then
    ok "tous les tickets ouverts portent un milestone"
  else
    for iid in $nomiles; do
      warn "#$iid ouvert sans milestone → poser celui de sa phase : glab issue update $iid -m \"<titre>\""
    done
  fi
fi

# --- Résumé -------------------------------------------------------------------------------------
section "Résumé"
printf '  %d erreur(s), %d avertissement(s)\n' "$errors" "$warns"
if [ "$errors" -gt 0 ]; then exit 1; fi
if [ "$strict" = 1 ] && [ "$warns" -gt 0 ]; then exit 1; fi
exit 0
