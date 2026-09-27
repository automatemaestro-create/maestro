"""**Les comptes d'un run disent la même chose partout** (#924, lot 3 de #921).

Le lot a livré le remède et a **différé ses tests ici** (docs/10 §5.1). Ce qu'il
répare est le constat le plus mesuré du retex du 2026-09-11 :

> Le pipeline annonce « 4 tâches », le Kanban en montre 1, l'en-tête compte
> « 0/1 » puis « 1/2 » puis « 2/3 » — le dénominateur grandit, donc la barre est
> presque pleine à mi-parcours. `/couts` compte 8 tâches pour un run qui en a 4.
> *(G3 et C8)*

Quatre surfaces dérivaient le compte chacune de leur côté. Il naît désormais
**une fois**, dans `EtatExecution.taches_vues`, et ce fichier garde exactement
cela : non pas que chaque surface ait raison, mais qu'elles aient **la même**
raison. Un test par surface aurait pu rester vert sur quatre nombres
contradictoires — c'est même précisément ce qui s'est produit, chaque écran
étant couvert de son côté quand le retex les a mis côte à côte.

D'où la forme : un run, un état, **les quatre lectures dans la même assertion**.

① **le compte unique** — `nb_taches`, `progression.total`, `nb_noeuds` du graphe,
   les cartes du Kanban et la ligne « par exécution » de l'écran Coûts ;
② **le dénominateur ne grandit plus** — il est celui du plan dès la
   décomposition, et seul le numérateur bouge (G3) ;
③ **une tâche hors plan entre dans le graphe** — la réciproque, trouvée en
   regardant l'écran à la première capture de la relecture visuelle : le graphe
   ne dessinait que le plan, donc il cachait du travail réel ;
④ **`:fusion` ne fabrique plus une tâche** — la cause de C8, qui vit dans le
   pont journal → bus et pas dans la projection.

**Ni Redis, ni réseau, ni appel modèle** : l'app est la vraie (`create_app`) sur
bus mémoire, alimentée par des événements posés à la main — ce que la pompe lui
livre en production.
"""

from __future__ import annotations

import asyncio

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
from maestro.controltower.state import (
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_TACHE_STATUT,
    EXECUTION_EN_COURS,
)
from maestro.plan_run import NoeudPlan
from maestro.telemetry.usage import StepUsage

RUN = "run-comptes"
PROJET = "prj-0001"
#: La portée des deux lectures qui l'exigent (#277). « tous » plutôt que
#: `PROJET` : le projet n'est **pas déclaré** dans ce harnais (aucun dépôt de
#: projets monté), et un identifiant inconnu sort en 404 `projet-inconnu` — ce
#: qu'on lirait alors serait un refus, pas un compte. La vue transverse ne
#: change rien à ce qui est mesuré ici : il n'y a qu'un run.
PORTEE = "tous"


# ------------------------------------------------------------------ harnais


def _noeud(identifiant: str, *dependances: str) -> NoeudPlan:
    return NoeudPlan(
        id=identifiant, titre=f"Tâche {identifiant}", dependances=tuple(dependances)
    )


def lancement() -> Event:
    return Event(
        type=EVENEMENT_EXECUTION_STATUT,
        run_id=RUN,
        statut=EXECUTION_EN_COURS,
        titre="Objectif",
        projet_id=PROJET,
    )


def plan_publie(*noeuds: NoeudPlan) -> Event:
    """L'événement `run.plan` — publié une fois, à la décomposition."""
    return Event(type=EVENEMENT_RUN_PLAN, run_id=RUN, plan=list(noeuds), projet_id=PROJET)


def tache(tache_id: str, statut: str, cout: float = 0.0) -> Event:
    return Event(
        type=EVENEMENT_TACHE_STATUT,
        run_id=RUN,
        tache_id=tache_id,
        titre=f"Tâche {tache_id}",
        agent="developpeur",
        role="Développeur",
        statut=statut,
        usage=StepUsage(appels=1, cout_usd=cout) if cout else None,
        projet_id=PROJET,
    )


def client_sur(*evenements: Event) -> TestClient:
    """L'app réelle, bus mémoire, historique rejoué par le lifespan."""
    log = InMemoryEventLog()
    for event in evenements:
        asyncio.run(log.consigner(event))
    return TestClient(
        create_app(bus=InMemoryEventBus(), state=ControlTowerState(), event_log=log)
    )


