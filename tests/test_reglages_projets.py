"""Tests du **répertoire des projets** : où naît un projet neuf (#1022, EF-38).

Les trois critères du ticket, dans cet ordre :

① le **réglage** (`maestro.projets.reglages`) : un défaut nommé `~/Maestro` — que
   `valider_racine` admet là où elle refuse le dossier personnel nu —, créé à la
   première utilisation et **dit** quand il l'est, stocké résolu, revenu au
   défaut par `None`, et un dépôt absent ou corrompu qui rend les défauts plutôt
   que de lever ;
② la **lecture et l'écriture par l'API** (`GET`/`PUT /api/projets/repertoire`) :
   un réglage refusé n'écrit rien, un répertoire devenu indéclarable revient en
   200 avec son motif, et le répertoire réglé devient explorable — un parent
   proposé que l'explorateur refuserait serait un cul-de-sac ;
③ ce que le réglage **ne fait pas** : il n'entre pas dans le dépôt des
   déclarations (`MAESTRO_PROJETS_DIR`), et déclarer un projet ne le touche pas.

Ni réseau, ni Redis : l'app FastAPI tourne sur le bus mémoire via le TestClient,
sur des dépôts jetables et un dossier personnel factice — sans quoi le `tmp_path`
de pytest, qui vit sous `AppData/Local/Temp` sous Windows, serait refusé à raison
mais pas pour la raison mesurée (même isolation qu'en #221).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.config import Settings
from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.projets import ServiceProjets
from maestro.projets import ProjetStore, RacineRefusee
from maestro.projets.reglages import (
    NOM_DEFAUT,
    ReglagesProjetsStore,
    repertoire_par_defaut,
)


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier personnel factice — même raison qu'en #221 et #223."""
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


@pytest.fixture()
def maison(_maison_isolee: Path) -> Path:
    """Le dossier personnel factice, sous un nom lisible en argument de test."""
    return _maison_isolee


@pytest.fixture()
def reglages(tmp_path: Path) -> ReglagesProjetsStore:
    """Un dépôt de réglages sur un dossier jetable."""
    return ReglagesProjetsStore(tmp_path / "reglages")


@pytest.fixture()
def atelier(tmp_path: Path) -> Path:
    """Un dossier explorable, hors du dossier personnel."""
    atelier = tmp_path / "atelier"
    atelier.mkdir()
    return atelier


@pytest.fixture()
def service(tmp_path: Path, reglages: ReglagesProjetsStore) -> ServiceProjets:
    """Le service sans restriction d'exploration — la configuration nominale."""
    return ServiceProjets(ProjetStore(tmp_path / "depot"), reglages=reglages)


@pytest.fixture()
def client(service: ServiceProjets) -> TestClient:
    """TestClient de l'app, branché sur ce service-là."""
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=service,
    )
    with TestClient(app) as client:
        yield client


# --- ① Le réglage : défaut nommé, création à la première utilisation ---------


def test_le_defaut_est_un_sous_dossier_nomme_du_dossier_personnel(maison: Path) -> None:
    """`~/Maestro`, jamais `~` : le dossier personnel nu est refusé par EF-38."""
    assert repertoire_par_defaut() == maison / NOM_DEFAUT
    # Le contre-test qui donne son sens au précédent : le dossier personnel nu,
    # lui, ne passerait pas — c'est la nuance sur laquelle le défaut repose.
    with pytest.raises(RacineRefusee) as refus:
        ReglagesProjetsStore(maison / "vide").ecrire(maison)
    assert refus.value.motif == "dossier-utilisateur-nu"


def test_sans_reglage_le_repertoire_est_le_defaut_et_n_existe_pas_encore(
    reglages: ReglagesProjetsStore, maison: Path
) -> None:
    """Lire le réglage ne crée rien : c'est `creer=True` qui le fait, et lui seul."""
    with pytest.raises(RacineRefusee) as refus:
        reglages.resoudre()
    assert refus.value.motif == "dossier-absent"
    assert not (maison / NOM_DEFAUT).exists()


