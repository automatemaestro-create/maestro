"""Le **graphe d'un run** : nœuds, arêtes, branches parallèles (#490, lot 2 de
#488 ; couvert ici par #492).

Le graphe existait dans le moteur et nulle part ailleurs : `Task.dependances`
portait les arêtes, l'exécution les respectait, le parallélisme était réel — et
l'API rendait des tâches **à plat**. Le lot 2 a ouvert le chemin ; les tests
étaient différés au lot final ([docs/10 §5.1](../docs/10-workflow-git.md)), les
voici.

Cinq volets, dans l'ordre où la donnée remonte — du plan jusqu'à la route :

① **La forme transportée** (`maestro.plan_run`) — module feuille, relu et jamais
   revalidé : un plan venu du bus ou d'un journal rejoué doit rester lisible même
   fautif. Ce qui n'apprend rien est écarté, le reste passe.

② **La composition** (`maestro.controltower.graphe`) — le cœur du lot, et les
   deux cas que le ticket #492 nomme en toutes lettres : un plan **sans aucune
   dépendance** (graphe plat, tous les nœuds au niveau 0) et **deux tâches
   indépendantes**, qui doivent tomber au **même** niveau. Le niveau est le *plus
   long chemin* qui mène au nœud — un tri topologique ordinaire les aurait mises
   l'une après l'autre, vrai comme séquence, faux comme dessin.

③ **Le transport** — `StepRecord.plan` → `run.plan` → `EtatExecution.plan`. Le
   plan voyage **entier et une seule fois**, sur l'étape `planification` ; il
   **double** l'`agent.activite` de cette étape sans la remplacer.

④ **La route** — `GET /api/executions/{run_id}/graphe`, son 404, et les trois
   lectures qui ne se confondent pas : plan plat, plan inconnu (`plan_connu:
   false`), run sans tâche.

⑤ **Deux branches réellement simultanées** — le second cas du ticket, sur le
   **vrai moteur**. La simultanéité se prouve par une **barrière** et jamais par
   un `sleep` (la règle du dépôt, #292) : sans elle, « deux tâches en parallèle »
   est une course que la charge de la machine tranche, et le test dit tantôt le
   code, tantôt l'ordonnancement du système. Ici la barrière est le **contrat** :
   si le moteur sérialisait les deux branches, aucune des deux ne la lèverait et
   le run n'irait jamais à son terme.

**Ni Redis, ni réseau, ni appel modèle** : l'app est la vraie (`create_app`) sur
bus mémoire, alimentée par des événements posés à la main — ce que la pompe lui
livre en production —, et le moteur du volet ⑤ tourne sur des fournisseurs
factices.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import (
    EVENEMENT_RUN_PLAN,
    ControlTowerState,
    Event,
    InMemoryEventBus,
    InMemoryEventLog,
    create_app,
)
from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.events import EVENEMENT_AGENT_ACTIVITE, EVENEMENT_TACHE_DETAIL
from maestro.controltower.graphe import (
    ARETE_ATTENDUE,
    ARETE_FRANCHIE,
    ARETE_ROMPUE,
    EtatNoeud,
    graphe_du_run,
)
from maestro.controltower.progression import A_FAIRE, STATUT_BACKLOG, TERMINEES
from maestro.controltower.state import (
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_TACHE_STATUT,
    EXECUTION_EN_COURS,
)
from maestro.detail_tache import ETAPE_A_FAIRE, ETAPE_FAITE, EtapeTache
from maestro.engine.loop import OrchestrationEngine
from maestro.orchestrator.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task
from maestro.plan_run import (
    NoeudPlan,
    dependants_directs,
    noeuds_depuis,
    noeuds_du_plan,
    noeuds_en_liste,
)
from maestro.providers.base import ModelProvider
from maestro.telemetry.journal import RunJournal
from maestro.telemetry.usage import StepUsage

RUN = "run-graphe"
PROJET = "prj-0001"


# ------------------------------------------------------------------ harnais


def _noeud(identifiant: str, *dependances: str, titre: str = "", etapes=()) -> NoeudPlan:
    return NoeudPlan(
        id=identifiant,
        titre=titre or f"Tâche {identifiant}",
        dependances=tuple(dependances),
        etapes=tuple(etapes),
    )


def _graphe(noeuds, etats=None, **kwargs):
    return graphe_du_run(RUN, noeuds, etats or {}, **kwargs)


def _niveau_de(graphe) -> dict[str, int]:
    return {noeud.id: noeud.niveau for noeud in graphe.noeuds}


def tache(tache_id: str, statut: str, *, run_id: str = RUN) -> Event:
    """L'événement qui fait exister une tâche dans la projection — et dans un run."""
    return Event(
        type=EVENEMENT_TACHE_STATUT,
        run_id=run_id,
        tache_id=tache_id,
        titre=f"Tâche {tache_id}",
        agent="developpeur",
        role="Développeur",
        statut=statut,
        projet_id=PROJET,
    )


