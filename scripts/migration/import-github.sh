#!/usr/bin/env bash
# Import du backlog sur GitHub, DANS L'ORDRE et TROUS COMBLÉS (ticket #340, parent #335).
#
# C'EST L'ACTION À SENS UNIQUE DU CHANTIER. Elle se joue une fois. GitHub attribue les numéros
# d'issue séquentiellement et ne permet pas de les choisir : la seule façon d'obtenir l'objet #123
# est de créer 122 objets avant lui. Il n'y a donc ni reprise en arrière, ni correction après coup —
# on ne peut pas renuméroter, et supprimer une issue ne libère pas son numéro.
#
# POURQUOI L'ORDRE EST LE LIVRABLE. 270 commits de `origin/main` portent un `Refs #<n>` /
# `Closes #<n>`. Sur GitHub ces références sont rendues comme des LIENS. Si la séquence décale d'un
# seul rang, elles ne deviennent pas mortes mais FAUSSES : « Refs #123 » pointant vers un ticket
# sans rapport, plausible et jamais signalé. Un lien mort se repère, un lien faux non (docs/27 §6).
# D'où le seul invariant qui compte ici, vérifié AVANT et APRÈS chaque création :
#
#     l'objet créé pour l'iid N porte le numéro N — sinon on s'arrête net (code 4).
#
# Les iid ABSENTS côté GitLab (tickets supprimés : 19, 20, 201, 241 à la mesure du 2026-08-14)
# consomment donc un objet BOUCHE-TROU, créé puis fermé. Ne pas les combler décalerait tout ce qui
# suit.
#
# CE QUI ENTRE : l'export du lot #337 (`scripts/migration/export-gitlab.sh`), et lui seul. Le script
# ne parle JAMAIS à GitLab — la source est un répertoire sur disque, ce qui rend l'import rejouable
# hors ligne côté lecture et indépendant de la disponibilité de GitLab.
#
# LA FIDÉLITÉ EST OBTENUE EN NE DÉCODANT RIEN, exactement comme à l'export. Les descriptions, titres
# et commentaires sont extraits du JSONL **encore échappés** (`\n`, `\"`, `<`) et RECOPIÉS tels
# quels dans le corps JSON envoyé à GitHub. Aucune étape ne décode puis ré-encode du texte : c'est
# précisément l'aller-retour où le mojibake de #141 s'était introduit. Les seules chaînes construites
# ici (en-têtes, tableaux de métadonnées) sont écrites DIRECTEMENT sous forme échappée.
#
# CE QUE GITHUB N'A PAS, et qui vit donc dans un commentaire de métadonnées (« forme maison », §4 du
# parent) : dates de début/échéance, temps passé (603 h d'historique), liens « related », assignés et
# auteur d'origine. Le choix du COMMENTAIRE plutôt que d'un pied de description n'est pas une
# économie : le suivi du temps continue après la migration, et `/ticket-finish` loggue en AJOUTANT.
# Un commentaire s'ajoute en un appel ; un pied de description demanderait un lire-modifier-écrire à
# chaque log. L'historique et le quotidien partagent ainsi le même mécanisme et le même marqueur.
#
# CE QUI N'EST PAS REJOUÉ : les 271 merge requests (impossible sans leurs branches d'origine, §3 du
# parent) et les notes SYSTÈME de GitLab (« added ~52011709 labels », « mentioned in issue #328 ») —
# c'est le journal d'activité de l'outil qu'on quitte, pas de la matière. Les 144 commentaires
# HUMAINS, eux, sont tous repris.
#
# Usage :  bash scripts/migration/import-github.sh [options]
#   --check          rend le PLAN COMPLET et n'écrit RIEN côté GitHub (à jouer en premier)
#   --source <dir>   répertoire de l'export #337 (défaut : .maestro/migration/)
#   --depot <repo>   dépôt cible « owner/nom » (défaut : $MAESTRO_GITHUB_REPO ou le miroir de #332)
#   --max <n>        n'importer que les n premiers objets de la plage, puis s'arrêter proprement
#   --pause <s>      temporisation entre deux écritures (défaut 1)
#   --tsv            sortie machine « clé <TAB> valeur », sans couleur ; avec --check, plan intégral
#   --recette        vérifie APRÈS COUP, en lecture seule, que « #n sur GitHub = #n sur GitLab » :
#                    les deux bornes, les trous, les tickets cités par des commits, et un échantillon
#                    réparti. Compare titre, état, milestone et labels.
#   --payload <iid>  écrit sur stdout le corps JSON qui SERAIT envoyé pour ce ticket, et sort.
#                    Aucun réseau, aucune écriture. C'est le seul moyen de vérifier la fidélité des
#                    octets AVANT une action irréversible : le corps peut être décodé par un parseur
#                    JSON et comparé, octet pour octet, à la description rendue par GitLab.
#
# LA REPRISE EST ACQUISE, ET ELLE N'A PAS D'OPTION : le journal `<source>/import/journal.tsv` fait
# foi. Chaque étape franchie y est ajoutée (append seul, une ligne par étape), et un nouveau
# lancement reprend là où le précédent s'est arrêté. Une coupure réseau au 200e ticket ne fait donc
# ni recommencer, ni doublonner. Un POST dont la réponse s'est perdue est rattrapé sans doublon en
# relisant le dernier numéro du dépôt avant de retenter.
#
# Codes de sortie — « n'a pas fini » et « a mal fini » ne se confondent pas :
#   0 : plan rendu (--check), ou import complet
#   1 : erreur d'exécution
#   2 : usage
#   3 : PRÉREQUIS NON TENU — export absent/non vérifié, cible non vierge, jeton sans écriture.
#       Rien n'a été écrit côté GitHub.
#   4 : SÉQUENCE ROMPUE — un objet n'a pas reçu le numéro attendu. ARRÊT IMMÉDIAT : tout ce qui
#       suivrait serait décalé. Demande un arbitrage humain, jamais une relance à l'aveugle.
#   5 : interrompu mais REPRENABLE (--max atteint, limite d'API tenace, coupure) — relancer suffit.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
racine="$(cd "$here/../.." && pwd)"

SOURCE="${MAESTRO_MIGRATION_DIR:-$racine/.maestro/migration}"
DEPOT="${MAESTRO_GITHUB_REPO:-automatemaestro-create/maestro}"
PAUSE=1
MAX=0
PAYLOAD_IID=""
mode="import"
format="texte"

while [ $# -gt 0 ]; do
  case "$1" in
    --check)   mode="check" ;;
    --source)  SOURCE="${2:-}"; shift ;;
    --depot)   DEPOT="${2:-}"; shift ;;
    --max)     MAX="${2:-}"; shift ;;
    --pause)   PAUSE="${2:-}"; shift ;;
    --recette) mode="recette" ;;
    --payload) mode="payload"; PAYLOAD_IID="${2:-}"; shift ;;
    --tsv)     format="tsv" ;;
    -h|--help) sed -n '2,64p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "argument inconnu : $1 (voir --help)" >&2; exit 2 ;;
  esac
  shift
done

