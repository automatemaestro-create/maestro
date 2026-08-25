#!/usr/bin/env bash
# Les écrans qu'un ticket a touchés, DÉRIVÉS DES COMMITS (#544, lot 1 de #543).
#
#   bash scripts/presentation/ecrans-touches.sh <iid…>
#   bash scripts/presentation/ecrans-touches.sh --check 474 528     # + le diagnostic sur stderr
#   bash scripts/presentation/ecrans-touches.sh --ref main 474
#
# `/milestone-presentation` (#142) photographie TOUTES les pages du menu, dans leur état du jour,
# et c'est l'agent qui devine au moment de rédiger quelle capture illustre quel ticket — l'étape 5
# de son prompt lui dit d'ailleurs de laisser `null` au moindre doute. La matière existe pourtant :
# le hook `commit-msg` impose `Refs #<iid>` / `Closes #<iid>` sur tout commit, et les écrans vivent
# sous `apps/web/app/<route>/`. Ce script rend donc l'appariement ticket ↔ écran au lieu de le
# parier, et les lots suivants de #543 l'affichent.
#
# Format de sortie (TSV sur stdout, en-tête préfixée « # » ignorable par les consommateurs
# machine — même convention que `scripts/orchestrate/queue.sh`) :
#
#     iid <TAB> route <TAB> cle <TAB> fichiers
#
# `fichiers` est la liste des chemins (relatifs à la racine du dépôt) qui ont produit la ligne,
# séparés par des virgules, dédoublonnés et triés — champ de longueur variable, donc en dernier.
# Une ligne par (ticket, route) : un ticket qui a touché trois écrans rend trois lignes.
#
# --- Ce que le script sait, et ce qu'il ne sait pas ------------------------------------------------
#
# 1. LA ROUTE VIENT DU DOSSIER. `apps/web/app/couts/page.tsx` → `/couts` ; `apps/web/app/page.tsx`
#    → `/`. Un segment DYNAMIQUE tronque la route (`apps/web/app/runs/[runId]/page.tsx` → `/runs`,
#    `apps/web/app/agents/[nom]/[onglet]/page.tsx` → `/agents`) : une page à segment dynamique n'a
#    pas d'entrée à elle, elle vit SOUS celle de sa liste — c'est déjà ce que dit `entreeCourante`
#    dans `apps/web/lib/navigation.ts`, et c'est aussi la seule route pour laquelle une capture
#    existe. Un groupe de routes (`(nom)`) est au contraire TRANSPARENT dans l'URL : il est sauté,
#    pas tronqué.
#
# 2. `cle` EST CELLE DU MANIFESTE DE CAPTURES, dérivée exactement comme `cleDeRoute` de
#    `captures.mjs` : bornes retirées, tout ce qui n'est ni lettre, ni chiffre, ni tiret ramené à un
#    tiret, `/` valant `accueil`. C'est par elle que le lot 3 retrouvera l'image d'un écran. Une
#    route SERVIE MAIS HORS MENU (`/projets`, #280) rend donc une clé pour laquelle le manifeste
#    n'a aucune capture — c'est un fait sur la présentation, pas une erreur ici : le script dit
#    l'écran touché, il ne promet pas qu'il ait été photographié.
#
# 3. CE QUI N'A PAS DE ROUTE EST COMPTÉ À PART ET NOMMÉ, `route` = `-` et `cle` = `-` :
#    - `apps/web/components/**`, le composant partagé — il touche potentiellement plusieurs écrans
#      sans qu'aucune route ne le dise (limite nommée dans #543) ;
#    - les fichiers à la RACINE de `apps/web/app/` autres que `page.tsx` — `layout.tsx`,
#      `globals.css`, les icônes : ce sont la coquille de TOUS les écrans, pas l'écran d'accueil.
#      Un `layout.tsx` IMBRIQUÉ, lui, est bien celui de sa route et compte pour elle.
#    Les rattacher à une route serait les rattacher à une route au hasard.
#
# 4. UN TICKET SANS SURFACE VISIBLE REND ZÉRO LIGNE, et c'est un résultat : moteur, CI, doc,
#    outillage n'ont pas d'écran, et le code de retour reste `0`. Un ticket qui n'a touché QUE des
#    composants partagés rend, lui, UNE ligne `-` : il a une surface visible, c'est son écran qui
#    est indéterminé — le taire reviendrait à dire « ce ticket n'a rien changé à l'écran », ce qui
#    est faux. C'est l'arbitrage entre les deux moitiés du critère : l'absence est muette, l'inconnu
#    est nommé.
#
# 5. HORS PÉRIMÈTRE, à dessein : `apps/web/lib/**` et `apps/web/hooks/**` ne sont pas comptés. Ce
#    sont de la plomberie, pas une surface — et les compter ferait rendre une ligne `-` à presque
#    tous les tickets de la Control Tower, ce qui n'apprendrait plus rien.
#
# --- Comment les commits sont trouvés --------------------------------------------------------------
#
# `git log --grep` sur `(Refs|Closes) #<iid>` suivi d'une NON-CHIFFRE : sans cette borne, `#5`
# hériterait des écrans de `#54`. Le mot-clé est OBLIGATOIRE dans le motif, et pas seulement pour la
# forme : GitHub suffixe le sujet d'un squash du NUMÉRO DE LA PR (`… (#542)`), qui n'est pas l'iid
# du ticket (celui-là est dans le `Closes #528` du corps) — un motif sur `#<iid>` seul apparierait
# les tickets aux PR des autres.
#
# On ne parie pas sur l'unicité du commit : le projet merge en squash, donc un ticket en a le plus
# souvent UN seul sur `main`, mais la reprise d'un ticket peut en laisser plusieurs. Ni sur son
# ancienneté : aucune fenêtre de dates n'est appliquée, un ticket d'un milestone pouvant avoir été
# commité avant l'ouverture de la phase.
#
# Les commits de FUSION sont lus contre leur PREMIER PARENT (`-m --first-parent`) : l'histoire
# d'avant la migration GitHub (#335) est faite de vraies fusions, dont la description porte le
# `Closes #<iid>` alors que `git show` n'en rend, par défaut, aucun fichier.
#
# --- Lecture seule et hors réseau -----------------------------------------------------------------
#
# Aucune écriture (pas même un fichier temporaire), aucun appel à la forge : le script vit ici avec
# les trois autres étapes de la présentation, et non dans `scripts/gitlab/lib.sh`, parce qu'il ne
# parle pas à GitHub — il lit `git log`.

