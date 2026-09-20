"""Le questionnaire d'outillage conduit dans le fil, et répondu d'un geste (#1031).

Lot 3/7 de #1020. Les questions, leurs recommandations et la recommandation
structurée qu'elles produisent vivent dans `maestro.projets.outillage` — un module
sans dépendance à la Control Tower, que l'analyse d'un projet existant (#1030) et la
génération (#1033) partagent. **Ici** vit seulement ce qui les fait tenir dans une
conversation.

## Pourquoi dans le fil, et pas dans un formulaire

« Chaque question propose une recommandation et se répond d'un geste, par la mécanique
du cadrage (#1014), **sans formulaire à part** » — c'est le critère du ticket, et il
n'est pas une préférence d'écran. Le chat est la seule porte d'entrée du produit depuis
#666 ; un questionnaire posé ailleurs demanderait de quitter la conversation où l'on
vient de dire ce qu'on voulait faire, pour y revenir ensuite.

La mécanique est donc **exactement** celle que #1014 a tranchée pour le cadrage, et
c'est la seule chose que ce module emprunte :

- la demande voyage **sur le message** (`MessageChat.question`), pas dans le texte
  d'une réponse — sinon aucune surface ne pourrait lui offrir un bouton, ce qui était
  le défaut G10 du retex du 2026-09-11 ;
- « ce qui attend » s'énonce **une seule fois** (`chat.question_en_attente`), pour les
  deux côtés — le geste qui répond et l'écran qui montre ;
- le geste **ne repasse pas par le juge** : un clic sur une option n'est pas un texte à
  reconnaître. Ici la raison est encore plus nette qu'en #1014 — la suite du
  questionnaire est une **fonction pure** (`deductions`, `question_suivante`), donc un
  appel modèle rendrait, au mieux, ce qu'on sait déjà.

## Le fil est la seule mémoire, et c'est ce qui rend ce module si petit

`ConducteurOutillage` **ne retient rien**. L'état du questionnaire est la suite des
messages : `chat.choix_du_fil` la relit, `deductions` la complète, `question_suivante`
en déduit la prochaine question. Trois conséquences qui tombent d'elles-mêmes, et
qu'aucune garde n'a à tenir :

- rouvrir la Control Tower, changer de poste, recharger la page ne perd rien ;
- rouvrir un questionnaire en cours le **reprend** où il en est plutôt que d'en
  recommencer un second ;
- revenir sur une réponse la corrige — `_repondues` garde la **dernière**.

C'est la propriété que `orchestration` tient déjà pour le cadrage, appliquée à un objet
qui a plus d'un tour.

## Ce que ce module ne fait pas

Il **n'écrit rien dans le projet** : la génération est le lot 5 (#1033), et ce qui la
précède est une recommandation, pas un fichier. Il ne décide pas non plus du **format**
de l'outillage — docs/38 l'a arrêté, et `maestro.projets.outillage` en dérive. Enfin il
ne pose la question **nulle part ailleurs** que dans le fil : l'étape d'outillage du
parcours de création (#1034) montera cette même carte, elle n'en fera pas une seconde.
"""

from __future__ import annotations

from collections.abc import Sequence

from maestro.controltower.chat import MessageChat, ReponseChat, choix_du_fil
from maestro.projets.outillage import (
    Choix,
    QuestionOutillage,
    deductions,
    question_suivante,
    recommandation_depuis_choix,
)


def _phrase_des_deductions(deduits: Sequence[Choix]) -> str:
    """Ce que le fil dit des réponses que Maestro a conclues à notre place.

    Elles se **lisent**, et ce n'est pas de la politesse : une question qu'on ne vous
    pose pas et dont vous découvrez la réponse dans un fichier généré est une décision
    prise sans vous. Chaque déduction sort donc avec sa cause, dans le message qui
    suit le geste — l'endroit exact où l'on regarde déjà.

    Vide quand il n'y en a pas : une phrase « aucune déduction » à chaque tour
    apprendrait à ne plus lire ce paragraphe.
    """
    if not deduits:
        return ""
    lignes = [f"— {c.parce_que}" for c in deduits if c.parce_que]
    if not lignes:
        return ""
    tete = (
        "Du coup, une question ne se pose pas :"
        if len(lignes) == 1
        else f"Du coup, {len(lignes)} questions ne se posent pas :"
    )
    return tete + "\n" + "\n".join(lignes)


def _phrase_de_la_question(question: QuestionOutillage) -> str:
    """Le texte du message qui porte une question — ce qu'on lit si rien ne s'affiche.

    La carte rend l'intitulé, ses options et sa recommandation ; ce texte-ci est ce
    qui reste quand on relit le fil ailleurs (un export, une lettre inter-agents, un
    client qui ne connaît pas le champ `question`). Il **redit** donc l'intitulé, et
    c'est voulu : un message vide dans un fil est un trou, et `ServiceChat` refuse de
    toute façon une réponse vide.
    """
    return (
        f"{question.intitule}\n"
        f"Je propose « {question.libelle_de(question.recommande)} » — "
        f"{question.pourquoi}"
    )


