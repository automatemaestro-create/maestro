"""Un refus dit pourquoi et l'agent repart de là ; une approbation peut valoir pour la suite (#1185).

Deux critères, et chacun se prouve sur la chaîne qui le porte plutôt que sur un
maillon :

① **Un refus motivé revient à l'agent comme consigne** — la tâche continue avec une
   autre action, soumise à son tour, au lieu d'être annulée. Joué par le **vrai**
   hook `PreToolUse` du fournisseur, le vrai arbitre du moteur et le vrai garde-fou :
   ce que l'agent lit est le `permissionDecisionReason` que le hook rend, et la
   seconde action repasse par le même canal. Même chose pour la validation d'une
   tâche sensible (`_valide_si_sensible`), qui repart de la consigne au lieu de
   s'arrêter, et pour l'agent qui lève la main (`reponse`).

② **Une approbation peut s'étendre à l'outil, pour le run ou pour le projet** —
   elle s'écrit dans les permissions de l'agent, s'y voit (fiche agent), s'y retire
   (`DELETE /api/permissions/{agent}/accords/{id}`), et les appels suivants ne
   redemandent plus. Le dernier test joue l'API et le moteur ensemble : un seul
   « oui, pour ce run » depuis la route, et les commandes suivantes passent sans
   qu'aucune demande ne naisse.

Ni réseau, ni Redis, ni modèle : plans constants, exécutants factices qui jouent des
commandes à travers le vrai hook, dépôts sur répertoire temporaire. Le seul absent
est le modèle qui *choisit* la seconde action — le double la joue, et c'est dit.
"""

from __future__ import annotations

import asyncio
import functools
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.agents.accords import Accord, AccordStore
from maestro.agents.mcp import McpStore
from maestro.agents.permissions import PermissionStore
from maestro.agents.store import AgentStore, SurchargeStore
from maestro.controltower import InMemoryEventBus, create_app
from maestro.controltower.attentes import ServiceAttentes, suite_de_la_decision
from maestro.controltower.events import (
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_VALIDATION_DECISION,
    EVENEMENT_VALIDATION_DEMANDE,
    Event,
)
from maestro.controltower.reglements import MOTIF_ETENDUE_REFUSEE, ReglementRefuse
from maestro.controltower.state import (
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    VALIDATION_APPROUVEE,
    VALIDATION_EN_ATTENTE,
    VALIDATION_REFUSEE,
    ControlTowerState,
)
from maestro.controltower.validation import ValidateurControlTower
from maestro.decision_humaine import (
    EN_TETE_CONSIGNES,
    ETENDUE_PROJET,
    ETENDUE_RUN,
    DecisionHumaine,
    DetailDecision,
    avec_consignes,
    consigne_de,
    etendue_de,
)
from maestro.engine import OrchestrationEngine
from maestro.engine.executor import SUFFIXE_ETAPE_REFUS
from maestro.engine.guardrails import MOTS_SENSIBLES, DemandeValidation, Guardrails
from maestro.orchestrator import Orchestrator
from maestro.providers import claude as claude_mod
from maestro.providers.arbitrage import reponse
from maestro.providers.base import ModelProvider
from maestro.sandbox.en_place import portee_de
from maestro.telemetry import RunJournal

#: L'agent du code que le plan route (« backend » → développeur).
AGENT = "developpeur"

#: La consigne de la personne qui refuse — l'exemple du ticket.
CONSIGNE = "archive au lieu de supprimer"

#: La politique d'une équipe qui fait trancher ses commandes par une personne.
POLITIQUE_HUMAINE = {"ask": {"Bash": "humain"}}


# --- Harnais ----------------------------------------------------------------------------


class ConstantProvider(ModelProvider):
    """Planificateur factice : rend toujours le même plan."""

    name = "constant"

    def __init__(self, response: str) -> None:
        self._response = response

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._response


class _Executant(ModelProvider):
    """Exécutant outillé factice : garde ce qu'on lui a fait lire, rend son livrable."""

    name = "executant"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        return "TEXTE"

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None, on_arbitrage=None, on_blocage=None, on_decision=None,
        credit_arbitrage=None,
        on_courrier=None, on_question=None,
        on_processus=None,
        plafond_tours=None, projet=None,
    ):
        self.prompts.append(prompt)
        (Path(workspace) / "livrable.txt").write_text("contenu", encoding="utf-8")
        return "LIVRÉ"


