#!/usr/bin/env bash
# Inventaire de départ de la migration GitLab → GitHub (ticket #336, parent #335).
#
# LECTURE SEULE et REJOUABLE : n'écrit rien, ni sur GitLab, ni sur GitHub, ni sur le disque.
# C'est délibéré et c'est le contrat — le lot d'import (#340) s'en sert comme référence de recette,
# et une référence de recette qui modifie ce qu'elle mesure ne vaut rien.
#
# Il répond à trois questions, dans cet ordre :
#
#   1. QUE FAUT-IL IMPORTER ?  — tickets, MR, milestones, labels côté GitLab, et surtout la plage
#      d'iid avec ses TROUS : un iid absent (ticket supprimé) devra être consommé par un objet
#      bouche-trou côté GitHub, sans quoi tout ce qui suit décale.
#   2. QU'EST-CE QUI CASSE SI ÇA DÉCALE ? — les commits de `origin/main` porteurs d'un
#      `Refs #<n>` / `Closes #<n>`, et la plage qu'ils couvrent. Sur GitHub ces références sont
#      rendues comme des LIENS : décalées, elles ne deviennent pas mortes mais FAUSSES, ce qui est
#      pire et irréversible une fois l'historique poussé (docs/27 §6).
#   3. LA CIBLE EST-ELLE ENCORE VIERGE ? — le prérequis dur. GitHub partage UNE SEULE séquence
#      entre issues et PR, donc un seul objet créé sur le dépôt cible consomme `#1` et rend la
#      plage `#2`→`#333` définitivement inatteignable.
#
# Usage :  bash scripts/migration/inventaire.sh [--source|--cible] [--tsv]
#   --source : n'interroge que GitLab (ne demande aucun accès GitHub)
#   --cible  : n'interroge que GitHub (le contrôle de numérotation, seul)
#   --tsv    : sortie machine « clé <TAB> valeur », une paire par ligne, sans couleur
#
# Codes de sortie — le verdict porte sur la CIBLE, pas sur la quantité à importer :
#   0 : cible vierge (ou non interrogée avec --source) — l'import peut être planifié
#   1 : erreur d'exécution (glab/gh absent ou non authentifié, dépôt illisible)
#   2 : usage
#   3 : INDÉTERMINÉ — la numérotation de la cible n'a pas pu être lue (jeton trop étroit).
#       Ne pas confondre avec 0 : « on n'a pas pu regarder » n'est pas « c'est vide ».
#   4 : cible NON VIERGE — au moins un numéro est consommé, la plage n'est plus préservable.
#
# Le dépôt cible est surchargeable par MAESTRO_GITHUB_REPO (défaut : le miroir monté par #332).
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
racine="$(cd "$here/../.." && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$racine/scripts/gitlab/lib.sh"

GH_REPO="${MAESTRO_GITHUB_REPO:-automatemaestro-create/maestro}"

portee="tout"
format="texte"
for arg in "$@"; do
  case "$arg" in
    --source) portee="source" ;;
    --cible)  portee="cible" ;;
    --tsv)    format="tsv" ;;
    -h|--help) sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "argument inconnu : $arg (voir --help)" >&2; exit 2 ;;
  esac
done

if [ "$format" = "texte" ] && [ -t 1 ]; then
  C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_B=$'\033[1m'; C_0=$'\033[0m'
else
  C_G=''; C_Y=''; C_R=''; C_B=''; C_0=''
fi

# largeur <chaîne> -> nombre de colonnes AFFICHÉES, et non d'octets.
# `printf '%-34s'` et `${#s}` comptent des octets : une clé accentuée en prend deux par accent, et
# la colonne des valeurs part en escalier — même piège que la vue d'orchestration (#325, docs/10
# §11.3). On retire les octets de continuation UTF-8 (0x80-0xBF), ce qui ramène le compte d'octets
# au compte de caractères.
largeur() {
  local nu
  nu="$(printf '%s' "$1" | LC_ALL=C sed 's/[\x80-\xBF]//g')"
  LC_ALL=C printf '%s' "${#nu}"
}