def test_la_premiere_utilisation_cree_le_dossier_et_le_dit(
    reglages: ReglagesProjetsStore, maison: Path
) -> None:
    """`cree` distingue « je viens de le créer » de « il était là » — jamais en silence."""
    chemin, par_defaut, cree = reglages.resoudre(creer=True)
    assert chemin == (maison / NOM_DEFAUT).resolve()
    assert (par_defaut, cree) == (True, True)
    # Idempotent, et la seconde fois ne prétend plus l'avoir créé.
    _, _, encore = reglages.resoudre(creer=True)
    assert encore is False


def test_un_repertoire_regle_est_stocke_resolu(
    reglages: ReglagesProjetsStore, atelier: Path
) -> None:
    """C'est le chemin **résolu** qui est stocké, jamais la saisie (EF-38)."""
    cible = atelier / "ailleurs"
    cible.mkdir()
    ecrit = reglages.ecrire(atelier / "ailleurs" / "." / ".." / "ailleurs")
    assert ecrit.repertoire == cible.resolve().as_posix()
    assert ecrit.modifie_le != ""
    chemin, par_defaut, _ = reglages.resoudre()
    assert chemin == cible.resolve()
    assert par_defaut is False


def test_regler_cree_le_dossier_qui_manque(
    reglages: ReglagesProjetsStore, atelier: Path
) -> None:
    """Un réglage posé est toujours un dossier déclarable, jamais une intention."""
    cible = atelier / "a-creer"
    reglages.ecrire(cible)
    assert cible.is_dir()


def test_revenir_au_defaut_efface_le_reglage(
    reglages: ReglagesProjetsStore, atelier: Path, maison: Path
) -> None:
    """`None` ne veut pas dire « aucun répertoire » mais « celui que Maestro propose »."""
    (atelier / "ailleurs").mkdir()
    reglages.ecrire(atelier / "ailleurs")
    reglages.ecrire(None)
    chemin, par_defaut, _ = reglages.resoudre(creer=True)
    assert (chemin, par_defaut) == ((maison / NOM_DEFAUT).resolve(), True)


def test_un_reglage_refuse_n_ecrit_rien(
    reglages: ReglagesProjetsStore, atelier: Path, maison: Path
) -> None:
    """Le réglage précédent reste en place : refuser n'est pas remettre à zéro."""
    (atelier / "bon").mkdir()
    reglages.ecrire(atelier / "bon")
    with pytest.raises(RacineRefusee):
        reglages.ecrire(maison)  # dossier personnel nu
    assert reglages.lire().repertoire == (atelier / "bon").resolve().as_posix()


def test_un_depot_corrompu_rend_les_defauts(
    reglages: ReglagesProjetsStore, maison: Path
) -> None:
    """Un réglage qu'on ne sait pas lire n'empêche pas de déclarer un projet."""
    reglages.racine.mkdir(parents=True, exist_ok=True)
    (reglages.racine / "projets.json").write_text("{ pas du json", encoding="utf-8")
    assert reglages.lire().repertoire is None
    chemin, par_defaut, _ = reglages.resoudre(creer=True)
    assert (chemin, par_defaut) == ((maison / NOM_DEFAUT).resolve(), True)


