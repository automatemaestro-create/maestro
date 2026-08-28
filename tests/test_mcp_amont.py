"""Client du registre MCP officiel et son miroir (#675, lot 1/6 — testé au lot 6, #680).

**Aucun test ne parle au registre en direct**, et ce n'est pas une précaution de
style : l'amont est en préversion et annonce « no uptime or data durability
guarantees ». Une suite qui en dépendrait rendrait un rouge qui n'apprend rien —
ni sur notre code, ni sur le sien. L'amont est donc joué par un
`httpx2.MockTransport` qui n'ouvre aucune socket : « sans réseau » est ici une
propriété de construction, pas une intention.

⚠ **Ce que ce fichier compte, ce sont des APPELS, jamais des durées** (règle de
#577). « L'amont n'est pas appelé » se prouve sur `len(amont.requetes)` ; un
chronomètre en CI mesurerait la charge de la machine et rendrait vert un jour,
rouge le lendemain, sur du code identique. Le même compteur porte l'autre moitié
du contrat : *quelles* requêtes ont été faites (`version`, `limit`, `cursor`,
`updated_since`, `include_deleted`), c'est-à-dire ce que le module a demandé et
non ce qu'il croit avoir demandé.

Les quatre situations que le critère d'acceptation nomme ont chacune leur test et
sont éprouvées **une par une** : la pagination par `cursor`, l'incrément par
`updated_since`, le `deleted` qui **sort** du miroir, et l'amont injoignable qui
laisse le miroir précédent **en place**. Une suite qui les vérifierait ensemble
ne dirait pas laquelle garde.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest

from maestro.agents.mcp_amont import (
    CLE_META_OFFICIELLE,
    LIMITE_PAGE,
    MODE_COMPLET,
    MODE_INCREMENTAL,
    PERIODE_MINIMALE_S,
    STATUT_DEPRECIE,
    STATUT_SUPPRIME,
    AmontHorsContrat,
    AmontInjoignable,
    AmontTropLent,
    ClientRegistreOfficiel,
    EntreeAmont,
    MiroirAmont,
)

AMONT = "https://registre.test"
SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"

#: L'en-tête `Date` que sert l'amont factice — l'horloge dont la borne incrémentale
#: est tirée. Une constante et non `datetime.now()` : la borne attendue se lit
#: alors dans le test, au lieu de se recalculer avec le code qu'il vérifie.
DATE_HTTP = "Fri, 28 Aug 2026 06:00:00 GMT"
#: `DATE_HTTP` moins `MARGE_BORNE_S` — ce que le module doit poser en borne.
BORNE_ATTENDUE = "2026-08-28T05:59:59Z"


def entree(
    nom: str,
    *,
    version: str = "1.0.0",
    statut: str = "active",
    maj: str = "2026-08-01T10:00:00Z",
) -> dict[str, Any]:
    """Une enveloppe de listing amont (`{"server": …, "_meta": …}`) minimale mais réelle."""
    return {
        "server": {
            "$schema": SCHEMA,
            "name": nom,
            "description": f"serveur {nom}",
            "version": version,
            "remotes": [{"type": "streamable-http", "url": f"https://{nom.split('/')[-1]}.test/mcp"}],
        },
        "_meta": {
            CLE_META_OFFICIELLE: {
                "status": statut,
                "statusChangedAt": maj,
                "publishedAt": maj,
                "updatedAt": maj,
                "isLatest": True,
            }
        },
    }


def page(entrees: list[dict[str, Any]], suivant: str = "") -> dict[str, Any]:
    """Une page de `GET /v0.1/servers`, avec ou sans curseur de suite."""
    return {"servers": entrees, "metadata": {"nextCursor": suivant} if suivant else {}}


class AmontFactice:
    """Un registre en mémoire, servi par un transport qui n'ouvre aucune socket.

    Il **garde toutes les requêtes reçues** : c'est sur cette liste que se lisent
    aussi bien « l'amont n'a pas été appelé » que « il a été appelé avec
    `updated_since` ». Le curseur est la position de page (`p1`, `p2`…), une
    forme opaque comme celle de l'amont réel — ce que le module en fait est de le
    renvoyer tel quel, jamais de le lire.
    """

    def __init__(
        self,
        pages: list[dict[str, Any]],
        *,
        date: str = DATE_HTTP,
        panne: Exception | None = None,
        statut: int = 200,
        corps: str = "",
    ) -> None:
        self.pages = pages
        self.date = date
        self.panne = panne
        self.statut = statut
        self.corps = corps
        self.requetes: list[dict[str, str]] = []

    @property
    def appels(self) -> int:
        """Le nombre d'allers vers l'amont — le compteur du contrat (#577)."""
        return len(self.requetes)

    def _repond(self, requete: httpx2.Request) -> httpx2.Response:
        self.requetes.append(dict(requete.url.params))
        if self.panne is not None:
            raise self.panne
        entetes = {"date": self.date} if self.date else {}
        if self.statut != 200:
            return httpx2.Response(self.statut, text=self.corps, headers=entetes)
        if self.corps:
            return httpx2.Response(200, text=self.corps, headers=entetes)
        curseur = requete.url.params.get("cursor", "")
        rang = int(curseur[1:]) if curseur.startswith("p") else 0
        return httpx2.Response(200, json=self.pages[rang], headers=entetes)

    def transport(self) -> httpx2.Client:
        """Un client HTTP qui n'ouvre aucune socket — il répond depuis la mémoire."""
        return httpx2.Client(transport=httpx2.MockTransport(self._repond))

    def client(self, **options: Any) -> ClientRegistreOfficiel:
        """Un client du registre branché sur ce faux amont."""
        return ClientRegistreOfficiel(amont=AMONT, client=self.transport(), **options)


def miroir(racine: Path, **options: Any) -> MiroirAmont:
    """Un miroir sur un répertoire jetable, à la périodicité plancher."""
    options.setdefault("periode_s", PERIODE_MINIMALE_S)
    return MiroirAmont(racine, amont=AMONT, **options)


def noms(racine: Path) -> list[str]:
    """Les noms présents dans `miroir.jsonl`, dans l'ordre du fichier."""
    lignes = (racine / MiroirAmont.FICHIER_ENTREES).read_text(encoding="utf-8").splitlines()
    return [json.loads(ligne)["nom"] for ligne in lignes if ligne.strip()]


# --------------------------------------------------------------------------- #
# Pagination par curseur
# --------------------------------------------------------------------------- #


def test_la_pagination_suit_le_curseur_jusqu_a_epuisement() -> None:
    amont = AmontFactice(
        [
            page([entree("io.github.a/un")], suivant="p1"),
            page([entree("io.github.b/deux")], suivant="p2"),
            page([entree("io.github.c/trois")]),
        ]
    )
    moisson = amont.client().moissonner()
    assert [e.nom for e in moisson.entrees] == [
        "io.github.a/un",
        "io.github.b/deux",
        "io.github.c/trois",
    ]
    assert moisson.pages == 3
    assert moisson.vues == 3
    # Trois pages, trois allers : ni un de plus (une page redemandée), ni un de
    # moins (une page sautée). C'est le compteur, pas la durée, qui le dit.
    assert amont.appels == 3


def test_la_premiere_page_ne_porte_pas_de_curseur_et_les_suivantes_le_portent() -> None:
    amont = AmontFactice([page([entree("io.github.a/un")], suivant="p1"), page([])])
    amont.client().moissonner()
    assert "cursor" not in amont.requetes[0]
    assert amont.requetes[1]["cursor"] == "p1"


def test_chaque_page_demande_la_derniere_version_et_le_plafond_de_pagination() -> None:
    amont = AmontFactice([page([entree("io.github.a/un")])])
    amont.client().moissonner()
    assert amont.requetes[0]["version"] == "latest"
    # `limit` est plafonné à 100 par l'amont — au-delà il répond 422 (mesuré).
    assert amont.requetes[0]["limit"] == str(LIMITE_PAGE)


def test_un_curseur_deja_servi_est_une_pagination_qui_boucle() -> None:
    amont = AmontFactice([page([entree("io.github.a/un")], suivant="p0")])
    with pytest.raises(AmontHorsContrat, match="boucle"):
        amont.client().moissonner()


def test_une_enveloppe_sans_liste_servers_est_hors_contrat() -> None:
    amont = AmontFactice([{"metadata": {}}])
    with pytest.raises(AmontHorsContrat, match="servers"):
        amont.client().moissonner()


def test_une_entree_sans_nom_est_comptee_ignoree_et_la_passe_continue() -> None:
    amont = AmontFactice([page([{"server": {"version": "1.0.0"}}, entree("io.github.a/un")])])
    moisson = amont.client().moissonner()
    assert moisson.vues == 2
    assert moisson.ignorees == 1
    assert [e.nom for e in moisson.entrees] == ["io.github.a/un"]


def test_des_entrees_toutes_inexploitables_disent_que_le_schema_a_bouge() -> None:
    amont = AmontFactice([page([{"serveur": {"nom": "autre-forme"}}])])
    with pytest.raises(AmontHorsContrat, match="schéma amont a changé"):
        amont.client().moissonner()


def test_un_statut_http_fache_est_hors_contrat_avec_son_extrait() -> None:
    amont = AmontFactice([], statut=503, corps="maintenance")
    with pytest.raises(AmontHorsContrat, match="503"):
        amont.client().moissonner()


def test_une_reponse_qui_n_est_pas_du_json_est_hors_contrat() -> None:
    amont = AmontFactice([], corps="<html>oups</html>")
    with pytest.raises(AmontHorsContrat, match="JSON"):
        amont.client().moissonner()


def test_une_panne_reseau_est_un_amont_injoignable() -> None:
    amont = AmontFactice([], panne=httpx2.ConnectError("DNS"))
    with pytest.raises(AmontInjoignable):
        amont.client().moissonner()


def test_un_delai_depasse_est_un_amont_trop_lent_et_non_un_injoignable() -> None:
    # Les deux familles existent parce qu'elles n'appellent pas le même geste :
    # `TimeoutException` **hérite** de `HTTPError`, donc l'ordre des `except` du
    # module est ce qui les sépare — un test qui ne demanderait que `ErreurAmont`
    # laisserait cet ordre s'inverser sans rien dire.
    amont = AmontFactice([], panne=httpx2.ReadTimeout("trop long"))
    with pytest.raises(AmontTropLent):
        amont.client().moissonner()


def test_le_budget_de_la_passe_arrete_une_pagination_sans_fin() -> None:
    amont = AmontFactice([page([entree("io.github.a/un")], suivant="p0")])
    # Budget nul : la garde tombe au premier tour de boucle, sans qu'aucune durée
    # réelle n'ait à s'écouler — le test mesure la règle, pas la machine. Le
    # `appels == 0` dit exactement ce qui est prouvé : la garde est évaluée
    # **avant** l'aller, donc un amont qui ne répond plus ne peut pas faire
    # tourner la pagination indéfiniment.
    with pytest.raises(AmontTropLent, match="budget"):
        amont.client(budget_s=-1.0).moissonner()
    assert amont.appels == 0


def test_un_client_injecte_n_est_jamais_ferme_par_le_module() -> None:
    interne = AmontFactice([page([])]).transport()
    client = ClientRegistreOfficiel(amont=AMONT, client=interne)
    client.fermer()
    assert not interne.is_closed


# --------------------------------------------------------------------------- #
# Incrément par `updated_since`
# --------------------------------------------------------------------------- #


def test_sans_borne_la_requete_ne_porte_pas_d_incrementale() -> None:
    amont = AmontFactice([page([entree("io.github.a/un")])])
    amont.client().moissonner()
    assert "updated_since" not in amont.requetes[0]
    assert "include_deleted" not in amont.requetes[0]


def test_avec_une_borne_la_requete_demande_l_increment_et_les_supprimees() -> None:
    amont = AmontFactice([page([entree("io.github.a/un")])])
    amont.client().moissonner(depuis="2026-08-01T00:00:00Z")
    assert amont.requetes[0]["updated_since"] == "2026-08-01T00:00:00Z"
    # L'amont force déjà `include_deleted` dans ce mode ; on le passe quand même
    # — une requête doit énoncer ce qu'elle obtient.
    assert amont.requetes[0]["include_deleted"] == "true"


def test_le_premier_passage_moissonne_tout_et_le_second_incremente(tmp_path: Path) -> None:
    complet = AmontFactice([page([entree("io.github.a/un")])])
    premier = miroir(tmp_path).rafraichir(complet.client())
    assert premier.ok and premier.mode == MODE_COMPLET
    assert complet.requetes[0].get("updated_since") is None

    incremental = AmontFactice([page([entree("io.github.b/deux")])])
    second = miroir(tmp_path).rafraichir(incremental.client())
    assert second.ok and second.mode == MODE_INCREMENTAL
    # La borne du second passage est celle que le premier a posée — l'horloge de
    # l'amont, pas la nôtre.
    assert incremental.requetes[0]["updated_since"] == BORNE_ATTENDUE
    assert noms(tmp_path) == ["io.github.a/un", "io.github.b/deux"]


def test_la_borne_vient_de_l_en_tete_date_et_non_du_plus_grand_updated_at(
    tmp_path: Path,
) -> None:
    # `max(updatedAt)` est le repli et jamais le premier choix : la pagination
    # parcourt les noms dans l'ordre alphabétique, donc une entrée de début
    # d'alphabet modifiée pendant qu'on lit la fin resterait *sous* ce maximum et
    # serait manquée pour toujours.
    amont = AmontFactice([page([entree("io.github.a/un", maj="2026-08-02T00:00:00Z")])])
    compte_rendu = miroir(tmp_path).rafraichir(amont.client())
    assert compte_rendu.etat.borne_amont == BORNE_ATTENDUE
    assert compte_rendu.etat.borne_amont != "2026-08-02T00:00:00Z"


def test_sans_en_tete_lisible_la_borne_se_replie_sur_le_plus_grand_updated_at(
    tmp_path: Path,
) -> None:
    amont = AmontFactice(
        [
            page(
                [
                    entree("io.github.a/un", maj="2026-08-02T00:00:00Z"),
                    entree("io.github.b/deux", maj="2026-08-09T00:00:00Z"),
                ]
            )
        ],
        date="",
    )
    compte_rendu = miroir(tmp_path).rafraichir(amont.client())
    assert compte_rendu.etat.borne_amont == "2026-08-09T00:00:00Z"


def test_la_borne_ne_recule_jamais(tmp_path: Path) -> None:
    premier = AmontFactice([page([entree("io.github.a/un")])])
    miroir(tmp_path).rafraichir(premier.client())
    # Un amont dont l'horloge repart en arrière (rejeu, nœud désynchronisé) ne
    # doit pas faire reculer la borne : ce serait remoissonner en boucle.
    second = AmontFactice(
        [page([entree("io.github.b/deux")])], date="Mon, 03 Aug 2026 06:00:00 GMT"
    )
    compte_rendu = miroir(tmp_path).rafraichir(second.client())
    assert compte_rendu.etat.borne_amont == BORNE_ATTENDUE


def test_un_miroir_frais_n_appelle_pas_l_amont(tmp_path: Path) -> None:
    amont = AmontFactice([page([entree("io.github.a/un")])])
    depot = miroir(tmp_path, periode_s=3600)
    depot.rafraichir(amont.client())
    appels_apres_moisson = amont.appels

    veilleur = AmontFactice([page([entree("io.github.b/deux")])])
    assert depot.rafraichir_si_perime(veilleur.client()) is None
    # Le contrat se lit sur un compteur d'appels et jamais sur une durée (#577) :
    # zéro aller, donc l'écran n'a pas payé un moissonnage pour s'afficher.
    assert veilleur.appels == 0
    assert amont.appels == appels_apres_moisson


def test_un_miroir_perime_rafraichit(tmp_path: Path) -> None:
    amont = AmontFactice([page([entree("io.github.a/un")])])
    depot = miroir(tmp_path)
    depot.rafraichir(amont.client())
    suite = AmontFactice([page([entree("io.github.b/deux")])])
    plus_tard = datetime.now(UTC) + timedelta(seconds=PERIODE_MINIMALE_S + 1)
    compte_rendu = depot.rafraichir_si_perime(suite.client(), maintenant=plus_tard)
    assert compte_rendu is not None and compte_rendu.ok
    assert suite.appels == 1


def test_un_miroir_jamais_moissonne_est_perime(tmp_path: Path) -> None:
    assert miroir(tmp_path).perime() is True


# --------------------------------------------------------------------------- #
# Le `deleted` sort du miroir
# --------------------------------------------------------------------------- #


def test_une_entree_supprimee_sort_du_miroir_a_l_increment(tmp_path: Path) -> None:
    # Le cas qui compte : l'entrée **était** dans le miroir, et la fenêtre
    # incrémentale la ramène en `deleted`. C'est la seule façon dont la
    # modération amont nous parvient.
    depart = AmontFactice([page([entree("io.github.a/un"), entree("io.github.b/deux")])])
    miroir(tmp_path).rafraichir(depart.client())
    assert noms(tmp_path) == ["io.github.a/un", "io.github.b/deux"]

    modere = AmontFactice([page([entree("io.github.a/un", statut=STATUT_SUPPRIME)])])
    compte_rendu = miroir(tmp_path).rafraichir(modere.client())
    assert compte_rendu.ok and compte_rendu.mode == MODE_INCREMENTAL
    assert compte_rendu.retirees == 1
    assert noms(tmp_path) == ["io.github.b/deux"]


def test_une_entree_supprimee_n_entre_pas_au_moissonnage_complet(tmp_path: Path) -> None:
    amont = AmontFactice(
        [page([entree("io.github.a/un", statut=STATUT_SUPPRIME), entree("io.github.b/deux")])]
    )
    miroir(tmp_path).rafraichir(amont.client())
    assert noms(tmp_path) == ["io.github.b/deux"]


def test_une_entree_depreciee_reste_dans_le_miroir_avec_son_statut(tmp_path: Path) -> None:
    # `deprecated` est **signalée, pas cachée** : la doc de modération demande de
    # tenir le statut à jour, pas de retirer l'entrée.
    amont = AmontFactice([page([entree("io.github.a/un", statut=STATUT_DEPRECIE)])])
    depot = miroir(tmp_path)
    depot.rafraichir(amont.client())
    (gardee,) = depot.entrees()
    assert gardee.statut == STATUT_DEPRECIE
    assert gardee.obsolete is True
    assert gardee.supprimee is False


def test_un_statut_inconnu_est_conserve_tel_quel_et_ne_retire_rien(tmp_path: Path) -> None:
    # La préversion peut ajouter des statuts : seul `deleted` retire, et un
    # statut qu'on ne connaît pas ne doit pas faire disparaître une entrée.
    amont = AmontFactice([page([entree("io.github.a/un", statut="quarantaine")])])
    depot = miroir(tmp_path)
    depot.rafraichir(amont.client())
    (gardee,) = depot.entrees()
    assert gardee.statut == "quarantaine"
    assert gardee.supprimee is False


# --------------------------------------------------------------------------- #
# L'amont injoignable laisse le miroir précédent en place
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("panne", "famille"),
    [
        (httpx2.ConnectError("DNS"), "amont injoignable"),
        (httpx2.ReadTimeout("trop long"), "amont trop lent"),
    ],
)
def test_un_amont_fache_rend_un_compte_rendu_et_ne_leve_jamais(
    tmp_path: Path, panne: Exception, famille: str
) -> None:
    bon = AmontFactice([page([entree("io.github.a/un"), entree("io.github.b/deux")])])
    miroir(tmp_path).rafraichir(bon.client())
    avant = (tmp_path / MiroirAmont.FICHIER_ENTREES).read_bytes()

    casse = AmontFactice([], panne=panne)
    compte_rendu = miroir(tmp_path).rafraichir(casse.client())

    assert compte_rendu.ok is False
    assert compte_rendu.cause.startswith(famille)
    # Le miroir précédent est intact **aux octets près** : « le rafraîchissement
    # a échoué » ne doit jamais vouloir dire « la bibliothèque est vide ».
    assert (tmp_path / MiroirAmont.FICHIER_ENTREES).read_bytes() == avant
    assert noms(tmp_path) == ["io.github.a/un", "io.github.b/deux"]


