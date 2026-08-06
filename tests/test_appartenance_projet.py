"""Tests du `projet_id` porté par la tâche et le run (#222, EF-35).

Lot final de la Phase 7 (#220, parent #219) — le lot 2 avait livré sans tests
(convention de découpage, [docs/10 §5.1](../docs/10-workflow-git.md)). Le fil
qui relie un **travail** à un projet traverse modèle → journal → événements →
projection → vues ; ces tests le suivent d'un bout à l'autre.

① la **normalisation** (`maestro.appartenance`) : tout ce qui vient de
   l'extérieur passe par `projet_id_valide`, qui rend `None` plutôt que de
   lever — l'appartenance est une donnée de rattachement, pas une donnée dont
   dépend l'exécution. C'est aussi un garde-fou : l'identifiant sert de nom de
   fichier au dépôt (#221), donc rien de non conforme ne doit voyager jusque-là ;
② le **modèle** (`Task`) et l'**événement** (`Event`) : aller-retour fidèle, clé
   **omise** quand il n'y a pas de projet (un plan sans projet reste
   sérialisable tel quel), et normalisation à la relecture ;
③ la **projection** (`ControlTowerState`) : la tâche hérite du projet porté par
   ses événements, le run le tient de son lancement — ou, à défaut, de sa
   première étape (un run publié hors de l'API n'émet aucun lancement) — et un
   champ absent n'efface jamais celui déjà posé ;
④ le **moteur** : `run(projet_id=…)` fait hériter chaque tâche du plan, sauf
   celle qui en porte déjà un, et l'étape de **planification** le porte elle
   aussi — c'est une dépense du projet, l'omettre creuserait un écart entre le
   total d'un projet et la somme de ses runs ;
⑤ les **vues filtrées** (`/api/taches`, `/api/executions`, `/api/analytics/couts`) :
   sans filtre, tout sort — projets confondus, comportement d'avant ce lot ;
   avec, un travail sans projet n'apparaît nulle part, et un identifiant
   inconnu rend une vue **vide** plutôt qu'une vue fausse.

Ni réseau ni Redis : bus mémoire, fournisseurs factices, TestClient de Starlette.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from maestro.appartenance import LONGUEUR_MAX_ID, projet_id_valide
from maestro.controltower import (
    EVENEMENT_TACHE_STATUT,
    ControlTowerState,
    Event,
    InMemoryEventBus,
    create_app,
)
from maestro.controltower.analytics import agrege_couts
from maestro.engine import OrchestrationEngine
from maestro.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal, StepUsage

PROJET = "prj-depensio"
AUTRE = "prj-autre"


class _Constant(ModelProvider):
    """Fournisseur factice : rend toujours la même réponse (planificateur ou exécutant)."""

    name = "constant"

    def __init__(self, reponse: str) -> None:
        self._reponse = reponse

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._reponse


def _plan_json(*taches: dict) -> str:
    """Un plan JSON minimal — la sortie que le planificateur factice rendra."""
    return json.dumps(list(taches), ensure_ascii=False)


def _tache(id_: str, **extra) -> dict:
    """Une tâche de plan bien formée et **routable** (compétences du catalogue)."""
    return {
        "id": id_,
        "titre": f"Tâche {id_}",
        "description": f"Faire {id_}.",
        "competences_requises": ["backend", "api"],
        "format_sortie": "markdown",
        "dependances": [],
        **extra,
    }


def _moteur(plan: str) -> OrchestrationEngine:
    """Un moteur dont la planification comme l'exécution sont factices."""
    return OrchestrationEngine(_Constant("LIVRABLE"), Orchestrator(_Constant(plan), model="m"))


def _evenement(**champs) -> Event:
    """Un événement de statut de tâche, du type que la projection applique."""
    base = {"type": EVENEMENT_TACHE_STATUT, "statut": "terminee", "agent": "developpeur"}
    return Event(**{**base, **champs})


# --- ① La normalisation de ce qui vient de l'extérieur -----------------------


@pytest.mark.parametrize(
    "brut",
    ["prj-depensio", "prj-1", "a", "projet_avec_underscore", "  prj-espaces  "],
)
def test_un_identifiant_conforme_est_retenu_et_normalise(brut: str) -> None:
    assert projet_id_valide(brut) == brut.strip()


@pytest.mark.parametrize(
    "brut",
    [
        None,
        "",
        "   ",
        42,
        ["prj-liste"],
        "PRJ-MAJUSCULES",
        "-commence-par-un-tiret",
        "avec/slash",
        "avec\\antislash",
        "avec.point",
        "prj avec espace",
        "a" * (LONGUEUR_MAX_ID + 1),
    ],
)
def test_un_identifiant_douteux_vaut_aucun_projet(brut: object) -> None:
    """Ne lève jamais : faire échouer un run sur un identifiant mal formé coûterait plus cher."""
    assert projet_id_valide(brut) is None


def test_le_motif_interdit_toute_traversee_de_chemin() -> None:
    """L'identifiant sert de nom de fichier au dépôt (#221) : le garde-fou est là."""
    for evasion in ("../../etc/passwd", "..", "prj/../autre", "C:/Windows"):
        assert projet_id_valide(evasion) is None


