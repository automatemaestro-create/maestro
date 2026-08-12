#!/usr/bin/env bash
# Helpers glab partagés pour le workflow de tickets Maestro (voir docs/10-workflow-git.md).
#
# Deux usages :
#   1. Sourcé depuis un autre script :   . scripts/gitlab/lib.sh ; gl_set_workflow 16 "En cours"
#   2. Exécuté en sous-commandes :        bash scripts/gitlab/lib.sh set-workflow 16 "En cours"
#
# ================================================================================================
# CONTRAT DE SURFACE DU CYCLE DE VIE — à lire avant d'y toucher (ticket #209, chantier #207)
# ================================================================================================
# Le cycle de vie d'un ticket (À faire / En cours / En revue / Terminé / Abandonné / Doublon) est
# porté par des LABELS SCOPÉS « workflow::* », et non plus par le champ Status natif de GitLab :
# les lifecycles custom sont une fonctionnalité Premium et l'essai Ultimate du groupe s'est terminé
# le 2026-08-02 (voir docs/10-workflow-git.md §3). Deux vocabulaires coexistent donc, et la règle
# est simple :
#
#   • SLUG      — « a-faire », « en-cours », « en-revue », « termine », « abandonne », « doublon ».
#                 C'est le STOCKAGE : le suffixe du label côté GitLab, ASCII par nécessité (un nom
#                 accentué devrait être ré-encodé dans chaque chemin `glab api` et à la création
#                 des listes de board — piège d'encodage connu sous Git Bash/Windows).
#   • LIBELLÉ   — « À faire », « En cours », « En revue », « Terminé », « Abandonné », « Doublon ».
#                 C'est la SURFACE : le vocabulaire du domaine, celui de la doc et des commandes.
#
# Décision, tranchée une fois pour toutes et valable pour TOUS les helpers de ce fichier :
#
#   → EN SORTIE, toujours le LIBELLÉ. Colonne `statut` des TSV (backlog-table, milestone-issues,
#     subtickets), gl_issue_owner, gl_start_brief, gl_close_guard : tous rendent « À faire », pas
#     « a-faire ». Le slug ne sort JAMAIS de ce fichier — c'est un détail de stockage.
#   → EN ENTRÉE, les DEUX sont acceptés (gl_set_workflow 16 "En cours" ≡ gl_set_workflow 16
#     en-cours), la normalisation étant faite par gl_workflow_slug. Écrire en libellé reste la
#     forme canonique dans les appelants.
#
# Pourquoi le libellé et pas le slug : les consommateurs comparent sur des chaînes en dur —
# queue.sh (« $2 == "À faire" »), run.sh (« En cours » / « En revue »), doctor.sh, et
# gl_subtickets_startables ici même. Garder le libellé fait de la bascule un changement INTERNE à
# ce fichier : les lots 3 et 4 de #207 n'ont pas à réécrire leurs comparaisons, seulement à
# renommer set-status → set-workflow. Passer aux slugs aurait propagé une rupture de contrat dans
# quatre scripts pour ne gagner qu'un `sed` de moins ici. Vérifié à la bascule : queue.sh a
# recommencé à compter des `a_faire` non nuls sans qu'une ligne y soit touchée.
#
# ⚠ L'EXCLUSION MUTUELLE EST À NOTRE CHARGE. Elle est Premium elle aussi : sur le plan Free, le
# « :: » n'est que cosmétique et rien n'empêche un ticket de porter deux labels workflow::. Toute
# pose doit donc AJOUTER la cible et RETIRER les cinq autres dans le MÊME appel (gl_set_workflow,
# gl_begin) — jamais un ajout seul. La détection de dérive (0 ou ≥ 2 labels) est le rôle de
# doctor.sh (lot 3 de #207).
#
# Comme pour les anciens GID de statut, aucun ID de label n'est codé en dur : gl_workflow_gids
# les re-dérive par NOM à chaque appel, donc le workflow survit à une recréation des labels.
# ================================================================================================
#
# NB : pas de `set -e` global — ce fichier est conçu pour être sourcé sans imposer son mode
# d'erreur au script appelant. Chaque fonction renvoie un code non nul en cas d'échec.

# --- Configuration (surchageable par variables d'environnement) --------------------------------
# Le répertoire de CE fichier, pour atteindre ses voisins (scripts/orchestrate/pilote.sh) sans
# dépendre du répertoire courant. Préfixé `GL_` parce que lib.sh est SOURCÉ par une dizaine de
# scripts qui ont déjà leur `ICI`/`RACINE` : écraser le leur les enverrait chercher leurs propres
# fichiers dans scripts/gitlab/.
GL_ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GL_PROJECT="${GL_PROJECT:-maestro-group4345327/maestro}"
GL_GROUP="${GL_GROUP:-${GL_PROJECT%%/*}}"   # groupe = tout ce qui précède le premier "/"
GL_WORKFLOW_SCOPE="${GL_WORKFLOW_SCOPE:-workflow}"  # scope des labels portant le cycle de vie

# Délai (en jours) entre la date de début et l'échéance, par priorité. L'échéance est posée au
# /ticket-start = début + délai. Surchargeable par variables d'environnement.
GL_DUE_DELAY_HAUTE="${GL_DUE_DELAY_HAUTE:-2}"
GL_DUE_DELAY_MOYENNE="${GL_DUE_DELAY_MOYENNE:-5}"
GL_DUE_DELAY_BASSE="${GL_DUE_DELAY_BASSE:-10}"

# Revue best-effort (voir la section « Revue » plus bas) : comptes d'AUTOMATISATION à ne jamais
# désigner comme relecteur, séparés par des virgules. Le compte de l'agent Maestro n'est pas un
# « bot » au sens de GitLab (`User.bot` y vaut false : c'est un compte utilisateur ordinaire), il ne
# peut donc pas être écarté par l'API seule. Cette liste est une CONFIGURATION d'instance — c'est le
# compte à exclure qui est nommé, jamais le relecteur, qui reste résolu dynamiquement.
GL_BOT_USERS="${GL_BOT_USERS:-MaestroAgents}"

# Niveau d'accès minimal d'un relecteur (30 = Developer) : en dessous, le membre ne peut ni pousser
# ni merger, donc le désigner n'aurait pas de sens.
GL_REVIEWER_MIN_ACCESS="${GL_REVIEWER_MIN_ACCESS:-30}"

# Seuil de SILENCE au-delà duquel un ticket « En cours » est tenu pour ORPHELIN (#328, docs/10 §9.6),
# en secondes. Volontairement GÉNÉREUX, et calé sur ce qui fait taire une session vivante : une
# session Claude Code qui épuise la limite d'usage de 5 h dort jusqu'à son reset sans rien écrire, et
# `run.sh` l'attend jusqu'à 5 h 30 (son PLAFOND_ATTENTE_S). Sous ce seuil, on désignerait comme
# abandonné un ticket dont la session attend légitimement — et c'est le sens dans lequel se tromper
# coûte cher, puisque #329 rendra l'orphelin prenable.
GL_ORPHELIN_SEUIL="${MAESTRO_ORPHELIN_SEUIL:-21600}"   # 6 h — juste au-dessus du plafond d'attente
# Une valeur d'environnement fantaisiste ne doit pas faire planter un verbe de diagnostic : on
# retombe sur le défaut plutôt que d'échouer au premier test arithmétique (même parti pris que
# scripts/orchestrate/status.sh).
[ "$GL_ORPHELIN_SEUIL" -ge 0 ] 2>/dev/null || GL_ORPHELIN_SEUIL=21600

# Nombre de fois qu'un même ticket peut être RENDU PRENABLE avant que la reprise ne demande
# `--force` (#329, docs/10 §9.6). Ce n'est pas un réglage de confort : sans plafond, un ticket que
# chaque session fait tomber au même endroit repart à chaque run, brûle une session entière et
# redevient orphelin — la reprise deviendrait une boucle, et une boucle sur un quota partagé. Deux
# essais, parce que le premier échec est souvent conjoncturel (limite d'usage, pilote tué) là où le
# second désigne le ticket lui-même. Au-delà, on ne refuse pas la reprise : on exige qu'elle soit
# demandée, ce qui est exactement la différence entre un geste et une boucle.
GL_REPRISES_MAX="${MAESTRO_REPRISES_MAX:-2}"
[ "$GL_REPRISES_MAX" -ge 0 ] 2>/dev/null || GL_REPRISES_MAX=2

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

# gl_current_user -> imprime le username de l'utilisateur glab authentifié (pour l'auto-assignation
# du ticket par /ticket-start). Parse `glab api user` en shell pur (grep/sed) — pas de dépendance à
# jq/python, et entièrement couvert par l'allowlist `bash scripts/gitlab/lib.sh:*` (docs/10 §7.1),
# pour que /ticket-start ne déclenche aucun prompt de permission sur cette étape.
gl_current_user() {
  local u
  u="$(glab api user 2>/dev/null | grep -o '"username":"[^"]*"' | head -1 | sed 's/.*"username":"//; s/"$//')"
  if [ -z "$u" ]; then
    echo "gl_current_user : username introuvable (glab authentifié ? cf. require)" >&2
    return 1
  fi
  printf '%s\n' "$u"
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

# --- Cycle de vie : slugs, libellés, GID de labels ----------------------------------------------
# Voir le CONTRAT DE SURFACE en tête de fichier : slug = stockage (suffixe du label), libellé =
# surface (ce que rendent tous les helpers). Ces trois fonctions sont le seul endroit du dépôt qui
# connaît la correspondance entre les deux.

# gl_workflow_slug <valeur> -> normalise une valeur de cycle de vie en SLUG (« En cours » →
# « en-cours », « en-cours » → « en-cours »). Accepte donc indifféremment le libellé ou le slug,
# c'est la porte d'entrée de toute écriture. Code 1 sur une valeur inconnue, avec la liste.
gl_workflow_slug() {
  local v="$1"
  if [ -z "$v" ]; then echo "gl_workflow_slug : valeur manquante" >&2; return 2; fi
  case "$v" in
    "À faire"|"a-faire"|"À FAIRE")     printf 'a-faire\n' ;;
    "En cours"|"en-cours")             printf 'en-cours\n' ;;
    "En revue"|"en-revue")             printf 'en-revue\n' ;;
    "Terminé"|"termine"|"Termine")     printf 'termine\n' ;;
    "Abandonné"|"abandonne"|"Abandonne") printf 'abandonne\n' ;;
    "Doublon"|"doublon")               printf 'doublon\n' ;;
    *)
      echo "Valeur de cycle de vie inconnue : « $v »." >&2
      echo "Attendu : À faire | En cours | En revue | Terminé | Abandonné | Doublon" >&2
      echo "  (les slugs a-faire|en-cours|en-revue|termine|abandonne|doublon sont acceptés aussi)" >&2
      return 1 ;;
  esac
}

# gl_workflow_label <slug> -> chemin inverse : rend le LIBELLÉ lisible d'un slug. C'est ce que
# toutes les lectures appliquent avant de rendre la main, pour que le slug ne sorte jamais d'ici.
# Une valeur inconnue est rendue TELLE QUELLE (pas d'erreur) : une lecture ne doit pas échouer sur
# un label exotique posé à la main — l'appelant verra passer la valeur brute et pourra la signaler.
gl_workflow_label() {
  case "$1" in
    a-faire)   printf 'À faire\n' ;;
    en-cours)  printf 'En cours\n' ;;
    en-revue)  printf 'En revue\n' ;;
    termine)   printf 'Terminé\n' ;;
    abandonne) printf 'Abandonné\n' ;;
    doublon)   printf 'Doublon\n' ;;
    *)         printf '%s\n' "$1" ;;
  esac
}

# gl_awk_workflow -> imprime un fragment awk à CONCATÉNER en tête d'un programme awk qui doit lire
# le cycle de vie dans un nœud JSON de work item. Définit une seule fonction :
#     wf_libelle(node) -> le LIBELLÉ du label workflow:: porté par le nœud, « - » si absent.
# Passé par substitution plutôt que recopié dans chaque projection (gl_backlog_table,
# gl_milestone_issues) : la correspondance slug→libellé du contrat n'existe ainsi qu'à deux
# endroits, ici et dans gl_workflow_label, et les deux se lisent côte à côte.
# Le scope voyage par -v WF_SCOPE (cf. les appelants) pour rester cohérent avec GL_WORKFLOW_SCOPE
# au lieu de figer « workflow » dans le motif.
# NB : si un ticket porte PLUSIEURS labels du scope (dérive possible sur Free, cf. contrat), c'est
# le premier rencontré qui est rendu — la détection de cette dérive est le rôle de doctor.sh.
gl_awk_workflow() {
  cat <<'AWK'
function wf_libelle(node,   pre, s) {
  pre = "\"" WF_SCOPE "::"
  if (!match(node, pre "[a-z-]+\"")) return "-"
  s = substr(node, RSTART + length(pre), RLENGTH - length(pre) - 1)
  if (s == "a-faire")   return "À faire"
  if (s == "en-cours")  return "En cours"
  if (s == "en-revue")  return "En revue"
  if (s == "termine")   return "Terminé"
  if (s == "abandonne") return "Abandonné"
  if (s == "doublon")   return "Doublon"
  return s
}
AWK
}

# gl_workflow_gids -> imprime « <slug><TAB><gid> » pour les six labels du scope, dérivés par NOM en
# UNE lecture (aucun ID codé en dur, cf. contrat en tête de fichier). C'est la brique qui permet à
# une pose d'ajouter la cible ET de retirer les cinq autres dans le même appel : sans la liste
# complète, on ne saurait pas quoi retirer.
gl_workflow_gids() {
  local raw rows
  raw="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { labels(searchTerm:"'"$GL_WORKFLOW_SCOPE"'::") { nodes { id title } } } }')" || return 1
  # Ordre garanti par la requête : id puis title. On ne retient que les labels du scope exact —
  # `searchTerm` est une recherche floue, elle pourrait ramener « anti-workflow::x ».
  rows="$(printf '%s' "$raw" | grep -o '"id":"gid://gitlab/[A-Za-z]*Label/[0-9]\+","title":"'"$GL_WORKFLOW_SCOPE"'::[a-z-]*"' \
         | sed 's|.*"id":"\(gid://gitlab/[A-Za-z]*Label/[0-9]*\)","title":"'"$GL_WORKFLOW_SCOPE"'::\([a-z-]*\)"|\2\t\1|')"
  if [ -z "$rows" ]; then
    echo "Aucun label « $GL_WORKFLOW_SCOPE::* » dans $GL_PROJECT — provisionner d'abord : bash scripts/gitlab/bootstrap.sh" >&2
    return 1
  fi
  printf '%s\n' "$rows"
}

# --- Actions ------------------------------------------------------------------------------------
# gl_set_workflow <iid> <valeur> -> pose le cycle de vie du ticket par les labels workflow::.
# <valeur> accepte le libellé (« En cours ») comme le slug (« en-cours »).
# EXCLUSION MUTUELLE : la cible est ajoutée et les cinq autres retirés dans la MÊME mutation —
# l'exclusion des labels scopés étant Premium, rien côté GitLab ne l'assurerait à notre place
# (cf. contrat en tête de fichier). Idempotent : reposer la valeur déjà présente ne change rien.
gl_set_workflow() {
  local iid="$1" valeur="$2"
  if [ -z "$iid" ] || [ -z "$valeur" ]; then echo "usage: gl_set_workflow <iid> <valeur>" >&2; return 2; fi
  local slug wiid gids cible retraits out
  slug="$(gl_workflow_slug "$valeur")" || return 1
  wiid="$(gl_workitem_gid "$iid")"     || return 1
  gids="$(gl_workflow_gids)"           || return 1

  cible="$(printf '%s\n' "$gids" | awk -F '\t' -v s="$slug" '$1 == s { print $2; exit }')"
  if [ -z "$cible" ]; then
    echo "Label « $GL_WORKFLOW_SCOPE::$slug » absent de $GL_PROJECT — provisionner : bash scripts/gitlab/bootstrap.sh" >&2
    return 1
  fi
  # Les cinq autres, en liste GraphQL. removeLabelIds sur un label non porté est sans effet côté
  # GitLab : on peut donc toujours les retirer tous, sans lire l'état courant du ticket.
  retraits="$(printf '%s\n' "$gids" | awk -F '\t' -v s="$slug" '
    $1 != s { printf "%s\"%s\"", (n++ ? "," : ""), $2 }')"

  out="$(glab api graphql -f query='mutation { workItemUpdate(input:{ id:"'"$wiid"'", labelsWidget:{ addLabelIds:["'"$cible"'"], removeLabelIds:['"$retraits"'] } }){ errors } }' 2>&1)"
  case "$out" in
    *'"errors":[]'*) printf 'Cycle de vie de #%s → « %s »\n' "$iid" "$(gl_workflow_label "$slug")" ;;
    *) echo "Échec de la pose du cycle de vie sur #$iid : $out" >&2; return 1 ;;
  esac
}

# gl_reconcile_workflow [--check] [<iid>…] -> pose « Terminé » sur les tickets dont le travail est
# SOLDÉ mais dont le cycle de vie est resté ACTIF (#275). C'est la réparation de la dérive que
# doctor.sh se contentait de diagnostiquer (« ticket fermé mais son état est encore actif ») : le
# merge FERME le ticket mais ne touche à aucun label, et depuis #207 seul /branch-cleanup — un geste
# manuel — posait « Terminé ». Entre les deux, un ticket mergé s'affiche « En revue » indéfiniment.
#
# Deux modes, même règle :
#   • avec des <iid>  : ne traite que ceux-là, une lecture par ticket. C'est ainsi que le ramassage
#                       des worktrees s'y branche (worktree.sh gc), sur un verdict DÉJÀ rendu. Ce
#                       mode FAIT CONFIANCE à l'appelant sur le fait que le travail est soldé — il
#                       ne revérifie pas que le ticket est fermé, `gl_worktree_done` rendant « fini »
#                       aussi sur une MR mergée dont le ticket est resté ouvert (MR sans `Closes`) ;
#   • sans argument   : balaie le backlog FERMÉ en UNE lecture (les labels y sont déjà) et répare
#                       tout ce qui traîne — le verbe explicite, utilisable seul. Périmètre : les
#                       100 derniers fermés (le `first: 100` de gl_backlog), donc exactement celui
#                       du diagnostic §4b de doctor.sh — ce qu'il signale, ce verbe le répare, ni
#                       plus ni moins. Un ticket fermé de longue date et resté actif lui échappe
#                       comme il échappe déjà au diagnostic.
#
# LA RÈGLE ET SON SEUL PIÈGE : on ne pose que sur un cycle de vie ACTIF (« À faire »/« En cours »/
# « En revue ») ou ABSENT. Un ticket déjà « Abandonné » ou « Doublon » n'est JAMAIS écrasé — un
# ticket fermé sans avoir été réalisé est fermé quand même, et `gl_worktree_done` rend « fini » pour
# lui exactement comme pour un ticket livré (cf. son en-tête). Sans ce filtre, ramasser le worktree
# d'un ticket abandonné le déclarerait « Terminé », et la dérive réparée en créerait une autre.
# « Terminé » déjà posé est également sauté : c'est le cas nominal en régime établi, et le sauter
# évite une écriture par passage de `gc`.
#
# Best-effort par construction : un ticket illisible est signalé et n'arrête pas les suivants. Le
# code de retour vaut 1 s'il en reste un en échec, mais aucun appelant ne doit en faire un motif de
# blocage (même statut que gl_sync_main, docs/10 §9.3).
gl_reconcile_workflow() {
  local check=0
  while [ "${1:-}" = "--check" ]; do check=1; shift; done
  local iids="$*" statut echecs=0 poses=0 sautes=0

  if [ -z "$iids" ]; then
    # Balayage : les labels sont DANS le backlog fermé, donc aucune lecture par ticket. Même filtre
    # que doctor.sh §4b — les trois valeurs actives, et elles seules.
    local ferme
    ferme="$(gl_backlog closed | sed 's/{"iid":/\n{"iid":/g')" || return 1
    iids="$(printf '%s\n' "$ferme" \
      | grep -E '"'"$GL_WORKFLOW_SCOPE"'::(a-faire|en-cours|en-revue)"' \
      | grep -o '"iid":"[0-9]*"' | grep -o '[0-9]*')"
    if [ -z "$iids" ]; then
      printf 'Aucun ticket fermé au cycle de vie resté actif — rien à réconcilier.\n'
      return 0
    fi
    # Le filtre a déjà tranché : ces tickets portent un label actif, la relecture serait redondante.
    local iid
    for iid in $iids; do
      if [ "$check" = 1 ]; then
        printf '  → #%s passerait à « Terminé »\n' "$iid"; poses=$((poses + 1)); continue
      fi
      if gl_set_workflow "$iid" "Terminé"; then poses=$((poses + 1)); else echecs=$((echecs + 1)); fi
    done
  else
    local iid brut
    for iid in $iids; do
      # Une lecture par ticket : c'est le prix du filtre ci-dessus, et il est payé sur des tickets
      # déjà identifiés par l'appelant (0 ou 1 par passage de `gc`), jamais sur une découverte.
      # Capture PUIS découpe : `gl_issue_owner | cut` rendrait le code de `cut`, toujours 0 —
      # un ticket illisible passerait alors pour un ticket sans cycle de vie, donc à poser.
      if ! brut="$(gl_issue_owner "$iid")"; then
        echecs=$((echecs + 1)); continue
      fi
      statut="${brut%%$'\t'*}"
      case "$statut" in
        'Abandonné'|'Doublon'|'Terminé') sautes=$((sautes + 1)); continue ;;
      esac
      if [ "$check" = 1 ]; then
        printf '  → #%s passerait de « %s » à « Terminé »\n' "$iid" "${statut:-aucun}"
        poses=$((poses + 1)); continue
      fi
      if gl_set_workflow "$iid" "Terminé"; then poses=$((poses + 1)); else echecs=$((echecs + 1)); fi
    done
  fi

  [ "$sautes" -gt 0 ] && printf '%s ticket(s) déjà à un état final — inchangé(s).\n' "$sautes"
  [ "$echecs" -gt 0 ] && { printf 'Réconciliation : %s ticket(s) en échec.\n' "$echecs" >&2; return 1; }
  return 0
}

