"""Le navigateur de Maestro : ce qu'il est, sur quel poste il tourne, où il écrit (#1532).

Sur p5, l'équipe d'une application web n'avait aucun navigateur, et ses agents ont
écrit leurs propres bancs (Chrome par CDP, puppeteer installé pour l'occasion). Le
registre en proposait un, mais sur activation. Cette suite tient ce que l'équipe
reçoit désormais à sa composition (`maestro.agents.navigateur`) :

① **une version connue** — l'entrée de l'allowlist est figée, jamais `@latest`, sans
   fenêtre et sous un profil tenu en mémoire ; sa procédure renvoie à l'éditeur, pas à
   un fichier du dépôt de développement ;
② **le navigateur du poste** — Chrome s'il est là, sinon Edge, sinon Chromium, lus aux
   endroits où ils s'installent ; rien trouvé n'invente rien ;
③ **ses captures hors du projet** — sous le jetable du poste, jamais dans l'espace de
   la tâche, dont tout fichier ressortirait en livrable ;
④ **le garde-fou du registre** — une entrée retirée de l'allowlist n'équipe personne ;
⑤ **une seule fabrique** — la bibliothèque des intégrations pose le même serveur qu'une
   équipe, complété par le poste et sous le même nom (#1313) ;
⑥ **ce qu'il écrit sous un nom que l'agent donne** — posé sous son dossier de sorties
   par le point de contrôle, jamais dans l'espace de la tâche (#1674).

Aucun navigateur n'est lancé ni cherché sur le poste qui joue la suite : la sonde est
injectable de bout en bout. Le seul test qui lance le vrai CLI porte `cli_reel`.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    TextBlock,
    create_sdk_mcp_server,
    query,
    tool,
)
from fastapi.testclient import TestClient

from maestro.agents import navigateur
from maestro.agents.mcp import McpStore
from maestro.agents.mcp_federation import oublier_memo
from maestro.agents.mcp_registry import SEED, VERSION_PLAYWRIGHT_MCP, RegistreMcp
from maestro.agents.navigateur import (
    CE_QU_IL_FAIT,
    CONSIGNE_DE_L_AGENT,
    GESTES_DE_L_AGENT,
    ID_INTEGRATION,
    NOM_SERVEUR,
    OUTILS_QUI_ECRIVENT,
    PAQUET_ADAPTATEUR,
    NavigateurDuPoste,
    RedirectionDesSorties,
    dossier_des_sorties,
    integration_navigateur,
    navigateur_du_poste,
    outils_du_navigateur,
    redirection_des_sorties,
    serveur_navigateur,
)
from maestro.agents.permissions import PolitiqueOutils
from maestro.controltower.app import create_app
from maestro.controltower.events import InMemoryEventBus
from maestro.emplacements import racine_jetable
from maestro.providers import ClaudeProvider, Credentials
from maestro.providers import claude as claude_mod

# ── ① une version connue ──────────────────────────────────────────────────────


def test_l_entree_du_registre_est_figee_sans_fenetre_et_isolee() -> None:
    """#1313 : `@latest` à chaque tâche ferait tourner un code que personne n'a vu."""
    entree = RegistreMcp.curee().get(ID_INTEGRATION)
    assert entree is not None
    paquet = next(a for a in entree.args if a.startswith("@playwright/mcp"))
    assert paquet == f"@playwright/mcp@{VERSION_PLAYWRIGHT_MCP}"
    assert "latest" not in " ".join(entree.args)
    assert VERSION_PLAYWRIGHT_MCP.count(".") == 2  # une version, pas une étiquette
    assert "--headless" in entree.args
    assert "--isolated" in entree.args


def test_la_procedure_renvoie_a_l_editeur_et_jamais_au_depot_de_developpement() -> None:
    """Une installation de Maestro n'a pas `scripts/` : la procédure y renverrait au vide."""
    entree = next(e for e in SEED if e.id == ID_INTEGRATION)
    assert entree.procedure_url.startswith("https://")
    assert "scripts/" not in entree.procedure_url


