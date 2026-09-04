"""Le **graphe d'un run** : nœuds, arêtes, branches parallèles (ticket #490, lot 2 de #488).

Un run se suit aujourd'hui comme une liste (le Kanban, #475) ou comme une barre
(la progression, #473) : deux lectures qui disent *combien dans quel état*, et
aucune qui dise *quoi après quoi*. Ce module rend la troisième — celle d'un
pipeline GitHub Actions ou d'un flux n8n : l'action en cours, ce qu'elle
enchaîne, ce qui part en parallèle.

Il **joint** deux sources et n'en invente aucune :

- le **plan** (`maestro.plan_run`), publié une fois par `run.plan` : les nœuds,
  les arêtes, l'ossature de chaque checklist. C'est ce qui est **figé au
  départ** — l'ordre a été décidé une fois, avant la première exécution ;
- l'**état** que la projection tient de chaque tâche (`EtatTache`) : agent,
  statut, coût, durée, checklist cochée. C'est ce qui **bouge**, et c'est déjà
  ce que le flux temps réel pousse au client, événement par événement.

D'où la propriété qui répond au critère « en direct sur le canal existant » :
**le graphe n'a pas d'événement à lui**. Il se recompose à la lecture, si bien
qu'un `tache.statut` (un nœud qui démarre), un `tache.detail` (une étape qui se
coche, #489) ou l'issue d'une tâche (une arête qui s'allume) le font bouger sans
qu'aucun second signal n'ait été inventé.

Ce que le front n'a pas à recalculer
------------------------------------

Le critère est explicite : « dans une forme qui se dessine sans être recalculée
par le front ». Trois choses sont donc **servies** plutôt que déductibles :

- `niveaux` — les nœuds rangés par rang topologique, et chaque nœud porte son
  `niveau` et son `rang`. Un client qui devrait les calculer réécrirait un tri
  topologique en TypeScript, sur les seuls nœuds qu'il a chargés ;
- `compartiment` — la couleur du nœud, lue dans la table partagée
  (`maestro.controltower.progression`) et non dans une correspondance réinventée
  par écran, exactement comme une colonne de Kanban ;
- `plat`, `profondeur`, `largeur` — de quoi choisir une mise en page avant même
  d'avoir parcouru les nœuds.

Les branches parallèles, et pourquoi ce sont les niveaux
--------------------------------------------------------

Deux tâches sans dépendance entre elles et prêtes en même temps **ne doivent pas
paraître séquentielles** — c'est le deuxième critère, et c'est un fait du moteur :
la boucle crée une tâche asyncio par tâche du plan, chacune n'attend que ses
propres dépendances, et le parallélisme est borné par un sémaphore, jamais par un
ordre. Le rang d'un nœud est donc **le plus long chemin qui y mène** (et non son
rang d'arrivée dans un tri topologique quelconque) : à ce compte-là, et à ce
compte-là seulement, deux tâches indépendantes tombent au même niveau. Un tri
topologique ordinaire les aurait mises l'une après l'autre — vraie comme
séquence, fausse comme dessin.

`largeur` est le niveau le plus peuplé : la parallélisation maximale que le plan
autorise. Elle ne dit pas ce que le run fera vraiment — le sémaphore
(`parallelisme`) peut être plus étroit —, et le graphe ne prétend pas le
contraire : il rend la **topologie**, pas l'ordonnancement.

Un graphe plat est un graphe, pas un vide
-----------------------------------------

Un plan sans aucune dépendance déclarée est le cas **le plus courant** : la
décomposition rend souvent des tâches indépendantes. Le graphe le dit alors
(`plat`), rend ses nœuds sur un **seul niveau** — ce qui est la lecture juste :
tout peut partir en même temps — et ne se confond pas avec un run sans tâche,
qui n'a, lui, aucun nœud.

`plan_connu` distingue le troisième cas, celui qu'on ne peut pas deviner : un run
qui n'a **pas publié son plan** (moteur antérieur à ce lot, journal durable
rejoué d'avant, planification en échec). Ses nœuds sont alors reconstruits de ses
seules tâches vues, sans aucune arête — c'est-à-dire un graphe plat lui aussi,
mais pour une raison qui n'est pas la même. Les confondre ferait lire « ces
tâches sont indépendantes » là où il faut lire « on ne sait pas ».

Ce module est une **feuille**, comme `progression.py` : il ne connaît ni FastAPI,
ni HTTP, ni la projection. Il ne sait que composer un graphe à partir de nœuds de
plan et d'états de tâche — d'où `EtatNoeud`, qui est exactement ce que
`ControlTowerState` lui passe de chaque `EtatTache`, et rien de plus.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from maestro.controltower.progression import (
    A_FAIRE,
    BLOQUEES,
    ECHECS,
    STATUT_BACKLOG,
    TERMINEES,
    compartiment,
)
from maestro.detail_tache import EtapeTache, etapes_en_liste
from maestro.plan_run import NoeudPlan, dependants_directs

#: États d'une arête, c'est-à-dire du **passage de relais** entre deux tâches :
#:
#: - `attendue` — l'amont n'a pas rendu son issue ; rien n'est encore passé ;
#: - `franchie` — l'amont a terminé : la main passe, l'aval peut démarrer ;
#: - `rompue` — l'amont n'a pas abouti (échec, ou blocage hérité) : l'aval ne
#:   démarrera pas et se bloquera à son tour (#43).
#:
#: Ce sont, aux mots près, les deux annonces de `HandoffRelais.annonce` (#44) —
#: `handoff` quand la tâche a réussi, `notification` sinon —, lues là où elles
#: existent **toujours** : dans le statut de l'amont. Se brancher sur le message
#: lui-même aurait laissé toutes les arêtes éteintes dans la configuration
#: ordinaire : le relais n'existe que si une messagerie est injectée
#: (`OrchestrationEngine(..., mailbox=…)`), et la Control Tower lance ses runs
#: par `OrchestrationEngine.default()`, qui n'en injecte aucune. Une arête qui ne
#: s'allume jamais est le défaut même que #488 a nommé chez `consigne_detail` :
#: « toute la plomberie est posée, rien ne la remplit ».
ARETE_ATTENDUE = "attendue"
ARETE_FRANCHIE = "franchie"
ARETE_ROMPUE = "rompue"

#: Les compartiments (`progression.py`) qui **rompent** le relais : l'amont ne
#: rendra pas ce que l'aval attendait. Lus dans la table partagée plutôt que dans
#: les statuts du moteur, pour la raison qui a fait exister cette table — un
#: statut de plus s'y déclare une fois, et toutes les vues suivent.
_COMPARTIMENTS_ROMPUS = frozenset({ECHECS, BLOQUEES})


@dataclass(frozen=True)
class EtatNoeud:
    """Ce que la projection sait d'une tâche, et que le plan ignore (#490).

    L'exact pendant de `progression_des_statuts(statuts)` : la projection décide
    **quelles** tâches et **dans quel état**, ce module ne décide que du dessin.
    Six champs, ceux du premier critère — agent, statut, checklist, coût,
    durée —, et pas un de plus : ce qui n'est pas dessiné n'a pas à traverser.

    Le défaut est celui d'une tâche que la projection ne connaît **pas encore** :
    un nœud du plan qui n'a pas démarré. C'est un état normal du graphe, et même
    le seul qu'il ait à montrer avant que le run ne commence. Son statut est donc
    `backlog` et non la chaîne vide — la machine à états
    ([docs/03 §3](../../docs/03-modele-de-donnees.md)) a un mot pour « déclarée,
    pas encore prise », le laisser vide le ferait tomber dans `autres`, et la
    moitié d'un graphe qui n'a pas commencé se dessinerait en « statut inconnu »
    alors que rien n'est inconnu. C'est l'usage même pour lequel
    `progression.py` déclare ce statut sans que le moteur l'émette : que la table
    couvre la machine à états entière, et pas seulement ce qui circule.
    """

    statut: str = STATUT_BACKLOG
    agent: str = ""
    role: str = ""
    cout_usd: float | None = None
    # Le coût ci-dessus est-il **partiel** (#835) — celui d'une tâche qui tourne
    # encore, relevé pendant qu'il se dépense — ou soldé par l'issue de la
    # tâche ? Posé par la projection, qui seule sait ce qui a été relevé.
    cout_partiel: bool = False
    duree_ms: int | None = None
    etapes: tuple[EtapeTache, ...] = ()


@dataclass(frozen=True)
class NoeudGraphe:
    """Un nœud du graphe : la tâche du plan, augmentée de là où elle en est.

    `dependances` sont les arêtes entrantes et `dependants` les sortantes — les
    deux, parce qu'un dessin les parcourt dans les deux sens et que la table
    inverse est déjà construite ici (`dependants_directs`). `niveau` est le rang
    topologique (le plus long chemin qui mène au nœud) et `rang` sa position dans
    ce niveau, dans l'ordre du plan : de quoi poser la boîte sans rien recalculer.
    """

    id: str
    titre: str = ""
    dependances: tuple[str, ...] = ()
    dependants: tuple[str, ...] = ()
    niveau: int = 0
    rang: int = 0
    statut: str = STATUT_BACKLOG
    compartiment: str = A_FAIRE
    agent: str = ""
    role: str = ""
    cout_usd: float | None = None
    cout_partiel: bool = False
    duree_ms: int | None = None
    etapes: tuple[EtapeTache, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Réémet le nœud en dict JSON-sérialisable (la forme du REST)."""
        return {
            "id": self.id,
            "titre": self.titre,
            "dependances": list(self.dependances),
            "dependants": list(self.dependants),
            "niveau": self.niveau,
            "rang": self.rang,
            "statut": self.statut,
            "compartiment": self.compartiment,
            "agent": self.agent,
            "role": self.role,
            "cout_usd": self.cout_usd,
            # Le même coût que la carte de la tâche, avec la même réserve
            # (#835) : `true` tant que la tâche tourne et que le montant est un
            # relevé en cours, `false` dès que son issue l'a soldé.
            "cout_partiel": self.cout_partiel,
            "duree_ms": self.duree_ms,
            "etapes": etapes_en_liste(self.etapes),
        }


