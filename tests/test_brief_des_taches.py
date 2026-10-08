"""Chaque tâche reçoit le brief approuvé du run (#1402).

Un agent ne voyait que **sa** tâche, jamais le brief que la personne avait approuvé
pour le run. Sur p4 (run `ad4d2ce8c3bf`), l'agent d'une tâche de vérification a
écrit : « Les six critères d'acceptation sont **reconstitués** d'après la
description de la tâche, car aucun brief n'existe dans le projet. » Le vérificateur
de fin de tâche « déduisait » lui aussi les critères absents, sans le brief.

Aucun appel réseau : l'agent et le vérificateur sont joués par un même
`ModelProvider` factice (il reconnaît le vérificateur à son prompt système), le
cadrage par un second, et les commandes de contrôle par un joueur factice.

Critères couverts :

① le message de chaque tâche porte une section « Brief approuvé du run » —
  objectif, hors-périmètre, critères d'acceptation —, et l'entrée consignée au
  journal la montre ; le brief qui y figure est celui qui a été **retenu**,
  correction humaine comprise, et un run sans brief garde son message d'avant ;
② le vérificateur de fin de tâche lit le même brief, à côté de la tâche, dans
  chacun de ses prompts ; les critères de la tâche restent son contrat ;
③ un run qui ne refait pas son cadrage — repris sur son plan (#1391) ou relancé sur
  son brief (#349) — garde le brief approuvé : il traverse l'ordre du run, l'hôte
  et le moteur.

La preuve sur le réel (critère 3 du ticket) n'est pas ici : c'est un run de la
vraie stack, consigné sur le ticket.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import (
    ControlTowerState,
    Event,
    InMemoryEventBus,
    InMemoryEventLog,
    RegistreBattementsMemoire,
    create_app,
)
from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.brief import evenement_demande_brief
from maestro.controltower.causes import CAUSE_EXTINCTION
from maestro.controltower.events import EVENEMENT_BRIEF_DECISION, EVENEMENT_EXECUTION_STATUT
from maestro.controltower.hote import HoteMort, HoteRun, OrdreRun
from maestro.controltower.hote_detache import ordre_depuis_dict, ordre_vers_dict
from maestro.controltower.state import BRIEF_APPROUVE, EXECUTION_ANNULEE, EXECUTION_EN_COURS
from maestro.engine import (
    MODE_BRIEF_AUTO,
    MODE_BRIEF_HUMAIN,
    STATUT_TERMINEE,
    DecisionBrief,
    DemandeBrief,
    OrchestrationEngine,
)
from maestro.engine.acquis import EtatAcquis, MagasinAcquisMemoire
from maestro.engine.executor import _build_task_description
from maestro.engine.verification import (
    SYSTEME,
    Controle,
    Livraison,
    VerificateurTaches,
)
from maestro.orchestrator import BRIEF_SYSTEM_PROMPT, Brief, Orchestrator
from maestro.orchestrator.schema import Task, validate_plan
from maestro.providers.base import ModelProvider
from maestro.sandbox.verification import Execution
from maestro.telemetry import RunJournal, StepUsage
from maestro.telemetry.costs import ETAPE_BRIEF

#: Le titre de la section que chaque tâche reçoit — ce que l'agent lit en premier.
SECTION = "Brief approuvé du run"

#: Un interpréteur quelconque : le joueur factice ne le lance pas (cf.
#: `tests/test_verification_taches.py`, qui en donne la raison).
_INTERPRETE = ("bash", "-c")

_CRITERE = "bonjour.txt salue en français"
_COMMANDE = "grep -q Bonjour bonjour.txt"

#: Le brief approuvé du run : chaque section que la tâche doit lire est
#: reconnaissable, et les autres aussi — pour vérifier qu'elles n'y entrent pas.
BRIEF = Brief.from_dict(
    {
        "objectif": "Accueillir la personne en français",
        "perimetre": ["Un fichier de salut"],
        "hors_perimetre": ["Traduire en anglais"],
        "contraintes": ["Aucune dépendance externe"],
        "criteres_acceptation": ["bonjour.txt dit Bonjour", "Le salut tient en une ligne"],
        "hypotheses": ["Une seule personne à accueillir"],
        "questions": [],
    }
)

#: Le plan de p4 en petit : une tâche qui produit, puis une tâche de vérification
#: — celle qui « reconstituait » les critères du run faute de les avoir.
_PLAN = json.dumps(
    [
        {
            "id": "salut",
            "titre": "Écrire le salut",
            "description": f"Écrire bonjour.txt.\n\nCritères de réussite : {_CRITERE}.",
            "competences_requises": ["backend"],
            "format_sortie": "Un fichier bonjour.txt",
            "dependances": [],
        },
        {
            "id": "verifier-salut",
            "titre": "Vérifier le salut",
            "description": (
                "Vérifier que le livrable tient les critères d'acceptation du run.\n\n"
                f"Critères de réussite : {_CRITERE}."
            ),
            "competences_requises": ["backend"],
            "format_sortie": "Un rapport de vérification",
            "dependances": ["salut"],
        },
    ],
    ensure_ascii=False,
)


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #


class AgentEtJuge(ModelProvider):
    """Joue l'agent (`run_agent`) et le vérificateur (`generate` sous `SYSTEME`).

    Retient chaque message de session et chaque prompt du vérificateur : c'est ce
    que le ticket regarde.
    """

    name = "agent-et-juge"

    def __init__(self) -> None:
        self.sessions: list[str] = []
        self.verifications: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        if system_prompt == SYSTEME:
            self.verifications.append(prompt)
            return json.dumps({"controles": [{"critere": _CRITERE, "commande": _COMMANDE}]})
        return "TEXTE"

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **_):
        self.sessions.append(prompt)
        (Path(workspace) / "bonjour.txt").write_text("Bonjour", encoding="utf-8")
        return "J'ai écrit le salut."


class Cadrage(ModelProvider):
    """Rend le brief à l'étape de brief, le plan à la planification — et compte les deux."""

    name = "cadrage"

    def __init__(self, brief: Brief = BRIEF) -> None:
        self._brief = brief
        self.briefs: list[str] = []
        self.plans: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        if system_prompt == BRIEF_SYSTEM_PROMPT:
            self.briefs.append(prompt)
            return json.dumps(self._brief.to_dict(), ensure_ascii=False)
        self.plans.append(prompt)
        return _PLAN