def test_une_reponse_hors_contrat_laisse_aussi_le_miroir_en_place(tmp_path: Path) -> None:
    bon = AmontFactice([page([entree("io.github.a/un")])])
    miroir(tmp_path).rafraichir(bon.client())
    avant = (tmp_path / MiroirAmont.FICHIER_ENTREES).read_bytes()

    casse = AmontFactice([], statut=500, corps="boum")
    compte_rendu = miroir(tmp_path).rafraichir(casse.client())
    assert compte_rendu.ok is False
    assert compte_rendu.cause.startswith("amont hors contrat")
    assert (tmp_path / MiroirAmont.FICHIER_ENTREES).read_bytes() == avant


def test_la_cause_survit_dans_l_etat_pour_un_ecran_ouvert_plus_tard(tmp_path: Path) -> None:
    bon = AmontFactice([page([entree("io.github.a/un")])])
    depot = miroir(tmp_path)
    depot.rafraichir(bon.client(), maintenant=datetime(2026, 8, 28, 6, 0, tzinfo=UTC))
    casse = AmontFactice([], panne=httpx2.ConnectError("DNS"))
    depot.rafraichir(casse.client(), maintenant=datetime(2026, 8, 28, 9, 0, tzinfo=UTC))

    # Un miroir relu **trois heures plus tard**, par un autre processus : la
    # cause doit être là, sinon l'écran affiche une fraîcheur qu'il ne peut pas
    # justifier.
    relu = miroir(tmp_path).etat
    assert relu.cause.startswith("amont injoignable")
    assert relu.echoue_le == "2026-08-28T09:00:00Z"
    assert relu.rafraichi_le == "2026-08-28T06:00:00Z"
    assert relu.nombre == 1


