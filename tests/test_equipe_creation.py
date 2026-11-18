"""The validated team is created in the project — or nothing is (#1040).

Final lot of the #1021 effort (#1043) — lot 4 had shipped without tests, under the
splitting convention ([docs/10 §5.1](../docs/10-workflow-git.md)).

The proposal creates nothing (#1039); this one creates everything, and **the order** is
what guarantees it. Three promises, and these tests take them one by one:

① **what is created is what was shown.** `ValidatedRole` is deliberately the form the
   API *served*, and not a re-derivation: replaying the drafting at validation would
   return another playbook, hence create an agent that nobody validated. The policy is
   the one `ProposedRole.policy()` returned, brought back as is — the only path of this
   translation, and that is what guarantees that an `auto` tier read with its reason is
   the tier that will be written;
② **everything is checked before the first write.** Three stores × N roles offer no
   transaction: a single rejection stops everything, and nothing is written — half a
   team would be worse than a rejection. And `rejected_roles` returns the **whole**
   list, because correcting a team of five roles one rejection at a time would be five
   round trips;
③ **what is written is written in the project**, never at the level of the role
   templates, which belong to nobody.

No network and no model call: `creer` is synchronous, and that is precisely what
distinguishes it from `proposer`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from maestro.agents.capacity import CapacityStore
from maestro.agents.configuration import ConfigurationAgents
from maestro.agents.mcp import IntegrationMcp, McpStore, ServeurMcp
from maestro.agents.navigateur import ID_INTEGRATION as BROWSER_ID
from maestro.agents.navigateur import NavigateurDuPoste, integration_navigateur
from maestro.agents.permissions import EntreeArbitrage, PermissionStore, PolitiqueOutils
from maestro.agents.playbook_du_code import roles_du_code
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.rangement import SEGMENT_PROJETS
from maestro.agents.store import AgentStore, SurchargeStore
from maestro.controltower.equipe import EquipeRefusee, ServiceEquipe
from maestro.controltower.projets import ServiceProjets
from maestro.decideur import Decideur
from maestro.projects.store import ProjectStore
from maestro.team import (
    BROWSER_HEADING,
    MAX_CREATED_INSTANCES,
    NO_SKILL,
    NO_SKILL_WRITTEN,
    SKILLS_HEADING,
    SKILLS_INTRO,
    CreatedTeam,
    RetainedSkill,
    ValidatedRole,
    capacity,
    definition,
    observed_skills,
    playbook_with_sections,
    rejected_roles,
    without_frozen_commands,
)
from maestro.team.creation import (
    EARLIER_SKILLS_HEADINGS,
    browser_section,
    split_skills_section,
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `Path.home()` under `tmp_path` (#221) — without it `validate_root` would
    refuse any project root under `AppData` on a Windows workstation."""
    home = tmp_path / "maison"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _role(name: str = "dev", **extra) -> ValidatedRole:
    """A validated role as the screen brings it back — read playbook included."""
    base = {
        "name": name,
        "role": "Développeur",
        "competencies": ("backend",),
        "playbook": "Tu écris le code de Dépensio.",
        "instances": 1,
        "role_template": "developpeur",
    }
    return ValidatedRole(**{**base, **extra})


def _templates(tmp_path: Path) -> ConfigurationAgents:
    """The six stores mounted side by side, at the role template level."""
    root = tmp_path / "core"
    return ConfigurationAgents(
        agents=AgentStore(root / "agents"),
        surcharges=SurchargeStore(root / "surcharges"),
        playbooks=PlaybookStore(root / "playbooks"),
        permissions=PermissionStore(root / "permissions"),
        mcp=McpStore(root / "mcp"),
        capacites=CapacityStore(root / "capacite"),
    )


def _service(tmp_path: Path) -> tuple[ServiceEquipe, ConfigurationAgents, str]:
    """The service, its stores and the id of a project that is really declared."""
    store = ProjectStore(tmp_path / "projets")
    root = Path.home() / "depensio"
    root.mkdir(parents=True, exist_ok=True)
    project = store.create("Dépensio", root)
    templates = _templates(tmp_path)
    service = ServiceEquipe(ServiceProjets(store), templates, playbooks=False)
    return service, templates, project.id


# --- ② Everything is checked before the first write --------------------------


def test_a_well_formed_team_meets_no_rejection() -> None:
    assert rejected_roles([_role(), _role("tests", role="QA", competencies=("tests",))]) == ()


