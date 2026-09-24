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

**Au plus proche** (#1260) : le repli ci-dessus cède sur **une** décision, celle de
faire le travail avec l'équipe qu'on a. L'orchestrateur a confronté l'équipe au
plan (#1227) et constaté un manque ; ce que personne n'y couvre après la
proposition — refusée, restée sans réponse, sans personne à qui la faire, ou
portant sur un autre métier — ne part plus « à assigner » : la tâche va au
candidat le plus proche, désigné par le classifieur à qui l'on pose cette
question-là (`plus_proche`). Sans cette décision, rien ne change — c'est
l'appelant qui la porte (`au_plus_proche`).

`assign` reste la règle pure historique (#6) : meilleur score gagne, l'ordre du
catalogue départage les ex æquo, `RoutingError` si aucune compétence couverte.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from contextlib import suppress
from dataclasses import dataclass

from maestro.agents.catalog import Agent
from maestro.equipe.manque import competences_non_couvertes
from maestro.orchestrator.schema import Task
from maestro.router.classifier import TaskClassifier

#: Méthodes d'assignation d'une décision de routage.
METHODE_COMPETENCES = "competences"
METHODE_CLASSIFIEUR = "classifieur"
METHODE_REPLI = "repli"
#: La tâche va au rôle le plus proche parce que l'équipe est restée telle qu'elle
#: est (#1260) — ni une compétence couverte, ni un départage entre compétents.
METHODE_PLUS_PROCHE = "plus_proche"

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

    `non_couvertes` (#1041) porte les compétences requises qu'**aucun candidat
    actif** ne possède. C'est le fait brut que seul le routage constate, et il ne
    se redéduit pas d'ailleurs : l'appelant ne connaît ni les agents que la
    capacité a écartés, ni le catalogue du projet que le routeur a reçu pour cet
    appel-là. Vide dans tous les cas où la question ne se pose pas — un agent
    assigné, un ex æquo que le classifieur n'a pas tranché, un catalogue
    entièrement désactivé (là, l'équipe a le rôle : il est éteint, pas absent).
    C'est `maestro.equipe.manque` qui en fait un **poste** nommé.
    """

    task: Task
    agent: Agent | None
    score: int
    confiance: float
    methode: str
    raison: str = ""
    non_couvertes: tuple[str, ...] = ()

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

    async def route(
        self,
        task: Task,
        *,
        exclus: Collection[str] = frozenset(),
        agents: Sequence[Agent] | None = None,
        au_plus_proche: bool = False,
        recrutees: Collection[str] = frozenset(),
    ) -> RoutingDecision:
        """Route `task` : agent assigné, ou décision « à assigner » — sans jamais lever.

        `exclus` écarte des candidats les agents **désactivés** (contrôle de
        capacité, #86/EF-21) : un agent désactivé ne reçoit plus de tâches — la
        tâche va au meilleur agent restant, ou part en repli « à assigner » si
        plus personne n'est disponible (jamais routée vers un exclu).

        `agents` (#1038) remplace, **pour cet appel seulement**, le catalogue du
        routeur : c'est ainsi qu'une tâche rattachée à un projet est routée sur
        les agents **de ce projet**, un catalogue figé au câblage ne pouvant pas
        les connaître (ils naissent après lui, et pas dans le même dossier).
        L'ordre reste celui du catalogue reçu — c'est lui qui départage les ex
        æquo, et il ne doit donc pas être trié ici.

        ⚠ Une séquence **vide n'est pas une omission** (#1042). Elle l'était, et
        un projet sans agent se routait alors sur le catalogue du câblage : c'est
        ce qui faisait travailler les cinq rôles du code dans un projet qui n'avait
        recruté personne. Désormais `None` seul veut dire « je n'ai pas d'équipe à
        te donner » (tâche hors projet, dépôts non câblés) ; `()` veut dire « ce
        projet n'a aucun agent », et la tâche part en repli « à assigner » —
        laquelle est la réponse juste à *un projet naît sans agent* : personne ne
        peut la prendre, et c'est un fait à montrer, pas à combler.

        `au_plus_proche` (#1260) porte la décision de faire ce travail avec
        l'équipe telle qu'elle est. Elle ne change qu'un cas : **personne ne
        couvre aucune compétence** de la tâche (un métier manque, `non_couvertes`
        non vide). La tâche va alors au candidat actif le plus proche au lieu de
        partir en repli. Les autres cas n'en sont pas touchés — une compétence
        couverte reste la règle, un ex æquo reste au classifieur ordinaire, et un
        catalogue vide ou tout désactivé reste « à assigner » : il n'y a alors
        personne de proche.

        `recrutees` (#1260) porte l'autre issue de la même confrontation : les
        compétences pour lesquelles un rôle vient d'être **recruté pour ce plan**.
        Une tâche qui en demande une va à un candidat qui la couvre, même si un
        autre couvre autant ou plus de ses compétences — c'est ce que le fil a
        promis en recrutant (« les tâches qui demandaient ces compétences iront au
        nouveau rôle »), et c'est la raison même du recrutement. Sans quoi un ex
        æquo entre le développeur (`frontend`) et le designer recruté (`ui`) se
        tranchait pour le premier, et l'équipe complétée ne servait à rien — vu
        une fois sur deux sur la vraie stack. Si personne ne les couvre (le rôle a
        été désactivé entre-temps), la règle ordinaire reprend.
        """
        catalogue = self._agents if agents is None else tuple(agents)
        candidats_actifs = tuple(a for a in catalogue if a.nom not in exclus)
        if not candidats_actifs:
            # Deux causes, et le repli les distingue : elles ne se corrigent pas du
            # tout pareil — réactiver un agent, ou en recruter un (#1042).
            return self._repli(
                task,
                raison=(
                    "tous les agents du catalogue sont désactivés"
                    if catalogue
                    else "aucun agent dans ce catalogue — l'équipe reste à recruter"
                ),
            )
        required = frozenset(task.competences_requises)
        vises = frozenset(recrutees) & required
        if vises:
            # Le métier recruté pour ce plan prend les tâches qui le demandaient
            # (#1260) : les candidats se réduisent à ceux qui le couvrent, puis la
            # règle ordinaire départage parmi eux.
            recrues = tuple(a for a in candidats_actifs if a.competences & vises)
            candidats_actifs = recrues or candidats_actifs
        couvertures = [(agent, agent.couverture(required)) for agent in candidats_actifs]
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
        # ou aucun recouvrement (il choisit parmi les agents actifs, depuis le texte).
        candidats = ex_aequo if meilleur_score > 0 else candidats_actifs
        # Ce que personne ne couvre (#1041), constaté sur les **candidats actifs**
        # et non sur le catalogue : un agent désactivé ne prendra pas la tâche, et
        # compter ses compétences comme couvertes ferait taire le signal juste au
        # moment où il est vrai. Vide dès qu'un candidat couvre quelque chose : la
        # tâche est alors routable, et l'ambiguïté est celle du départage.
        non_couvertes = (
            competences_non_couvertes(required, candidats_actifs)
            if meilleur_score == 0
            else ()
        )
        if au_plus_proche and non_couvertes:
            return await self._plus_proche(task, candidats_actifs, non_couvertes)
        return await self._departage(task, candidats, required, non_couvertes)

    async def _plus_proche(
        self,
        task: Task,
        candidats: tuple[Agent, ...],
        non_couvertes: tuple[str, ...],
    ) -> RoutingDecision:
        """Confie `task` au candidat le plus proche du métier qui manque (#1260).

        **Un seul candidat** : il n'y a rien à demander, c'est lui — le cas de
        l'essai, un projet qui n'a qu'un développeur. **Plusieurs** : le
        classifieur désigne, à la question qui n'admet pas l'abstention
        (`plus_proche`) ; c'est le modèle qui juge de la proximité, jamais l'ordre
        du catalogue. Ce n'est que s'il ne désigne toujours personne — absent, en
        panne, ou hors des candidats — que l'ordre du catalogue départage, et la
        raison le dit : un choix par défaut qui ne se dirait pas se lirait comme un
        jugement.

        `non_couvertes` voyage sur la décision comme sur un repli : c'est le fait
        que le routage a constaté, et il reste vrai une fois la tâche confiée.
        """
        manque = ", ".join(non_couvertes)
        if len(candidats) == 1:
            return self._au_plus_proche(
                task,
                candidats[0],
                confiance=0.0,
                raison=(
                    f"aucun rôle de l'équipe ne couvre {manque} ; l'équipe reste telle "
                    f"qu'elle est, et {candidats[0].nom} est son seul rôle en place"
                ),
                non_couvertes=non_couvertes,
            )
        noms = {a.nom: a for a in candidats}
        verdict = None
        if self._classifier is not None:
            with suppress(Exception):  # une panne ne fait pas échouer ce qui a été décidé
                verdict = await self._classifier.classify(task, candidats, plus_proche=True)
        if verdict is not None and verdict.agent in noms:
            return self._au_plus_proche(
                task,
                noms[verdict.agent],
                confiance=verdict.confiance,
                raison=(
                    f"aucun rôle de l'équipe ne couvre {manque} ; l'équipe reste telle "
                    f"qu'elle est, et le classifieur désigne {verdict.agent} comme le "
                    "plus proche"
                ),
                non_couvertes=non_couvertes,
            )
        return self._au_plus_proche(
            task,
            candidats[0],
            confiance=0.0,
            raison=(
                f"aucun rôle de l'équipe ne couvre {manque} ; l'équipe reste telle "
                f"qu'elle est, le classifieur n'a désigné personne, et {candidats[0].nom} "
                "est le premier rôle de l'équipe"
            ),
            non_couvertes=non_couvertes,
        )

    @staticmethod
    def _au_plus_proche(
        task: Task,
        agent: Agent,
        *,
        confiance: float,
        raison: str,
        non_couvertes: tuple[str, ...],
    ) -> RoutingDecision:
        """La décision « au plus proche » : un agent, sans compétence couverte, et pourquoi."""
        return RoutingDecision(
            task=task,
            agent=agent,
            score=0,
            confiance=confiance,
            methode=METHODE_PLUS_PROCHE,
            raison=f"au plus proche : {raison}.",
            non_couvertes=non_couvertes,
        )

    async def _departage(
        self,
        task: Task,
        candidats: tuple[Agent, ...],
        required: frozenset[str],
        non_couvertes: tuple[str, ...] = (),
    ) -> RoutingDecision:
        """Tranche un cas ambigu au classifieur, ou replie en « à assigner »."""
        noms = ", ".join(a.nom for a in candidats)
        if self._classifier is None:
            return self._repli(
                task,
                raison=f"règles de compétences non concluantes ({noms}) et aucun classifieur",
                non_couvertes=non_couvertes,
            )
        try:
            verdict = await self._classifier.classify(task, candidats)
        except Exception as exc:  # repli plutôt qu'un mauvais routage — jamais levé
            return self._repli(
                task,
                raison=f"classifieur indisponible ({exc})",
                non_couvertes=non_couvertes,
            )

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
            non_couvertes=non_couvertes,
        )

    def _repli(
        self,
        task: Task,
        *,
        raison: str,
        confiance: float = 0.0,
        non_couvertes: tuple[str, ...] = (),
    ) -> RoutingDecision:
        """Construit la décision de repli : tâche marquée « à assigner », cause consignée."""
        return RoutingDecision(
            task=task,
            agent=None,
            score=0,
            confiance=confiance,
            methode=METHODE_REPLI,
            raison=f"à assigner : {raison} — repli explicite plutôt qu'un mauvais routage.",
            non_couvertes=non_couvertes,
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
