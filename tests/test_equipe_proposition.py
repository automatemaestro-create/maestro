"""The project analysis **proposes** its team — and creates nothing (#1039).

Final lot of the #1021 effort (#1043) — lot 3 had shipped without tests, by the
splitting convention ([docs/10 §5.1](../docs/10-workflow-git.md)).

Four rules decide in `maestro.team`, and they are what is held here
(see the header of `maestro.team.proposal`):

① **nothing without its location** — each role carries the project `Piece` that makes
   it exist, the file read and not a sentence: a team is contested by opening the
   files that designated it;
② **what is not proposed is named, with its reason** — otherwise "no database role"
   would read as a failure of Maestro rather than as a fact of the project. It is also
   where the orchestrator lives, which is never a team member: it is Maestro, and it
   is Maestro that recruits;
③ **each permission carries its reason, and the tier derives from the project** —
   the ticket's criterion (#716): Maestro *drafts* the policy, the user *decides* it.
   An `auto` tier proposed without its reason would be a decision taken without
   anyone;
④ **nothing is created** — `cree: False`, `validation: "requise"`, and no repository
   opened for writing.

Since #1102, a fifth rule joins them, one the module did not carry and that a real run
pointed to:

⑤ **a role's playbook and its permission agree** — the `intent` from which #257 will
   write the playbook carries the **tier proposed for the execution tool**. Without
   it, the playbook ordered as its first move a command a person had to approve, and a
   team validated as is waited for a human from its first task.

The findings are **fabricated**: that is what makes the derivation testable without a
real project, without a disk and without a model provider. The tooling recommendation,
for its part, is derived from these findings by `recommander` — the real one, so that
a skill attached here is a skill the tooling would recommend there.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from maestro import team
from maestro.agents.regime_d_execution import (
    PORTEE_DE_L_AGENT,
    REGIME_DE_L_AGENT,
    REGIME_EXECUTION,
    REGIME_PORTEE,
)
from maestro.agents.store import NOMS_RESERVES
from maestro.controltower.generation_agent import _CADRE_GENERATION, INTENTION_MAX
from maestro.decideur import Decideur
from maestro.outillage.modele import Commande, Constats, Langage, Piece
from maestro.outillage.questionnaire import Choix
from maestro.outillage.recommandation import recommander
from maestro.portee import PORTEE_PROJET
from maestro.team import (
    EXCLUDED_ORCHESTRATOR,
    EXECUTION_TOOL,
    ID_PREFIX,
    MAX_PROPOSED_INSTANCES,
    PLAYBOOK_ORIGIN_GENERATED,
    PLAYBOOK_ORIGIN_TEMPLATE,
    PROPOSAL_VERSION,
    ROLE_TEMPLATES,
    SUBSTANTIAL_SHARE,
    ProposedPermission,
    propose_team,
    with_playbook,
)

PROJECT = "prj-depensio"

#: The verbs through which a module of the package would touch a repository — writing
#: a file, creating a directory, opening in write mode, or an agent repository's
#: `ecrire`. Searched as a **use** (the call), never as a mention: a docstring that
#: talks about writing does not write.
_WRITE = re.compile(r"\.(write_text|write_bytes|mkdir|ecrire)\(|\bopen\([^)]*[\"']w")


def _language(name: str, share: float, files: int = 10, example: str = "") -> Langage:
    return Langage(nom=name, fichiers=files, part=share, exemple=example or f"src/x.{name}")


def _findings(**extra) -> Constats:
    """An ordinary Python project: one dominant language and nothing else."""
    base = {"langages": (_language("Python", 1.0, example="src/app.py"),)}
    return Constats(**{**base, **extra})


def _propose(findings: Constats, **extra):
    """This project's proposal, on the real tooling recommendation."""
    return propose_team(findings, recommander(findings), project_id=PROJECT, **extra)


def _role(proposal, name: str):
    """The proposed role named `name` — raises if the team does not carry it."""
    return next(role for role in proposal.roles if role.name == name)


