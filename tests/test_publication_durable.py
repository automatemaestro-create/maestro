"""On consigne en **publiant**, plus en consommant — la durabilité rendue au producteur (#699).

L'incident du 2026-08-28 (run `811d738020d5`) : l'API arrêtée une quinzaine de
minutes pendant qu'un run détaché travaillait, puis relancée. Au retour, le
tableau mentait sur trois points — une tâche finie affichée « En cours », une
tâche démarrée sans aucun statut (donc lue comme une exécution parallèle qu'aucun
plan ne déclare), et un run annonçant une tâche là où son plan en portait cinq.

La cause n'est pas dans l'affichage. Le bus est du **pub/sub, éphémère**, et le
journal durable (#97) n'avait qu'un écrivain : la **pompe** de l'API, c'est-à-dire
un *consommateur*. L'hôte détaché (#441/#446) continue de publier pendant la
coupure ; personne ne consomme, donc rien n'est consigné, et le rejeu au
démarrage rebâtit fidèlement une projection trouée. Rien à rattraper après coup :
`RunJournal` ne garde ses étapes qu'en mémoire, et le `hote.log` du run était
vide.

La consignation a donc changé de côté — elle a lieu là où l'événement **naît** :
`BusDurable` pour les producteurs asynchrones, `bridge.publieur_redis` pour le
producteur synchrone du pont télémétrie. Et la pompe **ne consigne plus rien** :
c'est la seconde moitié du remède et non une conséquence, deux écrivains sans
dédoublonnage — un `Event` n'a pas d'identifiant — auraient doublé chaque ligne
au lieu d'en perdre.

Ce que cette suite éprouve, dans l'ordre des critères :

① un événement publié **sans personne à l'écoute** est acquis et rejoué ;
② l'incident lui-même, rejoué : les trois mensonges du tableau, et le motif
   **prouvé sur l'échantillon fautif** — le bus d'avant les produit tous les
   trois, sans quoi ces assertions vaudraient un ✓ sur une question jamais posée ;
③ le compte de tâches du run est celui de son plan ;
④ un événement publié une fois n'est consigné qu'une fois, API en marche ;
⑤ la production : le publieur synchrone écrit **la liste que l'API relit**, et
   les promesses d'avant (échec de persistance non fatal, clients paresseux) sont
   déplacées avec le geste, pas perdues en route.

**Ni Redis, ni réseau** : les doubles mémoire tiennent le bus et le journal, et le
volet ⑤ passe par un client Redis factice servant les deux bibliothèques à la
fois — même harnais que `tests/test_battement.py` pour `CLE_BATTEMENTS`, et pour
la même raison : une clé partagée est le seul endroit où les deux moitiés d'un
dispositif se rejoignent, et une divergence y serait invisible partout ailleurs.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
import redis
import redis.asyncio
from fastapi.testclient import TestClient

from maestro.agents.capacity import CapacityStore
from maestro.agents.store import AgentStore
from maestro.controltower import (
    EVENEMENT_AGENT_CAPACITE,
    EVENEMENT_RUN_PLAN,
    EVENEMENT_TACHE_STATUT,
    BusDurable,
    ControlTowerState,
    Event,
    EventBus,
    InMemoryEventBus,
    InMemoryEventLog,
    RedisEventLog,
    create_app,
)
from maestro.controltower.bridge import publieur_redis
from maestro.controltower.events import CANAL_EVENEMENTS, EVENEMENT_TACHE_DETAIL
from maestro.controltower.persistence import CLE_JOURNAL_EVENEMENTS, bus_durable
from maestro.controltower.state import EVENEMENT_EXECUTION_STATUT, EXECUTION_EN_COURS
from maestro.engine.executor import STATUT_EN_COURS, STATUT_TERMINEE
from maestro.plan_run import NoeudPlan

RUN = "811d738020d5"
PROJET = "prj-0001"
DELAI_ATTENTE_S = 5.0

#: Le plan de l'incident : cinq tâches enchaînées, `modele-persistance`
#: **dépendante** de `squelette-p1` — ce que le tableau donnait à lire comme deux
#: exécutions parallèles.
PLAN = (
    NoeudPlan(id="squelette-p1", titre="Squelette P1", dependances=()),
    NoeudPlan(id="modele-persistance", titre="Modèle de persistance",
              dependances=("squelette-p1",)),
    NoeudPlan(id="api-rest", titre="API REST", dependances=("modele-persistance",)),
    NoeudPlan(id="ui", titre="Interface", dependances=("api-rest",)),
    NoeudPlan(id="recette", titre="Recette", dependances=("ui",)),
)


# ------------------------------------------------------------------ harnais


def statut(tache_id: str, etat: str) -> Event:
    """L'événement qui fait exister une tâche dans un run — et lui donne son état."""
    return Event(
        type=EVENEMENT_TACHE_STATUT,
        run_id=RUN,
        tache_id=tache_id,
        titre=f"Tâche {tache_id}",
        agent="devops",
        role="DevOps / SRE",
        statut=etat,
        projet_id=PROJET,
    )