def test_a_role_template_name_is_rejected_because_it_is_reserved() -> None:
    """The package ships a playbook under each of the five names of the code, and it
    would mask the definition's: a project role named `developpeur` would go into
    execution with the code's playbook, silently."""
    (rejection,) = rejected_roles([_role("developpeur")])

    assert rejection.name == "developpeur"
    assert "réservé par le dépôt" in rejection.reason


def test_a_name_already_taken_in_the_project_is_rejected() -> None:
    (rejection,) = rejected_roles([_role()], taken_names=("dev",))

    assert "renommez le rôle" in rejection.reason


def test_a_duplicate_in_the_validated_team_is_rejected() -> None:
    """Two lines that would write the same file: the second would erase the first
    without anything saying so."""
    (rejection,) = rejected_roles([_role(), _role(role="Autre")])

    assert "figure deux fois" in rejection.reason


@pytest.mark.parametrize("instances", [0, -1, MAX_CREATED_INSTANCES + 1])
def test_an_out_of_bounds_instance_count_is_rejected(instances: int) -> None:
    """The ceiling exists so that a typo does not set a capacity to 400; it is raised
    in the capacity screens, which are not bounded."""
    (rejection,) = rejected_roles([_role(instances=instances)])

    assert f"attendu entre 1 et {MAX_CREATED_INSTANCES}" in rejection.reason


def test_an_empty_playbook_is_rejected_before_being_augmented() -> None:
    """The store cannot see this rejection: `playbook_with_sections()` would already
    have placed the skills section on top, and the agent would arrive in the project
    with the inventory of its skills for its whole system prompt."""
    (rejection,) = rejected_roles([_role(playbook="   ")])

    assert "playbook vide" in rejection.reason


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"name": "Dev Majuscule"}, "nom d'agent invalide"),
        ({"role": "  "}, "rôle vide"),
        ({"competencies": ()}, "compétence"),
    ],
)
def test_what_the_stores_would_reject_is_asked_of_the_stores(
    fields: dict, expected: str
) -> None:
    """Delegated, never rewritten: a pre-check more permissive than the write is
    useless, a stricter one refuses what would pass."""
    (rejection,) = rejected_roles([_role(**fields)])

    assert expected in rejection.reason


def test_the_rejections_are_returned_all_together() -> None:
    blockers = rejected_roles(
        [_role("developpeur"), _role("x", instances=0), _role(playbook="")]
    )

    assert len(blockers) == 3


# --- ① What is created is what was shown -------------------------------------


def test_the_persisted_definition_carries_the_playbook_augmented_with_its_skills() -> None:
    role = _role(skills=(RetainedSkill(name="lancer-les-tests", path=".claude/skills/t.md"),))

    agent_definition = definition(role)

    assert agent_definition.playbook.startswith("Tu écris le code de Dépensio.")
    assert SKILLS_HEADING in agent_definition.playbook
    assert ".claude/skills/t.md" in agent_definition.playbook


def test_the_definition_sets_neither_model_nor_provider_nor_effort() -> None:
    """A project's analysis says nothing about them, and setting them would be
    inventing: the definition takes the executors' defaults, and is tuned afterwards
    from its screens."""
    agent_definition = definition(_role())

    assert (
        agent_definition.modele,
        agent_definition.fournisseur,
        agent_definition.effort,
    ) == (None, None, None)


def test_a_role_without_skill_keeps_its_section_and_says_so() -> None:
    """Two different facts, "this role has none" and "nobody filed its skills", and
    only the first one is a decision."""
    assert NO_SKILL in playbook_with_sections(_role())


def test_the_skills_section_is_placed_again_without_stacking() -> None:
    """Idempotent: a section already present is replaced. Without it, a re-validated
    role would accumulate its inventories."""
    role = _role(skills=(RetainedSkill(name="mettre-en-route"),))

    once = playbook_with_sections(role)
    twice = playbook_with_sections(_role(playbook=once, skills=role.skills))

    assert twice.count(SKILLS_HEADING) == 1


def test_the_sections_after_the_skills_are_kept() -> None:
    """A playbook is a system prompt, not a file one truncates."""
    with_more = f"Tu écris.\n\n{SKILLS_HEADING}\n\nvieux\n\n## Garde-fous\n\nrien de destructif.\n"

    placed_again = playbook_with_sections(_role(playbook=with_more))

    assert "## Garde-fous" in placed_again
    assert "vieux" not in placed_again


