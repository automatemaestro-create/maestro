"""Role templates: what Maestro knows how to propose, and what justifies it (#1039).

The five frozen agents of the code are not agents a project receives by
default: they are **role templates** the analysis consults
([docs/37 §2.1, §4.1](../../docs/37-decision-equipe-sur-mesure.md)). This module
is that catalogue of templates, and **nothing else**: it does not read the disk,
touches no repository and builds no proposal — `maestro.team.proposal` is what
uses it.

What a template brings, and from where:

- its **role** and its **competencies** come from `GABARITS_DU_CODE`
  (`maestro.agents.catalog`), **derived and never copied**: widening a role's
  competencies in the catalogue widens them here without a line, whereas two
  lists would end up no longer saying the same thing;
- its **fallback playbook** comes from the package document
  (`maestro.agents.playbook_du_code`) — this is the "the material is not lost"
  of docs/37 §2.1;
- its **tooling usages** say which project skills it attaches, by usage and not
  by name: `SKILL_PAR_USAGE` (#1030) holds the names, and restating them here
  would make a second table to keep in agreement;
- its **need** is the function that answers *does this project call for this
  role?*, and that returns **the place in the project** that proves it. A role
  without its evidence would be an assertion, exactly what the tooling analysis
  already refuses (`Entree.justification`).

## The proposed name is not the template's

`RoleTemplate.name` (`dev`, `tests`, `infra`…) differs from
`RoleTemplate.agent_name` (`developpeur`, `qa`, `devops`…), and this is not an
affectation: `playbook_outille` (#1037) returns **the package document** for
any name of `roles_du_code()`. A definition named `developpeur` would therefore
have its playbook *written for this project* hidden by the code's one — the
generation of #257 would never reach execution. The template stays named after
the proposed role, the lineage stays readable, and the proposed playbook is the
one that applies.

These names are also free of the agent store's `NOMS_RESERVES`, which the team
tests check: a proposed role must be creatable (#1040) without running into a
name that `AgentStore` refuses.

## The orchestrator is not here, and that is the decision

It has no template because it is not a team role: it is Maestro, and it is the
one that recruits (docs/37 §4.2). It is not passed over in silence for all that
— `maestro.team.proposal` excludes it **by name**, with its reason.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from maestro.agents.catalog import GABARITS_DU_CODE, Agent
from maestro.agents.playbook_du_code import playbook_du_code
from maestro.outillage.detection import LANGAGE_PAR_EXTENSION
from maestro.outillage.modele import Constats, Piece

#: The frozen agents, indexed by name — the material of the templates. A
#: template naming an absent agent raises at this module's import, which is
#: better than a role proposed without competencies.
_AGENT_BY_NAME: dict[str, Agent] = {agent.nom: agent for agent in GABARITS_DU_CODE}

#: The extensions that exist only to draw a screen. This is the most honest
#: interface signal the analysis carries: `.ts` does not say whether the project
#: has an interface (a TypeScript service has none), a stylesheet or a screen
#: component does.
#:
#: The **languages** derive from it (never a second list of names): renaming a
#: language in the detection table follows here, and removing one of these
#: extensions from it raises outright at import.
INTERFACE_EXTENSIONS: tuple[str, ...] = (".css", ".scss", ".vue", ".svelte")
INTERFACE_LANGUAGES: frozenset[str] = frozenset(
    LANGAGE_PAR_EXTENSION[extension] for extension in INTERFACE_EXTENSIONS
)

#: The language that betrays a database in an analysed project — the same
#: reasoning, derived from the same table.
SQL_LANGUAGE: str = LANGAGE_PAR_EXTENSION[".sql"]

#: The verification usages a QA role covers, in order of preference: the first
#: one observed is the one that justifies the role.
VERIFICATION_USAGES: tuple[str, ...] = ("tester", "lint", "formater", "types")


@dataclass(frozen=True)
class Justification:
    """Why this role, and **the place in the project** that proves it.

    `evidence` can be `None` — on a project with no code file at all, the
    developer role stays justified without any file pointing to it, and
    inventing evidence would be worse than having none.
    """

    reason: str
    evidence: Piece | None = None


#: The signature of a need rule: the project's findings and the questionnaire's
#: answers (empty on an analysed project), and nothing else. No
#: `Recommandation`: a role is justified by what the project **is**, not by what
#: Maestro proposes to write into it — otherwise a role would justify a skill
#: that would justify that role.
NeedRule = Callable[[Constats, Mapping[str, str]], Justification | None]


def _developer_need(findings: Constats, answers: Mapping[str, str]) -> Justification:
    """Always — a project exists for code to be written in it.

    It is the only unconditional role, just as `AGENTS.md` is the only tooling
    item that depends on no finding. Its **justification**, though, is indeed
    observed: the dominant language and the file that illustrates it.
    """
    if findings.langages:
        dominant = findings.langages[0]
        return Justification(
            reason=(
                f"le projet est écrit en {dominant.nom} "
                f"({_percent(dominant.part)} des fichiers de code vus) : "
                "quelqu'un doit l'écrire et le modifier"
            ),
            evidence=Piece(
                nom=dominant.nom,
                chemin=dominant.exemple,
                role=f"{dominant.fichiers} fichier(s) {dominant.nom}",
            )
            if dominant.exemple
            else None,
        )
    return Justification(
        reason=(
            "aucun langage de code n'a été constaté dans les bornes de l'analyse, "
            "mais un projet existe pour qu'on y écrive : c'est le seul rôle qu'un "
            "projet appelle sans rien avoir à prouver"
        )
    )


def _qa_need(findings: Constats, answers: Mapping[str, str]) -> Justification | None:
    """An observed verification — tests, style, format or types."""
    for usage in VERIFICATION_USAGES:
        command = findings.commande_de(usage)
        if command is None:
            continue
        return Justification(
            reason=(
                f"le projet a sa commande de « {usage} » (`{command.commande}`, "
                f"{command.origine}) : quelqu'un doit la jouer et en rendre le verdict"
            ),
            evidence=Piece(
                nom=command.chemin,
                chemin=command.chemin,
                role=command.extrait or f"commande de {usage}",
            ),
        )
    return None


def _devops_need(findings: Constats, answers: Mapping[str, str]) -> Justification | None:
    """An observed CI, build or forge.

    The three in this order, from the most telling to the weakest: a CI says
    that something checks the code on every change, a build that there is a
    chain to maintain, a forge only that there is a place where all of this
    will live.
    """
    if findings.ci:
        evidence = findings.ci[0]
        return Justification(
            reason=(
                f"le projet a une intégration continue ({evidence.chemin}) : "
                "elle se tient, et ce n'est le métier d'aucun autre rôle"
            ),
            evidence=evidence,
        )
    build = findings.commande_de("construire")
    if build is not None:
        return Justification(
            reason=(
                f"le projet se construit (`{build.commande}`) : la chaîne de "
                "construction et son automatisation sont un métier à part"
            ),
            evidence=Piece(
                nom=build.chemin,
                chemin=build.chemin,
                role=build.extrait or "commande de construction",
            ),
        )
    if findings.forge is not None:
        return Justification(
            reason=(
                f"le code vit sur {findings.forge.nom} : il y a un endroit où poser "
                "une vérification automatique, même s'il n'y en a pas encore"
            ),
            evidence=Piece(
                nom=findings.forge.nom,
                chemin=findings.forge.chemin,
                role="forge constatée",
            )
            if findings.forge.chemin
            else None,
        )
    return None


def _designer_need(findings: Constats, answers: Mapping[str, str]) -> Justification | None:
    """An interface: an observed screen language, or a nature that says so."""
    for language in findings.langages:
        if language.nom in INTERFACE_LANGUAGES:
            return Justification(
                reason=(
                    f"le projet porte du {language.nom} : il a des écrans, et un écran "
                    "se conçoit avant de s'écrire"
                ),
                evidence=Piece(
                    nom=language.nom,
                    chemin=language.exemple,
                    role=f"{language.fichiers} fichier(s) {language.nom}",
                )
                if language.exemple
                else None,
            )
    if answers.get("nature") == "application-web":
        return Justification(
            reason=(
                "vous avez répondu qu'il s'agit d'une application web : elle aura des "
                "écrans, et un écran se conçoit avant de s'écrire"
            )
        )
    return None


def interface_observed(findings: Constats, answers: Mapping[str, str]) -> bool:
    """Does the project have an interface — the designer's question, asked for the browser.

    A single rule, the one that recruits the designer (`_designer_need`): a
    project whose interface justifies a design role is a project whose rendering
    is looked at. Restating it elsewhere would give two answers to the same
    question.
    """
    return _designer_need(findings, answers) is not None


def _database_need(findings: Constats, answers: Mapping[str, str]) -> Justification | None:
    """Observed SQL — a schema, a migration or a query laid down in the repository."""
    for language in findings.langages:
        if language.nom == SQL_LANGUAGE:
            return Justification(
                reason=(
                    f"le projet porte du {SQL_LANGUAGE} "
                    f"({language.fichiers} fichier(s)) : un schéma et ses migrations "
                    "ne se traitent pas comme du code applicatif"
                ),
                evidence=Piece(
                    nom=language.nom,
                    chemin=language.exemple,
                    role=f"{language.fichiers} fichier(s) {SQL_LANGUAGE}",
                )
                if language.exemple
                else None,
            )
    return None


@dataclass(frozen=True)
class RoleTemplate:
    """A role Maestro knows how to propose, and everything that decides it.

    `name` is the slug proposed for the definition, `agent_name` the name of the
    frozen agent it derives from (see the module docstring: the two differ on
    purpose).

    `usages` lists, for each tooling usage this role attaches, the reason why
    **this very role** attaches it. The usage, and never the skill's name:
    `SKILL_PAR_USAGE` (#1030) holds the names.

    ⚠ A template carries **no** permission policy of its own, and that is a
    decision: a tool's tier derives from what the **project** declares
    (`maestro.team.proposal`), never from the role. What the role must not do —
    "a destructive operation is escalated, it is not played", "no real
    deployment" — is in its **playbook**, which can tell a `SELECT` from a
    `DROP`; a permission policy cannot. Setting a more closed tier there would
    block `ls` to prevent `DROP TABLE`, and would leave the role unable to work
    without making the safeguard any truer.

    `instances_for` says whether this role multiplies. `None` — the case of
    every role but one — means *one instance*, which is the default of
    `CapaciteAgent` (docs/09: instances are increased to absorb the load, they
    are not set on principle).

    `watches_screen` (#1532) says whether this role receives Maestro's browser
    when the project has an interface (`interface_observed`) — the one who
    writes it, the one who designs it, the one who checks it. It only serves the
    **rules**, the fallback of a composition that did not succeed: when composed
    by the model, the team receives the browser according to its judgement
    (`maestro.team.composition`).
    """

    name: str
    agent_name: str
    need: NeedRule
    usages: tuple[tuple[str, str], ...] = ()
    instances_for: Callable[[Constats], tuple[int, str]] | None = None
    watches_screen: bool = False

    @property
    def agent(self) -> Agent:
        """The frozen agent this template takes its role and competencies from."""
        return _AGENT_BY_NAME[self.agent_name]

    @property
    def role(self) -> str:
        """The human label of the role — the catalogue's, never rewritten."""
        return self.agent.role

    @property
    def competencies(self) -> tuple[str, ...]:
        """The role's competencies, sorted — the catalogue's, never rewritten.

        Sorted because `Agent.competences` is a `frozenset`: without sorting, two
        proposals for the same project would return two orders, and a shape
        served by the API that moves for no reason is a shape that cannot be
        compared.
        """
        return tuple(sorted(self.agent.competences))

    def fallback_playbook(self) -> str:
        """The template's playbook — what serves when generation produced nothing.

        The package document, whole: it is a real role playbook, not a degraded
        version, and it is what docs/37 §2.1 calls "the material is not lost".
        """
        return playbook_du_code(self.agent_name)


#: The share of code files beyond which a language counts as a **surface** of
#: the project and not as a residue (a build script in Shell, a configuration
#: file in Python). A threshold, hence a trade-off: below it, the language stays
#: in the findings, it simply does not justify one more instance.
SUBSTANTIAL_SHARE = 0.15

#: The instance ceiling a proposal sets. A decision, not a measurement — see
#: `_developer_instances`.
MAX_PROPOSED_INSTANCES = 3

#: What a role with a single instance says. Written once: it is the answer to
#: "why only one?", and it is the same for every role that does not multiply.
SINGLE_INSTANCE_REASON = (
    "une instance : un seul travail à la fois sur ce rôle, c'est le défaut — on "
    "augmente les instances pour absorber la charge, on ne les pose pas par "
    "principe"
)


#: The languages that justify **another role** — a stylesheet calls for the
#: designer, an SQL file for the database role. They therefore do not count as
#: developer surfaces: the same material would be counted twice, once to
#: recruit a role and once to take its work away from it.
LANGUAGES_HANDLED_ELSEWHERE: frozenset[str] = INTERFACE_LANGUAGES | {SQL_LANGUAGE}


def _developer_instances(findings: Constats) -> tuple[int, str]:
    """One instance per substantial language, capped — and the reason that goes with it.

    The only role that multiplies, because it is the only one whose load reads
    in the findings: two substantial languages are two surfaces, and two
    instances work on them without fighting over the same files.

    The languages **handled by another role** are removed from them
    (`LANGUAGES_HANDLED_ELSEWHERE`): a project's CSS has already recruited a
    designer, and counting it here would recruit one more developer for work
    that is not theirs.

    ⚠ The ceiling is **a decision, not a measurement**: three agents writing at
    the same time in a project is already a lot for a team that has just been
    proposed and that nobody has seen work yet. Do not move it by invoking a
    measurement that does not exist — the user adjusts it at validation
    (#1040), and the capacity is then set at any time (#86).
    """
    substantial = [
        language
        for language in findings.langages
        if language.part >= SUBSTANTIAL_SHARE
        and language.nom not in LANGUAGES_HANDLED_ELSEWHERE
    ]
    if len(substantial) < 2:
        return 1, SINGLE_INSTANCE_REASON
    instances = min(len(substantial), MAX_PROPOSED_INSTANCES)
    named = ", ".join(f"{lang.nom} {_percent(lang.part)}" for lang in substantial)
    return instances, (
        f"{len(substantial)} langages substantiels ({named}) : {instances} instances "
        "travaillent sur des surfaces différentes sans se disputer les mêmes fichiers. "
        f"Le plafond de {MAX_PROPOSED_INSTANCES} est une décision, pas une mesure — "
        "c'est vous qui l'ajustez"
    )


#: The five templates, **in catalogue order** (`GABARITS_DU_CODE`). The order
#: is authoritative for rendering: two proposals for the same project return
#: the same list, without sorting at display time.
ROLE_TEMPLATES: tuple[RoleTemplate, ...] = (
    RoleTemplate(
        name="dev",
        agent_name="developpeur",
        need=_developer_need,
        usages=(
            ("installer", "il installe les dépendances avant d'écrire la première ligne"),
            (
                "construire",
                "il construit le projet pour vérifier que ce qu'il écrit tient debout",
            ),
            ("demarrer", "il lance le projet pour voir tourner ce qu'il vient de changer"),
        ),
        instances_for=_developer_instances,
        watches_screen=True,
    ),
    RoleTemplate(
        name="donnees",
        agent_name="bdd",
        need=_database_need,
        usages=(
            ("installer", "il lui faut les dépendances du projet pour jouer ses migrations"),
        ),
    ),
    RoleTemplate(
        name="infra",
        agent_name="devops",
        need=_devops_need,
        usages=(
            (
                "construire",
                "la construction est ce que la vérification automatique doit rejouer à "
                "chaque changement",
            ),
            (
                "installer",
                "installer les dépendances est la première étape de toute vérification "
                "automatique",
            ),
        ),
    ),
    RoleTemplate(
        name="interface",
        agent_name="designer",
        need=_designer_need,
        usages=(("demarrer", "un écran ne se juge qu'en le regardant tourner"),),
        watches_screen=True,
    ),
    RoleTemplate(
        name="tests",
        agent_name="qa",
        need=_qa_need,
        usages=(
            ("tester", "c'est sa vérification centrale : le verdict qu'il rend sort de là"),
            (
                "lint",
                "le style, le format et les types font partie de ce qu'il vérifie avant "
                "de conclure",
            ),
        ),
        watches_screen=True,
    ),
)


def _percent(share: float) -> str:
    """A share of files as a readable percentage (`0.6234` → "62 %")."""
    return f"{round(share * 100)} %"
