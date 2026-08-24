"""Tests du mode durable — workflows Temporal (ticket #93, lot final du parent #92).

Rejoue les critères des lots #94–#97 sur l'**environnement de test Temporal**
(`temporalio.testing.WorkflowEnvironment`, serveur de test *time-skipping*) et des
**fournisseurs factices** : aucun serveur Temporal réel, aucun conteneur, aucun
appel modèle. C'est ce qui rend testable ce que les lots amont avaient laissé de
côté (« exige un serveur Temporal vivant et un vrai fournisseur » — d'où l'exclusion
de couverture levée avec ce lot).

Couverture :

① **un run = un workflow, une tâche = une activité** (#95) — un run durable
   s'exécute via `MaestroRunWorkflow` + ses activités, `DurableEngine.run`
   reconstruit rapport et **grand livre** depuis l'agrégat sérialisé ;
② **blocage aval** (#43) — une dépendance en échec bloque la tâche aval, qui
   n'atteint jamais l'activité d'exécution (aucun appel modèle gaspillé) ;
③ **reprise sur panne** (#96) — l'état acquis (`resultats_acquis`) expose ce qui
   est déjà payé ; un process qui reprend en cours de route (`reprendre`) ne
   **repaie pas l'amont** (les tâches abouties ne sont pas ré-exécutées) ; un run
   déjà achevé se reprend en rendant simplement son rapport ; un run_id inconnu
   lève une erreur d'orchestration lisible ;
④ **planification invalide** — le workflow échoue et l'échec est retraduit en
   `OrchestratorError` (sans plan, rien à orchestrer).

La persistance de l'état Control Tower au redémarrage de l'API (#97) est couverte
côté projection dans `tests/test_controltower.py` (journal des événements rejoué).

**Boucle & environnement partagés.** Le dépôt n'utilise pas `pytest-asyncio` : chaque
scénario est une coroutine pilotée sur **une** boucle asyncio dédiée (le client
Temporal est lié à la boucle qui l'a créé). Le serveur de test — binaire téléchargé
une fois puis mis en cache — est démarré **une fois pour tout le module**. Sans accès
réseau pour ce téléchargement, le module est ignoré avec un message explicite ; la
CI, elle, y a accès (elle installe déjà ses dépendances depuis PyPI).

⚠ **Horloge verrouillée et requêtes (#457).** Le serveur *time-skipping* tient son
horloge **arrêtée** hors de `WorkflowHandle.result()` : aucun timer du serveur n'y
expire jamais. C'est sans effet tant qu'une requête (`query`) est servie par le
worker qui a exécuté le workflow — le cas de tous les scénarios sauf un. Mais une
**reprise** interroge un workflow dont le worker est mort : sa requête part alors
vers une file « sticky » que plus personne ne relève, et le repli qui devrait la
rattraper est justement l'un de ces timers. `_EnvTest.horloge_libre` (voir sa
docstring) rend l'horloge au seul appel concerné ; sans lui, le test était rouge par
intermittence, sur le délai de la requête et non sur ce qu'il vérifie.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from temporalio.testing import WorkflowEnvironment

from maestro.durable import (
    DurableEngine,
    MaestroRunWorkflow,
    construire_worker,
    identifiant_workflow,
)
from maestro.durable.activities import configurer_worker, reinitialiser_worker
from maestro.durable.engine import create_durable_engine
from maestro.orchestrator.errors import OrchestratorError
from maestro.orchestrator.prompt import ORCHESTRATOR_SYSTEM_PROMPT
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal
from maestro.telemetry.costs import ETAPE_REPRISE

#: Objectif de démonstration partagé par les scénarios (mots sensibles évités, #9).
_OBJECTIF = "Créer une API de gestion de tâches"


# --- Fournisseur factice : planifie puis exécute, sans jamais appeler de modèle -------


class FakeDurableProvider(ModelProvider):
    """Exécutant factice couvrant les **deux** rôles du worker durable.

    En mode durable, planification (activité `planifier`) et exécution (activité
    `executer_tache`) tournent sur le **même** fournisseur du worker (celui posé
    par `configurer_worker`). Ce double rôle est distingué de façon fiable par le
    `system_prompt` : l'orchestrateur (#3) appelle avec `ORCHESTRATOR_SYSTEM_PROMPT`
    et attend un plan JSON ; toute autre génération est une exécution de tâche.

    - `bloquer` : jeton qui, présent dans le prompt d'une tâche, la fait **attendre**
      `debloquer` (simule une tâche encore en vol au moment d'une reprise) ;
    - `echouer` : jeton qui fait **échouer** l'exécution (aléa fournisseur) — le
      `LocalExecutor` en fait un `TaskResult` au statut `echec`, sans lever.

    `executions` garde le prompt de chaque exécution **tentée**, dans l'ordre : la
    matière des assertions « telle tâche a (n'a pas) été exécutée / ré-exécutée ».
    """

    name = "fake-durable"

    def __init__(
        self, plan_json: str, *, bloquer: str | None = None, echouer: str | None = None
    ) -> None:
        self._plan = plan_json
        self._bloquer = bloquer
        self._echouer = echouer
        self.debloquer = asyncio.Event()
        self.executions: list[str] = []
        self.plans = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        if system_prompt == ORCHESTRATOR_SYSTEM_PROMPT:
            self.plans += 1
            return self._plan
        self.executions.append(prompt)
        if self._echouer and self._echouer in prompt:
            raise RuntimeError(f"panne fournisseur simulée ({self._echouer})")
        if self._bloquer and self._bloquer in prompt:
            await self.debloquer.wait()
        return f"LIVRABLE ({len(self.executions)})"

    def executions_de(self, jeton: str) -> list[str]:
        """Les prompts d'exécution d'une tâche identifiée par `jeton`.

        Le jeton est cherché sur la **première ligne** du prompt (« Tâche : <titre> »),
        l'identité de la tâche exécutée — pas ailleurs, où le titre/livrable d'une
        dépendance (tableau noir) le ferait ressurgir et gonflerait le comptage.
        """
        return [p for p in self.executions if jeton in p.splitlines()[0]]


# --- Plans factices (mots sensibles évités, #9) : jetons uniques par tâche -------------


def _tache(id, *, titre, description, competences, dependances=()):
    """Dict de tâche conforme au schéma partagé de l'orchestrateur."""
    return {
        "id": id,
        "titre": titre,
        "description": description,
        "competences_requises": list(competences),
        "format_sortie": "Texte",
        "dependances": list(dependances),
    }


def _plan(*taches) -> str:
    """Sérialise un plan (la sortie qu'un orchestrateur factice rendrait)."""
    return json.dumps(list(taches), ensure_ascii=False)


def _plan_chaine() -> str:
    """Plan en chaîne bdd → developpeur.

    Le jeton en majuscules (AMONT/AVAL) identifie chaque tâche dans son prompt
    d'exécution — le `LocalExecutor` y injecte titre et description : la sonde des
    assertions « telle tâche a (n'a pas) tourné ».
    """
    return _plan(
        _tache(
            "schema-bdd",
            titre="Schéma BDD [AMONT]",
            description="Définir le schéma des tâches (jeton AMONT).",
            competences=("sql", "schema"),
        ),
        _tache(
            "api-taches",
            titre="API des tâches [AVAL]",
            description="Exposer les tâches en lecture/écriture (jeton AVAL).",
            competences=("backend", "api"),
            dependances=("schema-bdd",),
        ),
    )


# --- Environnement de test partagé : une boucle, un serveur time-skipping --------------


class _EnvTest:
    """Boucle dédiée + `WorkflowEnvironment` : pilote un scénario async de bout en bout."""

    def __init__(self, loop: asyncio.AbstractEventLoop, environnement: WorkflowEnvironment) -> None:
        self.loop = loop
        self.environnement = environnement
        self.client = environnement.client

    def run(self, coro):
        """Déroule une coroutine sur la boucle du serveur de test (client lié à elle)."""
        return self.loop.run_until_complete(coro)

    def horloge_libre(self, coro):
        """`coro` déroulée pendant que l'horloge du serveur de test **avance** (#457).

        À réserver au code qui **interroge** (`query`) un workflow que le worker
        du process courant n'a pas exécuté lui-même — en pratique : une reprise
        (`DurableEngine.reprendre`), dont c'est tout le propos.

        Le serveur adresse une requête au worker qu'il croit détenir le workflow
        en cache (file « sticky »). Si ce worker n'est plus là, un
        **`sticky_queue_schedule_to_start_timeout`** (10 s par défaut, cf. SDK)
        la fait retomber sur la file normale, où n'importe quel worker la sert.
        Ce repli est un **timer du serveur**, donc porté par son horloge — or
        `WorkflowEnvironment.start_time_skipping()` la tient **verrouillée** hors
        de `WorkflowHandle.result()`. Une requête que le premier dispatch ne sert
        pas ne peut alors plus jamais l'être : le repli qui devrait la rattraper
        n'expire pas, et elle épuise le délai de sa requête RPC (30 s) — c'est le
        `RPCError: Query deadline of 29999 milliseconds exceeded` de #457, rouge
        une fois sur le pipeline puis vert au simple rejeu.

        Déverrouiller l'horloge le temps de l'appel rend au test le mécanisme qui
        existe en production : les 10 s du repli passent en temps **virtuel**
        (mesuré : requête servie en 0,02 s), donc le verdict cesse de dépendre de
        qui, du dispatch ou du démarrage des pollers, gagne la course. Ce n'est
        pas un plafond relevé : sans le repli, aucune attente ne suffirait.
        """

        async def _deroule():
            async with self.environnement.time_skipping_unlocked():
                return await coro

        return _deroule()


@pytest.fixture(scope="module")
def env():
    """Serveur de test Temporal (time-skipping) démarré une fois pour le module.

    Le binaire est téléchargé au premier usage puis mis en cache. En l'absence de
    réseau pour ce téléchargement, le module est ignoré plutôt que d'échouer (la CI,
    qui installe ses dépendances depuis PyPI, y a accès).
    """
    loop = asyncio.new_event_loop()
    try:
        environnement = loop.run_until_complete(WorkflowEnvironment.start_time_skipping())
    except Exception as exc:  # pragma: no cover - dépend de la disponibilité réseau/binaire
        loop.close()
        pytest.skip(f"environnement de test Temporal indisponible ({exc})")
    try:
        yield _EnvTest(loop, environnement)
    finally:
        loop.run_until_complete(environnement.shutdown())
        loop.close()


@pytest.fixture(autouse=True)
def _worker_reinitialise():
    """Rend au worker sa configuration par défaut après chaque test (fournisseur, relance)."""
    yield
    reinitialiser_worker()


@pytest.fixture(autouse=True)
def _depots_isoles(tmp_path, monkeypatch):
    """Isole l'exécuteur du worker des dépôts versionnés du dépôt (`core/…`).

    L'activité `executer_tache` passe par le **même** `LocalExecutor` que la boucle
    locale : il relit à chaud MCP, agents, playbooks, capacité, secrets et
    permissions. Sans isolation, une déclaration réelle (ex. `core/mcp/qa.json`, dont
    le token n'existe pas en test) ferait échouer les tâches — comme en CI. On pointe
    chaque dépôt vers un répertoire temporaire vide : l'exécution retombe sur les
    agents par défaut du code, sans dépendance externe.
    """
    for var in (
        "MAESTRO_MCP_DIR",
        "MAESTRO_AGENTS_DIR",
        "MAESTRO_PLAYBOOKS_DIR",
        "MAESTRO_CAPACITE_DIR",
        "MAESTRO_SECRETS_DIR",
        "MAESTRO_PERMISSIONS_DIR",
    ):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))