def lancement(run_id: str = RUN) -> Event:
    return Event(
        type=EVENEMENT_EXECUTION_STATUT,
        run_id=run_id,
        statut=EXECUTION_EN_COURS,
        titre="Objectif",
        projet_id=PROJET,
    )


def plan_publie(*noeuds: NoeudPlan, run_id: str = RUN) -> Event:
    """L'événement `run.plan` — publié une fois, à la décomposition."""
    return Event(type=EVENEMENT_RUN_PLAN, run_id=run_id, plan=list(noeuds), projet_id=PROJET)


def projection(*evenements: Event) -> ControlTowerState:
    state = ControlTowerState()
    for event in evenements:
        state.appliquer(event)
    return state


def client_sur(*evenements: Event) -> TestClient:
    """L'app réelle, bus mémoire, historique rejoué par le lifespan."""
    log = InMemoryEventLog()
    for event in evenements:
        asyncio.run(log.consigner(event))
    return TestClient(
        create_app(bus=InMemoryEventBus(), state=ControlTowerState(), event_log=log)
    )


# ------------------------------- ① La forme transportée : rien ne s'invente, rien ne se refuse


def test_le_noeud_fait_l_aller_retour():
    noeud = _noeud("api", "schema", titre="API CRUD", etapes=("Écrire les routes",))

    assert NoeudPlan.from_dict(noeud.to_dict()) == noeud


def test_un_noeud_sans_identifiant_est_ecarte():
    """Le titre seul ne fait pas un nœud : aucune arête ne pourrait le désigner,
    aucun état de tâche s'y rattacher — il ne resterait qu'une étiquette."""
    assert NoeudPlan.depuis({"titre": "Sans identité"}) is None
    assert noeuds_depuis([{"titre": "A"}, {"id": "b"}]) == [NoeudPlan(id="b")]


def test_un_identifiant_vu_deux_fois_ne_compte_qu_une_fois():
    """Un nœud dédoublé dessinerait deux boîtes pour une seule tâche."""
    lus = noeuds_depuis([{"id": "a", "titre": "Premier"}, {"id": "a", "titre": "Second"}])

    assert [noeud.titre for noeud in lus] == ["Premier"]


def test_une_chaine_de_dependances_n_est_pas_iteree_caractere_par_caractere():
    """Sinon un plan mal formé rendrait autant de fausses arêtes que de lettres."""
    assert NoeudPlan(id="a", dependances="bcd").valide().dependances == ()


def test_les_dependances_sont_dedoublonnees_dans_l_ordre_du_plan():
    assert _noeud("c", "a", "b", "a").valide().dependances == ("a", "b")


def test_l_ordre_du_plan_est_celui_du_flux():
    """Un graphe se lit dans l'ordre où il a été écrit, pas trié."""
    lus = noeuds_depuis([{"id": "z"}, {"id": "a"}, {"id": "m"}])

    assert [noeud.id for noeud in lus] == ["z", "a", "m"]


@pytest.mark.parametrize("brut", [None, "plan", 42, {"id": "a"}])
def test_une_valeur_qui_n_est_pas_une_liste_rend_une_liste_vide(brut):
    assert noeuds_depuis(brut) == []


def test_le_titre_est_normalise_et_borne():
    noeud = NoeudPlan(id="a", titre="  Un   titre\nsur deux lignes  ").valide()

    assert noeud.titre == "Un titre sur deux lignes"


def test_la_table_inverse_dit_qui_attend_quoi():
    """Le pendant lisible du `_dependants_directs` de la boucle, sur la forme
    transportée — même table, même ordre."""
    aval = dependants_directs([_noeud("schema"), _noeud("api", "schema"), _noeud("ui", "schema")])

    assert aval == {"schema": ("api", "ui"), "api": (), "ui": ()}


