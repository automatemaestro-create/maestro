"""La **pause** d'un run, et ce qui la sépare d'une annulation (#477 ; couvert ici par #480).

Le lot 5 du chantier #472 est le seul à toucher la boucle d'exécution, et le seul
dont le critère se formule en **négatif** : une pause ne tue rien. Trois faits en
découlent, et ce sont les trois que le ticket #480 nomme —

① **aucune tâche nouvelle n'est lancée** tant que la porte est fermée ;
② **les tâches en vol vont à leur terme** — c'est toute la différence avec
   `annuler`, qui appelle `Task.cancel()` et perd l'appel modèle déjà payé ;
③ **l'état est retrouvé après un redémarrage de l'API** — l'ordre emprunte le
   canal de l'annulation (`execution.statut`), donc il est dans le journal
   durable et se rejoue avec lui.

Quatre étages, du plus profond au plus visible :

- **la porte** (`maestro.engine.pause.PorteExecution`) — un `asyncio.Event`
  inversé, ouverte par défaut et **réutilisable** (un run se suspend, reprend, se
  resuspend), qui reste annulable ;
- **le moteur** (`maestro.engine.loop`) — la porte est franchie **avant** le
  sémaphore, et **après** le test de dépendances : une tâche qui attend n'occupe
  pas de créneau, et un blocage aval cascade même en pause ;
- **le service** (`maestro.controltower.executions`) — l'événement et la porte
  posés systématiquement, le battement **non** oublié, le statut **non** touché ;
- **les routes et la projection** — les deux 404, les trois 409, la sortie
  anticipée de `_applique_execution_statut` et la survie au rejeu.

**Ni Redis, ni réseau, ni appel modèle.** Le moteur est le vrai là où la règle à
vérifier est la sienne (fournisseurs factices à la place des modèles), un double
partout ailleurs.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import (
    ControlTowerState,
    Event,
    InMemoryEventBus,
    InMemoryEventLog,
    create_app,
    hote_detache,
)
from maestro.controltower.battement import RegistreBattementsMemoire
from maestro.controltower.executions import ServiceExecutions
from maestro.controltower.hote import HoteRun, OrdreRun
from maestro.controltower.state import (
    EVENEMENT_EXECUTION_STATUT,
    EXECUTION_ANNULEE,
    EXECUTION_EN_ATTENTE_BRIEF,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    ORDRE_PAUSE,
    ORDRE_REPRISE,
    ORDRES_PAUSE,
)
from maestro.engine import STATUT_BLOQUEE, STATUT_TERMINEE, OrchestrationEngine
from maestro.engine.pause import PorteExecution
from maestro.orchestrator import Orchestrator
from maestro.providers.base import ModelProvider

RUN = "run-suspendu"
PROJET = "prj-0002"
TRANSVERSE = "tous"

#: Plafond d'attente d'un rendez-vous entre coroutines. Jamais atteint quand tout
#: va bien : ce qu'on synchronise est une barrière, pas un travail.
DELAI_S = 5.0


# ------------------------------------------------------- ① La porte elle-même


def test_une_porte_neuve_est_ouverte_et_se_franchit_sans_attendre():
    """Le défaut porte tout le lot : un moteur qui ignore la pause ne doit pas figer.

    L'oubli du `set()` initial figerait **tous** les runs du dépôt — en faire
    l'état de construction retire l'oubli possible.
    """
    porte = PorteExecution()

    assert porte.ouverte is True
    asyncio.run(asyncio.wait_for(porte.franchir(), timeout=DELAI_S))


def test_une_porte_fermee_retient_puis_laisse_repartir_ce_qui_attendait():
    async def scenario() -> tuple[bool, bool]:
        porte = PorteExecution()
        porte.fermer()
        passee = asyncio.create_task(porte.franchir())
        await asyncio.sleep(0)  # laisse la tâche atteindre l'attente
        retenue = not passee.done()
        porte.ouvrir()
        await asyncio.wait_for(passee, timeout=DELAI_S)
        return retenue, passee.done()

    retenue, repartie = asyncio.run(scenario())

    assert retenue is True
    assert repartie is True


def test_la_porte_est_reutilisable_un_run_se_suspend_puis_se_resuspend():
    """La différence de nature avec l'annulation, dont l'ordre est définitif.

    Une forme « à usage unique » (un `Future`, un guet qui sortirait à `True`)
    interdirait le deuxième cycle — or c'est le geste normal.
    """
    porte = PorteExecution()

    porte.fermer()
    assert porte.ouverte is False
    porte.ouvrir()
    assert porte.ouverte is True
    porte.fermer()
    assert porte.ouverte is False


@pytest.mark.parametrize("geste", ["ouvrir", "fermer"])
def test_les_deux_gestes_sont_idempotents(geste):
    porte = PorteExecution()

    getattr(porte, geste)()
    getattr(porte, geste)()

    assert porte.ouverte is (geste == "ouvrir")


def test_une_annulation_traverse_une_porte_fermee():
    """Un run suspendu reste un run qu'on peut arrêter — sans quoi la pause piégerait."""

    async def scenario() -> None:
        porte = PorteExecution(ouverte=False)
        attente = asyncio.create_task(porte.franchir())
        await asyncio.sleep(0)
        attente.cancel()
        await attente

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())


