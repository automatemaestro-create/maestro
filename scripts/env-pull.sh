#!/usr/bin/env bash
# Clés partagées : compléter le .env depuis les variables du dépôt (ticket #162, parent #155).
#
# À plusieurs, la moitié d'un .env n'est pas à vous : les clés Langfuse, le bot Slack et les
# endpoints sont les mêmes pour toute l'équipe. Les faire circuler à la demande (« tu peux me
# renvoyer le token ? ») coûte un aller-retour à chaque arrivant et laisse des secrets traîner dans
# les canaux de discussion. Arbitrage retenu (#155) : ces valeurs vivent dans le magasin de
# variables du dépôt, réservé aux membres, et ce script les recopie en local.
#
# ⚠ CE QUE LA BASCULE SUR GITHUB A CHANGÉ (#344, docs/27 §5) — ce script lit les **variables**
# Actions (`GET /repos/:dépôt/actions/variables`), et il ne peut pas lire autre chose : les
# **secrets** Actions sont WRITE-ONLY, GitHub n'offre aucune API pour les relire, pas même à un
# administrateur. Un vrai secret partagé n'a donc plus de véhicule automatique, et il faut le dire
# plutôt que de laisser croire à une distribution qui n'a pas lieu.
#
# La perte est POTENTIELLE et non actuelle, et c'est ce qui a permis de basculer sans rien casser :
# le magasin de variables CI/CD du projet GitLab était **vide** au moment de la mesure
# (`GET /projects/:id/variables` → HTTP 200, `[]`), les sept clés partagées des `.env` y étant
# arrivées autrement. Le mécanisme est écrit, testé et documenté ; il ne distribuait déjà rien.
#
#   bash scripts/env-pull.sh            # complète le .env avec les clés partagées qui manquent
#   bash scripts/env-pull.sh --check    # diagnostic seul — n'écrit RIEN, ne lit aucune valeur
#   bash scripts/env-pull.sh --help
#
# Principes :
#   - LE GABARIT FAIT FOI : la liste des clés partagées est LUE dans .env.example (marqueurs
#     « # [partagé] » / « # [perso] »), jamais recopiée ici. Annoter une nouvelle clé là-bas suffit.
#   - NON DESTRUCTIF : une clé déjà renseignée dans le .env n'est JAMAIS écrasée — même si la
#     variable du dépôt dit autre chose. Le script ne remplit que le vide.
#   - AUCUNE CLÉ [perso] TOUCHÉE : jetons nominatifs, chemins de machine et services locaux ne
#     transitent pas par les variables du projet, et le script ne les regarde même pas.
#   - AUCUNE VALEUR IMPRIMÉE : la sortie ne porte que des NOMS de clés et des comptes. Les valeurs
#     ne traversent ni l'affichage, ni un argument de commande (lisible par tout processus de la
#     machine) — seulement des fichiers temporaires en 0600, effacés en sortie.
#   - FRANC SUR CE QU'IL NE PEUT PAS : une clé partagée absente des variables du dépôt est dite
#     comme telle, avec la commande qui la publie. Rien n'est deviné.
#
# Publier une valeur partagée (geste de MAINTENEUR, une fois par clé) :
#
#     gh variable set LANGFUSE_HOST --body "https://cloud.langfuse.com"
#
# Un secret vrai se pose par `gh secret set`, mais ce script ne pourra JAMAIS le relire : à publier
# en variable seulement ce qui peut l'être, et à transmettre à la main le reste.
#
# Pas de `set -e` : chaque étape rend son propre verdict.

set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GABARIT="$RACINE/.env.example"
CIBLE="$RACINE/.env"

# Source de la liste des variables. Par défaut : l'API du dépôt via `gh`. Un fichier JSON peut la
# remplacer (MAESTRO_ENV_PULL_SOURCE) — c'est la couture qui rend le script testable hors ligne,
# sans compte de forge ni secret réel.
SOURCE_JSON="${MAESTRO_ENV_PULL_SOURCE:-}"

