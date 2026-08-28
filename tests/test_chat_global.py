"""Tests du **fil global** — le canal `orchestrateur` (ticket #273, lot 6/6 de #244).

Le chat de la Control Tower avait deux canaux couverts et il lui en manquait un.
`tests/test_chat.py` (#84) éprouve le `ServiceChat` — persistance, acheminement,
diffusion — et `tests/test_controltower.py` ses endpoints. Le **fil global**
(#268, `maestro/controltower/orchestration.py`) n'avait, lui, aucun test : ni sa
règle d'intention, ni son aperçu, ni son répondeur, ni la route qui le sert. Or
c'est le seul canal du produit qui **agit** — une phrase mal reconnue y ouvre un
run, c'est-à-dire du quota et des écritures dans un projet.

Aucun réseau, aucun modèle, aucun moteur : le lanceur de run est un double qui
enregistre ce qu'on lui demande, et c'est exactement ce que le module permet en
n'exigeant qu'un `LanceurRun` (« ouvre un run sur cet objectif »).

Couvre :

① **la règle d'intention** et son asymétrie assumée — un verbe d'action en tête
   ouvre un run, tout le reste est une conversation, **le doute compris**. Le
   test porte les deux moitiés : ce qui doit ouvrir, et ce qui ne doit surtout
   pas ;
② **l'aperçu** de l'orchestration : la phrase d'état, ses accords, et le fait
   qu'elle soit relue à chaque question plutôt que figée à la construction ;
③ **le répondeur** : le run ouvert et **rattaché** à la réponse, un lancement en
   échec raconté dans le fil au lieu d'être levé, et le canal sans lanceur qui le
   dit plutôt que de faire semblant ;
④ **le contrat SSE** vu du répondeur : la concaténation des incréments *est* le
   texte final — ce dont dépend un client qui reconstitue la réponse des `delta`
   seuls ;
⑤ **les endpoints** : `/api/chat/orchestrateur` sert un fil que le catalogue ne
   porte pas, le run ouvert voyage jusqu'au JSON et jusqu'au WebSocket, et le
   flux rend `debut`/`fragment`/`fin`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.controltower.app import create_app
from maestro.controltower.chat import (
    FRAGMENT_CHAT_DEBUT,
    FRAGMENT_CHAT_DELTA,
    FRAGMENT_CHAT_FIN,
    ChatStore,
    MessageChat,
)
from maestro.controltower.events import (
    EVENEMENT_CHAT_MESSAGE,
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DEMANDE,
    Event,
    InMemoryEventBus,
)
from maestro.controltower.orchestration import (
    AGENT_ORCHESTRATION,
    INTENTION_ECHANGE,
    INTENTION_TRAVAIL,
    NOM_ORCHESTRATION,
    RepondeurOrchestration,
    apercu_de,
    intention,
)
from maestro.controltower.projets import ServiceProjets
from maestro.controltower.state import (
    EXECUTION_EN_ATTENTE_ARBITRAGE,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    VALIDATION_EN_ATTENTE,
    ControlTowerState,
)
from maestro.engine import RunReport
from maestro.projets import ProjetStore

UTILISATEUR = "utilisateur"


# ── ① la règle d'intention ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "demande",
    [
        "Ajoute la pagination à la liste des projets",
        "ajouter la pagination",
        # Les amorces de politesse et de volonté tombent avant qu'on cherche le
        # verbe — sans quoi la forme la plus courante d'une demande passerait
        # pour une question.
        "peux-tu corriger le tri des tâches",
        "Bonjour, peux-tu corriger le tri",
        "j'aimerais migrer la base",
        "il faudrait documenter le module",
        "Merci de déployer la version 1.2",
        # L'accentuation et la casse ne changent rien : `normaliser` passe avant.
        "DÉPLOIE la nouvelle image",
    ],
)
def test_un_verbe_d_action_en_tete_ouvre_un_run(demande: str) -> None:
    assert intention(demande) == INTENTION_TRAVAIL


@pytest.mark.parametrize(
    "demande",
    [
        # Une question *sur* le travail n'est pas le travail.
        "Comment ajouter une page ?",
        "Où en sont les runs ?",
        "Qu'est-ce qui attend mon arbitrage ?",
        "merci",
        "Bonjour",
        "",
        "   ",
        # Le doute compte pour une conversation : rien ici ne commence par un
        # verbe connu, et inventer une reconnaissance coûterait un run.
        "la pagination de la liste des projets",
        "le tri du Kanban est cassé",
    ],
)
def test_tout_le_reste_est_une_conversation(demande: str) -> None:
    """L'asymétrie du module : ne pas reconnaître coûte une phrase, reconnaître à tort un run."""
    assert intention(demande) == INTENTION_ECHANGE


