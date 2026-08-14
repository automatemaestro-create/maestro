#!/usr/bin/env bash
# Export FIDÈLE du backlog GitLab en JSON local (ticket #337, parent #335).
#
# LECTURE SEULE côté GitLab, REJOUABLE, et surtout FIDÈLE AUX OCTETS. C'est la source unique du lot
# d'import (#340) et la seule chose qui survivra si GitLab devient indisponible : tout ce qui n'est
# pas ici sera perdu à la bascule.
#
# LE PARTI PRIS, qui explique toute la suite : **on ne reformate rien**. Chaque ticket est écrit sur
# disque avec les OCTETS EXACTS que l'API a rendus — jamais décodés, jamais ré-encodés, jamais
# ré-échappés. Un export qui reconstruit son JSON doit re-sérialiser 330 descriptions pleines
# d'accents, d'em-dashes et de blocs de code : c'est précisément là que le mojibake de #141 s'est
# introduit (`glab … | python`, `sys.stdin` en cp1252 sous Windows). Ici la question ne se pose pas,
# parce qu'aucune étape ne DÉCODE : le découpage (awk sous LC_ALL=C) compte des accolades, il ne lit
# pas du texte. Les octets traversent, ou ils ne traversent pas — et la vérification le dit.
#
# Ce que l'export capture, ticket par ticket (les sept données du tableau de #337) :
#   iid, titre, état · description, commentaires (auteur, date, corps, système ou non) ·
#   labels workflow::/type::/agent::/prio:: · milestone · dates début/échéance ·
#   temps passé (total ET timelogs détaillés) · liens (relates_to) et assignés.
#
# Ce qu'il produit sous .maestro/migration/ (gitignoré — artefact de travail, pas un livrable) :
#   pages/page-NN.json  réponses GraphQL BRUTES, telles que reçues. C'est la pièce à conviction :
#                       elles permettent de re-découper hors ligne (--hors-ligne) sans réinterroger
#                       GitLab, donc de rejouer l'assemblage sans le fetch.
#   backlog.jsonl       UN TICKET PAR LIGNE, l'objet JSON tel quel. Format choisi pour l'import :
#                       streamable, reprenable ticket par ticket, et diffable.
#   milestones.json     référentiel brut (REST) — l'import doit recréer les milestones AVANT les
#   labels.json         tickets, et les tickets ne portent que le NOM du milestone comme jointure.
#   trous.txt           les iid ABSENTS de la plage : ce que l'import devra combler pour que
#                       « Refs #123 » de l'historique git pointe encore sur le bon objet (docs/27 §6).
#   manifeste.tsv       index machine, une ligne par ticket (voir l'en-tête du fichier).
#   resume.txt          le verdict, en clair.
#
# Usage :  bash scripts/migration/export-gitlab.sh [options]
#   --hors-ligne     ne rien demander à GitLab : ré-assemble et re-vérifie les pages déjà là
#   --verifier       vérification seule d'un export existant (n'écrit ni page ni jsonl)
#   --sortie <dir>   répertoire de sortie (défaut : .maestro/migration/ à la racine du dépôt)
#   --page <n>       tickets par requête (défaut 50 ; au-delà, la complexité GraphQL casse)
#   --echantillon <n> nombre de tickets re-lus par une SECONDE voie pour la preuve d'octets (défaut 3)
#   --tsv            sortie machine « clé <TAB> valeur », sans couleur
#
# Codes de sortie — « produit » et « digne de confiance » sont deux questions distinctes :
#   0 : export complet ET vérifié
#   1 : erreur d'exécution (glab absent/non authentifié, API illisible, page en erreur)
#   2 : usage
#   3 : export PRODUIT mais VÉRIFICATION EN ÉCHEC — ne pas s'en servir pour importer.
#       Le distinguer de 1 est le point : un export qu'on croit bon est plus dangereux qu'un export
#       absent, et c'est irréversible une fois les 330 tickets créés en face.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
racine="$(cd "$here/../.." && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$racine/scripts/gitlab/lib.sh"

SORTIE="${MAESTRO_MIGRATION_DIR:-$racine/.maestro/migration}"
PAGE=50
ECHANTILLON=3
mode="complet"
format="texte"

while [ $# -gt 0 ]; do
  case "$1" in
    --hors-ligne)  mode="hors-ligne" ;;
    --verifier)    mode="verifier" ;;
    --sortie)      SORTIE="${2:-}"; shift ;;
    --page)        PAGE="${2:-}"; shift ;;
    --echantillon) ECHANTILLON="${2:-}"; shift ;;
    --tsv)         format="tsv" ;;
    -h|--help)     sed -n '2,48p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "argument inconnu : $1 (voir --help)" >&2; exit 2 ;;
  esac
  shift
done

case "$PAGE" in ''|*[!0-9]*) echo "--page attend un entier" >&2; exit 2 ;; esac
case "$ECHANTILLON" in ''|*[!0-9]*) echo "--echantillon attend un entier" >&2; exit 2 ;; esac
[ -n "$SORTIE" ] || { echo "--sortie attend un chemin" >&2; exit 2; }

if [ "$format" = "texte" ] && [ -t 1 ]; then
  C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_B=$'\033[1m'; C_0=$'\033[0m'
else
  C_G=''; C_Y=''; C_R=''; C_B=''; C_0=''
fi

PAGES_DIR="$SORTIE/pages"
JSONL="$SORTIE/backlog.jsonl"
MANIFESTE="$SORTIE/manifeste.tsv"
TROUS="$SORTIE/trous.txt"
RESUME="$SORTIE/resume.txt"
MOJIBAKE_SRC="$SORTIE/mojibake-source.txt"