def test_une_dependance_sans_amont_connu_ne_cree_pas_de_cle_fantome():
    """La relecture est tolérante : une arête sans amont n'a rien à dessiner."""
    aval = dependants_directs([_noeud("api", "disparu")])

    assert aval == {"api": ()}


def test_le_plan_du_moteur_ne_transporte_que_ce_que_le_plan_sait():
    """Ni agent, ni statut, ni coût : rien de tout cela n'existe quand le plan est
    écrit. L'agent est routé au démarrage, le reste se mesure en travaillant."""
    tasks = [
        Task(
            id="schema", titre="Schéma SQL", description="d", competences_requises=("sql",),
            format_sortie="SQL", etapes=("Lister les entités",),
        ),
        Task(
            id="api", titre="API", description="d", competences_requises=("backend",),
            format_sortie="Module", dependances=("schema",),
        ),
    ]

    noeuds = noeuds_du_plan(tasks)

    assert [noeud.id for noeud in noeuds] == ["schema", "api"]
    assert noeuds[0].etapes == ("Lister les entités",)
    assert noeuds[1].dependances == ("schema",)
    assert set(noeuds_en_liste(noeuds)[0]) == {"id", "titre", "dependances", "etapes"}


# --------------------------- ② La composition : les branches parallèles sont les niveaux


def test_deux_taches_independantes_tombent_au_meme_niveau():
    """**Le** critère du lot : elles ne doivent pas paraître séquentielles.

    C'est un fait du moteur — chaque tâche n'attend que ses propres dépendances,
    le parallélisme est borné par un sémaphore et jamais par un ordre. Le volet ⑤
    le prouve sur le moteur lui-même ; ici on garde le **dessin**.
    """
    graphe = _graphe([_noeud("schema"), _noeud("api", "schema"), _noeud("ui", "schema")])

    assert _niveau_de(graphe) == {"schema": 0, "api": 1, "ui": 1}
    assert graphe.niveaux == (("schema",), ("api", "ui"))
    assert graphe.largeur == 2


def test_le_niveau_est_le_plus_long_chemin_et_non_un_rang_de_tri():
    """Le nœud de convergence suit son amont **le plus tardif**.

    Un tri topologique ordinaire aurait pu poser `ui` au niveau 1 et `livraison`
    au niveau 2, alors que `livraison` attend `api`, qui est au niveau 2 : le
    dessin aurait montré une flèche remontant une colonne.
    """
    graphe = _graphe(
        [
            _noeud("schema"),
            _noeud("api", "schema"),
            _noeud("ui"),
            _noeud("livraison", "api", "ui"),
        ]
    )

    assert _niveau_de(graphe) == {"schema": 0, "api": 1, "ui": 0, "livraison": 2}


def test_un_plan_sans_aucune_dependance_rend_un_graphe_plat_et_le_dit():
    """Le cas **le plus courant**, et une lecture juste : tout peut partir en même
    temps. À ne pas confondre avec un run sans tâche, que `nb_noeuds` distingue."""
    graphe = _graphe([_noeud("a"), _noeud("b"), _noeud("c")])

    assert graphe.plat
    assert graphe.nb_noeuds == 3
    assert graphe.niveaux == (("a", "b", "c"),)
    assert graphe.profondeur == 1
    assert graphe.largeur == 3


def test_un_run_sans_tache_est_plat_lui_aussi_mais_sans_aucun_noeud():
    graphe = _graphe([])

    assert graphe.plat
    assert graphe.nb_noeuds == 0
    assert graphe.profondeur == 0
    assert graphe.largeur == 0


def test_le_rang_range_les_noeuds_dans_leur_niveau_dans_l_ordre_du_plan():
    """De quoi poser la boîte sans rien recalculer côté client."""
    graphe = _graphe([_noeud("schema"), _noeud("ui", "schema"), _noeud("api", "schema")])

    rangs = {noeud.id: noeud.rang for noeud in graphe.noeuds}
    assert rangs == {"schema": 0, "ui": 0, "api": 1}


def test_l_arete_va_de_l_amont_vers_l_aval_et_non_dans_le_sens_de_la_declaration():
    """`Task.dependances` se lit « j'attends ceux-ci », un dessin « ceci mène à
    cela ». Prendre le sens de la déclaration ferait des flèches à rebours."""
    (arete,) = _graphe([_noeud("schema"), _noeud("api", "schema")]).aretes

    assert (arete.de, arete.vers) == ("schema", "api")


