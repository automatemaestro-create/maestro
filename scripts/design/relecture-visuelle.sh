#!/usr/bin/env bash
# LA RELECTURE VISUELLE — ce qu'il faut regarder, et de quoi le regarder (#932, lot 2 de #930).
#
#   bash scripts/design/relecture-visuelle.sh --plan <iid>   # ce qu'il y a à regarder. Ne démarre rien.
#   bash scripts/design/relecture-visuelle.sh <iid>          # le plan, + la stack montée, prête à regarder
#   bash scripts/design/relecture-visuelle.sh --fin          # arrête la stack et retire ce qu'elle a posé
#
# Le maillon qui manquait à la chaîne de docs/30 §5.1 : PERSONNE NE REGARDE LE RENDU. `verify` répond
# du câblage, `banc-mise-en-page` de la géométrie, `contraste.test.ts` / `a11y.test.tsx` /
# `sobriete.test.tsx` des règles — aucun ne dit « à quoi ça ressemble ». Une session peut écrire une
# interface, voir tous ses tests verts, et n'avoir jamais ouvert l'écran qu'elle vient de changer.
#
# CE SCRIPT NE REGARDE PAS. Il prépare ce qui est mécanique — quels écrans, sur quels ports, où déposer
# les captures — et s'arrête là : le jugement demande des yeux, et c'est la session qui les a (skill
# `relecture-visuelle`). Le partage est celui de #562, #612 et #714 : ce qui est automatique est la
# DÉSIGNATION de ce qui manque, jamais le verdict.
#
# --- 1. QUELS ÉCRANS : trois questions, une seule règle de classement ------------------------------
#
# `scripts/presentation/ecrans-touches.sh` (#544) sait déjà dire à quel écran appartient un fichier, et
# c'est SA règle qui répond ici — recopiée, elle finirait par ne plus nommer le même écran pour le même
# fichier. Ce script lui pose les trois questions dans l'ordre :
#
#   a. LES COMMITS ET L'ARBRE (`--ref HEAD --travail-en-cours`). La relecture a lieu AVANT la clôture :
#      le ticket n'a souvent aucun commit, jamais de merge, et `origin/main` ne sait rien de lui. On lit
#      donc la branche ET ce que l'arbre a de plus qu'elle.
#   b. LES ÉCRANS QUI AFFICHENT UN COMPOSANT PARTAGÉ. #544 range `apps/web/components/**` sous une ligne
#      « indéterminée », et il a raison : un composant n'EST aucune route. Mais on peut remonter à celles
#      qui le MONTRENT, en suivant les imports — question différente, donc pas un doublon de #544. Sans
#      cette remonte, le geste serait muet sur une bonne part des tickets d'interface : `components/` est
#      l'endroit le plus édité de `apps/web`.
#   c. CE QUI RESTE INDÉTERMINÉ EST NOMMÉ, jamais deviné (règle de #544) : la coquille de tous les écrans
#      (`app/layout.tsx`, `globals.css`) n'appartient à aucun, et un composant que personne n'importe
#      encore ne s'affiche nulle part. La session le relaie dans son jugement, à « ce que je n'ai pas pu
#      voir » — c'est une réponse, pas un trou.
#
# ⚠ LA REMONTE PAR LES IMPORTS EST UNE APPROXIMATION, et il faut que ça se voie. Elle apparie sur le NOM
# DE MODULE (`from "…/Composeur"`), donc deux composants homonymes dans deux dossiers se confondent, et
# un import construit à l'exécution lui échappe. Les deux erreurs ne coûtent pas la même chose : un écran
# de trop se regarde en vingt secondes, un écran manquant ne se regarde jamais. On ratisse donc large, et
# le plan dit toujours PAR QUEL FICHIER un écran est arrivé.
#
# --- 2. SUR QUELS PORTS : le fichier du worktree, jamais l'environnement ---------------------------
#
# Une session relocalisée par `/ticket-start` garde les ports du CLONE PRINCIPAL dans son bloc `env`
# (docs/10 §9.1 — `EnterWorktree` ne réévalue que les caches liés au CWD). L'environnement ment donc
# précisément là où ce script sert, et viser 8000/3000 arrêterait la stack d'à côté. La source de vérité
# est `.claude/settings.local.json` DU DÉPÔT COURANT, que `worktree.sh` écrit au montage ; l'environnement
# n'est qu'un repli, et 8000/3000 le repli du repli (clone principal, poste sans worktree).
#
# --- 3. CE QUE LE SCRIPT ÉCRIT ---------------------------------------------------------------------
#
#   - `.maestro/relecture/<iid>/` — les captures et le jugement. Sous la racine du dépôt et en chemin
#     RELATIF : un chemin absolu hors du répertoire de travail demande une approbation qu'une session
#     autonome n'a personne pour donner (#234, docs/10 §11.7). Le `filename` du MCP se donne relatif
#     lui aussi, et c'est mesuré : sa racine autorisée EST le worktree de la session, mais son contrôle
#     compare les chemins LITTÉRALEMENT — un `e:/…` (la forme qu'on obtient en convertissant la racine
#     MSYS) est refusé « outside allowed roots » face à un `E:/…` pourtant identique. Le relatif rend la
#     question sans objet plutôt que de la traiter une fois de plus.
#   - `core/projets/<PROJET_DEMO>.json` — sans projet actif, le shell ne rend que sa porte d'entrée
#     (#279) et il n'y a rien à regarder. Le fichier est gitignoré (`core/projets/.gitignore`), son
#     identifiant est LU dans `maestro/controltower/demo.py` plutôt que recopié, et il n'est retiré que
#     si c'est nous qui l'avons posé — un projet déclaré avant nous ne nous appartient pas.
#
# Il ne parle à aucune forge, ne commite rien, ne merge rien : il monte deux processus locaux sur les
# ports d'un worktree, et les arrête.
#
# --- 4. LE PRIX EST DU TEMPS DE MUR, ET IL S'ANNONCE (règle de #418) -------------------------------
#
# Monter la stack coûte, et un run à concurrence 3 en monterait trois — sur des ports distincts, ce que
# `worktree.sh` garantit depuis #152. C'est pourquoi `--plan` existe SÉPARÉMENT et ne démarre rien : un
# ticket sans surface visible rend `3` en une seconde, et personne ne paie la stack pour apprendre qu'il
# n'y avait rien à regarder.
#
# Codes de retour : 0 = il y a à regarder · 3 = aucune surface visible (abstention nominale, pas une
# panne) · 1 = échec · 2 = usage.