case "$MAX" in ''|*[!0-9]*) echo "--max attend un entier" >&2; exit 2 ;; esac
case "$PAUSE" in ''|*[!0-9.]*) echo "--pause attend un nombre de secondes" >&2; exit 2 ;; esac
[ -n "$SOURCE" ] || { echo "--source attend un chemin" >&2; exit 2; }
case "$DEPOT" in */*) : ;; *) echo "--depot attend « owner/nom »" >&2; exit 2 ;; esac

if [ "$format" = "texte" ] && [ -t 1 ]; then
  C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_B=$'\033[1m'; C_0=$'\033[0m'
else
  C_G=''; C_Y=''; C_R=''; C_B=''; C_0=''
fi

JSONL="$SOURCE/backlog.jsonl"
MANIFESTE="$SOURCE/manifeste.tsv"
TROUS="$SOURCE/trous.txt"
RESUME_EXPORT="$SOURCE/resume.txt"
LABELS_JSON="$SOURCE/labels.json"
MILESTONES_JSON="$SOURCE/milestones.json"

TRAVAIL="$SOURCE/import"
JOURNAL="$TRAVAIL/journal.tsv"
PLAN="$TRAVAIL/plan.tsv"

# Le label des objets bouche-trou : un scope à part, pour qu'un filtre les sorte d'un clic et qu'ils
# ne polluent jamais un décompte par `type::`.
LABEL_TROU="import::bouche-trou"

# largeur/kv/section/note : même rendu que inventaire.sh et export-gitlab.sh, pour que les trois
# étapes de la migration se lisent pareil. `largeur` compte des COLONNES et non des octets (un accent
# en vaut deux en UTF-8, et la colonne des valeurs part en escalier) — cf. #325, docs/10 §11.3.
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
echec()   { printf '  %s✗%s %s\n' "$C_R" "$C_0" "$1" >&2; }
# note_err : une note DEPUIS UNE FONCTION DONT LA SORTIE EST CAPTURÉE. `creer_objet` est appelée en
# substitution de commande (« $(creer_objet …) »), donc tout ce qu'elle écrit sur stdout part dans
# la variable de l'appelant — y compris, sans ceci, les trois lignes qui expliquent comment
# reprendre après un arrêt (#345). L'échec restait visible (`echec` écrit déjà sur stderr) et son
# mode d'emploi disparaissait : le pire découpage possible, puisque la personne voit le problème
# sans jamais voir la réponse. Même raison que la ligne de limite d'API dans `gh_ecrire`.
note_err() { [ "$format" = "tsv" ] || printf '  %s\n' "$1" >&2; }

# duree <secondes> -> « 3 h 00 » / « 45 min ». Sert au rendu humain du temps passé, jamais à un
# calcul : la valeur en secondes voyage à côté, dans le bloc machine.
duree() {
  local s="$1"
  if [ "$s" -ge 3600 ]; then printf '%d h %02d' "$((s / 3600))" "$(((s % 3600) / 60))"
  else printf '%d min' "$((s / 60))"; fi
}

# =================================================================================================
# Extraction — aucune de ces fonctions ne DÉCODE quoi que ce soit
# =================================================================================================
#
# Les programmes awk ci-dessous comptent des accolades en sautant ce qui est entre guillemets, puis
# rendent des TRANCHES D'OCTETS. Sûr en UTF-8 sous LC_ALL=C : tous les octets d'une séquence
# multi-octets valent >= 0x80, ils ne peuvent donc jamais collisionner avec les délimiteurs ASCII
# ({ } [ ] " \) qu'on cherche. Même raisonnement — et même raison de ne pas y toucher — que
# `gl_json_string_field` dans lib.sh et que le découpage de export-gitlab.sh.
#
# Ils voyagent par HEREDOC CITÉ (<<'AWK') et non par chaîne entre apostrophes : un commentaire
# français en contient forcément une (« s'arrête »), qui fermerait la chaîne et ferait interpréter la
# suite du programme comme des commandes shell.

# Bibliothèque commune aux programmes awk. Concaténée devant chacun d'eux plutôt que dupliquée.
AWK_LIB="$(cat <<'AWK'
# span(s, cle) -> la valeur BRUTE (encore échappée) du champ chaîne <cle>, en s'arrêtant au vrai
# guillemet fermant (les « \" » internes sont sautés). C'est CE QU'ON RECOPIE dans le corps JSON
# envoyé à GitHub : la chaîne n'est jamais décodée, donc jamais ré-encodée.
function span(s, cle,   i, p, n, c, debut) {
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
# champ/nombre : première occurrence dans la portée reçue. À appeler sur une tranche, pas sur la
# ligne entière, dès que la clé est ambiguë (« title » désigne le ticket, mais aussi chaque label).
function champ(s, cle,   m) {
  if (!match(s, "\"" cle "\":\"[^\"]*\"")) return ""
  m = substr(s, RSTART, RLENGTH); sub("^\"" cle "\":\"", "", m); sub("\"$", "", m)
  return m
}
function nombre(s, cle,   m) {
  if (!match(s, "\"" cle "\":-?[0-9]+")) return "0"
  m = substr(s, RSTART, RLENGTH); sub("^\"" cle "\":", "", m)
  return m
}
# tranche(s, cle) -> le contenu du tableau "<cle>":{"nodes":[ … ], délimité par comptage de
# profondeur (mêmes règles : on saute ce qui est entre guillemets).
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
# valeurs(tr, cle) -> toutes les valeurs de "<cle>":"…" de la tranche, jointes par « , ».
function valeurs(tr, cle,   out, reste, m) {
  out = ""; reste = tr
  while (match(reste, "\"" cle "\":\"[^\"]*\"")) {
    m = substr(reste, RSTART, RLENGTH); sub("^\"" cle "\":\"", "", m); sub("\"$", "", m)
    out = out (out == "" ? "" : ",") m
    reste = substr(reste, RSTART + RLENGTH)
  }
  return out
}
# objet_n(tr, k) -> le k-ième objet {...} de premier niveau d'une tranche, ou "" s'il n'y en a pas
# tant. Sert à parcourir des listes d'objets (notes, timelogs) SANS supposer l'ordre des champs à
# l'intérieur : chaque objet est ensuite interrogé par span()/champ(), qui cherchent par nom.
# L'alternative — lire les champs à la file en supposant l'ordre de la requête GraphQL — coupleraut
# ce fichier à la rédaction de la requête de #337, à distance et sans que rien ne le signale.
# dedans(s) -> l'INTÉRIEUR du tableau JSON de premier niveau (« [ … ] » -> « … »).
# Indispensable avant objet_n sur un fichier REST entier : objet_n compte « [ » comme un ouvrant,
# donc sur « [{…},{…}] » le premier objet de premier niveau est LE TABLEAU LUI-MÊME — un seul, et
# la boucle s'arrête après lui. C'est exactement ce qui rendait « 1 milestone » sur 13.
function dedans(s,   a, b) {
  a = index(s, "[")
  if (a == 0) return s
  b = length(s)
  while (b > a && substr(s, b, 1) != "]") b--
  if (b <= a) return s
  return substr(s, a + 1, b - a - 1)
}
function objet_n(tr, k,   p, n, depth, instr, c, debut, vus) {
  p = 1; n = length(tr); depth = 0; instr = 0; vus = 0
  while (p <= n) {
    c = substr(tr, p, 1)
    if (instr) {
      if (c == "\\") { p += 2; continue }
      if (c == "\"") instr = 0
      p++; continue
    }
    if (c == "\"") { instr = 1; p++; continue }
    if (c == "{" || c == "[") { if (depth == 0) debut = p; depth++ }
    else if (c == "}" || c == "]") {
      depth--
      if (depth == 0) { vus++; if (vus == k) return substr(tr, debut, p - debut + 1) }
      else if (depth < 0) return ""
    }
    p++
  }
  return ""
}
AWK
)"

# --- Un ticket -> un flux TSV typé ---------------------------------------------------------------
# Émet, pour la ligne JSONL reçue :
#   T <titre échappé>
#   D <description échappée>
#   M <clé> <valeur>                      (état, dates, temps, auteur, milestone, assignés, liés…)
#   B <label>                             (un par ligne)
#   N <auteur> <date> <corps échappé>     (commentaire HUMAIN, dans l'ordre du fil)
#   L <secondes> <date> <auteur> <résumé échappé>
# Les corps restent échappés, donc sans tabulation ni saut de ligne bruts (ils y seraient « \t » et
# « \n ») : le TSV tient par construction, ce n'est pas une supposition.
TICKET_AWK="$AWK_LIB$(cat <<'AWK'
{
  ligne = $0
  printf "T\t%s\n", span(ligne, "title")
  printf "D\t%s\n", span(ligne, "description")
  printf "M\tetat\t%s\n", champ(ligne, "state")
  printf "M\tcree_le\t%s\n", champ(ligne, "createdAt")
  printf "M\tferme_le\t%s\n", champ(ligne, "closedAt")
  printf "M\turl\t%s\n", champ(ligne, "webUrl")

  # auteur : ancré sur son conteneur, « username » désignant aussi chaque assigné et chaque
  # commentateur. Le premier "author" de la ligne est celui du ticket (la requête le place avant
  # les widgets), mais on l'ancre quand même — une ancre coûte moins qu'une hypothèse.
  auteur = ""
  if (match(ligne, "\"author\":\\{\"username\":\"[^\"]*\"")) {
    auteur = substr(ligne, RSTART, RLENGTH)
    sub("^\"author\":\\{\"username\":\"", "", auteur); sub("\"$", "", auteur)
  }
  printf "M\tauteur\t%s\n", auteur

  ms = ""
  if (match(ligne, "\"milestone\":\\{\"title\":\"[^\"]*\"")) {
    ms = substr(ligne, RSTART, RLENGTH); sub("^\"milestone\":\\{\"title\":\"", "", ms); sub("\"$", "", ms)
  }
  printf "M\tmilestone\t%s\n", ms
  printf "M\tdebut\t%s\n", champ(ligne, "startDate")
  printf "M\techeance\t%s\n", champ(ligne, "dueDate")
  printf "M\ttemps_s\t%s\n", nombre(ligne, "totalTimeSpent")
  printf "M\tassignes\t%s\n", valeurs(tranche(ligne, "assignees"), "username")
  printf "M\tlies\t%s\n", valeurs(tranche(ligne, "linkedItems"), "iid")

  n = split(valeurs(tranche(ligne, "labels"), "title"), lbl, ",")
  for (i = 1; i <= n; i++) if (lbl[i] != "") printf "B\t%s\n", lbl[i]

  # Timelogs : le détail derrière le total. Peu nombreux (un par cycle /ticket-start→/ticket-finish),
  # donc repris intégralement plutôt que résumés — c'est la seule trace des 603 h.
  tl = tranche(ligne, "timelogs")
  for (k = 1; ; k++) {
    o = objet_n(tl, k); if (o == "") break
    u = ""
    if (match(o, "\"username\":\"[^\"]*\"")) {
      u = substr(o, RSTART, RLENGTH); sub("^\"username\":\"", "", u); sub("\"$", "", u)
    }
    printf "L\t%s\t%s\t%s\t%s\n", nombre(o, "timeSpent"), champ(o, "spentAt"), u, span(o, "summary")
  }

  # Notes : discussions -> notes -> note. On ne garde que "system":false — les notes système sont
  # le journal d'activité de GitLab (« added ~52011709 labels », « mentioned in issue #328 »), donc
  # de la mécanique de l'outil qu'on quitte, et elles citent des identifiants de labels GitLab qui
  # n'existeront nulle part après la bascule.
  disc = tranche(ligne, "discussions")
  for (d = 1; ; d++) {
    od = objet_n(disc, d); if (od == "") break
    notes = tranche(od, "notes")
    for (k = 1; ; k++) {
      o = objet_n(notes, k); if (o == "") break
      if (index(o, "\"system\":true") > 0) continue
      # Même marqueur que pour l'échéance d'un milestone : l'auteur d'une note est vide quand le
      # compte GitLab a été supprimé, et un champ vide au milieu d'une ligne décale les suivants
      # à la lecture (`IFS=$'\t' read` fusionne les tabulations). Le corps passerait alors dans la
      # date, `ncorps` serait vide, et le `[ -n "$ncorps" ] || continue` de l'appelant SAUTERAIT le
      # commentaire — une perte de donnée silencieuse au milieu d'un import irréversible (#345).
      u = "-"
      if (match(o, "\"username\":\"[^\"]*\"")) {
        u = substr(o, RSTART, RLENGTH); sub("^\"username\":\"", "", u); sub("\"$", "", u)
        if (u == "") u = "-"
      }
      printf "N\t%s\t%s\t%s\n", u, champ(o, "createdAt"), span(o, "body")
    }
  }
}
AWK
)"

# --- Référentiels --------------------------------------------------------------------------------
# Les deux fichiers sont du JSON REST brut. Ils sont accumulés dans un tampon avant d'être découpés,
# plutôt que traités ligne à ligne : `glab api` les rend aujourd'hui sur une seule ligne, mais rien
# ne l'impose — et un référentiel lu à moitié se traduirait par des tickets importés sans milestone,
# sans que rien n'échoue.
LABELS_AWK="$AWK_LIB$(cat <<'AWK'
{ buf = buf $0 }
END {
  for (k = 1; ; k++) {
    o = objet_n(dedans(buf), k); if (o == "") break
    nom = champ(o, "name"); if (nom == "") continue
    coul = champ(o, "color"); sub("^#", "", coul)
    if (coul == "") coul = "cccccc"
    printf "%s\t%s\t%s\n", nom, tolower(coul), span(o, "description")
  }
}
AWK
)"

# ⚠ UN CHAMP VIDE AU MILIEU D'UNE LIGNE DÉCALE TOUS LES SUIVANTS, et le lecteur ne le voit pas :
# la tabulation est un caractère « IFS whitespace » pour bash, donc `IFS=$'\t' read` fusionne deux
# tabulations consécutives en un seul séparateur. Un milestone SANS échéance faisait ainsi lire sa
# DESCRIPTION comme titre et son TITRE comme échéance — donc un milestone créé sous le mauvais nom,
# une `due_on` absurde, et une jointure ticket → milestone qui ne retrouve plus rien (#345). Le
# champ optionnel voyage donc avec un marqueur, comme dans le journal (`journalise`, `${3:--}`),
# que l'appelant retire. Le titre, lui, n'est jamais vide — `t == ""` fait sauter l'objet.
MILESTONES_AWK="$AWK_LIB$(cat <<'AWK'
{ buf = buf $0 }
END {
  for (k = 1; ; k++) {
    o = objet_n(dedans(buf), k); if (o == "") break
    t = span(o, "title"); if (t == "") continue
    d = champ(o, "due_date"); if (d == "") d = "-"
    printf "%s\t%s\t%s\t%s\t%s\n", nombre(o, "iid"), champ(o, "state"), d, t, span(o, "description")
  }
}
AWK
)"

# Retrouve le numéro GitHub d'un milestone déjà présent, par son titre. Séparé pour rester lisible
# là où il sert (une reprise), et parce qu'il compare des titres ENCORE ÉCHAPPÉS des deux côtés.
# Retrouver un milestone GitHub par son titre demande de comparer DEUX APIS QUI N'ÉCHAPPENT PAS
# PAREIL : GitLab rend « Projets & espace » (comme pour < et >, cf. `lisible()` dans l'export),
# GitHub rend « Projets & espace ». Comparer les formes brutes ferait donc échouer la recherche sur
# les trois seuls titres du backlog qui contiennent « & » — et seulement sur ceux-là, ce qui est la
# pire forme de défaut. Les deux côtés sont ramenés au même texte avant comparaison.
MILESTONE_NUM_AWK="$AWK_LIB$(cat <<'AWK'
function denorm(s) {
  gsub(/\\u0026/, "\\&", s); gsub(/\\u003c/, "<", s); gsub(/\\u003e/, ">", s)
  return s
}
{ buf = buf $0 }
END {
  # Le titre cherché arrive par l'environnement et non par -v : voir `fait` ci-dessus, awk
  # interpréterait « & » dans la valeur d'un -v.
  cible = denorm(ENVIRON["MAESTRO_MS_TITRE"])
  for (k = 1; ; k++) {
    o = objet_n(dedans(buf), k); if (o == "") break
    if (denorm(span(o, "title")) == cible) { print nombre(o, "number"); exit }
  }
}
AWK
)"

# =================================================================================================
# Journal — la reprise, et rien d'autre
# =================================================================================================
# Append seul, une ligne par étape franchie. C'est ce qui rend l'import reprenable sans doublon, et
# c'est aussi la seule mémoire de la correspondance milestone -> numéro GitHub (les milestones ont
# leur propre séquence, indépendante de celle des issues).
#
#   label     <nom>        -
#   milestone <titre>      <numero>
#   issue     <iid>        <numero>     <url>
#   meta      <iid>        <numero>     -
#   notes     <iid>        <numero>     <n>
#   etat      <iid>        <numero>     <closed|open>
journalise() { printf '%s\t%s\t%s\t%s\n' "$1" "$2" "${3:--}" "${4:--}" >> "$JOURNAL"; }

# fait <type> <cle> / valeur_journal <type> <cle> — la clé est une DONNÉE, et elle voyage par
# l'ENVIRONNEMENT. Deux pièges se cumulaient ici, chacun invisible sauf sur trois titres du backlog :
#
#   1. la clé n'est pas un MOTIF. Trois milestones contiennent « & », que GitLab rend « & » ;
#      passé à grep, ce « \u » est un échappement d'expression régulière.
#   2. la clé n'est pas un LITTÉRAL awk. `awk -v c="…"` interprète les séquences d'échappement DE LA
#      VALEUR : « & » y devient « u0026 » (gawk le dit — « escape sequence \u treated as plain
#      u »), donc la comparaison porte sur un texte que personne n'a écrit. ENVIRON, lui, rend les
#      octets tels quels.
#
# Le second est le dangereux : `fait` qui se trompe ne coûte qu'un arrêt bruyant, mais
# `valeur_journal` qui se trompe rend une chaîne vide — et un milestone introuvable ne fait pas
# échouer un ticket, il l'importe SANS milestone. Tous les tickets de trois phases seraient arrivés
# nus sur GitHub, sans un mot, sur un import qui ne se rejoue pas.
fait() {
  [ -s "$JOURNAL" ] || return 1
  MAESTRO_JT="$1" MAESTRO_JC="$2" LC_ALL=C awk -F '\t' \
    '$1 == ENVIRON["MAESTRO_JT"] && $2 == ENVIRON["MAESTRO_JC"] { trouve = 1; exit } END { exit !trouve }' "$JOURNAL"
}
# valeur_journal <type> <cle> -> la 3e colonne de la dernière ligne correspondante.
valeur_journal() {
  [ -s "$JOURNAL" ] || return 1
  MAESTRO_JT="$1" MAESTRO_JC="$2" LC_ALL=C awk -F '\t' \
    '$1 == ENVIRON["MAESTRO_JT"] && $2 == ENVIRON["MAESTRO_JC"] { v = $3 } END { if (v == "") exit 1; print v }' "$JOURNAL"
}

# =================================================================================================
# Écriture GitHub — un seul point de passage, avec la temporisation et les reculs
# =================================================================================================
# TOUT ce qui écrit passe par gh_ecrire. C'est ce qui garantit qu'il n'existe qu'UN endroit où la
# cadence est tenue et où les limites d'API sont traitées — et, en --check, qu'il n'existe aucun
# chemin d'écriture du tout (la fonction refuse de s'exécuter).
#
# Les limites secondaires de GitHub ne sont pas les limites primaires : elles ne s'annoncent pas dans
# `x-ratelimit-remaining` et tombent sur les créations en rafale. La réponse est un recul franc
# (60 s, 120 s, 300 s, 600 s) plutôt qu'une temporisation fine — insister vite est précisément ce
# qui les prolonge. On ne parallélise JAMAIS : l'ordre est le livrable.
GH_ERR=""
gh_ecrire() {
  local methode="$1" chemin="$2" payload="${3:-}"
  if [ "$mode" = "check" ]; then
    echec "gh_ecrire appelé en --check — bug, aucune écriture ne doit avoir lieu."
    return 1
  fi
  # `</dev/null` sur chaque appel : gh est invoqué DEPUIS des boucles `while read`, dont l'entrée
  # standard est le flux qu'elles parcourent. Un sous-processus qui la lirait, même par accident,
  # avalerait des tickets — panne silencieuse et impossible à relire dans un journal.
  local essai attente=60 out code
  for essai in 1 2 3 4 5; do
    if [ -n "$payload" ]; then
      out="$(gh api --method "$methode" "$chemin" --input "$payload" </dev/null 2>&1)"; code=$?
    else
      out="$(gh api --method "$methode" "$chemin" </dev/null 2>&1)"; code=$?
    fi
    if [ "$code" -eq 0 ]; then
      GH_ERR=""
      printf '%s' "$out"
      sleep "$PAUSE"
      return 0
    fi
    GH_ERR="$out"
    # Un objet déjà présent (label, milestone) n'est pas une erreur à retenter : c'est une reprise.
    # L'appelant décide, on lui rend la main tout de suite.
    case "$out" in
      *"already_exists"*|*"already exists"*) return 2 ;;
    esac
    case "$out" in
      *"secondary rate limit"*|*"abuse detection"*|*"rate limit"*|*"was submitted too quickly"*)
        # Sur STDERR, et ce n'est pas un détail : l'appelant capture la sortie de cette fonction
        # dans une variable (« rep="$(gh_ecrire …)" »). Une ligne de progression écrite sur stdout
        # se retrouverait DANS la réponse JSON, d'où un numéro d'objet illisible juste après une
        # limite d'API — c'est-à-dire au pire moment, et seulement là.
        [ "$format" = "tsv" ] || printf '  %s⏳%s limite d'"'"'API — pause de %s s (tentative %s/5)\n' \
          "$C_Y" "$C_0" "$attente" "$essai" >&2
        sleep "$attente"
        attente=$((attente * 2)); [ "$attente" -gt 600 ] && attente=600
        ;;
      *"dial tcp"*|*"connectex"*|*"connection reset"*|*"EOF"*|*"timeout"*|*"TLS handshake"*|*"no such host"*)
        # PANNE DE TRANSPORT, et non refus de l'API : rien n'a été décidé en face, seule la
        # conversation a échoué. Elle mérite donc la même patience qu'une limite — un import de
        # deux heures traverse forcément quelques secondes de réseau absent, et s'arrêter dessus
        # revient à faire dépendre une action à sens unique de la qualité d'un lien Wi-Fi.
        # Mesuré : la première tentative complète est morte là-dessus au 73e objet sur 345.
        [ "$format" = "tsv" ] || printf '  %s⇄%s réseau injoignable — nouvelle tentative dans %s s (%s/5)\n' \
          "$C_Y" "$C_0" "$((essai * 15))" "$essai" >&2
        sleep $((essai * 15))
        ;;
      *)
        # Refus explicite de l'API (droits, payload, dépôt) : la réponse serait la même cinq fois,
        # et deux tentatives espacées suffisent à écarter un aléa. Au-delà on retarde un message.
        [ "$essai" -ge 2 ] && return 1
        sleep 5
        ;;
    esac
  done
  return 1
}

# gh_lire <chemin> -> lecture seule, autorisée même en --check. RETENTE tant que la réponse est
# VIDE, jamais sur son contenu : « gh api » écrit le corps de la réponse sur stdout même en 404, donc
# un JSON d'erreur est une réponse (l'objet n'existe pas — inutile de redemander) là où le vide n'en
# est pas une (on n'a pas pu savoir).
#
# La distinction n'est pas théorique : elle s'est présentée deux fois pendant #340, et les deux fois
# le silence a été pris pour un fait. Une lecture ratée a arrêté l'import au 18e objet en se lisant
# « je ne peux pas mesurer », et une autre a fait déclarer #48 absent d'un dépôt où il était.
gh_lire() {
  local essai out
  for essai in 1 2 3; do
    out="$(gh api "$1" </dev/null 2>/dev/null)"
    if [ -n "$out" ]; then printf '%s' "$out"; return 0; fi
    sleep $((essai * 3))
  done
  return 1
}

# gh_dernier_numero -> le plus grand numéro consommé sur le dépôt (issues ET pull requests, qui
# partagent UNE SEULE séquence), ou 0. C'est la mesure sur laquelle repose tout l'invariant d'ordre :
# elle est demandée AVANT chaque création, donc 345 fois.
#
# ELLE RETENTE, et c'est le correctif d'un vrai arrêt (mesuré : import stoppé au 18e objet sur une
# réponse vide). Le défaut n'était pas le réseau mais l'asymétrie : toutes les ÉCRITURES avaient
# leurs reprises, et la LECTURE qui les autorise n'en avait aucune — un aléa d'une seconde suffisait
# à arrêter un import de quarante minutes. Une mesure qui garde 345 écritures doit être au moins
# aussi robuste qu'elles.
#
# L'échec définitif reste franc : rendre « 0 » sur une lecture ratée ferait croire le dépôt vierge
# et relancerait la séquence depuis le début. Vide veut dire « je ne sais pas », et l'appelant
# s'arrête — jamais « il n'y a rien ».
# gh_existe <n> -> 0 si l'objet #<n> existe sur le dépôt. Lecture FORTEMENT COHÉRENTE : un GET sur un
# numéro précis répond dès l'instant de la création, là où la liste peut encore l'ignorer. Un 404
# rend un corps JSON — donc non vide, donc `gh_lire` le rend sans retenter : c'est la marque de
# l'objet DEMANDÉ qu'on exige, et non le simple fait d'avoir reçu quelque chose.
gh_existe() {
  local out
  out="$(gh_lire "repos/$DEPOT/issues/$1")" || return 1
  case "$out" in
    *'"number":'"$1"','*) return 0 ;;
  esac
  return 1
}

gh_dernier_numero() {
  local out n
  out="$(gh_lire "repos/$DEPOT/issues?state=all&sort=created&direction=desc&per_page=1")" || return 1
  case "$out" in
    '[]'*) n=0 ;;
    *) n="$(printf '%s' "$out" | grep -o '"number":[0-9]\+' | head -1 | grep -o '[0-9]\+')" ;;
  esac
  [ -n "$n" ] || return 1
  # LA LISTE EST EN RETARD SUR LA CRÉATION, et l'invariant compare au rang près : les deux ensemble
  # transforment une réplication d'index en fausse rupture. Mesuré pendant l'import de #340 — la
  # liste rendait #252 alors que #253 existait déjà (GET direct : 200, titre conforme), et la
  # création suivante s'est arrêtée sur « le dépôt est à #252, on allait créer #254 » alors que la
  # séquence était intacte : 253 objets, numéros #1 à #253, aucun trou.
  #
  # Le coût de l'erreur n'est pas symétrique. Un appel de plus par création ne coûte qu'un appel ;
  # un arrêt en code 4 réclame un arbitrage humain au milieu d'une action à sens unique, et il
  # accuse la DONNÉE d'un défaut de la MESURE — le pire endroit où se tromper ici.
  # On avance donc tant que le suivant RÉPOND, ce qui ne dépend d'aucun index répliqué.
  local avance=0
  while [ "$avance" -lt 25 ] && gh_existe "$((n + 1))"; do
    n=$((n + 1)); avance=$((avance + 1))
  done
  printf '%s' "$n"
}

# =================================================================================================
# Prérequis
# =================================================================================================
verifier_prerequis() {
  section "1. Prérequis"
  local souci=0

  local f
  for f in "$JSONL" "$MANIFESTE" "$TROUS" "$LABELS_JSON" "$MILESTONES_JSON"; do
    if [ ! -s "$f" ]; then
      echec "export incomplet : ${f#"$racine/"} absent ou vide — jouer scripts/migration/export-gitlab.sh"
      souci=1
    fi
  done
  [ "$souci" -eq 0 ] || return 1

  # Le verdict de l'export fait foi, et il est LU plutôt que supposé. #337 distingue « produit » de
  # « digne de confiance » (son code 3) : importer sur un export non vérifié, c'est créer 345 objets
  # irréversibles sur une matière dont personne n'a dit qu'elle était bonne.
  if LC_ALL=C grep -q '^vérification : OK' "$RESUME_EXPORT" 2>/dev/null; then
    kv "export vérifié (#337)" "oui ($C_G✓$C_0)"
  elif LC_ALL=C grep -q '^vérification' "$RESUME_EXPORT" 2>/dev/null; then
    kv "export vérifié (#337)" "${C_R}NON — $(LC_ALL=C sed -n 's/^vérification *: *//p' "$RESUME_EXPORT" | head -1)$C_0"
    echec "l'export n'a pas passé sa propre vérification — ne pas importer dessus."
    souci=1
  else
    kv "export vérifié (#337)" "${C_Y}inconnu — resume.txt illisible$C_0"
    souci=1
  fi
  kv "export produit le" "$(LC_ALL=C sed -n 's/^produit le //p' "$RESUME_EXPORT" 2>/dev/null | head -1)"

  # LA FRAÎCHEUR EST UN PRÉREQUIS, pas une curiosité — et c'est le piège le moins visible de cette
  # étape. Un export est un INSTANTANÉ : tout ce que GitLab a bougé depuis (une case cochée dans un
  # parent, un commentaire, un ticket créé) n'existe pas dedans, donc n'existera pas côté GitHub. Or
  # l'import ne se rejoue pas : ce qui manque à cet instant manquera pour toujours. Mesuré pendant
  # #340 sur un export vieux de quelques heures : la description de #335 y différait de 3 octets sur
  # 5927 (« - [ ] #336 » au lieu de « - [x] »), écart qu'aucune lecture à l'œil n'aurait attrapé.
  local age_s="" mtime
  mtime="$(stat -c %Y "$JSONL" 2>/dev/null)"
  if [ -n "$mtime" ]; then
    age_s=$(( $(date +%s) - mtime ))
    if [ "$age_s" -lt 3600 ]; then
      kv "âge de l'export" "$((age_s / 60)) min ($C_G✓$C_0)"
    else
      kv "âge de l'export" "$C_Y$((age_s / 3600)) h — rejouer export-gitlab.sh juste avant d'importer$C_0"
      note "l'import ne se rejoue pas : ce que GitLab a bougé depuis ne sera jamais repris."
    fi
  fi

  # Mojibake ANTÉRIEUR : la décision que #337 a explicitement laissée à ce lot. Elle est prise, et
  # elle est « IMPORTER TEL QUEL » — pour trois raisons qui tiennent ensemble :
  #   - l'import a un seul travail, être fidèle. Un import qui « améliore » sa source ne peut plus
  #     être vérifié par comparaison, et c'est la seule vérification dont on dispose ici ;
  #   - deux des cinq tickets (#141, #233) DOCUMENTENT le mojibake et en citent des exemples : une
  #     réparation en gros les abîmerait, eux qui sont corrects ;
  #   - réparer les trois autres (#79, #92, #102, abîmés dans GitLab depuis 2026-07) est un geste
  #     sur la SOURCE, à faire avant l'export, sur des tickets fermés depuis longtemps. C'est une
  #     décision humaine à un endroit réversible, pas une transformation cachée dans un import
  #     irréversible.
  # Ce qui compte est que ça se sache : le compter ici, c'est refuser de le découvrir après coup.
  if [ -s "$SOURCE/mojibake-source.txt" ]; then
    # `grep -c` IMPRIME son compte puis sort en 1 quand ce compte est zéro : le repli
    # `|| printf '0'` ajoutait donc un second « 0 » à celui que grep venait d'écrire, et le
    # `[ "$n_moji" -gt 0 ]` suivant partait en « integer expression expected » sur stderr —
    # exactement dans le cas nominal, celui d'un export sans mojibake (#345).
    local n_moji
    n_moji="$(LC_ALL=C grep -cv '^#' "$SOURCE/mojibake-source.txt" 2>/dev/null)"
    [ -n "$n_moji" ] || n_moji=0
    [ "$n_moji" -gt 0 ] && kv "mojibake déjà dans GitLab" "$n_moji ticket(s), importés TELS QUELS (décision #340)"
  fi

  if ! command -v gh >/dev/null 2>&1; then
    echec "gh absent — installer le CLI GitHub."; return 1
  fi
  local depot_json
  depot_json="$(gh_lire "repos/$DEPOT")"
  if [ -z "$depot_json" ]; then
    echec "dépôt $DEPOT illisible — jeton sans accès, ou dépôt inexistant."
    note "le compte GitHub du projet est isolé par GH_CONFIG_DIR (#334) : vérifier « gh auth status »."
    return 1
  fi
  kv "dépôt cible" "$DEPOT"
  kv "visibilité" "$(printf '%s' "$depot_json" | grep -o '"visibility":"[^"]*"' | head -1 | cut -d'"' -f4)"
  if printf '%s' "$depot_json" | grep -q '"has_issues":false'; then
    echec "les issues sont DÉSACTIVÉES sur $DEPOT — rien à importer nulle part."
    souci=1
  fi

  # Virginité de la cible. Le contrôle n'est PAS « le dépôt est vide » mais « le prochain numéro est
  # celui qu'on attend » : sur une reprise, 200 objets sont déjà là et c'est normal. La question est
  # la même dans les deux cas — d'où une seule mesure, et un seul invariant.
  local dernier
  dernier="$(gh_dernier_numero)"
  if [ -z "$dernier" ]; then
    kv "numérotation de la cible" "${C_Y}INDÉTERMINÉE — le jeton ne lit pas les issues$C_0"
    note "cf. #336 : ajouter la portée Issues (lecture/écriture) au jeton du poste suffit."
    souci=1
  else
    kv "dernier numéro consommé" "$dernier"
  fi

  # L'ÉCRITURE ne se prouve qu'en écrivant — mais elle se prouve GRATUITEMENT : la première écriture
  # de l'import est un LABEL, et les labels ne consomment aucun numéro d'issue. Un jeton en lecture
  # seule échoue donc là, avant que la séquence ait commencé, et sans rien coûter. C'est la raison
  # d'être de l'ordre « labels -> milestones -> issues », au-delà des dépendances de données.
  local perms
  perms="$(printf '%s' "$depot_json" | grep -o '"permissions":{[^}]*}' | head -1)"
  case "$perms" in
    *'"push":true'*) kv "droit d'écriture annoncé" "oui ($C_G✓$C_0)" ;;
    *'"push":false'*) kv "droit d'écriture annoncé" "${C_R}non$C_0"; souci=1 ;;
    *) kv "droit d'écriture annoncé" "non annoncé — prouvé au premier label créé" ;;
  esac

  return "$souci"
}

