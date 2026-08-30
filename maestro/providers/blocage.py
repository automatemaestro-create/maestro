"""Le blocage déclaré par l'agent vu de la couche fournisseur (#719).

Un agent qui bute n'avait **aucun mot pour le dire**. Il sait écrire ce qu'il
*découvre* (un ticket, #187), ce qu'il *prévoit* (sa checklist, #489) et ce qu'il
*s'apprête à faire* (un arbitrage, #582) — mais rien de ce qu'il **subit**. Il
produit un livrable vide en fin de tâche, et la cause n'apparaît nulle part.
C'est le constat qui a ouvert #355 : 53 minutes perdues sur le run du 14 août
sans qu'aucun écran ne dise pourquoi.

Ce module porte le vocabulaire du second verbe du serveur MCP `maestro`
(`signaler_blocage`), à côté de celui du premier
(`maestro.providers.arbitrage`) — le porte-outils de #718 les monte tous deux
sans qu'ils se croisent.

⚠ **Ne pas attendre de réponse est le cœur du verbe, pas un raccourci**, et
c'est ce qui trace ses deux frontières :

- avec `demander_arbitrage` (#582), qui **attend** : l'appel se bloque, une
  personne tranche, l'agent reçoit oui ou non. C'est un acte *soumis*. Ici rien
  n'est soumis à personne — on consigne, et on rend la main ;
- avec **#647**, qui cadre un canal « question » dont la réponse serait du
  texte. Un `signaler_blocage` bloquant serait un troisième canal d'arbitrage,
  à tenir d'accord avec les deux autres (docs/31 §3.1).

D'où la signature de `Signaleur` : **synchrone et sans valeur de retour**. Le
type dit ce que la prose promet — il n'y a rien à attendre, et aucun appelant ne
peut décider d'attendre quand même sans changer le contrat au vu de tous. C'est
la seule des quatre voies du fournisseur qui parte de l'agent sans en revenir.

⚠ **Il ne change pas non plus le statut de la tâche**, et ce n'est pas une
timidité : « changer le statut » est un verbe **refusé** par docs/31 §3.4, parce
qu'il fausserait la cascade de #43 — un agent qui poserait « bloquée » sur sa
propre tâche condamnerait tout son aval alors qu'il travaille encore. À ne pas
confondre, donc, avec `maestro.engine.loop._consigne_blocage`, qui porte le
blocage **hérité** d'une dépendance en échec (`STATUT_BLOQUEE`, tâche jamais
exécutée). Les deux mots se ressemblent et ne disent pas la même chose : là-bas
la tâche est morte, ici l'agent parle.

Ce que le verbe coûte : **rien de mesurable**. Une étape de journal à
`StepUsage()` vide — hors grand livre —, un événement de plus sur un pont qui en
porte une douzaine, une entrée de plus dans la frise de #355.
"""

from __future__ import annotations

from collections.abc import Callable

from maestro.providers.arbitrage import NOM_SERVEUR

#: Nom de l'outil tel que l'agent l'appelle, une fois préfixé par son serveur.
NOM_OUTIL = "signaler_blocage"

#: Le nom complet de l'outil dans une session SDK (`mcp__<serveur>__<outil>`) —
#: donc la forme sous laquelle une politique de permissions (#110) le désigne.
#: Le serveur est celui de `maestro.providers.arbitrage` (nom **réservé**), et il
#: est importé plutôt que réécrit : deux littéraux « maestro » seraient deux
#: serveurs le jour où l'un des deux change.
OUTIL_BLOCAGE = f"mcp__{NOM_SERVEUR}__{NOM_OUTIL}"