# ── ② le navigateur du poste ──────────────────────────────────────────────────

_WINDOWS = {
    "LOCALAPPDATA": r"C:\Users\p\AppData\Local",
    "PROGRAMFILES": r"C:\Program Files",
    "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
}


def _presents(*chemins: str):
    attendus = {Path(c) for c in chemins}
    return lambda chemin: chemin in attendus


def _trouve(existe, *, plateforme: str = "win32", environ=None) -> NavigateurDuPoste | None:
    return navigateur_du_poste(
        environ=_WINDOWS if environ is None else environ, plateforme=plateforme, existe=existe
    )


_EDGE = NavigateurDuPoste("msedge", Path(r"C:\Program Files (x86)\Microsoft\Edge\msedge.exe"))


def test_chrome_installe_est_le_navigateur_retenu() -> None:
    existe = _presents(
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Users\p\AppData\Local\Chromium\Application\chrome.exe",
    )
    assert _trouve(existe) == NavigateurDuPoste(
        "chrome", Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    )


def test_sans_chrome_edge_est_retenu() -> None:
    """Le poste de référence de p5 : Windows, Edge livré avec le système."""
    existe = _presents(
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Users\p\AppData\Local\Chromium\Application\chrome.exe",
    )
    trouve = _trouve(existe)
    assert trouve is not None
    assert trouve.nom == "msedge"


def test_chrome_installe_pour_la_seule_personne_est_trouve() -> None:
    existe = _presents(r"C:\Users\p\AppData\Local\Google\Chrome\Application\chrome.exe")
    trouve = _trouve(existe)
    assert trouve is not None
    assert trouve.nom == "chrome"


def test_sans_chrome_ni_edge_chromium_est_retenu_par_son_executable() -> None:
    """#1313 : Chromium n'est pas un canal de l'adaptateur — son `--browser=chromium`
    viserait un Chromium qu'il télécharge. Il le reçoit donc par l'exécutable trouvé."""
    chemin = r"C:\Users\p\AppData\Local\Chromium\Application\chrome.exe"
    trouve = _trouve(_presents(chemin))
    assert trouve == NavigateurDuPoste("chromium", Path(chemin))
    assert trouve.arguments() == (f"--executable-path={Path(chemin)}",)


def test_un_canal_se_designe_par_son_nom() -> None:
    """Chrome et Edge : Playwright les retrouve lui-même aux emplacements de la sonde."""
    assert _EDGE.arguments() == ("--browser=msedge",)
    chrome = NavigateurDuPoste("chrome", Path("/opt/google/chrome/chrome"))
    assert chrome.arguments() == ("--browser=chrome",)


def test_rien_trouve_n_invente_aucun_navigateur() -> None:
    """None : le serveur garde son défaut, et sa propre erreur dira ce qui manque."""
    assert _trouve(lambda _: False) is None
    # Une variable absente n'ouvre aucun chemin relatif au répertoire courant.
    vus: list[Path] = []
    _trouve(lambda c: vus.append(c) or False, environ={})
    assert vus == []


@pytest.mark.parametrize(
    ("plateforme", "chemin", "attendu"),
    [
        ("darwin", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "msedge"),
        ("darwin", "/Applications/Chromium.app/Contents/MacOS/Chromium", "chromium"),
        ("linux", "/opt/google/chrome/chrome", "chrome"),
        ("linux", "/usr/bin/chromium", "chromium"),
        ("linux", "/snap/bin/chromium", "chromium"),
    ],
)
def test_hors_de_windows_les_emplacements_fixes_sont_lus(
    plateforme: str, chemin: str, attendu: str
) -> None:
    trouve = _trouve(_presents(chemin), plateforme=plateforme, environ={})
    assert trouve == NavigateurDuPoste(attendu, Path(chemin))


# ── ③ ses captures hors du projet ─────────────────────────────────────────────


