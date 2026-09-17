#!/usr/bin/env bash
# LA RELECTURE VISUELLE — ce qu'il faut regarder, et de quoi le regarder (#932, lot 2 de #930).
#
#   bash scripts/design/relecture-visuelle.sh --plan <iid>   # ce qu'il y a à regarder. Ne démarre rien.
#   bash scripts/design/relecture-visuelle.sh <iid>          # le plan, + les stacks montées : après et avant
#   bash scripts/design/relecture-visuelle.sh <iid> --scenario vide   # les mêmes, sur un état limite (#978)
#   bash scripts/design/relecture-visuelle.sh --couverture <iid>      # écran par écran, les états capturés
#   bash scripts/design/relecture-visuelle.sh --saisine <iid>         # ce que le regard neuf reçoit (#980)
#   bash scripts/design/relecture-visuelle.sh --planche <iid>         # la planche HTML avant/après (#980)
#   bash scripts/design/relecture-visuelle.sh --fin          # arrête les stacks et retire ce qu'elles ont posé
#
# Le maillon qui manquait à la chaîne de docs/30 §5.1 : PERSONNE NE REGARDE LE RENDU. La décision et
# ses raisons sont en docs/30 §5.6 ; ce qui l'APPELLE — l'étape 4bis de `/ticket-finish` — en §5.5. `verify` répond
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
#   - `.maestro/relecture/.avant` — le témoin de l'avant (section 6) : où il est monté et sur quels
#     ports, pour que `--fin`, qui ne prend pas d'iid, sache quoi arrêter et quoi retirer.
#   - `.maestro/relecture/<iid>/saisine.md`, `paires.tsv` et `planche.html` — le regard neuf et sa
#     planche (section 7) ; la planche est recopiée au même chemin dans le CLONE PRINCIPAL, seule
#     écriture de ce script hors du dépôt courant.
#
# Il ne commite rien, ne merge rien, et n'ÉCRIT dans aucune forge : il monte des processus locaux sur
# les ports d'un worktree, et les arrête. Sa seule lecture de forge est celle de `--saisine` (section
# 7), et elle passe par `lib.sh relecture-attente` — jamais par un `gh` écrit ici.
#
# --- 4. LE PRIX EST DU TEMPS DE MUR, ET IL S'ANNONCE (règle de #418) -------------------------------
#
# Monter la stack coûte, et un run à concurrence 3 en monterait trois — sur des ports distincts, ce que
# `worktree.sh` garantit depuis #152. C'est pourquoi `--plan` existe SÉPARÉMENT et ne démarre rien : un
# ticket sans surface visible rend `3` en une seconde, et personne ne paie la stack pour apprendre qu'il
# n'y avait rien à regarder. L'avant ajoute sa part — ~50 s de plus la première fois, mesurés au
# cadrage de #977 —, et la préparation dit ce qu'elle a réellement coûté, chronomètre en main.
#
# --- 5. LES ÉTATS LIMITES : ouverts par la démo, comptés par écran (#978) ---------------------------
#
# La démo peuple l'état NOMINAL, et c'est rarement là que le rendu casse : c'est dans une file vide,
# une API en panne, un nom de 80 caractères ou une liste de 200 lignes. `maestro/controltower/demo.py`
# sert donc des SCÉNARIOS nommés, et ce script sait les monter (`--scenario <nom>`, relayé à `start.sh`).
# Deux choses à ne pas défaire :
#
#   - LES NOMS SONT LUS dans `demo.py` (`SCENARIO_* = "…"`), jamais recopiés — même règle que le projet
#     de démo, même raison (#830). Un nom que la démo ne sert pas est refusé ICI, avant la stack : le
#     module le refuserait aussi, mais en arrière-plan, et l'on ne lirait qu'« API injoignable ».
#   - CE QUI A ÉTÉ VU SE COMPTE SUR LE DISQUE, pas dans une déclaration. `--couverture` croise les écrans
#     du plan, les scénarios et les deux thèmes avec les captures déposées — celles du nominal à la
#     racine du dossier du ticket (le chemin d'avant #978, inchangé), celles d'un autre état dans un
#     sous-dossier à son nom. Le script ne sait voir qu'une CAPTURE, jamais un regard : une capture
#     qu'on n'a pas relue ne vaut rien, et c'est au skill de le tenir. Il ne décide pas non plus quels
#     états il FALLAIT couvrir — c'est la rubrique « États à couvrir » du ticket (#976), un texte, que
#     seule la session sait juger (#746) : ce mode constate, il ne rend aucun verdict.
#
# Chaque état se monte par un redémarrage de la stack (le scénario est celui de l'API, qu'on ne change
# pas à chaud) : ~18 s par état, qui s'annoncent au même titre que le reste.
#
# --- 6. L'AVANT : `origin/main`, servi à côté et jamais à la place (#977) --------------------------
#
# Une capture seule ne dit ni ce qui a changé, ni si le changement a abîmé ce qui allait. Chaque écran
# du plan se regarde donc DEUX fois : sur la branche (l'après) et sur `origin/main` (l'avant), dans le
# même thème et le même état. L'avant est servi par une SECONDE stack, montée depuis un SECOND
# worktree, détaché (`worktree.sh avant`, dont l'en-tête dit pourquoi cette voie et pas les deux
# autres) — le travail de la session n'est donc jamais mis en jeu pour l'obtenir : dans l'arbre du
# ticket, rien n'est écrit hors de `.maestro/`, et aucun fichier suivi n'est touché — pas même le temps
# d'une capture.
#
#   - SES PORTS sont ceux de l'après, décalés de 200. Les worktrees de ticket occupent 8001-8100 et
#     3001-3100 (`worktree.sh`, #152), le clone principal 8000/3000 : 8200-8300 et 3200-3300 ne
#     croisent personne. Dérivés de l'après et non de l'iid, pour qu'un `--ports` imposé au montage
#     du worktree emporte l'avant avec lui.
#   - SA CAPTURE EST À CÔTÉ DE L'APRÈS : même dossier d'état (§5), même clé, suffixe `-avant`. Le nom de
#     l'après ne bouge pas — `--couverture` le compte, et il ne compte que les regards sur la branche.
#   - UN ÉCRAN NOUVEAU n'a pas d'avant, et il est NOMMÉ comme tel — jamais capturé sur une 404. La
#     question « cet écran existe-t-il sur origin/main ? » se pose à la règle de #544 : les pages
#     d'origin/main, classées par `ecrans-touches.sh --chemins`. Seules comptent les pages SANS
#     segment dynamique, parce que `/runs/[runId]/page.tsx` se range sous `/runs` sans que `/runs`
#     réponde pour autant.
#   - UN ÉTAT (§5) que la démo d'origin/main ne déclare pas n'a pas d'avant non plus : l'avant sert le
#     même scénario que l'après ou ne sert rien — comparer un état limite au nominal ferait voir une
#     différence que le ticket n'a pas faite.
#   - L'AVANT EST BEST-EFFORT : `origin/main` introuvable, montage ou stack en échec — l'après reste
#     prêt, l'avant est dit indisponible avec sa cause, et c'est la session qui le reporte à « ce que je
#     n'ai pas pu voir ». Un avant manquant ne vaut jamais une relecture manquante.
#   - `MAESTRO_RELECTURE_AVANT=0` l'éteint, et le plan le dit : ne pas payer l'avant est un choix, pas
#     un oubli.
#
# --- 7. LE REGARD NEUF ET SA PLANCHE (#980) ---------------------------------------------------------
#
# « Est-ce que ça a l'air juste ? » n'a pas de réponse sans référence, et son juge était celui qui avait
# écrit l'écran : l'auteur voit ce qu'il a VOULU faire. Le jugement est donc rendu par un sous-agent
# (`.claude/agents/regard-neuf.md`, outil `Read` seul), et ce script prépare ce qu'il reçoit :
#
#   - `--saisine <iid>` écrit `saisine.md` : les PAIRES capturées (chemins absolus — l'outil `Read`
#     n'en prend pas d'autres), le RENDU ATTENDU et les DÉCISIONS déjà prises à l'écran (lus par
#     `lib.sh relecture-attente`, par ancre), la GRILLE et le gabarit à rendre. Rien d'autre : ni le
#     code, ni le diff, ni un mot de la session. C'est ce qui rend « ne reçoit que » VÉRIFIABLE — le
#     prompt du sous-agent n'est que le chemin de ce fichier, et le fichier reste sur le disque.
#     La forge muette ne bloque pas : la saisine le dit, et les rubriques deviennent « non vu ».
#     `--partis-pris <fichier>` y ajoute une veille antérieure à l'ancre, que la session recopie.
#   - LA GRILLE N'EST PAS ÉCRITE ICI : elle est lue dans `scripts/design/grille-relecture.tsv`, que
#     `lib.sh relecture-note` lit aussi pour refuser un jugement incomplet.
#   - `--planche <iid>` écrit `planche.html` : autonome, avant et après côte à côte, le jugement en
#     tête. Recopiée dans le `.maestro/relecture/<iid>/` du CLONE PRINCIPAL, qui survit au ramassage
#     du worktree après le merge ; sa dernière ligne, `PLANCHE <chemin absolu>`, est cette copie. Le gabarit est `scripts/design/planche.py`, qui reprend la mécanique de
#     `scripts/presentation/build.py` (images en `data:`, plafond de taille, bascule de thème,
#     visionneuse) au lieu d'en écrire une seconde. Rien n'est envoyé à la forge : `gh` ne sait pas
#     joindre une image à un commentaire, et le jugement consigné reste du texte.
#
# LES DEUX LISENT LES MÊMES PAIRES, et un seul endroit les dresse (`paires_de`) : le nom d'une capture
# se construit ici et nulle part ailleurs (`nom_capture`), faute de quoi la planche et la saisine
# finiraient par ne plus montrer les mêmes fichiers.
#
# Codes de retour : 0 = il y a à regarder · 3 = aucune surface visible (abstention nominale, pas une
# panne) · 4 = `--saisine`/`--planche` sans aucune capture sur le disque · 1 = échec · 2 = usage.