# --- ④ Nothing is created -----------------------------------------------------


def test_the_proposal_says_in_plain_words_that_it_created_nothing() -> None:
    """`cree` and `validation` are not settings: they are the ticket's promises made
    readable by the caller, and no call can change them."""
    served = _propose(_findings()).to_dict()

    assert served["cree"] is False
    assert served["validation"] == "requise"
    assert served["proposition"] == PROPOSAL_VERSION


def test_no_module_of_the_team_package_opens_a_file_for_writing() -> None:
    """The promise taken at the source. "Nothing is created" is said in the served
    shape, but is **held** in the code: no module of `maestro.team` writes, and
    writing is the only verb of `maestro.controltower.equipe`.

    The pattern is proven on a faulty sample before sweeping: a guard that no longer
    recognised anything would turn green on a package that writes.
    """
    faulty = "    chemin.write_text(json.dumps(role), encoding='utf-8')\n"
    assert _WRITE.search(faulty) is not None, "le motif ne reconnaît plus une écriture"

    package = Path(team.__file__).parent
    for module in sorted(package.glob("*.py")):
        fault = _WRITE.search(module.read_text(encoding="utf-8"))
        assert fault is None, f"{module.name} écrit ({fault.group(0)!r})"


def test_the_proposal_id_is_a_reference_not_a_counter() -> None:
    first = _propose(_findings())
    second = _propose(_findings())

    assert first.id.startswith(ID_PREFIX)
    assert first.id != second.id
    assert first.project_id == PROJECT


def test_the_manifest_provenance_is_copied_as_is() -> None:
    """Six months later, it will say whether this team comes from the analysis of an
    existing project or from the answers given on a new project."""
    source = {"origine": "analyse", "analyse_id": "ana-1234"}

    served = _propose(_findings(), source=source).to_dict()

    assert served["source"] == source


# --- ① Nothing without its location -------------------------------------------


def test_the_always_proposed_role_carries_the_dominant_language_and_its_file() -> None:
    dev = _role(_propose(_findings()), "dev")

    assert "Python" in dev.reason
    assert dev.justification == Piece(
        nom="Python", chemin="src/app.py", role="10 fichier(s) Python"
    )


def test_a_project_without_any_language_keeps_a_developer_without_inventing_evidence() -> None:
    """"A project exists so that one writes in it": it is the only role a project calls
    for without having anything to prove — and inventing evidence would be worse than
    having none."""
    dev = _role(_propose(Constats()), "dev")

    assert dev.justification is None
    assert "aucun langage de code n'a été constaté" in dev.reason


def test_each_proposed_role_carries_its_reason_and_its_fallback_playbook() -> None:
    proposal = _propose(
        _findings(commandes=(Commande(usage="tester", commande="pytest", chemin="Makefile"),))
    )

    for role in proposal.roles:
        assert role.reason.strip()
        assert role.playbook.strip()
        assert role.playbook_origin == PLAYBOOK_ORIGIN_TEMPLATE
        assert role.playbook_reason.strip()
        assert role.intent.strip()


def test_a_proposed_role_never_carries_the_name_of_its_role_template() -> None:
    """The lineage can be read (`role_template`), but the name differs — otherwise the
    package's document would hide the playbook written for this project
    (`playbook_outille`), silently. It is also what the repository's name reservation
    would refuse."""
    proposal = _propose(_findings())

    for role in proposal.roles:
        assert role.name != role.role_template
        assert role.name not in NOMS_RESERVES


# --- ② What is not proposed is named ------------------------------------------


def test_the_orchestrator_is_excluded_by_name_from_every_team() -> None:
    """It has no role template and never will: it is Maestro, and it is Maestro that
    recruits. Excluding it silently would suggest that no project has one."""
    proposal = _propose(_findings())

    assert EXCLUDED_ORCHESTRATOR in proposal.excluded
    assert all(role.name != "orchestrateur" for role in proposal.roles)
    assert "c'est lui qui recrute" in EXCLUDED_ORCHESTRATOR.reason


