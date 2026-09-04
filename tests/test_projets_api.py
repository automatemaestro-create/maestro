"""Tests d'API des projets : CRUD `/api/projets` et explorateur de dossiers (#223).

Lot final de la Phase 7 (#220, parent #219) — le lot 3 avait livré sans tests
(convention de découpage, [docs/10 §5.1](../docs/10-workflow-git.md)). Couvre le
second critère du ticket : les routes, leurs formes JSON et surtout leurs
**refus motivés**.

① le **CRUD** : déclaration (racine canonicalisée, VCS constaté sur le disque et
   jamais déclaré par le client), lecture, remplacement intégral, suppression
   qui n'efface que la déclaration — et un fichier de dépôt illisible, sauté du
   listing mais expliqué par le détail ;
② l'**explorateur** : le point d'entrée (les racines), l'énumération d'un
   dossier (marqueur Git, projet déjà déclaré), la frontière `parent: null`, la
   troncature annoncée, et la route qui n'est pas avalée par la capture
   `/api/projets/{id}` ;
③ les **refus**, qui sont le sujet : chaque frontière rend son `motif` et son
   code, et le code vient du motif — jamais de la route, si bien qu'une même
   frontière se lit pareil à la déclaration et à l'exploration. 403 « je refuse
   de regarder là » (hors racines explorables, zone sensible), 404 « ce chemin
   n'existe pas », 422 « la saisie est en cause » (dossier utilisateur nu,
   `../..` qui remonte trop haut, chemin relatif, racine déjà prise).
   **Jamais une liste vide, jamais un 500** : confondre « ce dossier n'a pas de
   sous-dossier » et « je refuse de regarder là » rend un explorateur
   inutilisable ;
⑤ les **racines élargies et les points d'entrée** (#278), ajoutés par le lot 6
   de #276 (#282) — le lot 2 avait lui aussi livré sans tests. Ce que #278
   change n'est pas l'explorateur mais **jusqu'où** il a le droit de regarder
   (la frontière, élargie aux volumes du poste) et **par où** il propose de
   commencer (les points d'entrée, avec leur origine) : deux notions qui se
   confondaient avant lui, et dont la séparation est tout le lot. L'invariant
   qui les tient : élargir l'explorable n'ouvre **aucune** brèche dans EF-38, et
   `MAESTRO_EXPLORATEUR_RACINES` reste une **restriction** ;
⑥ la **mise sous Git** (#855) — `POST /api/projets/{id}/versionner`, le seul
   geste du service qui écrive dans le dossier de l'utilisateur, et qui ne fait
   qu'appeler le verbe de #704. Ce qui s'y mesure : la fiche rendue est **relue**
   (le `vcs` constaté, la base qui résout, le `.gitignore` du projet respecté par
   le premier commit), un projet déjà versionné est rendu **tel quel** sans
   qu'aucun `git` soit appelé (un `.git` minimal suffit à le prouver), la route
   est **sans corps** (un `vcs` envoyé n'est ni lu ni stocké — EF-38), et chaque
   refus porte son **motif** et son code — 404 `projet-inconnu`, 422
   `dossier-absent` et `commit-refuse` (la racine dans l'état d'avant, `.git` né
   compris), 409 `depot-englobant`, 503 `git-indisponible` —, **jamais un 500**.
   Les tests qui posent un vrai dépôt exigent `git` (sauté sinon, erreur franche
   en CI par le conftest) et coupent la configuration globale du poste (#333).

Ni réseau, ni Redis, ni Docker : l'app FastAPI tourne sur le bus mémoire via le
TestClient de Starlette, sur un dépôt de projets jetable et des racines
explorables fixées explicitement — seule façon de ne pas dépendre du poste qui
joue la suite.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.projets import ServiceProjets
from maestro.projets import MESSAGE_PREMIER_COMMIT, ProjetStore, versionnement


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier utilisateur factice — même raison qu'en #221 (`tests/test_projets.py`).

    Sous Windows, le `tmp_path` de pytest vit dans `AppData/Local/Temp`, que la
    validation refuse à raison : sans cette isolation, tous les projets déclarés
    dans `tmp_path` seraient refusés pour une bonne raison, mais pas celle que
    ces tests mesurent.
    """
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


@pytest.fixture()
def maison(_maison_isolee: Path) -> Path:
    """Le dossier utilisateur factice, sous un nom lisible en argument de test.

    L'isolation est `autouse` (tout ce fichier en dépend) ; les tests des points
    d'entrée (#278) ont en plus besoin de **savoir où elle est**.
    """
    return _maison_isolee


@pytest.fixture()
def atelier(tmp_path: Path) -> Path:
    """Le dossier explorable des tests : celui qui contient les projets."""
    atelier = tmp_path / "atelier"
    atelier.mkdir()
    return atelier


@pytest.fixture()
def store(tmp_path: Path) -> ProjetStore:
    """Un dépôt de projets sur un dossier jetable."""
    return ProjetStore(tmp_path / "depot")


@pytest.fixture()
def client(store: ProjetStore, atelier: Path) -> TestClient:
    """TestClient de l'app, service des projets borné à l'atelier."""
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=ServiceProjets(store, racines_exploration=(atelier,)),
    )
    with TestClient(app) as client:
        yield client


def _dossier(parent: Path, nom: str) -> Path:
    """Un sous-dossier créé et rendu."""
    chemin = parent / nom
    chemin.mkdir(parents=True, exist_ok=True)
    return chemin