# largeur/kv/section/note : même rendu que scripts/migration/inventaire.sh, pour que les deux étapes
# de la migration se lisent pareil. `largeur` compte des COLONNES et non des octets (un accent en
# vaut deux en UTF-8, et la colonne des valeurs part en escalier) — cf. #325, docs/10 §11.3.
largeur() {
  local nu
  nu="$(printf '%s' "$1" | LC_ALL=C sed 's/[\x80-\xBF]//g')"
  LC_ALL=C printf '%s' "${#nu}"
}
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

# --- La requête ----------------------------------------------------------------------------------
# Une seule requête décrit tout ce qui doit survivre. Trois choix à connaître avant d'y toucher :
#
#   1. `pageInfo` est demandé AVANT `nodes`, et ce n'est pas cosmétique : le découpage repère le
#      tableau de tickets par la PREMIÈRE occurrence de `"nodes":[` dans la réponse. pageInfo n'en
#      contient aucune, donc la première est forcément la bonne. Inverser l'ordre casserait le
#      découpage en silence.
#   2. Le milestone ne rend que son `title` — surtout pas son `dueDate`. Le widget des dates en a un
#      aussi, et deux `"dueDate"` dans le même objet rendraient l'extraction du manifeste ambiguë.
#      Les métadonnées de milestone vivent dans leur propre fichier, où elles ne se marchent pas
#      dessus, et le titre suffit comme clé de jointure.
#   3. Les notes sont demandées AVEC les tickets, pas dans une seconde passe de 330 requêtes. La
#      contrepartie est la complexité GraphQL, qui plafonne la taille de page : 50 passe, 100 non.
requete_page() {
  local apres="$1" taille="$2" frag=""
  [ -n "$apres" ] && frag=', after: "'"$apres"'"'
  printf '%s' '{ project(fullPath:"'"$GL_PROJECT"'") { workItems(state: all, first: '"$taille$frag"') {
    pageInfo { endCursor hasNextPage }
    nodes {
      iid title state createdAt updatedAt closedAt webUrl
      workItemType { name }
      author { username }
      description
      widgets {
        ... on WorkItemWidgetLabels { labels { nodes { title } } }
        ... on WorkItemWidgetMilestone { milestone { title } }
        ... on WorkItemWidgetStartAndDueDate { startDate dueDate }
        ... on WorkItemWidgetTimeTracking { timeEstimate totalTimeSpent
          timelogs { nodes { timeSpent spentAt summary user { username } } } }
        ... on WorkItemWidgetAssignees { assignees { nodes { username } } }
        ... on WorkItemWidgetLinkedItems { linkedItems(first: 100) { nodes { linkType workItem { iid } } } }
        ... on WorkItemWidgetNotes { discussions(first: 100) { nodes { notes { nodes {
          author { username } createdAt body system } } } } }
      }
    }
  } } }'
}

# --- Étape 1 : le fetch --------------------------------------------------------------------------
# Écrit pages/page-NN.json. Rien d'autre ne parle à GitLab, et rien ici ne parle d'autre chose que
# de LIRE : gl_graphql_read est réservé aux lectures (cf. son en-tête dans lib.sh), et aucune
# mutation n'est construite nulle part dans ce fichier. C'est le contrat « sans aucune écriture ».
# page_en_erreur <réponse> -> vrai si la réponse GraphQL porte des erreurs. Fonction plutôt que
# `case` en ligne : elle sert dans une condition composée, où un `case` ne se compose pas.
page_en_erreur() {
  case "$1" in *'"errors":'*) return 0 ;; *) return 1 ;; esac
}

