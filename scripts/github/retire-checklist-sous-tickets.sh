#!/usr/bin/env bash
# LE RETRAIT DU SUPPORT HISTORIQUE — la checklist ôtée des descriptions de parents (#395, chantier #389).
#
# Le pendant exact du backfill de #392, dans l'autre sens : celui-là avait RATTACHÉ les 41 parents à
# leurs lots en sub-issues natives ; celui-ci retire de leurs descriptions la liste que plus personne
# ne lit depuis la bascule (#393). Le backfill est parti avec elle — il n'avait plus de source.
#
#   bash scripts/github/retire-checklist-sous-tickets.sh [--check] [<iid-parent>…]
#   bash scripts/github/retire-checklist-sous-tickets.sh --filtre < corps.md
#
# Codes : 0 = plus rien à retirer (ou tout vient de l'être) · 1 = au moins un échec ·
#         2 = usage · 3 = `--check` et il reste quelque chose à faire (convention de
#         `worktree.sh gc --check` et de `setup.sh --derive`).
#
# `--filtre` joue la SEULE transformation sur stdin et rend le corps sur stdout, sans forge ni
# réseau ni authentification. Ce n'est pas un mode de confort : c'est ce qui rend la règle
# observable — sur un corps réel avant de l'écrire, et sur un corps décrit par un test sans avoir à
# monter un double de la forge (`--check`, lui, lit le dépôt).
#
# ── LA LISTE PART, LA PROSE RESTE, ET C'EST UNE MESURE QUI L'A TRANCHÉ ───────────────────────────
# La section « ## Sous-tickets » ne portait pas QUE la liste : le `--check` du backfill (2026-08-27)
# a compté **11 parents sur 46 portant ~50 lignes de prose** rangées sous ce titre — l'ordre de
# réalisation de #314, l'arbitrage de #569, les notes de #155 —, et il les a laissées en nommant le
# problème plutôt qu'en le tranchant à notre place (« le lot 6 aura à en décider »).
#
# La décision est donc : on retire les LIGNES D'ITEM (`- [ ] #<n> — …`), c'est-à-dire le support
# dupliqué, et rien d'autre. Ce qui reste sous le titre est du raisonnement qu'aucun autre support ne
# porte — ni les sub-issues, ni les labels, ni le cycle de vie —, et le supprimer serait la seule
# perte irréversible de tout le chantier. L'asymétrie tranche : une section qui survit coûte un
# titre, une prose supprimée coûte le pourquoi d'un chantier.
#
# ── LE TITRE EST RENOMMÉ « ## Découpage » QUAND DE LA PROSE SURVIT ───────────────────────────────
# Et il disparaît avec la section quand il ne reste rien. Deux raisons, et la seconde est la vraie :
# « ## Sous-tickets » ANNONÇAIT UNE LISTE — laisser ce titre au-dessus de trois paragraphes ferait
# chercher la liste qu'il promet, là où GitHub rend désormais les lots lui-même, au-dessus de la
# description ; et ce titre était l'ANCRE DU PARSEUR (`/^#+[ \t]+Sous-tickets/`), si bien que le
# retirer partout est ce qui rend le critère du ticket vérifiable d'un `grep` plutôt que d'un
# jugement. Le niveau de titre d'origine est conservé au caractère près.
#
# ── CE QU'IL NE FAIT PAS, ET LE DIT ─────────────────────────────────────────────────────────────
# Il ne RELIT PAS la prose qu'il conserve. Une partie a vieilli — « Fermeture de ce parent :
# décision humaine, toutes cases cochées » était vrai avant #515, qui ferme le parent tout seul —,
# et la corriger demande un jugement par parent, pas un script. Elle est donc conservée, COMPTÉE et
# NOMMÉE dans le rapport : visible et à élaguer, plutôt que supprimée en silence.
#
# ── IDEMPOTENT PAR LA DONNÉE, PAS PAR UN COMPTEUR ───────────────────────────────────────────────
# Un second passage relit les mêmes descriptions, n'y trouve plus de ligne d'item, calcule un corps
# IDENTIQUE à celui qui est en place et n'écrit pas. Rien à retenir entre deux exécutions, et
# `--check` est exactement la phase de planification — non un second chemin à tenir d'accord avec
# le premier (même raison qu'au backfill).
#
# ── LES OCTETS, ET LE PIÈGE DE #141 ─────────────────────────────────────────────────────────────
# L'aller-retour lecture → réécriture d'une description a déjà repoussé du mojibake dans un parent.
# On passe donc par `gl_get_description` / `gl_set_description`, les deux seuls verbes écrits pour
# ça, et la transformation est un `awk` sous `LC_ALL=C` : il déplace des LIGNES, il ne décode rien.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$here/../gitlab/lib.sh"

