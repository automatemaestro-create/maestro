"""Tests des playbooks versionnés et de leur application à chaud (ticket #75).

Aucun appel réseau : dépôts sur répertoires temporaires, fournisseurs factices.
Lot final « tests + doc » du parent #74 — couvre les tests différés des lots :

① **stockage versionné** (#76, EF-24/EF-25) : publication append-only, lecture de
   la version courante ou d'une version passée, retour arrière par republication
   (historique linéaire, rien n'est réécrit), repli sur le playbook « du code »
   pour un agent jamais édité, validation du nom d'agent (pas de traversée de
   chemin), racine configurable (`MAESTRO_PLAYBOOKS_DIR`) ;
② **chargement figé au câblage** (#76) : `avec_playbooks` et
   `default_runtimes(playbooks=...)` prennent un instantané du dépôt — une
   édition postérieure ne les affecte pas ;
③ **application à chaud** (#78, EF-26) : l'exécuteur relit la version courante à
   chaque tâche — une édition (ou une restauration) publiée entre deux
   exécutions s'applique à la suivante sans reconstruire le moteur, sur les deux
   chemins (appel texte et runtime outillé) ; un agent jamais édité garde
   exactement son prompt du code ;
④ **traçabilité de la version utilisée** (#78) : la version du playbook exécuté
   est estampillée sur le `TaskResult`, consignée au journal et visible dans le
   rapport (synthèse et dict) — None quand l'agent a exécuté avec son prompt du
   code.

Le pont vers Langfuse (`playbook_version` dans les métadonnées d'observation)
est couvert dans `tests/test_langfuse.py`, l'API `/api/playbooks` dans
`tests/test_controltower.py`, la remontée par la file dans `tests/test_queue.py`.
"""

import asyncio
import json
from pathlib import Path

import pytest

from maestro.agents import default_runtimes
from maestro.agents.catalog import DEFAULT_AGENTS
from maestro.agents.playbooks import PLAYBOOK_DEFAUTS, PlaybookStore, avec_playbooks
from maestro.config import load_settings
from maestro.engine import OrchestrationEngine
from maestro.orchestrator import Orchestrator
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal


class ConstantProvider(ModelProvider):
    """Renvoie toujours la même réponse (sert de planificateur factice)."""

    name = "constant"

    def __init__(self, response: str) -> None:
        self._response = response

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._response


class TexteEnregistreur(ModelProvider):
    """Exécutant texte-seul : enregistre le prompt système de chaque appel."""

    name = "texte-enregistreur"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        return f"LIVRABLE #{len(self.calls)}"


class OutilleEnregistreur(ModelProvider):
    """Exécutant outillé : enregistre le prompt système reçu par `run_agent`."""

    name = "outille-enregistreur"

    def __init__(self) -> None:
        self.run_calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        return "TEXTE"

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None,
        on_arbitrage=None, credit_arbitrage=None,
        on_courrier=None,
        plafond_tours=None, projet=None,
    ):
        self.run_calls.append({"prompt": prompt, "system_prompt": system_prompt})
        (Path(workspace) / "livrable.txt").write_text("contenu", encoding="utf-8")
        return f"OUTILLE #{len(self.run_calls)}"


@pytest.fixture()
def store(tmp_path):
    """Dépôt versionné vierge, sur répertoire temporaire."""
    return PlaybookStore(tmp_path / "playbooks")


def _plan_json(competences=("backend",)):
    """Plan factice d'une tâche unique, routée par ses compétences requises."""
    return json.dumps(
        [
            {
                "id": "tache-unique",
                "titre": "Tâche unique",
                "description": "Réaliser la tâche.",
                "competences_requises": list(competences),
                "format_sortie": "Texte",
                "dependances": [],
            }
        ],
        ensure_ascii=False,
    )


def _moteur(provider, store, *, competences=("backend",)):
    """Boucle d'orchestration branchée sur le dépôt (planification factice)."""
    planner = ConstantProvider(_plan_json(competences))
    orchestrator = Orchestrator(planner, model="claude-opus-4-8")
    return OrchestrationEngine(provider, orchestrator, playbooks=store)