# ------------------------------------------- ② Le moteur : ce qui part, ce qui attend


def _plan_de_deux_taches() -> str:
    """Deux tâches **enchaînées** : la seconde attend la première."""
    return json.dumps(
        [
            {
                "id": "schema-bdd",
                "titre": "Schéma BDD",
                "description": "Définir le schéma.",
                "competences_requises": ["sql"],
                "format_sortie": "SQL",
                "dependances": [],
            },
            {
                "id": "api-taches",
                "titre": "API des tâches",
                "description": "Endpoints CRUD.",
                "competences_requises": ["backend"],
                "format_sortie": "Module",
                "dependances": ["schema-bdd"],
            },
        ],
        ensure_ascii=False,
    )


class PlanificateurConstant(ModelProvider):
    """Rend toujours le même plan — sert d'orchestrateur sans appeler de modèle."""

    name = "plan-constant"

    def __init__(self, plan: str) -> None:
        self._plan = plan

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._plan


class ExecutantObservable(ModelProvider):
    """Compte les tâches **parties** et sait en retenir une en vol.

    `en_vol` se lève dès qu'une tâche est chez l'exécuteur, `laisser_finir` la
    libère : c'est ce qui permet d'observer une pause posée pendant qu'une tâche
    travaille — l'exact scénario du critère ②.
    """

    name = "observable"

    def __init__(self, *, retenir_la_premiere: bool = False) -> None:
        self.departs: list[str] = []
        self.retenir = retenir_la_premiere
        self.en_vol = asyncio.Event()
        self.laisser_finir = asyncio.Event()

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.departs.append(prompt)
        if self.retenir and len(self.departs) == 1:
            self.en_vol.set()
            await asyncio.wait_for(self.laisser_finir.wait(), timeout=DELAI_S)
        return f"LIVRABLE #{len(self.departs)}"


def _moteur(executant: ModelProvider, plan: str) -> OrchestrationEngine:
    orchestrateur = Orchestrator(PlanificateurConstant(plan), model="claude-opus-4-8")
    return OrchestrationEngine(executant, orchestrateur)


def test_porte_fermee_aucune_tache_nouvelle_n_atteint_l_executeur():
    """Le premier des trois faits : on ne lance plus.

    La porte est fermée **avant** que le run ne démarre : pas une seule tâche ne
    part, et le run ne se termine pas de lui-même — il attend, ce qui est
    exactement ce qu'on lui demande.
    """
    executant = ExecutantObservable()

    async def scenario() -> tuple[list[str], bool]:
        porte = PorteExecution(ouverte=False)
        run = asyncio.create_task(
            _moteur(executant, _plan_de_deux_taches()).run("Objectif", porte=porte)
        )
        await asyncio.sleep(0.05)
        partis, fini = list(executant.departs), run.done()
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        return partis, fini

    partis, fini = asyncio.run(scenario())

    assert partis == []
    assert fini is False


