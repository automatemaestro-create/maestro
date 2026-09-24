"""L'outillage d'un projet, côté Control Tower : l'analyser, le demander, l'écrire.

Les trois bouts du même outillage (#1030, #1031, #1033), et ils tiennent dans un module
parce qu'ils servent la même chose à la même API : `ServiceOutillage` analyse un projet
**existant** puis écrit l'outillage retenu, `ConducteurOutillage` pose à l'utilisateur
les questions qui décident de celui d'un projet **neuf**. Tout ce qu'ils décident — les
questions, les constats, la recommandation, le texte écrit — vit dans
`maestro.outillage`, qui ne connaît ni HTTP ni projet déclaré ; ici vit seulement ce qui
les fait tenir dans une Control Tower.

**Ils ne se croisent nulle part**, et c'est voulu : un projet qu'on analyse n'a pas de
questionnaire, un projet neuf n'a rien à analyser. Ce qu'ils partagent est ce qu'ils
rendent — la `Recommandation` du lot 2, que `recommandation_depuis_choix` produit en
muant les réponses en `Constats` plutôt qu'en refaisant le chemin.

⚠ **La génération prend l'une ou l'autre, jamais l'analyse par défaut d'un projet
neuf** (#1100). Elle reçoit les réponses quand il y en a, et les mue par les mêmes
fonctions que la recommandation : analyser la racine vide d'un projet neuf rendait un
outillage vide, et les skills que l'écran avait lus dans la recommandation des
réponses disparaissaient à l'écriture sans que le rapport les nomme.

## Le questionnaire : pourquoi dans le fil, et pas dans un formulaire

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
- le geste **ne repasse pas par le juge** de l'orchestration : un clic sur une option
  n'est pas une demande de travail à reconnaître.

## Le modèle comprend, le code vérifie (#1147)

Jusqu'à #1147 la suite du questionnaire était une fonction pure sur un catalogue
fermé — quatre natures, des tests déduits du langage, trois forges —, et un projet
d'une autre sorte n'avait aucune réponse honnête. Elle est désormais **comprise** :
à chaque tour, `ComprehensionModele` confie au modèle ce qui a été dit — la
conversation, puis les réponses, cliquées ou tapées — et reçoit ce qu'il en a
compris (les constats) et ce qui manque encore (les questions, options écrites pour
ce projet). `maestro.outillage.questionnaire` lit cette réponse et ne croit rien sans
le vérifier. Le prompt porte le registre de langue (#945) : ce qu'il fait écrire —
intitulés, options, raisons — s'affiche à la personne.

Un seul cas se passe du modèle : **rien n'a encore été dit**. La question est alors
la question ouverte (« Qu'est-ce que ce projet ? »), sans options — il n'y a rien à
comprendre, et un appel ne rendrait que ce qu'on sait déjà.

## Le fil est la seule mémoire, et c'est ce qui rend ce module si petit

`ConducteurOutillage` **ne retient rien** du questionnaire. Son état est la suite des
messages : `chat.choix_du_fil` relit les réponses, le modèle les comprend, et ce qu'il
a compris est **écrit sur le message** qui pose la question suivante
(`MessageChat.comprehension`) — c'est ce que la conclusion relit pour écrire
l'outillage, sans rappeler le modèle et donc sans risquer qu'il comprenne autre chose
entre l'écran et l'écriture. Trois conséquences qui tombent d'elles-mêmes :

- rouvrir la Control Tower, changer de poste, recharger la page ne perd rien ;
- rouvrir un questionnaire en cours le **reprend** où il en est plutôt que d'en
  recommencer un second ;
- revenir sur une réponse la corrige, **tapée** comme cliquée : une phrase écrite dans
  la zone de saisie pendant qu'une question attend est la réponse à cette question
  (`chat.ServiceChat._deposer`), et le modèle la lit dans l'ordre.

C'est la propriété que `orchestration` tient déjà pour le cadrage, appliquée à un objet
qui a plus d'un tour.

Enfin la question ne se pose **nulle part ailleurs** que dans le fil : l'étape
d'outillage du parcours de création (#1034) montera cette même carte, elle n'en fera
pas une seconde.

## Et le fil **écrit**, depuis #1104

Les deux voies du questionnaire mènent maintenant au même endroit. Celle du parcours
de création y menait depuis #1100 ; celle du fil concluait sur « rien n'est écrit
tant que vous ne l'avez pas validé » et **rien ne validait** — le pied du fil
redevenait vide dès que le dernier message ne portait plus de question (réserve R7
du bouclage de « L'équipe sur mesure »).

Rien n'a été ajouté ici pour cela, et c'est le signe que le partage était bon : la
conclusion du fil se valide par **la même route** que l'étape de création
(`POST …/outillage/generation`, corps `{retenus, choix}`), parce que cette route ne
sait pas de quelle surface viennent les réponses — elle sait seulement qu'on lui en
donne au lieu d'une analyse. Ce que ce lot a changé tient en deux choses : une
surface qui offre le geste (`apps/web/components/chat/ConclusionOutillage.tsx`), et
une phrase de conclusion qui dit où il est.

## L'analyse et la génération : une couche mince sur `maestro.outillage`

La pièce que les routes `GET /api/projets/{id}/outillage/analyse` et
`POST /api/projets/{id}/outillage/generation` appellent, au patron de
[`maestro.controltower.projets`](./projets.py) : le service tient la forme JSON
et les refus, `app.py` ne fait que les traduire en codes HTTP.

**Une couche mince, et c'est voulu.** Toute l'analyse vit dans
`maestro.outillage` — qui ne connaît ni HTTP ni projet déclaré. Ce module n'en
ajoute qu'une chose : il **résout le projet** avant de regarder le disque. C'est
ce qui fait que la route n'analyse jamais un chemin qu'on lui apporte, mais
seulement la racine d'un projet déjà déclaré, donc déjà passée par
`valider_racine` (EF-38). Une route qui accepterait un chemin libre serait une
seconde porte d'entrée sur le disque à côté de l'explorateur, avec ses propres
frontières à tenir d'accord — exactement ce que `POST /api/projets/racine`
(#938) a rassemblé en une porte unique.

**Le périmètre du projet s'applique.** `Projet.perimetre` retire d'office
`.env` et `**/secrets/**` (docs/24 §2.5) : l'analyse ne peut donc pas lire les
deux gisements de secrets d'un dépôt d'utilisateur, et ce n'est pas une
précaution prise ici mais une propriété de ce qui est déclaré.

**L'analyse n'écrit rien.** Depuis #1158 elle **lit** : les tables ne rendent plus
que des indices, et le modèle lit le projet (`maestro.outillage.exploration`) —
deux verbes servis dans le périmètre, chaque constat confronté à ce qu'il a lu. Un
modèle ne répondant pas deux fois pareil, la dernière lecture réussie d'un projet
est **gardée** tant qu'il n'a pas bougé : deux appels sur un projet inchangé
rendent alors la même analyse, `id` compris — c'est la même lecture —, et la
génération écrit ce que l'écran a montré. **La génération, elle, écrit** (#1033) —
et c'est ici que le régime d'écriture de docs/24 §2.4 se referme :

- un projet **non versionné** reçoit son outillage **en place**, dans sa racine :
  il n'y a pas de moment de fusion où accrocher un accord, et ce qui garde est la
  frontière d'écriture, le manifeste qui refuse d'écraser et le rapport ;
- un projet **versionné** le reçoit sur une branche `maestro/outillage-…`, que
  `appliquer_sous_validation` propose ensuite à la **validation humaine**, diff
  sous les yeux (EF-37). C'est le geste unitaire de #227, pas un second : ce
  module marie le préparateur (`maestro.outillage.ecriture`) et le validateur,
  exactement comme `maestro.engine.executor` le fait pour le travail d'une tâche.

**Pourquoi le validateur est passé ici et non construit ici.** Il vient de
l'appelant (`create_app`, qui tient le bus d'événements) pour la raison qui vaut
déjà dans le moteur : un module qui écrit dans le projet de quelqu'un ne doit pas
être celui qui décide qu'on a le droit d'y écrire. Sans validateur, `Guardrails`
**refuse** (fail-safe, #9) : l'outillage reste sur sa branche et rien n'atteint la
racine — jamais l'inverse.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from maestro.agents.catalog import MODELE_EXECUTANT_DEFAUT
from maestro.agents.playbook_du_code import registre
from maestro.controltower.chat import (
    UTILISATEUR,
    MessageChat,
    ReponseChat,
    choix_du_fil,
    transcription,
)
from maestro.controltower.projets import ServiceProjets
from maestro.controltower.validation import appliquer_sous_validation
from maestro.engine.guardrails import Validateur
from maestro.outillage import (
    REGIME_BRANCHE,
    Analyse,
    Bornes,
    analyser,
    generer_outillage,
    lire_le_projet,
    sans_lecture,
)
from maestro.outillage.modele import Lecture, Recommandation
from maestro.outillage.questionnaire import (
    Choix,
    Comprehension,
    QuestionOutillage,
    acquis_de,
    comprehension_depuis_texte,
    constats_depuis_choix,
    donnees,
    question_ouverte,
    recommandation_depuis_choix,
    reponses_en_texte,
    resume_des_choix,
    schema_en_texte,
    source_manifeste_des_choix,
)
from maestro.projets import Projet
from maestro.providers.base import ModelProvider

#: Ce que le modèle lit de la conversation, au plus — les **derniers** caractères.
#: Le fil de l'orchestration porte tout ce qu'on y a dit, runs compris ; la
#: description du projet est presque toujours récente, et la borne évite qu'un fil
#: de six mois fasse le prix de chaque question.
CONVERSATION_MAX = 6000

#: Le cadre de la compréhension d'un projet neuf (#1147). **Concaténé**, comme
#: `_PROMPT_ORCHESTRATION` et pour la même raison : le contrat de réponse est un
#: gabarit JSON, dont les accolades littérales se liraient comme des champs dans une
#: f-string. Il porte le registre (#945) : intitulés, options et raisons s'affichent à
#: la personne, donc ils la vouvoient.
_PROMPT_COMPREHENSION = (
    """\
