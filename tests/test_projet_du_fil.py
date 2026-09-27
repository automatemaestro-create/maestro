"""Le fil sait sur quel projet il travaille, et une proposition garde son projet (#1180).

Trois défauts relevés le 2026-09-21 par les balayages « rien de figé », et chacun a
ici son échantillon :

- **le juge ne savait rien du projet** dont on lui parlait : des compteurs, ni nom,
  ni dossier, ni outillage (`orchestration._Contexte`, bloc `projet`) ;
- **une proposition faite sur A, approuvée en regardant B, s'exécutait dans B** : le
  geste partait avec le projet de la fenêtre du clic (`MessageChat.projet_vise`) ;
- **sans projet, le run partait quand même**, et n'apparaissait dans la liste
  d'aucun projet (`RepondeurOrchestration.produire`, `_ouvrir_un_run`).

Les tests d'API montent l'app **entière** — vrai répondeur, vrai service
d'exécutions, deux projets réellement déclarés, un moteur muet — et lisent ce que
le moteur a reçu et ce que les routes de l'écran rendent. Seul le fournisseur est
substitué, à la fabrique : c'est le maillon qu'aucun test ne laisse réel (#195).
Le juge y est un double, donc la **qualité** du jugement n'est pas tenue ici — ce
qui l'est, c'est ce que le juge reçoit, et ce que le canal fait de son verdict.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.agents.store import AgentStore
from maestro.controltower.app import create_app
from maestro.controltower.bornes import AUCUNE_BORNE, BornesRun
from maestro.controltower.chat import (
    ChatStore,
    MessageChat,
    ProjetVise,
    transcription,
)
from maestro.controltower.events import InMemoryEventBus
from maestro.controltower.orchestration import (
    _PROMPT_ORCHESTRATION,
    AGENT_ORCHESTRATION,
    FAIT_SANS_PROJET,
    NOM_ORCHESTRATION,
    PHRASE_RUN_SANS_PROJET,
    RepondeurOrchestration,
    bloc_du_projet,
    outillage_en_clair,
)
from maestro.controltower.projets import ServiceProjets
from maestro.controltower.state import ControlTowerState
from maestro.engine import RunReport
from maestro.equipe import RoleValide, definition
from maestro.outillage.contexte import (
    NonTransmis,
    OutillageDuProjet,
    SkillDuProjet,
)
from maestro.projets import ProjetStore
from maestro.providers.base import ModelProvider

UTILISATEUR = "utilisateur"

#: L'objectif que le juge propose puis recopie sur l'accord — il ne ressemble à
#: aucun message du fil, pour qu'on voie à l'assertion que c'est lui qui part.
OBJECTIF = "Ajouter la pagination à la liste des dépenses"

#: Ce que l'`AGENTS.md` du projet outillé porte : une commande qu'aucun bloc du
#: contexte ne devrait recopier (le contenu se lit, il n'entre pas).
COMMANDE_DU_PROJET = "npm run test:unitaires"


def _verdict(nom: str, reponse: str, objectif: str = "") -> str:
    """La réponse du modèle au contrat de `_PROMPT_ORCHESTRATION` : prose, puis la ligne."""
    ligne = json.dumps({"verdict": nom, "objectif": objectif}, ensure_ascii=False)
    return f"{reponse}\n%%MAESTRO%% {ligne}"


class JugeQuiNote(ModelProvider):
    """Le fournisseur que l'app résout : il rend un verdict fixé, et range ce qu'il reçoit.

    Trois appels passent par lui — le tour de lecture, le juge, la rédaction d'un
    geste — et ils se reconnaissent à leur **prompt système**, jamais au texte : les
    ranger par position ferait lire au test le prompt d'un autre appel.
    """

    name = "juge-qui-note"

    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.juges: list[str] = []
        self.systemes: list[str] = []
        self.redactions: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(
        self, prompt: str, *, model: str, system_prompt: str | None = None
    ) -> str:
        systeme = system_prompt or ""
        if "tu décides ce qu'il faut LIRE" in systeme:
            return "RIEN"
        if "Il vient de se passer quelque chose" in systeme:
            self.redactions.append(prompt)
            return "C'est noté."
        self.juges.append(prompt)
        self.systemes.append(systeme)
        return self.verdict


class MoteurMuet:
    """Moteur injecté à la place du vrai : il n'appelle rien et note où il a travaillé."""

    def __init__(self) -> None:
        self.projets: list[str | None] = []
        self.objectifs: list[str] = []

    def __call__(self, **reglages: Any) -> MoteurMuet:
        return self

    async def run(self, objectif: str, *, projet_id: str | None = None, **reste: Any) -> RunReport:
        self.projets.append(projet_id)
        self.objectifs.append(objectif)
        return RunReport(objectif=objectif, resultats=())