set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOUS_DOSSIER=".maestro/relecture"

MODE="preparer"
TSV=0
IID=""
SCENARIO=""
PARTIS_PRIS=""

usage() {
  cat <<'USAGE'
La relecture visuelle : ce qu'il faut regarder, et de quoi le regarder.

  bash scripts/design/relecture-visuelle.sh --plan <iid>          Ce qu'il y a à regarder. Ne démarre rien.
  bash scripts/design/relecture-visuelle.sh <iid>                 Le plan, puis les stacks montées : après et avant.
  bash scripts/design/relecture-visuelle.sh <iid> --scenario <nom>  Les mêmes, sur un état de la démo.
  bash scripts/design/relecture-visuelle.sh --couverture <iid>    Écran par écran, les états capturés.
  bash scripts/design/relecture-visuelle.sh --saisine <iid>       Ce que le regard neuf reçoit : paires, attente, grille.
  bash scripts/design/relecture-visuelle.sh --planche <iid>       La planche HTML autonome, avant et après côte à côte.
  bash scripts/design/relecture-visuelle.sh --fin                 Arrête les stacks et retire ce qu'elles ont posé.

Options :
  --plan            N'écrit rien, ne démarre rien : dit seulement s'il y a matière, et laquelle.
  --scenario <nom>  L'état que sert la démo (noms lus dans maestro/controltower/demo.py).
  --couverture      N'écrit rien, ne démarre rien : croise écrans, états et thèmes avec les captures.
  --saisine         Écrit .maestro/relecture/<iid>/saisine.md (lecture seule de la forge) ; sa
                    dernière ligne est « SAISINE <chemin absolu> », à donner au sous-agent regard-neuf.
  --partis-pris <fichier>  Avec --saisine : une veille consignée sans l'ancre « ## Veille de
                    conception », recopiée telle quelle du ticket.
  --planche         Écrit .maestro/relecture/<iid>/planche.html (images en data:, jugement en tête).
                    MAESTRO_RELECTURE_PLANCHE_MAX (Mio, 0 = aucun) plafonne sa taille.
  --tsv             Le plan (ou la couverture) en TSV, pour un appelant machine. La dernière colonne
                    du plan, `avant`, vaut l'URL sur origin/main, `nouveau` pour un écran absent
                    d'origin/main, ou `-` quand l'avant n'est pas évalué.
  -h, --help        Cette aide.

L'avant (origin/main) se sert sur les ports de l'après + 200. MAESTRO_RELECTURE_AVANT=0 l'éteint.

Codes de retour : 0 = il y a à regarder · 3 = aucune surface visible · 4 = aucune capture (saisine,
planche) · 1 = échec · 2 = usage.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --plan) MODE="plan" ;;
    --fin) MODE="fin" ;;
    --couverture) MODE="couverture" ;;
    --saisine) MODE="saisine" ;;
    --planche) MODE="planche" ;;
    --partis-pris)
      if [ $# -lt 2 ] || [ -z "$2" ]; then
        printf 'relecture-visuelle.sh : --partis-pris attend un fichier.\n\n' >&2
        usage >&2; exit 2
      fi
      PARTIS_PRIS="$2"; shift ;;
    --tsv) TSV=1 ;;
    --scenario)
      if [ $# -lt 2 ] || [ -z "$2" ]; then
        printf 'relecture-visuelle.sh : --scenario attend un nom de scénario.\n\n' >&2
        usage >&2; exit 2
      fi
      SCENARIO="$2"; shift ;;
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

# --- L'avant (section 6 de l'en-tête) -----------------------------------------------------------------
DECALAGE_AVANT=200
REF_AVANT="origin/main"
WORKTREE_SH="$RACINE/scripts/git/worktree.sh"
TEMOIN_AVANT="$RACINE/$SOUS_DOSSIER/.avant"
PORT_API_AVANT=""
PORT_UI_AVANT=""
case "$PORT_API$PORT_UI" in
  '' | *[!0-9]*) ;;   # un port venu de l'environnement qui n'est pas un nombre : pas d'avant à dériver
  *) PORT_API_AVANT=$((PORT_API + DECALAGE_AVANT)); PORT_UI_AVANT=$((PORT_UI + DECALAGE_AVANT)) ;;
