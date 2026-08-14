"""Le dépôt des sources téléversées (#317) : recevoir des octets, puis les rattacher à un run.

Le lot 7 a différé toute sa couverture au lot final (checklist de #314), et c'est
le module de la Phase 8 qui en avait le plus besoin : il **écrit sur le disque**
un contenu venu de l'extérieur, à un endroit calculé à partir d'un nom que le
navigateur propose. Les trois questions qu'il faut donc lui poser :

1. **ce qui entre est-il assaini** — un nom de fichier venu d'un navigateur est
   le vecteur classique de la traversée de répertoire, et un identifiant renvoyé
   par un client redevient un segment de chemin à la relecture ;
2. **un refus laisse-t-il quelque chose derrière lui** — c'est la promesse
   centrale du module (« rien n'est tronqué à l'entrée ») : un fichier à moitié
   reçu serait rapporté « lu » par le rapport de lecture, et le brief conclurait
   sur un document amputé sans que personne ne puisse le voir ;
3. **le dépôt fait-il foi sur le client** — nom et taille d'une source
   téléversée viennent des octets reçus, jamais de ce que la requête annonce,
   sans quoi le plafond par source se contourne en déclarant douze octets.

Les trois se mesurent **sur le disque** et non sur une intention : chaque test de
refus relit le dépôt pour vérifier qu'il est vide. Aucun réseau, aucun backend —
le module reçoit un flux binaire quelconque, ce qui est précisément ce qui le
rend testable sans requête HTTP.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import IO, Any

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.engine.guardrails import GardeFousIngestion
from maestro.sources import SourceRefusee, emplacement_ingestion
from maestro.sources.modele import TYPE_DOSSIER, TYPE_FICHIER, TYPE_URL
from maestro.sources.televersement import (
    DOSSIER_TELEVERSEMENTS,
    ID_TELEVERSEMENT,
    DepotTeleversements,
    declarer_televersements,
)

CONTENU = "Cahier des charges\n\nRefondre l'écran de lancement.".encode()


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier utilisateur factice — même raison qu'en #221/#223 et #315.

    Sous Windows, le `tmp_path` de pytest vit dans `AppData/Local/Temp`, que la
    validation de racine refuse à raison : sans cette isolation, les chemins de
    ces tests seraient refusés pour une bonne raison, mais pas celle qu'ils
    mesurent.
    """
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


@pytest.fixture(autouse=True)
def _ingestion_jetable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """L'emplacement d'ingestion pointé sur un dossier jetable, jamais `core/`."""
    racine = tmp_path / "ingestion"
    monkeypatch.setenv("MAESTRO_INGESTION_DIR", str(racine))
    return racine


@pytest.fixture()
def depot(tmp_path: Path) -> DepotTeleversements:
    """Un dépôt isolé, plafonds par défaut."""
    return DepotTeleversements(tmp_path / "depot")


def _entrees(depot: DepotTeleversements) -> list[Path]:
    """Ce que le dépôt porte réellement — mesuré sur le disque, jamais déduit."""
    racine = depot._racine  # noqa: SLF001 - l'état disque est justement le sujet
    return sorted(racine.iterdir()) if racine.is_dir() else []


class FluxCompte(io.BytesIO):
    """Un flux qui retient combien d'octets on lui a réellement pris.

    C'est ce qui distingue « refusé pendant la réception » de « reçu puis
    jeté » : sans ce compteur, les deux régimes rendent le même refus et le
    fichier de 2 Gio est quand même passé par le disque.
    """

    def __init__(self, contenu: bytes) -> None:
        super().__init__(contenu)
        self.lus = 0

    def read(self, taille: int | None = -1, /) -> bytes:
        tranche = super().read(taille)
        self.lus += len(tranche)
        return tranche


class FluxInterrompu(io.RawIOBase):
    """Un flux qui rend une tranche puis lève une `BaseException`.

    Reproduit ce que le module dit prévoir : l'annulation de la tâche qui portait
    la copie (client déconnecté, arrêt de l'app). `KeyboardInterrupt` n'hérite pas
    d'`Exception` — un `except Exception` laisserait donc le fichier tronqué.
    """

    def __init__(self) -> None:
        self.tranches = 0

    def read(self, taille: int | None = -1, /) -> bytes:
        self.tranches += 1
        if self.tranches == 1:
            return b"debut de document"
        raise KeyboardInterrupt("copie interrompue")


