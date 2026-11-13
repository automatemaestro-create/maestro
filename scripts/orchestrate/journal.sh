#!/usr/bin/env bash
# Le journal d'orchestration — `.maestro/orchestrate/` : son ménage (#198) et sa lecture (#235).
#
#   bash scripts/orchestrate/journal.sh gc           # purge les vieux runs, compacte leurs flux
#   bash scripts/orchestrate/journal.sh gc --check   # ce qui serait retiré, sans rien écrire
#   bash scripts/orchestrate/journal.sh refus        # ce qui a été refusé au dernier run
#   bash scripts/orchestrate/journal.sh refus --tous # le même agrégat, sur tout le journal
#   bash scripts/orchestrate/journal.sh audit        # où passe le temps du dernier run
#   bash scripts/orchestrate/journal.sh audit --tous # le même relevé, sur tout le journal
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
# --- Lire les refus de permission (#235, parent #232) --------------------------------------------------
# `docs/10 §11.7` pose le principe : l'`allow` de `settings.run.json` se complète À PARTIR DES REFUS
# OBSERVÉS, jamais à l'aveugle. Le principe n'était outillé que par ticket — `<iid>.resultat.txt`
# (#180) dit les refus d'UNE session. La question qu'on se pose après un run est l'autre : « qu'est-ce
# qui a été refusé, en tout ? ». Y répondre demandait de dépouiller 16 JSON à la main, ce qu'aucune
# instruction ne fera deux fois : `refus` en fait une commande.
#
# Ce que l'agrégat sait, et qu'une lecture ticket par ticket rate :
#   - le POIDS d'une forme — six refus `env` sur cinq sessions ne se voient pas un par un ;
#   - le MAILLON FAIBLE d'une commande composée. Le CLI découpe sur `&&`, `;` et `|` et exige chaque
#     morceau : on compte donc chaque maillon POUR LUI-MÊME, sans quoi `grep … | tail -8` serait
#     rangé sous « grep » alors que la ligne entière est tombée pour ce seul mot ;
#   - les refus que RIEN dans le dépôt ne lèvera (écriture sous `.claude/`, #229) — les signaler
#     évite d'aller ajouter une règle qui ne servira pas.
# Lecture seule, en `awk`, sans `jq` ni Python — le pilote est un script shell, il le reste (#180).
#
# --- Pourquoi un CLASSEMENT et pas seulement un comptage (#307) ----------------------------------------
# L'agrégat disait COMBIEN et DE QUOI, jamais POURQUOI — si bien qu'on a continué de lire chaque refus
# comme un trou d'allowlist, gisement que #232 avait pourtant fini d'exploiter. Mesure du 2026-08-09
# sur les onze runs du journal : les sept commandes les plus refusées (`echo`, `cd`, `tail`, `cat`,
# `head`, `grep`, `sed`) sont TOUTES dans l'`allow`. Ce qui les faisait tomber était ailleurs — la
# CIBLE, hors du répertoire de travail de la session (règle #234, §8.5).
#
# D'où trois familles, choisies sur le GESTE qu'elles appellent, pas sur la forme du refus :
#   - TROU D'ALLOWLIST     un maillon qu'aucune règle ne couvre  → `settings.run.json` ;
#   - ÉCHAPPÉE DE CHEMIN   tous les maillons couverts, mais la cible sort du worktree
#                          → le prompt de `run.sh` et ce que le dépôt dicte, jamais la liste :
#                            une règle de PRÉFIXE ne borne pas une cible ;
#   - BLOCAGE DUR .claude/ refus du CLI, en amont de la liste (#229, mesuré par #238) → rien.
# S'y ajoutent deux issues qui ne touchent pas davantage à la liste : la FORME immatchable (saut de
# ligne, `$(…)`, heredoc — déjà nommée plus bas) et le REFUS VOULU, qu'une règle `ask`/`deny` du
# dépôt réclame et que personne ne peut approuver en autonome. Les compter à part évite de les voir
# grossir « inclassé », d'où l'on repart chercher une règle manquante qui n'existe pas.
#
# Un refus ne compte que pour UNE famille, et L'ORDRE DE DÉCISION est le contenu du classement :
# `.claude/`, refus voulu, trou d'allowlist, échappée de chemin, forme. On ne conclut à l'échappée
# que si rien d'autre n'explique le refus — ce qui rend la thèse de #307 plus difficile à établir,
# pas plus facile. Le classement est REJOUABLE parce qu'il lit les règles là où elles vivent
# (`settings.run.json` ∪ `.claude/settings.json`) au lieu d'en figer une copie ici — avec ce
# corollaire : ce sont les règles D'AUJOURD'HUI, donc sur un vieux run « inclassé » dit souvent
# « déjà instruit depuis ». C'est le dernier run qui se lit pour agir.
#
# --- La famille `.claude/` se LIT, elle ne se redéfinit pas (#611, chantier #608) ----------------------
# `refus --claude` rend la même famille en TSV — une ligne par ticket, « iid <TAB> refus <TAB> exemple
# de cible » — pour le pilote, qui la nomme en terminant son run. C'est un MODE DE SORTIE et pas une
# seconde lecture : il se branche sur `famille`, une fois le classement rendu, si bien que la question
# « ce refus vient-il du blocage dur ? » n'a toujours qu'une réponse dans le dépôt. La redéfinir chez
# l'appelant — un `grep .claude/` sur les `<iid>.json` — aurait marché le premier jour et divergé le
# suivant, et c'est la version silencieuse (celle du pilote) qui se serait trompée sans le dire.
#
# Rien à imprimer = RIEN, et le code 3 : le silence est le cas nominal d'un run sans résidu, et un
# appelant best-effort doit pouvoir distinguer « aucun » (3) de « je n'ai pas pu lire » (1, 2).
#
# --- Réglages -----------------------------------------------------------------------------------------
# MAESTRO_ORCHESTRATE_JOURNAL_RUNS   nombre de runs conservés (défaut 10)
# MAESTRO_ORCHESTRATE_JOURNAL_GC=0   désactive le passage automatique appelé par `run.sh`
# MAESTRO_ORCHESTRATE_SILENCE        délai au-delà duquel un run est présumé fini (défaut 900 s)

set -uo pipefail

ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RACINE="$(cd "$ICI/../.." && pwd)"
SETTINGS_RUN="$RACINE/scripts/orchestrate/settings.run.json"
SETTINGS_DEPOT="$RACINE/.claude/settings.json"

# La lecture des blocs de permissions et le matching des règles sont PARTAGÉS avec `ecart-run.sh`
# (#789) : deux verbes qui posent la même question au même corpus doivent en tirer le même verdict,
# faute de quoi l'un enverrait élargir une allowlist que l'autre juge suffisante.
# shellcheck source=scripts/orchestrate/permissions.sh
. "$ICI/permissions.sh"