#: Ce que l'agent lit pour savoir **quand** appeler l'outil. Écrit comme celle de
#: `demander_arbitrage` (#582) pour la même raison : un **recours**, jamais une
#: étape. Un verbe qu'on appellerait par acquit de conscience noierait la frise
#: sous des blocages qui n'en sont pas — et c'est bien la description qui règle
#: ce débit, la politique de permissions tenant le droit (docs/31 §7).
#:
#: Elle dit aussi, en toutes lettres, que **rien ne répondra**. Sans cette
#: phrase, un agent peut légitimement croire qu'il vient de poser une question et
#: attendre son tour suivant une réponse qui n'arrivera jamais — c'est-à-dire
#: reproduire, par malentendu, le silence que le verbe existe pour lever.
DESCRIPTION_OUTIL = (
    "Signale que tu es bloqué : dis dans « raison » ce qui t'empêche d'avancer, "
    "précisément (ce que tu as tenté, ce qui a échoué, ce qui te manque). "
    "L'appel n'attend aucune réponse et ne te suspend pas — il consigne ta "
    "raison pour qu'un humain la lise pendant que la tâche tourne. Poursuis "
    "aussitôt : contourne si tu peux, sinon fais au mieux et dis-le dans ton "
    "compte-rendu final. N'appelle pas cet outil pour une difficulté ordinaire "
    "de ta tâche, ni pour demander une autorisation — cela, c'est "
    "« demander_arbitrage »."
)

#: Le schéma d'entrée de l'outil — un seul champ, celui que l'humain lira.
SCHEMA_ENTREE: dict[str, type] = {"raison": str}

#: Ce que lit l'agent qui a appelé l'outil sans rien dire. Un blocage sans motif
#: n'apprend rien à personne : « il est bloqué » est précisément ce que la frise
#: montrait déjà avant ce verbe, et la seule chose qu'il apporte est **la
#: raison** — celle qu'aucune règle de détection ne saura jamais produire.
#:
#: Rien n'est donc consigné, et le texte le dit plutôt que de laisser croire au
#: contraire : le seul geste utile est de rappeler l'outil, cette fois en
#: décrivant le blocage. Même parti pris que `arbitrage.RAISON_MANQUANTE`, et
#: pour la même raison — un appel vide n'est pas un refus, il n'a rien produit.
RAISON_MANQUANTE = (
    "Aucune raison fournie — rien n'a été consigné. Un blocage n'apprend "
    "quelque chose que par son motif : rappelle cet outil en disant ce qui te "
    "bloque, ce que tu as tenté et ce qui te manque."
)

#: Ce que lit l'agent dont la déclaration **est** partie. Il dit deux choses, et
#: la seconde compte autant que la première : c'est consigné, et **personne ne
#: répondra**. Un accusé de réception qui s'arrêterait à « c'est noté » laisserait
#: ouverte la question « dois-je attendre ? », qui est exactement celle que ce
#: verbe n'a pas le droit de poser.
BLOCAGE_CONSIGNE = (
    "Blocage consigné — il est visible dans la frise d'activité du run. "
    "Personne ne va te répondre ici : poursuis ta tâche, contourne si tu peux, "
    "et dis dans ton compte-rendu final ce que tu n'as pas pu faire."
)

#: Ce que rend la couche fournisseur quand le canal lui-même casse (callback en
#: erreur). On le **dit** à l'agent, plutôt que d'avaler l'échec : il vient de
#: signaler qu'il bute, et le laisser croire que sa raison est partie alors
#: qu'elle s'est perdue le priverait de la seule autre voie qui lui reste — son
#: compte-rendu final.
#:
#: Ce n'est pas un refus et il n'y a rien à réessayer : l'exception ne remonte
#: jamais (elle tuerait la tâche au moment où l'agent se montre coopératif), et
#: le verbe n'a de toute façon rien à autoriser ni à interdire.
CANAL_EN_ERREUR = (
    "Blocage NON consigné — le canal est en erreur ({cause}). Poursuis ta tâche "
    "et rapporte ce blocage dans ton compte-rendu final, qui est le seul endroit "
    "où il sera lu."
)

#: Le contrat de la couche fournisseur : une raison, et rien en retour.
#:
#: Reçoit la raison **telle que l'agent l'a écrite** (non vide, déjà nettoyée).
#: Synchrone et sans retour, à dessein : c'est la forme la plus courte de « il
#: n'attend aucune réponse et ne suspend jamais l'agent ». Un `Awaitable`
#: laisserait la porte ouverte à un appelant qui attendrait quelque chose, et le
#: jour où quelqu'un l'attendrait, ce verbe serait devenu le troisième canal
#: d'arbitrage que docs/31 §3.1 écarte.
#:
#: C'est aussi ce qui le range avec `on_refus` et `on_activite` — les voies qui
#: **rapportent** — et non avec `Arbitre`, la seule qui revienne vers l'agent.
Signaleur = Callable[[str], None]