# Séparateur des tables internes (clé/état/valeur). Nommé plutôt que tapé : une tabulation
# littérale au fil du code ne survit pas au premier éditeur qui « nettoie » les blancs.
TAB=$'\t'

MODE_CHECK=0
MODE_LISTE=0

usage() {
  cat <<'USAGE'
Complète le .env avec les clés PARTAGÉES publiées dans les variables du dépôt.

  bash scripts/env-pull.sh [options]

Options :
  --check       Diagnostic seul : dit ce qui serait posé, sans rien écrire ni lire aucune valeur.
  --manquantes  Imprime (un nom par ligne) les clés partagées encore à compléter, puis s'arrête.
                Aucun réseau, aucune écriture — c'est ce que `scripts/setup.sh` interroge.
  -h, --help    Cette aide.

Ce qui est partagé, ce qui ne l'est pas, est marqué clé par clé dans .env.example
(« # [partagé] » / « # [perso] »). Une clé déjà renseignée n'est jamais écrasée ; aucune valeur
n'est affichée. Publier une valeur partagée (mainteneur, une fois) :

  gh variable set LANGFUSE_HOST --body "https://cloud.langfuse.com"

Les SECRETS Actions (`gh secret set`) sont write-only : ce script ne peut pas les relire, et ce
qui doit rester secret se transmet donc à la main.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --check)      MODE_CHECK=1 ;;
    --manquantes) MODE_LISTE=1 ;;
    -h|--help)    usage; exit 0 ;;
    *)         printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# --- Fichiers temporaires : 0600, effacés quoi qu'il arrive -------------------------------------
umask 077
TEMPS=()
nettoie() { [ "${#TEMPS[@]}" -gt 0 ] && rm -f "${TEMPS[@]}"; }
trap nettoie EXIT
# Hors du dépôt, à dessein (#234) : ces fichiers portent des VALEURS de secrets. Leur chemin n'est
# jamais imprimé — donc rien n'oriente une session vers eux — et les écrire sous la racine ferait
# transiter un secret par le répertoire de travail pour le seul confort d'une lecture qu'on ne veut
# précisément pas rendre possible. Le `umask 077` et le trap de nettoyage ci-dessus font le reste.
temporaire() {
  local f
  f="$(mktemp "${TMPDIR:-/tmp}/maestro-env-pull.XXXXXX")" || return 1
  TEMPS+=("$f")
  printf '%s\n' "$f"
}

# --- Le gabarit fait foi : quelles clés sont partagées ? ----------------------------------------
# Un marqueur OUVRE sa ligne de commentaire (« # [partagé] … ») et vaut jusqu'au marqueur suivant.
# Les clés commentées (« # DATABASE_URL=… ») comptent : elles portent aussi un marqueur.
cles_marquees() {
  local marqueur="$1"
  LC_ALL=C awk -v vise="$marqueur" '
    /^# \[perso\]/   { m = "perso";   next }
    /^# \[partagé\]/ { m = "partagé"; next }
    /^#? ?[A-Za-z_][A-Za-z0-9_]*=/ {
      ligne = $0
      sub(/^# ?/, "", ligne)
      cle = substr(ligne, 1, index(ligne, "=") - 1)
      if (cle ~ /^[A-Za-z_][A-Za-z0-9_]*$/ && m == vise && !(cle in vues)) {
        vues[cle] = 1
        print cle
      }
    }
  ' "$GABARIT"
}

# Clés du gabarit sans aucun marqueur : la convention a dérivé, on le dit (elles sont ignorées,
# faute de savoir si les publier serait une fuite).
cles_sans_marqueur() {
  LC_ALL=C awk '
    /^# \[perso\]/   { m = "perso";   next }
    /^# \[partagé\]/ { m = "partagé"; next }
    /^#? ?[A-Za-z_][A-Za-z0-9_]*=/ {
      ligne = $0
      sub(/^# ?/, "", ligne)
      cle = substr(ligne, 1, index(ligne, "=") - 1)
      if (cle ~ /^[A-Za-z_][A-Za-z0-9_]*$/ && m == "" && !(cle in vues)) {
        vues[cle] = 1
        print cle
      }
    }
  ' "$GABARIT"
}

