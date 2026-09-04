"""Mesure d'usage des appels modèle — tokens, coût, durée, outils (ticket #8).

`StepUsage` est la mesure d'usage d'une étape (un ou plusieurs appels modèle
agrégés). Sa remontée passe par un **collecteur de contexte** (`contextvars`) :
l'appelant ouvre `collect_usage()` autour d'un travail, et les fournisseurs
signalent chaque appel via `report_usage(...)` — hors collecteur, le signalement
est sans effet. Ce canal évite d'élargir la signature de `ModelProvider` : un
fournisseur sans télémétrie fonctionne tel quel, ses étapes remontent simplement
sans usage. Le contexte se propageant à travers `await`, le canal traverse toutes
les couches (orchestrateur, runtime outillé) sans paramètre supplémentaire.

Ce canal **relaie** aussi le plafond de dépense (ticket #9) : c'est entre deux
appels modèle que la dépense se contrôle. Un collecteur ouvert avec un contrôle
de dépense (`plafond=`) le consulte à chaque mesure — sans rien compter lui-même :
le contrôle (`maestro.telemetry.costs.PlafondDepense`, #56) relit la comptabilité
par tâche de l'exécution, source unique du coût, et lève `PlafondDepenseDepasse`
depuis `report_usage` dès que le plafond est dépassé — l'appel modèle en cours est
déjà comptabilisé (le coût reste visible), mais la suite de l'étape est stoppée.
Un fournisseur qui ne rapporte pas de coût échappe au plafond *en USD* (coût
« inconnu ») mais reste plafonnable **en tokens** (#113) : le même contrôle
(`PlafondDepense`) accepte un plafond en tokens, toujours opérant.

Depuis #835, ce canal se lit aussi **pendant** le travail : un collecteur ouvert
avec `on_mesure=` rappelle son appelant à chaque mesure signalée, avec le cumul
de l'étape — c'est ainsi que le moteur relève la dépense d'une tâche en cours au
lieu d'attendre son issue —, et `usage_en_cours()` rend ce cumul à qui veut le
lire sans attendre la mesure suivante (l'ouverture d'une tentative, par
exemple). Ni l'un ni l'autre ne comptent quoi que ce soit : le cumul reste celui
que `report_usage` a construit, et la comptabilité du run ne le lit toujours
qu'à la consignation de l'étape.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, Protocol, TypeVar

_T = TypeVar("_T", int, float)


def _somme_optionnelle(a: _T | None, b: _T | None) -> _T | None:
    """Somme deux compteurs optionnels en préservant « inconnu » (None ≠ 0)."""
    if a is None:
        return b
    if b is None:
        return a
    return a + b


@dataclass(frozen=True)
class StepUsage:
    """Usage d'une étape : appels modèle, tokens, coût, durées, tours, outils.

    `duree_ms` est la durée *horloge* de l'étape, posée par l'appelant (le moteur)
    via `avec_duree` ; `duree_api_ms` le temps API cumulé rapporté par le
    fournisseur. `cout_usd` reste None quand aucun fournisseur ne l'a rapporté
    (inconnu, à distinguer d'un coût nul). `tokens_entree` inclut le cache côté
    fournisseur (cf. `maestro.providers.claude`).

    `duree_arbitrage_ms` (#584) est la part de `duree_ms` passée **suspendue à une
    décision humaine** (`maestro.deliberation.CreditArbitrage`) — jamais un temps
    de plus, toujours une part de celui qui est déjà là. C'est ce qui permet de
    lire « le temps d'exécution » (`duree_execution_ms`) sans le confondre avec le
    temps qu'un opérateur a mis à trancher : sans ce champ, une tâche suspendue
    quatre minutes sur un acte sensible se lit exactement comme une tâche lente,
    et l'audit d'un run (#495) range le second au compte du premier.

    Il vaut `None` — inconnu — partout où personne n'a mesuré, et `0` là où l'on a
    mesuré qu'il n'y a pas eu d'arbitrage : la distinction est celle de `cout_usd`,
    et elle vaut ici pour la même raison. Il n'est posé que sur l'étape **finale**
    d'une tâche, la seule qui porte sa durée horloge ; le poser aussi sur les
    étapes annexes (`:validation`, `:refus-outil`, à usage nul) le ferait compter
    deux fois dans `usage_totale`.
    """

    appels: int = 0
    tokens_entree: int = 0
    tokens_sortie: int = 0
    cout_usd: float | None = None
    duree_ms: int | None = None
    duree_api_ms: int | None = None
    duree_arbitrage_ms: int | None = None
    tours: int = 0
    outils: tuple[str, ...] = ()

    @property
    def tokens_total(self) -> int:
        """Total des tokens consommés (entrée + sortie)."""
        return self.tokens_entree + self.tokens_sortie

    @property
    def duree_execution_ms(self) -> int | None:
        """La durée horloge **moins** l'arbitrage : le temps réellement passé à travailler (#584).

        `None` quand la durée horloge l'est — il n'y a alors rien à décomposer.
        Sans arbitrage mesuré, c'est la durée horloge elle-même : une tâche qui
        n'a rien fait arbitrer a passé tout son temps à travailler. Bornée à zéro,
        les deux mesures venant d'horloges qui ne partent pas au même instant.
        """
        if self.duree_ms is None:
            return None
        if self.duree_arbitrage_ms is None:
            return self.duree_ms
        return max(0, self.duree_ms - self.duree_arbitrage_ms)

    def fusion(self, autre: StepUsage) -> StepUsage:
        """Agrège deux mesures : somme des compteurs, union ordonnée des outils."""
        return StepUsage(
            appels=self.appels + autre.appels,
            tokens_entree=self.tokens_entree + autre.tokens_entree,
            tokens_sortie=self.tokens_sortie + autre.tokens_sortie,
            cout_usd=_somme_optionnelle(self.cout_usd, autre.cout_usd),
            duree_ms=_somme_optionnelle(self.duree_ms, autre.duree_ms),
            duree_api_ms=_somme_optionnelle(self.duree_api_ms, autre.duree_api_ms),
            duree_arbitrage_ms=_somme_optionnelle(
                self.duree_arbitrage_ms, autre.duree_arbitrage_ms
            ),
            tours=self.tours + autre.tours,
            outils=self.outils + tuple(o for o in autre.outils if o not in self.outils),
        )

    def avec_duree(self, duree_ms: int, *, arbitrage_ms: int | None = None) -> StepUsage:
        """Copie de la mesure avec la durée horloge posée par l'appelant.

        `arbitrage_ms` (#584) pose du même geste la part de cette durée passée
        suspendue à une décision humaine. Absent, le champ reste `None` : un
        appelant qui ne mesure pas l'arbitrage ne déclare pas qu'il n'y en a pas
        eu — la plupart des appelants de ce verbe (files, activités durables,
        boucle de planification) n'ont aucun arbitrage à mesurer.
        """
        return replace(self, duree_ms=duree_ms, duree_arbitrage_ms=arbitrage_ms)

    def resume_court(self) -> str:
        """Rend la mesure en une ligne lisible (pour les synthèses Markdown)."""
        duree = "n/d" if self.duree_ms is None else f"{self.duree_ms / 1000:.1f} s"
        if self.duree_arbitrage_ms:
            # Nommé **seulement** quand il y en a eu : annoncer « dont 0,0 s
            # d'arbitrage » sur chacune des tâches d'un run apprendrait à ne plus
            # lire la mention, et c'est elle qui doit sauter aux yeux le jour où
            # une tâche a passé quatre minutes à attendre quelqu'un.
            duree += f" (dont {self.duree_arbitrage_ms / 1000:.1f} s d'arbitrage)"
        if not self.appels:
            return f"aucun usage fournisseur rapporté · durée {duree}"
        cout = "n/d" if self.cout_usd is None else f"{self.cout_usd:.4f} $"
        return (
            f"{self.appels} appel(s) modèle · {self.tokens_total} tokens "
            f"({self.tokens_entree} entrée / {self.tokens_sortie} sortie) · "
            f"coût {cout} · durée {duree}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Réémet la mesure en dict JSON-sérialisable."""
        return {
            "appels": self.appels,
            "tokens_entree": self.tokens_entree,
            "tokens_sortie": self.tokens_sortie,
            "tokens_total": self.tokens_total,
            "cout_usd": self.cout_usd,
            "duree_ms": self.duree_ms,
            "duree_api_ms": self.duree_api_ms,
            "duree_arbitrage_ms": self.duree_arbitrage_ms,
            "tours": self.tours,
            "outils": list(self.outils),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StepUsage:
        """Reconstruit une mesure depuis sa forme `to_dict` (aller-retour JSON, #41).

        `tokens_total` (dérivé) est ignoré ; les clés absentes retombent sur les
        défauts — la mesure d'un worker qui ne rapporte rien reste valide.
        """
        return cls(
            appels=data.get("appels", 0),
            tokens_entree=data.get("tokens_entree", 0),
            tokens_sortie=data.get("tokens_sortie", 0),
            cout_usd=data.get("cout_usd"),
            duree_ms=data.get("duree_ms"),
            duree_api_ms=data.get("duree_api_ms"),
            duree_arbitrage_ms=data.get("duree_arbitrage_ms"),
            tours=data.get("tours", 0),
            outils=tuple(data.get("outils", ())),
        )


class ControleDepense(Protocol):
    """Contrôle de dépense consulté par le collecteur à chaque mesure (#9).

    `verifie` reçoit l'usage cumulé de l'étape en cours et lève
    (`PlafondDepenseDepasse`) si la dépense de l'exécution dépasse son plafond (en
    USD **ou en tokens**, #113). L'implémentation du POC est
    `maestro.telemetry.costs.PlafondDepense`, adossée à la comptabilité par tâche
    (#56) — le protocole évite au canal de mesure de dépendre de la comptabilité,
    qui se construit au-dessus de lui.
    """

    def verifie(self, en_cours: StepUsage) -> None:
        """Lève si la dépense de l'exécution, `en_cours` compris, crève le plafond."""
        ...


class UsageCollector:
    """Accumule les mesures signalées pendant un bloc `collect_usage()`.

    Avec `plafond`, relaie le garde-fou de dépense (#9) : chaque mesure est
    d'abord comptabilisée (le coût reste visible), puis le contrôle est consulté
    avec le cumul de l'étape. C'est lui qui confronte la dépense de l'exécution
    à son plafond (#56) — le collecteur ne tient aucun compte parallèle.

    Avec `on_mesure` (#835), il **rappelle** son appelant à chaque mesure, avec
    le cumul de l'étape — **avant** le contrôle de dépense, et c'est l'ordre qui
    compte : une mesure qui fait crever le plafond est encore une dépense, et le
    relevé qui la montre doit partir même si le contrôle stoppe l'étape juste
    après. L'observateur ne casse jamais l'observé (règle du régulateur
    d'activité, `maestro.providers.activite`) : un rappel qui lève est avalé, la
    mesure reste comptée et le contrôle reste consulté.
    """

    def __init__(
        self,
        *,
        plafond: ControleDepense | None = None,
        on_mesure: Callable[[StepUsage], None] | None = None,
    ) -> None:
        self._total = StepUsage()
        self._plafond = plafond
        self._on_mesure = on_mesure

    @property
    def total(self) -> StepUsage:
        """Agrégat des mesures récoltées jusqu'ici."""
        return self._total

    def add(self, usage: StepUsage) -> None:
        """Ajoute une mesure à l'agrégat, rappelle l'observateur, puis consulte le contrôle."""
        self._total = self._total.fusion(usage)
        if self._on_mesure is not None:
            try:
                self._on_mesure(self._total)
            except Exception:  # noqa: BLE001 — observer ne casse jamais l'observé
                pass
        if self._plafond is not None:
            self._plafond.verifie(self._total)


#: Collecteur actif du contexte courant (None hors de tout `collect_usage()`).
_COLLECTEUR: ContextVar[UsageCollector | None] = ContextVar(
    "maestro_usage_collector", default=None
)


@contextmanager
def collect_usage(
    *,
    plafond: ControleDepense | None = None,
    on_mesure: Callable[[StepUsage], None] | None = None,
) -> Iterator[UsageCollector]:
    """Ouvre un collecteur : les `report_usage` du bloc s'y accumulent.

    Un bloc imbriqué masque le collecteur englobant (pas de double comptage) ;
    à la sortie, le collecteur précédent est restauré. `plafond` arme le garde-fou
    de dépense (#9) sur ce bloc : à chaque mesure, le contrôle confronte la dépense
    de l'exécution à son plafond et le `report_usage` fautif lève
    `PlafondDepenseDepasse` chez l'appelant du fournisseur. `on_mesure` (#835)
    reçoit le cumul du bloc à chaque mesure signalée — c'est le relevé en cours
    d'une tâche, celui qui se voit pendant qu'il se dépense.
    """
    collector = UsageCollector(plafond=plafond, on_mesure=on_mesure)
    token = _COLLECTEUR.set(collector)
    try:
        yield collector
    finally:
        _COLLECTEUR.reset(token)


def usage_en_cours() -> StepUsage:
    """Le cumul du collecteur actif — une mesure vide hors de tout `collect_usage()` (#835).

    Lecture seule : c'est ce qu'un appelant lit à un instant où aucune mesure
    n'arrive (l'ouverture d'une tentative, par exemple) pour dire ce que l'étape
    a déjà consommé. Hors collecteur, une mesure vide plutôt qu'une erreur — la
    question « qu'a-t-on dépensé jusqu'ici ? » a alors pour réponse « rien de
    mesuré », ce que `StepUsage()` dit exactement (coût inconnu, zéro appel).
    """
    collector = _COLLECTEUR.get()
    return collector.total if collector is not None else StepUsage()


def report_usage(usage: StepUsage) -> None:
    """Signale l'usage d'un appel modèle au collecteur actif (no-op sans collecteur).

    C'est le point d'entrée des fournisseurs : appelé après chaque appel modèle,
    sans qu'ils aient à savoir si quelqu'un écoute. Si le collecteur actif est
    plafonné (#9) et que ce signalement fait dépasser le plafond de dépense de
    l'exécution (#56), l'appel lève `PlafondDepenseDepasse` — c'est ainsi que le
    garde-fou stoppe l'étape.
    """
    collector = _COLLECTEUR.get()
    if collector is not None:
        collector.add(usage)