@pytest.mark.parametrize(
    ("statut_amont", "attendu"),
    [
        ("terminee", ARETE_FRANCHIE),
        ("echec", ARETE_ROMPUE),
        ("bloquee", ARETE_ROMPUE),
        ("en_cours", ARETE_ATTENDUE),
        ("backlog", ARETE_ATTENDUE),
        ("en_attente_validation", ARETE_ATTENDUE),
        # Un statut que la table partagée ignore tombe dans `autres`, donc dans
        # « attendue » : ne rien affirmer plutôt qu'affirmer de travers.
        ("teleporte", ARETE_ATTENDUE),
    ],
)
def test_l_arete_se_lit_dans_le_statut_de_son_amont(statut_amont, attendu):
    """Et non dans le message de `relais.annonce`, qui dit pourtant la même chose :
    le relais n'existe que si une messagerie est injectée, et la Control Tower
    lance ses runs sans. S'y brancher aurait laissé **toutes** les arêtes
    éteintes — le défaut exact que #488 a nommé chez `consigne_detail`."""
    graphe = _graphe(
        [_noeud("schema"), _noeud("api", "schema")],
        {"schema": EtatNoeud(statut=statut_amont)},
    )

    assert graphe.aretes[0].etat == attendu


def test_une_dependance_qui_ne_designe_aucun_noeud_est_declaree_mais_pas_dessinee():
    """Taire une déclaration ferait croire à un plan plus simple qu'il n'est ; la
    dessiner tracerait une flèche partant de nulle part."""
    graphe = _graphe([_noeud("api", "disparu")])

    assert graphe.noeuds[0].dependances == ("disparu",)
    assert graphe.aretes == ()


def test_un_noeud_que_la_projection_ignore_est_declare_pas_encore_pris():
    """`backlog` et non la chaîne vide : la machine à états a un mot pour
    « déclarée, pas encore prise ».

    Laisser vide le ferait tomber dans `autres`, et la moitié d'un graphe qui n'a
    pas commencé se dessinerait en « statut inconnu » alors que rien ne l'est.
    """
    (noeud,) = _graphe([_noeud("api")]).noeuds

    assert noeud.statut == STATUT_BACKLOG
    assert noeud.compartiment == A_FAIRE


def test_la_checklist_du_noeud_est_celle_de_l_agent_sinon_l_ossature_du_plan():
    """L'arbitrage de #489, un cran plus haut : le plan rend la tâche lisible
    **avant** qu'elle démarre, l'agent dit la vérité **pendant** qu'elle tourne."""
    graphe = _graphe(
        [_noeud("schema", etapes=("Lister les entités",)), _noeud("api", etapes=("Routes",))],
        {"schema": EtatNoeud(etapes=(EtapeTache(libelle="Relire le modèle", etat=ETAPE_FAITE),))},
    )

    par_id = {noeud.id: noeud for noeud in graphe.noeuds}
    assert [(e.libelle, e.etat) for e in par_id["schema"].etapes] == [
        ("Relire le modèle", ETAPE_FAITE)
    ]
    # Pas démarrée : l'ossature du plan, tout entière « à faire ».
    assert [(e.libelle, e.etat) for e in par_id["api"].etapes] == [("Routes", ETAPE_A_FAIRE)]


def test_le_graphe_sert_ce_que_le_front_n_a_pas_a_recalculer():
    """Un client qui déduirait les niveaux réécrirait un tri topologique en
    TypeScript, sur les seuls nœuds qu'il a chargés."""
    rendu = _graphe(
        [_noeud("schema"), _noeud("api", "schema"), _noeud("ui", "schema")],
        {"schema": EtatNoeud(statut="terminee", agent="bdd", cout_usd=0.02, duree_ms=1234)},
    ).to_dict()

    assert rendu["plat"] is False
    assert (rendu["nb_noeuds"], rendu["nb_aretes"]) == (3, 2)
    assert (rendu["profondeur"], rendu["largeur"]) == (2, 2)
    assert rendu["niveaux"] == [["schema"], ["api", "ui"]]
    premier = rendu["noeuds"][0]
    assert (premier["niveau"], premier["rang"]) == (0, 0)
    assert premier["compartiment"] == TERMINEES
    assert (premier["cout_usd"], premier["duree_ms"]) == (0.02, 1234)