def _phrase_de_conclusion(projet_id: str, acquis: Sequence[Choix]) -> str:
    """Ce que le fil dit quand il n'y a plus de question : l'outillage recommandé.

    Le questionnaire ne s'arrête pas sur un silence. Il rend **ce qu'il a produit** —
    le résumé du manifeste à venir et le compte des fichiers —, parce que c'est la
    seule chose qui donne rétrospectivement un sens aux questions qu'on vient de
    répondre. Le détail, lui, se sert par l'API (`POST /api/outillage/recommandation`)
    et s'affiche là où on le valide (#1034) : le redire ici en entier ferait du fil un
    second écran de recommandation.
    """
    reco = recommandation_depuis_choix(projet_id, acquis)
    skills = sum(1 for p in reco.pieces if p.role == "skill")
    return (
        f"C'est tout ce qu'il me fallait — {reco.resume}.\n"
        f"L'outillage recommandé : {len(reco.pieces)} fichier(s), dont {skills} skill(s), "
        "chacun avec la raison qui le justifie. Rien n'est écrit dans le projet tant "
        "que vous ne l'avez pas validé."
    )


class ConducteurOutillage:
    """Conduit le questionnaire d'outillage d'un projet neuf dans un fil de chat.

    Sans état : tout ce qu'il sait, il le relit du fil qu'on lui passe. Il est donc
    sûr de le partager entre conversations, et la Control Tower n'en construit qu'un.

    `projet_id` est **celui de la fenêtre**, passé au montage plutôt qu'à chaque
    appel : le questionnaire porte sur le projet qu'on est en train d'outiller, et
    changer de projet en cours de questionnaire n'a pas de sens — c'est un autre
    questionnaire. La valeur ne sert qu'à remplir la recommandation, jamais à décider
    d'une question.
    """

    def __init__(self, projet_id: str = "") -> None:
        self._projet_id = projet_id

    def acquis(self, fil: Sequence[MessageChat]) -> tuple[Choix, ...]:
        """Les réponses que ce fil porte — celles données, **puis** celles qui en découlent.

        L'ordre compte : les déductions se calculent sur les réponses données, et se
        rangent après elles. C'est ce qui rend la relecture fidèle — on voit ce qui a
        été choisi, puis ce que ça a entraîné.
        """
        donnes = list(choix_du_fil(fil))
        return tuple([*donnes, *deductions(donnes)])

    def _tour(self, fil: Sequence[MessageChat], prelude: str = "") -> ReponseChat:
        """Le tour suivant : la question à poser, ou la conclusion. Jamais rien.

        `prelude` est ce qu'on a à dire **avant** la question — les déductions que le
        geste précédent vient d'entraîner. Il est préfixé plutôt que posé dans un
        second message : deux messages d'agent d'affilée pour un seul tour
        décaleraient la question du geste qui l'a appelée, et `question_en_attente`
        ne lit que le **dernier** message.
        """
        donnes = list(choix_du_fil(fil))
        acquis = [*donnes, *deductions(donnes)]
        question = question_suivante(acquis)
        if question is None:
            corps = _phrase_de_conclusion(self._projet_id, acquis)
            return ReponseChat(contenu=_joint(prelude, corps))
        return ReponseChat(
            contenu=_joint(prelude, _phrase_de_la_question(question)),
            question=question,
        )

    async def ouvrir(self, fil: Sequence[MessageChat]) -> ReponseChat:
        """Ouvre — ou **reprend** — le questionnaire sur ce fil.

        Aucune différence entre les deux, et c'est la propriété qu'on veut : ouvrir
        un questionnaire déjà commencé repose la question là où il en est. Elle
        découle du fil comme seule mémoire, elle n'est pas gardée.
        """
        return self._tour(fil)

    async def repondre(
        self, fil: Sequence[MessageChat], question: QuestionOutillage, valeur: str
    ) -> ReponseChat:
        """Le tour qui suit un geste : ce qu'il déduit, puis la question suivante.

        Le fil reçu contient **déjà** le geste (`ServiceChat` l'écrit avant d'appeler
        le répondeur), donc `valeur` n'a pas à y être ajoutée ici : la relire du fil
        est ce qui garantit que la suite se calcule sur ce qui est persisté, et non
        sur un argument qui aurait pu ne jamais y arriver.

        `question` sert à nommer ce que ce geste vient d'entraîner — les déductions
        **nouvelles**, celles que la réponse à cette question-là a produites. Sans
        elle, on redirait à chaque tour toutes les déductions du fil.
        """
        donnes = list(choix_du_fil(fil))
        avant = {c.cle for c in deductions([c for c in donnes if c.cle != question.cle])}
        nouvelles = tuple(c for c in deductions(donnes) if c.cle not in avant)
        return self._tour(fil, prelude=_phrase_des_deductions(nouvelles))


def _joint(prelude: str, corps: str) -> str:
    """Colle le prélude au corps — sans ligne vide inutile quand il n'y en a pas."""
    return f"{prelude}\n\n{corps}" if prelude else corps