class PasseParLeVraiHook(_Executant):
    """Exécutant qui soumet ses commandes au **vrai** hook du fournisseur Claude.

    Armé comme `run_agent` l'arme (`_hook_permissions`, les canaux du moteur, la
    portée de l'espace de travail). Il joue ses commandes dans l'ordre et garde ce
    que le hook a rendu pour chacune : `{}` laisse passer, un `deny` porte le motif
    que l'agent lit. `entre_deux` est appelé après chaque commande — c'est la place
    d'un geste de la personne pendant que la tâche tourne.
    """

    name = "passe-par-le-vrai-hook"

    def __init__(self, commandes: tuple[str, ...], entre_deux=None) -> None:
        super().__init__()
        self.commandes = commandes
        self.entre_deux = entre_deux
        self.sorties: list[tuple[str, dict]] = []

    async def run_agent(self, prompt, *, workspace, politique=None, on_refus=None,
                        on_arbitrage_acte=None, projet=None, **reste):
        hook = claude_mod._hook_permissions(
            politique, on_refus, on_arbitrage_acte, portee=portee_de(workspace, projet)
        )
        for rang, commande in enumerate(self.commandes):
            sortie = await hook(
                {"tool_name": "Bash", "tool_input": {"command": commande}}, f"tu-{rang}", None
            )
            self.sorties.append((commande, sortie))
            if self.entre_deux is not None:
                self.entre_deux(rang)
        return await super().run_agent(
            prompt, workspace=workspace, politique=politique, on_refus=on_refus,
            on_arbitrage_acte=on_arbitrage_acte, projet=projet, **reste,
        )


class ValidateurSequence:
    """Le canal humain : rend ses décisions dans l'ordre, et garde ce qu'on lui a soumis."""

    def __init__(self, *decisions) -> None:
        self.decisions = list(decisions)
        self.demandes: list[DemandeValidation] = []

    def __call__(self, demande: DemandeValidation):
        self.demandes.append(demande)
        return self.decisions.pop(0)


def _tache(id_: str, titre: str, description: str) -> dict:
    return {
        "id": id_,
        "titre": titre,
        "description": description,
        "competences_requises": ["backend"],
        "format_sortie": "Texte",
        "dependances": [],
    }


PLAN_BUILD = json.dumps(
    [_tache("nettoyer", "Nettoyer la sortie de build", "Repartir d'un dossier dist propre.")],
    ensure_ascii=False,
)

#: Une tâche que le régime par mots-clés classe sensible (« supprim ») — le chemin de
#: `_valide_si_sensible`, qui annulait la tâche au premier refus.
PLAN_SENSIBLE = json.dumps(
    [
        _tache(
            "journaux",
            "Supprimer les anciens journaux",
            "Supprimer les journaux de plus de trente jours dans logs/.",
        )
    ],
    ensure_ascii=False,
)


@pytest.fixture()
def store(tmp_path):
    """Dépôt de permissions vierge, sur répertoire temporaire."""
    return PermissionStore(tmp_path / "permissions")


def _ecrire_politique(store: PermissionStore, politique: dict) -> None:
    store.racine.mkdir(parents=True, exist_ok=True)
    (store.racine / f"{AGENT}.json").write_text(
        json.dumps(politique, ensure_ascii=False), encoding="utf-8"
    )


def _moteur(provider, store, guardrails, plan) -> OrchestrationEngine:
    orchestrator = Orchestrator(ConstantProvider(plan), model="claude-opus-4-8")
    return OrchestrationEngine(provider, orchestrator, permissions=store, guardrails=guardrails)


def _motif_servi(sortie: dict) -> str:
    """Ce que l'agent lit d'un `deny` du hook — `""` si l'appel est passé."""
    return str(sortie.get("hookSpecificOutput", {}).get("permissionDecisionReason", ""))


def _demande_acte(**surcharge) -> DemandeValidation:
    champs = dict(
        task_id="t-1185", titre="Nettoyer", description="…", agent=AGENT,
        role="Développeur", raison="outil 'Bash' soumis à arbitrage",
        run_id="run-1185", outil="Bash", arguments={"command": "rm -rf dist"},
    )
    champs.update(surcharge)
    return DemandeValidation(**champs)