fetch_pages() {
  section "1. Lecture de GitLab ($GL_PROJECT)"
  rm -rf "$PAGES_DIR"
  mkdir -p "$PAGES_DIR" || return 1

  local curseur="" num=0 out fichier suivant taille="$PAGE" essai messages reduits=0
  while :; do
    num=$((num + 1))
    if [ "$num" -gt 200 ]; then
      echo "plus de 200 pages — pagination probablement en boucle, arrêt." >&2; return 1
    fi

    # Une page se retente, et se RÉTRÉCIT si elle insiste. GitLab borne le TEMPS d'exécution d'une
    # requête, pas seulement sa complexité : la requête ci-dessus passe à 50 tickets sur la plupart
    # des tranches du backlog et expire sur celles qui portent beaucoup de discussions
    # (« Timeout on DiscussionConnection.nodes »). C'est une propriété des DONNÉES, pas de la
    # requête — donc ni une taille de page fixe plus petite (qui ralentirait tout l'export pour
    # quelques tranches), ni un simple retry (qui rejouerait la même requête trop lourde) ne
    # suffisent. On retente d'abord à l'identique, l'expiration étant souvent conjoncturelle, puis
    # on divise la taille par deux jusqu'à un plancher.
    #
    # Réduire est sûr en pagination par CURSEUR : le curseur désigne un point dans la suite, pas un
    # numéro de page — prendre 25 éléments après lui au lieu de 50 ne saute rien.
    out=""
    for essai in 1 2 3 4 5; do
      out="$(gl_graphql_read "$(requete_page "$curseur" "$taille")")" || return 1
      # Une erreur GraphQL revient en HTTP 200, et une réponse PARTIELLE porte quand même un
      # « data ». Sans ce contrôle, une page tronquée s'écrirait sur disque et l'export serait
      # incomplet SANS que rien ne le dise — le pire des trois cas possibles.
      case "$out" in
        *'"errors":'*) : ;;
        *) break ;;
      esac
      messages="$(printf '%s' "$out" | grep -o '"message":"[^"]*"' | head -3)"
      # Seule une EXPIRATION se retente. Une erreur de schéma ou de droits se reproduirait à
      # l'identique cinq fois de suite, et la retenter ne ferait que retarder un message clair.
      case "$messages" in
        *Timeout*|*timeout*) : ;;
        *)
          echo "erreur GraphQL en page $num :" >&2
          printf '%s\n' "$messages" >&2
          return 1 ;;
      esac
      if [ "$essai" -ge 2 ] && [ "$taille" -gt 5 ]; then
        taille=$((taille / 2)); [ "$taille" -lt 5 ] && taille=5
        reduits=$((reduits + 1))
        [ "$format" = "tsv" ] || printf '  page %03d — expiration, taille ramenée à %s\n' "$num" "$taille"
      else
        [ "$format" = "tsv" ] || printf '  page %03d — expiration, nouvelle tentative\n' "$num"
      fi
      out=""
    done
    if [ -z "$out" ] || page_en_erreur "$out"; then
      echo "page $num : expirations répétées jusqu'à $taille ticket(s) par requête — abandon." >&2
      return 1
    fi
    case "$out" in
      *'"nodes":'*) : ;;
      *) echo "page $num sans tickets — réponse inattendue." >&2; return 1 ;;
    esac
    fichier="$(printf '%s/page-%03d.json' "$PAGES_DIR" "$num")"
    printf '%s' "$out" > "$fichier" || return 1
    [ "$format" = "tsv" ] || printf '  page %03d — %s tickets, %s octets\n' "$num" "$taille" \
      "$(wc -c < "$fichier" | tr -d ' ')"

    printf '%s' "$out" | grep -q '"hasNextPage":true' || break
    suivant="$(printf '%s' "$out" | grep -o '"endCursor":"[^"]*"' | head -1 | cut -d'"' -f4)"
    [ -n "$suivant" ] || break
    curseur="$suivant"
  done
  kv "pages écrites" "$num"
  [ "$reduits" -gt 0 ] && kv "réductions de taille (expirations)" "$reduits"
  return 0
}

# Référentiels : milestones et labels, en REST brut. L'import doit les recréer AVANT les tickets
# (un ticket ne porte que le nom de son milestone), donc ils ne sont pas un « bonus » mais un
# prérequis de l'ordre d'import.
fetch_referentiels() {
  local enc n_m n_l
  enc="$(gl_project_enc)"
  glab api "projects/$enc/milestones?per_page=100&state=all" > "$SORTIE/milestones.json" 2>/dev/null
  glab api "projects/$enc/labels?per_page=100&with_counts=false" > "$SORTIE/labels.json" 2>/dev/null
  n_m="$(grep -o '"iid":[0-9]\+' "$SORTIE/milestones.json" 2>/dev/null | wc -l | tr -d ' ')"
  n_l="$(grep -o '"name":"[^"]*"' "$SORTIE/labels.json" 2>/dev/null | wc -l | tr -d ' ')"
  kv "milestones exportés" "${n_m:-0}"
  kv "labels exportés" "${n_l:-0}"
}

# --- Étape 2 : le découpage ----------------------------------------------------------------------
# Transforme les pages en backlog.jsonl, un objet JSON par ligne.
#
# C'EST LE CŒUR DE LA FIDÉLITÉ, et c'est pour ça que c'est de l'awk et pas un parseur JSON : le
# programme ci-dessous ne DÉCODE rien. Il compte des accolades et des crochets en sautant ce qui est
# entre guillemets, puis rend des TRANCHES D'OCTETS de la réponse d'origine (substr). Un accent, un
# em-dash ou un bloc de code ne sont jamais interprétés — ils sont recopiés.
#
# Sûr en UTF-8 sous LC_ALL=C : tous les octets d'une séquence multi-octets valent >= 0x80, ils ne
# peuvent donc JAMAIS collisionner avec les délimiteurs ASCII qu'on cherche ({ } [ ] " \). C'est le
# même raisonnement que gl_json_string_field dans lib.sh — et la même raison de ne pas y toucher.
#
# Les lignes sont jointes SANS séparateur : en JSON valide un saut de ligne entre deux jetons est
# insignifiant et ne peut pas apparaître dans une chaîne (il y serait échappé « \n »). Joindre par
# "" garantit donc qu'aucun élément émis ne contient de saut de ligne brut — ce qui est exactement
# l'invariant dont le format JSONL a besoin, et que le contrôle « lignes == tickets » revérifie.
#
# Le programme voyage par HEREDOC CITÉ (<<'AWK') et non par chaîne entre apostrophes : un commentaire
# français en contient forcément une (« s'arrêtant »), qui fermerait la chaîne et ferait interpréter
# la suite du programme awk comme des commandes shell. Même idiome que gl_awk_workflow dans lib.sh.
DECOUPE_AWK="$(cat <<'AWK'
{ buf = buf $0 }
END {
  i = index(buf, "\"nodes\":[")
  if (i == 0) exit 1
  p = i + 9; n = length(buf); depth = 0; instr = 0; debut = 0; emis = 0
  while (p <= n) {
    c = substr(buf, p, 1)
    if (instr) {
      if (c == "\\") { p += 2; continue }
      if (c == "\"") instr = 0
      p++; continue
    }
    if (c == "\"") { instr = 1; p++; continue }
    if (c == "{" || c == "[") { if (depth == 0) debut = p; depth++ }
    else if (c == "}" || c == "]") {
      depth--
      if (depth == 0) { print substr(buf, debut, p - debut + 1); emis++ }
      else if (depth < 0) break
    }
    p++
  }
  if (emis == 0) exit 1
}
AWK
)"

