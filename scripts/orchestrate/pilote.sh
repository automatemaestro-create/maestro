#!/usr/bin/env bash
# Le pilote d'un run : sa carte d'identité, sa vivacité, son arrêt (#213).
#
# Sourçable, et sans effet de bord à l'inclusion :
#
#   . "$RACINE/scripts/orchestrate/pilote.sh"
#
# --- Pourquoi ce fichier existe ---------------------------------------------------------------------
# Rien n'enregistrait le processus d'un run : `status.sh` DÉDUISAIT son activité de la fraîcheur de
# ses écritures, faute de mieux (« faute de PID… », en-tête de `etat_du_run`). Une déduction suffit à
# *dire* qu'un run semble mort ; elle ne suffit pas à en *tuer* un. Et sans ce geste, deux runs
# cohabitent : ils brûlent le même quota, se partagent un unique fichier STOP et peuvent viser le
# même ticket — une reprise, en particulier, rejoue le plan d'un run qui tourne peut-être encore.
#
# Le pilote laisse donc une carte d'identité dans son journal, et ce fichier est le seul endroit qui
# sait l'écrire, la relire et s'en servir. Deux consommateurs, d'où la mise en commun : `run.sh`
# (qui pose la sienne et tue celles des autres) et `status.sh` (qui préfère savoir plutôt que
# déduire) — deux formules qui divergeraient se remarqueraient trop tard.
#
# --- Le PID seul ne suffit pas ----------------------------------------------------------------------
# Un numéro de processus se recycle, et une carte laissée par un run tué (aucun trap ne survit à un
# SIGKILL) désignerait un jour un processus innocent — que l'on tuerait à sa place. On enregistre
# donc, à côté du numéro, de quoi RECONNAÎTRE le processus : sa DATE DE NAISSANCE
# (`/proc/<pid>/stat`, champ 22, en ticks depuis le démarrage de la machine) et son WINPID sous MSYS.
#
# ⚠ La naissance ne peut pas porter seule cette décision, et c'est #456 : elle est exprimée dans une
# échelle — « ticks depuis le boot » — et cette ÉCHELLE PEUT SE DÉCALER PENDANT UN RUN. Mesuré le
# 2026-08-24 sur le run `20260824-094229` : carte à 834570974, relecture à 834568417 pour le même
# processus jamais redémarré, soit 2,557 s d'écart (CLK_TCK=1000), stable sur deux heures. Le pilote
# travaillait, `kill -0` répondait, son winpid concordait, son état était `R` — et il était déclaré
# MORT. Deux mesures encadrent le fait : la carte ne diverge PAS à l'écriture (banc `pilote_ecrit`
# puis relecture immédiate : identiques), et la naissance relue est cohérente avec `/proc/uptime`
# APRÈS coup. C'est donc l'échelle qui a bougé, pas la carte qui serait née fausse.
#
# D'où la règle actuelle : plusieurs TÉMOINS, et il suffit qu'UN SEUL concorde.
#
#   * un témoin comparable qui concorde (naissance, ou winpid) → c'est bien lui, il est vivant ;
#   * aucun témoin qui concorde, mais au moins un qui diverge → le PID est celui d'un autre, mort ;
#   * un témoin enregistré mais devenu illisible, et aucun autre → on s'abstient (mort), plutôt que
#     de tuer au jugé ;
#   * rien de jamais enregistré (plateforme sans /proc) → `kill -0` fait foi, seule chose disponible.
#
# Un témoin de plus ne relâche pas la protection contre le recyclage, il la renforce : pour tromper
# la règle il faudrait qu'un processus recycle À LA FOIS le numéro MSYS et le winpid de l'ancien.
#
# ⚠ Et l'asymétrie « dans le doute, ne pas tuer » ne vaut PAS ici, contrairement à ce que ce fichier
# a longtemps dit. Elle est juste pour `pilote_tue`, qui vise un processus ; elle est fausse pour
# `pilote_vivant`, que `pilotes_vivants` interroge pour savoir QUI TUER : un pilote déclaré mort à
# tort n'est pas tué, donc deux runs cohabitent — ce que #213 existe précisément pour empêcher — et
# le prétendu « doublon signalé » ne l'est même pas, l'arrêt étant muet quand il n'a rien à tuer.
# La protection contre le recyclage ne vient donc pas de la prudence du verdict, elle vient du
# NOMBRE DE TÉMOINS.
#
# --- Windows : deux mondes de processus ---------------------------------------------------------------
# Sous Git Bash, le pilote est un `bash.exe` MSYS et la session un `claude.exe` natif. `/proc` ne
# liste que les processus MSYS, et `kill` ne les atteint qu'eux : tuer le seul PID MSYS laisserait la
# session Claude vivante, rattachée à personne, en train de consommer du quota. D'où le WINPID
# enregistré à côté et le `taskkill //T //F`, qui descend l'arbre côté Windows.

