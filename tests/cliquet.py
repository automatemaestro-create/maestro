"""Un cliquet : une valeur gardée qui ne remonte jamais, et dont chaque baisse s'inscrit (#1523).

Le registre est un tuple `(ticket, valeur)`, du plus ancien au plus récent, écrit dans la suite qui
garde la valeur. Il tient lieu d'historique parce qu'une garde ne peut pas lire celui de git : la CI
clone à profondeur 1, et le conteneur du filet local ne résout pas le `.git` d'un worktree. Une
ligne ne s'ajoute que plus basse que la précédente, et la suite épingle ses premières lignes : sans
quoi une hausse passerait en retouchant le registre.

Deux suites s'en servent : le budget de `CLAUDE.md` (`tests/test_audit_commandes.py`, docs/49
§2.1) et le nombre de dérogations C901 (`tests/test_complexite.py`, docs/53 §5).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise


class CliquetRemonte(AssertionError):
    """Une valeur gardée en cliquet remonte, ou baisse sans que sa ligne soit inscrite."""


@dataclass(frozen=True)
class Cliquet:
    """Ce qu'un cliquet garde, et ce qu'on fait au lieu de le relever — les mots de ses refus."""

    #: Le nom du registre, tel que la suite qui le tient l'écrit.
    registre: str
    #: Ce qui est gardé, en sujet de phrase : « BUDGET_CLAUDE_MD_TOKENS vaut 25000 tokens ».
    valeur: str
    unite: str
    #: Le participe accordé à l'unité : « 19940 tokens livrés », « 70 dérogations livrées ».
    livre: str
    #: Ce qu'on fait au lieu de relever, dit à qui bute.
    regle: str
    #: Le document qu'une hausse inscrite renverserait.
    decision: str


def verifier_cliquet(valeur: int, livres: Sequence[tuple[str, int]], cliquet: Cliquet) -> None:
    """Lève si le registre remonte quelque part, ou si `valeur` n'est pas sa dernière ligne.

    Les deux portes d'une hausse : changer la valeur seule (elle quitte le registre), ou l'inscrire
    au registre (la ligne n'est pas plus basse que la précédente). Une baisse non inscrite lève
    aussi : sans sa ligne, la valeur suivante pourrait remonter jusqu'à l'ancienne sans que rien ne
    rougisse.
    """
    for (avant, precedent), (ticket, livree) in pairwise(livres):
        if livree >= precedent:
            raise CliquetRemonte(
                f"{cliquet.registre} remonte : {ticket} pose {livree} {cliquet.unite} après les "
                f"{precedent} de {avant}. {cliquet.regle} Le relever, c'est renverser "
                f"{cliquet.decision} par écrit."
            )
    ticket, derniere = livres[-1]
    if valeur > derniere:
        raise CliquetRemonte(
            f"{cliquet.valeur} vaut {valeur} {cliquet.unite}, au-dessus des {derniere} "
            f"{cliquet.livre} par {ticket}. {cliquet.regle}"
        )
    if valeur < derniere:
        raise CliquetRemonte(
            f"{cliquet.valeur} descend à {valeur} {cliquet.unite} sans sa ligne dans "
            f"{cliquet.registre} (dernière : {derniere}, {ticket}) : inscris la baisse avec son "
            "ticket, sans quoi la valeur suivante pourrait remonter jusqu'à l'ancienne."
        )
