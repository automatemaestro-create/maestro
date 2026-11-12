"""Ce que l'installation fait tourner, servi par l'API (#1622, `maestro.installation`).

Quatre questions, dans l'ordre où l'écran les pose :

1. **quelle version de Maestro tourne ici** — celle que l'assemblage a écrite, ou, dans un
   clone, celle que la source du paquet porte ; illisible, elle le dit avec son relevé. Installé
   ou clone, c'est le réglage du paquet qui le dit, jamais un `.git` (#1633) ;
2. **quel CLI `claude` il lance** — celui que le SDK embarque, sinon celui du `PATH`, et la
   provenance le dit : c'est elle qui signale un CLI pris hors de l'installation ;
3. **quelle version ce CLI a dite** — lue sur le binaire, retenue par son identité, jamais
   devinée ;
4. **ce que la route en sert.**

Le poste est décrit (dossier de SDK, `PATH`, lecteur de version), jamais lu : le seul binaire
réellement lancé est l'interpréteur de la suite, qui sait dire sa version.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import create_app
from maestro.emplacements import VARIABLE_INSTALLATION, InstallationIllisible
from maestro.installation import (
    FICHIER_VERSION,
    SOURCE_DE_LA_VERSION,
    LecteurInstallation,
    VersionIllisible,
    cli_claude_resolu,
    ecrire_version,
    forme_du_produit,
    nom_du_cli,
    version_du_depot,
    version_du_produit,
)
from maestro.providers import claude as claude_mod

#: Le réglage que le paquet pose (`resources/produit/.env`) : le produit se sait installé.
INSTALLE = {VARIABLE_INSTALLATION: "1"}

#: Sans réglage : un clone du dépôt.
CLONE: dict[str, str] = {}


def _clone(racine: Path, version: str | None = "1.2.3", *, git: bool = True) -> Path:
    """Un clone : la source de la version (`apps/desktop/package.json`), et son `.git`.

    `git=False` : une copie du dépôt qui n'en porte pas (une archive) — un clone tout de même.
    """
    racine.mkdir(parents=True, exist_ok=True)
    if git:
        (racine / ".git").mkdir()
    source = racine / SOURCE_DE_LA_VERSION
    source.parent.mkdir(parents=True)
    paquet: dict[str, str] = {"name": "desktop"}
    if version is not None:
        paquet["version"] = version
    source.write_text(json.dumps(paquet), encoding="utf-8")
    return racine


def _installation(racine: Path, version: str | None = "1.2.3", *, git: bool = False) -> Path:
    """Un produit installé : `version.json` s'il a été écrit, et un `.git` qui traînerait."""
    racine.mkdir(parents=True, exist_ok=True)
    if git:
        (racine / ".git").mkdir()
    if version is not None:
        (racine / FICHIER_VERSION).write_text(json.dumps({"version": version}), encoding="utf-8")
    return racine


def _sdk(racine: Path, *, embarque: bool) -> Path:
    """Le dossier d'un paquet `claude_agent_sdk`, avec ou sans son CLI embarqué."""
    dossier = racine / "claude_agent_sdk"
    (dossier / "_bundled").mkdir(parents=True)
    if embarque:
        (dossier / "_bundled" / nom_du_cli()).write_bytes(b"cli embarque")
    return dossier


def _aucun(_nom: str) -> str | None:
    return None


# --- ① La version du produit ------------------------------------------------------------


def test_la_forme_se_lit_sur_le_reglage_que_le_paquet_pose() -> None:
    assert forme_du_produit(CLONE) == "depot"
    assert forme_du_produit({VARIABLE_INSTALLATION: ""}) == "depot"
    assert forme_du_produit(INSTALLE) == "installation"


def test_un_reglage_illisible_n_est_rabattu_sur_aucune_forme() -> None:
    with pytest.raises(InstallationIllisible, match="oui"):
        forme_du_produit({VARIABLE_INSTALLATION: "oui"})