# --- La carte d'identité ------------------------------------------------------------------------------
# Fichier `<run-dir>/pid`, en clé=valeur d'une ligne chacune (lisible à l'œil, relisible sans outil).

# pilote_fichier <run-dir> : le chemin de la carte. Un seul endroit le décide.
pilote_fichier() { printf '%s/pid' "$1"; }

# pilote_champ <run-dir> <clé> : la valeur, ou rien. Lecture ligne à ligne, sans fork.
pilote_champ() {
  local f ligne
  f="$(pilote_fichier "$1")"
  [ -r "$f" ] || return 1
  while IFS= read -r ligne || [ -n "$ligne" ]; do
    case "$ligne" in
      "$2="*) printf '%s' "${ligne#*=}"; return 0 ;;
    esac
  done <"$f"
  return 1
}

pilote_windows() {
  case "$(uname -s 2>/dev/null)" in
    MINGW* | MSYS* | CYGWIN*) return 0 ;;
    *) return 1 ;;
  esac
}

# pilote_naissance <pid> : l'instant de démarrage du processus, en ticks (champ 22 de
# `/proc/<pid>/stat`). Rien si la lecture échoue — c'est un cas normal, pas une erreur.
#
# Le nom de la commande est entre parenthèses et peut contenir des espaces (« (Isolated Web Co) ») :
# on découpe donc APRÈS la dernière parenthèse fermante, jamais en comptant les champs depuis le
# début. Le reste commence au champ 3, la naissance y est donc le 20e.
# ⚠ La redirection est enveloppée dans un groupe, et le `2>/dev/null` porte sur LUI (#745). Une
# redirection d'entrée qui échoue est signalée par le SHELL, pas par la commande : le `2>/dev/null`
# accroché au seul `read` ne l'attrape pas, et « /proc/1234/stat: No such file or directory » sortait
# sur le stderr de l'appelant. Sans conséquence tant que ces fonctions ne servaient qu'à des
# processus vivants ; visible dès qu'on interroge un processus qui vient de disparaître — le cas
# NOMINAL de la file de `scripts/ci/verrou.sh`. Le contrat des trois lectures ci-dessous dit déjà
# « rien si la lecture échoue, c'est un cas normal » : le message le contredisait.
pilote_naissance() {
  local s n
  local -a champs
  { read -r s <"/proc/$1/stat"; } 2>/dev/null || return 1
  s="${s##*) }"
  # Découpage sans fork : le reste commence au champ 3, la naissance y est donc la 20e.
  read -r -a champs <<<"$s"
  n="${champs[19]:-}"
  case "$n" in '' | *[!0-9]*) return 1 ;; esac
  printf '%s' "$n"
}

# pilote_zombie <pid> : 0 si le processus est un zombie — déjà mort, mais pas encore ramassé par son
# parent. Un zombie ne tourne pas, ne consomme rien et ne touchera plus jamais à un ticket : c'est
# un mort, et le seul qui trompe les deux témoins d'en dessous. `kill -0` lui répond encore (son
# entrée de table subsiste jusqu'au `wait` du parent) et sa date de naissance ne bouge évidemment
# plus, donc sans ce test un run tué passerait pour vivant tant que personne ne ramasse le corps —
# ce qui n'arrive jamais quand le parent est occupé ailleurs ou parti sans attendre.
#
# L'état est le champ 3 de `/proc/<pid>/stat`, donc le premier après la parenthèse fermante du nom
# (même découpage que `pilote_naissance`, et pour la même raison). Illisible ou absent — plateforme
# sans /proc, Windows en particulier, où l'état n'existe pas : 1, et la suite tranche comme avant.
pilote_zombie() {
  local s
  # Groupe + redirection : voir `pilote_naissance` (#745).
  { read -r s <"/proc/$1/stat"; } 2>/dev/null || return 1
  s="${s##*) }"
  case "$s" in 'Z' | 'Z '*) return 0 ;; *) return 1 ;; esac
}

