#!/usr/bin/env bash
# OÙ ET COMMENT pytest s'exécute — la plomberie du régime conteneur (#372), partagée (#405).
#
# Ce fichier ne se lance pas : il se SOURCE. Il ne décide jamais QUELS tests jouer — c'est la
# question de son appelant — mais seulement où ils jouent, avec quelle image et combien de workers.
# Deux appelants, deux questions, une seule réponse :
#
#   scripts/ci/local.sh    « les suites que le diff concerne »  → un VERDICT avant de pousser
#   scripts/ci/pytest.sh   « ce que je lui passe »              → une ITÉRATION serrée
#
# POURQUOI IL EXISTE (#405). La plomberie vivait entière dans `local.sh`, joignable par son seul
# `job_pytest`, dont le périmètre est déduit du diff : rien ne permettait de viser une suite ou un
# test. Or c'est précisément là que le régime conteneur vaut le plus cher —
# `tests/test_cycle_de_vie.py` a coûté ~8 min en natif contre 21 s dans le conteneur (×18, mesuré le
# 2026-08-21). La recopier dans un second script aurait rendu DEUX plomberies à tenir d'accord, et
# c'est le moyen le plus sûr de rendre un vert sur une forme que l'autre a corrigée depuis : elle
# est donc PARTAGÉE, pas dupliquée.
#
# CE QU'IL N'EMPORTE PAS, à dessein : le journal, le résumé, les étages, le périmètre du diff. Tout
# cela est la mécanique de VERDICT de `local.sh` et n'a pas de sens pour une itération. Ce que le
# lanceur lui emprunte, ce sont les décisions qui doivent être IDENTIQUES des deux côtés — sans quoi
# « ça passe chez moi » redeviendrait une phrase qu'on peut dire depuis deux endroits.

# La racine du dépôt — ou du WORKTREE : `BASH_SOURCE` désigne la copie réellement sourcée, donc les
# tests jouent contre le code de LA BRANCHE. Un appelant qui l'a déjà calculée la garde.
if [ -z "${RACINE:-}" ]; then
  RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

case "$(uname -s 2>/dev/null)" in
  MINGW* | MSYS* | CYGWIN*) WINDOWS=1 ;;
  *) WINDOWS=0 ;;
esac

#: La raison d'un « non jouable », posée par les fonctions qui renoncent. `local.sh` l'affiche à
#: côté du statut du job, `pytest.sh` sur sa dernière ligne — la convention est la même des deux
#: côtés, seul le rendu diffère. Ne l'écrase pas si l'appelant l'a déjà déclarée.
DETAIL="${DETAIL:-}"

# --- Interpréteurs et chemins du poste ------------------------------------------------------------
# venv_bin <nom> : l'exécutable du venv du dépôt (Scripts/*.exe sous Windows, bin/* ailleurs).
# Renvoie 1 s'il n'y est pas — on ne retombe JAMAIS sur le python du système, dont les dépendances
# ne sont pas celles du projet (CLAUDE.md, « Environnement Python »).
venv_bin() {
  local exe
  if [ "$WINDOWS" = 1 ]; then exe="$RACINE/.venv/Scripts/$1.exe"; else exe="$RACINE/.venv/bin/$1"; fi
  [ -x "$exe" ] || return 1
  printf '%s\n' "$exe"
}

chemin_natif() {
  if [ "$WINDOWS" = 1 ] && command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s\n' "$1"; fi
}

# `docker image inspect` : l'image est-elle DÉJÀ là ? Sert aux deux replis par conteneur — celui du
# lint (qui ne construit rien, il se contente d'une image déjà tirée) et celui de pytest (qui la
# construit si besoin, l'image étant son régime nominal).
#
# ⚠ Ne pas commencer une ligne de commentaire par « # shellcheck » : le linter y lirait une
# DIRECTIVE et rendrait SC1073 sur un texte qui n'en est pas une (attrapé par le lint, #405).
image_docker_disponible() { docker image inspect "$1" >/dev/null 2>&1; }

