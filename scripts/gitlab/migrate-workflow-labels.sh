#!/usr/bin/env bash
# migrate-workflow-labels.sh — pose un (et un seul) label `workflow::*` sur CHAQUE ticket du
# projet. One-shot : c'est le rattrapage du passage du champ Status natif aux labels (#207, lot
# #208). Une fois joué, ce sont les commandes /ticket-* qui entretiennent le label.
#
# ⚠ La donnée d'origine est PERDUE (le champ Status a disparu avec l'essai Ultimate, cf. #207) :
# l'état de chaque ticket est DÉDUIT, il n'est pas restauré. Les déductions sont annoncées ligne
# par ligne, et `--check` permet de les relire avant d'écrire quoi que ce soit.
#
# Règles de déduction, par priorité décroissante :
#   1. ticket FERMÉ                                          → workflow::termine
#   2. ticket ouvert avec une MR OUVERTE rattachée           → workflow::en-revue
#   3. ticket ouvert, ASSIGNÉ, avec une BRANCHE `<type>/<iid>-…` sur le serveur → workflow::en-cours
#   4. sinon                                                 → workflow::a-faire
#
# Ce que le script ne fait pas : il ne ferme ni ne rouvre aucun ticket, ne touche à aucun autre
# label (`type::`/`agent::`/`prio::` sont laissés tels quels) et ne devine ni « abandonné » ni
# « doublon » — un ticket fermé pour l'une de ces raisons ressort en `termine` et se corrige à la
# main (ils sont peu nombreux, et rien dans l'API ne permet de les distinguer).
#
# IDEMPOTENT malgré le « one-shot » : rejouer ne réécrit que ce qui a dérivé.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$here/lib.sh"

usage() {
  cat <<'USAGE'
Migration one-shot : pose un label workflow:: sur chaque ticket du projet (#207).

  bash scripts/gitlab/migrate-workflow-labels.sh --check   # déductions seules, aucune écriture
  bash scripts/gitlab/migrate-workflow-labels.sh           # applique

Déduction : fermé → termine · MR ouverte → en-revue · assigné + branche → en-cours · sinon a-faire.
USAGE
}

check=0
case "${1:-}" in
  --check) check=1 ;;
  -h | --help) usage; exit 0 ;;
  "") ;;
  *) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
esac

gl_require_glab || exit 1

enc="$(gl_project_enc)"

# --- Le vocabulaire, lu dans GitLab -------------------------------------------------------------
# Les 6 labels sont la source de vérité pour « quels workflow:: retirer » : c'est bootstrap.sh qui
# les crée, ce script n'en réinvente pas la liste. Les 4 CIBLES, elles, sont nommées ici parce que
# les règles de déduction s'y réfèrent directement.
CIBLE_TERMINE="workflow::termine"
CIBLE_REVUE="workflow::en-revue"
CIBLE_COURS="workflow::en-cours"
CIBLE_AFAIRE="workflow::a-faire"