def test_une_amorce_seule_n_ouvre_rien() -> None:
    """« bonjour » réduit à rien ne doit pas retomber sur le premier mot venu."""
    assert intention("bonjour") == INTENTION_ECHANGE
    assert intention("s'il te plaît") == INTENTION_ECHANGE


# ── ② l'aperçu de l'orchestration ─────────────────────────────────────────────


def _run(run_id: str, statut: str, projet_id: str | None = None) -> Event:
    return Event(
        type=EVENEMENT_EXECUTION_STATUT, run_id=run_id, statut=statut, projet_id=projet_id
    )


def _tache(tache_id: str, run_id: str, projet_id: str | None = None) -> Event:
    return Event(
        type=EVENEMENT_TACHE_STATUT,
        run_id=run_id,
        tache_id=tache_id,
        titre="Écrire les tests",
        agent="qa",
        role="QA / Testeur",
        statut="en_cours",
        projet_id=projet_id,
    )


def _validation(tache_id: str) -> Event:
    return Event(
        type=EVENEMENT_VALIDATION_DEMANDE,
        tache_id=tache_id,
        titre="Déployer en production",
        agent="devops",
        role="DevOps / SRE",
        statut=VALIDATION_EN_ATTENTE,
    )


def test_apercu_sans_rien_le_dit_en_une_phrase() -> None:
    assert apercu_de(ControlTowerState())() == "Aucun run en cours."


def test_apercu_compte_les_runs_actifs_et_les_taches() -> None:
    state = ControlTowerState()
    state.appliquer(_run("run-1", EXECUTION_EN_COURS))
    state.appliquer(_tache("T-1", "run-1"))

    assert apercu_de(state)() == "1 run en cours, 1 tâche suivie."


def test_un_run_qui_attend_un_arbitrage_compte_pour_un_run_en_cours() -> None:
    """De la place où l'on pose la question, un run qui attend est un run en cours."""
    state = ControlTowerState()
    state.appliquer(_run("run-1", EXECUTION_EN_ATTENTE_ARBITRAGE))

    assert apercu_de(state)().startswith("1 run en cours")


def test_un_run_solde_ne_compte_plus() -> None:
    state = ControlTowerState()
    state.appliquer(_run("run-1", EXECUTION_TERMINEE))

    assert apercu_de(state)() == "Aucun run en cours."


def test_l_apercu_nomme_les_validations_en_attente() -> None:
    state = ControlTowerState()
    state.appliquer(_run("run-1", EXECUTION_EN_COURS))
    state.appliquer(_validation("T-1"))
    state.appliquer(_validation("T-2"))

    assert "2 validations attendent votre arbitrage." in apercu_de(state)()


def test_l_apercu_est_relu_a_chaque_question() -> None:
    """Figé à la construction de l'app, il annoncerait l'état d'hier."""
    state = ControlTowerState()
    apercu = apercu_de(state)
    assert apercu() == "Aucun run en cours."

    state.appliquer(_run("run-1", EXECUTION_EN_COURS))

    assert apercu() != "Aucun run en cours."


