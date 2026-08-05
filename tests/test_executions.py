"""Tests du pilotage d'une exécution depuis la Control Tower (ticket #188, lot 4/4 de #184).

**Ni Redis, ni réseau, ni appel de modèle.** L'app FastAPI est la vraie
(`create_app`), sur le **bus mémoire** et le TestClient de Starlette : routes,
projection, pompe de diffusion et service de pilotage
(`maestro.controltower.executions`) sont ceux de la production. Seul le
**moteur** est remplacé, par l'injection prévue à cet effet
(`create_app(fabrique_moteur=…)`, #185) : c'est ce qui permet d'exercer le
lancement d'un run sans qu'aucun fournisseur ne soit résolu ni qu'aucune tâche
ne soit réellement exécutée.

Deux volets, les deux critères de données de #188 :

① **Routes d'exécution** (#185) — lancer (`POST /api/executions` : 202, `run_id`
   rendu tout de suite, garde-fous transmis au moteur, run visible dans la
   projection), suivre (liste récents d'abord, détail, issue consignée quand le
   run finit — succès, échec partiel, erreur du moteur), annuler
   (`POST …/annuler` : statut `annulee` et `fin` posée, run réellement
   interrompu), et **refuser** ce qui n'est pas lançable (objectif vide,
   garde-fou hors bornes → 422 ; run inconnu → 404 ; run déjà soldé → 409).

② **Référence de ticket externe** (#187) de bout en bout de la donnée : le
   **plan** (schéma de tâche partagé) l'accepte et reste valide sans elle, elle
   traverse la **projection** (`EtatTache.ticket`), sort par le **REST**
   (`GET /api/taches`, `GET /api/executions`) et **survit au rejeu** du journal
   durable — app redémarrée sur le même `EventLog`, projection reconstruite.

③ **Le ticket du lancement descend jusqu'aux tâches** (#187) — le service le passe
   au moteur (`run(…, ticket=…)`), qui en dote **chaque tâche du plan**, sauf celle
   qui en porte déjà une : le plan a été plus précis que le lancement, sa référence
   gagne. Ce dernier volet tourne sur le **vrai moteur**, fournisseurs factices à la
   place des modèles — c'est le seul endroit où le double ne suffirait pas, la règle
   à vérifier étant précisément celle qu'il remplace.
"""

from __future__ import annotations

import asyncio
import json
import time

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
from maestro.controltower.events import ReferenceTicket
from maestro.controltower.state import (
    EXECUTION_ANNULEE,
    EXECUTION_ECHEC,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
)
from maestro.engine import (
    STATUT_ECHEC,
    STATUT_TERMINEE,
    OrchestrationEngine,
    RunReport,
    TaskResult,
)
from maestro.orchestrator import Orchestrator
from maestro.orchestrator.schema import TaskValidationError, validate_plan
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal

#: Plafond d'attente d'un statut de run, en secondes. Le moteur double rend la
#: main tout de suite : c'est l'ordonnancement de la boucle de l'app qu'on
#: attend, pas un travail — largement au-dessus du nécessaire, jamais atteint
#: quand tout va bien.
DELAI_ATTENTE_S = 5.0


# --------------------------------------------------------------- doubles


def _resultat(task_id: str, statut: str = STATUT_TERMINEE) -> TaskResult:
    """Un `TaskResult` minimal — le rapport ne sert ici qu'à compter les issues."""
    return TaskResult(
        task_id=task_id,
        titre=f"Tâche {task_id}",
        agent="developpeur",
        role="Développeur",
        competences_requises=("python",),
        score=1,
        statut=statut,
        sortie="ok" if statut == STATUT_TERMINEE else "",
        erreur=None if statut == STATUT_TERMINEE else "boum",
    )