@dataclass(frozen=True)
class AreteGraphe:
    """Une arête du graphe : une dépendance, et où en est le passage de relais.

    `de` est l'amont (la tâche qui doit finir), `vers` l'aval (celle qui
    attend) — le sens du **flux de travail**, et non celui de la déclaration :
    `Task.dependances` se lit « j'attends ceux-ci », un dessin se lit « ceci mène
    à cela ». Prendre le sens de la déclaration ferait des flèches à rebours sur
    tous les écrans.
    """

    de: str
    vers: str
    etat: str = ARETE_ATTENDUE

    def to_dict(self) -> dict[str, str]:
        """Réémet l'arête en dict JSON-sérialisable (`{de, vers, etat}`)."""
        return {"de": self.de, "vers": self.vers, "etat": self.etat}


@dataclass(frozen=True)
class GrapheRun:
    """Le graphe d'un run : ses nœuds, ses arêtes, ses niveaux.

    `plan_connu` dit si le run a publié son plan (#490) : sinon les nœuds sont
    reconstruits de ses seules tâches vues et il n'y a **aucune** arête, faute de
    les connaître — un graphe plat, mais pour une autre raison que « rien n'est
    déclaré ». La distinction est portée jusqu'au client parce qu'elle change ce
    qu'on a le droit d'en conclure.
    """

    run_id: str
    noeuds: tuple[NoeudGraphe, ...] = ()
    aretes: tuple[AreteGraphe, ...] = ()
    niveaux: tuple[tuple[str, ...], ...] = ()
    plan_connu: bool = False

    @property
    def plat(self) -> bool:
        """Le graphe est-il plat — aucune arête, donc rien à enchaîner ?

        Vrai aussi d'un run **sans tâche** : il n'y a rien à enchaîner là non
        plus. `nb_noeuds` distingue les deux, et c'est à lui de le faire — un
        `plat` qui voudrait dire deux choses n'en dirait aucune.
        """
        return not self.aretes

    @property
    def nb_noeuds(self) -> int:
        """Le nombre de nœuds du graphe — celui du **plan**, pas des tâches vues.

        ⚠ Il ne vaut donc pas `nb_taches`, et l'écart n'est pas un défaut : le
        plan annonce ce qui **sera** fait, `nb_taches` (donc `progression.total`,
        #473) compte ce que le run a **réellement porté**, c'est-à-dire les
        tâches qui ont démarré. Les deux se rejoignent à la fin d'un run qui va
        au bout, et divergent tout du long — c'est exactement ce qu'un graphe est
        là pour montrer. Les faire coïncider aurait demandé de retirer du graphe
        les nœuds pas encore démarrés, c'est-à-dire de rendre un dessin qui
        pousse au lieu d'un plan.
        """
        return len(self.noeuds)

    @property
    def nb_aretes(self) -> int:
        """Le nombre d'arêtes du graphe."""
        return len(self.aretes)

    @property
    def profondeur(self) -> int:
        """Le nombre de niveaux — la longueur du plus long enchaînement."""
        return len(self.niveaux)

    @property
    def largeur(self) -> int:
        """Le niveau le plus peuplé — la parallélisation que le plan **autorise**.

        Jamais celle qu'il obtiendra : le sémaphore du moteur (`parallelisme`)
        peut être plus étroit, et un run suspendu (#477) ne démarre rien du tout.
        Le graphe rend la topologie, pas l'ordonnancement.
        """
        return max((len(niveau) for niveau in self.niveaux), default=0)

    def to_dict(self) -> dict[str, Any]:
        """Réémet le graphe en dict JSON-sérialisable (la forme du REST).

        Les cinq scalaires dérivés sont **servis** plutôt que laissés à déduire,
        pour la raison qui a fait servir `total` et `soldees` dans la progression
        (#473) : ce sont eux qu'une vue lit d'abord — pour choisir une mise en
        page, pour afficher « 3 branches en parallèle », pour dire « graphe
        plat » — et les recalculer côté client demanderait de parcourir les
        nœuds avant de savoir comment les dessiner.
        """
        return {
            "run_id": self.run_id,
            "plan_connu": self.plan_connu,
            "plat": self.plat,
            "nb_noeuds": self.nb_noeuds,
            "nb_aretes": self.nb_aretes,
            "profondeur": self.profondeur,
            "largeur": self.largeur,
            "noeuds": [noeud.to_dict() for noeud in self.noeuds],
            "aretes": [arete.to_dict() for arete in self.aretes],
            "niveaux": [list(niveau) for niveau in self.niveaux],
        }