def test_une_tache_en_vol_va_a_son_terme_malgre_la_pause():
    """Le deuxième fait, et **la** différence avec l'annulation.

    Une tâche déjà chez l'exécuteur n'a plus de porte devant elle : l'appel modèle
    déjà payé n'est pas perdu, le livrable est écrit. `annuler` ferait l'inverse —
    c'est son propos, et c'est pourquoi les deux gestes existent.
    """
    executant = ExecutantObservable(retenir_la_premiere=True)

    async def scenario() -> tuple[list[str], list[str]]:
        porte = PorteExecution()
        run = asyncio.create_task(
            _moteur(executant, _plan_de_deux_taches()).run("Objectif", porte=porte)
        )
        await asyncio.wait_for(executant.en_vol.wait(), timeout=DELAI_S)
        porte.fermer()  # la pause tombe pendant que la 1re tâche travaille
        executant.laisser_finir.set()
        await asyncio.sleep(0.05)
        apres_la_premiere = list(executant.departs)
        porte.ouvrir()
        rapport = await asyncio.wait_for(run, timeout=DELAI_S)
        return apres_la_premiere, [r.statut for r in rapport.resultats]

    apres_la_premiere, statuts = asyncio.run(scenario())

    # La tâche en vol a fini — mais la suivante n'est jamais partie porte fermée.
    assert len(apres_la_premiere) == 1
    # Et une fois la porte rouverte, le run reprend **là où il en était** : il ne
    # rejoue pas la première tâche, il déroule la seconde.
    assert statuts == [STATUT_TERMINEE, STATUT_TERMINEE]


def test_un_blocage_aval_cascade_meme_porte_fermee():
    """Consigner un blocage n'engage rien — le retenir figerait la lecture du run.

    C'est la raison pour laquelle la porte est franchie **après** le test de
    dépendances : un run suspendu serait sinon indiscernable d'un run figé.
    """

    class ExecutantQuiEchoue(ModelProvider):
        name = "echoue"

        def supports(self, model: str) -> bool:
            return True

        async def generate(self, prompt, *, model, system_prompt=None):
            raise RuntimeError("boum")

    async def scenario() -> list[str]:
        porte = PorteExecution()
        # Le même plan enchaîné : la première tâche échoue, donc la seconde n'a
        # plus de dépendance satisfaite — c'est la cascade qu'on observe.
        moteur = _moteur(ExecutantQuiEchoue(), _plan_de_deux_taches())
        run = asyncio.create_task(moteur.run("Objectif", porte=porte))
        await asyncio.sleep(0.05)
        porte.fermer()
        rapport = await asyncio.wait_for(run, timeout=DELAI_S)
        return [r.statut for r in rapport.resultats]

    statuts = asyncio.run(scenario())

    # La tâche amont a échoué avant la pause ; son aval est **bloqué** et non
    # retenu à la porte — le run se solde au lieu de rester en attente.
    assert STATUT_BLOQUEE in statuts


def test_sans_porte_le_moteur_se_comporte_comme_avant_le_lot():
    """`porte=None` est le défaut, et il ne franchit rien."""
    executant = ExecutantObservable()
    rapport = asyncio.run(_moteur(executant, _plan_de_deux_taches()).run("Objectif"))

    assert len(rapport.resultats) == 2
    assert len(executant.departs) == 2


# ---------------------------------------------- ③ Le service : ce qu'il pose


class HoteMuet(HoteRun):
    """Un hôte qui accepte tout et ne porte rien — le service est le sujet, pas lui."""

    def __init__(self) -> None:
        self.lances: list[OrdreRun] = []
        self.annules: list[str] = []

    async def lancer(self, ordre_du_run: OrdreRun) -> None:
        self.lances.append(ordre_du_run)

    async def annuler(self, run_id: str, *, delai_s: float) -> bool:
        self.annules.append(run_id)
        return False

    def en_vol(self, run_id: str) -> bool:
        return False

    def runs_en_vol(self) -> tuple[str, ...]:
        return ()

    def ramasser(self):
        return ()

    async def fermer(self, *, delai_s: float) -> None:
        return None