# =================================================================================================
# Le plan — calculé avant toute écriture, et identique en --check et en import
# =================================================================================================
# Écrit plan.tsv : une ligne par NUMÉRO CIBLE, de 1 au plus grand iid. C'est la même fonction qui
# sert au plan et à l'import : deux calculs séparés pourraient diverger, et la divergence ne se
# verrait qu'après coup.
PLAN_MAX=0
PLAN_TICKETS=0
PLAN_TROUS=0
construire_plan() {
  mkdir -p "$TRAVAIL" || return 1
  local iids
  iids="$(LC_ALL=C awk -F '\t' 'NR > 1 { print $1 }' "$MANIFESTE" | sort -n)"
  [ -n "$iids" ] || { echec "manifeste sans iid."; return 1; }
  PLAN_MAX="$(printf '%s\n' "$iids" | tail -1)"

  {
    printf '# numero\tnature\tiid\tetat\ttitre\n'
    LC_ALL=C awk -F '\t' -v max="$PLAN_MAX" '
      FILENAME == ARGV[1] && FNR > 1 { etat[$1] = $2; titre[$1] = $18; vu[$1] = 1; next }
      FILENAME == ARGV[2] { trou[$1 + 0] = 1; next }
      END {
        for (n = 1; n <= max; n++) {
          if (n in vu)       printf "%d\tticket\t%d\t%s\t%s\n", n, n, etat[n], titre[n]
          else if (n in trou) printf "%d\tbouche-trou\t%d\t-\t-\n", n, n
          else                printf "%d\tMANQUANT\t%d\t-\t-\n", n, n
        }
      }' "$MANIFESTE" "$TROUS"
  } > "$PLAN" || return 1

  PLAN_TICKETS="$(LC_ALL=C awk -F '\t' '$2 == "ticket"' "$PLAN" | wc -l | tr -d ' ')"
  PLAN_TROUS="$(LC_ALL=C awk -F '\t' '$2 == "bouche-trou"' "$PLAN" | wc -l | tr -d ' ')"

  # Un numéro qui n'est ni un ticket ni un trou déclaré signerait un désaccord entre le manifeste et
  # trous.txt — donc un export incohérent. Le laisser passer créerait un décalage silencieux, c'est
  # exactement ce que tout ce fichier cherche à éviter.
  local manquants
  manquants="$(LC_ALL=C awk -F '\t' '$2 == "MANQUANT" { print $1 }' "$PLAN" | tr '\n' ' ')"
  if [ -n "$manquants" ]; then
    echec "numéros ni ticket ni trou déclaré : $manquants"
    note "manifeste.tsv et trous.txt sont en désaccord — rejouer l'export avant tout import."
    return 1
  fi
  return 0
}

