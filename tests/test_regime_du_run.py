"""Ce que l'orchestrateur dit d'un run repose sur ce que Maestro fera (ticket #1323).

Au bouclage du 2026-09-25 (`main` à `aa390ca`, vraie stack, vrai modèle), le fil a
affirmé deux fois sur deux une chose fausse sur la suite d'un run :

- **S3** — après la validation de l'équipe : « Comme le rangement va déplacer des
  fichiers, le run vous demandera votre accord avant de commencer. » Le run n'a
  posé **aucune** demande de validation : depuis #1226/#1237 un acte dans le
  projet passe sans personne, et l'acte que l'objectif nomme est imputé à l'accord
  du cadrage ;
- **S4 puis P8** — sur un projet dont le run précédent avait reçu la borne
  « s'interrompt à 1 tokens » à son accord, la proposition suivante : « si ce
  réglage n'a pas changé, ce nouveau run a toutes les chances de s'arrêter pareil.
  Je ne sais pas où ce plafond a été défini ». La carte de la même proposition
  disait « Aucune borne », et le run est allé au bout.

Le modèle devinait parce qu'on ne lui donnait pas les faits. Cette suite tient
qu'on les lui donne — **la politique réelle de l'équipe**, lue là où l'exécution
la lira, et **les bornes de chaque run**, consignées à son lancement — et elle ne
juge jamais le texte qu'il en tire (#1169) : le fournisseur est un double qui
**note ses prompts**, et c'est sur eux que portent les assertions.

① les deux situations du bouclage, **rejouées sur l'app entière** — vraie
   création d'équipe, vraie politique écrite sur le disque, vrai service
   d'exécutions ; seuls le modèle et le moteur sont des doubles. Et le cas où
   Maestro ne sait pas : une politique illisible se dit avec sa cause ;
② **les bornes**, un fait du run qui les a reçues — sur l'événement de
   lancement, à travers le journal durable, dans la projection et sur la fiche
   du run, « aucune » comprise et « non consignées » dit ;
③ **ce que la politique fait des actes** — le rendu du régime, agent par agent,
   dans les mots de l'intention d'un rôle, et ce que le bloc dit selon qu'il y a
   une équipe, aucune, ou pas de projet ;
④ **où le régime entre** — le prompt du juge, les faits des gestes qui mettent
   un run devant la personne, et nulle part ailleurs ; une lecture qui lève ne
   coûte que son bloc.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.agents.capacity import CapacityStore
from maestro.agents.configuration import ConfigurationAgents
from maestro.agents.mcp import McpStore
from maestro.agents.permissions import EntreeArbitrage, PermissionStore, PolitiqueOutils
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.regime_d_execution import REGIME_EXECUTION, REGIME_PORTEE
from maestro.agents.store import AgentStore, SurchargeStore
from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.bornes import AUCUNE_BORNE, BornesRun
from maestro.controltower.causes import CAUSE_PLAFOND_COUT
from maestro.controltower.chat import UTILISATEUR, ChatStore, MessageChat
from maestro.controltower.equipe import CompositeurEquipe
from maestro.controltower.events import EVENEMENT_EXECUTION_STATUT, Event
from maestro.controltower.generation_agent import GenerateurDefinitionAgent
from maestro.controltower.orchestration import (
    AGENT_ORCHESTRATION,
    NOM_ORCHESTRATION,
    RepondeurOrchestration,
    detail_du_run,
    fiche_du_run,
)
from maestro.controltower.projets import ServiceProjets
from maestro.controltower.regime import (
    CE_QUI_NE_SE_PREVOIT_PAS,
    CE_QUI_REVIENT,
    DE_FRONT_NON_VERSIONNE,
    DE_FRONT_VERSIONNE,
    ENTETE,
    PHRASES_DU_BRIEF,
    REGLE_DE_L_ACTE_ACCORDE,
    REGLE_DES_BORNES,
    SANS_AGENT,
    MembreDeLEquipe,
    bornes_du_run,
    regime_d_un_run,
    regime_des_actes,
)
from maestro.controltower.state import EXECUTION_ECHEC, EXECUTION_EN_COURS
from maestro.decideur import Decideur
from maestro.engine import RunReport
from maestro.engine.brief import MODE_BRIEF_AUTO, MODE_BRIEF_HUMAIN
from maestro.equipe.proposition import OUTIL_EXECUTION
from maestro.portee import PORTEE_PROJET
from maestro.projets import ProjetStore
from maestro.providers.base import ModelProvider
from maestro.telemetry import PlafondDepenseDepasse

CHAT = "/api/chat/orchestrateur"

#: Les objectifs que le juge double propose — ceux des deux scénarios du bouclage.
OBJECTIF_S3 = (
    "Ranger les sources du projet dans une organisation de dossiers cohérente, puis "
    "ajouter à sa racine un fichier NOTES.md qui décrit ce que contient le projet."
)
OBJECTIF_S4 = (
    "Créer à la racine du projet un fichier NOTES.md qui décrit en trois lignes ce "
    "que contient le projet."
)

#: Ce que le moteur de S4 a levé, au mot près (`PlafondDepense.verifie`).
ARRET_SUR_LA_BORNE = (
    "plafond de tokens dépassé : 38053 tokens consommés sur l'exécution pour un "
    "plafond de 1 — tâche stoppée."
)


# --------------------------------------------------------------- le harnais


class FournisseurQuiNote(ModelProvider):
    """Le modèle du fil, sans modèle : il propose, rédige, et **note ce qu'il reçoit**.

    Les trois appels du canal se distinguent par leur prompt système, comme le
    vrai canal les distingue : le tour de lecture (« RIEN » — rien à lire ici),
    la rédaction d'un geste, et le juge, qui propose toujours `objectif`. Le
    récit de fin d'un run reçoit une phrase et n'est pas noté. Tout autre appel
    — la lecture d'outillage d'un projet importé — ne répond pas, et la route
    retombe sur l'analyse des tables, ce qu'elle promet elle-même.

    Il ne décide de rien d'après le contenu des faits : ce qu'on vérifie, ce sont
    les faits qu'il **reçoit**, jamais ce qu'il en dirait.
    """

    name = "fournisseur-qui-note"

    def __init__(self, objectif: str) -> None:
        self.objectif = objectif
        self.juges: list[str] = []
        self.redactions: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(
        self,
        prompt: str,
        *,
        model: str,
        system_prompt: str | None = None,
        effort: str | None = None,
    ) -> str:
        systeme = system_prompt or ""
        if "tu décides ce qu'il faut LIRE" in systeme:
            return "RIEN"
        if "Il vient de se passer quelque chose" in systeme:
            self.redactions.append(prompt)
            return "C'est noté."
        if "%%MAESTRO%%" in systeme:
            # Le juge, reconnu à son contrat : le récit de fin d'un run s'ouvre sur
            # la même phrase que lui, et il s'écrit **en tâche de fond** — le
            # ranger ici ferait lire son prompt comme celui du dernier message.
            self.juges.append(prompt)
            return (
                f'%%MAESTRO%% {{"verdict": "proposition", "objectif": "{self.objectif}"}}\n'
                "Je vous propose ce travail."
            )
        if "vient de se terminer" in systeme:
            return "Le run est terminé."
        raise RuntimeError("hors ligne (test)")


class _GenerateurHorsLigne(GenerateurDefinitionAgent):
    """Aucun playbook généré : chaque rôle retombe sur celui de son gabarit."""

    async def proposer(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("hors ligne (test)")


class _CompositeurHorsLigne(CompositeurEquipe):
    """Aucune équipe composée par un modèle : la proposition suit les règles des gabarits."""

    async def ecrire(self, prompt: str) -> str:
        raise RuntimeError("hors ligne (test)")


class MoteurQuiRespecteSesBornes:
    """Le moteur d'un run : il s'arrête sur un plafond de tokens **s'il en a reçu un**.

    C'est la conduite du vrai moteur réduite à ce qui compte ici — un run lancé
    avec la borne « 1 token » s'arrête dessus, un run lancé sans borne va au
    bout —, et les bornes arrivent par le chemin réel : le geste, le service
    d'exécutions, les garde-fous passés à la fabrique.
    """

    def __init__(self) -> None:
        self.plafonds: list[int | None] = []

    def __call__(self, **reglages: Any) -> _RunDuMoteur:
        plafond = getattr(reglages.get("guardrails"), "plafond_tokens", None)
        self.plafonds.append(plafond)
        return _RunDuMoteur(plafond)


class _RunDuMoteur:
    def __init__(self, plafond_tokens: int | None) -> None:
        self._plafond = plafond_tokens

    async def run(self, objectif: str, **reste: Any) -> RunReport:
        if self._plafond is not None:
            raise PlafondDepenseDepasse(ARRET_SUR_LA_BORNE)
        return RunReport(objectif=objectif, resultats=())


@pytest.fixture()
def atelier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Le dossier des projets, sous un dossier utilisateur factice (#221 : pas sous AppData)."""
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    atelier = tmp_path / "atelier"
    atelier.mkdir()
    return atelier


