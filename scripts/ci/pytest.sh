#!/usr/bin/env bash
# Lanceur pytest pour l'ITÉRATION SERRÉE — une suite, un test, dans le conteneur Linux (#405).
#
#   bash scripts/ci/pytest.sh tests/test_cycle_de_vie.py -q
#   bash scripts/ci/pytest.sh tests/test_worktree.py -k ensure -x
#   bash scripts/ci/pytest.sh tests/test_engine.py::test_boucle --natif
#
# POURQUOI IL EXISTE. #372 a mis le job pytest du filet dans un conteneur Linux, parce que les
# suites d'outillage sont faites à 100 % de sous-processus shell et qu'un fork y coûte ~800 ms sous
# Windows contre < 1 ms sous Linux. Mais ce régime n'était joignable QUE par `local.sh`, dont le
# périmètre est déduit du diff : viser une suite restait un `python -m pytest` natif. Mesuré le
# 2026-08-21 sur `tests/test_cycle_de_vie.py`, même poste, même suite :
#
#   natif Windows                 ~8 min
#   conteneur, sans xdist         1 min 51
#   conteneur, -n auto              21 s      ← ×18
#
# CE QU'IL N'EST PAS : un verdict. Il ne calcule aucun périmètre, n'applique aucun seuil de
# couverture et ne rejoue pas les autres jobs — c'est le rôle de `bash scripts/ci/local.sh`, à
# passer avant de pousser. Ici on itère : on choisit soi-même ce qui tourne, et on regarde la sortie
# défiler.
#
# CE QU'IL PARTAGE, et pourquoi ça compte : tout ce qui décide OÙ et COMMENT pytest s'exécute vient
# de `scripts/ci/pytest-regime.sh`, sourcé par le filet lui aussi. Régime, empreinte de l'image,
# point de montage, identité git, workers, garde-fous du venv : une seule implémentation. Deux
# copies auraient fini par diverger, et un lanceur qui n'exécute plus tout à fait ce que le filet
# prédit ramène exactement la phrase qu'on essaie de supprimer — « ça passe chez moi ».

set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# shellcheck disable=SC1091  # le lint appelle shellcheck fichier par fichier (#285) : la source
# n'est pas sur sa ligne de commande, donc `source=` ne serait pas suivi de toute façon.
. "$RACINE/scripts/ci/pytest-regime.sh"

if [ -t 1 ]; then
  C_Y=$'\033[33m'; C_R=$'\033[31m'; C_D=$'\033[2m'; C_0=$'\033[0m'
else
  C_Y=''; C_R=''; C_D=''; C_0=''
fi

usage() {
  cat <<USAGE
Lanceur pytest — une suite, un test, dans le conteneur Linux (#405).

  bash scripts/ci/pytest.sh [--conteneur|--natif] <arguments pytest…>

Options du lanceur (elles se lisent AVANT les arguments pytest, et s'arrêtent au premier
argument inconnu — tout le reste part tel quel à pytest) :
  --conteneur   Exige le conteneur Linux : ÉCHOUE si Docker ne répond pas, au lieu de retomber.
  --natif       Force le venv du poste (.venv/), l'ancien régime.
  -h, --help    Cette aide.

Par défaut : le conteneur si le démon Docker répond, sinon le venv du poste — et le lanceur
DIT toujours lequel des deux a joué.

Exemples :
  bash scripts/ci/pytest.sh tests/test_cycle_de_vie.py -q
  bash scripts/ci/pytest.sh tests/test_worktree.py -k ensure -x
  bash scripts/ci/pytest.sh tests/test_engine.py::test_boucle --natif

Le parallélisme (« -n auto ») est ajouté d'office DANS LE CONTENEUR — c'est l'essentiel du
gain, et le drapeau même de la CI. Jamais en natif : y démarrer les workers coûte ~5,5 s, soit
plus qu'une suite applicative ciblée (test_engine.py : 6,3 s en série, 37,5 s à -n 8).
Il n'est pas ajouté non plus si tes arguments portent déjà -n/--numprocesses/-p no:xdist, ni
s'ils disent que tu veux REGARDER tourner : --pdb, -s, --capture=no.

Où jouer, selon la suite (docs/10 §8.4bis) :
  outillage (elle nomme un script du dépôt)   → conteneur, ×20
  applicative (elle n'en nomme aucun)         → natif, le conteneur n'y gagne rien

Ce lanceur ne rend PAS de verdict : avant de pousser, c'est « bash scripts/ci/local.sh » qui
rejoue les jobs du pipeline (lint compris) sur le périmètre du diff.
USAGE
}

# --- Options du lanceur -----------------------------------------------------------------------------
# On ne consomme QUE les options connues, depuis la gauche, et le premier jeton inconnu clôt la
# lecture : `-q`, `-k`, `tests/…` partent tous à pytest sans avoir à écrire `--`. C'est la règle la
# moins surprenante ici, où l'immense majorité des arguments sont ceux de pytest et non les nôtres.
#
# SC2034 est désactivé pour `PYTEST_REGIME_DEMANDE` : elle est bien LUE, mais dans
# `pytest-regime.sh`, que le lint n'a pas sur sa ligne de commande — il l'appelle fichier par
# fichier (#285), donc il ne peut pas le savoir.
# shellcheck disable=SC2034
while [ $# -gt 0 ]; do
  case "$1" in
    --conteneur) PYTEST_REGIME_DEMANDE=conteneur ;;
    --natif) PYTEST_REGIME_DEMANDE=natif ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) break ;;
  esac
  shift
