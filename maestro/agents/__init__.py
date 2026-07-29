"""Agents exécutants de Maestro : catalogue, profils outillés et runtime (tickets #6, #35).

Expose la forme d'un agent (`Agent` : compétences, modèle, prompt système) et le
catalogue par défaut du POC (`DEFAULT_AGENTS`). Le routeur (`maestro.router`) s'en
sert pour l'auto-assignation ; le moteur (`maestro.engine`) pour l'exécution.

    from maestro.agents import DEFAULT_AGENTS

Le catalogue n'est plus figé au code (#72, EF-03) : des **agents personnalisés**
se définissent hors du code (`maestro.agents.store`, dépôt `core/agents/`, API
Control Tower `/api/catalogue`) et `catalogue()` assemble le catalogue effectif —
les agents par défaut, puis les personnalisés. En V1 ce dépôt passera en base
(table AGENT) sans changer ce contrat.

Au-delà de l'identité (`Agent`), certains rôles disposent d'un **runtime outillé** —
un sous-agent du SDK qui exécute une tâche de bout en bout dans un espace isolé et
renvoie un livrable exploitable (`AgentOutcome`). Le runtime est **générique**
(`AgentRuntime`, ticket #35) et paramétré par un profil de rôle (`RoleProfile`) :
le Développeur (`DEVELOPER_PROFILE`, ticket #4), la Base de données
(`DATABASE_PROFILE`, ticket #5), le QA / Testeur (`QA_PROFILE`, ticket #45), le
DevOps (`DEVOPS_PROFILE`, ticket #67) et le Designer (`DESIGNER_PROFILE`, ticket #68).
Ajouter un rôle outillé = déclarer un profil et l'inscrire dans `TOOLED_PROFILES`.

Les **playbooks** (instructions d'un rôle) sont éditables hors du code via un
stockage versionné (`maestro.agents.playbooks`, ticket #76) et appliqués **à
chaud** (#78) : l'exécuteur relit la version courante à chaque tâche, une édition
publiée vaut pour l'exécution suivante sans redémarrage — un agent jamais édité
garde exactement ses prompts du code.

La **capacité** d'un agent (#86, EF-21) — activé/désactivé, plafond d'instances —
se règle de la même façon hors du code (`maestro.agents.capacity`, dépôt
`core/capacite/`, API Control Tower `/api/agents/{nom}/capacite`) et est relue à
chaud à chaque tâche : un agent désactivé ne reçoit plus de tâches, ses
exécutions simultanées sont bornées à son plafond.

Les **serveurs MCP** d'un agent (#104) se déclarent hors du code
(`maestro.agents.mcp`, dépôt `core/mcp/<agent>.json` versionné — secrets par
référence `${VAR}`, jamais en clair) et sont relus à chaud à chaque tâche puis
montés par la couche SDK sur les exécutions outillées de l'agent.

Les **secrets** de ces intégrations (#109) sortent de l'environnement global via
le **coffre par agent** (`maestro.agents.secrets`, dépôt local `core/secrets/`
jamais versionné) : dès qu'il est provisionné, chaque agent ne résout ses
références `${VAR}` que dans son propre coffre, et toute valeur servie est
masquée si elle réapparaît en sortie (journal, traces, livrables).

Les **permissions** d'un agent (#110) se déclarent hors du code de la même
façon (`maestro.agents.permissions`, dépôt `core/permissions/<agent>.json`
versionné) : une politique allow/deny par outil (et par serveur MCP), relue à
chaud à chaque tâche et appliquée à l'exécution — outils refusés retirés de la
session, serveurs refusés jamais montés, violation au vol refusée proprement
et tracée sans condamner le run.
"""

from __future__ import annotations

