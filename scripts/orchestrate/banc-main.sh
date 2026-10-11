#!/usr/bin/env bash
# Le banc entier sur `main` intégrée, une fois par lot (#1462, parent #1460, docs/47).
#
#   bash scripts/orchestrate/banc-main.sh                     # hors run : le passage sur origin/main
#   bash scripts/orchestrate/banc-main.sh --sortie <dossier>  # où écrire banc.etat, banc.txt, banc.log
#   bash scripts/orchestrate/banc-main.sh --forcer            # joue même si main n'a pas bougé
#   bash scripts/orchestrate/banc-main.sh --retirer           # retire la copie dédiée
#
# --- Pourquoi un passage par lot ---------------------------------------------------------------------
# Le banc des scénarios coûte du vrai modèle : un passage complet ≈ 13,5 $ et ≈ 1 h 25. Le 08/10, S12
# a été joué six fois par trois tickets du même jalon, chacun sur sa branche. Un passage sur `main`
# intégrée, une fois par lot, vérifie en plus ce qui sera LIVRÉ : le résultat des merges, pas une
# branche — le principe de la CI (#716). C'est ce filet qui permet au lot 4 de #1460 de retirer le
# banc des tickets dont le changement ne touche pas ce que le modèle reçoit ou fait (docs/47).
#
# Le pilote (`run.sh`) l'appelle après son drain final ; une personne l'appelle à la main les
# semaines où l'on merge en interactif. Même script, même copie, même verdict.
#
# --- Ce qu'il fait, dans l'ordre ---------------------------------------------------------------------
#   1. Le sha joué : `origin/main` (après un `fetch`, sauf `--sans-fetch`), ou `--sha`.
#   2. La DÉCISION, lue dans l'historique du banc (#1461) contre l'historique de main
#      (`maestro.scenarios.sur_main decider`) : on joue seulement si main a bougé sur le chemin des
#      scénarios (`GL_BANC_CHEMINS`, lib.sh — jamais recopié ici) depuis le dernier passage complet
#      sur main. Sinon on s'ABSTIENT EN LE DISANT. `--forcer` passe outre.
#   3. La COPIE DÉDIÉE (`worktree.sh main-banc`) : un worktree détaché sur ce sha, ses ports
#      (`MAESTRO_BANC_PORTS`, 8400:3400 — hors de la plage des tickets, 8001-8100, et de celle des
#      avants, +200) et son espace Redis (le nom du worktree, #1164). Ni le clone principal — la
#      stack de la personne doit rester libre, et Next.js n'admet qu'un serveur de dev par
#      `apps/web` —, ni le worktree d'un ticket.
#   4. Le PASSAGE, par le lanceur DE LA COPIE : `start.sh --etat-banc --rejouer --no-browser`. Il
#      remet le banc à neuf, sert la stack, joue tous les scénarios et sauve l'état — `start.sh
#      --etat-banc` le rouvre ensuite dans la copie. La stack est ARRÊTÉE après, quoi qu'il arrive.
#   5. Le VERDICT (`maestro.scenarios.sur_main verdict`) : un rouge nomme les PR mergées entre son
#      dernier vert sur main et le sha joué. On NOMME, on ne crée aucun ticket (comme #611).
#
# --- Une abstention n'est jamais un vert ---------------------------------------------------------------
# `start.sh` sort en `0` sur un passage vert comme rouge (« état sauvé ») : le verdict se lit dans le
# rapport, jamais dans le code. Et un processus tué sort en `1` sous Windows : c'est le TÉMOIN que le
# banc écrit en dernier geste qui dit qu'il est allé au bout (#1365). Pas de témoin — passage tué,
# Redis absent, API muette, copie impossible à monter : le banc est INJOUABLE, et c'est une
# abstention. Un scénario empêché (API, fournisseur, limite d'usage) aussi (docs/47 §5).
#
# --- Ce qu'il laisse ------------------------------------------------------------------------------------
# Dans `--sortie` (le répertoire du run pour le pilote ; `.maestro/banc-main/<horodatage>/` sinon) :
#   banc.etat   une ligne : état <TAB> sha <TAB> début <TAB> fin <TAB> motif — `en-cours` pendant le
#               passage, puis `vert`, `rouge` ou `abstention`. C'est ce que lit `status.sh`
#   banc.txt    le verdict en mots : chaque rouge et ses PR, chaque empêchement
#   banc.log    la sortie entière du montage et du passage
# Sur la sortie standard, quelques lignes seulement : ce qui se joue, puis le verdict.
#
# Codes : 0 vert · 1 rouge · 3 abstention (main n'a pas bougé, banc injouable, empêchement) · 2 usage.

set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$RACINE/scripts/gitlab/lib.sh"