def test_le_serveur_monte_porte_le_nom_de_maestro_et_le_navigateur_du_poste(
    tmp_path: Path,
) -> None:
    serveur = serveur_navigateur(sonde=lambda: _EDGE, sorties=tmp_path / "sorties")

    assert serveur.nom == NOM_SERVEUR
    assert outils_du_navigateur() == "mcp__navigateur__"
    assert serveur.type == "stdio"
    # Ce que le poste ajoute vient APRÈS l'entrée de l'allowlist, jamais à sa place.
    entree = RegistreMcp.curee().get(ID_INTEGRATION)
    assert entree is not None
    assert serveur.args[: len(entree.args)] == entree.args
    assert "--browser=msedge" in serveur.args
    assert f"--output-dir={tmp_path / 'sorties'}" in serveur.args


def test_chromium_est_pilote_par_son_executable(tmp_path: Path) -> None:
    chromium = NavigateurDuPoste("chromium", Path("/usr/bin/chromium"))
    serveur = serveur_navigateur(sonde=lambda: chromium, sorties=tmp_path)
    assert f"--executable-path={Path('/usr/bin/chromium')}" in serveur.args
    assert not any(a.startswith("--browser") for a in serveur.args)


def test_sans_navigateur_trouve_le_serveur_garde_son_defaut(tmp_path: Path) -> None:
    serveur = serveur_navigateur(sonde=lambda: None, sorties=tmp_path)
    assert not any(a.startswith(("--browser", "--executable-path")) for a in serveur.args)


def test_sans_sonde_injectee_le_poste_est_lu_a_l_appel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le défaut est `navigateur_du_poste`, cherché à l'appel et non figé à l'import."""
    monkeypatch.setattr(navigateur, "navigateur_du_poste", lambda: _EDGE)
    assert "--browser=msedge" in serveur_navigateur(sorties=tmp_path).args


def test_les_captures_tombent_sous_le_jetable_jamais_dans_l_espace_de_la_tache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sans `--output-dir`, le serveur écrit dans `.playwright-mcp/` du répertoire
    courant — l'espace de la tâche, qui part dans le projet à la fusion."""
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    sorties = dossier_des_sorties()
    assert sorties.parent == racine_jetable()
    assert sorties.is_relative_to(tmp_path)

    serveur = serveur_navigateur(sonde=lambda: _EDGE)
    assert f"--output-dir={sorties}" in serveur.args


def test_l_integration_garde_l_id_du_registre() -> None:
    """L'écran des intégrations la reconnaît comme l'entrée curée qu'elle est."""
    integration = integration_navigateur(sonde=lambda: _EDGE, sorties=Path("sorties"))
    assert integration.id == ID_INTEGRATION
    assert integration.serveur.nom == NOM_SERVEUR


def test_l_agent_lit_un_geste_par_promesse_faite_a_la_personne() -> None:
    """La personne lit `CE_QU_IL_FAIT`, l'agent `GESTES_DE_L_AGENT` : mêmes promesses."""
    assert len(GESTES_DE_L_AGENT) == len(CE_QU_IL_FAIT)
    assert any("reducedMotion" in geste for geste in GESTES_DE_L_AGENT)
    assert any("browser_console_messages" in geste for geste in GESTES_DE_L_AGENT)
    assert any("browser_resize" in geste for geste in GESTES_DE_L_AGENT)


def test_ce_que_le_navigateur_ecrit_se_lit_sans_passer_par_le_shell() -> None:
    """p5 rejoué (run `6193eadac741`) : deux agents ont lu au shell le journal de console
    que le serveur range hors du projet, et chaque lecture a attendu une personne. La
    consigne les envoie vers ce qui lit sans rien demander."""
    assert "`Read`" in CONSIGNE_DE_L_AGENT
    assert "`browser_console_messages`" in CONSIGNE_DE_L_AGENT
    assert "jamais au shell" in CONSIGNE_DE_L_AGENT


# ── ④ le garde-fou du registre ────────────────────────────────────────────────