# --- ① Accueillir : des octets, un identifiant, un nom assaini ---------------


def test_des_octets_accueillis_se_relisent_sous_leur_identifiant(
    depot: DepotTeleversements,
) -> None:
    """Le cas nominal : ce qui est déposé se retrouve, à l'octet près."""
    televerse = depot.accueillir("cdc.md", io.BytesIO(CONTENU))
    assert ID_TELEVERSEMENT.match(televerse.id)
    assert televerse.nom == "cdc.md"
    assert televerse.taille == len(CONTENU)
    assert televerse.chemin.read_bytes() == CONTENU
    relu = depot.lire(televerse.id)
    assert relu == televerse


def test_le_dossier_du_depot_est_hors_de_tout_run_et_de_tout_projet(
    _ingestion_jetable: Path,
) -> None:
    """Téléverser dépose la matière avant qu'un run existe — donc à côté d'eux."""
    depot = DepotTeleversements.default()
    televerse = depot.accueillir("note.md", io.BytesIO(b"un mot"))
    assert televerse.chemin.parent.parent == _ingestion_jetable / DOSSIER_TELEVERSEMENTS


def test_le_depot_par_defaut_ne_cree_rien_par_sa_seule_lecture(
    _ingestion_jetable: Path,
) -> None:
    """Un déploiement où personne ne téléverse ne pose aucun dossier (patron de #315)."""
    DepotTeleversements.default()
    assert not _ingestion_jetable.exists()


def test_le_dossier_des_televersements_ne_peut_pas_etre_pris_pour_un_run() -> None:
    """La collision dépôt ↔ run est impossible **par construction**, pas improbable.

    `DOSSIER_TELEVERSEMENTS` commence par un tiret bas, or `ID_RUN` (#315) exige
    une alphanumérique en tête : aucun `run_id` ne peut donc jamais désigner ce
    dossier. C'est ce que le commentaire du module affirme — on le mesure plutôt
    que de le relire.
    """
    with pytest.raises(SourceRefusee) as capture:
        emplacement_ingestion(DOSSIER_TELEVERSEMENTS)
    assert capture.value.motif == "run-invalide"


@pytest.mark.parametrize(
    "nom",
    [
        "../../evasion.md",  # traversée de répertoire, le classique
        "..",
        "/etc/passwd",
        "C:\\Windows\\system32\\cle.md",
        "note.md:cache",  # flux de données alterné NTFS : un contenu invisible
    ],
)
def test_un_nom_qui_est_un_chemin_est_refuse_avant_toute_ecriture(
    depot: DepotTeleversements, nom: str
) -> None:
    """Le nom vient du navigateur, donc de l'extérieur — et il est refusé **avant** le mkdir.

    Le même `nom_de_fichier` que la résolution (#315) : deux assainisseurs
    écrits séparément divergeraient, et c'est celui des deux qui oublie une
    classe de caractères qui ferait la faille.
    """
    with pytest.raises(SourceRefusee) as capture:
        depot.accueillir(nom, io.BytesIO(CONTENU))
    assert capture.value.motif == "nom-invalide"
    assert _entrees(depot) == []


def test_un_nom_absent_est_refuse(depot: DepotTeleversements) -> None:
    """Un champ `filename` vide côté navigateur ne doit pas produire un fichier sans nom."""
    with pytest.raises(SourceRefusee) as capture:
        depot.accueillir("   ", io.BytesIO(CONTENU))
    assert capture.value.motif == "nom-absent"
    assert _entrees(depot) == []


def test_deux_fichiers_de_meme_nom_ne_s_ecrasent_pas(depot: DepotTeleversements) -> None:
    """Un identifiant par dépôt : deux `cdc.md` cohabitent au lieu de se remplacer."""
    premier = depot.accueillir("cdc.md", io.BytesIO(b"version 1"))
    second = depot.accueillir("cdc.md", io.BytesIO(b"version 2"))
    assert premier.id != second.id
    assert premier.chemin.read_bytes() == b"version 1"
    assert second.chemin.read_bytes() == b"version 2"


# --- ② Un refus ne laisse rien derrière lui ----------------------------------


