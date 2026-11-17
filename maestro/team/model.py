"""The shapes of a team **proposed** for a project (#1039).

Inert, on the pattern of [`maestro.outillage.modele`](../outillage/modele.py): this
module **describes and serialises**; it touches neither the disk, nor the forge, nor
any agent repository. What derives a team from them lives elsewhere —
`maestro.team.role_templates` (the roles Maestro knows how to propose and what
justifies them) and `maestro.team.proposal` (the derivation itself). That is what
makes both testable on fabricated findings, without a real project.

Four objects, and the boundary between the first two is the one not to blur:

- **the proposed role** (`ProposedRole`) — what Maestro proposes to create: its
  reason, its instances, its playbook, the project skills it attaches and its
  permissions. Each one carries the **evidence** from the project that justifies it,
  just like a tooling `Entree`: a proposal without its location could not be
  checked;
- **the excluded role** (`ExcludedRole`) — what Maestro chose **not** to propose,
  with its reason. Without this list, "no database role" would read as something
  Maestro forgot rather than as a fact of the project. It is also where the
  orchestrator lives, which is **never** a team member (docs/37 §4.2);
- **the proposed permission** (`ProposedPermission`) — a tool, its tier, and **its
  reason**. The `ask` tier also carries its decider (#586), and that is where the
  ticket's criterion holds: *every proposed `auto` permission is named with its
  reason* (#716, docs/37 §4.3);
- **the attached skill** (`AttachedSkill`) — what this role takes from the project's
  tooling, copied as is from the `Entree` that recommended it.

`ProposedRole.policy()` returns the `PolitiqueOutils` (#110) that #1040 persists: it
is the **only** place where proposed permissions become a policy, and a second
translation would end up no longer saying the same thing as the one shown to the
user.

⚠ **Nothing is created.** `TeamProposal.to_dict()` says so in plain words
(`cree`, `validation`), on the pattern of `Bornes.to_dict()`, which returns
`lecture_seule` and `execution`: these are not settings, they are the ticket's
promises made readable by the caller. No field of this module can be negotiated from
outside, and nothing here opens a file for writing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from maestro.agents.navigateur import CE_QU_IL_FAIT
from maestro.agents.navigateur import NOM as BROWSER_NAME
from maestro.agents.permissions import EntreeArbitrage, PolitiqueOutils
from maestro.decideur import DECIDEUR_DEFAUT, Decideur
from maestro.outillage.modele import Piece
from maestro.portee import PORTEES

#: Version of the served shape. It travels in the response for the reason that gave
#: rise to `VERSION_ANALYSE`: a consumer — #1040, the screen that shows the team —
#: must be able to say "I cannot read this shape" rather than misread a shape that
#: has changed.
PROPOSAL_VERSION = 1

#: Prefix of team proposal identifiers. Same entropy as project and analysis
#: identifiers — 8 hex digits of a truncated `uuid4` — and for the same reason: it is
#: the **reference** a created team keeps to what proposed it.
ID_PREFIX = "equ-"

#: The three tiers a proposed permission can carry — those of `PolitiqueOutils`, and
#: nothing else. The list lives here because it bounds what `ProposedPermission`
#: accepts; the **semantics** (deny > ask > allow) live in `maestro.agents.permissions`
#: and are not restated.
TIERS: tuple[str, ...] = ("allow", "ask", "deny")

#: Where a proposed role's playbook comes from. `genere`: the mechanism of #257 wrote
#: it for this project; `gabarit`: the provider could produce nothing and the frozen
#: role's document serves as the fallback. The distinction is **served**, never
#: silenced: a generic playbook and a playbook written for the project are not worth
#: the same, and nothing would tell them apart on reading.
#:
#: `esquisse` (#1159): a role **composed by the model outside the role templates** — a
#: mobile application, a learning pipeline, security — has no fallback document. Until
#: its drafting succeeds, it carries a sketch written from its label, its reason and
#: its competencies, on the roles' common base
#: (`maestro.team.composition.sketch_playbook`). It is not a template playbook, and it
#: is not served under that name.
PLAYBOOK_ORIGIN_GENERATED = "genere"
PLAYBOOK_ORIGIN_TEMPLATE = "gabarit"
PLAYBOOK_ORIGIN_SKETCH = "esquisse"
PLAYBOOK_ORIGINS: frozenset[str] = frozenset(
    {PLAYBOOK_ORIGIN_GENERATED, PLAYBOOK_ORIGIN_TEMPLATE, PLAYBOOK_ORIGIN_SKETCH}
)

#: Who **composed** the proposed team (#1159, docs/41). `modele`: the model read the
#: project's findings and what the person said, and chose the roles — the main
#: mechanism. `regles`: the rules of the five role templates, the fallback when
#: composition by the model did not succeed, **said as such** with its cause. Empty:
#: the question does not arise (a reinforcement, whose position comes from the plan,
#: #1227).
COMPOSITION_ORIGIN_MODEL = "modele"
COMPOSITION_ORIGIN_RULES = "regles"


def new_id() -> str:
    """A fresh team proposal identifier, of the form `equ-<8 hex>`."""
    return f"{ID_PREFIX}{uuid.uuid4().hex[:8]}"


def maestro_browser() -> dict[str, str]:
    """What the person reads about Maestro's browser (#1532) — its name, and its use.

    Served **once**, next to the roles, and not copied by the screen: the face of the
    card and each role's line say the same thing because they read the same sentence
    (variant B retained on #1532, reservation 1 of the fresh look). The source is
    `maestro.agents.navigateur`, which shares it with the agent's playbook.
    """
    return {"libelle": BROWSER_NAME, "usage": ", ".join(CE_QU_IL_FAIT)}


@dataclass(frozen=True)
class ProposedPermission:
    """A tool, the tier proposed for it, and **the reason** for that tier.

    The reason is not decoration: it is the ticket's criterion. A permission policy
    is reread months later, in front of an agent that has just done something
    unexpected, and an `ask: {"Bash": "auto"}` without its reason does not say *who*
    decided it nor *on what*. So it names, when there is one, the piece of the
    project that pointed to it.

    `decider` only makes sense on the `ask` tier (#586) and is `None` elsewhere: an
    `allow` passes without anyone deciding, a `deny` refuses without anyone deciding.
    On an `ask`, its absence means `humain` — *an unspecified tier escalates, it does
    not self-approve* (`DECIDEUR_DEFAUT`).

    `scope` (#1226) says **where** this tier applies, and only makes sense on `ask`
    for the same reason. Empty: everywhere. It is what makes it possible to propose a
    permission to **execute in the project** without proposing a permission to
    execute anywhere — and the reason, right next to it, is what lets the person who
    validates the team decide on it.
    """

    tool: str
    tier: str
    reason: str
    decider: Decideur | None = None
    scope: str = ""

    def __post_init__(self) -> None:
        # Frozen: same precautions as `PolitiqueOutils.__post_init__`. We normalise
        # rather than refuse, except on the tier — an unknown tier would produce a
        # policy that `PermissionStore` would refuse to read back, that is a proposal
        # that could only be invalidated at creation time.
        if self.tier not in TIERS:
            raise ValueError(
                f"cran d'autorisation inconnu : {self.tier!r} "
                f"(attendus : {', '.join(TIERS)})."
            )
        if self.tier != "ask" and self.decider is not None:
            raise ValueError(
                f"un décideur ne se pose que sur le cran « ask » (reçu sur "
                f"« {self.tier} » pour {self.tool!r})."
            )
        if self.tier != "ask" and self.scope:
            raise ValueError(
                f"une portée borne un arbitrage : elle ne se pose que sur le cran "
                f"« ask » (reçue sur « {self.tier} » pour {self.tool!r})."
            )
        if self.scope and self.scope not in PORTEES:
            raise ValueError(
                f"portée d'autorisation inconnue : {self.scope!r} "
                f"(attendues : {', '.join(PORTEES)})."
            )

    @property
    def effective_decider(self) -> Decideur | None:
        """The decider that will apply — `humain` by default on an `ask`."""
        if self.tier != "ask":
            return None
        return self.decider if self.decider is not None else DECIDEUR_DEFAUT

    def to_dict(self) -> dict[str, Any]:
        """The permission as JSON — the **effective** decider, never its absence.

        Serving `None` on an `ask` would force every screen to know the default in
        order to display it, that is to hold a second time a rule that
        `maestro.decideur` already carries.
        """
        decider = self.effective_decider
        return {
            "outil": self.tool,
            "cran": self.tier,
            "decideur": str(decider) if decider is not None else None,
            "portee": self.scope,
            "raison": self.reason,
        }


@dataclass(frozen=True)
class AttachedSkill:
    """A skill from the project's tooling that this role attaches.

    Copied from the `Entree` that recommended it (`maestro.outillage.modele`) rather
    than described again: `path` and `state` are its own, to the character. `state`
    matters — a `deja-present` skill is already in the project, an `a-generer` one
    only exists once the tooling is written (#1033), and a role attached to a skill
    that does not exist yet is not an error but a marching order. That holds only
    **in the proposal**: at creation, only what the project carries on its disk is
    attached (`observed_skills`, #1212).

    `reason` says why **this role** attaches it; the reason for the skill itself
    stays the recommendation's, and is not copied here.
    """

    name: str
    path: str
    state: str
    reason: str
    commands: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """The attached skill as JSON."""
        return {
            "nom": self.name,
            "chemin": self.path,
            "etat": self.state,
            "raison": self.reason,
            "commandes": list(self.commands),
        }


@dataclass(frozen=True)
class ProposedRole:
    """A role the analysis proposes to recruit — never a created agent.

    `name` is the slug of the definition #1040 creates, `role_template` the frozen
    role it derives from (docs/37 §4.1). The two **differ**, and it is not a whim:
    `playbook_outille` (#1037) returns the package's document for any name in
    `roles_du_code()`, so a definition named `developpeur` would see its playbook
    **written for this project** hidden by the code's one. The role template stays
    named, the lineage can be read, and the proposed playbook is the one that will
    apply.

    `justification` is **the location in the project** that makes this role exist —
    the file read, not a sentence (same discipline as `Entree.justification`).
    `instances_reason` answers the other question nobody asks out loud: *why two, and
    not one?*

    `tools` says what this role will have **in hand**: the tools of its tooled
    runtime (#1037). It is not a permission — it is what permissions apply to —, and
    the two are kept apart on purpose: a policy that says "Bash: auto" without knowing
    what else the agent holds cannot be judged.

    `modele` and `effort` are not here: a project's analysis says nothing about the
    model a role should work with, and proposing one would be inventing. A definition
    without that setting takes the executors' default model, and that is exactly what
    we mean.

    `browser` (#1532) says whether this role receives **Maestro's browser**
    (`maestro.agents.navigateur`): a project that builds a web interface gives it to
    the roles that write, design or check its rendering, so that they do not build
    their own bench. It is **one more** tool in the role's hands, mounted for each
    task; it does not appear in `tools`, which states the tooled profile of its
    runtime (#1037) and which nothing here changes.
    """

    name: str
    role: str
    role_template: str
    competencies: tuple[str, ...]
    reason: str
    instances: int
    instances_reason: str
    playbook: str
    playbook_origin: str
    playbook_reason: str
    intent: str
    justification: Piece | None = None
    tools: tuple[str, ...] = ()
    skills: tuple[AttachedSkill, ...] = ()
    permissions: tuple[ProposedPermission, ...] = ()
    browser: bool = False

    def policy(self) -> PolitiqueOutils:
        """The proposed permissions turned into a `PolitiqueOutils` (#110).

        The **only** path for this translation, and that is what guarantees that what
        #1040 persists is exactly what the user validated: a second translation,
        written in the screen or in the creation route, would end up no longer saying
        the same thing as this one.

        Entries keep the proposal's order. It matters on `ask`, where the **first**
        entry covering a tool gives its decider (`PolitiqueOutils.decide`).

        ⚠ A proposal without an `allow` entry returns an **empty** `allow` list, that
        is an **open** one — and that is intended (see `maestro.team`). Do not
        "complete" this list with the role's `tools`: a non-empty `allow` list is
        *closed*, and closing this one would refuse Maestro's in-process channels
        (`mcp__maestro__…`: asking a question, recording a decision, reporting a
        blocker) that a project's analysis cannot enumerate.
        """
        return PolitiqueOutils(
            allow=tuple(a.tool for a in self.permissions if a.tier == "allow"),
            ask=tuple(
                EntreeArbitrage(
                    a.tool, a.effective_decider or DECIDEUR_DEFAUT, a.scope
                )
                for a in self.permissions
                if a.tier == "ask"
            ),
            deny=tuple(a.tool for a in self.permissions if a.tier == "deny"),
        )

    def to_dict(self) -> dict[str, Any]:
        """The proposed role as JSON — the shape served by the API.

        `politique` travels **next to** the detailed permissions, not in their place:
        the screen shows the reasons one by one (#1040), creation only needs the
        policy. Both come from the same source, so they cannot contradict each other.
        """
        return {
            "nom": self.name,
            "role": self.role,
            "gabarit": self.role_template,
            "competences": list(self.competencies),
            "raison": self.reason,
            "justification": (
                self.justification.to_dict() if self.justification is not None else None
            ),
            "instances": self.instances,
            "raison_instances": self.instances_reason,
            "outils": list(self.tools),
            "navigateur": self.browser,
            "playbook": self.playbook,
            "playbook_origine": self.playbook_origin,
            "playbook_raison": self.playbook_reason,
            "intention": self.intent,
            "skills": [skill.to_dict() for skill in self.skills],
            "autorisations": [a.to_dict() for a in self.permissions],
            "politique": self.policy().to_dict(),
        }


@dataclass(frozen=True)
class ExcludedRole:
    """A role the analysis **chose not to** propose, with its reason.

    Two families, and both are useful: what the project does not justify (no SQL file
    observed) and what is **never** a team member by decision — the orchestrator,
    which is Maestro and which recruits (docs/37 §4.2). Giving them the same shape is
    intended: the question of who is not in the team is asked once, and the answer
    is read in one place.
    """

    name: str
    role: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """The excluded role as JSON."""
        return {"nom": self.name, "role": self.role, "raison": self.reason}


@dataclass(frozen=True)
class TeamProposal:
    """The team a project calls for — proposed, never created (#1039).

    `source` is the provenance fragment of the tooling manifest (docs/38 §4.1), as
    `Analyse.source_manifeste()` or `source_manifeste_des_choix()` returns it: it is
    what tells, six months later, whether this team comes from the analysis of an
    existing project or from the answers given on a new one. It is **copied**, never
    rewritten — a second spelling of this fragment would end up diverging.

    `summary` fits on one line, for the reason that already holds for an analysis's
    summary: it is the sentence one rereads next to a team whose origin one wonders
    about.

    `composition_origin` and `composition_reason` (#1159) say **who** chose the roles
    — the model, or the role templates' rules as a fallback — and, for the fallback,
    why. A team composed by rules because the model did not answer is not worth a
    team composed for the project's need, and nothing would tell them apart on
    reading: that is the purpose of `playbook_origin`, one level up.
    """

    id: str
    project_id: str
    made_at: str
    summary: str = ""
    roles: tuple[ProposedRole, ...] = ()
    excluded: tuple[ExcludedRole, ...] = ()
    source: dict[str, Any] | None = None
    composition_origin: str = ""
    composition_reason: str = ""

    @property
    def total_instances(self) -> int:
        """The number of agents this team would put to work in parallel."""
        return sum(role.instances for role in self.roles)

    def to_dict(self) -> dict[str, Any]:
        """The whole proposal as JSON — the shape served by the API.

        `cree` and `validation` are not settings: they are the ticket's two promises
        made **readable by the caller**, on the pattern of
        `lecture_seule`/`execution` in `Bornes.to_dict()`. They are constant because
        they are not negotiable — no call can change them, and a proposal that
        created anything would not be a proposal set up differently, it would be
        something else (#1040).
        """
        return {
            "proposition": PROPOSAL_VERSION,
            "id": self.id,
            "projet_id": self.project_id,
            "faite_le": self.made_at,
            "resume": self.summary,
            "source": self.source,
            "composition": {
                "origine": self.composition_origin,
                "raison": self.composition_reason,
            },
            "navigateur_de_maestro": maestro_browser(),
            "roles": [role.to_dict() for role in self.roles],
            "ecartes": [excluded.to_dict() for excluded in self.excluded],
            "instances_total": self.total_instances,
            "cree": False,
            "validation": "requise",
        }