def detail(tache_id: str) -> Event:
    """Un `tache.detail` : il fait bouger une carte sans jamais lui donner de statut.

    C'est lui qui rendait l'incohérence **visible plutôt que silencieuse**, et
    c'est aussi ce qui la rendait trompeuse — une carte qui bouge sans statut se
    lit comme une tâche traitée en parallèle.
    """
    return Event(
        type=EVENEMENT_TACHE_DETAIL,
        run_id=RUN,
        tache_id=tache_id,
        description="Ce que la tâche demande, en long.",
        projet_id=PROJET,
    )


def lancement() -> Event:
    return Event(
        type=EVENEMENT_EXECUTION_STATUT,
        run_id=RUN,
        statut=EXECUTION_EN_COURS,
        titre="Prototyper le service",
        projet_id=PROJET,
    )


def plan_publie() -> Event:
    """`run.plan` — publié une fois, à l'instant où la décomposition rend son plan."""
    return Event(type=EVENEMENT_RUN_PLAN, run_id=RUN, plan=list(PLAN), projet_id=PROJET)


def publie(bus: EventBus, *evenements: Event) -> None:
    """Ce qu'un hôte publie — **sans aucun abonné**, c'est-à-dire API arrêtée.

    Un `InMemoryEventBus` sans abonné est la forme fidèle d'un pub/sub Redis que
    personne n'écoute : il accepte la publication et n'en garde rien. C'est
    exactement la situation de la coupure, et la seule qui distingue les deux
    bus qu'on compare.
    """

    async def _ecoule() -> None:
        for event in evenements:
            await bus.publish(event)

    asyncio.run(_ecoule())


def bus_d_avant() -> InMemoryEventBus:
    """L'échantillon fautif : le bus qui publie et ne consigne rien (avant #699)."""
    return InMemoryEventBus()


def pompe_consigne(log: InMemoryEventLog, *evenements: Event) -> None:
    """Ce que la pompe de l'API consignait tant qu'elle tournait — le monde d'avant.

    C'était la seule écriture du journal durable : un **consommateur**. Le
    reproduire ici est ce qui rend l'échantillon fautif fidèle — l'API n'a pas
    été arrêtée tout le run, elle l'a été un quart d'heure, donc ce qui a été
    publié avant et après est bien au journal. C'est le **trou du milieu** qui
    fait les trois mensonges du tableau, pas une absence générale de journal.
    """

    async def _consigner() -> None:
        for event in evenements:
            await log.consigner(event)

    asyncio.run(_consigner())


def redemarrage(log: InMemoryEventLog) -> TestClient:
    """L'API qui repart : projection **vide**, même journal, rejeu au démarrage."""
    return TestClient(
        create_app(bus=InMemoryEventBus(), state=ControlTowerState(), event_log=log)
    )


def evenements(log: InMemoryEventLog) -> list[Event]:
    return asyncio.run(log.relire())


def carte(client: TestClient, tache_id: str) -> dict[str, Any] | None:
    taches = client.get("/api/taches?projet=tous").json()
    return next((t for t in taches if t["id"] == tache_id), None)


def resume(client: TestClient) -> dict[str, Any]:
    reponse = client.get(f"/api/executions/{RUN}")
    assert reponse.status_code == 200, reponse.text
    return dict(reponse.json())