def test_a_bare_project_proposes_only_the_developer_and_names_the_four_others() -> None:
    proposal = _propose(_findings())

    assert [role.name for role in proposal.roles] == ["dev"]
    excluded_names = {excluded.name for excluded in proposal.excluded}
    assert excluded_names == {"orchestrateur", "donnees", "infra", "interface", "tests"}


def test_an_excluded_role_names_what_is_missing_in_the_project() -> None:
    """The reason names what is missing, not what Maestro did not do."""
    excluded = next(e for e in _propose(_findings()).excluded if e.name == "donnees")

    assert "aucun fichier SQL" in excluded.reason
    assert "bornes de l'analyse" in excluded.reason


def test_an_excluded_role_promises_no_action() -> None:
    """#1159: an excluded role's reason said "Vous pouvez l'ajouter à la validation",
    whereas the screen only allowed unchecking what was proposed.

    The reason now states the **fact** only; the action is named by the screen that
    offers it, next to its control. A sentence served to two surfaces (the team step
    and the thread card) cannot promise what one of them lacks."""
    for excluded in _propose(_findings()).excluded:
        assert "validation" not in excluded.reason, excluded.name
        assert "Vous pouvez" not in excluded.reason, excluded.name


def test_observed_sql_proposes_the_database_role() -> None:
    findings = _findings(
        langages=(
            _language("Python", 0.8, example="src/app.py"),
            _language("SQL", 0.2, files=4, example="db/schema.sql"),
        )
    )

    proposal = _propose(findings)

    database = _role(proposal, "donnees")
    assert database.role_template == "bdd"
    assert database.justification is not None
    assert database.justification.chemin == "db/schema.sql"


def test_a_declared_nature_is_enough_to_propose_the_interface_role() -> None:
    """On a **new** project, no file proves the screens yet: the questionnaire answer
    says so."""
    proposal = _propose(_findings(), choices=(Choix(cle="nature", valeur="application-web"),))

    interface = _role(proposal, "interface")
    assert interface.role_template == "designer"
    assert "application web" in interface.reason


# --- ③ Each permission carries its reason -------------------------------------


def test_each_proposed_permission_is_named_with_its_reason() -> None:
    proposal = _propose(
        _findings(
            commandes=(
                Commande(
                    usage="tester",
                    commande="pytest",
                    chemin="Makefile",
                    extrait="test:",
                    origine="declaree",
                ),
            )
        )
    )

    for role in proposal.roles:
        for permission in role.permissions:
            assert permission.reason.strip()
            assert permission.tool == EXECUTION_TOOL


def test_the_auto_tier_names_the_project_files_where_the_commands_were_read() -> None:
    """The ticket's criterion (#716): `auto` is not "the machine approves", it is a
    decision taken in advance — and it names what it is taken on."""
    findings = _findings(
        commandes=(
            Commande(
                usage="tester",
                commande="pytest -q",
                chemin="Makefile",
                extrait="test:",
                origine="declaree",
            ),
        )
    )

    tests = _role(_propose(findings), "tests")

    execution = next(a for a in tests.permissions if a.tool == EXECUTION_TOOL)
    assert execution.tier == "ask"
    assert execution.effective_decider is Decideur.AUTO
    assert execution.scope == PORTEE_PROJET
    assert "Makefile" in execution.reason
    assert "ne le borne pas à celles-là" in execution.reason


def test_a_convention_command_is_not_a_read_command() -> None:
    """Retaining it would pass a guess off as a reading — the exact flaw
    `ORIGINES_COMMANDE` exists to prevent.

    ⚠ Since #1226, what this check separates is no longer the **tier** — a validated
    team executes in its project whatever happens — but the **served reason**: a
    permission that names the evidence that designated it is contested by opening that
    evidence. A convention command is not one, so it is not cited."""
    findings = _findings(
        commandes=(
            Commande(
                usage="tester", commande="pytest", chemin="pyproject.toml", origine="convention"
            ),
        )
    )

    tests = _role(_propose(findings), "tests")

    execution = next(a for a in tests.permissions if a.tool == EXECUTION_TOOL)
    assert execution.effective_decider is Decideur.AUTO
    assert execution.scope == PORTEE_PROJET
    assert "pyproject.toml" not in execution.reason
    assert "aucune n'a pu être lue" in execution.reason


