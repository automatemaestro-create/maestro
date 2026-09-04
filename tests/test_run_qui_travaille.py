"""Un run qui travaille **se voit**, et la frise garde son tri (#838, lot 4 de #834).

Le défaut de #834 n'était **pas** une panne de transport : la chaîne
Redis → API → WebSocket → page tenait ses ~1 s de bout en bout, mesurée trois
fois. Ce qui manquait était la **matière** des payloads — les vues d'un run ne
portaient rien qui change entre `en_cours` et l'issue d'une tâche. Une suite qui
vérifierait « la page se recharge à l'événement » aurait donc été **verte
pendant toute la durée du défaut**. Ces tests ne mesurent ni latence ni durée
(règle de #577 : un chronomètre en CI mesure la charge de la machine) : ils
comptent la **présence des champs** et le **contenu** des payloads.

Et chacun **prouve son motif sur un échantillon fautif avant de conclure** — la
méthode d'`apps/web/tests/contraste.test.ts` (#534) et des `grep` de
`tests/test_cycle_de_vie.py` (#366) : on construit d'abord la forme d'**avant**
le chantier (un couloir en cours sans
signe de vie, un coût de tâche en vol resté `null`, une frise dont
`agent.activite` entre dans `entrees`), on vérifie que le contrôle rougit
dessus, puis on le joue sur la forme livrée. Sans cette moitié, un ✓ porterait
sur une question jamais posée.

Six volets, dans l'ordre où la donnée remonte :

① **La forme du signe** (`maestro.controltower.signe_de_vie`) — un horodatage et
   un libellé court ; la troncature se dit ; à instant égal, le premier reste.

② **La règle vit chez la projection** (`EtatTache.signe_de_vie`) — seule une
   tâche `en_cours` **au sens strict** porte un signe : ni l'attente de
   validation (que le compartiment de `progression.py` range pourtant avec
   `en_cours`), ni une tâche arrêtée, quel qu'ait été son dernier geste.

③ **Le nœud du graphe** lit cette décision et ne la refait pas : deux lectures
   espacées d'un geste rendent deux valeurs — c'est ce qui manquait à un graphe
   immobile pendant douze minutes.

④ **Le couloir de la frise** porte le **même** signe, comme **attribut de
   l'en-tête** : `entrees` ne reçoit aucune entrée d'activité, et la frise sans
   son signe est, au JSON près, celle d'avant.

⑤ **`TYPES_FRISE` est gardé** contre l'ouverture d'`agent.activite` en bloc —
   l'écart le plus plausible à la relecture, et celui que la docstring de
   `frise.py` écarte explicitement. Gardé deux fois : par le comportement (un
   ensemble élargi fait entrer l'activité dans `entrees`, ce que le test montre
   sur un échantillon fautif) et par la source (le littéral déclaré est exactement
   celui du contrat).

⑥ **Le coût en vol** (#835) — complément de `tests/test_usage_en_vol.py`, qui
   garde le critique : ici la carte d'une tâche en cours **sans relevé** est
   l'échantillon fautif (`null`, non partiel), et les trois lectures de la carte
   se distinguent deux à deux.

Puis **la route**, de bout en bout : carte, nœud et couloir servent le même
signe ; un run soldé rend exactement la vue d'avant.

**Ni Redis, ni réseau, ni appel modèle** : l'app est la vraie (`create_app`)
sur bus mémoire, alimentée par des événements posés à la main — ce que la pompe
lui livre en production.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

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
from maestro.controltower import frise as frise_mod
from maestro.controltower.events import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_TACHE_BLOCAGE,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_TACHE_USAGE,
    EVENEMENT_VALIDATION_DECISION,
    EVENEMENT_VALIDATION_DEMANDE,
)
from maestro.controltower.frise import COULOIR_REPLI, TYPES_FRISE, CouloirFrise, frise_du_run
from maestro.controltower.graphe import EtatNoeud, graphe_du_run
from maestro.controltower.journal import EntreeJournal
from maestro.controltower.progression import EN_COURS, STATUT_EN_ATTENTE_VALIDATION, compartiment
from maestro.controltower.signe_de_vie import ELLIPSE, LARGEUR_LIBELLE, SigneDeVie, libelle_court
from maestro.controltower.state import EVENEMENT_EXECUTION_STATUT, EXECUTION_EN_COURS
from maestro.engine.executor import (
    STATUT_BLOCAGE_SIGNALE,
    STATUT_BLOQUEE,
    STATUT_ECHEC,
    STATUT_EN_COURS,
    STATUT_TERMINEE,
)
from maestro.plan_run import NoeudPlan
from maestro.telemetry.usage import StepUsage

RUN = "run-vivant"
PROJET = "prj-0001"

T0 = "2026-08-30T07:41:00+00:00"
T1 = "2026-08-30T07:41:12+00:00"
T2 = "2026-08-30T07:41:27+00:00"
T3 = "2026-08-30T07:53:00+00:00"


# ------------------------------------------------------------------ harnais


def tache(
    tache_id: str,
    statut: str,
    *,
    agent: str = "developpeur",
    role: str = "Développeur",
    run_id: str = RUN,
    horodatage: str = T0,
    **champs,
) -> Event:
    """Le `tache.statut` qui fait exister une tâche dans la projection — et dans un run."""
    return Event(
        type=EVENEMENT_TACHE_STATUT,
        run_id=run_id,
        tache_id=tache_id,
        titre=f"Tâche {tache_id}",
        agent=agent,
        role=role,
        statut=statut,
        projet_id=PROJET,
        horodatage=horodatage,
        **champs,
    )


def geste(
    tache_id: str,
    detail: str,
    *,
    horodatage: str = T1,
    agent: str = "developpeur",
    role: str = "Développeur",
    run_id: str = RUN,
    type: str = EVENEMENT_AGENT_ACTIVITE,
) -> Event:
    """L'`agent.activite` qu'une salve du fournisseur consigne pendant la tâche (#479)."""
    return Event(
        type=type,
        run_id=run_id,
        tache_id=tache_id,
        titre=f"Tâche {tache_id}",
        agent=agent,
        role=role,
        statut="activite",
        detail=detail,
        projet_id=PROJET,
        horodatage=horodatage,
    )


def releve(tache_id: str, usage: StepUsage, *, horodatage: str = T1) -> Event:
    """Le `tache.usage` qu'un relevé en cours de tâche produit (#835)."""
    return Event(
        type=EVENEMENT_TACHE_USAGE,
        run_id=RUN,
        tache_id=tache_id,
        agent="developpeur",
        role="Développeur",
        usage=usage,
        cout_usd=usage.cout_usd,
        projet_id=PROJET,
        horodatage=horodatage,
    )


def lancement(run_id: str = RUN) -> Event:
    return Event(
        type=EVENEMENT_EXECUTION_STATUT,
        run_id=run_id,
        statut=EXECUTION_EN_COURS,
        titre="Objectif",
        projet_id=PROJET,
        horodatage=T0,
    )


def plan_publie(*identifiants: str) -> Event:
    return Event(
        type=EVENEMENT_RUN_PLAN,
        run_id=RUN,
        plan=[NoeudPlan(id=i, titre=f"Tâche {i}") for i in identifiants],
        projet_id=PROJET,
        horodatage=T0,
    )


def projection(*evenements: Event) -> ControlTowerState:
    state = ControlTowerState()
    for event in evenements:
        state.appliquer(event)
    return state


def entree(rang: int, event: Event) -> EntreeJournal:
    """L'entrée de journal requêtable que la pompe consignerait pour `event`."""
    return EntreeJournal(
        rang=rang,
        type=event.type,
        run_id=event.run_id,
        tache_id=event.tache_id,
        titre=event.titre,
        agent=event.agent,
        role=event.role,
        statut=event.statut,
        detail=event.detail,
        projet_id=event.projet_id,
        horodatage=event.horodatage,
    )


def client_sur(*evenements: Event, state: ControlTowerState | None = None) -> TestClient:
    """L'app réelle, bus mémoire, historique rejoué par le lifespan.

    Le rejeu alimente **la projection et le journal requêtable** — c'est ce qui
    permet aux trois vues de se composer sans qu'aucun test n'ait à remplir l'un
    ou l'autre à la main. `state` peut être fourni pour lui appliquer un
    événement **entre deux lectures**, sans passer par le journal.
    """
    log = InMemoryEventLog()
    for event in evenements:
        asyncio.run(log.consigner(event))
    return TestClient(
        create_app(bus=InMemoryEventBus(), state=state or ControlTowerState(), event_log=log)
    )


def signe(horodatage: str = T1, libelle: str = "Écrit api/contacts.py") -> dict[str, str]:
    return {"horodatage": horodatage, "libelle": libelle}


# ------------------------------------------ ① La forme du signe : un instant, un libellé court


def test_le_libelle_est_la_premiere_ligne_non_vide_blancs_replies():
    """Une salve (#479) fait plusieurs lignes, parfois longues : le signe n'en
    garde que de quoi reconnaître le geste."""
    assert libelle_court("\n\n  Écrit   api/contacts.py  \nputs relit\n") == "Écrit api/contacts.py"
    assert libelle_court("   \n\t\n") == ""


def test_la_troncature_se_dit_et_ne_depasse_jamais_la_largeur():
    """Une ligne coupée en silence se lirait comme la phrase entière."""
    long = "x" * (LARGEUR_LIBELLE + 20)

    court = libelle_court(long)

    assert len(court) == LARGEUR_LIBELLE
    assert court.endswith(ELLIPSE)
    # Le motif est prouvé sur un cas qui tient : pas d'ellipse ajoutée pour rien.
    assert libelle_court("x" * LARGEUR_LIBELLE) == "x" * LARGEUR_LIBELLE


def test_le_signe_reprend_l_instant_de_l_evenement_tel_quel():
    """C'est cet horodatage qu'une vue rend en « il y a 12 s », et lui qui prouve
    que deux lectures espacées d'un geste ne rendent pas la même chose."""
    vie = SigneDeVie.depuis(geste("t1", "Écrit api/contacts.py\npuis relit", horodatage=T1))

    assert vie == SigneDeVie(horodatage=T1, libelle="Écrit api/contacts.py")
    assert vie.to_dict() == signe()


def test_a_instant_egal_le_premier_reste():
    """Les horodatages sont à la seconde : un signe qui changerait de tâche sans
    qu'aucun geste ait eu lieu ferait sauter le libellé sous les yeux."""
    premier = SigneDeVie(horodatage=T1, libelle="a")
    meme_seconde = SigneDeVie(horodatage=T1, libelle="b")
    plus_tard = SigneDeVie(horodatage=T2, libelle="c")

    assert premier.plus_recent_que(None)
    assert plus_tard.plus_recent_que(premier)
    assert not meme_seconde.plus_recent_que(premier)
    assert not premier.plus_recent_que(plus_tard)


def test_le_signe_survit_au_rejeu_du_journal_durable():
    """Ce qui traverse le bus revient identique (#97) : le signe est reconstruit
    du `detail` et de l'`horodatage`, qui font l'aller-retour JSON."""
    event = geste("t1", "Écrit api/contacts.py", horodatage=T1)

    relu = Event.from_dict(json.loads(json.dumps(event.to_dict(), ensure_ascii=False)))

    assert SigneDeVie.depuis(relu) == SigneDeVie.depuis(event)


# ------------------------------------------ ② La règle vit chez la projection, une fois


def test_echantillon_fautif_une_tache_en_cours_sans_geste_ne_porte_aucun_signe():
    """La forme d'**avant** #836 — et celle d'une tâche qui vient de démarrer :
    `activite: null` sur une carte `en_cours`. C'est sur elle que le contrôle
    « la carte en cours porte un signe » rougit ; les tests suivants ne concluent
    qu'après l'avoir vu rougir ici."""
    state = projection(lancement(), tache("t1", STATUT_EN_COURS))
    t1 = state.tache("t1")

    assert t1 is not None and t1.statut == STATUT_EN_COURS
    assert t1.activite is None
    assert t1.signe_de_vie is None
    assert t1.to_dict()["activite"] is None


def test_le_dernier_geste_devient_le_signe_de_la_tache_en_cours():
    state = projection(
        lancement(),
        tache("t1", STATUT_EN_COURS),
        geste("t1", "Lit maestro/engine/loop.py", horodatage=T1),
        geste("t1", "Écrit api/contacts.py", horodatage=T2),
    )
    t1 = state.tache("t1")

    assert t1 is not None
    assert t1.signe_de_vie == SigneDeVie(horodatage=T2, libelle="Écrit api/contacts.py")
    assert t1.to_dict()["activite"] == signe(T2)


@pytest.mark.parametrize(
    "statut", [STATUT_TERMINEE, STATUT_ECHEC, STATUT_BLOQUEE, "assignee", "reassignee"]
)
def test_une_tache_arretee_garde_son_dernier_geste_mais_n_en_montre_rien(statut):
    """« Ça bouge » ne se dit pas d'une chose qui ne bouge plus — quel qu'ait été
    le dernier geste de son agent."""
    state = projection(
        lancement(),
        tache("t1", STATUT_EN_COURS),
        geste("t1", "Écrit api/contacts.py", horodatage=T1),
        tache("t1", statut, horodatage=T3),
    )
    t1 = state.tache("t1")

    assert t1 is not None and t1.statut == statut
    assert t1.activite == SigneDeVie(horodatage=T1, libelle="Écrit api/contacts.py")
    assert t1.signe_de_vie is None
    assert t1.to_dict()["activite"] is None


def test_l_attente_de_validation_ne_travaille_pas_meme_si_son_compartiment_dit_en_cours():
    """`en_cours` **au sens strict**, et non le compartiment de `progression.py`,
    qui range l'attente de validation avec `en_cours` pour une barre de
    progression. Une tâche arrêtée sur un humain n'avance pas : un signe de vie
    dessus redirait « ça bouge » là où la vérité est « ça attend » — la
    distinction même que #355 a fait exister."""
    # Le motif est prouvé : le compartiment partagé range bien l'attente en cours.
    assert compartiment(STATUT_EN_ATTENTE_VALIDATION) == EN_COURS

    state = projection(
        lancement(),
        tache("t1", STATUT_EN_COURS),
        geste("t1", "Prépare le déploiement", horodatage=T1),
        tache("t1", STATUT_EN_ATTENTE_VALIDATION, horodatage=T2),
    )
    t1 = state.tache("t1")

    assert t1 is not None and t1.activite is not None
    assert t1.signe_de_vie is None


def test_le_signe_reprend_des_que_la_tache_retravaille():
    """Le geste d'avant l'arrêt reste en mémoire ; c'est le statut qui décide."""
    state = projection(
        lancement(),
        tache("t1", STATUT_EN_COURS),
        geste("t1", "Premier jet", horodatage=T1),
        tache("t1", "assignee", horodatage=T2),
        tache("t1", STATUT_EN_COURS, horodatage=T3),
    )
    t1 = state.tache("t1")

    assert t1 is not None
    assert t1.signe_de_vie == SigneDeVie(horodatage=T1, libelle="Premier jet")


@pytest.mark.parametrize(
    ("type", "statut"),
    [
        (EVENEMENT_MESSAGE_INTER_AGENTS, "envoye"),
        (EVENEMENT_TACHE_BLOCAGE, STATUT_BLOCAGE_SIGNALE),
    ],
)
def test_un_message_ou_un_blocage_declare_rafraichit_aussi_le_signe(type, statut):
    """Tout ce que la projection tient déjà pour « l'agent vient de parler »
    (`_applique_activite`) rafraîchit le signe de sa tâche — un blocage déclaré
    (#719) est le geste le plus délibéré qu'un agent fasse en travaillant."""
    state = projection(
        lancement(),
        tache("t1", STATUT_EN_COURS),
        Event(
            type=type,
            run_id=RUN,
            tache_id="t1",
            agent="developpeur",
            role="Développeur",
            statut=statut,
            detail="le dépôt de recette refuse mes identifiants",
            projet_id=PROJET,
            horodatage=T2,
        ),
    )
    t1 = state.tache("t1")

    assert t1 is not None and t1.statut == STATUT_EN_COURS
    assert t1.signe_de_vie == SigneDeVie(
        horodatage=T2, libelle="le dépôt de recette refuse mes identifiants"
    )


def test_un_signe_n_ouvre_jamais_de_carte():
    """Une activité sans tâche (planification, reprise) ne rafraîchit que
    l'agent ; une activité sur une tâche inconnue ne la crée pas — le moteur
    consigne toujours `:debut` avant le premier geste, et une carte née d'un
    geste serait une carte sans statut au Kanban."""
    state = projection(
        lancement(),
        geste("", "2 tâche(s) planifiée(s)", agent="orchestrateur", role="Orchestrateur"),
        geste("t-fantome", "Écrit quelque chose"),
    )

    assert state.tache("t-fantome") is None
    assert state.tache("") is None
    assert state.taches_du_run(RUN) == frozenset()


def test_rejouer_le_meme_geste_rend_le_meme_signe():
    """Idempotent, donc rejouable : le journal durable reconstruit le signe à
    l'identique au redémarrage de l'API (#97)."""
    un = geste("t1", "Écrit api/contacts.py", horodatage=T1)
    state = projection(lancement(), tache("t1", STATUT_EN_COURS), un, Event.from_dict(un.to_dict()))
    t1 = state.tache("t1")

    assert t1 is not None
    assert t1.signe_de_vie == SigneDeVie(horodatage=T1, libelle="Écrit api/contacts.py")


def test_les_signes_du_run_sont_ceux_de_ses_agents_qui_travaillent():
    """`signes_de_vie_du_run` : un agent y figure s'il porte une tâche du run qui
    travaille, et un agent dont toutes les tâches sont arrêtées n'y est pas —
    la frise le lit comme « aucun signe », ce qui est la vérité d'un couloir
    arrêté."""
    state = projection(
        lancement(),
        tache("t1", STATUT_EN_COURS, agent="developpeur"),
        geste("t1", "Écrit api/contacts.py", horodatage=T1),
        tache("t2", STATUT_EN_COURS, agent="qa", role="Testeur"),
        geste("t2", "Lance la suite", agent="qa", role="Testeur", horodatage=T1),
        tache("t2", STATUT_TERMINEE, agent="qa", role="Testeur", horodatage=T2),
        tache("t3", STATUT_EN_COURS, agent="devops", role="DevOps"),
    )

    signes = state.signes_de_vie_du_run(RUN)

    assert signes == {"developpeur": SigneDeVie(horodatage=T1, libelle="Écrit api/contacts.py")}
    assert state.signes_de_vie_du_run("run-jamais-vu") == {}


def test_en_multi_instances_le_couloir_retient_le_plus_recent_de_ses_taches():
    """Un agent porte plusieurs tâches à la fois (#100) et le couloir n'a qu'un
    en-tête : c'est le plus récent des gestes qui le représente, et à instant
    égal le premier reste."""
    state = projection(
        lancement(),
        tache("t1", STATUT_EN_COURS),
        tache("t2", STATUT_EN_COURS),
        tache("t3", STATUT_EN_COURS),
        geste("t1", "Ancien", horodatage=T1),
        geste("t2", "Récent", horodatage=T2),
        geste("t3", "Même seconde que t2", horodatage=T2),
    )

    assert state.signes_de_vie_du_run(RUN) == {
        "developpeur": SigneDeVie(horodatage=T2, libelle="Récent")
    }


def test_le_signe_d_un_autre_run_ne_traverse_pas():
    state = projection(
        lancement(),
        lancement("run-voisin"),
        tache("t9", STATUT_EN_COURS, run_id="run-voisin"),
        geste("t9", "Ailleurs", run_id="run-voisin"),
    )

    assert state.signes_de_vie_du_run(RUN) == {}
    assert list(state.signes_de_vie_du_run("run-voisin")) == ["developpeur"]


# ------------------------------------------ ③ Le nœud du graphe lit la décision, ne la refait pas


def test_echantillon_fautif_le_noeud_en_cours_sans_geste_est_immobile():
    """Le graphe d'**avant** #836 : sur le nœud `en_cours`, rien qui change entre
    deux lectures — quatre nœuds strictement inchangés sur 60 s, mesurés. Le
    contrôle du test suivant rougit ici."""
    state = projection(lancement(), plan_publie("t1", "t2"), tache("t1", STATUT_EN_COURS))

    avant = state.graphe(RUN).to_dict()
    apres = state.graphe(RUN).to_dict()

    assert avant == apres
    assert avant["noeuds"][0]["statut"] == STATUT_EN_COURS
    assert avant["noeuds"][0]["activite"] is None


def test_deux_lectures_espacees_d_un_geste_rendent_deux_noeuds_differents():
    """Le premier critère de #836, sur la seule chose qui bouge d'une boîte en
    cours : son signe de vie."""
    state = projection(lancement(), plan_publie("t1", "t2"), tache("t1", STATUT_EN_COURS))
    state.appliquer(geste("t1", "Lit maestro/engine/loop.py", horodatage=T1))
    premiere = state.graphe(RUN).to_dict()
    state.appliquer(geste("t1", "Écrit api/contacts.py", horodatage=T2))
    seconde = state.graphe(RUN).to_dict()

    assert premiere["noeuds"][0]["activite"] == signe(T1, "Lit maestro/engine/loop.py")
    assert seconde["noeuds"][0]["activite"] == signe(T2)
    assert premiere != seconde
    # Rien d'autre n'a bougé sur le nœud : le signe est **la** différence.
    for noeud in (premiere["noeuds"][0], seconde["noeuds"][0]):
        noeud["activite"] = None
    assert premiere == seconde


def test_le_noeud_qui_ne_travaille_pas_rend_null_quel_que_soit_son_dernier_geste():
    state = projection(
        lancement(),
        plan_publie("t1", "t2"),
        tache("t1", STATUT_EN_COURS),
        geste("t1", "Écrit api/contacts.py", horodatage=T1),
        tache("t1", STATUT_TERMINEE, horodatage=T2),
    )

    par_id = {noeud["id"]: noeud for noeud in state.graphe(RUN).to_dict()["noeuds"]}

    assert par_id["t1"]["statut"] == STATUT_TERMINEE
    assert par_id["t1"]["activite"] is None
    assert par_id["t2"]["activite"] is None  # pas démarré : rien à montrer non plus


def test_le_graphe_transporte_le_signe_tel_que_la_projection_l_a_tranche():
    """`graphe_du_run` est une feuille : il ne rejoue pas la règle, il rend ce
    qu'on lui passe — y compris, si on le lui passait, un signe sur un nœud
    arrêté. C'est la projection qui ne le lui passe jamais."""
    vie = SigneDeVie(horodatage=T1, libelle="Écrit api/contacts.py")
    graphe = graphe_du_run(
        RUN,
        [NoeudPlan(id="t1"), NoeudPlan(id="t2")],
        {"t1": EtatNoeud(statut=STATUT_EN_COURS, activite=vie), "t2": EtatNoeud()},
    )

    assert graphe.noeuds[0].activite == vie
    assert graphe.noeuds[0].to_dict()["activite"] == signe()
    assert graphe.noeuds[1].to_dict()["activite"] is None


# ------------------------------------------ ④ Le couloir de la frise : un attribut, pas une entrée


def _matiere() -> tuple[ControlTowerState, list[EntreeJournal]]:
    """Un run tel que la pompe le laisse : la projection **et** le journal
    requêtable, où les gestes de l'agent sont consignés comme le reste."""
    evenements = [
        lancement(),
        tache("t1", STATUT_EN_COURS, agent="developpeur"),
        geste("t1", "Lit maestro/engine/loop.py", horodatage=T1),
        geste("t1", "Écrit api/contacts.py", horodatage=T2),
        tache("t2", STATUT_EN_COURS, agent="qa", role="Testeur"),
        geste("t2", "Lance la suite", agent="qa", role="Testeur", horodatage=T1),
        tache("t2", STATUT_TERMINEE, agent="qa", role="Testeur", horodatage=T2),
    ]
    state = projection(*evenements)
    entrees = [entree(rang, event) for rang, event in enumerate(evenements, start=1)]
    return state, entrees


def test_echantillon_fautif_le_couloir_en_cours_compose_sans_signe_n_en_porte_pas():
    """La frise d'**avant** #836 — celle qu'un appelant compose **sans**
    `activites` : le couloir dont la tâche travaille rend `activite: null`,
    comme le couloir arrêté, et rien ne les distingue. 1158 octets identiques à
    90 s d'intervalle, mesurés. Le contrôle du test suivant rougit ici."""
    state, entrees = _matiere()

    frise = frise_du_run(RUN, entrees, agents=state.agents_du_run(RUN))

    par_agent = {couloir.agent: couloir for couloir in frise.couloirs}
    assert state.tache("t1").statut == STATUT_EN_COURS  # type: ignore[union-attr]
    assert par_agent["developpeur"].activite is None
    assert par_agent["qa"].activite is None


def test_le_couloir_dont_la_tache_travaille_porte_le_signe_et_l_autre_non():
    state, entrees = _matiere()

    frise = frise_du_run(
        RUN,
        entrees,
        agents=state.agents_du_run(RUN),
        activites=state.signes_de_vie_du_run(RUN),
    )

    par_agent = {couloir.agent: couloir for couloir in frise.couloirs}
    assert par_agent["developpeur"].activite == SigneDeVie(
        horodatage=T2, libelle="Écrit api/contacts.py"
    )
    assert par_agent["qa"].activite is None
    assert par_agent["developpeur"].to_dict()["activite"] == signe(T2)
    assert par_agent["qa"].to_dict()["activite"] is None


def test_le_signe_est_un_attribut_de_l_en_tete_et_la_frise_sans_lui_est_celle_d_avant():
    """`entrees` ne reçoit **aucune** entrée d'activité : la frise avec son signe
    et la frise sans sont identiques au JSON près, une fois l'attribut retiré —
    c'est ce qui permet de montrer l'activité **sans défaire le tri**."""
    state, entrees = _matiere()
    agents = state.agents_du_run(RUN)

    sans = frise_du_run(RUN, entrees, agents=agents).to_dict()
    avec = frise_du_run(
        RUN, entrees, agents=agents, activites=state.signes_de_vie_du_run(RUN)
    ).to_dict()

    assert avec != sans
    for couloir in avec["couloirs"]:
        couloir["activite"] = None
    assert avec == sans
    # Les gestes sont bien dans la matière d'entrée ; aucun n'est sorti en entrée.
    assert any(e.type == EVENEMENT_AGENT_ACTIVITE for e in entrees)
    assert all(e["type"] != EVENEMENT_AGENT_ACTIVITE for e in avec["entrees"])
    assert [e["id"] for e in avec["entrees"]] == ["j-0002", "j-0005", "j-0007"]
    assert avec["total"] == 3


def test_un_signe_sans_couloir_n_en_ouvre_aucun_et_le_repli_n_en_porte_jamais():
    """Un signe sans couloir dirait « ça bouge » de quelqu'un qui n'est pas dans
    le run ; le repli n'a pas d'agent, donc pas de tâche qui travaille."""
    vie = SigneDeVie(horodatage=T1, libelle="Écrit")
    entrees = [
        entree(1, tache("t1", STATUT_EN_COURS, agent="developpeur")),
        entree(2, tache("t2", STATUT_BLOQUEE, agent="—", role="non exécutée")),
    ]

    frise = frise_du_run(
        RUN, entrees, activites={"inconnu": vie, "—": vie, "": vie, "developpeur": vie}
    )

    assert [c.agent for c in frise.couloirs] == ["developpeur", COULOIR_REPLI]
    assert frise.couloirs[0].activite == vie
    assert frise.couloirs[1].activite is None


def test_le_signe_passe_par_la_meme_normalisation_que_les_entrees_et_le_plus_recent_gagne():
    """Deux façons de nommer un agent ne font pas deux couloirs ici plus
    qu'ailleurs ; si deux clés tombent sur le même couloir, le plus récent reste."""
    entrees = [entree(1, tache("t1", STATUT_EN_COURS, agent="developpeur"))]

    frise = frise_du_run(
        RUN,
        entrees,
        activites={
            " developpeur ": SigneDeVie(horodatage=T2, libelle="Récent"),
            "developpeur": SigneDeVie(horodatage=T1, libelle="Ancien"),
        },
    )

    assert frise.couloirs[0].activite == SigneDeVie(horodatage=T2, libelle="Récent")


def test_un_couloir_declare_mais_muet_peut_porter_un_signe():
    """Un agent dont la tâche vient de démarrer a son couloir par la déclaration
    (`agents_du_run`) avant d'avoir une entrée dans la fenêtre du plafond."""
    vie = SigneDeVie(horodatage=T1, libelle="Écrit")

    frise = frise_du_run(
        RUN, [], agents={"developpeur": "Développeur"}, activites={"developpeur": vie}
    )

    assert frise.couloirs == (
        CouloirFrise(agent="developpeur", role="Développeur", entrees=(), activite=vie),
    )


# ------------------------------------------ ⑤ `TYPES_FRISE` est gardé, deux fois


_CONTRAT_FRISE = frozenset(
    {
        EVENEMENT_TACHE_STATUT,
        EVENEMENT_MESSAGE_INTER_AGENTS,
        EVENEMENT_VALIDATION_DEMANDE,
        EVENEMENT_VALIDATION_DECISION,
        EVENEMENT_TACHE_BLOCAGE,
    }
)


def test_types_frise_est_exactement_le_contrat_et_n_ouvre_ni_l_activite_ni_le_releve():
    """La liste est le contrat de ce que la frise montre ; tout le reste du
    journal en est écarté à dessein — `agent.activite` (le bruit de fond, dont le
    signe de vie est la réponse) et `tache.usage` (une jauge, pas un fait)."""
    assert TYPES_FRISE == _CONTRAT_FRISE
    assert EVENEMENT_AGENT_ACTIVITE not in TYPES_FRISE
    assert EVENEMENT_TACHE_USAGE not in TYPES_FRISE


def _types_declares(source: str) -> set[str]:
    """Les noms que le littéral `TYPES_FRISE = frozenset({…})` de `source` déclare."""
    for noeud in ast.walk(ast.parse(source)):
        if not isinstance(noeud, ast.Assign):
            continue
        if not any(isinstance(c, ast.Name) and c.id == "TYPES_FRISE" for c in noeud.targets):
            continue
        appel = noeud.value
        assert isinstance(appel, ast.Call) and isinstance(appel.args[0], ast.Set)
        return {e.id for e in appel.args[0].elts if isinstance(e, ast.Name)}
    raise AssertionError("aucun littéral TYPES_FRISE dans la source")


_NOMS_CONTRAT = {
    "EVENEMENT_TACHE_STATUT",
    "EVENEMENT_MESSAGE_INTER_AGENTS",
    "EVENEMENT_VALIDATION_DEMANDE",
    "EVENEMENT_VALIDATION_DECISION",
    "EVENEMENT_TACHE_BLOCAGE",
}


def test_la_source_de_frise_declare_le_contrat_et_pas_un_type_de_plus():
    """L'écart le plus plausible à la relecture est d'ajouter une ligne au
    littéral — « une entrée par geste, ce serait plus vivant ». La docstring de
    `frise.py` l'écarte explicitement ; ce test le refuse. Le motif est prouvé
    sur un échantillon fautif : la même lecture voit la ligne ajoutée."""
    fautif = "TYPES_FRISE = frozenset({EVENEMENT_TACHE_STATUT, EVENEMENT_AGENT_ACTIVITE})"
    assert "EVENEMENT_AGENT_ACTIVITE" in _types_declares(fautif)

    source = Path(frise_mod.__file__).read_text(encoding="utf-8")

    assert _types_declares(source) == _NOMS_CONTRAT


def test_echantillon_fautif_ouvrir_l_activite_en_bloc_la_verse_dans_les_entrees(monkeypatch):
    """Ce que l'ouverture coûterait, montré plutôt que dit : avec
    `agent.activite` dans l'ensemble, chaque geste devient une **entrée**, le
    tri est noyé (deux gestes pour un statut sur cet échantillon, ~220 pour 3
    sur le run mesuré) et le signe de vie n'a plus d'objet. La liste vraie
    rend `entrees` intact — et c'est le comportement gardé."""
    state, entrees = _matiere()
    agents = state.agents_du_run(RUN)

    monkeypatch.setattr(frise_mod, "TYPES_FRISE", TYPES_FRISE | {EVENEMENT_AGENT_ACTIVITE})
    ouverte = frise_du_run(RUN, entrees, agents=agents)
    assert [e.type for e in ouverte.entrees].count(EVENEMENT_AGENT_ACTIVITE) == 3
    assert ouverte.total == 6

    monkeypatch.setattr(frise_mod, "TYPES_FRISE", TYPES_FRISE)
    tenue = frise_du_run(RUN, entrees, agents=agents)
    assert all(e.type != EVENEMENT_AGENT_ACTIVITE for e in tenue.entrees)
    assert tenue.total == 3


# ------------------------------------------ ⑥ Le coût en vol : `null` n'est pas une lecture unique


def test_echantillon_fautif_une_tache_en_cours_sans_releve_reste_a_null_non_partiel():
    """La carte d'**avant** #835 : `cout_usd: null`, `cout_partiel: false` sur une
    tâche qui travaille depuis douze minutes — 5,26 $ inchangés sur le run,
    mesurés. C'est l'état qu'un contrôle « le coût en vol se voit » doit rougir,
    et c'est aussi celui d'une tâche avant son premier relevé : seul le relevé
    distingue les deux."""
    state = projection(lancement(), tache("t1", STATUT_EN_COURS))
    t1 = state.tache("t1")
    run = state.execution(RUN)

    assert t1 is not None and run is not None
    assert (t1.cout_usd, t1.cout_partiel) == (None, False)
    assert (run.cout_usd, run.cout_partiel) == (None, False)


def test_les_trois_lectures_de_la_carte_se_distinguent_deux_a_deux():
    """`0` partiel (rien consommé, mesuré), `null` partiel (consommé, pas encore
    tarifé — les tokens sont dans `usage`), `null` non partiel (inconnu) : trois
    états, trois paires de valeurs distinctes sur la carte servie."""
    state = projection(lancement(), tache("t1", STATUT_EN_COURS))
    inconnu = state.tache("t1").to_dict()  # type: ignore[union-attr]

    state.appliquer(releve("t1", StepUsage(cout_usd=0.0)))
    rien_encore = state.tache("t1").to_dict()  # type: ignore[union-attr]

    state.appliquer(releve("t1", StepUsage(tokens_entree=5000, tours=2), horodatage=T2))
    pas_tarife = state.tache("t1").to_dict()  # type: ignore[union-attr]

    lectures = [(d["cout_usd"], d["cout_partiel"]) for d in (inconnu, rien_encore, pas_tarife)]
    assert lectures == [(None, False), (0.0, True), (None, True)]
    assert len(set(lectures)) == 3
    assert pas_tarife["usage"]["tokens_entree"] == 5000


def test_le_cumul_du_run_bouge_entre_deux_lectures_pendant_qu_une_tache_travaille():
    """Le second critère de #835, lu sur le résumé que la liste des runs sert."""
    state = projection(
        lancement(),
        tache("t0", STATUT_TERMINEE, usage=StepUsage(appels=1, cout_usd=0.2), cout_usd=0.2),
        tache("t1", STATUT_EN_COURS),
    )
    premiere = state.execution(RUN).resume()  # type: ignore[union-attr]
    state.appliquer(releve("t1", StepUsage(tokens_entree=9000, cout_usd=0.3)))
    seconde = state.execution(RUN).resume()  # type: ignore[union-attr]

    assert (premiere["cout_usd"], premiere["cout_partiel"]) == (pytest.approx(0.2), False)
    assert (seconde["cout_usd"], seconde["cout_partiel"]) == (pytest.approx(0.5), True)
    # Le nœud du graphe porte la même réserve que la carte.
    assert state.graphe(RUN).to_dict()["noeuds"][1]["cout_partiel"] is True


# ------------------------------------------ La route : le même signe aux trois endroits


def _run_qui_travaille() -> list[Event]:
    return [
        lancement(),
        plan_publie("t1", "t2"),
        tache("t1", STATUT_EN_COURS, agent="developpeur"),
        geste("t1", "Lit maestro/engine/loop.py", horodatage=T1),
        geste("t1", "Écrit api/contacts.py", horodatage=T2),
        tache("t2", STATUT_EN_COURS, agent="qa", role="Testeur"),
        geste("t2", "Lance la suite", agent="qa", role="Testeur", horodatage=T1),
        tache("t2", STATUT_TERMINEE, agent="qa", role="Testeur", horodatage=T2),
    ]


def test_la_carte_le_noeud_et_le_couloir_servent_le_meme_signe():
    """Le critère « le même signe de vie » tient par construction — une règle,
    trois lecteurs —, et c'est ce que la route prouve : trois lectures, une
    valeur, sur la tâche qui travaille ; `null` partout ailleurs."""
    with client_sur(*_run_qui_travaille()) as client:
        cartes = {t["id"]: t for t in client.get("/api/taches?projet=tous").json()}
        noeuds = {n["id"]: n for n in client.get(f"/api/executions/{RUN}/graphe").json()["noeuds"]}
        frise = client.get(f"/api/executions/{RUN}/frise").json()

    couloirs = {c["agent"]: c for c in frise["couloirs"]}
    attendu = signe(T2)
    assert cartes["t1"]["activite"] == attendu
    assert noeuds["t1"]["activite"] == attendu
    assert couloirs["developpeur"]["activite"] == attendu
    assert cartes["t2"]["activite"] is None
    assert noeuds["t2"]["activite"] is None
    assert couloirs["qa"]["activite"] is None


def test_la_route_ne_verse_aucun_geste_dans_les_entrees_de_la_frise():
    """Les gestes sont dans le journal requêtable (qui garde tout) ; la frise
    n'en rend aucun comme entrée, et ses types restent ceux du contrat."""
    with client_sur(*_run_qui_travaille()) as client:
        frise = client.get(f"/api/executions/{RUN}/frise").json()
        page = client.get(f"/api/journal?run_id={RUN}&projet=tous&taille=200").json()

    assert any(e["type"] == EVENEMENT_AGENT_ACTIVITE for e in page["entrees"])
    assert {e["type"] for e in frise["entrees"]} <= TYPES_FRISE
    assert [e["type"] for e in frise["entrees"]] == [
        EVENEMENT_TACHE_STATUT,
        EVENEMENT_TACHE_STATUT,
        EVENEMENT_TACHE_STATUT,
    ]
    assert frise["total"] == 3


def test_le_signe_se_recompose_a_la_lecture_sans_evenement_a_lui():
    """Rien n'a été ajouté au canal : un geste appliqué à la projection entre
    deux lectures change le signe des trois vues — et **rien d'autre** de la
    frise, dont les entrées viennent du journal, qui n'a pas bougé."""
    state = ControlTowerState()
    with client_sur(*_run_qui_travaille(), state=state) as client:
        avant = client.get(f"/api/executions/{RUN}/frise").json()
        noeud_avant = client.get(f"/api/executions/{RUN}/graphe").json()["noeuds"][0]
        state.appliquer(geste("t1", "Relit le résultat", horodatage=T3))
        apres = client.get(f"/api/executions/{RUN}/frise").json()
        noeud_apres = client.get(f"/api/executions/{RUN}/graphe").json()["noeuds"][0]
        carte = client.get("/api/taches?projet=tous").json()[0]

    assert avant["couloirs"][0]["activite"] == signe(T2)
    assert apres["couloirs"][0]["activite"] == signe(T3, "Relit le résultat")
    assert noeud_avant["activite"] == signe(T2)
    assert noeud_apres["activite"] == carte["activite"] == signe(T3, "Relit le résultat")
    assert apres["entrees"] == avant["entrees"]


def test_un_run_solde_rend_exactement_la_vue_d_avant():
    """Le second critère de #837, côté contrat : aucune tâche ne travaille,
    aucun signe nulle part — carte, nœud, couloir —, quels qu'aient été les
    gestes consignés."""
    solde = [*_run_qui_travaille(), tache("t1", STATUT_TERMINEE, horodatage=T3)]
    with client_sur(*solde) as client:
        cartes = client.get("/api/taches?projet=tous").json()
        noeuds = client.get(f"/api/executions/{RUN}/graphe").json()["noeuds"]
        couloirs = client.get(f"/api/executions/{RUN}/frise").json()["couloirs"]

    assert all(t["activite"] is None for t in cartes)
    assert all(n["activite"] is None for n in noeuds)
    assert all(c["activite"] is None for c in couloirs)
    assert all(t["cout_partiel"] is False for t in cartes)