def _joueur(commande, cwd, *, interprete, delai_s):
    """Le joueur factice : constate pour de vrai que l'agent a écrit son fichier."""
    assert commande == _COMMANDE
    present = (Path(cwd) / "bonjour.txt").is_file()
    return Execution(code=0 if present else 1, sortie="", duree_s=0.01)


def _moteur(agent: AgentEtJuge, cadrage: Cadrage, *, arbitre=None) -> OrchestrationEngine:
    return OrchestrationEngine(
        agent,
        Orchestrator(cadrage, model="claude-opus-4-8"),
        verificateur=VerificateurTaches(agent, joueur=_joueur, interprete=_INTERPRETE),
        arbitre_brief=arbitre,
    )


def _issue(journal: RunJournal, tache_id: str):
    """L'étape terminale d'une tâche au journal — celle qui porte son entrée."""
    return next(r for r in journal.records if r.etape == tache_id)


# --------------------------------------------------------------------------- #
# ① Le message de chaque tâche porte le brief approuvé, et le journal le montre
# --------------------------------------------------------------------------- #


def test_le_message_de_chaque_tache_porte_le_brief_approuve_et_le_journal_le_montre():
    """Le premier critère, sur le chemin d'un vrai run : cadrage, plan, deux tâches.

    Chaque session d'agent — la tâche qui produit comme celle qui vérifie — lit
    l'objectif, le hors-périmètre et les critères d'acceptation du run. Sa propre
    description vient d'abord : c'est elle, son contrat. Et l'entrée que le journal
    consigne pour chaque tâche est ce message-là, brief compris.
    """
    agent, cadrage = AgentEtJuge(), Cadrage()
    journal = RunJournal(run_id="run-1402")

    rapport = asyncio.run(
        _moteur(agent, cadrage).run("Dis bonjour", journal=journal, mode_brief=MODE_BRIEF_AUTO)
    )

    assert all(r.ok for r in rapport.resultats), [r.erreur for r in rapport.resultats]
    assert len(agent.sessions) == 2
    for message in agent.sessions:
        assert SECTION in message
        assert "Objectif : Accueillir la personne en français" in message
        assert "Hors périmètre :\n- Traduire en anglais" in message
        assert "Contraintes :\n- Aucune dépendance externe" in message
        assert (
            "Critères d'acceptation du run :\n- bonjour.txt dit Bonjour\n"
            "- Le salut tient en une ligne"
        ) in message
        # La description de la tâche d'abord : le brief est à côté, pas à la place.
        assert message.index("Description :") < message.index(SECTION)
        # Ce qui a servi à approuver, et pas à travailler, n'y entre pas.
        assert "Une seule personne à accueillir" not in message
    for tache_id in ("salut", "verifier-salut"):
        entree = _issue(journal, tache_id).entree
        assert entree.startswith("Tâche : ")
        assert SECTION in entree
        assert "- bonjour.txt dit Bonjour" in entree