# Le journal est celui du CLONE PRINCIPAL, d'où qu'on le lise (#307). C'est là que `run.sh` est
# lancé et donc là qu'il écrit ; depuis un worktree, `$RACINE/.maestro/orchestrate` désigne un
# répertoire vide, et la seule façon d'atteindre le vrai journal serait un CHEMIN ABSOLU — que la
# couche permissions refuse justement à une session (§11.7). L'outil de mesure produirait le refus
# qu'il est censé mesurer. `--git-common-dir` rend `.git` dans le clone principal et un chemin
# ABSOLU depuis un worktree ; hors dépôt git (le dépôt jetable des tests) on garde la racine du
# script, qui EST alors le clone principal.
racine_principale() {
  local commun
  commun="$(git -C "$RACINE" rev-parse --git-common-dir 2>/dev/null)" || commun=""
  case "$commun" in
    "" | .git) printf '%s' "$RACINE" ;;
    /* | [A-Za-z]:[/\\]*) (cd "$commun/.." 2>/dev/null && pwd) || printf '%s' "$RACINE" ;;
    *) (cd "$RACINE/$commun/.." 2>/dev/null && pwd) || printf '%s' "$RACINE" ;;
  esac
}
ORCH_DIR="$(racine_principale)/.maestro/orchestrate"

GARDES="${MAESTRO_ORCHESTRATE_JOURNAL_RUNS:-10}"
# Un seuil illisible ou nul ferait tout retirer : on retombe sur le défaut plutôt que sur le pire.
[ "$GARDES" -ge 1 ] 2>/dev/null || GARDES=10
SEUIL_SILENCE="${MAESTRO_ORCHESTRATE_SILENCE:-900}"
[ "$SEUIL_SILENCE" -ge 0 ] 2>/dev/null || SEUIL_SILENCE=900

# Au-delà de ce délai HORS DE TOUT APPEL, `audit` nomme le trou au lieu de le fondre dans le total.
# 120 s sépare ce qu'on peut lire (une réflexion longue, une attente de limite d'usage) de ce qu'on
# ne peut pas (les centaines de pauses de quelques secondes entre deux appels, dont la liste ne
# serait qu'un second flux brut).
SEUIL_TROU_AUDIT="${MAESTRO_AUDIT_TROU:-120}"
[ "$SEUIL_TROU_AUDIT" -ge 1 ] 2>/dev/null || SEUIL_TROU_AUDIT=120

# Ce qu'une image pèse dans le contexte, pour le poids des résultats d'outils (#1465) : le plafond
# d'une capture d'écran, autour de 1 600 tokens. Son base64, compté comme du texte, la ferait peser
# cent fois plus — et la relecture visuelle passerait pour le premier poste d'un run.
IMAGE_TOK_AUDIT=1600

ok()     { printf '  ✓ %s\n' "$*"; }
ignore() { printf '  ~ %s\n' "$*"; }

usage() {
  cat <<'USAGE'
Le journal d'orchestration — .maestro/orchestrate/

  bash scripts/orchestrate/journal.sh gc [--check] [--auto] [--courant <run-id>]
  bash scripts/orchestrate/journal.sh refus [<run-id>… | --tous] [--claude]
  bash scripts/orchestrate/journal.sh audit [<run-id> | --tous]
  bash scripts/orchestrate/journal.sh origine <iid>

`gc` ne garde que les N runs les plus récents (MAESTRO_ORCHESTRATE_JOURNAL_RUNS, défaut 10),
ramasse les répertoires de run vides et compacte le flux `<iid>.jsonl` des runs conservés.
Un run qui a écrit il y a moins de MAESTRO_ORCHESTRATE_SILENCE (défaut 900 s) est présumé en
cours : il n'est jamais touché, pas plus que celui désigné par `--courant`.

  --check      dit ce qui serait retiré, sans rien écrire
  --auto       ne parle que s'il a quelque chose à dire (appelé au démarrage d'un run)
  --courant    le run-id à épargner en toutes circonstances

MAESTRO_ORCHESTRATE_JOURNAL_GC=0 désactive le passage automatique.

`refus` classe les refus par FAMILLE — trou d'allowlist, échappée de chemin, blocage dur
`.claude/`, refus voulu, forme immatchable — puis les agrège par outil et par commande, chaque
maillon d'une chaîne comptant pour lui-même. Sans argument : le dernier run qui en porte.
Lecture seule ; les instruire au cas par cas se fait en docs/10-workflow-git.md §11.7.

  --claude     la seule famille « blocage dur .claude/ », en TSV et par TICKET :
               « iid <TAB> refus <TAB> exemple de cible ». Rien (code 3) s'il n'y en a
               aucun. C'est ce que le pilote lit pour nommer les résidus en fin de run
               (#611) — la famille se LIT ici, elle ne se redéfinit pas ailleurs.

`audit` dit OÙ PASSE LE TEMPS d'un run : part du mur passée sous outil (ticket par ticket, puis
pour le run), détail par outil et par forme de commande Bash, palmarès des appels les plus longs,
pré-vol de /ticket-start, temps mort et commandes rejouées DANS UN MÊME TICKET. Puis OÙ PASSENT
SES TOKENS (#1465) : tours et tokens par ticket (entrée, cache relu, cache écrit, sortie), leur part
par famille d'appels, le poids du contexte que les résultats d'outils font relire, les fichiers les
plus relus. Sans argument : le dernier run qui porte un flux — un run EN COURS se lit comme un
autre. Hors ligne, lecture seule. Il MESURE, il ne corrige pas : chaque remède est son propre ticket
(portée de #495).

  MAESTRO_AUDIT_TROU   délai au-delà duquel un temps hors appel est nommé (défaut 120 s)

Le journal lu est TOUJOURS celui du clone principal, y compris appelé depuis un worktree : c'est
là que le pilote écrit, et y renvoyer par un chemin absolu ferait refuser la lecture (§11.7).

  <run-id>     un run précis, plutôt que le dernier. `refus` en accepte PLUSIEURS et les
               agrège : un run repris et le run qu'il reprend sont deux répertoires
               pour un même travail (#611)
  --tous       tout le journal, tous runs confondus

`origine` dit d'où sort un ticket : « run-id <TAB> verdict <TAB> raison » du dernier run qui
l'a eu en main — son bilan s'il a été jugé, « sans verdict » s'il était en vol à la coupure.
Rien (code 1) si aucun run ne l'a jamais pris : c'est le cas d'une session interactive. C'est ce
que `lib.sh reprendre-en-cours` consigne en reprenant un orphelin (#329).
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

# bloc_de <fichier json> <allow|ask|deny> : les règles d'un bloc, une par ligne, préfixées du nom du
# bloc. La lecture vit dans `permissions.sh` depuis #789 — volontairement pas une copie figée ici :
# le classement doit suivre les règles réellement en vigueur, sinon il se périme sans que rien ne le
# dise, ce qui est exactement le défaut que #307 est venu corriger.
bloc_de() { perm_bloc "$1" "$2"; }

# --- refus : l'agrégat des permission_denials ---------------------------------------------------------
# Deux passes, parce qu'elles ne lisent pas la même chose. La PREMIÈRE ouvre un `<iid>.json` — un
# objet JSON minifié sur une ligne — et en tire une ligne TSV par refus ; la SECONDE agrège ce TSV,
# qui ne dépend plus du JSON. Les fonctions de lecture JSON sont celles de `run.sh` (§ vue_resultat) :
# la duplication est assumée et petite, un « awk de bibliothèque » partagé demanderait de refaire la
# plomberie de `run.sh`, qui passe son programme en chaîne et ne peut donc pas le combiner à un `-f`.

AWK_REFUS=$(cat <<'AWK'
# chaine_a(s, p) : la chaîne JSON qui commence en p (guillemet ouvrant déjà consommé), échappements
# conservés — c'est desechappe() qui les rend.
function chaine_a(s, p,   i, n, c, out) {
  out = ""; n = length(s)
  for (i = p; i <= n; i++) {
    c = substr(s, i, 1)
    if (c == "\\") { out = out c substr(s, i + 1, 1); i++; continue }
    if (c == "\"") break
    out = out c
  }
  return out
}

function desechappe(s) {
  gsub(/\\n/, "\n", s); gsub(/\\t/, "\t", s); gsub(/\\r/, "", s)
  gsub(/\\"/, "\"", s); gsub(/\\\//, "/", s); gsub(/\\\\/, "\\", s)
  return s
}

# tableau(s, cle) : le CONTENU du tableau d'une clé, crochets exclus. Compte les niveaux en ignorant
# ce qui est dans une chaîne — une commande refusée contient volontiers un « } ».
function tableau(s, cle,   i, n, c, prof, dans, esc, out) {
  if (!match(s, "\"" cle "\"[ \t]*:[ \t]*\\[")) return ""
  n = length(s); prof = 1; dans = 0; esc = 0; out = ""
  for (i = RSTART + RLENGTH; i <= n; i++) {
    c = substr(s, i, 1)
    if (esc) { esc = 0; out = out c; continue }
    if (dans) {
      if (c == "\\") esc = 1
      else if (c == "\"") dans = 0
      out = out c
      continue
    }
    if (c == "\"") { dans = 1; out = out c; continue }
    if (c == "[" || c == "{") prof++
    else if (c == "]" || c == "}") { prof--; if (prof == 0) break }
    out = out c
  }
  return out
}

{ brut = brut $0 }

END {
  contenu = tableau(brut, "permission_denials")
  if (contenu == "") exit
  # Le tableau se découpe sur `"tool_name":` plutôt que sur `},{` : un objet imbriqué (`tool_input`)
  # et une commande porteuse d'accolades rendraient ce second découpage faux.
  parts = split(contenu, morceaux, /"tool_name"[ \t]*:[ \t]*/)
  for (k = 2; k <= parts; k++) {
    m = morceaux[k]
    if (substr(m, 1, 1) != "\"") continue
    outil = desechappe(chaine_a(m, 2))
    cible = ""
    if (match(m, /"(command|skill|file_path|pattern|path|url|description)"[ \t]*:[ \t]*"/))
      cible = desechappe(chaine_a(m, RSTART + RLENGTH))
    # Les FORMES immatchables se relèvent AVANT d'aplatir la commande : le saut de ligne en est une,
    # et il ne survivrait pas au TSV. Aucune règle de préfixe ne peut reconnaître l'une d'elles,
    # quelle que soit la commande qu'elles habillent — c'est ce que dit le prompt de run.sh (#235).
    formes = ""
    if (cible ~ /\n/) formes = formes "L"
    if (cible ~ /\$\(/ || cible ~ /`/) formes = formes "S"
    if (cible ~ /<</) formes = formes "H"
    # Le TSV est la frontière entre les deux passes : ni tabulation ni saut de ligne ne doivent y
    # survivre — une commande multi-ligne (la forme même que #232 traque) casserait le découpage.
    gsub(/[\n\t]+/, " ", cible)
    printf "%s\t%s\t%s\t%s\t%s\n", run, iid, outil, cible, formes
  }
}
AWK
)

AWK_AGREGE=$(cat <<'AWK'
function largeur(s,   t) { t = s; return length(t) - gsub(/[\200-\277]/, "", t) }

# tronque(s, n) : n colonnes au plus, comptées en CARACTÈRES — couper une séquence UTF-8 en deux
# laisserait un « <?> » en bout de ligne, sur une commande accentuée.
function tronque(s, n,   i, l, c, taille) {
  if (largeur(s) <= n) return s
  l = 0; i = 1
  while (i <= length(s) && l < n) {
    c = substr(s, i, 1)
    taille = 1
    if (match(c, /[\300-\337]/)) taille = 2
    else if (match(c, /[\340-\357]/)) taille = 3
    else if (match(c, /[\360-\367]/)) taille = 4
    i += taille; l++
  }
  return substr(s, 1, i - 1) "…"
}

function pad(s, n,   m) { m = n - largeur(s); if (m < 1) m = 1; return s sprintf("%*s", m, "") }

# segments(cmd, tab) : découpe une commande sur `&&`, `||`, `;` et `|` — les séparateurs mêmes sur
# lesquels le CLI découpe pour exiger que CHAQUE morceau soit autorisé. Hors guillemets seulement :
# un `grep -E "a|b"` est une commande, pas deux.
function segments(cmd, tab,   i, n, c, d, q, cur, k) {
  n = length(cmd); q = ""; cur = ""; k = 0
  for (i = 1; i <= n; i++) {
    c = substr(cmd, i, 1)
    if (q != "") { if (c == q) q = ""; cur = cur c; continue }
    if (c == "\"" || c == "'") { q = c; cur = cur c; continue }
    if (c == "\\") { cur = cur c substr(cmd, i + 1, 1); i++; continue }
    d = substr(cmd, i, 2)
    if (d == "&&" || d == "||") { k++; tab[k] = cur; cur = ""; i++; continue }
    if (c == ";" || c == "|") { k++; tab[k] = cur; cur = ""; continue }
    cur = cur c
  }
  k++; tab[k] = cur
  return k
}

# verbe(seg) : ce sur quoi une règle d'allowlist porterait — donc le premier mot, sauf pour les
# lanceurs qui n'ont aucun sens seuls (`git ls-remote`, `command -v`, `bash scripts/…`). Un préfixe
# `VAR=…` est rendu tel quel : c'est une forme qu'aucune règle ne peut matcher, pas un outil absent.
function verbe(seg,   n, t, v, i) {
  sub(/^[ \t]+/, "", seg)
  sub(/[ \t]+$/, "", seg)
  n = split(seg, t, /[ \t]+/)
  if (n == 0 || t[1] == "") return ""
  # Le découpage sur `;` coupe aussi les boucles shell : `until … ; do sleep 3 ; done` rendrait
  # « until », « do » et « done ». Les mots de liaison s'effacent devant ce qu'ils introduisent (`do
  # sleep` → « sleep », « done » → rien) ; les têtes de boucle, elles, RESTENT — une attente active
  # est un refus mérité (#178), et c'est ce mot-là qui le dit.
  i = 1
  while (i <= n && t[i] ~ /^(do|then|else|elif|\{|\()$/) i++
  if (i > n) return ""
  if (t[i] ~ /^(done|fi|esac|\}|\))$/) return ""
  if (i > 1) { for (v = 1; v + i - 1 <= n; v++) t[v] = t[v + i - 1]; n = n - i + 1 }
  v = t[1]
  if (v ~ /^[A-Za-z_][A-Za-z0-9_]*=/) return "VAR=… " (n >= 2 ? t[2] : "")
  if (v ~ /^(git|gh|npm|npx|bash|sh|command|docker|pip|python|python3|node|sudo)$/ && n >= 2)
    return v " " t[2]
  return v
}

# --- Le classement (#307) -----------------------------------------------------------------------
# nettoie(seg) : le maillon débarrassé de ce qui l'habille. Mêmes mots de liaison que verbe() — le
# découpage sur `;` coupe aussi les boucles shell, et « do sleep 3 » est un maillon « sleep ».
function nettoie(seg) {
  sub(/^[ \t]+/, "", seg); sub(/[ \t]+$/, "", seg)
  while (seg ~ /^(do|then|else|elif|\{|\()[ \t]+/) sub(/^(do|then|else|elif|\{|\()[ \t]+/, "", seg)
  if (seg ~ /^(done|fi|esac|\}|\))$/) return ""
  return seg
}

# `matche(seg, regles, n, large)` et `outil_couvert(nom, regles, n)` sont définies dans
# `scripts/orchestrate/permissions.awk`, concaténé DEVANT ce programme (#789) : `ecart-run.sh` pose
# la même question aux mêmes règles, et deux implémentations finiraient par ne plus rendre le même
# verdict sur la même règle — celui-là même qui décide si l'on va élargir une allowlist.

# chemin_hors(cmd) : l'appel vise-t-il un chemin hors du répertoire de travail ? C'est le TEXTE qui
# le dit, et il suffit : le journal ne sait pas où était le worktree, et n'en a pas besoin — une
# session travaille en RELATIF, donc tout chemin absolu sort de la borne, y compris celui de son
# propre worktree (la forme même que #307 a mesurée). `/dev/null` est retiré d'abord : c'est du
# silence, pas une cible, et il habille des commandes par ailleurs innocentes.
function chemin_hors(cmd,   c) {
  c = cmd
  gsub(/\/dev\/null/, " ", c)
  if (c ~ /(^|[^A-Za-z0-9_])[A-Za-z]:[\/\\]/) return 1                 # E:/…, C:\…
  # Le `/` d'un chemin absolu suit un espace, un `>` ou un guillemet — jamais une lettre (« sed
  # s/a/b/ »), un point (« ./x ») ni un autre `/` (« https://… »), qui sont les trois formes qui
  # feraient prendre une commande ordinaire pour une échappée.
  if (c ~ /(^|[^A-Za-z0-9_.\/])\/[A-Za-z]/) return 1                   # /tmp/…, /c/Users/…
  if (c ~ /(^|[^A-Za-z0-9_])~\//) return 1                             # ~/…
  if (c ~ /\$\{?(TMPDIR|TEMP|TMP)\}?/) return 1
  return 0
}

# tri(compte, cles) : les clés de `compte`, du plus fréquent au moins fréquent (alphabétique à
# égalité). Tri par insertion : une poignée d'entrées, et `asorti` n'existe pas partout.
function tri(compte, cles,   n, i, j, k, tmp) {
  n = 0
  for (k in compte) cles[++n] = k
  for (i = 2; i <= n; i++) {
    tmp = cles[i]; j = i - 1
    while (j >= 1 && (compte[cles[j]] < compte[tmp] || \
                      (compte[cles[j]] == compte[tmp] && cles[j] > tmp))) {
      cles[j + 1] = cles[j]; j--
    }
    cles[j + 1] = tmp
  }
  return n
}

# ticket_de(id) : le TICKET derrière une clé de session. Une session de déblocage journalise sous
# `<iid>-mrfix` (#420) ; le correctif qu'elle n'a pas pu écrire appartient au ticket, qui est ce qui
# porte un ticket de reprise — une tentative de déblocage n'en a pas. Le rapport HUMAIN, lui, garde
# la distinction : c'est là qu'on veut savoir quelle session a buté.
function ticket_de(id) { sub(/-mrfix[0-9]*$/, "", id); return id }

# provenance(cle) : « #130 x3, #131 » — de quels tickets vient une entrée, et combien de fois.
function provenance(cle, par_ticket, ordre,   n, t, i, s, iid) {
  n = split(ordre[cle], t, " ")
  s = ""
  for (i = 1; i <= n && i <= 6; i++) {
    iid = t[i]
    s = s (s != "" ? ", " : "") "#" iid (par_ticket[cle, iid] > 1 ? " ×" par_ticket[cle, iid] : "")
  }
  if (n > 6) s = s ", …"
  return s
}

BEGIN {
  FS = "\t"
  # Les règles arrivent par un fichier plutôt que par `-v` : awk applique ses séquences d'échappement
  # aux valeurs de `-v`, et un antislash ajouté un jour à une règle y serait mangé en silence.
  # Deux jeux, parce que le geste diffère : `allow` dit ce qui passe, `ask`/`deny` disent ce qu'aucun
  # élargissement ne doit lever — une règle `ask` est un refus VOULU dès qu'il n'y a personne pour
  # répondre, c'est-à-dire à chaque session autonome.
  while ((getline ligne < regles) > 0) {
    if (ligne == "") continue
    p = index(ligne, "\t")
    if (p == 0) continue
    if (substr(ligne, 1, p - 1) == "allow") allow[++n_allow] = substr(ligne, p + 1)
    else voulu[++n_voulu] = substr(ligne, p + 1)
  }
}

NF >= 3 {
  run = $1; iid = $2; outil = $3; cible = $4; formes = $5
  total++
  outils[outil]++
  if (!((run SUBSEP iid) in vues)) { vues[run SUBSEP iid] = 1; sessions++ }

  if (formes != "") {
    forme_total++
    if (formes ~ /L/) forme_n["saut de ligne dans la commande"]++
    if (formes ~ /S/) forme_n["substitution $(…) ou `…`"]++
    if (formes ~ /H/) forme_n["heredoc <<"]++
  }

  if (outil == "Bash") {
    nb = segments(cible, seg)
    decouvert = 0; par_regle = 0
    for (i = 1; i <= nb; i++) {
      v = verbe(seg[i])
      if (v == "") continue
      # Une même forme deux fois dans la même chaîne (`grep … | grep …`) n'est qu'un refus. La clé
      # porte le NUMÉRO du refus plutôt qu'un `delete` du tableau, que POSIX ne garantit pas.
      if ((total SUBSEP v) in deja) continue
      deja[total, v] = 1
      if (!(v in cmd_n)) nb_cmd++
      cmd_n[v]++
      if (nb > 1) cmd_comp[v]++
      if (!(v in cmd_ex)) cmd_ex[v] = cible
      if (!((v SUBSEP iid) in cmd_par)) cmd_ordre[v] = cmd_ordre[v] " " iid
      cmd_par[v, iid]++
      # Le maillon nu, lui, se compte pour instruire : c'est LA liste des règles qui manquent.
      # Deux exclusions, sans lesquelles cette liste enverrait élargir l'`allow` pour rien :
      #  - un maillon visé par une règle `ask`/`deny` est un refus VOULU (personne pour approuver) ;
      #  - un maillon qui porte un CHEMIN ABSOLU n'est pas un trou : sa forme relative, elle, serait
      #    couverte (`.venv/Scripts/python.exe …`, `bash scripts/…`), et aucune règle de PRÉFIXE ne
      #    pourra jamais borner un absolu. C'est une échappée — le geste est le prompt (§11.7,
      #    quatrième forme), pas `settings.run.json`.
      maillon = nettoie(seg[i])
      if (maillon == "") continue
      if (matche(maillon, voulu, n_voulu, 1)) { par_regle = 1; continue }
      if (!matche(maillon, allow, n_allow, 0) && !chemin_hors(maillon)) {
        decouvert = 1
        if (!(v in nu_n)) nb_nu++
        nu_n[v]++
      }
    }
    famille = par_regle ? "voulu" \
      : (decouvert ? "trou" : (chemin_hors(cible) ? "chemin" : (formes != "" ? "forme" : "reste")))
  } else {
    k = outil (cible != "" ? " — " tronque(cible, 58) : "")
    if (!(k in autre_n)) nb_autre++
    autre_n[k]++
    if (!((k SUBSEP iid) in autre_par)) autre_ordre[k] = autre_ordre[k] " " iid
    autre_par[k, iid]++
    # Le refus qui ne s'instruit pas (#229) : il vient du CLI, pas de la liste, et se reconnaît au
    # seul chemin visé. Le compter à part évite d'aller ajouter une règle qui ne servirait à rien.
    if (outil ~ /^(Write|Edit|NotebookEdit|MultiEdit)$/ && cible ~ /(^|[\/\\])\.claude[\/\\]/) {
      claude_n++
      famille = "claude"
    } else if (!outil_couvert(outil, allow, n_allow)) {
      famille = "trou"
      if (!(outil in nu_n)) nb_nu++
      nu_n[outil]++
    } else {
      famille = (chemin_hors(cible) ? "chemin" : (formes != "" ? "forme" : "reste"))
    }
  }
  fam_n[famille]++

  # Le mode machine (#611) se branche ICI, sur la famille DÉJÀ décidée, et jamais sur un second
  # motif : c'est ce qui garantit qu'il n'existe pas deux définitions de « blocage dur .claude/ »
  # dans le dépôt — et la seconde aurait été la muette, celle que le pilote lit sans que personne
  # relise sa sortie.
  if (famille == "claude") {
    tk = ticket_de(iid)
    if (!(tk in claude_par)) claude_ordre[++n_claude] = tk
    claude_par[tk]++
    if (!(tk in claude_ex)) claude_ex[tk] = cible
  }
}

END {
  # Le mode MACHINE sort avant tout le reste — pas une ligne d'en-tête, pas une portée : sa sortie
  # est lue par un `while read`, et le silence y vaut « aucun résidu ». Une ligne par TICKET, dans
  # l'ordre où le journal les a rendus (les `<iid>.json` sont lus triés, donc c'est reproductible).
  if (mode == "claude") {
    for (i = 1; i <= n_claude; i++) {
      tk = claude_ordre[i]
      printf "%s\t%s\t%s\n", tk, claude_par[tk], tronque(claude_ex[tk], 70)
    }
    exit
  }
  print ""
  print portee
  if (total == 0) {
    print ""
    print "  Aucun refus de permission — rien à instruire."
    print ""
    exit
  }
  printf "  %s session(s) · %s refus\n", sessions, total

  # Le classement passe EN PREMIER : la question qu'on se pose en ouvrant cette sortie est « qu'est-ce
  # qu'il faut en faire ? ». Les sections suivantes en sont le détail, pas la réponse.
  if (n_allow == 0) {
    print ""
    print "  ⚠ Classement indisponible — aucune règle « allow » lue (scripts/orchestrate/settings.run.json,"
    print "    .claude/settings.json). Le détail ci-dessous reste valable."
  } else {
    lib["chemin"] = "échappée de chemin"
    lib["trou"]   = "trou d'allowlist"
    lib["claude"] = "blocage dur .claude/"
    lib["voulu"]  = "refus voulu (ask/deny)"
    lib["forme"]  = "forme immatchable"
    lib["reste"]  = "inclassé"
    geste["chemin"] = "la cible sort du répertoire de travail → le prompt, jamais la liste"
    geste["trou"]   = "un maillon qu'aucune règle ne couvre → settings.run.json"
    geste["claude"] = "refus du CLI, en amont de la liste — rien ne le lèvera (#229)"
    geste["voulu"]  = "une règle du dépôt le demande, personne ne peut approuver → rien"
    geste["forme"]  = "saut de ligne, $(…), heredoc → l'outil Write (détail plus bas)"
    geste["reste"]  = "aucune cause connue — à regarder à la main"
    print ""
    printf "── Ce qui les explique — un refus, une famille, un geste (%s règle(s) allow lues)\n", n_allow
    n = tri(fam_n, cles)
    for (i = 1; i <= n; i++) {
      f = cles[i]
      printf "  %3d  %s%3d %%  %s\n",
        fam_n[f], pad(lib[f], 24), int(fam_n[f] * 100 / total + 0.5), geste[f]
    }
    print "       Ordre de décision : .claude/, refus voulu, trou d'allowlist, échappée de chemin,"
    print "       forme — on ne conclut à l'échappée que si rien d'autre n'explique le refus."
    print "       Les règles lues sont celles d'AUJOURD'HUI : sur un vieux run, « inclassé » dit"
    print "       souvent « déjà instruit depuis ». C'est le dernier run qui se lit pour agir."
    if (nb_nu > 0) {
      print ""
      print "  Maillons qu'aucune règle ne couvre — c'est CETTE liste-là qui s'instruit :"
      n = tri(nu_n, cles)
      for (i = 1; i <= n; i++) printf "    %3d  %s\n", nu_n[cles[i]], cles[i]
    }
  }

  print ""
  print "── Par outil"
  n = tri(outils, cles)
  for (i = 1; i <= n; i++) printf "  %s%s\n", pad(cles[i], 26), outils[cles[i]]

  if (nb_cmd > 0) {
    print ""
    print "── Par commande — une chaîne vaut son maillon le plus faible, chacun compte pour lui-même"
    n = tri(cmd_n, cles)
    for (i = 1; i <= n; i++) {
      v = cles[i]
      suffixe = (cmd_comp[v] > 0 ? "  (dont " cmd_comp[v] " en commande composée)" : "")
      printf "  %3d  %s%s%s\n", cmd_n[v], pad(v, 24), provenance(v, cmd_par, cmd_ordre), suffixe
      printf "       ex. %s\n", tronque(cmd_ex[v], 100)
    }
  }

  if (nb_autre > 0) {
    print ""
    print "── Hors Bash"
    n = tri(autre_n, cles)
    for (i = 1; i <= n; i++)
      printf "  %3d  %s%s\n", autre_n[cles[i]], pad(cles[i], 44), provenance(cles[i], autre_par, autre_ordre)
  }

  # Ces trois-là ne s'instruisent pas en élargissant la liste : aucune règle de préfixe ne les
  # reconnaîtra jamais. Les compter à part, c'est dire que le geste est ailleurs — dans la FORME de
  # l'appel, donc dans le prompt de run.sh ou dans la commande que le dépôt dicte à la session.
  if (forme_total > 0) {
    print ""
    printf "── Formes immatchables — %s refus sur %s en portent une, quoi qu'elle habille\n", forme_total, total
    n = tri(forme_n, cles)
    for (i = 1; i <= n; i++) printf "  %3d  %s\n", forme_n[cles[i]], cles[i]
    print "       (un même refus peut en cumuler plusieurs)"
    print "       Le geste : écrire le fichier avec l'outil Write, puis passer son CHEMIN à la commande."
  }

  print ""
  if (claude_n > 0)
    printf "  ⚠ %s refus visent .claude/ : ils viennent du CLI, aucune règle ne les lèvera (docs/10 §11.7).\n", claude_n
  print "  Les instruire au cas par cas — ajouter au « allow », corriger la FORME de l'appel dans le"
  print "  prompt de run.sh, ou laisser le refus mérité : docs/10-workflow-git.md §11.7."
  print ""
}
AWK
)

commande_refus() {
  # PLUSIEURS run-id sont acceptés, et pas un seul (#611) : un run repris et le run qu'il reprend
  # sont deux répertoires pour un même travail, et les sessions du premier ne sont journalisées que
  # là-bas. Les agréger ensemble se fait ICI, où l'agrégation vit déjà — les additionner chez
  # l'appelant reviendrait à en écrire une seconde, sur des comptes cette fois.
  local cibles="" tous=0 mode=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --tous) tous=1 ;;
      --claude) mode=claude ;;
      -h | --help) usage; return 0 ;;
      -*) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; return 2 ;;
      *) cibles="$cibles$1"$'\n' ;;
    esac
    shift
  done

  # En mode machine, « pas de journal » et « aucun refus » sont la MÊME réponse — aucun résidu à
  # nommer — et elle s'écrit avec un code, pas avec une phrase que personne ne lit.
  [ -d "$ORCH_DIR" ] || {
    [ -z "$mode" ] || return 3
    printf '\nAucun journal d'\''orchestration — aucun run n'\''a encore tourné ici.\n\n'
    return 0
  }

  # Les run-id sont horodatés (`AAAAMMJJ-HHMMSS`) : l'ordre alphabétique EST l'ordre chronologique,
  # sans avoir à interroger le système de fichiers.
  local dirs="" d id portee
  if [ "$tous" = 1 ]; then
    for d in "$ORCH_DIR"/*/; do
      [ -d "$d" ] || continue
      dirs="$dirs${d%/}"$'\n'
    done
    portee="Refus de permission — tout le journal"
  elif [ -n "$cibles" ]; then
    # Un run-id inconnu reste une ERREUR FRANCHE, même noyé dans une liste : le taire rendrait un
    # agrégat amputé sous les mots d'un agrégat complet, ce qui est le seul verdict qu'on ne veut pas.
    local noms=""
    while IFS= read -r id; do
      [ -n "$id" ] || continue
      [ -d "$ORCH_DIR/$id" ] || {
        printf 'journal.sh refus : run inconnu — %s\n' "$id" >&2
        printf 'Les runs présents : %s\n' "$(ls -1 "$ORCH_DIR" 2>/dev/null | tr '\n' ' ')" >&2
        return 2
      }
      dirs="$dirs$ORCH_DIR/$id"$'\n'
      noms="$noms${noms:+ + }$id"
    done <<EOF
$cibles
EOF
    portee="Refus de permission — run $noms"
  else
    # Sans argument : le dernier run qui porte un RÉSULTAT. Un run tout frais dont aucune session
    # n'a encore rendu la main masquerait sinon le seul run qu'on puisse lire.
    local f
    for d in "$ORCH_DIR"/*/; do
      [ -d "$d" ] || continue
      for f in "${d}"[0-9]*.json; do
        [ -s "$f" ] || continue
        dirs="${d%/}"$'\n'
        break
      done
    done
    [ -n "$dirs" ] || {
      [ -z "$mode" ] || return 3
      printf '\nAucun résultat de session dans le journal — rien à lire.\n\n'
      return 0
    }
    portee="Refus de permission — run $(basename "${dirs%$'\n'}") (le dernier qui en porte)"
  fi

  local brut="" f id id_run
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    id_run="$(basename "$d")"
    for f in "$d"/[0-9]*.json; do
      [ -s "$f" ] || continue
      id="$(basename "$f")"; id="${id%%.*}"
      brut="$brut$(awk -v run="$id_run" -v iid="$id" "$AWK_REFUS" "$f")"$'\n'
    done
  done <<EOF
$dirs
EOF

  # Les règles de l'`allow` que le classement rejoue — UNION des deux fichiers, parce que c'est le
  # régime réel d'une session autonome (§11.7) : `settings.run.json` n'a jamais redupliqué les verbes
  # git/gh du dépôt, et les ignorer rangerait chaque `git status` refusé en trou d'allowlist.
  # Le fichier va dans le temporaire du système et non sous `.maestro/` : c'est un brouillon de
  # calcul que personne n'ouvre, et la règle de §8.5 ne vise que ce qu'un script invite à lire.
  local regles
  regles="$(mktemp "${TMPDIR:-/tmp}/maestro-allow.XXXXXX" 2>/dev/null)" || regles=""
  if [ -n "$regles" ]; then
    trap 'rm -f "$regles" 2>/dev/null' RETURN
    perm_union "$SETTINGS_RUN" "$SETTINGS_DEPOT" > "$regles"
  fi

  # Le matching lui-même arrive de `permissions.awk`, concaténé DEVANT le programme (#789).
  local prog
  prog="$(perm_awk)$AWK_AGREGE"

  # LC_ALL=C : le tri et les comptes se font sur des octets, comme partout ailleurs dans ce dépôt.
  if [ -n "$mode" ]; then
    # Capturé plutôt qu'imprimé au fil de l'eau : c'est le seul moyen de rendre « aucun » par un
    # CODE (3) plutôt que par une sortie vide, qu'un appelant ne pourrait pas distinguer d'un échec.
    local tsv
    tsv="$(printf '%s' "$brut" | LC_ALL=C awk -v portee="$portee" -v regles="$regles" \
      -v mode="$mode" "$prog")"
    [ -n "$tsv" ] || return 3
    printf '%s\n' "$tsv"
    return 0
  fi
  printf '%s' "$brut" | LC_ALL=C awk -v portee="$portee" -v regles="$regles" "$prog"
  return 0
}

# --- origine : d'où sort ce ticket ? (#329, parent #327) ----------------------------------------------
# `reprendre-en-cours` remet un orphelin « À faire » et libre. La question qu'un humain se pose alors
# — et à laquelle rien ne répondait — est « qu'est-ce qui l'a laissé là ? ». Elle se lit ICI et nulle
# part ailleurs : le ticket, lui, ne porte aucune trace de la session qui est morte dessus.
#
# Sortie : « run-id <TAB> verdict <TAB> raison », rien (code 1) si aucun run n'a jamais eu ce ticket
# en main — le cas d'une session INTERACTIVE laissée en plan (#325), qui n'écrit aucun journal. Ne
# rien trouver est donc une réponse, pas une panne.
#
# DEUX SOURCES, dans cet ordre, et la seconde est celle qui compte le plus :
#   1. une ligne de bilan dans `resume.tsv` — le run a jugé le ticket (#316 : « ✗ ECHEC — timeout ») ;
#   2. à défaut, une TRACE DE SESSION (le témoin `<iid>.session`, ou son flux `<iid>.jsonl`) : le
#      ticket était EN VOL à la coupure. C'est le mode de mort qui fabrique les orphelins — un
#      pilote arrêté au `taskkill` n'exécute aucun trap, donc ne pose aucun verdict —, et s'arrêter à
#      la première source laisserait sans origine ceux qu'on reprend le plus souvent.
#
# Le run le PLUS RÉCENT gagne : les répertoires sont horodatés (`AAAAMMJJ-HHMMSS`), un tri décroissant
# sur leur nom suffit et ne coûte aucun `stat`. Lecture seule, sans `jq` ni réseau.
commande_origine() {
  local iid="${1:-}"
  case "$iid" in
    '' | *[!0-9]*) printf 'usage: journal.sh origine <iid>\n' >&2; return 2 ;;
  esac
  [ -d "$ORCH_DIR" ] || return 1

  local dir id ligne verdict raison
  # Les run-id sortent du GLOB (jamais d'un `ls`, qu'un nom exotique ferait mentir) puis passent par
  # `sort -r` : l'horodatage étant le nom, l'ordre alphabétique inverse EST l'ordre du plus récent
  # au plus ancien, sans un seul `stat`.
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    dir="$ORCH_DIR/$id"
    if [ -f "$dir/resume.tsv" ]; then
      ligne="$(awk -F '\t' -v iid="$iid" '$1 == iid { print; exit }' "$dir/resume.tsv")"
      verdict="$(printf '%s' "$ligne" | cut -f2)"
      # `SAUTE` n'est PAS une prise en main : le run a passé son tour (lot précédent en échec, ticket
      # pris entre-temps) sans jamais ouvrir de session dessus. Le compter ici ferait dire au verbe
      # « ce run l'a eu en main » d'un run qui n'y a pas touché — et masquerait le run PRÉCÉDENT,
      # celui qui l'a réellement laissé là. On continue donc de remonter le temps.
      if [ -n "$ligne" ] && [ "$verdict" != "SAUTE" ]; then
        raison="$(printf '%s' "$ligne" | cut -f6)"
        printf '%s\t%s\t%s\n' "$id" "${verdict:--}" "${raison:--}"
        return 0
      fi
    fi
    # Pas de bilan : le ticket a-t-il seulement été pris en main par ce run-là ?
    if [ -f "$dir/$iid.session" ] || [ -s "$dir/$iid.jsonl" ] || [ -s "$dir/$iid.jsonl.gz" ]; then
      printf '%s\tsans verdict\tsession en vol à la coupure\n' "$id"
      return 0
    fi
  done <<< "$(for dir in "$ORCH_DIR"/*/; do [ -d "$dir" ] && basename "$dir"; done | sort -r)"
  return 1
}

