"""Exporteur Langfuse du journal d'exécution — traces et coûts (ticket #81).

Le journal (#8) consigne chaque étape d'une exécution en JSON Lines sur le
logger `maestro.trace`, dans un format pensé pour se mapper sur les traces et
observations de Langfuse : cet exporteur est ce branchement. Posé en
`logging.Handler` (même motif que le pont Control Tower,
`maestro.controltower.bridge`), il convertit chaque ligne consignée en
événements de l'API d'ingestion Langfuse (`POST /api/public/ingestion`) :

- l'exécution devient une **trace** (id = `run_id` : la trace se retrouve
  depuis le rapport, le journal ou la Control Tower), son entrée est
  l'objectif porté par l'étape de planification ;
- chaque étape devient une **observation** : une *génération* si elle a
  consommé des appels modèle — tokens et coût de la comptabilité par tâche
  (#55) portés au format natif Langfuse, donc visibles par observation et
  agrégés par trace —, un *span* sinon (validation humaine, message
  inter-agents, blocage). Agent, rôle, statut et outils appelés partent en
  métadonnées ; la durée mesurée (#8) replace l'étape dans la chronologie.

L'intégration est **optionnelle** : sans `LANGFUSE_PUBLIC_KEY` +
`LANGFUSE_SECRET_KEY` dans la config, `activer_export_langfuse` ne branche
rien et le système fonctionne à l'identique. L'export parle l'API HTTP
publique via le client httpx déjà présent — même choix que le fournisseur
compatible OpenAI (#69) : pas de SDK dédié pour un dialecte stable.

En mode file (#41), l'export s'active côté **orchestrateur** seulement : le
résultat de chaque tâche (usage worker compris) y est déjà consigné par
`CeleryExecutor` — activer aussi les workers compterait chaque tâche deux fois.

L'**évaluation** des exécutions (#80) complète l'export : en fin de run,
`evaluer_run_langfuse` pose sur la trace des **scores** dérivés du journal —
réussite globale et taux de tâches réussies — via la même API d'ingestion,
exploitables dans Langfuse pour filtrer, agréger et comparer les exécutions.
Même bascule configurative, même résilience : sans clés, aucun envoi ; un
Langfuse injoignable n'affecte jamais l'exécution évaluée.

Le journal amont expurge déjà les secrets (`redact_secrets`) : ce qui part
vers Langfuse est ce qui partait déjà dans les logs.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from maestro.config import Settings, load_settings
from maestro.telemetry.costs import ETAPE_PLANIFICATION, RunCost
from maestro.telemetry.journal import LOGGER_NAME, RunJournal

#: Délai maximal d'une publication (connexion + réponse) : l'export est synchrone
#: (handler logging), un Langfuse injoignable ralentit l'étape mais ne suspend
#: jamais l'exécution — l'échec part dans `handleError`.
_TIMEOUT_S = 10.0

#: Nom des traces exportées — une trace = une exécution, reliée par `run_id`.
_NOM_TRACE = "maestro-run"

#: Niveau Langfuse par statut d'étape (défaut : DEFAULT). `echec` est une erreur ;
#: `bloquee` (#43) et `refuse` (#9) signalent sans être des erreurs de l'étape.
_NIVEAUX = {"echec": "ERROR", "bloquee": "WARNING", "refuse": "WARNING"}

#: Seule issue réussie d'une tâche (convention de statut du journal,
#: cf. `maestro.engine.executor.STATUT_TERMINEE`) — échec, blocage ou refus n'en sont pas.
_STATUT_REUSSI = "terminee"

#: Noms des scores d'évaluation d'une exécution (#80), posés sur sa trace :
#: la réussite globale (booléen) et la part de tâches réussies (0..1).
SCORE_RUN_REUSSI = "run-reussi"
SCORE_TAUX_REUSSITE = "taux-reussite"


def evenements_depuis_step(record: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Convertit une ligne de journal (`StepRecord.to_dict`) en événements d'ingestion.

    Deux événements par ligne : l'**upsert de la trace** de l'exécution (id =
    `run_id` — les upserts successifs fusionnent ; seule l'étape de
    planification pose l'horodatage de la trace et son entrée, l'objectif) et
    l'**observation** de l'étape — génération si elle a consommé des appels
    modèle (tokens et coût au format natif, #55), span sinon. Une ligne
    illisible (pas une étape de journal) ne produit **aucun** événement plutôt
    qu'un événement faux — l'export est un miroir, pas une source de vérité.
    """
    etape = record.get("etape")
    run_id = record.get("run_id")
    if not isinstance(etape, str) or not etape or not isinstance(run_id, str) or not run_id:
        return ()
    brut = record.get("usage")
    usage: dict[str, Any] = dict(brut) if isinstance(brut, Mapping) else {}
    fin = str(record.get("horodatage", ""))
    debut = _debut_depuis(fin, usage.get("duree_ms"))
    trace: dict[str, Any] = {"id": run_id, "name": _NOM_TRACE}
    if etape == ETAPE_PLANIFICATION:
        trace["timestamp"] = debut
        trace["input"] = record.get("entree", "")
    statut = str(record.get("statut", ""))
    erreur = record.get("erreur")
    observation: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "traceId": run_id,
        "name": str(record.get("nom") or etape),
        "startTime": debut,
        "endTime": fin,
        "input": record.get("entree", ""),
        "output": record.get("sortie", ""),
        "level": _NIVEAUX.get(statut, "DEFAULT"),
        "statusMessage": erreur if isinstance(erreur, str) else None,
        "metadata": {
            "etape": etape,
            "agent": record.get("agent", ""),
            "role": record.get("role", ""),
            "statut": statut,
            "outils": usage.get("outils", []),
            "usage": usage,
            "playbook_version": record.get("playbook_version"),
        },
    }
    appels = usage.get("appels")
    if isinstance(appels, int) and appels > 0:
        type_observation = "generation-create"
        observation["usage"] = _usage_langfuse(usage)
    else:
        type_observation = "span-create"
    return (
        _evenement("trace-create", fin, trace),
        _evenement(type_observation, fin, observation),
    )