def test_un_navigateur_retire_de_l_allowlist_n_equipe_personne() -> None:
    sans = RegistreMcp([e for e in SEED if e.id != ID_INTEGRATION])
    with pytest.raises(ValueError, match="hors allowlist"):
        serveur_navigateur(sans, sonde=lambda: _EDGE, sorties=Path("x"))


# ── ⑤ une seule fabrique ──────────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Une Control Tower aux disques MCP jetables, sur un poste où l'on « trouve » Edge."""
    monkeypatch.setenv("MAESTRO_MCP_DIR", str(tmp_path / "mcp"))
    monkeypatch.setenv("MAESTRO_MCP_AMONT_DIR", str(tmp_path / "amont"))
    monkeypatch.setenv("MAESTRO_SECRETS_DIR", str(tmp_path / "secrets"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "jetable"))
    monkeypatch.setattr(navigateur, "navigateur_du_poste", lambda: _EDGE)
    oublier_memo()
    with TestClient(create_app(bus=InMemoryEventBus())) as client:
        yield client
    oublier_memo()


def test_la_bibliotheque_pose_le_meme_navigateur_qu_une_equipe(
    client: TestClient, tmp_path: Path
) -> None:
    """#1313 : ajouté depuis les intégrations, le navigateur sortait nu de l'allowlist —
    le défaut du serveur (Chrome), ses captures dans l'espace de la tâche, et un nom
    (`playwright`) que le playbook d'une équipe ne nomme pas."""
    reponse = client.post("/api/mcp/pool", json={"registre_id": ID_INTEGRATION, "secrets": []})

    assert reponse.status_code == 201, reponse.text
    (integration,) = McpStore(tmp_path / "mcp").pool()
    assert integration == integration_navigateur(
        sonde=lambda: _EDGE, sorties=dossier_des_sorties()
    )
    assert integration.serveur.nom == NOM_SERVEUR
    assert "--browser=msedge" in integration.serveur.args
    assert f"@playwright/mcp@{VERSION_PLAYWRIGHT_MCP}" in integration.serveur.args


def test_un_autre_serveur_de_la_bibliotheque_n_est_pas_complete_par_le_poste(
    client: TestClient, tmp_path: Path
) -> None:
    reponse = client.post("/api/mcp/pool", json={"registre_id": "memory", "secrets": []})

    assert reponse.status_code == 201, reponse.text
    (integration,) = McpStore(tmp_path / "mcp").pool()
    entree = RegistreMcp.curee().get("memory")
    assert entree is not None
    assert integration.serveur == entree.vers_serveur()


# ── ⑥ ce qu'il écrit sous un nom que l'agent donne ────────────────────────────
#
# Relevé de #1674 : l'adaptateur résout un nom choisi par le modèle contre son espace
# de travail — la racine que le client annonce, c'est-à-dire la tâche —, et seul un nom
# qu'il choisit lui-même contre `--output-dir`. Une capture nommée tombait donc dans la
# tâche, et partait dans le projet de la personne à la fusion.

#: L'outil de capture, sous le nom que le point de contrôle lit.
_CAPTURE = f"{outils_du_navigateur()}browser_take_screenshot"


class _ClientPilote:
    """`ClaudeSDKClient` factice : serveurs connectés d'emblée, une réponse, options retenues."""

    options: ClaudeAgentOptions | None = None

    def __init__(self, options: ClaudeAgentOptions) -> None:
        type(self).options = options

    async def __aenter__(self) -> _ClientPilote:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get_mcp_status(self) -> dict[str, Any]:
        serveurs = (self.options.mcp_servers if self.options else None) or {}
        return {"mcpServers": [{"name": nom, "status": "connected"} for nom in serveurs]}

    async def query(self, prompt: str) -> None:
        return None

    async def receive_response(self):
        yield AssistantMessage(content=[TextBlock(text="Regardé.")], model="claude-double")


def _sortie_redirigee(sortie: object) -> dict[str, Any]:
    """L'entrée que le point de contrôle rend à l'outil — échoue s'il n'en rend aucune."""
    assert isinstance(sortie, dict) and sortie, f"l'appel est parti tel quel : {sortie!r}"
    decision = sortie["hookSpecificOutput"]
    assert decision["permissionDecision"] == "allow"
    return dict(decision["updatedInput"])


