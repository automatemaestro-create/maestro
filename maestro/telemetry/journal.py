"""Journal d'exécution structuré — une ligne JSON par étape (ticket #8).

Chaque exécution reçoit un `run_id` ; chaque étape (planification, tâche) est
consignée en `StepRecord` : entrée, sortie, outils, tokens, coût, durée, issue.
Les enregistrements sont émis en **JSON Lines** sur le logger `maestro.trace`
(silencieux tant qu'aucun handler n'est configuré — cf. `maestro-run --trace`).

Le format plat `run_id` / `etape` / `horodatage` / `usage` est pensé pour se
mapper plus tard sur les traces et observations de **Langfuse** sans changer les
appelants : brancher un exporteur suffira.

Aucun secret ne doit atteindre les logs : entrée, sortie et erreur passent par
`redact_secrets` avant d'être conservées ou émises.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from maestro.telemetry.redact import redact_secrets
from maestro.telemetry.usage import StepUsage

#: Logger d'émission du journal (une ligne JSON par étape).
LOGGER_NAME = "maestro.trace"


@dataclass(frozen=True)
class StepRecord:
    """Trace d'une étape : qui a fait quoi, avec quelle entrée, quelle issue, quel coût.

    `etape` est l'identifiant de l'étape dans l'exécution (id de tâche, ou
    « planification ») ; `entree`/`sortie`/`erreur` sont déjà expurgées des secrets.
    """

    run_id: str
    etape: str
    nom: str
    agent: str
    role: str
    statut: str
    horodatage: str
    entree: str
    sortie: str
    erreur: str | None
    usage: StepUsage

    def to_dict(self) -> dict[str, Any]:
        """Réémet la trace en dict JSON-sérialisable (la ligne du journal)."""
        return {
            "run_id": self.run_id,
            "etape": self.etape,
            "nom": self.nom,
            "agent": self.agent,
            "role": self.role,
            "statut": self.statut,
            "horodatage": self.horodatage,
            "entree": self.entree,
            "sortie": self.sortie,
            "erreur": self.erreur,
            "usage": self.usage.to_dict(),
        }


class RunJournal:
    """Journal d'une exécution : consigne chaque étape et émet sa ligne JSON.

    Conserve les enregistrements en mémoire (inspection, agrégation) en plus de
    les émettre sur le logger — c'est l'agrégat mémoire qui alimente le coût
    total, le logger n'étant qu'un canal de sortie.
    """

    def __init__(self, *, run_id: str | None = None, logger: logging.Logger | None = None) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self._logger = logger or logging.getLogger(LOGGER_NAME)
        self._records: list[StepRecord] = []

    @property
    def records(self) -> tuple[StepRecord, ...]:
        """Les étapes consignées, dans l'ordre d'exécution."""
        return tuple(self._records)

    @property
    def usage_totale(self) -> StepUsage:
        """Agrégat de l'usage de toutes les étapes consignées."""
        total = StepUsage()
        for record in self._records:
            total = total.fusion(record.usage)
        return total

    def consigne(
        self,
        *,
        etape: str,
        nom: str,
        agent: str,
        role: str,
        statut: str,
        entree: str,
        sortie: str,
        usage: StepUsage,
        erreur: str | None = None,
    ) -> StepRecord:
        """Consigne une étape (textes expurgés des secrets) et émet sa ligne JSON."""
        record = StepRecord(
            run_id=self.run_id,
            etape=etape,
            nom=nom,
            agent=agent,
            role=role,
            statut=statut,
            horodatage=datetime.now(UTC).isoformat(timespec="seconds"),
            entree=redact_secrets(entree),
            sortie=redact_secrets(sortie),
            erreur=redact_secrets(erreur) if erreur is not None else None,
            usage=usage,
        )
        self._records.append(record)
        self._logger.info(json.dumps(record.to_dict(), ensure_ascii=False))
        return record
