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

# Délai (en jours) entre la date de début et l'échéance, par priorité. L'échéance est posée au
# /ticket-start = début + délai. Surchargeable par variables d'environnement.
GL_DUE_DELAY_HAUTE="${GL_DUE_DELAY_HAUTE:-2}"
GL_DUE_DELAY_MOYENNE="${GL_DUE_DELAY_MOYENNE:-5}"
GL_DUE_DELAY_BASSE="${GL_DUE_DELAY_BASSE:-10}"

# Retry des LECTURES GraphQL (voir gl_graphql_read) : l'endpoint GraphQL de GitLab renvoie
# parfois une réponse vide (hoquet réseau / rate-limit). On ré-essaie jusqu'à GL_GQL_RETRIES
# tentatives, avec GL_GQL_RETRY_DELAY seconde(s) de pause entre deux. Surchargeable.
GL_GQL_RETRIES="${GL_GQL_RETRIES:-3}"
GL_GQL_RETRY_DELAY="${GL_GQL_RETRY_DELAY:-1}"

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

# --- Lecture GraphQL (avec retry) ---------------------------------------------------------------
# gl_graphql_read <query> -> exécute une LECTURE GraphQL et imprime la réponse JSON brute.
# Réessaie tant que la réponse revient VIDE (l'endpoint GraphQL de GitLab hoquette par
# intermittence : réponse vide/tronquée sur rate-limit ou aléa réseau), jusqu'à GL_GQL_RETRIES
# tentatives avec GL_GQL_RETRY_DELAY s de pause. Ne réessaie QUE sur réponse vide : une réponse
# non vide — même porteuse d'erreurs applicatives GraphQL — est rendue telle quelle, l'appelant
# reste responsable de son parsing.
# ⚠ Réservé aux LECTURES. Ne jamais envelopper une mutation : un retry pourrait la ré-appliquer
# (ex. gl_log_time est additif → double comptage). Les mutations gardent leur appel direct à glab.
gl_graphql_read() {
  local query="$1"
  if [ -z "$query" ]; then echo "gl_graphql_read : requête manquante" >&2; return 2; fi
  local attempt=1 out
  while :; do
    out="$(glab api graphql -f query="$query" 2>/dev/null)"
    if [ -n "$out" ]; then printf '%s\n' "$out"; return 0; fi
    if [ "$attempt" -ge "$GL_GQL_RETRIES" ]; then
      echo "gl_graphql_read : réponse vide de l'API GraphQL après $attempt tentative(s)" >&2
      return 1
    fi
    sleep "$GL_GQL_RETRY_DELAY"
    attempt=$((attempt + 1))
  done
}

# --- Résolution d'identifiants ------------------------------------------------------------------
# gl_workitem_gid <iid> -> imprime le GID global du work item (gid://gitlab/WorkItem/<n>).
gl_workitem_gid() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "gl_workitem_gid : iid manquant" >&2; return 2; fi
  local gid
  gid="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { workItems(iids:["'"$iid"'"]) { nodes { id } } } }' \
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
  raw="$(gl_graphql_read '{ group(fullPath:"'"$GL_GROUP"'") { lifecycles { nodes { name statuses { id name } } } } }')"
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

# --- Lecture / reporting ------------------------------------------------------------------------
# gl_backlog [state] -> JSON des work items du projet avec leur STATUT NATIF, leurs labels et
# leurs assignés. state ∈ opened (défaut) | closed | all. Requête canonique du backlog : elle
# lit le statut par son nom (aucun GID), et sert de source unique à /backlog comme aux futurs
# outils (Control Tower, agents). La mise en forme (regroupement par statut) est laissée à
# l'appelant — jq n'est pas requis.
gl_backlog() {
  local state="${1:-opened}"
  case "$state" in opened|closed|all) ;; *) echo "state invalide : $state (opened|closed|all)" >&2; return 2 ;; esac
  gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { workItems(state: '"$state"', first: 100) { nodes { iid title widgets { ... on WorkItemWidgetStatus { status { name } } ... on WorkItemWidgetLabels { labels { nodes { title } } } ... on WorkItemWidgetAssignees { assignees { nodes { username } } } } } } } }'
}