def test_l_apercu_ne_compte_que_ce_que_l_ecran_peut_montrer() -> None:
    """La seconde moitié de #683 : la phrase et l'écran parlaient de deux périmètres.

    Le fil annonçait « 1 run en cours » en comptant *tous* les runs du poste,
    quand chaque vue de travail est cadrée sur le projet actif (#277) — d'où un
    run annoncé en cours, absent de la liste et refusé par la vue de détail.
    L'aperçu prend donc le projet de la fenêtre, et ses **trois** compteurs avec
    lui : compter les runs d'un projet et les tâches de tous ferait une phrase
    qui se contredit d'une virgule à l'autre.
    """
    state = ControlTowerState()
    state.appliquer(_run("run-ici", EXECUTION_EN_COURS, projet_id="prj-ici"))
    state.appliquer(_tache("T-ici", "run-ici", projet_id="prj-ici"))
    state.appliquer(_run("run-ailleurs", EXECUTION_EN_COURS, projet_id="prj-ailleurs"))
    state.appliquer(_tache("T-ailleurs", "run-ailleurs", projet_id="prj-ailleurs"))
    apercu = apercu_de(state)

    assert apercu("prj-ici") == "1 run en cours, 1 tâche suivie."
    # L'échantillon fautif : sans portée, la phrase est celle d'avant le lot —
    # elle compte les deux projets, donc annonce un travail que l'écran cadré
    # sur « prj-ici » ne montre pas.
    assert apercu() == "2 runs en cours, 2 tâches suivies."


def test_un_run_sans_projet_ne_compte_dans_l_apercu_d_aucun_projet() -> None:
    """La règle de portée, pas une seconde : `PorteeProjet.retient` ne devine rien.

    C'est exactement le run que #683 a trouvé en vol — orphelin, donc dans la
    vue d'aucun projet. Le compter dans celle du projet actif redirait le
    mensonge que ce ticket supprime ; il reste visible sans portée.
    """
    state = ControlTowerState()
    state.appliquer(_run("run-orphelin", EXECUTION_EN_COURS))
    apercu = apercu_de(state)

    assert apercu("prj-ici") == "Aucun run en cours."
    assert apercu().startswith("1 run en cours")


# ── ③ le répondeur : ce qu'il ouvre, et ce qu'il n'ouvre pas ──────────────────


class LanceurEspion:
    """Un `LanceurRun` qui note ce qu'on lui demande — aucun moteur, aucun quota.

    Il note **les deux** arguments du contrat (#683) : l'objectif et le projet
    de la fenêtre. Un double qui n'accepterait que le premier rendrait le canal
    vert sur un lancement que le vrai service refuserait — et le répondeur
    rattrapant toute exception du lanceur, l'échec se lirait « le lancement a
    échoué » au lieu d'une erreur de signature.
    """

    def __init__(self, *, run_id: str = "run-42", statut: str = "en_cours") -> None:
        self.objectifs: list[str] = []
        self.projets: list[str | None] = []
        self._resume = {"run_id": run_id, "statut": statut}

    async def __call__(self, objectif: str, projet_id: str | None = None) -> dict[str, str]:
        self.objectifs.append(objectif)
        self.projets.append(projet_id)
        return dict(self._resume)


def _fil(contenu: str) -> list[MessageChat]:
    return [MessageChat(agent=NOM_ORCHESTRATION, auteur=UTILISATEUR, contenu=contenu)]


def test_une_demande_de_travail_ouvre_un_run_et_le_rattache() -> None:
    lanceur = LanceurEspion()
    repondeur = RepondeurOrchestration(lanceur=lanceur)

    reponse = asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION, _fil("Ajoute la pagination à la liste des projets")
        )
    )

    # L'objectif part **tel quel** : c'est la phrase de l'utilisateur qui est
    # cadrée par le run, pas une reformulation faite ici.
    assert lanceur.objectifs == ["Ajoute la pagination à la liste des projets"]
    # Et la réponse porte le run : sans ce rattachement le fil dirait « c'est
    # parti » sans dire vers quoi.
    assert reponse.run_id == "run-42"
    assert "run-42" in reponse.contenu


