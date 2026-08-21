#!/usr/bin/env bash
# Le `gh` factice de tests/test_migration.py — un dépôt GitHub qui TIENT UNE SÉQUENCE.
#
# POURQUOI EN BASH, ET POURQUOI DANS UN FICHIER. Les autres doubles du dépôt sont des chaînes
# Python posées dans un `fauxbin` (cf. tests/harnais_forge.py). Celui-ci ne peut pas l'être, pour
# une raison de coût mesurée : sous MSYS, un shim « bash → python » coûte ~0,38 s par appel, contre
# ~0,12 s pour un shim bash seul. `import-github.sh` demande le dernier numéro AVANT chaque
# création — l'invariant d'ordre est à ce prix —, si bien qu'un import complet fait une quarantaine
# d'appels et la suite entière plusieurs centaines. Jouée à côté des autres suites d'outillage, la
# version Python a fait dépasser son délai à l'import et poussé quatre tests sensibles à la charge
# dans le rouge : 1 h 17 de suite complète au lieu de ~15 min. Et un vrai fichier plutôt qu'une
# chaîne, parce qu'un script shell cité dans du Python devient illisible à la troisième couche
# d'échappement — celui-ci se relit, et se joue à la main.
#
# ⚠ AUCUNE SUBSTITUTION DE COMMANDE ICI, ET C'EST LE POINT LE PLUS FACILE À DÉFAIRE. Un `$(…)` est
# un fork, et un fork sous MSYS coûte ~0,1 s : la première version de ce double en faisait cinq par
# appel (lire le scénario, extraire un champ) et retombait à **853 ms par appel**, soit plus cher
# que le shim Python qu'il remplaçait. Tout passe donc par des affectations : le scénario est
# **sourcé** (des lignes `clé=valeur` deviennent des variables), les extractions rendent leur
# résultat dans une globale, et les fichiers se lisent par `read`. Ajouter un `$(…)` dans une
# fonction appelée par appel annulerait le gain sans que rien ne le signale.
#
# CE QU'IL NE FAIT PAS : comprendre ce qu'on lui envoie. Les corps reçus sont rangés TELS QUELS,
# à charge pour le test de les relire avec un vrai parseur JSON — plus fidèle, de toute façon, que
# de les faire transiter par une deuxième sérialisation. Les réponses, elles, sont minimales :
# `import-github.sh` n'y lit qu'un `"number"` et une `"html_url"`.
#
# ⚠ LIMITE ASSUMÉE : `extrait` s'arrête au premier guillemet, donc une valeur portant un `\"`
# échappé serait tronquée. Vraie du jeu d'essai (noms de labels, titres de milestones), fausse en
# général. Elle ne touche ni les descriptions ni les commentaires, qui ne sont jamais découpés ici.
#
# Piloté par $MAESTRO_FAUX_DEPOT (le répertoire d'état) et $MAESTRO_FAUX_NOM (le « owner/nom » visé).

D="$MAESTRO_FAUX_DEPOT"

# Une ligne par appel, arguments séparés par des TABULATIONS : c'est ainsi que le test démontre
# qu'une commande annoncée sans effet — `--check` — n'a rien écrit. IFS est restauré plutôt que
# confiné dans un sous-shell, qui coûterait un fork.
_ifs="$IFS"; IFS=$'\t'; printf '%s\n' "$*" >> "$D/journal.tsv"; IFS="$_ifs"

# Le scénario : des lignes `clé=valeur` SOURCÉES, donc lues une fois et sans fork. Les valeurs sont
# écrites par le test (`Migration.pose_scenario`), jamais par le script sous test.
refuser_issue=""; perdre_reponse=""; numero_menteur=""; liste_en_retard=""
refuser_commentaire_issue=""; refuser_commentaire_rang=""
intercaler_apres_n=""; intercaler_apres_k=""
# shellcheck disable=SC1091  # fichier de scénario écrit par le test, absent au premier appel
[ -f "$D/scenario" ] && . "$D/scenario"

# extrait <json> <clé> -> pose EXTRAIT (voir la limite ci-dessus). Une globale plutôt qu'un
# `printf` capturé : le capturer demanderait un `$(…)`, donc un fork par appel.
EXTRAIT=""
extrait() {
  local reste="${1#*\"$2\":\"}"
  EXTRAIT="${reste%%\"*}"
}

# lire_tout <fichier> -> pose CONTENU. `read -d ''` s'arrête sur un NUL, donc lit tout le fichier ;
# il rend 1 faute d'avoir trouvé son délimiteur, ce qui n'est pas une erreur ici.
CONTENU=""
lire_tout() { CONTENU=""; IFS= read -r -d '' CONTENU < "$1" || true; }

refus() { printf '{"message":"%s"}' "$1"; exit 1; }

[ "${1:-}" = "auth" ] && exit 0
[ "${1:-}" = "api" ] || refus "verbe non simulé"

# La ligne de commande : la méthode, le chemin, et le fichier de corps.
methode="GET"; chemin=""; entree=""
shift
while [ $# -gt 0 ]; do
  case "$1" in
    --method|-X) methode="$2"; shift 2; continue ;;
    --input)     entree="$2";  shift 2; continue ;;
    -*)          shift; continue ;;
  esac
  [ -n "$chemin" ] || chemin="$1"
  shift
done

prefixe="repos/$MAESTRO_FAUX_NOM"
seq=0
[ -f "$D/seq" ] && read -r seq < "$D/seq"