def test_un_passage_reussi_efface_la_cause_de_l_echec_precedent(tmp_path: Path) -> None:
    depot = miroir(tmp_path)
    depot.rafraichir(AmontFactice([], panne=httpx2.ConnectError("DNS")).client())
    assert depot.etat.cause
    depot.rafraichir(AmontFactice([page([entree("io.github.a/un")])]).client())
    assert miroir(tmp_path).etat.cause == ""
    assert miroir(tmp_path).etat.echoue_le == ""


def test_un_moissonnage_complet_vide_ne_vide_pas_un_miroir_peuple(tmp_path: Path) -> None:
    # L'amont répond 200 avec zéro entrée : rien ne le distingue d'un amont sain
    # par la seule lecture de sa réponse, donc on traite le cas comme une panne.
    bon = AmontFactice([page([entree("io.github.a/un"), entree("io.github.b/deux")])])
    miroir(tmp_path).rafraichir(bon.client())

    vide = AmontFactice([page([])])
    compte_rendu = miroir(tmp_path).rafraichir(vide.client(), complet=True)
    assert compte_rendu.ok is False
    assert "viderait un miroir de 2" in compte_rendu.cause
    assert noms(tmp_path) == ["io.github.a/un", "io.github.b/deux"]


def test_un_disque_qui_refuse_l_ecriture_laisse_le_miroir_precedent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bon = AmontFactice([page([entree("io.github.a/un")])])
    miroir(tmp_path).rafraichir(bon.client())
    avant = (tmp_path / MiroirAmont.FICHIER_ENTREES).read_bytes()

    def refuse(*_: object, **__: object) -> None:
        raise OSError("disque plein")

    monkeypatch.setattr(MiroirAmont, "_ecrire", refuse)
    suite = AmontFactice([page([entree("io.github.b/deux")])])
    compte_rendu = miroir(tmp_path).rafraichir(suite.client())
    assert compte_rendu.ok is False
    assert compte_rendu.cause.startswith("miroir non écrit")
    assert (tmp_path / MiroirAmont.FICHIER_ENTREES).read_bytes() == avant


