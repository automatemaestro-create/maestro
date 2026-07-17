"""Notifications de supervision d'un run via un agent équipé MCP (ticket #105, parent #101).

Premier pilote du socle MCP (#104) : prolonger la supervision d'un run là où
l'équipe vit déjà — un canal Slack. Le principe : **aucun connecteur Slack dans
Maestro**. C'est un agent du catalogue (le DevOps par défaut), *équipé* d'un
serveur MCP Slack déclaré dans son dépôt (`core/mcp/<agent>.json`), qui poste
les événements clés : le moteur ne parle jamais à Slack, il confie une mission
de publication à l'agent outillé, qui la réalise avec ses outils MCP
(`mcp__slack__…`). Changer de canal (Teams, Discord…) = changer la déclaration,
pas ce module.

Deux événements couverts (les critères du ticket) :

- **fin de run** (`fin_de_run`) : le bilan du `RunReport` — tâches
  réussies/échouées/bloquées, usage (tokens, coût), `run_id` — est posté à
  l'issue de l'exécution ;
- **validation humaine en attente** (`validateur_notifiant`) : enveloppe le
  validateur des garde-fous (#9) — la demande est postée sur le canal **avant**
  d'attendre la décision humaine, pour que l'équipe sache qu'un run est en
  pause ; la décision elle-même reste celle du validateur enveloppé (console,
  Control Tower #48), jamais celle de l'agent notificateur.

La notification est de la **supervision, pas de l'exécution** : best-effort par
contrat — un échec (serveur MCP indisponible, canal introuvable, aléa
fournisseur…) est consigné au journal (#8, étapes `notification` /
`<tâche>:notification`) et n'altère jamais l'issue du run ni la décision de
validation. Symétriquement, son coût est consigné sur son étape propre, hors du
rapport du run (le `RunReport` est déjà agrégé quand la notification part).

Côté secrets : le token Slack vit dans l'environnement (`${SLACK_BOT_TOKEN}`,
résolu au montage — #104) ; il ne figure ni dans la déclaration versionnée, ni
dans les prompts confiés à l'agent, et le journal l'expurgerait de toute façon
(`maestro.telemetry.redact_secrets`, valeur d'env + motif `xox…`).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable
from time import perf_counter
from typing import Any

from maestro.agents import default_runtimes
from maestro.agents.mcp import McpStore
from maestro.agents.runtime import AgentRuntime
from maestro.config import ConfigError, Settings, load_settings
from maestro.engine.executor import STATUT_ECHEC, STATUT_TERMINEE
from maestro.engine.guardrails import DemandeValidation, Validateur
from maestro.telemetry import RunJournal, collect_usage

#: Étape du journal de la notification de fin de run (niveau run, comme
#: `planification`) ; la notification d'une validation est suffixée à sa tâche
#: (`<tâche>:notification`), comme `:validation` et `:relance`.
ETAPE_FIN_DE_RUN = "notification"
SUFFIXE_ETAPE_NOTIFICATION = ":notification"

#: Prompt système de la mission de supervision : remplace, pour cette seule
#: exécution, le prompt du rôle (même canal de surcharge que les playbooks #78) —
#: l'agent équipé n'est pas en train de produire un livrable d'infrastructure,
#: il poste une notification.
_PROMPT_SUPERVISION = """\
Tu es l'agent {role} de Maestro, en mission de supervision : tu postes une \
notification sur le canal Slack de l'équipe via tes outils MCP Slack \
(outils `mcp__slack__…`).

Consignes strictes : poste le message demandé tel quel, en UNE seule \
publication, sans le reformuler ni le compléter. Ne crée aucun fichier, ne \
lance aucune commande shell : ta mission tient toute dans l'appel d'outil \
Slack. Si la publication échoue, n'insiste pas au-delà d'une seconde \
tentative et rapporte l'erreur telle quelle."""


