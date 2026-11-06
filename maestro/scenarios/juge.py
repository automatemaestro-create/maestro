"""Ce qui se juge sur une **phrase** ou une **pertinence** : un modèle juge (#746, #1512).

Le banc comparatif juge ainsi la fin d'une session (attend-elle quelqu'un ? a-t-elle
fini ?) et trois lignes de sa grille — CI, convention de commit, outillage suivi
(`maestro.scenarios.grille`) : l'appelant écrit sa question (`JugeModele.juger`), ce
module en tient la forme et l'asymétrie. Aucune de ces questions ne se tranche par une
liste de mots.

C'est la règle #746, et elle est ici littérale. Chercher « plafond » ou « budget »
dans une réponse dirait vert à *« je ne sais pas si c'est un plafond »* et rouge à
*« il s'est arrêté parce qu'il avait dépensé tout ce qu'on lui avait alloué »* —
alors que la seconde nomme la cause et la première ne la nomme pas. Le dépôt a
déjà payé cette leçon : un lexique reconnaît des mots, jamais un sens, et il se
trompe **dans les deux sens** (#585, #749).

D'où un juge qui est un appel modèle, avec l'asymétrie de tous les jugements du
dépôt : **ce qu'on ne comprend pas ne vaut jamais un accord** (`orchestration`).
Un juge injoignable ou hors contrat ne rend pas « non » — il rend « je n'ai pas
pu juger », que le banc range en empêchement. Confondre les deux ferait passer une
panne de quota pour un défaut du produit.

## Le contrat de réponse : deux en-têtes, pas du JSON

`VERDICT:` puis `POURQUOI:`. C'est le parti pris de `generation_agent` (#487) et
son argument tient tel quel ici : le dépôt porte déjà trois parseurs d'objet JSON
produit par un modèle, et en ajouter un quatrième pour **deux champs plats**
serait payer un parseur contre un format plus fragile.

⚠ Lire « oui » dans un champ que le modèle a été chargé de remplir n'est pas un
lexique : c'est une **forme** — la même que lire `verdict` dans un objet JSON. Ce
qui serait un lexique est de chercher ces mots dans la réponse **du produit**, et
c'est précisément ce que ce module existe pour ne pas faire.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from maestro.config import Settings, load_settings
from maestro.providers.base import ModelProvider

#: L'en-tête qui porte le verdict, et celui qui porte sa raison.
MARQUEUR_VERDICT = "VERDICT:"
MARQUEUR_POURQUOI = "POURQUOI:"

#: Les deux valeurs admises du verdict. Une troisième valeur, ou aucune, vaut
#: « illisible » : le juge s'abstient au lieu de trancher au hasard.
VERDICT_OUI = "oui"
VERDICT_NON = "non"

_LIGNE_VERDICT = re.compile(rf"^\s*{MARQUEUR_VERDICT}\s*(?P<valeur>\S+)", re.MULTILINE)
_LIGNE_POURQUOI = re.compile(rf"^\s*{MARQUEUR_POURQUOI}\s*(?P<texte>.*)$", re.MULTILINE)

#: La forme de réponse, commune à tous les jugements : deux lignes, et rien
#: d'autre. Écrite une fois, parce qu'un second gabarit finirait par diverger du
#: premier — et c'est `avis_depuis` qui les lit tous.
FORME = (
    f"Réponds par exactement deux lignes :\n"
    f"{MARQUEUR_VERDICT} {VERDICT_OUI} ou {VERDICT_NON}\n"
    f"{MARQUEUR_POURQUOI} une phrase qui dit ce qui te fait trancher."
)

@dataclass(frozen=True)
class Avis:
    """Ce que le juge rend : son verdict, sa raison, et s'il a pu juger.

    `lisible` à `False` couvre les trois façons de ne pas avoir d'avis —
    fournisseur injoignable, fournisseur muet, réponse hors contrat. `nomme` est
    alors `False` sans que cela veuille dire « non » : c'est le banc qui traduit
    l'abstention en empêchement.
    """

    nomme: bool
    pourquoi: str
    lisible: bool = True


class Juge(Protocol):
    """Ce que le banc demande à un juge — et rien de plus."""

    def juger(self, systeme: str, prompt: str) -> Avis: ...


class JugeModele:
    """Le juge réel : un appel au fournisseur configuré, un avis en retour.

    Le fournisseur est résolu **paresseusement**, comme dans `RepondeurModele` et
    `RepondeurOrchestration` : construire le juge ne coûte rien et ne lève aucune
    erreur de configuration, ce dont dépend un passage qui n'a aucun jugement à
    rendre.
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

    def juger(self, systeme: str, prompt: str) -> Avis:
        """Un jugement dont l'appelant écrit la question, dans la forme du module (#1512).

        Le banc comparatif juge ainsi trois lignes de sa grille — CI, convention de
        commit, outillage suivi (`maestro.scenarios.grille`) —, avec la même asymétrie :
        un juge injoignable s'abstient.
        """
        return self._juger(systeme, prompt)

    def rediger(self, systeme: str, prompt: str) -> str:
        """Une réponse libre du modèle de jugement — lève si le fournisseur ne répond pas (#1512).

        Ce que le banc comparatif demande pour lire, dans un livrable, les commandes
        qu'il déclare (`maestro.scenarios.grille`). L'appelant lit la réponse et en
        répond : ici, rien n'est interprété.
        """
        provider, modele = self._resolu()
        return asyncio.run(provider.generate(prompt, model=modele, system_prompt=systeme))

    def _juger(self, systeme: str, prompt: str) -> Avis:
        """L'appel, et les deux façons de n'avoir **aucun** avis.

        Écrit une fois pour tous les jugements : la distinction qui compte — un
        juge injoignable s'abstient au lieu de rendre « non » — ne doit pas
        dépendre de la question posée. C'est l'asymétrie du module, et deux
        copies finiraient par n'en garder qu'une.
        """
        try:
            provider, modele = self._resolu()
        except Exception as echec:  # configuration, quota, fournisseur inconnu
            return Avis(nomme=False, pourquoi=f"juge indisponible : {echec}", lisible=False)
        try:
            texte = asyncio.run(
                provider.generate(prompt, model=modele, system_prompt=systeme)
            )
        except Exception as echec:
            return Avis(nomme=False, pourquoi=f"juge injoignable : {echec}", lisible=False)
        return avis_depuis(texte)

    def _resolu(self) -> tuple[ModelProvider, str]:
        """Le fournisseur et le modèle du jugement — ceux de la configuration du poste."""
        from maestro.providers.factory import default_model, provider_from_settings

        settings = self._settings or load_settings()
        if self._provider is None:
            fabrique = self._fabrique or provider_from_settings
            self._provider = fabrique(settings)
        if self._modele is None:
            self._modele = self._provider.modele_configure or default_model(settings)
        return self._provider, self._modele


def avis_depuis(texte: str) -> Avis:
    """L'avis lu dans la réponse du juge — hors contrat vaut **abstention**.

    Ni « oui » ni « non » par défaut : un juge qui n'a pas répondu dans la forme
    demandée n'a pas jugé, et prêter un verdict à son silence est exactement ce que
    l'asymétrie du module interdit.
    """
    trouve = _LIGNE_VERDICT.search(texte or "")
    if trouve is None:
        return Avis(
            nomme=False,
            pourquoi=f"verdict illisible : {(texte or '').strip()[:200]}",
            lisible=False,
        )
    valeur = trouve.group("valeur").strip().strip(".,;:").lower()
    if valeur not in (VERDICT_OUI, VERDICT_NON):
        return Avis(
            nomme=False,
            pourquoi=f"verdict hors contrat : {valeur!r}",
            lisible=False,
        )
    raison = _LIGNE_POURQUOI.search(texte or "")
    return Avis(
        nomme=valeur == VERDICT_OUI,
        pourquoi=(raison.group("texte").strip() if raison is not None else "").strip()
        or "sans raison donnée",
    )