esac

# --- Le projet de la démo -----------------------------------------------------------------------------
# L'identifiant est LU dans le scénario : le jour où la démo change de projet, le script suit.
PROJET_DEMO="$(sed -n 's/^PROJET_ID *= *"\([^"]*\)".*/\1/p' "$RACINE/maestro/controltower/demo.py" | head -n 1)"
PROJET_DEMO="${PROJET_DEMO:-prj-demo}"
PROJET_FICHIER="$RACINE/core/projets/$PROJET_DEMO.json"
TEMOIN_PROJET="$RACINE/$SOUS_DOSSIER/.projet-pose"

# --- Les scénarios de la démo (#978) ------------------------------------------------------------------
# LUS dans le même fichier, dans l'ordre où la démo les déclare (voir l'en-tête, §5). Une démo qui n'en
# déclare aucun — antérieure à #978 — rend une liste vide : le plan le dit, `--scenario` est refusé, et
# la couverture ne compte que le nominal, qui existe toujours.
SCENARIOS_DEMO="$(sed -n 's/^SCENARIO_[A-Z_]* *= *"\([^"]*\)".*/\1/p' \
  "$RACINE/maestro/controltower/demo.py" 2>/dev/null | tr '\n' ' ')"
SCENARIOS_DEMO="${SCENARIOS_DEMO% }"
SCENARIO_NOMINAL="$(sed -n 's/^SCENARIO_NOMINAL *= *"\([^"]*\)".*/\1/p' \
  "$RACINE/maestro/controltower/demo.py" 2>/dev/null | head -n 1)"
SCENARIO_NOMINAL="${SCENARIO_NOMINAL:-nominal}"

# Le dossier des captures d'un état, RELATIF à la racine. Le nominal garde le chemin d'avant #978 — le
# dossier du ticket lui-même —, un autre état a le sien dessous : deux états d'un même écran ne
# s'écrasent pas, et une relecture qui n'ouvre que le nominal n'a rien à apprendre de neuf.
dossier_captures() {
  local iid="$1" scenario="${2:-}"
  if [ -z "$scenario" ] || [ "$scenario" = "$SCENARIO_NOMINAL" ]; then
    printf '%s/%s' "$SOUS_DOSSIER" "$iid"
  else
    printf '%s/%s/%s' "$SOUS_DOSSIER" "$iid" "$scenario"
  fi
}

# Le nom d'une capture : la clé d'écran de #544 (celle de `captures.mjs`) et le thème. Un seul endroit
# le construit, pour que le skill, le plan et la couverture parlent du même fichier.
nom_capture() { printf '%s-%s.png' "$1" "$2"; }
# Son avant (#977), à côté : même clé, même thème, suffixe `-avant`.
nom_capture_avant() { printf '%s-%s-avant.png' "$1" "$2"; }

# Le dossier que l'écran « Projets » affichera. Il ne sert qu'à ça : rien n'y est écrit, et la validation
# des racines (`maestro/projets/racine.py`) n'est pas rejouée à la LECTURE du dépôt de projets — c'est
# déjà ce dont `captures.sh` se sert pour poser un identifiant fixe. Forme à slashes, et le temporaire
# WINDOWS de préférence à `/tmp` : un `/tmp` de Git Bash n'est pas le même chemin pour le Python du venv.
base_temporaire() {
  local base="${TMPDIR:-${TEMP:-/tmp}}"
  printf '%s' "${base//\\//}"
}

# ecris_projet <fichier> <id> : la déclaration du projet de démo, écrite UNE fois pour les deux stacks.
ecris_projet() {
  local fichier="$1" id="$2" racine_fictive
  racine_fictive="$(base_temporaire)/maestro-relecture/mini-crm"
  mkdir -p "$racine_fictive" 2>/dev/null
  mkdir -p "$(dirname "$fichier")" 2>/dev/null
  printf '{"id":"%s","nom":"mini-CRM (démo)","racine":"%s","origine":"existant","vcs":null}\n' \
    "$id" "$racine_fictive" >"$fichier"
}

pose_projet() {
  if [ -f "$PROJET_FICHIER" ]; then
    # Posé par une relecture précédente de ce dépôt (un autre état monté, #978) : il est à nous, et
    # `--fin` le retirera. Le dire autrement laisserait croire qu'il appartient à quelqu'un d'autre.
    if [ -f "$TEMOIN_PROJET" ]; then
      dire "  projet     : « $PROJET_DEMO » déjà posé par cette relecture (retiré par --fin)"
    else
      dire "  projet     : « $PROJET_DEMO » déjà déclaré — laissé en place"
    fi
    return 0
  fi
  if ! ecris_projet "$PROJET_FICHIER" "$PROJET_DEMO"; then
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

# --- L'avant : ce qu'origin/main sait servir --------------------------------------------------------
AVANT_ETAT=""      # actif · eteint · indisponible
AVANT_SHA=""
AVANT_RAISON=""
ROUTES_AVANT=""

evalue_avant() {
  if [ "${MAESTRO_RELECTURE_AVANT:-1}" = 0 ]; then
    AVANT_ETAT="eteint"; AVANT_RAISON="MAESTRO_RELECTURE_AVANT=0"
    return 0
  fi
  if [ -z "$PORT_UI_AVANT" ]; then
    AVANT_ETAT="indisponible"; AVANT_RAISON="ports de l'après illisibles (UI « $PORT_UI »)"
    return 0
  fi
  AVANT_SHA="$(git -C "$RACINE" rev-parse --verify --quiet "$REF_AVANT^{commit}" 2>/dev/null)"
  if [ -z "$AVANT_SHA" ]; then
    AVANT_ETAT="indisponible"; AVANT_RAISON="$REF_AVANT introuvable dans ce dépôt"
    return 0
  fi
  AVANT_ETAT="actif"
  # Les écrans qu'origin/main SERT, à la règle de #544 : ses pages, classées comme celles du ticket.
  # Une page sous un segment dynamique est écartée — elle se range sous la route de sa liste sans que
  # cette route réponde, et l'avant serait alors capturé sur une 404.
  ROUTES_AVANT="$(git -C "$RACINE" ls-tree -r --name-only "$AVANT_SHA" -- apps/web/app 2>/dev/null \
    | grep -E '/page\.(tsx|ts|jsx|js|mdx)$' | grep -v '\[' \
    | bash "$ECRANS_TOUCHES" --chemins "$IID" 2>/dev/null \
    | directs_de | cut -f1 | LC_ALL=C sort -u)"
}

# avant_de <route> : ce que l'avant rend pour cet écran — son URL, `nouveau`, ou `-` (non évalué).
avant_de() {
  local route="$1"
  if [ "$AVANT_ETAT" != "actif" ] || [ "$route" = "-" ]; then
    printf -- '-'
  elif printf '%s\n' "$ROUTES_AVANT" | grep -qxF -- "$route"; then
    printf 'http://localhost:%s%s' "$PORT_UI_AVANT" "$route"
  else
    printf 'nouveau'
  fi
}

# arrete_stack_avant : arrête la stack de l'avant nommée par le témoin. Par le `start.sh` de CE dépôt :
# l'arrêt se fait par les ports, et il doit rester possible quand l'arbre de l'avant a déjà disparu.
arrete_stack_avant() {
  [ -f "$TEMOIN_AVANT" ] || return 0
  local _iid _chemin api ui
  IFS=$'\t' read -r _iid _chemin api ui <"$TEMOIN_AVANT"
  [ -n "$api" ] && [ -n "$ui" ] || return 0
  MAESTRO_PORT_API="$api" MAESTRO_PORT_UI="$ui" \
    bash "$RACINE/scripts/controltower/start.sh" --stop 2>&1 | sed 's/^/  [avant] /'
}

# retire_avant : retire le worktree de l'avant nommé par le témoin, et le témoin avec — seulement si le
# retrait a abouti, pour qu'un `--fin` rejoué retrouve ce qui reste à retirer.
retire_avant() {
  [ -f "$TEMOIN_AVANT" ] || return 0
  local iid _chemin _api _ui
  IFS=$'\t' read -r iid _chemin _api _ui <"$TEMOIN_AVANT"
  if [ -z "$iid" ] || [ ! -f "$WORKTREE_SH" ]; then
    rm -f "$TEMOIN_AVANT"
    return 0
  fi
  if bash "$WORKTREE_SH" avant --retirer "$iid" 2>&1; then
    rm -f "$TEMOIN_AVANT"
  fi
}

# prepare_avant : monte l'avant et sa stack, APRÈS l'après — l'après est ce qui compte, et un avant ne
# se paie pas pour une relecture qui ne pourra pas avoir lieu. Best-effort de bout en bout : il ne
# change jamais le code de retour.
prepare_avant() {
  local debut sortie code chemin id journal ligne
  case "$AVANT_ETAT" in
    eteint) dire "  avant      : éteint ($AVANT_RAISON) — l'après seul"; return 0 ;;
    actif) ;;
    *) dire "  avant      : indisponible — $AVANT_RAISON ; l'après seul"; return 0 ;;
  esac
  if [ "$NB_AVANT" -eq 0 ]; then
    dire "  avant      : rien à monter — tous les écrans du plan sont nouveaux"
    return 0
  fi
  if [ ! -f "$WORKTREE_SH" ]; then
    dire "  avant      : indisponible — scripts/git/worktree.sh introuvable ; l'après seul"
    return 0
  fi
  # Le même état que l'après, ou rien (en-tête, §6). Lu dans le `demo.py` d'origin/main AVANT de monter
  # quoi que ce soit : ne rien servir coûte moins cher qu'un worktree monté pour rien. Une stack d'avant
  # restée d'un état précédent est arrêtée — elle servirait un autre état sous la même URL.
  local args_avant=(--demo --no-browser)
  if [ -n "$SCENARIO" ] && [ "$SCENARIO" != "$SCENARIO_NOMINAL" ]; then
    if ! git -C "$RACINE" show "$AVANT_SHA:maestro/controltower/demo.py" 2>/dev/null \
      | grep -q "^SCENARIO_[A-Z_]* *= *\"$SCENARIO\""; then
      arrete_stack_avant >/dev/null
      dire "  avant      : $REF_AVANT ne sert pas l'état « $SCENARIO » — l'après seul pour cet état"
      return 0
    fi
    args_avant+=(--scenario "$SCENARIO")
  fi

  dire "  avant      : montage d'$REF_AVANT — worktree détaché et seconde stack (~50 s la première fois)"
  debut=$SECONDS
  sortie="$(bash "$WORKTREE_SH" avant --sans-fetch "$IID" 2>&1)"; code=$?
  chemin="$(printf '%s\n' "$sortie" | sed -n 's/^AVANT //p' | tail -n 1)"
  if [ "$code" -ne 0 ] || [ -z "$chemin" ]; then
    dire "  ⚠ avant indisponible — montage en échec : $(printf '%s\n' "$sortie" \
      | grep -v '^AVANT ' | sed '/^[[:space:]]*$/d' | tail -n 1 | sed 's/^[[:space:]]*//')"
    dire "    l'après reste prêt ; l'avant va à « ce que je n'ai pas pu voir »."
    return 0
  fi
  chemin="${chemin//\\//}"
  mkdir -p "$(dirname "$TEMOIN_AVANT")" 2>/dev/null
  printf '%s\t%s\t%s\t%s\n' "$IID" "$chemin" "$PORT_API_AVANT" "$PORT_UI_AVANT" >"$TEMOIN_AVANT"

  # Le projet de démo de l'AVANT : son identifiant est lu dans SON scénario, qui peut différer de celui
  # de la branche. Aucun témoin : l'arbre entier part avec `--fin`.
  id="$(sed -n 's/^PROJET_ID *= *"\([^"]*\)".*/\1/p' "$chemin/maestro/controltower/demo.py" 2>/dev/null | head -n 1)"
  id="${id:-$PROJET_DEMO}"
  [ -f "$chemin/core/projets/$id.json" ] || ecris_projet "$chemin/core/projets/$id.json" "$id"

  # Le `start.sh` de l'AVANT, et non celui de la branche : l'avant est origin/main tel qu'il se lance.
  if MAESTRO_PORT_API="$PORT_API_AVANT" MAESTRO_PORT_UI="$PORT_UI_AVANT" \
    bash "$chemin/scripts/controltower/start.sh" "${args_avant[@]}" >/dev/null 2>&1; then
    dire "  ✓ avant prêt en $((SECONDS - debut)) s : http://localhost:$PORT_UI_AVANT ($REF_AVANT ${AVANT_SHA:0:7}, état « $ETAT_ANNONCE »)"
    return 0
  fi
  dire "  ⚠ avant indisponible — la stack d'$REF_AVANT n'a pas démarré. Fin de ses journaux :"
  for journal in api ui; do
    while IFS= read -r ligne; do
      dire "      [$journal] $ligne"
    done < <(tail -n 5 "$chemin/.maestro/controltower/$PORT_API_AVANT-$PORT_UI_AVANT/$journal.log" 2>/dev/null)
  done
  arrete_stack_avant >/dev/null
  retire_avant >/dev/null
  dire "    l'après reste prêt ; l'avant va à « ce que je n'ai pas pu voir »."
}

# --- Le regard neuf et sa planche (section 7 de l'en-tête) ------------------------------------------
GRILLE="$RACINE/scripts/design/grille-relecture.tsv"
LIB_SH="$RACINE/scripts/gitlab/lib.sh"

# paires_de <iid> <lignes du plan> : une ligne par écran, état et thème —
# `etat <TAB> route <TAB> cle <TAB> theme <TAB> apres <TAB> avant`, où `apres` et `avant` sont le chemin
# RELATIF de la capture quand elle est sur le disque, `-` sinon, et `avant` vaut `nouveau` pour un écran
# absent d'origin/main. Toutes les combinaisons y sont, capturées ou non : ce qui manque se nomme.
paires_de() {
  local iid="$1" lignes="$2" etats scenario dossier route cle _origine _fichiers av theme apres avant
  etats="$SCENARIO_NOMINAL"
  [ -n "$SCENARIOS_DEMO" ] && etats="$SCENARIOS_DEMO"
  for scenario in $etats; do
    dossier="$(dossier_captures "$iid" "$scenario")"
    while IFS=$'\t' read -r route cle _origine _fichiers; do
      [ -z "$route" ] && continue
      av="$(avant_de "$route")"
      for theme in clair sombre; do
        apres="$dossier/$(nom_capture "$cle" "$theme")"
        avant="$dossier/$(nom_capture_avant "$cle" "$theme")"
        [ -s "$RACINE/$apres" ] || apres="-"
        if [ ! -s "$RACINE/$avant" ]; then
          avant="-"
          [ "$av" = "nouveau" ] && avant="nouveau"
        fi
        printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$scenario" "$route" "$cle" "$theme" "$apres" "$avant"
      done
    done <<<"$lignes"
  done
}

# Le nombre de captures RÉELLEMENT sur le disque dans une liste de paires.
compte_captures() {
  awk -F'\t' '$5 != "-" { n++ } $6 != "-" && $6 != "nouveau" { n++ } END { print n + 0 }'
}

# La racine sous la forme que l'outil `Read` du sous-agent accepte : `E:/…` sous Windows — la forme MSYS
# `/e/…` n'y désigne rien —, le chemin tel quel ailleurs.
racine_native() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$RACINE"; else printf '%s' "$RACINE"; fi
}

# cellule_capture <chemin|-|nouveau> <racine> : ce qu'une case du tableau de la saisine dit d'un côté.
cellule_capture() {
  case "$1" in
    -) printf 'non capturé' ;;
    nouveau) printf 'écran nouveau — aucun avant' ;;
    *) printf '`%s/%s`' "$2" "$1" ;;
  esac
}

# en_citation : recopie stdin tel quel, en citation Markdown. Une décision recopiée garde ses propres
# titres (`## Veille de conception…`) : citée, elle ne se confond pas avec les sections de la saisine.
en_citation() { awk '{ print ($0 == "" ? ">" : "> " $0) }'; }

# ecris_saisine <iid> <paires> <fichier> : la saisine du regard neuf. Rend 1 si la grille est
# introuvable — une saisine sans grille ferait rendre un texte libre, c'est-à-dire ce que #980 retire.
ecris_saisine() {
  local iid="$1" paires="$2" sortie="$3" racine attente code_attente=0 rendu="" decisions="" etats
  local scenario du_etat nb route _cle theme apres avant libelle question source n
  if [ ! -f "$GRILLE" ]; then
    printf 'relecture-visuelle.sh : grille introuvable (%s) — pas de saisine.\n' "$GRILLE" >&2
    return 1
  fi
  racine="$(racine_native)"
  attente="$(bash "$LIB_SH" relecture-attente "$iid" 2>/dev/null)" || code_attente=$?
  if [ "$code_attente" -eq 0 ]; then
    rendu="$(printf '%s\n' "$attente" | awk '/^@@decisions@@$/ { exit } { print }')"
    decisions="$(printf '%s\n' "$attente" | awk 'p { print } /^@@decisions@@$/ { p = 1 }')"
  fi
  etats="$(printf '%s\n' "$paires" | cut -f1 | awk 'NF && !vu[$0]++')"

  {
    printf '# Saisine du regard neuf — ticket #%s\n\n' "$iid"
    cat <<'TETE'
Préparée par `scripts/design/relecture-visuelle.sh --saisine`. Tout ce que tu as à juger est ici, et
rien d'autre n'est à lire : ouvre chaque capture nommée avec `Read` (les chemins sont absolus), puis
rends le gabarit de la section 5, rempli.

## 1. Les captures

Une ligne = une paire : même écran, même thème, même état. « écran nouveau » : il n'existe pas avant
ce ticket et n'a que son après. « non capturé » : personne ne l'a pris — ce qui en dépend est « non vu ».
TETE
    while IFS= read -r scenario; do
      [ -z "$scenario" ] && continue
      du_etat="$(printf '%s\n' "$paires" | ETAT="$scenario" awk -F'\t' '$1 == ENVIRON["ETAT"]')"
      nb="$(printf '%s\n' "$du_etat" | compte_captures)"
      if [ "$nb" -eq 0 ]; then
        printf '\n### État « %s » — aucune capture\n\nRien de cet état n'\''a été capturé : tout ce qui le concerne est « non vu ».\n' "$scenario"
        continue
      fi
      printf '\n### État « %s »\n\n| Écran | Thème | Après | Avant |\n|---|---|---|---|\n' "$scenario"
      while IFS=$'\t' read -r _e route _cle theme apres avant; do
        [ -z "$route" ] && continue
        printf '| `%s` | %s | %s | %s |\n' "$route" "$theme" \
          "$(cellule_capture "$apres" "$racine")" "$(cellule_capture "$avant" "$racine")"
      done <<<"$du_etat"
    done <<<"$etats"

    printf '\n## 2. Le rendu attendu\n\n'
    if [ "$code_attente" -ne 0 ]; then
      printf "Le ticket n'a pas pu être lu (lib.sh relecture-attente : code %s) : le rendu attendu est inconnu.\n" "$code_attente"
    elif [ -z "$(printf '%s' "$rendu" | tr -d '[:space:]')" ]; then
      printf 'Le ticket ne porte pas de rendu attendu.\n'
    else
      printf 'La section du ticket, telle qu'\''elle est écrite :\n\n'
      printf '%s\n' "$rendu" | sed '1d' | en_citation
    fi

    printf '\n## 3. Les décisions déjà prises à l'\''écran\n\n'
    if [ "$code_attente" -ne 0 ]; then
      printf "Le ticket n'a pas pu être lu : les décisions consignées sont inconnues.\n"
    elif [ -z "$(printf '%s' "$decisions" | tr -d '[:space:]')" ] && [ -z "$PARTIS_PRIS" ]; then
      printf 'Aucune décision consignée sur le ticket.\n'
    fi
    if [ "$code_attente" -eq 0 ] && [ -n "$(printf '%s' "$decisions" | tr -d '[:space:]')" ]; then
      printf 'Les commentaires du ticket qui les portent, tels qu'\''ils sont écrits — une citation par\n'
      printf 'commentaire :\n\n'
      # Le séparateur de `relecture-attente` devient une ligne HORS citation : deux commentaires font
      # deux citations, et aucun ne se lit comme la suite de l'autre.
      printf '%s\n' "$decisions" | awk '
        /^@@decision@@$/ { print ""; next }
        { print ($0 == "" ? ">" : "> " $0) }'
    fi
    if [ -n "$PARTIS_PRIS" ]; then
      if [ "$code_attente" -ne 0 ] || [ -n "$(printf '%s' "$decisions" | tr -d '[:space:]')" ]; then
        printf '\n'
      fi
      printf 'Une veille consignée sans ancre, recopiée du ticket par la session :\n\n'
      en_citation <"$PARTIS_PRIS"
    fi

    printf '\n## 4. La grille\n\n'
    printf 'Elle nomme ce qui se voit ; elle ne mesure ni contraste ni géométrie (d'\''autres outils le font).\n\n'
    n=0
    while IFS=$'\t' read -r libelle question source; do
      case "$libelle" in '' | '#'*) continue ;; esac
      n=$((n + 1))
      printf '%s. **%s** — %s *Source : %s.*\n' "$n" "$libelle" "$question" "$source"
    done <"$GRILLE"

    printf '\n## 5. Le gabarit à rendre — rempli, et rien d'\''autre\n\n'
    printf 'Réponses permises : ✓, ✗ (avec écran · thème · état), ou « non vu » (avec ce qui manque).\n'
    printf 'Recopie les titres `### Regard neuf — …` tels quels.\n\n'
    printf '### Regard neuf — grille\n\n| Ligne | Réponse | Où, et ce qui se voit |\n|---|---|---|\n'
    while IFS=$'\t' read -r libelle _question _source; do
      case "$libelle" in '' | '#'*) continue ;; esac
      printf '| %s |  |  |\n' "$libelle"
    done <"$GRILLE"

    printf '\n### Regard neuf — contre le rendu attendu\n\n'
    if [ "$code_attente" -ne 0 ]; then
      printf "Rendu attendu illisible sur le ticket : non confronté.\n"
    elif [ -z "$(printf '%s' "$rendu" | tr -d '[:space:]')" ]; then
      printf 'Le ticket ne porte pas de rendu attendu : rien à confronter.\n'
    else
      printf "Une rubrique que le ticket ne remplit pas se répond « non renseignée ».\n\n"
      printf '| Rubrique | Réponse | Ce qui se voit |\n|---|---|---|\n'
      printf '| Question |  |  |\n| Référence |  |  |\n| Ce qui ne bouge pas |  |  |\n| États à couvrir |  |  |\n'
    fi

    printf '\n### Regard neuf — contre les décisions déjà prises\n\n'
    if [ "$code_attente" -ne 0 ] && [ -z "$PARTIS_PRIS" ]; then
      printf 'Décisions illisibles sur le ticket : non confrontées.\n'
    elif [ -z "$(printf '%s' "$decisions" | tr -d '[:space:]')" ] && [ -z "$PARTIS_PRIS" ]; then
      printf 'Aucune décision consignée : rien à confronter.\n'
    else
      printf -- '- <chaque parti pris ou choix, en une ligne> — tenu · plié · non vu : <ce qui se voit>\n'
    fi
  } >"$sortie"
  return 0
}