set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOUS_DOSSIER=".maestro/relecture"

MODE="preparer"
TSV=0
IID=""

usage() {
  cat <<'USAGE'
La relecture visuelle : ce qu'il faut regarder, et de quoi le regarder.

  bash scripts/design/relecture-visuelle.sh --plan <iid>   Ce qu'il y a à regarder. Ne démarre rien.
  bash scripts/design/relecture-visuelle.sh <iid>          Le plan, puis la stack montée et prête.
  bash scripts/design/relecture-visuelle.sh --fin          Arrête la stack et retire ce qu'elle a posé.

Options :
  --plan        N'écrit rien, ne démarre rien : dit seulement s'il y a matière, et laquelle.
  --tsv         Le plan en TSV (route, url, origine, fichiers), pour un appelant machine.
  -h, --help    Cette aide.

Codes de retour : 0 = il y a à regarder · 3 = aucune surface visible · 1 = échec · 2 = usage.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --plan) MODE="plan" ;;
    --fin) MODE="fin" ;;
    --tsv) TSV=1 ;;
    -h | --help) usage; exit 0 ;;
    -*) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    *)
      brut="${1#\#}"
      case "$brut" in
        '' | *[!0-9]*)
          printf 'relecture-visuelle.sh : « %s » n'\''est pas un iid de ticket.\n\n' "$1" >&2
          usage >&2; exit 2 ;;
      esac
      IID="$brut" ;;
  esac
  shift
done

dire() { [ "$TSV" = 1 ] || printf '%s\n' "$*"; }