check=0
filtre=0
parents_demandes=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check) check=1 ;;
    --filtre) filtre=1 ;;
    -h|--help)
      echo "usage: $0 [--check] [<iid-parent>…]" >&2
      echo "       $0 --filtre < corps.md" >&2
      echo "  --check          n'écrit rien ; code 3 s'il reste quelque chose à retirer" >&2
      echo "  --filtre         joue la transformation sur stdin (ni forge ni réseau)" >&2
      echo "  <iid-parent>…    ces parents-là au lieu de la recherche « in:body » du dépôt" >&2
      exit 2 ;;
    *[!0-9]*) echo "$0 : « $1 » n'est ni une option connue ni un iid." >&2; exit 2 ;;
    *) parents_demandes+=("$1") ;;
  esac
  shift
done

if [ -t 1 ]; then
  C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_B=$'\033[1m'; C_0=$'\033[0m'
else
  C_G=''; C_Y=''; C_R=''; C_B=''; C_0=''
fi

# Les corps lus et leur version réécrite : des brouillons que personne ne relit — ils ne survivent
# pas à l'exécution, et ce qu'il y a à lire est le RAPPORT, pas un fichier. Répertoire temporaire du
# système, donc, et pas `.maestro/` (règle #234, docs/10 §8.5).
TMP="$(mktemp -d "${TMPDIR:-/tmp}/maestro-retrait.XXXXXX")" || exit 1
trap 'rm -rf "$TMP"' EXIT