usage() {
  cat <<'USAGE'
Le banc entier sur main intégrée, une fois par lot — hors run ou appelé par le pilote.

  bash scripts/orchestrate/banc-main.sh [options]

Options :
  --sortie <dossier>  Où écrire banc.etat, banc.txt et banc.log. Défaut :
                      .maestro/banc-main/<horodatage>/.
  --sha <sha>         Le sha de main à jouer. Défaut : origin/main, après un fetch.
  --sans-fetch        Ne rafraîchit pas origin/main avant de le lire.
  --forcer            Joue même si main n'a pas bougé sur le chemin des scénarios.
  --retirer           Retire la copie dédiée (worktree.sh main-banc --retirer) et s'arrête.
  -h, --help          Cette aide.

Vrai modèle, ≈ 13,5 $ et ≈ 1 h 25 pour le banc entier. Ports de la copie : MAESTRO_BANC_PORTS
(défaut 8400:3400). Codes : 0 vert · 1 rouge · 3 abstention · 2 usage.
USAGE
}

SORTIE=""
SHA=""
FETCH=1
FORCER=0
RETIRER=0
while [ $# -gt 0 ]; do
  case "$1" in
    --sortie) SORTIE="${2:-}"; shift ;;
    --sha) SHA="${2:-}"; shift ;;
    --sans-fetch) FETCH=0 ;;
    --forcer) FORCER=1 ;;
    --retirer) RETIRER=1 ;;
    -h | --help) usage; exit 0 ;;
    *) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

WORKTREE_SH="$RACINE/scripts/git/worktree.sh"
if [ "$RETIRER" = 1 ]; then
  bash "$WORKTREE_SH" main-banc --retirer
  exit $?
fi

PORTS="${MAESTRO_BANC_PORTS:-8400:3400}"
PORT_API="${PORTS%%:*}"
PORT_UI="${PORTS##*:}"
case "$PORT_API:$PORT_UI" in
  *[!0-9:]* | :* | *:) printf 'banc-main.sh : MAESTRO_BANC_PORTS attend <api>:<ui> (reçu : %s)\n' "$PORTS" >&2; exit 2 ;;
esac

# L'interpréteur du venv du dépôt (CLAUDE.md : le `python` du poste n'a pas les dépendances).
# `MAESTRO_BANC_PYTHON` le remplace — pour la suite, dont le dépôt jetable n'a pas de venv.
PYTHON="${MAESTRO_BANC_PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidat in "$RACINE/.venv/Scripts/python.exe" "$RACINE/.venv/bin/python"; do
    if [ -x "$candidat" ]; then PYTHON="$candidat"; break; fi
  done
fi

DEBUT="$(date +%s)"
[ -n "$SORTIE" ] || SORTIE="$RACINE/.maestro/banc-main/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$SORTIE" 2>/dev/null || { printf 'banc-main.sh : %s impossible à créer\n' "$SORTIE" >&2; exit 2; }
ETAT_F="$SORTIE/banc.etat"
TEXTE_F="$SORTIE/banc.txt"
JOURNAL_F="$SORTIE/banc.log"
: >"$JOURNAL_F"

# ecrit_etat <état> <motif> : la ligne que `status.sh` lit, réécrite en entier (tmp puis mv) —
# un lecteur ne voit jamais une ligne à moitié écrite. La fin vaut « - » pendant le passage : un
# champ vide se perdrait à la lecture (`read` fusionne deux tabulations).
ecrit_etat() {
  local fin="-"
  [ "$1" = "en-cours" ] || fin="$(date +%s)"
  printf '%s\t%s\t%s\t%s\t%s\n' "$1" "${SHA:-?}" "$DEBUT" "$fin" "$2" >"$ETAT_F.tmp" &&
    mv -f "$ETAT_F.tmp" "$ETAT_F"
}

# abstention <motif> : dite, écrite, et le code 3. Jamais un vert.
abstention() {
  ecrit_etat abstention "$1"
  printf 'Banc sur main %s : abstention — %s\n' "${SHA:0:7}" "$1" >"$TEXTE_F"
  printf '  banc sur main : abstention — %s\n' "$1"
  exit 3
}

# --- 1. Le sha joué -------------------------------------------------------------------------------------
if [ -z "$SHA" ]; then
  if [ "$FETCH" = 1 ]; then
    GIT_TERMINAL_PROMPT=0 git -C "$RACINE" fetch origin main >>"$JOURNAL_F" 2>&1
  fi
  SHA="$(git -C "$RACINE" rev-parse --verify --quiet 'origin/main^{commit}' 2>/dev/null)" || SHA=""
  [ -n "$SHA" ] || abstention "origin/main introuvable dans $RACINE — rien à jouer"
fi
ecrit_etat en-cours "décision"

[ -n "$PYTHON" ] || abstention "banc injouable : aucun python du venv sous $RACINE/.venv (bash scripts/setup.sh)"