# --- ① Un refus motivé revient à l'agent comme consigne ---------------------------------


def test_un_refus_motive_revient_a_l_agent_et_sa_nouvelle_action_est_soumise(store):
    """C1, sur toute la chaîne : la consigne atteint l'agent, la tâche continue.

    La personne refuse `rm -rf dist` en disant d'archiver. Le hook rend un `deny`
    dont le motif porte la consigne **et** l'ordre de replanifier — plus « poursuis
    sans cet outil », qui aurait été faux : archiver se fait par le même `Bash`. Le
    double joue alors l'action qu'un agent en tirerait ; elle repasse par
    l'arbitrage (seconde demande), est approuvée, et la tâche se solde réussie.
    """
    _ecrire_politique(store, POLITIQUE_HUMAINE)
    validateur = ValidateurSequence(DecisionHumaine(approuve=False, motif=CONSIGNE), True)
    provider = PasseParLeVraiHook(("rm -rf dist", "tar czf dist.tgz dist"))

    report = asyncio.run(
        _moteur(provider, store, Guardrails(validateur=validateur), PLAN_BUILD).run(
            "Nettoyer la sortie de build", journal=RunJournal(run_id="run-1185-c1")
        )
    )

    (premier, refus), (second, passe) = provider.sorties
    assert premier == "rm -rf dist" and second == "tar czf dist.tgz dist"
    motif = _motif_servi(refus)
    assert CONSIGNE in motif
    assert "replanifie ton geste" in motif and "soumise à son tour" in motif
    assert "sans cet outil" not in motif
    # La nouvelle action a été soumise à son tour — et elle est passée.
    assert passe == {}
    assert [d.arguments for d in validateur.demandes] == [
        {"command": "rm -rf dist"},
        {"command": "tar czf dist.tgz dist"},
    ]
    # La tâche n'est pas annulée : elle se solde, réussie.
    assert all(r.ok for r in report.resultats)


def test_un_refus_sec_garde_la_consigne_d_avant(store):
    """Sans consigne, rien ne change : l'agent poursuit sans l'outil, au texte près."""
    _ecrire_politique(store, POLITIQUE_HUMAINE)
    provider = PasseParLeVraiHook(("rm -rf dist",))

    asyncio.run(
        _moteur(provider, store, Guardrails(validateur=ValidateurSequence(False)), PLAN_BUILD).run(
            "Nettoyer la sortie de build", journal=RunJournal(run_id="run-1185-sec")
        )
    )

    ((_, refus),) = provider.sorties
    motif = _motif_servi(refus)
    assert motif.endswith("Poursuis la tâche sans cet outil.")
    assert "consigne" not in motif


def test_le_meme_appel_rejoue_retrouve_la_meme_consigne_sans_redemander(store):
    """Rejouer l'acte refusé ne rouvre pas de demande : la mémoire le rend, consigne comprise."""
    _ecrire_politique(store, POLITIQUE_HUMAINE)
    validateur = ValidateurSequence(DecisionHumaine(approuve=False, motif=CONSIGNE))
    provider = PasseParLeVraiHook(("rm -rf dist", "rm -rf dist"))

    asyncio.run(
        _moteur(provider, store, Guardrails(validateur=validateur), PLAN_BUILD).run(
            "Nettoyer la sortie de build", journal=RunJournal(run_id="run-1185-rejeu")
        )
    )

    assert len(validateur.demandes) == 1
    assert all(CONSIGNE in _motif_servi(sortie) for _, sortie in provider.sorties)


def test_la_consigne_est_consignee_au_journal_du_run(store):
    """La trace de l'appel refusé garde ce que la personne a demandé à la place."""
    _ecrire_politique(store, POLITIQUE_HUMAINE)
    journal = RunJournal(run_id="run-1185-trace")
    provider = PasseParLeVraiHook(("rm -rf dist",))

    asyncio.run(
        _moteur(
            provider,
            store,
            Guardrails(validateur=ValidateurSequence(DecisionHumaine(False, CONSIGNE))),
            PLAN_BUILD,
        ).run("Nettoyer la sortie de build", journal=journal)
    )

    (trace,) = [r for r in journal.records if r.etape == f"nettoyer{SUFFIXE_ETAPE_REFUS}"]
    assert CONSIGNE in trace.sortie


