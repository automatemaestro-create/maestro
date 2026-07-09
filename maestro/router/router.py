"""Routeur : auto-assignation d'une tâche à un agent (tickets #6 et #42).

Implémente la transition `prete → assignee` de la machine à états des tâches
(docs/03-modele-de-donnees.md §3) en combinant les **deux signaux** du routage
(docs/01 §3.2) :

1. **Règles de compétences** (`assign`, ticket #6) : confronte les
   `competences_requises` d'une tâche aux `competences` déclarées par chaque
   agent (entité CAPABILITY) et retient le meilleur recouvrement. Un vainqueur
   **net** (score > 0, sans ex æquo) est assigné directement — déterministe et
   sans appel modèle.
2. **Classifieur léger** (`Router`, ticket #42) : quand les règles ne tranchent
   pas (ex æquo, ou aucun recouvrement), un modèle rapide départage les
   candidats via la couche `ModelProvider` (voir `maestro.router.classifier`).

**Repli explicite** (critère MVP n°3) : si le classifieur est absent, hésite
(confiance sous le seuil) ou échoue, la tâche est marquée **« à assigner »**
(`RoutingDecision.agent is None`) plutôt que mal routée — l'assignation revient
alors à un humain (réassignation manuelle, Control Tower).

`assign` reste la règle pure historique (#6) : meilleur score gagne, l'ordre du
catalogue départage les ex æquo, `RoutingError` si aucune compétence couverte.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from maestro.agents.catalog import Agent
from maestro.orchestrator.schema import Task
from maestro.router.classifier import TaskClassifier

#: Méthodes d'assignation d'une décision de routage.
METHODE_COMPETENCES = "competences"
METHODE_CLASSIFIEUR = "classifieur"
METHODE_REPLI = "repli"

#: Confiance minimale exigée du classifieur pour assigner (en deçà : « à assigner »).
SEUIL_CONFIANCE_DEFAUT = 0.6


class RoutingError(RuntimeError):
    """Aucun agent du catalogue ne possède les compétences requises par la tâche."""


@dataclass(frozen=True)
class Assignment:
    """Résultat d'un routage par règles pures : la tâche, l'agent retenu et son score."""

    task: Task
    agent: Agent
    score: int


@dataclass(frozen=True)
class RoutingDecision:
    """Issue du routage combiné : un agent assigné, ou le repli « à assigner ».

    `agent` vaut None quand la tâche est **à assigner** (repli explicite) ;
    `raison` explique alors pourquoi. `methode` trace le signal qui a tranché
    (`competences`, `classifieur`, ou `repli`) et `confiance` sa certitude
    (1.0 pour une règle nette, la confiance du classifieur sinon).
    """

    task: Task
    agent: Agent | None
    score: int
    confiance: float
    methode: str
    raison: str = ""

    @property
    def a_assigner(self) -> bool:
        """La tâche attend-elle une assignation manuelle (repli explicite) ?"""
        return self.agent is None


class Router:
    """Routage combiné : règles de compétences, puis classifieur léger si ambigu.

    Sans classifieur (`classifier=None`), le routeur reste utilisable en mode
    règles pures : les cas nets sont assignés, les cas ambigus partent en repli
    « à assigner » — jamais routés au hasard. `route` ne lève pas : un échec du
    classifieur (fournisseur indisponible…) devient lui aussi un repli.
    """

    def __init__(
        self,
        agents: Sequence[Agent],
        *,
        classifier: TaskClassifier | None = None,
        seuil_confiance: float = SEUIL_CONFIANCE_DEFAUT,
    ) -> None:
        if not agents:
            raise ValueError("Aucun agent disponible pour le routage.")
        if not 0.0 <= seuil_confiance <= 1.0:
            raise ValueError(f"seuil_confiance doit être dans [0, 1] (reçu : {seuil_confiance}).")
        self._agents = tuple(agents)
        self._classifier = classifier
        self._seuil = seuil_confiance

    async def route(self, task: Task) -> RoutingDecision:
        """Route `task` : agent assigné, ou décision « à assigner » — sans jamais lever."""
        required = frozenset(task.competences_requises)
        couvertures = [(agent, agent.couverture(required)) for agent in self._agents]
        meilleur_score = max(score for _, score in couvertures)
        ex_aequo = tuple(agent for agent, score in couvertures if score == meilleur_score)

        if meilleur_score > 0 and len(ex_aequo) == 1:
            return RoutingDecision(
                task=task,
                agent=ex_aequo[0],
                score=meilleur_score,
                confiance=1.0,
                methode=METHODE_COMPETENCES,
            )

        # Ambigu : ex æquo (le classifieur départage les seuls candidats à égalité)
        # ou aucun recouvrement (il choisit parmi tout le catalogue, depuis le texte).
        candidats = ex_aequo if meilleur_score > 0 else self._agents
        return await self._departage(task, candidats, required)

    async def _departage(
        self, task: Task, candidats: tuple[Agent, ...], required: frozenset[str]
    ) -> RoutingDecision:
        """Tranche un cas ambigu au classifieur, ou replie en « à assigner »."""
        noms = ", ".join(a.nom for a in candidats)
        if self._classifier is None:
            return self._repli(
                task, raison=f"règles de compétences non concluantes ({noms}) et aucun classifieur"
            )
        try:
            verdict = await self._classifier.classify(task, candidats)
        except Exception as exc:  # repli plutôt qu'un mauvais routage — jamais levé
            return self._repli(task, raison=f"classifieur indisponible ({exc})")

        if verdict.agent is not None and verdict.confiance >= self._seuil:
            agent = next(a for a in candidats if a.nom == verdict.agent)
            return RoutingDecision(
                task=task,
                agent=agent,
                score=agent.couverture(required),
                confiance=verdict.confiance,
                methode=METHODE_CLASSIFIEUR,
            )
        return self._repli(
            task,
            raison=(
                f"le classifieur n'a pas départagé {noms} avec assez de confiance "
                f"({verdict.confiance:.2f} < seuil {self._seuil:.2f})"
            ),
            confiance=verdict.confiance,
        )

    def _repli(self, task: Task, *, raison: str, confiance: float = 0.0) -> RoutingDecision:
        """Construit la décision de repli : tâche marquée « à assigner », cause consignée."""
        return RoutingDecision(
            task=task,
            agent=None,
            score=0,
            confiance=confiance,
            methode=METHODE_REPLI,
            raison=f"à assigner : {raison} — repli explicite plutôt qu'un mauvais routage.",
        )


def assign(task: Task, agents: Sequence[Agent]) -> Assignment:
    """Assigne `task` au meilleur agent de `agents` selon le recouvrement de compétences.

    Règle pure du POC (#6) : itère dans l'ordre du catalogue et ne remplace le
    candidat qu'à score **strictement** supérieur — à égalité, le premier agent
    (ordre du catalogue) l'emporte, ce qui rend le routage déterministe. Lève
    `ValueError` si `agents` est vide, `RoutingError` si le meilleur score est
    nul (aucune compétence couverte). Pour le routage complet avec classifieur
    et repli « à assigner », voir `Router.route`.
    """
    if not agents:
        raise ValueError("Aucun agent disponible pour le routage.")

    required = frozenset(task.competences_requises)
    best = max(
        (Assignment(task=task, agent=agent, score=agent.couverture(required)) for agent in agents),
        key=lambda a: a.score,
    )
    if best.score == 0:
        demandees = ", ".join(sorted(required)) or "aucune"
        raise RoutingError(
            f"Aucun agent ne couvre les compétences requises par la tâche {task.id!r} "
            f"(demandées : {demandees})."
        )
    return best