# pilote_winpid <pid> : le PID Windows du processus MSYS, exposé par le /proc de MSYS. Rien
# ailleurs — et c'est très bien : hors Windows, personne n'en a l'usage.
pilote_winpid() {
  local w
  # Groupe + redirection : voir `pilote_naissance` (#745).
  { read -r w <"/proc/$1/winpid"; } 2>/dev/null || return 1
  printf '%s' "$w"
}

# pilote_ecrit <run-dir> : pose la carte du processus COURANT. Appelé une fois, par le run lui-même
# — jamais par le lanceur détaché, qui rend la main aussitôt et dont la carte serait périmée-née.
pilote_ecrit() {
  local dir="$1"
  [ -d "$dir" ] || return 1
  {
    printf 'pid=%s\n' "$$"
    printf 'winpid=%s\n' "$(pilote_winpid "$$" || true)"
    printf 'naissance=%s\n' "$(pilote_naissance "$$" || true)"
    printf 'epoch=%s\n' "$(date +%s)"
    printf 'hote=%s\n' "$(hostname 2>/dev/null || true)"
  } >"$(pilote_fichier "$dir")" 2>/dev/null || return 1
  return 0
}

# pilote_retire <run-dir> : retire la carte. Best-effort de bout en bout — un run qui n'arrive pas à
# faire le ménage ne doit pas échouer pour ça, la vérification d'identité rattrapant les cartes
# périmées.
pilote_retire() {
  rm -f "$(pilote_fichier "$1")" 2>/dev/null
  return 0
}

# pilote_identite_concorde <naissance-carte> <naissance-lue> <winpid-carte> <winpid-lu>
#   0 = c'est bien le processus de la carte · 1 = un autre (PID recyclé), ou invérifiable.
#
# Fonction PURE : elle ne lit ni /proc, ni la carte, ni l'horloge. C'est délibéré et c'est la moitié
# du ticket #456 — la règle qu'elle porte doit pouvoir être éprouvée cas par cas sur N'IMPORTE QUELLE
# plateforme. Le winpid n'existe que sous MSYS ; une règle qui ne vivrait qu'à l'intérieur de
# `pilote_vivant` ne serait donc testable que sous Windows, et le job Linux de la CI rendrait un vert
# sur une question jamais posée (#333). Ici, les quatre témoins sont des arguments : le test les pose.
#
# « Comparable » veut dire : les deux côtés portent une valeur. Un témoin absent des deux côtés n'a
# pas d'avis ; un témoin enregistré mais devenu illisible en a un, et c'est l'abstention.
pilote_identite_concorde() {
  local n_carte="${1:-}" n_lue="${2:-}" w_carte="${3:-}" w_lu="${4:-}"
  local comparable=0 perdu=0

  # Un seul témoin concordant suffit — voir « Le PID seul ne suffit pas » en tête de fichier.
  if [ -n "$n_carte" ] && [ -n "$n_lue" ]; then
    comparable=1
    [ "$n_carte" = "$n_lue" ] && return 0
  elif [ -n "$n_carte" ]; then
    perdu=1
  fi
  if [ -n "$w_carte" ] && [ -n "$w_lu" ]; then
    comparable=1
    [ "$w_carte" = "$w_lu" ] && return 0
  elif [ -n "$w_carte" ]; then
    perdu=1
  fi

  # Au moins un témoin comparable, et aucun n'a concordé : le numéro désigne quelqu'un d'autre.
  [ "$comparable" = 1 ] && return 1
  # Aucun témoin comparable. Un témoin enregistré qu'on ne sait plus relire fait s'abstenir ; rien
  # d'enregistré du tout laisse `kill -0` faire foi, seule chose disponible sur une plateforme
  # sans /proc.
  [ "$perdu" = 1 ] && return 1
  return 0
}