def test_un_fichier_trop_gros_est_refuse_et_le_depot_reste_vide(
    tmp_path: Path,
) -> None:
    """Le plafond par source (ENF-07), appliqué à la réception."""
    depot = DepotTeleversements(
        tmp_path / "depot", garde_fous=GardeFousIngestion(taille_max_source_octets=1_000)
    )
    with pytest.raises(SourceRefusee) as capture:
        depot.accueillir("gros.bin", io.BytesIO(b"x" * 5_000))
    assert capture.value.motif == "source-trop-volumineuse"
    assert _entrees(depot) == []


def test_le_refus_tombe_pendant_la_reception_et_non_apres(tmp_path: Path) -> None:
    """La distinction que le module existe pour tenir.

    Un fichier de 2 Gio ne doit pas d'abord être écrit sur le disque pour être
    ensuite déclaré trop gros. On le mesure sur les octets réellement **pris** au
    flux : la lecture s'arrête à la première tranche qui dépasse, pas à la fin.
    """
    depot = DepotTeleversements(
        tmp_path / "depot", garde_fous=GardeFousIngestion(taille_max_source_octets=1_000)
    )
    flux = FluxCompte(b"x" * (4 * 1024 * 1024))
    with pytest.raises(SourceRefusee):
        depot.accueillir("gros.bin", flux)
    assert flux.lus < len(flux.getvalue())


def test_le_cumul_de_l_appel_est_plafonne_lui_aussi(tmp_path: Path) -> None:
    """Sans `deja_recu`, vingt fichiers sous le plafond unitaire passeraient tous."""
    depot = DepotTeleversements(
        tmp_path / "depot",
        garde_fous=GardeFousIngestion(
            taille_max_source_octets=10_000, taille_max_totale_octets=1_000
        ),
    )
    with pytest.raises(SourceRefusee) as capture:
        depot.accueillir("suite.bin", io.BytesIO(b"x" * 600), deja_recu=600)
    assert capture.value.motif == "ingestion-trop-volumineuse"
    assert _entrees(depot) == []


def test_l_index_du_fichier_fautif_voyage_avec_le_refus(tmp_path: Path) -> None:
    """« Un fichier est trop gros » sans dire lequel obligerait à tout recommencer."""
    depot = DepotTeleversements(
        tmp_path / "depot", garde_fous=GardeFousIngestion(taille_max_source_octets=10)
    )
    with pytest.raises(SourceRefusee) as capture:
        depot.accueillir("troisieme.bin", io.BytesIO(b"x" * 100), index=2)
    assert capture.value.index == 2
    assert "Fichier 3" in str(capture.value)


def test_une_interruption_n_abandonne_pas_de_fichier_tronque(
    depot: DepotTeleversements,
) -> None:
    """`except BaseException` et non `except Exception` : le choix est mesuré ici.

    Une annulation de la tâche qui porte la copie laisserait sinon dans le dépôt
    un fichier tronqué **indiscernable** d'un téléversement complet — le rapport
    de lecture le dirait « lu ».
    """
    with pytest.raises(KeyboardInterrupt):
        depot.accueillir("interrompu.md", FluxInterrompu())
    assert _entrees(depot) == []


# --- ③ Relire, rattacher, oublier --------------------------------------------


def test_un_identifiant_inconnu_ne_designe_aucun_televersement(
    depot: DepotTeleversements,
) -> None:
    """None plutôt qu'une exception : c'est l'appelant qui sait dire de quel refus il s'agit."""
    assert depot.lire("a1b2c3d4e5f6") is None


@pytest.mark.parametrize(
    "identifiant",
    [
        "",
        "court",  # moins de 6 caractères
        "x" * 65,  # plus de 64
        "avec-tiret",
        "../voisin",  # la remontée d'un cran, celle qui compte
        "sous/dossier",
    ],
)
def test_un_identifiant_non_conforme_est_ecarte_sans_toucher_au_disque(
    depot: DepotTeleversements, identifiant: str
) -> None:
    """Un identifiant revient d'un client : il n'a pas à devenir un chemin pour être écarté."""
    assert depot.lire(identifiant) is None


def test_la_relecture_ne_remonte_pas_hors_du_depot(
    depot: DepotTeleversements, tmp_path: Path
) -> None:
    """Le pendant concret du test précédent : un voisin du dépôt reste inatteignable."""
    voisin = tmp_path / "depot" / ".." / "voisin"
    voisin.mkdir(parents=True)
    (voisin / "secret.md").write_bytes(b"rien a voir ici")
    assert depot.lire("../voisin") is None