# --- OÙ joue le job pytest (#372, docs/10 §8.4) ---------------------------------------------------
# Le job pytest joue DANS UN CONTENEUR LINUX, et le repli natif n'est là que pour les postes sans
# Docker. Ce n'est pas un choix de confort : les suites d'outillage sont faites à 100 % de
# sous-processus shell, donc leur durée est une fonction du prix d'un `fork` — ~800 ms sous Windows
# contre < 1 ms sous Linux, mesuré dos à dos le 2026-08-21 (voir scripts/ci/pytest.Dockerfile pour
# la table complète : ×31 sur une suite, 14 min 33 → 52,9 s sur le périmètre `scripts/**`).
#
# Le second gain n'est pas de vitesse : le filet joue enfin sur L'OS DU VERDICT. #332/#333 ont
# montré que 285 tests d'outillage n'avaient jamais tourné ailleurs que sous Windows, et que le
# premier runner Linux muni de git en avait trouvé 16 rouges d'un coup. Cette classe d'écart ne se
# voyait qu'au merge.
PYTEST_DOCKERFILE_REL="scripts/ci/pytest.Dockerfile"
PYTEST_IMAGE_NOM="${MAESTRO_PYTEST_IMAGE:-maestro-pytest}"
# Deux niveaux, JAMAIS la racine : monté à `/w`, le parent du dépôt serait `/` et
# `test_projets.py::test_depot_maestro_refuse` rendrait `racine-de-disque` au lieu de
# `au-dessus-du-depot-maestro` — un rouge qui ne dit rien du code (trouvé par les tests, #372).
PYTEST_MONTAGE=/maestro/depot
#: auto (défaut) · conteneur (exige Docker, échoue sinon) · natif (l'ancien régime).
PYTEST_REGIME_DEMANDE="${MAESTRO_PYTEST_REGIME:-auto}"
case "$PYTEST_REGIME_DEMANDE" in
  auto | conteneur | natif) ;;
  *) PYTEST_REGIME_DEMANDE=auto ;;
esac
# Les deux sondes vivent ICI, et non près de `job_pytest` : la boucle d'analyse des options appelle
# `liste_jobs`, et bash ne connaît une fonction qu'une fois la ligne qui la définit LUE. Définies
# plus bas, elles rendaient « regime_pytest_pressenti: command not found » sur `--list` — attrapé
# par les tests, pas à la relecture.
docker_repond() { docker version --format '{{.Server.Version}}' >/dev/null 2>&1; }

# --- Un démon éteint n'est pas un poste sans Docker (#425) ----------------------------------------
# Le repli natif plus bas a été écrit pour les postes SANS Docker. Un autre cas lui empruntait le
# même chemin et payait le même prix — ×15 à ×30, et un verdict aveugle aux écarts Windows/Linux que
# la CI verra (docs/10 §8.7) — alors qu'il se répare en une commande : **Docker Desktop installé,
# simplement pas démarré**. Les confondre fait du repli le régime ordinaire de qui n'a pas pensé à
# lancer Docker avant, c'est-à-dire à peu près tout le monde après un redémarrage.
#
# `docker desktop` est un plugin CLI livré AVEC Docker Desktop : sa présence est exactement ce qui
# sépare les deux situations, sans avoir à deviner un chemin d'installation ni à distinguer les
# plateformes. Absent, il n'y a rien à démarrer et le repli reste la bonne réponse — celle pour
# laquelle il a été écrit.
#: Tenter le démarrage ? 0 pour ne jamais essayer (poste où Docker ne doit pas se lancer seul).
DOCKER_DEMARRAGE="${MAESTRO_DOCKER_DEMARRAGE:-1}"
#: Le plafond laissé au démarrage, passé tel quel à `docker desktop start --timeout`. 180 s est
#: large à dessein : la mesure de référence est de 35 s (poste Windows, démon froid, 2026-08-22).
DOCKER_DEMARRAGE_DELAI="${MAESTRO_DOCKER_DEMARRAGE_DELAI:-180}"
case "$DOCKER_DEMARRAGE_DELAI" in
  '' | *[!0-9]*) DOCKER_DEMARRAGE_DELAI=180 ;;
esac

#: Pourquoi le démon n'a pas pu être joint — posée par `docker_reveille`, lue par son appelant.
DOCKER_RAISON=""

# Le plugin est-il là, et a-t-on le droit de s'en servir ? Sonde PURE : elle ne démarre rien, ce qui
# la rend jouable depuis `regime_pytest_pressenti`, dont tout le contrat est d'être gratuit.
docker_reveillable() {
  [ "$DOCKER_DEMARRAGE" != 0 ] || return 1
  docker desktop --help >/dev/null 2>&1
}