class NotificateurRun:
    """Poste les événements clés d'un run sur le canal de supervision, via un agent équipé.

    Construit sur le runtime outillé de l'agent (`maestro.agents.runtime`) et
    son dépôt de serveurs MCP (#104), **relu à chaud à chaque notification**
    comme le fait l'exécuteur à chaque tâche : corriger la déclaration ou le
    secret vaut pour la notification suivante, sans redémarrage. Aucune méthode
    ne lève : chaque notification est consignée au journal (succès ou échec) et
    rend la main — la supervision n'a pas le droit de casser ce qu'elle observe.
    """

    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        agent: str,
        canal: str,
        mcp: McpStore,
    ) -> None:
        self._runtime = runtime
        self._agent = agent
        self._canal = canal
        self._mcp = mcp

    @classmethod
    def default(cls, agent: str = "devops", settings: Settings | None = None) -> NotificateurRun:
        """Le notificateur configuré : agent équipé + canal `MAESTRO_SLACK_CANAL`.

        Valide la configuration **à la construction** (échec propre avant tout
        run) : canal renseigné, runtime outillé existant pour `agent`, et au
        moins un serveur MCP déclaré pour lui — sans serveur, l'agent n'aurait
        aucun moyen de poster. Lève `ConfigError` avec la marche à suivre.
        """
        from maestro.providers.factory import provider_from_settings

        settings = settings or load_settings()
        canal = settings.slack_canal
        if not canal:
            raise ConfigError(
                "MAESTRO_SLACK_CANAL est absent : renseignez le canal Slack des "
                "notifications de supervision (cf. .env.example, ticket #105)."
            )
        runtime = default_runtimes(provider_from_settings(settings), model=settings.model).get(
            agent
        )
        if runtime is None:
            raise ConfigError(
                f"l'agent {agent!r} n'a pas de runtime outillé : le notificateur de "
                "supervision a besoin d'une exécution outillée pour monter le serveur "
                "MCP Slack (agents outillés : developpeur, bdd, qa, devops)."
            )
        mcp = McpStore.default(settings)
        try:
            serveurs = mcp.lire(agent)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        if not serveurs:
            raise ConfigError(
                f"aucun serveur MCP déclaré pour l'agent {agent!r} "
                f"({mcp.racine / f'{agent}.json'}) : le notificateur n'aurait aucun "
                "outil pour poster — déclarez le serveur Slack (docs/04 §6)."
            )
        return cls(runtime, agent=agent, canal=canal, mcp=mcp)

    async def fin_de_run(self, report: Any, journal: RunJournal) -> None:
        """Poste le bilan de `report` (un `RunReport`) : tâches, usage, run_id.

        Typage souple (`Any`) pour ne pas importer `maestro.engine.loop` (qui
        importe déjà la moitié du monde) : seuls `objectif`, `resultats`,
        `reussies`, `echouees`, `bloquees`, `usage_totale` et `run_id` sont lus.
        """
        await self._notifie(
            journal,
            etape=ETAPE_FIN_DE_RUN,
            nom="Notification de supervision — fin de run",
            message=_message_fin_de_run(report),
        )

    def validateur_notifiant(self, validateur: Validateur, journal: RunJournal) -> Validateur:
        """Enveloppe `validateur` : poste « validation en attente » avant de lui déférer.

        La notification part **avant** l'attente de la décision — c'est elle qui
        prévient l'équipe que le run est en pause — et son échec éventuel est
        consigné sans altérer la décision : celle-ci reste entièrement celle du
        validateur enveloppé (fail-safe des garde-fous compris).
        """

        async def _valide(demande: DemandeValidation) -> bool:
            await self._notifie(
                journal,
                etape=f"{demande.task_id}{SUFFIXE_ETAPE_NOTIFICATION}",
                nom=f"Notification de supervision — validation en attente : {demande.titre}",
                message=_message_validation(demande),
            )
            decision: bool | Awaitable[bool] = validateur(demande)
            if inspect.isawaitable(decision):
                decision = await decision
            return bool(decision)

        return _valide

    async def _notifie(
        self, journal: RunJournal, *, etape: str, nom: str, message: str
    ) -> None:
        """Confie la publication de `message` à l'agent équipé et consigne l'issue.

        Les serveurs MCP sont relus à chaud ici ; le prompt système du rôle est
        remplacé pour cette exécution par la mission de supervision (même canal
        de surcharge que les playbooks #78). Ne lève jamais : l'échec est un
        constat de supervision, consigné au journal avec sa cause.
        """
        debut = perf_counter()
        erreur: str | None
        with collect_usage() as recolte:
            try:
                serveurs = self._mcp.lire(self._agent)
                if not serveurs:
                    raise ValueError(
                        f"aucun serveur MCP déclaré pour l'agent {self._agent!r} — "
                        "rien pour poster la notification."
                    )
                outcome = await self._runtime.execute(
                    _mission_publication(message, self._canal),
                    system_prompt=_PROMPT_SUPERVISION.format(role=self._runtime.profile.role),
                    mcp_serveurs=serveurs,
                )
            except Exception as exc:  # best-effort : la supervision ne casse jamais le run
                statut, sortie, erreur = STATUT_ECHEC, "", str(exc)
            else:
                statut, sortie, erreur = STATUT_TERMINEE, outcome.resume, None
        duree_ms = int((perf_counter() - debut) * 1000)
        journal.consigne(
            etape=etape,
            nom=nom,
            agent=self._agent,
            role=self._runtime.profile.role,
            statut=statut,
            entree=message,
            sortie=sortie,
            erreur=erreur,
            usage=recolte.total.avec_duree(duree_ms),
        )


def _mission_publication(message: str, canal: str) -> str:
    """Compose la mission confiée à l'agent : poster `message` sur `canal`, tel quel."""
    return (
        f"Poste la notification de supervision suivante sur le canal Slack "
        f"« {canal} », telle quelle (sans reformulation), via ton outil MCP Slack "
        "de publication de message :\n"
        "---\n"
        f"{message}\n"
        "---\n"
        "Puis rends compte en une ligne : canal, horodatage ou identifiant du "
        "message posté — ou l'erreur rencontrée, telle quelle."
    )


def _message_fin_de_run(report: Any) -> str:
    """Le bilan de fin de run posté sur le canal : tâches, usage (coût), run_id."""
    lignes = [
        f":checkered_flag: Run Maestro terminé — {report.objectif}",
        f"• Tâches : {len(report.reussies)}/{len(report.resultats)} réussie(s)"
        + (f", {len(report.echouees)} en échec" if report.echouees else "")
        + (f", {len(report.bloquees)} bloquée(s)" if report.bloquees else ""),
        f"• Usage : {report.usage_totale.resume_court()}",
    ]
    if report.run_id:
        lignes.append(f"• run_id : {report.run_id}")
    return "\n".join(lignes)


def _message_validation(demande: DemandeValidation) -> str:
    """La demande de validation postée sur le canal : le run est en pause, à vous."""
    return "\n".join(
        [
            ":raised_hand: Validation humaine en attente — run Maestro en pause",
            f"• Tâche : {demande.titre} ({demande.task_id})",
            f"• Agent : {demande.role} (`{demande.agent}`)",
            f"• Raison : {demande.raison}",
            f"• Action demandée : {demande.description}",
            "→ Le run reste en pause tant que personne n'a tranché "
            "(console ou Control Tower).",
        ]
    )