# --- Lecture / reporting ------------------------------------------------------------------------
# gl_backlog [state] -> JSON des work items du projet avec leurs labels (dont le cycle de vie,
# porté par workflow::*) et leurs assignés. state ∈ opened (défaut) | closed | all. Requête
# canonique du backlog, source unique de /backlog comme des futurs outils (Control Tower, agents).
# La mise en forme (regroupement par cycle de vie) est laissée à l'appelant — jq n'est pas requis.
# Depuis #209 le cycle de vie est DANS le widget Labels, déjà demandé : la bascule a RETIRÉ le
# widget de statut de cette requête plutôt que d'en ajouter un.
gl_backlog() {
  local state="${1:-opened}"
  case "$state" in opened|closed|all) ;; *) echo "state invalide : $state (opened|closed|all)" >&2; return 2 ;; esac
  gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { workItems(state: '"$state"', first: 100) { nodes { iid title widgets { ... on WorkItemWidgetLabels { labels { nodes { title } } } ... on WorkItemWidgetAssignees { assignees { nodes { username } } } } } } } }'
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
# (prio/agent/assigné absent) est rendu « - ». Le `statut`, lui, est le LIBELLÉ du cycle de vie
# (« À faire », « En revue » — jamais le slug du label : contrat de surface en tête de fichier),
# et vaut « - » si le ticket ne porte aucun label workflow::.
#
# Projection en awk pur (pas de jq requis) : le parsing suit la même approche grep/sed/awk que le
# reste de ce fichier, donc la commande fonctionne à l'identique que jq soit installé ou non.
gl_backlog_table() {
  local state="${1:-opened}" json
  json="$(gl_backlog "$state")" || return 1
  printf '# iid\tstatut\tprio\tagent\tassigne\ttitre\n'
  printf '%s\n' "$json" | awk -v WF_SCOPE="$GL_WORKFLOW_SCOPE" "$(gl_awk_workflow)"'
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

        status = wf_libelle(node)

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

# gl_issue_owner <iid> -> imprime « <statut><TAB><assignés> » : le LIBELLÉ du cycle de vie (lu dans
# le label workflow::, cf. contrat de surface en tête de fichier) et les usernames des assignés
# séparés par des virgules. Un champ vide signifie « non posé » pour le cycle de vie, « personne »
# (ticket LIBRE) pour les assignés. Une seule lecture GraphQL, parsing shell pur (pas de jq) —
# même approche que gl_backlog_table, en ciblant un seul ticket.
# Sert l'ANTI-COLLISION du travail à plusieurs (#159) : `glab issue view` expose bien les labels,
# mais pas de quoi décider d'un coup d'œil, donc gl_start_brief s'appuie là-dessus pour dire si un
# ticket est déjà pris — et /ticket-start pour refuser de le démarrer (gl_begin REMPLACE la liste
# des assignés : démarrer un ticket pris le retirerait en silence à son propriétaire).
gl_issue_owner() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_issue_owner <iid>" >&2; return 2; fi
  local raw statut assignes
  raw="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { workItems(iids:["'"$iid"'"]) { nodes { widgets { ... on WorkItemWidgetLabels { labels { nodes { title } } } ... on WorkItemWidgetAssignees { assignees { nodes { username } } } } } } } }')" || return 1
  if [ -z "$raw" ]; then echo "gl_issue_owner : lecture du ticket #$iid impossible" >&2; return 1; fi
  # Ticket inexistant : la requête réussit mais rend « "workItems":{"nodes":[]} ». Sans ce
  # garde-fou, la fonction imprimerait deux champs vides — que l'appelant lirait comme « statut non
  # posé, ticket libre ». On cible bien le nœud workItems : « "nodes":[] » tout court se produit
  # aussi, légitimement, sur un ticket sans assigné.
  case "$raw" in
    *'"workItems":{"nodes":[]}'*) echo "gl_issue_owner : ticket #$iid introuvable dans $GL_PROJECT" >&2; return 1 ;;
    # Projet inconnu (ou droits insuffisants) : GraphQL répond « "project":null » avec un code 0.
    # Sans ce cas, la fonction imprimerait deux champs vides — lus par l'appelant comme « ticket
    # libre », c'est-à-dire un feu vert (gl_close_guard, gl_start_brief). Mieux vaut l'erreur.
    *'"project":null'*) echo "gl_issue_owner : projet $GL_PROJECT illisible (inconnu ou droits insuffisants)" >&2; return 1 ;;
  esac
  # Cycle de vie : slug du label workflow:: → libellé. Vide si le ticket n'en porte aucun (dérive
  # possible sur Free, où l'exclusion n'est pas garantie : c'est doctor.sh qui la traque).
  statut="$(printf '%s' "$raw" | grep -o '"'"$GL_WORKFLOW_SCOPE"'::[a-z-]*"' | head -1 \
            | sed 's/^"'"$GL_WORKFLOW_SCOPE"':://; s/"$//')"
  [ -n "$statut" ] && statut="$(gl_workflow_label "$statut")"
  assignes="$(printf '%s' "$raw" | grep -o '"username":"[^"]*"' | sed 's/.*"username":"//; s/"$//' \
              | awk '{ out = (NR == 1 ? $0 : out "," $0) } END { if (NR) print out }')"
  printf '%s\t%s\n' "$statut" "$assignes"
}

# gl_issue_taken <iid> [moi] -> code 0 (et message sur stdout) si le ticket est DÉJÀ PRIS PAR
# QUELQU'UN D'AUTRE : statut « En cours » et assigné à un username différent de l'utilisateur glab
# courant (résolu par gl_current_user si l'argument est absent). Code 1 sinon (libre, à moi, ou
# statut différent). Prédicat volontairement étroit — c'est la seule situation où deux personnes se
# marchent dessus ; un ticket « En revue »/« Terminé » assigné à un tiers relève d'un autre sujet.
gl_issue_taken() {
  local iid="$1" moi="${2:-}"
  if [ -z "$iid" ]; then echo "usage: gl_issue_taken <iid> [username]" >&2; return 2; fi
  local owner statut assignes
  owner="$(gl_issue_owner "$iid")" || return 1
  IFS=$'\t' read -r statut assignes <<< "$owner"
  [ "$statut" = "En cours" ] || return 1
  [ -n "$assignes" ] || return 1
  [ -n "$moi" ] || moi="$(gl_current_user 2>/dev/null)"
  # Appartenance exacte à la liste (les virgules encadrantes évitent qu'« alice » matche
  # « alice-bot ») : si je suis dans les assignés, le ticket est à moi, pas « pris ».
  if [ -n "$moi" ] && printf '%s' ",$assignes," | grep -q ",$moi,"; then return 1; fi
  printf '%s\n' "$assignes"
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
  printf '%s\n' "$raw" | gl_issue_brief_render "$iid"
}

