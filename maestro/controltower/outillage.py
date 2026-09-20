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

Enfin la question ne se pose **nulle part ailleurs** que dans le fil : l'étape
d'outillage du parcours de création (#1034) montera cette même carte, elle n'en fera
pas une seconde.

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

**L'analyse n'écrit rien** : deux appels sur un projet inchangé rendent le même
contenu — seuls l'`id` et l'horodatage diffèrent, parce qu'ils datent la lecture
et non le projet. **La génération, elle, écrit** (#1033) — et c'est ici que le
régime d'écriture de docs/24 §2.4 se referme :

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
from collections.abc import Sequence
from typing import Any

from maestro.controltower.chat import MessageChat, ReponseChat, choix_du_fil
from maestro.controltower.projets import ServiceProjets
from maestro.controltower.validation import appliquer_sous_validation
from maestro.engine.guardrails import Validateur
from maestro.outillage import (
    REGIME_BRANCHE,
    Analyse,
    Bornes,
    analyser,
    generer_outillage,
)
from maestro.outillage.modele import Recommandation
from maestro.outillage.questionnaire import (
    Choix,
    QuestionOutillage,
    deductions,
    question_suivante,
    recommandation_depuis_choix,
    resume_des_choix,
    source_manifeste_des_choix,
)
from maestro.projets import Projet


def _retenue(
    recommandation: Recommandation, retenus: Sequence[str] | None
) -> Recommandation:
    """La recommandation réduite à ce que l'écran a **gardé** (#1034).

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

    Un chemin inconnu est **ignoré sans bruit** : la liste vient d'un écran qui
    a lu la même analyse, et un chemin qui n'y correspond plus ne désigne rien
    à écrire. Ce n'est pas une saisie à refuser, c'est une ligne qui a disparu
    entre deux lectures du projet.
    """
    if retenus is None:
        return recommandation
    gardes = set(retenus)
    return Recommandation(
        entrees=tuple(e for e in recommandation.entrees if e.chemin in gardes),
        ecartes=recommandation.ecartes,
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


def _phrase_de_conclusion(acquis: Sequence[Choix]) -> str:
    """Ce que le fil dit quand il n'y a plus de question : l'outillage recommandé.

    Le questionnaire ne s'arrête pas sur un silence. Il rend **ce qu'il a produit** —
    le résumé du manifeste à venir et le compte des entrées —, parce que c'est la
    seule chose qui donne rétrospectivement un sens aux questions qu'on vient de
    répondre. Le détail, lui, se sert par l'API (`POST …/outillage/recommandation`)
    et s'affiche là où on le valide (#1034) : le redire ici en entier ferait du fil un
    second écran de recommandation.
    """
    reco = recommandation_depuis_choix(acquis)
    skills = sum(1 for e in reco.entrees if e.type == "skill")
    return (
        f"C'est tout ce qu'il me fallait — {resume_des_choix(acquis)}.\n"
        f"L'outillage recommandé : {len(reco.entrees)} entrée(s), dont {skills} skill(s), "
        "chacune avec la raison qui la justifie. Rien n'est écrit dans le projet tant "
        "que vous ne l'avez pas validé."
    )


class ConducteurOutillage:
    """Conduit le questionnaire d'outillage d'un projet neuf dans un fil de chat.

    **Aucun attribut, et c'est la propriété qui compte** : tout ce qu'il sait, il le
    relit du fil qu'on lui passe. Il est donc sûr de le partager entre conversations,
    la Control Tower n'en construit qu'un, et il ne peut pas se désaccorder de ce qui
    est persisté — c'est la même garantie que `RepondeurOrchestration` tient pour une
    proposition de run (« aucun état de session », #685), à ceci près qu'ici l'objet a
    plusieurs tours et que la tentation d'en garder un bout est réelle.

    Le **projet** n'y est pas non plus, et c'est le même raisonnement : la
    recommandation ne dépend que des réponses (`recommandation_depuis_choix`), et le
    projet ne sert qu'à dater la provenance dans le manifeste
    (`source_manifeste_des_choix`, appelé par l'API qui, elle, sait de quel projet il
    s'agit).
    """

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
            corps = _phrase_de_conclusion(acquis)
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
    """

    def __init__(
        self,
        projets: ServiceProjets,
        *,
        bornes: Bornes | None = None,
        validateur: Validateur | None = None,
    ) -> None:
        self._projets = projets
        self._bornes = bornes
        self._validateur = validateur

    def analyser(self, id_projet: str) -> dict[str, Any]:
        """Analyse le projet `id_projet` et rend l'outillage recommandé (docs/38).

        Lève `ProjetInconnu`/`ProjetIllisible` **avant de toucher au disque** —
        un projet qu'on ne sait pas lire n'a pas de racine à parcourir —, et
        `RacineRefusee` motivée si la racine déclarée n'est plus un dossier
        lisible (`dossier-absent`, `pas-un-dossier`). Les trois portent un
        `motif` : c'est ce que `statut_http`/`detail_refus` traduisent, jamais
        un 500.

        Appelée **hors de la boucle d'événements** par la route : parcourir un
        projet réel prend des secondes, et une route qui bloquerait la boucle
        figerait les flux SSE des autres écrans.
        """
        return self._analyse(self._projet(id_projet)).to_dict()

    async def generer(
        self,
        id_projet: str,
        *,
        retenus: Sequence[str] | None = None,
        run_id: str = "",
    ) -> dict[str, Any]:
        """Écrit dans `id_projet` l'outillage que son analyse recommande (docs/38, #1033).

        Le déroulé, dans cet ordre — il compte :

        1. le projet est **résolu** puis **analysé** (lecture seule) : on ne
           propose jamais d'écrire sans avoir regardé ce qu'il y a ;
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
        un appel sans écran — écrit tout ce qui est recommandé.

        Rend le rapport dans les deux régimes, `application` portant le verdict
        de la validation quand il y en a eu une. Lève les refus **motivés** de
        ses couches (`ProjetInconnu`, `RacineRefusee`, `EspaceProjetIndisponible`,
        `ApplicationRefusee`) — jamais un 500 : écrire chez quelqu'un peut être
        refusé, ce n'est pas une panne.
        """
        projet = self._projet(id_projet)
        analyse = await asyncio.to_thread(self._analyse, projet)
        recommandation = _retenue(analyse.recommandation, retenus)
        preparation = await asyncio.to_thread(
            generer_outillage,
            projet,
            analyse.constats,
            recommandation,
            source=analyse.source_manifeste(),
        )
        reponse: dict[str, Any] = {
            "projet_id": projet.id,
            "analyse": analyse.id,
            **preparation.to_dict(),
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

    def _analyse(self, projet: Projet) -> Analyse:
        """L'analyse du projet — **bloquante**, et le seul lecteur du disque de ce module."""
        return analyser(
            projet.racine_chemin,
            projet_id=projet.id,
            perimetre=projet.perimetre,
            bornes=self._bornes,
        )

    def question(self, id_projet: str, choix: Sequence[Choix]) -> dict[str, Any]:
        """La prochaine question du questionnaire, vu les réponses déjà acquises (#1031).

        **Sans état** : l'appelant dit ce qu'il a, le service dit ce qui en découle.
        C'est ce qui permet au fil — qui tient ses réponses dans ses messages — et au
        parcours de création (#1034) — qui les tiendra à l'écran — de servir du même
        questionnaire sans partager de session.

        `deductions` rend les réponses que les choix donnés **entraînent**, chacune
        avec sa cause : une question qu'on ne pose pas n'est pas une question qu'on
        cache. `question` vaut `None` quand il n'y en a plus, et c'est alors
        `recommandation` qui a quelque chose à dire.

        Le projet est résolu (404/422 motivés) sans que rien du disque soit lu : le
        questionnaire ne regarde aucun fichier.
        """
        self._projet(id_projet)
        deduits = deductions(choix)
        suivante = question_suivante([*choix, *deduits])
        return {
            "question": suivante.to_dict() if suivante is not None else None,
            "deductions": [c.to_dict() for c in deduits],
            "terminee": suivante is None,
        }

    def recommandation(self, id_projet: str, choix: Sequence[Choix]) -> dict[str, Any]:
        """L'outillage que ces réponses recommandent — **la forme de l'analyse** (#1031).

        La même `Recommandation` que `analyser` rend, produite par la **même**
        fonction (`maestro.outillage.recommandation.recommander`) : c'est le second
        critère du ticket, et il se tient en n'ayant qu'un seul chemin plutôt qu'en
        gardant deux chemins d'accord.

        Rendue à **tout moment**, questionnaire fini ou non : un client qui veut
        montrer ce qui se dessine au fil des réponses n'a pas à attendre la dernière.
        Ce qui n'a pas été répondu ne justifie simplement aucune entrée — et
        `recommander` le dit, en écartant le skill correspondant avec sa raison.

        `source` est le fragment de provenance du manifeste (docs/38 §4.1), le jumeau
        de celui qu'`Analyse.source_manifeste()` rend : c'est lui qui dira, six mois
        plus tard, que cet outillage vient de réponses et lesquelles.
        """
        projet = self._projet(id_projet)
        acquis = [*choix, *deductions(choix)]
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
