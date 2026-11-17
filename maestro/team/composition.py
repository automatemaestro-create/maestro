"""The team the model composes for the project's **real need** (#1159, docs/41).

[docs/41 §6](../../docs/41-decision-maestro-juge-il-ne-bride-pas.md) replaces the
derivation of #1039: five templates retained by fixed rules **never** propose a mobile,
machine-learning, security or documentation role, nor the data role on a new project.
This module is the **pure** half of what replaces them: it writes what the model is
asked, reads what it answers, and **verifies** every element before it becomes a
proposed role. The call itself lives in `maestro.controltower.equipe`
(`CompositeurEquipe`), the only layer that knows a provider — the boundary that
`maestro.team.proposal` already holds for playbooks.

## Understand, propose, accept correction, verify

The pattern of docs/41 §3, in four verbs and two requests:

- **compose** (`composition_prompt` → `propose_composed_team`): the model receives
  what the analysis found (or what the person answered, on a new project), the skills
  the tooling recommends and the templates **as material**; it names the roles *this*
  project needs, each with its reason;
- **correct** (`correction_prompt` → `correct_team`): the person says what is missing
  in their own words — « ajoute quelqu'un pour la sécurité » — and the model returns
  the roles to add, those to remove or to restore, and a sentence that answers them.

Both requests share **a single frame** (`COMPOSITION_INSTRUCTIONS`) and a single
response contract: what a role is does not change depending on whether one composes or
corrects, and two contracts would end up no longer saying the same thing.

## What execution verifies, and what it does with a deviation

The model proposes, the code settles what can be settled without judgement (docs/41 §3:
*« le modèle propose, l'exécution tranche »*). No deviation fails the whole
composition; each one has its own handling, and that handling **is stated**:

| What the model writes | What is done with it |
| --- | --- |
| a role with no label or no reason | it is not proposed, and `composition_reason` names it |
| a template that does not exist | no lineage: the role keeps a playbook sketch |
| a skill the tooling does not recommend | it is not attached |
| evidence the analysis has not read | the role keeps its reason, not the invented file |
| instances outside 1 to `MAX_CREATED_INSTANCES` | one instance, and the reason says so |
| the orchestrator | never recruited: it is Maestro (docs/37 §4.2) |
| a name that is taken, reserved or already shown | made free by a suffix |
| competencies in prose | reduced to the tags routing can match |
| a browser that is not a boolean | the role does not receive it (#1532) |
| a playbook to rewrite outside the shown team, or with no instruction | not rewritten (#1521) |

A response that leaves **no** verified role raises `UnreadableComposition`: it is not
an empty team, it is an unusable response — and the service then falls back on the
template rules, saying so.

## Why JSON, here

The #257 generator sets JSON aside for a contract of four flat fields and a long
Markdown document (`maestro.controltower.generation_agent`). Here it is the reverse: a
**list** of short objects, with no multi-line document. That is the shape JSON serves
best, and the one a header-based format would serve worst.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from maestro.agents.catalog import MODELE_EXECUTANT_DEFAUT, Agent
from maestro.agents.fiche_outillee import profil_outille
from maestro.agents.playbook_du_code import registre, socle
from maestro.agents.store import NOMS_RESERVES
from maestro.outillage.modele import Constats, Entree, Piece, Recommandation
from maestro.outillage.questionnaire import Choix
from maestro.outillage.recommandation import SKILL_PAR_USAGE
from maestro.team.creation import MAX_CREATED_INSTANCES
from maestro.team.model import (
    COMPOSITION_ORIGIN_MODEL,
    PLAYBOOK_ORIGIN_GENERATED,
    PLAYBOOK_ORIGIN_SKETCH,
    PLAYBOOK_ORIGIN_TEMPLATE,
    ExcludedRole,
    ProposedRole,
    TeamProposal,
    new_id,
)
from maestro.team.proposal import (
    EXCLUDED_ORCHESTRATOR,
    _free_name,
    _now,
    _recommended_skills,
    _summary,
    role_intent,
    role_permissions,
    skills_by_usage,
    template_playbook_reason,
)
from maestro.team.role_templates import ROLE_TEMPLATES, SINGLE_INSTANCE_REASON, RoleTemplate

#: How much a correction request may weigh. One sentence, like the intent of #257
#: (`INTENTION_MAX`) and for the same reason: an unbounded field becomes a prompt
#: injection channel of unlimited volume. The refusal is plain rather than truncated.
MAX_REQUEST = 500

#: The number of roles one response may propose at once. Beyond it, this is no longer
#: a team one reviews line by line before validating it: the bound keeps the proposal
#: **reviewable**, it does not decide the size of a team — the person adds what they
#: want afterwards, one role at a time.
MAX_ROLES = 12

#: The name no proposed role bears, whatever the model writes: the orchestrator is
#: Maestro, and it is Maestro that recruits (docs/37 §4.2).
_ORCHESTRATOR_NAME = EXCLUDED_ORCHESTRATOR.name

#: What is said of a template the model did not retain without giving the reason.
#: A fact — it is the model's judgement on this project —, and no action: the screen
#: that offers to add a role says so next to its control (#1159).
NOT_RETAINED_REASON = (
    "l'analyse d'équipe ne l'a pas retenu pour ce projet : rien de ce qu'elle a lu, ni "
    "de ce que vous avez dit, ne l'appelle"
)

_INSTRUCTIONS = """\
Tu composes l'équipe d'agents IA qui travaillera sur un projet, pour Maestro, un
orchestrateur qui répartit du travail entre des agents autonomes. Chaque rôle que tu
proposes deviendra un agent — mais rien n'est créé sans que la personne l'ait relu et
validé.