def _depot_git(racine: Path, *, branche: str = "main", distant: str | None = None) -> Path:
    """Un `.git` minimal — ce que `detecter_vcs` lit réellement (aucun `git` appelé)."""
    git = racine / ".git"
    git.mkdir(parents=True, exist_ok=True)
    (git / "HEAD").write_text(f"ref: refs/heads/{branche}\n", encoding="utf-8")
    config = "[core]\n\trepositoryformatversion = 0\n"
    if distant is not None:
        config += f'[remote "origin"]\n\turl = {distant}\n'
    (git / "config").write_text(config, encoding="utf-8")
    return git


def _lien_dossier(lien: Path, cible: Path) -> None:
    """Pose un lien de dossier, ou saute le test si l'OS le refuse (cf. #221)."""
    try:
        lien.symlink_to(cible, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name != "nt":  # pragma: no cover - repli propre à Windows
        pytest.skip("liens de dossier indisponibles sur ce poste")
    fait = subprocess.run(  # noqa: S603 - arguments fixes, aucun contenu externe
        ["cmd", "/c", "mklink", "/J", str(lien), str(cible)],
        capture_output=True,
        check=False,
    )
    if fait.returncode != 0 or not lien.exists():  # pragma: no cover - poste verrouillé
        pytest.skip("ni lien symbolique ni jonction disponibles sur ce poste")


def _declarer(client: TestClient, nom: str, racine: Path, **extra: object) -> dict:
    """Déclare un projet et exige que ça ait marché — le préalable de bien des tests."""
    reponse = client.post(
        "/api/projets", json={"nom": nom, "racine": str(racine), **extra}
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


# --- ① Le CRUD ---------------------------------------------------------------


def test_le_listing_part_vide_puis_porte_le_projet_declare(
    client: TestClient, atelier: Path
) -> None:
    assert client.get("/api/projets").json() == []

    fiche = _declarer(client, "Dépensio", _dossier(atelier, "depensio"))

    assert fiche["nom"] == "Dépensio"
    assert fiche["racine"] == (atelier / "depensio").resolve().as_posix()
    assert fiche["origine"] == "existant"
    assert fiche["vcs"] is None  # dossier non versionné : déclarable tel quel
    assert client.get("/api/projets").json() == [fiche]
    assert client.get(f"/api/projets/{fiche['id']}").json() == fiche


def test_la_racine_est_canonicalisee_avant_stockage(client: TestClient, atelier: Path) -> None:
    """Ce qui arrive est une saisie : c'est le chemin résolu qui est stocké."""
    _dossier(atelier, "depensio")
    detour = atelier / "depensio" / ".." / "depensio"

    fiche = _declarer(client, "Dépensio", detour)

    assert fiche["racine"] == (atelier / "depensio").resolve().as_posix()
    assert ".." not in fiche["racine"]


def test_le_vcs_est_constate_sur_le_disque(client: TestClient, atelier: Path) -> None:
    racine = _dossier(atelier, "versionne")
    _depot_git(racine, branche="develop", distant="git@example.org:moi/versionne.git")

    fiche = _declarer(client, "Versionné", racine)

    assert fiche["vcs"] == {
        "type": "git",
        "branche_base": "develop",
        "distant": "git@example.org:moi/versionne.git",
    }


def test_un_vcs_annonce_par_le_client_ne_fait_pas_foi(client: TestClient, atelier: Path) -> None:
    """Le `vcs` n'est délibérément pas un champ de requête : un client mentirait."""
    racine = _dossier(atelier, "sans-git")

    fiche = _declarer(
        client,
        "Menteur",
        racine,
        vcs={"type": "git", "branche_base": "main", "distant": None},
    )

    assert fiche["vcs"] is None


def test_origine_nouveau_cree_le_dossier_absent(client: TestClient, atelier: Path) -> None:
    racine = atelier / "a-naitre"
    assert not racine.exists()

    fiche = _declarer(client, "À naître", racine, origine="nouveau")

    assert racine.is_dir()
    assert fiche["origine"] == "nouveau"


def test_le_perimetre_servi_porte_les_exclusions_par_defaut(
    client: TestClient, atelier: Path
) -> None:
    fiche = _declarer(client, "Dépensio", _dossier(atelier, "depensio"))

    assert fiche["perimetre"]["inclus"] == ["."]
    for gisement in (".git", "node_modules", ".env", "**/secrets/**"):
        assert gisement in fiche["perimetre"]["exclus"]


def test_le_perimetre_fourni_remplace_les_defauts(client: TestClient, atelier: Path) -> None:
    fiche = _declarer(
        client,
        "Ciblé",
        _dossier(atelier, "cible"),
        inclus=["src", "docs"],
        exclus=["build"],
    )

    assert fiche["perimetre"] == {"inclus": ["src", "docs"], "exclus": ["build"]}


def test_le_remplacement_est_integral_et_redetecte_le_vcs(
    client: TestClient, atelier: Path
) -> None:
    """Même parti pris que `PUT /api/catalogue/{nom}` : un champ absent retombe au défaut."""
    racine = _dossier(atelier, "depensio")
    fiche = _declarer(client, "Dépensio", racine, inclus=["src"])
    _depot_git(racine, branche="main")

    remplacee = client.put(
        f"/api/projets/{fiche['id']}",
        json={"nom": "Dépensio v2", "racine": str(racine)},
    )

    assert remplacee.status_code == 200, remplacee.text
    corps = remplacee.json()
    assert corps["nom"] == "Dépensio v2"
    assert corps["vcs"]["branche_base"] == "main"  # le dossier est passé sous Git depuis
    assert corps["perimetre"]["inclus"] == ["."]  # non fourni ⇒ défaut, pas « conservé »
    assert corps["cree_le"] == fiche["cree_le"]  # la date de création est préservée


def test_la_suppression_oublie_la_declaration_sans_toucher_au_disque(
    client: TestClient, atelier: Path
) -> None:
    racine = _dossier(atelier, "depensio")
    (racine / "travail.md").write_text("mon travail", encoding="utf-8")
    fiche = _declarer(client, "Dépensio", racine)

    reponse = client.delete(f"/api/projets/{fiche['id']}")

    assert reponse.status_code == 200
    assert reponse.json() == {"id": fiche["id"], "supprime": True}
    assert client.get("/api/projets").json() == []
    assert (racine / "travail.md").read_text(encoding="utf-8") == "mon travail"


def test_un_projet_illisible_saute_du_listing_mais_s_explique_au_detail(
    client: TestClient, atelier: Path, store: ProjetStore
) -> None:
    """Un JSON corrompu ne doit pas rendre l'écran Projets inutilisable."""
    lisible = _declarer(client, "Lisible", _dossier(atelier, "lisible"))
    (store.racine / "prj-casse.json").write_text("{ pas du json", encoding="utf-8")

    assert [f["id"] for f in client.get("/api/projets").json()] == [lisible["id"]]

    detail = client.get("/api/projets/prj-casse")
    assert detail.status_code == 422
    assert detail.json()["detail"]["motif"] == "projet-illisible"


# --- ② Les refus du CRUD : motivés, jamais un 500 ---------------------------


def test_projet_inconnu_rend_404_motive(client: TestClient) -> None:
    for appel in (
        client.get("/api/projets/prj-fantome"),
        client.delete("/api/projets/prj-fantome"),
    ):
        assert appel.status_code == 404, appel.text
        assert appel.json()["detail"]["motif"] == "projet-inconnu"


def test_remplacer_un_projet_inconnu_rend_404_sans_rien_ecrire(
    client: TestClient, atelier: Path
) -> None:
    racine = _dossier(atelier, "depensio")

    reponse = client.put(
        "/api/projets/prj-fantome", json={"nom": "Fantôme", "racine": str(racine)}
    )

    assert reponse.status_code == 404
    assert client.get("/api/projets").json() == []


def test_le_dossier_utilisateur_nu_est_refuse_avec_son_motif(
    client: TestClient, _maison_isolee: Path
) -> None:
    reponse = client.post(
        "/api/projets", json={"nom": "Tout", "racine": str(_maison_isolee)}
    )

    assert reponse.status_code == 422
    assert reponse.json()["detail"]["motif"] == "dossier-utilisateur-nu"


def test_un_chemin_sensible_est_refuse_avec_son_motif(
    client: TestClient, _maison_isolee: Path
) -> None:
    """403 et non 422 : une zone sensible est une frontière franchie, pas une saisie fautive.

    Le code vient du **motif**, jamais de la route : `chemin-sensible` rend donc
    le même 403 qu'on le rencontre en déclarant un projet ou en explorant.
    """
    ssh = _dossier(_maison_isolee, ".ssh")

    reponse = client.post("/api/projets", json={"nom": "Clés", "racine": str(ssh)})

    assert reponse.status_code == 403
    assert reponse.json()["detail"]["motif"] == "chemin-sensible"


def test_une_racine_qui_remonte_au_dessus_par_double_point_est_refusee(
    client: TestClient, _maison_isolee: Path
) -> None:
    """`../..` est écrasé par la canonicalisation, puis confronté aux interdits."""
    depuis = _dossier(_maison_isolee, "projets/depensio")
    evasion = depuis / ".." / ".."  # ⇒ le dossier utilisateur lui-même

    reponse = client.post("/api/projets", json={"nom": "Évasion", "racine": str(evasion)})

    assert reponse.status_code == 422
    assert reponse.json()["detail"]["motif"] == "dossier-utilisateur-nu"


def test_un_lien_vers_une_zone_sensible_est_refuse_comme_sa_cible(
    client: TestClient, atelier: Path, _maison_isolee: Path
) -> None:
    """La résolution a lieu **avant** la comparaison : un lien ne contourne rien."""
    _dossier(_maison_isolee, ".ssh")
    lien = atelier / "innocent"
    _lien_dossier(lien, _maison_isolee / ".ssh")

    reponse = client.post("/api/projets", json={"nom": "Innocent", "racine": str(lien)})

    assert reponse.status_code == 403
    assert reponse.json()["detail"]["motif"] == "chemin-sensible"


def test_une_racine_relative_est_refusee(client: TestClient) -> None:
    reponse = client.post("/api/projets", json={"nom": "Relatif", "racine": "./quelque-part"})

    assert reponse.status_code == 422
    assert reponse.json()["detail"]["motif"] == "chemin-relatif"


def test_une_racine_absente_est_refusee_sauf_a_la_creer(
    client: TestClient, atelier: Path
) -> None:
    absent = atelier / "jamais-vu"

    reponse = client.post("/api/projets", json={"nom": "Absent", "racine": str(absent)})

    assert reponse.status_code == 422
    assert reponse.json()["detail"]["motif"] == "dossier-absent"
    assert not absent.exists()  # un refus ne crée rien


def test_une_racine_deja_declaree_est_refusee(client: TestClient, atelier: Path) -> None:
    racine = _dossier(atelier, "depensio")
    _declarer(client, "Dépensio", racine)

    reponse = client.post("/api/projets", json={"nom": "Doublon", "racine": str(racine)})

    assert reponse.status_code == 422
    assert len(client.get("/api/projets").json()) == 1


def test_un_refus_porte_toujours_un_motif_et_un_message(
    client: TestClient, _maison_isolee: Path
) -> None:
    """L'écran Projets doit dire *pourquoi* : une phrase libre ne lui suffirait pas."""
    detail = client.post(
        "/api/projets", json={"nom": "Tout", "racine": str(_maison_isolee)}
    ).json()["detail"]

    assert set(detail) == {"motif", "message"}
    assert detail["motif"] and detail["message"]
    assert str(_maison_isolee) in detail["message"] or _maison_isolee.name in detail["message"]


# --- ③ L'explorateur de dossiers --------------------------------------------


def test_sans_chemin_l_explorateur_rend_les_racines(client: TestClient, atelier: Path) -> None:
    vue = client.get("/api/projets/explorateur").json()

    assert vue["chemin"] is None
    assert vue["parent"] is None
    assert vue["racines"] == [atelier.resolve().as_posix()]
    assert [d["chemin"] for d in vue["dossiers"]] == [atelier.resolve().as_posix()]
    assert vue["tronque"] is False


def test_la_route_explorateur_n_est_pas_avalee_par_la_capture_d_identifiant(
    client: TestClient,
) -> None:
    """« explorateur » est un identifiant de projet valide au regard du slug."""
    reponse = client.get("/api/projets/explorateur")

    assert reponse.status_code == 200
    assert "dossiers" in reponse.json()  # et non un 404 « projet inconnu »


def test_l_explorateur_liste_les_sous_dossiers_avec_leurs_marqueurs(
    client: TestClient, atelier: Path
) -> None:
    _dossier(atelier, "simple")
    _depot_git(_dossier(atelier, "versionne"))
    (atelier / "un-fichier.txt").write_text("pas un dossier", encoding="utf-8")
    declare = _declarer(client, "Simple", atelier / "simple")

    vue = client.get("/api/projets/explorateur", params={"chemin": str(atelier)}).json()

    par_nom = {d["nom"]: d for d in vue["dossiers"]}
    assert set(par_nom) == {"simple", "versionne"}  # les fichiers ne sortent pas
    assert par_nom["versionne"]["depot_git"] is True
    assert par_nom["simple"]["depot_git"] is False
    assert par_nom["simple"]["projet_id"] == declare["id"]
    assert par_nom["versionne"]["projet_id"] is None


def test_le_parent_est_nul_au_bord_des_racines(client: TestClient, atelier: Path) -> None:
    """La frontière se voit dans la réponse, elle ne se découvre pas au clic suivant."""
    enfant = _dossier(atelier, "enfant")

    au_bord = client.get("/api/projets/explorateur", params={"chemin": str(atelier)}).json()
    dedans = client.get("/api/projets/explorateur", params={"chemin": str(enfant)}).json()

    assert au_bord["parent"] is None
    assert dedans["parent"] == atelier.resolve().as_posix()


def test_la_racine_d_un_projet_declare_devient_explorable(
    tmp_path: Path, store: ProjetStore, atelier: Path
) -> None:
    """Une racine déclarée a passé `valider_racine` : elle est explorable par construction."""
    ailleurs = _dossier(tmp_path / "ailleurs", "depensio")
    _dossier(ailleurs, "src")
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=ServiceProjets(store, racines_exploration=(atelier,)),
    )
    with TestClient(app) as client:
        assert client.get(
            "/api/projets/explorateur", params={"chemin": str(ailleurs)}
        ).status_code == 403

        _declarer(client, "Dépensio", ailleurs)

        vue = client.get("/api/projets/explorateur", params={"chemin": str(ailleurs)})
        assert vue.status_code == 200
        assert [d["nom"] for d in vue.json()["dossiers"]] == ["src"]


def test_la_troncature_est_annoncee_jamais_silencieuse(
    client: TestClient, atelier: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une troncature muette se lit comme un dossier complet."""
    monkeypatch.setattr("maestro.controltower.projets.LIMITE_DOSSIERS", 2)
    for index in range(4):
        _dossier(atelier, f"lot-{index}")

    vue = client.get("/api/projets/explorateur", params={"chemin": str(atelier)}).json()

    assert len(vue["dossiers"]) == 2
    assert vue["tronque"] is True


# --- ④ Les refus de l'explorateur : motivés, jamais une liste vide ----------


def test_hors_des_racines_explorables_l_explorateur_refuse_en_403(
    client: TestClient, tmp_path: Path
) -> None:
    dehors = _dossier(tmp_path, "dehors")

    reponse = client.get("/api/projets/explorateur", params={"chemin": str(dehors)})

    assert reponse.status_code == 403
    assert reponse.json()["detail"]["motif"] == "hors-racines-explorables"


def test_une_zone_sensible_dans_une_racine_reste_refusee(
    client: TestClient, atelier: Path, _maison_isolee: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le dossier utilisateur est traversable, `~/.ssh` ne l'est pas."""
    ssh = _dossier(_maison_isolee, ".ssh")
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=ServiceProjets(
            ProjetStore(atelier / "depot"), racines_exploration=(_maison_isolee,)
        ),
    )
    with TestClient(app) as explorable:
        reponse = explorable.get("/api/projets/explorateur", params={"chemin": str(ssh)})

    assert reponse.status_code == 403
    assert reponse.json()["detail"]["motif"] == "chemin-sensible"


def test_un_dossier_absent_est_un_404_la_ou_une_racine_absente_est_un_422(
    client: TestClient, atelier: Path
) -> None:
    """Le même motif, deux lectures : la ressource manque / le corps est inexploitable."""
    absent = atelier / "jamais-vu"

    explore = client.get("/api/projets/explorateur", params={"chemin": str(absent)})
    declare = client.post("/api/projets", json={"nom": "Absent", "racine": str(absent)})

    assert explore.status_code == 404
    assert explore.json()["detail"]["motif"] == "dossier-absent"
    assert declare.status_code == 422
    assert declare.json()["detail"]["motif"] == "dossier-absent"


def test_un_chemin_relatif_est_refuse_par_l_explorateur(client: TestClient) -> None:
    reponse = client.get("/api/projets/explorateur", params={"chemin": "quelque/part"})

    assert reponse.status_code == 422
    assert reponse.json()["detail"]["motif"] == "chemin-relatif"


def test_un_fichier_n_est_pas_un_dossier_explorable(client: TestClient, atelier: Path) -> None:
    fichier = atelier / "notes.md"
    fichier.write_text("des notes", encoding="utf-8")

    reponse = client.get("/api/projets/explorateur", params={"chemin": str(fichier)})

    assert reponse.status_code == 422
    assert reponse.json()["detail"]["motif"] == "pas-un-dossier"


def test_sans_aucune_racine_explorable_le_refus_le_dit(
    tmp_path: Path, store: ProjetStore
) -> None:
    """Aucune racine n'est **pas** « le disque entier » : c'est un refus motivé."""
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=ServiceProjets(store, racines_exploration=(tmp_path / "disparu",)),
    )
    with TestClient(app) as client:
        reponse = client.get("/api/projets/explorateur")

    assert reponse.status_code == 403
    assert reponse.json()["detail"]["motif"] == "aucune-racine-explorable"


