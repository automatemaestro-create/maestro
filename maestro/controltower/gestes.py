"""Les **gestes** qu'un run reçoit une fois lancé — pause, reprise, annulation, relance (#1179).

Quatre verbes existaient déjà, chacun avec sa route et son bouton : `…/pause` et
`…/reprendre` (#477), `…/annuler` (#185), `…/relancer` (#349). Le fil de
l'orchestrateur, lui, ne savait que **proposer un run** : « mets-le en pause »,
« annule », « relance-le avec 5 $ de plus » repartaient en proposition de run neuf,
ou en renvoi vers un autre écran. Il agit désormais sur les runs, et par les
**mêmes** services que les boutons (`ServiceExecutions.agir`).

Ce module ne porte que le **vocabulaire** que trois couches partagent — le service
qui exécute, le fil qui propose puis exécute sur accord, la route qui sert le
geste — et le refus commun. Il ne tire rien : l'orchestration reste jouable sans
moteur (`maestro.controltower.orchestration`), et c'est pourquoi ces noms ne vivent
pas dans `executions`, qui tire la couche d'exécution entière.

## Le refus est un fait, avec son motif

`GesteRefuse` est ce que le service rend quand l'état du run interdit le geste —
un run soldé qu'on voudrait suspendre, un run qui travaille qu'on voudrait
reprendre, un run vivant qu'on voudrait relancer. `motif` est un code stable, le
message la phrase lisible : la règle de `RelanceRefusee` (#349), qui en hérite
désormais. Les **règles** qui le lèvent vivent une seule fois, dans
`ServiceExecutions.refus_du_geste` : les routes et le fil les appellent, aucun ne
les recopie. Deux formulations de « ce run peut-il être suspendu ? » finiraient par
répondre différemment au bouton et à la phrase.
"""

from __future__ import annotations

#: Suspendre un run : ce qui est parti va à son terme, ce qui ne l'est pas attend (#477).
GESTE_PAUSE = "pause"

#: Reprendre un run suspendu, **le même**, là où il en était (#477).
GESTE_REPRISE = "reprise"

#: Interrompre un run : les tâches en vol s'arrêtent là où elles en sont (#185).
GESTE_ANNULATION = "annulation"

#: Rejouer un run arrêté sur son brief approuvé — un **nouveau** run, avec ou sans
#: nouvelles bornes (#349, #1179).
GESTE_RELANCE = "relance"

#: Les quatre, dans l'ordre où le prompt et l'écran les nomment. Une liste
#: **blanche** : un geste hors de ces quatre n'atteint jamais le service.
GESTES_RUN = (GESTE_PAUSE, GESTE_REPRISE, GESTE_ANNULATION, GESTE_RELANCE)


class GesteRefuse(ValueError):
    """Un geste sur un run refusé par son état, **avec son motif** — jamais un rejet muet.

    Hérite de `ValueError` pour la raison de `RelanceRefusee` (#349) : c'est une
    requête que l'état du run rend invalide, pas une panne. `motif` est un code
    court et stable (`run-solde`, `run-suspendu`…), que les routes traduisent en
    statut HTTP ; le message est la phrase que l'écran et le fil montrent telle
    quelle.
    """

    def __init__(self, motif: str, message: str) -> None:
        super().__init__(message)
        self.motif = motif