def test_une_tache_sensible_refusee_avec_consigne_repart_et_se_resoumet(store):
    """C1 sur la validation d'une tâche : la consigne réoriente au lieu d'annuler.

    La tâche réorientée — sa description, puis la consigne — est **soumise à son
    tour** ; approuvée, elle part, et l'agent lit la consigne dans ce qu'on lui
    confie.
    """
    validateur = ValidateurSequence(DecisionHumaine(approuve=False, motif=CONSIGNE), True)
    provider = _Executant()

    report = asyncio.run(
        _moteur(
            provider,
            store,
            Guardrails(validateur=validateur, mots_sensibles=MOTS_SENSIBLES),
            PLAN_SENSIBLE,
        ).run("Faire le ménage dans les journaux", journal=RunJournal(run_id="run-1185-tache"))
    )

    assert len(validateur.demandes) == 2
    premiere, seconde = validateur.demandes
    assert CONSIGNE not in premiere.description
    assert EN_TETE_CONSIGNES in seconde.description and CONSIGNE in seconde.description
    (lu,) = provider.prompts
    assert CONSIGNE in lu
    assert all(r.ok for r in report.resultats)


def test_une_tache_sensible_refusee_sans_consigne_reste_stoppee(store):
    """Le témoin : un non sec arrête la tâche avant toute exécution, comme avant."""
    provider = _Executant()

    report = asyncio.run(
        _moteur(
            provider,
            store,
            Guardrails(validateur=ValidateurSequence(False), mots_sensibles=MOTS_SENSIBLES),
            PLAN_SENSIBLE,
        ).run("Faire le ménage dans les journaux", journal=RunJournal(run_id="run-1185-stop"))
    )

    assert provider.prompts == []
    (resultat,) = report.resultats
    assert not resultat.ok


def test_la_main_levee_refusee_avec_consigne_dit_de_replanifier():
    """L'autre canal (#582) : l'agent qui a demandé lit la consigne et l'ordre d'en repartir."""
    detail = DetailDecision(
        f"refusée par le validateur humain — consigne : « {CONSIGNE} »", consigne=CONSIGNE
    )

    lu = reponse(False, detail)

    assert CONSIGNE in lu and "replanifie" in lu
    assert "sans elle" not in lu
    # Sans consigne, la réponse d'avant.
    assert "Poursuis ta tâche sans elle" in reponse(False, "refusée par le validateur humain")


# --- Le détail d'une décision : consigne et étendue, jamais retrouvées dans la phrase ----


def test_le_garde_fou_porte_la_consigne_dans_le_detail():
    approuve, detail = asyncio.run(
        Guardrails(validateur=lambda d: DecisionHumaine(False, f"  {CONSIGNE}  ")).demande_validation(
            _demande_acte()
        )
    )

    assert approuve is False
    assert consigne_de(detail) == CONSIGNE
    assert f"consigne : « {CONSIGNE} »" in detail


def test_le_garde_fou_dit_une_approbation_etendue_et_seulement_pour_un_acte():
    etendue = DecisionHumaine(True, etendue=ETENDUE_RUN)

    approuve, detail = asyncio.run(
        Guardrails(validateur=lambda d: etendue).demande_validation(_demande_acte())
    )
    assert approuve is True
    assert etendue_de(detail) == ETENDUE_RUN
    assert "tout appel de 'Bash' sur ce run" in detail

    # Une demande sans acte (une tâche) n'a pas d'outil à ne plus redemander.
    _, sans_acte = asyncio.run(
        Guardrails(validateur=lambda d: etendue).demande_validation(_demande_acte(outil=""))
    )
    assert sans_acte == "approuvée par le validateur humain"


def test_un_booleen_reste_une_reponse_entiere():
    """Un validateur d'avant — `True`/`False` — rend exactement les textes d'avant."""
    assert asyncio.run(
        Guardrails(validateur=lambda d: False).demande_validation(_demande_acte())
    ) == (False, "refusée par le validateur humain")
    assert consigne_de("refusée par le validateur humain") == ""


def test_avec_consignes_les_ajoute_dans_l_ordre_et_rien_sans_elles():
    assert avec_consignes("Tâche.", []) == "Tâche."
    reorientee = avec_consignes("Tâche.", ["première", "  ", "seconde"])
    assert reorientee.startswith("Tâche.\n\n" + EN_TETE_CONSIGNES)
    assert reorientee.endswith("- première\n- seconde")


