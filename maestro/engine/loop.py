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

Depuis #1178, un échec **se rattrape** avant de bloquer quoi que ce soit, quand le
rattrapage est armé (`maestro.engine.rattrapage`, armé sur `default()`) : le Chef
de projet juge la cause, une tentative différente part — la tâche reprise
autrement, confiée à un autre agent, ou redécoupée —, les tâches aval sont
ajustées et repartent dès qu'elle aboutit, sans relancer le run. Ce qu'il ne sait
pas lever devient une question dans le fil, et le run attend la réponse. Le
blocage de #43 reste l'issue de ce qui n'a pas pu être rattrapé.

Depuis #1181, ce qui **manque** à une tâche se propose avant de se juger : un
serveur MCP injoignable, un rôle que personne ne couvre (constatés), ou ce qu'un
agent a signalé comme blocage (nommé par le Chef de projet). La tâche est
suspendue, le fil propose le remède — la procédure de la bibliothèque MCP, le rôle
à recruter —, et elle reprend telle quelle une fois qu'on le lui a donné.

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

Entre le plan et la première tâche, l'**équipe est confrontée au plan** (#1227,
[docs/42](../../docs/42-decision-equipe-ajustee-au-plan.md)) : quand la
décomposition appelle un métier que l'équipe du projet n'a pas, le manque est
consigné et — s'il y a un arbitre de renfort — proposé à une personne, qui accepte
ou non (`maestro.engine.renfort`). C'est le seul arrêt de cette boucle qui ne peut
**jamais** l'interrompre : un refus, un silence, un arbitre absent ou en panne
laissent le run continuer avec l'équipe qu'il a, en le disant au journal. Un rôle
recruté pendant l'attente prend ses tâches sans qu'une ligne du plan change —
c'est le routage qui relit le dépôt d'agents, tâche par tâche.

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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any

from maestro.agents.capacity import STATUT_INSTANCES_DERIVEES, CapacityStore
from maestro.agents.catalog import GABARITS_DU_CODE, Agent
from maestro.agents.mcp import McpStore
from maestro.agents.mcp_registry import RegistreMcp
from maestro.agents.permissions import PermissionStore
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.runtime import AgentRuntime
from maestro.agents.secrets import SecretStore
from maestro.agents.store import (
    AgentStore,
    catalogue_du_projet,
    catalogue_hors_projet,
)
from maestro.config import Settings, load_settings
from maestro.deliberation import cle_acte
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
from maestro.engine.cadence import (
    CAUSE_PROJET_NON_VERSIONNE,
    CHOIX_GARDER,
    CHOIX_VERSIONNER,
    HYPOTHESE_VERSIONNEMENT,
    PROPOSITION_ACCEPTEE,
    PROPOSITION_DECLINEE,
    PROPOSITION_ECHOUEE,
    PROPOSITION_EN_ATTENTE,
    PROPOSITION_SANS_REPONSE,
    PROPOSITION_SANS_SUITE,
    STATUT_PROJET_VERSIONNE,
    STATUT_UNE_A_UNE,
    STATUT_VERSIONNEMENT_DECLINE,
    STATUT_VERSIONNEMENT_ECHOUE,
    STATUT_VERSIONNEMENT_SANS_REPONSE,
    STATUT_VERSIONNEMENT_SANS_SUITE,
    VERBE_VERSIONNEMENT,
    Cadence,
    avec_proposition,
    cadence_du_plan,
    consigne_cadence,
    question_du_versionnement,
)
from maestro.engine.executor import (
    ACTEUR_ORCHESTRATEUR,
    ROLE_ORCHESTRATEUR,
    STATUT_BLOQUEE,
    STATUT_ECHEC,
    STATUT_EN_ATTENTE_VALIDATION,
    STATUT_ROLE_MANQUANT,
    STATUT_TERMINEE,
    LocalExecutor,
    TaskExecutor,
    TaskResult,
    _ecoule_ms,
)
from maestro.engine.guardrails import Guardrails
from maestro.engine.pause import PorteExecution
from maestro.engine.plafond import (
    STATUT_PLAFOND_ARRETE,
    STATUT_PLAFOND_ATTEINT,
    STATUT_PLAFOND_REDUIT,
    STATUT_PLAFOND_RELEVE,
    STATUT_PLAFOND_SANS_DECISION,
    ArbitrePlafond,
    DecisionPlafond,
    DemandePlafond,
    Plafonds,
    TacheRestante,
)
from maestro.engine.questions import ArbitreQuestion, DemandeQuestion, identifiant_question
from maestro.engine.rattrapage import (
    CHOIX_PREREQUIS_LEVE,
    RATTRAPAGE_DEFAUT,
    STATUT_PREREQUIS_DECLINE,
    STATUT_PREREQUIS_LEVE,
    STATUT_PREREQUIS_REPONDU,
    STATUT_PREREQUIS_SANS_REPONSE,
    VERBE_PREREQUIS,
    VERBE_RATTRAPAGE,
    Dossier,
    JugeDesEchecs,
    PolitiqueRattrapage,
    Refus,
    Verdict,
    consigne_issue,
    consigne_proposition,
    consigne_question,
    hypothese_du_rattrapage,
    question_du_prerequis,
    question_du_rattrapage,
)
from maestro.engine.renfort import (
    STATUT_RENFORT_DECLINE,
    STATUT_RENFORT_RECRUTE,
    STATUT_RENFORT_SANS_REPONSE,
    ArbitreRenfort,
    DecisionRenfort,
    DemandeRenfort,
)
from maestro.engine.retry import RELANCE_DEFAUT, PolitiqueRelance
from maestro.engine.verification import (
    CONSTAT_NON_TENU,
    SUFFIXE_ETAPE_VERIFICATION,
    Constat,
    Renvoi,
    VerificateurTaches,
)
from maestro.engine.verification import Verdict as VerdictVerification
from maestro.equipe.manque import (
    ManqueAuPlan,
    competences_non_couvertes,
    manque_au_plan,
    role_manquant,
)
from maestro.messaging.handoff import HandoffRelais
from maestro.messaging.mailbox import Mailbox
from maestro.orchestrator.orchestrator import Orchestrator
from maestro.orchestrator.rattrapage import (
    GESTE_ABANDONNER,
    Rattrapage,
    Tentative,
    tache_reprise,
    taches_redecoupees,
)
from maestro.orchestrator.schema import Brief, Clarification, Task, topological_order
from maestro.plan_run import largeur_du_plan, noeuds_du_plan
from maestro.prerequis import GENRE_ROLE, PrerequisManquant, prerequis_du_role
from maestro.projets.store import ProjetStore
from maestro.providers.arbitrage import BornesArbitrage
from maestro.providers.base import ModelProvider
from maestro.references import ReferenceTicket
from maestro.telemetry import (
    RunJournal,
    StepUsage,
    collect_usage,
    resume_controle_depense,
)
from maestro.telemetry.costs import (
    ETAPE_BRIEF,
    ETAPE_CADENCE,
    ETAPE_EQUIPE,
    ETAPE_PLAFOND,
    RunCost,
    TaskCost,
)

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

