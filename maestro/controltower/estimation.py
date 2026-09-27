"""L'**ordre de grandeur** de ce qu'un run proposé coûterait (ticket #1184).

Une proposition de run (« Je lance ? ») se tranchait sur un objectif et des bornes
brutes — tokens, secondes par tâche —, sans rien qui dise ce que l'accord allait
engager. Or l'estimation existait déjà, deux fois dans le produit : sur le brief
qu'on approuve (`apps/web/lib/estimation.ts`, `estimerSuite`, #322) et sur la
question posée au plafond de dépense (`estimerReste`, #1182). Ce module est la
**même** estimation, portée côté API, pour la raison qui la fait vivre ici plutôt
qu'à l'écran : elle entre aussi dans les **faits du juge**. À « combien ça
coûtera ? », l'orchestrateur répond avec les chiffres que la carte montre, et non
avec une généralité — un chiffre qu'il ne voit pas, il l'inventerait ou le tairait.

## La même méthode, aux mêmes chiffres

Les coûts de référence sont ceux de [docs/09 §4.3](../../docs/09-exemple-chiffre.md),
l'estimation détaillée d'une fonctionnalité complète, et **d'aucune mesure de ce
run-ci** : le découpage (0,80 $), une tâche entre 0,74 $ et 1,40 $, une marge de
relance de ≈ +30 % sur la borne haute seule, un plancher de trois tâches. Ce sont
les constantes d'`estimation.ts`, recopiées parce que deux langages : un test les
confronte (`tests/test_accord_et_proposition.py`), et c'est lui qui empêche la
carte d'une proposition et celle d'un brief de chiffrer le même travail de deux
façons.

## Ce qui change : qui compte les tâches

Le nombre de tâches est inconnu avant la décomposition, par construction. Le brief
prend son nombre de critères d'acceptation ; une proposition n'a pas encore de
brief. C'est le **modèle** qui l'estime, en même temps qu'il propose : il vient de
comprendre le travail, il sait si c'est une retouche ou une application entière
— ce qu'aucune règle tirée du texte ne saurait (« Maestro juge, il ne bride pas »,
#1169). Le code n'applique ensuite que ce qui se vérifie : le plancher, et les
coûts de référence. Sans estimation du modèle — un modèle qui l'omet, une
proposition reposée après une équipe créée —, c'est le plancher seul.

## Ce qu'elle ne fait pas : borner

Aucune borne n'est imposée par défaut (#494), et l'estimation n'en devient pas
une : elle **informe** celui qui décide, elle ne plafonne rien. Un run accepté sur
une estimation de 6 $ part sans plafond si personne n'en a posé.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: Le découpage lui-même (le Chef de projet) — docs/09 §4.3, ligne « Découpage +
#: synthèse ». Le seul poste qu'un accord engage à coup sûr. Jumeau de
#: `COUT_DECOMPOSITION_USD` (`apps/web/lib/estimation.ts`).
COUT_DECOMPOSITION_USD = 0.8

#: Ce que coûte une tâche, borne basse et borne haute — docs/09 §4.3. L'écart
#: entre les deux **est** l'information : une tâche coûte « autour d'un dollar ».
COUT_TACHE_USD_BAS = 0.74
COUT_TACHE_USD_HAUT = 1.4

#: La marge de relance, sur la borne haute seule — docs/09 §4.3 (≈ 7 $ → ≈ 9 $).
MARGE_RELANCES = 1.3

#: Le plancher du nombre de tâches : faire, vérifier, livrer. Trois est le plus
#: petit compte qui ne mente pas par optimisme.
NB_TACHES_PLANCHER = 3


def _taches(valeur: Any) -> int | None:
    """Le nombre de tâches qu'un modèle a écrit, s'il en est un — `None` sinon.

    Tolérant comme `bornes._nombre` : un entier, un flottant, ou une chaîne qui
    en porte un (« 5 ») passent ; un booléen, une phrase ou un nombre nul ne
    disent rien et valent « pas d'estimation », c'est-à-dire le plancher.
    """
    if valeur is None or isinstance(valeur, bool):
        return None
    if isinstance(valeur, str):
        try:
            valeur = float(valeur.strip().replace(",", "."))
        except ValueError:
            return None
    if not isinstance(valeur, (int, float)) or valeur != valeur or valeur < 1:
        return None
    return round(valeur)


def _dollars(montant: float) -> str:
    """« 3,02 $ » — la virgule et le symbole de `formatCout`, comme `bornes._cout`."""
    return f"{montant:.2f}".replace(".", ",") + " $"


@dataclass(frozen=True)
class EstimationRun:
    """Ce qu'un accord engagerait, en ordre de grandeur : des tâches et une fourchette.

    `taches` est le compte retenu, plancher appliqué ; `estimees` dit s'il vient du
    modèle (`True`) ou du seul plancher (`False`) — l'écran et le juge ne le
    présentent pas de la même façon, un plancher n'étant pas une estimation du
    travail. `bas_usd` et `haut_usd` sont la fourchette, découpage compris.
    """

    taches: int
    bas_usd: float
    haut_usd: float
    estimees: bool = True

    def to_dict(self) -> dict[str, Any]:
        """L'estimation en JSON — la forme du REST et du stockage."""
        return {
            "taches": self.taches,
            "bas_usd": round(self.bas_usd, 2),
            "haut_usd": round(self.haut_usd, 2),
            "estimees": self.estimees,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EstimationRun:
        """Relit une estimation persistée, **sans la recalculer** : c'est celle qu'on a montrée."""
        return cls(
            taches=int(data.get("taches") or 0),
            bas_usd=float(data.get("bas_usd") or 0.0),
            haut_usd=float(data.get("haut_usd") or 0.0),
            estimees=bool(data.get("estimees", True)),
        )

    def en_phrase(self) -> str:
        """L'estimation en une phrase, pour les faits du juge — sa nature comprise.

        Elle dit d'où vient le chiffre et ce qu'il ne fait pas : un ordre de
        grandeur dont on ignore la provenance ne se conteste pas, et une
        estimation prise pour un plafond ferait croire à une borne qui n'existe
        pas (#494).
        """
        compte = f"{self.taches} tâche{'s' if self.taches > 1 else ''}"
        qui = (
            f"découpage puis ≈ {compte}, que tu as estimées"
            if self.estimees
            else f"découpage puis {compte} au moins — le plancher, faute d'estimation"
        )
        return (
            f"≈ {_dollars(self.bas_usd)} à {_dollars(self.haut_usd)} ({qui}). C'est un "
            "ordre de grandeur tiré de coûts de référence par tâche, pas une mesure de "
            "ce run ni un devis — et il ne borne rien : sans borne posée, le run ira "
            "jusqu'au bout"
        )


def estimer_run(taches: Any = None) -> EstimationRun:
    """L'estimation d'un run proposé, pour le nombre de tâches que le modèle a donné.

    `taches` est la valeur **brute** du verdict : ce qui n'est pas un nombre de
    tâches exploitable vaut « aucune estimation », et c'est alors le plancher.
    """
    lu = _taches(taches)
    compte = max(NB_TACHES_PLANCHER, lu or 0)
    return EstimationRun(
        taches=compte,
        bas_usd=COUT_DECOMPOSITION_USD + compte * COUT_TACHE_USD_BAS,
        haut_usd=COUT_DECOMPOSITION_USD + compte * COUT_TACHE_USD_HAUT * MARGE_RELANCES,
        estimees=lu is not None,
    )
