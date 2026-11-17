"""A project's team, **proposed** by its analysis (#1039, lot 3 of #1021).

The effort of [docs/37](../../docs/37-decision-equipe-sur-mesure.md): a project is
born without agents, and the analysis proposes its team — roles, reasons, number of
instances, playbooks, the tooling skills they attach, and permissions. The user
validates it, and **it alone** creates anything (#1040):

    from maestro.team import propose_team

    proposal = propose_team(
        analysis.constats, analysis.recommandation,
        project_id=project.id, source=analysis.source_manifeste(),
    )
    proposal.summary                     # "Développeur ×2, QA — 2 rôle(s), 3 instance(s) ; …"
    proposal.roles[0].reason             # why this role, and the file that proves it
    proposal.roles[0].skills             # the project skills it attaches
    proposal.roles[0].permissions        # each tier with ITS reason (#716)
    proposal.roles[0].policy()           # the `PolitiqueOutils` that #1040 persists
    proposal.excluded                    # what is not proposed, and why

This package comes **after** the tooling package, and depends on it in that direction
only: a team *attaches* the skills the tooling recommended (`maestro.outillage`). Both
tooling sources meet here without the package having to tell them apart — an
analysed project (#1030) and a new project whose choices were collected (#1031)
return the same `Constats` / `Recommandation` pair.

Six modules, and the boundary between them is the boundary of what decides:

- `maestro.team.model` — the shapes, **inert**: they describe and serialise, they
  touch nothing;
- `maestro.team.role_templates` — the five roles Maestro knows how to propose,
  derived from the frozen agents turned into **role templates** (docs/37 §2.1), and
  the rule that says whether this project calls for one. They are only **material**
  and a **fallback** (#1159): the model composes;
- `maestro.team.composition` (#1159, docs/41) — the team composed by the **model** for
  the project's real need, and corrected in natural language: what it is asked, and
  the **verification** of what it answers. Pure as well — the call lives in
  `maestro.controltower.equipe` (`CompositeurEquipe`);
- `maestro.team.proposal` — from findings and tooling to the team, with, for each
  role, its reason, its location, its instances and its permissions;
- `maestro.team.creation` (#1040) — from the **validated** team to the three
  artefacts of a project agent: its definition (playbook included, skills attached
  inside it), its permission policy, its capacity. Pure as well — writing is the only
  verb of `maestro.controltower.equipe` that touches a repository;
- `maestro.team.gap` — the role an already created team **would lack** to take a
  task (#1041) or to execute a plan (`plan_gap`, #1227). The only module that looks
  at a run in progress rather than at a project being born, and it does not recruit
  either: it names. The engine **proposes** completing the team before executing, and
  a person accepts or not (`maestro.engine.renfort`, #1227); this overturns the last
  two bullets of docs/37 §3, while those of #1040 and docs/31 §3.5 still hold.

⚠ **The proposed policy leaves `allow` open**, and that is a decision. A non-empty
`allow` list is *closed* (`maestro.agents.permissions`): filling it with the role's
tools would refuse everything else, starting with Maestro's in-process channels —
asking a question (#1023), recording a decision (#1024), reporting a blocker —, which
a project's analysis cannot enumerate and whose refusal would make the agent mute.
The five policies shipped with the repository (`core/permissions/`) all open their
`allow` for the same reason. What a role holds is read in `ProposedRole.tools`, which
is **not** a policy. Do not "complete" one with the other.

**The package's promises, and where they hold.** *Nothing is created*: no module
opens a file for writing, none calls an agent repository's `ecrire`, and
`TeamProposal.to_dict()` makes it readable by the caller (`cree`, `validation`).
*Nothing is guessed*: each role carries the project `Piece` that justifies it, each
excluded role its reason, and each permission its own. *The orchestrator is never
recruited*: it has no role template, and it is excluded by name (docs/37 §4.2).

What this package does not do: **write the playbooks**. It sets the role template's
one and the `intent` from which #257 derives this project's; the model call lives in
`maestro.controltower.equipe`, the only layer that knows a provider. What the
proposal returns is **served by the API** —
`POST /api/projets/{id}/equipe/proposition`.
"""

from __future__ import annotations

from maestro.team.creation import (
    BROWSER_HEADING,
    MAX_CREATED_INSTANCES,
    NO_SKILL,
    NO_SKILL_WRITTEN,
    SKILLS_HEADING,
    SKILLS_INTRO,
    CreatedAgent,
    CreatedTeam,
    RejectedRole,
    RetainedSkill,
    ValidatedRole,
    capacity,
    definition,
    observed_skills,
    playbook_with_sections,
    rejected_roles,
    without_frozen_commands,
)
from maestro.team.gap import (
    MissingRole,
    PlanGap,
    missing_role,
    plan_gap,
    uncovered_competencies,
)
from maestro.team.model import (
    ID_PREFIX,
    PLAYBOOK_ORIGIN_GENERATED,
    PLAYBOOK_ORIGIN_TEMPLATE,
    PLAYBOOK_ORIGINS,
    PROPOSAL_VERSION,
    TIERS,
    AttachedSkill,
    ExcludedRole,
    ProposedPermission,
    ProposedRole,
    TeamProposal,
    new_id,
)
from maestro.team.proposal import (
    EXCLUDED_ORCHESTRATOR,
    EXECUTION_TOOL,
    propose_reinforcement,
    propose_team,
    with_playbook,
)
from maestro.team.role_templates import (
    INTERFACE_LANGUAGES,
    MAX_PROPOSED_INSTANCES,
    ROLE_TEMPLATES,
    SINGLE_INSTANCE_REASON,
    SUBSTANTIAL_SHARE,
    Justification,
    RoleTemplate,
)

__all__ = [
    "BROWSER_HEADING",
    "EXCLUDED_ORCHESTRATOR",
    "EXECUTION_TOOL",
    "ID_PREFIX",
    "INTERFACE_LANGUAGES",
    "MAX_CREATED_INSTANCES",
    "MAX_PROPOSED_INSTANCES",
    "NO_SKILL",
    "NO_SKILL_WRITTEN",
    "PLAYBOOK_ORIGINS",
    "PLAYBOOK_ORIGIN_GENERATED",
    "PLAYBOOK_ORIGIN_TEMPLATE",
    "PROPOSAL_VERSION",
    "ROLE_TEMPLATES",
    "SINGLE_INSTANCE_REASON",
    "SKILLS_HEADING",
    "SKILLS_INTRO",
    "SUBSTANTIAL_SHARE",
    "TIERS",
    "AttachedSkill",
    "CreatedAgent",
    "CreatedTeam",
    "ExcludedRole",
    "Justification",
    "MissingRole",
    "PlanGap",
    "ProposedPermission",
    "ProposedRole",
    "RejectedRole",
    "RetainedSkill",
    "RoleTemplate",
    "TeamProposal",
    "ValidatedRole",
    "capacity",
    "definition",
    "missing_role",
    "new_id",
    "observed_skills",
    "plan_gap",
    "playbook_with_sections",
    "propose_reinforcement",
    "propose_team",
    "rejected_roles",
    "uncovered_competencies",
    "with_playbook",
    "without_frozen_commands",
]
