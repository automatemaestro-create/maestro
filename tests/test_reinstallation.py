"""Une réinstallation ne perd rien : ce que Maestro écrit vit sous l'état du poste (#1620).

Ce que ce fichier tient :

① **le réglage** — `MAESTRO_INSTALLATION` se lit en trois valeurs (absent, `1`, refusé), le
  paquet le pose, et la suite le neutralise ;
② **les dépôts** — installé, chacun écrit sous `~/.maestro/configuration/`, jamais sous le
  `core/` du code ; en clone, rien ne change ;
③ **les défauts livrés** — les politiques et les déclarations MCP que le paquet embarque se
  relisent sous ce que le poste a écrit, sans jamais être écrites ni retirées ;
④ **les deux `.env`** — celui du paquet, que chaque installation remplace, et celui de la
  personne sous l'état du poste, qui l'emporte ;
⑤ **le lanceur** — ses journaux et sa session sous l'état du poste, la reprise jouée avant
  l'API ;
⑥ **la reprise** d'une 0.1.0 — une seule fois, sans doublon, sans rien écraser.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import conftest
import pytest
from dotenv import dotenv_values

from maestro import config as configuration
from maestro import emplacements
from maestro.agents.capacity import CapacityStore
from maestro.agents.mcp import McpStore
from maestro.agents.mcp_amont import MiroirAmont
from maestro.agents.permissions import PermissionStore
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.secrets import SecretStore
from maestro.agents.store import AgentStore, SurchargeStore
from maestro.controltower.accueil import DepotAccueil
from maestro.controltower.chat import ChatStore
from maestro.emplacements import (
    VARIABLE_INSTALLATION,
    InstallationIllisible,
    configuration_du_poste,
    depot_configuration,
    depot_livre,
    env_du_poste,
    installation,
    journaux_du_lanceur,
    racine_livree,
    reprise_du_poste,
)
from maestro.fournisseur_par_defaut import DepotFournisseurParDefaut
from maestro.installation import LecteurInstallation
from maestro.lanceur import emplacement as lieu
from maestro.lanceur import lanceur
from maestro.lanceur.emplacement import Emplacement
from maestro.lanceur.reprise import ENTETE_ENV, TEMOIN, Bilan, reprendre
from maestro.projets.reglages import ReglagesProjetsStore
from maestro.projets.store import ProjetStore
from maestro.sources.resolution import ingestion_root

RACINE_DEPOT = Path(__file__).resolve().parents[1]

INSTALLE = {VARIABLE_INSTALLATION: "1"}

#: Les variables qui déplacent un dépôt : toutes vidées, pour lire le défaut.
VARIABLES_DES_DEPOTS = (
    "MAESTRO_PROJETS_DIR",
    "MAESTRO_REGLAGES_DIR",
    "MAESTRO_SECRETS_DIR",
    "MAESTRO_AGENTS_DIR",
    "MAESTRO_SURCHARGES_DIR",
    "MAESTRO_PLAYBOOKS_DIR",
    "MAESTRO_PERMISSIONS_DIR",
    "MAESTRO_MCP_DIR",
    "MAESTRO_MCP_AMONT_DIR",
    "MAESTRO_CAPACITE_DIR",
    "MAESTRO_CHAT_DIR",
    "MAESTRO_INGESTION_DIR",
)


@pytest.fixture
def maison(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier personnel à part : l'état du poste du test, jamais celui du poste."""
    dossier = tmp_path_factory.mktemp("maison")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: dossier))
    return dossier


@pytest.fixture
def defauts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aucun dépôt déplacé par l'environnement : chacun lit son défaut."""
    for variable in VARIABLES_DES_DEPOTS:
        monkeypatch.setenv(variable, "")


@pytest.fixture
def installe(monkeypatch: pytest.MonkeyPatch, maison: Path, defauts: None) -> Path:
    """Le produit se croit installé, sur un dossier personnel à part."""
    monkeypatch.setenv(VARIABLE_INSTALLATION, "1")
    return maison


# =================================================================================================
# ① Le réglage
# =================================================================================================


def test_le_reglage_se_lit_en_trois_valeurs() -> None:
    assert installation({}) is False
    assert installation({VARIABLE_INSTALLATION: ""}) is False
    assert installation({VARIABLE_INSTALLATION: " 1 "}) is True
    with pytest.raises(InstallationIllisible, match="oui"):
        installation({VARIABLE_INSTALLATION: "oui"})


