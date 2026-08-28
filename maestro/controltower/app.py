"""API Control Tower — REST + WebSocket temps réel (ticket #46).

Le backend du poste de pilotage (docs/05) : expose l'**état courant** de
l'orchestration en REST et **pousse les événements** en temps réel par
WebSocket. Stack retenue : FastAPI + WebSocket + Redis Pub/Sub (docs/02 §4).

Endpoints :

- `GET  /api/sante` — vitalité du service ;
- `GET  /api/taches` — les tâches (statut, agent, coût détaillé — tokens,
  durée) : la source du Kanban ;
- `GET  /api/agents` — l'état des agents (libre/occupé, charge, compteurs,
  capacité : actif/instances) ;
- `POST /api/agents/{nom}/capacite` — le contrôle de capacité (#86, EF-21) :
  active/désactive l'agent et/ou ajuste son plafond d'instances — persisté
  (`maestro.agents.capacity`) et relu à chaud par moteur et workers ;
- `GET  /api/executions` — les runs connus (en cours et passés), récents d'abord :
  objectif, statut, volume, coût, bornes temporelles (#185) et, sur un run non
  soldé, sa **vitalité** (#348) — `vivant` / `orphelin` / `indetermine`, selon que
  l'hôte du run bat encore, ne bat plus depuis le seuil, ou n'a jamais battu ;
- `POST /api/executions` — **lance** une exécution (objectif + garde-fous + les
  **sources** de l'objectif, #317) chez l'**hôte de run** du déploiement — un
  process détaché depuis #446 — et rend son `run_id` immédiatement (#185), avec le
  **rapport de lecture** de sa matière ;
- `POST /api/sources` — **téléverse** un ou plusieurs fichiers (multipart) et
  rend leurs identifiants de source (#317, EF-39) : les octets attendent dans le
  dépôt de téléversement, hors de tout projet, jusqu'au lancement qui les
  rattachera à son run. Refus motivé au-delà des plafonds d'ingestion (ENF-07),
  jamais de troncature silencieuse ;
- `POST /api/executions/{run_id}/annuler` — interrompt un run en cours (#185) ;
- `POST /api/executions/{run_id}/brief/decision` — tranche le **brief** d'un run
  suspendu (#320, décision D5) : approuver (avec un brief éventuellement corrigé,
  qui devient l'entrée de la décomposition) ou refuser (run « annulée », aucune
  tâche créée) ;
- `GET  /api/executions/{run_id}` — le détail d'une exécution (état, trace, coût)
  et, depuis #320, le **brief** soumis ou retenu ;
- `GET  /api/executions/{run_id}/cout` — le grand livre du run (#57) : coût
  par tâche (tokens entrée/sortie, coût estimé, durée) et agrégat ;
- `GET  /api/executions/{run_id}/graphe` — le **graphe** du run (#490) : un nœud
  par tâche du plan, une arête par dépendance, les nœuds rangés par niveaux —
  deux tâches indépendantes y tombent au même niveau, donc se lisent comme
  parallèles ;
- `GET  /api/analytics/couts` — la vue coûts & analytics (#87) : agrégats par
  tâche, par agent et par exécution, total et série temporelle du coût
  (`depuis` pour la période, `pas` pour la granularité des seaux) ;
- `POST /api/taches/{tache_id}/reassigner` — réassignation manuelle (Kanban) ;
- `GET  /api/validations` — les demandes de validation humaine (#48 : en
  attente d'abord le contexte, puis l'issue une fois tranchée). Une demande
  d'**application dans le projet** (#227, EF-37) y porte en plus son `diff` :
  les fichiers que l'accord écrirait et la branche qu'il fusionnerait ;
- `POST /api/validations/{tache_id}/decision` — la décision humaine
  (approuver/refuser) : le moteur, en attente sur le bus, reprend ou annule. Un
  refus peut porter un `motif` (#272), facultatif, qui rejoint le `detail` de
  l'événement — donc le journal et la `decision` de la demande ;
- `GET  /api/playbooks` — les playbooks des agents (#76 : version courante et
  provenance — défaut du code ou stockage versionné) ;
- `GET  /api/playbooks/{agent}` — le playbook courant d'un agent (contenu) ;
- `PUT  /api/playbooks/{agent}` — publie une nouvelle version du playbook ;
- `GET  /api/playbooks/{agent}/versions` — l'historique des versions ;
- `GET  /api/playbooks/{agent}/versions/{version}` — une version passée ;
- `POST /api/playbooks/{agent}/restaurer` — retour arrière (EF-25) : republie
  une version passée comme nouvelle version courante ;
- `GET  /api/playbooks/{agent}/propositions` — les propositions d'auto-amélioration
  en brouillon (#111 : jamais courantes, jamais chargées tant que non appliquées) ;
- `POST /api/playbooks/{agent}/propositions` — analyse **à la demande** les échecs
  d'un run (`run_id`) et génère une proposition de révision du playbook (#139) ;
- `GET  /api/playbooks/{agent}/propositions/{numero}` — une proposition, contenu compris ;
- `POST /api/playbooks/{agent}/propositions/{numero}/appliquer` — l'action humaine (#140) :
  la proposition devient la version courante (chargée à chaud, #78) et quitte les brouillons ;
- `POST /api/playbooks/{agent}/propositions/{numero}/rejeter` — écarte la proposition sans
  toucher à la version courante ;
- `GET  /api/mcp/registre` — la bibliothèque de serveurs MCP (#131), **à trois
  sources** depuis #678 : templates recherchables (`q`, par nom/tag/éditeur)
  portant transport, gabarit `${VAR}`, mode d'auth (docs/21), variables à
  fournir et procédure côté outil. `source=toutes|curee|admise|decouverte` n'en
  sert qu'une ; `curee` (le booléen) dit ce qui est **montable**, `source` dit
  **d'où ça vient** — garde-fou supply-chain, docs/19 ;
- `GET  /api/mcp/registre/provenance` — les **trois** provenances : le seed relu
  à la main avec sa date de revue, les admissions avec leur dernier geste, le
  miroir avec son rafraîchissement et son compte (#271, #677, #678) ;
- `GET  /api/mcp/registre/{id}` — une entrée, quelle que soit sa source (404 sinon) ;
- `GET  /api/mcp/admissions` — le **journal des admissions** (#678) : ce qu'un
  humain a fait entrer dans l'allowlist (actives et révoquées), les signaux que
  l'amont a émis depuis, et la politique qui garde la porte ;
- `POST /api/mcp/admissions` — **admet** une entrée découverte : enregistre
  l'entrée traduite figée avec sa source (nom amont, version épinglée, éditeur,
  dépôt, horodatage du miroir) et le geste (qui, quand, pourquoi) ;
- `POST /api/mcp/admissions/{id}/revocation` — **révoque** une admission :
  l'entrée sort de l'allowlist, rien n'est effacé ni démonté, et la réponse dit
  ce qui reste monté (un `POST` parce qu'une révocation est un acte tracé, pas
  une suppression) ;
- `GET  /api/mcp/pool` — le **pool projet** des intégrations MCP configurées
  (#133) : chaque intégration avec son mode d'auth et l'état (présent/valide)
  de ses secrets côté coffre projet — jamais une valeur de secret —, plus depuis
  #678 son `admission`, ses `signaux` d'amont et son `alerte` si elle n'est plus
  dans l'allowlist ;
- `POST /api/mcp/pool` — ajoute (ou reconfigure) une intégration au pool depuis
  la bibliothèque : instancie l'entrée de l'allowlist (garde-fou supply-chain)
  et pose ses secrets **une seule fois** dans le coffre projet chiffré (#133).
  Un 404 y **nomme le geste manquant** — l'admission — depuis #678 ;
- `DELETE /api/mcp/pool/{id}` — retire une intégration du pool, désactive son id
  chez chaque agent et purge les secrets qu'elle était seule à référencer ;
- `PUT  /api/mcp/activations/{agent}` — fixe les intégrations du pool **activées**
  pour un agent (#133) : l'écriture derrière l'interrupteur par agent qui
  remplace l'affichage lecture seule des serveurs MCP ;
- `GET  /api/projets` — les projets déclarés de l'utilisateur (#223, EF-35) :
  racine canonicalisée sur le disque, origine, `vcs` détecté et périmètre ;
- `GET  /api/projets/explorateur` — l'**explorateur de dossiers servi par
  l'API** (docs/05 §2.7) : énumère les sous-dossiers d'un chemin (marqueur
  « dépôt Git », projet déjà déclaré), borné aux **racines explorables** et aux
  zones non sensibles — un dépassement est un **refus motivé**, jamais une
  liste vide. Sans `chemin`, il rend les **points d'entrée** (#278) : dossier
  utilisateur, dossiers récents, projets déclarés, volumes du poste, chacun
  avec son `origine` ;
- `GET  /api/projets/selecteur` — le **sélecteur de dossier natif** est-il
  ouvrable ici (#278) ? Toujours 200 : une indisponibilité (backend distant,
  réglage à `0`, aucun dialogue sur le poste) est une réponse motivée, que
  l'écran affiche au lieu d'un bouton mort ;
- `POST /api/projets/selecteur` — ouvre le dialogue de dossier de l'OS et rend
  le chemin choisi, confronté à EF-38 (`racine_valide`, `refus`). Annuler rend
  200 (`annule: true`) — fermer une fenêtre n'est pas une erreur ;
- `GET  /api/projets/{id}` — un projet déclaré ;
- `POST /api/projets` — déclare un projet : racine **validée** (EF-38, refus
  motivé en 422) et VCS **constaté** sur le disque ;
- `PUT  /api/projets/{id}` — remplace la déclaration (l'intégrale, pas un diff) ;
- `DELETE /api/projets/{id}` — oublie un projet, sans jamais toucher au dossier
  sur le disque ;
- `GET  /api/catalogue` — le catalogue d'agents (#72, EF-03) : les agents par
  défaut du code et les personnalisés persistés, avec leur provenance, leurs
  serveurs MCP déclarés (#104, lecture seule — `mcp_serveurs`/`mcp_erreur`) et
  leur politique de permissions effective (#110, lecture seule —
  `permissions`/`permissions_erreur`) ;
- `GET  /api/catalogue/{nom}` — la définition complète d'un agent (playbook
  compris) ;
- `POST /api/catalogue` — crée un agent personnalisé (persisté hors du code,
  routable et exécutable par les moteurs construits ensuite) ;
- `PUT  /api/catalogue/{nom}` — remplace la définition d'un agent personnalisé ;
- `DELETE /api/catalogue/{nom}` — supprime un agent personnalisé ;
- `GET  /api/chat/{agent}` — le fil de conversation utilisateur ↔ agent (#84),
  persisté et relu du `ChatStore` ;
- `POST /api/chat/{agent}/messages` — envoie un message à l'agent et rend la
  paire message/réponse (le fil est aussi diffusé en `chat.message` sur le
  WebSocket, réponse comprise) ; `assistance` (#123) y désigne le **canal
  d'aide** — mêmes endpoints, fiche hors catalogue et réponses sans modèle
  (`maestro.controltower.assistance`) — et `orchestrateur` (#268) le **fil
  global** : on y adresse une demande à l'orchestration, qui répond et **ouvre
  un run** quand c'en est une (`maestro.controltower.orchestration`). Le corps
  porte le `projet_id` de la fenêtre (#683) : le run ouvert **appartient** au
  projet actif, donc il figure dans sa liste de runs et s'ouvre en détail ;
- `GET  /api/chat/{agent}/flux` — la même réponse rendue **au fur et à mesure**
  (SSE, trames `debut`/`fragment`/`fin`/`erreur`, #268) : un canal, valable pour
  les trois fils ; `?projet_id=` y porte le même rattachement que le corps du
  POST — et n'est pas le `?projet=` des lectures, qui est une portée (#277) ;
- `POST /api/chat/{agent}/flux` — le **même flux** pour un message qui embarque
  des sources (#692) : corps de `POST …/messages`, réponse en `text/event-stream`.
  Une URL ne pouvant porter ni identifiants de sources ni corps, le GET reste la
  voie du cas sans source ; les deux verbes appellent le même `ServiceChat.diffuser`
  (arbitrage écrit en tête de `maestro.controltower.chat`) ;
- `WS   /ws/evenements` — le flux d'événements (statuts de tâches, activité
  des agents, messages inter-agents, validations, chat), au format
  `Event.to_dict`.

Contrats d'API **v2** (#183 — formes figées des Phases 5/6, servies en fixtures
par la démo ; **501** en production tant que leur lot n'est pas livré) :

- `GET  /api/executions` — la liste des runs (résumés : statut, coût, dates,
  ticket) ; `POST /api/executions` — lance un run (objectif + garde-fous) ;
  `POST /api/executions/{run_id}/annuler` — interrompt un run en cours (#185) ;
- `GET  /api/journal` — le journal requêtable (filtres agent / type / run /
  période, tri, pagination) — servi **pour de bon** depuis #478 ;
- `GET  /api/configuration` — le registre de configuration éditable (réglages
  produit, couche 1 du cadrage sécurité #182) ;
- `GET  /api/playbooks/propositions` — les propositions d'auto-amélioration
  **tous agents confondus** (badge + notifications).

Assemblage : une **pompe** unique s'abonne au bus (`EventBus`), projette chaque
événement sur l'état (`ControlTowerState`), l'ajoute au journal requêtable
(`ServiceJournal`, #478), le **consigne** au journal durable (`EventLog`, #97)
puis le rediffuse aux WebSockets connectées — l'ordre « état d'abord, diffusion
ensuite » garantit qu'un client qui reçoit un événement lit un REST déjà à jour.
Au démarrage, le lifespan **rejoue** le journal pour reconstruire la projection
(exécutions, grands livres, analytics, tâches, agents, validations) **et
l'historique requêtable** d'avant un redémarrage de l'API : l'état survit à la
vie du process (#97). `create_app` s'injecte bus, état et journal (les tests d'API
tournent sur `InMemoryEventBus`/`InMemoryEventLog`, sans Redis) ;
`create_default_app` câble le `RedisEventBus` et le `RedisEventLog` de production
(canal `maestro.evenements`, alimenté par `maestro.controltower.bridge` côté
moteur ; journal persistant sur la liste `maestro.evenements:journal`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from maestro.agents.capacity import CapaciteAgent, CapacityStore
from maestro.agents.catalog import DEFAULT_AGENTS, Agent
from maestro.agents.mcp import IntegrationMcp, McpStore, references_env
from maestro.agents.mcp_admission import (
    MOTIF_NON_ADMISE,
    RefusAdmission,
    ServiceAdmission,
    etat_politique,
)
from maestro.agents.mcp_federation import federer_memo, oublier_memo
from maestro.agents.mcp_registry import (
    SOURCE_ADMISE,
    SOURCE_CUREE,
    SOURCE_DECOUVERTE,
    SOURCE_TOUTES,
    RegistreMcp,
)
from maestro.agents.permissions import PermissionStore
from maestro.agents.playbooks import PLAYBOOK_DEFAUTS, PlaybookStore
from maestro.agents.secrets import SecretStore
from maestro.agents.store import NOMS_RESERVES, AgentDefinition, AgentStore, catalogue
from maestro.appartenance import projet_id_valide
from maestro.config import ConfigError, Settings, load_settings
from maestro.controltower import selecteur
from maestro.controltower.analytics import PAS_HEURE, PAS_VALIDES, agrege_couts
from maestro.controltower.assistance import (
    AGENT_ASSISTANCE,
    NOM_ASSISTANCE,
    RepondeurAssistance,
)
from maestro.controltower.auto_amelioration import (
    AnalyseurEchecs,
    RevisionIndisponible,
    echecs_du_run,
)
from maestro.controltower.battement import (
    RegistreBattements,
    RegistreBattementsMemoire,
    RegistreBattementsRedis,
)
from maestro.controltower.brief import ACTEUR_BRIEF, ROLE_BRIEF
from maestro.controltower.chat import (
    ChatStore,
    RepondeurChat,
    RepondeurModele,
    ReponseIndisponible,
    ServiceChat,
)
from maestro.controltower.events import (
    EVENEMENT_AGENT_CAPACITE,
    EVENEMENT_BRIEF_DECISION,
    EVENEMENT_BRIEF_REPONSES,
    EVENEMENT_TACHE_REASSIGNATION,
    EVENEMENT_TACHE_REFERENCE,
    EVENEMENT_VALIDATION_DECISION,
    Event,
    EventBus,
    InMemoryEventBus,
    RedisEventBus,
    brief_depuis,
)
from maestro.controltower.executions import (
    MOTIF_RELANCE_RUN_INCONNU,
    MOTIF_RELANCE_RUN_SOLDE,
    MOTIF_RELANCE_RUN_VIVANT,
    FabriqueMoteur,
    LecteurSources,
    RelanceRefusee,
    ServiceExecutions,
)
from maestro.controltower.fixtures import FixturesControlTower
from maestro.controltower.frise import frise_du_run
from maestro.controltower.hote import (
    HOTE_RUN_DETACHE,
    HOTE_RUN_EN_PROCESS,
    HOTES_RUN,
    HoteRun,
)
from maestro.controltower.journal import (
    ORDRE_DESC,
    ORDRES_JOURNAL,
    TAILLE_PAGE_DEFAUT,
    TAILLE_PAGE_MAX,
    TRI_JOURNAL_HORODATAGE,
    TRIS_JOURNAL,
    ServiceJournal,
)
from maestro.controltower.orchestration import (
    AGENT_ORCHESTRATION,
    NOM_ORCHESTRATION,
    RepondeurOrchestration,
    apercu_de,
)
from maestro.controltower.persistence import (
    EventLog,
    InMemoryEventLog,
    RedisEventLog,
)
from maestro.controltower.portee import (
    PorteeProjet,
    PorteeRefusee,
    PorteeRun,
    resoudre_portee,
    resoudre_portee_run,
)
from maestro.controltower.projets import (
    ProjetInconnu,
    ServiceProjets,
    detail_refus,
    statut_http,
)
from maestro.controltower.state import (
    BRIEF_APPROUVE,
    BRIEF_REFUSE,
    CAPACITE_ACTIVE,
    CAPACITE_DESACTIVE,
    EXECUTION_EN_ATTENTE_BRIEF,
    EXECUTION_EN_ATTENTE_REPONSES,
    STATUTS_EXECUTION_TERMINAUX,
    VALIDATION_APPROUVEE,
    VALIDATION_REFUSEE,
    ControlTowerState,
)
from maestro.engine.brief import MODE_BRIEF_AUTO, MODE_BRIEF_HUMAIN
from maestro.messaging import InMemoryMailbox, Mailbox, RedisMailbox
from maestro.orchestrator.errors import BriefValidationError
from maestro.orchestrator.schema import validate_brief
from maestro.projets import RacineRefusee, canonique, valider_racine
from maestro.references import ReferenceTicket
from maestro.sources import DepotTeleversements, SourceRefusee, apercu_sources


class ReferenceTicketRequete(BaseModel):
    """La référence d'un ticket externe posée au lancement d'un run (#185, contrat #183).

    Générique : un identifiant lisible (`id`, ex. « #185 », « PROJ-42 ») et son
    `url` — vide quand seul l'identifiant est connu. GitLab, Jira ou Linear
    passent par la même forme, aucun champ propre à un outil. La référence
    descend ensuite jusqu'aux tâches du run (contrat #183).
    """

    id: str
    url: str = ""

    def en_reference(self) -> ReferenceTicket | None:
        """La référence du modèle métier — None si elle n'apprend rien (ni id, ni URL).

        Passe par `ReferenceTicket.depuis`, donc par la normalisation (#187) :
        identifiant borné, URL non suivable (`javascript:`, chemin relatif)
        jetée. Ce qui entre par l'API est traité comme ce qui arrive par le
        flux — non fiable jusqu'à validation.
        """
        return ReferenceTicket.depuis({"id": self.id, "url": self.url})


class SourceRequete(BaseModel):
    """Une source déclarée au lancement (#317, EF-39) — docs/05 §6.1.

    Deux formes pour un `fichier`, et une seule est complète : le **renvoi** à un
    téléversement (`id`, rendu par `POST /api/sources`), qui seul désigne de
    vrais octets, et la déclaration nue de docs/24 §3.2 (`nom` + `taille`), qui
    résout mais ressortira `source-absente` au rapport de lecture. Un `dossier`
    porte son `chemin`, une `url` sa `valeur`.

    **Aucun champ n'est requis**, `type` compris : un corps mal formé doit
    ressortir en refus **motivé** de la résolution (`type-inconnu` à l'index
    fautif) et non en erreur de schéma Pydantic, dont la forme n'a ni motif ni
    index. Le reste des contrôles appartient à `maestro.sources.resolution` :
    ce qui arrive ici est une saisie, jamais une vérité.
    """

    type: str = ""
    id: str = ""
    nom: str = ""
    chemin: str = ""
    valeur: str = ""
    taille: int | None = None


class LancementExecutionRequete(BaseModel):
    """Corps de lancement d'une exécution (#185) : objectif, garde-fous, ticket.

    `objectif` est l'énoncé en langage naturel que l'orchestrateur décompose ;
    les garde-fous (#9) plafonnent l'exécution — coût, tokens, time-out par
    tâche, parallélisme —, chacun optionnel, None laissant le défaut du moteur.
    `ticket` rattache le run à un ticket externe (optionnel). `projet_id` (#222)
    le rattache au **projet** dans lequel il travaille (optionnel, `null` :
    aucun projet — le comportement d'avant ce lot). Une requête invalide
    (objectif vide, garde-fou hors bornes) est refusée en 422 ; un `projet_id`
    mal formé, lui, est **écarté** (le run part sans projet) plutôt que de faire
    échouer le lancement — cf. `maestro.appartenance`.

    `sources` (#317, EF-39) porte la **matière** de l'objectif : fichiers
    téléversés au préalable (§6.8), dossier de références, URL. Absente ou vide,
    le lancement est exactement celui d'avant la Phase 8 ; une source refusée
    (racine interdite, plafond dépassé, téléversement inconnu) sort en 422
    **motivé**, index de la source fautive compris.
    """

    objectif: str
    plafond_cout_usd: float | None = None
    plafond_tokens: int | None = None
    timeout_tache_s: float | None = None
    parallelisme: int | None = None
    ticket: ReferenceTicketRequete | None = None
    projet_id: str | None = None
    sources: list[SourceRequete] | None = None
    # Le régime du brief (#320) — `humain` par défaut : la Control Tower est la
    # voie de lancement qui a quelqu'un devant, et la décision D5 veut le brief
    # validé avant décomposition. `auto` rédige le brief sans attendre, `sans`
    # décompose l'objectif brut (le comportement d'avant ce lot). Un mode inconnu
    # est refusé en 422, jamais traité comme un `sans` silencieux.
    brief: str | None = MODE_BRIEF_HUMAIN

    def sources_declarees(self) -> list[dict[str, Any]] | None:
        """Les sources en dicts pour la résolution — `None` quand il n'y en a pas.

        `None` et non `[]` : c'est ce qui fait qu'un lancement sans matière émet
        un événement sans le champ `sources` (#315), donc strictement celui
        d'avant la Phase 8.
        """
        if self.sources is None:
            return None
        return [source.model_dump() for source in self.sources]


class ReassignationRequete(BaseModel):
    """Corps de la réassignation manuelle : l'agent qui reprend la tâche."""

    agent: str


class DecisionRequete(BaseModel):
    """Corps de la décision humaine (#48) : approuver ou refuser l'action sensible.

    `motif` (#272) est la raison **facultative** d'un refus, telle que la personne
    l'a écrite. Il ne change rien à ce que le moteur fait — celui-ci ne lit que
    `statut` — mais il rejoint le `detail` de l'événement, donc le journal durable
    et la `decision` de la demande projetée : sans lui, un refus revenait plus tard
    comme un fait sans cause, et rien ne distinguait « trop risqué avant la démo »
    d'une erreur de clic.

    Sur une **approbation** il est ignoré, comme le `brief` d'une décision de brief
    l'est sur un refus (`DecisionBriefRequete`) : le canal porte les deux gestes,
    chacun ne lit que ce qui le concerne. Vide ou absent, la décision est celle
    d'avant ce lot, au caractère près.
    """

    approuve: bool
    motif: str = ""


class DecisionBriefRequete(BaseModel):
    """Corps de la décision sur un brief (#320) : approuver — corrigé ou non — ou refuser.

    `brief` porte la **version corrigée** que l'humain veut voir décomposée ; absent
    (`null`), le brief proposé est approuvé tel quel. C'est ce qui distingue ce
    canal de la validation d'action sensible (#48), dont le corps se résume à un
    booléen : ici la personne ne fait pas que dire oui, elle réécrit avant de le
    dire — et c'est son texte qui part en décomposition.

    Le brief corrigé est validé contre la **JSON Schema partagée** (#318) avant de
    partir : une correction qui casse la forme doit coûter un 422 à celui qui la
    soumet, pas un échec de run une seconde plus tard, quand plus personne ne
    regarde. Sur un **refus**, il est ignoré — il n'y a rien à décomposer.
    """

    approuve: bool
    brief: dict[str, Any] | None = None


class ReponsesBriefRequete(BaseModel):
    """Corps des réponses aux questions de clarification d'un brief (#321).

    `reponses` est une liste de chaînes **appariée par position** aux questions du
    brief stocké du run. Pas de clé, pas d'identifiant de question : le brief est
    régénéré en entier à chaque tour, donc une question n'a pas d'identité stable
    d'une version à l'autre (#318, note du schéma) — un identifiant laisserait croire
    le contraire. Ce qui rend la position sûre est que les réponses s'adressent au
    brief **stocké**, dont la liste de questions est figée entre sa publication
    (`brief.questions`) et sa réponse.

    D'où le contrôle de longueur exigé par la route : une liste qui ne fait pas le
    compte est refusée en 422. C'est le seul moment où quelqu'un est là pour corriger
    sa requête — plus loin, en plein run, l'appariement est volontairement tolérant
    (une réponse manquante vaut « sans réponse »), parce que lever y coûterait le run.

    Une chaîne **vide est licite** et veut dire « je ne sais pas » : la question sera
    inscrite en hypothèse explicite plutôt que reposée au tour suivant. C'est ce qui
    permet de répondre à trois questions sur cinq sans bloquer le run.
    """

    reponses: list[str]


class CapaciteRequete(BaseModel):
    """Corps du contrôle de capacité (#86) : les réglages à poser, champ par champ.

    Chaque champ est optionnel — None laisse la valeur en place : on peut
    désactiver sans toucher aux instances, et inversement. Au moins un champ
    doit être renseigné (sinon 422 : il n'y a rien à régler).
    """

    actif: bool | None = None
    instances: int | None = None


class PlaybookEcritureRequete(BaseModel):
    """Corps d'écriture d'un playbook (#76) : le nouveau contenu Markdown, intégral."""

    contenu: str


class PlaybookRestaurationRequete(BaseModel):
    """Corps du retour arrière (#76) : la version passée à republier comme courante."""

    version: int


class PlaybookPropositionRequete(BaseModel):
    """Corps d'une demande de proposition d'auto-amélioration (#139) : le run à analyser.

    `run_id` désigne l'exécution dont les échecs de l'agent alimentent l'analyse. Le
    déclenchement est **à la demande** (ce POST), jamais automatique en fin de run.
    """

    run_id: str


class AgentCreationRequete(BaseModel):
    """Corps de création d'un agent personnalisé (#72) : sa définition complète.

    `modele` est optionnel (None : le modèle par défaut des exécutants) ;
    `fournisseur` est déclaratif au POC (le moteur est mono-fournisseur).
    """

    nom: str
    role: str
    competences: list[str]
    playbook: str
    modele: str | None = None
    fournisseur: str | None = None


class AgentModificationRequete(BaseModel):
    """Corps de modification d'un agent personnalisé (#72) : la définition intégrale.

    Un remplacement, pas un diff — le nom, lui, vit dans l'URL et ne change pas
    (c'est la clé de routage et de stockage).
    """

    role: str
    competences: list[str]
    playbook: str
    modele: str | None = None
    fournisseur: str | None = None


class ProjetRequete(BaseModel):
    """Corps de déclaration d'un projet (#223) : sa racine sur le disque et son périmètre.

    `racine` est le chemin **absolu** du projet sur le poste ; il est
    canonicalisé et confronté aux racines interdites côté serveur (EF-38) — ce
    qui arrive ici est une saisie, jamais une vérité. `origine` vaut `existant`
    (on reprend un dossier) ou `nouveau` (on l'initie : le dossier est créé s'il
    manque). `inclus`/`exclus` sont des motifs **relatifs à la racine** ; `null`
    laisse les défauts du modèle (`.`, et l'exclusion de `.git`, `node_modules`,
    `.env`, `**/secrets/**`).

    Le `vcs` n'est délibérément **pas** un champ de requête : il est constaté sur
    le disque, jamais déclaré — un client qui l'annoncerait pourrait mentir.
    """

    nom: str
    racine: str
    origine: str = "existant"
    inclus: list[str] | None = None
    exclus: list[str] | None = None


class SelecteurRequete(BaseModel):
    """Corps (facultatif) d'une ouverture du sélecteur natif (#278).

    `depart` est le dossier où le dialogue s'ouvre — un confort, jamais une
    permission : le chemin **rendu** par le dialogue est confronté à EF-38 quel
    que soit l'endroit d'où l'utilisateur est parti.
    """

    depart: str | None = None


class ChatEnvoiRequete(BaseModel):
    """Corps d'un envoi de chat (#84) : le message de l'utilisateur à l'agent.

    `sources` (#482) est la matière que le message embarque — fichiers déposés,
    dossier de références, adresses —, **dans l'ordre où l'écran l'a composée** :
    c'est lui qui décide de ce qui entre quand le budget de tokens s'épuise
    (#316). Même forme qu'au lancement (`LancementExecutionRequete.sources`), et
    pour la même raison : un fichier y voyage par l'`id` que `POST /api/sources`
    lui a rendu, seule façon d'en désigner de vrais octets. Absente ou vide, le
    fil est exactement celui d'avant ce lot.

    `projet_id` (#683) est le **projet de la fenêtre** d'où part le message —
    à ne pas confondre avec le `?projet=` des lectures (#277), qui est une
    portée et porte les mots réservés `tous`/`aucun` : ici c'est un identifiant
    de projet, et rien d'autre. Le fil ne s'en trouve ni cadré ni filtré ; il
    n'intéresse que ce que la réponse **ouvre** — l'orchestration y rattache son
    run et y cadre son aperçu. Absent, le run part sans projet, comme avant.
    """

    contenu: str
    sources: list[dict[str, Any]] | None = None
    projet_id: str | None = None


class SecretPoolRequete(BaseModel):
    """Une valeur de secret saisie pour une intégration du pool (#133).

    `cle` est le nom de la variable `${VAR}` du gabarit (ex. `GITLAB_TOKEN`),
    `valeur` ce que l'humain saisit (token, canal d'appairage, token OAuth
    importé). `expire_le` (ISO 8601) n'a de sens que pour un token OAuth
    importé (`oauth_importe`) : c'est l'échéance qui rend sa validité visible.
    Une valeur vide est ignorée (le secret reste à configurer).
    """

    cle: str
    valeur: str
    expire_le: str | None = None


class IntegrationPoolRequete(BaseModel):
    """Corps d'ajout d'une intégration au pool projet (#133) depuis la bibliothèque.

    `registre_id` désigne l'entrée **curée** à instancier (garde-fou
    supply-chain, docs/19 — un id hors registre est refusé). `nom` nomme la
    liaison (le préfixe d'outils `mcp__<nom>__…`, défaut : l'id) ; `secrets`
    porte les valeurs saisies **une seule fois**, stockées chiffrées côté
    serveur dans le coffre projet partagé.
    """

    registre_id: str
    nom: str | None = None
    secrets: list[SecretPoolRequete] = []


class AdmissionRequete(BaseModel):
    """Corps de l'admission d'une entrée découverte dans l'allowlist (#678).

    `registre_id` désigne l'entrée **découverte** à admettre — celle que l'écran
    vient de lister avec ses signaux de confiance (éditeur, version, dépôt,
    statut). `par` est qui admet, `note` pourquoi : l'admission est un geste
    **tracé**, et une trace sans auteur ni raison ne sert à rien six mois plus
    tard. Aucun des deux n'est **exigé** — refuser une admission faute de nom
    n'ajouterait aucune sécurité (rien ne prouve un nom saisi ici) et
    déplacerait la porte du côté du formulaire.
    """

    registre_id: str
    par: str = ""
    note: str = ""


class RevocationRequete(BaseModel):
    """Corps de la révocation d'une admission (#678).

    `par` et `motif` sont la trace du geste inverse — pourquoi on retire de
    l'allowlist ce qu'on y avait fait entrer. Ils ne conditionnent rien : ce qui
    compte est que la révocation ait lieu, pas qu'elle soit bien remplie.
    """

    par: str = ""
    motif: str = ""


class ActivationsMcpRequete(BaseModel):
    """Corps de l'activation des intégrations du pool pour un agent (#133).

    Remplacement intégral : la liste `integrations` (des ids du pool) devient
    l'ensemble activé de l'agent — une liste vide le désactive de toutes. C'est
    ce que pose l'interrupteur par agent qui **remplace** l'affichage lecture
    seule des serveurs MCP (critère #133).
    """

    integrations: list[str]


class Diffusion:
    """Fan-out des événements vers les WebSockets connectées, **à leur portée**.

    Une file par connexion, alimentée par la pompe ; chaque handler WebSocket
    draine la sienne. Files non bornées : le POC diffuse peu d'événements et
    une connexion les consomme au fil de l'eau.

    Chaque connexion déclare sa **portée projet** à l'ouverture (#277) et ne
    reçoit que les événements que celle-ci retient — le tri se fait donc **à
    l'entrée de la file**, pas à l'émission : une socket cadrée sur un projet
    n'est même pas réveillée par le travail d'un autre, et aucun événement
    étranger ne peut être servi par erreur en aval. C'est la même règle que les
    vues REST (`PorteeProjet.retient`), appliquée au flux : un client qui
    recharge son état puis suit les événements voit deux fois le même périmètre.
    """

    def __init__(self) -> None:
        self._connexions: dict[asyncio.Queue[Event], PorteeProjet] = {}

    def connecter(self, portee: PorteeProjet | None = None) -> asyncio.Queue[Event]:
        """Enregistre une connexion : sa file recevra les événements de sa portée.

        `None` vaut la vue transverse — le comportement d'avant #277, conservé
        pour les appels internes ; les routes, elles, exigent une portée.
        """
        file: asyncio.Queue[Event] = asyncio.Queue()
        self._connexions[file] = portee if portee is not None else PorteeProjet.tous()
        return file

    def deconnecter(self, file: asyncio.Queue[Event]) -> None:
        """Retire la connexion ; ses événements en attente disparaissent avec elle."""
        self._connexions.pop(file, None)

    def diffuser(self, event: Event) -> None:
        """Pousse `event` aux connexions dont la portée le retient."""
        for file, portee in self._connexions.items():
            if portee.retient(event.projet_id):
                file.put_nowait(event)


_LOGGER = logging.getLogger("maestro.controltower")


#: Le code HTTP de chaque motif de refus d'une **relance** (#349). La table vit
#: ici et non dans le service : c'est la route qui parle HTTP, le service ne
#: connaît que ses motifs. Repli à `422` pour un motif non listé — un refus qu'on
#: n'a pas su classer reste une requête que l'API n'a pas honorée, jamais un
#: succès.
_CODE_REFUS_RELANCE: dict[str, int] = {
    MOTIF_RELANCE_RUN_INCONNU: 404,
    # Les deux mêmes 409 que l'annulation, et pour la même raison : l'état du run
    # interdit le geste, sans que la requête soit malformée.
    MOTIF_RELANCE_RUN_SOLDE: 409,
    MOTIF_RELANCE_RUN_VIVANT: 409,
}


def _detail_refus(exc: Exception) -> dict[str, Any]:
    """Le corps d'un refus de source ou de lancement : `{motif, message[, index]}`.

    `detail_refus` (#223) donne les deux premiers champs — même convention que
    les routes projets, un code stable plutôt qu'une phrase à analyser, et
    `requete-invalide` en repli pour ce qui n'a pas de motif propre (objectif
    vide, garde-fou hors bornes). L'`index` s'y ajoute quand le refus **vise une
    source** (#315) : « une source est trop grosse » sans dire laquelle
    obligerait à tout relire pour savoir quoi retirer.
    """
    detail: dict[str, Any] = dict(detail_refus(exc))
    index = getattr(exc, "index", None)
    if isinstance(index, int) and not isinstance(index, bool):
        detail["index"] = index
    return detail


async def _pompe(
    bus: EventBus,
    state: ControlTowerState,
    diffusion: Diffusion,
    event_log: EventLog,
    journal: ServiceJournal,
) -> None:
    """Le seul consommateur du bus : projette sur l'état, **persiste**, puis rediffuse.

    Cet ordre rend le flux cohérent pour les clients : à réception d'un
    événement WebSocket, l'état REST le reflète déjà — le journal requêtable
    (#478) compris, d'où sa consignation **avant** la diffusion : un client qui
    recharge sur un événement qu'il vient de recevoir en direct doit le
    retrouver dans son historique, jamais l'inverse. Chaque événement est
    consigné au journal durable (#97) entre la projection et la diffusion —
    c'est la mémoire longue qui, rejouée au démarrage, reconstruit l'état après
    un redémarrage de l'API. Une panne de **persistance** (Redis injoignable le
    temps d'un événement) est tracée mais n'interrompt pas le flux temps réel :
    le seul risque est que cet événement manque au prochain rejeu. Une panne du
    **bus**, elle, arrête le flux temps réel mais pas l'API : le REST continue
    de servir l'état déjà projeté — la panne est tracée, pas avalée.
    """
    try:
        async for event in bus.subscribe():
            state.appliquer(event)
            journal.consigner(event)
            try:
                await event_log.consigner(event)
            except Exception:
                _LOGGER.exception(
                    "Échec de persistance d'un événement : il manquera au prochain "
                    "rejeu au démarrage (flux temps réel et projection préservés)."
                )
            diffusion.diffuser(event)
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOGGER.exception(
            "La pompe d'événements s'est arrêtée : flux temps réel interrompu "
            "(le REST reste servi sur le dernier état projeté)."
        )


def create_app(
    *,
    bus: EventBus | None = None,
    state: ControlTowerState | None = None,
    playbooks: PlaybookStore | None = None,
    agents_store: AgentStore | None = None,
    mailbox: Mailbox | None = None,
    chat_store: ChatStore | None = None,
    chat_repondeur: RepondeurChat | None = None,
    assistance_repondeur: RepondeurChat | None = None,
    orchestration_repondeur: RepondeurChat | None = None,
    analyseur: AnalyseurEchecs | None = None,
    capacites: CapacityStore | None = None,
    mcp: McpStore | None = None,
    registre_mcp: RegistreMcp | None = None,
    admissions: ServiceAdmission | None = None,
    secrets: SecretStore | None = None,
    permissions: PermissionStore | None = None,
    projets: ServiceProjets | None = None,
    event_log: EventLog | None = None,
    journal: ServiceJournal | None = None,
    battements: RegistreBattements | None = None,
    fixtures: FixturesControlTower | None = None,
    fabrique_moteur: FabriqueMoteur | None = None,
    televersements: DepotTeleversements | None = None,
    lecteur_sources: LecteurSources | None = None,
    hote_run: HoteRun | None = None,
) -> FastAPI:
    """Construit l'app FastAPI de la Control Tower autour d'un bus et d'un état.

    Par défaut : bus mémoire et état neuf (agents du catalogue, statut libre) —
    la configuration des tests et d'une démo mono-process. La production passe
    par `create_default_app` (bus Redis). La pompe vit avec l'app (lifespan) :
    démarrée à l'ouverture, annulée à l'arrêt, bus refermé derrière elle.

    `playbooks` (#76) est le dépôt versionné servi par les endpoints
    `/api/playbooks` — par défaut celui de la config (`MAESTRO_PLAYBOOKS_DIR`,
    sinon `core/playbooks/` du dépôt) ; les tests en injectent un temporaire.

    `agents_store` (#72) est le dépôt des agents personnalisés servi par les
    endpoints `/api/catalogue` — par défaut celui de la config
    (`MAESTRO_AGENTS_DIR`, sinon `core/agents/` du dépôt). L'état par défaut se
    construit sur le catalogue **effectif** : les agents personnalisés déjà
    persistés sont présents dès le démarrage, comme ceux du code.

    `mailbox`, `chat_store` et `chat_repondeur` (#84) portent le chat
    utilisateur ↔ agent des endpoints `/api/chat` : la messagerie inter-agents
    où transitent les échanges (mémoire par défaut, Redis en production), le
    fil persisté (`MAESTRO_CHAT_DIR`, sinon `core/chat/` du dépôt) et la
    production de la réponse — par défaut le fournisseur configuré, cadré par
    le playbook courant de l'agent ; la démo (#65) et les tests injectent un
    répondeur scripté.

    `assistance_repondeur` (#123) porte le **canal d'aide** `/api/chat/assistance` :
    un second `ServiceChat` sur le même fil persisté, la même messagerie et le même
    bus que le chat, mais avec son propre répondeur — par défaut
    `RepondeurAssistance`, déterministe et sans modèle (les questions portent sur
    l'outil, pas sur le projet ; voir `maestro.controltower.assistance`).

    `orchestration_repondeur` (#268) porte le **fil global**
    `/api/chat/orchestrateur` : un troisième `ServiceChat` sur les mêmes rouages,
    dont le répondeur par défaut (`RepondeurOrchestration`) **agit** — une demande
    de travail y est proposée par le modèle (#685), et l'accord de l'utilisateur y
    ouvre un run par le service d'exécutions de cette app, dont la réponse porte
    l'identifiant. Il est construit **après** `executions`, dont il tient son
    lanceur ; c'est aussi le point d'injection qui permet de jouer le fil **sans
    fournisseur** — la démo (#65) y met un répondeur scripté, les tests un
    répondeur à fournisseur factice, ou sans lanceur.

    `analyseur` (#139) produit les propositions d'auto-amélioration servies par
    `POST /api/playbooks/{agent}/propositions` : à la demande, il analyse les
    échecs d'un run via la couche fournisseur et enregistre un brouillon (#138).
    Par défaut il partage le dépôt `playbooks` et résout son fournisseur par
    config ; les tests (#137) en injectent un à fournisseur factice.

    `capacites` (#86) est le dépôt du contrôle de capacité servi par
    `POST /api/agents/{nom}/capacite` — par défaut celui de la config
    (`MAESTRO_CAPACITE_DIR`, sinon `core/capacite/` du dépôt) : le même que
    relisent moteur et workers, pour que le réglage ait un effet réel. L'état
    par défaut est amorcé des réglages déjà persistés (un état injecté reste,
    lui, tel quel — la configuration des tests).

    `mcp` (#104) est le dépôt des serveurs MCP déclarés par agent, affichés en
    **lecture seule** sur les fiches du catalogue (`mcp_serveurs`, valeurs de
    secrets masquées) — par défaut celui de la config (`MAESTRO_MCP_DIR`, sinon
    `core/mcp/` du dépôt) : le même que montent moteur et workers.

    `registre_mcp` (#131) est la **bibliothèque** de serveurs MCP servie par
    `/api/mcp/registre` : des templates recherchables (nom/tag/éditeur) portant
    transport, gabarit `${VAR}`, mode d'auth (docs/21) et procédure côté outil.
    Par défaut elle est **fédérée** (#677) : le seed curé en code — l'allowlist
    supply-chain du garde-fou docs/19, seule instanciable — plus les entrées
    découvertes dans le miroir du registre officiel, s'il y en a un sur le
    disque. Sans miroir, le résultat est exactement le seed d'avant. Un registre
    **injecté** court-circuite la fédération : c'est ainsi que les tests servent
    une allowlist restreinte, et la valeur injectée est alors la seule servie.

    `secrets` (#132/#133) est le **coffre chiffré** des secrets d'intégrations :
    l'UI d'écriture (`POST /api/mcp/pool`) y pose les secrets du pool projet
    **une seule fois** (coffre projet, `SecretStore.enregistrer_projet`), partagés
    par tout agent qui active l'intégration — par défaut celui de la config
    (`MAESTRO_SECRETS_DIR`, sinon `core/secrets/` du dépôt), le même que résolvent
    moteur et workers au montage. Les tests en injectent un coffre temporaire.

    `permissions` (#110) est le dépôt des politiques allow/ask/deny par agent,
    affichées en **lecture seule** sur les fiches du catalogue (`permissions`,
    la politique effective appliquée à l'exécution) — par défaut celui de la
    config (`MAESTRO_PERMISSIONS_DIR`, sinon `core/permissions/` du dépôt) :
    le même que relisent moteur et workers.

    `projets` (#223) porte le CRUD des projets de l'utilisateur et l'explorateur
    de dossiers servis par `/api/projets` — par défaut le service de la config
    (dépôt `MAESTRO_PROJETS_DIR`, sinon `core/projets/` du dépôt ; racines
    explorables `MAESTRO_EXPLORATEUR_RACINES`, sinon le dossier utilisateur).
    Les tests en injectent un service sur dépôt temporaire, seule façon de fixer
    les racines explorables sans dépendre du poste qui joue la suite.

    `event_log` (#97) est le **journal durable des événements** que la pompe
    consigne et que le lifespan **rejoue au démarrage** pour reconstruire la
    projection (exécutions, grands livres, analytics, tâches, agents,
    validations) après un redémarrage de l'API — par défaut un journal mémoire
    (pas de durabilité inter-redémarrage : la configuration des tests et d'une
    démo mono-process). La production câble `RedisEventLog` via
    `create_default_app`. Un état injecté (`state`) reste tel quel puis reçoit
    le rejeu par-dessus (idempotent : les événements reconstruisent le même
    état).

    `journal` (#478) est le **journal requêtable** servi par `GET /api/journal` :
    l'index transverse, filtrable et paginé, des événements consignés. Il n'a
    aucune durabilité propre — il se remplit du rejeu d'`event_log` à l'ouverture
    puis de la pompe au fil de l'eau —, ce qui fait de lui une **vue** du journal
    durable et non un second stockage à tenir d'accord avec lui.

    `battements` (#348) est le registre où l'**hôte** d'un run pose son signal de
    vie, et où `GET /api/executions` lit la vitalité de chaque run non soldé
    (vivant / orphelin / indéterminé) — par défaut un registre mémoire, qui ne
    voit battre que les runs de ce process. La production câble
    `RegistreBattementsRedis` via `create_default_app` : c'est ce qui fait qu'un
    run lancé par `maestro-run --publier` reste reconnu vivant **à travers un
    redémarrage de l'API**, la lecture ne dépendant alors d'aucun process.

    `fixtures` (#183) branche les **contrats d'API v2** (routes des Phases 5/6 :
    registre de configuration, propositions de playbook globales) sur des
    **données factices**. Les exécutions (#185), le journal requêtable (#478)
    puis le flux SSE d'un fil de chat (#268) en sont sortis à mesure que leur lot
    était livré.
    None (production) : ces routes répondent **501** — le contrat est stable, son
    lot d'implémentation n'est pas encore livré. Fourni (la démo, #65) : elles
    servent les fixtures, et la voie front code contre elles sans backend réel.

    `fabrique_moteur` (#185) construit le moteur de chaque exécution lancée par
    `POST /api/executions` — par défaut `OrchestrationEngine.default`, résolu au
    **premier lancement** et non à la construction de l'app : une API qui ne
    lance aucun run ne résout aucun fournisseur. Les tests en injectent une
    fabrique factice pour exercer le pilotage sans appeler de modèle.

    `televersements` (#317) est le dépôt où `POST /api/sources` pose les octets
    reçus, et où le lancement retrouve par identifiant ce qu'il doit rattacher au
    run — par défaut celui de la config
    (`<MAESTRO_INGESTION_DIR|core/ingestion>/_televersements/`). Les tests en
    injectent un dépôt temporaire : c'est la seule façon de plafonner sans
    écrire dix mégaoctets sur le disque de qui joue la suite.

    `lecteur_sources` (#316) lit la matière d'un objectif et rend son rapport de
    lecture — par défaut `extraire_sources`. Injectable parce qu'une source `url`
    part sur le réseau : `tests/conftest.py` (#195) exige qu'aucun test n'en ait
    besoin.

    `hote_run` (#442) est **où** les exécutions se déroulent : le contrat d'hôte
    de run (`maestro.controltower.hote`). Même point d'injection que
    `fabrique_moteur`, et pour une raison voisine : la fabrique décide de *quoi*
    déroule le run, l'hôte de *où* il vit. C'est par lui que l'hôte survivant à
    l'API (#441) se câble, sans que les routes, les événements ni la projection en
    sachent quoi que ce soit. `None` laisse le service se donner
    `HoteRunEnProcess` — la tâche de fond, la configuration des tests et d'une démo
    mono-process ; la **production** câble l'hôte détaché, résolu depuis
    l'environnement par `create_default_app` (#446).
    """
    bus = bus if bus is not None else InMemoryEventBus()
    event_log = event_log if event_log is not None else InMemoryEventLog()
    journal = journal if journal is not None else ServiceJournal()
    battements = battements if battements is not None else RegistreBattementsMemoire()
    agents_store = agents_store if agents_store is not None else AgentStore.default()
    capacites = capacites if capacites is not None else CapacityStore.default()
    mcp = mcp if mcp is not None else McpStore.default()
    # La bibliothèque MCP a **trois sources** depuis #678 : le seed curé (en
    # code), les entrées admises (le journal, sur le disque) et le miroir du
    # registre officiel (sur le disque aussi). Un registre **injecté**
    # l'emporte toujours — c'est ainsi que les tests servent une allowlist
    # restreinte, et rien de ce qui suit ne s'applique à eux. Sinon on fédère à
    # la lecture, mémoïsé sur l'empreinte du miroir : la bibliothèque suit donc
    # un rafraîchissement sans redémarrage, et ne repaie la traduction de
    # 25 000 entrées qu'au moment où le miroir a effectivement bougé.
    registre_mcp_injecte = registre_mcp

    def registre() -> RegistreMcp:
        """La bibliothèque du moment : celle injectée, sinon la fédérée (mémoïsée)."""
        if registre_mcp_injecte is not None:
            return registre_mcp_injecte
        return federer_memo().registre

    # La **porte d'admission** (#678) : le geste humain tracé qui fait entrer une
    # entrée découverte dans l'allowlist. Le service écrit le journal
    # (`admissions.json`, à côté du pool) ; la bibliothèque le relit à la
    # composition suivante. ⚠ Un `registre_mcp` **injecté** ne relit rien : c'est
    # une allowlist figée, donc les routes d'admission écrivent bien mais leur
    # effet ne s'y verra pas — le contrat d'un registre injecté est justement de
    # ne dépendre d'aucun disque.
    admissions = admissions if admissions is not None else ServiceAdmission.default()
    secrets = secrets if secrets is not None else SecretStore.default()
    permissions = permissions if permissions is not None else PermissionStore.default()
    projets = projets if projets is not None else ServiceProjets.default()
    state = (
        state
        if state is not None
        else ControlTowerState(catalogue(agents_store), capacites=capacites.lister())
    )
    playbooks = playbooks if playbooks is not None else PlaybookStore.default()
    analyseur = analyseur if analyseur is not None else AnalyseurEchecs(playbooks=playbooks)
    mailbox = mailbox if mailbox is not None else InMemoryMailbox()
    chat_store = chat_store if chat_store is not None else ChatStore.default()
    # Un seul dépôt de téléversement (#317) pour la route qui reçoit les octets et
    # pour tout service qui les rattache : deux instances pointeraient le même
    # disque, mais l'injection des tests n'en tiendrait qu'une. Résolu **avant**
    # les services parce que le fil en dépend désormais aussi (#482) — un message
    # peut porter des sources, et il les résout par la même chaîne qu'un
    # lancement.
    televersements = (
        televersements if televersements is not None else DepotTeleversements.default()
    )
    chat = ServiceChat(
        store=chat_store,
        repondeur=(
            chat_repondeur if chat_repondeur is not None else RepondeurModele(playbooks=playbooks)
        ),
        mailbox=mailbox,
        bus=bus,
        televersements=televersements,
    )
    # Le canal d'aide (#123) : mêmes rouages que le chat — persistance, messagerie,
    # bus — pour que le fil se relise et se diffuse à l'identique ; seul le
    # répondeur change, l'assistant ne parlant pas du projet mais de l'outil. Le
    # dépôt en fait partie : l'assistance sert les mêmes endpoints, donc le même
    # contrat, et lui refuser les sources ferait dépendre un 422 du nom du fil.
    assistance = ServiceChat(
        store=chat_store,
        repondeur=(
            assistance_repondeur if assistance_repondeur is not None else RepondeurAssistance()
        ),
        mailbox=mailbox,
        bus=bus,
        televersements=televersements,
    )
    diffusion = Diffusion()
    # Pilotage des exécutions (#185) : lance sur le bus et la projection de
    # cette app — le run est donc suivi par les mêmes rouages que n'importe
    # quelle orchestration observée.
    executions = ServiceExecutions(
        bus,
        state,
        fabrique_moteur=fabrique_moteur,
        televersements=televersements,
        lecteur_sources=lecteur_sources,
        battements=battements,
        hote=hote_run,
    )

    async def ouvrir_un_run(objectif: str, projet_id: str | None = None) -> dict[str, Any]:
        """Le lanceur du fil global (#268) — un run sur l'objectif dicté au chat.

        `MODE_BRIEF_AUTO` et non le `humain` des lancements par l'écran : le
        cadrage d'une demande **est** la conversation qu'on est en train d'avoir,
        et renvoyer vers un écran de validation de brief pour la poursuivre
        couperait le fil en deux. Le run part donc, brief rédigé sans attendre ;
        l'écran des exécutions reste la voie de celui qui veut le valider avant.

        `projet_id` (#683) est le projet de la fenêtre, transmis par le message
        et **normalisé à la frontière** (`envoyer_chat`). Il est passé à `lancer`
        exactement comme le fait `POST /api/executions` — même paramètre, même
        validation de forme (`projet_id_valide`), donc une seule règle pour les
        deux portes d'entrée. Sans projet, le run part sans projet : le
        rattachement est une donnée, jamais une condition du lancement (#222).
        """
        return await executions.lancer(
            objectif, projet_id=projet_id, mode_brief=MODE_BRIEF_AUTO
        )

    # Le fil global (#268) : mêmes rouages que le chat — persistance, messagerie,
    # bus —, un répondeur qui peut ouvrir un run, et rien de plus côté REST. Il se
    # construit ici, et pas avec les deux autres, parce qu'il tient son lanceur du
    # service d'exécutions ci-dessus.
    orchestration = ServiceChat(
        store=chat_store,
        repondeur=(
            orchestration_repondeur
            if orchestration_repondeur is not None
            else RepondeurOrchestration(lanceur=ouvrir_un_run, apercu=apercu_de(state))
        ),
        mailbox=mailbox,
        bus=bus,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Rejeu du journal durable (#97) **avant** d'ouvrir la pompe : la
        # projection retrouve l'historique (exécutions, grands livres, analytics)
        # d'avant le redémarrage, puis la pompe prend le relais du flux à venir.
        # Le journal requêtable (#478) se remplit du **même** parcours : c'est ce
        # qui fait de lui une vue de l'historique durable et non un second
        # stockage — et ce qui lui donne des rangs, donc des identifiants
        # d'entrée, stables d'un redémarrage à l'autre.
        # Un journal illisible (Redis absent au démarrage…) est tracé sans bloquer
        # l'API : elle repart sur la projection courante (vide en production).
        try:
            for event in await event_log.relire():
                state.appliquer(event)
                journal.consigner(event)
        except Exception:
            _LOGGER.exception(
                "Rejeu du journal des événements impossible : démarrage sur la "
                "projection courante (l'historique persisté n'a pas pu être relu)."
            )
        pompe = asyncio.create_task(_pompe(bus, state, diffusion, event_log, journal))
        try:
            yield
        finally:
            # Les runs en vol s'arrêtent **avant** la pompe et le bus : leur
            # issue (annulation) a encore un canal pour être consignée.
            await executions.fermer()
            pompe.cancel()
            with suppress(asyncio.CancelledError):
                await pompe
            await mailbox.close()
            await bus.close()
            await event_log.close()
            await battements.close()

    app = FastAPI(
        title="Maestro — Control Tower",
        description="État de l'orchestration (REST) et flux d'événements (WebSocket).",
        lifespan=lifespan,
    )
    # L'UI (apps/web, ticket #47) est servie sur une autre origine que l'API
    # (Next.js sur :3000, API sur :8000) : sans CORS le navigateur bloque les
    # appels REST. Origines ouvertes au POC — l'API n'écoute qu'en local
    # (127.0.0.1, cf. cli.py) et ne porte aucune authentification à restreindre.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )

    @app.get("/api/sante")
    async def sante() -> dict[str, str]:
        """Vitalité du service (sonde de supervision)."""
        return {"statut": "ok"}

    @app.post("/api/extinction")
    async def eteindre() -> dict[str, Any]:
        """**Maestro s'éteint** : ses runs en vol sont soldés avec lui (#486).

        La porte de l'arrêt **volontaire**, et la seule : `scripts/controltower/
        start.sh --stop` l'appelle avant de libérer les ports, et la fermeture de
        l'enveloppe le fera le jour où elle existera. Chaque hôte détaché est éteint
        avec sa **descendance**, son run consigné `annulee` avec la cause
        `extinction` — donc jamais laissé `en_cours` —, et ses tâches soldées avec
        lui.

        ⚠ **Ce n'est pas l'arrêt de l'API.** Fermer la fenêtre du navigateur (#149),
        relancer après une modification, planter : ces trois-là passent par le
        `lifespan`, où l'hôte détaché ne touche à rien — c'est la propriété de #441
        et elle n'est pas défaite. La distinction ne se déduit d'aucun signal reçu
        ici : elle **descend** de l'appelant, seul à savoir qu'il arrête exprès
        (docs/28 §11).

        Rend les résumés des runs soldés — vide quand rien ne tournait, ce qui est
        le cas courant. `200` dans les deux cas : éteindre une Control Tower au
        repos n'est pas une erreur, et un code d'échec ferait chercher une panne là
        où il n'y avait simplement rien à éteindre. Ce que le run **redevient** est
        décrit par `POST …/relancer`, qui accepte un run soldé de la sorte.
        """
        soldes = await executions.eteindre()
        return {"runs": soldes, "nb": len(soldes)}

    # --- Contrats d'API v2 (#183) : routes des Phases 5/6, formes JSON figées ---
    # Le contrat (chemin, méthode, forme) est stable ; l'implémentation réelle
    # vient dans les lots dédiés (Phase 5, #184+). Sans `fixtures` (production),
    # ces routes répondent 501 ; branchées sur les fixtures (la démo), elles
    # servent des données factices contre lesquelles la voie front code (docs/05 §6).
    def _exige_fixtures() -> FixturesControlTower:
        """Les fixtures v2, ou un 501 explicite tant que le lot réel n'est pas livré."""
        if fixtures is None:
            raise HTTPException(
                status_code=501,
                detail=(
                    "route de contrat (API v2) non encore implémentée : servie en "
                    "fixtures par la démo (maestro.controltower.demo, #183)."
                ),
            )
        return fixtures

    def _portee(projet: str | None) -> PorteeProjet:
        """La **portée projet** d'une lecture (#277), ou un refus motivé.

        Le contrat unique des vues qui agrègent — Kanban, exécutions, coûts,
        validations, journal, flux temps réel : `?projet=<id>` pour un projet,
        `?projet=tous` pour la vue transverse, `?projet=aucun` pour les travaux
        hors projet. **Omis, le paramètre est refusé** (422 `projet-requis`) :
        une lecture sans périmètre n'est plus un mélange silencieux de tous les
        projets, et un refus motivé se diagnostique là où une liste vide se
        confondrait avec un projet sans activité. Un identifiant inconnu du
        dépôt sort en 404 `projet-inconnu`, par la même porte que les refus de
        `ServiceProjets` (#223) — motif, message, code.
        """
        try:
            return resoudre_portee(projet, projet_connu=projets)
        except PorteeRefusee as exc:
            raise _refus_projet(exc) from exc

    def _portee_run(run: str | None) -> PorteeRun:
        """La **portée run** d'une lecture (#473), ou un refus motivé.

        Le second périmètre, qui **s'ajoute** au premier sans le remplacer :
        `?run=<run_id>` restreint aux tâches que ce run a portées, `?projet=`
        restant obligatoire à côté. Omis, il ne restreint rien — c'est un
        paramètre facultatif, et l'absence y est la lecture normale d'avant ce
        lot, non le mélange silencieux que #277 refusait. Un run dont la
        projection n'a aucune trace sort en 404 `run-inconnu`, par la porte de
        `projet-inconnu` et pour la même raison : une liste vide se lirait « ce
        run n'a rien fait ».
        """
        try:
            return resoudre_portee_run(
                run, run_connu=lambda run_id: state.execution(run_id) is not None
            )
        except PorteeRefusee as exc:
            raise _refus_projet(exc) from exc

    @app.get("/api/journal")
    async def journal_requetable(
        agent: str | None = None,
        type: str | None = None,
        run_id: str | None = None,
        projet: str | None = None,
        depuis: str | None = None,
        jusqua: str | None = None,
        tri: str = TRI_JOURNAL_HORODATAGE,
        ordre: str = ORDRE_DESC,
        page: int = 1,
        taille: int = TAILLE_PAGE_DEFAUT,
    ) -> dict[str, Any]:
        """Le journal requêtable : filtres (agent / type / run / projet / période), tri, pagination.

        Servi **en mode réel** depuis #478 : l'historique des événements consignés
        par la pompe et rejoués au démarrage (`ServiceJournal`, journal durable
        #97). La gate 501 qui le murait hors démo a disparu avec l'implémentation
        qu'elle annonçait, et les fixtures du journal avec elle — les exécutions
        avaient ouvert la voie (#185).

        `depuis`/`jusqua` sont des horodatages ISO-8601 (bornes incluses).
        `projet` est **obligatoire** (#277) et suit le contrat commun :
        `<id>` | `tous` | `aucun` — 422 `projet-requis` s'il manque, 404
        `projet-inconnu` sur un identifiant non déclaré. 422 aussi sur un
        `tri`/`ordre` inconnu, une `page` < 1 ou une `taille` hors [1, {max}].
        """
        portee = _portee(projet)
        if tri not in TRIS_JOURNAL:
            raise HTTPException(
                status_code=422,
                detail=f"tri invalide : {tri} (attendus : {', '.join(TRIS_JOURNAL)}).",
            )
        if ordre not in ORDRES_JOURNAL:
            raise HTTPException(
                status_code=422,
                detail=f"ordre invalide : {ordre} (attendus : {', '.join(ORDRES_JOURNAL)}).",
            )
        if page < 1:
            raise HTTPException(status_code=422, detail=f"page invalide : {page} (attendu ≥ 1).")
        if not 1 <= taille <= TAILLE_PAGE_MAX:
            raise HTTPException(
                status_code=422,
                detail=f"taille invalide : {taille} (attendu entre 1 et {TAILLE_PAGE_MAX}).",
            )
        return journal.page(
            agent=agent,
            type=type,
            run_id=run_id,
            portee=portee,
            depuis=depuis,
            jusqua=jusqua,
            tri=tri,
            ordre=ordre,
            page=page,
            taille=taille,
        )

    @app.get("/api/configuration")
    async def configuration() -> dict[str, Any]:
        """Le registre de configuration éditable (couche 1 du cadrage sécurité #182).

        Les réglages produit (fournisseur, modèle, plafonds, isolation,
        intégrations, rétention) : type, valeur courante (masquée si secret),
        valeur par défaut, s'ils sont modifiables. Liste blanche stricte : aucune
        écriture arbitraire de variable d'environnement.
        """
        return _exige_fixtures().configuration()

    # Enregistrée **avant** `/api/playbooks/{agent}` (plus bas) pour que le chemin
    # littéral l'emporte sur la capture `{agent}` (sinon agent = "propositions").
    @app.get("/api/playbooks/propositions")
    async def propositions_playbook_globales() -> list[dict[str, Any]]:
        """Les propositions d'auto-amélioration, **tous agents confondus** (#111 global).

        L'agrégat transverse qui alimente le badge d'attente et les notifications
        (cadrage #182, items 8/9) — chaque proposition enrichie du `role` de son
        agent. Le pendant temps réel est l'événement `playbook.proposition` du bus.
        """
        return _exige_fixtures().propositions_playbook()

    @app.get("/api/taches")
    async def taches(projet: str | None = None, run: str | None = None) -> list[dict[str, Any]]:
        """Les tâches connues : statut, agent, coût détaillé (#57) — la source du Kanban.

        `projet` est **obligatoire** (#277) : `<id>` cadre le Kanban sur un
        projet, `tous` rend la vue transverse, `aucun` les tâches hors projet.
        Omis, 422 `projet-requis` ; inconnu, 404 `projet-inconnu`. Une tâche
        sans projet n'apparaît dans la vue d'aucun projet — on ne devine pas à
        quel projet elle appartiendrait.

        `run` est **facultatif** et s'**ajoute** au précédent (#473) : il
        restreint aux tâches que ce run a portées, sans dispenser de dire sur
        quel projet on lit — un run appartient à un projet, les deux filtres se
        composent donc au lieu de se relayer. C'est la source du Kanban **d'un
        run**, et elle compte exactement les tâches que sa `progression` répartit
        (`GET /api/executions/{run_id}`). 404 `run-inconnu` sur un run dont la
        projection n'a aucune trace.
        """
        return [t.to_dict() for t in state.taches(_portee(projet), _portee_run(run))]

    @app.get("/api/agents")
    async def agents() -> list[dict[str, Any]]:
        """L'état des agents : libre/occupé, tâche courante, compteurs, coût cumulé."""
        return [a.to_dict() for a in state.agents()]

    @app.get("/api/executions")
    async def executions_liste(projet: str | None = None) -> list[dict[str, Any]]:
        """Les runs connus (#185) : résumés, **récents d'abord**.

        En cours comme passés, lancés depuis la Control Tower comme publiés par
        un autre process (`maestro-run --publier`) : le suivi ne distingue pas
        leur origine — la projection est la même. C'est la source de l'écran
        *Exécutions & traces* (docs/05 §2.4). `projet` est **obligatoire**
        (#277), au contrat commun : `<id>` | `tous` | `aucun`.

        Chaque résumé porte de quoi **dresser une liste utile** sans un appel par
        ligne (#473) : état, objectif, `progression` par statut de tâche, début
        et coût cumulé.
        """
        return await executions.resumes(_portee(projet))

    @app.post("/api/executions", status_code=202)
    async def lancer_execution(requete: LancementExecutionRequete) -> dict[str, Any]:
        """Lance une exécution (#185) et rend son résumé, `run_id` compris, **aussitôt**.

        Le run se déroule **hors** de la requête HTTP — et, depuis #446, hors du
        process de l'API : il part chez son **hôte** (`MAESTRO_HOTE_RUN`, un
        process détaché par défaut), qui lui survit. La réponse ne dit donc ni ce
        qu'il a produit ni même qu'il ira au bout : elle dit qu'il est parti. La
        suite arrive par le flux d'événements existant — chaque étape devient un
        `tache.statut` du WebSocket et une ligne du Kanban — et se relit sur
        `GET /api/executions/{run_id}`. 422 sur un objectif vide, un garde-fou
        hors bornes (les plafonds sont des maximums : ils doivent être > 0) ou
        une **source** refusée (#317).

        Avec des `sources`, la réponse porte en plus le **rapport de lecture**
        (#316) et n'est plus instantanée : la matière est lue avant qu'elle ne
        parte, sans quoi le rapport n'aurait rien à dire (docs/05 §6.1).
        """
        try:
            return await executions.lancer(
                requete.objectif,
                plafond_cout_usd=requete.plafond_cout_usd,
                plafond_tokens=requete.plafond_tokens,
                timeout_tache_s=requete.timeout_tache_s,
                parallelisme=requete.parallelisme,
                ticket=None if requete.ticket is None else requete.ticket.en_reference(),
                projet_id=requete.projet_id,
                sources=requete.sources_declarees(),
                mode_brief=requete.brief,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=_detail_refus(exc)) from exc

    @app.post("/api/sources", status_code=201)
    async def televerser_sources(
        fichier: Annotated[list[UploadFile], File()],
    ) -> dict[str, Any]:
        """Téléverse un ou plusieurs fichiers (#317) et rend leurs **identifiants de source**.

        Le champ `fichier` est répétable : un formulaire qui dépose trois
        documents fait un appel, pas trois. Les octets vont dans le dépôt de
        téléversement — hors de tout projet et hors de tout run —, et c'est le
        lancement qui les rattachera au run qui les consomme (docs/05 §6.8).

        Les plafonds d'ingestion (ENF-07) s'appliquent **pendant** la réception :
        le nombre de fichiers d'abord, sans toucher au disque, puis la taille de
        chacun et le cumul de l'appel, tranche par tranche. Un refus est motivé
        (422, `{motif, message, index}`) et **ne laisse rien** : les fichiers
        déjà acceptés dans le même appel sont retirés du dépôt, faute de quoi ils
        y resteraient sous des identifiants que personne n'a reçus.
        """
        plafond = televersements.garde_fous.nb_max_sources
        if plafond is not None and len(fichier) > plafond:
            raise HTTPException(
                status_code=422,
                detail={
                    "motif": "trop-de-sources",
                    "message": (
                        f"Trop de fichiers dans un même téléversement : {len(fichier)} "
                        f"reçus, {plafond} au maximum."
                    ),
                },
            )
        recus: list[dict[str, Any]] = []
        total = 0
        try:
            for index, envoi in enumerate(fichier):
                # Dans un fil : la copie des octets est du disque, pas de
                # l'attente réseau — la boucle de l'API ne doit pas la porter.
                televerse = await asyncio.to_thread(
                    televersements.accueillir,
                    envoi.filename or "",
                    envoi.file,
                    index=index,
                    deja_recu=total,
                )
                total += televerse.taille
                recus.append(televerse.to_dict())
        except SourceRefusee as exc:
            for accepte in recus:
                televersements.oublier(str(accepte["id"]))
            raise HTTPException(status_code=422, detail=_detail_refus(exc)) from exc
        return {"sources": recus, "total_octets": total}

    @app.post("/api/executions/{run_id}/annuler")
    async def annuler_execution(run_id: str) -> dict[str, Any]:
        """Interrompt un run en cours (#185) : rend son résumé passé à « annulée ».

        L'issue est consignée comme n'importe quel fait du run : elle apparaît
        dans la projection (statut `annulee`, `fin` posée) **et** sur le flux
        d'événements. 404 si le run est inconnu, 409 s'il est déjà soldé — un run
        terminé n'est plus interruptible, et le dire vaut mieux que faire croire
        à une annulation.
        """
        resume = executions.resume(run_id)
        if resume is None:
            raise HTTPException(
                status_code=404,
                detail=f"exécution inconnue : {run_id} (voir GET /api/executions).",
            )
        if resume["statut"] in STATUTS_EXECUTION_TERMINAUX:
            raise HTTPException(
                status_code=409,
                detail=f"exécution déjà soldée ({resume['statut']}) : {run_id}.",
            )
        annulee = await executions.annuler(run_id)
        if annulee is None:  # pragma: no cover - le résumé vient d'être lu
            raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
        return annulee

    @app.post("/api/executions/{run_id}/pause")
    async def suspendre_execution(run_id: str) -> dict[str, Any]:
        """Suspend un run en cours (#477) : rend son résumé passé à « en pause ».

        **Aucune tâche nouvelle n'est lancée ; celles qui sont en vol vont à leur
        terme.** C'est ce qui sépare cette route d'`…/annuler`, où les tâches sont
        tuées là où elles en sont et perdent leur travail. Le run n'est pas soldé,
        il ne change même pas de statut : `en_pause` est un drapeau **à côté** du
        statut, si bien qu'un run suspendu pendant l'attente de son brief continue
        de montrer qu'il attend ce brief.

        L'ordre traverse la frontière d'exécution par le **bus**, comme
        l'annulation (#444) : l'événement consigné ici est celui que le process
        détaché guette. Il survit donc au redémarrage de l'API — il est dans le
        journal durable (#97), rejoué au démarrage suivant.

        Le run **bat toujours** pendant sa pause (#348) : un run suspendu qui
        cesserait de battre ressortirait `orphelin` au bout d'une demi-heure, et
        #349 proposerait de le relancer depuis son brief, c'est-à-dire de repayer
        le cadrage d'un run qui n'a rien perdu.

        `404` si le run est inconnu, `409` s'il est **déjà soldé** (il n'y a plus
        rien à suspendre) ou **déjà suspendu** — répondre 200 à une pause qui
        n'était pas la première ferait passer pour un geste ce qui n'en est pas un.
        """
        resume = executions.resume(run_id)
        if resume is None:
            raise HTTPException(
                status_code=404,
                detail=f"exécution inconnue : {run_id} (voir GET /api/executions).",
            )
        if resume["statut"] in STATUTS_EXECUTION_TERMINAUX:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"exécution déjà soldée ({resume['statut']}) : {run_id} — "
                    "il n'y a rien à suspendre d'un run qui a rendu son issue."
                ),
            )
        if resume["en_pause"]:
            raise HTTPException(
                status_code=409,
                detail=f"exécution déjà suspendue : {run_id} (POST …/reprendre pour la relancer).",
            )
        suspendue = await executions.mettre_en_pause(run_id)
        if suspendue is None:  # pragma: no cover - le résumé vient d'être lu
            raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
        return suspendue

    @app.post("/api/executions/{run_id}/reprendre")
    async def reprendre_execution(run_id: str) -> dict[str, Any]:
        """Reprend un run suspendu **là où il en était** (#477) : son résumé remis en route.

        Le plan, les tâches déjà terminées, le brief approuvé, le coût engagé : rien
        n'a bougé pendant la pause, puisque rien n'a été tué. Cette route ne
        reconstruit donc rien — elle rouvre la porte, et les tâches qui attendaient
        repartent.

        ⚠ À ne pas confondre avec `…/relancer` (#349), qui rejoue un run **mort**
        depuis son brief approuvé et repaie une planification : c'est un **nouveau**
        run, avec un nouvel identifiant. Ici il n'y a qu'un run, le même, qui
        reprend son travail — repayer le cadrage n'est pas une reprise.

        `404` si le run est inconnu, `409` s'il n'est **pas suspendu** : il n'y a
        rien à reprendre d'un run qui travaille, et le dire vaut mieux que rendre un
        200 sans effet.
        """
        resume = executions.resume(run_id)
        if resume is None:
            raise HTTPException(
                status_code=404,
                detail=f"exécution inconnue : {run_id} (voir GET /api/executions).",
            )
        if not resume["en_pause"]:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"exécution non suspendue ({resume['statut']}) : {run_id} — "
                    "il n'y a rien à reprendre d'un run qui n'a pas été mis en pause."
                ),
            )
        reprise = await executions.reprendre(run_id)
        if reprise is None:  # pragma: no cover - le résumé vient d'être lu
            raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
        return reprise

    @app.post("/api/executions/{run_id}/relancer", status_code=202)
    async def relancer_execution(run_id: str) -> dict[str, Any]:
        """Rejoue un run interrompu **sur son brief approuvé** (#349) : le nouveau résumé.

        Un run dont l'hôte est tombé emporte un cadrage **payé et validé par un
        humain** — clarification, brief, approbation. Ce cadrage est intégralement
        conservé dans la projection : cette route le rejoue en mode `sans`, donc sans
        repasser par la clarification ni par la validation, en conservant le projet
        et le ticket du run repris.

        La réponse est celle d'un lancement (`202` + `ResumeExecution`) parce que
        c'en est un : le run relancé est un **nouveau** run, qui porte `reprise_de`
        — de qui il est la suite. Le run repris, lui, est soldé en `annulee` : rien
        n'a raté, son hôte est tombé et quelqu'un a repris la main.

        `404` si le run est inconnu, `409` s'il est **déjà soldé** (rien à reprendre)
        ou **encore vivant** (verdict de `vitalite`, #348 — l'interrompre d'abord si
        c'est bien voulu), `422` si son brief n'a **jamais été approuvé** : le
        relancer reviendrait à repartir de son objectif brut en silence, c'est-à-dire
        à sauter la validation qu'il attendait encore. Le refus est motivé à la
        convention du reste (`{motif, message}`, §6.1).
        """
        try:
            return await executions.relancer(run_id)
        except RelanceRefusee as refus:
            raise HTTPException(
                status_code=_CODE_REFUS_RELANCE.get(refus.motif, 422),
                detail=_detail_refus(refus),
            ) from refus

    @app.post("/api/executions/{run_id}/brief/decision")
    async def decider_brief(
        run_id: str, requete: DecisionBriefRequete
    ) -> dict[str, Any]:
        """Tranche le brief d'un run suspendu (#320, décision D5) : il repart, ou il s'arrête.

        **Approuver** relance la décomposition, et ce qui sera décomposé est le
        brief tel qu'il vient d'être approuvé — la version corrigée si le corps en
        porte une. **Refuser** solde le run en « annulée » : rien de payant n'aura
        été engagé au-delà du brief lui-même.

        Même mécanique que la décision de validation (#48) et pour les mêmes
        raisons : l'état est appliqué **d'abord** (le REST répond déjà à jour) puis
        l'événement est publié — le moteur, en attente sur ce même bus, reprend ou
        s'arrête, et la pompe réapplique l'événement sans effet (idempotence).

        404 si le run est inconnu, **409 s'il n'attend pas de décision** (jamais
        tranché deux fois, et surtout pas un run soldé ramené en vol), 422 si le
        brief corrigé n'est pas conforme au schéma partagé — un refus qui coûte un
        aller-retour à celui qui le soumet, plutôt qu'un run qui échoue plus tard.
        """
        resume = executions.resume(run_id)
        if resume is None:
            raise HTTPException(
                status_code=404,
                detail=f"exécution inconnue : {run_id} (voir GET /api/executions).",
            )
        if resume["statut"] != EXECUTION_EN_ATTENTE_BRIEF:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"cette exécution n'attend pas de décision sur son brief "
                    f"({resume['statut']}) : {run_id}."
                ),
            )
        corrige = None
        if requete.approuve and requete.brief is not None:
            try:
                validate_brief(requete.brief, where="brief corrigé")
            except BriefValidationError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            corrige = brief_depuis(requete.brief)
        event = Event(
            type=EVENEMENT_BRIEF_DECISION,
            run_id=run_id,
            titre=resume["objectif"],
            agent=ACTEUR_BRIEF,
            role=ROLE_BRIEF,
            statut=BRIEF_APPROUVE if requete.approuve else BRIEF_REFUSE,
            detail=(
                "brief approuvé depuis la Control Tower"
                if requete.approuve
                else "brief refusé depuis la Control Tower : rien n'a été décomposé"
            ),
            # La **correction**, ou None quand il n'y en a pas — jamais une recopie
            # du brief proposé. « Absent » veut dire « celui d'avant tient », et
            # cette lecture-là est faite au même endroit par la projection et par le
            # moteur (`DecisionBrief.retenu`), donc énoncée une seule fois.
            brief=corrige,
            projet_id=resume["projet_id"],
        )
        state.appliquer(event)
        await bus.publish(event)
        return await executions.resume_vivant(run_id) or resume

    @app.post("/api/executions/{run_id}/brief/reponses")
    async def repondre_brief(
        run_id: str, requete: ReponsesBriefRequete
    ) -> dict[str, Any]:
        """Répond aux questions de clarification d'un brief (#321) : le run repart.

        Le run avait publié ses questions et s'était suspendu (`en_attente_reponses`,
        état non terminal — il est resté annulable tout du long). Les réponses le
        relancent : il **régénère son brief entier** en les intégrant, puis repose
        des questions s'il en reste et que le plafond le permet, sinon passe en
        validation.

        Même mécanique que la décision de brief (#320) et pour les mêmes raisons :
        l'état est appliqué **d'abord** (le REST répond déjà à jour) puis l'événement
        est publié — le moteur, en attente sur ce même bus, reprend, et la pompe
        réapplique l'événement sans effet (idempotence).

        404 si le run est inconnu, **409 s'il n'attend pas de réponses** (jamais
        répondu deux fois, jamais un run soldé ramené en vol), 422 si le nombre de
        réponses ne correspond pas aux questions du brief stocké — l'appariement est
        positionnel, donc une liste décalée affecterait des réponses aux mauvaises
        questions sans que rien ne le signale.
        """
        resume = executions.resume(run_id)
        if resume is None:
            raise HTTPException(
                status_code=404,
                detail=f"exécution inconnue : {run_id} (voir GET /api/executions).",
            )
        if resume["statut"] != EXECUTION_EN_ATTENTE_REPONSES:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"cette exécution n'attend pas de réponses sur son brief "
                    f"({resume['statut']}) : {run_id}."
                ),
            )
        detail = state.execution(run_id)
        attendues = len(detail.brief.questions) if detail and detail.brief else 0
        if len(requete.reponses) != attendues:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{len(requete.reponses)} réponse(s) pour {attendues} question(s) : "
                    "les réponses s'apparient par position aux questions du brief en "
                    "attente. Répondre « » (chaîne vide) laisse une question sans "
                    "réponse ; elle partira en hypothèse."
                ),
            )
        event = Event(
            type=EVENEMENT_BRIEF_REPONSES,
            run_id=run_id,
            titre=resume["objectif"],
            agent=ACTEUR_BRIEF,
            role=ROLE_BRIEF,
            detail=(
                f"{sum(1 for r in requete.reponses if r.strip())}/{attendues} "
                "question(s) répondue(s) depuis la Control Tower"
            ),
            # **Pas** expurgées, et c'est le même choix assumé que pour le brief
            # (cf. `evenement_demande_brief`, #320) : ces réponses ne voyagent pas
            # pour être affichées, elles voyagent pour **atteindre le moteur**, qui
            # les intègre au brief régénéré. Les masquer ici ne protégerait rien —
            # le brief qui en sort circule déjà en clair sur le même bus — mais
            # corromprait l'entrée de la régénération, et un `[REDACTED]` au milieu
            # d'une réponse produirait un brief faux sans que personne le voie.
            reponses=list(requete.reponses),
            tour=resume.get("tour_clarification", 0),
            tours_max=resume.get("tours_clarification_max", 0),
            projet_id=resume["projet_id"],
        )
        state.appliquer(event)
        await bus.publish(event)
        return await executions.resume_vivant(run_id) or resume

    @app.get("/api/executions/{run_id}")
    async def execution(run_id: str) -> dict[str, Any]:
        """L'état d'une exécution (#185) : son résumé, sa trace et son coût.

        Le résumé de `GET /api/executions` (objectif, statut, volume, bornes,
        **vitalité** #348, **progression** #473) enrichi de ce qu'il ne porte
        pas : le grand livre du run (#57) et sa trace événement par événement.

        La `progression` est comptée **ici**, sur la machine à états du moteur
        (docs/03 §3) : à faire, en cours, bloquées, terminées, échecs — jamais
        recomptée par le front, qui ne verrait de toute façon que les tâches
        qu'il a chargées. Les tâches ainsi comptées sont exactement celles que
        rend `GET /api/taches?projet=…&run=<run_id>`.
        """
        detail = state.execution(run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
        return {**detail.to_dict(), **(await executions.resume_vivant(run_id) or {})}

    @app.get("/api/executions/{run_id}/cout")
    async def cout_execution(run_id: str) -> dict[str, Any]:
        """Le grand livre d'une exécution (#57) : coût par tâche et agrégat du run.

        La comptabilité du lot #55 (forme `RunCost.to_dict`) reconstruite du
        flux d'événements : `planification` (l'usage de l'orchestrateur),
        `taches` (tokens entrée/sortie, coût estimé, durée par tâche) et
        `total` (l'agrégat de l'exécution) — sans la trace événement par
        événement du détail. 404 si aucune trace reçue pour ce `run_id`.
        """
        detail = state.execution(run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
        return detail.cout.to_dict()

    @app.get("/api/executions/{run_id}/graphe")
    async def graphe_execution(run_id: str) -> dict[str, Any]:
        """Le **graphe** d'une exécution (#490) : nœuds, arêtes, branches parallèles.

        La troisième lecture d'un run, à côté du Kanban (« combien dans quel
        état ») et de la progression (« où en est-on ») : celle qui dit **quoi
        après quoi**. Un nœud par tâche du plan — agent, statut, checklist, coût,
        durée —, une arête par dépendance, et les nœuds rangés par `niveaux` :
        deux tâches sans dépendance entre elles y tombent au **même** niveau,
        donc se lisent comme parallèles au lieu de paraître séquentielles.

        Rien n'est à recalculer côté client : `niveau`/`rang` par nœud,
        `compartiment` (la table partagée de la progression, #473),
        `profondeur`, `largeur` et `plat` sont servis.

        Un plan **sans aucune dépendance** rend un graphe plat et le dit
        (`plat: true`, tous les nœuds au niveau 0) : c'est le cas courant, pas un
        graphe vide. `plan_connu: false` marque le cas qu'on ne peut pas
        deviner — un run qui n'a jamais publié son plan, dont les nœuds sont
        alors reconstruits de ses seules tâches vues, sans arête.

        La mise à jour **en direct** passe par le flux existant, sans second
        canal : le graphe se recompose à chaque lecture depuis la projection, et
        ce sont donc `run.plan` (le plan est connu), `tache.statut` (un nœud
        démarre, une arête s'allume) et `tache.detail` (une étape se coche, #489)
        qui le font bouger. 404 si aucune trace reçue pour ce `run_id`.
        """
        if state.execution(run_id) is None:
            raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
        return state.graphe(run_id).to_dict()

    @app.get("/api/executions/{run_id}/frise")
    async def frise_execution(run_id: str) -> dict[str, Any]:
        """La **frise d'activité** d'une exécution (#355) : ce que les agents font et se disent.

        La quatrième lecture d'un run, à côté du Kanban (« combien dans quel
        état »), de la progression (« où en est-on ») et du graphe (« quoi après
        quoi ») : celle qui dit **dans quel ordre**. Les changements de statut de
        tâche et les messages inter-agents sur une même frise, triés dans le
        temps, chaque entrée portant son agent, son horodatage et son objet.

        Les entrées sont rangées en **couloirs** — un par agent que le run a
        employé, y compris muet —, et un couloir de **repli** recueille ce qui n'a
        pas d'agent résoluble : une tâche bloquée n'a jamais été routée, donc le
        moteur y consigne un tiret (`_consigne_blocage`), qui n'est pas un nom
        d'agent. Chaque entrée porte son `couloir`, et ce couloir est toujours
        dans la liste servie : aucune entrée ne se perd faute de file.

        Une tâche **bloquée**, une tâche **en attente de validation humaine** et
        une tâche **en cours** portent trois `statut` distincts — c'est le cas
        d'usage qui a motivé le ticket : une attente de décision y cesse d'être
        indiscernable d'un travail en cours. `en_attente_validation` est résolu
        depuis `validation.demande`, qui **est** l'instant où la tâche s'arrête
        sur un humain ; la file `GET /api/validations` en dit l'état courant, pas
        la seconde.

        Rien n'est créé : les trois flux fusionnés sont déjà persistés et déjà
        servis par `GET /api/journal?run_id=…`, dont chaque entrée garde ici son
        identifiant. Comme le graphe, la frise **n'a pas d'événement à elle** :
        elle se recompose à la lecture, donc la mise à jour en direct passe par
        le flux existant, sans second canal.

        Bornée à `PLAFOND_FRISE` entrées, les plus **récentes** : `total` et
        `tronquee` disent ce qui a été laissé de côté — une borne muette ferait
        passer un run d'une heure pour un run de cinq cents lignes. 404 si aucune
        trace reçue pour ce `run_id`.
        """
        if state.execution(run_id) is None:
            raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
        return frise_du_run(
            run_id,
            journal.entrees_du_run(run_id),
            agents=state.agents_du_run(run_id),
        ).to_dict()

    @app.post("/api/sources/apercu")
    async def apercu_ingestion(
        sources: Annotated[str, Form()] = "[]",
        fichier: Annotated[list[UploadFile] | None, File()] = None,
    ) -> dict[str, Any]:
        """Ce que des sources **donneraient**, sans lancer et sans rien conserver (#319).

        Le pendant gratuit du rapport rendu par le lancement (docs/05 §6.1) :
        même résolution (#315), même extraction (#316), mêmes plafonds — et rien
        au bout. C'est ce qui rend le geste de composer un objectif **réversible
        tant qu'il est gratuit** : on voit ce qui sera lu, ignoré ou tronqué, et
        ce que ça coûtera, avant de dépenser la première tâche.

        Le corps est un **multipart** parce qu'un aperçu porte des octets et non
        des identifiants : il ne dépose rien dans le dépôt de téléversement
        (§6.8), qui n'existe que pour faire **survivre** une matière jusqu'au run
        qui la consomme. `sources` est le JSON des sources déclarées, **dans
        l'ordre de l'écran** — celui qui décide de ce qui entre quand le budget de
        tokens s'épuise — et `fichier` est répétable : le n-ième correspond à la
        n-ième source de type `fichier`.

        Toujours du texte, jamais une panne : une source illisible est une
        **ligne** du rapport (« ignoré », avec son motif). Le 422 motivé
        (`{motif, message, index}`) est réservé à ce que la résolution **refuse**
        — un type inconnu, une racine interdite, un plafond dépassé —, c'est-à-dire
        à ce qu'une correction de saisie répare.
        """
        try:
            declarations = json.loads(sources or "[]")
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "motif": "sources-illisibles",
                    "message": f"Sources illisibles : {exc.msg}.",
                },
            ) from exc
        envois = [(envoi.filename or "", envoi.file) for envoi in fichier or []]
        try:
            # Dans un fil : l'extraction ouvre des fichiers et peut récupérer une
            # page (#316), ce que la boucle de l'API ne doit pas porter.
            rapport = await asyncio.to_thread(
                apercu_sources, declarations, fichiers=envois
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=_detail_refus(exc)) from exc
        return rapport.to_dict()

    @app.get("/api/analytics/couts")
    async def analytics_couts(
        depuis: str | None = None, pas: str = PAS_HEURE, projet: str | None = None
    ) -> dict[str, Any]:
        """La vue coûts & analytics (#87) : agrégats transverses et série temporelle.

        Recalculée des exécutions projetées, avec la même convention
        d'attribution que le grand livre d'un run (#57) : coût agrégé par
        tâche, par agent (planification comprise) et par exécution, total, et
        série temporelle du coût en seaux de `pas` (minute/heure/jour).
        `depuis` (ISO-8601, réputé UTC sans fuseau) restreint la fenêtre — la
        période sélectionnable de l'UI. `projet` est **obligatoire** (#277) et
        restreint la dépense : seuls les événements que la portée retient
        comptent, planification comprise — un travail sans projet n'entre dans
        le total d'aucun projet. La réponse rappelle la `portee` servie, un
        total ne se lisant pas sans savoir de quoi il est le total. 422 sur un
        `pas` ou un `depuis` invalide.
        """
        portee = _portee(projet)
        if pas not in PAS_VALIDES:
            raise HTTPException(
                status_code=422,
                detail=f"pas invalide : {pas} (attendus : {', '.join(PAS_VALIDES)})",
            )
        borne = None
        if depuis is not None:
            try:
                borne = datetime.fromisoformat(depuis)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"depuis invalide : {depuis} (attendu : horodatage ISO-8601)",
                ) from exc
            if borne.tzinfo is None:
                borne = borne.replace(tzinfo=UTC)
        # La portée est appliquée **événement par événement** par l'agrégat (un
        # run peut en mélanger) : la liste des exécutions lui arrive entière.
        return agrege_couts(
            state.executions(), depuis=borne, pas=pas, portee=portee
        ).to_dict()

    @app.post("/api/taches/{tache_id}/reassigner")
    async def reassigner(tache_id: str, requete: ReassignationRequete) -> dict[str, Any]:
        """Réassigne manuellement une tâche à un agent (glisser-déposer du Kanban).

        Applique l'événement à l'état (le REST répond déjà à jour) puis le
        publie sur le bus — les clients WebSocket voient la réassignation, et
        la pompe la réapplique sans effet (idempotence). 404 si la tâche est
        inconnue, 422 si l'agent ne l'est pas — ou s'il est **désactivé** (#86 :
        un agent désactivé ne reçoit plus de tâches, réassignation comprise).
        """
        tache = state.tache(tache_id)
        if tache is None:
            raise HTTPException(status_code=404, detail=f"tâche inconnue : {tache_id}")
        agent = state.agent(requete.agent)
        if agent is None:
            raise HTTPException(
                status_code=422,
                detail=f"agent inconnu : {requete.agent} (voir GET /api/agents)",
            )
        if not agent.actif:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"agent désactivé : {agent.nom} — il ne reçoit plus de tâches "
                    "(le réactiver via POST /api/agents/{nom}/capacite)."
                ),
            )
        event = Event(
            type=EVENEMENT_TACHE_REASSIGNATION,
            run_id=tache.run_id,
            tache_id=tache_id,
            titre=tache.titre,
            agent=agent.nom,
            role=agent.role,
            statut="assignee",
            detail="réassignation manuelle (Control Tower)",
            # Le projet de la tâche (#277) voyage avec l'événement : sans lui,
            # un geste posé **depuis** la Control Tower d'un projet sortirait de
            # son propre flux temps réel, la portée ne retenant que ce qui porte
            # l'appartenance. La projection l'a déjà, la relayer suffit.
            projet_id=tache.projet_id,
        )
        state.appliquer(event)
        await bus.publish(event)
        return tache.to_dict()

    @app.post("/api/taches/{tache_id}/reference")
    async def poser_reference(
        tache_id: str, requete: ReferenceTicketRequete
    ) -> dict[str, Any]:
        """Rattache une tâche au **ticket externe** dont elle relève (#187).

        Le second chemin de pose, celui de l'exécution en cours : un agent
        équipé du serveur MCP de son outil de ticketing (#104) découvre le
        ticket au fil de sa tâche et le pose ici — l'autre chemin étant le
        lancement du run, qui part déjà d'un ticket. Ni cette route ni le moteur
        ne connaissent l'outil : ils transportent un identifiant et une URL.

        N'affecte **que** le ticket : ni statut, ni agent, ni coût ne bougent —
        dire d'où vient une tâche ne la fait pas changer de colonne. Applique
        l'événement à l'état (le REST répond déjà à jour) puis le publie sur le
        bus, où le journal durable le reprend : le ticket survit au redémarrage.
        404 si la tâche est inconnue, 422 si la référence n'apprend rien (ni
        identifiant, ni URL en http(s)).
        """
        tache = state.tache(tache_id)
        if tache is None:
            raise HTTPException(status_code=404, detail=f"tâche inconnue : {tache_id}")
        reference = requete.en_reference()
        if reference is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "référence vide : il faut au moins un identifiant lisible, "
                    "ou une URL en http(s)."
                ),
            )
        event = Event(
            type=EVENEMENT_TACHE_REFERENCE,
            run_id=tache.run_id,
            tache_id=tache_id,
            titre=tache.titre,
            detail=f"ticket externe {reference.id or reference.url}",
            ticket=reference,
            # Même raison qu'à la réassignation (#277) : l'appartenance de la
            # tâche accompagne l'événement, faute de quoi il n'atteindrait pas
            # le flux temps réel du projet dont il parle.
            projet_id=tache.projet_id,
        )
        state.appliquer(event)
        await bus.publish(event)
        return tache.to_dict()

    @app.post("/api/agents/{nom}/capacite")
    async def regler_capacite(nom: str, requete: CapaciteRequete) -> dict[str, Any]:
        """Règle la capacité d'un agent (#86, EF-21) : actif/désactivé, instances.

        Persiste le réglage dans le dépôt partagé — celui que moteur et workers
        relisent à chaque tâche : un agent désactivé ne reçoit plus de tâches,
        le plafond d'instances borne ses exécutions simultanées. Applique
        l'événement `agent.capacite` à l'état (le REST répond déjà à jour) puis
        le publie sur le bus — les fiches agents des clients WebSocket suivent
        en temps réel, et la pompe le réapplique sans effet (idempotence).
        404 si l'agent est inconnu, 422 si la requête ne règle rien ou si le
        plafond d'instances est invalide (< 1).
        """
        fiche = state.agent(nom)
        if fiche is None:
            raise HTTPException(
                status_code=404, detail=f"agent inconnu : {nom} (voir GET /api/agents)"
            )
        if requete.actif is None and requete.instances is None:
            raise HTTPException(
                status_code=422,
                detail="rien à régler : renseigner `actif` et/ou `instances`.",
            )
        courante = capacites.lire(nom)
        try:
            capacite = capacites.ecrire(
                CapaciteAgent(
                    nom=nom,
                    actif=courante.actif if requete.actif is None else requete.actif,
                    instances=(
                        courante.instances
                        if requete.instances is None
                        else requete.instances
                    ),
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        event = Event(
            type=EVENEMENT_AGENT_CAPACITE,
            agent=nom,
            role=fiche.role,
            statut=CAPACITE_ACTIVE if capacite.actif else CAPACITE_DESACTIVE,
            instances=capacite.instances,
            detail="capacité réglée depuis la Control Tower",
        )
        state.appliquer(event)
        await bus.publish(event)
        return fiche.to_dict()

    @app.get("/api/validations")
    async def validations(projet: str | None = None) -> list[dict[str, Any]]:
        """Les demandes de validation humaine (#48) : contexte, statut, décision.

        `projet` est **obligatoire** (#277), au contrat commun
        (`<id>` | `tous` | `aucun`) : une validation appartient au projet de sa
        tâche, et une Control Tower cadrée sur un projet n'a pas à faire
        arbitrer une action qui se déroule ailleurs.
        """
        return [v.to_dict() for v in state.validations(_portee(projet))]

    @app.post("/api/validations/{tache_id}/decision")
    async def decider(tache_id: str, requete: DecisionRequete) -> dict[str, Any]:
        """Tranche une demande de validation (#48) : la décision part vers le moteur.

        Applique la décision à l'état (le REST répond déjà à jour) puis la
        publie sur le bus — le moteur, en attente sur ce même bus, reprend la
        tâche (approbation) ou l'annule proprement (refus), et la pompe
        réapplique l'événement sans effet (idempotence). 404 si aucune demande
        pour cette tâche, 409 si elle est déjà tranchée (jamais deux décisions).

        Le `motif` d'un refus (#272) voyage dans le `detail` de l'événement, et
        nulle part ailleurs : c'est le champ que la projection recopie dans
        `decision`, donc celui que l'UI relit et que le journal durable conserve.
        Lui ouvrir un champ d'événement à lui aurait demandé de le faire traverser
        le schéma du journal pour un texte que `detail` porte déjà — au prix d'un
        second endroit où lire « pourquoi ce refus ».
        """
        demande = state.validation(tache_id)
        if demande is None:
            raise HTTPException(
                status_code=404, detail=f"aucune demande de validation : {tache_id}"
            )
        if not demande.en_attente:
            raise HTTPException(
                status_code=409,
                detail=f"demande déjà tranchée ({demande.statut}) : {tache_id}",
            )
        approuve = requete.approuve
        motif = "" if approuve else requete.motif.strip()
        event = Event(
            type=EVENEMENT_VALIDATION_DECISION,
            tache_id=tache_id,
            titre=demande.titre,
            agent=demande.agent,
            role=demande.role,
            statut=VALIDATION_APPROUVEE if approuve else VALIDATION_REFUSEE,
            detail=(
                "approuvée depuis la Control Tower"
                if approuve
                else f"refusée depuis la Control Tower — {motif}"
                if motif
                else "refusée depuis la Control Tower"
            ),
            # Le projet de la validation (#277), recollé par la projection depuis
            # sa tâche : la décision doit atteindre le flux du projet où elle se
            # joue — c'est ce que le moteur y attend.
            projet_id=demande.projet_id,
        )
        state.appliquer(event)
        await bus.publish(event)
        return demande.to_dict()

    def _exige_playbook_connu(agent: str) -> None:
        """404 si `agent` n'est pas un agent à playbook (la clé de `PLAYBOOK_DEFAUTS`).

        L'API n'édite que les playbooks des rôles du catalogue : pas de création
        de playbook orphelin par une simple faute de frappe dans l'URL.
        """
        if agent not in PLAYBOOK_DEFAUTS:
            raise HTTPException(
                status_code=404,
                detail=f"playbook inconnu : {agent} (voir GET /api/playbooks)",
            )

    def _fiche_playbook(agent: str, *, avec_contenu: bool) -> dict[str, Any]:
        """La fiche du playbook d'un agent : version courante et provenance.

        `version` 0 et `source` « defaut » tant que le playbook n'a jamais été
        édité : le contenu effectif est alors le prompt du code (#76, repli).
        `provenance` est celle de la version courante (« humain » — une proposition
        n'est jamais courante, #111), None quand le contenu vient du code.
        """
        defaut = PLAYBOOK_DEFAUTS[agent]
        courant = playbooks.lire(agent)
        fiche: dict[str, Any] = {
            "agent": agent,
            "role": defaut.role,
            "version": courant.version if courant else 0,
            "nb_versions": len(playbooks.numeros(agent)),
            "source": "stockage" if courant else "defaut",
            "provenance": courant.provenance if courant else None,
            "cree_le": courant.cree_le if courant else None,
        }
        if avec_contenu:
            fiche["contenu"] = courant.contenu if courant else defaut.contenu
        return fiche

    @app.get("/api/playbooks")
    async def playbooks_liste() -> list[dict[str, Any]]:
        """Les playbooks des agents (#76) : version courante et provenance de chacun."""
        return [_fiche_playbook(agent, avec_contenu=False) for agent in PLAYBOOK_DEFAUTS]

    @app.get("/api/playbooks/{agent}")
    async def playbook_courant(agent: str) -> dict[str, Any]:
        """Le playbook courant d'un agent : le contenu effectivement chargé par le moteur."""
        _exige_playbook_connu(agent)
        return _fiche_playbook(agent, avec_contenu=True)

    @app.put("/api/playbooks/{agent}")
    async def ecrire_playbook(agent: str, requete: PlaybookEcritureRequete) -> dict[str, Any]:
        """Publie une nouvelle version du playbook (le contenu intégral, pas un diff).

        La version créée devient la courante : elle sera chargée par les moteurs
        construits ensuite (l'application à chaud d'un moteur déjà en vie est le
        lot #78). 422 si le contenu est vide.
        """
        _exige_playbook_connu(agent)
        try:
            version = playbooks.ecrire(agent, requete.contenu)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return version.to_dict()

    @app.get("/api/playbooks/{agent}/versions")
    async def versions_playbook(agent: str) -> list[dict[str, Any]]:
        """L'historique consultable (EF-25) : les versions, de la première à la courante.

        Métadonnées seules — le contenu d'une version passée se lit sur
        `GET /api/playbooks/{agent}/versions/{version}`.
        """
        _exige_playbook_connu(agent)
        return [v.to_dict(avec_contenu=False) for v in playbooks.versions(agent)]

    @app.get("/api/playbooks/{agent}/versions/{version}")
    async def version_playbook(agent: str, version: int) -> dict[str, Any]:
        """Une version passée du playbook, contenu compris. 404 si elle n'existe pas."""
        _exige_playbook_connu(agent)
        lue = playbooks.lire(agent, version)
        if lue is None:
            raise HTTPException(
                status_code=404, detail=f"version inconnue : {agent} v{version}"
            )
        return lue.to_dict()

    @app.get("/api/playbooks/{agent}/propositions")
    async def propositions_playbook(agent: str) -> list[dict[str, Any]]:
        """Les propositions d'auto-amélioration en brouillon (#111), listées à part des versions.

        Métadonnées + justification (sans le contenu) — une proposition n'est jamais la
        version courante et le moteur ne la charge pas tant qu'elle n'est pas appliquée.
        """
        _exige_playbook_connu(agent)
        return [p.to_dict(avec_contenu=False) for p in playbooks.propositions(agent)]

    @app.get("/api/playbooks/{agent}/propositions/{numero}")
    async def proposition_playbook(agent: str, numero: int) -> dict[str, Any]:
        """Une proposition en brouillon, contenu compris — de quoi la relire avant d'agir.

        404 si elle n'existe pas (jamais créée, ou déjà appliquée/rejetée).
        """
        _exige_playbook_connu(agent)
        lue = playbooks.lire_proposition(agent, numero)
        if lue is None:
            raise HTTPException(
                status_code=404, detail=f"proposition inconnue : {agent} p{numero}"
            )
        return lue.to_dict()

    @app.post("/api/playbooks/{agent}/propositions/{numero}/appliquer")
    async def appliquer_proposition_playbook(agent: str, numero: int) -> dict[str, Any]:
        """L'action humaine qui adopte une proposition (#140) : elle devient la courante.

        Le contenu candidat rejoint l'historique comme version ordinaire — donc chargée
        à chaud par le moteur dès la tâche suivante (#78) — et sort des brouillons.
        Renvoie la version publiée. 404 si la proposition n'existe pas.
        """
        _exige_playbook_connu(agent)
        try:
            version = playbooks.appliquer_proposition(agent, numero)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return version.to_dict()

    @app.post("/api/playbooks/{agent}/propositions/{numero}/rejeter")
    async def rejeter_proposition_playbook(agent: str, numero: int) -> dict[str, Any]:
        """Écarte une proposition (#140) : elle disparaît, la version courante ne bouge pas.

        Renvoie la proposition rejetée (contenu compris — l'appelant garde une trace de
        ce qu'il vient d'écarter). 404 si elle n'existe pas.
        """
        _exige_playbook_connu(agent)
        try:
            rejetee = playbooks.rejeter_proposition(agent, numero)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return rejetee.to_dict()

    def _agent_du_catalogue(nom: str) -> Agent:
        """La fiche catalogue de `nom` (modèle, prompt du code) — un rôle du code au pire.

        Sert à l'analyse d'auto-amélioration, qui a besoin du modèle de l'agent. Les
        playbooks n'existent que pour les rôles du code (`_exige_playbook_connu`), toujours
        présents dans `DEFAULT_AGENTS` : le repli garantit une fiche même si le catalogue
        effectif ne renvoyait pas ce nom.
        """
        for agent in (*catalogue(agents_store), *DEFAULT_AGENTS):
            if agent.nom == nom:
                return agent
        # Injoignable en pratique : `_exige_playbook_connu` a déjà garanti un rôle du code.
        raise HTTPException(  # pragma: no cover - garanti connu en amont
            status_code=404, detail=f"agent inconnu : {nom}"
        )

    @app.post("/api/playbooks/{agent}/propositions")
    async def proposer_playbook(
        agent: str, requete: PlaybookPropositionRequete
    ) -> dict[str, Any]:
        """Analyse **à la demande** les échecs d'un run → proposition de révision (#139).

        L'analyse relit les échecs consignés du run `run_id` imputables à `agent`, confie
        à la couche fournisseur la rédaction d'une version révisée du playbook, et
        l'enregistre en **brouillon** (provenance « proposition », #138) — jamais la version
        courante, jamais appliquée sans action humaine (lot UI #140). Renvoie la proposition
        créée (métadonnées + justification + contenu). 404 si le run est inconnu, 422 s'il
        n'a aucun échec pour cet agent, 502 si la génération échoue.
        """
        _exige_playbook_connu(agent)
        execution = state.execution(requete.run_id)
        if execution is None:
            raise HTTPException(
                status_code=404, detail=f"exécution inconnue : {requete.run_id}"
            )
        echecs = echecs_du_run(execution, agent)
        if not echecs:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"aucun échec consigné pour l'agent {agent} dans le run "
                    f"{requete.run_id} : rien à proposer."
                ),
            )
        try:
            proposition = await analyseur.proposer_revision(
                _agent_du_catalogue(agent), requete.run_id, echecs
            )
        except RevisionIndisponible as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return proposition.to_dict()

    @app.post("/api/playbooks/{agent}/restaurer")
    async def restaurer_playbook(
        agent: str, requete: PlaybookRestaurationRequete
    ) -> dict[str, Any]:
        """Retour arrière (EF-25) : republie une version passée comme nouvelle courante.

        L'historique reste linéaire — rien n'est supprimé, la restauration crée
        une version de plus. 404 si la version demandée n'existe pas.
        """
        _exige_playbook_connu(agent)
        try:
            version = playbooks.restaurer(agent, requete.version)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return version.to_dict()

    def _integration_pool_dict(
        integration: IntegrationMcp, etats: dict[str, Any]
    ) -> dict[str, Any]:
        """Une intégration du pool + l'état de ses secrets côté coffre projet (#133).

        Enrichit `IntegrationMcp.to_dict` (id + déclaration à secrets masqués) de
        ce dont l'UI a besoin pour dire *où en est* l'intégration : son
        `mode_auth` et sa `procedure_url` (repris de l'entrée curée quand l'id y
        correspond), et pour **chaque** variable `${VAR}` requise, si son secret
        est **présent** dans le coffre projet et **valide** (un token OAuth
        expiré ne l'est plus). Aucune valeur de secret n'est jamais réémise.

        Depuis #678 elle porte aussi **ce qui a changé sous elle** : `admission`
        (par quel geste elle est montable), `signaux` (ce que l'amont en dit
        depuis), et `alerte` quand elle n'est plus dans l'allowlist — révoquée,
        ou disparue de la bibliothèque. C'est la moitié « sans le dire » du
        critère 2 : le serveur reste monté, mais l'écran qui le liste ne peut
        plus l'afficher comme si de rien n'était. `curee: false` le disait déjà
        et ne suffisait pas — il ne dit pas *pourquoi*, et une intégration
        montée hier qui devient « non curée » sans un mot se lit comme un bug.
        """
        courant = registre()
        entree = courant.get(integration.id)
        requis = (
            {v.cle: v for v in entree.secrets}
            if entree is not None
            else {cle: None for cle in references_env(integration.serveur)}
        )
        secrets_etat: list[dict[str, Any]] = []
        for cle in requis:
            variable = requis[cle]
            etat = etats.get(cle)
            secrets_etat.append(
                {
                    "cle": cle,
                    "description": variable.description if variable is not None else "",
                    "secret": variable.secret if variable is not None else True,
                    "present": etat is not None,
                    "valide": etat["valide"] if etat is not None else False,
                    "ephemere": etat["ephemere"] if etat is not None else False,
                    "expire_le": etat["expire_le"] if etat is not None else None,
                }
            )
        revocation = courant.revocation_de(integration.id)
        if entree is not None:
            alerte = ""
        elif revocation is not None:
            alerte = (
                f"admission révoquée le {revocation.revoquee_le or '?'} par "
                f"{revocation.revoquee_par or '?'}"
                f"{f' — {revocation.motif}' if revocation.motif else ''} : ce serveur "
                "reste monté mais n'est plus dans l'allowlist. Le retirer du pool : "
                f"DELETE /api/mcp/pool/{integration.id}."
            )
        else:
            alerte = (
                "ce serveur est monté mais ne figure plus dans la bibliothèque : "
                "il a pu être retiré du seed, ou son entrée admise être devenue "
                "illisible. Il reste monté tel qu'il a été configuré."
            )
        return {
            **integration.to_dict(),
            "mode_auth": entree.mode_auth if entree is not None else None,
            "procedure_url": entree.procedure_url if entree is not None else "",
            "curee": entree is not None,
            "source": entree.source if entree is not None else None,
            "admission": (
                entree.admission.trace()
                if entree is not None and entree.admission is not None
                else revocation.trace()
                if revocation is not None
                else None
            ),
            "signaux": [s.to_dict() for s in courant.signaux_de(integration.id)],
            "alerte": alerte,
            "secrets": secrets_etat,
        }

    def _pool_mcp() -> dict[str, Any]:
        """Le pool projet des intégrations MCP + l'état de leurs secrets (#133).

        `{integrations: [...], erreur}` : chaque intégration est enrichie de
        l'état de ses secrets (coffre projet). `erreur` porte la cause si le
        pool stocké est invalide — même contrat de visibilité que `mcp_erreur`
        (la misconfiguration s'affiche sans casser la fiche ni le listing).
        """
        try:
            integrations = mcp.pool()
        except ValueError as exc:
            return {"integrations": [], "erreur": str(exc)}
        try:
            etats = {e.cle: e.to_dict() for e in secrets.etat_projet()}
        except ValueError:
            # Coffre projet illisible : on sert le pool sans état de secret
            # plutôt que de masquer les intégrations déjà configurées.
            etats = {}
        return {
            "integrations": [_integration_pool_dict(i, etats) for i in integrations],
            "erreur": None,
        }

    def _volet_mcp(nom: str) -> dict[str, Any]:
        """Le volet « serveurs MCP » d'une fiche catalogue (#104, #133).

        `mcp_serveurs` porte les serveurs **effectifs** montés pour l'agent
        (déclaration héritée `<agent>.json` composée avec le pool activé, forme
        publique à secrets masqués) ; `mcp_erreur` porte la cause exacte si une
        source est invalide. `mcp_pool`/`mcp_pool_erreur` exposent le **pool
        projet** (les intégrations configurables, avec l'état de leurs secrets)
        et `mcp_activations` les ids **activés** pour cet agent — de quoi
        remplacer l'affichage lecture seule par des interrupteurs par agent (#133).
        """
        try:
            serveurs = mcp.lire(nom)
            volet_serveurs: dict[str, Any] = {
                "mcp_serveurs": [s.to_dict() for s in serveurs],
                "mcp_erreur": None,
            }
        except ValueError as exc:
            volet_serveurs = {"mcp_serveurs": [], "mcp_erreur": str(exc)}
        pool = _pool_mcp()
        try:
            activations = list(mcp.activations(nom))
        except ValueError:
            activations = []
        return {
            **volet_serveurs,
            "mcp_pool": pool["integrations"],
            "mcp_pool_erreur": pool["erreur"],
            "mcp_activations": activations,
        }

    def _volet_permissions(nom: str) -> dict[str, Any]:
        """Le volet « permissions » d'une fiche catalogue (#110), lecture seule.

        `permissions` porte la politique allow/ask/deny effective (celle que le
        moteur applique à l'exécution — None : aucune politique, tout ce que
        le profil expose est permis) ; `permissions_erreur` porte la cause
        exacte si la politique stockée est invalide — même contrat de
        visibilité que `mcp_erreur`. Les **trois** listes sont servies depuis
        #580, `ask` comprise et vide par défaut : une politique écrite avant ce
        lot se relit sous le régime d'hier.
        """
        try:
            politique = permissions.lire(nom)
        except ValueError as exc:
            return {"permissions": None, "permissions_erreur": str(exc)}
        return {
            "permissions": politique.to_dict() if politique is not None else None,
            "permissions_erreur": None,
        }

    def _fiche_defaut(agent: Agent, *, avec_playbook: bool) -> dict[str, Any]:
        """La fiche catalogue d'un agent par défaut : sa définition « du code ».

        `source` « defaut », sans dates : la définition vit dans le code
        (`maestro.agents.catalog`), seule l'édition de son playbook passe par
        le stockage versionné (`/api/playbooks`).
        """
        fiche: dict[str, Any] = {
            "nom": agent.nom,
            "role": agent.role,
            "competences": sorted(agent.competences),
            "modele": agent.modele,
            "fournisseur": None,
            "source": "defaut",
            "cree_le": None,
            "modifie_le": None,
            **_volet_mcp(agent.nom),
            **_volet_permissions(agent.nom),
        }
        if avec_playbook:
            fiche["playbook"] = agent.prompt_systeme
        return fiche

    def _fiche_personnalise(
        definition: AgentDefinition, *, avec_playbook: bool
    ) -> dict[str, Any]:
        """La fiche catalogue d'un agent personnalisé : sa définition persistée (#72)."""
        fiche = definition.to_dict(avec_playbook=avec_playbook)
        fiche["source"] = "personnalise"
        fiche.update(_volet_mcp(definition.nom))
        fiche.update(_volet_permissions(definition.nom))
        return fiche

    def _personnalise_ou_none(nom: str) -> AgentDefinition | None:
        """La définition persistée de `nom`, ou None (nom hors slug compris — jamais levé)."""
        try:
            return agents_store.lire(nom)
        except ValueError:
            return None

    def _exige_personnalise(nom: str) -> AgentDefinition:
        """La définition personnalisée de `nom`, ou l'erreur HTTP qui explique son absence.

        403 sur un agent par défaut (défini par le code : ni modifiable ni
        supprimable ici — son playbook s'édite via `/api/playbooks`), 404 sur
        un nom inconnu du dépôt.
        """
        if nom in NOMS_RESERVES:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"agent par défaut ou acteur système : {nom} — défini par le "
                    "code, ni modifiable ni supprimable (playbook éditable via "
                    "PUT /api/playbooks/{agent})."
                ),
            )
        definition = _personnalise_ou_none(nom)
        if definition is None:
            raise HTTPException(
                status_code=404,
                detail=f"agent personnalisé inconnu : {nom} (voir GET /api/catalogue)",
            )
        return definition

    @app.get("/api/mcp/registre")
    async def mcp_registre(q: str = "", source: str = SOURCE_TOUTES) -> list[dict[str, Any]]:
        """La bibliothèque de serveurs MCP (#131, #271), **à trois sources** depuis #678.

        `q` filtre par nom, id, éditeur, description ou tag (recherche libre,
        insensible à la casse et aux accents ; vide → tout le registre). Chaque
        entrée est un **template** : transport, gabarit d'exécution `${VAR}`
        (jamais de secret), mode d'auth (docs/21), variables à fournir
        (`secrets`) et lien de procédure côté outil (`procedure_url`) — de quoi
        guider la configuration.

        `source` sert à n'en demander qu'une : `toutes` (défaut), `curee`,
        `admise` ou `decouverte` — une valeur inconnue est un **422**, jamais
        une liste silencieusement autre que celle demandée. Sans le paramètre,
        la réponse est celle d'avant #677 **augmentée** : curées d'abord, puis
        admises, puis découvertes (tri `_rang`), donc un appelant qui ignore le
        paramètre lit toujours la même tête de liste.

        ⚠ `curee` et `source` ne disent **pas** la même chose (#678) : le
        booléen marque l'appartenance à l'**allowlist** — c'est lui qui répond à
        « montable ? », garde-fou supply-chain, docs/19 —, la source dit d'où
        l'entrée vient. Une entrée `admise` est donc `curee: true` : un humain
        l'a fait entrer par la porte d'admission. Une entrée `decouverte` se
        lit, se cherche, porte ses signaux d'amont (`editeur`, `version`,
        `depot`, `statut`) — et ne se monte pas.
        """
        try:
            entrees = registre().rechercher(q, source)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return [e.to_dict() for e in entrees]

    @app.get("/api/mcp/registre/provenance")
    async def mcp_registre_provenance() -> dict[str, Any]:
        """D'où vient la bibliothèque, quand elle a été revue, et par quoi chercher (#271).

        Le critère 1 de #271 demande que la provenance de la liste et sa date de
        revue soient **dites à l'écran** : un registre curé sans provenance
        affichée demande une confiance qu'il ne justifie pas. `tags` est la
        sortie du cul-de-sac du critère 2 — les pistes qu'une recherche sans
        résultat propose, plutôt que de répéter qu'elle n'a rien trouvé.

        Depuis #677 la bibliothèque a plusieurs sources, donc plusieurs
        provenances, et elles ne répondent pas à la même question : la curée se
        date par sa **revue humaine**, la découverte par le **rafraîchissement**
        du miroir et le nombre d'entrées qu'il porte. `provenances` les rend côte à côte ;
        `moissonnee` dit si le miroir a effectivement rapporté quelque chose —
        c'est ce qui fait cesser le « jamais moissonnée » du critère 5 du parent
        sans le remplacer par une promesse que personne n'a tenue.

        Depuis #678 il y a **trois** sources, donc une troisième provenance : les
        entrées **admises**, qui ne se datent ni par une revue de code ni par un
        rafraîchissement de miroir mais par le **geste** qui les a fait entrer.
        `derniere_le` est la date de la plus récente, `revoquees` ce qu'on a
        retiré, `signaux` ce que l'amont dit de tout ça depuis — l'écran a de
        quoi dire l'état de l'allowlist locale sans recharger le journal entier.

        ⚠ Les clés historiques (`resume`, `sources`, `revue_le`, `tags`,
        `total`) sont **conservées à plat et inchangées de sens** : elles parlent
        de la curation, que l'écran affiche déjà. L'ajout est purement additif —
        `total` reste le compte servi par `GET /api/mcp/registre` sans filtre, et
        `total_curees`/`total_admises`/`total_decouvertes` le détaillent.

        ⚠ `total_curees` compte le **seed seul** : une entrée admise n'y est plus,
        elle est dans `total_admises`. C'est le seul endroit du contrat où un
        chiffre a changé de portée, et la raison est la même que pour le filtre
        `source=curee` — ce qui répond à « montable ? » n'est pas la source mais
        le champ `curee` de chaque entrée.

        ⚠ La forme de `GET /api/mcp/registre` n'a **pas** bougé : elle rend
        toujours une liste nue. Emballer la liste pour y loger la provenance
        aurait cassé tous ses appelants pour une donnée qui ne change qu'à
        chaque revue — d'où une route sœur. `provenance` est un id **réservé**
        du registre (`ID_RESERVES`), donc aucune entrée ne peut être masquée par
        cette route et l'ordre de déclaration ci-dessous ne porte aucune règle.
        """
        courant = registre()
        curees = courant.lister(SOURCE_CUREE)
        admises = courant.lister(SOURCE_ADMISE)
        decouvertes = courant.lister(SOURCE_DECOUVERTE)
        journal = courant.admissions()
        return {
            **courant.provenance.to_dict(),
            "tags": list(courant.tags()),
            "total": len(courant.lister()),
            "total_curees": len(curees),
            "total_admises": len(admises),
            "total_decouvertes": len(decouvertes),
            "provenances": [
                {
                    **courant.provenance.to_dict(),
                    "source": SOURCE_CUREE,
                    "total": len(curees),
                },
                {
                    "source": SOURCE_ADMISE,
                    "resume": (
                        "Entrées du registre officiel qu'un humain a fait entrer dans "
                        "l'allowlist par un geste tracé — chacune figée à la version "
                        "admise, avec sa source et qui l'a admise. Révocable ; une "
                        "nouvelle version amont ne remplace rien sans un nouveau geste."
                    ),
                    "total": len(admises),
                    "revoquees": len(courant.admissions(revoquees=True)),
                    "derniere_le": max((a.le for a in journal), default=""),
                    "signaux": len(courant.signaux),
                },
                {
                    **courant.provenance_decouverte.to_dict(),
                    "total": len(decouvertes),
                },
            ],
        }

    @app.get("/api/mcp/registre/{id}")
    async def mcp_registre_entree(id: str) -> dict[str, Any]:
        """Une entrée du registre MCP (#131) — 404 si l'id n'est d'**aucune** source.

        Sert une entrée découverte ou admise aussi bien qu'une curée (#677,
        #678) : c'est la fiche de ce que l'écran vient de lister, `source` dit
        laquelle des trois on regarde et `curee` si elle est montable. Ouvrir
        cette lecture n'ouvre aucune voie de montage — `instancier` ne passe pas
        par ici, et `POST /api/mcp/pool` continue de n'accepter que l'allowlist.
        """
        entree = registre().trouver(id)
        if entree is None:
            raise HTTPException(
                status_code=404,
                detail=f"serveur MCP inconnu du registre : {id} (voir GET /api/mcp/registre)",
            )
        return entree.to_dict()

    def _impact_pool(id: str) -> dict[str, Any]:
        """Ce qu'une révocation de `id` laisse **debout** dans le pool projet (#678).

        Le « sans le dire » du critère 2 : révoquer ne démonte rien — casser un
        run en cours pour appliquer une décision d'allowlist serait un remède
        pire que le mal —, donc la route doit dire ce qui reste monté, chez qui,
        et par quel geste le retirer. Un pool illisible n'est pas une raison de
        faire échouer la révocation : on rend l'ignorance plutôt qu'une fausse
        réponse (`erreur` non nul, `montee` faux).
        """
        try:
            pool = mcp.pool()
        except ValueError as exc:
            return {"montee": False, "agents": [], "erreur": str(exc)}
        if not any(i.id == id for i in pool):
            return {"montee": False, "agents": [], "erreur": None}
        agents = [agent for agent in mcp.agents() if id in mcp.activations(agent)]
        return {"montee": True, "agents": sorted(agents), "erreur": None}

    @app.get("/api/mcp/admissions")
    async def mcp_admissions() -> dict[str, Any]:
        """Le journal des admissions (#678) : ce qu'un humain a fait entrer dans l'allowlist.

        `admissions` porte les **actives** (ce qui est montable aujourd'hui) et
        `revoquees` ce qui l'a été puis retiré, avec qui, quand et pourquoi. Les
        deux listes plutôt qu'une à filtrer : ce ne sont pas les mêmes lectures —
        l'une répond à « qu'est-ce qu'on autorise ? », l'autre à « qu'a-t-on
        retiré, et pour quel motif ? ».

        `signaux` porte ce que l'amont dit **depuis** de ces entrées
        (dépréciation, suppression, disparition, version plus récente) : rien
        n'est jamais retiré en silence, c'est le critère 3 du ticket. `politique`
        nomme qui garde la porte — une porte dont on ignore le gardien n'en est
        pas une, et le défaut du dépôt (le geste humain suffit) doit se
        distinguer d'une politique d'entreprise branchée.

        `erreur` porte la cause si le journal est illisible : ce cas-là **retire
        de l'allowlist** tout ce qu'il autorisait, il ne se tait pas.
        """
        courant = registre()
        try:
            journal = admissions.lister()
            erreur = None
        except ValueError as exc:
            journal, erreur = (), str(exc)
        return {
            "admissions": [a.to_dict() for a in journal if a.active],
            "revoquees": [a.to_dict() for a in journal if not a.active],
            "signaux": [s.to_dict() for s in courant.signaux],
            "politique": etat_politique(admissions.politique),
            "erreur": erreur,
        }

    @app.post("/api/mcp/admissions", status_code=201)
    async def admettre_entree(requete: AdmissionRequete) -> dict[str, Any]:
        """**Admet** une entrée découverte dans l'allowlist — le geste du ticket (#678).

        C'est la porte : après elle, et seulement après elle, `POST
        /api/mcp/pool` accepte l'entrée. Elle enregistre l'entrée **traduite et
        figée** avec sa source (nom amont, version épinglée, éditeur, dépôt,
        horodatage du miroir) et le geste (qui, quand, pourquoi).

        Rend l'entrée telle que la bibliothèque la sert **après** l'admission —
        `curee: true`, `source: "admise"`, `admission` renseignée : l'appelant
        n'a pas à recharger pour savoir ce qu'il a obtenu.

        404 si l'id n'est d'aucune source (une entrée que la traduction a refusée
        n'est pas dans la bibliothèque, donc pas admissible — la porte ne fabrique
        pas ce que le gabarit ne sait pas exprimer) ; 409 si elle est déjà curée
        au seed, supprimée chez l'amont, ou refusée par la politique. Ré-admettre
        une entrée déjà admise **à la même version** est idempotent ; à une autre
        version, c'est le nouveau geste qui promeut cette version-là.
        """
        courant = registre()
        entree = courant.trouver(requete.registre_id)
        if entree is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"serveur MCP inconnu de la bibliothèque : {requete.registre_id} — "
                    "rien à admettre. Une entrée que la traduction a refusée n'y "
                    "figure pas (voir GET /api/mcp/registre)."
                ),
            )
        provenance = courant.provenance_decouverte
        try:
            admission = admissions.admettre(
                entree,
                par=requete.par,
                note=requete.note,
                amont=provenance.amont,
                miroir_le=provenance.rafraichi_le,
            )
        except RefusAdmission as exc:
            raise HTTPException(status_code=409, detail=exc.cause) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Le journal vient de changer : la bibliothèque doit servir l'entrée
        # admise **à la requête suivante**, sans attendre que le mtime du
        # système de fichiers se distingue (cf. `federer_memo`).
        oublier_memo()
        admise = registre().trouver(admission.id)
        return admise.to_dict() if admise is not None else admission.to_dict()

    @app.post("/api/mcp/admissions/{id}/revocation")
    async def revoquer_admission(id: str, requete: RevocationRequete) -> dict[str, Any]:
        """**Révoque** une admission (#678, critère 2) — sans rien démonter.

        ⚠ Un `POST …/revocation` et non un `DELETE …/{id}`, pour deux raisons qui
        vont dans le même sens : **rien n'est effacé** — l'admission reste au
        journal avec qui l'a révoquée, quand et pourquoi, et c'est ce qui permet
        au refus d'instanciation de la nommer —, et le geste porte un corps
        (l'auteur, le motif) qu'un `DELETE` transporte mal, certains
        intermédiaires HTTP le laissant tomber en route.

        L'entrée sort de l'allowlist : `POST /api/mcp/pool` la refuse à nouveau,
        et `instancier` **nomme** la révocation au lieu de rendre le refus d'un
        id inconnu.

        ⚠ Un serveur **déjà monté** dans le pool projet y reste, et la réponse le
        dit (`pool`) : les agents qui l'ont activée, et le geste pour le retirer
        (`DELETE /api/mcp/pool/{id}`). Le démonter d'office couperait un run en
        cours pour appliquer une décision d'allowlist ; ce qui est promis est
        « jamais sans le dire », pas « jamais sans casser ».

        404 si l'id n'a jamais été admis (une entrée curée du seed se retire en
        revue de code, pas ici), 409 si l'admission est déjà révoquée.
        """
        try:
            admission = admissions.revoquer(id, par=requete.par, motif=requete.motif)
        except RefusAdmission as exc:
            code = 404 if exc.motif == MOTIF_NON_ADMISE else 409
            raise HTTPException(status_code=code, detail=exc.cause) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        oublier_memo()
        impact = _impact_pool(id)
        return {
            "admission": admission.to_dict(),
            "pool": impact,
            "message": (
                f"« {id} » est sortie de l'allowlist mais reste montée dans le pool "
                "projet : rien n'a été démonté. Pour la retirer aussi du pool, "
                f"DELETE /api/mcp/pool/{id}."
                if impact["montee"]
                else f"« {id} » est sortie de l'allowlist ; elle n'était pas dans le pool."
            ),
        }

    @app.get("/api/mcp/pool")
    async def mcp_pool() -> dict[str, Any]:
        """Le pool projet des intégrations MCP configurées (#133), avec l'état des secrets.

        `{integrations: [...], erreur}` : les intégrations ajoutées au pool
        depuis la bibliothèque, chacune enrichie de son mode d'auth et de
        l'état (présent/valide) de ses secrets côté coffre projet — jamais une
        valeur de secret. `erreur` porte la cause si le pool stocké est invalide.
        C'est ce que liste la section « Intégrations MCP » des Paramètres.
        """
        return _pool_mcp()

    @app.post("/api/mcp/pool", status_code=201)
    async def ajouter_integration_pool(requete: IntegrationPoolRequete) -> dict[str, Any]:
        """Ajoute (ou reconfigure) une intégration du registre dans le pool projet (#133).

        Le parcours **configuration** du critère 1 : instancie l'entrée de
        l'**allowlist** `registre_id` (garde-fou supply-chain — 404 si elle n'y
        est pas, docs/19), l'inscrit au pool (remplacement si l'id y est déjà —
        reconfiguration) et pose ses secrets **une seule fois** dans le coffre
        projet chiffré, selon le mode d'auth de l'entrée (token
        statique/appairage/OAuth importé). Une valeur de secret vide est ignorée
        (le secret reste à configurer). 404 si l'id est hors allowlist, 422 si
        une valeur/échéance est invalide.

        ⚠ Le 404 **nomme le geste qui manque** depuis #678 : une entrée
        découverte y renvoie vers l'admission (`POST /api/mcp/admissions`), une
        entrée révoquée dit qui l'a retirée et quand. La phrase vient de
        `RegistreMcp.cause_non_instanciable` et n'est pas recopiée ici — c'est
        exactement celle que lèverait `instancier` deux lignes plus bas, et deux
        formulations pour un même refus finiraient par se contredire.
        """
        courant = registre()
        entree = courant.get(requete.registre_id)
        if entree is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"{courant.cause_non_instanciable(requete.registre_id)} "
                    "Voir GET /api/mcp/registre."
                ),
            )
        try:
            serveur = registre().instancier(requete.registre_id, nom=requete.nom or None)
            integration = IntegrationMcp(id=requete.registre_id, serveur=serveur)
            pool = [i for i in mcp.pool() if i.id != integration.id]
            pool.append(integration)
            mcp.ecrire_pool(pool)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Les secrets, une seule fois, dans le coffre projet partagé. On ne
        # retient que les variables déclarées par l'entrée curée (les autres
        # n'ont pas de sens pour ce serveur) et on ignore les valeurs vides.
        variables = {v.cle: v for v in entree.secrets}
        for saisi in requete.secrets:
            variable = variables.get(saisi.cle)
            if variable is None or not saisi.valeur:
                continue
            try:
                secrets.enregistrer_projet(
                    saisi.cle,
                    saisi.valeur,
                    mode_auth=entree.mode_auth,
                    secret=variable.secret,
                    expire_le=saisi.expire_le or None,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            etats = {e.cle: e.to_dict() for e in secrets.etat_projet()}
        except ValueError:
            etats = {}
        return _integration_pool_dict(integration, etats)

    @app.delete("/api/mcp/pool/{id}")
    async def retirer_integration_pool(id: str) -> dict[str, Any]:
        """Retire une intégration du pool projet (#133) et fait le ménage derrière elle.

        Sort l'intégration du pool, **désactive** son id chez tout agent qui
        l'avait activée (sinon `McpStore.lire` casserait sur une activation
        orpheline) et **supprime** du coffre projet les secrets qu'elle était
        seule à référencer (un secret encore utilisé par une autre intégration
        du pool reste). 404 si l'id n'est pas dans le pool.
        """
        try:
            pool = list(mcp.pool())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        cible = next((i for i in pool if i.id == id), None)
        if cible is None:
            raise HTTPException(
                status_code=404,
                detail=f"intégration inconnue du pool : {id} (voir GET /api/mcp/pool).",
            )
        mcp.ecrire_pool([i for i in pool if i.id != id])
        for agent in mcp.agents():
            actives = mcp.activations(agent)
            if id in actives:
                mcp.ecrire_activations(agent, [a for a in actives if a != id])
        # Secrets : ne retirer que ceux qu'aucune intégration restante ne référence.
        references_restantes = {
            cle for i in mcp.pool() for cle in references_env(i.serveur)
        }
        for cle in references_env(cible.serveur):
            if cle not in references_restantes:
                secrets.supprimer_projet(cle)
        return {"id": id, "supprime": True}

    @app.put("/api/mcp/activations/{agent}")
    async def definir_activations_mcp(
        agent: str, requete: ActivationsMcpRequete
    ) -> dict[str, Any]:
        """Fixe les intégrations du pool **activées** pour un agent (#133) — critère 2.

        Remplacement intégral : `integrations` (des ids du pool) devient
        l'ensemble activé de l'agent, vide pour tout désactiver. C'est l'écriture
        derrière l'interrupteur par agent qui remplace l'affichage lecture seule
        des serveurs MCP. 404 si l'agent n'est pas au catalogue, 422 si un id
        n'est pas dans le pool (une activation orpheline casserait la lecture).
        """
        _exige_agent_du_catalogue(agent)
        try:
            pool_ids = {i.id for i in mcp.pool()}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        inconnues = sorted(set(requete.integrations) - pool_ids)
        if inconnues:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"intégration(s) absente(s) du pool : {', '.join(inconnues)} "
                    "(voir GET /api/mcp/pool)."
                ),
            )
        try:
            activees = mcp.ecrire_activations(agent, requete.integrations)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"agent": agent, "integrations": list(activees)}

    # --- Projets de l'utilisateur (#223) : CRUD et explorateur de dossiers ---
    def _refus_projet(exc: Exception, *, explorateur: bool = False) -> HTTPException:
        """Un refus du service des projets en `HTTPException` — motif compris.

        Le corps est `{motif, message}` et non une phrase libre : l'écran
        Projets doit pouvoir dire *pourquoi* une racine est refusée (docs/05
        §2.7), ce qu'un texte à analyser ne lui permettrait pas. Le code vient
        du motif (`statut_http`), avec 422 en repli — **jamais un 500** : une
        racine hors périmètre est une réponse, pas une panne (critère #223).
        """
        return HTTPException(
            status_code=statut_http(exc, explorateur=explorateur),
            detail=detail_refus(exc),
        )

    def _motifs(valeur: list[str] | None) -> tuple[str, ...] | None:
        """Les motifs de périmètre d'une requête — None laissant les défauts du modèle."""
        return None if valeur is None else tuple(valeur)

    def _poste(requete: Request) -> bool:
        """La requête vient-elle du poste ? — le discriminant « poste » vs « serveur ».

        Lu sur le **client TCP**, jamais sur un en-tête : `X-Forwarded-For` et
        consorts sont posés par le client, donc un `127.0.0.1` déclaré ouvrirait
        une fenêtre sur la machine du serveur depuis n'importe où.
        """
        return selecteur.hote_du_poste(requete.client.host if requete.client else None)

    # Enregistrées **avant** `/api/projets/{id_projet}` : « explorateur » et
    # « selecteur » sont des identifiants de projet valides au regard du slug
    # `ID_PROJET`, la capture les avalerait donc si elle passait la première
    # (même piège qu'au-dessus avec `/api/playbooks/propositions`).
    @app.get("/api/projets/selecteur")
    async def selecteur_disponible(requete: Request) -> dict[str, Any]:
        """Le sélecteur de dossier **natif du poste** est-il ouvrable ici — et sinon, pourquoi ?

        C'est la réponse que l'écran lit **avant** d'afficher quoi que ce soit :
        un bouton quand `disponible`, la phrase de `message` sinon (#278). Jamais
        un bouton mort, jamais un silence — l'explorateur servi par l'API reste
        dans les deux cas la voie complète.

        Toujours **200** : une indisponibilité est une réponse, pas une erreur.
        Trois motifs — `selecteur-desactive` (`MAESTRO_SELECTEUR_NATIF=0`),
        `selecteur-hors-poste` (backend joint depuis le réseau : le dialogue
        s'ouvrirait sur le serveur, devant personne) et `selecteur-sans-outil`
        (ni PowerShell, ni osascript, ni zenity/kdialog, ou pas de session
        graphique).
        """
        return selecteur.disponibilite(
            poste=_poste(requete),
            reglage=load_settings().selecteur_natif,
        )

    @app.post("/api/projets/selecteur")
    async def ouvrir_selecteur(
        requete: Request,
        corps: SelecteurRequete | None = None,
    ) -> dict[str, Any]:
        """Ouvre le dialogue de dossier de l'OS et rend le chemin choisi (#278).

        Le pas d'après l'explorateur : un navigateur ne livre jamais de chemin
        absolu, mais le backend **tourne sur le poste** et peut ouvrir le
        dialogue natif. `depart` (facultatif) est le dossier d'ouverture.

        Le chemin rendu est **confronté aux frontières d'EF-38** avant de
        revenir : `chemin` porte le dossier canonicalisé et `racine_valide` dit
        s'il est déclarable tel quel — un dossier explorable mais non déclarable
        (une racine de disque, le dossier utilisateur nu) revient avec son
        `refus` motivé plutôt qu'en 4xx, l'écran ayant besoin de le montrer dans
        le formulaire, pas de le traiter en panne.

        **Annuler n'est pas une erreur** : fermer la fenêtre rend
        `{"annule": true}` en 200. Les vrais empêchements sont des 403 motivés
        (`selecteur-hors-poste`, `selecteur-desactive`, `selecteur-sans-outil`,
        `selecteur-en-cours`, `selecteur-expire`).
        """
        etat = selecteur.disponibilite(
            poste=_poste(requete),
            reglage=load_settings().selecteur_natif,
        )
        if not etat["disponible"]:
            raise HTTPException(
                status_code=403,
                detail={"motif": etat["motif"], "message": etat["message"]},
            )
        try:
            chemin = await asyncio.to_thread(
                selecteur.choisir_dossier,
                depart=(corps.depart if corps else None),
            )
        except selecteur.SelecteurRefuse as exc:
            raise HTTPException(
                status_code=403,
                detail={"motif": exc.motif, "message": str(exc)},
            ) from exc
        if chemin is None:
            return {"annule": True, "chemin": None, "racine_valide": False, "refus": None}
        return {"annule": False, **_verdict_racine(chemin)}

    def _verdict_racine(chemin: str) -> dict[str, Any]:
        """Le chemin choisi, canonicalisé, et ce qu'EF-38 en dit — sans jamais lever.

        Deux questions distinctes, et c'est tout l'intérêt de les séparer : le
        dossier est-il **lisible** (canonicalisable), et est-il **déclarable**
        comme racine de projet ? Un `D:/` choisi au dialogue répond oui à la
        première et non à la seconde ; l'écran doit pouvoir le dire au lieu de
        laisser l'utilisateur découvrir le refus à la soumission.
        """
        try:
            resolu = valider_racine(chemin)
        except RacineRefusee as exc:
            try:
                lisible = canonique(chemin).as_posix()
            except OSError:  # pragma: no cover - chemin illisible pour l'OS
                lisible = chemin
            return {
                "chemin": lisible,
                "racine_valide": False,
                "refus": {"motif": exc.motif, "message": str(exc)},
            }
        return {"chemin": resolu.as_posix(), "racine_valide": True, "refus": None}

    @app.get("/api/projets/explorateur")
    async def explorer_dossiers(chemin: str | None = None) -> dict[str, Any]:
        """Énumère les **dossiers** de `chemin`, ou les racines explorables sans `chemin`.

        La brique sans laquelle l'écran Projets ne peut pas exister : un
        navigateur ne livre jamais de chemin absolu, c'est donc le backend — qui
        tourne déjà sur le poste — qui énumère (docs/05 §2.7). Chaque entrée
        porte son marqueur « dépôt Git » et l'identifiant du projet qui l'a déjà
        déclarée. `parent` est `null` quand remonter sortirait des racines.

        Ce n'est **pas** un « lis n'importe quel chemin » : hors des racines
        explorables (dossier utilisateur par défaut, `MAESTRO_EXPLORATEUR_RACINES`,
        et les racines des projets déclarés) ou dans une zone sensible (`.ssh`,
        `AppData`, dossiers système, dépôt de Maestro), la route **refuse avec
        son motif** — 403 — au lieu de rendre une liste vide. 404 sur un dossier
        absent, 422 sur un chemin relatif ou un fichier.
        """
        try:
            return projets.explorer(chemin)
        except ValueError as exc:
            raise _refus_projet(exc, explorateur=True) from exc

    @app.get("/api/projets")
    async def projets_liste() -> list[dict[str, Any]]:
        """Les projets déclarés (#223), dans l'ordre des identifiants.

        Forme du fichier stocké (docs/24 §2.3) : racine canonicalisée, origine,
        `vcs` détecté (`null` si le projet n'est pas versionné) et périmètre. Un
        fichier du dépôt illisible est **sauté** plutôt que de rendre la page
        entière inutilisable ; `GET /api/projets/{id}` l'explique.
        """
        return projets.lister()

    @app.get("/api/projets/{id_projet}")
    async def projet_detail(id_projet: str) -> dict[str, Any]:
        """Un projet déclaré. 404 s'il est inconnu, 422 si son fichier est illisible."""
        try:
            return projets.detail(id_projet)
        except (ValueError, ProjetInconnu) as exc:
            raise _refus_projet(exc) from exc

    @app.post("/api/projets", status_code=201)
    async def creer_projet(requete: ProjetRequete) -> dict[str, Any]:
        """Déclare un projet : racine validée (EF-38), VCS détecté, identifiant engendré.

        `origine="nouveau"` crée le dossier s'il manque ; `existant` exige qu'il
        soit là. 422 avec son **motif** si la racine est refusée par la
        validation du lot 1 (racine de disque, dossier utilisateur nu, `.ssh`,
        `AppData`, dossier système, dépôt de Maestro, chemin relatif, dossier
        absent) ou déjà déclarée par un autre projet — jamais un 500.
        """
        try:
            return projets.creer(
                requete.nom,
                requete.racine,
                origine=requete.origine,
                inclus=_motifs(requete.inclus),
                exclus=_motifs(requete.exclus),
            )
        except ValueError as exc:
            raise _refus_projet(exc) from exc

    @app.put("/api/projets/{id_projet}")
    async def modifier_projet(id_projet: str, requete: ProjetRequete) -> dict[str, Any]:
        """Remplace la déclaration d'un projet — l'intégrale, pas un diff (cf. `/api/catalogue`).

        Le `vcs` est **re-détecté** sur la racine servie et `cree_le` est
        préservé. 404 si le projet est inconnu, 422 motivé si la nouvelle racine
        est refusée ou déjà déclarée ailleurs.
        """
        try:
            return projets.remplacer(
                id_projet,
                requete.nom,
                requete.racine,
                origine=requete.origine,
                inclus=_motifs(requete.inclus),
                exclus=_motifs(requete.exclus),
            )
        except (ValueError, ProjetInconnu) as exc:
            raise _refus_projet(exc) from exc

    @app.delete("/api/projets/{id_projet}")
    async def supprimer_projet(id_projet: str) -> dict[str, Any]:
        """Oublie un projet. Ne touche **jamais** au dossier sur le disque (#221).

        Supprimer une déclaration n'est pas supprimer le travail de
        l'utilisateur : seul le fichier du dépôt part. 404 si le projet est
        inconnu.
        """
        try:
            return projets.supprimer(id_projet)
        except (ValueError, ProjetInconnu) as exc:
            raise _refus_projet(exc) from exc

    @app.get("/api/catalogue")
    async def catalogue_liste() -> list[dict[str, Any]]:
        """Le catalogue d'agents (#72) : les agents par défaut puis les personnalisés.

        Métadonnées seules (le playbook d'une fiche se lit sur
        `GET /api/catalogue/{nom}`), dans l'ordre du catalogue effectif — celui
        que les moteurs chargent au démarrage.
        """
        return [_fiche_defaut(a, avec_playbook=False) for a in DEFAULT_AGENTS] + [
            _fiche_personnalise(d, avec_playbook=False) for d in agents_store.lister()
        ]

    @app.get("/api/catalogue/{nom}")
    async def catalogue_fiche(nom: str) -> dict[str, Any]:
        """La définition complète d'un agent du catalogue, playbook compris."""
        defaut = next((a for a in DEFAULT_AGENTS if a.nom == nom), None)
        if defaut is not None:
            return _fiche_defaut(defaut, avec_playbook=True)
        definition = _personnalise_ou_none(nom)
        if definition is None:
            raise HTTPException(
                status_code=404,
                detail=f"agent inconnu : {nom} (voir GET /api/catalogue)",
            )
        return _fiche_personnalise(definition, avec_playbook=True)

    @app.post("/api/catalogue", status_code=201)
    async def creer_agent(requete: AgentCreationRequete) -> dict[str, Any]:
        """Crée un agent personnalisé (#72) : définition persistée hors du code.

        L'agent entre immédiatement dans la vue `GET /api/agents` (cible de
        réassignation manuelle) ; il est routable et exécutable par les moteurs
        et workers construits ensuite, qui chargent le catalogue effectif à
        leur démarrage. 409 si le nom est déjà pris (agent par défaut, acteur
        système ou personnalisé existant), 422 si la définition est invalide.
        """
        if requete.nom in NOMS_RESERVES or _personnalise_ou_none(requete.nom) is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"nom déjà pris : {requete.nom} — modification d'un agent "
                    "personnalisé via PUT /api/catalogue/{nom}."
                ),
            )
        try:
            definition = agents_store.ecrire(
                AgentDefinition(
                    nom=requete.nom,
                    role=requete.role,
                    competences=tuple(requete.competences),
                    playbook=requete.playbook,
                    modele=requete.modele,
                    fournisseur=requete.fournisseur,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        state.ajouter_agent(definition.nom, definition.role)
        return _fiche_personnalise(definition, avec_playbook=True)

    @app.put("/api/catalogue/{nom}")
    async def modifier_agent(nom: str, requete: AgentModificationRequete) -> dict[str, Any]:
        """Remplace la définition d'un agent personnalisé (l'intégrale, pas un diff).

        La définition modifiée vaut pour les moteurs construits ensuite. 403
        sur un agent par défaut (défini par le code), 404 si l'agent n'existe
        pas, 422 si la nouvelle définition est invalide.
        """
        _exige_personnalise(nom)
        try:
            definition = agents_store.ecrire(
                AgentDefinition(
                    nom=nom,
                    role=requete.role,
                    competences=tuple(requete.competences),
                    playbook=requete.playbook,
                    modele=requete.modele,
                    fournisseur=requete.fournisseur,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        state.ajouter_agent(definition.nom, definition.role)
        return _fiche_personnalise(definition, avec_playbook=True)

    @app.delete("/api/catalogue/{nom}")
    async def supprimer_agent(nom: str) -> dict[str, Any]:
        """Supprime un agent personnalisé du catalogue.

        Sa fiche quitte la vue `GET /api/agents` ; les moteurs construits
        ensuite ne le chargent plus. Son réglage de capacité (#86) part avec
        lui — un homonyme recréé plus tard repartira des défauts. 403 sur un
        agent par défaut, 404 s'il n'existe pas.
        """
        _exige_personnalise(nom)
        agents_store.supprimer(nom)
        capacites.supprimer(nom)
        state.retirer_agent(nom)
        return {"nom": nom, "supprime": True}

    def _exige_agent_du_catalogue(nom: str) -> Agent:
        """La fiche catalogue de `nom` (défaut ou personnalisé), ou l'erreur 404.

        Le chat ne s'adresse qu'aux agents du catalogue effectif — celui que
        les moteurs chargent : un nom inconnu n'ouvre pas de fil orphelin.
        """
        defaut = next((a for a in DEFAULT_AGENTS if a.nom == nom), None)
        if defaut is not None:
            return defaut
        definition = _personnalise_ou_none(nom)
        if definition is None:
            raise HTTPException(
                status_code=404,
                detail=f"agent inconnu : {nom} (voir GET /api/catalogue)",
            )
        return definition.to_agent()

    def _canal_chat(nom: str) -> tuple[Agent, ServiceChat]:
        """La fiche et le service du fil `nom` — agent, assistant (#123), orchestration (#268).

        Les trois canaux se servent des mêmes endpoints : `assistance` résout sur
        une fiche hors catalogue et sur son propre `ServiceChat`, `orchestrateur`
        de même, tout autre nom sur l'agent du catalogue et le chat ordinaire.
        L'UI n'a donc qu'un seul contrat REST à connaître, le nom du fil départage
        — et les deux noms système sont **réservés** côté catalogue
        (`maestro.agents.store.NOMS_RESERVES`), donc aucun agent personnalisé ne
        peut venir prendre l'un de ces fils.
        """
        if nom == NOM_ASSISTANCE:
            return AGENT_ASSISTANCE, assistance
        if nom == NOM_ORCHESTRATION:
            return AGENT_ORCHESTRATION, orchestration
        return _exige_agent_du_catalogue(nom), chat

    @app.get("/api/chat/{agent}")
    async def fil_chat(agent: str) -> dict[str, Any]:
        """Le fil de conversation utilisateur ↔ agent (#84), relu de la persistance.

        Vide tant que l'agent n'a jamais été contacté ; 404 si l'agent n'est
        pas au catalogue. `assistance` (#123) désigne le canal d'aide.
        """
        fiche, service = _canal_chat(agent)
        return {
            "agent": fiche.nom,
            "role": fiche.role,
            "messages": [m.to_dict() for m in service.fil(fiche.nom)],
        }

    @app.post("/api/chat/{agent}/messages", status_code=201)
    async def envoyer_chat(agent: str, requete: ChatEnvoiRequete) -> dict[str, Any]:
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
        """
        fiche, service = _canal_chat(agent)
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
            "messages": [message.to_dict(), reponse.to_dict()],
        }

    async def _flux_reponse(
        agent: str,
        contenu: str,
        sources: list[dict[str, Any]] | None,
        projet_id: str | None,
    ) -> StreamingResponse:
        """Le flux SSE d'une réponse — la mécanique des **deux** verbes (#268, #692).

        Écrite une fois et appelée par `GET …/flux` comme par `POST …/flux` :
        deux façons de déclarer l'envoi, un seul chemin d'envoi. Elle ouvre un
        `text/event-stream` où chaque `data: <json>` est un `FragmentChat` —
        `debut` (portant le message utilisateur), des `fragment` (incréments
        `delta`), puis `fin` (portant la réponse complète). Une réponse
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
        """
        fiche, service = _canal_chat(agent)
        projet = projet_id_valide(projet_id)

        async def flux() -> AsyncIterator[str]:
            async for trame in service.diffuser(fiche, contenu, sources, projet_id=projet):
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

    @app.get("/api/chat/{agent}/flux")
    async def flux_chat(
        agent: str, contenu: str = "", projet_id: str | None = None
    ) -> StreamingResponse:
        """Flux SSE d'une réponse de chat — le contrat #183, servi pour de bon (#268).

        `GET /api/chat/{agent}/flux?contenu=…` : la voie **sans source**, et le
        seul verbe qu'un `EventSource` sache ouvrir. Une URL ne pouvant porter ni
        identifiants de sources ni corps, un message qui embarque de la matière
        (#482) passe par `POST …/flux` — voir l'arbitrage en tête de
        `maestro.controltower.chat`. Ce n'est pas une voie dégradée : c'est le cas
        courant, gardé tel quel plutôt que retiré.

        `?projet_id=` porte le **projet de la fenêtre** (#683), comme le corps de
        `POST …/messages` : un run ouvert par cette voie se rattache donc comme
        un run ouvert par l'autre. Il n'est pas nommé `?projet=` à dessein — ce
        nom-là désigne partout ailleurs une **portée** de lecture, avec ses mots
        réservés `tous`/`aucun` (#277), et deux contrats sous un même nom seraient
        la première façon de les confondre.
        """
        return await _flux_reponse(agent, contenu, None, projet_id)

    @app.post("/api/chat/{agent}/flux")
    async def flux_chat_poste(agent: str, requete: ChatEnvoiRequete) -> StreamingResponse:
        """Le flux d'une réponse pour un message qui **embarque** quelque chose (#692).

        Même corps que `POST …/messages` — `contenu`, `sources` (#482),
        `projet_id` (#683) — et même `text/event-stream` que `GET …/flux` : c'est
        exactement l'envoi du POST, rendu au fur et à mesure au lieu d'être rendu
        d'un coup. Le client n'a donc **pas** à choisir entre le direct et ses
        pièces jointes, ce qui était le seul motif du choix écrit dans
        `app/chat/page.tsx`.

        Le message utilisateur **résolu** — ses sources et leur rapport de
        lecture (#316) — voyage sur la trame `debut`, la réponse sur la trame
        `fin` : la paire que le POST rend d'un coup, rendue en deux trames. 404 si
        le fil n'existe pas, 422 sur un message vide, 422 motivé sur une source
        refusée.
        """
        return await _flux_reponse(agent, requete.contenu, requete.sources, requete.projet_id)

    @app.websocket("/ws/evenements")
    async def evenements(websocket: WebSocket, projet: str | None = None) -> None:
        """Flux temps réel : les événements de la portée demandée, en JSON sur la socket.

        Émission et écoute de la déconnexion courent en parallèle : sans cela,
        un client parti resterait connecté jusqu'à la prochaine émission.

        `?projet=` suit **le même contrat que le REST** (#277) —
        `<id>` | `tous` | `aucun`, obligatoire : un client qui charge son état
        filtré puis suit le flux voit deux fois le même périmètre, et un
        événement d'un autre projet n'arrive jamais dans une vue filtrée. Un
        événement **sans** projet n'entre pas non plus dans une vue de projet,
        exactement comme une tâche sans projet n'entre dans aucun Kanban filtré.

        Le refus se dit **sur la socket** avant de la fermer : la connexion est
        acceptée, le motif part en une trame `{"erreur": {motif, message}}`,
        puis la fermeture porte le code 1008 (violation de politique). Refuser
        la poignée de main laisserait le client devant un échec réseau muet,
        alors que la cause tient en un mot.
        """
        await websocket.accept()
        try:
            portee = resoudre_portee(projet, projet_connu=projets)
        except PorteeRefusee as exc:
            await websocket.send_json({"erreur": detail_refus(exc)})
            await websocket.close(code=1008, reason=exc.motif)
            return
        file = diffusion.connecter(portee)
        emission = asyncio.create_task(_emet(websocket, file))
        deconnexion = asyncio.create_task(_attend_deconnexion(websocket))
        try:
            _, en_attente = await asyncio.wait(
                {emission, deconnexion}, return_when=asyncio.FIRST_COMPLETED
            )
            for tache_asyncio in en_attente:
                tache_asyncio.cancel()
                with suppress(asyncio.CancelledError):
                    await tache_asyncio
        finally:
            diffusion.deconnecter(file)

    return app


async def _emet(websocket: WebSocket, file: asyncio.Queue[Event]) -> None:
    """Draine la file de la connexion : chaque événement part en JSON."""
    while True:
        event = await file.get()
        await websocket.send_json(event.to_dict())


async def _attend_deconnexion(websocket: WebSocket) -> None:
    """Rend la main dès que le client ferme (on ne lit rien du flux entrant)."""
    with suppress(WebSocketDisconnect):
        while True:
            await websocket.receive_text()


def create_default_app() -> FastAPI:
    """L'app de production : bus Redis Pub/Sub configuré depuis l'environnement.

    Consomme le canal `maestro.evenements` de l'instance `REDIS_URL` (celle du
    docker-compose par défaut — la même que la file de tâches #41), alimenté
    côté moteur par `maestro.controltower.bridge` ; le chat (#84) transite par
    la messagerie Redis de la même instance (boîtes `maestro.boite.<agent>`).
    Les événements y sont aussi **persistés** (#97) sur la liste Redis
    `maestro.evenements:journal` et rejoués au démarrage : l'état de la Control
    Tower survit au redémarrage de l'API.

    Les **battements** des runs (#348) vivent sur la même instance, dans le hash
    `maestro.runs:battements` — hors du process, seule façon de voir battre un run
    lancé par `maestro-run --publier` et de le retrouver vivant après un
    redémarrage de l'API.

    L'**hôte des runs** (#443) se choisit ici, et nulle part ailleurs. Depuis #446
    le défaut est l'hôte **détaché** : chaque run vit dans un process indépendant,
    qui survit à l'arrêt **subi** de `maestro-api` — fermer la fenêtre du
    navigateur, relancer après une modification, planter n'emportent plus le run.
    Il ne survit pas à l'arrêt **volontaire** (#486, `POST /api/extinction`), qui
    est une décision et non un accident.
    `MAESTRO_HOTE_RUN=process` ramène la tâche de fond de l'API, dont les runs
    meurent avec elle. C'est le seul endroit du dépôt qui *nomme* un hôte : le
    service ne connaît que le contrat, et le résoudre là où sont déjà résolus le
    bus, le journal et le registre met la frontière d'exécution parmi les autres
    choix de déploiement, ce qu'elle est.

    C'est la cible *factory* d'uvicorn :
    `uvicorn --factory maestro.controltower.app:create_default_app`
    (ou le script `maestro-api`).
    """
    settings = load_settings()
    return create_app(
        bus=RedisEventBus(settings.redis_url),
        mailbox=RedisMailbox(settings.redis_url),
        event_log=RedisEventLog(settings.redis_url),
        battements=RegistreBattementsRedis(settings.redis_url),
        hote_run=_hote_configure(settings),
    )


def _hote_configure(settings: Settings) -> HoteRun | None:
    """Résout `MAESTRO_HOTE_RUN` en hôte — **détaché** par défaut depuis #446.

    La bascule du chantier #441 tient dans la valeur de repli de la première
    ligne, et c'est voulu : les quatre lots précédents ont livré du code inerte
    pour que celui-ci n'ait rien d'autre à changer. Ce que ce défaut promet est
    écrit dans `hote_detache` — un run survit à l'accident, pas à l'extinction ni
    à sa machine (#486) ; ce
    qu'il exige est un Redis joignable, sur lequel le process publie ses étapes,
    bat son cœur, reçoit les décisions humaines et consigne son issue.

    `process` reste disponible et **doit se nommer** : le silence ne le désigne
    plus. C'est le sens de la bascule — un déploiement qui veut des runs mourant
    avec l'API le dit, là où c'était jusqu'ici ce qu'on obtenait sans rien dire.
    `None` est rendu pour lui, et non `HoteRunEnProcess()` : le service en fabrique
    un autour de son propre dérouleur, qu'on n'a pas ici.

    Une valeur inconnue est une **erreur franche** et non un repli silencieux :
    `MAESTRO_HOTE_RUN=procesus` laisserait sinon croire que les runs meurent avec
    l'API alors qu'ils lui survivent — et depuis la bascule, l'erreur se lit dans
    l'autre sens qu'avant, ce qui ne change rien à sa nature : une frontière
    d'exécution mal orthographiée ne doit jamais ressembler à un choix. Même parti
    pris que `MAESTRO_ISOLATION` (#108).

    L'import de l'hôte détaché est **local** à cette branche : il tire
    `subprocess` et, par la ligne de commande du fils, tout le moteur — une app
    qui ne le demande pas n'a pas à le charger, exactement comme `moteur_par_defaut`
    ne résout aucun fournisseur tant qu'aucun run ne part.
    """
    nom = (settings.hote_run or HOTE_RUN_DETACHE).strip().lower()
    if nom == HOTE_RUN_EN_PROCESS:
        return None
    if nom == HOTE_RUN_DETACHE:
        from maestro.controltower.hote_detache import HoteRunDetache

        return HoteRunDetache()
    raise ConfigError(
        f"MAESTRO_HOTE_RUN : hôte inconnu {nom!r} "
        f"(attendu : {' | '.join(HOTES_RUN)}, ou vide pour « {HOTE_RUN_DETACHE} »)."
    )
