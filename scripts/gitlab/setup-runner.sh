#!/usr/bin/env bash
# Volet « conteneurs + CI » de la mise en route (ticket #146, parent #144) : Docker présent et
# démarré, puis runner CI de PROJET créé et enregistré pour CETTE machine.
#
#   bash scripts/gitlab/setup-runner.sh              # monte ce qui manque
#   bash scripts/gitlab/setup-runner.sh --check      # diagnostic seul : n'installe rien, ne crée rien
#   bash scripts/gitlab/setup-runner.sh --no-install # ne pas installer Docker s'il manque
#   bash scripts/gitlab/setup-runner.sh --partage    # runner PARTAGÉ (machine toujours allumée)
#
# Appelé par scripts/setup.sh (étape `runner`), mais utilisable seul.
#
# DEUX TYPES DE RUNNER (#158) — même mécanique, deux rôles :
#   • LOCAL (défaut) : le runner de CE poste, description `maestro-<machine>`. Il s'éteint avec la
#     machine ; c'est le secours.
#   • PARTAGÉ (`--partage`) : le runner de l'équipe, description `runner-partage-<machine>`, monté
#     UNE fois sur une machine qui reste allumée. C'est lui qui permet de merger quand tous les
#     postes sont éteints. Il relève `concurrent` (≥ 2) pour servir plusieurs personnes à la fois —
#     réglage global du runner, absent des options de `gitlab-runner register`, donc appliqué dans
#     config.toml. Prérequis : Docker, un jeton `glab` portant la portée `create_runner`, et une
#     machine qui ne s'éteint pas (docs/10 §8).
# Les deux acceptent les jobs NON-TAGGÉS : n'importe lequel prend n'importe quel job, aucun `tags:`
# n'est à poser dans .gitlab-ci.yml.
#
# Deux volets, dans cet ordre — le second dépend du premier :
#
#   1. DOCKER — client installé ? démon joignable ? Ce qui manque est installé D'OFFICE via le
#      gestionnaire de paquets de la plateforme (winget / brew / apt), comme le reste du parcours
#      de mise en route (#145). Sous Windows, l'installation de Docker Desktop déclenche une
#      élévation UAC et peut exiger un redémarrage de session : c'est dit tel quel, et tant que le
#      démon ne répond pas la machine n'est PAS annoncée prête.
#
#   2. RUNNER — un runner est-il enregistré SUR CETTE MACHINE (conteneur `gitlab-runner`) ? Si oui,
#      no-op : on se contente de le remettre en ligne (ensure-runner.sh). Sinon : création côté
#      GitLab (POST /user/runners, type projet, accepte les jobs non-taggés), démarrage du
#      conteneur, enregistrement avec le jeton obtenu, puis attente du passage `online`.
#      L'id est persisté dans .claude/settings.local.json (NON versionné) pour qu'ensure-runner.sh
#      le retrouve — c'est ce qui remplace l'ancien id codé en dur, propre au poste d'origine.
#
# SECRETS — le jeton d'enregistrement (`glrt-…`) ne transite jamais par une ligne de commande (elle
# est lisible par tout processus de la machine) : il est passé au conteneur par l'ENVIRONNEMENT
# (`docker run -e CI_SERVER_TOKEN`, valeur reprise de l'environnement appelant), jamais imprimé,
# jamais journalisé, jamais écrit dans un fichier versionné. Les messages d'erreur bruts de l'API
# sont expurgés par précaution (voir `expurge`).
#
# Sortie : des lignes de progression, puis une ligne `RESULTAT|<volet>|<statut>|<détail>` par volet
# (statuts OK / DEJA / IGNORE / ECHEC) que scripts/setup.sh reprend dans son rapport.
# Code de sortie : 0 si rien n'a échoué, 1 sinon.

set -uo pipefail

ici="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RACINE="$(cd "$ici/../.." && pwd)"
# ensure-runner.sh source lui-même lib.sh, et n'exécute rien quand il est sourcé. On lui reprend
# la logique Docker (démarrage du démon, attente) et la résolution de l'id du runner, plutôt que
# de la dupliquer.
# shellcheck source=scripts/gitlab/ensure-runner.sh
. "$ici/ensure-runner.sh"