def test_la_suite_joue_le_produit_tel_qu_un_clone() -> None:
    """Le conftest vide le réglage, avec la même clé que le module des racines."""
    assert conftest.CLE_INSTALLATION == VARIABLE_INSTALLATION
    assert os.environ.get(VARIABLE_INSTALLATION) == ""
    assert installation() is False


def test_la_forme_que_l_api_sert_suit_le_reglage_qui_range_la_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, maison: Path, defauts: None
) -> None:
    """Un seul signal décide de « installé » (#1633) : le réglage qui range la configuration
    donne aussi sa forme au produit (`GET /api/installation`). Ni un `.git` qui traîne dans
    une installation, ni une copie du dépôt qui n'en porte pas n'en décident."""
    avec_git = tmp_path / "avec-git"
    (avec_git / ".git").mkdir(parents=True)
    sans_git = tmp_path / "sans-git"
    sans_git.mkdir()

    for valeur, forme, rangement in (
        ("", "depot", racine_livree()),
        ("1", "installation", configuration_du_poste()),
    ):
        monkeypatch.setenv(VARIABLE_INSTALLATION, valeur)
        assert emplacements.racine_configuration() == rangement
        for racine in (avec_git, sans_git):
            # Le lecteur de la route, sur l'environnement du process ; aucun CLI à lancer.
            lecteur = LecteurInstallation(
                racine=racine, sdk=tmp_path / "sans-sdk", resolveur=lambda _nom: None
            )
            assert lecteur.lire().forme == forme, f"{racine.name}, réglage {valeur!r}"


# =================================================================================================
# ② Les dépôts
# =================================================================================================


def _racines_des_depots() -> dict[str, Path]:
    """La racine par défaut de chaque dépôt qu'écrit le produit."""
    depots = {
        "projets": ProjetStore.default(),
        "reglages": ReglagesProjetsStore.default(),
        "secrets": SecretStore.default(),
        "agents": AgentStore.default(),
        "surcharges": SurchargeStore.default(),
        "playbooks": PlaybookStore.default(),
        "permissions": PermissionStore.default(),
        "mcp": McpStore.default(),
        "mcp-amont": MiroirAmont.default(),
        "capacite": CapacityStore.default(),
        "chat": ChatStore.default(),
    }
    racines = {nom: Path(getattr(d, "racine", None) or d._racine) for nom, d in depots.items()}
    racines["accueil"] = DepotAccueil.default().chemin.parent
    racines["fournisseur"] = DepotFournisseurParDefaut.default().chemin.parent
    racines["ingestion"] = ingestion_root()
    return racines


#: Le dossier de chaque dépôt sous la configuration (l'accueil et le fournisseur vivent
#: dans celui des réglages).
DOSSIERS = {
    "accueil": "reglages",
    "fournisseur": "reglages",
}


def test_installe_chaque_depot_ecrit_sous_l_etat_du_poste(installe: Path) -> None:
    configuration = installe / ".maestro" / "configuration"
    assert configuration_du_poste() == configuration
    for nom, racine in _racines_des_depots().items():
        assert racine == configuration / DOSSIERS.get(nom, nom), nom
        assert not racine.is_relative_to(emplacements.racine_du_code()), nom


def test_en_clone_chaque_depot_reste_dans_le_core_du_depot(maison: Path, defauts: None) -> None:
    """Le mode clone ne change pas : `core/` du dépôt, comme avant."""
    for nom, racine in _racines_des_depots().items():
        assert racine == RACINE_DEPOT / "core" / DOSSIERS.get(nom, nom), nom