def test_the_reason_names_the_project_skill_where_the_commands_are_written() -> None:
    """#1591: a skill the project carries writes the role's commands — the reason says so.

    Without a declared command, the reason claimed that "aucune commande n'est encore
    écrite dans le projet", whereas the attached skill, present on disk, carries them.
    The tier does not move (#1226): only the served reason says where they are.
    """
    skill = ".agents/skills/lancer-les-tests/SKILL.md"
    findings = _findings(
        commandes=(
            Commande(
                usage="tester", commande="pytest", chemin="pyproject.toml", origine="convention"
            ),
        ),
        outillage_present=(Piece(nom="lancer-les-tests", chemin=skill, role="skill"),),
    )

    proposal = _propose(findings)
    tests = _role(proposal, "tests")

    assert [(s.path, s.state) for s in tests.skills] == [(skill, "deja-present")]
    execution = next(a for a in tests.permissions if a.tool == EXECUTION_TOOL)
    assert (execution.tier, execution.effective_decider, execution.scope) == (
        "ask",
        Decideur.AUTO,
        PORTEE_PROJET,
    )
    assert skill in execution.reason
    assert "aucune n'a pu être lue" not in execution.reason
    # A convention command remains a guess: it is not cited as read.
    assert "pyproject.toml" not in execution.reason
    assert "sans vous demander de trancher chaque commande" in execution.reason
    assert "sort du dossier du projet" in execution.reason
    # The developer does not attach this skill: the reason says that **its** commands are
    # written nowhere, without claiming that none is written in the project.
    (dev_permission,) = _role(proposal, "dev").permissions
    assert "Aucune commande de ce rôle n'est encore écrite dans le projet" in dev_permission.reason


def test_the_proposed_policy_leaves_allow_open_and_carries_the_decider() -> None:
    """A non-empty `allow` list is *closed*: filling it with the role's tools would
    refuse Maestro's in-process channels — asking a question, recording a decision —
    and would make the agent mute."""
    dev = _role(_propose(_findings()), "dev")

    policy = dev.policy()

    assert policy.allow == ()
    assert policy.deny == ()
    assert [entry for entry in policy.ask] == [EXECUTION_TOOL]
    assert policy.decideur(EXECUTION_TOOL) is Decideur.AUTO
    # The scope travels with the tier (#1226): an `auto` served without it would be a
    # permission to execute anywhere, and that is not what is proposed.
    assert policy.decide(EXECUTION_TOOL).portee == PORTEE_PROJET
    assert policy.to_dict()["portees"] == {EXECUTION_TOOL: PORTEE_PROJET}


def test_the_served_policy_cannot_contradict_the_detailed_permissions() -> None:
    """Both travel side by side — the screen shows the reasons one by one, creation
    only needs the policy —, and they come from the same source."""
    dev = _role(_propose(_findings()), "dev").to_dict()

    tiers = {a["outil"]: a["cran"] for a in dev["autorisations"]}
    assert set(dev["politique"]["ask"]) == {tool for tool, tier in tiers.items() if tier == "ask"}


def test_an_unknown_tier_is_refused_at_construction() -> None:
    """An unknown tier would produce a policy that `PermissionStore` would refuse to
    read back — that is, a proposal that could only be invalidated at creation."""
    with pytest.raises(ValueError, match="cran d'autorisation inconnu"):
        ProposedPermission(tool="Bash", tier="parfois", reason="…")


def test_a_decider_is_only_set_on_the_ask_tier() -> None:
    """An `allow` passes without anyone deciding, a `deny` refuses without anyone
    deciding."""
    with pytest.raises(ValueError, match="décideur ne se pose que sur le cran"):
        ProposedPermission(
            tool="Bash", tier="allow", reason="…", decider=Decideur.AUTO
        )