# python_du_depot : l'interpréteur du venv du dépôt, sinon celui du poste. La planche n'importe que la
# bibliothèque standard (et `build.py`, qui n'en importe pas plus) : un poste sans venv la produit quand
# même, là où tout le reste du dépôt exigerait le venv.
python_du_depot() {
  local candidat
  for candidat in "$RACINE/.venv/Scripts/python.exe" "$RACINE/.venv/bin/python"; do
    if [ -x "$candidat" ]; then printf '%s' "$candidat"; return 0; fi
  done
  for candidat in python3 python; do
    if command -v "$candidat" >/dev/null 2>&1; then printf '%s' "$candidat"; return 0; fi
  done
  return 1
}

# --- Les modes ------------------------------------------------------------------------------------
affiche_plan() {
  local iid="$1" lignes="$2" nb="$3" indet="$4" route _cle origine fichiers suffixe f avant
  dire "Relecture visuelle du ticket #$iid"
  dire ""
  dire "  ports      : UI $PORT_UI · API $PORT_API"
  case "$AVANT_ETAT" in
    actif)  dire "  avant      : $REF_AVANT (${AVANT_SHA:0:7}) — UI $PORT_UI_AVANT · API $PORT_API_AVANT" ;;
    eteint) dire "  avant      : éteint ($AVANT_RAISON) — l'après seul" ;;
    *)      dire "  avant      : indisponible — $AVANT_RAISON ; l'après seul" ;;
  esac
  if [ "$AVANT_ETAT" = "actif" ]; then
    dire "  captures   : $SOUS_DOSSIER/$iid/<ecran>-<theme>.png, et <ecran>-<theme>-avant.png à côté — chemin RELATIF"
  else
    dire "  captures   : $SOUS_DOSSIER/$iid/<ecran>-<theme>.png — chemin RELATIF, jamais absolu"
  fi
  dire "  thèmes     : clair, sombre — les deux, toujours (le socle en porte deux, on en garde deux)"
  if [ -n "$SCENARIOS_DEMO" ]; then
    dire "  états      : ${SCENARIOS_DEMO// / · } — lus dans maestro/controltower/demo.py"
    dire "               un état autre que « $SCENARIO_NOMINAL » se monte par --scenario <nom>, ses captures"
    dire "               sous $SOUS_DOSSIER/$iid/<nom>/ ; --couverture dit, écran par écran, lesquels sont vus"
  else
    dire "  états      : « $SCENARIO_NOMINAL » seul — cette démo ne déclare aucun autre scénario"
  fi
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
      avant="$(avant_de "$route")"
      case "$avant" in
        -) ;;
        nouveau) dire "$(printf '    %-12s avant : aucun — écran NOUVEAU, absent d'\''%s' "" "$REF_AVANT")" ;;
        *) dire "$(printf '    %-12s avant : %s' "" "$avant")" ;;
      esac
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

