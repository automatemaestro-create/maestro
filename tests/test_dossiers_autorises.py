"""Tests des **dossiers autorisés depuis l'écran** (#1610, EF-38).

Un dossier que l'explorateur refuse parce qu'il sort des dossiers explorables
s'autorise depuis l'écran de déclaration, après confirmation, au lieu de
renvoyer à `MAESTRO_EXPLORATEUR_RACINES` et à un redémarrage. Les deux critères
du ticket, dans cet ordre :

① **le geste aboutit** : le refus dit que le dossier peut s'autoriser, autoriser
   l'écrit dans les réglages du poste (`maestro.projets.reglages`), l'explorateur
   l'ouvre dès l'appel suivant — aucun redémarrage —, l'autorisation **s'ajoute**
   à la frontière configurée, et elle se retire ;
② **il ne s'ouvre que là où il peut aboutir** : un refus pour un autre motif ne
   porte pas d'autorisation, un dossier que `valider_racine` refuserait ne
   s'autorise pas (le refus dit pourquoi), et un dossier autorisé n'ouvre
   aucune zone interdite — même écrit à la main dans le fichier.

Ni réseau, ni Redis : l'app FastAPI tourne sur le bus mémoire via le TestClient,
sur des dépôts jetables et un dossier personnel factice (même isolation qu'en
#221 : le `tmp_path` de pytest vit sous `AppData/Local/Temp` sous Windows).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.projets import ServiceProjets
from maestro.projets import ProjetStore, RacineRefusee
from maestro.projets.reglages import ReglagesProjets, ReglagesProjetsStore


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier personnel factice — même raison qu'en #221 et #223."""
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


@pytest.fixture()
def reglages(tmp_path: Path) -> ReglagesProjetsStore:
    """Un dépôt de réglages sur un dossier jetable — jamais celui du poste."""
    return ReglagesProjetsStore(tmp_path / "reglages")


@pytest.fixture()
def atelier(tmp_path: Path) -> Path:
    """Le seul dossier explorable configuré : la restriction de ces tests."""
    atelier = tmp_path / "atelier"
    atelier.mkdir()
    return atelier


@pytest.fixture()
def dehors(tmp_path: Path) -> Path:
    """Un dossier déclarable, mais hors de l'atelier."""
    dehors = tmp_path / "clients" / "acme"
    dehors.mkdir(parents=True)
    return dehors


def _client(tmp_path: Path, reglages: ReglagesProjetsStore, *racines: Path) -> TestClient:
    """L'app, service des projets borné à `racines`, réglages jetables."""
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=ServiceProjets(
            ProjetStore(tmp_path / "depot"),
            racines_exploration=racines,
            reglages=reglages,
        ),
    )
    return TestClient(app)


@pytest.fixture()
def client(tmp_path: Path, reglages: ReglagesProjetsStore, atelier: Path) -> TestClient:
    """TestClient de l'app, explorateur borné à l'atelier."""
    with _client(tmp_path, reglages, atelier) as client:
        yield client


def _explorer(client: TestClient, chemin: Path | str | None = None):  # noqa: ANN202 - Response
    params = {} if chemin is None else {"chemin": str(chemin)}
    return client.get("/api/projets/explorateur", params=params)


# --- ① Le geste aboutit -------------------------------------------------------


def test_le_refus_hors_frontiere_dit_que_le_dossier_s_autorise(
    client: TestClient, dehors: Path
) -> None:
    """Le refus porte le dossier en POSIX et l'absence de refus de `valider_racine`.

    Et il ne renvoie plus à une variable d'environnement ni à un redémarrage :
    c'était le cul-de-sac que le ticket retire.
    """
    reponse = _explorer(client, dehors)

    assert reponse.status_code == 403
    detail = reponse.json()["detail"]
    assert detail["motif"] == "hors-racines-explorables"
    assert detail["autorisation"] == {"chemin": dehors.resolve().as_posix(), "refus": None}
    assert "MAESTRO_EXPLORATEUR_RACINES" not in detail["message"]
    assert dehors.resolve().as_posix() in detail["message"]


def test_le_refus_hors_frontiere_se_lit_en_une_phrase(
    client: TestClient, dehors: Path
) -> None:
    """Le dossier, puis les dossiers explorables, dans une seule phrase (#1613).

    Le message écrivait « … : <dossier> — explorables : <liste>. », où le
    second fragment se lisait comme un adjectif orphelin sous le titre du
    bandeau.
    """
    message = _explorer(client, dehors).json()["detail"]["message"]

    assert message.startswith(f"{dehors.resolve().as_posix()} est hors des dossiers explorables")
    assert "— explorables" not in message
    assert message.count("explorables") == 1


