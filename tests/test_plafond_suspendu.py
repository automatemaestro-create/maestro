"""Au plafond de dépense, le run se suspend et demande — relever, réduire, arrêter (#1182).

Le défaut, mesuré dans le moteur le 2026-09-21 : au plafond, la tâche en vol était
stoppée et son travail jeté, l'erreur n'était pas rejouable, et chaque tâche
restante était refusée. À 101 % du budget, la personne perdait la tâche presque
finie et tout ce qui restait, sans qu'on lui ait rien demandé.

Ce que cette suite éprouve, dans l'ordre des critères du ticket :

1. **au plafond, le run attend une décision au lieu d'échouer** — la tâche en vol
   est mise de côté (sa dépense au grand livre, son travail conservé), une seule
   question part pour tout le run, et **rien ne se dépense avant la réponse** :
   aucune tâche ne démarre, aucun appel modèle n'a lieu ;
2. **le travail de la tâche en vol n'est pas perdu** — sur un vrai projet Git, ce
   qu'elle avait écrit avant la coupure est sur sa branche, et sa reprise repart
   de là ;
3. **les trois réponses** reprennent ou soldent le run : relever reprend tout,
   réduire écarte ce que la personne a désigné, arrêter solde sur ce qui est fait.

Sans arbitre, rien ne change : l'arrêt sec d'avant reste éprouvé par
`tests/test_guardrails.py`, que ce lot ne touche pas.

Aucun réseau, aucun modèle : les fournisseurs sont des doubles qui signalent leur
dépense comme le ferait un vrai (`report_usage`), et l'arbitre est une coroutine du
test. Le projet Git est un vrai dépôt jetable (sauté sans `git`).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.engine import Guardrails, OrchestrationEngine
from maestro.engine.executor import STATUT_BLOQUEE, STATUT_ECHEC, LocalExecutor
from maestro.engine.loop import NOTE_REPRISE_AU_PLAFOND
from maestro.engine.plafond import (
    GESTE_ARRETER,
    GESTE_REDUIRE,
    GESTE_RELEVER,
    STATUT_PLAFOND_ARRETE,
    STATUT_PLAFOND_ATTEINT,
    STATUT_PLAFOND_REDUIT,
    STATUT_PLAFOND_RELEVE,
    STATUT_PLAFOND_SANS_DECISION,
    STATUT_TACHE_SUSPENDUE,
    SUFFIXE_ETAPE_PLAFOND,
    DecisionPlafond,
    DemandePlafond,
    Plafonds,
)
from maestro.orchestrator import Orchestrator
from maestro.projets import Projet, ProjetStore
from maestro.providers.base import ModelProvider
from maestro.sandbox import branche_de_tache
from maestro.telemetry import ETAPE_PLAFOND, RunCost, RunJournal, StepUsage, report_usage

GIT = shutil.which("git")


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #


class _Planificateur(ModelProvider):
    """Rend toujours le même plan — le Chef de projet du test."""

    name = "planificateur"

    def __init__(self, plan: str) -> None:
        self._plan = plan

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._plan


class _Depensier(ModelProvider):
    """Chaque appel signale deux mesures de 0,006 $ — le cumul (0,012 $) éprouve le plafond.

    `prompts` garde ce que chaque appel a reçu, `acheves` ne compte que les appels
    menés à leur terme : s'il n'avance pas, aucun travail n'a eu lieu après la
    coupure.
    """

    name = "depensier"

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.acheves = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.prompts.append(prompt)
        report_usage(StepUsage(appels=1, cout_usd=0.006))
        report_usage(StepUsage(appels=1, cout_usd=0.006))
        self.acheves += 1
        return f"LIVRABLE {self.acheves}"


class _Arbitre:
    """L'arbitre du test : note chaque demande, et répond ce qu'on lui a dit — quand on le lui dit.

    `posee` se lève à la première demande ; tant que `libre` n'est pas levé,
    l'arbitre **attend** — c'est la fenêtre où l'on vérifie que rien ne se dépense.
    """

    def __init__(self, *decisions: DecisionPlafond, attendre: bool = False) -> None:
        self._decisions = list(decisions)
        self.demandes: list[DemandePlafond] = []
        self.posee = asyncio.Event()
        self.libre = asyncio.Event()
        if not attendre:
            self.libre.set()

    async def __call__(self, demande: DemandePlafond) -> DecisionPlafond:
        self.demandes.append(demande)
        self.posee.set()
        await self.libre.wait()
        return self._decisions.pop(0)


class _ArbitreMuet:
    """Un canal qui se referme sans réponse — le bus d'une Control Tower arrêtée."""

    def __init__(self) -> None:
        self.demandes: list[DemandePlafond] = []

    async def __call__(self, demande: DemandePlafond) -> DecisionPlafond:
        self.demandes.append(demande)
        raise RuntimeError("le bus d'événements s'est refermé sans décision")