@pytest.mark.parametrize(
    "skills",
    [
        (RetainedSkill(name="mettre-en-route", path=".agents/skills/m/SKILL.md"),),
        (),
    ],
    ids=["avec-skills", "sans-skill"],
)
def test_the_skills_section_addresses_the_agent_with_tu_like_its_playbook(skills) -> None:
    """#1404: « Servez-vous-en » under a playbook written with « tu » (`_CADRE_GENERATION`)."""
    section = split_skills_section(playbook_with_sections(_role(skills=skills)))[1]
    unwritten = playbook_with_sections(_role(missing_skills=(RetainedSkill(name="x"),)))

    for text in (section, unwritten, NO_SKILL, NO_SKILL_WRITTEN):
        assert re.findall(r"\b(vous|votre|vos|vôtres?|servez)\b", text, re.IGNORECASE) == []


def test_a_section_placed_under_the_earlier_heading_is_replaced_not_stacked() -> None:
    """A definition written before #1404 keeps its text until it is rewired — which
    replaces it, because the earlier heading stays an anchor."""
    (earlier,) = EARLIER_SKILLS_HEADINGS
    before = f"Tu écris.\n\n{earlier}\n\nServez-vous-en.\n\n## Garde-fous\n\nrien de destructif.\n"

    placed_again = playbook_with_sections(_role(playbook=before))

    assert earlier not in placed_again
    assert placed_again.count(SKILLS_HEADING) == 1
    assert "Servez-vous-en" not in placed_again
    assert "## Garde-fous" in placed_again
    assert split_skills_section(before)[1].startswith(earlier)


def test_the_created_capacity_is_active_and_carries_the_validated_instances() -> None:
    """`actif=True` unconditionally — it has just been recruited; creating it switched
    off would make no sense at the end of a team validation."""
    setting = capacity(_role(instances=3))

    assert (setting.nom, setting.actif, setting.instances) == ("dev", True, 3)


# --- ③ Create, and in the project --------------------------------------------


@pytest.mark.parametrize("origin", ["existant", "nouveau"])
def test_declaring_a_project_instantiates_no_agent(tmp_path: Path, origin: str) -> None:
    """*« À la création ou import d'un projet, aucun agent défini encore »* — the
    request of 2026-09-19, taken where it is checked: declaring a project writes
    **nothing** into the agent configuration stores. Validating a team (#1040) is the
    only path by which an agent is born there.
    """
    store = ProjectStore(tmp_path / "projets")
    root = Path.home() / f"projet-{origin}"
    if origin == "existant":
        root.mkdir(parents=True, exist_ok=True)
    templates = _templates(tmp_path)

    project = store.create("Neuf", root, origin=origin)

    cfg = templates.pour_projet(project.id)
    assert cfg.agents.noms() == ()
    assert cfg.capacites.lister() == ()
    assert cfg.permissions.agents() == ()
    # The project's **effective** catalogue, and not only its stores: the five
    # definitions of the code no longer enter it (#1042), they are role templates.
    assert cfg.catalogue() == ()
    assert not (templates.agents.racine / SEGMENT_PROJETS).exists()


def test_the_code_definitions_remain_readable_as_role_templates(tmp_path: Path) -> None:
    """No agents does not mean no material: the five definitions are there, at the
    role template level, and the analysis draws the proposed roles from them."""
    templates = _templates(tmp_path)

    assert {
        agent_definition.nom for agent_definition in templates.gabarits_du_code()
    } == set(roles_du_code())
    assert templates.catalogue() == ()


def test_the_project_catalogue_is_exactly_the_validated_team(tmp_path: Path) -> None:
    """The other half of the criterion: after the validation, the project has a team —
    that one, and nothing else."""
    service, templates, project_id = _service(tmp_path)

    service.creer(project_id, [_role(), _role("tests", role="QA", competencies=("tests",))])

    team = templates.pour_projet(project_id).catalogue()
    assert {agent.nom for agent in team} == {"dev", "tests"}


