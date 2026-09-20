"""La décision qu'un agent tranche seul, vue de la couche fournisseur (#1024).

Le socle des playbooks disait l'inverse de ce que
[docs/37 §2.2](../../docs/37-decision-equipe-sur-mesure.md) décide : « personne ne
répondra en cours de tâche, préfère une hypothèse à une question ». Le régime
retenu est en deux moitiés, et celle-ci est la seconde :

- ce qui **demande un humain** se demande — un acte irréversible, un coût ou une
  portée qui dépasse le brief, un choix produit à deux issues défendables ;
- **tout le reste se tranche seul, et se consigne.**

C'est cette dernière virgule que ce module sert. Un agent tranchait déjà seul
(c'est le régime sénior de #293) ; ce qu'il tranchait ne se lisait nulle part
avant son compte-rendu final, c'est-à-dire **après**. Un run d'une heure qui
prend douze décisions n'en montrait aucune pendant qu'il tournait, et un run en
échec les emportait toutes. L'autonomie n'est acceptable que si elle se vérifie
après coup : c'est le cadrage du parent #1019, et le motif du lot #1026 qui les
fera lire dans la vue du run.

Quatrième verbe du serveur MCP in-process `maestro` (porte-outils de #718), à
côté de `demander_arbitrage` (#582), `signaler_blocage` (#719) et
`ecrire_a_un_pair` (#720). Il tient sa place dans la règle unique de
[docs/31 §2](../../docs/31-decision-surface-ecriture-agents.md) — *le verbe
écrit-il une observation, ou une décision sur le plan ?* — du côté **ouvert** :
« voici ce que j'ai tranché » dit ce qui **est**, s'ajoute, ne retire rien, et le
moteur peut l'ignorer sans que le run change de sens. Il n'amende aucune tâche,
ne pose aucun statut, ne réassigne personne.

⚠ **Il ne demande rien et n'attend rien**, et c'est ce qui trace ses trois
frontières :

- avec `demander_arbitrage` (#582), qui **attend** : là, l'agent hésite devant un
  acte et une personne tranche. Ici, l'agent a **déjà tranché** — l'appel n'est
  pas une demande d'accord, et le présenter comme telle ferait demander la
  permission pour ce que le régime sénior donne le droit de décider ;
- avec `signaler_blocage` (#719), qui dit ce que l'agent **subit**. Ici il dit ce
  qu'il **fait** : les deux consignent sans attendre, et c'est leur seul point
  commun ;
- avec la **question libre** de #1023, qui suspend la tâche jusqu'à la réponse ou
  jusqu'à une borne. Une décision consignée n'a personne au bout du fil ; c'est
  même la raison pour laquelle on l'écrit.

D'où la signature de `Consigneur` : **synchrone et sans valeur de retour**, celle
de `Signaleur` au mot près. Le type dit ce que la prose promet — il n'y a rien à
attendre, et aucun appelant ne peut décider d'attendre quand même sans changer le
contrat au vu de tous.

⚠ Ses constantes portent les **mêmes noms** que celles de ses voisins
`maestro.providers.blocage` et `maestro.providers.courrier` — `NOM_OUTIL`,
`DESCRIPTION_OUTIL`, `SCHEMA_ENTREE`, `CANAL_EN_ERREUR` : `claude.py` les importe
**qualifiées** (`decision.NOM_OUTIL`), si bien que le vocabulaire de chaque verbe
se lit avec le nom du verbe devant.

Ce que le verbe coûte : **rien de mesurable**, comme les deux autres verbes
d'écriture. Une étape de journal à `StepUsage()` vide — hors grand livre —, un
événement de plus sur un pont qui en porte une douzaine. Consigner ce qu'on a
décidé ne doit rien coûter, faute de quoi se taire serait la stratégie payante.
"""

from __future__ import annotations

from collections.abc import Callable

from maestro.providers.arbitrage import NOM_SERVEUR

#: Nom de l'outil tel que l'agent l'appelle, une fois préfixé par son serveur.
#: Un verbe à l'infinitif, comme ses trois voisins : c'est ce que l'agent fait,
#: pas ce que le moteur en tire.
NOM_OUTIL = "consigner_decision"

#: Le nom complet de l'outil dans une session SDK (`mcp__<serveur>__<outil>`) —
#: donc la forme sous laquelle une politique de permissions (#110) le désigne.
#: Le serveur est celui de `maestro.providers.arbitrage` (nom **réservé**), et il
#: est importé plutôt que réécrit : deux littéraux « maestro » seraient deux
#: serveurs le jour où l'un des deux change.
OUTIL_DECISION = f"mcp__{NOM_SERVEUR}__{NOM_OUTIL}"

