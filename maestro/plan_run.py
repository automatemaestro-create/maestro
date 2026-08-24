"""Le plan d'un run, tel qu'il voyage : nœuds et arêtes (ticket #490, lot 2 de #488).

Le graphe d'un run existait **dans le moteur et nulle part ailleurs**.
`Task.dependances` porte les arêtes, `dependants_directs()` construit la table
inverse, l'exécution les respecte et le parallélisme est réel — mais rien de
tout cela ne franchissait la frontière : ni `StepRecord`, ni `Event`, ni
`EtatTache` ne portaient la moindre dépendance, si bien que l'API rendait des
tâches **à plat**. Ce module ouvre le chemin, et il ne transporte que ce que le
plan sait : un identifiant, un titre, les dépendances, l'ossature de checklist.

Ce qui n'y est pas est aussi important que ce qui y est. **Ni agent, ni statut,
ni coût, ni durée** : rien de tout cela n'existe au moment où le plan est écrit
— l'agent est routé au démarrage de la tâche (#42), le reste se mesure en
travaillant. Le plan dit *ce qu'il y a à faire et dans quel ordre* ; l'état dit
*où l'on en est*, et il vit déjà dans la projection (`EtatTache`). Le graphe
servi par l'API est la **jointure des deux** (`maestro.controltower.graphe`) ;
mélanger les deux ici aurait fait porter au plan une vérité qu'il n'a pas.

**Pourquoi le plan entier, et une seule fois.** Les nœuds auraient pu arriver
un par un, portés par le `tache.statut` de chaque tâche qui démarre — c'est le
chemin qu'ont pris le ticket externe (#187), le projet (#222) et le détail
(#246). Il aurait été faux ici : une tâche n'émet son premier événement qu'en
**démarrant**, donc le graphe aurait poussé nœud par nœud, dans l'ordre
d'exécution, et aurait donné à lire une découverte progressive là où le plan est
**figé au départ** (`topological_order`, appelé une fois avant la première
exécution). Un graphe qui pousse en direct ne se lit pas, il se subit — c'est
l'argument même par lequel #489 a tranché en faveur d'une ossature déclarée
d'avance, et il vaut à l'identique un cran au-dessus.

Le module est **feuille**, comme `maestro.detail_tache` et pour les mêmes
raisons : ces formes traversent des couches qui ne peuvent pas dépendre les unes
des autres — le journal les transporte (`StepRecord.plan`), le pont les diffuse
(`run.plan`), la projection les garde (`EtatExecution.plan`).
`maestro.controltower.events` les ré-exporte, comme il ré-exporte
`ReferenceTicket` et `EtapeTache`.

Deux règles gouvernent la relecture, et ce sont celles de #246 : **rien ne
s'invente** — un nœud sans identifiant n'a rien à dessiner et est écarté plutôt
que rendu en blanc — et **rien ne se refuse** — un plan relu du bus ou du
journal durable n'est jamais revalidé (une dépendance qui ne désigne aucun nœud
du plan est gardée telle quelle et ignorée au dessin, plutôt que de rendre
illisible un run passé).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from maestro.references import LONGUEUR_MAX_ID

if TYPE_CHECKING:  # pragma: no cover - annotations seulement
    from maestro.orchestrator.schema import Task

#: Longueur au-delà de laquelle un titre de nœud n'est plus une étiquette mais
#: un paragraphe : celle du champ `titre` de `task.schema.json`, redite ici
#: parce que ce module ne dépend pas du schéma (il relit ce qui a **déjà** été
#: validé, cf. la docstring) et qu'un plan venu du bus n'en a pas la garantie.
LONGUEUR_MAX_TITRE = 120


def _texte(valeur: Any) -> str:
    """Le texte d'une valeur brute, espaces normalisés (jamais None)."""
    return " ".join(str(valeur or "").split())


def _identifiants(valeurs: Any) -> tuple[str, ...]:
    """Les identifiants lisibles d'une valeur brute, dédoublonnés, dans l'ordre.

    Une chaîne **est** une `Sequence` : l'exclure évite d'itérer ses caractères,
    ce qui rendrait autant de fausses dépendances que de lettres.
    """
    if isinstance(valeurs, str) or not isinstance(valeurs, Sequence):
        return ()
    vus: dict[str, None] = {}
    for brut in valeurs:
        identifiant = _texte(brut)[:LONGUEUR_MAX_ID]
        if identifiant:
            vus.setdefault(identifiant, None)
    return tuple(vus)