def test_un_dossier_vide_ne_designe_aucun_televersement(
    depot: DepotTeleversements, tmp_path: Path
) -> None:
    """Le nom du téléversement est celui du fichier qu'il contient : sans fichier, rien."""
    (tmp_path / "depot" / "a1b2c3d4e5f6").mkdir(parents=True)
    assert depot.lire("a1b2c3d4e5f6") is None


def test_rattacher_copie_les_octets_et_laisse_l_original(
    depot: DepotTeleversements, tmp_path: Path
) -> None:
    """Le rattachement **copie** : relancer le même objectif ne doit pas faire re-téléverser."""
    televerse = depot.accueillir("cdc.md", io.BytesIO(CONTENU))
    cible = tmp_path / "ingestion" / "a1b2c3d4e5f6" / "cdc.md"
    rendu = depot.rattacher(televerse.id, cible)
    assert rendu == cible
    assert cible.read_bytes() == CONTENU
    assert televerse.chemin.read_bytes() == CONTENU


def test_rattacher_cree_le_dossier_du_run(
    depot: DepotTeleversements, tmp_path: Path
) -> None:
    """`emplacement_ingestion` (#315) se contente de calculer ; écrire, c'est ici."""
    televerse = depot.accueillir("cdc.md", io.BytesIO(CONTENU))
    cible = tmp_path / "jamais" / "cree" / "cdc.md"
    depot.rattacher(televerse.id, cible)
    assert cible.is_file()


def test_rattacher_un_inconnu_leve_au_lieu_de_sauter_la_copie(
    depot: DepotTeleversements, tmp_path: Path
) -> None:
    """Une copie silencieusement sautée rendrait un run travaillant sur une matière absente."""
    with pytest.raises(SourceRefusee) as capture:
        depot.rattacher("a1b2c3d4e5f6", tmp_path / "cible.md")
    assert capture.value.motif == "televersement-inconnu"


def test_oublier_retire_le_televersement_et_reste_muet_sur_un_inconnu(
    depot: DepotTeleversements,
) -> None:
    """Best-effort : c'est ce qui permet à un appel refusé de ne rien laisser."""
    televerse = depot.accueillir("cdc.md", io.BytesIO(CONTENU))
    depot.oublier(televerse.id)
    assert depot.lire(televerse.id) is None
    depot.oublier(televerse.id)  # deux fois : aucun effet, aucune erreur


def test_oublier_ne_supprime_rien_hors_du_depot(
    depot: DepotTeleversements, tmp_path: Path
) -> None:
    """Le garde-fou qui compte : `oublier` prend un identifiant venu d'un client."""
    voisin = tmp_path / "depot" / ".." / "voisin"
    voisin.mkdir(parents=True)
    (voisin / "secret.md").write_bytes(b"rien a voir ici")
    depot.oublier("../voisin")
    assert (voisin / "secret.md").is_file()


# --- ④ Déclarer : le dépôt fait foi, jamais le client ------------------------


def test_un_renvoi_est_complete_par_le_nom_et_la_taille_du_depot(
    depot: DepotTeleversements,
) -> None:
    """Un client qui annoncerait douze octets pour 12 Mio passerait sinon le plafond."""
    televerse = depot.accueillir("cdc.md", io.BytesIO(CONTENU))
    declarees, identifiants = declarer_televersements(
        [{"type": TYPE_FICHIER, "id": televerse.id, "nom": "menteur.md", "taille": 12}],
        depot=depot,
    )
    assert declarees == [
        {"type": TYPE_FICHIER, "nom": "cdc.md", "taille": len(CONTENU)}
    ]
    assert identifiants == (televerse.id,)


def test_une_declaration_nue_passe_telle_quelle(depot: DepotTeleversements) -> None:
    """La seconde forme d'une source `fichier` (docs/24 §3.2) : `nom` + `taille`, sans octets.

    Elle ressortira `ignore` / `source-absente` au rapport de lecture — ce que le
    rapport existe précisément pour dire.
    """
    nue = {"type": TYPE_FICHIER, "nom": "cdc.md", "taille": 42}
    declarees, identifiants = declarer_televersements([nue], depot=depot)
    assert declarees == [nue]
    assert identifiants == ("",)


