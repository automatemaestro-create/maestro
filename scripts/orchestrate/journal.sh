#!/usr/bin/env bash
# Le ménage du journal d'orchestration — `.maestro/orchestrate/` (#198).
#
#   bash scripts/orchestrate/journal.sh gc           # purge les vieux runs, compacte leurs flux
#   bash scripts/orchestrate/journal.sh gc --check   # ce qui serait retiré, sans rien écrire
#
# --- Pourquoi -----------------------------------------------------------------------------------------
# `run.sh` crée un répertoire horodaté PAR LANCEMENT et n'en supprime aucun : ses deux `rm -rf` sont
# des renoncements (lancement détaché en échec, `--dry-run`), pas un ménage. Tant que le journal ne
# portait que des logs c'était indolore — 41 Ko pour un run entier. Le `<iid>.jsonl` de #176 change
# l'échelle : c'est le flux `stream-json` BRUT d'une session, un événement par ligne, non tronqué,
# et c'est lui qui décide désormais de la croissance.
#
# Deux gestes, un seul principe — ne jamais retirer ce qui peut encore servir :
#   - RÉTENTION : au-delà des N runs les plus récents, les plus anciens partent entièrement ;
#   - COMPACTION : dans les runs conservés, le `.jsonl` d'un ticket terminé est gzippé. Il garde
#     toute sa valeur de diagnostic (`zcat`, `zgrep`) sans son volume.
# S'y ajoute le ramassage des répertoires VIDES, que les sorties précoces de `run.sh` laissent
# derrière elles (plan vide, `queue.sh` en échec) : `mkdir -p` a déjà eu lieu, aucun `rm -rf` ne
# couvre ces chemins-là.
#
# --- Ce qui n'est JAMAIS retiré -----------------------------------------------------------------------
# Un run n'écrit pas de PID : rien ne dit « je tourne ». Comme `status.sh`, on le déduit de la date
# de la dernière écriture, et on tranche dans le sens prudent — un run qui a écrit il y a moins de
# MAESTRO_ORCHESTRATE_SILENCE est présumé VIVANT et épargné, même s'il est le plus ancien du lot.
# S'y ajoute le run courant, que `run.sh` nomme explicitement (`--courant`) : purger sous les pieds
# d'un run détaché lui ferait perdre son journal et laisserait `status.sh` sans rien à lire.
#
# Le ménage est BEST-EFFORT de bout en bout : son échec ne doit jamais empêcher un run de partir.
#
# --- Réglages -----------------------------------------------------------------------------------------
# MAESTRO_ORCHESTRATE_JOURNAL_RUNS   nombre de runs conservés (défaut 10)
# MAESTRO_ORCHESTRATE_JOURNAL_GC=0   désactive le passage automatique appelé par `run.sh`
# MAESTRO_ORCHESTRATE_SILENCE        délai au-delà duquel un run est présumé fini (défaut 900 s)

set -uo pipefail

ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RACINE="$(cd "$ICI/../.." && pwd)"
ORCH_DIR="$RACINE/.maestro/orchestrate"

GARDES="${MAESTRO_ORCHESTRATE_JOURNAL_RUNS:-10}"
# Un seuil illisible ou nul ferait tout retirer : on retombe sur le défaut plutôt que sur le pire.
[ "$GARDES" -ge 1 ] 2>/dev/null || GARDES=10
SEUIL_SILENCE="${MAESTRO_ORCHESTRATE_SILENCE:-900}"
[ "$SEUIL_SILENCE" -ge 0 ] 2>/dev/null || SEUIL_SILENCE=900

ok()     { printf '  ✓ %s\n' "$*"; }
ignore() { printf '  ~ %s\n' "$*"; }

