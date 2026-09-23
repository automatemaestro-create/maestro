"""Le rôle qui manquerait à une équipe pour prendre une tâche (#1041), ou un plan (#1227).

[docs/37 §3.5](../../docs/37-decision-equipe-sur-mesure.md) tenait une frontière :
**un agent ne recrute pas pendant un run**. Un agent ne recrute toujours pas — et
c'est [docs/31 §3.5](../../docs/31-decision-surface-ecriture-agents.md) qui le dit,
définitivement. Ce qui a changé avec #1227 est **qui** propose : l'orchestrateur, à
la personne, avant d'exécuter. Les deux dernières puces de docs/37 §3 — « recruter
reste un geste validé hors du run », « l'équipe ne se forme pas dans un run » — sont
renversées ; rien n'est recruté sans accord (#1040), et c'est la seule promesse qui
comptait.

Ce module est la moitié « et qu'est-ce qui manque, au juste ? » de ce signal. Il ne
lit rien, n'écrit rien et ne crée personne : il **nomme**, à partir de deux choses
que l'appelant tient déjà — les compétences que le travail demande, et les fiches de
l'équipe. Il le fait à deux moments, qui ne se confondent pas :

- `competences_non_couvertes` / `role_manquant` — **une tâche**, au moment du
  routage (`maestro.engine.executor`), là où l'on sait que personne ne l'a prise ;
- `manque_au_plan` (#1227) — **le plan entier**, juste après la décomposition
  (`maestro.engine.loop`), avant que quoi que ce soit ne soit exécuté. C'est le
  moment utile : un rôle recruté là prend ses tâches, un rôle recruté au routage
  arrive après que la tâche est partie « à assigner ».

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
from typing import Protocol

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

    `couvre` (#1227) porte **ce que ce poste-là couvre** parmi les compétences
    manquantes. Il n'est pas redondant avec `competences` : le gabarit retenu est
    celui qui en couvre le plus, pas forcément toutes — et proposer un rôle en
    laissant croire qu'il prend tout ce qui manque serait un recrutement vendu
    pour plus qu'il n'est. Vide quand aucun gabarit ne couvre le manque : il n'y a
    alors pas de poste, donc rien qui couvre quoi que ce soit.
    """

    competences: tuple[str, ...]
    role: str
    gabarit: str | None = None
    couvre: tuple[str, ...] = ()

    def phrase(self) -> str:
        """Le signal en une phrase — ce que la ligne du fil affiche telle quelle.

        Elle dit les deux choses qu'il faut pour agir : ce qui manque, et le poste
        que cela désigne. Elle ne dit plus « le recrutement se fait hors du run »
        (#1227) : c'est faux depuis que l'orchestrateur propose de compléter
        l'équipe avant d'exécuter, et une phrase qui renverrait ailleurs ferait
        chercher un écran là où un geste attend dans le fil.
        """
        manque = ", ".join(self.competences)
        poste = (
            f"il manque un rôle « {self.role} » (gabarit `{self.gabarit}`)"
            if self.gabarit is not None
            else f"il manque un rôle couvrant {self.role}"
        )
        return f"aucun rôle de l'équipe ne couvre : {manque} — {poste}."


class TacheDuPlan(Protocol):
    """Ce qu'une tâche doit porter pour être confrontée à l'équipe (#1227).

    Deux attributs, et pas la `Task` du schéma : ce module est pur et n'a pas à
    connaître `maestro.orchestrator` — ni son `id`, ni ses dépendances, ni sa
    description n'entrent dans la question *qui sait prendre ça ?*.
    """

    @property
    def titre(self) -> str:
        """L'intitulé, tel qu'il sera nommé à qui décide du recrutement."""
        ...  # pragma: no cover - protocole

    @property
    def competences_requises(self) -> tuple[str, ...]:
        """Les tags que la tâche demande, tels que la décomposition les a écrits."""
        ...  # pragma: no cover - protocole