def _service(**reglages) -> tuple[ServiceExecutions, ControlTowerState, HoteMuet]:
    """Un pilote sur bus mémoire, projection neuve et hôte muet."""
    hote = HoteMuet()
    projection = ControlTowerState()
    pilote = ServiceExecutions(InMemoryEventBus(), projection, hote=hote, **reglages)
    return pilote, projection, hote


def _pose_un_run(
    pilote: ServiceExecutions, statut: str = EXECUTION_EN_COURS, run_id: str = RUN
) -> None:
    """Inscrit un run dans la projection, comme le ferait son lancement."""
    pilote._consigne(run_id, statut, "Objectif", "lancée depuis la Control Tower")


#: ⚠ Un service qui a suspendu un run a **armé sa pompe** sur la boucle courante
#: (`_demarrer`), et cette boucle meurt avec l'`asyncio.run` qui l'a ouverte : un
#: second `asyncio.run` sur le même service publierait sur une boucle fermée. Tout
#: enchaînement de gestes se joue donc **dans un seul** `asyncio.run`, d'où ce
#: passe-plat plutôt qu'un `async def scenario()` recopié à chaque test.
def _enchaine(*gestes):
    """Joue une suite de coroutines sur **une seule** boucle, et rend leurs retours."""

    async def scenario():
        return [await geste() for geste in gestes]

    return asyncio.run(scenario())


def _releve(etats: list[bool], porte: PorteExecution):
    """Un « geste » qui n'en est pas un : il note l'état de la porte au passage."""

    async def noter() -> None:
        etats.append(porte.ouverte)

    return noter


def test_suspendre_un_run_inconnu_rend_none_sans_rien_consigner():
    """Le service ne juge de rien d'autre — c'est la route qui a un code à rendre."""
    pilote, projection, _ = _service()

    assert asyncio.run(pilote.mettre_en_pause("run-fantome")) is None
    assert asyncio.run(pilote.reprendre("run-fantome")) is None
    assert projection.execution("run-fantome") is None


def test_l_ordre_de_pause_emprunte_le_canal_de_l_annulation():
    """Pas de second transport : `execution.statut`, donc le journal durable.

    C'est ce qui le fait traverser la frontière d'exécution (le process détaché
    guette déjà ce canal) **et** survivre au redémarrage de l'API.
    """
    pilote, projection, _ = _service()
    _pose_un_run(pilote)

    asyncio.run(pilote.mettre_en_pause(RUN))

    ordres = [
        e.statut
        for e in projection.execution(RUN).evenements
        if e.type == EVENEMENT_EXECUTION_STATUT
    ]
    assert ordres[-1] == ORDRE_PAUSE
    assert ORDRE_PAUSE in ORDRES_PAUSE


def test_la_pause_ferme_la_porte_du_run_qu_on_deroule_ici():
    pilote, _, _ = _service()
    _pose_un_run(pilote)
    porte = PorteExecution()
    pilote._portes[RUN] = porte
    etats: list[bool] = []

    _enchaine(
        lambda: pilote.mettre_en_pause(RUN),
        _releve(etats, porte),
        lambda: pilote.reprendre(RUN),
        _releve(etats, porte),
    )

    assert etats == [False, True]


def test_la_pause_d_un_run_detache_ne_manque_de_rien_faute_de_porte_locale():
    """`self._portes` est vide pour un run détaché — l'événement suffit, et il part."""
    pilote, projection, _ = _service()
    _pose_un_run(pilote)

    resume = asyncio.run(pilote.mettre_en_pause(RUN))

    assert pilote._portes == {}
    assert resume["en_pause"] is True
    assert projection.execution(RUN).en_pause is True