def comptes(client: TestClient) -> dict[str, int]:
    """Ce que les quatre surfaces disent du nombre de tâches de ce run.

    Lues **par les routes** et non sur la projection : c'est ce que les écrans
    reçoivent, et le défaut de C8 vivait justement dans une agrégation de route
    (`analytics`), pas dans l'état.
    """
    execution = client.get(f"/api/executions/{RUN}").json()
    graphe = client.get(f"/api/executions/{RUN}/graphe").json()
    kanban = client.get("/api/taches", params={"projet": PORTEE, "run": RUN}).json()
    couts = client.get("/api/analytics/couts", params={"projet": PORTEE}).json()
    ligne = next(e for e in couts["executions"] if e["run_id"] == RUN)
    return {
        "pipeline (nb_taches)": execution["nb_taches"],
        "barre (progression.total)": execution["progression"]["total"],
        "graphe (nb_noeuds)": graphe["nb_noeuds"],
        "kanban (cartes)": len(kanban),
        "couts (par execution)": ligne["nb_taches"],
    }


# ------------------------------------------ ① Le compte naît une fois, quatre surfaces l'héritent


def test_les_quatre_surfaces_comptent_le_meme_nombre_de_taches():
    """**Le** critère du lot, et il ne se vérifie que d'un seul tenant.

    Le run décompose en quatre tâches, deux ont démarré, une seule a dépensé.
    C'est exactement l'état où le retex a relevé quatre nombres différents : le
    plan en annonçait quatre, le Kanban n'avait de carte que pour celles qui
    avaient émis un événement, et l'écran Coûts ne comptait que celles de sa
    fenêtre de dépense.
    """
    with client_sur(
        lancement(),
        plan_publie(
            _noeud("schema"), _noeud("api", "schema"), _noeud("ui", "schema"), _noeud("doc")
        ),
        tache("schema", "terminee", cout=0.42),
        tache("api", "en_cours"),
    ) as client:
        mesures = comptes(client)

    assert set(mesures.values()) == {4}, mesures


def test_un_run_sans_plan_publie_compte_encore_ce_qu_il_a_porte():
    """Le repli, et il n'est pas un cas de bord : journal antérieur à #490,
    producteur minimaliste, run arrêté avant sa décomposition.

    L'union des deux moitiés vaut alors la seconde seule, et les quatre surfaces
    restent d'accord — sans quoi le remède aurait déplacé la contradiction au
    lieu de la retirer.
    """
    with client_sur(
        lancement(), tache("schema", "terminee"), tache("api", "en_cours")
    ) as client:
        mesures = comptes(client)

    assert set(mesures.values()) == {2}, mesures


# ------------------------------------------------- ② Le dénominateur ne grandit plus (G3)


def test_le_denominateur_est_pose_par_la_decomposition_et_ne_bouge_plus():
    """« 0/1 », puis « 1/2 », « 2/3 », « 3/4 » : la barre était presque pleine à
    mi-parcours parce que son dénominateur grandissait sous les yeux.

    Le plan est **figé avant la première exécution** (`plan_run`), donc le total
    est connu dès la décomposition ; ce sont les soldées qui montent.
    """
    plan = plan_publie(_noeud("schema"), _noeud("api"), _noeud("ui"), _noeud("doc"))

    with client_sur(lancement(), plan) as client:
        depart = client.get(f"/api/executions/{RUN}").json()["progression"]
    with client_sur(lancement(), plan, tache("schema", "terminee")) as client:
        une = client.get(f"/api/executions/{RUN}").json()["progression"]
    with client_sur(
        lancement(), plan, tache("schema", "terminee"), tache("api", "terminee")
    ) as client:
        deux = client.get(f"/api/executions/{RUN}").json()["progression"]

    assert [depart["total"], une["total"], deux["total"]] == [4, 4, 4]
    assert [depart["soldees"], une["soldees"], deux["soldees"]] == [0, 1, 2]


def test_un_run_arrete_sur_son_brief_ne_compte_aucune_tache():
    """Zéro avant la décomposition, et c'est l'état normal — pas une panne.

    L'échantillon fautif de la garde ci-dessus : si `taches_vues` inventait un
    total, celui-ci ne serait pas nul ici, et « le dénominateur est stable »
    vaudrait pour un nombre faux.
    """
    with client_sur(lancement()) as client:
        execution = client.get(f"/api/executions/{RUN}").json()

    assert execution["nb_taches"] == 0
    assert execution["progression"]["total"] == 0


# --------------------------------------- ③ Le graphe ne cache pas le travail hors plan


