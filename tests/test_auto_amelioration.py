"""Auto-amélioration des playbooks — de l'échec consigné à la proposition appliquée (#139, #137).

Tout tient sur des **fournisseurs factices** : aucun réseau, aucune clé, aucun modèle
appelé. Quatre sections :

① **extraction des échecs** (`echecs_du_run`) : ce que l'analyse reçoit d'un run ;
② **moteur d'analyse** (`AnalyseurEchecs`) : la proposition écrite en brouillon, et les
   cas où rien ne doit être écrit (#139) ;
③ **endpoint** `POST /api/playbooks/{agent}/propositions` : l'analyse à la demande (#139) ;
④ **bout en bout et garde-fou** (#137) : un vrai run du moteur qui échoue → analyse →
   proposition → application humaine → le playbook adopté est chargé **à chaud** par le
   moteur déjà en vie (#78) ; et, en miroir, la garantie qu'une proposition non appliquée
   n'est **jamais** chargée et qu'un rejet ne touche pas la version courante.

La boucle et sa prudence sur le coût sont documentées dans
[docs/22-auto-amelioration-playbooks.md](../docs/22-auto-amelioration-playbooks.md).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from maestro.agents.catalog import DEFAULT_AGENTS, Agent
from maestro.agents.playbooks import PLAYBOOK_DEFAUTS, PlaybookStore
from maestro.controltower.app import create_app
from maestro.controltower.auto_amelioration import (
    MARQUEUR_PLAYBOOK,
    AnalyseurEchecs,
    RevisionIndisponible,
    echecs_du_run,
)
from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.events import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_TACHE_STATUT,
    Event,
    InMemoryEventBus,
)
from maestro.controltower.state import ControlTowerState, EtatExecution
from maestro.engine import STATUT_ECHEC, STATUT_TERMINEE, OrchestrationEngine
from maestro.orchestrator import Orchestrator
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal

_RATIONALE = "Le timeout revient : j'ajoute une consigne de patience."
_REPONSE = f"{_RATIONALE}\n{MARQUEUR_PLAYBOOK}\nPlaybook révisé."


class FournisseurScript(ModelProvider):
    """Fournisseur factice : enregistre chaque appel et rend une réponse scriptée."""

    name = "script"

    def __init__(self, reponse: str = _REPONSE) -> None:
        self.reponse = reponse
        self.appels: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.appels.append({"prompt": prompt, "model": model, "system_prompt": system_prompt})
        return self.reponse


class FournisseurEnPanne(ModelProvider):
    """Fournisseur factice qui échoue à chaque appel (simule un fournisseur indisponible)."""

    name = "panne"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        raise RuntimeError("fournisseur indisponible")


def _agent(nom="developpeur", role="Développeur", modele="claude-sonnet-5",
           prompt="Playbook du code."):
    """Fiche catalogue factice, de quoi analyser sans charger le vrai catalogue."""
    return Agent(nom=nom, role=role, competences=frozenset({"backend"}),
                 modele=modele, prompt_systeme=prompt)


def _echec(tache_id="t1", *, agent="developpeur", titre="Écrire l'API",
           detail="Outil X en timeout.", run_id="run-1"):
    """Événement `tache.statut` au statut « echec » pour peupler une exécution."""
    return Event(type=EVENEMENT_TACHE_STATUT, run_id=run_id, tache_id=tache_id,
                 titre=titre, agent=agent, statut="echec", detail=detail)


# ------------------------------------------------------------- extraction des échecs


def test_echecs_du_run_ne_retient_que_les_echecs_de_l_agent():
    """Seuls les `tache.statut` « echec » du bon agent sortent, dans l'ordre, avec leur raison."""
    execution = EtatExecution(
        run_id="run-1",
        evenements=[
            _echec("t1", titre="Écrire l'API", detail="Outil X en timeout."),
            Event(type=EVENEMENT_TACHE_STATUT, run_id="run-1", tache_id="t2",
                  titre="Tester", agent="developpeur", statut="terminee"),  # pas un échec
            _echec("t3", agent="qa", detail="Assertion KO"),  # autre agent
            _echec("t4", titre="Migrer le schéma", detail="Colonne absente."),
            Event(type=EVENEMENT_AGENT_ACTIVITE, run_id="run-1", agent="developpeur",
                  statut="echec", detail="pas une tâche"),  # mauvais type d'événement
        ],
    )

    echecs = echecs_du_run(execution, "developpeur")

    assert [(e.tache_id, e.titre, e.raison) for e in echecs] == [
        ("t1", "Écrire l'API", "Outil X en timeout."),
        ("t4", "Migrer le schéma", "Colonne absente."),
    ]


