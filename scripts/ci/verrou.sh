#!/usr/bin/env bash
# La file d'attente du filet CI local — un `local.sh` à la fois sur le poste (#745).
#
# Sourçable, et sans effet de bord à l'inclusion :
#
#   . "$RACINE/scripts/ci/verrou.sh"
#
# --- Pourquoi ce fichier existe ----------------------------------------------------------------------
# Rien n'empêchait N exécutions simultanées de `scripts/ci/local.sh` sur la même machine, et ce n'est
# pas un cas de bord : c'est le régime NOMINAL depuis #455/#626. Un run tourne à concurrence 3, chaque
# session passe le filet avant de pousser (`/ticket-finish`), et une session interactive peut en
# lancer un par-dessus. Trois filets en vol, ce sont trois `docker run … pytest -n auto` sur le même
# démon, trois `npm run build`, et une machine qu'aucun des trois n'a plus à lui.
#
# Ce que ça coûte n'est pas d'abord du temps, c'est le VERDICT. `tests/test_orchestrate.py` mesure de
# la simultanéité réelle — barrières, pics, vrais processus lancés puis tués (#292/#313) — et rougit
# sous charge sans qu'une ligne de code soit en cause. `local.sh` porte déjà le principe dans son
# en-tête : un filet qui ment est pire que pas de filet.
#
# D'où ce fichier : les filets se rangent en file, et un seul joue à la fois.
#
# --- SÉRIALISER, JAMAIS REFUSER -----------------------------------------------------------------------
# Un filet qui répondrait « un autre tourne, reviens plus tard » déplacerait le problème sur son
# appelant — et l'appelant, c'est `/ticket-finish` juste avant un push. Il ATTEND donc son tour, puis
# joue. La seule chose qu'on ne fait jamais, c'est sauter le filet.
#
# --- Où vit la file, et pourquoi là ---------------------------------------------------------------------
# Sous le RÉPERTOIRE GIT COMMUN (`git rev-parse --git-common-dir`), qui est par DÉFINITION partagé par
# le clone principal et tous ses worktrees — c'est exactement la portée qu'on veut, et c'est le seul
# repère qui l'offre sans recopier une troisième fois `depot_principal` (elle existe déjà en double,
# dans `worktree.sh` et `lib.sh`, avec le commentaire qui assume ce doublon).
#
# Deux endroits ont été écartés, chacun pour une raison qui se vérifie :
#   · `<racine>/.maestro/ci-local/` — le journal du filet, RASÉ à chaque lancement (« table rase »,
#     #234) : le prochain filet à démarrer effacerait le verrou de celui qui tourne ;
#   · `<racine>/.maestro/…` en général — un worktree a le sien, donc deux sessions ne se verraient
#     pas, ce qui est précisément la panne à corriger.
VERROU_CI_SOUS_DOSSIER="maestro-ci-file"

# La vivacité d'un processus est DÉLÉGUÉE à scripts/orchestrate/pilote.sh, seul endroit du dépôt qui
# sache écrire une carte d'identité et la relire — PID recyclé, zombie non ramassé, échelle de
# naissance qui dérive en cours de route (#456), WINPID sous MSYS. Ces pièges ont chacun coûté un
# ticket ; en réécrire ici un `kill -0` de trois lignes serait la seconde formule, et deux formules
# qui divergent se remarquent trop tard. Même délégation que `gl_pilotes_en_vol` dans `lib.sh`, qui
# appelle ce fichier depuis un autre domaine pour exactement cette raison.
VERROU_CI_ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/orchestrate/pilote.sh
. "$VERROU_CI_ICI/../orchestrate/pilote.sh"

# --- Réglages -----------------------------------------------------------------------------------------
# MAESTRO_CI_FILE=0 : aucune file, comportement d'avant #745. La sortie explicite que porte tout
# automatisme du dépôt.
VERROU_CI_ACTIF="${MAESTRO_CI_FILE:-1}"
# Le plafond d'attente. Un filet dure de deux à quinze minutes ; trois en file n'atteignent pas
# l'heure. Ce plafond ne protège donc pas d'une file normale — il protège d'un porteur qu'on n'aurait
# su ni voir vivant, ni déclarer mort, et au-delà duquel attendre n'apprendrait plus rien.
VERROU_CI_MAX="${MAESTRO_CI_FILE_ATTENTE_MAX:-3600}"
# Le pas de sondage. Rien de ce qu'on attend ne bouge plus vite qu'une seconde, et chaque tour ne
# coûte qu'un `sleep` (un fork, ~120 ms sous MSYS) plus la relecture d'une poignée de fichiers.
VERROU_CI_PAS="${MAESTRO_CI_FILE_PAS:-1}"
# L'intervalle des rappels « toujours en attente ». Une attente muette de plusieurs minutes passe
# pour un blocage — c'est déjà la raison pour laquelle la construction de l'image est annoncée dans
# `pytest-regime.sh`.
VERROU_CI_RAPPEL="${MAESTRO_CI_FILE_RAPPEL:-30}"