# --- ② Le modèle et l'événement ---------------------------------------------


def test_la_tache_omet_la_cle_quand_elle_ne_releve_d_aucun_projet() -> None:
    """Un plan sans projet doit rester sérialisable tel quel (le schéma refuse `null`)."""
    tache = Task.from_dict(_tache("t1"))

    assert tache.projet_id is None
    assert "projet_id" not in tache.to_dict()


def test_la_tache_transporte_le_projet_en_aller_retour() -> None:
    tache = Task.from_dict(_tache("t1", projet_id=PROJET))

    assert tache.projet_id == PROJET
    assert tache.to_dict()["projet_id"] == PROJET
    assert Task.from_dict(tache.to_dict()).projet_id == PROJET


def test_un_projet_mal_forme_dans_un_plan_est_ecarte_sans_faire_echouer_le_plan() -> None:
    tache = Task.from_dict(_tache("t1", projet_id="../evasion"))

    assert tache.projet_id is None


def test_l_evenement_transporte_le_projet_et_le_normalise_au_retour() -> None:
    event = _evenement(tache_id="t1", projet_id=PROJET)

    assert event.to_dict()["projet_id"] == PROJET
    assert Event.from_dict(event.to_dict()).projet_id == PROJET
    assert Event.from_dict({**event.to_dict(), "projet_id": "../evasion"}).projet_id is None
    assert Event.from_dict({**event.to_dict(), "projet_id": None}).projet_id is None


# --- ③ La projection ---------------------------------------------------------


def test_la_tache_projetee_porte_le_projet_de_son_evenement() -> None:
    state = ControlTowerState()

    state.appliquer(_evenement(tache_id="t1", run_id="run-1", projet_id=PROJET))

    assert state.tache("t1").projet_id == PROJET
    assert state.tache("t1").to_dict()["projet_id"] == PROJET


def test_un_evenement_sans_projet_n_efface_pas_celui_deja_pose() -> None:
    """Inconnu ≠ absent : un événement muet sur le projet ne détache pas la tâche."""
    state = ControlTowerState()
    state.appliquer(_evenement(tache_id="t1", run_id="run-1", projet_id=PROJET))

    state.appliquer(_evenement(tache_id="t1", run_id="run-1", statut="terminee"))

    assert state.tache("t1").projet_id == PROJET


def test_le_run_herite_du_projet_de_sa_premiere_etape() -> None:
    """Un run publié hors de l'API n'émet aucun lancement : ses étapes font foi."""
    state = ControlTowerState()

    state.appliquer(_evenement(tache_id="t1", run_id="run-1", projet_id=PROJET))
    state.appliquer(_evenement(tache_id="t2", run_id="run-1", projet_id=AUTRE))

    # Le premier vu fait foi : un run n'appartient pas à deux projets.
    assert state.execution("run-1").projet_id == PROJET
    assert state.execution("run-1").resume()["projet_id"] == PROJET


def test_les_vues_de_la_projection_se_filtrent_par_projet() -> None:
    state = ControlTowerState()
    state.appliquer(_evenement(tache_id="t1", run_id="run-1", projet_id=PROJET))
    state.appliquer(_evenement(tache_id="t2", run_id="run-2", projet_id=AUTRE))
    state.appliquer(_evenement(tache_id="t3", run_id="run-3"))  # aucun projet

    assert [t.id for t in state.taches()] == ["t1", "t2", "t3"]
    assert [t.id for t in state.taches(PROJET)] == ["t1"]
    assert [e.run_id for e in state.executions(PROJET)] == ["run-1"]
    # Une tâche sans projet n'apparaît dans aucune vue filtrée : on ne devine pas.
    assert [t.id for t in state.taches(AUTRE)] == ["t2"]
    assert state.taches("prj-inconnu") == []


def test_le_grand_livre_recolle_le_projet_sur_les_lignes_de_tache() -> None:
    """Le projet n'a pas de coût : il arrive aussi sur des événements sans usage."""
    state = ControlTowerState()
    state.appliquer(_evenement(tache_id="t1", run_id="run-1", projet_id=PROJET))
    state.appliquer(
        _evenement(tache_id="t1", run_id="run-1", usage=StepUsage(cout_usd=0.5))
    )

    livre = state.execution("run-1").cout
    ligne = next(e for e in livre.taches if e.tache_id == "t1")

    assert ligne.projet_id == PROJET


# --- ④ Le moteur --------------------------------------------------------------


def test_le_run_fait_heriter_son_projet_a_chaque_tache_du_plan() -> None:
    journal = RunJournal()

    asyncio.run(_moteur(_plan_json(_tache("t1"), _tache("t2"))).run(
        "Objectif", journal=journal, projet_id=PROJET
    ))

    portes = {ligne.projet_id for ligne in journal.records if ligne.etape != "planification"}
    assert portes == {PROJET}


