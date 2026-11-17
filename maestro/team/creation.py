"""From the **validated** team to the three artefacts of a project agent (#1040).

The counterpart of [`maestro.team.proposal`](./proposal.py), and their symmetry is
the point: the proposal starts from the findings and returns a team **that nobody
has accepted yet**; this module starts from the team **that was accepted** and
returns what must be written for it to exist. Like its neighbour, it is pure — it
touches neither the disk, nor the forge, nor any agent store. What writes is
`maestro.controltower.equipe`, the only layer that knows a declared project and its
six stores.

## What "creating a role" means, and where each piece goes

A validated role goes into **three** of the six stores of `ConfigurationAgents`
(#1038), and nowhere else:

| What the user validated | What is written | Store |
| --- | --- | --- |
| name, role, competencies, **playbook** | `AgentDefinition` | `agents` |
| the **permissions**, reviewed one by one | `PolitiqueOutils` | `permissions` |
| the number of **instances** | `CapaciteAgent` | `capacites` |

Settings overrides (#259) receive nothing: a project's analysis says nothing about
the model a role should work with (`maestro.team.model`). A definition without
settings takes the executors' default model, and that is exactly what is meant.

**MCP servers** (#104) receive one thing only (#1532): **Maestro's browser**, for a
role the validated team equips with it (`ValidatedRole.browser`). It enters the
project's pool and is enabled for that role — `maestro.controltower.equipe` writes
it, this module only names it in the playbook (`browser_section`). No other server
is derived from a project.

## Attached skills live in the playbook, and that is a decision

The ticket's criterion asks for agents created "with **their skills attached**".
There is no "skills per agent" store, and none is created here: a project's tooling
reaches **the whole project** through the task message (#1032,
`maestro.outillage.contexte`), manifest bounds included. What an agent lacks is
therefore not access to the skills — it has them all — but knowing **which ones are
its own**. That is an instruction, so it lives in its playbook:
`playbook_with_sections()` adds a section that names them, with their path — and
**without** the commands they wrap (see below).

Three reasons to prefer this to a sixth store:

1. **it can be reread and edited where the team is reviewed** — a project agent's
   playbook is edited from its screens (#72, #1038), so re-attaching a skill is a
   gesture that already exists;
2. **nothing goes stale silently**: a skill removed from the project leaves a line
   in a playbook that gets reread, where an association table would point to a
   dead path that nobody would ever open;
3. **no new boundary.** Adding a store would force the executor to read it, hence
   to filter the tooling per agent — that is, to undo `outillage_du_projet`, which
   already bounds what comes in (docs/38 §5).

The section is **idempotent**: placing it again replaces the previous one instead of
stacking it, so that a re-validated team does not leave two contradictory
inventories in the same prompt.

## The skill is authoritative: the section freezes no command (#1520)

A command is **replaced** after recruitment — said by the person
(`maestro.outillage.correction`), proposed by the review that follows a run (#1381)
— and that replacement rewrites the tooling items and the manifest, not a copy
stored in each agent's system prompt. A playbook that copied the command (« il
enveloppe : `python assembler.py` ») would keep the old one, and an agent of the
next run would receive two commands for the same gesture — its playbook's and the
skill's.

The section therefore names the skill and its path, nothing more: the command lives
**once**, in the skill, which the correction rewrites. A team whose section still
carries frozen commands (the form written before #1520) is **rewired**
(`without_frozen_commands`) on every tooling write
(`maestro.controltower.recu_par_l_equipe`): its section loses its frozen commands,
the rest of its playbook does not move.

## Only the skills the project **holds** are attached (#1212)

The proposal attaches a role to the skills the tooling **recommends**, including
those not written yet (`a-generer`): at that stage, it is a marching order
(`maestro.team.model.AttachedSkill`). But a team can be validated without its
tooling being written — the chat proposes one without mentioning tooling —, and the
playbook would then name `.agents/skills/…/SKILL.md` in a project that has no
`.agents/`. The agent goes looking for the file, and its search waits for a human
(#1211).

Creation therefore decides **on disk**, at the moment it writes: `observed_skills()`
keeps only the skills whose path exists in the project, and sets the others aside
(`ValidatedRole.missing_skills`) so that the section tells the truth — *the
recommended tooling is not written* — rather than *none was recommended*. The module
stays pure: the caller (`maestro.controltower.equipe`), the only one that knows the
project root, supplies the observation. Tooling written **after** the team is not
re-attached on its own; the playbook is reread and edited from the agent screens
(#1038), and a path added there does exist, this time.

## What is checked **before** anything is written

A team is written into three stores × N roles: there is no transaction, and a file
system will not offer one. The recourse is therefore to **check everything before
writing anything** (`rejected_roles`), so that the common case — a name already
taken, a malformed policy — ends in a reasoned rejection and **no write at all**,
rather than in half a team. It is the rule `PermissionStore.ecrire` already applies
at the scale of one file ("a store that wrote only half of a guardrail policy would
be worse than no write at all"), carried here to the scale of the team.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from maestro.agents.capacity import CapaciteAgent
from maestro.agents.navigateur import CONSIGNE_DE_L_AGENT, GESTES_DE_L_AGENT, outils_du_navigateur
from maestro.agents.navigateur import LIBELLE as BROWSER_LABEL
from maestro.agents.permissions import PolitiqueOutils
from maestro.agents.store import (
    NOMS_RESERVES,
    AgentDefinition,
    definition_validee,
)

#: The heading of the section `playbook_with_sections()` places in the playbook. It is
#: also an **anchor**: it is how an already placed section is found to be replaced, which
#: is why it is not reworded lightly — two spellings would make two sections.
#:
#: It addresses the agent with « tu », like the whole playbook (#1404,
#: `generation_agent._CADRE_GENERATION`): « qui sont les vôtres » would address it with
#: « vous » under a craft written with « tu ». The earlier heading stays an anchor
#: (`EARLIER_SKILLS_HEADINGS`): a definition written with it keeps its text until it is
#: rewired, which replaces it instead of stacking a second section.
SKILLS_HEADING = "## Les skills du projet qui sont les tiens"

#: The earlier headings of a skills section, recognised so that they are replaced — never
#: written.
EARLIER_SKILLS_HEADINGS: tuple[str, ...] = ("## Les skills du projet qui sont les vôtres",)

#: What the section says before listing. Two sentences, because an agent's playbook is not
#: documentation: they recall that the skills are the **project's** (the agent receives them
#: all, #1032), that this list says which ones belong to its role, and that the command is
#: read **in the skill** (#1520) — the skill carries it, up to date with what the project
#: corrected.
SKILLS_INTRO = (
    "L'outillage du projet t'est transmis en entier ; ceux-ci sont ceux que ton "
    "rôle branche. Chacun porte la commande qu'il enveloppe, et c'est lui qui fait "
    "foi : ouvre-le plutôt que de réinventer la commande."
)

#: The earlier intro sentences of a skills section, replaced on rewiring
#: (`without_frozen_commands`) — never written.
EARLIER_SKILLS_INTROS: tuple[str, ...] = (
    "L'outillage du projet t'est transmis en entier ; ceux-ci sont ceux que ton "
    "rôle branche. Sers-t'en plutôt que de réinventer la commande.",
)

#: What is written for a role that attaches **no** skill. The section stays placed, on
#: purpose: "this role has none" and "nobody filed its skills" are two different facts,
#: and only the first one is a decision.
NO_SKILL = (
    "Aucun skill du projet n'est rattaché à ton rôle : l'outillage du projet "
    "n'en a recommandé aucun pour ce que tu fais."
)

#: What is written when the tooling **recommended** skills for this role but the project
#: does not hold them (#1212). Same opening as `NO_SKILL` — the fact is the same, no skill
#: is attached —, another reason, and the instruction the agent needs: not to go looking
#: for them. The names are not repeated: naming a missing skill invites a search for it.
NO_SKILL_WRITTEN = (
    "Aucun skill du projet n'est rattaché à ton rôle : l'outillage recommandé "
    "pour ce que tu fais n'est pas écrit dans le projet. Ne le cherche pas ; "
    "sers-toi des commandes que le projet déclare."
)

#: The instance ceiling a **creation** accepts. A decision, not a measurement — like the
#: ceiling of three the *proposal* sets (`MAX_PROPOSED_INSTANCES`), and higher than it
#: because it is no longer Maestro that proposes but a person who decides. It exists so
#: that a typo does not set a capacity to 400; it is raised in the capacity screens (#86),
#: which are not bounded.
MAX_CREATED_INSTANCES = 20


@dataclass(frozen=True)
class RetainedSkill:
    """A tooling skill this role attaches — taken from the proposal.

    `path` and `commands` come from the tooling `Entree` that recommended it
    (`maestro.outillage.modele`), character for character: the playbook names a
    path that exists in the project, never a rewording — and `observed_skills()`
    makes sure it **exists** (#1212). `commands` is not written into the playbook
    (#1520): the skill carries it.
    """

    name: str
    path: str = ""
    commands: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidatedRole:
    """A role the user **kept**, as they reread and adjusted it.

    It is deliberately the form the API **served** (`ProposedRole.to_dict()`) and
    not a re-derivation: what is created must be exactly what was shown. Two fields
    say it better than all the rest:

    - `playbook` is the one read on screen — written for this project by the
      mechanism of #257, or the template's when drafting did not succeed.
      Replaying it at validation would return **another** text, hence create an
      agent that nobody validated;
    - `policy` is the `PolitiqueOutils` that `ProposedRole.policy()` returned,
      brought back as is. It is the **only** path of the translation "proposed
      permissions → policy" (`maestro.team.model`), and keeping it unique is what
      guarantees that an `auto` tier read with its reason is the tier that will be
      written. `None`: the role has no permission to set, and **no policy file is
      written** — which is not the same thing as an empty policy, which would be a
      policy.

    `instances` is the only field the screen freely **adjusts**. `role_template`
    only serves the trace (which frozen role this one descends from, docs/37 §2.1).

    `missing_skills` is **never** served nor brought back by the API:
    `observed_skills()` fills it, with the validated skills the project does not
    hold, so that the playbook section says why it is empty.

    `browser` (#1532): this role receives Maestro's browser, as the screen showed
    it — proposed by the composition, removed or given in words. Creation enables
    it for the role in the project's pool, and its playbook names it.
    """

    name: str
    role: str
    competencies: tuple[str, ...]
    playbook: str
    instances: int = 1
    role_template: str = ""
    skills: tuple[RetainedSkill, ...] = ()
    policy: PolitiqueOutils | None = None
    missing_skills: tuple[RetainedSkill, ...] = ()
    browser: bool = False


@dataclass(frozen=True)
class CreatedAgent:
    """What a validation wrote for a role — enough to reread it, not to redo it.

    Serves the API response: the screen announces what now exists in the project,
    and the agent screens take over (#1038).
    """

    name: str
    role: str
    instances: int
    role_template: str = ""
    skills: tuple[str, ...] = ()
    policy: PolitiqueOutils | None = None
    browser: bool = False

    def to_dict(self) -> dict[str, Any]:
        """The created agent as JSON."""
        return {
            "nom": self.name,
            "role": self.role,
            "instances": self.instances,
            "gabarit": self.role_template,
            "skills": list(self.skills),
            "politique": None if self.policy is None else self.policy.to_dict(),
            "navigateur": self.browser,
        }


@dataclass(frozen=True)
class RejectedRole:
    """A role whose creation is refused, **before** any write, and why.

    `name` can be empty: a role without a name is refused too, and saying so "for
    role ''" is better than keeping the faulty line quiet.
    """

    name: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """The rejection as JSON."""
        return {"nom": self.name, "raison": self.reason}


@dataclass(frozen=True)
class CreatedTeam:
    """The report of a validation — what was created, and under which proposal.

    `cree` is **true** as soon as the team went through: it is the counterpart of
    the `cree: False` the proposal serves (`TeamProposal.to_dict`), and the two read
    together — the first says it did nothing, this one says what was done.
    """

    project_id: str
    proposal_id: str = ""
    agents: tuple[CreatedAgent, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """The whole report as JSON — the form the API serves."""
        return {
            "projet_id": self.project_id,
            "proposition_id": self.proposal_id,
            "cree": True,
            "agents": [agent.to_dict() for agent in self.agents],
            "instances_total": sum(agent.instances for agent in self.agents),
        }


def rejected_roles(
    roles: Sequence[ValidatedRole], taken_names: Iterable[str] = ()
) -> tuple[RejectedRole, ...]:
    """What prevents writing this team — **everything**, before writing any part of it.

    Five causes, and none is a surprise when reading a rejection: a name already
    taken in this project (or reserved everywhere), a duplicate **within the
    proposal itself** (two lines that would write the same file), an instance count
    out of bounds, an empty playbook, and what the stores would refuse anyway (name
    not a slug, empty role, no competency).

    The last two families are **delegated**: the `AgentDefinition` goes through the
    same validator as the store's (`AgentStore.ecrire`) and the policy through
    `PermissionStore`'s. Copying their rules here would make them diverge, and
    divergence is precisely what a pre-check must avoid — a check more permissive
    than the write is useless, a stricter one refuses what would pass.

    Returns the **whole** list rather than the first rejection: correcting a team of
    five roles one rejection at a time would be five round trips.
    """
    taken = {name for name in taken_names} | set(NOMS_RESERVES)
    seen: set[str] = set()
    rejected: list[RejectedRole] = []
    for role in roles:
        name = role.name.strip()
        if name in seen:
            rejected.append(
                RejectedRole(name, f"« {name} » figure deux fois dans l'équipe validée.")
            )
            continue
        seen.add(name)
        if name in taken:
            rejected.append(
                RejectedRole(
                    name,
                    f"« {name} » est déjà pris dans ce projet (ou réservé par le "
                    "dépôt) : renommez le rôle, ou retirez-le de l'équipe.",
                )
            )
            continue
        if not 1 <= role.instances <= MAX_CREATED_INSTANCES:
            rejected.append(
                RejectedRole(
                    name,
                    f"{role.instances} instance(s) pour « {name} » : attendu entre 1 "
                    f"et {MAX_CREATED_INSTANCES}.",
                )
            )
            continue
        error = _store_error(role)
        if error is not None:
            rejected.append(RejectedRole(name, error))
    return tuple(rejected)


def _store_error(role: ValidatedRole) -> str | None:
    """What the stores would refuse — asked **of the stores**, never rewritten.

    One exception, and it is here because the store cannot see it: an **empty**
    playbook would pass its validation, `playbook_with_sections()` having already
    placed the skills section on top of it. So the **validated** playbook is judged,
    the one read on screen, and not the augmented definition — otherwise a role
    would arrive in the project with, for its whole system prompt, the inventory of
    its skills.
    """
    if not role.playbook.strip():
        return (
            f"playbook vide pour « {role.name} » : un agent dont le prompt système "
            "se réduit à la liste de ses skills ne saurait pas quoi en faire."
        )
    try:
        definition(role)
    except ValueError as exc:
        return str(exc)
    if role.policy is not None:
        try:
            PolitiqueOutils.from_dict(role.policy.to_dict())
        except ValueError as exc:  # pragma: no cover - an already built policy
            return str(exc)
    return None


def definition(role: ValidatedRole) -> AgentDefinition:
    """The definition to persist — playbook **augmented with its attached skills**.

    No model, no provider, no effort: a project's analysis says nothing about them,
    and setting them would be inventing (`maestro.team.model`). The definition
    therefore takes the executors' defaults, and is tuned afterwards from its
    screens.

    Raises `ValueError` — the store's, word for word — if the definition is invalid.
    """
    return definition_validee(
        AgentDefinition(
            nom=role.name.strip(),
            role=role.role,
            competences=tuple(role.competencies),
            playbook=playbook_with_sections(role),
        )
    )


def capacity(role: ValidatedRole) -> CapaciteAgent:
    """The capacity setting: active, and the validated number of instances.

    `actif=True` unconditionally — it has just been recruited. An agent one does not
    want to see working right away is disabled from its screens (#86); creating it
    switched off would make no sense at the end of a team validation.
    """
    return CapaciteAgent(nom=role.name.strip(), actif=True, instances=role.instances)


def observed_skills(role: ValidatedRole, holds: Callable[[str], bool]) -> ValidatedRole:
    """The role, its skills reduced to those the project **holds** — the others set aside.

    `holds(path)` answers for the disk: only the caller knows the project root, and
    this module opens none. A skill without a path cannot be observed, so it is not
    attached: the playbook asserts only what was seen. The kept skills follow the
    validation's order.
    """
    kept = tuple(s for s in role.skills if s.path and holds(s.path))
    missing = tuple(s for s in role.skills if s not in kept)
    return replace(role, skills=kept, missing_skills=role.missing_skills + missing)


def playbook_with_sections(role: ValidatedRole) -> str:
    """The validated playbook, its sections placed (or placed again) at the end.

    Two sections, which Maestro places and nothing drafts: the **skills** one,
    always, and the **browser** one (#1532) when the role receives it.

    Idempotent: a section already present is **replaced**, never stacked — and a
    role whose browser was removed loses its section. The cut is made at the heading
    (`SKILLS_HEADING`, `BROWSER_HEADING`) and runs to the next heading of the same
    level, so that a playbook with sections after them keeps them — a playbook is a
    system prompt, not a file one truncates.
    """
    body = _without_placed_sections(role.playbook).rstrip()
    sections = [_skills_section(role.skills, role.missing_skills)]
    if role.browser:
        sections.append(browser_section())
    return f"{body}\n\n" + "\n\n".join(sections) + "\n"


#: The heading of the section `playbook_with_sections()` places for a role that receives
#: Maestro's browser (#1532). An **anchor**, like `SKILLS_HEADING`: it is how the section
#: is found to be replaced, or removed.
BROWSER_HEADING = "## Le navigateur de Maestro"


def browser_section() -> str:
    """The browser section, as it appears in the agent's system prompt.

    Its actions and its instruction come from `maestro.agents.navigateur`, the only
    module that knows the adapter's tools: the section copies them, it does not write
    them.
    """
    lines = [
        BROWSER_HEADING,
        "",
        f"Tu as {BROWSER_LABEL} : ses outils commencent par "
        f"`{outils_du_navigateur()}`. Sers-t'en pour regarder ce que le projet affiche :",
        "",
        *(f"- {action}" for action in GESTES_DE_L_AGENT),
        "",
        CONSIGNE_DE_L_AGENT,
    ]
    return "\n".join(lines)


def _skills_section(
    skills: Sequence[RetainedSkill], missing: Sequence[RetainedSkill] = ()
) -> str:
    """The section, as it appears in the agent's system prompt.

    Each skill by its name and its path — its command, the skill carries it (#1520).
    """
    if not skills:
        return f"{SKILLS_HEADING}\n\n{NO_SKILL_WRITTEN if missing else NO_SKILL}"
    lines = [SKILLS_HEADING, "", SKILLS_INTRO, ""]
    for skill in skills:
        path = f" — `{skill.path}`" if skill.path else ""
        lines.append(f"- **{skill.name}**{path}")
    return "\n".join(lines)


def without_frozen_commands(playbook: str) -> str:
    """The playbook, its skills section stripped of the commands it froze (#1520).

    Rewires a playbook in the form written before #1520: each skill line there
    carries « (il enveloppe : `…`) », copied at recruitment, and its intro sentence
    points to that command. Both are placed again in the current form
    (`_skills_section`); nothing else moves — neither the playbook's body, written
    for the project and reread by the person, nor the sections that follow, nor a
    section without skills. It is the form that **Maestro** writes that is
    recognised here, character for character, like its heading
    (`_SKILLS_SECTION_PATTERN`): a line a person rewrote is not Maestro's, and stays.

    Idempotent: a playbook that freezes nothing comes back **identical**, which lets
    the caller write only what changes.
    """
    found = _SKILLS_SECTION_PATTERN.search(playbook)
    if found is None:
        return playbook
    section = found.group(0)
    rewritten = "\n".join(_line_in_current_form(line) for line in section.split("\n"))
    if rewritten == section:
        return playbook
    return f"{playbook[: found.start()]}{rewritten}{playbook[found.end() :]}"


def _line_in_current_form(line: str) -> str:
    """A line of the skills section, in the current form — unchanged otherwise."""
    if line.rstrip() in EARLIER_SKILLS_INTROS:
        return SKILLS_INTRO
    if line.startswith("- **"):
        return _FROZEN_COMMAND_PATTERN.sub("", line)
    return line


#: What a skill line in the form written before #1520 adds after its path: the frozen
#: command, to the end of the line — a command can carry its own parentheses.
_FROZEN_COMMAND_PATTERN = re.compile(r" \(il enveloppe : .*\)(?=\r?$)")


def split_skills_section(playbook: str) -> tuple[str, str]:
    """`(body, section)`: the playbook without its placed sections, and those sections.

    The sections are **those Maestro placed** (`playbook_with_sections`) — the
    skills, and the browser if it is there (#1532) —, cut at the same headings and
    by the same pattern: that is what lets a model rewrite the body (#1405,
    `maestro.controltower.regeneration_playbooks`) without it ever touching what is
    not drafted. `section` returns them in their order, and is empty when the
    playbook carries none.
    """
    sections = [found.group(0).strip() for found in _PLACED_SECTIONS_PATTERN.finditer(playbook)]
    return _without_placed_sections(playbook).strip(), "\n\n".join(sections)


#: The skills section alone: its heading — the current one or an earlier one —, up to the
#: next heading of the same level or the end of the document. It is the one the rewiring
#: of #1520 places again (`without_frozen_commands`); the browser section, which follows
#: it, starts at a heading of the same level, so it does not enter it.
_SKILLS_SECTION_PATTERN = re.compile(
    rf"^(?:{'|'.join(re.escape(t) for t in (SKILLS_HEADING, *EARLIER_SKILLS_HEADINGS))})"
    r"\s*$.*?(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)

#: A section placed by Maestro: its heading — the skills one, one of its earlier headings,
#: or the browser one —, up to the next heading of the same level or the end of the
#: document.
_PLACED_SECTIONS_PATTERN = re.compile(
    r"^(?:"
    + "|".join(
        re.escape(t) for t in (SKILLS_HEADING, *EARLIER_SKILLS_HEADINGS, BROWSER_HEADING)
    )
    + r")\s*$.*?(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)


def _without_placed_sections(playbook: str) -> str:
    """The playbook stripped of the sections Maestro placed in it, if it carried any."""
    return _PLACED_SECTIONS_PATTERN.sub("", playbook)