# --- État d'une clé dans le .env local ----------------------------------------------------------
# « vide » (à compléter), « renseignée » (intouchable) ou « absente » (à ajouter). Aucune valeur
# n'est lue : grep ne rend que son verdict.
etat_cle() {
  local cle="$1"
  if grep -qE "^${cle}=[[:space:]]*$" "$CIBLE" 2>/dev/null; then printf 'vide\n'; return 0; fi
  if grep -qE "^${cle}=" "$CIBLE" 2>/dev/null; then printf 'renseignée\n'; return 0; fi
  printf 'absente\n'
}

# --- Variables du dépôt --------------------------------------------------------------------------
# Écrit un objet JSON PAR LIGNE dans le fichier passé en argument. `gh` n'est exigé que là : un
# --check sur un dépôt sans clé partagée manquante n'ouvre aucune connexion.
#
# `--jq '.variables[]'` (le jq embarqué dans `gh`, pas une dépendance de plus) sert à APLATIR :
# l'API rend « {"total_count":N,"variables":[…]} », donc des objets à la profondeur 2, quand
# l'analyseur ci-dessous compte les accolades et n'émet qu'à la profondeur 1. Aplatir ici coûte un
# drapeau ; l'apprendre à l'analyseur lui coûterait un mode.
recupere_variables() {
  local dest="$1" depot

  if [ -n "$SOURCE_JSON" ]; then
    if [ ! -f "$SOURCE_JSON" ]; then
      printf 'MAESTRO_ENV_PULL_SOURCE : fichier introuvable (%s)\n' "$SOURCE_JSON" >&2
      return 1
    fi
    cat "$SOURCE_JSON" > "$dest"
    return 0
  fi

  if ! command -v gh >/dev/null 2>&1; then
    printf "gh n'est pas installé — impossible de lire les variables du dépôt.\n" >&2
    printf 'Voir https://github.com/cli/cli, ou : bash scripts/setup.sh --only prerequis\n' >&2
    return 1
  fi
  if ! gh auth status >/dev/null 2>&1; then
    printf 'gh non authentifié. Lancer d abord : gh auth login\n' >&2
    return 1
  fi

  depot="$(bash "$RACINE/scripts/gitlab/lib.sh" depot-courant)" || return 1
  # Un magasin VIDE est une réponse valide (« 0 variable publiée ») et non une panne : on ne juge
  # donc que le code de retour, jamais la forme de la sortie.
  if ! gh api --paginate "repos/$depot/actions/variables" --jq '.variables[]' > "$dest" 2>/dev/null; then
    printf 'Lecture des variables du dépôt refusée — droit de collaborateur requis.\n' >&2
    return 1
  fi
}