def test_un_refus_de_l_explorateur_n_est_jamais_une_liste_vide(
    client: TestClient, tmp_path: Path
) -> None:
    """Le cœur du critère : « rien à voir ici » et « je refuse » sont deux réponses."""
    vide = _dossier(tmp_path / "atelier", "vide")
    interdit = _dossier(tmp_path, "dehors")

    sans_sous_dossier = client.get("/api/projets/explorateur", params={"chemin": str(vide)})
    refuse = client.get("/api/projets/explorateur", params={"chemin": str(interdit)})

    assert sans_sous_dossier.status_code == 200
    assert sans_sous_dossier.json()["dossiers"] == []
    assert refuse.status_code == 403
    assert "dossiers" not in refuse.json()


def test_les_racines_configurees_viennent_de_l_environnement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`MAESTRO_EXPLORATEUR_RACINES`, éclaté sur `os.pathsep` (`;` sous Windows)."""
    un = _dossier(tmp_path / "atelier", "un")
    deux = _dossier(tmp_path / "atelier", "deux")
    monkeypatch.setenv("MAESTRO_EXPLORATEUR_RACINES", os.pathsep.join([str(un), str(deux)]))
    monkeypatch.setenv("MAESTRO_PROJETS_DIR", str(tmp_path / "depot"))

    service = ServiceProjets.default()

    assert set(service.racines()) == {un.resolve(), deux.resolve()}


def test_le_depot_des_projets_vient_de_l_environnement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAESTRO_PROJETS_DIR", str(tmp_path / "depot-a-moi"))

    service = ServiceProjets.default()

    assert service.store.racine == (tmp_path / "depot-a-moi")


def test_le_fichier_stocke_et_la_reponse_http_ont_la_meme_forme(
    client: TestClient, atelier: Path, store: ProjetStore
) -> None:
    """Aucune seconde définition du contrat, donc aucune dérive possible (docs/24 §2.3)."""
    fiche = _declarer(client, "Dépensio", _dossier(atelier, "depensio"))

    sur_disque = json.loads(
        (store.racine / f"{fiche['id']}.json").read_text(encoding="utf-8")
    )

    assert sur_disque == fiche


# --- ⑤ Les racines élargies et les points d'entrée (#278) --------------------
#
# Le lot 2 de #276 a livré sans tests (tests différés → ce lot, #282). Ce qu'il
# a changé n'est pas l'explorateur mais **jusqu'où** il a le droit de regarder,
# et **par où** il propose de commencer — deux notions qui ne se recouvraient
# pas avant lui et qu'il faut donc mesurer séparément.


@pytest.fixture()
def service_defaut(store: ProjetStore) -> ServiceProjets:
    """Le service **sans** racines configurées : le cas par défaut, celui de #278.

    Le `client` ci-dessus borne l'exploration à l'atelier — indispensable pour
    ne pas dépendre du poste, mais c'est justement la branche que #278 ne
    change pas. Les points d'entrée et les volumes ne s'observent que sur le
    défaut, avec le dossier utilisateur factice posé par `_maison_isolee`.
    """
    return ServiceProjets(store)


def _origines(service: ServiceProjets) -> dict[str, str]:
    """Les points d'entrée indexés par chemin POSIX → origine."""
    return {point.as_posix(): origine for point, origine in service.points_entree()}