def attendre(condition, quoi: str) -> None:
    """Attend un fait **asynchrone** — l'indexation par la pompe.

    La consignation, elle, ne s'attend plus : elle a lieu dans la publication,
    donc elle est acquise dès que l'appel qui publie est rendu (#699).
    """
    limite = time.monotonic() + DELAI_ATTENTE_S
    while not condition():
        if time.monotonic() > limite:  # pragma: no cover - filet anti-blocage
            pytest.fail(f"{quoi} n'est jamais arrivé en {DELAI_ATTENTE_S} s")
        time.sleep(0.02)


# ------------- ① publier sans personne à l'écoute suffit à être acquis


def test_un_evenement_publie_sans_abonne_est_consigne_puis_rejoue():
    """Le premier critère, et tout le ticket tient dedans.

    Personne n'écoute — c'est la définition de la coupure — et l'événement est
    pourtant au journal durable, donc dans la projection de l'API suivante.
    """
    log = InMemoryEventLog()

    publie(BusDurable(bus_d_avant(), log), lancement(), statut("squelette-p1", STATUT_TERMINEE))

    assert [e.type for e in evenements(log)] == [
        EVENEMENT_EXECUTION_STATUT,
        EVENEMENT_TACHE_STATUT,
    ]
    with redemarrage(log) as client:
        assert carte(client, "squelette-p1")["statut"] == STATUT_TERMINEE


def test_sans_le_dispositif_le_meme_evenement_est_perdu_pour_toujours():
    """L'échantillon fautif : le bus d'avant, publié dans les mêmes conditions.

    Sans cette moitié, le test ci-dessus vaudrait un ✓ sur une question jamais
    posée. Et « perdu pour toujours » n'est pas une formule : le journal n'a rien
    à rejouer, `RunJournal` ne garde ses étapes qu'en mémoire, le `hote.log` du
    run était vide — un rattrapage à la reprise n'a jamais été une option.
    """
    log = InMemoryEventLog()

    publie(bus_d_avant(), lancement(), statut("squelette-p1", STATUT_TERMINEE))

    assert evenements(log) == []
    with redemarrage(log) as client:
        assert carte(client, "squelette-p1") is None


def test_le_bus_durable_ne_change_rien_a_ce_que_les_abonnes_recoivent():
    """Consigner est une affaire de producteur : `subscribe` délègue tel quel.

    Le transport ne bouge pas d'un octet — c'est toujours le pub/sub qui diffuse,
    et un abonné ne doit rien voir de différent de ce qu'il voyait avant.
    """
    recus: list[Event] = []

    async def scenario() -> None:
        bus = BusDurable(InMemoryEventBus(), InMemoryEventLog())

        async def ecoute() -> None:
            async for event in bus.subscribe():
                recus.append(event)
                return

        async with asyncio.TaskGroup() as tg:
            tg.create_task(ecoute())
            await asyncio.sleep(0)  # laisse l'abonnement se poser
            await bus.publish(statut("squelette-p1", STATUT_EN_COURS))

    asyncio.run(scenario())

    assert [e.tache_id for e in recus] == ["squelette-p1"]


# ------------------------------- ② l'incident du 2026-08-28, rejoué


#: Ce que l'hôte a publié **pendant** que l'API était arrêtée : les deux
#: événements qui structurent le tableau, et exactement ceux que le rapport
#: nomme — le statut terminal de `squelette-p1` (étape nue, `evenements_depuis_step`)
#: et le passage « en cours » de `modele-persistance` (`executor._consigne_debut`).
def pendant_la_coupure() -> tuple[Event, ...]:
    return (
        statut("squelette-p1", STATUT_TERMINEE),
        statut("modele-persistance", STATUT_EN_COURS),
    )


def incident_avec_le_dispositif() -> InMemoryEventLog:
    """Le run de l'incident, publié sur le bus durable de bout en bout.

    Un hôte ne change pas de canal parce que l'API s'arrête — c'est même tout le
    problème, il n'en sait rien. Tout passe donc par le même bus, et c'est le bus
    qui garde.
    """
    log = InMemoryEventLog()
    bus = BusDurable(bus_d_avant(), log)
    publie(bus, lancement(), plan_publie(), statut("squelette-p1", STATUT_EN_COURS))
    publie(bus, *pendant_la_coupure())
    # Les `tache.detail` publiés **après** le retour de l'API passaient
    # normalement : c'est ce qui rendait l'incohérence visible plutôt que
    # silencieuse — et trompeuse, une carte qui bouge sans statut se lisant
    # comme une tâche traitée en parallèle.
    publie(bus, detail("modele-persistance"))
    return log


