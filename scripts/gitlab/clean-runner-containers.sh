#!/usr/bin/env bash
# Ménage des conteneurs de jobs CI laissés derrière lui par le GitLab Runner (#166).
#
# Contexte : l'exécuteur `docker` du runner crée DEUX conteneurs éphémères par job —
# `runner-<jeton>-project-<id>-concurrent-<n>-<hash>-predefined` (clone, cache, artefacts) et
# `…-build` (le script du job), plus un `…-svc-<n>` par service. Il les supprime en fin de job…
# sauf quand il est tué en cours de route (Docker Desktop arrêté, poste éteint, job annulé) : le
# ménage n'a jamais lieu et les conteneurs restent `Exited` indéfiniment. Constat qui a motivé ce
# script : 8 résidus (~1,5 Go) issus de deux pipelines interrompus à une semaine d'intervalle.
#
# Câblé à côté de `ensure-runner.sh` dans les skills de clôture (/ticket-finish, /mr-fix,
# donc /ticket-ship par ricochet) : le moment où l'on prépare la CI avant la MR est aussi le bon
# moment pour ramasser les restes du pipeline précédent. Contrairement à `ensure-runner.sh`, il
# n'est PAS court-circuité quand le runner partagé tient la CI — le ménage est local à la machine,
# il concerne les conteneurs de CE poste quel que soit le runner qui sert les jobs.
#
# JAMAIS `docker container prune` (ni `docker system prune`) : sur un poste de développement, ils
# détruiraient les conteneurs arrêtés des AUTRES projets (bases de données, n8n, stacks docker
# compose au repos…). La suppression se fait exclusivement par `docker rm` sur une liste filtrée
# par nom, conteneur par conteneur.
#
# TROIS GARDE-FOUS, parce qu'un conteneur `Exited` n'est pas forcément un déchet :
#   1. `état = exited` — un job en cours d'exécution est `running`, il est écarté d'office.
#   2. Job encore vivant — le conteneur `-predefined` d'un job SORT (code 0) pendant que le job
#      continue dans `-build` : le supprimer casserait l'envoi des artefacts. On regroupe donc les
#      conteneurs par job (le préfixe jusqu'au hash) et on épargne TOUT le groupe dès qu'un de ses
#      conteneurs tourne encore. C'est le garde-fou qui compte vraiment.
#   3. Ancienneté — au cas où le second conteneur du job ne serait pas encore créé (quelques
#      secondes entre les étapes), on n'efface qu'au-delà de MAESTRO_CLEAN_AGE_MIN minutes.
#
# Best-effort et idempotent : silencieux quand il n'y a rien à faire, jamais d'exception bloquante.
# Appel attendu : `bash scripts/gitlab/clean-runner-containers.sh || …`.
#
# Les VOLUMES de cache (`runner-<hash>-cache-…`, un jeu par enregistrement du runner) ne sont
# JAMAIS supprimés automatiquement : ceux de l'enregistrement courant servent à accélérer les
# jobs. Le script se contente de les signaler ; `--volumes` purge, sur demande explicite, les jeux
# des enregistrements périmés (docker refuse de lui-même un volume monté).
#
# Deux usages :
#   1. Exécuté :   bash scripts/gitlab/clean-runner-containers.sh [--check] [--volumes]
#   2. Sourcé :    . scripts/gitlab/clean-runner-containers.sh ; clean_runner_containers
#      (comme lib.sh et ensure-runner.sh, ce fichier n'impose pas son mode d'erreur quand il est
#       sourcé : `set` n'est activé que dans la branche d'exécution directe.)

# --- Configuration (surchargeable par variables d'environnement) --------------------------------
# Conteneur du démon gitlab-runner lui-même : à ne JAMAIS supprimer. Son nom ne correspond pas au
# motif des conteneurs de jobs, mais on l'exclut explicitement — la double sécurité coûte une ligne.
MAESTRO_RUNNER_CONTAINER="${MAESTRO_RUNNER_CONTAINER:-gitlab-runner}"
# Ancienneté minimale (minutes) avant qu'un conteneur terminé soit considéré comme un résidu.
MAESTRO_CLEAN_AGE_MIN="${MAESTRO_CLEAN_AGE_MIN:-10}"