@pytest.fixture()
def gabarits(tmp_path: Path) -> ConfigurationAgents:
    """Les six dépôts d'agents, dans `tmp_path` — jamais sous `core/` du dépôt."""
    racine = tmp_path / "core"
    return ConfigurationAgents(
        agents=AgentStore(racine / "agents"),
        surcharges=SurchargeStore(racine / "surcharges"),
        playbooks=PlaybookStore(racine / "playbooks"),
        permissions=PermissionStore(racine / "permissions"),
        mcp=McpStore(racine / "mcp"),
        capacites=CapacityStore(racine / "capacite"),
    )


def _fil(
    tmp_path: Path,
    atelier: Path,
    gabarits: ConfigurationAgents,
    monkeypatch: pytest.MonkeyPatch,
    objectif: str,
) -> Iterator[tuple[TestClient, FournisseurQuiNote, MoteurQuiRespecteSesBornes]]:
    """L'app réelle et son fil, sur des dépôts jetables ; modèle et moteur sont des doubles."""
    fournisseur = FournisseurQuiNote(objectif)
    monkeypatch.setattr(
        "maestro.providers.factory.provider_from_settings", lambda *a, **k: fournisseur
    )
    moteur = MoteurQuiRespecteSesBornes()
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=ServiceProjets(ProjetStore(tmp_path / "depot"), racines_exploration=(atelier,)),
        agents_store=gabarits.agents,
        surcharges=gabarits.surcharges,
        playbooks=gabarits.playbooks,
        permissions=gabarits.permissions,
        mcp=gabarits.mcp,
        capacites=gabarits.capacites,
        generateur_agent=_GenerateurHorsLigne(),
        compositeur_equipe=_CompositeurHorsLigne(),
        lecteur_outillage=fournisseur,
        chat_store=ChatStore(tmp_path / "chat"),
        fabrique_moteur=moteur,
    )
    with TestClient(app) as client:
        yield client, fournisseur, moteur