# gl_backlog_table [state] -> projette le JSON de gl_backlog en une TABLE PLATE COMPACTE (une ligne
# par ticket) pour réinjecter beaucoup moins de contexte que le JSON imbriqué. Le JSON brut reste
# disponible via gl_backlog / la sous-commande `backlog` pour tout appelant qui en a besoin.
#
# Format de sortie (source unique, exploitable par /backlog comme par les futurs outils — Control
# Tower, agents) : TSV (séparateur TABULATION), une ligne d'en-tête préfixée « # » que les
# consommateurs machine peuvent ignorer, puis une ligne par ticket :
#     iid <TAB> statut <TAB> prio <TAB> agent <TAB> assigne <TAB> titre
# Les valeurs `prio`/`agent` sont le suffixe nu du label (« moyenne », « devops ») ; un champ vide
# (prio/agent/assigné absent) est rendu « - ». Le statut est le statut NATIF (widget Status).
#
# Projection en awk pur (pas de jq requis) : le parsing suit la même approche grep/sed/awk que le
# reste de ce fichier, donc la commande fonctionne à l'identique que jq soit installé ou non.
gl_backlog_table() {
  local state="${1:-opened}" json
  json="$(gl_backlog "$state")" || return 1
  printf '# iid\tstatut\tprio\tagent\tassigne\ttitre\n'
  printf '%s\n' "$json" | awk '
    {
      n = split($0, parts, /\{"iid":"/)
      for (i = 2; i <= n; i++) {
        node = parts[i]
        match(node, /^[0-9]+/); iid = substr(node, RSTART, RLENGTH)

        title = "-"
        if (match(node, /","title":"/)) {
          rest = substr(node, RSTART + RLENGTH)
          if (match(rest, /","widgets":/)) title = substr(rest, 1, RSTART - 1)
        }
        gsub(/\\u0026/, "\\&", title); gsub(/\\u003e/, ">", title); gsub(/\\u003c/, "<", title)

        status = "-"
        if (match(node, /"status":\{"name":"[^"]*"/)) {
          m = substr(node, RSTART, RLENGTH); sub(/.*"name":"/, "", m); sub(/"$/, "", m); status = m
        }

        prio = "-"; agent = "-"
        if (match(node, /prio::[a-z]+/))  prio  = substr(node, RSTART + 6, RLENGTH - 6)
        if (match(node, /agent::[a-z]+/)) agent = substr(node, RSTART + 7, RLENGTH - 7)

        assignee = "-"
        if (match(node, /"username":"[^"]*"/)) {
          m = substr(node, RSTART, RLENGTH); sub(/.*"username":"/, "", m); sub(/"$/, "", m); assignee = m
        }

        printf "%s\t%s\t%s\t%s\t%s\t%s\n", iid, status, prio, agent, assignee, title
      }
    }
  '
}

# gl_issue_brief <iid> -> projection compacte de `glab issue view <iid>` : uniquement le titre, les
# labels et la section « Critères d'acceptation ». Le reste du corps (Description, « Pourquoi
# maintenant ? »…) est écarté. Utilisé par /ticket-start à la place du `glab issue view` intégral
# pour réinjecter moins de contexte (le view complet reste disponible en direct si besoin).
# Parsing en awk pur (pas de jq requis).
gl_issue_brief() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_issue_brief <iid>" >&2; return 2; fi
  local raw
  raw="$(glab issue view "$iid" 2>/dev/null)" || { echo "Issue #$iid introuvable dans $GL_PROJECT" >&2; return 1; }
  # Deux formats de « critères d'acceptation » coexistent dans le backlog : les tickets récents
  # (issue templates) posent un titre de section « ## Critères d'acceptation » suivi d'une liste
  # « - [ ] … » ; les tickets plus anciens l'écrivent en paragraphe inline (« Critères d'acceptation :
  # … »). Le mot « acceptation » n'a pas d'accent → on l'utilise comme ancre robuste aux deux formes
  # (avec ou sans accent sur « Critères »). En forme titre on capture les lignes suivantes jusqu'au
  # prochain titre ; en forme inline on n'imprime que la ligne elle-même.
  printf '%s\n' "$raw" | awk -v iid="$iid" '
    ph == 1 {
      if (crit == 0 && $0 ~ /[Aa]cceptation/) {
        print ""; print $0
        if ($0 ~ /^#+[ \t]/) crit = 1
        next
      }
      if (crit && $0 ~ /^#+[ \t]/) crit = 0
      if (crit) print $0
      next
    }
    /^--$/ {
      printf "#%s — %s\n", iid, title
      if (labels != "") printf "labels: %s\n", labels
      ph = 1; next
    }
    /^title:/  { t = $0; sub(/^title:[ \t]*/,  "", t); title  = t; next }
    /^labels:/ { l = $0; sub(/^labels:[ \t]*/, "", l); labels = l; next }
  '
}

# --- Dates & time tracking ----------------------------------------------------------------------
# Renseignés automatiquement le long du cycle de vie (voir docs/10-workflow-git.md §3.3) :
#   • date de début + échéance  → posées par /ticket-start (gl_start_dates)
#   • temps passé               → proposé puis loggé par /ticket-finish (gl_log_time)
# Tout passe par la mutation workItemUpdate, comme gl_set_status (widgets startAndDueDate / timeTracking).

# gl_prio <iid> -> imprime le label prio du ticket (« prio::haute » | « prio::moyenne » | « prio::basse »),
# vide si absent.
gl_prio() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "gl_prio : iid manquant" >&2; return 2; fi
  gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { workItems(iids:["'"$iid"'"]) { nodes { widgets { ... on WorkItemWidgetLabels { labels { nodes { title } } } } } } } }' \
    | grep -o 'prio::[a-z]*' | head -1
}

# gl_prio_delay <prio> -> imprime le délai (jours) pour l'échéance. Accepte « haute » ou
# « prio::haute ». Défaut (moyenne ou priorité absente) = GL_DUE_DELAY_MOYENNE.
gl_prio_delay() {
  case "${1#prio::}" in
    haute) echo "$GL_DUE_DELAY_HAUTE" ;;
    basse) echo "$GL_DUE_DELAY_BASSE" ;;
    *)     echo "$GL_DUE_DELAY_MOYENNE" ;;
  esac
}

# gl_get_start_date <iid> -> imprime la date de début (YYYY-MM-DD) déjà posée sur le ticket,
# vide si aucune. Sert à /ticket-finish (calcul du temps écoulé) et à l'idempotence de gl_start_dates.
gl_get_start_date() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "gl_get_start_date : iid manquant" >&2; return 2; fi
  gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { workItems(iids:["'"$iid"'"]) { nodes { widgets { ... on WorkItemWidgetStartAndDueDate { startDate } } } } } }' \
    | grep -o '"startDate":"[0-9-]*"' | head -1 | sed 's/.*"startDate":"//; s/"$//'
}

