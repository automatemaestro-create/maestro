"""Tests du **récit de fin d'un run** — le message que le fil reçoit (#1224).

`maestro/controltower/recit.py` répond à un constat du 2026-09-22 : la fin d'un
run n'écrivait rien dans le fil, et l'annonce dérivée de #928 dit *où* est le
livrable sans dire *ce qu'il contient* ni *comment l'essayer*. La personne
avait le lien du dossier et écrivait pourtant « on ne me dit pas comment
tester ».

Aucun réseau, aucun modèle, aucun moteur : le rédacteur est un double qui note
ce qu'on lui donne, ce que le module permet en ne lui demandant qu'un `rediger`.
C'est la règle de `tests/conftest.py` (#195), et c'est aussi ce qui rend
observable la seule chose qui compte vraiment ici — **ce qui atteint le
rédacteur** : les faits du run, les chemins qu'il a le droit de mettre en lien,
et le contenu du livrable encadré comme donnée.

Couvre :

① **la lecture du livrable** — elle passe par la chaîne d'ingestion (#316), donc
   elle respecte le périmètre du projet (un `.env` n'entre pas), rend des
   chemins **absolus** prêts à devenir des liens, et ne lève jamais sur une
   racine disparue ;
② **l'idempotence sur le fil** — `deja_raconte` est prouvé sur un échantillon
   fautif : un fil qui n'a que le message de lancement n'est **pas** raconté, et
   c'est ce qui fait que le test suivant veut dire quelque chose ;
③ **le conteur** — il écrit dans la conversation **qui a demandé le run** et pas
   une autre, le message porte le `run_id`, et il n'écrit ni deux fois, ni pour
   un run en vol, ni pour un run qu'aucun fil n'a ouvert ;
④ **rien n'est fabriqué** — un rédacteur injoignable ou muet ne pose aucun
   message et ne lève pas : l'annonce de #928 reste, elle est dérivée du
   persisté ;
⑤ **ce que le rédacteur lit** — les faits du run, les chemins offerts, et le
   contenu dans un bloc de données dont la consigne dit qu'il n'est pas une
   consigne (ENF-13) ;
⑥ **bout en bout par l'app** — un `execution.statut` terminal publié sur le bus
   fait paraître le récit dans `GET /api/chat/orchestrateur`, avec son
   rattachement au run.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.controltower.app import create_app
from maestro.controltower.chat import (
    UTILISATEUR,
    ChatStore,
    MessageChat,
    RepondeurChat,
    ServiceChat,
)
from maestro.controltower.events import (
    EVENEMENT_EXECUTION_STATUT,
    Event,
    InMemoryEventBus,
)
from maestro.controltower.orchestration import AGENT_ORCHESTRATION, NOM_ORCHESTRATION
from maestro.controltower.projets import ServiceProjets
from maestro.controltower.recit import (
    ConteurDeFin,
    Livrable,
    contexte_du_recit,
    deja_raconte,
    lire_le_livrable,
)
from maestro.controltower.state import (
    EXECUTION_ECHEC,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    ControlTowerState,
)
from maestro.messaging import InMemoryMailbox
from maestro.projets.modele import Perimetre, Projet
from maestro.projets.store import ProjetStore
from maestro.sources.extraction import RapportLecture

RUN = "run-1224"
RECIT = (
    "Vous avez une petite application Python. Pour l'essayer :\n\n"
    "```bash\npython app.py\n```\n\n"
    "Le point d'entrée : [app.py](<{chemin}>)"
)


# --------------------------------------------------------------------------
# Doubles
# --------------------------------------------------------------------------


class RedacteurEspion:
    """Un `RedacteurRecit` qui note ce qu'on lui donne et rend ce qu'on lui a posé.

    `panne` couvre la moitié que le texte ne couvre pas : un fournisseur qui ne
    répond pas. Le conteur ne doit alors **rien** écrire, et surtout pas lever —
    la pompe d'événements est derrière lui.
    """

    def __init__(self, texte: str = "Voilà ce que le run a produit.", panne: bool = False) -> None:
        self.texte = texte
        self.panne = panne
        self.contextes: list[str] = []

    async def rediger(self, *, agent: Any, contexte: str) -> str:
        self.contextes.append(contexte)
        if self.panne:
            raise RuntimeError("fournisseur injoignable")
        return self.texte


def _service_chat(depot: ChatStore) -> ServiceChat:
    """Un `ServiceChat` complet sur un dépôt jetable — bus et messagerie en mémoire."""
    return ServiceChat(
        store=depot,
        repondeur=_RepondeurMuet(),
        mailbox=InMemoryMailbox(),
        bus=InMemoryEventBus(),
    )


class _RepondeurMuet(RepondeurChat):
    """Le répondeur n'est jamais appelé ici : le récit n'est pas une réponse.

    Il lève plutôt qu'il ne rend une phrase : un récit qui passerait par le
    répondeur du fil serait un second chemin d'écriture, et ce test le dirait.
    """

    async def repondre(self, agent: Any, fil: Any) -> str:  # pragma: no cover
        raise AssertionError("le récit de fin ne passe pas par le répondeur du fil")


def _etat(statut: str = EXECUTION_TERMINEE, projet_id: str | None = "p1") -> ControlTowerState:
    """Une projection portant un seul run, dans le statut demandé."""
    state = ControlTowerState()
    state.appliquer(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN,
            statut=EXECUTION_EN_COURS,
            detail="Lancement",
            projet_id=projet_id,
        )
    )
    if statut != EXECUTION_EN_COURS:
        state.appliquer(
            Event(
                type=EVENEMENT_EXECUTION_STATUT,
                run_id=RUN,
                statut=statut,
                detail="1/1 tâche(s) réussie(s)",
                projet_id=projet_id,
            )
        )
    return state


def _lancement(conversation: str, run_id: str = RUN) -> list[MessageChat]:
    """La paire qu'un lancement laisse dans un fil : la demande, puis la réponse."""
    return [
        MessageChat(
            agent=NOM_ORCHESTRATION,
            conversation=conversation,
            auteur=UTILISATEUR,
            contenu="Crée-moi une petite application.",
        ),
        MessageChat(
            agent=NOM_ORCHESTRATION,
            conversation=conversation,
            auteur=NOM_ORCHESTRATION,
            contenu="C'est parti.",
            run_id=run_id,
        ),
    ]