# --- audit : où passe le temps d'un run (#497, parent #495) --------------------------------------------
# `status.sh` dit OÙ EN EST un run, `refus` dit ce qui lui a été REFUSÉ. Personne ne disait OÙ PASSE
# SON TEMPS — alors qu'un run coûte des heures de mur et un quota partagé par N sessions (#289), et
# que depuis #418 il va jusqu'au merge sans reprendre son souffle.
#
# La matière est là depuis #176 : `<iid>.jsonl` est le flux `stream-json` intégral, un événement par
# ligne, HORODATÉ À LA MILLISECONDE. Ce qui manquait est l'appariement — la durée d'un appel ne se
# lit sur aucune ligne, elle est l'écart entre le `tool_use` et son `tool_result`, qui vivent sur
# deux lignes différentes et se retrouvent par leur identifiant.
#
# Deux passes, comme `refus` et pour la même raison : l'extraction connaît la FORME du flux et rend
# des faits en TSV, l'agrégation connaît la QUESTION et ne relit plus le JSON. Les douze sections du
# rapport — huit pour le temps, quatre pour les tokens (#1465) — se servent toutes des mêmes faits,
# au lieu de parcourir le flux douze fois.
#
# Ce que l'audit MESURE, et qu'une lecture ticket par ticket rate :
#   - la PART du mur passée sous outil — 38 % sur un ticket, 89 % sur un autre : un run lent n'a pas
#     toujours la même maladie, et un rapport qui dirait seulement « Bash est lent » se tromperait
#     de remède une fois sur deux ;
#   - le poids d'une FORME de commande. Mesure du 2026-08-24 sur le run 20260823-182458 (6 tickets) :
#     `lib.sh` pèse 22,9 min en 93 invocations de 14,8 s — premier poste du run, invisible appel par
#     appel. Le filet CI, lui, coûte 16,5 min en 6 appels : c'est un coût ATTENDU, et les mettre sur
#     la même ligne sans les distinguer ferait chercher l'économie du mauvais côté ;
#   - le PRÉ-VOL, payé une fois PAR TICKET (~3 min : `worktree.sh ensure` 61 s, `lib.sh begin` 23 s,
#     `start-brief` 19 s, `start-branch` 5 s) — donc N fois par run, ce qu'aucune vue ne totalisait ;
#   - le TEMPS MORT, qui n'est pas du temps lent : ce qui est hors de tout appel.
#
# Ce qu'il NE FAIT PAS, et c'est la portée de #495 : corriger. Il mesure et classe ; chaque remède
# est son propre ticket, priorisé sur ce que l'audit chiffre.
#
# Lecture seule, hors ligne, sans `jq` ni Python — le pilote est un script shell, il le reste (#180).
# Un run EN COURS se lit comme un autre : c'est même la question qu'on pose le plus souvent.