if [ -n "$PARTIS_PRIS" ]; then
  if [ "$MODE" != "saisine" ]; then
    printf 'relecture-visuelle.sh : --partis-pris ne vaut qu'\''avec --saisine (c'\''est ce que le regard neuf reçoit).\n' >&2
    exit 2
  fi
  if [ ! -s "$PARTIS_PRIS" ]; then
    printf 'relecture-visuelle.sh : --partis-pris : fichier introuvable ou vide : %s\n' "$PARTIS_PRIS" >&2
    exit 2
  fi
fi

case "$MODE" in
  fin)
    if [ -n "$IID" ]; then
      printf 'relecture-visuelle.sh : --fin ne prend pas d'\''iid (la stack est celle du dépôt courant).\n' >&2
      exit 2
    fi
    if [ -n "$SCENARIO" ]; then
      printf 'relecture-visuelle.sh : --fin ne prend pas de scénario (il arrête la stack, quel que soit son état).\n' >&2
      exit 2
    fi
    printf 'Fin de la relecture visuelle — ports UI %s · API %s\n' "$PORT_UI" "$PORT_API"
    # L'arrêt d'abord : un projet retiré sous une API vivante la laisserait servir un projet fantôme.
    # Les DEUX stacks avant tout retrait — et l'avant d'abord, ce qui laisse à ses processus le temps
    # de lâcher leurs fichiers pendant que l'après s'arrête : un dossier encore tenu résisterait au
    # retrait de son worktree (#422).
    arrete_stack_avant
    MAESTRO_PORT_API="$PORT_API" MAESTRO_PORT_UI="$PORT_UI" \
      bash "$RACINE/scripts/controltower/start.sh" --stop
    retire_projet
    retire_avant
    exit 0
    ;;
  plan | preparer | couverture | saisine | planche)
    if [ -z "$IID" ]; then
      printf 'relecture-visuelle.sh : un iid de ticket est attendu.\n\n' >&2
      usage >&2
      exit 2
    fi
    if [ -n "$SCENARIO" ] && [ "$MODE" != "preparer" ]; then
      printf 'relecture-visuelle.sh : --scenario ne vaut que pour monter la stack (le plan et la couverture valent pour tous les états).\n' >&2
      exit 2
    fi
    # Refusé ICI, avant la stack : `demo.py` le refuserait aussi, mais en arrière-plan (voir §5).
    if [ -n "$SCENARIO" ]; then
      case " $SCENARIOS_DEMO " in
        *" $SCENARIO "*) ;;
        *)
          printf 'relecture-visuelle.sh : scénario inconnu « %s » (la démo sert : %s).\n' \
            "$SCENARIO" "${SCENARIOS_DEMO:-aucun}" >&2
          exit 2
          ;;
      esac
    fi
    ;;