def test_une_variable_de_depot_passe_devant_dans_les_deux_cas(
    installe: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAESTRO_PROJETS_DIR", str(tmp_path / "ailleurs"))
    assert ProjetStore.default().racine == tmp_path / "ailleurs"


def test_installe_un_reglage_pose_vit_sous_l_etat_du_poste(installe: Path) -> None:
    depot = DepotAccueil.default()
    depot.terminer()
    assert depot.chemin == installe / ".maestro" / "configuration" / "reglages" / "accueil.json"
    assert depot.chemin.is_file()
    assert not (racine_livree() / "reglages" / "accueil.json").exists()


# =================================================================================================
# ③ Les défauts livrés
# =================================================================================================


def test_installe_les_politiques_livrees_se_relisent_dans_l_installation(installe: Path) -> None:
    depot = PermissionStore.default()
    assert depot.livres == racine_livree() / "permissions"
    assert depot.racine == depot_configuration("permissions")
    suivies = sorted(chemin.stem for chemin in (racine_livree() / "permissions").glob("*.json"))
    assert suivies, "le dépôt suit des politiques par défaut"
    assert set(suivies) <= set(depot.agents())
    assert depot.lire(suivies[0]) is not None


def test_une_politique_ecrite_recouvre_la_livree_sans_la_toucher(tmp_path: Path) -> None:
    livres = tmp_path / "livres"
    livres.mkdir()
    source = RACINE_DEPOT / "core" / "permissions" / "qa.json"
    (livres / "qa.json").write_bytes(source.read_bytes())
    depot = PermissionStore(tmp_path / "poste", livres=livres)

    livree = depot.lire("qa")
    assert livree is not None
    depot.ecrire("qa", {"allow": ["Read"], "ask": [], "deny": ["Bash"]})

    assert (tmp_path / "poste" / "qa.json").is_file()
    assert (livres / "qa.json").read_bytes() == source.read_bytes()
    relue = depot.lire("qa")
    assert relue is not None and relue != livree
    assert depot.agents() == ("qa",)


def test_en_clone_aucun_defaut_n_est_relu_ailleurs(maison: Path, defauts: None) -> None:
    assert depot_livre("permissions") is None
    assert PermissionStore.default().livres is None
    assert McpStore.default().livres is None


def test_une_declaration_mcp_livree_migree_est_recouverte_et_jamais_effacee(
    tmp_path: Path,
) -> None:
    livres = tmp_path / "livres"
    livres.mkdir()
    source = RACINE_DEPOT / "core" / "mcp" / "qa.json"
    (livres / "qa.json").write_bytes(source.read_bytes())
    depot = McpStore(tmp_path / "poste", livres=livres)

    assert depot.agents() == ("qa",)
    assert [s.nom for s in depot.heritees("qa")] == ["forge"]

    migration = depot.migrer_agent("qa")

    assert migration.fichier_retire
    assert depot.heritees("qa") == ()
    assert [s.nom for s in depot.lire("qa")] == ["forge"]
    assert (livres / "qa.json").read_bytes() == source.read_bytes()
    with pytest.raises(ValueError, match="aucune déclaration héritée"):
        depot.migrer_agent("qa")


def test_sans_defaut_livre_la_migration_retire_le_fichier(tmp_path: Path) -> None:
    racine = tmp_path / "poste"
    racine.mkdir()
    (racine / "qa.json").write_bytes((RACINE_DEPOT / "core" / "mcp" / "qa.json").read_bytes())
    McpStore(racine).migrer_agent("qa")
    assert not (racine / "qa.json").exists()


# =================================================================================================
# ④ Les deux .env
# =================================================================================================


@pytest.fixture
def env_propre(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Les clés que le test pose, retirées de l'environnement à la sortie."""
    cles: list[str] = []
    yield cles
    for cle in cles:
        os.environ.pop(cle, None)


def _produit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, texte: str) -> Path:
    env = tmp_path / "produit" / ".env"
    env.parent.mkdir(parents=True)
    env.write_text(texte, encoding="utf-8")
    monkeypatch.setattr(configuration, "_env_du_produit", lambda: str(env))
    return env


def test_installe_le_env_de_la_personne_l_emporte_sur_celui_du_paquet(
    maison: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env_propre: list[str]
) -> None:
    monkeypatch.setenv(VARIABLE_INSTALLATION, "x")
    monkeypatch.delenv(VARIABLE_INSTALLATION)
    env_propre.extend(["ESSAI_1620_COMMUN", "ESSAI_1620_PAQUET", "ESSAI_1620_PERSONNE"])
    _produit(
        tmp_path,
        monkeypatch,
        "MAESTRO_INSTALLATION=1\nESSAI_1620_COMMUN=paquet\nESSAI_1620_PAQUET=paquet\n",
    )
    personne = env_du_poste(INSTALLE)
    assert personne == maison / ".maestro" / "configuration" / ".env"
    personne.parent.mkdir(parents=True)
    personne.write_text("ESSAI_1620_COMMUN=personne\nESSAI_1620_PERSONNE=oui\n", encoding="utf-8")

    configuration.charger_les_env()

    assert os.environ["ESSAI_1620_COMMUN"] == "personne"
    assert os.environ["ESSAI_1620_PAQUET"] == "paquet"
    assert os.environ["ESSAI_1620_PERSONNE"] == "oui"
    assert installation() is True


def test_en_clone_le_env_du_poste_n_est_pas_lu(
    maison: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env_propre: list[str]
) -> None:
    env_propre.extend(["ESSAI_1620_COMMUN"])
    _produit(tmp_path, monkeypatch, "ESSAI_1620_COMMUN=depot\n")
    personne = maison / ".maestro" / "configuration" / ".env"
    personne.parent.mkdir(parents=True)
    personne.write_text("ESSAI_1620_COMMUN=personne\n", encoding="utf-8")

    configuration.charger_les_env()

    assert os.environ["ESSAI_1620_COMMUN"] == "depot"


def test_une_valeur_illisible_ne_fait_pas_tomber_la_configuration(
    maison: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env_propre: list[str]
) -> None:
    """Elle se refuse là où elle décide — un dépôt, le lanceur —, avec son motif."""
    monkeypatch.setenv(VARIABLE_INSTALLATION, "oui")
    env_propre.extend(["ESSAI_1620_PAQUET"])
    _produit(tmp_path, monkeypatch, "ESSAI_1620_PAQUET=paquet\n")
    configuration.charger_les_env()
    assert os.environ["ESSAI_1620_PAQUET"] == "paquet"
    with pytest.raises(InstallationIllisible):
        ProjetStore.default()


# =================================================================================================
# ⑤ Le lanceur
# =================================================================================================


def test_installe_les_journaux_du_lanceur_vivent_sous_l_etat_du_poste(
    maison: Path, tmp_path: Path
) -> None:
    (tmp_path / "web").mkdir()
    installe = lieu.emplacement(INSTALLE, depart=tmp_path)
    assert installe.journaux == maison / ".maestro" / "lanceur" == journaux_du_lanceur()
    assert installe.session == journaux_du_lanceur() / "session.json"
    clone = lieu.emplacement({}, depart=tmp_path)
    assert clone.journaux == tmp_path / ".maestro" / "lanceur"


def test_le_lanceur_refuse_une_valeur_illisible_avec_son_motif(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from maestro.lanceur import cli

    monkeypatch.setenv(VARIABLE_INSTALLATION, "oui")
    assert cli.main(["--diagnostic"]) == 1
    assert "MAESTRO_INSTALLATION='oui'" in capsys.readouterr().err


def _emplacement_servable(racine: Path) -> Emplacement:
    front = racine / "apps" / "web"
    (front / ".next").mkdir(parents=True)
    (front / ".next" / "BUILD_ID").write_text("abc", encoding="utf-8")
    binaire = front / "node_modules" / "next" / "dist" / "bin"
    binaire.mkdir(parents=True)
    (binaire / "next").write_text("", encoding="utf-8")
    return Emplacement(racine=racine, front=lieu.resoudre_front(racine, {}), node="node")


class _Sortie:
    def __init__(self) -> None:
        self.lignes: list[str] = []

    def dire(self, message: str = "") -> None:
        self.lignes.append(message)

    def alerter(self, message: str) -> None:
        self.lignes.append(message)


def _demarrer_jusqu_au_preflight(
    tmp_path: Path, environ: dict[str, str], reprise: object
) -> tuple[list[str], _Sortie]:
    """Joue `demarrer` jusqu'au préflight, qu'on fait échouer : rien d'autre ne démarre."""
    ordre: list[str] = []
    sortie = _Sortie()

    def preflight() -> int:
        ordre.append("preflight")
        return 1

    def reprendre_(env: dict[str, str]) -> Bilan:
        ordre.append("reprise")
        return reprise(env) if callable(reprise) else Bilan()

    code = lanceur.demarrer(
        lanceur.Options(navigateur=False),
        emplacement=_emplacement_servable(tmp_path),
        systeme=None,  # type: ignore[arg-type] — le préflight refuse avant tout appel
        sortie=sortie,  # type: ignore[arg-type]
        environ=environ,
        preflight=preflight,
        reprise=reprendre_,
    )
    assert code == 1
    return ordre, sortie


def test_installe_la_reprise_passe_avant_l_api(tmp_path: Path) -> None:
    bilan = Bilan(reprises=("projets/projets.json",), cible=Path("C:/cible"), source=Path("s"))
    ordre, sortie = _demarrer_jusqu_au_preflight(tmp_path, dict(INSTALLE), lambda _: bilan)
    assert ordre == ["reprise", "preflight"]
    assert any("1 fichier(s) repris" in ligne for ligne in sortie.lignes)


def test_en_clone_aucune_reprise(tmp_path: Path) -> None:
    ordre, _ = _demarrer_jusqu_au_preflight(tmp_path, {}, None)
    assert ordre == ["preflight"]


def test_une_reprise_en_panne_se_dit_et_n_empeche_pas_de_demarrer(tmp_path: Path) -> None:
    def panne(_: dict[str, str]) -> Bilan:
        raise OSError("disque plein")

    ordre, sortie = _demarrer_jusqu_au_preflight(tmp_path, dict(INSTALLE), panne)
    assert ordre == ["reprise", "preflight"]
    assert any("disque plein" in ligne and "prochain démarrage" in ligne for ligne in sortie.lignes)


# =================================================================================================
# ⑥ La reprise d'une 0.1.0
# =================================================================================================


@pytest.fixture
def poste(tmp_path: Path) -> dict[str, Path]:
    """Une copie d'installeur (`source`), une configuration vide, et le `core/` du paquet."""
    livree = tmp_path / "livree"
    (livree / "permissions").mkdir(parents=True)
    (livree / "permissions" / "qa.json").write_text('{"allow": []}\n', encoding="utf-8")
    (livree / "projets").mkdir()
    (livree / "projets" / "README.md").write_text("# nouveau README\n", encoding="utf-8")

    source = tmp_path / "reprise"
    anciens = source / "core"
    for dossier in ("projets", "reglages", "permissions", "secrets", "chat"):
        (anciens / dossier).mkdir(parents=True)
    (anciens / "projets" / "README.md").write_text("# ancien README\n", encoding="utf-8")
    (anciens / "projets" / ".gitignore").write_text("*\n", encoding="utf-8")
    (anciens / "projets" / "prj-1.json").write_text('{"id": "prj-1"}\n', encoding="utf-8")
    (anciens / "reglages" / "fournisseur.json").write_text('{"f": 1}\n', encoding="utf-8")
    (anciens / "permissions" / "qa.json").write_text('{"allow": []}\n', encoding="utf-8")
    (anciens / "permissions" / "dev.json").write_text('{"allow": ["Read"]}\n', encoding="utf-8")
    (anciens / "secrets" / ".cle").write_text("cle-ancienne\n", encoding="utf-8")
    (anciens / "secrets" / "qa.json").write_text('{"chiffre": "x"}\n', encoding="utf-8")
    (anciens / "chat" / "fils" / "f1.jsonl").parent.mkdir(parents=True)
    (anciens / "chat" / "fils" / "f1.jsonl").write_text("{}\n", encoding="utf-8")
    return {"source": source, "cible": tmp_path / "configuration", "livree": livree}


def _reprendre(poste: dict[str, Path], **options: Path | None) -> Bilan:
    return reprendre(poste["source"], poste["cible"], poste["livree"], **options)


def test_la_reprise_range_les_donnees_et_laisse_le_paquet(poste: dict[str, Path]) -> None:
    bilan = _reprendre(poste)
    assert sorted(bilan.reprises) == [
        "chat/fils/f1.jsonl",
        "permissions/dev.json",
        "projets/prj-1.json",
        "reglages/fournisseur.json",
        "secrets/.cle",
        "secrets/qa.json",
    ]
    assert bilan.conservees == ()
    cible = poste["cible"]
    assert (cible / "projets" / "prj-1.json").read_text(encoding="utf-8") == '{"id": "prj-1"}\n'
    assert not (cible / "projets" / "README.md").exists()
    assert not (cible / "permissions" / "qa.json").exists(), "identique au paquet : pas une donnée"
    temoin = json.loads((poste["source"] / TEMOIN).read_text(encoding="utf-8"))
    assert sorted(temoin["reprises"]) == sorted(bilan.reprises)
    assert (poste["source"] / "core" / "projets" / "prj-1.json").is_file(), "la copie reste"


def test_la_reprise_n_a_lieu_qu_une_fois(poste: dict[str, Path]) -> None:
    _reprendre(poste)
    nouveau = poste["source"] / "core" / "projets" / "prj-2.json"
    nouveau.write_text('{"id": "prj-2"}\n', encoding="utf-8")

    seconde = _reprendre(poste)

    assert seconde.deja_faite and seconde.rien
    assert not (poste["cible"] / "projets" / "prj-2.json").exists()


def test_la_reprise_n_ecrase_rien_et_le_dit(poste: dict[str, Path]) -> None:
    deja = poste["cible"] / "projets" / "prj-1.json"
    deja.parent.mkdir(parents=True)
    deja.write_text('{"id": "prj-1", "nom": "réglé depuis"}\n', encoding="utf-8")

    bilan = _reprendre(poste)

    assert "projets/prj-1.json" in bilan.conservees
    assert "projets/prj-1.json" not in bilan.reprises
    assert "réglé depuis" in deja.read_text(encoding="utf-8")


def test_le_coffre_se_reprend_entier_ou_pas_du_tout(poste: dict[str, Path]) -> None:
    coffre = poste["cible"] / "secrets"
    coffre.mkdir(parents=True)
    (coffre / ".cle").write_text("cle-du-poste\n", encoding="utf-8")

    bilan = _reprendre(poste)

    assert set(bilan.conservees) == {"secrets/.cle", "secrets/qa.json"}
    assert not (coffre / "qa.json").exists(), "un secret chiffré par une autre clé serait perdu"
    assert (coffre / ".cle").read_text(encoding="utf-8") == "cle-du-poste\n"


def test_une_reprise_interrompue_se_rejoue_sans_doublon(
    poste: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from maestro.lanceur import reprise as module

    vraie = module._copier
    copies: list[Path] = []

    def copier_puis_tomber(fichier: Path, destination: Path) -> None:
        if copies:
            raise OSError("fichier verrouillé")
        copies.append(destination)
        vraie(fichier, destination)

    monkeypatch.setattr(module, "_copier", copier_puis_tomber)
    with pytest.raises(OSError):
        _reprendre(poste)
    assert not (poste["source"] / TEMOIN).exists()

    monkeypatch.setattr(module, "_copier", vraie)
    bilan = _reprendre(poste)

    assert bilan.conservees == ()
    assert len(bilan.reprises) == 5
    assert (poste["source"] / TEMOIN).is_file()


def test_les_reglages_ajoutes_au_env_de_l_ancienne_installation_passent_dans_le_sien(
    poste: dict[str, Path],
) -> None:
    (poste["source"] / ".env").write_text(
        "MAESTRO_PERSISTANCE=sqlite\n"
        "MAESTRO_ISOLATION=bac-a-sable\n"
        'MAESTRO_ESSAI="avec \\"guillemets\\" et # dièse"\n'
        "MAESTRO_DEJA=ancien\n",
        encoding="utf-8",
    )
    du_produit = poste["livree"].parent / "produit.env"
    du_produit.write_text("MAESTRO_PERSISTANCE=sqlite\nMAESTRO_INSTALLATION=1\n", encoding="utf-8")
    de_la_personne = poste["cible"] / ".env"
    de_la_personne.parent.mkdir(parents=True)
    de_la_personne.write_text("MAESTRO_DEJA=sien", encoding="utf-8")

    bilan = _reprendre(poste, env_du_produit=du_produit, env_de_la_personne=de_la_personne)

    assert bilan.reglages == ("MAESTRO_ISOLATION", "MAESTRO_ESSAI")
    relues = dotenv_values(de_la_personne)
    assert relues["MAESTRO_DEJA"] == "sien"
    assert relues["MAESTRO_ISOLATION"] == "bac-a-sable"
    assert relues["MAESTRO_ESSAI"] == 'avec "guillemets" et # dièse'
    assert "MAESTRO_PERSISTANCE" not in relues
    assert ENTETE_ENV in de_la_personne.read_text(encoding="utf-8")


def test_sans_copie_la_reprise_pose_son_temoin_et_ne_dit_rien(
    installe: Path,
) -> None:
    """Une installation neuve : rien à reprendre, et une copie posée plus tard est ignorée."""
    from maestro.lanceur.reprise import reprendre_du_poste

    bilan = reprendre_du_poste(INSTALLE)
    assert bilan.rien and not bilan.deja_faite
    assert (reprise_du_poste() / TEMOIN).is_file()
    assert reprendre_du_poste(INSTALLE).deja_faite