# gl_get_time_spent <iid> -> imprime le temps total déjà loggé, en secondes (0 si aucun).
# Sert à /ticket-finish pour ne pas re-logger silencieusement du temps sur une ré-exécution.
gl_get_time_spent() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "gl_get_time_spent : iid manquant" >&2; return 2; fi
  local v
  v="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { workItems(iids:["'"$iid"'"]) { nodes { widgets { ... on WorkItemWidgetTimeTracking { totalTimeSpent } } } } } }' \
    | grep -o '"totalTimeSpent":[0-9]*' | head -1 | sed 's/.*://')"
  printf '%s\n' "${v:-0}"
}

# gl_elapsed_days <date-début YYYY-MM-DD> -> imprime le nombre de jours calendaires écoulés
# entre cette date et aujourd'hui (entier, plancher 0).
gl_elapsed_days() {
  local start="$1"
  if [ -z "$start" ]; then echo "gl_elapsed_days : date de début manquante" >&2; return 2; fi
  local s n d
  s="$(date -d "$start" +%s 2>/dev/null)" || { echo "gl_elapsed_days : date invalide « $start »" >&2; return 1; }
  n="$(date +%s)"
  d=$(( (n - s) / 86400 ))
  [ "$d" -lt 0 ] && d=0
  printf '%s\n' "$d"
}