def test_c_est_le_brief_retenu_que_les_taches_recoivent():
    """Corrigé par la personne avant d'être approuvé : c'est la correction qui part.

    La règle de `DecisionBrief.retenu` — « ce qui est décomposé est le brief tel
    qu'il a été approuvé » — vaut pour ce que les tâches lisent comme pour le plan.
    """
    corrige = Brief.from_dict(
        {**BRIEF.to_dict(), "criteres_acceptation": ["bonjour.txt dit Bonjour, avec majuscule"]}
    )

    async def arbitre(demande: DemandeBrief) -> DecisionBrief:
        return DecisionBrief(approuve=True, brief=corrige)

    agent = AgentEtJuge()
    asyncio.run(
        _moteur(agent, Cadrage(), arbitre=arbitre).run(
            "Dis bonjour", mode_brief=MODE_BRIEF_HUMAIN
        )
    )

    assert agent.sessions
    for message in agent.sessions:
        assert "- bonjour.txt dit Bonjour, avec majuscule" in message
        assert "Le salut tient en une ligne" not in message


def test_sans_brief_le_message_et_le_journal_sont_ceux_d_avant():
    """Un run `sans` n'a pas de brief : rien n'est inventé à sa place, au mot près."""
    agent = AgentEtJuge()
    journal = RunJournal()

    asyncio.run(_moteur(agent, Cadrage()).run("Dis bonjour", journal=journal))

    assert agent.sessions
    assert all(SECTION not in message for message in agent.sessions)
    assert all("<brief>" not in prompt for prompt in agent.verifications)
    assert SECTION not in _issue(journal, "salut").entree
    tache = validate_plan(json.loads(_PLAN))[0]
    assert _build_task_description(tache, []) == _build_task_description(tache, [], None)


# --------------------------------------------------------------------------- #
# ② Le vérificateur lit le même brief ; les critères de la tâche restent son contrat
# --------------------------------------------------------------------------- #


def test_le_verificateur_de_fin_de_tache_lit_le_meme_brief():
    """Le second critère, sur le chemin d'un vrai run : chaque vérification a le brief."""
    agent = AgentEtJuge()

    asyncio.run(_moteur(agent, Cadrage()).run("Dis bonjour", mode_brief=MODE_BRIEF_AUTO))

    assert len(agent.verifications) == 2  # une par tâche, chacune tenue d'emblée
    for prompt in agent.verifications:
        assert f"<brief>\n{BRIEF.cadre()}\n</brief>" in prompt
        # La tâche d'abord, son contrat ; le brief ensuite, son cadre.
        assert prompt.index("</tache>") < prompt.index("<brief>")


_TACHE = Task(
    id="t",
    titre="Écrire le salut",
    description=f"Critères de réussite : {_CRITERE}.",
    competences_requises=("backend",),
    format_sortie="Un fichier",
)


class _Reponses(ModelProvider):
    """Le vérificateur seul : rend ses réponses dans l'ordre, retient chaque prompt."""

    name = "reponses"

    def __init__(self, *reponses: str) -> None:
        self._reponses = list(reponses)
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.prompts.append(prompt)
        return self._reponses[min(len(self.prompts), len(self._reponses)) - 1]


