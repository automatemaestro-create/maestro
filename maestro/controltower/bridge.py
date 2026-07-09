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
    EVENEMENT_TACHE_STATUT,
    REDIS_URL_DEFAUT,
    Event,
)
from maestro.telemetry import LOGGER_NAME

#: Étape du journal qui n'est pas une tâche : la planification de l'orchestrateur.
_ETAPE_PLANIFICATION = "planification"

#: Suffixe des étapes de validation humaine (cf. `LocalExecutor._valide_si_sensible`).
_SUFFIXE_VALIDATION = ":validation"


def evenements_depuis_step(record: Mapping[str, Any]) -> tuple[Event, ...]:
    """Convertit une ligne de journal (`StepRecord.to_dict`) en événements du bus.

    - l'étape `planification` et les étapes `<tache>:validation` deviennent des
      **activités d'agent** (l'orchestrateur planifie, un humain tranche) ;
    - toute autre étape est l'issue d'une **tâche** : événement `tache.statut`
      portant statut, agent, rôle et coût rapporté (#8).

    Une ligne illisible (pas un objet JSON de journal) ne produit **aucun**
    événement plutôt qu'un événement faux — le flux temps réel est un miroir,
    pas une source de vérité.
    """
    etape = record.get("etape")
    if not isinstance(etape, str) or not etape:
        return ()
    usage = record.get("usage")
    cout_brut = usage.get("cout_usd") if isinstance(usage, Mapping) else None
    est_activite = etape == _ETAPE_PLANIFICATION or etape.endswith(_SUFFIXE_VALIDATION)
    if est_activite:
        tache_id = "" if etape == _ETAPE_PLANIFICATION else etape.removesuffix(_SUFFIXE_VALIDATION)
        detail = str(record.get("sortie") or record.get("erreur") or "")
    else:
        tache_id = etape
        detail = str(record.get("erreur") or "")
    return (
        Event(
            type=EVENEMENT_AGENT_ACTIVITE if est_activite else EVENEMENT_TACHE_STATUT,
            run_id=str(record.get("run_id", "")),
            tache_id=tache_id,
            titre=str(record.get("nom", "")),
            agent=str(record.get("agent", "")),
            role=str(record.get("role", "")),
            statut=str(record.get("statut", "")),
            detail=detail,
            cout_usd=float(cout_brut) if isinstance(cout_brut, int | float) else None,
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