def test_echecs_du_run_vide_quand_aucun_echec_pour_l_agent():
    execution = EtatExecution(run_id="run-1", evenements=[_echec("t1", agent="qa")])
    assert echecs_du_run(execution, "developpeur") == ()


# ------------------------------------------------------------- moteur d'analyse


def test_proposer_revision_enregistre_un_brouillon_reference_les_echecs(tmp_path):
    """Happy path : proposition « proposition » stockée à part, justification tracée à sa source."""
    depot = PlaybookStore(tmp_path / "pb")
    fournisseur = FournisseurScript()
    analyseur = AnalyseurEchecs(provider=fournisseur, playbooks=depot)
    echecs = echecs_du_run(EtatExecution("run-1", [_echec()]), "developpeur")

    proposition = asyncio.run(analyseur.proposer_revision(_agent(), "run-1", echecs))

    # Un brouillon versionné à part, provenance « proposition », contenu = après le marqueur.
    assert proposition.provenance == "proposition"
    assert proposition.contenu == "Playbook révisé."
    assert depot.numeros_propositions("developpeur") == (1,)
    assert depot.numeros("developpeur") == ()  # jamais la version courante
    # La justification référence les échecs analysés (déterministe) puis le motif du modèle.
    assert "run-1" in proposition.justification
    assert "Outil X en timeout." in proposition.justification
    assert "j'ajoute une consigne de patience" in proposition.justification
    # L'appel modèle passe par la couche fournisseur, au modèle de l'agent, avec le cadre système.
    (appel,) = fournisseur.appels
    assert appel["model"] == "claude-sonnet-5"
    assert "expert en conception de playbooks" in appel["system_prompt"]
    # La base révisée = le playbook courant, ici le **document** du rôle (#294) : rien
    # n'ayant été publié, c'est le repli de `PLAYBOOK_DEFAUTS` — celui que la fiche
    # playbook affiche et que l'application de la proposition remplacera.
    assert PLAYBOOK_DEFAUTS["developpeur"].contenu in appel["prompt"]
    assert "Outil X en timeout." in appel["prompt"]


def test_proposer_revision_part_du_playbook_courant_edite(tmp_path):
    """La base révisée est la version courante éditée (#76), pas le prompt du code."""
    depot = PlaybookStore(tmp_path / "pb")
    depot.ecrire("developpeur", "Consignes éditées maison.")
    fournisseur = FournisseurScript()
    analyseur = AnalyseurEchecs(provider=fournisseur, playbooks=depot)
    echecs = echecs_du_run(EtatExecution("run-1", [_echec()]), "developpeur")

    asyncio.run(analyseur.proposer_revision(_agent(), "run-1", echecs))

    (appel,) = fournisseur.appels
    assert "Consignes éditées maison." in appel["prompt"]
    assert PLAYBOOK_DEFAUTS["developpeur"].contenu not in appel["prompt"]


def test_proposer_revision_revise_le_document_pas_sa_condensation(tmp_path):
    """Rien de publié : la base est le **document** du rôle, jamais le prompt du catalogue (#294).

    Les deux existent et diffèrent : `PLAYBOOK_DEFAUTS` sert le document Markdown structuré
    livré avec le paquet (#295), `Agent.prompt_systeme` la version condensée que compose
    l'exécution texte. C'est le document que l'éditeur de l'UI ouvre et que l'application
    de la proposition remplacera — réviser la condensation produirait un brouillon d'un
    autre format que celui qu'il remplace, et perdrait la structure au premier clic.
    """
    depot = PlaybookStore(tmp_path / "pb")
    fournisseur = FournisseurScript()
    analyseur = AnalyseurEchecs(provider=fournisseur, playbooks=depot)
    echecs = echecs_du_run(EtatExecution("run-1", [_echec()]), "developpeur")
    catalogue = next(a for a in DEFAULT_AGENTS if a.nom == "developpeur")

    asyncio.run(analyseur.proposer_revision(catalogue, "run-1", echecs))

    (appel,) = fournisseur.appels
    assert PLAYBOOK_DEFAUTS["developpeur"].contenu in appel["prompt"]
    # Le témoin : la structure du document, absente de la condensation texte.
    assert "## Garde-fous" in appel["prompt"]
    assert catalogue.prompt_systeme not in appel["prompt"]


