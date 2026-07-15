"""Pont télémétrie → événements : le journal (#8) alimente la Control Tower (#46).

Les événements temps réel sont **sourcés depuis la télémétrie** : chaque ligne
du journal d'exécution (`RunJournal`, une ligne JSON par étape sur le logger
`maestro.trace`) devient un ou zéro événement du bus. Le pont se pose en
`logging.Handler` (`JournalEventHandler`) : aucun changement du moteur ni du
journal — là où le worker ou l'orchestrateur consigne déjà, la Control Tower
écoute.

Deux pièces :

- `evenements_depuis_step(record)` : la conversion pure d'une ligne de journal
  (forme `StepRecord.to_dict`) en événements — testable sans logger ni Redis ;
- `JournalEventHandler` + `publieur_redis` : le branchement production — le
  handler publie chaque événement via un callable synchrone, typiquement le
  `PUBLISH` Redis (le côté API consomme le canal via `RedisEventBus`).

Le journal amont expurge déjà les secrets (`redact_secrets`) : ce qui part sur
le bus est ce qui partait déjà dans les logs.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from typing import Any

from maestro.controltower.events import (
    CANAL_EVENEMENTS,
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_TACHE_STATUT,
    REDIS_URL_DEFAUT,
    Event,
)
from maestro.telemetry import LOGGER_NAME
from maestro.telemetry.usage import StepUsage

#: Étape du journal qui n'est pas une tâche : la planification de l'orchestrateur.
_ETAPE_PLANIFICATION = "planification"

#: Suffixe des étapes de validation humaine (cf. `LocalExecutor._valide_si_sensible`).
_SUFFIXE_VALIDATION = ":validation"

#: Suffixe des étapes de relance automatique (#91 — cf.
#: `maestro.engine.executor`, `SUFFIXE_ETAPE_RELANCE`).
_SUFFIXE_RELANCE = ":relance"

#: Suffixe des étapes de messagerie inter-agents (#44 — cf.
#: `maestro.messaging.mailbox.consigne_message`, `SUFFIXE_ETAPE_MESSAGE`).
_SUFFIXE_MESSAGE = ":message"


def evenements_depuis_step(record: Mapping[str, Any]) -> tuple[Event, ...]:
    """Convertit une ligne de journal (`StepRecord.to_dict`) en événements du bus.

    - les étapes `<tache>:message` (#44) deviennent des **messages
      inter-agents** (entité AGENT_MESSAGE — handoff, notification…) ;
    - l'étape `planification` et les étapes `<tache>:validation` et
      `<tache>:relance` (#91) deviennent des **activités d'agent**
      (l'orchestrateur planifie, un humain tranche, le moteur relance — la
      raison de chaque relance voyage dans `detail`) ;
    - toute autre étape est l'issue d'une **tâche** : événement `tache.statut`
      portant statut, agent, rôle et coût rapporté (#8).

    Chaque événement embarque la **mesure d'usage** de son étape (tokens,
    coût, durée — forme `StepUsage`) quand le journal en porte une : c'est la
    matière de la comptabilité par tâche côté Control Tower (#57), annexes
    comprises (validation, message — usage nul aujourd'hui, compté s'il vient).

    Une ligne illisible (pas un objet JSON de journal) ne produit **aucun**
    événement plutôt qu'un événement faux — le flux temps réel est un miroir,
    pas une source de vérité.
    """
    etape = record.get("etape")
    if not isinstance(etape, str) or not etape:
        return ()
    usage = record.get("usage")
    mesure = StepUsage.from_dict(usage) if isinstance(usage, Mapping) else None
    cout_brut = usage.get("cout_usd") if isinstance(usage, Mapping) else None
    est_message = etape.endswith(_SUFFIXE_MESSAGE)
    est_activite = etape == _ETAPE_PLANIFICATION or etape.endswith(
        (_SUFFIXE_VALIDATION, _SUFFIXE_RELANCE)
    )
    if est_message:
        type_evenement = EVENEMENT_MESSAGE_INTER_AGENTS
        tache_id = etape.removesuffix(_SUFFIXE_MESSAGE)
        detail = str(record.get("sortie") or "")
    elif est_activite:
        type_evenement = EVENEMENT_AGENT_ACTIVITE
        tache_id = (
            ""
            if etape == _ETAPE_PLANIFICATION
            else etape.removesuffix(_SUFFIXE_VALIDATION).removesuffix(_SUFFIXE_RELANCE)
        )
        detail = str(record.get("sortie") or record.get("erreur") or "")
    else:
        type_evenement = EVENEMENT_TACHE_STATUT
        tache_id = etape
        detail = str(record.get("erreur") or "")
    return (
        Event(
            type=type_evenement,
            run_id=str(record.get("run_id", "")),
            tache_id=tache_id,
            titre=str(record.get("nom", "")),
            agent=str(record.get("agent", "")),
            role=str(record.get("role", "")),
            statut=str(record.get("statut", "")),
            detail=detail,
            cout_usd=float(cout_brut) if isinstance(cout_brut, int | float) else None,
            usage=mesure,
            horodatage=str(record.get("horodatage", "")),
        ),
    )


class JournalEventHandler(logging.Handler):
    """Handler du logger `maestro.trace` : chaque ligne consignée part sur le bus.

    `publier` est un callable **synchrone** (le journal consigne en synchrone) ;
    en production c'est le `PUBLISH` Redis de `publieur_redis`. Une ligne
    inconvertible ou une publication en échec est signalée via `handleError`
    (silencieuse par défaut, comme tout handler logging) : la télémétrie ne
    doit jamais faire échouer l'exécution qu'elle observe.
    """

    def __init__(self, publier: Callable[[Event], None]) -> None:
        super().__init__(level=logging.INFO)
        self._publier = publier

    def emit(self, record: logging.LogRecord) -> None:
        try:
            data = json.loads(record.getMessage())
            if not isinstance(data, dict):
                return
            for evenement in evenements_depuis_step(data):
                self._publier(evenement)
        except Exception:
            self.handleError(record)


def publieur_redis(
    url: str | None = None, *, canal: str = CANAL_EVENEMENTS
) -> Callable[[Event], None]:
    """Construit le callable de publication Redis (synchrone) du pont.

    Client Redis **synchrone** (le handler logging l'est) sur l'instance du
    docker-compose par défaut — le pendant producteur du `RedisEventBus`
    consommé par l'API. La connexion est paresseuse (ouverte au premier
    `PUBLISH`).
    """
    # Import local : seule la publication Redis dépend du client.
    import redis

    client = redis.Redis.from_url(url or REDIS_URL_DEFAUT)

    def publier(evenement: Event) -> None:
        client.publish(canal, evenement.to_json())

    return publier


def activer_publication(publier: Callable[[Event], None]) -> logging.Handler:
    """Branche le pont : le journal (#8) publie désormais ses étapes en événements.

    Pose un `JournalEventHandler` sur le logger `maestro.trace` et le renvoie
    (à retirer via `logging.getLogger(LOGGER_NAME).removeHandler(...)` pour
    débrancher). S'utilise côté **producteur** : process de l'orchestrateur
    (`maestro-run --publier`) comme workers de la file (#41).
    """
    handler = JournalEventHandler(publier)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return handler