def test_une_duree_inconnue_reste_nulle_et_ne_devient_pas_zero():
    """Une boîte annoncée à « 0 ms » se lirait comme instantanée."""
    (noeud,) = _graphe([_noeud("api")]).noeuds

    assert noeud.duree_ms is None
    assert noeud.cout_usd is None


def test_un_cycle_relu_du_bus_rend_un_graphe_etrange_plutot_que_rien():
    """Un plan validé n'en porte pas (`validate_plan` le refuse), mais un plan relu
    du bus ne repasse par aucune validation : rendre un graphe étrange vaut mieux
    que faire tourner une boucle sans fin."""
    graphe = _graphe([_noeud("a", "b"), _noeud("b", "a")])

    assert graphe.nb_noeuds == 2
    assert set(_niveau_de(graphe).values()) == {0}


def test_composer_le_graphe_d_un_run_inconnu_ne_leve_pas():
    """La projection répond à ce qu'elle sait ; le refus motivé est le rôle des
    routes."""
    assert _graphe([]).run_id == RUN


# ------------------------------- ③ Le transport : entier, une fois, et sans rien remplacer


def test_le_plan_voyage_sur_la_seule_etape_de_planification():
    """C'est l'instant où le plan existe et où il est **figé**."""
    journal = RunJournal(run_id=RUN)
    journal.consigne(
        etape="planification", nom="", agent="orchestrateur", role="", statut="",
        entree="", sortie="2 tâche(s) planifiée(s)", usage=StepUsage(),
        plan=[_noeud("schema"), _noeud("api", "schema")],
    )
    journal.consigne(
        etape="schema", nom="Schéma", agent="bdd", role="", statut="terminee",
        entree="", sortie="fait", usage=StepUsage(),
    )

    lignes = [record.to_dict() for record in journal.records]
    assert lignes[0]["plan"] == noeuds_en_liste([_noeud("schema"), _noeud("api", "schema")])
    # `null` et non `[]` partout ailleurs : une ligne ordinaire ne parle pas du
    # plan, donc la projection ne devra toucher à rien.
    assert lignes[1]["plan"] is None


def test_la_planification_produit_deux_evenements_et_non_un():
    """Le `run.plan` **double** l'`agent.activite` de la planification, il ne le
    remplace pas : ce que le cadrage a **coûté** et ce qu'il a **décidé** sont
    deux faits."""
    journal = RunJournal(run_id=RUN)
    journal.consigne(
        etape="planification", nom="", agent="orchestrateur", role="", statut="",
        entree="", sortie="2 tâche(s) planifiée(s)", usage=StepUsage(),
        plan=[_noeud("schema"), _noeud("api", "schema")],
    )

    activite, plan = evenements_depuis_step(journal.records[0].to_dict())

    assert activite.type == EVENEMENT_AGENT_ACTIVITE
    assert plan.type == EVENEMENT_RUN_PLAN
    # Sur le run entier, jamais sur une tâche : un `tache_id` ferait naître une
    # carte fantôme au Kanban.
    assert plan.tache_id == ""
    assert plan.detail == "2 tâche(s), 1 enchaînement(s)"
    assert [noeud.id for noeud in plan.plan or []] == ["schema", "api"]


def test_une_planification_sans_plan_ne_publie_qu_un_evenement():
    """Ligne d'échec, ou journal antérieur au lot : il n'y a pas de graphe à
    annoncer, et un graphe sans nœud remplacerait un plan déjà posé par rien."""
    evenements = evenements_depuis_step(
        {"run_id": RUN, "etape": "planification", "sortie": "échec de la décomposition"}
    )

    assert len(evenements) == 1


def test_l_evenement_de_plan_fait_l_aller_retour_json():
    """Ce qui traverse le bus doit revenir identique — le rejeu en dépend."""
    event = plan_publie(_noeud("schema"), _noeud("api", "schema", etapes=("Routes",)))

    relu = Event.from_dict(json.loads(json.dumps(event.to_dict(), ensure_ascii=False)))

    assert relu.plan == event.plan


def test_une_ligne_ordinaire_ne_porte_aucun_plan():
    (event,) = evenements_depuis_step({"run_id": RUN, "etape": "t1", "statut": "en_cours"})

    assert event.plan is None