# --------------------------------------------------------------------------
# ① La lecture du livrable
# --------------------------------------------------------------------------


def test_le_livrable_est_lu_par_la_chaine_d_ingestion_perimetre_compris(tmp_path: Path) -> None:
    """Le contenu entre, les chemins sortent en absolu, et le `.env` reste dehors.

    Le périmètre par défaut d'un projet exclut `.git`, `node_modules`, `.env` et
    `**/secrets/**` (docs/24 §2.5) : passer par `extraire_sources` est ce qui
    donne cette garantie sans l'écrire une seconde fois ici. Le test la **prouve
    sur un échantillon fautif** — le secret est écrit, lisible, et n'apparaît
    nulle part.
    """
    racine = tmp_path / "livrable"
    racine.mkdir()
    (racine / "README.md").write_text("# Demo\n\nLancer : `python app.py`\n", encoding="utf-8")
    (racine / "app.py").write_text("print('bonjour')\n", encoding="utf-8")
    (racine / ".env").write_text("MAESTRO_JETON=secret-a-ne-pas-lire\n", encoding="utf-8")

    livrable = lire_le_livrable(racine, Perimetre())

    assert not livrable.vide
    assert str(racine / "README.md") in livrable.chemins
    assert str(racine / "app.py") in livrable.chemins
    assert all(".env" not in chemin for chemin in livrable.chemins)
    assert "python app.py" in livrable.contexte
    assert "secret-a-ne-pas-lire" not in livrable.contexte


def test_une_racine_disparue_rend_un_livrable_vide_sans_lever(tmp_path: Path) -> None:
    """Un run qui n'a rien laissé est une information, jamais une panne."""
    livrable = lire_le_livrable(tmp_path / "jamais-cree", Perimetre())

    assert livrable.vide
    assert livrable.chemins == ()


