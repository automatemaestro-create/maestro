"""Router: automatic assignment of a task to an agent (#6 and #42).

Implements the `prete → assignee` transition of the task state machine
(docs/03-modele-de-donnees.md §3) by combining the **two routing signals**
(docs/01 §3.2):

1. **Competency rules** (`assign`, #6): match a task's `competences_requises`
   against the `competences` each agent declares (CAPABILITY entity) and keep the
   best overlap. A **clear** winner (score > 0, no tie) is assigned directly:
   deterministic and without a model call.
2. **Lightweight classifier** (`Router`, #42): when the rules do not settle it (a
   tie, or no overlap), a fast model breaks the tie between candidates through the
   `ModelProvider` layer (see `maestro.router.classifier`).

**Explicit fallback** (MVP criterion 3): if the classifier is absent, hesitates
(confidence under the threshold) or fails, the task is marked **"à assigner"**
(`RoutingDecision.agent is None`) rather than wrongly routed; assignment then goes
back to a human (manual reassignment, Control Tower).

**Closest** (#1260): the fallback above gives way to **one** decision, that of
doing the work with the team one has. The orchestrator confronted the team with the
plan (#1227) and found a gap; what no one covers after the proposal (refused, left
unanswered, with no one to make it to, or about another trade) no longer goes "à
assigner": the task goes to the closest candidate, named by the classifier asked
that question (`closest`). Without that decision nothing changes; the caller
carries it (`closest`).

`assign` remains the pure rule (#6): the best score wins, catalog order breaks
ties, `RoutingError` if no competency is covered.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from contextlib import suppress
from dataclasses import dataclass

from maestro.agents.catalog import Agent
from maestro.equipe.manque import competences_non_couvertes
from maestro.orchestrator.schema import Task
from maestro.router.classifier import TaskClassifier

#: Assignment methods of a routing decision. The values travel with the decision
#: and keep their names.
METHOD_COMPETENCIES = "competences"
METHOD_CLASSIFIER = "classifieur"
METHOD_FALLBACK = "repli"
#: The task goes to the closest role because the team stayed as it is (#1260):
#: neither a covered competency, nor a tiebreak among competent agents.
METHOD_CLOSEST = "plus_proche"

#: Minimum confidence required from the classifier to assign (below: "à assigner").
DEFAULT_CONFIDENCE_THRESHOLD = 0.6


class RoutingError(RuntimeError):
    """No agent of the catalog has the competencies the task requires."""


@dataclass(frozen=True)
class Assignment:
    """Result of routing by pure rules: the task, the chosen agent and its score."""

    task: Task
    agent: Agent
    score: int


@dataclass(frozen=True)
class RoutingDecision:
    """Outcome of combined routing: an assigned agent, or the "à assigner" fallback.

    `agent` is None when the task is **to be assigned** (explicit fallback);
    `reason` then explains why. `method` traces the signal that settled it
    (`competences`, `classifieur`, `repli` or `plus_proche`) and `confidence` its
    certainty (1.0 for a clear rule, the classifier's confidence otherwise).

    `uncovered` (#1041) carries the required competencies that **no active
    candidate** has. It is the raw fact only routing observes, and it cannot be
    deduced again elsewhere: the caller knows neither the agents capacity ruled
    out, nor the project catalog the router received for that call. Empty in every
    case where the question does not arise: an assigned agent, a tie the classifier
    did not settle, an entirely disabled catalog (there the team has the role: it is
    switched off, not missing). `maestro.equipe.manque` turns it into a named
    **position**.
    """

    task: Task
    agent: Agent | None
    score: int
    confidence: float
    method: str
    reason: str = ""
    uncovered: tuple[str, ...] = ()

    @property
    def needs_assignment(self) -> bool:
        """Does the task await a manual assignment (explicit fallback)?"""
        return self.agent is None


class Router:
    """Combined routing: competency rules, then the lightweight classifier when ambiguous.

    Without a classifier (`classifier=None`), the router stays usable in pure-rules
    mode: clear cases are assigned, ambiguous ones go to the "à assigner" fallback,
    never routed at random. `route` does not raise: a classifier failure (provider
    unavailable…) becomes a fallback too.
    """

    def __init__(
        self,
        agents: Sequence[Agent],
        *,
        classifier: TaskClassifier | None = None,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        if not agents:
            raise ValueError("Aucun agent disponible pour le routage.")
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError(
                f"confidence_threshold doit être dans [0, 1] (reçu : {confidence_threshold})."
            )
        self._agents = tuple(agents)
        self._classifier = classifier
        self._threshold = confidence_threshold

    async def route(
        self,
        task: Task,
        *,
        excluded: Collection[str] = frozenset(),
        agents: Sequence[Agent] | None = None,
        closest: bool = False,
        recruited: Collection[str] = frozenset(),
    ) -> RoutingDecision:
        """Route `task`: an assigned agent, or an "à assigner" decision, never raising.

        `excluded` removes the **disabled** agents from the candidates (capacity
        control, #86/EF-21): a disabled agent receives no more tasks. The task goes
        to the best remaining agent, or to the "à assigner" fallback if no one is
        available any more (never routed to an excluded agent).

        `agents` (#1038) replaces the router's catalog **for this call only**: that
        is how a task attached to a project is routed to the agents **of that
        project**, which a catalog fixed at wiring time cannot know (they are born
        after it, and not in the same folder). The order stays that of the catalog
        received: it breaks ties, so it must not be sorted here.

        ⚠ An **empty** sequence is not an omission (#1042). `None` alone means "I
        have no team to give you" (task outside a project, stores not wired); `()`
        means "this project has no agent", and the task goes to the "à assigner"
        fallback, which is the right answer to *a project is born without agents*:
        no one can take it, and that is a fact to show, not to fill in. Otherwise a
        project without agents would be routed to the wiring's catalog, and the five
        roles of the code would work in a project that recruited no one.

        `closest` (#1260) carries the decision to do this work with the team as it
        is. It changes one case only: **no one covers any competency** of the task
        (a trade is missing, `uncovered` not empty). The task then goes to the
        closest active candidate instead of the fallback. The other cases are not
        affected: a covered competency stays the rule, a tie stays with the ordinary
        classifier, and an empty or fully disabled catalog stays "à assigner": there
        is then no one close.

        `recruited` (#1260) carries the other outcome of the same confrontation: the
        competencies for which a role has just been **recruited for this plan**. A
        task that requires one of them goes to a candidate that covers it, even if
        another covers as many or more of its competencies. That is what the thread
        promised when recruiting ("the tasks that required these competencies will
        go to the new role"), and the very reason for recruiting. Otherwise a tie
        between the developer (`frontend`) and the recruited designer (`ui`) went to
        the former, and the completed team was of no use. If no one covers them (the
        role was disabled in the meantime), the ordinary rule resumes.
        """
        catalog = self._agents if agents is None else tuple(agents)
        active = tuple(a for a in catalog if a.nom not in excluded)
        if not active:
            # Two causes, and the fallback tells them apart: they are not fixed the
            # same way at all, reactivating an agent or recruiting one (#1042).
            return self._fallback(
                task,
                reason=(
                    "tous les agents du catalogue sont désactivés"
                    if catalog
                    else "aucun agent dans ce catalogue — l'équipe reste à recruter"
                ),
            )
        required = frozenset(task.required_competencies)
        targeted = frozenset(recruited) & required
        if targeted:
            # The trade recruited for this plan takes the tasks that required it
            # (#1260): the candidates narrow down to those that cover it, then the
            # ordinary rule breaks ties among them.
            recruits = tuple(a for a in active if a.competences & targeted)
            active = recruits or active
        coverages = [(agent, agent.couverture(required)) for agent in active]
        best_score = max(score for _, score in coverages)
        tied = tuple(agent for agent, score in coverages if score == best_score)

        if best_score > 0 and len(tied) == 1:
            return RoutingDecision(
                task=task,
                agent=tied[0],
                score=best_score,
                confidence=1.0,
                method=METHOD_COMPETENCIES,
            )

        # Ambiguous: a tie (the classifier breaks it among the tied candidates
        # only) or no overlap (it picks among the active agents, from the text).
        candidates = tied if best_score > 0 else active
        # What no one covers (#1041), observed on the **active candidates** and not
        # on the catalog: a disabled agent will not take the task, and counting its
        # competencies as covered would silence the signal exactly when it is true.
        # Empty as soon as a candidate covers something: the task is then routable,
        # and the ambiguity is that of the tiebreak.
        uncovered = competences_non_couvertes(required, active) if best_score == 0 else ()
        if closest and uncovered:
            return await self._closest(task, active, uncovered)
        return await self._tiebreak(task, candidates, required, uncovered)

    async def _closest(
        self,
        task: Task,
        candidates: tuple[Agent, ...],
        uncovered: tuple[str, ...],
    ) -> RoutingDecision:
        """Hand `task` to the candidate closest to the missing trade (#1260).

        **A single candidate**: there is nothing to ask, it is that one (the case of
        a trial, a project with only a developer). **Several**: the classifier
        names one, on the question that does not admit abstention (`closest`); the
        model judges closeness, never catalog order. Only if it still names no one
        (absent, failing, or outside the candidates) does catalog order break the
        tie, and the reason says so: a default choice that went unsaid would read
        as a judgement.

        `uncovered` travels on the decision as on a fallback: it is the fact routing
        observed, and it stays true once the task is handed over.
        """
        missing = ", ".join(uncovered)
        if len(candidates) == 1:
            return self._closest_decision(
                task,
                candidates[0],
                confidence=0.0,
                reason=(
                    f"aucun rôle de l'équipe ne couvre {missing} ; l'équipe reste telle "
                    f"qu'elle est, et {candidates[0].nom} est son seul rôle en place"
                ),
                uncovered=uncovered,
            )
        by_name = {a.nom: a for a in candidates}
        verdict = None
        if self._classifier is not None:
            with suppress(Exception):  # a failure does not undo what was decided
                verdict = await self._classifier.classify(task, candidates, closest=True)
        if verdict is not None and verdict.agent in by_name:
            return self._closest_decision(
                task,
                by_name[verdict.agent],
                confidence=verdict.confidence,
                reason=(
                    f"aucun rôle de l'équipe ne couvre {missing} ; l'équipe reste telle "
                    f"qu'elle est, et le classifieur désigne {verdict.agent} comme le "
                    "plus proche"
                ),
                uncovered=uncovered,
            )
        return self._closest_decision(
            task,
            candidates[0],
            confidence=0.0,
            reason=(
                f"aucun rôle de l'équipe ne couvre {missing} ; l'équipe reste telle "
                f"qu'elle est, le classifieur n'a désigné personne, et {candidates[0].nom} "
                "est le premier rôle de l'équipe"
            ),
            uncovered=uncovered,
        )

    @staticmethod
    def _closest_decision(
        task: Task,
        agent: Agent,
        *,
        confidence: float,
        reason: str,
        uncovered: tuple[str, ...],
    ) -> RoutingDecision:
        """The "closest" decision: an agent, with no covered competency, and why."""
        return RoutingDecision(
            task=task,
            agent=agent,
            score=0,
            confidence=confidence,
            method=METHOD_CLOSEST,
            reason=f"au plus proche : {reason}.",
            uncovered=uncovered,
        )

    async def _tiebreak(
        self,
        task: Task,
        candidates: tuple[Agent, ...],
        required: frozenset[str],
        uncovered: tuple[str, ...] = (),
    ) -> RoutingDecision:
        """Settle an ambiguous case with the classifier, or fall back to "à assigner"."""
        names = ", ".join(a.nom for a in candidates)
        if self._classifier is None:
            return self._fallback(
                task,
                reason=f"règles de compétences non concluantes ({names}) et aucun classifieur",
                uncovered=uncovered,
            )
        try:
            verdict = await self._classifier.classify(task, candidates)
        except Exception as exc:  # fallback rather than a wrong routing, never raised
            return self._fallback(
                task,
                reason=f"classifieur indisponible ({exc})",
                uncovered=uncovered,
            )

        if verdict.agent is not None and verdict.confidence >= self._threshold:
            agent = next(a for a in candidates if a.nom == verdict.agent)
            return RoutingDecision(
                task=task,
                agent=agent,
                score=agent.couverture(required),
                confidence=verdict.confidence,
                method=METHOD_CLASSIFIER,
            )
        return self._fallback(
            task,
            reason=(
                f"le classifieur n'a pas départagé {names} avec assez de confiance "
                f"({verdict.confidence:.2f} < seuil {self._threshold:.2f})"
            ),
            confidence=verdict.confidence,
            uncovered=uncovered,
        )

    def _fallback(
        self,
        task: Task,
        *,
        reason: str,
        confidence: float = 0.0,
        uncovered: tuple[str, ...] = (),
    ) -> RoutingDecision:
        """Build the fallback decision: task marked "à assigner", cause recorded."""
        return RoutingDecision(
            task=task,
            agent=None,
            score=0,
            confidence=confidence,
            method=METHOD_FALLBACK,
            reason=f"à assigner : {reason} — repli explicite plutôt qu'un mauvais routage.",
            uncovered=uncovered,
        )


def assign(task: Task, agents: Sequence[Agent]) -> Assignment:
    """Assign `task` to the best agent of `agents` by competency overlap.

    The pure rule (#6): iterate in catalog order and replace the candidate only on a
    **strictly** higher score. On a tie the first agent (catalog order) wins, which
    makes routing deterministic. Raises `ValueError` if `agents` is empty,
    `RoutingError` if the best score is zero (no competency covered). For full
    routing with classifier and "à assigner" fallback, see `Router.route`.
    """
    if not agents:
        raise ValueError("Aucun agent disponible pour le routage.")

    required = frozenset(task.required_competencies)
    best = max(
        (Assignment(task=task, agent=agent, score=agent.couverture(required)) for agent in agents),
        key=lambda a: a.score,
    )
    if best.score == 0:
        requested = ", ".join(sorted(required)) or "aucune"
        raise RoutingError(
            f"Aucun agent ne couvre les compétences requises par la tâche {task.id!r} "
            f"(demandées : {requested})."
        )
    return best
