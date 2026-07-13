"""Tests du catalogue dynamique d'agents personnalisés (ticket #71).

Aucun appel réseau : dépôts sur répertoires temporaires, fournisseurs factices.
Lot final « tests + doc » du parent #70 — couvre les tests différés du lot #72 :

① **dépôt persisté** (`AgentStore`) : écrire/lire/lister/supprimer une définition
   (un fichier `<nom>.json` par agent), la date de création survit au
   remplacement, le nom du fichier fait foi sur le contenu, validation du nom
   (slug sûr — pas de traversée de chemin — et noms réservés du code), refus des
   définitions inexécutables (rôle/playbook vides, aucune compétence), racine
   configurable (`MAESTRO_AGENTS_DIR`) ;
② **catalogue effectif** (`catalogue()`) : un dépôt vide reproduit exactement le
   catalogue par défaut, les personnalisés suivent les agents du code (ordre du
   routage), la bascule de modèle (#69, `MAESTRO_MODEL`) s'applique aussi aux
   définitions personnalisées ;
③ **routage** : une tâche portant les compétences d'un agent personnalisé lui est
   assignée par les règles (`maestro.router.assign`) ; à score égal, les agents
   du code gardent la priorité (ordre du catalogue effectif) ;
④ **exécution** : un moteur construit sur le catalogue effectif route et exécute
   une tâche sur l'agent personnalisé — chemin texte, cadré par son playbook
   (prompt système) et son modèle.

L'API `/api/catalogue` (création, modification, suppression par HTTP et vue
`GET /api/agents`) est couverte dans `tests/test_controltower.py`.
"""

import asyncio
import json

import pytest

from maestro.agents.catalog import DEFAULT_AGENTS, MODELE_EXECUTANT_DEFAUT
from maestro.agents.store import (
    NOMS_RESERVES,
    AgentDefinition,
    AgentStore,
    catalogue,
)
from maestro.config import load_settings
from maestro.engine import OrchestrationEngine
from maestro.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task
from maestro.providers.base import ModelProvider
from maestro.router.router import assign


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
    """Exécutant texte-seul : enregistre modèle et prompt système de chaque appel."""

    name = "texte-enregistreur"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.calls.append({"prompt": prompt, "model": model, "system_prompt": system_prompt})
        return f"LIVRABLE #{len(self.calls)}"


@pytest.fixture()
def store(tmp_path):
    """Dépôt d'agents personnalisés vierge, sur répertoire temporaire."""
    return AgentStore(tmp_path / "agents")


def _definition(nom="redacteur", **overrides):
    """Définition valide prête à l'emploi (un rédacteur technique)."""
    champs = {
        "nom": nom,
        "role": "Rédacteur technique",
        "competences": ("redaction", "documentation"),
        "playbook": "Tu rédiges la documentation technique demandée.",
    }
    champs.update(overrides)
    return AgentDefinition(**champs)


def _task(**overrides) -> Task:
    """Tâche minimale routable par ses compétences requises."""
    champs = {
        "id": "t1",
        "titre": "Documenter l'API",
        "description": "Rédiger la page de documentation.",
        "competences_requises": frozenset({"redaction"}),
        "format_sortie": "Markdown",
        "dependances": (),
    }
    champs.update(overrides)
    return Task(**champs)


# --- ① Dépôt persisté : cycle de vie d'une définition ---------------------------------


def test_un_depot_vide_ne_liste_rien(store):
    assert store.noms() == ()
    assert store.lister() == ()
    assert store.lire("redacteur") is None


def test_ecrire_persiste_un_fichier_json_par_agent(store):
    ecrite = store.ecrire(_definition())

    chemin = store.racine / "redacteur.json"
    assert chemin.is_file()
    # Le fichier stocke la forme `to_dict` intégrale — rechargeable telle quelle.
    stocke = json.loads(chemin.read_text(encoding="utf-8"))
    assert AgentDefinition.from_dict(stocke) == ecrite
    assert store.noms() == ("redacteur",)


def test_ecrire_pose_les_dates_et_la_creation_survit_au_remplacement(store):
    v1 = store.ecrire(_definition())
    assert v1.cree_le and v1.modifie_le == v1.cree_le

    v2 = store.ecrire(_definition(role="Rédacteur senior"))

    # Remplacement intégral : le rôle change, la date de création reste.
    assert v2.role == "Rédacteur senior"
    assert v2.cree_le == v1.cree_le
    assert v2.modifie_le >= v1.modifie_le
    relue = store.lire("redacteur")
    assert relue is not None and relue.role == "Rédacteur senior"


def test_le_nom_du_fichier_fait_foi_sur_le_contenu(store):
    store.ecrire(_definition())
    # Un fichier recopié sous un autre nom (contenu inchangé) reste adressé —
    # et donc routé — par le nom du fichier, pas celui du contenu recopié.
    copie = store.racine / "plagiaire.json"
    copie.write_text(
        (store.racine / "redacteur.json").read_text(encoding="utf-8"), encoding="utf-8"
    )

    relue = store.lire("plagiaire")

    assert relue is not None and relue.nom == "plagiaire"
    assert {d.nom for d in store.lister()} == {"redacteur", "plagiaire"}


def test_supprimer_retire_la_definition(store):
    store.ecrire(_definition())

    assert store.supprimer("redacteur") is True
    assert store.lire("redacteur") is None
    assert store.supprimer("redacteur") is False  # déjà absent : rien à faire