def incident_sans_le_dispositif() -> InMemoryEventLog:
    """Le même run avant #699 : la pompe consignait, et la coupure a mangé le milieu."""
    log = InMemoryEventLog()
    pompe_consigne(log, lancement(), plan_publie(), statut("squelette-p1", STATUT_EN_COURS))
    publie(bus_d_avant(), *pendant_la_coupure())  # personne ne consomme : rien n'est gardé
    pompe_consigne(log, detail("modele-persistance"))
    return log


def test_la_tache_finie_pendant_la_coupure_est_rendue_terminee():
    """Premier mensonge : `squelette-p1` restait « En cours », figée sur son étape.

    Son agent n'avait plus rien publié depuis quinze minutes et la tâche était
    finie — le statut terminal, lui, était tombé dans le canal éphémère.
    """
    with redemarrage(incident_avec_le_dispositif()) as client:
        assert carte(client, "squelette-p1")["statut"] == STATUT_TERMINEE


def test_la_tache_demarree_pendant_la_coupure_est_rendue_en_cours():
    """Deuxième mensonge : `modele-persistance` recevait ses détails sans statut.

    `statut: ""`, compartiment `autres` — une carte qui « se met à jour » sans
    jamais passer en cours, ce qui a fait croire à une exécution **parallèle**
    alors que le plan la déclare dépendante de `squelette-p1`.
    """
    with redemarrage(incident_avec_le_dispositif()) as client:
        assert carte(client, "modele-persistance")["statut"] == STATUT_EN_COURS


def test_le_tableau_d_avant_mentait_sur_les_trois_points_du_rapport():
    """L'échantillon fautif : le même run, le dispositif d'avant, les trois mensonges.

    Sans cette moitié, les assertions ci-dessus vaudraient un ✓ sur une question
    jamais posée. Ce sont les trois chiffres du rapport, au mot près : une tâche
    finie encore « en cours », une tâche démarrée sans aucun statut, et une
    progression qui annonce **une** tâche là où le plan en porte cinq — ce
    dernier point parce qu'un `tache.detail` fait bouger une carte sans jamais la
    faire entrer au compte du run (`taches_vues`).
    """
    with redemarrage(incident_sans_le_dispositif()) as client:
        execution = resume(client)
        graphe = client.get(f"/api/executions/{RUN}/graphe").json()

        assert carte(client, "squelette-p1")["statut"] == STATUT_EN_COURS
        assert carte(client, "modele-persistance")["statut"] == ""

    assert execution["progression"]["total"] == 1
    assert graphe["nb_noeuds"] == len(PLAN)


def test_le_dispositif_rend_au_run_les_taches_qu_il_a_vraiment_portees():
    """Le pendant du test ci-dessus : deux tâches vues, parce que rien n'a été perdu.

    Le compte reste celui des tâches que le run a **réellement portées** — un
    plan annonce ce qui *sera* fait et ne crée aucune carte (#490) —, et c'est
    précisément ce qui rend le « 1 » d'avant faux : il n'y avait pas une tâche,
    il y en avait deux, dont une dont le statut s'était perdu.
    """
    with redemarrage(incident_avec_le_dispositif()) as client:
        assert resume(client)["progression"]["total"] == 2


# --------------------------- ③ le compte de tâches est celui du plan