def test_proposer_revision_d_un_agent_personnalise_part_de_son_prompt(tmp_path):
    """Un agent hors catalogue (#72) n'a pas de document livré : sa condensation EST son playbook.

    Le repli de `PLAYBOOK_DEFAUTS` ne le couvre pas — il faut donc que la base retombe sur
    `Agent.prompt_systeme`, sans quoi l'analyse d'un agent personnalisé lèverait.
    """
    depot = PlaybookStore(tmp_path / "pb")
    fournisseur = FournisseurScript()
    analyseur = AnalyseurEchecs(provider=fournisseur, playbooks=depot)
    echecs = echecs_du_run(EtatExecution("run-1", [_echec(agent="redacteur")]), "redacteur")

    asyncio.run(
        analyseur.proposer_revision(
            _agent(nom="redacteur", role="Rédacteur", prompt="Playbook maison."),
            "run-1",
            echecs,
        )
    )

    (appel,) = fournisseur.appels
    assert "Playbook maison." in appel["prompt"]


def test_proposer_revision_sans_echec_refuse(tmp_path):
    depot = PlaybookStore(tmp_path / "pb")
    analyseur = AnalyseurEchecs(provider=FournisseurScript(), playbooks=depot)
    with pytest.raises(ValueError):
        asyncio.run(analyseur.proposer_revision(_agent(), "run-1", []))


@pytest.mark.parametrize(
    "reponse",
    ["Une justification sans marqueur ni playbook.", f"Justif seule.\n{MARQUEUR_PLAYBOOK}\n   "],
)
def test_proposer_revision_reponse_inexploitable_leve_et_n_ecrit_rien(tmp_path, reponse):
    """Marqueur absent ou playbook vide : `RevisionIndisponible`, aucun brouillon écrit."""
    depot = PlaybookStore(tmp_path / "pb")
    analyseur = AnalyseurEchecs(provider=FournisseurScript(reponse), playbooks=depot)
    echecs = echecs_du_run(EtatExecution("run-1", [_echec()]), "developpeur")

    with pytest.raises(RevisionIndisponible):
        asyncio.run(analyseur.proposer_revision(_agent(), "run-1", echecs))
    assert depot.numeros_propositions("developpeur") == ()


def test_proposer_revision_fournisseur_en_panne_leve_et_n_ecrit_rien(tmp_path):
    depot = PlaybookStore(tmp_path / "pb")
    analyseur = AnalyseurEchecs(provider=FournisseurEnPanne(), playbooks=depot)
    echecs = echecs_du_run(EtatExecution("run-1", [_echec()]), "developpeur")

    with pytest.raises(RevisionIndisponible):
        asyncio.run(analyseur.proposer_revision(_agent(), "run-1", echecs))
    assert depot.numeros_propositions("developpeur") == ()


def test_sans_fournisseur_resout_celui_de_la_config(tmp_path, monkeypatch):
    """Sans fournisseur injecté, l'analyseur résout paresseusement celui de la config (#69)."""
    depot = PlaybookStore(tmp_path / "pb")
    fournisseur = FournisseurScript()
    monkeypatch.setattr(
        "maestro.providers.factory.provider_from_settings", lambda *a, **k: fournisseur
    )
    analyseur = AnalyseurEchecs(playbooks=depot)  # aucun provider explicite
    echecs = echecs_du_run(EtatExecution("run-1", [_echec()]), "developpeur")

    asyncio.run(analyseur.proposer_revision(_agent(), "run-1", echecs))

    assert fournisseur.appels  # le fournisseur de la config a bien été appelé
    assert depot.numeros_propositions("developpeur") == (1,)


# ------------------------------------------------------------- endpoint (à la demande)


def _client(depot, fournisseur, *, run_id="run-1", agent="developpeur", avec_echec=True):
    """TestClient avec un état peuplé d'un run (échec optionnel) et l'analyseur injecté."""
    state = ControlTowerState()
    if avec_echec:
        state.appliquer(_echec(agent=agent, run_id=run_id))
    else:
        state.appliquer(Event(type=EVENEMENT_TACHE_STATUT, run_id=run_id, tache_id="t1",
                              titre="Écrire l'API", agent=agent, statut="terminee"))
    app = create_app(
        bus=InMemoryEventBus(),
        state=state,
        playbooks=depot,
        analyseur=AnalyseurEchecs(provider=fournisseur, playbooks=depot),
    )
    return TestClient(app)


