"""Tests de la scalabilité horizontale : N instances d'un même agent en parallèle réel (#100).

Simule le **run de charge de démonstration** (docs/07 §6.6) sur fournisseurs
factices — aucun appel réseau : plusieurs tâches indépendantes routées vers le
même agent (`bdd`), la capacité (#86) réglée à N instances. Couvre les trois
critères d'acceptation :

① le moteur exécute en parallèle jusqu'à **N tâches d'un même agent** (N = le
   plafond d'instances de la capacité), jamais au-delà ; sans réglage, une
   instance sérialise ; le **plafond global** du run (`max_parallele`) reste
   prioritaire sur la capacité par agent ; un agent **désactivé reste à 0**
   (aucune exécution — repli « à assigner ») ;
② coûts, journal, grand livre et événements temps réel restent corrects **par
   tâche** en multi-instances : usage attribué à la bonne tâche sans mélange
   entre instances, et la projection Control Tower (journal → pont → état) voit
   l'agent porter plusieurs tâches à la fois sans se déclarer libre trop tôt ;
③ le run de charge est rejouable depuis la CLI : `--parallele <n>` pose le
   plafond global (validation du flag dans tests/test_cli_smoke.py).
"""

import asyncio
import json

import pytest

from maestro.agents.capacity import CapaciteAgent, CapacityStore
from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.events import EVENEMENT_TACHE_STATUT
from maestro.controltower.state import AGENT_LIBRE, AGENT_OCCUPE, ControlTowerState
from maestro.engine import STATUT_ECHEC, STATUT_EN_COURS, STATUT_TERMINEE, OrchestrationEngine
from maestro.orchestrator import Orchestrator
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal, StepUsage, report_usage
from maestro.telemetry.costs import RunCost


def _plan_charge(n: int) -> str:
    """Un plan de charge : `n` tâches **indépendantes**, toutes pour l'agent `bdd` (sql)."""
    return json.dumps(
        [
            {
                "id": f"t{i}",
                "titre": f"Requête SQL n°{i}",
                "description": f"Écrire la requête d'analyse n°{i}.",
                "competences_requises": ["sql"],
                "format_sortie": "SQL",
                "dependances": [],
            }
            for i in range(1, n + 1)
        ],
        ensure_ascii=False,
    )


class PlanProvider(ModelProvider):
    """Planificateur factice : rend toujours le même plan JSON."""

    name = "plan"

    def __init__(self, plan: str) -> None:
        self._plan = plan

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._plan


class PicProvider(ModelProvider):
    """Mesure le pic d'appels simultanés, chaque appel cédant brièvement la main."""

    name = "pic"

    def __init__(self) -> None:
        self._en_vol = 0
        self.pic = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self._en_vol += 1
        self.pic = max(self.pic, self._en_vol)
        await asyncio.sleep(0.01)
        self._en_vol -= 1
        return "LIVRABLE"


class RendezvousProvider(ModelProvider):
    """Force la simultanéité : chaque appel attend `attendus` appels en vol.

    En exécution sérialisée, le premier appel attendrait seul : le timeout borne
    l'attente et mue une régression en échec net plutôt qu'en suite suspendue.
    `usage`, si fourni, est signalé *pendant* la simultanéité — pour vérifier
    que les collecteurs de contextes concurrents ne se contaminent pas.
    """

    name = "rendezvous"

    def __init__(self, attendus: int, *, usage: StepUsage | None = None) -> None:
        self._attendus = attendus
        self._usage = usage
        self._en_vol = 0
        self._complet = asyncio.Event()
        self.pic = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self._en_vol += 1
        self.pic = max(self.pic, self._en_vol)
        if self._en_vol >= self._attendus:
            self._complet.set()
        await asyncio.wait_for(self._complet.wait(), timeout=5)
        if self._usage is not None:
            report_usage(self._usage)
        self._en_vol -= 1
        return "LIVRABLE"


class TraceurProvider(ModelProvider):
    """Enregistre chaque prompt reçu ; sa réponse ne désigne jamais un agent du catalogue."""

    name = "traceur"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.prompts.append(prompt)
        return "hors-catalogue"


def _moteur(provider, *, plan, capacites=None, max_parallele=None):
    orchestrator = Orchestrator(PlanProvider(plan), model="claude-opus-4-8")
    return OrchestrationEngine(
        provider, orchestrator, capacites=capacites, max_parallele=max_parallele
    )


# --- ① N instances = N tâches d'un même agent en parallèle réel ---------------------------


def test_deux_instances_executent_deux_taches_du_meme_agent_de_front(tmp_path):
    store = CapacityStore(tmp_path)
    store.ecrire(CapaciteAgent(nom="bdd", instances=2))
    provider = RendezvousProvider(attendus=2)

    report = asyncio.run(
        _moteur(provider, plan=_plan_charge(4), capacites=store).run("Run de charge")
    )

    assert all(r.ok for r in report.resultats)
    assert {r.agent for r in report.resultats} == {"bdd"}
    # ≥ 2 : parallélisme réel (le rendez-vous n'aboutit pas en séquentiel) ;
    # ≤ 2 : le plafond d'instances n'est jamais dépassé, même à 4 tâches prêtes.
    assert provider.pic == 2


