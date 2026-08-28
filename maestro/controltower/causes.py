"""Pourquoi un run s'est arrêté — la cause, nommée (#479).

Le moteur *connaît* les causes d'arrêt : `TurnLimitReached` (#91),
`PlafondDepenseDepasse` (#9/#56), `DemarrageHoteRate` (#443), l'annulation. Mais
tout ce qui atteignait l'écran était le texte que trois appelants recopiaient
chacun de leur côté :

    self._consigne(run_id, EXECUTION_ECHEC, "", f"{type(exc).__name__} : {exc}")

Un nom de classe Python suivi d'un message — donc « pourquoi » était là, en
anglais, dans une chaîne, et il fallait le lire pour le savoir. La **liste** des
runs, elle, ne montrait que « Échec » : rien n'y distinguait un plafond atteint
d'un hôte qui n'a jamais démarré, alors que l'un se relève d'un réglage et
l'autre d'un redémarrage.

Ce module ne crée aucune cause, il les **classe**. Le code qu'il rend voyage sur
`Event.cause`, se pose dans la projection et se lit à l'écran ; le `detail`
d'origine reste intact à côté, parce qu'un code dit *de quoi* il s'agit et
jamais *ce qui s'est passé* (quelle borne, quel montant, quel process).

Deux choix à ne pas défaire.

**La classification est un `isinstance`, pas une lecture de texte** — sauf pour
la limite d'usage, qui n'a aucun type. C'est un manque réel et non un oubli :
rien dans `maestro/` ne détecte aujourd'hui un 429, une limite d'abonnement ou
un solde épuisé ; `est_transitoire` les range même parmi les aléas relançables,
faute de savoir les reconnaître. Les marqueurs ci-dessous sont donc **repris de
l'outillage**, qui affronte la même question depuis #171
(`scripts/orchestrate/run.sh`, `limite_atteinte`) — deux listes à tenir
d'accord seraient le premier moyen pour que le produit et le pilote ne
reconnaissent pas la même panne.

**Le texte examiné est celui de l'exception, jamais le stderr collecté** (#346).
La tentation est grande : le stderr en dit souvent plus. Mais il porte aussi la
télémétrie du CLI, qui place en tête de chaque flux un
`{"type":"rate_limit_event","rate_limit_info":{"status":"allowed",…}}` — une
ligne qui *rapporte* la fenêtre en cours et n'annonce aucune limite. C'est
exactement le piège de #203, où un run est parti dormir jusqu'au reset après
avoir livré son ticket. Chercher « rate limit » dans ce flux ferait donc nommer
« limite d'usage » à peu près tous les échecs.
"""

from __future__ import annotations

import re

from maestro.controltower.hote import DemarrageHoteRate
from maestro.providers.base import TurnLimitReached
from maestro.telemetry import PlafondDepenseDepasse

#: Le plafond de tours de l'exécution agentique a été atteint (#91). Depuis #494
#: aucun agent du dépôt n'en pose plus, donc cette cause désigne un plafond
#: **explicitement** réglé — ou la borne d'un fournisseur tiers.
CAUSE_PLAFOND_TOURS = "plafond_tours"

#: Le plafond de dépense du run a été crevé (#9/#56) — en dollars ou en tokens,
#: le contrôle étant le même (`PlafondDepense`). Les deux partagent une cause :
#: ce qu'on veut savoir est qu'une borne de dépense a stoppé le run, et le
#: `detail` dit laquelle et de combien.
CAUSE_PLAFOND_COUT = "plafond_cout"

#: Le fournisseur a refusé de servir : limite d'usage de l'abonnement, quota,
#: 429, solde épuisé. La seule cause reconnue au texte (voir l'en-tête).
CAUSE_LIMITE_USAGE = "limite_usage"

#: L'hôte du run n'a pas démarré (#443, `DemarrageHoteRate`). À distinguer d'un
#: run qui échoue : ici rien n'a jamais tourné, donc il n'y a ni tâche, ni coût,
#: ni journal à lire — seulement un process qui n'est pas parti.
CAUSE_HOTE = "hote_non_demarre"

#: Le run a été interrompu : annulation humaine, brief refusé, tâche annulée.
CAUSE_ANNULATION = "annulation"

#: **Maestro s'est éteint** (#486) — `start.sh --stop`, et la fermeture de
#: l'enveloppe le jour où elle existe. Une seconde forme d'interruption, distincte
#: de `CAUSE_ANNULATION` alors que le statut consigné est le même (`annulee`), et
#: la distinction porte du sens plutôt que du vocabulaire : personne n'a dit stop
#: **à ce run-là**, on a éteint l'application qui le tenait. D'où la seule
#: différence de traitement du dépôt — un run soldé de la sorte reste
#: **reprenable** (`ServiceExecutions.relancer`), là où un run qu'on a délibérément
#: annulé n'a rien à reprendre : les confondre ferait soit reproposer un run que
#: quelqu'un venait d'arrêter, soit perdre au redémarrage le cadrage de tous les
#: autres.
#:
#: Elle n'est **pas** rendue par `cause_de` et n'a aucun type d'exception : rien
#: n'a levé, l'extinction est un geste posé de l'extérieur. Elle se pose donc par
#: son appelant, exactement comme `CAUSE_ANNULATION` sur un brief refusé
#: (`hote_detache.main`).
CAUSE_EXTINCTION = "extinction"

