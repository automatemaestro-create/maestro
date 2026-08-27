#!/usr/bin/env bash
# L'ordre de traitement des tickets pour la boucle d'orchestration autonome (#168, parent #167).
#
#   bash scripts/orchestrate/queue.sh              # le plan, en TSV sur stdout
#   bash scripts/orchestrate/queue.sh --check      # + le diagnostic (écartés, groupes) sur stderr
#   bash scripts/orchestrate/queue.sh --milestone "Phase 3 — …"
#
# Ce script est en LECTURE SEULE : il n'écrit rien dans GitLab, ne crée aucune branche et ne touche
# pas au dépôt. Sa seule sortie est le plan, que `run.sh` fige dans le `plan.tsv` d'un run — l'ordre
# est ainsi calculé UNE FOIS au début et ne bouge plus, même si le backlog évolue pendant le run.
#
# Format de sortie (TSV, en-tête préfixée « # » ignorable par les consommateurs machine) :
#     rang <TAB> iid <TAB> parent <TAB> prio <TAB> groupe <TAB> titre
# `parent` vaut « - » pour un ticket qui n'est pas un lot. Le rang est l'ordre de traitement.
# `groupe` est le GROUPE DE DÉPENDANCE (#288, règle 5 ci-dessous) — il vient AVANT `titre` parce que
# le titre est le champ absorbant d'un `read` : placé après lui, il s'y ferait avaler.
#
# --- Les règles d'ordonnancement, et pourquoi -----------------------------------------------------
#
# 1. Ne sont retenus que les tickets « À faire » et NON ASSIGNÉS du milestone courant. Un ticket
#    assigné est le travail de quelqu'un (anti-collision, docs/10 §5) ; un ticket d'un autre
#    milestone n'est pas la phase en cours.
#
#    « À faire » est le LIBELLÉ du cycle de vie, porté depuis #365 par le champ Status d'un projet
#    GitHub Projects v2 — après le champ natif de GitLab, puis six labels `workflow::*`. Ce fichier
#    n'a pas à le savoir : lib.sh rend toujours le libellé dans la colonne `statut` de ses TSV,
#    jamais un slug (« a-faire ») — contrat de surface documenté en tête de scripts/gitlab/lib.sh.
#    Les comparaisons ci-dessous n'ont bougé à aucun des trois changements de support.
#
# 2. Les PARENTS DE SUIVI sont écartés : ils ne portent ni branche ni code (docs/10 §5.1). À leur
#    place viennent leurs lots, DANS L'ORDRE DE LEUR CHECKLIST — c'est cet ordre qui encode les
#    dépendances entre lots, et lui seul (le marqueur « (parallèle) » dit que deux lots peuvent
#    être pris en même temps, pas qu'ils peuvent être pris à l'envers).
#
# 3. Les lots d'un même parent restent CONTIGUS, le parent héritant de la priorité maximale de ses
#    lots. S'intercaler entre le lot 3 et le lot 4 ferait partir le lot 4 d'un `origin/main` qui a
#    bougé pour rien — et allongerait d'autant la fenêtre pendant laquelle un parent est à moitié
#    livré.
#
# 4. Le reste est trié par `prio::` (haute > moyenne > basse) puis par iid croissant, pour que
#    l'ordre soit REPRODUCTIBLE : deux appels sur le même backlog rendent le même plan.
#
# 5. Le plan DIT, en plus, ce qui pourrait partir en même temps — colonne `groupe` (#288, parent
#    #287). Jusqu'ici le marqueur « (parallèle) » de la checklist servait à ordonner puis était jeté,
#    si bien que rien dans le plan figé ne portait l'indépendance ; la boucle du lot suivant n'aurait
#    eu qu'un ordre plat sur quoi décider, ou aurait dû recalculer la règle à chaud alors que le plan,
#    lui, ne bouge plus une fois écrit.
#
#    RÈGLE DE LECTURE — deux tickets peuvent être en vol en même temps si leurs `parent` DIFFÈRENT,
#    ou si leur `groupe` est IDENTIQUE. C'est la règle du parent (« parents différents, ou même
#    parent et tous deux marqués (parallèle) »), rendue transitive : le groupe est la VAGUE du lot
#    dans la chaîne de son parent — une suite maximale de lots consécutifs marqués « (parallèle) »
#    forme une vague, un lot non marqué forme la sienne et sert de barrière. Valeur : « <parent>.<n> »
#    pour un lot, « - » pour un ticket qui n'est pas un lot (tous mutuellement indépendants, comme ils
#    l'étaient déjà).
#
#    Pourquoi une vague, et non le marqueur recopié tel quel : la règle du parent, prise littéralement,
#    n'est PAS une relation d'équivalence — un lot marqué est indépendant d'un autre lot marqué, mais
#    dépendant d'un lot non marqué du même parent, si bien qu'aucune étiquette ne peut la porter (A∥ et
#    B∥ indépendants, C non marqué dépendant des deux : A et B devraient être à la fois dans le groupe
#    de C et hors du groupe l'un de l'autre). La vague tranche dans le sens SÛR, celui de docs/10 §5.1 :
#    « un lot non marqué reste barré par tout ce qui le précède ». Deux lots marqués séparés par un lot
#    non marqué tombent donc dans deux vagues — leur indépendance de principe était de toute façon sans
#    effet, la barrière qui les sépare les ordonnant déjà.
#
#    La vague se compte sur TOUTE la checklist du parent, lots déjà livrés compris : c'est une
#    propriété de la checklist, pas du plan, et deux plans successifs doivent la donner pareille.
#
# --- Coût en appels -------------------------------------------------------------------------------
# Deux lectures GraphQL (les tickets du milestone, les assignés du backlog ouvert) puis UNE lecture
# par candidat, mise en cache : la même sortie de `lib.sh issue-raw` sert à répondre aux deux
# questions « ce ticket est-il un lot ? » (marqueur « Sous-ticket de #N ») et « ce ticket est-il un
# parent ? » (section « ## Sous-tickets »). C'est l'approche de gl_start_brief — une lecture,
# plusieurs projections — plutôt qu'un appel de helper par question.