# --- État de l'appel (lu par l'appelant après `verrou_ci_prend`) -------------------------------------
VERROU_CI_DIR=""       # le répertoire de la file, une fois résolu
VERROU_CI_MON_RANG=""  # mon entrée dans la file, tant que j'y suis
VERROU_CI_TENU=0       # 1 = je tiens le verrou (et suis donc le seul à pouvoir le rendre)
VERROU_CI_ATTENTE=0    # secondes réellement attendues
VERROU_CI_MOTIF=""     # pourquoi il n'y a pas de verrou, quand il n'y en a pas

# verrou_ci_maintenant : l'instant, en secondes. `EPOCHSECONDS` est une variable de bash 5, donc
# gratuite ; `date` est le repli, et un fork.
verrou_ci_maintenant() {
  if [ -n "${EPOCHSECONDS:-}" ]; then printf '%s' "$EPOCHSECONDS"; else date +%s; fi
}

# verrou_ci_duree <secondes> : « 12 s », « 3 min 05 ». Même forme que `duree_lisible` de `local.sh`,
# redite ici pour que ce fichier reste sourçable seul.
verrou_ci_duree() {
  local s="$1"
  if [ "$s" -lt 60 ]; then printf '%d s' "$s"; else printf '%d min %02d s' $((s / 60)) $((s % 60)); fi
}

# verrou_ci_dir : le répertoire de la file, ou 1 si le dépôt ne se laisse pas interroger (git absent,
# clone partiel). Ne rend pas d'erreur bruyante : l'appelant en fait une abstention annoncée.
verrou_ci_dir() {
  local commun
  commun="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
  if [ -z "$commun" ]; then
    commun="$(git rev-parse --git-common-dir 2>/dev/null)" || return 1
    commun="$(cd "$commun" 2>/dev/null && pwd)" || return 1
  fi
  [ -n "$commun" ] || return 1
  printf '%s/%s' "$commun" "$VERROU_CI_SOUS_DOSSIER"
}

# --- La file ------------------------------------------------------------------------------------------
# Une entrée par candidat, nommée « <instant sur 20 chiffres>-<pid> » : le tri LEXICAL des noms est
# alors le tri chronologique, sans fork ni comparaison numérique. Le PID départage deux arrivées dans
# la même seconde — arbitrairement, mais TOTALEMENT, ce qui est tout ce qu'on demande à un ordre.
#
# La file ne porte pas l'exclusion (c'est le `mkdir` du verrou qui la porte, et lui seul est
# atomique) : elle porte l'ORDRE, et le droit de reprendre un verrou périmé. C'est ce second rôle qui
# compte le plus — sans elle, deux candidats jugeant le même verrou mort le retireraient tous les
# deux, et le second effacerait le verrou que le premier vient de prendre.
verrou_ci_mon_nom() {
  printf '%020d-%s' "$(verrou_ci_maintenant)" "$$"
}

# verrou_ci_menage : sort de la file les entrées dont le processus n'est plus là. Sans ce ménage, un
# candidat tué (Ctrl-C avant son trap, extinction du poste) resterait le plus ancien pour toujours et
# la file entière attendrait un absent.
#
# Écarter à tort un candidat VIVANT ne casse rien : il perd son rang, pas son droit — le `mkdir` du
# verrou reste l'arbitre. L'asymétrie penche donc vers le ménage, à l'inverse de `pilote_tue`.
verrou_ci_menage() {
  local d
  for d in "$VERROU_CI_DIR"/file/*/; do
    [ -d "$d" ] || continue
    d="${d%/}"
    [ "${d##*/}" = "$VERROU_CI_MON_RANG" ] && continue
    pilote_vivant "$d" || rm -rf "$d" 2>/dev/null
  done
  return 0
}

# verrou_ci_devant : combien de candidats me précèdent dans la file. 0 = c'est mon tour.
verrou_ci_devant() {
  local d n=0 nom
  for d in "$VERROU_CI_DIR"/file/*/; do
    [ -d "$d" ] || continue
    nom="${d%/}"
    nom="${nom##*/}"
    [ "$nom" = "$VERROU_CI_MON_RANG" ] && continue
    [[ "$nom" < "$VERROU_CI_MON_RANG" ]] && n=$((n + 1))
  done
  printf '%d' "$n"
}

# --- Le verrou ------------------------------------------------------------------------------------------
# `mkdir` est l'arbitre : atomique sur tous les systèmes de fichiers qui nous intéressent, et il
# échoue si le répertoire existe. `flock` n'existe pas sous MSYS ; la création exclusive est le
# mécanisme que le dépôt emploie déjà pour le rendez-vous `.limite` d'un run (#291).
verrou_ci_verrou() { printf '%s/verrou' "$VERROU_CI_DIR"; }