def test_le_run_ouvert_appartient_au_projet_de_la_fenetre() -> None:
    """#683 : sans projet, un run dicté au fil n'entrait dans la vue d'aucun.

    Le fil est transverse (#281) et le reste — mais ce qu'il **ouvre** a un
    périmètre, et c'est la fenêtre qui le donne. Le projet part au lanceur avec
    l'objectif, et rien n'est deviné : le répondeur transmet, il ne cherche pas.
    """
    lanceur = LanceurEspion()
    repondeur = RepondeurOrchestration(lanceur=lanceur)

    asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION, _fil("Ajoute la pagination"), projet_id="prj-depensio"
        )
    )

    assert lanceur.objectifs == ["Ajoute la pagination"]
    assert lanceur.projets == ["prj-depensio"]


def test_sans_projet_le_run_part_sans_projet() -> None:
    """Le comportement d'avant #683, gardé : un rattachement absent n'empêche rien.

    Le projet est une **donnée** portée par le run (#222), jamais une condition
    de son lancement — un poste sans projet actif doit continuer à ouvrir des
    runs, quitte à ce qu'ils ne relèvent d'aucune vue de projet.
    """
    lanceur = LanceurEspion()
    repondeur = RepondeurOrchestration(lanceur=lanceur)

    reponse = asyncio.run(
        repondeur.produire(AGENT_ORCHESTRATION, _fil("Ajoute la pagination"))
    )

    assert lanceur.projets == [None]
    assert reponse.run_id == "run-42"


def test_la_voie_conversationnelle_cadre_son_apercu_sur_le_meme_projet() -> None:
    """Les deux voies reçoivent le projet, faute de quoi la phrase et le run divergent."""
    vus: list[str | None] = []

    def apercu(projet_id: str | None = None) -> str:
        vus.append(projet_id)
        return "Aucun run en cours."

    repondeur = RepondeurOrchestration(lanceur=LanceurEspion(), apercu=apercu)

    asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION, _fil("Où en sont les runs ?"), projet_id="prj-depensio"
        )
    )

    assert vus == ["prj-depensio"]


def test_une_question_n_ouvre_aucun_run() -> None:
    lanceur = LanceurEspion()
    repondeur = RepondeurOrchestration(
        lanceur=lanceur, apercu=lambda projet_id=None: "Aucun run en cours."
    )

    reponse = asyncio.run(
        repondeur.produire(AGENT_ORCHESTRATION, _fil("Où en sont les runs ?"))
    )

    assert lanceur.objectifs == []
    assert reponse.run_id == ""
    # Elle répond avec ce qu'elle sait de l'état, puis dit ce qu'elle sait faire.
    assert reponse.contenu.startswith("Aucun run en cours.")


def test_un_lancement_en_echec_se_raconte_dans_le_fil() -> None:
    """Levée, l'exception deviendrait un 502 sans trace — or la demande est acquise."""

    async def lanceur_qui_echoue(objectif: str, projet_id: str | None = None) -> dict[str, str]:
        raise RuntimeError("objectif refusé : plafond hors bornes")

    repondeur = RepondeurOrchestration(lanceur=lanceur_qui_echoue)

    reponse = asyncio.run(
        repondeur.produire(AGENT_ORCHESTRATION, _fil("Ajoute la pagination"))
    )

    assert reponse.run_id == ""
    assert "Le lancement a échoué" in reponse.contenu
    # La cause est nommée : c'est ce qui permet de reformuler.
    assert "plafond hors bornes" in reponse.contenu


