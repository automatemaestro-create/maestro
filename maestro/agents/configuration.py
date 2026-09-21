"""La configuration d'agent **d'un projet** : les six dépôts d'un bloc (ticket #1038).

Un agent appartient à un projet ([docs/37 §2.1](../../docs/37-decision-equipe-sur-mesure.md)),
et son appartenance ne se règle pas dépôt par dépôt : sa définition, son
playbook, ses autorisations, ses serveurs MCP et sa capacité sont **la même**
question posée cinq fois (six avec les surcharges de réglages, #259). Ce module
en fait un objet unique, pour que le projet actif soit cadré **une fois** — par
l'API à la requête, par l'exécuteur à la tâche — au lieu de six fois, ce qui
finirait par un dépôt qu'on oublie et une politique d'autorisations venue d'un
autre projet.

Le rangement lui-même — où vit la configuration d'un projet, et ce dont elle
hérite du gabarit — est dans `maestro.agents.rangement`. Ici, seulement
l'assemblage.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Self

from maestro.agents.capacity import CapacityStore
from maestro.agents.catalog import Agent
from maestro.agents.mcp import McpStore
from maestro.agents.permissions import PermissionStore
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.store import (
    AgentStore,
    SurchargeStore,
    catalogue,
    gabarits_du_code,
)
from maestro.config import Settings, load_settings


@dataclass(frozen=True)
class ConfigurationAgents:
    """Les six dépôts de configuration d'agent, cadrés (ou non) sur un projet.

    `projet_id` à None désigne le niveau des **gabarits** — la racine de chaque
    dépôt, ce que le code lisait avant ce lot et ce que #1039 consultera pour
    proposer une équipe. Il n'est pas dérivable des dépôts (`AgentStore`
    n'hérite de rien, donc ne porte pas de repli d'où le déduire) : il est
    **porté** par l'objet, et c'est lui qu'une réponse d'API rappelle.
    """

    agents: AgentStore
    surcharges: SurchargeStore
    playbooks: PlaybookStore
    permissions: PermissionStore
    mcp: McpStore
    capacites: CapacityStore
    projet_id: str | None = None

    @classmethod
    def default(cls, settings: Settings | None = None) -> Self:
        """Les six dépôts configurés du poste, au niveau des gabarits.

        Chaque dépôt résout **sa** racine (`MAESTRO_*_DIR`, sinon `core/…`) :
        aucun chemin n'est recopié ici.
        """
        settings = settings or load_settings()
        return cls(
            agents=AgentStore.default(settings),
            surcharges=SurchargeStore.default(settings),
            playbooks=PlaybookStore.default(settings),
            permissions=PermissionStore.default(settings),
            mcp=McpStore.default(settings),
            capacites=CapacityStore.default(settings),
        )

    def pour_projet(self, projet_id: str | None) -> Self:
        """La même configuration, cadrée sur `projet_id` — ou celle-ci si None.

        `None` rend l'objet **intact** : le niveau des gabarits est un niveau,
        pas un cas particulier, et une API sans projet actif comme un moteur
        sans projet de tâche continuent d'y lire ce qu'ils y lisaient.
        """
        if projet_id is None:
            return self
        return replace(
            self,
            agents=self.agents.pour_projet(projet_id),
            surcharges=self.surcharges.pour_projet(projet_id),
            playbooks=self.playbooks.pour_projet(projet_id),
            permissions=self.permissions.pour_projet(projet_id),
            mcp=self.mcp.pour_projet(projet_id),
            capacites=self.capacites.pour_projet(projet_id),
            projet_id=projet_id,
        )

    def catalogue(self, modele: str | None = None) -> tuple[Agent, ...]:
        """Le catalogue effectif à ce niveau : les agents **de ce niveau**, et eux seuls.

        Vide sur un projet qui n'a pas encore recruté (#1042) : les cinq fiches
        du code n'y entrent plus, elles sont des gabarits (`gabarits_du_code`).
        """
        return catalogue(self.agents, modele)

    def gabarits_du_code(self, modele: str | None = None) -> tuple[Agent, ...]:
        """Les gabarits de rôle, leurs surcharges de **ce niveau** posées (#1042).

        Servi à côté du catalogue et jamais mélangé avec lui : un gabarit se
        consulte et se règle, il ne travaille pas. Les surcharges suivent le
        rangement de #1038 — celles du projet par-dessus celles du gabarit.
        """
        return gabarits_du_code(self.surcharges, modele)