# Analyse le JSON des variables (un objet par ligne) et imprime une ligne « clé<TAB>état<TAB>valeur »
# par variable. États : ok (valeur utilisable), vide (variable sans valeur), multiligne (valeur à
# retours chariot — inexploitable dans un .env).
# La valeur n'est présente que sur les lignes « ok », et jamais quand sansval=1 (mode --check).
# Analyseur JSON en awk pur, comme le reste de l'outillage du dépôt (pas de jq ni de python) :
# lecture de chaînes échappement par échappement, y compris \uXXXX ré-encodé en UTF-8 — le mojibake
# de #141 est venu d'un décodage approximatif, on ne recommence pas.
analyse_variables() {
  local source="$1" sansval="${2:-0}"
  LC_ALL=C awk -v sansval="$sansval" '
    function hex2dec(h,   i, c, v, d) {
      v = 0
      for (i = 1; i <= length(h); i++) {
        c = tolower(substr(h, i, 1))
        d = index("0123456789abcdef", c) - 1
        if (d < 0) return -1
        v = v * 16 + d
      }
      return v
    }
    function utf8(code) {
      if (code <= 0) return ""
      if (code < 128) return sprintf("%c", code)
      if (code < 2048) return sprintf("%c%c", 192 + int(code / 64), 128 + (code % 64))
      return sprintf("%c%c%c", 224 + int(code / 4096), 128 + int((code % 4096) / 64), 128 + (code % 64))
    }
    # Lit la chaîne JSON qui commence en p (sur son guillemet ouvrant) : dépose le texte décodé
    # dans S_STR et renvoie la position juste après le guillemet fermant.
    function lit_chaine(s, p, n,   out, c, e, code) {
      p++
      out = ""
      while (p <= n) {
        c = substr(s, p, 1)
        if (c == "\\") {
          e = substr(s, p + 1, 1)
          if      (e == "n") out = out "\n"
          else if (e == "t") out = out "\t"
          else if (e == "r") out = out "\r"
          else if (e == "b") out = out "\b"
          else if (e == "f") out = out "\f"
          else if (e == "u") {
            code = hex2dec(substr(s, p + 2, 4))
            out = out utf8(code)
            p += 6
            continue
          }
          else out = out e          # \" \\ \/ … : le caractère littéral
          p += 2
          continue
        }
        if (c == "\"") { p++; break }
        out = out c
        p++
      }
      S_STR = out
      return p
    }
    function emet(k, v, ok,   etat) {
      if (k == "") return
      if (!ok || v == "")      etat = "vide"
      else if (v ~ /[\n\r\t]/) etat = "multiligne"
      else                     etat = "ok"
      if (etat == "ok" && !sansval) printf "%s\t%s\t%s\n", k, etat, v
      else                          printf "%s\t%s\t\n", k, etat
    }
    { buf = buf $0 "\n" }
    END {
      n = length(buf); p = 1; profondeur = 0
      champ = ""; cle = ""; val = ""; ok = 0
      while (p <= n) {
        c = substr(buf, p, 1)
        if (c == "{") {
          profondeur++
          if (profondeur == 1) { cle = ""; val = ""; ok = 0; champ = "" }
          p++; continue
        }
        if (c == "}") {
          if (profondeur == 1) emet(cle, val, ok)
          profondeur--; p++; continue
        }
        if (c == "\"") {
          p = lit_chaine(buf, p, n)
          texte = S_STR
          q = p
          while (q <= n && substr(buf, q, 1) ~ /[ \t\r\n]/) q++
          if (substr(buf, q, 1) == ":") { champ = texte; p = q + 1; continue }
          if      (champ == "name")  cle = texte
          else if (champ == "value") { val = texte; ok = 1 }
          champ = ""
          continue
        }
        p++
      }
    }
  ' "$source"
}

# --- Écriture : compléter sans jamais écraser ---------------------------------------------------
# Remplit sur place les lignes « CLÉ= » restées vides, ajoute les clés absentes en fin de fichier.
# Tout le reste du .env ressort tel quel : commentaires, ordre, espaces et fins de ligne CRLF.
# En bash plutôt qu'en awk, à dessein : l'awk de Git Bash lit en mode TEXTE et convertit les CRLF
# en LF — il réécrirait, sans le dire, des lignes que ce script n'a pas à toucher.
# Aucune valeur ne devient un argv visible par les autres processus : elle est lue par `read` et
# écrite par `printf`, deux builtins ; le seul appel externe (grep) ne reçoit qu'un nom de clé.
applique() {
  local paires="$1" sortie="$2" ligne sans_cr fin cle entete=0 a_poser="|" fin_fichier=""

  while IFS= read -r ligne || [ -n "$ligne" ]; do
    [ -n "$ligne" ] && a_poser="$a_poser${ligne%%"$TAB"*}|"
  done < "$paires"

  {
    while IFS= read -r ligne || [ -n "$ligne" ]; do
      sans_cr="${ligne%$'\r'}"
      fin=""
      [ "$sans_cr" != "$ligne" ] && fin=$'\r'
      fin_fichier="$fin"   # les lignes ajoutées suivront la convention du fichier (CRLF ou LF)
      if [[ "$sans_cr" =~ ^([A-Za-z_][A-Za-z0-9_]*)=[[:space:]]*$ ]]; then
        cle="${BASH_REMATCH[1]}"
        if [[ "$a_poser" == *"|$cle|"* ]]; then
          printf '%s=%s%s\n' "$cle" "$(valeur_de "$cle" "$paires")" "$fin"
          a_poser="${a_poser/|$cle|/|}"
          continue
        fi
      fi
      printf '%s\n' "$ligne"
    done < "$CIBLE"

    # Ce qui n'avait pas de ligne d'accueil : ajouté en fin de fichier, sous un en-tête qui dit
    # d'où ça vient (relire un .env six mois plus tard, c'est la moitié du sujet).
    while IFS= read -r ligne || [ -n "$ligne" ]; do
      [ -n "$ligne" ] || continue
      cle="${ligne%%"$TAB"*}"
      [[ "$a_poser" == *"|$cle|"* ]] || continue
      if [ "$entete" = 0 ]; then
        printf '%s\n# --- Clés partagées récupérées des variables du dépôt (bash scripts/env-pull.sh) ---%s\n' \
          "$fin_fichier" "$fin_fichier"
        entete=1
      fi
      printf '%s=%s%s\n' "$cle" "${ligne#*"$TAB"}" "$fin_fichier"
    done < "$paires"
  } > "$sortie"
}

