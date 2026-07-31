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
#     rang <TAB> iid <TAB> parent <TAB> prio <TAB> titre
# `parent` vaut « - » pour un ticket qui n'est pas un lot. Le rang est l'ordre de traitement.
#
# --- Les règles d'ordonnancement, et pourquoi -----------------------------------------------------
#
# 1. Ne sont retenus que les tickets « À faire » et NON ASSIGNÉS du milestone courant. Un ticket
#    assigné est le travail de quelqu'un (anti-collision, docs/10 §5) ; un ticket d'un autre
#    milestone n'est pas la phase en cours.
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
# --- Coût en appels -------------------------------------------------------------------------------
# Deux lectures GraphQL (les tickets du milestone, les assignés du backlog ouvert) puis UNE lecture
# par candidat, mise en cache : la même sortie de `glab issue view` sert à répondre aux deux
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

usage() {
  cat <<'USAGE'
L'ordre de traitement des tickets pour la boucle d'orchestration autonome.

  bash scripts/orchestrate/queue.sh [options]

Options :
  --check              Affiche aussi, sur stderr, le diagnostic : milestone retenu, tickets
                       écartés avec leur raison, et les groupes de lots formés.
  --milestone <titre>  Milestone à traiter (titre exact). Par défaut : la phase courante
                       (lib.sh current-milestone).
  --milestones         N'imprime pas de plan : liste les milestones ACTIFS sur lesquels un run
                       peut porter, avec ce qu'ils ont de traitable — titre, courant (0/1),
                       « À faire » et libres, ouverts, échéance. C'est ce que /orchestrate lit
                       pour proposer le choix du milestone avant un run neuf.
  -h, --help           Cette aide.

Sortie (stdout, TSV) : rang, iid, parent, prio, titre. Lecture seule — n'écrit rien.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --milestone) MILESTONE="${2:-}"; shift ;;
    --milestones | --jalons) LISTE_MILESTONES=1 ;;
    -h | --help) usage; exit 0 ;;
    *) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

diag() { [ "$CHECK" = 1 ] && printf '%s\n' "$*" >&2; return 0; }

gl_require_glab || exit 1

TMP="$(mktemp -d "${TMPDIR:-/tmp}/maestro-queue.XXXXXX")" || {
  echo "queue.sh : impossible de créer un dossier temporaire" >&2; exit 1
}
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/vue"

# --- 0. Les milestones sur lesquels un run peut porter (#204) --------------------------------------
# `--milestones` répond à « quel milestone traiter ? », la question que /orchestrate pose avant de
# lancer un run NEUF. Elle était tranchée en silence par la phase courante — le bon défaut, mais pas
# toujours le bon choix : plusieurs milestones actifs peuvent porter du travail en même temps.
#
# Sortie TSV, du plus ancien au plus récent (l'ordre de gl_milestones), en-tête « # » ignorable :
#     titre <TAB> courant <TAB> a_faire <TAB> ouverts <TAB> echeance
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
  local courant titre echeance ouverts a_faire
  courant="$(gl_current_milestone 2>/dev/null)" || courant=""
  gl_backlog_table opened >"$TMP/backlog.tsv" || return 1
  printf '# titre\tcourant\ta_faire\touverts\techeance\n'
  gl_milestones | awk -F'\t' -v OFS='\t' '$1 !~ /^#/ && $2 == "active" { print $1, $4, $6 - $5 }' |
    while IFS=$'\t' read -r titre echeance ouverts; do
      [ -n "$titre" ] || continue
      gl_milestone_issues "$titre" >"$TMP/milestone-liste.tsv" 2>/dev/null ||
        : >"$TMP/milestone-liste.tsv"
      a_faire="$(awk -F'\t' '
        FNR == NR { if ($1 !~ /^#/) assigne[$1] = $5; next }
        /^#/ { next }
        $2 == "À faire" && (!($1 in assigne) || assigne[$1] == "-" || assigne[$1] == "") { n++ }
        END { print n + 0 }' "$TMP/backlog.tsv" "$TMP/milestone-liste.tsv")"
      printf '%s\t%s\t%s\t%s\t%s\n' \
        "$titre" "$([ "$titre" = "$courant" ] && printf 1 || printf 0)" \
        "$a_faire" "$ouverts" "$echeance"
    done
}

if [ "$LISTE_MILESTONES" = 1 ]; then
  milestones_traitables || exit 1
  exit 0
fi

# --- 1. Le milestone ------------------------------------------------------------------------------
if [ -z "$MILESTONE" ]; then
  MILESTONE="$(gl_current_milestone)" || {
    echo "queue.sh : aucun milestone actif non soldé — rien à planifier." >&2; exit 1
  }
fi
diag "milestone : $MILESTONE — seuls ses tickets sont lus ; un ticket d'un autre milestone n'est pas candidat."

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
    if (statut != "À faire") { print iid, "statut « " statut " »", titre > ecartes; next }
    a = (iid in assigne) ? assigne[iid] : "-"
    if (a != "-" && a != "") { print iid, "assigné à " a, titre > ecartes; next }
    print iid, prio, titre
  }