@pytest.fixture(autouse=True)
def _client_sur_env(env, monkeypatch):
    """Branche `DurableEngine` sur le client du serveur de test.

    L'engine se connecte par `Client.connect(adresse)` ; on le fait rendre le client
    *time-skipping* du serveur de test, pour que démarrage, requêtes et reprise
    tournent contre l'environnement de test (et non un vrai serveur).
    """

    async def _connect(*_args, **_kwargs):
        return env.client

    monkeypatch.setattr("maestro.durable.engine.Client.connect", _connect)


def _engine() -> DurableEngine:
    """Un moteur durable en **une seule tentative** (relance applicative neutralisée).

    Les tests comptent les exécutions et simulent des pannes : la relance masquerait
    l'un et rejouerait l'autre. La relance elle-même est couverte par `test_retry.py`.
    """
    return DurableEngine(relance=None)


async def _entree(objectif: str, run_id: str) -> dict:
    """L'entrée du workflow, telle que `DurableEngine.run` la compose (plafonds nuls)."""
    return {
        "objectif": objectif,
        "run_id": run_id,
        "plafond_cout_usd": None,
        "plafond_tokens": None,
    }


# --- ① Un run = un workflow, une tâche = une activité (#95) -----------------------------


def test_un_run_durable_execute_le_plan_via_workflow_et_activites(env):
    """Le run se déroule comme workflow + activités ; rapport et grand livre agrégés.

    Deux tâches en chaîne (bdd → developpeur) : chacune est routée et exécutée par
    une activité, le résultat porte l'empreinte du worker Temporal, et le rapport —
    reconstruit de l'agrégat sérialisé par le workflow — restitue l'ordre du plan et
    le grand livre (une ligne par tâche + planification).
    """
    provider = FakeDurableProvider(_plan_chaine())
    configurer_worker(provider_factory=lambda: provider)
    journal = RunJournal(run_id="run-durable-ok")

    report = env.run(_engine().run(_OBJECTIF, journal=journal))

    assert [r.agent for r in report.resultats] == ["bdd", "developpeur"]
    assert all(r.ok for r in report.resultats)
    assert all(r.worker.startswith("temporal/") for r in report.resultats)
    assert report.run_id == "run-durable-ok"

    # Grand livre reconstruit depuis l'agrégat : une entrée par tâche + planification.
    livre = report.grand_livre
    assert livre.run_id == "run-durable-ok"
    assert {t.tache_id for t in livre.taches} == {"schema-bdd", "api-taches"}
    assert provider.plans == 1  # planifié une fois, via l'activité `planifier`
    # Le tableau noir a voyagé : la 2ᵉ tâche a vu le livrable de la 1ʳᵉ dans son prompt.
    assert any("LIVRABLE (1)" in p for p in provider.executions_de("AVAL"))