set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$RACINE/scripts/gitlab/lib.sh"

CHECK=0
MILESTONE=""
LISTE_MILESTONES=0
LISTE_ORPHELINS=0
LISTE_NON_ARBITRES=0
LISTE_TOUCHE_CLAUDE=0

usage() {
  cat <<'USAGE'
L'ordre de traitement des tickets pour la boucle d'orchestration autonome.

  bash scripts/orchestrate/queue.sh [options]

Options :
  --check              Affiche aussi, sur stderr, le diagnostic : milestone retenu, tickets
                       écartés avec leur raison, les blocs de lots gardés contigus, et les
                       groupes de dépendance obtenus (ce qui pourrait partir en même temps).
  --milestone <titre>  Milestone à traiter (titre exact). Par défaut : la phase courante
                       (lib.sh current-milestone).
  --milestones         N'imprime pas de plan : liste les milestones ACTIFS sur lesquels un run
                       peut porter, avec ce qu'ils ont de traitable — titre, courant (0/1),
                       « À faire » et libres, ouverts, échéance, rail. C'est ce que /orchestrate lit
                       pour proposer le choix du milestone avant un run neuf. `courant` vaut 1 pour
                       AU PLUS un milestone par rail, et pour aucun si celui du rail n'a rien à
                       prendre (#619) : un défaut sur lequel un run planifierait zéro ticket n'est
                       pas un défaut.
  --orphelins          N'imprime pas de plan : liste les tickets « En cours » du milestone dont
                       plus personne ne s'occupe — ce que le plan N'INCLUT PAS et qu'un geste
                       explicite peut rendre prenable (`lib.sh reprendre-en-cours <iid>`). TSV :
                       iid, reprises, plafond, run d'origine, verdict, détail, titre. C'est ce que
                       /orchestrate lit pour PROPOSER la reprise avant un run neuf.
  --non-arbitres       N'imprime pas de plan : liste les PARENTS du plan dont les lots n'ont jamais
                       été arbitrés — personne n'a tranché lesquels sont parallélisables, si bien
                       que leur séquentiel n'a pas été décidé, il a été subi. TSV : parent, lots
                       au plan, lots marqués, lots au total, titre. C'est ce que /orchestrate lit
                       pour PROPOSER l'arbitrage avant un run neuf. Aucune lecture de forge de
                       plus : le plan a déjà lu chacun de ces parents.
  --touche-claude      N'imprime pas de plan : liste les tickets DU PLAN qui nomment « .claude/ »,
                       où une session autonome ne peut pas écrire (blocage dur du CLI, en amont de
                       l'allowlist — #229/#238). Leur correctif partira dans la description de la
                       PR au lieu d'être appliqué, et depuis #418/#419 cette PR est mergée sans que
                       personne ne l'ouvre. TSV : iid, parent, titre. Ils RESTENT au plan — écarter
                       est une décision, et le geste existe déjà : les assigner. Aucune lecture de
                       forge de plus : le plan a déjà lu chacun de ces tickets.
  -h, --help           Cette aide.

Sortie (stdout, TSV) : rang, iid, parent, prio, groupe, titre. Lecture seule — n'écrit rien.
`groupe` : deux tickets peuvent partir en même temps si leurs parents diffèrent, ou si leur
groupe est identique.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --milestone) MILESTONE="${2:-}"; shift ;;
    --milestones | --jalons) LISTE_MILESTONES=1 ;;
    --orphelins) LISTE_ORPHELINS=1 ;;
    --non-arbitres) LISTE_NON_ARBITRES=1 ;;
    --touche-claude) LISTE_TOUCHE_CLAUDE=1 ;;
    -h | --help) usage; exit 0 ;;
    *) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

diag() { [ "$CHECK" = 1 ] && printf '%s\n' "$*" >&2; return 0; }

gl_require || exit 1

# Brouillon de calcul, hors du dépôt et effacé au trap : son chemin n'est jamais imprimé, rien n'y
# renvoie personne, et le plan — la seule sortie qui compte — part sur stdout puis dans le journal
# du run. Il n'oriente donc aucune session vers un chemin hors du worktree (#234).
TMP="$(mktemp -d "${TMPDIR:-/tmp}/maestro-queue.XXXXXX")" || {
  echo "queue.sh : impossible de créer un dossier temporaire" >&2; exit 1
}
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/vue" "$TMP/chaine"

