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


def _max_optionnel(a: int | None, b: int | None) -> int | None:
    """Le plus grand de deux maxima optionnels, en préservant « inconnu » (#1398)."""
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


@dataclass(frozen=True)
class StepUsage:
    """Usage d'une étape : appels modèle, tokens, coût, durées, tours, outils.

    `duree_ms` est la durée *horloge* de l'étape, posée par l'appelant (le moteur)
    via `avec_duree` ; `duree_api_ms` le temps API cumulé rapporté par le
    fournisseur. `cout_usd` reste None quand aucun fournisseur ne l'a rapporté
    (inconnu, à distinguer d'un coût nul). `tokens_entree` inclut le cache côté
    fournisseur (cf. `maestro.providers.claude`) — et, depuis #1398, il dit
    **combien** : voir `tokens_cache_lus` plus bas.

    `tokens_cache_lus` et `tokens_cache_ecrits` (#1398) sont deux **parts** de
    `tokens_entree`, sur le modèle de `tokens_non_tarifes` : jamais des tokens de
    plus. `contexte_max` est le **contexte atteint** — le plus grand prompt d'un
    seul appel d'API —, et il se fusionne par **maximum**, jamais par somme. Sans
    eux, une tâche longue servie par le cache se lisait comme une tâche gloutonne :
    `tokens_entree` additionne le contexte de **chaque** tour, si bien que
    `socle-nextjs` affichait 1 845 438 tokens sur p5 pour un contexte qui n'a jamais
    dépassé ≈ 66 k, et un coût de 1,87 $ — celui d'un travail relu en cache à 90 %
    du prix en moins. Le total reste juste, c'est sa lecture seule qui trompait :
    #8 et #57 l'avaient fait le chiffre de la consommation, il n'en est que le
    volume traité. `contexte_max` vaut `None` — inconnu — là où personne n'a mesuré
    un appel isolément (un résultat cumulé, une ligne d'avant ce ticket) : il ne se
    déduit jamais d'une somme.

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

    `duree_attente_creneau_ms` et `duree_attente_atelier_ms` (#989) sont les deux
    autres parts de `duree_ms` qui ne sont pas du travail : le temps passé à
    attendre un **créneau d'instance** de son agent (#86), puis l'**atelier** de
    son projet (#839, une tâche à la fois dans la racine d'un projet non
    versionné). Elles vivent ici pour la raison exacte de `duree_arbitrage_ms` —
    ce sont des parts d'une durée déjà mesurée, jamais un temps de plus — et le
    moteur le disait déjà sans le compter : « attendre son tour n'est pas
    travailler » (`maestro/engine/executor.py`). Sans elles, une tâche restée
    dix-huit minutes en file se lit exactement comme une tâche lente, mesuré deux
    fois : 21 min 17 s annoncées pour 8 min 05 s de travail (revue du 2026-08-26),
    24 min 17 s pour ~11 min (retex du 2026-09-11).

    **Deux champs et non un**, parce que les deux attentes n'appellent pas le même
    geste : un créneau qui manque se corrige en ajoutant des instances à l'agent,
    un atelier qui bloque est le régime de sérialisation d'un projet non versionné,
    qu'on ne change pas à la légère (#839). Les fondre dans un « temps d'attente »
    unique rendrait le chiffre inactionnable.

    `tokens_non_tarifes` (#1280) est la part de `tokens_total` qu'**aucun coût ne
    couvre** — une part, comme les attentes le sont de la durée, jamais des tokens
    de plus. Une mesure sans coût (`cout_usd` None) a **tous** ses tokens non
    tarifés, et c'est posé à la construction (`__post_init__`) : un producteur n'a
    rien à déclarer pour dire « je n'ai pas de prix ». La fusion **somme** cette
    part comme les autres compteurs, et c'est tout ce qui la rend juste là où la
    seule règle sur le coût ne l'était pas : 1,17 $ tarifés plus 2 M de tokens sans
    prix fusionnent en un coût de 1,17 $ — `_somme_optionnelle` garde le connu —
    **et** 2 M de tokens non tarifés, là où l'on ne voyait plus qu'un total
    « complet ». Une seule mesure peut **retirer** de cette part : celle d'un
    fournisseur dont le résultat tarifie après coup des tours déjà signalés (le
    reste du `ResultMessage` Claude, `maestro.providers.claude._CompteurTours`),
    qui porte alors une part négative, exactement celle de ses tours. Une session
    tuée avant son résultat garde donc la sienne, et une relance qui aboutit ne
    couvre que ses propres tours.
    """

    appels: int = 0
    tokens_entree: int = 0
    tokens_sortie: int = 0
    cout_usd: float | None = None
    duree_ms: int | None = None
    duree_api_ms: int | None = None
    duree_arbitrage_ms: int | None = None
    duree_attente_creneau_ms: int | None = None
    duree_attente_atelier_ms: int | None = None
    tours: int = 0
    outils: tuple[str, ...] = ()
    tokens_non_tarifes: int = 0
    tokens_cache_lus: int = 0
    tokens_cache_ecrits: int = 0
    contexte_max: int | None = None

    def __post_init__(self) -> None:
        # Sans coût, rien de ce que la mesure a consommé n'a de prix (#1280). Posé
        # ici plutôt que demandé à chaque producteur : un fournisseur qui ne
        # tarife pas (#113), un tour signalé avant son résultat (#835) ou une
        # ligne de journal d'avant ce ticket le disent tous sans le savoir. Zéro
        # vaut « non renseigné » parce qu'une mesure sans coût ne peut pas, en
        # vérité, avoir moins de tokens non tarifés que de tokens.
        if self.cout_usd is None and not self.tokens_non_tarifes:
            object.__setattr__(self, "tokens_non_tarifes", self.tokens_total)

    @property
    def tokens_total(self) -> int:
        """Total des tokens consommés (entrée + sortie)."""
        return self.tokens_entree + self.tokens_sortie

    @property
    def tokens_hors_cache_lu(self) -> int:
        """Le total **moins ce que le cache a relu** (#1398) — ce que compte le plafond en tokens.

        Relire un contexte déjà en cache n'est pas le consommer une seconde fois :
        le fournisseur le facture une fraction du prix d'entrée, et c'est ce que
        chaque tour d'une tâche longue fait de tout ce qui précède. Un plafond en
        tokens (#113) qui le comptait coupait une tâche sur ce qu'elle **relit** —
        1 845 438 tokens sur p5 pour 1,87 $ — au lieu de ce qu'elle **traite**. Le
        prompt frais, le cache écrit et la sortie restent comptés.
        """
        return self.tokens_total - self.tokens_cache_lus

    @property
    def duree_attente_ms(self) -> int | None:
        """Ce que la tâche a passé à **attendre** : arbitrage, créneau et atelier (#989).

        `None` — inconnu — tant qu'aucune des trois n'a été mesurée ; sinon la
        somme de celles qui l'ont été. Ces trois attentes-là **ne se recouvrent
        pas** : elles sont prises l'une après l'autre (créneau, puis atelier, puis
        le travail dans lequel un arbitrage peut suspendre), donc les additionner
        est ici légitime — ce qui ne s'additionne pas est l'occupation de deux
        tâches menées de front, et cela se règle un cran plus haut
        (`maestro.telemetry.costs.RunCost`).
        """
        parts = (
            self.duree_arbitrage_ms,
            self.duree_attente_creneau_ms,
            self.duree_attente_atelier_ms,
        )
        mesurees = [part for part in parts if part is not None]
        return sum(mesurees) if mesurees else None

    @property
    def duree_execution_ms(self) -> int | None:
        """La durée horloge **moins ses attentes** : le temps de travail (#584, #989).

        `None` quand la durée horloge l'est — il n'y a alors rien à décomposer.
        Sans attente mesurée, c'est la durée horloge elle-même : une tâche dont
        personne n'a mesuré les files a passé, pour ce qu'on en sait, tout son
        temps à travailler. Bornée à zéro, les mesures venant d'horloges qui ne
        partent pas au même instant.

        #584 avait nommé ce temps et n'en retirait que l'**arbitrage** ; #989 lui
        retire les deux attentes que le moteur rangeait déjà hors du travail sans
        les compter. Le nom ne change pas, parce que le concept ne change pas :
        c'est son contenu qui était incomplet.
        """
        if self.duree_ms is None:
            return None
        attente = self.duree_attente_ms
        if attente is None:
            return self.duree_ms
        return max(0, self.duree_ms - attente)

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
            duree_attente_creneau_ms=_somme_optionnelle(
                self.duree_attente_creneau_ms, autre.duree_attente_creneau_ms
            ),
            duree_attente_atelier_ms=_somme_optionnelle(
                self.duree_attente_atelier_ms, autre.duree_attente_atelier_ms
            ),
            tours=self.tours + autre.tours,
            outils=self.outils + tuple(o for o in autre.outils if o not in self.outils),
            tokens_non_tarifes=self.tokens_non_tarifes + autre.tokens_non_tarifes,
            tokens_cache_lus=self.tokens_cache_lus + autre.tokens_cache_lus,
            tokens_cache_ecrits=self.tokens_cache_ecrits + autre.tokens_cache_ecrits,
            # Un maximum, jamais une somme (#1398) : deux tours de 60 k n'ont pas
            # atteint un contexte de 120 k, et c'est exactement l'erreur que
            # l'additivité de `tokens_entree` faisait lire.
            contexte_max=_max_optionnel(self.contexte_max, autre.contexte_max),
        )

    def avec_duree(
        self,
        duree_ms: int,
        *,
        arbitrage_ms: int | None = None,
        attente_creneau_ms: int | None = None,
        attente_atelier_ms: int | None = None,
    ) -> StepUsage:
        """Copie de la mesure avec la durée horloge posée par l'appelant.

        `arbitrage_ms` (#584) pose du même geste la part de cette durée passée
        suspendue à une décision humaine ; `attente_creneau_ms` et
        `attente_atelier_ms` (#989) les deux parts passées à attendre son tour.
        Absents, les champs restent `None` : un appelant qui ne mesure pas une
        attente ne déclare pas qu'il n'y en a pas eu — la plupart des appelants de
        ce verbe (files, activités durables, boucle de planification) n'ont aucune
        attente à mesurer.
        """
        return replace(
            self,
            duree_ms=duree_ms,
            duree_arbitrage_ms=arbitrage_ms,
            duree_attente_creneau_ms=attente_creneau_ms,
            duree_attente_atelier_ms=attente_atelier_ms,
        )

    def resume_court(self) -> str:
        """Rend la mesure en une ligne lisible (pour les synthèses Markdown)."""
        duree = "n/d" if self.duree_ms is None else f"{self.duree_ms / 1000:.1f} s"
        # Nommées **seulement** quand il y en a eu : annoncer « dont 0,0 s
        # d'arbitrage » sur chacune des tâches d'un run apprendrait à ne plus
        # lire la mention, et c'est elle qui doit sauter aux yeux le jour où
        # une tâche a passé quatre minutes à attendre quelqu'un — ou dix-huit à
        # attendre son tour (#989).
        attentes = [
            f"{part / 1000:.1f} s {mot}"
            for part, mot in (
                (self.duree_arbitrage_ms, "d'arbitrage"),
                (self.duree_attente_creneau_ms, "de file d'agent"),
                (self.duree_attente_atelier_ms, "d'atelier de projet"),
            )
            if part
        ]
        if attentes:
            duree += " (dont " + ", ".join(attentes) + ")"
        if not self.appels:
            return f"aucun usage fournisseur rapporté · durée {duree}"
        cout = "n/d" if self.cout_usd is None else f"{self.cout_usd:.4f} $"
        if self.cout_usd is not None and self.tokens_non_tarifes > 0:
            # Un montant qui ne couvre pas tout ce qui a été consommé (#1280) ne
            # s'écrit pas comme un solde : c'est un plancher, et il le dit.
            cout = f"≥ {cout} ({self.tokens_non_tarifes} tokens sans coût)"
        # Le contexte atteint et la part relue en cache (#1398), nommés quand on
        # les connaît : sans eux, le total se lit comme une consommation, ce qu'il
        # n'est pas sur une tâche longue servie par le cache.
        contexte = (
            "" if self.contexte_max is None else f" · contexte max {self.contexte_max}"
        )
        relus = (
            f", dont {self.tokens_cache_lus} relus en cache" if self.tokens_cache_lus else ""
        )
        return (
            f"{self.appels} appel(s) modèle · {self.tokens_total} tokens "
            f"({self.tokens_entree} entrée{relus} / {self.tokens_sortie} sortie)"
            f"{contexte} · coût {cout} · durée {duree}"
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
            "duree_attente_creneau_ms": self.duree_attente_creneau_ms,
            "duree_attente_atelier_ms": self.duree_attente_atelier_ms,
            # Les deux **dérivées** (#989) voyagent comme `tokens_total` : calculées
            # ici et ignorées au retour. La règle « ce qui est du travail, ce qui
            # est de l'attente » ne doit exister qu'à un endroit — la réécrire en
            # TypeScript pour l'écran, c'est se donner deux règles qui divergeront.
            "duree_attente_ms": self.duree_attente_ms,
            "duree_execution_ms": self.duree_execution_ms,
            "tours": self.tours,
            "outils": list(self.outils),
            # La part que le coût ne couvre pas (#1280) : c'est par elle que le
            # grand livre et l'écran savent qu'un montant n'est qu'un plancher.
            "tokens_non_tarifes": self.tokens_non_tarifes,
            # Le contexte atteint et les deux parts du cache (#1398) : ce qu'une
            # tâche a vraiment consommé se lit par eux, pas par `tokens_total`.
            "tokens_cache_lus": self.tokens_cache_lus,
            "tokens_cache_ecrits": self.tokens_cache_ecrits,
            "contexte_max": self.contexte_max,
            # Dérivée comme `tokens_total` : ce que le plafond en tokens compte,
            # calculé ici pour que l'écran ne refasse pas la règle.
            "tokens_hors_cache_lu": self.tokens_hors_cache_lu,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StepUsage:
        """Reconstruit une mesure depuis sa forme `to_dict` (aller-retour JSON, #41).

        `tokens_total`, `tokens_hors_cache_lu`, `duree_attente_ms` et
        `duree_execution_ms` (dérivés) sont ignorés ; les clés absentes retombent
        sur les défauts — la mesure d'un worker qui ne rapporte rien reste valide,
        et une ligne de journal écrite avant #989 se relit sans attentes mesurées
        (inconnu, pas zéro). Une ligne d'avant #1280 se relit avec la part non
        tarifée que son coût dit : tous ses tokens s'il est inconnu, aucun s'il est
        connu. Une ligne d'avant #1398 se relit sans contexte connu (`None`) et sans
        part de cache : rien ne se reconstitue d'une somme de tours.
        """
        return cls(
            appels=data.get("appels", 0),
            tokens_entree=data.get("tokens_entree", 0),
            tokens_sortie=data.get("tokens_sortie", 0),
            cout_usd=data.get("cout_usd"),
            duree_ms=data.get("duree_ms"),
            duree_api_ms=data.get("duree_api_ms"),
            duree_arbitrage_ms=data.get("duree_arbitrage_ms"),
            duree_attente_creneau_ms=data.get("duree_attente_creneau_ms"),
            duree_attente_atelier_ms=data.get("duree_attente_atelier_ms"),
            tours=data.get("tours", 0),
            outils=tuple(data.get("outils", ())),
            tokens_non_tarifes=data.get("tokens_non_tarifes", 0),
            tokens_cache_lus=data.get("tokens_cache_lus", 0),
            tokens_cache_ecrits=data.get("tokens_cache_ecrits", 0),
            contexte_max=data.get("contexte_max"),
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


#: Qui consigne une dépense du **run** — celle qu'aucune tâche n'a faite (#1398).
#: Reçoit l'étape (une étape de run de `maestro.telemetry.costs`), son nom lisible
#: et la mesure.
ConsigneDepenseDuRun = Callable[[str, str, StepUsage], None]

#: Le consignataire actif du contexte courant (None hors de tout `depenses_du_run()`).
_DEPENSE_DU_RUN: ContextVar[ConsigneDepenseDuRun | None] = ContextVar(
    "maestro_depense_du_run", default=None
)


@contextmanager
def depenses_du_run(consigner: ConsigneDepenseDuRun) -> Iterator[None]:
    """Ouvre un canal pour les dépenses du run faites **pendant** une tâche (#1398).

    Le collecteur suit le contexte, et c'est sa force : une session de l'agent,
    ses sous-tâches asyncio, ses relances signalent toutes à la tâche qui les a
    lancées. C'est aussi ce qui rangeait chez la première tâche d'un run ce
    qu'elle n'avait pas dépensé : la **sonde de démarrage** (#1304) court dans une
    tâche asyncio que la session de l'agent crée, donc sous son collecteur — les
    ≈ 20 k tokens de la sonde tombaient dans `socle-nextjs` sur p5. Ce canal est
    l'autre adresse : ce qui y est signalé (`report_usage_du_run`) part au
    consignataire, qui l'écrit sur une ligne du **run**, et n'entre dans aucun
    collecteur de tâche.

    Ouvert par le moteur autour de chaque tâche (`maestro.engine.executor`), qui
    tient le journal du run. Comme le collecteur, il se propage à travers `await`
    et aux tâches asyncio créées dans le bloc.
    """
    token = _DEPENSE_DU_RUN.set(consigner)
    try:
        yield
    finally:
        _DEPENSE_DU_RUN.reset(token)


def report_usage_du_run(etape: str, nom: str, usage: StepUsage) -> None:
    """Signale une dépense du run (`etape`, déclarée dans `costs`) au consignataire actif.

    **Sans consignataire, la mesure retombe dans le collecteur actif**
    (`report_usage`) : un appelant qui ne tient pas de journal de run — un
    fournisseur appelé hors du moteur — la voit ainsi là où il mesure. Un chiffre
    mal rangé se corrige ; un chiffre perdu ne se retrouve pas. Une mesure vide
    n'est signalée à personne.
    """
    if not (usage.appels or usage.tokens_total or usage.tours or usage.cout_usd):
        return
    consigner = _DEPENSE_DU_RUN.get()
    if consigner is None:
        report_usage(usage)
        return
    consigner(etape, nom, usage)