def test_lister_rend_les_definitions_triees_par_nom(store):
    store.ecrire(_definition(nom="zeta", competences=("z",)))
    store.ecrire(_definition(nom="alpha", competences=("a",)))

    # Ordre déterministe : c'est lui qui départage les ex æquo du routage.
    assert [d.nom for d in store.lister()] == ["alpha", "zeta"]


@pytest.mark.parametrize("nom", ["../evasion", "Redacteur", "a.b", "", "un nom"])
def test_un_nom_hors_slug_est_refuse(store, nom):
    # Le nom vient de l'API : jamais un chemin arbitraire sur disque.
    with pytest.raises(ValueError, match="invalide"):
        store.ecrire(_definition(nom=nom))
    with pytest.raises(ValueError, match="invalide"):
        store.lire(nom)


@pytest.mark.parametrize("nom", sorted(NOMS_RESERVES))
def test_un_nom_reserve_est_refuse(store, nom):
    # Les agents du code et l'orchestrateur ne peuvent pas être masqués.
    with pytest.raises(ValueError, match="réservé"):
        store.ecrire(_definition(nom=nom))


def test_une_definition_inexecutable_est_refusee(store):
    with pytest.raises(ValueError, match="rôle vide"):
        store.ecrire(_definition(role="  \n"))
    with pytest.raises(ValueError, match="playbook vide"):
        store.ecrire(_definition(playbook="  "))
    with pytest.raises(ValueError, match="compétence"):
        store.ecrire(_definition(competences=("", "  ")))
    assert store.noms() == ()  # aucun refus n'a rien persisté


def test_les_competences_sont_epurees_a_l_ecriture(store):
    ecrite = store.ecrire(
        _definition(competences=(" redaction ", "redaction", "documentation"))
    )

    # Doublons et espaces retirés, ordre de déclaration conservé.
    assert ecrite.competences == ("redaction", "documentation")


def test_la_racine_du_depot_est_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv("MAESTRO_AGENTS_DIR", str(tmp_path / "ailleurs"))

    depot = AgentStore.default(load_settings())

    assert depot.racine == tmp_path / "ailleurs"


# --- ② Catalogue effectif : défauts du code + personnalisés ---------------------------


def test_un_depot_vide_reproduit_le_catalogue_par_defaut(store):
    assert catalogue(store) == DEFAULT_AGENTS


def test_les_personnalises_suivent_les_agents_du_code(store):
    store.ecrire(_definition())

    agents = catalogue(store)

    assert agents[: len(DEFAULT_AGENTS)] == DEFAULT_AGENTS
    dernier = agents[-1]
    assert dernier.nom == "redacteur"
    assert dernier.competences == frozenset({"redaction", "documentation"})
    # Le playbook de la définition devient le prompt système d'exécution.
    assert dernier.prompt_systeme == "Tu rédiges la documentation technique demandée."
    assert dernier.modele == MODELE_EXECUTANT_DEFAUT  # aucun modèle conseillé : le défaut


def test_le_modele_conseille_de_la_definition_est_utilise(store):
    store.ecrire(_definition(modele="claude-haiku-4-5-20251001"))

    assert catalogue(store)[-1].modele == "claude-haiku-4-5-20251001"


def test_la_bascule_de_modele_s_applique_aussi_aux_personnalises(store):
    # #69 : `MAESTRO_MODEL` impose un modèle unique à tout le catalogue effectif,
    # y compris aux définitions qui conseillent un autre modèle.
    store.ecrire(_definition(modele="claude-haiku-4-5-20251001"))

    agents = catalogue(store, "modele-unique")

    assert {a.modele for a in agents} == {"modele-unique"}


# --- ③ Routage : un agent personnalisé est assignable par les règles ------------------


def test_une_tache_est_routee_vers_l_agent_personnalise(store):
    store.ecrire(_definition())

    assignation = assign(_task(), catalogue(store))

    assert assignation.agent.nom == "redacteur"


def test_a_score_egal_les_agents_du_code_gardent_la_priorite(store):
    # Mêmes compétences que le QA du code : l'ordre du catalogue effectif départage.
    store.ecrire(_definition(nom="doublure", competences=("tests", "review")))

    assignation = assign(_task(competences_requises=frozenset({"tests"})), catalogue(store))

    assert assignation.agent.nom == "qa"


# --- ④ Exécution : le moteur exécute l'agent personnalisé, cadré par son playbook -----


def test_le_moteur_execute_l_agent_personnalise_par_le_chemin_texte(store):
    store.ecrire(_definition(modele="claude-haiku-4-5-20251001"))
    plan = json.dumps(
        [
            {
                "id": "t1",
                "titre": "Documenter l'API",
                "description": "Rédiger la page de documentation.",
                "competences_requises": ["redaction"],
                "format_sortie": "Markdown",
                "dependances": [],
            }
        ],
        ensure_ascii=False,
    )
    executant = TexteEnregistreur()
    moteur = OrchestrationEngine(
        executant,
        Orchestrator(ConstantProvider(plan), model="claude-opus-4-8"),
        agents=catalogue(store),
    )

    rapport = asyncio.run(moteur.run("Documenter l'API"))

    # La tâche est routée, exécutée et livrée par l'agent personnalisé…
    (tache,) = rapport.resultats
    assert tache.agent == "redacteur" and tache.ok
    assert tache.sortie == "LIVRABLE #1"
    # … sur son modèle conseillé, cadrée par son playbook en prompt système.
    (appel,) = executant.calls
    assert appel["model"] == "claude-haiku-4-5-20251001"
    assert appel["system_prompt"] == "Tu rédiges la documentation technique demandée."