# --- 0. Les milestones sur lesquels un run peut porter (#204) --------------------------------------
# `--milestones` répond à « quel milestone traiter ? », la question que /orchestrate pose avant de
# lancer un run NEUF. Elle était tranchée en silence par la phase courante — le bon défaut, mais pas
# toujours le bon choix : plusieurs milestones actifs peuvent porter du travail en même temps.
#
# Sortie TSV, du plus ancien au plus récent (l'ordre de gl_milestones), en-tête « # » ignorable :
#     titre <TAB> courant <TAB> a_faire <TAB> ouverts <TAB> echeance <TAB> rail
#
# `rail` (#617) vaut « produit » ou « outillage » et sépare les milestones de PRODUIT de ceux de
# l'OUTILLAGE de la forge. Il change la lecture de `courant`, qui vaut désormais 1 pour le milestone
# courant **de son rail** : il y en a donc AU PLUS DEUX à 1, un par rail, et non plus un seul. C'est
# voulu — un run porte sur un rail, et proposer « le » courant sans dire lequel est ce qui a laissé
# un run « produit » traiter de l'outillage.
#
# « AU PLUS » et non « exactement » depuis #619 : `courant` est retiré d'un milestone à
# `a_faire = 0`, où il désignerait un défaut sur lequel un run planifierait zéro ticket. Un rail
# peut donc n'avoir aucune ligne à 1 — ce n'est pas une donnée manquante, c'est le verdict « rien à
# prendre sur ce rail », et /orchestrate le dit.
#
# `a_faire` compte ce que la boucle POURRAIT prendre — « À faire » ET libre, exactement le filtre du
# §3 ci-dessous — et non les tickets ouverts : un milestone dont les « À faire » sont tous assignés
# rendrait un plan vide, et le proposer serait un piège. Le compte reste **indicatif** sur un point,
# dit ici plutôt que découvert plus tard : il ne défait pas les parents de suivi en leurs lots (ça
# coûterait une lecture par ticket), donc un parent y compte pour un.
#
# Seuls les milestones ACTIFS sont listés : un milestone fermé est une phase soldée, on n'y lance pas
# un run. Coût : deux lectures fixes (milestones, backlog ouvert) plus une par milestone actif.
milestones_traitables() {
  local courant_produit courant_outillage titre echeance ouverts rail a_faire courant
  # Deux lectures et non une : le courant d'un rail ne se déduit pas de celui de l'autre. Elles ne
  # coûtent rien de plus en allers de forge — gl_current_milestone lit la même page de jalons.
  courant_produit="$(gl_current_milestone produit 2>/dev/null)" || courant_produit=""
  courant_outillage="$(gl_current_milestone outillage 2>/dev/null)" || courant_outillage=""
  gl_backlog_table opened >"$TMP/backlog.tsv" || return 1
  printf '# titre\tcourant\ta_faire\touverts\techeance\trail\n'
  gl_milestones | awk -F'\t' -v OFS='\t' '$1 !~ /^#/ && $2 == "active" { print $1, $4, $6 - $5, $7 }' |
    while IFS=$'\t' read -r titre echeance ouverts rail; do
      [ -n "$titre" ] || continue
      gl_milestone_issues "$titre" >"$TMP/milestone-liste.tsv" 2>/dev/null ||
        : >"$TMP/milestone-liste.tsv"
      a_faire="$(awk -F'\t' '
        FNR == NR { if ($1 !~ /^#/) assigne[$1] = $5; next }
        /^#/ { next }
        $2 == "À faire" && (!($1 in assigne) || assigne[$1] == "-" || assigne[$1] == "") { n++ }
        END { print n + 0 }' "$TMP/backlog.tsv" "$TMP/milestone-liste.tsv")"
      courant=0
      [ "$rail" = outillage ] && [ "$titre" = "$courant_outillage" ] && courant=1
      [ "$rail" = produit ] && [ "$titre" = "$courant_produit" ] && courant=1
      # #619 : `courant` désigne CE QU'UN RUN PRENDRAIT SANS CONSIGNE, et un milestone dont la
      # boucle ne peut rien tirer n'est pas un défaut, c'est un piège — le run partirait sur un
      # plan vide. Le critère est celui de `a_faire` (« À faire » ET libre, le filtre du §3) et non
      # celui de `current-milestone` (au moins un ticket ouvert) : il est STRICTEMENT plus étroit,
      # et c'est voulu — un milestone dont tout est assigné est ouvert pour la forge et vide pour
      # la boucle. Ce n'est donc pas la règle du §3.4 recopiée ici, c'est la question d'à côté.
      # Conséquence assumée : un rail peut n'avoir AUCUNE ligne à `courant = 1` — /orchestrate le
      # dit alors au lieu de proposer un défaut qui ne planifierait rien.
      [ "$a_faire" = 0 ] && courant=0
      printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$titre" "$courant" "$a_faire" "$ouverts" "$echeance" "$rail"
    done
}

if [ "$LISTE_MILESTONES" = 1 ]; then
  milestones_traitables || exit 1
  exit 0
fi

# --- 1. Le milestone ------------------------------------------------------------------------------
if [ -z "$MILESTONE" ]; then
  MILESTONE="$(gl_current_milestone)" || {
    # Le helper vient de nommer sur stderr CE QU'IL A SAUTÉ et pourquoi (soldé / vide, #619) : on
    # ne le paraphrase pas, on dit seulement la conséquence ici.
    echo "queue.sh : aucun milestone utilisable sur le rail produit — rien à planifier." >&2; exit 1
  }
fi
diag "milestone : $MILESTONE — seuls ses tickets sont lus ; un ticket d'un autre milestone n'est pas candidat."

# --- 1 bis. Les orphelins : ce que le plan n'inclut pas, et qu'on pourrait reprendre (#329) --------
# La règle 1 écarte les tickets « En cours » et assignés, et c'est ce qui protège le travail des
# autres : ce filtre ne bouge pas. Mais il écarte aussi, du même geste, les tickets qu'une session
# MORTE a laissés dans cet état — invisibles pour toujours, alors que leur worktree porte parfois des
# milliers de lignes (#316). D'où cette sortie SÉPARÉE : le plan reste ce qu'il était, et ce qui
# pourrait le rejoindre se LIT à côté, sans jamais s'y glisser tout seul.
#
# Le renversement de #327 tenu jusqu'au bout : on ne DÉCIDE pas de reprendre, on le PROPOSE. C'est
# exactement la forme de la reprise d'un run inachevé (#204) — le plan liste, /orchestrate demande,
# et le choix remplace le feu vert. Rien ici n'écrit quoi que ce soit.
#
# Le verdict (« quelqu'un s'en occupe-t-il encore ? ») n'est PAS recalculé ici : il est demandé au
# verbe du lot 1, seul à savoir départager un vivant d'un orphelin (carte du pilote, fraîcheur du
# worktree, seuil généreux). MAESTRO_ORPHELINS_SOURCE remplace l'appel — couture des tests, comme
# MAESTRO_EN_COURS_SIGNAL côté worktree.sh : elle permet d'éprouver la COMPOSITION (filtre du
# milestone, comptage des reprises, plafond) sans monter de worktree ni de carte de pilote.
#
# Le filtre du MILESTONE est la seule chose que ce fichier ajoute au verdict, et il n'est pas
# cosmétique : un run porte sur un milestone, donc un orphelin d'ailleurs ne rejoindrait pas ce
# plan-ci même repris. Le signalement global, lui, existe déjà (`reconcile-en-cours`, `doctor.sh`).
if [ "$LISTE_ORPHELINS" = 1 ]; then
  gl_milestone_issues "$MILESTONE" >"$TMP/milestone.tsv" || exit 1
  if [ -n "${MAESTRO_ORPHELINS_SOURCE:-}" ]; then
    # shellcheck disable=SC2086  # la couture EST une ligne de commande : son découpage est voulu
    $MAESTRO_ORPHELINS_SOURCE >"$TMP/en-cours.tsv" 2>/dev/null
  else
    gl_reconcile_en_cours --tsv >"$TMP/en-cours.tsv" 2>/dev/null
  fi
  printf '# iid\treprises\tplafond\trun\tverdict\tdetail\ttitre\n'
  # `read` sur cinq champs : le TSV du lot 1 est « iid, verdict, source, détail, titre ». La `source`
  # n'est pas reprise — elle dit d'où vient le VERDICT (carte ou déduction), question déjà tranchée
  # ici, puisque seuls les orphelins passent et qu'un orphelin est toujours une déduction.
  while IFS=$'\t' read -r iid verdict _ detail titre; do
    case "$iid" in ''|'#'*|*[!0-9]*) continue ;; esac
    [ "$verdict" = "orphelin" ] || continue
    awk -F '\t' -v iid="$iid" '$1 == iid { trouve = 1 } END { exit !trouve }' "$TMP/milestone.tsv" || continue

    deja="$(gl_reprises_de "$iid")" && lisible=1 || lisible=0
    if [ "$lisible" = 0 ]; then plafond="?"
    elif [ "$deja" -ge "$GL_REPRISES_MAX" ]; then plafond="atteint"
    else plafond="-"; fi

    origine="$(bash "$RACINE/scripts/orchestrate/journal.sh" origine "$iid" 2>/dev/null)"
    run="$(printf '%s' "$origine" | cut -f1)"
    verdict_run="$(printf '%s' "$origine" | cut -f2)"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$iid" "$deja" "$plafond" "${run:--}" "${verdict_run:--}" "$detail" "$titre"
  done <"$TMP/en-cours.tsv"
  exit 0
fi

# --- 2. Les tickets du milestone, et qui les a pris -----------------------------------------------
gl_milestone_issues "$MILESTONE" >"$TMP/milestone.tsv" || exit 1
gl_backlog_table opened >"$TMP/backlog.tsv" || exit 1

# Les deux lectures plafonnent à `first: 100` côté GraphQL (lib.sh). Une troncature silencieuse
# serait pire qu'une erreur : un ticket assigné dont la ligne manque passerait pour libre, et la
# boucle le prendrait à quelqu'un. On alerte donc TOUJOURS, pas seulement en --check.
alerte_troncature() { # <fichier> <quoi>
  local nb
  nb="$(grep -cv '^#' "$1")"
  [ "$nb" -ge 100 ] || return 0
  printf 'queue.sh : ⚠ %s — %s lignes, soit le plafond de l'\''API. Le plan peut être incomplet et un ticket pris passer pour libre.\n' \
    "$2" "$nb" >&2
}
alerte_troncature "$TMP/milestone.tsv" "tickets du milestone"
alerte_troncature "$TMP/backlog.tsv" "backlog ouvert (assignés)"

# --- 3. Les candidats : « À faire » et libres -----------------------------------------------------
# Les écartés partent dans un fichier à part plutôt qu'à la poubelle : `--check` doit pouvoir dire
# POURQUOI un ticket n'est pas dans le plan — sans quoi une absence est indistinguable d'un bug.
awk -F '\t' -v OFS='\t' -v ecartes="$TMP/ecartes.tsv" '
  FNR == NR { if ($1 !~ /^#/) assigne[$1] = $5; next }
  /^#/ { next }
  {
    iid = $1; statut = $2; prio = $5; titre = $6
    if (statut != "À faire") { print iid, "cycle de vie « " statut " »", titre > ecartes; next }
    a = (iid in assigne) ? assigne[iid] : "-"
    if (a != "-" && a != "") { print iid, "assigné à " a, titre > ecartes; next }
    print iid, prio, titre
  }
' "$TMP/backlog.tsv" "$TMP/milestone.tsv" >"$TMP/candidats.tsv"
touch "$TMP/ecartes.tsv"

if [ ! -s "$TMP/candidats.tsv" ]; then
  diag "aucun ticket « À faire » et libre dans ce milestone."
  printf '# rang\tiid\tparent\tprio\tgroupe\ttitre\n'
  exit 0
fi

# --- 4. Une lecture par ticket, deux projections --------------------------------------------------
# vue <iid> -> chemin d'un fichier contenant LA VUE TEXTE CANONIQUE du ticket, mise en cache. Le
# cache est ce qui rend gratuite la relecture d'un parent déjà lu comme candidat.
#
# `gl_issue_raw` et non un appel direct au CLI de la forge (#341) : c'est l'une des trois primitives
# forge (en-tête de lib.sh), et son format est le MÊME des deux côtés — d'où les deux projections
# ci-dessous, inchangées. L'appel direct, lui, aurait interrogé la mauvaise forge à la bascule, et
# le plan d'un run se serait construit sur les descriptions du mauvais dépôt : les marqueurs
# « Sous-ticket de #N » et « ## Sous-tickets » auraient été lus ailleurs que là où le run travaille.
vue() {
  # Deux instructions, et non `local iid="$1" f="…$iid…"` : `local` est une commande, dont bash
  # développe TOUS les mots avant d'exécuter la moindre affectation — `$iid` y vaudrait donc encore
  # celui de la portée appelante, et le cache rendrait la vue d'un autre ticket.
  local iid="$1"
  local f="$TMP/vue/$iid.txt"
  if [ ! -f "$f" ]; then
    gl_issue_raw "$iid" >"$f" 2>/dev/null || { rm -f "$f"; return 1; }
  fi
  printf '%s\n' "$f"
}

# parent_de <iid> -> iid du parent si le ticket est un lot, rien sinon (même marqueur que
# gl_parent_of : « Sous-ticket de #<iid> » en tête de description).
parent_de() {
  local f
  f="$(vue "$1")" || return 1
  grep -o 'Sous-ticket de #[0-9]\+' "$f" | head -1 | grep -o '[0-9]\+$'
}

# est_parent <iid> -> 0 si le ticket porte une section « ## Sous-tickets » non vide. Réutilise
# gl_subticket_rows (lib.sh) pour que le parsing de la checklist n'existe qu'à un seul endroit.
est_parent() {
  local f
  f="$(vue "$1")" || return 1
  [ -n "$(gl_subticket_rows <"$f")" ]
}

# --- 5. Blocs contigus : un par parent, un par ticket isolé ----------------------------------------
# Un « bloc » est l'unité que le tri déplace : soit les lots d'un même parent (qui doivent rester
# contigus et ordonnés), soit un ticket seul. On note pour chaque candidat son bloc et son rang
# DANS le bloc ; le tri final se fait sur (priorité du bloc, iid minimal du bloc, rang).
#
# À ne pas confondre avec le GROUPE DE DÉPENDANCE de la règle 5, calculé juste après : le bloc dit ce
# qui reste CÔTE À CÔTE dans le plan, le groupe ce qui pourrait partir EN MÊME TEMPS. Deux questions
# voisines, deux colonnes — d'où deux mots, le second seul étant publié.
: >"$TMP/membres.tsv"   # bloc <TAB> rang-dans-bloc <TAB> iid <TAB> parent <TAB> prio <TAB> groupe <TAB> titre

rang_prio() { # <prio> -> clé de tri numérique (haute d'abord)
  case "$1" in
    haute) printf '1' ;;
    moyenne) printf '2' ;;
    basse) printf '3' ;;
    *) printf '4' ;;
  esac
}