def _niveaux_topologiques(noeuds: Sequence[NoeudPlan]) -> dict[str, int]:
    """Le rang de chaque nœud : **le plus long chemin qui y mène**, en partant de 0.

    Un nœud sans dépendance (dans le plan) est au niveau 0 ; les autres suivent
    leur amont le plus tardif. C'est ce qui met au même niveau deux tâches
    indépendantes — la propriété que le deuxième critère demande, et que le tri
    topologique du moteur (`topological_order`, qui rend une **séquence**) ne
    donne pas.

    Une dépendance qui ne désigne aucun nœud du plan est ignorée : la relecture
    est tolérante (`maestro.plan_run`), et une arête sans amont n'a rien à
    retenir. Un **cycle** est impossible sur un plan validé (`validate_plan` le
    refuse avant tout run), mais un plan relu du bus ne repasse par aucune
    validation : les nœuds qu'aucun ordre ne résout sont donc rangés **après**
    tout le reste, sur un dernier niveau, plutôt que de faire tourner la boucle
    sans fin. Rendre un graphe étrange vaut mieux que ne rien rendre du tout.
    """
    connus = {noeud.id for noeud in noeuds}
    amont = {
        noeud.id: [dep for dep in noeud.dependances if dep in connus] for noeud in noeuds
    }
    niveau: dict[str, int] = {}
    restants = list(noeuds)
    while restants:
        differes: list[NoeudPlan] = []
        for noeud in restants:
            if all(dep in niveau for dep in amont[noeud.id]):
                niveau[noeud.id] = 1 + max(
                    (niveau[dep] for dep in amont[noeud.id]), default=-1
                )
            else:
                differes.append(noeud)
        if len(differes) == len(restants):
            # Aucun n'a pu être rangé : c'est un cycle (ou une portion de plan
            # qui n'aurait pas dû arriver jusqu'ici). On les pose tous ensemble,
            # au niveau suivant, et on sort.
            dernier = 1 + max(niveau.values(), default=-1)
            for noeud in differes:
                niveau[noeud.id] = dernier
            break
        restants = differes
    return niveau