# Motif d'un conteneur de job de l'exécuteur docker. Le `.+` couvre le jeton court du runner, qui
# commence lui-même par un tiret (d'où le `runner--…` observé).
MAESTRO_CLEAN_MOTIF='^runner-.+-project-[0-9]+-concurrent-[0-9]+-[0-9a-f]+'

log()  { printf '  %s\n' "$*" >&2; }
fail() { printf 'clean-runner-containers: %s\n' "$*" >&2; }

# --- Petits utilitaires -------------------------------------------------------------------------

# Démon Docker joignable ? (contrairement à ensure-runner.sh, on ne le démarre JAMAIS : le ménage
# ne vaut pas le réveil de Docker Desktop.)
docker_is_up() { command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }

# epoch_de <timestamp-RFC3339> -> secondes epoch. GNU date (Linux, Git Bash) puis BSD date (macOS).
# Renvoie non nul si aucune des deux syntaxes ne s'applique — l'appelant traite alors le conteneur
# comme « trop récent » et l'épargne : mieux vaut un résidu de plus qu'une suppression au jugé.
epoch_de() {
  local base="${1%%.*}"        # 2026-07-26T09:58:37.123456789Z -> 2026-07-26T09:58:37
  base="${base%Z}"
  [ -n "$base" ] || return 1
  date -u -d "${base}Z" +%s 2>/dev/null && return 0
  date -u -j -f '%Y-%m-%dT%H:%M:%S' "$base" +%s 2>/dev/null && return 0
  return 1
}

# format_octets <n> -> taille lisible. Arithmétique entière (bash n'a pas de flottants) : on garde
# une décimale en multipliant par 10 avant la division.
format_octets() {
  local o="${1:-0}"
  if [ "$o" -ge 1073741824 ]; then printf '%s.%s Go\n' "$((o / 1073741824))" "$(((o * 10 / 1073741824) % 10))"
  elif [ "$o" -ge 1048576 ]; then printf '%s.%s Mo\n' "$((o / 1048576))" "$(((o * 10 / 1048576) % 10))"
  elif [ "$o" -ge 1024 ]; then printf '%s Ko\n' "$((o / 1024))"
  else printf '%s o\n' "$o"; fi
}

# Clé de job d'un conteneur : son nom tronqué au hash du job (« …-concurrent-1-90c5261e »), donc
# commune à `-predefined`, `-build` et aux `-svc-<n>` d'un même job. Vide si le nom n'est pas
# celui d'un conteneur de job.
cle_de_job() { printf '%s' "$1" | grep -oE "$MAESTRO_CLEAN_MOTIF"; }

# --- Conteneurs de jobs -------------------------------------------------------------------------

# Inventaire « <nom>|<état> » des conteneurs de jobs présents sur la machine. `|` est un séparateur
# sûr : docker l'interdit dans un nom de conteneur.
conteneurs_de_jobs() {
  docker ps -a --format '{{.Names}}|{{.State}}' 2>/dev/null \
    | grep -E "$MAESTRO_CLEAN_MOTIF" \
    | grep -v "^$MAESTRO_RUNNER_CONTAINER|"
}

# Clés des jobs encore vivants : celles dont au moins un conteneur n'est pas `exited`. Ce sont les
# groupes à épargner en entier (garde-fou 2).
jobs_vivants() {
  local inventaire="$1" ligne nom
  printf '%s\n' "$inventaire" | while IFS= read -r ligne; do
    [ -n "$ligne" ] || continue
    nom="${ligne%%|*}"
    [ "${ligne##*|}" = "exited" ] && continue
    cle_de_job "$nom"
  done | sort -u
}