class MoteurDouble:
    """Moteur injecté à la place du vrai : aucun fournisseur, aucun appel modèle.

    `rapport` est ce que `run` rendra ; `erreur` la fait lever à la place ;
    `bloquant` la fait attendre indéfiniment (le run reste « en cours », de quoi
    exercer l'annulation). Les garde-fous et le parallélisme reçus à la
    construction sont conservés : c'est ainsi qu'on vérifie que le corps de la
    requête arrive bien jusqu'au moteur.
    """

    def __init__(
        self,
        *,
        rapport: RunReport | None = None,
        erreur: Exception | None = None,
        bloquant: bool = False,
    ) -> None:
        self.rapport = rapport if rapport is not None else RunReport(objectif="", resultats=())
        self.erreur = erreur
        self.bloquant = bloquant
        self.appels: list[dict[str, object]] = []
        self.objectifs: list[str] = []
        self.tickets: list[ReferenceTicket | None] = []
        self.projets: list[str | None] = []
        self.annule = False

    def __call__(self, **reglages: object) -> MoteurDouble:
        """La fabrique et le moteur sont le même objet : un run par app suffit ici."""
        self.appels.append(reglages)
        return self

    async def run(
        self,
        objectif: str,
        *,
        journal: object = None,
        ticket: ReferenceTicket | None = None,
        projet_id: str | None = None,
    ) -> RunReport:
        # `ticket` (#187) et `projet_id` (#222) font partie de la signature du vrai
        # moteur : le service les lui passe pour qu'il en dote chaque tâche du plan.
        # Le double la reproduit — sans quoi il ne testerait plus l'appel réellement
        # effectué.
        self.objectifs.append(objectif)
        self.tickets.append(ticket)
        self.projets.append(projet_id)
        if self.erreur is not None:
            raise self.erreur
        if self.bloquant:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.annule = True
                raise
        return self.rapport


@pytest.fixture()
def state():
    """Projection injectée : les tests la relisent directement en plus du REST."""
    return ControlTowerState()


def app_avec(moteur: MoteurDouble, state: ControlTowerState) -> TestClient:
    """TestClient de l'app réelle, bus mémoire, moteur double (contexte = lifespan)."""
    return TestClient(
        create_app(bus=InMemoryEventBus(), state=state, fabrique_moteur=moteur)
    )


def attendre_statut(client: TestClient, run_id: str, statut: str) -> dict:
    """Attend que le run atteigne `statut` et rend son résumé (le run est en fond).

    Le lancement rend la main **avant** que le run ne se déroule : son issue
    arrive quand la tâche de fond a été ordonnancée. On interroge donc l'API —
    la même que l'UI — plutôt que de fouiller les internes du service.
    """
    limite = time.monotonic() + DELAI_ATTENTE_S
    while True:
        resume = client.get(f"/api/executions/{run_id}").json()
        if resume["statut"] == statut:
            return resume
        if time.monotonic() > limite:  # pragma: no cover - filet anti-blocage
            pytest.fail(f"run {run_id} resté en « {resume['statut']} », attendu « {statut} »")
        time.sleep(0.02)


# ------------------------------------------------- ① Routes d'exécution (#185)


def test_lancement_rend_le_run_id_aussitot_et_le_run_est_suivi(state):
    """`POST /api/executions` : 202, résumé complet, run déjà connu de la projection."""
    moteur = MoteurDouble(rapport=RunReport(objectif="Prototyper", resultats=(_resultat("t1"),)))
    with app_avec(moteur, state) as client:
        reponse = client.post("/api/executions", json={"objectif": "Prototyper un mini-CRM"})

        assert reponse.status_code == 202
        resume = reponse.json()
        assert resume["run_id"]  # le pilotage commence par savoir QUEL run a démarré
        assert resume["objectif"] == "Prototyper un mini-CRM"
        assert resume["statut"] == EXECUTION_EN_COURS
        assert resume["fin"] is None
        # La projection connaît le run avant que la réponse ne parte (#185).
        assert state.execution(resume["run_id"]) is not None
        attendre_statut(client, resume["run_id"], EXECUTION_TERMINEE)

    assert moteur.objectifs == ["Prototyper un mini-CRM"]


def test_les_garde_fous_du_corps_arment_le_moteur(state):
    """Plafonds, time-out et parallélisme du corps arrivent jusqu'au moteur (#9)."""
    moteur = MoteurDouble()
    with app_avec(moteur, state) as client:
        reponse = client.post(
            "/api/executions",
            json={
                "objectif": "Prototyper",
                "plafond_cout_usd": 5.0,
                "plafond_tokens": 200000,
                "timeout_tache_s": 600,
                "parallelisme": 3,
            },
        )
        assert reponse.status_code == 202
        attendre_statut(client, reponse.json()["run_id"], EXECUTION_TERMINEE)

    (reglages,) = moteur.appels
    garde_fous = reglages["guardrails"]
    assert garde_fous.plafond_cout_usd == pytest.approx(5.0)
    assert garde_fous.plafond_tokens == 200000
    assert garde_fous.timeout_s == pytest.approx(600)
    assert reglages["max_parallele"] == 3


