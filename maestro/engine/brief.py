"""Validation humaine du brief avant décomposition — le contrat (#320, décision D5).

Le brief structuré existe depuis #318 (`Orchestrator.brief`,
`OrchestrationEngine.etape_brief`) mais rien ne l'attendait : la boucle décomposait
toujours l'objectif brut. Ce module porte ce qui manquait — **le régime du brief**
et **le canal de sa décision** — pour que `OrchestrationEngine.run` puisse
s'arrêter dessus et ne décomposer que ce qu'un humain a approuvé.

C'est le point de contrôle le plus rentable du produit : corriger un plan coûte un
message, corriger douze tâches coûte douze exécutions
([docs/24 §3.3](../../docs/24-projets-locaux-et-poste-de-travail.md)).

**Trois régimes, jamais devinés** (`MODE_BRIEF_*`) — c'est l'appelant qui choisit,
parce que lui seul sait s'il y a quelqu'un devant :

- `sans` — pas d'étape de brief du tout : la boucle décompose l'objectif brut.
  C'est le comportement d'avant ce lot, et le **défaut du moteur** : un appelant
  qui ne dit rien ne se met pas à payer un appel modèle de plus ni à attendre.
- `auto` — le brief est rédigé et devient l'entrée de la décomposition, **sans
  attendre personne**. Le mode d'un lancement sans humain devant (CLI
  `maestro-run`, orchestration autonome) : un run headless qui attend une
  approbation est un run mort.
- `humain` — le run **s'arrête** sur le brief et attend la décision. Le mode d'un
  lancement piloté depuis la Control Tower, donc son défaut à elle (#185).

**Ce n'est pas le validateur d'action sensible** (#48) et il ne faut pas le
détourner : sa décision est un booléen (`DecisionRequete.approuve`), celle-ci
transporte un **brief corrigé** — l'humain ne se contente pas d'approuver, il
réécrit avant d'approuver, et c'est son texte qui part en décomposition. Canal
distinct (`brief.demande`/`brief.decision`), même bus, même patron d'attente :
`ArbitreBrief` est au brief ce que `Validateur` (#9) est à l'action sensible, et
`maestro.controltower.brief` en est l'implémentation Control Tower, pendant exact
de `ValidateurControlTower`.

L'attente est **indéfinie et fail-safe dans le bon sens** : sans arbitre configuré,
ou si l'arbitre lève, on ne décompose pas (`BriefRefuse` / l'exception remonte) —
jamais l'inverse. C'est la moitié du produit qui est gratuite (le brief) qui
protège l'autre (les tâches).

**Un second arrêt s'intercale depuis #321**, avant celui-ci : les **questions de
clarification**. Le brief que rend le Chef de projet porte ce qu'il refuse de
trancher seul (`Brief.questions`) ; plutôt que de décomposer à l'aveugle ou de
faire porter ces zones d'ombre à l'écran de validation, le run les **pose** et
attend les réponses (`ArbitreClarification`), puis **régénère le brief entier** en
les intégrant. Deux différences avec l'arrêt de validation, qui expliquent le canal
distinct : il peut se produire **plusieurs fois** (jusqu'à `tours_clarification`),
et ce qui revient n'est pas une décision mais du **texte libre**. Le plafond est ce
qui empêche la boucle sans fin — atteint, les questions restantes deviennent des
**hypothèses explicites** (`Brief.questions_en_hypotheses`) et le brief part en
validation, où un humain les verra et pourra encore les corriger.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from maestro.orchestrator.schema import Brief, Clarification

#: Régimes du brief dans la boucle (cf. docstring du module). Ensemble **fermé** :
#: un mode inconnu est refusé avant le premier appel modèle plutôt que traité comme
#: un `sans` silencieux — se tromper de mode, c'est soit payer un brief qu'on ne
#: voulait pas, soit suspendre un run que personne ne viendra débloquer.
MODE_BRIEF_SANS = "sans"
MODE_BRIEF_AUTO = "auto"
MODE_BRIEF_HUMAIN = "humain"

#: Les trois modes, dans l'ordre croissant d'implication humaine.
MODES_BRIEF: tuple[str, ...] = (MODE_BRIEF_SANS, MODE_BRIEF_AUTO, MODE_BRIEF_HUMAIN)


class BriefRefuse(RuntimeError):
    """Le brief a été refusé par l'humain : le run s'arrête **avant** toute tâche.

    Levée par `OrchestrationEngine.run` en mode `humain` sur un refus. Distincte
    d'un échec : rien n'a raté, quelqu'un a dit non — c'est pour cela que le
    pilotage par l'API la mue en run « annulée » et non en run « échec » (#185),
    et que rien de payant n'a été engagé au-delà du brief lui-même.
    """


def mode_brief_valide(mode: str | None) -> str:
    """Normalise `mode` en l'un des `MODE_BRIEF_*` — lève `ValueError` sinon.

    None et la chaîne vide retombent sur `sans` : ne rien dire, c'est demander le
    comportement d'avant ce lot. Tout le reste est **refusé**, et refusé tôt : un
    mode mal orthographié doit coûter un 422 au lancement, pas un run suspendu
    pour toujours ou une décomposition d'objectif brut passée inaperçue.
    """
    normalise = (mode or MODE_BRIEF_SANS).strip().lower()
    if normalise not in MODES_BRIEF:
        attendus = ", ".join(MODES_BRIEF)
        raise ValueError(f"mode de brief inconnu : {mode!r} (attendu : {attendus}).")
    return normalise


@dataclass(frozen=True)
class DemandeBrief:
    """Ce qu'un humain reçoit pour trancher : le brief proposé et d'où il vient.

    `objectif` est l'énoncé **brut** du lancement, conservé à côté du brief parce
    que c'est ce que la personne compare : le brief est une reformulation, et on
    n'approuve pas une reformulation sans son original. `run_id` est ce qui relie
    la demande à son run — donc au canal sur lequel la décision reviendra.
    """

    run_id: str
    objectif: str
    brief: Brief


@dataclass(frozen=True)
class DecisionBrief:
    """La décision humaine sur un brief : approuver (tel quel ou corrigé) ou refuser.

    `brief` porte la **version corrigée** quand l'humain a réécrit avant
    d'approuver — c'est elle qui part en décomposition. None : le brief proposé
    est approuvé tel quel. Sur un refus, il est ignoré (rien ne sera décomposé).
    `detail` est la raison, telle qu'elle sera consignée au journal.
    """

    approuve: bool
    brief: Brief | None = None
    detail: str = ""

    def retenu(self, propose: Brief) -> Brief:
        """Le brief qui part en décomposition : le corrigé s'il existe, sinon `propose`.

        Rendu **ici** plutôt que par l'appelant : la règle « ce qui est décomposé
        est le brief tel qu'il a été approuvé » n'a qu'un seul énoncé, et le moteur
        comme la projection y lisent la même chose.
        """
        return self.brief if self.brief is not None else propose


class ArbitreBrief(Protocol):
    """Soumet le brief à un humain et rend sa décision — le contrat de l'attente.

    Pendant exact du `Validateur` des garde-fous (#9) : le moteur ne connaît que
    ce protocole, l'implémentation décide *où* la question est posée
    (`maestro.controltower.brief.ArbitreBriefControlTower` la pose sur le bus de la
    Control Tower). L'attente est **indéfinie** — pas de time-out silencieux : un
    brief en attente ne se résout que par une décision ou par l'arrêt du run.
    """

    async def __call__(self, demande: DemandeBrief) -> DecisionBrief:
        """Rend la décision humaine sur `demande` (attente indéfinie)."""
        ...  # pragma: no cover - protocole


#: Forme appelable acceptée partout où un `ArbitreBrief` est attendu : une simple
#: coroutine convient (les tests en passent une, sans classe intermédiaire).
ArbitreBriefAppelable = Callable[[DemandeBrief], Awaitable[DecisionBrief]]


#: Plafond d'allers-retours de clarification par run (#321). **Deux**, et le chiffre
#: est un arbitrage, pas un réglage neutre : un tour lève l'essentiel (le playbook
#: borne déjà à cinq questions, et les réponses en referment la plupart), un second
#: rattrape ce qu'une réponse a ouvert, un troisième signifie presque toujours que le
#: modèle tourne en rond — et chaque tour coûte un appel modèle **et** une attente
#: humaine, la plus chère des deux. Au-delà, les zones d'ombre restantes deviennent
#: des hypothèses écrites et le brief part en validation : c'est un humain qui
#: tranchera, sur un écran fait pour ça (#322), plutôt qu'un questionnaire sans fin.
TOURS_CLARIFICATION_DEFAUT = 2


def tours_clarification_valide(tours: int | None) -> int:
    """Normalise le plafond d'allers-retours — lève `ValueError` s'il est négatif.

    None retombe sur `TOURS_CLARIFICATION_DEFAUT`. **Zéro est licite et veut dire
    « aucun aller-retour »** : le brief part en validation avec ses questions
    telles quelles, ce qui est exactement le comportement de #320. C'est ce qui
    permet de désarmer la clarification sans retirer l'arbitre, et donc de la
    couper sur un déploiement où personne ne répondrait.
    """
    if tours is None:
        return TOURS_CLARIFICATION_DEFAUT
    if tours < 0:
        raise ValueError(
            f"plafond d'allers-retours de clarification négatif : {tours} "
            "(attendu : 0 pour aucun, ou un entier positif)."
        )
    return tours


def motif_sans_reponse(tours: int) -> str:
    """Le préfixe des questions muées en hypothèses au plafond (#321).

    Énoncé **ici** et pas au point d'appel : c'est le texte que lira l'humain qui
    valide, et deux formulations — une par chemin de conversion — rendraient
    illisible la seule chose qu'il doit comprendre, à savoir que personne n'a
    répondu à ce point-là.
    """
    return f"Sans réponse après {tours} tour(s) de clarification"


@dataclass(frozen=True)
class DemandeClarification:
    """Ce qu'un humain reçoit pour lever les zones d'ombre d'un brief (#321).

    Le pendant de `DemandeBrief` pour l'**autre** question posée à l'humain : là-bas
    « décompose-t-on ce brief ? », ici « que répondez-vous à ce que le Chef de projet
    refuse de trancher seul ? ». Les questions sont celles du `brief` — jamais
    recopiées à côté : le brief est régénéré en entier à chaque tour, donc dupliquer
    sa liste de questions créerait deux vérités dont l'une se périmerait.

    `tour` (à partir de 1) et `tours_max` portent l'**annonce du plafond**, deuxième
    exigence du ticket : celui qui répond doit savoir s'il lui reste un tour ou si
    c'est le dernier — sans quoi il garderait pour plus tard une précision qui ne
    sera plus jamais demandée.
    """

    run_id: str
    objectif: str
    brief: Brief
    tour: int
    tours_max: int

    @property
    def dernier_tour(self) -> bool:
        """Est-ce le dernier aller-retour avant la conversion en hypothèses ?"""
        return self.tour >= self.tours_max


class ArbitreClarification(Protocol):
    """Pose les questions du brief à un humain et rend ses réponses (#321).

    Même patron que `ArbitreBrief`, même bus, canal distinct — et pour la même
    raison qu'entre `brief.*` et `validation.*` : ce qui voyage n'est pas de même
    nature (des réponses de texte libre, pas une décision), et le moment non plus
    (avant la validation, et possiblement plusieurs fois).

    L'attente est **indéfinie** : un run suspendu sur ses questions ne se résout que
    par des réponses ou par l'arrêt du run. C'est ce que la troisième exigence du
    ticket rend supportable — l'attente est un état visible (`en_attente_reponses`,
    son ancienneté, ses questions), et le run reste annulable pendant tout ce temps.

    Les réponses sont rendues **dans l'ordre des questions du brief soumis** : elles
    s'apparient par position, faute d'identité stable d'une régénération à l'autre
    (cf. `Clarification`). Une implémentation qui n'en rendrait pas autant que de
    questions n'est pas une erreur : les manquantes sont traitées comme sans
    réponse, donc muées en hypothèses plutôt que reposées.
    """

    async def __call__(
        self, demande: DemandeClarification
    ) -> tuple[Clarification, ...]:
        """Rend les réponses humaines aux questions de `demande` (attente indéfinie)."""
        ...  # pragma: no cover - protocole


#: Forme appelable acceptée partout où un `ArbitreClarification` est attendu — même
#: commodité que `ArbitreBriefAppelable`.
ArbitreClarificationAppelable = Callable[
    [DemandeClarification], Awaitable[tuple[Clarification, ...]]
]


def apparie_reponses(
    brief: Brief, reponses: Sequence[str]
) -> tuple[Clarification, ...]:
    """Apparie les questions de `brief` aux `reponses`, **par position** (#321).

    Le seul appariement possible, et il est assumé : une question n'a pas
    d'identifiant parce qu'elle n'a pas d'identité stable d'une régénération à
    l'autre (#318, note du schéma). Ce qui le rend sûr est ailleurs — les réponses
    s'adressent au brief **stocké** du run, dont la liste de questions est figée
    entre sa publication et sa réponse.

    Tolérant dans les deux sens plutôt que levant : une réponse **manquante** vaut
    « sans réponse » (la question sera muée en hypothèse, jamais reposée), une
    réponse **en trop** est ignorée (elle ne vise aucune question de ce brief). Le
    contrôle strict de longueur a lieu à l'entrée de l'API, où il y a encore
    quelqu'un pour corriger sa requête ; ici, en plein run, lever coûterait le run.
    """
    return tuple(
        Clarification(
            question=question,
            reponse=reponses[rang] if rang < len(reponses) else "",
        )
        for rang, question in enumerate(brief.questions)
    )
