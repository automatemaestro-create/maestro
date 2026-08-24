"""Le journal **persisté** : les logs survivent au rechargement (#478 ; couvert ici par #480).

Le lot 6 du chantier #472 est le seul à avoir livré des tests avec lui — le
contrat HTTP est gardé par [`tests/test_contrats_v2.py`](test_contrats_v2.py)
(rejeu au démarrage, filtres agent/run, pagination, fenêtre temporelle, les cinq
422) et la portée projet par
[`tests/test_appartenance_projet.py`](test_appartenance_projet.py). Ce fichier ne
les redit pas : il couvre ce qui restait dehors, et qui est précisément **la
promesse du ticket**.

Trois volets :

① **Le service seul** (`maestro.controltower.journal`) — la forme d'une entrée,
   son identifiant, le départage du tri par rang (sans lequel deux pages
   consécutives pourraient se recouvrir ou sauter une ligne), et le contrat d'une
   page hors bornes, qui n'est **pas** une erreur.

② **Ce qui l'alimente, et rien d'autre** — le rejeu du journal durable puis la
   pompe, dans cet ordre, qui est celui des rangs. C'est ce qui rend les
   identifiants stables d'un redémarrage à l'autre, et c'est *la* raison pour
   laquelle ils sont dérivés du rang plutôt que tirés au sort.

③ **La survie au rechargement** — un run terminé la veille montre son journal, là
   où le fil du shell ne contenait que ce qui était passé par le WebSocket depuis
   l'ouverture de la page. C'est le défaut que le lot ferme, et il se vérifie en
   redémarrant l'API sur le même journal durable.

**Ni Redis, ni réseau** : `InMemoryEventLog` tient le journal durable,
`InMemoryEventBus` le bus, et l'app est la vraie.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_TACHE_STATUT,
    ControlTowerState,
    EntreeJournal,
    Event,
    InMemoryEventBus,
    InMemoryEventLog,
    ServiceJournal,
    create_app,
)
from maestro.controltower.journal import (
    ORDRE_ASC,
    ORDRE_DESC,
    PREFIXE_ENTREE,
    TAILLE_PAGE_DEFAUT,
    TRI_JOURNAL_AGENT,
    TRI_JOURNAL_HORODATAGE,
    TRI_JOURNAL_TYPE,
    identifiant_entree,
)
from maestro.controltower.portee import PorteeProjet
from maestro.controltower.state import EVENEMENT_EXECUTION_STATUT, EXECUTION_TERMINEE

RUN = "run-journalise"
PROJET = "prj-0003"
TRANSVERSE = "tous"

#: Les douze champs qu'une entrée porte en JSON — `rang` n'en fait pas partie.
CHAMPS_ENTREE = {
    "id",
    "type",
    "run_id",
    "tache_id",
    "titre",
    "agent",
    "role",
    "statut",
    "detail",
    "description",
    "projet_id",
    "horodatage",
}


def evenement(
    *,
    agent: str = "developpeur",
    type: str = EVENEMENT_TACHE_STATUT,
    run_id: str = RUN,
    horodatage: str = "2026-08-24T10:00:00+00:00",
    projet_id: str | None = PROJET,
    **reste,
) -> Event:
    return Event(
        type=type,
        run_id=run_id,
        agent=agent,
        role="Développeur",
        horodatage=horodatage,
        projet_id=projet_id,
        **reste,
    )


def journal_de(*evenements: Event) -> ServiceJournal:
    journal = ServiceJournal()
    for event in evenements:
        journal.consigner(event)
    return journal


def log_de(*evenements: Event) -> InMemoryEventLog:
    log = InMemoryEventLog()
    for event in evenements:
        asyncio.run(log.consigner(event))
    return log


def app_sur(log: InMemoryEventLog) -> TestClient:
    """Une API **neuve** sur un journal durable donné — projection reconstruite."""
    return TestClient(
        create_app(bus=InMemoryEventBus(), state=ControlTowerState(), event_log=log)
    )


# ------------------------------------------- ① Le service : la forme d'une entrée


def test_l_identifiant_derive_du_rang_et_se_lit():
    assert identifiant_entree(1) == f"{PREFIXE_ENTREE}0001"
    assert identifiant_entree(42) == f"{PREFIXE_ENTREE}0042"
    # Au-delà de la largeur, l'identifiant s'allonge plutôt que de se tronquer —
    # deux entrées ne doivent jamais porter le même.
    assert identifiant_entree(123456) == f"{PREFIXE_ENTREE}123456"


def test_une_entree_porte_douze_champs_et_pas_son_rang():
    """`rang` est la clé de départage du tri, jamais une donnée du client."""
    entree = EntreeJournal.depuis(evenement(titre="Planification"), rang=3)

    assert set(entree.to_dict()) == CHAMPS_ENTREE
    assert entree.to_dict()["id"] == "j-0003"
    assert entree.rang == 3


def test_une_entree_laisse_dehors_le_cout_et_le_brief():
    """Ce qui n'appartient pas à une **ligne** de journal : usage, brief, sources, diff.

    Une page de 200 entrées doit rester une page ; ces champs restent lisibles là
    où ils ont un sens (le résumé d'un run, les coûts).
    """
    entree = EntreeJournal.depuis(evenement(cout_usd=1.25), rang=1)

    assert "cout_usd" not in entree.to_dict()
    assert "usage" not in entree.to_dict()


def test_les_rangs_se_suivent_dans_l_ordre_d_arrivee():
    journal = journal_de(evenement(titre="a"), evenement(titre="b"), evenement(titre="c"))

    assert len(journal) == 3
    ids = [e["id"] for e in journal.page(tri=TRI_JOURNAL_HORODATAGE, ordre=ORDRE_ASC)["entrees"]]
    assert ids == ["j-0001", "j-0002", "j-0003"]


def test_le_rang_departage_un_tri_qui_ne_distingue_pas_deux_entrees():
    """Sans lui, deux pages consécutives pourraient se recouvrir ou sauter une ligne —
    exactement ce que la pagination doit empêcher."""
    journal = journal_de(*(evenement(titre=str(n)) for n in range(5)))

    page = journal.page(tri=TRI_JOURNAL_AGENT, ordre=ORDRE_ASC)

    assert [e["id"] for e in page["entrees"]] == [f"j-{n:04d}" for n in range(1, 6)]


@pytest.mark.parametrize(
    ("tri", "ordre", "attendu"),
    [
        (TRI_JOURNAL_AGENT, ORDRE_ASC, ["bdd", "developpeur", "qa"]),
        (TRI_JOURNAL_AGENT, ORDRE_DESC, ["qa", "developpeur", "bdd"]),
    ],
)
def test_le_tri_par_agent_va_dans_les_deux_sens(tri, ordre, attendu):
    journal = journal_de(
        evenement(agent="qa"), evenement(agent="bdd"), evenement(agent="developpeur")
    )

    page = journal.page(tri=tri, ordre=ordre)

    assert [e["agent"] for e in page["entrees"]] == attendu


def test_le_tri_par_type_range_les_familles_d_evenements():
    journal = journal_de(
        evenement(type=EVENEMENT_TACHE_STATUT),
        evenement(type=EVENEMENT_AGENT_ACTIVITE),
    )

    page = journal.page(tri=TRI_JOURNAL_TYPE, ordre=ORDRE_ASC)

    assert [e["type"] for e in page["entrees"]] == [
        EVENEMENT_AGENT_ACTIVITE,
        EVENEMENT_TACHE_STATUT,
    ]


def test_un_journal_vide_rend_zero_page_et_non_une_page_vide():
    page = ServiceJournal().page()

    assert page == {
        "entrees": [],
        "total": 0,
        "page": 1,
        "taille": TAILLE_PAGE_DEFAUT,
        "pages": 0,
    }


def test_une_page_au_dela_de_la_derniere_rend_des_compteurs_justes():
    """« Et après ? » a une réponse — ce n'est pas une erreur de chemin."""
    journal = journal_de(evenement(), evenement())

    page = journal.page(page=9, taille=1)

    assert page["entrees"] == []
    assert page["total"] == 2
    assert page["pages"] == 2
    assert page["page"] == 9


@pytest.mark.parametrize("vide", ["", None])
def test_un_filtre_vide_ne_filtre_rien(vide):
    """Un champ de recherche effacé rend la vue complète, jamais une vue vide."""
    journal = journal_de(evenement(agent="qa"), evenement(agent="bdd"))

    assert journal.page(agent=vide, type=vide, run_id=vide)["total"] == 2


def test_les_bornes_temporelles_sont_incluses():
    journal = journal_de(
        evenement(horodatage="2026-08-24T09:00:00+00:00"),
        evenement(horodatage="2026-08-24T10:00:00+00:00"),
        evenement(horodatage="2026-08-24T11:00:00+00:00"),
    )

    page = journal.page(
        depuis="2026-08-24T09:00:00+00:00", jusqua="2026-08-24T10:00:00+00:00"
    )

    assert page["total"] == 2


def test_la_portee_projet_est_celle_du_depot_et_non_une_seconde_regle():
    journal = journal_de(
        evenement(projet_id=PROJET),
        evenement(projet_id="prj-autre"),
        evenement(projet_id=None),
    )

    assert journal.page(portee=PorteeProjet.tous())["total"] == 3
    assert journal.page(portee=PorteeProjet.projet(PROJET))["total"] == 1
    assert journal.page(portee=PorteeProjet.aucun())["total"] == 1


# --------------------------- ② Ce qui l'alimente : le rejeu, puis la pompe


def test_le_journal_se_remplit_du_journal_durable_au_demarrage():
    log = log_de(evenement(titre="Planification"), evenement(titre="Schéma"))

    with app_sur(log) as client:
        page = client.get("/api/journal", params={"projet": TRANSVERSE}).json()

    assert page["total"] == 2
    assert [e["titre"] for e in page["entrees"]] == ["Schéma", "Planification"]


def test_les_identifiants_sont_les_memes_a_chaque_redemarrage():
    """C'est *la* raison pour laquelle ils dérivent du rang plutôt que d'être tirés.

    Le journal durable est append-only et rejoué dans le même ordre : une entrée
    garde son identifiant, donc un lien vers une ligne reste valide.
    """
    log = log_de(*(evenement(titre=f"étape {n}") for n in range(4)))

    pages = []
    for _ in range(2):
        with app_sur(log) as client:
            pages.append(client.get("/api/journal", params={"projet": TRANSVERSE}).json())

    assert pages[0]["entrees"] == pages[1]["entrees"]
    assert [e["id"] for e in pages[0]["entrees"]] == ["j-0004", "j-0003", "j-0002", "j-0001"]


def test_la_pompe_prolonge_le_journal_sans_rompre_les_rangs():
    """Deux sources, un seul compteur : le rejeu précède, la pompe continue."""
    log = log_de(evenement(titre="d'avant"))

    with app_sur(log) as client:
        # Un run suspendu depuis l'API : son ordre passe par la pompe, donc par
        # le journal — c'est le chemin de production, pas un raccourci de test.
        client.post(f"/api/executions/{RUN}/pause")
        page = client.get(
            "/api/journal", params={"projet": TRANSVERSE, "ordre": ORDRE_ASC}
        ).json()

    assert page["total"] == 2
    assert [e["id"] for e in page["entrees"]] == ["j-0001", "j-0002"]
    assert page["entrees"][1]["statut"] == "pause"


# ------------------------------------------ ③ La survie au rechargement


def test_le_journal_d_un_run_termine_la_veille_se_lit_encore():
    """Le défaut que le lot ferme : le fil du shell ne contenait que ce qui était
    passé par le WebSocket **depuis l'ouverture de la page**, donc ouvrir la vue
    d'un run terminé la veille ne montrait rien du tout."""
    log = log_de(
        evenement(titre="Planification", horodatage="2026-08-23T09:00:00+00:00"),
        evenement(titre="Schéma", horodatage="2026-08-23T09:30:00+00:00"),
        evenement(
            type=EVENEMENT_EXECUTION_STATUT,
            statut=EXECUTION_TERMINEE,
            titre="Prototyper",
            horodatage="2026-08-23T10:00:00+00:00",
        ),
    )

    # Une API ouverte **après** la fin du run : rien n'est passé par son WebSocket.
    with app_sur(log) as client:
        page = client.get(
            "/api/journal", params={"projet": TRANSVERSE, "run_id": RUN}
        ).json()

    assert page["total"] == 3
    assert [e["titre"] for e in page["entrees"]] == ["Prototyper", "Schéma", "Planification"]


def test_le_journal_d_un_run_ne_contient_que_le_sien():
    """Le filtre de la vue d'un run (§2.4.2) : `?run_id=`, jamais un tri local."""
    log = log_de(
        evenement(titre="à moi"),
        evenement(titre="au voisin", run_id="run-voisin"),
    )

    with app_sur(log) as client:
        page = client.get(
            "/api/journal", params={"projet": TRANSVERSE, "run_id": RUN}
        ).json()
        inconnu = client.get(
            "/api/journal", params={"projet": TRANSVERSE, "run_id": "run-fantome"}
        ).json()

    assert [e["titre"] for e in page["entrees"]] == ["à moi"]
    # Un run sans ligne rend un journal vide, pas un refus : la route du journal
    # ne connaît pas les runs, contrairement à `GET /api/taches?run=` (§6.0bis).
    assert inconnu["total"] == 0
    assert inconnu["pages"] == 0


def test_un_journal_durable_illisible_ne_fait_pas_tomber_l_api():
    """Le rejeu est best-effort : une panne de relecture laisse le journal vide,
    l'API sert quand même — mais elle ne prétend pas avoir un historique."""

    class LogEnPanne(InMemoryEventLog):
        async def relire(self):  # type: ignore[override]
            raise RuntimeError("journal durable injoignable")

    with app_sur(LogEnPanne()) as client:
        page = client.get("/api/journal", params={"projet": TRANSVERSE}).json()

    assert page["total"] == 0