def test_endpoint_genere_une_proposition_a_la_demande(tmp_path):
    """POST → 200, proposition rendue et retrouvée dans la liste des propositions."""
    depot = PlaybookStore(tmp_path / "pb")
    with _client(depot, FournisseurScript()) as client:
        reponse = client.post("/api/playbooks/developpeur/propositions", json={"run_id": "run-1"})

        assert reponse.status_code == 200
        corps = reponse.json()
        assert corps["provenance"] == "proposition"
        assert corps["contenu"] == "Playbook révisé."
        assert "Outil X en timeout." in corps["justification"]
        # La proposition est listée à part et le playbook courant reste au défaut du code.
        listees = client.get("/api/playbooks/developpeur/propositions").json()
        assert len(listees) == 1 and listees[0]["provenance"] == "proposition"
        assert client.get("/api/playbooks/developpeur").json()["source"] == "defaut"


def test_endpoint_run_inconnu_404(tmp_path):
    depot = PlaybookStore(tmp_path / "pb")
    with _client(depot, FournisseurScript()) as client:
        reponse = client.post("/api/playbooks/developpeur/propositions", json={"run_id": "absent"})
    assert reponse.status_code == 404
    assert depot.numeros_propositions("developpeur") == ()


def test_endpoint_run_sans_echec_pour_l_agent_422(tmp_path):
    depot = PlaybookStore(tmp_path / "pb")
    with _client(depot, FournisseurScript(), avec_echec=False) as client:
        reponse = client.post("/api/playbooks/developpeur/propositions", json={"run_id": "run-1"})
    assert reponse.status_code == 422
    assert depot.numeros_propositions("developpeur") == ()


def test_endpoint_playbook_inconnu_404(tmp_path):
    depot = PlaybookStore(tmp_path / "pb")
    with _client(depot, FournisseurScript()) as client:
        reponse = client.post("/api/playbooks/stagiaire/propositions", json={"run_id": "run-1"})
    assert reponse.status_code == 404


def test_endpoint_fournisseur_en_panne_502(tmp_path):
    depot = PlaybookStore(tmp_path / "pb")
    with _client(depot, FournisseurEnPanne()) as client:
        reponse = client.post("/api/playbooks/developpeur/propositions", json={"run_id": "run-1"})
    assert reponse.status_code == 502
    assert depot.numeros_propositions("developpeur") == ()


# ------------------------------------------------------------- bout en bout (#137)

#: Le motif d'échec du run simulé — on le retrouve tel quel dans la justification de la
#: proposition, ce qui prouve que le brouillon est tracé jusqu'à sa source.
_RAISON_ECHEC = "Outil de test indisponible : la tâche n'a rien produit."

#: Le playbook « révisé » que le fournisseur factice fait mine d'avoir rédigé.
_PLAYBOOK_REVISE = "# Playbook révisé\n\nVérifier la disponibilité de l'outil avant de commencer."

#: Un playbook courant déjà publié par un humain, témoin de ce que le moteur doit
#: continuer à charger tant qu'une proposition n'est pas appliquée.
_PLAYBOOK_COURANT = "# Playbook maison\n\nConsignes écrites à la main."


class PlanFige(ModelProvider):
    """Planificateur factice : rend toujours le même plan d'une tâche « backend »."""

    name = "plan-fige"

    def __init__(self) -> None:
        self.plan = json.dumps(
            [
                {
                    "id": "tache-unique",
                    "titre": "Écrire l'API",
                    "description": "Réaliser la tâche.",
                    "competences_requises": ["backend"],
                    "format_sortie": "Texte",
                    "dependances": [],
                }
            ],
            ensure_ascii=False,
        )

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self.plan


class ExecutantScenario(ModelProvider):
    """Exécutant factice du run : échoue les `echecs` premières tâches, réussit ensuite.

    Enregistre le prompt système reçu à chaque tâche : c'est lui qui dit **quel playbook
    le moteur a réellement chargé** — le seul témoin fiable du chargement à chaud (#78)
    et du garde-fou « une proposition ne s'exécute pas ».
    """

    name = "executant-scenario"

    def __init__(self, echecs: int = 1) -> None:
        self.echecs_restants = echecs
        self.prompts_systeme: list[str | None] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.prompts_systeme.append(system_prompt)
        if self.echecs_restants > 0:
            self.echecs_restants -= 1
            raise RuntimeError(_RAISON_ECHEC)
        return "LIVRABLE"


