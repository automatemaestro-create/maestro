"""Tests de la messagerie inter-agents — boîtes aux lettres et handoff (ticket #44).

Aucun réseau : les boîtes aux lettres tournent en mémoire (`InMemoryMailbox`,
même contrat que `RedisMailbox`) et la boucle d'orchestration sur des
fournisseurs factices. Couvre les critères d'acceptation du ticket #44 :

① boîte aux lettres par agent : publication et consommation de messages —
   message direct reçu par le seul destinataire, diffusion reçue par tous,
   abonnement posé dès le retour de `subscribe` (pas de message manqué) ;
② handoff démontré (critère MVP n°7) : dans la boucle d'orchestration, l'agent
   qui termine une tâche annonce l'issue par message et la tâche aval n'est
   exécutée qu'après réception — y compris la notification d'un échec, qui
   prévient l'aval avant son blocage (#43) ;
③ l'échange est journalisé dans la télémétrie (#8, étape `<tache>:message`) et
   visible dans le flux d'événements de la Control Tower (#46, événement
   `message.inter_agents` via le pont télémétrie).

Plus la résilience du relais : attente bornée sans message, publication en
échec abandonnée sans faire échouer la tâche.
"""

import asyncio
import json

from maestro.controltower import EVENEMENT_MESSAGE_INTER_AGENTS, activer_publication
from maestro.engine import STATUT_BLOQUEE, STATUT_ECHEC, STATUT_TERMINEE, OrchestrationEngine
from maestro.engine.executor import TaskResult
from maestro.messaging import (
    DIFFUSION,
    MESSAGE_HANDOFF,
    MESSAGE_NOTIFICATION,
    MESSAGE_REQUETE,
    STATUT_ENVOYE,
    AgentMessage,
    HandoffRelais,
    InMemoryMailbox,
    canal_boite,
    consigne_message,
)
from maestro.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task
from maestro.providers.base import ModelProvider
from maestro.telemetry import LOGGER_NAME, RunJournal


class ConstantProvider(ModelProvider):
    """Renvoie toujours la même réponse (planificateur, ou exécutant en échec)."""

    name = "constant"

    def __init__(self, response):
        self._response = response

    def supports(self, model):
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._response


class RecordingProvider(ModelProvider):
    """Exécutant factice : enregistre chaque appel et renvoie un livrable unique."""

    name = "recording"

    def __init__(self):
        self.calls = []

    def supports(self, model):
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.calls.append({"prompt": prompt, "model": model})
        return f"LIVRABLE pour l'appel #{len(self.calls)}"


def _plan_json():
    # 3 tâches en chaîne : bdd -> developpeur -> qa (via les compétences requises).
    return json.dumps(
        [
            {
                "id": "schema-bdd",
                "titre": "Schéma BDD",
                "description": "Définir le schéma des tâches.",
                "competences_requises": ["sql", "schema"],
                "format_sortie": "Fichier SQL",
                "dependances": [],
            },
            {
                "id": "api-taches",
                "titre": "API des tâches",
                "description": "Endpoints CRUD.",
                "competences_requises": ["backend", "api"],
                "format_sortie": "Module d'API",
                "dependances": ["schema-bdd"],
            },
            {
                "id": "tests-api",
                "titre": "Tests de l'API",
                "description": "Tests d'intégration.",
                "competences_requises": ["tests", "e2e"],
                "format_sortie": "Suite de tests",
                "dependances": ["api-taches"],
            },
        ],
        ensure_ascii=False,
    )


def _engine(mailbox, *, exec_provider=None):
    planner = ConstantProvider(_plan_json())
    orchestrator = Orchestrator(planner, model="claude-opus-4-8")
    execu = exec_provider if exec_provider is not None else RecordingProvider()
    return OrchestrationEngine(execu, orchestrator, mailbox=mailbox)


def _message(type_=MESSAGE_HANDOFF, *, de="bdd", a=DIFFUSION, tache_id="t1"):
    return AgentMessage(
        type=type_,
        de_agent=de,
        a_agent=a,
        tache_id=tache_id,
        run_id="run-44",
        objet="Tâche « Schéma » terminée — la main passe à l'aval (t2).",
        payload={"statut": STATUT_TERMINEE, "debloque": ["t2"]},
    )


def _tache(tache_id="t1", titre="Schéma", dependances=()):
    return Task(
        id=tache_id,
        titre=titre,
        description="…",
        competences_requises=("sql",),
        format_sortie="SQL",
        dependances=tuple(dependances),
    )


def _resultat(statut=STATUT_TERMINEE, *, agent="bdd", role="Base de données"):
    return TaskResult(
        task_id="t1",
        titre="Schéma",
        agent=agent,
        role=role,
        competences_requises=("sql",),
        score=2,
        statut=statut,
        sortie="livrable" if statut == STATUT_TERMINEE else "",
        erreur=None if statut == STATUT_TERMINEE else "cause",
    )


# ----------------------------------------------------- ① Boîtes aux lettres


def test_message_aller_retour_json():
    message = _message()
    assert AgentMessage.from_json(message.to_json()) == message
    assert AgentMessage.from_json(message.to_json().encode("utf-8")) == message


def test_message_direct_recu_par_le_seul_destinataire_diffusion_par_tous():
    """La boîte de chaque agent reçoit ses messages directs et les diffusions."""

    async def scenario():
        mailbox = InMemoryMailbox()
        boite_qa = await mailbox.subscribe("qa")
        boite_dev = await mailbox.subscribe("developpeur")

        # Abonnements posés dès le retour de `subscribe` : publier tout de suite
        # ne perd rien (contrat au cœur du handoff, pub/sub sans rejeu).
        direct = _message(MESSAGE_REQUETE, a="qa")
        diffusion = _message(MESSAGE_NOTIFICATION, a=DIFFUSION)
        await mailbox.publish(direct)
        await mailbox.publish(diffusion)

        assert await anext(boite_qa) == direct
        assert await anext(boite_qa) == diffusion
        # Le premier message de la boîte du développeur est la diffusion : le
        # message direct adressé à qa ne l'a jamais atteinte.
        assert await anext(boite_dev) == diffusion

        await boite_qa.close()
        await boite_dev.close()

    asyncio.run(scenario())