def _etat_arete(amont: EtatNoeud) -> str:
    """L'état d'une arête, lu dans le **compartiment** de son amont.

    Terminée, le relais passe ; échouée ou bloquée, il est rompu ; le reste
    attend. Passer par `compartiment()` plutôt que par les statuts du moteur
    n'est pas une commodité : c'est la table que toutes les vues partagent, et un
    statut qu'elle ignorerait tomberait dans `autres`, donc dans « attendue » —
    ne rien affirmer plutôt qu'affirmer de travers.
    """
    range_dans = compartiment(amont.statut)
    if range_dans == TERMINEES:
        return ARETE_FRANCHIE
    if range_dans in _COMPARTIMENTS_ROMPUS:
        return ARETE_ROMPUE
    return ARETE_ATTENDUE


def _etapes_du_noeud(noeud: NoeudPlan, etat: EtatNoeud) -> tuple[EtapeTache, ...]:
    """La checklist à dessiner : celle que l'agent tient, sinon l'ossature du plan.

    Exactement l'arbitrage de #489, un cran plus haut : le plan rend la tâche
    lisible **avant** qu'elle démarre (des libellés, tous « à faire »), l'agent
    dit la vérité **pendant** qu'elle tourne. La projection porte la seconde dès
    la première ligne de détail consignée — qui est justement l'ossature, posée
    par l'exécuteur au démarrage —, si bien que le repli ne sert qu'au nœud qui
    n'a pas encore commencé. C'est là qu'il compte : sur un graphe, la moitié des
    boîtes n'a pas commencé.
    """
    if etat.etapes:
        return tuple(etat.etapes)
    return tuple(EtapeTache(libelle=libelle) for libelle in noeud.etapes)