def test_les_identifiants_restent_alignes_sur_les_index(
    depot: DepotTeleversements,
) -> None:
    """C'est cet alignement qui permettra de rattacher les octets une fois le run connu."""
    televerse = depot.accueillir("cdc.md", io.BytesIO(CONTENU))
    declarees, identifiants = declarer_televersements(
        [
            {"type": TYPE_URL, "valeur": "https://exemple.test/spec"},
            {"type": TYPE_FICHIER, "id": televerse.id},
            {"type": TYPE_DOSSIER, "nom": "maquettes", "chemin": "/refs/maquettes"},
        ],
        depot=depot,
    )
    assert len(declarees) == 3
    assert identifiants == ("", televerse.id, "")


def test_un_renvoi_inconnu_est_refuse_avec_son_index(
    depot: DepotTeleversements,
) -> None:
    """Un identifiant déjà retiré ne doit pas produire un run à la matière absente."""
    with pytest.raises(SourceRefusee) as capture:
        declarer_televersements(
            [
                {"type": TYPE_URL, "valeur": "https://exemple.test/spec"},
                {"type": TYPE_FICHIER, "id": "a1b2c3d4e5f6"},
            ],
            depot=depot,
        )
    assert capture.value.motif == "televersement-inconnu"
    assert capture.value.index == 1


@pytest.mark.parametrize("brut", [None, "pas une liste", 42, {"type": TYPE_FICHIER}])
def test_ce_qui_n_est_pas_une_liste_ressort_inchange(
    depot: DepotTeleversements, brut: Any
) -> None:
    """Le refus de forme appartient à `resoudre_sources` : le dupliquer ferait deux messages."""
    declarees, identifiants = declarer_televersements(brut, depot=depot)
    assert declarees is brut
    assert identifiants == ()


@pytest.mark.parametrize(
    "entree",
    [
        {"type": TYPE_URL, "id": "a1b2c3d4e5f6"},
        {"type": TYPE_DOSSIER, "id": "a1b2c3d4e5f6"},
        {"type": TYPE_FICHIER, "id": "   "},
        "pas un objet",
    ],
)
def test_ce_qui_ne_renvoie_pas_a_un_televersement_passe_sans_lecture(
    depot: DepotTeleversements, entree: Any
) -> None:
    """Seul un `fichier` porteur d'un `id` est un renvoi — le reste n'interroge pas le dépôt."""
    declarees, identifiants = declarer_televersements([entree], depot=depot)
    assert declarees == [entree]
    assert identifiants == ("",)


# --- ⑤ La route `POST /api/sources` ------------------------------------------


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """L'app réelle, dépôt de téléversement injecté sur un dossier jetable."""
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        televersements=DepotTeleversements(tmp_path / "depot"),
    )
    with TestClient(app) as client:
        yield client


def _envoi(nom: str, contenu: bytes) -> tuple[str, tuple[str, IO[bytes], str]]:
    """Un fichier de formulaire, tel que l'écran de composition l'envoie."""
    return ("fichier", (nom, io.BytesIO(contenu), "text/markdown"))


def test_la_route_rend_un_identifiant_par_fichier_et_le_cumul(client: TestClient) -> None:
    """`POST /api/sources` : 201, un `id` par fichier, et le total reçu (docs/05 §6.8)."""
    reponse = client.post(
        "/api/sources",
        files=[_envoi("cdc.md", CONTENU), _envoi("notes.md", b"trois mots ici")],
    )
    assert reponse.status_code == 201
    corps = reponse.json()
    assert [source["nom"] for source in corps["sources"]] == ["cdc.md", "notes.md"]
    assert corps["total_octets"] == len(CONTENU) + len(b"trois mots ici")
    assert {source["type"] for source in corps["sources"]} == {TYPE_FICHIER}


def test_la_route_ne_publie_jamais_le_chemin_des_octets(client: TestClient) -> None:
    """Un détail d'implantation du serveur — le client désigne sa matière par son `id`."""
    corps = client.post("/api/sources", files=[_envoi("cdc.md", CONTENU)]).json()
    assert set(corps["sources"][0]) == {"id", "type", "nom", "taille"}