# valeur_de <clé> <fichier-de-paires> -> la valeur associée, sur stdout (jamais affichée : le
# résultat n'est consommé que par la substitution de commande d'applique).
valeur_de() {
  local ligne
  ligne="$(grep -m1 "^$1$TAB" "$2")" || return 1
  printf '%s' "${ligne#*"$TAB"}"
}

# --- Rendu ---------------------------------------------------------------------------------------
liste() {
  local titre="$1" contenu="$2"
  [ -n "$contenu" ] || return 0
  printf '  %s : %s\n' "$titre" "$(printf '%s' "$contenu" | tr '\n' ' ' | sed 's/ $//')"
}

# ================================================================================================
# Déroulé
# ================================================================================================

if [ ! -f "$GABARIT" ]; then
  printf '.env.example introuvable (%s) — lancez le script depuis un clone du dépôt.\n' "$GABARIT" >&2
  exit 1
fi

PARTAGEES="$(cles_marquees 'partagé')"
SANS_MARQUEUR="$(cles_sans_marqueur)"

if [ -n "$SANS_MARQUEUR" ] && [ "$MODE_LISTE" = 0 ]; then
  printf 'Clés du gabarit sans marqueur [perso]/[partagé] — ignorées, à annoter dans .env.example :\n'
  liste 'sans marqueur' "$SANS_MARQUEUR"
fi

if [ -z "$PARTAGEES" ]; then
  [ "$MODE_LISTE" = 1 ] || printf "Aucune clé [partagé] dans .env.example : rien à récupérer.\n"
  exit 0
fi

# --manquantes tolère un .env absent (tout est alors à compléter) : c'est justement l'état d'un
# clone frais, et l'appelant (setup.sh) veut la liste, pas une erreur.
if [ ! -f "$CIBLE" ] && [ "$MODE_LISTE" = 0 ]; then
  printf ".env absent. Créez-le d'abord (il n'est jamais écrasé ensuite) :\n" >&2
  printf '  bash scripts/setup.sh --only env\n' >&2
  exit 1
fi

# Tri des clés partagées selon l'état du .env local.
A_COMPLETER=""
DEJA=""
while IFS= read -r cle; do
  [ -n "$cle" ] || continue
  case "$(etat_cle "$cle")" in
    renseignée) DEJA="$DEJA$cle"$'\n' ;;
    *)          A_COMPLETER="$A_COMPLETER$cle"$'\n' ;;
  esac
done <<< "$PARTAGEES"

# Mode liste : juste les noms, rien d'autre — sortie destinée à être consommée par un script.
if [ "$MODE_LISTE" = 1 ]; then
  printf '%s' "$A_COMPLETER"
  exit 0
fi