# Même dossier que setup.sh, qui délègue ici — et sous la racine pour la même raison (#234) : le
# chemin du log imprimé en cas d'échec est la seule piste, et une session autonome ne peut pas
# ouvrir un chemin absolu hors de son répertoire de travail. `.maestro/` est gitignoré.
LOG_DIR_REL=".maestro/setup"
LOG_DIR="$RACINE/$LOG_DIR_REL"

# --- Configuration (surchargeable par variables d'environnement) --------------------------------
MAESTRO_RUNNER_IMAGE="${MAESTRO_RUNNER_IMAGE:-gitlab/gitlab-runner:latest}"  # image du runner
MAESTRO_RUNNER_VOLUME="${MAESTRO_RUNNER_VOLUME:-gitlab-runner-config}"       # volume de sa config
# Image par défaut des jobs qui n'en déclarent pas. Tous les jobs de .gitlab-ci.yml posent la leur ;
# celle-ci n'est qu'un filet exigé par l'exécuteur Docker.
MAESTRO_RUNNER_JOB_IMAGE="${MAESTRO_RUNNER_JOB_IMAGE:-alpine:latest}"
# Jobs simultanés du runner partagé (clé `concurrent` de config.toml). ≥ 2 pour que deux personnes
# ne se mettent pas en file d'attente l'une derrière l'autre.
MAESTRO_RUNNER_CONCURRENT="${MAESTRO_RUNNER_CONCURRENT:-2}"

# --- Drapeaux -----------------------------------------------------------------------------------
MODE_CHECK=0
MODE_PARTAGE=0
AUTO_INSTALL="${MAESTRO_AUTO_INSTALL:-1}"

usage() {
  cat <<USAGE
Docker + runner CI de projet pour cette machine.

  bash scripts/gitlab/setup-runner.sh [options]

Options :
  --check       Diagnostic seul : n'installe rien, ne crée rien, n'écrit rien.
  --no-install  N'installe pas Docker s'il manque, contente-toi de le signaler.
  --partage     Monte le runner PARTAGÉ de l'équipe (description « runner-partage-<machine> »,
                concurrent >= $MAESTRO_RUNNER_CONCURRENT) au lieu du runner local de ce poste.
                À lancer sur une machine qui reste allumée : c'est ce runner qui permet de merger
                quand tous les postes sont éteints.
  -h, --help    Cette aide.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --check)      MODE_CHECK=1 ;;
    --no-install) AUTO_INSTALL=0 ;;
    --partage)    MODE_PARTAGE=1 ;;
    -h|--help)    usage; exit 0 ;;
    *)            printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# --- Sortie ---------------------------------------------------------------------------------------
NB_ECHECS=0

info() { printf '  %s\n' "$*"; }

# resultat <volet> <statut> <détail> — ligne reprise telle quelle par le rapport de setup.sh.
resultat() {
  printf 'RESULTAT|%s|%s|%s\n' "$1" "$2" "$3"
  if [ "$2" = ECHEC ]; then NB_ECHECS=$((NB_ECHECS + 1)); fi
  return 0
}

# Expurge tout jeton de runner d'un texte avant impression. Filet de sécurité : les chemins qui
# impriment une réponse d'API sont ceux de l'ÉCHEC (donc sans jeton), mais un jeton ne doit jamais
# pouvoir fuir par une variante d'erreur non anticipée.
expurge() { sed 's/glrt-[A-Za-z0-9_-]*/glrt-***/g'; }

# Exécute une commande longue en la journalisant ; imprime le chemin du log en cas d'échec.
journalise() {
  local nom="$1"; shift
  local log="$LOG_DIR/$nom.log"
  mkdir -p "$LOG_DIR"
  if "$@" >"$log" 2>&1; then
    return 0
  fi
  printf '    (détail : %s)\n' "$LOG_DIR_REL/$nom.log" >&2
  return 1
}

# ================================================================================================
# Volet 1 — Docker
# ================================================================================================