# --- Le transport : la consigne et l'étendue voyagent en champs ---------------------------


@pytest.mark.parametrize(
    ("champs", "attendu"),
    [
        ({"statut": VALIDATION_REFUSEE}, False),
        ({"statut": VALIDATION_APPROUVEE}, True),
        (
            {"statut": VALIDATION_REFUSEE, "motif": CONSIGNE},
            DecisionHumaine(approuve=False, motif=CONSIGNE),
        ),
        (
            {"statut": VALIDATION_APPROUVEE, "etendue": ETENDUE_PROJET},
            DecisionHumaine(approuve=True, etendue=ETENDUE_PROJET),
        ),
    ],
)
def test_le_validateur_rend_la_consigne_et_l_etendue_lues_sur_l_evenement(champs, attendu):
    async def scenario():
        bus = InMemoryEventBus()
        validateur = ValidateurControlTower(bus)

        async def personne():
            async for event in bus.subscribe():
                if event.type == EVENEMENT_VALIDATION_DEMANDE:
                    await bus.publish(
                        Event(type=EVENEMENT_VALIDATION_DECISION, tache_id="t-1185", **champs)
                    )
                    return

        async with asyncio.TaskGroup() as tg:
            tg.create_task(personne())
            await asyncio.sleep(0)
            return await validateur(_demande_acte())

    assert asyncio.run(scenario()) == attendu


def test_l_evenement_de_decision_fait_l_aller_retour_avec_ses_deux_champs():
    event = Event(
        type=EVENEMENT_VALIDATION_DECISION, tache_id="t", motif=CONSIGNE, etendue=ETENDUE_RUN
    )
    relu = Event.from_json(event.to_json())
    assert (relu.motif, relu.etendue) == (CONSIGNE, ETENDUE_RUN)
    # Une décision d'avant ce lot se relit sans rien de plus.
    assert Event.from_dict({"type": EVENEMENT_VALIDATION_DECISION}).motif == ""


# --- ② L'API : l'étendue s'écrit, se voit, se retire -------------------------------------


@pytest.fixture()
def bus():
    return InMemoryEventBus()


@pytest.fixture()
def state():
    return ControlTowerState()


@pytest.fixture()
def client(tmp_path, store, bus, state):
    """App montée sur des dépôts temporaires — jamais sur les `core/` du dépôt."""
    app = create_app(
        bus=bus,
        state=state,
        agents_store=AgentStore(tmp_path / "agents"),
        surcharges=SurchargeStore(tmp_path / "surcharges"),
        permissions=store,
        mcp=McpStore(tmp_path / "mcp"),
    )
    with TestClient(app) as client:
        yield client


def _demande(tache_id: str = "t-acte", **surcharge) -> Event:
    champs = dict(
        type=EVENEMENT_VALIDATION_DEMANDE,
        tache_id=tache_id,
        run_id="run-a",
        titre="Nettoyer la sortie de build",
        agent=AGENT,
        role="Développeur",
        statut=VALIDATION_EN_ATTENTE,
        detail="outil 'Bash' soumis à arbitrage",
        outil="Bash",
        arguments={"command": "rm -rf dist"},
        decideur="humain",
    )
    champs.update(surcharge)
    return Event(**champs)


def test_un_refus_motive_part_au_moteur_dans_son_champ(client, state):
    state.appliquer(_demande())

    with client.websocket_connect("/ws/evenements?projet=tous") as ws:
        reponse_http = client.post(
            "/api/validations/t-acte/decision", json={"approuve": False, "motif": CONSIGNE}
        )
        recu = ws.receive_json()

    assert reponse_http.status_code == 200
    assert recu["type"] == EVENEMENT_VALIDATION_DECISION
    assert recu["motif"] == CONSIGNE
    assert recu["etendue"] == ""
    (validation,) = client.get("/api/validations?projet=tous").json()
    assert validation["decision"] == f"refusée depuis la Control Tower — {CONSIGNE}"