AWK_AUDIT_EXTRAIT=$(cat <<'AWK'
# Extraction d'un flux `<iid>.jsonl` : une ligne TSV par FAIT mesuré, rien d'agrégé ici.
#
# La durée d'un appel d'outil ne se lit sur aucune ligne : elle s'obtient en APPARIANT le `tool_use`
# (ligne `"type":"assistant"`) et son `tool_result` (ligne `"type":"user"`) PAR leur identifiant, sur
# les horodatages de LIGNE. Les deux ne sont jamais sur la même ligne — d'où une table
# `id -> (nom, cible, horodatage)` tenue au fil de la lecture, et des appels laissés OUVERTS quand
# la session est morte avant leur retour.
#
# L'appariement ne s'ancre PAS sur `"type":"tool_use"` / `"type":"tool_result"`, et c'est le point
# qui a demandé deux essais : l'ORDRE DES CLÉS D'UN BLOC N'EST PAS STABLE. Mesuré sur le flux de
# #346, un bloc de résultat s'écrit tantôt `{"type":"tool_result","tool_use_id":…}`, tantôt
# `{"tool_use_id":…,"type":"tool_result"}` — si bien qu'un découpage sur le marqueur de type range
# l'identifiant tantôt dans le segment courant, tantôt dans le précédent, et n'apparie plus que les
# appels dont l'ordre l'arrange (4 sur 98 au premier essai, tous les autres déclarés « morts sans
# retour »). On s'ancre donc sur ce qui ne varie pas : le PRÉFIXE `toolu_` d'un identifiant, et la
# CLÉ QUI LE PORTE — `"id":"` le déclare, `"tool_use_id":"` le référence. Le discriminant est exact
# et ne suppose aucun ordre.

# _epoch : « 2026-08-23T15:27:33.570Z » -> secondes. Écrit à la main plutôt que par `mktime`, qui
# est une extension gawk : l'image du job pytest est une Debian, donc `mawk`, qui ne l'a pas. Tout
# est en Z, donc aucun fuseau n'entre dans le calcul.
function _epoch(s,   y,mo,d,h,mi,se,ms,era,yoe,doy,doe,a) {
  if (length(s) < 19) return -1
  y = substr(s,1,4)+0; mo = substr(s,6,2)+0; d = substr(s,9,2)+0
  h = substr(s,12,2)+0; mi = substr(s,15,2)+0; se = substr(s,18,2)+0
  ms = (substr(s,20,1) == ".") ? substr(s,21,3)+0 : 0
  a = y; if (mo <= 2) a -= 1
  era = int((a >= 0 ? a : a-399) / 400)
  yoe = a - era*400
  doy = int((153*(mo + (mo > 2 ? -3 : 9)) + 2)/5) + d-1
  doe = yoe*365 + int(yoe/4) - int(yoe/100) + doy
  return (era*146097 + doe - 719468)*86400 + h*3600 + mi*60 + se + ms/1000
}

# _jval : la valeur de chaîne JSON qui commence à `pos` (premier caractère APRÈS le guillemet
# ouvrant), rendue DÉSESCAPÉE.
#
# C'est le point où l'on ne peut pas se contenter d'un `[^"]*` : la valeur est échappée, si bien
# qu'un `{"command":"cd \"E:/…\" && git log"}` s'arrête au guillemet ÉCHAPPÉ et rend « cd \ ». C'est
# le défaut que #496 corrige dans la vue, et l'audit ne peut pas se permettre de le refaire — il
# rangerait tout le run sous une seule et même forme de commande.
function _jval(s, pos,   out, c, n) {
  out = ""; n = length(s)
  while (pos <= n) {
    c = substr(s, pos, 1)
    if (c == "\\") {
      c = substr(s, pos+1, 1)
      if (c == "n" || c == "t" || c == "r") out = out " "
      else if (c == "u") { out = out "?"; pos += 4 }
      else out = out c
      pos += 2
    } else if (c == "\"") return out
    else { out = out c; pos++ }
  }
  return out
}

# _pos : l'index où COMMENCE la valeur de `key` dans `s`, 0 si absente. Le motif `"key":"` occupe
# `length(key) + 4` caractères (guillemet, clé, guillemet, deux-points, guillemet) : la valeur
# commence donc juste après. Un caractère de moins et `_jval` démarre SUR le guillemet ouvrant, y
# lit une fin de chaîne et rend le vide — panne muette, aucune ligne en sortie.
function _pos(s, key,   p) {
  p = index(s, "\"" key "\":\"")
  return (p == 0) ? 0 : p + length(key) + 4
}
function _key(s, key,   p) {
  p = _pos(s, key)
  return (p == 0) ? "" : _jval(s, p)
}

# _cible : la première des clés d'entrée présentes, par POSITION et non par ordre de la liste — un
# `description` placé avant `command` dans le JSON décrirait sinon l'appel à la place de sa commande.
function _cible(seg,   i, n, p, best, bestp, cles) {
  n = split("command,file_path,pattern,path,url,description", cles, ",")
  bestp = 0; best = ""
  for (i = 1; i <= n; i++) {
    p = _pos(seg, cles[i])
    if (p > 0 && (bestp == 0 || p < bestp)) { bestp = p; best = _jval(seg, p) }
  }
  return best
}

# _num : l'entier qui suit `"key":` dans `s`, 0 si la clé est absente. Le guillemet ouvrant fait
# partie du motif : `"input_tokens":` ne se trouve ni dans `"cache_creation_input_tokens":` ni
# dans `"ephemeral_5m_input_tokens":`, où la clé est précédée d'un souligné.
function _num(s, key,   p, r, c) {
  p = index(s, "\"" key "\":")
  if (p == 0) return 0
  p += length(key) + 3
  r = ""
  while ((c = substr(s, p, 1)) ~ /[0-9]/) { r = r c; p++ }
  return r + 0
}

# _taille : ce qu'un résultat d'outil fait relire, en tokens, à raison de quatre caractères par
# token. Une image est comptée au forfait IMAGE_TOK, le plafond d'une capture d'écran : compter son
# base64 la ferait peser cent fois ce que le modèle en lit.
function _taille(seg,   n, p, q, img, reste) {
  n = length(seg); img = 0; reste = seg
  while ((p = index(reste, "\"data\":\"")) > 0) {
    q = index(substr(reste, p + 8), "\"")
    if (q == 0) break
    n -= q - 1
    reste = substr(reste, p + 8 + q)
  }
  reste = seg
  while ((p = index(reste, "\"type\":\"image\"")) > 0) { img++; reste = substr(reste, p + 14) }
  return int(n / 4) + img * IMAGE_TOK
}

BEGIN { IMAGE_TOK = (IMAGE_TOK == "") ? 1600 : IMAGE_TOK; CIBLE_MAX = 400 }

{
  # `rate_limit_event` : télémétrie que le CLI place en tête de CHAQUE flux (#203). Elle ne dit rien
  # de ce que la session fait — l'écarter ici évite qu'elle compte pour un événement de travail.
  if (index($0, "\"type\":\"rate_limit_event\"") > 0) next

  t = _epoch(_key($0, "timestamp"))
  if (t >= 0) { if (premier == 0) premier = t; dernier = t; nb++ }

  # --- l'usage d'un message (#1465) ----------------------------------------------------------------
  # Un message du modèle arrive en PLUSIEURS événements, un par bloc (réflexion, texte, chaque
  # `tool_use`), et chacun porte l'usage du message entier : on regroupe par `message.id` et on
  # garde le plus grand relevé de chaque compteur, sans quoi un message de trois blocs compterait
  # trois fois son contexte. Un événement qui porte un `parent_tool_use_id` vient d'un SOUS-AGENT :
  # il a son propre contexte, et le mêler aux tours de la session ferait relire à celle-ci ce
  # qu'elle n'a jamais lu. On le compte à part.
  #
  # Rien ne s'ancre en tête de ligne : deux événements peuvent partager une ligne quand leurs
  # écritures se croisent (mesuré sur le flux de #1399, un `thinking_tokens` collé à un `assistant`).
  #
  # La SORTIE d'un message, elle, n'est pas dans ses événements : le compteur qu'ils portent est
  # celui du début de la génération, quelques tokens (mesuré sur #1400 : 1,4k sur toute la session,
  # contre 83k à son bilan). Elle ne se lit qu'au bilan `result` d'un passage de la session — par
  # ticket, donc, jamais par tour ni par famille.
  sous = (index($0, "\"parent_tool_use_id\":\"toolu_") > 0)
  if (!sous && index($0, "\"subtype\":\"compact_boundary\"") > 0) bornes[++nb_bornes] = n_main
  mid = ""
  if (index($0, "\"type\":\"assistant\"") > 0 && index($0, "\"role\":\"assistant\"") > 0 \
      && (p = index($0, "\"id\":\"msg_")) > 0) {
    mid = _jval($0, p + 6)
    if (!(mid in M_vu)) {
      M_vu[mid] = 1; M_sous[mid] = sous
      if (sous) S_ordre[++n_sous] = mid; else M_ordre[++n_main] = mid
    }
    if ((p = index($0, "\"usage\":{")) > 0) {
      u = substr($0, p)
      v = _num(u, "input_tokens");                if (v > M_in[mid])  M_in[mid] = v
      v = _num(u, "cache_read_input_tokens");     if (v > M_cr[mid])  M_cr[mid] = v
      v = _num(u, "cache_creation_input_tokens"); if (v > M_cw[mid])  M_cw[mid] = v
    }
  } else if (index($0, "\"type\":\"result\"") > 0 && index($0, "\"type\":\"user\"") == 0 \
             && (p = index($0, "\"usage\":{")) > 0) {
    # La garde sur `user` : le double `tool_use_result` d'un résultat est du JSON en clair, et ce
    # qu'un outil rend (un sous-agent, son usage) pourrait y porter les deux clés.
    # Un bilan par passage : une session reprise, ou réveillée par une tâche de fond, en rend
    # plusieurs, chacun pour ce qu'il a fait (mesuré sur #1520 : un bilan à zéro, puis le vrai).
    # Le détail par itération, dans l'usage, porte ses propres `output_tokens` : on retire le
    # tableau (ses objets n'en contiennent aucun autre) plutôt que de couper l'usage à sa clé,
    # l'ordre des clés n'étant pas stable — le bilan à zéro de #1520 ouvre sur un autre champ.
    u = substr($0, p)
    if ((q = index(u, "\"iterations\":[")) > 0 && (r = index(substr(u, q), "]")) > 0)
      u = substr(u, 1, q - 1) substr(u, q + r)
    B_out += _num(u, "output_tokens"); nb_bilans++
  }

  if (index($0, "toolu_") == 0) next

  # Un `tool_use_id` ne suffit pas à faire un résultat : les lignes `"type":"system"` de suivi d'une
  # tâche d'arrière-plan (`task_started`, `task_progress`, `task_notification`) en portent un aussi,
  # SANS horodatage de ligne. Les prendre pour des retours refermait l'appel à l'instant -1 et rendait
  # des durées négatives de dix-sept chiffres — l'`Agent` et les `Bash` longs, c'est-à-dire
  # exactement les appels que l'audit cherche. On exige donc que la LIGNE porte le bloc, garde qui ne
  # suppose toujours aucun ordre de clés puisqu'elle ne regarde pas où il est.
  est_use = (index($0, "\"type\":\"tool_use\"") > 0)
  est_res = (index($0, "\"type\":\"tool_result\"") > 0 && t >= 0)
  if (!est_use && !est_res) next

  # 1er passage : relever TOUS les identifiants de la ligne, avec leur rôle et leur position.
  nocc = 0; pos = 1
  while ((p = index(substr($0, pos), "toolu_")) > 0) {
    abs = pos + p - 1
    role = ""
    if (substr($0, abs-6, 6) == "\"id\":\"") role = "use"
    else if (substr($0, abs-15, 15) == "\"tool_use_id\":\"") role = "res"
    if (role != "") {
      nocc++
      O_pos[nocc] = abs; O_role[nocc] = role; O_id[nocc] = _jval($0, abs)
    }
    pos = abs + 6
  }

  # Le message proprement dit, pour peser un résultat : le CLI recopie ce que l'outil a rendu dans
  # `tool_use_result`, à côté du message, et ce double n'est jamais montré au modèle. Le message
  # s'arrête donc à la première des clés qui le suivent dans l'événement.
  # Seule une ligne de résultat de la session en a besoin : le calcul copie la ligne.
  nres = 0; fin_msg = 0
  if (est_res && !sous) {
    deb_msg = index($0, "\"message\":"); if (deb_msg == 0) deb_msg = 1
    fin_msg = length($0) + 1
    apres_msg = substr($0, deb_msg)
    n = split("parent_tool_use_id,tool_use_result,session_id", apres, ",")
    for (i = 1; i <= n; i++) {
      q = index(apres_msg, "\"" apres[i] "\":")
      if (q > 0 && deb_msg + q - 1 < fin_msg) fin_msg = deb_msg + q - 1
    }
    for (k = 1; k <= nocc; k++) if (O_role[k] == "res" && O_pos[k] < fin_msg) nres++
  }

  # 2e passage : un `use` prend son nom et sa cible dans la FENÊTRE qui va de son identifiant au
  # suivant — la borne évite qu'un bloc emprunte le `name` de son voisin quand plusieurs appels
  # partent dans le même message (appels parallèles).
  for (k = 1; k <= nocc; k++) {
    id = O_id[k]
    if (O_role[k] == "res") {
      if (est_res && id in U_t && !(id in vu)) {
        printf "A\t%s\t%s\t%s\t%.3f\t%.3f\t%.3f\t%s\n", run, iid, U_nom[id], U_t[id], t, t - U_t[id], U_cible[id]
        vu[id] = 1
      }
      # Le poids d'un résultat (#1465) : sa taille, et le rang du tour qui l'a reçu — les tours qui
      # le relisent ne seront connus qu'à la fin du flux. Un seul résultat dans le message (les
      # 1 485 du run 20261008-080648) : il pèse le message entier, quel que soit l'ordre des clés
      # de son bloc. Plusieurs : chacun pèse sa fenêtre, d'un identifiant au suivant.
      if (est_res && !sous && O_pos[k] < fin_msg && id in U_nom && !U_sous[id] && !(id in R_vu)) {
        R_vu[id] = 1
        if (nres == 1) seg = substr($0, deb_msg, fin_msg - deb_msg)
        else {
          fin = fin_msg
          for (j = k + 1; j <= nocc; j++) if (O_pos[j] < fin_msg) { fin = O_pos[j]; break }
          seg = substr($0, O_pos[k], fin - O_pos[k])
        }
        R_id[++n_res] = id; R_tok[n_res] = _taille(seg); R_rang[n_res] = n_main
      }
      continue
    }
    if (!est_use) continue
    fin = (k < nocc) ? O_pos[k+1] : length($0) + 1
    seg = substr($0, O_pos[k], fin - O_pos[k])
    nom = _key(seg, "name")
    if (nom == "") continue
    U_nom[id] = nom; U_cible[id] = _cible(seg); U_t[id] = t
    ordre[++rang] = id
    # Le message qui porte l'appel : c'est à lui que l'usage se prend, partagé entre ses appels.
    if (mid != "" && !(id in U_mid)) { U_mid[id] = mid; U_sous[id] = sous; M_nuses[mid]++ }
  }
}

END {
  # Les appels restés OUVERTS : la session est morte avant leur retour (limite d'usage, `taskkill`,
  # flux tronqué). Les taire ferait passer un run coupé en plein `local.sh` pour un run sans incident.
  for (k = 1; k <= rang; k++) {
    id = ordre[k]
    if (!(id in vu)) printf "O\t%s\t%s\t%s\t%.3f\t%s\n", run, iid, U_nom[id], U_t[id], U_cible[id]
  }
  if (nb > 0) printf "W\t%s\t%s\t%.3f\t%.3f\t%d\n", run, iid, premier, dernier, nb

  # --- les faits de l'usage (#1465) -----------------------------------------------------------------
  # M : un tour de la session — un message du modèle et son contexte.
  for (m = 1; m <= n_main; m++) {
    mid = M_ordre[m]
    printf "M\t%s\t%s\t%d\t%d\t%d\t%d\n", run, iid, m, M_in[mid], M_cr[mid], M_cw[mid]
  }
  # C : la part d'un tour imputée à un appel. Un message qui appelle trois outils de front leur
  # partage son usage à parts égales ; un message sans appel (la réponse, la réflexion seule) le
  # garde sous le nom « - ». La cible est tronquée : elle ne sert qu'à nommer la famille.
  for (k = 1; k <= rang; k++) {
    id = ordre[k]
    if (!(id in U_mid) || U_sous[id] || (id in C_vu)) continue
    C_vu[id] = 1; mid = U_mid[id]; n = M_nuses[mid]
    printf "C\t%s\t%s\t%s\t%.3f\t%.3f\t%.3f\t%s\n", run, iid, U_nom[id],
           M_in[mid]/n, M_cr[mid]/n, M_cw[mid]/n, substr(U_cible[id], 1, CIBLE_MAX)
  }
  for (m = 1; m <= n_main; m++) {
    mid = M_ordre[m]
    if (M_nuses[mid] == 0)
      printf "C\t%s\t%s\t-\t%d\t%d\t%d\t\n", run, iid, M_in[mid], M_cr[mid], M_cw[mid]
  }
  # B : la sortie de la session, au total de ses bilans — rien si elle n'en a rendu aucun.
  if (nb_bilans > 0) printf "B\t%s\t%s\t%d\t%.0f\n", run, iid, nb_bilans, B_out
  # R : un résultat d'outil et les tours qui le relisent — tous ceux qui le suivent, jusqu'à la
  # fin du flux ou jusqu'à la compaction suivante, qui retire du contexte ce qui la précède.
  for (r = 1; r <= n_res; r++) {
    fin = n_main
    for (b = 1; b <= nb_bornes; b++) if (bornes[b] >= R_rang[r]) { fin = bornes[b]; break }
    id = R_id[r]
    printf "R\t%s\t%s\t%d\t%d\t%s\t%s\n", run, iid, R_tok[r], fin - R_rang[r], U_nom[id],
           substr(U_cible[id], 1, CIBLE_MAX)
  }
  # S : les sous-agents du ticket, en un seul total — ils ne relisent pas le contexte principal.
  if (n_sous > 0) {
    si = 0; scr = 0; scw = 0
    for (s = 1; s <= n_sous; s++) { mid = S_ordre[s]; si += M_in[mid]; scr += M_cr[mid]; scw += M_cw[mid] }
    printf "S\t%s\t%s\t%d\t%.0f\t%.0f\t%.0f\n", run, iid, n_sous, si, scr, scw
  }
}
AWK
)