def test_the_validated_team_is_written_in_the_project_and_not_at_template_level(
    tmp_path: Path,
) -> None:
    service, templates, project_id = _service(tmp_path)
    policy = PolitiqueOutils(ask=(EntreeArbitrage("Bash", Decideur.AUTO),))

    report = service.creer(
        project_id,
        [_role(policy=policy, instances=2)],
        proposition_id="equ-12345678",
    )

    cfg = templates.pour_projet(project_id)
    assert cfg.agents.noms() == ("dev",)
    assert cfg.capacites.lire("dev").instances == 2
    assert cfg.permissions.lire("dev") == policy
    # Role templates belong to nobody: nothing is written there.
    assert templates.agents.noms() == ()
    assert report["cree"] is True
    assert report["proposition_id"] == "equ-12345678"
    assert report["instances_total"] == 2


def test_the_validated_tier_is_the_written_tier(tmp_path: Path) -> None:
    """The only path of the translation "proposed permissions → policy" is
    `ProposedRole.policy()`: what arrives here is what the user read."""
    service, templates, project_id = _service(tmp_path)
    policy = PolitiqueOutils(ask=(EntreeArbitrage("Bash", Decideur.AUTO),))

    service.creer(project_id, [_role(policy=policy)])

    written = templates.pour_projet(project_id).permissions.lire("dev")
    assert written is not None
    assert written.decideur("Bash") is Decideur.AUTO


def test_a_role_without_policy_receives_none(tmp_path: Path) -> None:
    """An empty policy file *would be* a policy (empty `allow` list = open): setting
    what was not decided is what this effort avoids."""
    service, templates, project_id = _service(tmp_path)

    service.creer(project_id, [_role(policy=None)])

    cfg = templates.pour_projet(project_id)
    assert cfg.agents.noms() == ("dev",)
    assert cfg.permissions.lire("dev") is None
    assert not (cfg.permissions.racine / "dev.json").exists()


def test_a_single_rejection_stops_everything_and_nothing_is_written(tmp_path: Path) -> None:
    """The heart of the lot: without a file system transaction, half a team would be
    worse than a rejection."""
    service, templates, project_id = _service(tmp_path)

    with pytest.raises(EquipeRefusee) as rejection:
        service.creer(project_id, [_role(), _role("infra", instances=0)])

    assert rejection.value.motif == "equipe-refusee"
    assert {r.name for r in rejection.value.refus} == {"infra"}
    assert templates.pour_projet(project_id).agents.noms() == ()


def test_a_name_already_created_in_the_project_is_rejected_at_the_second_validation(
    tmp_path: Path,
) -> None:
    service, _templates, project_id = _service(tmp_path)
    service.creer(project_id, [_role()])

    with pytest.raises(EquipeRefusee):
        service.creer(project_id, [_role()])


def test_two_projects_do_not_share_their_team(tmp_path: Path) -> None:
    service, templates, project_id = _service(tmp_path)
    other = Path.home() / "autre"
    other.mkdir(parents=True, exist_ok=True)
    neighbour = ProjectStore(tmp_path / "projets").create("Voisin", other)

    service.creer(project_id, [_role()])

    assert templates.pour_projet(project_id).agents.noms() == ("dev",)
    assert templates.pour_projet(neighbour.id).agents.noms() == ()


def test_the_creation_report_says_what_now_exists() -> None:
    """The counterpart of the `cree: False` the proposal serves: the first says it did
    nothing, this one says what was done."""
    report = CreatedTeam(project_id="prj-1", proposal_id="equ-1").to_dict()

    assert report["cree"] is True
    assert report["agents"] == []
    assert report["instances_total"] == 0


# --- ④ Only the skills the project holds are attached (#1212) ----------------
#
# Finding of the 2026-09-22 milestone closing review (trial `20260922-164402`): on S3
# and S4, the team is created without the tooling being written, and the `dev`
# playbook named `.agents/skills/mettre-en-route/SKILL.md` in a project without
# `.agents/`. The chat and the route both go through `ServiceEquipe.creer`
# (`creer_equipe_du_projet`, app.py): that is where these tests take it.

#: The exact skill of the finding, as the proposal attaches it.
SET_UP_SKILL_PATH = ".agents/skills/mettre-en-route/SKILL.md"
SET_UP_SKILL = RetainedSkill(
    name="mettre-en-route", path=SET_UP_SKILL_PATH, commands=("pip install -e .",)
)


def _write_skill(root: Path, path: str) -> None:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("---\nname: skill\n---\n", encoding="utf-8")


