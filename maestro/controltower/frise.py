"""La **frise d'activité d'un run** : ce que les agents font et se disent (ticket #355).

Pendant un run, on ne voyait pas ce qui se passe. Deux compteurs — tâches
traitées, agents actifs — puis le rapport à la fin ; entre les deux, **une
attente de décision humaine était indiscernable d'un travail en cours** (53
minutes perdues sur le run du 14 août sans qu'aucun écran ne le dise). Ce module
rend la lecture qui manquait : les changements de statut de tâche **et** les
messages inter-agents sur une même frise, triés dans le temps, rangés en couloirs
— un couloir par agent.

Il ne crée aucune donnée. Les trois flux qu'il fusionne existent déjà et sont
déjà persistés ; ce qui manquait était leur **jointure** :

- `tache.statut` — le moteur consigne le démarrage (`<tâche>:debut`, #98) puis
  l'issue de chaque tâche, blocages compris (`_consigne_blocage`, #43) ;
- `message.inter_agents` — la messagerie journalise chaque passage de relais
  (`consigne_message`, #44) ;
- `validation.demande` / `validation.decision` — le garde-fou humain publie sa
  question puis la réponse (#48).

La source est le **journal requêtable** (`ServiceJournal`, #478) et non la
projection : il porte déjà l'identifiant stable que le tri exige, il est alimenté
des deux côtés (rejeu du journal durable au démarrage, puis pompe au fil de
l'eau), et il se filtre par run. Comme le graphe (#490), la frise **n'a pas
d'événement à elle** : elle se recompose à la lecture, si bien que tout ce qui
la fait bouger circule déjà sur le canal existant.

Pourquoi la validation entre dans la frise
------------------------------------------

Le troisième critère du ticket est le cas d'usage qui l'a motivé : une tâche
**bloquée** et une tâche **en attente de validation humaine** doivent se
distinguer d'une tâche **en cours**, à l'œil, sans ouvrir de détail. Or le
moteur n'émet pas `en_attente_validation` — `progression.py` le nomme depuis
#473 sans que rien ne le produise —, et l'attente ne se lit aujourd'hui que dans
la file des validations, qui dit l'**état courant** et jamais l'**instant** où il
a changé. Une frise a besoin de l'instant.

`validation.demande` **est** ce changement de statut, vu du run : c'est la
seconde où la tâche s'arrête sur un humain. La frise le rend donc comme tel, avec
le statut que la machine à états de [docs/03 §3](../../docs/03-modele-de-donnees.md)
avait déjà prévu pour lui. Aucun vocabulaire nouveau n'est inventé : la décision
reprend au mot près les deux statuts que le moteur écrit lui-même sur l'étape
`<tâche>:validation` (`approuve`, `refuse` — `maestro.engine.executor`).

Ce qui n'y entre pas est aussi un choix : `agent.activite` (relances, refus
d'outil, activité en cours de tâche) est le **bruit de fond** d'un run, pas un
changement d'état ni un échange — l'y verser noierait les trois signaux que le
ticket demande de distinguer. Le journal requêtable reste là pour qui veut tout.

Pourquoi le blocage déclaré y entre quand même (#719)
-----------------------------------------------------

`tache.blocage` est le quatrième flux, et il n'affaiblit pas la règle ci-dessus :
il en est le cas limite qui l'explique. Un agent qui appelle `signaler_blocage`
ne produit pas du bruit de fond — il fait le geste **rare et délibéré** de dire
*pourquoi* il n'avance pas, et c'est précisément le signal dont l'absence a
coûté les 53 minutes du 14 août. Une règle de détection sait dire « bloquée
depuis 40 minutes » ; elle ne saura jamais dire « le dépôt de recette refuse mes
identifiants ».

⚠ C'est aussi pour cela qu'il lui fallait un **type à lui** (docs/31 §3.1) : la
note de #719 annonçait que « la frise reçoit l'entrée sans travail de son côté »,
ce qui n'était pas exact — la frise filtre par type, et un blocage rangé sous
`agent.activite` aurait été consigné puis **invisible ici**, l'inverse exact de
ce que le verbe existe pour faire. Ouvrir `agent.activite` en bloc pour l'y faire
entrer aurait noyé les trois autres. Un type distinct est la seule voie qui
montre le blocage **sans** défaire le tri.

Il ne dit rien du **statut** de la tâche, qui ne bouge pas : un agent qui bute
n'est pas une tâche `bloquee` au sens de la cascade de #43 — celle-là n'a jamais
été exécutée, celui-ci travaille encore et parle.

Le signe de vie du couloir, et pourquoi ce n'est pas une entrée (#836)
----------------------------------------------------------------------

La règle ci-dessus a un coût, mesuré sur un run réel : ~220 entrées de journal,
**3** sur la frise, et pas un pixel qui bouge pendant les douze minutes d'une
tâche en cours. Le remède n'est pas d'ouvrir `agent.activite` — la raison de
l'écarter tient —, c'est de donner au **couloir** un attribut : `activite`, le
dernier geste de l'agent sur une tâche qui travaille, daté et abrégé
(`maestro.controltower.signe_de_vie`). Un attribut de l'en-tête, jamais une
ligne de plus : `entrees` ne reçoit **aucune** entrée d'activité, `TYPES_FRISE`
ne bouge pas, et le tri reste ce qu'il est. Un couloir dont aucune tâche ne
travaille n'en porte pas (`null`) — c'est ce qui distingue à l'œil un agent qui
avance d'un agent arrêté, sans lire une seule ligne.

La règle qui décide s'il y a un signe (« seule une tâche `en_cours` en porte
un ») vit chez la projection, avec le graphe qui la lit aussi : ce module ne
fait que ranger le signe dans le couloir qui lui revient, par la même
normalisation de nom que les entrées — deux façons de nommer un agent ne
feraient pas deux couloirs ici plus qu'ailleurs.

Le couloir de repli, et pourquoi il n'est pas une commodité
-----------------------------------------------------------

« Aucune entrée n'est jamais perdue faute de couloir » : c'est le deuxième
critère, et le piège n'est pas celui qu'on croit. Il ne suffit pas de ramasser
les entrées sans agent — **le moteur en produit qui portent un agent qui n'en est
pas un** : une tâche bloquée n'a jamais été routée, donc `_consigne_blocage`
consigne `agent="—"`, `role="non exécutée"`. Un couloir nommé « — » serait
absurde, et ce sont précisément les entrées du troisième critère. `AGENT_ABSENT`
les reconnaît et les range au repli, où elles restent parfaitement lisibles :
leur statut, lui, dit « bloquée ».

Le rattachement se fait donc au **rôle** — le nom d'agent que le run a routé —,
comme la note technique du ticket l'annonce : le plan est centré tâche et non
membre, et l'identité d'instance est instruite ailleurs. Un couloir par agent
**du run**, dans l'ordre où ses tâches sont apparues, y compris pour un agent qui
n'a encore rien dit : une file muette est une information.

Le tri, et pourquoi il se départage sur le rang
-----------------------------------------------

Les horodatages du dépôt sont à la **seconde** (`Event`, `StepRecord`,
`AgentMessage` — tous les trois). Sur un run parallèle, deux entrées portent donc
couramment le même instant, et un tri instable ferait sauter des lignes d'un
rafraîchissement à l'autre. Le départage est le **rang** du journal — pas la
chaîne `j-0007`, où « j-10000 » précéderait « j-9999 » (`journal.LARGEUR_ENTREE`
le dit déjà pour la pagination ; la raison est la même ici). Le rang est figé à
la consignation : deux appels sur le même journal rendent donc le même ordre,
quel que soit l'ordre dans lequel les entrées sont présentées à ce module.

Comme `progression.py` et `graphe.py`, ce module ne connaît ni FastAPI, ni HTTP,
ni la projection : il ne sait que composer une frise à partir d'entrées de
journal, et c'est l'appelant qui décide lesquelles. Il emprunte à `state.py` le
seul vocabulaire de la **file** de validation (`approuvee`), qui y est déclaré —
le redéclarer ici en ferait un second support, exactement ce que #365 a supprimé
ailleurs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from maestro.controltower.events import (
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_TACHE_BLOCAGE,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DECISION,
    EVENEMENT_VALIDATION_DEMANDE,
)
from maestro.controltower.journal import EntreeJournal
from maestro.controltower.progression import STATUT_EN_ATTENTE_VALIDATION
from maestro.controltower.signe_de_vie import SigneDeVie
from maestro.controltower.state import VALIDATION_APPROUVEE
from maestro.engine.executor import (
    STATUT_VALIDATION_APPROUVE,
    STATUT_VALIDATION_REFUSE,
)

#: Le nom d'agent que le moteur consigne quand il n'y en a **pas** : une tâche
#: bloquée n'a jamais été routée (`maestro.engine.loop._consigne_blocage`). Ce
#: n'est pas un agent, c'est un tiret — le prendre pour un couloir ouvrirait une
#: file nommée « — » où s'entasseraient toutes les tâches jamais exécutées.
AGENT_ABSENT = "—"

#: La clé du **couloir de repli** : ce qui n'a pas d'agent résoluble. Une chaîne
#: vide plutôt qu'un nom réservé, pour la raison qui vaut déjà dans le dépôt —
#: un nom réservé entre un jour en collision avec un vrai agent.
COULOIR_REPLI = ""

#: Les types d'événements que la frise retient. L'ordre n'a pas d'importance —
#: c'est un ensemble d'appartenance —, mais la **liste** en a une : elle est le
#: contrat de ce que la frise montre, et tout le reste du journal en est écarté
#: à dessein (cf. la docstring du module).
TYPES_FRISE = frozenset(
    {
        EVENEMENT_TACHE_STATUT,
        EVENEMENT_MESSAGE_INTER_AGENTS,
        EVENEMENT_VALIDATION_DEMANDE,
        EVENEMENT_VALIDATION_DECISION,
        EVENEMENT_TACHE_BLOCAGE,
    }
)

#: Combien d'entrées une frise rend au plus. Le plafond existe parce qu'un run
#: d'une heure en produit des centaines et qu'une vue en direct n'est pas un
#: export ; il retient les **plus récentes**, qui sont ce qu'on regarde quand on
#: demande « où en est-on ? ». Il ne se tait jamais : `total` et `tronquee`
#: disent ce qui a été laissé de côté, et le journal requêtable rend le reste.
PLAFOND_FRISE = 500


def _statut_de(entree: EntreeJournal) -> str:
    """Le statut de tâche que porte `entree`, dans le vocabulaire de docs/03 §3.

    Trois familles, une seule échelle. Un `tache.statut` porte déjà le sien.
    Une **demande** de validation est la seconde où la tâche s'arrête sur un
    humain : `en_attente_validation`, le statut que `progression.py` nomme depuis
    #473 sans que le moteur le produise. Une **décision** reprend les deux mots
    que le moteur écrit lui-même sur l'étape `<tâche>:validation`, plutôt que les
    `approuvee`/`refusee` du bus, qui sont le vocabulaire de la *file* de
    validation et non celui d'une tâche.

    Un `tache.blocage` (#719) porte le sien aussi, et le porte **tel quel** :
    `blocage_signale` (`maestro.engine.executor`). Ce mot n'est pas `bloquee`, à
    dessein — le rendre en `bloquee` ferait lire « cette tâche est morte » là où
    la vérité est « son agent bute et le dit, en travaillant encore », c'est-à-dire
    afficher la cascade de #43 au moment précis où quelqu'un demande de l'aide.
    Ce n'est pas non plus un mot du vocabulaire de docs/03 §3, et la famille des
    décisions de validation ci-dessus ne l'est déjà pas : ce champ dit le statut
    de l'**entrée**, pas celui de la carte.

    Un message n'a pas de statut de tâche : il rend la chaîne vide, et c'est
    ainsi qu'une vue distingue les deux flux sans interpréter le type.
    """
    if entree.type in (EVENEMENT_TACHE_STATUT, EVENEMENT_TACHE_BLOCAGE):
        return entree.statut
    if entree.type == EVENEMENT_VALIDATION_DEMANDE:
        return STATUT_EN_ATTENTE_VALIDATION
    if entree.type == EVENEMENT_VALIDATION_DECISION:
        return (
            STATUT_VALIDATION_APPROUVE
            if entree.statut == VALIDATION_APPROUVEE
            else STATUT_VALIDATION_REFUSE
        )
    return ""


def _couloir_de(agent: str) -> str:
    """Le couloir d'un agent — le repli quand il n'y en a pas de résoluble.

    Le tiret de `_consigne_blocage` compte pour une absence (cf. `AGENT_ABSENT`),
    et les espaces autour d'un nom ne font pas deux agents.
    """
    nom = agent.strip()
    return COULOIR_REPLI if not nom or nom == AGENT_ABSENT else nom


@dataclass(frozen=True)
class EntreeFrise:
    """Une entrée de la frise : un fait daté, attribué à un couloir.

    `id`, `agent`, `role`, `tache_id`, `titre` et `horodatage` sont ceux de
    l'entrée de journal dont elle vient — rien n'est réécrit, et c'est ce qui
    permet de la retrouver dans `GET /api/journal`.

    `type` reste le **type d'événement d'origine** : c'est lui qui sépare les
    deux flux que le critère demande de mêler, et le masquer derrière un
    vocabulaire à nous ferait perdre la trace de ce qui a été fusionné.

    `statut` est le statut de tâche **résolu** (`_statut_de`) : vide pour un
    message, et c'est la seule valeur que la frise calcule.

    `objet` est ce que l'entrée dit — la phrase du journal quand il y en a une
    (le détail d'un message, la raison d'une validation, l'erreur d'un échec),
    le titre de la tâche sinon : l'issue **réussie** d'une tâche ne porte aucun
    détail (`bridge.evenements_depuis_step`), et une frise dont une ligne sur
    deux serait muette ne se lirait pas.

    `rang` est l'ordre d'arrivée au journal ; il **ne sort pas** en JSON, comme
    dans `EntreeJournal` et pour la même raison : c'est la clé de départage du
    tri, que l'identifiant ne peut pas assurer.
    """

    rang: int
    id: str
    type: str
    couloir: str = COULOIR_REPLI
    agent: str = ""
    role: str = ""
    tache_id: str = ""
    titre: str = ""
    statut: str = ""
    objet: str = ""
    horodatage: str = ""

    @classmethod
    def depuis(cls, entree: EntreeJournal) -> EntreeFrise:
        """L'entrée de frise que porte cette entrée de journal."""
        return cls(
            rang=entree.rang,
            id=entree.id,
            type=entree.type,
            couloir=_couloir_de(entree.agent),
            agent=entree.agent,
            role=entree.role,
            tache_id=entree.tache_id,
            titre=entree.titre,
            statut=_statut_de(entree),
            objet=entree.detail or entree.titre,
            horodatage=entree.horodatage,
        )

    @property
    def cle_tri(self) -> tuple[str, int]:
        """La clé de tri : l'instant, départagé par le rang d'arrivée au journal."""
        return (self.horodatage, self.rang)

    def to_dict(self) -> dict[str, Any]:
        """La forme JSON d'une entrée (`EntreeFrise`, apps/web/lib/types.ts)."""
        return {
            "id": self.id,
            "type": self.type,
            "couloir": self.couloir,
            "agent": self.agent,
            "role": self.role,
            "tache_id": self.tache_id,
            "titre": self.titre,
            "statut": self.statut,
            "objet": self.objet,
            "horodatage": self.horodatage,
        }


@dataclass(frozen=True)
class CouloirFrise:
    """Un couloir de la frise : un agent et les entrées qui lui reviennent.

    `entrees` ne porte que des **identifiants** : les entrées elles-mêmes vivent
    une fois, dans la frise à plat, et les recopier ici doublerait la charge
    utile pour rien. C'est la mécanique des `niveaux` du graphe (#490), et elle a
    la même vertu : un client dessine les couloirs sans regrouper lui-même, et un
    client qui ne veut que la chronologie ignore ce champ.

    Un couloir **vide** est un couloir légitime : un agent du run qui n'a encore
    rien dit se lit comme tel, là où l'omettre le ferait apparaître en cours de
    route sans qu'on sache s'il était prévu.

    `activite` (#836) est le **signe de vie** du couloir : le dernier geste de
    son agent sur une tâche qui travaille, daté et abrégé — None quand aucune
    ne travaille. C'est un attribut de l'en-tête et non une entrée : rien de ce
    qu'il porte ne figure dans `entrees`, ni dans la frise à plat.
    """

    agent: str
    role: str = ""
    entrees: tuple[str, ...] = ()
    activite: SigneDeVie | None = None

    @property
    def repli(self) -> bool:
        """Ce couloir est-il celui du repli (aucun agent résoluble) ?"""
        return self.agent == COULOIR_REPLI

    def to_dict(self) -> dict[str, Any]:
        """La forme JSON d'un couloir (`CouloirFrise`, apps/web/lib/types.ts)."""
        return {
            "agent": self.agent,
            "role": self.role,
            "repli": self.repli,
            "entrees": list(self.entrees),
            # `null` sur un couloir dont aucune tâche ne travaille (#836) : un
            # client qui ignore la clé lit exactement la forme d'avant.
            "activite": self.activite.to_dict() if self.activite is not None else None,
        }


@dataclass(frozen=True)
class FriseRun:
    """La frise d'un run : ses entrées triées, et les couloirs qui les rangent.

    `total` compte **avant** le plafond ; `tronquee` dit s'il a mordu. Les deux
    sont servis plutôt que déduits, pour la règle du dépôt qui interdit une
    borne muette : une frise qui rendrait ses 500 dernières lignes sans le dire
    se lirait comme un run de 500 lignes.
    """

    run_id: str
    entrees: tuple[EntreeFrise, ...] = ()
    couloirs: tuple[CouloirFrise, ...] = ()
    total: int = 0
    plafond: int = PLAFOND_FRISE

    @property
    def tronquee(self) -> bool:
        """La frise a-t-elle laissé des entrées plus anciennes de côté ?"""
        return self.total > len(self.entrees)

    def to_dict(self) -> dict[str, Any]:
        """La forme JSON de la frise (`GET /api/executions/{run_id}/frise`)."""
        return {
            "run_id": self.run_id,
            "entrees": [entree.to_dict() for entree in self.entrees],
            "couloirs": [couloir.to_dict() for couloir in self.couloirs],
            "total": self.total,
            "plafond": self.plafond,
            "tronquee": self.tronquee,
        }


@dataclass
class _Couloir:
    """Un couloir en cours de construction (rôle à confirmer, entrées à empiler)."""

    agent: str
    role: str = ""
    entrees: list[str] = field(default_factory=list)
    activite: SigneDeVie | None = None


def frise_du_run(
    run_id: str,
    entrees: Iterable[EntreeJournal],
    *,
    agents: Mapping[str, str] | None = None,
    activites: Mapping[str, SigneDeVie] | None = None,
    plafond: int = PLAFOND_FRISE,
) -> FriseRun:
    """Assemble la frise du run `run_id` depuis des entrées de journal.

    `entrees` est ce que le journal requêtable garde du run — l'appelant a déjà
    filtré sur le `run_id` ; ce qui n'est pas de la frise (`TYPES_FRISE`) est
    écarté ici, une fois, plutôt que par chaque appelant.

    `agents` déclare les couloirs **attendus** (agent → rôle) dans l'ordre où le
    run les a employés : la projection les tient de ses tâches, et c'est ce qui
    ouvre un couloir pour un agent qui n'a encore rien dit. Tout agent rencontré
    dans les entrées et absent de cette table ouvre le sien à la suite — la
    déclaration ordonne, elle ne filtre pas : un couloir manquant ferait perdre
    des entrées, ce que le critère interdit.

    `activites` (#836) porte le **signe de vie** de chaque agent qui travaille
    (agent → signe), tel que la projection l'a déjà tranché
    (`ControlTowerState.signes_de_vie_du_run`). Il se pose sur le couloir de
    l'agent — déclaré ou découvert — et **n'ouvre jamais** de couloir à lui
    seul : un agent qui travaille a une tâche `en_cours`, donc un `:debut`
    consigné, donc son couloir ; un signe sans couloir dirait « ça bouge » de
    quelqu'un qui n'est pas dans le run. Le repli n'en porte pas : il n'a pas
    d'agent, donc pas de tâche qui travaille.

    L'ordre des couloirs est donc : les agents déclarés, puis les agents
    découverts dans l'ordre chronologique, puis le **repli** en dernier s'il a
    quelque chose — un couloir de repli vide n'a rien à montrer, et il n'ouvre
    que s'il a recueilli au moins une entrée, ce qui est la forme testable de
    « aucune entrée n'est jamais perdue faute de couloir ».

    `plafond` borne le nombre d'entrées rendues, les plus **récentes** d'abord
    servies ; les couloirs ne référencent alors que ce qui est rendu, faute de
    quoi ils désigneraient des identifiants absents de la frise.
    """
    retenues = sorted(
        (EntreeFrise.depuis(entree) for entree in entrees if entree.type in TYPES_FRISE),
        key=lambda entree: entree.cle_tri,
    )
    total = len(retenues)
    if plafond >= 0 and total > plafond:
        # La queue, pas la tête : « pendant qu'ils le font » se lit par la fin.
        retenues = retenues[total - plafond :]
    return FriseRun(
        run_id=run_id,
        entrees=tuple(retenues),
        couloirs=_couloirs(retenues, agents or {}, activites or {}),
        total=total,
        plafond=plafond,
    )


def _couloirs(
    entrees: Sequence[EntreeFrise],
    agents: Mapping[str, str],
    activites: Mapping[str, SigneDeVie],
) -> tuple[CouloirFrise, ...]:
    """Range `entrees` (déjà triées) en couloirs — déclarés d'abord, repli en dernier."""
    couloirs: dict[str, _Couloir] = {}
    for nom, role in agents.items():
        # Les noms déclarés passent par la **même** normalisation que ceux des
        # entrées, sans quoi un agent déclaré « developpeur » avec un espace
        # ouvrirait un couloir que ses propres entrées ne rejoindraient jamais.
        cle = _couloir_de(nom)
        if cle != COULOIR_REPLI:
            couloirs.setdefault(cle, _Couloir(agent=cle, role=role))
    repli = _Couloir(agent=COULOIR_REPLI)
    for entree in entrees:
        if entree.couloir == COULOIR_REPLI:
            repli.entrees.append(entree.id)
            continue
        couloir = couloirs.get(entree.couloir)
        if couloir is None:
            couloir = couloirs[entree.couloir] = _Couloir(agent=entree.couloir)
        # Le rôle vient de la première entrée qui en porte un : la déclaration
        # peut l'ignorer (un agent connu par ses seules entrées), et une entrée
        # de blocage n'en porte pas de significatif.
        if not couloir.role and entree.role:
            couloir.role = entree.role
        couloir.entrees.append(entree.id)
    for nom, signe in activites.items():
        # Même normalisation que les entrées et les déclarations ; un signe qui
        # ne trouve pas son couloir est écarté plutôt que d'en ouvrir un (cf.
        # `frise_du_run`), et le repli n'en reçoit jamais.
        couloir = couloirs.get(_couloir_de(nom))
        if couloir is not None and signe.plus_recent_que(couloir.activite):
            couloir.activite = signe
    rangs = [
        CouloirFrise(
            agent=c.agent, role=c.role, entrees=tuple(c.entrees), activite=c.activite
        )
        for c in couloirs.values()
    ]
    if repli.entrees:
        rangs.append(CouloirFrise(agent=repli.agent, entrees=tuple(repli.entrees)))
    return tuple(rangs)