@pytest.fixture()
def fil_s3(
    tmp_path: Path, atelier: Path, gabarits: ConfigurationAgents, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, FournisseurQuiNote, MoteurQuiRespecteSesBornes]]:
    yield from _fil(tmp_path, atelier, gabarits, monkeypatch, OBJECTIF_S3)


@pytest.fixture()
def fil_s4(
    tmp_path: Path, atelier: Path, gabarits: ConfigurationAgents, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, FournisseurQuiNote, MoteurQuiRespecteSesBornes]]:
    yield from _fil(tmp_path, atelier, gabarits, monkeypatch, OBJECTIF_S4)


def _projet_existant(client: TestClient, atelier: Path, nom: str) -> str:
    """Un petit projet Python réel, non versionné, déclaré comme l'écran le déclare."""
    racine = atelier / nom
    (racine / "src").mkdir(parents=True)
    (racine / "src" / "app.py").write_text("print('salut')\n", encoding="utf-8")
    (racine / "pyproject.toml").write_text(
        f'[project]\nname = "{nom}"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    reponse = client.post(
        "/api/projets", json={"nom": nom, "racine": str(racine), "origine": "existant"}
    )
    assert reponse.status_code == 201, reponse.text
    return str(reponse.json()["id"])


def _validee(proposition: dict[str, Any]) -> dict[str, Any]:
    """L'équipe proposée, rapportée telle quelle — ce que l'étape d'équipe renvoie."""
    return {
        "proposition_id": proposition["id"],
        "roles": [
            {
                "nom": r["nom"],
                "role": r["role"],
                "competences": r["competences"],
                "playbook": r["playbook"],
                "instances": r["instances"],
                "gabarit": r["gabarit"],
                "skills": [
                    {"nom": s["nom"], "chemin": s["chemin"], "commandes": s["commandes"]}
                    for s in r["skills"]
                ],
                "politique": r["politique"],
            }
            for r in proposition["roles"]
        ],
    }


def _attendre_la_fin(client: TestClient, run_id: str, projet: str) -> dict[str, Any]:
    """Le résumé du run une fois soldé — le lancement rend la main avant que le moteur ne tourne."""
    limite = time.monotonic() + 10
    while True:
        resume = client.get(f"/api/executions/{run_id}", params={"projet": projet}).json()
        if resume.get("statut") not in (None, "en_cours"):
            return resume
        if time.monotonic() > limite:  # pragma: no cover - filet anti-blocage
            pytest.fail(f"run {run_id} resté en cours")
        time.sleep(0.02)


def _phrase(regime: str) -> str:
    """Une phrase de régime de l'intention d'un rôle, sans son espace de tête."""
    return regime.strip()


# ------------------------------------ ① les deux situations du bouclage, rejouées


