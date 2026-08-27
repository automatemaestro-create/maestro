#!/usr/bin/env bash
# LE PEUPLEMENT DU DÉCOUPAGE NATIF — les parents existants rattachés à leurs lots (#392, chantier #389).
#
# C'est l'équivalent exact du backfill de #361 : #390 a appris à LIRE les sub-issues, #391 à les
# ÉCRIRE, et rien n'en porte encore. Sans ce script, la bascule du lot 4 (`MAESTRO_LOTS=natif` par
# défaut, #393) rendrait invisible TOUT l'historique du découpage — et la panne serait silencieuse :
# un parent sans sub-issue native est indiscernable d'un ticket ordinaire, `subtickets` répondrait
# « pas un ticket parent » et `/ticket-start` refuserait de rediriger vers un lot.
#
#   bash scripts/github/backfill-sous-tickets.sh [--check] [<iid-parent>…]
#
# Codes : 0 = tout est en place (ou vient de l'être) · 1 = au moins un échec ou un conflit ·
#         2 = usage · 3 = `--check` et il reste quelque chose à faire (convention de
#         `worktree.sh gc --check` et de `setup.sh --derive`).
#
# ── IL N'AJOUTE QUE DE LA DONNÉE, ET C'EST CE QUI LE REND MERGEABLE SEUL ────────────────────────
# La section `## Sous-tickets` reste en place — c'est le lot 6 (#395) qui la retire, et lui seul.
# Tant que `MAESTRO_LOTS` vaut `checklist`, ce que ce script écrit est écrit sans être lu : le dépôt
# se comporte au bit près comme avant, et un retour arrière ne coûte rien puisqu'il n'y a rien à
# défaire. Écrire les deux supports est le propre d'une migration ; en LIRE deux serait la panne que
# le commutateur interdit (même argument que `gl_issue_link`, #391).
#
# ── UNE LECTURE PAR PARENT, ET LES DEUX RÉGIMES LA RELISENT ─────────────────────────────────────
# Le ticket est lu UNE FOIS en régime `natif` : la vue canonique porte alors, dans son en-tête, un
# `lot:` par sub-issue DÉJÀ rattachée (#390) et, dans son corps, la checklist markdown intacte.
# `gl_subticket_rows` en tire donc les deux états sans un aller de plus — l'ÉTAT VOULU en régime
# `checklist`, l'ÉTAT COURANT en régime `natif` —, aux mêmes quatre colonnes et par le même parseur.
# Aucun parseur n'est réécrit ici, et c'est délibéré : deux formulations de « quels sont les lots de
# ce parent ? » finiraient par ne plus rendre le même verdict, sur le seul geste qui les fige.
#
# ── PLANIFIER D'ABORD, ÉCRIRE ENSUITE ───────────────────────────────────────────────────────────
# Les deux phases sont séparées pour trois raisons, dont une seule est de confort. Le confort :
# `--check` est alors exactement la phase 1, et non un second chemin à tenir d'accord avec le
# premier. Les deux autres pèsent plus. Le PLAN COMPLET permet de nommer d'un coup ce qui ne sera
# pas fait — un rapport qui découvre ses trous au fil de l'eau les noie dans la trace des succès.
# Et surtout, la garde de Status ci-dessous a besoin d'un AVANT et d'un APRÈS pris sur l'ensemble
# des lots concernés : deux allers pour tout le dépôt, là où la mesurer parent par parent en
# coûterait deux par parent (~80 allers pour 41 parents, ~3 min de réseau).
#
# ── LA GARDE DE STATUS : CE QUE LE RATTACHEMENT PEUT REPOSER SANS QU'ON LE DEMANDE ──────────────
# Le workflow natif « Auto-add sub-issues to project » est ACTIVÉ sur le projet « Maestro » (#389) :
# un lot rattaché y entre d'office. S'il n'y était pas déjà, le workflow « Item added to project »
# peut lui poser un Status par défaut — c'est-à-dire « À faire » sur un ticket FERMÉ depuis des
# mois, et la dérive exacte que #275 et #377 passent leur temps à réparer. Le backfill de #361 ayant
# déjà mis tout le backlog dans le projet, le cas devrait être vide ; « devrait » n'est pas une
# vérification. On relève donc le Status des lots à rattacher AVANT et APRÈS, et tout écart est
# NOMMÉ avec la commande qui le répare (`reconcile-workflow`, verbe de #275, dont la liste blanche
# protège déjà « Abandonné »/« Doublon »). On ne le répare PAS d'office : ce script rattache, il ne
# tient pas le cycle de vie, et une réparation muette rendrait la dérive invisible une fois de plus.
#
# ── IL EST FRANC, ET C'EST UN CRITÈRE ───────────────────────────────────────────────────────────
# Quatre choses peuvent mal tourner, et aucune ne se solde par un saut silencieux : une ligne de
# checklist que le parseur ne résout pas (`bf_lignes_orphelines`), un `#<n>` qui ne désigne aucune
# issue, un lot déjà rattaché à un AUTRE parent — un conflit et non un doublon, qu'on signale sans
# jamais le déplacer (`gl_subticket_add` refuse de lui-même) —, et une sub-issue native que la
# checklist ne mentionne pas. Toutes sont comptées et rendues nommément en fin de rapport.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$here/../gitlab/lib.sh"