assembler() {
  section "2. Assemblage — backlog.jsonl"
  # Tableau et non chaîne : la racine du dépôt contient un espace sur le poste de référence
  # (« E:\Projects Solutions\… »), donc un `for f in $(ls …)` couperait chaque chemin en deux.
  local pages=() f
  for f in "$PAGES_DIR"/page-*.json; do [ -f "$f" ] && pages+=("$f"); done
  if [ "${#pages[@]}" -eq 0 ]; then
    echo "aucune page sous $PAGES_DIR — lancer l'export sans --hors-ligne d'abord." >&2
    return 1
  fi
  : > "$JSONL" || return 1
  for f in "${pages[@]}"; do
    if ! LC_ALL=C awk "$DECOUPE_AWK" "$f" >> "$JSONL"; then
      echo "découpage impossible sur $f" >&2; return 1
    fi
  done
  kv "tickets extraits (lignes)" "$(wc -l < "$JSONL" | tr -d ' ')"
  kv "taille" "$(wc -c < "$JSONL" | tr -d ' ') octets"
}

# --- Étape 3 : le manifeste ----------------------------------------------------------------------
# Index machine, une ligne par ticket. Il n'ajoute AUCUNE information — tout est déjà dans le
# jsonl — mais il rend les sept données lisibles sans parseur, et c'est lui que la recette du lot
# d'import comparera à ce qui aura été créé côté GitHub.
#
# Les extractions sont ANCRÉES sur la clé de leur conteneur (`tranche`) plutôt que cherchées à plat :
# « title » désigne le titre du ticket, mais aussi celui de chaque label et du milestone ; « username »
# l'auteur, mais aussi chaque assigné et chaque auteur de commentaire. Chercher à plat rendrait un
# manifeste faux d'apparence plausible.
MANIFESTE_AWK="$(cat <<'AWK'
# tranche(s, cle) -> le contenu du tableau "<cle>":{"nodes":[ … ], délimité par comptage de
# profondeur (les mêmes règles que le découpage : on saute ce qui est entre guillemets).
function tranche(s, cle,   i, p, n, depth, instr, c, debut) {
  i = index(s, "\"" cle "\":{\"nodes\":[")
  if (i == 0) return ""
  debut = i + length(cle) + 13
  p = debut; n = length(s); depth = 0; instr = 0
  while (p <= n) {
    c = substr(s, p, 1)
    if (instr) {
      if (c == "\\") { p += 2; continue }
      if (c == "\"") instr = 0
      p++; continue
    }
    if (c == "\"") { instr = 1; p++; continue }
    if (c == "{" || c == "[") depth++
    else if (c == "}") depth--
    else if (c == "]") { if (depth == 0) return substr(s, debut, p - debut); depth-- }
    p++
  }
  return ""
}
# champ/nombre : première occurrence dans la portée reçue (à appeler sur une tranche, pas sur la
# ligne entière, dès que la clé est ambiguë).
function champ(s, cle,   m) {
  if (!match(s, "\"" cle "\":\"[^\"]*\"")) return "-"
  m = substr(s, RSTART, RLENGTH); sub("^\"" cle "\":\"", "", m); sub("\"$", "", m)
  return m
}
function nombre(s, cle,   m) {
  if (!match(s, "\"" cle "\":-?[0-9]+")) return "0"
  m = substr(s, RSTART, RLENGTH); sub("^\"" cle "\":", "", m)
  return m
}
# valeurs(tr, cle) -> toutes les valeurs de "<cle>":"…" de la tranche, jointes par « , ».
function valeurs(tr, cle,   out, reste, m) {
  out = ""; reste = tr
  while (match(reste, "\"" cle "\":\"[^\"]*\"")) {
    m = substr(reste, RSTART, RLENGTH); sub("^\"" cle "\":\"", "", m); sub("\"$", "", m)
    out = out (out == "" ? "" : ",") m
    reste = substr(reste, RSTART + RLENGTH)
  }
  return out == "" ? "-" : out
}
# scope(liste, prefixe) -> le suffixe du premier label du scope (« workflow::en-cours » -> « en-cours »).
function scope(liste, pre,   n, i, t) {
  n = split(liste, t, ",")
  for (i = 1; i <= n; i++) if (index(t[i], pre) == 1) return substr(t[i], length(pre) + 1)
  return "-"
}
# span_chaine(s, cle) -> la valeur BRUTE (encore échappée) du champ chaîne <cle>, en s'arrêtant au
# vrai guillemet fermant (les « \" » internes sont sautés). Sert à mesurer la description sans la
# décoder — on veut sa taille et son taux d accents, pas son texte — et à sortir le titre : la forme
# échappée est TSV-SÛRE par construction, une tabulation y étant forcément « \t ».
function span_chaine(s, cle,   i, p, n, c, debut) {
  i = index(s, "\"" cle "\":\"")
  if (i == 0) return ""
  debut = i + length(cle) + 4
  p = debut; n = length(s)
  while (p <= n) {
    c = substr(s, p, 1)
    if (c == "\\") { p += 2; continue }
    if (c == "\"") return substr(s, debut, p - debut)
    p++
  }
  return ""
}
# occurrences(s, m) -> nombre de fois que la SUITE D OCTETS m apparaît dans s. Volontairement bâti
# sur index() et non sur gsub() : compter des octets >= 0x80 par une classe de caractères obligerait
# à écrire des échappements (\x80, \303) DANS UNE EXPRESSION RÉGULIÈRE, ce que gawk accepte mais que
# mawk rend autrement — et le verdict d un export ne doit pas dépendre de l awk du poste. index() ne
# connaît que des octets, partout pareil.
function occurrences(s, m,   n, p) {
  n = 0; p = index(s, m)
  while (p > 0) { n++; s = substr(s, p + length(m)); p = index(s, m) }
  return n
}
# lisible(s) -> déséchappe les SEULS échappements que GitLab produit hors ASCII de contrôle, pour
# que la colonne « titre » du manifeste se lise (« objectif -> liste » -> « objectif -> liste »).
# On ne touche NI à \t NI à \n : les laisser échappés est ce qui garde une ligne TSV sur une ligne.
function lisible(s) {
  gsub(/\\u003c/, "<", s); gsub(/\\u003e/, ">", s); gsub(/\\u0026/, "\\&", s)
  gsub(/\\"/, "\"", s);    gsub(/\\\\/, "\\", s)
  return s
}
BEGIN {
  FS = "\n"
  print "# iid\tetat\ttype_wi\tworkflow\ttype\tagent\tprio\tmilestone\tdebut\techeance\ttemps_s\tassignes\tlies\tnotes\tnotes_hum\to_desc\tnon_ascii\ttitre"
}
{
  ligne = $0
  iid = champ(ligne, "iid")
  etat = champ(ligne, "state")
  # Le titre du ticket est le PREMIER "title" de la ligne (la requête le place avant les widgets) ;
  # ceux des labels et du milestone viennent après. Lu par span_chaine pour rester exact sur un
  # titre contenant un guillemet échappé, et TSV-sûr.
  titre = lisible(span_chaine(ligne, "title"))
  # workItemType : ancré sur son conteneur, « name » étant un mot trop courant pour être cherché à plat.
  type_wi = "-"
  if (match(ligne, "\"workItemType\":\\{\"name\":\"[^\"]*\"")) {
    type_wi = substr(ligne, RSTART, RLENGTH)
    sub("^\"workItemType\":\\{\"name\":\"", "", type_wi); sub("\"$", "", type_wi)
  }

  lbl = valeurs(tranche(ligne, "labels"), "title")
  ms  = "-"
  if (match(ligne, "\"milestone\":\\{\"title\":\"[^\"]*\"")) {
    ms = substr(ligne, RSTART, RLENGTH); sub("^\"milestone\":\\{\"title\":\"", "", ms); sub("\"$", "", ms)
  }
  debut_d = champ(ligne, "startDate")
  fin_d = champ(ligne, "dueDate")
  temps = nombre(ligne, "totalTimeSpent")
  assignes = valeurs(tranche(ligne, "assignees"), "username")
  lies = valeurs(tranche(ligne, "linkedItems"), "iid")

  # Chaque note porte exactement un "system": — le compter, c'est compter les notes, sans avoir à
  # descendre dans les discussions. "system":false isole les commentaires HUMAINS, les seuls dont
  # l import doit reproduire le corps ; les autres sont l historique d activité de GitLab.
  tmp = ligne; notes = gsub(/"system":/, "", tmp)
  tmp = ligne; notes_h = gsub(/"system":false/, "", tmp)

  # Densité d accents de la description : sert à CHOISIR les tickets sur lesquels la vérification
  # ira chercher la preuve par octets. On compte les lettres accentuées les plus courantes du
  # backlog et l em-dash — un ticket qui en concentre beaucoup est le pire cas d un défaut
  # d encodage, donc le meilleur témoin.
  desc = span_chaine(ligne, "description")
  o_desc = length(desc)
  na = occurrences(desc, "\303\251") + occurrences(desc, "\303\250") \
     + occurrences(desc, "\303\240") + occurrences(desc, "\342\200\224")

  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n",
    iid, etat, type_wi, scope(lbl, "workflow::"), scope(lbl, "type::"), scope(lbl, "agent::"),
    scope(lbl, "prio::"), ms, debut_d, fin_d, temps, assignes, lies, notes, notes_h,
    o_desc, na, titre
}
AWK
)"