@pytest.fixture()
def maison(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier utilisateur factice : sous Windows, `tmp_path` vit dans `AppData`,
    que la validation de racine refuse à raison (#221)."""
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


def _outiller(racine: Path) -> None:
    """Écrit dans `racine` l'outillage que Maestro y aurait posé : `AGENTS.md`, un skill."""
    (racine / "AGENTS.md").write_text(
        f"# Instructions\n\nLes tests se lancent par `{COMMANDE_DU_PROJET}`.\n", encoding="utf-8"
    )
    skill = racine / ".agents" / "skills" / "tester"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: tester\ndescription: Lance les tests unitaires du projet.\n---\n\nCorps.\n",
        encoding="utf-8",
    )
    manifeste = racine / ".maestro" / "outillage" / "manifeste.json"
    manifeste.parent.mkdir(parents=True)
    manifeste.write_text(
        json.dumps(
            {
                "manifeste": 1,
                "genere_par": "maestro",
                "entrees": [
                    {"chemin": "AGENTS.md", "role": "instructions", "portee": "fichier"},
                    {"chemin": ".agents/skills/tester/SKILL.md", "role": "skill"},
                ],
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def projets(maison: Path, tmp_path: Path) -> ServiceProjets:
    """Deux projets réellement déclarés — `depensio`, outillé, et `carnet`, qui ne l'est pas."""
    service = ServiceProjets(ProjetStore(tmp_path / "depot"))
    for nom in ("depensio", "carnet"):
        (maison / nom).mkdir()
        service.creer(nom, str(maison / nom))
    _outiller(maison / "depensio")
    return service


@pytest.fixture()
def ids(projets: ServiceProjets) -> dict[str, str]:
    """L'identifiant de chaque projet, par son nom."""
    return {fiche["nom"]: fiche["id"] for fiche in projets.lister()}


@pytest.fixture()
def agents(tmp_path: Path, projets: ServiceProjets) -> AgentStore:
    """Chaque projet a son équipe : ces tests parlent du projet, pas du recrutement (#1146)."""
    store = AgentStore(tmp_path / "agents")
    for fiche in projets.lister():
        store.pour_projet(fiche["id"]).ecrire(
            definition(
                RoleValide(
                    nom="dev-du-projet",
                    role="Développeur",
                    competences=("frontend",),
                    playbook="Tu écris le code.",
                )
            )
        )
    return store


@pytest.fixture()
def moteur() -> MoteurMuet:
    return MoteurMuet()


def _client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    projets: ServiceProjets,
    agents: AgentStore,
    moteur: MoteurMuet,
    juge: JugeQuiNote,
) -> TestClient:
    """L'app entière, son fournisseur substitué à la fabrique."""
    monkeypatch.setattr("maestro.providers.factory.provider_from_settings", lambda *a, **k: juge)
    return TestClient(
        create_app(
            bus=InMemoryEventBus(),
            state=ControlTowerState(),
            chat_store=ChatStore(tmp_path / "chat"),
            projets=projets,
            agents_store=agents,
            fabrique_moteur=moteur,
        )
    )


def _dire(client: TestClient, contenu: str, projet_id: str | None) -> dict[str, Any]:
    """Envoie un message au fil depuis la fenêtre `projet_id`, et rend la réponse."""
    reponse = client.post(
        f"/api/chat/{NOM_ORCHESTRATION}/messages",
        json={"contenu": contenu, "projet_id": projet_id},
    )
    assert reponse.status_code == 201
    return reponse.json()["messages"][1]


def _runs_de(client: TestClient, portee: str) -> set[str]:
    """Les runs que la liste de l'écran rend pour cette portée."""
    reponse = client.get("/api/executions", params={"projet": portee})
    assert reponse.status_code == 200
    return {run["run_id"] for run in reponse.json()}


# ── critère 1 : le contexte porte le projet actif — nom, dossier, équipe, outillage ─


def test_le_juge_recoit_le_nom_le_dossier_l_equipe_et_l_outillage_du_projet(
    monkeypatch, tmp_path, projets, agents, moteur, ids, maison
) -> None:
    """L'échantillon fautif : « N runs en cours », et rien du projet dont on parle.

    Le prompt du juge porte désormais, pour le projet de la fenêtre, son nom, son
    dossier, son équipe (le bloc de #1223, lu par la règle du routeur) et son
    outillage — le chemin des instructions et l'index des skills, tels que les
    agents les reçoivent. Le **contenu** de l'`AGENTS.md`, lui, n'y entre pas : il
    se lit quand la question en dépend.
    """
    juge = JugeQuiNote(_verdict("echange", "Le projet est outillé."))
    with _client(monkeypatch, tmp_path, projets, agents, moteur, juge) as client:
        _dire(client, "Qu'est-ce que ce projet sait déjà faire ?", ids["depensio"])

    prompt = juge.juges[-1]
    assert "Le projet de cette conversation : « depensio »" in prompt
    assert (maison / "depensio").as_posix() in prompt
    assert "Développeur « dev-du-projet »" in prompt
    assert "- instructions : AGENTS.md" in prompt
    assert "skill « tester » — Lance les tests unitaires du projet." in prompt
    assert COMMANDE_DU_PROJET not in prompt


def test_un_projet_non_outille_se_dit_non_outille(
    monkeypatch, tmp_path, projets, agents, moteur, ids
) -> None:
    """Un projet sans outillage n'est pas un projet dont on ne sait rien : le fil le dit."""
    juge = JugeQuiNote(_verdict("echange", "Pas encore outillé."))
    with _client(monkeypatch, tmp_path, projets, agents, moteur, juge) as client:
        _dire(client, "Comment se lancent ses tests ?", ids["carnet"])

    prompt = juge.juges[-1]
    assert "Le projet de cette conversation : « carnet »" in prompt
    assert "Outillage du projet : aucun" in prompt


def test_l_outillage_en_clair_dit_chacune_des_trois_situations() -> None:
    """Pas outillé, outillé, outillé sans rien de lisible — et aucune ne se tait."""
    neuf = outillage_en_clair(OutillageDuProjet())
    outille = outillage_en_clair(
        OutillageDuProjet(
            manifeste=".maestro/outillage/manifeste.json",
            chemin_instructions="AGENTS.md",
            instructions="Lance les tests.",
            skills=(SkillDuProjet(nom="lancer", description="Lance l'app.", chemin="s/SKILL.md"),),
        )
    )
    illisible = outillage_en_clair(
        OutillageDuProjet(
            manifeste=".maestro/outillage/manifeste.json",
            non_transmis=(NonTransmis(chemin="AGENTS.md", role="instructions", raison="vide"),),
        )
    )

    assert neuf.startswith("Outillage du projet : aucun")
    assert "- instructions : AGENTS.md" in outille
    assert "skill « lancer » — Lance l'app. → s/SKILL.md" in outille
    assert "Lance les tests." not in outille
    assert "rien de ce qu'il déclare n'est transmis" in illisible
    assert "non transmis : AGENTS.md — vide" in illisible


def test_le_cadre_dit_au_juge_que_la_proposition_part_dans_le_projet_de_la_conversation() -> None:
    """La règle vit dans le prompt, le fait dans le contexte — la forme de #1146."""
    assert "LE PROJET DE CETTE CONVERSATION" in _PROMPT_ORCHESTRATION
    assert "Un run travaille toujours dans UN projet" in _PROMPT_ORCHESTRATION
    assert "C'est dans ce projet que travaillera tout run" in bloc_du_projet(
        ProjetVise(id="prj-1", nom="depensio", racine="E:/depensio")
    )


# ── critère 1 : une proposition enregistre son projet, et s'exécute dans celui-là ──


def test_une_proposition_porte_son_projet_jusqu_au_message(
    monkeypatch, tmp_path, projets, agents, moteur, ids, maison
) -> None:
    """Le projet est écrit sur la proposition, au moment où elle est faite."""
    juge = JugeQuiNote(_verdict("proposition", "Je vous propose ce run. Je lance ?", OBJECTIF))
    with _client(monkeypatch, tmp_path, projets, agents, moteur, juge) as client:
        propose = _dire(client, "Ajoute la pagination", ids["depensio"])
        relu = client.get(f"/api/chat/{NOM_ORCHESTRATION}").json()["messages"][-1]

    attendu = {
        "id": ids["depensio"],
        "nom": "depensio",
        "racine": (maison / "depensio").as_posix(),
    }
    assert propose["proposition"] == OBJECTIF
    assert propose["projet_vise"] == attendu
    # Persisté, pas seulement rendu : c'est ce que le geste relira.
    assert relu["projet_vise"] == attendu
    assert moteur.objectifs == []


def test_approuvee_depuis_un_autre_projet_elle_s_execute_dans_le_sien(
    monkeypatch, tmp_path, projets, agents, moteur, ids
) -> None:
    """L'échantillon fautif du ticket : proposée sur A, approuvée en regardant B.

    Le geste part avec le projet de la fenêtre du clic — B —, et le run va dans A :
    il figure dans la liste de A, pas dans celle de B, et c'est A que le moteur a
    reçu, donc les tâches du plan (#222).
    """
    juge = JugeQuiNote(_verdict("proposition", "Je vous propose ce run. Je lance ?", OBJECTIF))
    with _client(monkeypatch, tmp_path, projets, agents, moteur, juge) as client:
        _dire(client, "Ajoute la pagination", ids["depensio"])
        geste = client.post(
            f"/api/chat/{NOM_ORCHESTRATION}/cadrage",
            json={"approuve": True, "projet_id": ids["carnet"]},
        )
        assert geste.status_code == 201
        run_id = geste.json()["messages"][1]["run_id"]

        assert run_id in _runs_de(client, ids["depensio"])
        assert run_id not in _runs_de(client, ids["carnet"])
    assert moteur.projets == [ids["depensio"]]
    assert moteur.objectifs == [OBJECTIF]
    # Et le modèle qui en parle sait où le run est parti — pas dans la fenêtre
    # d'où la personne a cliqué.
    assert "Il travaille dans le projet « depensio »" in juge.redactions[-1]


class JugeEnDeuxTours(JugeQuiNote):
    """Il propose au premier jugement, accorde au second — le protocole tapé (#688)."""

    def __init__(self) -> None:
        super().__init__("")
        self._tours = [
            _verdict("proposition", "Je vous propose ce run. Je lance ?", OBJECTIF),
            _verdict("accord", "C'est parti.", OBJECTIF),
        ]

    async def generate(
        self, prompt: str, *, model: str, system_prompt: str | None = None
    ) -> str:
        self.verdict = self._tours[min(len(self.juges), len(self._tours) - 1)]
        return await super().generate(prompt, model=model, system_prompt=system_prompt)


def test_un_oui_tape_depuis_un_autre_projet_ouvre_le_run_dans_celui_de_la_proposition(
    monkeypatch, tmp_path, projets, agents, moteur, ids
) -> None:
    """Le même défaut par la zone de saisie : l'accord tapé suit la proposition, pas la fenêtre."""
    juge = JugeEnDeuxTours()
    with _client(monkeypatch, tmp_path, projets, agents, moteur, juge) as client:
        _dire(client, "Ajoute la pagination", ids["depensio"])
        repondu = _dire(client, "oui, vas-y", ids["carnet"])

    assert repondu["run_id"]
    assert moteur.projets == [ids["depensio"]]
    # Le juge du second tour a vu où la proposition travaillerait.
    assert "[Run proposé sur la carte, dans le projet « depensio »" in juge.juges[1]


def test_la_transcription_dit_ou_travaillera_le_run_propose() -> None:
    """Ce que la carte montre, le modèle le relit — un « oui » tardif sait ce qu'il approuve."""
    propose = MessageChat(
        agent=NOM_ORCHESTRATION,
        auteur=NOM_ORCHESTRATION,
        contenu="Je lance ?",
        proposition=OBJECTIF,
        projet_vise=ProjetVise(id="prj-1", nom="depensio", racine="E:/depensio"),
    )

    assert "[Run proposé sur la carte, dans le projet « depensio » (E:/depensio)]" in (
        transcription([propose])
    )


def test_un_projet_vise_se_relit_et_une_ligne_d_avant_ce_lot_n_en_porte_aucun() -> None:
    """Le champ voyage par le stockage ; une proposition écrite avant #1180 se relit sans."""
    vise = ProjetVise(id="prj-1", nom="depensio", racine="E:/depensio")
    ecrit = MessageChat(
        agent=NOM_ORCHESTRATION, auteur=NOM_ORCHESTRATION, contenu="?", proposition=OBJECTIF,
        projet_vise=vise,
    )
    ancienne = {"agent": NOM_ORCHESTRATION, "auteur": NOM_ORCHESTRATION, "proposition": OBJECTIF}

    assert MessageChat.from_dict(ecrit.to_ligne()).projet_vise == vise
    assert MessageChat.from_dict(ancienne).projet_vise is None


# ── critère 2 : sans projet, aucun run ne part du fil ─────────────────────────────


def test_sans_projet_une_demande_de_travail_ne_propose_aucun_run(
    monkeypatch, tmp_path, projets, agents, moteur
) -> None:
    """Installation neuve, ou porte d'un nouveau projet : la demande n'a pas de carte de run.

    Le juge a reçu le fait — aucun projet, et ce qui débloque — et la règle, dans
    son cadre : c'est lui qui propose de créer le projet ou d'en choisir un. Le
    canal, lui, tient la structure : même un verdict `proposition` ne pose aucune
    demande de cadrage, et rien n'atteint le moteur.
    """
    juge = JugeQuiNote(_verdict("proposition", "Je vous propose ce run. Je lance ?", OBJECTIF))
    with _client(monkeypatch, tmp_path, projets, agents, moteur, juge) as client:
        repondu = _dire(client, "Crée-moi une application d'agenda", None)

    assert repondu["proposition"] == ""
    assert repondu["projet_vise"] is None
    assert repondu["run_id"] == ""
    assert moteur.objectifs == []
    assert FAIT_SANS_PROJET in juge.juges[-1]
    # Les projets déjà déclarés sont sous ses yeux : c'est parmi eux qu'il propose
    # d'en choisir un (#1294, `naissance.contexte`).
    assert "« depensio »" in juge.juges[-1] and "« carnet »" in juge.juges[-1]
    assert "propose d'en créer un pour ce travail" in juge.systemes[-1]


def test_sans_projet_un_accord_tape_n_ouvre_rien(
    monkeypatch, tmp_path, projets, agents, moteur
) -> None:
    """Le « oui » qui ouvrait le run orphelin : il n'atteint plus le lanceur."""
    juge = JugeQuiNote(_verdict("accord", "Il faut d'abord un projet.", OBJECTIF))
    with _client(monkeypatch, tmp_path, projets, agents, moteur, juge) as client:
        repondu = _dire(client, "oui, lance", None)
        orphelins = _runs_de(client, "aucun")

    assert repondu["run_id"] == ""
    assert moteur.objectifs == []
    assert orphelins == set()


def test_un_projet_que_l_api_ne_declare_plus_vaut_aucun_projet(
    monkeypatch, tmp_path, projets, agents, moteur
) -> None:
    """Une fenêtre restée sur un projet retiré : le run n'aurait nulle part où apparaître."""
    juge = JugeQuiNote(_verdict("proposition", "Je lance ?", OBJECTIF))
    with _client(monkeypatch, tmp_path, projets, agents, moteur, juge) as client:
        repondu = _dire(client, "Ajoute la pagination", "prj-0123456789ab")

    assert repondu["proposition"] == ""
    assert FAIT_SANS_PROJET in juge.juges[-1]


def test_une_proposition_d_avant_ce_lot_approuvee_sans_projet_ne_part_pas(
    tmp_path,
) -> None:
    """La dernière porte : une proposition sans projet à elle, approuvée d'une fenêtre sans projet.

    `produire` n'en pose plus ; celle-ci a été écrite avant #1180. Le geste est
    reçu, le lanceur n'est jamais appelé, et le fil dit l'empêchement — c'est le
    code seul qui sait que rien ne s'est ouvert.
    """
    lances: list[str | None] = []

    async def lanceur(
        objectif: str,
        projet_id: str | None = None,
        bornes: BornesRun = AUCUNE_BORNE,
        contexte_sources: str = "",
    ) -> dict[str, str]:
        lances.append(projet_id)
        return {"run_id": "run-orphelin"}

    repondeur = RepondeurOrchestration(lanceur=lanceur, provider=JugeQuiNote(""))
    ancienne = MessageChat(
        agent=NOM_ORCHESTRATION, auteur=NOM_ORCHESTRATION, contenu="Je lance ?",
        proposition=OBJECTIF,
    )

    reponse = asyncio.run(
        repondeur.trancher_cadrage(
            AGENT_ORCHESTRATION, [ancienne], approuve=True, objectif=OBJECTIF, projet_id=None
        )
    )

    assert lances == []
    assert reponse.run_id == ""
    assert reponse.contenu.strip() == PHRASE_RUN_SANS_PROJET


def test_avec_un_projet_la_meme_demande_se_propose(
    monkeypatch, tmp_path, projets, agents, moteur, ids
) -> None:
    """Le témoin : ce n'est pas la demande qui fait taire la carte, c'est l'absence de projet."""
    juge = JugeQuiNote(_verdict("proposition", "Je vous propose ce run. Je lance ?", OBJECTIF))
    with _client(monkeypatch, tmp_path, projets, agents, moteur, juge) as client:
        repondu = _dire(client, "Crée-moi une application d'agenda", ids["carnet"])

    assert repondu["proposition"] == OBJECTIF
    assert FAIT_SANS_PROJET not in juge.juges[-1]