def test_un_objectif_seul_laisse_les_defauts_du_moteur(state):
    """Aucun garde-fou dans le corps : None partout — le moteur garde ses défauts."""
    moteur = MoteurDouble()
    with app_avec(moteur, state) as client:
        run_id = client.post("/api/executions", json={"objectif": "Prototyper"}).json()["run_id"]
        attendre_statut(client, run_id, EXECUTION_TERMINEE)

    (reglages,) = moteur.appels
    garde_fous = reglages["guardrails"]
    assert (garde_fous.plafond_cout_usd, garde_fous.plafond_tokens) == (None, None)
    assert reglages["max_parallele"] is None


def test_l_issue_du_run_est_consignee_et_lisible(state):
    """Un run qui s'achève passe « terminée » ; son détail dit combien ont réussi."""
    rapport = RunReport(
        objectif="Prototyper",
        resultats=(_resultat("t1"), _resultat("t2")),
    )
    with app_avec(MoteurDouble(rapport=rapport), state) as client:
        run_id = client.post("/api/executions", json={"objectif": "Prototyper"}).json()["run_id"]
        resume = attendre_statut(client, run_id, EXECUTION_TERMINEE)
        assert resume["fin"] is not None  # bornes temporelles closes

        detail = client.get(f"/api/executions/{run_id}").json()

    details = [e["detail"] for e in detail["evenements"]]
    assert "lancée depuis la Control Tower" in details
    assert "2/2 tâche(s) réussie(s)" in details


def test_une_tache_en_echec_solde_le_run_en_echec(state):
    """Le statut du run reflète ses tâches : une échouée ⇒ run « échec »."""
    rapport = RunReport(
        objectif="Prototyper",
        resultats=(_resultat("t1"), _resultat("t2", STATUT_ECHEC)),
    )
    with app_avec(MoteurDouble(rapport=rapport), state) as client:
        run_id = client.post("/api/executions", json={"objectif": "Prototyper"}).json()["run_id"]
        resume = attendre_statut(client, run_id, EXECUTION_ECHEC)

    assert resume["statut"] == EXECUTION_ECHEC


def test_une_erreur_du_moteur_devient_le_statut_du_run(state):
    """Une tâche de fond n'a pas d'appelant : l'exception devient un statut, pas un 500."""
    with app_avec(MoteurDouble(erreur=RuntimeError("fournisseur injoignable")), state) as client:
        run_id = client.post("/api/executions", json={"objectif": "Prototyper"}).json()["run_id"]
        attendre_statut(client, run_id, EXECUTION_ECHEC)

        detail = client.get(f"/api/executions/{run_id}").json()

    assert any("fournisseur injoignable" in e["detail"] for e in detail["evenements"])


def test_liste_des_executions_recents_d_abord_quelle_que_soit_leur_origine(state):
    """`GET /api/executions` : tous les runs connus, du plus récent au plus ancien.

    Les runs **publiés par un autre process** (`maestro-run --publier`, worker
    #41) sont listés comme ceux lancés ici : le suivi lit la projection, il ne
    distingue pas l'origine. Leurs horodatages sont posés explicitement — le
    `debut` d'un run est à la **seconde** près (précision du journal, #8), donc
    deux lancements de la même seconde ne se départageraient pas.
    """
    for run_id, horodatage in (("run-veille", "2026-08-02T09:00:00+00:00"),
                               ("run-matin", "2026-08-03T08:00:00+00:00")):
        state.appliquer(
            Event(
                type=EVENEMENT_TACHE_STATUT,
                run_id=run_id,
                tache_id=f"t-{run_id}",
                titre="Tâche d'un run venu d'ailleurs",
                agent="developpeur",
                role="Développeur",
                statut=STATUT_TERMINEE,
                horodatage=horodatage,
            )
        )

    with app_avec(MoteurDouble(), state) as client:
        lance = client.post("/api/executions", json={"objectif": "Lancé d'ici"}).json()
        attendre_statut(client, lance["run_id"], EXECUTION_TERMINEE)

        liste = client.get("/api/executions").json()

    # Le run lancé à l'instant est le plus récent ; les deux autres suivent dans
    # l'ordre décroissant de leur début.
    assert [r["run_id"] for r in liste] == [lance["run_id"], "run-matin", "run-veille"]


