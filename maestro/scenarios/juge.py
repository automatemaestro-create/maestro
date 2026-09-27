"""Les oracles qui portent sur une **phrase** : un modèle juge (#1148, #746, #1224, #1162).

Sept des onze scénarios se jugent sur des faits seulement — un dossier vide, une
application qui s'exécute, une équipe écrite sur le disque, une tâche prise par
le rôle recruté, un projet déclaré sur le dossier demandé, une demande de
validation née et un registre intact hors du projet, deux tâches au travail en
même temps dans la trace datée d'un run. Deux portent sur des
phrases : « pourquoi le run a-t-il échoué ? » (S4) et « comment j'essaie ce que
tu viens de livrer ? » (S5). Deux, enfin, sur une **pertinence** : l'outillage et
l'équipe d'un projet qu'aucune liste ne prévoyait lui correspondent-ils (S9, S10) ?
Et ni une phrase ni une pertinence ne se jugent par une liste de mots.

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

#: La forme de réponse, commune aux deux jugements : deux lignes, et rien
#: d'autre. Écrite une fois, parce qu'un second gabarit finirait par diverger du
#: premier — et c'est `avis_depuis` qui les lit tous les deux.
_FORME = (
    f"Réponds par exactement deux lignes :\n"
    f"{MARQUEUR_VERDICT} {VERDICT_OUI} ou {VERDICT_NON}\n"
    f"{MARQUEUR_POURQUOI} une phrase qui dit ce qui te fait trancher."
)

SYSTEME = (
    "Tu juges une seule chose : la réponse d'un assistant nomme-t-elle, pour "
    "quelqu'un qui la lit, la cause d'arrêt que le système a relevée ?\n"
    "Tu ne juges ni le style, ni la longueur, ni la politesse, ni l'exactitude "
    "de ce qui est dit en plus. Une reformulation en mots ordinaires compte comme "
    "nommer la cause ; une réponse vague, évasive, ou qui nomme une autre cause, "
    "ne compte pas ; une réponse qui dit ne pas savoir ne compte pas.\n" + _FORME
)

#: Le jugement de S5 (#1224). La question n'est **pas** « le texte est-il
#: joli » ni « la commande est-elle exacte » — un banc ne saurait pas juger la
#: seconde sans exécuter, ce que l'oracle de S2 fait déjà pour son compte. Elle
#: est celle du retex du 2026-09-22, mot pour mot : *on ne me dit pas comment
#: tester*. Donc : quelqu'un qui lit ceci sait-il quoi taper, et où ?
SYSTEME_ESSAI = (
    "Tu juges une seule chose : après avoir lu ce que l'assistant a écrit, la "
    "personne sait-elle comment essayer ce que le travail vient de produire ?\n"
    "Pour que ce soit oui, il faut une façon concrète de s'y prendre — une "
    "commande à taper, un fichier à ouvrir, un geste précis — rattachée à ce qui "
    "a réellement été produit. Tu ne juges ni le style, ni la longueur, ni la "
    "politesse ; tu ne vérifies pas non plus que la commande fonctionne, "
    "seulement qu'elle est donnée et qu'elle porte sur ce livrable.\n"
    "Ne compte pas : renvoyer vers un écran ou un dossier sans dire quoi y faire, "
    "dire qu'on ne sait pas, décrire le travail sans dire comment l'essayer, "
    "proposer une commande générique sans rapport avec les fichiers listés.\n" + _FORME
)

#: Le jugement de S9 et S10 (#1162). La question est celle de #1155, mot pour mot :
#: *l'outillage et l'équipe correspondent-ils au projet ?* Elle ne porte pas sur les
#: commandes qui marchent — l'exécution le constate à part, et mieux qu'un modèle —,
#: mais sur ce qu'aucune liste ne peut trancher : un outillage et une équipe **pour
#: ce projet-là**, et non ceux d'un projet qu'on aurait fait entrer dans une case.
SYSTEME_PROJET = (
    "Tu juges une seule chose : l'outillage écrit dans ce projet et l'équipe recrutée "
    "pour lui correspondent-ils à CE projet — à ce qu'il est et à ce qu'on veut en "
    "faire ?\n"
    "Pour que ce soit oui, l'outillage doit parler de la pile et des gestes réels du "
    "projet (comment on le construit, on le vérifie, on s'en sert), et l'équipe doit "
    "couvrir le besoin qu'il exprime, chaque rôle avec une raison qui tient à ce "
    "projet-là. Tu ne juges ni le style, ni la longueur, ni l'exhaustivité ; tu ne "
    "vérifies pas que les commandes fonctionnent, c'est constaté à part en les "
    "exécutant.\n"
    "Ne compte pas : un outillage générique qui pourrait être celui de n'importe quel "
    "projet, des commandes ou des conventions d'une autre pile que celle du projet, une "
    "équipe taillée pour un autre besoin, des rôles sans lien avec ce que le projet "
    "demande.\n" + _FORME
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

    def dit_comment_essayer(self, *, livrable: str, recit: str, reponse: str) -> Avis: ...

    def convient_au_projet(self, *, projet: str, outillage: str, equipe: str) -> Avis: ...


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
        return self._juger(
            SYSTEME, _prompt(cause=cause, releve=releve, reponse=reponse)
        )

    def dit_comment_essayer(self, *, livrable: str, recit: str, reponse: str) -> Avis:
        """Après lecture du fil, sait-on comment essayer ce qui a été produit ? (#1224)"""
        return self._juger(
            SYSTEME_ESSAI, _prompt_essai(livrable=livrable, recit=recit, reponse=reponse)
        )

    def convient_au_projet(self, *, projet: str, outillage: str, equipe: str) -> Avis:
        """L'outillage écrit et l'équipe recrutée correspondent-ils à ce projet ? (#1162)"""
        return self._juger(
            SYSTEME_PROJET, _prompt_projet(projet=projet, outillage=outillage, equipe=equipe)
        )

    def _juger(self, systeme: str, prompt: str) -> Avis:
        """L'appel, et les deux façons de n'avoir **aucun** avis.

        Écrit une fois pour les deux jugements : la distinction qui compte — un
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


def _prompt_essai(*, livrable: str, recit: str, reponse: str) -> str:
    """Ce que le juge de S5 lit : ce qui est sur le disque, puis ce que le fil en dit.

    Le livrable vient **en premier** parce que c'est le seul fait vérifiable de
    la question : « la commande porte-t-elle sur ce qui a été produit ? » ne se
    juge pas sans savoir ce qui l'a été. Les trois blocs sont encadrés comme
    données (ENF-13) — le contenu du disque comme les mots du modèle jugé : ni
    l'un ni l'autre ne doit pouvoir passer pour une consigne.
    """
    return (
        "Fichiers réellement produits par le travail, tels que le disque les porte :\n"
        f"<livrable>{livrable}</livrable>\n"
        "Ce que l'assistant a écrit de lui-même quand le travail s'est terminé :\n"
        f"<recit>{recit}</recit>\n"
        "Sa réponse à la question « comment j'essaie ce que tu viens de livrer ? » :\n"
        f"<reponse>{reponse}</reponse>\n"
        "Après avoir lu cela, sait-on comment essayer ce qui a été produit ?"
    )


def _prompt_projet(*, projet: str, outillage: str, equipe: str) -> str:
    """Ce que le juge de S9 et S10 lit : le projet, puis ce que Maestro en a fait.

    Le projet vient **en premier**, pour la même raison que le livrable de S5 : « pour
    ce projet-là » ne se juge pas sans savoir lequel. Les trois blocs sont encadrés
    comme données (ENF-13) — ce que la personne a dit, ce que Maestro a écrit dans son
    dossier, l'équipe qu'il a recrutée : aucun ne doit pouvoir passer pour une consigne.
    """
    return (
        "Le projet — ce que la personne en a dit, et ce que son dossier porte :\n"
        f"<projet>{projet}</projet>\n"
        "L'outillage que Maestro a écrit dans ce projet pour les agents qui y travaillent "
        "(fichiers d'instructions, commandes par usage et leur vérification) :\n"
        f"<outillage>{outillage}</outillage>\n"
        "L'équipe recrutée pour ce projet, chaque rôle avec sa raison :\n"
        f"<equipe>{equipe}</equipe>\n"
        "L'outillage et l'équipe correspondent-ils à ce projet ?"
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