manifester() {
  section "3. Manifeste et trous"
  LC_ALL=C awk "$MANIFESTE_AWK" "$JSONL" > "$MANIFESTE" || return 1

  # Les trous : ce que l'import devra combler. Calculé sur les iid RÉELLEMENT exportés, pas sur ce
  # que l'inventaire annonçait — un export est la seule source qui engage l'import.
  local iids min max presents attendus n_trous
  iids="$(LC_ALL=C awk -F '\t' 'NR > 1 { print $1 }' "$MANIFESTE" | sort -n)"
  min="$(printf '%s\n' "$iids" | head -1)"
  max="$(printf '%s\n' "$iids" | tail -1)"
  presents="$(printf '%s\n' "$iids" | wc -l | tr -d ' ')"
  attendus=$((max - min + 1))
  printf '%s\n' "$iids" | LC_ALL=C awk -v mn="$min" -v mx="$max" '
    { vu[$1] = 1 } END { for (i = mn; i <= mx; i++) if (!(i in vu)) print i }' > "$TROUS"
  n_trous="$(wc -l < "$TROUS" | tr -d ' ')"

  kv "plage d'iid" "#$min → #$max"
  kv "tickets exportés" "$presents"
  kv "iid attendus sur la plage" "$attendus"
  kv "trous à combler (trous.txt)" "$n_trous"
  if [ "$min" -gt 1 ]; then
    kv "objets à créer côté GitHub" "$max (dont $((min - 1)) avant le premier ticket réel)"
  else
    kv "objets à créer côté GitHub" "$max"
  fi
  local temps_total
  temps_total="$(LC_ALL=C awk -F '\t' 'NR > 1 { s += $11 } END { printf "%d", s }' "$MANIFESTE")"
  kv "temps passé cumulé" "$((temps_total / 3600)) h ($temps_total s)"
  kv "tickets avec temps passé" "$(LC_ALL=C awk -F '\t' 'NR > 1 && $11 > 0' "$MANIFESTE" | wc -l | tr -d ' ')"
  kv "tickets avec dates" "$(LC_ALL=C awk -F '\t' 'NR > 1 && $9 != "-"' "$MANIFESTE" | wc -l | tr -d ' ')"
  kv "commentaires (dont humains)" "$(LC_ALL=C awk -F '\t' 'NR > 1 { n += $14; h += $15 } END { printf "%d (%d)", n, h }' "$MANIFESTE")"
}