afficher_plan() {
  section "2. Plan — un numéro GitHub par iid GitLab"
  kv "plage à reconstituer" "#1 → #$PLAN_MAX"
  kv "tickets à importer" "$PLAN_TICKETS"
  kv "bouche-trous à créer" "$PLAN_TROUS ($(LC_ALL=C tr '\n' ' ' < "$TROUS" | sed 's/ $//'))"
  kv "objets à créer (numéros)" "$PLAN_MAX"

  local n_lbl n_ms n_ferme n_notes n_meta total
  n_lbl="$(LC_ALL=C awk "$LABELS_AWK" "$LABELS_JSON" | wc -l | tr -d ' ')"
  n_ms="$(LC_ALL=C awk "$MILESTONES_AWK" "$MILESTONES_JSON" | wc -l | tr -d ' ')"
  n_ferme="$(LC_ALL=C awk -F '\t' 'NR > 1 && ($4 == "CLOSED" || $2 == "bouche-trou")' "$PLAN" | wc -l | tr -d ' ')"
  n_notes="$(LC_ALL=C awk -F '\t' 'NR > 1 { n += $15 } END { printf "%d", n }' "$MANIFESTE")"
  n_meta="$PLAN_TICKETS"
  total=$((n_lbl + 1 + n_ms + PLAN_MAX + n_meta + n_notes + n_ferme))

  kv "labels à créer" "$((n_lbl + 1))  (dont $LABEL_TROU)"
  kv "milestones à créer" "$n_ms"
  kv "commentaires de métadonnées" "$n_meta"
  kv "commentaires humains repris" "$n_notes"
  kv "fermetures" "$n_ferme"
  kv "ÉCRITURES AU TOTAL" "$total"
  # La durée est bornée par les limites secondaires de GitHub sur les créations en rafale, pas par
  # la temporisation choisie. On annonce donc les deux : ce qu'on demande, et ce que la plateforme
  # décidera. Une reprise coûte zéro — le journal fait foi — donc dépasser l'estimation est sans gravité.
  # Partie entière de --pause : l'estimation est un ordre de grandeur, et `$(( ))` ne connaît pas
  # les décimales — un « --pause 0.5 » ferait échouer le calcul, pas l'import.
  local p_ent="${PAUSE%%.*}"; [ -n "$p_ent" ] || p_ent=0
  kv "durée au rythme demandé" "≈ $((total * (p_ent + 1) / 60)) min (pause $PAUSE s + appel)"
  note "les limites secondaires de GitHub sur les créations en rafale peuvent l'allonger nettement ;"
  note "le script recule et reprend tout seul, et un import coupé se relance sans doublon."

  local deja
  deja="$( [ -s "$JOURNAL" ] && LC_ALL=C awk -F '\t' '$1 == "issue"' "$JOURNAL" | wc -l | tr -d ' ' || printf '0')"
  [ "$deja" -gt 0 ] && kv "déjà importé (journal)" "$deja objet(s) — la reprise partira du suivant"

  if [ "$format" = "tsv" ]; then
    LC_ALL=C awk -F '\t' 'NR > 1 { printf "plan\t%s\t%s\t%s\n", $1, $2, $3 }' "$PLAN"
  else
    note ""
    note "plan complet (un numéro par ligne) : ${PLAN#"$racine/"}"
    note "premiers : $(LC_ALL=C awk -F '\t' 'NR > 1 && NR <= 4 { printf "#%s(%s) ", $1, $2 }' "$PLAN")…"
    note "derniers : $(LC_ALL=C awk -F '\t' 'NR > 1' "$PLAN" | tail -3 | LC_ALL=C awk -F '\t' '{ printf "#%s(%s) ", $1, $2 }')"
  fi
}