def test_chaque_prompt_du_verificateur_porte_le_brief_encadre_comme_donnee(tmp_path):
    """Établir, contre-expertiser, relire : le brief est dans les trois, entre balises.

    Une commande qui ne tient pas déclenche la contre-expertise (« le défaut est-il
    au livrable ou au contrôle ? »), une lecture déjà établie se relit à la livraison
    suivante : le juge a le même cadre à chaque fois qu'il juge.
    """
    controles = json.dumps({"controles": [{"critere": _CRITERE, "commande": _COMMANDE}]})
    provider = _Reponses(controles, json.dumps({"commandes": []}))
    verificateur = VerificateurTaches(provider, joueur=_joueur, interprete=_INTERPRETE)

    # Rien d'écrit : la commande ne tient pas, la contre-expertise est appelée.
    asyncio.run(
        verificateur.verifier(
            _TACHE, Livraison(sortie="fait", espace=tmp_path), modele="m", brief=BRIEF
        )
    )
    lecture = Controle(critere="le salut tient en une ligne", lecture="une seule ligne")
    relecture = _Reponses(json.dumps({"lectures": [{"n": 1, "tenu": True, "preuve": "ok"}]}))
    asyncio.run(
        VerificateurTaches(relecture).verifier(
            _TACHE, Livraison(sortie="fait"), modele="m", etablis=(lecture,), brief=BRIEF
        )
    )

    etablir, contre_expertise = provider.prompts
    assert "<non_tenues>" in contre_expertise
    (relire,) = relecture.prompts
    for prompt in (etablir, contre_expertise, relire):
        assert f"<brief>\n{BRIEF.cadre()}\n</brief>" in prompt


def test_sans_brief_le_prompt_du_verificateur_est_celui_d_avant(tmp_path):
    controles = json.dumps({"controles": [{"critere": _CRITERE, "commande": _COMMANDE}]})
    avec, sans = _Reponses(controles), _Reponses(controles)
    (tmp_path / "bonjour.txt").write_text("Bonjour", encoding="utf-8")
    livraison = Livraison(sortie="fait", espace=tmp_path)

    asyncio.run(
        VerificateurTaches(sans, joueur=_joueur, interprete=_INTERPRETE).verifier(
            _TACHE, livraison, modele="m"
        )
    )
    asyncio.run(
        VerificateurTaches(avec, joueur=_joueur, interprete=_INTERPRETE).verifier(
            _TACHE, livraison, modele="m", brief=BRIEF
        )
    )

    assert "<brief>" not in sans.prompts[0]
    # Le brief s'ajoute ; il ne retire ni ne réécrit rien de ce qui était lu.
    assert avec.prompts[0].replace(f"\n\n{_bloc_brief_attendu()}", "") == sans.prompts[0]


def _bloc_brief_attendu() -> str:
    """Le bloc tel que le vérificateur le lit — son introduction comprise."""
    from maestro.engine.verification import _bloc_brief

    return _bloc_brief(BRIEF)


def test_le_juge_sait_que_le_contrat_reste_la_tache():
    """Ce que le prompt système dit du brief : le cadre, jamais le contrat.

    Le juge contrôle les critères **de la tâche**. Le brief l'aide à les comprendre,
    et à déduire ceux qu'elle n'écrit pas — c'est ce qu'il « déduisait » sans lui —,
    mais un critère du run que la tâche ne prend pas en charge ne devient pas un
    contrôle de cette tâche : la vérifier dessus la ferait échouer pour le travail
    d'une autre.
    """
    systeme = " ".join(SYSTEME.split())
    assert "brief approuvé du run" in systeme
    assert "il ne remplace pas son contrat" in systeme
    assert "un critère du run qu'elle ne prend pas en charge" in systeme


# --------------------------------------------------------------------------- #
# ③ Un run qui ne refait pas son cadrage garde le brief approuvé
# --------------------------------------------------------------------------- #