#: Ce que l'agent lit pour savoir **quand** appeler l'outil, et ce qu'il obtient.
#:
#: Le débit se règle ici, et il ne se règle pas comme celui des trois autres
#: verbes. `demander_arbitrage` et `signaler_blocage` sont des **recours** : on
#: les présente comme l'exception, sans quoi chaque tâche deviendrait une file
#: d'attente humaine. Celui-ci est la contrepartie d'un droit — l'agent tranche
#: seul, donc il consigne — et le sous-employer le viderait de son objet. La
#: borne est donc posée sur la **nature** de la décision et non sur sa rareté :
#: ce qui mérite d'être écrit ici est ce qui mériterait d'être écrit dans
#: « Décisions & arbitrages » du compte-rendu final, et rien d'autre.
#:
#: La dernière moitié dit, en toutes lettres, que **rien ne répondra** — même
#: raison qu'en #719 : sans cette phrase, un agent peut croire qu'il vient de
#: soumettre quelque chose et attendre un tour de plus une réponse qui n'arrivera
#: jamais.
DESCRIPTION_OUTIL = (
    "Consigne une décision que tu as prise seul, au moment où tu la prends. "
    "Mets dans « decision » ce que tu as tranché (l'option retenue, l'hypothèse "
    "que tu retiens faute d'entrée, ce que tu écartes du périmètre) et dans "
    "« raison » pourquoi, avec ce que tu as écarté. "
    "Appelle-le pour tout ce que tu écrirais dans « Décisions & arbitrages » de "
    "ton compte-rendu final — pas pour chaque geste de ton travail. "
    "L'appel n'attend aucune réponse et ne te suspend pas : il écrit ta décision "
    "au journal du run, rattachée à ta tâche et à ton nom, pour qu'elle se lise "
    "pendant que tu travailles et se relise après. Poursuis aussitôt. "
    "Ce n'est ni une demande d'accord — cela, c'est « demander_arbitrage » — ni "
    "une difficulté que tu subis — cela, c'est « signaler_blocage »."
)

#: Le schéma d'entrée de l'outil — ce qui a été tranché, et pourquoi. Rien
#: d'autre : l'agent, la tâche et le run **ne sont pas demandés à l'agent**, ils
#: sont fermés par l'exécuteur (`maestro.engine.executor._consigneur`), seul à
#: les connaître et seul à en répondre. Un agent qui les fournirait pourrait
#: signer d'un autre nom ou rattacher sa décision à la tâche d'un tiers — c'est
#: la règle que `_courrier` (#720) tient déjà.
SCHEMA_ENTREE: dict[str, type] = {"decision": str, "raison": str}

#: Ce que lit l'agent qui a appelé l'outil sans dire ce qu'il a décidé. Rien
#: n'est consigné, et le texte le dit plutôt que de laisser croire au contraire.
#: Même parti pris que `blocage.RAISON_MANQUANTE` : ce n'est **pas un refus** —
#: rien n'a été soumis à personne, il n'y a que le champ à remplir.
DECISION_MANQUANTE = (
    "Aucune décision fournie — rien n'a été consigné. Rappelle cet outil en "
    "disant ce que tu as tranché, en une phrase, et pourquoi."
)

#: Ce que lit l'agent qui dit ce qu'il a décidé sans dire pourquoi. Rien n'est
#: consigné non plus, et c'est le point où ce verbe est plus exigeant que ses
#: voisins : une décision sans son motif n'est **pas relisible**. « J'ai retenu
#: SQLite » ne se conteste pas ; « j'ai retenu SQLite parce que la tâche ne
#: nomme aucun serveur et que le livrable doit tourner sans service » se
#: conteste, et c'est tout ce qui rend l'autonomie vérifiable après coup (#1019).
RAISON_MANQUANTE = (
    "Décision reçue sans sa raison — rien n'a été consigné. Une décision ne se "
    "relit que par son motif : rappelle cet outil en disant pourquoi tu as "
    "tranché ainsi, et ce que tu as écarté."
)

#: Ce que lit l'agent dont la décision **est** consignée. Il dit les deux choses
#: dans l'ordre où elles comptent : c'est écrit et durable, et **personne ne
#: répondra**. Un accusé de réception qui s'arrêterait à « c'est noté »
#: laisserait ouverte la question « dois-je attendre ? », qui est exactement
#: celle que ce verbe n'a pas le droit de poser.
DECISION_CONSIGNEE = (
    "Décision consignée au journal du run, rattachée à ta tâche — elle se lit "
    "pendant que tu travailles et se relit après coup. Personne ne va te "
    "répondre ici : poursuis ta tâche, et redis-la dans « Décisions & "
    "arbitrages » de ton compte-rendu final."
)

#: Ce que rend la couche fournisseur quand le canal lui-même casse (callback en
#: erreur). On le **dit** à l'agent plutôt que d'avaler l'échec : il croirait sa
#: décision écrite et ne la répéterait pas dans son compte-rendu final, seul
#: endroit qui lui reste.
#:
#: Ce n'est pas un refus et il n'y a rien à réessayer : l'exception ne remonte
#: jamais (elle tuerait la tâche au moment où l'agent rend compte de lui-même),
#: et le verbe n'a de toute façon rien à autoriser ni à interdire.
CANAL_EN_ERREUR = (
    "Décision NON consignée — le canal est en erreur ({cause}). Poursuis ta "
    "tâche et écris cette décision dans « Décisions & arbitrages » de ton "
    "compte-rendu final, qui est le seul endroit où elle sera lue."
)

#: Le contrat de la couche fournisseur : ce qui a été décidé, pourquoi, et rien
#: en retour.
#:
#: Reçoit les deux textes **tels que l'agent les a écrits** (non vides, déjà
#: nettoyés). Synchrone et sans retour, à dessein : c'est la forme la plus courte
#: de « il n'attend aucune réponse et ne suspend jamais l'agent ». Un `Awaitable`
#: laisserait la porte ouverte à un appelant qui attendrait quelque chose, et le
#: jour où quelqu'un l'attendrait, ce verbe serait devenu un canal d'arbitrage de
#: plus — exactement ce que la première moitié du régime (demander ce qui demande
#: un humain) a déjà, et que docs/31 §3.1 refuse de dédoubler.
#:
#: Il ne rend rien, mais il peut **lever** : ce que le fournisseur sert alors est
#: `CANAL_EN_ERREUR`. C'est le pendant exact de `Signaleur` (#719), et pour la
#: même raison — l'agent n'attend pas de réponse, mais il attend un accusé.
Consigneur = Callable[[str, str], None]