def test_une_session_qui_monte_le_navigateur_pose_ses_captures_nommees_hors_de_la_tache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'étape de reproduction de #1674, au point où Maestro la tient : une session qui
    monte le navigateur, sans politique ni frontière, et une capture que l'agent nomme."""
    monkeypatch.setattr(claude_mod, "ClaudeSDKClient", _ClientPilote)
    sorties = tmp_path / "sorties"
    tache = tmp_path / "tache"
    tache.mkdir()

    reponse = asyncio.run(
        ClaudeProvider(Credentials()).run_agent(
            "Regarde la page d'accueil.",
            model="claude-double",
            workspace=tache,
            tools=("Read",),
            mcp_serveurs=(serveur_navigateur(sonde=lambda: _EDGE, sorties=sorties),),
        )
    )

    assert reponse == "Regardé."
    options = _ClientPilote.options
    assert options is not None
    assert options.hooks, "aucun point de contrôle monté : la capture part telle que nommée"
    (matcher,) = options.hooks["PreToolUse"]
    (hook,) = matcher.hooks
    appel = {"tool_name": _CAPTURE, "tool_input": {"filename": "exercice-1313.png"}}
    sortie = asyncio.run(hook(appel, "tu-1", {"signal": None}))
    assert _sortie_redirigee(sortie) == {"filename": str(sorties / "exercice-1313.png")}
    # L'espace que la session reçoit est celui que la redirection connaît : un chemin
    # absolu sous la tâche tombe hors d'elle, au même chemin relatif.
    appel = {"tool_name": _CAPTURE, "tool_input": {"filename": str(tache / "accueil-375.png")}}
    sortie = asyncio.run(hook(appel, "tu-2", {"signal": None}))
    assert _sortie_redirigee(sortie) == {"filename": str(sorties / "accueil-375.png")}


def test_une_redirection_n_arme_aucune_sonde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La sonde éprouve qu'un **refus** tient : une session qui ne pose aucun refus n'en
    a pas à payer, même quand son point de contrôle est monté pour le navigateur."""
    monkeypatch.setattr(claude_mod, "ClaudeSDKClient", _ClientPilote)
    sondes: list[object] = []

    async def sonde(self: ClaudeProvider, **_: object) -> None:
        sondes.append(self)

    monkeypatch.setattr(ClaudeProvider, "_point_de_controle_verifie", sonde)

    asyncio.run(
        ClaudeProvider(Credentials()).run_agent(
            "Regarde.",
            model="claude-double",
            workspace=tmp_path,
            tools=("Read",),
            mcp_serveurs=(serveur_navigateur(sonde=lambda: _EDGE, sorties=tmp_path / "s"),),
        )
    )

    assert sondes == []


def _redirection(tmp_path: Path) -> RedirectionDesSorties:
    serveur = serveur_navigateur(sonde=lambda: _EDGE, sorties=tmp_path / "sorties")
    redirection = redirection_des_sorties([serveur], espace=tmp_path / "tache")
    assert redirection is not None
    return redirection


def test_une_capture_que_l_agent_nomme_tombe_sous_le_dossier_de_sorties(tmp_path: Path) -> None:
    """Le relevé de #1674 : `filename: "exercice-1313.png"` tombait dans la tâche."""
    entree = _redirection(tmp_path).entree(
        _CAPTURE, {"filename": "exercice-1313.png", "type": "png"}
    )
    assert entree == {"filename": str(tmp_path / "sorties" / "exercice-1313.png"), "type": "png"}


@pytest.mark.parametrize("outil", sorted(OUTILS_QUI_ECRIVENT))
def test_tout_ce_qu_il_ecrit_sous_un_nom_donne_tombe_sous_le_dossier_de_sorties(
    tmp_path: Path, outil: str
) -> None:
    """Captures, instantanés, console, réseau, PDF, état de stockage, vidéo : la consigne
    promet que tout ce que le navigateur écrit tombe hors du projet."""
    entree = _redirection(tmp_path).entree(
        f"{outils_du_navigateur()}{outil}", {"filename": "sortie.txt"}
    )
    assert entree == {"filename": str(tmp_path / "sorties" / "sortie.txt")}


