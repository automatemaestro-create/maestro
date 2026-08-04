"""Pilotage des exécutions depuis la Control Tower — lancer, suivre, annuler (#185).

L'API exposait l'**état** d'une orchestration sans savoir en **déclencher** une :
un run se lançait en ligne de commande (`maestro-run`), ce qui interdisait à la
Control Tower d'être un vrai poste de pilotage. Ce module est la pièce
manquante — le service que les routes `/api/executions` appellent :

- `lancer` construit les garde-fous (#9) du run, l'ouvre en **tâche de fond** du
  process de l'API et rend son résumé (donc son `run_id`) immédiatement ;
- `resumes`/`resume` lisent la **projection** (`ControlTowerState`) : le service
  ne tient pas de second registre des runs, il lit celui qui existe déjà ;
- `annuler` interrompt un run en vol et consigne l'issue.

**Frontière d'exécution retenue : la tâche de fond du process de l'API.** Le
ticket laissait le choix avec la soumission à la file (`--queue`,
`maestro.queue`). La tâche de fond est retenue pour le POC : elle garde
l'annulation *à portée* (`asyncio.Task.cancel`, là où interrompre un run déjà
distribué demanderait un protocole de révocation côté workers) et n'ajoute aucune
dépendance d'infrastructure à `maestro-api`. Le contrat REST n'en dépend pas :
basculer sur la file plus tard ne change que la fabrique du moteur
(`fabrique_moteur`), pas les routes. Le corollaire est assumé : un run ne survit
pas au redémarrage de l'API — sa **trace**, elle, survit (journal durable #97).

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
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from maestro.controltower.bridge import JournalEventHandler
from maestro.controltower.events import (
    EVENEMENT_EXECUTION_STATUT,
    Event,
    EventBus,
)
from maestro.controltower.state import (
    EXECUTION_ANNULEE,
    EXECUTION_ECHEC,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    ControlTowerState,
)
from maestro.controltower.validation import ValidateurControlTower
from maestro.engine.guardrails import Guardrails
from maestro.references import ReferenceTicket
from maestro.telemetry import LOGGER_NAME, RunJournal, redact_secrets

if TYPE_CHECKING:
    from maestro.engine.loop import OrchestrationEngine

#: Fabrique du moteur d'un run : appelée avec les garde-fous du lancement et le
#: plafond de parallélisme. Injectable (`create_app`) — les tests y passent un
#: moteur factice, aucun fournisseur n'étant résolu tant qu'aucun run ne part.
FabriqueMoteur = Callable[..., "OrchestrationEngine"]

#: Délai laissé à un run annulé pour s'éteindre avant que la requête d'annulation
#: rende la main. Au-delà, l'issue est déjà consignée et la tâche est abandonnée
#: à son extinction — même parti pris que `maestro.engine.runner` : une
#: réalisation qui avale son annulation ne doit pas suspendre l'appelant.
DELAI_ANNULATION_S: float = 5.0

#: L'acteur au nom duquel le cycle de vie d'un run est consigné — le même que
#: celui de l'étape de planification du journal (#8).
_ACTEUR_RUN = "orchestrateur"
_ROLE_RUN = "Orchestrateur"

_LOGGER = logging.getLogger("maestro.controltower")


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
    `OrchestrationEngine.default`).
    """

    def __init__(
        self,
        bus: EventBus,
        state: ControlTowerState,
        *,
        fabrique_moteur: FabriqueMoteur | None = None,
    ) -> None:
        self._bus = bus
        self._state = state
        self._fabrique = fabrique_moteur if fabrique_moteur is not None else moteur_par_defaut
        self._runs: dict[str, asyncio.Task[None]] = {}
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

    # ------------------------------------------------------------------ lecture

    def resumes(self) -> list[dict[str, Any]]:
        """Les runs connus (`GET /api/executions`) : résumés, **récents d'abord**.

        Tous ceux dont la projection porte une trace — lancés par l'API comme
        publiés par un autre process (`maestro-run --publier`) : le suivi ne
        distingue pas leur origine.
        """
        resumes = [e.resume() for e in self._state.executions()]
        return sorted(resumes, key=lambda r: str(r["debut"]), reverse=True)

    def resume(self, run_id: str) -> dict[str, Any] | None:
        """Le résumé du run `run_id`, ou None s'il est inconnu de la projection."""
        execution = self._state.execution(run_id)
        return None if execution is None else execution.resume()

    def en_vol(self, run_id: str) -> bool:
        """Le run est-il porté par ce service et encore en cours ?"""
        tache = self._runs.get(run_id)
        return tache is not None and not tache.done()

    # ----------------------------------------------------------------- écriture

    def lancer(
        self,
        objectif: str,
        *,
        plafond_cout_usd: float | None = None,
        plafond_tokens: int | None = None,
        timeout_tache_s: float | None = None,
        parallelisme: int | None = None,
        ticket: Mapping[str, str] | ReferenceTicket | None = None,
    ) -> dict[str, Any]:
        """Lance une exécution en tâche de fond et rend son résumé **immédiatement**.

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

        Lève `ValueError` sur un objectif vide ou un garde-fou hors bornes (les
        plafonds sont des maximums : ils doivent être > 0) — la route en fait un
        422. Le run, lui, ne peut plus échouer de façon synchrone : ce qui lui
        arrive ensuite se lit dans son statut.
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
        garde_fous = Guardrails(
            plafond_cout_usd=plafond_cout_usd,
            plafond_tokens=plafond_tokens,
            timeout_s=timeout_tache_s,
            validateur=ValidateurControlTower(self._bus),
        )

        reference = (
            ticket
            if isinstance(ticket, ReferenceTicket)
            else ReferenceTicket.depuis(ticket)
        )

        run_id = uuid.uuid4().hex[:12]
        self._demarrer()
        # L'événement de lancement **avant** la tâche de fond : la projection
        # connaît le run (donc le résumé rendu est complet) avant qu'une seule
        # étape ne puisse y arriver. C'est lui qui porte le ticket du run (#187) :
        # il part sur le bus, donc dans le journal durable — plus rien n'est tenu
        # en mémoire par ce service.
        self._consigne(
            run_id,
            EXECUTION_EN_COURS,
            objectif,
            "lancée depuis la Control Tower",
            ticket=reference,
        )
        tache = asyncio.get_running_loop().create_task(
            self._derouler(run_id, objectif, garde_fous, parallelisme, reference)
        )
        self._runs[run_id] = tache
        tache.add_done_callback(lambda _: self._runs.pop(run_id, None))
        resume = self.resume(run_id)
        if resume is None:  # pragma: no cover - le lancement vient d'inscrire le run
            raise RuntimeError(f"run {run_id} absent de la projection après son lancement")
        return resume

    async def annuler(self, run_id: str) -> dict[str, Any] | None:
        """Interrompt le run `run_id` et rend son résumé passé à « annulée ».

        L'issue est consignée **d'abord** (elle est acquise : c'est une décision
        humaine, pas le verdict de la tâche), puis la tâche de fond est annulée
        et l'on attend son extinction au plus `DELAI_ANNULATION_S` — un run qui
        avale son annulation ne suspend pas la requête. Rend None si le run est
        inconnu de la projection.
        """
        if self.resume(run_id) is None:
            return None
        self._consigne(
            run_id, EXECUTION_ANNULEE, "", "exécution interrompue depuis la Control Tower"
        )
        tache = self._runs.get(run_id)
        if tache is not None and not tache.done():
            tache.cancel()
            await asyncio.wait({tache}, timeout=DELAI_ANNULATION_S)
        return self.resume(run_id)

    async def fermer(self) -> None:
        """Arrête le service : runs en vol annulés, pont déposé, pompe éteinte.

        Appelé à l'arrêt de l'app (lifespan), **avant** la fermeture du bus.
        Aucun run ne survit au process : c'est la contrepartie assumée de la
        tâche de fond (cf. docstring du module).
        """
        en_vol = {t for t in self._runs.values() if not t.done()}
        for tache in en_vol:
            tache.cancel()
        if en_vol:
            await asyncio.wait(en_vol, timeout=DELAI_ANNULATION_S)
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
        self._handler = JournalEventHandler(self._pousser)
        self._logger.addHandler(self._handler)

    async def _derouler(
        self,
        run_id: str,
        objectif: str,
        garde_fous: Guardrails,
        parallelisme: int | None,
        ticket: ReferenceTicket | None = None,
    ) -> None:
        """Déroule le run en tâche de fond et consigne son issue.

        Aucune exception ne remonte : une tâche de fond n'a pas d'appelant à qui
        les rendre — l'échec devient le **statut** du run (visible dans la
        projection et sur le flux), sa cause partant dans le détail. Seule
        l'annulation se propage, pour que la tâche finisse bien « annulée » (son
        issue, elle, a déjà été consignée par `annuler`).

        `ticket` (#187) descend jusqu'au moteur, qui en dote chaque
        tâche du plan : le ticket du run se retrouve ainsi sur chaque carte, par
        le seul chemin du journal — le moteur n'a rien à savoir de son origine.
        """
        journal = RunJournal(run_id=run_id, logger=self._logger)
        try:
            moteur = self._fabrique(guardrails=garde_fous, max_parallele=parallelisme)
            rapport = await moteur.run(
                objectif, journal=journal, ticket=ticket
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOGGER.exception("Exécution %s interrompue par une erreur.", run_id)
            self._consigne(run_id, EXECUTION_ECHEC, "", f"{type(exc).__name__} : {exc}")
            return
        echouees = len(rapport.echouees) + len(rapport.bloquees)
        total = len(rapport.resultats)
        self._consigne(
            run_id,
            EXECUTION_ECHEC if echouees else EXECUTION_TERMINEE,
            "",
            f"{len(rapport.reussies)}/{total} tâche(s) réussie(s)",
        )

    def _consigne(
        self,
        run_id: str,
        statut: str,
        objectif: str,
        detail: str,
        *,
        ticket: ReferenceTicket | None = None,
    ) -> None:
        """Émet le cycle de vie du run : projection d'abord, bus ensuite.

        Même ordre que les endpoints d'écriture de l'app (« état d'abord,
        diffusion ensuite ») : le REST répond déjà à jour, et la pompe
        réapplique l'événement sans effet (idempotence). L'objectif comme le
        détail sont expurgés des secrets avant de partir — ce qui va sur le bus
        est montrable, même filet que le journal (#8). Seul le **lancement**
        porte une référence externe (#187) : la projection ne la retire pas aux
        événements suivants, qui n'en savent rien.
        """
        self._emettre(
            Event(
                type=EVENEMENT_EXECUTION_STATUT,
                run_id=run_id,
                titre=redact_secrets(objectif),
                agent=_ACTEUR_RUN,
                role=_ROLE_RUN,
                statut=statut,
                detail=redact_secrets(detail),
                ticket=ticket,
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