# chaine_du_parent <parent> -> chemin d'un fichier « iid <TAB> rang <TAB> vague », une ligne par lot
# de la checklist DANS SON ORDRE, mis en cache. Deux réponses en une lecture : la position du lot
# (l'ordre, règle 2) et sa vague de dépendance (règle 5).
#
# La vague s'incrémente à chaque lot, SAUF quand le lot et son prédécesseur immédiat portent tous
# deux le marqueur — c'est ce qui agrège une suite de lots « (parallèle) » en une seule vague et fait
# de tout lot non marqué une barrière. Le marqueur est lu dans la colonne que gl_subticket_rows
# extrait déjà (« ∥ ») : le parsing de la checklist n'existe qu'à un seul endroit, lib.sh.
chaine_du_parent() {
  local parent="$1"
  local f="$TMP/chaine/$parent.tsv"
  if [ ! -f "$f" ]; then
    local v
    v="$(vue "$parent")" || return 1
    gl_subticket_rows <"$v" | awk -F '\t' -v OFS='\t' '
      { par = ($3 == "∥")
        if (!(par && precedent)) vague++
        precedent = par
        print $1, NR, vague }
    ' >"$f"
  fi
  printf '%s\n' "$f"
}

while IFS=$'\t' read -r iid prio titre; do
  [ -n "$iid" ] || continue
  if est_parent "$iid"; then
    printf '%s\t%s\t%s\n' "$iid" "parent de suivi (ne porte ni branche ni code)" "$titre" >>"$TMP/ecartes.tsv"
    continue
  fi
  parent="$(parent_de "$iid")"
  rang=""; vague=""
  if [ -n "$parent" ]; then
    chaine="$(chaine_du_parent "$parent")" &&
      IFS=$'\t' read -r rang vague < <(awk -F '\t' -v OFS='\t' -v cible="$iid" \
        '$1 == cible { print $2, $3; exit }' "$chaine")
  fi
  # Un lot absent de la checklist de son parent est traité comme isolé plutôt que placé au hasard :
  # mieux vaut un ordre visiblement plat qu'un ordre faux qui a l'air juste. Son groupe suit — un
  # lot dont on ne sait pas dire la place ne peut pas non plus se voir attribuer une vague.
  if [ -n "$parent" ] && [ -z "$rang" ]; then
    diag "  ⚠ #$iid se déclare lot de #$parent mais n'est pas dans sa checklist — traité isolément"
    parent=""
  fi
  if [ -n "$parent" ]; then
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$parent" "$rang" "$iid" "$parent" "$prio" "$parent.$vague" "$titre" >>"$TMP/membres.tsv"
  else
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$iid" 1 "$iid" "-" "$prio" "-" "$titre" >>"$TMP/membres.tsv"
  fi
