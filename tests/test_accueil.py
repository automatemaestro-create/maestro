"""Le premier lancement d'un poste (#642) : `/api/accueil` et son dépôt.

Ce qui est gardé ici est ce que le parcours promet côté API (C3 de #642) : il
se montre à un poste neuf, il ne se rejoue pas une fois terminé — après un
redémarrage de l'API compris —, et un poste qui a déjà des projets n'y entre
jamais. Le parcours lui-même (ses étapes, leurs épreuves) se lit sur les routes
du fournisseur par défaut et du répertoire des projets, gardées par leurs
propres suites ; l'écran est gardé par `apps/web/tests/premier-lancement.test.tsx`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.accueil import FICHIER, DepotAccueil, ServiceAccueil
from maestro.controltower.projets import ServiceProjets
from maestro.projets.store import ProjetStore


class Poste:
    """Un poste décrit : son dépôt de réglages et le nombre de projets qu'il déclare."""

    def __init__(self, racine: Path) -> None:
        self.depot = DepotAccueil(racine / "reglages")
        self.projets = 0
        self.service = ServiceAccueil(self.depot, lambda: self.projets)

    def client(self) -> TestClient:
        app = create_app(bus=InMemoryEventBus(), state=ControlTowerState(), accueil=self.service)
        return TestClient(app)


@pytest.fixture()
def poste(tmp_path: Path) -> Poste:
    return Poste(tmp_path)


@pytest.fixture()
def client(poste: Poste) -> Iterator[TestClient]:
    with poste.client() as client:
        yield client


def test_un_poste_neuf_est_a_son_premier_lancement(client: TestClient) -> None:
    reponse = client.get("/api/accueil")

    assert reponse.status_code == 200
    assert reponse.json() == {"a_faire": True, "termine_le": None}


def test_terminer_clot_le_parcours_et_le_note_dans_le_depot_du_poste(
    client: TestClient, poste: Poste
) -> None:
    reponse = client.post("/api/accueil/terminer")

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["a_faire"] is False
    assert corps["termine_le"] is not None
    assert json.loads(poste.depot.chemin.read_text(encoding="utf-8")) == {
        "termine_le": corps["termine_le"]
    }
    assert client.get("/api/accueil").json() == corps


def test_terminer_deux_fois_garde_la_date_ou_le_parcours_a_vraiment_fini(poste: Poste) -> None:
    premiere = poste.depot.terminer(datetime(2026, 11, 11, 9, 0, tzinfo=UTC))

    seconde = poste.depot.terminer(datetime(2026, 11, 12, 9, 0, tzinfo=UTC))

    assert seconde == premiere == "2026-11-11T09:00:00+00:00"


def test_le_parcours_termine_ne_se_rejoue_pas_au_redemarrage(poste: Poste) -> None:
    with poste.client() as client:
        client.post("/api/accueil/terminer")

    # Une API neuve sur le même poste : ce qui la décide est le fichier, pas la mémoire.
    relance = Poste(poste.depot.chemin.parents[1])
    with relance.client() as client:
        assert client.get("/api/accueil").json()["a_faire"] is False


def test_un_poste_qui_declare_deja_un_projet_n_y_entre_jamais(
    client: TestClient, poste: Poste
) -> None:
    poste.projets = 1

    assert client.get("/api/accueil").json() == {"a_faire": False, "termine_le": None}
    assert not poste.depot.chemin.exists()


def test_un_poste_termine_qui_retire_ses_projets_retrouve_la_creation_pas_le_parcours(
    client: TestClient, poste: Poste
) -> None:
    poste.projets = 1
    client.post("/api/accueil/terminer")
    poste.projets = 0

    assert client.get("/api/accueil").json()["a_faire"] is False


@pytest.mark.parametrize("contenu", ["{pas du json", "[]", '{"termine_le": ""}', '{"autre": 1}'])
def test_un_fichier_abime_se_lit_comme_un_parcours_jamais_termine(
    client: TestClient, poste: Poste, contenu: str
) -> None:
    poste.depot.chemin.parent.mkdir(parents=True)
    poste.depot.chemin.write_text(contenu, encoding="utf-8")

    assert client.get("/api/accueil").json() == {"a_faire": True, "termine_le": None}


def test_une_ecriture_refusee_rend_503_et_ne_note_rien(tmp_path: Path) -> None:
    # Le dépôt des réglages est un fichier : rien ne peut s'écrire dessous.
    bouche = tmp_path / "reglages"
    bouche.write_text("", encoding="utf-8")
    service = ServiceAccueil(DepotAccueil(bouche), lambda: 0)
    app = create_app(bus=InMemoryEventBus(), state=ControlTowerState(), accueil=service)

    with TestClient(app) as client:
        reponse = client.post("/api/accueil/terminer")
        assert reponse.status_code == 503
        assert "premier lancement" in reponse.json()["detail"]
        assert client.get("/api/accueil").json()["a_faire"] is True


@pytest.fixture()
def maison(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier utilisateur factice : sous Windows, `tmp_path` vit sous AppData,
    que la validation de racine refuse à raison (même isolation que #221/#223)."""
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


def test_par_defaut_le_parcours_se_juge_sur_les_projets_declares_et_les_reglages_du_poste(
    tmp_path: Path, maison: Path
) -> None:
    """Sans `accueil` injecté : le dépôt des réglages du poste et les projets du service."""
    projets = ServiceProjets(ProjetStore(tmp_path / "depot"))
    app = create_app(bus=InMemoryEventBus(), state=ControlTowerState(), projets=projets)

    with TestClient(app) as client:
        assert client.get("/api/accueil").json()["a_faire"] is True

        (maison / "kombucha").mkdir()
        projets.creer("kombucha", str(maison / "kombucha"))
        assert client.get("/api/accueil").json()["a_faire"] is False

        client.post("/api/accueil/terminer")

    # Le dépôt du poste est celui que `tests/conftest.py` isole (`MAESTRO_REGLAGES_DIR`).
    assert (Path(os.environ["MAESTRO_REGLAGES_DIR"]) / FICHIER).is_file()
