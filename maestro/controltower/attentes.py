"""Le service qui **règle** ce qui attend quelqu'un — questions d'agent et validations (#1183).

Deux routes réglaient deux files, chacune avec ses règles écrites dans son corps :
`POST /api/questions/{id}/reponse` (#1023) et `POST /api/validations/{tache}/decision`
(#48). Le fil de l'orchestrateur règle désormais les mêmes attentes, et deux
formulations de « cette question peut-elle encore recevoir une réponse ? » finiraient
par répondre différemment à l'écran et à la phrase. Les règles vivent donc **ici**,
une fois — les routes et le fil les appellent, aucun ne les recopie : c'est le partage
de `ServiceExecutions.refus_du_geste` (#1179), appliqué à ces deux files.

Le service fait trois choses, dans l'ordre des routes d'avant lui :

1. **vérifier** que l'attente existe et attend encore (`refus_du_reglement`) — le
   fil le demande **avant** de poser sa carte, les routes au moment d'agir ;
2. **appliquer** l'événement à la projection, pour que ce qu'on rend soit déjà l'état
   d'après ;
3. le **publier** sur le bus, où le moteur attend : l'agent suspendu sur sa question
   la lit et reprend (`ArbitreQuestionControlTower`), la tâche retenue par sa
   validation repart ou s'arrête (`ValidateurControlTower`).

## La raison d'un refus ne s'écrit qu'ici

Le motif d'un refus (#272) voyage dans le `detail` de l'événement de décision, et la
projection le recopie dans `decision` : c'est ce que l'écran relit et ce que le
journal durable garde. `detail_de_la_decision` le compose, **une fois** : un refus
tranché depuis le fil porte donc exactement la raison qu'un refus motivé depuis
l'écran des validations.

Depuis #1185 il voyage **aussi** dans un champ à lui (`Event.motif`) : c'est une
**consigne**, que le moteur rend à l'agent pour qu'il replanifie son geste au lieu
d'abandonner. `detail` reste la phrase qu'on affiche ; le champ est le fait qu'on
transmet, et personne n'a à le retrouver dans la phrase.

## Une approbation peut valoir pour la suite

L'**étendue** d'une approbation (#1185, `maestro.decision_humaine`) — cet appel, ou
l'outil pour la suite du run ou du projet — est un choix de la personne, admis ici
et **seulement pour un acte** : une demande sans outil n'a rien à ne plus redemander.
Un accord étendu s'écrit dans les permissions de l'agent (`maestro.agents.accords`)
**avant** que la décision parte : l'appel suivant de l'agent le trouve déjà.

## Ce qui reprend, dit depuis l'attente

Ce qu'un règlement fait au travail n'est pas le même d'une attente à l'autre, et le
fil le dit (`ReglementFait.suite`) — sans jamais le deviner d'un texte :

- une **réponse** fait reprendre l'agent qui l'attend ; passée l'échéance de sa
  question (#1025), il est déjà reparti sur son hypothèse, et la réponse le
  rattrapera au prochain appel identique (`MemoireArbitrage`, #584) ;
- une **approbation** fait reprendre la tâche — l'appel s'exécute pour un acte, le
  travail s'écrit pour une demande d'écriture dans le projet (#227) ;
- un **refus** écarte l'acte, et l'agent poursuit sa tâche sans lui
  (`motif_refus`) ; pour une demande d'écriture, rien n'est écrit ; ailleurs,
  l'action demandée n'a pas lieu. Avec une **consigne** (#1185), l'agent repart
  d'elle et sa nouvelle action sera soumise à son tour ;
- sur un run **déjà soldé**, rien ne reprend : la réponse ou la décision est
  consignée, et le fil le dit ainsi (vu sur la vraie stack, où un refus tranché après
  la fin du run annonçait encore que l'agent poursuivait sa tâche).

La distinction se lit sur la **structure** de la demande — un outil, un diff —,
jamais sur sa raison, qui est du texte.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from maestro.agents.accords import AccordStore
from maestro.controltower.events import (
    EVENEMENT_QUESTION_REPONSE,
    EVENEMENT_VALIDATION_DECISION,
    Event,
    EventBus,
)
from maestro.controltower.reglements import (
    GENRE_DU_REGLEMENT,
    GENRE_QUESTION,
    GENRE_VALIDATION,
    MOTIF_ATTENTE_INCONNUE,
    MOTIF_ATTENTE_REGLEE,
    MOTIF_ETENDUE_REFUSEE,
    MOTIF_REPONSE_VIDE,
    REGLEMENT_APPROBATION,
    REGLEMENT_REFUS,
    REGLEMENT_REPONSE,
    REGLEMENTS,
    AttenteVisee,
    ReglementFait,
    ReglementRefuse,
)
from maestro.controltower.state import (
    QUESTION_REPONDUE,
    QUESTION_RETIREE,
    STATUTS_EXECUTION_TERMINAUX,
    VALIDATION_APPROUVEE,
    VALIDATION_REFUSEE,
    ControlTowerState,
    EtatQuestion,
    EtatValidation,
)
from maestro.decision_humaine import (
    ETENDUE_APPEL,
    ETENDUE_PROJET,
    ETENDUE_RUN,
    ETENDUES,
    ETENDUES_DURABLES,
    portee_de_l_etendue,
)

#: Combien d'un objet (question, acte, description) une carte recopie. La lecture du
#: juge a la même borne (`orchestration._ATTENTE_MAX`) : la carte ne montre pas plus
#: que ce que le modèle a lu pour la proposer.
_OBJET_MAX = 400

#: Ce que l'écran dit d'une décision déjà rendue — les libellés de l'écran des
#: validations, jamais le code de la machine à états (la règle de #571).
_LIBELLES_DECISION = {
    VALIDATION_APPROUVEE: "approuvée",
    VALIDATION_REFUSEE: "refusée",
}

#: Ce qu'une réponse vide ferait à l'agent — la phrase du `422` de la route (#1023).
_PHRASE_REPONSE_VIDE = (
    "une réponse vide n'apprend rien à l'agent : écrivez ce que vous lui répondez, "
    "ou laissez-le reprendre sur son hypothèse."
)


def detail_de_la_decision(
    approuve: bool, motif: str = "", etendue: str = ETENDUE_APPEL, outil: str = ""
) -> str:
    """Le `detail` d'une décision de validation — là où voyage la raison d'un refus (#272).

    La **seule** composition de ce texte : la route de l'écran des validations et le
    fil la partagent, si bien qu'un refus motivé porte la même raison, au caractère
    près, d'où qu'il vienne. Vide ou absent, le motif laisse la phrase d'avant #272.

    Une approbation **étendue** (#1185) le dit — c'est la ligne que « Déjà
    tranchées » et le journal garderont d'un accord qui dépasse l'appel. À l'unité,
    la phrase est celle d'avant, au caractère près.
    """
    if approuve:
        if etendue in ETENDUES_DURABLES and outil:
            return (
                "approuvée depuis la Control Tower — et pour "
                f"{portee_de_l_etendue(etendue, outil)}"
            )
        return "approuvée depuis la Control Tower"
    motif = motif.strip()
    if motif:
        return f"refusée depuis la Control Tower — {motif}"
    return "refusée depuis la Control Tower"


def _borne(texte: str) -> str:
    """Un objet ramené à `_OBJET_MAX`, sur une ligne, coupé **en le disant**."""
    propre = " ".join(texte.split())
    if len(propre) <= _OBJET_MAX:
        return propre
    return f"{propre[:_OBJET_MAX].rstrip()}… (tronqué)"


def question_visee(question: EtatQuestion) -> AttenteVisee:
    """La question d'un agent telle qu'une carte du fil la montre."""
    return AttenteVisee(
        genre=GENRE_QUESTION,
        identifiant=question.question_id,
        agent=question.agent,
        role=question.role,
        titre=question.titre,
        objet=_borne(question.question),
        run_id=question.run_id,
        hypothese=_borne(question.hypothese),
        echeance=question.echeance,
    )


def validation_visee(validation: EtatValidation) -> AttenteVisee:
    """Une validation telle qu'une carte du fil la montre — l'acte en tête (#573).

    L'acte quand la demande en porte un (l'outil et ses arguments, déjà expurgés par
    `evenement_demande`), sinon ce qu'elle ferait, sinon le titre de sa tâche : l'ordre
    de l'écran des validations (`CarteValidation`), qui n'affiche jamais « Rédiger le
    README » au-dessus d'un `rm -rf`.
    """
    if validation.outil:
        arguments = " ".join(
            f"{cle}={valeur}" for cle, valeur in (validation.arguments or {}).items()
        )
        objet = f"{validation.outil} {arguments}".strip()
    else:
        objet = validation.description or validation.titre
    return AttenteVisee(
        genre=GENRE_VALIDATION,
        identifiant=validation.tache_id,
        agent=validation.agent,
        role=validation.role,
        titre=validation.titre,
        objet=_borne(objet),
        run_id=validation.run_id,
        outil=validation.outil,
    )


class ServiceAttentes:
    """Les règles d'une réponse à une question et d'une décision sur une validation.

    `state` est la projection que les routes lisent, `bus` celui où le moteur attend.
    `horloge` rend l'instant présent — injectable, pour qu'un test dise « l'échéance
    est passée » sans attendre qu'elle le soit.

    `accords` (#1185) rend le dépôt des accords étendus d'un projet (`None` : les
    gabarits) — là où une approbation étendue s'écrit. Sans lui, une étendue durable
    est refusée : un accord qu'on ne peut pas écrire serait un oui qu'on redemande
    au prochain appel, c'est-à-dire une promesse que l'écran ne tiendrait pas.
    """

    def __init__(
        self,
        state: ControlTowerState,
        bus: EventBus,
        *,
        horloge: Callable[[], datetime] | None = None,
        accords: Callable[[str | None], AccordStore] | None = None,
    ) -> None:
        self._state = state
        self._bus = bus
        self._horloge = horloge or (lambda: datetime.now(UTC))
        self._accords = accords

    # ── Lire ───────────────────────────────────────────────────────────────────

    def visee(self, genre: str, identifiant: str) -> AttenteVisee | None:
        """L'attente d'un genre et d'un identifiant, telle qu'une carte la montre.

        `None` quand aucune ne porte cet identifiant dans cette file — qu'elle attende
        encore ou non : c'est `refus_du_reglement` qui dit si elle se règle.
        """
        if genre == GENRE_QUESTION:
            question = self._state.question(identifiant)
            return question_visee(question) if question is not None else None
        if genre == GENRE_VALIDATION:
            validation = self._state.validation(identifiant)
            return validation_visee(validation) if validation is not None else None
        return None

    # ── Vérifier ───────────────────────────────────────────────────────────────

    def refus_de_la_reponse(self, question_id: str, reponse: str) -> ReglementRefuse | None:
        """Le refus qu'une réponse à cette question recevrait maintenant, `None` si elle passe.

        `404` si aucune question ne porte cet identifiant ; `409` si elle a déjà reçu sa
        réponse — l'agent n'a lu que la première ; `422` si la réponse est vide, ce qui
        dirait à l'agent qu'on lui a répondu sans rien lui apprendre (#1023).
        """
        question = self._state.question(question_id)
        if question is None:
            return ReglementRefuse(
                MOTIF_ATTENTE_INCONNUE,
                f"aucune question d'agent ne porte l'identifiant {question_id}.",
            )
        if question.statut == QUESTION_RETIREE:
            # Une proposition du run passée sa borne, ou dont le run est fini (#1298) :
            # y répondre ne ferait plus rien, et le dire vaut mieux que d'accepter un
            # geste que personne n'écoute.
            return ReglementRefuse(
                MOTIF_ATTENTE_REGLEE,
                "cette question a été retirée sans réponse, elle ne vaut plus : le run "
                "qui la posait ne l'attend plus.",
            )
        if not question.en_attente:
            return ReglementRefuse(
                MOTIF_ATTENTE_REGLEE,
                f"cette question a déjà reçu sa réponse : « {_borne(question.reponse)} ».",
            )
        if not reponse.strip():
            return ReglementRefuse(MOTIF_REPONSE_VIDE, _PHRASE_REPONSE_VIDE)
        return None

    def refus_de_la_decision(self, tache_id: str) -> ReglementRefuse | None:
        """Le refus qu'une décision sur cette validation recevrait maintenant, `None` si elle passe.

        `404` si aucune demande ne porte sur cette tâche ; `409` si elle est déjà
        tranchée — jamais deux décisions, le moteur n'ayant lu que la première (#48).
        """
        validation = self._state.validation(tache_id)
        if validation is None:
            return ReglementRefuse(
                MOTIF_ATTENTE_INCONNUE,
                f"aucune demande de validation ne porte sur la tâche {tache_id}.",
            )
        if not validation.en_attente:
            libelle = _LIBELLES_DECISION.get(validation.statut, validation.statut)
            return ReglementRefuse(
                MOTIF_ATTENTE_REGLEE, f"cette demande a déjà été tranchée — {libelle}."
            )
        return None

    def refus_de_l_etendue(
        self, validation: EtatValidation, etendue: str
    ) -> ReglementRefuse | None:
        """Le refus qu'une approbation **étendue** recevrait, `None` si elle passe (#1185).

        `422` à chaque fois, et chacun dit ce qui manque : une étendue inconnue ; une
        demande qui ne porte pas d'**acte** (une tâche, un diff — il n'y a pas d'outil
        à ne plus redemander) ; un run introuvable pour un accord de run, un projet
        pour un accord de projet ; un dépôt d'accords absent.
        """
        if etendue not in ETENDUES:
            return ReglementRefuse(
                MOTIF_ETENDUE_REFUSEE,
                f"étendue inconnue : {etendue!r} ({', '.join(ETENDUES)}).",
            )
        if etendue not in ETENDUES_DURABLES:
            return None
        if not validation.outil:
            return ReglementRefuse(
                MOTIF_ETENDUE_REFUSEE,
                "cette demande ne porte pas d'acte : il n'y a pas d'outil à ne plus "
                "redemander — elle s'approuve pour elle seule.",
            )
        if etendue == ETENDUE_RUN and not validation.run_id:
            return ReglementRefuse(
                MOTIF_ETENDUE_REFUSEE,
                "cette demande ne relève d'aucun run : l'accord ne peut pas valoir pour "
                "la suite du run.",
            )
        if etendue == ETENDUE_PROJET and not validation.projet_id:
            return ReglementRefuse(
                MOTIF_ETENDUE_REFUSEE,
                "cette demande ne relève d'aucun projet : l'accord ne peut pas valoir "
                "pour le projet.",
            )
        if self._accords is None:
            return ReglementRefuse(
                MOTIF_ETENDUE_REFUSEE,
                "aucun dépôt d'accords n'est câblé sur cette Control Tower : l'accord ne "
                "pourrait pas être gardé.",
            )
        return None

    def suite(self, action: str, identifiant: str, texte: str = "") -> str:
        """Ce que ce règlement fera, dit **avant** qu'il parte — `""` si rien ne le dit.

        La phrase que la carte du fil montre sous la question, et celle que le fait
        redira une fois le règlement parti (`regler`) : une seule rédaction de « ce qui
        va se passer », au service qui le fera. `texte` est la raison d'un refus, qui
        revient à l'agent comme consigne (#1185) : la carte le dit comme le fait le
        redira.
        """
        if action == REGLEMENT_REPONSE:
            question = self._state.question(identifiant)
            return self._suite_de_la_reponse(question) if question is not None else ""
        if action not in (REGLEMENT_APPROBATION, REGLEMENT_REFUS):
            return ""
        validation = self._state.validation(identifiant)
        if validation is None:
            return ""
        approuve = action == REGLEMENT_APPROBATION
        return self._suite_de_la_decision(
            validation, approuve=approuve, motif="" if approuve else texte
        )

    def refus_du_reglement(
        self, action: str, identifiant: str, texte: str = ""
    ) -> ReglementRefuse | None:
        """Le refus qu'un règlement recevrait maintenant, selon sa file — `None` s'il passe."""
        if action == REGLEMENT_REPONSE:
            return self.refus_de_la_reponse(identifiant, texte)
        if action in (REGLEMENT_APPROBATION, REGLEMENT_REFUS):
            return self.refus_de_la_decision(identifiant)
        return ReglementRefuse(
            MOTIF_ATTENTE_INCONNUE, f"règlement inconnu : {action!r} ({', '.join(REGLEMENTS)})."
        )

    # ── Régler ─────────────────────────────────────────────────────────────────

    async def repondre(self, question_id: str, reponse: str) -> EtatQuestion:
        """Répond à la question d'un agent : l'état d'abord, le bus ensuite (#1023).

        Lève `ReglementRefuse` sur une question inconnue, déjà répondue, ou une
        réponse vide. La réponse voyage dans `detail`, rognée, et nulle part ailleurs.

        ⚠ **Répondre n'approuve rien** : le texte n'autorise aucun acte, et la file des
        validations est le seul endroit où un acte se tranche (EF-08, docs/32 §5).
        """
        refus = self.refus_de_la_reponse(question_id, reponse)
        if refus is not None:
            raise refus
        question = self._state.question(question_id)
        assert question is not None  # vérifiée par `refus_de_la_reponse`
        event = Event(
            type=EVENEMENT_QUESTION_REPONSE,
            # Le run et la tâche sont recopiés depuis la question projetée : c'est
            # `question.demande` qui les porte, et une seule source vaut mieux que
            # deux — celle qui a posé la question.
            run_id=question.run_id,
            tache_id=question.tache_id,
            titre=question.titre,
            agent=question.agent,
            role=question.role,
            statut=QUESTION_REPONDUE,
            detail=reponse.strip(),
            projet_id=question.projet_id,
            question_id=question_id,
        )
        # `appliquer` met à jour la question **en place** : ce qu'on rend est donc
        # déjà l'état d'après.
        self._state.appliquer(event)
        await self._bus.publish(event)
        return question

    async def trancher(
        self,
        tache_id: str,
        *,
        approuve: bool,
        motif: str = "",
        etendue: str = ETENDUE_APPEL,
    ) -> EtatValidation:
        """Tranche une demande de validation : l'accord, l'état, puis le bus (#48).

        Lève `ReglementRefuse` sur une demande inconnue ou déjà tranchée, ou sur une
        étendue que la demande ne permet pas (`refus_de_l_etendue`). Le `motif` d'un
        refus est une **consigne** (#1185) : il voyage dans le `detail`
        (`detail_de_la_decision`) et dans `Event.motif`, que le moteur rend à
        l'agent ; il est ignoré sur une approbation. L'`etendue` est ignorée sur un
        refus.

        Une approbation étendue **écrit son accord d'abord** — dans les permissions
        de l'agent, au projet de la demande —, puis publie : le moteur, qui reprend
        à la décision, trouvera l'accord dès l'appel suivant. Dans l'autre ordre, un
        agent rapide redemanderait ce qu'on vient d'accorder.
        """
        refus = self.refus_de_la_decision(tache_id)
        if refus is not None:
            raise refus
        demande = self._state.validation(tache_id)
        assert demande is not None  # vérifiée par `refus_de_la_decision`
        etendue = etendue if approuve else ETENDUE_APPEL
        refus = self.refus_de_l_etendue(demande, etendue)
        if refus is not None:
            raise refus
        consigne = "" if approuve else motif.strip()
        if etendue in ETENDUES_DURABLES:
            assert self._accords is not None  # vérifié par `refus_de_l_etendue`
            try:
                self._accords(demande.projet_id).accorder(
                    agent=demande.agent,
                    outil=demande.outil,
                    etendue=etendue,
                    run_id=demande.run_id,
                    tache_id=tache_id,
                )
            except ValueError as exc:
                raise ReglementRefuse(MOTIF_ETENDUE_REFUSEE, str(exc)) from exc
        event = Event(
            type=EVENEMENT_VALIDATION_DECISION,
            tache_id=tache_id,
            titre=demande.titre,
            agent=demande.agent,
            role=demande.role,
            statut=VALIDATION_APPROUVEE if approuve else VALIDATION_REFUSEE,
            detail=detail_de_la_decision(approuve, consigne, etendue, demande.outil),
            # Le projet de la validation (#277), recollé par la projection depuis sa
            # tâche : la décision doit atteindre le flux du projet où elle se joue.
            projet_id=demande.projet_id,
            # Les faits que le moteur lit (#1185) — jamais retrouvés dans `detail`.
            motif=consigne,
            etendue=etendue if etendue in ETENDUES_DURABLES else "",
        )
        self._state.appliquer(event)
        await self._bus.publish(event)
        return demande

    async def regler(self, action: str, identifiant: str, texte: str = "") -> ReglementFait:
        """Règle une attente — répondre, approuver, refuser — et dit ce qui en sort.

        Le chemin du fil (#1183) : les deux verbes ci-dessus, derrière un seul nom, et
        ce qui a repris en mots (`ReglementFait.suite`). Lève `ReglementRefuse` comme
        eux ; une action hors des trois est refusée sans rien toucher.
        """
        genre = GENRE_DU_REGLEMENT.get(action)
        if genre is None:
            refus = self.refus_du_reglement(action, identifiant, texte)
            assert refus is not None
            raise refus
        if action == REGLEMENT_REPONSE:
            question = await self.repondre(identifiant, texte)
            return ReglementFait(
                action,
                attente=question_visee(question),
                texte=question.reponse,
                suite=self._suite_de_la_reponse(question),
            )
        approuve = action == REGLEMENT_APPROBATION
        motif = "" if approuve else texte.strip()
        validation = await self.trancher(identifiant, approuve=approuve, motif=motif)
        return ReglementFait(
            action,
            attente=validation_visee(validation),
            texte=motif,
            suite=self._suite_de_la_decision(validation, approuve=approuve, motif=motif),
        )

    def _run_solde(self, run_id: str) -> bool:
        """Le run de l'attente a-t-il rendu son issue ? `False` s'il est inconnu.

        Une attente reste servie après son run — une réponse tardive sert encore
        (#584) —, mais ce qu'un règlement en fait n'est plus une reprise. Vu sur la vraie
        stack : un refus tranché après la fin du run disait « l'agent poursuit sa tâche ».
        """
        execution = self._state.execution(run_id) if run_id else None
        return execution is not None and execution.statut in STATUTS_EXECUTION_TERMINAUX

    def _suite_de_la_decision(
        self, validation: EtatValidation, *, approuve: bool, motif: str = ""
    ) -> str:
        """Ce qu'une décision fait au travail — rien, si son run est déjà soldé."""
        if self._run_solde(validation.run_id):
            consigne = "l'approbation est consignée" if approuve else "le refus est consigné"
            return f"son run est déjà soldé : {consigne}, et rien ne reprend"
        return suite_de_la_decision(validation, approuve=approuve, motif=motif)

    def _suite_de_la_reponse(self, question: EtatQuestion) -> str:
        """Ce qu'une réponse fait à l'agent — il reprend, ou elle le rattrapera (#1025)."""
        if self._run_solde(question.run_id):
            return (
                "son run est déjà soldé : la réponse est consignée, et plus aucun agent ne "
                "l'attend"
            )
        if _echue(question.echeance, self._horloge()):
            hypothese = f" (« {_borne(question.hypothese)} »)" if question.hypothese else ""
            return (
                f"l'agent était déjà reparti sur son hypothèse{hypothese} : il retrouvera "
                "cette réponse au prochain appel identique"
            )
        return "l'agent attendait cette réponse : il la lit et reprend sa tâche"


def suite_de_la_decision(
    validation: EtatValidation, *, approuve: bool, motif: str = ""
) -> str:
    """Ce qu'une décision fait au travail, lu sur la **structure** de la demande.

    Un outil : un acte, arbitré au moment où il a lieu (#583) — refusé, l'agent
    poursuit sans lui (`motif_refus`). Un diff : une écriture dans le projet (#227) —
    refusée, rien n'est écrit et le travail reste consultable. Ailleurs, une tâche ou
    une action que l'agent a lui-même soumise : refusée, elle n'a pas lieu.

    Un refus **avec consigne** (#1185) ne dit plus « sans lui » : l'agent — ou la
    tâche, pour une validation de tâche — repart de ce qu'on lui a dit, et ce qu'il
    en tire revient à la personne s'il le demande. Pour un diff, la consigne est
    consignée mais l'écriture n'a pas d'autre forme à prendre.
    """
    consigne = "" if approuve else motif.strip()
    if validation.outil:
        if approuve:
            return "l'appel s'exécute et la tâche reprend"
        if consigne:
            return (
                "l'appel est écarté : l'agent reçoit votre consigne et replanifie son "
                "geste — sa nouvelle action vous sera soumise si elle le demande"
            )
        return "l'appel est écarté : l'agent poursuit sa tâche sans lui"
    if validation.diff is not None:
        if approuve:
            return "le travail s'écrit dans le projet"
        return "rien n'est écrit dans le projet, et le travail reste consultable"
    if approuve:
        return "la tâche reprend"
    if consigne:
        return (
            "l'action n'a pas lieu telle quelle : votre consigne revient à l'agent, qui "
            "repart d'elle — ce qu'il en tire vous sera soumis s'il le demande"
        )
    return "l'action demandée n'aura pas lieu"


def _echue(echeance: str, maintenant: datetime) -> bool:
    """L'échéance d'une question est-elle passée ? `False` si elle est vide ou illisible.

    La règle de `questionEchue` (`apps/web/lib/questions.ts`) : sans date lisible, on
    ne dit pas que l'agent est reparti — ce serait affirmer un fait qu'on ignore.
    """
    if not echeance:
        return False
    try:
        borne = datetime.fromisoformat(echeance)
    except ValueError:
        return False
    if borne.tzinfo is None:
        borne = borne.replace(tzinfo=UTC)
    return maintenant >= borne