# pilote_vivant <run-dir> : 0 si le processus décrit par la carte tourne ENCORE ET est bien celui
# qu'on croit. 1 sinon — carte absente, processus disparu, zombie pas encore ramassé, PID recyclé,
# ou identité invérifiable (cf. les témoins en tête de fichier).
pilote_vivant() {
  local dir="$1" pid hote naissance actuelle winpid winpid_lu
  pid="$(pilote_champ "$dir" pid)" || return 1
  case "$pid" in '' | *[!0-9]*) return 1 ;; esac

  # Une carte écrite sur une autre machine ne désigne rien d'atteignable ici, et son numéro
  # correspond très bien à un processus local sans rapport.
  hote="$(pilote_champ "$dir" hote)" || hote=""
  if [ -n "$hote" ] && [ "$hote" != "$(hostname 2>/dev/null || true)" ]; then return 1; fi

  kill -0 "$pid" 2>/dev/null || return 1
  # `kill -0` répond oui à un cadavre non ramassé, et la naissance d'un zombie est celle qu'on a
  # enregistrée : les deux témoins ci-dessus le déclareraient vivant.
  pilote_zombie "$pid" && return 1

  naissance="$(pilote_champ "$dir" naissance)" || naissance=""
  actuelle="$(pilote_naissance "$pid")" || actuelle=""
  winpid="$(pilote_champ "$dir" winpid)" || winpid=""
  winpid_lu="$(pilote_winpid "$pid")" || winpid_lu=""
  pilote_identite_concorde "$naissance" "$actuelle" "$winpid" "$winpid_lu"
}

# --- L'arrêt --------------------------------------------------------------------------------------

# pilote_table : « <pid> <ppid> » pour tout ce qui est visible. En pur bash sur /proc (des centaines
# de processus × un fork chacun mettraient plusieurs secondes sous Git Bash, où fork coûte cher),
# avec `ps` en repli pour les systèmes sans /proc.
pilote_table() {
  local d p s
  if [ -d /proc ]; then
    for d in /proc/[0-9]*; do
      p="${d##*/}"
      read -r s <"$d/stat" 2>/dev/null || continue
      s="${s##*) }"    # « <état> <ppid> … »
      s="${s#* }"      # « <ppid> … »
      printf '%s %s\n' "$p" "${s%% *}"
    done
    return 0
  fi
  ps -e -o pid=,ppid= 2>/dev/null && return 0
  ps -e 2>/dev/null | awk '$1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ { print $1, $2 }'
}

# pilote_descendants <pid> : sa descendance, les plus profonds d'abord. Tuer le parent seul
# laisserait ses enfants orphelins mais bien vivants — c'est justement la session Claude.
pilote_descendants() {
  local niveau="$1" table suivant trouve="" pid ppid
  table="$(pilote_table)"
  while [ -n "$niveau" ]; do
    suivant=""
    while read -r pid ppid; do
      case " $niveau " in
        *" $ppid "*) suivant="$suivant $pid" ;;
      esac
    done <<<"$table"
    [ -n "$suivant" ] || break
    # Les enfants passent DEVANT les parents déjà trouvés : la liste finale va du plus profond au
    # plus superficiel.
    trouve="$suivant $trouve"
    niveau="$suivant"
  done
  printf '%s' "${trouve# }"
}