def test_autoriser_ouvre_le_dossier_sans_redemarrage(
    client: TestClient, reglages: ReglagesProjetsStore, dehors: Path
) -> None:
    """Le même client, la même app : l'appel suivant voit l'autorisation."""
    pose = client.post("/api/projets/dossiers-autorises", json={"chemin": str(dehors)})

    assert pose.status_code == 200
    corps = pose.json()
    assert corps["chemin"] == dehors.resolve().as_posix()
    assert corps["dossiers"] == [{"chemin": dehors.resolve().as_posix(), "refus": None}]
    assert reglages.lire().dossiers_autorises == (dehors.resolve().as_posix(),)

    vue = _explorer(client, dehors)
    assert vue.status_code == 200
    assert vue.json()["chemin"] == dehors.resolve().as_posix()


def test_un_dossier_autorise_est_un_point_d_entree(
    client: TestClient, dehors: Path
) -> None:
    """Il rejoint la page d'entrée avec son origine, que l'écran nomme « dossier autorisé »."""
    client.post("/api/projets/dossiers-autorises", json={"chemin": str(dehors)})

    dossiers = _explorer(client).json()["dossiers"]

    assert {"chemin": dehors.resolve().as_posix(), "origine": "autorise"} in [
        {"chemin": d["chemin"], "origine": d["origine"]} for d in dossiers
    ]


def test_l_autorisation_s_ajoute_a_la_frontiere_sans_la_remplacer(
    client: TestClient, atelier: Path, dehors: Path
) -> None:
    """`MAESTRO_EXPLORATEUR_RACINES` (ici l'atelier) reste explorable après l'autorisation."""
    client.post("/api/projets/dossiers-autorises", json={"chemin": str(dehors)})

    assert _explorer(client, atelier).status_code == 200
    racines = _explorer(client, dehors).json()["racines"]
    assert atelier.resolve().as_posix() in racines
    assert dehors.resolve().as_posix() in racines


def test_retirer_une_autorisation_referme_le_dossier(
    client: TestClient, reglages: ReglagesProjetsStore, dehors: Path
) -> None:
    """Le retrait se fait depuis Paramètres › Projets : le dossier redevient refusé."""
    client.post("/api/projets/dossiers-autorises", json={"chemin": str(dehors)})
    chemin = client.get("/api/projets/dossiers-autorises").json()["dossiers"][0]["chemin"]

    retrait = client.delete("/api/projets/dossiers-autorises", params={"chemin": chemin})

    assert retrait.status_code == 200
    assert retrait.json() == {"dossiers": []}
    assert reglages.lire().dossiers_autorises == ()
    refus = _explorer(client, dehors)
    assert refus.status_code == 403
    assert refus.json()["detail"]["motif"] == "hors-racines-explorables"


def test_retirer_ce_qui_n_est_pas_autorise_est_un_404_motive(
    client: TestClient, dehors: Path
) -> None:
    retrait = client.delete("/api/projets/dossiers-autorises", params={"chemin": str(dehors)})

    assert retrait.status_code == 404
    assert retrait.json()["detail"]["motif"] == "autorisation-inconnue"


def test_autoriser_deux_fois_n_inscrit_qu_une_fois(
    reglages: ReglagesProjetsStore, dehors: Path
) -> None:
    reglages.autoriser(dehors)
    reglages.autoriser(dehors.as_posix())

    assert reglages.lire().dossiers_autorises == (dehors.resolve().as_posix(),)


def test_regler_le_repertoire_garde_les_dossiers_autorises(
    reglages: ReglagesProjetsStore, atelier: Path, dehors: Path
) -> None:
    """Les deux réglages partagent un fichier : écrire l'un n'efface pas l'autre."""
    reglages.autoriser(dehors)
    reglages.ecrire(atelier / "mes-projets")
    reglages.ecrire(None)

    assert reglages.lire().dossiers_autorises == (dehors.resolve().as_posix(),)


def test_une_autorisation_devenue_invalide_se_dit_et_se_retire(
    client: TestClient, dehors: Path
) -> None:
    """Un disque débranché : l'autorisation ne sert plus de frontière, l'écran le montre."""
    client.post("/api/projets/dossiers-autorises", json={"chemin": str(dehors)})
    chemin = dehors.resolve().as_posix()
    dehors.rmdir()

    (entree,) = client.get("/api/projets/dossiers-autorises").json()["dossiers"]
    assert entree["chemin"] == chemin
    assert entree["refus"]["motif"] == "dossier-absent"

    retrait = client.delete("/api/projets/dossiers-autorises", params={"chemin": chemin})
    assert retrait.status_code == 200


