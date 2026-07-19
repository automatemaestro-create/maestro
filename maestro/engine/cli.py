"""Démo en ligne de commande de la boucle d'orchestration (ticket #6).

`maestro-run "<objectif>"` déroule la boucle complète (objectif → tâches → agents →
agrégat) et imprime la **synthèse** Markdown ; `--json` imprime plutôt le rapport
structuré. `--trace` émet en plus le journal d'exécution (#8) sur stderr — une
ligne JSON par étape : entrée, sortie, outils, tokens, coût, durée. Fine couche
autour de `OrchestrationEngine.default` : elle sert à *exercer* le flux de bout en
bout contre le vrai fournisseur Claude.

Garde-fous (#9) : `--plafond-cout <usd>` arme le plafond de dépense de
l'**exécution entière** (adossé à la comptabilité par tâche, #56) et
`--timeout <s>` le time-out **par tâche** ; une tâche classée sensible déclenche
une **demande de validation** posée sur la console (refusée par défaut si
l'entrée n'est pas interactive — fail-safe).

Relance automatique (#91, ENF-06) : les échecs **transitoires** (aléa
fournisseur — erreur immédiate, crash du sous-processus SDK) sont relancés avec
backoff, **2 relances par défaut** (3 tentatives, `maestro.engine.retry`).
`--relances <n>` ajuste le nombre de relances (`0` : désactivé). Les échecs non
transitoires (time-out, plafonds, refus de validation) ne sont jamais relancés.

`--parallele <n>` (#100) pose le **plafond global** de concurrence du run — le
plafond transverse, prioritaire sur la capacité par agent (#86) qui s'applique
en dessous : utile pour ménager les limites de débit du fournisseur sur un run
de charge (les aléas croissent avec la concurrence, docs/13 §4.3). Sans le
flag : illimité (les plans restent petits).

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

`--validation-ui` (#48) route les demandes de validation humaine vers la
**Control Tower** au lieu de la console : la tâche sensible passe en pause, la
demande apparaît dans l'UI (contexte : agent, tâche, action, justification) et
l'exécution reprend (ou s'annule) selon la décision prise depuis l'UI — sans
time-out silencieux. Requiert le même Redis que `--publier` (et `maestro-api`
lancé) ; non combinable avec `--queue` (les garde-fous s'appliquent alors côté
worker, qui refuse les tâches sensibles — fail-safe).

`--messagerie` (#44) active la **messagerie inter-agents** (boîtes aux lettres
Redis Pub/Sub, `maestro.messaging`) : l'agent qui termine une tâche à
dépendants annonce l'issue par message (handoff) et chaque tâche aval attend ce
message avant de démarrer. L'échange est journalisé (visible avec `--trace`, et
dans la Control Tower avec `--publier`). Requiert le même Redis que `--queue`.

`--notifier <agent>` (#105) branche les **notifications de supervision Slack**
(`maestro.supervision`) : l'agent nommé (ex. `devops`), équipé de son serveur
MCP Slack déclaré (#104, `core/mcp/<agent>.json`), poste sur le canal
`MAESTRO_SLACK_CANAL` la fin de run (bilan tâches/coût) et chaque validation
humaine en attente — best-effort : un échec de notification est consigné au
journal sans altérer le run. Non combinable avec `--queue` (les garde-fous, donc
la notification de validation, s'appliquent côté worker).

L'export **Langfuse** (#81) ne passe pas par une option : il est purement
configuratif. Dès que `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` sont dans
l'environnement, chaque exécution produit sa trace Langfuse (étapes, outils
appelés, durées, tokens et coûts par tâche — cf. `maestro.telemetry.langfuse`)
et reçoit en fin de run ses **scores d'évaluation** (#80 : réussite globale,
taux de tâches réussies) ; sans elles, rien ne change.

Code de sortie : 0 si toutes les tâches réussissent, 1 si au moins une échoue (ou
en cas d'erreur de configuration / planification), 2 si l'appel est mal formé.
"""

from __future__ import annotations

import io
import json
import logging
import sys
from collections.abc import Sequence

