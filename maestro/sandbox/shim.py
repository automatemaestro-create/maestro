"""Shim du mode isolé — relaie le lancement du CLI vers `docker run` (ticket #108).

C'est l'exécutable que le fournisseur passe en `cli_path` à l'Agent SDK quand le
mode isolé est actif (`MAESTRO_ISOLATION=conteneur`) : le SDK croit lancer le CLI,
le shim lance à la place le conteneur durci (`maestro.sandbox.container.commande_docker`)
qui exécute `claude` avec les mêmes arguments. Le flux stdio du protocole SDK ↔ CLI
traverse tel quel (`docker run --interactive` hérite des descripteurs du shim), et
le code de sortie du CLI remonte inchangé.

Fine couche volontairement sans logique : tout le durcissement (montages, réseau,
privilèges) vit dans `maestro.sandbox.container`, le protocole d'invocation
(variables `MAESTRO_SANDBOX_*`) y est documenté. Smoke test d'invocation (comme
les autres points d'entrée CLI) : tests/test_isolation.py (#107).
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence

from maestro.config import ConfigError
from maestro.sandbox.container import commande_docker


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée de `maestro-sandbox-shim` : exécute le CLI dans le conteneur.

    `argv` (défaut : `sys.argv[1:]`) porte les arguments que l'Agent SDK destinait
    au CLI. Un protocole d'invocation absent (`MAESTRO_SANDBOX_*`) rend 2 avec
    l'explication sur stderr — le SDK la relaie dans son diagnostic d'échec.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        commande = commande_docker(os.environ, arguments)
    except ConfigError as exc:
        print(f"maestro-sandbox-shim : {exc}", file=sys.stderr)
        return 2
    return subprocess.run(commande, check=False).returncode


if __name__ == "__main__":  # pragma: no cover — invocation directe de secours
    sys.exit(main())
