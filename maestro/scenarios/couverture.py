"""La couverture des critères d'acceptation d'un livrable, jugée par un modèle sur pièces (#1513).

Le banc comparatif rejoue chaque livrable selon sa grille (`maestro.scenarios.grille`) :
installer, vérifier, construire, servir. La grille dit si le livrable **tient
debout** ; elle ne dit pas s'il fait **ce que la personne a demandé**. C'est la
couverture des critères d'acceptation de l'objectif, et c'est sur elle — avec les
gestes de la personne — que le go/no-go attend Maestro sur un projet long (docs/48).

## Un modèle juge, sur pièces

Un critère d'acceptation dit une capacité en mots de la personne (« un adhérent
s'inscrit lui-même à une sortie, et peut se désister ») : aucune liste de mots ne
le reconnaît dans un dépôt (#746). Un modèle le juge, comme les lignes jugées de la
grille, mais il a besoin de **lire** : une capacité vit dans le code, ses tests, ses
écrans, rarement dans les fichiers de la racine. Le juge est donc un **agent en
lecture seule** (`OUTILS_DU_JUGE`), lancé par l'adaptateur du fournisseur dans le
**clone** que la grille a fait — ce que le projet a commité, et rien de ce que le
run a laissé traîner. Il lit ce qu'il lui faut, puis rend une ligne par critère.

La règle de la grille vaut ici : **ce qu'on ne comprend pas ne vaut jamais un
accord**. Un critère sans ligne lisible est **non jugé** — une abstention, qui ne
compte ni pour ni contre —, et un juge injoignable laisse tous les critères non
jugés : une panne de quota n'est pas un défaut du livrable.

## Le contrat de réponse

Une ligne par critère, `C<n>: tenu — <pièce>` ou `C<n>: manqué — <pièce>`. Lire
« tenu » dans un champ que le modèle a été chargé de remplir est une **forme**, comme
le `VERDICT:` du juge (`maestro.scenarios.juge`) — jamais une recherche dans le texte
du livrable. Le juge peut réfléchir à voix haute avant : seule la **dernière** ligne
de chaque critère compte.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from maestro.config import Settings, load_settings
from maestro.telemetry import collect_usage

if TYPE_CHECKING:
    from maestro.providers.base import ModelProvider

#: Ce que le juge peut faire dans le clone : lire, et rien d'autre.
OUTILS_DU_JUGE: tuple[str, ...] = ("Read", "Glob", "Grep")

#: Les trois états d'un critère. `NON_JUGE` ne compte pas : le juge s'est abstenu.
TENU = "tenu"
MANQUE = "manqué"
NON_JUGE = "non jugé"

#: Une ligne de verdict. `manque` sans accent est admis : c'est la même valeur, et un
#: juge qui l'écrit ainsi n'a pas répondu hors contrat.
_LIGNE = re.compile(
    r"^[ \t>*\-]*C(?P<rang>\d+)[ \t]*:[ \t]*(?P<etat>tenu|manqu[ée])\b[ \t]*[—–\-:]?[ \t]*"
    r"(?P<piece>.*?)[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)

SYSTEME_COUVERTURE = (
    "Tu vérifies un logiciel livré contre les critères d'acceptation de la personne qui "
    "l'a commandé. Tu travailles dans une copie de son dépôt, en lecture seule : lis son "
    "code, ses tests, ses écrans et sa documentation, autant qu'il le faut pour trancher.\n"
    "Un critère est tenu si le livrable le réalise réellement : du code qui le fait, branché, "
    "que la personne peut atteindre en se servant de l'application. Il est manqué s'il est "
    "absent, partiel, factice (des données écrites en dur, un bouton sans effet, une page "
    "qui ne fait rien) ou seulement annoncé dans un texte : une intention écrite dans un "
    "README ou un commentaire ne tient aucun critère.\n"
    "Ignore les dossiers de dépendances et de construction (node_modules, .next, dist, "
    "build, .venv, __pycache__).\n"
    "Termine ta réponse par exactement une ligne par critère, dans l'ordre, et rien "
    "d'autre après :\n"
    "C1: tenu — la pièce qui le montre (un fichier, et ce qu'il fait)\n"
    "C2: manqué — ce qui manque, et où tu l'as cherché\n"
    "Chaque ligne commence par le numéro du critère, puis « tenu » ou « manqué »."
)


@dataclass(frozen=True)
class Verdict:
    """Un critère jugé : son rang (`C1`…), son texte, son état et la pièce qui le dit."""

    rang: int
    critere: str
    etat: str
    piece: str = ""

    @property
    def cle(self) -> str:
        """`C1`, `C2`… — le nom du critère dans le rapport."""
        return f"C{self.rang}"

    @property
    def tenu(self) -> bool:
        return self.etat == TENU

    @property
    def juge(self) -> bool:
        """A-t-il un verdict ? Une abstention n'en est pas un."""
        return self.etat != NON_JUGE

    def to_dict(self) -> dict[str, Any]:
        return {
            "cle": self.cle,
            "critere": self.critere,
            "etat": self.etat,
            "piece": self.piece,
        }


