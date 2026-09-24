"""Le plafond de lecture du flux fournisseur (#1277) — éprouvé sur le vrai transport du SDK.

Le run `3fe501fc0878` est mort sur une capture d'écran : la tâche `maquette-sections`
relisait son rendu avec `Read`, et la ligne de flux correspondante (1 186 283
octets) dépassait le plafond de lecture par défaut de l'Agent SDK (1 Mio). Le moteur
l'a pris pour un aléa, relancé deux fois à l'identique, et le récit de fin l'a
présenté comme « transitoire ».

Ces tests ne remplacent pas le SDK par un double : ils le font parler à un **faux
CLI** — un script Python qui tient juste assez du protocole `stream-json` pour que
le SDK l'accepte (réponse à `initialize`, un message utilisateur en entrée, un
résultat en sortie) et qui rejoue la ligne de l'incident : un résultat de `Read`
dont l'image voyage deux fois, en bloc `image` et en `tool_use_result`. Ce qui est
éprouvé est donc la chaîne réelle — options du fournisseur, transport du SDK, son
découpage des lignes, son plafond — et non ce que nous croyons en savoir.

Deux coutures, et seulement deux, dans le transport : la commande lancée (le faux
CLI à la place de `claude`) et la recherche du binaire (inutile ici). Elles sont
posées sur des méthodes privées du SDK à dessein : si le SDK les renomme, ces tests
tombent bruyamment au `monkeypatch`, plutôt que de passer à côté du vrai chemin.
"""

import asyncio
import sys
import textwrap
from pathlib import Path

import pytest
from claude_agent_sdk import CLIJSONDecodeError
from claude_agent_sdk._internal.transport import subprocess_cli

from maestro.engine.retry import est_transitoire
from maestro.providers import ClaudeProvider, Credentials, ImageJointe, PlafondFluxDepasse
from maestro.providers import claude as claude_mod

#: Le défaut du SDK, que l'incident a franchi — recopié pour le nommer, pas pour s'y fier.
DEFAUT_SDK = 1024 * 1024

#: La taille brute de « l'image » que le faux CLI relaie : en base64 elle fait
#: 600 000 octets, et deux fois dans la même ligne, ~1,2 Mo — la ligne de l'incident.
IMAGE_OCTETS = 450_000

FAUX_CLI = textwrap.dedent(
    '''
    """Faux CLI Claude Code : le strict nécessaire du protocole stream-json (#1277)."""
    import base64
    import json
    import os
    import sys

    image = base64.b64encode(os.urandom(int(os.environ["MAESTRO_FAUX_CLI_IMAGE_OCTETS"])))
    image = image.decode("ascii")
    session = "faux-cli"


    def ecrire(message):
        ligne = json.dumps(message).encode("utf-8") + b"\\n"
        sys.stdout.buffer.write(ligne)
        sys.stdout.buffer.flush()
        return len(ligne)


    def assistant(*blocs):
        return {
            "type": "assistant",
            "message": {"role": "assistant", "model": "claude-opus-5", "content": list(blocs)},
            "parent_tool_use_id": None,
            "session_id": session,
        }


    for brute in sys.stdin:
        message = json.loads(brute)
        if message.get("type") == "control_request":
            ecrire({
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": message["request_id"],
                    "response": {},
                },
            })
        elif message.get("type") == "user":
            ecrire(assistant({
                "type": "tool_use",
                "id": "toolu_capture",
                "name": "Read",
                "input": {"file_path": ".maestro/maquette-sections/desktop-1440.png"},
            }))
            taille = ecrire({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": "toolu_capture",
                        "content": [{
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": image},
                        }],
                    }],
                },
                "parent_tool_use_id": None,
                "session_id": session,
                "tool_use_result": {
                    "type": "image",
                    "file": {"base64": image, "type": "image/png"},
                },
            })
            with open(os.environ["MAESTRO_FAUX_CLI_RELEVE"], "w", encoding="utf-8") as releve:
                releve.write(str(taille))
            ecrire(assistant({"type": "text", "text": "Rendu vérifié."}))
            ecrire({
                "type": "result",
                "subtype": "success",
                "duration_ms": 1,
                "duration_api_ms": 1,
                "is_error": False,
                "num_turns": 2,
                "session_id": session,
                "result": "Rendu vérifié.",
            })
    '''
)