# =================================================================================================
# Étapes d'écriture
# =================================================================================================

# --- Labels --------------------------------------------------------------------------------------
# Première écriture de l'import, et donc la preuve que le jeton écrit — sans consommer un seul numéro
# d'issue (cf. verifier_prerequis).
importer_labels() {
  section "3. Labels"
  local nom coul desc payload cree=0 deja=0 rate=0 code
  payload="$TRAVAIL/payload.json"
  while IFS=$'\t' read -r nom coul desc; do
    [ -n "$nom" ] || continue
    if fait "label" "$nom"; then deja=$((deja + 1)); continue; fi
    printf '{"name":"%s","color":"%s","description":"%s"}' "$nom" "$coul" "$desc" > "$payload"
    gh_ecrire POST "repos/$DEPOT/labels" "$payload" >/dev/null; code=$?
    case "$code" in
      0) journalise label "$nom"; cree=$((cree + 1)) ;;
      2) journalise label "$nom"; deja=$((deja + 1)) ;;
      *) echec "label « $nom » : $(printf '%s' "$GH_ERR" | head -1)"; rate=$((rate + 1)) ;;
    esac
  done < <(LC_ALL=C awk "$LABELS_AWK" "$LABELS_JSON")

  # Le label des bouche-trous n'existe pas côté GitLab : il naît ici, avec les objets qu'il désigne.
  if ! fait "label" "$LABEL_TROU"; then
    printf '{"name":"%s","color":"ededed","description":"%s"}' "$LABEL_TROU" \
      "Numéro réservé par la migration : aucun ticket GitLab correspondant (#340)." > "$payload"
    gh_ecrire POST "repos/$DEPOT/labels" "$payload" >/dev/null; code=$?
    case "$code" in
      0|2) journalise label "$LABEL_TROU"; cree=$((cree + 1)) ;;
      *) echec "label « $LABEL_TROU » : $(printf '%s' "$GH_ERR" | head -1)"; rate=$((rate + 1)) ;;
    esac
  fi
  rm -f "$payload"
  kv "labels créés / déjà là / en échec" "$cree / $deja / $rate"
  [ "$rate" -eq 0 ]
}

# --- Milestones ----------------------------------------------------------------------------------
# Créés AVANT les tickets : un ticket ne porte que le NOM de son milestone, et l'API des issues
# demande son NUMÉRO GitHub. La correspondance nom -> numéro vit dans le journal, donc elle survit à
# une reprise sans re-interroger GitHub.
importer_milestones() {
  section "4. Milestones"
  local iid etat echeance titre desc payload cree=0 deja=0 rate=0 code rep num due
  payload="$TRAVAIL/payload.json"
  while IFS=$'\t' read -r iid etat echeance titre desc; do
    [ -n "$titre" ] || continue
    if fait "milestone" "$titre"; then deja=$((deja + 1)); continue; fi
    [ "$echeance" = "-" ] && echeance=""      # marqueur de champ vide, cf. MILESTONES_AWK
    # GitLab rend une DATE (2026-08-05), GitHub attend un instant. Midi UTC plutôt que minuit :
    # l'affichage se fait dans le fuseau du lecteur, et minuit bascule d'un jour dès qu'on est à
    # l'ouest — la date rendue ne serait pas celle du milestone.
    due=""
    [ -n "$echeance" ] && due=',"due_on":"'"$echeance"'T12:00:00Z"'
    printf '{"title":"%s","state":"%s","description":"%s"%s}' \
      "$titre" "$([ "$etat" = "closed" ] && printf 'closed' || printf 'open')" "$desc" "$due" > "$payload"
    rep="$(gh_ecrire POST "repos/$DEPOT/milestones" "$payload")"; code=$?
    if [ "$code" -eq 0 ]; then
      num="$(printf '%s' "$rep" | grep -o '"number":[0-9]\+' | head -1 | grep -o '[0-9]\+')"
      journalise milestone "$titre" "$num"; cree=$((cree + 1))
    elif [ "$code" -eq 2 ]; then
      # Déjà présent : on retrouve son numéro par la liste plutôt que de le supposer.
      num="$(gh_lire "repos/$DEPOT/milestones?state=all&per_page=100" \
             | MAESTRO_MS_TITRE="$titre" LC_ALL=C awk "$MILESTONE_NUM_AWK")"
      if [ -n "$num" ]; then journalise milestone "$titre" "$num"; deja=$((deja + 1))
      else echec "milestone « $titre » existe mais son numéro est introuvable."; rate=$((rate + 1)); fi
    else
      echec "milestone « $titre » : $(printf '%s' "$GH_ERR" | head -1)"; rate=$((rate + 1))
    fi
  done < <(LC_ALL=C awk "$MILESTONES_AWK" "$MILESTONES_JSON")
  rm -f "$payload"
  kv "milestones créés / déjà là / en échec" "$cree / $deja / $rate"
  [ "$rate" -eq 0 ]
}