# --- Les ports du dépôt courant ---------------------------------------------------------------------
# Lecture d'un JSON à deux niveaux en sed : le fichier est écrit par `worktree.sh` (json.dump indenté),
# donc une clé par ligne. `jq` serait plus juste et n'est pas un prérequis du dépôt ; Python le serait
# aussi et coûterait un démarrage d'interpréteur pour deux entiers.
port_de() {
  local cle="$1" defaut="$2" valeur="" fichier="$RACINE/.claude/settings.local.json"
  if [ -f "$fichier" ]; then
    valeur="$(sed -n 's/.*"'"$cle"'"[[:space:]]*:[[:space:]]*"\{0,1\}\([0-9]\{2,5\}\)"\{0,1\}.*/\1/p' \
      "$fichier" | head -n 1)"
  fi
  # L'environnement N'EST QU'UN REPLI (voir l'en-tête) : dans une session relocalisée il porte les ports
  # du clone principal, c'est-à-dire ceux de la stack de quelqu'un d'autre.
  if [ -z "$valeur" ]; then valeur="${!cle:-}"; fi
  if [ -z "$valeur" ]; then valeur="$defaut"; fi
  printf '%s' "$valeur"
}

PORT_API="$(port_de MAESTRO_PORT_API 8000)"
PORT_UI="$(port_de MAESTRO_PORT_UI 3000)"

