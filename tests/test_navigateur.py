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
   équipe, complété par le poste et sous le même nom (#1313).

Aucun navigateur n'est lancé ni cherché sur le poste qui joue la suite : la sonde est
injectable de bout en bout.
"""

from __future__ import annotations

from pathlib import Path

import pytest
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
    NavigateurDuPoste,
    dossier_des_sorties,
    integration_navigateur,
    navigateur_du_poste,
    outils_du_navigateur,
    serveur_navigateur,
)
from maestro.controltower.app import create_app
from maestro.controltower.events import InMemoryEventBus
from maestro.emplacements import racine_jetable

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