def test_les_chemins_offerts_en_lien_sont_bornes(tmp_path: Path) -> None:
    """Le récit nomme ce qui compte ; l'arborescence, c'est l'explorateur (#928)."""
    racine = tmp_path / "beaucoup"
    racine.mkdir()
    for rang in range(20):
        (racine / f"fichier{rang:02d}.txt").write_text(f"ligne {rang}\n", encoding="utf-8")

    livrable = lire_le_livrable(racine, Perimetre(), fichiers_max=5)

    assert len(livrable.chemins) == 5


# --------------------------------------------------------------------------
# ② L'idempotence, prouvée sur un échantillon fautif
# --------------------------------------------------------------------------


def test_un_fil_qui_n_a_que_le_lancement_n_est_pas_deja_raconte() -> None:
    """L'échantillon fautif de la sonde : sans lui, ② ne prouverait rien.

    Un run ouvert depuis le fil y laisse **un** message d'agent portant son
    `run_id`. Si `deja_raconte` répondait « oui » dès ce message-là, aucun récit
    ne serait jamais écrit — et le test suivant serait vert pour la mauvaise
    raison.
    """
    assert deja_raconte(_lancement("origine"), RUN) is False


def test_un_fil_qui_porte_deja_le_recit_ne_le_reecrit_pas() -> None:
    """Deux messages d'agent sur le même run : le second **est** le récit."""
    fil = [
        *_lancement("origine"),
        MessageChat(
            agent=NOM_ORCHESTRATION,
            conversation="origine",
            auteur=NOM_ORCHESTRATION,
            contenu="Voilà ce que le run a produit.",
            run_id=RUN,
        ),
    ]

    assert deja_raconte(fil, RUN) is True


def test_le_recit_d_un_autre_run_ne_compte_pas() -> None:
    """Le marqueur est **par run**, pas par fil : deux runs cohabitent."""
    fil = [*_lancement("origine"), *_lancement("origine", run_id="run-autre")]

    assert deja_raconte(fil, RUN) is False


# --------------------------------------------------------------------------
# ③ Le conteur écrit dans le bon fil, une seule fois
# --------------------------------------------------------------------------


@pytest.fixture()
def depot(tmp_path: Path) -> ChatStore:
    """Le fil sur un répertoire temporaire — jamais le `core/chat/` réel."""
    return ChatStore(tmp_path / "chat")


@pytest.fixture()
def livrable(tmp_path: Path) -> Path:
    """Un livrable minimal : de quoi lire un mode d'emploi."""
    racine = tmp_path / "projet"
    racine.mkdir()
    (racine / "README.md").write_text("Lancer : `python app.py`\n", encoding="utf-8")
    (racine / "app.py").write_text("print('bonjour')\n", encoding="utf-8")
    return racine


def _conteur(
    depot: ChatStore,
    racine: Path | None,
    redacteur: RedacteurEspion,
    *,
    statut: str = EXECUTION_TERMINEE,
) -> ConteurDeFin:
    projet = (
        None
        if racine is None
        else Projet(id="p1", nom="projet", racine=str(racine), perimetre=Perimetre())
    )
    return ConteurDeFin(
        chat=_service_chat(depot),
        state=_etat(statut),
        agent=AGENT_ORCHESTRATION,
        projet=lambda projet_id: projet if projet_id == "p1" else None,
        redacteur=redacteur,
    )


def test_le_recit_est_ecrit_dans_la_conversation_qui_a_demande_le_run(
    depot: ChatStore, livrable: Path
) -> None:
    """Dans **celle-là** et pas une autre : le rattachement de #268 décide seul."""
    for message in _lancement("c-du-run"):
        depot.ajouter(message)
    depot.ajouter(
        MessageChat(
            agent=NOM_ORCHESTRATION,
            conversation="c-voisine",
            auteur=UTILISATEUR,
            contenu="Bonjour.",
        )
    )
    redacteur = RedacteurEspion(RECIT.format(chemin=livrable / "app.py"))

    message = asyncio.run(_conteur(depot, livrable, redacteur).raconter(RUN))

    assert message is not None
    assert message.conversation == "c-du-run"
    assert message.auteur == NOM_ORCHESTRATION
    assert message.run_id == RUN
    assert "python app.py" in message.contenu
    assert [m.contenu for m in depot.fil(NOM_ORCHESTRATION, "c-voisine")] == ["Bonjour."]