def test_la_frontiere_par_defaut_contient_les_volumes_du_poste(
    service_defaut: ServiceProjets,
) -> None:
    """Le cul-de-sac que #278 ouvre : un projet sur `D:/` était inatteignable.

    Le dossier utilisateur seul était la frontière, et la seule sortie était de
    renseigner `MAESTRO_EXPLORATEUR_RACINES` — un réglage d'environnement pour
    un geste d'écran. La frontière contient désormais les volumes ; sous POSIX
    il n'y en a qu'un (`/`), qui **est** le volume.
    """
    racines = service_defaut.racines()

    assert racines, "la frontière par défaut ne peut pas être vide"
    assert any(
        racine == Path(racine.anchor) for racine in racines
    ), f"aucun volume dans {racines}"


def test_la_frontiere_dedoublonne_par_contenance(service_defaut: ServiceProjets) -> None:
    """Un volume avale le dossier utilisateur : le garder ferait lister deux fois le même arbre.

    C'est la raison d'être de `points_entree` — sans ce dédoublonnage la
    frontière serait redondante, avec lui elle se réduit au volume et ne peut
    plus servir de page d'accueil.
    """
    racines = service_defaut.racines()

    for racine in racines:
        autres = tuple(a for a in racines if a != racine)
        assert not any(
            racine.is_relative_to(autre) for autre in autres
        ), f"{racine} est contenue dans une autre racine"


