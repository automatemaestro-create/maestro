"""Mesure d'usage des appels modèle — tokens, coût, durée, outils (ticket #8).

`StepUsage` est la mesure d'usage d'une étape (un ou plusieurs appels modèle
agrégés). Sa remontée passe par un **collecteur de contexte** (`contextvars`) :
l'appelant ouvre `collect_usage()` autour d'un travail, et les fournisseurs
signalent chaque appel via `report_usage(...)` — hors collecteur, le signalement
est sans effet. Ce canal évite d'élargir la signature de `ModelProvider` : un
fournisseur sans télémétrie fonctionne tel quel, ses étapes remontent simplement
sans usage. Le contexte se propageant à travers `await`, le canal traverse toutes
les couches (orchestrateur, runtime outillé) sans paramètre supplémentaire.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, TypeVar

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
    """

    appels: int = 0
    tokens_entree: int = 0
    tokens_sortie: int = 0
    cout_usd: float | None = None
    duree_ms: int | None = None
    duree_api_ms: int | None = None
    tours: int = 0
    outils: tuple[str, ...] = ()

    @property
    def tokens_total(self) -> int:
        """Total des tokens consommés (entrée + sortie)."""
        return self.tokens_entree + self.tokens_sortie

    def fusion(self, autre: StepUsage) -> StepUsage:
        """Agrège deux mesures : somme des compteurs, union ordonnée des outils."""
        return StepUsage(
            appels=self.appels + autre.appels,
            tokens_entree=self.tokens_entree + autre.tokens_entree,
            tokens_sortie=self.tokens_sortie + autre.tokens_sortie,
            cout_usd=_somme_optionnelle(self.cout_usd, autre.cout_usd),
            duree_ms=_somme_optionnelle(self.duree_ms, autre.duree_ms),
            duree_api_ms=_somme_optionnelle(self.duree_api_ms, autre.duree_api_ms),
            tours=self.tours + autre.tours,
            outils=self.outils + tuple(o for o in autre.outils if o not in self.outils),
        )

    def avec_duree(self, duree_ms: int) -> StepUsage:
        """Copie de la mesure avec la durée horloge posée par l'appelant."""
        return replace(self, duree_ms=duree_ms)

    def resume_court(self) -> str:
        """Rend la mesure en une ligne lisible (pour les synthèses Markdown)."""
        duree = "n/d" if self.duree_ms is None else f"{self.duree_ms / 1000:.1f} s"
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
            "tours": self.tours,
            "outils": list(self.outils),
        }


class UsageCollector:
    """Accumule les mesures signalées pendant un bloc `collect_usage()`."""

    def __init__(self) -> None:
        self._total = StepUsage()

    @property
    def total(self) -> StepUsage:
        """Agrégat des mesures récoltées jusqu'ici."""
        return self._total

    def add(self, usage: StepUsage) -> None:
        """Ajoute une mesure à l'agrégat."""
        self._total = self._total.fusion(usage)


#: Collecteur actif du contexte courant (None hors de tout `collect_usage()`).
_COLLECTEUR: ContextVar[UsageCollector | None] = ContextVar(
    "maestro_usage_collector", default=None
)


@contextmanager
def collect_usage() -> Iterator[UsageCollector]:
    """Ouvre un collecteur : les `report_usage` du bloc s'y accumulent.

    Un bloc imbriqué masque le collecteur englobant (pas de double comptage) ;
    à la sortie, le collecteur précédent est restauré.
    """
    collector = UsageCollector()
    token = _COLLECTEUR.set(collector)
    try:
        yield collector
    finally:
        _COLLECTEUR.reset(token)


def report_usage(usage: StepUsage) -> None:
    """Signale l'usage d'un appel modèle au collecteur actif (no-op sans collecteur).

    C'est le point d'entrée des fournisseurs : appelé après chaque appel modèle,
    sans qu'ils aient à savoir si quelqu'un écoute.
    """
    collector = _COLLECTEUR.get()
    if collector is not None:
        collector.add(usage)