# Commande d'installation exacte de la plateforme — affichée quand le script n'a PAS pu installer
# lui-même (pas de gestionnaire de paquets, élévation refusée, --no-install).
commande_docker() {
  case "$(os_kind)" in
    windows) echo "winget install --id Docker.DockerDesktop --exact" ;;
    macos)   echo "brew install --cask docker" ;;
    linux)   echo "sudo apt install docker.io  (ou https://docs.docker.com/engine/install/)" ;;
    *)       echo "voir https://docs.docker.com/get-docker/" ;;
  esac
}

# installe_docker : sans poser de question. Codes de retour :
#   0 installé · 1 la commande d'installation a échoué · 2 pas de gestionnaire de paquets utilisable
# Les prompts d'ÉLÉVATION (UAC sous Windows, mot de passe sudo) viennent du système et ne peuvent
# pas être supprimés depuis un script non privilégié : refusés, l'installation échoue et c'est
# rapporté tel quel.
installe_docker() {
  case "$(os_kind)" in
    windows)
      command -v winget >/dev/null 2>&1 || return 2
      journalise install-docker \
        winget install --id Docker.DockerDesktop --exact --silent --disable-interactivity \
                       --accept-package-agreements --accept-source-agreements || return 1 ;;
    macos)
      command -v brew >/dev/null 2>&1 || return 2
      journalise install-docker brew install --cask docker || return 1 ;;
    linux)
      command -v apt-get >/dev/null 2>&1 || return 2
      # sudo -n : n'installe que si l'élévation est déjà accordée (ou si on est root) — sinon le
      # script bloquerait sur une invite de mot de passe, ce qu'il ne doit jamais faire.
      if [ "$(id -u 2>/dev/null)" = 0 ]; then
        journalise install-docker env DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io || return 1
      elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        journalise install-docker sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io || return 1
      else
        return 2
      fi ;;
    *) return 2 ;;
  esac
  return 0
}

# Rend `docker` visible dans CETTE session après installation. Sous Windows, l'installeur écrit
# dans l'environnement PERSISTANT (registre), que le shell déjà lancé ne relit jamais : on ajoute
# le dossier connu de Docker Desktop au PATH courant.
rafraichir_docker_path() {
  local bin
  if [ "$(os_kind)" = windows ] && command -v cygpath >/dev/null 2>&1; then
    bin="$(cygpath -u "${PROGRAMFILES:-C:\\Program Files}" 2>/dev/null)/Docker/Docker/resources/bin"
    if [ -d "$bin" ]; then
      case ":$PATH:" in *":$bin:"*) ;; *) PATH="$PATH:$bin"; export PATH ;; esac
    fi
  fi
  hash -r 2>/dev/null || true
}

# 0 = Docker utilisable (client + démon), non nul sinon. Le volet runner en dépend.
volet_docker() {
  local code venait_d_etre_installe=0

  if ! command -v docker >/dev/null 2>&1; then
    if [ "$MODE_CHECK" = 1 ]; then
      resultat docker IGNORE "Docker absent — serait installé (--check : rien fait)"
      return 1
    fi
    if [ "$AUTO_INSTALL" != 1 ]; then
      resultat docker ECHEC "Docker absent (--no-install) → $(commande_docker)"
      return 1
    fi
    info "installation de Docker — plusieurs minutes ; le système peut demander une élévation"
    installe_docker; code=$?
    case "$code" in
      0) venait_d_etre_installe=1 ;;
      2) resultat docker ECHEC "Docker absent, pas de gestionnaire de paquets utilisable → $(commande_docker)"
         return 1 ;;
      *) resultat docker ECHEC "installation de Docker échouée (élévation refusée ?) → $(commande_docker)"
         return 1 ;;
    esac
    rafraichir_docker_path
    if ! command -v docker >/dev/null 2>&1; then
      resultat docker ECHEC "Docker installé, mais pas encore dans le PATH — rouvrir le terminal et relancer"
      return 1
    fi
  fi

  if docker_is_up; then
    if [ "$venait_d_etre_installe" = 1 ]; then
      resultat docker OK "Docker installé et démon actif"
    else
      resultat docker DEJA "Docker : démon actif"
    fi
    return 0
  fi

  if [ "$MODE_CHECK" = 1 ]; then
    resultat docker IGNORE "démon Docker éteint — serait démarré (--check : rien fait)"
    return 1
  fi

  info "démarrage de Docker Desktop…"
  start_docker_desktop || true
  if wait_until "$MAESTRO_DOCKER_TIMEOUT" "$MAESTRO_RUNNER_POLL" docker_is_up; then
    resultat docker OK "démon Docker démarré"
    return 0
  fi
  if [ "$venait_d_etre_installe" = 1 ]; then
    resultat docker ECHEC "Docker installé mais le démon ne répond pas — une fermeture de session, voire un redémarrage, est souvent nécessaire après l'installation"
  else
    resultat docker ECHEC "démon Docker injoignable après ${MAESTRO_DOCKER_TIMEOUT}s — démarrer Docker Desktop à la main"
  fi
  return 1
}

