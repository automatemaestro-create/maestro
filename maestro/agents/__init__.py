"""Agents exécutants de Maestro : catalogue, profils outillés et runtime (tickets #6, #35).

Expose la forme d'un agent (`Agent` : compétences, modèle, prompt système) et le
catalogue par défaut du POC (`DEFAULT_AGENTS`). Le routeur (`maestro.router`) s'en
sert pour l'auto-assignation ; le moteur (`maestro.engine`) pour l'exécution.

    from maestro.agents import DEFAULT_AGENTS

Le catalogue est statique au POC ; il proviendra de la base en V1 (table AGENT)
sans changer ce contrat.

Au-delà de l'identité (`Agent`), certains rôles disposent d'un **runtime outillé** —
un sous-agent du SDK qui exécute une tâche de bout en bout dans un espace isolé et
renvoie un livrable exploitable (`AgentOutcome`). Le runtime est **générique**
(`AgentRuntime`, ticket #35) et paramétré par un profil de rôle (`RoleProfile`) :
le Développeur (`DEVELOPER_PROFILE`, ticket #4), la Base de données
(`DATABASE_PROFILE`, ticket #5), le QA / Testeur (`QA_PROFILE`, ticket #45), le
DevOps (`DEVOPS_PROFILE`, ticket #67) et le Designer (`DESIGNER_PROFILE`, ticket #68).
Ajouter un rôle outillé = déclarer un profil et l'inscrire dans `TOOLED_PROFILES`.
"""

from __future__ import annotations

from maestro.agents.catalog import DEFAULT_AGENTS, Agent, agents_pour
from maestro.agents.database import DATABASE_PROFILE
from maestro.agents.designer import DESIGNER_PROFILE
from maestro.agents.developer import DEVELOPER_PROFILE
from maestro.agents.devops import DEVOPS_PROFILE
from maestro.agents.qa import QA_PROFILE
from maestro.agents.runtime import (
    DEFAULT_TOOLS,
    AgentOutcome,
    AgentRuntime,
    RoleProfile,
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
    provider: ModelProvider, *, model: str | None = None
) -> dict[str, AgentRuntime]:
    """Construit les runtimes outillés par défaut, indexés par nom d'agent du catalogue.

    C'est le câblage que consomme la boucle d'orchestration : une tâche routée vers
    l'un de ces noms (`developpeur`, `bdd`, `devops`, `designer`, `qa`) s'exécute via
    son runtime outillé plutôt que par un appel texte. `model` (optionnel, #69)
    impose un modèle unique à tous les rôles — sinon chacun garde celui de son profil.
    """
    return {
        profile.nom: AgentRuntime(provider, profile, model=model)
        for profile in TOOLED_PROFILES
    }


__all__ = [
    "DATABASE_PROFILE",
    "DEFAULT_AGENTS",
    "DEFAULT_TOOLS",
    "DESIGNER_PROFILE",
    "DEVELOPER_PROFILE",
    "DEVOPS_PROFILE",
    "QA_PROFILE",
    "TOOLED_PROFILES",
    "Agent",
    "AgentOutcome",
    "AgentRuntime",
    "RoleProfile",
    "agents_pour",
    "default_runtimes",
]