def test_an_ask_without_decider_escalates_rather_than_self_approving() -> None:
    served = ProposedPermission(tool="Bash", tier="ask", reason="…").to_dict()

    assert served["decideur"] == str(Decideur.HUMAIN)


# --- ⑤ The playbook says the craft, Maestro says the regime (#1102, #1405) ----


@pytest.mark.parametrize(
    ("name", "findings"),
    [
        ("dev", _findings()),
        (
            "tests",
            _findings(
                commandes=(
                    Commande(
                        usage="tester",
                        commande="pytest -q",
                        chemin="Makefile",
                        extrait="test:",
                        origine="declaree",
                    ),
                )
            ),
        ),
    ],
)
def test_the_intent_no_longer_gives_the_regime_to_the_model(name: str, findings) -> None:
    """#1405: the intent no longer says under which regime the role executes.

    #1102 had brought it in and #1226 had added the scope to it; the model that drafts
    the playbook **rephrased** it, and on p5 it added an exception the policy does not
    have ("joindre un service extérieur") — the agent made someone wait before an
    `npm install` that went through on its own. The regime is now written by Maestro
    from the policy (`maestro.agents.regime_d_execution`), and what is not given to the
    model, the model does not rephrase. The tier, for its part, has not moved: the
    proposed permission remains the one #1226 set."""
    role = _role(_propose(findings), name)

    execution = next(a for a in role.permissions if a.tool == EXECUTION_TOOL)
    assert execution.effective_decider is Decideur.AUTO
    sentences = (
        *REGIME_EXECUTION.values(),
        *REGIME_PORTEE.values(),
        *REGIME_DE_L_AGENT.values(),
        *PORTEE_DE_L_AGENT.values(),
    )
    assert not any(sentence.strip() in role.intent for sentence in sentences)


def _loaded_findings():
    """Three languages, five commands: enough to attach several skills to each role."""
    return _findings(
        langages=(
            _language("Python", 0.5, example="src/app.py"),
            _language("TypeScript", 0.3, example="web/app.ts"),
            _language("CSS", 0.2, example="web/app.css"),
        ),
        commandes=tuple(
            Commande(usage=usage, commande=f"make {usage}", chemin="Makefile", origine="declaree")
            for usage in ("installer", "construire", "demarrer", "tester", "lint")
        ),
    )


def test_the_intent_fits_under_the_generator_bound() -> None:
    """`ServiceEquipe._playbook` cuts the intent at `INTENTION_MAX`: what the project
    is, which closes the sentence, would be the first lost."""
    for role in _propose(_loaded_findings()).roles:
        # The cut made by `_playbook` must be a non-event.
        assert role.intent[:INTENTION_MAX] == role.intent


def test_the_intent_does_not_give_the_skills_to_the_model() -> None:
    """#1404: the model that drafts the playbook copied the skills the intent named to
    it ("Ton périmètre", on p5), then creation added its section — the agent read its
    list twice. Only the creation's section names them now."""
    roles = _propose(_loaded_findings()).roles

    assert any(role.skills for role in roles)
    for role in roles:
        for skill in role.skills:
            assert skill.name not in role.intent, (role.name, skill.name)
        assert "skill" not in role.intent.lower()


def test_the_generation_frame_leaves_the_regime_to_maestro() -> None:
    """#257's instruction, since #1405: the playbook says the craft, not the
    permissions — and #1102's method rule (a command is never the first move) stays in
    it, **unconditionally**, because it no longer depends on a tier the intent no
    longer gives."""
    # The frame is wrapped at ~80 columns: an expected sentence crosses a line end in
    # it. So we compare on the flattened text, not on the layout.
    frame = " ".join(_CADRE_GENERATION.split())

    assert "Le playbook dit le métier de l'agent, pas ses permissions" in frame
    assert "Maestro l'écrit lui-même, depuis la politique qu'il applique à l'agent" in frame
    assert "jamais d'une commande à exécuter le premier geste obligatoire" in frame
    assert "avec son outil de lecture, jamais par le shell" in frame
    # No instruction left that would make the model write the regime the intent used
    # to give it: that is how p5's exception got in.
    assert "Si l'intention dit" not in frame
    assert "Nomme alors les seules exceptions" not in frame