set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

CHECK=0
REF=""
IIDS=()

usage() {
  cat <<'USAGE'
Les écrans qu'un ticket a touchés, dérivés des commits.

  bash scripts/presentation/ecrans-touches.sh [options] <iid…>

Options :
  --ref <ref>   Branche ou révision à lire. Par défaut, la première qui existe parmi
                `origin/main`, `main`, `HEAD` — `origin/main` d'abord parce qu'un `main` local
                peut être en retard, et que c'est une ref LOCALE : aucun appel réseau.
  --check       Affiche aussi, sur stderr, le diagnostic : ref retenue, et les tickets dont
                aucun commit ne porte `Refs #<iid>` / `Closes #<iid>` sur cette ref (pas
                encore mergés, ou d'un autre dépôt) — à distinguer d'un ticket sans écran.
  -h, --help    Cette aide.

Sortie (stdout, TSV) : iid, route, cle, fichiers. Lecture seule, hors réseau, code de retour 0
même quand le résultat est vide.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="${2:-}"; shift ;;
    --check) CHECK=1 ;;
    -h | --help) usage; exit 0 ;;
    -*) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    *)
      brut="${1#\#}"
      case "$brut" in
        '' | *[!0-9]*)
          printf 'ecrans-touches.sh : « %s » n'\''est pas un iid de ticket.\n\n' "$1" >&2
          usage >&2; exit 2 ;;
      esac
      # Dédoublonnage à la volée : un iid donné deux fois ne rend pas deux fois ses lignes.
      deja=0
      for connu in ${IIDS[@]+"${IIDS[@]}"}; do
        [ "$connu" = "$brut" ] && deja=1
      done
      [ "$deja" = 0 ] && IIDS+=("$brut")
      ;;
  esac
  shift
done

diag() { [ "$CHECK" = 1 ] && printf 'ecrans-touches.sh : %s\n' "$*" >&2; return 0; }

if [ "${#IIDS[@]}" -eq 0 ]; then
  printf 'ecrans-touches.sh : au moins un iid de ticket est attendu.\n\n' >&2
  usage >&2
  exit 2
fi