# --------------------------------------------------------------------------- #
# Ce que le miroir garde, et comment il le relit
# --------------------------------------------------------------------------- #


def test_le_document_amont_est_garde_verbatim(tmp_path: Path) -> None:
    # Un miroir qui remodèle sa source est un miroir qu'il faut remoissonner —
    # dix minutes — chaque fois que le lecteur change d'avis. La traduction vit
    # ailleurs (#676), et le `$schema` doit lui parvenir intact.
    brut = entree("io.github.a/un")
    depot = miroir(tmp_path)
    depot.rafraichir(AmontFactice([page([brut])]).client())
    (gardee,) = depot.entrees()
    assert gardee.document == brut["server"]
    assert gardee.document["$schema"] == SCHEMA


def test_les_entrees_sont_relues_triees_par_nom(tmp_path: Path) -> None:
    amont = AmontFactice(
        [page([entree("io.github.z/zeta"), entree("io.github.a/alpha")])]
    )
    depot = miroir(tmp_path)
    depot.rafraichir(amont.client())
    assert [e.nom for e in depot.entrees()] == ["io.github.a/alpha", "io.github.z/zeta"]


def test_un_miroir_absent_rend_zero_entree_sans_lever(tmp_path: Path) -> None:
    depot = miroir(tmp_path / "jamais-ecrit")
    assert depot.entrees() == ()
    assert depot.etat.nombre == 0