# verrou_ci_porteur : « <pid> <TAB> <racine> <TAB> <âge en secondes> » du filet qui tient le verrou.
# Les champs manquants sortent en « ? » / 0 : cette ligne sert à RACONTER l'attente, jamais à décider.
verrou_ci_porteur() {
  local v pid racine epoch
  v="$(verrou_ci_verrou)"
  pid="$(pilote_champ "$v" pid)" || pid=""
  epoch="$(pilote_champ "$v" epoch)" || epoch=""
  racine="$(cat "$v/racine" 2>/dev/null)" || racine=""
  case "$epoch" in '' | *[!0-9]*) epoch=0 ;; esac
  printf '%s\t%s\t%s' "${pid:-?}" "${racine:-?}" \
    "$([ "$epoch" = 0 ] && printf 0 || printf '%s' "$(($(verrou_ci_maintenant) - epoch))")"
}

# verrou_ci_tente : essaie de prendre le verrou maintenant.
#   0 pris · 1 tenu par un vivant · 2 tenu par un mort (à reprendre — l'appelant décide s'il en a le
#   droit, et il ne l'a que s'il est le premier de la file).
verrou_ci_tente() {
  local v
  v="$(verrou_ci_verrou)"
  if mkdir "$v" 2>/dev/null; then
    pilote_ecrit "$v"
    # La RACINE du répertoire de travail, pas le répertoire courant : c'est elle qui identifie le
    # filet aux yeux de celui qui attend (« lequel de mes worktrees tourne ? »), et un filet lancé
    # depuis un sous-dossier répondrait sinon autre chose que son voisin.
    printf '%s\n' "${RACINE:-$PWD}" >"$v/racine" 2>/dev/null
    VERROU_CI_TENU=1
    return 0
  fi
  pilote_vivant "$v" && return 1
  return 2
}

# verrou_ci_reprend <pid-jugé-mort> : retire un verrou périmé, à condition que ce soit BIEN celui
# qu'on a jugé. 0 = repris · 1 = non (il a changé de main entre-temps).
#
# La confirmation n'est pas une précaution de style : entre le verdict « mort » et le retrait, le
# verrou a pu être repris par quelqu'un d'autre — et retirer un verrou VIVANT ferait tourner deux
# filets, c'est-à-dire exactement ce que ce fichier existe pour empêcher. Seul le premier de la file
# arrive ici, ce qui rend la fenêtre étroite ; la relecture la referme presque entièrement. Ce qui
# reste dégrade vers deux filets simultanés — l'état d'avant #745, jamais pire.
verrou_ci_reprend() {
  local v pid_relu
  v="$(verrou_ci_verrou)"
  pid_relu="$(pilote_champ "$v" pid)" || pid_relu=""
  [ "$pid_relu" = "$1" ] || return 1
  rm -rf "$v" 2>/dev/null
  return 0
}