def _agent_du_code(nom):
    """L'agent du catalogue (son prompt « du code » sert de témoin)."""
    return next(a for a in DEFAULT_AGENTS if a.nom == nom)


# --- ① Stockage versionné (#76) : append-only, historique, retour arrière -------------


def test_un_depot_vide_retombe_sur_le_playbook_du_code(store):
    assert store.lire("developpeur") is None
    assert store.numeros("developpeur") == ()
    assert store.versions("developpeur") == ()
    assert store.prompt_systeme("developpeur", "prompt du code") == "prompt du code"


def test_ecrire_publie_des_versions_successives(store):
    v1 = store.ecrire("developpeur", "Consignes v1.")
    v2 = store.ecrire("developpeur", "Consignes v2.")

    assert (v1.version, v2.version) == (1, 2)
    assert store.numeros("developpeur") == (1, 2)
    # La courante est la plus haute ; chaque version garde son contenu propre.
    courant = store.lire("developpeur")
    assert courant is not None
    assert courant.version == 2 and courant.contenu == "Consignes v2."
    assert v1.cree_le  # horodatage de publication posé (ISO, cf. to_dict ci-dessous)
    # Une version écrite est de provenance « humain » (#111) ; pas de justification.
    assert v1.to_dict() == {
        "agent": "developpeur", "version": 1, "cree_le": v1.cree_le,
        "provenance": "humain", "contenu": "Consignes v1.",
    }
    assert "contenu" not in v1.to_dict(avec_contenu=False)


def test_lire_une_version_passee(store):
    store.ecrire("qa", "Consignes v1.")
    store.ecrire("qa", "Consignes v2.")

    passee = store.lire("qa", 1)

    assert passee is not None
    assert passee.version == 1 and passee.contenu == "Consignes v1."
    assert store.lire("qa", 99) is None
    # L'historique complet, de la première à la courante.
    assert [v.version for v in store.versions("qa")] == [1, 2]


def test_restaurer_republie_sans_reecrire_l_historique(store):
    store.ecrire("bdd", "Consignes v1.")
    store.ecrire("bdd", "Consignes v2.")

    restauree = store.restaurer("bdd", 1)

    # Le retour arrière (EF-25) crée une version de plus — rien n'est supprimé.
    assert restauree.version == 3 and restauree.contenu == "Consignes v1."
    assert store.numeros("bdd") == (1, 2, 3)
    v2 = store.lire("bdd", 2)
    assert v2 is not None and v2.contenu == "Consignes v2."


def test_restaurer_une_version_inconnue_est_refuse(store):
    store.ecrire("bdd", "Consignes v1.")
    with pytest.raises(ValueError, match="version inconnue"):
        store.restaurer("bdd", 7)


def test_un_contenu_vide_est_refuse(store):
    with pytest.raises(ValueError, match="vide"):
        store.ecrire("developpeur", "   \n")
    assert store.numeros("developpeur") == ()


# --- Propositions d'auto-amélioration (#111, lot 1/4) : provenance + brouillons ---
# Le reste du parcours (échec simulé → génération par l'analyse → application manuelle,
# lots #139/#140) est couvert par le lot tests final #137. Ici, l'invariant *critique* :
# une proposition ne devient jamais la version courante et n'est jamais chargée.


def test_proposer_ne_touche_pas_la_version_courante(store):
    store.ecrire("developpeur", "Consignes courantes.")

    proposition = store.proposer(
        "developpeur", "Consignes proposées.", justification="2 échecs : outil X en timeout."
    )

    # La proposition est un brouillon numéroté à part, provenance « proposition ».
    assert proposition.version == 1 and proposition.provenance == "proposition"
    assert proposition.justification == "2 échecs : outil X en timeout."
    # Elle ne devient PAS la version courante : lire()/numeros()/prompt_systeme inchangés,
    # donc le moteur (chargement à chaud #78) ne la charge jamais.
    courant = store.lire("developpeur")
    assert courant is not None and courant.contenu == "Consignes courantes."
    assert store.numeros("developpeur") == (1,)
    assert store.prompt_systeme("developpeur", "repli") == "Consignes courantes."