def test_la_pause_n_oublie_pas_le_battement_du_run():
    """Sans quoi il ressortirait orphelin, et #349 proposerait de repayer son cadrage.

    C'est l'un des trois gestes que la pause s'interdit, et le plus coûteux à
    oublier : le cadrage qu'elle **préserve** serait rejoué.
    """
    battements = RegistreBattementsMemoire()
    pilote, _, _ = _service(battements=battements)
    _pose_un_run(pilote)
    asyncio.run(battements.battre(RUN))

    asyncio.run(pilote.mettre_en_pause(RUN))

    assert RUN in asyncio.run(battements.battements())


def test_la_pause_ne_demande_a_personne_d_interrompre_quoi_que_ce_soit():
    """L'exact inverse du geste : appeler l'hôte tuerait le travail en vol."""
    pilote, _, hote = _service()
    _pose_un_run(pilote)

    asyncio.run(pilote.mettre_en_pause(RUN))

    assert hote.annules == []


# ------------------------- ④ La projection : un drapeau à côté du statut


def test_la_pause_ne_touche_pas_au_statut_du_run():
    """Les deux faits coexistent — un run suspendu **pendant** l'attente de son brief
    doit continuer de montrer qu'il attend ce brief.

    Un statut `en_pause` aurait été écrasé par la demande de brief qui suit,
    laissant une porte fermée que plus rien à l'écran ne permettait de rouvrir.
    """
    pilote, projection, _ = _service()
    _pose_un_run(pilote, EXECUTION_EN_ATTENTE_BRIEF)

    asyncio.run(pilote.mettre_en_pause(RUN))

    execution = projection.execution(RUN)
    assert execution.statut == EXECUTION_EN_ATTENTE_BRIEF
    assert execution.en_pause is True


def test_un_ordre_de_pause_ne_pose_ni_fin_ni_cause():
    """La sortie anticipée de `_applique_execution_statut` : un ordre n'est pas une issue."""
    pilote, projection, _ = _service()
    _pose_un_run(pilote)

    asyncio.run(pilote.mettre_en_pause(RUN))

    execution = projection.execution(RUN)
    assert execution.fin is None
    assert execution.cause == ""


def test_solder_un_run_leve_sa_pause():
    """Un run terminé n'est pas « terminé et suspendu » : `en_pause` retombe."""
    pilote, projection, _ = _service()
    _pose_un_run(pilote)

    async def solder() -> None:
        pilote._consigne(RUN, EXECUTION_TERMINEE, "", "1/1 tâche(s) réussie(s)")

    _enchaine(lambda: pilote.mettre_en_pause(RUN), solder)

    assert projection.execution(RUN).statut == EXECUTION_TERMINEE
    assert projection.execution(RUN).en_pause is False


def test_une_annulation_reste_possible_sur_un_run_suspendu():
    """`en_pause` n'empêche pas l'annulation — la pause ne piège pas le run."""
    pilote, projection, hote = _service()
    _pose_un_run(pilote)

    _enchaine(lambda: pilote.mettre_en_pause(RUN), lambda: pilote.annuler(RUN))

    execution = projection.execution(RUN)
    assert execution.statut == EXECUTION_ANNULEE
    assert execution.en_pause is False
    assert hote.annules == [RUN]


def test_reprendre_repose_le_drapeau_a_faux():
    pilote, projection, _ = _service()
    _pose_un_run(pilote)

    _, resume = _enchaine(lambda: pilote.mettre_en_pause(RUN), lambda: pilote.reprendre(RUN))

    assert resume["en_pause"] is False
    assert projection.execution(RUN).en_pause is False
    assert projection.execution(RUN).statut == EXECUTION_EN_COURS


# ------------------------------------------- ⑤ Les routes, et leurs refus


def _client(state: ControlTowerState | None = None, log: InMemoryEventLog | None = None):
    """L'app réelle sur bus mémoire — routes, projection et pompe de production."""
    return TestClient(
        create_app(
            bus=InMemoryEventBus(),
            state=state if state is not None else ControlTowerState(),
            event_log=log if log is not None else InMemoryEventLog(),
        )
    )