# ── Découverte ──────────────────────────────────────────────────────────────────────────────────
# LA MÊME RECHERCHE QUE LE BACKFILL, et elle rend les mêmes CANDIDATS : un ticket qui PARLE de la
# section (#389, #392, #395 le font) y répond comme un ticket qui en PORTE une. Le verdict est rendu
# plus bas sur le corps réel — la forge propose, le dépôt tranche.
#
# ELLE CONVERGE, ET C'EST CE QUI REND LE CRITÈRE VÉRIFIABLE : chaque passage retire des lignes de la
# recherche, et ce qui finit par rester est exactement l'ensemble des tickets qui MENTIONNENT la
# section sans en porter une — c'est-à-dire les tickets de ce chantier-ci.
rc_candidats() {
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

# rc_retire (stdin = description) -> la description SANS la checklist, sur stdout.
#
# Trois temps, et le découpage en index plutôt qu'en machine à états est délibéré : la gestion des
# LIGNES VIDES autour de la section demande de regarder ce qui la précède et ce qui la suit, ce
# qu'un automate qui imprime au fil de l'eau ne peut pas faire sans se souvenir de tout.
#   1. repérer la section : du titre `^#+[ \t]+Sous-tickets` au titre suivant (ou la fin) ;
#   2. y trier les lignes d'item (jetées) et le reste (gardé, blancs de bord retirés) ;
#   3. réassembler — section renommée si de la prose survit, section absente sinon, avec dans les
#      deux cas UNE ligne vide de séparation et aucune en fin de description.
#
# `printf` et non `print` pour les lignes du corps : `print` ajoute l'OFS/ORS courant, et une
# description est rendue telle quelle. `LC_ALL=C` : on déplace des octets, on n'en décode aucun.
rc_retire() {
  LC_ALL=C RC_TITRE="Découpage" awk '
    { l[++n] = $0 }
    END {
      # 1. la section
      s = 0
      for (i = 1; i <= n; i++) {
        if (l[i] ~ /^#+[ \t]+Sous-tickets/) { s = i; break }
      }
      if (s == 0) { for (i = 1; i <= n; i++) printf "%s\n", l[i]; exit }
      e = n + 1
      for (i = s + 1; i <= n; i++) {
        if (l[i] ~ /^#+[ \t]/) { e = i; break }
      }

      # 2. le tri. Le niveau du titre est repris au caractère près : un parent en « ### » le reste.
      diese = l[s]; sub(/[^#].*$/, "", diese)
      g = 0
      for (i = s + 1; i < e; i++) {
        if (l[i] ~ /^- \[[ xX]\] #[0-9]+/) continue
        garde[++g] = l[i]
      }
      d = 1; f = g
      while (d <= f && garde[d] ~ /^[ \t]*$/) d++
      while (f >= d && garde[f] ~ /^[ \t]*$/) f--

      # 3. le réassemblage. Avant la section : on retire les lignes vides de queue, la séparation
      # étant réémise ci-dessous — sans quoi une section qui disparaît laisserait le trou de son
      # titre.
      avant = s - 1
      while (avant >= 1 && l[avant] ~ /^[ \t]*$/) avant--
      for (i = 1; i <= avant; i++) printf "%s\n", l[i]

      if (f >= d) {
        if (avant >= 1) printf "\n"
        printf "%s %s\n\n", diese, ENVIRON["RC_TITRE"]
        for (i = d; i <= f; i++) printf "%s\n", garde[i]
      }

      # Après la section : on saute ses lignes vides de tête, pour la raison symétrique.
      apres = e
      while (apres <= n && l[apres] ~ /^[ \t]*$/) apres++
      if (apres <= n) {
        if (avant >= 1 || f >= d) printf "\n"
        for (i = apres; i <= n; i++) printf "%s\n", l[i]
      }
    }
  '
}

# rc_items (stdin = description) -> le nombre de lignes d'item de la section, 0 s'il n'y en a pas.
# C'EST LUI QUI DIT « PARENT », ET PAS LE TITRE : un ticket peut porter un titre de section sans
# liste (aucun dans le dépôt, mais le contrat ne le promet pas), et transformer celui-là ne
# retirerait rien tout en renommant son titre — une écriture pour rien.
rc_items() {
  LC_ALL=C awk '
    insec {
      if ($0 ~ /^#+[ \t]/) { insec = 0; next }
      if ($0 ~ /^- \[[ xX]\] #[0-9]+/) k++
      next
    }
    /^#+[ \t]+Sous-tickets/ { insec = 1 }
    END { print k + 0 }
  '
}

# rc_prose (stdin = description) -> le nombre de lignes NON VIDES et NON ITEM sous le titre, c'est-
# à-dire ce que le retrait conserve. Le rapport les compte pour que « il en reste » soit un fait
# annoncé et non une découverte au prochain `grep`.
rc_prose() {
  LC_ALL=C awk '
    insec {
      if ($0 ~ /^#+[ \t]/) { insec = 0; next }
      if ($0 ~ /^- \[[ xX]\] #[0-9]+/) next
      if ($0 ~ /^[ \t]*$/) next
      k++
      next
    }
    /^#+[ \t]+Sous-tickets/ { insec = 1 }
    END { print k + 0 }
  '
}

# LE FILTRE SORT AVANT `gl_require`, et c'est tout son intérêt : il ne parle à personne, donc il ne
# demande aucune authentification. Placé après lui, il aurait exigé un `gh` connecté pour relire un
# fichier local.
if [ "$filtre" = 1 ]; then
  rc_retire
  exit 0
fi

gl_require || exit 1

# ── Phase 1 : le plan ───────────────────────────────────────────────────────────────────────────
if [ "${#parents_demandes[@]}" -gt 0 ]; then
  candidats=("${parents_demandes[@]}")
  printf '%sDécouverte%s — %s parent(s) nommé(s) en argument.\n' "$C_B" "$C_0" "${#candidats[@]}"
else
  printf '%sDécouverte%s — recherche « in:body "## Sous-tickets" » dans %s…\n' "$C_B" "$C_0" "$GL_GH_REPO"
  mapfile -t candidats < <(rc_candidats)
  if [ "${#candidats[@]}" = 0 ]; then
    printf '  %s✓%s aucun candidat — la section n%s plus nulle part.\n' "$C_G" "$C_0" "'existe"
    exit 0
  fi
  printf '  %s candidat(s) — un ticket qui PARLE de la section y répond comme un ticket qui en porte une.\n' \
    "${#candidats[@]}"
fi

parents=()
non_parents=0
illisibles=()
vides=()
items_vus=0
prose_parents=()
prose_lignes=0

for iid in "${candidats[@]}"; do
  corps="$TMP/$iid.md"
  if ! gl_get_description "$iid" >"$corps" 2>/dev/null; then
    illisibles+=("$iid")
    continue
  fi

  items="$(rc_items <"$corps")"
  if [ "$items" = 0 ]; then
    if [ "${#parents_demandes[@]}" -gt 0 ]; then
      printf "  %s⚠%s #%s ne porte aucune ligne de checklist — ignoré.\n" "$C_Y" "$C_0" "$iid"
    fi
    non_parents=$((non_parents + 1))
    continue
  fi

  rc_retire <"$corps" >"$TMP/$iid.neuf"
  # LA COMPARAISON EST L'IDEMPOTENCE, et elle porte sur les OCTETS : un corps qui ne bouge pas ne
  # s'écrit pas. Elle ne peut pas être vraie ici (on vient de compter des items à retirer), mais
  # c'est elle qui garde la propriété quand le contenu de la section évoluera.
  if cmp -s "$corps" "$TMP/$iid.neuf"; then
    printf '  %s✓%s #%-4s déjà sans checklist.\n' "$C_G" "$C_0" "$iid"
    continue
  fi

  # ON NE VIDE JAMAIS UNE DESCRIPTION. Un parent dont le corps n'est QUE sa checklist en ressortirait
  # blanc — le pire résultat que ce script puisse produire, et le seul qu'aucune relecture ne
  # rattraperait puisqu'il ne laisse rien à relire. Aucun des parents du dépôt n'est dans ce cas ; le
  # garde-fou coûte deux lignes et refuse au lieu de parier.
  if ! grep -q '[^[:space:]]' "$TMP/$iid.neuf"; then
    vides+=("$iid")
    printf '  %s⚠%s #%-4s la checklist EST toute la description — laissé en place.\n' "$C_Y" "$C_0" "$iid"
    continue
  fi

  parents+=("$iid")
  items_vus=$((items_vus + items))
  prose="$(rc_prose <"$corps")"
  if [ "$prose" -gt 0 ]; then
    prose_parents+=("$iid")
    prose_lignes=$((prose_lignes + prose))
    printf '  %s·%s #%-4s %2s item(s) retiré(s), %s ligne(s) de prose conservée(s) sous « Découpage ».\n' \
      "$C_B" "$C_0" "$iid" "$items" "$prose"
  else
    printf '  %s·%s #%-4s %2s item(s) retiré(s), section supprimée.\n' "$C_B" "$C_0" "$iid" "$items"
  fi
done

printf '\n%sPlan%s — %s parent(s) sur %s candidat(s), %s ligne(s) de checklist à retirer.\n' \
  "$C_B" "$C_0" "${#parents[@]}" "${#candidats[@]}" "$items_vus"
[ "$non_parents" -gt 0 ] && printf '  %s candidat(s) ne portent pas de checklist (mention de la section, pas un parent).\n' "$non_parents"
if [ "${#illisibles[@]}" -gt 0 ]; then
  printf '  %s⚠%s illisible(s) : %s\n' "$C_Y" "$C_0" "${illisibles[*]}"
fi

# rc_signalements -> ce qui demande un œil humain, dit une fois et de la même façon des deux côtés
# (`--check` et rapport final) : un signalement qui ne se lit que sur l'un des deux chemins est un
# signalement qu'on découvre trop tard (même raison qu'au backfill).
rc_signalements() {
  if [ "${#vides[@]}" -gt 0 ]; then
    printf '\n  %s⚠%s %s parent(s) dont la checklist est TOUTE la description : %s\n' \
      "$C_Y" "$C_0" "${#vides[@]}" "${vides[*]}"
    printf '      Laissés en place — retirer la liste les viderait. À reprendre à la main.\n'
  fi
  if [ "${#prose_parents[@]}" -gt 0 ]; then
    printf '\n  %s·%s %s parent(s) gardent de la prose sous « Découpage » (%s ligne(s)) : %s\n' \
      "$C_B" "$C_0" "${#prose_parents[@]}" "$prose_lignes" "${prose_parents[*]}"
    printf '      Conservée telle quelle et jamais relue — une partie a vieilli (« toutes cases\n'
    printf '      cochées » date d%savant #515). À élaguer au jugé, parent par parent.\n' "'"
  fi
}

if [ "$check" = 1 ]; then
  rc_signalements
  printf "\n--check : rien n'a été écrit.\n"
  [ "${#parents[@]}" -gt 0 ] && exit 3
  exit 0
fi

if [ "${#parents[@]}" = 0 ]; then
  rc_signalements
  { [ "${#illisibles[@]}" -gt 0 ] || [ "${#vides[@]}" -gt 0 ]; } && exit 1
  exit 0
fi

# ── Phase 2 : l'écriture ────────────────────────────────────────────────────────────────────────
printf '\n%sÉcriture%s\n' "$C_B" "$C_0"
retires=0
echecs=()

for iid in "${parents[@]}"; do
  if gl_set_description "$iid" "$TMP/$iid.neuf" >/dev/null 2>&1; then
    retires=$((retires + 1))
    printf '  %s✓%s #%s\n' "$C_G" "$C_0" "$iid"
  else
    echecs+=("#$iid")
    printf '  %s✗%s #%s — écriture refusée\n' "$C_R" "$C_0" "$iid"
  fi
done

# ── Rapport ─────────────────────────────────────────────────────────────────────────────────────
printf '\n%sBilan%s — %s parent(s) réécrit(s), %s ligne(s) de checklist retirée(s).\n' \
  "$C_B" "$C_0" "$retires" "$items_vus"

rc_signalements

if [ "${#echecs[@]}" -gt 0 ]; then
  printf '\n  %s✗%s %s écriture(s) en échec : %s\n' "$C_R" "$C_0" "${#echecs[@]}" "${echecs[*]}"
fi

if [ "${#echecs[@]}" -gt 0 ] || [ "${#illisibles[@]}" -gt 0 ] || [ "${#vides[@]}" -gt 0 ]; then
  exit 1
fi
exit 0