def test_create_durable_engine_lit_l_adresse_de_la_config(env):
    """La fabrique `create_durable_engine` (pendant de `OrchestrationEngine.default`) tourne."""
    provider = FakeDurableProvider(_plan_chaine())
    configurer_worker(provider_factory=lambda: provider)
    engine = create_durable_engine(relance=None)

    report = env.run(engine.run(_OBJECTIF, journal=RunJournal(run_id="run-fab")))

    assert len(report.resultats) == 2 and all(r.ok for r in report.resultats)


# --- ② Blocage aval : une dépendance en échec n'exécute jamais l'aval (#43) -------------


def test_une_dependance_en_echec_bloque_l_aval_sans_l_executer(env):
    """La racine échoue : l'aval est bloqué (activité `consigner_blocage`), jamais exécuté."""
    provider = FakeDurableProvider(_plan_chaine(), echouer="AMONT")
    configurer_worker(provider_factory=lambda: provider)

    report = env.run(_engine().run(_OBJECTIF, journal=RunJournal(run_id="run-bloc")))

    amont, aval = report.resultats
    assert amont.statut == "echec"
    assert aval.statut == "bloquee"
    assert "schema-bdd" in (aval.erreur or "")  # le blocage cite la dépendance non satisfaite
    # L'amont a été tenté (puis a échoué) ; l'aval n'a jamais atteint le fournisseur.
    assert provider.executions_de("AMONT")  # tentée
    assert provider.executions_de("AVAL") == []  # jamais exécutée