def test_on_a_new_project_the_dev_receives_the_right_to_execute_in_the_project() -> None:
    """The first criterion of #1226. A new project declares no command: until then the
    `dev` received `humain` there, and the run of 2026-09-22 asked for 14 `Bash`
    validations for 14 approvals — `mkdir`, `python`, `pytest`, and the cleanup of the
    caches its own executions had just produced.

    The proposed permission now states both halves, and the reason is what makes it
    decidable at validation (#1040): what it does without disturbing anyone, and what
    still comes back to the person."""
    dev = _role(_propose(_findings()), "dev")

    execution = next(a for a in dev.permissions if a.tool == EXECUTION_TOOL)
    assert execution.tier == "ask"
    assert execution.effective_decider is Decideur.AUTO
    assert execution.scope == PORTEE_PROJET
    assert execution.to_dict()["portee"] == PORTEE_PROJET
    # What it does alone — and cleanup is no longer part of it (#1401): what gets
    # rebuilt stays, ignored; what it stops at the end of a task is what it started.
    assert "sans vous demander de trancher chaque commande" in execution.reason
    assert "nettoi" not in execution.reason
    assert "arrête ce qu'il a lancé" in execution.reason
    # …and what still comes back to you — the three families, named.
    assert "sort du dossier du projet" in execution.reason
    assert "effacerait ce que vous aviez posé là" in execution.reason
    assert "arrêterait des programmes par leur nom" in execution.reason


# --- Instances: how many, and why ---------------------------------------------


def test_a_single_substantial_language_gives_one_instance_with_its_reason() -> None:
    dev = _role(_propose(_findings()), "dev")

    assert dev.instances == 1
    assert "on augmente les instances pour absorber la charge" in dev.instances_reason


def test_two_substantial_languages_are_two_surfaces_so_two_instances() -> None:
    findings = _findings(
        langages=(
            _language("Python", 0.55, example="src/app.py"),
            _language("TypeScript", 0.45, example="web/app.ts"),
        )
    )

    dev = _role(_propose(findings), "dev")

    assert dev.instances == 2
    assert "TypeScript" in dev.instances_reason
    assert "décision, pas une mesure" in dev.instances_reason


def test_a_language_handled_by_another_role_does_not_recruit_one_more_developer() -> None:
    """The same material would be counted twice: once to recruit the designer, once to
    take its work away from it."""
    findings = _findings(
        langages=(
            _language("Python", 0.6, example="src/app.py"),
            _language("CSS", 0.4, example="web/style.css"),
        )
    )

    proposal = _propose(findings)

    assert _role(proposal, "dev").instances == 1
    assert _role(proposal, "interface").role_template == "designer"


def test_a_residue_under_the_threshold_does_not_justify_one_more_instance() -> None:
    findings = _findings(
        langages=(
            _language("Python", 0.95, example="src/app.py"),
            _language("Shell", SUBSTANTIAL_SHARE / 2, example="build.sh"),
        )
    )

    assert _role(_propose(findings), "dev").instances == 1


def test_the_proposed_instances_are_capped() -> None:
    findings = _findings(
        langages=tuple(
            _language(name, 0.2, example=f"src/x.{name}")
            for name in ("Python", "TypeScript", "Go", "Rust", "Java")
        )
    )

    dev = _role(_propose(findings), "dev")

    assert dev.instances == MAX_PROPOSED_INSTANCES


# --- Attached skills, and the definition's name -------------------------------