done <"$TMP/candidats.tsv"

# --- 5 bis. L'arbitrage : ce que le plan ne peut pas savoir tout seul (#562, docs/10 §5.1) --------
# La colonne `groupe` ne dit que ce que la checklist du parent DÉCLARE. Le marqueur « (parallèle) »
# étant facultatif (#160), un parent sans aucun marqueur rend une chaîne de vagues à un lot chacune —
# c'est-à-dire un plan parfaitement séquentiel, indiscernable d'un séquentiel VOULU. Mesure du
# 2026-08-26 : 16 parents sur 42 n'ont jamais reçu un seul marqueur.
#
# Ce bloc ne CORRIGE rien et n'infère rien : il nomme les parents sur lesquels la question n'a pas
# été posée. Marquer d'office enverrait un lot partir d'une base incomplète, et le sens sûr est le
# séquentiel — c'est la règle de docs/10 §5.1, et la raison pour laquelle l'arbitrage reste un
# jugement que /orchestrate PROPOSE au feu vert, exactement comme la reprise des orphelins (#327).
#
# LA RÈGLE N'EST PAS RECOPIÉE ICI : le verdict est celui de `gl_arbitrage_de`, rejoué sur la vue que
# `chaine_du_parent` a déjà mise en cache. Zéro lecture de forge en plus, et une seule définition de
# « arbitré » — ce qui compte quand #389 fera passer le marqueur au label `lot::parallele`.
: >"$TMP/non-arbitres.tsv"
cut -f4 "$TMP/membres.tsv" | sort -u | while read -r parent; do
  [ -n "$parent" ] && [ "$parent" != "-" ] || continue
  vue_parent="$(vue "$parent")" || continue
  verdict="$(gl_arbitrage_de <"$vue_parent")"
  [ "$(printf '%s' "$verdict" | cut -f1)" = "jamais" ] || continue
  au_plan="$(awk -F '\t' -v p="$parent" '$4 == p { n++ } END { print n + 0 }' "$TMP/membres.tsv")"
  titre_parent="$(sed -n 's/^title:[[:space:]]*//p' "$vue_parent" | head -1)"
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "$parent" "$au_plan" "$(printf '%s' "$verdict" | cut -f2)" \
    "$(printf '%s' "$verdict" | cut -f3)" "$titre_parent" >>"$TMP/non-arbitres.tsv"
