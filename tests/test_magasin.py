"""L'API qui perd son magasin le dit (#1206) — santé, garde des routes, reprise.

Jusqu'à #1206, une API privée de son Redis démarrait quand même, répondait
« ok » sur `/api/sante` et servait des listes vides en `200` : un vide qui passe
pour « aucune donnée ». Ces tests tiennent les trois promesses qui l'ont remplacé :

- la **santé** dit la panne dans son corps, et reste un `200` (les sondes du
  dépôt y lisent « un process sert ce port ») ;
- les **routes** se refusent en `503` motivé — la panne, puis le geste —, sauf
  celles que `ROUTES_HORS_GARDE` nomme avec leur raison ;
- l'API **reprend seule** quand le magasin revient : la pompe réessaie et relit
  l'historique qu'elle n'avait pas pu relire au démarrage.

Le magasin est simulé par un journal en process dont la sonde lève : c'est le
contrat (`EventLog.sonder`) qu'un journal Redis honore par un `PING`. La panne
réelle — Redis injoignable au démarrage, coupé par un relais, puis rétabli — a
été rejouée sur la vraie API pour ce ticket.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import (
    EVENEMENT_TACHE_STATUT,
    ControlTowerState,
    Event,
    InMemoryEventBus,
    InMemoryEventLog,
    create_app,
)
from maestro.controltower.magasin import (
    COMMANDE_REDIS,
    PANNE_MAGASIN,
    ROUTES_HORS_GARDE,
    Magasin,
    endpoint_lisible,
)
from maestro.controltower.persistence import RedisEventLog

TRANSVERSE = "tous"


def evenement(tache: str = "t1") -> Event:
    return Event(
        type=EVENEMENT_TACHE_STATUT,
        run_id="run-magasin",
        agent="developpeur",
        role="Développeur",
        horodatage="2026-09-23T10:00:00+00:00",
        projet_id="prj-0001",
        tache_id=tache,
        statut="en_cours",
    )


class JournalCoupable(InMemoryEventLog):
    """Un journal en process qu'on coupe et qu'on rétablit — un Redis simulé."""

    def __init__(self, *, coupe: bool = False) -> None:
        super().__init__()
        self.coupe = coupe
        self.sondes = 0

    async def sonder(self) -> None:
        self.sondes += 1
        if self.coupe:
            raise ConnectionError("Error 10061 connecting to 127.0.0.1:6399.")

    async def relire(self) -> list[Event]:
        if self.coupe:
            raise ConnectionError("Error 10061 connecting to 127.0.0.1:6399.")
        return await super().relire()

    def lieu(self) -> str | None:
        return "Redis, redis://127.0.0.1:6399/0"


def app_sur(log: InMemoryEventLog, bus: InMemoryEventBus | None = None) -> TestClient:
    return TestClient(
        create_app(
            bus=bus if bus is not None else InMemoryEventBus(),
            state=ControlTowerState(),
            event_log=log,
        )
    )


def attendre(condition, delai_s: float = 10.0) -> None:
    """Attend qu'une condition tienne — la pompe reprend dans sa propre boucle."""
    fin = time.monotonic() + delai_s
    while not condition():
        if time.monotonic() > fin:
            pytest.fail("la condition n'a pas tenu dans le délai")
        time.sleep(0.05)


# ------------------------------------------------------------ ① La santé


def test_la_sante_ne_repond_plus_ok_quand_le_magasin_manque():
    with app_sur(JournalCoupable(coupe=True)) as client:
        reponse = client.get("/api/sante")

    # Toujours un 200 : la purge, le lanceur, start.sh et le banc y lisent
    # « un process sert ce port », et c'est vrai.
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["statut"] == "degrade"
    magasin = corps["magasin"]
    assert magasin["disponible"] is False
    assert magasin["lieu"] == "Redis, redis://127.0.0.1:6399/0"
    assert magasin["titre"] == "Magasin des événements injoignable"
    assert "Error 10061" in magasin["motif"]
    assert magasin["geste"] == "relancer Redis, l'API reprend seule"
    assert magasin["commande"] == COMMANDE_REDIS


def test_la_sante_dit_ok_quand_le_magasin_repond_et_que_l_historique_est_relu():
    with app_sur(JournalCoupable()) as client:
        corps = client.get("/api/sante").json()

    assert corps["statut"] == "ok"
    assert corps["magasin"]["disponible"] is True
    assert corps["magasin"]["motif"] is None


# ------------------------------------------------------------ ② La garde


@pytest.mark.parametrize(
    "route",
    [
        "/api/executions?projet=tous",
        f"/api/journal?projet={TRANSVERSE}",
        "/api/chat/orchestrateur",
        "/api/taches?projet=tous",
    ],
)
def test_un_ecran_ne_lit_plus_un_vide_mais_la_panne(route: str):
    """Les trois lectures du ticket, et une de plus : aucune ne rend un vide en 200."""
    with app_sur(JournalCoupable(coupe=True)) as client:
        reponse = client.get(route)

    assert reponse.status_code == 503
    corps = reponse.json()
    # La panne, où elle a frappé, puis le geste — sur une ligne, pour tout client.
    assert corps["detail"].startswith(
        "Magasin des événements injoignable (Redis, redis://127.0.0.1:6399/0) : "
    )
    assert corps["detail"].endswith(f"— relancer Redis, l'API reprend seule ({COMMANDE_REDIS})")
    # Et nommée par un champ, que l'écran lit sans relire le texte.
    assert corps["panne"] == PANNE_MAGASIN
    assert corps["magasin"]["commande"] == COMMANDE_REDIS
    assert reponse.headers["retry-after"]