# --- Étape 4 : la vérification -------------------------------------------------------------------
# Trois contrôles indépendants, et aucun ne regarde un écran : un terminal cp1252 réaffiche le
# mojibake de façon PLAUSIBLE (« — » corrompu ressort « — », qui se lit), donc vérifier à l'œil
# revient à ne pas vérifier. Tout se joue sur des octets et des comparaisons de fichiers.
#
#   a) SIGNATURE DE MOJIBAKE. Un texte UTF-8 relu en cp1252 puis ré-encodé en UTF-8 produit des
#      octets caractéristiques : « é » (c3 a9) devient « Ã© » (c3 83 c2 a9) et « — » (e2 80 94)
#      devient « â€" » (c3 a2 e2 82 ac …). On cherche donc c3 83 et c3 a2 e2 82 ac, qui n'ont
#      aucune raison d'exister dans du français correct.
#
#      ⚠ UNE SIGNATURE N'EST PAS UN VERDICT, et c'est le point le moins évident de ce fichier.
#      Elle ne dit pas QUI a corrompu : l'export, ou GitLab avant lui. Le premier run l'a montré —
#      5 tickets marqués, dont AUCUN n'était de notre fait. Deux (#141, #233) *documentent* le
#      mojibake et en citent des exemples, donc les octets suspects sont leur contenu légitime ;
#      trois (#79, #92, #102) sont les VICTIMES SURVIVANTES de l'incident de #141 — leur
#      description est corrompue DANS GitLab depuis 2026-07, #92 l'étant même deux fois
#      (« ÃƒÂ© » = « é » ré-encodé trois fois). Échouer là-dessus dirait « l'export est faux »
#      alors qu'il est exact, et masquerait la seule information qui compte pour l'import : ces
#      tickets arriveront abîmés sur GitHub si personne ne tranche.
#      Chaque suspect est donc REJUGÉ par le contrôle (c) ci-dessous. Identique à la voie REST =
#      corruption ANTÉRIEURE, consignée dans mojibake-source.txt à l'intention du lot #340, et
#      l'export reste valide. Divergent = c'est nous, et là c'est un échec.
#   b) VALIDITÉ UTF-8 de tout le fichier (iconv), qui attrape une troncature au milieu d'une
#      séquence multi-octets — ce qu'un découpage à l'octet pourrait provoquer et qu'aucune
#      signature ne montrerait.
#   c) PREUVE PAR UNE SECONDE VOIE : sur les tickets les plus accentués de l'export, la description
#      est ré-extraite du jsonl (chemin GraphQL + découpage awk) et comparée OCTET POUR OCTET, par
#      cmp, à ce que rend gl_get_description (chemin REST + gl_json_string_field). Deux chaînes
#      indépendantes de bout en bout : si elles tombent d'accord sur des milliers d'octets d'accents,
#      d'em-dashes et de blocs de code, l'export est fidèle. C'est le contrôle qui coûte du réseau,
#      d'où l'échantillon — et il est SAUTÉ en --hors-ligne, ce qui se dit au lieu de se supposer.
verifier() {
  section "4. Vérification — par octets, jamais à l'affichage"
  local echecs=0

  if [ ! -s "$JSONL" ]; then
    printf '  %s✗%s backlog.jsonl absent ou vide.\n' "$C_R" "$C_0"; return 1
  fi

  # (a) signatures de mojibake — on RELÈVE, on ne juge pas encore (voir l'en-tête de la fonction).
  local suspects n_susp
  suspects="$(LC_ALL=C awk '
    index($0, "\303\203") || index($0, "\303\242\342\202\254") {
      match($0, /^\{"iid":"[0-9]+"/)
      print substr($0, 9, RLENGTH - 9)
    }' "$JSONL")"
  n_susp="$(printf '%s' "$suspects" | grep -c . || true)"
  if [ "$n_susp" -eq 0 ]; then
    kv "signatures de mojibake" "0 — aucune ($C_G✓$C_0)"
  else
    kv "signatures de mojibake" "$n_susp ticket(s) à rejuger : $(printf '%s' "$suspects" | tr '\n' ' ')"
  fi

  # Contre-preuve : le fichier DOIT contenir de l'UTF-8 multi-octets. Zéro accent sur un backlog
  # rédigé en français ne signifierait pas « propre » mais « quelque chose les a mangés » — un
  # contrôle qui ne peut pas échouer ne vérifie rien.
  local n_emdash n_accents compte_awk
  # index() plutôt qu une classe de caractères : voir occurrences() dans le manifeste — un
  # échappement d octet dans une regex ne se comporte pas pareil d un awk à l autre.
  compte_awk="$(cat <<'AWK'
    function occurrences(s, m,   n, p) {
      n = 0; p = index(s, m)
      while (p > 0) { n++; s = substr(s, p + length(m)); p = index(s, m) }
      return n
    }
    { t += occurrences($0, M) } END { printf "%d", t + 0 }
AWK
)"
  n_emdash="$(LC_ALL=C awk -v M="$(printf '\342\200\224')" "$compte_awk" "$JSONL")"
  n_accents="$(LC_ALL=C awk -v M="$(printf '\303\251')" "$compte_awk" "$JSONL")"
  if [ "$n_emdash" -gt 0 ] && [ "$n_accents" -gt 0 ]; then
    kv "UTF-8 attendu présent" "$n_accents « é », $n_emdash « — » ($C_G✓$C_0)"
  else
    kv "UTF-8 attendu présent" "$C_R« é »=$n_accents, « — »=$n_emdash — suspect ✗$C_0"; echecs=$((echecs + 1))
  fi

  # (b) validité UTF-8 d'ensemble
  if command -v iconv >/dev/null 2>&1; then
    if iconv -f UTF-8 -t UTF-8 "$JSONL" >/dev/null 2>&1; then
      kv "UTF-8 valide de bout en bout" "oui ($C_G✓$C_0)"
    else
      kv "UTF-8 valide de bout en bout" "${C_R}NON — séquence invalide ✗$C_0"; echecs=$((echecs + 1))
    fi
  else
    kv "UTF-8 valide de bout en bout" "non vérifié (iconv absent)"
  fi

  # Cohérence de comptage : une ligne par ticket, et autant de lignes que d'iid distincts. Un
  # découpage qui aurait fusionné ou coupé un objet se voit ici et nulle part ailleurs.
  local n_lignes n_iid
  n_lignes="$(wc -l < "$JSONL" | tr -d ' ')"
  n_iid="$(LC_ALL=C awk -F '\t' 'NR > 1 { print $1 }' "$MANIFESTE" | sort -u | wc -l | tr -d ' ')"
  if [ "$n_lignes" = "$n_iid" ]; then
    kv "lignes == iid distincts" "$n_lignes ($C_G✓$C_0)"
  else
    kv "lignes == iid distincts" "$C_R$n_lignes ≠ $n_iid ✗$C_0"; echecs=$((echecs + 1))
  fi

  # (c) preuve par une seconde voie — sur l'échantillon le plus accentué ET sur TOUS les suspects.
  # Le fichier porte son mode d'emploi : il sera lu par le lot d'import, longtemps après, et sa
  # liste seule se lirait « voici les tickets abîmés » — ce qui est faux pour une partie d'entre eux.
  {
    printf '# Tickets dont la description porte une signature de mojibake DANS GITLAB.\n'
    printf '# Vérifié : leurs octets sont identiques par la voie REST, donc l export est fidèle —\n'
    printf '# la corruption lui est ANTÉRIEURE. Deux familles, à distinguer à l œil avant d agir :\n'
    printf '#   - des tickets qui DOCUMENTENT le mojibake et en citent des exemples (contenu légitime) ;\n'
    printf '#   - des tickets réellement ABÎMÉS par l incident de #141 (2026-07), jamais réparés.\n'
    printf '# Décision attendue au lot #340 : importer tel quel, ou réparer la source avant.\n'
  } > "$MOJIBAKE_SRC"
  if [ "$mode" = "hors-ligne" ]; then
    note "preuve par seconde voie : sautée (--hors-ligne, elle demande le réseau)."
    if [ "$n_susp" -gt 0 ]; then
      kv "suspects non rejugés" "$C_Y$n_susp — relancer sans --hors-ligne pour trancher$C_0"
    fi
  elif [ "$ECHANTILLON" -eq 0 ] && [ "$n_susp" -eq 0 ]; then
    note "preuve par seconde voie : sautée (--echantillon 0)."
  else
    local cibles cible avant apres node ok=0 ko=0 anterieurs=0 taille
    # Les plus accentués d'abord : c'est là qu'un défaut d'encodage a le plus de chances de se voir.
    # Les suspects viennent ensuite, sans doublon — un ticket très accentué peut être suspect.
    cibles="$(
      { LC_ALL=C awk -F '\t' 'NR > 1 { print $17 "\t" $1 }' "$MANIFESTE" \
          | sort -rn | head -"$ECHANTILLON" | cut -f2
        printf '%s\n' "$suspects"
      } | awk 'NF && !vu[$0]++'
    )"
    avant="$(mktemp)" || return 1
    apres="$(mktemp)" || { rm -f "$avant"; return 1; }
    node="$(mktemp)"  || { rm -f "$avant" "$apres"; return 1; }
    for cible in $cibles; do
      LC_ALL=C awk -v id="$cible" '
        index($0, "{\"iid\":\"" id "\",") == 1 { print; exit }' "$JSONL" > "$node"
      gl_json_string_field description < "$node" > "$apres"
      gl_get_description "$cible" > "$avant" 2>/dev/null
      taille="$(wc -c < "$apres" | tr -d ' ')"
      # Un suspect : la question n'est pas « est-ce propre ? » mais « est-ce NOUS ? ».
      local est_suspect="non"
      printf '%s\n' "$suspects" | grep -qx "$cible" && est_suspect="oui"
      if [ -s "$avant" ] && cmp -s "$avant" "$apres"; then
        if [ "$est_suspect" = "oui" ]; then
          anterieurs=$((anterieurs + 1))
          printf '%s\n' "$cible" >> "$MOJIBAKE_SRC"
          kv "  #$cible — signature" "identique à REST → corruption ANTÉRIEURE, pas la nôtre"
        else
          ok=$((ok + 1))
          kv "  #$cible — GraphQL vs REST" "$taille octets identiques ($C_G✓$C_0)"
        fi
      else
        ko=$((ko + 1))
        kv "  #$cible — GraphQL vs REST" "${C_R}DIVERGENT ✗$C_0"
        [ "$format" = "tsv" ] || cmp "$avant" "$apres" 2>&1 | head -3 | sed 's/^/      /'
      fi
    done
    rm -f "$avant" "$apres" "$node"
    [ "$ko" -gt 0 ] && echecs=$((echecs + 1))
    kv "preuve par seconde voie" "$ok fidèle(s), $ko divergent(s)"
    if [ "$anterieurs" -gt 0 ]; then
      kv "mojibake ANTÉRIEUR à l'export" "$C_Y$anterieurs ticket(s) — mojibake-source.txt$C_0"
      note "l'export les reproduit fidèlement : la corruption est DANS GitLab, pas ici."
      note "à trancher au lot #340 — importer tel quel, ou réparer avant. Ne pas le découvrir"
      note "après coup : une fois les 330 tickets créés, la reprise coûte cher."
    fi
  fi

  return "$echecs"
}