def test_s3_apres_l_equipe_validee_la_redaction_recoit_la_politique_que_l_equipe_porte(
    fil_s3: tuple[TestClient, FournisseurQuiNote, MoteurQuiRespecteSesBornes],
    atelier: Path,
    gabarits: ConfigurationAgents,
) -> None:
    """S3 : ce que le fil dit après la validation de l'équipe s'écrit sur la politique réelle.

    Le projet n'a pas d'équipe, la demande de rangement en fait proposer une,
    elle est validée d'un geste par la route de l'écran, et elle est écrite dans
    le projet par la voie de #1040 — politique comprise. La rédaction qui suit
    reçoit alors ce que cette politique fait : l'exécution **passe sans
    personne** dans le dossier du projet, et ce qui en sort ou efface le
    préexistant revient à une personne. Rien ne l'invitait plus à inventer un
    accord que le run ne demanderait pas.

    L'échantillon fautif est tenu du même geste : la phrase du régime **humain**
    n'y est pas — les faits viennent de la politique écrite, pas d'un texte fixe.
    """
    client, fournisseur, _ = fil_s3
    projet = _projet_existant(client, atelier, "s3-sans-equipe")
    envoi = client.post(
        f"{CHAT}/messages",
        json={
            "contenu": "Range les sources de ce projet : ajoute un fichier NOTES.md qui "
            "décrit ce qu'il contient.",
            "projet_id": projet,
        },
    )
    assert envoi.status_code == 201, envoi.text
    assert envoi.json()["messages"][1]["recrutement"]["objectif"] == OBJECTIF_S3
    proposition = client.post(f"/api/projets/{projet}/equipe/proposition").json()

    recrutement = client.post(
        f"{CHAT}/recrutement",
        json={"approuve": True, "conversation": None, **_validee(proposition)},
    )

    assert recrutement.status_code == 201, recrutement.text
    # La politique que l'équipe porte **sur le disque**, lue comme l'exécution la lira.
    cfg = gabarits.pour_projet(projet)
    decisions = [
        cfg.permissions.lire(role["nom"]).decide(OUTIL_EXECUTION) for role in proposition["roles"]
    ]
    assert {(d.decideur, d.portee) for d in decisions} == {(Decideur.AUTO, PORTEE_PROJET)}
    redaction = fournisseur.redactions[-1]
    assert _phrase(REGIME_EXECUTION[Decideur.AUTO]) in redaction
    assert _phrase(REGIME_PORTEE[PORTEE_PROJET]) in redaction
    assert _phrase(REGIME_EXECUTION[Decideur.HUMAIN]) not in redaction
    # Les deux faits qui répondent à la phrase du bouclage : l'accord de la carte
    # est le seul qu'un run du fil attend pour démarrer, et le rangement que
    # l'objectif nomme est accordé avec lui.
    assert PHRASES_DU_BRIEF[MODE_BRIEF_AUTO] in redaction
    assert REGLE_DE_L_ACTE_ACCORDE in redaction

    lance = client.post(f"{CHAT}/cadrage", json={"approuve": True, "projet_id": projet})

    assert lance.status_code == 201, lance.text
    assert lance.json()["messages"][1]["run_id"]
    # Le geste qui ouvre le run parle de la suite, lui aussi : mêmes faits.
    assert _phrase(REGIME_PORTEE[PORTEE_PROJET]) in fournisseur.redactions[-1]
    assert REGLE_DE_L_ACTE_ACCORDE in fournisseur.redactions[-1]


def test_s3_le_juge_lit_la_politique_a_chaque_message_et_suit_ce_qui_la_change(
    fil_s3: tuple[TestClient, FournisseurQuiNote, MoteurQuiRespecteSesBornes],
    atelier: Path,
    gabarits: ConfigurationAgents,
) -> None:
    """Le juge reçoit la politique **du moment** : réglée autrement, elle se dit autrement.

    Une politique n'est pas une propriété de l'équipe figée à sa création : elle
    se règle depuis l'écran d'un agent (#262), et l'exécution la relit à chaque
    tâche. Le fil la relit donc à chaque message, sans quoi il annoncerait le
    régime d'hier.
    """
    client, fournisseur, _ = fil_s3
    projet = _projet_existant(client, atelier, "s3-regle")
    proposition = client.post(f"/api/projets/{projet}/equipe/proposition").json()
    creation = client.post(f"/api/projets/{projet}/equipe", json=_validee(proposition))
    assert creation.status_code == 201, creation.text
    demande = {"contenu": "Range les sources de ce projet.", "projet_id": projet}

    client.post(f"{CHAT}/messages", json=demande)
    avant = fournisseur.juges[-1]
    cfg = gabarits.pour_projet(projet)
    for role in proposition["roles"]:
        cfg.permissions.ecrire(
            role["nom"],
            PolitiqueOutils(ask=(EntreeArbitrage(OUTIL_EXECUTION, Decideur.HUMAIN),)),
        )
    client.post(f"{CHAT}/messages", json=demande)
    apres = fournisseur.juges[-1]

    assert _phrase(REGIME_EXECUTION[Decideur.AUTO]) in avant
    assert _phrase(REGIME_EXECUTION[Decideur.HUMAIN]) not in avant
    assert _phrase(REGIME_EXECUTION[Decideur.HUMAIN]) in apres
    assert _phrase(REGIME_EXECUTION[Decideur.AUTO]) not in apres


def test_une_politique_illisible_se_dit_avec_sa_cause_au_lieu_d_un_regime_invente(
    fil_s3: tuple[TestClient, FournisseurQuiNote, MoteurQuiRespecteSesBornes],
    atelier: Path,
    gabarits: ConfigurationAgents,
) -> None:
    """Quand Maestro ne sait pas, le fil le reçoit comme tel — la cause de la lecture, telle quelle.

    Une politique que la lecture refuse ferait échouer chaque tâche de cet agent
    avant tout acte (`Executor._politique_permissions`). Le fil ne la remplace
    ni par un régime par défaut, ni par un silence : il reçoit la cause exacte
    que l'exécution rencontrera.
    """
    client, fournisseur, _ = fil_s3
    projet = _projet_existant(client, atelier, "s3-illisible")
    proposition = client.post(f"/api/projets/{projet}/equipe/proposition").json()
    creation = client.post(f"/api/projets/{projet}/equipe", json=_validee(proposition))
    assert creation.status_code == 201, creation.text
    politiques = gabarits.pour_projet(projet).permissions
    causes: list[str] = []
    for role in proposition["roles"]:
        (politiques.racine / f"{role['nom']}.json").write_text("{ pas du json", encoding="utf-8")
        with pytest.raises(ValueError) as refus:
            politiques.lire(role["nom"])
        causes.append(str(refus.value))

    client.post(f"{CHAT}/messages", json={"contenu": "Range le projet.", "projet_id": projet})

    juge = fournisseur.juges[-1]
    assert all(cause in juge for cause in causes)
    assert _phrase(REGIME_EXECUTION[Decideur.AUTO]) not in juge


