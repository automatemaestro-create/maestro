"""L'oracle de S4 : **un modèle** juge si la réponse nomme la cause relevée (#1148, #746).

Trois des quatre scénarios se jugent sur des faits — un dossier vide, une
application qui s'exécute, une équipe écrite sur le disque. Le quatrième porte sur
une **phrase** : « pourquoi le run a-t-il échoué ? ». Et une phrase ne se juge pas
par une liste de mots.

C'est la règle #746, et elle est ici littérale. Chercher « plafond » ou « budget »
dans la réponse dirait vert à *« je ne sais pas si c'est un plafond »* et rouge à
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

SYSTEME = (
    "Tu juges une seule chose : la réponse d'un assistant nomme-t-elle, pour "
    "quelqu'un qui la lit, la cause d'arrêt que le système a relevée ?\n"
    "Tu ne juges ni le style, ni la longueur, ni la politesse, ni l'exactitude "
    "de ce qui est dit en plus. Une reformulation en mots ordinaires compte comme "
    "nommer la cause ; une réponse vague, évasive, ou qui nomme une autre cause, "
    "ne compte pas ; une réponse qui dit ne pas savoir ne compte pas.\n"
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

    def nomme_la_cause(self, *, cause: str, releve: str, reponse: str) -> Avis: ...


class JugeModele:
    """Le juge réel : un appel au fournisseur configuré, un avis en retour.

    Le fournisseur est résolu **paresseusement**, comme dans `RepondeurModele` et
    `RepondeurOrchestration` : construire le juge ne coûte rien et ne lève aucune
    erreur de configuration, ce dont dépend un banc qui ne joue qu'un scénario
    sur quatre (`--scenario S1` n'a aucun jugement à rendre).
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

    def nomme_la_cause(self, *, cause: str, releve: str, reponse: str) -> Avis:
        """La réponse du fil nomme-t-elle la cause que l'API a relevée ?"""
        try:
            provider, modele = self._resolu()
        except Exception as echec:  # configuration, quota, fournisseur inconnu
            return Avis(nomme=False, pourquoi=f"juge indisponible : {echec}", lisible=False)
        try:
            texte = asyncio.run(
                provider.generate(
                    _prompt(cause=cause, releve=releve, reponse=reponse),
                    model=modele,
                    system_prompt=SYSTEME,
                )
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


def _prompt(*, cause: str, releve: str, reponse: str) -> str:
    """Ce que le juge lit : la cause telle que l'API la relève, puis la réponse.

    La cause vient **encadrée comme donnée** et la réponse aussi : l'une et
    l'autre sont du texte produit ailleurs, et ce prompt ne leur laisse aucun
    moyen de passer pour une consigne (ENF-13).
    """
    return (
        "Cause d'arrêt relevée par le système, code interne :\n"
        f"<cause>{cause}</cause>\n"
        "Ce que le système en dit, tel qu'il l'a consigné :\n"
        f"<releve>{releve}</releve>\n"
        "Réponse de l'assistant à la question « pourquoi le run a-t-il échoué ? » :\n"
        f"<reponse>{reponse}</reponse>\n"
        "Cette réponse nomme-t-elle cette cause ?"
    )


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
