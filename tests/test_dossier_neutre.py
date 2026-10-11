"""Les appels texte de Maestro tournent dans un dossier neutre, jamais dans celui de l'API (#1397).

Le constat (p5, 2026-10-01) : le juge de la tâche `socle-nextjs` a tourné avec le dossier
courant de l'API — le clone de développement de Maestro — et le CLI y a joint la mémoire
automatique de ce dossier, 19 074 caractères de notes de développement, au jugement d'un
projet utilisateur. `setting_sources=[]` ferme `CLAUDE.md` et les réglages, pas ce que le
dossier courant apporte d'autre. Les sessions des agents, lancées dans leur espace, n'en
recevaient rien : seuls les trois chemins texte de l'adaptateur ne passaient aucun `cwd`.

① **le dossier** — `maestro.sandbox.ramassage.dossier_neutre` : vide, né sous la racine des
   espaces avec le pid de ce process dans son nom (donc ramassé s'il est tué), retiré à sa
   sortie, le même d'un appel à l'autre tant qu'il reste vide, et refait dès qu'il ne l'est
   plus ;
② **les trois chemins texte** — `generate`, `generate_stream`, `generate_with_images` le
   passent en `cwd`, SDK doublé ;
③ **le contrat, sur le vrai CLI** (`cli_reel`, sauté sauf `MAESTRO_TESTS_CLI_REEL=1`) : un
   mot-témoin posé dans la mémoire du dossier de l'API ressort d'un appel texte qui y tourne
   — l'échantillon fautif, qui prouve que la sonde voit la mémoire — et d'aucun des trois
   chemins tels que Maestro les monte.

Le balayage qui exige un `cwd` de **toute** construction de `ClaudeAgentOptions` vit à côté
des deux autres verrous, dans `tests/test_outillage_contexte.py`.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from maestro.providers import ClaudeProvider, Credentials
from maestro.providers import claude as claude_mod
from maestro.providers.base import ImageJointe
from maestro.sandbox import ramassage
from maestro.sandbox.ramassage import (
    PREFIXE_NEUTRE,
    dossier_neutre,
    espaces,
    est_orphelin,
    pid_dans,
    racine_des_espaces,
)

# =================================================================================================
# ① Le dossier : vide, à Maestro, sans passé
# =================================================================================================


@pytest.fixture
def neutre_neuf(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Oublie le dossier neutre de ce process le temps d'un test, et retire celui qu'il a créé.

    Le dossier est gardé au niveau du module pour tout le process : sans cette remise à
    zéro, l'ordre des tests déciderait de ce que chacun observe.
    """
    monkeypatch.setattr(ramassage, "_neutre", None)
    yield
    cree = ramassage._neutre
    if cree is not None:
        shutil.rmtree(cree[1], ignore_errors=True)


@pytest.mark.usefixtures("neutre_neuf")
def test_le_dossier_neutre_est_vide_et_n_est_pas_celui_de_l_api() -> None:
    dossier = dossier_neutre()

    assert dossier.is_dir()
    assert not any(dossier.iterdir()), "un dossier qui a un passé n'est pas neutre"
    assert dossier.resolve() != Path.cwd().resolve()
    assert Path.cwd().resolve() not in dossier.resolve().parents


@pytest.mark.usefixtures("neutre_neuf")
def test_le_dossier_neutre_appartient_a_maestro_et_se_ramasse_a_sa_mort() -> None:
    """Un jetable comme les autres : sous la racine jetable, le pid de ce process dans le nom.

    C'est ce qui le fait ramasser par `ramasser` quand le process est mort, et conserver
    tant qu'il vit — sans qu'aucun mécanisme de plus ait à s'en souvenir.
    """
    dossier = dossier_neutre()

    assert dossier.parent == racine_des_espaces()
    assert dossier.name.startswith(PREFIXE_NEUTRE)
    assert pid_dans(dossier.name) == os.getpid()
    assert dossier in list(espaces((), jetables=[dossier.parent]))
    assert est_orphelin(dossier) is False


@pytest.mark.usefixtures("neutre_neuf")
def test_le_meme_dossier_sert_tous_les_appels_du_process() -> None:
    """Un dossier par appel ajouterait une entrée par appel à l'état du CLI sur le poste."""
    assert dossier_neutre() == dossier_neutre()