def _tache(id_, titre, *, dependances=()):
    return {
        "id": id_,
        "titre": titre,
        "description": f"Le travail de {titre}.",
        "competences_requises": ["tests"],
        "format_sortie": "Note",
        "dependances": list(dependances),
    }


def _plan(*taches) -> str:
    return json.dumps(list(taches), ensure_ascii=False)


def _moteur(provider, plan, *, arbitre=None, plafond=0.01, max_parallele=1):
    return OrchestrationEngine(
        provider,
        Orchestrator(_Planificateur(plan), model="claude-opus-4-8"),
        guardrails=Guardrails(plafond_cout_usd=plafond),
        max_parallele=max_parallele,
        arbitre_plafond=arbitre,
    )


def _lignes(journal: RunJournal, etape: str):
    return [r for r in journal.records if r.etape == etape]


async def _laisser_tourner() -> None:
    """Rend la main à la boucle assez longtemps pour que tout ce qui pouvait partir parte.

    Rien ici n'attend une horloge : tout le travail est en process, et une
    cinquantaine de tours suffit à ce qu'une tâche prête franchisse le sémaphore,
    passe l'exécuteur et vienne se ranger derrière la question.
    """
    for _ in range(50):
        await asyncio.sleep(0)


# --------------------------------------------------------------------------- #
# Critère 1 — au plafond, le run attend une décision au lieu d'échouer
# --------------------------------------------------------------------------- #


def test_au_plafond_le_run_attend_une_decision_puis_reprend_la_tache():
    provider = _Depensier()
    arbitre = _Arbitre(DecisionPlafond(GESTE_RELEVER, plafond_cout_usd=1.0))
    journal = RunJournal(run_id="run-releve")

    rapport = asyncio.run(
        _moteur(provider, _plan(_tache("t1", "Tests de l'API")), arbitre=arbitre).run(
            "Objectif", journal=journal
        )
    )

    (tache,) = rapport.resultats
    # Pas d'échec : la tâche a été mise de côté, puis reprise et menée à terme.
    assert tache.ok, tache.erreur
    assert provider.acheves == 1  # la première tentative a bien été coupée
    assert len(provider.prompts) == 2
    # Une seule question, pour ce run.
    assert len(arbitre.demandes) == 1
    # La dépense des deux tentatives voyage avec l'issue…
    assert tache.usage.cout_usd == pytest.approx(0.024)
    # …et le grand livre dit la même chose, sans tâche fantôme « plafond ».
    livre = RunCost.depuis_journal(journal)
    assert [t.tache_id for t in livre.taches] == ["t1"]
    assert livre.taches[0].usage.cout_usd == pytest.approx(0.024)
    # La tentative interrompue a laissé sa ligne, sa dépense comprise.
    (mise_de_cote,) = _lignes(journal, f"t1{SUFFIXE_ETAPE_PLAFOND}")
    assert mise_de_cote.statut == STATUT_TACHE_SUSPENDUE
    assert mise_de_cote.usage.cout_usd == pytest.approx(0.012)
    # Le run dit qu'il a attendu, puis ce qui a été décidé.
    assert [r.statut for r in _lignes(journal, ETAPE_PLAFOND)] == [
        STATUT_PLAFOND_ATTEINT,
        STATUT_PLAFOND_RELEVE,
    ]
    # Le rapport tient le plafond relevé, celui qui a tenu jusqu'au bout.
    assert rapport.plafond_cout_usd == 1.0


