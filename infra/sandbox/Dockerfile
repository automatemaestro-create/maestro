# Image dédiée du mode isolé (ticket #108) — voir docs/17-isolation-execution.md.
#
# En mode isolé (MAESTRO_ISOLATION=conteneur), chaque exécution outillée lance un
# conteneur jetable depuis cette image : le CLI Claude Code — et tout ce qu'il
# lance (outils, Bash, serveurs MCP stdio, code produit) — tourne ici, pas sur
# l'hôte. Le durcissement au lancement (racine en lecture seule, tmpfs, réseau,
# privilèges retirés, plafonds) vit dans maestro/sandbox/container.py : cette
# image ne définit que le contenu (runtime + outillage minimal) et l'utilisateur
# non-root.
#
# Construction :
#   docker build -t maestro-sandbox:latest infra/sandbox
FROM node:22-bookworm-slim

# Outillage minimal des exécutions outillées : git et ripgrep (outils du CLI),
# python3 (exécuter/vérifier le code produit par les agents), procps (ps, utilisé
# par le CLI pour gérer ses sous-processus). Rien d'autre : chaque ajout élargit
# la surface offerte au code exécuté.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        procps \
        python3 \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

# Le runtime que l'Agent SDK pilote via stdio (relayé par maestro-sandbox-shim).
RUN npm install -g @anthropic-ai/claude-code

# Utilisateur non-root dédié — uid aligné sur maestro/sandbox/container.py, qui
# monte un tmpfs jetable sur ce home au lancement : l'état du CLI (session,
# caches npm/npx des serveurs MCP) ne survit jamais au conteneur.
RUN useradd --create-home --uid 10001 --shell /bin/bash agent
USER agent

# Le point de montage du workspace de la tâche (seul chemin hôte monté, en
# lecture-écriture) ; --workdir du lancement le confirme.
WORKDIR /workspace
