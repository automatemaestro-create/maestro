"""Ce qu'une décision humaine dit **de plus** qu'un oui ou un non (#1185).

Jusqu'à #1185 le validateur rendait un booléen : la raison qu'une personne écrivait
en refusant finissait au journal, jamais chez l'agent, et une approbation ne valait
que pour l'appel qu'on lui montrait. Deux choses voyagent désormais avec le verdict :

- la **consigne** d'un refus — « archive au lieu de supprimer » — rendue à l'agent,
  qui replanifie son geste à partir d'elle au lieu de voir sa tâche annulée ; sa
  nouvelle action passe par l'arbitrage comme n'importe quelle autre ;
- l'**étendue** d'une approbation — cet appel (le défaut), ou cet outil pour la suite
  du run, ou pour le projet. La personne la choisit ; ce n'est jamais un défaut, et
  un accord étendu se relit et se retire dans les permissions de l'agent
  (`maestro.agents.accords`).

Ce module vit **à la racine**, comme `maestro.deliberation`, parce qu'il est lu des
deux côtés d'une frontière : le **moteur** (`maestro.engine.guardrails`) compose le
détail d'une décision, le **fournisseur** (`maestro.providers.arbitrage`) en tire ce
qu'il sert à l'agent — et `maestro.providers` ne dépend pas du moteur.

## Pourquoi le détail est une chaîne qui porte des attributs

Le couple `(approuvée ?, détail)` est le contrat de tout le chemin d'arbitrage :
`Guardrails.demande_validation`, `MemoireArbitrage`, les deux canaux du fournisseur
(`Arbitre`, `ArbitreActe`) et leurs doubles de test. `DetailDecision` **est** ce
détail — une `str`, ce qu'on trace et ce qu'on lit — et porte en plus la consigne et
l'étendue, comme `maestro.agents.permissions.EntreeArbitrage` est un nom d'outil qui
porte son décideur. Élargir le couple en triplet aurait changé la signature de huit
contrats pour deux champs que presque personne ne lit ; les lire se fait par
`consigne_de` et `etendue_de`, jamais en analysant la phrase (#746).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: L'approbation vaut pour **cet appel** et lui seul — le geste par défaut, celui
#: d'avant #1185 au caractère près.
ETENDUE_APPEL = "appel"

#: L'approbation vaut pour **cet outil**, pour tous ses appels par cet agent **sur
#: ce run**. Elle meurt avec le run : un run suivant redemande.
ETENDUE_RUN = "run"

#: L'approbation vaut pour **cet outil**, pour tous ses appels par cet agent **dans
#: ce projet**, jusqu'à ce que quelqu'un la retire des permissions de l'agent.
ETENDUE_PROJET = "projet"

#: Les trois, dans l'ordre de l'écran — du plus étroit au plus large. Une liste
#: **blanche** : une étendue hors d'elles est refusée, jamais ramenée au défaut en
#: silence (le défaut étant le plus étroit, s'y replier ne serait pas dangereux, mais
#: taire qu'on n'a pas compris ce qu'on a reçu le serait).
ETENDUES = (ETENDUE_APPEL, ETENDUE_RUN, ETENDUE_PROJET)

#: Les étendues qui **durent** au-delà de l'appel — celles qui laissent un accord.
ETENDUES_DURABLES = (ETENDUE_RUN, ETENDUE_PROJET)


@dataclass(frozen=True)
class DecisionHumaine:
    """Ce qu'un validateur rend quand la décision dit plus qu'un oui ou un non.

    Un validateur peut toujours rendre un booléen — c'est le contrat de #9, et une
    décision sans consigne ni étendue le reste au caractère près. Celui de la Control
    Tower rend cet objet dès que la personne a écrit une consigne ou étendu son
    accord (`maestro.controltower.validation`). Vrai quand approuvée, pour que tout
    `if decision:` d'avant lise encore juste.

    `motif` n'a de sens que sur un refus, `etendue` que sur une approbation : chacun
    est ignoré sur l'autre geste, comme le motif l'était déjà sur une approbation
    (#272).
    """

    approuve: bool
    motif: str = ""
    etendue: str = ETENDUE_APPEL

    def __bool__(self) -> bool:
        return self.approuve


class DetailDecision(str):
    """Le détail traçable d'une décision, qui porte sa **consigne** et son **étendue**.

    Sous-classe de `str` : c'est la phrase que le journal garde et que le motif servi
    à l'agent reprend — « refusée par le validateur humain — consigne : « … » ». La
    consigne y figure en toutes lettres pour qui relit ; l'attribut est ce que le code
    lit, pour ne jamais la retrouver par un motif de texte.
    """

    __slots__ = ("consigne", "etendue")

    consigne: str
    etendue: str

    def __new__(
        cls, texte: str, *, consigne: str = "", etendue: str = ETENDUE_APPEL
    ) -> DetailDecision:
        objet = super().__new__(cls, texte)
        objet.consigne = consigne
        objet.etendue = etendue
        return objet


def consigne_de(detail: str) -> str:
    """La consigne qu'un refus porte, `""` s'il n'en porte pas (ou si le détail est nu)."""
    return detail.consigne if isinstance(detail, DetailDecision) else ""


def etendue_de(detail: str) -> str:
    """L'étendue d'une approbation, `ETENDUE_APPEL` par défaut (détail nu compris)."""
    return detail.etendue if isinstance(detail, DetailDecision) else ETENDUE_APPEL


#: L'en-tête sous lequel les consignes rejoignent une tâche réorientée. Écrit pour
#: l'agent : ce qu'on lui demande n'est pas d'abandonner, c'est de faire autrement.
EN_TETE_CONSIGNES = (
    "Consignes de la personne qui a refusé l'action telle qu'elle était décrite — "
    "réalise la tâche en les suivant :"
)


def avec_consignes(texte: str, consignes: Sequence[str]) -> str:
    """`texte` suivi des consignes reçues en refusant — `texte` tel quel s'il n'y en a pas.

    C'est la forme d'une tâche **réorientée** (#1185) : sa description d'origine,
    puis ce que la personne a demandé à la place, dans l'ordre où elle l'a dit. La
    même forme est soumise à la personne (elle voit ce que la tâche fera) et servie
    à l'agent (il sait ce qu'on attend de lui).
    """
    propres = [c.strip() for c in consignes if c.strip()]
    if not propres:
        return texte
    lignes = "\n".join(f"- {consigne}" for consigne in propres)
    return f"{texte}\n\n{EN_TETE_CONSIGNES}\n{lignes}"


def portee_de_l_etendue(etendue: str, outil: str) -> str:
    """Ce qu'une étendue durable couvre, en mots — « tout appel de 'Bash' sur ce run »."""
    if etendue == ETENDUE_RUN:
        return f"tout appel de {outil!r} sur ce run"
    if etendue == ETENDUE_PROJET:
        return f"tout appel de {outil!r} dans ce projet"
    return "cet appel"