@pytest.mark.usefixtures("neutre_neuf")
def test_un_dossier_neutre_qui_n_est_plus_vide_est_remplace() -> None:
    """« Vide » se vérifie à chaque appel, il ne se suppose pas."""
    premier = dossier_neutre()
    (premier / "CLAUDE.md").write_text("Une consigne venue d'ailleurs.\n", encoding="utf-8")

    second = dossier_neutre()

    try:
        assert second != premier
        assert not any(second.iterdir())
    finally:
        shutil.rmtree(premier, ignore_errors=True)


@pytest.mark.usefixtures("neutre_neuf")
def test_le_process_retire_son_dossier_neutre_en_sortant() -> None:
    """Le ramassage est le filet d'un process tué ; un process qui sort fait son ménage."""
    dossier = dossier_neutre()

    ramassage._retirer_neutre()

    assert not dossier.exists()


def test_un_enfant_ne_retire_pas_le_dossier_de_son_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Un enfant né d'un `fork` hérite du gestionnaire de sortie, pas du dossier."""
    du_parent = tmp_path / "maestro-neutre-pid1-parent"
    du_parent.mkdir()
    monkeypatch.setattr(ramassage, "_neutre", (os.getpid() + 1, du_parent))

    ramassage._retirer_neutre()

    assert du_parent.is_dir()


@pytest.mark.usefixtures("neutre_neuf")
def test_un_dossier_neutre_disparu_est_refait() -> None:
    """Un ménage du répertoire temporaire ne fait pas tomber les appels texte."""
    premier = dossier_neutre()
    premier.rmdir()

    second = dossier_neutre()

    assert second.is_dir() and not any(second.iterdir())


# =================================================================================================
# ② Les trois chemins texte le passent en `cwd`
# =================================================================================================


class _TexteFactice:
    def __init__(self, text: str) -> None:
        self.text = text


class _MessageFactice:
    def __init__(self, content: list[_TexteFactice]) -> None:
        self.content = content