# Démarre Docker Desktop, puis attend que le MOTEUR réponde. → 0 il répond · 1 non.
docker_demarre() {
  local essais=15
  docker desktop start --timeout "$DOCKER_DEMARRAGE_DELAI" >/dev/null 2>&1 || return 1
  # `start` rend la main quand Docker Desktop est LANCÉ, ce qui ne dit pas encore que le MOTEUR
  # répond — et c'est la seule question qui nous intéresse. On la repose donc, mais l'attente
  # longue reste dans `--timeout` : ce sursis-ci ne couvre que l'écart entre les deux événements
  # (nul sur le poste de référence, où `docker version` répond dès le retour de `start`). Il ne se
  # paie que sur un poste plus lent, et la boucle sort au premier `oui` — jamais après 30 s.
  while ! docker_repond; do
    essais=$((essais - 1))
    [ "$essais" -gt 0 ] || return 1
    sleep 2
  done
  return 0
}

# Le démon ne répond pas : sait-on le réveiller ? → 0 il répond maintenant · 1 non (DOCKER_RAISON).
#
# Le démarrage est ANNONCÉ avant d'être tenté, au même titre que la construction de l'image : une
# attente muette d'une demi-minute au milieu d'un lancement passerait pour un blocage.
docker_reveille() {
  DOCKER_RAISON="démon Docker injoignable"
  docker_reveillable || return 1
  printf '    ─── démon Docker éteint : démarrage de Docker Desktop (jusqu'\''à %s s) …\n' \
    "$DOCKER_DEMARRAGE_DELAI"
  docker_demarre && return 0
  DOCKER_RAISON="démarrage de Docker Desktop en échec (plafond ${DOCKER_DEMARRAGE_DELAI} s)"
  return 1
}

#: Le régime conteneur suppose-t-il de DÉMARRER Docker Desktop d'abord ? Posé par la sonde seule,
#: lu par `local.sh` — que le lint ne voit jamais sur la même ligne de commande (#285), d'où le
#: SC2034 désactivé ici comme sur la fonction qui l'écrit.
# shellcheck disable=SC2034
PYTEST_REGIME_REVEIL=0

# Quel régime jouerait, SANS RIEN CONSTRUIRE NI RIEN DÉMARRER. Sépare la sonde (gratuite) de
# l'engagement (coûteux) : c'est ce qui permet à `--list` de dire la vérité sur la commande jouée —
# son contrat depuis #194 — sans déclencher au passage une construction d'image de plusieurs
# minutes, ni le démarrage d'un Docker que personne n'a demandé à cet instant-là.
# shellcheck disable=SC2034  # PYTEST_REGIME_REVEIL est lue par `local.sh` (lint fichier par fichier).
regime_pytest_pressenti() {
  PYTEST_REGIME_REVEIL=0
  case "$PYTEST_REGIME_DEMANDE" in
    natif) PYTEST_REGIME=natif ;;
    conteneur) PYTEST_REGIME=conteneur ;;
    *)
      if docker_repond; then
        PYTEST_REGIME=conteneur
      elif docker_reveillable; then
        # Le démon dort, mais on sait le réveiller (#425). Annoncer « natif » ici annoncerait un
        # régime que le lancement ne tiendra pas — or cette sonde ne sert qu'à dire ce qui va
        # RÉELLEMENT jouer. Constater que le plugin est là reste gratuit ; on ne démarre rien.
        PYTEST_REGIME=conteneur
        PYTEST_REGIME_REVEIL=1
      else
        PYTEST_REGIME=natif
      fi
      ;;
  esac
}

# --- Ce que cette bibliothèque REND à ses appelants -----------------------------------------------
# Ces trois variables sont la SORTIE de `choisit_regime_pytest` : écrites ici, lues LÀ-BAS
# (`local.sh` les met dans son résumé, `pytest.sh` dans sa ligne d'annonce).
#
# Qu'elles soient rendues plutôt que redéduites par chacun est le fond du partage : les deux
# appelants doivent dire la MÊME chose sur l'endroit où pytest a joué, sans quoi un vert vingt fois
# plus rapide pourrait taire qu'il a joué ailleurs.
#
# ⚠ Le linter est appelé fichier par fichier (#285) : il ne voit jamais les lecteurs et prend ces
# trois-là pour des variables mortes. D'où le SC2034 désactivé sur la fonction qui les écrit — pas
# sur le fichier, où il masquerait une vraie variable morte le jour où il y en aurait une.
#: « conteneur » ou « natif » — le régime RÉELLEMENT tenu, jamais celui qui a été demandé.
PYTEST_REGIME=""
#: Pourquoi le régime demandé n'a pas pu être tenu (vide si de rien n'est).
PYTEST_REGIME_MOTIF=""
#: L'image du conteneur, étiquetée par l'empreinte de ce dont elle est faite (vide en natif).
PYTEST_IMAGE=""