check=0
parents_demandes=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check) check=1 ;;
    -h|--help)
      echo "usage: $0 [--check] [<iid-parent>…]" >&2
      echo "  --check          n'écrit rien ; code 3 s'il reste quelque chose à faire" >&2
      echo "  <iid-parent>…    ces parents-là au lieu de la recherche « in:body » du dépôt" >&2
      exit 2 ;;
    *[!0-9]*) echo "$0 : « $1 » n'est ni une option connue ni un iid." >&2; exit 2 ;;
    *) parents_demandes+=("$1") ;;
  esac
  shift
done

gl_require || exit 1

if [ -t 1 ]; then
  C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_B=$'\033[1m'; C_0=$'\033[0m'
else
  C_G=''; C_Y=''; C_R=''; C_B=''; C_0=''
fi

# ── Découverte ──────────────────────────────────────────────────────────────────────────────────
# LA RECHERCHE `in:body` EST CELLE DU TICKET, et elle rend des CANDIDATS, pas des parents : un
# ticket qui PARLE de la section (#389 et #392 le font, #395 le fera) y répond comme un ticket qui
# en PORTE une. Le verdict est rendu plus bas par `gl_subticket_rows`, sur le corps réel — c'est le
# même parti pris que partout ici : la forge propose, le parseur du dépôt tranche.
#
# La liste explicite existe pour deux usages, et pas pour se passer de la recherche : rejouer un
# parent isolé après avoir corrigé sa checklist, et rattraper à la main ce que l'index de recherche
# de GitHub n'aurait pas rendu — c'est la seule faiblesse connue de la découverte, et la nommer vaut
# mieux que de prétendre l'avoir couverte.
bf_candidats() {
  local apres='null' raw
  while :; do
    raw="$(gh_graphql_read '{ search(query: "repo:'"$GL_GH_REPO"' in:body \"## Sous-tickets\"", type: ISSUE, first: 100, after: '"$apres"') { pageInfo { hasNextPage endCursor } nodes { ... on Issue { number } } } }')" || return 1
    printf '%s' "$raw" | grep -o '"number":[0-9]\+' | sed 's/.*://'
    case "$raw" in
      *'"hasNextPage":true'*) ;;
      *) return 0 ;;
    esac
    apres="\"$(printf '%s' "$raw" | grep -o '"endCursor":"[^"]*"' | head -1 | sed 's/.*:"//; s/"$//')\""
    [ "$apres" = '""' ] && return 0
  done
}

