"""Les **bornes d'un run** — ce qui l'arrête, et comment on le dit (ticket #990).

`ServiceExecutions.lancer` accepte quatre garde-fous depuis #9 : un plafond de
coût, un plafond de tokens, un délai par tâche, un nombre de tâches en
parallèle. `POST /api/executions` les expose, la ligne de commande aussi — mais
la **seule porte de lancement de l'interface**, la conversation (#666), ne
passait que l'objectif, le projet et le mode de brief. Un run lancé depuis le
chat était donc, littéralement, sans borne : mesuré à 12,51 $ par le retex du
2026-09-11 (G5), sans qu'aucun écran permette d'y mettre une limite.

Ce module porte les quatre valeurs **ensemble**, et il existe pour une raison
précise : elles traversent quatre couches — le corps HTTP
(`app.CadrageDecisionRequete`), le service de chat (`chat.ServiceChat`), le
répondeur (`orchestration.RepondeurOrchestration`), puis `lancer`. Les passer en
quatre arguments nommés à chaque étage rendait chaque signature illisible et,
surtout, laissait quatre occasions d'en oublier un en silence. Un objet les fait
voyager d'un bloc : on ne peut pas en perdre trois sur quatre.

## Ce qu'il ne fait pas : valider

`lancer` refuse déjà un garde-fou hors bornes (« doit être > 0 »), **avant toute
écriture**, et c'est le bon endroit : la règle vit là où elle est appliquée, une
seule fois, pour les deux portes d'entrée. La redoubler ici donnerait deux
formulations de « un plafond est un maximum » qui finiraient par diverger. Ce
module **transporte** et **raconte** ; il ne juge pas.

## Ce qu'il fait : dire ce que les bornes font

La veille de conception du ticket (commentaire de #990, d'après les budgets de
GitHub) a rendu ce parti pris : *la borne dit ce qu'elle fait, pas seulement son
chiffre*. GitHub ne se contente pas d'afficher « $50 budget », il met à côté une
colonne « Stop usage : Yes » — la limite et son effet sont deux informations, et
c'est la seconde qui rend la première utile.

D'où `en_phrase()`, et d'où le fait qu'elle **parle aussi quand il n'y a aucune
borne** : « aucune borne : le run ira jusqu'au bout ». C'est le troisième critère
du ticket, et c'est la règle de la ligne `plan :` d'un run d'outillage (#286) —
un régime s'annonce **dans les deux sens**, sans quoi l'illimité se lit comme un
oubli plutôt que comme un choix.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def _nombre(valeur: Any) -> float | None:
    """La valeur telle quelle si c'est un nombre exploitable, `None` sinon.

    Ni validation ni refus (voir le module) : on écarte ce qui n'est pas un
    nombre — `None`, une chaîne vide, un booléen — et on laisse passer le reste,
    `lancer` restant seul juge de ce qui est hors bornes.
    """
    if valeur is None or isinstance(valeur, bool):
        return None
    if isinstance(valeur, (int, float)):
        return float(valeur)
    return None


def _cout(montant: float) -> str:
    """Un plafond de coût en mots du produit — « 5,00 $ ».

    La virgule décimale et le symbole suivent `formatCout` (`apps/web/lib/format`)
    : le même run lu dans le fil puis sur son écran ne doit pas afficher deux
    montants d'apparence différente (règle de #571).
    """
    return f"{montant:.2f}".replace(".", ",") + " $"


def _entier(valeur: float) -> str:
    """« 200000 » → « 200 000 » : le séparateur de milliers, comme l'UI.

    L'espace est une **espace fine insécable** (U+202F) et non une espace
    ordinaire : c'est celle qu'`Intl.NumberFormat("fr-FR")` produit, donc celle
    que `formatTokens` (`apps/web/lib/format`) affiche. Le même compte lu dans
    le fil et sur l'écran du run ne doit pas se présenter de deux façons (#571).
    """
    return f"{int(valeur):,}".replace(",", " ")


def _duree(secondes: float) -> str:
    """« 120 s » — la seconde est l'unité du garde-fou, on ne la traduit pas."""
    entier = int(secondes)
    return f"{entier} s" if entier == secondes else f"{secondes:g} s"


@dataclass(frozen=True)
class BornesRun:
    """Jusqu'où un run peut aller — les quatre garde-fous de `lancer`, ensemble.

    Chaque champ est optionnel et `None` veut dire « pas de borne » : c'est déjà
    le contrat de `lancer`, et le reprendre tel quel évite d'inventer un second
    vocabulaire (un `0` qui voudrait dire « illimité », par exemple, que rien ne
    distinguerait d'une saisie fautive).
    """

    plafond_cout_usd: float | None = None
    plafond_tokens: int | None = None
    timeout_tache_s: float | None = None
    parallelisme: int | None = None

    @classmethod
    def depuis(cls, charge: Mapping[str, Any] | None) -> BornesRun:
        """Les bornes lues dans un corps de requête — tout ce qui manque vaut « aucune ».

        Tolérante par construction : un client qui n'envoie rien obtient
        `AUCUNE_BORNE`, donc exactement le comportement d'avant ce ticket.
        """
        if not charge:
            return AUCUNE_BORNE
        cout = _nombre(charge.get("plafond_cout_usd"))
        tokens = _nombre(charge.get("plafond_tokens"))
        delai = _nombre(charge.get("timeout_tache_s"))
        parallele = _nombre(charge.get("parallelisme"))
        return cls(
            plafond_cout_usd=cout,
            plafond_tokens=None if tokens is None else int(tokens),
            timeout_tache_s=delai,
            parallelisme=None if parallele is None else int(parallele),
        )

    @property
    def aucune(self) -> bool:
        """Aucune des quatre n'est posée — le run ira jusqu'au bout."""
        return all(
            valeur is None
            for valeur in (
                self.plafond_cout_usd,
                self.plafond_tokens,
                self.timeout_tache_s,
                self.parallelisme,
            )
        )

    def en_phrase(self) -> str:
        """Ce que ces bornes **font**, en une ligne — y compris quand il n'y en a aucune.

        Les verbes sont ceux de l'effet et non de la valeur (« s'interrompt à »
        plutôt que « plafond de ») : c'est le parti pris tiré des budgets GitHub,
        et c'est ce qui rend la phrase utile au moment de lancer.
        """
        if self.aucune:
            return "aucune borne : le run ira jusqu'au bout"
        morceaux: list[str] = []
        if self.plafond_cout_usd is not None:
            morceaux.append(f"s'interrompt à {_cout(self.plafond_cout_usd)}")
        if self.plafond_tokens is not None:
            morceaux.append(f"s'interrompt à {_entier(self.plafond_tokens)} tokens")
        if self.timeout_tache_s is not None:
            morceaux.append(f"{_duree(self.timeout_tache_s)} par tâche")
        if self.parallelisme is not None:
            tache = "tâche" if self.parallelisme <= 1 else "tâches"
            morceaux.append(f"{self.parallelisme} {tache} à la fois")
        return " · ".join(morceaux)


#: Le régime par défaut — celui de tous les runs ouverts depuis le fil avant
#: #990. Une constante nommée plutôt qu'un `BornesRun()` écrit partout : « aucune
#: borne » est un **choix** que le produit affiche, pas un objet vide qu'on
#: construit distraitement.
AUCUNE_BORNE = BornesRun()