done

if [ "$LISTE_NON_ARBITRES" = 1 ]; then
  printf '# parent\tau-plan\tmarques\tlots\ttitre\n'
  [ -s "$TMP/non-arbitres.tsv" ] && sort -t$'\t' -k1,1n "$TMP/non-arbitres.tsv"
  exit 0
fi

# --- 5 ter. Ce qui touche « .claude/ » : la moitié AMONT du résidu (#612, docs/10 §11.7) ---------
# Une session autonome ne peut pas écrire sous `.claude/` — blocage dur du CLI, en amont de
# l'allowlist (#229/#238) —, donc elle rend son correctif dans la description de sa PR (#188), que
# depuis #418/#419 le pilote merge sans que personne ne l'ouvre. §11.7 le dit depuis #238 sans
# l'avoir jamais outillé : « Mieux vaut ne pas l'y envoyer. […] **`queue.sh` ne le détecte pas** —
# c'est au rédacteur du ticket de le dire. » Il le détecte désormais.
#
# CE BLOC N'ÉCARTE RIEN, exactement comme le 5 bis ne marque rien : le ticket reste au plan et le
# signalement le DIT. Écarter est une décision, et le geste existe déjà — l'assigner, que le filtre
# « À faire ET libre » du §3 suffit à tenir dehors. Décider ici reviendrait à retirer d'un run,
# sans que personne l'ait voulu, un ticket dont une part est peut-être parfaitement traitable.
#
# LA RÈGLE N'EST PAS RECOPIÉE ICI : le verdict est celui de `gl_touche_claude_de`, rejoué sur la vue
# que `vue` a déjà mise en cache pour chaque candidat (§4). Zéro lecture de forge en plus, et une
# seule définition de « touche `.claude/` » — même raison que `gl_arbitrage_de` (#562).
: >"$TMP/touche-claude.tsv"
# shellcheck disable=SC2034  # les champs non lus sont nommés : c'est la disposition de membres.tsv
while IFS=$'\t' read -r bloc rang iid parent prio groupe titre; do
  [ -n "$iid" ] || continue
  vue_iid="$(vue "$iid")" || continue
  gl_touche_claude_de <"$vue_iid" >/dev/null || continue
  printf '%s\t%s\t%s\n' "$iid" "$parent" "$titre" >>"$TMP/touche-claude.tsv"
