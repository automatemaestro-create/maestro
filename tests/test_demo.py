"""Tests de la démo POC (ticket #10) : le parcours complet sur fournisseurs factices.

Aucun appel réseau. Couvre les critères d'acceptation du ticket #10 :
① la **démo tourne de bout en bout** (plan JSON → agents → agrégat) : elle écrit ses
  artefacts (synthèse, rapport, journal, verdict, livrables par tâche) et sort en 0
  quand le critère de sortie est rempli ;
② le **critère de sortie Phase 0 est vérifié** volet par volet (« des tâches »,
  « 2 agents produisent », « un résultat exploitable ») et le verdict bascule à
  NON VALIDÉ (code 1) dès qu'un volet manque (un seul agent, tâche en échec) ;
③ dérouler la démo sur des fournisseurs factices est aussi la **preuve exécutable**
  du volet architectural du critère : la boucle ne dépend que de `ModelProvider`.
"""

import json

from maestro.demo import main, rendu_verdict, run_demo, verifier_critere_sortie
from maestro.engine import (
    STATUT_ECHEC,
    STATUT_TERMINEE,
    OrchestrationEngine,
    RunReport,
    TaskResult,
)
from maestro.orchestrator import Orchestrator
from maestro.providers.base import ModelProvider
from maestro.sandbox import ProducedFile


class ConstantProvider(ModelProvider):
    """Renvoie toujours la même réponse (sert de planificateur factice)."""

    name = "constant"

    def __init__(self, response):
        self._response = response

    def supports(self, model):
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._response


class EchoProvider(ModelProvider):
    """Exécutant factice texte-seul : livre un texte distinct par appel."""

    name = "echo"

    def __init__(self):
        self.calls = 0

    def supports(self, model):
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.calls += 1
        return f"LIVRABLE #{self.calls}"


def _plan_json(taches=None):
    # 2 tâches en chaîne : bdd (schéma) puis developpeur (API) — le duo du POC.
    defaut = [
        {
            "id": "schema-contacts",
            "titre": "Schéma des contacts",
            "description": "Table contacts + migration.",
            "competences_requises": ["sql", "schema"],
            "format_sortie": "Fichier SQL",
            "dependances": [],
        },
        {
            "id": "api-contacts",
            "titre": "API des contacts",
            "description": "Endpoints créer/lister.",
            "competences_requises": ["backend", "api"],
            "format_sortie": "Module d'API",
            "dependances": ["schema-contacts"],
        },
    ]
    return json.dumps(taches if taches is not None else defaut, ensure_ascii=False)


def _engine(*, plan_json=None, exec_provider=None):
    planner = ConstantProvider(plan_json or _plan_json())
    orchestrator = Orchestrator(planner, model="claude-opus-4-8")
    execu = exec_provider if exec_provider is not None else EchoProvider()
    # runtimes={} : exécution texte-seule — la démo n'exige pas le chemin outillé.
    return OrchestrationEngine(execu, orchestrator, runtimes={})


def _resultat(task_id, agent, role, *, ok=True, fichiers=()):
    return TaskResult(
        task_id=task_id,
        titre=task_id,
        agent=agent,
        role=role,
        competences_requises=(),
        score=1,
        statut=STATUT_TERMINEE if ok else STATUT_ECHEC,
        sortie=f"Livrable de {task_id}" if ok else "",
        erreur=None if ok else "explosion en vol",
        fichiers=tuple(fichiers),
    )


# --- Critère ① : la démo tourne de bout en bout et écrit ses artefacts ----------------


def test_demo_tourne_de_bout_en_bout_et_valide_le_critere(tmp_path, capsys):
    code = run_demo(_engine(), objectif="Prototyper un mini-CRM", dossier=tmp_path)

    assert code == 0
    racine = next(tmp_path.glob("run-*"))
    for artefact in ("synthese.md", "rapport.json", "journal.jsonl", "verdict.md"):
        assert (racine / artefact).is_file()

    verdict = (racine / "verdict.md").read_text(encoding="utf-8")
    assert "Critère de sortie Phase 0 — VALIDÉ" in verdict
    # Le verdict est aussi imprimé (la démo se joue sur la console).
    assert "Critère de sortie Phase 0 — VALIDÉ" in capsys.readouterr().out

    # Les livrables texte des 2 tâches sont écrits dans des fichiers (docs/06 Phase 0).
    assert (racine / "livrables" / "schema-contacts" / "livrable.md").is_file()
    assert (racine / "livrables" / "api-contacts" / "livrable.md").is_file()


