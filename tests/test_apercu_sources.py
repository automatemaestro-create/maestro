"""L'aperçu d'ingestion (#319) : ce que des sources donneraient, sans rien engager.

Deux niveaux, et le second est celui qui compte pour l'écran : le module
`maestro.sources.apercu` (ce qu'un aperçu lit, refuse et **efface**) et la route
`POST /api/sources/apercu` (la forme du contrat, docs/05 §6.9).

Le reste de la couverture de la Phase 8 est différé au lot final #323 (checklist
de #314) ; ce fichier ne garde que ce qu'un écran ne peut pas rattraper — un
aperçu qui laisserait de la matière derrière lui, ou qui refuserait ce qu'il
devrait seulement signaler.

Aucun réseau : les sources `url` passent par l'injection `recuperer_url` de #316,
comme l'exige `tests/conftest.py` (#195).
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.engine.guardrails import GardeFousIngestion
from maestro.sources import SourceRefusee, racine_ingestion
from maestro.sources.apercu import apercu_sources


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier utilisateur factice — même raison qu'en #221/#223.

    Sous Windows, le `tmp_path` de pytest vit dans `AppData/Local/Temp`, que
    `valider_racine` refuse à raison : sans cette isolation, un dossier de
    références déclaré dans `tmp_path` serait refusé pour une bonne raison, mais
    pas celle que ces tests mesurent.
    """
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


@pytest.fixture()
def client() -> TestClient:
    """TestClient de l'app — l'aperçu ne dépend d'aucun service injecté."""
    app = create_app(bus=InMemoryEventBus(), state=ControlTowerState())
    with TestClient(app) as client:
        yield client


def _declarer(nom: str, contenu: bytes) -> dict[str, object]:
    """La déclaration d'un fichier déposé, telle que l'écran l'envoie."""
    return {"type": "fichier", "nom": nom, "taille": len(contenu)}


# --- Le module : lire, refuser, effacer ------------------------------------


def test_un_fichier_depose_est_lu_et_chiffre() -> None:
    """Le cas nominal : le contenu entre, et son coût est annoncé."""
    contenu = "Cahier des charges\n\nRefondre l'écran de lancement.".encode()
    rapport = apercu_sources(
        [_declarer("cdc.md", contenu)],
        fichiers=[("cdc.md", io.BytesIO(contenu))],
    )
    (lecture,) = rapport.lectures
    assert lecture.nom == "cdc.md"
    assert lecture.etat == "lu"
    assert lecture.tokens > 0
    assert rapport.tokens == lecture.tokens


def test_un_apercu_ne_laisse_rien_derriere_lui(tmp_path: Path) -> None:
    """La promesse du module : les octets lus s'en vont avec le dossier jetable.

    Mesuré sur le disque et non sur une intention : ni dans l'emplacement
    d'ingestion des runs, ni ailleurs — regarder deux fois ne doit pas remplir
    un disque pour un geste réputé gratuit.
    """
    contenu = b"une matiere quelconque"
    avant = sorted(racine_ingestion().glob("*")) if racine_ingestion().exists() else []
    rapport = apercu_sources(
        [_declarer("note.txt", contenu)], fichiers=[("note.txt", io.BytesIO(contenu))]
    )
    assert rapport.lectures[0].etat == "lu"
    apres = sorted(racine_ingestion().glob("*")) if racine_ingestion().exists() else []
    assert apres == avant


def test_un_format_non_gere_est_signale_jamais_refuse() -> None:
    """« Rien à lire ici » n'est pas « je refuse de lire ça » (critère 3).

    Un `.png` déposé au milieu d'un dossier de maquettes ne doit faire échouer
    aucun aperçu : il ressort **ligne du rapport**, avec son motif.
    """
    contenu = b"\x89PNG\r\n\x1a\n binaire"
    rapport = apercu_sources(
        [_declarer("maquette.png", contenu)],
        fichiers=[("maquette.png", io.BytesIO(contenu))],
    )
    (lecture,) = rapport.lectures
    assert lecture.etat == "ignore"
    assert lecture.motif == "format-non-gere"
    assert lecture.tokens == 0


def test_une_source_hors_bornes_est_refusee_avec_son_index() -> None:
    """Le régime de la résolution : refuser, en disant **laquelle** corriger."""
    petit = b"ok"
    trop = b"x" * 4096
    with pytest.raises(SourceRefusee) as refus:
        apercu_sources(
            [_declarer("a.txt", petit), _declarer("b.txt", trop)],
            fichiers=[("a.txt", io.BytesIO(petit)), ("b.txt", io.BytesIO(trop))],
            garde_fous=GardeFousIngestion(taille_max_source_octets=1024),
        )
    assert refus.value.motif == "source-trop-volumineuse"
    assert refus.value.index == 1


