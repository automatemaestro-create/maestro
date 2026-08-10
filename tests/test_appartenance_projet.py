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
⑤ les **vues filtrées** — reprises par #277, qui remplace le filtre optionnel de
   #222 par un **contrat unique** (`maestro.controltower.portee`) : `?projet=`
   est obligatoire, vaut `<id>` | `tous` | `aucun`, et son absence est refusée
   (« rien plutôt qu'un mélange ») comme l'est un projet non déclaré. Le
   contrat de #222 que ces tests mesuraient — sans filtre, tout sort ; un
   identifiant inconnu rend une vue vide — a donc été **remplacé**, pas oublié ;
⑥ le **journal requêtable**, ajouté par le lot 6 de #276 (#282) : servi par les
   fixtures et par elles seules, il échappait aux tests d'au-dessus, qui
   tournent sur une projection réelle. C'est pourtant la vue où un mélange se
   voit le moins — un fil d'activité ne dit pas de quel projet il est le fil ;
⑦ le **contrat de portée pris pour lui-même** (#282) : jusqu'ici mesuré
   uniquement à travers l'API, donc jamais sur ses branches sans HTTP — le
   libellé rappelé dans les réponses, les mots réservés résolus sans dépôt, et
   le **dépôt injoignable**, qui accorde la portée plutôt que de ressortir un
   « projet inconnu » faux.

Ni réseau ni Redis : bus mémoire, fournisseurs factices, TestClient de Starlette,
dépôt de projets jetable (la portée exige des projets réellement déclarés).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

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
from maestro.controltower.fixtures import FixturesControlTower
from maestro.controltower.portee import PorteeProjet, PorteeRefusee, resoudre_portee
from maestro.controltower.projets import ServiceProjets
from maestro.engine import OrchestrationEngine
from maestro.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task
from maestro.projets import ProjetStore
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


def test_les_vues_de_la_projection_se_filtrent_par_portee() -> None:
    state = ControlTowerState()
    state.appliquer(_evenement(tache_id="t1", run_id="run-1", projet_id=PROJET))
    state.appliquer(_evenement(tache_id="t2", run_id="run-2", projet_id=AUTRE))
    state.appliquer(_evenement(tache_id="t3", run_id="run-3"))  # aucun projet

    assert [t.id for t in state.taches()] == ["t1", "t2", "t3"]
    assert [t.id for t in state.taches(PorteeProjet.tous())] == ["t1", "t2", "t3"]
    assert [t.id for t in state.taches(PorteeProjet.projet(PROJET))] == ["t1"]
    assert [e.run_id for e in state.executions(PorteeProjet.projet(PROJET))] == ["run-1"]
    # Une tâche sans projet n'apparaît dans aucune vue de projet : on ne devine
    # pas — et `aucun` (#277) est la seule vue qui la montre pour elle-même.
    assert [t.id for t in state.taches(PorteeProjet.projet(AUTRE))] == ["t2"]
    assert [t.id for t in state.taches(PorteeProjet.aucun())] == ["t3"]
    assert state.taches(PorteeProjet.projet("prj-inconnu")) == []


def test_une_validation_herite_du_projet_de_sa_tache() -> None:
    """`validation.demande` ne porte pas de projet : la projection le recolle (#277)."""
    state = ControlTowerState()
    state.appliquer(_evenement(tache_id="t1", run_id="run-1", projet_id=PROJET))
    state.appliquer(
        Event(type="validation.demande", tache_id="t1", agent="developpeur", titre="Publier")
    )

    assert state.validation("t1").projet_id == PROJET
    assert state.validation("t1").to_dict()["projet_id"] == PROJET
    assert [v.tache_id for v in state.validations(PorteeProjet.projet(PROJET))] == ["t1"]
    assert state.validations(PorteeProjet.projet(AUTRE)) == []


def test_une_validation_deposee_avant_que_la_tache_ait_son_projet_le_rattrape() -> None:
    """L'ordre des événements ne doit pas sortir une validation de son propre projet."""
    state = ControlTowerState()
    state.appliquer(
        Event(type="validation.demande", tache_id="t1", agent="developpeur", titre="Publier")
    )
    state.appliquer(_evenement(tache_id="t1", run_id="run-1", projet_id=PROJET))

    assert state.validation("t1").projet_id == PROJET


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
def maison(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier utilisateur factice — même raison qu'en #221/#223.

    Sous Windows le `tmp_path` de pytest vit dans `AppData/Local/Temp`, que la
    validation de racine refuse à raison : sans cette isolation, déclarer un
    projet échouerait pour une bonne raison, mais pas celle qu'on mesure ici.
    """
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


@pytest.fixture()
def projets(maison: Path, tmp_path: Path) -> ServiceProjets:
    """Deux projets réellement **déclarés** : la portée n'accepte qu'eux (#277)."""
    service = ServiceProjets(ProjetStore(tmp_path / "depot"))
    for nom in ("depensio", "autre"):
        (maison / nom).mkdir()
        service.creer(nom, str(maison / nom))
    return service


@pytest.fixture()
def ids(projets: ServiceProjets) -> tuple[str, str]:
    """Les identifiants **engendrés** des deux projets déclarés (`prj-<empreinte>`)."""
    declares = projets.lister()
    return declares[0]["id"], declares[1]["id"]


@pytest.fixture()
def client(projets: ServiceProjets, ids: tuple[str, str]) -> TestClient:
    """TestClient sur bus mémoire, avec deux projets et un travail sans projet."""
    projet, autre = ids
    state = ControlTowerState()
    state.appliquer(
        _evenement(
            tache_id="t1", run_id="run-1", projet_id=projet, usage=StepUsage(cout_usd=1.0)
        )
    )
    state.appliquer(
        _evenement(
            tache_id="t2", run_id="run-2", projet_id=autre, usage=StepUsage(cout_usd=2.0)
        )
    )
    state.appliquer(_evenement(tache_id="t3", run_id="run-3", usage=StepUsage(cout_usd=4.0)))
    with TestClient(
        create_app(bus=InMemoryEventBus(), state=state, projets=projets)
    ) as client:
        yield client


@pytest.mark.parametrize(
    "route",
    ["/api/taches", "/api/executions", "/api/analytics/couts", "/api/validations"],
)
def test_une_lecture_sans_projet_est_refusee_avec_son_motif(
    client: TestClient, route: str
) -> None:
    """« Rien plutôt qu'un mélange » (#277) : l'API ne devine plus le périmètre.

    Un refus se diagnostique ; une liste vide se confondrait avec un projet sans
    activité, et la vue transverse d'avant se confondait avec un seul projet.
    """
    reponse = client.get(route)

    assert reponse.status_code == 422, reponse.text
    assert reponse.json()["detail"]["motif"] == "projet-requis"


def test_un_projet_inconnu_est_refuse_comme_le_refuse_le_service_des_projets(
    client: TestClient,
) -> None:
    """404 motivé, par la même porte que `GET /api/projets/{id}` — pas une vue vide."""
    reponse = client.get("/api/taches", params={"projet": "prj-jamais-vu"})

    assert reponse.status_code == 404
    assert reponse.json()["detail"]["motif"] == "projet-inconnu"


def test_un_identifiant_mal_forme_est_refuse_comme_un_projet_inconnu(
    client: TestClient,
) -> None:
    """Il ne peut désigner aucun projet du dépôt (#221) : même refus, même code."""
    reponse = client.get("/api/taches", params={"projet": "../evasion"})

    assert reponse.status_code == 404
    assert reponse.json()["detail"]["motif"] == "projet-inconnu"


def test_le_kanban_transverse_se_demande_explicitement(client: TestClient) -> None:
    """`?projet=tous` rend ce que rendait l'absence de paramètre — en le disant."""
    taches = client.get("/api/taches", params={"projet": "tous"}).json()

    assert [t["id"] for t in taches] == ["t1", "t2", "t3"]


def test_le_kanban_d_un_projet_ne_montre_que_ses_taches(
    client: TestClient, ids: tuple[str, str]
) -> None:
    projet, _ = ids
    taches = client.get("/api/taches", params={"projet": projet}).json()

    assert [t["id"] for t in taches] == ["t1"]
    assert taches[0]["projet_id"] == projet


def test_le_kanban_hors_projet_montre_ce_qui_echappe_au_cadre(client: TestClient) -> None:
    """`?projet=aucun` (#277) : la vue qu'aucun paramètre ne permettait d'atteindre."""
    taches = client.get("/api/taches", params={"projet": "aucun"}).json()

    assert [t["id"] for t in taches] == ["t3"]
    assert taches[0]["projet_id"] is None


def test_les_executions_se_filtrent_par_projet(
    client: TestClient, ids: tuple[str, str]
) -> None:
    projet, _ = ids
    assert len(client.get("/api/executions", params={"projet": "tous"}).json()) == 3
    filtrees = client.get("/api/executions", params={"projet": projet}).json()
    assert [e["run_id"] for e in filtrees] == ["run-1"]
    assert filtrees[0]["projet_id"] == projet


def test_la_depense_d_un_projet_ignore_ce_qui_ne_lui_appartient_pas(
    client: TestClient, ids: tuple[str, str]
) -> None:
    projet, _ = ids
    tout = client.get("/api/analytics/couts", params={"projet": "tous"}).json()
    du_projet = client.get("/api/analytics/couts", params={"projet": projet}).json()

    assert tout["projet"] is None
    assert tout["portee"] == "tous"
    assert tout["total"]["cout_usd"] == pytest.approx(7.0)
    assert du_projet["projet"] == projet
    assert du_projet["portee"] == projet
    assert du_projet["total"]["cout_usd"] == pytest.approx(1.0)


def test_un_travail_sans_projet_n_entre_dans_le_total_d_aucun() -> None:
    state = ControlTowerState()
    state.appliquer(
        _evenement(
            tache_id="t1", run_id="run-1", projet_id=PROJET, usage=StepUsage(cout_usd=1.0)
        )
    )
    state.appliquer(_evenement(tache_id="t3", run_id="run-1", usage=StepUsage(cout_usd=4.0)))

    agrege = agrege_couts(state.executions(), portee=PorteeProjet.projet(PROJET))

    assert agrege.total.cout_usd == pytest.approx(1.0)
    assert [t.tache_id for t in agrege.taches] == ["t1"]


def test_le_flux_temps_reel_ne_livre_pas_l_evenement_d_un_autre_projet(
    client: TestClient, ids: tuple[str, str]
) -> None:
    """Troisième critère #277 : le WebSocket suit exactement la règle du REST."""
    projet, autre = ids
    with client.websocket_connect(f"/ws/evenements?projet={projet}") as socket:
        client.post("/api/taches/t2/reassigner", json={"agent": "developpeur"})
        client.post("/api/taches/t1/reassigner", json={"agent": "developpeur"})

        # t2 appartient à `autre` : la socket ne doit voir que t1, sans quoi
        # elle bloquerait ici sur l'événement suivant plutôt que de le sauter.
        recu = socket.receive_json()

    assert recu["tache_id"] == "t1"
    assert recu["projet_id"] == projet
    assert autre != projet


def test_le_flux_temps_reel_refuse_une_portee_absente_en_le_disant(
    client: TestClient,
) -> None:
    """Le motif part **sur la socket** : un échec de poignée de main serait muet."""
    with client.websocket_connect("/ws/evenements") as socket:
        assert socket.receive_json()["erreur"]["motif"] == "projet-requis"


def test_le_lancement_d_un_run_accepte_un_projet_et_ecarte_un_identifiant_douteux(
    client: TestClient,
) -> None:
    """Un `projet_id` mal formé n'échoue pas le lancement : le run part sans projet."""
    corps = {"objectif": "Faire quelque chose", "projet_id": "../evasion"}

    reponse = client.post("/api/executions", json=corps)

    # Le lancement est accepté (202) : l'appartenance n'est pas une condition d'exécution.
    assert reponse.status_code == 202, reponse.text


def test_les_validations_d_un_projet_ne_montrent_que_les_siennes(
    projets: ServiceProjets, ids: tuple[str, str]
) -> None:
    """La file de validation est cadrée comme le reste — pas seulement son refus.

    Les tests d'au-dessus vérifient qu'une lecture **sans** projet est refusée
    sur cette route ; celui-ci vérifie ce qu'elle rend **avec**. La distinction
    compte : une validation est une demande d'action sur le projet de quelqu'un,
    et la voir depuis un autre projet serait la faute la plus coûteuse de la
    vague — on décide sur ce qu'on voit.
    """
    projet, autre = ids
    state = ControlTowerState()
    state.appliquer(_evenement(tache_id="t1", run_id="run-1", projet_id=projet))
    state.appliquer(_evenement(tache_id="t2", run_id="run-2", projet_id=autre))
    for tache in ("t1", "t2"):
        state.appliquer(
            Event(type="validation.demande", tache_id=tache, agent="devops", titre="Publier")
        )

    with TestClient(
        create_app(bus=InMemoryEventBus(), state=state, projets=projets)
    ) as client:
        vues = client.get("/api/validations", params={"projet": projet}).json()

    assert [v["tache_id"] for v in vues] == ["t1"]
    assert vues[0]["projet_id"] == projet


# --- ⑥ Le journal requêtable, cadré comme les autres vues (#277) --------------
#
# Servi par les **fixtures** et par elles seules (`maestro.controltower.fixtures`),
# le journal échappe aux tests d'au-dessus, qui tournent sur une projection
# réelle : sans fixtures il répond 501 avant même de regarder la portée. C'est
# pourtant la vue la plus exposée au mélange — un fil d'activité qui montre le
# travail d'un autre projet se lit « il se passe quelque chose ici ».


@pytest.fixture()
def client_journal(projets: ServiceProjets) -> TestClient:
    """App branchée sur les fixtures : la seule où `/api/journal` est servi."""
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=projets,
        fixtures=FixturesControlTower(),
    )
    with TestClient(app) as client:
        yield client


def test_le_journal_refuse_lui_aussi_une_lecture_sans_projet(
    client_journal: TestClient,
) -> None:
    """Même contrat, même motif — la gate 501 passe avant, mais elle est franchie ici."""
    reponse = client_journal.get("/api/journal")

    assert reponse.status_code == 422
    assert reponse.json()["detail"]["motif"] == "projet-requis"


def test_le_journal_hors_projet_est_vide_la_ou_le_transverse_est_plein(
    client_journal: TestClient,
) -> None:
    """`tous` et `aucun` sur le même jeu : la portée est bien appliquée aux entrées.

    Toutes les entrées du scénario de démo appartiennent à un projet, donc
    `aucun` doit rendre une page **vide mais valide** — pas un refus, pas la
    liste entière. C'est la différence que #277 a rendue observable.
    """
    transverse = client_journal.get("/api/journal", params={"projet": "tous"}).json()
    hors_projet = client_journal.get("/api/journal", params={"projet": "aucun"}).json()

    assert transverse["total"] > 0
    assert hors_projet["total"] == 0
    assert hors_projet["entrees"] == []


def test_le_journal_filtre_ses_entrees_sur_la_portee_demandee() -> None:
    """La règle elle-même, mesurée sans HTTP : `portee.retient` décide, entrée par entrée.

    Le passer par l'API demanderait de **déclarer** le projet de la démo dans le
    dépôt (la portée n'accepte qu'un projet existant), c'est-à-dire de faire
    dépendre le test d'un identifiant engendré — alors que ce qu'on mesure est
    le filtre, pas la résolution.
    """
    fixtures = FixturesControlTower()
    demo = fixtures.journal(portee=PorteeProjet.tous())["entrees"][0]["projet_id"]

    du_projet = fixtures.journal(portee=PorteeProjet.projet(demo))
    d_un_autre = fixtures.journal(portee=PorteeProjet.projet(AUTRE))

    assert demo is not None
    assert du_projet["total"] == fixtures.journal(portee=PorteeProjet.tous())["total"]
    assert d_un_autre["total"] == 0


# --- ⑦ Le contrat de portée pris pour lui-même (#277) -------------------------


def test_la_portee_rappelle_ce_qui_a_ete_demande() -> None:
    """`libelle` est ce que les réponses renvoient : la portée servie, jamais déduite.

    Sans lui, un client devrait deviner le périmètre d'une réponse à partir de
    son contenu — et une liste vide ne dit rien du périmètre qui l'a produite.
    """
    assert PorteeProjet.tous().libelle == "tous"
    assert PorteeProjet.aucun().libelle == "aucun"
    assert PorteeProjet.projet(PROJET).libelle == PROJET


def test_les_mots_reserves_ne_consultent_pas_le_depot() -> None:
    """`tous` et `aucun` sont résolus sans dépôt : aucun projet ne peut les masquer."""

    class _DepotQuiDitNon:
        def existe(self, _identifiant: str) -> bool:
            return False

    depot = _DepotQuiDitNon()

    assert resoudre_portee("tous", projet_connu=depot).transverse is True
    assert resoudre_portee("aucun", projet_connu=depot).projet_id is None


@pytest.mark.parametrize("brut", [None, "", "   "])
def test_une_portee_vide_est_refusee_et_le_message_donne_les_trois_formes(
    brut: str | None,
) -> None:
    """Le refus doit être **actionnable** : il nomme les trois valeurs acceptées."""
    with pytest.raises(PorteeRefusee) as capture:
        resoudre_portee(brut)

    assert capture.value.motif == "projet-requis"
    assert "tous" in str(capture.value) and "aucun" in str(capture.value)


def test_un_depot_injoignable_n_invente_pas_un_projet_inconnu() -> None:
    """Un incident de stockage ne doit pas ressortir en diagnostic faux.

    Refuser ici dirait « ce projet n'existe pas » d'un projet qui existe
    peut-être : la vue est accordée sur la seule forme de l'identifiant et
    ressortira vide si rien ne lui correspond, ce qui est vrai et vérifiable.
    """

    class _DepotEnPanne:
        def existe(self, _identifiant: str) -> bool:
            raise OSError("dépôt illisible")

    portee = resoudre_portee(PROJET, projet_connu=_DepotEnPanne())

    assert portee.projet_id == PROJET


def test_un_identifiant_mal_forme_est_refuse_meme_sans_depot_pour_le_dire() -> None:
    """Il ne peut désigner aucun projet (#221) : le refus ne dépend d'aucune lecture."""
    with pytest.raises(PorteeRefusee) as capture:
        resoudre_portee("../evasion")

    assert capture.value.motif == "projet-inconnu"
