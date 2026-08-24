"""Le journal requêtable de la Control Tower — l'historique servi par `GET /api/journal` (#478).

Le contrat de cette route est **figé depuis #183** (docs/05 §6.2) — filtres par
agent, type, run et période, tri, pagination, portée `?projet=` — mais il n'avait
jamais été servi : la route répondait `501` hors mode fixtures. La conséquence se
lisait à l'écran plutôt que dans le code : le fil d'activité ne contenant que ce
qui est passé par le WebSocket depuis l'ouverture de la page, **un rechargement
pendant un run d'une heure effaçait tout ce qu'on avait sous les yeux**.

Ce module sert la route pour de bon. Il ne stocke rien de nouveau : la donnée est
déjà persistée par le **journal durable** (`EventLog`, #97, `persistence.py`), que
le lifespan de l'API rejoue au démarrage. Ce qui manquait était une **lecture
requêtable** — un index transverse, ordonné, avec un identifiant stable par
entrée.

Pourquoi un index à part, et pas la projection (`ControlTowerState`) :

- elle n'indexe les événements **que par run** (`EtatExecution.evenements`) et
  **jette purement et simplement** ceux qui n'en portent pas (`if event.run_id:`),
  or `agent.capacite`, `chat.message` et `playbook.proposition` sont dans ce cas —
  un journal qui les perdrait ne serait pas le journal ;
- une lecture transverse y coûterait un parcours de tous les runs à chaque appel.

Pourquoi un index en mémoire, et pas une relecture du journal durable à chaque
requête : `EventLog.relire()` rend **tout** l'historique — un `LRANGE 0 -1` suivi
d'un `json.loads` par événement. Ouvrir la page Journal pendant un run parallèle,
qui publie beaucoup, paierait ce prix à chaque affichage. L'index est donc
alimenté une fois — par le rejeu au démarrage, puis par la pompe au fil de l'eau —
et ne relit plus jamais.

Volumétrie, puisque la question est au ticket : une entrée ne garde **que ce que
la ligne du journal dit** (douze champs de texte), et laisse derrière elle les
charges lourdes d'un événement — `usage`, `brief`, `sources`, `diff`. Le reste est
tenu par le contrat lui-même : pagination obligatoire (défaut 50, plafond dur
200 — au-delà, `422`) et bornes de période. La rétention n'est **pas** bornée ici,
au même titre que le journal durable et la projection (`persistence.py` :
« la liste croît avec l'historique ») — borner le seul index ferait mentir la page
qu'on vient de réparer, en lui faisant perdre en silence ce que le disque a gardé.
La politique de rétention viendra avec la bascule PostgreSQL, pour les trois.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from maestro.controltower.events import Event
from maestro.controltower.portee import PorteeProjet

#: Clés de tri du journal requêtable (contrat #183, docs/05 §6.2) et sens. Elles
#: vivaient dans `fixtures.py` tant que la route n'était servie que par lui ;
#: elles ont suivi l'implémentation réelle, pour que le contrat et le code qui
#: l'honore se lisent au même endroit.
TRI_JOURNAL_HORODATAGE = "horodatage"
TRI_JOURNAL_AGENT = "agent"
TRI_JOURNAL_TYPE = "type"
TRIS_JOURNAL = (TRI_JOURNAL_HORODATAGE, TRI_JOURNAL_AGENT, TRI_JOURNAL_TYPE)
ORDRE_ASC = "asc"
ORDRE_DESC = "desc"
ORDRES_JOURNAL = (ORDRE_ASC, ORDRE_DESC)
#: Taille de page par défaut et plafond dur (au-delà, 422 : pas de scan illimité).
TAILLE_PAGE_DEFAUT = 50
TAILLE_PAGE_MAX = 200

#: Préfixe des identifiants d'entrée — la forme documentée (`j-0002`, docs/05 §6.2).
PREFIXE_ENTREE = "j-"
#: Largeur du remplissage à zéro. Purement cosmétique : au-delà de `j-9999` les
#: identifiants s'élargissent d'eux-mêmes et restent uniques. C'est pourquoi le
#: tri se départage sur le **rang numérique** et jamais sur cette chaîne, où
#: « j-10000 » précéderait « j-9999 ».
LARGEUR_ENTREE = 4


def identifiant_entree(rang: int) -> str:
    """L'identifiant d'une entrée depuis son rang dans le journal (1-indexé).

    Le rang, et non un uuid : le journal durable est **append-only** (`RPUSH`),
    donc son rejeu au démarrage rend les mêmes événements dans le même ordre, donc
    les mêmes identifiants — un `j-0002` reste `j-0002` d'un redémarrage à l'autre,
    ce qu'exige « id stable (référençable, triable) » du contrat.
    """
    return f"{PREFIXE_ENTREE}{rang:0{LARGEUR_ENTREE}d}"


@dataclass(frozen=True)
class EntreeJournal:
    """Une entrée du journal requêtable : un événement persisté doté d'un id stable.

    Les dix champs du contrat #183 (`id`, `type`, `run_id`, `tache_id`, `agent`,
    `role`, `statut`, `detail`, `projet_id`, `horodatage`), plus `titre` et
    `description`. Ces deux-là sont une **extension additive** de #478, et la
    raison est le critère du ticket : `titre` est ce que la ligne d'activité
    *prononce* (`resumeEvenement`, apps/web) — sans lui, « dev a terminé
    « Planification » » se relit « dev a terminé : une étape », et « un
    rechargement ne perd rien » serait faux d'une ligne sur deux. Un consommateur
    qui ignore ces deux clés lit exactement la forme d'avant.

    Ce qui reste **dehors** est aussi un choix : `usage`, `brief`, `sources` et
    `diff` sont les charges lourdes d'un événement, n'apparaissent sur aucune
    ligne du journal, et une page de 200 entrées doit rester une page. Elles
    restent lisibles là où elles ont un sens — le résumé d'un run
    (`GET /api/executions/{run_id}`) et les coûts.

    `rang` est l'ordre d'arrivée dans le journal ; il **ne sort pas** en JSON —
    c'est la clé de départage du tri, que l'identifiant ne peut pas assurer (voir
    `LARGEUR_ENTREE`).
    """

    rang: int
    type: str
    run_id: str = ""
    tache_id: str = ""
    titre: str = ""
    agent: str = ""
    role: str = ""
    statut: str = ""
    detail: str = ""
    description: str = ""
    projet_id: str | None = None
    horodatage: str = ""

    @property
    def id(self) -> str:
        """L'identifiant stable de l'entrée (`j-0002`)."""
        return identifiant_entree(self.rang)

    @classmethod
    def depuis(cls, event: Event, rang: int) -> EntreeJournal:
        """L'entrée que consigne `event`, à ce `rang` dans le journal."""
        return cls(
            rang=rang,
            type=event.type,
            run_id=event.run_id,
            tache_id=event.tache_id,
            titre=event.titre,
            agent=event.agent,
            role=event.role,
            statut=event.statut,
            detail=event.detail,
            description=event.description,
            projet_id=event.projet_id,
            horodatage=event.horodatage,
        )

    def to_dict(self) -> dict[str, Any]:
        """La forme JSON d'une entrée (`EntreeJournal`, apps/web/lib/types.ts)."""
        return {
            "id": self.id,
            "type": self.type,
            "run_id": self.run_id,
            "tache_id": self.tache_id,
            "titre": self.titre,
            "agent": self.agent,
            "role": self.role,
            "statut": self.statut,
            "detail": self.detail,
            "description": self.description,
            "projet_id": self.projet_id,
            "horodatage": self.horodatage,
        }

    def cle_tri(self, tri: str) -> tuple[str, int]:
        """La clé de tri sur `tri`, départagée par le rang d'arrivée.

        Le départage n'est pas un détail d'implémentation : `agent` et `type` ne
        distinguent pas deux entrées voisines, et sans lui deux pages
        consécutives pourraient se recouvrir ou sauter une entrée d'un appel à
        l'autre — exactement ce que la pagination doit empêcher.
        """
        valeur = getattr(self, tri, "")
        return (valeur if isinstance(valeur, str) else "", self.rang)