def _evenement(type_evenement: str, horodatage: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """Enveloppe d'ingestion Langfuse : id d'événement unique (déduplication serveur)."""
    return {
        "id": str(uuid.uuid4()),
        "type": type_evenement,
        "timestamp": horodatage,
        "body": dict(body),
    }


def _usage_langfuse(usage: Mapping[str, Any]) -> dict[str, Any]:
    """Le bloc `usage` natif d'une génération Langfuse : tokens, et coût s'il est connu.

    Le coût est celui rapporté par le fournisseur via la télémétrie (#55) —
    absent (None) quand le fournisseur n'en rapporte pas, jamais évalué ici.
    """
    bloc: dict[str, Any] = {
        "input": int(usage.get("tokens_entree") or 0),
        "output": int(usage.get("tokens_sortie") or 0),
        "unit": "TOKENS",
    }
    cout = usage.get("cout_usd")
    if isinstance(cout, int | float):
        bloc["totalCost"] = float(cout)
    return bloc


def _debut_depuis(horodatage: str, duree_ms: Any) -> str:
    """L'horodatage de début d'une étape : sa fin consignée moins sa durée mesurée.

    Le journal consigne à l'achèvement (`horodatage` = fin d'étape) ; la durée
    horloge (#8) permet de replacer le début — l'étendue de l'observation dans
    la chronologie Langfuse. Sans durée (étape annexe à usage nul) ou
    horodatage illisible, le début retombe sur la fin (observation ponctuelle).
    """
    if not isinstance(duree_ms, int | float) or duree_ms <= 0:
        return horodatage
    try:
        fin = datetime.fromisoformat(horodatage)
    except ValueError:
        return horodatage
    return (fin - timedelta(milliseconds=duree_ms)).isoformat(timespec="seconds")


class LangfuseExportHandler(logging.Handler):
    """Handler du logger `maestro.trace` : chaque ligne consignée part vers Langfuse.

    `publier` est un callable **synchrone** (le journal consigne en synchrone) ;
    en production c'est le `POST` d'ingestion de `publieur_langfuse`. Une ligne
    inconvertible ou une publication en échec est signalée via `handleError`
    (silencieuse par défaut, comme tout handler logging) : l'export ne doit
    jamais faire échouer l'exécution qu'il observe.
    """

    def __init__(self, publier: Callable[[Sequence[dict[str, Any]]], None]) -> None:
        super().__init__(level=logging.INFO)
        self._publier = publier

    def emit(self, record: logging.LogRecord) -> None:
        try:
            data = json.loads(record.getMessage())
            if not isinstance(data, dict):
                return
            evenements = evenements_depuis_step(data)
            if evenements:
                self._publier(evenements)
        except Exception:
            self.handleError(record)


def publieur_langfuse(settings: Settings) -> Callable[[Sequence[dict[str, Any]]], None]:
    """Construit le callable de publication HTTP (synchrone) vers Langfuse.

    Client httpx **synchrone** (le handler logging l'est), authentifié en Basic
    (clé publique / clé secrète) sur l'API d'ingestion par lots de
    `LANGFUSE_HOST`. La connexion est paresseuse (ouverte au premier envoi) et
    chaque envoi est borné (`_TIMEOUT_S`).
    """
    # Import local : seule la publication dépend du client HTTP (cf. bridge Redis).
    import httpx2

    client = httpx2.Client(
        auth=(settings.langfuse_public_key or "", settings.langfuse_secret_key or ""),
        timeout=_TIMEOUT_S,
    )
    url = f"{settings.langfuse_host.rstrip('/')}/api/public/ingestion"

    def publier(evenements: Sequence[dict[str, Any]]) -> None:
        client.post(url, json={"batch": list(evenements)}).raise_for_status()

    return publier


def activer_export_langfuse(settings: Settings | None = None) -> logging.Handler | None:
    """Branche l'export Langfuse si la config le demande ; sinon ne fait rien (None).

    La bascule est purement configurative (intégration optionnelle, #81) : sans
    les deux clés (`LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY`), aucun handler
    n'est posé et le système fonctionne à l'identique. Avec elles, un
    `LangfuseExportHandler` est posé sur le logger `maestro.trace` : chaque
    exécution journalisée (#8) produit sa trace Langfuse, coûts compris (#55).
    S'active côté orchestrateur (cf. docstring du module) ; renvoie le handler
    posé (à retirer via `logging.getLogger(LOGGER_NAME).removeHandler(...)`).
    """
    settings = settings or load_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    handler = LangfuseExportHandler(publieur_langfuse(settings))
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return handler


def scores_depuis_journal(journal: RunJournal) -> tuple[dict[str, Any], ...]:
    """Évalue une exécution consignée : les événements `score-create` de sa trace (#80).

    L'évaluation relit la comptabilité par tâche (#55) — la télémétrie est la
    source unique de l'issue, aucun verdict parallèle — et pose deux scores sur
    la trace de l'exécution (`run_id`), exploitables dans Langfuse (filtres,
    agrégats, comparaison entre exécutions) :

    - **`run-reussi`** (booléen) : 1 si toutes les tâches sont terminées avec
      succès, 0 dès qu'une a échoué ou été bloquée (#43) ;
    - **`taux-reussite`** (numérique, 0..1) : la part de tâches réussies.

    Le commentaire de chaque score détaille le décompte (réussites, échecs,
    blocages). Un journal sans aucune tâche (planification seule, ou échouée)
    n'a rien à évaluer : aucun score plutôt qu'un score faux.
    """
    taches = RunCost.depuis_journal(journal).taches
    if not taches:
        return ()
    reussies = sum(1 for t in taches if t.statut == _STATUT_REUSSI)
    echecs = sum(1 for t in taches if t.statut == "echec")
    bloquees = sum(1 for t in taches if t.statut == "bloquee")
    commentaire = (
        f"{reussies}/{len(taches)} tâche(s) réussie(s) — "
        f"{echecs} échec(s), {bloquees} bloquée(s)."
    )
    horodatage = datetime.now(UTC).isoformat(timespec="seconds")
    scores = (
        (SCORE_RUN_REUSSI, "BOOLEAN", 1 if reussies == len(taches) else 0),
        (SCORE_TAUX_REUSSITE, "NUMERIC", reussies / len(taches)),
    )
    return tuple(
        _evenement(
            "score-create",
            horodatage,
            {
                "id": str(uuid.uuid4()),
                "traceId": journal.run_id,
                "name": nom,
                "value": valeur,
                "dataType": type_score,
                "comment": commentaire,
            },
        )
        for nom, type_score, valeur in scores
    )


def evaluer_run_langfuse(
    journal: RunJournal, settings: Settings | None = None
) -> tuple[dict[str, Any], ...]:
    """Publie les scores d'évaluation d'une exécution ; no-op sans clés Langfuse (#80).

    À appeler en **fin d'exécution** (rapport rendu, journal complet) : les
    scores dérivés du journal partent sur la trace que l'exporteur (#81) a
    construite au fil des étapes, via la même API d'ingestion. Même politique
    que l'export : purement configuratif (sans les deux clés, aucun envoi,
    fonctionnement identique — le mode dégradé) et **résilient** (un Langfuse
    injoignable est signalé en avertissement sur le logger du module, jamais
    propagé à l'exécution évaluée). Renvoie les événements publiés — vide si
    l'intégration est désactivée, s'il n'y a rien à évaluer ou si la
    publication a échoué.
    """
    settings = settings or load_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return ()
    evenements = scores_depuis_journal(journal)
    if not evenements:
        return ()
    try:
        publieur_langfuse(settings)(evenements)
    except Exception:
        logging.getLogger(__name__).warning(
            "Scores Langfuse non publiés (run %s) — l'exécution n'est pas affectée.",
            journal.run_id,
            exc_info=True,
        )
        return ()
    return evenements
