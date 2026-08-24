"""La **portée run** et la **progression** d'un run (#473, lot 1 de #472 ; couvert ici par #480).

Le lot 1 du chantier « le run, objet de premier plan » a livré la moitié API de
tout le reste : une lecture peut se cadrer sur un run (`?run=`), et un run dit
**où il en est** (`progression`) sans que personne ait à recompter des cartes.
Les tests étaient différés au lot final ([docs/10 §5.1](../docs/10-workflow-git.md)),
les voici.

Quatre volets, dans l'ordre où la donnée remonte :

① **Le rangement des statuts** (`maestro.controltower.progression`) — la table
   `COMPARTIMENT_PAR_STATUT` *est* le contrat partagé, donc c'est elle qu'on
   éprouve, avec ses deux arbitrages (`assignee` → à faire,
   `en_attente_validation` → en cours) et son ramasse-miettes `autres`, sans
   lequel `total` cesserait silencieusement d'égaler `nb_taches`.

② **La portée** (`maestro.controltower.portee.PorteeRun`) — la dissymétrie avec
   `PorteeProjet` est le critère : le run est **facultatif**, le projet
   obligatoire. Et son prédicat se juge sur les tâches que le run a *portées*,
   jamais sur le `run_id` d'une tâche.

③ **La projection** — `taches_vues`, `taches_du_run`, `progression` et
   l'invariant `progression.total == nb_taches`, y compris sur le cas qui a
   motivé le choix : une **relance** (#349), où deux runs portent le même
   identifiant de tâche et où le second volerait les tâches du premier si
   l'appartenance se lisait sur la carte.

④ **Les routes** — `GET /api/taches?run=`, son 404 `run-inconnu`, sa composition
   avec `?projet=`, et la clé `progression` dans les deux lectures d'exécution.

**Ni Redis, ni réseau, ni appel modèle** : l'app est la vraie (`create_app`) sur
bus mémoire, et la projection est alimentée par des événements posés à la main —
c'est exactement ce que la pompe lui livre en production.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import (
    EVENEMENT_TACHE_STATUT,
    ControlTowerState,
    Event,
    InMemoryEventBus,
    InMemoryEventLog,
    create_app,
)
from maestro.controltower.portee import (
    PorteeProjet,
    PorteeRefusee,
    PorteeRun,
    resoudre_portee_run,
)
from maestro.controltower.progression import (
    A_FAIRE,
    AUTRES,
    BLOQUEES,
    COMPARTIMENT_PAR_STATUT,
    COMPARTIMENTS,
    COMPARTIMENTS_SOLDES,
    ECHECS,
    EN_COURS,
    TERMINEES,
    Progression,
    compartiment,
    progression_des_statuts,
)
from maestro.controltower.state import (
    EVENEMENT_EXECUTION_STATUT,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
)

RUN = "run-un"
AUTRE_RUN = "run-deux"
PROJET = "prj-0001"
#: La vue transverse : un mot réservé, donc lisible sans dépôt de projets déclaré
#: (`?projet=<id>` exigerait un `ProjetStore`, hors sujet ici — voir
#: `tests/test_appartenance_projet.py`, qui éprouve précisément cette porte-là).
TRANSVERSE = "tous"


# ------------------------------------------------------------------ harnais


def tache(
    tache_id: str,
    statut: str,
    *,
    run_id: str = RUN,
    projet_id: str | None = PROJET,
) -> Event:
    """L'événement qui fait exister une tâche dans la projection — et dans un run."""
    return Event(
        type=EVENEMENT_TACHE_STATUT,
        run_id=run_id,
        tache_id=tache_id,
        titre=f"Tâche {tache_id}",
        agent="developpeur",
        role="Développeur",
        statut=statut,
        projet_id=projet_id,
    )


def lancement(run_id: str = RUN, *, projet_id: str | None = PROJET) -> Event:
    """L'événement de lancement d'un run — ce qui l'inscrit dans la projection."""
    return Event(
        type=EVENEMENT_EXECUTION_STATUT,
        run_id=run_id,
        statut=EXECUTION_EN_COURS,
        titre="Objectif",
        detail="lancée depuis la Control Tower",
        projet_id=projet_id,
    )