#: Le passage d'une tâche vers l'exécuteur, porte de pause et plafond compris —
#: celui qu'une tentative de rattrapage emprunte comme une tâche du plan (#1178).
_Executer = Callable[[Task, Sequence[TaskResult]], Awaitable[TaskResult]]


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
        """Rend l'agrégat en Markdown : récap chiffré puis livrable par tâche.

        Chaque section porte le `task_id` de sa tâche (#721). `tache_id` est la
        clé partout où l'on mesure — journal, JSON du rapport, grand livre,
        télémétrie —, et cette surface-ci était **l'exception** que docs/31 §6
        nomme : celle qu'un humain lit. Sans elle, deux tâches de même titre
        routées vers le même rôle rendent deux sections indiscernables, et le
        rapport dit qu'il s'est passé deux choses sans dire lesquelles.
        """
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
            # La clé de la tâche, imprimée ici et nulle part ailleurs jusqu'à
            # #721 : c'est la **dette d'un refus**. docs/31 §6 écarte l'identité
            # d'instance (pas de `slot_id`) au motif que `tache_id` la porte déjà
            # partout où l'on mesure — journal, JSON du rapport, grand livre,
            # métadonnées Langfuse. Une seule surface y échappait, celle-ci : deux
            # tâches de même titre routées vers le même rôle y rendaient deux
            # sections rigoureusement identiques, et rien ne disait laquelle on
            # lisait. Le remède n'est donc pas de nommer l'instance — ce serait un
            # champ dans `Task`, dans le journal, dans les événements, dans les
            # projections et dans le grand livre — mais **d'imprimer la clé qu'on
            # a déjà**. Quand deux remèdes traitent le même symptôme et que l'un
            # coûte cent fois l'autre, le symptôme ne justifie pas le second.
            lignes.append(f"- Tâche : `{r.task_id}`")
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
        # Le catalogue du **câblage**, hors projet (cf. `LocalExecutor`) : les
        # gabarits du code par défaut, jamais ce qu'un projet reçoit (#1042).
        agents: Sequence[Agent] = GABARITS_DU_CODE,
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
        agents_store: AgentStore | None = None,
        modele: str | None = None,
        arbitre_brief: ArbitreBrief | None = None,
        arbitre_clarification: ArbitreClarification | None = None,
        tours_clarification: int | None = None,
        questionneur: ArbitreQuestion | None = None,
        bornes_question: BornesArbitrage | None = None,
        arbitre_renfort: ArbitreRenfort | None = None,
        verificateur: VerificateurTaches | None = None,
        rattrapage: PolitiqueRattrapage | None = None,
        registre_mcp: Callable[[], RegistreMcp] | None = None,
        arbitre_plafond: ArbitrePlafond | None = None,
    ) -> None:
        if max_parallele is not None and max_parallele < 1:
            raise ValueError(f"max_parallele doit être ≥ 1 (reçu : {max_parallele}).")
        self._orchestrator = orchestrator
        # L'équipe **du projet** pour la décomposition (#1041) : les mêmes entrées
        # que l'exécuteur reçoit pour le routage, retenues ici parce que la
        # planification arrive **avant** lui et doit découper sur la même équipe.
        # `agents` reste le catalogue du câblage — ce qui vaut hors projet, et le
        # repli quand le dépôt n'est pas branché. Le dépôt de **surcharges** n'y
        # est plus (#1042) : une surcharge règle un gabarit, pas une équipe.
        self._agents = tuple(agents)
        self._agents_store = agents_store
        self._modele = modele
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
        # À qui proposer de **compléter l'équipe** quand le plan appelle un rôle
        # qu'elle n'a pas (#1227) — en pratique
        # `maestro.controltower.renfort.ArbitreRenfortControlTower`. None
        # (défaut) : le manque est nommé au journal et le run continue avec
        # l'équipe actuelle, c'est-à-dire la conduite d'avant ce lot. Même nature
        # que les trois arbitres ci-dessus : du câblage de déploiement (*où* la
        # proposition est posée), jamais un réglage du lancement.
        self._arbitre_renfort = arbitre_renfort
        # La borne de l'attente de renfort est celle d'un arbitrage (#1227) : le
        # même temps humain, donc le même chiffre — cf. `maestro.engine.renfort`.
        # Elle voyage sur la demande, l'arbitre la tient.
        self._bornes_renfort = bornes_question or BornesArbitrage()
        # Garde-fous du run (#9) : retenus ici pour que le rapport dise quel contrôle
        # de dépense a tenu (#113). Le défaut laisse les plafonds inactifs. En mode
        # distribué (exécuteur injecté), les garde-fous s'appliquent côté worker :
        # ce que retient l'orchestrateur ne reflète alors que ce qu'on lui a passé.
        self._guardrails = guardrails if guardrails is not None else Guardrails()
        # Plafond d'exécutions simultanées (#7) — None : illimité. Utile pour ménager
        # les limites de débit d'un fournisseur sur un plan très large.
        self._max_parallele = max_parallele
        # Messagerie inter-agents (#44) — None : pas de handoff par message, la
        # synchronisation des dépendances reste purement en process. Elle est
        # aussi passée à l'exécuteur local (#720), qui y notifie les mots qu'un
        # agent adresse à un pair : là non plus, None ne retire rien d'essentiel
        # — le journal reste la livraison, et il n'y a alors personne à prévenir.
        self._mailbox = mailbox
        # Le rattrapage d'une tâche en échec (#1178) — None : l'échec barre son
        # aval, la conduite d'avant. Armé, le Chef de projet juge chaque échec aux
        # deux étages où l'on retente (`maestro.engine.rattrapage`) : l'exécuteur
        # lui demande avant chaque relance, la boucle exécute la tentative
        # différente qu'il décide, et pose dans le fil ce qu'il ne sait pas lever.
        # La question part par le même canal qu'une question d'agent (#1023) —
        # d'où le `questionneur` et sa borne, retenus ici aussi —, et un rejeu
        # jugé passager attend le backoff de la relance.
        self._rattrapage = rattrapage
        self._relance = relance
        self._questionneur = questionneur
        self._bornes_question = bornes_question or BornesArbitrage()
        # À qui demander quoi faire quand le run atteint son plafond de dépense
        # (#1182) — en pratique `maestro.controltower.plafond.ArbitrePlafondControlTower`.
        # None : personne, et le run garde l'arrêt sec d'avant (la tâche en vol
        # échoue, les suivantes sont refusées). Même nature que les arbitres
        # ci-dessus : *où* la question est posée est un câblage de déploiement.
        self._arbitre_plafond = arbitre_plafond
        # Les plafonds **en vigueur** de chaque run (#1182) : ceux des garde-fous,
        # relevés sur la décision de la personne. Un seul registre, partagé par
        # l'exécuteur (qui arme le contrôle de chaque tâche) et le juge des échecs
        # (qui dépense sous le même plafond) — ceux de l'exécuteur injecté quand il
        # en tient, pour que la boucle relève le plafond là où les tâches le
        # relisent.
        injectes = executor.plafonds if executor is not None else None
        self._plafonds = injectes if injectes is not None else Plafonds.de(self._guardrails)
        self._juge = (
            JugeDesEchecs(orchestrator, self._equipe, self._plafonds)
            if rattrapage is not None
            else None
        )
        # Un seul renfort en vol par run (#1181) : une décision de renfort revient
        # sur le bus filtrée par **run** (`maestro.controltower.renfort`), si bien
        # que deux tâches parallèles proposant chacune un rôle se verraient
        # répondre la même décision. Elles passent donc l'une après l'autre — un
        # rôle recruté pour la première sert souvent la seconde. Un verrou par
        # run, créé à la demande dans la boucle qui l'attend.
        self._verrous_renfort: dict[str, asyncio.Lock] = {}
        # Une tâche à qui il manque un prérequis constaté se **suspend** (#1181)
        # quand ce moteur peut le proposer : il faut le rattrapage (c'est lui qui
        # propose, puis reprend) et un fil où le poser — la carte d'une question
        # sert tous les genres, le renfort s'y ajoute pour un rôle.
        suspendre = rattrapage is not None and questionneur is not None
        # Frontière d'exécution (#41) : en process par défaut ; un exécuteur injecté
        # (ex. `maestro.queue.CeleryExecutor`) distribue les tâches à des workers.
        # `playbooks` (#78), `capacites` (#86), `mcp` (#104) et `projets` (#224) :
        # les dépôts que l'exécuteur local relit à chaque tâche — l'application à
        # chaud ; ignorés si un exécuteur est injecté (en distribué, chaque worker
        # câble les siens — `relance` (#91) comprise, cf.
        # maestro.queue.worker.configurer_worker).
        # La `mailbox` descend aussi (#720) : c'est la **même** que celle du relais
        # de handoff, et pour deux usages qui ne se confondent pas — la boucle y
        # annonce les fins de tâche, l'exécuteur y notifie le mot qu'un agent
        # adresse à un pair. Deux transports séparés donneraient deux moitiés de
        # messagerie ; un seul, deux producteurs, chacun avec sa promesse.
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
                # Les agents **du projet de la tâche** (#1038) : ce dépôt descend
                # pour la même raison que les quatre au-dessus — un agent recruté
                # pour un projet naît après le câblage, et le catalogue figé du
                # routeur ne peut pas le connaître.
                agents_store=agents_store,
                modele=modele,
                mailbox=mailbox,
                # La question libre d'un agent (#1023) descend jusqu'à
                # l'exécuteur, où elle est posée et consignée : c'est lui qui tient
                # la tâche, le journal et la borne. Comme les deux arbitres de
                # brief ci-dessus, l'arbitre est un **câblage de déploiement**
                # (*où* la question est posée) et non un réglage du run ; ignoré
                # si un exécuteur est injecté, qui câble le sien.
                questionneur=questionneur,
                bornes_question=bornes_question,
                # Le vérificateur des livraisons (#1177) descend lui aussi : c'est
                # l'exécuteur qui tient l'espace de travail où ses contrôles se
                # jouent. Ignoré si un exécuteur est injecté, qui câble le sien.
                verificateur=verificateur,
                # Le juge des relances (#1178) : la relance d'une tentative n'est
                # plus présumée, le Chef de projet la juge. Ignoré si un exécuteur
                # est injecté — un worker ne voit pas le plan du run, et garde la
                # présomption ; la boucle rattrape quand même ce qu'il rend.
                juge=self._juge,
                # Ce qui manque se propose au lieu d'échouer (#1181) : l'exécuteur
                # suspend la tâche — elle attend un geste à l'écran —, et y lit la
                # procédure d'accès d'un serveur MCP dans la bibliothèque.
                suspendre_sur_prerequis=suspendre,
                registre_mcp=registre_mcp,
                # Les plafonds en vigueur (#1182) : ceux que la boucle relève sur
                # décision, relus par l'exécuteur à chaque tâche.
                plafonds=self._plafonds,
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
        questionneur: ArbitreQuestion | None = None,
        arbitre_renfort: ArbitreRenfort | None = None,
        verification: bool = True,
        rattrapage: PolitiqueRattrapage | None = RATTRAPAGE_DEFAUT,
        arbitre_plafond: ArbitrePlafond | None = None,
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
        construits après lui. **Aucune table de runtimes n'est passée** (#1037) :
        l'exécuteur dérive celui de chaque tâche de la fiche de l'agent routé, si
        bien qu'un agent personnalisé travaille outillé — fichiers, écriture dans
        le projet, commandes — au lieu de produire son livrable en texte. Lui
        passer `default_runtimes(...)` le restreindrait aux cinq rôles du code.

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

        `questionneur` (#1023) est **à qui** un agent pose une question libre
        pendant sa tâche — en pratique
        `maestro.controltower.question.ArbitreQuestionControlTower`. None
        (défaut) : le verbe n'est pas servi aux agents de ce moteur, plutôt que
        servi sans aboutir. La **borne** de l'attente, elle, vient de la config
        (`MAESTRO_ARBITRAGE_ATTENTE`) et non d'un paramètre : c'est le même temps
        humain que celui d'un arbitrage, et lui donner un second réglage ferait
        deux chiffres à tenir d'accord pour une seule question — *combien laisse-
        t-on à qui répond ?*

        `arbitre_renfort` (#1227) est **à qui** proposer de compléter l'équipe
        quand le plan appelle un rôle qu'elle n'a pas — en pratique
        `maestro.controltower.renfort.ArbitreRenfortControlTower`. None (défaut) :
        le manque est nommé au journal et le run continue avec l'équipe actuelle.
        Sa **borne** ne passe pas par ici non plus : c'est le même temps humain que
        celui d'un arbitrage, et il n'a qu'un réglage.

        La **vérification des livraisons** (#1177) est **armée par défaut**, comme
        la relance et pour la même raison : ce moteur est celui des vrais runs, et
        une tâche n'y est « Terminée » qu'une fois ses critères vérifiés en
        l'exécutant (`maestro.engine.verification`). Le vérificateur parle au
        fournisseur du run, avec le modèle de `MAESTRO_MODEL` s'il est posé, celui
        de l'agent vérifié sinon. `verification=False` l'éteint — un choix qu'on
        fait en le disant, jamais un défaut.

        Le **rattrapage** d'une tâche en échec (#1178) est **armé par défaut**
        (`RATTRAPAGE_DEFAUT`), comme la relance : sur les vrais runs, un échec est
        jugé par le Chef de projet, retenté autrement, et ce qu'il ne sait pas
        lever est demandé dans le fil — par le `questionneur`, sous la même borne
        qu'une question d'agent. `rattrapage=None` rend la conduite d'avant :
        l'échec barre son aval.

        `arbitre_plafond` (#1182) est **à qui** demander quoi faire quand le run
        atteint son plafond de dépense — en pratique
        `maestro.controltower.plafond.ArbitrePlafondControlTower`. None (défaut) :
        personne, et le run garde l'arrêt sec d'avant.
        """
        from maestro.providers.factory import default_model, provider_from_settings

        settings = settings or load_settings()
        provider = provider_from_settings(settings)
        orchestrator = Orchestrator(provider, model=default_model(settings))
        agents_store = AgentStore.default(settings)
        return cls(
            provider,
            orchestrator,
            # Le catalogue du câblage : ce moteur route hors projet avec lui, et
            # relit l'équipe du projet de chaque tâche qui en a un (#1038, #1042).
            agents=catalogue_hors_projet(agents_store, modele=settings.model),
            guardrails=guardrails,
            mailbox=mailbox,
            playbooks=PlaybookStore.default(settings),
            capacites=CapacityStore.default(settings),
            mcp=McpStore.default(settings),
            secrets=SecretStore.default(settings),
            permissions=PermissionStore.default(settings),
            projets=ProjetStore.default(settings),
            agents_store=agents_store,
            modele=settings.model,
            relance=relance,
            max_parallele=max_parallele,
            arbitre_brief=arbitre_brief,
            arbitre_clarification=arbitre_clarification,
            tours_clarification=tours_clarification,
            questionneur=questionneur,
            bornes_question=BornesArbitrage.from_settings(settings),
            arbitre_renfort=arbitre_renfort,
            verificateur=(
                VerificateurTaches(provider, modele=settings.model) if verification else None
            ),
            rattrapage=rattrapage,
            # La procédure d'accès d'un serveur MCP injoignable (#1181) se lit dans
            # l'allowlist du poste — le seed et ce qu'une admission y a fait entrer.
            registre_mcp=_allowlist_mcp_du_poste,
            arbitre_plafond=arbitre_plafond,
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
        contexte_sources: str = "",
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

        `contexte_sources` (#1172) est ce que la personne a joint à l'objectif, déjà
        lu et **encadré comme donnée** par `contexte_markdown` (ENF-13). Il nourrit
        la première étape qui lit l'objectif : le **brief** et chacune de ses
        régénérations, ou le **plan** d'un run sans brief. Vide (le défaut), rien ne
        change.

        Entre le plan et la première tâche, l'**équipe est confrontée au plan**
        (#1227, `_confronte_equipe`) : quand celui-ci appelle un rôle que l'équipe
        du projet n'a pas, le manque est consigné et — s'il y a un arbitre de
        renfort — proposé à une personne, qui accepte ou non. Cette étape ne
        s'arrête jamais sur un refus ni sur un silence : le run continue avec
        l'équipe actuelle, en le disant. Sans projet, sans équipe ou sans manque,
        elle ne fait rien et ne consigne rien.
        """
        journal = journal if journal is not None else RunJournal()
        mode_brief = mode_brief_valide(mode_brief)
        cadrage, brief, tours_clarification = await self._cadrage(
            objective, journal, mode_brief, projet_id, contexte_sources
        )
        # L'entrée de la décomposition : le brief retenu, ou l'objectif brut en mode
        # « sans ». `Brief.synthese()` plutôt que le seul `brief.objectif` — c'est le
        # texte que l'humain a relu pour approuver, périmètre et critères compris, et
        # décomposer moins que ce qui a été approuvé rendrait l'approbation trompeuse.
        # Les sources suivent la même règle : le brief les a digérées, sinon le plan
        # est le premier à les lire.
        entree_plan = objective if brief is None else brief.synthese()
        plan_usage, tasks = await self._plan(
            entree_plan,
            journal,
            projet_id,
            contexte_sources=contexte_sources if brief is None else "",
        )
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
        # L'équipe confrontée au plan (#1227), **avant** la première tâche : c'est
        # le seul moment où un recrutement change encore quelque chose. Elle ne
        # touche ni au plan ni aux tâches — un rôle créé pendant l'attente est lu
        # par le routage, tâche par tâche.
        await self._confronte_equipe(objective, tasks, projet_id, journal)
        # Ce que le plan laisse partir de front devient le plafond d'instances du
        # run (#1299), annoncé avant la première tâche — c'est le même instant.
        self._derive_les_instances(tasks, projet_id, journal)
        ordered = topological_order(tasks)
        # La cadence du run (#1298) : pourquoi ses tâches passeront une à une, dite
        # **avant** la première — et, sur un projet non versionné, la proposition
        # de le versionner, posée dans le fil sans que le run l'attende.
        versionnement = self._dit_la_cadence(ordered, projet_id, journal)
        dependants = _dependants_directs(ordered)
        # Le juge des échecs apprend l'objectif et le plan (#1178) : c'est de là
        # qu'il lit ce qui attend une tâche en échec, aux deux étages.
        if self._juge is not None:
            self._juge.suit(journal.run_id, objective, ordered)
        # Les tâches aval dont un rattrapage a ajusté la description (#1178) : lues
        # juste avant leur exécution, donc après celle qu'elles attendaient — c'est
        # le seul moment où les ajuster ne réécrit rien qui ait déjà tourné.
        ajustements: dict[str, str] = {}
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
        # La décision au plafond de dépense (#1182) : armée quand quelqu'un peut
        # la rendre **et** que l'exécuteur sait relever le plafond du run. Sinon
        # rien ne change — l'arrêt sec d'avant, que le journal dit déjà.
        plafonds = self._executor.plafonds
        au_plafond = (
            _AuPlafond(
                self._arbitre_plafond,
                plafonds,
                journal,
                objective,
                projet_id,
                # Ce qui reste à faire : les tâches du plan dont l'issue n'est pas
                # encore connue — celles mises de côté comprises.
                lambda: [t for t in ordered if t.id in en_vol and not en_vol[t.id].done()],
            )
            if self._arbitre_plafond is not None and plafonds is not None
            else None
        )
        if au_plafond is not None and plafonds is not None:
            plafonds.suspendre(journal.run_id)
        # Les livrables **refaits** à la demande d'une QA (#1177) : la dernière
        # issue d'une tâche renvoyée à son rôle producteur. Ce qui est lu d'une
        # tâche, par la QA qui la rejuge comme par le rapport, passe par
        # `resultat_de` — jamais par `en_vol` seul, qui garde la première issue.
        refaits: dict[str, TaskResult] = {}
        par_id = {task.id: task for task in ordered}

        def resultat_de(tache_id: str) -> TaskResult:
            if tache_id in refaits:
                return refaits[tache_id]
            return en_vol[tache_id].result()

        async def _executer(task: Task, dependances: Sequence[TaskResult]) -> TaskResult:
            # Le seul passage vers l'exécuteur, pour une tâche du plan comme pour
            # une tentative de rattrapage (#1178) ou une reprise demandée par la QA
            # (#1177) : la porte de pause, le plafond d'exécutions simultanées et
            # la décision au plafond de dépense (#1182) valent pour toutes.
            depense = StepUsage()
            courante = task
            dernier: TaskResult | None = None
            while True:
                if au_plafond is not None and au_plafond.ecartee(task.id):
                    # Écartée par la personne au plafond : elle ne part pas, et
                    # ce qui l'attend se bloque en cascade (#43).
                    return au_plafond.solde(task, dernier, depense)
                if porte is not None:
                    # La pause (#477), et elle est **ici** : la dernière ligne
                    # avant que quoi que ce soit ne soit engagé. Franchie avant le
                    # sémaphore, pour la raison qui vaut déjà des dépendances —
                    # une tâche qui attend n'occupe pas un créneau. Une tâche déjà
                    # passée n'a plus de porte devant elle : elle finit, et c'est
                    # ce qui distingue une pause d'une annulation.
                    await porte.franchir()
                if semaphore is None:
                    result = await self._executor.execute(courante, dependances, journal)
                else:
                    async with semaphore:
                        result = await self._executor.execute(courante, dependances, journal)
                if au_plafond is None or not result.au_plafond:
                    if depense.appels or depense.tokens_total or depense.cout_usd is not None:
                        # Ce que les tentatives mises de côté avaient dépensé
                        # voyage avec l'issue : le rapport dit ce que la tâche a
                        # coûté en tout, comme le grand livre.
                        result = replace(result, usage=depense.fusion(result.usage))
                    return result
                # Au plafond (#1182) : la tâche est mise de côté — sa dépense est
                # au grand livre, son travail sur sa branche — et le run demande.
                # Le créneau est rendu **avant** d'attendre : une personne qui
                # répond dans une heure ne retient pas le parallélisme du run.
                depense = depense.fusion(result.usage)
                commencee = result.agent != "—"
                if commencee or dernier is None:
                    dernier = result
                decision = await au_plafond.decision(task, result, commencee=commencee)
                if decision is None or not decision.reprend:
                    return au_plafond.solde(task, dernier, depense)
                if commencee:
                    courante = _reprise_au_plafond(courante)

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
                if task.id in ajustements:
                    # Un rattrapage en amont a changé ce que cette tâche reçoit
                    # (#1178) : elle part sur la description qui en tient compte.
                    task = replace(task, description=ajustements[task.id])
                result = await _executer(task, dependances)
                if not result.ok:
                    # Une tâche en échec se rattrape (#1178) : jugée, retentée
                    # autrement, ou demandée dans le fil. L'aval l'attend — c'est
                    # son `await` ci-dessus —, donc il repart sur ce qu'elle rend,
                    # sans que rien d'autre ne soit relancé.
                    result = await self._rattrape(
                        task, result, dependances, journal, _executer, ajustements
                    )
                if result.ok and result.renvois:
                    # Le verdict « non conforme » d'une QA (#1177) : les livrables
                    # visés repartent à leur rôle producteur, puis la QA rejuge. Un
                    # producteur repart sur la description qu'il a reçue, ajustement
                    # d'un rattrapage compris (#1178).
                    result = await self._renvoie_aux_producteurs(
                        task,
                        result,
                        dependances,
                        journal,
                        par_id={
                            tache_id: (
                                replace(tache, description=ajustements[tache_id])
                                if tache_id in ajustements
                                else tache
                            )
                            for tache_id, tache in par_id.items()
                        },
                        resultat_de=resultat_de,
                        executer=_executer,
                        refaits=refaits,
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
            if au_plafond is not None:
                # Un run annulé pendant qu'il attend sa décision emporte la
                # question : personne n'attend plus la réponse.
                au_plafond.fermer()
            if relais is not None:
                await relais.fermer()
            if versionnement is not None:
                await self._solde_le_versionnement(versionnement, projet_id, journal)

        # Le plafond **en vigueur** à la fin du run (#1182) : relevé sur décision,
        # c'est lui que le contrôle de dépense a tenu jusqu'au bout.
        plafond_cout_usd, plafond_tokens = self._plafonds.en_vigueur(journal.run_id)
        return RunReport(
            objectif=objective,
            resultats=tuple(resultat_de(task.id) for task in ordered),
            run_id=journal.run_id,
            planification=plan_usage,
            plafond_cout_usd=plafond_cout_usd,
            plafond_tokens=plafond_tokens,
            mode_brief=mode_brief,
            brief=brief,
            cadrage=cadrage,
            tours_clarification=tours_clarification,
        )

    async def _renvoie_aux_producteurs(
        self,
        task: Task,
        result: TaskResult,
        dependances: Sequence[TaskResult],
        journal: RunJournal,
        *,
        par_id: Mapping[str, Task],
        resultat_de: Callable[[str], TaskResult],
        executer: Callable[[Task, Sequence[TaskResult]], Awaitable[TaskResult]],
        refaits: dict[str, TaskResult],
    ) -> TaskResult:
        """Le verdict « non conforme » d'une QA renvoie le livrable à son rôle producteur (#1177).

        Renverse « sans rétro-boucle automatique » (docs/04 §QA) : au POC, le
        verdict de la QA éclairait une décision humaine et ne changeait rien au
        run — une tâche jugée non conforme restait verte. Maestro n'est pas un POC.

        `task` est la tâche qui juge (en pratique une QA), `result` son issue, qui
        nomme les livrables amont qu'elle juge non conformes (`TaskResult.renvois`,
        lu par le vérificateur de la tâche — le modèle, jamais un lexique). Pour
        chacun, dans cet ordre :

        1. le renvoi est **consigné** sur la tâche productrice
           (`:verification`, non tenue, les preuves de la QA en constat) — c'est
           ce que son détail montre, et ce que le fil dit ;
        2. la tâche productrice est **réexécutée**, sa description suivie des
           défauts et de leurs preuves : par son rôle, sous ses propres
           vérifications, dans la même branche ou la même racine ;
        3. la QA **rejuge**, sur le livrable refait.

        La QA évalue toujours et ne réécrit jamais : elle **renvoie**. Ce qui
        arrête la boucle est un fait, comme pour la vérification d'une tâche
        (`maestro.engine.verification`) : la QA juge conforme, le budget du run
        refuse une exécution de plus, un producteur échoue sa propre reprise, ou
        la correction **n'a rien fait gagner** — autant de défauts bloquants ou
        plus que la meilleure revue précédente. Dans ce dernier cas, la tâche
        productrice finit en **échec motivé** (`_consigne_non_conforme`) : jamais
        un vert sur un livrable qu'une QA déclare non conforme.

        Limite dite : une tâche qui dépendait du livrable renvoyé **sans** passer
        par la QA a pu partir sur la première version — la boucle ne rejoue que la
        paire producteur → QA. Le cas courant (une QA en bout de chaîne) n'en a pas.
        """
        dependances = list(dependances)
        meilleur: int | None = None
        while result.ok and result.renvois:
            amont = {dep.task_id: dep for dep in dependances}
            renvois = [
                r for r in result.renvois if r.tache_id in amont and amont[r.tache_id].ok
            ]
            if not renvois:
                break
            defauts = sum(r.defauts for r in renvois)
            if meilleur is not None and defauts >= meilleur:
                for renvoi in renvois:
                    refaits[renvoi.tache_id] = _consigne_non_conforme(
                        par_id[renvoi.tache_id], amont[renvoi.tache_id], task, renvoi, journal
                    )
                break
            meilleur = defauts
            for renvoi in renvois:
                producteur = par_id[renvoi.tache_id]
                _consigne_renvoi(producteur, amont[renvoi.tache_id], task, renvoi, journal)
                refait = await executer(
                    replace(
                        producteur,
                        description=producteur.description + _retour_qa(task, renvoi),
                    ),
                    [resultat_de(dep) for dep in producteur.dependances],
                )
                self._solde_si_suspendue(producteur, refait, journal)
                # L'usage d'une reprise **s'ajoute** à celui des exécutions
                # précédentes : le rapport garde une issue par tâche, et il ne
                # doit pas perdre ce que la première a coûté.
                refaits[producteur.id] = replace(
                    refait, usage=amont[renvoi.tache_id].usage.fusion(refait.usage)
                )
            dependances = [refaits.get(dep.task_id, dep) for dep in dependances]
            if not all(dep.ok for dep in dependances):
                # Un producteur a échoué sa reprise — son issue le dit, motivée par
                # ses propres vérifications ; la QA n'a plus rien à rejuger.
                break
            rejuge = await executer(task, dependances)
            self._solde_si_suspendue(task, rejuge, journal)
            result = replace(rejuge, usage=result.usage.fusion(rejuge.usage))
        return result

    def _solde_si_suspendue(self, task: Task, result: TaskResult, journal: RunJournal) -> None:
        """Solde l'attente d'une exécution que rien ne proposera (#1181).

        Seul le rattrapage d'une tâche du plan propose un prérequis. Une exécution
        faite ailleurs — une tâche d'un redécoupage, la reprise qu'une QA renvoie
        (#1177) — dont l'exécuteur a suspendu la carte ne serait jamais reprise :
        son échec, qui dit ce qui manquait, remonte à qui l'a lancée, et sa carte
        doit le dire tout de suite au lieu d'attendre un geste.
        """
        if self._executor.suspendue(result):
            self._consigne_la_carte(task, result, STATUT_ECHEC, result.erreur or "", journal)

    async def _cadrage(
        self,
        objective: str,
        journal: RunJournal,
        mode_brief: str,
        projet_id: str | None,
        contexte_sources: str = "",
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
        cadrage, brief = await self.etape_brief(
            objective, journal, projet_id=projet_id, contexte_sources=contexte_sources
        )
        if arbitre is None or mode_brief == MODE_BRIEF_AUTO:
            return cadrage, brief, 0
        cadrage, brief, tours = await self._clarifications(
            objective, brief, cadrage, journal, projet_id, contexte_sources
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
        contexte_sources: str = "",
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
            # Les sources à chaque tour, pas seulement au premier : le brief est
            # régénéré en entier, et un tour qui ne les verrait plus les perdrait.
            usage, brief = await self.etape_brief(
                objective,
                journal,
                contexte_sources=contexte_sources,
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
        self,
        objective: str,
        journal: RunJournal,
        projet_id: str | None = None,
        *,
        contexte_sources: str = "",
    ) -> tuple[StepUsage, list[Task]]:
        """Planifie l'objectif en consignant l'étape (usage et issue) dans le journal.

        Les erreurs de planification sont propagées (sans plan, rien à orchestrer)
        mais consignées d'abord : l'échec reste traçable dans le journal.

        `projet_id` (#222) est porté par l'étape elle-même, alors qu'elle
        précède le plan : la planification est une **dépense du projet** au même
        titre que les tâches (c'est la convention du grand livre, #57), et
        l'omettre creuserait un écart entre le total d'un projet et la somme de
        ses runs.

        L'étape de succès porte aussi le **graphe du plan** (#490,
        `maestro.plan_run`) : un nœud par tâche, ses dépendances, son ossature de
        checklist. C'est le seul chemin par lequel les arêtes quittent le moteur,
        et il passe **par cette étape-là** parce que c'est l'instant où le plan
        existe et où il est figé — le publier tâche par tâche, au fil des
        démarrages, donnerait à lire une découverte progressive d'un ordre qui a
        été décidé une fois pour toutes. Un plan **en échec** n'en porte aucun :
        il n'y a pas de graphe à dessiner.

        Le découpage se fait sur l'**équipe du projet** (#1041) : les rôles et les
        compétences transmis à l'orchestrateur sont ceux des agents de `projet_id`
        — les mêmes fiches que le routage lira tâche par tâche
        (`LocalExecutor._equipe`), par la même règle
        (`maestro.agents.store.catalogue_du_projet`). C'est ce qui fait qu'un tag
        proposé au découpage est un tag que quelqu'un sait prendre.
        """
        debut = perf_counter()
        with collect_usage() as recolte:
            try:
                tasks = await self._orchestrator.plan(
                    objective,
                    equipe=self._equipe(projet_id),
                    contexte_sources=contexte_sources,
                )
            except Exception as exc:
                journal.consigne(
                    etape="planification",
                    nom="Planification de l'objectif",
                    agent=ACTEUR_ORCHESTRATEUR,
                    role=ROLE_ORCHESTRATEUR,
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
            agent=ACTEUR_ORCHESTRATEUR,
            role=ROLE_ORCHESTRATEUR,
            statut=STATUT_TERMINEE,
            entree=objective,
            sortie=f"{len(tasks)} tâche(s) planifiée(s)",
            usage=usage,
            projet_id=projet_id,
            plan=noeuds_du_plan(tasks),
        )
        return usage, tasks

    async def _confronte_equipe(
        self,
        objective: str,
        tasks: Sequence[Task],
        projet_id: str | None,
        journal: RunJournal,
    ) -> None:
        """Confronte l'équipe du projet au plan, et propose de la compléter (#1227).

        L'étape que #1227 ajoute entre la décomposition et l'exécution. Trois
        choses, dans cet ordre, et l'ordre est la décision :

        1. **le manque est calculé** sur l'équipe du projet et sur ce que le plan
           demande (`manque_au_plan`, fonction pure) ;
        2. **il est consigné** — toujours, même sans arbitre et même si personne ne
           répond. C'est la ligne qui manquait au run du 2026-09-22 : *« aucune
           modification de l'équipe n'a été suggérée »* était vrai jusque dans le
           journal ;
        3. **il est proposé**, s'il y a quelqu'un à qui le proposer et un poste à
           pourvoir. La décision revient consignée à son tour.

        Rien ne s'arrête ici, jamais. Un manque qu'aucun gabarit ne couvre, un
        arbitre absent, un refus, une absence de réponse, un arbitre qui lève : le
        run continue avec l'équipe actuelle, en le disant. C'est l'inverse du
        brief (`BriefRefuse`), et pour une raison qui n'est pas une nuance de
        style — un plan reste exécutable par l'équipe qu'on a, tandis qu'un brief
        refusé n'a rien à décomposer.

        **Hors projet, rien à confronter** : les tâches d'un run sans projet sont
        routées sur le catalogue du câblage, dont l'équipe ne se recrute pas —
        c'est le dépôt qui la livre. Même abstention sur un projet dont le dépôt
        d'agents n'est pas branché (`None`) : « je ne sais pas qui prendra » n'est
        pas « il manque quelqu'un », la règle de `_sans_equipe` côté fil (#1146).
        """
        if projet_id is None:
            return
        equipe = catalogue_du_projet(self._agents_store, projet_id, self._modele)
        if not equipe:
            return
        manque = manque_au_plan(tasks, equipe)
        if manque is None:
            return
        self._consigne_manque(manque, projet_id, journal)
        # Quoi qu'il advienne de la proposition, le run fait son travail avec
        # l'équipe qu'il aura (#1260) : ce que **personne** n'y couvre va au rôle
        # le plus proche au lieu de partir « à assigner ». Un refus, un silence,
        # personne à qui proposer, rien à proposer — ou un second métier absent
        # qu'on n'a jamais proposé, puisqu'on n'en propose qu'un par run (run
        # `2e7f7278a991` : Designer recruté, validation QA « à assigner », 2/3).
        # Ce qu'un rôle recruté couvre n'est pas touché : il le couvre.
        self._executor.continuer_avec_l_equipe(journal.run_id)
        if self._arbitre_renfort is None or not manque.recrutable:
            return
        demande = DemandeRenfort(
            run_id=journal.run_id,
            projet_id=projet_id,
            objectif=objective,
            manque=manque,
            attente_s=self._bornes_renfort.attente_s,
        )
        try:
            decision = await self._arbitre_renfort(demande)
        except asyncio.CancelledError:
            # Le run est annulé, pas la proposition : l'annulation doit remonter
            # intacte, comme partout dans cette boucle.
            raise
        except Exception as echec:  # noqa: BLE001 — un canal muet ne condamne pas un plan
            # Fail-safe dans le sens utile : ce qui est en jeu n'est pas un acte
            # sensible mais une **amélioration** de l'équipe. Un bus refermé, une
            # Control Tower arrêtée, un fil illisible — le plan reste exécutable
            # par l'équipe qu'on a, et la cause s'écrit au journal.
            decision = DecisionRenfort(
                approuve=False,
                detail=f"la proposition de renfort n'a pas abouti : {echec}",
                sans_reponse=True,
            )
        if decision.approuve:
            # Le rôle recruté pour ce plan prend les tâches qui demandaient son
            # métier (#1260) — ce que le fil vient de promettre en recrutant.
            self._executor.equipe_completee(
                journal.run_id, manque.manque.couvre or manque.manque.competences
            )
        self._consigne_renfort(manque, decision, projet_id, journal)

    def _derive_les_instances(
        self, tasks: Sequence[Task], projet_id: str | None, journal: RunJournal
    ) -> None:
        """Dérive de la largeur du plan le plafond d'instances du run, et l'annonce (#1299).

        Le retex du 2026-09-24 : quatre tâches indépendantes du même rôle passaient en
        file, parce qu'un agent jamais réglé ne prenait qu'une tâche à la fois
        (`INSTANCES_DEFAUT`). La largeur du plan — ce qu'il laisse partir de front,
        la même mesure que la vue du pipeline (`largeur_du_plan`) — devient le
        plafond de ces agents, borné par un plafond global. C'est l'exécuteur qui
        l'applique, lui qui tient la jauge ; la boucle ne fait que lui dire la largeur.

        L'annonce est une étape de run `equipe` — l'équipe confrontée au plan, dont
        c'est la seconde moitié : **combien** de tâches chaque rôle prendra de front.
        Elle porte le chiffre et son origine, les agents réglés à la main qui gardent
        leur réglage, et le plafond du run s'il y en a un. Usage nul : dériver un
        entier ne sollicite aucun modèle.

        Rien à dire hors projet, ni quand l'exécuteur n'applique rien — pas de jauge,
        exécuteur distribué, projet non versionné (#839 y garde une tâche à la fois,
        et c'est à la cadence du run de le dire, #1298). Rien non plus sur un plan
        **en chaîne** : il n'y a rien à mener de front, et la cadence du run dit déjà
        pourquoi ses tâches passent une à une — deux lignes pour un même fait feraient
        du fil d'activité un écho.
        """
        if projet_id is None:
            return
        largeur = largeur_du_plan(noeuds_du_plan(tasks))
        derivees = self._executor.derive_les_instances(journal.run_id, projet_id, largeur)
        if derivees is None or largeur <= 1:
            return
        journal.consigne(
            etape=ETAPE_EQUIPE,
            nom="Tâches de front",
            agent=ACTEUR_ORCHESTRATEUR,
            role=ROLE_ORCHESTRATEUR,
            statut=STATUT_INSTANCES_DERIVEES,
            entree=(
                f"largeur du plan : {largeur} · plafond global : {derivees.plafond_global} "
                f"· origine : {derivees.origine}"
            ),
            sortie=derivees.phrase(parallelisme=self._max_parallele),
            usage=StepUsage(),
            projet_id=projet_id,
        )

    def _consigne_manque(
        self, manque: ManqueAuPlan, projet_id: str, journal: RunJournal
    ) -> None:
        """Écrit au journal le rôle que le plan appelle et que l'équipe n'a pas.

        Étape de **run** (`equipe`), comme la planification et le brief : elle ne
        porte sur aucune tâche — le manque est celui du plan entier — et le pont
        Control Tower en fait une activité d'orchestrateur, jamais une carte de
        Kanban (`maestro.controltower.bridge`, `maestro.telemetry.costs`).

        Usage nul : confronter des tags à des fiches ne sollicite aucun modèle.
        Le statut est celui du rôle manquant au routage (#1041) — c'est le même
        fait, constaté un cran plus tôt, et lui donner un mot de plus obligerait
        trois lecteurs à traiter à l'identique deux mots pour une seule chose.
        """
        journal.consigne(
            etape=ETAPE_EQUIPE,
            nom="L'équipe confrontée au plan",
            agent=ACTEUR_ORCHESTRATEUR,
            role=ROLE_ORCHESTRATEUR,
            statut=STATUT_ROLE_MANQUANT,
            entree="",
            sortie=manque.phrase(),
            usage=StepUsage(),
            projet_id=projet_id,
        )

    def _consigne_renfort(
        self,
        manque: ManqueAuPlan,
        decision: DecisionRenfort,
        projet_id: str,
        journal: RunJournal,
    ) -> None:
        """Écrit au journal ce que la proposition de renfort a donné.

        Une seconde ligne et non une réécriture de la première : le manque est un
        **fait** (il a été constaté, il reste vrai même si personne n'a recruté),
        l'issue est une **décision**. Fondre les deux ferait disparaître le constat
        dès qu'on décline, c'est-à-dire exactement quand il faut pouvoir le relire.

        Les trois issues portent leur statut : recrutée, refusée, ou sans réponse.
        La dernière n'est pas un refus (cf. `DecisionRenfort`) — et le run repart
        dans les trois cas.
        """
        statut = (
            STATUT_RENFORT_RECRUTE
            if decision.approuve
            else STATUT_RENFORT_SANS_REPONSE
            if decision.sans_reponse
            else STATUT_RENFORT_DECLINE
        )
        suite = (
            "le run reprend avec l'équipe complétée"
            if decision.approuve
            else "le run continue avec l'équipe actuelle : ces tâches vont au "
            "rôle le plus proche"
        )
        # Ce que le rôle proposé ne couvre pas (#1260) — un second métier absent,
        # qu'on n'a pas proposé : il se **dit**, puisqu'il ira au plus proche.
        reste = sorted(set(manque.manque.competences) - set(manque.manque.couvre))
        if decision.approuve and reste:
            suite += (
                f" ; personne ne couvre encore {', '.join(reste)}, ces tâches vont "
                "au rôle le plus proche"
            )
        detail = decision.detail.strip()
        journal.consigne(
            etape=ETAPE_EQUIPE,
            nom=f"Renfort « {manque.manque.role} »",
            agent=ACTEUR_ORCHESTRATEUR,
            role=ROLE_ORCHESTRATEUR,
            statut=statut,
            entree="",
            sortie=f"{detail} — {suite}" if detail else suite,
            usage=StepUsage(),
            projet_id=projet_id,
        )

    def _dit_la_cadence(
        self, tasks: Sequence[Task], projet_id: str | None, journal: RunJournal
    ) -> _Versionnement | None:
        """Dit pourquoi les tâches passeront une à une, et propose ce qui les libérerait (#1298).

        La cause est lue là où le moteur la sait, jamais devinée : la forme du plan
        (`cadence_du_plan`), et le régime que l'exécuteur **applique** au projet
        (`TaskExecutor.atelier_du_projet`, l'atelier de #839). Elle est consignée
        — toujours, même sans personne à qui proposer quoi que ce soit : c'est la
        ligne que la vue du run et le fil relisent.

        Sur un projet non versionné, ce qui la lèverait se **propose** : versionner
        le projet, sur la carte d'une question du fil (le canal de #1023, celui des
        prérequis de #1181). À la différence du renfort (#1227), le run **ne
        l'attend pas** : un plan reste exécutable une tâche à la fois, et le faire
        patienter des minutes pour une amélioration coûterait plus que la lenteur
        qu'elle corrige. L'accord arrive quand il arrive ; l'exécuteur versionne
        alors **entre deux tâches** (`versionner_le_projet`), et les suivantes
        partent de front. Rend la proposition en vol, que la fin du run solde.

        Rien n'est proposé sans canal (pas de questionneur) : la cause est dite, et
        c'est tout — une carte que personne ne verrait serait une promesse creuse.
        """
        atelier = self._executor.atelier_du_projet(projet_id) if projet_id is not None else None
        cadence = cadence_du_plan(tasks, atelier=atelier)
        if cadence is None:
            return None
        proposer = (
            cadence.cause == CAUSE_PROJET_NON_VERSIONNE
            and self._questionneur is not None
            and projet_id is not None
        )
        if proposer:
            cadence = avec_proposition(cadence, PROPOSITION_EN_ATTENTE)
        self._consigne_cadence(journal, cadence, STATUT_UNE_A_UNE, projet_id)
        if not proposer or projet_id is None:
            return None
        versionnement = _Versionnement(cadence=cadence)
        versionnement.tache = asyncio.create_task(
            self._propose_de_versionner(versionnement, projet_id, journal)
        )
        return versionnement

    async def _propose_de_versionner(
        self, versionnement: _Versionnement, projet_id: str, journal: RunJournal
    ) -> None:
        """Pose la proposition dans le fil, et l'applique sur accord — jamais d'office (#1298).

        Le **seul** geste qui écrive dans le projet est le choix « Versionner le
        projet » de la carte : c'est l'accord de l'arbitrage des actes, rendu sur
        ce qui sera écrit (un dépôt local, un premier commit de l'existant). Une
        phrase tapée, « Garder tel quel », un silence jusqu'à la borne ou une
        question qui n'a pas pu partir laissent tout en l'état — et chacune de ces
        issues est consignée, comme celle d'une mise sous Git refusée.
        """
        cadence = versionnement.cadence
        reponse, motif = await self._demande_du_run(
            question_du_versionnement(cadence),
            journal,
            projet_id,
            titre=f"Versionner le projet « {cadence.projet} »",
            choix=(CHOIX_VERSIONNER, CHOIX_GARDER),
            hypothese=HYPOTHESE_VERSIONNEMENT,
            verbe=VERBE_VERSIONNEMENT,
        )
        if reponse is None:
            self._issue_du_versionnement(
                versionnement,
                PROPOSITION_SANS_REPONSE,
                STATUT_VERSIONNEMENT_SANS_REPONSE,
                f"{motif} — le run continue une tâche à la fois, le projet reste tel quel",
                projet_id,
                journal,
            )
            return
        choix = reponse.strip()
        if choix != CHOIX_VERSIONNER:
            self._issue_du_versionnement(
                versionnement,
                PROPOSITION_DECLINEE,
                STATUT_VERSIONNEMENT_DECLINE,
                (
                    "décliné d'un geste"
                    if choix == CHOIX_GARDER
                    else f"réponse « {choix} » : seul le geste « {CHOIX_VERSIONNER} » écrit "
                    "dans le projet"
                )
                + " — le run continue une tâche à la fois, le projet reste tel quel",
                projet_id,
                journal,
            )
            return
        versionnement.en_ecriture = True
        try:
            projet = await self._executor.versionner_le_projet(projet_id)
        except asyncio.CancelledError:
            raise
        except Exception as refus:  # noqa: BLE001 — un refus motivé ne condamne pas le run
            self._issue_du_versionnement(
                versionnement,
                PROPOSITION_ECHOUEE,
                STATUT_VERSIONNEMENT_ECHOUE,
                f"accepté, mais la mise sous Git a échoué : {refus} — le run continue une "
                "tâche à la fois, le projet reste tel quel",
                projet_id,
                journal,
                detail=str(refus),
            )
            return
        base = projet.vcs.branche_base if projet.vcs is not None else ""
        self._issue_du_versionnement(
            versionnement,
            PROPOSITION_ACCEPTEE,
            STATUT_PROJET_VERSIONNE,
            "versionné sur accord — dépôt Git local"
            + (f", branche « {base} »" if base else "")
            + ", premier commit de l'existant : les tâches suivantes partent chacune dans "
            "sa copie, de front",
            projet_id,
            journal,
        )

    async def _solde_le_versionnement(
        self, versionnement: _Versionnement, projet_id: str | None, journal: RunJournal
    ) -> None:
        """À la fin du run, solde la proposition restée en vol (#1298).

        Une mise sous Git **en cours** va à son terme : elle est courte, se fait sous
        l'atelier, et l'interrompre laisserait le projet versionné sans que le
        journal le dise. Une proposition qui attend encore sa réponse est retirée —
        le run est fini, il n'y a plus de tâche à libérer — et la cause le dit.
        """
        tache = versionnement.tache
        if tache is None or tache.done():
            return
        if versionnement.en_ecriture:
            await asyncio.gather(tache, return_exceptions=True)
            return
        tache.cancel()
        await asyncio.gather(tache, return_exceptions=True)
        self._issue_du_versionnement(
            versionnement,
            PROPOSITION_SANS_SUITE,
            STATUT_VERSIONNEMENT_SANS_SUITE,
            "le run s'est achevé avant la réponse : rien n'a été versionné",
            projet_id,
            journal,
        )

    def _issue_du_versionnement(
        self,
        versionnement: _Versionnement,
        proposition: str,
        statut: str,
        sortie: str,
        projet_id: str | None,
        journal: RunJournal,
        *,
        detail: str = "",
    ) -> None:
        """Consigne ce qu'est devenue la proposition, et la cause telle qu'elle est désormais."""
        versionnement.cadence = avec_proposition(versionnement.cadence, proposition, detail)
        self._consigne_cadence(journal, versionnement.cadence, statut, projet_id, sortie=sortie)

    def _consigne_cadence(
        self,
        journal: RunJournal,
        cadence: Cadence,
        statut: str,
        projet_id: str | None,
        *,
        sortie: str = "",
    ) -> None:
        """La cadence au journal, au nom de l'orchestrateur — étape de run, usage nul."""
        consigne_cadence(
            journal,
            cadence,
            statut,
            agent=ACTEUR_ORCHESTRATEUR,
            role=ROLE_ORCHESTRATEUR,
            projet_id=projet_id,
            sortie=sortie,
        )

    async def _demande_du_run(
        self,
        texte: str,
        journal: RunJournal,
        projet_id: str | None,
        *,
        titre: str,
        choix: tuple[str, ...],
        hypothese: str,
        verbe: str,
    ) -> tuple[str | None, str]:
        """Pose dans le fil une question **du run**, et attend la réponse — bornée (#1298).

        Le pendant de `_demande` pour une question qui ne porte sur aucune tâche :
        même canal (la carte d'une question, #1023), même borne
        (`BornesArbitrage.attente_s`, le seul réglage du temps humain), mais aucune
        tâche à qui la rattacher — l'identifiant se range sous l'étape du run
        (`ETAPE_CADENCE`), et l'issue est consignée par l'appelant sur cette étape.

        Rend la réponse et, quand il n'y en a pas, pourquoi : personne n'a répondu
        à temps, ou la question n'a pas pu partir.
        """
        questionneur = self._questionneur
        if questionneur is None:  # pragma: no cover — l'appelant l'a vérifié
            return None, "aucun fil où poser la question"
        attente_s = self._bornes_question.attente_s
        cle = cle_acte(verbe, {"run": journal.run_id, "question": texte})
        demande = DemandeQuestion(
            question_id=identifiant_question(ETAPE_CADENCE, cle),
            question=texte,
            hypothese=hypothese,
            choix=choix,
            titre=titre,
            agent=ACTEUR_ORCHESTRATEUR,
            role=ROLE_ORCHESTRATEUR,
            run_id=journal.run_id,
            projet_id=projet_id,
            attente_s=attente_s,
            # Une question du run ne vaut que tant qu'il peut s'en servir : à la
            # borne, ou à sa fin, elle quitte le fil au lieu d'y laisser un geste
            # qui ne ferait plus rien.
            retirer_sans_reponse=True,
        )
        try:
            return await asyncio.wait_for(questionneur(demande), attente_s), ""
        except TimeoutError:
            return None, f"personne n'a répondu en {attente_s:g} s"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — un canal muet ne condamne rien de plus
            return None, f"la proposition n'a pas pu être posée dans le fil ({exc})"

    def _equipe(self, projet_id: str | None) -> tuple[Agent, ...]:
        """L'équipe sur laquelle découper — celle du projet, sinon celle du câblage.

        Une seule règle pour deux lecteurs (#1041) : la même que l'exécuteur
        applique tâche par tâche, appelée ici une fois par run parce qu'un plan se
        découpe une fois. Le repli est **le catalogue du câblage** et non une
        liste vide : `prompt_orchestrateur` retomberait sinon sur les gabarits du
        code en ignorant les agents personnalisés que ce moteur a chargés au
        démarrage (#72).

        ⚠ Le repli couvre donc aussi l'**équipe vide** que #1042 a rendue
        possible, et c'est voulu : découper sur zéro rôle ne donnerait pas un
        meilleur plan qu'en découper un sur les rôles du code. Ce n'est pas un
        contournement du « projet sans agent » — le routage, lui, distingue bien
        les deux (`LocalExecutor._equipe`), et toutes les tâches d'un projet sans
        équipe finissent « à assigner ». Ici on choisit selon quoi *découper*, là
        on choisit *qui prend*.
        """
        return (
            catalogue_du_projet(self._agents_store, projet_id, self._modele)
            or self._agents
        )

    async def _rattrape(
        self,
        task: Task,
        echec: TaskResult,
        dependances: Sequence[TaskResult],
        journal: RunJournal,
        executer: _Executer,
        ajustements: dict[str, str],
    ) -> TaskResult:
        """Rattrape une tâche en échec : jugée, retentée autrement, ou demandée (#1178).

        Rend le résultat qui **tient lieu** de celui de la tâche du plan — son
        identifiant, son titre, et l'usage de tout ce qui a été dépensé pour elle
        (tentatives et diagnostics) : c'est lui que l'aval reçoit, et lui que le
        rapport agrège.

        La boucle, dans cet ordre :

        1. le **budget** d'abord — un plafond atteint n'engage plus rien ;
        2. le **jugement** du Chef de projet : celui que l'exécuteur a rendu avant
           de renoncer à relancer (`JugeDesEchecs.retenu`), sinon un nouveau, sur
           toute l'histoire de la tâche ;
        3. son **geste** : rejouer ou retenter — la tentative part, et si elle
           aboutit les tâches aval sont ajustées puis repartent —, abandonner sur
           la réponse de l'utilisateur, ou **demander** ;
        4. une **question** dans le fil, quand le Chef de projet la pose, quand sa
           proposition est refusée ou illisible, ou quand les tentatives sont
           épuisées : la cause et les tentatives faites y sont écrites, et le run
           attend la réponse, sous la borne d'une question d'agent. Une réponse
           ouvre une nouvelle série de tentatives.

        Rien ici ne lève : ce qui n'aboutit pas rend l'échec, avec ce qui l'a
        arrêté, et l'aval se bloque comme avant (#43).

        ## Ce qui manque se propose avant de se juger (#1181)

        Un échec qui porte un **prérequis constaté** (`TaskResult.prerequis` : un
        serveur MCP injoignable, un rôle que personne ne couvre) ne passe pas
        d'abord par le diagnostic : ce qui lui manque est su, sans modèle, et il se
        **propose** dans le fil — la procédure de la bibliothèque MCP sur une carte
        à un geste, ou le rôle sur la carte d'équipe. La personne le donne, et la
        tâche **reprend telle quelle**, dans ce run ; un même manque rencontré à la
        reprise se repropose (« toujours suspendue »), jusqu'à
        `PolitiqueRattrapage.max_propositions`. Une réponse en mots n'est lue par
        aucun motif : elle part au Chef de projet, qui la juge. Et le Chef de
        projet peut **proposer** lui aussi (`GESTE_PROPOSER`) — c'est ainsi qu'un
        blocage que l'agent a signalé devient une chose à donner.

        Tant qu'une proposition attend, la carte de la tâche dit « Attente
        humaine » (l'exécuteur a suspendu son étape) ; si elle n'aboutit pas, la
        boucle **solde** cette attente en échec, au lieu de laisser à l'écran un
        geste que plus rien ne reprendrait.
        """
        juge, politique = self._juge, self._rattrapage
        if (
            juge is None
            or politique is None
            or echec.statut != STATUT_ECHEC
            or not echec.rattrapable
        ):
            return echec
        dossier = juge.ouvre(journal.run_id, task)
        dossier.tentatives.append(
            Tentative(
                taches=(task,),
                agent=echec.agent,
                role=echec.role,
                erreur=echec.erreur or "",
                blocages=echec.blocages,
            )
        )
        usage = echec.usage
        dernier = echec
        # La tâche que la dernière tentative a exécutée — celle qu'on reprend
        # telle quelle quand un prérequis est donné : la tâche du plan, ou sa
        # version reprise autrement (#1178), jamais l'une pour l'autre.
        derniere_tache = task
        # La carte de la tâche attend-elle un geste ? (#1181) — vrai tant que la
        # dernière exécution de cette tâche a été suspendue sur un prérequis.
        suspendue = self._executor.suspendue(echec)
        # Le résultat sur lequel un prérequis constaté a déjà été proposé : une
        # réponse en mots le laisse au Chef de projet, qui ne doit pas le voir
        # reproposé au tour suivant comme s'il était neuf.
        deja_propose: TaskResult | None = None
        essais = questions = tours = propositions = 0
        diagnostic = issue = ""
        budget_depense = "le budget du run est dépensé, aucune tentative de plus n'est engagée"
        try:
            while True:
                if juge.budget_epuise(journal):
                    issue = budget_depense
                    break
                a_proposer: PrerequisManquant | None = None
                verdict: Verdict | None = None
                question = motif = ""
                constate = dernier.prerequis
                est_constate = False
                if (
                    constate is not None
                    and dernier is not deja_propose
                    and propositions < politique.max_propositions
                    and self._peut_proposer(task, constate)
                ):
                    # Ce qui manque est constaté : il se propose, sans diagnostic.
                    a_proposer, deja_propose, est_constate = constate, dernier, True
                else:
                    retenu = juge.retenu(journal.run_id, task.id)
                    if essais < politique.max_tentatives:
                        verdict = retenu
                        if verdict is None:
                            verdict, cout = await juge.juge(dossier, journal)
                            usage = usage.fusion(cout)
                    elif isinstance(retenu, Rattrapage):
                        # Les tentatives sont épuisées : ce que le juge proposait ne
                        # part pas, mais ce qu'il a compris va dans la question.
                        diagnostic = retenu.diagnostic
                    # Pourquoi on en vient à demander — c'est ce que l'issue dira si
                    # la question ne peut pas partir, ou ne reçoit pas de réponse.
                    if essais >= politique.max_tentatives:
                        motif = f"{essais} tentative(s) sans succès"
                    elif isinstance(verdict, Refus):
                        motif = f"la proposition du Chef de projet est refusée ({verdict.raison})"
                    elif verdict is None:
                        motif = "le Chef de projet n'a pas pu juger l'échec"
                    else:
                        motif = "il faut une réponse de l'utilisateur"
                if isinstance(verdict, Rattrapage):
                    diagnostic = verdict.diagnostic
                    if verdict.execute:
                        essais += 1
                        tours += 1
                        resultat, executees, geste = await self._retente(
                            task, verdict, dependances, journal, executer, tours, dossier
                        )
                        usage = usage.fusion(resultat.usage)
                        if resultat.ok:
                            ajustements.update(verdict.ajustements)
                            return replace(resultat, usage=usage)
                        dossier.tentatives.append(
                            Tentative(
                                taches=executees,
                                agent=resultat.agent,
                                role=resultat.role,
                                erreur=resultat.erreur or "",
                                geste=geste,
                                diagnostic=verdict.diagnostic,
                                blocages=resultat.blocages,
                            )
                        )
                        dernier = resultat
                        if len(executees) == 1:
                            derniere_tache = executees[0]
                            suspendue = self._executor.suspendue(resultat)
                        continue
                    if verdict.geste == GESTE_ABANDONNER:
                        issue = (
                            "abandonnée sur la réponse de l'utilisateur — "
                            f"« {(dossier.reponse or '').strip()} »"
                        )
                        break
                    if verdict.prerequis is not None:
                        # Le Chef de projet nomme ce qui manque (#1181) — souvent ce
                        # que l'agent a signalé. Il se propose comme un constat ; à
                        # défaut de pouvoir le proposer, il devient la question.
                        nomme = self._prerequis_nomme(task, verdict.prerequis)
                        if propositions < politique.max_propositions and self._peut_proposer(
                            task, nomme
                        ):
                            a_proposer = nomme
                        else:
                            question = nomme.phrase()
                    else:
                        question = verdict.question
                if a_proposer is not None:
                    propositions += 1
                    if not suspendue:
                        # La carte dit l'attente d'un geste pendant la proposition,
                        # même quand l'exécuteur ne l'a pas suspendue lui-même : un
                        # prérequis nommé par le Chef de projet, un exécuteur
                        # distribué. Une tâche qui attend quelqu'un n'est pas morte.
                        self._consigne_la_carte(
                            task, dernier, STATUT_EN_ATTENTE_VALIDATION,
                            dernier.erreur or "", journal,
                        )
                        suspendue = True
                    proposition = await self._propose(
                        task, a_proposer, journal, propositions, constate=est_constate
                    )
                    if proposition.reprendre:
                        if proposition.recrute:
                            # Le rôle recruté prend la tâche : elle demande
                            # désormais son métier, et le routage le lui donne
                            # (`equipe_completee`) — sans quoi elle retournerait
                            # à l'agent qui a buté.
                            derniere_tache = replace(
                                derniere_tache,
                                competences_requises=tuple(
                                    dict.fromkeys(
                                        (
                                            *(a_proposer.couvre or a_proposer.competences),
                                            *derniere_tache.competences_requises,
                                        )
                                    )
                                ),
                            )
                        # Ce qui manquait est donné : la tâche reprend telle
                        # quelle — rien d'autre n'était à changer en elle.
                        resultat = await executer(derniere_tache, dependances)
                        usage = usage.fusion(resultat.usage)
                        suspendue = self._executor.suspendue(resultat)
                        if resultat.ok:
                            return replace(
                                resultat, task_id=task.id, titre=task.titre, usage=usage
                            )
                        dossier.tentatives.append(
                            Tentative(
                                taches=(derniere_tache,),
                                agent=resultat.agent,
                                role=resultat.role,
                                erreur=resultat.erreur or "",
                                geste=f"reprise après le prérequis « {a_proposer.objet} »",
                                blocages=resultat.blocages,
                            )
                        )
                        dernier = resultat
                        continue
                    if proposition.reponse is not None:
                        # Une réponse en mots : le Chef de projet la lit, elle fait
                        # autorité — comme la réponse à une question.
                        dossier.question, dossier.reponse = proposition.texte, proposition.reponse
                        questions += 1
                        essais = 0
                        continue
                    issue = proposition.issue
                    break
                if juge.budget_epuise(journal):
                    # Le diagnostic a pu dépenser le reste du budget : une réponse
                    # n'engagerait plus rien, et la question serait posée pour rien.
                    issue = budget_depense
                    break
                if questions >= politique.max_questions:
                    issue = f"{motif}, et plus aucune question ne peut être posée"
                    break
                if self._questionneur is None:
                    issue = (
                        f"{motif}, et personne n'est branché sur ce run pour le demander"
                    )
                    break
                texte = question_du_rattrapage(task, dossier, question, diagnostic)
                reponse = await self._demande(task, texte, journal)
                questions += 1
                if reponse is None:
                    issue = "la question posée dans le fil est restée sans réponse"
                    break
                dossier.question, dossier.reponse = texte, reponse
                essais = 0
        finally:
            juge.ferme(journal.run_id, task.id)
        consigne_issue(journal, task, dernier.erreur or "", issue)
        erreur = f"{dernier.erreur} — rattrapage : {issue}."
        if suspendue:
            # La carte attendait un geste que plus rien ne reprendra : elle le dit.
            self._consigne_la_carte(task, dernier, STATUT_ECHEC, erreur, journal)
        return replace(
            dernier,
            task_id=task.id,
            titre=task.titre,
            statut=STATUT_ECHEC,
            erreur=erreur,
            usage=usage,
        )

    def _consigne_la_carte(
        self,
        task: Task,
        dernier: TaskResult,
        statut: str,
        erreur: str,
        journal: RunJournal,
    ) -> None:
        """Pose `statut` sur la carte de `task`, sans rien compter (#1181).

        Deux usages, et ce sont les deux bouts d'une suspension : la mettre
        « en attente d'un humain » quand un prérequis se propose et que
        l'exécuteur ne l'a pas fait lui-même, et la **solder** en échec quand la
        proposition n'aboutit pas — une carte laissée en attente sur une tâche que
        plus rien ne reprendra mentirait. Une étape de la tâche, à **usage nul**
        comme celle d'un redécoupage abouti : l'usage de chaque tentative est déjà
        porté par sa propre étape, le redire compterait deux fois.
        """
        journal.consigne(
            etape=task.id,
            nom=task.titre,
            agent=dernier.agent,
            role=dernier.role,
            statut=statut,
            entree="",
            sortie="",
            erreur=erreur,
            usage=StepUsage(),
            ticket=task.ticket,
            projet_id=task.projet_id,
            description=task.description,
        )

    def _prerequis_nomme(self, task: Task, prerequis: PrerequisManquant) -> PrerequisManquant:
        """Le prérequis que le Chef de projet a nommé, rapporté à ce que Maestro sait faire (#1181).

        Un **rôle** se nomme par les compétences qu'il couvrirait ; c'est ici qu'il
        devient un poste à pourvoir, par la règle du routage (`role_manquant`, sur
        l'équipe du projet) : le gabarit qui couvre le plus de ce qui manque, et la
        raison du Chef de projet — c'est lui qui a lu le blocage. Si l'équipe couvre
        déjà ces compétences, ou si aucun gabarit n'y répond, le prérequis reste tel
        que nommé : il se proposera sur la carte d'une question, sans recrutement
        inventé. Les autres genres passent tels quels.
        """
        if prerequis.genre != GENRE_ROLE or prerequis.gabarit:
            return prerequis
        equipe = catalogue_du_projet(self._agents_store, task.projet_id, self._modele)
        if equipe is None:
            return prerequis
        manque = role_manquant(competences_non_couvertes(prerequis.competences, equipe))
        if manque is None or manque.gabarit is None:
            return prerequis
        return replace(prerequis_du_role(manque, task.titre), raison=prerequis.raison)

    def _peut_proposer(self, task: Task, prerequis: PrerequisManquant) -> bool:
        """Y a-t-il un fil où proposer ce prérequis ? (#1181)

        La carte d'une question le sait pour **tous** les genres — elle dit ce qui
        manque, comment le donner, et offre le geste qui reprend. Un rôle
        recrutable a en plus sa carte d'équipe, qui recrute en un geste — il lui
        faut un arbitre de renfort et un projet où le rôle naîtrait.
        """
        if self._questionneur is not None:
            return True
        return (
            prerequis.recrutable
            and self._arbitre_renfort is not None
            and task.projet_id is not None
        )

    async def _propose(
        self,
        task: Task,
        prerequis: PrerequisManquant,
        journal: RunJournal,
        rang: int,
        *,
        constate: bool,
    ) -> _Proposition:
        """Propose `prerequis` dans le fil et rend ce que la personne en a fait (#1181).

        Un rôle recrutable part sur la carte d'**équipe** (le canal de renfort de
        #1227, en cours de run cette fois) : son recrutement complète l'équipe et
        la tâche reprend sur elle. Tout le reste part sur la carte d'une
        **question** à un geste (`CHOIX_PREREQUIS_LEVE`), avec la procédure pour
        le donner. Chaque issue est consignée (`<tâche>:rattrapage`).

        `constate` dit d'où vient le prérequis — le routage ou l'exécuteur, ou le
        Chef de projet — et ne change qu'une chose : ce que devient un rôle
        **décliné** (cf. `_propose_un_role`).
        """
        if (
            prerequis.recrutable
            and self._arbitre_renfort is not None
            and task.projet_id is not None
        ):
            return await self._propose_un_role(task, prerequis, journal, constate=constate)
        texte = question_du_prerequis(task, prerequis, rang)
        reponse = await self._demande(
            task, texte, journal, choix=(CHOIX_PREREQUIS_LEVE,), verbe=VERBE_PREREQUIS
        )
        if reponse is None:
            issue = "la proposition posée dans le fil est restée sans réponse"
            consigne_proposition(journal, task, prerequis, STATUT_PREREQUIS_SANS_REPONSE, issue)
            return _Proposition(issue=issue)
        if reponse.strip() == CHOIX_PREREQUIS_LEVE:
            consigne_proposition(
                journal,
                task,
                prerequis,
                STATUT_PREREQUIS_LEVE,
                "l'utilisateur l'a donné — la tâche reprend, sans relancer le run",
            )
            return _Proposition(reprendre=True)
        consigne_proposition(
            journal,
            task,
            prerequis,
            STATUT_PREREQUIS_REPONDU,
            f"l'utilisateur a répondu « {reponse.strip()} » — le Chef de projet en juge",
        )
        return _Proposition(reponse=reponse, texte=texte)

    async def _propose_un_role(
        self,
        task: Task,
        prerequis: PrerequisManquant,
        journal: RunJournal,
        *,
        constate: bool,
    ) -> _Proposition:
        """Propose de recruter le rôle qui manque à `task`, par la carte d'équipe (#1181).

        La demande est celle de #1227 — le rôle, sa raison, la tâche qui l'attend —,
        marquée **en cours de run** (`DemandeRenfort.tache`) pour que le fil dise
        qu'une tâche est suspendue, et non qu'un plan attend. Un seul renfort en
        vol par run (`_verrous_renfort`) : la décision revient filtrée par run.

        Un **refus** n'a pas la même suite selon d'où vient le rôle. Constaté au
        routage (`constate`), la tâche n'avait personne : elle reprend au rôle le
        plus proche, la conduite de #1260 — la laisser « à assigner » serait pire.
        Nommé par le Chef de projet d'après un blocage, la tâche avait un agent, qui
        a buté : la reprendre à l'identique rebuterait, donc le refus lui revient,
        comme une réponse, et il juge. Un **silence** laisse la tâche en échec dans
        le second cas — il n'y a rien à reprendre sans réponse.
        """
        arbitre = self._arbitre_renfort
        if arbitre is None or task.projet_id is None:  # pragma: no cover — vérifié avant
            return _Proposition(issue="personne n'est branché sur ce run pour le proposer")
        objectif = self._juge.objectif(journal.run_id) if self._juge is not None else ""
        demande = DemandeRenfort(
            run_id=journal.run_id,
            projet_id=task.projet_id,
            objectif=objectif,
            manque=ManqueAuPlan(manque=prerequis.role_manquant(), taches=(task.titre,)),
            attente_s=self._bornes_renfort.attente_s,
            tache=task.titre,
        )
        verrou = self._verrous_renfort.setdefault(journal.run_id, asyncio.Lock())
        async with verrou:
            try:
                decision = await arbitre(demande)
            except asyncio.CancelledError:
                raise
            except Exception as echec:  # noqa: BLE001 — un canal muet ne condamne pas la tâche
                decision = DecisionRenfort(
                    approuve=False,
                    detail=f"la proposition de renfort n'a pas abouti : {echec}",
                    sans_reponse=True,
                )
        detail = decision.detail.strip()
        statut = (
            STATUT_PREREQUIS_LEVE
            if decision.approuve
            else STATUT_PREREQUIS_SANS_REPONSE
            if decision.sans_reponse
            else STATUT_PREREQUIS_DECLINE
        )
        if decision.approuve:
            # Le rôle recruté prend la tâche qui l'attendait — la promesse du fil.
            self._executor.equipe_completee(
                journal.run_id, prerequis.couvre or prerequis.competences
            )
            suite = "la tâche reprend avec l'équipe complétée"
        elif constate:
            # Personne n'est recruté : la tâche va au rôle le plus proche (#1260),
            # plutôt que de rester « à assigner » pour toujours.
            self._executor.continuer_avec_l_equipe(journal.run_id)
            suite = "la tâche reprend avec l'équipe actuelle, au rôle le plus proche"
        elif decision.sans_reponse:
            suite = "personne n'a répondu : la tâche reste en échec"
        else:
            suite = "le recrutement est décliné : le Chef de projet en juge"
        issue = f"{detail} — {suite}" if detail else suite
        consigne_proposition(journal, task, prerequis, statut, issue)
        if decision.approuve or constate:
            return _Proposition(reprendre=True, recrute=decision.approuve, issue=issue)
        if decision.sans_reponse:
            return _Proposition(
                issue=f"la proposition de recruter « {prerequis.objet} » est restée sans réponse"
            )
        # Le geste, dit comme un fait : c'est ce que le Chef de projet lit en
        # réponse — la personne a décliné, d'un clic, sans rien écrire.
        return _Proposition(
            reponse=(
                f"(décliné d'un geste) pas de recrutement du rôle « {prerequis.objet} »"
                + (f" — {detail}" if detail else "")
            ),
            texte=f"Recruter le rôle « {prerequis.objet} » pour la tâche « {task.titre} » ?",
        )

    async def _retente(
        self,
        task: Task,
        verdict: Rattrapage,
        dependances: Sequence[TaskResult],
        journal: RunJournal,
        executer: _Executer,
        tour: int,
        dossier: Dossier,
    ) -> tuple[TaskResult, tuple[Task, ...], str]:
        """Exécute la tentative que le verdict décide, et rend son issue.

        Trois formes, et c'est le verdict qui choisit : la tâche **rejouée** telle
        quelle (échec passager), la tâche **reprise** autrement — sous son propre
        identifiant, pour que sa carte repasse « en cours » —, ou la tâche
        **redécoupée** en plusieurs. Rend aussi ce qui a été exécuté et comment,
        pour la ligne d'histoire d'un nouvel échec.
        """
        if verdict.rejoue:
            dossier.en_cours = "rejouée à l'identique (jugée passagère)"
            if self._relance is not None:
                await asyncio.sleep(self._relance.attente_s(tour))
            return await executer(task, dependances), (task,), dossier.en_cours
        if len(verdict.taches) == 1:
            dossier.en_cours = "reprise autrement"
            reprise = tache_reprise(task, verdict.taches[0])
            return await executer(reprise, dependances), (reprise,), dossier.en_cours
        sous = taches_redecoupees(task, verdict.taches, tour)
        dossier.en_cours = f"redécoupée en {len(sous)} tâches"
        resultat = await self._redecoupe(task, sous, dependances, journal, executer)
        return resultat, sous, dossier.en_cours

    async def _redecoupe(
        self,
        task: Task,
        sous: Sequence[Task],
        dependances: Sequence[TaskResult],
        journal: RunJournal,
        executer: _Executer,
    ) -> TaskResult:
        """Exécute un redécoupage comme un petit plan ; rend le résultat de la tâche remplacée.

        Même régime que la boucle principale, en plus court : chaque tâche attend
        les siennes, un échec bloque ce qui en dépend (#43), et ce qui ne s'attend
        pas s'exécute de front. Chacune reçoit ce que la tâche d'origine avait
        reçu de ses dépendances du plan, plus les livrables des tâches du
        redécoupage dont elle dépend.

        Tout abouti : les livrables sont **rassemblés** sous l'identifiant de la
        tâche du plan, et sa ligne au journal la dit terminée — à usage nul,
        chaque tâche du redécoupage ayant consigné le sien. Sinon : un échec qui
        cite celles qui ont échoué.
        """
        issues: dict[str, asyncio.Task[TaskResult]] = {}

        async def une(sous_tache: Task) -> TaskResult:
            propres = [await issues[dep] for dep in sous_tache.dependances]
            insatisfaites = [dep for dep in propres if not dep.ok]
            if insatisfaites:
                return _consigne_blocage(sous_tache, insatisfaites, journal)
            resultat = await executer(sous_tache, [*dependances, *propres])
            # Une tâche du redécoupage ne se propose pas à part (#1181) : c'est la
            # tâche du plan que la boucle rattrape, et son échec dit ce qui manquait.
            self._solde_si_suspendue(sous_tache, resultat, journal)
            return resultat

        ordre = topological_order(sous)
        try:
            async with asyncio.TaskGroup() as tg:
                for sous_tache in ordre:
                    issues[sous_tache.id] = tg.create_task(une(sous_tache))
        finally:
            if self._juge is not None:
                # Un verdict rendu sur une tâche du redécoupage, à l'étage de sa
                # tentative, ne sert à personne ensuite : c'est la tâche du plan
                # que la boucle rejuge. On ne le garde pas.
                for sous_tache in ordre:
                    self._juge.ferme(journal.run_id, sous_tache.id)
        resultats = [issues[sous_tache.id].result() for sous_tache in ordre]
        usage = StepUsage()
        for resultat in resultats:
            usage = usage.fusion(resultat.usage)
        echecs = [resultat for resultat in resultats if not resultat.ok]
        derniere = resultats[-1]
        if echecs:
            return TaskResult(
                task_id=task.id,
                titre=task.titre,
                agent=echecs[0].agent,
                role=echecs[0].role,
                competences_requises=task.competences_requises,
                score=echecs[0].score,
                statut=STATUT_ECHEC,
                sortie="",
                erreur="; ".join(
                    f"{r.task_id} ({r.statut}) : {r.erreur}" for r in echecs
                ),
                usage=usage,
            )
        sortie = "\n\n".join(f"— [{r.titre}]\n{r.sortie}" for r in resultats)
        journal.consigne(
            etape=task.id,
            nom=task.titre,
            agent=derniere.agent,
            role=derniere.role,
            statut=STATUT_TERMINEE,
            entree=task.description,
            sortie=sortie,
            usage=StepUsage(),
            ticket=task.ticket,
            projet_id=task.projet_id,
            description=task.description,
        )
        return TaskResult(
            task_id=task.id,
            titre=task.titre,
            agent=derniere.agent,
            role=derniere.role,
            competences_requises=task.competences_requises,
            score=derniere.score,
            statut=STATUT_TERMINEE,
            sortie=sortie,
            fichiers=tuple(f for r in resultats for f in r.fichiers),
            usage=usage,
        )

    async def _demande(
        self,
        task: Task,
        texte: str,
        journal: RunJournal,
        *,
        choix: tuple[str, ...] = (),
        verbe: str = VERBE_RATTRAPAGE,
    ) -> str | None:
        """Pose dans le fil la question d'un échec, et attend la réponse — bornée (#1178).

        `choix` et `verbe` (#1181) servent la **proposition** d'un prérequis, qui
        passe par la même carte : un geste déclaré (« c'est fait ») que la carte
        rend en bouton, et un espace de clés à elle pour que sa réponse ne soit
        jamais celle d'une question.

        Le canal est celui d'une question d'agent (#1023, `ArbitreQuestion`) : la
        carte existe déjà au pied du fil de l'orchestration, avec son champ de
        réponse et ce qui se passera sans réponse. Seul change qui demande — le
        Chef de projet, pas un agent — et ce qui se passe à la borne : la tâche
        reste en échec, et l'aval ne part pas. La borne est **la même** qu'une
        question d'agent (`BornesArbitrage.attente_s`) : c'est le même temps
        humain, et il n'a qu'un réglage.

        Rend la réponse, ou None — personne n'a répondu, ou la question n'a pas
        pu partir. Les deux issues sont consignées, comme une question d'agent.
        """
        questionneur = self._questionneur
        if questionneur is None:  # pragma: no cover — l'appelant l'a vérifié
            return None
        attente_s = self._bornes_question.attente_s
        hypothese = hypothese_du_rattrapage(
            self._juge.aval(journal.run_id, task.id) if self._juge is not None else ()
        )
        cle = cle_acte(verbe, {"run": journal.run_id, "question": texte})
        demande = DemandeQuestion(
            question_id=identifiant_question(task.id, cle),
            question=texte,
            hypothese=hypothese,
            choix=choix,
            tache_id=task.id,
            titre=task.titre,
            agent=ACTEUR_ORCHESTRATEUR,
            role=ROLE_ORCHESTRATEUR,
            run_id=journal.run_id,
            projet_id=task.projet_id,
            attente_s=attente_s,
        )
        reponse: str | None = None
        try:
            reponse = await asyncio.wait_for(questionneur(demande), attente_s)
            motif = f"réponse reçue à : {texte}"
        except TimeoutError:
            motif = f"aucune réponse après {attente_s:g} s à : {texte}"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — un canal muet ne condamne rien de plus
            motif = f"la question n'a pas pu être posée ({exc}) : {texte}"
        consigne_question(journal, demande, task, reponse, motif)
        return reponse

    async def etape_brief(
        self,
        objectif: str,
        journal: RunJournal,
        *,
        contexte_sources: str = "",
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
                    contexte_sources,
                    clarifications,
                    dernier_tour=dernier_tour,
                )
            except Exception as exc:
                journal.consigne(
                    etape=ETAPE_BRIEF,
                    nom=f"Brief de l'objectif{rang}",
                    agent=ACTEUR_ORCHESTRATEUR,
                    role=ROLE_ORCHESTRATEUR,
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
            agent=ACTEUR_ORCHESTRATEUR,
            role=ROLE_ORCHESTRATEUR,
            statut=STATUT_TERMINEE,
            entree=objectif,
            sortie=(
                f"{len(brief.criteres_acceptation)} critère(s) d'acceptation, "
                f"{len(brief.questions)} question(s)"
            ),
            usage=usage,
            projet_id=projet_id,
            # Le brief lui-même (#1174) : c'est par cette ligne qu'un run en mode
            # `auto`, qui ne demande ni ne reçoit de décision, fait connaître son
            # brief à la Control Tower — donc devient relançable dessus.
            brief=brief.to_dict(),
        )
        return usage, brief


@dataclass(frozen=True)
class _Proposition:
    """Ce qu'une proposition de prérequis a donné, vu de la boucle (#1181).

    Trois issues : `reprendre` — la personne a donné ce qui manquait (ou décliné
    un rôle constaté au routage, la tâche allant alors au plus proche), la tâche
    repart — `recrute` quand c'est un rôle qui vient de naître ; `reponse` —
    elle a répondu **en mots**, et c'est le Chef de projet qui les lit (`texte`
    est la carte à laquelle elle répondait) ; ni l'un ni l'autre — personne n'a
    répondu, et `issue` dit pourquoi la tâche reste en échec.
    """

    reprendre: bool = False
    recrute: bool = False
    reponse: str | None = None
    texte: str = ""
    issue: str = ""


@dataclass
class _Versionnement:
    """La proposition de versionner le projet, en vol pendant le run (#1298).

    `cadence` est la cause telle qu'elle a été dite en dernier — chaque issue la
    fait avancer —, `tache` l'attente de la réponse, qui court à côté des tâches,
    et `en_ecriture` dit que l'accord est donné et que la mise sous Git a commencé :
    la fin du run l'attend alors au lieu de la retirer.
    """

    cadence: Cadence
    tache: asyncio.Task[None] | None = None
    en_ecriture: bool = False


def _allowlist_mcp_du_poste() -> RegistreMcp:
    """L'allowlist MCP du poste : le seed curé et les entrées admises (#678, #1181).

    Ce que `OrchestrationEngine.default` donne à l'exécuteur pour lire la
    procédure d'accès d'un serveur injoignable. L'allowlist et non la
    bibliothèque fédérée : un serveur monté sur un agent en sort forcément, et la
    fédérer traduirait le miroir amont entier pour une seule fiche. Relue à chaque
    constat — une admission faite pendant le run y est. Import local : le moteur
    n'a pas à charger la porte d'admission pour un run où rien ne manque.
    """
    from maestro.agents.mcp_federation import lire_admissions
    from maestro.agents.mcp_registry import PROVENANCE, SEED

    admissions, _ = lire_admissions()
    return RegistreMcp(SEED, PROVENANCE, admissions=admissions)


def _dependants_directs(tasks: Sequence[Task]) -> dict[str, list[str]]:
    """Inverse le graphe de dépendances : pour chaque tâche, qui dépend d'elle.

    C'est le carnet d'adresses du handoff (#44) : une tâche sans dépendant n'a
    personne à qui passer la main (aucune annonce), une tâche à dépendants
    annonce son issue pour les débloquer. Les ids sont dans l'ordre du plan.

    Le pendant côté lecture est `maestro.plan_run.dependants_directs` (#490),
    qui rend la même table sur la forme **transportée** du plan — celle que la
    Control Tower reçoit. Deux fonctions plutôt qu'une parce qu'elles ne
    travaillent pas sur le même objet (des `Task` ici, des `NoeudPlan` là-bas) et
    ne franchissent pas la même frontière : ce module est le moteur, l'autre est
    une feuille que le journal, le bus et la projection se partagent.
    """
    dependants: dict[str, list[str]] = {task.id: [] for task in tasks}
    for task in tasks:
        for dep in task.dependances:
            dependants[dep].append(task.id)
    return dependants


#: Ce que l'agent d'une tâche reprise au plafond lit en plus de sa description
#: (#1182). Écrit au conditionnel du projet et non comme une promesse : une tâche
#: qui travaillait dans un projet retrouve son travail (sa branche, ou la racine
#: d'un projet non versionné) ; un livrable purement textuel, lui, n'avait rien
#: d'écrit à conserver. L'agent regarde où il en était — c'est à lui d'en juger.
NOTE_REPRISE_AU_PLAFOND = (
    "Reprise : cette tâche avait déjà commencé. Elle a été mise de côté quand le "
    "run a atteint son plafond de dépense, puis reprise sur la décision de "
    "l'utilisateur. Si tu travaillais dans le projet, ce que tu avais produit y "
    "est resté : regarde où tu en étais et continue, plutôt que de tout refaire."
)


def _reprise_au_plafond(task: Task) -> Task:
    """La tâche mise de côté, telle qu'elle repart — sa description dit qu'elle reprend (#1182)."""
    if NOTE_REPRISE_AU_PLAFOND in task.description:
        return task
    return replace(task, description=f"{task.description}\n\n{NOTE_REPRISE_AU_PLAFOND}")


class _AuPlafond:
    """La décision d'un run au plafond de dépense (#1182) — une question par franchissement.

    Tenue le temps d'un `run`. Les tâches qui atteignent le plafond s'y présentent
    une à une — la première en vol à sa mesure fautive, les autres à leur mesure
    suivante ou à l'entrée de l'exécuteur —, et toutes attendent **la même**
    réponse : la première pose la question, les suivantes la rejoignent. C'est ce
    qui fait qu'une personne répond une fois pour son run, et non une fois par
    tâche en vol.

    Ce qu'elle retient ensuite vaut pour la suite du run : les tâches **écartées**
    ne partent plus, un **arrêt** solde tout ce qui se présente sans redemander.
    Un plafond **relevé** ne se retient pas ici — il vit dans `Plafonds`, là où les
    tâches le relisent — et une tâche qui se présente après coup reprend sans
    question tant qu'il n'est pas atteint à nouveau.
    """

    def __init__(
        self,
        arbitre: ArbitrePlafond,
        plafonds: Plafonds,
        journal: RunJournal,
        objectif: str,
        projet_id: str | None,
        restantes: Callable[[], Sequence[Task]],
    ) -> None:
        self._arbitre = arbitre
        self._plafonds = plafonds
        self._journal = journal
        self._objectif = objectif
        self._projet_id = projet_id
        self._restantes = restantes
        self._question: asyncio.Task[DecisionPlafond | None] | None = None
        self._ecartees: set[str] = set()
        # Les tâches mises de côté qui attendent la réponse, par identifiant :
        # celles-là avaient commencé, et la demande le dit.
        self._mises_de_cote: dict[str, str] = {}
        self._derniere: DecisionPlafond | None = None
        self._arret: DecisionPlafond | None = None
        self._arrete = False
        self._motif_arret = ""

    def ecartee(self, tache_id: str) -> bool:
        """La personne a-t-elle écarté cette tâche au plafond ?"""
        return tache_id in self._ecartees

    async def decision(
        self, task: Task, result: TaskResult, *, commencee: bool
    ) -> DecisionPlafond | None:
        """La décision qui vaut pour `task`, arrêtée au plafond — posée si personne ne l'a demandée.

        Rend None quand le run s'arrête sans décision (la question n'a pas pu
        partir). Rend la dernière décision de reprise, sans rien demander, quand
        le plafond a été relevé entre la mesure de la tâche et son arrivée ici.
        """
        if self._arrete:
            return self._arret
        if commencee:
            self._mises_de_cote[task.id] = task.titre
        try:
            while True:
                if self._arrete:
                    return self._arret
                if self._question is None:
                    if self._derniere is not None and not self._plafonds.epuise(self._journal):
                        return self._derniere
                    self._question = asyncio.create_task(self._demander(result.erreur or ""))
                question = self._question
                # `shield` : une tâche annulée qui attendait n'emporte pas la
                # question des autres. Le run annulé, lui, la ferme (`fermer`).
                decision = await asyncio.shield(question)
                if decision is None or not decision.reprend or not self._plafonds.epuise(
                    self._journal
                ):
                    return decision
                # Relevé, mais pas assez : la dépense a continué de courir pendant
                # l'attente (une tâche parallèle mesurée entre-temps). On redemande
                # plutôt que de repartir pour retomber aussitôt.
                self._derniere = None
        finally:
            self._mises_de_cote.pop(task.id, None)

    def solde(
        self, task: Task, result: TaskResult | None, depense: StepUsage
    ) -> TaskResult:
        """Solde `task` sur la décision au plafond — écartée, ou arrêtée avec le run.

        Son étape finale est consignée ici, **à usage nul** : ce qu'elle a
        dépensé est déjà au grand livre par sa ligne `<tâche>:plafond`. Le
        résultat rendu porte, lui, cette dépense — c'est ce que le rapport agrège.
        Un échec qui n'est pas rattrapable : c'est une décision.
        """
        raison = (result.erreur if result is not None else "") or ""
        if self.ecartee(task.id):
            cause = "tâche écartée au plafond de dépense, sur décision de l'utilisateur"
        elif self._arret is not None:
            cause = (
                f"{raison} — le run s'arrête au plafond de dépense, sur décision de "
                "l'utilisateur ; ce que la tâche avait fait reste sur sa branche"
            )
        else:
            cause = (
                f"{raison} — la décision au plafond n'a pas pu être demandée "
                f"({self._motif_arret or 'personne pour la rendre'}) : le run s'arrête"
            )
        agent = result.agent if result is not None else "—"
        role = result.role if result is not None else "non exécutée"
        self._journal.consigne(
            etape=task.id,
            nom=task.titre,
            agent=agent,
            role=role,
            statut=STATUT_ECHEC,
            entree=task.description,
            sortie="",
            erreur=cause,
            usage=StepUsage(),
            ticket=task.ticket,
            projet_id=task.projet_id,
            description=task.description,
        )
        return TaskResult(
            task_id=task.id,
            titre=task.titre,
            agent=agent,
            role=role,
            competences_requises=task.competences_requises,
            score=result.score if result is not None else 0,
            statut=STATUT_ECHEC,
            sortie="",
            erreur=cause,
            usage=depense,
            rattrapable=False,
            au_plafond=True,
        )

    def fermer(self) -> None:
        """Abandonne une question encore en vol — le run s'achève ou s'annule."""
        if self._question is not None and not self._question.done():
            self._question.cancel()

    async def _demander(self, raison: str) -> DecisionPlafond | None:
        """Pose la question, attend la réponse, l'applique et la consigne — une fois pour tous."""
        try:
            await asyncio.sleep(0)  # que les tâches arrivées ensemble s'inscrivent
            demande = self._demande(raison)
            self._consigne_question(demande)
            decision: DecisionPlafond | None = None
            motif = ""
            try:
                decision = await self._arbitre(demande)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — un canal muet n'invente pas de réponse
                motif = str(exc) or type(exc).__name__
            self._applique(decision, demande, motif)
            return decision
        finally:
            self._question = None

    def _demande(self, raison: str) -> DemandePlafond:
        """Les faits de la question : dépense, plafonds en vigueur, ce qui reste à faire."""
        total = RunCost.depuis_journal(self._journal).total
        cout, jetons = self._plafonds.en_vigueur(self._journal.run_id)
        restantes = tuple(
            TacheRestante(
                tache_id=tache.id,
                titre=tache.titre,
                interrompue=tache.id in self._mises_de_cote,
            )
            for tache in self._restantes()
            if tache.id not in self._ecartees
        )
        return DemandePlafond(
            run_id=self._journal.run_id,
            projet_id=self._projet_id,
            objectif=self._objectif,
            depense_usd=total.cout_usd,
            depense_tokens=total.tokens_total,
            plafond_cout_usd=cout,
            plafond_tokens=jetons,
            raison=raison,
            restantes=restantes,
        )

    def _applique(
        self, decision: DecisionPlafond | None, demande: DemandePlafond, motif: str
    ) -> None:
        """Applique la réponse : plafond relevé, tâches écartées, ou run arrêté — et le dit."""
        connues = {tache.tache_id for tache in demande.restantes}
        if decision is None:
            self._arrete = True
            self._motif_arret = motif
            statut = STATUT_PLAFOND_SANS_DECISION
            sortie = (
                f"la décision au plafond n'a pas pu être demandée ({motif}) : le run "
                "s'arrête sur ce qui est fait"
            )
        elif not decision.reprend:
            self._arrete = True
            self._arret = decision
            statut = STATUT_PLAFOND_ARRETE
            sortie = (
                "l'utilisateur arrête le run au plafond : il se solde sur ce qui est "
                f"fait, {len(demande.restantes)} tâche(s) non faite(s)"
            )
        else:
            self._plafonds.relever(
                self._journal.run_id,
                cout_usd=decision.plafond_cout_usd,
                tokens=decision.plafond_tokens,
            )
            # Une tâche que la question ne nommait pas ne s'écarte pas : la
            # personne n'a décidé que de ce qu'on lui a montré.
            self._ecartees.update(t for t in decision.ecartees if t in connues)
            self._derniere = decision
            statut = STATUT_PLAFOND_REDUIT if decision.ecartees else STATUT_PLAFOND_RELEVE
            sortie = _phrase_de_reprise(decision, demande, self._plafonds.en_vigueur(
                self._journal.run_id
            ))
        detail = decision.detail.strip() if decision is not None else ""
        self._journal.consigne(
            etape=ETAPE_PLAFOND,
            nom="Décision au plafond de dépense",
            agent=ACTEUR_ORCHESTRATEUR,
            role=ROLE_ORCHESTRATEUR,
            statut=statut,
            entree="",
            sortie=f"{sortie} — {detail}" if detail else sortie,
            usage=StepUsage(),
            projet_id=self._projet_id,
        )

    def _consigne_question(self, demande: DemandePlafond) -> None:
        """Écrit au journal que le run attend une décision au plafond, et sur quels faits."""
        titres = ", ".join(
            f"« {t.titre} »" + (" (mise de côté)" if t.interrompue else "")
            for t in demande.restantes
        )
        self._journal.consigne(
            etape=ETAPE_PLAFOND,
            nom="Plafond de dépense atteint",
            agent=ACTEUR_ORCHESTRATEUR,
            role=ROLE_ORCHESTRATEUR,
            statut=STATUT_PLAFOND_ATTEINT,
            entree=demande.raison,
            sortie=(
                f"budget du run atteint — {_depense_en_clair(demande)} ; "
                f"reste {len(demande.restantes)} tâche(s)"
                + (f" : {titres}" if titres else "")
                + " — le run attend la décision de l'utilisateur : relever, réduire ou arrêter"
            ),
            usage=StepUsage(),
            projet_id=self._projet_id,
        )


def _depense_en_clair(demande: DemandePlafond) -> str:
    """Ce qui est dépensé face au plafond qui tient — en dollars s'il y en a, en tokens sinon."""
    morceaux: list[str] = []
    if demande.plafond_cout_usd is not None and demande.depense_usd is not None:
        morceaux.append(f"{demande.depense_usd:.2f} $ sur {demande.plafond_cout_usd:.2f} $")
    if demande.plafond_tokens is not None:
        morceaux.append(f"{demande.depense_tokens} tokens sur {demande.plafond_tokens}")
    return " et ".join(morceaux) or f"{demande.depense_tokens} tokens dépensés"


def _phrase_de_reprise(
    decision: DecisionPlafond,
    demande: DemandePlafond,
    en_vigueur: tuple[float | None, int | None],
) -> str:
    """Ce que la reprise dit au journal : le nouveau plafond, et ce qui ne repart pas."""
    cout, jetons = en_vigueur
    morceaux: list[str] = []
    if decision.plafond_cout_usd is not None and cout is not None:
        morceaux.append(f"{cout:.2f} $")
    if decision.plafond_tokens is not None and jetons is not None:
        morceaux.append(f"{jetons} tokens")
    plafond = " et ".join(morceaux)
    phrase = f"plafond relevé à {plafond} — le run reprend"
    titres = {t.tache_id: t.titre for t in demande.restantes}
    ecartees = [titres[i] for i in decision.ecartees if i in titres]
    if ecartees:
        phrase += " sans " + ", ".join(f"« {titre} »" for titre in ecartees)
    return phrase


#: Le critère que porte le constat d'un renvoi de QA (#1177) : c'est ce que la
#: QA a jugé, et c'est ce que la tâche productrice doit tenir à sa reprise.
CRITERE_QA = "la QA juge ce livrable conforme"


def _retour_qa(juge: Task, renvoi: Renvoi) -> str:
    """Ce qui suit la description d'une tâche renvoyée par la QA — les défauts, et la consigne."""
    return (
        "\n\n## Renvoyé par la QA\n\n"
        f"La tâche « {juge.titre} » a jugé ton livrable NON CONFORME "
        f"({renvoi.defauts} défaut(s) bloquant(s)). Ses constats, preuves à l'appui :\n\n"
        f"{renvoi.motif}\n\n"
        "Corrige ton livrable pour lever ces défauts. La QA évalue, elle ne réécrit "
        "pas ton travail : c'est à toi de le reprendre, puis elle le rejugera."
    )


def _verdict_de_renvoi(juge: Task, renvoi: Renvoi) -> VerdictVerification:
    """Le renvoi, sous la forme d'un verdict de vérification — ce que le détail de la tâche lit."""
    return VerdictVerification(
        constats=(
            Constat(
                critere=CRITERE_QA,
                etat=CONSTAT_NON_TENU,
                preuve=(
                    f"« {juge.titre} » : {renvoi.defauts} défaut(s) bloquant(s) — "
                    f"{renvoi.motif}"
                ),
            ),
        )
    )


def _consigne_renvoi(
    producteur: Task, dernier: TaskResult, juge: Task, renvoi: Renvoi, journal: RunJournal
) -> None:
    """Consigne sur la tâche productrice qu'une QA renvoie son livrable (#1177).

    Une étape `:verification` comme les autres : c'est une vérification qui ne
    tient pas, rendue par la QA au lieu du vérificateur. Le fil la dit, et le
    détail de la tâche la montre jusqu'à la vérification de sa reprise.
    """
    verdict = _verdict_de_renvoi(juge, renvoi)
    journal.consigne(
        etape=f"{producteur.id}{SUFFIXE_ETAPE_VERIFICATION}",
        nom=f"Vérification — {producteur.titre}",
        agent=dernier.agent,
        role=dernier.role,
        statut=verdict.statut,
        entree=f"verdict de « {juge.titre} »",
        sortie=f"non conforme selon « {juge.titre} » — renvoyée à {dernier.role}",
        description=verdict.preuves(),
        usage=StepUsage(),
        projet_id=producteur.projet_id,
        verification={**verdict.to_dict(), "renvoi": juge.id},
    )


def _consigne_non_conforme(
    producteur: Task, dernier: TaskResult, juge: Task, renvoi: Renvoi, journal: RunJournal
) -> TaskResult:
    """L'échec motivé d'une tâche que la QA juge encore non conforme, sans progrès (#1177).

    La tâche productrice s'était soldée verte sur ses propres critères ; la QA en
    dit autrement, et la dernière reprise n'a levé aucun défaut de plus. Son issue
    est donc **reconsignée en échec** — le dernier mot au journal fait foi pour la
    carte comme pour le grand livre — avec la revue en motif. Usage nul : ce qui
    a été dépensé est déjà porté par ses exécutions, et le recompter ici le
    compterait deux fois.
    """
    verdict = _verdict_de_renvoi(juge, renvoi)
    erreur = (
        f"non conforme selon « {juge.titre} » — {renvoi.defauts} défaut(s) bloquant(s), "
        "et la dernière reprise n'en a levé aucun de plus.\n"
        f"{verdict.preuves()}"
    )
    journal.consigne(
        etape=f"{producteur.id}{SUFFIXE_ETAPE_VERIFICATION}",
        nom=f"Vérification — {producteur.titre}",
        agent=dernier.agent,
        role=dernier.role,
        statut=verdict.statut,
        entree=f"verdict de « {juge.titre} »",
        sortie=f"non conforme selon « {juge.titre} » — aucun défaut levé de plus",
        description=verdict.preuves(),
        usage=StepUsage(),
        projet_id=producteur.projet_id,
        verification={**verdict.to_dict(), "renvoi": juge.id},
    )
    echec = replace(dernier, statut=STATUT_ECHEC, sortie="", erreur=erreur, renvois=())
    journal.consigne(
        etape=producteur.id,
        nom=producteur.titre,
        agent=echec.agent,
        role=echec.role,
        statut=echec.statut,
        entree=producteur.description,
        sortie="",
        erreur=erreur,
        usage=StepUsage(),
        ticket=producteur.ticket,
        projet_id=producteur.projet_id,
        description=producteur.description,
    )
    return echec


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