# --- Combien de workers pytest (#285, révisé #372) ------------------------------------------------
# `-n auto` demande UN WORKER PAR CŒUR LOGIQUE : 16 sur le poste de mesure. La contrainte n'est pas
# le CPU, c'est la MÉMOIRE — un worker de cette suite pèse ~130 Mo (il monte des dépôts git jetables
# et attend des processus), soit ~2 Go à seize pour ~1,8 Go de RAM libre. Le poste pagine, et le
# parallélisme se mange lui-même : mesuré sur tests/test_worktree.py, `-n auto` et `-n 8` sont à
# ÉGALITÉ (74 s), pour deux fois moins de workers, de mémoire et de processus.
#
# Le plafond est un MINIMUM avec le nombre de cœurs, jamais un forçage : sur une machine qui en a
# moins, rien ne change (4 cœurs ⇒ `-n 4`, ce que `-n auto` aurait donné). C'est ce qui permet de le
# poser sans distinguer les plateformes — le gain est mesuré sous Windows, où créer un processus
# coûte ~50 ms, mais le raisonnement mémoire, lui, n'a rien de propre à Windows. Le job CI garde
# `-n auto` : il tourne dans un conteneur dédié où la mémoire n'est pas le facteur limitant
# (1 min 53 s au lieu de ~10 min, docs/10 §8.4).
#
# ⚠ CE PLAFOND NE VAUT QUE POUR LE RÉGIME NATIF (#372). Sa raison a toujours été la mémoire du
# POSTE, jamais un fait sur pytest : dans le conteneur, la contrainte n'est pas la même et le
# plafond coûte cher. Re-mesuré le 2026-08-21 sur les six suites du périmètre `scripts/**`
# (598 tests), même machine, à la suite :
#
#              conteneur Linux        Windows/MSYS (#285, mémoire)
#   -n 4          177,2 s              6 min 58
#   -n 8           63,1 s              5 min 28
#   -n 16          56,4 s             11 min 37  ET 4 ROUGES
#   -n auto        46,1 s                  —
#
# Sous Windows, `-n 16` faisait pire que `-n 8` et rougissait quatre tests de la vue console
# (#284/#290/#325) par saturation du poste. Dans le conteneur, les mêmes 598 tests passent à tous
# les régimes et `-n auto` est le plus rapide — il n'y a donc rien à plafonner, et c'est en prime
# EXACTEMENT le drapeau que joue `.github/workflows/ci.yml`. Un écart de moins entre le filet et le
# verdict qu'il prédit.
#
# MAESTRO_PYTEST_WORKERS relève ou abaisse le plafond, pour un poste qui n'a pas ce profil-là.
PYTEST_WORKERS_PLAFOND="${MAESTRO_PYTEST_WORKERS:-8}"
case "$PYTEST_WORKERS_PLAFOND" in
  '' | 0 | *[!0-9]*) PYTEST_WORKERS_PLAFOND=8 ;;
esac

# Le nombre de cœurs, ou le plafond lui-même si personne ne sait le dire — se tromper vers le haut
# retombe sur le plafond, qui est précisément la valeur sûre.
coeurs_logiques() {
  local n
  n="$(nproc 2>/dev/null)" || n="${NUMBER_OF_PROCESSORS:-}"
  case "$n" in
    '' | 0 | *[!0-9]*) printf '%s\n' "$PYTEST_WORKERS_PLAFOND" ;;
    *) printf '%s\n' "$n" ;;
  esac
}

workers_pytest() {
  local coeurs
  # Dans le conteneur : `auto`, comme la CI. Le plafond ci-dessus répond à une question du poste
  # Windows (la mémoire) qui ne s'y pose pas — voir la table.
  if [ "${PYTEST_REGIME:-}" = conteneur ] && [ -z "${MAESTRO_PYTEST_WORKERS:-}" ]; then
    printf 'auto\n'
    return 0
  fi
  coeurs="$(coeurs_logiques)"
  if [ "$coeurs" -lt "$PYTEST_WORKERS_PLAFOND" ]; then
    printf '%s\n' "$coeurs"
  else
    printf '%s\n' "$PYTEST_WORKERS_PLAFOND"
  fi
}

