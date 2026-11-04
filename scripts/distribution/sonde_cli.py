"""Le CLI `claude` lancé par le SDK, depuis une installation, jusqu'au seuil du modèle (#641).

    <installation>\\resources\\produit\\runtime\\python\\python.exe sonde_cli.py

Joué par `scripts/distribution/verifier.sh` avec l'interpréteur **embarqué** : c'est lui, et le
SDK installé à côté de lui, qu'on éprouve. La sonde ouvre une session du SDK sans lui demander
de réponse — `connect()` lance le CLI en sous-processus et lui fait la poignée de main du
protocole (`initialize`), que le CLI rend sans joindre le modèle —, puis la referme. Elle dit
quel exécutable a été lancé, et ce que le CLI a répondu, ou pourquoi il n'a pas répondu.

Aucune conversation n'est ouverte : la sonde ne demande rien au modèle, et le profil de la
vérification n'a d'ailleurs aucun compte pour le faire.

Codes : `0` le CLI a répondu à la poignée de main · `1` il n'a pas répondu (la cause est dite).
"""

from __future__ import annotations

import asyncio
import sys

DELAI = 90.0


async def sonder() -> int:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    from maestro.providers.claude import _commande_du_cli

    commande = _commande_du_cli()
    print(f"cli du produit : {commande[0] if commande else 'aucun'}")
    # Le SDK choisit le sien, sans `cli_path` : c'est sa découverte qu'on éprouve. Sur le poste
    # de la vérification (PATH système seul, profil neuf), le seul `claude.exe` qu'il puisse
    # trouver est celui de son paquet.
    client = ClaudeSDKClient(ClaudeAgentOptions())
    try:
        await asyncio.wait_for(client.connect(), timeout=DELAI)
        infos = await client.get_server_info() or {}
    except Exception as erreur:  # noqa: BLE001 - la cause est le constat
        print(f"poignée de main : refusée — {type(erreur).__name__}: {erreur}")
        return 1
    finally:
        try:
            await asyncio.wait_for(client.disconnect(), timeout=30)
        except Exception:  # noqa: BLE001 - la fermeture ne change pas le constat
            pass
    print(f"poignée de main : rendue — {', '.join(sorted(infos)) or 'réponse vide'}")
    return 0


if __name__ == "__main__":
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(sonder()))
