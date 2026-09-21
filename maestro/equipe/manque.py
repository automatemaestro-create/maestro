"""Le rôle qui manquerait à une équipe pour prendre une tâche (#1041).

[docs/37 §3.5](../../docs/37-decision-equipe-sur-mesure.md) tient une frontière :
**un agent ne recrute pas pendant un run**. Une tâche qu'aucun rôle de l'équipe ne
sait prendre n'ouvre donc pas de poste — elle est **signalée**, et recruter reste un
geste validé hors du run (#1040).

Ce module est la moitié « et qu'est-ce qui manque, au juste ? » de ce signal. Il ne
lit rien, n'écrit rien et ne crée personne : il **nomme**, à partir de deux choses
que l'appelant tient déjà — les compétences que la tâche demande, et les fiches de
l'équipe. Le signalement lui-même vit dans `maestro.engine.executor`, au moment du
routage, là où l'on sait que personne n'a pris la tâche.

**Nommer un rôle, pas une liste de tags.** Un signal qui dirait « personne ne couvre
`sql`, `migration` » demande à qui le lit de traduire lui-même en un poste à pourvoir.
Le rôle se cherche donc dans les **gabarits** (`maestro.equipe.gabarits`), qui sont
exactement ce que Maestro sait proposer de recruter : le signal nomme alors la chose
qu'on peut effectivement faire. Quand aucun gabarit ne couvre le manque — un tag que
le catalogue ne connaît pas —, on rend les compétences telles quelles plutôt que
d'inventer un rôle : *un rôle fabriqué se lirait comme un rôle existant*.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from maestro.agents.catalog import Agent
from maestro.equipe.gabarits import GABARITS


@dataclass(frozen=True)
class RoleManquant:
    """Ce qu'aucun rôle de l'équipe ne sait prendre, et le poste qui y répondrait.

    `competences` porte les compétences requises **qu'aucune fiche de l'équipe ne
    possède** — le fait brut, celui que le routage a constaté. `role` est le
    libellé du poste qui manque, et `gabarit` le nom du gabarit à recruter quand
    il y en a un (`None` : aucun gabarit ne couvre ce manque, et `role` ne fait
    alors que redire les compétences — cf. la docstring du module).
    """

    competences: tuple[str, ...]
    role: str
    gabarit: str | None = None

    def phrase(self) -> str:
        """Le signal en une phrase — ce que la ligne du fil affiche telle quelle.

        Elle dit les trois choses qu'il faut pour agir : ce qui manque, le poste
        que cela désigne, et que le recrutement ne se fait pas ici. La dernière
        n'est pas une précaution de style : sans elle, le lecteur d'un run en
        cours peut croire que Maestro va s'en charger, et la tâche resterait « à
        assigner » sans que personne ne bouge.
        """
        manque = ", ".join(self.competences)
        poste = (
            f"il manque un rôle « {self.role} » (gabarit `{self.gabarit}`)"
            if self.gabarit is not None
            else f"il manque un rôle couvrant {self.role}"
        )
        return (
            f"aucun rôle de l'équipe ne couvre : {manque} — {poste}. "
            "Le recrutement se fait hors du run : un agent n'en crée pas un autre "
            "en cours d'exécution."
        )


def competences_non_couvertes(
    competences_requises: Iterable[str], equipe: Sequence[Agent]
) -> tuple[str, ...]:
    """Les compétences demandées que **personne** dans `equipe` ne possède, triées.

    Le fait brut du routage, isolé ici pour qu'il soit le même partout : le
    routeur le calcule sur ses candidats actifs, et ce module le retraduit en
    poste. Triées, parce que `Agent.competences` est un `frozenset` et qu'un
    signal dont l'ordre bouge d'un run à l'autre n'est pas comparable.
    """
    demandees = frozenset(competences_requises)
    couvertes: frozenset[str] = frozenset().union(*(a.competences for a in equipe))
    return tuple(sorted(demandees - couvertes))


def role_manquant(non_couvertes: Sequence[str]) -> RoleManquant | None:
    """Le poste qui répondrait à `non_couvertes` — `None` s'il n'y a rien à combler.

    Le gabarit retenu est celui qui couvre **le plus** de compétences manquantes ;
    à égalité, le premier de `GABARITS`, dont l'ordre est celui du catalogue —
    même départage déterministe que le routage par compétences
    (`maestro.router.assign`). Un gabarit qui n'en couvrirait aucune n'est pas un
    candidat : proposer « Designer » pour une tâche qui demande `monitoring`
    serait un recrutement au hasard.
    """
    if not non_couvertes:
        return None
    manquantes = frozenset(non_couvertes)
    meilleur = max(
        GABARITS,
        key=lambda gabarit: len(manquantes & frozenset(gabarit.competences)),
    )
    couvertes = manquantes & frozenset(meilleur.competences)
    if not couvertes:
        return RoleManquant(
            competences=tuple(non_couvertes),
            role=", ".join(non_couvertes),
        )
    return RoleManquant(
        competences=tuple(non_couvertes),
        role=meilleur.role,
        gabarit=meilleur.nom,
    )