def test_une_ligne_illisible_du_miroir_est_sautee_sans_couter_les_autres(
    tmp_path: Path,
) -> None:
    depot = miroir(tmp_path)
    depot.rafraichir(AmontFactice([page([entree("io.github.a/un")])]).client())
    chemin = tmp_path / MiroirAmont.FICHIER_ENTREES
    chemin.write_text(
        chemin.read_text(encoding="utf-8") + "{ceci n'est pas du JSON\n", encoding="utf-8"
    )
    assert [e.nom for e in miroir(tmp_path).entrees()] == ["io.github.a/un"]


def test_un_etat_illisible_vaut_jamais_moissonne(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / MiroirAmont.FICHIER_ETAT).write_text("[]", encoding="utf-8")
    etat = miroir(tmp_path).etat
    assert etat.borne_amont == ""
    assert etat.rafraichi_le == ""
    # Le repli va vers le plus prudent : sans borne, le passage suivant refait un
    # moissonnage complet — dix minutes, jamais un trou.
    assert miroir(tmp_path).perime() is True


def test_la_memoire_des_entrees_tombe_a_l_ecriture(tmp_path: Path) -> None:
    depot = miroir(tmp_path)
    depot.rafraichir(AmontFactice([page([entree("io.github.a/un")])]).client())
    assert [e.nom for e in depot.entrees()] == ["io.github.a/un"]
    # Même objet miroir, second passage : la mémoire est empreintée sur le
    # fichier, donc elle tombe à l'instant même où elle mentirait.
    depot.rafraichir(AmontFactice([page([entree("io.github.b/deux")])]).client())
    assert [e.nom for e in depot.entrees()] == ["io.github.a/un", "io.github.b/deux"]