@pytest.fixture
def faux_cli(monkeypatch, tmp_path) -> Path:
    """Branche le vrai transport du SDK sur le faux CLI ; rend le fichier où il relève sa ligne."""
    script = tmp_path / "faux_claude.py"
    script.write_text(FAUX_CLI, encoding="utf-8")
    releve = tmp_path / "ligne.txt"
    construire = subprocess_cli.SubprocessCLITransport._build_command

    def commande(self):
        # Les arguments restent ceux que le SDK a construits à partir de nos
        # options ; seul l'exécutable change.
        return [sys.executable, str(script), *construire(self)[1:]]

    monkeypatch.setattr(subprocess_cli.SubprocessCLITransport, "_build_command", commande)
    monkeypatch.setattr(
        subprocess_cli.SubprocessCLITransport, "_find_cli", lambda self: sys.executable
    )
    monkeypatch.setenv("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")
    monkeypatch.setenv("MAESTRO_FAUX_CLI_IMAGE_OCTETS", str(IMAGE_OCTETS))
    monkeypatch.setenv("MAESTRO_FAUX_CLI_RELEVE", str(releve))
    return releve


async def _flux(provider: ClaudeProvider) -> str:
    return "".join([m async for m in provider.generate_stream("Vérifie", model="claude-opus-5")])


#: Les quatre sessions que le fournisseur ouvre sans serveur MCP : le plafond est
#: réglé sur chacune, et chacune est éprouvée — une règle qui ne vaudrait que pour
#: `run_agent` laisserait le prochain chemin qui montre une image sur le défaut.
SESSIONS = {
    "run_agent": lambda p, ws: p.run_agent(
        "Vérifie le rendu de la maquette.",
        model="claude-opus-5",
        workspace=ws,
        tools=("Read",),
    ),
    "generate": lambda p, ws: p.generate("Vérifie", model="claude-opus-5"),
    "generate_stream": lambda p, ws: _flux(p),
    "generate_with_images": lambda p, ws: p.generate_with_images(
        "Vérifie",
        images=(ImageJointe(nom="capture.png", type_media="image/png", octets=b"\x89PNG"),),
        model="claude-opus-5",
    ),
}


@pytest.mark.parametrize("session", sorted(SESSIONS))
def test_une_capture_relue_de_plus_d_un_mio_passe_par_le_fournisseur(
    faux_cli, tmp_path, session
):
    # La ligne de l'incident, à l'octet près de son ordre de grandeur : une image
    # relue par `Read`, présente deux fois dans le même message du flux.
    provider = ClaudeProvider(Credentials())

    reponse = asyncio.run(SESSIONS[session](provider, tmp_path))

    assert reponse == "Rendu vérifié."
    assert int(faux_cli.read_text(encoding="utf-8")) > DEFAUT_SDK
    assert claude_mod.PLAFOND_FLUX_OCTETS > DEFAUT_SDK


def test_au_defaut_du_sdk_la_meme_ligne_est_un_depassement_type_jamais_relance(
    faux_cli, tmp_path, monkeypatch
):
    # Le régime d'avant #1277 : aucun plafond posé, donc le défaut du SDK. La même
    # ligne tue la session — c'est l'incident reproduit —, mais elle en sort
    # désormais sous son nom, et le moteur ne la relance pas.
    monkeypatch.setattr(claude_mod, "PLAFOND_FLUX_OCTETS", None)
    provider = ClaudeProvider(Credentials())

    with pytest.raises(PlafondFluxDepasse) as excinfo:
        asyncio.run(SESSIONS["run_agent"](provider, tmp_path))

    erreur = excinfo.value
    assert isinstance(erreur.__cause__, CLIJSONDecodeError)
    assert "plafond du flux fournisseur dépassé" in str(erreur)
    assert f"exceeds limit {DEFAUT_SDK}" in str(erreur)
    assert "transitoire" not in str(erreur)
    assert not est_transitoire(erreur)


def test_une_ligne_illisible_reste_un_alea():
    # Le SDK lève la même classe pour une ligne qui n'est pas du JSON : celle-là
    # n'a rien d'un plafond, et reste à la relance (ENF-06).
    illisible = CLIJSONDecodeError("{pas du json", ValueError("Expecting value"))

    assert claude_mod._depassement_flux(illisible) is None
    assert est_transitoire(illisible)