# `-n <n>` sur un venv sans pytest-xdist sort en erreur d'ARGUMENTS : un rouge qui ne parle pas du
# code. Le venv d'un clone antérieur à #214 est dans ce cas tant que `setup.sh --only venv` n'a pas
# rejoué `pip install -e ".[dev]"`. Dans le conteneur la question ne se pose pas — xdist vient des
# `[dev]` de pyproject.toml, que l'image installe.
#
# Sonde SANS EFFET DE BORD (#405) : chacun en tire la conséquence qui lui va — le filet le signale
# dans son résumé (« en série »), le lanceur se contente de retomber en série.
xdist_installe() { # <python>
  ( cd "$RACINE" && "$1" -c "import xdist" ) >/dev/null 2>&1
}

# --- Le régime conteneur du job pytest (#372) -----------------------------------------------------
# Trois questions, dans cet ordre : le démon répond-il ? quelle image ? est-elle là ?
#
# Aucune n'est posée au CHARGEMENT du fichier, toutes le sont dans `job_pytest` : `docker` répond en
# ~0,4 s et le hachage coûte deux forks, ce qui serait payé par tout appel du filet — `--only lint`
# compris. C'est la leçon de `GL_ICI` dans lib.sh, sur laquelle porte l'autre moitié de ce ticket.
# L'étiquette de l'image PORTE L'EMPREINTE de ce dont elle est faite : les dépendances
# (pyproject.toml) ET la recette (pytest.Dockerfile). Une dépendance ajoutée au dépôt change
# l'étiquette, donc l'image manque, donc elle est reconstruite — personne n'a à s'en souvenir, et
# une image périmée ne peut pas rendre un vert sur des dépendances qu'elle n'a pas.
#
# `git hash-object` plutôt que `sha256sum` : git est déjà une dépendance dure de ce fichier
# (`fichiers_modifies`), là où les coreutils varient d'une plateforme à l'autre.
pytest_image() {
  local empreinte
  # Les deux fichiers doivent être LÀ, et pas seulement lisibles « au mieux » : `cat` d'un fichier
  # absent n'écrit rien, et `git hash-object --stdin` rend alors l'empreinte du vide — une étiquette
  # parfaitement stable qui ne décrit plus rien. Mieux vaut renoncer au régime, en le disant.
  [ -r "$RACINE/pyproject.toml" ] && [ -r "$RACINE/$PYTEST_DOCKERFILE_REL" ] || return 1
  empreinte="$(cat "$RACINE/pyproject.toml" "$RACINE/$PYTEST_DOCKERFILE_REL" 2>/dev/null |
    git hash-object --stdin 2>/dev/null)"
  [ -n "$empreinte" ] || return 1
  printf '%s:%s\n' "$PYTEST_IMAGE_NOM" "${empreinte:0:12}"
}

# Construit l'image si elle manque. C'est la SEULE chose que ce filet fabrique, et elle est
# annoncée : contrairement au repli docker de shellcheck — qui ne télécharge rien et se contente
# d'une image déjà là —, on ne peut pas se passer de celle-ci, elle est le régime nominal. Mais une
# construction muette de plusieurs minutes au milieu d'une « boucle courte » serait pire que lente :
# elle passerait pour un blocage.
pytest_image_construit() { # <image> → 0 prête · 1 échec
  local image="$1"
  image_docker_disponible "$image" && return 0
  printf '    ─── image %s absente : construction (une fois, puis mise en cache) …\n' "$image"
  # Le contexte est réduit au strict nécessaire : le Dockerfile ne copie que pyproject.toml, mais
  # docker enverrait sinon tout le dépôt au démon — .venv, .tools et node_modules compris.
  MSYS_NO_PATHCONV=1 docker build \
    --quiet \
    --file "$(chemin_natif "$RACINE/$PYTEST_DOCKERFILE_REL")" \
    --tag "$image" \
    "$(chemin_natif "$RACINE")" >"${JOURNAL:-/dev/stderr}" 2>&1 || return 1
  return 0
}

