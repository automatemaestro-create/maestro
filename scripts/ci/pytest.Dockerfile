# Image du job `pytest` du filet CI local (#372, docs/10 §8.4).
#
# ─────────────────────────────────────────────────────────────────────────────
# POURQUOI UNE IMAGE — le filet ne joue pas ici pour être « propre », il joue ici parce que
# LE COÛT D'UN FORK DÉCIDE DE SA DURÉE.
#
# Les suites d'outillage du dépôt sont faites à 100 % de sous-processus shell : `test_orchestrate`
# à elle seule en lance plus de deux cents, sans compter les forks internes de `run.sh`, `lib.sh` et
# `awk`. Leur durée est donc une fonction quasi linéaire du prix d'un `CreateProcess` — et ce prix
# diffère de TROIS ORDRES DE GRANDEUR entre les deux plateformes. Mesuré le 2026-08-21 sur le poste
# de référence, dos à dos, même `-n 8`, aucune ligne de test modifiée :
#
#   bash -c 'exit 0'                        Windows/MSYS ~800 ms   ·  Linux < 1 ms
#   tests/test_worktree.py (97 tests)       Windows      424 s     ·  conteneur  13,5 s   (×31)
#   les 6 suites du périmètre scripts/**    Windows    14 min 33   ·  conteneur  52,9 s
#   la suite ENTIÈRE                        Windows    injouable   ·  conteneur  1 min 51
#
# Ce n'est pas propre à MSYS (`cmd //c exit` coûte 842 ms sur la même machine) : c'est Windows qui
# lance cher, et il lance d'autant plus cher que la machine approche de sa limite de validation
# mémoire. Tant que le filet jouait sous MSYS, sa durée restait indexée sur l'ÉTAT DU POSTE — un
# onglet de navigateur de plus la faisait bouger, et aucun réglage du dépôt n'y pouvait rien.
#
# LE SECOND GAIN N'EST PAS DE VITESSE, et c'est le plus important : le filet joue désormais sur
# L'OS DU VERDICT. #332/#333 ont montré que 285 tests d'outillage n'avaient JAMAIS tourné ailleurs
# que sous Windows, et que le premier runner Linux muni de git en avait trouvé 16 rouges d'un coup,
# dont un bug de production. Cette classe d'écart ne se voyait qu'au merge : le filet local ne
# pouvait structurellement pas l'attraper. Il le peut.
#
# ─────────────────────────────────────────────────────────────────────────────
# L'IMAGE PLEINE, ET PAS UN `apt-get install git` — la leçon de #333.
#
# Les suites d'outillage exigent git. L'obtenir par `apt-get install` a été essayé puis RETIRÉ :
# ça met une dépendance réseau aux miroirs Debian sur le chemin critique de chaque construction
# (le pipeline de !269 est mort dessus, « Unable to connect to deb.debian.org », avant même que
# pytest démarre). `python:3.11` — l'image PLEINE, pas `-slim` — le porte déjà.
FROM python:3.11

# ─────────────────────────────────────────────────────────────────────────────
# AUCUNE IDENTITÉ GIT GLOBALE — la seconde moitié de #333, et elle est indissociable de la première.
#
# Poser un `user.name`/`user.email` global ici remasquerait EXACTEMENT le bug que #332 a trouvé : la
# fusion de `maestro/projets/application.py` n'échouait que sur les machines sans `~/.gitconfig`,
# c'est-à-dire nulle part où quelqu'un regardait. On ne pose donc rien, et les tests qui ont besoin
# d'une identité la posent sur leur dépôt jetable, localement, comme ils le font déjà.
#
# `safe.directory` n'est pas posé ici non plus : le dépôt est MONTÉ au lancement et n'appartient pas
# à l'uid du conteneur, mais c'est une propriété de l'appel, pas de l'image — `local.sh` le passe
# par `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0`, qui n'écrit aucun fichier.

# ─────────────────────────────────────────────────────────────────────────────
# LES DÉPENDANCES, SANS LE PAQUET.
#
# Seul `pyproject.toml` est copié : c'est ce qui rend la couche `pip install` réutilisable tant que
# les dépendances ne bougent pas (`local.sh` étiquette d'ailleurs l'image sur son empreinte). Le
# paquet `maestro`, lui, arrive au lancement par le MONTAGE du dépôt — jamais par l'image, sous
# peine de tester le code d'hier.
#
# D'où la manœuvre du stub : `pip install -e ".[dev]"` a besoin d'un paquet pour résoudre la liste
# des dépendances, on lui en donne un VIDE — puis on efface ses SOURCES sans le désinstaller.
#
# Les deux moitiés comptent, et l'ordre dans lequel on les a trouvées vaut d'être dit :
#
#   · EFFACER LES SOURCES (`rm -rf /src`) laisse l'installation éditable pointer vers un répertoire
#     qui n'existe plus. `import maestro` n'a donc qu'une seule source possible — le dépôt MONTÉ,
#     désigné par PYTHONPATH —, ce qui rend la question de #194 (« quel paquet maestro est
#     testé ? ») sans objet ici plutôt que résolue : pas de venv partagé par jonction, pas
#     d'éditable pointant sur une autre branche, rien à départager.
#   · NE PAS DÉSINSTALLER, en revanche. La première version faisait `pip uninstall maestro` après
#     l'installation, et la suite entière l'a refusée : `[project.scripts]` déclare une dizaine de
#     POINTS D'ENTRÉE (`maestro-sandbox-shim`, `maestro-run`, `maestro-demo`…) que la désinstallation
#     emporte avec le paquet, et `tests/test_isolation.py` vérifie que le shim du mode isolé existe
#     bien à côté de l'interpréteur. Les garder coûte zéro ambiguïté : ce sont de fins lanceurs qui
#     importent `maestro.*`, donc le code du dépôt monté.
WORKDIR /src
COPY pyproject.toml /src/pyproject.toml
RUN mkdir -p /src/maestro \
 && : > /src/maestro/__init__.py \
 && : > /src/README.md \
 && pip install --no-cache-dir --quiet -e ".[dev]" \
 && rm -rf /src

# ─────────────────────────────────────────────────────────────────────────────
# LE DÉPÔT NE SE MONTE PAS À LA RACINE — trouvé par les tests, à ne pas défaire.
#
# Monté à `/w`, le PARENT du dépôt est `/`, et `test_projets.py::test_depot_maestro_refuse` reçoit
# `racine-de-disque` là où il attend `au-dessus-du-depot-maestro` : un rouge qui ne dit rien du code
# et tout du point de montage. Deux niveaux suffisent, et `local.sh` monte à `/maestro/depot`.
WORKDIR /maestro/depot