# --- Le projet de la démo -----------------------------------------------------------------------------
# L'identifiant est LU dans le scénario : le jour où la démo change de projet, le script suit.
PROJET_DEMO="$(sed -n 's/^PROJET_ID *= *"\([^"]*\)".*/\1/p' "$RACINE/maestro/controltower/demo.py" | head -n 1)"
PROJET_DEMO="${PROJET_DEMO:-prj-demo}"
PROJET_FICHIER="$RACINE/core/projets/$PROJET_DEMO.json"
TEMOIN_PROJET="$RACINE/$SOUS_DOSSIER/.projet-pose"

# Le dossier que l'écran « Projets » affichera. Il ne sert qu'à ça : rien n'y est écrit, et la validation
# des racines (`maestro/projets/racine.py`) n'est pas rejouée à la LECTURE du dépôt de projets — c'est
# déjà ce dont `captures.sh` se sert pour poser un identifiant fixe. Forme à slashes, et le temporaire
# WINDOWS de préférence à `/tmp` : un `/tmp` de Git Bash n'est pas le même chemin pour le Python du venv.
base_temporaire() {
  local base="${TMPDIR:-${TEMP:-/tmp}}"
  printf '%s' "${base//\\//}"
}

pose_projet() {
  if [ -f "$PROJET_FICHIER" ]; then
    dire "  projet     : « $PROJET_DEMO » déjà déclaré — laissé en place"
    return 0
  fi
  local racine_fictive
  racine_fictive="$(base_temporaire)/maestro-relecture/mini-crm"
  mkdir -p "$racine_fictive" 2>/dev/null
  mkdir -p "$(dirname "$PROJET_FICHIER")" 2>/dev/null
  if ! printf '{"id":"%s","nom":"mini-CRM (démo)","racine":"%s","origine":"existant","vcs":null}\n' \
    "$PROJET_DEMO" "$racine_fictive" >"$PROJET_FICHIER"; then
    dire "  ⚠ projet    : déclaration impossible — les écrans rendront la porte d'entrée"
    return 1
  fi
  mkdir -p "$(dirname "$TEMOIN_PROJET")" 2>/dev/null
  printf '%s\n' "$PROJET_FICHIER" >"$TEMOIN_PROJET"
  dire "  projet     : « $PROJET_DEMO » déclaré (retiré par --fin)"
}

retire_projet() {
  [ -f "$TEMOIN_PROJET" ] || return 0
  local cible
  cible="$(head -n 1 "$TEMOIN_PROJET")"
  if [ -n "$cible" ] && [ -f "$cible" ]; then
    rm -f "$cible" && printf '  ✓ projet « %s » retiré\n' "$PROJET_DEMO"
  fi
  rm -f "$TEMOIN_PROJET"
}

# --- Les écrans ---------------------------------------------------------------------------------------
ECRANS_TOUCHES="$RACINE/scripts/presentation/ecrans-touches.sh"

# Les fichiers rangés sous la ligne « indéterminée » de #544 — ceux dont aucune route ne se dérive.
indetermines_de() {
  awk -F'\t' '$1 !~ /^#/ && $2 == "-" { n = split($4, f, ","); for (i = 1; i <= n; i++) print f[i] }'
}

# Les écrans DIRECTS : route, clé, fichiers.
directs_de() {
  awk -F'\t' '$1 !~ /^#/ && $2 != "-" { print $2 "\t" $3 "\t" $4 }'
}

# La remonte : quels fichiers importent ceux qu'on lui donne, transitivement. L'appariement se fait sur
# le nom de module — `from "…/Composeur"`, `import("…/Composeur")` —, avec le `/` obligatoire pour ne pas
# confondre un composant avec un paquet npm homonyme. Bornée à 6 tours : un graphe d'imports de
# `apps/web` est plat (page → composant → primitive), et une borne vaut mieux qu'un point fixe qu'un
# cycle d'imports empêcherait d'atteindre.
importateurs_de() {
  local vus vague tour=0 trouves motif base fichier
  vague="$(LC_ALL=C sort -u | sed '/^$/d')"
  [ -z "$vague" ] && return 0
  vus="$vague"
  while [ -n "$vague" ] && [ "$tour" -lt 6 ]; do
    tour=$((tour + 1))
    motif=""
    while IFS= read -r fichier; do
      [ -z "$fichier" ] && continue
      base="$(basename "$fichier")"
      base="${base%.*}"
      # Un fichier d'index est importé par le nom de son DOSSIER, jamais par le sien.
      if [ "$base" = "index" ]; then base="$(basename "$(dirname "$fichier")")"; fi
      motif="${motif}${motif:+|}/${base}"
    done <<<"$vague"
    [ -z "$motif" ] && break
    trouves="$(grep -rlE "(from|import\()[[:space:]]*[\"'][^\"']*(${motif})[\"']" \
      "$RACINE/apps/web/app" "$RACINE/apps/web/components" \
      --include='*.tsx' --include='*.ts' 2>/dev/null \
      | sed "s|^${RACINE}/||" | LC_ALL=C sort -u)"
    # Ne repartir que de ce qu'on ne connaissait pas : sans ça, deux composants qui s'importent l'un
    # l'autre feraient tourner la boucle jusqu'à sa borne pour rien.
    vague="$(printf '%s\n@@@\n%s\n' "$vus" "$trouves" | awk '
      /^@@@$/ { phase = 1; next }
      phase == 0 { vu[$0] = 1; next }
      $0 != "" && !($0 in vu) { print }
    ')"
    if [ -n "$vague" ]; then vus="$(printf '%s\n%s' "$vus" "$vague")"; fi
  done
  printf '%s\n' "$vus"
}

# Le plan : une ligne par écran, `route <TAB> cle <TAB> origine <TAB> fichiers`, où `origine` vaut
# `direct` (le ticket a touché la page) ou `via` (il a touché un composant qu'elle affiche). `direct`
# l'emporte quand les deux se présentent : c'est le rattachement le plus sûr, et le plus court à lire.
plan_de() {
  local iid="$1" brut directs indet composants autres remontes="" orphelins="" composant routes r c
  brut="$(bash "$ECRANS_TOUCHES" --ref HEAD --travail-en-cours "$iid" 2>/dev/null)"
  directs="$(printf '%s\n' "$brut" | directs_de)"

  indet="$(printf '%s\n' "$brut" | indetermines_de)"
  composants="$(printf '%s\n' "$indet" | grep '^apps/web/components/' || true)"
  autres="$(printf '%s\n' "$indet" | grep -v '^apps/web/components/' | sed '/^$/d' || true)"

  # UN COMPOSANT À LA FOIS, et non tous ensemble : la question « quels écrans affichent CE fichier ? »
  # est celle qu'on veut lire dans le plan (un « ← Conversation.tsx » dit quoi regarder ; un
  # « ← app/agents/…/page.tsx » nomme l'importateur, c'est-à-dire la réponse et non la cause). C'est
  # aussi ce qui permet de repérer le composant QUI NE MÈNE NULLE PART — un `Shell.tsx`, importé du
  # seul `app/layout.tsx`, ou une primitive que personne n'affiche encore : fondu dans une remonte
  # collective, il disparaîtrait en silence, alors que la règle de #544 est que l'inconnu se nomme.
  while IFS= read -r composant; do
    [ -z "$composant" ] && continue
    routes="$(printf '%s\n' "$composant" | importateurs_de \
      | grep '^apps/web/app/' \
      | bash "$ECRANS_TOUCHES" --chemins "$iid" 2>/dev/null \
      | directs_de | cut -f1,2 | LC_ALL=C sort -u)"
    if [ -z "$routes" ]; then
      orphelins="${orphelins}${orphelins:+$'\n'}${composant}"
      continue
    fi
    while IFS=$'\t' read -r r c; do
      [ -z "$r" ] && continue
      remontes="${remontes}${remontes:+$'\n'}${r}"$'\t'"${c}"$'\t'"${composant}"
    done <<<"$routes"
  done <<<"$composants"

  # Les indéterminés sortent par le MÊME canal que les écrans, sous la route `-` — le contrat de #544,
  # repris tel quel. Une globale ne pourrait pas les porter : `plan_de` est appelée en substitution de
  # commande, donc dans un sous-shell, où toute affectation meurt avec lui.
  if [ -n "$orphelins" ]; then
    autres="${autres}${autres:+$'\n'}${orphelins}"
  fi

  {
    printf '%s\n@@@\n%s\n' "$directs" "$remontes" | awk -F'\t' '
      /^@@@$/ { phase = 1; next }
      $1 == "" { next }
      {
        origine = (phase == 0) ? "direct" : "via"
        if (!($1 in vu)) { vu[$1] = 1; ordre[++n] = $1; cle[$1] = $2; org[$1] = origine; src[$1] = $3 }
        else {
          if (org[$1] == "via" && origine == "direct") { org[$1] = "direct" }
          if (index(src[$1], $3) == 0) { src[$1] = src[$1] "," $3 }
        }
      }
      END { for (i = 1; i <= n; i++) { r = ordre[i]; print r "\t" cle[r] "\t" org[r] "\t" src[r] } }
    ' | LC_ALL=C sort
    # Une ligne par fichier indéterminé, et jamais regroupées : chacun est une question à part, et la
    # session doit pouvoir les nommer un par un dans « ce que je n'ai pas pu voir ».
    while IFS= read -r fichier; do
      [ -n "$fichier" ] && printf -- '-\t-\tindetermine\t%s\n' "$fichier"
    done <<<"$autres"
  }
}

# --- Les modes ------------------------------------------------------------------------------------
affiche_plan() {
  local iid="$1" lignes="$2" nb="$3" indet="$4" route _cle origine fichiers suffixe f
  dire "Relecture visuelle du ticket #$iid"
  dire ""
  dire "  ports      : UI $PORT_UI · API $PORT_API"
  dire "  captures   : $SOUS_DOSSIER/$iid/<ecran>-<theme>.png — chemin RELATIF, jamais absolu"
  dire "  thèmes     : clair, sombre — les deux, toujours (le socle en porte deux, on en garde deux)"
  dire ""
  if [ "$nb" -eq 0 ]; then
    dire "  aucun écran : ce ticket n'a touché aucune surface visible."
  else
    dire "  écrans à regarder ($nb) :"
    while IFS=$'\t' read -r route _cle origine fichiers; do
      [ -z "$route" ] && continue
      suffixe=""
      [ "$origine" = "via" ] && suffixe="  (composant affiché ici)"
      dire "$(printf '    %-12s http://localhost:%s%-12s ← %s%s' \
        "$route" "$PORT_UI" "$route" "$fichiers" "$suffixe")"
    done <<<"$lignes"
  fi
  if [ -n "$indet" ]; then
    dire ""
    dire "  indéterminé — aucune route ne s'en dérive, à relayer tel quel dans le jugement :"
    while IFS=$'\t' read -r _route _cle _origine f; do
      [ -n "$f" ] && dire "    $f"
    done <<<"$indet"
  fi
}

case "$MODE" in
  fin)
    if [ -n "$IID" ]; then
      printf 'relecture-visuelle.sh : --fin ne prend pas d'\''iid (la stack est celle du dépôt courant).\n' >&2
      exit 2
    fi
    printf 'Fin de la relecture visuelle — ports UI %s · API %s\n' "$PORT_UI" "$PORT_API"
    # L'arrêt d'abord : un projet retiré sous une API vivante la laisserait servir un projet fantôme.
    MAESTRO_PORT_API="$PORT_API" MAESTRO_PORT_UI="$PORT_UI" \
      bash "$RACINE/scripts/controltower/start.sh" --stop
    retire_projet
    exit 0
    ;;
  plan | preparer)
    if [ -z "$IID" ]; then
      printf 'relecture-visuelle.sh : un iid de ticket est attendu.\n\n' >&2
      usage >&2
      exit 2
    fi
    ;;
esac

BRUT="$(plan_de "$IID")"
# Deux natures dans un seul flux (le contrat de #544) : les ÉCRANS, qui portent une route, et les
# INDÉTERMINÉS, qui portent `-`. Ce sont les premiers, et eux seuls, qui décident s'il y a matière à
# monter une stack — un `layout.tsx` ne s'ouvre pas dans un navigateur.
LIGNES="$(printf '%s\n' "$BRUT" | grep -v $'^-\t' | sed '/^$/d' || true)"
INDET="$(printf '%s\n' "$BRUT" | grep $'^-\t' || true)"
NB="$(printf '%s\n' "$LIGNES" | sed '/^$/d' | wc -l | tr -d ' ')"

if [ "$TSV" = 1 ]; then
  printf '# route\turl\torigine\tfichiers\n'
  while IFS=$'\t' read -r route _cle origine fichiers; do
    [ -z "$route" ] && continue
    if [ "$route" = "-" ]; then
      printf -- '-\t-\t%s\t%s\n' "$origine" "$fichiers"
    else
      printf '%s\thttp://localhost:%s%s\t%s\t%s\n' "$route" "$PORT_UI" "$route" "$origine" "$fichiers"
    fi
  done <<<"$BRUT"
else
  affiche_plan "$IID" "$LIGNES" "$NB" "$INDET"
fi

# Un ticket sans surface visible n'est pas une panne : c'est la réponse, et elle vaut `3` — le même code
# que l'abstention de `current-milestone` ou de `touche-surface`. L'appelant qui enchaînerait une stack
# sur un plan vide paierait une minute pour ne rien voir.
if [ "$NB" -eq 0 ]; then
  [ "$MODE" = "preparer" ] && dire "" && dire "  rien à monter : la stack n'est pas démarrée."
  exit 3
fi

[ "$MODE" = "plan" ] && exit 0

# --- Préparation ------------------------------------------------------------------------------------
dire ""
mkdir -p "$RACINE/$SOUS_DOSSIER/$IID" 2>/dev/null
pose_projet

dire "  stack      : démarrage (mode démo, sans navigateur) — le premier passage construit l'UI"
# `--demo` : bus mémoire, aucun Redis requis, et surtout des écrans PEUPLÉS — un poste vide ne montre
# pas le rendu qu'on vient d'écrire. `--no-browser` est obligatoire : sans lui le script ouvre sa propre
# fenêtre et arrête la stack dès qu'elle se ferme (#149), ce qui couperait l'API sous le navigateur
# qu'on pilote.
if MAESTRO_PORT_API="$PORT_API" MAESTRO_PORT_UI="$PORT_UI" \
  bash "$RACINE/scripts/controltower/start.sh" --demo --no-browser; then
  dire ""
  dire "  ✓ prête : http://localhost:$PORT_UI"
  dire "    à faire ensuite — poser le localStorage (guide vu, thème, projet actif), ouvrir chaque écran"
  dire "    dans les deux thèmes, capturer sous $SOUS_DOSSIER/$IID/, puis :"
  dire "        bash scripts/design/relecture-visuelle.sh --fin"
  exit 0
fi

dire ""
dire "  ✗ la stack n'a pas démarré — journaux sous .maestro/controltower/$PORT_API-$PORT_UI/"
dire "    rien n'est laissé derrière : arrêt et retrait du projet."
MAESTRO_PORT_API="$PORT_API" MAESTRO_PORT_UI="$PORT_UI" \
  bash "$RACINE/scripts/controltower/start.sh" --stop >/dev/null 2>&1
retire_projet
exit 1