# gl_set_dates <iid> [début] [échéance] -> pose le widget startAndDueDate (dates YYYY-MM-DD).
# Un argument vide laisse le champ correspondant inchangé ; au moins une date est requise.
gl_set_dates() {
  local iid="$1" start="$2" due="$3"
  if [ -z "$iid" ]; then echo "usage: gl_set_dates <iid> [début YYYY-MM-DD] [échéance YYYY-MM-DD]" >&2; return 2; fi
  if [ -z "$start" ] && [ -z "$due" ]; then echo "gl_set_dates : au moins une date (début ou échéance) requise" >&2; return 2; fi
  local wiid frag out
  wiid="$(gl_workitem_gid "$iid")" || return 1
  frag=""
  [ -n "$start" ] && frag="startDate:\"$start\""
  [ -n "$due" ]   && frag="${frag:+$frag, }dueDate:\"$due\""
  out="$(glab api graphql -f query='mutation { workItemUpdate(input:{ id:"'"$wiid"'", startAndDueDateWidget:{ '"$frag"' } }){ errors } }' 2>&1)"
  case "$out" in
    *'"errors":[]'*) printf 'Dates de #%s → début=%s, échéance=%s\n' "$iid" "${start:-inchangé}" "${due:-inchangé}" ;;
    *) echo "Échec de la pose des dates sur #$iid : $out" >&2; return 1 ;;
  esac
}

# gl_start_dates <iid> -> pose les dates au démarrage : début = aujourd'hui (conservé si déjà
# renseigné), échéance = début + délai dérivé de la priorité du ticket (gl_prio_delay). Idempotent :
# une ré-exécution garde la date de début d'origine et recalcule l'échéance.
gl_start_dates() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_start_dates <iid>" >&2; return 2; fi
  local today start prio delay due
  today="$(date +%F)"
  start="$(gl_get_start_date "$iid")"
  [ -z "$start" ] && start="$today"
  prio="$(gl_prio "$iid")"
  delay="$(gl_prio_delay "$prio")"
  due="$(date -d "$start +$delay days" +%F 2>/dev/null)"
  if [ -z "$due" ]; then echo "gl_start_dates : calcul de l'échéance impossible (commande date indisponible ?)" >&2; return 1; fi
  gl_set_dates "$iid" "$start" "$due" || return 1
  printf '  (priorité %s → échéance à +%s j)\n' "${prio:-prio::moyenne (défaut)}" "$delay"
}

# gl_log_time <iid> <durée> [résumé] -> ajoute une entrée de temps passé (timelog) au ticket.
# <durée> au format GitLab (« 2h », « 1h 30m », « 1d »…). Additif côté GitLab (n'écrase pas l'existant).
gl_log_time() {
  local iid="$1" dur="$2" summary="${3:-}"
  if [ -z "$iid" ] || [ -z "$dur" ]; then echo "usage: gl_log_time <iid> <durée> [résumé]" >&2; return 2; fi
  local wiid out sumfrag
  wiid="$(gl_workitem_gid "$iid")" || return 1
  sumfrag=""
  [ -n "$summary" ] && sumfrag=", summary:\"$summary\""
  out="$(glab api graphql -f query='mutation { workItemUpdate(input:{ id:"'"$wiid"'", timeTrackingWidget:{ timelog:{ timeSpent:"'"$dur"'"'"$sumfrag"' } } }){ errors } }' 2>&1)"
  case "$out" in
    *'"errors":[]'*) printf 'Temps loggé sur #%s : %s\n' "$iid" "$dur" ;;
    *) echo "Échec du log de temps sur #$iid : $out" >&2; return 1 ;;
  esac
}

# --- Nettoyage des branches locales -------------------------------------------------------------
# gl_mr_state <branche> -> imprime l'état de la MR associée à la branche (opened|closed|merged),
# vide si aucune MR n'est trouvée.
gl_mr_state() {
  local branch="$1"
  if [ -z "$branch" ]; then echo "gl_mr_state : branche manquante" >&2; return 2; fi
  glab mr view "$branch" --output json 2>/dev/null \
    | grep -o '"state":"[a-z]*"' | head -1 | sed 's/.*:"//; s/"//'
}