def test_sans_reglage_une_instance_serialise_la_charge(tmp_path):
    # Capacité par défaut (une instance) : les tâches du même agent se suivent.
    provider = PicProvider()

    report = asyncio.run(
        _moteur(provider, plan=_plan_charge(3), capacites=CapacityStore(tmp_path)).run(
            "Run de charge"
        )
    )

    assert all(r.ok for r in report.resultats)
    assert provider.pic == 1


def test_le_plafond_global_du_run_reste_prioritaire(tmp_path):
    # Trois instances accordées à l'agent, mais un plafond global de 1 : c'est le
    # plafond transverse qui borne — la capacité par agent s'applique en dessous.
    store = CapacityStore(tmp_path)
    store.ecrire(CapaciteAgent(nom="bdd", instances=3))
    provider = PicProvider()

    report = asyncio.run(
        _moteur(provider, plan=_plan_charge(3), capacites=store, max_parallele=1).run(
            "Run de charge"
        )
    )

    assert all(r.ok for r in report.resultats)
    assert provider.pic == 1


def test_un_agent_desactive_reste_a_zero_execution(tmp_path):
    store = CapacityStore(tmp_path)
    store.ecrire(CapaciteAgent(nom="bdd", actif=False))
    provider = TraceurProvider()

    report = asyncio.run(
        _moteur(provider, plan=_plan_charge(3), capacites=store).run("Run de charge")
    )

    assert all(r.statut == STATUT_ECHEC for r in report.resultats)
    assert all("à assigner" in (r.erreur or "") for r in report.resultats)
    assert all(r.agent != "bdd" for r in report.resultats)
    # Zéro exécution : seuls des prompts de routage (classifieur) ont pu partir,
    # jamais un prompt de production de livrable.
    assert not [p for p in provider.prompts if "Produis maintenant le livrable demandé." in p]


# --- ② Comptabilité et temps réel corrects par tâche en multi-instances -------------------

_USAGE_UNITAIRE = StepUsage(appels=1, tokens_entree=100, tokens_sortie=20, cout_usd=0.01)


def _run_de_charge_multi_instances(tmp_path):
    """Deux tâches du même agent exécutées de front (2 instances), journal conservé."""
    store = CapacityStore(tmp_path)
    store.ecrire(CapaciteAgent(nom="bdd", instances=2))
    provider = RendezvousProvider(attendus=2, usage=_USAGE_UNITAIRE)
    journal = RunJournal()
    report = asyncio.run(
        _moteur(provider, plan=_plan_charge(2), capacites=store).run(
            "Run de charge", journal=journal
        )
    )
    assert provider.pic == 2  # la simultanéité a bien eu lieu
    return report, journal


def test_les_usages_restent_par_tache_en_multi_instances(tmp_path):
    report, journal = _run_de_charge_multi_instances(tmp_path)

    # Chaque résultat porte l'usage de SA tâche — pas celui des deux instances.
    for r in report.resultats:
        assert r.usage.appels == 1
        assert r.usage.tokens_total == 120
        assert r.usage.cout_usd == pytest.approx(0.01)

    # Le grand livre (#55) attribue chaque dépense à sa tâche, une seule fois.
    livre = RunCost.depuis_journal(journal)
    entrees = {t.tache_id: t for t in livre.taches}
    assert set(entrees) == {"t1", "t2"}
    for entree in entrees.values():
        assert entree.agent == "bdd"
        assert entree.usage.cout_usd == pytest.approx(0.01)
    assert livre.total.cout_usd == pytest.approx(0.02)

    # Le journal porte le début (#98) et l'issue de chaque instance, par tâche.
    etapes = {r.etape for r in journal.records}
    assert {"t1", "t1:debut", "t2", "t2:debut"} <= etapes


def test_le_temps_reel_ne_melange_pas_les_instances(tmp_path):
    # Rejoue le flux complet journal → pont (#46) → projection : la fiche agent
    # doit porter les deux tâches en vol, puis ne se libérer qu'à la dernière.
    _, journal = _run_de_charge_multi_instances(tmp_path)
    evenements = [
        e for record in journal.records for e in evenements_depuis_step(record.to_dict())
    ]
    state = ControlTowerState()

    debuts = [e for e in evenements if e.statut == STATUT_EN_COURS]
    for e in debuts:
        state.appliquer(e)
    fiche = state.agent("bdd")
    assert fiche.statut == AGENT_OCCUPE
    assert set(fiche.taches_en_cours) == {"t1", "t2"}

    finales = [
        e
        for e in evenements
        if e.type == EVENEMENT_TACHE_STATUT and e.statut == STATUT_TERMINEE
    ]
    assert len(finales) == 2
    state.appliquer(finales[0])
    fiche = state.agent("bdd")
    # Une instance rendue : l'agent reste occupé sur l'autre — pas libéré trop tôt.
    assert fiche.statut == AGENT_OCCUPE
    assert len(fiche.taches_en_cours) == 1
    assert fiche.taches_terminees == 1

    state.appliquer(finales[1])
    fiche = state.agent("bdd")
    assert fiche.statut == AGENT_LIBRE
    assert fiche.taches_en_cours == []
    assert fiche.tache_courante == ""
    assert fiche.taches_terminees == 2
    # Le coût cumulé de l'agent somme ses deux instances, sans double compte.
    assert fiche.cout_usd == pytest.approx(0.02)
