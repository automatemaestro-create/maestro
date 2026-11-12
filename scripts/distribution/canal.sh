#!/usr/bin/env bash
# Le canal local d'une vérification de mise à jour (#644) : le servir, puis l'arrêter.
#
# Sourçable, sans effet à l'inclusion, après `pilote.sh`, qui porte la carte et l'arrêt :
#
#   . "$RACINE/scripts/orchestrate/pilote.sh"
#   . "$RACINE/scripts/distribution/canal.sh"
#
# Le serveur joue le rôle des publications GitHub : il n'appartient pas au poste vierge, d'où
# l'interpréteur du dépôt. Sous Windows, le `python.exe` d'un venv est un LANCEUR : celui qui
# écoute est son enfant Windows, le Python du Store, que le `kill` de MSYS n'atteint pas (#1644).
# La carte de `pilote.sh` garde l'identité du lanceur (#456), et `pilote_tue` descend l'arbre
# Windows (`taskkill //T`) : c'est le même arrêt que celui de l'UI de `start.sh` (#1579).

# canal_sonde <port> : le code HTTP de `latest.yml` sur l'hôte local, `000` si rien ne répond.
canal_sonde() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$1/latest.yml"
}

# canal_servir <python> <port> <dossier> <trace> : sert <dossier> sur 127.0.0.1:<port>, et pose la
# carte du serveur sous <trace>/canal/.
#   0 = `latest.yml` y répond · 1 = il n'a pas répondu, serveur arrêté · 3 = le port répondait
#   déjà : un autre serveur l'occupe — celui d'une vérification tuée servirait l'ANCIEN canal, et
#   c'est lui qu'on lirait. Rien n'est lancé, et ce serveur-là n'est pas le nôtre : on n'y touche pas.
canal_servir() {
  local python="$1" port="$2" dossier="$3" trace="$4" pid
  [ "$(canal_sonde "$port")" = 000 ] || return 3
  mkdir -p "$trace/canal"
  "$python" -m http.server "$port" --bind 127.0.0.1 --directory "$dossier" >"$trace/canal.log" 2>&1 &
  pid=$!
  # La carte s'écrit une fois le serveur installé, jamais au lancement : sous MSYS, l'`exec` de
  # l'interpréteur natif suit le `fork` et change le winpid que la carte retient (`pilote_ecrit`).
  for _ in $(seq 1 20); do
    if [ "$(canal_sonde "$port")" = 200 ]; then
      pilote_ecrit "$trace/canal" "$pid"
      return 0
    fi
    sleep 1
  done
  pilote_ecrit "$trace/canal" "$pid"
  canal_arreter "$port" "$trace" >/dev/null 2>&1
  return 1
}

# canal_en_vol <trace> : 0 si une carte de serveur attend son arrêt.
canal_en_vol() {
  [ -e "$(pilote_fichier "$1/canal")" ]
}

# canal_arreter <port> <trace> : arrête le serveur de la carte avec sa descendance, retire la
# carte, puis CONSTATE : 0 = plus rien ne répond sur le port · 1 = il répond encore.
canal_arreter() {
  local port="$1" trace="$2"
  pilote_tue "$trace/canal"
  pilote_retire "$trace/canal"
  for _ in 1 2 3 4 5; do
    [ "$(canal_sonde "$port")" = 000 ] && return 0
    sleep 1
  done
  return 1
}