def _moteur(executant, depot):
    """Boucle d'orchestration branchée sur `depot` (plan figé, tâche routée au développeur)."""
    return OrchestrationEngine(
        executant, Orchestrator(PlanFige(), model="claude-opus-4-8"), playbooks=depot
    )


def _prompt_du_code(nom="developpeur"):
    """Le prompt système « du code » de l'agent — le repli tant que rien n'est publié (#76)."""
    return next(a for a in DEFAULT_AGENTS if a.nom == nom).prompt_systeme


def _etat_depuis(journal):
    """L'état Control Tower alimenté par le journal du run, via le vrai pont (#46).

    Rejoue le chemin de production `journal → evenements_depuis_step → ControlTowerState`
    plutôt que de fabriquer les événements à la main : l'analyse lit ce que la
    supervision aurait réellement reçu.
    """
    state = ControlTowerState()
    for record in journal.records:
        for evenement in evenements_depuis_step(record.to_dict()):
            state.appliquer(evenement)
    return state


def test_boucle_complete_echec_proposition_application_chargement_a_chaud(tmp_path):
    """La boucle entière sur fournisseurs factices, sur un moteur construit **une seule fois**.

    Run en échec → analyse à la demande → proposition (provenance « proposition »,
    justification liée à l'échec) → application humaine → le playbook adopté devient la
    version courante et s'applique à chaud à l'exécution suivante.
    """
    depot = PlaybookStore(tmp_path / "pb")
    executant = ExecutantScenario(echecs=1)
    moteur = _moteur(executant, depot)  # construit ici, jamais rebâti de tout le scénario

    # ① Un run qui échoue, projeté sur l'état de la Control Tower comme en production.
    journal = RunJournal(run_id="run-echec")
    rapport = asyncio.run(moteur.run("Livrer l'API", journal=journal))
    (resultat,) = rapport.resultats
    assert (resultat.statut, resultat.agent) == (STATUT_ECHEC, "developpeur")
    assert _RAISON_ECHEC in resultat.erreur
    assert executant.prompts_systeme[-1] == _prompt_du_code()  # dépôt vierge : prompt du code

    analyste = FournisseurScript(
        f"Le run a buté sur un outil indisponible.\n{MARQUEUR_PLAYBOOK}\n{_PLAYBOOK_REVISE}"
    )
    app = create_app(
        bus=InMemoryEventBus(),
        state=_etat_depuis(journal),
        playbooks=depot,
        analyseur=AnalyseurEchecs(provider=analyste, playbooks=depot),
    )
    with TestClient(app) as client:
        # ② Analyse déclenchée à la demande → une proposition en brouillon, tracée à sa source.
        reponse = client.post(
            "/api/playbooks/developpeur/propositions", json={"run_id": "run-echec"}
        )
        assert reponse.status_code == 200
        proposition = reponse.json()
        assert proposition["provenance"] == "proposition"
        assert proposition["contenu"] == _PLAYBOOK_REVISE
        assert "run-echec" in proposition["justification"]
        assert _RAISON_ECHEC in proposition["justification"]

        # ③ Tant que personne n'a tranché : rien n'a bougé côté playbook courant…
        assert client.get("/api/playbooks/developpeur").json()["source"] == "defaut"
        rapport_avant = asyncio.run(moteur.run("Livrer l'API"))
        assert executant.prompts_systeme[-1] == _prompt_du_code()  # …ni côté moteur
        assert rapport_avant.resultats[0].playbook_version is None

        # ④ L'action humaine : appliquer. La proposition devient la version courante.
        applique = client.post(
            f"/api/playbooks/developpeur/propositions/{proposition['version']}/appliquer"
        )
        assert applique.status_code == 200
        assert (applique.json()["version"], applique.json()["provenance"]) == (1, "humain")
        # Le brouillon a quitté la file d'attente ; le playbook courant est le contenu adopté.
        assert client.get("/api/playbooks/developpeur/propositions").json() == []
        courant = client.get("/api/playbooks/developpeur").json()
        assert (courant["source"], courant["version"]) == ("stockage", 1)
        assert courant["contenu"] == _PLAYBOOK_REVISE

    # ⑤ Application à chaud (#78) : le même moteur exécute désormais le playbook adopté.
    rapport_apres = asyncio.run(moteur.run("Livrer l'API"))
    assert executant.prompts_systeme[-1] == _PLAYBOOK_REVISE
    assert rapport_apres.resultats[0].statut == STATUT_TERMINEE
    assert rapport_apres.resultats[0].playbook_version == 1


