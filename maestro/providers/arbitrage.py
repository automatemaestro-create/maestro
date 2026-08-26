"""L'agent lève la main : le canal par lequel il demande lui-même l'arbitrage (#582).

Le déclencheur d'arbitrage reste **le nôtre** — c'est tout ce qui le rend un
garde-fou : une classification faite par le gardé céderait des faux positifs
bruyants contre des faux négatifs silencieux, et le fail-safe d'aujourd'hui
(« pas de validateur → refus », EF-08/ENF-04) ne tient que parce que la
décision de classer n'appartient pas à l'agent (cadrage de #573). Ce module
n'y touche pas : il ouvre un canal **de plus**.

Ce qui manquait tenait à ceci : un agent qui *sait* qu'il s'apprête à quelque
chose d'irréversible — au-delà de ce que la politique avait prévu — n'avait que
le choix entre le faire et ne pas le faire. Il peut désormais le dire, et
recevoir une réponse. **Son silence ne dispense de rien** : rien ici ne
remplace la classification, et un agent qui n'appelle jamais l'outil est
exactement dans le régime d'avant ce lot.

Trois choses à connaître avant d'y toucher :

- **le couplage à l'outillage vit ici**, comme pour la checklist
  (`maestro.providers.checklist`) : le contrat de la couche fournisseur est un
  simple `Arbitre` — une raison en entrée, une décision en sortie — au même
  titre que `on_refus`, `on_activite` et `on_etapes`. Un fournisseur qui n'a
  pas d'outillage n'expose jamais le canal, et le moteur ne s'en aperçoit pas ;
- **un refus n'est pas une panne d'outil**. La réponse servie à l'agent est un
  résultat normal, porteur d'un motif exploitable : « ne fais pas cette action,
  poursuis la tâche sans elle ». Rendue en erreur, elle inviterait à réessayer
  — c'est-à-dire à insister contre la décision qu'on vient de lui rendre ;
- **le canal refuse plutôt que de disparaître**. Sans validateur configuré,
  l'outil reste monté et rend le refus du fail-safe : c'est le régime que le
  critère demande, et il vaut mieux qu'une absence — un agent qui ne trouve pas
  l'outil ne sait pas s'il n'existe pas ou s'il a mal cherché, là où un refus
  motivé se lit et se rapporte.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

#: Nom du serveur MCP **in-process** qui porte l'outil. Le nom est **réservé** :
#: un serveur déclaré par un agent (`core/mcp/<agent>.json`) qui le porterait
#: serait supplanté par celui-ci. C'est le sens sûr des deux — le canal d'un
#: garde-fou ne se laisse pas masquer par une déclaration.
NOM_SERVEUR = "maestro"

#: Nom de l'outil tel que l'agent l'appelle, une fois préfixé par son serveur.
NOM_OUTIL = "demander_arbitrage"

#: Le nom complet de l'outil dans une session SDK (`mcp__<serveur>__<outil>`) —
#: donc la forme sous laquelle une politique de permissions (#110) le désigne.
OUTIL_ARBITRAGE = f"mcp__{NOM_SERVEUR}__{NOM_OUTIL}"

#: Ce que l'agent lit pour savoir **quand** appeler l'outil. Écrit pour être un
#: recours et non une étape : « au-delà de ce qui est prévu », jamais « avant
#: chaque action ». Un outil qu'on appellerait par acquit de conscience ferait
#: de chaque tâche une file d'attente humaine.
DESCRIPTION_OUTIL = (
    "Demande un arbitrage humain avant une action que tu juges irréversible ou "
    "hors de ce que ta tâche prévoyait (suppression, déploiement, écriture hors "
    "de ton espace de travail, dépense…). Décris dans « raison » l'action exacte "
    "et pourquoi tu hésites. L'appel attend la réponse : approuvée, réalise "
    "l'action ; refusée, ne la réalise pas et poursuis la tâche sans elle. "
    "N'appelle pas cet outil pour une action ordinaire de ta tâche."
)

#: Le schéma d'entrée de l'outil — un seul champ, celui que l'humain lira.
SCHEMA_ENTREE: dict[str, type] = {"raison": str}

#: Ce que lit l'agent qui a appelé l'outil sans rien dire. Un arbitrage sans
#: raison n'est pas arbitrable : personne ne peut trancher « il demande quelque
#: chose », et rien n'a été soumis à qui que ce soit.
#:
#: Ce n'est donc **pas** un refus, et c'est la nuance qui a coûté une relecture :
#: la réponse d'un refus dit « ne fais pas cette action, poursuis sans elle » —
#: exactement ce qu'il ne faut pas dire ici, où l'appel n'a rien décidé du tout.
#: Le seul geste utile est de rappeler l'outil, cette fois en décrivant l'action.
RAISON_MANQUANTE = (
    "Aucune raison fournie — la demande n'a été soumise à personne. Un arbitrage "
    "se tranche sur l'action décrite : rappelle cet outil en disant précisément "
    "ce que tu veux faire et pourquoi tu hésites."
)

#: Ce que rend la couche fournisseur quand le canal d'arbitrage lui-même casse
#: (callback en erreur). Refus, comme partout ailleurs sur ce chemin : une
#: panne d'observation n'a jamais autorisé une action sensible.
CANAL_EN_ERREUR = "canal d'arbitrage en erreur ({cause}) — refus par défaut"

#: Le contrat de la couche fournisseur : une raison, une décision.
#:
#: Reçoit la raison **telle que l'agent l'a écrite** (non vide, déjà nettoyée) et
#: rend `(approuvée ?, détail traçable)` — exactement le couple de
#: `Guardrails.demande_validation`, dont c'est le seul appelant en amont. Le
#: fournisseur n'en fabrique jamais la décision : il la demande, l'attend et la
#: transmet.
Arbitre = Callable[[str], Awaitable[tuple[bool, str]]]


def reponse(approuve: bool, detail: str) -> str:
    """Compose ce que l'agent lit en retour de sa demande — décision et suite à donner.

    Les deux branches disent **la décision, son motif, et ce qu'il faut en
    faire** : sans la troisième moitié, un agent approuvé peut hésiter et un
    agent refusé peut réessayer. Le motif vient du garde-fou (`detail`) et n'est
    jamais réécrit ici — « aucun validateur humain configuré » et « refusée par
    le validateur humain » ne se répondent pas de la même façon, et c'est à
    l'agent d'en tenir compte dans son compte-rendu.
    """
    if approuve:
        return (
            f"Arbitrage approuvé — {detail}. "
            "Réalise l'action que tu as décrite, puis poursuis ta tâche."
        )
    return (
        f"Arbitrage refusé — {detail}. "
        "Ne réalise pas cette action. Poursuis ta tâche sans elle, et dis dans "
        "ton compte-rendu final ce que tu n'as pas pu faire et pourquoi."
    )