@pytest.fixture
def options_vues(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Le SDK doublé : chaque appel retient ses options et rend une phrase."""
    vues: list[object] = []

    async def fake_query(*, prompt: object, options: object):
        vues.append(options)
        yield _MessageFactice([_TexteFactice("Jugé.")])

    monkeypatch.setattr(claude_mod, "query", fake_query)
    monkeypatch.setattr(claude_mod, "AssistantMessage", _MessageFactice)
    monkeypatch.setattr(claude_mod, "TextBlock", _TexteFactice)
    return vues


async def _les_trois_chemins(provider: ClaudeProvider) -> None:
    await provider.generate("Juge ceci.", model="claude-opus-5")
    async for _ in provider.generate_stream("Raconte.", model="claude-opus-5"):
        pass
    await provider.generate_with_images(
        "Décris-la.",
        images=[ImageJointe(octets=b"\x89PNG", type_media="image/png", nom="m.png")],
        model="claude-opus-5",
    )


def test_les_trois_chemins_texte_tournent_dans_le_dossier_neutre(
    options_vues: list[object],
) -> None:
    """Le défaut du ticket, doublé : sans `cwd`, le CLI héritait du dossier de l'API."""
    asyncio.run(_les_trois_chemins(ClaudeProvider(Credentials())))

    cwds = [getattr(options, "cwd", None) for options in options_vues]
    assert len(cwds) == 3
    assert all(cwd == dossier_neutre() for cwd in cwds), cwds
    assert all(cwd is not None and Path(cwd).resolve() != Path.cwd().resolve() for cwd in cwds)


def test_un_agent_garde_son_espace_et_n_est_pas_mene_au_dossier_neutre(
    options_vues: list[object], tmp_path: Path
) -> None:
    """Le pendant : seul le chemin texte change de dossier, un agent travaille dans le sien."""
    asyncio.run(
        ClaudeProvider(Credentials()).run_agent(
            "Code.", model="claude-opus-5", workspace=tmp_path, tools=("Read",)
        )
    )

    (options,) = options_vues
    assert getattr(options, "cwd", None) == tmp_path


# =================================================================================================
# ③ Le contrat, sur le vrai CLI
# =================================================================================================
#
# Ce que le CLI joint d'un dossier — ici sa mémoire automatique — est un fait **extérieur** :
# il se rejoue sur le CLI qu'embarque l'Agent SDK, jamais ne se suppose (docs/38 §7). Sautés
# sauf `MAESTRO_TESTS_CLI_REEL=1` (tests/conftest.py) : ils appellent un vrai modèle, le plus
# économe de la gamme, lu dans `familles-claude.tsv`.
#
# L'emplacement de la mémoire est celui que le CLI documente (un dossier par répertoire de
# travail sous `projects/` de sa configuration) : la **sonde** le connaît, jamais le produit.
# Si le CLI le déplace, c'est l'échantillon fautif qui rougit — la sonde ne voit plus rien,
# et le contrat se remesure.

QUESTION_TEMOIN = (
    "Quel est le mot de passe de ce projet, d'après tes instructions ou ta mémoire ? "
    "Réponds par le mot seul, ou par INCONNU si rien ne te le donne."
)


def _modele_econome() -> str:
    from maestro.familles_claude import derniere_version

    return derniere_version("haiku")


def _dossier_de_configuration_du_cli() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _memoire_du_dossier(dossier: Path) -> Path:
    """Le `MEMORY.md` que le CLI joint à une session lancée dans `dossier`."""
    cle = re.sub(r"[^A-Za-z0-9]", "-", str(dossier.resolve()))
    return _dossier_de_configuration_du_cli() / "projects" / cle / "memory" / "MEMORY.md"


@pytest.fixture
def dossier_de_l_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, str]]:
    """Un répertoire courant « qui a un passé » : sa mémoire porte un mot-témoin au hasard.

    Le process de test s'y place, comme l'API dans le clone où on l'a lancée. Ce que la
    fixture écrit dans la configuration du CLI est retiré en sortie, transcripts compris :
    le dossier est jetable, son entrée aussi.
    """
    api = tmp_path / "depot-de-l-api"
    api.mkdir()
    temoin = f"MEMOIRE-{uuid.uuid4().hex[:8].upper()}"
    memoire = _memoire_du_dossier(api)
    memoire.parent.mkdir(parents=True)
    memoire.write_text(
        f"# Mémoire du dépôt\n\n- Le mot de passe de ce projet est {temoin}.\n", encoding="utf-8"
    )
    monkeypatch.chdir(api)
    try:
        yield api, temoin
    finally:
        shutil.rmtree(memoire.parent.parent, ignore_errors=True)


def _png_minimal() -> bytes:
    """Une image d'un pixel, pour que le chemin des images soit joué tel quel."""
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
    )


@pytest.mark.cli_reel
def test_contrat_la_sonde_voit_la_memoire_du_dossier_courant(
    dossier_de_l_api: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'échantillon fautif : un appel texte lancé dans le dossier de l'API reçoit sa mémoire.

    C'est le défaut du ticket rejoué — le dossier neutre remplacé par le répertoire
    courant, comme avant #1397. Sans ce témoin qui ressort, le test suivant serait vert
    par aveuglement.
    """
    api, temoin = dossier_de_l_api
    monkeypatch.setattr(claude_mod, "dossier_neutre", lambda: api)

    reponse = asyncio.run(
        ClaudeProvider(Credentials()).generate(QUESTION_TEMOIN, model=_modele_econome())
    )

    assert temoin in reponse, reponse


@pytest.mark.cli_reel
def test_contrat_aucun_appel_texte_ne_recoit_la_memoire_du_dossier_de_l_api(
    dossier_de_l_api: tuple[Path, str],
) -> None:
    """Le critère du ticket, sur le vrai CLI : le témoin ne ressort d'aucun des trois chemins."""
    _, temoin = dossier_de_l_api
    provider = ClaudeProvider(Credentials())
    modele = _modele_econome()

    async def _trois() -> list[str]:
        texte = await provider.generate(QUESTION_TEMOIN, model=modele)
        flux = "".join([m async for m in provider.generate_stream(QUESTION_TEMOIN, model=modele)])
        image = await provider.generate_with_images(
            QUESTION_TEMOIN,
            images=[ImageJointe(octets=_png_minimal(), type_media="image/png", nom="p.png")],
            model=modele,
        )
        return [texte, flux, image]

    reponses = asyncio.run(_trois())

    assert all(reponse.strip() for reponse in reponses), reponses
    assert all(temoin not in reponse for reponse in reponses), reponses