# --- ③ Reprise sur panne : sans repayer l'amont (#96) ----------------------------------


def test_reprendre_un_run_acheve_rend_le_rapport_sans_rien_reexecuter(env):
    """Un run déjà achevé se reprend : rapport rendu, amont **non repayé**.

    Modélise le process tué après la dernière tâche, avant l'affichage : le workflow
    est `COMPLETED` côté Temporal. `reprendre` rattache un process neuf, consigne la
    reprise (raison « déjà achevé ») et rend le rapport — sans un seul appel modèle
    de plus (le compteur d'exécutions ne bouge pas).
    """
    provider = FakeDurableProvider(_plan_chaine())
    configurer_worker(provider_factory=lambda: provider)
    engine = _engine()

    report = env.run(engine.run(_OBJECTIF, journal=RunJournal(run_id="run-fini")))
    executions_avant = list(provider.executions)
    assert len(report.reussies) == 2

    journal_reprise = RunJournal(run_id="run-fini")
    # `horloge_libre` : le worker du run vient de s'éteindre, et la requête de la
    # reprise a besoin du repli « sticky → file normale » — un timer du serveur,
    # que l'horloge verrouillée du serveur de test fige (#457).
    repris = env.run(
        env.horloge_libre(engine.reprendre("run-fini", journal=journal_reprise))
    )

    # Aucune ré-exécution : l'amont vient de l'historique, pas d'un nouvel appel.
    assert provider.executions == executions_avant
    assert [r.task_id for r in repris.resultats] == [r.task_id for r in report.resultats]
    # La reprise est consignée (césure visible), et n'entre pas au grand livre (usage nul).
    reprises = [rec for rec in journal_reprise.records if rec.etape == ETAPE_REPRISE]
    assert len(reprises) == 1
    assert "achevé" in reprises[0].sortie or "achevé" in reprises[0].entree