def test_le_plan_se_pose_sur_l_execution_sans_toucher_a_ses_taches():
    """Le plan annonce ce qui *sera* fait ; les tâches d'un run restent celles que
    ses événements ont réellement portées. Les confondre ferait diverger
    `progression.total` de `nb_taches` pendant tout le run."""
    state = projection(lancement(), plan_publie(_noeud("schema"), _noeud("api", "schema")))

    execution = state.execution(RUN)
    assert execution is not None
    assert [noeud.id for noeud in execution.plan] == ["schema", "api"]
    assert execution.nb_taches == 0
    assert state.taches_du_run(RUN) == frozenset()


def test_poser_deux_fois_le_meme_plan_est_sans_effet():
    """Idempotent, donc rejouable : le journal durable reconstruit le graphe à
    l'identique au redémarrage de l'API."""
    plan = plan_publie(_noeud("schema"), _noeud("api", "schema"))
    state = projection(lancement(), plan, Event.from_dict(plan.to_dict()))

    execution = state.execution(RUN)
    assert execution is not None
    assert [noeud.id for noeud in execution.plan] == ["schema", "api"]


def test_le_flot_ordinaire_des_statuts_n_efface_pas_le_plan_pose():
    state = projection(
        lancement(),
        plan_publie(_noeud("schema"), _noeud("api", "schema")),
        tache("schema", "terminee"),
        tache("api", "en_cours"),
    )

    execution = state.execution(RUN)
    assert execution is not None
    assert len(execution.plan) == 2


def test_le_graphe_de_la_projection_joint_le_plan_et_l_etat_des_taches():
    """La jointure des deux moitiés, chacune lue là où elle fait autorité — si
    bien que le graphe, le Kanban et la barre de progression d'un même écran ne
    peuvent pas se contredire."""
    state = projection(
        lancement(),
        plan_publie(_noeud("schema"), _noeud("api", "schema")),
        tache("schema", "terminee"),
        Event(
            type=EVENEMENT_TACHE_DETAIL,
            run_id=RUN,
            tache_id="schema",
            etapes=[EtapeTache(libelle="Lister les entités", etat=ETAPE_FAITE)],
        ),
    )

    graphe = state.graphe(RUN)

    assert graphe.plan_connu
    par_id = {noeud.id: noeud for noeud in graphe.noeuds}
    assert par_id["schema"].statut == "terminee"
    assert par_id["schema"].agent == "developpeur"
    assert [e.libelle for e in par_id["schema"].etapes] == ["Lister les entités"]
    # Le nœud pas encore démarré n'est pas une erreur : c'est l'état normal.
    assert par_id["api"].statut == STATUT_BACKLOG
    assert graphe.aretes[0].etat == ARETE_FRANCHIE


# ------------------------------------------- ④ La route : trois vides qui ne se confondent pas


def test_la_route_rend_le_graphe_du_run():
    with client_sur(
        lancement(),
        plan_publie(_noeud("schema"), _noeud("api", "schema"), _noeud("ui", "schema")),
        tache("schema", "terminee"),
    ) as client:
        reponse = client.get(f"/api/executions/{RUN}/graphe")

    assert reponse.status_code == 200
    graphe = reponse.json()
    assert graphe["run_id"] == RUN
    assert graphe["plan_connu"] is True
    assert graphe["niveaux"] == [["schema"], ["api", "ui"]]
    assert {a["vers"]: a["etat"] for a in graphe["aretes"]} == {
        "api": ARETE_FRANCHIE,
        "ui": ARETE_FRANCHIE,
    }


def test_la_route_refuse_un_run_dont_aucune_trace_n_est_arrivee():
    """Par la même porte que `/cout` : la projection répond à ce qu'elle sait, le
    refus motivé est le rôle de la route."""
    with client_sur(lancement()) as client:
        reponse = client.get("/api/executions/run-fantome/graphe")

    assert reponse.status_code == 404
    assert "run-fantome" in reponse.json()["detail"]