if [ -n "$REF" ]; then
  if ! git -C "$RACINE" rev-parse --verify --quiet "${REF}^{commit}" >/dev/null; then
    printf 'ecrans-touches.sh : révision introuvable : %s\n' "$REF" >&2
    exit 2
  fi
else
  for candidate in origin/main main HEAD; do
    if git -C "$RACINE" rev-parse --verify --quiet "${candidate}^{commit}" >/dev/null; then
      REF="$candidate"
      break
    fi
  done
  if [ -z "$REF" ]; then
    printf 'ecrans-touches.sh : aucune révision lisible (origin/main, main, HEAD).\n' >&2
    exit 2
  fi
fi
diag "ref lue : $REF"

# Le classement d'un chemin en (route, clé), et l'agrégation par route. En awk plutôt qu'en shell :
# c'est une passe par fichier touché, et la dédup + le regroupement y tiennent sans fichier de
# travail. `IID` arrive par -v — c'est une suite de chiffres, déjà validée, donc hors de portée du
# piège des échappements de -v (#340).
AWK_ROUTES='
function cle_de(route,   s) {
  # La dérivation de cleDeRoute (captures.mjs), à la lettre.
  s = route
  gsub(/^\/+|\/+$/, "", s)
  gsub(/[^a-zA-Z0-9-]+/, "-", s)
  return (s == "") ? "accueil" : s
}
function ajoute(route, chemin) {
  if (route in fichiers) { fichiers[route] = fichiers[route] "," chemin; return }
  routes[++n] = route
  fichiers[route] = chemin
}
BEGIN { APP = "apps/web/app/"; PARTAGE = "apps/web/components/" }
{
  chemin = $0
  if (chemin == "") next
  if (substr(chemin, 1, length(PARTAGE)) == PARTAGE) { ajoute("-", chemin); next }
  if (substr(chemin, 1, length(APP)) != APP) next

  reste = substr(chemin, length(APP) + 1)
  nb = split(reste, seg, "/")
  if (nb == 1) {
    # Racine de app/ : seul page.tsx est un écran (/) ; le reste est la coquille de tous.
    ajoute((seg[1] ~ /^page\.(tsx|ts|jsx|js|mdx)$/) ? "/" : "-", chemin)
    next
  }
  route = ""
  for (i = 1; i < nb; i++) {
    s = seg[i]
    if (s ~ /^\(.*\)$/) continue   # groupe de routes : transparent dans une URL
    if (s ~ /^[\[@]/) break        # segment dynamique : la page vit sous celle de sa liste
    route = route "/" s
  }
  if (route == "") route = "/"
  ajoute(route, chemin)
}
END {
  # Première colonne = clé de tri (0 pour une route, 1 pour le reste), retirée juste après par cut.
  for (i = 1; i <= n; i++) {
    r = routes[i]
    printf "%s\t%s\t%s\t%s\t%s\n", (r == "-" ? 1 : 0), IID, r, (r == "-" ? "-" : cle_de(r)), fichiers[r]
  }
}
'

printf '# iid\troute\tcle\tfichiers\n'

for iid in "${IIDS[@]}"; do
  # Le mot-clé fait partie du motif (voir l en-tête) ; la borne « non-chiffre ou fin » distingue
  # #5 de #54.
  motif='(Refs|Closes) #'"$iid"'([^0-9]|$)'
  shas="$(git -C "$RACINE" log "$REF" --extended-regexp --grep="$motif" --format=%H)"
  if [ -z "$shas" ]; then
    diag "#$iid — aucun commit sur $REF"
    continue
  fi
  mapfile -t commits <<<"$shas"
  diag "#$iid — ${#commits[@]} commit(s)"

  # `--no-renames` pour qu'un écran DÉPLACÉ compte pour ses deux routes (la détection de renommage
  # ne rendrait que la nouvelle). `core.quotepath=false` pour qu'un chemin accentué sorte en UTF-8
  # plutôt qu'en octets échappés — un -c ne touche à aucune configuration du dépôt.
  git -C "$RACINE" -c core.quotepath=false show \
      --format= --name-only --no-renames -m --first-parent "${commits[@]}" \
    | LC_ALL=C sort -u \
    | awk -v IID="$iid" "$AWK_ROUTES" \
    | LC_ALL=C sort -t"$(printf '\t')" -k1,1 -k3,3 \
    | cut -f2-
done

# Un résultat vide est un résultat : le code de retour ne dit pas « pas d'écran », il dit
# « la question a été posée ».
exit 0