def _inscrit(state: ControlTowerState, statut: str = EXECUTION_EN_COURS) -> None:
    state.appliquer(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN,
            statut=statut,
            titre="Objectif",
            projet_id=PROJET,
        )
    )


def test_la_route_de_pause_rend_le_resume_passe_en_pause():
    state = ControlTowerState()
    _inscrit(state)

    with _client(state) as client:
        reponse = client.post(f"/api/executions/{RUN}/pause")

    assert reponse.status_code == 200
    resume = reponse.json()
    assert resume["run_id"] == RUN
    assert resume["en_pause"] is True
    # Le statut est intact : la pause se superpose, elle ne remplace pas.
    assert resume["statut"] == EXECUTION_EN_COURS


def test_la_route_de_reprise_rouvre_ce_que_la_pause_avait_fermé():
    state = ControlTowerState()
    _inscrit(state)

    with _client(state) as client:
        client.post(f"/api/executions/{RUN}/pause")
        reponse = client.post(f"/api/executions/{RUN}/reprendre")

    assert reponse.status_code == 200
    assert reponse.json()["en_pause"] is False


@pytest.mark.parametrize("verbe", ["pause", "reprendre"])
def test_un_run_inconnu_est_refuse_par_les_deux_verbes(verbe):
    with _client() as client:
        reponse = client.post(f"/api/executions/run-fantome/{verbe}")

    assert reponse.status_code == 404
    assert "run-fantome" in reponse.json()["detail"]


@pytest.mark.parametrize("statut", [EXECUTION_TERMINEE, EXECUTION_ANNULEE])
def test_un_run_solde_n_est_plus_suspendable(statut):
    """« Il n'y a rien à suspendre d'un run qui a rendu son issue. »"""
    state = ControlTowerState()
    _inscrit(state, statut)

    with _client(state) as client:
        reponse = client.post(f"/api/executions/{RUN}/pause")

    assert reponse.status_code == 409
    assert statut in reponse.json()["detail"]


def test_un_run_deja_suspendu_ne_se_suspend_pas_deux_fois():
    state = ControlTowerState()
    _inscrit(state)

    with _client(state) as client:
        client.post(f"/api/executions/{RUN}/pause")
        reponse = client.post(f"/api/executions/{RUN}/pause")

    assert reponse.status_code == 409
    assert "reprendre" in reponse.json()["detail"]


def test_un_run_qui_n_est_pas_suspendu_ne_se_reprend_pas():
    """Le pendant du refus ci-dessus, et il vaut aussi pour un run soldé.

    Un run soldé a `en_pause == False` (l'issue lève la pause) : il tombe donc
    dans ce refus-ci, et non dans celui du « déjà soldé ». C'est le même
    diagnostic — il n'y a rien à reprendre.
    """
    state = ControlTowerState()
    _inscrit(state)

    with _client(state) as client:
        reponse = client.post(f"/api/executions/{RUN}/reprendre")

    assert reponse.status_code == 409
    assert "pas été mis en pause" in reponse.json()["detail"]


def test_la_liste_des_runs_porte_le_drapeau_de_pause():
    state = ControlTowerState()
    _inscrit(state)

    with _client(state) as client:
        client.post(f"/api/executions/{RUN}/pause")
        (resume,) = client.get("/api/executions", params={"projet": TRANSVERSE}).json()

    assert resume["en_pause"] is True


# ------------------------- ⑥ Le troisième fait : l'état survit au redémarrage


def test_un_run_suspendu_le_reste_apres_un_redemarrage_de_l_api():
    """Le critère ③ du ticket, et la raison du choix de canal.

    Rien n'est conservé à part : c'est l'événement `execution.statut` du journal
    durable qui est rejoué au démarrage, et la projection en redéduit `en_pause`.
    """
    log = InMemoryEventLog()
    state = ControlTowerState()
    _inscrit(state)
    asyncio.run(
        log.consigner(
            Event(
                type=EVENEMENT_EXECUTION_STATUT,
                run_id=RUN,
                statut=EXECUTION_EN_COURS,
                titre="Objectif",
                projet_id=PROJET,
            )
        )
    )

    with _client(state, log) as client:
        client.post(f"/api/executions/{RUN}/pause")

    # L'API repart sur une projection **neuve**, du seul journal durable.
    with _client(ControlTowerState(), log) as client:
        resume = client.get(f"/api/executions/{RUN}").json()

    assert resume["en_pause"] is True
    # Et il se reprend depuis cette API-là, sans rien reconstruire.
    with _client(ControlTowerState(), log) as client:
        assert client.post(f"/api/executions/{RUN}/reprendre").status_code == 200


