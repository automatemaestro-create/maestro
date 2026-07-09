"""Démo en ligne de commande de la boucle d'orchestration (ticket #6).

`maestro-run "<objectif>"` déroule la boucle complète (objectif → tâches → agents →
agrégat) et imprime la **synthèse** Markdown ; `--json` imprime plutôt le rapport
structuré. `--trace` émet en plus le journal d'exécution (#8) sur stderr — une
ligne JSON par étape : entrée, sortie, outils, tokens, coût, durée. Fine couche
autour de `OrchestrationEngine.default` : elle sert à *exercer* le flux de bout en
bout contre le vrai fournisseur Claude.

Garde-fous (#9) : `--plafond-cout <usd>` et `--timeout <s>` arment le plafond de
dépense et le time-out **par tâche** ; une tâche classée sensible déclenche une
**demande de validation** posée sur la console (refusée par défaut si l'entrée
n'est pas interactive — fail-safe).

`--queue` (#41) exécute les tâches via la **file Celery + Redis** au lieu du
process courant : Redis lancé (infra/docker-compose.yml) et au moins un worker
démarré (`celery -A maestro.queue worker --pool=solo`) sont requis. Les
garde-fous s'appliquant alors **côté worker**, `--plafond-cout`/`--timeout` ne
sont pas combinables avec `--queue` (une tâche sensible y est refusée par
défaut — fail-safe sans validateur).

`--publier` (#46) publie chaque étape du journal en **événement temps réel**
sur Redis Pub/Sub (canal `maestro.evenements`, via le pont
`maestro.controltower.bridge`) : le backend Control Tower (`maestro-api`) les
rediffuse aux clients WebSocket. Requiert le même Redis que `--queue`.

Code de sortie : 0 si toutes les tâches réussissent, 1 si au moins une échoue (ou
en cas d'erreur de configuration / planification), 2 si l'appel est mal formé.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import sys
from collections.abc import Sequence

from maestro.config import ConfigError
from maestro.engine.guardrails import DemandeValidation, Guardrails
from maestro.engine.loop import OrchestrationEngine
from maestro.orchestrator.errors import OrchestratorError
from maestro.telemetry import LOGGER_NAME

_USAGE = (
    "Usage : maestro-run [--json] [--trace] [--queue] [--publier] [--plafond-cout <usd>] "
    '[--timeout <s>] "<objectif en langage naturel>"'
)


def main(argv: Sequence[str] | None = None) -> int:
    console_tolerante()
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-h", "--help"}:
        print(_USAGE, file=sys.stderr)
        return 0

    as_json = False
    via_queue = False
    plafond_cout: float | None = None
    timeout: float | None = None
    flags_connus = {"--json", "--trace", "--queue", "--publier", "--plafond-cout", "--timeout"}
    while args and args[0] in flags_connus:
        flag = args.pop(0)
        if flag == "--json":
            as_json = True
        elif flag == "--trace":
            activer_trace()
        elif flag == "--queue":
            via_queue = True
        elif flag == "--publier":
            activer_publication_evenements()
        else:
            valeur = _valeur_numerique(flag, args)
            if valeur is None:
                return 2
            if flag == "--plafond-cout":
                plafond_cout = valeur
            else:
                timeout = valeur

    objective = " ".join(args).strip()
    if not objective:
        print(_USAGE, file=sys.stderr)
        return 2

    if via_queue and (plafond_cout is not None or timeout is not None):
        print(
            "--plafond-cout/--timeout ne sont pas combinables avec --queue : les "
            "garde-fous s'appliquent côté worker (maestro.queue.worker.configurer_worker).",
            file=sys.stderr,
        )
        return 2

    try:
        guardrails = Guardrails(
            plafond_cout_usd=plafond_cout,
            timeout_s=timeout,
            validateur=validation_console,
        )
    except ValueError as exc:
        print(f"Garde-fous : {exc}", file=sys.stderr)
        return 2

    try:
        engine = _build_engine(via_queue=via_queue, guardrails=guardrails)
        report = asyncio.run(engine.run(objective))
    except ConfigError as exc:
        print(f"Configuration : {exc}", file=sys.stderr)
        return 1
    except OrchestratorError as exc:
        print(f"Orchestration : {exc}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.synthese())
    return 0 if not report.echouees else 1


def _build_engine(*, via_queue: bool, guardrails: Guardrails) -> OrchestrationEngine:
    """Construit la boucle : locale par défaut, distribuée (file #41) avec `--queue`.

    L'import de `maestro.queue` (donc de Celery) reste local à la branche
    distribuée : le chemin historique n'en dépend pas.
    """
    if via_queue:
        from maestro.queue import create_distributed_engine

        return create_distributed_engine()
    return OrchestrationEngine.default(guardrails=guardrails)


def _valeur_numerique(flag: str, args: list[str]) -> float | None:
    """Consomme et convertit la valeur de `flag` ; None (et message) si invalide."""
    if not args:
        print(f"{flag} attend une valeur numérique.\n{_USAGE}", file=sys.stderr)
        return None
    brut = args.pop(0)
    try:
        return float(brut)
    except ValueError:
        print(f"{flag} attend une valeur numérique (reçu : {brut!r}).", file=sys.stderr)
        return None


def console_tolerante() -> None:
    """Rend stdout/stderr tolérants aux caractères hors de l'encodage de la console.

    Les synthèses imprimées contiennent les livrables des agents — n'importe quel
    Unicode peut s'y trouver, qu'une console Windows héritée (cp1252) ne sait pas
    encoder : sans cela, `print` planterait après une exécution pourtant réussie.
    Les caractères inencodables sont remplacés à l'affichage seulement ; les
    artefacts écrits sur disque restent en UTF-8 intacts. Partagée avec
    `maestro-demo` (ticket #10).
    """
    for flux in (sys.stdout, sys.stderr):
        if isinstance(flux, io.TextIOWrapper):
            flux.reconfigure(errors="replace")


def validation_console(demande: DemandeValidation) -> bool:
    """Demande de validation humaine sur la console (#9) — refus par défaut.

    Bloque la boucle asyncio le temps de la réponse : assumé pour la démo CLI
    (un humain, une console). Une entrée non interactive (EOF) vaut refus —
    même fail-safe que l'absence de validateur.
    """
    print(
        f"\n[Validation requise] {demande.titre} — agent {demande.role} "
        f"(`{demande.agent}`)\n  Raison : {demande.raison}\n"
        f"  Description : {demande.description}",
        file=sys.stderr,
    )
    try:
        reponse = input("Approuver cette action sensible ? [o/N] ")
    except EOFError:
        print("(entrée non interactive — action refusée)", file=sys.stderr)
        return False
    return reponse.strip().lower() in {"o", "oui", "y", "yes"}


def activer_publication_evenements() -> None:
    """Publie le journal (#8) en événements temps réel sur Redis Pub/Sub (#46).

    Branche le pont télémétrie → bus (`maestro.controltower.bridge`) sur le
    Redis de la config (`REDIS_URL`, l'instance du docker-compose par défaut) :
    chaque étape consignée part sur le canal `maestro.evenements`, que le
    backend Control Tower (`maestro-api`) rediffuse en WebSocket. La connexion
    est paresseuse : sans Redis joignable, l'échec de publication est signalé
    sur stderr (politique des handlers logging) sans gêner l'exécution.
    """
    from maestro.config import load_settings
    from maestro.controltower.bridge import activer_publication, publieur_redis

    activer_publication(publieur_redis(load_settings().redis_url))


def activer_trace() -> None:
    """Émet le journal d'exécution (JSON Lines, #8) sur stderr.

    Partagée avec `maestro-demo` (ticket #10) — d'où sa visibilité publique.

    Configure le logger `maestro.trace` sans toucher au root : la synthèse reste
    seule sur stdout, le journal part sur stderr (redirigeable vers un fichier).
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