def test_une_approbation_pour_le_run_s_ecrit_et_se_voit_dans_les_permissions(
    client, state, store
):
    """C2, côté API : le oui étendu s'écrit dans les permissions de l'agent, et s'y lit."""
    state.appliquer(_demande())

    with client.websocket_connect("/ws/evenements?projet=tous") as ws:
        reponse_http = client.post(
            "/api/validations/t-acte/decision", json={"approuve": True, "etendue": "run"}
        )
        recu = ws.receive_json()

    assert reponse_http.status_code == 200
    assert recu["etendue"] == ETENDUE_RUN and recu["motif"] == ""
    (accord,) = store.accords().lire(AGENT)
    assert (accord.outil, accord.etendue, accord.run_id, accord.tache_id) == (
        "Bash",
        ETENDUE_RUN,
        "run-a",
        "t-acte",
    )
    fiche = client.get(f"/api/catalogue/{AGENT}").json()
    assert [a["id"] for a in fiche["permissions_accords"]] == [accord.id]
    (validation,) = client.get("/api/validations?projet=tous").json()
    assert "et pour tout appel de 'Bash' sur ce run" in validation["decision"]


def test_l_accord_se_retire_depuis_les_permissions_de_l_agent(client, state, store):
    state.appliquer(_demande())
    client.post("/api/validations/t-acte/decision", json={"approuve": True, "etendue": "run"})
    (accord,) = store.accords().lire(AGENT)

    retrait = client.delete(f"/api/permissions/{AGENT}/accords/{accord.id}")

    assert retrait.status_code == 200
    assert retrait.json()["accords"] == []
    assert store.accords().lire(AGENT) == ()
    assert client.get(f"/api/catalogue/{AGENT}").json()["permissions_accords"] == []
    # Déjà retiré : il n'y a plus rien à retirer.
    assert client.delete(f"/api/permissions/{AGENT}/accords/{accord.id}").status_code == 404


def test_un_accord_de_run_solde_n_est_plus_servi(client, state, store):
    """Un accord qui ne couvre plus rien ne se montre plus comme un laissez-passer."""
    state.appliquer(Event(type=EVENEMENT_EXECUTION_STATUT, run_id="run-a", statut=EXECUTION_EN_COURS))
    state.appliquer(_demande())
    client.post("/api/validations/t-acte/decision", json={"approuve": True, "etendue": "run"})
    assert len(client.get(f"/api/catalogue/{AGENT}").json()["permissions_accords"]) == 1

    state.appliquer(
        Event(type=EVENEMENT_EXECUTION_STATUT, run_id="run-a", statut=EXECUTION_TERMINEE)
    )

    assert client.get(f"/api/catalogue/{AGENT}").json()["permissions_accords"] == []


@pytest.mark.parametrize(
    ("demande", "etendue"),
    [
        (_demande(outil="", arguments=None), "run"),  # une tâche : pas d'acte
        (_demande(run_id=""), "run"),  # aucun run à qui l'accord vaudrait
        (_demande(), "projet"),  # aucun projet
        (_demande(), "toujours"),  # étendue inconnue
    ],
)
def test_une_etendue_que_la_demande_ne_permet_pas_est_refusee_sans_rien_trancher(
    client, state, store, demande, etendue
):
    state.appliquer(demande)

    refus = client.post(
        "/api/validations/t-acte/decision", json={"approuve": True, "etendue": etendue}
    )

    assert refus.status_code == 422
    (validation,) = client.get("/api/validations?projet=tous").json()
    assert validation["statut"] == VALIDATION_EN_ATTENTE  # rien n'est parti
    assert store.accords().lire(AGENT) == ()


def test_l_etendue_est_ignoree_sur_un_refus(client, state, store):
    state.appliquer(_demande())

    client.post("/api/validations/t-acte/decision", json={"approuve": False, "etendue": "run"})

    assert store.accords().lire(AGENT) == ()


def test_la_suite_dit_que_l_agent_repart_de_la_consigne(state):
    state.appliquer(_demande())
    validation = state.validation("t-acte")

    assert "replanifie son geste" in suite_de_la_decision(
        validation, approuve=False, motif=CONSIGNE
    )
    assert "sans lui" in suite_de_la_decision(validation, approuve=False)