def test_la_demande_porte_la_depense_les_plafonds_et_le_reste_a_faire():
    provider = _Depensier()
    arbitre = _Arbitre(DecisionPlafond(GESTE_ARRETER))

    asyncio.run(
        _moteur(
            provider,
            _plan(
                _tache("t1", "Tests de l'API"),
                _tache("t2", "Tests de charge"),
                _tache("t3", "Rapport", dependances=("t2",)),
            ),
            arbitre=arbitre,
        ).run("Livrer l'API", journal=RunJournal(run_id="run-faits"))
    )

    (demande,) = arbitre.demandes
    assert demande.run_id == "run-faits"
    assert demande.objectif == "Livrer l'API"
    assert demande.depense_usd == pytest.approx(0.012)
    assert demande.depense_tokens == 0
    assert demande.plafond_cout_usd == 0.01
    assert demande.plafond_tokens is None
    assert "plafond de dépense dépassé" in demande.raison
    # Tout ce qui reste, dans l'ordre du plan — et la tâche coupée dite comme telle.
    assert [(t.tache_id, t.interrompue) for t in demande.restantes] == [
        ("t1", True),
        ("t2", False),
        ("t3", False),
    ]


def test_rien_ne_se_depense_ni_ne_demarre_avant_la_reponse():
    provider = _Depensier()
    arbitre = _Arbitre(DecisionPlafond(GESTE_RELEVER, plafond_cout_usd=1.0), attendre=True)
    journal = RunJournal(run_id="run-attente")
    moteur = _moteur(
        provider,
        _plan(_tache("t1", "Tests de l'API"), _tache("t2", "Tests de charge")),
        arbitre=arbitre,
    )

    async def scenario():
        run = asyncio.create_task(moteur.run("Objectif", journal=journal))
        await arbitre.posee.wait()
        await _laisser_tourner()
        # Pendant l'attente : un seul appel a eu lieu — celui qui a franchi le
        # plafond —, la seconde tâche n'a pas démarré, et la dépense est figée.
        pendant = (len(provider.prompts), journal.usage_totale.cout_usd, run.done())
        arbitre.libre.set()
        return pendant, await run

    (appels, depense, fini), rapport = asyncio.run(scenario())

    assert appels == 1
    assert depense == pytest.approx(0.012)
    assert not fini
    # La seconde tâche, arrivée pendant l'attente, a rejoint la **même** question.
    assert len(arbitre.demandes) == 1
    # Et après la réponse, les deux aboutissent.
    assert all(r.ok for r in rapport.resultats)
    assert len(provider.prompts) == 3


def test_plusieurs_taches_au_plafond_attendent_une_seule_reponse():
    provider = _Depensier()
    arbitre = _Arbitre(DecisionPlafond(GESTE_RELEVER, plafond_cout_usd=1.0))

    rapport = asyncio.run(
        _moteur(
            provider,
            _plan(_tache("t1", "Tests de l'API"), _tache("t2", "Tests de charge")),
            arbitre=arbitre,
            # Au premier signalement de chacune, en parallèle : les deux franchissent.
            plafond=0.005,
            max_parallele=None,
        ).run("Objectif", journal=RunJournal(run_id="run-parallele"))
    )

    assert len(arbitre.demandes) == 1
    assert all(r.ok for r in rapport.resultats)