vocabulaire="$(glab api "projects/$enc/labels?per_page=100" --paginate --output ndjson 2>/dev/null \
  | grep -o '"name":"workflow::[^"]*"' | sed 's/.*"name":"//; s/"$//' | sort -u)"
for cible in "$CIBLE_TERMINE" "$CIBLE_REVUE" "$CIBLE_COURS" "$CIBLE_AFAIRE"; do
  if ! printf '%s\n' "$vocabulaire" | grep -qx -- "$cible"; then
    echo "Label « $cible » absent du projet — lancer d'abord : bash scripts/gitlab/bootstrap.sh" >&2
    exit 1
  fi
done

# --- Les faits : MR ouvertes, branches du serveur, tickets --------------------------------------
# Le rattachement MR ↔ ticket se lit dans le NOM DE BRANCHE (`<type>/<iid>-…`), comme partout
# ailleurs dans l'outillage (gl_branch_iid) : c'est la convention du dépôt, et ça tient en une
# lecture au lieu d'une par ticket.
# Délimiteur « # » et non « , » : le quantificateur `\{1,\}` en contient un, qui refermerait
# l'expression sed au milieu du motif (« unknown option to `s' », silencieux sur le résultat).
iid_de_branche() { # <branche> -> iid, vide si la branche ne suit pas la convention
  printf '%s\n' "$1" | sed -n 's#^[a-z]\{1,\}/\([0-9]\{1,\}\)-.*#\1#p'
}

echo "Lecture de $GL_PROJECT…"

mr_branches="$(gl_graphql_read '{ project(fullPath:"'"$GL_PROJECT"'") { mergeRequests(state: opened, first: 100) { nodes { sourceBranch } } } }' \
  | grep -o '"sourceBranch":"[^"]*"' | sed 's/.*"sourceBranch":"//; s/"$//')"
iids_en_revue=""
while IFS= read -r br; do
  [ -n "$br" ] || continue
  iid="$(iid_de_branche "$br")"
  [ -n "$iid" ] && iids_en_revue="$iids_en_revue $iid"
done <<EOF
$mr_branches
EOF

branches="$(glab api "projects/$enc/repository/branches?per_page=100" --paginate --output ndjson 2>/dev/null \
  | grep -o '^{"name":"[^"]*"' | sed 's/^{"name":"//; s/"$//')"
iids_avec_branche=""
while IFS= read -r br; do
  [ -n "$br" ] || continue
  iid="$(iid_de_branche "$br")"
  [ -n "$iid" ] && iids_avec_branche="$iids_avec_branche $iid"
done <<EOF
$branches
EOF

# Tous les tickets, ouverts ET fermés, avec juste ce qu'il faut pour décider. GraphQL plutôt que
# REST : la réponse ne porte que les 4 champs demandés là où `/issues` rend plusieurs Ko par ticket
# (jalon, auteur, liens…) qu'il faudrait ensuite démêler.
tickets="$(glab api graphql --paginate \
  -f query='query($endCursor: String) { project(fullPath:"'"$GL_PROJECT"'") { issues(first: 100, state: all, after: $endCursor) { pageInfo { hasNextPage endCursor } nodes { iid state labels { nodes { title } } assignees { nodes { username } } } } } }' 2>/dev/null \
  | tr -d '\n\r' | awk '
    {
      n = split($0, parts, /\{"iid":"/)
      for (i = 2; i <= n; i++) {
        node = parts[i]
        match(node, /^[0-9]+/); iid = substr(node, RSTART, RLENGTH)

        etat = "?"
        if (match(node, /"state":"[a-z]+"/)) etat = substr(node, RSTART + 9, RLENGTH - 10)

        # Un seul workflow:: est attendu, mais on les collecte TOUS : sur le plan Free, deux
        # labels du même scope peuvent coexister sur un ticket — le nettoyage en dépend.
        wf = "-"
        if (match(node, /"labels":\{"nodes":\[[^]]*\]/)) {
          bloc = substr(node, RSTART, RLENGTH)
          k = split(bloc, t, /"title":"/)
          for (j = 2; j <= k; j++) {
            lab = t[j]; sub(/".*/, "", lab)
            if (lab ~ /^workflow::/) wf = (wf == "-" ? lab : wf "," lab)
          }
        }

        qui = "-"
        if (match(node, /"assignees":\{"nodes":\[[^]]*\]/)) {
          bloc = substr(node, RSTART, RLENGTH)
          k = split(bloc, t, /"username":"/)
          for (j = 2; j <= k; j++) {
            u = t[j]; sub(/".*/, "", u)
            qui = (qui == "-" ? u : qui "," u)
          }
        }

        printf "%s\t%s\t%s\t%s\n", iid, etat, qui, wf
      }
    }
  ')"

nb_tickets="$(printf '%s\n' "$tickets" | grep -c '^[0-9]')"
if [ "$nb_tickets" = 0 ]; then
  echo "Aucun ticket lu — API muette ou projet vide. Rien fait." >&2
  exit 1
fi
printf '%s ticket(s), %s branche(s), %s MR ouverte(s)\n\n' \
  "$nb_tickets" "$(printf '%s\n' "$branches" | grep -c .)" "$(printf '%s\n' "$mr_branches" | grep -c .)"

# --- Déduction + application --------------------------------------------------------------------
contient() { # <liste-espacée> <valeur>
  case " $1 " in *" $2 "*) return 0 ;; esac
  return 1
}

# Retire de la liste des workflow:: portés par le ticket tous ceux qui ne sont pas la cible.
# C'est ici que se tient l'invariant « exactement un » : GitLab ne l'assure pas sur Free.
a_retirer() { # <workflow-actuels-csv> <cible> -> csv des labels à retirer
  local actuels="$1" cible="$2" out="" lab
  [ "$actuels" = "-" ] && return 0
  for lab in ${actuels//,/ }; do
    [ "$lab" = "$cible" ] && continue
    out="$out${out:+,}$lab"
  done
  printf '%s' "$out"
}

# Une écriture, avec RÉESSAI. Une migration à 200 tickets tape l'API bien plus vite que le reste du
# workflow, et GitLab.com finit par refuser en rafale : le premier jet de ce script a écrit 120
# tickets puis échoué sur les 89 suivants d'affilée, alors que le même appel rejoué à la main
# passait. Un échec définitif garde le message de l'API — l'avaler laisserait croire à un ticket
# récalcitrant là où c'est le rythme qui est en cause.
MAX_TENTATIVES="${MAESTRO_MIGRATION_TENTATIVES:-4}"
ERREUR=""
maj_ticket() { # <iid> <label-à-poser> <labels-à-retirer-csv> -> 0 ok / 1 échec (pose ERREUR)
  local iid="$1" ajout="$2" retrait="$3" tentative=1 pause=5 out
  ERREUR=""
  while :; do
    set -- --method PUT "projects/$enc/issues/$iid" -f "add_labels=$ajout"
    [ -n "$retrait" ] && set -- "$@" -f "remove_labels=$retrait"
    out="$(glab api "$@" 2>&1)"
    # La réponse d'un PUT réussi est le ticket lui-même : son `iid` en est la signature.
    case "$out" in *"\"iid\":$iid,"*) return 0 ;; esac
    if [ "$tentative" -ge "$MAX_TENTATIVES" ]; then
      ERREUR="$(printf '%s' "$out" | tr -d '\r\n' | cut -c1-140)"
      return 1
    fi
    sleep "$pause"
    tentative=$((tentative + 1))
    pause=$((pause * 3))
  done
}

nb_change=0
nb_inchange=0
nb_echec=0
declare -A compte

while IFS=$'\t' read -r iid etat qui wf; do
  [ -n "$iid" ] || continue

  if [ "$etat" = "closed" ]; then
    cible="$CIBLE_TERMINE"; motif="ticket fermé"
  elif contient "$iids_en_revue" "$iid"; then
    cible="$CIBLE_REVUE"; motif="MR ouverte rattachée"
  elif [ "$qui" != "-" ] && contient "$iids_avec_branche" "$iid"; then
    cible="$CIBLE_COURS"; motif="assigné à $qui, branche présente"
  else
    # `a-faire` est le cas par défaut : il attrape aussi bien le ticket que personne n'a ouvert que
    # celui à qui il ne manque qu'une moitié de la règle 3. Le motif dit LAQUELLE — c'est là que se
    # relit une déduction douteuse.
    cible="$CIBLE_AFAIRE"
    if [ "$qui" != "-" ]; then
      motif="ouvert, assigné à $qui, pas de branche"
    elif contient "$iids_avec_branche" "$iid"; then
      motif="ouvert, branche présente mais personne d'assigné"
    else
      motif="ouvert, ni MR ni branche"
    fi
  fi
  compte[$cible]=$(( ${compte[$cible]:-0} + 1 ))

  retirer="$(a_retirer "$wf" "$cible")"
  if [ "$wf" = "$cible" ]; then
    nb_inchange=$((nb_inchange + 1))
    continue
  fi

  detail="→ ${cible#workflow::}"
  [ -n "$retirer" ] && detail="$detail (retire ${retirer//workflow::/})"
  if [ "$check" = 1 ]; then
    printf '  [check] #%-4s %-34s %s\n' "$iid" "$detail" "$motif"
    nb_change=$((nb_change + 1))
    continue
  fi

  # maj_ticket pose add_labels/remove_labels et NON `labels`, qui remplacerait la liste entière et
  # effacerait type::/agent::/prio:: au passage.
  if maj_ticket "$iid" "$cible" "$retirer"; then
    printf '  ✓ #%-4s %-34s %s\n' "$iid" "$detail" "$motif"
    nb_change=$((nb_change + 1))
  else
    printf '  ✗ #%-4s %-34s ÉCHEC après %s tentative(s) : %s\n' "$iid" "$detail" "$MAX_TENTATIVES" "$ERREUR" >&2
    nb_echec=$((nb_echec + 1))
  fi
done <<EOF
$tickets
EOF

# --- Bilan ---------------------------------------------------------------------------------------
echo
echo "Répartition déduite :"
for cible in "$CIBLE_AFAIRE" "$CIBLE_COURS" "$CIBLE_REVUE" "$CIBLE_TERMINE"; do
  printf '  %-22s %s\n' "$cible" "${compte[$cible]:-0}"
done
echo
if [ "$check" = 1 ]; then
  printf "%s ticket(s) à mettre à jour, %s déjà à jour — diagnostic seul, rien n'a été écrit.\n" \
    "$nb_change" "$nb_inchange"
  echo "Appliquer : bash scripts/gitlab/migrate-workflow-labels.sh"
  exit 0
fi
printf '%s ticket(s) mis à jour, %s déjà à jour, %s échec(s).\n' "$nb_change" "$nb_inchange" "$nb_echec"
echo "Rappel : ce sont des DÉDUCTIONS, pas une restauration — relire les cas douteux dans le board."
[ "$nb_echec" -eq 0 ] || exit 1