# gl_issue_brief_render <iid> — cœur de gl_issue_brief, séparé pour être rejoué sur un ticket DÉJÀ
# LU (stdin = sortie brute de `glab issue view`) : gl_start_brief s'en sert pour ne lire le ticket
# qu'une seule fois et enchaîner toutes les projections sur le même texte.
# Deux formats de « critères d'acceptation » coexistent dans le backlog : les tickets récents
# (issue templates) posent un titre de section « ## Critères d'acceptation » suivi d'une liste
# « - [ ] … » ; les tickets plus anciens l'écrivent en paragraphe inline (« Critères d'acceptation :
# … »). Le mot « acceptation » n'a pas d'accent → on l'utilise comme ancre robuste aux deux formes
# (avec ou sans accent sur « Critères »). En forme titre on capture les lignes suivantes jusqu'au
# prochain titre ; en forme inline on n'imprime que la ligne elle-même.
gl_issue_brief_render() {
  local iid="$1"
  awk -v iid="$iid" '
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

# --- Milestone de phase ---------------------------------------------------------------------------
# gl_current_milestone -> imprime le TITRE du milestone de la « phase courante » : le milestone
# ACTIF le plus ancien (tri par échéance croissante) qui n'est pas déjà soldé — c'est-à-dire ayant
# au moins un ticket ouvert, OU aucun ticket (phase pas encore entamée). Un milestone actif dont
# tous les tickets sont fermés est SAUTÉ : la phase est finie, seule sa fermeture — décision
# humaine (jalon go/no-go de la roadmap) — reste à faire, et doctor.sh la suggère. La règle est
# volontairement indépendante des dates prévisionnelles des milestones : le réel peut être en
# avance sur elles. Sortie vide + code 1 si aucun candidat (aucun milestone actif, ou tous
# soldés) ; /ticket-create omet alors simplement --milestone à la création.
gl_current_milestone() {
  local raw title
  raw="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { milestones(state: active, sort: DUE_DATE_ASC, first: 20) { nodes { title stats { totalIssuesCount closedIssuesCount } } } } }')" || return 1
  title="$(printf '%s' "$raw" | awk '
    {
      n = split($0, parts, /\{"title":"/)
      for (i = 2; i <= n; i++) {
        node = parts[i]
        t = node; sub(/".*$/, "", t)
        gsub(/\\u0026/, "\\&", t); gsub(/\\u003e/, ">", t); gsub(/\\u003c/, "<", t)
        total = 0; closed = 0
        if (match(node, /"totalIssuesCount":[0-9]+/))  { m = substr(node, RSTART, RLENGTH); sub(/.*:/, "", m); total = m + 0 }
        if (match(node, /"closedIssuesCount":[0-9]+/)) { m = substr(node, RSTART, RLENGTH); sub(/.*:/, "", m); closed = m + 0 }
        if (total == 0 || closed < total) { print t; exit }
      }
    }
  ')"
  if [ -z "$title" ]; then
    echo "gl_current_milestone : aucun milestone actif non soldé (rien à poser)" >&2
    return 1
  fi
  printf '%s\n' "$title"
}

# gl_milestones -> table plate des milestones du projet, du plus ancien au plus récent (tri par
# échéance croissante, comme gl_current_milestone). Sert à /milestone-presentation : choisir le
# milestone à présenter, et lever une ambiguïté quand l'utilisateur donne un fragment de titre.
#
# Sortie TSV (en-tête préfixée « # » ignorable par les consommateurs machine) :
#     titre <TAB> etat <TAB> debut <TAB> echeance <TAB> fermes <TAB> total
# `etat` vaut `active`/`closed` ; une date absente vaut « - ». Le titre vient EN PREMIER parce
# qu'il est la clé (c'est lui qu'on repasse à gl_milestone_issues), et en dernier viennent les
# compteurs, de largeur fixe.
gl_milestones() {
  local raw
  raw="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { milestones(sort: DUE_DATE_ASC, first: 50) { nodes { title state startDate dueDate stats { totalIssuesCount closedIssuesCount } } } } }')" || return 1
  printf '# titre\tetat\tdebut\techeance\tfermes\ttotal\n'
  printf '%s\n' "$raw" | awk '
    {
      n = split($0, parts, /\{"title":"/)
      for (i = 2; i <= n; i++) {
        node = parts[i]
        title = node; sub(/".*$/, "", title)
        gsub(/\\u0026/, "\\&", title); gsub(/\\u003e/, ">", title); gsub(/\\u003c/, "<", title)

        etat = "-"
        if (match(node, /"state":"[^"]*"/)) {
          m = substr(node, RSTART, RLENGTH); sub(/.*:"/, "", m); sub(/"$/, "", m); etat = m
        }

        debut = "-"; echeance = "-"
        if (match(node, /"startDate":"[0-9-]+"/)) { m = substr(node, RSTART, RLENGTH); sub(/.*:"/, "", m); sub(/"$/, "", m); debut = m }
        if (match(node, /"dueDate":"[0-9-]+"/))   { m = substr(node, RSTART, RLENGTH); sub(/.*:"/, "", m); sub(/"$/, "", m); echeance = m }

        total = 0; fermes = 0
        if (match(node, /"totalIssuesCount":[0-9]+/))  { m = substr(node, RSTART, RLENGTH); sub(/.*:/, "", m); total = m + 0 }
        if (match(node, /"closedIssuesCount":[0-9]+/)) { m = substr(node, RSTART, RLENGTH); sub(/.*:/, "", m); fermes = m + 0 }

        printf "%s\t%s\t%s\t%s\t%d\t%d\n", title, etat, debut, echeance, fermes, total
      }
    }
  '
}

# gl_milestone_issues <titre-exact> -> table plate des tickets d'un milestone, même modèle compact
# que gl_backlog_table (une ligne par ticket, projection awk sans dépendance à jq).
#
# Le titre doit être EXACT (c'est le filtre `milestoneTitle` de l'API) : la résolution d'un
# fragment (« Phase 3 ») est le travail de l'appelant, via gl_milestones. Un titre inconnu ne
# lève pas d'erreur côté API — il rend simplement zéro ticket, d'où le garde-fou ci-dessous.
#
# Sortie TSV (en-tête préfixée « # » ignorable) :
#     iid <TAB> statut <TAB> type <TAB> agent <TAB> prio <TAB> titre
# `statut` est le LIBELLÉ du cycle de vie (À faire / En cours / En revue / Terminé / Abandonné /
# Doublon — lu dans le label workflow::, jamais son slug : contrat de surface en tête de fichier ;
# « - » si le ticket n'en porte aucun) ; `type`/`agent`/`prio` sont le suffixe nu du label (« feature »,
# « dev », « moyenne ») ; un champ absent vaut « - ». Les tickets sortent du plus récent au plus
# ancien (ordre de l'API) ; l'appelant regroupe et trie selon sa présentation.
gl_milestone_issues() {
  local title="$1"
  if [ -z "$title" ]; then echo "usage: gl_milestone_issues <titre-exact-du-milestone>" >&2; return 2; fi
  # Le titre voyage dans la requête GraphQL : on échappe guillemets et antislashs, sans quoi un
  # titre exotique casserait la requête (les titres de phase n'en ont pas, mais le helper est
  # générique — il sera rappelé sur des projets provisionnés par bootstrap.sh).
  local escaped
  escaped="$(printf '%s' "$title" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  local raw
  raw="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { workItems(milestoneTitle:["'"$escaped"'"], first: 100) { nodes { iid title state widgets { ... on WorkItemWidgetLabels { labels { nodes { title } } } } } } } }')" || return 1

  local rows
  rows="$(printf '%s\n' "$raw" | awk -v WF_SCOPE="$GL_WORKFLOW_SCOPE" "$(gl_awk_workflow)"'
    {
      n = split($0, parts, /\{"iid":"/)
      for (i = 2; i <= n; i++) {
        node = parts[i]
        match(node, /^[0-9]+/); iid = substr(node, RSTART, RLENGTH)

        # Le titre du TICKET est celui qui suit immédiatement son iid : on borne sur le champ
        # suivant (`state`) pour ne pas ramasser un `"title"` de label plus loin dans le nœud.
        titre = "-"
        if (match(node, /","title":"/)) {
          rest = substr(node, RSTART + RLENGTH)
          if (match(rest, /","state":"/)) titre = substr(rest, 1, RSTART - 1)
        }
        gsub(/\\u0026/, "\\&", titre); gsub(/\\u003e/, ">", titre); gsub(/\\u003c/, "<", titre)

        statut = wf_libelle(node)

        type = "-"; agent = "-"; prio = "-"
        if (match(node, /type::[a-z]+/))  type  = substr(node, RSTART + 6, RLENGTH - 6)
        if (match(node, /agent::[a-z]+/)) agent = substr(node, RSTART + 7, RLENGTH - 7)
        if (match(node, /prio::[a-z]+/))  prio  = substr(node, RSTART + 6, RLENGTH - 6)

        printf "%s\t%s\t%s\t%s\t%s\t%s\n", iid, statut, type, agent, prio, titre
      }
    }
  ')"

  if [ -z "$rows" ]; then
    echo "gl_milestone_issues : aucun ticket pour le milestone « $title » (titre exact attendu — cf. lib.sh milestones)" >&2
    return 1
  fi
  printf '# iid\tstatut\ttype\tagent\tprio\ttitre\n'
  printf '%s\n' "$rows"
}

# --- Sous-tickets (découpage parent / lots) -------------------------------------------------------
# Convention (docs/10-workflow-git.md §5.1) : un besoin qui dépasse ~1 session de travail est porté
# par un ticket PARENT de suivi dont la description contient une section « ## Sous-tickets » :
# checklist ORDONNÉE « - [ ] #<iid> — <titre> » (ordre de réalisation, lot final tests+doc).
# Chaque sous-ticket commence sa description par « Sous-ticket de #<parent> » (marqueur parsé par
# gl_parent_of) et est lié au parent via un issue link « relates to » (gl_issue_link).
#
# MARQUEUR « (parallèle) » (ticket #160) — un lot dont le titre de checklist se termine par
# « (parallèle) » déclare qu'il **ne dépend pas** des autres lots parallèles qui le précèdent :
# deux personnes peuvent les prendre en même temps sans que /ticket-start n'en bloque un. Le
# marqueur est FACULTATIF, et son absence conserve le comportement séquentiel d'origine. D'où la
# règle de blocage, appliquée par gl_start_brief et gl_subtickets_startables :
#   un lot précédent non livré (ni « Terminé » ni « En revue ») bloque, SAUF si le lot visé ET ce
#   lot précédent portent tous deux le marqueur.
# Un lot NON marqué reste donc barré par tout ce qui le précède — c'est ce qui garde le lot final
# « tests + doc » derrière l'ensemble des lots, marqueurs compris.

# gl_issue_link <iid> <iid-cible> -> lie deux tickets du projet (issue link « relates to »).
# Idempotent : un lien déjà présent (409 « already assigned ») est traité comme un succès.
gl_issue_link() {
  local iid="$1" target="$2"
  if [ -z "$iid" ] || [ -z "$target" ]; then echo "usage: gl_issue_link <iid> <iid-cible>" >&2; return 2; fi
  local out
  # target_project_id voyage dans le CORPS de la requête : chemin BRUT ("groupe/projet"), pas
  # l'encodage %2F (réservé au chemin d'URL) — encodé, l'API répond « 404 Project Not Found ».
  out="$(glab api "projects/$(gl_project_enc)/issues/$iid/links" \
        -f target_project_id="$GL_PROJECT" -f target_issue_iid="$target" 2>&1)"
  case "$out" in
    *'"source_issue"'*)   printf 'Lien posé : #%s ↔ #%s\n' "$iid" "$target" ;;
    *'already assigned'*) printf 'Lien déjà présent : #%s ↔ #%s\n' "$iid" "$target" ;;
    *) echo "Échec du lien #$iid ↔ #$target : $out" >&2; return 1 ;;
  esac
}

# gl_parent_of <iid> -> imprime l'iid du ticket PARENT si <iid> est un sous-ticket (marqueur
# « Sous-ticket de #<parent> » dans sa description), rien (code 1) sinon.
gl_parent_of() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_parent_of <iid>" >&2; return 2; fi
  local raw
  raw="$(glab issue view "$iid" 2>/dev/null)" || { echo "Issue #$iid introuvable dans $GL_PROJECT" >&2; return 1; }
  printf '%s\n' "$raw" | grep -o 'Sous-ticket de #[0-9]\+' | head -1 | grep -o '[0-9]\+$'
}

# gl_subtickets <iid-parent> -> liste ORDONNÉE des sous-tickets déclarés dans la checklist
# « ## Sous-tickets » du parent, enrichie du cycle de vie (une seule requête backlog, pas N).
# Sortie TSV : iid <TAB> coche(x|-) <TAB> statut <TAB> par(∥|-) <TAB> titre  (ligne d'en-tête « # »
# à ignorer). La colonne « par » porte le marqueur « (parallèle) », retiré du titre pour que le
# marqueur ne soit lu qu'à un seul endroit.
# Code 1 si le ticket n'a pas de section « ## Sous-tickets » (ce n'est pas un ticket parent) —
# c'est le test utilisé par /ticket-start pour détecter un parent de suivi.
gl_subtickets() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_subtickets <iid-parent>" >&2; return 2; fi
  local raw rows
  raw="$(glab issue view "$iid" 2>/dev/null)" || { echo "Issue #$iid introuvable dans $GL_PROJECT" >&2; return 1; }
  rows="$(printf '%s\n' "$raw" | gl_subticket_rows)"
  if [ -z "$rows" ]; then
    echo "Pas de section « ## Sous-tickets » dans #$iid — pas un ticket parent." >&2
    return 1
  fi
  printf '%s\n' "$rows" | gl_subtickets_enrich
}

# gl_subticket_rows — cœur du parsing de gl_subtickets, séparé pour être rejoué sur un ticket DÉJÀ
# LU (stdin = sortie brute de `glab issue view`) : imprime les lignes de la checklist
# « ## Sous-tickets » en TSV brut (iid <TAB> coche(x|-) <TAB> par(∥|-) <TAB> titre), rien si la
# section est absente. gl_start_brief s'en sert pour détecter un parent de suivi sans relire le
# ticket. Le marqueur « (parallèle) » de fin de titre est extrait dans sa propre colonne : détection
# sur le titre minusculé et motif « parall[^)]* » plutôt qu'une classe [eè], parce qu'un awk orienté
# octets (mawk) ne sait pas faire tenir le « è » (2 octets en UTF-8) dans une classe de caractères.
gl_subticket_rows() {
  awk '
    insec {
      if ($0 ~ /^#+[ \t]/) { insec = 0; next }
      if ($0 ~ /^- \[[ xX]\] #[0-9]+/) {
        coche = ($0 ~ /^- \[[xX]\]/) ? "x" : "-"
        match($0, /#[0-9]+/)
        id = substr($0, RSTART + 1, RLENGTH - 1)
        titre = substr($0, RSTART + RLENGTH)
        sub(/^[-—–: \t]+/, "", titre)
        par = "-"
        if (tolower(titre) ~ /\([ \t]*parall[^)]*\)[ \t]*$/) {
          par = "∥"
          sub(/[ \t]*\([ \t]*[Pp]arall[^)]*\)[ \t]*$/, "", titre)
        }
        printf "%s\t%s\t%s\t%s\n", id, coche, par, titre
      }
      next
    }
    /^#+[ \t]+Sous-tickets/ { insec = 1 }
  '
}

# gl_subtickets_enrich — enrichit du CYCLE DE VIE les lignes TSV de gl_subticket_rows (stdin) et
# imprime la table finale « iid/coche/statut/par/titre » (une seule requête backlog, pas N).
# La colonne `statut` reprend telle quelle celle de gl_backlog_table : le LIBELLÉ (« À faire »),
# jamais le slug — c'est sur ces libellés que gl_subtickets_startables compare.
gl_subtickets_enrich() {
  local table siid coche par titre statut
  table="$(gl_backlog_table all)" || table=""
  printf '# iid\tcoche\tstatut\tpar\ttitre\n'
  while IFS=$'\t' read -r siid coche par titre; do
    statut="$(printf '%s\n' "$table" | awk -F '\t' -v id="$siid" '$1 == id { print $2; exit }')"
    printf '%s\t%s\t%s\t%s\t%s\n' "$siid" "$coche" "${statut:-?}" "$par" "$titre"
  done
}

# gl_subtickets_startables — stdin = table enrichie de gl_subtickets SANS son en-tête. Imprime les
# lots « À faire » que la règle de blocage laisse démarrer **maintenant** (« #<iid> — <titre> »,
# suffixé « (parallèle) » pour les lots marqués), rien s'il n'en reste aucun. C'est ce qui permet à
# /ticket-start de proposer, sur un parent, TOUS les lots démarrables et plus seulement le premier.
gl_subtickets_startables() {
  awk -F '\t' '
    { iid[NR] = $1; statut[NR] = $3; par[NR] = $4; titre[NR] = $5; n = NR }
    END {
      for (i = 1; i <= n; i++) {
        if (statut[i] != "À faire") continue
        bloque = 0
        for (j = 1; j < i; j++) {
          if (statut[j] == "Terminé" || statut[j] == "En revue") continue
          if (par[i] == "∥" && par[j] == "∥") continue
          bloque = 1
          break
        }
        if (!bloque) printf "  #%s — %s%s\n", iid[i], titre[i], (par[i] == "∥" ? " (parallèle)" : "")
      }
    }
  '
}

# --- Démarrage de ticket (/ticket-start : préflight + mutation groupée) --------------------------
# Deux helpers pour que /ticket-start remplace une dizaine d'allers-retours par deux (ticket #61) :
# gl_start_brief fait tout le préflight en UNE lecture du ticket, gl_begin pose assignation,
# statut et dates en UNE mutation. Les sous-commandes unitaires restent disponibles à côté.

# gl_start_brief <iid> -> préflight complet de /ticket-start en un appel et UNE SEULE lecture du
# ticket (un unique `glab issue view`, rejoué pour toutes les projections ; autres lectures : le
# statut/assigné du ticket, et la checklist du parent si <iid> est un sous-ticket). Vérifie les
# pré-requis (gl_require_glab), signale un arbre sale, puis imprime un bloc compact : titre/labels/
# critères (gl_issue_brief_render), la ligne « statut : … — libre / pris par … » (gl_issue_owner,
# avec ⚠ si le ticket est « En cours » chez quelqu'un d'autre), selon le cas marqueur sous-ticket
# (parent, rang « lot n/total », tests différés, contrôle du statut des lots précédents) ou
# checklist « ## Sous-tickets » (parent de suivi — qui ne porte ni branche ni code : pas de branche
# proposée dans ce cas), et enfin la branche proposée (gl_branch_prefix depuis le label type:: +
# gl_slug du titre).
# Informatif : les avertissements (ticket déjà pris, lot précédent non livré, label type:: absent)
# sont dans la sortie ; la décision — démarrer, rediriger, s'arrêter — reste à l'appelant. Code
# retour non nul seulement sur vrai échec (pré-requis, ticket introuvable) — l'arbre sale est
# depuis #181 un avertissement, pas un refus : le travail se fait dans le worktree du ticket.
gl_start_brief() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_start_brief <iid>" >&2; return 2; fi
  gl_require_glab || return 1
  # Arbre sale : AVERTISSEMENT, plus un refus (#181). Depuis que /ticket-start monte un worktree
  # par ticket, le travail ne se fait plus forcément ici : des changements non commités dans le
  # répertoire courant restent alors derrière nous, intacts et hors du chemin — les refuser
  # bloquerait le démarrage pour une saleté sans rapport avec le ticket. La décision revient à
  # l'appelant, qui seul connaît le verdict de `worktree.sh ensure` : bloquant si « ICI » (on
  # travaillerait DANS cet arbre), anodin si « WORKTREE ».
  local sales
  sales="$(git status --porcelain 2>/dev/null | grep -c .)" || sales=0
  if [ "${sales:-0}" -gt 0 ]; then
    printf '⚠ arbre de travail non propre : %s fichier(s) non commité(s) dans %s\n' \
      "$sales" "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" >&2
    printf '  Sans objet si un worktree est monté pour ce ticket ; à trancher sinon.\n' >&2
  fi
  local raw
  raw="$(glab issue view "$iid" 2>/dev/null)" || { echo "Issue #$iid introuvable dans $GL_PROJECT" >&2; return 1; }

  printf '%s\n' "$raw" | gl_issue_brief_render "$iid"

  # Cycle de vie + assigné (gl_issue_owner) : de quoi voir d'un coup d'œil si le ticket est LIBRE
  # ou DÉJÀ PRIS, sans avoir à lire les labels à la main. Avertissement explicite quand il est
  # « En cours » chez quelqu'un d'autre : /ticket-start doit s'arrêter là plutôt que de lui
  # retirer l'assignation en silence (gl_begin remplace la liste des assignés).
  local owner statut assignes moi
  owner="$(gl_issue_owner "$iid" 2>/dev/null)"
  IFS=$'\t' read -r statut assignes <<< "$owner"
  printf '\n'
  if [ -z "$owner" ]; then
    printf 'statut : ? — appartenance illisible (lecture GitLab en échec) : à vérifier à la main\n'
  elif [ -z "$assignes" ]; then
    printf 'statut : %s — libre (aucun assigné)\n' "${statut:-?}"
  else
    printf 'statut : %s — pris par : %s\n' "${statut:-?}" "$assignes"
    moi="$(gl_current_user 2>/dev/null)"
    if [ "$statut" = "En cours" ] && ! printf '%s' ",$assignes," | grep -q ",${moi:-__aucun__},"; then
      printf '⚠ déjà pris par %s — ne pas démarrer : le démarrer retirerait son assignation.\n' "$assignes"
      printf '  Reprise seulement sur demande explicite de la personne qui pilote.\n'
    fi
  fi

  # Parent de suivi ? (section « ## Sous-tickets » dans la description déjà lue)
  local rows
  rows="$(printf '%s\n' "$raw" | gl_subticket_rows)"
  if [ -n "$rows" ]; then
    local ptable startables
    ptable="$(printf '%s\n' "$rows" | gl_subtickets_enrich)"
    printf '\nparent de suivi — ne porte ni branche ni code ; rediriger vers un lot démarrable :\n'
    printf '%s\n' "$ptable"
    startables="$(printf '%s\n' "$ptable" | tail -n +2 | gl_subtickets_startables)"
    printf '\n'
    if [ -n "$startables" ]; then
      printf 'lots démarrables maintenant (les lots « parallèle » ne se bloquent pas entre eux) :\n'
      printf '%s\n' "$startables"
    else
      printf 'lots démarrables maintenant : aucun (tout est livré, en cours, ou bloqué par un lot précédent)\n'
    fi
    return 0
  fi

  # Sous-ticket ? (marqueur « Sous-ticket de #<parent> ») → rang de lot + contrôle des lots
  # précédents (ordre de la checklist du parent — ils doivent être livrés : « Terminé » ou
  # « En revue », les lots étant additifs et mergeables seuls depuis main ; ticket #63).
  local parent
  parent="$(printf '%s\n' "$raw" | grep -o 'Sous-ticket de #[0-9]\+' | head -1 | grep -o '[0-9]\+$')"
  if [ -n "$parent" ]; then
    local ptable total rank self_par blocked deferred
    ptable="$(gl_subtickets "$parent" 2>/dev/null | tail -n +2)"
    printf '\n'
    if [ -n "$ptable" ]; then
      total="$(printf '%s\n' "$ptable" | awk 'END { print NR }')"
      rank="$(printf '%s\n' "$ptable" | awk -F '\t' -v id="$iid" '$1 == id { print NR; exit }')"
      printf 'sous-ticket de #%s — lot %s/%s\n' "$parent" "${rank:-?}" "$total"
      # Marqueur « (parallèle) » du lot visé : il neutralise le blocage par les AUTRES lots
      # marqués qui le précèdent (voir la règle en tête de section). Un lot non marqué, lui,
      # reste barré par tout lot précédent non livré — marqueur compris.
      self_par="$(printf '%s\n' "$ptable" | awk -F '\t' -v id="$iid" '$1 == id { print $4; exit }')"
      [ "$self_par" = "∥" ] && printf 'lot marqué « parallèle » — indépendant des autres lots marqués du parent\n'
      blocked="$(printf '%s\n' "$ptable" | awk -F '\t' -v id="$iid" -v self_par="$self_par" '
        $1 == id { exit }
        $3 == "Terminé" || $3 == "En revue" { next }
        self_par == "∥" && $4 == "∥" { next }
        { printf "#%s (%s) ", $1, $3 }')"
      if [ -n "$blocked" ]; then
        printf 'lots précédents : ⚠ non livrés : %s— les terminer (au moins « En revue ») avant de démarrer ce lot\n' "$blocked"
      else
        printf 'lots précédents : OK (aucun lot bloquant — livrés ou marqués « parallèle »)\n'
      fi
    else
      printf 'sous-ticket de #%s (checklist du parent illisible — contrôler les lots précédents à la main)\n' "$parent"
    fi
    deferred="$(printf '%s\n' "$raw" | grep -o '[Tt]ests différés[^#]*#[0-9]\+' | head -1 | grep -o '[0-9]\+$')"
    if [ -n "$deferred" ]; then printf 'tests différés → #%s\n' "$deferred"; fi
  fi

  # Branche proposée : préfixe depuis le label type:: + slug du titre.
  local branche code
  branche="$(printf '%s\n' "$raw" | gl_branch_from_raw "$iid")"; code=$?
  if [ "$code" = 0 ]; then
    printf '\nbranche proposée : %s\n' "$branche"
  else
    printf '\nbranche proposée : %s (label type:: absent — préfixe à déduire : feat|fix|chore|docs)\n' "$branche"
  fi
}

# gl_begin <iid> [username] -> démarrage groupé du ticket : assignation (username auto-résolu si
# absent) + cycle de vie « En cours » + dates début/échéance (mêmes règles que gl_start_dates :
# début = aujourd'hui conservé si déjà posé — idempotent —, échéance = début + délai selon prio::)
# en UNE SEULE mutation workItemUpdate multi-widgets (assigneesWidget + labelsWidget +
# startAndDueDateWidget). Le GID du work item, la date de début existante et la priorité sont
# résolus en une lecture combinée. NB : assigneeIds REMPLACE la liste des assignés (sémantique
# voulue au démarrage : le ticket passe à celui qui le démarre) ; labelsWidget, lui, est ADDITIF
# (addLabelIds/removeLabelIds), ce qui préserve type::/agent::/prio:: — d'où le retrait explicite
# des cinq autres workflow::, comme dans gl_set_workflow.
gl_begin() {
  local iid="$1" user="${2:-}"
  if [ -z "$iid" ]; then echo "usage: gl_begin <iid> [username]" >&2; return 2; fi

  # Assigné : sans argument, `glab api user` donne username + id en un appel ; avec argument,
  # résolution GraphQL du username fourni.
  local ugid uraw
  if [ -z "$user" ]; then
    uraw="$(glab api user 2>/dev/null)"
    user="$(printf '%s' "$uraw" | grep -o '"username":"[^"]*"' | head -1 | sed 's/.*"username":"//; s/"$//')"
    ugid="$(printf '%s' "$uraw" | grep -o '"id":[0-9]*' | head -1 | sed 's/.*://')"
    ugid="${ugid:+gid://gitlab/User/$ugid}"
  else
    ugid="$(gl_graphql_read '{ user(username:"'"$user"'") { id } }' | grep -o 'gid://gitlab/User/[0-9]\+' | head -1)"
  fi
  if [ -z "$user" ] || [ -z "$ugid" ]; then
    echo "gl_begin : assigné irrésoluble (username « ${user:-?} ») — glab authentifié ? (cf. require)" >&2
    return 1
  fi

  # Lecture combinée du work item : GID + date de début déjà posée + label prio, en une requête.
  local wraw wiid start prio
  wraw="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { workItems(iids:["'"$iid"'"]) { nodes { id widgets { ... on WorkItemWidgetStartAndDueDate { startDate } ... on WorkItemWidgetLabels { labels { nodes { title } } } } } } } }')"
  wiid="$(printf '%s' "$wraw" | grep -o 'gid://gitlab/WorkItem/[0-9]\+' | head -1)"
  if [ -z "$wiid" ]; then echo "Work item #$iid introuvable dans $GL_PROJECT" >&2; return 1; fi
  start="$(printf '%s' "$wraw" | grep -o '"startDate":"[0-9-]*"' | head -1 | sed 's/.*"startDate":"//; s/"$//')"
  prio="$(printf '%s' "$wraw" | grep -o 'prio::[a-z]*' | head -1)"

  # Labels workflow:: par nom (même robustesse que gl_set_workflow : aucun GID en dur, et les cinq
  # autres retirés dans la même mutation) et calcul des dates (règles gl_start_dates).
  local gids cible retraits today delay due
  gids="$(gl_workflow_gids)" || return 1
  cible="$(printf '%s\n' "$gids" | awk -F '\t' '$1 == "en-cours" { print $2; exit }')"
  if [ -z "$cible" ]; then
    echo "gl_begin : label « $GL_WORKFLOW_SCOPE::en-cours » absent — provisionner : bash scripts/gitlab/bootstrap.sh" >&2
    return 1
  fi
  retraits="$(printf '%s\n' "$gids" | awk -F '\t' '
    $1 != "en-cours" { printf "%s\"%s\"", (n++ ? "," : ""), $2 }')"
  today="$(date +%F)"
  [ -z "$start" ] && start="$today"
  delay="$(gl_prio_delay "$prio")"
  due="$(date -d "$start +$delay days" +%F 2>/dev/null)"
  if [ -z "$due" ]; then echo "gl_begin : calcul de l'échéance impossible (commande date indisponible ?)" >&2; return 1; fi

  # La mutation groupée — appel direct à glab, jamais enveloppé de retry (cf. gl_graphql_read).
  local out
  out="$(glab api graphql -f query='mutation { workItemUpdate(input:{ id:"'"$wiid"'", assigneesWidget:{ assigneeIds:["'"$ugid"'"] }, labelsWidget:{ addLabelIds:["'"$cible"'"], removeLabelIds:['"$retraits"'] }, startAndDueDateWidget:{ startDate:"'"$start"'", dueDate:"'"$due"'" } }){ errors } }' 2>&1)"
  case "$out" in
    *'"errors":[]'*)
      printf '#%s démarré : assigné=%s, cycle de vie « En cours », début=%s, échéance=%s\n' "$iid" "$user" "$start" "$due"
      printf '  (priorité %s → échéance à +%s j)\n' "${prio:-prio::moyenne (défaut)}" "$delay"
      ;;
    *) echo "Échec du démarrage groupé de #$iid : $out" >&2; return 1 ;;
  esac
}

# --- Dates & time tracking ----------------------------------------------------------------------
# Renseignés automatiquement le long du cycle de vie (voir docs/10-workflow-git.md §3.3) :
#   • date de début + échéance  → posées par /ticket-start (gl_start_dates)
#   • temps passé               → proposé puis loggé par /ticket-finish (gl_log_time)
# Tout passe par la mutation workItemUpdate, comme gl_set_workflow (widgets startAndDueDate / timeTracking).

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

# --- Descriptions : lecture/écriture fidèles aux octets (ticket #141) ------------------------------
# Relire puis réécrire une description GitLab (cocher la checklist d'un parent, mettre à jour celle
# d'une MR) est un aller-retour à risque : il a corrompu #111 le 2026-07-22 en y repoussant du
# mojibake (« â€” » au lieu de « — », « Ã© » au lieu de « é »).
#
# La cause n'est PAS glab, qui émet du bon UTF-8 : c'est un consommateur qui re-décode les octets —
# typiquement `sys.stdin` de Python, en cp1252 sous Windows. Le piège précis :
#   PYTHONIOENCODING=utf-8 glab ... | python     <-- la variable s'applique à GLAB, pas à python :
#                                                    bash ne la propage pas au reste du pipeline,
#                                                    elle n'a donc AUCUN effet.
#   glab ... | PYTHONIOENCODING=utf-8 python     <-- correct (variable sur le lecteur)
#
# Ces helpers suppriment l'improvisation : tout reste en shell, qui est byte-transparent, donc les
# octets traversent inchangés quelles que soient la locale et la plateforme. Les commandes
# /ticket-start, /ticket-ship et /ticket-finish doivent passer par eux plutôt que d'inventer une
# lecture. Vérifier une correction d'encodage se fait par OCTETS, jamais à l'affichage : un terminal
# cp1252 réaffiche le mojibake de façon plausible (em-dash correct = e2 80 94).

# gl_json_string_field <champ> -> lit un JSON sur stdin, imprime la valeur DÉSÉCHAPPÉE du champ
# chaîne <champ>. Balayage awk sous LC_ALL=C : sûr en UTF-8, car les octets d'une séquence
# multi-octets valent tous >= 0x80 et ne peuvent donc jamais collisionner avec les délimiteurs
# ASCII (" et \) que l'on cherche. Le JSON de GitLab rend les non-ASCII en UTF-8 brut ; seuls
# \n, \", \\ et quelques &/</> (&/</>) sont échappés.
gl_json_string_field() {
  local champ="$1"
  if [ -z "$champ" ]; then echo "usage: gl_json_string_field <champ>" >&2; return 2; fi
  LC_ALL=C awk -v champ="$champ" '
    { buf = buf $0 }
    END {
      cle = "\"" champ "\":\""
      i = index(buf, cle)
      if (i == 0) exit 1
      p = i + length(cle); n = length(buf); out = ""
      while (p <= n) {
        c = substr(buf, p, 1)
        if (c == "\\") {
          e = substr(buf, p + 1, 1)
          if      (e == "n") out = out "\n"
          else if (e == "t") out = out "\t"
          else if (e == "r") out = out "\r"
          else if (e == "u") {
            hex = substr(buf, p + 2, 4)
            if      (hex == "0026") out = out "&"
            else if (hex == "003c") out = out "<"
            else if (hex == "003e") out = out ">"
            else                    out = out "\\u" hex   # échappement inconnu : laissé tel quel
            p += 6; continue
          }
          else out = out e            # \" \\ \/ … : le caractère littéral
          p += 2; continue
        }
        if (c == "\"") break
        out = out c
        p++
      }
      printf "%s", out
    }
  '
}

# gl_get_description <iid> -> la description du ticket <iid>, en UTF-8 intact, sur stdout.
gl_get_description() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_get_description <iid>" >&2; return 2; fi
  glab api "projects/$(gl_project_enc)/issues/$iid" 2>/dev/null | gl_json_string_field description
}

# gl_set_description <iid> <fichier> -> remplace la description du ticket <iid> par le contenu du
# fichier (UTF-8). L'écriture par argument est fidèle : bash est byte-transparent.
gl_set_description() {
  local iid="$1" fichier="$2"
  if [ -z "$iid" ] || [ -z "$fichier" ]; then echo "usage: gl_set_description <iid> <fichier>" >&2; return 2; fi
  if [ ! -f "$fichier" ]; then echo "fichier introuvable : $fichier" >&2; return 1; fi
  if ! glab issue update "$iid" --description "$(cat "$fichier")" >/dev/null 2>&1; then
    echo "Échec de la mise à jour de la description de #$iid" >&2; return 1
  fi
  printf 'Description de #%s mise à jour.\n' "$iid"
}

# gl_get_mr_description <mr> -> la description de la MR <mr>, en UTF-8 intact, sur stdout.
gl_get_mr_description() {
  local mr="$1"
  if [ -z "$mr" ]; then echo "usage: gl_get_mr_description <mr>" >&2; return 2; fi
  glab api "projects/$(gl_project_enc)/merge_requests/$mr" 2>/dev/null | gl_json_string_field description
}

# gl_set_mr_description <mr> <fichier> -> remplace la description de la MR <mr> par le fichier.
gl_set_mr_description() {
  local mr="$1" fichier="$2"
  if [ -z "$mr" ] || [ -z "$fichier" ]; then echo "usage: gl_set_mr_description <mr> <fichier>" >&2; return 2; fi
  if [ ! -f "$fichier" ]; then echo "fichier introuvable : $fichier" >&2; return 1; fi
  if ! glab mr update "$mr" --description "$(cat "$fichier")" >/dev/null 2>&1; then
    echo "Échec de la mise à jour de la description de !$mr" >&2; return 1
  fi
  printf 'Description de !%s mise à jour.\n' "$mr"
}

# gl_roundtrip_description <iid> -> validation REPRODUCTIBLE de la fidélité (ticket #141) : lit la
# description, la réécrit telle quelle, la relit, puis compare OCTET POUR OCTET. C'est la preuve
# qu'un aller-retour ne perd rien sur un texte à accents et em-dash. Sans effet de bord quand tout
# va bien : on réécrit un contenu identique. Code 0 si fidèle, 1 sinon.
gl_roundtrip_description() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_roundtrip_description <iid>" >&2; return 2; fi
  local avant apres taille
  avant="$(mktemp)" || return 1
  apres="$(mktemp)" || { rm -f "$avant"; return 1; }
  if ! gl_get_description "$iid" > "$avant" || [ ! -s "$avant" ]; then
    echo "Description vide ou illisible pour #$iid" >&2
    rm -f "$avant" "$apres"; return 1
  fi
  if ! gl_set_description "$iid" "$avant" >/dev/null; then
    rm -f "$avant" "$apres"; return 1
  fi
  if ! gl_get_description "$iid" > "$apres"; then
    rm -f "$avant" "$apres"; return 1
  fi
  if cmp -s "$avant" "$apres"; then
    taille="$(wc -c < "$avant" | tr -d ' ')"
    printf 'Aller-retour fidèle sur #%s : %s octets identiques.\n' "$iid" "$taille"
    rm -f "$avant" "$apres"; return 0
  fi
  echo "ALLER-RETOUR INFIDÈLE sur #$iid — les octets ont changé :" >&2
  cmp "$avant" "$apres" >&2
  rm -f "$avant" "$apres"; return 1
}

# --- Création : MR et notes, depuis un FICHIER (#233) ---------------------------------------------
# Pourquoi ces helpers existent alors que `glab mr create` et `glab issue note` sont DÉJÀ autorisés
# (docs/10-workflow-git.md §7.1) : la couche permissions de Claude Code découpe une commande sur ses
# SAUTS DE LIGNE et ne sait matcher aucune SUBSTITUTION `$(…)`. Or une description de MR fait par
# nature plusieurs lignes. La commande prescrite jusqu'ici par /ticket-finish était donc refusée
# telle quelle, et ses deux replis naturels l'étaient tout autant — `--description "$(cat f)"`, puis
# `D="$(cat f)"; glab mr create … "$D"`. 10 refus sur 8 sessions autonomes (#232, cause n°1), et
# toujours sur la DERNIÈRE action du ticket : tout est commité, rien ne le déclare.
#
# Le remède ne demande AUCUN droit nouveau — il rend matchable une commande déjà autorisée. Le texte
# voyage par FICHIER (écrit par l'outil Write, qui n'est pas une ligne de commande), l'appel reste
# plat et court, et c'est `Bash(bash scripts/gitlab/lib.sh:*)` qui le couvre. Le `$(cat …)` survit,
# mais à l'INTÉRIEUR du script, où aucune permission ne s'applique : c'est exactement le parti pris
# de gl_set_description / gl_set_mr_description, dont ceci est le pendant à la CRÉATION.

