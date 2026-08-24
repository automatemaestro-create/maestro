"""Moteur d'orchestration : la boucle objectif → tâches → agents → agrégat (ticket #6).

Assemble les briques du POC en une **boucle d'orchestration** :

1. l'orchestrateur (#3) découpe l'objectif en tâches validées ;
2. le routeur (#6) assigne chaque tâche à l'agent le plus compétent ;
3. le moteur exécute les tâches en respectant les dépendances, **en parallèle dès
   qu'elles sont indépendantes** (#7) : chaque tâche démarre sitôt ses dépendances
   résolues, chaque agent produisant son livrable via la couche fournisseur
   (`ModelProvider`) et recevant les résultats des tâches dont il dépend (tableau
   noir léger) ;
4. les résultats sont **agrégés** en un `RunReport` (rapport structuré déterministe).

L'exécution d'une tâche (routage, garde-fous #9, production du livrable — runtime
outillé #35 ou texte —, mesure d'usage #8) vit dans `maestro.engine.executor` : la
boucle la **délègue** à un `TaskExecutor` injectable. Par défaut c'est le
`LocalExecutor` (en process, comportement historique) ; la file de tâches (#41,
`maestro.queue.CeleryExecutor`) s'injecte à sa place pour distribuer les tâches à
des **workers séparés** via Celery + Redis — la boucle (dépendances, parallélisme,
agrégation, journal) ne change pas.

L'exécution parallèle (#7) n'introduit aucun état partagé entre tâches : chaque
exécution ne reçoit que les résultats de **ses** dépendances, la mesure d'usage (#8)
est isolée par le contexte (`contextvars`, copié par tâche asyncio — pas de fuite
entre agents), et chaque exécution outillée ouvre son propre espace de travail
jetable (`maestro.sandbox`). Le rassemblement reste déterministe : les résultats du
rapport suivent l'ordre topologique du plan, pas l'ordre d'achèvement.
`max_parallele` plafonne au besoin le nombre d'exécutions simultanées (illimité par
défaut — les plans du POC sont petits).

La boucle est **suspendable** (#477) : une `PorteExecution` passée à `run` se ferme
en cours de route, et plus aucune tâche n'atteint alors l'exécuteur — celles qui y
sont déjà vont à leur terme. C'est le seul point du moteur qui distingue une pause
d'une annulation, et il tient en un `await` (`maestro.engine.pause`).

Le moteur ne dépend que de `ModelProvider` : il reste **agnostique du fournisseur**.
`OrchestrationEngine.default` résout fournisseur et modèle depuis la config
(`MAESTRO_PROVIDER`/`MAESTRO_MODEL`, #69), comme `Orchestrator.default`.

La boucle est **résiliente** : un échec de routage ou d'exécution est consigné dans
le résultat de la tâche (`statut = "echec"`) et n'interrompt pas les tâches
indépendantes. En revanche, les tâches **aval** d'un échec sont **bloquées** (#43) :
statut explicite `bloquee`, jamais transmises à l'exécuteur (donc jamais mises en
file), blocage propagé en cascade — pas d'exécution orpheline. Le rapport agrège
réussites, échecs et blocages.

Chaque étape (planification comprise) est **journalisée et mesurée** (#8) : durée
horloge chronométrée, tokens/coût/outils récoltés auprès du fournisseur via
`maestro.telemetry.collect_usage`, le tout consigné dans un `RunJournal` (une ligne
JSON par étape) et porté par les `TaskResult` — le coût par tâche est visible dans
la synthèse comme dans le rapport structuré, et traçable par le `run_id`.

Le **brief structuré** (#318) est disponible au même régime — `etape_brief`, pendant
exact de `_plan` — et **`run` le branche** depuis #320 : selon `mode_brief`, la boucle
décompose l'objectif brut (`sans`), le brief rédigé sans attendre personne (`auto`) ou
le brief **approuvé par un humain** (`humain`, décision D5). Dans ce dernier cas le run
s'arrête sur le brief — aucune tâche n'est créée tant que rien n'est tranché — et ce qui
part en décomposition est le brief tel qu'il a été approuvé, corrections comprises.

Avec une **messagerie inter-agents** injectée (#44, `mailbox=`), le relais entre
tâches dépendantes devient un **handoff observable** (critère MVP n°7) : l'agent
qui termine une tâche à dépendants **annonce** l'issue par message (diffusion,
`maestro.messaging`), et chaque tâche aval n'est transmise à l'exécuteur qu'à
**réception** du message de ses dépendances. L'échange est journalisé (#8) et
visible dans le flux d'événements de la Control Tower (#46). Sans messagerie
(défaut), la synchronisation reste purement en process — comportement historique.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any

from maestro.agents import default_runtimes
from maestro.agents.capacity import CapacityStore
from maestro.agents.catalog import DEFAULT_AGENTS, Agent
from maestro.agents.mcp import McpStore
from maestro.agents.permissions import PermissionStore
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.runtime import AgentRuntime
from maestro.agents.secrets import SecretStore
from maestro.agents.store import AgentStore, catalogue
from maestro.config import Settings, load_settings
from maestro.engine.brief import (
    MODE_BRIEF_AUTO,
    MODE_BRIEF_HUMAIN,
    MODE_BRIEF_SANS,
    ArbitreBrief,
    ArbitreClarification,
    BriefRefuse,
    DemandeBrief,
    DemandeClarification,
    mode_brief_valide,
    motif_sans_reponse,
    tours_clarification_valide,
)
from maestro.engine.executor import (
    STATUT_BLOQUEE,
    STATUT_ECHEC,
    STATUT_TERMINEE,
    LocalExecutor,
    TaskExecutor,
    TaskResult,
    _ecoule_ms,
)
from maestro.engine.guardrails import Guardrails
from maestro.engine.pause import PorteExecution
from maestro.engine.retry import RELANCE_DEFAUT, PolitiqueRelance
from maestro.messaging.handoff import HandoffRelais
from maestro.messaging.mailbox import Mailbox
from maestro.orchestrator.orchestrator import Orchestrator
from maestro.orchestrator.schema import Brief, Clarification, Task, topological_order
from maestro.projets.store import ProjetStore
from maestro.providers.base import ModelProvider
from maestro.references import ReferenceTicket
from maestro.sources.extraction import RapportLecture
from maestro.telemetry import (
    RunJournal,
    StepUsage,
    collect_usage,
    resume_controle_depense,
)
from maestro.telemetry.costs import ETAPE_BRIEF, RunCost, TaskCost

__all__ = [
    "MODE_BRIEF_AUTO",
    "MODE_BRIEF_HUMAIN",
    "MODE_BRIEF_SANS",
    "STATUT_BLOQUEE",
    "STATUT_ECHEC",
    "STATUT_TERMINEE",
    "BriefRefuse",
    "OrchestrationEngine",
    "RunReport",
    "TaskResult",
]


@dataclass(frozen=True)
class RunReport:
    """Agrégat déterministe d'une exécution : l'objectif et le résultat de chaque tâche.

    `resultats` est dans l'**ordre topologique du plan** — un ordre déterministe,
    indépendant de l'ordre d'achèvement des tâches exécutées en parallèle (#7). Le
    rapport n'appelle aucun modèle : il assemble ce que la boucle a collecté (choix
    « rapport structuré » du ticket #6).

    `run_id` relie le rapport aux lignes du journal d'exécution (#8) ;
    `planification` porte l'usage de l'étape de planification, qui s'ajoute à celui
    des tâches dans `usage_totale`.

    `plafond_cout_usd`/`plafond_tokens` sont les seuils du garde-fou de dépense (#9)
    tels qu'armés pour ce run : le rapport en tire `controle_depense`, la ligne qui
    dit à l'opérateur quel contrôle a réellement tenu (#113) — coût réel, ou tokens
    quand le fournisseur ne rapporte pas de coût.

    `mode_brief` dit sous quel régime le run a tourné (#320) et `brief` porte le
    brief **tel qu'il a été retenu** — corrigé par l'humain, le cas échéant, puisque
    c'est lui qui a servi d'entrée à la décomposition. Tous deux sont rendus par la
    synthèse : un run headless annonce ainsi qu'il n'a attendu personne, et un run
    approuvé garde la trace de ce qui a réellement été décomposé. `cadrage` porte
    l'usage de l'étape de brief, compté à part de la planification (deux appels
    modèle distincts, #318) mais entrant comme elle dans `usage_totale` — sans quoi
    le brief serait la seule dépense du run à ne figurer nulle part.
    """

    objectif: str
    resultats: tuple[TaskResult, ...]
    run_id: str = ""
    planification: StepUsage = StepUsage()
    plafond_cout_usd: float | None = None
    plafond_tokens: int | None = None
    mode_brief: str = MODE_BRIEF_SANS
    brief: Brief | None = None
    cadrage: StepUsage = StepUsage()
    tours_clarification: int = 0

    @property
    def reussies(self) -> tuple[TaskResult, ...]:
        """Sous-ensemble des tâches terminées avec succès."""
        return tuple(r for r in self.resultats if r.ok)

    @property
    def echouees(self) -> tuple[TaskResult, ...]:
        """Sous-ensemble des tâches en échec (routage ou exécution)."""
        return tuple(r for r in self.resultats if r.statut == STATUT_ECHEC)

    @property
    def bloquees(self) -> tuple[TaskResult, ...]:
        """Sous-ensemble des tâches bloquées par une dépendance en échec (#43).

        Jamais exécutées ni mises en file : leur `erreur` cite les dépendances non
        satisfaites. Toujours vide si `echouees` l'est (un blocage a forcément un
        échec en amont).
        """
        return tuple(r for r in self.resultats if r.statut == STATUT_BLOQUEE)

    @property
    def usage_totale(self) -> StepUsage:
        """Usage agrégé de l'exécution : cadrage, planification et toutes les tâches."""
        total = self.planification.fusion(self.cadrage)
        for r in self.resultats:
            total = total.fusion(r.usage)
        return total

    @property
    def grand_livre(self) -> RunCost:
        """Le grand livre du run (#55/#57) : coût par tâche et agrégat, depuis l'agrégat.

        Pendant de `RunCost.depuis_journal`, mais sourcé du **rapport** plutôt que
        du journal d'un process. La distinction devient essentielle en mode
        durable repris (#96) : l'agrégat est assemblé depuis l'historique
        Temporal, donc il porte l'avant **et** l'après reprise, là où le journal
        du process qui reprend n'a vu que l'après (les étapes acquises ont été
        consignées par le process disparu, et les activités qui reprennent
        tiennent chacune leur propre journal).

        L'attribution est directe — une entrée par tâche du plan, plus l'usage de
        planification — sans la convention d'étapes annexes du journal : le
        rapport ne porte que les issues de tâches, annexes déjà fusionnées dans
        l'usage de chacune.
        """
        return RunCost(
            run_id=self.run_id,
            planification=self.planification,
            brief=self.cadrage,
            taches=tuple(
                TaskCost(
                    tache_id=r.task_id,
                    nom=r.titre,
                    agent=r.agent,
                    role=r.role,
                    statut=r.statut,
                    usage=r.usage,
                )
                for r in self.resultats
            ),
        )

    @property
    def controle_depense(self) -> str:
        """Le contrôle de dépense qui a réellement tenu ce run, en clair (#113).

        Dit à l'opérateur si le plafond en USD avait prise (coût rapporté) ou si
        seul le plafond en tokens plafonnait — au lieu d'un garde-fou silencieusement
        inopérant sur un fournisseur sans coût rapporté.
        """
        return resume_controle_depense(
            self.plafond_cout_usd, self.plafond_tokens, self.usage_totale
        )

    def synthese(self) -> str:
        """Rend l'agrégat en Markdown : récap chiffré puis livrable par tâche."""
        lignes = [
            f"# Synthèse — {self.objectif}",
            "",
            f"{len(self.reussies)}/{len(self.resultats)} tâche(s) réussie(s).",
            f"Usage total (cadrage et planification inclus) : "
            f"{self.usage_totale.resume_court()}",
            f"Contrôle de dépense : {self.controle_depense}",
            # Le régime du brief est **toujours** annoncé (#320), y compris « sans » :
            # savoir qu'un run n'a attendu personne est une information, pas une
            # section manquante — c'est même la seule qui distingue un run headless
            # d'un run que quelqu'un a validé.
            f"Brief : {self.resume_brief()}",
            "",
        ]
        for r in self.resultats:
            # Marqueurs en texte (pas d'emoji) : portables sur une console Windows
            # héritée (cp1252) comme en UTF-8, sans faire planter l'affichage.
            if r.ok:
                etat = "[terminée]"
            elif r.statut == STATUT_BLOQUEE:
                etat = "[bloquée]"
            else:
                etat = "[échec]"
            competences = ", ".join(r.competences_requises)
            lignes.append(f"## {etat} {r.titre}")
            lignes.append(f"- Agent : {r.role} (`{r.agent}`) — compétences : {competences}")
            if r.worker:
                lignes.append(f"- Worker : `{r.worker}`")
            if r.playbook_version is not None:
                lignes.append(f"- Playbook : v{r.playbook_version}")
            lignes.append(f"- Usage : {r.usage.resume_court()}")
            if r.ok:
                lignes.extend(["", r.sortie, ""])
                if r.fichiers:
                    lignes.append(f"Fichiers produits ({len(r.fichiers)}) :")
                    lignes.extend(f"- `{f.chemin}`" for f in r.fichiers)
                    lignes.append("")
            elif r.statut == STATUT_BLOQUEE:
                lignes.extend([f"- Bloquée : {r.erreur}", ""])
            else:
                lignes.extend([f"- Échec : {r.erreur}", ""])
        return "\n".join(lignes).rstrip() + "\n"

    def resume_brief(self) -> str:
        """Le régime du brief de ce run, en clair (#320) — pour la synthèse.

        Dit **ce qui s'est passé**, pas seulement le mode demandé : `auto` annonce
        qu'aucune approbation n'a été attendue (le point de la troisième exigence
        du lot — un run headless qui attend est un run mort), `humain` qu'une
        décision a été rendue, et `sans` que l'objectif brut a été décomposé.

        Les **allers-retours de clarification** (#321) s'y ajoutent quand il y en a
        eu : c'est là que le nombre est « annoncé » pour qui relit un run terminé —
        l'annonce en cours de run, elle, voyage sur l'événement des questions. Zéro
        tour ne se dit pas, faute d'être une information : c'est le cas courant d'un
        objectif que le Chef de projet a trouvé limpide.
        """
        if self.mode_brief == MODE_BRIEF_AUTO:
            regime = "rédigé et décomposé sans attendre d'approbation (mode « auto »)"
        elif self.mode_brief == MODE_BRIEF_HUMAIN:
            regime = "approuvé par un humain avant décomposition (mode « humain »)"
        else:
            return "aucun — l'objectif brut a été décomposé (mode « sans »)"
        if self.tours_clarification:
            regime += (
                f" — {self.tours_clarification} tour(s) de clarification"
            )
        return regime

    def to_dict(self) -> dict[str, Any]:
        """Réémet le rapport en dict JSON-sérialisable."""
        return {
            "objectif": self.objectif,
            "run_id": self.run_id,
            "reussies": len(self.reussies),
            "bloquees": len(self.bloquees),
            "total": len(self.resultats),
            "planification": self.planification.to_dict(),
            "cadrage": self.cadrage.to_dict(),
            "usage_totale": self.usage_totale.to_dict(),
            "plafond_cout_usd": self.plafond_cout_usd,
            "plafond_tokens": self.plafond_tokens,
            "controle_depense": self.controle_depense,
            "mode_brief": self.mode_brief,
            "tours_clarification": self.tours_clarification,
            # `null` quand le run n'est pas passé par l'étape (mode « sans ») : le
            # consommateur distingue ainsi « pas de brief » de « brief vide ».
            "brief": self.brief.to_dict() if self.brief is not None else None,
            "resultats": [r.to_dict() for r in self.resultats],
        }


class OrchestrationEngine:
    """Boucle d'orchestration : objectif → plan → assignation → exécution → agrégat."""

    def __init__(
        self,
        provider: ModelProvider,
        orchestrator: Orchestrator,
        *,
        agents: Sequence[Agent] = DEFAULT_AGENTS,
        runtimes: Mapping[str, AgentRuntime] | None = None,
        max_parallele: int | None = None,
        guardrails: Guardrails | None = None,
        executor: TaskExecutor | None = None,
        mailbox: Mailbox | None = None,
        playbooks: PlaybookStore | None = None,
        capacites: CapacityStore | None = None,
        mcp: McpStore | None = None,
        secrets: SecretStore | None = None,
        permissions: PermissionStore | None = None,
        relance: PolitiqueRelance | None = None,
        projets: ProjetStore | None = None,
        arbitre_brief: ArbitreBrief | None = None,
        arbitre_clarification: ArbitreClarification | None = None,
        tours_clarification: int | None = None,
    ) -> None:
        if max_parallele is not None and max_parallele < 1:
            raise ValueError(f"max_parallele doit être ≥ 1 (reçu : {max_parallele}).")
        self._orchestrator = orchestrator
        # À qui poser les questions du brief (#321) — None : personne, et les
        # questions partent alors telles quelles en validation (le comportement de
        # #320). Même nature que `arbitre_brief` : du câblage de déploiement.
        self._arbitre_clarification = arbitre_clarification
        # Le plafond d'allers-retours, refusé **ici** s'il est absurde plutôt qu'au
        # milieu d'un run : un plafond négatif ne se découvre pas après un brief payé.
        self._tours_clarification = tours_clarification_valide(tours_clarification)
        # À qui soumettre le brief quand un run tourne en mode « humain » (#320) —
        # None : personne, et un run qui demanderait ce mode sera refusé **avant**
        # son premier appel modèle. Injecté à la construction comme le `Validateur`
        # des garde-fous (#9) : c'est du câblage de déploiement (où la question est
        # posée), là où le *mode* est un choix du lancement (y a-t-il quelqu'un ?).
        self._arbitre_brief = arbitre_brief
        # Garde-fous du run (#9) : retenus ici pour que le rapport dise quel contrôle
        # de dépense a tenu (#113). Le défaut laisse les plafonds inactifs. En mode
        # distribué (exécuteur injecté), les garde-fous s'appliquent côté worker :
        # ce que retient l'orchestrateur ne reflète alors que ce qu'on lui a passé.
        self._guardrails = guardrails if guardrails is not None else Guardrails()
        # Plafond d'exécutions simultanées (#7) — None : illimité. Utile pour ménager
        # les limites de débit d'un fournisseur sur un plan très large.
        self._max_parallele = max_parallele
        # Messagerie inter-agents (#44) — None : pas de handoff par message, la
        # synchronisation des dépendances reste purement en process.
        self._mailbox = mailbox
        # Frontière d'exécution (#41) : en process par défaut ; un exécuteur injecté
        # (ex. `maestro.queue.CeleryExecutor`) distribue les tâches à des workers.
        # `playbooks` (#78), `capacites` (#86), `mcp` (#104) et `projets` (#224) :
        # les dépôts que l'exécuteur local relit à chaque tâche — l'application à
        # chaud ; ignorés si un exécuteur est injecté (en distribué, chaque worker
        # câble les siens — `relance` (#91) comprise, cf.
        # maestro.queue.worker.configurer_worker).
        self._executor = (
            executor
            if executor is not None
            else LocalExecutor(
                provider,
                agents=agents,
                runtimes=runtimes,
                guardrails=guardrails,
                playbooks=playbooks,
                capacites=capacites,
                mcp=mcp,
                secrets=secrets,
                permissions=permissions,
                relance=relance,
                projets=projets,
            )
        )

    @classmethod
    def default(
        cls,
        settings: Settings | None = None,
        *,
        guardrails: Guardrails | None = None,
        mailbox: Mailbox | None = None,
        relance: PolitiqueRelance | None = RELANCE_DEFAUT,
        max_parallele: int | None = None,
        arbitre_brief: ArbitreBrief | None = None,
        arbitre_clarification: ArbitreClarification | None = None,
        tours_clarification: int | None = None,
    ) -> OrchestrationEngine:
        """Moteur par défaut : fournisseur et modèle issus de la config (#69).

        Importe la fabrique ici (et non en tête de module) pour ne pas lier le
        moteur agnostique à un fournisseur concret : le choix vit dans la config
        (`MAESTRO_PROVIDER`). `MAESTRO_MODEL`, s'il est renseigné, bascule d'un même
        geste l'orchestrateur, le catalogue d'exécutants et les runtimes outillés.

        Les prompts système des exécutants sont chargés depuis le **stockage
        versionné des playbooks** (#76) et appliqués **à chaud** (#78) : le dépôt
        est passé à l'exécuteur, qui relit la version courante à chaque tâche —
        une édition publiée depuis la Control Tower vaut pour l'exécution
        suivante, sans reconstruire le moteur ni redémarrer le process. Un agent
        jamais édité garde son prompt du code.

        Le catalogue d'exécutants est le catalogue **effectif** (#72) : les
        agents par défaut plus les agents personnalisés persistés
        (`MAESTRO_AGENTS_DIR`, sinon `core/agents/`), chargés ici, à la
        construction du moteur — un agent créé ensuite vaut pour les moteurs
        construits après lui. Sans runtime outillé, un agent personnalisé
        produit son livrable par le chemin texte, cadré par son playbook.

        Le **contrôle de capacité** (#86, EF-21) est branché sur le dépôt
        configuré (`MAESTRO_CAPACITE_DIR`, sinon `core/capacite/`), relu à
        chaud à chaque tâche : un agent désactivé depuis la Control Tower ne
        reçoit plus de tâches, et ses exécutions simultanées sont bornées à
        son plafond d'instances.

        Les **serveurs MCP par agent** (#104) sont branchés sur le dépôt
        configuré (`MAESTRO_MCP_DIR`, sinon `core/mcp/`), relu à chaud à
        chaque tâche : les serveurs déclarés pour un agent sont montés par la
        couche SDK sur ses exécutions outillées — un serveur indisponible est
        un échec propre, jamais relancé. Leurs références `${VAR}` se résolvent
        dans le **coffre de secrets par agent** (#109, `MAESTRO_SECRETS_DIR`,
        sinon `core/secrets/`) dès qu'il est provisionné : chaque agent ne voit
        que ses propres secrets ; coffre absent, résolution historique dans
        l'environnement du process.

        Les **politiques de permissions par agent** (#110) sont branchées sur
        le dépôt configuré (`MAESTRO_PERMISSIONS_DIR`, sinon
        `core/permissions/`), relu à chaud à chaque tâche : allow/deny par
        outil (et par serveur MCP) appliqué à l'exécution — outils refusés
        retirés de la session, serveurs refusés jamais montés, violation au
        vol refusée proprement et tracée sans condamner le run.

        La **relance automatique** (#91, ENF-06) est **armée par défaut**
        (`PolitiqueRelance()` : 3 tentatives, backoff exponentiel) : sur ce
        moteur — celui des vrais runs —, un aléa fournisseur transitoire ne
        condamne plus l'exécution. `relance=None` la désactive ;
        `relance=PolitiqueRelance(...)` l'ajuste.

        `max_parallele` (#100) pose le **plafond global** du run — le plafond
        transverse, prioritaire sur la capacité par agent (#86) qui s'applique
        en dessous : quel que soit le nombre d'instances accordé à un agent,
        jamais plus de `max_parallele` tâches en vol toutes files confondues.
        None (défaut) : illimité, comportement historique.

        `arbitre_brief` (#320) est **à qui** soumettre le brief quand un run est
        lancé en mode « humain » — en pratique
        `maestro.controltower.brief.ArbitreBriefControlTower`. None (défaut) :
        aucun régime humain n'est possible sur ce moteur, et le demander sera
        refusé plutôt qu'ignoré.
        """
        from maestro.providers.factory import default_model, provider_from_settings

        settings = settings or load_settings()
        provider = provider_from_settings(settings)
        orchestrator = Orchestrator(provider, model=default_model(settings))
        return cls(
            provider,
            orchestrator,
            agents=catalogue(AgentStore.default(settings), settings.model),
            runtimes=default_runtimes(provider, model=settings.model),
            guardrails=guardrails,
            mailbox=mailbox,
            playbooks=PlaybookStore.default(settings),
            capacites=CapacityStore.default(settings),
            mcp=McpStore.default(settings),
            secrets=SecretStore.default(settings),
            permissions=PermissionStore.default(settings),
            projets=ProjetStore.default(settings),
            relance=relance,
            max_parallele=max_parallele,
            arbitre_brief=arbitre_brief,
            arbitre_clarification=arbitre_clarification,
            tours_clarification=tours_clarification,
        )

    async def run(
        self,
        objective: str,
        *,
        journal: RunJournal | None = None,
        ticket: ReferenceTicket | None = None,
        projet_id: str | None = None,
        mode_brief: str = MODE_BRIEF_SANS,
        porte: PorteExecution | None = None,
    ) -> RunReport:
        """Exécute la boucle complète pour `objective` et renvoie l'agrégat.

        Lève `ValueError` si l'objectif est vide et propage les erreurs de
        **planification** (`PlanParsingError`, `TaskValidationError`) : sans plan
        valide, il n'y a rien à orchestrer. En revanche, les échecs *par tâche*
        (routage, exécution) sont consignés, pas propagés.

        Les tâches **indépendantes s'exécutent en parallèle** (#7) : chacune reçoit
        sa propre tâche asyncio et n'est transmise à l'exécuteur (donc mise en file,
        en mode distribué) que lorsque **toutes** ses dépendances sont terminées
        avec succès (#43). Une dépendance en échec (ou elle-même bloquée) **bloque**
        la tâche : statut explicite `bloquee`, consigné au journal, sans aucune
        exécution ni mise en file — le blocage se propage ainsi en cascade sur tout
        l'aval. Les résultats sont rassemblés dans l'ordre topologique du plan,
        déterministe quel que soit l'ordre d'achèvement.

        Chaque étape est consignée dans `journal` (#8) — un `RunJournal` neuf par
        défaut ; en injecter un permet d'inspecter les traces ou de fixer le
        `run_id`. Le rapport porte ce `run_id` et l'usage par tâche. Les traces des
        tâches parallèles y apparaissent dans l'ordre d'achèvement.

        Avec une messagerie injectée (#44), le passage de relais est **observable** :
        l'issue de chaque tâche à dépendants est annoncée par message (handoff ou
        notification, journalisé), et la tâche aval attend ce message avant de
        démarrer — la synchronisation en process (#43) reste le filet de sécurité
        (une annonce perdue ne suspend pas l'exécution au-delà du time-out).

        `ticket` (#187) est le **ticket dont part le run** : chaque
        tâche du plan en hérite, sauf celle qui en porte déjà une (le plan a été
        plus précis que le lancement — sa référence gagne). Le moteur ne fait
        que la transporter : il ne l'interprète pas, n'appelle aucun outil de
        ticketing et n'en connaît aucun.

        `projet_id` (#222) est le **projet dans lequel le run travaille** : même
        régime que `ticket` — chaque tâche du plan en hérite, sauf celle qui en
        porte déjà un. Le moteur ne fait ici que le transporter jusqu'au
        journal, d'où il remonte aux vues ; c'est l'espace de travail dérivé
        (#224) qui lui donnera un effet sur l'exécution.

        `mode_brief` (#320, décision D5) décide de ce qui est décomposé — l'objectif
        brut (`sans`, le défaut : le comportement d'avant ce lot) ou le **brief**,
        rédigé sans attendre personne (`auto`) ou approuvé par un humain (`humain`).
        En mode humain, **aucune tâche n'est créée** tant que rien n'est tranché :
        la boucle s'arrête dans `_cadrage`, et un refus lève `BriefRefuse` avant la
        première planification — rien de payant n'a alors été engagé au-delà du
        brief lui-même. Ce qui part en décomposition est le brief **tel qu'il a été
        approuvé**, corrections humaines comprises.

        En amont de cette validation, le run **pose les questions** que le brief a
        laissées ouvertes et attend les réponses (#321), puis régénère le brief en
        les intégrant — jusqu'à `tours_clarification` fois. Ce qui n'a pas été levé
        au plafond part en validation **inscrit en hypothèses explicites** plutôt que
        de faire boucler le run. Sans arbitre de clarification, cette étape n'a pas
        lieu et les questions partent telles quelles en validation.

        `porte` (#477) est la **pause** du run : tant qu'elle est fermée, aucune
        tâche nouvelle n'atteint l'exécuteur, et celles qui y sont déjà vont à leur
        terme. None (le défaut) : rien à franchir, le comportement d'avant ce lot.
        Le moteur ne sait ni qui la ferme ni pourquoi — voir `maestro.engine.pause`.
        """
        journal = journal if journal is not None else RunJournal()
        mode_brief = mode_brief_valide(mode_brief)
        cadrage, brief, tours_clarification = await self._cadrage(
            objective, journal, mode_brief, projet_id
        )
        # L'entrée de la décomposition : le brief retenu, ou l'objectif brut en mode
        # « sans ». `Brief.synthese()` plutôt que le seul `brief.objectif` — c'est le
        # texte que l'humain a relu pour approuver, périmètre et critères compris, et
        # décomposer moins que ce qui a été approuvé rendrait l'approbation trompeuse.
        entree_plan = objective if brief is None else brief.synthese()
        plan_usage, tasks = await self._plan(entree_plan, journal, projet_id)
        if ticket is not None:
            tasks = [
                task
                if task.ticket is not None
                else replace(task, ticket=ticket)
                for task in tasks
            ]
        if projet_id is not None:
            tasks = [
                task
                if task.projet_id is not None
                else replace(task, projet_id=projet_id)
                for task in tasks
            ]
        ordered = topological_order(tasks)
        dependants = _dependants_directs(ordered)
        # Boîte de diffusion ouverte avant toute exécution (pub/sub sans rejeu :
        # aucune annonce ne peut être manquée) — None sans messagerie (#44).
        relais = (
            await HandoffRelais.ouvrir(self._mailbox, journal)
            if self._mailbox is not None
            else None
        )
        # Sémaphore créé ici (et pas au constructeur) : lié à la boucle asyncio de
        # cette exécution, il ne survit pas d'un `run` à l'autre.
        semaphore = (
            asyncio.Semaphore(self._max_parallele) if self._max_parallele else None
        )
        en_vol: dict[str, asyncio.Task[TaskResult]] = {}

        async def _des_que_prete(task: Task) -> TaskResult:
            # Attend ses seules dépendances : chaque exécution ne voit que le
            # tableau noir qui la concerne, aucun état partagé entre tâches. Le
            # sémaphore n'est pris qu'une fois les dépendances résolues, pour ne
            # pas occuper un créneau à attendre (ni s'interbloquer).
            dependances = [await en_vol[dep] for dep in task.dependances]
            if relais is not None:
                # Handoff (#44) : la tâche ne démarre qu'une fois le message de
                # chacune de ses dépendances relevé dans la boîte aux lettres.
                for dep in task.dependances:
                    await relais.attend(dep)
            insatisfaites = [dep for dep in dependances if not dep.ok]
            if insatisfaites:
                # Blocage aval (#43) : la tâche n'atteint jamais l'exécuteur — ni
                # exécution ni mise en file — et le blocage cascade sur l'aval.
                # Volontairement **avant** la porte de pause (#477) : consigner un
                # blocage n'engage rien (pas d'appel modèle, pas de mise en file),
                # et retenir la cascade rendrait un run suspendu indiscernable
                # d'un run figé — l'aval d'un échec doit se lire tout de suite.
                result = _consigne_blocage(task, insatisfaites, journal)
            else:
                if porte is not None:
                    # La pause (#477), et elle est **ici** : la dernière ligne
                    # avant que quoi que ce soit ne soit engagé. Franchie avant le
                    # sémaphore, pour la raison qui vaut déjà des dépendances —
                    # une tâche qui attend n'occupe pas un créneau. Une tâche déjà
                    # passée n'a plus de porte devant elle : elle finit, et c'est
                    # ce qui distingue une pause d'une annulation.
                    await porte.franchir()
                if semaphore is None:
                    result = await self._executor.execute(task, dependances, journal)
                else:
                    async with semaphore:
                        result = await self._executor.execute(
                            task, dependances, journal
                        )
            if relais is not None and dependants[task.id]:
                # L'agent qui termine annonce l'issue à l'aval (handoff ou
                # notification) — publication journalisée, résiliente.
                await relais.annonce(task, result, dependants[task.id])
            return result

        try:
            # Créées dans l'ordre topologique, les tâches asyncio des dépendances
            # existent toujours avant celles qui les attendent. `execute` ne levant
            # jamais, le TaskGroup ne se déclenche que sur un bug interne.
            async with asyncio.TaskGroup() as tg:
                for task in ordered:
                    en_vol[task.id] = tg.create_task(_des_que_prete(task))
        finally:
            if relais is not None:
                await relais.fermer()

        return RunReport(
            objectif=objective,
            resultats=tuple(en_vol[task.id].result() for task in ordered),
            run_id=journal.run_id,
            planification=plan_usage,
            plafond_cout_usd=self._guardrails.plafond_cout_usd,
            plafond_tokens=self._guardrails.plafond_tokens,
            mode_brief=mode_brief,
            brief=brief,
            cadrage=cadrage,
            tours_clarification=tours_clarification,
        )

    async def _cadrage(
        self,
        objective: str,
        journal: RunJournal,
        mode_brief: str,
        projet_id: str | None,
    ) -> tuple[StepUsage, Brief | None, int]:
        """Rédige le brief, lève ses zones d'ombre, le fait trancher — avant tout plan.

        L'étape de cadrage complète (#320 puis #321), dans l'ordre où elle se joue :
        rédaction, **allers-retours de clarification** bornés (`_clarifications`),
        puis validation humaine. Cet ordre est le sujet : questionner après avoir
        fait valider reviendrait à faire approuver un brief qu'on s'apprête à
        réécrire, et l'approbation ne porterait plus sur ce qui est décomposé.

        Rend l'usage **cumulé** de l'étape (rédaction initiale et régénérations), le
        brief **retenu** (None en mode « sans » : la boucle décompose alors
        l'objectif brut, exactement comme avant ces lots) et le nombre de tours de
        clarification joués. Le contrôle du mode humain a lieu **avant** l'appel
        modèle : un run qui demande une approbation sans que personne puisse la
        donner échoue tout de suite, gratuitement, plutôt que de payer un brief pour
        se suspendre ensuite.

        Lève `BriefRefuse` sur un refus. C'est ce qui garantit qu'**aucune tâche
        n'est créée** : la levée précède `_plan`, donc le premier appel payant du
        run après le brief lui-même.
        """
        if mode_brief == MODE_BRIEF_SANS:
            return StepUsage(), None, 0
        arbitre = self._arbitre_brief
        if mode_brief == MODE_BRIEF_HUMAIN and arbitre is None:
            raise ValueError(
                "mode de brief « humain » demandé sans arbitre configuré : "
                "personne ne pourrait trancher, le run resterait suspendu."
            )
        cadrage, brief = await self.etape_brief(objective, journal, projet_id=projet_id)
        if arbitre is None or mode_brief == MODE_BRIEF_AUTO:
            return cadrage, brief, 0
        cadrage, brief, tours = await self._clarifications(
            objective, brief, cadrage, journal, projet_id
        )
        # Mode humain : l'attente est indéfinie et n'est bornée par aucun time-out —
        # même parti pris que la validation d'action sensible (#48). Le time-out par
        # tâche du moteur ne court pas ici : il est armé par l'exécuteur, autour de la
        # réalisation d'une tâche, et aucune tâche n'existe encore.
        decision = await arbitre(
            DemandeBrief(run_id=journal.run_id, objectif=objective, brief=brief)
        )
        if not decision.approuve:
            raise BriefRefuse(
                decision.detail or "brief refusé : la décomposition n'a pas eu lieu."
            )
        return cadrage, decision.retenu(brief), tours

    async def _clarifications(
        self,
        objective: str,
        brief: Brief,
        cadrage: StepUsage,
        journal: RunJournal,
        projet_id: str | None,
    ) -> tuple[StepUsage, Brief, int]:
        """Lève les zones d'ombre du brief par allers-retours **bornés** (#321).

        Tant que le brief porte des questions et que le plafond n'est pas atteint :
        les poser, attendre les réponses, **régénérer le brief entier** en les
        intégrant. Régénérer plutôt que rapiécer est le choix structurant — une
        réponse ne se range pas dans une case connue d'avance (elle peut élargir le
        périmètre, poser une contrainte, réécrire un critère), et le brief reste
        ainsi à tout instant un objet validé contre son schéma, jamais un assemblage
        de morceaux d'âges différents.

        La sortie de boucle est **le plafond, pas l'absence de questions** : un
        modèle qui repose une question à chaque tour ferait boucler indéfiniment une
        condition qui n'attendrait que `questions` vide. Au plafond, ce qui reste est
        inscrit en **hypothèses explicites** — en Python, donc quoi qu'ait répondu le
        modèle au dernier tour — et le brief part en validation : c'est un humain qui
        tranchera, sur l'écran fait pour ça (#322).

        Rend l'usage **cumulé** (chaque régénération est un appel modèle de plus, et
        le grand livre les fusionne déjà sous `ETAPE_BRIEF`, #318), le brief retenu,
        et le **nombre de tours réellement joués** — celui qu'annonce la synthèse.

        Sans arbitre de clarification, ou avec un plafond à zéro, ne fait rien et
        rend le brief tel quel : le comportement exact d'avant ce lot (#320), où les
        questions partent en validation sans avoir été posées.
        """
        arbitre = self._arbitre_clarification
        tours_max = self._tours_clarification
        if arbitre is None or tours_max <= 0:
            return cadrage, brief, 0
        clarifications: tuple[Clarification, ...] = ()
        tour = 0
        while brief.a_des_questions and tour < tours_max:
            tour += 1
            reponses = await arbitre(
                DemandeClarification(
                    run_id=journal.run_id,
                    objectif=objective,
                    brief=brief,
                    tour=tour,
                    tours_max=tours_max,
                )
            )
            # Cumulées, jamais remplacées : le brief est réécrit en entier à chaque
            # tour, donc le modèle a besoin de tout l'historique pour ne pas reperdre
            # ce qu'un tour précédent avait levé.
            clarifications += tuple(reponses)
            usage, brief = await self.etape_brief(
                objective,
                journal,
                projet_id=projet_id,
                clarifications=clarifications,
                dernier_tour=tour >= tours_max,
                tour=tour,
            )
            cadrage = cadrage.fusion(usage)
        if brief.a_des_questions:
            brief = brief.questions_en_hypotheses(motif_sans_reponse(tour))
        return cadrage, brief, tour

    async def _plan(
        self, objective: str, journal: RunJournal, projet_id: str | None = None
    ) -> tuple[StepUsage, list[Task]]:
        """Planifie l'objectif en consignant l'étape (usage et issue) dans le journal.

        Les erreurs de planification sont propagées (sans plan, rien à orchestrer)
        mais consignées d'abord : l'échec reste traçable dans le journal.

        `projet_id` (#222) est porté par l'étape elle-même, alors qu'elle
        précède le plan : la planification est une **dépense du projet** au même
        titre que les tâches (c'est la convention du grand livre, #57), et
        l'omettre creuserait un écart entre le total d'un projet et la somme de
        ses runs.
        """
        debut = perf_counter()
        with collect_usage() as recolte:
            try:
                tasks = await self._orchestrator.plan(objective)
            except Exception as exc:
                journal.consigne(
                    etape="planification",
                    nom="Planification de l'objectif",
                    agent="orchestrateur",
                    role="Orchestrateur",
                    statut=STATUT_ECHEC,
                    entree=objective,
                    sortie="",
                    erreur=str(exc),
                    usage=recolte.total.avec_duree(_ecoule_ms(debut)),
                    projet_id=projet_id,
                )
                raise
        usage = recolte.total.avec_duree(_ecoule_ms(debut))
        journal.consigne(
            etape="planification",
            nom="Planification de l'objectif",
            agent="orchestrateur",
            role="Orchestrateur",
            statut=STATUT_TERMINEE,
            entree=objective,
            sortie=f"{len(tasks)} tâche(s) planifiée(s)",
            usage=usage,
            projet_id=projet_id,
        )
        return usage, tasks

    async def etape_brief(
        self,
        objectif: str,
        journal: RunJournal,
        *,
        sources_extraites: RapportLecture | None = None,
        projet_id: str | None = None,
        clarifications: Sequence[Clarification] = (),
        dernier_tour: bool = False,
        tour: int = 0,
    ) -> tuple[StepUsage, Brief]:
        """Rédige le brief de `objectif` en consignant l'étape au journal (#318).

        Le pendant exact de `_plan` pour l'étape qui la précède : un `collect_usage`
        autour de l'appel modèle, une ligne de journal à l'issue — succès **comme**
        échec —, et l'usage rendu à l'appelant. C'est ce qui fait que le brief
        « ne disparaît pas du coût » : sa ligne entre dans `RunJournal.usage_totale`
        comme n'importe quelle autre, et `RunCost` la comptabilise à part des tâches
        (`ETAPE_BRIEF`) au lieu d'en faire une tâche fantôme.

        `projet_id` (#222) est porté par l'étape pour la même raison qu'en
        planification : le cadrage est une **dépense du projet**, et l'omettre
        creuserait un écart entre le total d'un projet et la somme de ses runs.

        `clarifications`, `dernier_tour` et `tour` (#321) sont les allers-retours
        déjà joués et le rang de celui-ci : l'étape devient **ré-appelable**, chaque
        appel régénérant le brief entier avec une entrée plus riche. Chaque tour
        consigne **sa propre ligne** de journal, avec son numéro — le grand livre les
        fusionne ensuite sous `ETAPE_BRIEF` (#318), si bien que le coût du cadrage
        reste un seul poste tout en gardant, dans la trace, le détail de ce qui a été
        payé pour lever les zones d'ombre.
        """
        debut = perf_counter()
        rang = f" (clarification {tour})" if tour else ""
        with collect_usage() as recolte:
            try:
                brief = await self._orchestrator.brief(
                    objectif,
                    sources_extraites,
                    clarifications,
                    dernier_tour=dernier_tour,
                )
            except Exception as exc:
                journal.consigne(
                    etape=ETAPE_BRIEF,
                    nom=f"Brief de l'objectif{rang}",
                    agent="orchestrateur",
                    role="Orchestrateur",
                    statut=STATUT_ECHEC,
                    entree=objectif,
                    sortie="",
                    erreur=str(exc),
                    usage=recolte.total.avec_duree(_ecoule_ms(debut)),
                    projet_id=projet_id,
                )
                raise
        usage = recolte.total.avec_duree(_ecoule_ms(debut))
        journal.consigne(
            etape=ETAPE_BRIEF,
            nom=f"Brief de l'objectif{rang}",
            agent="orchestrateur",
            role="Orchestrateur",
            statut=STATUT_TERMINEE,
            entree=objectif,
            sortie=(
                f"{len(brief.criteres_acceptation)} critère(s) d'acceptation, "
                f"{len(brief.questions)} question(s)"
            ),
            usage=usage,
            projet_id=projet_id,
        )
        return usage, brief


def _dependants_directs(tasks: Sequence[Task]) -> dict[str, list[str]]:
    """Inverse le graphe de dépendances : pour chaque tâche, qui dépend d'elle.

    C'est le carnet d'adresses du handoff (#44) : une tâche sans dépendant n'a
    personne à qui passer la main (aucune annonce), une tâche à dépendants
    annonce son issue pour les débloquer. Les ids sont dans l'ordre du plan.
    """
    dependants: dict[str, list[str]] = {task.id: [] for task in tasks}
    for task in tasks:
        for dep in task.dependances:
            dependants[dep].append(task.id)
    return dependants


def _consigne_blocage(
    task: Task, insatisfaites: Sequence[TaskResult], journal: RunJournal
) -> TaskResult:
    """Construit et consigne le résultat `bloquee` d'une tâche à l'aval d'un échec (#43).

    `insatisfaites` porte les résultats non réussis des dépendances de `task`
    (échec direct, ou blocage hérité en cascade) : l'erreur les cite pour rendre la
    cause traçable dans le rapport comme au journal. Aucun agent n'a été sollicité
    ni aucun message mis en file — l'usage est nul.
    """
    causes = ", ".join(f"{dep.task_id} ({dep.statut})" for dep in insatisfaites)
    result = TaskResult(
        task_id=task.id,
        titre=task.titre,
        agent="—",
        role="non exécutée",
        competences_requises=task.competences_requises,
        score=0,
        statut=STATUT_BLOQUEE,
        sortie="",
        erreur=(
            f"dépendance(s) non satisfaite(s) : {causes} — tâche bloquée, "
            "jamais exécutée ni mise en file."
        ),
    )
    journal.consigne(
        etape=task.id,
        nom=task.titre,
        agent=result.agent,
        role=result.role,
        statut=result.statut,
        entree=task.description,
        sortie="",
        erreur=result.erreur,
        usage=StepUsage(),
        ticket=task.ticket,
        projet_id=task.projet_id,
    )
    return result