def test_journal_relie_planification_et_taches_au_run_id(tmp_path):
    run_demo(_engine(), objectif="Objectif", dossier=tmp_path)

    racine = next(tmp_path.glob("run-*"))
    lignes = [
        json.loads(ligne)
        for ligne in (racine / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {ligne["etape"] for ligne in lignes} == {
        "planification",
        "schema-contacts:debut", "schema-contacts",
        "api-contacts:debut", "api-contacts",
    }
    # Toutes les étapes portent le run_id du dossier d'artefacts.
    assert {ligne["run_id"] for ligne in lignes} == {racine.name.removeprefix("run-")}


def test_les_fichiers_produits_sont_ecrits_dans_les_livrables(tmp_path):
    fichiers = (
        ProducedFile(chemin="migrations/001_contacts.sql", contenu="CREATE TABLE contacts;"),
        ProducedFile(chemin="binaire.bin", contenu=None),
    )
    report = RunReport(
        objectif="Objectif",
        resultats=(
            _resultat("schema-contacts", "bdd", "Base de données", fichiers=fichiers),
            _resultat("api-contacts", "developpeur", "Développeur"),
        ),
        run_id="abc123",
    )
    from maestro.demo import ecrire_artefacts
    from maestro.telemetry import RunJournal

    racine = ecrire_artefacts(
        report, RunJournal(run_id="abc123"), verifier_critere_sortie(report), tmp_path
    )

    sql = racine / "livrables" / "schema-contacts" / "fichiers" / "migrations" / "001_contacts.sql"
    assert sql.read_text(encoding="utf-8") == "CREATE TABLE contacts;"
    # Le binaire (contenu None) n'est pas écrit, mais reste listé dans le rapport.
    assert not (racine / "livrables" / "schema-contacts" / "fichiers" / "binaire.bin").exists()
    rapport = json.loads((racine / "rapport.json").read_text(encoding="utf-8"))
    assert "binaire.bin" in json.dumps(rapport)


# --- Critère ② : le critère de sortie est vérifié volet par volet ----------------------


def test_verifier_critere_valide_avec_deux_agents_et_zero_echec():
    report = RunReport(
        objectif="Objectif",
        resultats=(
            _resultat("t1", "bdd", "Base de données"),
            _resultat("t2", "developpeur", "Développeur"),
        ),
    )
    verifications = verifier_critere_sortie(report)

    assert [v.ok for v in verifications] == [True, True, True]


def test_verifier_critere_echoue_avec_un_seul_agent():
    report = RunReport(
        objectif="Objectif",
        resultats=(
            _resultat("t1", "developpeur", "Développeur"),
            _resultat("t2", "developpeur", "Développeur"),
        ),
    )
    verifications = {v.volet: v.ok for v in verifier_critere_sortie(report)}

    assert verifications["des tâches"] is True
    assert verifications["2 agents produisent"] is False
    assert "NON VALIDÉ" in rendu_verdict(report, verifier_critere_sortie(report))


def test_verifier_critere_echoue_si_une_tache_echoue():
    report = RunReport(
        objectif="Objectif",
        resultats=(
            _resultat("t1", "bdd", "Base de données"),
            _resultat("t2", "developpeur", "Développeur", ok=False),
        ),
    )
    verifications = {v.volet: v.ok for v in verifier_critere_sortie(report)}

    # L'agent en échec n'a rien produit : les deux volets tombent.
    assert verifications["2 agents produisent"] is False
    assert verifications["un résultat exploitable"] is False


def test_demo_sort_en_1_si_le_plan_ne_mobilise_qu_un_agent(tmp_path):
    plan = _plan_json(
        [
            {
                "id": "api-seule",
                "titre": "API seule",
                "description": "Un seul domaine.",
                "competences_requises": ["backend"],
                "format_sortie": "Module",
                "dependances": [],
            }
        ]
    )
    code = run_demo(_engine(plan_json=plan), objectif="Objectif", dossier=tmp_path)

    assert code == 1
    racine = next(tmp_path.glob("run-*"))
    assert "NON VALIDÉ" in (racine / "verdict.md").read_text(encoding="utf-8")


# --- Garde-fous du CLI ------------------------------------------------------------------


def test_main_refuse_un_plafond_negatif(capsys):
    # Refusé par la validation des garde-fous (#9), avant toute construction de moteur.
    assert main(["--plafond-cout", "-1"]) == 2
    assert "Garde-fous" in capsys.readouterr().err