def test_annulation_interrompt_le_run_en_vol(state):
    """`POST …/annuler` : statut « annulée », `fin` posée, tâche de fond réellement coupée."""
    moteur = MoteurDouble(bloquant=True)
    with app_avec(moteur, state) as client:
        run_id = client.post("/api/executions", json={"objectif": "Long"}).json()["run_id"]
        # Le run doit être PARTI avant d'être annulable : on attend que le moteur
        # ait reçu l'objectif (la tâche de fond a été ordonnancée).
        limite = time.monotonic() + DELAI_ATTENTE_S
        while not moteur.objectifs and time.monotonic() < limite:
            time.sleep(0.02)

        annulee = client.post(f"/api/executions/{run_id}/annuler").json()

        assert annulee["statut"] == EXECUTION_ANNULEE
        assert annulee["fin"] is not None
        assert client.get(f"/api/executions/{run_id}").json()["statut"] == EXECUTION_ANNULEE

    assert moteur.annule  # le moteur a bien été interrompu, pas seulement l'affichage


def test_annuler_un_run_inconnu_404(state):
    with app_avec(MoteurDouble(), state) as client:
        reponse = client.post("/api/executions/nulle-part/annuler")

    assert reponse.status_code == 404
    assert "nulle-part" in reponse.json()["detail"]


def test_annuler_un_run_deja_solde_409(state):
    """Un run terminé n'est plus interruptible — le dire vaut mieux que faire semblant."""
    with app_avec(MoteurDouble(), state) as client:
        run_id = client.post("/api/executions", json={"objectif": "Court"}).json()["run_id"]
        attendre_statut(client, run_id, EXECUTION_TERMINEE)

        reponse = client.post(f"/api/executions/{run_id}/annuler")

    assert reponse.status_code == 409
    assert EXECUTION_TERMINEE in reponse.json()["detail"]


@pytest.mark.parametrize(
    "corps",
    [
        pytest.param({"objectif": ""}, id="objectif-vide"),
        pytest.param({"objectif": "   "}, id="objectif-blanc"),
        pytest.param({"objectif": "Prototyper", "plafond_cout_usd": 0}, id="cout-nul"),
        pytest.param({"objectif": "Prototyper", "plafond_tokens": -1}, id="tokens-negatifs"),
        pytest.param({"objectif": "Prototyper", "timeout_tache_s": 0}, id="timeout-nul"),
        pytest.param({"objectif": "Prototyper", "parallelisme": -2}, id="parallelisme-negatif"),
    ],
)
def test_une_requete_invalide_est_refusee_sans_lancer_de_run(corps, state):
    """422 sur un lancement mal formé — et **aucun** run n'est parti pour autant."""
    moteur = MoteurDouble()
    with app_avec(moteur, state) as client:
        reponse = client.post("/api/executions", json=corps)

        assert reponse.status_code == 422
        assert client.get("/api/executions").json() == []

    assert moteur.appels == []  # le moteur n'a même pas été construit


def test_un_corps_sans_objectif_est_refuse(state):
    """Le champ `objectif` est requis : le refus vient de la validation du modèle."""
    with app_avec(MoteurDouble(), state) as client:
        assert client.post("/api/executions", json={}).status_code == 422


# --------------------------- ② Référence de ticket externe, bout en bout (#187)


def _tache_du_plan(**extra) -> dict:
    """Une tâche de plan valide, à laquelle les scénarios ajoutent leur `ticket`."""
    return {
        "id": "schema-bdd",
        "titre": "Concevoir le schéma",
        "description": "Modéliser les entités du mini-CRM.",
        "competences_requises": ["sql"],
        "format_sortie": "markdown",
        **extra,
    }


def test_le_plan_accepte_une_reference_de_ticket():
    """Maillon 1 — le schéma de tâche partagé porte `{id, url}` (#187)."""
    (tache,) = validate_plan(
        [_tache_du_plan(ticket={"id": "#188", "url": "https://gitlab.example/issues/188"})]
    )

    assert tache.id == "schema-bdd"