done

# Sans argument, pytest ramasserait TOUTE la suite. Ce n'est pas ce qu'on vient chercher ici, et ça
# se paie en minutes : mieux vaut l'aide qu'une collecte surprise. Qui veut tout jouer le demande —
# `pytest.sh tests/` — ou passe par le filet, dont c'est le métier (`local.sh --complet`).
if [ $# -eq 0 ]; then
  usage
  exit 2
fi

# --- Où l'on joue ------------------------------------------------------------------------------------
if ! choisit_regime_pytest; then
  printf '%spytest : %s%s\n' "$C_R" "$DETAIL" "$C_0" >&2
  exit 2
fi

exe=""
if [ "$PYTEST_REGIME" = natif ]; then
  if ! verifie_venv_natif; then
    printf '%spytest : %s%s\n' "$C_R" "$DETAIL" "$C_0" >&2
    exit 2
  fi
  exe="$PYTEST_PYTHON"
fi

# --- Ce qu'on ajoute aux arguments ------------------------------------------------------------------
# Le parallélisme est le gros du gain (1 min 51 → 21 s sur test_cycle_de_vie), donc il est posé
# d'office — mais JAMAIS contre une intention explicite. Deux familles s'y opposent :
#   · un choix déjà fait (-n, --numprocesses, -p no:xdist) — le sien gagne, toujours ;
#   · une intention de REGARDER (--pdb, -s, --capture=no) — xdist capture et entrelace la sortie
#     par worker, ce qui vide ces trois options de leur sens. pytest ne s'en plaint pas (vérifié :
#     `-n auto --pdb` passe sans broncher), il les rend juste inutiles — le pire des deux.
ajoute_n=1
for argument in "$@"; do
  case "$argument" in
    -n | -n[0-9]* | -nauto | --numprocesses | --numprocesses=* | --pdb | -s | --capture=no)
      ajoute_n=0
      break
      ;;
    # `-p no:xdist` voyage en DEUX jetons : on reconnaît le second, jamais `-p` seul — qui sert
    # aussi à charger d'autres plugins et n'a alors rien à voir avec le parallélisme.
    no:xdist)
      ajoute_n=0
      break
      ;;
  esac
done

args=()
# Le conteneur n'a pas de TTY (`docker run -it` est refusé depuis un Git Bash : « the input device
# is not a TTY »), donc pytest y voit un tube et éteint la couleur. On la rallume quand NOTRE sortie
# est un terminal — l'argument passe avant les siens, si bien qu'un `--color=no` explicite gagne.
[ -t 1 ] && args+=(--color=yes)
# DANS LE CONTENEUR SEULEMENT, et c'est la règle déjà écrite pour le filet : démarrer les workers
# coûte ~5,5 s, soit plus qu'une suite applicative ciblée. Mesuré ici sur `tests/test_engine.py` en
# natif — 6,3 s en série contre 37,5 s avec `-n 8` : le parallélisme d'office rendait SIX FOIS plus
# lent le cas même qu'il devait servir. Dans le conteneur la question ne se pose pas (`-n auto`,
# 46 s contre 177 s à `-n 4` sur les six suites du périmètre) ; en natif, une suite d'outillage assez
# grosse pour rentabiliser des workers est de toute façon celle qu'il faut jouer AILLEURS.
if [ "$ajoute_n" = 1 ] && [ "$PYTEST_REGIME" = conteneur ]; then
  args+=(-n "$(workers_pytest)")
fi
args+=("$@")

# --- Annonce, puis on joue ---------------------------------------------------------------------------
# OÙ ça joue se dit AVANT et non après : sur une suite d'outillage l'écart est d'un facteur vingt,
# donc qui voit « NATIF » sait tout de suite qu'il a le temps d'aller chercher un café — et pourquoi.
if [ "$PYTEST_REGIME" = conteneur ]; then
  printf '%spytest — conteneur Linux (%s)%s\n' "$C_D" "$PYTEST_IMAGE" "$C_0"
  pytest_conteneur "$PYTEST_IMAGE" "${args[@]}"
else
  # Le natif SUBI et le natif VOULU ne se disent pas de la même façon. Le premier est un repli qu'il
  # faut pouvoir corriger — on le crie, et on nomme sa cause. Le second est un choix légitime : sur
  # une suite APPLICATIVE le natif est la bonne réponse (6,3 s, contre ~6 s de seul démarrage pour
  # le conteneur), et l'avertir d'un facteur vingt qui ne le concerne pas est du bruit qui apprend à
  # ne plus lire les avertissements.
  if [ -n "$PYTEST_REGIME_MOTIF" ]; then
    printf '%spytest — NATIF : %s%s\n' "$C_Y" "$PYTEST_REGIME_MOTIF" "$C_0"
    printf '%s  sur une suite d'\''outillage, compter ~20× le temps du conteneur (#372)%s\n' \
      "$C_D" "$C_0"
  else
    printf '%spytest — natif (--natif)%s\n' "$C_D" "$C_0"
  fi
  ( cd "$RACINE" && "$exe" -m pytest "${args[@]}" )
fi