def test_sans_lanceur_le_canal_le_dit_au_lieu_de_faire_semblant() -> None:
    repondeur = RepondeurOrchestration()

    travail = asyncio.run(
        repondeur.produire(AGENT_ORCHESTRATION, _fil("Ajoute la pagination"))
    )
    echange = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil("Bonjour")))

    assert travail.run_id == ""
    assert "Je ne peux pas ouvrir de run" in travail.contenu
    assert "La demande est bien enregistrée" in travail.contenu
    assert "pas encore l'ouvrir" in echange.contenu


def test_repondre_rend_le_texte_de_produire() -> None:
    """`repondre` est la voie courte : le même texte, sans le rattachement."""
    repondeur = RepondeurOrchestration(lanceur=LanceurEspion())

    texte = asyncio.run(
        repondeur.repondre(AGENT_ORCHESTRATION, _fil("Ajoute la pagination"))
    )
    complet = asyncio.run(
        repondeur.produire(AGENT_ORCHESTRATION, _fil("Ajoute la pagination"))
    )

    assert texte == complet.contenu


# ── ④ le contrat SSE, vu du répondeur ─────────────────────────────────────────


@pytest.mark.parametrize(
    "demande", ["Ajoute la pagination à la liste des projets", "Où en sont les runs ?"]
)
def test_les_increments_reconstituent_exactement_la_reponse(demande: str) -> None:
    """Ce dont dépend un client SSE : concaténer les `delta` rend la trame `fin`.

    Éprouvé sur les **deux** voies du répondeur — celle qui ouvre un run et celle
    qui converse —, l'écriture par morceaux n'étant pas la même de part et
    d'autre. Le `strip` final est celui de `_Redaction.texte`, d'où la
    comparaison sur le texte ébarbé plutôt que sur la somme brute.
    """
    incremente: list[str] = []

    async def incrementer(delta: str) -> None:
        incremente.append(delta)

    repondeur = RepondeurOrchestration(
        lanceur=LanceurEspion(), apercu=lambda projet_id=None: "Aucun run en cours."
    )

    reponse = asyncio.run(
        repondeur.produire(AGENT_ORCHESTRATION, _fil(demande), incrementer=incrementer)
    )

    assert incremente != []
    assert "".join(incremente).strip() == reponse.contenu


def test_sans_incrementeur_rien_n_est_publie_et_le_texte_est_le_meme() -> None:
    """`POST …/messages` passe par la même production, sans flux : elle doit tenir."""
    repondeur = RepondeurOrchestration(lanceur=LanceurEspion())

    reponse = asyncio.run(
        repondeur.produire(AGENT_ORCHESTRATION, _fil("Ajoute la pagination"))
    )

    assert reponse.contenu.startswith("J'ouvre un run sur cette demande.")


# ── ⑤ les endpoints du fil global ─────────────────────────────────────────────


@pytest.fixture()
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture()
def depot_chat(tmp_path) -> ChatStore:
    """Fil sur répertoire temporaire — jamais le `core/chat/` réel."""
    return ChatStore(tmp_path / "chat")


@pytest.fixture()
def lanceur() -> LanceurEspion:
    return LanceurEspion()


@pytest.fixture()
def client_global(bus, depot_chat, lanceur):
    """L'app avec un fil global branché sur un lanceur factice — aucun moteur."""
    with TestClient(
        create_app(
            bus=bus,
            chat_store=depot_chat,
            orchestration_repondeur=RepondeurOrchestration(
                lanceur=lanceur, apercu=lambda projet_id=None: "Aucun run en cours."
            ),
        )
    ) as client:
        yield client