# gl_cleanup_merged -> supprime les branches LOCALES (hors main et hors branche courante) dont
# GitLab confirme la MR à l'état « merged ». Conçu pour tourner automatiquement (appelé par
# /ticket-start après mise à jour de main) — c'est le pendant non-interactif de /branch-cleanup :
#   • ne supprime QUE ce que GitLab confirme mergé (garde-fou docs/10 §6) — jamais une branche au
#     statut incertain (opened/closed/aucune MR) ;
#   • `git branch -D` est sûr ici car le merge est confirmé (le projet merge en squash) ;
#   • ne change jamais de branche, n'écrit rien sur GitLab, et s'abstient si l'arbre est sale.
gl_cleanup_merged() {
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    echo "Nettoyage des branches ignoré : changements non commités présents." >&2
    return 0
  fi
  # Pruning cosmétique des refs de suivi ; non bloquant (jamais de prompt d'identifiants) et non
  # fatal : la décision de suppression s'appuie sur l'état MR côté GitLab, pas sur ce fetch.
  GIT_TERMINAL_PROMPT=0 git fetch --prune origin >/dev/null 2>&1
  local current branch state deleted=0 kept=0
  current="$(git branch --show-current 2>/dev/null)"
  while IFS= read -r branch; do
    [ -z "$branch" ] && continue
    [ "$branch" = "main" ] && continue
    [ "$branch" = "$current" ] && continue
    state="$(gl_mr_state "$branch")"
    if [ "$state" = "merged" ]; then
      if git branch -D "$branch" >/dev/null 2>&1; then
        printf '  supprimée : %s (MR merged)\n' "$branch"
        deleted=$((deleted + 1))
      fi
    else
      kept=$((kept + 1))
    fi
  done < <(git branch --format='%(refname:short)')
  printf 'Nettoyage des branches : %s supprimée(s), %s conservée(s).\n' "$deleted" "$kept"
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
    graphql-read)   gl_graphql_read "$@" ;;
    workitem-gid)   gl_workitem_gid "$@" ;;
    status-gid)     gl_status_gid "$@" ;;
    set-status)     gl_set_status "$@" ;;
    backlog)        gl_backlog "$@" ;;
    backlog-table)  gl_backlog_table "$@" ;;
    issue-brief)    gl_issue_brief "$@" ;;
    prio)           gl_prio "$@" ;;
    prio-delay)     gl_prio_delay "$@" ;;
    get-start-date) gl_get_start_date "$@" ;;
    get-time-spent) gl_get_time_spent "$@" ;;
    elapsed-days)   gl_elapsed_days "$@" ;;
    set-dates)      gl_set_dates "$@" ;;
    start-dates)    gl_start_dates "$@" ;;
    log-time)       gl_log_time "$@" ;;
    mr-state)       gl_mr_state "$@" ;;
    cleanup-merged) gl_cleanup_merged "$@" ;;
    slug)           gl_slug "$@" ;;
    branch-prefix)  gl_branch_prefix "$@" ;;
    *)
      echo "usage: bash scripts/gitlab/lib.sh <sous-commande> [args]" >&2
      echo "  require | workitem-gid <iid> | status-gid <nom> | set-status <iid> <nom>" >&2
      echo "  backlog [opened|closed|all]        (JSON brut du backlog)" >&2
      echo "  backlog-table [opened|closed|all]  (table plate compacte TSV — voir en-tête gl_backlog_table)" >&2
      echo "  issue-brief <iid>                  (titre + labels + critères d'acceptation)" >&2
      echo "  slug <titre> | branch-prefix <type>" >&2
      echo "  Dates & temps :" >&2
      echo "    start-dates <iid>            (début=aujourd'hui + échéance selon prio)" >&2
      echo "    set-dates <iid> [début] [échéance]   get-start-date <iid>" >&2
      echo "    prio <iid>   prio-delay <prio>   elapsed-days <date>" >&2
      echo "    log-time <iid> <durée> [résumé]   get-time-spent <iid>" >&2
      echo "  Branches :" >&2
      echo "    cleanup-merged              (supprime les branches locales dont la MR est mergée)" >&2
      echo "    mr-state <branche>          (opened|closed|merged)" >&2
      exit 2 ;;
  esac
fi