# --- Déroulé -------------------------------------------------------------------------------------

if [ "$mode" != "hors-ligne" ]; then
  gl_require_glab || exit 1
fi
mkdir -p "$SORTIE" || exit 1

if [ "$mode" = "complet" ]; then
  fetch_pages || exit 1
  fetch_referentiels
fi
if [ "$mode" != "verifier" ]; then
  assembler || exit 1
fi
if [ ! -s "$JSONL" ]; then
  echo "aucun export sous $SORTIE — lancer d'abord sans --verifier." >&2; exit 1
fi
manifester || exit 1

verifier; echecs=$?

section "Verdict"
if [ "$echecs" -eq 0 ]; then
  # Chemin RELATIF : c'est ce que le script invite à lire, et un chemin absolu hors du répertoire
  # de travail demande une approbation qu'une session autonome n'a personne pour donner (#234).
  # Le verdict nomme AUSSI ce qui n'a pas été joué : « vérifié » sans la preuve par seconde voie
  # n'est pas le même mot que « vérifié » avec elle, et c'est sur ce mot que l'import s'engagera.
  if [ "$mode" = "hors-ligne" ]; then
    if [ "$format" = "tsv" ]; then kv "verdict" "hors-ligne"
    else printf '  %s✓%s export assemblé, contrôles hors ligne passés — %s\n' "$C_G" "$C_0" "${JSONL#"$racine/"}"
         note "la preuve par seconde voie (comparaison REST) n'a pas été jouée : relancer sans --hors-ligne."
    fi
  else
    if [ "$format" = "tsv" ]; then kv "verdict" "ok"
    else printf '  %s✓%s export complet et vérifié — %s\n' "$C_G" "$C_0" "${JSONL#"$racine/"}"
    fi
  fi
  code=0