def test_canal_redis_de_la_boite_d_un_agent():
    """Le transport Redis nomme un canal par boîte (préfixe dédié, docs/03 §4)."""
    assert canal_boite("qa") == "maestro.boite.qa"


# ------------------------------------------- ③ Journal (#8) et Control Tower (#46)


def test_consigne_message_journalise_l_echange():
    journal = RunJournal(run_id="run-44")
    consigne_message(journal, _message(), role="Base de données")

    (record,) = journal.records
    assert record.etape == "t1:message"
    assert record.agent == "bdd" and record.role == "Base de données"
    assert record.statut == STATUT_ENVOYE
    assert record.nom.startswith("Tâche « Schéma » terminée")
    assert "handoff de bdd à diffusion" in record.sortie


def test_ligne_de_message_devient_evenement_inter_agents():
    from maestro.controltower import evenements_depuis_step

    journal = RunJournal(run_id="run-44")
    consigne_message(journal, _message(), role="Base de données")

    (event,) = evenements_depuis_step(journal.records[0].to_dict())

    assert event.type == EVENEMENT_MESSAGE_INTER_AGENTS
    assert event.run_id == "run-44" and event.tache_id == "t1"
    assert event.agent == "bdd" and event.statut == STATUT_ENVOYE
    assert "handoff de bdd à diffusion" in event.detail


# --------------------------------------------- ② Handoff dans la boucle (MVP n°7)


def test_handoff_annonce_puis_debloque_la_tache_aval():
    """Chaîne bdd → developpeur → qa : chaque fin de tâche annonce, l'aval suit."""
    journal = RunJournal(run_id="run-44")
    report = asyncio.run(_engine(InMemoryMailbox()).run("Objectif", journal=journal))

    assert [r.statut for r in report.resultats] == [STATUT_TERMINEE] * 3
    etapes = [r.etape for r in journal.records]
    # L'annonce du handoff est consignée entre la fin de la tâche amont et le
    # démarrage (#98) de la tâche aval : l'échange précède (et débloque) l'exécution.
    assert etapes == [
        "planification",
        "schema-bdd:debut", "schema-bdd", "schema-bdd:message",
        "api-taches:debut", "api-taches", "api-taches:message",
        "tests-api:debut", "tests-api",  # dernière tâche du plan : pas d'annonce
    ]

    annonce = journal.records[3]
    assert annonce.agent == "bdd" and annonce.statut == STATUT_ENVOYE
    assert "la main passe" in annonce.nom
    assert "handoff" in annonce.sortie


def test_echec_amont_notifie_l_aval_avant_son_blocage():
    """Un échec voyage aussi par message : l'aval est prévenu, puis se bloque (#43)."""
    journal = RunJournal(run_id="run-44")
    report = asyncio.run(
        _engine(InMemoryMailbox(), exec_provider=ConstantProvider("   ")).run(
            "Objectif", journal=journal
        )
    )

    racine, *aval = report.resultats
    assert racine.statut == STATUT_ECHEC
    assert all(r.statut == STATUT_BLOQUEE for r in aval)

    par_etape = {r.etape: r for r in journal.records}
    notification = par_etape["schema-bdd:message"]
    assert "notification" in notification.sortie and "echec" in notification.nom
    # La tâche bloquée a elle aussi un aval : elle le prévient à son tour.
    assert "bloquee" in par_etape["api-taches:message"].nom


def test_le_handoff_est_visible_dans_le_flux_control_tower():
    """Bout en bout du critère ③ : boucle + messagerie ⇒ événement `message.inter_agents`."""
    import logging

    captes = []
    handler = activer_publication(captes.append)
    try:
        asyncio.run(_engine(InMemoryMailbox()).run("Objectif"))
    finally:
        logging.getLogger(LOGGER_NAME).removeHandler(handler)

    messages = [e for e in captes if e.type == EVENEMENT_MESSAGE_INTER_AGENTS]
    assert [m.tache_id for m in messages] == ["schema-bdd", "api-taches"]
    assert messages[0].agent == "bdd"
    assert "handoff" in messages[0].detail


# ------------------------------------------------------- Résilience du relais


def test_attente_bornee_quand_aucune_annonce_n_arrive():
    """Un message perdu ne suspend pas la boucle : l'attente rend None après le délai."""

    async def scenario():
        mailbox = InMemoryMailbox()
        relais = await HandoffRelais.ouvrir(mailbox, RunJournal(), timeout_s=0.05)
        try:
            assert await relais.attend("fantome") is None
        finally:
            await relais.fermer()

    asyncio.run(scenario())


def test_annonce_abandonnee_si_la_publication_echoue():
    """La messagerie ne fait jamais échouer la tâche qu'elle annonce (résilience)."""

    class MailboxEnPanne(InMemoryMailbox):
        async def publish(self, message):
            raise ConnectionError("Redis injoignable")

    async def scenario():
        journal = RunJournal()
        relais = await HandoffRelais.ouvrir(MailboxEnPanne(), journal)
        try:
            annonce = await relais.annonce(_tache(), _resultat(), ["t2"])
        finally:
            await relais.fermer()
        assert annonce is None
        assert journal.records == ()  # rien d'envoyé ⇒ rien de journalisé

    asyncio.run(scenario())