# clean_runner_containers [--check] -> supprime les conteneurs de jobs terminés.
# 0 = rien à faire ou ménage effectué ; non nul = Docker installé mais injoignable.
clean_runner_containers() {
  local dry=0
  case "${1:-}" in
    --check) dry=1 ;;
    "")      ;;
    *)       fail "option inconnue : $1 (attendu : --check)"; return 2 ;;
  esac

  # Docker absent = machine qui n'héberge aucun runner : il n'y a rien à nettoyer, et ce n'est pas
  # une anomalie. Docker installé mais éteint, en revanche, est signalé (code non nul) : le ménage
  # n'a pas pu se faire alors qu'il y avait peut-être matière.
  command -v docker >/dev/null 2>&1 || return 0
  if ! docker_is_up; then
    fail "démon Docker injoignable — ménage des conteneurs de jobs reporté"
    return 1
  fi

  local inventaire vivants maintenant seuil
  inventaire="$(conteneurs_de_jobs)"
  [ -n "$inventaire" ] || return 0
  vivants="$(jobs_vivants "$inventaire")"
  maintenant="$(date -u +%s)"
  seuil=$((MAESTRO_CLEAN_AGE_MIN * 60))

  local ligne nom cle infos fin taille age
  local supprimes=0 octets=0 epargnes=0
  while IFS= read -r ligne; do
    [ -n "$ligne" ] || continue
    nom="${ligne%%|*}"
    [ "${ligne##*|}" = "exited" ] || continue

    # Garde-fou 2 : un autre conteneur du même job tourne encore.
    cle="$(cle_de_job "$nom")"
    if [ -n "$cle" ] && printf '%s\n' "$vivants" | grep -qxF "$cle"; then
      epargnes=$((epargnes + 1))
      continue
    fi

    # `-s` peuple SizeRw (octets écrits dans la couche du conteneur) : de quoi chiffrer l'espace
    # récupéré sans parser le « 256MB (virtual 397MB) » de `docker ps --size`.
    infos="$(docker inspect -s -f '{{.State.FinishedAt}}|{{.SizeRw}}' "$nom" 2>/dev/null)"
    [ -n "$infos" ] || continue
    fin="${infos%%|*}"
    taille="${infos##*|}"
    case "$taille" in ''|*[!0-9]*) taille=0 ;; esac

    # Garde-fou 3 : trop récent (ou date illisible) => on épargne.
    if ! fin="$(epoch_de "$fin")"; then
      epargnes=$((epargnes + 1))
      continue
    fi
    age=$((maintenant - fin))
    if [ "$age" -lt "$seuil" ]; then
      epargnes=$((epargnes + 1))
      continue
    fi

    if [ "$dry" -eq 1 ]; then
      supprimes=$((supprimes + 1))
      octets=$((octets + taille))
      log "à supprimer : $nom ($(format_octets "$taille"))"
    elif docker rm "$nom" >/dev/null 2>&1; then
      supprimes=$((supprimes + 1))
      octets=$((octets + taille))
    fi
    # Un `docker rm` en échec (course avec le runner qui fait son propre ménage) n'est pas une
    # erreur : le conteneur a disparu, c'est le résultat voulu.
  done <<EOF
$inventaire
EOF

  if [ "$supprimes" -gt 0 ]; then
    if [ "$dry" -eq 1 ]; then
      log "ménage CI : $supprimes conteneur(s) de job à supprimer ($(format_octets "$octets"))."
    else
      log "ménage CI : $supprimes conteneur(s) de job supprimé(s) ($(format_octets "$octets") libérés)."
    fi
  fi
  [ "$epargnes" -gt 0 ] && [ "$dry" -eq 1 ] && log "$epargnes conteneur(s) épargné(s) (job en cours ou trop récent)."
  return 0
}

# --- Volumes de cache ---------------------------------------------------------------------------
# Un enregistrement du runner possède son propre jeu de volumes `runner-<hash>-cache-<clé>` (+ leur
# variante `-protected`). Rejouer setup-runner.sh en crée un nouveau jeu sans réclamer l'ancien.

volumes_cache() { docker volume ls -q 2>/dev/null | grep -E '^runner-[0-9a-f]+-cache-'; }