usage() {
  cat <<'USAGE'
Le ménage du journal d'orchestration — .maestro/orchestrate/

  bash scripts/orchestrate/journal.sh gc [--check] [--auto] [--courant <run-id>]

`gc` ne garde que les N runs les plus récents (MAESTRO_ORCHESTRATE_JOURNAL_RUNS, défaut 10),
ramasse les répertoires de run vides et compacte le flux `<iid>.jsonl` des runs conservés.
Un run qui a écrit il y a moins de MAESTRO_ORCHESTRATE_SILENCE (défaut 900 s) est présumé en
cours : il n'est jamais touché, pas plus que celui désigné par `--courant`.

  --check      dit ce qui serait retiré, sans rien écrire
  --auto       ne parle que s'il a quelque chose à dire (appelé au démarrage d'un run)
  --courant    le run-id à épargner en toutes circonstances

MAESTRO_ORCHESTRATE_JOURNAL_GC=0 désactive le passage automatique.
USAGE
}

mtime() {
  stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || date -r "$1" +%s 2>/dev/null
}

# derniere_ecriture <run-dir> : la plus récente date de modification du répertoire ET de ce qu'il
# contient. Le répertoire seul ne suffit pas — sa date ne bouge qu'à la création ou au retrait d'un
# fichier, pas quand une session écrit dans son `.jsonl` pendant 45 minutes.
derniere_ecriture() {
  local dir="$1" f t max
  max="$(mtime "$dir")" || max=0
  [ -n "$max" ] || max=0
  for f in "$dir"/*; do
    [ -e "$f" ] || continue
    t="$(mtime "$f")" || continue
    [ -n "$t" ] || continue
    [ "$t" -gt "$max" ] && max="$t"
  done
  printf '%s' "$max"
}

# vide <run-dir> : 0 si le répertoire ne contient rien — ni fichier, ni sous-dossier, ni caché.
vide() {
  local f
  for f in "$1"/* "$1"/.[!.]*; do
    [ -e "$f" ] && return 1
  done
  return 0
}

# compacte_flux <run-dir> <check 0|1> : gzippe les `<iid>.jsonl` non vides du run, et rend le nombre
# de fichiers traités. N'est appelé que sur un run PRÉSUMÉ FINI : pendant un ticket, `run.sh` relit
# le `.jsonl` entier pour y détecter une limite d'usage et calculer l'attente avant reprise — le
# compacter sous ses pieds ferait passer une pause pour un échec.
compacte_flux() {
  local dir="$1" check="$2" f n=0
  command -v gzip >/dev/null 2>&1 || return 0
  for f in "$dir"/*.jsonl; do
    [ -s "$f" ] || continue
    if [ "$check" = 1 ]; then n=$((n + 1)); continue; fi
    # `-f` : un `.jsonl.gz` déjà là (run rejoué sous le même run-id) doit céder la place, sans quoi
    # gzip refuserait et laisserait le flux brut en place.
    gzip -f "$f" 2>/dev/null && n=$((n + 1))
  done
  printf '%s' "$n"
}

commande_gc() {
  local check=0 auto=0 courant=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --check) check=1 ;;
      --auto)  auto=1 ;;
      --courant) courant="${2:-}"; shift ;;
      -h | --help) usage; return 0 ;;
      *) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; return 2 ;;
    esac
    shift
  done

  [ -d "$ORCH_DIR" ] || {
    [ "$auto" = 1 ] || printf '\nAucun journal d'\''orchestration — rien à ramasser.\n\n'
    return 0
  }

  local maintenant dir id ecriture age
  maintenant="$(date +%s)"

  # Deux temps volontaires : on INVENTORIE d'abord (« date<TAB>run-id »), on décide ensuite. Un
  # `rm -rf` au fil du parcours réécrirait le glob que l'on est en train de lire.
  local inventaire="" vides="" epargnes=0 rapport=""
  for dir in "$ORCH_DIR"/*/; do
    [ -d "$dir" ] || continue
    dir="${dir%/}"
    id="$(basename "$dir")"
    ecriture="$(derniere_ecriture "$dir")"
    age=$((maintenant - ecriture))

    if [ -n "$courant" ] && [ "$id" = "$courant" ]; then
      epargnes=$((epargnes + 1))
      [ "$auto" = 1 ] || rapport="$rapport$(ignore "$id — run courant, jamais ramassé")"$'\n'
      continue
    fi
    if [ "$age" -lt "$SEUIL_SILENCE" ]; then
      epargnes=$((epargnes + 1))
      [ "$auto" = 1 ] || rapport="$rapport$(ignore "$id — écrit il y a moins de ${SEUIL_SILENCE}s, présumé en cours")"$'\n'
      continue
    fi

    if vide "$dir"; then
      vides="$vides$id"$'\n'
    else
      inventaire="$inventaire$ecriture"$'\t'"$id"$'\n'
    fi
  done

  # Les plus récents d'abord : le rang dans cette liste EST la décision de rétention.
  local classe="" rang=0 retires=0 gardes=0 compactes=0 n
  [ -n "$inventaire" ] && classe="$(printf '%s' "$inventaire" | sort -rn)"

  while IFS=$'\t' read -r ecriture id; do
    [ -n "$id" ] || continue
    rang=$((rang + 1))
    dir="$ORCH_DIR/$id"
    if [ "$rang" -le "$GARDES" ]; then
      gardes=$((gardes + 1))
      n="$(compacte_flux "$dir" "$check")"
      if [ "${n:-0}" -gt 0 ]; then
        compactes=$((compactes + n))
        rapport="$rapport$(ok "$id — $n flux de session compacté(s)")"$'\n'
      fi
      continue
    fi
    if [ "$check" = 1 ]; then
      retires=$((retires + 1))
      rapport="$rapport$(printf '  → %s à retirer — au-delà des %s runs conservés' "$id" "$GARDES")"$'\n'
      continue
    fi
    if rm -rf "$dir" 2>/dev/null; then
      retires=$((retires + 1))
      rapport="$rapport$(ok "$id retiré — au-delà des $GARDES runs conservés")"$'\n'
    else
      rapport="$rapport$(printf '  ✗ %s non retiré (dossier occupé ?)' "$id")"$'\n'
    fi
  done <<< "$classe"

  # Les répertoires vides ne comptent dans aucune rétention : ils ne portent rien à conserver.
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    if [ "$check" = 1 ]; then
      retires=$((retires + 1))
      rapport="$rapport$(printf '  → %s à retirer — répertoire vide' "$id")"$'\n'
      continue
    fi
    if rmdir "$ORCH_DIR/$id" 2>/dev/null; then
      retires=$((retires + 1))
      rapport="$rapport$(ok "$id retiré — répertoire vide")"$'\n'
    fi
  done <<< "$vides"

  # En `--auto` (démarrage d'un run) le silence est le cas normal : on ne parle que d'un retrait ou
  # d'une compaction. Personne n'est devant la console pour lire « rien à faire ».
  if [ "$auto" = 1 ] && [ "$retires" -eq 0 ] && [ "$compactes" -eq 0 ]; then
    return 0
  fi
  [ "$auto" = 1 ] || printf '\nMénage du journal — %s\n\n' "$ORCH_DIR"
  printf '%s' "$rapport"
  if [ "$check" = 1 ]; then
    printf 'Ménage (--check) : %s à retirer, %s conservé(s), %s flux à compacter — rien n'\''a été touché.\n' \
      "$retires" "$((gardes + epargnes))" "$compactes"
  else
    printf 'Ménage du journal : %s run(s) retiré(s), %s conservé(s), %s flux compacté(s).\n' \
      "$retires" "$((gardes + epargnes))" "$compactes"
  fi
  [ "$auto" = 1 ] || printf '\n'
  return 0
}

cmd="${1:-}"
[ "$#" -gt 0 ] && shift
case "$cmd" in
  gc)           commande_gc "$@" ;;
  -h | --help | '') usage ;;
  *) printf 'Sous-commande inconnue : %s\n\n' "$cmd" >&2; usage >&2; exit 2 ;;
esac