# bf_rows <régime> (stdin = vue canonique) -> les lots que CE régime-là sait lire, aux quatre
# colonnes de `gl_subticket_rows` (iid, coche, marqueur, titre).
#
# Le régime voyage en PRÉFIXE d'appel et non par une affectation posée à côté : bash le pose le
# temps de la fonction, ses sous-shells compris, puis le retire — un `MAESTRO_LOTS=…` laissé dans la
# portée du script gouvernerait toutes les lectures qui suivent, et c'est précisément l'ambiguïté
# que ce verbe existe pour lever, puisqu'on lit la même vue dans les deux régimes à trois lignes
# d'intervalle.
bf_rows() {
  MAESTRO_LOTS="$1" gl_subticket_rows
}

# bf_lignes_orphelines (stdin = vue canonique) -> « item<TAB><ligne> » ou « prose<TAB><ligne> » pour
# chaque ligne de la section « ## Sous-tickets » que `gl_subticket_rows` N'A PAS retenue, vides
# exceptées.
#
# C'EST LE COMPLÉMENT D'UN PARSEUR, PAS UN SECOND PARSEUR. Ses trois motifs — début de section, fin
# de section, ligne d'item — sont ceux de `gl_subticket_rows` recopiés au caractère près, et le seul
# but de la recopie est de rendre visible ce que l'original laisse tomber. Sans elle, le script
# sauterait ces lignes exactement comme le parseur les saute — en silence, sur le seul geste qui
# fige le découpage pour de bon. Les deux disparaissent ensemble au lot 6 (#395), avec la section.
#
# ── LES DEUX CLASSES NE SE MÉLANGENT PAS, ET C'EST UNE MESURE QUI L'A TRANCHÉ ───────────────────
# Un premier jet rendait TOUTE ligne non retenue, ce que le critère demande à la lettre (« nommer
# les lignes qu'il n'a pas su résoudre »). Le `--check` du 2026-08-27 a montré ce que ça donne :
# 11 parents sur 46, et pas une seule ligne fautive — rien que des paragraphes d'explication rangés
# sous le titre de section (l'ordre de réalisation de #314, l'arbitrage de #569, les notes de #155),
# soit ~50 lignes de prose parfaitement légitime noyant un rapport où le nombre de vrais défauts
# était ZÉRO. Un signalement qui nomme 11 parents dont 11 vont bien n'est plus lu (même leçon que
# les 25 parents arbitrés de #562).
#
# La ligne de partage est donc la PUCE de liste : dans une section faite de `- [ ] #<n> …`, une
# ligne à puce PRÉTEND être un lot — si le parseur ne l'a pas prise, c'est un défaut, et c'est
# exactement le cas que le critère vise (« - [ ] Écrire la doc » sans iid, un item mal formé, une
# coche exotique). Une ligne SANS puce ne prétend rien : c'est de la prose, comptée et jamais
# recopiée. Elle n'est pas pour autant sans intérêt — le lot 6 (#395), qui supprimera ces sections,
# a besoin de savoir qu'il y en a à sauver —, d'où un compte, qui est ce qu'un lot voisin peut lire.
bf_lignes_orphelines() {
  awk '
    insec {
      if ($0 ~ /^#+[ \t]/) { insec = 0; next }
      if ($0 ~ /^- \[[ xX]\] #[0-9]+/) next
      if ($0 ~ /^[ \t]*$/) next
      if ($0 ~ /^[ \t]*([-*+][ \t]|[0-9]+[.)][ \t])/) printf "item\t%s\n", $0
      else printf "prose\t%s\n", $0
      next
    }
    /^#+[ \t]+Sous-tickets/ { insec = 1 }
  '
}

# bf_statuts <iid…> -> « <iid><TAB><Status> » pour chacun, par paquets de 100.
# `st_statuts` construit un alias GraphQL par iid dans un seul document : c'est ce qui rend la garde
# de Status abordable (deux allers pour tout le dépôt), et le paquet borne la taille du document
# plutôt que de parier sur ce que l'API accepte.
bf_statuts() {
  local lot=()
  while [ "$#" -gt 0 ]; do
    lot+=("$1"); shift
    if [ "${#lot[@]}" -ge 100 ] || [ "$#" = 0 ]; then
      st_statuts "${lot[@]}" || return 1
      lot=()
    fi
  done
}

# ── Phase 1 : le plan ───────────────────────────────────────────────────────────────────────────
if [ "${#parents_demandes[@]}" -gt 0 ]; then
  candidats=("${parents_demandes[@]}")
  printf '%sDécouverte%s — %s parent(s) nommé(s) en argument.\n' "$C_B" "$C_0" "${#candidats[@]}"
else
  printf '%sDécouverte%s — recherche « in:body "## Sous-tickets" » dans %s…\n' "$C_B" "$C_0" "$GL_GH_REPO"
  mapfile -t candidats < <(bf_candidats)
  if [ "${#candidats[@]}" = 0 ]; then
    echo "Aucun candidat rendu par la recherche — index vide, ou lecture en échec." >&2
    exit 1
  fi
  printf '  %s candidat(s) — un ticket qui PARLE de la section y répond comme un ticket qui en porte une.\n' \
    "${#candidats[@]}"
fi

declare -A plan_attacher=()   # parent -> lots à rattacher, dans l'ordre de la checklist
declare -A plan_ordre=()      # parent -> ordre voulu (tous les lots de la checklist)
declare -A plan_marquer=()    # parent -> lots dont le marqueur « (parallèle) » reste à poser
declare -A plan_orphelines=() # parent -> lignes À PUCE non résolues (séparées par \n) : des défauts
declare -A plan_hors=()       # parent -> sub-issues natives absentes de la checklist
parents=()
non_parents=0
illisibles=()
a_faire=0
lots_vus=0
prose_parents=0
prose_lignes=0

for iid in "${candidats[@]}"; do
  # RÉGIME `natif` À LA LECTURE : c'est lui qui ajoute les lignes `lot:` de l'en-tête. Le corps —
  # donc la checklist — est là dans les deux régimes ; le natif est le seul à porter les deux.
  raw="$(MAESTRO_LOTS=natif gl_issue_raw "$iid" 2>/dev/null)"
  if [ -z "$raw" ]; then
    illisibles+=("$iid")
    continue
  fi

  # Les deux relectures de la MÊME vue, par le MÊME parseur, dans les deux régimes.
  voulu="$(printf '%s\n' "$raw" | bf_rows checklist)"
  courant="$(printf '%s\n' "$raw" | bf_rows natif)"

  restes="$(printf '%s\n' "$raw" | bf_lignes_orphelines)"
  orphelines="$(printf '%s\n' "$restes" | awk -F '\t' '$1 == "item" { print substr($0, 6) }')"
  prose="$(printf '%s\n' "$restes" | awk -F '\t' '$1 == "prose"' | wc -l | tr -d ' ')"

  if [ -z "$voulu" ]; then
    # Pas de checklist : soit le candidat ne fait que citer la section (cas nominal de la
    # recherche), soit un parent nommé en argument n'en est pas un — et là il faut le dire.
    if [ "${#parents_demandes[@]}" -gt 0 ]; then
      printf "  %s⚠%s #%s n'a pas de section « ## Sous-tickets » — ignoré.\n" "$C_Y" "$C_0" "$iid"
    fi
    non_parents=$((non_parents + 1))
    continue
  fi

  parents+=("$iid")
  lots_vus=$((lots_vus + $(printf '%s\n' "$voulu" | wc -l)))
  if [ "$prose" -gt 0 ]; then
    prose_parents=$((prose_parents + 1))
    prose_lignes=$((prose_lignes + prose))
  fi

  voulu_iids="$(printf '%s\n' "$voulu" | cut -f1 | tr '\n' ' ')"
  courant_iids="$(printf '%s\n' "$courant" | cut -f1 | tr '\n' ' ')"

  a_attacher=''
  a_marquer=''
  for lot in $voulu_iids; do
    case " $courant_iids " in
      *" $lot "*) ;;
      *) a_attacher="$a_attacher $lot" ;;
    esac
  done
  # LE MARQUEUR SE POSE QUAND LE LOT NE LE PORTE PAS ENCORE, et la colonne `par` du régime natif est
  # justement le label lu sur le lot (#390). Sur un lot pas encore rattaché elle n'existe pas, donc
  # on pose ; au rejeu elle vaut « ∥ », donc on s'abstient. L'idempotence tombe de la donnée, sans
  # qu'aucun compteur n'ait à s'en souvenir.
  while IFS=$'\t' read -r lot _ par _; do
    [ "$par" = '∥' ] || continue
    if ! printf '%s\n' "$courant" | awk -F '\t' -v l="$lot" '$1 == l && $3 == "∥" { trouve = 1 } END { exit !trouve }'; then
      a_marquer="$a_marquer $lot"
    fi
  done <<< "$voulu"

  hors=''
  for lot in $courant_iids; do
    case " $voulu_iids " in
      *" $lot "*) ;;
      *) hors="$hors $lot" ;;
    esac
  done

  # L'ORDRE NE SE REPOSE QUE S'IL DIFFÈRE, et la comparaison porte sur la suite des lots natifs
  # RESTREINTE à ceux de la checklist : un lot rattaché à la main, hors checklist, ne doit pas faire
  # croire à un désordre à chaque rejeu. C'est la moitié « ne réordonne rien » de l'idempotence, et
  # la seule qui demande de regarder autre chose que la présence d'un lien.
  courant_restreint="$(printf '%s\n' "$courant" | cut -f1 | grep -x -F -f <(printf '%s\n' "$voulu" | cut -f1) | tr '\n' ' ')"
  ordre=''
  if [ -n "$a_attacher" ] || [ "$courant_restreint" != "$voulu_iids" ]; then
    ordre="$voulu_iids"
  fi

  [ -n "$a_attacher" ]  && plan_attacher["$iid"]="${a_attacher# }"
  [ -n "$a_marquer" ]   && plan_marquer["$iid"]="${a_marquer# }"
  [ -n "$hors" ]        && plan_hors["$iid"]="${hors# }"
  [ -n "$ordre" ]       && plan_ordre["$iid"]="$ordre"
  [ -n "$orphelines" ]  && plan_orphelines["$iid"]="$orphelines"

  if [ -n "$a_attacher" ] || [ -n "$a_marquer" ] || [ -n "$ordre" ]; then
    a_faire=$((a_faire + 1))
    printf '  %s·%s #%-4s %2s lot(s) —%s%s%s\n' "$C_B" "$C_0" "$iid" \
      "$(printf '%s\n' "$voulu" | wc -l | tr -d ' ')" \
      "${a_attacher:+ rattacher:${a_attacher}}" \
      "${a_marquer:+ marquer:${a_marquer}}" \
      "${ordre:+ + ordre}"
  else
    printf '  %s✓%s #%-4s déjà rattaché et ordonné.\n' "$C_G" "$C_0" "$iid"
  fi