done <"$TMP/membres.tsv"

if [ "$LISTE_TOUCHE_CLAUDE" = 1 ]; then
  printf '# iid\tparent\ttitre\n'
  [ -s "$TMP/touche-claude.tsv" ] && sort -t$'\t' -k1,1n "$TMP/touche-claude.tsv"
  exit 0
fi

if [ ! -s "$TMP/membres.tsv" ]; then
  diag "tous les candidats ont été écartés."
  printf '# rang\tiid\tparent\tprio\tgroupe\ttitre\n'
  [ "$CHECK" = 1 ] && sort -t$'\t' -k1,1n "$TMP/ecartes.tsv" |
    while IFS=$'\t' read -r i r t; do printf '  écarté #%s — %s (%s)\n' "$i" "$r" "$t" >&2; done
  exit 0
fi

# --- 6. Priorité et iid minimal de chaque bloc -----------------------------------------------------
# La priorité d'un bloc est la MEILLEURE de ses membres : un parent dont un seul lot est
# « prio::haute » passe devant, sans quoi ce lot serait retenu par ses voisins moins prioritaires.
# shellcheck disable=SC2034  # les champs non lus sont nommés : c'est la disposition de membres.tsv
while IFS=$'\t' read -r bloc rang iid parent prio groupe titre; do
  printf '%s\t%s\t%s\n' "$bloc" "$(rang_prio "$prio")" "$iid"
done <"$TMP/membres.tsv" | awk -F '\t' -v OFS='\t' '
  { if (!($1 in p) || $2 < p[$1]) p[$1] = $2
    if (!($1 in m) || $3 + 0 < m[$1]) m[$1] = $3 + 0 }
  END { for (g in p) print g, p[g], m[g] }
' >"$TMP/blocs.tsv"

# --- 7. Le plan ------------------------------------------------------------------------------------
printf '# rang\tiid\tparent\tprio\tgroupe\ttitre\n'
awk -F '\t' -v OFS='\t' '
  FNR == NR { pr[$1] = $2; mi[$1] = $3; next }
  { print pr[$1], mi[$1], $2, $3, $4, $5, $6, $7 }
' "$TMP/blocs.tsv" "$TMP/membres.tsv" |
  sort -t$'\t' -k1,1n -k2,2n -k3,3n |
  awk -F '\t' -v OFS='\t' '{ print NR, $4, $5, $6, $7, $8 }'

# Le plan dit SUR QUOI il porte (#617), par la même mécanique de commentaire que la réserve
# ci-dessous : le milestone et son rail. Sans cette ligne, la seule façon pour le pilote d'annoncer
# le rail serait de redemander le milestone courant à la forge — donc de reposer, après coup, une
# question déjà tranchée ici, avec le risque de rendre une AUTRE réponse si un milestone s'est soldé
# entre-temps. Le plan est la source : c'est lui qui est rejoué à l'identique par `--resume` (#204).
printf '# milestone\t%s\t%s\n' "$MILESTONE" "$(gl_milestone_rail "$MILESTONE" 2>/dev/null || printf 'produit')"

# Le plan porte sa propre réserve (#562). Ces lignes sont des COMMENTAIRES : les deux lectures du
# plan par run.sh les écartent déjà (`grep -v '^#'`, puis `case "$rang" in '#'*`), et un plan
# ANTÉRIEUR à ce lot n'en porte simplement aucune — même dégradation douce que la colonne `groupe`
# sur un plan d'avant #288. C'est ce qui permet à la ligne `plan :` d'un run de signaler l'arbitrage
# manquant SANS repayer une planification : le fichier qu'il vient de recevoir le lui dit.
if [ -s "$TMP/non-arbitres.tsv" ]; then
  sort -t$'\t' -k1,1n "$TMP/non-arbitres.tsv" |
    while IFS=$'\t' read -r p au_plan marques lots t; do
      printf '# non-arbitre\t%s\t%s\t%s\t%s\t%s\n' "$p" "$au_plan" "$marques" "$lots" "$t"
    done
fi