' "$TMP/backlog.tsv" "$TMP/milestone.tsv" >"$TMP/candidats.tsv"
touch "$TMP/ecartes.tsv"

if [ ! -s "$TMP/candidats.tsv" ]; then
  diag "aucun ticket « À faire » et libre dans ce milestone."
  printf '# rang\tiid\tparent\tprio\ttitre\n'
  exit 0
fi

# --- 4. Une lecture par ticket, deux projections --------------------------------------------------
# vue <iid> -> chemin d'un fichier contenant la sortie de `glab issue view`, mise en cache. Le cache
# est ce qui rend gratuite la relecture d'un parent déjà lu comme candidat.
vue() {
  # Deux instructions, et non `local iid="$1" f="…$iid…"` : `local` est une commande, dont bash
  # développe TOUS les mots avant d'exécuter la moindre affectation — `$iid` y vaudrait donc encore
  # celui de la portée appelante, et le cache rendrait la vue d'un autre ticket.
  local iid="$1"
  local f="$TMP/vue/$iid.txt"
  if [ ! -f "$f" ]; then
    glab issue view "$iid" >"$f" 2>/dev/null || { rm -f "$f"; return 1; }
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

# --- 5. Groupes : un par parent, un par ticket isolé -----------------------------------------------
# Un « groupe » est l'unité que le tri déplace : soit les lots d'un même parent (qui doivent rester
# contigus et ordonnés), soit un ticket seul. On note pour chaque candidat son groupe et son rang
# DANS le groupe ; le tri final se fait sur (priorité du groupe, iid minimal du groupe, rang).
: >"$TMP/membres.tsv"   # groupe <TAB> rang-dans-groupe <TAB> iid <TAB> parent <TAB> prio <TAB> titre

rang_prio() { # <prio> -> clé de tri numérique (haute d'abord)
  case "$1" in
    haute) printf '1' ;;
    moyenne) printf '2' ;;
    basse) printf '3' ;;
    *) printf '4' ;;
  esac
}

# rang_dans_checklist <parent> <iid> -> position du lot dans la checklist du parent (1, 2, …), ou
# rien si le lot n'y figure pas (lot lié mais oublié de la checklist : on ne devine pas sa place).
rang_dans_checklist() {
  local f
  f="$(vue "$1")" || return 1
  gl_subticket_rows <"$f" | awk -F '\t' -v cible="$2" '$1 == cible { print NR; exit }'
}