Tu aides Maestro à comprendre un projet NEUF — un dossier encore vide — pour lui écrire
son outillage : les instructions que tout agent lira, et les commandes qui installent,
construisent, testent, vérifient et lancent le projet. Tu ne vois aucun fichier : tu ne
sais du projet que ce que la personne en a dit, dans la conversation et dans ses
réponses.

À chaque tour :
1. Tire de ce qui a été dit les CONSTATS : ce qu'on sait du projet, et ce qui en découle
   sans ambiguïté (un projet Flutter s'écrit en Dart, ses commandes vivent dans
   pubspec.yaml, ses tests se lancent par flutter test). N'invente rien : un choix encore
   ouvert n'est pas un constat.
2. Ne pose que les QUESTIONS qui comblent un vrai manque : ce qui décide de l'outillage et
   que rien de ce qui a été dit ne tranche. Une seule par sujet. Quand plus rien ne manque,
   n'en pose aucune : c'est fini. Il n'y a pas de nombre de questions à atteindre.

Les sujets, et la clé à employer pour chacun :
"""
    + schema_en_texte()
    + """

Pour "installer", "construire", "tester", "lint", "formater", "types" et "demarrer", la
valeur est la commande exacte, telle qu'on la taperait. Pour "manifeste" et "ci", un
chemin de fichier relatif au projet. La valeur "aucun" dit explicitement qu'il n'y en a
pas (« pas de tests pour l'instant ») : ce n'est pas un manque. Un constat ne s'écrit que
sous l'une de ces clés : hors d'elles, il ne nourrirait aucune entrée de l'outillage.

Les réponses de la personne font foi. Une réponse CLIQUÉE vaut telle quelle, sauf si une
réponse tapée plus tard la corrige. Une réponse TAPÉE est dite avec ses mots : comprends-la
et traduis-la en constats. Si elle ne répond pas à la question — une question en retour,
un doute —, réponds-y brièvement dans "message", et demande autrement ce qui manque.

Les options d'une question sont écrites POUR CE PROJET : deux à quatre options plausibles
pour ce qui a été dit, chacune avec une raison d'une ligne — ce que ce choix entraîne.
"recommande" est la valeur de l'une d'elles, et "pourquoi" dit, en une seule phrase courte,
ce qui, dans CE projet, la désigne. L'intitulé est une question courte — dix mots au plus :
tout s'affiche sur une carte, parfois dans une colonne étroite.
Ne justifie jamais un choix par ce que Maestro utilise lui-même : Maestro n'est pas le
projet. Si rien ne désigne une option plutôt qu'une autre, dis-le. La personne peut
toujours répondre autre chose avec ses mots : n'ajoute pas d'option « autre ».

Réponds par un objet JSON et rien d'autre — ni texte autour, ni bloc de code :

{"message": "...",
 "constats": [{"cle": "...", "valeur": "...", "parce_que": "..."}],
 "questions": [{"cle": "...", "intitule": "...",
                "options": [{"valeur": "...", "libelle": "...", "raison": "..."}],
                "recommande": "...", "pourquoi": "..."}]}

- "message" : une phrase à la personne, facultative — vide s'il n'y a rien à dire ;
- "constats" : TOUT ce qu'on sait à ce tour, réponses comprises, pas seulement ce qui est
  nouveau ;
- "parce_que" : ce qui, dans ce qui a été dit, établit le constat, en quelques mots ;
- "questions" : dans l'ordre où les poser, vide quand plus rien ne manque. Tant que la
  sorte de projet n'est pas dite, la seule question est "nature", sans options.

"""
    + registre()
)


class ComprehensionModele:
    """Ce que Maestro comprend d'un projet neuf, demandé au modèle (#1147).

    **Un appel par tour**, et c'est le prix de « rien de figé » : chaque réponse de la
    personne — cliquée ou tapée — est relue avec tout ce qui a été dit avant, et le
    modèle rend la compréhension **entière** (tous les constats, les questions qui
    restent). C'est ce qui permet à une correction tapée de défaire un clic antérieur.

    Le fournisseur est résolu **au premier usage**, comme celui des répondeurs du
    chat : construire le service ne coûte rien et ne lève aucune erreur de
    configuration. Un fournisseur **injecté** — un double de test, un câblage
    explicite — est pris tel quel. Le modèle suit le fournisseur (#1173,
    `modele_du_canal`) : `MAESTRO_MODEL` s'il est posé, sinon le défaut du canal.

    Ce que rend le modèle est lu par `comprehension_depuis_texte`, qui ne croit rien
    sans le vérifier ; un texte illisible lève `ComprehensionIllisible`, que l'appelant
    traduit en réponse indisponible.
    """

    def __init__(self, provider: ModelProvider | None = None) -> None:
        self._provider = provider

    async def comprendre(
        self, conversation: str, reponses: Sequence[Choix]
    ) -> Comprehension:
        """La compréhension de ce qui a été dit — `conversation` puis les `reponses`."""
        from maestro.providers.factory import modele_du_canal, provider_from_settings

        if self._provider is None:
            self._provider = provider_from_settings()
        fournisseur = self._provider
        texte = await fournisseur.generate(
            _prompt_de_comprehension(conversation, reponses),
            model=modele_du_canal(MODELE_EXECUTANT_DEFAUT, fournisseur),
            system_prompt=_PROMPT_COMPREHENSION,
        )
        return comprehension_depuis_texte(texte, reponses)


def _prompt_de_comprehension(conversation: str, reponses: Sequence[Choix]) -> str:
    """Le prompt d'utilisateur : ce qui a été dit, puis les réponses aux questions."""
    dit = conversation.strip() or "(rien d'autre n'a été dit)"
    return (
        "## Ce qui a été dit dans la conversation\n\n"
        f"{dit}\n\n"
        "## Les réponses aux questions, dans l'ordre\n\n"
        f"{reponses_en_texte(reponses)}\n"
    )


def _conversation_de(fil: Sequence[MessageChat]) -> str:
    """Ce que le modèle lit du fil : la conversation, bornée à ses derniers caractères.

    Les lignes sont celles de `chat.transcription` — même libellé par auteur, le
    contenu des sources rangé sous le message qui les a portées, déjà encadré comme
    donnée (ENF-13) : un cahier des charges joint **décrit** le projet, et c'est
    exactement ce qu'on cherche ici. Seule la consigne finale de `transcription`
    (« réponds au dernier message ») n'est pas reprise : elle s'adresse à un
    répondeur, pas à qui comprend un projet.

    Vide quand la personne n'a encore **rien dit** — c'est ce qui dispense d'appeler
    le modèle pour ouvrir un questionnaire sur un fil muet.
    """
    if not any(m.auteur == UTILISATEUR and m.resume.strip() for m in fil):
        return ""
    corps = transcription(fil).split("\n\n", 1)[-1].rsplit("\n\n", 1)[0]
    return corps if len(corps) <= CONVERSATION_MAX else "…" + corps[-CONVERSATION_MAX:]


def _retenue(
    recommandation: Recommandation, retenus: Sequence[str] | None
) -> tuple[Recommandation, tuple[str, ...]]:
    """La recommandation réduite à ce que l'écran a **gardé** (#1034) — et ce qui ne désignait rien.

    `None` rend la recommandation telle quelle — un appel qui ne vient pas d'un
    écran écrit tout ce qui est recommandé, et c'est le comportement de #1033
    inchangé.

    Le filtre porte sur `entrees` et **pas** sur `ecartes` : les écartés ne sont
    pas des entrées qu'on aurait décochées, ce sont des choses que le projet ne
    justifie pas (docs/38 §3.5) — il n'y a rien à écrire pour elles, et les
    « ajouter » n'aurait pas de sens. Ce que l'écran appelle *ajouter* est la
    remise d'une entrée retirée, ou la reprise d'une entrée `deja-present` que
    la liste ne gardait pas d'office : dans les deux cas son `chemin` revient
    ici, et rien d'autre ne change.

    Un chemin inconnu n'est **pas refusé**, mais il est **rendu** (second terme,
    dans l'ordre reçu, sans doublon) : ce n'est pas une saisie fautive, c'est une
    entrée que l'écran a lue et que la génération ne reconnaît plus. Jusqu'à
    #1100 il était ignoré sans bruit — et c'est ainsi que les quatre skills d'un
    projet neuf disparaissaient d'une génération qui rendait `retires: []` : la
    génération dérivait de l'analyse d'une racine vide, et aucun des chemins que
    l'écran avait lus dans la recommandation des **réponses** n'y correspondait.
    Une ligne qui disparaît entre deux lectures doit se lire dans le rapport,
    sans quoi « non écrit » se confond avec « écrit ».
    """
    if retenus is None:
        return recommandation, ()
    gardes = set(retenus)
    connus = {e.chemin for e in recommandation.entrees}
    inconnus = tuple(dict.fromkeys(c for c in retenus if c not in connus))
    return (
        Recommandation(
            entrees=tuple(e for e in recommandation.entrees if e.chemin in gardes),
            ecartes=recommandation.ecartes,
        ),
        inconnus,
    )


def _phrase_de_la_question(question: QuestionOutillage) -> str:
    """Le texte du message qui porte une question — ce qu'on lit si rien ne s'affiche.

    La carte rend l'intitulé, ses options et sa recommandation ; ce texte-ci est ce
    qui reste quand on relit le fil ailleurs (un export, une lettre inter-agents, un
    client qui ne connaît pas le champ `question`). Il **redit** donc l'intitulé, et
    c'est voulu : un message vide dans un fil est un trou, et `ServiceChat` refuse de
    toute façon une réponse vide. Une question sans option (la question ouverte) n'a
    pas de proposition à dire : elle dit comment y répondre.
    """
    if not question.options:
        return f"{question.intitule}\n{question.pourquoi}"
    return (
        f"{question.intitule}\n"
        f"Je propose « {question.libelle_de(question.recommande)} » — "
        f"{question.pourquoi}"
    )


def _phrase_de_conclusion(acquis: Sequence[Choix]) -> str:
    """Ce que le fil dit quand il n'y a plus de question : l'outillage recommandé.

    Le questionnaire ne s'arrête pas sur un silence. Il rend **ce qu'il a produit** —
    le résumé du manifeste à venir et le compte des entrées —, parce que c'est la
    seule chose qui donne rétrospectivement un sens aux questions qu'on vient de
    répondre. Le détail, lui, se sert par l'API (`POST …/outillage/recommandation`)
    et s'affiche là où on le valide (#1034) : le redire ici en entier ferait du fil un
    second écran de recommandation.

    ⚠ **La dernière phrase promet un geste, donc elle le nomme** (#1104). « Rien
    n'est écrit tant que vous ne l'avez pas validé » était vraie et pourtant
    trompeuse : jusqu'à ce lot, aucune surface n'offrait de quoi valider — le pied
    du fil redevenait vide dès que le dernier message ne portait plus de question.
    Le geste existe désormais (`ConclusionOutillage`, au pied de la conversation),
    et la phrase dit où il est. C'est la règle du canal, pas une politesse : ce
    qu'un message annonce doit se trouver là où il dit qu'il est.
    """
    reco = recommandation_depuis_choix(acquis)
    skills = sum(1 for e in reco.entrees if e.type == "skill")
    return (
        f"C'est tout ce qu'il me fallait — {resume_des_choix(acquis)}.\n"
        f"L'outillage recommandé : {len(reco.entrees)} entrée(s), dont {skills} skill(s), "
        "chacune avec la raison qui la justifie. Rien n'est écrit dans le projet tant "
        "que vous ne l'avez pas validé.\n"
        "Le geste est au pied de cette conversation : il dit ce qui sera écrit, "
        "et dans quel dossier."
    )


class ConducteurOutillage:
    """Conduit le questionnaire d'outillage d'un projet neuf dans un fil de chat.

    **Aucun état du questionnaire, et c'est la propriété qui compte** : tout ce qu'il
    sait, il le relit du fil qu'on lui passe. Son seul attribut est son
    **collaborateur** — celui qui comprend (`ComprehensionModele`) —, jamais une
    réponse, un tour ou une question en cours. Il est donc sûr de le partager entre
    conversations, et il ne peut pas se désaccorder de ce qui est persisté — la même
    garantie que `RepondeurOrchestration` tient pour une proposition de run (« aucun
    état de session », #685).

    Le **projet** n'y est pas non plus : la recommandation ne dépend que des constats
    (`recommandation_depuis_choix`), et le projet ne sert qu'à dater la provenance
    dans le manifeste (`source_manifeste_des_choix`, appelé par l'API qui, elle, sait
    de quel projet il s'agit).
    """

    def __init__(self, comprehension: ComprehensionModele | None = None) -> None:
        self._comprehension = comprehension or ComprehensionModele()

    async def _tour(self, fil: Sequence[MessageChat]) -> ReponseChat:
        """Le tour suivant : la question à poser, ou la conclusion. Jamais rien.

        Sur un fil où la personne n'a **rien dit**, la question ouverte, sans appeler
        le modèle. Sinon le modèle comprend ce qui a été dit, et sa compréhension
        voyage sur le message (`ReponseChat.comprehension`) : c'est elle que la
        conclusion relira pour écrire l'outillage.

        Ce que le modèle a à dire avant la question (`Comprehension.message`) est
        **préfixé** au même message plutôt que posé dans un second : deux messages
        d'agent d'affilée décaleraient la question du geste qui l'a appelée, et
        `question_en_attente` ne lit que le **dernier** message.

        Ce qu'il a **compris** ne se recopie pas dans le texte : il voyage sur le
        message, et la carte le rend en tête de la question, une entrée par constat.
        La relecture de #1147 l'a montré recopié en entier dans chaque bulle du fil,
        un flot de clés qui noyait la question.
        """
        reponses = choix_du_fil(fil)
        conversation = _conversation_de(fil)
        if not conversation and not reponses:
            ouverte = question_ouverte()
            return ReponseChat(contenu=_phrase_de_la_question(ouverte), question=ouverte)
        comprise = await self._comprehension.comprendre(conversation, reponses)
        acquis = acquis_de([*reponses, *comprise.constats])
        suivante = comprise.question_suivante(rang=len(donnees(reponses)) + 1)
        if suivante is None:
            return ReponseChat(
                contenu=_joint(comprise.message, _phrase_de_conclusion(acquis)),
                comprehension=acquis,
            )
        return ReponseChat(
            contenu=_joint(comprise.message, _phrase_de_la_question(suivante)),
            question=suivante,
            comprehension=acquis,
        )

    async def ouvrir(self, fil: Sequence[MessageChat]) -> ReponseChat:
        """Ouvre — ou **reprend** — le questionnaire sur ce fil.

        Aucune différence entre les deux, et c'est la propriété qu'on veut : ouvrir
        un questionnaire déjà commencé repose la question là où il en est. Elle
        découle du fil comme seule mémoire, elle n'est pas gardée. Ce qui a déjà été
        dit dans la conversation compte : un projet décrit trois messages plus haut
        n'a pas à l'être une seconde fois.
        """
        return await self._tour(fil)

    async def repondre(
        self, fil: Sequence[MessageChat], question: QuestionOutillage, valeur: str
    ) -> ReponseChat:
        """Le tour qui suit une réponse — cliquée ou tapée : la suite, comprise.

        Le fil reçu contient **déjà** la réponse (`ServiceChat` l'écrit avant
        d'appeler le répondeur), donc `valeur` n'a pas à y être ajoutée ici : la relire
        du fil est ce qui garantit que la suite se calcule sur ce qui est persisté, et
        non sur un argument qui aurait pu ne jamais y arriver. `question` et `valeur`
        restent dans la signature du contrat de `RepondeurChat.repondre_question`.
        """
        del question, valeur  # relues du fil, voir ci-dessus
        return await self._tour(fil)


def _joint(prelude: str, corps: str) -> str:
    """Colle le prélude au corps — sans ligne vide inutile quand l'un des deux manque."""
    return "\n\n".join(morceau for morceau in (prelude, corps) if morceau)


def _empreinte(indices: Analyse) -> str:
    """Ce qui, du relevé des tables, change quand le projet change — ni l'id ni la date."""
    return json.dumps(
        {
            "racine": indices.racine,
            "bornes": indices.bornes.to_dict(),
            "parcours": indices.parcours.to_dict(),
            "constats": indices.constats.to_dict(),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def _temoins(racine: Path, lecture: Lecture) -> tuple[tuple[str, int, int], ...]:
    """Taille et date de ce que la lecture a ouvert ou listé — bloquant, jamais d'exception.

    Le relevé des tables ne voit pas le **contenu** d'un fichier qu'il ne sait
    pas lire : un `.csproj` modifié sans changer de nom laisse l'empreinte
    intacte. Ce que le modèle a lu, lui, a une date — et un dossier listé en a
    une qui bouge quand une entrée y naît ou disparaît. Un chemin devenu
    illisible compte comme changé.
    """
    temoins: list[tuple[str, int, int]] = []
    for chemin in (*lecture.lus, *lecture.listes):
        try:
            etat = os.stat(racine / chemin, follow_symlinks=False)
        except OSError:
            temoins.append((chemin, -1, -1))
            continue
        temoins.append((chemin, etat.st_size, etat.st_mtime_ns))
    return tuple(temoins)


class _LectureGardee:
    """La dernière lecture **réussie** d'un projet, et de quoi savoir si elle vaut encore."""

    def __init__(self, empreinte: str, analyse: Analyse, temoins: tuple[Any, ...]) -> None:
        self.empreinte = empreinte
        self.analyse = analyse
        self.temoins = temoins

    @classmethod
    def de(cls, empreinte: str, analyse: Analyse, lecture: Lecture) -> _LectureGardee:
        """La lecture gardée, témoins relevés maintenant — bloquant."""
        return cls(empreinte, analyse, _temoins(Path(analyse.racine), lecture))

    def vaut_pour(self, empreinte: str) -> bool:
        """Le projet est-il celui qui a été lu ? Même relevé, mêmes fichiers — bloquant."""
        lecture = self.analyse.lecture
        if empreinte != self.empreinte or lecture is None:
            return False
        return _temoins(Path(self.analyse.racine), lecture) == self.temoins


class ServiceOutillage:
    """L'outillage d'un projet déclaré, servi par l'API : analysé, choisi, puis écrit.

    `bornes` permet de resserrer la lecture (les tests s'en servent pour
    fabriquer une troncature sur un projet minuscule) ; `None` — le cas nominal
    — laisse le défaut de `maestro.outillage`, dossiers ignorés compris. Elle ne
    concerne que l'analyse : un questionnaire ne lit rien.

    **Les deux voies exigent un projet déclaré**, et c'est la même raison des deux
    côtés : une route qui accepterait un chemin libre serait une seconde porte
    d'entrée sur le disque à côté de l'explorateur. Le questionnaire, lui, n'ouvre
    aucun fichier — mais son résultat nomme un projet dans la provenance du
    manifeste, et ce projet doit être celui qu'on a déclaré, pas une chaîne
    apportée par l'appelant.

    `validateur` tranche « écrire cet outillage dans mon projet ? » sur un projet
    versionné. `None` **refuse** (fail-safe des garde-fous, #9) : la branche reste
    et la racine est intacte. C'est le bon défaut — un service monté sans
    validateur ne doit pas écrire chez quelqu'un parce que personne n'a été
    câblé pour dire non.

    `provider` est le modèle qui **lit** un projet existant (#1158) — résolu
    **paresseusement** comme le générateur d'agent (#257) : construire le service
    ne coûte rien et ne lève aucune erreur de configuration, ce dont `create_app`
    dépend. Sans fournisseur résolu, l'analyse reste celle des tables et le dit
    (`lecture.etat == "indisponible"`) : un projet se relit toujours, modèle ou
    pas.

    **Une lecture réussie est gardée** tant que le projet n'a pas bougé
    (`_LectureGardee`). C'est ce qui fait que la génération écrit ce que l'écran
    a montré : relire le projet entre l'analyse et l'écriture, c'est redemander
    au modèle, et un modèle n'est pas tenu de répondre deux fois pareil — les
    skills que l'écran a lus disparaîtraient à l'écriture, exactement le défaut
    de #1100. Une lecture **manquée** n'est jamais gardée : le prochain appel
    retente.

    `comprehension` (#1147) est ce qui comprend un projet neuf — le modèle, derrière
    `ComprehensionModele`. `None` en construit un qui résout le fournisseur configuré
    au premier usage ; seule la voie du questionnaire l'appelle.
    """

    def __init__(
        self,
        projets: ServiceProjets,
        *,
        bornes: Bornes | None = None,
        validateur: Validateur | None = None,
        provider: ModelProvider | None = None,
        modele: str = MODELE_EXECUTANT_DEFAUT,
        comprehension: ComprehensionModele | None = None,
    ) -> None:
        self._projets = projets
        self._bornes = bornes
        self._validateur = validateur
        self._provider = provider
        self._modele = modele
        self._lues: dict[str, _LectureGardee] = {}
        self._comprehension = comprehension or ComprehensionModele()

    async def analyser(self, id_projet: str) -> dict[str, Any]:
        """Analyse le projet `id_projet` et rend l'outillage recommandé (docs/38).

        Lève `ProjetInconnu`/`ProjetIllisible` **avant de toucher au disque** —
        un projet qu'on ne sait pas lire n'a pas de racine à parcourir —, et
        `RacineRefusee` motivée si la racine déclarée n'est plus un dossier
        lisible (`dossier-absent`, `pas-un-dossier`). Les trois portent un
        `motif` : c'est ce que `statut_http`/`detail_refus` traduisent, jamais
        un 500.

        Le parcours des tables et chaque lecture servie au modèle sont joués
        **hors de la boucle d'événements** : parcourir un projet réel prend des
        secondes, et une route qui bloquerait la boucle figerait les flux SSE
        des autres écrans.
        """
        return (await self._analyse(self._projet(id_projet))).to_dict()

    async def generer(
        self,
        id_projet: str,
        *,
        retenus: Sequence[str] | None = None,
        choix: Sequence[Choix] = (),
        run_id: str = "",
    ) -> dict[str, Any]:
        """Écrit dans `id_projet` l'outillage que son analyse — ou ses réponses — recommandent.

        Le déroulé, dans cet ordre — il compte :

        1. le projet est **résolu**, puis sa **matière** est obtenue : l'analyse
           de sa racine (lecture seule) quand aucun choix n'est donné, la mue des
           **réponses** au questionnaire sinon (#1100) ;
        2. l'outillage est **préparé** au régime du projet, hors de la boucle
           d'événements (parcours du disque, et sous-processus Git sur un projet
           versionné) ;
        3. sur un projet **versionné seulement**, la branche est soumise à
           l'**accord humain**, diff en pièce jointe — et l'attente est celle du
           mécanisme existant : indéfinie, sans time-out silencieux. Un refus,
           ou un validateur absent, laisse la branche intacte : l'outillage
           proposé reste consultable et se récupère d'un `git merge`.

        `retenus` (#1034) est ce que l'utilisateur a **gardé** à l'étape
        d'outillage : la liste des `chemin` d'entrées. Seules ces entrées sont
        rédigées ; le reste n'est pas écrit et **quitte le manifeste sans quitter
        le disque**, ce qui est déjà la règle de docs/38 §4.2 pour « un fichier
        que l'analyse ne recommande plus ». C'est le seul endroit où le choix de
        l'écran entre dans la génération : filtrer la **recommandation** suffit,
        parce que c'est elle, et elle seule, que la rédaction parcourt. `None` —
        un appel sans écran — écrit tout ce qui est recommandé. Un chemin qui ne
        correspond à aucune entrée est rendu dans `retenus_inconnus`, jamais
        perdu en silence (`_retenue`).

        `choix` (#1100) sont les réponses d'un projet **neuf**, et depuis #1147 ce
        qui en a été compris (les constats `deduit`, tels que le questionnaire les a
        rendus) : ils tiennent lieu d'analyse, par les **mêmes** fonctions que
        `recommandation` et que la proposition d'équipe
        (`ServiceEquipe._matiere_choisie`). Le modèle **n'est pas rappelé** ici :
        l'outillage écrit est celui que l'écran a montré, pas une seconde lecture
        qui pourrait comprendre autre chose. Ce sont les **constats** qui voyagent,
        jamais les entrées ni leur contenu : le quoi et le où restent dérivés ici.
        `source` dit alors d'où sort l'outillage (`type: "choix"`, les constats en
        `reference`) et `analyse` est vide : il n'y en a pas eu.

        Rend le rapport dans les deux régimes, `application` portant le verdict
        de la validation quand il y en a eu une. Lève les refus **motivés** de
        ses couches (`ProjetInconnu`, `RacineRefusee`, `EspaceProjetIndisponible`,
        `ApplicationRefusee`) — jamais un 500 : écrire chez quelqu'un peut être
        refusé, ce n'est pas une panne.
        """
        projet = self._projet(id_projet)
        acquis = list(choix)
        if acquis:
            reference = ""
            constats = constats_depuis_choix(acquis)
            recommandee = recommandation_depuis_choix(acquis)
            source = source_manifeste_des_choix(projet.id, acquis)
        else:
            analyse = await self._analyse(projet)
            reference = analyse.id
            constats, recommandee = analyse.constats, analyse.recommandation
            source = analyse.source_manifeste()
        recommandation, inconnus = _retenue(recommandee, retenus)
        preparation = await asyncio.to_thread(
            generer_outillage, projet, constats, recommandation, source=source
        )
        reponse: dict[str, Any] = {
            "projet_id": projet.id,
            "analyse": reference,
            "source": dict(source),
            **preparation.to_dict(),
            "retenus_inconnus": list(inconnus),
            "application": None,
        }
        if preparation.regime != REGIME_BRANCHE:
            return reponse
        resultat = await appliquer_sous_validation(
            projet,
            tache_id=preparation.tache_id,
            validateur=self._validateur,
            branche=preparation.branche,
            titre=f"Écrire l'outillage dans {projet.nom}",
            run_id=run_id,
        )
        reponse["application"] = resultat.to_dict()
        return reponse

    async def _analyse(self, projet: Projet) -> Analyse:
        """L'analyse du projet : les indices des tables, puis la lecture par le modèle.

        Le seul lecteur du disque de ce module. La lecture gardée est rendue
        telle quelle si le projet n'a pas bougé depuis — même relevé des tables,
        mêmes fichiers lus et dossiers listés, à la taille et à la date près.
        """
        indices = await asyncio.to_thread(self._indices, projet)
        empreinte = _empreinte(indices)
        gardee = self._lues.get(projet.id)
        if gardee is not None and await asyncio.to_thread(gardee.vaut_pour, empreinte):
            return gardee.analyse
        try:
            fournisseur = self._fournisseur()
        except Exception as exc:  # noqa: BLE001 — sans fournisseur, les tables seules
            return sans_lecture(indices, f"aucun fournisseur de modèle n'est utilisable : {exc}")
        analyse = await lire_le_projet(
            indices, perimetre=projet.perimetre, provider=fournisseur, modele=self._modele
        )
        if analyse.lecture is not None and analyse.lecture.etat == "lue":
            self._lues[projet.id] = await asyncio.to_thread(
                _LectureGardee.de, empreinte, analyse, analyse.lecture
            )
        return analyse

    def _indices(self, projet: Projet) -> Analyse:
        """Le relevé des tables — **bloquant**, joué hors de la boucle d'événements."""
        return analyser(
            projet.racine_chemin,
            projet_id=projet.id,
            perimetre=projet.perimetre,
            bornes=self._bornes,
        )

    def _fournisseur(self) -> ModelProvider:
        """Le fournisseur de la lecture, résolu au premier usage (import local, comme #257).

        Le modèle suit le fournisseur configuré (#1173) : le défaut de ce canal
        est un nom Claude, qui n'a de sens que chez Claude. Résolu **avant** de
        retenir le fournisseur, pour qu'un échec laisse tout à résoudre au
        prochain appel plutôt qu'un fournisseur sans son modèle.
        """
        if self._provider is None:
            from maestro.providers.factory import modele_du_canal, provider_from_settings

            fournisseur = provider_from_settings()
            self._modele = modele_du_canal(self._modele, fournisseur)
            self._provider = fournisseur
        return self._provider

    async def question(self, id_projet: str, choix: Sequence[Choix]) -> dict[str, Any]:
        """La prochaine question du questionnaire, vu les réponses données (#1031, #1147).

        **Sans état** : l'appelant dit ce qu'il a, le service dit ce qui en découle.
        C'est ce qui permet au fil — qui tient ses réponses dans ses messages — et au
        parcours de création (#1034) — qui les tient à l'écran — de servir du même
        questionnaire sans partager de session.

        Seules les réponses **données** (cliquées ou tapées) sont lues : des constats
        `deduit` renvoyés par le client ne sont pas crus, ils sont recompris. Sans
        aucune réponse, la question ouverte, sans appeler le modèle — la voie sans
        état n'a pas de conversation à relire.

        `deductions` rend ce qui a été **compris**, chaque constat avec sa cause : une
        question qu'on ne pose pas n'est pas une question qu'on cache. `question` vaut
        `None` quand plus rien ne manque, et c'est alors `recommandation` qui a
        quelque chose à dire. `message` est ce que le modèle a à dire à la personne
        avant la question — vide le plus souvent.

        Le projet est résolu (404/422 motivés) sans que rien du disque soit lu, et
        avant tout appel au modèle : le questionnaire ne regarde aucun fichier.
        """
        self._projet(id_projet)
        reponses = donnees(choix)
        if not reponses:
            ouverte = question_ouverte()
            return {
                "question": ouverte.to_dict(),
                "deductions": [],
                "terminee": False,
                "message": "",
            }
        comprise = await self._comprehension.comprendre("", reponses)
        acquis = acquis_de([*reponses, *comprise.constats])
        suivante = comprise.question_suivante(rang=len(reponses) + 1)
        return {
            "question": suivante.to_dict() if suivante is not None else None,
            "deductions": [c.to_dict() for c in acquis if c.deduit],
            "terminee": suivante is None,
            "message": comprise.message,
        }

    def recommandation(self, id_projet: str, choix: Sequence[Choix]) -> dict[str, Any]:
        """L'outillage que ces réponses recommandent — **la forme de l'analyse** (#1031).

        La même `Recommandation` que `analyser` rend, produite par la **même**
        fonction (`maestro.outillage.recommandation.recommander`) : c'est le second
        critère du ticket, et il se tient en n'ayant qu'un seul chemin plutôt qu'en
        gardant deux chemins d'accord.

        Rendue à **tout moment**, questionnaire fini ou non : un client qui veut
        montrer ce qui se dessine au fil des réponses n'a pas à attendre la dernière.
        Ce qui n'est pas acquis ne justifie simplement aucune entrée — et
        `recommander` le dit, en écartant le skill correspondant avec sa raison.

        `choix` porte les réponses **et** ce qui en a été compris (les `deduit` que
        le questionnaire a rendus) : **aucun appel au modèle** ici, la recommandation
        est une fonction de ce qu'on lui donne, et c'est ce qui garantit que la
        génération écrira la même chose (`acquis_de` dit ce qui fait foi).

        `source` est le fragment de provenance du manifeste (docs/38 §4.1), le jumeau
        de celui qu'`Analyse.source_manifeste()` rend : c'est lui qui dira, six mois
        plus tard, que cet outillage vient de réponses et lesquelles.
        """
        projet = self._projet(id_projet)
        acquis = acquis_de(choix)
        return {
            "projet_id": projet.id,
            "source": source_manifeste_des_choix(projet.id, acquis),
            "choix": [c.to_dict() for c in acquis],
            "recommandation": recommandation_depuis_choix(acquis).to_dict(),
        }

    def _projet(self, id_projet: str) -> Projet:
        """Le projet déclaré, relu par le service des projets — jamais un second lecteur.

        `ServiceProjets.detail` rend un dict ; ici il faut l'entité (sa racine
        en `Path`, son périmètre), et c'est `ServiceProjets.entite` qui la rend.
        On garde ainsi un seul lecteur de projets dans la Control Tower : deux
        relectures de la même fiche finiraient par ne plus se refuser les mêmes
        fichiers. Ses refus (404/422 motivés) tombent **avant** toute lecture du
        disque.
        """
        return self._projets.entite(id_projet)