def test_un_run_repris_sur_son_plan_garde_le_brief_qu_il_ne_recadre_pas():
    """#1391 : la reprise ne refait ni cadrage ni plan — le brief arrive avec l'ordre."""
    agent, cadrage = AgentEtJuge(), Cadrage()
    etat = EtatAcquis.depuis(validate_plan(json.loads(_PLAN)), [])

    rapport = asyncio.run(
        _moteur(agent, cadrage).run("Dis bonjour", reprise=etat, brief_approuve=BRIEF)
    )

    assert all(r.ok for r in rapport.resultats)
    assert cadrage.briefs == [] and cadrage.plans == []
    assert len(agent.sessions) == 2
    assert all(SECTION in message for message in agent.sessions)
    assert all("<brief>" in prompt for prompt in agent.verifications)


def test_un_run_relance_sur_son_brief_le_donne_a_ses_taches_sans_le_repayer():
    """#349 : la relance décompose la synthèse en mode `sans` — et garde le brief."""
    agent, cadrage = AgentEtJuge(), Cadrage()

    asyncio.run(_moteur(agent, cadrage).run(BRIEF.synthese(), brief_approuve=BRIEF))

    assert cadrage.briefs == []  # le cadrage n'est pas repayé
    assert len(cadrage.plans) == 1
    assert all(SECTION in message for message in agent.sessions)


def test_un_cadrage_refait_l_emporte_sur_le_brief_donne():
    """Le brief qu'un run rédige lui-même est le sien : celui qu'on lui passe s'efface."""
    agent = AgentEtJuge()
    ancien = Brief.from_dict({**BRIEF.to_dict(), "objectif": "Un objectif d'avant"})

    asyncio.run(
        _moteur(agent, Cadrage()).run(
            "Dis bonjour", mode_brief=MODE_BRIEF_AUTO, brief_approuve=ancien
        )
    )

    assert all("Accueillir la personne en français" in m for m in agent.sessions)
    assert all("Un objectif d'avant" not in m for m in agent.sessions)


def test_le_brief_traverse_l_ordre_du_run_tel_quel():
    """L'ordre voyage en JSON vers le process détaché : le brief n'y perd rien."""
    ordre = OrdreRun(run_id="run-1402", objectif="Dis bonjour", brief=BRIEF)

    relu = ordre_depuis_dict(json.loads(json.dumps(ordre_vers_dict(ordre))))

    assert relu.brief == BRIEF
    assert ordre_depuis_dict(ordre_vers_dict(OrdreRun(run_id="r", objectif="x"))).brief is None


# --- Le service : la reprise et la relance confient le brief à l'hôte --------------------


RUN = "a1402b000001"
PROJET = "prj-1402"


class HoteEspion(HoteRun):
    """Un hôte qui ne lance rien et retient chaque ordre — c'est l'ordre qu'on juge."""

    def __init__(self) -> None:
        self.ordres: list[OrdreRun] = []
        self._en_vol: list[str] = []

    async def lancer(self, ordre: OrdreRun) -> None:
        self.ordres.append(ordre)
        self._en_vol.append(ordre.run_id)

    async def annuler(self, run_id: str, *, delai_s: float) -> bool:
        if run_id not in self._en_vol:
            return False
        self._en_vol.remove(run_id)
        return True

    def en_vol(self, run_id: str) -> bool:
        return run_id in self._en_vol

    def runs_en_vol(self) -> tuple[str, ...]:
        return tuple(self._en_vol)

    def ramasser(self) -> tuple[HoteMort, ...]:
        return ()

    async def fermer(self, *, delai_s: float) -> None:
        return None


class MoteurQuiRetient:
    """Le moteur de l'hôte en process : retient ce qu'on lui confie, puis ne rend jamais la main."""

    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []

    def __call__(self, **_reglages: object) -> MoteurQuiRetient:
        return self

    async def run(self, objectif: str, **reglages: Any) -> None:
        self.runs.append({"objectif": objectif, **reglages})
        await asyncio.Event().wait()


def _journal(*evenements: Event) -> InMemoryEventLog:
    journal = InMemoryEventLog()

    async def consigner() -> None:
        for evenement in evenements:
            await journal.consigner(evenement)

    asyncio.run(consigner())
    return journal


