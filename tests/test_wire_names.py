"""The names that travel or are stored keep their value when the code is renamed (#1649).

Translating the code into English renames constants, functions and fields, never
what goes over a wire or into a store: a Redis key, a Celery task name, a Temporal
activity type, a JSON key. Workers of an older version, workflows in flight on the
Temporal server and states saved on disk still use those values (docs/55, rule 3).
This suite pins them, so that a rename which reaches a value fails here first.

The values are written out literally on purpose: comparing a constant with itself
would prove nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from temporalio import activity, workflow

from maestro.decideur import Decideur
from maestro.durable import DURABLE_QUEUE, WORKFLOW_ID_PREFIX, MaestroRunWorkflow, workflow_id
from maestro.durable.activities import execute_task, plan, record_blocker
from maestro.messaging import (
    BROADCAST,
    BROADCAST_CHANNEL,
    MAILBOX_CHANNEL_PREFIX,
    MESSAGE_HANDOFF,
    MESSAGE_NOTIFICATION,
    MESSAGE_REQUEST,
    MESSAGE_RESPONSE,
    MESSAGE_STEP_SUFFIX,
    RELAY_AGENT,
    STATUS_PROCESSED,
    STATUS_READ,
    STATUS_SENT,
    AgentMessage,
    mailbox_channel,
)
from maestro.orchestrator import Brief, Task, validate_brief, validate_task
from maestro.orchestrator.orchestrator import (
    _extract_brief_object,
    _extract_recovery_object,
    _extract_task_array,
)
from maestro.orchestrator.prompt import (
    BRIEF_PLAYBOOK_PATH,
    PLAYBOOK_PATH,
    RECOVERY_PLAYBOOK_PATH,
)
from maestro.orchestrator.recovery import (
    ACTIONS,
    NATURES,
    Attempt,
    TaskFailure,
    validate_recovery,
)
from maestro.outillage.modele import Constats, Langage, Recommandation
from maestro.projects import (
    APPLY_APPROVED,
    APPLY_DECLINED,
    APPLY_NOT_NEEDED,
    AUTHOR_EMAIL,
    AUTHOR_NAME,
    DEFAULT_EXCLUDED,
    DEFAULT_INCLUDED,
    FIRST_COMMIT_MESSAGE,
    KIND_ADDED,
    KIND_DELETED,
    KIND_MODIFIED,
    ORIGINS,
    ApplyResult,
    FileChange,
    Perimeter,
    Project,
    ProjectDiff,
    ProjectSettings,
    ProjectSettingsStore,
    ProjectStore,
    RootRejected,
    ToolingDecision,
    Vcs,
    validate_root,
)
from maestro.projects import ID_PREFIX as PROJECT_ID_PREFIX
from maestro.projects.model import GIT_IDENTITY_ENV
from maestro.queue import EXECUTE_TASK_NAME, TASKS_QUEUE, create_app
from maestro.queue.worker import execute_task as celery_execute_task
from maestro.references import ReferenceTicket
from maestro.router import (
    METHOD_CLASSIFIER,
    METHOD_CLOSEST,
    METHOD_COMPETENCIES,
    METHOD_FALLBACK,
)
from maestro.router.classifier import _parse_classification
from maestro.sources import (
    PREVIEW_RUN,
    REASON_SEEN_AT_LAUNCH,
    REASON_VISION_UNAVAILABLE,
    STATES,
    TYPE_DIRECTORY,
    TYPE_FILE,
    TYPE_URL,
    UPLOADS_DIRECTORY,
    Reading,
    ReadingReport,
    Source,
    SourceRejected,
    resolve_sources,
)
from maestro.sources.upload import Upload
from maestro.team import (
    ROLE_TEMPLATES,
    AttachedSkill,
    CreatedAgent,
    CreatedTeam,
    ExcludedRole,
    ProposedPermission,
    ProposedRole,
    RejectedRole,
    TeamProposal,
)
from maestro.team.composition import (
    CorrectedPlaybook,
    TeamCorrection,
    propose_composed_team,
    read_response,
)
from maestro.team.creation import (
    BROWSER_HEADING,
    EARLIER_SKILLS_HEADINGS,
    SKILLS_HEADING,
    split_skills_section,
)
from maestro.team.model import (
    COMPOSITION_ORIGIN_MODEL,
    COMPOSITION_ORIGIN_RULES,
    ID_PREFIX,
    PLAYBOOK_ORIGIN_GENERATED,
    PLAYBOOK_ORIGIN_SKETCH,
    PLAYBOOK_ORIGIN_TEMPLATE,
    PROPOSAL_VERSION,
    TIERS,
)
from maestro.team.proposal import DECLARED_ORIGIN
from maestro.telemetry.costs import (
    STEP_ACCEPTANCE,
    STEP_ASSESSMENT,
    STEP_BRIEF,
    STEP_CADENCE,
    STEP_PLANNING,
    STEP_PROBE,
    STEP_RESUME,
    STEP_SPENDING_CAP,
    STEP_TEAM,
    STEP_VERSIONS,
    RunCost,
    TaskCost,
)
from maestro.telemetry.langfuse import SCORE_RUN_SUCCEEDED, SCORE_SUCCESS_RATE
from maestro.telemetry.redact import SECRET_MARKER
from maestro.telemetry.usage import StepUsage

ROOT = Path(__file__).resolve().parents[1]


def test_mailbox_channels_keep_their_redis_names() -> None:
    assert MAILBOX_CHANNEL_PREFIX == "maestro.boite."
    assert BROADCAST_CHANNEL == "maestro.diffusion"
    assert mailbox_channel("qa") == "maestro.boite.qa"
    assert BROADCAST == ""


def test_message_types_statuses_and_step_suffix_keep_their_values() -> None:
    assert (MESSAGE_HANDOFF, MESSAGE_REQUEST, MESSAGE_RESPONSE, MESSAGE_NOTIFICATION) == (
        "handoff",
        "requete",
        "reponse",
        "notification",
    )
    assert (STATUS_SENT, STATUS_READ, STATUS_PROCESSED) == ("envoye", "lu", "traite")
    assert MESSAGE_STEP_SUFFIX == ":message"
    assert RELAY_AGENT == "orchestrateur"


def test_an_agent_message_keeps_its_json_keys() -> None:
    message = AgentMessage(
        type=MESSAGE_NOTIFICATION,
        from_agent="qa",
        to_agent="dev",
        task_id="t1",
        run_id="r1",
        subject="salut",
        payload={"cle": "valeur"},
        timestamp="2026-11-13T00:00:00+00:00",
    )
    assert message.to_dict() == {
        "type": "notification",
        "de_agent": "qa",
        "a_agent": "dev",
        "tache_id": "t1",
        "run_id": "r1",
        "objet": "salut",
        "payload": {"cle": "valeur"},
        "statut": "envoye",
        "horodatage": "2026-11-13T00:00:00+00:00",
    }
    # A message written by an older version reads back field for field.
    assert AgentMessage.from_dict(message.to_dict()) == message


def test_the_celery_task_keeps_its_registered_name_and_queue() -> None:
    assert EXECUTE_TASK_NAME == "maestro.executer_tache"
    assert celery_execute_task.name == "maestro.executer_tache"
    assert TASKS_QUEUE == "maestro.taches"
    assert create_app().conf.include == ["maestro.queue.worker"]


def test_the_temporal_activities_keep_their_types() -> None:
    # The type a workflow in flight addresses is the registered name, not the
    # Python function name: the SDK resolves it from the `@activity.defn` decoration.
    activities = (plan, execute_task, record_blocker)
    names = [activity._Definition.must_from_callable(f).name for f in activities]
    assert names == ["planifier", "executer_tache", "consigner_blocage"]


def test_the_temporal_workflow_keeps_its_name_query_and_ids() -> None:
    definition = workflow._Definition.must_from_class(MaestroRunWorkflow)
    assert definition.name == "MaestroRunWorkflow"
    assert list(definition.queries) == ["resultats_acquis"]
    assert DURABLE_QUEUE == "maestro-durable"
    assert WORKFLOW_ID_PREFIX == "maestro-run-"
    assert workflow_id("abc") == "maestro-run-abc"


def test_the_workflow_checkpoint_keeps_its_keys() -> None:
    assert set(MaestroRunWorkflow().checkpoint()) == {
        "objectif",
        "run_id",
        "planification",
        "resultats",
    }


def test_routing_methods_keep_their_values() -> None:
    assert (METHOD_COMPETENCIES, METHOD_CLASSIFIER, METHOD_FALLBACK, METHOD_CLOSEST) == (
        "competences",
        "classifieur",
        "repli",
        "plus_proche",
    )


def test_the_classifier_still_reads_the_confiance_key() -> None:
    verdict = _parse_classification('{"agent": "qa", "confiance": 0.7}', frozenset({"qa"}))
    assert (verdict.agent, verdict.confidence) == ("qa", 0.7)


def test_source_types_reading_states_and_reasons_keep_their_values() -> None:
    assert (TYPE_FILE, TYPE_DIRECTORY, TYPE_URL) == ("fichier", "dossier", "url")
    assert STATES == ("lu", "tronque", "ignore")
    assert (REASON_VISION_UNAVAILABLE, REASON_SEEN_AT_LAUNCH) == (
        "vision-indisponible",
        "vue-au-lancement",
    )
    assert (UPLOADS_DIRECTORY, PREVIEW_RUN) == ("_televersements", "apercu")


def test_a_rejected_source_keeps_its_reason_code() -> None:
    with pytest.raises(SourceRejected) as rejected:
        resolve_sources([{"type": "inconnu", "nom": "x"}], run_id="r1")
    assert rejected.value.reason == "type-inconnu"


def test_a_source_keeps_its_json_keys() -> None:
    source = Source(type="fichier", name="a.md", path="/x/a.md", size=3)
    assert source.to_dict() == {
        "type": "fichier",
        "nom": "a.md",
        "chemin": "/x/a.md",
        "valeur": "",
        "taille": 3,
        "lecture_seule": True,
    }
    # A source written by an older version reads back field for field.
    assert Source.from_dict(source.to_dict()) == source


def test_a_reading_report_keeps_its_json_keys() -> None:
    entry = Reading(name="b.md", type="fichier", state="lu", markdown="b", tokens=1)
    reading = Reading(
        name="d",
        type="dossier",
        state="tronque",
        tokens=2,
        reason="r",
        message="m",
        limit="l",
        entries=(entry,),
    )
    entry_keys = {"nom": "b.md", "type": "fichier", "etat": "lu", "tokens": 1, "motif": ""}
    assert ReadingReport(readings=(reading,)).to_dict() == {
        "tokens": 2,
        "lectures": [
            {
                "nom": "d",
                "type": "dossier",
                "etat": "tronque",
                "tokens": 2,
                "motif": "r",
                "message": "m",
                "limite": "l",
                "entrees": [{**entry_keys, "message": "", "limite": "", "entrees": []}],
            }
        ],
    }


def test_an_upload_keeps_its_json_keys() -> None:
    upload = Upload(id="abc123", name="a.md", size=3, path=Path("/x/a.md"))
    assert upload.to_dict() == {"id": "abc123", "type": "fichier", "nom": "a.md", "taille": 3}
    assert upload.declaration() == {"type": "fichier", "nom": "a.md", "taille": 3}


def test_the_cost_ledger_keeps_its_json_keys_and_the_steps_it_reads() -> None:
    task = TaskCost(
        task_id="t1", name="n", agent="dev", role="r", status="terminee", project_id="p"
    )
    task_keys = {"tache_id", "nom", "agent", "role", "statut", "usage", "ticket", "projet_id"}
    assert set(task.to_dict()) == task_keys
    ledger = RunCost(run_id="r1", tasks=(task,)).to_dict()
    run_keys = {"run_id", "planification", "brief", "taches", "bilan", "sonde", "recette", "total"}
    assert set(ledger) == run_keys
    steps = (
        STEP_PLANNING,
        STEP_BRIEF,
        STEP_RESUME,
        STEP_TEAM,
        STEP_CADENCE,
        STEP_SPENDING_CAP,
        STEP_ASSESSMENT,
        STEP_PROBE,
        STEP_ACCEPTANCE,
        STEP_VERSIONS,
    )
    assert steps == (
        "planification",
        "brief",
        "reprise",
        "equipe",
        "cadence",
        "plafond",
        "bilan",
        "sonde",
        "recette",
        "versions",
    )


def test_langfuse_scores_and_the_secret_marker_keep_their_values() -> None:
    assert (SCORE_RUN_SUCCEEDED, SCORE_SUCCESS_RATE) == ("run-reussi", "taux-reussite")
    assert SECRET_MARKER == "[secret masqué]"


#: The keys `StepUsage.to_dict` writes. The run journal, the cost ledger, the API
#: and `apps/web` read them, and journals on disk still hold them.
STEP_USAGE_KEYS = {
    "appels",
    "tokens_entree",
    "tokens_sortie",
    "tokens_total",
    "cout_usd",
    "duree_ms",
    "duree_api_ms",
    "duree_arbitrage_ms",
    "duree_attente_creneau_ms",
    "duree_attente_atelier_ms",
    "duree_attente_ms",
    "duree_execution_ms",
    "tours",
    "outils",
    "tokens_non_tarifes",
    "tokens_cache_lus",
    "tokens_cache_ecrits",
    "contexte_max",
    "tokens_hors_cache_lu",
}


def test_a_step_usage_keeps_its_json_keys() -> None:
    # Every field holds a distinct value, so that two swapped keys fail too.
    usage = StepUsage(
        calls=2,
        input_tokens=1000,
        output_tokens=50,
        cost_usd=0.25,
        duration_ms=9000,
        api_duration_ms=7000,
        approval_duration_ms=1000,
        slot_wait_duration_ms=500,
        workshop_wait_duration_ms=250,
        turns=3,
        tools=("Read", "Write"),
        unpriced_tokens=40,
        cache_read_tokens=600,
        cache_write_tokens=100,
        max_context=800,
    )
    assert usage.to_dict() == {
        "appels": 2,
        "tokens_entree": 1000,
        "tokens_sortie": 50,
        "tokens_total": 1050,
        "cout_usd": 0.25,
        "duree_ms": 9000,
        "duree_api_ms": 7000,
        "duree_arbitrage_ms": 1000,
        "duree_attente_creneau_ms": 500,
        "duree_attente_atelier_ms": 250,
        "duree_attente_ms": 1750,
        "duree_execution_ms": 7250,
        "tours": 3,
        "outils": ["Read", "Write"],
        "tokens_non_tarifes": 40,
        "tokens_cache_lus": 600,
        "tokens_cache_ecrits": 100,
        "contexte_max": 800,
        "tokens_hors_cache_lu": 450,
    }
    assert set(usage.to_dict()) == STEP_USAGE_KEYS
    # A measure written by an older version reads back field for field.
    assert StepUsage.from_dict(usage.to_dict()) == usage


def test_a_journal_line_without_the_recent_keys_still_reads_back() -> None:
    line = {
        "appels": 1,
        "tokens_entree": 10,
        "tokens_sortie": 2,
        "cout_usd": None,
        "duree_ms": 5,
        "tours": 1,
        "outils": ["Bash"],
    }
    usage = StepUsage.from_dict(line)
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (1, 10, 2)
    assert (usage.duration_ms, usage.turns, usage.tools) == (5, 1, ("Bash",))
    # Unknown stays unknown: no measured wait, no context, every token unpriced.
    assert usage.approval_duration_ms is None and usage.max_context is None
    assert usage.unpriced_tokens == 12


def test_the_screen_reads_the_usage_keys_the_api_writes() -> None:
    types = (ROOT / "apps" / "web" / "lib" / "types.ts").read_text(encoding="utf-8")
    block = re.search(r"^export type Usage = \{\n(.*?)^\};", types, re.MULTILINE | re.DOTALL)
    assert block is not None, "the `Usage` type of apps/web/lib/types.ts is gone"
    keys = set(re.findall(r"^  (\w+)\??:", block.group(1), re.MULTILINE))
    assert keys == STEP_USAGE_KEYS


# --- The orchestrator (#1652) ------------------------------------------------------------
#
# A plan, a brief and a recovery are written by the model, then travel to the Celery queue,
# between the Temporal workflow and its activities, into a run's saved state and to the
# screen. Their keys are those of the shared schemas (`packages/shared/schemas/`), which
# the playbooks ask the model for.


def _task(**changes: object) -> Task:
    # Every field holds a distinct value, so that two swapped keys fail too.
    fields: dict[str, object] = {
        "id": "t2",
        "title": "Écrire l'API",
        "description": "Exposer les contacts",
        "required_competencies": ("api", "python"),
        "output_format": "code",
        "dependencies": ("t1",),
        "ticket": ReferenceTicket(id="#42", url="https://example.com/42"),
        "project_id": "crm-minimal",
        "steps": ("Écrire", "Tester"),
        "granted_act": "vider le dossier",
        "resumes_from": "t0",
    }
    fields.update(changes)
    return Task(**fields)  # type: ignore[arg-type]


def test_a_task_keeps_its_json_keys() -> None:
    task = _task()
    assert task.to_dict() == {
        "id": "t2",
        "titre": "Écrire l'API",
        "description": "Exposer les contacts",
        "competences_requises": ["api", "python"],
        "format_sortie": "code",
        "dependances": ["t1"],
        "ticket": {"id": "#42", "url": "https://example.com/42"},
        "projet_id": "crm-minimal",
        "etapes": ["Écrire", "Tester"],
        "acte_accorde": "vider le dossier",
        "reprend": "t0",
    }
    # The shared schema still accepts them, and a task written by an older version reads
    # back field for field.
    validate_task(task.to_dict())
    assert Task.from_dict(task.to_dict()) == task


def test_a_brief_keeps_its_json_keys() -> None:
    brief = Brief(
        goal="Un CRM minimal",
        in_scope=("contacts",),
        out_of_scope=("facturation",),
        acceptance_criteria=("un contact se crée",),
        constraints=("Python",),
        assumptions=("usage interne",),
        questions=("Quel volume ?",),
    )
    assert brief.to_dict() == {
        "objectif": "Un CRM minimal",
        "perimetre": ["contacts"],
        "hors_perimetre": ["facturation"],
        "contraintes": ["Python"],
        "criteres_acceptation": ["un contact se crée"],
        "hypotheses": ["usage interne"],
        "questions": ["Quel volume ?"],
    }
    validate_brief(brief.to_dict())
    assert Brief.from_dict(brief.to_dict()) == brief


def test_recovery_natures_actions_and_first_attempt_keep_their_values() -> None:
    # The engine compares the actions literally and records them in the journal.
    assert NATURES == ("passager", "configuration", "approche")
    assert ACTIONS == ("rejouer", "retenter", "demander", "abandonner", "proposer")
    attempt = Attempt(tasks=(_task(),), agent="developpeur", role="dev", error="403")
    assert attempt.action == "telle que planifiée"


def test_the_recovery_reads_the_keys_the_model_writes() -> None:
    downstream = _task(id="t3", dependencies=("t2",), resumes_from="", granted_act="")
    failure = TaskFailure(
        task=_task(),
        attempts=(Attempt(tasks=(_task(),), agent="developpeur", role="dev", error="403"),),
        downstream=(downstream,),
    )
    retry = validate_recovery(
        {
            "nature": "approche",
            "geste": "retenter",
            "diagnostic": "le jeton manque",
            "taches": [
                {
                    "id": "t2",
                    "titre": "Écrire l'API sans jeton",
                    "description": "Exposer les contacts en local",
                    "competences_requises": ["api"],
                    "format_sortie": "code",
                }
            ],
            "aval": [{"id": "t3", "description": "Tester en local"}],
        },
        failure,
    )
    assert (retry.nature, retry.action, retry.diagnosis) == (
        "approche",
        "retenter",
        "le jeton manque",
    )
    assert [task.title for task in retry.tasks] == ["Écrire l'API sans jeton"]
    assert retry.adjustments == (("t3", "Tester en local"),)

    ask = validate_recovery(
        {"nature": "configuration", "geste": "demander", "diagnostic": "d", "question": "Q ?"},
        failure,
    )
    assert ask.question == "Q ?"

    propose = validate_recovery(
        {
            "nature": "configuration",
            "geste": "proposer",
            "diagnostic": "d",
            "prerequis": {
                "genre": "role",
                "objet": "un rôle data",
                "raison": "personne ne sait lire le CSV",
                "procedure": "le recruter",
                "competences": ["csv"],
            },
        },
        failure,
    )
    assert propose.prerequisite is not None
    assert (propose.prerequisite.objet, propose.prerequisite.competences) == (
        "un rôle data",
        ("csv",),
    )


@pytest.mark.parametrize("envelope", ["taches", "tâches", "tasks", "plan"])
def test_a_plan_is_still_read_in_each_envelope(envelope: str) -> None:
    text = '{"' + envelope + '": [{"id": "t1"}]}'
    assert _extract_task_array(text) == [{"id": "t1"}]


@pytest.mark.parametrize("envelope", ["brief", "brief_structure", "brief_structuré"])
def test_a_brief_is_still_read_in_each_envelope(envelope: str) -> None:
    text = '{"' + envelope + '": {"objectif": "o"}}'
    assert _extract_brief_object(text) == {"objectif": "o"}
    # Bare, the brief is recognised by its `objectif` key.
    assert _extract_brief_object('{"objectif": "o"}') == {"objectif": "o"}


def test_a_recovery_is_still_read_bare_or_in_its_envelope() -> None:
    # A second object beside it: only the `rattrapage` key can tell which one it is.
    text = '{"rattrapage": {"geste": "rejouer"}, "contexte": {"tour": 1}}'
    assert _extract_recovery_object(text) == {"geste": "rejouer"}
    assert _extract_recovery_object('{"geste": "rejouer"}') == {"geste": "rejouer"}


def test_the_playbooks_keep_their_files_and_markers() -> None:
    # `pyproject.toml` ships them by the `playbook*.md` glob of the package data.
    assert (PLAYBOOK_PATH.name, BRIEF_PLAYBOOK_PATH.name, RECOVERY_PLAYBOOK_PATH.name) == (
        "playbook.md",
        "playbook_brief.md",
        "playbook_rattrapage.md",
    )
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"maestro.orchestrator" = ["playbook*.md"]' in pyproject
    assert 'maestro-orchestrate = "maestro.orchestrator.cli:main"' in pyproject
    markers = set()
    for path in (PLAYBOOK_PATH, BRIEF_PLAYBOOK_PATH, RECOVERY_PLAYBOOK_PATH):
        text = path.read_text(encoding="utf-8")
        markers |= set(re.findall(r"\{\{\s*([a-z_]+)\s*\}\}", text))
    assert markers == {"roles", "equipe", "competences", "competences_recrutables"}


# --- The team (#1670) ---------------------------------------------------------------------
#
# A team proposal, a correction and a created team are served by the API and read by the
# screen (`apps/web/lib/types.ts`). The composition reads back the keys its prompt asks the
# model to write. The origins and the template names are compared and stored, and the
# headings are written into the playbooks of created agents, then found again on disk.


def _proposed_role() -> ProposedRole:
    # Every field holds a distinct value, so that two swapped keys fail too.
    return ProposedRole(
        name="dev",
        role="Développeur",
        role_template="developpeur",
        competencies=("python",),
        reason="du Python",
        instances=2,
        instances_reason="deux surfaces",
        playbook="# Dev",
        playbook_origin=PLAYBOOK_ORIGIN_GENERATED,
        playbook_reason="écrit pour le projet",
        intent="écrire le code",
        tools=("Read",),
        skills=(
            AttachedSkill(
                name="tester",
                path=".claude/skills/tester/SKILL.md",
                state="a-generer",
                reason="il teste",
                commands=("pytest",),
            ),
        ),
        permissions=(
            ProposedPermission(
                tool="Bash",
                tier="ask",
                reason="exécuter",
                decider=Decideur.AUTO,
                scope="projet",
            ),
        ),
        browser=True,
    )


def test_a_team_proposal_keeps_its_json_keys() -> None:
    proposal = TeamProposal(
        id="equ-0001",
        project_id="crm",
        made_at="2026-11-17T10:00:00Z",
        summary="Développeur ×2",
        roles=(_proposed_role(),),
        excluded=(ExcludedRole(name="donnees", role="Base de données", reason="aucun SQL"),),
        source={"analyse": "ana-1"},
        composition_origin=COMPOSITION_ORIGIN_MODEL,
        composition_reason="",
    )
    data = proposal.to_dict()
    assert list(data) == [
        "proposition",
        "id",
        "projet_id",
        "faite_le",
        "resume",
        "source",
        "composition",
        "navigateur_de_maestro",
        "roles",
        "ecartes",
        "instances_total",
        "cree",
        "validation",
    ]
    assert (data["proposition"], data["projet_id"], data["faite_le"], data["resume"]) == (
        1,
        "crm",
        "2026-11-17T10:00:00Z",
        "Développeur ×2",
    )
    assert data["composition"] == {"origine": "modele", "raison": ""}
    assert set(data["navigateur_de_maestro"]) == {"libelle", "usage"}
    assert (data["instances_total"], data["cree"], data["validation"]) == (2, False, "requise")
    assert data["ecartes"] == [{"nom": "donnees", "role": "Base de données", "raison": "aucun SQL"}]
    role = data["roles"][0]
    assert list(role) == [
        "nom",
        "role",
        "gabarit",
        "competences",
        "raison",
        "justification",
        "instances",
        "raison_instances",
        "outils",
        "navigateur",
        "playbook",
        "playbook_origine",
        "playbook_raison",
        "intention",
        "skills",
        "autorisations",
        "politique",
    ]
    assert (role["nom"], role["gabarit"], role["raison_instances"], role["intention"]) == (
        "dev",
        "developpeur",
        "deux surfaces",
        "écrire le code",
    )
    assert role["skills"] == [
        {
            "nom": "tester",
            "chemin": ".claude/skills/tester/SKILL.md",
            "etat": "a-generer",
            "raison": "il teste",
            "commandes": ["pytest"],
        }
    ]
    assert role["autorisations"] == [
        {
            "outil": "Bash",
            "cran": "ask",
            "decideur": "auto",
            "portee": "projet",
            "raison": "exécuter",
        }
    ]


def test_a_team_correction_keeps_its_json_keys() -> None:
    correction = TeamCorrection(
        reply="C'est fait.",
        additions=(_proposed_role(),),
        removals=("tests",),
        restored=("infra",),
        instances={"dev": 3},
        browser={"dev": False},
        playbooks={"dev": CorrectedPlaybook(playbook="# Dev v2", reason="suivre la convention")},
    )
    data = correction.to_dict()
    assert list(data) == [
        "reponse",
        "ajouts",
        "retraits",
        "remis",
        "instances",
        "navigateur",
        "playbooks",
        "cree",
    ]
    assert (data["reponse"], data["retraits"], data["remis"], data["cree"]) == (
        "C'est fait.",
        ["tests"],
        ["infra"],
        False,
    )
    assert (data["instances"], data["navigateur"]) == ({"dev": 3}, {"dev": False})
    assert data["ajouts"][0]["nom"] == "dev"
    assert data["playbooks"] == {
        "dev": {
            "playbook": "# Dev v2",
            "playbook_origine": "genere",
            "playbook_raison": "suivre la convention",
        }
    }


def test_a_created_team_and_a_rejected_role_keep_their_json_keys() -> None:
    created = CreatedTeam(
        project_id="crm",
        proposal_id="equ-0001",
        agents=(
            CreatedAgent(
                name="dev",
                role="Développeur",
                instances=2,
                role_template="developpeur",
                skills=("tester",),
                browser=True,
            ),
        ),
    )
    assert created.to_dict() == {
        "projet_id": "crm",
        "proposition_id": "equ-0001",
        "cree": True,
        "agents": [
            {
                "nom": "dev",
                "role": "Développeur",
                "instances": 2,
                "gabarit": "developpeur",
                "skills": ["tester"],
                "politique": None,
                "navigateur": True,
            }
        ],
        "instances_total": 2,
    }
    assert RejectedRole(name="dev", reason="nom déjà pris").to_dict() == {
        "nom": "dev",
        "raison": "nom déjà pris",
    }


def test_the_composition_reads_the_keys_the_model_writes() -> None:
    text = json.dumps(
        {
            "roles": [
                {
                    "nom": "mobile",
                    "role": "Développeur mobile",
                    "raison": "une application Flutter",
                    "gabarit": "",
                    "competences": ["flutter", "dart"],
                    "skills": [],
                    "instances": 2,
                    "raison_instances": "iOS et Android",
                    "preuve": "lib/main.dart",
                    "navigateur": True,
                }
            ],
            "ecartes": [{"nom": "donnees", "raison": "aucune base"}],
            "retraits": ["tests"],
            "remis": ["infra"],
            "instances": {"dev": 2},
            "navigateur": {"dev": True},
            "playbooks": {"dev": "# Dev"},
            "reponse": "Voilà.",
        },
        ensure_ascii=False,
    )
    response = read_response(text)
    assert (response.removals, response.restored, response.reply) == (
        ("tests",),
        ("infra",),
        "Voilà.",
    )
    assert (dict(response.instances), dict(response.browser), dict(response.playbooks)) == (
        {"dev": 2},
        {"dev": True},
        {"dev": "# Dev"},
    )
    findings = Constats(
        langages=(Langage(nom="Dart", fichiers=3, part=1.0, exemple="lib/main.dart"),)
    )
    proposal = propose_composed_team(findings, Recommandation(), response, project_id="crm")
    (role,) = proposal.roles
    assert (role.name, role.role, role.reason, role.competencies) == (
        "mobile",
        "Développeur mobile",
        "une application Flutter",
        ("flutter", "dart"),
    )
    assert (role.instances, role.instances_reason, role.browser) == (2, "iOS et Android", True)
    assert role.justification is not None and role.justification.chemin == "lib/main.dart"
    excluded = {e.name: e.reason for e in proposal.excluded}
    assert excluded["donnees"] == "aucune base"


def test_team_origins_tiers_and_templates_keep_their_values() -> None:
    assert (PLAYBOOK_ORIGIN_GENERATED, PLAYBOOK_ORIGIN_TEMPLATE, PLAYBOOK_ORIGIN_SKETCH) == (
        "genere",
        "gabarit",
        "esquisse",
    )
    assert (COMPOSITION_ORIGIN_MODEL, COMPOSITION_ORIGIN_RULES) == ("modele", "regles")
    assert DECLARED_ORIGIN == "declaree"
    assert (ID_PREFIX, PROPOSAL_VERSION, TIERS) == ("equ-", 1, ("allow", "ask", "deny"))
    assert [(t.name, t.agent_name) for t in ROLE_TEMPLATES] == [
        ("dev", "developpeur"),
        ("donnees", "bdd"),
        ("infra", "devops"),
        ("interface", "designer"),
        ("tests", "qa"),
    ]


def test_the_playbook_sections_keep_their_headings() -> None:
    # A created agent's playbook carries these headings on disk; they are how its
    # sections are found again, replaced or removed.
    assert SKILLS_HEADING == "## Les skills du projet qui sont les tiens"
    assert EARLIER_SKILLS_HEADINGS == ("## Les skills du projet qui sont les vôtres",)
    assert BROWSER_HEADING == "## Le navigateur de Maestro"
    for heading in (SKILLS_HEADING, *EARLIER_SKILLS_HEADINGS):
        body, section = split_skills_section(f"# Dev\n\nCorps.\n\n{heading}\n\n- tester\n")
        assert body.rstrip() == "# Dev\n\nCorps."
        assert section.startswith(heading)


# --- The projects (#1671) -----------------------------------------------------------------
#
# A project's declaration is a `<id>.json` file in the projects store, read back by every
# later version; the projects settings are the single `projets.json` file of the settings
# store. The diff of a task's work and the outcome of its application are served by the API
# and carried by the events. The origins, the change kinds, the apply outcomes and the
# refusal reasons are compared and stored; the identity and the first commit message are
# written into the person's repository.


@pytest.fixture()
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # `tmp_path` lives under the profile's `AppData` on Windows, which validation rejects.
    home = tmp_path / "maison"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def test_a_project_declaration_keeps_its_json_keys() -> None:
    project = Project(
        id="prj-0001",
        name="Dépensio",
        root="D:/projets/depensio",
        origin="nouveau",
        vcs=Vcs(type="git", base_branch="main", remote="git@exemple:depensio.git"),
        perimeter=Perimeter(included=("src",), excluded=(".env",)),
        tooling=ToolingDecision(deferred_at="2026-11-18T10:00:00+00:00"),
        created_at="2026-11-01T09:00:00+00:00",
        updated_at="2026-11-02T09:00:00+00:00",
    )
    written = {
        "id": "prj-0001",
        "nom": "Dépensio",
        "racine": "D:/projets/depensio",
        "origine": "nouveau",
        "vcs": {"type": "git", "branche_base": "main", "distant": "git@exemple:depensio.git"},
        "perimetre": {"inclus": ["src"], "exclus": [".env"]},
        "outillage": {"reporte_le": "2026-11-18T10:00:00+00:00"},
        "cree_le": "2026-11-01T09:00:00+00:00",
        "modifie_le": "2026-11-02T09:00:00+00:00",
    }
    assert project.to_dict() == written
    assert Project.from_dict(written) == project


def test_the_project_store_writes_one_file_per_project_under_its_id(
    tmp_path: Path, _isolated_home: Path
) -> None:
    root = _isolated_home / "depensio"
    created = ProjectStore(tmp_path / "projets").create("Dépensio", root, origin="nouveau")
    assert re.fullmatch(r"prj-[0-9a-f]{8}", created.id)
    assert PROJECT_ID_PREFIX == "prj-"
    stored = json.loads((tmp_path / "projets" / f"{created.id}.json").read_text("utf-8"))
    assert set(stored) == {
        "id",
        "nom",
        "racine",
        "origine",
        "vcs",
        "perimetre",
        "outillage",
        "cree_le",
        "modifie_le",
    }
    assert stored["nom"] == "Dépensio"
    assert stored["origine"] == "nouveau"


def test_the_project_settings_keep_their_file_and_keys(
    tmp_path: Path, _isolated_home: Path
) -> None:
    directory = _isolated_home / "Maestro"
    store = ProjectSettingsStore(tmp_path / "reglages")
    store.write(directory)
    stored = json.loads((tmp_path / "reglages" / "projets.json").read_text("utf-8"))
    assert set(stored) == {"repertoire", "dossiers_autorises", "modifie_le"}
    settings = ProjectSettings.from_dict(
        {"repertoire": "D:/Maestro", "dossiers_autorises": ["D:/code"], "modifie_le": "t"}
    )
    assert (settings.directory, settings.allowed_folders, settings.updated_at) == (
        "D:/Maestro",
        ("D:/code",),
        "t",
    )
    assert settings.to_dict() == {
        "repertoire": "D:/Maestro",
        "dossiers_autorises": ["D:/code"],
        "modifie_le": "t",
    }


def test_a_project_diff_and_an_apply_result_keep_their_json_keys() -> None:
    diff = ProjectDiff(
        changes=(
            FileChange(path="src/app.py", kind=KIND_MODIFIED, additions=3, deletions=1),
            FileChange(path="logo.png", kind=KIND_ADDED, binary=True),
        ),
        branch="maestro/t1",
        base="main",
    )
    served = {
        "modifications": [
            {
                "chemin": "src/app.py",
                "nature": "modifie",
                "ajouts": 3,
                "suppressions": 1,
                "binaire": False,
            },
            {
                "chemin": "logo.png",
                "nature": "ajoute",
                "ajouts": 0,
                "suppressions": 0,
                "binaire": True,
            },
        ],
        "branche": "maestro/t1",
        "base": "main",
        "fichiers": 2,
        "ajouts": 3,
        "suppressions": 1,
    }
    assert diff.to_dict() == served
    assert ProjectDiff.from_dict(served) == diff
    result = ApplyResult(status=APPLY_APPROVED, diff=diff, detail="fusionné", applied=("a",))
    assert result.to_dict() == {
        "statut": "approuvee",
        "diff": served,
        "detail": "fusionné",
        "appliques": ["a"],
    }


def test_project_origins_kinds_outcomes_and_identity_keep_their_values() -> None:
    assert ORIGINS == frozenset({"nouveau", "existant"})
    assert (KIND_ADDED, KIND_MODIFIED, KIND_DELETED) == ("ajoute", "modifie", "supprime")
    assert (APPLY_NOT_NEEDED, APPLY_APPROVED, APPLY_DECLINED) == (
        "sans_objet",
        "approuvee",
        "refusee",
    )
    assert DEFAULT_EXCLUDED == (".git", "node_modules", ".env", "**/secrets/**")
    assert DEFAULT_INCLUDED == (".",)
    assert (AUTHOR_NAME, AUTHOR_EMAIL) == ("Maestro", "maestro@localhost")
    assert GIT_IDENTITY_ENV == {
        "GIT_AUTHOR_NAME": "Maestro",
        "GIT_AUTHOR_EMAIL": "maestro@localhost",
        "GIT_COMMITTER_NAME": "Maestro",
        "GIT_COMMITTER_EMAIL": "maestro@localhost",
    }
    assert FIRST_COMMIT_MESSAGE == "Maestro : état initial du projet"


@pytest.mark.parametrize(
    ("path", "reason"), [("", "chemin-vide"), ("relatif/projet", "chemin-relatif")]
)
def test_a_rejected_root_keeps_its_reason_code(path: str, reason: str) -> None:
    with pytest.raises(RootRejected) as rejection:
        validate_root(path)
    assert rejection.value.reason == reason