esac

# La couverture : pour chaque écran du plan, chaque état et chaque thème, la capture est-elle là ?
# Lecture du disque seule — ni stack, ni navigateur, ni forge. Voir l'en-tête, §5 : elle CONSTATE.
# Une cellule de 10 colonnes. La largeur VISIBLE est passée à la main : `printf '%-10s'` compte des
# octets sous une locale C, et `✓`, `—` en pèsent trois chacun — le tableau se décalait d'une ligne à
# l'autre selon ce qu'elle contenait (même piège que la vue de `run.sh`, #325).
cellule() { printf '%s%*s' "$1" "$((10 - $2))" ''; }

affiche_couverture() {
  local iid="$1" lignes="$2" route cle scenario theme fichier etats marque clair sombre
  etats="$SCENARIO_NOMINAL"
  [ -n "$SCENARIOS_DEMO" ] && etats="$SCENARIOS_DEMO"
  if [ "$TSV" = 1 ]; then
    printf '# route\tcle\tscenario\tclair\tsombre\n'
  else
    dire "Couverture de la relecture du ticket #$iid — captures sous $SOUS_DOSSIER/$iid/"
    dire ""
    dire "  écran         $(for scenario in $etats; do printf '%-10s' "$scenario"; done)"
  fi
  while IFS=$'\t' read -r route cle _origine _fichiers; do
    [ -z "$route" ] && continue
    marque=""
    for scenario in $etats; do
      clair=0; sombre=0
      for theme in clair sombre; do
        fichier="$(dossier_captures "$iid" "$scenario")/$(nom_capture "$cle" "$theme")"
        if [ -s "$RACINE/$fichier" ]; then
          [ "$theme" = clair ] && clair=1
          [ "$theme" = sombre ] && sombre=1
        fi
      done
      if [ "$TSV" = 1 ]; then
        printf '%s\t%s\t%s\t%s\t%s\n' "$route" "$cle" "$scenario" "$clair" "$sombre"
      else
        case "$clair$sombre" in
          11) marque="${marque}$(cellule '✓' 1)" ;;
          10) marque="${marque}$(cellule 'clair' 5)" ;;
          01) marque="${marque}$(cellule 'sombre' 6)" ;;
          *) marque="${marque}$(cellule '—' 1)" ;;
        esac
      fi
    done
    [ "$TSV" = 1 ] || dire "$(printf '  %-14s' "$route")$marque"
  done <<<"$lignes"
  if [ "$TSV" != 1 ]; then
    dire ""
    dire "  ✓ les deux thèmes · clair / sombre : un seul · — rien de capturé"
    dire "  une capture n'est pas un regard : ne compte comme vue que celle qu'on a relue."
    dire "  fichiers attendus : $SOUS_DOSSIER/$iid/[<état>/]<clé>-<thème>.png — clés : $(
      printf '%s\n' "$lignes" | cut -f2 | sed '/^$/d' | tr '\n' ' ')"
  fi
}