# --- L'API ------------------------------------------------------------------------------------------
# verrou_ci_prend : prend le verrou, en attendant son tour s'il le faut.
#   0 = pris (VERROU_CI_ATTENTE dit combien on a attendu)
#   1 = pas de verrou du tout : éteint, ou dépôt qui ne se laisse pas interroger (VERROU_CI_MOTIF)
#   2 = plafond d'attente atteint : ON JOUE QUAND MÊME, sans le verrou (VERROU_CI_MOTIF)
#
# Le cas 2 est un choix, et il se dit DEUX fois — ici, et près du verdict. L'alternative serait
# d'échouer, ce qui remplacerait un verdict peut-être faussé par PAS de verdict, juste avant un
# push : le remède serait pire que le mal.
#
# shellcheck disable=SC2034  # VERROU_CI_ATTENTE est lue par l'APPELANT, que le lint n'a pas sur sa
# ligne de commande — il l'appelle fichier par fichier (#285), donc il ne peut pas le savoir.
verrou_ci_prend() {
  local debut attendu=0 dernier_rappel=0 devant pid racine age annonce=0 code
  if [ "$VERROU_CI_ACTIF" = 0 ]; then
    VERROU_CI_MOTIF="file désactivée (MAESTRO_CI_FILE=0)"
    return 1
  fi
  VERROU_CI_DIR="$(verrou_ci_dir)" || {
    VERROU_CI_MOTIF="dépôt git illisible : aucune file possible"
    return 1
  }
  if ! mkdir -p "$VERROU_CI_DIR/file" 2>/dev/null; then
    VERROU_CI_DIR=""
    VERROU_CI_MOTIF="file non créée sous le répertoire git commun"
    return 1
  fi

  # On s'inscrit AVANT de regarder qui est là : l'inverse laisserait deux arrivants simultanés se
  # croire tous deux les plus anciens.
  VERROU_CI_MON_RANG="$(verrou_ci_mon_nom)"
  mkdir -p "$VERROU_CI_DIR/file/$VERROU_CI_MON_RANG" 2>/dev/null
  pilote_ecrit "$VERROU_CI_DIR/file/$VERROU_CI_MON_RANG"

  debut="$(verrou_ci_maintenant)"
  while :; do
    verrou_ci_menage
    devant="$(verrou_ci_devant)"
    if [ "$devant" = 0 ]; then
      verrou_ci_tente
      code=$?
      case "$code" in
        0)
          rm -rf "$VERROU_CI_DIR/file/$VERROU_CI_MON_RANG" 2>/dev/null
          VERROU_CI_MON_RANG=""
          VERROU_CI_ATTENTE="$attendu"
          if [ "$annonce" = 1 ]; then
            printf '  %s▶%s tour venu après %s\n\n' \
              "${C_G:-}" "${C_0:-}" "$(verrou_ci_duree "$attendu")"
          fi
          return 0
          ;;
        2)
          # Personne devant moi, et le porteur ne répond plus : c'est à moi de reprendre.
          IFS=$'\t' read -r pid racine age <<<"$(verrou_ci_porteur)"
          if verrou_ci_reprend "$pid"; then
            printf '  %s───%s verrou périmé (pid %s, %s) : repris\n' \
              "${C_D:-}" "${C_0:-}" "$pid" "$(verrou_ci_duree "$age")"
            continue
          fi
          ;;
      esac
    fi

    if [ "$annonce" = 0 ]; then
      IFS=$'\t' read -r pid racine age <<<"$(verrou_ci_porteur)"
      # Pas de saut de ligne d'ouverture : l'en-tête du filet vient d'en poser un, et deux lignes
      # vides feraient croire à une sortie tronquée.
      printf '  %s⏳ un filet CI tourne déjà sur ce poste%s — pid %s, depuis %s\n' \
        "${C_Y:-}" "${C_0:-}" "$pid" "$(verrou_ci_duree "$age")"
      printf '     %s\n' "$racine"
      if [ "$devant" -gt 0 ]; then
        printf "     j'attends mon tour — %d devant moi (MAESTRO_CI_FILE=0 pour jouer sans attendre)\n" "$devant"
      else
        printf "     j'attends mon tour (MAESTRO_CI_FILE=0 pour jouer sans attendre)\n"
      fi
      annonce=1
      dernier_rappel="$attendu"
    elif [ $((attendu - dernier_rappel)) -ge "$VERROU_CI_RAPPEL" ]; then
      # Rappelé, jamais muet : sans ces lignes, une attente légitime est indiscernable d'un blocage.
      printf '     … toujours en attente (%s)\n' "$(verrou_ci_duree "$attendu")"
      dernier_rappel="$attendu"
    fi

    if [ "$attendu" -ge "$VERROU_CI_MAX" ]; then
      rm -rf "$VERROU_CI_DIR/file/$VERROU_CI_MON_RANG" 2>/dev/null
      VERROU_CI_MON_RANG=""
      VERROU_CI_ATTENTE="$attendu"
      VERROU_CI_MOTIF="plafond d'attente atteint ($(verrou_ci_duree "$VERROU_CI_MAX")) — joué EN PARALLÈLE d'un autre filet"
      printf '\n  %s⚠%s %s\n' "${C_Y:-}" "${C_0:-}" "$VERROU_CI_MOTIF"
      printf '     le verdict peut être faussé par la charge — MAESTRO_CI_FILE_ATTENTE_MAX déplace ce plafond\n\n'
      return 2
    fi

    sleep "$VERROU_CI_PAS"
    attendu=$(($(verrou_ci_maintenant) - debut))
  done
}

# verrou_ci_rend : relâche ce qu'on tient. Idempotent, best-effort, appelable depuis un trap.
#
# Le verrou n'est retiré QUE s'il porte encore mon numéro : un filet dépossédé par une reprise (jugé
# mort à tort pendant qu'il travaillait) ne doit pas emporter en partant le verrou de son successeur.
verrou_ci_rend() {
  local v
  [ -n "$VERROU_CI_DIR" ] || return 0
  [ -n "$VERROU_CI_MON_RANG" ] && rm -rf "$VERROU_CI_DIR/file/$VERROU_CI_MON_RANG" 2>/dev/null
  VERROU_CI_MON_RANG=""
  if [ "$VERROU_CI_TENU" = 1 ]; then
    v="$(verrou_ci_verrou)"
    [ "$(pilote_champ "$v" pid 2>/dev/null)" = "$$" ] && rm -rf "$v" 2>/dev/null
    VERROU_CI_TENU=0
  fi
  return 0
}