def test_sans_aucun_dossier_explorable_un_chemin_s_autorise(
    tmp_path: Path, reglages: ReglagesProjetsStore, dehors: Path
) -> None:
    """« Aucun dossier explorable » n'est plus un cul-de-sac : un chemin donné s'autorise."""
    with _client(tmp_path, reglages, tmp_path / "disparu") as client:
        assert _explorer(client).json()["detail"]["motif"] == "aucune-racine-explorable"
        refus = _explorer(client, dehors).json()["detail"]
        assert refus["motif"] == "hors-racines-explorables"
        assert refus["autorisation"]["refus"] is None

        client.post("/api/projets/dossiers-autorises", json={"chemin": str(dehors)})

        assert _explorer(client).status_code == 200
        assert _explorer(client, dehors).status_code == 200


def test_un_fichier_abime_garde_les_entrees_lisibles(tmp_path: Path) -> None:
    lu = ReglagesProjets.from_dict({"dossiers_autorises": ["D:/clients", 3, "", None, " E:/x "]})

    assert lu.dossiers_autorises == ("D:/clients", "E:/x")


# --- ② Il ne s'ouvre que là où il peut aboutir ---------------------------------


def test_un_refus_pour_un_autre_motif_ne_porte_pas_d_autorisation(
    tmp_path: Path, reglages: ReglagesProjetsStore, _maison_isolee: Path
) -> None:
    """Une zone protégée sous une racine explorable : refusée, avec son motif, sans geste."""
    ssh = _maison_isolee / ".ssh"
    ssh.mkdir()
    with _client(tmp_path, reglages, _maison_isolee) as client:
        detail = _explorer(client, ssh).json()["detail"]

    assert detail["motif"] == "chemin-sensible"
    assert "autorisation" not in detail


def test_un_dossier_hors_frontiere_mais_indeclarable_dit_pourquoi(
    client: TestClient, _maison_isolee: Path
) -> None:
    """Le dossier personnel nu, hors de l'atelier : le refus porte le motif de `valider_racine`."""
    detail = _explorer(client, _maison_isolee).json()["detail"]

    assert detail["motif"] == "hors-racines-explorables"
    assert detail["autorisation"]["refus"]["motif"] == "dossier-utilisateur-nu"


@pytest.mark.parametrize(
    ("dossier", "motif"),
    [
        (lambda maison, tmp: maison / ".ssh", "chemin-sensible"),
        (lambda maison, tmp: maison, "dossier-utilisateur-nu"),
        (lambda maison, tmp: tmp, "au-dessus-du-dossier-utilisateur"),
        (lambda maison, tmp: tmp / "absent", "dossier-absent"),
    ],
    ids=["zone-protegee", "dossier-utilisateur-nu", "au-dessus", "absent"],
)
def test_autoriser_passe_par_valider_racine_et_n_ecrit_rien_sur_un_refus(
    client: TestClient,
    reglages: ReglagesProjetsStore,
    _maison_isolee: Path,
    tmp_path: Path,
    dossier,  # noqa: ANN001 - fabrique paramétrée
    motif: str,
) -> None:
    cible = dossier(_maison_isolee, tmp_path)
    if motif == "chemin-sensible":
        cible.mkdir()

    reponse = client.post("/api/projets/dossiers-autorises", json={"chemin": str(cible)})

    assert reponse.status_code in (403, 404, 422)
    assert reponse.json()["detail"]["motif"] == motif
    assert not (reglages.racine / "projets.json").exists()


def test_une_autorisation_n_ouvre_aucune_zone_interdite(
    tmp_path: Path, reglages: ReglagesProjetsStore, _maison_isolee: Path
) -> None:
    """Même écrite à la main dans le fichier, une autorisation est rejugée par `valider_racine`.

    Le service ne fait pas confiance au fichier : `~/.ssh` glissé dans les
    dossiers autorisés n'entre ni dans la frontière ni dans les points d'entrée,
    et l'explorateur continue de le refuser pour son vrai motif.
    """
    ssh = _maison_isolee / ".ssh"
    ssh.mkdir()
    reglages.racine.mkdir(parents=True)
    (reglages.racine / "projets.json").write_text(
        json.dumps({"repertoire": None, "dossiers_autorises": [ssh.as_posix()]}),
        encoding="utf-8",
    )
    atelier = tmp_path / "atelier"
    atelier.mkdir()
    service = ServiceProjets(
        ProjetStore(tmp_path / "depot"), racines_exploration=(atelier,), reglages=reglages
    )

    assert ssh.resolve() not in service.racines()
    assert "autorise" not in {origine for _, origine in service.points_entree()}
    with pytest.raises(RacineRefusee) as refus:
        service.explorer(str(ssh))
    assert refus.value.motif == "chemin-sensible"
