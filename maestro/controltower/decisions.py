"""Les décisions qu'un agent a tranchées **seul**, pour la vue d'un run (#1026).

Le parent #1019 pose une condition à l'autonomie : elle n'est acceptable que si
elle se **vérifie après coup**. Le lot 2 (#1024) a fait consigner chaque décision
qu'un agent tranche sans demander ; ce module en fait une **lecture** — la liste
qu'on ouvre pour savoir *ce que l'agent a décidé sans moi, et pourquoi*.

Servi par `GET /api/executions/{run_id}/decisions`, cinquième lecture d'un run à
côté du Kanban (« combien dans quel état »), de la progression (« où en est-on »),
du graphe (« quoi après quoi ») et de la frise (« dans quel ordre »).

## Rien n'est créé : tout est déjà au journal

Comme le graphe (#490) et la frise (#355), cette liste **n'a pas d'événement à
elle** : elle se recompose à la lecture depuis les entrées que
`GET /api/journal?run_id=…` sert déjà, et chaque ligne garde l'identifiant de
l'entrée dont elle vient. La mise à jour en direct passe donc par le flux
existant, sans second canal.

## Deux familles, et il en faut deux

Le ticket demande que **les hypothèses prises faute de réponse y figurent aussi,
marquées comme telles**. Elles ne viennent pas du même endroit que les décisions,
et c'est la seule raison pour laquelle ce module lit **deux** types plutôt qu'un :

- `tache.decision` (#1024) — l'agent a jugé que la question ne demandait personne,
  a tranché, et l'a écrit. `detail` porte la décision, `description` son motif :
  deux champs et non un texte, précisément pour que cette vue n'ait **rien à
  redécouper** ;
- une étape `<tache>:question` soldée **sans réponse** (#1023), que le pont range
  en `agent.activite` et que son `statut` distingue
  (`STATUT_QUESTION_SANS_REPONSE`). L'agent a demandé, personne n'a répondu avant
  la borne, et il est reparti sur l'hypothèse qu'il avait annoncée. C'est une
  décision de fait — prise faute de mieux, pas faute d'avoir demandé —, et la
  taire rendrait la liste fausse dans le seul cas où elle compte.

`origine` les sépare, et ne les mélange jamais : l'une dit « je n'avais pas à
demander », l'autre « j'ai demandé et personne n'a répondu ». Un lecteur qui les
lirait sous un même mot ne saurait plus lequel des deux il lit — c'est le partage
que `events.EVENEMENT_TACHE_DECISION` fait déjà entre la décision d'un agent et
celle d'une personne.

## Ce que ce module ne fait pas

Il ne **juge** rien : une décision autonome n'est ni approuvée ni refusée ici, et
répondre après coup à une question n'est pas de son ressort (c'est
`POST /api/questions/{question_id}/reponse`). Il ne **résume** rien non plus — la
décision et son motif sortent tels que l'agent les a écrits, expurgés des secrets
en amont, sur le bus (#1024, #1023).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from maestro.controltower.events import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_TACHE_DECISION,
)
from maestro.controltower.journal import EntreeJournal
from maestro.engine.executor import STATUT_QUESTION_SANS_REPONSE

#: Une décision que l'agent a **jugée sienne** : il n'avait à demander à personne
#: (docs/37 §2.2, socle des playbooks), il a tranché, il l'a consignée.
ORIGINE_TRANCHEE = "tranchee"
#: Une décision **prise faute de réponse** : l'agent a posé sa question, personne
#: n'a répondu avant la borne, il est reparti sur l'hypothèse qu'il avait
#: annoncée (#1023). Marquée comme telle, parce que ce n'est pas le même fait.
ORIGINE_HYPOTHESE = "hypothese"

#: Combien de décisions la liste rend au plus. Même raison qu'au plafond de la
#: frise, et même promesse : il retient les **plus récentes** et ne se tait
#: jamais — `total` et `tronquee` disent ce qui a été laissé de côté, et le
#: journal requêtable rend le reste. Plus bas que celui de la frise (500) parce
#: qu'une décision n'est pas du bruit de fond : un run qui en produirait deux
#: cents n'aurait plus rien d'autonome à faire vérifier, il aurait un problème de
#: cadrage.
PLAFOND_DECISIONS = 200


def _origine_de(entree: EntreeJournal) -> str | None:
    """L'origine de la décision que porte `entree` — None si ce n'en est pas une.

    Le tri se fait sur le **type** d'abord, sur le `statut` ensuite et seulement
    pour la seconde famille : une étape de question soldée par une **réponse**
    n'est pas une décision d'agent — quelqu'un a répondu, c'est le contraire de
    l'autonomie —, et elle reste où elle est, au journal du run.
    """
    if entree.type == EVENEMENT_TACHE_DECISION:
        return ORIGINE_TRANCHEE
    if entree.type == EVENEMENT_AGENT_ACTIVITE and entree.statut == STATUT_QUESTION_SANS_REPONSE:
        return ORIGINE_HYPOTHESE
    return None


@dataclass(frozen=True)
class Decision:
    """Une décision de la liste : ce qui a été tranché, par qui, pourquoi et quand.

    `id`, `agent`, `role`, `tache_id` et `horodatage` sont ceux de l'entrée de
    journal dont elle vient — rien n'est réécrit, et c'est ce qui permet de la
    retrouver dans `GET /api/journal`.

    `decision` et `raison` sortent **tels quels** des deux champs que le moteur
    sépare à l'écriture (`detail` et `description`) : c'est l'engagement pris par
    #1024, et le respecter ici est tout ce qu'il demandait.

    `tache` est le **titre** de la tâche, résolu par l'appelant depuis la
    projection, et non le `nom` de l'étape de journal — celui-là porte un préfixe
    (« Décision de l'agent — … ») qu'il faudrait découper pour en tirer le titre.
    Vide quand la tâche n'est pas (ou plus) connue : la vue rend alors son
    identifiant, qui reste un renvoi valable.
    """

    rang: int
    id: str
    origine: str
    tache_id: str = ""
    tache: str = ""
    agent: str = ""
    role: str = ""
    decision: str = ""
    raison: str = ""
    horodatage: str = ""

    @property
    def hypothese(self) -> bool:
        """Cette décision a-t-elle été prise faute de réponse ?"""
        return self.origine == ORIGINE_HYPOTHESE

    @classmethod
    def depuis(
        cls, entree: EntreeJournal, origine: str, *, tache: str = ""
    ) -> Decision:
        """La décision que porte cette entrée de journal, pour cette `origine`."""
        return cls(
            rang=entree.rang,
            id=entree.id,
            origine=origine,
            tache_id=entree.tache_id,
            tache=tache,
            agent=entree.agent,
            role=entree.role,
            decision=entree.detail,
            raison=entree.description,
            horodatage=entree.horodatage,
        )

    @property
    def cle_tri(self) -> tuple[str, int]:
        """La clé de tri : l'instant, départagé par le rang d'arrivée au journal."""
        return (self.horodatage, self.rang)

    def to_dict(self) -> dict[str, Any]:
        """La forme JSON d'une décision (`Decision`, apps/web/lib/types.ts)."""
        return {
            "id": self.id,
            "origine": self.origine,
            "hypothese": self.hypothese,
            "tache_id": self.tache_id,
            "tache": self.tache,
            "agent": self.agent,
            "role": self.role,
            "decision": self.decision,
            "raison": self.raison,
            "horodatage": self.horodatage,
        }


@dataclass(frozen=True)
class DecisionsRun:
    """Les décisions autonomes d'un run, de la plus récente à la plus ancienne.

    **Du plus récent au plus ancien**, comme le journal du run et non comme la
    frise : les deux lectures chronologiques de la bascule doivent aller dans le
    même sens, faute de quoi passer de l'une à l'autre demanderait de relire le
    sens de lecture avant la première ligne.

    `hypotheses` compte la seconde famille. Il est servi plutôt que recompté par
    le client pour la raison qui vaut déjà au `total` : une liste tronquée le
    recompterait sur ce qu'elle a reçu, donc faux.

    `total` compte **avant** le plafond ; `tronquee` dit s'il a mordu — une borne
    muette ferait passer un run bavard pour un run sobre.
    """

    run_id: str
    entrees: tuple[Decision, ...] = ()
    total: int = 0
    hypotheses: int = 0
    plafond: int = PLAFOND_DECISIONS

    @property
    def tronquee(self) -> bool:
        """La liste a-t-elle laissé des décisions plus anciennes de côté ?"""
        return self.total > len(self.entrees)

    def to_dict(self) -> dict[str, Any]:
        """La forme JSON (`GET /api/executions/{run_id}/decisions`)."""
        return {
            "run_id": self.run_id,
            "entrees": [entree.to_dict() for entree in self.entrees],
            "total": self.total,
            "hypotheses": self.hypotheses,
            "plafond": self.plafond,
            "tronquee": self.tronquee,
        }


def decisions_du_run(
    run_id: str,
    entrees: Iterable[EntreeJournal],
    *,
    taches: Mapping[str, str] | None = None,
    plafond: int = PLAFOND_DECISIONS,
) -> DecisionsRun:
    """Assemble les décisions autonomes du run `run_id` depuis des entrées de journal.

    `entrees` est ce que le journal garde du run — l'appelant a déjà filtré sur le
    `run_id` ; ce qui n'est pas une décision est écarté ici, une fois, plutôt que
    par chaque appelant.

    `taches` donne le titre de chaque tâche (identifiant → titre), tel que la
    projection le tient : c'est ce qui permet à la ligne de nommer la tâche vers
    laquelle elle renvoie. Une tâche absente de cette table laisse le titre vide
    et n'écarte rien — perdre une décision faute de connaître le titre de sa tâche
    serait exactement l'inverse de ce que ce module existe pour faire.

    `plafond` borne le nombre de lignes rendues, les plus **récentes** d'abord.
    `total` et `hypotheses` comptent, eux, **avant** la borne.
    """
    titres = taches or {}
    retenues: list[Decision] = []
    hypotheses = 0
    for entree in entrees:
        origine = _origine_de(entree)
        if origine is None:
            continue
        if origine == ORIGINE_HYPOTHESE:
            hypotheses += 1
        retenues.append(
            Decision.depuis(entree, origine, tache=titres.get(entree.tache_id, ""))
        )
    total = len(retenues)
    # Tri décroissant sur l'instant, départagé par le rang : deux décisions
    # consignées dans la même seconde — une salve d'agents parallèles — gardent
    # l'ordre du journal plutôt qu'un ordre arbitraire.
    retenues.sort(key=lambda decision: decision.cle_tri, reverse=True)
    return DecisionsRun(
        run_id=run_id,
        entrees=tuple(retenues[:plafond]),
        total=total,
        hypotheses=hypotheses,
        plafond=plafond,
    )