def test_une_reprise_consignee_survit_elle_aussi_au_rejeu():
    """Le dernier ordre l'emporte : rejouer l'histoire ne fige pas un run repris."""
    log = InMemoryEventLog()
    state = ControlTowerState()
    _inscrit(state)
    asyncio.run(
        log.consigner(
            Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=EXECUTION_EN_COURS)
        )
    )

    with _client(state, log) as client:
        client.post(f"/api/executions/{RUN}/pause")
        client.post(f"/api/executions/{RUN}/reprendre")

    with _client(ControlTowerState(), log) as client:
        assert client.get(f"/api/executions/{RUN}").json()["en_pause"] is False


# ----------------- ⑦ La frontière : l'ordre traverse jusqu'au process détaché


class BusScripte(InMemoryEventBus):
    """Un bus qui rend une suite d'événements connue d'avance, puis se tarit.

    Même double que `tests/test_hote_detache.py` : avec un vrai bus il faudrait
    publier *après* que l'abonnement soit effectif, c'est-à-dire trancher un
    ordonnancement au lieu de tester une lecture.
    """

    def __init__(self, evenements) -> None:
        super().__init__()
        self._scenario = tuple(evenements)

    async def subscribe(self):  # type: ignore[override]
        for evenement in self._scenario:
            yield evenement


def test_le_guet_du_process_detache_bascule_la_porte_sans_sortir_de_sa_boucle():
    """La différence de nature avec l'annulation : une pause se lève et se repose.

    Un guet qui sortirait au premier ordre ne verrait jamais le second — et le run
    resterait suspendu pour toujours, sans que rien puisse le rouvrir.
    """
    porte = PorteExecution()
    bus = BusScripte(
        [
            Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=ORDRE_PAUSE),
            Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=ORDRE_REPRISE),
            Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=ORDRE_PAUSE),
        ]
    )

    # Le flux se tarit sans annulation : le guet rend False, la porte a suivi les
    # trois ordres dans l'ordre.
    assert asyncio.run(hote_detache._observer_ordres(RUN, bus=bus, porte=porte)) is False
    assert porte.ouverte is False


def test_le_guet_ignore_les_ordres_de_pause_des_autres_runs():
    porte = PorteExecution()
    bus = BusScripte(
        [Event(type=EVENEMENT_EXECUTION_STATUT, run_id="un-autre", statut=ORDRE_PAUSE)]
    )

    asyncio.run(hote_detache._observer_ordres(RUN, bus=bus, porte=porte))

    assert porte.ouverte is True


def test_l_annulation_sort_toujours_du_guet_meme_apres_une_pause():
    """Les deux ordres cohabitent sur le canal, et seul l'un des deux est définitif."""
    porte = PorteExecution()
    bus = BusScripte(
        [
            Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=ORDRE_PAUSE),
            Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=EXECUTION_ANNULEE),
            Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=ORDRE_REPRISE),
        ]
    )

    assert asyncio.run(hote_detache._observer_ordres(RUN, bus=bus, porte=porte)) is True
    # Le troisième ordre n'a jamais été lu : le guet était sorti.
    assert porte.ouverte is False


def test_un_guet_sans_porte_ignore_les_ordres_de_pause_sans_lever():
    """`porte=None` est le cas d'un appelant qui ne suspend rien — pas une panne."""
    bus = BusScripte(
        [Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=ORDRE_PAUSE)]
    )

    assert asyncio.run(hote_detache._observer_ordres(RUN, bus=bus)) is False
