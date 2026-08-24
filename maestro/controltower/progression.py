"""La **progression d'un run** par statut de tâche (ticket #473, lot 1 de #472).

Un run n'était jusqu'ici lisible que par deux scalaires — `nb_taches` et
`cout_usd` — et par sa trace événement par événement. « Où en est ce run ? » se
répondait donc en recomptant les cartes du Kanban côté front, c'est-à-dire en
réécrivant la machine à états du moteur dans du TypeScript, sur les seules
tâches que la vue avait chargées. Ce module en fait **un compte unique**, rendu
par l'API : `GET /api/executions` et `GET /api/executions/{run_id}` portent la
même `progression`, et le front n'a plus qu'à l'afficher.

Cinq compartiments, ceux du critère : **à faire**, **en cours**, **bloquées**,
**terminées**, **échecs**. Ils ne sont pas un vocabulaire de plus : chacun
rassemble des statuts de la machine à états de [docs/03 §3](
../../docs/03-modele-de-donnees.md), et la table `COMPARTIMENT_PAR_STATUT`
ci-dessous **est** le contrat que les vues partagent — c'est elle que lit une
colonne de Kanban, pas une correspondance réinventée par écran.

Un sixième compartiment, `autres`, ramasse ce que la table ne connaît pas. Il
n'est pas une commodité : sans lui, un statut nouveau (ou une tâche dont aucun
événement n'a encore porté de statut lisible) disparaîtrait du compte, et
`total` cesserait silencieusement d'égaler le nombre de tâches du run. Un
compartiment visible à 1 se remarque ; une somme fausse, non.

Ce module est une **feuille**, comme `portee.py` : il ne connaît ni FastAPI, ni
HTTP, ni la projection. Il ne sait que compter des statuts.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from maestro.engine.executor import (
    STATUT_BLOQUEE,
    STATUT_ECHEC,
    STATUT_EN_COURS,
    STATUT_TERMINEE,
)

#: Statuts de la machine à états (docs/03 §3) qui n'ont **pas** de constante
#: côté moteur : `assignee` est posé par la réassignation manuelle du Kanban
#: (#52), les trois autres ne sont à ce jour que documentaires — le moteur
#: n'émet ni `backlog`, ni `prete`, ni `en_attente_validation`. Ils sont nommés
#: ici pour que la table ci-dessous couvre la machine à états **entière** : une
#: correspondance qui n'existerait que pour ce qui circule aujourd'hui ferait
#: tomber dans `autres` le jour où le moteur les émettra, c'est-à-dire au pire
#: moment.
STATUT_BACKLOG = "backlog"
STATUT_PRETE = "prete"
STATUT_ASSIGNEE = "assignee"
STATUT_EN_ATTENTE_VALIDATION = "en_attente_validation"

#: Les cinq compartiments du critère #473, plus le ramasse-miettes.
A_FAIRE = "a_faire"
EN_COURS = "en_cours"
BLOQUEES = "bloquees"
TERMINEES = "terminees"
ECHECS = "echecs"
AUTRES = "autres"

#: L'ordre du flux de travail — celui dans lequel une vue les présente.
COMPARTIMENTS = (A_FAIRE, EN_COURS, BLOQUEES, TERMINEES, ECHECS, AUTRES)

#: **Le contrat partagé** : quel compartiment pour quel statut de tâche. Deux
#: partis pris, et ce sont les seuls arbitrages du module :
#:
#: - `assignee` compte pour **à faire**, avec `backlog` et `prete` : la tâche a
#:   un exécutant mais n'a pas commencé, et c'est ce que « à faire » veut dire
#:   dans une barre de progression. La colonne « Assignées » du Kanban est la
#:   même population, vue autrement ;
#: - `en_attente_validation` compte pour **en cours** : la tâche est en vol,
#:   simplement suspendue sur un humain — la machine à états la ramène à
#:   `en_cours` dès l'approbation. La ranger dans `autres` la ferait disparaître
#:   d'un compte de travail en cours qui, lui, continue de l'attendre.
COMPARTIMENT_PAR_STATUT: dict[str, str] = {
    STATUT_BACKLOG: A_FAIRE,
    STATUT_PRETE: A_FAIRE,
    STATUT_ASSIGNEE: A_FAIRE,
    STATUT_EN_COURS: EN_COURS,
    STATUT_EN_ATTENTE_VALIDATION: EN_COURS,
    STATUT_BLOQUEE: BLOQUEES,
    STATUT_TERMINEE: TERMINEES,
    STATUT_ECHEC: ECHECS,
}

#: Les compartiments qui rassemblent les statuts **terminaux** du moteur
#: (`terminee`, `echec`, `bloquee` — docs/03 §3, et le `_STATUTS_TERMINAUX` de
#: la projection) : une tâche qui y est comptée ne bougera plus. C'est ce qui
#: donne `soldees`, donc le dénominateur d'une barre de progression honnête —
#: une tâche bloquée est acquise au même titre qu'une tâche échouée, elle ne
#: sera pas jouée.
COMPARTIMENTS_SOLDES = (TERMINEES, ECHECS, BLOQUEES)


def compartiment(statut: str) -> str:
    """Le compartiment d'un statut de tâche — `autres` si la table l'ignore.

    Jamais d'exception et jamais de rejet : un statut inconnu est un fait à
    montrer, pas une panne. C'est la même règle que la colonne « Autres » du
    Kanban, et elle est ici pour que les deux la lisent au même endroit.
    """
    return COMPARTIMENT_PAR_STATUT.get(statut, AUTRES)


@dataclass(frozen=True)
class Progression:
    """Où en est un run : ses tâches réparties par compartiment.

    `total` est le nombre de tâches comptées — il vaut le `nb_taches` du run,
    par construction : tout statut tombe dans un compartiment, `autres`
    compris. `soldees` est ce qui ne bougera plus (les trois statuts terminaux
    du moteur), de sorte qu'une barre de progression se dessine par une simple
    division, sans que le front ait à savoir lesquels des cinq compartiments
    sont terminaux — ce qui serait exactement la machine à états réécrite
    ailleurs que le critère #473 interdit.
    """

    a_faire: int = 0
    en_cours: int = 0
    bloquees: int = 0
    terminees: int = 0
    echecs: int = 0
    autres: int = 0

    @property
    def total(self) -> int:
        """Le nombre de tâches comptées, tous compartiments confondus."""
        return sum(getattr(self, nom) for nom in COMPARTIMENTS)

    @property
    def soldees(self) -> int:
        """Les tâches qui ne bougeront plus : terminées, échouées ou bloquées."""
        return sum(getattr(self, nom) for nom in COMPARTIMENTS_SOLDES)

    def to_dict(self) -> dict[str, Any]:
        """Réémet la progression en dict JSON-sérialisable (la forme du REST).

        `total` et `soldees` sont **servis** plutôt que laissés à déduire : ce
        sont eux que lit une barre de progression, et les recalculer côté
        client demanderait de savoir quels compartiments sont terminaux.
        """
        compte: dict[str, Any] = {nom: getattr(self, nom) for nom in COMPARTIMENTS}
        compte["soldees"] = self.soldees
        compte["total"] = self.total
        return compte


def progression_des_statuts(statuts: Iterable[str]) -> Progression:
    """Répartit des statuts de tâche en compartiments — un statut, une tâche.

    L'appelant décide **quelles** tâches il compte (celles d'un run, celles
    d'un projet…) ; ce module ne décide que du rangement. Rien n'est dédupliqué
    ici : c'est à la source de ne présenter chaque tâche qu'une fois.
    """
    compte = dict.fromkeys(COMPARTIMENTS, 0)
    for statut in statuts:
        compte[compartiment(statut)] += 1
    return Progression(**compte)