def test_les_points_d_entree_gardent_le_dossier_utilisateur_a_part(
    service_defaut: ServiceProjets, maison: Path
) -> None:
    """La frontière se réduit au volume ; la page d'entrée, elle, garde l'utile.

    Le test qui donne son sens au précédent : `racines()` et `points_entree()`
    ont divergé avec #278, et confondre les deux rendrait l'écran inutilisable
    (un seul point d'entrée `C:/` d'où redescendre tout l'arbre à chaque fois).
    """
    origines = _origines(service_defaut)

    assert origines.get(maison.resolve().as_posix()) == "utilisateur"
    assert "volume" in origines.values()


def test_un_projet_declare_propose_son_dossier_et_son_parent(
    service_defaut: ServiceProjets, maison: Path
) -> None:
    """Les origines `projet` et `recent`, et ce que « récent » veut dire ici.

    `recent` est le **parent** du projet — le dossier où l'on range ses dépôts,
    donc là où l'on déclarera le suivant. Ce n'est pas un historique de
    navigation : rien n'est enregistré au clic, donc rien n'est à purger.
    """
    depots = _dossier(maison, "depots")
    racine = _dossier(depots, "depensio")
    service_defaut.creer(nom="Dépensio", racine=str(racine))

    origines = _origines(service_defaut)

    assert origines[racine.resolve().as_posix()] == "projet"
    assert origines[depots.resolve().as_posix()] == "recent"