done

printf '\n%sPlan%s — %s parent(s) sur %s candidat(s), %s lot(s) déclaré(s) ; %s à traiter, %s déjà en place.\n' \
  "$C_B" "$C_0" "${#parents[@]}" "${#candidats[@]}" "$lots_vus" "$a_faire" "$((${#parents[@]} - a_faire))"
[ "$non_parents" -gt 0 ] && printf '  %s candidat(s) ne portent pas de checklist (mention de la section, pas un parent).\n' "$non_parents"
if [ "${#illisibles[@]}" -gt 0 ]; then
  printf '  %s⚠%s illisible(s) : %s\n' "$C_Y" "$C_0" "${illisibles[*]}"
fi

# bf_signalements -> ce qui demande un œil humain, dit une fois et de la même façon des deux côtés
# (`--check` et rapport final). Deux appels et un seul texte : un signalement qui ne se lit que sur
# l'un des deux chemins est un signalement qu'on découvre trop tard.
bf_signalements() {
  if [ "$prose_parents" -gt 0 ]; then
    printf '  %s·%s %s parent(s) portent de la prose sous leur titre de section (%s ligne(s)) : ce ne sont\n' \
      "$C_B" "$C_0" "$prose_parents" "$prose_lignes"
    printf '      pas des lots, rien ici ne les touche — mais le lot 6 (#395) aura à en décider.\n'
  fi
  local iid
  for iid in "${!plan_orphelines[@]}"; do
    printf '\n  %s⚠%s #%s — ligne(s) à puce que le parseur des lots n%s résoudre :\n' \
      "$C_Y" "$C_0" "$iid" "'a pas su"
    printf '%s\n' "${plan_orphelines[$iid]}" | sed 's/^/      /'
  done
  for iid in "${!plan_hors[@]}"; do
    printf '  %s⚠%s #%s — sub-issue(s) native(s) hors checklist, laissée(s) en place : %s\n' \
      "$C_Y" "$C_0" "$iid" "${plan_hors[$iid]}"
  done
}