def graphe_du_run(
    run_id: str,
    noeuds: Sequence[NoeudPlan],
    etats: Mapping[str, EtatNoeud],
    *,
    plan_connu: bool = True,
) -> GrapheRun:
    """Compose le graphe d'un run à partir de son plan et de l'état de ses tâches.

    `noeuds` est le plan **dans son ordre** (celui de la décomposition, figé au
    départ) ; `etats` ce que la projection sait de chaque tâche, indexé par
    identifiant — une tâche absente est une tâche qui n'a pas démarré, pas une
    erreur. `plan_connu` dit d'où viennent les nœuds : du plan publié, ou d'un
    repli sur les tâches vues (cf. la docstring du module).

    Ne lève jamais et ne refuse rien : un run inconnu se demande **avant**, à la
    route, qui a un client à qui répondre.
    """
    aval = dependants_directs(noeuds)
    niveau_de = _niveaux_topologiques(noeuds)
    rang_courant: dict[int, int] = {}
    lignes: list[NoeudGraphe] = []
    for noeud in noeuds:
        etat = etats.get(noeud.id, EtatNoeud())
        niveau = niveau_de.get(noeud.id, 0)
        rang = rang_courant.get(niveau, 0)
        rang_courant[niveau] = rang + 1
        lignes.append(
            NoeudGraphe(
                id=noeud.id,
                titre=noeud.titre,
                # Les dépendances sont rendues **telles que le plan les
                # déclare**, y compris celles qui ne désignent aucun nœud
                # connu : le graphe ne dessine que ce qu'il sait relier
                # (`aretes`), mais taire une déclaration ferait croire à un plan
                # plus simple qu'il n'est.
                dependances=noeud.dependances,
                dependants=aval.get(noeud.id, ()),
                niveau=niveau,
                rang=rang,
                statut=etat.statut,
                compartiment=compartiment(etat.statut),
                agent=etat.agent,
                role=etat.role,
                cout_usd=etat.cout_usd,
                cout_partiel=etat.cout_partiel,
                duree_ms=etat.duree_ms,
                etapes=_etapes_du_noeud(noeud, etat),
            )
        )
    connus = {noeud.id for noeud in noeuds}
    aretes = tuple(
        AreteGraphe(
            de=amont,
            vers=noeud.id,
            etat=_etat_arete(etats.get(amont, EtatNoeud())),
        )
        for noeud in noeuds
        for amont in noeud.dependances
        if amont in connus
    )
    profondeur = 1 + max(niveau_de.values(), default=-1)
    niveaux = tuple(
        tuple(ligne.id for ligne in lignes if ligne.niveau == rang)
        for rang in range(profondeur)
    )
    return GrapheRun(
        run_id=run_id,
        noeuds=tuple(lignes),
        aretes=aretes,
        niveaux=niveaux,
        plan_connu=plan_connu,
    )