class ServiceJournal:
    """Le journal requêtable : l'historique des événements, filtrable et paginé (#478).

    Alimenté de deux côtés, et de deux seulement — le **rejeu** du journal durable
    à l'ouverture de l'API, puis la **pompe** au fil de l'eau (`app._pompe`) —,
    tous deux dans l'ordre d'arrivée, qui est celui des rangs. Le WebSocket ne
    sert plus qu'au direct : il pousse par-dessus un historique déjà là, au lieu
    d'en être la seule source.

    Aucune écriture concurrente à craindre : la pompe est **l'unique consommateur
    du bus** et le rejeu la précède dans le même lifespan.
    """

    def __init__(self) -> None:
        self._entrees: list[EntreeJournal] = []

    def __len__(self) -> int:
        """Le nombre d'entrées consignées, toutes portées confondues."""
        return len(self._entrees)

    def consigner(self, event: Event) -> None:
        """Ajoute `event` en fin de journal, au rang suivant."""
        self._entrees.append(EntreeJournal.depuis(event, len(self._entrees) + 1))

    def page(
        self,
        *,
        agent: str | None = None,
        type: str | None = None,
        run_id: str | None = None,
        portee: PorteeProjet | None = None,
        depuis: str | None = None,
        jusqua: str | None = None,
        tri: str = TRI_JOURNAL_HORODATAGE,
        ordre: str = ORDRE_DESC,
        page: int = 1,
        taille: int = TAILLE_PAGE_DEFAUT,
    ) -> dict[str, Any]:
        """Une page du journal (`GET /api/journal`) : filtres, tri, pagination.

        Filtre par `agent`, `type`, `run_id`, `portee` (#277) et fenêtre
        temporelle (`depuis`/`jusqua`, ISO-8601, bornes **incluses**, comparaison
        lexicale des horodatages — tous sont produits par `Event` au même format
        UTC), trie sur `tri`/`ordre`, puis découpe en pages de `taille`
        (1-indexé). `total` est le compte **après filtres, avant pagination** ;
        `pages` le nombre de pages, `0` quand rien ne sort. Une page au-delà de la
        dernière rend une liste vide avec des compteurs justes — pas un 404 : la
        question « et après ? » a une réponse, ce n'est pas une erreur de chemin.

        Les paramètres sont réputés **déjà validés** par la route (tri et ordre
        connus, page ≥ 1, taille bornée, portée résolue ou refusée en amont) : ce
        service rend une page, il ne rend pas de refus HTTP.
        """
        entrees = self._entrees
        if agent:
            entrees = [e for e in entrees if e.agent == agent]
        if type:
            entrees = [e for e in entrees if e.type == type]
        if run_id:
            entrees = [e for e in entrees if e.run_id == run_id]
        if portee is not None:
            # Une entrée sans projet (`None`) ne relève d'aucun : elle sort de
            # toute vue de projet plutôt que d'être rattachée au hasard (#222),
            # et c'est la portée du lot #277 qui en décide — la même règle que
            # le Kanban, les runs, les coûts et le flux temps réel.
            entrees = [e for e in entrees if portee.retient(e.projet_id)]
        if depuis:
            entrees = [e for e in entrees if e.horodatage >= depuis]
        if jusqua:
            entrees = [e for e in entrees if e.horodatage <= jusqua]
        retenues = sorted(entrees, key=lambda e: e.cle_tri(tri), reverse=(ordre == ORDRE_DESC))
        total = len(retenues)
        pages = (total + taille - 1) // taille if total else 0
        debut = (page - 1) * taille
        return {
            "entrees": [e.to_dict() for e in retenues[debut : debut + taille]],
            "total": total,
            "page": page,
            "taille": taille,
            "pages": pages,
        }