# --- Un objet de la séquence ---------------------------------------------------------------------
# creer_objet <numéro attendu> <fichier payload> -> écrit « <numéro><TAB><url> » sur stdout.
#
# L'URL sort par STDOUT et non par une variable : la fonction est appelée dans une substitution de
# commande (« $(creer_objet …) »), donc dans un SOUS-SHELL — une variable qu'elle poserait mourrait
# avec lui, et le journal garderait un « - » à la place de chaque URL. Défaut sans gravité pour
# l'import lui-même, mais le journal EST la trace d'audit d'une action irréversible.
# C'est ici que vit l'invariant. Trois contrôles, dans cet ordre :
#   AVANT  : le dernier numéro consommé vaut-il attendu-1 ? sinon on ne crée rien.
#   RETRY  : un POST dont la réponse s'est perdue a pu ABOUTIR. Avant de retenter, on relit le
#            dernier numéro : s'il vaut déjà l'attendu, l'objet existe — retenter le doublerait, et
#            un doublon décale tout le reste aussi sûrement qu'un objet manquant.
#   APRÈS  : le numéro obtenu vaut-il l'attendu ?
creer_objet() {
  local attendu="$1" payload="$2" avant rep num url
  avant="$(gh_dernier_numero)"
  if [ -z "$avant" ]; then
    echec "#$attendu : dernier numéro illisible — arrêt avant écriture."
    return 1
  fi
  # Deux causes mènent ici, et les confondre coûte cher : l'objet est DÉJÀ LÀ (arrêt tombé entre le
  # POST et sa ligne de journal — rien n'est décalé, une ligne suffit à reprendre), ou la séquence a
  # réellement dérivé. Le message unique envoyait chercher un décalage inexistant et faisait passer
  # une reprise d'une ligne pour une avarie irréparable. C'est exactement ce qui est arrivé sur #183.
  if [ "$avant" -eq "$attendu" ]; then
    echec "#$attendu existe déjà côté GitHub alors que le journal l'ignore."
    note_err "l'arrêt précédent est tombé ENTRE le POST et sa ligne de journal : rien n'est décalé."
    note_err "vérifier les octets : bash scripts/migration/import-github.sh --payload $attendu"
    note_err "puis reprendre en ajoutant sa ligne au journal :"
    note_err "  printf 'issue\\t%s\\t%s\\t%s\\n' $attendu $attendu https://github.com/$DEPOT/issues/$attendu >> ${JOURNAL#"$racine/"}"
    return 4
  fi
  if [ "$avant" -ne $((attendu - 1)) ]; then
    echec "SÉQUENCE : le dépôt est à #$avant, on allait créer #$attendu."
    return 4
  fi
  rep="$(gh_ecrire POST "repos/$DEPOT/issues" "$payload")"
  if [ $? -ne 0 ]; then
    num="$(gh_dernier_numero)"
    if [ "$num" = "$attendu" ]; then
      # Le POST avait abouti ; seule la réponse s'est perdue. On récupère l'URL et on continue.
      rep="$(gh_lire "repos/$DEPOT/issues/$attendu")"
    else
      echec "#$attendu : création impossible — $(printf '%s' "$GH_ERR" | head -2 | tr '\n' ' ')"
      return 1
    fi
  fi
  num="$(printf '%s' "$rep" | grep -o '"number":[0-9]\+' | head -1 | grep -o '[0-9]\+')"
  if [ "$num" != "$attendu" ]; then
    echec "SÉQUENCE ROMPUE : l'iid $attendu a reçu le numéro ${num:-?}."
    return 4
  fi
  url="$(printf '%s' "$rep" | grep -o '"html_url":"[^"]*"' | head -1 | cut -d'"' -f4)"
  printf '%s\t%s' "$num" "$url"
  return 0
}

# --- Corps du commentaire de métadonnées ---------------------------------------------------------
# Construit DIRECTEMENT sous forme échappée (les « \n » sont littéraux) : aucune chaîne n'est donc
# encodée puis ré-encodée, et les seuls fragments venus de GitLab (résumés de timelog) y arrivent
# déjà échappés. C'est la même discipline que pour les descriptions, appliquée au texte qu'on écrit.
#
# Le bloc HTML de tête est invisible au rendu et porte la version du format : c'est lui que relira
# l'outillage d'après-bascule (#339/#341) pour retrouver dates et temps passé. Le tableau en dessous
# est pour les humains. Les deux disent la même chose, et ils sont posés du même geste.
corps_meta() {
  local iid="$1" url="$2" auteur="$3" cree_le="$4" debut="$5" echeance="$6" temps="$7" assignes="$8" lies="$9"
  local c lignes=""
  c='<!-- maestro:meta v1 iid='"$iid"' temps_s='"$temps"
  [ -n "$debut" ] && c="$c debut=$debut"
  [ -n "$echeance" ] && c="$c echeance=$echeance"
  [ -n "$assignes" ] && c="$c assignes=$assignes"
  [ -n "$lies" ] && c="$c lies=$lies"
  c="$c -->"'\n'
  c="$c"'**Importé de GitLab** · [`#'"$iid"'`]('"$url"')'
  [ -n "$auteur" ] && c="$c"' · ouvert par `@'"$auteur"'`'
  [ -n "$cree_le" ] && c="$c"' le '"${cree_le%%T*}"
  c="$c"'\n\n'

  if [ -n "$debut" ] || [ -n "$echeance" ]; then
    local d="—"
    [ -n "$debut" ] && d="début $debut"
    [ -n "$echeance" ] && d="$d · échéance $echeance"
    lignes="$lignes"'| Dates | '"$d"' |\n'
  fi
  [ "$temps" -gt 0 ] 2>/dev/null && lignes="$lignes"'| Temps passé | '"$(duree "$temps")"' ('"$temps"' s) |\n'
  # Les noms d'utilisateur GitLab ne sont PAS des comptes GitHub : ils sont écrits « @nom » sans
  # lien volontaire — mentionner un homonyme GitHub sur 341 tickets serait une notification de masse
  # envoyée à un inconnu, et c'est irréversible.
  [ -n "$assignes" ] && lignes="$lignes"'| Assigné (GitLab) | '"${assignes//,/, }"' |\n'
  # Les liens « related » de GitLab n'ont pas d'équivalent natif sur GitHub. Écrits « #N » ils sont
  # rendus comme des liens ET produisent une référence en retour sur la cible : le lien existe donc
  # dans les deux sens, ce qui est ce qu'on lui demandait. Les liens parent ↔ lot, eux, sont déjà
  # dans les descriptions (« Sous-ticket de #335 », checklist « ## Sous-tickets ») et se rendent
  # d'eux-mêmes — l'import n'a rien à faire pour eux, et c'est pour ça qu'il n'invente rien.
  [ -n "$lies" ] && lignes="$lignes"'| Liés | #'"${lies//,/, #}"' |\n'

  if [ -n "$lignes" ]; then
    c="$c"'| | |\n|---|---|\n'"$lignes"'\n'
  fi
  c="$c"'<sub>Dates, temps passé, assignés et liens n'"'"'ont pas d'"'"'équivalent natif GitHub : ils vivent ici, sous la forme maison du chantier #335.</sub>'
  printf '%s' "$c"
}

# --- Préparation d'un ticket : tout ce qui ne parle à personne ------------------------------------
# Sépare STRICTEMENT la construction des corps JSON de leur envoi. Deux raisons, et la seconde est la
# vraie : `--payload` peut ainsi rendre EXACTEMENT ce qui partira, sans réseau ni écriture — donc la
# fidélité des octets se vérifie AVANT une action irréversible, sur le code qui sera joué, pas sur un
# double qui pourrait diverger de lui.
#
# Pose T_DIR (le répertoire de travail du ticket), les champs T_* et les fichiers :
#   $T_DIR/issue.json   corps de la création
#   $T_DIR/meta.json    corps du commentaire de métadonnées
T_DIR=""
preparer_ticket() {
  local iid="$1"
  T_DIR="$TRAVAIL/t"
  rm -rf "$T_DIR"; mkdir -p "$T_DIR" || return 1

  LC_ALL=C awk -v id="$iid" 'index($0, "{\"iid\":\"" id "\",") == 1 { print; exit }' "$JSONL" \
    | LC_ALL=C awk "$TICKET_AWK" > "$T_DIR/flux.tsv"
  if [ ! -s "$T_DIR/flux.tsv" ]; then
    echec "#$iid introuvable dans backlog.jsonl."; return 1
  fi

  local titre desc
  titre="$(LC_ALL=C awk -F '\t' '$1 == "T" { print $2; exit }' "$T_DIR/flux.tsv")"
  desc="$(LC_ALL=C awk -F '\t' '$1 == "D" { print $2; exit }' "$T_DIR/flux.tsv")"
  champ_meta() { LC_ALL=C awk -F '\t' -v c="$1" '$1 == "M" && $2 == c { print $3; exit }' "$T_DIR/flux.tsv"; }
  T_ETAT="$(champ_meta etat)"
  local auteur cree_le milestone debut echeance temps assignes lies url_gl
  auteur="$(champ_meta auteur)"; cree_le="$(champ_meta cree_le)"; url_gl="$(champ_meta url)"
  milestone="$(champ_meta milestone)"; debut="$(champ_meta debut)"; echeance="$(champ_meta echeance)"
  temps="$(champ_meta temps_s)"; assignes="$(champ_meta assignes)"; lies="$(champ_meta lies)"

  local labels_json="[]" l
  l="$(LC_ALL=C awk -F '\t' '$1 == "B" { printf "%s\"%s\"", (n++ ? "," : ""), $2 }' "$T_DIR/flux.tsv")"
  [ -n "$l" ] && labels_json="[$l]"
  local ms_json="" ms_num
  if [ -n "$milestone" ]; then
    ms_num="$(valeur_journal milestone "$milestone")"
    if [ -n "$ms_num" ]; then ms_json=',"milestone":'"$ms_num"
    elif [ "$mode" != "payload" ]; then
      note "#$iid : milestone « $milestone » sans numéro connu — ticket importé sans milestone."
    fi
  fi
  printf '{"title":"%s","body":"%s","labels":%s%s}' "$titre" "$desc" "$labels_json" "$ms_json" > "$T_DIR/issue.json"

  local corps tl
  corps="$(corps_meta "$iid" "$url_gl" "$auteur" "$cree_le" "$debut" "$echeance" "$temps" "$assignes" "$lies")"
  # Les relevés de temps, repliés : présents pour qui les cherche, absents de la lecture courante.
  tl="$(LC_ALL=C awk -F '\t' '$1 == "L" {
          printf "- %s · %s · `@%s`%s\\n", substr($3, 1, 10), ($2 >= 3600 ? sprintf("%d h %02d", $2/3600, ($2%3600)/60) : sprintf("%d min", $2/60)), $4, ($5 == "" ? "" : " — " $5) }' "$T_DIR/flux.tsv")"
  if [ -n "$tl" ]; then
    corps="$corps"'\n\n<details><summary>Relevés de temps</summary>\n\n'"$(printf '%s' "$tl" | tr -d '\n')"'\n</details>'
  fi
  printf '{"body":"%s"}' "$corps" > "$T_DIR/meta.json"
  return 0
}