# Le `docker run` du job. Le dépôt est monté (jamais copié) : c'est le code de LA BRANCHE qui est
# testé, travail non commité compris — exactement ce que le régime natif teste, et ce sur quoi le
# pipeline se prononcera.
#
# ⚠ NE REDIRIGE RIEN (#405) : c'est l'appelant qui décide où va la sortie, et les deux réponses sont
# légitimes. `local.sh` l'envoie dans son journal — il rend un verdict, et n'affiche la trace qu'en
# cas d'échec, là où on en a besoin. `pytest.sh` la laisse à l'écran : sur une itération de vingt
# secondes, voir les points défiler EST l'information, et une trace d'échec qu'il faut aller
# chercher dans un fichier est une trace qu'on ne lit pas.
pytest_conteneur() { # <image> <args pytest…>
  local image="$1" identite=()
  shift
  # Sous Windows le montage n'a pas de propriétaire à respecter. Ailleurs, un conteneur qui tourne
  # en root sèmerait dans le dépôt des fichiers appartenant à root (.pytest_cache, .coverage) que
  # l'utilisateur ne pourrait plus effacer. HOME suit, sinon pytest cherche à écrire dans /root.
  if [ "$WINDOWS" = 0 ]; then
    identite=(--user "$(id -u):$(id -g)" -e HOME=/tmp)
  fi
  # GIT_CONFIG_* plutôt qu'un `git config --global` dans l'image : le dépôt monté n'appartient pas à
  # l'uid du conteneur, donc git le refuserait comme « dubious ownership » — mais c'est une
  # propriété de CET APPEL, pas de l'image, et l'image doit rester SANS identité git globale sous
  # peine de remasquer le bug de #332 (docs/10 §8.7).
  MSYS_NO_PATHCONV=1 docker run --rm \
    "${identite[@]}" \
    -v "$(chemin_natif "$RACINE"):$PYTEST_MONTAGE" \
    -w "$PYTEST_MONTAGE" \
    -e PYTHONPATH="$PYTEST_MONTAGE" \
    -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e 'GIT_CONFIG_VALUE_0=*' \
    "$image" pytest "$@"
}

# Décide OÙ le job va jouer et pose PYTEST_REGIME / PYTEST_IMAGE / PYTEST_REGIME_MOTIF.
# → 0 un régime est tenu · 2 aucun (job IGNORÉ, DETAIL posé).
#
# Le repli est le même mécanisme que celui du job shellcheck, avec une différence qui compte : il
# est ANNONCÉ dans le verdict. Un filet qui retomberait en silence sur le régime natif rendrait un
# vert de quinze minutes en se faisant passer pour un vert d'une minute — et surtout un vert qui
# n'a pas vu ce que la CI verra.
# shellcheck disable=SC2034  # PYTEST_IMAGE / PYTEST_REGIME_MOTIF sont lues par les APPELANTS.
choisit_regime_pytest() {
  local image raison=""
  PYTEST_IMAGE=""
  PYTEST_REGIME_MOTIF=""
  if [ "$PYTEST_REGIME_DEMANDE" = natif ]; then
    PYTEST_REGIME=natif
    return 0
  fi
  # `docker version` est posé ici et non dans la sonde : celle-ci répond « lequel des deux »,
  # celui-ci « et est-ce que ça marche ». En régime EXIGÉ la sonde ne demande rien, donc la
  # question doit être posée une fois, ici, pour les deux cas.
  #
  # Et s'il ne répond pas, on essaie de le réveiller AVANT de conclure (#425) : sur un poste où
  # Docker Desktop est installé, « éteint » est une cause qui se répare, pas un verdict. C'est ici
  # et non dans la sonde, parce que c'est ici qu'on s'engage — la sonde, elle, reste gratuite.
  if ! docker_repond && ! docker_reveille; then
    raison="$DOCKER_RAISON"
  elif ! image="$(pytest_image)"; then
    raison="empreinte de l'image incalculable (git ou $PYTEST_DOCKERFILE_REL manquant)"
  elif ! pytest_image_construit "$image"; then
    raison="construction de $image en échec — voir le journal"
  else
    PYTEST_IMAGE="$image"
    PYTEST_REGIME=conteneur
    return 0
  fi
  # Régime EXIGÉ : on ne retombe pas sur un régime que personne n'a demandé. C'est ce qui rend
  # MAESTRO_PYTEST_REGIME=conteneur utilisable en CI ou dans un test, où un repli silencieux ferait
  # passer « Docker manquait » pour « tout va bien ».
  if [ "$PYTEST_REGIME_DEMANDE" = conteneur ]; then
    DETAIL="régime « conteneur » exigé mais indisponible : $raison"
    return 2
  fi
  PYTEST_REGIME=natif
  PYTEST_REGIME_MOTIF="$raison"
  return 0
}

