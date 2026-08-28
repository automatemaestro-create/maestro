"""La **frise d'activité d'un run** : ce que les agents font et se disent (#355).

Pendant un run, on ne voyait pas ce qui se passe : deux compteurs, puis le
rapport à la fin. Entre les deux, **une attente de décision humaine était
indiscernable d'un travail en cours** — 53 minutes perdues le 14 août sans
qu'aucun écran ne le dise. Ces tests gardent la lecture qui manquait, et ils sont
écrits dans l'ordre où la donnée remonte :

① **La résolution d'une entrée** — ce qu'une ligne de journal devient sur la
   frise. Le point sensible n'est pas le passage à plat : c'est que le moteur
   consigne `agent="—"` sur une tâche **jamais routée** (`_consigne_blocage`,
   #43), donc que le nom d'agent le plus fréquent d'un run en panne n'est pas un
   agent. Un couloir « — » serait absurde, et ce sont exactement les entrées du
   troisième critère.

② **Le tri** — par instant, départagé par le **rang** du journal. Les
   horodatages du dépôt sont à la seconde (`Event`, `StepRecord`,
   `AgentMessage`) : sur un run parallèle deux entrées portent couramment le
   même instant, et un tri instable ferait sauter des lignes d'un
   rafraîchissement à l'autre. Le test le prouve en **présentant les mêmes
   entrées dans deux ordres** et en exigeant la même frise.

③ **Les couloirs** — un par agent du run, muet compris, repli en dernier, et
   l'invariant que le critère écrit en toutes lettres : **aucune entrée n'est
   jamais perdue faute de couloir**. Il se vérifie par un ensemble, jamais par
   un décompte : compter laisserait passer une entrée rangée dans un couloir qui
   n'est pas servi.

④ **Les trois états à l'œil** — bloquée, en attente de validation, en cours. Le
   moteur n'émet pas `en_attente_validation` ; la frise le résout depuis
   `validation.demande`, qui **est** l'instant où la tâche s'arrête sur un
   humain. C'est le cas d'usage qui a motivé le ticket, donc le test le pose
   comme tel : trois statuts distincts, sur le même run.

⑤ **Le plafond** — il retient les entrées les plus récentes et **le dit**
   (`total`, `tronquee`) : une borne muette ferait passer un run d'une heure
   pour un run de cinq cents lignes.

⑥ **La route** — `GET /api/executions/{run_id}/frise`, son 404, et le fait que
   rien n'est créé : chaque entrée garde l'identifiant que
   `GET /api/journal?run_id=…` lui donne, si bien que les deux lectures ne
   peuvent pas se contredire.

**Ni Redis, ni réseau, ni appel modèle** : l'app est la vraie (`create_app`) sur
bus mémoire, alimentée par des événements posés à la main — ce que la pompe lui
livre en production.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from maestro.controltower import (
    ControlTowerState,
    Event,
    InMemoryEventBus,
    InMemoryEventLog,
    create_app,
)
from maestro.controltower.events import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DECISION,
    EVENEMENT_VALIDATION_DEMANDE,
)
from maestro.controltower.frise import (
    AGENT_ABSENT,
    COULOIR_REPLI,
    PLAFOND_FRISE,
    EntreeFrise,
    frise_du_run,
)
from maestro.controltower.journal import EntreeJournal, ServiceJournal
from maestro.controltower.progression import STATUT_EN_ATTENTE_VALIDATION
from maestro.controltower.state import (
    EVENEMENT_EXECUTION_STATUT,
    EXECUTION_EN_COURS,
    VALIDATION_APPROUVEE,
    VALIDATION_EN_ATTENTE,
    VALIDATION_REFUSEE,
)
from maestro.engine.executor import (
    STATUT_BLOQUEE,
    STATUT_EN_COURS,
    STATUT_TERMINEE,
    STATUT_VALIDATION_APPROUVE,
    STATUT_VALIDATION_REFUSE,
)

RUN = "run-frise"
PROJET = "prj-0001"


# ------------------------------------------------------------------ harnais


def _entree(
    rang: int,
    type: str,
    *,
    horodatage: str = "2026-08-28T10:00:00+00:00",
    agent: str = "developpeur",
    role: str = "Développeur",
    tache_id: str = "t1",
    titre: str = "",
    statut: str = "",
    detail: str = "",
    run_id: str = RUN,
) -> EntreeJournal:
    """Une entrée de journal telle que la pompe la consigne."""
    return EntreeJournal(
        rang=rang,
        type=type,
        run_id=run_id,
        tache_id=tache_id,
        titre=titre or f"Tâche {tache_id}",
        agent=agent,
        role=role,
        statut=statut,
        detail=detail,
        projet_id=PROJET,
        horodatage=horodatage,
    )


def _frise(*entrees: EntreeJournal, **kwargs):
    return frise_du_run(RUN, entrees, **kwargs)


def _identifiants(frise) -> list[str]:
    return [entree.id for entree in frise.entrees]


def _couloirs(frise) -> list[str]:
    return [couloir.agent for couloir in frise.couloirs]


def tache(
    tache_id: str,
    statut: str,
    *,
    agent: str = "developpeur",
    role: str = "Développeur",
    run_id: str = RUN,
    horodatage: str = "",
) -> Event:
    """L'événement qui fait exister une tâche dans la projection — et dans un run."""
    champs = {"horodatage": horodatage} if horodatage else {}
    return Event(
        type=EVENEMENT_TACHE_STATUT,
        run_id=run_id,
        tache_id=tache_id,
        titre=f"Tâche {tache_id}",
        agent=agent,
        role=role,
        statut=statut,
        projet_id=PROJET,
        **champs,
    )