def projection(*evenements: Event) -> ControlTowerState:
    """Une projection alimentée comme la pompe le ferait : événement par événement."""
    state = ControlTowerState()
    for event in evenements:
        state.appliquer(event)
    return state


def client_sur(*evenements: Event) -> TestClient:
    """L'app réelle, bus mémoire, historique rejoué par le lifespan.

    On passe par l'`EventLog` plutôt que par une projection préremplie : c'est le
    chemin de production (rejeu au démarrage), et il alimente du même coup le
    journal requêtable.
    """
    log = InMemoryEventLog()
    for event in evenements:
        asyncio.run(log.consigner(event))
    return TestClient(create_app(bus=InMemoryEventBus(), state=ControlTowerState(), event_log=log))


# --------------------- ① Le rangement des statuts : la table est le contrat


@pytest.mark.parametrize(
    ("statut", "attendu"),
    [
        ("backlog", A_FAIRE),
        ("prete", A_FAIRE),
        # Le premier des deux arbitrages du module : une tâche assignée a un
        # exécutant mais n'a pas commencé — dans une barre, c'est « à faire ».
        ("assignee", A_FAIRE),
        ("en_cours", EN_COURS),
        # Le second : suspendue sur un humain, la tâche est toujours en vol.
        ("en_attente_validation", EN_COURS),
        ("bloquee", BLOQUEES),
        ("terminee", TERMINEES),
        ("echec", ECHECS),
    ],
)
def test_chaque_statut_de_la_machine_a_etats_a_son_compartiment(statut, attendu):
    """La table couvre la machine à états **entière**, pas seulement ce qui circule."""
    assert compartiment(statut) == attendu
    assert COMPARTIMENT_PAR_STATUT[statut] == attendu


def test_un_statut_inconnu_tombe_dans_autres_sans_lever():
    """Un statut à venir est un fait à montrer, jamais une panne — ni un trou.

    Sans `autres`, un statut que le moteur émettrait demain disparaîtrait du
    compte et `total` cesserait *silencieusement* d'égaler `nb_taches` : un
    compartiment visible à 1 se remarque, une somme fausse non.
    """
    assert compartiment("teleporte") == AUTRES
    assert compartiment("") == AUTRES
    assert progression_des_statuts(["teleporte"]).total == 1


def test_la_progression_compte_par_compartiment_et_totalise():
    progression = progression_des_statuts(
        ["assignee", "en_cours", "en_attente_validation", "terminee", "echec", "bloquee", "?"]
    )

    assert progression.a_faire == 1
    assert progression.en_cours == 2
    assert progression.bloquees == 1
    assert progression.terminees == 1
    assert progression.echecs == 1
    assert progression.autres == 1
    assert progression.total == 7


def test_soldees_rassemble_les_trois_statuts_terminaux_du_moteur():
    """`soldees` est **servi** pour qu'une barre se dessine sans réécrire la machine à états."""
    assert COMPARTIMENTS_SOLDES == (TERMINEES, ECHECS, BLOQUEES)

    progression = progression_des_statuts(["terminee", "echec", "bloquee", "en_cours"])

    assert progression.soldees == 3
    # Une tâche bloquée est acquise au même titre qu'une échouée : elle ne sera
    # pas jouée, donc elle appartient au dénominateur de l'avancement.
    assert progression.soldees != progression.terminees + progression.echecs


def test_le_dict_servi_porte_les_six_compartiments_plus_les_deux_agregats():
    servi = progression_des_statuts(["terminee", "en_cours"]).to_dict()

    assert set(servi) == set(COMPARTIMENTS) | {"soldees", "total"}
    assert servi["total"] == 2
    assert servi["soldees"] == 1