# --- Le garde-fou du régime natif (#194) ----------------------------------------------------------
# Où `import maestro` se résout POUR LE LANCEUR ET LE RÉPERTOIRE du job — la question qui décide si
# le verdict de pytest est digne de foi (#194). Le `.venv` est partagé par jonction entre le clone
# principal et ses worktrees (docs/10 §9) et y installe `maestro` en éditable POINTÉ SUR LE CLONE
# PRINCIPAL : un lanceur qui n'ajoute pas le répertoire courant à `sys.path` importe alors le code
# d'une AUTRE branche. La sonde tourne dans le même répertoire et par le même python que le job
# — c'est ce qui la rend fidèle — et compare côté Python, où les chemins n'ont pas à traverser la
# conversion MSYS.
sonde_maestro() { # <python> → « ICI » | « AILLEURS <chemin> » | « ABSENT <erreur> »
  ( cd "$RACINE" && "$1" - <<'PY' 2>/dev/null
import os

attendu = os.path.join(os.getcwd(), "maestro")
try:
    import maestro
except Exception as erreur:  # large à dessein : tout import raté rend le job non jouable
    print("ABSENT", erreur)
else:
    paquet = os.path.dirname(os.path.realpath(maestro.__file__))
    memes = os.path.normcase(paquet) == os.path.normcase(os.path.realpath(attendu))
    print("ICI" if memes else "AILLEURS " + paquet)
PY
  )
}


#: L'interpréteur du venv retenu pour le régime natif — posé par `verifie_venv_natif`.
PYTEST_PYTHON=""

# Le régime natif est-il DIGNE DE FOI sur ce poste ? Trois questions, et l'échec de l'une d'elles ne
# rend NI vert NI rouge : c'est « non jouable », le même verdict que ruff ou mypy absents.
#
# Ces contrôles n'ont de sens QUE pour le natif. Dans le conteneur il n'y a ni venv partagé par
# jonction, ni installation éditable pointant ailleurs : `import maestro` n'y a qu'une source
# possible — le dépôt MONTÉ, désigné par PYTHONPATH — ce qui rend la question de #194 sans objet
# plutôt que résolue.
#
# → 0 jouable (`PYTEST_PYTHON` posé) · 2 non jouable (`DETAIL` posé).
verifie_venv_natif() {
  local sonde
  PYTEST_PYTHON=""
  # pytest est lancé par `python -m`, mais c'est bien la présence du SCRIPT CONSOLE qui dit s'il est
  # installé dans le venv du dépôt : `python -m pytest` sur un venv sans pytest sortirait en 1, donc
  # en ÉCHEC, là où « outil absent » doit rendre IGNORÉ.
  venv_bin pytest >/dev/null || {
    DETAIL="pytest absent du venv du dépôt — bash scripts/setup.sh --only venv"
    return 2
  }
  PYTEST_PYTHON="$(venv_bin python)" || {
    DETAIL="python absent du venv du dépôt — bash scripts/setup.sh --only venv"
    return 2
  }
  # Un job qui ne teste pas le code d'ici ne rend NI vert NI rouge : les deux mentiraient. Le rouge
  # se voit (couverture 0 %) ; le vert, lui, passe inaperçu — un correctif cassé sort vert parce que
  # le code fautif n'a jamais été chargé.
  sonde="$(sonde_maestro "$PYTEST_PYTHON")"
  case "$sonde" in
    ICI) return 0 ;;
    AILLEURS*)
      DETAIL="couverture et tests non mesurables ici : « import maestro » résout vers ${sonde#AILLEURS } au lieu du dépôt courant (venv partagé, docs/10 §9)"
      ;;
    ABSENT*)
      DETAIL="le paquet maestro ne s'importe pas (${sonde#ABSENT }) — bash scripts/setup.sh --only venv"
      ;;
    *)
      DETAIL="impossible de vérifier quel paquet maestro serait testé (sonde muette) — le verdict serait sans valeur"
      ;;
  esac
  PYTEST_PYTHON=""
  return 2
}