# --- 2. La décision -------------------------------------------------------------------------------------
if [ "$FORCER" = 1 ]; then
  MOTIF_JEU="passage forcé (--forcer)"
else
  code=0
  decision="$(cd "$RACINE" && PYTHONIOENCODING=utf-8 "$PYTHON" -m maestro.scenarios.sur_main decider \
    --cible "$SHA" --chemins "$GL_BANC_CHEMINS" --depot "$RACINE" 2>>"$JOURNAL_F")" || code=$?
  MOTIF_JEU="${decision#*$'\t'}"
  case "$code" in
    0) ;;
    3) abstention "$MOTIF_JEU" ;;
    *) abstention "banc injouable : la décision n'a pas pu se prendre (code $code) — voir $JOURNAL_F" ;;
  esac
fi
printf '  banc sur main %s : on joue — %s\n' "${SHA:0:7}" "$MOTIF_JEU"

# --- 3. La copie dédiée ---------------------------------------------------------------------------------
ecrit_etat en-cours "montage de la copie dédiée"
code=0
sortie="$(bash "$WORKTREE_SH" main-banc --sha "$SHA" 2>&1)" || code=$?
printf '%s\n' "$sortie" >>"$JOURNAL_F"
COPIE="$(printf '%s\n' "$sortie" | sed -n 's/^COPIE //p' | tail -n 1)"
COPIE="${COPIE//\\//}"
if [ "$code" -ne 0 ] || [ -z "$COPIE" ] || [ ! -d "$COPIE" ]; then
  abstention "banc injouable : la copie dédiée n'a pas pu être montée (code $code) — voir $JOURNAL_F"
fi

# --- 4. Le passage --------------------------------------------------------------------------------------
# Le lanceur DE LA COPIE : c'est le code de main qui se joue, lanceur compris. Le témoin est retiré
# AVANT : celui d'un passage précédent ferait lire son rapport pour celui-ci, si ce passage-ci
# tombait avant d'avoir retiré le sien (`start.sh` ne le fait qu'au moment de jouer).
LANCEUR="$COPIE/scripts/controltower/start.sh"
TEMOIN="$COPIE/.maestro/controltower/$PORT_API-$PORT_UI/banc.temoin"
rm -f "$TEMOIN" 2>/dev/null
ecrit_etat en-cours "passage du banc entier — vrai modèle, ≈ 1 h 25 (journal : $JOURNAL_F)"
printf '  banc sur main : passage du banc entier dans %s (API :%s) — vrai modèle, ≈ 1 h 25\n' \
  "$COPIE" "$PORT_API"
printf '                  journal : %s\n' "$JOURNAL_F"
code=0
MAESTRO_PORT_API="$PORT_API" MAESTRO_PORT_UI="$PORT_UI" \
  bash "$LANCEUR" --etat-banc --rejouer --no-browser >>"$JOURNAL_F" 2>&1 </dev/null || code=$?
# La stack s'arrête quoi qu'il arrive : elle tiendrait ses ports, son quota et une UI que personne
# ne regarde. L'arrêt solde ce qui serait encore en vol (#700).
MAESTRO_PORT_API="$PORT_API" MAESTRO_PORT_UI="$PORT_UI" \
  bash "$LANCEUR" --stop >>"$JOURNAL_F" 2>&1 </dev/null || true

if [ ! -s "$TEMOIN" ]; then
  abstention "banc injouable : le passage n'est pas allé au bout (code $code, aucun témoin) — voir $JOURNAL_F"
fi
DOSSIER="$(head -n 1 "$TEMOIN" | tr -d '\r')"
DOSSIER="${DOSSIER//\\//}"
case "$DOSSIER" in
  /* | [A-Za-z]:/*) ;;
  *) DOSSIER="$COPIE/$DOSSIER" ;;
esac
RAPPORT="$DOSSIER/rapport.json"
[ -s "$RAPPORT" ] || abstention "banc injouable : rapport introuvable ($RAPPORT)"

# --- 5. Le verdict --------------------------------------------------------------------------------------
code=0
(cd "$RACINE" && PYTHONIOENCODING=utf-8 "$PYTHON" -m maestro.scenarios.sur_main verdict \
  --cible "$SHA" --rapport "$RAPPORT" --chemins "$GL_BANC_CHEMINS" --depot "$RACINE" \
  >"$TEXTE_F" 2>>"$JOURNAL_F") || code=$?
case "$code" in
  0) etat=vert ;;
  1) etat=rouge ;;
  3) etat=abstention ;;
  *) abstention "banc injouable : verdict illisible (code $code) — rapport $RAPPORT" ;;
esac
ecrit_etat "$etat" "$(head -n 1 "$TEXTE_F" | tr -d '\r')"
sed 's/^/  /' "$TEXTE_F"
case "$etat" in
  vert) exit 0 ;;
  rouge) exit 1 ;;
  *) exit 3 ;;
esac