@dataclass(frozen=True)
class ManqueAuPlan:
    """Le poste que **ce plan-là** appelle et que l'équipe n'a pas (#1227).

    `manque` est le poste, calculé sur l'union de ce que le plan demande ;
    `taches` nomme les tâches qu'il prendrait — celles dont une compétence entre
    dans ce qu'il couvre. Les deux ensemble sont ce qu'on montre avant d'exécuter :
    le rôle, sa raison, et le travail qui l'attend.

    Les titres plutôt que les tâches : ce qui est montré à qui décide est ce qu'il
    lit, et transporter des `Task` entières ferait voyager jusqu'au fil une
    description de quatre sections dont personne n'a besoin pour dire oui.
    """

    manque: RoleManquant
    taches: tuple[str, ...]

    @property
    def recrutable(self) -> bool:
        """Y a-t-il un poste à proposer ? — sinon il n'y a qu'un fait à nommer.

        Faux quand aucun gabarit ne couvre le manque : Maestro ne sait pas
        recruter pour ces compétences-là, et proposer un rôle inventé le ferait
        lire comme un rôle existant (cf. `role_manquant`). Le manque est alors
        **dit** au journal, et le run continue — c'est exactement la conduite
        d'avant #1227, appliquée au seul cas où elle reste la bonne.
        """
        return self.manque.gabarit is not None

    def raison(self) -> str:
        """Pourquoi ce rôle, à montrer à qui décide — le plan, pas le projet.

        C'est la `raison` que porteront le rôle proposé et la carte du fil. Elle
        nomme ce que le plan demande et que personne ne couvre : une équipe se
        conteste en relisant ce qui l'a désignée, et ici ce n'est pas un fichier
        du projet mais **le plan qu'on vient de payer**.

        Elle **ne liste pas les tâches**, alors que `phrase` le fait : la carte du
        fil les affiche à part (`taches` voyage sur la demande), et les dire deux
        fois au même endroit ferait lire la même chose comme deux faits.
        """
        manque = ", ".join(self.manque.couvre or self.manque.competences)
        return (
            f"le plan de ce travail demande {manque}, et aucun rôle de l'équipe "
            "ne le couvre."
        )

    def phrase(self) -> str:
        """Le manque en une ligne, tâches comprises — ce que le journal du run porte.

        Le pendant de `RoleManquant.phrase` pour un plan entier. Les tâches y
        sont **nommées** et non comptées : « 3 tâches » ne dit pas si l'on
        s'apprête à confier une animation à un développeur, et c'est ce qu'on
        relit six mois plus tard pour comprendre ce qui est parti de travers.
        """
        if not self.taches:
            return self.raison()
        travail = ", ".join(f"« {titre} »" for titre in self.taches)
        return f"{self.raison()} {len(self.taches)} tâche(s) le demandent : {travail}."


def manque_au_plan(
    taches: Sequence[TacheDuPlan], equipe: Sequence[Agent]
) -> ManqueAuPlan | None:
    """Le poste qui manque à `equipe` pour exécuter `taches` — `None` si rien ne manque.

    La confrontation de #1227, en une fonction pure : l'union de ce que le plan
    demande, ce que l'équipe ne couvre pas, le poste que cela désigne, et les
    tâches qui l'attendent. Elle se calcule **une fois par run**, juste après la
    décomposition — le routage, lui, repose la même question tâche par tâche, avec
    les mêmes deux verbes (`competences_non_couvertes`, `role_manquant`).

    **Un seul poste**, et c'est une décision : un plan qui appellerait deux rôles
    absents en rendrait ici le plus couvrant, et la personne ne se voit proposer
    qu'un recrutement à la fois. Deux propositions d'affilée pour un seul plan
    feraient deux attentes humaines sur un run qui n'a pas encore commencé, et
    l'équipe se complète aussi bien au tour suivant : ce qui reste non couvert
    après ce recrutement sera signalé au routage (#1041), et découvert en cours
    d'exécution il relève de #1181.

    **Une équipe vide n'est pas une équipe incomplète** : elle rend `None`. Un
    projet sans aucun agent se voit proposer son équipe entière avant même
    d'ouvrir un run (#1146) ; en déduire ici « il manque un rôle » ferait proposer
    un poste unique là où il faut une équipe.
    """
    if not equipe or not taches:
        return None
    demandees = {tag for tache in taches for tag in tache.competences_requises}
    non_couvertes = competences_non_couvertes(demandees, equipe)
    manque = role_manquant(non_couvertes)
    if manque is None:
        return None
    vises = frozenset(manque.couvre or manque.competences)
    return ManqueAuPlan(
        manque=manque,
        taches=tuple(
            tache.titre
            for tache in taches
            if vises & frozenset(tache.competences_requises)
        ),
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
        # Triées, comme `competences` et pour la même raison : un signal dont
        # l'ordre bouge d'un run à l'autre n'est pas comparable, et celui-ci part
        # jusqu'à la carte du fil.
        couvre=tuple(sorted(couvertes)),
    )