def test_un_nom_relatif_garde_ses_dossiers_sous_le_dossier_de_sorties(tmp_path: Path) -> None:
    entree = _redirection(tmp_path).entree(_CAPTURE, {"filename": "accueil/375.png"})
    assert entree == {"filename": str(tmp_path / "sorties" / "accueil" / "375.png")}


def test_un_chemin_absolu_dans_la_tache_tombe_sous_le_dossier_de_sorties(
    tmp_path: Path,
) -> None:
    """Le premier exercice de #1674 : la personne demandait qu'on lui « garde » une
    capture, et l'agent l'a nommée sous un chemin du projet. Elle tombe hors de la tâche,
    au même chemin relatif, et la consigne dit comment la remettre."""
    absolu = str(tmp_path / "tache" / "docs" / "accueil.png")
    entree = _redirection(tmp_path).entree(_CAPTURE, {"filename": absolu, "fullPage": True})
    attendu = str(tmp_path / "sorties" / "docs" / "accueil.png")
    assert entree == {"filename": attendu, "fullPage": True}
    assert "même sous un chemin du projet" in CONSIGNE_DE_L_AGENT
    assert "compte rendu" in CONSIGNE_DE_L_AGENT


def test_un_chemin_deja_sous_le_dossier_de_sorties_n_est_pas_retouche(tmp_path: Path) -> None:
    """Un nom que l'adaptateur a lui-même désigné, et que l'agent lui rend."""
    absolu = str(tmp_path / "sorties" / "page-1.yml")
    assert _redirection(tmp_path).entree(_CAPTURE, {"filename": absolu}) is None


def test_le_dossier_de_sorties_dans_la_tache_reste_le_dossier_de_sorties(tmp_path: Path) -> None:
    """Un dossier de sorties posé sous l'espace (un réglage, pas le défaut) : un chemin
    déjà dessous n'y est pas reposé une seconde fois."""
    serveur = serveur_navigateur(sonde=lambda: _EDGE, sorties=tmp_path / "sorties")
    redirection = redirection_des_sorties([serveur], espace=tmp_path)
    assert redirection is not None
    assert redirection.entree(_CAPTURE, {"filename": str(tmp_path / "sorties" / "a.png")}) is None
    entree = redirection.entree(_CAPTURE, {"filename": str(tmp_path / "b.png")})
    assert entree == {"filename": str(tmp_path / "sorties" / "b.png")}


def test_un_chemin_absolu_hors_des_deux_racines_part_tel_quel(tmp_path: Path) -> None:
    """Ni sous la tâche ni sous le dossier de sorties : l'adaptateur le refuse lui-même, et
    le reposer ailleurs réécrirait en silence un geste qu'il juge."""
    ailleurs = str(tmp_path / "ailleurs" / "a.png")
    assert _redirection(tmp_path).entree(_CAPTURE, {"filename": ailleurs}) is None


def test_sans_espace_connu_un_chemin_absolu_part_tel_quel(tmp_path: Path) -> None:
    serveur = serveur_navigateur(sonde=lambda: _EDGE, sorties=tmp_path / "sorties")
    redirection = redirection_des_sorties([serveur])
    assert redirection is not None
    absolu = str(tmp_path / "tache" / "a.png")
    assert redirection.entree(_CAPTURE, {"filename": absolu}) is None


def test_la_tache_elle_meme_n_est_pas_un_nom_de_fichier(tmp_path: Path) -> None:
    assert _redirection(tmp_path).entree(_CAPTURE, {"filename": str(tmp_path / "tache")}) is None