@dataclass(frozen=True)
class NoeudPlan:
    """Un nœud du plan : la tâche telle que la décomposition l'a écrite (#490).

    `dependances` porte les **arêtes entrantes** — les identifiants des tâches
    qui doivent être terminées avant celle-ci —, dans l'ordre du plan. `etapes`
    porte l'**ossature de checklist** posée par l'orchestrateur (#489) : des
    libellés seuls, jamais un état. C'est ce qui rend une tâche lisible **avant**
    qu'elle démarre, et c'est la seule raison de la transporter ici plutôt que
    d'attendre le `tache.detail` que le moteur consigne à l'exécution — sur un
    graphe, un nœud qui n'a pas encore démarré est la moitié du dessin.
    """

    id: str
    titre: str = ""
    dependances: tuple[str, ...] = ()
    etapes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Réémet le nœud en dict JSON-sérialisable (`{id, titre, dependances, etapes}`)."""
        return {
            "id": self.id,
            "titre": self.titre,
            "dependances": list(self.dependances),
            "etapes": list(self.etapes),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NoeudPlan:
        """Reconstruit un nœud depuis sa forme `to_dict` (clés absentes → défauts)."""
        return cls(
            id=data.get("id", ""),
            titre=data.get("titre", ""),
            dependances=tuple(data.get("dependances") or ()),
            etapes=tuple(data.get("etapes") or ()),
        )

    @property
    def vide(self) -> bool:
        """Le nœud n'apprend-il rien (pas d'identifiant) ?

        Le titre seul ne fait pas un nœud : sans identifiant, aucune arête ne
        peut le désigner et aucun état de tâche ne peut s'y rattacher — il ne
        resterait qu'une étiquette flottante.
        """
        return not self.id

    def valide(self) -> NoeudPlan:
        """Rend le nœud **normalisé** : identifiant et titre bornés, arêtes dédoublonnées."""
        return NoeudPlan(
            id=_texte(self.id)[:LONGUEUR_MAX_ID],
            titre=_texte(self.titre)[:LONGUEUR_MAX_TITRE],
            dependances=_identifiants(self.dependances),
            etapes=tuple(
                libelle
                for brut in (self.etapes or ())
                if (libelle := _texte(brut)[:LONGUEUR_MAX_TITRE])
            ),
        )

    @classmethod
    def depuis(cls, data: Any) -> NoeudPlan | None:
        """Construit un nœud depuis un dict brut — **None** s'il n'apprend rien.

        Le pendant tolérant de `from_dict`, pour tout ce qui arrive de
        l'extérieur : une ligne de journal relue, un événement JSON.
        """
        if not isinstance(data, Mapping):
            return None
        noeud = cls.from_dict(data).valide()
        return None if noeud.vide else noeud


def noeuds_depuis(data: Any) -> list[NoeudPlan]:
    """Les nœuds lisibles d'une valeur brute — **liste vide** quand il n'y en a pas.

    L'ordre est celui du plan : un graphe se lit dans l'ordre où il a été écrit,
    pas trié. Ce qui n'apprend rien est écarté nœud à nœud — une entrée illisible
    ne fait pas perdre les autres. Un identifiant vu deux fois ne compte qu'une
    fois : un nœud dédoublé dessinerait deux boîtes pour une seule tâche.
    """
    lus = (NoeudPlan.depuis(brut) for brut in (data if isinstance(data, list) else ()))
    retenus: dict[str, NoeudPlan] = {}
    for noeud in lus:
        if noeud is not None:
            retenus.setdefault(noeud.id, noeud)
    return list(retenus.values())


def noeuds_en_liste(noeuds: Sequence[NoeudPlan]) -> list[dict[str, Any]]:
    """La forme JSON d'une liste de nœuds — `[]` quand il n'y en a aucun.

    Le pendant de `etapes_en_liste` (#246) : côté client, une liste vide dit
    « ce run n'a pas publié son plan », et le graphe se reconstruit alors de ses
    seules tâches (cf. `maestro.controltower.graphe`).
    """
    return [noeud.to_dict() for noeud in noeuds]


def noeuds_du_plan(tasks: Sequence[Task]) -> list[NoeudPlan]:
    """Les nœuds transportables d'un plan du moteur — dans l'ordre du plan.

    Le seul point où ce module touche au moteur, et il n'en lit que quatre
    champs : le plan est déjà validé contre `task.schema.json` (identifiants
    uniques, dépendances résolubles, graphe acyclique), donc rien n'est
    revalidé — seulement borné, pour que ce qui part sur le bus soit de taille
    connue.
    """
    lus = (
        NoeudPlan(
            id=task.id,
            titre=task.titre,
            dependances=tuple(task.dependances),
            etapes=tuple(task.etapes),
        ).valide()
        for task in tasks
    )
    return [noeud for noeud in lus if not noeud.vide]


def dependants_directs(noeuds: Sequence[NoeudPlan]) -> dict[str, tuple[str, ...]]:
    """Inverse le graphe : pour chaque nœud, qui dépend de lui — dans l'ordre du plan.

    Le pendant lisible du `_dependants_directs` de la boucle
    (`maestro.engine.loop`), qui sert le carnet d'adresses du handoff (#44) :
    même table, même ordre, sur la forme transportée plutôt que sur les `Task`.
    Une dépendance qui ne désigne **aucun** nœud du plan est ignorée plutôt que
    de créer une clé fantôme — la relecture est tolérante (cf. la docstring du
    module), et une arête sans amont n'a rien à dessiner.
    """
    dependants: dict[str, list[str]] = {noeud.id: [] for noeud in noeuds}
    for noeud in noeuds:
        for amont in noeud.dependances:
            if amont in dependants and noeud.id not in dependants[amont]:
                dependants[amont].append(noeud.id)
    return {identifiant: tuple(aval) for identifiant, aval in dependants.items()}