NB_PARTAGEES="$(printf '%s' "$PARTAGEES" | grep -c . || true)"
NB_DEJA="$(printf '%s' "$DEJA" | grep -c . || true)"

printf 'Clés partagées du gabarit : %s — dont %s déjà renseignée(s) dans .env (préservée(s)).\n' \
  "$NB_PARTAGEES" "$NB_DEJA"

if [ -z "$A_COMPLETER" ]; then
  printf 'Rien à compléter : toutes les clés partagées sont renseignées.\n'
  exit 0
fi

liste 'à compléter' "$A_COMPLETER"

# Lecture des variables du projet.
BRUT="$(temporaire)" || exit 1
recupere_variables "$BRUT" || exit 1

VARS="$(temporaire)" || exit 1
analyse_variables "$BRUT" "$MODE_CHECK" > "$VARS"

PAIRES="$(temporaire)" || exit 1
: > "$PAIRES"

POSABLES=""
INDISPONIBLES=""
ILLISIBLES=""
while IFS= read -r cle; do
  [ -n "$cle" ] || continue
  ligne="$(grep -m1 "^${cle}"$'\t' "$VARS" 2>/dev/null || true)"
  if [ -z "$ligne" ]; then
    INDISPONIBLES="$INDISPONIBLES$cle"$'\n'
    continue
  fi
  etat="$(printf '%s' "$ligne" | cut -f2)"
  case "$etat" in
    ok)
      POSABLES="$POSABLES$cle"$'\n'
      printf '%s\t%s\n' "$cle" "$(printf '%s' "$ligne" | cut -f3-)" >> "$PAIRES"
      ;;
    *)  ILLISIBLES="$ILLISIBLES$cle ($etat)"$'\n' ;;
  esac
done <<< "$A_COMPLETER"

# Variables publiées sans clé correspondante dans le gabarit : souvent une coquille côté dépôt.
HORS_GABARIT=""
CONNUES="$(cles_marquees 'partagé'; cles_marquees 'perso')"
while IFS= read -r ligne; do
  [ -n "$ligne" ] || continue
  cle="$(printf '%s' "$ligne" | cut -f1)"
  printf '%s\n' "$CONNUES" | grep -qx "$cle" || HORS_GABARIT="$HORS_GABARIT$cle"$'\n'
done < "$VARS"

liste 'disponibles dans les variables du dépôt' "$POSABLES"
liste 'absentes des variables du dépôt' "$INDISPONIBLES"
liste 'publiées mais inexploitables' "$ILLISIBLES"
liste 'variables du dépôt hors gabarit (ignorées)' "$HORS_GABARIT"

if [ -n "$INDISPONIBLES" ]; then
  printf "  → à publier une fois, par un mainteneur : gh variable set <CLÉ> --body <valeur>\n"
  printf "    (un SECRET vrai se pose par « gh secret set » et ne sera pas relisible ici — docs/27 §5)\n"
fi

if [ "$MODE_CHECK" = 1 ]; then
  printf 'Mode --check : rien écrit, aucune valeur lue.\n'
  exit 0
fi

if [ ! -s "$PAIRES" ]; then
  printf "Aucune valeur à poser : le .env est inchangé.\n"
  exit 0
fi

SORTIE="$(temporaire)" || exit 1
if ! applique "$PAIRES" "$SORTIE"; then
  printf 'Réécriture du .env impossible — fichier inchangé.\n' >&2
  exit 1
fi
if [ ! -s "$SORTIE" ]; then
  printf 'Réécriture du .env vide — fichier inchangé (anomalie).\n' >&2
  exit 1
fi

# Copie du contenu plutôt que `mv` : le .env garde son inode et ses permissions.
if ! cat "$SORTIE" > "$CIBLE"; then
  printf 'Écriture du .env impossible.\n' >&2
  exit 1
fi

NB_POSEES="$(grep -c . "$PAIRES" || true)"
printf '%s clé(s) partagée(s) posée(s) dans .env — aucune valeur existante touchée.\n' "$NB_POSEES"
