"""Agrégations coûts & analytics de la Control Tower (ticket #87).

La comptabilité par tâche existe depuis la Phase 1 (#55–#58) : le grand livre
d'un run (`RunCost`) et son endpoint (`GET /api/executions/{run_id}/cout`).
Cette brique construit la vue **transverse** au-dessus des mêmes événements —
la matière du tableau de bord coûts & analytics (`GET /api/analytics/couts`) :

- le coût agrégé **par tâche** (toutes exécutions confondues), **par agent**
  (planification de l'orchestrateur comprise) et **par exécution** ;
- la **série temporelle** du coût (seaux par minute, heure ou jour) pour
  visualiser l'évolution de la dépense, sur une période sélectionnable
  (`depuis`).

Aucune nouvelle collecte : tout est recalculé des exécutions déjà projetées
(`ControlTowerState.executions`), avec la même convention d'attribution que le
grand livre du run (`EtatExecution.cout`, #57) — seuls les événements de
statut de tâche, d'activité d'agent et de message inter-agents portent de
l'usage, chacun compté exactement une fois par vue. Un événement qui ne
rapporte qu'un `cout_usd` scalaire (producteur minimaliste) est compté pour ce
seul coût ; un coût jamais rapporté reste « inconnu » (None, jamais confondu
avec un coût nul).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from maestro.controltower.events import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_TACHE_STATUT,
    Event,
)
from maestro.controltower.portee import PORTEE_TOUS, PorteeProjet
from maestro.references import ReferenceTicket, ticket_en_dict
from maestro.telemetry.usage import StepUsage

if TYPE_CHECKING:  # import différé : seul le typage en a besoin (pas de cycle)
    from maestro.controltower.state import EtatExecution

#: Granularités de la série temporelle — le paramètre `pas` de l'endpoint.
PAS_MINUTE = "minute"
PAS_HEURE = "heure"
PAS_JOUR = "jour"
PAS_VALIDES = (PAS_MINUTE, PAS_HEURE, PAS_JOUR)

#: Types d'événements porteurs d'usage — la même liste que le grand livre du
#: run (`EtatExecution.cout`, #57) : compter un autre type créerait un écart
#: entre la vue analytique et la comptabilité par exécution.
_TYPES_COMPTABLES = frozenset(
    {EVENEMENT_TACHE_STATUT, EVENEMENT_AGENT_ACTIVITE, EVENEMENT_MESSAGE_INTER_AGENTS}
)

#: Valeurs d'`Event.agent` qui ne désignent pas un acteur réel (tâche bloquée
#: jamais exécutée : « — », routage sans élu…) : pas de ligne « par agent ».
_AGENTS_NON_EXECUTANTS = frozenset({"", "—"})


@dataclass(frozen=True)
class CoutAgent:
    """La ligne « par agent » : l'usage cumulé d'un acteur, toutes exécutions confondues.

    L'orchestrateur y figure comme les exécutants (sa planification est une
    dépense au même titre) ; `taches` compte les tâches distinctes auxquelles
    son usage a été attribué — 0 pour un usage hors tâche (planification).
    """

    agent: str
    role: str = ""
    taches: int = 0
    usage: StepUsage = StepUsage()

    def to_dict(self) -> dict[str, Any]:
        """Réémet la ligne en dict JSON-sérialisable (la forme de l'API)."""
        return {
            "agent": self.agent,
            "role": self.role,
            "taches": self.taches,
            "usage": self.usage.to_dict(),
        }


@dataclass(frozen=True)
class CoutTacheAgregee:
    """La ligne « par tâche » : l'usage cumulé d'une tâche, toutes exécutions confondues.

    Même identité que l'entrée du grand livre (`TaskCost`, #55) — un événement
    `tache.statut` fait foi pour `nom`/`agent`/`role`/`statut` (le dernier vu
    l'emporte : c'est l'état courant) — complétée du nombre d'exécutions où la
    tâche est apparue (`executions` > 1 : re-tentatives, la dépense se cumule).
    """

    tache_id: str
    nom: str = ""
    agent: str = ""
    role: str = ""
    statut: str = ""
    executions: int = 0
    usage: StepUsage = StepUsage()
    ticket: ReferenceTicket | None = None
    projet_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Réémet la ligne en dict JSON-sérialisable (la forme de l'API)."""
        return {
            "tache_id": self.tache_id,
            "nom": self.nom,
            "agent": self.agent,
            "role": self.role,
            "statut": self.statut,
            "executions": self.executions,
            "usage": self.usage.to_dict(),
            "ticket": ticket_en_dict(self.ticket),
            "projet_id": self.projet_id,
        }


@dataclass(frozen=True)
class CoutExecutionResume:
    """La ligne « par exécution » : le run résumé — bornes, tâches, usage cumulé.

    `debut`/`fin` sont les horodatages extrêmes des événements retenus (vides
    si aucun n'est daté) ; `nb_taches` compte les tâches distinctes du run.
    Le détail par tâche du run reste du côté du grand livre (#57).
    """

    run_id: str
    nb_taches: int = 0
    debut: str = ""
    fin: str = ""
    usage: StepUsage = StepUsage()
    projet_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Réémet la ligne en dict JSON-sérialisable (la forme de l'API)."""
        return {
            "run_id": self.run_id,
            "nb_taches": self.nb_taches,
            "debut": self.debut,
            "fin": self.fin,
            "usage": self.usage.to_dict(),
            "projet_id": self.projet_id,
        }


@dataclass(frozen=True)
class PointCout:
    """Un seau de la série temporelle : l'usage cumulé sur `periode` (début du seau)."""

    periode: str
    usage: StepUsage = StepUsage()

    def to_dict(self) -> dict[str, Any]:
        """Réémet le point en dict JSON-sérialisable (la forme de l'API)."""
        return {"periode": self.periode, "usage": self.usage.to_dict()}


@dataclass(frozen=True)
class AnalyticsCouts:
    """La vue coûts & analytics complète — la réponse de `GET /api/analytics/couts`.

    `depuis` (ISO, ou None : tout l'historique projeté) et `pas` rappellent la
    fenêtre et la granularité demandées ; `total` retombe sur la somme des
    grands livres des runs de la fenêtre.

    `projet` (#222) rappelle l'identifiant demandé — None dès que la vue ne
    porte pas sur un projet précis. `portee` (#277) lève l'ambiguïté que ce
    `None` laissait : elle dit **laquelle** des trois lectures a été servie —
    `tous`, `aucun`, ou l'identifiant. Un total ne se lit pas sans savoir de quoi
    il est le total ; c'est le pendant, dans la réponse, du paramètre obligatoire
    de la requête.
    """

    depuis: str | None = None
    pas: str = PAS_HEURE
    projet: str | None = None
    portee: str = PORTEE_TOUS
    total: StepUsage = StepUsage()
    executions: tuple[CoutExecutionResume, ...] = ()
    agents: tuple[CoutAgent, ...] = ()
    taches: tuple[CoutTacheAgregee, ...] = ()
    serie: tuple[PointCout, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Réémet la vue en dict JSON-sérialisable (la forme de l'API)."""
        return {
            "depuis": self.depuis,
            "pas": self.pas,
            "projet": self.projet,
            "portee": self.portee,
            "total": self.total.to_dict(),
            "executions": [e.to_dict() for e in self.executions],
            "agents": [a.to_dict() for a in self.agents],
            "taches": [t.to_dict() for t in self.taches],
            "serie": [p.to_dict() for p in self.serie],
        }


def _parse_horodatage(horodatage: str) -> datetime | None:
    """L'horodatage ISO d'un événement en datetime aware (None si absent/illisible)."""
    if not horodatage:
        return None
    try:
        date = datetime.fromisoformat(horodatage)
    except ValueError:
        return None
    # Un producteur sans fuseau est réputé UTC — la convention du bus (#46).
    return date if date.tzinfo is not None else date.replace(tzinfo=UTC)


def _seau(date: datetime, pas: str) -> str:
    """Le début du seau contenant `date`, tronqué au `pas` demandé (ISO)."""
    if pas == PAS_MINUTE:
        tronquee = date.replace(second=0, microsecond=0)
    elif pas == PAS_HEURE:
        tronquee = date.replace(minute=0, second=0, microsecond=0)
    else:
        tronquee = date.replace(hour=0, minute=0, second=0, microsecond=0)
    return tronquee.isoformat()


def _usage_de(event: Event) -> StepUsage | None:
    """L'usage comptable d'un événement — même convention que le grand livre (#57).

    La mesure complète quand la télémétrie l'a rapportée, le raccourci scalaire
    `cout_usd` sinon, None quand l'événement ne rapporte rien (il ne compte
    dans aucune vue).
    """
    if event.type not in _TYPES_COMPTABLES:
        return None
    if event.usage is not None:
        return event.usage
    if event.cout_usd is not None:
        return StepUsage(cout_usd=event.cout_usd)
    return None


@dataclass
class _Accumulateur:
    """L'état mutable d'une agrégation en cours (interne à `agrege_couts`)."""

    usage: StepUsage = StepUsage()
    role: str = ""
    nom: str = ""
    agent: str = ""
    statut: str = ""
    taches: set[str] = field(default_factory=set)
    executions: set[str] = field(default_factory=set)
    debut: datetime | None = None
    fin: datetime | None = None
    debut_brut: str = ""
    fin_brut: str = ""
    projet_id: str | None = None


def _tri_par_cout(usage: StepUsage) -> tuple[float, int]:
    """Clé de tri décroissant des vues : coût rapporté d'abord, tokens ensuite."""
    return (usage.cout_usd or 0.0, usage.tokens_total)


def agrege_couts(
    executions: Sequence[EtatExecution],
    *,
    depuis: datetime | None = None,
    pas: str = PAS_HEURE,
    portee: PorteeProjet | None = None,
) -> AnalyticsCouts:
    """Agrège les coûts des exécutions projetées en vue analytique (#87).

    `depuis` (aware) restreint la fenêtre : seuls les événements datés à partir
    de cette borne comptent — un événement sans horodatage lisible est alors
    écarté (fenêtre indémontrable) ; sans borne, il compte dans les agrégats
    mais pas dans la série. `pas` fixe la granularité des seaux temporels.

    `portee` (#277, contrat de `maestro.controltower.portee`) restreint la
    dépense : seuls les événements que la portée **retient** comptent. Le filtre
    est posé événement par événement plutôt que run par run — ce qui n'a pas
    d'appartenance n'entre dans aucun total de projet, et une dépense reste
    comptée là où elle a été engagée même si un run venait à en mélanger. `None`
    vaut la vue transverse : comme pour la projection, refuser une question sans
    périmètre est le rôle des routes, pas celui du calcul.
    """
    if pas not in PAS_VALIDES:
        raise ValueError(f"pas invalide : {pas} (attendus : {', '.join(PAS_VALIDES)})")
    portee = portee if portee is not None else PorteeProjet.tous()
    if depuis is not None and depuis.tzinfo is None:
        depuis = depuis.replace(tzinfo=UTC)

    total = StepUsage()
    par_execution: dict[str, _Accumulateur] = {}
    par_agent: dict[str, _Accumulateur] = {}
    par_tache: dict[str, _Accumulateur] = {}
    par_seau: dict[str, StepUsage] = {}
    # Les tickets externes (#187) se collectent **à part** : ils n'ont pas de
    # coût, donc ils arrivent aussi sur des événements sans usage (le
    # `tache.reference` n'en porte jamais) — les accumuler dans `par_tache`
    # y créerait des lignes à usage nul pour des tâches qui n'ont rien dépensé
    # dans la fenêtre. Ils sont recollés à la construction des lignes.
    references: dict[str, ReferenceTicket] = {}
    # Les projets (#222) se collectent comme les tickets, et pour la même
    # raison : ils n'ont pas de coût et arrivent donc aussi sur des événements
    # sans usage. Recollés à la construction des lignes.
    projets: dict[str, str] = {}

    for execution in executions:
        for event in execution.evenements:
            if not portee.retient(event.projet_id):
                continue
            date = _parse_horodatage(event.horodatage)
            if depuis is not None and (date is None or date < depuis):
                continue

            # La trace du run : bornes et tâches distinctes, usage ou pas —
            # une exécution de la fenêtre reste visible même à coût inconnu.
            run = par_execution.setdefault(execution.run_id, _Accumulateur())
            if date is not None:
                if run.debut is None or date < run.debut:
                    run.debut, run.debut_brut = date, event.horodatage
                if run.fin is None or date > run.fin:
                    run.fin, run.fin_brut = date, event.horodatage
            if event.projet_id is not None:
                run.projet_id = event.projet_id
            if event.tache_id:
                run.taches.add(event.tache_id)
                if event.ticket is not None:
                    references[event.tache_id] = event.ticket
                if event.projet_id is not None:
                    projets[event.tache_id] = event.projet_id

            usage = _usage_de(event)
            if usage is None:
                continue

            total = total.fusion(usage)
            run.usage = run.usage.fusion(usage)
            if date is not None:
                seau = _seau(date, pas)
                par_seau[seau] = par_seau.get(seau, StepUsage()).fusion(usage)

            if event.agent not in _AGENTS_NON_EXECUTANTS:
                acteur = par_agent.setdefault(event.agent, _Accumulateur())
                acteur.usage = acteur.usage.fusion(usage)
                acteur.role = event.role or acteur.role
                if event.tache_id:
                    acteur.taches.add(event.tache_id)

            if event.tache_id:
                tache = par_tache.setdefault(event.tache_id, _Accumulateur())
                tache.usage = tache.usage.fusion(usage)
                tache.executions.add(execution.run_id)
                if event.type == EVENEMENT_TACHE_STATUT:
                    # L'étape de la tâche elle-même fait foi pour l'identité,
                    # comme au grand livre (#55/#57) — le dernier statut vu.
                    tache.nom = event.titre or tache.nom
                    tache.agent = event.agent or tache.agent
                    tache.role = event.role or tache.role
                    tache.statut = event.statut or tache.statut

    return AnalyticsCouts(
        depuis=depuis.isoformat() if depuis is not None else None,
        pas=pas,
        projet=portee.projet_id,
        portee=portee.libelle,
        total=total,
        executions=tuple(
            CoutExecutionResume(
                run_id=run_id,
                nb_taches=len(acc.taches),
                debut=acc.debut_brut,
                fin=acc.fin_brut,
                usage=acc.usage,
                projet_id=acc.projet_id,
            )
            for run_id, acc in sorted(
                par_execution.items(),
                # Les runs jamais datés passent en queue, entre eux dans
                # l'ordre de première apparition (tri stable).
                key=lambda item: item[1].debut or datetime.max.replace(tzinfo=UTC),
            )
        ),
        agents=tuple(
            CoutAgent(agent=nom, role=acc.role, taches=len(acc.taches), usage=acc.usage)
            for nom, acc in sorted(
                par_agent.items(), key=lambda item: _tri_par_cout(item[1].usage), reverse=True
            )
        ),
        taches=tuple(
            CoutTacheAgregee(
                tache_id=tache_id,
                nom=acc.nom,
                agent=acc.agent,
                role=acc.role,
                statut=acc.statut,
                executions=len(acc.executions),
                usage=acc.usage,
                ticket=references.get(tache_id),
                projet_id=projets.get(tache_id),
            )
            for tache_id, acc in sorted(
                par_tache.items(), key=lambda item: _tri_par_cout(item[1].usage), reverse=True
            )
        ),
        serie=tuple(
            PointCout(periode=periode, usage=usage)
            for periode, usage in sorted(par_seau.items())
        ),
    )