def test_un_run_sans_plan_publie_reconstruit_ses_noeuds_et_le_dit():
    """`plan_connu: false` marque le cas qu'on ne peut pas **deviner** — moteur
    antérieur au lot, journal rejoué d'avant, planification en échec.

    Il se dessine comme un plan plat, et on n'a pas le droit d'en conclure la
    même chose : là on lit « on ne sait pas », ici « ces tâches sont
    indépendantes ».
    """
    with client_sur(
        lancement(), tache("schema", "terminee"), tache("api", "en_cours")
    ) as client:
        graphe = client.get(f"/api/executions/{RUN}/graphe").json()

    assert graphe["plan_connu"] is False
    assert graphe["plat"] is True
    assert [noeud["id"] for noeud in graphe["noeuds"]] == ["schema", "api"]
    assert graphe["aretes"] == []


def test_un_plan_plat_publie_se_distingue_d_un_plan_inconnu():
    """Les deux booléens existent pour cette raison, et une vue qui n'en lirait
    qu'un dirait « aucune dépendance déclarée » là où la phrase juste est « on ne
    les connaît pas »."""
    with client_sur(lancement(), plan_publie(_noeud("a"), _noeud("b"))) as client:
        graphe = client.get(f"/api/executions/{RUN}/graphe").json()

    assert (graphe["plan_connu"], graphe["plat"]) == (True, True)
    assert graphe["niveaux"] == [["a", "b"]]


def test_le_nombre_de_noeuds_ne_vaut_pas_le_nombre_de_taches_portees():
    """L'écart n'est pas un défaut : le plan annonce ce qui **sera** fait,
    `nb_taches` compte ce que le run a **réellement porté**. Les faire coïncider
    aurait demandé de rendre un dessin qui pousse au lieu d'un plan."""
    with client_sur(
        lancement(),
        plan_publie(_noeud("schema"), _noeud("api", "schema"), _noeud("ui", "schema")),
        tache("schema", "terminee"),
    ) as client:
        graphe = client.get(f"/api/executions/{RUN}/graphe").json()
        execution = client.get(f"/api/executions/{RUN}").json()

    assert graphe["nb_noeuds"] == 3
    assert execution["nb_taches"] == 1


def test_le_graphe_se_recompose_a_la_lecture_sans_evenement_a_lui():
    """La mise à jour en direct passe par le flux existant : ce sont `run.plan`,
    `tache.statut` et `tache.detail` qui le font bouger."""
    log = InMemoryEventLog()
    for event in (lancement(), plan_publie(_noeud("schema"), _noeud("api", "schema"))):
        asyncio.run(log.consigner(event))
    state = ControlTowerState()
    client = TestClient(create_app(bus=InMemoryEventBus(), state=state, event_log=log))

    with client:
        avant = client.get(f"/api/executions/{RUN}/graphe").json()
        state.appliquer(tache("schema", "terminee"))
        apres = client.get(f"/api/executions/{RUN}/graphe").json()

    assert avant["aretes"][0]["etat"] == ARETE_ATTENDUE
    assert apres["aretes"][0]["etat"] == ARETE_FRANCHIE


# ----------------------- ⑤ Deux branches réellement simultanées, prouvées par une barrière


class BarriereProvider(ModelProvider):
    """Exécutant factice dont **deux** tâches doivent se rejoindre pour avancer.

    La barrière est le contrat du test, et c'est ce qui le distingue d'un `sleep`
    (règle du dépôt, #292) : `attendus` tâches doivent y être **ensemble** pour
    que l'une d'elles puisse rendre son livrable. Un moteur qui les sérialiserait
    laisserait la première attendre une compagne qui ne viendra jamais, et le run
    n'irait pas à son terme — c'est le `wait_for` qui le dit, pas une mesure de
    durée.
    """

    name = "barriere"

    def __init__(self, attendus: int, cibles: frozenset[str]) -> None:
        self._cibles = cibles
        self._barriere: asyncio.Barrier | None = None
        self._attendus = attendus
        self.pic = 0
        self._en_vol = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        # Sur la **première ligne** du prompt (`Tâche : <titre>`) et non n'importe
        # où : le tableau noir d'une tâche aval reprend les livrables de ses
        # dépendances, donc chercher le titre dans tout le texte ferait entrer
        # `recette` dans une barrière qu'elle est seule à attendre.
        premiere = prompt.splitlines()[0] if prompt else ""
        cible = next((c for c in self._cibles if premiere == f"Tâche : {c}"), None)
        if cible is None:
            return "TEXTE (hors barrière)"
        if self._barriere is None:
            self._barriere = asyncio.Barrier(self._attendus)
        self._en_vol += 1
        # Le pic est relevé **avant** l'attente : c'est la seule mesure qui
        # distingue « jamais ensemble » de « ensemble mais trop vite pour être vu ».
        self.pic = max(self.pic, self._en_vol)
        try:
            await asyncio.wait_for(self._barriere.wait(), timeout=10)
        finally:
            self._en_vol -= 1
        return f"LIVRABLE {cible}"