def _written_playbook(templates: ConfigurationAgents, project_id: str, name: str = "dev") -> str:
    agent_definition = templates.pour_projet(project_id).agents.lire(name)
    assert agent_definition is not None
    return agent_definition.playbook


def test_a_recommended_but_unwritten_skill_is_not_attached(tmp_path: Path) -> None:
    """The finding, replayed: a project without `.agents/`, a team validated as
    proposed."""
    service, templates, project_id = _service(tmp_path)
    role = _role(skills=(SET_UP_SKILL,))
    # The faulty sample: the section from before #1212, placed without looking at the
    # disk, does name the path — which is what makes the absence below attributable to
    # the observation, and not to a text that would never have carried it.
    assert SET_UP_SKILL_PATH in playbook_with_sections(role)

    report = service.creer(project_id, [role])

    playbook = _written_playbook(templates, project_id)
    assert SET_UP_SKILL_PATH not in playbook
    assert ".agents/" not in playbook
    assert NO_SKILL_WRITTEN in playbook
    assert report["agents"][0]["skills"] == []


def test_a_tooled_project_keeps_its_section_intact_paths_included(tmp_path: Path) -> None:
    """The other half: the tooling is written, the section is the one from before #1212
    character for character — the observation removes nothing from a project that holds
    its skills."""
    service, templates, project_id = _service(tmp_path)
    _write_skill(Path.home() / "depensio", SET_UP_SKILL_PATH)
    role = _role(skills=(SET_UP_SKILL,))

    report = service.creer(project_id, [role])

    assert _written_playbook(templates, project_id) == playbook_with_sections(role)
    assert report["agents"][0]["skills"] == ["mettre-en-route"]


def test_only_written_skills_are_named_when_the_tooling_is_partial(
    tmp_path: Path,
) -> None:
    service, templates, project_id = _service(tmp_path)
    written = RetainedSkill(
        name="lancer-les-tests", path=".agents/skills/lancer-les-tests/SKILL.md"
    )
    _write_skill(Path.home() / "depensio", written.path)

    service.creer(project_id, [_role(skills=(SET_UP_SKILL, written))])

    playbook = _written_playbook(templates, project_id)
    assert written.path in playbook
    assert SET_UP_SKILL_PATH not in playbook
    assert NO_SKILL_WRITTEN not in playbook


@pytest.mark.parametrize(
    "path",
    ["../dehors/SKILL.md", "{dehors}/SKILL.md", ".agents/skills/dossier"],
)
def test_a_path_outside_the_project_or_not_a_file_is_not_held(
    tmp_path: Path, path: str
) -> None:
    """The path arrives in the request body: a file that exists **outside** the root is
    not a skill of the project, and a folder is not a `SKILL.md` the agent could
    read."""
    service, templates, project_id = _service(tmp_path)
    outside = Path.home() / "dehors"
    _write_skill(outside, "SKILL.md")
    (Path.home() / "depensio" / ".agents" / "skills" / "dossier").mkdir(parents=True)
    path = path.format(dehors=outside.as_posix())

    service.creer(project_id, [_role(skills=(RetainedSkill(name="x", path=path),))])

    playbook = _written_playbook(templates, project_id)
    assert path not in playbook
    assert NO_SKILL_WRITTEN in playbook


def test_the_observation_keeps_the_order_and_does_not_attach_a_skill_without_path() -> None:
    """Pure: `observed_skills` touches no disk, it asks `holds`."""
    a, b = RetainedSkill(name="a", path="a.md"), RetainedSkill(name="b", path="b.md")
    without_path = RetainedSkill(name="c")
    asked: list[str] = []

    def holds(path: str) -> bool:
        asked.append(path)
        return True

    observed = observed_skills(_role(skills=(b, without_path, a)), holds)

    assert observed.skills == (b, a)
    assert observed.missing_skills == (without_path,)
    assert asked == ["b.md", "a.md"]


def test_a_role_with_nothing_recommended_keeps_its_sentence() -> None:
    """Two reasons, "nothing was recommended" and "nothing is written": the second
    does not replace the first where the first is true."""
    observed = observed_skills(_role(), lambda _path: False)

    assert NO_SKILL in playbook_with_sections(observed)
    assert NO_SKILL_WRITTEN not in playbook_with_sections(observed)


# --- ④ Maestro's browser (#1532) ---------------------------------------------