def test_sans_depot_d_accords_une_etendue_durable_est_refusee(state, bus):
    state.appliquer(_demande())
    attentes = ServiceAttentes(state, bus)

    with pytest.raises(ReglementRefuse) as refus:
        asyncio.run(attentes.trancher("t-acte", approuve=True, etendue=ETENDUE_RUN))

    assert refus.value.motif == MOTIF_ETENDUE_REFUSEE


# --- Le dépôt des accords ---------------------------------------------------------------


def test_un_accord_est_idempotent_et_ne_couvre_que_son_outil_et_son_run(tmp_path):
    accords = AccordStore(tmp_path / "_accords")

    premier = accords.accorder(agent=AGENT, outil="Bash", etendue=ETENDUE_RUN, run_id="r1")
    second = accords.accorder(agent=AGENT, outil="Bash", etendue=ETENDUE_RUN, run_id="r1")

    assert premier == second and len(accords.lire(AGENT)) == 1
    assert accords.couvre(AGENT, "Bash", "r1") == premier
    assert accords.couvre(AGENT, "Bash", "r2") is None
    assert accords.couvre(AGENT, "Bash", "") is None
    assert accords.couvre(AGENT, "Write", "r1") is None
    assert accords.couvre("autre-agent", "Bash", "r1") is None


def test_un_accord_de_projet_ne_vaut_ni_au_gabarit_ni_dans_un_autre_projet(store):
    store.pour_projet("prj-a").accords().accorder(
        agent=AGENT, outil="Bash", etendue=ETENDUE_PROJET
    )

    assert store.pour_projet("prj-a").accords().couvre(AGENT, "Bash", "n-importe") is not None
    assert store.pour_projet("prj-b").accords().couvre(AGENT, "Bash", "n-importe") is None
    assert store.accords().couvre(AGENT, "Bash", "n-importe") is None
    # Et la politique du projet n'en est pas touchée : l'accord vit à côté.
    assert store.pour_projet("prj-a").agents() == ()


@pytest.mark.parametrize(
    ("etendue", "run_id", "outil"),
    [("appel", "r1", "Bash"), ("run", "", "Bash"), ("projet", "", "rm -rf")],
)
def test_un_accord_mal_forme_ne_s_ecrit_pas(tmp_path, etendue, run_id, outil):
    accords = AccordStore(tmp_path / "_accords")
    with pytest.raises(ValueError):
        accords.accorder(agent=AGENT, outil=outil, etendue=etendue, run_id=run_id)
    assert accords.lire(AGENT) == ()


def test_un_accord_illisible_est_ignore_et_ramene_a_l_arbitrage(tmp_path):
    accords = AccordStore(tmp_path / "_accords")
    accords.racine.mkdir(parents=True)
    (accords.racine / f"{AGENT}.json").write_text(
        json.dumps({"accords": [{"id": "x", "agent": AGENT, "outil": "Bash", "etendue": "toujours"}]}),
        encoding="utf-8",
    )
    assert accords.lire(AGENT) == ()


# --- ② Le moteur : les appels suivants ne redemandent plus -------------------------------


def test_sous_un_accord_de_run_les_appels_suivants_ne_redemandent_plus(store):
    """C2, côté moteur : aucune demande ne naît, et chaque appel laisse sa trace."""
    _ecrire_politique(store, POLITIQUE_HUMAINE)
    store.accords().accorder(agent=AGENT, outil="Bash", etendue=ETENDUE_RUN, run_id="run-1185-c2")
    validateur = ValidateurSequence()
    provider = PasseParLeVraiHook(("npm run build", "npm test", "npm run lint"))
    journal = RunJournal(run_id="run-1185-c2")

    asyncio.run(
        _moteur(provider, store, Guardrails(validateur=validateur), PLAN_BUILD).run(
            "Construire", journal=journal
        )
    )

    assert validateur.demandes == []
    assert [sortie for _, sortie in provider.sorties] == [{}, {}, {}]
    traces = [r for r in journal.records if r.etape == f"nettoyer{SUFFIXE_ETAPE_REFUS}"]
    assert len(traces) == 3
    assert all("accord donné pour la suite par la personne" in t.sortie for t in traces)