def test_la_route_borne_le_nombre_de_fichiers_avant_de_toucher_au_disque(
    tmp_path: Path,
) -> None:
    """Le plafond de **nombre** se juge sur la requête : rien n'est écrit pour être ensuite jeté."""
    depot = DepotTeleversements(
        tmp_path / "depot", garde_fous=GardeFousIngestion(nb_max_sources=2)
    )
    app = create_app(
        bus=InMemoryEventBus(), state=ControlTowerState(), televersements=depot
    )
    with TestClient(app) as client:
        reponse = client.post(
            "/api/sources",
            files=[_envoi(f"f{i}.md", b"court") for i in range(3)],
        )
    assert reponse.status_code == 422
    assert reponse.json()["detail"]["motif"] == "trop-de-sources"
    assert _entrees(depot) == []


def test_un_refus_retire_les_fichiers_deja_acceptes_du_meme_appel(
    tmp_path: Path,
) -> None:
    """L'atomicité de l'appel — la raison d'être d'`oublier`.

    Si le second fichier déborde, le premier n'a pas à rester dans le dépôt sous
    un identifiant que personne n'a reçu : il y serait pour toujours, aucun run
    ne le réclamant jamais.
    """
    depot = DepotTeleversements(
        tmp_path / "depot", garde_fous=GardeFousIngestion(taille_max_source_octets=100)
    )
    app = create_app(
        bus=InMemoryEventBus(), state=ControlTowerState(), televersements=depot
    )
    with TestClient(app) as client:
        reponse = client.post(
            "/api/sources",
            files=[_envoi("petit.md", b"court"), _envoi("gros.bin", b"x" * 500)],
        )
    assert reponse.status_code == 422
    detail = reponse.json()["detail"]
    assert detail["motif"] == "source-trop-volumineuse"
    assert detail["index"] == 1
    assert _entrees(depot) == []


def test_la_route_refuse_un_fichier_au_nom_qui_est_un_chemin(client: TestClient) -> None:
    """Le refus du lot 1 traverse la route avec son motif, pas en 500."""
    reponse = client.post("/api/sources", files=[_envoi("../../evasion.md", CONTENU)])
    assert reponse.status_code == 422
    assert reponse.json()["detail"]["motif"] == "nom-invalide"


# --- ⑥ Du dépôt au run : les octets rejoignent l'emplacement d'ingestion ------


def test_les_octets_televerses_rejoignent_l_emplacement_du_run(
    tmp_path: Path, _ingestion_jetable: Path
) -> None:
    """Le bout en bout de #317 : téléverser, puis lancer en renvoyant à l'identifiant.

    C'est l'ordre obligé du service (`_composer`) qu'on vérifie ici : compléter
    la déclaration depuis le dépôt, résoudre pour savoir **où** la matière doit
    être, puis y copier les octets — l'emplacement dépend du `run_id`, qui
    n'existe pas au moment du téléversement.
    """
    depot = DepotTeleversements(tmp_path / "depot")
    app = create_app(
        bus=InMemoryEventBus(), state=ControlTowerState(), televersements=depot
    )
    with TestClient(app) as client:
        depose = client.post("/api/sources", files=[_envoi("cdc.md", CONTENU)]).json()
        identifiant = depose["sources"][0]["id"]
        lancement = client.post(
            "/api/executions",
            json={
                "objectif": "Reprendre le cahier des charges",
                "sources": [{"type": TYPE_FICHIER, "id": identifiant}],
            },
        )
        assert lancement.status_code == 202
        resume = lancement.json()

    (source,) = resume["sources"]
    depose_dans_le_run = Path(source["chemin"])
    assert depose_dans_le_run == (_ingestion_jetable / resume["run_id"] / "cdc.md").resolve()
    assert depose_dans_le_run.read_bytes() == CONTENU
    # Le dépôt garde les siens : relancer après un échec ne re-téléverse pas.
    assert depot.lire(identifiant) is not None


def test_un_renvoi_inconnu_refuse_le_lancement_avec_son_motif(tmp_path: Path) -> None:
    """Un identifiant périmé coûte un 422 motivé, jamais un run à la matière absente."""
    depot = DepotTeleversements(tmp_path / "depot")
    app = create_app(
        bus=InMemoryEventBus(), state=ControlTowerState(), televersements=depot
    )
    with TestClient(app) as client:
        reponse = client.post(
            "/api/executions",
            json={
                "objectif": "Reprendre le cahier des charges",
                "sources": [{"type": TYPE_FICHIER, "id": "a1b2c3d4e5f6"}],
            },
        )
    assert reponse.status_code == 422
    detail = reponse.json()["detail"]
    assert detail["motif"] == "televersement-inconnu"
    assert detail["index"] == 0