BRUT="$(plan_de "$IID")"
# Deux natures dans un seul flux (le contrat de #544) : les ÉCRANS, qui portent une route, et les
# INDÉTERMINÉS, qui portent `-`. Ce sont les premiers, et eux seuls, qui décident s'il y a matière à
# monter une stack — un `layout.tsx` ne s'ouvre pas dans un navigateur.
LIGNES="$(printf '%s\n' "$BRUT" | grep -v $'^-\t' | sed '/^$/d' || true)"
INDET="$(printf '%s\n' "$BRUT" | grep $'^-\t' || true)"
NB="$(printf '%s\n' "$LIGNES" | sed '/^$/d' | wc -l | tr -d ' ')"

if [ "$MODE" = "couverture" ]; then
  if [ "$NB" -eq 0 ]; then
    dire "Couverture de la relecture du ticket #$IID — aucun écran : rien à couvrir."
    exit 3
  fi
  affiche_couverture "$IID" "$LIGNES"
  exit 0
fi

# L'avant se juge sur l'origin/main LOCAL en `--plan` — gratuit, hors réseau, comme le reste du plan.
# La préparation, elle, va chercher le plus frais d'abord : c'est lui qu'on va servir, et un écran
# mergé entre-temps ne doit pas y être annoncé nouveau. Best-effort : hors ligne, on sert ce qu'on a.
if [ "$MODE" = "preparer" ] && [ "$NB" -gt 0 ] && [ "${MAESTRO_RELECTURE_AVANT:-1}" != 0 ]; then
  GIT_TERMINAL_PROMPT=0 git -C "$RACINE" fetch origin main >/dev/null 2>&1
fi
evalue_avant
NB_AVANT=0
while IFS=$'\t' read -r route _cle _origine _fichiers; do
  [ -z "$route" ] && continue
  case "$(avant_de "$route")" in http*) NB_AVANT=$((NB_AVANT + 1)) ;; esac
done <<<"$LIGNES"