def test_le_depot_des_reglages_vient_de_l_environnement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`MAESTRO_REGLAGES_DIR`, sinon `core/reglages/` du dépôt — jamais `core/projets/`."""
    monkeypatch.setenv("MAESTRO_REGLAGES_DIR", str(tmp_path / "ailleurs"))
    assert ReglagesProjetsStore.default(Settings.from_env()).racine == tmp_path / "ailleurs"

    monkeypatch.delenv("MAESTRO_REGLAGES_DIR", raising=False)
    defaut = ReglagesProjetsStore.default(Settings.from_env()).racine
    assert defaut.name == "reglages"
    assert defaut.parent.name == "core"


# --- ② L'API : lire, poser, et rester explorable ----------------------------


def test_lire_le_repertoire_rend_le_defaut_et_le_cree(
    client: TestClient, maison: Path
) -> None:
    """La lecture crée le dossier — « créé à la première utilisation » — et le dit."""
    corps = client.get("/api/projets/repertoire").json()
    assert corps["par_defaut"] is True
    assert corps["cree"] is True
    assert corps["refus"] is None
    assert Path(corps["chemin"]) == (maison / NOM_DEFAUT).resolve()
    assert (maison / NOM_DEFAUT).is_dir()
    # Le second appel ne prétend plus l'avoir créé : `cree` est un fait, pas un état.
    assert client.get("/api/projets/repertoire").json()["cree"] is False


def test_poser_un_repertoire_le_relit(client: TestClient, atelier: Path) -> None:
    """`PUT` puis `GET` disent la même chose — un seul support de la vérité."""
    cible = atelier / "mes-projets"
    corps = client.put("/api/projets/repertoire", json={"chemin": str(cible)}).json()
    assert corps["par_defaut"] is False
    assert Path(corps["chemin"]) == cible.resolve()
    assert client.get("/api/projets/repertoire").json()["chemin"] == corps["chemin"]


def test_le_chemin_est_rendu_en_posix_comme_les_autres_racines(
    client: TestClient, atelier: Path
) -> None:
    """Un seul dossier, une seule écriture — sinon l'écran ne sait plus les reconnaître.

    L'explorateur rend ses chemins en POSIX (`_fiche_dossier`), et l'écran
    **compare** le dossier choisi au répertoire réglé pour savoir s'il est
    « hors » de lui. Deux écritures du même dossier lui faisaient annoncer le
    contraire de ce qu'il affichait (relecture visuelle de #1022).
    """
    cible = atelier / "mes-projets"
    pose = client.put("/api/projets/repertoire", json={"chemin": str(cible)}).json()
    assert pose["chemin"] == cible.resolve().as_posix()
    assert "\\" not in client.get("/api/projets/repertoire").json()["chemin"]
    # Et le défaut suit la même règle : `~/Maestro` n'échappe pas à la forme.
    defaut = client.put("/api/projets/repertoire", json={"chemin": None}).json()
    assert "\\" not in defaut["chemin"]
    # Le même dossier, énuméré par l'explorateur, s'écrit exactement pareil.
    vue = client.get("/api/projets/explorateur", params={"chemin": str(cible)}).json()
    assert vue["chemin"] == pose["chemin"]


def test_poser_null_revient_au_defaut(
    client: TestClient, atelier: Path, maison: Path
) -> None:
    """`chemin: null` revient au défaut — ce n'est pas « plus de répertoire »."""
    (atelier / "mes-projets").mkdir()
    client.put("/api/projets/repertoire", json={"chemin": str(atelier / "mes-projets")})
    corps = client.put("/api/projets/repertoire", json={"chemin": None}).json()
    assert corps["par_defaut"] is True
    assert Path(corps["chemin"]) == (maison / NOM_DEFAUT).resolve()


def test_un_repertoire_refuse_rend_un_motif_et_ne_change_rien(
    client: TestClient, atelier: Path, maison: Path
) -> None:
    """Le refus d'EF-38 traverse l'API motivé, et le réglage précédent tient."""
    (atelier / "bon").mkdir()
    client.put("/api/projets/repertoire", json={"chemin": str(atelier / "bon")})
    reponse = client.put("/api/projets/repertoire", json={"chemin": str(maison)})
    assert reponse.status_code == 422
    assert reponse.json()["detail"]["motif"] == "dossier-utilisateur-nu"
    assert (
        Path(client.get("/api/projets/repertoire").json()["chemin"])
        == (atelier / "bon").resolve()
    )


def test_un_repertoire_devenu_indisponible_rend_200_avec_son_motif(
    client: TestClient, atelier: Path
) -> None:
    """Un disque débranché n'empêche pas de déclarer un projet ailleurs."""
    cible = atelier / "sur-un-disque"
    client.put("/api/projets/repertoire", json={"chemin": str(cible)})
    cible.rmdir()
    cible.write_text("plus un dossier", encoding="utf-8")
    corps = client.get("/api/projets/repertoire").json()
    assert corps["existe"] is False
    assert corps["refus"]["motif"] == "pas-un-dossier"
    # Le chemin réglé reste rendu, et dans la même forme que d'habitude : on
    # corrige ce qu'on voit, et on doit le reconnaître.
    assert corps["chemin"] == cible.resolve().as_posix()