def test_s4_la_borne_d_un_run_passe_est_dite_la_sienne_et_la_proposition_suivante_n_en_herite_pas(
    fil_s4: tuple[TestClient, FournisseurQuiNote, MoteurQuiRespecteSesBornes], atelier: Path
) -> None:
    """S4 puis P8 : la borne d'un run passé est un fait de ce run, jamais un réglage qui dure.

    Le run de S4 est lancé d'un geste avec « 1 token » et s'arrête dessus. Dans une
    conversation neuve, une nouvelle demande : le juge reçoit la borne du run
    passé **telle qu'elle a été donnée à son accord**, et ce que sera celle du
    run qu'il propose — celle que la carte posera, aucune par défaut. Il n'a plus
    à supposer un « réglage » dont il ne sait pas où il vit.
    """
    client, fournisseur, moteur = fil_s4
    projet = _projet_existant(client, atelier, "s4-pourquoi")
    proposition = client.post(f"/api/projets/{projet}/equipe/proposition").json()
    creation = client.post(f"/api/projets/{projet}/equipe", json=_validee(proposition))
    assert creation.status_code == 201, creation.text
    client.post(
        f"{CHAT}/messages",
        json={"contenu": "Ajoute à ce projet un fichier NOTES.md.", "projet_id": projet},
    )
    lance = client.post(
        f"{CHAT}/cadrage", json={"approuve": True, "projet_id": projet, "plafond_tokens": 1}
    )
    run_borne = lance.json()["messages"][1]["run_id"]
    assert _attendre_la_fin(client, run_borne, projet)["statut"] == "echec"
    assert moteur.plafonds == [1]
    neuve = client.post(f"{CHAT}/conversations").json()["conversation"]["id"]

    client.post(
        f"{CHAT}/messages",
        json={
            "contenu": "Ajoute à ce projet un fichier NOTES.md.",
            "projet_id": projet,
            "conversation": neuve,
        },
    )

    juge = fournisseur.juges[-1]
    assert ARRET_SUR_LA_BORNE in juge, "le run passé et son arrêt sont bien sous ses yeux"
    assert BornesRun(plafond_tokens=1).en_phrase() in juge
    assert AUCUNE_BORNE.en_phrase() in juge
    # La borne est dite **sur la fiche de son run**, et la règle dit qu'elle y reste.
    assert f"- Run {run_borne} " in juge
    fiche = juge.split(f"- Run {run_borne} ", 1)[1].split("\n\n", 1)[0]
    assert bornes_du_run(BornesRun(plafond_tokens=1)) in fiche
    assert REGLE_DES_BORNES in juge

    lance = client.post(
        f"{CHAT}/cadrage", json={"approuve": True, "projet_id": projet, "conversation": neuve}
    )

    run_libre = lance.json()["messages"][1]["run_id"]
    assert _attendre_la_fin(client, run_libre, projet)["statut"] == "terminee"
    assert moteur.plafonds == [1, None]
    # Les faits du geste — ce qui vient avant le régime — disent les bornes de
    # **ce** run-ci : aucune, et non celle du run d'avant.
    geste = fournisseur.redactions[-1].split(ENTETE, 1)[0]
    assert AUCUNE_BORNE.en_phrase() in geste
    assert BornesRun(plafond_tokens=1).en_phrase() not in geste

    client.post(
        f"{CHAT}/messages",
        json={"contenu": "Où en est-on ?", "projet_id": projet, "conversation": neuve},
    )

    # Chaque run garde les siennes, « aucune » comprise : consignées au lancement
    # par le service d'exécutions, relues dans la projection.
    juge = fournisseur.juges[-1]
    fiche_libre = juge.split(f"- Run {run_libre} ", 1)[1].split("\n- Run ", 1)[0]
    assert bornes_du_run(AUCUNE_BORNE) in fiche_libre
    fiche_bornee = juge.split(f"- Run {run_borne} ", 1)[1].split("\n\n", 1)[0]
    assert bornes_du_run(BornesRun(plafond_tokens=1)) in fiche_bornee


# ------------------------------------ ② les bornes, un fait du run qui les a reçues


def _lancement(run_id: str, bornes: BornesRun | None) -> Event:
    return Event(
        type=EVENEMENT_EXECUTION_STATUT,
        run_id=run_id,
        statut=EXECUTION_EN_COURS,
        description=OBJECTIF_S4,
        projet_id="prj-essai",
        bornes=bornes,
    )


def test_les_bornes_voyagent_sur_le_lancement_et_survivent_au_journal() -> None:
    """L'événement de lancement porte les bornes, et le journal durable les relit.

    Trois cas qui ne se confondent pas : une borne posée, **aucune** borne — un
    fait, qui doit revenir comme tel —, et un lancement d'avant ce lot, qui n'en
    disait rien et revient `None`.
    """
    borne = BornesRun(plafond_tokens=1)
    assert Event.from_json(_lancement("r", borne).to_json()).bornes == borne
    assert Event.from_json(_lancement("r", AUCUNE_BORNE).to_json()).bornes == AUCUNE_BORNE
    ancien = _lancement("r", None).to_dict()
    del ancien["bornes"]
    assert Event.from_dict(ancien).bornes is None