def test_une_tache_qui_porte_deja_un_projet_garde_le_sien() -> None:
    """Le plus précis gagne — même régime que la référence de ticket externe (#187)."""
    journal = RunJournal()
    plan = _plan_json(_tache("t1", projet_id=AUTRE), _tache("t2"))

    asyncio.run(_moteur(plan).run("Objectif", journal=journal, projet_id=PROJET))

    par_tache = {ligne.etape: ligne.projet_id for ligne in journal.records}
    assert par_tache["t1"] == AUTRE
    assert par_tache["t2"] == PROJET


def test_la_planification_est_une_depense_du_projet() -> None:
    """Sinon le total d'un projet et la somme de ses runs divergeraient (convention #57)."""
    journal = RunJournal()

    asyncio.run(_moteur(_plan_json(_tache("t1"))).run(
        "Objectif", journal=journal, projet_id=PROJET
    ))

    planification = [ligne for ligne in journal.records if ligne.etape == "planification"]
    assert planification
    assert all(ligne.projet_id == PROJET for ligne in planification)


def test_un_run_sans_projet_se_deroule_a_l_identique() -> None:
    """`None` est le comportement d'avant ce lot : rien ne doit en dépendre."""
    journal = RunJournal()

    rapport = asyncio.run(_moteur(_plan_json(_tache("t1"))).run("Objectif", journal=journal))

    assert len(rapport.reussies) == 1
    assert all(ligne.projet_id is None for ligne in journal.records)


# --- ⑤ Les vues filtrées de l'API --------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    """TestClient sur bus mémoire, avec deux projets et un travail sans projet."""
    state = ControlTowerState()
    state.appliquer(
        _evenement(
            tache_id="t1", run_id="run-1", projet_id=PROJET, usage=StepUsage(cout_usd=1.0)
        )
    )
    state.appliquer(
        _evenement(
            tache_id="t2", run_id="run-2", projet_id=AUTRE, usage=StepUsage(cout_usd=2.0)
        )
    )
    state.appliquer(_evenement(tache_id="t3", run_id="run-3", usage=StepUsage(cout_usd=4.0)))
    with TestClient(create_app(bus=InMemoryEventBus(), state=state)) as client:
        yield client


def test_le_kanban_sans_filtre_montre_tout_projets_confondus(client: TestClient) -> None:
    assert [t["id"] for t in client.get("/api/taches").json()] == ["t1", "t2", "t3"]


def test_le_kanban_filtre_ne_montre_que_les_taches_du_projet(client: TestClient) -> None:
    taches = client.get("/api/taches", params={"projet": PROJET}).json()

    assert [t["id"] for t in taches] == ["t1"]
    assert taches[0]["projet_id"] == PROJET


def test_un_filtre_vide_ne_filtre_rien(client: TestClient) -> None:
    """`?projet=` est la vue complète, exactement comme avant ce lot."""
    assert len(client.get("/api/taches", params={"projet": ""}).json()) == 3


def test_un_projet_inconnu_rend_une_vue_vide_et_non_une_erreur(client: TestClient) -> None:
    """Vide vaut mieux que faux — et une faute de frappe n'est pas une erreur d'API."""
    reponse = client.get("/api/taches", params={"projet": "prj-jamais-vu"})

    assert reponse.status_code == 200
    assert reponse.json() == []


def test_les_executions_se_filtrent_par_projet(client: TestClient) -> None:
    assert len(client.get("/api/executions").json()) == 3
    filtrees = client.get("/api/executions", params={"projet": PROJET}).json()
    assert [e["run_id"] for e in filtrees] == ["run-1"]
    assert filtrees[0]["projet_id"] == PROJET


def test_la_depense_d_un_projet_ignore_ce_qui_ne_lui_appartient_pas(
    client: TestClient,
) -> None:
    tout = client.get("/api/analytics/couts").json()
    du_projet = client.get("/api/analytics/couts", params={"projet": PROJET}).json()

    assert tout["projet"] is None
    assert tout["total"]["cout_usd"] == pytest.approx(7.0)
    assert du_projet["projet"] == PROJET
    assert du_projet["total"]["cout_usd"] == pytest.approx(1.0)


def test_un_travail_sans_projet_n_entre_dans_le_total_d_aucun() -> None:
    state = ControlTowerState()
    state.appliquer(
        _evenement(
            tache_id="t1", run_id="run-1", projet_id=PROJET, usage=StepUsage(cout_usd=1.0)
        )
    )
    state.appliquer(_evenement(tache_id="t3", run_id="run-1", usage=StepUsage(cout_usd=4.0)))

    agrege = agrege_couts(state.executions(), projet=PROJET)

    assert agrege.total.cout_usd == pytest.approx(1.0)
    assert [t.tache_id for t in agrege.taches] == ["t1"]


def test_le_lancement_d_un_run_accepte_un_projet_et_ecarte_un_identifiant_douteux(
    client: TestClient,
) -> None:
    """Un `projet_id` mal formé n'échoue pas le lancement : le run part sans projet."""
    corps = {"objectif": "Faire quelque chose", "projet_id": "../evasion"}

    reponse = client.post("/api/executions", json=corps)

    # Le lancement est accepté (202) : l'appartenance n'est pas une condition d'exécution.
    assert reponse.status_code == 202, reponse.text