def test_les_propositions_se_listent_a_part_avec_justification(store):
    p1 = store.proposer("qa", "Brouillon 1.", justification="raison 1")
    p2 = store.proposer("qa", "Brouillon 2.")  # justification optionnelle

    assert store.numeros_propositions("qa") == (1, 2)
    assert (p1.version, p2.version) == (1, 2)
    listees = store.propositions("qa")
    assert [p.version for p in listees] == [1, 2]
    # Les métadonnées exposent provenance + justification, jamais le contenu ici ;
    # une justification absente n'apparaît pas dans le dict.
    meta1 = listees[0].to_dict(avec_contenu=False)
    assert meta1["provenance"] == "proposition" and meta1["justification"] == "raison 1"
    assert "contenu" not in meta1
    assert "justification" not in listees[1].to_dict(avec_contenu=False)
    # Aucune version courante n'a été créée par les propositions.
    assert store.numeros("qa") == () and store.lire("qa") is None


def test_une_proposition_de_contenu_vide_est_refusee(store):
    with pytest.raises(ValueError, match="vide"):
        store.proposer("developpeur", "   \n")
    assert store.numeros_propositions("developpeur") == ()
    assert store.lire_proposition("developpeur", 1) is None


@pytest.mark.parametrize("nom", ["../evasion", "Developpeur", "a/b", "point.", ""])
def test_un_nom_d_agent_invalide_est_refuse(store, nom):
    # Le nom vient de l'URL de l'API : jamais un chemin arbitraire sur disque.
    with pytest.raises(ValueError, match="invalide"):
        store.ecrire(nom, "contenu")


def test_les_fichiers_etrangers_sont_ignores(store, tmp_path):
    store.ecrire("qa", "Consignes v1.")
    dossier = tmp_path / "playbooks" / "qa"
    (dossier / "notes.txt").write_text("brouillon", encoding="utf-8")
    (dossier / "v2.md").write_text("numérotation invalide", encoding="utf-8")

    assert store.numeros("qa") == (1,)


def test_le_depot_par_defaut_suit_la_config(tmp_path, monkeypatch):
    monkeypatch.setenv("MAESTRO_PLAYBOOKS_DIR", str(tmp_path / "ailleurs"))
    assert PlaybookStore.default(load_settings()).racine == tmp_path / "ailleurs"

    monkeypatch.delenv("MAESTRO_PLAYBOOKS_DIR")
    racine = PlaybookStore.default(load_settings()).racine
    assert racine.name == "playbooks" and racine.parent.name == "core"


def test_les_playbooks_par_defaut_couvrent_les_roles_outilles():
    # C'est aussi la liste des agents éditables par l'API Control Tower.
    assert set(PLAYBOOK_DEFAUTS) == {"developpeur", "bdd", "devops", "designer", "qa"}
    for nom, defaut in PLAYBOOK_DEFAUTS.items():
        assert defaut.agent == nom
        assert defaut.contenu.strip()


# --- ② Chargement figé au câblage (#76) : un instantané, pas une liaison ---------------


def test_avec_playbooks_ne_remplace_que_les_prompts_stockes(store):
    store.ecrire("qa", "Playbook QA stocké.")

    agents = avec_playbooks(DEFAULT_AGENTS, store)

    par_nom = {a.nom: a for a in agents}
    assert par_nom["qa"].prompt_systeme == "Playbook QA stocké."
    # Les agents jamais édités sont rendus à l'identique — un dépôt vide aussi.
    assert par_nom["developpeur"] == _agent_du_code("developpeur")
    assert avec_playbooks(DEFAULT_AGENTS, PlaybookStore(store.racine / "vide")) == tuple(
        DEFAULT_AGENTS
    )


def test_default_runtimes_fige_le_playbook_au_cablage(store):
    provider = OutilleEnregistreur()
    store.ecrire("developpeur", "Consignes figées v1.")
    runtimes = default_runtimes(provider, playbooks=store)
    store.ecrire("developpeur", "Consignes v2, publiées après le câblage.")

    asyncio.run(runtimes["developpeur"].execute("Décrire la tâche."))

    # L'instantané du câblage fait foi : la v2 n'est pas relue par ce chemin
    # (l'application à chaud passe par l'exécuteur, cf. section ③).
    assert provider.run_calls[-1]["system_prompt"] == "Consignes figées v1."