def test_l_issue_d_un_run_n_efface_pas_les_bornes_de_son_lancement() -> None:
    """Posées au lancement, jamais retirées — la règle du ticket, du projet, du brief.

    L'issue qui arrête un run sur sa borne ne la porte pas : si elle l'effaçait,
    la fiche du run perdrait précisément ce qui explique son arrêt.
    """
    state = ControlTowerState()
    state.appliquer(_lancement("r", BornesRun(plafond_tokens=1)))
    state.appliquer(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id="r",
            statut=EXECUTION_ECHEC,
            detail=ARRET_SUR_LA_BORNE,
            cause=CAUSE_PLAFOND_COUT,
        )
    )

    execution = state.execution("r")
    assert execution is not None
    assert execution.bornes == BornesRun(plafond_tokens=1)


def test_la_fiche_et_le_detail_d_un_run_disent_ses_bornes_ou_qu_on_ne_les_connait_pas() -> None:
    """Chaque run dit les siennes ; un run lancé avant ce lot dit qu'elles ne sont pas consignées.

    Taire la ligne quand on ne sait pas laisserait le modèle devant un arrêt sur
    plafond sans rien pour le rattacher — le vide exact qu'il comblait en P8.
    """
    state = ControlTowerState()
    state.appliquer(_lancement("borne", BornesRun(plafond_tokens=1)))
    state.appliquer(_lancement("libre", AUCUNE_BORNE))
    state.appliquer(_lancement("ancien", None))

    def fiche(run_id: str) -> str:
        execution = state.execution(run_id)
        assert execution is not None
        return "\n".join(fiche_du_run(state, execution))

    assert bornes_du_run(BornesRun(plafond_tokens=1)) in fiche("borne")
    assert bornes_du_run(AUCUNE_BORNE) in fiche("libre")
    assert bornes_du_run(None) in fiche("ancien")
    assert bornes_du_run(BornesRun(plafond_tokens=1)) in detail_du_run(state)("borne")
    assert bornes_du_run(None) != bornes_du_run(AUCUNE_BORNE)


# ------------------------------------------- ③ ce que la politique fait des actes


def _membre(politique: PolitiqueOutils | None, *, nom: str = "dev") -> MembreDeLEquipe:
    return MembreDeLEquipe(role="Développeur", nom=nom, politique=politique)


#: La politique qu'une équipe proposée reçoit depuis #1226 : l'exécution d'office,
#: dans le dossier du projet.
POLITIQUE_DU_PROJET = PolitiqueOutils(
    ask=(EntreeArbitrage(OUTIL_EXECUTION, Decideur.AUTO, PORTEE_PROJET),)
)


def test_le_regime_d_un_agent_suit_le_cran_de_sa_politique_sur_le_shell() -> None:
    """Le shell se dit par le verdict que le hook lira, avec les phrases du régime.

    Les phrases sont celles de `maestro.agents.regime_d_execution` (`REGIME_EXECUTION`,
    `REGIME_PORTEE`), rangées à côté de celles que l'agent lit dans son propre prompt
    (#1405) : le fil et l'agent décrivent le même régime depuis le même endroit.
    """
    auto = regime_des_actes([_membre(POLITIQUE_DU_PROJET)])
    humain = regime_des_actes([_membre(PolitiqueOutils(ask=(OUTIL_EXECUTION,)))])

    assert REGIME_EXECUTION[Decideur.AUTO] + REGIME_PORTEE[PORTEE_PROJET] in auto
    assert REGIME_EXECUTION[Decideur.HUMAIN] in humain
    # La portée ne borne que le cran `auto` : sur `humain`, une personne tranche partout.
    assert _phrase(REGIME_PORTEE[PORTEE_PROJET]) not in humain
    assert _phrase(REGIME_EXECUTION[Decideur.AUTO]) not in humain


def test_un_shell_libre_refuse_ou_sans_politique_ne_se_dit_jamais_arbitre() -> None:
    """Hors d'une entrée `ask`, personne ne tranche : aucune phrase d'arbitrage ne s'écrit."""
    arbitrages = (REGIME_EXECUTION[Decideur.AUTO], REGIME_EXECUTION[Decideur.HUMAIN])
    textes = {
        "libre": regime_des_actes([_membre(PolitiqueOutils())]),
        "refuse": regime_des_actes([_membre(PolitiqueOutils(deny=(OUTIL_EXECUTION,)))]),
        "sans": regime_des_actes([_membre(None)]),
    }

    for texte in textes.values():
        assert not any(_phrase(phrase) in texte for phrase in arbitrages)
    assert f"Refusés d'office : {OUTIL_EXECUTION}." in textes["refuse"]
    assert len(set(textes.values())) == 3