# gl_issue_title <iid> -> titre du ticket <iid>, UTF-8 intact, sur stdout. Même lecture REST +
# décodage octet-transparent que gl_get_description (le `title` du ticket précède celui du
# milestone dans la charge REST, donc la première occurrence est bien la bonne).
gl_issue_title() {
  local iid="$1" titre
  if [ -z "$iid" ]; then echo "usage: gl_issue_title <iid>" >&2; return 2; fi
  titre="$(glab api "projects/$(gl_project_enc)/issues/$iid" 2>/dev/null | gl_json_string_field title)"
  if [ -z "$titre" ]; then echo "gl_issue_title : titre de #$iid illisible" >&2; return 1; fi
  printf '%s\n' "$titre"
}

# gl_create_mr <iid> <fichier> [branche] -> ouvre la MR de <branche> (défaut : la branche courante)
# en DRAFT vers main, avec --remove-source-branch, le TITRE lu depuis le ticket et la DESCRIPTION
# lue depuis le fichier. Imprime l'URL de la MR en dernière ligne.
# IDEMPOTENT : si une MR ouverte existe déjà pour la branche, sa description est mise à jour au lieu
# d'échouer — /ticket-finish peut donc être rejoué (reprise de session, second passage après un
# commit de plus) sans que la deuxième passe casse.
# Ne merge ni ne dé-draft jamais : passer une MR en « prête » reste un geste explicite.
gl_create_mr() {
  local iid="$1" fichier="$2" branche="${3:-}" mr titre sortie
  if [ -z "$iid" ] || [ -z "$fichier" ]; then
    echo "usage: gl_create_mr <iid> <fichier> [branche]" >&2; return 2
  fi
  if [ ! -f "$fichier" ]; then echo "fichier introuvable : $fichier" >&2; return 1; fi
  if [ ! -s "$fichier" ]; then echo "gl_create_mr : $fichier est vide — description requise" >&2; return 1; fi
  [ -n "$branche" ] || branche="$(git branch --show-current 2>/dev/null)"
  if [ -z "$branche" ]; then echo "gl_create_mr : branche courante indéterminable" >&2; return 1; fi
  case "$branche" in
    main|master) echo "gl_create_mr : refus d'ouvrir une MR depuis « $branche »" >&2; return 1 ;;
  esac

  # Une MR ouverte porte déjà cette branche : on met sa description à jour, on ne recrée pas.
  if mr="$(gl_mr_iid "$branche" 2>/dev/null)" && [ -n "$mr" ]; then
    gl_set_mr_description "$mr" "$fichier" >/dev/null || return 1
    printf 'MR !%s déjà ouverte pour « %s » — description mise à jour (aucune MR recréée).\n' "$mr" "$branche"
    printf 'https://%s/%s/-/merge_requests/%s\n' "$(gl_host)" "$GL_PROJECT" "$mr"
    return 0
  fi

  titre="$(gl_issue_title "$iid")" || return 1

  # --yes : pas de confirmation interactive (une session autonome n'a personne pour répondre).
  if ! sortie="$(glab mr create --yes --draft --target-branch main --remove-source-branch \
      --source-branch "$branche" --title "$titre" --description "$(cat "$fichier")" 2>&1)"; then
    printf '%s\n' "$sortie" >&2
    echo "Échec de la création de la MR pour #$iid (branche « $branche »)" >&2
    return 1
  fi
  printf '%s\n' "$sortie"
}

# gl_issue_note <iid> <fichier> -> poste le contenu du fichier en COMMENTAIRE sur le ticket <iid>.
# Même raison d'être que gl_create_mr : `glab issue note -m "$(cat …)"` n'est pas matchable (#186).
gl_issue_note() {
  local iid="$1" fichier="$2"
  if [ -z "$iid" ] || [ -z "$fichier" ]; then echo "usage: gl_issue_note <iid> <fichier>" >&2; return 2; fi
  if [ ! -f "$fichier" ]; then echo "fichier introuvable : $fichier" >&2; return 1; fi
  if [ ! -s "$fichier" ]; then echo "gl_issue_note : $fichier est vide — rien à poster" >&2; return 1; fi
  if ! glab issue note "$iid" -m "$(cat "$fichier")" >/dev/null 2>&1; then
    echo "Échec de la publication du commentaire sur #$iid" >&2; return 1
  fi
  printf 'Commentaire posté sur #%s.\n' "$iid"
}

# --- Pipelines CI ---------------------------------------------------------------------------------
# Helpers REST pour le diagnostic de pipeline (/mr-fix — voir docs/10-workflow-git.md §8.3).
# Même parti pris que le reste du fichier : parsing shell pur (grep/sed/awk), pas de jq/python.

# gl_project_enc -> chemin du projet URL-encodé pour l'API REST ("groupe%2Fprojet").
# (Les helpers GraphQL utilisent GL_PROJECT tel quel ; REST exige l'encodage du "/".)
gl_project_enc() {
  printf '%s\n' "$GL_PROJECT" | sed 's,/,%2F,g'
}

# gl_project_id -> id NUMÉRIQUE du projet. Certains endpoints ne prennent pas le chemin encodé
# (POST /user/runners veut un `project_id` entier — cf. setup-runner.sh, #146).
gl_project_id() {
  local id
  id="$(glab api "projects/$(gl_project_enc)" 2>/dev/null | grep -o '"id":[0-9]\+' | head -1 | grep -o '[0-9]\+')"
  if [ -z "$id" ]; then
    echo "gl_project_id : projet $GL_PROJECT introuvable (glab authentifié ? cf. require)" >&2
    return 1
  fi
  printf '%s\n' "$id"
}

# gl_host -> hôte GitLab du dépôt, déduit du remote `origin` (défaut gitlab.com). Rien n'est codé
# en dur : le workflow doit tenir sur une instance auto-hébergée. Gère les deux formes d'URL
# (https://hote/groupe/projet et git@hote:groupe/projet).
gl_host() {
  local url racine
  racine="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  url="$(git -C "$racine" remote get-url origin 2>/dev/null)" || { echo "gitlab.com"; return 0; }
  case "$url" in
    *://*) url="${url#*://}"; url="${url#*@}"; printf '%s\n' "${url%%/*}" ;;
    *@*:*) url="${url#*@}"; printf '%s\n' "${url%%:*}" ;;
    *)     echo "gitlab.com" ;;
  esac
}

# gl_mr_pipelines <branche> -> JSON brut du dernier pipeline RATTACHÉ À LA MR ouverte de cette
# branche source (liste d'un élément), vide + code 1 si aucune MR ouverte n'en porte la branche.
# Raison d'être (#165) : la CI ne se déclenche plus que sur les Merge Requests, et un pipeline de
# MR est « détaché » — sa ref est refs/merge-requests/<iid>/head, pas le nom de la branche. Le
# filtre `pipelines?ref=<branche>` ne le voit donc pas ; l'endpoint des pipelines de la MR, si.
gl_mr_pipelines() {
  local branch="$1" iid
  if [ -z "$branch" ]; then echo "usage: gl_mr_pipelines <branche>" >&2; return 2; fi
  iid="$(glab api "projects/$(gl_project_enc)/merge_requests?source_branch=$branch&state=opened&per_page=1" 2>/dev/null \
    | grep -o '"iid":[0-9]*' | head -1 | sed 's/.*://')"
  [ -n "$iid" ] || return 1
  glab api "projects/$(gl_project_enc)/merge_requests/$iid/pipelines?per_page=1" 2>/dev/null
}

# gl_pipeline_latest <ref> -> dernier pipeline de la branche, en une ligne TSV :
#   id <TAB> status <TAB> sha <TAB> web_url
# Cherche d'abord du côté de la MR ouverte de la branche — le cas NORMAL depuis #165, et cet
# endpoint remonte AUSSI les pipelines de branche ou manuels du même sha, donc la même vue que le
# garde-fou de merge ; puis, à défaut de MR, les pipelines portant la ref (`main`, branche sans MR,
# déclenchement manuel `glab ci run -b`).
# Code 1 (et message) si aucun pipeline n'existe ni pour la MR, ni pour la ref.
gl_pipeline_latest() {
  local ref="$1"
  if [ -z "$ref" ]; then echo "usage: gl_pipeline_latest <ref>" >&2; return 2; fi
  local raw id status sha url
  raw="$(gl_mr_pipelines "$ref")" || raw=""
  if [ -z "$raw" ] || [ "$raw" = "[]" ]; then
    raw="$(glab api "projects/$(gl_project_enc)/pipelines?ref=$ref&per_page=1" 2>/dev/null)"
  fi
  if [ -z "$raw" ] || [ "$raw" = "[]" ]; then
    echo "Aucun pipeline pour « $ref » dans $GL_PROJECT (ni sur la ref, ni sur sa MR ouverte — la CI ne tourne que sur les MR, cf. docs/10 §8)" >&2
    return 1
  fi
  id="$(printf '%s' "$raw" | grep -o '"id":[0-9]*' | head -1 | sed 's/.*://')"
  status="$(printf '%s' "$raw" | grep -o '"status":"[a-z_]*"' | head -1 | sed 's/.*:"//; s/"//')"
  sha="$(printf '%s' "$raw" | grep -o '"sha":"[0-9a-f]*"' | head -1 | sed 's/.*:"//; s/"//')"
  url="$(printf '%s' "$raw" | grep -o '"web_url":"[^"]*/pipelines/[0-9]*"' | head -1 | sed 's/.*:"//; s/"//')"
  printf '%s\t%s\t%s\t%s\n' "$id" "$status" "$sha" "$url"
}

# gl_pipeline_status <pipeline-id> -> imprime le statut courant du pipeline (created|pending|
# running|success|failed|canceled|skipped|manual…). Le premier "status" du JSON détaillé est
# celui du pipeline lui-même (les objets imbriqués — user, commit — viennent après).
gl_pipeline_status() {
  local pid="$1"
  if [ -z "$pid" ]; then echo "usage: gl_pipeline_status <pipeline-id>" >&2; return 2; fi
  local status
  status="$(glab api "projects/$(gl_project_enc)/pipelines/$pid" 2>/dev/null \
    | grep -o '"status":"[a-z_]*"' | head -1 | sed 's/.*:"//; s/"//')"
  if [ -z "$status" ]; then echo "Pipeline $pid introuvable dans $GL_PROJECT" >&2; return 1; fi
  printf '%s\n' "$status"
}

# gl_pipeline_failed_jobs <pipeline-id> -> jobs en échec du pipeline, une ligne TSV par job :
#   id <TAB> name <TAB> stage <TAB> failure_reason
# S'appuie sur le filtre serveur `scope[]=failed` (seuls les jobs rouges reviennent) et sur
# l'ordre stable des premiers champs du JSON job ("id","status","stage","name") pour ne matcher
# que les objets job de tête — jamais les objets imbriqués (user/commit/pipeline), dont l'ordre
# de champs diffère. Le failure_reason est cherché dans le corps du job courant uniquement.
gl_pipeline_failed_jobs() {
  local pid="$1"
  if [ -z "$pid" ]; then echo "usage: gl_pipeline_failed_jobs <pipeline-id>" >&2; return 2; fi
  local raw
  raw="$(glab api "projects/$(gl_project_enc)/pipelines/$pid/jobs?scope[]=failed&per_page=50" 2>/dev/null)"
  if [ -z "$raw" ]; then echo "Jobs du pipeline $pid illisibles dans $GL_PROJECT" >&2; return 1; fi
  if [ "$raw" = "[]" ]; then echo "Aucun job en échec dans le pipeline $pid." >&2; return 0; fi
  printf '# id\tname\tstage\tfailure_reason\n'
  printf '%s' "$raw" | awk '
    {
      s = $0
      hdr = "\"id\":[0-9]+,\"status\":\"failed\",\"stage\":\"[^\"]*\",\"name\":\"[^\"]*\""
      while (match(s, hdr)) {
        seg = substr(s, RSTART, RLENGTH)
        s = substr(s, RSTART + RLENGTH)
        id = seg;    sub(/^"id":/, "", id);          sub(/,.*$/, "", id)
        stage = seg; sub(/^.*"stage":"/, "", stage); sub(/",.*$/, "", stage)
        name = seg;  sub(/^.*"name":"/, "", name);   sub(/"$/, "", name)
        body = s
        if (match(s, hdr)) body = substr(s, 1, RSTART - 1)
        reason = "-"
        if (match(body, /"failure_reason":"[^"]*"/)) {
          reason = substr(body, RSTART, RLENGTH)
          sub(/^"failure_reason":"/, "", reason); sub(/"$/, "", reason)
        }
        printf "%s\t%s\t%s\t%s\n", id, name, stage, reason
      }
    }
  '
}

# gl_job_trace <job-id> [lignes] -> queue de la trace du job (défaut : 100 dernières lignes).
# C'est la matière première du diagnostic ; l'appelant en extrait les lignes d'erreur utiles
# plutôt que de recopier le log brut.
gl_job_trace() {
  local jid="$1" lines="${2:-100}"
  if [ -z "$jid" ]; then echo "usage: gl_job_trace <job-id> [lignes]" >&2; return 2; fi
  local raw
  raw="$(glab api "projects/$(gl_project_enc)/jobs/$jid/trace" 2>/dev/null)"
  if [ -z "$raw" ]; then echo "Trace du job $jid vide ou illisible dans $GL_PROJECT" >&2; return 1; fi
  printf '%s\n' "$raw" | tail -n "$lines"
}

# gl_pipeline_wait <pipeline-id> [timeout-s] -> suit le pipeline jusqu'à un état terminal et
# imprime le statut final. Codes retour : 0 = success ; 1 = failed/canceled/skipped/manual ;
# 3 = timeout (défaut 900 s), le dernier statut observé est quand même imprimé.
gl_pipeline_wait() {
  local pid="$1" timeout="${2:-900}" poll=15 waited=0 status
  if [ -z "$pid" ]; then echo "usage: gl_pipeline_wait <pipeline-id> [timeout-s]" >&2; return 2; fi
  while :; do
    status="$(gl_pipeline_status "$pid")" || return 1
    case "$status" in
      success) printf '%s\n' "$status"; return 0 ;;
      failed|canceled|skipped|manual) printf '%s\n' "$status"; return 1 ;;
    esac
    if [ "$waited" -ge "$timeout" ]; then
      echo "gl_pipeline_wait : délai dépassé (${timeout}s) — dernier statut : $status" >&2
      printf '%s\n' "$status"
      return 3
    fi
    sleep "$poll"; waited=$((waited + poll))
  done
}

# --- Revue best-effort : file de revue + relecteur posé à la main --------------------------------
# Arbitrage du chantier « travail à plusieurs » (#155/#161) : l'approbation n'est PAS rendue
# obligatoire (`approvals_before_merge` reste à 0 — une approbation bloquante recréerait une
# dépendance entre personnes et le merge reste une décision humaine, §6). Ce qui est outillé, c'est
# la VISIBILITÉ : la file d'attente est affichée en tête de /backlog (gl_review_queue), la plus
# ancienne d'abord. La pose d'un relecteur (gl_set_reviewer) reste OUTILLÉE mais n'est plus
# AUTOMATIQUE : depuis #196, /ticket-finish ne l'appelle plus — désigner un relecteur est un geste
# humain explicite, la file de revue portant seule le signal « cette MR attend quelqu'un ».