def test_les_points_d_entree_sont_dans_l_ordre_d_affichage(
    service_defaut: ServiceProjets, maison: Path
) -> None:
    """Utilisateur, récents, projets, volumes — du plus probable au dernier recours."""
    service_defaut.creer(nom="Dépensio", racine=str(_dossier(maison / "depots", "depensio")))

    origines = [origine for _, origine in service_defaut.points_entree()]

    assert origines[0] == "utilisateur"
    assert origines.index("recent") < origines.index("projet")
    assert origines.index("projet") < origines.index("volume")


def test_un_point_d_entree_n_est_jamais_propose_deux_fois(
    service_defaut: ServiceProjets, maison: Path
) -> None:
    """Deux projets rangés au même endroit : un seul « récent », pas deux lignes identiques."""
    depots = _dossier(maison, "depots")
    service_defaut.creer(nom="Dépensio", racine=str(_dossier(depots, "depensio")))
    service_defaut.creer(nom="Autre", racine=str(_dossier(depots, "autre")))

    points = [point for point, _ in service_defaut.points_entree()]

    assert len(points) == len(set(points))


def test_les_racines_configurees_restent_une_restriction(
    store: ProjetStore, atelier: Path
) -> None:
    """`MAESTRO_EXPLORATEUR_RACINES` **restreint**, il n'élargit pas — l'invariant de #278.

    Les volumes ne sont proposés que là où la frontière est celle par défaut :
    un point d'entrée est filtré sur la frontière, et une frontière configurée
    ne contient pas les volumes. Sans ce filtre, élargir le défaut aurait
    élargi *tout le monde*, y compris les postes qui s'étaient explicitement
    bornés.
    """
    service = ServiceProjets(store, racines_exploration=(atelier,))

    origines = _origines(service)

    assert origines == {atelier.resolve().as_posix(): "configuree"}


def test_une_zone_interdite_n_est_jamais_un_point_d_entree(
    service_defaut: ServiceProjets, maison: Path
) -> None:
    """Élargir la frontière n'ouvre pas EF-38 : un point qui refuserait au clic est retiré.

    Le `.ssh` du dossier utilisateur est **dans** la frontière élargie (le
    volume la contient) : seule la vérification de zone interdite l'écarte.
    """
    zone_sensible = _dossier(maison, ".ssh")

    assert zone_sensible.resolve().as_posix() not in _origines(service_defaut)


def test_la_page_d_entree_rend_les_points_avec_leur_origine(
    store: ProjetStore, maison: Path
) -> None:
    """La forme JSON de la page d'entrée (docs/05 §6.7) : `origine` sur chaque dossier."""
    app = create_app(
        bus=InMemoryEventBus(), state=ControlTowerState(), projets=ServiceProjets(store)
    )
    with TestClient(app) as client:
        page = client.get("/api/projets/explorateur").json()

    assert page["chemin"] is None
    assert page["parent"] is None
    origines = {dossier["chemin"]: dossier["origine"] for dossier in page["dossiers"]}
    assert origines[maison.resolve().as_posix()] == "utilisateur"


def test_un_sous_dossier_enumere_n_a_pas_d_origine(client: TestClient, atelier: Path) -> None:
    """`origine` est propre à la page d'entrée : ailleurs elle est `null`, jamais devinée."""
    _dossier(atelier, "depensio")

    page = client.get("/api/projets/explorateur", params={"chemin": str(atelier)}).json()

    assert [dossier["origine"] for dossier in page["dossiers"]] == [None]


def test_la_frontiere_elargie_refuse_toujours_les_zones_interdites(
    store: ProjetStore, maison: Path
) -> None:
    """Le critère du lot, énoncé à l'envers : élargir l'explorable n'ouvre **aucune** brèche.

    `.ssh` est sous le dossier utilisateur, donc sous le volume, donc dans la
    frontière depuis #278 — et pourtant refusé, avec le motif d'EF-38 et son
    403. C'est `verifier_zone_interdite` qui tient, pas la frontière.
    """
    zone_sensible = _dossier(maison, ".ssh")
    app = create_app(
        bus=InMemoryEventBus(), state=ControlTowerState(), projets=ServiceProjets(store)
    )
    with TestClient(app) as client:
        reponse = client.get(
            "/api/projets/explorateur", params={"chemin": str(zone_sensible)}
        )

    assert reponse.status_code == 403
    assert reponse.json()["detail"]["motif"] == "chemin-sensible"