def test_une_tache_hors_plan_entre_dans_le_graphe_et_dans_les_quatre_comptes():
    """La réciproque du reste du ticket, trouvée **en regardant l'écran**.

    La démo publie un plan de quatre nœuds puis exécute une cinquième tâche
    qu'il n'annonçait pas — une vérification de santé rejouée en boucle. Le
    graphe ne dessinait que le plan : il annonçait « 4 tâches » quand les trois
    autres surfaces en disaient cinq.

    Le cas ne se voyait dans aucun test, ni dans aucun banc de mesure, où les
    runs sont construits bien formés : sans la capture de la relecture visuelle,
    le critère serait parti annoncé tenu sans l'être.
    """
    with client_sur(
        lancement(),
        plan_publie(_noeud("schema"), _noeud("api"), _noeud("ui"), _noeud("doc")),
        tache("sante", "terminee"),
    ) as client:
        mesures = comptes(client)
        graphe = client.get(f"/api/executions/{RUN}/graphe").json()

    assert set(mesures.values()) == {5}, mesures
    # Le nœud isolé est **dans** le graphe, et personne n'a déclaré ce qu'il
    # attend : aucune arête ne le relie.
    assert "sante" in {noeud["id"] for noeud in graphe["noeuds"]}
    assert all(
        "sante" not in (arete["de"], arete["vers"]) for arete in graphe["aretes"]
    )


# ---------------------------------------------- ④ `:fusion` ne fabrique plus une tâche (C8)


def _ligne(etape: str) -> dict:
    """Une ligne de journal, telle que `StepRecord.to_dict` la rend."""
    return {
        "run_id": RUN,
        "etape": etape,
        "nom": "Fusion dans la racine",
        "agent": "developpeur",
        "role": "Développeur",
        "statut": "terminee",
        "usage": StepUsage(appels=1, cout_usd=0.1).to_dict(),
        "horodatage": "2026-09-20T10:00:00+00:00",
        "projet_id": PROJET,
    }


def test_une_etape_de_fusion_se_rattache_a_sa_tache_et_non_a_un_identifiant_fantome():
    """La cause de C8, et elle vit dans le pont, pas dans la projection.

    `:fusion` (#705) avait rejoint la liste qui reconnaît une étape d'activité et
    **pas** celle qui retire son suffixe : l'événement sortait avec
    `coquille-ui:fusion` pour `tache_id`. Un identifiant qui n'est la tâche de
    personne se compte comme une tâche de plus partout où le flux en compte —
    huit tâches annoncées par `/couts` pour un run qui en avait quatre.
    """
    (event,) = evenements_depuis_step(_ligne("coquille-ui:fusion"))

    assert event.tache_id == "coquille-ui"


def test_les_quatre_suffixes_voisins_se_rattachent_de_la_meme_facon():
    """La liste vit **une fois** (`_SUFFIXES_ACTIVITE`) : la tenir en double est
    précisément ce qui a échoué. Ce contrôle les balaie d'un coup, pour qu'un
    suffixe ajouté d'un seul côté se voie ici — `:processus` (#1279) compris."""
    for suffixe in (
        ":validation",
        ":relance",
        ":refus-outil",
        ":activite",
        ":fusion",
        ":processus",
    ):
        (event,) = evenements_depuis_step(_ligne(f"coquille-ui{suffixe}"))
        assert event.tache_id == "coquille-ui", suffixe


def test_une_activite_de_fusion_ne_gonfle_aucun_compte():
    """Le même fait, vu du bout de la chaîne : la tâche est comptée une fois.

    C'est la moitié qui manquerait si l'on ne gardait que le pont — l'écran
    Coûts comptait sa propre population, et c'est là que l'écart se lisait.
    """
    fusion = evenements_depuis_step(_ligne("schema:fusion"))

    with client_sur(
        lancement(),
        plan_publie(_noeud("schema"), _noeud("api")),
        tache("schema", "terminee", cout=0.42),
        *fusion,
    ) as client:
        mesures = comptes(client)

    assert set(mesures.values()) == {2}, mesures


def test_l_ecran_couts_compte_les_taches_du_run_et_non_celles_de_sa_fenetre():
    """L'autre moitié de C8 : la fenêtre de période ne change pas ce qu'est un run.

    Une fois la cause du pont réglée, l'écran aurait encore annoncé **moins** que
    le pipeline tant qu'une tâche du plan n'avait pas dépensé — un nombre à lui,
    que nulle autre surface n'aurait confirmé. Le compte vient donc du run.
    """
    with client_sur(
        lancement(),
        plan_publie(_noeud("schema"), _noeud("api"), _noeud("ui")),
        tache("schema", "terminee", cout=0.42),
    ) as client:
        couts = client.get("/api/analytics/couts", params={"projet": PORTEE}).json()
        ligne = next(e for e in couts["executions"] if e["run_id"] == RUN)
        # Le grand livre par tâche, lui, ne liste que ce qui a dépensé : les deux
        # comptes diffèrent légitimement, parce que la question n'est pas la même.
        detail = client.get(f"/api/executions/{RUN}/cout").json()

    assert ligne["nb_taches"] == 3
    assert len(detail["taches"]) == 1