def test_les_octets_recus_sont_plafonnes_et_pas_la_taille_annoncee() -> None:
    """La taille vient du navigateur : c'est ce qui **arrive** qui est plafonné.

    Une déclaration honnête accompagnée d'un flux plus gros ne doit pas passer :
    le refus a lieu pendant l'écriture, tranche par tranche, comme à la réception
    d'un téléversement (#317).
    """
    with pytest.raises(SourceRefusee) as refus:
        apercu_sources(
            [{"type": "fichier", "nom": "menteur.txt", "taille": 10}],
            fichiers=[("menteur.txt", io.BytesIO(b"y" * 4096))],
            garde_fous=GardeFousIngestion(taille_max_source_octets=1024),
        )
    assert refus.value.motif == "source-trop-volumineuse"


def test_un_decompte_de_fichiers_faux_est_refuse() -> None:
    """Deux listes ordonnées et pas de correspondance : mal apparier serait pire.

    Associer le mauvais document au mauvais nom produirait un rapport crédible
    et faux — le refus est donc préféré au rapprochement au hasard.
    """
    with pytest.raises(SourceRefusee) as refus:
        apercu_sources([_declarer("a.txt", b"ok")], fichiers=[])
    assert refus.value.motif == "apercu-sans-octets"


def test_un_dossier_de_references_rend_une_ligne_par_fichier(maison: Path) -> None:
    """Le dossier est une source, ses fichiers sont ses `entrees` (#316)."""
    references = maison / "references"
    references.mkdir()
    (references / "specification.md").write_text("# Spec\n\nDeux phrases.", encoding="utf-8")
    (references / "logo.png").write_bytes(b"\x89PNG\r\n")

    rapport = apercu_sources([{"type": "dossier", "chemin": str(references)}])

    (lecture,) = rapport.lectures
    assert lecture.type == "dossier"
    assert {entree.nom for entree in lecture.entrees} == {"specification.md", "logo.png"}
    assert {entree.etat for entree in lecture.entrees} == {"lu", "ignore"}


@pytest.fixture()
def maison(_maison_isolee: Path) -> Path:
    """Le dossier utilisateur factice, sous un nom lisible en argument de test."""
    return _maison_isolee


# --- La route : la forme du contrat (docs/05 §6.9) --------------------------


