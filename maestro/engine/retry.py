"""Relance automatique des échecs transitoires — politique et classification (ticket #91, ENF-06).

La démo V1 (docs/13 §4.3) a mesuré ~15 % d'aléas fournisseur transitoires (erreur
immédiate ou crash du sous-processus SDK) : sans relance, un seul aléa condamne le
run entier. Ce module porte la réponse, côté **couche d'abstraction** — donc
indépendante du fournisseur :

- `PolitiqueRelance` : combien de tentatives, avec quel backoff. Injectée à
  `LocalExecutor`, qui relance la production du livrable (l'appel fournisseur)
  tant que l'échec est transitoire et que les tentatives ne sont pas épuisées ;
- `est_transitoire` : la classification. Par **exclusion** : tout échec de
  réalisation est présumé transitoire (c'est lui que la relance vise), *sauf*
  les causes déterministes qui reproduiraient le même échec — plafond de coût
  (`PlafondDepenseDepasse`), plafond de tours (`TurnLimitReached`, mué par chaque
  fournisseur depuis son signal natif), capacité non supportée
  (`UnsupportedCapability`).

Les deux autres échecs *jamais relancés* du cahier des charges n'atteignent pas
cette classification par construction : le **time-out d'échéance ferme** (#64)
borne la réalisation *relances comprises* (la boucle de relance court sous
l'échéance — à l'échéance, l'échec est consigné sans nouvelle tentative), et le
**refus de validation humaine** stoppe la tâche avant toute réalisation
(`LocalExecutor._valide_si_sensible`).

Le plafond de dépense par run couvre aussi les tentatives supplémentaires : la
relance ne change rien aux garde-fous (#9), elle s'exécute sous eux.
"""

from __future__ import annotations

from dataclasses import dataclass

from maestro.providers.base import TurnLimitReached, UnsupportedCapability
from maestro.telemetry import PlafondDepenseDepasse


@dataclass(frozen=True)
class PolitiqueRelance:
    """Politique de relance d'une tâche en échec transitoire. Immuable.

    `max_tentatives` est le nombre **total** d'exécutions permises (la première
    comprise) : 3 ⇒ jusqu'à 2 relances ; 1 ⇒ aucune relance (comportement
    historique). `backoff_s` est l'attente avant la première relance,
    multipliée par `facteur` à chaque relance suivante (backoff exponentiel).
    """

    max_tentatives: int = 3
    backoff_s: float = 2.0
    facteur: float = 2.0

    def __post_init__(self) -> None:
        if self.max_tentatives < 1:
            raise ValueError(
                f"max_tentatives doit être ≥ 1 (reçu : {self.max_tentatives})."
            )
        if self.backoff_s < 0:
            raise ValueError(f"backoff_s doit être ≥ 0 (reçu : {self.backoff_s}).")
        if self.facteur < 1:
            raise ValueError(f"facteur doit être ≥ 1 (reçu : {self.facteur}).")

    def attente_s(self, tentative: int) -> float:
        """L'attente (secondes) avant la relance qui suit la tentative n° `tentative`."""
        return self.backoff_s * self.facteur ** (tentative - 1)


#: Politique par défaut des vrais runs (`OrchestrationEngine.default()`, workers
#: de la file) : 3 tentatives (2 relances), backoff exponentiel 2 s puis 4 s.
RELANCE_DEFAUT = PolitiqueRelance()


def est_transitoire(erreur: BaseException) -> bool:
    """L'échec de réalisation `erreur` est-il transitoire (donc relançable) ?

    Classification par exclusion : les seules causes **non transitoires** qui
    remontent en exception de la réalisation sont les plafonds (coût du run,
    tours de l'exécution agentique) et une capacité que le fournisseur n'a pas —
    relancer reproduirait le même échec. Tout le reste (crash du sous-processus
    SDK, erreur immédiate du CLI, coupure réseau…) est un aléa fournisseur :
    présumé transitoire, c'est la cible d'ENF-06.
    """
    return not isinstance(
        erreur, PlafondDepenseDepasse | TurnLimitReached | UnsupportedCapability
    )