def test_un_chemin_saisi_hors_atelier_reste_explorable_par_defaut(
    store: ProjetStore, tmp_path: Path
) -> None:
    """Le geste que #278 rend possible : taper un chemin lointain et que l'API l'accepte.

    Le même chemin est refusé par le `client` de ce fichier, borné à l'atelier
    (`test_hors_des_racines_explorables_l_explorateur_refuse_en_403`) : ce qui
    change n'est pas la saisie mais la frontière, et c'est bien elle que ce lot
    déplace.
    """
    lointain = _dossier(tmp_path, "depots-lointains")
    app = create_app(
        bus=InMemoryEventBus(), state=ControlTowerState(), projets=ServiceProjets(store)
    )
    with TestClient(app) as client:
        reponse = client.get("/api/projets/explorateur", params={"chemin": str(lointain)})

    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["chemin"] == lointain.resolve().as_posix()


# --- ⑥ La mise sous Git d'un projet non versionné (#855) ---------------------

GIT = shutil.which("git")

#: Les tests qui posent un **vrai** dépôt — `git init`, un commit, un hook — ne
#: se jouent pas sans `git` ; ceux qui n'ont besoin que d'un `.git` minimal ou
#: d'un refus rendu avant toute commande tournent partout.
avec_git = pytest.mark.skipif(GIT is None, reason="git introuvable")