AWK_AUDIT_AGREGE=$(cat <<'AWK'
# Agrégation des faits extraits par `AWK_AUDIT_EXTRAIT` — rend le rapport, ne lit aucun fichier.
#
# Deux temps plutôt qu'un, comme `refus` : l'extraction connaît la forme du flux, l'agrégation
# connaît la question posée. Les mêmes faits servent les six sections sans être relus six fois.

function _duree(s,   h, m) {
  if (s < 0) return "?"
  if (s < 90) return sprintf("%.1fs", s)
  if (s < 5400) return sprintf("%.1f min", s/60)
  h = int(s/3600); m = int((s - h*3600)/60)
  return sprintf("%dh%02d", h, m)
}

# _sans_worktree : le chemin, privé de son préfixe `…/<base>/<nom-du-worktree>/`. Il est le MÊME
# sur toutes les lignes d'un ticket et fait à lui seul la moitié de la largeur — même raison que le
# `cd` retiré par `_forme`, et que le `${cible#"$RACINE/"}` de la vue.
#
# La base est celle de `worktree.sh` par défaut — `.claude/worktrees` depuis #847, sous ses deux
# séparateurs (la session reçoit des chemins « E:/… » comme « E:\… »), et `maestro-worktrees` avant,
# gardée pour que les journaux d'avant restent lisibles : un audit qui ne saurait plus lire un run
# de la veille ne vaut rien. Un `MAESTRO_WORKTREE_DIR` déplacé ne fait que laisser la ligne plus
# longue, jamais fausse.
function _sans_worktree(s,   r) {
  r = _sans_base(s, ".claude/worktrees");  if (r != s) return r
  r = _sans_base(s, ".claude\\worktrees"); if (r != s) return r
  return _sans_base(s, "maestro-worktrees")
}

# _sans_base <chemin> <base> : le chemin privé de `…/<base>/<premier segment>/`, ou tel quel.
#
# Découpé à la main plutôt que par une expression régulière, et ce n'est pas un excès de prudence :
# un `[\/\\]` dans un LITTÉRAL regex awk n'est pas portable — gawk consomme le `\\` à la lecture du
# littéral, la classe devient `[\/\]`, le `\]` échappe le crochet et le motif ne se termine plus
# (« unterminated regexp », mesuré). Dans une chaîne, `"\\"` vaut un antislash partout, sans
# ambiguïté.
function _sans_base(s, base,   p, q, c) {
  p = index(s, base)
  if (p == 0) return s
  q = p + length(base)
  c = substr(s, q, 1)
  if (c != "/" && c != "\\") return s
  q++
  while (q <= length(s)) {
    c = substr(s, q, 1)
    if (c == "/" || c == "\\") return substr(s, q+1)
    q++
  }
  return s
}

# _court : la cible, ramenée à ce qui la distingue.
function _court(s, n) {
  gsub(/[ \t]+/, " ", s)
  s = _sans_worktree(s)
  return (length(s) > n) ? substr(s, 1, n-1) "…" : s
}

# _forme : la commande, réduite à ce qui la désigne — ses deux premiers mots utiles.
#
# Le `cd "<worktree>" &&` de préfixe est RETIRÉ d'abord : le prompt d'une session autonome lui fait
# préfixer presque tous ses appels (règle #234 — la cible doit rester dans le worktree), si bien que
# le garder rangerait tout le run sous une seule forme, « cd », et ne dirait plus rien de personne.
# Même raison que le maillon d'une chaîne compté pour lui-même dans `refus`.
function _forme(c,   n, w) {
  c = _sans_cd(c)
  n = split(c, w, /[ \t]+/)
  if (n == 0) return "(sans commande)"
  return (n >= 2) ? w[1] " " w[2] : w[1]
}

# _sans_cd : la commande privée de son `cd "<worktree>" &&` de préfixe — partagé par `_forme` et
# `_famille`, qui doivent voir la même commande.
function _sans_cd(c,   p) {
  sub(/^[ \t]+/, "", c)
  if (substr(c, 1, 4) == "cd \"") { p = index(substr(c, 5), "\""); if (p > 0) c = substr(c, 5+p) }
  else if (substr(c, 1, 3) == "cd ") { p = index(c, "&&"); if (p > 0) c = substr(c, p) }
  sub(/^[ \t]*&&[ \t]*/, "", c)
  sub(/^[ \t]+/, "", c)
  return c
}

# _famille : la famille d'un appel, d'après son outil et, pour une commande, ses premiers mots.
#
# Les familles sont choisies sur ce qu'on CHERCHE à réduire quand on lit le coût d'un ticket
# (docs/47) : lire du code, écrire, le banc, la stack réelle et ses exercices, la relecture visuelle
# et le navigateur, les tests et le filet local, la forge et git, l'attente, les sous-agents, le
# web. Elles servent au temps et
# aux tokens à la fois — une seule définition, sans quoi les deux colonnes d'une même ligne ne
# parleraient pas de la même chose.
#
# Une commande se range sur son PREMIER MOT, une fois le `cd` de préfixe et les affectations
# (`env VAR=…`) écartés : `grep -n x scripts/gitlab/lib.sh` est une lecture, pas un appel à la
# forge. Une lecture le reste, même du journal du banc : ce qui se paie alors est ce qui est lu.
# Viennent ensuite deux exceptions au premier mot, parce que leur coût tient à ce qu'elles
# déclenchent et non au verbe qui les lance : une commande qui nomme le banc (le démarrer,
# l'attendre) et l'attente d'un pipeline.
function _famille(nom, c,   n, w1, w2, w3) {
  if (nom == "-") return "sans appel (réponse, réflexion)"
  if (nom == "Read" || nom == "Grep" || nom == "Glob" || nom == "LS" || nom == "NotebookRead")
    return "lecture de code"
  if (nom == "Edit" || nom == "Write" || nom == "MultiEdit" || nom == "NotebookEdit") return "écriture"
  if (nom == "Agent" || nom == "Task") return "sous-agent"
  if (nom == "WebSearch" || nom == "WebFetch") return "web"
  if (nom == "Monitor" || nom == "BashOutput" || nom == "TaskOutput" || nom == "ScheduleWakeup")
    return "attente"
  if (substr(nom, 1, 5) == "mcp__")
    return (index(nom, "chrome") || index(nom, "browser") || index(nom, "playwright")) \
      ? "relecture visuelle et navigateur" : "autre outil"
  if (nom != "Bash" && nom != "PowerShell") return "autre outil"

  n = _mots(c)
  w1 = (n >= 1) ? MOTS[1] : ""; w2 = (n >= 2) ? MOTS[2] : ""; w3 = (n >= 3) ? MOTS[3] : ""
  if (w1 ~ /^(cat|head|tail|sed|grep|rg|find|ls|wc|nl|less|awk|tree|stat|diff|file)$/)
    return "lecture de code"
  if (index(c, "banc") > 0) return "banc"
  if (w1 == "sleep" || index(c, "pipeline-wait") > 0) return "attente"
  if (w1 == "git" || w1 == "gh") return "forge et git"
  if (w1 ~ /^(pytest|npm|npx|ruff|shellcheck|vitest|tsc)$/) return "tests et filet local"
  if (index(w1, "python") > 0 && index(c, "pytest") > 0) return "tests et filet local"
  # Le script qu'une session s'écrit pour attendre un processus (mesuré le 2026-10-08 : `node
  # .maestro/session/attente-run.js`, 24 min en trois appels) attend, quoi qu'il lance.
  if ((w1 == "node" || index(w1, "python") > 0) && (index(w2, "attente") > 0 || index(w2, "attendre") > 0))
    return "attente"
  if (w1 == "node" && index(w2, "captures") > 0) return "relecture visuelle et navigateur"
  if (index(w1, "python") > 0 && w2 == "-m" && substr(w3, 1, 7) == "maestro") return "stack et exercice"
  if (w1 == "bash" || w1 == "sh") {
    if (index(w2, "scripts/ci/") > 0) return "tests et filet local"
    if (index(w2, "relecture-visuelle") > 0 || index(w2, "scripts/design/") > 0 \
        || index(w2, "scripts/presentation/") > 0) return "relecture visuelle et navigateur"
    if (index(w2, "scripts/gitlab/") > 0 || index(w2, "scripts/github/") > 0 \
        || index(w2, "scripts/git/") > 0) return "forge et git"
    if (index(w2, "scripts/controltower/") > 0 || index(w2, "desktop.sh") > 0) return "stack et exercice"
  }
  return "autre commande"
}

# _mots : les mots d'une commande, sans son `cd` de préfixe ni ses affectations de tête, rangés
# dans le tableau global MOTS — un tableau local passé d'une fonction à l'autre n'est pas garanti
# partout, et le job pytest tourne sous `mawk`.
function _mots(c,   t, n, i, m) {
  n = split(_sans_cd(c), t, /[ \t]+/)
  m = 0
  for (i = 1; i <= n; i++) {
    if (t[i] == "") continue
    if (m == 0 && (t[i] == "env" || t[i] ~ /^[A-Za-z_][A-Za-z0-9_]*=/)) continue
    MOTS[++m] = t[i]
  }
  return m
}

# _tok : un nombre de tokens, lisible. Jamais `%d` au-delà du millier : le `mawk` de l'image du job
# pytest le borne à 32 bits, et le poids d'un run dépasse le milliard.
function _tok(n) {
  if (n < 1000) return sprintf("%.0f", n)
  if (n < 1e6) return sprintf("%.1fk", n / 1e3)
  if (n < 1e9) return sprintf("%.1fM", n / 1e6)
  return sprintf("%.2fG", n / 1e9)
}

# _chemin : un fichier lu, nommé comme le dépôt le nomme — sans préfixe de worktree, et avec des
# `/` : le même fichier arrive en « E:/… » d'une session et en « E:\… » d'une autre.
function _chemin(s,   r, p) {
  s = _sans_worktree(s); r = ""
  while ((p = index(s, "\\")) > 0) { r = r substr(s, 1, p - 1) "/"; s = substr(s, p + 1) }
  return r s
}

# _qs, _mediane : le contexte médian d'un tour. Un tri rapide plutôt que par insertion : `--tous`
# porte des dizaines de milliers de tours.
function _qs(A, lo, hi,   i, j, p, x) {
  if (lo >= hi) return
  p = A[int((lo + hi) / 2)]; i = lo; j = hi
  while (i <= j) {
    while (A[i] < p) i++
    while (A[j] > p) j--
    if (i <= j) { x = A[i]; A[i] = A[j]; A[j] = x; i++; j-- }
  }
  _qs(A, lo, j); _qs(A, i, hi)
}
function _mediane(A, n) {
  if (n == 0) return 0
  _qs(A, 1, n)
  return (n % 2) ? A[(n + 1) / 2] : (A[n / 2] + A[n / 2 + 1]) / 2
}

# _ticket : inscrit un ticket rencontré par n'importe lequel de ses faits.
function _ticket(run, i,   k) {
  k = run SUBSEP i
  if (!(k in vus)) { vus[k] = 1; tickets[++nb_tickets] = k; K_run[k] = run; K_iid[k] = i }
  if (!(run in runs_vus)) { runs_vus[run] = 1; nb_runs++ }
  return k
}

# _prevol : le geste de démarrage que cet appel exécute, vide s'il n'en est pas un. Les quatre
# gestes de `/ticket-start` sont NOMMÉS — jamais devinés d'une position dans le flux, un run repris
# ne commençant pas au même endroit. Rendre le geste et non un booléen permet de les détailler :
# rangés par `_forme`, `start-brief` et `begin` se confondraient sous « bash scripts/gitlab/lib.sh »,
# et le pré-vol ne dirait plus lequel de ses quatre temps coûte.
function _est_prevol(c,   i, n, g, gestes) {
  n = split("worktree.sh ensure,lib.sh start-brief,lib.sh start-branch,lib.sh begin", gestes, ",")
  for (i = 1; i <= n; i++) if (index(c, gestes[i]) > 0) return gestes[i]
  return ""
}

# _etiq : comment nommer un ticket dans le rapport. Les faits sont indexés par (run, iid) et non par
# iid seul — un ticket REPRIS apparaît dans deux runs, et les fondre additionnerait deux temps de mur
# qui ne se suivent pas, pour un total que rien ne vérifierait. Sur un seul run l'étiquette reste
# « #<iid> » ; dès qu'il y en a plusieurs elle porte le run, sans quoi deux lignes identiques
# désigneraient deux passages différents.
function _etiq(cle) {
  return (nb_runs > 1) ? K_run[cle] "/#" K_iid[cle] : "#" K_iid[cle]
}
# La colonne suit l'étiquette : la caler sur le pire cas laisserait treize blancs par ligne sur
# l'usage courant, qui ne porte qu'un run. Elle s'élargit pour la plus longue — « #1401-mrfix »,
# la session de `/mr-fix` d'un ticket, en déborderait sinon et décalerait toute sa ligne.
function _larg(   ti, l, m) {
  m = (nb_runs > 1) ? 21 : 8
  for (ti = 1; ti <= nb_tickets; ti++) { l = length(_etiq(tickets[ti])); if (l > m) m = l }
  return m
}

BEGIN { FS = "\t"; SEUIL_TROU = (SEUIL_TROU == "") ? 120 : SEUIL_TROU }

$1 == "A" {
  iid = $2 SUBSEP $3; nom = $4; d = $5; f = $6; duree = $7; cible = $8
  if (!(iid in vus)) { vus[iid] = 1; tickets[++nb_tickets] = iid; K_run[iid] = $2; K_iid[iid] = $3 }
  if (!($2 in runs_vus)) { runs_vus[$2] = 1; nb_runs++ }
  k = iid SUBSEP (++par_ticket[iid])
  D[k] = d; F[k] = f
  t_outil[nom] += duree; n_outil[nom]++
  FA_t[_famille(nom, cible)] += duree
  cumul_ticket[iid] += duree
  if (nom == "Bash") {
    fo = _forme(cible)
    t_forme[fo] += duree; n_forme[fo]++
  }
  # Palmarès : on garde tout, le tri se fait à la fin sur au plus quelques centaines d'entrées.
  n_long++; L_d[n_long] = duree; L_iid[n_long] = iid; L_nom[n_long] = nom; L_cible[n_long] = cible
  # Commandes rejouées à l'identique — BASH SEULEMENT, et DANS UN MÊME TICKET. Deux restrictions,
  # deux motifs distincts, et aucune n'est un raffinement de l'autre.
  #
  # Bash seulement : rouvrir deux fois le même fichier avec `Read` ou l'éditer dix fois avec `Edit`
  # est du travail normal, et les compter noyait le signal sous la liste des fichiers du ticket.
  #
  # Dans un même ticket (#578) : la clé était la commande SEULE, agrégée sur tout le run — si bien
  # qu'un appel joué UNE FOIS PAR TICKET remontait « 2x … au-delà du premier passage » sur un run de
  # deux tickets. Le filet CI avant push, un verbe `lib.sh` sur le parent commun de deux lots : la
  # CHAÎNE est identique, le rejeu ne l'est pas. Le défaut n'était pas de calcul — « au-delà du
  # premier passage » était exact au sens littéral — mais de LECTURE, et il grandissait avec le run :
  # sur douze tickets la section aurait été dominée par les douze passages du filet CI, c'est-à-dire
  # par le coût le plus attendu de tous, dans la section faite pour montrer l'inattendu. Ce que la
  # section attrape est la commande relancée à l'intérieur d'une même session — reprise après échec,
  # ou tour en rond —, et elle seule ; le coût d'un appel structurel se lit « par forme de commande »
  # et, pour le pré-vol, dans sa propre section.
  if (nom == "Bash") {
    r = iid SUBSEP cible
    n_repet[r]++; t_repet[r] += duree
    R_iid[r] = iid; R_cmd[r] = cible   # portés à côté : `cible` peut tout contenir, SUBSEP compris
  }
  if (nom == "Bash") {
    pv = _est_prevol(cible)
    if (pv != "") {
      prevol_t[iid] += duree; prevol_par[iid SUBSEP pv] += duree
      if (!(iid SUBSEP pv in pv_vu)) {
        pv_vu[iid SUBSEP pv] = 1
        prevol_ordre[iid] = prevol_ordre[iid] (prevol_ordre[iid] == "" ? "" : "\n") pv
      }
    }
  }
}

$1 == "O" { orphelins[++nb_orph] = $3 "\t" $4 "\t" $6 }

# --- l'usage (#1465) : faits M, C, B, R, S de l'extraction ------------------------------------------
# M — un tour : son contexte, l'entrée, le cache relu et le cache écrit — ce que le modèle relit à
# chaque appel.
$1 == "M" {
  k = _ticket($2, $3)
  T_tours[k]++; T_in[k] += $5; T_cr[k] += $6; T_cw[k] += $7
  ctx = $5 + $6 + $7
  CTX[k SUBSEP T_tours[k]] = ctx; CTX_RUN[++n_ctx] = ctx; tot_ctx += ctx
  if (ctx > T_pic[k]) T_pic[k] = ctx
  if (ctx > pic_run) pic_run = ctx
  run_in += $5; run_cr += $6; run_cw += $7
}

# C — la part d'un tour imputée à un appel, rangée dans la famille de l'appel.
$1 == "C" {
  k = _ticket($2, $3)
  fam = _famille($4, $8)
  tok = $5 + $6 + $7
  F_tok[fam] += tok; F_cr[fam] += $6; F_cw[fam] += $7; tot_tok_c += tok
  if ($4 != "-") F_n[fam]++; else F_n[fam] += 0
  if (!((k SUBSEP fam) in FT_tok)) FT_fams[k] = FT_fams[k] (FT_fams[k] == "" ? "" : "\n") fam
  FT_tok[k SUBSEP fam] += tok; FT_tot[k] += tok
}

# B — la sortie d'une session, au total de ses bilans.
$1 == "B" { k = _ticket($2, $3); T_bilan[k] = 1; T_out[k] += $5; run_out += $5 }

# R — un résultat d'outil : sa taille et les tours qui le relisent. Leur produit est son POIDS.
$1 == "R" {
  _ticket($2, $3)
  fam = _famille($6, $7); poids = $4 * $5
  P_poids[fam] += poids; P_n[fam]++; P_taille[fam] += $4; tot_poids += poids
  if ($6 == "Read" && $7 != "") {
    f = _chemin($7)
    FI_poids[f] += poids; FI_n[f]++; FI_taille[f] += $4
  }
}

# S — les sous-agents d'un ticket, en un total.
$1 == "S" {
  k = _ticket($2, $3)
  S_msg[k] += $4; S_tok[k] += $5 + $6 + $7; sous_msg += $4; sous_tok += $5 + $6 + $7
}

$1 == "W" {
  iid = $2 SUBSEP $3
  if (!(iid in vus)) { vus[iid] = 1; tickets[++nb_tickets] = iid; K_run[iid] = $2; K_iid[iid] = $3 }
  if (!($2 in runs_vus)) { runs_vus[$2] = 1; nb_runs++ }
  W_debut[iid] = $4; W_fin[iid] = $5; W_nb[iid] = $6
  mur[iid] = $5 - $4
}

END {
  # Les formats sont CONSTRUITS et non écrits en dur, la colonne du ticket dépendant de la portée
  # (« #473 » sur un run, « 20260824-192234/#473 » sur le journal entier). Une largeur variable
  # s'écrirait `%-*s`, que POSIX prévoit mais que rien ne garantit dans le `mawk` de l'image Debian
  # du job pytest — un format assemblé, lui, marche partout.
  L = _larg()
  F_ENTETE = "   %-" L "s %10s %10s %7s %8s\n"
  F_LIGNE  = "   %-" L "s %10s %10s %6.0f%% %8d\n"
  F_LONG   = "   %9s  %-" L "s %-6s %s\n"
  F_PREVOL = "   %-" L "s %9s   %s\n"
  F_TROU   = "   %9s  %-" L "s après %.0f s de session\n"
  F_REPET  = "   %3dx %9s  %-" L "s %s\n"

  # --- occupation réelle : l'UNION des intervalles, jamais leur somme ---------------------------
  # Des appels PARALLÈLES se recouvrent (plusieurs `tool_use` dans un même message) : leur somme
  # dépasserait alors le temps de mur et rendrait une « part d'outils » de 110 %. On trie par début
  # et on fusionne — c'est la seule mesure qui reste juste quand la session appelle de front.
  for (ti = 1; ti <= nb_tickets; ti++) {
    iid = tickets[ti]
    n = par_ticket[iid]
    for (i = 1; i <= n; i++) { ord[i] = i }
    for (i = 2; i <= n; i++) {            # tri par insertion : n vaut quelques centaines au plus
      v = ord[i]; j = i - 1
      while (j >= 1 && D[iid SUBSEP ord[j]] > D[iid SUBSEP v]) { ord[j+1] = ord[j]; j-- }
      ord[j+1] = v
    }
    occ = 0; cd = -1; cf = -1; ntrou = 0
    for (i = 1; i <= n; i++) {
      dd = D[iid SUBSEP ord[i]]; ff = F[iid SUBSEP ord[i]]
      if (cd < 0) { cd = dd; cf = ff; continue }
      if (dd <= cf) { if (ff > cf) cf = ff; continue }
      occ += cf - cd
      if (dd - cf >= SEUIL_TROU) { ntrou++; T_iid[++nb_trous] = iid; T_debut[nb_trous] = cf; T_duree[nb_trous] = dd - cf }
      cd = dd; cf = ff
    }
    if (cd >= 0) occ += cf - cd
    occup[iid] = occ
    total_occ += occ; total_mur += mur[iid]; total_appels += n
  }

  printf "\n"
  printf "  Audit — %s\n", portee
  printf "\n"

  # --- 1. vue d'ensemble ------------------------------------------------------------------------
  printf "── Où passe le temps, ticket par ticket\n"
  printf F_ENTETE, "ticket", "mur", "sous outil", "part", "appels"
  for (ti = 1; ti <= nb_tickets; ti++) {
    iid = tickets[ti]
    printf F_LIGNE, _etiq(iid), _duree(mur[iid]), _duree(occup[iid]),
           (mur[iid] > 0 ? 100*occup[iid]/mur[iid] : 0), par_ticket[iid]
  }
  if (nb_tickets > 1)
    printf F_LIGNE, (nb_runs > 1 ? "TOTAL" : "run"), _duree(total_mur), _duree(total_occ),
           (total_mur > 0 ? 100*total_occ/total_mur : 0), total_appels
  printf "\n"
  printf "   « sous outil » est l'UNION des appels, pas leur somme : des appels parallèles se\n"
  printf "   recouvrent. Le reste du mur est de la réflexion ou de l'attente — voir « temps mort ».\n"
  printf "\n"

  # --- 2. par outil -----------------------------------------------------------------------------
  printf "── Par outil\n"
  _classe(t_outil, n_outil, 99)
  printf "\n"

  # --- 3. Bash par forme de commande ------------------------------------------------------------
  if (length(t_forme) > 0) {
    printf "── Bash, par forme de commande — le `cd \"<worktree>\" &&` de préfixe est écarté\n"
    _classe(t_forme, n_forme, 12)
    printf "\n"
  }

  # --- 4. les appels les plus longs -------------------------------------------------------------
  printf "── Les appels les plus longs\n"
  for (rangc = 1; rangc <= 10 && rangc <= n_long; rangc++) {
    best = 0; bi = 0
    for (i = 1; i <= n_long; i++) if (!pris[i] && L_d[i] > best) { best = L_d[i]; bi = i }
    if (bi == 0) break
    pris[bi] = 1
    printf F_LONG, _duree(L_d[bi]), _etiq(L_iid[bi]), L_nom[bi], _court(L_cible[bi], 58)
  }
  printf "\n"

  # --- 5. le pré-vol ----------------------------------------------------------------------------
  if (length(prevol_t) > 0) {
    printf "── Le pré-vol — les quatre gestes de /ticket-start, avant la première ligne de code\n"
    for (ti = 1; ti <= nb_tickets; ti++) {
      iid = tickets[ti]
      if (!(iid in prevol_t)) continue
      detail = ""
      np = split(prevol_ordre[iid], pvs, "\n")
      for (i = 1; i <= np; i++)
        detail = detail (detail == "" ? "" : " · ") pvs[i] " " _duree(prevol_par[iid SUBSEP pvs[i]])
      printf F_PREVOL, _etiq(iid), _duree(prevol_t[iid]), detail
      total_pv += prevol_t[iid]
    }
    if (nb_tickets > 1) printf "   %-7s %9s   payé une fois par ticket\n", "total", _duree(total_pv)
    printf "\n"
  }

  # --- 6. le temps mort -------------------------------------------------------------------------
  # Le total D'ABORD, les gros trous ensuite : c'est le total qui répond à « où est passé le reste
  # du mur ? », et il vaut souvent plusieurs minutes sans qu'aucun trou pris isolément ne dépasse le
  # seuil. N'afficher que le palmarès rendait « aucun trou » sur un ticket qui passait la moitié de
  # son temps hors appel — une réponse qui se lit comme « rien à signaler ».
  printf "── Le temps mort — %s hors de tout appel, soit %.0f%% du mur\n",
         _duree(total_mur - total_occ), (total_mur > 0 ? 100*(total_mur - total_occ)/total_mur : 0)
  if (nb_trous == 0) printf "   réparti en attentes courtes : aucun trou n'atteint %d s.\n", SEUIL_TROU
  else {
    printf "   dont %d trou(s) d'au moins %d s :\n", nb_trous, SEUIL_TROU
    for (rangt = 1; rangt <= 8 && rangt <= nb_trous; rangt++) {
      best = 0; bi = 0
      for (i = 1; i <= nb_trous; i++) if (!prist[i] && T_duree[i] > best) { best = T_duree[i]; bi = i }
      if (bi == 0) break
      prist[bi] = 1
      total_trou_vu += T_duree[bi]
      printf F_TROU, _duree(T_duree[bi]), _etiq(T_iid[bi]),
             T_debut[bi] - W_debut[T_iid[bi]]
    }
    printf "\n   Un trou de plusieurs heures est une limite d'usage ; quelques minutes, de la\n"
    printf "   réflexion — l'audit les mesure, il ne les départage pas.\n"
  }
  printf "\n"

  # --- 7. ce qui a été refait à l'identique, DANS UN MÊME TICKET --------------------------------
  # Le total est calculé sur les MÊMES entrées que les lignes en dessous : un total qui compterait
  # aussi les appels une-fois-par-ticket annoncerait un gisement d'économie que rien de ce qui suit
  # ne montrerait — et c'est ce chiffre-là qu'on lit en premier. L'intitulé porte la portée, faute
  # de quoi « rejouées à l'identique » se relit comme « sur tout le run ».
  nr = 0
  for (r in n_repet) if (n_repet[r] > 1) { nr++; R_k[nr] = r; total_repet += t_repet[r] - t_repet[r]/n_repet[r] }
  if (nr > 0) {
    printf "── Commandes rejouées dans un même ticket — %s au-delà du premier passage\n", _duree(total_repet)
    for (rangr = 1; rangr <= 8 && rangr <= nr; rangr++) {
      best = 0; bi = 0
      for (i = 1; i <= nr; i++) if (!prisr[i] && t_repet[R_k[i]] > best) { best = t_repet[R_k[i]]; bi = i }
      if (bi == 0) break
      prisr[bi] = 1
      printf F_REPET, n_repet[R_k[bi]], _duree(t_repet[R_k[bi]]), _etiq(R_iid[R_k[bi]]),
             _court(R_cmd[R_k[bi]], 58)
    }
    printf "\n"
    printf "   Une commande jouée une fois PAR TICKET n'est pas un rejeu (filet CI avant push, verbe\n"
    printf "   `lib.sh` sur un parent commun) : son coût se lit « par forme de commande ».\n"
    printf "\n"
  }

  # --- 8. les appels sans retour ----------------------------------------------------------------
  if (nb_orph > 0) {
    printf "── Appels restés sans retour — la session s'est arrêtée pendant\n"
    for (i = 1; i <= nb_orph && i <= 8; i++) {
      split(orphelins[i], o, "\t")
      printf "   #%-6s %-6s %s\n", o[1], o[2], _court(o[3], 62)
    }
    printf "\n"
  }

  if (n_ctx > 0) _tokens()

  printf "  Ce que ceci ne dit pas : ce qui a été REFUSÉ à une session — `journal.sh refus`.\n"
  printf "\n"
}

# _tokens : les quatre sections de l'usage (#1465) — où passent les tokens, par ticket puis par
# famille, le poids du contexte que les résultats d'outils font relire, les fichiers les plus relus.
# Elles ne s'impriment que si le flux porte l'usage de ses messages : un flux sans `message.id` (les
# journaux fabriqués d'avant, un flux coupé avant son premier tour) n'a rien à en dire.
function _tokens(   L, F_TOK, ti, k, i, n, med, rangf, best, bf, cles, nc, pris3, fams, nf,
                    ligne, x, part, j, bj, sans_bilan) {
  L = _larg()
  F_TOK = "   %-" L "s %6d %8s %11s %12s %8s   %s · %s\n"

  # --- 9. les tokens, ticket par ticket -------------------------------------------------------
  # L'en-tête est écrit tel quel : `printf` compte des octets sous LC_ALL=C, et « entrée »,
  # « écrit », « médian » en ont un de plus que de caractères — la colonne glisserait d'autant.
  printf "── Où passent les tokens, ticket par ticket — un message compté une fois\n"
  printf "   %-" L "s  tours   entrée  cache relu  cache écrit   sortie   contexte médian · pic\n", "ticket"
  sans_bilan = 0
  for (ti = 1; ti <= nb_tickets; ti++) {
    k = tickets[ti]
    if (!(k in T_tours)) continue
    n = T_tours[k]
    split("", B)
    for (i = 1; i <= n; i++) B[i] = CTX[k SUBSEP i]
    med = _mediane(B, n)
    if (!(k in T_bilan)) sans_bilan++
    printf F_TOK, _etiq(k), n, _tok(T_in[k]), _tok(T_cr[k]), _tok(T_cw[k]),
           (k in T_bilan ? _tok(T_out[k]) : "?"), _tok(med), _tok(T_pic[k])
  }
  if (nb_tickets > 1)
    printf F_TOK, (nb_runs > 1 ? "TOTAL" : "run"), n_ctx, _tok(run_in), _tok(run_cr), _tok(run_cw),
           _tok(run_out) (sans_bilan > 0 ? "+?" : ""), _tok(_mediane(CTX_RUN, n_ctx)), _tok(pic_run)
  printf "\n"
  printf "   Un tour est un message du modèle ; son contexte, l'entrée et le cache relu ou écrit\n"
  printf "   qu'il relit. Les événements d'un même message sont regroupés par `message.id`.\n"
  printf "   La sortie n'est connue qu'au bilan `result` d'une session"
  if (sans_bilan > 0) printf " : « ? », la session n'en a rendu aucun"
  printf ".\n"
  if (sous_msg > 0)
    printf "   Sous-agents, comptés à part : %d message(s), %s tokens de contexte — hors contexte principal.\n",
           sous_msg, _tok(sous_tok)
  printf "\n"

  # --- 10. par famille d'appels ---------------------------------------------------------------
  # La part se compte sur le CONTEXTE des tours : la sortie n'est connue que par session.
  printf "── Par famille d'appels — le contexte d'un tour imputé aux outils qu'il appelle\n"
  printf "   %10s %6s %11s  cache écrit %7s %10s   %s\n", "tokens", "part", "cache relu", "appels",
         "temps", "famille"
  nc = 0
  for (x in F_tok) cles[++nc] = x
  for (rangf = 1; rangf <= nc; rangf++) {
    best = -1; bf = ""
    for (i = 1; i <= nc; i++) if (!(cles[i] in pris3) && F_tok[cles[i]] > best) { best = F_tok[cles[i]]; bf = cles[i] }
    if (bf == "") break
    pris3[bf] = 1
    printf "   %10s %5.0f%% %11s %12s %7d %10s   %s\n", _tok(F_tok[bf]),
           (tot_tok_c > 0 ? 100 * F_tok[bf] / tot_tok_c : 0), _tok(F_cr[bf]), _tok(F_cw[bf]),
           F_n[bf], (bf in FA_t ? _duree(FA_t[bf]) : "-"), bf
  }
  printf "\n"
  printf "   Un tour qui appelle trois outils de front leur partage son contexte à parts égales.\n"
  printf "   « temps » est la somme des appels de la famille, sous-agents compris — « Par outil ».\n"
  if (nb_tickets > 1) {
    printf "\n   Par ticket, les trois premières familles :\n"
    for (ti = 1; ti <= nb_tickets; ti++) {
      k = tickets[ti]
      if (!(k in FT_tot) || FT_tot[k] <= 0) continue
      nf = split(FT_fams[k], fams, "\n")
      ligne = ""
      split("", pris3)
      for (j = 1; j <= 3 && j <= nf; j++) {
        best = -1; bj = 0
        for (i = 1; i <= nf; i++) if (!(i in pris3) && FT_tok[k SUBSEP fams[i]] > best) { best = FT_tok[k SUBSEP fams[i]]; bj = i }
        if (bj == 0) break
        pris3[bj] = 1
        part = 100 * FT_tok[k SUBSEP fams[bj]] / FT_tot[k]
        ligne = ligne (ligne == "" ? "" : " · ") fams[bj] " " sprintf("%.0f%%", part)
      }
      printf "   %-" L "s %s\n", _etiq(k), ligne
    }
  }
  printf "\n"

  # --- 11. le poids du contexte ---------------------------------------------------------------
  # Le poids d'un résultat est sa taille multipliée par les tours qui le suivent : chacun le relit.
  # C'est ce poids, et non la taille seule, qui dit ce qu'une lecture coûte — un fichier lu au
  # premier tour d'une session de deux cents tours pèse deux cents fois sa taille.
  if (tot_poids > 0) {
    printf "── Le poids du contexte — un résultat d'outil, relu à chaque tour qui le suit\n"
    printf "   %10s %6s %7s %10s   %s\n", "poids", "part", "appels", "taille", "famille"
    nc = 0; split("", cles); split("", pris3)
    for (x in P_poids) cles[++nc] = x
    for (rangf = 1; rangf <= nc; rangf++) {
      best = -1; bf = ""
      for (i = 1; i <= nc; i++) if (!(cles[i] in pris3) && P_poids[cles[i]] > best) { best = P_poids[cles[i]]; bf = cles[i] }
      if (bf == "") break
      pris3[bf] = 1
      printf "   %10s %5.0f%% %7d %10s   %s\n", _tok(P_poids[bf]), 100 * P_poids[bf] / tot_poids,
             P_n[bf], _tok(P_taille[bf]), bf
    }
    printf "\n"
    printf "   %s tokens relus du fait des résultats d'outils, soit %.0f%% du contexte des tours.\n",
           _tok(tot_poids), (tot_ctx > 0 ? 100 * tot_poids / tot_ctx : 0)
    printf "   Taille ≈ caractères / 4 ; une image compte %d tokens, quelle que soit sa taille.\n", IMAGE_TOK
    printf "\n"

    # --- 12. les fichiers les plus relus ------------------------------------------------------
    nc = 0; split("", cles); split("", pris3)
    for (x in FI_poids) cles[++nc] = x
    if (nc > 0) {
      printf "── Les fichiers les plus relus — lus par Read, au poids qu'ils font relire\n"
      printf "   %10s %6s %8s %10s   %s\n", "poids", "part", "lectures", "taille", "fichier"
      for (rangf = 1; rangf <= 10 && rangf <= nc; rangf++) {
        best = -1; bf = ""
        for (i = 1; i <= nc; i++) if (!(cles[i] in pris3) && FI_poids[cles[i]] > best) { best = FI_poids[cles[i]]; bf = cles[i] }
        if (bf == "") break
        pris3[bf] = 1
        printf "   %10s %5.0f%% %8d %10s   %s\n", _tok(FI_poids[bf]), 100 * FI_poids[bf] / tot_poids,
               FI_n[bf], _tok(FI_taille[bf]), _court(bf, 70)
      }
      printf "\n"
    }
  }
}

# _classe : un tableau temps + un tableau comptes, du plus coûteux au moins coûteux.
function _classe(t, n, max,   i, k, best, bk, pris2, r, cles, nb) {
  nb = 0
  for (k in t) { nb++; cles[nb] = k }
  for (r = 1; r <= max && r <= nb; r++) {
    best = -1; bk = ""
    for (i = 1; i <= nb; i++) if (!(cles[i] in pris2) && t[cles[i]] > best) { best = t[cles[i]]; bk = cles[i] }
    if (bk == "") break
    pris2[bk] = 1
    printf "   %10s %5dx  moy %7s   %s\n", _duree(t[bk]), n[bk], _duree(t[bk]/n[bk]), bk
  }
}
AWK
)