# Préfixes distincts = nombre d'enregistrements successifs du runner.
groupes_cache() { volumes_cache | sed -E 's/^(runner-[0-9a-f]+)-cache-.*/\1/' | sort -u; }

# Le jeu de l'enregistrement COURANT, par déduction : le plus récemment créé. Les `CreatedAt` sont
# en RFC3339, donc l'ordre lexicographique est l'ordre chronologique.
groupe_courant() {
  local groupe v ts meilleur_ts="" meilleur=""
  while IFS= read -r groupe; do
    [ -n "$groupe" ] || continue
    ts=""
    while IFS= read -r v; do
      [ -n "$v" ] || continue
      ts="$(printf '%s\n%s' "$ts" "$(docker volume inspect -f '{{.CreatedAt}}' "$v" 2>/dev/null)" | sort | tail -1)"
    done <<EOF
$(volumes_cache | grep -E "^$groupe-cache-")
EOF
    if [ -z "$meilleur_ts" ] || [ "$ts" \> "$meilleur_ts" ]; then
      meilleur_ts="$ts"
      meilleur="$groupe"
    fi
  done <<EOF
$(groupes_cache)
EOF
  printf '%s\n' "$meilleur"
}

# signale_volumes_cache : n'imprime QUE s'il y a plus d'un enregistrement, et ne supprime rien.
signale_volumes_cache() {
  local total groupes
  command -v docker >/dev/null 2>&1 || return 0
  docker_is_up || return 0
  total="$(volumes_cache | grep -c . )"
  groupes="$(groupes_cache | grep -c . )"
  [ "${groupes:-0}" -gt 1 ] || return 0
  log "$total volumes de cache sur $groupes enregistrements du runner — purge des périmés :"
  log "  bash scripts/gitlab/clean-runner-containers.sh --volumes"
}

# purge_volumes_cache : supprime les jeux des enregistrements périmés. Geste EXPLICITE, jamais
# appelé par les skills. `docker volume rm` refuse de lui-même un volume monté par un conteneur —
# c'est le filet de sécurité qui rend l'heuristique « le plus récent est le courant » acceptable.
purge_volumes_cache() {
  local courant v groupe supprimes=0
  command -v docker >/dev/null 2>&1 || return 0
  if ! docker_is_up; then
    fail "démon Docker injoignable — purge des volumes de cache impossible"
    return 1
  fi
  courant="$(groupe_courant)"
  if [ -z "$courant" ]; then
    log "aucun volume de cache de runner — rien à purger."
    return 0
  fi
  log "enregistrement courant conservé : $courant"
  while IFS= read -r v; do
    [ -n "$v" ] || continue
    groupe="$(printf '%s' "$v" | sed -E 's/^(runner-[0-9a-f]+)-cache-.*/\1/')"
    [ "$groupe" = "$courant" ] && continue
    if docker volume rm "$v" >/dev/null 2>&1; then
      supprimes=$((supprimes + 1))
    else
      log "conservé (en service ou verrouillé) : $v"
    fi
  done <<EOF
$(volumes_cache)
EOF
  log "$supprimes volume(s) de cache périmé(s) supprimé(s)."
}

# --- Exécution directe (pas quand sourcé) -------------------------------------------------------
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  set -uo pipefail
  mode=""
  purge=0
  for arg in "$@"; do
    case "$arg" in
      --check)   mode="--check" ;;
      --volumes) purge=1 ;;
      -h|--help)
        sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
        printf '\nUsage : bash scripts/gitlab/clean-runner-containers.sh [--check] [--volumes]\n'
        printf '  --check    diagnostic : liste ce qui serait supprimé, ne supprime rien\n'
        printf '  --volumes  purge aussi les volumes de cache des enregistrements périmés\n'
        exit 0 ;;
      *) fail "option inconnue : $arg (attendu : --check, --volumes, --help)"; exit 2 ;;
    esac
  done
  code=0
  clean_runner_containers ${mode:+"$mode"} || code=$?
  if [ "$purge" -eq 1 ]; then
    purge_volumes_cache || code=$?
  else
    signale_volumes_cache
  fi
  exit "$code"
fi