def _lancement(mode_brief: str) -> Event:
    return Event(
        type=EVENEMENT_EXECUTION_STATUT,
        run_id=RUN,
        titre="Dis bonjour",
        description="Dis bonjour",
        agent="orchestrateur",
        role="Orchestrateur",
        statut=EXECUTION_EN_COURS,
        mode_brief=mode_brief,
        projet_id=PROJET,
    )


def _etape_de_brief() -> tuple[Event, ...]:
    """L'étape `brief` d'un run du fil, par le vrai journal et le vrai pont (#1174)."""
    record = RunJournal(run_id=RUN).consigne(
        etape=ETAPE_BRIEF,
        nom="Brief de l'objectif",
        agent="orchestrateur",
        role="Orchestrateur",
        statut=STATUT_TERMINEE,
        entree="Dis bonjour",
        sortie="2 critère(s) d'acceptation, 0 question(s)",
        usage=StepUsage(),
        projet_id=PROJET,
        brief=BRIEF.to_dict(),
    )
    return evenements_depuis_step(record.to_dict())


def _eteint() -> Event:
    return Event(
        type=EVENEMENT_EXECUTION_STATUT,
        run_id=RUN,
        agent="orchestrateur",
        role="Orchestrateur",
        statut=EXECUTION_ANNULEE,
        detail="Maestro s'est éteint",
        cause=CAUSE_EXTINCTION,
    )


def _acquis() -> MagasinAcquisMemoire:
    magasin = MagasinAcquisMemoire()
    asyncio.run(magasin.poser_plan(RUN, validate_plan(json.loads(_PLAN))))
    return magasin


def test_la_reprise_confie_le_brief_approuve_a_l_hote():
    """Un run du fil éteint en route : il reprend sur son plan, **avec** son brief."""
    hote = HoteEspion()
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        event_log=_journal(_lancement(MODE_BRIEF_AUTO), *_etape_de_brief(), _eteint()),
        battements=RegistreBattementsMemoire(),
        hote_run=hote,
        acquis=_acquis(),
    )
    with TestClient(app) as client:
        reponse = client.post(f"/api/executions/{RUN}/reprendre")

    assert reponse.status_code == 200, reponse.text
    (ordre,) = hote.ordres
    assert ordre.reprise is not None
    assert ordre.brief == BRIEF


def test_un_brief_jamais_approuve_ne_part_pas_avec_la_reprise():
    """Un brief rédigé que personne n'a tranché n'est pas un brief approuvé (#1174)."""
    hote = HoteEspion()
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        event_log=_journal(
            _lancement(MODE_BRIEF_HUMAIN),
            evenement_demande_brief(DemandeBrief(run_id=RUN, objectif="Dis bonjour", brief=BRIEF)),
            _eteint(),
        ),
        battements=RegistreBattementsMemoire(),
        hote_run=hote,
        acquis=_acquis(),
    )
    with TestClient(app) as client:
        reponse = client.post(f"/api/executions/{RUN}/reprendre")

    assert reponse.status_code == 200, reponse.text
    (ordre,) = hote.ordres
    assert ordre.brief is None


def test_la_relance_donne_le_brief_approuve_au_moteur_du_nouveau_run():
    """La relance (#349) jusqu'au moteur de l'hôte en process : `brief_approuve` arrive."""
    moteur = MoteurQuiRetient()
    journal = _journal(
        _lancement(MODE_BRIEF_HUMAIN),
        evenement_demande_brief(DemandeBrief(run_id=RUN, objectif="Dis bonjour", brief=BRIEF)),
        Event(type=EVENEMENT_BRIEF_DECISION, run_id=RUN, statut=BRIEF_APPROUVE, brief=BRIEF),
    )
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        event_log=journal,
        battements=RegistreBattementsMemoire(),
        fabrique_moteur=moteur,
    )
    with TestClient(app) as client:
        reponse = client.post(f"/api/executions/{RUN}/relancer")
        assert reponse.status_code == 202, reponse.text
        limite = time.monotonic() + 5.0
        while not moteur.runs:
            if time.monotonic() > limite:  # pragma: no cover - filet anti-blocage
                pytest.fail("le moteur du run relancé n'a jamais été appelé")
            time.sleep(0.02)

    (run,) = moteur.runs
    assert run["brief_approuve"] == BRIEF
    assert run["objectif"] == BRIEF.synthese().strip()