def test_un_plan_sans_reference_reste_valide():
    """La référence est **optionnelle** : son absence n'invalide rien."""
    (tache,) = validate_plan([_tache_du_plan()])

    assert tache.id == "schema-bdd"


@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param({}, id="sans-identifiant"),
        pytest.param({"id": ""}, id="identifiant-vide"),
        pytest.param({"id": "#188", "outil": "gitlab"}, id="champ-propre-a-un-outil"),
    ],
)
def test_une_reference_mal_formee_est_refusee(ticket):
    """Générique par construction : un `id` non vide, une `url`, et rien d'autre."""
    with pytest.raises(TaskValidationError):
        validate_plan([_tache_du_plan(ticket=ticket)])


def test_la_reference_traverse_projection_et_rest(state):
    """Maillons 2 et 3 — l'événement de tâche la pose, `GET /api/taches` la sert."""
    with app_avec(MoteurDouble(), state) as client:
        state.appliquer(
            Event(
                type=EVENEMENT_TACHE_STATUT,
                run_id="run-1",
                tache_id="t1",
                titre="Concevoir le schéma",
                agent="bdd",
                role="Base de données",
                statut=STATUT_TERMINEE,
                ticket=ReferenceTicket(id="#188", url="https://gitlab.example/issues/188"),
            )
        )

        (tache,) = client.get("/api/taches").json()

    assert state.tache("t1").ticket == ReferenceTicket(
        id="#188", url="https://gitlab.example/issues/188"
    )
    assert tache["ticket"] == {"id": "#188", "url": "https://gitlab.example/issues/188"}


def test_une_tache_sans_reference_la_sert_a_null(state):
    """`null` vaut « aucune référence » — distinct d'une chaîne vide (contrat #183)."""
    with app_avec(MoteurDouble(), state) as client:
        state.appliquer(
            Event(
                type=EVENEMENT_TACHE_STATUT,
                run_id="run-1",
                tache_id="t1",
                titre="Sans ticket",
                agent="bdd",
                role="Base de données",
                statut=STATUT_TERMINEE,
            )
        )

        (tache,) = client.get("/api/taches").json()

    assert tache["ticket"] is None


def test_la_reference_survit_au_rejeu_du_journal():
    """Maillon 4 — app redémarrée sur le même journal : la référence est rejouée.

    C'est la propriété qui la rend fiable côté UI : la carte porte son lien même
    après un redémarrage de l'API, sans que personne n'ait à la reposer.
    """
    journal = InMemoryEventLog()
    evenement = Event(
        type=EVENEMENT_TACHE_STATUT,
        run_id="run-persistant",
        tache_id="t1",
        titre="Concevoir le schéma",
        agent="bdd",
        role="Base de données",
        statut=STATUT_TERMINEE,
        ticket=ReferenceTicket(id="#188", url="https://gitlab.example/issues/188"),
    )

    asyncio.run(journal.consigner(evenement))  # la « session précédente »

    app = create_app(bus=InMemoryEventBus(), state=ControlTowerState(), event_log=journal)
    with TestClient(app) as redemarree:
        (tache,) = redemarree.get("/api/taches").json()

    assert tache["ticket"] == {"id": "#188", "url": "https://gitlab.example/issues/188"}


def test_le_ticket_du_lancement_est_rendu_avec_le_run(state):
    """L'origine du run peut porter la référence : elle revient dans son résumé (#185)."""
    with app_avec(MoteurDouble(), state) as client:
        lance = client.post(
            "/api/executions",
            json={
                "objectif": "Traiter le ticket",
                "ticket": {"id": "#188", "url": "https://gitlab.example/issues/188"},
            },
        ).json()
        attendre_statut(client, lance["run_id"], EXECUTION_TERMINEE)

        (liste,) = client.get("/api/executions").json()

    attendu = {"id": "#188", "url": "https://gitlab.example/issues/188"}
    assert lance["ticket"] == attendu
    assert liste["ticket"] == attendu


def test_un_run_sans_ticket_le_rend_a_null(state):
    """Sans référence au lancement, le résumé porte `null` — pas de forme vide."""
    with app_avec(MoteurDouble(), state) as client:
        lance = client.post("/api/executions", json={"objectif": "Sans ticket"}).json()
        attendre_statut(client, lance["run_id"], EXECUTION_TERMINEE)

    assert lance["ticket"] is None