def test_la_consigne_detourne_la_capture_par_du_code() -> None:
    """Ce que le point de contrôle ne voit pas : un chemin écrit dans un programme passé à
    `browser_run_code_unsafe`. La consigne nomme l'outil de capture à sa place."""
    assert "`browser_take_screenshot`" in CONSIGNE_DE_L_AGENT
    assert "`browser_run_code_unsafe`" in CONSIGNE_DE_L_AGENT
    assert any("fullPage" in geste for geste in GESTES_DE_L_AGENT)


@pytest.mark.parametrize("nom", ["../x.png", "a/../../x.png", "."])
def test_un_nom_qui_sortirait_du_dossier_de_sorties_n_y_est_pas_pose(
    tmp_path: Path, nom: str
) -> None:
    """Joint au dossier de sorties, il en sortirait : il part tel que l'agent l'a écrit, et
    l'adaptateur le refuse lui-même, hors de ses racines."""
    assert _redirection(tmp_path).entree(_CAPTURE, {"filename": nom}) is None


@pytest.mark.parametrize(
    ("outil", "entree"),
    [
        ("browser_run_code_unsafe", {"filename": "banc.js"}),
        ("browser_set_storage_state", {"filename": "etat.json"}),
        ("browser_file_upload", {"paths": ["fixtures/photo.png"]}),
    ],
)
def test_ce_qu_il_lit_dans_le_projet_reste_lu_dans_le_projet(
    tmp_path: Path, outil: str, entree: dict[str, Any]
) -> None:
    """Ces noms désignent un fichier à **lire** : posés sous le dossier de sorties, l'agent
    ne pourrait plus donner au navigateur un fichier de son projet."""
    assert not OUTILS_QUI_ECRIVENT & {outil}
    assert _redirection(tmp_path).entree(f"{outils_du_navigateur()}{outil}", entree) is None


def test_sans_nom_donne_rien_ne_change(tmp_path: Path) -> None:
    redirection = _redirection(tmp_path)
    assert redirection.entree(_CAPTURE, {}) is None
    assert redirection.entree(_CAPTURE, {"filename": ""}) is None
    assert redirection.entree(_CAPTURE, {"filename": 42}) is None
    assert redirection.entree("mcp__autre__browser_take_screenshot", {"filename": "a.png"}) is None


def test_la_redirection_suit_le_nom_de_la_liaison(tmp_path: Path) -> None:
    serveur = serveur_navigateur(nom="web", sonde=lambda: _EDGE, sorties=tmp_path)
    redirection = redirection_des_sorties([serveur])
    assert redirection is not None
    entree = redirection.entree("mcp__web__browser_take_screenshot", {"filename": "a.png"})
    assert entree == {"filename": str(tmp_path / "a.png")}


def test_une_session_sans_navigateur_n_a_rien_a_rediriger(tmp_path: Path) -> None:
    """Rien à rediriger : ni serveur, ni un autre serveur, ni l'adaptateur sans dossier de
    sorties déclaré — on ne sait alors pas où il écrit, et on ne devine pas."""
    memoire = RegistreMcp.curee().get("memory")
    entree = RegistreMcp.curee().get(ID_INTEGRATION)
    assert memoire is not None and entree is not None
    nu = replace(serveur_navigateur(sonde=lambda: _EDGE, sorties=tmp_path), args=entree.args)

    assert redirection_des_sorties([]) is None
    assert redirection_des_sorties([memoire.vers_serveur()]) is None
    assert redirection_des_sorties([nu]) is None


def test_l_adaptateur_reconnu_est_celui_de_l_allowlist() -> None:
    """Le paquet que la redirection reconnaît est celui que le registre monte : deux
    orthographes qui divergeraient laisseraient le navigateur sans redirection."""
    entree = RegistreMcp.curee().get(ID_INTEGRATION)
    assert entree is not None
    assert f"{PAQUET_ADAPTATEUR}@{VERSION_PLAYWRIGHT_MCP}" in entree.args