def test_a_role_attaches_the_project_skills_by_usage() -> None:
    """By usage, never by name: `SKILL_PAR_USAGE` holds the names, and a usage the
    recommendation excluded produces nothing."""
    findings = _findings(
        commandes=(
            Commande(usage="tester", commande="pytest", chemin="Makefile", origine="declaree"),
            Commande(
                usage="installer", commande="pip install -e .", chemin="Makefile",
                origine="declaree",
            ),
        )
    )

    proposal = _propose(findings)

    assert [s.name for s in _role(proposal, "dev").skills] == ["mettre-en-route"]
    assert [s.name for s in _role(proposal, "tests").skills] == ["lancer-les-tests"]


def test_an_attached_skill_copies_the_entry_that_recommended_it() -> None:
    findings = _findings(
        commandes=(
            Commande(usage="tester", commande="pytest", chemin="Makefile", origine="declaree"),
        )
    )
    recommendation = recommander(findings)

    tests = _role(
        propose_team(findings, recommendation, project_id=PROJECT), "tests"
    )

    entry = next(e for e in recommendation.entrees if e.nom == "lancer-les-tests")
    attached = tests.skills[0]
    assert (attached.path, attached.state, attached.commands) == (
        entry.chemin,
        entry.etat,
        entry.commandes,
    )
    assert attached.reason != entry.raison  # why **this role** attaches it


def test_a_name_already_taken_in_the_project_is_suffixed_before_being_proposed() -> None:
    """The collision is not a validation — the repository remains the sole judge at
    creation —, but proposing "dev" to a project that already has one is the frequent,
    avoidable case."""
    proposal = _propose(_findings(), taken_names=("dev",))

    assert _role(proposal, "dev-2").role_template == "developpeur"


# --- The summary and the reported playbook ------------------------------------


def test_the_summary_fits_on_one_line_and_counts_the_excluded() -> None:
    findings = _findings(
        langages=(
            _language("Python", 0.55, example="src/app.py"),
            _language("TypeScript", 0.45, example="web/app.ts"),
        )
    )

    proposal = _propose(findings)

    assert proposal.summary.startswith("Développeur ×2")
    assert f"{len(proposal.excluded)} rôle(s) écarté(s)" in proposal.summary
    assert proposal.total_instances == sum(r.instances for r in proposal.roles)


def test_a_playbook_written_for_the_project_replaces_the_role_template_one() -> None:
    """The only path for this replacement: drafting lives in the Control Tower, the
    only layer that knows a model provider."""
    dev = _role(_propose(_findings()), "dev")

    reported = with_playbook(
        dev, "Tu travailles sur Dépensio.", origin=PLAYBOOK_ORIGIN_GENERATED, reason="écrit"
    )

    assert reported.playbook == "Tu travailles sur Dépensio."
    assert reported.playbook_origin == PLAYBOOK_ORIGIN_GENERATED
    assert dev.playbook != reported.playbook  # the original proposal is inert


def test_an_unknown_playbook_origin_is_refused() -> None:
    """Without this guard, we would serve a proposal whose playbook origin could not be
    told — the information this field exists to carry."""
    dev = _role(_propose(_findings()), "dev")

    with pytest.raises(ValueError, match="origine de playbook inconnue"):
        with_playbook(dev, "texte", origin="ailleurs", reason="…")


def test_an_empty_playbook_is_refused() -> None:
    dev = _role(_propose(_findings()), "dev")

    with pytest.raises(ValueError, match="playbook vide"):
        with_playbook(dev, "   ", origin=PLAYBOOK_ORIGIN_GENERATED, reason="…")


def test_the_role_templates_are_consulted_and_never_instantiated() -> None:
    """What docs/37 §2.1 calls "frozen agents become role templates": the material is
    there, the roles descend from it, and nothing instantiates them."""
    assert {template.agent_name for template in ROLE_TEMPLATES} <= NOMS_RESERVES
    assert all(template.name not in NOMS_RESERVES for template in ROLE_TEMPLATES)
    assert all(template.fallback_playbook().strip() for template in ROLE_TEMPLATES)