def test_une_progression_vide_est_un_zero_lisible_et_non_un_trou():
    """Le cas nominal d'un run arrêté sur son brief : aucune tâche, et il le dit."""
    vide = Progression()

    assert vide.total == 0
    assert vide.soldees == 0
    assert vide.to_dict()["total"] == 0


# ------------------------------------ ② La portée run : facultative, et additive


def test_une_portee_sans_run_ne_restreint_rien():
    """La dissymétrie voulue : omettre `?run=` n'est pas un périmètre oublié."""
    portee = resoudre_portee_run(None)

    assert portee.transverse is True
    assert portee.libelle == "tous"
    assert portee.retient("n-importe-quoi", frozenset()) is True


@pytest.mark.parametrize("brut", ["", "   "])
def test_un_run_vide_vaut_un_run_omis(brut):
    assert resoudre_portee_run(brut) == PorteeRun.tous()


def test_une_portee_de_run_ne_retient_que_les_taches_que_le_run_a_portees():
    portee = resoudre_portee_run(RUN)

    assert portee.run_id == RUN
    assert portee.libelle == RUN
    assert portee.retient("schema-bdd", frozenset({"schema-bdd"})) is True
    assert portee.retient("api-users", frozenset({"schema-bdd"})) is False


def test_un_run_inconnu_est_refuse_plutot_que_rendu_vide():
    """Sur une faute de frappe, une liste vide se lit « ce run n'a rien fait »."""
    with pytest.raises(PorteeRefusee) as refus:
        resoudre_portee_run("run-fantome", run_connu=lambda _: False)

    assert refus.value.motif == "run-inconnu"
    assert "run-fantome" in str(refus.value)


def test_sans_moyen_de_verifier_l_existence_la_portee_se_resout_sur_l_identifiant():
    """`run_connu=None` : une projection isolée n'a pas à refuser ce qu'elle ne sait pas."""
    assert resoudre_portee_run(RUN) == PorteeRun.run(RUN)


# --------------------------------- ③ La projection : l'appartenance et le compte


def test_les_taches_d_un_run_se_lisent_dans_ses_evenements():
    state = projection(
        lancement(),
        tache("schema-bdd", "terminee"),
        tache("api-users", "en_cours"),
        tache("api-users", "terminee"),  # deux fois : une tâche, pas deux
    )

    assert state.taches_du_run(RUN) == frozenset({"schema-bdd", "api-users"})
    assert state.execution(RUN).nb_taches == 2


def test_un_run_inconnu_n_a_ni_taches_ni_progression_et_ne_leve_pas():
    """La projection répond ce qu'elle sait ; le refus motivé est le rôle des routes."""
    state = projection(lancement())

    assert state.taches_du_run("run-fantome") == frozenset()
    assert state.progression("run-fantome") == Progression()


def test_la_progression_d_un_run_vaut_toujours_son_nombre_de_taches():
    """L'invariant du contrat : `progression.total == nb_taches`, par construction."""
    state = projection(
        lancement(),
        tache("t1", "terminee"),
        tache("t2", "en_cours"),
        tache("t3", "assignee"),
        tache("t4", "echec"),
    )

    progression = state.progression(RUN)

    assert progression.to_dict() == {
        "a_faire": 1,
        "en_cours": 1,
        "bloquees": 0,
        "terminees": 1,
        "echecs": 1,
        "autres": 0,
        "soldees": 2,
        "total": 4,
    }
    assert progression.total == state.execution(RUN).nb_taches


