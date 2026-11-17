"""The team is proposed for the project's **real need**, and corrected in natural language (#1159).

[docs/41](../docs/41-decision-maestro-juge-il-ne-bride-pas.md) overturns the rule-based
derivation of #1039: five templates retained by fixed rules **never** proposed a mobile,
machine-learning, security or documentation role, and the data role never on a new
project. The model composes the team; the templates remain **material**, and the rules
the **fallback** when it does not answer.

What this suite holds, in the ticket's order:

① **the model names the relevant roles, with their reason** — a mobile project, a
   learning pipeline, and the data role on a new project that needs it;
② **execution verifies what it writes** — a role with no reason, an unknown template, a
   skill the tooling does not recommend, evidence the analysis has not read, a number
   of instances out of bounds, the orchestrator: none of that passes as is;
③ **the fallback is stated** — a composition that does not succeed falls back on the
   template rules, and the proposal names the cause;
④ **« ajoute quelqu'un pour la sécurité » is understood** — the role is added with its
   playbook, by the route the team step calls;
⑤ **the prompt carries the project and the person's words**, and the language register;
⑦ **what the person has set about the project, their playbooks follow** (#1521) — the
   writer receives their answers, and a convention corrected in words reaches every
   shown role whose playbook contradicts it, on playbooks the model has read.

The provider is a **fake** (§5 of docs/41: unit tests keep their doubles): it returns
the text a model would have returned, and records what it was asked.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.agents.capacity import CapacityStore
from maestro.agents.configuration import ConfigurationAgents
from maestro.agents.mcp import McpStore
from maestro.agents.permissions import PermissionStore
from maestro.agents.playbook_du_code import registre
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.store import NOMS_RESERVES, AgentStore, SurchargeStore
from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.auto_amelioration import MARQUEUR_PLAYBOOK
from maestro.controltower.equipe import CompositeurEquipe, ServiceEquipe
from maestro.controltower.generation_agent import (
    INTENTION_MAX,
    DefinitionProposee,
    GenerateurDefinitionAgent,
)
from maestro.controltower.projets import ServiceProjets
from maestro.decideur import Decideur
from maestro.outillage.generation import generer
from maestro.outillage.modele import Commande, Constats, Langage
from maestro.outillage.questionnaire import (
    Choix,
    constats_depuis_choix,
    recommandation_depuis_choix,
    source_manifeste_des_choix,
)
from maestro.outillage.recommandation import recommander
from maestro.outillage.redaction import rediger
from maestro.portee import PORTEE_PROJET
from maestro.projets import ProjetStore
from maestro.providers.base import ModelProvider
from maestro.team import (
    EXCLUDED_ORCHESTRATOR,
    EXECUTION_TOOL,
    MAX_CREATED_INSTANCES,
    propose_team,
)
from maestro.team.composition import (
    COMPOSITION_INSTRUCTIONS,
    MAX_REQUEST,
    CurrentMember,
    UnreadableComposition,
    composition_prompt,
    correct_team,
    correction_prompt,
    propose_composed_team,
    read_response,
)
from maestro.team.model import (
    COMPOSITION_ORIGIN_MODEL,
    COMPOSITION_ORIGIN_RULES,
    PLAYBOOK_ORIGIN_GENERATED,
    PLAYBOOK_ORIGIN_SKETCH,
    PLAYBOOK_ORIGIN_TEMPLATE,
)
from maestro.team.proposal import project_facts

PROJECT = "prj-mobile"


# --------------------------------------------------------------------------- #
# The doubles: a provider that returns what a model would have returned
# --------------------------------------------------------------------------- #


class _ScriptedProvider(ModelProvider):
    """Returns, in order, the texts it was given — and records each request."""

    name = "ecrit"

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.requests: list[tuple[str, str | None]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        self.requests.append((prompt, system_prompt))
        return self._replies.pop(0)

    async def run_agent(self, *args: Any, **kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError("la composition d'équipe ne fait qu'écrire")


class _FailingProvider(ModelProvider):
    """A provider that never answers — quota exhausted, network down."""

    name = "en-panne"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        raise RuntimeError("quota épuisé (test)")

    async def run_agent(self, *args: Any, **kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError("jamais appelé")


class _ScriptedGenerator(GenerateurDefinitionAgent):
    """A playbook generator that writes a recognisable playbook per role.

    It also records the project facts it is passed (#1521), and rewrites a shown
    playbook by adding the instruction it received — except for the roles of
    `failing`, whose rewrite fails like an exhausted quota.
    """

    def __init__(self, *, failing: tuple[str, ...] = ()) -> None:
        super().__init__(provider=_FailingProvider())
        self.intents: list[str] = []
        self.facts: list[str] = []
        self.corrections: list[tuple[str, str, str]] = []
        self._failing = failing

    async def proposer(self, intention: str, **kwargs: Any) -> DefinitionProposee:
        self.intents.append(intention)
        self.facts.append(kwargs.get("faits", ""))
        return DefinitionProposee(
            intention=intention,
            nom="x",
            role="x",
            competences=("x",),
            playbook=f"Playbook écrit pour : {intention}",
        )

    async def appliquer(self, playbook: str, *, demande: str, consigne: str) -> str:
        self.corrections.append((playbook, demande, consigne))
        if any(name in playbook for name in self._failing):
            raise RuntimeError("quota épuisé (test)")
        return f"{playbook}\n\nRéécrit : {consigne}"


class _OfflineGenerator(GenerateurDefinitionAgent):
    """A playbook generator that never answers — the role keeps its fallback."""

    async def proposer(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("hors ligne (test)")

    async def appliquer(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("hors ligne (test)")


def _json(**content: Any) -> str:
    """What a model returns, buried in a little prose to put the reading to the test."""
    return "Voici l'équipe :\n```json\n" + json.dumps(content, ensure_ascii=False) + "\n```"


# --------------------------------------------------------------------------- #
# Projects outside the five templates
# --------------------------------------------------------------------------- #


def _mobile_findings() -> Constats:
    """A Flutter application: Dart, and nothing that looks like a screen template."""
    return Constats(
        langages=(Langage(nom="Dart", fichiers=24, part=1.0, exemple="lib/main.dart"),),
        commandes=(
            Commande(
                usage="tester",
                commande="flutter test",
                chemin="pubspec.yaml",
                origine="declaree",
            ),
        ),
    )


def _ml_findings() -> Constats:
    """A training pipeline: Python and notebooks, no interface."""
    return Constats(
        langages=(
            Langage(nom="Python", fichiers=18, part=0.7, exemple="pipeline/entrainer.py"),
            Langage(nom="Jupyter Notebook", fichiers=6, part=0.3, exemple="carnets/eda.ipynb"),
        ),
    )


MOBILE_ROLE = {
    "nom": "mobile",
    "role": "Développeur mobile",
    "competences": ["flutter", "dart", "ios", "android"],
    "raison": "le projet est une application Flutter (lib/main.dart) : iOS et Android se "
    "construisent et se testent à part",
    "preuve": "lib/main.dart",
    "gabarit": "",
    "instances": 1,
    "skills": ["lancer-les-tests"],
}

ML_ROLE = {
    "nom": "ml",
    "role": "Ingénieur apprentissage automatique",
    "competences": ["machine learning", "entraînement", "évaluation", "données"],
    "raison": "le projet entraîne un modèle (pipeline/entrainer.py) et l'évalue dans des "
    "carnets : le jeu de données et les métriques sont un métier à part",
    "preuve": "pipeline/entrainer.py",
    "gabarit": "",
    "instances": 1,
    "skills": [],
}


@pytest.mark.parametrize(
    ("findings", "raw", "expected"),
    [
        (_mobile_findings(), MOBILE_ROLE, "Développeur mobile"),
        (_ml_findings(), ML_ROLE, "Ingénieur apprentissage automatique"),
    ],
    ids=["application-mobile", "pipeline-ml"],
)
def test_outside_the_templates_the_proposal_names_the_relevant_roles_with_their_reason(
    findings: Constats, raw: dict[str, Any], expected: str
) -> None:
    """① The ticket's criterion: what the rules would **never** have proposed."""
    by_rules = propose_team(findings, recommander(findings), project_id=PROJECT)
    assert expected not in {role.role for role in by_rules.roles}

    response = read_response(_json(roles=[raw], ecartes=[]))
    proposal = propose_composed_team(findings, recommander(findings), response, project_id=PROJECT)

    role = next(r for r in proposal.roles if r.role == expected)
    assert role.reason == raw["raison"]
    assert role.role_template == ""  # no template is its material
    assert role.justification is not None
    assert role.justification.chemin == raw["preuve"]
    # A role outside the templates has a playbook from the proposal on — a sketch, said so.
    assert role.playbook.strip()
    assert role.playbook_origin == PLAYBOOK_ORIGIN_SKETCH
    assert expected in role.playbook
    assert registre() in role.playbook
    # And it receives its permissions by the same rule as a template role.
    assert [p.tool for p in role.permissions] == [EXECUTION_TOOL]
    assert proposal.composition_origin == COMPOSITION_ORIGIN_MODEL


def test_a_composed_role_s_competencies_are_tags_routing_can_read() -> None:
    """Routing matches exact tags (`Agent.couverture`), and the catalog writes them in
    lower case, without accents, joined by hyphens. A « machine learning » as is would
    never be matched."""
    proposal = propose_composed_team(
        _ml_findings(),
        recommander(_ml_findings()),
        read_response(_json(roles=[ML_ROLE])),
        project_id=PROJECT,
    )

    assert proposal.roles[0].competencies == (
        "machine-learning",
        "entrainement",
        "evaluation",
        "donnees",
    )


def test_the_data_role_is_proposed_on_a_new_project_that_needs_it() -> None:
    """① The data role was born **only** from SQL files, so never on a new project. The
    model reads what the person answered, and proposes it with its reason; it derives
    from the `bdd` template, whose fallback playbook it keeps."""
    choices = (
        Choix(cle="nature", valeur="service-api"),
        Choix(cle="langages", valeur="python"),
    )
    findings = constats_depuis_choix(choices)
    by_rules = propose_team(findings, recommandation_depuis_choix(choices), choices=choices)
    assert "donnees" not in {role.name for role in by_rules.roles}

    response = read_response(
        _json(
            roles=[
                {
                    "nom": "donnees",
                    "role": "Base de données",
                    "competences": ["sql", "schema", "migration"],
                    "raison": "vous décrivez un service qui enregistre et restitue des "
                    "données : leur schéma et ses migrations sont à tenir dès le départ",
                    "gabarit": "bdd",
                }
            ]
        )
    )
    proposal = propose_composed_team(
        findings, recommandation_depuis_choix(choices), response, project_id=PROJECT
    )

    data = next(r for r in proposal.roles if r.name == "donnees")
    assert data.role_template == "bdd"
    assert "enregistre et restitue des données" in data.reason
    assert data.playbook_origin == PLAYBOOK_ORIGIN_TEMPLATE
    assert data.justification is None  # no file proves it yet: nothing invented


def test_a_role_derived_from_a_template_keeps_its_material() -> None:
    """Templates are material: a « qa » role keeps its competencies if it gives none, and
    attaches its template's skills by usage."""
    findings = _mobile_findings()
    tester = {"role": "Testeur", "raison": "les tests Flutter sont déclarés", "gabarit": "qa"}
    response = read_response(_json(roles=[tester]))

    tests = propose_composed_team(findings, recommander(findings), response).roles[0]

    assert tests.role_template == "qa"
    assert set(tests.competencies) == {"tests", "e2e", "review", "qa"}
    assert [s.name for s in tests.skills] == ["lancer-les-tests"]
    assert tests.name not in NOMS_RESERVES


# --------------------------------------------------------------------------- #
# ② Execution verifies what the model writes
# --------------------------------------------------------------------------- #


def _compose(*roles: dict[str, Any], findings: Constats | None = None, **extra: Any):
    findings = findings or _mobile_findings()
    return propose_composed_team(
        findings,
        recommander(findings),
        read_response(_json(roles=list(roles), **extra)),
        project_id=PROJECT,
    )


def test_a_role_without_reason_is_not_proposed_and_the_proposal_says_so() -> None:
    """Each role with its reason — a role that carries none is an assertion."""
    proposal = _compose(MOBILE_ROLE, {**ML_ROLE, "raison": "  "})

    assert [r.role for r in proposal.roles] == ["Développeur mobile"]
    assert "Ingénieur apprentissage automatique" in proposal.composition_reason


def test_an_unknown_template_does_not_become_a_lineage() -> None:
    role = _compose({**MOBILE_ROLE, "gabarit": "astronaute"}).roles[0]

    assert role.role_template == ""
    assert role.playbook_origin == PLAYBOOK_ORIGIN_SKETCH


def test_a_skill_the_tooling_does_not_recommend_is_not_attached() -> None:
    skills = ["lancer-les-tests", "deployer-sur-la-lune"]
    role = _compose({**MOBILE_ROLE, "skills": skills}).roles[0]

    assert [s.name for s in role.skills] == ["lancer-les-tests"]


def test_evidence_the_analysis_has_not_read_is_not_cited() -> None:
    """« Ce qui n'est pas vérifié n'est pas cité »: the path must appear in the findings,
    otherwise the role keeps its reason but loses the invented evidence."""
    role = _compose({**MOBILE_ROLE, "preuve": "ios/Runner/Info.plist"}).roles[0]

    assert role.justification is None
    assert role.reason == MOBILE_ROLE["raison"]


@pytest.mark.parametrize("instances", [0, -2, MAX_CREATED_INSTANCES + 1, "trois"])
def test_out_of_bounds_instances_fall_back_to_one_and_say_so(instances: Any) -> None:
    role = _compose({**MOBILE_ROLE, "instances": instances}).roles[0]

    assert role.instances == 1
    assert "hors des bornes" in role.instances_reason


def test_instances_within_bounds_are_kept_with_their_reason() -> None:
    role = _compose(
        {**MOBILE_ROLE, "instances": 2, "raison_instances": "iOS et Android en parallèle"}
    ).roles[0]

    assert role.instances == 2
    assert role.instances_reason == "iOS et Android en parallèle"


def test_the_orchestrator_is_never_recruited_even_if_the_model_proposes_it() -> None:
    proposal = _compose(
        MOBILE_ROLE,
        {"nom": "orchestrateur", "role": "Orchestrateur", "raison": "il faut coordonner"},
    )

    assert all("orchestrateur" not in r.name for r in proposal.roles)
    assert EXCLUDED_ORCHESTRATOR in proposal.excluded


def test_a_taken_or_reserved_name_is_made_free() -> None:
    findings = _mobile_findings()
    proposal = propose_composed_team(
        findings,
        recommander(findings),
        read_response(
            _json(
                roles=[
                    MOBILE_ROLE,
                    {**MOBILE_ROLE, "role": "Développeur mobile (Android)"},
                    {**MOBILE_ROLE, "nom": "developpeur", "role": "Développeur"},
                ]
            )
        ),
        taken_names=("mobile",),
    )

    names = [r.name for r in proposal.roles]
    assert names == ["mobile-2", "mobile-3", "developpeur-2"]
    assert not set(names) & NOMS_RESERVES


@pytest.mark.parametrize(
    ("name", "role", "template", "expected"),
    [
        ("developpeur", "Développeur backend Python", "developpeur", "dev"),
        ("qa", "QA / Testeur", "qa", "tests"),
        ("devops", "DevOps", "devops", "infra"),
        ("developpeur", "Développeur mobile", "", "developpeur-mobile"),
    ],
)
def test_a_reserved_name_falls_back_on_a_meaningful_name_rather_than_a_suffix(
    name: str, role: str, template: str, expected: str
) -> None:
    """Observed on the real stack (2026-09-24): the model readily names a role like the
    code agent it derives from — `developpeur`, `qa`, `devops` —, names the repository
    reserves. The suffix made them free but unreadable (`developpeur-2`). The role takes
    its template's name, or its label."""
    raw = {
        "nom": name,
        "role": role,
        "raison": "le projet l'appelle",
        "gabarit": template,
        "competences": ["code"],
    }

    (proposed,) = _compose(raw, findings=_mobile_findings()).roles

    assert proposed.name == expected


def test_templates_not_retained_are_excluded_by_name_without_promising_an_action() -> None:
    """A template the model did not retain is named, with the reason it gives — or a
    reason that says it is its judgement. None promises an action (#1159)."""
    proposal = _compose(
        MOBILE_ROLE,
        ecartes=[{"gabarit": "bdd", "raison": "l'application ne stocke rien côté serveur"}],
    )

    excluded = {e.name: e.reason for e in proposal.excluded}
    assert set(excluded) == {"orchestrateur", "dev", "donnees", "infra", "interface", "tests"}
    assert excluded["donnees"] == "l'application ne stocke rien côté serveur"
    assert "ne l'a pas retenu" in excluded["infra"]
    for reason in excluded.values():
        assert "validation" not in reason


@pytest.mark.parametrize(
    "text",
    ["", "Je ne sais pas.", '{"roles": "mobile"}', _json(roles=[{"role": "Sans raison"}])],
    ids=["vide", "prose", "roles-pas-une-liste", "aucun-role-valide"],
)
def test_an_unusable_composition_raises(text: str) -> None:
    """A composition that leaves **no** verified role is not an empty team: it is an
    unusable response, and the service falls back on the rules."""
    with pytest.raises(UnreadableComposition):
        propose_composed_team(
            _mobile_findings(), recommander(_mobile_findings()), read_response(text)
        )


def test_a_composed_role_s_intent_names_its_trade_and_fits_under_the_bound() -> None:
    """#257 writes the playbook from the intent: a label alone (« Sécurité ») does not
    say what the trade is about. The execution regime, which closes it, must survive the
    cut of `ServiceEquipe._playbook`."""
    role = _compose(MOBILE_ROLE).roles[0]

    assert "flutter" in role.intent
    assert role.intent[:INTENTION_MAX] == role.intent


# --------------------------------------------------------------------------- #
# ⑤ The prompt carries the project, the person's words and the register
# --------------------------------------------------------------------------- #


def test_the_instructions_carry_the_register_and_open_the_list_of_roles() -> None:
    instructions = " ".join(COMPOSITION_INSTRUCTIONS.split())

    assert registre() in COMPOSITION_INSTRUCTIONS
    assert "pas une liste fermée" in instructions
    assert "Ne propose jamais l'orchestrateur" in instructions
    # Observed at the review of #1159 on the real stack: reasons spoke of « la personne »
    # in the third person, before the very eyes of that person.
    assert "jamais « la personne »" in instructions


def test_the_composition_prompt_carries_the_findings_the_answers_and_the_material() -> None:
    choices = (Choix(cle="nature", valeur="service-api"),)
    findings = _mobile_findings()

    prompt = composition_prompt(findings, recommander(findings), choices, project_name="Pousse")

    assert "Pousse" in prompt
    assert "Dart" in prompt and "lib/main.dart" in prompt
    assert "nature : service-api" in prompt
    assert "lancer-les-tests" in prompt
    for template in ("developpeur", "bdd", "devops", "designer", "qa"):
        assert template in prompt


def test_the_correction_prompt_carries_the_request_and_the_current_team() -> None:
    findings = _mobile_findings()
    team = (CurrentMember(name="mobile", role="Développeur mobile", retained=True, instances=1),)

    prompt = correction_prompt(
        findings, recommander(findings), (), "ajoute quelqu'un pour la sécurité", team
    )

    assert "ajoute quelqu'un pour la sécurité" in prompt
    assert "mobile" in prompt and "Développeur mobile" in prompt


# --------------------------------------------------------------------------- #
# ④ The natural-language correction
# --------------------------------------------------------------------------- #

SECURITY_ROLE = {
    "nom": "securite",
    "role": "Sécurité applicative",
    "competences": ["securite", "audit", "dependances", "secrets"],
    "raison": "vous demandez quelqu'un pour la sécurité : l'application manipule des "
    "comptes, et ses dépendances comme ses secrets se relisent",
}

MOBILE_TEAM = (
    CurrentMember(name="mobile", role="Développeur mobile", retained=True, instances=1),
    CurrentMember(name="tests", role="QA / Testeur", retained=False, instances=1),
)


def test_add_someone_for_security_is_understood_and_the_role_added() -> None:
    findings = _mobile_findings()
    correction = correct_team(
        findings,
        recommander(findings),
        read_response(
            _json(roles=[SECURITY_ROLE], reponse="J'ajoute un rôle Sécurité applicative.")
        ),
        team=MOBILE_TEAM,
    )

    (security,) = correction.additions
    assert security.name == "securite"
    assert security.role == "Sécurité applicative"
    assert security.playbook.strip()
    assert correction.reply == "J'ajoute un rôle Sécurité applicative."
    assert correction.to_dict()["cree"] is False


def test_a_correction_only_touches_roles_of_the_shown_team() -> None:
    """Removing, restoring, changing the instances: only the names of the shown team are
    admitted — an invented name removes nothing and adds nothing."""
    findings = _mobile_findings()
    correction = correct_team(
        findings,
        recommander(findings),
        read_response(
            _json(
                retraits=["mobile", "fantome"],
                remis=["tests", "fantome"],
                instances={"mobile": 2, "tests": MAX_CREATED_INSTANCES + 5, "fantome": 2},
                reponse="Fait.",
            )
        ),
        team=MOBILE_TEAM,
    )

    assert correction.removals == ("mobile",)
    assert correction.restored == ("tests",)
    assert correction.instances == {"mobile": 2}


def test_an_addition_does_not_reuse_the_name_of_an_already_shown_role() -> None:
    findings = _mobile_findings()
    correction = correct_team(
        findings,
        recommander(findings),
        read_response(_json(roles=[{**SECURITY_ROLE, "nom": "mobile"}], reponse="Ajouté.")),
        team=MOBILE_TEAM,
    )

    assert correction.additions[0].name == "mobile-2"


def test_a_request_not_about_the_team_returns_the_reply_without_changing_anything() -> None:
    """Accepting correction also means saying one has not understood — never guessing."""
    findings = _mobile_findings()
    correction = correct_team(
        findings,
        recommander(findings),
        read_response(_json(reponse="Je n'ai pas compris quel rôle vous voulez changer.")),
        team=MOBILE_TEAM,
    )

    assert correction.additions == () and correction.removals == ()
    assert correction.reply.startswith("Je n'ai pas compris")


def test_a_silent_correction_raises() -> None:
    findings = _mobile_findings()
    with pytest.raises(UnreadableComposition):
        correct_team(findings, recommander(findings), read_response(_json()), team=MOBILE_TEAM)


# --------------------------------------------------------------------------- #
# ⑥ Maestro's browser (#1532): whoever builds a web interface receives it
# --------------------------------------------------------------------------- #


def _web_findings() -> Constats:
    """p5's recipe book: TypeScript and style sheets."""
    return Constats(
        langages=(
            Langage(nom="TypeScript", fichiers=30, part=0.8, exemple="app/page.tsx"),
            Langage(nom="CSS", fichiers=6, part=0.2, exemple="app/globals.css"),
        ),
        commandes=(
            Commande(
                usage="tester", commande="npm test", chemin="package.json", origine="declaree"
            ),
        ),
    )


def _web_role(name: str, role: str, template: str, browser: Any) -> dict[str, Any]:
    return {
        "nom": name,
        "role": role,
        "raison": f"le livre de recettes a des pages (app/page.tsx) : {role} y travaille",
        "gabarit": template,
        "navigateur": browser,
    }


WEB_TEAM = (
    _web_role("dev", "Développeur Next.js", "developpeur", True),
    _web_role("charte", "Designer UI", "designer", True),
    _web_role("tests", "Testeur Vitest", "qa", True),
    _web_role("ci", "Intégration continue", "devops", False),
)


def test_a_team_building_a_web_interface_receives_the_browser() -> None:
    """The ticket's criterion: the model judges who watches the screen, and the proposal
    carries it — role by role, in the shape the screen receives."""
    proposal = _compose(*WEB_TEAM, findings=_web_findings())

    by_name = {role.name: role.browser for role in proposal.roles}
    assert by_name == {"dev": True, "charte": True, "tests": True, "ci": False}
    served = {role["nom"]: role["navigateur"] for role in proposal.to_dict()["roles"]}
    assert served == by_name
    # The person reads its name and its usage, served once: the face of the card and a
    # role's line say the same sentence (variant B retained on #1532).
    assert proposal.to_dict()["navigateur_de_maestro"] == {
        "libelle": "Navigateur de Maestro",
        "usage": "ouvrir et parcourir une page, la capturer à une largeur donnée, "
        "émuler la réduction des animations, lire les erreurs de la console",
    }
    # One more tool in the role's hands — its tooled profile does not move.
    dev = next(r for r in proposal.roles if r.name == "dev")
    assert not any("navigateur" in tool for tool in dev.tools)


def test_a_team_without_interface_does_not_receive_the_browser() -> None:
    """A learning pipeline has nothing to watch in a browser: the model does not give it,
    and a role that says nothing receives none."""
    proposal = _compose(ML_ROLE, findings=_ml_findings())
    assert [role.browser for role in proposal.roles] == [False]


@pytest.mark.parametrize("raw", ["true", "oui", 1, None])
def test_a_browser_outside_the_contract_is_not_given(raw: Any) -> None:
    """« oui » spelled out is not a decision one reads: outside the contract, the role
    receives nothing — never guessed."""
    proposal = _compose(
        _web_role("dev", "Développeur Next.js", "developpeur", raw), findings=_web_findings()
    )
    assert proposal.roles[0].browser is False


def test_the_instructions_name_the_browser_and_say_who_receives_it() -> None:
    instructions = " ".join(COMPOSITION_INSTRUCTIONS.split())
    assert "Maestro a un navigateur" in instructions
    assert '"navigateur": true' in instructions
    assert "Un projet sans interface web n'en a pas besoin" in instructions
    # The correction contract carries it too: take away or give, in words.
    assert '"navigateur": {"nom d\'un rôle de l\'équipe montrée": false}' in instructions


SHOWN_WEB_TEAM = (
    CurrentMember(name="dev", role="Développeur Next.js", browser=True),
    CurrentMember(name="tests", role="Testeur Vitest", browser=True),
    CurrentMember(name="ci", role="Intégration continue"),
)


def test_take_the_browser_from_the_tester_is_understood() -> None:
    """The person takes it away **in words**: the model knows who has it (the shown team
    says so), and the correction names only shown roles, with a strict boolean."""
    findings = _web_findings()
    prompt = correction_prompt(
        findings, recommander(findings), (), "retire le navigateur au testeur", SHOWN_WEB_TEAM
    )
    assert "- tests — Testeur Vitest (a le navigateur de Maestro)" in prompt
    assert "- ci — Intégration continue\n" in prompt

    correction = correct_team(
        findings,
        recommander(findings),
        read_response(
            _json(navigateur={"tests": False, "inconnu": False, "dev": "non"}, reponse="")
        ),
        team=SHOWN_WEB_TEAM,
    )

    assert correction.browser == {"tests": False}
    assert correction.to_dict()["navigateur"] == {"tests": False}
    # Silent, the model still changed the team: the default sentence says so.
    assert "navigateur de Maestro retiré à : tests" in correction.reply


def test_give_the_browser_to_continuous_integration_is_understood() -> None:
    findings = _web_findings()
    correction = correct_team(
        findings,
        recommander(findings),
        read_response(_json(navigateur={"ci": True}, reponse="C'est fait.")),
        team=SHOWN_WEB_TEAM,
    )
    assert correction.browser == {"ci": True}


def test_a_misplaced_browser_in_a_composition_does_not_bring_it_down() -> None:
    """Set at the wrong level, it is ignored: the team is not lost for all that."""
    response = read_response(_json(roles=[MOBILE_ROLE], navigateur=True))
    assert response.browser == {}
    assert response.roles


def test_the_fallback_rules_give_the_browser_to_whoever_watches_the_screen() -> None:
    """When the model does not answer, the rules give it to the roles that write, design
    or verify an observed interface — to them alone."""
    web = propose_team(_web_findings(), recommander(_web_findings()), project_id=PROJECT)
    assert {role.name: role.browser for role in web.roles} == {
        "dev": True,
        "interface": True,
        "tests": True,
    }

    ml = propose_team(_ml_findings(), recommander(_ml_findings()), project_id=PROJECT)
    assert not any(role.browser for role in ml.roles)


def test_a_new_project_declared_web_application_receives_the_browser_by_the_rules() -> None:
    """On a new project, no file proves the interface yet: the answer says it."""
    choices = (Choix(cle="nature", valeur="application-web"), Choix(cle="langages", valeur="ts"))
    findings = constats_depuis_choix(choices)
    proposal = propose_team(findings, recommandation_depuis_choix(choices), choices=choices)
    assert any(role.browser for role in proposal.roles)


# --------------------------------------------------------------------------- #
# The service: the model first, the rules as fallback, stated as such
# --------------------------------------------------------------------------- #


@pytest.fixture()
def workshop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Under a fake home folder: `valider_racine` refuses `AppData` (#221)."""
    home = tmp_path / "maison"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    workshop = tmp_path / "atelier"
    workshop.mkdir()
    return workshop


@pytest.fixture()
def agent_config(tmp_path: Path) -> ConfigurationAgents:
    root = tmp_path / "core"
    return ConfigurationAgents(
        agents=AgentStore(root / "agents"),
        surcharges=SurchargeStore(root / "surcharges"),
        playbooks=PlaybookStore(root / "playbooks"),
        permissions=PermissionStore(root / "permissions"),
        mcp=McpStore(root / "mcp"),
        capacites=CapacityStore(root / "capacite"),
    )


def _flutter_project(workshop: Path, projects: ServiceProjets) -> str:
    root = workshop / "pousse"
    (root / "lib").mkdir(parents=True)
    (root / "lib" / "main.dart").write_text("void main() {}\n", encoding="utf-8")
    (root / "pubspec.yaml").write_text("name: pousse\n", encoding="utf-8")
    return str(projects.creer("pousse", str(root), origine="existant")["id"])


def _service(
    tmp_path: Path,
    workshop: Path,
    agent_config: ConfigurationAgents,
    provider: ModelProvider,
    generator: GenerateurDefinitionAgent | None = None,
) -> tuple[ServiceEquipe, str]:
    projects = ServiceProjets(ProjetStore(tmp_path / "depot"), racines_exploration=(workshop,))
    project = _flutter_project(workshop, projects)
    service = ServiceEquipe(
        projects,
        agent_config,
        generateur=generator or _OfflineGenerator(),
        compositeur=CompositeurEquipe(provider=provider),
    )
    return service, project


def test_the_service_composes_the_team_through_the_model(
    tmp_path: Path, workshop: Path, agent_config: ConfigurationAgents
) -> None:
    provider = _ScriptedProvider(_json(roles=[{**MOBILE_ROLE, "preuve": "lib/main.dart"}]))
    generator = _ScriptedGenerator()
    service, project = _service(tmp_path, workshop, agent_config, provider, generator)

    served = asyncio.run(service.proposer(project))

    assert served["composition"]["origine"] == COMPOSITION_ORIGIN_MODEL
    (mobile,) = served["roles"]
    assert mobile["role"] == "Développeur mobile"
    assert mobile["justification"]["chemin"] == "lib/main.dart"
    # The playbook is written for this project, from the composed role's intent.
    assert mobile["playbook_origine"] == PLAYBOOK_ORIGIN_GENERATED
    assert "Développeur mobile" in generator.intents[0]
    # The model received the project actually analysed, under the instructions that
    # carry the register.
    prompt, system = provider.requests[0]
    assert "Dart" in prompt
    assert system == COMPOSITION_INSTRUCTIONS


#: What the questionnaire understood of S9's songbook (#1350) — the material its tooling
#: was written from, and which its manifest keeps.
SONGBOOK_CHOICES = (
    Choix(
        "nature",
        "Le carnet de chants d'une chorale : un fichier texte par chant, assemblé avec le "
        "sommaire des titres",
        deduit=True,
    ),
    Choix("langages", "Python", deduit=True),
    Choix(
        "construire",
        "python assembler.py",
        deduit=True,
        pour="Assembler le carnet à partir des chants, sommaire des titres compris",
    ),
    Choix(
        "tester",
        "python -m unittest",
        deduit=True,
        pour="Vérifier que le carnet est complet et que le sommaire nomme chaque chant",
    ),
    Choix("ci", "aucun", deduit=True),
)


def _project_born_in_chat(
    workshop: Path, projects: ServiceProjets, *, with_own_files: bool = False
) -> str:
    """A new project tooled from its answers: on disk, nothing but the tooling.

    This is the state in which the chat proposes the team (S9): `AGENTS.md`, the skills
    and the manifest — what Maestro wrote —, and no file of the project itself.
    """
    root = workshop / "carnet"
    project_id = str(projects.creer("carnet-de-chants", str(root), origine="nouveau")["id"])
    choices = list(SONGBOOK_CHOICES)
    generer(
        root,
        rediger(constats_depuis_choix(choices), recommandation_depuis_choix(choices)),
        source=source_manifeste_des_choix(project_id, choices),
    )
    if with_own_files:
        (root / "chants").mkdir()
        (root / "chants" / "ave-verum.txt").write_text("Ave verum corpus\n", encoding="utf-8")
    return project_id


def _songbook_service(
    tmp_path: Path,
    workshop: Path,
    agent_config: ConfigurationAgents,
    provider: ModelProvider,
    *,
    with_own_files: bool = False,
) -> tuple[ServiceEquipe, str]:
    projects = ServiceProjets(ProjetStore(tmp_path / "depot"), racines_exploration=(workshop,))
    project = _project_born_in_chat(workshop, projects, with_own_files=with_own_files)
    service = ServiceEquipe(
        projects,
        agent_config,
        generateur=_OfflineGenerator(),
        compositeur=CompositeurEquipe(provider=provider),
    )
    return service, project


def test_a_still_empty_project_composes_its_team_on_what_it_is(
    tmp_path: Path, workshop: Path, agent_config: ConfigurationAgents
) -> None:
    """#1350: the songbook's team speaks of the songbook, not of a repository that « se
    construit et se teste ».

    The chat asks for the team **without answers** (the card has none): without them, the
    project was analysed, and its disk held only Maestro's tooling — the model knew of
    the songbook only its name, and its reasons held for any repository. A project that
    has no file of its own yet is **described** (the tooling rule in the chat): its team
    is composed on the answers its manifest keeps.
    """
    provider = _ScriptedProvider(_json(roles=[MOBILE_ROLE]))
    service, project = _songbook_service(tmp_path, workshop, agent_config, provider)

    asyncio.run(service.proposer(project))

    prompt, _ = provider.requests[0]
    assert "un projet neuf, décrit par les réponses de la personne" in prompt
    assert "nature : Le carnet de chants d'une chorale" in prompt
    # What each skill does for this project, so that the roles attach to it.
    assert "Assembler le carnet à partir des chants" in prompt


def test_a_project_with_its_own_files_composes_its_team_on_its_reading(
    tmp_path: Path, workshop: Path, agent_config: ConfigurationAgents
) -> None:
    """The counterpart: as soon as the project has its files, they say what it is."""
    provider = _ScriptedProvider(_json(roles=[MOBILE_ROLE]))
    service, project = _songbook_service(
        tmp_path, workshop, agent_config, provider, with_own_files=True
    )

    asyncio.run(service.proposer(project))

    prompt, _ = provider.requests[0]
    assert "un projet existant, lu par l'analyse" in prompt
    assert "nature :" not in prompt


#: The two skills the songbook's tooling writes — its build and its tests.
SONGBOOK_SKILLS = {
    "construire-le-projet": ".agents/skills/construire-le-projet/SKILL.md",
    "lancer-les-tests": ".agents/skills/lancer-les-tests/SKILL.md",
}


def _served_skills(served: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The skills the proposed roles attach, by name — as the card reads them."""
    return {skill["nom"]: skill for role in served["roles"] for skill in role["skills"]}


def _execution(served: dict[str, Any], name: str) -> dict[str, Any]:
    """The execution permission of role `name`, as the card unfolds it."""
    role = next(r for r in served["roles"] if r["nom"] == name)
    (execution,) = role["autorisations"]
    return execution


def test_a_skill_the_tooling_wrote_is_said_written_on_the_card(
    tmp_path: Path, workshop: Path, agent_config: ConfigurationAgents
) -> None:
    """#1591: the team of a described project is derived from its answers, without looking
    at the disk.

    On the herbarium's `verify` walkthrough, the tooling's pieces had just been written on
    agreement, and the team card said « mettre-en-route (à générer) »: the recommendation
    drawn from the answers does not know what the project holds, so every entry came out
    `a-generer`. What the project holds is written.
    """
    service, project = _songbook_service(tmp_path, workshop, agent_config, _FailingProvider())

    served = asyncio.run(service.proposer(project))

    skills = _served_skills(served)
    assert {name: s["chemin"] for name, s in skills.items()} == SONGBOOK_SKILLS
    for skill in skills.values():
        assert (workshop / "carnet" / skill["chemin"]).is_file()
        assert skill["etat"] == "deja-present", skill["nom"]


def test_a_skill_the_project_does_not_hold_stays_to_generate(
    tmp_path: Path, workshop: Path, agent_config: ConfigurationAgents
) -> None:
    """The counterpart of #1591: « à générer » stays only on a skill absent from the disk."""
    service, project = _songbook_service(tmp_path, workshop, agent_config, _FailingProvider())
    (workshop / "carnet" / SONGBOOK_SKILLS["lancer-les-tests"]).unlink()

    served = asyncio.run(service.proposer(project))

    skills = _served_skills(served)
    assert skills["lancer-les-tests"]["etat"] == "a-generer"
    assert skills["construire-le-projet"]["etat"] == "deja-present"
    # Its commands are written nowhere: the reason says so, as before.
    assert "aucune n'a pu être lue" in _execution(served, "tests")["raison"]


def test_the_tier_reason_names_the_skills_where_the_tooling_wrote_the_commands(
    tmp_path: Path, workshop: Path, agent_config: ConfigurationAgents
) -> None:
    """#1591: « Aucune commande n'est encore écrite dans le projet » was false.

    The role's commands had just been written in the skills of the accepted tooling. The
    reason says where they are; the tier itself does not move (#1226).
    """
    service, project = _songbook_service(tmp_path, workshop, agent_config, _FailingProvider())

    served = asyncio.run(service.proposer(project))

    execution = _execution(served, "tests")
    assert SONGBOOK_SKILLS["lancer-les-tests"] in execution["raison"]
    assert "aucune n'a pu être lue" not in execution["raison"]
    assert "ne le borne pas à celles-là" in execution["raison"]
    assert (execution["cran"], execution["decideur"], execution["portee"]) == (
        "ask",
        str(Decideur.AUTO),
        PORTEE_PROJET,
    )


def test_the_instructions_ask_for_reasons_that_hold_only_for_this_project() -> None:
    """#1350: « une phrase : pourquoi ce projet appelle ce rôle » let through a reason that
    held for any repository — S9's judge read it as such."""
    instructions = " ".join(COMPOSITION_INSTRUCTIONS.split())
    assert "ce que ce rôle fera DANS CE projet" in instructions
    assert "vaudrait pour n'importe quel dépôt" in instructions


def test_a_failing_model_falls_back_on_the_rules_and_says_so(
    tmp_path: Path, workshop: Path, agent_config: ConfigurationAgents
) -> None:
    """③ A whole team lost because a quota is exhausted would be worse than a team by
    rules — provided it says so, with its cause."""
    service, project = _service(tmp_path, workshop, agent_config, _FailingProvider())

    served = asyncio.run(service.proposer(project))

    assert served["composition"]["origine"] == COMPOSITION_ORIGIN_RULES
    assert "quota épuisé" in served["composition"]["raison"]
    assert [r["nom"] for r in served["roles"]] == ["dev", "tests"]


def test_a_sketch_playbook_that_fails_to_be_written_stays_a_sketch(
    tmp_path: Path, workshop: Path, agent_config: ConfigurationAgents
) -> None:
    """The #257 fallback keeps the fallback's origin: a sketch does not become a
    « playbook du gabarit » because writing failed."""
    service, project = _service(
        tmp_path, workshop, agent_config, _ScriptedProvider(_json(roles=[MOBILE_ROLE]))
    )

    (mobile,) = asyncio.run(service.proposer(project))["roles"]

    assert mobile["playbook_origine"] == PLAYBOOK_ORIGIN_SKETCH
    assert "hors ligne" in mobile["playbook_raison"]


def test_the_service_adds_the_requested_role_with_its_playbook(
    tmp_path: Path, workshop: Path, agent_config: ConfigurationAgents
) -> None:
    """④ The ticket's criterion, through the service: understood, added, playbook written."""
    provider = _ScriptedProvider(_json(roles=[SECURITY_ROLE], reponse="J'ajoute la sécurité."))
    generator = _ScriptedGenerator()
    service, project = _service(tmp_path, workshop, agent_config, provider, generator)

    served = asyncio.run(
        service.corriger(project, "ajoute quelqu'un pour la sécurité", MOBILE_TEAM)
    )

    (security,) = served["ajouts"]
    assert security["role"] == "Sécurité applicative"
    assert security["playbook_origine"] == PLAYBOOK_ORIGIN_GENERATED
    assert "Sécurité applicative" in security["playbook"]
    assert served["reponse"] == "J'ajoute la sécurité."
    assert "ajoute quelqu'un pour la sécurité" in provider.requests[0][0]


@pytest.mark.parametrize("user_request", ["", "   ", "x" * (MAX_REQUEST + 1)])
def test_an_empty_or_too_long_request_is_refused_before_any_call(
    tmp_path: Path, workshop: Path, agent_config: ConfigurationAgents, user_request: str
) -> None:
    provider = _ScriptedProvider()
    service, project = _service(tmp_path, workshop, agent_config, provider)

    with pytest.raises(ValueError):
        asyncio.run(service.corriger(project, user_request, MOBILE_TEAM))
    assert provider.requests == []


# --------------------------------------------------------------------------- #
# ④ Over HTTP — the exact call of the chat's team card (#1331; the team step of
# #1159, which made it before it, is removed)
# --------------------------------------------------------------------------- #


@pytest.fixture()
def http_client(
    tmp_path: Path, workshop: Path, agent_config: ConfigurationAgents
) -> Iterator[tuple[TestClient, _ScriptedProvider]]:
    provider = _ScriptedProvider()
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=ServiceProjets(ProjetStore(tmp_path / "depot"), racines_exploration=(workshop,)),
        agents_store=agent_config.agents,
        surcharges=agent_config.surcharges,
        playbooks=agent_config.playbooks,
        permissions=agent_config.permissions,
        mcp=agent_config.mcp,
        capacites=agent_config.capacites,
        generateur_agent=_OfflineGenerator(),
        compositeur_equipe=CompositeurEquipe(provider=provider),
    )
    with TestClient(app) as client:
        yield client, provider


def _declare_flutter(client: TestClient, workshop: Path) -> str:
    root = workshop / "pousse"
    (root / "lib").mkdir(parents=True)
    (root / "lib" / "main.dart").write_text("void main() {}\n", encoding="utf-8")
    response = client.post(
        "/api/projets", json={"nom": "pousse", "racine": str(root), "origine": "existant"}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_the_correction_route_adds_the_requested_role(
    http_client: tuple[TestClient, _ScriptedProvider], workshop: Path
) -> None:
    client, provider = http_client
    project = _declare_flutter(client, workshop)
    provider._replies.append(_json(roles=[SECURITY_ROLE], reponse="J'ajoute la sécurité."))

    response = client.post(
        f"/api/projets/{project}/equipe/correction",
        json={
            "demande": "ajoute quelqu'un pour la sécurité",
            "equipe": [
                {"nom": "mobile", "role": "Développeur mobile", "retenu": True, "instances": 1}
            ],
        },
    )

    assert response.status_code == 200, response.text
    served = response.json()
    assert [a["nom"] for a in served["ajouts"]] == ["securite"]
    assert served["ajouts"][0]["playbook"].strip()
    assert served["cree"] is False


def test_the_correction_route_removes_the_designer_and_changes_the_instances(
    http_client: tuple[TestClient, _ScriptedProvider], workshop: Path
) -> None:
    """The two other actions of #1331's criterion, through the call the card makes: the
    shown team (ticks included), no questionnaire answer."""
    client, provider = http_client
    project = _declare_flutter(client, workshop)
    provider._replies.append(
        _json(
            retraits=["interface"],
            instances={"mobile": 2},
            reponse="Je retire le designer et je passe à deux développeurs.",
        )
    )

    response = client.post(
        f"/api/projets/{project}/equipe/correction",
        json={
            "demande": "retire le designer, et deux développeurs",
            "equipe": [
                {"nom": "mobile", "role": "Développeur mobile", "retenu": True, "instances": 1},
                {"nom": "interface", "role": "Designer", "retenu": True, "instances": 1},
            ],
            "choix": [],
        },
    )

    assert response.status_code == 200, response.text
    served = response.json()
    assert served["retraits"] == ["interface"]
    assert served["instances"] == {"mobile": 2}
    assert served["ajouts"] == []
    assert served["cree"] is False
    # The model received the team as the card showed it.
    prompt = provider.requests[0][0]
    assert "retire le designer, et deux développeurs" in prompt
    assert "Designer" in prompt


def test_the_correction_route_refuses_an_empty_request(
    http_client: tuple[TestClient, _ScriptedProvider], workshop: Path
) -> None:
    client, _ = http_client
    project = _declare_flutter(client, workshop)

    response = client.post(
        f"/api/projets/{project}/equipe/correction", json={"demande": " ", "equipe": []}
    )

    assert response.status_code == 422


def test_the_correction_route_takes_the_browser_from_a_shown_role(
    http_client: tuple[TestClient, _ScriptedProvider], workshop: Path
) -> None:
    """#1532: « retire le navigateur au testeur », through the call the card makes — the
    model knows who has it, since the shown team says so."""
    client, provider = http_client
    project = _declare_flutter(client, workshop)
    provider._replies.append(
        _json(navigateur={"tests": False}, reponse="Le testeur n'a plus le navigateur.")
    )

    response = client.post(
        f"/api/projets/{project}/equipe/correction",
        json={
            "demande": "retire le navigateur au testeur",
            "equipe": [
                {"nom": "dev", "role": "Développeur", "navigateur": True},
                {"nom": "tests", "role": "QA", "navigateur": True},
            ],
        },
    )

    assert response.status_code == 200, response.text
    served = response.json()
    assert served["navigateur"] == {"tests": False}
    assert served["retraits"] == []
    assert "tests — QA (a le navigateur de Maestro)" in provider.requests[0][0]


def test_the_correction_route_returns_502_when_the_model_does_not_answer(
    tmp_path: Path, workshop: Path, agent_config: ConfigurationAgents
) -> None:
    """A correction does not fall back on rules: there is no rule for « ajoute quelqu'un
    pour la sécurité ». It fails plainly, and the screen says so."""
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=ServiceProjets(ProjetStore(tmp_path / "depot"), racines_exploration=(workshop,)),
        agents_store=agent_config.agents,
        generateur_agent=_OfflineGenerator(),
        compositeur_equipe=CompositeurEquipe(provider=_FailingProvider()),
    )
    with TestClient(app) as client:
        project = _declare_flutter(client, workshop)
        response = client.post(
            f"/api/projets/{project}/equipe/correction",
            json={"demande": "ajoute quelqu'un pour la sécurité", "equipe": []},
        )

    assert response.status_code == 502
    assert "quota épuisé" in response.text


# --------------------------------------------------------------------------- #
# ⑦ What the person has set about the project, their playbooks follow (#1521)
# --------------------------------------------------------------------------- #

#: The herbarium's answer from the assessment, as the person typed it: a restriction
#: bounded to the generator, and pytest for the tests.
HERBARIUM_ANSWER = (
    "Python, avec la bibliothèque standard seulement pour le générateur. "
    "Les tests avec pytest."
)

HERBARIUM_CHOICES = (
    Choix(
        "nature",
        "Un herbier de randonnées : une fiche Markdown par plante, un index par région généré",
        deduit=True,
    ),
    Choix("langages", HERBARIUM_ANSWER, libre=True),
    Choix(
        "tester",
        ".venv/Scripts/python -m pytest",
        deduit=True,
        pour="Vérifier que l'index nomme chaque fiche",
    ),
)

TESTER_ROLE = {
    "nom": "testeur",
    "role": "Testeur",
    "competences": ["tests", "pytest"],
    "raison": "vos réponses prévoient pytest pour vérifier le générateur d'index",
    "gabarit": "qa",
}

#: The assessment's two playbooks, as the card showed them: each contradicts « Les tests
#: avec pytest », and the person named only the tester.
TESTER_PLAYBOOK = (
    "Tu écris les tests de l'herbier. Utilise `unittest` de la bibliothèque standard. "
    "N'introduis aucune dépendance externe : ni pytest ni bibliothèque tierce."
)
DEV_PLAYBOOK = (
    "Tu écris le générateur d'index. N'ajoute aucune dépendance tierce, même pour les "
    "tests. Utilise `unittest` sauf si le projet utilise déjà autre chose."
)
HERBARIUM_TEAM = (
    CurrentMember(name="dev", role="Développeur", playbook=DEV_PLAYBOOK),
    CurrentMember(name="testeur", role="Testeur", playbook=TESTER_PLAYBOOK),
)
PYTEST_REQUEST = "le testeur écrit ses tests avec pytest, comme je l'ai dit, pas avec unittest"


def test_the_project_facts_take_the_answers_as_they_are() -> None:
    """A paraphrase would widen the restriction: « seulement pour le générateur » would be
    lost. The answers go through word for word, and what a command does as well."""
    facts = project_facts(constats_depuis_choix(HERBARIUM_CHOICES), HERBARIUM_CHOICES)

    assert f"- langages : {HERBARIUM_ANSWER}" in facts
    assert (
        "- tester : .venv/Scripts/python -m pytest — Vérifier que l'index nomme chaque fiche"
        in facts
    )


def test_without_answers_the_facts_are_the_commands_the_analysis_read() -> None:
    findings = Constats(
        commandes=(
            Commande(
                usage="tests", commande="pytest -q", chemin="pyproject.toml", origine="declaree"
            ),
        )
    )

    assert project_facts(findings) == "- commande de « tests » : `pytest -q` (pyproject.toml)"
    assert project_facts(Constats()) == ""


def test_each_role_s_playbook_receives_what_the_person_said_of_the_project(
    tmp_path: Path, workshop: Path, agent_config: ConfigurationAgents
) -> None:
    """The assessment's defect: the writer received only « un projet écrit en Python », and
    imposed `unittest` on the tester of someone who had answered « Les tests avec pytest »."""
    generator = _ScriptedGenerator()
    service, project = _service(
        tmp_path,
        workshop,
        agent_config,
        _ScriptedProvider(_json(roles=[TESTER_ROLE])),
        generator,
    )

    (tester,) = asyncio.run(service.proposer(project, HERBARIUM_CHOICES))["roles"]

    (facts,) = generator.facts
    assert HERBARIUM_ANSWER in facts
    assert tester["playbook_origine"] == PLAYBOOK_ORIGIN_GENERATED
    # The reason says what fed the playbook: it can be contested on an answer.
    assert "vos réponses, qu'il ne contredit pas" in tester["playbook_raison"]


def test_the_writer_puts_the_facts_in_its_prompt_under_instructions_that_make_them_hold() -> None:
    writer = _ScriptedProvider(
        "NOM: testeur\nROLE: Testeur\nCOMPETENCES: tests, pytest\nFOURNISSEUR:\nMODELE:\n"
        f"{MARQUEUR_PLAYBOOK}\nTu écris les tests avec pytest.\n"
    )
    generator = GenerateurDefinitionAgent(provider=writer, fournisseurs=lambda: ())

    asyncio.run(
        generator.proposer("Un agent « Testeur »", faits=f"- langages : {HERBARIUM_ANSWER}")
    )

    prompt, instructions = writer.requests[0]
    assert HERBARIUM_ANSWER in prompt
    flat = " ".join((instructions or "").split())
    assert "n'y écris aucune règle qui contredise l'une de ses réponses" in flat
    assert "ne prête pas à tout le projet une restriction qu'elle a bornée à une partie" in flat


def test_without_facts_the_writer_prompt_is_unchanged() -> None:
    writer = _ScriptedProvider(
        f"NOM: a\nROLE: A\nCOMPETENCES: a\nFOURNISSEUR:\nMODELE:\n{MARQUEUR_PLAYBOOK}\nTu fais A.\n"
    )
    generator = GenerateurDefinitionAgent(provider=writer, fournisseurs=lambda: ())

    asyncio.run(generator.proposer("Un agent « A »"))

    assert "Ce que la personne a dit de son projet" not in writer.requests[0][0]


def test_the_correction_prompt_carries_the_playbooks_the_card_shows() -> None:
    """Without them, Maestro asserted what an agent would do without having read its
    playbook."""
    findings = constats_depuis_choix(HERBARIUM_CHOICES)
    team = (*HERBARIUM_TEAM, CurrentMember(name="doc", role="Rédacteur"))

    prompt = correction_prompt(
        findings, recommandation_depuis_choix(HERBARIUM_CHOICES), (), PYTEST_REQUEST, team
    )

    assert TESTER_PLAYBOOK in prompt and DEV_PLAYBOOK in prompt
    # A missing playbook is stated: the model knows it does not know what it prescribes.
    assert "### doc — Rédacteur\n\n(playbook non transmis" in prompt


def test_the_instructions_ask_to_reread_each_playbook_and_assert_only_what_is_read() -> None:
    instructions = " ".join(COMPOSITION_INSTRUCTIONS.split())
    assert "relis le playbook de CHAQUE rôle montré" in instructions
    assert "celui qu'elle cite comme les autres" in instructions
    assert "Ne dis jamais ce qu'un playbook prescrit, ni ce qu'un agent fera" in instructions


def test_an_instruction_targets_only_a_shown_role_and_says_what_to_follow() -> None:
    findings = _mobile_findings()
    correction = correct_team(
        findings,
        recommander(findings),
        read_response(
            _json(playbooks={"dev": " suivre  pytest ", "fantome": "pytest", "testeur": "  "})
        ),
        team=HERBARIUM_TEAM,
    )

    assert correction.instructions == {"dev": "suivre pytest"}
    # The model said nothing: the default reply names what will be rewritten.
    assert "playbook à réécrire pour suivre votre demande : dev" in correction.reply


def test_a_convention_corrected_in_words_reaches_every_role_that_contradicts_it(
    tmp_path: Path, workshop: Path, agent_config: ConfigurationAgents
) -> None:
    """The ticket's criterion: the person names the tester, the developer also says
    « même pour les tests… `unittest` » — both are rewritten, at the first attempt,
    without replacing any role."""
    provider = _ScriptedProvider(
        _json(
            playbooks={
                "testeur": "écrire et lancer les tests avec pytest",
                "dev": "pytest est la dépendance de test du projet ; le générateur reste "
                "en bibliothèque standard",
            },
            reponse="Le testeur et le développeur suivent désormais pytest pour les tests.",
        )
    )
    generator = _ScriptedGenerator()
    service, project = _service(tmp_path, workshop, agent_config, provider, generator)

    served = asyncio.run(
        service.corriger(project, PYTEST_REQUEST, HERBARIUM_TEAM, HERBARIUM_CHOICES)
    )

    assert set(served["playbooks"]) == {"dev", "testeur"}
    assert served["retraits"] == [] and served["ajouts"] == []
    tester = served["playbooks"]["testeur"]
    assert tester["playbook"].startswith(TESTER_PLAYBOOK)
    assert "Réécrit : écrire et lancer les tests avec pytest" in tester["playbook"]
    assert tester["playbook_origine"] == PLAYBOOK_ORIGIN_GENERATED
    assert PYTEST_REQUEST in tester["playbook_raison"]
    # The writer received the shown playbook and the person's sentence.
    assert {(p, r) for p, r, _ in generator.corrections} == {
        (TESTER_PLAYBOOK, PYTEST_REQUEST),
        (DEV_PLAYBOOK, PYTEST_REQUEST),
    }
    # The correction model read the playbooks before answering.
    assert DEV_PLAYBOOK in provider.requests[0][0]


def test_a_playbook_that_is_not_rewritten_stays_as_is_and_the_reply_says_so(
    tmp_path: Path, workshop: Path, agent_config: ConfigurationAgents
) -> None:
    """Never « c'est réglé » of a role that is not: a rewrite that fails, or a playbook the
    card did not send, is named in the reply."""
    provider = _ScriptedProvider(
        _json(
            playbooks={"testeur": "pytest", "dev": "pytest", "doc": "pytest"},
            reponse="Les trois rôles suivent pytest.",
        )
    )
    generator = _ScriptedGenerator(failing=("générateur d'index",))
    service, project = _service(tmp_path, workshop, agent_config, provider, generator)
    team = (*HERBARIUM_TEAM, CurrentMember(name="doc", role="Rédacteur"))

    served = asyncio.run(service.corriger(project, PYTEST_REQUEST, team))

    assert set(served["playbooks"]) == {"testeur"}
    assert served["reponse"].startswith("Les trois rôles suivent pytest.")
    assert (
        "le playbook de « dev » n'a pas pu être réécrit (quota épuisé (test))" in served["reponse"]
    )
    assert (
        "le playbook de « doc » n'a pas pu être réécrit (son texte n'a pas été transmis"
        in served["reponse"]
    )
    assert "il reste tel qu'il était" in served["reponse"]


def test_a_role_added_by_the_request_follows_what_it_just_set(
    tmp_path: Path, workshop: Path, agent_config: ConfigurationAgents
) -> None:
    provider = _ScriptedProvider(_json(roles=[TESTER_ROLE], reponse="J'ajoute un testeur."))
    generator = _ScriptedGenerator()
    service, project = _service(tmp_path, workshop, agent_config, provider, generator)

    served = asyncio.run(service.corriger(project, "ajoute un testeur qui écrit en pytest", ()))

    assert "ajoute un testeur qui écrit en pytest" in generator.facts[0]
    assert "votre demande, qu'il ne contredit pas" in served["ajouts"][0]["playbook_raison"]


def test_the_writer_rewrites_a_playbook_on_the_request_and_the_instruction() -> None:
    writer = _ScriptedProvider(
        f"J'ai remplacé unittest par pytest.\n{MARQUEUR_PLAYBOOK}\n"
        "Tu écris les tests avec pytest.\n"
    )
    generator = GenerateurDefinitionAgent(provider=writer, fournisseurs=lambda: ())

    rewritten = asyncio.run(
        generator.appliquer(TESTER_PLAYBOOK, demande=PYTEST_REQUEST, consigne="pytest")
    )

    # The model's announcement does not enter the agent's prompt.
    assert rewritten == "Tu écris les tests avec pytest."
    prompt, instructions = writer.requests[0]
    assert PYTEST_REQUEST in prompt and TESTER_PLAYBOOK in prompt
    assert prompt.rstrip().endswith("selon le format demandé.")
    assert "sans rien changer d'autre" in " ".join((instructions or "").split())


def test_the_correction_route_returns_the_rewritten_playbooks(
    tmp_path: Path, workshop: Path, agent_config: ConfigurationAgents
) -> None:
    """The exact call of the chat's card: each member carries its playbook."""
    provider = _ScriptedProvider(
        _json(playbooks={"testeur": "pytest", "dev": "pytest"}, reponse="Réécrits.")
    )
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=ServiceProjets(ProjetStore(tmp_path / "depot"), racines_exploration=(workshop,)),
        agents_store=agent_config.agents,
        generateur_agent=_ScriptedGenerator(),
        compositeur_equipe=CompositeurEquipe(provider=provider),
    )
    with TestClient(app) as client:
        project = _declare_flutter(client, workshop)
        response = client.post(
            f"/api/projets/{project}/equipe/correction",
            json={
                "demande": PYTEST_REQUEST,
                "equipe": [
                    {"nom": "dev", "role": "Développeur", "playbook": DEV_PLAYBOOK},
                    {"nom": "testeur", "role": "Testeur", "playbook": TESTER_PLAYBOOK},
                ],
            },
        )

    assert response.status_code == 200, response.text
    served = response.json()
    assert set(served["playbooks"]) == {"dev", "testeur"}
    assert served["playbooks"]["dev"]["playbook"].startswith(DEV_PLAYBOOK)
    assert TESTER_PLAYBOOK in provider.requests[0][0]