def test_l_etat_est_ecrit_a_cote_des_entrees_et_les_compte(tmp_path: Path) -> None:
    amont = AmontFactice([page([entree("io.github.a/un"), entree("io.github.b/deux")])])
    miroir(tmp_path).rafraichir(amont.client())
    etat = json.loads((tmp_path / MiroirAmont.FICHIER_ETAT).read_text(encoding="utf-8"))
    assert etat["nombre"] == len(noms(tmp_path)) == 2
    assert etat["amont"] == AMONT
    # Aucun fichier temporaire ne survit : les deux écritures sont des
    # remplacements atomiques (tampon puis renommage).
    assert not list(tmp_path.glob("*.tmp"))


def test_une_entree_sans_meta_reste_mirroir_able_en_actif() -> None:
    # `_meta` est une **extension** : disparue, l'entrée reste stockable — c'est
    # la borne qui en pâtira (repli sur un moissonnage complet), pas la donnée.
    lue = EntreeAmont.depuis_amont({"server": {"name": "io.github.a/un", "version": "2.0.0"}})
    assert lue.nom == "io.github.a/un"
    assert lue.statut == "active"
    assert lue.mis_a_jour_le == ""


def test_une_entree_sans_nom_est_refusee_a_la_lecture() -> None:
    with pytest.raises(AmontHorsContrat, match="server.name"):
        EntreeAmont.depuis_amont({"server": {"version": "2.0.0"}})


def test_l_aller_retour_par_le_miroir_conserve_l_entree() -> None:
    depart = EntreeAmont.depuis_amont(entree("io.github.a/un", statut=STATUT_DEPRECIE))
    assert EntreeAmont.depuis_miroir(depart.to_dict()) == depart