# gl_project_humans [access-min] -> membres HUMAINS du projet éligibles à une revue, une ligne TSV
# par membre : username <TAB> access_level, triés par username (ordre stable, d'où la reproductibilité
# de gl_pick_reviewer). Sont écartés : les bots GitLab (`User.bot`), les comptes non actifs, les
# comptes d'automatisation listés dans GL_BOT_USERS, et les niveaux d'accès < access-min
# (défaut GL_REVIEWER_MIN_ACCESS). Membres directs ET hérités du groupe.
gl_project_humans() {
  local min="${1:-$GL_REVIEWER_MIN_ACCESS}" raw
  raw="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { projectMembers(relations:[DIRECT,INHERITED], first:100) { nodes { accessLevel { integerValue } user { username bot state } } } } }')" || return 1
  printf '%s\n' "$raw" | awk -v min="$min" -v bots=",$GL_BOT_USERS," '
    {
      n = split($0, parts, /\{"accessLevel":\{"integerValue":/)
      for (i = 2; i <= n; i++) {
        node = parts[i]
        match(node, /^[0-9]+/); lvl = substr(node, RSTART, RLENGTH) + 0
        if (lvl < min) continue
        if (node ~ /"bot":true/) continue
        if (node !~ /"state":"active"/) continue
        if (!match(node, /"username":"[^"]*"/)) continue
        u = substr(node, RSTART, RLENGTH); sub(/^"username":"/, "", u); sub(/"$/, "", u)
        if (index(bots, "," u ",")) continue
        printf "%s\t%s\n", u, lvl
      }
    }
  ' | sort -u
}

# gl_pick_reviewer [auteur] [graine] -> imprime le username d'un relecteur humain DIFFÉRENT de
# l'auteur (défaut : l'utilisateur glab courant). Aucun nom n'est codé en dur : les candidats
# viennent de l'API des membres (gl_project_humans).
# La graine (l'iid de la MR en pratique) sert de ROTATION : même MR -> même relecteur (la pose est
# donc reproductible et idempotente), MR différentes -> relecteurs répartis plutôt que toujours le
# même. Code 1 si aucun candidat (projet à une seule personne) : l'appelant continue sans relecteur,
# la revue est best-effort.
gl_pick_reviewer() {
  local auteur="${1:-}" graine="${2:-0}"
  [ -n "$auteur" ] || auteur="$(gl_current_user 2>/dev/null)"
  local candidats n idx
  candidats="$(gl_project_humans | awk -F'\t' -v a="$auteur" '$1 != a { print $1 }')" || return 1
  n="$(printf '%s\n' "$candidats" | grep -c .)"
  if [ "$n" -eq 0 ]; then
    echo "gl_pick_reviewer : aucun relecteur humain disponible (hors auteur « ${auteur:-?} » et comptes d'automatisation « $GL_BOT_USERS »)" >&2
    return 1
  fi
  graine="$(printf '%s' "$graine" | tr -cd '0-9')"
  [ -n "$graine" ] || graine=0
  idx=$(( graine % n + 1 ))
  printf '%s\n' "$candidats" | sed -n "${idx}p"
}

# gl_mr_iid [mr|branche] -> imprime l'iid de la MR OUVERTE désignée : un nombre est rendu tel quel,
# un nom de branche est résolu via l'API (défaut : la branche courante). Code 1 si aucune MR ouverte.
gl_mr_iid() {
  local ref="${1:-}"
  [ -n "$ref" ] || ref="$(git branch --show-current 2>/dev/null)"
  if [ -z "$ref" ]; then echo "gl_mr_iid : ni MR ni branche à résoudre" >&2; return 2; fi
  case "$ref" in
    *[!0-9]*) ;;
    *) printf '%s\n' "$ref"; return 0 ;;
  esac
  local iid
  iid="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { mergeRequests(state: opened, sourceBranches:["'"$ref"'"], first:1) { nodes { iid } } } }' \
        | grep -o '"iid":"[0-9]*"' | head -1 | sed 's/.*:"//; s/"//')"
  if [ -z "$iid" ]; then echo "Aucune MR ouverte pour la branche « $ref » dans $GL_PROJECT" >&2; return 1; fi
  printf '%s\n' "$iid"
}

# gl_mr_review_info <mr|branche> -> « auteur <TAB> relecteurs » (relecteurs séparés par des virgules,
# champ vide si aucun). Une seule lecture GraphQL, parsing shell pur.
gl_mr_review_info() {
  local ref="${1:-}" mr raw auteur rev
  mr="$(gl_mr_iid "$ref")" || return 1
  raw="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { mergeRequest(iid:"'"$mr"'") { author { username } reviewers { nodes { username } } } } }')" || return 1
  case "$raw" in
    *'"reviewers"'*) ;;
    *) echo "gl_mr_review_info : MR !$mr illisible dans $GL_PROJECT" >&2; return 1 ;;
  esac
  auteur="$(printf '%s' "$raw" | grep -o '"author":{"username":"[^"]*"' | head -1 | sed 's/.*"username":"//; s/"$//')"
  # Les relecteurs se lisent APRÈS la clé "reviewers" : l'auteur, lu plus haut, ne doit pas y entrer.
  rev="$(printf '%s' "$raw" | sed 's/.*"reviewers"//' | grep -o '"username":"[^"]*"' \
         | sed 's/.*"username":"//; s/"$//' | awk '{ out = (NR == 1 ? $0 : out "," $0) } END { if (NR) print out }')"
  printf '%s\t%s\n' "$auteur" "$rev"
}

# gl_mr_reviewers <mr|branche> -> relecteurs actuellement posés sur la MR (CSV, vide si aucun).
gl_mr_reviewers() {
  local info
  info="$(gl_mr_review_info "$@")" || return 1
  printf '%s\n' "$info" | cut -f2
}

# gl_set_reviewer [mr|branche] [username] -> pose un relecteur humain sur la MR (défaut : la MR
# ouverte de la branche courante ; relecteur choisi par gl_pick_reviewer, graine = iid de la MR).
# APPEL EXPLICITE UNIQUEMENT (#196) : aucune commande du workflow ne l'invoque — /ticket-finish ne
# pose plus de relecteur d'office, la désignation étant un geste humain.
# IDEMPOTENT et non destructif : si un relecteur est DÉJÀ posé (par un humain ou par un passage
# précédent), il est conservé tel quel — la fonction ne remplace jamais. Refuse de désigner l'auteur.
# Best-effort par nature : sur un projet à une seule personne, elle échoue proprement (code 1) et
# l'appelant poursuit sans relecteur.
gl_set_reviewer() {
  local ref="${1:-}" who="${2:-}" mr info auteur rev out
  mr="$(gl_mr_iid "$ref")" || return 1
  info="$(gl_mr_review_info "$mr")" || return 1
  IFS=$'\t' read -r auteur rev <<< "$info"
  if [ -n "$rev" ]; then
    printf 'MR !%s : relecteur déjà posé (@%s) — inchangé.\n' "$mr" "$rev"
    return 0
  fi
  if [ -z "$who" ]; then
    who="$(gl_pick_reviewer "$auteur" "$mr")" || return 1
  fi
  if [ "$who" = "$auteur" ]; then
    echo "gl_set_reviewer : @$who est l'auteur de la MR !$mr — le relecteur doit en être distinct." >&2
    return 1
  fi
  out="$(glab mr update "$mr" --reviewer "$who" 2>&1)" || {
    echo "gl_set_reviewer : échec de la pose du relecteur @$who sur !$mr : $out" >&2
    return 1
  }
  printf 'MR !%s : relecteur → @%s (auteur @%s).\n' "$mr" "$who" "$auteur"
}

# gl_review_queue -> file des MR OUVERTES en attente de revue, la plus ANCIENNE d'abord, une ligne
# TSV par MR (en-tête préfixée « # » à ignorer côté machine) :
#     mr <TAB> age_j <TAB> etat <TAB> pipeline <TAB> auteur <TAB> relecteur <TAB> branche <TAB> titre
# `age_j` = jours écoulés depuis la création (c'est l'ancienneté qui déclenche la relecture),
# `etat` ∈ draft|ready, `pipeline` = statut du dernier pipeline en minuscules (success/failed/
# running/…, « - » si aucun), `relecteur` = CSV des relecteurs posés (« - » si personne).
# Le préfixe « Draft: » du titre est retiré : l'information est déjà dans la colonne `etat`.
gl_review_queue() {
  local raw lignes
  raw="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { mergeRequests(state: opened, sort: CREATED_ASC, first: 50) { nodes { iid title createdAt draft sourceBranch author { username } reviewers { nodes { username } } headPipeline { status } } } } }')" || return 1
  printf '# mr\tage_j\tetat\tpipeline\tauteur\trelecteur\tbranche\ttitre\n'
  # 1re passe (awk) : projection des champs. 2e passe (shell) : l'ancienneté, calculée par `date`
  # via gl_elapsed_days — mktime() n'existe pas dans tous les awk (mawk), on ne s'y appuie pas.
  lignes="$(printf '%s\n' "$raw" | awk '
    {
      n = split($0, parts, /\{"iid":"/)
      for (i = 2; i <= n; i++) {
        node = parts[i]
        match(node, /^[0-9]+/); iid = substr(node, RSTART, RLENGTH)

        titre = "-"
        if (match(node, /","title":"/)) {
          rest = substr(node, RSTART + RLENGTH)
          if (match(rest, /","createdAt":"/)) titre = substr(rest, 1, RSTART - 1)
        }
        gsub(/\\u0026/, "\\&", titre); gsub(/\\u003e/, ">", titre); gsub(/\\u003c/, "<", titre)
        sub(/^Draft: /, "", titre)

        cree = "-"
        if (match(node, /"createdAt":"[0-9-]+/)) {
          cree = substr(node, RSTART, RLENGTH); sub(/^"createdAt":"/, "", cree)
        }

        etat = (node ~ /"draft":true/) ? "draft" : "ready"

        branche = "-"
        if (match(node, /"sourceBranch":"[^"]*"/)) {
          branche = substr(node, RSTART, RLENGTH); sub(/^"sourceBranch":"/, "", branche); sub(/"$/, "", branche)
        }

        auteur = "-"
        if (match(node, /"author":\{"username":"[^"]*"/)) {
          auteur = substr(node, RSTART, RLENGTH); sub(/^.*"username":"/, "", auteur); sub(/"$/, "", auteur)
        }

        # Relecteurs : uniquement ceux du bloc "reviewers" de CE nœud (l auteur est déjà consommé).
        rel = "-"
        if (match(node, /"reviewers":\{"nodes":\[[^]]*\]/)) {
          bloc = substr(node, RSTART, RLENGTH); liste = ""
          while (match(bloc, /"username":"[^"]*"/)) {
            u = substr(bloc, RSTART, RLENGTH); sub(/^"username":"/, "", u); sub(/"$/, "", u)
            liste = (liste == "" ? u : liste "," u)
            bloc = substr(bloc, RSTART + RLENGTH)
          }
          if (liste != "") rel = liste
        }

        pipe = "-"
        if (match(node, /"headPipeline":\{"status":"[A-Z_]*"/)) {
          pipe = substr(node, RSTART, RLENGTH); sub(/^.*"status":"/, "", pipe); sub(/"$/, "", pipe)
          pipe = tolower(pipe)
        }

        printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n", iid, cree, etat, pipe, auteur, rel, branche, titre
      }
    }
  ')"
  [ -n "$lignes" ] || return 0
  local mr cree etat pipe auteur rel branche titre age
  while IFS=$'\t' read -r mr cree etat pipe auteur rel branche titre; do
    [ -n "$mr" ] || continue
    age="$(gl_elapsed_days "$cree" 2>/dev/null)" || age="-"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$mr" "$age" "$etat" "$pipe" "$auteur" "$rel" "$branche" "$titre"
  done <<< "$lignes"
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

# gl_cleanup_merged [--auto] -> supprime les branches LOCALES (hors main et hors branche courante)
# dont GitLab confirme la MR à l'état « merged ». Conçu pour tourner automatiquement (appelé par
# `worktree.sh ensure`, donc tout /ticket-start) — c'est le pendant non-interactif de
# /branch-cleanup :
#   • ne supprime QUE ce que GitLab confirme mergé (garde-fou docs/10 §6) — jamais une branche au
#     statut incertain (opened/closed/aucune MR) ;
#   • `git branch -D` est sûr ici car le merge est confirmé (le projet merge en squash) ;
#   • ne change jamais de branche, n'écrit rien sur GitLab, et s'abstient si l'arbre est sale.
#
# Opère sur le CLONE PRINCIPAL d'où qu'on l'appelle (#305) — même parti pris que gl_sync_main et
# que worktree.sh gc, et pour une raison précise. Les refs, elles, sont partagées par tous les
# worktrees d'un dépôt : la liste des branches et le résultat des suppressions seraient les mêmes
# de partout. Ce qui change, c'est ce sur quoi portent les deux garde-fous — l'arbre regardé est
# celui du clone principal, normalement propre et sur `main`, et non celui d'un worktree en plein
# travail, qui ferait sauter la purge en silence à chaque reprise de session.
#
# ⚠ Une branche EMPRUNTÉE PAR UN WORKTREE ne se supprime pas : `git branch -D` la refuse (« checked
# out at … ») quel que soit le répertoire d'où on l'appelle — c'est une protection de git, pas un
# effet de bord du chemin choisi. Elle est donc comptée À PART et NOMMÉE (#305) : jusque-là l'échec
# n'incrémentait AUCUN des deux compteurs, si bien que la branche sortait du compte rendu sans un
# mot — 3 branches sur 41 lors de la purge de rattrapage du 2026-08-07, et un bilan qui annonçait
# moins de branches qu'il n'en avait examinées. C'est aussi pourquoi le ramassage des worktrees
# passe AVANT cette purge dans `ensure` comme dans /branch-cleanup (#197, docs/10 §9.2) : sans lui,
# les branches des worktrees soldés resteraient indéfiniment.
#
# En `--auto` (appel d'office par un point de passage), muet quand il n'y a rien à dire : aucune
# suppression et aucun refus = aucune ligne. Même parti pris que `worktree.sh gc --auto`.
gl_cleanup_merged() {
  local auto=0
  case "${1:-}" in
    --auto) auto=1 ;;
    '') ;;
    *) echo "usage: gl_cleanup_merged [--auto]" >&2; return 2 ;;
  esac

  local principal
  principal="$(gl_depot_principal)" || {
    echo "Nettoyage des branches ignoré : hors d'un dépôt git." >&2
    return 0
  }
  if [ -n "$(git -C "$principal" status --porcelain 2>/dev/null)" ]; then
    echo "Nettoyage des branches ignoré : changements non commités présents." >&2
    return 0
  fi
  # Pruning cosmétique des refs de suivi ; non bloquant (jamais de prompt d'identifiants) et non
  # fatal : la décision de suppression s'appuie sur l'état MR côté GitLab, pas sur ce fetch.
  GIT_TERMINAL_PROMPT=0 git -C "$principal" fetch --prune origin >/dev/null 2>&1
  local current branch state porteur deleted=0 kept=0 empruntees=0
  current="$(git -C "$principal" branch --show-current 2>/dev/null)"
  while IFS= read -r branch; do
    [ -z "$branch" ] && continue
    [ "$branch" = "main" ] && continue
    [ "$branch" = "$current" ] && continue
    state="$(gl_mr_state "$branch")"
    if [ "$state" != "merged" ]; then
      kept=$((kept + 1))
      continue
    fi
    if git -C "$principal" branch -D "$branch" >/dev/null 2>&1; then
      printf '  supprimée : %s (MR merged)\n' "$branch"
      deleted=$((deleted + 1))
      continue
    fi
    porteur="$(gl_worktree_de_branche "$principal" "$branch")"
    if [ -n "$porteur" ]; then
      printf '  ⚠ conservée : %s (MR merged, empruntée par le worktree %s)\n' "$branch" "$porteur"
    else
      printf '  ⚠ conservée : %s (MR merged, suppression refusée par git)\n' "$branch"
    fi
    empruntees=$((empruntees + 1))
  done < <(git -C "$principal" branch --format='%(refname:short)')

  [ "$auto" = 1 ] && [ "$deleted" -eq 0 ] && [ "$empruntees" -eq 0 ] && return 0
  if [ "$empruntees" -gt 0 ]; then
    printf 'Nettoyage des branches : %s supprimée(s), %s conservée(s), %s mergée(s) mais empruntée(s) par un worktree.\n' \
      "$deleted" "$kept" "$empruntees"
  else
    printf 'Nettoyage des branches : %s supprimée(s), %s conservée(s).\n' "$deleted" "$kept"
  fi
}

# --- Fin de vie d'un worktree -------------------------------------------------------------------
# gl_worktree_done <iid> [branche] -> « <verdict><TAB><sha><TAB><raison> » : la seule question que se
# pose le ramassage de scripts/git/worktree.sh (#197) — ce worktree a-t-il encore une raison
# d'exister ? La réponse vient de GitLab, JAMAIS du nom de la branche (garde-fou docs/10 §6).
#
#   fini     MR de la branche MERGÉE, ou ticket FERMÉ (réalisé, abandonné, doublon) ;
#   actif    travail en cours (ticket ouvert, MR absente ou ouverte) — on n'y touche pas ;
#   inconnu  GitLab illisible (glab absent, hors ligne, ticket introuvable) — on n'y touche pas
#            non plus, et le code de retour 1 le dit : ne rien savoir n'autorise rien.
#
# Le <sha> n'est renseigné que sur un « fini » par merge, et vaut « - » sinon. C'est la tête de la
# branche source AU MOMENT du merge, et la seule référence locale fiable pour distinguer « tout est
# parti » de « il reste des commits ici » : le projet mergeant en SQUASH, les commits de la branche
# ne sont pas des ancêtres de `main`, et GitLab supprime la branche distante au merge — il ne reste
# donc ni `origin/<branche>` à comparer, ni ascendance à tester.
#
# « - » et non un champ vide : dans un TSV lu par `IFS=$'\t' read`, la tabulation est un séparateur
# BLANC, donc deux tabulations consécutives comptent pour une seule et le champ suivant se décale
# (le sha atterrirait dans la raison). Même convention que le plan de scripts/orchestrate/run.sh.
#
# Une seule lecture dans le cas nominal (MR mergée) ; deux quand il faut départager par le ticket.
gl_worktree_done() {
  local iid="$1" branche="${2:-}" json etat="" mr sha raw etat_ticket
  if [ -z "$iid" ]; then echo "usage: gl_worktree_done <iid> [branche]" >&2; return 2; fi
  gl_require_glab >/dev/null 2>&1 || { printf 'inconnu\t-\tglab indisponible ou non authentifié\n'; return 1; }

  if [ -n "$branche" ]; then
    # `head -1` sur la première clé de premier niveau : même lecture que gl_mr_state, dont l'état
    # précède les objets imbriqués (auteur, jalon, pipeline) qui portent aussi une clé « state ».
    json="$(glab mr view "$branche" --output json 2>/dev/null)"
    if [ -n "$json" ]; then
      etat="$(printf '%s' "$json" | grep -o '"state":"[a-z]*"' | head -1 | sed 's/.*:"//; s/"//')"
      if [ "$etat" = "merged" ]; then
        mr="$(printf '%s' "$json" | grep -o '"iid":[0-9]*' | head -1 | sed 's/.*://')"
        # « "sha": » et non « _sha": » : diff_refs porte base_sha/head_sha/start_sha, que ce motif
        # laisse de côté.
        sha="$(printf '%s' "$json" | grep -o '"sha":"[0-9a-f]\{7,40\}"' | head -1 | sed 's/.*:"//; s/"//')"
        printf 'fini\t%s\tMR !%s mergée\n' "${sha:--}" "${mr:-?}"
        return 0
      fi
    fi
  fi

  # Pas de MR mergée : le ticket tranche. Lecture en TEXTE et non en JSON — la ligne « state: » y est
  # de premier niveau, là où le JSON d'un ticket imbrique le `state` de son jalon (« closed » sur
  # toute phase soldée), qu'un grep prendrait pour celui du ticket.
  raw="$(glab issue view "$iid" 2>/dev/null)"
  if [ -z "$raw" ]; then
    printf 'inconnu\t-\tticket #%s illisible dans %s\n' "$iid" "$GL_PROJECT"
    return 1
  fi
  etat_ticket="$(printf '%s\n' "$raw" | sed -n 's/^state:[[:space:]]*//p' | head -1)"
  case "$etat_ticket" in
    closed) printf 'fini\t-\tticket #%s fermé (MR « %s »)\n' "$iid" "${etat:-aucune}" ;;
    ''|*)   printf 'actif\t-\tticket #%s « %s » (MR « %s »)\n' "$iid" "${etat_ticket:-?}" "${etat:-aucune}" ;;
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

# --- Branche de travail & worktrees --------------------------------------------------------------
# gl_branch_from_raw <iid> (stdin = sortie brute de `glab issue view`) -> nom de la branche de
# travail : <préfixe du label type::>/<iid>-<slug du titre>. Fonction PURE : elle ne lit pas le
# ticket, ce qui permet à gl_start_brief de la nourrir avec la lecture qu'il a déjà faite.
# Sans label type::, imprime le préfixe littéral « <type> » et renvoie 3 — à l'appelant de le
# déduire du titre plutôt que de fabriquer une branche mal nommée.
gl_branch_from_raw() {
  local iid="$1" raw title labels type prefix slug
  raw="$(cat)"
  title="$(printf '%s\n' "$raw" | sed -n 's/^title:[[:space:]]*//p' | head -1)"
  labels="$(printf '%s\n' "$raw" | sed -n 's/^labels:[[:space:]]*//p' | head -1)"
  type="$(printf '%s' "$labels" | grep -o 'type::[a-z]*' | head -1)"
  slug="$(gl_slug "$title")"
  if [ -n "$type" ] && prefix="$(gl_branch_prefix "$type" 2>/dev/null)"; then
    printf '%s/%s-%s\n' "$prefix" "$iid" "$slug"
    return 0
  fi
  printf '<type>/%s-%s\n' "$iid" "$slug"
  return 3
}

# gl_branch_for <iid> -> même chose, en lisant le ticket (une lecture). Sert à scripts/git/worktree.sh,
# qui n'a pas de brief sous la main.
gl_branch_for() {
  local iid="$1" raw
  if [ -z "$iid" ]; then echo "usage: gl_branch_for <iid>" >&2; return 2; fi
  gl_require_glab || return 1
  raw="$(glab issue view "$iid" 2>/dev/null)" || { echo "Issue #$iid introuvable dans $GL_PROJECT" >&2; return 1; }
  printf '%s\n' "$raw" | gl_branch_from_raw "$iid"
}

# Sommes-nous dans un worktree LIÉ (`git worktree add`) plutôt que dans le clone principal ?
# Signature universelle et sans dépendance de version : à la racine d'un worktree lié, `.git` est
# un FICHIER (« gitdir: … ») là où le clone principal porte un répertoire.
gl_in_linked_worktree() {
  local top
  top="$(git rev-parse --show-toplevel 2>/dev/null)" || return 1
  [ -f "$top/.git" ]
}

# gl_depot_principal -> racine du clone PRINCIPAL, d'où que l'on appelle (worktree lié compris) :
# le répertoire git commun est partagé par tous les worktrees d'un dépôt, son parent est le clone
# principal. Jumeau de `depot_principal` dans scripts/git/worktree.sh, qui appelle ce fichier en
# SOUS-PROCESSUS (jamais en le sourçant) et ne peut donc pas la lui emprunter.
gl_depot_principal() {
  local commun
  commun="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
  if [ -z "$commun" ]; then
    commun="$(git rev-parse --git-common-dir 2>/dev/null)" || return 1
    commun="$(cd "$commun" 2>/dev/null && pwd)" || return 1
  fi
  [ -n "$commun" ] || return 1
  dirname "$commun"
}

# gl_start_branch <branche> -> place le dépôt sur la branche de travail, que l'on soit dans le
# clone principal ou dans un worktree lié (docs/10-workflow-git.md §9). Idempotent, trois cas :
#   - déjà sur la branche (situation normale dans un worktree créé par scripts/git/worktree.sh) ;
#   - branche locale existante -> bascule ;
#   - branche absente -> création depuis `origin/main` à jour.
# Dans le clone principal, `main` est rafraîchi au passage. Dans un worktree lié on ne passe JAMAIS
# par `git checkout main` : `main` est déjà emprunté par le clone principal, et git refuse
# d'emprunter deux fois la même branche.
#
# ⚠ Ce helper ne purge PLUS les branches mergées (#305). Il l'a fait de #23 à #305, à l'époque où
# il était le point de passage qui mettait `main` à jour ; depuis #181 c'est `worktree.sh ensure`
# qui tient ce rôle, et l'appel n'était plus joignable — /ticket-start appelle `ensure` d'abord, si
# bien que `start-branch` sort soit par « déjà sur la branche », soit par la voie worktree lié,
# jamais par celle qui purgeait. Le résultat s'est vu à l'œil nu : 35 branches mergées accumulées
# sur le clone principal, la plus ancienne remontant à #220. Garder un second point d'appel
# inatteignable est exactement ce qui a rendu la régression invisible — la purge a donc UN seul
# déclencheur automatique, `ensure` (plus /branch-cleanup à la demande).
gl_start_branch() {
  local branche="$1" courante
  if [ -z "$branche" ]; then echo "usage: gl_start_branch <branche>" >&2; return 2; fi
  case "$branche" in
    *'<type>'*)
      echo "Branche sans préfixe : « $branche » — déduire le type (feat|fix|chore|docs) avant de démarrer." >&2
      return 2 ;;
  esac

  courante="$(git branch --show-current 2>/dev/null)"
  if [ "$courante" = "$branche" ]; then
    GIT_TERMINAL_PROMPT=0 git fetch origin main >/dev/null 2>&1
    printf 'Déjà sur %s — rien à créer.\n' "$branche"
    return 0
  fi

  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    echo "Arbre de travail non propre : committer, stasher ou annuler avant de changer de branche." >&2
    return 1
  fi

  if git show-ref --verify --quiet "refs/heads/$branche"; then
    git checkout "$branche" || return 1
    printf 'Branche existante : %s (bascule).\n' "$branche"
    return 0
  fi

  if gl_in_linked_worktree; then
    git fetch origin main || return 1
    git checkout -b "$branche" origin/main || return 1
  else
    git checkout main || return 1
    git pull origin main || return 1
    git checkout -b "$branche" || return 1
  fi
  printf 'Branche créée : %s (depuis origin/main).\n' "$branche"
}

# --- Repères des worktrees ------------------------------------------------------------------------
# gl_worktree_de_branche <clone-principal> <branche> -> chemin du répertoire de travail qui a cette
# branche en HEAD, vide si elle n'est empruntée nulle part. Deux appelants, deux questions : pour
# gl_sync_main, COMMENT avancer `main` (poser la ref, ou merge --ff-only dans le répertoire qui
# l'emprunte) ; pour gl_cleanup_merged, QUI retient une branche mergée que `git branch -D` refuse
# de supprimer (#305).
#
# Le motif du `case` est entre GUILLEMETS : sans eux le nom de branche serait interprété comme un
# motif, et un slug porteur d'un `?` ou d'un `*` matcherait la mauvaise ligne.
gl_worktree_de_branche() {
  local principal="$1" branche="$2" courant="" ligne
  while IFS= read -r ligne; do
    case "$ligne" in
      worktree\ *)                  courant="${ligne#worktree }" ;;
      "branch refs/heads/$branche") printf '%s' "$courant"; return 0 ;;
    esac
  done < <(git -C "$principal" worktree list --porcelain 2>/dev/null)
  return 1
}

# --- Mise à jour de la branche main locale --------------------------------------------------------
# gl_worktree_de_main <clone-principal> -> le cas particulier de `main` (voir gl_sync_main).
gl_worktree_de_main() { gl_worktree_de_branche "${1:-}" main; }

# gl_sync_main [--check] -> avance `refs/heads/main` du CLONE PRINCIPAL sur `origin/main`, en
# FAST-FORWARD seulement (#205).
#
# Le retard n'est pas un détail cosmétique : depuis #181, /ticket-start monte un worktree et y
# relocalise la session, donc le clone principal ne change plus de branche et la branche « clone
# principal » de gl_start_branch (`git checkout main && git pull`) n'est plus jamais empruntée.
# Plus rien ne faisait avancer `main` — ce que montrent l'IDE, `git log` et un diff local sur le
# clone principal restait figé au dernier /branch-cleanup. À NE PAS confondre avec `origin/main`,
# lui déjà rafraîchi partout (gl_start_branch, gl_cleanup_merged, worktree.sh) : c'est de lui que
# part chaque worktree de ticket, le code produit n'a donc jamais été en cause.
#
# Il n'existe aucun événement local à écouter : le merge a lieu sur GitLab et aucun hook git ne se
# déclenche à ce moment-là (`post-merge` ne réagit qu'à un merge ou un pull LOCAL). D'où le
# câblage aux points de passage obligés — `worktree.sh ensure` (donc tout /ticket-start, manuel
# comme autonome) et /branch-cleanup — plutôt qu'un déclencheur événementiel qui n'existe pas.
#
# Deux façons d'avancer la ref, selon que `main` est empruntée ou non par un répertoire de travail :
# posée directement (`update-ref`, aucun fichier touché, marche depuis un worktree), ou par
# `merge --ff-only` DANS ce répertoire — sans quoi l'index y resterait sur l'ancien arbre et tout
# le delta apparaîtrait en « supprimé/modifié ».
#
# S'ABSTIENT plutôt que de forcer, dans la lignée de gl_behind_main et de worktree.sh gc : ça dit,
# ça ne casse pas. Jamais de `reset --hard`, jamais de non-fast-forward — un `main` local divergent
# porte un commit que personne n'a poussé, l'écraser serait une perte de données.
#
# Codes de retour, pour l'appelant (best-effort : un code non nul n'est PAS une erreur fatale, il
# ne doit interrompre ni un /ticket-start ni un run /orchestrate) :
#   0 = à jour, ou mise à jour faite      3 = main local divergent (non fast-forward) — abstention
#   1 = état illisible (hors dépôt git, origin/main absent)
#   2 = usage                             4 = répertoire porteur de main non propre — abstention
gl_sync_main() {
  local check=0
  case "${1:-}" in
    --check) check=1 ;;
    '') ;;
    *) echo "usage: gl_sync_main [--check]" >&2; return 2 ;;
  esac

  local principal
  principal="$(gl_depot_principal)" || {
    echo "sync-main : hors d'un dépôt git — mise à jour de main sautée." >&2
    return 1
  }

  # Fetch non bloquant (jamais de prompt d'identifiants, cf. gl_behind_main) : hors ligne on
  # retombe sur le dernier origin/main connu, qu'un fetch précédent a pu avancer.
  GIT_TERMINAL_PROMPT=0 git -C "$principal" fetch origin main >/dev/null 2>&1
  local cible locale
  cible="$(git -C "$principal" rev-parse --verify --quiet refs/remotes/origin/main 2>/dev/null)"
  if [ -z "$cible" ]; then
    echo "sync-main : origin/main introuvable — mise à jour de main sautée." >&2
    return 1
  fi
  locale="$(git -C "$principal" rev-parse --verify --quiet refs/heads/main 2>/dev/null)"

  # Le cas de loin le plus fréquent, et le seul qui ne mérite aucune ligne : rien à faire.
  [ "$locale" = "$cible" ] && return 0

  if [ -n "$locale" ] && ! git -C "$principal" merge-base --is-ancestor "$locale" "$cible" 2>/dev/null; then
    printf '⚠ sync-main : main local a divergé de origin/main — mise à jour sautée (jamais de force).\n' >&2
    printf '  à trancher à la main : git -C "%s" log --oneline origin/main..main\n' "$principal" >&2
    return 3
  fi

  local retard porteur
  if [ -n "$locale" ]; then
    retard="$(git -C "$principal" rev-list --count "$locale..$cible" 2>/dev/null)" || retard="?"
  else
    retard="0"   # `main` locale absente : ce n'est pas un retard, c'est une création
  fi
  porteur="$(gl_worktree_de_main "$principal")"

  if [ -n "$porteur" ]; then
    if [ -n "$(git -C "$porteur" status --porcelain 2>/dev/null)" ]; then
      printf '⚠ sync-main : main en retard de %s commit(s), mais son répertoire de travail a des changements non commités — mise à jour sautée.\n' "$retard" >&2
      printf '  %s\n' "$porteur" >&2
      return 4
    fi
    if [ "$check" = 1 ]; then
      printf 'sync-main : main avancerait de %s commit(s) (merge --ff-only dans %s).\n' "$retard" "$porteur"
      return 0
    fi
    if ! git -C "$porteur" merge --ff-only origin/main >/dev/null 2>&1; then
      printf '⚠ sync-main : fast-forward de main refusé par git — mise à jour sautée.\n' >&2
      return 3
    fi
  else
    if [ "$check" = 1 ]; then
      printf 'sync-main : main avancerait de %s commit(s) (pose de la ref, aucun répertoire de travail concerné).\n' "$retard"
      return 0
    fi
    # `main` n'est empruntée nulle part : la ref se pose directement, sans toucher au moindre
    # fichier — c'est ce qui rend l'appel valide depuis un worktree. Le fast-forward vient d'être
    # vérifié ; l'ancienne valeur est passée en dernier argument pour que git refuse d'écrire si
    # quelqu'un a bougé la ref entre-temps.
    if ! git -C "$principal" update-ref -m "sync-main : fast-forward sur origin/main" \
        refs/heads/main "$cible" ${locale:+"$locale"} 2>/dev/null; then
      printf '⚠ sync-main : pose de refs/heads/main refusée — mise à jour sautée.\n' >&2
      return 3
    fi
  fi

  printf 'main mis à jour : %s commit(s) repris depuis origin/main.\n' "$retard"
}