# --- Le regard neuf et sa planche (section 7) : lecture du disque, rien ne démarre ---------------------
if [ "$MODE" = "saisine" ] || [ "$MODE" = "planche" ]; then
  if [ "$NB" -eq 0 ]; then
    printf 'Relecture visuelle du ticket #%s — aucun écran : rien à juger.\n' "$IID"
    exit 3
  fi
  PAIRES="$(paires_de "$IID" "$LIGNES")"
  NB_CAPTURES="$(printf '%s\n' "$PAIRES" | compte_captures)"
  NB_PAIRES="$(printf '%s\n' "$PAIRES" | sed '/^$/d' | wc -l | tr -d ' ')"
  NB_PAIRES_VUES="$(printf '%s\n' "$PAIRES" \
    | awk -F'\t' 'NF && ($5 != "-" || ($6 != "-" && $6 != "nouveau")) { n++ } END { print n + 0 }')"
  DOSSIER="$SOUS_DOSSIER/$IID"
  if [ "$NB_CAPTURES" -eq 0 ]; then
    printf 'Relecture visuelle du ticket #%s — aucune capture sous %s/ : rien à juger.\n' "$IID" "$DOSSIER" >&2
    printf '  La relecture n'\''a pas eu lieu : sa raison se consigne (lib.sh relecture-note --raison).\n' >&2
    exit 4
  fi
  mkdir -p "$RACINE/$DOSSIER" 2>/dev/null

  if [ "$MODE" = "saisine" ]; then
    SAISINE="$DOSSIER/saisine.md"
    ecris_saisine "$IID" "$PAIRES" "$RACINE/$SAISINE" || exit 1
    printf 'Saisine du regard neuf — ticket #%s\n\n' "$IID"
    printf '  captures   : %s — %s paire(s) sur %s en portent au moins une (écran × état × thème)\n' \
      "$NB_CAPTURES" "$NB_PAIRES_VUES" "$NB_PAIRES"
    if grep -q "^Le ticket n'a pas pu être lu" "$RACINE/$SAISINE"; then
      printf '  ⚠ attente  : ticket illisible — rendu attendu et décisions iront à « non vu »\n'
    else
      printf '  attente    : rendu attendu et décisions lus sur le ticket (lib.sh relecture-attente)\n'
    fi
    [ -n "$PARTIS_PRIS" ] && printf '  partis pris: %s, recopié tel quel\n' "$PARTIS_PRIS"
    printf '  grille     : scripts/design/grille-relecture.tsv\n'
    printf '  ensuite    : sous-agent « regard-neuf », dont le prompt est ce seul chemin :\n'
    printf 'SAISINE %s/%s\n' "$(racine_native)" "$SAISINE"
    exit 0
  fi

  # La planche : les paires sur le disque, puis `planche.py` depuis la racine — les chemins restent
  # RELATIFS de bout en bout, ce qui évite de traduire `/e/…` pour un Python Windows.
  printf '%s\n' "$PAIRES" >"$RACINE/$DOSSIER/paires.tsv"
  if ! PYTHON="$(python_du_depot)"; then
    printf 'relecture-visuelle.sh : aucun interpréteur Python — pas de planche.\n' >&2
    exit 1
  fi
  # PYTHONIOENCODING sur l'interpréteur lui-même : sous Windows sa sortie serait en cp1252, et ses
  # « ⚠ » arriveraient en mojibake (même piège que #141, dans l'autre sens).
  if ! (cd "$RACINE" && PYTHONIOENCODING=utf-8 "$PYTHON" scripts/design/planche.py --iid "$IID" \
    --paires "$DOSSIER/paires.tsv" --dossier "$DOSSIER" --sortie "$DOSSIER/planche.html"); then
    printf 'relecture-visuelle.sh : la planche n'\''a pas pu être écrite.\n' >&2
    exit 1
  fi
  # ELLE SURVIT AU WORKTREE. `/ticket-finish` ramasse le worktree juste après le merge, avant son
  # résumé : une planche nommée là, mais laissée ici, serait un lien mort au moment où on le lit. Elle
  # est donc recopiée sous le `.maestro/relecture/` du CLONE PRINCIPAL — le parent du répertoire git
  # commun, comme `worktree.sh` le trouve —, et c'est ce chemin-là que la dernière ligne rend. Un
  # seul fichier suffit : les captures y sont en `data:`. Best-effort : une copie ratée laisse la
  # planche du worktree, et le dit.
  PLANCHE="$DOSSIER/planche.html"
  COMMUN="$(git -C "$RACINE" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
  PRINCIPAL=""
  [ -n "$COMMUN" ] && PRINCIPAL="$(cd "$(dirname "$COMMUN")" 2>/dev/null && pwd)"
  if [ -n "$PRINCIPAL" ] && [ "$PRINCIPAL" != "$(cd "$RACINE" && pwd)" ]; then
    if mkdir -p "$PRINCIPAL/$DOSSIER" 2>/dev/null \
      && cp "$RACINE/$PLANCHE" "$PRINCIPAL/$PLANCHE" 2>/dev/null; then
      printf '  copie de travail : %s (ramassée avec le worktree)\n' "$PLANCHE"
      if command -v cygpath >/dev/null 2>&1; then PRINCIPAL="$(cygpath -m "$PRINCIPAL")"; fi
      printf 'PLANCHE %s/%s\n' "$PRINCIPAL" "$PLANCHE"
      exit 0
    fi
    printf '  ⚠ copie vers le clone principal impossible : la planche partira avec le worktree.\n' >&2
  fi
  printf 'PLANCHE %s/%s\n' "$(racine_native)" "$PLANCHE"
  exit 0
fi

if [ "$TSV" = 1 ]; then
  # `avant` en DERNIÈRE colonne : les quatre premières sont un contrat qu'un appelant lit par leur rang.
  printf '# route\turl\torigine\tfichiers\tavant\n'
  # Les états que la démo sait servir, en commentaire : un appelant machine les lit sans rejouer le
  # `sed` sur `demo.py`, et un lecteur de TSV qui ignore les `#` n'y voit rien de changé.
  printf '# scenarios\t%s\n' "${SCENARIOS_DEMO:-$SCENARIO_NOMINAL}"
  while IFS=$'\t' read -r route _cle origine fichiers; do
    [ -z "$route" ] && continue
    if [ "$route" = "-" ]; then
      printf -- '-\t-\t%s\t%s\t-\n' "$origine" "$fichiers"
    else
      printf '%s\thttp://localhost:%s%s\t%s\t%s\t%s\n' \
        "$route" "$PORT_UI" "$route" "$origine" "$fichiers" "$(avant_de "$route")"
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
CAPTURES="$(dossier_captures "$IID" "$SCENARIO")"
mkdir -p "$RACINE/$CAPTURES" 2>/dev/null
pose_projet

# `--demo` : bus mémoire, aucun Redis requis, et surtout des écrans PEUPLÉS — un poste vide ne montre
# pas le rendu qu'on vient d'écrire. `--no-browser` est obligatoire : sans lui le script ouvre sa propre
# fenêtre et arrête la stack dès qu'elle se ferme (#149), ce qui couperait l'API sous le navigateur
# qu'on pilote. Un état limite (#978) n'est passé que s'il est DEMANDÉ : sans `--scenario`, l'appel est
# celui d'avant, au caractère près.
ARGS_START=(--demo --no-browser)
ETAT_ANNONCE="$SCENARIO_NOMINAL"
if [ -n "$SCENARIO" ]; then
  ARGS_START+=(--scenario "$SCENARIO")
  ETAT_ANNONCE="$SCENARIO"
fi
dire "  stack      : démarrage (mode démo, état « $ETAT_ANNONCE », sans navigateur) — le premier passage construit l'UI"
if MAESTRO_PORT_API="$PORT_API" MAESTRO_PORT_UI="$PORT_UI" \
  bash "$RACINE/scripts/controltower/start.sh" "${ARGS_START[@]}"; then
  dire ""
  dire "  ✓ prête : http://localhost:$PORT_UI — état « $ETAT_ANNONCE »"
  prepare_avant
  dire ""
  dire "    à faire ensuite — poser le localStorage (guide vu, thème, projet actif) SUR CHAQUE ORIGINE"
  dire "    servie, ouvrir chaque écran dans les deux thèmes, capturer l'après et l'avant côte à côte"
  dire "    sous $CAPTURES/, puis l'état suivant (--scenario <nom>), et pour finir :"
  dire "        bash scripts/design/relecture-visuelle.sh --couverture $IID"
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
