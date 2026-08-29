"""Écrire à un pair vu de la couche fournisseur — le mot adressé (#720).

Un canal **de plus** vers l'agent, à côté de `demander_arbitrage` (#582) :
l'outil `ecrire_a_un_pair(destinataire, message)`, monté sur sa session par le
même serveur MCP in-process (`arbitrage.NOM_SERVEUR`, porte-outils depuis #718).
`AgentMessage` existait, complet, depuis #44 ; il ne lui manquait qu'un appelant
— les deux seuls producteurs étaient le handoff et le chat, c'est-à-dire la
machinerie. **Aucun agent n'écrivait à un pair.**

⚠ **Ce qui est livré est une TRACE ADRESSÉE, jamais une livraison**, et ce n'est
pas une prudence de rédaction : c'est la réserve mesurée de
[docs/31 §3.2](../../docs/31-decision-surface-ecriture-agents.md), et elle décide
de ce que ce module dit. Le transport (`maestro.messaging.mailbox`) est un
pub/sub **éphémère** — pas de rejeu, et `subscribe` doit être posé *avant* la
publication —, or un agent de Maestro n'existe que pendant sa tâche : un mot
adressé à un pair dont la tâche n'a pas démarré, ou est déjà finie, part dans un
canal que personne n'écoute. Les statuts `lu`/`traite` sont définis et
**assignés nulle part** : il n'y a aucun accusé de réception dans le dépôt.

D'où le partage retenu, repris du handoff qui le pratique déjà :

    Le journal est la livraison ; le pub/sub n'est que la notification.

Trois conséquences, et elles se lisent toutes ici plutôt que dans le code du
fournisseur — ce module porte le vocabulaire, `maestro.providers.claude` la seule
machinerie SDK, `maestro.engine.executor` la construction du message et son
journal :

- **la description dit cette vérité** (`DESCRIPTION_COURRIER`). C'est un critère
  du ticket et non une formule de style : un agent qui croirait être lu
  attendrait une réponse qui ne viendra pas, et ouvrir ce verbe en le présentant
  comme une messagerie fiable ajouterait un troisième producteur à un canal qui
  n'a qu'un lecteur — et pas celui qu'on vise ;
- **la réponse ne distingue pas « notifié » de « non notifié »** (`MOT_CONSIGNE`,
  texte unique). Ce n'est pas une omission : une publication qui *réussit* ne
  prouve rien non plus — sans abonné, elle disparaît tout autant. La seule chose
  vraie dans les deux cas est celle qu'on écrit, et la seule chose que l'agent
  pourrait faire d'un « la notification a échoué » serait de réessayer, c'est-à-
  dire de dupliquer la trace sur un canal qui n'a toujours personne au bout ;
- **le destinataire doit être un pair** (`DESTINATAIRE_MANQUANT`,
  `DESTINATAIRE_RESERVE`). Vide, c'est `DIFFUSION` : le mot partirait dans
  *toutes* les boîtes, ce que ce verbe ne promet pas — et il tomberait dans celle
  du relais de handoff, qui écoute justement la diffusion. Le refus de
  `AGENT_RELAIS` ferme la même porte par l'autre bout, et pour la même raison :
  ce n'est pas un pair, c'est l'identité sous laquelle la boucle relève les
  passages de relais (`maestro.messaging.handoff`). Un mot posté là porterait le
  `tache_id` de l'expéditeur et **résoudrait l'attente de handoff de sa propre
  tâche** — sans conséquence visible aujourd'hui, l'ordre étant tenu en process,
  et impossible à retrouver le jour où ça compterait.

Ce que ce verbe ne fait pas, et c'est la frontière avec les trois refus de
[docs/31 §3.3-3.5](../../docs/31-decision-surface-ecriture-agents.md) : il
**n'attend rien** et **ne touche pas au graphe du plan**. Il n'ajoute ni ne
retire de tâche, ne pose aucun statut, ne réassigne personne — il consigne une
observation et rend la main. C'est la règle unique de la note : un agent écrit ce
qu'il *observe*, jamais ce que le plan *décide*.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from maestro.messaging.handoff import AGENT_RELAIS
from maestro.providers.arbitrage import NOM_SERVEUR

#: Nom de l'outil tel que l'agent l'appelle, une fois préfixé par son serveur.
#: Un verbe à l'infinitif, comme `demander_arbitrage` : c'est ce que l'agent
#: fait, pas ce que le moteur en tire.
NOM_OUTIL_COURRIER = "ecrire_a_un_pair"

#: Le nom complet de l'outil dans une session SDK (`mcp__<serveur>__<outil>`) —
#: donc la forme sous laquelle une politique de permissions (#110) le désigne.
#: Le serveur est celui de l'arbitrage : nom réservé, porte-outils depuis #718,
#: et une seule définition de ce nom dans le dépôt (deux supports pour un même
#: fait sont la panne que #365 a supprimée ailleurs).
OUTIL_COURRIER = f"mcp__{NOM_SERVEUR}__{NOM_OUTIL_COURRIER}"

#: Ce que l'agent lit pour savoir **quand** appeler l'outil, et surtout **ce
#: qu'il obtient**. Les deux moitiés comptent, et la seconde est un critère du
#: ticket : la promesse tenue est la consignation, jamais la livraison. La
#: dernière phrase règle le débit — même parti pris que `demander_arbitrage`,
#: qui se présente comme un recours et non comme une étape : un verbe appelé par
#: acquit de conscience remplirait la frise de mots que personne n'a demandés.
DESCRIPTION_COURRIER = (
    "Adresse un mot à un autre agent du run : ce que tu viens de découvrir et qui "
    "le concerne, une contrainte qu'il rencontrera, une information qu'il ne "
    "trouvera pas dans sa propre tâche. Donne dans « destinataire » le nom de "
    "l'agent visé (son rôle dans le run) et dans « message » ce que tu as à lui "
    "dire, en clair. "
    "Ce que cet outil garantit est une TRACE ADRESSÉE, pas une livraison : ton "
    "mot est consigné au journal du run — durable, relu après coup, rattaché à ta "
    "tâche — et notifié en direct au destinataire seulement s'il est en train de "
    "travailler et à l'écoute. Il n'y a ni accusé de réception, ni réponse : "
    "l'appel rend la main aussitôt. "
    "N'attends donc rien de ce que tu écris, et n'appelle pas cet outil pour ce "
    "que ton compte-rendu final dira aussi bien."
)

#: Le schéma d'entrée de l'outil — à qui, et quoi. Rien d'autre : l'expéditeur,
#: la tâche et le run **ne sont pas demandés à l'agent**, ils sont fermés par
#: l'exécuteur (`maestro.engine.executor._courrier`), seul à les connaître et
#: seul à en répondre. Un agent qui les fournirait pourrait signer d'un autre nom.
SCHEMA_COURRIER: dict[str, type] = {"destinataire": str, "message": str}

#: Ce que lit l'agent dont le mot est parti. Il dit les trois choses dans
#: l'ordre où elles comptent : c'est écrit et durable, ce n'est pas garanti lu,
#: il n'y a rien à attendre. La dernière phrase est le rattrapage que la réserve
#: du §3.2 impose — le compte-rendu final, lui, est lu à coup sûr.
MOT_CONSIGNE = (
    "Mot consigné au journal du run et adressé à « {destinataire} » — c'est une "
    "trace durable, rattachée à ta tâche, que lira qui reprendra ce run. La "
    "notification en direct est au mieux : sans accusé de réception, "
    "« {destinataire} » ne la reçoit que s'il travaille au moment où tu écris. "
    "N'attends aucune réponse et poursuis ta tâche ; si ce que tu viens d'écrire "
    "pèse sur l'issue, redis-le dans ton compte-rendu final."
)

#: Destinataire vide. Rien n'est écrit, et surtout rien n'est diffusé : côté
#: transport, un destinataire vide **est** la diffusion (`mailbox.DIFFUSION`),
#: donc l'accepter enverrait le mot dans toutes les boîtes au lieu d'une seule.
#: Ce n'est pas un refus de ce que l'agent voulait faire — c'est un champ à
#: remplir, et le texte le dit comme tel plutôt que de le renvoyer sur son
#: intention.
DESTINATAIRE_MANQUANT = (
    "Aucun destinataire — rien n'a été écrit. Ce verbe est adressé : il lui faut "
    "le nom du pair que tu veux joindre (le rôle qui mène une autre tâche du "
    "run). Rappelle cet outil en le nommant."
)

#: Destinataire réservé : l'identité sous laquelle la boucle relève les passages
#: de relais. Le texte dit pourquoi et où aller à la place — sans quoi un agent
#: relancerait le même appel, l'orchestrateur étant le destinataire le plus
#: évident quand on veut « prévenir ».
DESTINATAIRE_RESERVE = (
    "« {relais} » n'est pas un pair mais l'identité de la boucle d'orchestration, "
    "qui relève par là les passages de relais : un mot posté sur cette boîte s'y "
    "mêlerait. Rien n'a été écrit. Rappelle cet outil en nommant l'agent que tu "
    "veux joindre — et ce qui s'adresse à l'orchestration, dis-le dans ton "
    "compte-rendu final, qui est lu."
)

#: Message vide. Même nature que `DESTINATAIRE_MANQUANT`, et surtout **pas un
#: refus** : rien n'a été soumis à personne, donc rien n'a été décidé. Le seul
#: geste utile est de rappeler l'outil avec le texte.
MESSAGE_MANQUANT = (
    "Message vide — rien n'a été écrit ni adressé. Rappelle cet outil en disant à "
    "« {destinataire} » ce que tu as à lui transmettre."
)

#: Le canal lui-même a levé : ni trace ni notification. C'est le seul cas où
#: l'agent apprend que **rien** n'a été écrit, et il faut le lui dire — la
#: promesse de ce verbe est la consignation, donc son échec est la seule nouvelle
#: qui change quelque chose pour lui. On ne laisse pas non plus l'exception
#: remonter : elle tuerait la tâche au moment où l'agent essayait d'être utile.
COURRIER_EN_ERREUR = (
    "Le mot n'a pas pu être consigné ({cause}) — rien n'a été écrit ni adressé. "
    "Poursuis ta tâche, et redis dans ton compte-rendu final ce que tu voulais "
    "transmettre."
)

#: Le contrat de la couche fournisseur : un destinataire, un message, et rien en
#: retour.
#:
#: Reçoit le destinataire et le message **tels que l'agent les a écrits** (non
#: vides, déjà nettoyés, destinataire déjà reconnu comme un pair) et ne rend
#: rien. Le fournisseur n'a rien à apprendre de l'issue : la seule information
#: qu'un booléen porterait — « la notification est-elle partie ? » — ne prouve
#: pas la livraison, ne se distingue pas de son contraire côté agent, et
#: l'inviterait à réessayer sur un canal sans lecteur.
#:
#: Il ne rend rien, mais il peut **lever** : ce que le fournisseur sert alors est
#: `COURRIER_EN_ERREUR`, et c'est le seul cas qui apprenne quelque chose à
#: l'agent. La différence avec `Arbitre` tient en un mot — celui-là transporte
#: une **décision**, celui-ci une **écriture**.
Courrier = Callable[[str, str], Awaitable[None]]


def destinataire_reserve(destinataire: str) -> bool:
    """`destinataire` est-il l'identité de la boucle plutôt qu'un pair (#720) ?

    La règle vit **une fois**, ici, et pas dans le corps de l'outil : le jour où
    une seconde identité serait réservée — un second relais, un consommateur de
    supervision —, deux formulations à tenir d'accord seraient le premier moyen
    d'en oublier une. La comparaison est insensible à la casse et aux espaces de
    bord, parce que ce qui arrive vient d'un modèle et non d'un appelant : refuser
    « Orchestrateur » et laisser passer « orchestrateur  » rendrait la garde
    contournable par une majuscule.
    """
    return destinataire.strip().casefold() == AGENT_RELAIS.casefold()