def test_les_autres_outils_soumis_se_disent_a_part_du_shell() -> None:
    """L'acte accordé ne couvre que le shell : un autre outil soumis se nomme avec son décideur."""
    politique = PolitiqueOutils(
        ask=(
            EntreeArbitrage(OUTIL_EXECUTION, Decideur.AUTO, PORTEE_PROJET),
            EntreeArbitrage("mcp__slack", Decideur.HUMAIN),
            EntreeArbitrage("WebFetch", Decideur.AUTO),
        )
    )

    texte = regime_des_actes([_membre(politique)])

    assert "Attendent l'accord d'une personne : mcp__slack." in texte
    assert "Passent d'office, en étant tracés : WebFetch." in texte
    assert f"personne : {OUTIL_EXECUTION}" not in texte


def test_deux_agents_d_une_meme_equipe_gardent_chacun_leur_regime() -> None:
    """Une ligne par agent : ce que chacun fera sans personne dépend de **sa** politique."""
    texte = regime_des_actes(
        [
            _membre(POLITIQUE_DU_PROJET, nom="dev"),
            MembreDeLEquipe(
                role="QA", nom="qa", politique=PolitiqueOutils(ask=(OUTIL_EXECUTION,))
            ),
        ]
    )

    lignes = texte.splitlines()
    ligne_dev, ligne_qa = (
        next(ligne for ligne in lignes if f"« {nom} »" in ligne) for nom in ("dev", "qa")
    )
    assert _phrase(REGIME_EXECUTION[Decideur.AUTO]) in ligne_dev
    assert _phrase(REGIME_EXECUTION[Decideur.HUMAIN]) in ligne_qa


def test_une_politique_illisible_se_dit_avec_sa_cause() -> None:
    texte = regime_des_actes(
        [MembreDeLEquipe(role="Développeur", nom="dev", illisible="politique illisible : JSON")]
    )

    assert "politique illisible : JSON" in texte
    assert not any(_phrase(p) in texte for p in REGIME_EXECUTION.values())


def test_le_bloc_dit_ce_qu_il_sait_selon_qu_il_y_a_une_equipe_aucune_ou_pas_de_projet() -> None:
    """Trois situations, trois blocs honnêtes — et le cadrage comme les bornes partout.

    Avec une équipe : ses actes, l'acte accordé, ce qui revient à la personne.
    Sans agent : le fait qu'il n'y a pas encore de politique à lire. Sans projet :
    rien sur une équipe qu'on ne connaît pas.
    """
    equipe = regime_d_un_run([_membre(POLITIQUE_DU_PROJET)])
    vide = regime_d_un_run([])
    sans_projet = regime_d_un_run(None)

    for bloc in (equipe, vide, sans_projet):
        assert bloc.startswith(ENTETE)
        assert PHRASES_DU_BRIEF[MODE_BRIEF_AUTO] in bloc
        assert REGLE_DES_BORNES in bloc
    assert REGLE_DE_L_ACTE_ACCORDE in equipe
    assert CE_QUI_REVIENT in equipe
    assert CE_QUI_NE_SE_PREVOIT_PAS in equipe
    assert SANS_AGENT in vide
    assert REGLE_DE_L_ACTE_ACCORDE not in vide
    assert SANS_AGENT not in sans_projet
    assert "Actes de l'équipe" not in sans_projet