commande_audit() {
  local cible="" tous=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --tous) tous=1 ;;
      -h | --help) usage; return 0 ;;
      -*) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; return 2 ;;
      *) cible="$1" ;;
    esac
    shift
  done

  [ -d "$ORCH_DIR" ] || {
    printf '\nAucun journal d'"'"'orchestration — aucun run n'"'"'a encore tourné ici.\n\n'
    return 0
  }

  local dirs="" d portee f
  if [ "$tous" = 1 ]; then
    for d in "$ORCH_DIR"/*/; do
      [ -d "$d" ] || continue
      dirs="$dirs${d%/}"$'\n'
    done
    portee="tout le journal"
  elif [ -n "$cible" ]; then
    [ -d "$ORCH_DIR/$cible" ] || {
      printf 'journal.sh audit : run inconnu — %s\n' "$cible" >&2
      printf 'Les runs présents : %s\n' "$(ls -1 "$ORCH_DIR" 2>/dev/null | tr '\n' ' ')" >&2
      return 2
    }
    dirs="$ORCH_DIR/$cible"$'\n'
    portee="run $cible"
  else
    # Le dernier run qui porte un FLUX — et non, comme `refus`, un RÉSULTAT : un run en cours n'a
    # pas encore rendu la main, mais son `.jsonl` est déjà écrit et parfaitement mesurable. Exiger
    # un résultat renverrait sur le run précédent celui qu'on regarde tourner.
    for d in "$ORCH_DIR"/*/; do
      [ -d "$d" ] || continue
      for f in "$d"[0-9]*.jsonl "$d"[0-9]*.jsonl.gz; do
        [ -s "$f" ] || continue
        dirs="${d%/}"$'\n'
        break
      done
    done
    [ -n "$dirs" ] || {
      printf '\nAucun flux de session dans le journal — rien à mesurer.\n\n'
      return 0
    }
    portee="run $(basename "${dirs%$'\n'}")"
  fi

  local brut="" id id_run vus=""
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    id_run="$(basename "$d")"
    # `.jsonl` AVANT `.jsonl.gz` dans le glob, et un ticket déjà vu est sauté : `gc` retire le flux
    # clair en le compactant, mais un run rejoué sous le même run-id peut laisser les deux — les
    # compter tous les deux doublerait le ticket sans que rien ne le montre.
    for f in "$d"/[0-9]*.jsonl "$d"/[0-9]*.jsonl.gz; do
      [ -s "$f" ] || continue
      id="$(basename "$f")"; id="${id%%.*}"
      case " $vus " in *" $id_run/$id "*) continue ;; esac
      vus="$vus $id_run/$id"
      # LC_ALL=C : une ligne de flux pèse jusqu'à des centaines de Ko, et un awk en locale UTF-8 y
      # paie chaque `substr`/`index` en caractères ; les octets suffisent à tout ce qu'on y cherche.
      case "$f" in
        *.gz) brut="$brut$(gzip -dc "$f" 2>/dev/null | LC_ALL=C awk -v run="$id_run" -v iid="$id" \
                -v IMAGE_TOK="$IMAGE_TOK_AUDIT" "$AWK_AUDIT_EXTRAIT")"$'\n' ;;
        *)    brut="$brut$(LC_ALL=C awk -v run="$id_run" -v iid="$id" -v IMAGE_TOK="$IMAGE_TOK_AUDIT" \
                "$AWK_AUDIT_EXTRAIT" "$f")"$'\n' ;;
      esac
    done
  done <<EOF
$dirs
EOF

  case "$brut" in
    *[!$' \t\n']*) ;;
    *) printf '\nAudit — %s : aucun appel d'"'"'outil mesurable dans ce journal.\n\n' "$portee"; return 0 ;;
  esac

  # LC_ALL=C : les comptes et les tris se font sur des octets, comme partout ailleurs dans ce dépôt.
  printf '%s' "$brut" | LC_ALL=C awk -v portee="$portee" -v SEUIL_TROU="$SEUIL_TROU_AUDIT" \
    -v IMAGE_TOK="$IMAGE_TOK_AUDIT" "$AWK_AUDIT_AGREGE"
  return 0
}

cmd="${1:-}"
[ "$#" -gt 0 ] && shift
case "$cmd" in
  gc)           commande_gc "$@" ;;
  refus)        commande_refus "$@" ;;
  audit)        commande_audit "$@" ;;
  origine)      commande_origine "$@" ;;
  -h | --help | '') usage ;;
  *) printf 'Sous-commande inconnue : %s\n\n' "$cmd" >&2; usage >&2; exit 2 ;;
esac