def test_l_etat_acquis_expose_l_amont_pendant_que_l_aval_est_en_vol(env):
    """`resultats_acquis` (#96) : l'amont payé est visible dès qu'il aboutit, l'aval en vol non.

    C'est le socle de la reprise sans repayer l'amont — un process qui reprend lit cet
    état pour savoir ce qui est déjà acquis. L'aval (`AVAL`) est tenu en vol par le
    fournisseur : l'état acquis expose alors le plan et l'amont, **pas** l'aval (rien
    produit). Une fois l'aval débloqué, le run s'achève — et l'amont n'a jamais été
    ré-exécuté (un seul passage `AMONT`), sa valeur venant de l'historique du workflow.
    """
    provider = FakeDurableProvider(_plan_chaine(), bloquer="AVAL")
    configurer_worker(provider_factory=lambda: provider)

    async def scenario():
        async with construire_worker(env.client):
            handle = await env.client.start_workflow(
                MaestroRunWorkflow.run,
                await _entree(_OBJECTIF, "run-vol"),
                id=identifiant_workflow("run-vol"),
                task_queue="maestro-durable",
            )
            # Attendre que l'amont soit acquis (l'aval, bloqué, ne l'est jamais avant déblocage).
            for _ in range(4000):
                etat = await handle.query(MaestroRunWorkflow.resultats_acquis)
                if any(r["task_id"] == "schema-bdd" for r in etat["resultats"]):
                    break
                await asyncio.sleep(0.005)
            etat = await handle.query(MaestroRunWorkflow.resultats_acquis)
            assert {r["task_id"] for r in etat["resultats"]} == {"schema-bdd"}  # amont acquis…
            assert etat["planification"] is not None  # …plan acquis, aval encore en vol
            provider.debloquer.set()  # l'aval peut enfin aboutir
            return await handle.result()

    agregat = env.run(scenario())

    resultats = agregat["resultats"]
    assert {r["task_id"] for r in resultats} == {"schema-bdd", "api-taches"}
    assert all(r["statut"] == "terminee" for r in resultats)
    assert len(provider.executions_de("AMONT")) == 1  # amont jamais repayé


def test_reprendre_un_run_inconnu_leve_une_erreur_lisible(env):
    """Reprendre un run_id qu'aucun workflow ne porte : `OrchestratorError` explicite."""
    configurer_worker(provider_factory=lambda: FakeDurableProvider(_plan_chaine()))

    with pytest.raises(OrchestratorError, match="aucun run durable"):
        env.run(_engine().reprendre("run-fantome"))


# --- ④ Planification invalide : le workflow échoue, retraduit en OrchestratorError ------


def test_une_planification_invalide_fait_echouer_le_run(env):
    """Un plan indécodable fait échouer le workflow ; l'échec devient `OrchestratorError`."""
    provider = FakeDurableProvider("ceci n'est pas un plan JSON")
    configurer_worker(provider_factory=lambda: provider)

    with pytest.raises(OrchestratorError):
        env.run(_engine().run("Objectif hors sujet", journal=RunJournal(run_id="run-ko")))
    assert provider.executions == []  # aucune tâche exécutée : il n'y a jamais eu de plan