# --- Lectures -------------------------------------------------------------------------------------
if [ "$methode" = "GET" ]; then
  case "$chemin" in
    "$prefixe")
      printf '{"full_name":"x","visibility":"private","has_issues":true,"permissions":{"push":true}}'
      ;;
    "$prefixe"/issues\?*)
      # La liste est RÉPLIQUÉE côté GitHub, donc parfois en retard d'un objet sur la création.
      dernier="$seq"
      [ -n "$liste_en_retard" ] && [ "$dernier" -gt 0 ] && dernier=$((dernier - 1))
      if [ "$dernier" -eq 0 ]; then
        printf '[]'
      else
        printf '[{"number":%s,"state":"open"}]' "$dernier"
      fi
      ;;
    "$prefixe"/milestones*)
      out="["; k=1
      while [ -f "$D/milestone-$k.json" ]; do
        [ "$k" -gt 1 ] && out="$out,"
        lire_tout "$D/milestone-$k.json"; extrait "$CONTENU" title
        out="$out{\"number\":$k,\"title\":\"$EXTRAIT\"}"
        k=$((k + 1))
      done
      printf '%s]' "$out"
      ;;
    "$prefixe"/issues/*)
      # Un 404 rend un CORPS : `gh_lire` ne retente que sur une réponse VIDE, jamais sur son
      # contenu — « l'objet n'existe pas » est une réponse, le silence n'en est pas une.
      n="${chemin##*/}"
      [ -f "$D/issue-$n.json" ] || refus "Not Found"
      printf '{"number":%s,"state":"open","html_url":"https://ex/%s"}' "$n" "$n"
      ;;
    *) refus "chemin non simulé" ;;
  esac
  exit 0
fi

# --- Écritures ------------------------------------------------------------------------------------
corps=""
if [ -n "$entree" ]; then lire_tout "$entree"; corps="$CONTENU"; fi

case "$chemin" in
  "$prefixe"/labels)
    extrait "$corps" name; nom="$EXTRAIT"
    if [ -f "$D/labels.txt" ]; then
      while IFS= read -r deja; do
        if [ "$deja" = "$nom" ]; then
          printf '{"message":"Validation Failed","errors":[{"code":"already_exists"}]}'
          exit 1
        fi
      done < "$D/labels.txt"
    fi
    printf '%s\n' "$nom" >> "$D/labels.txt"
    printf '{"name":"%s"}' "$nom"
    ;;

  "$prefixe"/milestones)
    extrait "$corps" title; titre="$EXTRAIT"
    k=1
    while [ -f "$D/milestone-$k.json" ]; do
      lire_tout "$D/milestone-$k.json"; extrait "$CONTENU" title
      if [ "$EXTRAIT" = "$titre" ]; then
        printf '{"message":"Validation Failed","errors":[{"code":"already_exists"}]}'
        exit 1
      fi
      k=$((k + 1))
    done
    printf '%s' "$corps" > "$D/milestone-$k.json"
    printf '{"number":%s,"title":"x"}' "$k"
    ;;

  "$prefixe"/issues)
    attendu=$((seq + 1))
    [ "$refuser_issue" = "$attendu" ] && refus "refus simulé"
    if [ -f "$D/refuser-prochain-post" ]; then
      # La RETENTATIVE d'un POST dont la réponse s'est perdue : cette fois la requête n'arrive
      # même pas. C'est le cas pour lequel la relecture du dernier numéro a été écrite — sans
      # elle, `creer_objet` conclurait à l'échec d'une création qui a bel et bien eu lieu.
      rm -f "$D/refuser-prochain-post"
      refus "connexion perdue"
    fi
    printf '%s' "$corps" > "$D/issue-$attendu.json"
    printf '%s' "$attendu" > "$D/seq"
    # Un objet intercalé par quelqu'un d'autre : sur GitHub, issues ET pull requests partagent UNE
    # SEULE séquence, donc une PR ouverte pendant l'import consomme un numéro.
    if [ "$intercaler_apres_n" = "$attendu" ]; then
      i=0; n="$attendu"
      while [ "$i" -lt "${intercaler_apres_k:-0}" ]; do
        n=$((n + 1)); i=$((i + 1))
        printf '{"title":"PR d\\u0027un tiers","body":"","labels":[]}' > "$D/issue-$n.json"
      done
      printf '%s' "$n" > "$D/seq"
    fi
    if [ "$perdre_reponse" = "$attendu" ]; then
      : > "$D/refuser-prochain-post"
      refus "réponse perdue"
    fi
    rendu="$attendu"
    [ "$numero_menteur" = "$attendu" ] && rendu=$((attendu + 5))
    printf '{"number":%s,"html_url":"https://ex/%s"}' "$rendu" "$attendu"
    ;;

  */comments)
    reste="${chemin%/comments}"; n="${reste##*/}"; k=1
    while [ -f "$D/commentaire-$n-$k.json" ]; do k=$((k + 1)); done
    if [ "$refuser_commentaire_issue" = "$n" ] && [ "$refuser_commentaire_rang" = "$k" ]; then
      refus "refus simulé sur le commentaire"
    fi
    printf '%s' "$corps" > "$D/commentaire-$n-$k.json"
    printf '{"id":1}'
    ;;

  "$prefixe"/issues/*)
    n="${chemin##*/}"
    printf '%s' "$corps" > "$D/etat-$n.json"
    printf '{"number":%s}' "$n"
    ;;

  *) refus "écriture non simulée" ;;
esac
