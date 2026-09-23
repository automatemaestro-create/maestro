"""Compléter l'équipe **avant** d'exécuter le plan — le contrat (#1227).

La décomposition planifie désormais *pour le besoin* : une tâche nomme la
compétence qu'elle demande même quand l'équipe ne l'a pas
(`maestro.orchestrator.playbook`). Ce module porte ce qui manquait ensuite — **le
canal de la décision** — pour que `OrchestrationEngine.run` puisse s'arrêter entre
le plan et la première tâche, montrer le rôle qui manque, et repartir avec l'équipe
que la personne a acceptée.

Le défaut mesuré (projet `p1`, 2026-09-22) : « une petite animation du logo
Maestro » sur un projet qui n'avait qu'un `dev`. Le plan sortait en quatre tâches —
logo stylisé, script d'animation, test de lancement, documentation — et les quatre
allaient au `dev`, parce que le playbook du Chef de projet lui interdisait de
nommer une compétence que l'équipe n'avait pas. *« Aucune modification de l'équipe
n'a été suggérée. »*

**Ce que ce canal renverse**, et ce qu'il ne touche pas. Les deux dernières puces
de [docs/37 §3](../../docs/37-decision-equipe-sur-mesure.md) — « recruter reste un
geste validé hors du run », « l'équipe ne se forme pas dans un run » — tombent :
l'équipe se complète **dans** le run, au seul moment où c'est utile. Ne bougent
pas, et ce sont elles qui comptaient : rien n'est recruté sans accord (#1040), un
**agent** ne recrute jamais ([docs/31
§3.5](../../docs/31-decision-surface-ecriture-agents.md) — ici c'est
l'orchestrateur qui propose et une personne qui décide), et le brief est validé
avant la décomposition (D5).

## Le quatrième arbitre, et ce qui le distingue des trois autres

Même patron que `ArbitreBrief` (#320), `ArbitreClarification` (#321) et
`ArbitreQuestion` (#1023) : le moteur ne connaît qu'un protocole,
l'implémentation décide *où* la question est posée
(`maestro.controltower.renfort` la pose dans le fil de l'orchestration). Trois
différences, et chacune décide d'une ligne de code :

- **la borne voyage sur la demande** (`attente_s`), et c'est l'arbitre qui la
  tient. Elle ne pouvait pas vivre dans la boucle, comme celle d'une question vit
  dans l'exécuteur : sans réponse, il faut **le dire dans le fil** — c'est le
  second critère du ticket —, et l'arbitre est le seul à y avoir accès. Une
  boucle qui bornerait elle-même annulerait l'arbitre au milieu de son attente,
  donc avant qu'il ait pu écrire quoi que ce soit ;
- **l'issue par défaut est « on continue »**, jamais « on s'arrête ». C'est
  l'inverse du brief (`BriefRefuse`) et c'est le sujet : un plan reste exécutable
  par l'équipe actuelle — moins bien, et c'est un fait qui se dit. Un run
  suspendu pour toujours faute de répondant serait un remède pire que le mal ;
- **rien n'est réécrit dans le plan.** L'équipe complétée est lue par le routage
  tâche par tâche (`LocalExecutor._equipe` relit le dépôt d'agents à chaque
  tâche) : un rôle créé pendant l'attente prend ses tâches sans qu'une ligne du
  plan change. C'est ce qui rend ce canal si court.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from maestro.equipe.manque import ManqueAuPlan


@dataclass(frozen=True)
class DemandeRenfort:
    """Ce qu'une personne reçoit pour décider de compléter l'équipe (#1227).

    `manque` porte le poste et les tâches qui l'attendent (`ManqueAuPlan`) :
    c'est la pièce, et elle est rendue telle quelle plutôt que recopiée en
    champs — la raison montrée dans le fil doit être *la* raison calculée, pas
    une seconde formulation à tenir d'accord avec elle.

    `objectif` est ce que la personne a demandé, conservé à côté du manque parce
    que c'est ce qui donne son sens au rôle proposé : « un designer » ne se juge
    pas sans « pour une animation de logo ». `run_id` relie la demande au run
    qu'elle suspend — donc au canal sur lequel la décision reviendra —, et
    `projet_id` est le projet où l'équipe naîtrait.

    `attente_s` est ce qu'on laisse à qui répond. Elle voyage ici, et c'est
    l'arbitre qui la tient (cf. l'en-tête du module) ; c'est la **même** borne
    qu'un arbitrage ou qu'une question d'agent (`BornesArbitrage.attente_s`), et
    lui donner un second réglage ferait deux chiffres à tenir d'accord pour une
    seule question — *combien laisse-t-on à qui répond ?*
    """

    run_id: str
    projet_id: str
    objectif: str
    manque: ManqueAuPlan
    attente_s: float


@dataclass(frozen=True)
class DecisionRenfort:
    """L'issue d'une demande de renfort — et laquelle des trois s'est produite.

    `approuve` dit si l'équipe a été complétée. `detail` est ce qui sera consigné
    au journal du run : qui a été recruté, ou pourquoi personne ne l'a été.

    `sans_reponse` distingue les deux façons de ne pas recruter, et elles ne se
    lisent pas pareil : un **refus** est une décision (quelqu'un a jugé que
    l'équipe actuelle suffisait), une **absence de réponse** n'en est pas une. Les
    confondre dans un seul booléen ferait écrire « refusé » au journal d'un run
    que personne n'a regardé passer.
    """

    approuve: bool
    detail: str = ""
    sans_reponse: bool = False


class ArbitreRenfort(Protocol):
    """Propose le renfort à une personne et rend sa décision — le contrat de l'attente.

    L'attente est **bornée par `demande.attente_s`**, et c'est l'implémentation
    qui la tient : à l'échéance elle rend `DecisionRenfort(approuve=False,
    sans_reponse=True)` après l'avoir dit là où la demande a été posée. Un
    arbitre qui attendrait indéfiniment suspendrait le run pour toujours — la
    seule chose que ce canal ne doit jamais faire.
    """

    async def __call__(self, demande: DemandeRenfort) -> DecisionRenfort:
        """Rend la décision sur `demande` (attente bornée par `demande.attente_s`)."""
        ...  # pragma: no cover - protocole


#: Forme appelable acceptée partout où un `ArbitreRenfort` est attendu : une
#: simple coroutine convient (les tests en passent une, sans classe
#: intermédiaire) — même commodité qu'`ArbitreBriefAppelable`.
ArbitreRenfortAppelable = Callable[[DemandeRenfort], Awaitable[DecisionRenfort]]


#: Les trois issues d'une proposition de renfort, telles que le journal du run les
#: porte (étape `equipe`, cf. `maestro.telemetry.costs.ETAPE_EQUIPE`). Trois mots
#: et pas un seul « refusé » : ce qu'on relit d'un run, c'est *qui a décidé* —
#: quelqu'un a jugé l'équipe suffisante, ou personne n'a regardé passer la
#: demande, et les deux n'appellent pas la même suite.
STATUT_RENFORT_RECRUTE = "renfort_recrute"
STATUT_RENFORT_DECLINE = "renfort_decline"
STATUT_RENFORT_SANS_REPONSE = "renfort_sans_reponse"