def test_le_fil_global_est_servi_sans_etre_au_catalogue(client_global) -> None:
    """`orchestrateur` n'exécute aucune tâche — et son fil répond quand même.

    C'est tout le dessin du canal : la fiche est hors catalogue (rien ne doit
    pouvoir lui router une tâche), mais `/api/chat/{agent}` la résout avant de
    passer par le catalogue. Un nom inconnu, lui, reste un 404.
    """
    fil = client_global.get(f"/api/chat/{NOM_ORCHESTRATION}")

    assert fil.status_code == 200
    assert fil.json() == {
        "agent": NOM_ORCHESTRATION,
        "role": "Orchestrateur",
        "messages": [],
    }
    assert NOM_ORCHESTRATION not in {
        agent["nom"] for agent in client_global.get("/api/catalogue").json()
    }
    assert client_global.get("/api/chat/pas-un-agent").status_code == 404


def test_une_demande_postee_au_fil_global_ouvre_un_run_et_le_porte(
    client_global, lanceur, depot_chat
) -> None:
    reponse = client_global.post(
        f"/api/chat/{NOM_ORCHESTRATION}/messages",
        json={"contenu": "Ajoute la pagination à la liste des projets"},
    )

    assert reponse.status_code == 201
    envoye, repondu = reponse.json()["messages"]
    assert envoye["auteur"] == UTILISATEUR and envoye["run_id"] == ""
    # Le `run_id` voyage jusqu'au JSON du message persisté : c'est lui que
    # l'écran relit pour lister ce que le fil a ouvert (#269).
    assert repondu["run_id"] == "run-42"
    assert lanceur.objectifs == ["Ajoute la pagination à la liste des projets"]
    # Le fil global a son propre fichier, sous le nom du canal.
    assert (depot_chat.racine / f"{NOM_ORCHESTRATION}.jsonl").is_file()


def test_le_run_ouvert_part_aussi_sur_le_websocket(client_global) -> None:
    """Un client temps réel apprend le rattachement sans rien relire."""
    with client_global.websocket_connect("/ws/evenements?projet=tous") as ws:
        client_global.post(
            f"/api/chat/{NOM_ORCHESTRATION}/messages",
            json={"contenu": "Corrige le tri des tâches"},
        )
        aller = ws.receive_json()
        retour = ws.receive_json()

    assert aller["type"] == EVENEMENT_CHAT_MESSAGE and aller["run_id"] == ""
    assert retour["type"] == EVENEMENT_CHAT_MESSAGE
    assert retour["agent"] == NOM_ORCHESTRATION and retour["run_id"] == "run-42"


def test_une_question_postee_au_fil_global_n_ouvre_rien(client_global, lanceur) -> None:
    reponse = client_global.post(
        f"/api/chat/{NOM_ORCHESTRATION}/messages", json={"contenu": "Où en sont les runs ?"}
    )

    _, repondu = reponse.json()["messages"]
    assert repondu["run_id"] == ""
    assert lanceur.objectifs == []


def _trames(reponse) -> list[dict]:
    """Les objets JSON d'un corps `text/event-stream` (`data: <json>` par trame)."""
    return [
        json.loads(ligne[len("data: ") :])
        for ligne in reponse.text.splitlines()
        if ligne.startswith("data: ")
    ]


def test_le_flux_du_fil_global_rend_debut_fragments_et_fin(client_global) -> None:
    """Le canal SSE vaut pour les trois fils — ici le global, qui agit en plus."""
    reponse = client_global.get(
        f"/api/chat/{NOM_ORCHESTRATION}/flux",
        params={"contenu": "Ajoute la pagination à la liste des projets"},
    )

    assert reponse.status_code == 200
    assert reponse.headers["content-type"].startswith("text/event-stream")
    trames = _trames(reponse)
    assert trames[0]["type"] == FRAGMENT_CHAT_DEBUT
    assert trames[-1]["type"] == FRAGMENT_CHAT_FIN
    deltas = [t["delta"] for t in trames if t["type"] == FRAGMENT_CHAT_DELTA]
    assert deltas != []
    # La promesse du contrat : les `delta` seuls reconstituent la trame `fin`.
    final = trames[-1]["message"]
    assert "".join(deltas).strip() == final["contenu"]
    assert final["run_id"] == "run-42"