if [ "$check" = 1 ]; then
  bf_signalements
  printf "\n--check : rien n'a été écrit.\n"
  [ "$a_faire" -gt 0 ] && exit 3
  exit 0
fi

# ── Phase 2 : le Status des lots à rattacher, AVANT ─────────────────────────────────────────────
tous_a_attacher=()
for iid in "${!plan_attacher[@]}"; do
  # shellcheck disable=SC2206  # découpage sur l'espace voulu : ce sont des iid validés en chiffres.
  tous_a_attacher+=(${plan_attacher[$iid]})
done

statuts_avant=''
if [ "${#tous_a_attacher[@]}" -gt 0 ]; then
  statuts_avant="$(bf_statuts "${tous_a_attacher[@]}")" \
    || echo "  ⚠ relevé des Status en échec — la garde ne pourra rien comparer." >&2
fi

# ── Phase 3 : l'écriture ────────────────────────────────────────────────────────────────────────
printf '\n%sÉcriture%s\n' "$C_B" "$C_0"
rattaches=0
marques=0
ordonnes=0
conflits=()
echecs=()

for iid in "${parents[@]}"; do
  [ -n "${plan_attacher[$iid]:-}${plan_marquer[$iid]:-}${plan_ordre[$iid]:-}" ] || continue
  printf '  #%s\n' "$iid"

  attaches_ok=''
  for lot in ${plan_attacher[$iid]:-}; do
    if sortie="$(gl_subticket_add "$iid" "$lot" 2>&1)"; then
      attaches_ok="$attaches_ok $lot"
      rattaches=$((rattaches + 1))
      printf '    %s✓%s %s\n' "$C_G" "$C_0" "$sortie"
    else
      # `gl_subticket_add` nomme lui-même le parent en place sur un conflit, et le ticket manquant
      # sur un `#<n>` qui ne désigne rien. On ne réinterprète pas son message : on le relaie.
      case "$sortie" in
        *"est déjà un lot de"*) conflits+=("#$lot (parent #$iid)") ;;
        *) echecs+=("#$lot → #$iid") ;;
      esac
      printf '    %s✗%s %s\n' "$C_R" "$C_0" "$(printf '%s' "$sortie" | head -1)"
    fi
  done

  for lot in ${plan_marquer[$iid]:-}; do
    if gh_add_label "$lot" "$GL_LABEL_LOT_PARALLELE" 2>/dev/null; then
      marques=$((marques + 1))
      printf '    %s✓%s #%s marqué « %s »\n' "$C_G" "$C_0" "$lot" "$GL_LABEL_LOT_PARALLELE"
    else
      echecs+=("marqueur sur #$lot")
      printf '    %s✗%s marqueur « %s » refusé sur #%s\n' "$C_R" "$C_0" "$GL_LABEL_LOT_PARALLELE" "$lot"
    fi
  done

  # L'ORDRE NE PORTE QUE SUR CE QUI EST EFFECTIVEMENT RATTACHÉ. `gl_subticket_order` refuse en bloc
  # dès qu'un iid nommé n'est pas un lot de ce parent — et il a raison : un ordre posé à moitié
  # laisserait le plan dans un état que personne n'a voulu. Lui passer un lot dont le rattachement
  # vient d'échouer ferait donc perdre l'ordre de TOUS les autres, pour un lot qu'on sait absent.
  if [ -n "${plan_ordre[$iid]:-}" ]; then
    voulu_ordre=''
    for lot in ${plan_ordre[$iid]}; do
      case " ${plan_attacher[$iid]:-} " in
        *" $lot "*) case " $attaches_ok " in *" $lot "*) voulu_ordre="$voulu_ordre $lot" ;; esac ;;
        *) voulu_ordre="$voulu_ordre $lot" ;;
      esac
    done
    # shellcheck disable=SC2086  # découpage sur l'espace voulu : iid validés en chiffres.
    if [ -n "$voulu_ordre" ] && sortie="$(gl_subticket_order "$iid" $voulu_ordre 2>&1)"; then
      ordonnes=$((ordonnes + 1))
      printf '    %s✓%s %s\n' "$C_G" "$C_0" "$(printf '%s' "$sortie" | head -1)"
    elif [ -n "$voulu_ordre" ]; then
      echecs+=("ordre de #$iid")
      printf '    %s✗%s %s\n' "$C_R" "$C_0" "$(printf '%s' "$sortie" | head -1)"
    fi
  fi