def test_une_relance_ne_vole_pas_les_taches_du_run_qu_elle_reprend():
    """Le renversement que la portée existe pour éviter (#349).

    Un identifiant de tâche est un slug engendré depuis son contenu : deux runs
    qui décomposent le même objectif portent **les mêmes**. Lue sur la carte,
    l'appartenance donnerait toutes les tâches au dernier run qui les a touchées,
    et la vue du run repris se viderait toute seule.
    """
    state = projection(
        lancement(RUN),
        tache("schema-bdd", "echec", run_id=RUN),
        lancement(AUTRE_RUN),
        tache("schema-bdd", "terminee", run_id=AUTRE_RUN),
        tache("api-users", "terminee", run_id=AUTRE_RUN),
    )

    # La carte, elle, ne connaît que le dernier run — c'est justement pourquoi on
    # ne la lit pas pour trancher l'appartenance.
    (carte,) = [t for t in state.taches() if t.id == "schema-bdd"]
    assert carte.run_id == AUTRE_RUN

    assert state.taches_du_run(RUN) == frozenset({"schema-bdd"})
    assert state.taches_du_run(AUTRE_RUN) == frozenset({"schema-bdd", "api-users"})
    # L'état compté est celui de la **carte** : la tâche reprise avec succès se
    # lit « terminée » des deux côtés, plutôt qu'échouée dans l'une des barres.
    assert state.progression(RUN).terminees == 1
    assert state.progression(RUN).echecs == 0


def test_les_deux_portees_se_composent_au_lieu_de_se_relayer():
    """`?run=` s'**ajoute** à `?projet=` — c'est l'arbitrage du parent, à l'étage projection."""
    state = projection(
        lancement(RUN),
        tache("t-projet", "en_cours", projet_id=PROJET),
        tache("t-hors-projet", "en_cours", projet_id=None),
    )

    du_run = state.taches(PorteeProjet.tous(), PorteeRun.run(RUN))
    du_run_et_du_projet = state.taches(PorteeProjet.projet(PROJET), PorteeRun.run(RUN))

    assert {t.id for t in du_run} == {"t-projet", "t-hors-projet"}
    assert {t.id for t in du_run_et_du_projet} == {"t-projet"}


def test_une_tache_vue_sans_carte_compte_pour_autres():
    """Une tâche du run que la projection ne connaît pas ne disparaît pas du compte."""
    state = projection(lancement(), tache("t1", "en_cours"))
    state._taches.clear()  # la carte s'évapore, l'événement du run reste

    progression = state.progression(RUN)

    assert progression.autres == 1
    assert progression.total == 1


# ------------------------------------------------- ④ Les routes qui les servent


def test_la_route_des_taches_filtre_sur_le_run_demande():
    with client_sur(
        lancement(RUN),
        tache("schema-bdd", "terminee", run_id=RUN),
        lancement(AUTRE_RUN),
        tache("api-users", "en_cours", run_id=AUTRE_RUN),
    ) as client:
        toutes = client.get("/api/taches", params={"projet": TRANSVERSE})
        du_run = client.get("/api/taches", params={"projet": TRANSVERSE, "run": RUN})

    assert {t["id"] for t in toutes.json()} == {"schema-bdd", "api-users"}
    assert [t["id"] for t in du_run.json()] == ["schema-bdd"]


def test_un_run_vide_dans_l_url_vaut_la_lecture_d_avant_ce_lot():
    with client_sur(lancement(), tache("schema-bdd", "terminee")) as client:
        reponse = client.get("/api/taches", params={"projet": TRANSVERSE, "run": ""})

    assert reponse.status_code == 200
    assert [t["id"] for t in reponse.json()] == ["schema-bdd"]


def test_la_route_des_taches_refuse_un_run_inconnu_avec_son_motif():
    with client_sur(lancement(), tache("schema-bdd", "terminee")) as client:
        reponse = client.get("/api/taches", params={"projet": TRANSVERSE, "run": "run-fantome"})

    assert reponse.status_code == 404
    assert reponse.json()["detail"]["motif"] == "run-inconnu"


def test_la_portee_projet_reste_obligatoire_meme_avec_un_run():
    """`?run=` ne dispense pas de `?projet=` : il s'y ajoute, il ne le remplace pas."""
    with client_sur(lancement(), tache("schema-bdd", "terminee")) as client:
        reponse = client.get("/api/taches", params={"run": RUN})

    assert reponse.status_code == 422
    assert reponse.json()["detail"]["motif"] == "projet-requis"