On te donne ce que l'analyse du projet a constaté (ou, pour un projet neuf, ce que la
personne a répondu), les skills que l'outillage du projet recommande, et des rôles
types que Maestro connaît. Ces rôles types sont une matière, pas une liste fermée :
reprends-en un quand il convient (nomme-le dans "gabarit"), écarte-le quand le projet
ne l'appelle pas, et propose tout autre rôle que le besoin réel demande — une
application mobile, un pipeline d'apprentissage automatique, la sécurité, la
documentation, les données d'un projet neuf qui en stockera…

Chaque rôle porte sa raison : ce que ce rôle fera DANS CE projet, avec les mots du
projet — ce qu'il écrit, ce qu'il produit, ce qu'il vérifie —, et ce que le projet
contient, ou ce que la personne a dit, qui le rend nécessaire. Une raison qui vaudrait
pour n'importe quel dépôt (« le projet se construit et se teste ») n'en est pas une. Un
rôle sans raison tirée du projet ou de ses mots n'est pas proposé. Si un fichier cité
dans ce qu'on te donne le prouve, recopie son chemin exact dans "preuve" ; sinon laisse
"preuve" vide — n'invente jamais un chemin. Une instance
par rôle par défaut ; davantage seulement si le projet a des surfaces indépendantes, et
dis pourquoi. Ne propose jamais l'orchestrateur : c'est Maestro lui-même, présent dans
tout projet, et c'est lui qui recrute.

Maestro a un navigateur : il ouvre une page et la parcourt, la capture à une largeur
donnée, émule la réduction des animations et lit les erreurs de la console. Si le
projet construit une interface web — des pages qu'on ouvre dans un navigateur —,
donne-le ("navigateur": true) aux rôles qui en écrivent, en conçoivent ou en
vérifient le rendu : sans lui, ils écriraient leur propre banc pour la regarder. Un
projet sans interface web n'en a pas besoin, ni un rôle qui ne regarde pas l'écran.

Réponds par UN SEUL objet JSON, sans rien autour :

{
  "roles": [
    {
      "nom": "identifiant court en minuscules, chiffres et tirets, autre qu'un rôle type",
      "role": "le libellé du rôle, en quelques mots",
      "competences": ["trois à six tags courts, en minuscules"],
      "raison": "une phrase : pourquoi ce projet appelle ce rôle",
      "preuve": "le chemin exact d'un fichier cité qui le prouve, ou une chaîne vide",
      "gabarit": "le nom du rôle type dont il descend, ou une chaîne vide",
      "instances": 1,
      "raison_instances": "pourquoi ce nombre, s'il dépasse un",
      "skills": ["les skills du projet dont ce rôle se sert, parmi ceux listés"],
      "navigateur": false
    }
  ],
  "ecartes": [{"gabarit": "un rôle type non retenu", "raison": "une phrase : pourquoi"}],
  "retraits": ["noms de rôles de l'équipe montrée à retirer"],
  "remis": ["noms de rôles retirés de l'équipe montrée à remettre"],
  "instances": {"nom d'un rôle de l'équipe montrée": 2},
  "navigateur": {"nom d'un rôle de l'équipe montrée": false},
  "playbooks": {"nom d'un rôle de l'équipe montrée": "ce que son playbook suivra, en une phrase"},
  "reponse": "une ou deux phrases adressées à la personne"
}

Quand on te demande de COMPOSER l'équipe, remplis "roles" et "ecartes" et laisse le
reste vide. Quand on te transmet une DEMANDE de la personne sur l'équipe qu'elle a sous
les yeux, ne mets dans "roles" que les rôles à ajouter ; remplis "retraits", "remis",
"instances" et "navigateur" (true pour le donner à un rôle montré, false pour le lui
retirer) selon ce qu'elle demande, et dis-lui dans "reponse" ce que tu as fait —
ou, si sa demande ne porte pas sur l'équipe ou reste ambiguë, ce qu'il te faudrait
savoir, sans rien changer. Ne devine jamais : ne change que ce qu'elle a demandé.