# ------------------------------------------------------------- garde-fou (#137)


def test_le_moteur_ne_charge_jamais_une_proposition_non_appliquee(tmp_path):
    """Une proposition en attente n'entre ni dans l'historique, ni dans ce que le moteur charge."""
    depot = PlaybookStore(tmp_path / "pb")
    depot.ecrire("developpeur", _PLAYBOOK_COURANT)
    depot.proposer("developpeur", _PLAYBOOK_REVISE, "justification liée aux échecs")
    executant = ExecutantScenario(echecs=0)

    rapport = asyncio.run(_moteur(executant, depot).run("Livrer l'API"))

    # Le moteur a exécuté la version courante, pas le brouillon.
    assert executant.prompts_systeme[-1] == _PLAYBOOK_COURANT
    assert rapport.resultats[0].playbook_version == 1
    # Et le brouillon reste hors de la numérotation des versions comme de la lecture courante.
    assert depot.numeros("developpeur") == (1,)
    assert depot.numeros_propositions("developpeur") == (1,)
    assert depot.prompt_systeme("developpeur", "prompt du code") == _PLAYBOOK_COURANT
    assert [v.provenance for v in depot.versions("developpeur")] == ["humain"]


def test_une_proposition_seule_ne_remplace_pas_le_prompt_du_code(tmp_path):
    """Sans version publiée, une proposition en attente laisse le moteur sur le prompt du code."""
    depot = PlaybookStore(tmp_path / "pb")
    depot.proposer("developpeur", _PLAYBOOK_REVISE, "justification liée aux échecs")
    executant = ExecutantScenario(echecs=0)

    rapport = asyncio.run(_moteur(executant, depot).run("Livrer l'API"))

    assert executant.prompts_systeme[-1] == _prompt_du_code()
    assert rapport.resultats[0].playbook_version is None  # rien de stocké : rien à tracer
    assert depot.numeros("developpeur") == ()


def test_le_rejet_retire_la_proposition_sans_toucher_la_version_courante(tmp_path):
    """Rejeter écarte le brouillon : l'historique ne bouge pas, le moteur non plus."""
    depot = PlaybookStore(tmp_path / "pb")
    depot.ecrire("developpeur", _PLAYBOOK_COURANT)
    depot.proposer("developpeur", _PLAYBOOK_REVISE, "justification liée aux échecs")

    with _client(depot, FournisseurScript()) as client:
        rejet = client.post("/api/playbooks/developpeur/propositions/1/rejeter")

        assert rejet.status_code == 200
        # Le rejet rend ce qu'il écarte — l'appelant garde une trace de la proposition.
        assert rejet.json()["contenu"] == _PLAYBOOK_REVISE
        assert client.get("/api/playbooks/developpeur/propositions").json() == []
        assert client.get("/api/playbooks/developpeur/propositions/1").status_code == 404
        # La version courante est intacte : ni nouvelle version, ni contenu modifié.
        courant = client.get("/api/playbooks/developpeur").json()
        assert (courant["version"], courant["contenu"]) == (1, _PLAYBOOK_COURANT)
        assert client.get("/api/playbooks/developpeur/versions").json() == [
            {"agent": "developpeur", "version": 1, "cree_le": courant["cree_le"],
             "provenance": "humain"}
        ]

    executant = ExecutantScenario(echecs=0)
    asyncio.run(_moteur(executant, depot).run("Livrer l'API"))
    assert executant.prompts_systeme[-1] == _PLAYBOOK_COURANT


def test_rejeter_puis_appliquer_la_meme_proposition_est_sans_effet(tmp_path):
    """Une proposition rejetée n'est plus applicable : 404, et toujours aucune version publiée."""
    depot = PlaybookStore(tmp_path / "pb")
    depot.proposer("developpeur", _PLAYBOOK_REVISE, "justification liée aux échecs")

    with _client(depot, FournisseurScript()) as client:
        assert client.post("/api/playbooks/developpeur/propositions/1/rejeter").status_code == 200
        rejouee = client.post("/api/playbooks/developpeur/propositions/1/appliquer")

    assert rejouee.status_code == 404
    assert depot.numeros("developpeur") == ()