#: Les causes que ce module sait nommer, dans l'ordre où elles se lisent.
CAUSES = (
    CAUSE_PLAFOND_TOURS,
    CAUSE_PLAFOND_COUT,
    CAUSE_LIMITE_USAGE,
    CAUSE_HOTE,
    CAUSE_ANNULATION,
    CAUSE_EXTINCTION,
)

#: Ce qui trahit une limite d'usage du fournisseur dans le message d'un échec.
#: **Repris tel quel** de `scripts/orchestrate/run.sh` (`limite_atteinte`, #171),
#: qui pose la même question sur la même matière depuis plus longtemps que nous.
#: Le motif est insensible à la casse et cherche une **sous-chaîne** : un message
#: de CLI n'a pas de forme stable, et exiger un format le rendrait faux au
#: premier changement de libellé.
_MOTIF_LIMITE_USAGE = re.compile(
    r"usage limit reached|rate.?limit|too many requests"
    r"|\"?api_error_status\"?\s*:?\s*\"?429|credit balance",
    re.IGNORECASE,
)


def cause_de(erreur: BaseException) -> str:
    """La cause nommée de `erreur` — chaîne vide si aucune ne s'applique.

    L'ordre des tests est celui de la **précision** : les types d'abord, le texte
    en dernier. Un `TurnLimitReached` dont le message citerait « rate limit »
    reste un plafond de tours — c'est ce que le moteur *sait*, contre ce qu'un
    message *suggère*.

    Rendre `""` plutôt qu'une cause fourre-tout est un choix. Un échec que ce
    module ne sait pas classer n'est pas « inconnu » au sens où il faudrait
    l'annoncer : son `detail` porte déjà le type et le message de l'exception, et
    l'écran le montre. Inventer une sixième cause pour tout le reste ferait
    passer « je n'ai pas su ranger ceci » pour un diagnostic.
    """
    if isinstance(erreur, TurnLimitReached):
        return CAUSE_PLAFOND_TOURS
    if isinstance(erreur, PlafondDepenseDepasse):
        return CAUSE_PLAFOND_COUT
    if isinstance(erreur, DemarrageHoteRate):
        return CAUSE_HOTE
    # `CancelledError` hérite de `BaseException` et non d'`Exception` : la
    # nommer ici suppose qu'un appelant la rattrape, ce que `_derouler` fait
    # explicitement avant de la relancer. Elle est traitée pour les appelants
    # qui, eux, la soldent (l'hôte détaché).
    if isinstance(erreur, BaseException) and type(erreur).__name__ == "CancelledError":
        return CAUSE_ANNULATION
    if _MOTIF_LIMITE_USAGE.search(str(erreur)):
        return CAUSE_LIMITE_USAGE
    return ""


def cause_lisible(erreur: BaseException) -> str:
    """Le message de `erreur`, sans les guillemets qu'un `KeyError` ajoute.

    `UnknownProviderError` est un `KeyError`, dont `__str__` rend le `repr` de son
    argument — et c'est justement l'échec de configuration le plus probable d'un
    poste local (`MAESTRO_PROVIDER` mal orthographié). Sans ce déballage, la cause la
    plus fréquente serait aussi la moins lisible du fil, entre guillemets et avec ses
    échappements.

    Elle vit **ici** depuis #764, avec les causes qu'elle sert à rendre. Elle était
    née au fil global (#686) ; l'assistance documentée pose la même question sur la
    même matière, et deux déballages écrits côte à côte finiraient par ne plus rendre
    le même message pour le même échec. À la différence de `cause_de`, elle ne
    **classe** rien : elle met en forme ce qu'un humain va lire.
    """
    if isinstance(erreur, KeyError) and erreur.args:
        return str(erreur.args[0])
    return str(erreur)


def detail_avec_cause(erreur: BaseException) -> tuple[str, str]:
    """Le couple `(detail, cause)` d'un échec — ce que les appelants consignent.

    `detail` garde **exactement** la forme que les trois soldeurs recopiaient
    (`« TypeErreur : message »`) : c'est ce que le fil d'activité, le journal
    persisté et les traces montrent déjà, et le changer réécrirait l'historique
    de tous les runs passés à l'écran sans rien apprendre de plus.

    La cause vient donc **en plus** et jamais à la place — un code pour ce que
    l'écran doit ranger et teinter, une phrase pour ce qu'un humain doit lire.
    """
    return f"{type(erreur).__name__} : {erreur}", cause_de(erreur)