from maestro.config import ConfigError
from maestro.engine.guardrails import DemandeValidation, Guardrails, Validateur
from maestro.engine.loop import OrchestrationEngine
from maestro.engine.retry import RELANCE_DEFAUT, PolitiqueRelance
from maestro.engine.runner import run_borne
from maestro.orchestrator.errors import OrchestratorError
from maestro.telemetry import (
    LOGGER_NAME,
    RunJournal,
    activer_export_langfuse,
    evaluer_run_langfuse,
    redact_secrets,
)

_USAGE = (
    "Usage : maestro-run [--json] [--trace] [--queue] [--publier] [--messagerie] "
    "[--validation-ui] [--notifier <agent>] [--plafond-cout <usd>] [--timeout <s>] "
    '[--relances <n>] [--parallele <n>] "<objectif en langage naturel>"'
)


def main(argv: Sequence[str] | None = None) -> int:
    console_tolerante()
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-h", "--help"}:
        print(_USAGE, file=sys.stderr)
        return 0

    as_json = False
    via_queue = False
    messagerie = False
    validation_ui = False
    notifier_agent: str | None = None
    plafond_cout: float | None = None
    timeout: float | None = None
    relances: int | None = None
    parallele: int | None = None
    flags_connus = {
        "--json", "--trace", "--queue", "--publier", "--messagerie",
        "--validation-ui", "--notifier", "--plafond-cout", "--timeout",
        "--relances", "--parallele",
    }
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
        elif flag == "--messagerie":
            messagerie = True
        elif flag == "--validation-ui":
            validation_ui = True
        elif flag == "--notifier":
            if not args or args[0].startswith("--"):
                print(
                    "--notifier attend un nom d'agent équipé MCP (ex. devops).",
                    file=sys.stderr,
                )
                return 2
            notifier_agent = args.pop(0)
        else:
            valeur = _valeur_numerique(flag, args)
            if valeur is None:
                return 2
            if flag == "--plafond-cout":
                plafond_cout = valeur
            elif flag == "--relances":
                if valeur != int(valeur) or valeur < 0:
                    print(
                        f"--relances attend un entier ≥ 0 (reçu : {valeur:g}).",
                        file=sys.stderr,
                    )
                    return 2
                relances = int(valeur)
            elif flag == "--parallele":
                if valeur != int(valeur) or valeur < 1:
                    print(
                        f"--parallele attend un entier ≥ 1 (reçu : {valeur:g}).",
                        file=sys.stderr,
                    )
                    return 2
                parallele = int(valeur)
            else:
                timeout = valeur

    objective = " ".join(args).strip()
    if not objective:
        print(_USAGE, file=sys.stderr)
        return 2

    if via_queue and (plafond_cout is not None or timeout is not None or relances is not None):
        print(
            "--plafond-cout/--timeout/--relances ne sont pas combinables avec --queue : "
            "garde-fous et relance s'appliquent côté worker "
            "(maestro.queue.worker.configurer_worker).",
            file=sys.stderr,
        )
        return 2
    if via_queue and validation_ui:
        print(
            "--validation-ui n'est pas combinable avec --queue : les garde-fous "
            "s'appliquent côté worker, qui refuse les tâches sensibles (fail-safe).",
            file=sys.stderr,
        )
        return 2
    if via_queue and notifier_agent is not None:
        print(
            "--notifier n'est pas combinable avec --queue : les garde-fous (donc la "
            "notification de validation) s'appliquent côté worker — lancez le run "
            "en local pour la supervision Slack (#105).",
            file=sys.stderr,
        )
        return 2

    journal = RunJournal()
    # Notificateur de supervision (#105) : construit avant les garde-fous — sa
    # configuration (canal, agent équipé, déclaration MCP) échoue proprement
    # avant tout appel modèle, et son enveloppe s'ajoute au validateur choisi.
    notificateur = None
    if notifier_agent is not None:
        from maestro.supervision import NotificateurRun

        try:
            notificateur = NotificateurRun.default(notifier_agent)
        except ConfigError as exc:
            print(f"Configuration : {exc}", file=sys.stderr)
            return 1
    validateur: Validateur = _validateur_ui() if validation_ui else validation_console
    if notificateur is not None:
        validateur = notificateur.validateur_notifiant(validateur, journal)

    try:
        guardrails = Guardrails(
            plafond_cout_usd=plafond_cout,
            timeout_s=timeout,
            validateur=validateur,
        )
    except ValueError as exc:
        print(f"Garde-fous : {exc}", file=sys.stderr)
        return 2

    # Export Langfuse (#81) : purement configuratif — no-op sans clés dans l'env.
    activer_export_langfuse()

    try:
        engine = _build_engine(
            via_queue=via_queue,
            guardrails=guardrails,
            messagerie=messagerie,
            relance=_politique_relance(relances),
            max_parallele=parallele,
        )
        # Arrêt borné (#64) : une réalisation détachée par le time-out ne peut pas
        # suspendre la fermeture de la boucle — le rapport est toujours rendu.
        report = run_borne(engine.run(objective, journal=journal))
    except ConfigError as exc:
        print(f"Configuration : {exc}", file=sys.stderr)
        return 1
    except OrchestratorError as exc:
        print(f"Orchestration : {exc}", file=sys.stderr)
        return 1

    if notificateur is not None:
        # Fin de run (#105) : le bilan part sur le canal de supervision — avant
        # l'évaluation, pour que l'étape de notification parte aussi sur la
        # trace. Best-effort : un échec est consigné au journal, jamais levé.
        run_borne(notificateur.fin_de_run(report, journal))

    # Évaluation (#80) : les scores de l'exécution partent sur sa trace Langfuse —
    # même bascule configurative que l'export, no-op sans clés.
    evaluer_run_langfuse(journal)

    # Restitution expurgée (#115, même exigence que la trace) : un secret servi
    # pendant le run (canal Figma, token…) peut ressurgir dans l'objectif cité
    # ou dans un livrable de l'agent — masqué ici comme partout ailleurs.
    if as_json:
        print(redact_secrets(json.dumps(report.to_dict(), ensure_ascii=False, indent=2)))
    else:
        print(redact_secrets(report.synthese()))
    return 0 if not report.echouees else 1