def test_un_contenu_vide_sort_en_422_sans_rien_persister(client_global, depot_chat) -> None:
    """La question se tranche **avant** la première trame — sinon plus de statut à rendre."""
    reponse = client_global.get(f"/api/chat/{NOM_ORCHESTRATION}/flux", params={"contenu": ""})

    assert reponse.status_code == 422
    assert client_global.get(f"/api/chat/{NOM_ORCHESTRATION}").json()["messages"] == []
    assert not (depot_chat.racine / f"{NOM_ORCHESTRATION}.jsonl").exists()


# ── ⑥ le projet de la fenêtre, du corps de la requête jusqu'au run (#683) ─────


def test_le_projet_de_la_fenetre_voyage_du_corps_jusqu_au_lanceur(
    client_global, lanceur
) -> None:
    """Le rattachement traverse la route sans rien perdre en chemin."""
    client_global.post(
        f"/api/chat/{NOM_ORCHESTRATION}/messages",
        json={"contenu": "Ajoute la pagination", "projet_id": "prj-depensio"},
    )

    assert lanceur.projets == ["prj-depensio"]


def test_un_projet_mal_forme_vaut_aucun_projet(client_global, lanceur) -> None:
    """Normalisé à la frontière (#222) : un identifiant douteux ne fait pas échouer un message.

    Le rattachement est une donnée, pas une condition du lancement. Refuser le
    message ferait dépendre une conversation de la bonne tenue d'un identifiant
    que l'utilisateur n'a jamais tapé.
    """
    reponse = client_global.post(
        f"/api/chat/{NOM_ORCHESTRATION}/messages",
        json={"contenu": "Ajoute la pagination", "projet_id": "../../etc"},
    )

    assert reponse.status_code == 201
    assert lanceur.projets == [None]


def test_le_flux_porte_le_projet_comme_le_post(client_global, lanceur) -> None:
    """Les deux voies mènent au même `_repondre` : un run ouvert par le flux se rattache aussi.

    `?projet_id=` et non `?projet=` : ce dernier désigne partout ailleurs une
    **portée** de lecture, avec ses mots réservés `tous`/`aucun` (#277), et deux
    contrats sous un même nom seraient la première façon de les confondre.
    """
    client_global.get(
        f"/api/chat/{NOM_ORCHESTRATION}/flux",
        params={"contenu": "Ajoute la pagination", "projet_id": "prj-depensio"},
    )

    assert lanceur.projets == ["prj-depensio"]


def test_le_fil_lui_meme_reste_transverse(client_global, depot_chat) -> None:
    """Le projet accompagne la demande ; il n'entre ni dans le fil ni dans l'événement.

    C'est la moitié de #281 que ce lot **ne** défait pas : un `chat.message` sans
    `projet_id` est ce qui permet à une socket cadrée sur un projet de recevoir
    quand même le fil. Le rattachement vit sur le **run** ouvert, jamais sur le
    message qui l'a demandé.
    """
    with client_global.websocket_connect("/ws/evenements?projet=tous") as ws:
        client_global.post(
            f"/api/chat/{NOM_ORCHESTRATION}/messages",
            json={"contenu": "Ajoute la pagination", "projet_id": "prj-depensio"},
        )
        aller = ws.receive_json()
        retour = ws.receive_json()

    assert aller["projet_id"] is None and retour["projet_id"] is None
    persistes = depot_chat.fil(NOM_ORCHESTRATION)
    assert [m.to_dict().get("projet_id") for m in persistes] == [None, None]


# ── ⑦ le câblage réel : de la demande au run qui figure dans la liste (#683) ──
#
# Les tests ci-dessus injectent le répondeur, donc **court-circuitent** le
# lanceur que l'app construit — or c'est précisément lui qui était en défaut :
# `ouvrir_un_run` appelait `lancer(objectif)` sans projet, et le run naissait
# orphelin. Ceux-ci montent donc l'app **sans** `orchestration_repondeur`, avec
# un moteur muet et deux projets réellement déclarés, et lisent le résultat par
# la route que l'écran interroge.