Quand sa demande fixe une convention du projet — un outil, une bibliothèque, une façon
de tester ou d'écrire —, relis le playbook de CHAQUE rôle montré : nomme dans
"playbooks" tous ceux qui la contredisent, celui qu'elle cite comme les autres, avec ce
que chacun doit désormais suivre. Maestro les réécrit, sans retirer ni remplacer le
rôle. Ne dis jamais ce qu'un playbook prescrit, ni ce qu'un agent fera, sans l'avoir lu
dans ce qu'on te donne : un playbook qu'on ne t'a pas transmis, tu ne sais pas ce qu'il
dit.

Les raisons — celles des rôles écartés comprises — et la réponse sont lues telles
quelles par la personne : adresse-toi à elle, « vous avez répondu », « vos réponses »,
jamais « la personne » ni « l'utilisateur » à la troisième personne.

"""

#: The frame of the composition and correction call — its instruction, its response
#: contract, and the **language register** (#945): the reasons and the reply are read
#: as they are by the person, at the team step.
COMPOSITION_INSTRUCTIONS = _INSTRUCTIONS + registre()


class UnreadableComposition(ValueError):
    """The model's response leaves nothing usable — and nothing has been proposed.

    The service does one of two things with it, depending on the request: a
    **composition** falls back on the template rules, stated as such; a **correction**
    fails plainly (502), because there is no rule for « ajoute quelqu'un pour la
    sécurité », and inventing an answer would be worse than giving none.
    """


@dataclass(frozen=True)
class CurrentMember:
    """A role of the team **as the person sees it** at the time of their request.

    `retained` tells a ticked line from a removed one: « remets les tests » means
    nothing if the model does not know the tests were removed. Only these names can be
    removed, restored or have their instances changed by a correction.

    `browser` (#1532) says whether this role has, on screen, Maestro's browser:
    « retire-lui le navigateur » cannot be understood without knowing who has it.

    `playbook` (#1521) is the one the person reads on the card. Without it, the model
    would answer about what an agent does without having read it — « la commande de
    test est déjà pytest, c'est elle qu'il utilisera » of a tester whose playbook
    imposes `unittest` —, and a convention corrected in words would reach only the
    named role. Empty: not sent, and the model knows it.
    """

    name: str
    role: str
    retained: bool = True
    instances: int = 1
    browser: bool = False
    playbook: str = ""


@dataclass(frozen=True)
class ModelResponse:
    """What the model answered, **read but not yet verified**.

    Reading judges only the shape (an object, lists, strings); verification — what
    becomes a role, what is excluded — belongs to `propose_composed_team` and
    `correct_team`, which know the project.
    """

    roles: tuple[Mapping[str, Any], ...] = ()
    excluded: tuple[Mapping[str, Any], ...] = ()
    removals: tuple[str, ...] = ()
    restored: tuple[str, ...] = ()
    instances: Mapping[str, Any] = field(default_factory=dict)
    browser: Mapping[str, Any] = field(default_factory=dict)
    playbooks: Mapping[str, Any] = field(default_factory=dict)
    reply: str = ""


@dataclass(frozen=True)
class CorrectedPlaybook:
    """The playbook of a shown role, rewritten to follow what the person has set (#1521).

    Its keys are named as on a role (`playbook_origine`, `playbook_raison`): the card
    lays it on the line it corrects without translating anything. The origin is
    `genere` — written for this project by the model —, and the reason states the
    request it follows.
    """

    playbook: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "playbook": self.playbook,
            "playbook_origine": PLAYBOOK_ORIGIN_GENERATED,
            "playbook_raison": self.reason,
        }


@dataclass(frozen=True)
class TeamCorrection:
    """What a natural-language request changes in the shown team — nothing created.

    `additions` are roles proposed **in their own right**: same shape, same
    permissions, same playbook written for the project as a role of the composition.
    `removals`, `restored`, `instances` and `browser` name only roles of the shown
    team — `browser` (#1532) says to whom Maestro's browser is given (`True`) or taken
    away (`False`). `reply` is the sentence the screen shows the person.

    `instructions` (#1521) names, among the shown roles, those whose playbook
    contradicts the convention the request sets, with what each one must follow;
    `playbooks` holds those Maestro has rewritten. The two do not always coincide: a
    playbook that was not sent, or a rewrite that fails, stays as it was, and the reply
    says so (`maestro.controltower.equipe`).
    """

    reply: str
    additions: tuple[ProposedRole, ...] = ()
    removals: tuple[str, ...] = ()
    restored: tuple[str, ...] = ()
    instances: Mapping[str, int] = field(default_factory=dict)
    browser: Mapping[str, bool] = field(default_factory=dict)
    instructions: Mapping[str, str] = field(default_factory=dict)
    playbooks: Mapping[str, CorrectedPlaybook] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """The correction as JSON — `cree: False`, like every proposal (#1040)."""
        return {
            "reponse": self.reply,
            "ajouts": [role.to_dict() for role in self.additions],
            "retraits": list(self.removals),
            "remis": list(self.restored),
            "instances": dict(self.instances),
            "navigateur": dict(self.browser),
            "playbooks": {name: fixed.to_dict() for name, fixed in self.playbooks.items()},
            "cree": False,
        }


# --------------------------------------------------------------------------- #
# What the model is asked
# --------------------------------------------------------------------------- #


def composition_prompt(
    findings: Constats,
    recommendation: Recommandation,
    choices: Sequence[Choix] = (),
    *,
    project_name: str = "",
) -> str:
    """The composition request: the project, then the instruction that closes the prompt."""
    return "\n".join(
        (
            *_context(findings, recommendation, choices, project_name),
            "Compose l'équipe de ce projet selon le format demandé.",
        )
    )


def correction_prompt(
    findings: Constats,
    recommendation: Recommandation,
    choices: Sequence[Choix],
    request: str,
    team: Sequence[CurrentMember],
    *,
    project_name: str = "",
) -> str:
    """The correction request: the project, the shown team, then the person's own words.

    The request comes **last**, just before the instruction: it is the material being
    handled, and the rule of the global chat holds here — what closes the prompt reads
    as an instruction, so the instruction is ours, not theirs.

    The shown playbooks follow the team (#1521): the model answers on what it has read,
    and names every role that a convention set by the request contradicts.
    """
    lines = [f"- {m.name} — {m.role}{_member_state(m)}" for m in team]
    return "\n".join(
        (
            *_context(findings, recommendation, choices, project_name),
            "Équipe que la personne a sous les yeux :",
            "",
            "\n".join(lines) if lines else "- (aucun rôle)",
            "",
            *_shown_playbooks(team),
            "Demande de la personne, avec ses mots :",
            "",
            valid_request(request),
            "",
            "Applique sa demande à l'équipe selon le format demandé.",
        )
    )


def valid_request(request: str) -> str:
    """The request flattened — or `ValueError` if it is empty or too long.

    Refused **before** any call: an input is not an outage, and the caller turns it
    into a 422, never a 502.
    """
    sentence = " ".join(request.split())
    if not sentence:
        raise ValueError("demande vide : dites ce que vous voulez changer à l'équipe.")
    if len(sentence) > MAX_REQUEST:
        raise ValueError(
            f"demande trop longue ({len(sentence)} caractères, {MAX_REQUEST} au plus) : "
            "dites en une phrase ce que vous voulez changer à l'équipe."
        )
    return sentence


_PLAYBOOK_NOT_SENT = "(playbook non transmis : tu ne sais pas ce qu'il dit)"


def _shown_playbooks(team: Sequence[CurrentMember]) -> tuple[str, ...]:
    """The playbook of every shown role, as the person reads it — or the fact it is missing.

    A missing playbook is **stated** instead of left out: the model then knows that it
    does not know what the playbook prescribes, and the frame forbids it to assert
    anything about it.
    """
    if not team:
        return ()
    blocks: list[str] = ["Playbooks de l'équipe montrée, tels que la personne les lit :", ""]
    for member in team:
        blocks += [f"### {member.name} — {member.role}", ""]
        blocks += [member.playbook.strip() or _PLAYBOOK_NOT_SENT, ""]
    return tuple(blocks)


def _member_state(member: CurrentMember) -> str:
    state = [] if member.retained else ["retiré par la personne"]
    if member.instances > 1:
        state.append(f"{member.instances} instances")
    if member.browser:
        state.append("a le navigateur de Maestro")
    return f" ({', '.join(state)})" if state else ""


def _context(
    findings: Constats,
    recommendation: Recommandation,
    choices: Sequence[Choix],
    project_name: str,
) -> tuple[str, ...]:
    """What the model must know about the project — everything that is read, nothing else."""
    provenance = (
        "un projet neuf, décrit par les réponses de la personne"
        if choices
        else "un projet existant, lu par l'analyse"
    )
    named = f" « {project_name} »" if project_name else ""
    return (
        f"Le projet{named} est {provenance}.",
        "",
        "Ce que l'analyse a constaté :",
        "",
        _plain_findings(findings),
        "",
        *(
            (
                "Ce que la personne a répondu :",
                "",
                "\n".join(f"- {c.cle} : {c.valeur}" for c in choices),
                "",
            )
            if choices
            else ()
        ),
        "Skills que l'outillage du projet recommande :",
        "",
        _plain_skills(recommendation),
        "",
        "Rôles types que Maestro connaît (une matière, pas une liste fermée) :",
        "",
        "\n".join(
            f"- {t.agent_name} — {t.role} : {', '.join(t.competencies)}" for t in ROLE_TEMPLATES
        ),
        "",
    )


def _plain_findings(findings: Constats) -> str:
    lines = []
    for lang in findings.langages:
        example = f", par exemple {lang.exemple}" if lang.exemple else ""
        lines.append(
            f"- langage {lang.nom} : {round(lang.part * 100)} % des fichiers de code "
            f"({lang.fichiers} fichier(s){example})"
        )
    lines += [
        f"- commande de « {c.usage} » : `{c.commande}` ({c.chemin}, {c.origine})"
        for c in findings.commandes
    ]
    lines += [f"- intégration continue : {evidence.chemin}" for evidence in findings.ci]
    if findings.forge is not None:
        lines.append(f"- forge : {findings.forge.nom}")
    lines += [f"- convention : {evidence.chemin}" for evidence in findings.conventions]
    lines += [f"- script : {evidence.chemin}" for evidence in findings.dossier_scripts.scripts]
    lines += [
        f"- outillage d'agents déjà présent : {evidence.chemin}"
        for evidence in findings.outillage_present
    ]
    return "\n".join(lines) if lines else "- rien : le projet est vide, ou neuf"


def _plain_skills(recommendation: Recommandation) -> str:
    """The recommended skills, their command — and what they do for this project (#1350).

    What the model understood of a command (`Entree.pour`, « assembler le carnet à
    partir des chants ») is what ties a role to the project's real work: without it, a
    skill is only a generic name, and so is the reason of the role that uses it (S9).
    """
    skills = _recommended_skills(recommendation)
    if not skills:
        return "- aucun : l'outillage n'en recommande pas"
    lines = []
    for name, entry in skills.items():
        command = f" : `{entry.commandes[0]}`" if entry.commandes else ""
        purpose = f" — {entry.pour}" if entry.pour else ""
        lines.append(f"- {name}{command}{purpose}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# What the model answers — read, then verified
# --------------------------------------------------------------------------- #

_FENCE = re.compile(r"```(?:json)?\s*(?P<corps>.*?)```", re.DOTALL)


def read_response(text: str) -> ModelResponse:
    """The model's response, read in its shape — `UnreadableComposition` if it has none.

    The object is looked for bare, in a code block, or buried in prose (a model adds
    some) — the pattern of `orchestration._objet_json`, without importing it: this
    module is pure, and orchestration pulls in the whole engine. Unknown keys are
    ignored; an expected key of the wrong shape raises, because a list of roles that is
    not a list is not repaired by guessing.
    """
    obj = _json_object(text)
    if not isinstance(obj, dict):
        raise UnreadableComposition("la réponse du modèle ne contient aucun objet JSON lisible")
    roles, excluded = obj.get("roles") or [], obj.get("ecartes") or []
    removals, restored = obj.get("retraits") or [], obj.get("remis") or []
    instances = obj.get("instances") or {}
    if not all(isinstance(v, list) for v in (roles, excluded, removals, restored)) or not (
        isinstance(instances, dict)
    ):
        raise UnreadableComposition(
            "la réponse du modèle n'a pas la forme attendue (des listes de rôles et de noms)"
        )
    # The browser (#1532) and the playbooks to rewrite (#1521) only have a shape in a
    # correction: a composition that sets them at the wrong level does not lose its team
    # for it — they are ignored.
    browser = obj.get("navigateur")
    playbooks = obj.get("playbooks")
    return ModelResponse(
        roles=tuple(r for r in roles if isinstance(r, dict)),
        excluded=tuple(e for e in excluded if isinstance(e, dict)),
        removals=tuple(str(n) for n in removals if isinstance(n, str)),
        restored=tuple(str(n) for n in restored if isinstance(n, str)),
        instances=instances,
        browser=browser if isinstance(browser, dict) else {},
        playbooks=playbooks if isinstance(playbooks, dict) else {},
        reply=_text(obj.get("reponse")),
    )


def _json_object(text: str) -> Any:
    candidates = [text.strip()]
    fence = _FENCE.search(text)
    if fence is not None:
        candidates.append(fence.group("corps").strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def propose_composed_team(
    findings: Constats,
    recommendation: Recommandation,
    response: ModelResponse,
    *,
    project_id: str = "",
    taken_names: Sequence[str] = (),
    source: Mapping[str, object] | None = None,
) -> TeamProposal:
    """The team the model composed, **verified** — never created.

    Same shape as `propose_team`: the screen, creation (#1040) and the chat card read a
    proposal without knowing who composed it; only `composition_origin` says so. The
    templates the model did not retain are excluded **by name**, with its reason when it
    gives one — rule 2 of `maestro.team.proposal`.

    Raises `UnreadableComposition` if no role survives verification.
    """
    skills = _recommended_skills(recommendation)
    taken = [*taken_names, *sorted(NOMS_RESERVES)]
    roles, rejections = _verified_roles(response.roles, findings, skills, taken)
    if not roles:
        raise UnreadableComposition(
            "le modèle n'a proposé aucun rôle vérifiable"
            + (f" ({'; '.join(rejections)})" if rejections else "")
        )
    excluded = [EXCLUDED_ORCHESTRATOR, *_excluded(roles, response.excluded)]
    return TeamProposal(
        id=new_id(),
        project_id=project_id,
        made_at=_now(),
        summary=_summary(roles, excluded),
        roles=tuple(roles),
        excluded=tuple(excluded),
        source=dict(source) if source is not None else None,
        composition_origin=COMPOSITION_ORIGIN_MODEL,
        composition_reason=(
            f"{len(rejections)} rôle(s) proposé(s) par le modèle écarté(s) à la vérification : "
            + " ; ".join(rejections)
            if rejections
            else ""
        ),
    )


def correct_team(
    findings: Constats,
    recommendation: Recommandation,
    response: ModelResponse,
    *,
    team: Sequence[CurrentMember],
    taken_names: Sequence[str] = (),
) -> TeamCorrection:
    """What the request changes in the shown team, **verified** — never applied here.

    Additions follow the composition's verification; their names are made free **also**
    against the shown team, otherwise an addition would overwrite a line on screen.
    Removals, restorations and instances name only shown roles.

    A response with no change at all **and** no sentence raises `UnreadableComposition`:
    the model said nothing, and a silent correction would leave the person not knowing
    whether their request was read.
    """
    shown = {member.name for member in team}
    taken = [*taken_names, *shown, *sorted(NOMS_RESERVES)]
    additions, rejections = _verified_roles(
        response.roles, findings, _recommended_skills(recommendation), taken
    )
    removals = tuple(dict.fromkeys(n for n in response.removals if n in shown))
    restored = tuple(dict.fromkeys(n for n in response.restored if n in shown))
    instances = {
        name: count
        for name, raw in response.instances.items()
        if name in shown and (count := _admitted_instances(raw)) is not None
    }
    # A boolean, and only that: « oui » spelled out is not a decision one reads, it is
    # an answer outside the contract — ignored, never guessed.
    browser = {
        name: raw
        for name, raw in response.browser.items()
        if name in shown and isinstance(raw, bool)
    }
    # One instruction per shown role, on one line: an invented name rewrites nothing,
    # and an empty instruction does not say what to follow (#1521).
    instructions = {
        name: instruction
        for name, raw in response.playbooks.items()
        if name in shown and (instruction := _one_line(_text(raw)))
    }
    reply = response.reply
    if rejections:
        reply = f"{reply} ({'; '.join(rejections)})".strip()
    if not (additions or removals or restored or instances or browser or instructions or reply):
        raise UnreadableComposition("le modèle n'a rien changé et n'a rien répondu")
    return TeamCorrection(
        reply=reply
        or _default_reply(additions, removals, restored, instances, browser, instructions),
        additions=tuple(additions),
        removals=removals,
        restored=restored,
        instances=instances,
        browser=browser,
        instructions=instructions,
    )


def _default_reply(
    additions: Sequence[ProposedRole],
    removals: Sequence[str],
    restored: Sequence[str],
    instances: Mapping[str, int],
    browser: Mapping[str, bool] | None = None,
    instructions: Mapping[str, str] | None = None,
) -> str:
    """What is said when the model changed the team without saying anything about it."""
    parts = []
    if additions:
        parts.append("ajouté : " + ", ".join(r.role for r in additions))
    if removals:
        parts.append("retiré : " + ", ".join(removals))
    if restored:
        parts.append("remis : " + ", ".join(restored))
    if instances:
        parts.append(
            "instances : " + ", ".join(f"{name} ×{n}" for name, n in instances.items())
        )
    given = [name for name, yes in (browser or {}).items() if yes]
    taken_away = [name for name, yes in (browser or {}).items() if not yes]
    if given:
        parts.append("navigateur de Maestro donné à : " + ", ".join(given))
    if taken_away:
        parts.append("navigateur de Maestro retiré à : " + ", ".join(taken_away))
    if instructions:
        parts.append("playbook à réécrire pour suivre votre demande : " + ", ".join(instructions))
    return "Équipe modifiée — " + " ; ".join(parts) + "."


def _verified_roles(
    raw_roles: Sequence[Mapping[str, Any]],
    findings: Constats,
    skills: Mapping[str, Entree],
    taken: list[str],
) -> tuple[list[ProposedRole], list[str]]:
    """The roles verification lets through, and what it set aside (in plain words)."""
    roles: list[ProposedRole] = []
    rejections: list[str] = []
    for raw in raw_roles[:MAX_ROLES]:
        label = _one_line(_text(raw.get("role")))
        reason = _one_line(_text(raw.get("raison")))
        slug = _slug(_text(raw.get("nom"))) or _slug(label)
        if _ORCHESTRATOR_NAME in (slug, _slug(label)):
            rejections.append("l'orchestrateur, qui est Maestro et n'est jamais recruté")
            continue
        if not label or not reason:
            rejections.append(f"« {label or slug or '?'} », proposé sans raison")
            continue
        template = _template(_text(raw.get("gabarit")))
        competencies = _competencies(raw.get("competences")) or (
            template.competencies if template is not None else ()
        )
        if not competencies:
            rejections.append(f"« {label} », proposé sans aucune compétence")
            continue
        name = _free_name(_base_name(slug, label, template), taken)
        taken.append(name)
        roles.append(
            _composed_role(
                raw, name, label, reason, competencies, template, findings, skills
            )
        )
    return roles, rejections


def _base_name(slug: str, label: str, template: RoleTemplate | None) -> str:
    """The name to make free: the model's, unless it is **reserved**.

    Observed on the real stack: the model readily names a role after the code agent it
    derives from (`developpeur`, `qa`, `devops`), names the repository reserves
    (`NOMS_RESERVES`). Suffixing them gives `developpeur-2` — free, but unreadable at
    the team step. So the name falls back, in order, on the **template**'s name (`dev`,
    `tests`, `infra` — that is what `RoleTemplate.name` is for), then on the label; the
    suffix is only a last resort.
    """
    candidates = (
        slug,
        template.name if template is not None else "",
        _slug(label),
    )
    free = [c for c in candidates if c and c not in NOMS_RESERVES]
    return free[0] if free else (slug or "role")


def _composed_role(
    raw: Mapping[str, Any],
    name: str,
    label: str,
    reason: str,
    competencies: tuple[str, ...],
    template: RoleTemplate | None,
    findings: Constats,
    skills: Mapping[str, Entree],
) -> ProposedRole:
    """The verified role, in the exact shape of a template role.

    A role that **derives from a template** keeps its material: its usages (hence its
    skills), its tools, its fallback playbook. A role **outside the templates** attaches
    the skills the model named among those recommended, holds the tools of the generic
    frame — those the created agent will really have (`profil_outille`) — and carries a
    playbook sketch until #257 writes one for this project.
    """
    usages = _usages(template, raw.get("skills"), skills)
    tools = profil_outille(
        template.agent
        if template is not None
        else Agent(
            nom=name,
            role=label,
            competences=frozenset(competencies),
            modele=MODELE_EXECUTANT_DEFAUT,
            prompt_systeme="",
        )
    ).outils
    attached = skills_by_usage(usages, skills)
    permissions = role_permissions(tools, usages, findings, attached)
    instances, instances_reason = _instances(raw)
    return ProposedRole(
        name=name,
        role=label,
        role_template=template.agent_name if template is not None else "",
        competencies=competencies,
        reason=reason,
        justification=_evidence(_text(raw.get("preuve")), findings),
        instances=instances,
        instances_reason=instances_reason,
        playbook=(
            template.fallback_playbook()
            if template is not None
            else sketch_playbook(label, reason, competencies)
        ),
        playbook_origin=(
            PLAYBOOK_ORIGIN_TEMPLATE if template is not None else PLAYBOOK_ORIGIN_SKETCH
        ),
        playbook_reason=(
            template_playbook_reason(template.agent_name)
            if template is not None
            else SKETCH_REASON
        ),
        intent=role_intent(
            label,
            findings,
            competencies=() if template is not None else competencies,
        ),
        tools=tools,
        skills=attached,
        permissions=permissions,
        # The model's judgement, and only that (#1532, docs/41): no lexicon decides that
        # a project « a des écrans ». A strict boolean — « oui » spelled out is outside
        # the contract, and gives nothing.
        browser=raw.get("navigateur") is True,
    )


#: What is said of a sketch **before** its drafting has been attempted.
SKETCH_REASON = (
    "esquisse écrite à partir du rôle, de sa raison et de ses compétences : ce rôle ne "
    "descend d'aucun gabarit, et sa rédaction pour ce projet n'a pas encore eu lieu"
)


def sketch_playbook(role: str, reason: str, competencies: Sequence[str]) -> str:
    """The playbook of a role **outside the templates**, until #257 has written one.

    A real system prompt, not an empty template: the role, why it was recruited, its
    competencies, then the roles' **base** (`socle()`) — the senior regime and the
    language register every code role already receives. It states itself as such
    (`PLAYBOOK_ORIGIN_SKETCH`) and is reviewed at validation, like every playbook.
    """
    return (
        f"# {role}\n\n"
        f"Tu es l'agent « {role} » de ce projet. On t'a recruté parce que {reason}.\n\n"
        f"Ton métier : {', '.join(competencies)}. Tu t'en tiens à lui, et tu signales ce "
        "qui en sort plutôt que de le prendre.\n\n"
        f"{socle()}"
    )


def _excluded(
    roles: Sequence[ProposedRole], raw_items: Sequence[Mapping[str, Any]]
) -> list[ExcludedRole]:
    """Every template no role derives from, with the model's reason or its own."""
    retained = {role.role_template for role in roles}
    reasons: dict[str, str] = {}
    for raw in raw_items:
        template = _template(_text(raw.get("gabarit")) or _text(raw.get("nom")))
        reason = _one_line(_text(raw.get("raison")))
        if template is not None and reason:
            reasons.setdefault(template.agent_name, reason)
    return [
        ExcludedRole(
            name=template.name,
            role=template.role,
            reason=reasons.get(template.agent_name, NOT_RETAINED_REASON),
        )
        for template in ROLE_TEMPLATES
        if template.agent_name not in retained
    ]


def _template(raw: str) -> RoleTemplate | None:
    """The template the model names — by its agent name or its proposed slug."""
    key = _slug(raw)
    if not key:
        return None
    return next((t for t in ROLE_TEMPLATES if key in (t.agent_name, t.name)), None)


def _usages(
    template: RoleTemplate | None, names: Any, skills: Mapping[str, Entree]
) -> tuple[tuple[str, str], ...]:
    """The usages this role attaches: those of its template, then those it named.

    A named skill is retained only if it is **recommended** for this project: the model
    picks from the list it was given, it does not add to it.
    """
    usages: dict[str, str] = dict(template.usages) if template is not None else {}
    usage_of_skill = {name: usage for usage, (name, _) in SKILL_PAR_USAGE.items()}
    for name in names if isinstance(names, list) else ():
        usage = usage_of_skill.get(_text(name))
        if usage is not None and usage not in usages and _text(name) in skills:
            usages[usage] = "l'analyse d'équipe l'a rattaché à ce rôle"
    return tuple(usages.items())


def _instances(raw: Mapping[str, Any]) -> tuple[int, str]:
    """The number of instances and its reason — one, stated as such, when out of bounds."""
    proposed = raw.get("instances", 1)
    count = _admitted_instances(proposed)
    if count is None:
        return 1, (
            f"le modèle en proposait « {proposed} », hors des bornes de 1 à "
            f"{MAX_CREATED_INSTANCES} : une instance, que vous ajustez si besoin"
        )
    if count == 1:
        return 1, SINGLE_INSTANCE_REASON
    reason = _one_line(_text(raw.get("raison_instances")))
    return count, reason or f"{count} instances proposées par l'analyse d'équipe"


def _admitted_instances(raw: Any) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw if 1 <= raw <= MAX_CREATED_INSTANCES else None


def _evidence(path: str, findings: Constats) -> Piece | None:
    """The evidence the model cites, **if the analysis has read it** — otherwise none.

    The admitted paths are those the findings carry: language examples, commands, CI,
    forge, conventions, scripts, tooling already present. A plausible path that was
    never read would be invented evidence — worse than no evidence.
    """
    if not path:
        return None
    seen: dict[str, str] = {}
    for language in findings.langages:
        if language.exemple:
            seen.setdefault(language.exemple, f"{language.fichiers} fichier(s) {language.nom}")
    for command in findings.commandes:
        seen.setdefault(command.chemin, f"commande de {command.usage}")
    for evidence in (
        *findings.ci,
        *findings.conventions,
        *findings.dossier_scripts.scripts,
        *findings.outillage_present,
    ):
        seen.setdefault(evidence.chemin, evidence.role or evidence.nom)
    if findings.forge is not None and findings.forge.chemin:
        seen.setdefault(findings.forge.chemin, "forge constatée")
    role = seen.get(path.strip())
    return Piece(nom=path.strip(), chemin=path.strip(), role=role) if role else None


def _competencies(raw: Any) -> tuple[str, ...]:
    """The competencies reduced to the routing tags — lowercase, unaccented, hyphenated."""
    if not isinstance(raw, list):
        return ()
    return tuple(dict.fromkeys(tag for tag in (_slug(_text(c)) for c in raw) if tag))


def _text(raw: Any) -> str:
    return raw.strip() if isinstance(raw, str) else ""


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _slug(raw: str) -> str:
    """`raw` in the shape of an agent name (`[a-z0-9][a-z0-9_-]*`), or empty."""
    decomposed = unicodedata.normalize("NFKD", raw)
    unaccented = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", unaccented).strip("-")[:40].strip("-")