def test_la_presence_d_un_git_ne_decide_pas_de_la_forme(tmp_path: Path) -> None:
    """#1633 : une archive du dépôt reste un clone, une installation qui porte un `.git` reste
    une installation — et chacune lit sa version là où sa forme le dit."""
    archive = LecteurInstallation(
        racine=_clone(tmp_path / "archive", "1.2.3", git=False),
        environnement=CLONE,
        sdk=_sdk(tmp_path / "a", embarque=False),
        resolveur=_aucun,
    ).lire()
    assert (archive.forme, archive.produit.version) == ("depot", "1.2.3")

    installee = LecteurInstallation(
        racine=_installation(tmp_path / "produit", "4.5.6", git=True),
        environnement=INSTALLE,
        sdk=_sdk(tmp_path / "b", embarque=False),
        resolveur=_aucun,
    ).lire()
    assert (installee.forme, installee.produit.version) == ("installation", "4.5.6")


def test_un_clone_lit_la_version_que_porterait_son_paquet(tmp_path: Path) -> None:
    assert version_du_produit(_clone(tmp_path), "depot").version == "1.2.3"


def test_une_installation_lit_la_version_ecrite_a_l_assemblage(tmp_path: Path) -> None:
    lue = version_du_produit(_installation(tmp_path, "4.5.6"), "installation")
    assert (lue.version, lue.releve) == ("4.5.6", "")


def test_une_installation_sans_version_ecrite_la_dit_illisible(tmp_path: Path) -> None:
    # Pas de repli sur une autre source : une installation qui ne dit pas ce qu'elle est le dit,
    # même si elle porte la source d'un clone.
    racine = _clone(_installation(tmp_path, version=None), "1.2.3", git=False)
    lue = version_du_produit(racine, "installation")
    assert lue.version is None
    assert FICHIER_VERSION in lue.releve


def test_une_version_ecrite_mais_vide_ne_passe_pas_pour_une_version(tmp_path: Path) -> None:
    racine = _installation(tmp_path, version=None)
    (racine / FICHIER_VERSION).write_text(json.dumps({"version": "  "}), encoding="utf-8")
    assert version_du_produit(racine, "installation").version is None
    (racine / FICHIER_VERSION).write_text("{pas du json", encoding="utf-8")
    assert "illisible" in version_du_produit(racine, "installation").releve


def test_un_clone_dont_la_source_ne_porte_pas_de_version_le_dit(tmp_path: Path) -> None:
    lue = version_du_produit(_clone(tmp_path, version=None), "depot")
    assert lue.version is None
    assert str(SOURCE_DE_LA_VERSION) in lue.releve
    with pytest.raises(VersionIllisible):
        version_du_depot(tmp_path)


def test_l_assemblage_ecrit_la_version_que_le_produit_installe_relit(tmp_path: Path) -> None:
    depot = _clone(tmp_path / "depot", "7.8.9")
    produit = _installation(tmp_path / "produit", version=None)

    assert ecrire_version(depot, produit) == "7.8.9"
    assert version_du_produit(produit, "installation").version == "7.8.9"


def test_la_source_de_la_version_est_celle_du_paquet_de_la_coque() -> None:
    """Une seule version pour le paquet et le produit : celle qu'electron-builder lit."""
    racine = Path(__file__).resolve().parents[1]
    paquet = json.loads((racine / "apps" / "desktop" / "package.json").read_text(encoding="utf-8"))
    assert version_du_depot(racine) == paquet["version"]


# --- ② Le CLI que le produit lance --------------------------------------------------------


def test_le_cli_embarque_par_le_sdk_passe_avant_celui_du_poste(tmp_path: Path) -> None:
    sdk = _sdk(tmp_path, embarque=True)
    provenance, chemin = cli_claude_resolu(sdk=sdk, resolveur=lambda _: "/poste/claude")
    assert provenance == "paquet"
    assert chemin == sdk / "_bundled" / nom_du_cli()


def test_sans_cli_embarque_celui_du_path_est_pris_et_dit_du_poste(tmp_path: Path) -> None:
    demandes: list[str] = []

    def resolveur(nom: str) -> str | None:
        demandes.append(nom)
        return "/poste/claude"

    provenance, chemin = cli_claude_resolu(sdk=_sdk(tmp_path, embarque=False), resolveur=resolveur)
    assert (provenance, chemin) == ("poste", Path("/poste/claude"))
    # Le nom que le SDK accepte : `claude.exe` sous Windows, jamais le `claude.cmd` de npm.
    assert demandes == [nom_du_cli()]