done

# ── Phase 4 : le Status des lots rattachés, APRÈS ───────────────────────────────────────────────
derives=()
if [ -n "$statuts_avant" ]; then
  statuts_apres="$(bf_statuts "${tous_a_attacher[@]}")" || statuts_apres=''
  if [ -n "$statuts_apres" ]; then
    while IFS=$'\t' read -r lot apres; do
      avant="$(printf '%s\n' "$statuts_avant" | awk -F '\t' -v l="$lot" '$1 == l { print $2; exit }')"
      [ "$avant" = "$apres" ] && continue
      derives+=("#$lot : « ${avant:--} » → « $apres »")
    done <<< "$statuts_apres"
  fi
fi

# ── Rapport ─────────────────────────────────────────────────────────────────────────────────────
printf '\n%sBilan%s — %s lot(s) rattaché(s), %s marqueur(s) posé(s), %s parent(s) réordonné(s).\n' \
  "$C_B" "$C_0" "$rattaches" "$marques" "$ordonnes"

if [ "${#derives[@]}" -gt 0 ]; then
  printf '\n  %s⚠%s Status reposé par le rattachement sur %s ticket(s) :\n' "$C_Y" "$C_0" "${#derives[@]}"
  printf '      %s\n' "${derives[@]}"
  printf '      Réparation : bash scripts/gitlab/lib.sh reconcile-workflow <iid>\n'
elif [ -n "$statuts_avant" ]; then
  printf '  %s✓%s aucun Status touché par le rattachement (garde « Auto-add sub-issues », #389).\n' "$C_G" "$C_0"
fi

bf_signalements

if [ "${#conflits[@]}" -gt 0 ]; then
  printf '\n  %s✗%s %s lot(s) déjà rattaché(s) à un AUTRE parent — signalés, jamais déplacés :\n' \
    "$C_R" "$C_0" "${#conflits[@]}"
  printf '      %s\n' "${conflits[@]}"
fi

if [ "${#echecs[@]}" -gt 0 ]; then
  printf '\n  %s✗%s %s écriture(s) en échec :\n' "$C_R" "$C_0" "${#echecs[@]}"
  printf '      %s\n' "${echecs[@]}"
fi

if [ "${#conflits[@]}" -gt 0 ] || [ "${#echecs[@]}" -gt 0 ] || [ "${#illisibles[@]}" -gt 0 ]; then
  exit 1
fi
exit 0