def test_les_deux_portees_se_composent_aussi_sur_la_route():
    """Le run cadre, le projet cadre encore — et ce qui sort est l'intersection."""
    with client_sur(
        lancement(RUN),
        tache("t-projet", "en_cours", projet_id=PROJET),
        tache("t-sans-projet", "en_cours", projet_id=None),
    ) as client:
        du_run = client.get("/api/taches", params={"projet": TRANSVERSE, "run": RUN})
        du_run_sans_projet = client.get("/api/taches", params={"projet": "aucun", "run": RUN})

    assert {t["id"] for t in du_run.json()} == {"t-projet", "t-sans-projet"}
    assert [t["id"] for t in du_run_sans_projet.json()] == ["t-sans-projet"]


def test_la_liste_des_executions_porte_la_progression_de_chaque_run():
    with client_sur(
        lancement(RUN),
        tache("t1", "terminee"),
        tache("t2", "en_cours"),
    ) as client:
        (resume,) = client.get("/api/executions", params={"projet": TRANSVERSE}).json()

    assert resume["nb_taches"] == 2
    assert resume["progression"]["terminees"] == 1
    assert resume["progression"]["en_cours"] == 1
    assert resume["progression"]["total"] == resume["nb_taches"]


def test_le_detail_d_un_run_porte_la_meme_progression_que_la_liste():
    """Un run lu à deux endroits ne se contredit pas sur son propre avancement."""
    with client_sur(
        lancement(RUN),
        tache("t1", "terminee"),
        tache("t2", "bloquee"),
    ) as client:
        (dans_la_liste,) = client.get("/api/executions", params={"projet": TRANSVERSE}).json()
        detail = client.get(f"/api/executions/{RUN}").json()

    assert detail["progression"] == dans_la_liste["progression"]
    assert detail["progression"]["soldees"] == 2


def test_les_taches_comptees_sont_exactement_celles_que_la_route_rend():
    """La barre et le Kanban d'un même écran comptent la même population."""
    with client_sur(
        lancement(RUN),
        tache("t1", "terminee"),
        tache("t2", "en_cours"),
        lancement(AUTRE_RUN),
        tache("t3", "terminee", run_id=AUTRE_RUN),
    ) as client:
        detail = client.get(f"/api/executions/{RUN}").json()
        cartes = client.get("/api/taches", params={"projet": TRANSVERSE, "run": RUN}).json()

    assert detail["progression"]["total"] == len(cartes)


def test_un_run_sans_tache_le_dit_plutot_que_de_taire_sa_progression():
    """L'état normal d'un run arrêté sur son brief — pas le symptôme d'une lecture ratée."""
    with client_sur(lancement()) as client:
        detail = client.get(f"/api/executions/{RUN}").json()

    assert detail["nb_taches"] == 0
    assert detail["progression"]["total"] == 0


def test_la_progression_survit_au_redemarrage_de_l_api():
    """Elle est recomptée du journal durable, jamais conservée à part."""
    log = InMemoryEventLog()
    for event in (lancement(), tache("t1", "terminee"), tache("t2", "echec")):
        asyncio.run(log.consigner(event))

    for _ in range(2):
        app = create_app(bus=InMemoryEventBus(), state=ControlTowerState(), event_log=log)
        with TestClient(app) as client:
            detail = client.get(f"/api/executions/{RUN}").json()
        assert detail["progression"] == {
            "a_faire": 0,
            "en_cours": 0,
            "bloquees": 0,
            "terminees": 1,
            "echecs": 1,
            "autres": 0,
            "soldees": 2,
            "total": 2,
        }


def test_un_run_solde_garde_ses_taches_et_sa_progression():
    """Rien n'est remis à zéro à la fin : le compte est celui de ce qui a été fait."""
    with client_sur(
        lancement(),
        tache("t1", "terminee"),
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN,
            statut=EXECUTION_TERMINEE,
            detail="1/1 tâche(s) réussie(s)",
            projet_id=PROJET,
        ),
    ) as client:
        detail = client.get(f"/api/executions/{RUN}").json()

    assert detail["statut"] == EXECUTION_TERMINEE
    assert detail["progression"]["terminees"] == 1