def message(de: str, phrase: str, *, tache_id: str = "t1", run_id: str = RUN) -> Event:
    """Le message inter-agents tel que `consigne_message` le fait remonter (#44)."""
    return Event(
        type=EVENEMENT_MESSAGE_INTER_AGENTS,
        run_id=run_id,
        tache_id=tache_id,
        agent=de,
        role="Développeur",
        statut="envoye",
        detail=phrase,
        projet_id=PROJET,
    )


def demande_validation(tache_id: str, *, agent: str = "devops") -> Event:
    return Event(
        type=EVENEMENT_VALIDATION_DEMANDE,
        run_id=RUN,
        tache_id=tache_id,
        titre=f"Tâche {tache_id}",
        agent=agent,
        role="DevOps",
        statut=VALIDATION_EN_ATTENTE,
        detail="déploiement en production",
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


def client_sur(*evenements: Event) -> TestClient:
    """L'app réelle, bus mémoire, historique rejoué par le lifespan.

    Le rejeu alimente **la projection et le journal requêtable** (`app`, lifespan)
    — c'est ce qui permet à la frise de se composer sans qu'aucun test n'ait à
    remplir l'un ou l'autre à la main.
    """
    log = InMemoryEventLog()
    for event in evenements:
        asyncio.run(log.consigner(event))
    return TestClient(
        create_app(bus=InMemoryEventBus(), state=ControlTowerState(), event_log=log)
    )


# ------------------------------- ① La résolution d'une entrée : le tiret n'est pas un agent


def test_une_entree_de_statut_garde_son_identite():
    """Rien n'est réécrit : c'est ce qui permet de la retrouver dans le journal."""
    entree = EntreeFrise.depuis(
        _entree(7, EVENEMENT_TACHE_STATUT, statut=STATUT_TERMINEE, titre="Écrire les tests")
    )

    assert entree.id == "j-0007"
    assert entree.type == EVENEMENT_TACHE_STATUT
    assert entree.couloir == "developpeur"
    assert entree.tache_id == "t1"
    assert entree.statut == STATUT_TERMINEE
    assert entree.horodatage == "2026-08-28T10:00:00+00:00"


def test_l_objet_retombe_sur_le_titre_quand_le_journal_ne_dit_rien():
    """L'issue **réussie** d'une tâche ne porte aucun détail (`bridge`) : une
    frise dont une ligne sur deux serait muette ne se lirait pas."""
    reussie = EntreeFrise.depuis(
        _entree(1, EVENEMENT_TACHE_STATUT, statut=STATUT_TERMINEE, titre="Écrire les tests")
    )
    echouee = EntreeFrise.depuis(
        _entree(2, EVENEMENT_TACHE_STATUT, titre="Écrire les tests", detail="timeout")
    )

    assert reussie.objet == "Écrire les tests"
    assert echouee.objet == "timeout"


def test_le_tiret_d_une_tache_jamais_routee_n_ouvre_aucun_couloir():
    """`_consigne_blocage` (#43) consigne `agent="—"` : ce n'est pas un agent.

    Sans cette reconnaissance, un run en cascade de blocages ouvrirait un couloir
    nommé « — » où s'entasseraient toutes les tâches jamais exécutées — et ce
    sont précisément les entrées du troisième critère.
    """
    bloquee = _entree(
        1,
        EVENEMENT_TACHE_STATUT,
        agent=AGENT_ABSENT,
        role="non exécutée",
        statut=STATUT_BLOQUEE,
        detail="dépendance(s) non satisfaite(s) : t0 (echec)",
    )

    frise = _frise(bloquee)

    assert _couloirs(frise) == [COULOIR_REPLI]
    assert frise.couloirs[0].repli is True
    assert frise.entrees[0].statut == STATUT_BLOQUEE
    # L'agent brut n'est pas effacé : la ligne dit encore ce que le journal dit.
    assert frise.entrees[0].agent == AGENT_ABSENT


def test_un_agent_absent_ou_entoure_d_espaces_ne_fait_pas_un_couloir_de_plus():
    frise = _frise(
        _entree(1, EVENEMENT_TACHE_STATUT, agent="", statut=STATUT_TERMINEE),
        _entree(2, EVENEMENT_TACHE_STATUT, agent=" developpeur ", statut=STATUT_TERMINEE),
        _entree(3, EVENEMENT_TACHE_STATUT, agent="developpeur", statut=STATUT_TERMINEE),
    )

    assert _couloirs(frise) == ["developpeur", COULOIR_REPLI]


def test_le_bruit_de_fond_d_un_run_reste_dehors():
    """`agent.activite` (relances, refus d'outil, activité) n'est ni un
    changement d'état ni un échange : l'y verser noierait les trois signaux que
    le ticket demande de distinguer. Le journal requêtable reste là pour tout."""
    frise = _frise(
        _entree(1, EVENEMENT_TACHE_STATUT, statut=STATUT_EN_COURS),
        _entree(2, EVENEMENT_AGENT_ACTIVITE, detail="relance 1/3"),
        _entree(3, EVENEMENT_MESSAGE_INTER_AGENTS, detail="handoff de dev à qa : à toi"),
    )

    assert _identifiants(frise) == ["j-0001", "j-0003"]
    assert frise.total == 2


# ------------------------------- ② Le tri : l'instant, départagé par le rang


def test_les_deux_flux_se_melent_dans_l_ordre_du_temps():
    """Le premier critère : une **même** frise, statuts et messages mêlés."""
    frise = _frise(
        _entree(1, EVENEMENT_TACHE_STATUT, horodatage="2026-08-28T10:00:00+00:00"),
        _entree(2, EVENEMENT_MESSAGE_INTER_AGENTS, horodatage="2026-08-28T10:00:02+00:00"),
        _entree(3, EVENEMENT_TACHE_STATUT, horodatage="2026-08-28T10:00:01+00:00"),
    )

    assert _identifiants(frise) == ["j-0001", "j-0003", "j-0002"]
    assert [e.type for e in frise.entrees] == [
        EVENEMENT_TACHE_STATUT,
        EVENEMENT_TACHE_STATUT,
        EVENEMENT_MESSAGE_INTER_AGENTS,
    ]


def test_deux_entrees_du_meme_instant_se_departagent_sur_le_rang():
    """Les horodatages sont à la **seconde** partout : sur un run parallèle, deux
    entrées du même instant sont le cas courant, pas le cas de bord."""
    meme_seconde = "2026-08-28T10:00:00+00:00"
    frise = _frise(
        _entree(3, EVENEMENT_TACHE_STATUT, horodatage=meme_seconde),
        _entree(1, EVENEMENT_TACHE_STATUT, horodatage=meme_seconde),
        _entree(2, EVENEMENT_MESSAGE_INTER_AGENTS, horodatage=meme_seconde),
    )

    assert _identifiants(frise) == ["j-0001", "j-0002", "j-0003"]


def test_l_ordre_ne_depend_pas_de_l_ordre_de_presentation():
    """La propriété que le ticket demande : « déterministe quel que soit l'ordre
    d'arrivée ». Un tri instable ferait sauter des lignes d'un rafraîchissement
    à l'autre — c'est-à-dire pendant qu'on regarde."""
    meme_seconde = "2026-08-28T10:00:00+00:00"
    entrees = [
        _entree(rang, EVENEMENT_TACHE_STATUT, horodatage=meme_seconde, tache_id=f"t{rang}")
        for rang in range(1, 6)
    ]

    endroit = frise_du_run(RUN, entrees)
    envers = frise_du_run(RUN, list(reversed(entrees)))

    assert _identifiants(endroit) == _identifiants(envers)
    assert endroit.to_dict() == envers.to_dict()


# ------------------------------- ③ Les couloirs : un par agent, et rien ne se perd


def test_un_couloir_par_agent_dans_l_ordre_declare():
    frise = _frise(
        _entree(1, EVENEMENT_TACHE_STATUT, agent="qa", role="Testeur"),
        _entree(2, EVENEMENT_TACHE_STATUT, agent="developpeur"),
        agents={"developpeur": "Développeur", "qa": "Testeur"},
    )

    assert _couloirs(frise) == ["developpeur", "qa"]
    assert frise.couloirs[0].entrees == ("j-0002",)
    assert frise.couloirs[1].entrees == ("j-0001",)


def test_un_agent_du_run_encore_muet_a_son_couloir():
    """Une file muette est une information ; un couloir qui apparaîtrait en cours
    de route ne dirait pas s'il était prévu."""
    frise = _frise(
        _entree(1, EVENEMENT_TACHE_STATUT, agent="developpeur"),
        agents={"developpeur": "Développeur", "qa": "Testeur"},
    )

    assert _couloirs(frise) == ["developpeur", "qa"]
    assert frise.couloirs[1].entrees == ()
    assert frise.couloirs[1].role == "Testeur"


def test_un_agent_declare_passe_par_la_meme_normalisation_que_ses_entrees():
    """Sinon il ouvrirait un couloir que ses propres entrées ne rejoindraient
    jamais — et le tiret déclaré ouvrirait le couloir « — » qu'on refuse."""
    frise = _frise(
        _entree(1, EVENEMENT_TACHE_STATUT, agent="developpeur"),
        agents={" developpeur ": "Développeur", AGENT_ABSENT: "non exécutée"},
    )

    assert _couloirs(frise) == ["developpeur"]
    assert frise.couloirs[0].entrees == ("j-0001",)


def test_un_agent_non_declare_ouvre_son_couloir_a_la_suite():
    """La déclaration **ordonne**, elle ne filtre pas : un couloir manquant
    ferait perdre des entrées, ce que le critère interdit."""
    frise = _frise(
        _entree(1, EVENEMENT_TACHE_STATUT, agent="developpeur"),
        _entree(2, EVENEMENT_TACHE_STATUT, agent="orchestrateur", role="Orchestrateur"),
        agents={"developpeur": "Développeur"},
    )

    assert _couloirs(frise) == ["developpeur", "orchestrateur"]
    assert frise.couloirs[1].role == "Orchestrateur"


def test_aucune_entree_n_est_jamais_perdue_faute_de_couloir():
    """L'invariant du deuxième critère, vérifié par un **ensemble** et jamais par
    un décompte : compter laisserait passer une entrée rangée dans un couloir qui
    n'est pas servi."""
    frise = _frise(
        _entree(1, EVENEMENT_TACHE_STATUT, agent="developpeur"),
        _entree(2, EVENEMENT_TACHE_STATUT, agent=AGENT_ABSENT, statut=STATUT_BLOQUEE),
        _entree(3, EVENEMENT_MESSAGE_INTER_AGENTS, agent="qa"),
        _entree(4, EVENEMENT_TACHE_STATUT, agent=""),
        agents={"developpeur": "Développeur"},
    )

    servis = {couloir.agent for couloir in frise.couloirs}
    assert {entree.couloir for entree in frise.entrees} <= servis
    ranges = {identifiant for couloir in frise.couloirs for identifiant in couloir.entrees}
    assert ranges == set(_identifiants(frise))


def test_le_repli_vient_en_dernier_et_seulement_s_il_a_quelque_chose():
    avec = _frise(
        _entree(1, EVENEMENT_TACHE_STATUT, agent="developpeur"),
        _entree(2, EVENEMENT_TACHE_STATUT, agent=AGENT_ABSENT),
    )
    sans = _frise(_entree(1, EVENEMENT_TACHE_STATUT, agent="developpeur"))

    assert _couloirs(avec) == ["developpeur", COULOIR_REPLI]
    assert _couloirs(sans) == ["developpeur"]
    assert all(not couloir.repli for couloir in sans.couloirs)


def test_un_agent_declare_sans_role_prend_celui_de_sa_premiere_entree():
    """Une entrée de blocage porte « non exécutée », qui n'est pas un rôle."""
    frise = _frise(
        _entree(1, EVENEMENT_TACHE_STATUT, agent="qa", role=""),
        _entree(2, EVENEMENT_TACHE_STATUT, agent="qa", role="Testeur"),
    )

    assert frise.couloirs[0].role == "Testeur"


# ------------------------------- ④ Les trois états, à l'œil et sans ouvrir de détail


def test_bloquee_attente_et_en_cours_portent_trois_statuts_distincts():
    """Le cas d'usage qui a motivé le ticket : une attente de décision humaine
    cesse d'être indiscernable d'un travail en cours."""
    frise = _frise(
        _entree(1, EVENEMENT_TACHE_STATUT, tache_id="t1", statut=STATUT_EN_COURS),
        _entree(2, EVENEMENT_VALIDATION_DEMANDE, tache_id="t2", statut=VALIDATION_EN_ATTENTE),
        _entree(
            3,
            EVENEMENT_TACHE_STATUT,
            tache_id="t3",
            agent=AGENT_ABSENT,
            statut=STATUT_BLOQUEE,
        ),
    )

    statuts = [entree.statut for entree in frise.entrees]
    assert statuts == [STATUT_EN_COURS, STATUT_EN_ATTENTE_VALIDATION, STATUT_BLOQUEE]
    assert len(set(statuts)) == 3


def test_la_demande_de_validation_est_l_instant_ou_la_tache_s_arrete():
    """Le moteur n'émet pas `en_attente_validation` (`progression.py` le nomme
    depuis #473 sans que rien ne le produise) ; la file `GET /api/validations` en
    dit l'état **courant**, jamais la seconde. Une frise a besoin de la seconde."""
    entree = EntreeFrise.depuis(
        _entree(1, EVENEMENT_VALIDATION_DEMANDE, statut=VALIDATION_EN_ATTENTE)
    )

    assert entree.statut == STATUT_EN_ATTENTE_VALIDATION


def test_la_decision_reprend_les_mots_que_le_moteur_ecrit_lui_meme():
    """`approuve`/`refuse` sont les statuts de l'étape `<tâche>:validation`
    (`maestro.engine.executor`) — pas un vocabulaire de plus, et pas ceux de la
    **file** de validation, qui parlent d'une demande et non d'une tâche."""
    approuvee = EntreeFrise.depuis(
        _entree(1, EVENEMENT_VALIDATION_DECISION, statut=VALIDATION_APPROUVEE)
    )
    refusee = EntreeFrise.depuis(
        _entree(2, EVENEMENT_VALIDATION_DECISION, statut=VALIDATION_REFUSEE)
    )

    assert approuvee.statut == STATUT_VALIDATION_APPROUVE
    assert refusee.statut == STATUT_VALIDATION_REFUSE


def test_un_message_ne_porte_aucun_statut_de_tache():
    """C'est ainsi qu'une vue sépare les deux flux sans interpréter le type."""
    entree = EntreeFrise.depuis(
        _entree(1, EVENEMENT_MESSAGE_INTER_AGENTS, detail="handoff de dev à qa : à toi")
    )

    assert entree.statut == ""
    assert entree.objet == "handoff de dev à qa : à toi"


# ------------------------------- ⑤ Le plafond : il mord par la tête, et il le dit


def test_le_plafond_retient_les_entrees_les_plus_recentes_et_le_dit():
    """« Pendant qu'ils le font » se lit par la fin ; une borne muette ferait
    passer un run d'une heure pour un run de cinq cents lignes."""
    entrees = [
        _entree(
            rang,
            EVENEMENT_TACHE_STATUT,
            horodatage=f"2026-08-28T10:00:{rang:02d}+00:00",
            tache_id=f"t{rang}",
        )
        for rang in range(1, 11)
    ]

    frise = frise_du_run(RUN, entrees, plafond=3)

    assert _identifiants(frise) == ["j-0008", "j-0009", "j-0010"]
    assert frise.total == 10
    assert frise.tronquee is True
    assert frise.to_dict()["plafond"] == 3


def test_les_couloirs_ne_designent_que_ce_qui_est_rendu():
    """Sinon ils pointeraient des identifiants absents de la frise."""
    entrees = [
        _entree(rang, EVENEMENT_TACHE_STATUT, horodatage=f"2026-08-28T10:00:{rang:02d}+00:00")
        for rang in range(1, 6)
    ]

    frise = frise_du_run(RUN, entrees, plafond=2)

    ranges = {identifiant for couloir in frise.couloirs for identifiant in couloir.entrees}
    assert ranges == set(_identifiants(frise))


def test_une_frise_sous_le_plafond_ne_se_dit_pas_tronquee():
    frise = _frise(_entree(1, EVENEMENT_TACHE_STATUT))

    assert frise.tronquee is False
    assert frise.total == 1
    assert frise.plafond == PLAFOND_FRISE


def test_un_run_sans_activite_rend_une_frise_vide_et_pas_une_panne():
    frise = _frise()

    assert frise.entrees == ()
    assert frise.couloirs == ()
    assert frise.total == 0
    assert frise.tronquee is False


# ------------------------------- ⑥ La route : rien n'est créé, tout se recoupe


def test_la_route_rend_la_frise_du_run():
    with client_sur(
        lancement(),
        tache("t1", STATUT_EN_COURS),
        message("developpeur", "handoff de developpeur à qa : à toi"),
        demande_validation("t2"),
    ) as client:
        reponse = client.get(f"/api/executions/{RUN}/frise")

    assert reponse.status_code == 200
    frise = reponse.json()
    assert frise["run_id"] == RUN
    assert [entree["type"] for entree in frise["entrees"]] == [
        EVENEMENT_TACHE_STATUT,
        EVENEMENT_MESSAGE_INTER_AGENTS,
        EVENEMENT_VALIDATION_DEMANDE,
    ]
    assert frise["tronquee"] is False
    assert frise["total"] == 3


def test_la_route_range_les_entrees_en_couloirs_servis():
    with client_sur(
        lancement(),
        tache("t1", STATUT_TERMINEE, agent="developpeur"),
        tache("t2", STATUT_BLOQUEE, agent=AGENT_ABSENT, role="non exécutée"),
        demande_validation("t3", agent="devops"),
    ) as client:
        frise = client.get(f"/api/executions/{RUN}/frise").json()

    servis = {couloir["agent"] for couloir in frise["couloirs"]}
    assert {entree["couloir"] for entree in frise["entrees"]} <= servis
    assert COULOIR_REPLI in servis
    assert [couloir["repli"] for couloir in frise["couloirs"]][-1] is True


def test_la_route_refuse_un_run_inconnu():
    with client_sur(lancement(), tache("t1", STATUT_EN_COURS)) as client:
        reponse = client.get("/api/executions/run-jamais-vu/frise")

    assert reponse.status_code == 404
    assert "run-jamais-vu" in reponse.json()["detail"]


def test_chaque_entree_garde_l_identifiant_que_le_journal_lui_donne():
    """Rien n'est créé : les deux lectures ne peuvent pas se contredire."""
    with client_sur(
        lancement(),
        tache("t1", STATUT_EN_COURS),
        message("developpeur", "handoff de developpeur à qa : à toi"),
    ) as client:
        frise = client.get(f"/api/executions/{RUN}/frise").json()
        page = client.get(f"/api/journal?run_id={RUN}&projet=tous&taille=200").json()

    du_journal = {entree["id"]: entree for entree in page["entrees"]}
    for entree in frise["entrees"]:
        assert entree["id"] in du_journal
        assert entree["horodatage"] == du_journal[entree["id"]]["horodatage"]
        assert entree["type"] == du_journal[entree["id"]]["type"]


def test_la_frise_d_un_run_ignore_l_activite_d_un_autre():
    with client_sur(
        lancement(),
        lancement("run-voisin"),
        tache("t1", STATUT_EN_COURS),
        tache("t9", STATUT_EN_COURS, run_id="run-voisin"),
    ) as client:
        frise = client.get(f"/api/executions/{RUN}/frise").json()

    assert [entree["tache_id"] for entree in frise["entrees"]] == ["t1"]


# ------------------------------- Les deux accès qui alimentent la frise


def test_le_journal_rend_les_entrees_d_un_run_dans_l_ordre_des_rangs():
    journal = ServiceJournal()
    journal.consigner(tache("t1", STATUT_EN_COURS))
    journal.consigner(tache("t9", STATUT_EN_COURS, run_id="run-voisin"))
    journal.consigner(message("developpeur", "à toi"))

    entrees = journal.entrees_du_run(RUN)

    assert [entree.rang for entree in entrees] == [1, 3]
    assert [entree.id for entree in entrees] == ["j-0001", "j-0003"]


def test_la_projection_rend_les_agents_du_run_dans_l_ordre_d_apparition():
    state = ControlTowerState()
    for event in (
        lancement(),
        tache("t1", STATUT_EN_COURS, agent="developpeur", role="Développeur"),
        tache("t2", STATUT_EN_COURS, agent="qa", role="Testeur"),
        tache("t3", STATUT_EN_COURS, agent="developpeur", role="Développeur"),
    ):
        state.appliquer(event)

    assert state.agents_du_run(RUN) == {"developpeur": "Développeur", "qa": "Testeur"}
    assert list(state.agents_du_run(RUN)) == ["developpeur", "qa"]
    assert state.agents_du_run("run-jamais-vu") == {}