def test_le_bloc_dit_ce_qui_partira_de_front_selon_le_versionnement_du_projet() -> None:
    """#1299 : versionné, les tâches indépendantes partent de front ; sinon, une à une (#839).

    Le fait n'est dit que s'il est su : sans versionnement constaté, rien — la règle
    générique du projet non versionné, restée seule, s'appliquait à tort à un
    projet versionné (passage `20260927-214542` de S11).
    """
    membres = [_membre(POLITIQUE_DU_PROJET)]

    versionne = regime_d_un_run(membres, versionne=True)
    non_versionne = regime_d_un_run(membres, versionne=False)
    inconnu = regime_d_un_run(membres)

    assert DE_FRONT_VERSIONNE in versionne and DE_FRONT_NON_VERSIONNE not in versionne
    assert DE_FRONT_NON_VERSIONNE in non_versionne and DE_FRONT_VERSIONNE not in non_versionne
    assert "Tâches de front" not in inconnu
    # Sans agent, aucune tâche ne partira : rien à dire de leur cadence.
    assert "Tâches de front" not in regime_d_un_run([], versionne=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git introuvable")
def test_le_juge_lit_le_versionnement_du_projet_a_chaque_message(
    fil_s4: tuple[TestClient, FournisseurQuiNote, MoteurQuiRespecteSesBornes],
    atelier: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Le projet versionné par le geste de l'écran se dit tel au message suivant (#1299).

    Relu là où l'exécution le relit — le seul lecteur de projets —, jamais retenu :
    un fil qui garderait le régime d'avant annoncerait « une à une » à des tâches
    qui partent de front.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig-absent"))
    client, fournisseur, _ = fil_s4
    projet = _projet_existant(client, atelier, "vitrine")
    proposition = client.post(f"/api/projets/{projet}/equipe/proposition").json()
    equipe = client.post(f"/api/projets/{projet}/equipe", json=_validee(proposition))
    assert equipe.status_code == 201, equipe.text

    client.post(f"{CHAT}/messages", json={"contenu": "Maquette le site.", "projet_id": projet})
    assert DE_FRONT_NON_VERSIONNE in fournisseur.juges[-1]

    versionne = client.post(f"/api/projets/{projet}/versionner")
    assert versionne.status_code == 200, versionne.text
    client.post(f"{CHAT}/messages", json={"contenu": "Et maintenant ?", "projet_id": projet})

    assert DE_FRONT_VERSIONNE in fournisseur.juges[-1]
    assert DE_FRONT_NON_VERSIONNE not in fournisseur.juges[-1]


def test_le_cadrage_dit_le_regime_de_brief_que_le_lanceur_pose() -> None:
    """Le bloc suit le mode qu'on lui donne : un lanceur qui en change ne fait pas mentir le fil."""
    humain = regime_d_un_run(None, mode_brief=MODE_BRIEF_HUMAIN)

    assert PHRASES_DU_BRIEF[MODE_BRIEF_HUMAIN] in humain
    assert PHRASES_DU_BRIEF[MODE_BRIEF_AUTO] not in humain


def test_la_regle_des_bornes_parle_avec_les_mots_de_la_carte() -> None:
    """Le défaut se dit comme la carte le dit : deux régimes pour un même run, c'est un de trop."""
    assert AUCUNE_BORNE.en_phrase() in REGLE_DES_BORNES


# ----------------------------------------- ④ le répondeur : où le régime entre


class JugeQuiNote(ModelProvider):
    """Un juge qui propose et une rédaction qui répond — et qui note leurs prompts, rangés."""

    name = "juge-qui-note"

    def __init__(self) -> None:
        self.juges: list[str] = []
        self.redactions: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(
        self, prompt: str, *, model: str, system_prompt: str | None = None
    ) -> str:
        if "Il vient de se passer quelque chose" in (system_prompt or ""):
            self.redactions.append(prompt)
            return "C'est noté."
        self.juges.append(prompt)
        return (
            f'%%MAESTRO%% {{"verdict": "proposition", "objectif": "{OBJECTIF_S4}"}}\n'
            "Je vous propose ce travail."
        )


def _demande(contenu: str) -> list[MessageChat]:
    return [MessageChat(agent=NOM_ORCHESTRATION, auteur=UTILISATEUR, contenu=contenu)]


async def _lanceur(
    objectif: str, projet_id: str | None, bornes: BornesRun, contexte: str
) -> dict[str, str]:
    return {"run_id": "run-1", "statut": "en_cours"}


def _regime_de_test(projet_id: str | None) -> str:
    return regime_d_un_run([_membre(POLITIQUE_DU_PROJET)]) if projet_id else regime_d_un_run(None)


def test_le_juge_recoit_le_regime_du_projet_de_la_fenetre() -> None:
    juge = JugeQuiNote()

    asyncio.run(
        RepondeurOrchestration(provider=juge, regime=_regime_de_test).produire(
            AGENT_ORCHESTRATION, _demande("Range le projet."), projet_id="prj-essai"
        )
    )

    assert _regime_de_test("prj-essai") in juge.juges[-1]


def test_un_regime_qui_leve_ne_coute_que_son_bloc() -> None:
    """La règle de `_sans_echec` : une lecture qui n'aboutit pas coûte un bloc, pas la réponse."""
    juge = JugeQuiNote()

    def casse(_projet: str | None) -> str:
        raise RuntimeError("dépôt disparu")

    reponse = asyncio.run(
        RepondeurOrchestration(provider=juge, regime=casse).produire(
            AGENT_ORCHESTRATION, _demande("Range le projet."), projet_id="prj-essai"
        )
    )

    assert reponse.contenu
    assert ENTETE not in juge.juges[-1]


def test_le_lancement_dit_les_bornes_du_geste_puis_ce_que_le_run_fera() -> None:
    """Les faits du geste d'abord — les bornes que la carte vient de poser —, le régime ensuite."""
    juge = JugeQuiNote()
    posees = BornesRun(plafond_cout_usd=2, parallelisme=1)
    repondeur = RepondeurOrchestration(lanceur=_lanceur, provider=juge, regime=_regime_de_test)

    reponse = asyncio.run(
        repondeur.trancher_cadrage(
            AGENT_ORCHESTRATION,
            _demande("Ajoute NOTES.md."),
            approuve=True,
            objectif=OBJECTIF_S4,
            projet_id="prj-essai",
            bornes=posees,
        )
    )

    assert reponse.run_id == "run-1"
    geste, regime = juge.redactions[-1].split(ENTETE, 1)
    assert posees.en_phrase() in geste
    assert REGLE_DE_L_ACTE_ACCORDE in regime


def test_un_refus_ne_parle_pas_de_la_suite_d_un_run_qui_n_existe_pas() -> None:
    """Le régime n'accompagne que les gestes qui mettent un run devant la personne."""
    juge = JugeQuiNote()
    repondeur = RepondeurOrchestration(lanceur=_lanceur, provider=juge, regime=_regime_de_test)

    asyncio.run(
        repondeur.trancher_cadrage(
            AGENT_ORCHESTRATION,
            _demande("Ajoute NOTES.md."),
            approuve=False,
            objectif=OBJECTIF_S4,
            projet_id="prj-essai",
        )
    )

    assert ENTETE not in juge.redactions[-1]