# --- Un ticket, de bout en bout ------------------------------------------------------------------
# L'ordre à l'intérieur d'un ticket n'est pas indifférent : seule la CRÉATION consomme un numéro.
# Commentaires et fermeture viennent après, et chacun est journalisé séparément — une coupure entre
# deux étapes se reprend là où elle s'est produite, sans re-créer l'issue.
importer_ticket() {
  local iid="$1" numero
  local payload="$TRAVAIL/payload.json"
  preparer_ticket "$iid" || return 1
  local t="$T_DIR"

  # --- création
  if fait "issue" "$iid"; then
    numero="$(valeur_journal issue "$iid")"
  else
    local obtenu
    obtenu="$(creer_objet "$iid" "$t/issue.json")" || return $?
    numero="${obtenu%%$'\t'*}"
    journalise issue "$iid" "$numero" "${obtenu#*$'\t'}"
  fi

  # --- commentaire de métadonnées
  if ! fait "meta" "$iid"; then
    if gh_ecrire POST "repos/$DEPOT/issues/$numero/comments" "$t/meta.json" >/dev/null; then
      journalise meta "$iid" "$numero"
    else
      echec "#$iid : commentaire de métadonnées — $(printf '%s' "$GH_ERR" | head -1)"
      return 1
    fi
  fi

  # --- commentaires humains, dans l'ordre du fil
  #
  # Chaque commentaire est journalisé POUR LUI-MÊME (`note <iid>/<rang>`), et pas seulement le fil
  # une fois complet : une coupure au troisième commentaire d'un ticket qui en porte six ferait
  # sinon reposter les deux premiers à la reprise. Un doublon ne casse rien, mais il ne se répare
  # qu'à la main et il salit une donnée qu'on migre précisément pour la garder propre. Le compteur
  # de fin reste écrit — il ferme le ticket d'un seul test au lancement suivant.
  if ! fait "notes" "$iid"; then
    local n_notes=0 rang=0 nauteur ndate ncorps entete
    while IFS=$'\t' read -r _ nauteur ndate ncorps; do
      [ -n "$ncorps" ] || continue
      # Marqueur de champ vide (cf. TICKET_AWK) : le compte GitLab a été supprimé. On le dit
      # plutôt que d'écrire « @ » suivi de rien, qui se lirait comme une coquille.
      [ "$nauteur" = "-" ] && nauteur="compte supprimé"
      rang=$((rang + 1))
      if fait "note" "$iid/$rang"; then n_notes=$((n_notes + 1)); continue; fi
      # L'API attribue tout commentaire au porteur du jeton : l'auteur d'origine ne survit que si on
      # l'écrit. L'en-tête est donc une donnée, pas une décoration.
      #
      # Le nom est en CODE (`@nom`) et jamais en gras : « @nom » écrit nu est une MENTION GitHub, qui
      # notifie le compte homonyme s'il existe — et il en existe (Yvanrandria). 144 commentaires
      # repris, c'est 144 notifications à un inconnu, envoyées d'un coup et irrattrapables. Les
      # comptes GitLab ne sont pas des comptes GitHub : on les cite, on ne les interpelle pas.
      entete='<!-- maestro:note v1 auteur='"$nauteur"' date='"$ndate"' -->\n> **`@'"$nauteur"'`** · '"${ndate%%T*}"' · importé de GitLab\n\n'
      printf '{"body":"%s"}' "$entete$ncorps" > "$payload"
      if gh_ecrire POST "repos/$DEPOT/issues/$numero/comments" "$payload" >/dev/null; then
        journalise note "$iid/$rang" "$numero"
        n_notes=$((n_notes + 1))
      else
        echec "#$iid : commentaire de $nauteur — $(printf '%s' "$GH_ERR" | head -1)"
        return 1
      fi
    done < <(LC_ALL=C awk -F '\t' '$1 == "N"' "$t/flux.tsv")
    journalise notes "$iid" "$numero" "$n_notes"
  fi

  # --- état
  if ! fait "etat" "$iid"; then
    if [ "$T_ETAT" = "CLOSED" ]; then
      # « Abandonné » et « Doublon » ne sont pas des tickets réalisés : GitHub sait les distinguer
      # par state_reason, et c'est la seule nuance du cycle de vie qu'il porte nativement. Le reste
      # (« Terminé », « En revue »…) reste dans les labels workflow::, transposés tels quels.
      # Le test se fait en awk et NON en `grep -E`, où « \t » ne désigne pas une tabulation : les
      # expressions rationnelles étendues ne connaissent pas cet échappement, et `^B\tworkflow::…`
      # y matche le littéral « Btworkflow::… ». Le motif ne pouvait donc jamais tomber juste, et
      # tous les tickets fermés — abandonnés et doublons compris — partaient en « completed »,
      # c'est-à-dire en travail réalisé (#345). Défaut silencieux par construction : la fermeture
      # réussissait, seul son motif était faux. Même idiome que `fait` et `valeur_journal`, qui
      # comparent des colonnes plutôt que des motifs.
      local raison="completed"
      if LC_ALL=C awk -F '\t' '
           $1 == "B" && ($2 == "workflow::abandonne" || $2 == "workflow::doublon") { trouve = 1 }
           END { exit !trouve }' "$t/flux.tsv"; then
        raison="not_planned"
      fi
      printf '{"state":"closed","state_reason":"%s"}' "$raison" > "$payload"
      if gh_ecrire PATCH "repos/$DEPOT/issues/$numero" "$payload" >/dev/null; then
        journalise etat "$iid" "$numero" "closed"
      else
        echec "#$iid : fermeture — $(printf '%s' "$GH_ERR" | head -1)"
        return 1
      fi
    else
      journalise etat "$iid" "$numero" "open"
    fi
  fi

  rm -rf "$t"
  return 0
}

# --- Recette : « #n sur GitHub est-il bien #n de GitLab ? » ---------------------------------------
# Lecture seule des deux côtés. Elle ne rejoue pas le raisonnement de l'import — elle interroge le
# RÉSULTAT et le compare à la source, ce qui est la seule façon d'attraper un décalage : un import
# qui se vérifierait avec ses propres calculs confirmerait ses propres erreurs.
#
# L'attendu est lu dans `manifeste.tsv`, l'index déjà DÉCODÉ de l'export (#337), et le constaté est
# lu par `gh api --jq` (jq est fourni avec gh, aucune dépendance de plus). Les deux rendent du texte,
# la comparaison est directe.
#
# L'échantillon n'est pas aléatoire : les deux BORNES (un décalage global s'y voit), les TROUS (les
# seuls objets que l'import invente), les tickets CITÉS PAR DES COMMITS de l'historique — c'est pour
# eux que tout ce mécanisme existe — puis un ticket tous les ~25 pour couvrir la plage.
# desechappe <texte> -> le même texte, « & » rendu tel quel.
#
# GitLab échappe « & » en « & » jusque dans le manifeste ; GitHub le rend nu. Comparer les deux
# demande donc cette normalisation — et elle passe par `sed`, PAS par `${v//motif/remplacement}`, où
# DEUX pièges se cumulaient pour rendre l'opération SILENCIEUSEMENT NULLE (mesuré sur bash 5.2.37) :
#
#   1. le motif est un GLOB : « \u » y est un échappement qui vaut « u », donc « & » matchait
#      « u0026 » et laissait la barre oblique inverse en place ;
#   2. depuis bash 5.2, « & » dans le REMPLACEMENT désigne LE TEXTE MATCHÉ. Le motif était donc
#      remplacé par lui-même — zéro modification, zéro message.
#
# Le second annulait le premier, ce qui est le pire des deux mondes : la recette comparait un attendu
# encore échappé à un obtenu décodé et annonçait un écart sur les trois milestones qui contiennent
# « & ». Un verdict ROUGE sur un import CORRECT, prononcé par l'outil dont c'est le seul rôle — et
# une fois qu'on sait que « ces trois-là sortent toujours en écart », on ne lit plus le verdict.
desechappe() { printf '%s' "$1" | LC_ALL=C sed 's/\\u0026/\&/g'; }