def run_entier(coupure: EventBus | None, log: InMemoryEventLog) -> None:
    """Le run mené jusqu'au bout, avec une tâche qui **vit et meurt** dans la coupure.

    C'est le cas qui distingue une perte passagère d'une perte définitive :
    `api-rest` est démarrée pendant la coupure mais se termine après, donc son
    statut de fin la rattrape ; `modele-persistance`, elle, tient tout entière
    dans le quart d'heure — et une coupure de quinze minutes contient beaucoup de
    tâches. Rien après elle n'en reparlera : ce que le journal n'a pas gardé, le
    run ne le redira jamais.

    `coupure=None` est le monde de #699 : il n'y a pas de « pendant », tout est
    publié sur le bus durable. Un bus passé est l'échantillon fautif — il reçoit
    le milieu et n'en garde rien, la pompe consignant le reste.
    """
    durable = coupure is None
    bus = BusDurable(bus_d_avant(), log) if durable else bus_d_avant()
    avant = (lancement(), plan_publie(), statut("squelette-p1", STATUT_EN_COURS))
    milieu = (
        statut("squelette-p1", STATUT_TERMINEE),
        statut("modele-persistance", STATUT_EN_COURS),
        statut("modele-persistance", STATUT_TERMINEE),
        statut("api-rest", STATUT_EN_COURS),
    )
    apres = tuple(
        statut(noeud, STATUT_TERMINEE) for noeud in ("api-rest", "ui", "recette")
    )
    if durable:
        publie(bus, *avant, *milieu, *apres)
        return
    pompe_consigne(log, *avant)
    publie(coupure, *milieu)
    pompe_consigne(log, *apres)


def test_le_compte_de_taches_du_run_est_celui_de_son_plan():
    """Le troisième critère : un run fini compte autant de tâches que son plan.

    Il ne l'était plus dès qu'une tâche traversait la coupure de bout en bout —
    et il ne le redevenait **jamais**, pas même le run terminé : `RunJournal` ne
    garde ses étapes qu'en mémoire et le `hote.log` du run était vide, donc il
    n'y avait rien à rattraper après coup. C'est la consignation elle-même qui
    devait cesser de dépendre d'un consommateur.
    """
    log = InMemoryEventLog()
    run_entier(None, log)

    with redemarrage(log) as client:
        execution = resume(client)
        graphe = client.get(f"/api/executions/{RUN}/graphe").json()

    assert execution["nb_taches"] == len(PLAN)
    assert execution["progression"]["total"] == graphe["nb_noeuds"] == len(PLAN)


def test_sans_le_dispositif_une_tache_entiere_manque_au_compte_pour_toujours():
    """L'échantillon fautif : le run est fini, et il lui manque une tâche.

    Pas « pas encore » : `modele-persistance` a été faite, et aucun écran ne la
    montrera jamais — ni le Kanban, ni la barre, ni le journal. Le graphe, lui,
    la dessine, puisque le plan avait été publié avant la coupure : c'est
    exactement la contradiction qu'on lisait à l'écran.
    """
    log = InMemoryEventLog()
    run_entier(bus_d_avant(), log)

    with redemarrage(log) as client:
        execution = resume(client)
        graphe = client.get(f"/api/executions/{RUN}/graphe").json()
        assert carte(client, "modele-persistance") is None

    assert execution["progression"]["total"] == len(PLAN) - 1
    assert graphe["nb_noeuds"] == len(PLAN)


# ------------------ ④ publié une fois, consigné une fois (API en marche)


@pytest.fixture()
def client_reel(tmp_path) -> TestClient:
    """L'app réelle sur bus et journal mémoire, dépôts temporaires.

    C'est le régime « API en marche » : la pompe tourne, s'abonne et indexe. Le
    journal durable est rendu à côté pour qu'on puisse le compter.
    """
    log = InMemoryEventLog()
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        event_log=log,
        capacites=CapacityStore(tmp_path / "capacite"),
        agents_store=AgentStore(tmp_path / "agents"),
    )
    with TestClient(app) as client:
        client.journal_durable = log  # type: ignore[attr-defined]
        yield client


def test_un_evenement_publie_par_l_api_n_est_consigne_qu_une_fois(client_reel):
    """Le quatrième critère — et la raison pour laquelle la pompe a dû se taire.

    L'API publie et consigne dans le même geste ; sa pompe reçoit ensuite le même
    événement pour le projeter et l'indexer. Si elle consignait encore, chaque
    geste posé depuis la Control Tower laisserait **deux** lignes au journal
    durable, donc deux entrées `j-000N` au journal requêtable — un `Event` n'a
    pas d'identifiant, il n'y a aucun dédoublonnage pour rattraper ça.
    """
    reponse = client_reel.post("/api/agents/qa/capacite", json={"actif": False})
    assert reponse.status_code == 200, reponse.text

    log = client_reel.journal_durable
    capacites = [e for e in evenements(log) if e.type == EVENEMENT_AGENT_CAPACITE]
    assert len(capacites) == 1

    route = f"/api/journal?projet=tous&type={EVENEMENT_AGENT_CAPACITE}"
    attendre(
        lambda: client_reel.get(route).json()["total"] >= 1,
        "l'événement indexé au journal requêtable",
    )
    page = client_reel.get(route).json()
    assert page["total"] == 1
    assert [entree["statut"] for entree in page["entrees"]] == ["desactive"]


