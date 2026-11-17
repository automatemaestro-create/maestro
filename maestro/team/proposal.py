"""From findings and tooling to the proposed team — role by role (#1039).

The second half of the package: `maestro.team.role_templates` says what Maestro knows how
to propose, this module deduces what **this project** calls for. It does not touch the
disk — it receives only `Constats`, a `Recommandation` and the questionnaire's answers, if
any —, and that is what makes it testable on fabricated findings, with no real project and
no model provider.

## The four rules that decide

1. **Nothing without its place.** Each role carries the project's `Piece` that makes it
   exist — the file read, not a sentence. It is the rule of the tooling recommendation
   (#1030), and it holds here for the same reason: a team is challenged by opening the
   files that designated it.
2. **What is not proposed is named, with its reason.** `excluded` carries the roles no
   finding justifies *and* the orchestrator, which is never a member of the team
   (docs/37 §4.2). Without this list, "no database role" would read as a failure of
   Maestro rather than as a fact of the project.
3. **Each permission carries its reason, and the `auto` tier derives from the
   project.** It is the ticket's second criterion (#716, docs/37 §4.3): Maestro *drafts*
   the policy, the user *decides* it (#1040). An `auto` tier proposed without its reason
   would be a decision taken with nobody.
4. **Nothing is created.** No repository is opened for writing here, and
   `TeamProposal.to_dict()` says so in plain words.

## What this module does not decide

**The text of playbooks.** It sets the **template**'s playbook and the `intent` from which
one will be written for this project, and composes the project facts that playbook will not
contradict (`project_facts`, #1521); the writing is #257's mechanism
(`GenerateurDefinitionAgent`), called by `maestro.controltower.equipe` — the only layer that
knows a model provider. A role whose generation fails therefore keeps its template's
playbook, **and says so** (`playbook_origin`): a whole team lost because a quota ran out
would be a far worse answer than a generic playbook announced as such.

⚠ **The execution regime is not in the intent** (#1405). Given it, the model that writes
the playbook rephrases it, and can add an exception the policy does not have (on p5, it
did). Maestro writes the regime itself, from the policy, at the end of each task's system
prompt (`maestro.agents.regime_d_execution`): this module sets the tier
(`role_permissions`), execution tells the agent, and the model writes only the trade.

**The model of each role.** A project's analysis says nothing about the model a role should
work with. A definition with no setting takes the executors' default model, and that is
exactly what we mean.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime

from maestro.agents.fiche_outillee import profil_outille
from maestro.decideur import Decideur
from maestro.outillage.modele import Constats, Entree, Recommandation
from maestro.outillage.questionnaire import Choix
from maestro.outillage.recommandation import SKILL_PAR_USAGE
from maestro.portee import PORTEE_PROJET
from maestro.team.gap import MissingRole
from maestro.team.model import (
    COMPOSITION_ORIGIN_RULES,
    PLAYBOOK_ORIGIN_TEMPLATE,
    PLAYBOOK_ORIGINS,
    AttachedSkill,
    ExcludedRole,
    ProposedPermission,
    ProposedRole,
    TeamProposal,
    new_id,
)
from maestro.team.role_templates import (
    ROLE_TEMPLATES,
    SINGLE_INSTANCE_REASON,
    Justification,
    RoleTemplate,
    interface_observed,
)

#: The orchestrator, excluded **by name** from every team. It has no role template
#: (`maestro.team.role_templates`) and will not have one: it is Maestro, and it is the one
#: that recruits (docs/37 §4.2). Excluding it silently would suggest that no project has
#: one — when every project has one, and it is simply not recruited.
EXCLUDED_ORCHESTRATOR = ExcludedRole(
    name="orchestrateur",
    role="Orchestrateur",
    reason=(
        "l'orchestrateur n'est pas un membre de l'équipe : c'est Maestro, présent "
        "dans tout projet, et c'est lui qui recrute. Il n'y a donc rien à créer "
        "pour lui, ni playbook ni autorisations de projet"
    ),
)

#: The tool whose tier is **derived from the project** rather than set. It is the only one
#: of `DEFAULT_TOOLS` that executes something, so the only one for which the question
#: "should every call be arbitrated?" really arises — and it is also the only one that the
#: five policies shipped with the repository (`core/permissions/`) all cite.
EXECUTION_TOOL = "Bash"

#: The origin of a command the **project declares** (as opposed to its manager's
#: convention). Named so that `_execution_tier` reads what makes it decidable in advance:
#: someone wrote it in the project, and the analysis read it there.
DECLARED_ORIGIN = "declaree"


def propose_team(
    findings: Constats,
    recommendation: Recommandation,
    *,
    project_id: str = "",
    choices: Sequence[Choix] = (),
    taken_names: Sequence[str] = (),
    source: Mapping[str, object] | None = None,
) -> TeamProposal:
    """The team this project calls for — proposed, never created.

    `findings` and `recommendation` come from the **same** pair as the tooling (#1030 on an
    existing project, #1031 on a new one): there are therefore not two paths of "what this
    project needs" to keep in agreement, and a team derived from answers attaches the same
    skills as a team derived from an analysis.

    `choices` is used only for what the findings do not carry — the project's **nature**,
    which says a web application will have screens before any file has proved it. Empty on
    an analysed project, and that is the nominal case.

    `taken_names` are the names a definition cannot carry in this project: the proposed name
    is made free before leaving, so that a validation (#1040) does not run into a collision
    the user did not author.

    `source` is the provenance fragment of the tooling manifest (docs/38 §4.1), taken as
    is — never rewritten.
    """
    answers = {c.cle: c.valeur for c in choices}
    skills = _recommended_skills(recommendation)
    interface = interface_observed(findings, answers)
    taken = [*taken_names]
    roles: list[ProposedRole] = []
    excluded: list[ExcludedRole] = [EXCLUDED_ORCHESTRATOR]
    for template in ROLE_TEMPLATES:
        justification = template.need(findings, answers)
        if justification is None:
            excluded.append(_excluded_role(template, findings))
            continue
        name = _free_name(template.name, taken)
        taken.append(name)
        roles.append(
            _role(
                template,
                name,
                justification,
                findings,
                skills,
                browser=interface and template.watches_screen,
            )
        )
    return TeamProposal(
        id=new_id(),
        project_id=project_id,
        made_at=_now(),
        summary=_summary(roles, excluded),
        roles=tuple(roles),
        excluded=tuple(excluded),
        source=dict(source) if source is not None else None,
        composition_origin=COMPOSITION_ORIGIN_RULES,
    )


def propose_reinforcement(
    findings: Constats,
    recommendation: Recommandation,
    gap: MissingRole,
    reason: str,
    *,
    project_id: str = "",
    taken_names: Sequence[str] = (),
    source: Mapping[str, object] | None = None,
) -> TeamProposal:
    """The **single role** a team lacks to execute a plan (#1227).

    The counterpart of `propose_team` on the other question. There: *what team does this
    project call for?*, and the answer is read in the findings. Here: *who is missing from
    the team it already has?*, and the answer is computed elsewhere
    (`maestro.team.gap.plan_gap`) — on the **plan**, not on the project.

    Hence the only substantive difference between the two: the `reason` is **given** instead
    of being derived from a finding. It is the module's rule 1 kept, not bypassed — "nothing
    without its place" requires the role to carry what makes it exist, and what makes it
    exist here is the plan just paid for, which only the caller knows. The `Piece` stays
    `None` for the same reason: no file of the project designates this role, and inventing
    one would be worse than having none (see `Justification`).

    Everything else is **identical**: same skills attached by usage, same permissions derived
    from the project with their reason, same instances, same intent for #257, same name made
    free. That is what makes a role recruited mid-run a full-fledged role, and not a
    cut-rate definition.

    `excluded` carries the orchestrator **and not** the roles the team does not call for
    here: that would not be the same list as a whole-team proposal's (it would say nothing
    of the roles already in place), and serving it would suggest they had been examined.
    Only the orchestrator stays, because its reason — *it is not a member of the team* — is
    true whatever the moment (docs/37 §4.2).

    Raises `ValueError` on a gap with no role template: there is then no position to
    propose, and the caller knows it before calling (`PlanGap.recruitable`). Proposing
    something here would amount to inventing a role.
    """
    template = _template_for(gap)
    skills = _recommended_skills(recommendation)
    name = _free_name(template.name, taken_names)
    role = _role(
        template,
        name,
        Justification(reason=reason),
        findings,
        skills,
        # Recruited mid-run, the project has its files: they are what says whether it has
        # an interface, as for the rest of this role.
        browser=template.watches_screen and interface_observed(findings, {}),
    )
    return TeamProposal(
        id=new_id(),
        project_id=project_id,
        made_at=_now(),
        summary=_summary((role,), (EXCLUDED_ORCHESTRATOR,)),
        roles=(role,),
        excluded=(EXCLUDED_ORCHESTRATOR,),
        source=dict(source) if source is not None else None,
    )


def _template_for(gap: MissingRole) -> RoleTemplate:
    """The role template `gap` designates — raises if the gap names none.

    Resolved by its **template name** (`RoleTemplate.name`, the proposed slug), which is
    exactly what `missing_role` put there: a second equivalence table would be one more
    table to keep in agreement with the catalogue's order.
    """
    if gap.role_template is None:
        raise ValueError(
            f"aucun gabarit ne couvre {', '.join(gap.competencies)} : il n'y a "
            "pas de rôle à proposer, et en inventer un le ferait lire comme un "
            "rôle du catalogue."
        )
    for template in ROLE_TEMPLATES:
        if template.name == gap.role_template:
            return template
    raise ValueError(
        f"gabarit inconnu : {gap.role_template!r} (attendus : "
        f"{', '.join(t.name for t in ROLE_TEMPLATES)})."
    )


def _role(
    template: RoleTemplate,
    name: str,
    justification: Justification,
    findings: Constats,
    skills: Mapping[str, Entree],
    *,
    browser: bool = False,
) -> ProposedRole:
    """The role proposed for this template: its skills, its instances, its permissions.

    `browser` (#1532): whether this role receives Maestro's browser. The rules give it to
    the templates that watch the screen when the project has an interface
    (`RoleTemplate.watches_screen`, `interface_observed`); the person removes it in words.
    """
    tools = profil_outille(template.agent).outils
    attached = skills_by_usage(template.usages, skills)
    permissions = role_permissions(tools, template.usages, findings, attached)
    instances, instances_reason = (
        template.instances_for(findings)
        if template.instances_for is not None
        else (1, SINGLE_INSTANCE_REASON)
    )
    return ProposedRole(
        name=name,
        role=template.role,
        role_template=template.agent_name,
        competencies=template.competencies,
        reason=justification.reason,
        justification=justification.evidence,
        instances=instances,
        instances_reason=instances_reason,
        playbook=template.fallback_playbook(),
        playbook_origin=PLAYBOOK_ORIGIN_TEMPLATE,
        playbook_reason=template_playbook_reason(template.agent_name),
        intent=role_intent(template.role, findings),
        tools=tools,
        skills=attached,
        permissions=permissions,
        browser=browser,
    )


def template_playbook_reason(template: str) -> str:
    """What is said of a template's playbook **before** its writing has been attempted."""
    return (
        f"playbook du gabarit « {template} » : sa rédaction pour ce projet "
        "n'a pas encore eu lieu"
    )


def _recommended_skills(recommendation: Recommandation) -> dict[str, Entree]:
    """The tooling's `skill`-type entries, indexed by name.

    The other types (`instructions`, `pont`, `script`) are not skills: a role does not
    "attach" an `AGENTS.md`, it reads it like everyone else (`outillage_du_projet`, #1032),
    and a script is already called by the skill that wraps it.
    """
    return {
        entry.nom: entry for entry in recommendation.entrees if entry.type == "skill"
    }


def skills_by_usage(
    usages: Sequence[tuple[str, str]], skills: Mapping[str, Entree]
) -> tuple[AttachedSkill, ...]:
    """The project's skills a role attaches — by usage, never by name.

    `usages` carries, for each usage, the reason why **this** role attaches it: a
    template's (`RoleTemplate.usages`), or those a role composed by the model named
    (`maestro.team.composition`, #1159).

    A usage the recommendation excluded (no command observed) produces **nothing**: the
    role is not attached to a skill that will not exist, and the recommendation already
    carries the reason for its absence.
    """
    attached: list[AttachedSkill] = []
    for usage, reason in usages:
        skill_name = SKILL_PAR_USAGE.get(usage, ("", ""))[0]
        entry = skills.get(skill_name)
        if entry is None:
            continue
        attached.append(
            AttachedSkill(
                name=entry.nom,
                path=entry.chemin,
                state=entry.etat,
                reason=reason,
                commands=entry.commandes,
            )
        )
    return tuple(attached)


def role_permissions(
    tools: Sequence[str],
    usages: Sequence[tuple[str, str]],
    findings: Constats,
    skills: Sequence[AttachedSkill] = (),
) -> tuple[ProposedPermission, ...]:
    """The permissions proposed for this role — each with its reason.

    They depend only on what the role **holds** (`tools`) and on the usages it attaches: a
    role composed by the model outside the templates (#1159) therefore receives them exactly
    like a template role, by the same rule. `skills` are those it attaches
    (`skills_by_usage`): those the project carries say where its commands are written
    (#1591).

    **No `allow` entry, and that is a decision.** A non-empty `allow` list is *closed*
    (`PolitiqueOutils`): filling it with the profile's tools would refuse everything else,
    starting with Maestro's in-process channels (`mcp__maestro__…`: asking a question
    (#1023), recording a decision (#1024), reporting a blocker, writing to a peer) — which a
    project's analysis cannot enumerate, and whose refusal would make the agent mute exactly
    where the milestone gives it a voice. The five policies shipped with the repository
    (`core/permissions/`) all open their `allow` for this reason; what the role holds is
    read in `ProposedRole.tools`, which is not a policy.

    What remains is the only question a project lets us settle: the **execution** tool. It
    is also the only one that arises — the profile's other tools read and write in a
    workspace already bounded by the write boundary (#839).
    """
    if EXECUTION_TOOL not in tools:
        return ()
    return (_execution_tier(usages, findings, skills),)


#: What the "project" scope **keeps** for the person, said once and reused in both reasons.
#: The three families are those of `maestro.portee`, and they are not paraphrased from one
#: case to the other: what leaves the folder, what destroys what the agent did not produce
#: (#1226), and what stops programs without designating them (#1502).
WHAT_STAYS_YOURS = (
    "⚠ Ce que vous gardez : un acte qui **sort du dossier du projet** — un chemin "
    "au-dessus ou à côté, une installation ailleurs sur la machine —, un acte qui "
    "**effacerait ce que vous aviez posé là** avant lui, et un acte qui **arrêterait "
    "des programmes par leur nom** — tous ceux de ce nom, pas seulement les siens — "
    "vous reviennent, un par un. "
    "C'est vous qui décidez tout cela ici, à froid, et cela se révoque"
)

#: What the scope does not do, said as soon as a reason names commands: it bounds **where**
#: the act lands, never **which** command is launched.
SCOPE_WITHOUT_LIST = (
    "⚠ La portée dit **où** l'acte tombe, pas quelle commande est lancée : elle ne le "
    "borne pas à celles-là."
)


def _execution_tier(
    usages: Sequence[tuple[str, str]],
    findings: Constats,
    skills: Sequence[AttachedSkill] = (),
) -> ProposedPermission:
    """The tier proposed for command execution — and **what it bounds**.

    A role recruited for a project **does its work there without waiting for anyone**:
    running what it writes, testing it, creating its folders, stopping what it launched.
    The tier is therefore `auto`, and its **scope** is the project (#1226,
    `maestro.portee`): what leaves the folder or destroys what the agent did not produce
    goes to a person, one act at a time.

    ⚠ The reason does not count **cleanup** as part of this work (#1401): the execution
    frame tells the agent to leave what can be rebuilt, ignored by `.gitignore`. The gesture
    stays allowed by the scope; it is not what is announced of it.

    ⚠ **`auto` rests on a measure, not on a guess.** A role whose project declares no
    command — always the case on a new project — asks for every command otherwise: run
    `96d0c3482649` asked for 14 `Bash` approvals and got 14 (`mkdir`, `python`, `pytest`,
    `python --version`, and cleaning the caches its own executions had just produced), and
    none left the project. [docs/32 §8](../../docs/32-decision-cran-orchestrateur.md)
    states what an approval rate of 1 says — *those acts deserve `auto`* — and what the case
    where the verdict depends on the arguments costs: a **scope**, never an intermediate
    decider.

    What the project separates is therefore not the tier but the **reason**, and this
    module's rule 1 requires it: when the project declares this role's commands, the reason
    names the files where they were read; otherwise it says what it authorises and what it
    keeps, and **where its commands are written** when a skill it attaches is already in the
    project (`skills`, #1591) — the accepted tooling put them there, and claiming none is
    would be false. A permission is challenged by opening the evidence that designated it —
    when there is one.

    ⚠ The reason says so, because it is true: the scope bounds **where** the act lands, not
    **which** command is launched. It is not a list of permitted commands, and keeping quiet
    about it would make the proposal read as more cautious than it is. It is also why no
    role receives a more closed tier here in the name of its trade: what a role must not
    *do* lives in its playbook, which can tell a `SELECT` from a `DROP` — a tier cannot, and
    setting one would block `ls` without preventing anything.
    """
    declared = _declared_commands(usages, findings)
    if not declared:
        written = _project_skills(skills)
        where = (
            f"Ses commandes sont écrites dans l'outillage du projet ({', '.join(written)}). "
            f"{SCOPE_WITHOUT_LIST}"
            if written
            # "of this role": another role may have its own written (#1591).
            else "Aucune commande de ce rôle n'est encore écrite dans le projet, donc "
            "aucune n'a pu être lue : c'est le travail qui vient."
        )
        return ProposedPermission(
            tool=EXECUTION_TOOL,
            tier="ask",
            decider=Decideur.AUTO,
            scope=PORTEE_PROJET,
            reason=(
                "ce rôle travaille dans le dossier de ce projet : il y lance ce "
                "qu'il écrit, le teste, crée les dossiers dont il a besoin et "
                "arrête ce qu'il a lancé — **sans vous demander de trancher "
                f"chaque commande**, qui passent en étant tracées. {where} "
                f"{WHAT_STAYS_YOURS}"
            ),
        )
    return ProposedPermission(
        tool=EXECUTION_TOOL,
        tier="ask",
        decider=Decideur.AUTO,
        scope=PORTEE_PROJET,
        reason=(
            "les commandes de ce rôle sont écrites dans le projet et l'analyse les y "
            f"a lues ({', '.join(declared)}). Il les joue dans le dossier de ce "
            "projet **en étant tracé**, au lieu de vous faire arbitrer chaque "
            f"exécution. {SCOPE_WITHOUT_LIST} {WHAT_STAYS_YOURS}"
        ),
    )


def _project_skills(skills: Sequence[AttachedSkill]) -> tuple[str, ...]:
    """The paths of the attached skills the project **carries** — where its commands are written.

    A `deja-present` skill is in the project: the accepted tooling wrote it there, or the
    person did. It is not a *read* command (`_declared_commands`) — Maestro authorises
    itself nothing from it —, but it is the place one opens to know what the role will run.
    An `a-generer` skill is nowhere yet, and is not named.
    """
    return tuple(dict.fromkeys(s.path for s in skills if s.state == "deja-present"))


def _declared_commands(
    role_usages: Sequence[tuple[str, str]], findings: Constats
) -> tuple[str, ...]:
    """The places in the project where this role's commands were **read**.

    Two sources, and both count: a command the project *declares* (`origine == "declaree"`
    — a `package.json` script, a `Makefile` target) and a project **script** for this usage
    (docs/38 §3.4). A `convention` command does not: it is the detected tool's command,
    which the project writes nowhere — keeping it would pass a guess off as a reading, the
    exact flaw `ORIGINES_COMMANDE` exists to prevent.

    ⚠ **A command Maestro itself wrote in a project skill does not count either** — weighed,
    not assumed (#1102). It is indeed *written in the project*, but nobody decided it
    there: it comes from the same convention, copied one step further. Counting it would
    make Maestro the author of its own permission, when docs/38 §5.1 says that *what
    authorises a tool is not the file's origin, it is a person's gesture* — and the tier
    obtained would not be bounded to that command, a tier bearing on a tool and not on its
    arguments. What an agent needs is not to execute in order to **read** its tooling, and
    that is settled elsewhere, without touching this tier. The reason nonetheless names the
    skill where the command is written (`_project_skills`, #1591): it is a place to open,
    not a reading that would authorise — and hiding that it exists would make the reason
    say that no command is written.

    ⚠ Reading without executing is settled by execution (`maestro.lecture`), not by the
    playbook's text: on a real run, an agent reads through the shell whatever its playbook
    says (#1197). There, a command that only reads suspends nothing.

    ⚠ **What these sources decide is the reason served, not the tier** (#1226). The tier
    does not depend on this survey, because a validated team is already *a person's
    gesture* — it authorises this role to execute **in this project** (`_execution_tier`).
    A permission that names the evidence that designated it is challenged by opening that
    evidence, and that is this module's rule 1. The motive still holds for what it bounds:
    a command Maestro wrote for itself authorises nothing.
    """
    usages = {usage for usage, _ in role_usages}
    places: list[str] = []
    for command in findings.commandes:
        if command.usage in usages and command.origine == DECLARED_ORIGIN:
            places.append(
                f"{command.chemin} ({command.extrait})"
                if command.extrait
                else command.chemin
            )
    places.extend(
        script.chemin for script in findings.dossier_scripts.scripts if script.role in usages
    )
    return tuple(dict.fromkeys(places))


#: The number of competencies an intent names for a composed role (#1159). Bounded because
#: the intent is (`INTENTION_MAX`, #257): beyond it, the end of the sentence — what the
#: project is — would be cut.
INTENT_COMPETENCIES = 5


def role_intent(
    role: str,
    findings: Constats,
    competencies: Sequence[str] = (),
) -> str:
    """The sentence from which #257 writes this role's playbook — *for this project*.

    One sentence, like #257's input: the role, what the project is. Nothing of what the
    playbook must say is written in it — #257's frame asks for that, and saying it again
    here would make a second instruction to keep in agreement.

    ⚠ **The role's skills are not in it** (#1404). Given them, the model that writes the
    playbook copies them — on p5, a "Ton périmètre" section named them —, and creation adds
    its own section (`maestro.team.creation.playbook_with_sections`), which names them with
    their path and commands: the agent would read its list twice. Creation's section is the
    one that says them, because only it knows which ones the project **carries** (#1212);
    what is not given to the model, it does not copy.

    ⚠ **The execution regime is not in it** (#1405). Given it, the model that writes the
    playbook **rephrases** it, and on p5 it added an exception the policy does not have —
    "reaching an external service" waited for a person's agreement —, so that the agent
    made someone wait before an `npm install` that went through on its own. Maestro writes
    the regime itself, from the policy, in each task's system prompt
    (`maestro.agents.regime_d_execution`); what is not given to the model, it does not
    rephrase. #1102's concern is kept: the rule "a command is never the first gesture"
    stays in #257's frame, and the `humain` regime tells it to the agent again at each task.

    It lives **in the proposal** and not only in the call: it is what one rereads to judge a
    playbook that misses the point, and a playbook without the sentence it was born from
    cannot be judged (`DefinitionProposee` keeps its own for the same reason).

    `competencies` serves only roles **composed by the model** (#1159): a label like
    "Sécurité" does not tell #257 what the trade bears on, whereas a template's playbook
    already has its document. Empty — the template case —, nothing is added to the
    sentence, to the character.
    """
    languages = ", ".join(language.nom for language in findings.langages[:3])
    trade = ", ".join(competencies[:INTENT_COMPETENCIES])
    parts = [
        f"Un agent « {role} »",
        f" ({trade})" if trade else "",
        " pour un projet",
        f" écrit en {languages}" if languages else "",
        ". Il travaille dans le dossier du projet.",
    ]
    return "".join(parts)


def project_facts(
    findings: Constats, choices: Sequence[Choix] = (), *, request: str = ""
) -> str:
    """What the person said of the project and what it shows — a playbook does not contradict it.

    The counterpart of `role_intent`, and not a second intent: the intent says **the
    role**, these facts say **the project**, for all its roles. Without them, the model that
    writes a playbook knows only a project's language — on the assessment walkthrough, it
    imposed `unittest` and banned pytest for the tester of someone who had answered "Les
    tests avec pytest".

    The answers are taken **as is**, typed words included: that is where a restriction is
    bounded ("la bibliothèque standard seulement pour le générateur"), and a paraphrase
    would extend it. Without answers — an analysed project —, the commands the analysis
    read say its conventions; with answers, the commands are drawn from them, and saying
    them again would state the same fact twice.

    `request` is the sentence of a correction in words: a role it adds follows what it has
    just set. Empty when there is nothing to say — the writer then receives the prompt
    unchanged, to the character.
    """
    lines = [
        f"- {c.cle} : {' '.join(c.valeur.split())}"
        + (f" — {' '.join(c.pour.split())}" if c.pour else "")
        for c in choices
        if c.valeur.strip()
    ]
    if not choices:
        lines += [
            f"- commande de « {c.usage} » : `{c.commande}` ({c.chemin})"
            for c in findings.commandes
        ]
    sentence = " ".join(request.split())
    if sentence:
        lines.append(f"- ce que la personne vient de demander sur l'équipe : {sentence}")
    return "\n".join(lines)


def _excluded_role(template: RoleTemplate, findings: Constats) -> ExcludedRole:
    """The role not proposed, with what the project did not show.

    The reason names **what is missing**, not what Maestro did not do: otherwise, "no
    database role" would read as an oversight of the analysis rather than as a fact of the
    project. It also recalls that the analysis has bounds — a truncated project can carry
    what the analysis did not see.

    ⚠ It names **no gesture** (#1159). It is served on two surfaces (the team step, the
    chat card) that do not offer the same controls, so a gesture promised here ("you can
    add it at validation") would be false on at least one of them. The gesture is named
    next to the control that carries it, by the screen.
    """
    return ExcludedRole(
        name=template.name,
        role=template.role,
        reason=(
            f"rien dans les bornes de l'analyse ne justifie un rôle « {template.role} » : "
            f"{_what_is_missing(template)}"
        ),
    )


def _what_is_missing(template: RoleTemplate) -> str:
    """What, concretely, was not found for this template.

    Derived from the template rather than restated per role: the need rule is the source,
    and a sentence written next to it would end up describing a rule that has since
    changed.
    """
    return GAP_BY_TEMPLATE.get(
        template.agent_name, f"aucun constat ne désigne « {template.role} »"
    )


#: What each need rule looked for, said plainly. Indexed by the template's agent
#: (`RoleTemplate.agent_name`), not by the proposed role's name, because the rule is what
#: is at stake, not the name of the definition that would have been created.
GAP_BY_TEMPLATE: dict[str, str] = {
    "bdd": (
        "aucun fichier SQL n'a été vu, donc ni schéma ni migration à tenir"
    ),
    "devops": (
        "ni intégration continue, ni commande de construction, ni forge constatée"
    ),
    "designer": (
        "aucune feuille de style ni composant d'écran constaté, et le projet n'a pas "
        "été déclaré comme une application web"
    ),
    "qa": (
        "aucune commande de test, de style, de format ni de types constatée"
    ),
}


def _free_name(base: str, taken: Sequence[str]) -> str:
    """The definition name `base`, suffixed while it is already taken (`dev`, `dev-2`…).

    The collision check is **not** a validation: the repository stays the only judge at
    creation (#1040), and an agent created between this answer and the validation would
    make it stale anyway. What is avoided is the frequent and avoidable case — proposing
    "dev" to a project that already has one.
    """
    occupied = {name.casefold() for name in taken}
    if base.casefold() not in occupied:
        return base
    for suffix in range(2, 100):
        candidate = f"{base}-{suffix}"
        if candidate.casefold() not in occupied:
            return candidate
    return base


def _summary(roles: Sequence[ProposedRole], excluded: Sequence[ExcludedRole]) -> str:
    """The sentence one rereads next to a team when wondering where it comes from.

    One line, and only what was proposed — the counterpart of
    `maestro.outillage.analyse.resume`. The excluded roles are **counted**, not named:
    their list is right below, with their reasons, and copying it would make the summary a
    second inventory.
    """
    if not roles:
        return "aucun rôle proposé"
    named = ", ".join(
        f"{role.role} ×{role.instances}" if role.instances > 1 else role.role
        for role in roles
    )
    instances = sum(role.instances for role in roles)
    return (
        f"{named} — {len(roles)} rôle(s), {instances} instance(s) ; "
        f"{len(excluded)} rôle(s) écarté(s)"
    )


def with_playbook(
    role: ProposedRole, playbook: str, *, origin: str, reason: str
) -> ProposedRole:
    """The same role, its playbook replaced — the only path for that replacement.

    Pure, because the writing is not: `maestro.controltower.equipe` calls #257's
    mechanism, and that layer is the only one that knows a model provider. This verb is
    what it reports here, and it **validates the origin** — an origin outside
    `PLAYBOOK_ORIGINS` would serve a proposal whose playbook's provenance could not be told,
    which is precisely the information this field exists to carry.
    """
    if origin not in PLAYBOOK_ORIGINS:
        raise ValueError(
            f"origine de playbook inconnue : {origin!r} "
            f"(attendues : {', '.join(sorted(PLAYBOOK_ORIGINS))})."
        )
    if not playbook.strip():
        raise ValueError(
            f"playbook vide proposé pour le rôle {role.name!r} : un prompt système "
            "vide n'a aucun sens."
        )
    return replace(
        role,
        playbook=playbook,
        playbook_origin=origin,
        playbook_reason=reason,
    )


def _now() -> str:
    """A proposal's timestamp (ISO 8601, UTC, to the second)."""
    return datetime.now(tz=UTC).isoformat(timespec="seconds")