# Une seule paire clé/valeur : rendue en TSV ou en ligne alignée, selon --tsv. Toute donnée de
# l'inventaire passe par ici, pour que les deux formats ne puissent pas diverger.
KV_COL=34
kv() {
  if [ "$format" = "tsv" ]; then
    printf '%s\t%s\n' "$1" "$2"
  else
    local n pad=""
    n="$(largeur "$1")"
    [ "$n" -lt "$KV_COL" ] && pad="$(printf '%*s' "$((KV_COL - n))" '')"
    printf '  %s%s %s\n' "$1" "$pad" "$2"
  fi
}
section() { [ "$format" = "tsv" ] || printf '\n%s%s%s\n' "$C_B" "$1" "$C_0"; }
note()    { [ "$format" = "tsv" ] || printf '  %s\n' "$1"; }

# --- Côté GitLab (la source) ---------------------------------------------------------------------

# inv_iids -> tous les iid de tickets, un par ligne, triés numériquement.
#
# Pagination GraphQL explicite, pour deux raisons distinctes :
#   - gl_backlog plafonne à `first: 100` (il sert /backlog, pas un inventaire), et un inventaire
#     qui s'arrête à 100 tickets est un inventaire faux ;
#   - le JSON REST d'un ticket porte PLUSIEURS `"iid"` — le sien, mais aussi celui du milestone
#     imbriqué — donc un `grep '"iid"'` sur /issues compte presque le double (677 pour 341
#     tickets, mesuré). Un nœud GraphQL qui ne demande que `iid` n'en porte qu'un, par
#     construction : la requête dit ce qu'elle veut au lieu de le filtrer après coup.
inv_iids() {
  local curseur="" apres out lot tours=0
  while :; do
    [ -n "$curseur" ] && apres=', after: "'"$curseur"'"' || apres=""
    out="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { workItems(state: all, first: 100'"$apres"') { pageInfo { endCursor hasNextPage } nodes { iid } } } }')" || return 1
    lot="$(printf '%s' "$out" | grep -o '"iid":"[0-9]\+"' | grep -o '[0-9]\+')"
    [ -z "$lot" ] && break
    printf '%s\n' "$lot"
    printf '%s' "$out" | grep -q '"hasNextPage":true' || break
    curseur="$(printf '%s' "$out" | grep -o '"endCursor":"[^"]*"' | head -1 | cut -d'"' -f4)"
    [ -z "$curseur" ] && break
    tours=$((tours + 1))
    [ "$tours" -gt 20 ] && break   # garde-fou : 2 000 tickets, très au-delà du besoin
  done | sort -n
}

inventaire_source() {
  section "1. Source — GitLab ($GL_PROJECT)"

  local counts issues_n mr_n
  counts="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { issues { count } mergeRequests { count } } }')" || {
    echo "lecture GraphQL impossible — glab est-il authentifié ?" >&2; return 1; }
  issues_n="$(printf '%s' "$counts" | grep -o '"issues":{"count":[0-9]\+' | grep -o '[0-9]\+$')"
  mr_n="$(printf '%s' "$counts" | grep -o '"mergeRequests":{"count":[0-9]\+' | grep -o '[0-9]\+$')"

  local enc milestones_n labels_n
  enc="$(gl_project_enc)"
  # `grep -c` compte les LIGNES porteuses d'une correspondance, pas les correspondances : sur une
  # réponse REST tenant sur une seule ligne il rend 1, quoi qu'elle contienne. D'où `grep -o | wc -l`.
  milestones_n="$(glab api "projects/$enc/milestones?per_page=100" 2>/dev/null | grep -o '"iid":[0-9]\+' | wc -l | tr -d ' ')"
  labels_n="$(glab api "projects/$enc/labels?per_page=100" 2>/dev/null | grep -o '"name":"[^"]*"' | wc -l | tr -d ' ')"

  kv "tickets" "${issues_n:-?}"
  kv "merge requests" "${mr_n:-?}"
  kv "milestones" "$milestones_n"
  kv "labels" "$labels_n"

  section "2. Séquence des iid — ce que l'import doit reconstituer"
  local iids min max presents attendus trous premiers
  iids="$(inv_iids)"
  if [ -z "$iids" ]; then
    printf '  %s✗%s aucun iid lu — inventaire interrompu.\n' "$C_R" "$C_0" >&2
    return 1
  fi
  min="$(printf '%s\n' "$iids" | head -1)"
  max="$(printf '%s\n' "$iids" | tail -1)"
  presents="$(printf '%s\n' "$iids" | wc -l | tr -d ' ')"
  attendus=$((max - min + 1))
  trous=$((attendus - presents))
  kv "premier iid" "$min"
  kv "dernier iid" "$max"
  kv "iid présents" "$presents"
  kv "iid attendus sur la plage" "$attendus"
  kv "trous à combler" "$trous"
  if [ "$trous" -gt 0 ]; then
    premiers="$(printf '%s\n' "$iids" | awk -v mn="$min" -v mx="$max" '
      { vu[$1] = 1 }
      END { for (i = mn; i <= mx; i++) if (!(i in vu)) { out = (out == "" ? i : out " " i); n++; if (n >= 12) { out = out " …"; break } } print out }')"
    kv "trous (12 premiers)" "$premiers"
  fi
  # Le premier objet créé sur GitHub prendra le numéro 1, quoi qu'on fasse : si l'iid 1 n'existe
  # pas côté GitLab, il faut donc prévoir explicitement ce que devient ce numéro (bouche-trou, ou
  # ticket de traçabilité de l'import). C'est le genre de détail qui ne se voit qu'une fois.
  if [ "$min" -gt 1 ]; then
    kv "objets à créer côté GitHub" "$((max))  (dont $((min - 1)) avant le premier ticket réel)"
  else
    kv "objets à créer côté GitHub" "$max"
  fi

  section "3. Historique git — ce qui casse si la séquence décale"
  local refs commits_n refs_min refs_max refs_distincts
  # Lecture sur origin/main, pas sur HEAD : c'est l'historique publié qui porte les liens.
  refs="$(git -C "$racine" log origin/main --pretty=%B 2>/dev/null \
          | grep -oE '(Refs|Closes) #[0-9]+' | grep -o '[0-9]\+' | sort -n)"
  commits_n="$(git -C "$racine" log origin/main --extended-regexp --grep='(Refs|Closes) #[0-9]+' --oneline 2>/dev/null | wc -l | tr -d ' ')"
  if [ -z "$refs" ]; then
    note "aucune référence trouvée (origin/main est-il à jour ?)"
  else
    refs_min="$(printf '%s\n' "$refs" | head -1)"
    refs_max="$(printf '%s\n' "$refs" | tail -1)"
    refs_distincts="$(printf '%s\n' "$refs" | sort -u | wc -l | tr -d ' ')"
    kv "commits porteurs d'une référence" "$commits_n"
    kv "références (occurrences)" "$(printf '%s\n' "$refs" | wc -l | tr -d ' ')"
    kv "tickets distincts référencés" "$refs_distincts"
    kv "plage référencée" "#$refs_min → #$refs_max"
  fi
}

# --- Côté GitHub (la cible) ----------------------------------------------------------------------

# Rend 0 (vierge), 3 (indéterminé) ou 4 (non vierge). Le contrôle porte sur /issues ET /pulls :
# /issues suffit en théorie (GitHub y renvoie AUSSI les PR), mais un prérequis à sens unique se
# vérifie deux fois plutôt qu'une.
inventaire_cible() {
  section "4. Cible — GitHub ($GH_REPO)"

  if ! command -v gh >/dev/null 2>&1; then
    printf '  %s✗%s gh absent — installer le CLI GitHub.\n' "$C_R" "$C_0" >&2
    return 1
  fi

  local depot visibilite
  depot="$(gh api "repos/$GH_REPO" 2>/dev/null)"
  if [ -z "$depot" ]; then
    kv "dépôt" "illisible (jeton sans accès, ou dépôt inexistant)"
    printf '  %s⚠%s numérotation INDÉTERMINÉE.\n' "$C_Y" "$C_0"
    return 3
  fi
  visibilite="$(printf '%s' "$depot" | grep -o '"visibility":"[^"]*"' | head -1 | cut -d'"' -f4)"
  kv "dépôt" "$GH_REPO"
  kv "visibilité" "${visibilite:-?}"

  # Deux champs que `GET /repos/:repo` livre même à un jeton sans droit sur les issues. Ils ne
  # tranchent pas le prérequis — `open_issues_count` ne compte que les objets OUVERTS, et un
  # numéro consommé puis fermé reste consommé — mais ils valent mieux que rien quand le contrôle
  # ferme rend « indéterminé », et `has_issues` est un prérequis à part entière : issues
  # désactivées, il n'y a rien à importer nulle part.
  local issues_actives ouverts cree
  issues_actives="$(printf '%s' "$depot" | grep -o '"has_issues":[a-z]*' | head -1 | cut -d: -f2)"
  ouverts="$(printf '%s' "$depot" | grep -o '"open_issues_count":[0-9]\+' | head -1 | grep -o '[0-9]\+')"
  cree="$(printf '%s' "$depot" | grep -o '"created_at":"[^"]*"' | head -1 | cut -d'"' -f4)"
  kv "issues activées" "${issues_actives:-?}"
  kv "issues + PR ouvertes (indice)" "${ouverts:-?}"
  kv "dépôt créé le" "${cree:-?}"
  if [ "$issues_actives" = "false" ]; then
    printf '  %s✗%s les issues sont DÉSACTIVÉES sur le dépôt cible — à activer avant tout import.\n' "$C_R" "$C_0"
  fi

  local sortie_i code_i sortie_p code_p
  sortie_i="$(gh api "repos/$GH_REPO/issues?state=all&per_page=100" 2>&1)"; code_i=$?
  sortie_p="$(gh api "repos/$GH_REPO/pulls?state=all&per_page=100" 2>&1)"; code_p=$?

  local n_i="?" n_p="?" indetermine=0
  if [ "$code_i" -eq 0 ]; then
    n_i="$(printf '%s' "$sortie_i" | grep -o '"number":[0-9]\+' | wc -l | tr -d ' ')"
  else
    indetermine=1
  fi
  if [ "$code_p" -eq 0 ]; then
    n_p="$(printf '%s' "$sortie_p" | grep -o '"number":[0-9]\+' | wc -l | tr -d ' ')"
  else
    indetermine=1
  fi
  kv "issues + PR lues (/issues)" "$n_i"
  kv "PR lues (/pulls)" "$n_p"

  if [ "$indetermine" -eq 1 ]; then
    printf '  %s⚠%s numérotation INDÉTERMINÉE — le jeton ne peut pas lire les issues du dépôt.\n' "$C_Y" "$C_0"
    note "réponse : $(printf '%s' "$sortie_i$sortie_p" | grep -o '"message":"[^"]*"' | head -1 | cut -d'"' -f4)"
    note "ce n'est PAS un jeton de plus à créer : le jeton du poste (#332, Actions: Read seul,"
    note "rangé dans le GH_CONFIG_DIR du projet — #334) est déjà le bon acteur. Lui ajouter"
    note "Issues et Pull requests en lecture/écriture SUFFIT, et un PAT fine-grained s'édite en"
    note "place : la chaîne ne change pas, donc aucun « gh auth login » à refaire."
    note "Ne pas toucher au second jeton de #332, celui du miroir : il vit côté GitLab, il écrit"
    note "du code et non des tickets, et il meurt avec le miroir à la bascule."
    if [ "${ouverts:-1}" = "0" ]; then
      note ""
      note "présomption seulement : 0 objet OUVERT, et un miroir push ne crée ni issue ni PR."
      note "un numéro consommé puis fermé resterait invisible ici — ça n'est pas une vérification."
    fi
    return 3
  fi

  if [ "$n_i" = "0" ] && [ "$n_p" = "0" ]; then
    printf '  %s✓%s numérotation VIERGE — la plage est encore préservable.\n' "$C_G" "$C_0"
    return 0
  fi

  local plus_haut
  plus_haut="$(printf '%s' "$sortie_i$sortie_p" | grep -o '"number":[0-9]\+' | grep -o '[0-9]\+' | sort -n | tail -1)"
  printf '  %s✗%s numérotation NON VIERGE — numéro le plus haut consommé : #%s\n' "$C_R" "$C_0" "${plus_haut:-?}"
  note "la plage d'origine n'est plus atteignable telle quelle sur ce dépôt."
  return 4
}

# --- Déroulé -------------------------------------------------------------------------------------

if [ "$portee" != "cible" ]; then
  gl_require_glab || exit 1
  inventaire_source || exit 1
fi

code=0
if [ "$portee" != "source" ]; then
  inventaire_cible || code=$?
fi

section "Verdict"
case "$code" in
  0) [ "$portee" = "source" ] \
       && note "cible non interrogée (--source)." \
       || printf '  %s✓%s cible vierge — l'"'"'import peut être planifié (lot #340).\n' "$C_G" "$C_0" ;;
  3) printf '  %s⚠%s indéterminé — jeton insuffisant. Le prérequis de #336 reste OUVERT.\n' "$C_Y" "$C_0" ;;
  4) printf '  %s✗%s cible non vierge — arbitrage nécessaire avant tout import.\n' "$C_R" "$C_0" ;;
  *) printf '  %s✗%s erreur d'"'"'exécution.\n' "$C_R" "$C_0" ;;
esac
exit "$code"