def test_le_recit_ne_s_ecrit_pas_deux_fois(depot: ChatStore, livrable: Path) -> None:
    """Le même événement terminal reçu deux fois ne fait pas deux bulles."""
    for message in _lancement("c-du-run"):
        depot.ajouter(message)
    redacteur = RedacteurEspion()
    conteur = _conteur(depot, livrable, redacteur)

    assert asyncio.run(conteur.raconter(RUN)) is not None
    assert asyncio.run(conteur.raconter(RUN)) is None
    assert len(depot.fil(NOM_ORCHESTRATION, "c-du-run")) == 3


def test_un_run_encore_en_vol_ne_se_raconte_pas(depot: ChatStore, livrable: Path) -> None:
    """Le conteur relit le statut dans la projection, il ne croit pas l'appelant."""
    for message in _lancement("c-du-run"):
        depot.ajouter(message)
    redacteur = RedacteurEspion()

    resultat = asyncio.run(
        _conteur(depot, livrable, redacteur, statut=EXECUTION_EN_COURS).raconter(RUN)
    )

    assert resultat is None
    assert redacteur.contextes == []


def test_un_run_qu_aucun_fil_n_a_ouvert_ne_se_raconte_nulle_part(
    depot: ChatStore, livrable: Path
) -> None:
    """Un run lancé depuis l'écran des exécutions n'a pas de fil : la cloche est là pour lui."""
    depot.ajouter(
        MessageChat(
            agent=NOM_ORCHESTRATION,
            conversation="origine",
            auteur=UTILISATEUR,
            contenu="Bonjour.",
        )
    )
    redacteur = RedacteurEspion()

    assert asyncio.run(_conteur(depot, livrable, redacteur).raconter(RUN)) is None
    assert redacteur.contextes == []


def test_un_run_en_echec_se_raconte_aussi(depot: ChatStore, livrable: Path) -> None:
    """« Ce qui reste à faire » est une des quatre choses que le récit doit dire."""
    for message in _lancement("c-du-run"):
        depot.ajouter(message)

    message = asyncio.run(
        _conteur(depot, livrable, RedacteurEspion(), statut=EXECUTION_ECHEC).raconter(RUN)
    )

    assert message is not None


# --------------------------------------------------------------------------
# ④ Rien n'est fabriqué
# --------------------------------------------------------------------------


def test_un_redacteur_injoignable_n_ecrit_aucune_phrase_gabarit(
    depot: ChatStore, livrable: Path
) -> None:
    """Ce qu'on ne sait pas dire ne se dit pas — et ne lève pas non plus.

    La pompe d'événements est derrière ce chemin : une exception qui remonterait
    couperait le flux temps réel de tous les écrans pour une bulle manquante.
    """
    for message in _lancement("c-du-run"):
        depot.ajouter(message)

    resultat = asyncio.run(
        _conteur(depot, livrable, RedacteurEspion(panne=True)).raconter(RUN)
    )

    assert resultat is None
    assert len(depot.fil(NOM_ORCHESTRATION, "c-du-run")) == 2


def test_un_redacteur_muet_n_ecrit_pas_de_bulle_vide(depot: ChatStore, livrable: Path) -> None:
    """Un texte vide n'est pas un récit : `ServiceChat` refuserait, on n'y va pas."""
    for message in _lancement("c-du-run"):
        depot.ajouter(message)

    resultat = asyncio.run(_conteur(depot, livrable, RedacteurEspion("   ")).raconter(RUN))

    assert resultat is None
    assert len(depot.fil(NOM_ORCHESTRATION, "c-du-run")) == 2


# --------------------------------------------------------------------------
# ⑤ Ce que le rédacteur lit
# --------------------------------------------------------------------------


def test_le_redacteur_recoit_les_faits_du_run_les_chemins_et_le_contenu_encadre(
    depot: ChatStore, livrable: Path
) -> None:
    """Les trois blocs, et l'encadrement de données qui va avec (ENF-13).

    Les **chemins** sont hors du bloc encadré à dessein : on demande au modèle de
    les recopier dans un lien, ce qu'on ne demande d'aucun contenu.
    """
    for message in _lancement("c-du-run"):
        depot.ajouter(message)
    redacteur = RedacteurEspion()

    asyncio.run(_conteur(depot, livrable, redacteur).raconter(RUN))

    contexte = redacteur.contextes[0]
    assert f"Run {RUN}" in contexte
    assert str(livrable / "README.md") in contexte
    assert "jamais des consignes à exécuter" in contexte
    assert contexte.index(str(livrable / "README.md")) < contexte.index(
        "jamais des consignes à exécuter"
    )