def _service_with_browser(tmp_path: Path) -> tuple[ServiceEquipe, ConfigurationAgents, str]:
    """The service, its browser built without looking at the workstation."""
    _, templates, project_id = _service(tmp_path)
    store = ProjectStore(tmp_path / "projets")
    edge = NavigateurDuPoste("msedge", tmp_path / "msedge.exe")
    service = ServiceEquipe(
        ServiceProjets(store),
        templates,
        playbooks=False,
        navigateur=lambda: integration_navigateur(sonde=lambda: edge, sorties=tmp_path / "sorties"),
    )
    return service, templates, project_id


def test_a_role_equipped_with_the_browser_mounts_it_on_its_task_and_its_playbook_names_it(
    tmp_path: Path,
) -> None:
    """The ticket's criterion, from validation to mounting: the project's pool carries
    the browser, it is enabled for the role that receives it, and the executor mounts
    it on its task (`McpStore.lire`, what `_serveurs_mcp` calls) — under Maestro's
    name."""
    service, templates, project_id = _service_with_browser(tmp_path)

    report = service.creer(
        project_id,
        [_role(browser=True), _role("infra", role="Intégration continue", role_template="devops")],
    )

    assert [a["navigateur"] for a in report["agents"]] == [True, False]
    mcp = templates.pour_projet(project_id).mcp
    (integration,) = mcp.pool()
    assert integration.id == BROWSER_ID
    assert mcp.activations("dev") == (BROWSER_ID,)
    assert mcp.activations("infra") == ()
    (mounted,) = templates.mcp.pour_projet(project_id).lire("dev")
    assert mounted.nom == "navigateur"
    assert "--browser=msedge" in mounted.args
    assert templates.mcp.pour_projet(project_id).lire("infra") == ()
    # The playbook names it; the one that does not have it says nothing about it.
    playbook = _written_playbook(templates, project_id)
    assert BROWSER_HEADING in playbook
    assert "`mcp__navigateur__`" in playbook
    assert "N'écris pas ton propre banc" in playbook
    assert BROWSER_HEADING not in _written_playbook(templates, project_id, "infra")
    # Nothing at the role template level, which belongs to nobody.
    assert templates.mcp.pool() == ()


def test_a_team_without_browser_does_not_touch_the_pool(tmp_path: Path) -> None:
    built: list[str] = []
    _, templates, project_id = _service(tmp_path)
    service = ServiceEquipe(
        ServiceProjets(ProjectStore(tmp_path / "projets")),
        templates,
        playbooks=False,
        navigateur=lambda: built.append("appelé") or integration_navigateur(sonde=lambda: None),
    )

    service.creer(project_id, [_role()])

    assert built == []
    assert templates.pour_projet(project_id).mcp.pool() == ()
    assert not (templates.mcp.racine / SEGMENT_PROJETS / project_id / "pool.json").exists()


def test_a_browser_already_in_the_pool_is_kept_as_is(tmp_path: Path) -> None:
    """Set by the person from the integrations — or by a previous team: what is there
    was decided by someone, and creation does not rewrite it."""
    service, templates, project_id = _service_with_browser(tmp_path)
    mcp = templates.pour_projet(project_id).mcp
    configured = IntegrationMcp(
        id=BROWSER_ID,
        serveur=ServeurMcp(nom="mon-navigateur", type="stdio", commande="npx", args=("x",)),
    )
    mcp.ecrire_pool([configured])
    mcp.ecrire_activations("dev", ["autre"])  # an activation the agent already had

    service.creer(project_id, [_role(browser=True)])

    assert mcp.pool() == (configured,)
    assert mcp.activations("dev") == ("autre", BROWSER_ID)


def test_an_unreadable_pool_rejects_the_team_without_writing_anything(tmp_path: Path) -> None:
    """A role that was promised the browser is not born without it — nor is the half
    team that a rejection after the first definition would leave."""
    service, templates, project_id = _service_with_browser(tmp_path)
    root = templates.mcp.racine / SEGMENT_PROJETS / project_id
    root.mkdir(parents=True)
    (root / "pool.json").write_text("{pas du json", encoding="utf-8")

    with pytest.raises(EquipeRefusee) as rejection:
        service.creer(project_id, [_role("tests", role="QA"), _role(browser=True)])

    assert [r.name for r in rejection.value.refus] == ["dev"]
    assert "navigateur de Maestro" in rejection.value.refus[0].reason
    assert templates.pour_projet(project_id).agents.noms() == ()