from maestro.agents.capacity import (
    INSTANCES_DEFAUT,
    CapaciteAgent,
    CapacityStore,
    JaugeInstances,
)
from maestro.agents.catalog import DEFAULT_AGENTS, Agent, agents_pour
from maestro.agents.database import DATABASE_PROFILE
from maestro.agents.designer import DESIGNER_PROFILE
from maestro.agents.developer import DEVELOPER_PROFILE
from maestro.agents.devops import DEVOPS_PROFILE
from maestro.agents.mcp import TYPES_SERVEUR, IntegrationMcp, McpStore, ServeurMcp
from maestro.agents.permissions import PermissionStore, PolitiqueOutils
from maestro.agents.playbooks import (
    PLAYBOOK_DEFAUTS,
    PlaybookDefaut,
    PlaybookStore,
    PlaybookVersion,
    avec_playbooks,
)
from maestro.agents.qa import QA_PROFILE
from maestro.agents.runtime import (
    DEFAULT_TOOLS,
    AgentOutcome,
    AgentRuntime,
    RoleProfile,
)
from maestro.agents.secrets import SecretStore
from maestro.agents.store import (
    NOMS_RESERVES,
    AgentDefinition,
    AgentStore,
    catalogue,
)
from maestro.providers.base import ModelProvider

#: Les profils outillés du POC, dans l'ordre du catalogue. La boucle d'orchestration
#: (`maestro.engine`) route les tâches assignées à ces rôles vers leur runtime.
TOOLED_PROFILES: tuple[RoleProfile, ...] = (
    DEVELOPER_PROFILE,
    DATABASE_PROFILE,
    DEVOPS_PROFILE,
    DESIGNER_PROFILE,
    QA_PROFILE,
)


def default_runtimes(
    provider: ModelProvider,
    *,
    model: str | None = None,
    playbooks: PlaybookStore | None = None,
) -> dict[str, AgentRuntime]:
    """Construit les runtimes outillés par défaut, indexés par nom d'agent du catalogue.

    C'est le câblage que consomme la boucle d'orchestration : une tâche routée vers
    l'un de ces noms (`developpeur`, `bdd`, `devops`, `designer`, `qa`) s'exécute via
    son runtime outillé plutôt que par un appel texte. `model` (optionnel, #69)
    impose un modèle unique à tous les rôles — sinon chacun garde celui de son profil.
    `playbooks` (optionnel, #76) **fige** le prompt système de chaque rôle sur son
    playbook versionné du moment — un instantané au câblage ; l'application à
    chaud (#78) passe, elle, par `LocalExecutor(playbooks=...)`, qui relit le
    dépôt à chaque tâche et surcharge ces runtimes ponctuellement.
    """
    return {
        profile.nom: AgentRuntime(
            provider,
            profile,
            model=model,
            system_prompt=(
                playbooks.prompt_systeme(profile.nom, profile.prompt_systeme)
                if playbooks is not None
                else None
            ),
        )
        for profile in TOOLED_PROFILES
    }


__all__ = [
    "DATABASE_PROFILE",
    "DEFAULT_AGENTS",
    "DEFAULT_TOOLS",
    "DESIGNER_PROFILE",
    "DEVELOPER_PROFILE",
    "DEVOPS_PROFILE",
    "INSTANCES_DEFAUT",
    "NOMS_RESERVES",
    "PLAYBOOK_DEFAUTS",
    "QA_PROFILE",
    "TOOLED_PROFILES",
    "TYPES_SERVEUR",
    "Agent",
    "AgentDefinition",
    "AgentOutcome",
    "AgentRuntime",
    "AgentStore",
    "CapaciteAgent",
    "CapacityStore",
    "IntegrationMcp",
    "JaugeInstances",
    "McpStore",
    "PermissionStore",
    "PlaybookDefaut",
    "PlaybookStore",
    "PlaybookVersion",
    "PolitiqueOutils",
    "RoleProfile",
    "SecretStore",
    "ServeurMcp",
    "agents_pour",
    "avec_playbooks",
    "catalogue",
    "default_runtimes",
]