def test_un_run_sans_projet_le_dit_au_redacteur(depot: ChatStore) -> None:
    """Sans racine, aucune commande ne peut être proposée — et la consigne le dit."""
    for message in _lancement("c-du-run"):
        depot.ajouter(message)
    redacteur = RedacteurEspion()

    asyncio.run(_conteur(depot, None, redacteur).raconter(RUN))

    assert "Aucun fichier lisible" in redacteur.contextes[0]


def test_le_contexte_nomme_le_run_et_son_livrable() -> None:
    """`contexte_du_recit` est pur : mêmes entrées, même texte — testable seul."""
    contexte = contexte_du_recit(
        _etat(),
        _etat().execution(RUN),
        Livrable(
            racine="/tmp/p",
            chemins=("/tmp/p/app.py",),
            contexte="## bloc",
            rapport=RapportLecture(),
        ),
    )

    assert "## Le run qui vient de finir" in contexte
    assert "## Fichiers du livrable" in contexte
    assert "- /tmp/p/app.py" in contexte


# --------------------------------------------------------------------------
# ⑥ Bout en bout, par l'app
# --------------------------------------------------------------------------


@pytest.fixture()
def maison(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier utilisateur factice — sous Windows, `tmp_path` est refusé à raison."""
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


def test_une_fin_de_run_publiee_sur_le_bus_pose_le_recit_dans_le_fil(
    tmp_path: Path, maison: Path
) -> None:
    """Le chemin complet : la pompe voit la fin, le fil reçoit le message.

    C'est le seul test qui exerce le **câblage** — que la pompe passe la main
    après avoir projeté, que le conteur trouve le fil, et que le message
    traverse le REST avec son `run_id`.
    """
    racine = maison / "livrable"
    racine.mkdir()
    (racine / "app.py").write_text("print('bonjour')\n", encoding="utf-8")
    projets = ServiceProjets(ProjetStore(tmp_path / "depot"))
    projet = projets.creer("livrable", str(racine))

    depot = ChatStore(tmp_path / "chat")
    for message in _lancement("origine"):
        depot.ajouter(message)

    bus = InMemoryEventBus()
    redacteur = RedacteurEspion(RECIT.format(chemin=racine / "app.py"))
    with TestClient(
        create_app(
            bus=bus,
            chat_store=depot,
            projets=projets,
            recit_redacteur=redacteur,
        )
    ) as client:
        for statut in (EXECUTION_EN_COURS, EXECUTION_TERMINEE):
            client.portal.call(
                bus.publish,
                Event(
                    type=EVENEMENT_EXECUTION_STATUT,
                    run_id=RUN,
                    statut=statut,
                    detail="1/1 tâche(s) réussie(s)",
                    projet_id=projet["id"],
                ),
            )
        messages = _attendre_le_recit(depot)

    assert messages is not None, "aucun récit n'a été posé au fil"
    dernier = messages[-1]
    assert dernier.auteur == NOM_ORCHESTRATION
    assert dernier.run_id == RUN
    assert "python app.py" in dernier.contenu
    assert str(racine / "app.py") in dernier.contenu


def _attendre_le_recit(depot: ChatStore) -> tuple[MessageChat, ...] | None:
    """Le fil dès qu'il porte plus que la paire du lancement — `None` s'il n'y vient pas.

    Ce qu'on attend est un **enchaînement de tâches** `asyncio` (pompe → récit →
    écriture) qui se joue dans la boucle de l'app, sur un autre fil d'exécution
    que le test : on relit le dépôt jusqu'à ce qu'il porte le message, sans
    jamais dormir d'une durée choisie pour « laisser le temps ». Le plafond est
    là pour rendre un échec en secondes plutôt qu'en pendaison.
    """
    for _ in range(400):
        fil = depot.fil(NOM_ORCHESTRATION, "origine")
        if len(fil) > 2:
            return fil
        time.sleep(0.01)
    return None