def test_la_route_rend_le_rapport_du_fichier_depose(client: TestClient) -> None:
    """`POST /api/sources/apercu` : multipart en entrée, `RapportLecture` en sortie."""
    reponse = client.post(
        "/api/sources/apercu",
        data={"sources": json.dumps([_declarer("cdc.md", b"# Objectif\n\nRefondre.")])},
        files=[("fichier", ("cdc.md", b"# Objectif\n\nRefondre.", "text/markdown"))],
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["tokens"] > 0
    assert [lecture["nom"] for lecture in corps["lectures"]] == ["cdc.md"]
    assert corps["lectures"][0]["etat"] == "lu"


def test_la_route_rend_un_rapport_vide_sans_source(client: TestClient) -> None:
    """Aucune source : un rapport vide, pas une erreur — l'écran s'ouvre là-dessus."""
    reponse = client.post("/api/sources/apercu", data={"sources": "[]"})
    assert reponse.status_code == 200
    assert reponse.json() == {"tokens": 0, "lectures": []}


def test_la_route_refuse_une_url_non_suivable_avec_son_motif(client: TestClient) -> None:
    """Un refus est motivé et situé : `{motif, message, index}`, jamais une phrase nue."""
    reponse = client.post(
        "/api/sources/apercu",
        data={
            "sources": json.dumps(
                [
                    {"type": "url", "valeur": "https://exemple.test/spec"},
                    {"type": "url", "valeur": "file:///etc/passwd"},
                ]
            )
        },
    )
    assert reponse.status_code == 422
    detail = reponse.json()["detail"]
    assert detail["motif"] == "url-non-suivable"
    assert detail["index"] == 1


def test_la_route_refuse_un_type_inconnu(client: TestClient) -> None:
    """Un type hors `fichier`/`dossier`/`url` est refusé, jamais ignoré (#315)."""
    reponse = client.post(
        "/api/sources/apercu",
        data={"sources": json.dumps([{"type": "presse-papier", "valeur": "…"}])},
    )
    assert reponse.status_code == 422
    assert reponse.json()["detail"]["motif"] == "type-inconnu"


def test_la_route_refuse_un_json_illisible(client: TestClient) -> None:
    """Le corps est une saisie comme une autre : il refuse avec un motif."""
    reponse = client.post("/api/sources/apercu", data={"sources": "{pas du json"})
    assert reponse.status_code == 422
    assert reponse.json()["detail"]["motif"] == "sources-illisibles"


# --- Le reste, différé au lot final (#323) ----------------------------------


def test_une_url_s_apercoit_sans_jamais_sortir_sur_le_reseau() -> None:
    """Le troisième type de source n'était exercé nulle part côté aperçu.

    L'injection `recuperer_url=` est le seul chemin par lequel une URL entre
    dans un aperçu : sans test, rien ne garantissait que le paramètre soit
    transmis jusqu'à l'extraction.
    """
    appelees: list[str] = []

    def _recuperer(url: str) -> str:
        appelees.append(url)
        return "# Spécification\n\nLe contenu de la page."

    rapport = apercu_sources(
        [{"type": "url", "valeur": "https://exemple.test/spec"}],
        recuperer_url=_recuperer,
    )
    (lecture,) = rapport.lectures
    assert appelees == ["https://exemple.test/spec"]
    assert lecture.etat == "lu"
    assert "Spécification" in lecture.markdown


def test_le_cumul_des_octets_recus_est_plafonne_lui_aussi() -> None:
    """Deux fichiers licites l'un après l'autre peuvent être illicites ensemble.

    Le plafond **par source** était couvert, le plafond **total** ne l'était pas
    — c'est pourtant celui qui borne le coût d'un aperçu.
    """
    moitie = b"z" * 700
    with pytest.raises(SourceRefusee) as refus:
        apercu_sources(
            [_declarer("a.txt", moitie), _declarer("b.txt", moitie)],
            fichiers=[("a.txt", io.BytesIO(moitie)), ("b.txt", io.BytesIO(moitie))],
            garde_fous=GardeFousIngestion(
                taille_max_source_octets=1024, taille_max_totale_octets=1024
            ),
        )
    assert refus.value.motif == "ingestion-trop-volumineuse"
    assert refus.value.index == 1


def test_le_cumul_se_compte_sur_les_octets_recus_et_non_sur_ceux_annonces() -> None:
    """Le pendant du test précédent, sur le seul chemin qui compte vraiment.

    Là, les tailles **déclarées** dépassaient déjà : le refus tombait avant que
    le moindre octet ne soit lu. Ici elles sont minuscules et les octets, eux,
    débordent — c'est le cas d'un client qui annonce douze octets pour douze
    mégaoctets, et le seul où le plafond cumulé doit se défendre **pendant**
    l'écriture. La taille annoncée vient du navigateur : elle ne peut pas être
    ce sur quoi le budget d'ingestion s'appuie.
    """
    moitie = b"z" * 700
    with pytest.raises(SourceRefusee) as refus:
        apercu_sources(
            [
                {"type": "fichier", "nom": "a.txt", "taille": 10},
                {"type": "fichier", "nom": "b.txt", "taille": 10},
            ],
            fichiers=[("a.txt", io.BytesIO(moitie)), ("b.txt", io.BytesIO(moitie))],
            garde_fous=GardeFousIngestion(
                taille_max_source_octets=1024, taille_max_totale_octets=1024
            ),
        )
    assert refus.value.motif == "ingestion-trop-volumineuse"
    assert refus.value.index == 1


def test_un_apercu_refuse_ne_laisse_pas_ses_octets_derriere_lui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le ménage est dans un `finally` : il doit valoir aussi sur le chemin d'échec.

    C'est le cas qui compte le plus — un refus écrit un fichier **partiel**, et
    c'est précisément ce qu'il ne faut pas conserver.
    """
    jetables = tmp_path / "jetables"
    jetables.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(jetables))

    trop = b"x" * 4096
    with pytest.raises(SourceRefusee):
        apercu_sources(
            [_declarer("gros.txt", trop)],
            fichiers=[("gros.txt", io.BytesIO(trop))],
            garde_fous=GardeFousIngestion(taille_max_source_octets=1024),
        )

    assert list(jetables.iterdir()) == []


def test_la_route_apercoit_plusieurs_fichiers_dans_l_ordre_declare(
    client: TestClient,
) -> None:
    """Un multipart transporte deux listes ordonnées, pas une correspondance.

    L'ordre est ce qui décide de ce qui entre quand le budget s'épuise : un
    aperçu qui l'inverserait annoncerait un lancement qui n'aura pas lieu.
    """
    premier = b"# Premier\n\nUn contenu."
    second = b"# Second\n\nUn autre contenu."
    reponse = client.post(
        "/api/sources/apercu",
        data={
            "sources": json.dumps(
                [_declarer("un.md", premier), _declarer("deux.md", second)]
            )
        },
        files=[
            ("fichier", ("un.md", premier, "text/markdown")),
            ("fichier", ("deux.md", second, "text/markdown")),
        ],
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert [lecture["nom"] for lecture in corps["lectures"]] == ["un.md", "deux.md"]
    assert corps["tokens"] == sum(lecture["tokens"] for lecture in corps["lectures"])


def test_la_route_refuse_un_decompte_de_fichiers_faux(client: TestClient) -> None:
    """Deux déclarations pour un seul flux : rapprocher au hasard serait crédible et faux."""
    contenu = b"# Un\n"
    reponse = client.post(
        "/api/sources/apercu",
        data={
            "sources": json.dumps(
                [_declarer("un.md", contenu), _declarer("deux.md", contenu)]
            )
        },
        files=[("fichier", ("un.md", contenu, "text/markdown"))],
    )
    assert reponse.status_code == 422
    assert reponse.json()["detail"]["motif"] == "apercu-sans-octets"