class PlanConstant(ModelProvider):
    """Planificateur factice : rend toujours le même plan."""

    name = "plan-constant"

    def __init__(self, plan_json: str) -> None:
        self._plan = plan_json

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._plan


_PLAN_DEUX_BRANCHES = json.dumps(
    [
        {
            "id": "schema", "titre": "Schéma SQL", "description": "Poser le schéma.",
            "competences_requises": ["sql"], "format_sortie": "SQL", "dependances": [],
        },
        {
            "id": "api", "titre": "API CRUD", "description": "Exposer les routes.",
            "competences_requises": ["backend"], "format_sortie": "Module",
            "dependances": ["schema"],
        },
        {
            "id": "maquette", "titre": "Maquette", "description": "Dessiner l'écran.",
            "competences_requises": ["ux"], "format_sortie": "Maquette",
            "dependances": ["schema"],
        },
        {
            "id": "recette", "titre": "Recette", "description": "Vérifier le tout.",
            "competences_requises": ["tests"], "format_sortie": "Rapport",
            "dependances": ["api", "maquette"],
        },
    ],
    ensure_ascii=False,
)


def _moteur_deux_branches() -> tuple[OrchestrationEngine, BarriereProvider]:
    """Un moteur réel dont `api` et `maquette` doivent tourner **ensemble**.

    `runtimes={}` retire les runtimes outillés que le catalogue câble d'office :
    le fournisseur d'exécution répond alors par `generate`, ce qui suffit — la
    question posée ici est celle de l'**ordonnancement**, pas de l'outillage, et
    un espace de travail par tâche n'y apporterait rien.
    """
    execution = BarriereProvider(2, frozenset({"API CRUD", "Maquette"}))
    orchestrator = Orchestrator(PlanConstant(_PLAN_DEUX_BRANCHES), model="claude-opus-4-8")
    return OrchestrationEngine(execution, orchestrator, runtimes={}), execution


def test_deux_branches_partent_reellement_ensemble():
    """La preuve par la barrière : sans simultanéité réelle, le run ne finit pas.

    Et le pic mesuré dit qu'elles y étaient **ensemble** — une barrière levée
    mais un pic à 1 signalerait un harnais qui se ment à lui-même.

    Le motif a été **prouvé sur un moteur fautif** avant d'être posé : le même
    moteur monté en `max_parallele=1` rougit ici (les deux branches s'attendent
    l'une l'autre, `wait_for` expire, la tâche échoue) et rougit au test suivant
    (l'arête de la branche coupée devient `rompue`). C'est ce qui distingue ce
    contrôle d'un ✓ sur une question jamais posée.
    """
    moteur, execution = _moteur_deux_branches()
    journal = RunJournal(run_id=RUN)

    rapport = asyncio.run(moteur.run("Prototyper un mini-CRM", journal=journal))

    assert execution.pic == 2
    assert all(resultat.ok for resultat in rapport.resultats)


def test_le_graphe_du_run_range_ces_deux_branches_au_meme_niveau():
    """Le bout en bout du chantier : le plan du moteur traverse le journal, le
    pont et la projection, et ressort en graphe où les deux branches sont
    lisibles comme simultanées."""
    moteur, _ = _moteur_deux_branches()
    journal = RunJournal(run_id=RUN)
    asyncio.run(moteur.run("Prototyper un mini-CRM", journal=journal))

    state = ControlTowerState()
    state.appliquer(lancement())
    for record in journal.records:
        for event in evenements_depuis_step(record.to_dict()):
            state.appliquer(event)

    graphe = state.graphe(RUN)

    assert graphe.plan_connu
    assert graphe.niveaux == (("schema",), ("api", "maquette"), ("recette",))
    assert graphe.largeur == 2
    assert graphe.profondeur == 3
    # Toutes les tâches ont abouti : toutes les arêtes sont franchies.
    assert {arete.etat for arete in graphe.aretes} == {ARETE_FRANCHIE}