# ================================================================================================
# Volet 2 — Runner de projet
# ================================================================================================

# Description du runner : elle porte le nom de la machine, c'est ce qui permet de retrouver LE
# runner de ce poste quand le projet en compte plusieurs (cf. runner_id_decouvert). Le préfixe
# distingue le rôle — `maestro-` pour le runner local d'un poste, `runner-partage-` pour celui de
# l'équipe : la liste des runners du projet se lit alors sans avoir à demander à qui est quoi.
description_runner() {
  if [ "$MODE_PARTAGE" = 1 ]; then
    printf 'runner-partage-%s (setup-runner.sh --partage)\n' "$(machine_nom)"
  else
    printf 'maestro-%s (setup-runner.sh)\n' "$(machine_nom)"
  fi
}

# Persiste l'id du runner dans .claude/settings.local.json (non versionné, .gitignore) : Claude Code
# injecte ce bloc `env` dans l'environnement des commandes qu'il lance, et ensure-runner.sh sait
# aussi le relire directement. Fusion clé par clé en Python (c'est du JSON — une réécriture en shell
# corromprait le fichier) ; sans interpréteur disponible, on le dit et on s'en remet à la
# redécouverte par l'API, qui reste fonctionnelle.
persiste_runner_id() {
  # Même fichier que celui relu par ensure-runner.sh (MAESTRO_LOCAL_SETTINGS) : une seule source.
  local id="$1" cible="$MAESTRO_LOCAL_SETTINGS" py=() candidat sortie
  for candidat in "$RACINE/.venv/Scripts/python.exe" "$RACINE/.venv/bin/python"; do
    if [ -x "$candidat" ]; then py=("$candidat"); break; fi
  done
  if [ "${#py[@]}" -eq 0 ]; then
    for candidat in python3 python; do
      if command -v "$candidat" >/dev/null 2>&1; then py=("$candidat"); break; fi
    done
  fi
  if [ "${#py[@]}" -eq 0 ]; then
    info "id du runner non persisté (aucun interpréteur Python) — ensure-runner.sh le redécouvrira par l'API"
    return 1
  fi

  sortie="$(PYTHONIOENCODING=utf-8 "${py[@]}" - "$cible" "$id" <<'PY' 2>&1
import json, os, sys

cible, runner_id = sys.argv[1], sys.argv[2]

reglages = {}
if os.path.exists(cible):
    try:
        with open(cible, encoding="utf-8") as f:
            reglages = json.load(f) or {}
    except (OSError, ValueError) as exc:
        print("ERREUR|%s illisible (%s) — laissé intact" % (cible, exc))
        raise SystemExit(0)

env = reglages.setdefault("env", {})
if not isinstance(env, dict):
    print("ERREUR|la clé 'env' n'est pas un objet — laissé intact")
    raise SystemExit(0)

if env.get("MAESTRO_RUNNER_ID") == runner_id:
    print("INCHANGE|id déjà persisté")
    raise SystemExit(0)

env["MAESTRO_RUNNER_ID"] = runner_id
os.makedirs(os.path.dirname(cible) or ".", exist_ok=True)
with open(cible, "w", encoding="utf-8") as f:
    json.dump(reglages, f, indent=2, ensure_ascii=False)
    f.write("\n")
print("MODIFIE|id persisté")
PY
  )"

  case "${sortie%%|*}" in
    MODIFIE)  info "id du runner (#$id) persisté dans $cible" ;;
    INCHANGE) ;;
    *)        info "id du runner non persisté : ${sortie#*|}" ; return 1 ;;
  esac
  return 0
}