@dataclass(frozen=True)
class Couverture:
    """La couverture d'un livrable : un verdict par critère, et ce que le jugement a coûté.

    `cout_usd` est le coût du **juge** — une dépense du banc, jamais celle du côté
    jugé. `motif` dit pourquoi rien n'a été jugé, quand c'est le cas.
    """

    verdicts: tuple[Verdict, ...]
    cout_usd: float | None = None
    motif: str = ""

    @property
    def tenus(self) -> int:
        return sum(1 for verdict in self.verdicts if verdict.tenu)

    @property
    def juges(self) -> int:
        """Combien de critères ont un verdict — le dénominateur de la couverture."""
        return sum(1 for verdict in self.verdicts if verdict.juge)

    @property
    def complete(self) -> bool:
        """Tous les critères sont-ils jugés, et tenus ?"""
        return bool(self.verdicts) and all(verdict.tenu for verdict in self.verdicts)

    def verdict(self, rang: int) -> Verdict | None:
        return next((v for v in self.verdicts if v.rang == rang), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenus": self.tenus,
            "juges": self.juges,
            "criteres": len(self.verdicts),
            "cout_usd": self.cout_usd,
            "motif": self.motif,
            "verdicts": [verdict.to_dict() for verdict in self.verdicts],
        }


def non_jugee(criteres: Sequence[str], motif: str, *, cout_usd: float | None = None) -> Couverture:
    """Une couverture où aucun critère n'a de verdict, pour la même raison."""
    return Couverture(
        verdicts=tuple(
            Verdict(rang, critere, NON_JUGE, motif) for rang, critere in _numerotes(criteres)
        ),
        cout_usd=cout_usd,
        motif=motif,
    )


def couverture_depuis(
    brut: str, criteres: Sequence[str], *, cout_usd: float | None = None
) -> Couverture:
    """Les verdicts lus dans la réponse du juge — un critère sans ligne lisible est non jugé."""
    lus: dict[int, tuple[str, str]] = {}
    for trouve in _LIGNE.finditer(brut or ""):
        etat = TENU if trouve.group("etat").lower() == TENU else MANQUE
        lus[int(trouve.group("rang"))] = (etat, trouve.group("piece").strip())
    verdicts = []
    for rang, critere in _numerotes(criteres):
        etat, piece = lus.get(rang, (NON_JUGE, "le juge n'a rendu aucune ligne lisible"))
        verdicts.append(Verdict(rang, critere, etat, piece))
    return Couverture(verdicts=tuple(verdicts), cout_usd=cout_usd)


def prompt_couverture(criteres: Sequence[str]) -> str:
    """Ce que le juge reçoit : les critères numérotés, encadrés comme données (ENF-13)."""
    liste = "\n".join(f"C{rang}. {critere}" for rang, critere in _numerotes(criteres))
    return (
        "Les critères d'acceptation de la personne, numérotés :\n"
        f"<criteres>\n{liste}\n</criteres>\n"
        "Le dépôt livré est le répertoire courant. Juge chaque critère sur pièces."
    )


def _numerotes(criteres: Sequence[str]) -> list[tuple[int, str]]:
    return list(enumerate(criteres, start=1))


class JugeCouverture(Protocol):
    """Ce que le banc demande pour juger une couverture — et rien de plus."""

    def couvrir(self, depot: Path, criteres: Sequence[str]) -> Couverture: ...


class JugeCouvertureModele:
    """Le juge réel : un agent en lecture seule, par l'adaptateur du fournisseur configuré.

    Résolu **paresseusement**, comme `JugeModele` : construire le juge ne coûte rien,
    et un banc sans critères à juger n'appelle jamais le fournisseur.
    """

    def __init__(
        self,
        provider: ModelProvider | None = None,
        *,
        modele: str | None = None,
        settings: Settings | None = None,
        fabrique: Callable[[Settings], ModelProvider] | None = None,
    ) -> None:
        self._provider = provider
        self._modele = modele
        self._settings = settings
        self._fabrique = fabrique

    def couvrir(self, depot: Path, criteres: Sequence[str]) -> Couverture:
        """Juge chaque critère dans `depot` — non jugés, tous, si le juge n'a pas pu lire."""
        if not criteres:
            return Couverture(verdicts=())
        try:
            provider, modele = self._resolu()
        except Exception as echec:  # configuration, fournisseur inconnu
            return non_jugee(criteres, f"juge indisponible : {echec}")
        with collect_usage() as depense:
            try:
                brut = asyncio.run(
                    provider.run_agent(
                        prompt_couverture(criteres),
                        model=modele,
                        system_prompt=SYSTEME_COUVERTURE,
                        workspace=depot,
                        tools=OUTILS_DU_JUGE,
                        plafond_tours=None,
                    )
                )
            except Exception as echec:  # quota, fournisseur injoignable : aucun avis
                return non_jugee(
                    criteres, f"juge injoignable : {echec}", cout_usd=depense.total.cout_usd
                )
        return couverture_depuis(brut, criteres, cout_usd=depense.total.cout_usd)

    def _resolu(self) -> tuple[ModelProvider, str]:
        from maestro.providers.factory import default_model, provider_from_settings

        settings = self._settings or load_settings()
        if self._provider is None:
            fabrique = self._fabrique or provider_from_settings
            self._provider = fabrique(settings)
        if self._modele is None:
            self._modele = self._provider.modele_configure or default_model(settings)
        return self._provider, self._modele


def comptes_par_critere(
    couvertures: Sequence[Couverture], criteres: Sequence[str]
) -> dict[str, Mapping[str, int]]:
    """Critère par critère : combien de passages le tiennent, sur combien qui l'ont jugé."""
    comptes: dict[str, Mapping[str, int]] = {}
    for rang, _critere in _numerotes(criteres):
        verdicts = [v for c in couvertures if (v := c.verdict(rang)) is not None and v.juge]
        comptes[f"C{rang}"] = {"tenus": sum(1 for v in verdicts if v.tenu), "juges": len(verdicts)}
    return comptes
