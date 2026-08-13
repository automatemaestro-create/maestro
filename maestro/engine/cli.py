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
l'entrée n'est pas interactive — fail-safe). `--plafond-tokens <n>` arme un
plafond **en tokens** : le seul opérant sur un fournisseur qui ne rapporte pas de
coût (`--plafond-cout` reste alors sans prise, #113). Les deux sont cumulables ; la
synthèse dit lequel a tenu.

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

`--durable` (#95) fait du run un **workflow Temporal** durable (un run = un
workflow, une tâche = une activité, `maestro.durable`) : Temporal lancé
(infra/docker-compose.yml) est requis. Un worker est embarqué dans ce process le
temps du run — les garde-fous (plafond, time-out, validation console), la relance
(#91) et la publication (#46) opèrent donc comme en local ; planification,
dépendances/handoffs et blocages passent par le workflow. Mode **opt-in** : sans
le flag, l'exécution en process reste le défaut. Non combinable avec `--queue`
(frontières d'exécution exclusives) ni, à ce stade, avec `--messagerie`/
`--validation-ui`/`--notifier`/`--parallele`.

`--reprendre <run_id>` (#96) **reprend un run durable interrompu** au lieu d'en
lancer un nouveau : le run vit côté Temporal, pas dans ce process — une CLI tuée
(ou une API redémarrée) ne le perd pas. Le `run_id` est celui qu'affichent le
journal, la trace et la Control Tower. Les tâches déjà réussies ne sont **pas
ré-exécutées** (leur résultat vient de l'historique) : l'amont n'est pas repayé,
et la reprise est consignée avec sa raison. Le flag implique `--durable` et se
passe d'objectif (il est déjà dans le run repris). Si c'est le **worker** qui est
tombé, aucune commande n'est nécessaire : le run repart dès qu'un worker revient
sur la file (`python -m maestro.durable.worker`).

`--brief <sans|auto|humain>` (#320) choisit le **régime du brief** — l'étape de
cadrage qui précède la décomposition (#318, décision D5). Le défaut est `sans` :
un `maestro-run` décompose l'objectif brut, comme avant ce lot. `auto` rédige le
brief et décompose **ce brief**, sans attendre d'approbation — c'est le mode d'un
lancement sans humain devant, et le seul qu'une orchestration autonome doive
employer : un run headless qui attend une approbation est un run mort. `humain`
arrête le run sur son brief et attend la décision depuis la Control Tower ; il
exige donc `--validation-ui` (même Redis, même canal — sans quoi personne ne
recevrait la demande) et n'est pas combinable avec `--durable`. Le mode retenu est
**annoncé dans la synthèse**, y compris `sans` : savoir qu'un run n'a attendu
personne est une information, pas une section manquante.

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
from typing import TYPE_CHECKING, cast

from maestro.config import ConfigError
from maestro.engine.brief import (
    MODE_BRIEF_HUMAIN,
    MODE_BRIEF_SANS,
    MODES_BRIEF,
    ArbitreBrief,
    ArbitreClarification,
    BriefRefuse,
)
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

if TYPE_CHECKING:
    from maestro.durable import DurableEngine

_USAGE = (
    "Usage : maestro-run [--json] [--trace] [--queue] [--durable] "
    "[--reprendre <run_id>] [--publier] "
    "[--messagerie] [--validation-ui] [--notifier <agent>] [--plafond-cout <usd>] "
    "[--plafond-tokens <n>] [--timeout <s>] [--brief sans|auto|humain] "
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
    via_durable = False
    reprendre: str | None = None
    messagerie = False
    validation_ui = False
    notifier_agent: str | None = None
    plafond_cout: float | None = None
    plafond_tokens: int | None = None
    timeout: float | None = None
    relances: int | None = None
    parallele: int | None = None
    mode_brief = MODE_BRIEF_SANS
    flags_connus = {
        "--json", "--trace", "--queue", "--durable", "--reprendre", "--publier",
        "--messagerie", "--validation-ui", "--notifier", "--plafond-cout",
        "--plafond-tokens", "--timeout", "--relances", "--parallele", "--brief",
    }
    while args and args[0] in flags_connus:
        flag = args.pop(0)
        if flag == "--json":
            as_json = True
        elif flag == "--trace":
            activer_trace()
        elif flag == "--queue":
            via_queue = True
        elif flag == "--durable":
            via_durable = True
        elif flag == "--reprendre":
            if not args or args[0].startswith("--"):
                print(
                    "--reprendre attend le run_id du run durable à reprendre "
                    "(celui du journal / de la Control Tower).",
                    file=sys.stderr,
                )
                return 2
            reprendre = args.pop(0)
            # Reprendre, c'est se rattacher à un workflow Temporal : le mode
            # durable est impliqué, pas à redemander.
            via_durable = True
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
        elif flag == "--brief":
            if not args or args[0].startswith("--"):
                print(
                    f"--brief attend un mode : {' | '.join(MODES_BRIEF)}.",
                    file=sys.stderr,
                )
                return 2
            mode_brief = args.pop(0).strip().lower()
            if mode_brief not in MODES_BRIEF:
                print(
                    f"--brief : mode inconnu {mode_brief!r} "
                    f"(attendu : {' | '.join(MODES_BRIEF)}).",
                    file=sys.stderr,
                )
                return 2
        else:
            valeur = _valeur_numerique(flag, args)
            if valeur is None:
                return 2
            if flag == "--plafond-cout":
                plafond_cout = valeur
            elif flag == "--plafond-tokens":
                if valeur != int(valeur) or valeur < 1:
                    print(
                        f"--plafond-tokens attend un entier ≥ 1 (reçu : {valeur:g}).",
                        file=sys.stderr,
                    )
                    return 2
                plafond_tokens = int(valeur)
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
    if not objective and reprendre is None:
        print(_USAGE, file=sys.stderr)
        return 2
    if objective and reprendre is not None:
        print(
            f"--reprendre reprend le run « {reprendre} » : son objectif est déjà "
            "celui du run repris, ne le redonnez pas en argument.",
            file=sys.stderr,
        )
        return 2

    if via_queue and (
        plafond_cout is not None
        or plafond_tokens is not None
        or timeout is not None
        or relances is not None
    ):
        print(
            "--plafond-cout/--plafond-tokens/--timeout/--relances ne sont pas "
            "combinables avec --queue : garde-fous et relance s'appliquent côté worker "
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

    if via_durable and via_queue:
        print(
            "--durable et --queue sont deux frontières d'exécution exclusives "
            "(workflow Temporal vs file Celery) : choisissez-en une.",
            file=sys.stderr,
        )
        return 2
    if via_durable and (messagerie or validation_ui or notifier_agent is not None):
        print(
            "--durable ne se combine pas encore avec --messagerie/--validation-ui/"
            "--notifier (#95, lot 2/5) : en mode durable les dépendances/handoffs "
            "passent par le workflow et la validation humaine par la console. Ces "
            "transports Redis viendront dans un lot ultérieur.",
            file=sys.stderr,
        )
        return 2
    if via_durable and parallele is not None:
        print(
            "--parallele n'est pas géré en mode --durable : le plafond global de "
            "concurrence n'est pas encore porté par le workflow (#95, lot 2/5).",
            file=sys.stderr,
        )
        return 2
    if via_durable and mode_brief != MODE_BRIEF_SANS:
        print(
            "--brief n'est pas géré en mode --durable : l'étape de cadrage (#318) "
            "n'est pas encore une étape du workflow Temporal — elle serait rejouée "
            "à chaque reprise, et payée à chaque fois.",
            file=sys.stderr,
        )
        return 2
    if mode_brief == MODE_BRIEF_HUMAIN and not validation_ui:
        print(
            "--brief humain exige --validation-ui : sans lui, la demande ne part "
            "sur aucun canal, personne ne peut trancher et le run reste suspendu "
            "pour toujours. Utilisez --brief auto pour un lancement sans humain.",
            file=sys.stderr,
        )
        return 2

    # Reprise (#96) : le journal du process qui reprend porte le `run_id` du run
    # repris — c'est lui qui rattache ses étapes à celles d'avant l'interruption,
    # dans la trace comme au grand livre.
    journal = RunJournal(run_id=reprendre) if reprendre is not None else RunJournal()
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
            plafond_tokens=plafond_tokens,
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
            via_durable=via_durable,
            guardrails=guardrails,
            messagerie=messagerie,
            relance=_politique_relance(relances),
            max_parallele=parallele,
            arbitre_brief=(
                _arbitre_brief_ui() if mode_brief == MODE_BRIEF_HUMAIN else None
            ),
            # Les questions de clarification (#321) suivent le régime du brief : même
            # condition, même canal, même Redis. Poser l'un sans l'autre ferait d'un
            # `--brief humain` un run qui fait valider un brief dont les zones
            # d'ombre n'ont jamais été posées à personne.
            arbitre_clarification=(
                _arbitre_clarification_ui() if mode_brief == MODE_BRIEF_HUMAIN else None
            ),
        )
        # Arrêt borné (#64) : une réalisation détachée par le time-out ne peut pas
        # suspendre la fermeture de la boucle — le rapport est toujours rendu.
        if reprendre is not None:
            # `--reprendre` a imposé le mode durable : le moteur est le durable.
            durable = cast("DurableEngine", engine)
            report = run_borne(durable.reprendre(reprendre, journal=journal))
        elif via_durable:
            # Le moteur durable ne connaît pas le régime du brief (#320) — et il n'a
            # pas à le connaître : `--brief` y est refusé plus haut, l'étape de
            # cadrage n'étant pas une étape du workflow Temporal. La branche est
            # séparée pour que ce fait soit **vérifié par le typage** plutôt que
            # tenu par la seule validation d'arguments, qu'un ajout d'option
            # pourrait défaire sans bruit.
            report = run_borne(cast("DurableEngine", engine).run(objective, journal=journal))
        else:
            locale = cast(OrchestrationEngine, engine)
            report = run_borne(
                locale.run(objective, journal=journal, mode_brief=mode_brief)
            )
    except ConfigError as exc:
        print(f"Configuration : {exc}", file=sys.stderr)
        return 1
    except BriefRefuse as refus:
        # Un refus n'est pas un plantage : quelqu'un a lu le brief et dit non. Le
        # dire comme tel, et sortir en 1 comme pour un run qui n'a rien produit —
        # rien n'a été décomposé, donc il n'y a pas de synthèse à imprimer.
        print(f"Brief refusé : {refus}", file=sys.stderr)
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
    via_durable: bool = False,
    guardrails: Guardrails,
    messagerie: bool = False,
    relance: PolitiqueRelance | None = None,
    max_parallele: int | None = None,
    arbitre_brief: ArbitreBrief | None = None,
    arbitre_clarification: ArbitreClarification | None = None,
) -> OrchestrationEngine | DurableEngine:
    """Construit la boucle : locale par défaut, distribuée (#41) ou durable (#95).

    L'import de `maestro.queue` (Celery) comme de `maestro.durable` (Temporal)
    reste local à sa branche : le chemin historique n'en dépend pas. `--durable`
    (#95) fait du run un workflow Temporal (un run = un workflow, une tâche = une
    activité) : garde-fous (#9) et relance (#91) sont posés sur le worker embarqué
    dans ce process, la validation humaine passe par la console. `--messagerie`
    (#44) branche les boîtes aux lettres Redis Pub/Sub (l'instance de la config,
    comme `--publier`) — la connexion est paresseuse, et une publication en
    échec est abandonnée sans gêner l'exécution (relais résilient). `relance`
    (#91) ne s'applique qu'en local et en durable — côté file, chaque worker câble
    la sienne. `max_parallele` (#100) pose le plafond global du run en local et en
    file (non géré en durable, cf. validation d'arguments). `arbitre_brief` (#320)
    n'est posé que sur la boucle locale : `--brief humain` exige `--validation-ui`,
    lui-même incompatible avec `--queue` et `--durable`, donc les deux autres
    branches ne peuvent pas l'atteindre. `arbitre_clarification` (#321) suit
    exactement le même chemin, et pour la même raison.
    """
    if via_durable:
        from maestro.durable import DurableEngine

        return DurableEngine(guardrails=guardrails, relance=relance)
    mailbox = None
    if messagerie:
        from maestro.config import load_settings
        from maestro.messaging import RedisMailbox

        mailbox = RedisMailbox(load_settings().redis_url)
    if via_queue:
        from maestro.queue import create_distributed_engine

        return create_distributed_engine(mailbox=mailbox, max_parallele=max_parallele)
    return OrchestrationEngine.default(
        guardrails=guardrails,
        mailbox=mailbox,
        relance=relance,
        max_parallele=max_parallele,
        arbitre_brief=arbitre_brief,
        arbitre_clarification=arbitre_clarification,
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


def _arbitre_brief_ui() -> ArbitreBrief:
    """Construit l'arbitre du brief (#320) sur le Redis de la config.

    Pendant exact de `_validateur_ui` : la demande part sur le canal
    `maestro.evenements` (l'UI l'affiche via `maestro-api`) et la décision en
    revient par le même canal. Import local, pour la même raison qu'là-bas — seul
    ce chemin dépend de la Control Tower.
    """
    from maestro.config import load_settings
    from maestro.controltower.brief import arbitre_brief_redis

    return arbitre_brief_redis(load_settings().redis_url)


def _arbitre_clarification_ui() -> ArbitreClarification:
    """Construit l'arbitre de clarification (#321) sur le Redis de la config.

    Pendant exact de `_arbitre_brief_ui`, sur l'autre canal : les questions partent
    sur `maestro.evenements` et les réponses en reviennent par le même. Même import
    local, même raison.
    """
    from maestro.config import load_settings
    from maestro.controltower.brief import arbitre_clarification_redis

    return arbitre_clarification_redis(load_settings().redis_url)


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