# Et ce que le plan sait déjà de ce qu'une session ne pourra pas écrire (#612). Même mécanique de
# commentaire, même dégradation douce — un plan ANTÉRIEUR à ce lot n'en porte aucune —, et surtout
# la même règle 4 : ces lignes sont calculées sur la vue des mêmes tickets, donc deux appels sur le
# même backlog rendent toujours le même plan, à l'octet près.
if [ -s "$TMP/touche-claude.tsv" ]; then
  sort -t$'\t' -k1,1n "$TMP/touche-claude.tsv" |
    while IFS=$'\t' read -r i p t; do
      printf '# touche-claude\t%s\t%s\t%s\n' "$i" "$p" "$t"
    done
fi

# --- 8. Diagnostic ---------------------------------------------------------------------------------
if [ "$CHECK" = 1 ]; then
  nb_ecartes="$(wc -l <"$TMP/ecartes.tsv" | tr -d ' ')"
  nb_retenus="$(wc -l <"$TMP/membres.tsv" | tr -d ' ')"
  printf '\n%s ticket(s) retenu(s), %s écarté(s)\n' "$nb_retenus" "$nb_ecartes" >&2
  if [ "$nb_ecartes" -gt 0 ]; then
    sort -t$'\t' -k1,1n "$TMP/ecartes.tsv" |
      while IFS=$'\t' read -r i r t; do printf '  écarté #%-4s %s — %s\n' "$i" "$r" "$t" >&2; done
    # Un écarté « En cours » est peut-être un ORPHELIN (#329) : une session morte y laisse son
    # ticket, que ce filtre écarte alors sans distinguer le travail vivant du travail mort. On ne
    # tranche pas ici — ce serait une lecture de plus sur un diagnostic — on renvoie au verbe qui
    # sait, et qui est en lecture seule lui aussi.
    if grep -q 'cycle de vie « En cours »' "$TMP/ecartes.tsv"; then
      printf '  → un « En cours » écarté peut être un orphelin (session morte) : bash scripts/orchestrate/queue.sh --orphelins\n' >&2
    fi
  fi
  # Les blocs de plus d'un membre sont ce que le tri a dû garder contigu : les montrer, c'est
  # rendre vérifiable la règle 3 sans relire le plan à la main.
  awk -F '\t' '{ n[$1]++ } END { for (g in n) if (n[g] > 1) printf "  bloc #%s : %d lot(s) contigus\n", g, n[g] }' \
    "$TMP/membres.tsv" | sort >&2

  # Et les GROUPES DE DÉPENDANCE (règle 5), pour la même raison : une colonne de plus dans le plan
  # ne dit pas d'elle-même ce qu'elle a conclu. Un groupe à plusieurs membres est exactement ce que
  # le run pourra mener de front ; un groupe seul est une barrière.
  printf '\ngroupes de dépendance — partent ensemble si le groupe est identique, ou si les parents diffèrent :\n' >&2
  sort -t$'\t' -k6,6 -k2,2n "$TMP/membres.tsv" | awk -F '\t' '
    function vide() {
      if (g == "") return
      printf "  %-10s %s%s\n", g, liste,
        (g == "-" ? "   (hors lot — indépendants entre eux)" : (nb > 1 ? "   (parallélisables)" : ""))
    }
    $6 != g { vide(); g = $6; liste = ""; nb = 0 }
    { liste = liste (nb ? ", " : "") "#" $3; nb++ }
    END { vide() }
  ' >&2

  # Et ce que ces groupes ne peuvent pas dire (#562) : un parent sans un seul marqueur rend autant
  # de vagues que de lots, donc un séquentiel qui a l'air décidé. MUET quand tout est arbitré —
  # signaler l'abstention nominale apprend à ne plus lire les signalements (règle de `gc --auto`).
  if [ -s "$TMP/non-arbitres.tsv" ]; then
    printf '\nparents jamais arbitrés — leur séquentiel n'\''a pas été décidé, il a été subi :\n' >&2
    sort -t$'\t' -k1,1n "$TMP/non-arbitres.tsv" |
      while IFS=$'\t' read -r p au_plan _ lots t; do
        printf '  #%-5s %s lot(s) au plan sur %s, aucun marqué — %s\n' "$p" "$au_plan" "$lots" "$t" >&2
      done
    printf '  → les arbitrer : bash scripts/orchestrate/queue.sh --non-arbitres\n' >&2
  fi

  # Et ce qu'une session autonome ne pourra pas écrire (#612). MUET quand aucun ticket du plan n'est
  # concerné, même raison que juste au-dessus. Le verbe de la ligne compte : ces tickets sont
  # SIGNALÉS et restent au plan — un `--check` qui dirait « écartés » mentirait sur ce que le run
  # va faire.
  if [ -s "$TMP/touche-claude.tsv" ]; then
    printf '\ntickets qui nomment « .claude/ » — une session autonome ne peut pas y écrire :\n' >&2
    sort -t$'\t' -k1,1n "$TMP/touche-claude.tsv" |
      while IFS=$'\t' read -r i p t; do
        printf '  #%-5s %s%s\n' "$i" "$t" \
          "$([ "$p" != "-" ] && printf ' (lot de #%s)' "$p")" >&2
      done
    printf '  → leur correctif partira dans la description de la PR au lieu d'\''être appliqué (#188),\n' >&2
    printf '    et depuis #418/#419 cette PR est mergée sans que personne ne l'\''ouvre. Ils RESTENT au\n' >&2
    printf '    plan : les écarter est une décision, et le geste est de les assigner.\n' >&2
  fi
fi