@pytest.fixture()
def _git_isole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Coupe la configuration Git globale du poste (#333) : ni identité, ni `core.hooksPath`.

    Le premier commit pose son identité par `-c` ; ce qui reste à couper est ce
    qu'un poste pourrait ajouter — un `pre-commit` global, un `init.templateDir`
    — et qui ferait dépendre le verdict de la machine qui joue la suite.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig-absent"))


def _git(cwd: Path, *arguments: str) -> str:
    """Lance `git` dans `cwd` et rend sa sortie — échoue le test si Git échoue."""
    resultat = subprocess.run(  # noqa: S603 - arguments fixes, aucun contenu externe
        [GIT or "git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert resultat.returncode == 0, f"git {' '.join(arguments)} : {resultat.stderr}"
    return resultat.stdout


def _projet_nu(client: TestClient, atelier: Path, nom: str = "agenda") -> tuple[Path, dict]:
    """Un dossier **sans** `.git` et un fichier, déclaré : ce que l'écran propose de versionner.

    Le point de départ des tests du geste, nominal comme refusé.
    """
    racine = _dossier(atelier, nom)
    (racine / "README.md").write_text("# Agenda\n", encoding="utf-8")
    fiche = _declarer(client, "Agenda", racine)
    assert fiche["vcs"] is None
    return racine, fiche


@avec_git
@pytest.mark.usefixtures("_git_isole")
def test_versionner_met_le_projet_sous_git_et_rend_la_fiche_relue(
    client: TestClient, atelier: Path
) -> None:
    """Le geste de #704 par la route : `git init`, premier commit de toute la racine, fiche relue.

    Ce que la confirmation de l'écran promet est vérifié ici sur le disque : le
    commit porte la racine telle qu'elle est, `.gitignore` **respecté** — un
    fichier ignoré n'entre pas dans l'index —, et la racine ressort **propre**,
    condition pour que `maestro.projets.application` accepte d'y fusionner.
    """
    racine, fiche = _projet_nu(client, atelier)
    (racine / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
    (racine / "secret.txt").write_text("mot de passe", encoding="utf-8")

    reponse = client.post(f"/api/projets/{fiche['id']}/versionner")

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["vcs"] is not None and corps["vcs"]["type"] == "git"
    assert corps["vcs"]["branche_base"] != ""  # la base **résout** : un `HEAD` né
    assert corps["id"] == fiche["id"] and corps["cree_le"] == fiche["cree_le"]
    # La fiche rendue est celle du dépôt, relue : `GET` dit exactement la même chose.
    assert client.get(f"/api/projets/{fiche['id']}").json() == corps
    # Sur le disque : un seul commit, celui de Maestro, qui porte la racine — sauf l'ignoré.
    assert _git(racine, "log", "--format=%s").splitlines() == [MESSAGE_PREMIER_COMMIT]
    suivis = _git(racine, "ls-files").split()
    assert "README.md" in suivis and ".gitignore" in suivis
    assert "secret.txt" not in suivis
    assert _git(racine, "status", "--porcelain").strip() == ""


def test_un_projet_deja_versionne_est_rendu_tel_quel_sans_commande(
    client: TestClient, atelier: Path
) -> None:
    """Idempotent : un second clic ne coûte ni commande Git, ni écriture, ni `modifie_le`.

    Le `.git` est **minimal** (un `HEAD`, un `config`) et aucun `git` n'est
    disponible pour ce test : si la route lançait la moindre commande sur un
    projet déjà versionné, elle échouerait ici au lieu de rendre la fiche.
    """
    racine = _dossier(atelier, "versionne")
    _depot_git(racine, branche="develop")
    fiche = _declarer(client, "Versionné", racine)
    assert fiche["vcs"]["branche_base"] == "develop"

    reponse = client.post(f"/api/projets/{fiche['id']}/versionner")

    assert reponse.status_code == 200, reponse.text
    assert reponse.json() == fiche  # `modifie_le` compris : rien n'a été écrit


def test_la_route_est_sans_corps_un_vcs_envoye_n_est_ni_lu_ni_stocke(
    client: TestClient, atelier: Path
) -> None:
    """EF-38 : le `vcs` se **constate**, il ne se déclare pas — la route déclenche, sans corps.

    C'est ce qui la distingue d'un `PUT` élargi : un client qui annoncerait une
    autre branche ne change rien à ce que le disque dit.
    """
    racine = _dossier(atelier, "versionne")
    _depot_git(racine, branche="develop")
    fiche = _declarer(client, "Versionné", racine)

    reponse = client.post(
        f"/api/projets/{fiche['id']}/versionner",
        json={"vcs": {"type": "git", "branche_base": "menteur", "distant": None}},
    )

    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["vcs"]["branche_base"] == "develop"
    assert client.get(f"/api/projets/{fiche['id']}").json()["vcs"]["branche_base"] == "develop"


def test_versionner_un_projet_inconnu_rend_404_motive(client: TestClient) -> None:
    reponse = client.post("/api/projets/prj-fantome/versionner")

    assert reponse.status_code == 404, reponse.text
    assert reponse.json()["detail"]["motif"] == "projet-inconnu"


def test_une_racine_disparue_est_refusee_avec_son_motif_avant_toute_commande(
    client: TestClient, atelier: Path
) -> None:
    """`RacineRefusee` (EF-38) remonte comme un refus, jamais un 500 — et rien n'est créé.

    La racine est **revalidée** avant d'écrire chez l'utilisateur : le fichier du
    dépôt s'édite à la main, et un dossier déclaré peut avoir disparu depuis.
    Aucun `git` n'est nécessaire — le refus tombe avant la première commande.
    """
    racine = _dossier(atelier, "ephemere")
    fiche = _declarer(client, "Éphémère", racine)
    racine.rmdir()

    reponse = client.post(f"/api/projets/{fiche['id']}/versionner")

    assert reponse.status_code == 422, reponse.text
    detail = reponse.json()["detail"]
    assert detail["motif"] == "dossier-absent"
    assert detail["message"]
    assert not racine.exists()  # un refus ne crée rien — ni dossier, ni `.git`
    assert client.get(f"/api/projets/{fiche['id']}").json()["vcs"] is None


@avec_git
@pytest.mark.usefixtures("_git_isole")
def test_une_racine_dans_un_autre_depot_est_refusee_en_409_motive(
    client: TestClient, atelier: Path
) -> None:
    """`depot-englobant` : l'**état du disque** s'oppose au geste — 409, et rien n'a bougé.

    Un dépôt imbriqué modifierait le dépôt du dessus (un lien de sous-module là
    où il avait des fichiers) ; ni la saisie (422) ni une frontière (403) ne
    sont en cause, d'où le code des refus d'état.
    """
    monorepo = _dossier(atelier, "monorepo")
    _git(monorepo, "init", "--quiet")
    racine = _dossier(monorepo, "paquets/agenda")
    (racine / "app.py").write_text("x\n", encoding="utf-8")
    fiche = _declarer(client, "Agenda", racine)
    assert fiche["vcs"] is None  # `detecter_vcs` ne regarde que `<racine>/.git`

    reponse = client.post(f"/api/projets/{fiche['id']}/versionner")

    assert reponse.status_code == 409, reponse.text
    detail = reponse.json()["detail"]
    assert detail["motif"] == "depot-englobant"
    assert "monorepo" in detail["message"]  # le message nomme le dépôt du dessus
    assert not (racine / ".git").exists()
    assert client.get(f"/api/projets/{fiche['id']}").json()["vcs"] is None


@avec_git
def test_un_premier_commit_refuse_remonte_en_422_motive_et_laisse_la_racine_intacte(
    client: TestClient, atelier: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Les hooks de l'utilisateur tiennent, et leur refus est une **réponse** — pas un 500.

    Un `core.hooksPath` global dont le `pre-commit` refuse tout : le `.git` né
    ici repart avec l'échec, le reste de la racine n'a pas bougé, et la
    déclaration n'a rien enregistré.
    """
    hooks = _dossier(tmp_path, "hooks")
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'refusé par le pre-commit' >&2\nexit 1\n", encoding="utf-8")
    os.chmod(hook, 0o755)
    config = tmp_path / "gitconfig-hooks"
    config.write_text(f"[core]\n\thooksPath = {hooks.as_posix()}\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    racine, fiche = _projet_nu(client, atelier)

    reponse = client.post(f"/api/projets/{fiche['id']}/versionner")

    assert reponse.status_code == 422, reponse.text
    detail = reponse.json()["detail"]
    assert detail["motif"] == "commit-refuse"
    assert "pre-commit" in detail["message"]
    assert not (racine / ".git").exists()  # le `.git` né ici est reparti avec l'échec
    assert (racine / "README.md").read_text(encoding="utf-8") == "# Agenda\n"
    assert client.get(f"/api/projets/{fiche['id']}").json()["vcs"] is None


def test_un_poste_sans_git_repond_503_motive(
    client: TestClient, atelier: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`git-indisponible` : ni la saisie ni le disque, c'est le **poste** — 503, motif compris.

    Le binaire absent est simulé au niveau du sous-processus (le seul endroit où
    `versionnement` le rencontre), après la déclaration — qui, elle, ne lance
    jamais `git`.
    """
    racine, fiche = _projet_nu(client, atelier)

    def _pas_de_git(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError(2, "git introuvable")

    monkeypatch.setattr(versionnement.subprocess, "run", _pas_de_git)

    reponse = client.post(f"/api/projets/{fiche['id']}/versionner")

    assert reponse.status_code == 503, reponse.text
    detail = reponse.json()["detail"]
    assert detail["motif"] == "git-indisponible"
    assert "git introuvable" in detail["message"]
    assert not (racine / ".git").exists()
    assert client.get(f"/api/projets/{fiche['id']}").json()["vcs"] is None