def test_le_ticket_du_lancement_descend_jusqu_au_moteur(state):
    """Le service ne se contente pas d'afficher la référence : il la **passe** au moteur."""
    moteur = MoteurDouble()
    with app_avec(moteur, state) as client:
        run_id = client.post(
            "/api/executions",
            json={
                "objectif": "Traiter le ticket",
                "ticket": {"id": "#188", "url": "https://gitlab.example/issues/188"},
            },
        ).json()["run_id"]
        attendre_statut(client, run_id, EXECUTION_TERMINEE)

    assert moteur.tickets == [
        ReferenceTicket(id="#188", url="https://gitlab.example/issues/188")
    ]


def test_un_lancement_sans_ticket_n_en_invente_pas(state):
    """Sans référence, le moteur en reçoit `None` — pas une forme vide à démêler ensuite."""
    moteur = MoteurDouble()
    with app_avec(moteur, state) as client:
        run_id = client.post("/api/executions", json={"objectif": "Sans ticket"}).json()["run_id"]
        attendre_statut(client, run_id, EXECUTION_TERMINEE)

    assert moteur.tickets == [None]


# ------------------- ③ Du run aux tâches : le vrai moteur, fournisseurs factices


class ProviderConstant(ModelProvider):
    """Rend toujours la même réponse — sert de planificateur comme d'exécutant."""

    name = "constant"

    def __init__(self, reponse: str) -> None:
        self._reponse = reponse

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._reponse


def moteur_sur_plan(*taches: dict) -> OrchestrationEngine:
    """Un vrai `OrchestrationEngine` dont le plan est figé — aucun appel modèle réel."""
    planificateur = ProviderConstant(json.dumps(list(taches), ensure_ascii=False))
    return OrchestrationEngine(
        ProviderConstant("livrable"),
        Orchestrator(planificateur, model="claude-opus-4-8"),
    )


def tickets_des_taches(journal: RunJournal) -> dict[str, object]:
    """Le ticket consigné pour chaque tâche (les étapes techniques écartées)."""
    return {
        trace.etape: trace.ticket
        for trace in journal.records
        if trace.etape not in {"planification"} and not trace.etape.endswith(":debut")
    }


def test_le_ticket_du_run_dote_chaque_tache_du_plan():
    """Une référence posée au lancement se retrouve sur **toutes** les tâches (#187)."""
    moteur = moteur_sur_plan(_tache_du_plan(), _tache_du_plan(id="api", titre="Exposer l'API"))
    journal = RunJournal(run_id="run-dote")
    reference = ReferenceTicket(id="#188", url="https://gitlab.example/issues/188")

    asyncio.run(moteur.run("Prototyper", journal=journal, ticket=reference))

    assert tickets_des_taches(journal) == {"schema-bdd": reference, "api": reference}


def test_la_reference_du_plan_gagne_sur_celle_du_lancement():
    """Le plan a été plus précis que le lancement : c'est **sa** référence qui reste."""
    propre = {"id": "#42", "url": "https://gitlab.example/issues/42"}
    moteur = moteur_sur_plan(
        _tache_du_plan(ticket=propre),
        _tache_du_plan(id="api", titre="Exposer l'API"),
    )
    journal = RunJournal(run_id="run-mixte")

    asyncio.run(
        moteur.run(
            "Prototyper",
            journal=journal,
            ticket=ReferenceTicket(id="#188", url="https://gitlab.example/issues/188"),
        )
    )

    # La tâche qui portait la sienne la garde ; l'autre hérite de celle du run.
    assert tickets_des_taches(journal) == {
        "schema-bdd": ReferenceTicket(**propre),
        "api": ReferenceTicket(id="#188", url="https://gitlab.example/issues/188"),
    }


def test_un_run_sans_ticket_laisse_les_taches_sans_reference():
    """Rien n'est inventé : sans référence au lancement, les tâches n'en portent pas."""
    moteur = moteur_sur_plan(_tache_du_plan())
    journal = RunJournal(run_id="run-nu")

    asyncio.run(moteur.run("Prototyper", journal=journal))

    assert tickets_des_taches(journal) == {"schema-bdd": None}