def test_le_repertoire_regle_est_un_point_d_entree_de_l_explorateur(
    client: TestClient, atelier: Path
) -> None:
    """Le premier endroit à regarder est celui d'où l'on vient (#1022)."""
    cible = atelier / "mes-projets"
    client.put("/api/projets/repertoire", json={"chemin": str(cible)})
    dossiers = client.get("/api/projets/explorateur").json()["dossiers"]
    par_origine = {d["origine"]: d["chemin"] for d in dossiers}
    assert par_origine["repertoire"] == cible.resolve().as_posix()
    assert dossiers[0]["origine"] == "repertoire"


def test_le_repertoire_regle_reste_explorable_malgre_une_restriction(
    tmp_path: Path, reglages: ReglagesProjetsStore, atelier: Path
) -> None:
    """Un parent prérempli que l'explorateur refuserait d'ouvrir serait un cul-de-sac.

    `MAESTRO_EXPLORATEUR_RACINES` reste une **restriction** : elle ne s'élargit
    pas d'elle-même. Mais le répertoire des projets est un dossier que l'écran
    **propose** — et proposer un dossier qu'on refusera au clic est pire que ne
    rien proposer.
    """
    hors_atelier = tmp_path / "hors-atelier"
    hors_atelier.mkdir()
    reglages.ecrire(hors_atelier)
    service = ServiceProjets(
        ProjetStore(tmp_path / "depot"),
        racines_exploration=(atelier,),
        reglages=reglages,
    )
    vue = service.explorer(str(hors_atelier))
    assert vue["chemin"] == hors_atelier.resolve().as_posix()


def test_un_repertoire_absent_n_est_pas_un_point_d_entree(
    client: TestClient, atelier: Path
) -> None:
    """Un point d'entrée qui refuserait au clic serait pire que son absence."""
    cible = atelier / "mes-projets"
    client.put("/api/projets/repertoire", json={"chemin": str(cible)})
    cible.rmdir()
    origines = {
        d["origine"] for d in client.get("/api/projets/explorateur").json()["dossiers"]
    }
    assert "repertoire" not in origines


# --- ③ Ce que le réglage ne fait pas ----------------------------------------


def test_le_reglage_ne_se_melange_pas_aux_declarations(
    client: TestClient, service: ServiceProjets, atelier: Path
) -> None:
    """Deux « racines » se croisent ici, et elles ne partagent pas un dossier.

    Le dépôt des déclarations (`MAESTRO_PROJETS_DIR`) ne contient que des
    `<id>.json` : un `projets.json` de réglages glissé dedans serait relu comme
    un projet illisible de plus.
    """
    cible = atelier / "mes-projets"
    client.put("/api/projets/repertoire", json={"chemin": str(cible)})
    declarations = service.store.racine
    assert declarations != service.reglages.racine
    assert not (declarations / "projets.json").exists()
    assert client.get("/api/projets").json() == []


def test_declarer_un_projet_ne_touche_pas_au_reglage(
    client: TestClient, reglages: ReglagesProjetsStore, atelier: Path
) -> None:
    """Choisir un autre parent pour **ce** projet ne règle rien (critère 2)."""
    client.put("/api/projets/repertoire", json={"chemin": str(atelier / "mes-projets")})
    avant = json.loads((reglages.racine / "projets.json").read_text(encoding="utf-8"))
    ailleurs = atelier / "ailleurs"
    ailleurs.mkdir()
    reponse = client.post(
        "/api/projets",
        json={"nom": "Dépensio", "racine": str(ailleurs / "depensio"), "origine": "nouveau"},
    )
    assert reponse.status_code == 201
    apres = json.loads((reglages.racine / "projets.json").read_text(encoding="utf-8"))
    assert apres == avant