def test_the_browser_section_is_placed_again_and_removed_without_stacking() -> None:
    """Idempotent like the skills one: placed again, it replaces; the browser removed in
    words, it goes — and regenerating the body sets it aside with the skills."""
    once = playbook_with_sections(_role(browser=True))
    twice = playbook_with_sections(_role(playbook=once, browser=True))
    assert twice == once
    assert twice.count(BROWSER_HEADING) == 1

    removed = playbook_with_sections(_role(playbook=once, browser=False))
    assert BROWSER_HEADING not in removed
    assert SKILLS_HEADING in removed

    body, sections = split_skills_section(once)
    assert body == "Tu écris le code de Dépensio."
    assert sections.startswith(SKILLS_HEADING)
    assert BROWSER_HEADING in sections
    assert browser_section() in sections


# --- ⑤ The skill is authoritative: the playbook freezes no command (#1520) ------
#
# Reservation R1 of the 2026-10-14 milestone closing review: each playbook listed the
# command a skill "wraps", frozen at recruitment. Replaced afterwards — said by the
# person, proposed by the review after the run —, it changed in the skill and stayed in
# the playbook: an agent of the next run received two commands for the same gesture.

#: The skills section as creation wrote it before #1520 — the state of the teams
#: already recruited (S9, trial `20261014-171347`).
SECTION_BEFORE_1520 = (
    f"{SKILLS_HEADING}\n\n"
    "L'outillage du projet t'est transmis en entier ; ceux-ci sont ceux que ton rôle "
    "branche. Sers-t'en plutôt que de réinventer la commande.\n\n"
    "- **construire-le-projet** — `.agents/skills/construire-le-projet/SKILL.md` "
    "(il enveloppe : `python assembler.py`)\n"
    "- **verifier-le-style** — `.agents/skills/verifier-le-style/SKILL.md` "
    "(il enveloppe : `python -m ruff check .`, `python -c \"print(1)\"`)\n"
)


def test_the_section_freezes_no_command_the_skill_is_authoritative(tmp_path: Path) -> None:
    """The skill named, its path to open — the command lives in the skill, and nowhere
    else: a command replaced in the project has no copy left to catch up."""
    service, templates, project_id = _service(tmp_path)
    _write_skill(Path.home() / "depensio", SET_UP_SKILL_PATH)

    service.creer(project_id, [_role(skills=(SET_UP_SKILL,))])

    playbook = _written_playbook(templates, project_id)
    assert f"**mettre-en-route** — `{SET_UP_SKILL_PATH}`" in playbook
    assert "pip install -e ." not in playbook
    assert SKILLS_INTRO in playbook


def test_an_earlier_playbook_stops_freezing_its_commands_and_keeps_the_rest() -> None:
    """Rewiring an already created team: Maestro's section loses its frozen commands —
    a command that carries parentheses included —, and nothing else moves. The body of
    the playbook, written for the project and reread by the person, is not Maestro's:
    what it quotes stays there, and the observation at write time is what will say so."""
    body = "Tu assembles le carnet. On t'a dit un jour : `python assembler.py`."
    before = f"{body}\n\n{SECTION_BEFORE_1520}\n## Garde-fous\n\nrien de destructif.\n"

    after = without_frozen_commands(before)

    assert "(il enveloppe :" not in after
    assert "print(1)" not in after and "ruff check" not in after
    assert "- **construire-le-projet** — `.agents/skills/construire-le-projet/SKILL.md`\n" in after
    assert SKILLS_INTRO in after
    assert after.startswith(body)
    assert after.endswith("## Garde-fous\n\nrien de destructif.\n")
    assert without_frozen_commands(after) == after


@pytest.mark.parametrize(
    "playbook",
    [
        "Tu écris le code.\n",
        playbook_with_sections(_role()),
        playbook_with_sections(_role(missing_skills=(SET_UP_SKILL,))),
        playbook_with_sections(_role(skills=(SET_UP_SKILL,))),
    ],
    ids=["sans-section", "aucun-skill", "aucun-skill-ecrit", "section-d-aujourd-hui"],
)
def test_a_playbook_that_freezes_nothing_comes_back_as_is(playbook: str) -> None:
    """Idempotent, and silent where it has nothing to do: rewiring does not write for
    nothing."""
    assert without_frozen_commands(playbook) == playbook