def test_sans_aucun_cli_la_provenance_est_absente(tmp_path: Path) -> None:
    assert cli_claude_resolu(sdk=_sdk(tmp_path, embarque=False), resolveur=_aucun) == (
        "absent",
        None,
    )


def test_le_fournisseur_lance_le_cli_que_l_installation_montre() -> None:
    """Une seule résolution : celle des Paramètres est celle que le confinement lance."""
    _, chemin = cli_claude_resolu()
    attendu = (str(chemin),) if chemin is not None else None
    assert claude_mod._commande_du_cli() == attendu


# --- ③ La version du CLI, lue sur le binaire ----------------------------------------------


def test_la_version_est_lue_sur_le_binaire_resolu_et_retenue(tmp_path: Path) -> None:
    sdk = _sdk(tmp_path, embarque=True)
    lus: list[str] = []

    def versionneur(chemin: str) -> str | None:
        lus.append(chemin)
        return "2.1.281"

    lecteur = LecteurInstallation(
        racine=_installation(tmp_path / "produit"),
        sdk=sdk,
        resolveur=_aucun,
        versionneur=versionneur,
    )
    assert lecteur.lire().cli.version == "2.1.281"
    assert lecteur.lire().cli.version == "2.1.281"
    # Rouvrir les Paramètres ne relance pas le CLI…
    assert lus == [str(sdk / "_bundled" / nom_du_cli())]

    # …mais un CLI remplacé est relu.
    (sdk / "_bundled" / nom_du_cli()).write_bytes(b"un autre cli, plus long")
    lecteur.lire()
    assert len(lus) == 2


def test_une_version_qui_ne_vient_pas_reste_inconnue_et_n_est_pas_retenue(tmp_path: Path) -> None:
    reponses = [None, "2.1.281"]
    lecteur = LecteurInstallation(
        racine=_installation(tmp_path / "produit"),
        sdk=_sdk(tmp_path, embarque=True),
        resolveur=_aucun,
        versionneur=lambda _: reponses.pop(0),
    )
    assert lecteur.lire().cli.version is None
    # L'échec n'est pas gardé : la lecture suivante peut réussir.
    assert lecteur.lire().cli.version == "2.1.281"


def test_la_version_d_un_vrai_binaire_se_lit_par_son_option_version(tmp_path: Path) -> None:
    """Le lecteur par défaut, sur un binaire réel : l'interpréteur de la suite, « au poste »."""
    lecteur = LecteurInstallation(
        racine=_clone(tmp_path / "clone"),
        sdk=_sdk(tmp_path, embarque=False),
        resolveur=lambda _: sys.executable,
    )
    cli = lecteur.lire().cli
    assert cli.provenance == "poste"
    assert cli.version == ".".join(str(n) for n in sys.version_info[:3])


# --- ④ La route ---------------------------------------------------------------------------


def test_la_route_sert_la_version_du_produit_et_le_cli_avec_sa_provenance(
    tmp_path: Path,
) -> None:
    sdk = _sdk(tmp_path, embarque=True)
    lecteur = LecteurInstallation(
        racine=_installation(tmp_path / "produit", "0.1.0"),
        environnement=INSTALLE,
        sdk=sdk,
        resolveur=_aucun,
        versionneur=lambda _: "2.1.281",
    )
    with TestClient(create_app(installation=lecteur)) as client:
        reponse = client.get("/api/installation")

    assert reponse.status_code == 200
    assert reponse.json() == {
        "forme": "installation",
        "produit": {"version": "0.1.0", "releve": ""},
        "cli_claude": {
            "provenance": "paquet",
            "chemin": (sdk / "_bundled" / nom_du_cli()).as_posix(),
            "version": "2.1.281",
        },
    }


def test_la_route_dit_un_cli_absent_sans_inventer_de_version(tmp_path: Path) -> None:
    lecteur = LecteurInstallation(
        racine=_clone(tmp_path / "clone"),
        environnement=CLONE,
        sdk=_sdk(tmp_path, embarque=False),
        resolveur=_aucun,
        versionneur=lambda _: pytest.fail("aucun binaire à lancer"),
    )
    with TestClient(create_app(installation=lecteur)) as client:
        charge = client.get("/api/installation").json()

    assert charge["forme"] == "depot"
    assert charge["cli_claude"] == {"provenance": "absent", "chemin": None, "version": None}