def test_ce_que_l_api_publie_est_acquis_avant_meme_que_sa_pompe_l_ait_vu(client_reel):
    """Corollaire du déplacement : la durabilité ne court plus après un ordonnancement.

    Elle avait lieu dans la pompe, c'est-à-dire *plus tard* et *ailleurs* ; elle
    a lieu dans l'appel qui publie. Un événement est donc au journal à l'instant
    où la route qui l'émet a répondu — y compris si l'API tombe juste après.
    """
    client_reel.post("/api/agents/qa/capacite", json={"actif": False})

    assert any(
        e.type == EVENEMENT_AGENT_CAPACITE for e in evenements(client_reel.journal_durable)
    )


# ------------------------- ⑤ la production, et les promesses conservées


class TuyauFactice:
    """Le `MULTI`/`EXEC` du client synchrone : on accumule, `execute` applique."""

    def __init__(self, client: ClientRedisFactice) -> None:
        self._client = client
        self._gestes: list[tuple[str, str, str]] = []

    def rpush(self, cle: str, valeur: str) -> TuyauFactice:
        self._gestes.append(("rpush", cle, valeur))
        return self

    def publish(self, canal: str, valeur: str) -> TuyauFactice:
        self._gestes.append(("publish", canal, valeur))
        return self

    def execute(self) -> list[Any]:
        for geste, cible, valeur in self._gestes:
            if geste == "rpush":
                self._client.listes.setdefault(cible, []).append(valeur.encode())
            else:
                self._client.publiés.append((cible, valeur))
        self._gestes.clear()
        return []


class ClientRedisFactice:
    """Le strict nécessaire d'un client Redis : une liste, un canal, et rien d'autre.

    Il sert les **deux** clients à la fois — le synchrone de `publieur_redis` et
    l'asynchrone de `RedisEventLog` —, ce qui est le sujet : en production ce
    n'est pas la même bibliothèque mais c'est la même **instance**, et une liste
    écrite d'un côté doit se relire de l'autre. Il rend des **octets**, comme le
    vrai client par défaut, ce qui exerce la relecture de `Event.from_json`.
    """

    def __init__(self) -> None:
        self.listes: dict[str, list[bytes]] = {}
        self.publiés: list[tuple[str, str]] = []

    def pipeline(self) -> TuyauFactice:
        return TuyauFactice(self)

    async def lrange(self, cle: str, debut: int, fin: int) -> list[bytes]:
        assert (debut, fin) == (0, -1)  # le journal se relit en entier
        return list(self.listes.get(cle, []))

    async def aclose(self) -> None:
        return None


@pytest.fixture()
def redis_factice(monkeypatch) -> ClientRedisFactice:
    """Une seule instance Redis, servie aux deux fabriques de clients.

    `from_url` est remplacée sur les deux classes plutôt que sur un module :
    `publieur_redis` et `RedisEventLog` importent `redis` **localement**, donc
    résolvent l'attribut à l'appel — un module doublé au niveau de `sys.modules`
    ne serait pas vu.
    """
    faux = ClientRedisFactice()
    monkeypatch.setattr(redis.Redis, "from_url", lambda *_a, **_k: faux)
    monkeypatch.setattr(redis.asyncio.Redis, "from_url", lambda *_a, **_k: faux)
    return faux


def test_le_publieur_du_pont_ecrit_la_liste_que_l_api_relit(redis_factice):
    """La clé partagée, seul endroit où les deux moitiés du dispositif se rejoignent.

    C'est **le** chemin de l'incident : le pont télémétrie publie en synchrone
    (le handler `logging` l'est), l'API relit en asynchrone ; deux clients, deux
    fabriques, deux fichiers. Rien ne les rapproche sauf
    `CLE_JOURNAL_EVENEMENTS`, et une divergence y serait invisible partout
    ailleurs — chaque moitié marcherait parfaitement, et l'historique de tout run
    publié hors de l'API serait troué pour toujours (même leçon que
    `CLE_BATTEMENTS`, #351).
    """
    publieur_redis()(statut("squelette-p1", STATUT_TERMINEE))

    assert set(redis_factice.listes) == {CLE_JOURNAL_EVENEMENTS}
    relus = asyncio.run(RedisEventLog().relire())
    assert [(e.tache_id, e.statut) for e in relus] == [("squelette-p1", STATUT_TERMINEE)]