def _politique_relance(relances: int | None) -> PolitiqueRelance | None:
    """Traduit `--relances <n>` en politique (#91) : n relances = n+1 tentatives.

    Sans le flag (None), la politique par défaut du moteur (2 relances) ; `0`
    désactive la relance (comportement d'avant ENF-06).
    """
    if relances is None:
        return RELANCE_DEFAUT
    if relances == 0:
        return None
    return PolitiqueRelance(max_tentatives=relances + 1)


def _build_engine(
    *,
    via_queue: bool,
    guardrails: Guardrails,
    messagerie: bool = False,
    relance: PolitiqueRelance | None = None,
    max_parallele: int | None = None,
) -> OrchestrationEngine:
    """Construit la boucle : locale par défaut, distribuée (file #41) avec `--queue`.

    L'import de `maestro.queue` (donc de Celery) reste local à la branche
    distribuée : le chemin historique n'en dépend pas. `--messagerie` (#44)
    branche les boîtes aux lettres Redis Pub/Sub (l'instance de la config,
    comme `--publier`) — la connexion est paresseuse, et une publication en
    échec est abandonnée sans gêner l'exécution (relais résilient). `relance`
    (#91) ne s'applique qu'en local — côté file, chaque worker câble la sienne.
    `max_parallele` (#100) pose le plafond global du run sur les deux chemins.
    """
    mailbox = None
    if messagerie:
        from maestro.config import load_settings
        from maestro.messaging import RedisMailbox

        mailbox = RedisMailbox(load_settings().redis_url)
    if via_queue:
        from maestro.queue import create_distributed_engine

        return create_distributed_engine(mailbox=mailbox, max_parallele=max_parallele)
    return OrchestrationEngine.default(
        guardrails=guardrails, mailbox=mailbox, relance=relance, max_parallele=max_parallele
    )


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


def _validateur_ui() -> Validateur:
    """Construit le validateur Control Tower (#48) sur le Redis de la config.

    Les demandes de validation partent sur le canal `maestro.evenements` (l'UI
    les affiche via `maestro-api`) et la décision humaine en revient par le
    même canal. Import local : seul ce chemin dépend de la Control Tower.
    """
    from maestro.config import load_settings
    from maestro.controltower.validation import validateur_redis

    return validateur_redis(load_settings().redis_url)


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
