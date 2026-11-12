"""Les routes de l'API Control Tower, un module par domaine, et l'ordre où l'application les inclut.

`create_app` (`maestro.controltower.app`) assemble les services et les pose dans le contexte
(`contexte.Contexte`) ; chaque module de ce paquet porte un `router` FastAPI dont les routes le
reçoivent par `ctx: Ctx`. Le contrat de chaque route est décrit dans docs/05 §6.

Carte des modules :

- `systeme` : la vitalité du service, ce que l'installation fait tourner, son arrêt ;
- `journal` : le journal requêtable des événements ;
- `taches` : les tâches, leur réassignation et leur référence de ticket ;
- `agents` : le parc d'agents et la capacité de chacun ;
- `executions` : lancer un run, le piloter, trancher ses arbitrages, lire ce qu'il a produit ;
- `sources` : le téléversement des sources et l'aperçu de leur lecture ;
- `couts` : la vue analytique des coûts et la dépense du fil ;
- `validations` : les validations humaines et les questions des agents ;
- `playbooks` : lecture, écriture, versions, propositions, rédaction et restauration ;
- `mcp` : la bibliothèque MCP, ses admissions, le pool, les activations, la migration ;
- `permissions` : la politique d'un agent et ses accords ;
- `projets` : la déclaration des projets, leur racine, les dossiers autorisés, le versionnement ;
- `outillage` : l'outillage et l'équipe d'un projet ;
- `fournisseurs` : le catalogue des fournisseurs et le fournisseur par défaut ;
- `accueil` : le premier lancement ;
- `catalogue` : le catalogue d'agents et leurs réglages ;
- `chat` : le fil, ses conversations, ses gestes et ses réponses en flux ;
- `evenements` : le WebSocket des événements.

Trois modules ne portent pas de routes : `contexte` (ce que les routes partagent), `requetes`
(les corps de requête) et `communs` (les aides de plusieurs domaines)."""

from __future__ import annotations

from fastapi import APIRouter

from maestro.controltower.routes import (
    accueil,
    agents,
    catalogue,
    chat,
    couts,
    evenements,
    executions,
    fournisseurs,
    journal,
    mcp,
    outillage,
    permissions,
    playbooks,
    projets,
    sources,
    systeme,
    taches,
    validations,
)

#: Les routeurs, dans l'ordre où `create_app` les inclut. FastAPI essaie les routes dans
#: l'ordre de leur inclusion : une route littérale doit donc précéder la route paramétrée qui la
#: recouvrirait. Entre deux modules, cet ordre est celui que l'API a toujours eu.
ROUTEURS: tuple[APIRouter, ...] = (
    systeme.router,
    journal.router,
    taches.router,
    agents.router,
    executions.router,
    sources.router,
    couts.router,
    validations.router,
    playbooks.router,
    mcp.router,
    permissions.router,
    projets.router,
    outillage.router,
    fournisseurs.router,
    accueil.router,
    catalogue.router,
    chat.router,
    evenements.router,
)