while IFS=$'\t' read -r iid prio titre; do
  [ -n "$iid" ] || continue
  if est_parent "$iid"; then
    printf '%s\t%s\t%s\n' "$iid" "parent de suivi (ne porte ni branche ni code)" "$titre" >>"$TMP/ecartes.tsv"
    continue
  fi
  parent="$(parent_de "$iid")"
  if [ -n "$parent" ]; then
    rang="$(rang_dans_checklist "$parent" "$iid")"
    # Un lot absent de la checklist de son parent est traité comme isolé plutôt que placé au
    # hasard : mieux vaut un ordre visiblement plat qu'un ordre faux qui a l'air juste.
    if [ -z "$rang" ]; then
      diag "  ⚠ #$iid se déclare lot de #$parent mais n'est pas dans sa checklist — traité isolément"
      printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$iid" 1 "$iid" "-" "$prio" "$titre" >>"$TMP/membres.tsv"
      continue
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$parent" "$rang" "$iid" "$parent" "$prio" "$titre" >>"$TMP/membres.tsv"
  else
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$iid" 1 "$iid" "-" "$prio" "$titre" >>"$TMP/membres.tsv"
  fi
done <"$TMP/candidats.tsv"

if [ ! -s "$TMP/membres.tsv" ]; then
  diag "tous les candidats ont été écartés."
  printf '# rang\tiid\tparent\tprio\ttitre\n'
  [ "$CHECK" = 1 ] && sort -t$'\t' -k1,1n "$TMP/ecartes.tsv" |
    while IFS=$'\t' read -r i r t; do printf '  écarté #%s — %s (%s)\n' "$i" "$r" "$t" >&2; done
  exit 0
fi

# --- 6. Priorité et iid minimal de chaque groupe ---------------------------------------------------
# La priorité d'un groupe est la MEILLEURE de ses membres : un parent dont un seul lot est
# « prio::haute » passe devant, sans quoi ce lot serait retenu par ses voisins moins prioritaires.
while IFS=$'\t' read -r groupe rang iid parent prio titre; do
  printf '%s\t%s\t%s\n' "$groupe" "$(rang_prio "$prio")" "$iid"
done <"$TMP/membres.tsv" | awk -F '\t' -v OFS='\t' '
  { if (!($1 in p) || $2 < p[$1]) p[$1] = $2
    if (!($1 in m) || $3 + 0 < m[$1]) m[$1] = $3 + 0 }
  END { for (g in p) print g, p[g], m[g] }
' >"$TMP/groupes.tsv"

# --- 7. Le plan ------------------------------------------------------------------------------------
printf '# rang\tiid\tparent\tprio\ttitre\n'
awk -F '\t' -v OFS='\t' '
  FNR == NR { pr[$1] = $2; mi[$1] = $3; next }
  { print pr[$1], mi[$1], $2, $3, $4, $5, $6 }
' "$TMP/groupes.tsv" "$TMP/membres.tsv" |
  sort -t$'\t' -k1,1n -k2,2n -k3,3n |
  awk -F '\t' -v OFS='\t' '{ print NR, $4, $5, $6, $7 }'

# --- 8. Diagnostic ---------------------------------------------------------------------------------
if [ "$CHECK" = 1 ]; then
  nb_ecartes="$(wc -l <"$TMP/ecartes.tsv" | tr -d ' ')"
  nb_retenus="$(wc -l <"$TMP/membres.tsv" | tr -d ' ')"
  printf '\n%s ticket(s) retenu(s), %s écarté(s)\n' "$nb_retenus" "$nb_ecartes" >&2
  if [ "$nb_ecartes" -gt 0 ]; then
    sort -t$'\t' -k1,1n "$TMP/ecartes.tsv" |
      while IFS=$'\t' read -r i r t; do printf '  écarté #%-4s %s — %s\n' "$i" "$r" "$t" >&2; done
  fi
  # Les groupes de plus d'un membre sont ce que le tri a dû garder contigu : les montrer, c'est
  # rendre vérifiable la règle 3 sans relire le plan à la main.
  awk -F '\t' '{ n[$1]++ } END { for (g in n) if (n[g] > 1) printf "  groupe #%s : %d lot(s) contigus\n", g, n[g] }' \
    "$TMP/membres.tsv" | sort >&2
fi
