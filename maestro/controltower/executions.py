"""Pilotage des exécutions depuis la Control Tower — lancer, suivre, annuler (#185).

L'API exposait l'**état** d'une orchestration sans savoir en **déclencher** une :
un run se lançait en ligne de commande (`maestro-run`), ce qui interdisait à la
Control Tower d'être un vrai poste de pilotage. Ce module est la pièce
manquante — le service que les routes `/api/executions` appellent :

- `lancer` valide les garde-fous (#9) du run, le confie à l'**hôte** du service
  — depuis #446, un **process détaché** dans le déploiement de production — et
  rend son résumé (donc son `run_id`) immédiatement ;
  depuis #317 il **compose** aussi la matière de l'objectif : sources résolues
  (#315), octets téléversés rattachés au run, rapport de lecture (#316) rendu
  avec le résumé ;
- `resumes`/`resume` lisent la **projection** (`ControlTowerState`) : le service
  ne tient pas de second registre des runs, il lit celui qui existe déjà ;
- `annuler` interrompt un run en vol et consigne l'issue — **et solde ce que ce
  run portait** (#466) : ses tâches non terminées passent `echec`, donc les agents
  qui les tenaient redeviennent `libre`. Ce n'était pas le cas avant, et pas
  seulement à l'écran : la projection se reconstruit par rejeu du journal, si bien
  qu'une tâche laissée `en_cours` par une annulation l'était **pour toujours**, son
  agent compris. Le geste vit dans `_solder`, donc la relance (#349) en hérite ;
  `_ramasser` le rejoue pour l'autre façon dont un run meurt sans écrire l'issue
  de ses tâches.

Depuis #320 un run lancé d'ici **s'arrête sur son brief** par défaut (décision D5,
mode `humain`) : `en_attente_brief` dans la projection, aucune tâche créée, et la
décision rendue par `POST /api/executions/{run_id}/brief/decision`. C'est le seul
point d'entrée du dépôt où ce défaut s'applique, et pour une raison qui ne se
généralise pas : la Control Tower est la voie de lancement qui a quelqu'un devant.
Un run suspendu reste **annulable** — attendre une approbation n'est pas un état
terminal —, et un refus le solde en « annulée », pas en « échec ».

**Frontière d'exécution retenue : un hôte de run détaché** (docs/28 §5, livré par
le chantier #441 et basculé en défaut par #446). Le POC avait retenu la tâche de
fond du process de l'API, pour de bonnes raisons — annulation *à portée*
(`asyncio.Task.cancel`, là où interrompre un run déjà distribué demanderait un
protocole de révocation côté workers) et aucune dépendance d'infrastructure
ajoutée à `maestro-api` —, et l'hôte détaché ne repaie ni l'une ni l'autre :
l'annulation reste `Task.cancel`, à un aller Redis près (#444), et le process fils
est le même interpréteur dans le même `.venv`. Ce qu'il supprime est la panne de
chaque heure : fermer la fenêtre du navigateur, relancer l'API, `start.sh --stop`
n'arrête plus le run.

Le corollaire n'a pas disparu, il a **changé de portée** : un run survit à son
API, **pas à sa machine** — sa trace, elle, survit aux deux (journal durable #97).
La tâche de fond reste disponible et se nomme (`MAESTRO_HOTE_RUN=process`) ; le
contrat REST ne dépend d'aucun des deux.

Depuis #442, cette frontière a un **nom** et n'est plus un détail
d'implémentation du service : `maestro.controltower.hote` porte le contrat
d'hôte de run — lancer, annuler, observer —, et `HoteRunEnProcess` en est
l'unique implémentation, celle que ce module appliquait lui-même. Rien n'a
changé de ce que fait un run ; ce qui a changé, c'est que la phrase « un run est
une tâche de ce process » ne se lit plus qu'à un endroit, au lieu d'être répandue
dans le lancement, l'annulation, la fermeture, le cœur et la question « ce run
est-il en vol ? ». C'est le seam sur lequel un hôte **détaché** se branchera
(#443) sans réécrire ni les routes, ni les événements, ni la projection — et le
service, lui, ne tient plus aucune tâche : il demande à son hôte ce qu'il porte.

Cet hôte détaché existe depuis #443 (`maestro.controltower.hote_detache`), et
**l'annulation le rejoint depuis #444** — sans que ce module ait une ligne de plus
à ce sujet, ce qui est la propriété qu'on cherchait. L'issue que `_solder` consigne
part sur le bus comme le reste ; le process détaché la guette, y reconnaît la fin
de *son* run et annule sa propre tâche `asyncio`. Deux conséquences pour qui relit
ce fichier : la publication de cette issue n'est plus seulement une trace, c'est
**l'ordre lui-même** (cf. `_solder`), et l'ordre des deux gestes — consigner, puis
demander l'interruption — n'est plus seulement juste, il est *nécessaire*.

Le service n'en sait toujours rien d'autre que **deux** choses, et les deux
portent sur la **fin de vie** d'un hôte plutôt que sur son travail :

- **un hôte peut rater son départ**. Créer une tâche de fond n'échoue pas ; créer
  un process, si. Ce qu'il en fait est écrit une fois, dans `lancer` — le run est
  soldé `echec` avec sa cause, parce qu'à cet instant plus rien ne viendra de
  l'hôte et que l'appelant est le seul encore là pour l'écrire ;
- **un hôte peut mourir sans dire son issue** (#446). Un hôte qui part
  normalement la publie désormais lui-même (`bridge.solder_le_run`), donc ce qui
  reste muet est ce qui est mort net. `_ramasser` confronte alors ce que l'hôte a
  vu mourir (`HoteRun.ramasser`, un constat) à la projection (l'issue, s'il y en a
  une) et solde le reste avec sa cause. Le verdict d'orphelinat, lui, n'est **pas
  redéduit** ici : `vitalite` en est la seule formule, comme pour `relancer`.

Ce dispositif n'est pas toute l'histoire (#347) : depuis #348 la mort de l'hôte se
**voit**, et depuis #349 ce qu'elle emporte se **rattrape**. L'inventaire, parce
que c'est lui qu'on cherche quand un run vient de disparaître — et il se lit
maintenant sur la mort de la **machine**, l'arrêt de l'API ne coûtant plus rien :

- **ne survit pas** — le process du run et le travail qu'il avait en cours, le
  cœur qui battait pour lui, et les événements encore en file de publication au
  moment du coup d'arrêt (ils n'atteignent alors ni le bus, ni le journal). Un run
  orphelin n'est plus **interruptible** non plus : plus aucun process ne porte sa
  tâche, si bien qu'`annuler` ne fait plus que consigner son issue ;
- **survit** — tout ce qui est passé par le bus avant l'arrêt : objectif, statut,
  tâches, coûts, ticket, projet, sources, **brief** et le fait qu'un humain l'ait
  approuvé (`brief_approuve`). Et le **dernier battement**, qui n'est effacé que
  par un soldage : il vieillit, et c'est ce vieillissement qui transforme un
  `en_cours` éternel en `orphelin` ;
- **on rattrape par** `relancer` (ci-dessous), qui rejoue le cadrage approuvé dans
  un nouveau run. Ce qui ne se rattrape **pas**, c'est le travail des tâches déjà
  faites : le run repart de la décomposition, jamais de sa tâche 3. Reprendre à
  l'endroit exact de l'interruption suppose une frontière d'exécution durable, et
  fait l'objet d'un cadrage à part (#350).

Un run publié hors de l'API (`maestro-run --publier`) lit cette liste à l'envers,
et c'est le seul cas qui s'y ajoute : aucun redémarrage de l'API ne le concerne —
il vit dans son process et y bat. Sa fin **normale**, elle, n'est plus muette
depuis #446 : `--publier` fait de ce process l'hôte du run, et un hôte publie son
issue en partant.

**Le flux d'événements existant reste le seul canal.** Le run est journalisé
comme n'importe quel autre (`RunJournal`) et le pont télémétrie → bus
(`maestro.controltower.bridge`) transforme chaque étape en événement : les tâches
apparaissent au Kanban et sur le WebSocket sans que ce module ait à les
connaître. Il n'y ajoute que le **cycle de vie du run lui-même**
(`execution.statut` : lancement, issue, annulation), que le journal ne dit pas —
il ne parle que de tâches.

Deux précautions dans ce câblage :

- le pont se pose sur un logger **propre au service** (`maestro.trace.<...>`),
  jamais sur le logger global `maestro.trace` : un handler global survivrait au
  service qui l'a posé et republierait les runs de tout le process (#195) ;
- la publication passe par une **file drainée par une seule tâche**, parce que
  le pont publie de façon *synchrone* (c'est un handler `logging`) alors que le
  bus est asynchrone : l'ordre des événements est ainsi préservé jusqu'au bus,
  et rien n'est publié depuis un contexte annulé.

Depuis #348 le service **fait battre le cœur** de ses runs (`maestro.controltower
.battement`) : une tâche unique pose un battement pour chaque run en vol, et
`resumes` rend le verdict de vitalité — vivant, orphelin, indéterminé — à côté du
statut. C'est la contrepartie visible du corollaire ci-dessus : un run ne survit
toujours pas au sommeil de sa machine, mais sa mort cesse d'être silencieuse. La
même tâche porte depuis #446 le **ramassage** (`_ramasser`), pour la raison qui a
fait mettre le battement là : ce qui doit être fait à chaque période l'est par un
seul réveil, jamais par une tâche d'horloge de plus.

Et depuis #349 elle cesse d'être irrattrapable : `relancer` rejoue un run non
soldé **sur son brief approuvé**, en mode `sans`, sans repasser par la
clarification ni par la validation. Ce qu'un run mort emporte n'est pas du temps
machine mais un cadrage validé par un humain — 2,52 $ et une vingtaine de minutes
d'attention sur le run du 2026-08-14 —, et ce cadrage est intégralement conservé
dans la projection. Le run relancé est un **nouveau** run, qui porte la référence
de celui qu'il reprend (`reprise_de`, même relation que le fichier `reprise-de`
entre deux runs d'orchestration, #204) ; le run repris est soldé au lieu de rester
`en_cours`. Ce n'est **pas** une reprise à l'endroit exact de l'interruption :
celle-là suppose une frontière d'exécution durable, et fait l'objet d'un cadrage à
part (#350).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from maestro.appartenance import projet_id_valide
from maestro.controltower.battement import (
    PERIODE_BATTEMENT_S,
    SEUIL_ORPHELIN_S,
    VITALITE_VIVANT,
    RegistreBattements,
    RegistreBattementsMemoire,
    vitalite,
)
from maestro.controltower.bridge import JournalEventHandler
from maestro.controltower.brief import (
    ArbitreBriefControlTower,
    ArbitreClarificationControlTower,
)
from maestro.controltower.causes import CAUSE_ANNULATION, cause_de, detail_avec_cause
from maestro.controltower.events import (
    ACTEUR_RUN,
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_TACHE_STATUT,
    ROLE_RUN,
    Event,
    EventBus,
)
from maestro.controltower.hote import DemarrageHoteRate, HoteRun, OrdreRun
from maestro.controltower.hote_en_process import HoteRunEnProcess
from maestro.controltower.portee import PorteeProjet, PorteeRun
from maestro.controltower.state import (
    EXECUTION_ANNULEE,
    EXECUTION_ECHEC,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    ORDRE_PAUSE,
    ORDRE_REPRISE,
    STATUTS_EXECUTION_TERMINAUX,
    STATUTS_TACHE_TERMINAUX,
    ControlTowerState,
)
from maestro.controltower.validation import ValidateurControlTower
from maestro.engine.brief import (
    MODE_BRIEF_HUMAIN,
    MODE_BRIEF_SANS,
    BriefRefuse,
    mode_brief_valide,
)
from maestro.engine.executor import STATUT_ECHEC
from maestro.engine.guardrails import GardeFousIngestion, Guardrails
from maestro.engine.pause import PorteExecution
from maestro.references import ReferenceTicket
from maestro.sources import (
    DepotTeleversements,
    RapportLecture,
    Source,
    declarer_televersements,
    extraire_sources,
    resoudre_sources,
)
from maestro.telemetry import LOGGER_NAME, RunJournal, redact_secrets

if TYPE_CHECKING:
    from maestro.engine.loop import OrchestrationEngine

#: Fabrique du moteur d'un run : appelée avec les garde-fous du lancement et le
#: plafond de parallélisme. Injectable (`create_app`) — les tests y passent un
#: moteur factice, aucun fournisseur n'étant résolu tant qu'aucun run ne part.
FabriqueMoteur = Callable[..., "OrchestrationEngine"]

#: Lecture de la matière d'un objectif : des sources **résolues** au rapport de
#: lecture (#316). Injectable pour la même raison que la fabrique du moteur — une
#: source `url` part sur le réseau, et `tests/conftest.py` (#195) exige qu'aucun
#: test n'en ait besoin. Un seul point d'injection plutôt que trois réglages :
#: desserrer les plafonds d'extraction se fait par un
#: `partial(extraire_sources, garde_fous=…)`.
LecteurSources = Callable[[Sequence[Source]], RapportLecture]

#: Délai laissé à un run annulé pour s'éteindre avant que la requête d'annulation
#: rende la main. Au-delà, l'issue est déjà consignée et la tâche est abandonnée
#: à son extinction — même parti pris que `maestro.engine.runner` : une
#: réalisation qui avale son annulation ne doit pas suspendre l'appelant.
DELAI_ANNULATION_S: float = 5.0

#: Motifs de refus d'une **relance** (#349) — codes stables, à la convention des
#: refus de sources et de projets (`{motif, message}`, cf. `detail_refus`). Ils
#: disent *pourquoi* la relance n'a pas eu lieu, et c'est la route qui les traduit
#: en code HTTP : le service, lui, n'a pas à connaître les codes de statut.
#:
#: Les trois premiers sont les refus **du ticket** ; le quatrième est sa note
#: technique — « un run sans brief approuvé n'a rien à rejouer : le dire plutôt que
#: de relancer sur l'objectif brut en silence ».
MOTIF_RELANCE_RUN_INCONNU = "run-inconnu"
MOTIF_RELANCE_RUN_SOLDE = "run-solde"
MOTIF_RELANCE_RUN_VIVANT = "run-vivant"
MOTIF_RELANCE_SANS_CADRAGE = "cadrage-absent"

_LOGGER = logging.getLogger("maestro.controltower")


class RelanceRefusee(ValueError):
    """Une relance refusée, **avec son motif** — jamais un rejet muet (#349).

    Même contrat que `SourceRefusee` (#315) et même raison d'hériter de
    `ValueError` : un refus de relance est une requête invalide, pas une panne, et
    `_detail_refus` sait déjà en tirer le corps `{motif, message}` que servent les
    routes. `motif` est l'un des `MOTIF_RELANCE_*` ; le message reste la phrase
    lisible, celle que l'UI affiche telle quelle.
    """

    def __init__(self, motif: str, message: str) -> None:
        super().__init__(message)
        self.motif = motif


def moteur_par_defaut(**reglages: Any) -> OrchestrationEngine:
    """La fabrique de production : le moteur par défaut (#6), fournisseur de la config.

    Import local : construire l'app ne doit pas résoudre de fournisseur — c'est
    le premier lancement qui le fait, jamais le démarrage de l'API.
    """
    from maestro.engine.loop import OrchestrationEngine

    return OrchestrationEngine.default(**reglages)


class ServiceExecutions:
    """Lance, suit et interrompt les exécutions pilotées par l'API (#185).

    `bus` et `state` sont ceux de l'app : les événements du run partent sur le
    bus (donc au WebSocket et au journal durable) et les résumés se lisent dans
    la projection — une seule source de vérité, celle qui survit déjà au
    redémarrage. `fabrique_moteur` construit le moteur de chaque run (par défaut
    `OrchestrationEngine.default`). `garde_fous_ingestion` (#315) plafonne la
    matière d'entrée des objectifs — actif par défaut, réglable par injection :
    c'est un réglage de déploiement, pas un choix du client qui lance un run.

    `televersements` (#317) est le dépôt où `POST /api/sources` a posé les octets
    d'un fichier : le service y retrouve par identifiant ce qu'un lancement
    déclare, et **rattache** la matière au run une fois son emplacement connu.
    `lecteur_sources` (#316) produit le rapport de lecture rendu avec le résumé.

    `battements` (#348) est le registre où l'hôte d'un run pose son signal de vie
    — par défaut un registre **mémoire**, qui suffit tant qu'un seul process lance
    et lit ; la production câble `RegistreBattementsRedis` via `create_app`, seule
    façon de voir battre un run publié par un autre process. `seuil_orphelin_s`
    est le silence au-delà duquel un run non soldé est déclaré orphelin (défaut :
    `SEUIL_ORPHELIN_S`) ; `periode_battement_s` l'intervalle entre deux
    battements. Les deux sont injectables **pour les tests**, pas comme réglage de
    déploiement : y toucher est un choix de code, comme `DELAI_ANNULATION_S`.

    `hote` (#442) est **l'hôte de run** : ce à quoi le service confie une
    exécution, et à qui il ne demande que de la lancer, de l'annuler, de dire ce
    qu'il porte encore et ce qu'il a vu mourir (`maestro.controltower.hote`).
    Injectable comme `fabrique_moteur`, et pour la même raison : le service ne doit
    rien savoir de *où* le run s'exécute, condition pour qu'un hôte survivant à
    l'API (#441) s'y branche sans réécrire ni les routes, ni les événements, ni la
    projection.

    ⚠ Son défaut n'est **pas** celui du déploiement, et les deux se lisent à des
    endroits différents depuis #446. Ici, faute d'hôte injecté, c'est
    `HoteRunEnProcess` : le seul à qui l'on puisse passer une coroutine, donc le
    seul qu'une app puisse se donner sans avoir de process à fabriquer — c'est ce
    que veulent les tests et une démo mono-process. Le défaut de **production**,
    lui, est l'hôte détaché, et il se résout là où se résolvent le bus, le journal
    et le registre (`create_default_app`, `MAESTRO_HOTE_RUN`).
    """

    def __init__(
        self,
        bus: EventBus,
        state: ControlTowerState,
        *,
        fabrique_moteur: FabriqueMoteur | None = None,
        garde_fous_ingestion: GardeFousIngestion | None = None,
        televersements: DepotTeleversements | None = None,
        lecteur_sources: LecteurSources | None = None,
        battements: RegistreBattements | None = None,
        seuil_orphelin_s: float = SEUIL_ORPHELIN_S,
        periode_battement_s: float = PERIODE_BATTEMENT_S,
        hote: HoteRun | None = None,
    ) -> None:
        self._bus = bus
        self._state = state
        self._fabrique = fabrique_moteur if fabrique_moteur is not None else moteur_par_defaut
        self._ingestion = (
            garde_fous_ingestion if garde_fous_ingestion is not None else GardeFousIngestion()
        )
        self._televersements = (
            televersements
            if televersements is not None
            else DepotTeleversements.default(garde_fous=self._ingestion)
        )
        self._lecteur = lecteur_sources if lecteur_sources is not None else extraire_sources
        self._battements = (
            battements if battements is not None else RegistreBattementsMemoire()
        )
        self._seuil_orphelin_s = seuil_orphelin_s
        self._periode_battement_s = periode_battement_s
        self._coeur: asyncio.Task[None] | None = None
        # L'hôte des runs (#442) — leur seul registre : le service ne tient plus
        # aucune tâche, il demande à l'hôte ce qu'il porte. Par défaut celui en
        # process, construit autour du déroulement de ce service : c'est le seul
        # hôte qui puisse recevoir une coroutine, et le contrat le dit en ne
        # passant à `lancer` que des données.
        self._hote = hote if hote is not None else HoteRunEnProcess(self._derouler)
        # Logger **propre à ce service** : le pont télémétrie s'y pose sans
        # toucher au logger global du journal (cf. docstring du module). Les
        # lignes remontent quand même à `maestro.trace` par propagation — un
        # export Langfuse (#81) posé sur lui les voit comme celles d'un run CLI.
        self._logger = logging.getLogger(f"{LOGGER_NAME}.controltower.{uuid.uuid4().hex[:8]}")
        self._logger.setLevel(logging.INFO)
        self._handler: JournalEventHandler | None = None
        self._file: asyncio.Queue[Event] | None = None
        self._pompe: asyncio.Task[None] | None = None
        self._boucle: asyncio.AbstractEventLoop | None = None
        # Les portes des runs que **ce process** déroule (#477), c'est-à-dire ceux
        # de l'hôte en process et d'eux seuls. Un run détaché n'en a pas ici : sa
        # porte vit dans son process, et l'ordre l'y rejoint par le bus. D'où deux
        # chemins pour un même geste, et c'est la conséquence directe d'avoir deux
        # hôtes — ce que le service assume déjà pour l'annulation, où l'issue
        # publiée sert d'ordre au détaché pendant que `HoteRunEnProcess.annuler`
        # fait un `Task.cancel()` de la main à la main.
        self._portes: dict[str, PorteExecution] = {}

    # ------------------------------------------------------------------ lecture

    async def resumes(self, portee: PorteeProjet | None = None) -> list[dict[str, Any]]:
        """Les runs connus (`GET /api/executions`) : résumés, **récents d'abord**.

        Tous ceux dont la projection porte une trace — lancés par l'API comme
        publiés par un autre process (`maestro-run --publier`) : le suivi ne
        distingue pas leur origine. `portee` (#277) est le périmètre de la
        lecture, celui-là même qu'appliquent le Kanban et les coûts ; `None`
        rend tout, comme la projection qu'il ne fait que traverser.

        Chaque résumé porte en plus sa **vitalité** (#348) — `vivant`, `orphelin`
        ou `indetermine` sur un run non soldé, `null` sinon. La méthode est
        devenue asynchrone pour cette seule raison : le verdict se lit dans le
        registre des battements, qui vit hors du process en production, et **un
        seul** aller-retour le rend pour tous les runs de la liste.

        Et sa **progression** (#473), posée par `resume` ci-dessous : c'est ce
        qui rend la liste utile sans un appel par ligne — état, objectif,
        progression, début et coût y sont désormais tous, de quoi dresser la
        liste des runs d'un projet d'une seule lecture.
        """
        connus = await self._registre()
        resumes = [
            self._avec_vitalite(self._avec_progression(e.resume()), connus)
            for e in self._state.executions(portee)
        ]
        return sorted(resumes, key=lambda r: str(r["debut"]), reverse=True)

    def resume(self, run_id: str) -> dict[str, Any] | None:
        """Le résumé du run `run_id`, ou None s'il est inconnu de la projection.

        **Sans** verdict de vitalité, à dessein : c'est la lecture interne du
        service (annulation, décision de brief, contrôles 404/409 des routes),
        dont aucun appelant n'a besoin d'interroger un registre distant. La vue
        publique d'un run, elle, passe par `resume_vivant`.

        **Avec** sa progression (#473), en revanche, et c'est la même raison lue
        dans l'autre sens : elle se compte dans la projection déjà en mémoire,
        donc rien ne justifie de la réserver aux vues publiques.
        """
        execution = self._state.execution(run_id)
        return None if execution is None else self._avec_progression(execution.resume())

    async def resume_vivant(self, run_id: str) -> dict[str, Any] | None:
        """Le résumé du run `run_id`, **vitalité comprise** (#348) — None s'il est inconnu.

        Le pendant unitaire de `resumes`, pour `GET /api/executions/{run_id}` :
        une liste qui saurait dire qu'un run est orphelin pendant que l'écran de
        ce run ne le saurait pas serait une couture, pas une économie.
        """
        resume = self.resume(run_id)
        if resume is None:
            return None
        return self._avec_vitalite(resume, await self._registre())

    def _avec_progression(self, resume: dict[str, Any]) -> dict[str, Any]:
        """Ajoute la progression par statut de tâche à un résumé, sans toucher au reste.

        Même patron que `_avec_vitalite`, mais **synchrone et gratuit** : le
        compte se lit dans la projection déjà en mémoire, là où la vitalité
        demande un registre qui vit hors du process. C'est ce qui permet de la
        poser dès `resume`, donc sur *toutes* les réponses qui portent un run —
        liste, détail, lancement, annulation, relance — au lieu des seules vues
        publiques.

        Elle est posée **ici** et non dans `EtatExecution.resume()`, pour la
        raison qui y garde la vitalité au-dehors : un run ne sait pas compter ses
        propres tâches. Ce qu'il connaît, ce sont ses événements ; le statut
        courant d'une tâche appartient à la projection, et le recopier dans le
        run en ferait une seconde vérité à tenir d'accord.
        """
        return {
            **resume,
            "progression": self._state.progression(str(resume["run_id"])).to_dict(),
        }

    def _avec_vitalite(
        self, resume: dict[str, Any], battements: Mapping[str, str]
    ) -> dict[str, Any]:
        """Ajoute le verdict de vitalité à un résumé, sans toucher au reste."""
        return {
            **resume,
            "vitalite": vitalite(
                str(resume["statut"]),
                battements.get(str(resume["run_id"])),
                seuil_s=self._seuil_orphelin_s,
            ),
        }

    async def _registre(self) -> Mapping[str, str]:
        """Les battements connus — **jamais une levée** : un registre muet dit `{}`.

        Un Redis injoignable ne doit pas faire échouer `GET /api/executions` : la
        liste des runs est ce qu'on regarde *quand quelque chose ne va pas*.
        Faute de battements, tous les runs non soldés ressortent `indetermine`,
        ce qui est la vérité — on ne sait pas.
        """
        try:
            return await self._battements.battements()
        except Exception:
            _LOGGER.exception(
                "Lecture des battements impossible : les runs en cours sont rendus "
                "« indetermine » (leur vitalité est inconnue, pas leur statut)."
            )
            return {}

    def en_vol(self, run_id: str) -> bool:
        """Le run est-il porté par l'hôte de ce service, et encore en cours ?

        La question porte sur l'**hôte**, jamais sur le statut : un run peut être
        soldé dans la projection pendant que son exécution s'éteint encore, et
        c'est même la fenêtre que le cœur (#348) doit savoir distinguer.
        """
        return self._hote.en_vol(run_id)

    # ----------------------------------------------------------------- écriture

    async def lancer(
        self,
        objectif: str,
        *,
        plafond_cout_usd: float | None = None,
        plafond_tokens: int | None = None,
        timeout_tache_s: float | None = None,
        parallelisme: int | None = None,
        ticket: Mapping[str, str] | ReferenceTicket | None = None,
        projet_id: str | None = None,
        sources: Sequence[Mapping[str, Any] | Source] | None = None,
        mode_brief: str | None = MODE_BRIEF_HUMAIN,
        reprise_de: str = "",
    ) -> dict[str, Any]:
        """Confie une exécution à l'hôte et rend son résumé **immédiatement**.

        L'hôte (#442) est celui du service — par défaut la tâche de fond du
        process de l'API, comme depuis #185. Ce que la méthode fait avant de la
        lui confier ne change pas selon l'hôte : les refus, la matière, l'événement
        de lancement et le premier battement sont écrits ici, une bonne fois.

        Les garde-fous (#9) sont ceux du lancement : plafonds de coût et de
        tokens, time-out par tâche, plafond de parallélisme — chacun optionnel
        (None : le défaut du moteur). La validation humaine d'une action sensible
        passe par la Control Tower elle-même (#48) : la demande apparaît dans
        `GET /api/validations` et le run reprend sur la décision.

        `ticket` (#187) rattache le run au **ticket dont il part** :
        elle est portée par l'événement de lancement (donc par la projection, et
        donc elle survit au redémarrage) et **héritée par chaque tâche du plan**,
        jusqu'à la carte du Kanban. Le service ne fait que la transmettre : ni
        lui ni le moteur ne savent de quel outil de ticketing elle vient.

        `projet_id` (#222) rattache le run au **projet dans lequel il
        travaille** : même chemin que le ticket — porté par l'événement de
        lancement, donc par la projection, et hérité par chaque tâche du plan.
        Un identifiant qui n'en est pas un est écarté plutôt que refusé (le run
        part alors sans projet, comme avant ce lot) ; l'existence du projet, elle,
        se vérifie à la saisie, côté API des projets (#223). Tant que l'espace de
        travail dérivé (#224) n'est pas là, le champ **rattache sans isoler** :
        il ne change rien à ce que le run peut lire ou écrire.

        `sources` (#315, EF-39) porte la **matière** de l'objectif — fichiers
        téléversés, dossier de références, URL. Elles sont **résolues ici**,
        c'est-à-dire canonicalisées, confrontées aux racines interdites (EF-38)
        et plafonnées (`GardeFousIngestion`, ENF-07) **avant** que le run ne
        parte : refuser après coup reviendrait à annuler un run déjà lancé.
        Elles rejoignent ensuite la projection par l'événement de lancement,
        comme le ticket et le projet. `sources=None` laisse le lancement
        identique à celui d'avant la Phase 8.

        Depuis #317, deux gestes s'y ajoutent et c'est ce qui rend la méthode
        **asynchrone**. Les fichiers désignés par un identifiant de téléversement
        (`{"type": "fichier", "id": …}`, docs/05 §6.8) sont **rattachés** au run :
        leurs octets sont copiés du dépôt vers l'emplacement d'ingestion propre à
        ce run, seul endroit où l'extraction ira les chercher. Puis la matière est
        **lue** (#316), et le rapport de lecture — lu / ignoré / tronqué, coût
        estimé — est rendu sous la clé `rapport`, **à côté** du résumé.

        Deux conséquences à connaître. La lecture a lieu dans un **fil**
        (`asyncio.to_thread`) : elle ouvre des fichiers et peut récupérer une
        page, ce qui bloquerait la boucle de l'API. Et un lancement **avec**
        sources n'est plus instantané — il attend sa matière, faute de quoi le
        rapport n'aurait rien à dire ; sans source, rien ne change. Le rapport ne
        va **pas** dans l'événement de lancement : il décrit une lecture, pas un
        fait du run, et le rendre durable est le travail de la validation du
        brief (#320).

        `mode_brief` (#320, décision D5) est le **régime du brief** de ce run.
        Son défaut est `humain` — et c'est le seul endroit du dépôt où il l'est :
        la Control Tower est la voie de lancement qui a, par construction,
        quelqu'un devant. Le run s'arrête alors sur son brief
        (`en_attente_brief`), **aucune tâche n'est créée** et la décision se rend
        par `POST /api/executions/{run_id}/brief/decision`. `auto` rédige le brief
        et décompose sans attendre ; `sans` décompose l'objectif brut, comme avant
        ce lot. L'arbitre est câblé sur le bus de l'app, donc la demande atteint
        l'UI par le même canal que tout le reste.

        `reprise_de` (#349) est le run **dont celui-ci est la suite**, vide pour un
        lancement ordinaire. Il n'a aucun effet sur le déroulement : il voyage avec
        l'événement de lancement, rejoint donc la projection et survit au rejeu du
        journal durable, exactement comme le ticket et le projet. C'est `relancer`
        qui le pose — le renseigner depuis la route de lancement ferait dire « ceci
        est la suite de cela » sans que rien n'ait été repris.

        Lève `ValueError` sur un objectif vide, un garde-fou hors bornes (les
        plafonds sont des maximums : ils doivent être > 0), un mode de brief
        inconnu ou une source refusée (`SourceRefusee`, qui en hérite et porte son
        motif) — la route en fait un 422. Le run, lui, ne peut plus échouer de
        façon synchrone : ce qui lui arrive ensuite se lit dans son statut.

        Un **démarrage raté** de l'hôte (#443, `DemarrageHoteRate` — un process qui
        ne part pas, ou qui meurt aussitôt) ne fait pas exception à cette phrase :
        il est rattrapé ici et devient le **statut** du run, `echec` avec sa cause.
        Ce n'est pas une requête invalide — rien de ce que le client a demandé
        n'était fautif —, et le lancement rend donc son résumé comme les autres,
        déjà soldé. La seule chose qu'on refuse est de rendre un run `en_cours` que
        personne ne porte : il attendrait le seuil d'orphelinat pour s'éteindre.
        """
        objectif = objectif.strip()
        if not objectif:
            raise ValueError("objectif vide : il n'y a rien à orchestrer.")
        for nom, valeur in (
            ("plafond_cout_usd", plafond_cout_usd),
            ("plafond_tokens", plafond_tokens),
            ("timeout_tache_s", timeout_tache_s),
            ("parallelisme", parallelisme),
        ):
            if valeur is not None and valeur <= 0:
                raise ValueError(f"{nom} doit être > 0 (reçu : {valeur}).")
        # Refusé **avant** toute écriture, comme les garde-fous : un mode mal
        # orthographié ne doit pas laisser derrière lui un run inscrit dans la
        # projection — et surtout pas un run qui attendrait une décision que
        # personne ne sait qu'il attend.
        #
        # `None` retombe ici sur le défaut de **cette** voie d'entrée (`humain`) et
        # non sur celui de `mode_brief_valide` (`sans`, le défaut du moteur) : un
        # client qui envoie `"brief": null` demande la même chose que celui qui omet
        # la clé, et deux façons d'écrire « je ne précise pas » ne peuvent pas
        # donner deux régimes différents.
        regime_brief = mode_brief_valide(mode_brief or MODE_BRIEF_HUMAIN)

        reference = (
            ticket
            if isinstance(ticket, ReferenceTicket)
            else ReferenceTicket.depuis(ticket)
        )
        projet = projet_id_valide(projet_id)

        # Le `run_id` est tiré **avant** la résolution des sources, et non plus
        # juste avant le lancement : l'emplacement d'ingestion d'un fichier
        # téléversé est propre au run (#315), donc il faut le connaître pour
        # savoir où la matière atterrira. Tirer un identifiant n'a aucun effet de
        # bord — un lancement refusé plus bas le laisse simplement inutilisé.
        run_id = uuid.uuid4().hex[:12]
        # Refus **avant** toute écriture : une source hors bornes ou hors
        # périmètre ne doit pas laisser derrière elle un run inscrit dans la
        # projection, ni un événement sur le bus.
        matiere = self._composer(sources, run_id=run_id)
        # La lecture dans un fil : elle ouvre des fichiers et peut récupérer une
        # page (#316), ce que la boucle de l'API ne doit pas porter. Aucune
        # source, aucun fil — un lancement sans matière garde son coût d'avant.
        rapport = (
            await asyncio.to_thread(self._lecteur, matiere) if matiere else RapportLecture()
        )

        self._demarrer()
        # L'événement de lancement **avant** que l'hôte ne parte : la projection
        # connaît le run (donc le résumé rendu est complet) avant qu'une seule
        # étape ne puisse y arriver. C'est lui qui porte le ticket du run (#187)
        # et son projet (#222) : il part sur le bus, donc dans le journal
        # durable — plus rien n'est tenu en mémoire par ce service.
        self._consigne(
            run_id,
            EXECUTION_EN_COURS,
            objectif,
            "lancée depuis la Control Tower",
            ticket=reference,
            projet_id=projet,
            sources=matiere,
            mode_brief=regime_brief,
            reprise_de=reprise_de,
        )
        # Premier battement **avant** le départ chez l'hôte, pour la même raison que
        # l'événement de lancement l'a précédée : entre le moment où le run entre
        # dans la projection et son premier tour d'horloge, il serait lu
        # « indetermine » — c'est-à-dire indiscernable d'un run d'avant #348.
        await self._battement(run_id)
        # Le run part chez son **hôte** (#442), qui n'en reçoit que l'ordre : des
        # données, jamais le travail à exécuter. Les plafonds y voyagent bruts —
        # les garde-fous sont recomposés au déroulement, où seul le validateur
        # humain, câblage de *ce* bus, a un sens (cf. `_derouler`).
        try:
            await self._hote.lancer(
                OrdreRun(
                    run_id=run_id,
                    objectif=objectif,
                    plafond_cout_usd=plafond_cout_usd,
                    plafond_tokens=plafond_tokens,
                    timeout_tache_s=timeout_tache_s,
                    parallelisme=parallelisme,
                    ticket=reference,
                    projet_id=projet,
                    mode_brief=regime_brief,
                )
            )
        except DemarrageHoteRate as echec:
            # Un hôte qui n'est pas parti (#443) : rien ne viendra plus de lui —
            # ni étape, ni battement, ni statut de fin. Le run est donc **soldé
            # ici**, avec sa cause, au lieu d'être laissé `en_cours` jusqu'à ce que
            # le seuil d'orphelinat l'éteigne une demi-heure plus tard. C'est le
            # seul instant où quelqu'un peut l'écrire, et c'est l'appelant.
            _LOGGER.error("Démarrage de l'hôte du run %s raté : %s", run_id, echec)
            # `str(echec)` et non `detail_avec_cause` : le détail d'un démarrage
            # raté est déjà une phrase écrite pour être lue (la commande, le code
            # de sortie, les dernières lignes du journal de l'hôte), et la
            # préfixer de « DemarrageHoteRate : » n'y ajouterait qu'un nom de
            # classe. Seule la **cause** manquait.
            self._consigne(run_id, EXECUTION_ECHEC, "", str(echec), cause=cause_de(echec))
            await self._oublier(run_id)
        resume = await self.resume_vivant(run_id)
        if resume is None:  # pragma: no cover - le lancement vient d'inscrire le run
            raise RuntimeError(f"run {run_id} absent de la projection après son lancement")
        return {**resume, "rapport": rapport.to_dict()}

    async def annuler(self, run_id: str) -> dict[str, Any] | None:
        """Interrompt le run `run_id` et rend son résumé passé à « annulée ».

        L'issue est consignée **d'abord** (elle est acquise : c'est une décision
        humaine, pas le verdict de l'exécution), puis l'hôte (#442) est prié
        d'interrompre le run et l'on attend son extinction au plus
        `DELAI_ANNULATION_S` — un run qui avale son annulation ne suspend pas la
        requête. Rend None si le run est inconnu de la projection.

        Cet ordre vaut pour **tous** les hôtes, y compris celui qui n'est pas dans
        ce process (#444) : consigner l'issue, c'est la publier, et c'est ce que le
        run détaché écoute pour s'annuler lui-même. La borne d'attente ne bouge pas
        pour autant — un hôte qui ne répond plus ne fait pas patienter la requête
        au-delà de `DELAI_ANNULATION_S`, et le run est soldé de toute façon.
        """
        if self.resume(run_id) is None:
            return None
        await self._solder(run_id, "exécution interrompue depuis la Control Tower")
        return await self.resume_vivant(run_id)

    async def mettre_en_pause(self, run_id: str) -> dict[str, Any] | None:
        """Suspend le run `run_id` et rend son résumé passé à « en pause » (#477).

        **Ce qui est parti va à son terme, ce qui ne l'est pas attend** : c'est la
        seule chose qui distingue ce geste de l'annulation, et elle ne se décide
        pas ici — elle vit dans la porte que le moteur franchit avant chaque tâche
        (`maestro.engine.pause`). Ici on ne fait que fermer cette porte, par les
        deux chemins que le service a déjà pour tout ordre :

        - l'**événement**, consigné donc publié, qui traverse la frontière
          d'exécution — c'est par là que le process détaché (#444) reçoit l'ordre,
          sur le canal où il guette déjà l'annulation, sans un transport de plus ;
        - la **porte en mémoire**, quand le run est déroulé par ce process
          (`HoteRunEnProcess`) : pas de bus à traverser, la porte est là.

        Les deux sont posés systématiquement, et non l'un *ou* l'autre : le
        service ne sait pas quel hôte porte ce run, c'est tout son propos, et
        `self._portes` est simplement vide pour un run détaché.

        Trois choses qu'on **ne** fait **pas**, chacune pour une raison :

        - le **battement** n'est pas oublié — un run suspendu bat toujours (#348),
          sans quoi il ressortirait `orphelin` au bout d'une demi-heure et #349
          proposerait de le relancer **depuis son brief**, c'est-à-dire de repayer
          le cadrage d'un run qui n'a rien perdu ;
        - le **statut** n'est pas touché : la pause se superpose à ce que le run
          fait, elle ne le remplace pas (cf. `ORDRES_PAUSE` dans `state`) ;
        - l'**hôte** n'est pas prié d'interrompre quoi que ce soit. C'est
          exactement l'inverse du geste, et l'appeler tuerait le travail en vol.

        Rend None si le run est inconnu de la projection. Ne juge de rien d'autre :
        run déjà soldé, déjà suspendu — c'est la route qui refuse, comme pour
        l'annulation, parce que c'est elle qui a un code HTTP à rendre.
        """
        if self.resume(run_id) is None:
            return None
        self._demarrer()
        self._consigne(run_id, ORDRE_PAUSE, "", "exécution suspendue depuis la Control Tower")
        porte = self._portes.get(run_id)
        if porte is not None:
            porte.fermer()
        return await self.resume_vivant(run_id)

    async def reprendre(self, run_id: str) -> dict[str, Any] | None:
        """Reprend le run `run_id` **là où il en était** — le pendant exact de la pause.

        Le plan, les tâches déjà terminées, le brief approuvé, le coût engagé : rien
        n'a bougé pendant la pause, puisque rien n'a été tué. Reprendre n'a donc rien
        à reconstruire — c'est un `Event.set()` à un aller Redis près, et c'est ce
        qui sépare ce verbe de `relancer` (#349), qui rejoue un run **mort** depuis
        son brief et paie une planification neuve. Les deux existent parce que les
        deux situations existent ; les confondre ferait repayer le cadrage d'un run
        qu'on avait simplement mis de côté.

        Rend None si le run est inconnu. Comme la pause, ne juge de rien : la route
        refuse de reprendre ce qui n'est pas suspendu.
        """
        if self.resume(run_id) is None:
            return None
        self._demarrer()
        self._consigne(run_id, ORDRE_REPRISE, "", "exécution reprise depuis la Control Tower")
        porte = self._portes.get(run_id)
        if porte is not None:
            porte.ouvrir()
        return await self.resume_vivant(run_id)

    async def relancer(self, run_id: str) -> dict[str, Any]:
        """Rejoue un run interrompu **sur son brief approuvé** (#349) — le nouveau résumé.

        Ce qui se perd quand un run meurt n'est pas du temps machine : c'est un
        cadrage **validé par un humain** — sur le run `3ff0bcb065f9`, deux tours de
        clarification, trois réponses et une approbation, soit 2,52 $ et une
        vingtaine de minutes d'attention. Ce brief est intégralement conservé dans la
        projection ; il ne manquait que le geste pour le rejouer, fait à la main le
        2026-08-14 et outillé ici.

        Le run relancé est un **nouveau run** — pas une reprise à l'endroit exact de
        l'interruption, qui supposerait une frontière d'exécution durable (cadrage
        #350). Il part de la **synthèse** du brief retenu, en mode `sans` : c'est,
        au texte près, ce que le run mort allait décomposer (`OrchestrationEngine.run`
        donne `brief.synthese()` à la planification dès qu'un brief existe), et
        `sans` est le seul régime qui ne repaie ni la rédaction, ni la clarification,
        ni la validation. Il conserve le **projet** du run repris — c'est le critère
        du ticket — et son **ticket**, qui n'est qu'une référence : le travail reprend
        là où il portait.

        Ce qui n'est **pas** repris, et c'est un choix : les **sources** (#315). Elles
        ont été résolues vers l'emplacement d'ingestion **du run mort**, propre à son
        `run_id` ; les rattacher au nouveau ferait pointer sa matière vers le dossier
        d'un autre. Et elles n'ont plus rien à apprendre : le brief a été rédigé
        *après* les avoir lues, il en est la synthèse validée. Les redéclarer serait
        repayer la lecture pour un contenu déjà dans le texte qu'on rejoue.

        Quatre refus — les trois premiers sont ceux du ticket, le dernier est sa
        note technique :

        - le run est **inconnu** de la projection ;
        - il est **déjà soldé** — il n'y a rien à reprendre d'un run qui a rendu son
          issue, et le relancer serait le dupliquer ;
        - il est **vivant** — verdict de `vitalite` (#348) et de lui seul : re-déduire
          ici l'orphelinat depuis les horodatages donnerait une seconde formule à
          tenir d'accord avec la première, et c'est exactement ce que le lot 1 existe
          pour éviter ;
        - son **cadrage n'a pas été approuvé** : un run mort avant la validation de
          son brief n'a rien à rejouer, et le dire vaut mieux que repartir en silence
          sur l'objectif brut — ce serait sauter la validation que le run attendait
          encore, sans que personne ne l'ait demandé.

        `indetermine` **passe**, et c'est le choix le moins évident. Un run qui n'a
        jamais battu est un run dont on ne sait rien — les quatre runs fantômes du
        2026-08-17 sont dans ce cas, tous antérieurs au battement, et refuser ici
        rendrait la commande inutile précisément pour ceux qui l'ont motivée. Le
        rapport de coûts penche du même côté que le seuil de #348 : rejouer un run
        qui travaillait encore coûte un run en double, qu'on annule ; refuser coûte
        le cadrage, définitivement. L'**UI**, elle, ne propose le geste que sur
        `orphelin` — proposer sur une absence d'information serait deviner, ce que le
        troisième verdict existe pour refuser.

        Lève `RelanceRefusee` (⊂ `ValueError`) avec son motif dans les quatre cas ;
        la route en fait un 404, un 409 ou un 422.
        """
        execution = self._state.execution(run_id)
        if execution is None:
            raise RelanceRefusee(
                MOTIF_RELANCE_RUN_INCONNU,
                f"exécution inconnue : {run_id} (voir GET /api/executions).",
            )
        if execution.statut in STATUTS_EXECUTION_TERMINAUX:
            raise RelanceRefusee(
                MOTIF_RELANCE_RUN_SOLDE,
                f"exécution déjà soldée ({execution.statut}) : {run_id} — "
                "il n'y a rien à reprendre d'un run qui a rendu son issue.",
            )
        verdict = vitalite(
            execution.statut,
            (await self._registre()).get(run_id),
            seuil_s=self._seuil_orphelin_s,
        )
        if verdict == VITALITE_VIVANT:
            raise RelanceRefusee(
                MOTIF_RELANCE_RUN_VIVANT,
                f"exécution encore vivante : {run_id} — son hôte bat toujours. "
                "L'interrompre d'abord (POST …/annuler) si c'est bien voulu.",
            )
        brief = execution.brief
        if brief is None or not execution.brief_approuve:
            raise RelanceRefusee(
                MOTIF_RELANCE_SANS_CADRAGE,
                f"exécution sans brief approuvé : {run_id} — elle s'est arrêtée "
                f"avant la validation de son cadrage ({execution.statut}), il n'y a "
                "donc rien à rejouer. La relancer reviendrait à repartir de son "
                "objectif brut, ce qui est un nouveau run, pas une reprise.",
            )
        ticket, projet = execution.ticket, execution.projet_id

        # Rien entre ce point et l'écriture ci-dessous ne doit **attendre** : la
        # lecture du registre est passée, et c'est le statut relu ici puis soldé dans
        # la foulée qui empêche deux relances concurrentes (un double clic dans l'UI)
        # de partir toutes les deux. `_solder` commence par une écriture synchrone,
        # donc la seconde requête trouvera le run déjà `annulee` et sortira en
        # « déjà soldée » — un garde-fou par construction plutôt que par chance.
        self._demarrer()
        # Le run repris est soldé **avant** que le nouveau ne parte : c'est le
        # critère du ticket (« au lieu de rester `en_cours` »), et l'ordre est ce qui
        # évite qu'un lecteur voie un instant deux runs en vol sur le même cadrage.
        # « annulée » et non « échec » : rien n'a raté — son hôte est tombé, et
        # quelqu'un a décidé de reprendre. Même lecture que le refus d'un brief
        # (#320), qui solde en « annulée » pour la même raison.
        #
        # Le lien entre les deux ne s'écrit **que** sur le nouveau (`reprise_de`) :
        # le run repris n'est jamais réécrit pour désigner son successeur, comme le
        # fichier `reprise-de` d'un run d'orchestration (#204). Un run peut donc être
        # repris sans que sa trace change de forme.
        await self._solder(
            run_id,
            "run repris sur son brief approuvé depuis la Control Tower : "
            "un nouveau run en porte la suite",
        )
        return await self.lancer(
            brief.synthese(),
            ticket=ticket,
            projet_id=projet,
            mode_brief=MODE_BRIEF_SANS,
            reprise_de=run_id,
        )

    async def _solder(self, run_id: str, detail: str) -> None:
        """Solde un run en vol : issue, exécution éteinte, tâches soldées, battement oublié.

        Le geste commun de l'annulation (#185) et de la relance (#349), qui soldent
        toutes deux un run non terminé — la seule chose qui les distingue est le
        `detail`, c'est-à-dire *pourquoi*. En faire deux copies laisserait diverger
        l'ordre des quatre écritures, dont chacune a sa raison :

        1. l'**issue** d'abord : elle est acquise (une décision humaine, pas le
           verdict de la tâche) ;
        2. l'**exécution** ensuite, si l'hôte la porte — au plus
           `DELAI_ANNULATION_S`, un run qui avale son annulation ne suspendant pas
           l'appelant. Dans le cas d'une relance, l'hôte ne la porte généralement
           pas (le run est orphelin *parce que* son hôte est tombé) et le dit en
           rendant False ; s'il la porte quand même — registre muet, verdict
           `indetermine` —, ne pas l'éteindre laisserait un run soldé continuer de
           travailler ;
        3. les **tâches** que le run portait (#466), et les agents avec elles —
           après l'extinction et jamais avant, sous peine de les voir remises
           `en_cours` par le dernier événement d'une exécution qui s'éteint encore
           (cf. `_solder_les_taches`). C'est ici que le geste vit, et pas chez ses
           deux appelants, pour la raison même qui a fait exister cette méthode :
           solder un run est **un** geste, pas une liste à recopier ;
        4. le **battement** en dernier, jamais avant le statut terminal : entre les
           deux, un lecteur verrait un run encore en cours et sans battement,
           c'est-à-dire un orphelin qui n'en est pas un.

        Depuis #444, le premier de ces trois gestes en fait **deux** : consigner
        l'issue, c'est la publier sur le bus, et c'est par là que l'annulation
        traverse la frontière d'exécution. Un hôte détaché
        (`maestro.controltower.hote_detache`) guette cette issue, y reconnaît la fin
        de son run et annule sa propre tâche `asyncio` — `Task.cancel` à un aller
        Redis près. Rien ne change ici, et c'est bien le sujet : la bascule vit dans
        **l'hôte**, seul à savoir *où* le run tourne, pendant que ce service reste
        celui qui ne le sait pas.

        Deux corollaires à ne pas défaire. L'ordre 1 → 2 devient **nécessaire** et
        non plus seulement juste : demander l'interruption avant de consigner
        reviendrait à laisser l'hôte détaché attendre un ordre qui n'est pas encore
        parti. Et cette issue est le **seul** message : aucun événement d'annulation
        ne double `execution.statut`, parce que deux façons de dire un même fait
        finissent par se séparer — l'une émise sans l'autre — et qu'on aurait alors
        un run soldé qui travaille encore.

        Le `_demarrer()` initial n'est pas une précaution de style. `_pousser`
        **abandonne** l'événement tant que le câblage n'est pas armé, et il ne l'est
        qu'au premier lancement : sur une API qui vient de redémarrer et n'a encore
        rien lancé — le cas exact d'un run orphelin, dont l'hôte est justement
        tombé —, l'issue serait appliquée à la projection en mémoire sans jamais
        atteindre le bus, donc ni le journal durable (#97) ni le prochain rejeu. Le
        run réapparaîtrait `en_cours` au redémarrage suivant, c'est-à-dire la panne
        même que #347 traite. Depuis #444 il porte une seconde charge, et c'est la
        même : sans câblage armé, l'ordre d'annulation n'atteindrait pas davantage
        le process détaché que le journal — un run relancé depuis une API fraîche
        continuerait de travailler sous son ancien identifiant.
        """
        self._demarrer()
        self._consigne(run_id, EXECUTION_ANNULEE, "", detail, cause=CAUSE_ANNULATION)
        await self._hote.annuler(run_id, delai_s=DELAI_ANNULATION_S)
        self._solder_les_taches(run_id, detail, cause=CAUSE_ANNULATION)
        await self._oublier(run_id)

    def _solder_les_taches(self, run_id: str, motif: str, *, cause: str = "") -> None:
        """Solde ce que le run **portait** : ses tâches non terminales, donc ses agents (#466).

        Solder un run ne soldait que le run. Ses tâches restaient `en_cours` et les
        agents qui les portaient `occupe`, **définitivement** : la projection est
        reconstruite par rejeu du journal, donc l'état faux survivait au
        redémarrage de l'API. Constat du 2026-08-24 sur le poste — `brief-directions`
        en vol depuis vingt jours sur un run annulé le jour même, `bdd` et
        `designer` occupés sur des tâches mortes.

        Rien de neuf n'est inventé pour le réparer, et c'est tout le point : la
        projection **sait déjà** libérer un agent — un `tache.statut` terminal
        appelle `agent.termine(event.tache_id)`, instance par instance (#100) —, il
        n'y manquait que l'émission. C'est exactement le geste fait à la main ce
        jour-là, trois `tache.statut` publiés sur le bus ; on le rend ici à la
        machine qui aurait dû le faire.

        **`echec` et non `bloquee`**, alors que les deux sont terminaux et que seul
        le premier incrémente `taches_echouees` sur la fiche de l'agent. `bloquee` a
        un sens précis dans la machine à états (docs/03 §3 : « dépendance en échec —
        la tâche n'est jamais mise en file ni exécutée »), et le poser sur une tâche
        qui était bel et bien en vol ferait mentir l'état plutôt qu'épargner un
        compteur. Le compteur, lui, dit vrai : la tâche n'a pas abouti. Le *pourquoi*
        voyage à côté — `cause` (#479) et le `motif` en clair —, ce qui est
        précisément la séparation que `causes.py` existe pour tenir.

        Trois choses à ne pas défaire :

        - les tâches sont celles que **le run a portées** (`PorteeRun`, #473) et non
          celles dont le `run_id` le désigne : un identifiant de tâche est un slug
          engendré de son contenu, donc partagé dès qu'une relance rejoue le même
          brief — lu là-bas, un run annulé solderait les tâches de son successeur ;
        - le geste vient **après** `_hote.annuler`, jamais avant : une tâche soldée
          pendant que l'exécution s'éteint encore serait remise `en_cours` par le
          dernier événement du moteur, et on aurait réparé dans le sens du vent ;
        - les tâches déjà terminales sont **sautées**. Sans ce filtre, une tâche
          réussie avant l'annulation repasserait `echec` et l'agent perdrait un
          `taches_terminees` acquis — un run annulé n'annule pas ce qui a été fait.

        ⚠ Et l'événement se **pousse** (`_pousser`), il ne s'émet pas (`_emettre`) :
        c'est la seule différence de forme avec les issues de run d'à côté, et elle
        n'est pas cosmétique. `_emettre` applique à la projection *puis* publie, en
        s'appuyant sur l'idempotence pour que la pompe réapplique sans effet — vrai
        d'`execution.statut`, **faux** de `tache.statut`, dont l'application
        incrémente `taches_terminees`/`taches_echouees` sur la fiche de l'agent.
        Émettre ici compterait donc chaque tâche soldée **deux fois** (constaté :
        `taches_echouees` à 2 pour un seul échec). Pousser est aussi ce que fait
        déjà tout autre événement de tâche — le pont télémétrie n'a jamais eu
        d'autre chemin —, et c'est exactement le geste de la réparation manuelle du
        2026-08-24 : publier sur le bus, laisser la pompe appliquer, consigner au
        journal durable (#97) et rediffuser à l'UI.

        Ne rattrape **pas** l'existant : ce qui est déjà en vol aujourd'hui le reste,
        la projection étant rejouée depuis un journal que ce correctif ne réécrit
        pas. Le geste de rattrapage est le même qu'en août — republier un
        `tache.statut` terminal par tâche restée en l'air.
        """
        for tache in self._state.taches(run=PorteeRun.run(run_id)):
            if tache.statut in STATUTS_TACHE_TERMINAUX:
                continue
            self._pousser(
                Event(
                    type=EVENEMENT_TACHE_STATUT,
                    run_id=run_id,
                    tache_id=tache.id,
                    titre=tache.titre,
                    # L'agent **de la tâche**, sans quoi rien ne serait libéré : la
                    # projection ne rend son créneau qu'à l'agent que l'événement
                    # nomme (`_applique_statut_tache`), et un événement sans
                    # exécutant est écarté d'emblée (`_AGENTS_NON_EXECUTANTS`).
                    agent=tache.agent,
                    role=tache.role,
                    statut=STATUT_ECHEC,
                    detail=f"tâche non terminée quand son run a été soldé — {motif}",
                    ticket=tache.ticket,
                    projet_id=tache.projet_id,
                    cause=cause,
                )
            )

    async def fermer(self) -> None:
        """Arrête le service : l'hôte se retire, pont déposé, pompe éteinte.

        Appelé à l'arrêt de l'app (lifespan), **avant** la fermeture du bus. Ce
        qu'il advient des runs en vol n'est plus décidé ici mais par l'**hôte**
        (#442) : celui en process les annule, faute de pouvoir leur survivre —
        c'est la contrepartie assumée de la tâche de fond (cf. docstring du
        module) —, là où un hôte détaché les laissera vivre. Le service, lui, dit
        seulement qu'il se retire.

        Le cœur s'éteint avec le service et **n'efface aucun battement** (#348) :
        un run emporté par l'arrêt de l'API garde son dernier battement, qui
        vieillit — c'est ainsi qu'il ressortira `orphelin` au lieu de rester
        `en_cours` pour toujours, et c'est exactement la panne du 2026-08-14.
        """
        await self._hote.fermer(delai_s=DELAI_ANNULATION_S)
        if self._coeur is not None:
            self._coeur.cancel()
            with suppress(asyncio.CancelledError):
                await self._coeur
            self._coeur = None
        if self._handler is not None:
            self._logger.removeHandler(self._handler)
            self._handler = None
        if self._pompe is not None:
            self._pompe.cancel()
            with suppress(asyncio.CancelledError):
                await self._pompe
            self._pompe = None
        self._file = None
        self._boucle = None

    # ------------------------------------------------------------------ interne

    def _composer(
        self,
        sources: Sequence[Mapping[str, Any] | Source] | None,
        *,
        run_id: str,
    ) -> tuple[Source, ...]:
        """La matière du run : sources résolues, octets téléversés rattachés (#317).

        Trois temps, et leur ordre est le contenu de la méthode.

        1. **Compléter** — un `{"type": "fichier", "id": …}` ne dit ni nom ni
           taille ; le dépôt les lui donne, et ce sont ceux des octets reçus, pas
           ceux qu'un client annonce. Sans quoi le plafond par source se
           contournerait en déclarant douze octets.
        2. **Résoudre** (#315) — canonicalisation, racines interdites, plafonds.
           C'est le seul endroit qui refuse, et il refuse **avant** toute
           écriture : rien de ce qui suit ne doit laisser un run à moitié inscrit.
        3. **Rattacher** — la résolution vient de calculer *où* la matière doit
           être ; les octets y sont copiés. Cet ordre est obligé : l'emplacement
           d'ingestion dépend du `run_id`, qui n'existe pas au téléversement.

        Une source déclarée sans `id` traverse les trois temps sans octets : elle
        ressortira `ignore` / `source-absente` au rapport de lecture, ce qui est
        exactement ce qu'on veut qu'elle dise.
        """
        declarees, identifiants = declarer_televersements(
            sources, depot=self._televersements
        )
        matiere = resoudre_sources(declarees, run_id=run_id, garde_fous=self._ingestion)
        for source, identifiant in zip(matiere, identifiants, strict=False):
            if identifiant:
                self._televersements.rattacher(identifiant, Path(source.chemin))
        return matiere

    def _demarrer(self) -> None:
        """Arme le câblage du service — idempotent, au premier lancement seulement.

        Rien n'est posé tant qu'aucun run n'est demandé : une app qui n'en lance
        jamais (les tests des autres endpoints) ne pose ni handler ni tâche.
        """
        if self._pompe is not None:
            return
        self._boucle = asyncio.get_running_loop()
        self._file = asyncio.Queue()
        self._pompe = self._boucle.create_task(self._draine())
        # Un **seul** cœur pour tous les runs du service, et non un par run : ce
        # qu'il publie ne dépend que de la liste des runs en vol, et N tâches
        # d'horloge pour une même information coûteraient N réveils par période.
        self._coeur = self._boucle.create_task(self._battre())
        self._handler = JournalEventHandler(self._pousser)
        self._logger.addHandler(self._handler)

    async def _battre(self) -> None:
        """Le cœur du service : un battement par run en vol, à chaque période (#348).

        « En vol » se juge sur **deux** choses, et la seconde n'est pas une
        précaution de style : l'hôte porte encore le run, *et* celui-ci n'a pas
        déjà consigné son issue. Une issue est consignée **avant** que l'exécution
        ne s'éteigne (`annuler` le fait explicitement, `_derouler` en sortant),
        donc s'en tenir à l'hôte laisserait le cœur reposer un battement juste
        après `_oublier` — et l'entrée d'un run soldé resterait alors pour
        toujours.

        Un battement en échec (Redis injoignable) est tracé sans arrêter le cœur —
        même parti pris que la pompe de publication : le run continue, seul son
        signal de vie manque, et manquer un battement sur soixante ne change rien
        au verdict.

        Le **ramassage** (#446) partage ce réveil, et passe **avant** les
        battements : un hôte mort n'a pas à recevoir un signal de vie de plus au
        tour où on le solde. Il n'a pas de tâche à lui pour la raison qui a fait
        n'en donner qu'une au cœur — ce qui se fait à chaque période se fait sur un
        seul réveil.
        """
        while True:
            await asyncio.sleep(self._periode_battement_s)
            await self._ramasser()
            for run_id in self._hote.runs_en_vol():
                resume = self.resume(run_id)
                if resume is not None and resume["statut"] in STATUTS_EXECUTION_TERMINAUX:
                    continue
                await self._battement(run_id)

    async def _ramasser(self) -> None:
        """Solde les runs dont l'hôte est mort **sans publier d'issue** (#446).

        L'hôte rend un constat — ces process se sont éteints, voici leur code et
        leur trace (`HoteRun.ramasser`) — et c'est **ici** qu'on décide de ce qu'il
        signifie, en le confrontant à la projection. Un hôte qui a publié son issue
        avant de partir laisse un run déjà terminal : rien à faire. Un hôte tué net
        laisse un run `en_cours` que plus personne ne porte, et qui attendrait sinon
        le seuil d'orphelinat pour cesser d'être affiché comme actif — puis le
        resterait indéfiniment, `orphelin` n'étant pas un statut mais un verdict sur
        son hôte.

        `echec` et non `annulee`, à la différence de tout ce que solde `_solder` :
        personne n'a dit stop, l'hôte est tombé. Et le `detail` porte la **cause**
        telle que l'hôte la connaît — code de sortie, dernières lignes de son
        journal —, ce qui est la seule information dont dispose quelqu'un qui
        découvre le run soldé une heure plus tard.

        Trois choses qu'il ne fait pas, et chacune est un choix :

        - il ne **redéduit pas l'orphelinat**. Il ne balaie pas la projection à la
          recherche de runs muets : `vitalite` est la seule formule du verdict
          (#348), comme `relancer` l'a déjà tranché, et un second calcul serait un
          second endroit à tenir d'accord avec le premier ;
        - il ne solde donc **pas les orphelins**, seulement les morts *observées*.
          Un run dont l'hôte est mort pendant que l'API était arrêtée reste
          `orphelin`, et c'est ce qu'on veut : c'est exactement le run que
          `relancer` (#349) sait rejouer sur son brief approuvé, et le solder le
          rendrait au contraire irrattrapable (`run-solde`) ;
        - il ne **lève jamais**. Le ramassage vit dans le cœur : une panne ici
          arrêterait les battements de tous les autres runs, c'est-à-dire ferait
          exactement le mal qu'il existe pour réparer.
        """
        try:
            for defunt in self._hote.ramasser():
                resume = self.resume(defunt.run_id)
                if resume is None or resume["statut"] in STATUTS_EXECUTION_TERMINAUX:
                    continue
                _LOGGER.error(
                    "Hôte du run %s mort sans publier d'issue : %s",
                    defunt.run_id,
                    defunt.cause,
                )
                self._consigne(defunt.run_id, EXECUTION_ECHEC, "", defunt.cause)
                # Ce que le run portait est soldé avec lui (#466) : le process est
                # mort, il n'écrira plus l'issue de ses tâches, et sans ce geste
                # elles resteraient `en_cours` avec leurs agents `occupe` pour
                # toujours — le défaut de l'annulation, à l'identique, par l'autre
                # chemin qui solde un run de l'extérieur. Sans `cause` : personne
                # n'a dit stop, c'est l'hôte qui est tombé, et `defunt.cause` porte
                # déjà en clair ce qu'on en sait.
                self._solder_les_taches(defunt.run_id, defunt.cause)
                # Après le statut terminal, jamais avant — même ordre et même
                # raison que partout ailleurs (cf. `_solder`).
                await self._oublier(defunt.run_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "Ramassage des hôtes morts impossible : les runs concernés "
                "resteront « en cours » jusqu'à leur seuil d'orphelinat."
            )

    async def _battement(self, run_id: str) -> None:
        """Pose le battement de `run_id` — **best-effort**, jamais une levée."""
        try:
            await self._battements.battre(run_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "Battement du run %s impossible : le run continue, mais l'API "
                "finira par le croire orphelin.",
                run_id,
            )

    async def _oublier(self, run_id: str) -> None:
        """Retire le battement d'un run **soldé** — best-effort, jamais une levée.

        Seul effacement du dispositif, et il ne juge de rien : le run porte
        désormais un statut terminal, donc sa vitalité n'est plus consultée. Ce
        qui s'arrête sans statut terminal — API tuée, machine endormie — garde son
        battement et vieillit vers `orphelin`.
        """
        try:
            await self._battements.oublier(run_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "Retrait du battement du run %s impossible : sans effet sur son "
                "statut, déjà terminal.",
                run_id,
            )

    async def _derouler(self, ordre: OrdreRun) -> None:
        """Déroule le run décrit par `ordre` et consigne son issue.

        C'est ce que le service confie à son hôte en process (#442) : l'hôte crée
        la tâche, cette méthode est ce qu'elle exécute. Elle ne prend plus qu'un
        `OrdreRun` — des données —, ce qui n'est pas un rangement de signature :
        c'est la forme sous laquelle un hôte qui exécute **ailleurs** recevra le
        même run, et la garantie que rien d'intransportable (un bus, un moteur,
        une closure) ne s'y est glissé.

        Aucune exception ne remonte : une tâche de fond n'a pas d'appelant à qui
        les rendre — l'échec devient le **statut** du run (visible dans la
        projection et sur le flux), sa cause partant dans le détail. Seule
        l'annulation se propage, pour que la tâche finisse bien « annulée » (son
        issue, elle, a déjà été consignée par `annuler`).

        Les **garde-fous** (#9) se recomposent ici, et la séparation est le sujet :
        les plafonds sont un réglage du **lancement**, donc ils voyagent dans
        l'ordre ; le **validateur** humain est un câblage de déploiement (*où* la
        décision est demandée — ici le bus de cette app), donc il se branche là où
        le run se déroule. Les faire voyager ensemble rendrait l'ordre
        intransportable pour un seul de ses cinq champs.

        `ticket` (#187) descend jusqu'au moteur, qui en dote chaque
        tâche du plan : le ticket du run se retrouve ainsi sur chaque carte, par
        le seul chemin du journal — le moteur n'a rien à savoir de son origine.
        `projet_id` (#222) descend par le même chemin et pour la même raison :
        chaque tâche du plan en hérite, et l'appartenance remonte aux vues.

        `mode_brief` (#320) descend, lui, en deux morceaux, et la séparation est la
        même que celle des garde-fous : l'**arbitre** est un câblage de déploiement
        (*où* la question est posée — ici le bus de l'app), donc il part à la
        construction du moteur ; le **mode** est un choix du lancement (*y a-t-il
        quelqu'un devant ?*), donc il part avec l'objectif. Un refus humain remonte
        en `BriefRefuse` et devient un run **annulé**, jamais un échec : rien n'a
        raté, quelqu'un a dit non — et rien de payant n'a été engagé au-delà du
        brief.

        La **porte** de pause (#477) est ouverte ici et retirée en partant. Elle
        n'est pas un câblage de déploiement comme le validateur ou les arbitres :
        c'est un objet **par run**, que `mettre_en_pause` retrouve par son
        `run_id`. Le `finally` compte autant que l'ouverture — une porte laissée
        derrière soi ferait grossir le registre d'un service qui vit aussi
        longtemps que l'API, et un `run_id` réutilisé (une relance rejoue le même
        objectif, pas le même identifiant, mais rien ne l'interdit) trouverait la
        porte d'un run mort, refermée, donc un run neuf figé sans cause lisible.
        """
        run_id = ordre.run_id
        self._portes[run_id] = PorteExecution()
        try:
            await self._derouler_sous_porte(ordre, self._portes[run_id])
        finally:
            self._portes.pop(run_id, None)

    async def _derouler_sous_porte(self, ordre: OrdreRun, porte: PorteExecution) -> None:
        """Le déroulement proprement dit — séparé pour que la porte soit retirée d'un `finally`.

        Découpage purement structurel : `_derouler` pose et retire la porte, ceci
        est son corps d'avant #477, à un paramètre près.
        """
        run_id = ordre.run_id
        garde_fous = Guardrails(
            plafond_cout_usd=ordre.plafond_cout_usd,
            plafond_tokens=ordre.plafond_tokens,
            timeout_s=ordre.timeout_tache_s,
            validateur=ValidateurControlTower(self._bus),
        )
        journal = RunJournal(run_id=run_id, logger=self._logger)
        try:
            moteur = self._fabrique(
                guardrails=garde_fous,
                max_parallele=ordre.parallelisme,
                arbitre_brief=ArbitreBriefControlTower(self._bus),
                # Les questions de clarification (#321) passent par le même bus et
                # se câblent au même endroit, pour la même raison : *où* la question
                # est posée est un choix de déploiement. Le **plafond**, lui, n'est
                # pas passé ici — c'est un réglage du moteur, dont le défaut
                # (`TOURS_CLARIFICATION_DEFAUT`) vaut pour tout run lancé par l'API.
                arbitre_clarification=ArbitreClarificationControlTower(self._bus),
            )
            rapport = await moteur.run(
                ordre.objectif,
                journal=journal,
                ticket=ordre.ticket,
                projet_id=ordre.projet_id,
                mode_brief=ordre.mode_brief,
                porte=porte,
            )
        except asyncio.CancelledError:
            raise
        except BriefRefuse as refus:
            # L'issue est peut-être déjà consignée : la projection passe le run à
            # « annulée » dès la décision (`_applique_brief_decision`), pour qu'un
            # run publié hors de l'API le montre aussi. La reconsigner ici est sans
            # effet sur l'état (idempotent) et pose la `fin` du run.
            self._consigne(
                run_id, EXECUTION_ANNULEE, "", str(refus), cause=CAUSE_ANNULATION
            )
            await self._oublier(run_id)
            return
        except Exception as exc:
            _LOGGER.exception("Exécution %s interrompue par une erreur.", run_id)
            detail, cause = detail_avec_cause(exc)
            self._consigne(run_id, EXECUTION_ECHEC, "", detail, cause=cause)
            await self._oublier(run_id)
            return
        echouees = len(rapport.echouees) + len(rapport.bloquees)
        total = len(rapport.resultats)
        self._consigne(
            run_id,
            EXECUTION_ECHEC if echouees else EXECUTION_TERMINEE,
            "",
            f"{len(rapport.reussies)}/{total} tâche(s) réussie(s)",
        )
        # Le battement s'efface **après** le statut terminal, jamais avant : entre
        # les deux, un lecteur verrait un run encore `en_cours` sans battement,
        # c'est-à-dire un orphelin qui n'en est pas un.
        await self._oublier(run_id)

    def _consigne(
        self,
        run_id: str,
        statut: str,
        objectif: str,
        detail: str,
        *,
        ticket: ReferenceTicket | None = None,
        projet_id: str | None = None,
        sources: Sequence[Source] = (),
        mode_brief: str = "",
        reprise_de: str = "",
        cause: str = "",
    ) -> None:
        """Émet le cycle de vie du run : projection d'abord, bus ensuite.

        Même ordre que les endpoints d'écriture de l'app (« état d'abord,
        diffusion ensuite ») : le REST répond déjà à jour, et la pompe
        réapplique l'événement sans effet (idempotence). L'objectif comme le
        détail sont expurgés des secrets avant de partir — ce qui va sur le bus
        est montrable, même filet que le journal (#8). Seul le **lancement**
        porte une référence externe (#187), un projet (#222), des sources
        (#315), le régime du brief (#320) et le run repris (#349) : la projection
        ne les retire pas aux événements suivants, qui n'en savent rien. Un
        lancement **sans** source émet `sources=None` et non une liste vide —
        l'événement reste alors celui d'avant ce lot.

        Les sources, elles, ne passent **pas** par `redact_secrets`, et c'est un
        choix : l'objectif et le détail sont du texte libre écrit par un humain,
        où un secret collé est plausible ; une source est un localisateur
        structuré et déjà validé, dont le `chemin` doit rester **exact** — c'est
        la destination où le téléversement (#317) écrira. Un masquage y
        corromprait la donnée pour un gain hypothétique.
        """
        self._emettre(
            Event(
                type=EVENEMENT_EXECUTION_STATUT,
                run_id=run_id,
                titre=redact_secrets(objectif),
                agent=ACTEUR_RUN,
                role=ROLE_RUN,
                statut=statut,
                detail=redact_secrets(detail),
                ticket=ticket,
                projet_id=projet_id,
                sources=list(sources) or None,
                mode_brief=mode_brief,
                reprise_de=reprise_de,
                # La cause n'est **pas** expurgée, contrairement à l'objectif et
                # au détail (#479) : c'est un code d'un ensemble fermé
                # (`causes.CAUSES`), jamais du texte libre — il n'y a rien à y
                # masquer, et un masquage le rendrait illisible pour l'écran qui
                # doit le ranger.
                cause=cause,
            )
        )

    def _emettre(self, evenement: Event) -> None:
        """Applique l'événement à la projection puis le met en file de publication."""
        self._state.appliquer(evenement)
        self._pousser(evenement)

    def _pousser(self, evenement: Event) -> None:
        """Met un événement en file de publication — **synchrone, appelable partout**.

        C'est le publieur du pont télémétrie (handler `logging`, donc synchrone
        et potentiellement appelé hors de la boucle, un validateur console
        passant par `asyncio.to_thread`) : le passage par `call_soon_threadsafe`
        est ce qui rend l'appel sûr depuis n'importe quel fil, et la file ce qui
        garde l'ordre des étapes jusqu'au bus. Sans câblage armé (aucun run
        lancé), l'événement est abandonné plutôt que perdu en route.
        """
        boucle, file = self._boucle, self._file
        if boucle is None or file is None:
            return
        boucle.call_soon_threadsafe(file.put_nowait, evenement)

    async def _draine(self) -> None:
        """L'unique publieur : draine la file vers le bus, un événement à la fois.

        Un échec de publication (bus en panne) est tracé sans arrêter le
        drainage : le run continue, seul son reflet temps réel manque — même
        parti pris que la pompe de l'app.
        """
        file = self._file
        if file is None:  # pragma: no cover - `_demarrer` la pose avant la tâche
            return
        while True:
            evenement = await file.get()
            try:
                await self._bus.publish(evenement)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception(
                    "Publication d'un événement d'exécution impossible : le run "
                    "continue, son reflet temps réel manquera cet événement."
                )
