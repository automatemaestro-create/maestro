#!/usr/bin/env bash
# Le journal d'orchestration — `.maestro/orchestrate/` : son ménage (#198) et sa lecture (#235).
#
#   bash scripts/orchestrate/journal.sh gc           # purge les vieux runs, compacte leurs flux
#   bash scripts/orchestrate/journal.sh gc --check   # ce qui serait retiré, sans rien écrire
#   bash scripts/orchestrate/journal.sh refus        # ce qui a été refusé au dernier run
#   bash scripts/orchestrate/journal.sh refus --tous # le même agrégat, sur tout le journal
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
# --- Réglages -----------------------------------------------------------------------------------------
# MAESTRO_ORCHESTRATE_JOURNAL_RUNS   nombre de runs conservés (défaut 10)
# MAESTRO_ORCHESTRATE_JOURNAL_GC=0   désactive le passage automatique appelé par `run.sh`
# MAESTRO_ORCHESTRATE_SILENCE        délai au-delà duquel un run est présumé fini (défaut 900 s)

set -uo pipefail

ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RACINE="$(cd "$ICI/../.." && pwd)"
SETTINGS_RUN="$RACINE/scripts/orchestrate/settings.run.json"
SETTINGS_DEPOT="$RACINE/.claude/settings.json"

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

ok()     { printf '  ✓ %s\n' "$*"; }
ignore() { printf '  ~ %s\n' "$*"; }

usage() {
  cat <<'USAGE'
Le journal d'orchestration — .maestro/orchestrate/

  bash scripts/orchestrate/journal.sh gc [--check] [--auto] [--courant <run-id>]
  bash scripts/orchestrate/journal.sh refus [<run-id> | --tous]
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

Le journal lu est TOUJOURS celui du clone principal, y compris appelé depuis un worktree : c'est
là que le pilote écrit, et y renvoyer par un chemin absolu ferait refuser la lecture (§11.7).

  <run-id>     un run précis, plutôt que le dernier
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
# bloc. Même lecture que le `deny_de` de `guard.sh` — et volontairement pas une copie figée ici : le
# classement doit suivre les règles réellement en vigueur, sinon il se périme sans que rien ne le
# dise, ce qui est exactement le défaut que #307 est venu corriger.
bloc_de() {
  [ -f "$1" ] || return 0
  awk -v bloc="$2" '$0 ~ "\"" bloc "\"[[:space:]]*:" { dans = 1 } dans { print; if (/\]/) exit }' \
    "$1" 2>/dev/null |
    grep -o '"[^"]*"' | tr -d '"' | grep -v "^$2\$" | sed "s/^/$2	/"
}

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

# matche(seg, regles, n, large) : une de ces règles couvre-t-elle ce maillon ? On rejoue le matching
# du CLI, qui est un matching de PRÉFIXE DE COMMANDE : `Bash(git status:*)` couvre « git status » et
# tout ce qui commence par « git status » ; sans `:*` la règle est exacte. Le maillon est jugé sur
# son TEXTE et non sur son premier mot — une règle borne « command -v » ou « bash scripts/… »,
# qu'un verbe seul ne rendrait pas.
#
# `large` sert les règles `ask`/`deny`, et l'écart est délibéré : leurs OPTIONS peuvent être
# n'importe où dans la commande. Le CLI comprend les options, un préfixe non — sans cela
# `git commit --no-edit --no-verify` échapperait à `Bash(git commit --no-verify:*)`, et le refus
# VOULU qu'il déclenche irait grossir « inclassé ». La tête de la règle, elle, reste un préfixe :
# la relâcher aussi ferait tomber `git commit -m "clean up"` sous `Bash(git clean:*)`.
function matche(seg, regles, n, large,   i, r, p, k, mots, j, tete, ok) {
  for (i = 1; i <= n; i++) {
    r = regles[i]
    if (r !~ /^Bash\(.*\)$/) continue
    p = substr(r, 6, length(r) - 6)
    if (p !~ /:\*$/) { if (seg == p) return 1; continue }
    p = substr(p, 1, length(p) - 2)
    if (!large) {
      if (seg == p || index(seg, p " ") == 1) return 1
      continue
    }
    k = split(p, mots, /[ \t]+/)
    tete = ""
    for (j = 1; j <= k && substr(mots[j], 1, 1) != "-"; j++)
      tete = tete (tete == "" ? "" : " ") mots[j]
    if (tete != "" && seg != tete && index(seg, tete " ") != 1) continue
    ok = 1
    for (; j <= k; j++) if (seg !~ ("(^|[ \t])" mots[j] "([ \t]|$)")) { ok = 0; break }
    if (ok) return 1
  }
  return 0
}

# outil_couvert(nom) : l'outil lui-même est-il autorisé, nu (« Write ») ou paramétré (« Write(…) ») ?
function outil_couvert(nom,   i) {
  for (i = 1; i <= n_allow; i++)
    if (allow[i] == nom || index(allow[i], nom "(") == 1) return 1
  return 0
}

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
    } else if (!outil_couvert(outil)) {
      famille = "trou"
      if (!(outil in nu_n)) nb_nu++
      nu_n[outil]++
    } else {
      famille = (chemin_hors(cible) ? "chemin" : (formes != "" ? "forme" : "reste"))
    }
  }
  fam_n[famille]++
}

END {
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
  elif [ -n "$cible" ]; then
    [ -d "$ORCH_DIR/$cible" ] || {
      printf 'journal.sh refus : run inconnu — %s\n' "$cible" >&2
      printf 'Les runs présents : %s\n' "$(ls -1 "$ORCH_DIR" 2>/dev/null | tr '\n' ' ')" >&2
      return 2
    }
    dirs="$ORCH_DIR/$cible"$'\n'
    portee="Refus de permission — run $cible"
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
  local regles f bloc
  regles="$(mktemp "${TMPDIR:-/tmp}/maestro-allow.XXXXXX" 2>/dev/null)" || regles=""
  if [ -n "$regles" ]; then
    trap 'rm -f "$regles" 2>/dev/null' RETURN
    for f in "$SETTINGS_RUN" "$SETTINGS_DEPOT"; do
      for bloc in allow ask deny; do bloc_de "$f" "$bloc"; done
    done | sort -u > "$regles"
  fi

  # LC_ALL=C : le tri et les comptes se font sur des octets, comme partout ailleurs dans ce dépôt.
  printf '%s' "$brut" | LC_ALL=C awk -v portee="$portee" -v regles="$regles" "$AWK_AGREGE"
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

cmd="${1:-}"
[ "$#" -gt 0 ] && shift
case "$cmd" in
  gc)           commande_gc "$@" ;;
  refus)        commande_refus "$@" ;;
  origine)      commande_origine "$@" ;;
  -h | --help | '') usage ;;
  *) printf 'Sous-commande inconnue : %s\n\n' "$cmd" >&2; usage >&2; exit 2 ;;
esac