class MoteurMuet:
    """Moteur injecté à la place du vrai : il n'appelle rien et note son projet.

    Volontairement plus court que le `MoteurDouble` de `tests/test_executions.py`
    (dont ce n'est pas le sujet ici) : ce qui compte est que `projet_id` arrive
    jusqu'au moteur, donc jusqu'aux tâches du plan. Il ne peut pas rendre un faux
    vert — un service qui passerait le projet sous un autre nom lui ferait noter
    `None`, et l'assertion tomberait.
    """

    def __init__(self) -> None:
        self.projets: list[str | None] = []

    def __call__(self, **reglages: Any) -> MoteurMuet:
        return self

    async def run(self, objectif: str, *, projet_id: str | None = None, **reste: Any) -> RunReport:
        self.projets.append(projet_id)
        return RunReport(objectif=objectif, resultats=())


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
def moteur() -> MoteurMuet:
    return MoteurMuet()


@pytest.fixture()
def client_reel(bus, depot_chat, projets, moteur):
    """L'app **entière** : vrai répondeur d'orchestration, vrai service d'exécutions."""
    with TestClient(
        create_app(
            bus=bus,
            state=ControlTowerState(),
            chat_store=depot_chat,
            projets=projets,
            fabrique_moteur=moteur,
        )
    ) as client:
        yield client


def _demander(client: TestClient, **corps: Any) -> str:
    """Dicte une demande de travail au fil et rend le `run_id` que la réponse porte."""
    reponse = client.post(
        f"/api/chat/{NOM_ORCHESTRATION}/messages",
        json={"contenu": "Ajoute la pagination à la liste des projets", **corps},
    )
    assert reponse.status_code == 201
    return reponse.json()["messages"][1]["run_id"]


def _runs_de(client: TestClient, portee: str) -> set[str]:
    """Les runs que la liste de l'écran rend pour cette portée (`GET /api/executions`)."""
    reponse = client.get("/api/executions", params={"projet": portee})
    assert reponse.status_code == 200
    return {run["run_id"] for run in reponse.json()}


def test_un_run_dicte_au_fil_figure_dans_la_liste_de_son_projet(
    client_reel, projets, moteur
) -> None:
    """Le défaut de #683, par le chemin exact où il se produisait.

    Le run ouvert depuis le fil appartient au projet de la fenêtre : il figure
    dans la liste des runs de ce projet — celle que l'écran lit —, il ne figure
    pas dans celle du projet d'à côté, et son projet descend jusqu'au moteur,
    donc jusqu'aux tâches du plan (#222).
    """
    ici, ailleurs = (p["id"] for p in projets.lister())

    run_id = _demander(client_reel, projet_id=ici)

    assert run_id in _runs_de(client_reel, ici)
    assert run_id not in _runs_de(client_reel, ailleurs)
    assert run_id not in _runs_de(client_reel, "aucun")
    assert moteur.projets == [ici]


def test_sans_projet_le_run_reste_introuvable_a_l_ecran(client_reel, projets) -> None:
    """L'échantillon fautif : ce que faisait **tout** run du chat avant ce lot.

    Sans rattachement, le run n'entre dans la vue d'aucun projet (`PorteeProjet`,
    #277) — il n'est atteignable que sous `aucun`, portée qu'aucun sélecteur de
    l'UI ne propose. C'est ce qui le rendait invisible dans la liste et
    impossible à ouvrir en détail, alors même que l'orchestrateur l'annonçait en
    cours. Le garder ici est ce qui empêche de croire que l'assertion précédente
    passerait de toute façon.
    """
    ici, _ = (p["id"] for p in projets.lister())

    run_id = _demander(client_reel)

    assert run_id not in _runs_de(client_reel, ici)
    assert run_id in _runs_de(client_reel, "aucun")