def test_un_accord_de_run_ne_vaut_pas_pour_un_autre_run(store):
    _ecrire_politique(store, POLITIQUE_HUMAINE)
    store.accords().accorder(agent=AGENT, outil="Bash", etendue=ETENDUE_RUN, run_id="un-autre")
    validateur = ValidateurSequence(True)
    provider = PasseParLeVraiHook(("npm run build",))

    asyncio.run(
        _moteur(provider, store, Guardrails(validateur=validateur), PLAN_BUILD).run(
            "Construire", journal=RunJournal(run_id="run-1185-autre")
        )
    )

    assert len(validateur.demandes) == 1


def test_un_accord_retire_redemande_des_l_appel_suivant(store):
    """Retiré pendant la tâche, l'accord cesse de valoir au prochain appel, pas à la prochaine tâche."""
    _ecrire_politique(store, POLITIQUE_HUMAINE)
    accord = store.accords().accorder(
        agent=AGENT, outil="Bash", etendue=ETENDUE_RUN, run_id="run-1185-retrait"
    )
    validateur = ValidateurSequence(True)

    def retirer_apres_le_premier(rang: int) -> None:
        if rang == 0:
            store.accords().retirer(AGENT, accord.id)

    provider = PasseParLeVraiHook(("npm run build", "npm test"), entre_deux=retirer_apres_le_premier)

    asyncio.run(
        _moteur(provider, store, Guardrails(validateur=validateur), PLAN_BUILD).run(
            "Construire", journal=RunJournal(run_id="run-1185-retrait")
        )
    )

    (demande,) = validateur.demandes
    assert demande.arguments == {"command": "npm test"}


def test_un_accord_ne_leve_pas_la_liste_deny(store):
    _ecrire_politique(store, {"deny": ["Bash"], "ask": {"Bash": "humain"}})
    store.accords().accorder(agent=AGENT, outil="Bash", etendue=ETENDUE_RUN, run_id="run-1185-deny")
    provider = PasseParLeVraiHook(("npm run build",))

    asyncio.run(
        _moteur(provider, store, Guardrails(validateur=ValidateurSequence()), PLAN_BUILD).run(
            "Construire", journal=RunJournal(run_id="run-1185-deny")
        )
    )

    ((_, sortie),) = provider.sorties
    assert "liste deny" in _motif_servi(sortie)


# --- ② De bout en bout : un oui pour le run, depuis l'API, et le moteur ne redemande plus ---


def _attend_que(condition, delai_s: float = 10.0):
    fin = time.monotonic() + delai_s
    while time.monotonic() < fin:
        valeur = condition()
        if valeur:
            return valeur
        time.sleep(0.02)
    raise AssertionError("condition non remplie avant le délai du test")


def test_de_bout_en_bout_un_oui_pour_le_run_dispense_les_appels_suivants(
    client, bus, store
):
    """L'API et le moteur ensemble : une seule demande, tranchée « pour ce run ».

    Le moteur tourne dans la boucle de l'app, sur le même bus : la demande du
    premier `Bash` atteint `GET /api/validations`, la personne l'approuve pour la
    suite du run par la route, l'accord s'écrit dans les permissions de l'agent, et
    les deux commandes suivantes passent sans qu'aucune demande ne naisse.
    """
    _ecrire_politique(store, POLITIQUE_HUMAINE)
    provider = PasseParLeVraiHook(("npm run build", "npm test", "npm run lint"))
    moteur = _moteur(provider, store, Guardrails(validateur=ValidateurControlTower(bus)), PLAN_BUILD)

    issue = client.portal.start_task_soon(
        functools.partial(moteur.run, "Construire", journal=RunJournal(run_id="run-bout"))
    )
    (demande,) = _attend_que(
        lambda: [
            v for v in client.get("/api/validations?projet=tous").json()
            if v["statut"] == VALIDATION_EN_ATTENTE
        ]
    )
    assert demande["arguments"] == {"command": "npm run build"}
    decision = client.post(
        f"/api/validations/{demande['tache_id']}/decision",
        json={"approuve": True, "etendue": "run"},
    )
    report = issue.result(timeout=30)

    assert decision.status_code == 200
    assert [sortie for _, sortie in provider.sorties] == [{}, {}, {}]
    # Une seule demande a jamais existé : les suivantes sont passées sur l'accord.
    assert len(client.get("/api/validations?projet=tous").json()) == 1
    (accord,) = store.accords().lire(AGENT)
    assert accord.run_id == "run-bout" and isinstance(accord, Accord)
    assert all(r.ok for r in report.resultats)