# --------------------------------------------------------------------------- #
# Critère 1 (suite) — le travail de la tâche en vol n'est pas perdu
# --------------------------------------------------------------------------- #


def _git(cwd: Path, *arguments: str) -> str:
    resultat = subprocess.run(
        [GIT or "git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert resultat.returncode == 0, f"git {' '.join(arguments)} : {resultat.stderr}"
    return resultat.stdout


def _projet_git(tmp_path: Path) -> tuple[ProjetStore, Projet]:
    """Un dépôt de projets et un projet versionné : dépôt jetable, un commit."""
    racine = tmp_path / "projets" / "agenda"
    racine.mkdir(parents=True)
    (racine / "README.md").write_text("# Agenda\n", encoding="utf-8")
    _git(racine, "init", "--quiet")
    _git(racine, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(racine, "add", "-A")
    _git(
        racine, "-c", "user.email=tests@maestro", "-c", "user.name=Tests",
        "commit", "--quiet", "-m", "socle",
    )
    depot = ProjetStore(tmp_path / "depot")
    projet = depot.creer("Agenda", racine)
    assert projet.versionne, projet
    return depot, projet


class _EcritPuisDepense(ModelProvider):
    """Un développeur outillé qui écrit **avant** de dépenser — et qui regarde ce qu'il retrouve.

    Première tentative : il écrit `moitie.md`, puis sa mesure franchit le plafond.
    À la reprise, il note ce que son espace contient en entrant et ce que sa
    description lui dit, écrit `fin.md` et termine.
    """

    name = "ecrit-puis-depense"

    def __init__(self) -> None:
        self.tentatives = 0
        self.retrouves: list[str] = []
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **canaux):
        self.tentatives += 1
        self.prompts.append(prompt)
        espace = Path(workspace)
        if self.tentatives == 1:
            (espace / "moitie.md").write_text("la moitié du travail", encoding="utf-8")
        else:
            self.retrouves = sorted(
                p.name for p in espace.iterdir() if p.is_file() and p.name.endswith(".md")
            )
            (espace / "fin.md").write_text("la fin du travail", encoding="utf-8")
        report_usage(StepUsage(appels=1, cout_usd=0.006))
        report_usage(StepUsage(appels=1, cout_usd=0.006))
        return "Fait."


class _Accord:
    """Le validateur du test : accorde l'écriture dans le projet."""

    async def __call__(self, demande) -> bool:
        return True


@pytest.mark.skipif(GIT is None, reason="git introuvable")
def test_le_travail_de_la_tache_en_vol_n_est_pas_perdu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig-absent"))
    depot, projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    fournisseur = _EcritPuisDepense()
    guardrails = Guardrails(plafond_cout_usd=0.01, validateur=_Accord())
    executeur = LocalExecutor(
        fournisseur,
        runtimes={DEVELOPER_PROFILE.nom: AgentRuntime(fournisseur, DEVELOPER_PROFILE)},
        projets=depot,
        guardrails=guardrails,
    )
    plan = json.dumps(
        [
            {
                "id": "t1",
                "titre": "Écrire l'agenda",
                "description": "Écrire l'agenda.",
                "competences_requises": ["backend"],
                "format_sortie": "markdown",
                "dependances": [],
            }
        ]
    )
    arbitre = _Arbitre(DecisionPlafond(GESTE_RELEVER, plafond_cout_usd=1.0), attendre=True)
    moteur = OrchestrationEngine(
        fournisseur,
        Orchestrator(_Planificateur(plan), model="claude-opus-4-8"),
        guardrails=guardrails,
        executor=executeur,
        arbitre_plafond=arbitre,
    )

    async def scenario():
        run = asyncio.create_task(
            moteur.run("Agenda", journal=RunJournal(run_id="run-travail"), projet_id=projet.id)
        )
        await arbitre.posee.wait()
        # Pendant que le run attend : le travail de la tâche coupée est sur sa branche.
        sur_la_branche = _git(racine, "show", f"{branche_de_tache('t1')}:moitie.md").strip()
        arbitre.libre.set()
        return sur_la_branche, await run

    sur_la_branche, rapport = asyncio.run(scenario())

    assert sur_la_branche == "la moitié du travail"
    (tache,) = rapport.resultats
    assert tache.ok, tache.erreur
    # La reprise est repartie de là : elle a retrouvé la moitié écrite…
    assert "moitie.md" in fournisseur.retrouves
    # …et on lui a dit qu'elle reprenait.
    assert NOTE_REPRISE_AU_PLAFOND not in fournisseur.prompts[0]
    assert NOTE_REPRISE_AU_PLAFOND in fournisseur.prompts[1]
    # Les deux moitiés ont rejoint le projet.
    assert (racine / "moitie.md").read_text(encoding="utf-8") == "la moitié du travail"
    assert (racine / "fin.md").read_text(encoding="utf-8") == "la fin du travail"


# --------------------------------------------------------------------------- #
# Critère 2 — « relever », « réduire » ou « arrêter » reprennent ou soldent le run
# --------------------------------------------------------------------------- #


def test_arreter_solde_le_run_sur_ce_qui_est_fait():
    provider = _Depensier()
    arbitre = _Arbitre(DecisionPlafond(GESTE_ARRETER, detail="arrêté depuis le fil"))
    journal = RunJournal(run_id="run-arret")

    rapport = asyncio.run(
        _moteur(
            provider,
            _plan(_tache("t1", "Tests de l'API"), _tache("t2", "Tests de charge")),
            arbitre=arbitre,
        ).run("Objectif", journal=journal)
    )

    premiere, seconde = rapport.resultats
    assert premiere.statut == STATUT_ECHEC and seconde.statut == STATUT_ECHEC
    assert "sur décision de l'utilisateur" in (premiere.erreur or "")
    assert "plafond de dépense dépassé" in (premiere.erreur or "")
    assert not premiere.rattrapable
    # Rien de plus n'a été dépensé : un appel, coupé ; la seconde n'a jamais démarré.
    assert len(provider.prompts) == 1
    assert seconde.agent == "—"
    assert journal.usage_totale.cout_usd == pytest.approx(0.012)
    assert len(arbitre.demandes) == 1
    (_, decision) = _lignes(journal, ETAPE_PLAFOND)
    assert decision.statut == STATUT_PLAFOND_ARRETE
    assert "arrêté depuis le fil" in decision.sortie


def test_reduire_ecarte_les_taches_designees_et_reprend_le_reste():
    provider = _Depensier()
    arbitre = _Arbitre(
        DecisionPlafond(GESTE_REDUIRE, plafond_cout_usd=1.0, ecartees=("t2",))
    )
    journal = RunJournal(run_id="run-reduit")

    rapport = asyncio.run(
        _moteur(
            provider,
            _plan(
                _tache("t1", "Tests de l'API"),
                _tache("t2", "Tests de charge"),
                _tache("t3", "Rapport de charge", dependances=("t2",)),
            ),
            arbitre=arbitre,
        ).run("Objectif", journal=journal)
    )

    t1, t2, t3 = rapport.resultats
    # La tâche coupée reprend et aboutit…
    assert t1.ok, t1.erreur
    # …celle qu'on a écartée ne part pas, et ce qui l'attendait se bloque.
    assert t2.statut == STATUT_ECHEC and "écartée" in (t2.erreur or "")
    assert t3.statut == STATUT_BLOQUEE
    assert len(provider.prompts) == 2  # t1 coupée, t1 reprise — rien pour t2
    (_, decision) = _lignes(journal, ETAPE_PLAFOND)
    assert decision.statut == STATUT_PLAFOND_REDUIT
    assert "« Tests de charge »" in decision.sortie


def test_un_plafond_releve_trop_court_repose_la_question():
    provider = _Depensier()
    arbitre = _Arbitre(
        # 0,015 $ couvre la dépense (0,012 $) mais pas la reprise (+ 0,012 $)…
        DecisionPlafond(GESTE_RELEVER, plafond_cout_usd=0.015),
        # …qui revient donc au plafond, et c'est la personne qui décide encore.
        DecisionPlafond(GESTE_ARRETER),
    )
    journal = RunJournal(run_id="run-deux-fois")

    rapport = asyncio.run(
        _moteur(provider, _plan(_tache("t1", "Tests de l'API")), arbitre=arbitre).run(
            "Objectif", journal=journal
        )
    )

    assert len(arbitre.demandes) == 2
    assert arbitre.demandes[1].plafond_cout_usd == 0.015
    (tache,) = rapport.resultats
    assert tache.statut == STATUT_ECHEC
    # Les deux tentatives coupées sont comptées, sur la tâche comme au grand livre :
    # 0,012 $ pour la première, 0,006 $ pour la reprise, coupée à sa première
    # mesure (0,018 $ > 0,015 $).
    assert tache.usage.cout_usd == pytest.approx(0.018)
    assert journal.usage_totale.cout_usd == pytest.approx(0.018)


def test_un_canal_muet_n_invente_pas_de_reponse():
    provider = _Depensier()
    arbitre = _ArbitreMuet()
    journal = RunJournal(run_id="run-muet")

    rapport = asyncio.run(
        _moteur(
            provider,
            _plan(_tache("t1", "Tests de l'API"), _tache("t2", "Tests de charge")),
            arbitre=arbitre,
        ).run("Objectif", journal=journal)
    )

    # La question n'a pas pu partir : le run s'arrête comme avant, en le disant.
    assert len(arbitre.demandes) == 1
    assert all(r.statut == STATUT_ECHEC for r in rapport.resultats)
    assert "n'a pas pu être demandée" in (rapport.resultats[0].erreur or "")
    assert len(provider.prompts) == 1
    (_, issue) = _lignes(journal, ETAPE_PLAFOND)
    assert issue.statut == STATUT_PLAFOND_SANS_DECISION


# --------------------------------------------------------------------------- #
# Le contrat de la décision et du registre
# --------------------------------------------------------------------------- #


def test_une_reprise_sans_nouveau_plafond_n_existe_pas():
    with pytest.raises(ValueError, match="nouveau plafond"):
        DecisionPlafond(GESTE_RELEVER)
    with pytest.raises(ValueError, match="écarter"):
        DecisionPlafond(GESTE_REDUIRE, plafond_cout_usd=2.0)
    with pytest.raises(ValueError, match="inconnu"):
        DecisionPlafond("continuer")
    # Arrêter ne demande rien.
    assert not DecisionPlafond(GESTE_ARRETER).reprend


def test_la_decision_et_la_demande_font_l_aller_retour_json():
    decision = DecisionPlafond(
        GESTE_REDUIRE, plafond_tokens=5000, ecartees=("t2",), detail="depuis le fil"
    )
    assert DecisionPlafond.from_dict(json.loads(json.dumps(decision.to_dict()))) == decision
    demande = DemandePlafond(
        run_id="r",
        projet_id="p",
        objectif="o",
        depense_usd=None,
        depense_tokens=1200,
        plafond_cout_usd=None,
        plafond_tokens=1000,
        raison="plafond de tokens dépassé",
    )
    assert DemandePlafond.from_dict(json.loads(json.dumps(demande.to_dict()))) == demande


def test_un_plafond_releve_ne_vaut_que_pour_son_run():
    plafonds = Plafonds.de(Guardrails(plafond_cout_usd=1.0, plafond_tokens=100))
    plafonds.relever("a", cout_usd=5.0)
    assert plafonds.en_vigueur("a") == (5.0, 100)
    assert plafonds.en_vigueur("b") == (1.0, 100)