def test_le_point_de_controle_pose_la_capture_sans_politique(tmp_path: Path) -> None:
    hook = claude_mod._hook_permissions(None, None, redirection=_redirection(tmp_path))
    appel = {"tool_name": _CAPTURE, "tool_input": {"filename": "exercice-1313.png"}}
    sortie = asyncio.run(hook(appel, "t", None))
    attendu = {"filename": str(tmp_path / "sorties" / "exercice-1313.png")}
    assert _sortie_redirigee(sortie) == attendu


def test_le_point_de_controle_pose_la_capture_que_la_politique_laisse_passer(
    tmp_path: Path,
) -> None:
    hook = claude_mod._hook_permissions(
        PolitiqueOutils(deny=("Bash",)), None, redirection=_redirection(tmp_path)
    )
    appel = {"tool_name": _CAPTURE, "tool_input": {"filename": "a.png"}}
    assert _sortie_redirigee(asyncio.run(hook(appel, "t", None))) == {
        "filename": str(tmp_path / "sorties" / "a.png")
    }


def test_une_capture_refusee_reste_refusee(tmp_path: Path) -> None:
    """La redirection ne passe qu'après la politique : elle ne rouvre rien."""
    hook = claude_mod._hook_permissions(
        PolitiqueOutils(deny=(f"mcp__{NOM_SERVEUR}",)), None, redirection=_redirection(tmp_path)
    )
    appel = {"tool_name": _CAPTURE, "tool_input": {"filename": "a.png"}}
    sortie = asyncio.run(hook(appel, "t", None))
    assert sortie["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "updatedInput" not in sortie["hookSpecificOutput"]


def test_un_appel_sans_rien_a_poser_passe_comme_avant(tmp_path: Path) -> None:
    hook = claude_mod._hook_permissions(None, None, redirection=_redirection(tmp_path))
    appel = {
        "tool_name": f"{outils_du_navigateur()}browser_navigate",
        "tool_input": {"url": "http://localhost:3000"},
    }
    assert asyncio.run(hook(appel, "t", None)) == {}


def _modele_econome() -> str:
    from maestro.familles_claude import derniere_version

    return derniere_version("haiku")


@pytest.mark.cli_reel
def test_sur_le_vrai_cli_l_outil_recoit_le_nom_que_le_point_de_controle_a_pose(
    tmp_path: Path,
) -> None:
    """Ce que les doubles ne prouvent pas : que le CLI réel remet à l'outil l'entrée que le
    point de contrôle a réécrite, et non celle que le modèle a écrite."""
    recu: list[dict[str, Any]] = []

    @tool("browser_take_screenshot", "Capture la page sous le nom donné.", {"filename": str})
    async def capture(args: dict[str, Any]) -> dict[str, Any]:
        recu.append(dict(args))
        return {"content": [{"type": "text", "text": "Capture faite."}]}

    redirection = RedirectionDesSorties({outils_du_navigateur(): tmp_path / "sorties"})
    point_de_controle = claude_mod._hook_permissions(None, None, redirection=redirection)
    options = ClaudeAgentOptions(
        model=_modele_econome(),
        system_prompt="Tu appelles un outil une fois quand on te le demande, puis tu réponds FIN.",
        env=ClaudeProvider(Credentials())._auth_env(),
        cwd=tmp_path,
        tools=[],
        permission_mode="bypassPermissions",
        max_turns=3,
        mcp_servers={NOM_SERVEUR: create_sdk_mcp_server(name=NOM_SERVEUR, tools=[capture])},
        strict_mcp_config=True,
        setting_sources=claude_mod.sans_reglages_du_poste(),
        skills=claude_mod.sans_skills_du_poste(),
        hooks={"PreToolUse": [HookMatcher(hooks=[point_de_controle])]},
    )

    async def joue() -> None:
        prompt = (
            "Appelle l'outil browser_take_screenshot une seule fois, avec "
            'filename="exercice-1674.png", puis réponds FIN.'
        )
        async for _ in query(prompt=prompt, options=options):
            pass

    asyncio.run(joue())

    assert recu == [{"filename": str(tmp_path / "sorties" / "exercice-1674.png")}]