def test_une_ecriture_est_refusee_aussi_plutot_que_perdue():
    """Un run lancé sans magasin publierait dans le vide : on le refuse, motivé."""
    with app_sur(JournalCoupable(coupe=True)) as client:
        reponse = client.post("/api/chat/orchestrateur", json={"texte": "bonjour"})

    assert reponse.status_code == 503


def test_les_routes_hors_garde_passent_et_disent_pourquoi():
    assert set(ROUTES_HORS_GARDE) == {"/api/sante", "/api/extinction"}
    assert all(raison.strip() for raison in ROUTES_HORS_GARDE.values())
    with app_sur(JournalCoupable(coupe=True)) as client:
        assert client.get("/api/sante").status_code == 200


def test_le_refus_du_magasin_reste_lisible_par_la_page():
    """Le 503 ressort avec ses en-têtes CORS : sans eux, la page ne lirait pas le motif."""
    with app_sur(JournalCoupable(coupe=True)) as client:
        reponse = client.get("/api/taches?projet=tous", headers={"Origin": "http://localhost:3000"})

    assert reponse.status_code == 503
    assert reponse.headers["access-control-allow-origin"]


def test_un_historique_illisible_se_dit_avec_sa_cause_meme_si_le_magasin_repond():
    """La sonde passe, la relecture échoue : la panne dit la relecture, pas un Redis éteint."""

    class RelectureEnPanne(InMemoryEventLog):
        async def relire(self) -> list[Event]:
            raise ValueError("entrée illisible au rang 12")

    with app_sur(RelectureEnPanne()) as client:
        reponse = client.get("/api/taches?projet=tous")

    assert reponse.status_code == 503
    detail = reponse.json()["detail"]
    assert detail.startswith("Historique illisible")
    assert "entrée illisible au rang 12" in detail
    assert COMMANDE_REDIS not in detail


# ------------------------------------------------------------ ③ La reprise


def test_l_api_relit_l_historique_des_que_le_magasin_revient():
    """Redis absent au démarrage, puis relancé : l'historique revient sans redémarrer l'API."""
    log = JournalCoupable(coupe=True)
    asyncio.run(InMemoryEventLog.consigner(log, evenement("t1")))
    asyncio.run(InMemoryEventLog.consigner(log, evenement("t2")))

    with app_sur(log) as client:
        assert client.get("/api/taches?projet=tous").status_code == 503
        log.coupe = False

        def repris() -> bool:
            return client.get("/api/sante").json()["statut"] == "ok"

        attendre(repris)
        taches = client.get("/api/taches?projet=tous").json()

    assert {t["id"] for t in taches} >= {"t1", "t2"}


class BusQuiTombe(InMemoryEventBus):
    """Un bus dont le premier abonnement casse — un Redis coupé en cours de route."""

    def __init__(self) -> None:
        super().__init__()
        self.abonnements = 0

    async def subscribe(self) -> AsyncIterator[Event]:
        self.abonnements += 1
        if self.abonnements == 1:
            raise ConnectionError("Connection closed by server.")
        async for event in super().subscribe():
            yield event


def test_la_pompe_reprend_le_flux_apres_une_coupure_du_bus():
    """Elle s'arrêtait pour de bon : le temps réel ne revenait qu'au redémarrage."""
    bus = BusQuiTombe()
    with app_sur(JournalCoupable(), bus=bus) as client:
        attendre(lambda: bus.abonnements >= 2)
        client.portal.call(bus.publish, evenement("apres-la-coupure"))

        def arrive() -> bool:
            return any(
                t["id"] == "apres-la-coupure" for t in client.get("/api/taches?projet=tous").json()
            )

        attendre(arrive)


# ------------------------------------------------------------ ④ La sonde


def test_la_sonde_ne_pingue_pas_a_chaque_question():
    """Un écran lit dix routes d'un coup : un verdict tient `validite_s` secondes."""
    log = JournalCoupable()
    instant = [0.0]
    magasin = Magasin(log.sonder, horloge=lambda: instant[0], validite_s=1.0)
    magasin.rejeu_abouti()

    async def scenario() -> None:
        for _ in range(5):
            assert (await magasin.etat()).disponible
        assert log.sondes == 1
        instant[0] = 2.0
        log.coupe = True
        assert not (await magasin.etat()).disponible
        assert log.sondes == 2
        # La pompe qui perd le flux fait oublier le verdict : la question
        # suivante sonde à nouveau, sans attendre la fin de sa validité.
        log.coupe = False
        magasin.oublier()
        assert (await magasin.etat()).disponible
        assert log.sondes == 3

    asyncio.run(scenario())


def test_une_sonde_sans_reponse_ne_bloque_pas_la_requete():
    """Un hôte qui ne répond plus ferait attendre le time-out TCP : la sonde a le sien."""

    async def muette() -> None:
        await asyncio.sleep(10)

    magasin = Magasin(muette, lieu="Redis, redis://10.0.0.1:6379/0", delai_s=0.05)
    magasin.rejeu_abouti()

    etat = asyncio.run(magasin.etat())

    assert not etat.disponible
    assert "pas de réponse en 0.05 s" in etat.motif


def test_le_lieu_d_un_journal_redis_ne_publie_pas_de_mot_de_passe():
    log = RedisEventLog("redis://utilisateur:secret@127.0.0.1:6379/0")
    try:
        assert log.lieu() == "Redis, redis://127.0.0.1:6379/0"
        assert "secret" not in (log.lieu() or "")
    finally:
        asyncio.run(log.close())
    assert endpoint_lisible("redis://u:p@[::1]:6380/2") == "redis://[::1]:6380/2"