# Crée le runner côté GitLab. Renseigne RUNNER_ID_CREE et exporte le jeton dans CI_SERVER_TOKEN
# pour l'enregistrement du conteneur (l'appelant l'efface ensuite). Le résultat passe par une
# variable et non par stdout : une substitution de commande s'exécute dans un SOUS-SHELL, où
# l'export du jeton mourrait aussitôt. Le jeton n'est jamais imprimé.
RUNNER_ID_CREE=""
cree_runner_gitlab() {
  local projet_id reponse id msg
  RUNNER_ID_CREE=""
  projet_id="$(gl_project_id)" || return 1
  reponse="$(glab api --method POST user/runners \
      --field "runner_type=project_type" \
      --field "project_id=$projet_id" \
      --field "description=$(description_runner)" \
      --field "run_untagged=true" \
      --field "access_level=not_protected" 2>&1)"

  id="$(printf '%s' "$reponse" | grep -o '"id":[0-9]\+' | head -1 | grep -o '[0-9]\+')"
  CI_SERVER_TOKEN="$(printf '%s' "$reponse" | grep -o '"token":"[^"]*"' | head -1 | sed 's/.*"token":"//; s/"$//')"
  export CI_SERVER_TOKEN

  if [ -z "$id" ] || [ -z "$CI_SERVER_TOKEN" ]; then
    unset CI_SERVER_TOKEN
    msg="$(printf '%s' "$reponse" | grep -o '"message":"[^"]*"' | head -1 | sed 's/.*"message":"//; s/"$//')"
    [ -n "$msg" ] || msg="$(printf '%s' "$reponse" | head -1 | expurge)"
    # Pas d'apostrophe dans le mot par défaut : bash la traite comme une ouverture de quote à
    # l'intérieur de ${…:-…}, même entre guillemets doubles.
    fail "création du runner refusée : ${msg:-reponse inattendue de GitLab}"
    return 1
  fi
  RUNNER_ID_CREE="$id"
}

# Monte le conteneur du runner. Le jeton entre par l'ENVIRONNEMENT (`-e CI_SERVER_TOKEN` sans
# valeur : Docker reprend celle de l'environnement appelant), donc jamais par argv ; c'est aussi lui
# que `docker exec` retrouvera à l'enregistrement.
demarre_conteneur() {
  # Git Bash réécrit tout argument qui ressemble à un chemin absolu (« /var/run/docker.sock »
  # deviendrait « C:/Program Files/Git/var/run/docker.sock ») : MSYS_NO_PATHCONV désactive cette
  # conversion pour cet appel. Le socket est monté parce que l'exécuteur `docker` pilote le démon
  # de l'hôte pour lancer les conteneurs de job.
  MSYS_NO_PATHCONV=1 journalise runner-run \
    docker run -d --name "$MAESTRO_RUNNER_CONTAINER" --restart always \
      -e CI_SERVER_TOKEN \
      -v "$MAESTRO_RUNNER_VOLUME:/etc/gitlab-runner" \
      -v /var/run/docker.sock:/var/run/docker.sock \
      "$MAESTRO_RUNNER_IMAGE"
}

# Enregistre le runner dans le conteneur. `--token` est repris de CI_SERVER_TOKEN, hérité de
# l'environnement du conteneur — la commande ne porte donc aucun secret.
enregistre_runner() {
  MSYS_NO_PATHCONV=1 journalise runner-register \
    docker exec "$MAESTRO_RUNNER_CONTAINER" \
      gitlab-runner register --non-interactive \
        --url "https://$(gl_host)" \
        --executor docker \
        --docker-image "$MAESTRO_RUNNER_JOB_IMAGE" \
        --description "$(description_runner)"
}

# Relève le nombre de jobs simultanés du runner à AU MOINS $MAESTRO_RUNNER_CONCURRENT. `concurrent`
# est un réglage GLOBAL du démon gitlab-runner : il ne vit que dans config.toml (aucune option de
# `gitlab-runner register` ne l'expose), d'où l'édition en place puis le redémarrage du conteneur —
# le démon relit bien son fichier tout seul, mais on ne veut pas parier sur le moment.
# Idempotent : ne touche à rien si la valeur est déjà suffisante (cas du second passage).
# MSYS_NO_PATHCONV : sans lui, Git Bash réécrit « /etc/gitlab-runner/… » en chemin Windows.
regle_concurrent() {
  local cible="$MAESTRO_RUNNER_CONCURRENT" actuel
  actuel="$(MSYS_NO_PATHCONV=1 docker exec "$MAESTRO_RUNNER_CONTAINER" \
              sed -n 's/^concurrent *= *\([0-9]\{1,\}\).*/\1/p' /etc/gitlab-runner/config.toml \
            2>/dev/null | head -1)"
  if [ -n "$actuel" ] && [ "$actuel" -ge "$cible" ] 2>/dev/null; then
    return 0
  fi

  # `1i` en repli : `concurrent` est une clé de premier niveau, elle doit précéder toute section
  # [[runners]] — l'ajouter en fin de fichier l'enfermerait dans la dernière.
  if ! MSYS_NO_PATHCONV=1 journalise runner-concurrent \
        docker exec "$MAESTRO_RUNNER_CONTAINER" sh -c \
          "f=/etc/gitlab-runner/config.toml
           if grep -q '^concurrent *=' \"\$f\"; then
             sed -i 's/^concurrent *=.*/concurrent = $cible/' \"\$f\"
           else
             sed -i '1i concurrent = $cible' \"\$f\"
           fi"; then
    return 1
  fi
  journalise runner-restart docker restart "$MAESTRO_RUNNER_CONTAINER" || return 1
  info "jobs simultanés (concurrent) portés à $cible"
}

# Description d'un runner déjà connu, lue dans l'inventaire de projet (ensure-runner.sh).
description_de_runner() {
  runners_projet 2>/dev/null | grep "^$1|" | head -1 | cut -d'|' -f3-
}

volet_runner() {
  local id role desc
  role="runner de projet"
  [ "$MODE_PARTAGE" = 1 ] && role="runner partagé"

  if ! gl_require_glab >/dev/null 2>&1; then
    resultat runner IGNORE "glab non authentifié — runner non vérifié (glab auth login)"
    return 0
  fi

  # Déjà monté sur cette machine ? Le conteneur fait foi : un runner déclaré côté GitLab peut être
  # celui d'un autre poste.
  if conteneur_existe; then
    if ! id="$(runner_id)"; then
      resultat runner ECHEC "conteneur $MAESTRO_RUNNER_CONTAINER présent, mais aucun runner de projet côté GitLab — supprimer le conteneur (docker rm -f $MAESTRO_RUNNER_CONTAINER) puis relancer"
      return 1
    fi
    desc="$(description_de_runner "$id")"
    # En mode partagé, on ne DÉTOURNE pas le runner local d'un poste : le conteneur existant reste
    # le sien. Monter les deux sur la même machine est possible, mais c'est un choix explicite
    # (conteneur + volume distincts), pas un effet de bord de cette commande.
    if [ "$MODE_PARTAGE" = 1 ] && ! printf '%s' "$desc" | grep -q '^runner-partage-'; then
      resultat runner ECHEC "le conteneur $MAESTRO_RUNNER_CONTAINER héberge le runner local #$id (« $desc ») — monter le runner partagé sur une machine dédiée, ou ici dans un conteneur à part : MAESTRO_RUNNER_CONTAINER=gitlab-runner-partage MAESTRO_RUNNER_VOLUME=gitlab-runner-partage-config bash scripts/gitlab/setup-runner.sh --partage"
      return 1
    fi
    if [ "$MODE_CHECK" = 1 ]; then
      resultat runner IGNORE "runner #$id enregistré sur cette machine — statut non forcé (--check : rien fait)"
      return 0
    fi
    persiste_runner_id "$id" || true
    [ "$MODE_PARTAGE" = 1 ] && { regle_concurrent || info "concurrent non réglé — le runner reste utilisable"; }
    # --strict : la mise en route rend compte de CETTE machine. Sans lui, ensure_runner se
    # contenterait d'un autre runner du projet déjà en ligne et le rapport serait faux (#158).
    if ensure_runner --strict; then
      resultat runner DEJA "$role #$id enregistré et en ligne"
      return 0
    fi
    resultat runner ECHEC "runner #$id enregistré mais pas en ligne (voir le message ci-dessus)"
    return 1
  fi

  if [ "$MODE_CHECK" = 1 ]; then
    resultat runner IGNORE "aucun runner sur cette machine — un $role serait créé et enregistré (--check : rien fait)"
    return 0
  fi

  if [ "$MODE_PARTAGE" = 1 ]; then
    info "runner PARTAGÉ : à monter sur une machine qui reste allumée — c'est lui qui sert la CI quand les postes sont éteints"
  fi

  info "création du $role côté GitLab…"
  if ! cree_runner_gitlab; then
    resultat runner ECHEC "création du runner impossible — le jeton glab doit porter la portée « create_runner » (glab auth login, ou un token projet dédié)"
    return 1
  fi
  id="$RUNNER_ID_CREE"

  # À partir d'ici, un échec laisse un runner ORPHELIN côté GitLab (créé, jamais joignable). Le
  # script ne le supprime pas de lui-même — supprimer un runner est une action destructive, donc une
  # décision humaine — mais il le dit, pour que la reprise ne s'en fabrique pas un second.
  info "démarrage du conteneur $MAESTRO_RUNNER_CONTAINER…"
  if ! demarre_conteneur; then
    unset CI_SERVER_TOKEN
    resultat runner ECHEC "runner #$id créé côté GitLab, mais le conteneur n'a pas démarré — supprimer ce runner (Paramètres → CI/CD → Runners) avant de relancer"
    return 1
  fi

  info "enregistrement du runner dans le conteneur…"
  if ! enregistre_runner; then
    unset CI_SERVER_TOKEN
    resultat runner ECHEC "runner #$id créé, conteneur démarré, mais l'enregistrement a échoué — supprimer le runner et le conteneur (docker rm -f $MAESTRO_RUNNER_CONTAINER) avant de relancer"
    return 1
  fi
  unset CI_SERVER_TOKEN   # le jeton vit désormais dans la config du conteneur, plus dans ce shell

  persiste_runner_id "$id" || true

  # Après l'enregistrement (config.toml existe désormais) et AVANT l'attente : le réglage redémarre
  # le conteneur, donc le passage `online` doit être constaté après lui, pas avant.
  if [ "$MODE_PARTAGE" = 1 ] && ! regle_concurrent; then
    info "concurrent non réglé — le runner reste utilisable, mais un seul job à la fois"
  fi

  info "attente du passage en ligne…"
  MAESTRO_RUNNER_ID="$id"
  if wait_until "$MAESTRO_RUNNER_TIMEOUT" "$MAESTRO_RUNNER_POLL" runner_is_online; then
    resultat runner OK "$role #$id créé, enregistré et en ligne"
    return 0
  fi
  resultat runner ECHEC "runner #$id créé et enregistré, mais toujours hors ligne après ${MAESTRO_RUNNER_TIMEOUT}s"
  return 1
}

# ================================================================================================
# Déroulé
# ================================================================================================
if [ "$MODE_PARTAGE" = 1 ]; then
  printf '\nDocker + runner CI PARTAGÉ (%s) — %s\n' "$(description_runner)" "$RACINE"
else
  printf '\nDocker + runner CI de projet — %s\n' "$RACINE"
fi
if [ "$MODE_CHECK" = 1 ]; then
  printf 'Mode --check : diagnostic seul, rien ne sera installé ni créé.\n'
fi
printf '\n'

if volet_docker; then
  volet_runner
else
  resultat runner IGNORE "Docker indisponible — runner non vérifié"
fi

[ "$NB_ECHECS" -eq 0 ] || exit 1
exit 0
