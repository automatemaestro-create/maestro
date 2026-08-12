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
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from maestro.orchestrator.schema import Brief

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