# pilote_tue <run-dir> : arrête le run décrit par la carte, avec sa descendance.
#   0 = tué (ou déjà parti dans la foulée)   1 = rien à tuer   2 = toujours vivant après insistance
#
# N'imprime rien : c'est l'appelant qui raconte, lui seul sachant dans quel contexte il le fait.
pilote_tue() {
  local dir="$1" pid winpid cibles c w
  pilote_vivant "$dir" || return 1
  pid="$(pilote_champ "$dir" pid)" || return 1
  winpid="$(pilote_champ "$dir" winpid)" || winpid=""
  # La liste est prise MAINTENANT, tant que tout le monde est encore là, et elle va du plus profond
  # au plus superficiel : c'est dans cet ordre qu'on tue, tuer un parent avant ses enfants étant
  # exactement ce qui fabrique l'orphelin qu'on cherche à éviter.
  cibles="$(pilote_descendants "$pid") $pid"

  # Côté Windows d'abord : `//T` descend l'arbre natif (le `claude.exe` de la session, invisible
  # de /proc), `//F` ne demande pas la permission à un processus qui n'a pas de fenêtre pour
  # répondre. Les doubles barres ne sont pas une coquette : MSYS convertirait « /T » en chemin.
  #
  # Un `//T` sur le SEUL pilote ne suffit plus à N (#291). Il construit l'arbre à partir des liens
  # parent→enfant que Windows connaît à cet instant, or les N sessions d'un run concurrent pendent
  # chacune d'un sous-shell : il suffit qu'un maillon intermédiaire soit déjà sorti — une session qui
  # rend la main pendant qu'on tue — pour que son `claude.exe` ne soit plus rattaché à rien
  # d'atteignable depuis le pilote, et il survivrait à l'arrêt en continuant de brûler du quota. On
  # vise donc CHAQUE cible par son propre WINPID plutôt que de faire confiance à un seul arbre : le
  # coût est un `taskkill` par sous-shell, et il est payé une fois, au moment d'arrêter un run.
  if pilote_windows; then
    for c in $cibles; do
      w="$(pilote_winpid "$c" 2>/dev/null)" || continue
      [ -n "$w" ] && taskkill //T //F //PID "$w" >/dev/null 2>&1
    done
    # Le WINPID de la CARTE en dernier : enregistré au démarrage, il reste lisible quand le /proc du
    # pilote a déjà disparu — le seul cas où la boucle ci-dessus n'aurait rien trouvé à viser.
    [ -n "$winpid" ] && taskkill //T //F //PID "$winpid" >/dev/null 2>&1
  fi

  for c in $cibles; do kill -TERM "$c" 2>/dev/null; done
  for _ in 1 2 3; do
    pilote_vivant "$dir" || return 0
    sleep 1
  done
  for c in $cibles; do kill -KILL "$c" 2>/dev/null; done
  sleep 1
  pilote_vivant "$dir" && return 2
  return 0
}

# pilotes_vivants <orch-dir> [<run-id à ignorer>] : une ligne TSV par run encore en vol —
# « run-id <TAB> pid <TAB> tickets-en-vol ». Sortie vide = personne ne tourne, et c'est le cas
# courant : le silence est donc la réponse normale, pas une anomalie à signaler.
pilotes_vivants() {
  local orch="$1" exclu="${2:-}" d id
  [ -d "$orch" ] || return 0
  for d in "$orch"/*/; do
    [ -d "$d" ] || continue
    id="$(basename "$d")"
    [ "$id" != "$exclu" ] || continue
    pilote_vivant "${d%/}" || continue
    printf '%s\t%s\t%s\n' "$id" "$(pilote_champ "${d%/}" pid)" "$(pilote_tickets_en_vol "${d%/}")"
  done
}

# pilote_tickets_en_vol <run-dir> : les iid des tickets pris en main mais sans verdict, séparés par
# des virgules. Même témoin que `status.sh` (`<iid>.session` sans ligne de bilan), redit ici pour que
# la liste des runs à tuer puisse nommer ce qu'elle interrompt sans dépendre de `status.sh`.
#
# TOUS, et non le premier (#291) : un run concurrent en a N en main, et n'en nommer qu'un ferait
# annoncer l'interruption d'un seul ticket là où N worktrees vont garder du travail non commité —
# précisément ce que le rapport d'arrêt existe pour dire. Une seule colonne TSV quand même : la
# virgule évite d'inventer un format à N champs pour une phrase qui se lit à l'œil.
pilote_tickets_en_vol() {
  local dir="$1" iid liste=""
  [ -f "$dir/plan.tsv" ] || return 0
  while IFS=$'\t' read -r _ iid _ _ _ _; do
    [ -n "${iid:-}" ] || continue
    [ -e "$dir/$iid.session" ] || continue
    awk -F'\t' -v iid="$iid" '$1 !~ /^#/ && $1 == iid { trouve = 1 } END { exit !trouve }' \
      "$dir/resume.tsv" 2>/dev/null && continue
    liste="${liste:+$liste,}$iid"
  done < <(grep -v '^#' "$dir/plan.tsv" 2>/dev/null)
  printf '%s' "$liste"
  return 0
}