else
  if [ "$format" = "tsv" ]; then kv "verdict" "echec"
  else printf '  %s✗%s %s contrôle(s) en échec — NE PAS importer sur cet export.\n' "$C_R" "$C_0" "$echecs"
  fi
  code=3
fi
[ "$format" = "tsv" ] && kv "export" "${JSONL#"$racine/"}"

# resume.txt : le même verdict, sur disque, pour que le lot d'import n'ait pas à relancer l'export
# pour savoir s'il peut s'en servir. Chemins RELATIFS à la racine du dépôt (CLAUDE.md, #234) — un
# chemin absolu hors du répertoire de travail demanderait une approbation qu'une session autonome
# n'a personne pour donner.
{
  printf 'Export du backlog GitLab — %s\n' "$GL_PROJECT"
  printf 'produit le %s\n\n' "$(date '+%F %T')"
  printf 'tickets      : %s\n' "$(wc -l < "$JSONL" | tr -d ' ')"
  printf 'trous        : %s (voir trous.txt)\n' "$(wc -l < "$TROUS" | tr -d ' ')"
  if [ "$echecs" -ne 0 ]; then
    printf 'vérification : ÉCHEC (%s contrôle(s)) — ne pas importer sur cet export\n' "$echecs"
  elif [ "$mode" = "hors-ligne" ]; then
    printf 'vérification : contrôles hors ligne seuls (preuve par seconde voie NON jouée)\n'
  else
    printf 'vérification : OK, par octets (dont preuve par seconde voie GraphQL vs REST)\n'
  fi
  [ -s "$MOJIBAKE_SRC" ] && printf 'mojibake source : %s ticket(s) — voir mojibake-source.txt\n' \
    "$(grep -cv '^#' "$MOJIBAKE_SRC" || true)"
  printf '\nfichiers (relatifs à la racine du dépôt) :\n'
  printf '  %s\n' "${JSONL#"$racine/"}" "${MANIFESTE#"$racine/"}" "${TROUS#"$racine/"}" \
                  "${SORTIE#"$racine/"}/milestones.json" "${SORTIE#"$racine/"}/labels.json" \
                  "${PAGES_DIR#"$racine/"}/"
} > "$RESUME"
[ "$format" = "tsv" ] || printf '  résumé : %s\n' "${RESUME#"$racine/"}"

exit "$code"