def test_le_publieur_du_pont_diffuse_toujours_ce_qu_il_consigne(redis_factice):
    """Consigner **en plus**, jamais **à la place** : le direct n'a pas bougé.

    Les deux gestes tiennent dans un seul aller (`MULTI`/`EXEC`), ce qui rend
    impossible la moitié de panne — un événement diffusé que le journal n'aurait
    pas, ou l'inverse.
    """
    publieur_redis()(statut("squelette-p1", STATUT_TERMINEE))

    (canal, charge) = redis_factice.publiés[0]
    assert canal == CANAL_EVENEMENTS
    assert Event.from_json(charge).tache_id == "squelette-p1"
    assert len(redis_factice.publiés) == 1
    assert len(redis_factice.listes[CLE_JOURNAL_EVENEMENTS]) == 1


def test_construire_les_clients_de_production_n_ouvre_aucune_connexion():
    """Les deux fabriques restent **paresseuses** — construites ici, ouvertes au premier appel.

    C'est ce qui permet à `maestro-run` de câbler son pont et à l'hôte détaché
    d'ouvrir son bus sans exiger un Redis joignable. Le test le vérifie du seul
    endroit où ça se voit : ici, où aucun réseau n'est disponible
    (`tests/conftest.py`). Sans le doublon de la fixture, donc : ce sont les
    **vrais** clients qu'on construit.
    """
    assert callable(publieur_redis("redis://exemple.test:6379/0"))
    assert isinstance(bus_durable("redis://exemple.test:6379/0"), BusDurable)


class JournalEnPanne(InMemoryEventLog):
    """Un journal qui refuse d'écrire — Redis injoignable le temps d'un événement."""

    async def consigner(self, event: Event) -> None:
        raise RuntimeError("journal injoignable")


def test_une_consignation_en_panne_n_interrompt_pas_le_flux_temps_reel():
    """La promesse de la pompe, déplacée avec le geste — pas une promesse en moins.

    Une panne de persistance était tracée sans couper le direct ; elle l'est
    toujours, à ceci près qu'elle se trace là où l'on consigne désormais. Le seul
    prix reste le même : cet événement-là manquera au prochain rejeu.
    """
    recus: list[Event] = []

    async def scenario() -> None:
        bus = BusDurable(InMemoryEventBus(), JournalEnPanne())

        async def ecoute() -> None:
            async for event in bus.subscribe():
                recus.append(event)
                return

        async with asyncio.TaskGroup() as tg:
            tg.create_task(ecoute())
            await asyncio.sleep(0)
            await bus.publish(statut("squelette-p1", STATUT_TERMINEE))

    asyncio.run(scenario())

    assert [e.tache_id for e in recus] == ["squelette-p1"]


class BusFermable(InMemoryEventBus):
    def __init__(self) -> None:
        super().__init__()
        self.ferme = False

    async def close(self) -> None:
        self.ferme = True


class JournalFermable(InMemoryEventLog):
    def __init__(self) -> None:
        super().__init__()
        self.ferme = False

    async def close(self) -> None:
        self.ferme = True


def test_le_bus_ne_ferme_le_journal_que_s_il_lui_appartient():
    """Une ressource à deux maîtres est une ressource fermée deux fois.

    Le journal de l'API a son propriétaire — le lifespan, qui le relit au
    démarrage et le referme en partant ; celui qu'une fabrique de production a
    ouvert avec son bus (`bus_durable`) n'en a pas d'autre que le bus lui-même,
    et un appelant qui fermait déjà son bus (`hote_detache`) n'a pas à apprendre
    un second geste.
    """
    emprunte, propre = JournalFermable(), JournalFermable()

    asyncio.run(BusDurable(BusFermable(), emprunte).close())
    asyncio.run(BusDurable(BusFermable(), propre, possede_le_journal=True).close())

    assert emprunte.ferme is False
    assert propre.ferme is True