recette() {
  section "Recette — #n sur GitHub = #n sur GitLab ?"
  local ok=0 ko=0 n att_titre att_etat att_ms att_lbl obt

  # Alignement d'ensemble d'abord : si le dernier numéro n'est pas le bon, le reste ne veut rien dire.
  local dernier total
  dernier="$(gh_dernier_numero)"
  kv "dernier numéro sur GitHub" "${dernier:-?}  (attendu $PLAN_MAX)"
  [ "$dernier" = "$PLAN_MAX" ] || { echec "la plage ne va pas jusqu'à #$PLAN_MAX."; ko=$((ko + 1)); }
  total="$(gh_lire "repos/$DEPOT/issues?state=all&per_page=1" | grep -c '"number"')"
  [ "$total" -gt 0 ] || { echec "aucune issue lisible sur $DEPOT."; return 1; }

  local cibles
  cibles="$(
    { printf '1\n%s\n' "$PLAN_MAX"
      LC_ALL=C cat "$TROUS"
      printf '48\n141\n165\n229\n331\n'
      LC_ALL=C awk -v max="$PLAN_MAX" 'BEGIN { for (i = 25; i < max; i += 25) print i }'
    } | sort -n | LC_ALL=C awk 'NF && !vu[$0]++'
  )"

  for n in $cibles; do
    obt="$(gh_lire "repos/$DEPOT/issues/$n")"
    # « gh api » écrit le CORPS de la réponse sur stdout même en 404 : une réponse non vide ne prouve
    # donc pas que l'objet existe. On exige la marque de l'objet demandé — sans quoi le message
    # d'erreur JSON serait comparé au titre attendu et rendu comme un écart de contenu, ce qui
    # raconte tout autre chose qu'un objet manquant.
    if [ -z "$obt" ] || ! printf '%s' "$obt" | grep -q "\"number\":$n,"; then
      echec "#$n absent de GitHub."; ko=$((ko + 1)); continue
    fi
    # Un trou : le seul objet que l'import fabrique. On vérifie qu'il est reconnaissable et fermé —
    # sans quoi il passerait pour un vrai ticket vide.
    if LC_ALL=C grep -qx "$n" "$TROUS"; then
      if printf '%s' "$obt" | grep -q "\"$LABEL_TROU\"" && printf '%s' "$obt" | grep -q '"state":"closed"'; then
        kv "  #$n" "bouche-trou, fermé et étiqueté ($C_G✓$C_0)"; ok=$((ok + 1))
      else
        echec "#$n : bouche-trou attendu, mais ni le label ni l'état ne le disent."; ko=$((ko + 1))
      fi
      continue
    fi

    att_titre="$(LC_ALL=C awk -F '\t' -v i="$n" 'NR > 1 && $1 == i { print $18; exit }' "$MANIFESTE")"
    att_etat="$(LC_ALL=C awk -F '\t' -v i="$n" 'NR > 1 && $1 == i { print tolower($2); exit }' "$MANIFESTE")"
    att_ms="$(LC_ALL=C awk -F '\t' -v i="$n" 'NR > 1 && $1 == i { print $8; exit }' "$MANIFESTE")"
    att_lbl="$(LC_ALL=C awk -F '\t' -v i="$n" 'NR > 1 && $1 == i {
                 s = ""
                 if ($4 != "-") s = s "workflow::" $4 "\n"
                 if ($5 != "-") s = s "type::" $5 "\n"
                 if ($6 != "-") s = s "agent::" $6 "\n"
                 if ($7 != "-") s = s "prio::" $7 "\n"
                 printf "%s", s; exit }' "$MANIFESTE" | sort | tr '\n' ',')"

    # UN seul appel pour les quatre champs : le même objet interrogé quatre fois pourrait, en
    # théorie, rendre quatre états différents — et en pratique c'est surtout quatre fois le quota.
    local vu g_titre g_etat g_ms g_lbl souci=""
    vu="$(gh api "repos/$DEPOT/issues/$n" </dev/null 2>/dev/null \
          --jq '[.title, .state, (.milestone.title // "-"), ([.labels[].name] | sort | join(","))] | @tsv')"
    IFS=$'\t' read -r g_titre g_etat g_ms g_lbl <<< "$vu"
    [ -n "$g_lbl" ] && g_lbl="$g_lbl,"

    # Le titre attendu vient du manifeste, déjà déséchappé sauf « & » que GitLab rend « & » et
    # GitHub non : la même normalisation que pour les milestones, et pour la même raison.
    att_titre="$(desechappe "$att_titre")"; att_ms="$(desechappe "$att_ms")"
    [ "$g_titre" = "$att_titre" ] || souci="$souci titre"
    [ "$g_etat" = "$att_etat" ] || souci="$souci état"
    [ "$g_ms" = "$att_ms" ] || souci="$souci milestone"
    [ "$g_lbl" = "$att_lbl" ] || souci="$souci labels"
    if [ -z "$souci" ]; then
      kv "  #$n" "titre, état, milestone, labels ($C_G✓$C_0)"; ok=$((ok + 1))
    else
      echec "#$n : écart sur$souci"
      [ "$g_titre" = "$att_titre" ] || note "    titre GitHub  : $g_titre"
      [ "$g_titre" = "$att_titre" ] || note "    titre attendu : $att_titre"
      [ "$g_ms" = "$att_ms" ] || note "    milestone : « $g_ms » vs « $att_ms »"
      [ "$g_lbl" = "$att_lbl" ] || note "    labels    : « $g_lbl » vs « $att_lbl »"
      ko=$((ko + 1))
    fi
  done

  kv "échantillon" "$ok conforme(s), $ko écart(s)"
  [ "$ko" -eq 0 ]
}

# --- Diagnostic : ce qui SERAIT envoyé ------------------------------------------------------------
# Rend les deux corps JSON d'un ticket, sans réseau ni écriture. C'est le seul point de contrôle
# possible avant une action irréversible : le corps se décode par un parseur JSON et se compare, par
# octets, à ce que GitLab rend — la seule vérification qui vaille (un terminal cp1252 réaffiche le
# mojibake de façon plausible, donc regarder l'écran revient à ne pas vérifier, cf. #337).
rendre_payload() {
  local iid="$1"
  case "$iid" in ''|*[!0-9]*) echo "--payload attend un iid" >&2; return 2 ;; esac
  mkdir -p "$TRAVAIL" || return 1
  # Un trou n'a pas de payload de ticket : le dire, plutôt que de rendre un fichier vide qu'on
  # prendrait pour une extraction ratée.
  if LC_ALL=C grep -qx "$iid" "$TROUS" 2>/dev/null; then
    echo "#$iid est un TROU (aucun ticket GitLab) : l'import y créera un bouche-trou fermé." >&2
    return 1
  fi
  preparer_ticket "$iid" || return 1
  cat "$T_DIR/issue.json"; printf '\n'
  cat "$T_DIR/meta.json";  printf '\n'
  rm -rf "$T_DIR"
  return 0
}

# --- Un bouche-trou ------------------------------------------------------------------------------
# Il n'a qu'un travail : consommer un numéro. Mais il sera lu un jour par quelqu'un qui tombe dessus
# depuis un « Refs #19 » — d'où un corps qui explique, un titre qui se reconnaît d'un coup d'œil, et
# un label dédié pour les filtrer tous en un clic.
importer_trou() {
  local n="$1" numero corps
  local payload="$TRAVAIL/payload.json"
  if fait "issue" "$n"; then
    numero="$(valeur_journal issue "$n")"
  else
    corps='**Numéro réservé par la migration GitLab → GitHub** (#340, chantier #335).\n\n'
    corps="$corps"'Aucun ticket ne portait l'"'"'iid `#'"$n"'` sur GitLab : il y a été supprimé. Cet objet **consomme le numéro** pour que la suite reste alignée.\n\n'
    corps="$corps"'Sans lui, tous les tickets suivants décaleraient d'"'"'un rang et les `Refs #<n>` / `Closes #<n>` des commits de l'"'"'historique pointeraient vers un ticket sans rapport — un lien *plausible et faux*, ce qui est pire qu'"'"'un lien mort et ne se répare pas.\n\n'
    corps="$corps"'<sub>Voir `docs/27-decision-gitlab-vers-github.md`.</sub>'
    printf '{"title":"[trou] #%s — numéro réservé par l'"'"'import (aucun ticket GitLab)","body":"%s","labels":["%s"]}' \
      "$n" "$corps" "$LABEL_TROU" > "$payload"
    local obtenu
    obtenu="$(creer_objet "$n" "$payload")" || return $?
    numero="${obtenu%%$'\t'*}"
    journalise issue "$n" "$numero" "${obtenu#*$'\t'}"
    journalise meta "$n" "$numero"
    journalise notes "$n" "$numero" 0
  fi
  if ! fait "etat" "$n"; then
    printf '{"state":"closed","state_reason":"not_planned"}' > "$payload"
    if gh_ecrire PATCH "repos/$DEPOT/issues/$numero" "$payload" >/dev/null; then
      journalise etat "$n" "$numero" "closed"
    else
      echec "#$n (bouche-trou) : fermeture — $(printf '%s' "$GH_ERR" | head -1)"
      return 1
    fi
  fi
  return 0
}

# --- La séquence ---------------------------------------------------------------------------------
importer_sequence() {
  section "5. Séquence — #1 → #$PLAN_MAX, dans l'ordre, jamais en parallèle"
  local numero nature iid faits=0 code
  while IFS=$'\t' read -r numero nature iid _; do
    case "$numero" in ''|\#*) continue ;; esac
    if [ "$MAX" -gt 0 ] && [ "$faits" -ge "$MAX" ]; then
      note "--max $MAX atteint — arrêt propre, relancer pour continuer."
      return 5
    fi
    if [ "$nature" = "bouche-trou" ]; then
      importer_trou "$numero"; code=$?
    else
      importer_ticket "$iid"; code=$?
    fi
    if [ "$code" -ne 0 ]; then
      [ "$code" -eq 4 ] && return 4
      return 5
    fi
    faits=$((faits + 1))
    if [ "$format" != "tsv" ] && [ $((numero % 10)) -eq 0 ]; then
      printf '  … #%s / %s\n' "$numero" "$PLAN_MAX"
    fi
  done < "$PLAN"
  kv "objets traités ce lancement" "$faits"
  return 0
}

# =================================================================================================
# Déroulé
# =================================================================================================
# --payload sort AVANT les prérequis : il ne touche ni au réseau ni à GitHub, et devoir un jeton
# valide pour relire ses propres octets n'aurait aucun sens.
if [ "$mode" = "payload" ]; then
  rendre_payload "$PAYLOAD_IID"; exit $?
fi

verifier_prerequis || { section "Verdict"; echec "prérequis non tenu — rien n'a été écrit."; exit 3; }
construire_plan || { section "Verdict"; echec "plan impossible à construire — rien n'a été écrit."; exit 3; }

if [ "$mode" = "recette" ]; then
  recette; verdict=$?
  section "Verdict"
  if [ "$verdict" -eq 0 ]; then
    printf '  %s✓%s la séquence est alignée : #n sur GitHub = #n de GitLab.\n' "$C_G" "$C_0"
    exit 0
  fi
  printf '  %s✗%s écart(s) constaté(s) — voir ci-dessus.\n' "$C_R" "$C_0"
  exit 1
fi

afficher_plan

if [ "$mode" = "check" ]; then
  section "Verdict"
  printf '  %s✓%s plan rendu, %saucune écriture%s côté GitHub — %s\n' "$C_G" "$C_0" "$C_B" "$C_0" "${PLAN#"$racine/"}"
  note "pour importer : bash scripts/migration/import-github.sh"
  note "l'import est À SENS UNIQUE : relire le plan avant de le lancer."
  exit 0
fi

mkdir -p "$TRAVAIL" || exit 1
touch "$JOURNAL" || exit 1

code=0
importer_labels || code=5
[ "$code" -eq 0 ] && { importer_milestones || code=5; }
[ "$code" -eq 0 ] && { importer_sequence; code=$?; }

section "Verdict"
case "$code" in
  0) printf '  %s✓%s import complet — %s objets, plage #1 → #%s\n' "$C_G" "$C_0" "$PLAN_MAX" "$PLAN_MAX"
     note "recette : bash scripts/migration/import-github.sh --check (doit annoncer 0 reste)" ;;
  4) printf '  %s✗%s SÉQUENCE ROMPUE — arrêt immédiat, ne PAS relancer à l'"'"'aveugle.\n' "$C_R" "$C_0"
     note "un objet n'a pas reçu son numéro : tout ce qui suivrait serait décalé."
     note "journal : ${JOURNAL#"$racine/"} — arbitrage humain requis." ;;
  *) printf '  %s⚠%s interrompu, mais REPRENABLE — relancer la même commande.\n' "$C_Y" "$C_0"
     note "journal : ${JOURNAL#"$racine/"} (le déjà-fait n'est jamais refait)" ;;
esac
exit "$code"
