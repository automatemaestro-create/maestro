"""Les routes du fil : ses conversations, ses messages, ses gestes et ses réponses en flux.

Les gestes se tranchent dans la conversation : le cadrage, le recrutement, la déclaration d'un
projet, le geste sur un run, le règlement et les pièces de l'outillage."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from maestro.agents.catalog import Agent
from maestro.agents.configuration import ConfigurationAgents
from maestro.appartenance import projet_id_valide
from maestro.controltower.assistance import AGENT_ASSISTANCE, NOM_ASSISTANCE
from maestro.controltower.chat import (
    CadrageIntrouvable,
    ChatStore,
    DeclarationIntrouvable,
    GesteRunIntrouvable,
    PieceIntrouvable,
    QuestionIntrouvable,
    RecrutementIntrouvable,
    ReglementIntrouvable,
    RepondeurModele,
    ReponseIndisponible,
    ServiceChat,
    recrutement_en_attente,
)
from maestro.controltower.events import EVENEMENT_RENFORT_DECISION, Event
from maestro.controltower.orchestration import AGENT_ORCHESTRATION, NOM_ORCHESTRATION
from maestro.controltower.routes.communs import _config, _detail_refus, _exige_agent_du_catalogue
from maestro.controltower.routes.contexte import Contexte, Ctx
from maestro.controltower.routes.requetes import (
    CadrageDecisionRequete,
    ChatEnvoiRequete,
    DecisionPieceRequete,
    DeclarationProjetRequete,
    GesteRunRequete,
    RecrutementDecisionRequete,
    ReglementRequete,
    ReponseOutillageRequete,
)
from maestro.controltower.state import RENFORT_ACCORDE, RENFORT_DECLINE
from maestro.sources import SourceRefusee

router = APIRouter()


def _canal_chat(ctx: Contexte, nom: str, projet: str | None = None) -> tuple[Agent, ServiceChat]:
    """La fiche et le service du fil `nom` — agent, assistant (#123), orchestration (#268).

    Les trois canaux se servent des mêmes endpoints : `assistance` résout sur
    une fiche hors catalogue et sur son propre `ServiceChat`, `orchestrateur`
    de même, tout autre nom sur l'agent du catalogue et le chat ordinaire.
    L'UI n'a donc qu'un seul contrat REST à connaître, le nom du fil départage
    — et les deux noms système sont **réservés** côté catalogue
    (`maestro.agents.store.NOMS_RESERVES`), donc aucun agent personnalisé ne
    peut venir prendre l'un de ces fils.
    """
    assistance = ctx.assistance
    chat = ctx.chat
    orchestration = ctx.orchestration
    if nom == NOM_ASSISTANCE:
        return AGENT_ASSISTANCE, assistance
    if nom == NOM_ORCHESTRATION:
        return AGENT_ORCHESTRATION, orchestration
    if projet is None:
        return _exige_agent_du_catalogue(ctx, nom), chat
    # L'agent **d'une équipe de projet** (#1175) : résolu dans la
    # configuration de ce projet, comme toutes les routes d'agent (#1038).
    # Sans elle, `developpeur-2` rendrait 404 et un `developpeur` de projet
    # parlerait avec la fiche du gabarit homonyme — playbook, compétences et
    # serveurs d'un autre agent, sans que rien ne le signale.
    cfg = _config(ctx, projet)
    return _exige_agent_du_catalogue(ctx, nom, cfg), _chat_du_projet(ctx, cfg)



def _chat_du_projet(ctx: Contexte, cfg: ConfigurationAgents) -> ServiceChat:
    """Le chat des agents de `cfg` : leurs fils, et leurs playbooks à eux.

    Le **dépôt** est propre au projet — deux projets ont chacun leur
    `developpeur`, et leurs conversations ne se mêlent pas. Il vit sous
    `_projets/` dans la racine du chat : un nom qu'aucun agent ne peut porter
    (un nom d'agent commence par une lettre ou un chiffre), donc jamais pris
    pour le dossier de conversations d'un agent. Le **répondeur** lit les
    playbooks du projet, pour la raison qui fait résoudre la fiche dans le
    projet : un playbook édité du gabarit n'est pas celui de l'agent.
    """
    bus = ctx.bus
    chat = ctx.chat
    chat_repondeur = ctx.chat_repondeur
    chat_store = ctx.chat_store
    chats_de_projet = ctx.chats_de_projet
    mailbox = ctx.mailbox
    televersements = ctx.televersements
    if cfg.projet_id is None:
        return chat
    service = chats_de_projet.get(cfg.projet_id)
    if service is None:
        service = ServiceChat(
            store=ChatStore(chat_store.racine / "_projets" / cfg.projet_id),
            repondeur=(
                chat_repondeur
                if chat_repondeur is not None
                else RepondeurModele(playbooks=cfg.playbooks)
            ),
            mailbox=mailbox,
            bus=bus,
            televersements=televersements,
        )
        chats_de_projet[cfg.projet_id] = service
    return service



def _conversation_demandee(
    service: ServiceChat, fiche: Agent, demandee: str | None
) -> str:
    """L'identifiant à servir : celui demandé, sinon la conversation courante (#694).

    Trois réponses, et elles ne se confondent pas : un identifiant **mal
    formé** est un 422 (la garde de traversée de chemin, note technique du
    ticket — même famille qu'un message vide), un identifiant **bien formé
    mais inconnu** est un 404 (on ne peut pas adresser un fil qui n'existe
    pas), et l'absence n'est pas une erreur du tout — c'est le cas nominal :
    la conversation courante.
    """
    if not demandee:
        return service.courante(fiche.nom)
    try:
        connue = service.existe(fiche.nom, demandee)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not connue:
        raise HTTPException(
            status_code=404,
            detail=(
                f"conversation inconnue : {demandee} "
                f"(voir GET /api/chat/{fiche.nom}/conversations)"
            ),
        )
    return demandee



@router.get("/api/chat/{agent}")
async def fil_chat(
    ctx: Ctx,
    agent: str, conversation: str | None = None, projet: str | None = None
) -> dict[str, Any]:
    """Le fil de conversation utilisateur ↔ agent (#84), relu de la persistance.

    Vide tant que l'agent n'a jamais été contacté ; 404 si l'agent n'est
    pas au catalogue. `assistance` (#123) désigne le canal d'aide.

    `?conversation=` désigne **laquelle** de ses conversations relire (#694) ;
    sans elle, la plus récente — donc, pour un agent qui n'en a qu'une, son
    fil entier. 404 si l'identifiant est inconnu, 422 s'il est
    mal formé. La réponse **nomme** la conversation servie, ce dont un client
    qui n'en a demandé aucune a besoin pour savoir dans laquelle il est.

    `?projet=` (#1175) désigne le projet dont l'agent est membre, comme sur
    toute route d'agent (#1038) : son équipe, sa fiche, ses conversations.
    Omis, le fil est celui du niveau des gabarits.
    """
    fiche, service = _canal_chat(ctx, agent, projet)
    fil = _conversation_demandee(service, fiche, conversation)
    return {
        "agent": fiche.nom,
        "role": fiche.role,
        "conversation": fil,
        "messages": [m.to_dict() for m in service.fil(fiche.nom, fil)],
    }



@router.get("/api/chat/{agent}/conversations")
async def conversations_chat(ctx: Ctx, agent: str, projet: str | None = None) -> dict[str, Any]:
    """Les conversations d'un fil, **la plus récente d'abord** (#694).

    De quoi peupler un historique sans charger un seul message : identifiant,
    titre dérivé du premier message, ouverture, dernière activité et nombre
    de messages. Jamais vide — un agent a toujours au moins la conversation
    `origine`, fût-elle vierge —, et la première de la liste est celle qu'un
    envoi sans précision rejoindrait. 404 si l'agent n'est pas au catalogue.
    `?projet=` (#1175) : comme `GET /api/chat/{agent}`.
    """
    fiche, service = _canal_chat(ctx, agent, projet)
    return {
        "agent": fiche.nom,
        "role": fiche.role,
        "conversations": [c.to_dict() for c in service.conversations(fiche.nom)],
    }



@router.post("/api/chat/{agent}/conversations", status_code=201)
async def ouvrir_conversation_chat(
    ctx: Ctx, agent: str, projet: str | None = None
) -> dict[str, Any]:
    """Ouvre une conversation neuve sur un fil et rend sa carte (#694).

    **Idempotent tant que rien n'a été dit** : si la plus récente est vierge,
    elle *est* la conversation neuve et c'est elle qui revient — deux clics
    sur « nouvelle conversation » ne laissent donc pas deux fils vides
    derrière eux. Le 201 vaut pour les deux cas : la conversation demandée
    existe et elle est prête, que l'appel l'ait créée ou reconnue.

    L'écrire ne rend pas l'ancienne moins accessible : elle reste listée et
    relisible par `?conversation=`. 404 si l'agent n'est pas au catalogue.
    `?projet=` (#1175) : comme `GET /api/chat/{agent}`.
    """
    fiche, service = _canal_chat(ctx, agent, projet)
    return {
        "agent": fiche.nom,
        "role": fiche.role,
        "conversation": service.ouvrir_conversation(fiche.nom).to_dict(),
    }



@router.post("/api/chat/{agent}/messages", status_code=201)
async def envoyer_chat(
    ctx: Ctx,
    agent: str, requete: ChatEnvoiRequete, projet: str | None = None
) -> dict[str, Any]:
    """Envoie un message utilisateur à l'agent et rend la paire message/réponse.

    Le message et la réponse sont persistés au fil, passés par la
    messagerie inter-agents (#44) et diffusés en `chat.message` sur le
    WebSocket — les clients temps réel voient le message dès l'envoi puis
    la réponse quand elle tombe. 404 si l'agent n'est pas au catalogue,
    422 sur un message vide, 502 si la réponse n'a pas pu être produite
    (le message utilisateur reste acquis : relancer ne perd pas le fil).
    `assistance` (#123) désigne le canal d'aide, qui répond sans modèle.

    Le message peut porter des **sources** (#482) : elles empruntent la chaîne
    d'ingestion du lancement — mêmes plafonds, mêmes racines interdites, mêmes
    motifs — et le message rendu porte leur **rapport de lecture** (#316). Un
    refus est un **422 motivé** (`{motif, message, index}`), la forme déjà
    servie par `POST /api/sources` et `POST /api/executions` : c'est ce qui
    permet à l'écran de l'afficher sur la source fautive. Un message **sans
    texte mais avec des sources** est accepté — le dépôt *est* le message.

    `conversation` (#694) range l'échange dans **un** des fils de l'agent ;
    absente, il rejoint la plus récente. La réponse la nomme, et message
    comme réponse la portent : les deux moitiés d'un tour vont ensemble.
    `?projet=` (#1175) : comme `GET /api/chat/{agent}`.
    """
    fiche, service = _canal_chat(ctx, agent, projet)
    fil = _conversation_demandee(service, fiche, requete.conversation)
    try:
        message, reponse = await service.envoyer(
            fiche,
            requete.contenu,
            requete.sources,
            # Normalisé **ici**, à la frontière, comme tout ce qui entre
            # (#222) : un identifiant mal formé vaut « aucun projet » et ne
            # fait pas échouer un message. En dessous, plus personne n'a à se
            # demander d'où vient la chaîne qu'il porte.
            projet_id=projet_id_valide(requete.projet_id),
            conversation=fil,
        )
    except SourceRefusee as exc:
        # Avant le `ValueError` nu dont elle hérite : une source refusée porte
        # un motif et un index, et les aplatir en chaîne perdrait l'endroit où
        # l'écran doit afficher le refus.
        raise HTTPException(status_code=422, detail=_detail_refus(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReponseIndisponible as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "agent": fiche.nom,
        "role": fiche.role,
        "conversation": fil,
        "messages": [message.to_dict(), reponse.to_dict()],
    }



@router.post("/api/chat/{agent}/cadrage", status_code=201)
async def trancher_cadrage_chat(
    ctx: Ctx,
    agent: str, requete: CadrageDecisionRequete
) -> dict[str, Any]:
    """Tranche la demande de cadrage que le fil porte, et rend la paire (#943).

    Le **geste** qui répond à une proposition de l'orchestration — « Je
    lance ? » — sans repasser par la zone de saisie : `approuve` dit oui ou
    non, `objectif` porte la version corrigée ou `null` pour « la
    proposition tient ». Même forme et même réponse que
    `POST …/messages` — l'acte est écrit au fil, la réponse suit —, parce
    que c'est bien un tour de conversation : ce qui change est que la
    décision vient d'un clic.

    Les quatre **garde-fous** (#990) voyagent avec l'accord : c'est le même
    geste qui dit « lance » et « jusque-là ». Ils ne sont pas jugés ici —
    `lancer` refuse une valeur hors bornes, et son refus se raconte dans le
    fil plutôt que de remonter en statut, parce que le geste, lui, a bien eu
    lieu et reste acquis au fil.

    `409` quand rien n'attend : aucune proposition, une déjà tranchée, ou un
    fil dont le répondeur n'en fait pas — c'est le `409` de §6.10 un cran
    plus tôt, et il couvre le double geste comme le geste tardif. `404` hors
    catalogue, `422` sur une conversation mal formée, `502` si la décision
    n'a pas pu être exécutée (le geste, lui, reste acquis au fil).
    """
    fiche, service = _canal_chat(ctx, agent)
    fil = _conversation_demandee(service, fiche, requete.conversation)
    try:
        geste, reponse = await service.trancher_cadrage(
            fiche,
            approuve=requete.approuve,
            objectif=requete.objectif,
            # Normalisé **ici**, à la frontière, comme sur un envoi : c'est
            # le projet de la fenêtre. Il ne rattache le run que si la
            # proposition n'a pas de projet à elle (sans `projet_vise`, #1180).
            projet_id=projet_id_valide(requete.projet_id),
            bornes=requete.bornes_posees(),
            conversation=fil,
        )
    except CadrageIntrouvable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReponseIndisponible as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "agent": fiche.nom,
        "role": fiche.role,
        "conversation": fil,
        "messages": [geste.to_dict(), reponse.to_dict()],
    }



@router.post("/api/chat/{agent}/recrutement", status_code=201)
async def recruter_chat(
    ctx: Ctx,
    agent: str, requete: RecrutementDecisionRequete
) -> dict[str, Any]:
    """Valide — ou décline — l'équipe que le fil propose, et rend la paire (#1146).

    Le geste qui répond à « ce projet n'a encore aucun agent » : `approuve`
    dit oui ou non, `roles` porte l'équipe **retenue** telle que l'écran l'a
    montrée — la forme de `POST /api/projets/{id}/equipe`, parce que c'est la
    même création (#1040). Le projet et le travail à reprendre ne sont pas
    dans le corps : ils sont sur la demande que le fil porte, et c'est elle
    qui fait foi.

    Même forme et même réponse que `POST …/cadrage` — l'acte est écrit au
    fil, la réponse suit —, et même traitement d'une équipe refusée par la
    création : elle se **raconte** dans le fil (rien n'a été créé, la demande
    reste posée), parce que le geste, lui, a bien eu lieu.

    ⚠ **Un run peut attendre cette décision** (#1227) : quand la demande porte
    un `run_id`, elle a été posée par un run suspendu entre son plan et sa
    première tâche, et la décision lui est publiée sur le bus
    (`renfort.decision`) juste après l'écriture au fil. L'ordre compte et c'est
    le même que partout ici — le fil d'abord, la diffusion ensuite : un run qui
    repartirait avant que sa trace soit écrite laisserait le fil en retard sur
    ce qui se passe. L'issue est **dérivée du fil** et non du corps de la
    requête : l'équipe est recrutée si la réponse porte l'équipe **créée**
    (`MessageChat.equipe`, #1262) — une création refusée repose la demande sans
    rien créer, et un run relancé sur une équipe qui n'existe pas serait le pire
    des deux mondes. Le `detail` de la décision est ce fait-là, la composition,
    et non les mots de la réponse : il finit au journal du run, où l'on relit
    ce qui a été recruté, pas ce que l'orchestrateur en a dit.

    `422` sur une validation sans rôle (« ne pas recruter » se dit en
    déclinant) ou une conversation mal formée, `409` quand rien n'attend —
    le double clic ne crée pas deux équipes —, `404` hors catalogue, `502` si
    la suite n'a pas pu être produite.
    """
    bus = ctx.bus
    if requete.approuve and not requete.roles:
        raise HTTPException(
            status_code=422,
            detail=(
                "aucun rôle validé : ne pas recruter se dit en déclinant "
                "l'équipe, pas en validant une équipe vide."
            ),
        )
    fiche, service = _canal_chat(ctx, agent)
    fil = _conversation_demandee(service, fiche, requete.conversation)
    # La demande **avant** le geste : c'est elle qui dit si un run attend, et
    # le geste la solde (un message suit, donc `recrutement_en_attente` ne la
    # rendra plus). La lire après serait la lire trop tard.
    attente = recrutement_en_attente(service.fil(fiche.nom, fil))
    en_attente = attente.recrutement if attente is not None else None
    try:
        geste, reponse = await service.recruter(
            fiche,
            approuve=requete.approuve,
            roles=requete.roles_valides() if requete.approuve else (),
            proposition_id=requete.proposition_id,
            conversation=fil,
        )
    except RecrutementIntrouvable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReponseIndisponible as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if en_attente is not None and en_attente.pendant_un_run:
        creee = reponse.equipe
        await bus.publish(
            Event(
                type=EVENEMENT_RENFORT_DECISION,
                run_id=en_attente.run_id,
                projet_id=en_attente.projet_id,
                titre=en_attente.role,
                statut=RENFORT_ACCORDE if creee is not None else RENFORT_DECLINE,
                detail=f"recruté : {creee.composition()}" if creee is not None else "",
            )
        )
    return {
        "agent": fiche.nom,
        "role": fiche.role,
        "conversation": fil,
        "messages": [geste.to_dict(), reponse.to_dict()],
    }



@router.post("/api/chat/{agent}/projet", status_code=201)
async def declarer_projet_chat(
    ctx: Ctx,
    agent: str, requete: DeclarationProjetRequete
) -> dict[str, Any]:
    """Accepte — ou refuse — le projet que le fil propose, et rend la paire (#1294).

    Le geste qui fait **naître** un projet dans la conversation : nom, dossier
    et versionnement sont ceux de la proposition que le fil porte, déjà
    vérifiés, et c'est elle qui est déclarée — par le chemin de
    `POST /api/projets`, puis de `POST …/versionner` si la mise sous Git était
    proposée. La réponse porte ce qui a été déclaré (`projet_cree`) : c'est ce
    que l'écran lit pour ouvrir le projet.

    Même forme et même réponse que `POST …/cadrage`. Une déclaration refusée
    par le disque se **raconte** dans le fil (rien n'est créé, la proposition
    est reposée), parce que le geste, lui, a bien eu lieu.

    `409` quand rien n'attend — le double clic ne déclare pas deux projets —,
    `404` hors catalogue, `422` sur une conversation mal formée, `502` si la
    suite n'a pas pu être produite.
    """
    fiche, service = _canal_chat(ctx, agent)
    fil = _conversation_demandee(service, fiche, requete.conversation)
    try:
        geste, reponse = await service.declarer_projet(
            fiche, approuve=requete.approuve, conversation=fil
        )
    except DeclarationIntrouvable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReponseIndisponible as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "agent": fiche.nom,
        "role": fiche.role,
        "conversation": fil,
        "messages": [geste.to_dict(), reponse.to_dict()],
    }



@router.post("/api/chat/{agent}/geste", status_code=201)
async def trancher_geste_chat(ctx: Ctx, agent: str, requete: GesteRunRequete) -> dict[str, Any]:
    """Confirme — ou écarte — le geste sur un run que le fil propose ; rend la paire (#1179).

    Le geste qui **agit** sur un run depuis la conversation : suspendre,
    reprendre, annuler, relancer. L'action, le run et les bornes d'une relance
    sont ceux de la carte que le fil porte, relus du fil ; la confirmation les
    exécute par le service des boutons (`ServiceExecutions.agir`), puis relit
    le run — l'état relu voyage sur la réponse (`geste_fait`), et une relance y
    rattache le run qu'elle ouvre (`run_id`).

    Même forme et même réponse que `POST …/projet`. Un geste **refusé** par le
    service (l'état du run a changé depuis la carte) ne lève pas : il se
    raconte dans le fil, motivé, parce que la confirmation, elle, a bien eu lieu.

    `409` quand rien n'attend — le double clic n'annule pas deux fois, ne
    relance pas deux runs —, `404` hors catalogue, `422` sur une conversation
    mal formée, `502` si la suite n'a pas pu être produite.
    """
    fiche, service = _canal_chat(ctx, agent)
    fil = _conversation_demandee(service, fiche, requete.conversation)
    try:
        geste, reponse = await service.trancher_geste(
            fiche, approuve=requete.approuve, conversation=fil
        )
    except GesteRunIntrouvable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReponseIndisponible as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "agent": fiche.nom,
        "role": fiche.role,
        "conversation": fil,
        "messages": [geste.to_dict(), reponse.to_dict()],
    }



@router.post("/api/chat/{agent}/reglement", status_code=201)
async def trancher_reglement_chat(
    ctx: Ctx, agent: str, requete: ReglementRequete
) -> dict[str, Any]:
    """Confirme — ou écarte — le règlement que le fil propose ; rend la paire (#1183).

    Le geste qui **règle** depuis la conversation ce qui attend quelqu'un :
    répondre à la question d'un agent, approuver ou refuser une validation. La
    question ou la validation, la réponse et la raison d'un refus sont ceux de la
    carte que le fil porte, relus du fil ; la confirmation les passe au service
    des écrans (`ServiceAttentes`), et ce qui en sort voyage sur la réponse
    (`reglement_fait`) — ce qui a repris, ou le refus du service.

    Même forme et même réponse que `POST …/geste`. Un règlement **refusé** par le
    service (l'attente a été réglée ailleurs depuis la carte) ne lève pas : il se
    raconte dans le fil, motivé, parce que la confirmation, elle, a bien eu lieu.

    `409` quand rien n'attend — le double clic ne répond pas deux fois —, `404`
    hors catalogue, `422` sur une conversation mal formée, `502` si la suite n'a
    pas pu être produite.
    """
    fiche, service = _canal_chat(ctx, agent)
    fil = _conversation_demandee(service, fiche, requete.conversation)
    try:
        geste, reponse = await service.trancher_reglement(
            fiche, approuve=requete.approuve, conversation=fil
        )
    except ReglementIntrouvable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReponseIndisponible as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "agent": fiche.nom,
        "role": fiche.role,
        "conversation": fil,
        "messages": [geste.to_dict(), reponse.to_dict()],
    }



@router.post("/api/chat/{agent}/outillage/piece", status_code=201)
async def trancher_piece_chat(
    ctx: Ctx, agent: str, requete: DecisionPieceRequete
) -> dict[str, Any]:
    """Tranche d'un **geste** la pièce d'outillage que le fil propose (#1161).

    Le jumeau de `POST …/projet` sur la cinquième demande du canal, et la même
    réponse : l'acte est écrit au fil, la suite vient derrière — la pièce écrite
    (`piece_ecrite`, sous la bulle), puis la suivante, déjà vérifiée. La pièce
    visée n'est pas dans le corps : c'est celle qui attend, si bien qu'un double
    clic tombe sur le `409` au lieu d'écrire deux fois.

    `422` sur une décision inconnue, et sur l'écriture d'une version dont la
    **correction a échoué à l'exécution** — elle ne s'écrit pas, et le fil le
    dit. `409` quand rien n'attend, `404` hors catalogue, `502` si la suite n'a
    pas pu être produite (le geste, lui, reste acquis au fil).
    """
    fiche, service = _canal_chat(ctx, agent)
    fil = _conversation_demandee(service, fiche, requete.conversation)
    try:
        geste, reponse = await service.trancher_piece(
            fiche, decision=requete.decision, piece=requete.piece, conversation=fil
        )
    except PieceIntrouvable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReponseIndisponible as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "agent": fiche.nom,
        "role": fiche.role,
        "conversation": fil,
        "messages": [geste.to_dict(), reponse.to_dict()],
    }



@router.post("/api/chat/{agent}/outillage/questionnaire", status_code=201)
async def ouvrir_questionnaire_chat(
    ctx: Ctx,
    agent: str, conversation: str | None = None, projet: str | None = None
) -> dict[str, Any]:
    """Ouvre l'outillage d'un projet dans le fil — ou le reprend (#1031, #1161).

    Elle n'écrit **aucun message d'utilisateur** — personne n'a rien demandé —,
    seulement ce qui vient : la question qui manque, ou la première pièce, par la
    même voie que n'importe quelle réponse d'agent. `projet` nomme le projet à
    outiller quand le fil ne le dit pas : « Outiller maintenant » sur la carte
    d'un projet dont l'outillage a été reporté (#1161) — l'outillage se construit
    dans la conversation, pas dans une étape de formulaire.

    **Idempotente** : rappelée sur un questionnaire en cours, elle repose la
    question là où il en est plutôt que d'en recommencer un second. La propriété
    vient de ce que le fil est la seule mémoire du canal, pas d'une garde.

    `409` sur un fil dont le répondeur ne conduit pas de questionnaire — un
    aparté avec un agent du catalogue, par exemple. `404` hors catalogue, `422`
    sur une conversation mal formée.
    """
    fiche, service = _canal_chat(ctx, agent)
    fil = _conversation_demandee(service, fiche, conversation)
    try:
        message = await service.poser_question(fiche, conversation=fil, projet_id=projet)
    except QuestionIntrouvable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReponseIndisponible as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "agent": fiche.nom,
        "role": fiche.role,
        "conversation": fil,
        "messages": [message.to_dict()],
    }



@router.post("/api/chat/{agent}/outillage", status_code=201)
async def repondre_question_chat(
    ctx: Ctx,
    agent: str, requete: ReponseOutillageRequete
) -> dict[str, Any]:
    """Répond d'un **geste** à la question d'outillage que le fil porte (#1031).

    Le jumeau de `POST …/cadrage` sur l'autre demande du canal, et la même
    réponse : l'acte est écrit au fil, la suite vient derrière. La question
    visée n'est pas dans le corps — c'est celle qui attend —, si bien qu'un
    geste tardif ou un double clic tombe sur le `409` au lieu de répondre à une
    question déjà tranchée.

    `422` si la valeur n'est pas une option de la question posée — sauf réponse
    **avec ses mots** (`libre`, #1147), acceptée dès qu'elle n'est pas vide. `409`
    quand rien n'attend, `404` hors catalogue, `502` si la suite n'a pas pu être
    produite (le geste, lui, reste acquis au fil).
    """
    fiche, service = _canal_chat(ctx, agent)
    fil = _conversation_demandee(service, fiche, requete.conversation)
    try:
        geste, reponse = await service.repondre_question(
            fiche, valeur=requete.valeur, libre=requete.libre, conversation=fil
        )
    except QuestionIntrouvable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReponseIndisponible as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "agent": fiche.nom,
        "role": fiche.role,
        "conversation": fil,
        "messages": [geste.to_dict(), reponse.to_dict()],
    }



async def _flux_reponse(
    ctx: Contexte,
    agent: str,
    contenu: str,
    sources: list[dict[str, Any]] | None,
    projet_id: str | None,
    conversation: str | None,
    projet: str | None = None,
) -> StreamingResponse:
    """Le flux SSE d'une réponse — la mécanique des **deux** verbes (#268, #692).

    Écrite une fois et appelée par `GET …/flux` comme par `POST …/flux` :
    deux façons de déclarer l'envoi, un seul chemin d'envoi. Elle ouvre un
    `text/event-stream` où chaque `data: <json>` est un `FragmentChat` —
    `debut` (portant le message utilisateur), des `fragment` (incréments
    `delta`), puis `fin` (portant la réponse complète) ou `interrompu`
    (portant ce qui a été persisté d'une réponse arrêtée, #695). Une réponse
    impossible sort en trame `erreur` plutôt qu'en statut HTTP : les en-têtes
    sont déjà partis quand elle se découvre, et le message utilisateur, lui,
    est déjà persisté et diffusé.

    Même échange que `POST …/messages` — même persistance, même messagerie,
    mêmes `chat.message` sur le WebSocket —, rendu au fur et à mesure. Le
    canal vaut pour les **trois** fils : un agent du catalogue, `assistance`
    (#123) et `orchestrateur` (#268). 404 si le fil n'existe pas.

    Deux refus se tranchent **avant** la première trame, donc en statut HTTP
    et sans rien persister : un message vide (422 nu) et une source hors
    bornes (422 `{motif, message, index}`, #315 — la forme que `POST
    …/messages` sert déjà, celle qui permet à l'écran d'afficher le refus sur
    la source fautive).

    `conversation` désigne le fil où diffuser (#694) — même contrat que sur
    `POST …/messages`, et **toutes** les trames la portent, `debut` comprise :
    un client sait dès la première si ce qui arrive est le fil qu'il affiche.
    Un identifiant inconnu est un 404, mal formé un 422, absent le cas
    nominal (la conversation la plus récente).
    """
    fiche, service = _canal_chat(ctx, agent, projet)
    projet = projet_id_valide(projet_id)
    fil = _conversation_demandee(service, fiche, conversation)

    async def flux() -> AsyncIterator[str]:
        async for trame in service.diffuser(
            fiche, contenu, sources, projet_id=projet, conversation=fil
        ):
            yield f"data: {json.dumps(trame.to_dict(), ensure_ascii=False)}\n\n"

    try:
        # Le premier fragment est tiré **ici**, avant de rendre la réponse :
        # c'est ce qui fait qu'un contenu vide sort en 422 et non en flux
        # ouvert sur une erreur — `diffuser` lève avant sa première trame.
        trames = flux()
        premiere = await anext(trames)
    except SourceRefusee as exc:
        # Avant le `ValueError` nu dont elle hérite, pour la raison qui vaut
        # sur `POST …/messages` : aplatir le refus en chaîne perdrait l'index
        # de la source fautive, donc l'endroit où l'écran doit l'afficher.
        raise HTTPException(status_code=422, detail=_detail_refus(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    async def flux_complet() -> AsyncIterator[str]:
        yield premiere
        async for trame in trames:
            yield trame

    return StreamingResponse(flux_complet(), media_type="text/event-stream")



@router.get("/api/chat/{agent}/flux")
async def flux_chat(
    ctx: Ctx,
    agent: str,
    contenu: str = "",
    projet_id: str | None = None,
    conversation: str | None = None,
    projet: str | None = None,
) -> StreamingResponse:
    """Flux SSE d'une réponse de chat — le contrat de #183 (#268).

    `GET /api/chat/{agent}/flux?contenu=…` : la voie **sans source**, et le
    seul verbe qu'un `EventSource` sache ouvrir. Une URL ne pouvant porter ni
    identifiants de sources ni corps, un message qui embarque de la matière
    (#482) passe par `POST …/flux` — voir l'arbitrage en tête de
    `maestro.controltower.chat`. Ce n'est pas une voie dégradée : c'est le cas
    courant.

    `?projet_id=` porte le **projet de la fenêtre** (#683), comme le corps de
    `POST …/messages` : un run ouvert par cette voie se rattache donc comme
    un run ouvert par l'autre. Il n'est pas nommé `?projet=` à dessein — ce
    nom-là désigne partout ailleurs une **portée** de lecture, avec ses mots
    réservés `tous`/`aucun` (#277), et deux contrats sous un même nom seraient
    la première façon de les confondre.

    `?conversation=` désigne le fil où diffuser (#694), comme sur les deux
    autres verbes ; absent, la conversation la plus récente.
    """
    return await _flux_reponse(ctx, agent, contenu, None, projet_id, conversation, projet)



@router.post("/api/chat/{agent}/flux")
async def flux_chat_poste(
    ctx: Ctx,
    agent: str, requete: ChatEnvoiRequete, projet: str | None = None
) -> StreamingResponse:
    """Le flux d'une réponse pour un message qui **embarque** quelque chose (#692).

    Même corps que `POST …/messages` — `contenu`, `sources` (#482),
    `projet_id` (#683) — et même `text/event-stream` que `GET …/flux` : c'est
    exactement l'envoi du POST, rendu au fur et à mesure au lieu d'être rendu
    d'un coup. Le client n'a donc **pas** à choisir entre le direct et ses
    pièces jointes.

    Le message utilisateur **résolu** — ses sources et leur rapport de
    lecture (#316) — voyage sur la trame `debut`, la réponse sur la trame
    `fin` : la paire que le POST rend d'un coup, rendue en deux trames. 404 si
    le fil n'existe pas, 422 sur un message vide, 422 motivé sur une source
    refusée.

    Le corps porte aussi la `conversation` (#694) : mêmes clés que
    `POST …/messages`, donc les deux façons de poster un message se
    rattachent au fil de la même manière.
    """
    return await _flux_reponse(ctx, 
        agent,
        requete.contenu,
        requete.sources,
        requete.projet_id,
        requete.conversation,
        projet,
    )



@router.post("/api/chat/{agent}/flux/{echange}/arret")
async def arreter_flux_chat(
    ctx: Ctx,
    agent: str, echange: str, projet: str | None = None
) -> dict[str, Any]:
    """Arrête la génération en vol de `echange` — le geste, pas l'accident (#695).

    L'`echange` est celui que les trames du flux portent (`FragmentChat`). La
    génération est annulée, **ce qui en a déjà été reçu est persisté** comme
    réponse et le flux se clôt sur une trame `interrompu` qui la porte : la
    portion reçue reste au fil au lieu d'être un état d'écran.

    Ce n'est pas un second chemin d'envoi — rien n'est envoyé ici, et rien
    n'est produit : c'est le seul verbe qui **retire** quelque chose au canal.
    Il n'annule pas non plus ce qu'une déconnexion laisse vivre (arbitrage de
    #268, repris en tête de `maestro.controltower.chat`) : une déconnexion est
    un accident dont aucune intention ne se déduit, un arrêt est un acte.

    404 si le fil n'existe pas, et 404 aussi quand il n'y a **rien à
    arrêter** — identifiant inconnu ou échange qui vient de se terminer. Les
    deux se ressemblent d'ici et appellent la même conduite côté client :
    cliquer au moment où la réponse tombe est une course normale, pas une
    panne.
    """
    _, service = _canal_chat(ctx, agent, projet)
    if not service.interrompre(echange):
        raise HTTPException(
            status_code=404,
            detail=f"aucune génération en vol pour l'échange {echange!r}.",
        )
    return {"agent": agent, "echange": echange, "arrete": True}