# --- « Quelqu'un s'occupe-t-il encore de ce ticket ? » (#328) --------------------------------------
# Sixième membre de la famille des réconciliations — `worktree.sh gc` (§9.2), `reconcile-workflow`
# (#275), `sync-main` (§9.3), `setup --derive` (§9.4), `cleanup-merged` (§9.5) — et celui qui
# manquait le plus. Un ticket entre en « En cours » (et s'assigne) à /ticket-start ; il n'en sort que
# par /ticket-ship, /ticket-finish ou /ticket-abandon. LA TROISIÈME SORTIE EST L'ABSENCE DE SORTIE :
# session coupée par un délai, pilote arrêté au `taskkill` (aucun trap ne s'exécute), console fermée,
# limite d'usage épuisée, session interactive laissée en plan. Le ticket reste « En cours » ET
# assigné — c'est-à-dire exactement le filtre par lequel `queue.sh` l'écarte : la règle
# d'anti-collision qui protège le travail vivant cache définitivement le travail mort. Deux tickets
# dans cet état au constat du 2026-08-11 : #316 (2047 lignes commitées, jamais poussées, plus sept
# lots sautés en cascade) et #325 (396 lignes non commitées dans son worktree).
#
# LE RENVERSEMENT DE QUESTION FAIT TOUT LE TRAVAIL. On ne demande pas « ce run a-t-il échoué ? » — un
# pilote tué ne pose aucun verdict, or c'est précisément lui qui fabrique l'orphelin — mais
# « quelqu'un s'occupe-t-il encore de ce ticket ? ». Posée ainsi, la question couvre tous les modes de
# mort, run ou pas, session interactive comprise.
#
# DEUX SOURCES, DANS CET ORDRE :
#   1. la CARTE DU PILOTE (#213) — un run vivant qui nomme le ticket parmi ceux en vol. Elle est
#      VÉRIFIABLE (PID, naissance du processus, hôte), donc c'est une preuve et non un indice ;
#   2. sinon la FRAÎCHEUR DU WORKTREE, annoncée comme une DÉDUCTION — précédent de `status.sh`, dont
#      l'état « en cours » se lit dans la carte quand elle est là et se déduit sinon.
#
# ⚠ La carte ne prouve JAMAIS la mort, seulement la vie : un pilote mort ne dit rien du ticket, qu'une
# session interactive a très bien pu reprendre depuis. Même asymétrie que dans `pilote.sh`, et elle va
# toujours dans le même sens — désigner à tort le ticket d'une session vivante coûte infiniment plus
# cher que de rater un orphelin d'un tour, puisque #329 rendra l'orphelin prenable.
#
# TROIS VERDICTS et non deux, parce que « je ne sais pas » est une réponse :
#   vivant       quelqu'un est dessus (carte du pilote, ou worktree écrit récemment) ;
#   orphelin     worktree présent ICI, silencieux depuis plus de GL_ORPHELIN_SEUIL, et aucun pilote
#                vivant ne le nomme ;
#   hors-portee  aucun worktree sur cette machine — rien à en dire, et surtout pas que c'est un
#                orphelin : le ticket peut être en plein travail sur le clone de quelqu'un d'autre.
#                La couverture est celle des worktrees de CETTE machine, comme `gc` et
#                `cleanup-merged` ; elle se dit dans la sortie plutôt que de laisser croire à un
#                balayage global.
#
# EN LECTURE SEULE, entièrement : ce verbe SIGNALE. Il ne pose aucun label, ne touche à aucune
# assignation, ne retire aucun worktree et n'écrit rien côté GitLab. Le geste de reprise est celui de
# #329, et il est explicite — toute la famille signale sans décider. `--check` est accepté par
# cohérence de famille et n'a donc aucun effet : le refuser serait un piège pour la main qui vient de
# taper `reconcile-workflow --check`.
#
# Modes :
#   --auto        ne parle que s'il y a un orphelin. C'est ainsi que `worktree.sh gc` l'appelle, donc
#                 /ticket-start, /branch-cleanup et le démarrage d'un run — mêmes points de passage
#                 que #275 et pour la même raison : greffer sur `gc` les sert tous les trois d'un
#                 coup, sans ajouter d'étape à `ensure` ;
#   --tsv         la surface machine « iid <TAB> verdict <TAB> source <TAB> détail <TAB> titre »,
#                 que #329 consommera plutôt que de relire une phrase en français ;
#   --sauf <iid>  écarte un ticket. `ensure` s'en sert pour celui qu'il est en train de démarrer :
#                 le signaler orphelin serait vrai une seconde et faux la suivante.
#
# Codes de retour : 0 = rien à signaler · 3 = au moins un orphelin (même convention que
# `setup.sh --derive`) · 1 = backlog illisible · 2 = usage. AUCUN n'est un motif de blocage pour un
# appelant : ce verbe ne doit jamais empêcher un ticket de démarrer ni un run de continuer.

# gl_mtime <chemin> -> date de dernière modification, en secondes Unix. GNU (`stat -c`) puis BSD
# (`stat -f`) puis `date -r` : le dépôt tourne sous Git Bash comme sur un runner Linux. Jumeau de
# `mtime` dans scripts/orchestrate/status.sh, qui ne source pas ce fichier.
gl_mtime() {
  stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || date -r "$1" +%s 2>/dev/null
}

# gl_duree_lisible <secondes> -> « 42s », « 12min30 », « 3h05 ». Même formule que status.sh, pour
# que deux sorties qui parlent du même silence l'écrivent pareil.
gl_duree_lisible() {
  local s="${1:-0}"
  [ "$s" -ge 0 ] 2>/dev/null || s=0
  if [ "$s" -lt 60 ]; then printf '%ds' "$s"
  elif [ "$s" -lt 3600 ]; then printf '%dmin%02d' $((s / 60)) $((s % 60))
  else printf '%dh%02d' $((s / 3600)) $(((s % 3600) / 60)); fi
}

# gl_orch_dir -> le journal d'orchestration du CLONE PRINCIPAL (`.maestro/orchestrate`), d'où qu'on
# appelle. Même résolution que `journal.sh` (#307) et pour la même raison : depuis un worktree, le
# `.maestro/orchestrate` local est un répertoire vide, et la carte des pilotes serait invisible.
gl_orch_dir() {
  local principal
  principal="$(gl_depot_principal)" || return 1
  printf '%s/.maestro/orchestrate' "$principal"
}

# gl_pilotes_en_vol -> une ligne « run-id <TAB> pid <TAB> iids-en-vol » par run VIVANT. Sortie vide =
# personne ne tourne, et c'est le cas courant : le silence est la réponse normale.
#
# La lecture est DÉLÉGUÉE à scripts/orchestrate/pilote.sh, seul endroit qui sache relire une carte et
# la vérifier (PID recyclé, zombie, autre machine) — deux formules qui divergeraient se remarqueraient
# trop tard. Le fichier peut manquer (dépôt jetable des tests, clone partiel) : on rend alors le
# silence, et la déduction tranche seule.
gl_pilotes_en_vol() {
  local orch
  orch="$(gl_orch_dir)" || return 1
  [ -d "$orch" ] || return 1
  [ -r "$GL_ICI/../orchestrate/pilote.sh" ] || return 1
  # shellcheck source=scripts/orchestrate/pilote.sh
  . "$GL_ICI/../orchestrate/pilote.sh" || return 1
  pilotes_vivants "$orch"
}

# gl_branche_du_iid <clone-principal> <iid> -> la branche LOCALE du ticket, d'après la convention
# `<type>/<iid>-<slug>`. Lue dans git plutôt que demandée à GitLab : c'est gratuit, hors ligne, et ça
# marche encore quand le label `type::` a changé depuis (même formule que status.sh).
gl_branche_du_iid() {
  git -C "$1" for-each-ref --format='%(refname:short)' refs/heads 2>/dev/null |
    awk -v iid="$2" 'index($0, "/" iid "-") || $0 ~ ("/" iid "$") { print; exit }'
}