# --- ③ Application à chaud (#78, EF-26) : l'édition vaut pour l'exécution suivante -----


def test_l_edition_s_applique_a_l_execution_suivante_sans_reconstruction(store):
    provider = TexteEnregistreur()
    moteur = _moteur(provider, store)  # construit une seule fois, jamais rebâti

    asyncio.run(moteur.run("Objectif"))
    store.ecrire("developpeur", "Consignes éditées v1.")
    asyncio.run(moteur.run("Objectif"))
    store.ecrire("developpeur", "Consignes éditées v2.")
    rapport = asyncio.run(moteur.run("Objectif"))

    # Avant édition : le prompt du code ; puis chaque édition vaut pour la suite.
    assert provider.calls[0]["system_prompt"] == _agent_du_code("developpeur").prompt_systeme
    assert provider.calls[1]["system_prompt"] == "Consignes éditées v1."
    assert provider.calls[2]["system_prompt"] == "Consignes éditées v2."
    assert rapport.resultats[0].playbook_version == 2


def test_la_restauration_s_applique_a_chaud(store):
    provider = TexteEnregistreur()
    moteur = _moteur(provider, store)
    store.ecrire("developpeur", "Consignes v1.")
    store.ecrire("developpeur", "Consignes v2.")

    store.restaurer("developpeur", 1)
    rapport = asyncio.run(moteur.run("Objectif"))

    # Le retour arrière est une republication : la v3 (contenu v1) exécute.
    assert provider.calls[-1]["system_prompt"] == "Consignes v1."
    assert rapport.resultats[0].playbook_version == 3


def test_le_playbook_s_applique_aussi_au_runtime_outille(store):
    provider = OutilleEnregistreur()
    store.ecrire("developpeur", "Consignes outillées v1.")

    rapport = asyncio.run(_moteur(provider, store).run("Objectif"))

    # Le contenu stocké surcharge ponctuellement le runtime outillé (#78) —
    # même version tracée que sur le chemin texte.
    assert provider.run_calls[-1]["system_prompt"] == "Consignes outillées v1."
    (resultat,) = rapport.resultats
    assert resultat.playbook_version == 1
    assert [f.chemin for f in resultat.fichiers] == ["livrable.txt"]


def test_un_agent_jamais_edite_garde_son_prompt_du_code(store):
    provider = TexteEnregistreur()
    store.ecrire("bdd", "Consignes BDD.")  # un autre agent est édité, pas le qa
    journal = RunJournal(run_id="run-pb")

    rapport = asyncio.run(
        _moteur(provider, store, competences=("tests",)).run("Objectif", journal=journal)
    )

    assert provider.calls[-1]["system_prompt"] == _agent_du_code("qa").prompt_systeme
    assert rapport.resultats[0].playbook_version is None  # prompt du code : rien à tracer
    assert journal.records[-1].playbook_version is None


# --- ④ Traçabilité (#78) : la version utilisée est visible de bout en bout -------------


def test_la_version_utilisee_est_tracee_du_resultat_au_journal(store):
    provider = TexteEnregistreur()
    store.ecrire("developpeur", "Consignes v1.")
    store.ecrire("developpeur", "Consignes v2.")
    journal = RunJournal(run_id="run-pb")

    rapport = asyncio.run(_moteur(provider, store).run("Objectif", journal=journal))

    # Sur le résultat, dans le rapport structuré, la synthèse et le journal.
    (resultat,) = rapport.resultats
    assert resultat.playbook_version == 2
    assert rapport.to_dict()["resultats"][0]["playbook_version"] == 2
    assert "Playbook : v2" in rapport.synthese()
    trace = journal.records[-1]
    assert trace.etape == "tache-unique"
    assert trace.playbook_version == 2
    assert trace.to_dict()["playbook_version"] == 2
