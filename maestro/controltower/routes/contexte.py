"""Le contexte de l'application : les services que `create_app` assemble, servis à chaque route.

Une route le reçoit par `ctx: Ctx`, une dépendance FastAPI qui le lit sur l'application qui sert
la requête (`app.state.contexte`). Deux applications montées côte à côte, comme les tests en
montent une par cas, gardent ainsi chacune le leur. La dépendance est asynchrone : elle se résout
dans la boucle d'événements, sans passer par le pool de threads."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from starlette.requests import HTTPConnection

if TYPE_CHECKING:
    import asyncio
    from typing import Any

    from maestro.agents.configuration import ConfigurationAgents
    from maestro.agents.mcp import McpStore
    from maestro.agents.mcp_admission import ServiceAdmission
    from maestro.agents.mcp_registry import RegistreMcp
    from maestro.agents.permissions import PermissionStore
    from maestro.agents.secrets import SecretStore
    from maestro.agents.store import AgentStore
    from maestro.controltower.accueil import ServiceAccueil
    from maestro.controltower.attentes import ServiceAttentes
    from maestro.controltower.auto_amelioration import AnalyseurEchecs, RedacteurPlaybook
    from maestro.controltower.battement import RegistreBattements
    from maestro.controltower.bilan import ServiceBilan
    from maestro.controltower.chat import ChatStore, RepondeurChat, ServiceChat
    from maestro.controltower.consultation import Consultations
    from maestro.controltower.equipe import ServiceEquipe
    from maestro.controltower.executions import ServiceExecutions
    from maestro.controltower.fournisseur_defaut import ServiceFournisseurParDefaut
    from maestro.controltower.generation_agent import GenerateurDefinitionAgent
    from maestro.controltower.journal import ServiceJournal
    from maestro.controltower.magasin import Magasin
    from maestro.controltower.outillage import ServiceOutillage
    from maestro.controltower.persistence import BusDurable, EventLog
    from maestro.controltower.projets import ServiceProjets
    from maestro.controltower.recit import ConteurDeFin
    from maestro.controltower.renfort import RelaisRenfort
    from maestro.controltower.routes.communs import Diffusion
    from maestro.controltower.state import ControlTowerState
    from maestro.installation import LecteurInstallation
    from maestro.messaging import Mailbox
    from maestro.poste import SondePoste
    from maestro.sources import UploadStore


class Contexte:
    """Les services et les valeurs que les routes partagent, posés par `create_app`.

    Chaque attribut est posé dès que `create_app` l'a construit : une aide appelée pendant
    l'assemblage lit donc ce qui existe déjà. Les types de ce bloc ne servent qu'à la vérification
    statique, et leurs imports restent sous `TYPE_CHECKING`.
    """

    accueil: ServiceAccueil
    admissions: ServiceAdmission
    agents_store: AgentStore
    analyseur: AnalyseurEchecs
    assistance: ServiceChat
    attentes: ServiceAttentes
    battements: RegistreBattements
    bilans: ServiceBilan
    bus: BusDurable
    chat: ServiceChat
    chat_repondeur: RepondeurChat | None
    chat_store: ChatStore
    chats_de_projet: dict[str, ServiceChat]
    consultations: Consultations
    conteur: ConteurDeFin
    diffusion: Diffusion
    equipe: ServiceEquipe
    event_log: EventLog
    executions: ServiceExecutions
    fournisseur_defaut: ServiceFournisseurParDefaut
    gabarits: ConfigurationAgents
    generateur_agent: GenerateurDefinitionAgent
    installation: LecteurInstallation
    journal: ServiceJournal
    magasin: Magasin
    mailbox: Mailbox
    mcp: McpStore
    mode_brief_du_fil: str
    orchestration: ServiceChat
    outillage: ServiceOutillage
    permissions: PermissionStore
    projets: ServiceProjets
    recits: set[asyncio.Future[Any]]
    redacteur_playbook: RedacteurPlaybook
    registre_mcp_injecte: RegistreMcp | None
    relais_renfort: RelaisRenfort
    secrets: SecretStore
    sonde_poste: SondePoste
    state: ControlTowerState
    televersements: UploadStore


async def contexte_de(connexion: HTTPConnection) -> Contexte:
    """Le contexte de l'application qui sert cette requête ou ce WebSocket."""
    contexte: Contexte = connexion.app.state.contexte
    return contexte


#: Le contexte, tel qu'une route le déclare : `ctx: Ctx`.
Ctx = Annotated[Contexte, Depends(contexte_de)]