# gl_worktree_activite <chemin> -> l'instant de la dernière écriture attribuable à une session qui
# travaille là, en secondes Unix. Rien (code 1) si le répertoire a disparu ou n'apprend rien.
#
# TROIS TÉMOINS, le plus récent l'emporte — aucun ne suffit seul :
#   • l'INDEX git, touché par tout `git add`/`commit`/`status` de la session (même témoin que
#     status.sh, pour qui c'est « le signal de progression le plus fiable ») ;
#   • les FICHIERS que `git status` rend modifiés ou non suivis : une session qui édite du code
#     pendant quarante minutes sans lancer une seule commande git laisse l'index froid et les
#     fichiers brûlants ;
#   • l'ATELIER DE SESSION `.maestro/session/` (#307), gitignoré donc invisible du deuxième témoin,
#     et par construction l'endroit où une session pose ses fichiers de travail.
#
# ⚠ L'index est lu AVANT le `git status`, et celui-ci passe par `--no-optional-locks` : un
# `git status` ordinaire RÉÉCRIT l'index (rafraîchissement du cache de stat), donc le mesurer le
# rendrait frais — l'outil de mesure produirait la fraîcheur qu'il mesure, et plus aucun worktree ne
# serait jamais silencieux.
gl_worktree_activite() {
  local wt="$1" index="" f t max="" n=0 atelier
  [ -d "$wt" ] || return 1

  index="$(git -C "$wt" rev-parse --git-path index 2>/dev/null)"
  # `--git-path` rend un chemin ABSOLU pour un worktree lié, mais RELATIF (« .git/index ») pour un
  # répertoire de travail principal : sans cette reprise il serait résolu depuis le répertoire
  # courant, et l'activité passerait pour nulle (même piège que status.sh).
  case "$index" in /* | ?:[/\\]*) ;; *) [ -n "$index" ] && index="$wt/$index" ;; esac
  if [ -n "$index" ] && t="$(gl_mtime "$index")" && [ -n "$t" ]; then max="$t"; fi

  # Sortie en `-z` : un chemin porteur d'espaces ou d'accents y voyage tel quel, là où le format
  # ordinaire le met entre guillemets et l'échappe. Bornée à cent entrées — dater une session au
  # travail n'en demande pas plus, et un worktree qui en porte mille ne doit pas coûter mille `stat`.
  # Les lignes de renommage rendent l'ancien chemin dans un enregistrement à part, qui ne se `stat`
  # pas : il est sauté comme tout chemin illisible, sans conséquence sur le maximum.
  while IFS= read -r -d '' f; do
    n=$((n + 1)); [ "$n" -gt 100 ] && break
    f="${f:3}"
    [ -n "$f" ] || continue
    t="$(gl_mtime "$wt/$f")" || continue
    [ -n "$t" ] || continue
    if [ -z "$max" ] || [ "$t" -gt "$max" ]; then max="$t"; fi
  done < <(git --no-optional-locks -C "$wt" status --porcelain -z 2>/dev/null)

  atelier="$wt/.maestro/session"
  if [ -d "$atelier" ]; then
    for f in "$atelier" "$atelier"/*; do
      [ -e "$f" ] || continue
      t="$(gl_mtime "$f")" || continue
      [ -n "$t" ] || continue
      if [ -z "$max" ] || [ "$t" -gt "$max" ]; then max="$t"; fi
    done
  fi

  [ -n "$max" ] || return 1
  printf '%s' "$max"
}

gl_reconcile_en_cours() {
  local auto=0 tsv=0 sauf=""
  while [ $# -gt 0 ]; do
    case "$1" in
      # Accepté et sans effet : ce verbe est en lecture seule par nature (cf. en-tête).
      --check) ;;
      --auto)  auto=1 ;;
      --tsv)   tsv=1 ;;
      --sauf)  sauf="${2:-}"; shift ;;
      *) echo "usage: gl_reconcile_en_cours [--check] [--auto] [--tsv] [--sauf <iid>]" >&2; return 2 ;;
    esac
    shift
  done

  local principal
  principal="$(gl_depot_principal)" || {
    echo "reconcile-en-cours : hors d'un dépôt git — contrôle sauté." >&2
    return 1
  }
  # UNE lecture pour tout le monde : le cycle de vie est dans le backlog ouvert, déjà projeté en TSV.
  local table
  table="$(gl_backlog_table opened)" || {
    echo "reconcile-en-cours : backlog illisible — contrôle sauté." >&2
    return 1
  }
  # Une lecture des cartes pour tout le monde aussi : `pilotes_vivants` balaie tous les runs.
  local pilotes
  pilotes="$(gl_pilotes_en_vol 2>/dev/null)" || pilotes=""

  local maintenant iid statut titre run branche wt activite silence
  local verdict origine detail lignes="" orphelins=0 vivants=0 hors=0
  maintenant="$(date +%s)"

  while IFS=$'\t' read -r iid statut _ _ _ titre; do
    case "$iid" in ''|'#'*|*[!0-9]*) continue ;; esac
    [ "$statut" = "En cours" ] || continue
    [ -n "$sauf" ] && [ "$iid" = "$sauf" ] && continue

    # 1. La carte du pilote, qui fait foi quand elle est là.
    run="$(printf '%s\n' "$pilotes" | awk -F'\t' -v iid="$iid" '
      { n = split($3, v, ","); for (i = 1; i <= n; i++) if (v[i] == iid) { print $1 "\t" $2; exit } }')"
    if [ -n "$run" ]; then
      verdict="vivant"; origine="carte du pilote"
      detail="run ${run%%$'\t'*}, pilote pid ${run##*$'\t'}"
      vivants=$((vivants + 1))
    else
      # 2. Sinon la déduction, annoncée comme telle.
      branche="$(gl_branche_du_iid "$principal" "$iid")"
      wt=""
      [ -n "$branche" ] && wt="$(gl_worktree_de_branche "$principal" "$branche")"
      if [ -z "$wt" ] || [ ! -d "$wt" ]; then
        verdict="hors-portee"; origine="hors de portée"
        detail="aucun worktree sur cette machine"
        hors=$((hors + 1))
      elif ! activite="$(gl_worktree_activite "$wt")" || [ -z "$activite" ]; then
        verdict="hors-portee"; origine="hors de portée"
        detail="worktree illisible : $wt"
        hors=$((hors + 1))
      else
        silence=$((maintenant - activite))
        [ "$silence" -ge 0 ] || silence=0
        if [ "$silence" -lt "$GL_ORPHELIN_SEUIL" ]; then
          verdict="vivant"; origine="déduction"
          detail="worktree écrit il y a $(gl_duree_lisible "$silence")"
          vivants=$((vivants + 1))
        else
          verdict="orphelin"; origine="déduction"
          detail="worktree silencieux depuis $(gl_duree_lisible "$silence") — $wt"
          orphelins=$((orphelins + 1))
        fi
      fi
    fi

    if [ "$tsv" = 1 ]; then
      # Le TITRE est la cinquième colonne : #329 aura à dire QUOI il propose de reprendre, et une
      # relecture du backlog rien que pour ça serait une lecture de plus sur un verbe qui n'en fait
      # qu'une. Il ne va pas dans le rendu humain, où c'est le chemin du worktree qui est actionnable.
      lignes="$lignes$iid"$'\t'"$verdict"$'\t'"$origine"$'\t'"$detail"$'\t'"$titre"$'\n'
      continue
    fi
    case "$verdict" in
      orphelin)    lignes="$lignes$(printf '  ⚠ #%s orphelin — %s : %s' "$iid" "$origine" "$detail")"$'\n' ;;
      vivant)      lignes="$lignes$(printf '  ✓ #%s vivant — %s : %s' "$iid" "$origine" "$detail")"$'\n' ;;
      *)           lignes="$lignes$(printf '  ~ #%s — %s' "$iid" "$detail")"$'\n' ;;
    esac
  done <<< "$table"

  # En `--auto` (appel d'office par un point de passage), on ne parle que des orphelins : un ticket
  # bien vivant n'est pas une nouvelle, et le silence est le cas normal. Même parti pris que
  # `worktree.sh gc --auto` et `cleanup-merged --auto`.
  if [ "$auto" = 1 ]; then
    [ "$orphelins" -eq 0 ] && return 0
    printf '%s' "$lignes" | grep -F ' orphelin — '
    printf '  → détail : bash scripts/gitlab/lib.sh reconcile-en-cours\n'
    # Le geste de reprise est NOMMÉ ici, à l'unique endroit dont héritent les trois points de
    # passage de `gc` (/ticket-start, /branch-cleanup, démarrage d'un run) — un constat qui ne dit
    # pas quoi en faire se relit trois fois et ne se traite jamais. Nommer n'est pas décider : rien
    # ne se reprend d'office, ce serait défaire l'asymétrie qui fonde tout ce dispositif (#329).
    printf '  → le reprendre (« À faire » + libéré, worktree intact) : bash scripts/gitlab/lib.sh reprendre-en-cours <iid>\n'
    return 3
  fi

  if [ "$tsv" = 1 ]; then
    printf '# iid\tverdict\tsource\tdetail\ttitre\n'
    printf '%s' "$lignes"
  else
    printf '\nTickets « En cours » — quelqu'\''un s'\''en occupe-t-il encore ?\n\n'
    if [ -z "$lignes" ]; then
      printf '  aucun ticket « En cours » dans le backlog ouvert.\n'
    else
      printf '%s' "$lignes"
    fi
    printf '\n%s vivant(s), %s orphelin(s), %s hors de portée.\n' "$vivants" "$orphelins" "$hors"
    printf 'Portée : les worktrees de CETTE machine (comme le ramassage et la purge) — un ticket\n'
    printf 'travaillé sur un autre clone est « hors de portée », jamais orphelin.\n\n'
  fi
  [ "$orphelins" -gt 0 ] && return 3
  return 0
}

# --- Rendre un orphelin prenable (#329) -----------------------------------------------------------
# Le lot précédent DÉSIGNE (`reconcile-en-cours`, #328) ; celui-ci REND PRENABLE. Sans lui, la
# détection ne fait que nommer une perte : #316 serait resté exactement où il était, avec ses
# 2047 lignes commitées et jamais poussées.
#
# « Prenable » est une CONJONCTION, parce que le filtre de `queue.sh` en est une : un ticket entre
# dans un plan s'il est « À faire » ET libre. Poser le cycle de vie sans retirer l'assignation
# laisserait le ticket écarté par la seconde moitié du filtre, et l'inverse par la première — d'où
# une seule mutation qui fait les deux, comme `gl_begin` fait l'aller.
#
# CE QUE LA REPRISE NE TOUCHE PAS, et c'est tout son intérêt : le worktree, la branche, les commits
# non poussés, les fichiers non commités. Elle n'écrit QUE dans GitLab. Le travail attend là où la
# session l'a laissé, et `worktree.sh ensure` l'y retrouve au démarrage suivant — c'est exactement ce
# qu'on veut de #316 et de ses 2047 lignes. Aucun `gc`, aucun `git`, aucune suppression.
#
# TROIS GARDE-FOUS, et le premier est le seul qui compte vraiment :
#   1. NE JAMAIS REPRENDRE UN VIVANT. Le verdict est demandé à `reconcile-en-cours`, jamais
#      redéduit ici — deux formules qui divergeraient se remarqueraient trop tard, et celle du lot 1
#      sait ce que cette fonction ignore (carte du pilote, fraîcheur du worktree, seuil généreux).
#      Seul « orphelin » ouvre la porte ; « vivant » et « hors de portée » la ferment, le second
#      parce que ne rien savoir n'autorise rien (le ticket peut être en plein travail ailleurs).
#   2. LE PLAFOND (GL_REPRISES_MAX) : au-delà, la reprise se demande explicitement. Voir plus haut
#      pourquoi ce n'est pas un détail de confort.
#   3. « Abandonné »/« Doublon » ne sont jamais concernés — ils ne sont pas « En cours », donc
#      `reconcile-en-cours` ne les rend même pas. Même filtre que `reconcile-workflow`, obtenu ici
#      gratuitement.
# `--force` lève les deux premiers, JAMAIS en silence : c'est le geste de qui sait quelque chose que
# la machine ignore (le worktree a été retiré, la session d'en face est morte pour de bon).
#
# LA TRACE EST DOUBLE, parce qu'elle répond à deux questions qui n'ont pas le même lecteur :
#   • un COMMENTAIRE sur le ticket — « d'où sort ce ticket revenu à “À faire” ? » se pose devant
#     GitLab, des semaines plus tard, par quelqu'un qui n'a pas la machine sous la main ;
#   • une ligne dans `.maestro/orchestrate/reprises.tsv` — c'est elle qui PORTE LE PLAFOND, et un
#     compteur qui vit dans un fil de commentaires se relit en parsant du texte libre.
# Les deux sont BEST-EFFORT et postérieures à la mutation : une trace qui échoue ne doit pas laisser
# croire que la reprise n'a pas eu lieu — elle a eu lieu, GitLab fait foi. Le compteur, lui, le dit
# franchement quand il n'a pas pu compter : un plafond qu'on croit tenu et qui ne l'est pas serait
# pire que pas de plafond du tout.
#
# Codes de retour : 0 = tout repris · 1 = au moins un échec · 2 = usage · 3 = au moins un REFUS
# (vivant, hors de portée, plafond atteint). Le 3 est un refus, pas une panne : rien n'a été écrit.

# gl_reprises_fichier -> le registre des reprises, dans le journal d'orchestration du CLONE
# PRINCIPAL (même résolution que `gl_orch_dir`, donc juste depuis un worktree).
#
# Il vit à CÔTÉ des répertoires de run et non dedans : une reprise est une propriété du TICKET, pas
# d'un run — celle de #325 n'a jamais eu de run du tout (session interactive laissée en plan). Le
# ménage du journal (#198) ne balaie que les répertoires `<run-id>/`, ce fichier lui est donc
# invisible, et c'est voulu : le plafond doit survivre à la rétention des dix derniers runs.
gl_reprises_fichier() {
  local orch
  orch="$(gl_orch_dir)" || return 1
  printf '%s/reprises.tsv' "$orch"
}

# gl_reprises_de <iid> -> le nombre de reprises DÉJÀ consignées pour ce ticket. Imprime toujours un
# nombre (0 quand il n'y a rien à lire) mais rend 1 si le registre est INATTEIGNABLE : l'appelant
# doit pouvoir distinguer « jamais repris » de « je ne sais pas compter », le plafond n'ayant aucun
# sens dans le second cas.
gl_reprises_de() {
  local iid="$1" f
  if ! f="$(gl_reprises_fichier)"; then printf '0'; return 1; fi
  if [ ! -f "$f" ]; then printf '0'; return 0; fi
  awk -F '\t' -v iid="$iid" '$1 !~ /^#/ && $2 == iid { n++ } END { printf "%d", n + 0 }' "$f"
}

# gl_consigne_reprise <iid> <run> <verdict> <rang> -> ajoute une ligne au registre. Best-effort :
# rend 1 sans rien dire de plus si le journal est hors d'atteinte, l'appelant portant le message.
gl_consigne_reprise() {
  local iid="$1" run="$2" verdict="$3" rang="$4" f
  f="$(gl_reprises_fichier)" || return 1
  mkdir -p "$(dirname "$f")" 2>/dev/null || return 1
  if [ ! -f "$f" ]; then
    printf '# date\tiid\trun_origine\tverdict_origine\trang\tpar\n' >"$f" 2>/dev/null || return 1
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date +%FT%T)" "$iid" "${run:--}" "${verdict:--}" "$rang" "$(gl_current_user 2>/dev/null || printf '?')" \
    >>"$f" 2>/dev/null || return 1
}

# gl_origine_du_ticket <iid> -> « run <TAB> verdict <TAB> raison » du dernier run qui a eu ce ticket
# en main, vide s'il n'y en a jamais eu (une session interactive n'écrit aucun journal). DÉLÉGUÉ à
# `journal.sh`, qui est le fichier dont c'est le métier de lire `.maestro/orchestrate/` — et qui le
# résout déjà vers le clone principal d'où qu'on l'appelle.
gl_origine_du_ticket() {
  local j="$GL_ICI/../orchestrate/journal.sh"
  [ -r "$j" ] || return 1
  bash "$j" origine "$1" 2>/dev/null
}

# gl_worktree_du_ticket <iid> -> le répertoire de travail où dort le travail de ce ticket, vide s'il
# n'y en a pas sur cette machine. Recomposé à partir des deux helpers du lot 1 plutôt que découpé
# dans la colonne `detail` de son TSV : cette colonne est une phrase écrite pour un humain, et la
# lire comme une donnée reviendrait à figer sa formulation.
gl_worktree_du_ticket() {
  local principal branche
  principal="$(gl_depot_principal)" || return 1
  branche="$(gl_branche_du_iid "$principal" "$1")"
  [ -n "$branche" ] || return 1
  gl_worktree_de_branche "$principal" "$branche"
}

gl_reprendre_en_cours() {
  local check=0 force=0 iids=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --check) check=1 ;;
      --force) force=1 ;;
      -h|--help|--*) echo "usage: gl_reprendre_en_cours [--check] [--force] <iid>…" >&2; return 2 ;;
      *) iids="$iids $1" ;;
    esac
    shift
  done
  if [ -z "$iids" ]; then
    echo "usage: gl_reprendre_en_cours [--check] [--force] <iid>… (le ticket se NOMME : c'est le geste)" >&2
    return 2
  fi

  # UNE lecture des verdicts pour tous les iid demandés. En `--force` on ne la fait pas du tout :
  # elle ne servirait qu'à être ignorée, et elle coûte deux appels GitLab plus un balayage des
  # worktrees — or `--force` existe justement pour les cas où elle conclut mal (worktree retiré).
  local table="" code_table=0
  if [ "$force" = 0 ]; then
    table="$(gl_reconcile_en_cours --tsv)" || code_table=$?
    # 3 = « il y a des orphelins », qui est le cas nominal ici. Seul 1 (backlog illisible) est une
    # panne : ne rien savoir n'autorise rien.
    if [ "$code_table" != 0 ] && [ "$code_table" != 3 ]; then
      echo "reprendre-en-cours : état des tickets « En cours » illisible — aucune reprise tentée." >&2
      return 1
    fi
  fi

  local iid verdict detail titre run verdict_run raison deja code_deja wt rang
  local echecs=0 refus=0 repris=0
  for iid in $iids; do
    case "$iid" in ''|*[!0-9]*) printf '  ✗ « %s » n'\''est pas un iid.\n' "$iid" >&2; echecs=$((echecs + 1)); continue ;; esac

    verdict=""; detail=""; titre=""
    if [ "$force" = 1 ]; then
      # `--force` ne se joue JAMAIS en silence : il lève le garde-fou qui protège le travail des
      # autres, et une sortie qui n'en dirait rien laisserait croire que la machine a conclu à
      # l'abandon — alors que c'est l'appelant qui en répond.
      printf '  ! #%s : --force — ni le verdict ni le plafond ne sont vérifiés (vous en répondez).\n' "$iid"
    else
      IFS=$'\t' read -r verdict detail titre <<< "$(printf '%s\n' "$table" |
        awk -F '\t' -v i="$iid" '$1 == i { print $2 "\t" $4 "\t" $5; exit }')"
      case "$verdict" in
        orphelin) ;;
        vivant)
          printf '  ⚠ #%s : quelqu'\''un s'\''en occupe (%s) — pas repris.\n' "$iid" "$detail"
          refus=$((refus + 1)); continue ;;
        hors-portee)
          printf '  ⚠ #%s : %s — hors de portée d'\''ici, donc rien ne dit qu'\''il est abandonné.\n' "$iid" "$detail"
          printf '      (--force si vous savez que plus personne n'\''est dessus)\n'
          refus=$((refus + 1)); continue ;;
        *)
          printf '  ⚠ #%s n'\''est pas « En cours » — il n'\''y a rien à reprendre.\n' "$iid"
          refus=$((refus + 1)); continue ;;
      esac
    fi

    # Le plafond, et l'aveu quand il n'a pas pu être vérifié.
    deja="$(gl_reprises_de "$iid")"; code_deja=$?
    if [ "$code_deja" != 0 ]; then
      printf '  ~ #%s : registre des reprises hors d'\''atteinte — cette reprise ne sera pas comptée.\n' "$iid"
    elif [ "$deja" -ge "$GL_REPRISES_MAX" ] && [ "$force" = 0 ]; then
      printf '  ⚠ #%s déjà repris %s fois (plafond %s) — pas repris.\n' "$iid" "$deja" "$GL_REPRISES_MAX"
      printf '      Un ticket qui retombe à chaque run brûle une session entière à chaque fois :\n'
      printf '      lisez sa trace (bash scripts/gitlab/lib.sh reprises %s) avant d'\''insister par --force.\n' "$iid"
      refus=$((refus + 1)); continue
    fi

    # D'où il sort — la moitié « lisible » de la trace, et la seule information qu'un humain n'a
    # aucun moyen de retrouver seul.
    run=""; verdict_run=""; raison=""
    IFS=$'\t' read -r run verdict_run raison <<< "$(gl_origine_du_ticket "$iid")"
    wt="$(gl_worktree_du_ticket "$iid" 2>/dev/null)" || wt=""
    rang=$((deja + 1))

    if [ "$check" = 1 ]; then
      printf '  → #%s passerait à « À faire » et serait libéré (reprise %s/%s)\n' "$iid" "$rang" "$GL_REPRISES_MAX"
      [ -n "$run" ] && printf '      origine : run %s — %s%s\n' "$run" "$verdict_run" \
        "$([ -n "$raison" ] && printf ' (%s)' "$raison")"
      [ -n "$wt" ] && printf '      worktree : %s (intact)\n' "$wt"
      repris=$((repris + 1)); continue
    fi

    # LA mutation : « À faire » (et les cinq autres retirés, l'exclusion mutuelle étant à notre
    # charge sur le plan Free) ET la liste des assignés VIDÉE, en un seul appel. `assigneeIds`
    # REMPLACE la liste — c'est la sémantique que `gl_begin` utilise pour prendre le ticket, et la
    # liste vide est donc exactement le geste inverse.
    local wiid gids cible retraits out
    wiid="$(gl_workitem_gid "$iid")" || { echecs=$((echecs + 1)); continue; }
    gids="$(gl_workflow_gids)"       || { echecs=$((echecs + 1)); continue; }
    cible="$(printf '%s\n' "$gids" | awk -F '\t' '$1 == "a-faire" { print $2; exit }')"
    if [ -z "$cible" ]; then
      echo "reprendre-en-cours : label « $GL_WORKFLOW_SCOPE::a-faire » absent — provisionner : bash scripts/gitlab/bootstrap.sh" >&2
      echecs=$((echecs + 1)); continue
    fi
    retraits="$(printf '%s\n' "$gids" | awk -F '\t' '$1 != "a-faire" { printf "%s\"%s\"", (n++ ? "," : ""), $2 }')"
    out="$(glab api graphql -f query='mutation { workItemUpdate(input:{ id:"'"$wiid"'", assigneesWidget:{ assigneeIds:[] }, labelsWidget:{ addLabelIds:["'"$cible"'"], removeLabelIds:['"$retraits"'] } }){ errors } }' 2>&1)"
    case "$out" in
      *'"errors":[]'*) ;;
      *) printf 'Échec de la reprise de #%s : %s\n' "$iid" "$out" >&2; echecs=$((echecs + 1)); continue ;;
    esac
    repris=$((repris + 1))

    printf '  ✓ #%s repris — « À faire » et libre%s\n' "$iid" \
      "$([ -n "$titre" ] && printf ' : %s' "$titre")"
    if [ -n "$run" ]; then
      printf '      origine  : run %s — %s%s\n' "$run" "$verdict_run" \
        "$([ -n "$raison" ] && printf ' (%s)' "$raison")"
    else
      printf '      origine  : aucun run ne l'\''a jugé (session interactive laissée en plan ?)\n'
    fi
    if [ -n "$wt" ]; then
      printf '      worktree : %s — INTACT (commits et fichiers non commités conservés)\n' "$wt"
    fi
    printf '      reprise  : %s/%s\n' "$rang" "$GL_REPRISES_MAX"

    # Les deux traces, après coup et sans jamais faire échouer la reprise elle-même.
    gl_consigne_reprise "$iid" "$run" "$verdict_run" "$rang" ||
      printf '      ~ trace locale non écrite (journal hors d'\''atteinte) — le plafond ne comptera pas celle-ci.\n'
    # Le commentaire voyage par FICHIER, jamais sur la ligne de commande (#233) : un `-m "$(cat …)"`
    # multi-ligne n'est matchable par aucune règle de permission. Le brouillon reste dans le
    # répertoire temporaire du système — personne ne le relit, rien n'y renvoie (règle #234).
    local note
    note="$(mktemp "${TMPDIR:-/tmp}/maestro-reprise.XXXXXX")" 2>/dev/null && {
      {
        printf '🔁 **Ticket repris** — il était « En cours » sans que personne ne s'\''en occupe encore.\n\n'
        if [ -n "$run" ]; then
          printf -- '- origine : run `%s`, verdict `%s`%s\n' "$run" "$verdict_run" \
            "$([ -n "$raison" ] && printf ' (%s)' "$raison")"
        else
          printf -- '- origine : aucun run ne l'\''a jugé (session interactive laissée en plan).\n'
        fi
        [ -n "$wt" ] && printf -- '- worktree conservé : `%s` — commits et travail non commité intacts.\n' "$wt"
        printf -- '- cycle de vie remis à « À faire », assignation retirée : le ticket est de nouveau prenable.\n'
        printf -- '- reprise %s/%s (au-delà du plafond, une reprise se demande par `--force`).\n' "$rang" "$GL_REPRISES_MAX"
      } >"$note"
      gl_issue_note "$iid" "$note" >/dev/null 2>&1 ||
        printf '      ~ commentaire non posté sur #%s (la reprise, elle, a bien eu lieu).\n' "$iid"
      rm -f "$note"
    }
  done

  [ "$check" = 1 ] && printf '\n(--check : rien n'\''a été écrit.)\n'
  [ "$echecs" -gt 0 ] && return 1
  [ "$refus" -gt 0 ] && return 3
  [ "$repris" -gt 0 ] || return 3
  return 0
}

# gl_reprises [<iid>] -> le registre, en clair. La trace n'a de valeur que si elle se lit : c'est ce
# qu'on consulte avant d'insister par `--force` sur un ticket qui a déjà rechuté deux fois.
gl_reprises() {
  local iid="${1:-}" f
  if ! f="$(gl_reprises_fichier)"; then
    echo "reprises : journal d'orchestration hors d'atteinte (hors dépôt git ?)." >&2
    return 1
  fi
  if [ ! -f "$f" ]; then
    printf 'Aucune reprise consignée%s.\n' "$([ -n "$iid" ] && printf ' pour #%s' "$iid")"
    return 0
  fi
  printf '\nReprises consignées%s — %s\n\n' "$([ -n "$iid" ] && printf ' pour #%s' "$iid")" "$f"
  awk -F '\t' -v iid="$iid" '
    $1 ~ /^#/ { next }
    iid != "" && $2 != iid { next }
    { n++; printf "  %s  #%-5s reprise %s — origine : run %s, verdict %s (par %s)\n", $1, $2, $5, $3, $4, $6 }
    END { if (!n) printf "  aucune ligne.\n" }
  ' "$f"
  printf '\n'
}

# --- Retard sur origin/main ----------------------------------------------------------------------
# gl_behind_main [branche] -> « ma branche a-t-elle pris du retard sur origin/main ? », à consulter
# AVANT le push (/ticket-finish). Purement CONSULTATIF : cette fonction ne rebase pas, ne pousse
# pas et n'écrit rien — elle imprime le constat et la commande de rebase, dont le déclenchement
# reste une décision humaine. Un rebase réécrit l'historique d'une branche déjà poussée et
# appellerait un force-push, interdit par les garde-fous (docs/10 §6).
#
# Le « conflit probable » est une heuristique de FICHIERS : ceux modifiés des deux côtés depuis la
# base commune. Volontairement grossière (git seul tranche vraiment), mais c'est exactement le
# signal qui manque sur les fichiers aimants à conflits — CLAUDE.md, docs/10, ce fichier-ci.
#
# Codes de retour, pour l'appelant :
#   0 = à jour, rien à faire          3 = en retard, aucun fichier commun (rebase a priori serein)
#   4 = en retard + conflit probable  2 = usage   1 = état illisible (pas d'origin/main, etc.)
# Un code non nul n'est donc PAS une erreur, juste un constat : l'appeler en
# `bash … behind-main || echo "verdict=$?"` pour lire le verdict sans interrompre une clôture
# sous `set -e` — c'est ce que fait /ticket-finish.
gl_behind_main() {
  local branche="${1:-}" base derriere devant communs nb
  branche="${branche:-$(git branch --show-current 2>/dev/null)}"
  if [ -z "$branche" ]; then
    echo "gl_behind_main : branche indéterminée (HEAD détachée ?) — la préciser en argument." >&2
    return 2
  fi
  case "$branche" in
    main|master)
      printf 'Branche « %s » : rien à comparer avec origin/main.\n' "$branche"
      return 0 ;;
  esac

  # Fetch non bloquant (jamais de prompt d'identifiants) : sans réseau on compare au dernier
  # origin/main connu, ce qui reste plus utile que de ne rien dire.
  GIT_TERMINAL_PROMPT=0 git fetch origin main >/dev/null 2>&1
  if ! git rev-parse --verify --quiet origin/main >/dev/null 2>&1; then
    echo "gl_behind_main : origin/main introuvable — contrôle du retard sauté." >&2
    return 1
  fi
  if ! base="$(git merge-base "$branche" origin/main 2>/dev/null)" || [ -z "$base" ]; then
    echo "gl_behind_main : aucune base commune entre « $branche » et origin/main." >&2
    return 1
  fi
  derriere="$(git rev-list --count "$branche..origin/main" 2>/dev/null)" || derriere=0
  devant="$(git rev-list --count "origin/main..$branche" 2>/dev/null)" || devant=0

  if [ "${derriere:-0}" -eq 0 ]; then
    printf "Branche « %s » à jour avec origin/main (%s commit(s) d'avance).\n" "$branche" "${devant:-0}"
    return 0
  fi

  # Intersection des fichiers touchés de part et d'autre de la base commune.
  communs="$(comm -12 \
      <(git diff --name-only "$base" "$branche" 2>/dev/null | sort -u) \
      <(git diff --name-only "$base" origin/main 2>/dev/null | sort -u))"

  printf "⚠ Branche « %s » en retard : %s commit(s) derrière origin/main (%s d'avance).\n" \
    "$branche" "$derriere" "${devant:-0}"
  if [ -n "$communs" ]; then
    nb="$(printf '%s\n' "$communs" | wc -l | tr -d '[:space:]')"
    printf '  conflit probable — %s fichier(s) modifié(s) des deux côtés :\n' "$nb"
    printf '%s\n' "$communs" | sed 's/^/    - /'
  else
    printf '  aucun fichier modifié des deux côtés — rebase a priori sans conflit.\n'
  fi
  printf '  rebase proposé (décision humaine, jamais automatique) :\n'
  printf '    git fetch origin main && git rebase origin/main\n'

  if [ -n "$communs" ]; then return 4; fi
  return 3
}

# --- Conflit réel avec origin/main --------------------------------------------------------------
# gl_mr_conflict [branche] -> « cette branche se merge-t-elle proprement dans origin/main ? », le
# verdict RÉEL, à consulter avant de remédier une MR (/mr-fix, docs/10 §8.3). Purement CONSULTATIF
# et en lecture seule : ni checkout, ni index touché, ni écriture — d'où l'appel possible depuis le
# clone principal comme depuis un worktree, sur une branche qu'on ne sort jamais.
#
# Le verdict vient de `git merge-tree --write-tree`, qui joue un VRAI merge 3-way en base d'objets.
# C'est ce qui le sépare des deux sources déjà présentes, et pourquoi aucune des deux ne suffisait :
#   • gl_behind_main (ci-dessus) est une heuristique de FICHIERS — modifiés des deux côtés —,
#     pessimiste par construction : elle est vraie presque partout sur les fichiers aimants du dépôt
#     (CLAUDE.md, docs/10-workflow-git.md, ce fichier-ci), donc sans valeur prédictive. Elle répond
#     par ailleurs à une autre question, posée AVANT le push, quand le conflit naît des merges qui
#     suivent ;
#   • `has_conflicts`/`detailed_merge_status` de GitLab est ASYNCHRONE : à la mesure du 2026-08-07,
#     5 MR ouvertes sur 6 répondaient `checking` ou `unchecked`. Il se lit en complément, jamais en
#     source unique, et surtout jamais en l'attendant.
#
# Forme de la sortie de git, sur laquelle repose le parsing (mesurée, git 2.50) : ligne 1 = l'OID de
# l'arbre produit, puis un chemin en conflit par ligne, puis une LIGNE VIDE qui sépare des messages
# (« CONFLICT (content): … »). D'où la lecture « à partir de la ligne 2, jusqu'à la première ligne
# vide ».
#
# Codes de retour, alignés sur gl_behind_main — un code non nul est un CONSTAT, pas une erreur :
#   0 = se merge proprement           3 = conflit (fichiers listés)
#   2 = usage                         1 = état illisible (pas d'origin/main, histoires sans ancêtre
#                                         commun — que git rend en 128, à ne pas confondre avec le
#                                         1 d'un conflit)
# À appeler en `bash … mr-conflict || echo "verdict=$?"` pour lire le verdict sans interrompre une
# remédiation sous `set -e`.
gl_mr_conflict() {
  local branche="${1:-}" sortie rc fichiers nb
  branche="${branche:-$(git branch --show-current 2>/dev/null)}"
  if [ -z "$branche" ]; then
    echo "gl_mr_conflict : branche indéterminée (HEAD détachée ?) — la préciser en argument." >&2
    return 2
  fi
  case "$branche" in
    main|master)
      printf 'Branche « %s » : rien à merger dans origin/main.\n' "$branche"
      return 0 ;;
  esac

  # Fetch non bloquant (jamais de prompt d'identifiants), même politique que gl_behind_main : sans
  # réseau on tranche contre le dernier origin/main connu, ce qui reste plus utile que se taire.
  GIT_TERMINAL_PROMPT=0 git fetch origin main >/dev/null 2>&1
  if ! git rev-parse --verify --quiet origin/main >/dev/null 2>&1; then
    echo "gl_mr_conflict : origin/main introuvable — contrôle de conflit sauté." >&2
    return 1
  fi
  if ! git rev-parse --verify --quiet "$branche" >/dev/null 2>&1; then
    echo "gl_mr_conflict : branche « $branche » introuvable." >&2
    return 1
  fi

  sortie="$(git merge-tree --write-tree --name-only origin/main "$branche" 2>&1)"
  rc=$?
  case "$rc" in
    0)
      printf 'Branche « %s » : se merge proprement dans origin/main.\n' "$branche"
      return 0 ;;
    1) ;;  # conflit — seul cas où l'on poursuit
    *)
      # 128 & consorts : histoires sans ancêtre commun, ref illisible… Ce n'est pas un conflit, et
      # le dire serait un faux positif qui enverrait /mr-fix résoudre un merge impossible.
      printf 'gl_mr_conflict : merge impossible à évaluer (git a rendu %s) — %s\n' \
        "$rc" "$(printf '%s\n' "$sortie" | tail -1)" >&2
      return 1 ;;
  esac

  fichiers="$(printf '%s\n' "$sortie" | awk 'NR == 1 { next } /^$/ { exit } { print }')"
  nb="$(printf '%s\n' "$fichiers" | sed '/^$/d' | wc -l | tr -d '[:space:]')"
  printf "⚠ Branche « %s » en conflit avec origin/main — %s fichier(s) :\n" "$branche" "$nb"
  printf '%s\n' "$fichiers" | sed '/^$/d; s/^/    - /'
  printf '  résolution proposée (merge, jamais rebase — un rebase appellerait un force-push) :\n'
  printf '    git merge origin/main\n'
  return 3
}

# --- Garde-fou de clôture : la session traite-t-elle bien ce ticket ? ----------------------------
# gl_branch_iid [branche] -> imprime l'iid porté par le NOM de la branche (motif
# `<type>/<iid>-<slug>`, docs/10 §2), et rien (code 1) si le nom n'en porte pas — `main`, branche
# hors convention, HEAD détachée. Purement local : aucune lecture GitLab, donc disponible sans
# réseau et vérifiable sans dépôt distant.
gl_branch_iid() {
  local branche="${1:-}" iid
  branche="${branche:-$(git branch --show-current 2>/dev/null)}"
  [ -n "$branche" ] || return 1
  # Le slug est toléré absent (`chore/164`) : c'est l'iid qui porte l'information.
  iid="$(printf '%s\n' "$branche" | sed -n 's|^[a-z]\{1,\}/\([0-9]\{1,\}\)\(-.*\)\{0,1\}$|\1|p')"
  [ -n "$iid" ] || return 1
  printf '%s\n' "$iid"
}

# gl_close_guard <iid> [branche] -> « cette session traite-t-elle vraiment ce ticket ? », à
# consulter AVANT toute écriture de /ticket-finish et /ticket-ship (commit, push, MR, statut,
# temps). C'est le pendant en SORTIE du garde-fou d'entrée de /ticket-start
# (gl_issue_taken, #159) : rien n'empêchait jusqu'ici un `/ticket-finish 158` lancé depuis
# `chore/163-…` de faire basculer #158 « En revue » et d'y logger le temps du travail d'un autre,
# ni une session ayant récupéré la branche d'un collègue de clôturer à sa place.
#
# Deux contrôles, de force très inégale :
#   1. cohérence iid ↔ branche courante — LOCAL, toujours disponible, c'est le contrôle FORT :
#      la branche est le seul témoin fiable de ce que la session travaille réellement ;
#   2. propriété du ticket (assignés, via gl_issue_owner) — une lecture GitLab, contrôle FAIBLE
#      tant que l'équipe partage un même compte glab (le bot, cf. GL_BOT_USERS) : il n'attrape que
#      les tickets assignés à une personne nommée. Il reste utile — c'est exactement le cas d'un
#      ticket pris à la main par un humain — mais ne doit jamais être le seul filet.
#
# Comme gl_behind_main, la fonction est CONSULTATIVE : elle n'écrit rien, imprime son constat et
# laisse la décision à l'appelant — le refus reste franchissable sur demande explicite de
# l'utilisateur (reprise assumée d'un ticket laissé en plan), jamais en silence.
#
# Codes de retour, pour l'appelant :
#   0 = cohérent, rien à signaler         3 = la branche porte un AUTRE ticket
#   4 = ticket assigné à quelqu'un d'autre
#   5 = branche sans iid (`main`, hors convention) — cohérence invérifiable
#   1 = ticket illisible (GitLab injoignable) : verdict partiel, à signaler sans bloquer
#   2 = usage
# Priorité quand plusieurs constats tombent : 3 > 4 > 5. Appeler en
# `bash … close-guard <iid> || verdict=$?` pour ne pas interrompre une clôture sous `set -e`.
gl_close_guard() {
  local iid="$1" branche="${2:-}" iid_branche owner statut assignes moi
  local decalage=0 tiers=0 inverifiable=0
  if [ -z "$iid" ]; then echo "usage: gl_close_guard <iid> [branche]" >&2; return 2; fi
  branche="${branche:-$(git branch --show-current 2>/dev/null)}"
  if [ -z "$branche" ]; then
    echo "gl_close_guard : branche indéterminée (HEAD détachée ?) — la préciser en argument." >&2
    return 2
  fi

  # 1. Cohérence iid ↔ branche (local).
  iid_branche="$(gl_branch_iid "$branche")" || iid_branche=""
  if [ -z "$iid_branche" ]; then
    printf "⚠ branche « %s » : aucun iid dans son nom — cohérence avec #%s invérifiable.\n" "$branche" "$iid"
    printf "  (convention « <type>/<iid>-<slug> », docs/10 §2 ; sur main aucune clôture n'a lieu d'être)\n"
    inverifiable=1
  elif [ "$iid_branche" != "$iid" ]; then
    printf "⚠ décalage ticket ↔ branche : « %s » porte le ticket #%s, pas #%s.\n" "$branche" "$iid_branche" "$iid"
    printf "  clôturer #%s d'ici poserait la MR de #%s sur #%s — statut et temps compris.\n" \
      "$iid" "$iid_branche" "$iid"
    printf "  cette session peut clôturer #%s ; pour #%s, reprendre sa branche (bash scripts/gitlab/lib.sh branch-for %s).\n" \
      "$iid_branche" "$iid" "$iid"
    decalage=1
  else
    printf "ticket #%s ↔ branche « %s » : cohérents.\n" "$iid" "$branche"
  fi

  # 2. Propriété du ticket (une lecture GitLab). Son échec ne masque jamais le constat local.
  local owner_code=0
  owner="$(gl_issue_owner "$iid" 2>/dev/null)" || owner_code=$?
  # Le verdict d'illisibilité se lit sur le CODE DE RETOUR de gl_issue_owner, pas sur la vacuité
  # de sa sortie. Du temps du statut natif les deux se confondaient — un ticket réel portait
  # toujours un statut (« À faire » par défaut), donc deux champs vides trahissaient une réponse
  # dégradée. Depuis que le cycle de vie est un LABEL (#209), un ticket peut légitimement n'en
  # porter aucun (dérive que doctor.sh traque) : conserver le test sur la vacuité classerait un
  # ticket sans label et sans assigné comme « illisible » et bloquerait sa clôture à tort.
  # gl_issue_owner, lui, distingue déjà les vrais échecs (GraphQL muet, ticket introuvable, projet
  # illisible) — le sens du doute continue donc d'aller vers le refus, sur un signal plus juste.
  if [ "$owner_code" -ne 0 ]; then
    printf "  propriété de #%s : indéterminée (ticket illisible — GitLab injoignable ?).\n" "$iid"
    [ "$decalage" -eq 1 ] && return 3
    [ "$inverifiable" -eq 1 ] && return 5
    return 1
  fi
  IFS=$'\t' read -r statut assignes <<< "$owner"
  moi="$(gl_current_user 2>/dev/null)"
  if [ -z "$assignes" ]; then
    printf "propriété de #%s : « %s », aucun assigné (ticket libre).\n" "$iid" "${statut:-statut non posé}"
  elif [ -n "$moi" ] && printf '%s' ",$assignes," | grep -q ",$moi,"; then
    printf "propriété de #%s : « %s », assigné à %s — dont moi (%s).\n" \
      "$iid" "${statut:-statut non posé}" "$assignes" "$moi"
  else
    printf "⚠ #%s appartient à quelqu'un d'autre : « %s », assigné à %s (moi : %s).\n" \
      "$iid" "${statut:-statut non posé}" "$assignes" "${moi:-inconnu}"
    printf "  clôturer à sa place lui pose une MR et un temps qu'il n'a pas demandés.\n"
    tiers=1
  fi

  [ "$decalage" -eq 1 ] && return 3
  [ "$tiers" -eq 1 ] && return 4
  [ "$inverifiable" -eq 1 ] && return 5
  return 0
}

# --- Dispatcher (uniquement quand exécuté directement, pas quand sourcé) -------------------------
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  cmd="${1:-}"; [ "$#" -gt 0 ] && shift
  case "$cmd" in
    require)        gl_require_glab ;;
    current-user)   gl_current_user ;;
    graphql-read)   gl_graphql_read "$@" ;;
    workitem-gid)   gl_workitem_gid "$@" ;;
    set-workflow)   gl_set_workflow "$@" ;;
    reconcile-workflow) gl_reconcile_workflow "$@" ;;
    workflow-slug)  gl_workflow_slug "$@" ;;
    workflow-label) gl_workflow_label "$@" ;;
    workflow-gids)  gl_workflow_gids ;;
    backlog)        gl_backlog "$@" ;;
    backlog-table)  gl_backlog_table "$@" ;;
    issue-brief)    gl_issue_brief "$@" ;;
    issue-owner)    gl_issue_owner "$@" ;;
    issue-taken)    gl_issue_taken "$@" ;;
    current-milestone) gl_current_milestone ;;
    milestones)        gl_milestones ;;
    milestone-issues)  gl_milestone_issues "$@" ;;
    issue-link)     gl_issue_link "$@" ;;
    parent-of)      gl_parent_of "$@" ;;
    subtickets)     gl_subtickets "$@" ;;
    startables)     gl_subtickets "$@" | tail -n +2 | gl_subtickets_startables ;;
    start-brief)    gl_start_brief "$@" ;;
    begin)          gl_begin "$@" ;;
    prio)           gl_prio "$@" ;;
    prio-delay)     gl_prio_delay "$@" ;;
    get-start-date) gl_get_start_date "$@" ;;
    get-time-spent) gl_get_time_spent "$@" ;;
    elapsed-days)   gl_elapsed_days "$@" ;;
    set-dates)      gl_set_dates "$@" ;;
    start-dates)    gl_start_dates "$@" ;;
    log-time)       gl_log_time "$@" ;;
    mr-state)       gl_mr_state "$@" ;;
    project-humans) gl_project_humans "$@" ;;
    pick-reviewer)  gl_pick_reviewer "$@" ;;
    mr-iid)         gl_mr_iid "$@" ;;
    mr-reviewers)   gl_mr_reviewers "$@" ;;
    set-reviewer)   gl_set_reviewer "$@" ;;
    review-queue)   gl_review_queue "$@" ;;
    cleanup-merged) gl_cleanup_merged "$@" ;;
    worktree-done)  gl_worktree_done "$@" ;;
    reconcile-en-cours) gl_reconcile_en_cours "$@" ;;
    reprendre-en-cours) gl_reprendre_en_cours "$@" ;;
    reprises)       gl_reprises "$@" ;;
    branch-for)     gl_branch_for "$@" ;;
    start-branch)   gl_start_branch "$@" ;;
    sync-main)      gl_sync_main "$@" ;;
    behind-main)    gl_behind_main "$@" ;;
    mr-conflict)    gl_mr_conflict "$@" ;;
    branch-iid)     gl_branch_iid "$@" ;;
    close-guard)    gl_close_guard "$@" ;;
    get-description)    gl_get_description "$@" ;;
    set-description)    gl_set_description "$@" ;;
    get-mr-description) gl_get_mr_description "$@" ;;
    set-mr-description) gl_set_mr_description "$@" ;;
    roundtrip-description) gl_roundtrip_description "$@" ;;
    issue-title)    gl_issue_title "$@" ;;
    create-mr)      gl_create_mr "$@" ;;
    issue-note)     gl_issue_note "$@" ;;
    pipeline-latest)      gl_pipeline_latest "$@" ;;
    pipeline-status)      gl_pipeline_status "$@" ;;
    pipeline-failed-jobs) gl_pipeline_failed_jobs "$@" ;;
    job-trace)            gl_job_trace "$@" ;;
    pipeline-wait)        gl_pipeline_wait "$@" ;;
    project-enc)    gl_project_enc ;;
    project-id)     gl_project_id ;;
    host)           gl_host ;;
    slug)           gl_slug "$@" ;;
    branch-prefix)  gl_branch_prefix "$@" ;;
    *)
      echo "usage: bash scripts/gitlab/lib.sh <sous-commande> [args]" >&2
      echo "  require | current-user | workitem-gid <iid>" >&2
      echo "  Cycle de vie (labels workflow::*, cf. contrat en tête de lib.sh) :" >&2
      echo "    set-workflow <iid> <valeur>   (pose la valeur ET retire les cinq autres, en un appel)" >&2
      echo "                                  valeur = « À faire »… ou le slug « a-faire »… ; sortie toujours en libellé" >&2
      echo "    workflow-slug <valeur>        (normalise en slug)   workflow-label <slug> (rend le libellé)" >&2
      echo "    workflow-gids                 (les six labels du scope : slug/GID, dérivés par nom)" >&2
      echo "    reconcile-workflow [--check] [<iid>…]" >&2
      echo "                                  (pose « Terminé » sur les tickets soldés restés actifs ;" >&2
      echo "                                   sans iid : balaie tout le backlog fermé. N'écrase jamais" >&2
      echo "                                   « Abandonné »/« Doublon ». Best-effort, jamais bloquant)" >&2
      echo "  backlog [opened|closed|all]        (JSON brut du backlog)" >&2
      echo "  backlog-table [opened|closed|all]  (table plate compacte TSV — voir en-tête gl_backlog_table)" >&2
      echo "  issue-brief <iid>                  (titre + labels + critères d'acceptation)" >&2
      echo "  issue-owner <iid>                  (cycle de vie + assignés du ticket, TSV — vide = libre)" >&2
      echo "  issue-taken <iid> [username]       (0 + assignés si le ticket est « En cours » chez quelqu'un d'autre)" >&2
      echo "  current-milestone                  (titre du milestone de la phase courante — actif le plus ancien non soldé)" >&2
      echo "  milestones                         (tous les milestones : titre/état/dates/avancement, TSV)" >&2
      echo "  milestone-issues <titre-exact>     (tickets d'un milestone : iid/statut/type/agent/prio/titre, TSV)" >&2
      echo "  slug <titre> | branch-prefix <type>" >&2
      echo "  project-enc | project-id | host   (chemin encodé, id numérique, hôte GitLab du remote)" >&2
      echo "  Sous-tickets (découpage parent/lots, docs/10 §5.1) :" >&2
      echo "    issue-link <iid> <iid-cible>    (lie deux tickets — relates to, idempotent)" >&2
      echo "    parent-of <iid>                 (iid du parent si <iid> est un sous-ticket)" >&2
      echo "    subtickets <iid-parent>         (checklist ## Sous-tickets : iid/coche/statut/par/titre)" >&2
      echo "    startables <iid-parent>         (lots « À faire » démarrables maintenant)" >&2
      echo "  Démarrage de ticket (/ticket-start) :" >&2
      echo "    start-brief <iid>            (préflight en une lecture : pré-requis, arbre sale signalé, brief, parent/sous-ticket, branche proposée)" >&2
      echo "    branch-for <iid>             (nom de la branche de travail du ticket)" >&2
      echo "    start-branch <branche>       (place le dépôt sur la branche — clone principal ou worktree lié, idempotent)" >&2
      echo "    begin <iid> [username]       (assignation + « En cours » + dates en une mutation groupée)" >&2
      echo "  Dates & temps :" >&2
      echo "    start-dates <iid>            (début=aujourd'hui + échéance selon prio)" >&2
      echo "    set-dates <iid> [début] [échéance]   get-start-date <iid>" >&2
      echo "    prio <iid>   prio-delay <prio>   elapsed-days <date>" >&2
      echo "    log-time <iid> <durée> [résumé]   get-time-spent <iid>" >&2
      echo "  Descriptions (aller-retour fidèle aux octets — à utiliser au lieu d'improviser une lecture) :" >&2
      echo "    get-description <iid>              (description du ticket, UTF-8 intact, sur stdout)" >&2
      echo "    set-description <iid> <fichier>    (remplace la description du ticket par le fichier)" >&2
      echo "    get-mr-description <mr>            (idem pour une MR)" >&2
      echo "    set-mr-description <mr> <fichier>  (idem pour une MR)" >&2
      echo "    roundtrip-description <iid>        (valide la fidélité : lit/réécrit/relit et compare les octets)" >&2
      echo "  Création depuis un FICHIER (jamais de description multi-ligne ni de \$(cat …) sur la ligne de commande) :" >&2
      echo "    create-mr <iid> <fichier> [branche]  (MR en Draft vers main, titre du ticket, description du fichier ;" >&2
      echo "                                         idempotent : met à jour la MR ouverte existante au lieu d'échouer)" >&2
      echo "    issue-note <iid> <fichier>          (poste le fichier en commentaire sur le ticket)" >&2
      echo "    issue-title <iid>                   (titre du ticket, UTF-8 intact)" >&2
      echo "  Branches :" >&2
      echo "    cleanup-merged [--auto]     (supprime les branches locales dont la MR est mergée ; --auto = muet si rien)" >&2
      echo "    sync-main [--check]         (avance main du clone principal sur origin/main, fast-forward seul ; 0=à jour/fait, 3=divergent, 4=arbre sale)" >&2
      echo "    mr-state <branche>          (opened|closed|merged)" >&2
      echo "    worktree-done <iid> [branche] (fini|actif|inconnu + sha de merge + raison — fin de vie d'un worktree)" >&2
      echo "    behind-main [branche]       (retard sur origin/main + conflit probable ; 0=à jour, 3=en retard, 4=+conflit)" >&2
      echo "  Tickets « En cours » orphelins (lecture seule — signale, ne répare rien) :" >&2
      echo "    reconcile-en-cours [--check] [--auto] [--tsv] [--sauf <iid>]" >&2
      echo "                                (« quelqu'un s'occupe-t-il encore de ce ticket ? » : vivant / orphelin /" >&2
      echo "                                 hors de portée, avec sa source — carte du pilote, ou déduction annoncée." >&2
      echo "                                 Portée : les worktrees de CETTE machine. 0=rien à signaler, 3=orphelin(s))" >&2
      echo "    reprendre-en-cours [--check] [--force] <iid>…" >&2
      echo "                                (LE GESTE : remet l'orphelin « À faire » ET le libère, en une mutation." >&2
      echo "                                 N'écrit que dans GitLab — worktree, branche et commits intacts." >&2
      echo "                                 Refuse un ticket vivant, hors de portée, ou déjà repris $GL_REPRISES_MAX fois" >&2
      echo "                                 (--force lève les deux derniers). 0=repris, 3=refusé, 1=échec)" >&2
      echo "    reprises [<iid>]            (la trace : d'où venait chaque ticket repris, et combien de fois)" >&2
      echo "    mr-conflict [branche]       (conflit RÉEL avec origin/main via merge-tree ; 0=propre, 3=conflit)" >&2
      echo "  Garde-fou de clôture (session ↔ ticket, avant toute écriture de /ticket-finish|ship) :" >&2
      echo "    branch-iid [branche]        (iid porté par le nom de la branche ; rien si hors convention)" >&2
      echo "    close-guard <iid> [branche] (0=cohérent, 3=autre ticket, 4=ticket d'un tiers, 5=branche sans iid, 1=ticket illisible)" >&2
      echo "  Revue best-effort (file de revue ; relecteur posé à la main seulement) :" >&2
      echo "    review-queue                     (MR ouvertes en attente de revue, la plus ancienne d'abord — TSV)" >&2
      echo "    set-reviewer [mr|branche] [user] (pose un relecteur humain ≠ auteur — appel MANUEL, aucune commande ne l'invoque)" >&2
      echo "    mr-reviewers [mr|branche]        (relecteurs posés, CSV — vide si aucun)" >&2
      echo "    pick-reviewer [auteur] [graine]  (choisit un relecteur humain, rotation par graine)" >&2
      echo "    project-humans [access-min]      (membres humains éligibles : username/niveau, TSV)" >&2
      echo "    mr-iid [mr|branche]              (iid de la MR ouverte — défaut : branche courante)" >&2
      echo "  Pipelines CI :" >&2
      echo "    pipeline-latest <ref>            (id/status/sha/url du dernier pipeline de la branche)" >&2
      echo "    pipeline-status <pipeline-id>    (statut courant)" >&2
      echo "    pipeline-failed-jobs <pipeline-id>  (jobs rouges : id/name/stage/failure_reason)" >&2
      echo "    job-trace <job-id> [lignes]      (queue de la trace du job)" >&2
      echo "    pipeline-wait <pipeline-id> [timeout-s]  (suit jusqu'au verdict, 0=success)" >&2
      exit 2 ;;
  esac
fi
