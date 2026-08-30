"""Tests du catalogue des fournisseurs, modèles et efforts (ticket #253).

Lot final « tests + doc » du parent #243 — couvre les tests différés du lot #253,
côté **registre** : ce que Maestro sait faire, indépendamment de ce que le poste a.
La jonction des deux colonnes (registre × sonde) et la route `GET /api/fournisseurs`
sont couvertes par `tests/test_sonde_poste.py` §③ ; ici on juge la source, qu'elle
lit sans la définir.

① **le registre rend le catalogue** (`catalogue_fournisseurs`) : trié par nom, une
   fabrique qui ne déclare aucune gamme rendue **vide plutôt qu'omise**, et le
   registre qui fait foi sur le nom quand une fabrique est inscrite sous un autre ;
② **la forme servie** (`to_dict`) : le repli `libelle → nom`, et les trois clés que
   le front lit sans les deviner ;
③ **l'effort admis** (`efforts_admis` / `effort_admis`) : le **filtre unique**, qui
   écarte sans erreur un effort inconnu comme un modèle hors gamme ;
④ **le transport** : le mot-clé `effort` ne part vers le fournisseur *que* lorsque
   ce filtre rend une valeur — un fournisseur qui n'annonce aucune gamme reçoit
   l'appel au bit près celui d'avant #253.

Aucun appel réseau ni credentials : le catalogue se lit sur les **classes**
enregistrées, sans construire un seul fournisseur.
"""

import asyncio
import json

import pytest

from maestro.agents.store import AgentDefinition, AgentStore, catalogue
from maestro.engine import OrchestrationEngine
from maestro.orchestrator import Orchestrator
from maestro.providers import (
    available_providers,
    catalogue_fournisseurs,
    register,
    unregister,
)
from maestro.providers.base import (
    FournisseurDisponible,
    ModeleDisponible,
    ModelProvider,
)
from maestro.providers.claude import EFFORTS, ClaudeProvider
from maestro.providers.openai_compat import OpenAICompatProvider


class ConstantProvider(ModelProvider):
    """Planificateur factice : rend toujours le même plan JSON."""

    name = "constant"

    def __init__(self, response: str) -> None:
        self._response = response

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._response


class ExecutantSansGamme(ModelProvider):
    """Exécutant texte-seul qui **n'annonce aucun modèle** — le cas nominal du #4.

    Sa signature `generate` est **fermée** à dessein : elle n'accepte pas
    `effort`. Un mot-clé qui partirait quand même ferait un `TypeError`, ce qui
    est le témoin le plus franc qu'on puisse poser — un test qui se contenterait
    de lire les kwargs enregistrés passerait aussi sur un fournisseur tolérant.
    """

    name = "sans-gamme"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.calls.append({"model": model, "system_prompt": system_prompt})
        return "LIVRABLE"


class ExecutantAvecGamme(ModelProvider):
    """Exécutant qui annonce une gamme, et enregistre **tous** ses mots-clés."""

    name = "avec-gamme"

    MODELES = (
        ModeleDisponible("modele-reglable", "Modèle réglable", ("leger", "soutenu")),
        ModeleDisponible("modele-fixe", "Modèle fixe"),
    )

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None, **extra):
        self.calls.append({"model": model, **extra})
        return "LIVRABLE"


@pytest.fixture()
def registre_restaure():
    """Rend le registre à son état d'origine, quoi qu'un test y ait inscrit.

    Le registre est un **état de module** partagé par toute la suite : un
    fournisseur laissé derrière soi ferait rougir, plus tard et ailleurs, un test
    qui compte ce qui est enregistré.
    """
    avant = dict.fromkeys(available_providers())
    yield
    for nom in available_providers():
        if nom not in avant:
            unregister(nom)


def _definition(nom="redacteur", **overrides):
    """Définition d'agent personnalisé valide, prête à router sur « redaction »."""
    champs = {
        "nom": nom,
        "role": "Rédacteur technique",
        "competences": ("redaction",),
        "playbook": "Tu rédiges la documentation demandée.",
    }
    champs.update(overrides)
    return AgentDefinition(**champs)


def _plan() -> str:
    """Plan d'une tâche, routée sur la compétence « redaction »."""
    return json.dumps(
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


def _execute(executant, store) -> None:
    """Fait tourner un moteur sur le catalogue de `store`, avec `executant`."""
    moteur = OrchestrationEngine(
        executant,
        Orchestrator(ConstantProvider(_plan()), model="claude-opus-4-8"),
        agents=catalogue(store),
    )
    rapport = asyncio.run(moteur.run("Documenter l'API"))
    (tache,) = rapport.resultats
    assert tache.ok, tache.erreur


def _fiche(catalogue_rendu, nom):
    """La fiche de `nom` dans un catalogue, ou None."""
    return next((f for f in catalogue_rendu if f.nom == nom), None)


# --- ① Le registre rend le catalogue ---------------------------------------------------


def test_le_catalogue_part_du_registre_et_le_rend_trie():
    fiches = catalogue_fournisseurs()

    noms = [fiche.nom for fiche in fiches]
    # L'ordre d'enregistrement dépend de l'ordre des imports : une liste servie à
    # une UI ne doit pas en dépendre.
    assert noms == sorted(noms)
    assert noms == available_providers()


def test_la_gamme_annoncee_par_la_classe_est_celle_du_catalogue():
    claude = _fiche(catalogue_fournisseurs(), "claude")

    assert claude is not None
    assert claude.modeles == ClaudeProvider.MODELES
    assert claude.modeles_libres is True
    # `MODELES_LIBRES` sans gamme n'est pas « aucun modèle » mais « saisis le nom ».
    openai = _fiche(catalogue_fournisseurs(), "openai")
    assert openai is not None
    assert openai.modeles == () and openai.modeles_libres is True


def test_une_fabrique_sans_gamme_est_rendue_vide_plutot_qu_omise(registre_restaure):
    # Une fermeture posée par un test, un fournisseur branché à la volée : il est
    # enregistré, donc résolvable, donc il existe pour qui appelle. Le taire
    # serait le seul vrai mensonge de cette vue.
    register("branche-a-la-volee", lambda credentials: ConstantProvider("rien"))

    fiche = _fiche(catalogue_fournisseurs(), "branche-a-la-volee")

    assert fiche is not None
    assert fiche.modeles == ()
    assert fiche.modeles_libres is False


def test_le_registre_fait_foi_sur_le_nom(registre_restaure):
    # Inscrite sous une autre clé que le `name` de sa classe : c'est la **clé**
    # qu'un client devra écrire dans `fournisseur`, donc c'est elle que la fiche
    # porte — sans quoi le front proposerait un nom que `resolve_provider`
    # ne saurait pas résoudre.
    register("claude-bis", ClaudeProvider)

    fiche = _fiche(catalogue_fournisseurs(), "claude-bis")

    assert fiche is not None
    assert fiche.nom == "claude-bis"
    # Le renommage ne coûte pas la gamme : elle reste celle de la classe.
    assert fiche.modeles == ClaudeProvider.MODELES


def test_un_fournisseur_retire_quitte_le_catalogue(registre_restaure):
    register("ephemere", lambda credentials: ConstantProvider("rien"))
    assert _fiche(catalogue_fournisseurs(), "ephemere") is not None

    unregister("ephemere")

    assert _fiche(catalogue_fournisseurs(), "ephemere") is None


# --- ② La forme servie : ce que le front lit -------------------------------------------


def test_un_modele_sans_libelle_se_nomme_par_son_identifiant():
    # Le repli évite une pastille vide à l'écran : un fournisseur qui n'a pas de
    # nom lisible à donner reste désignable par la chaîne exacte du modèle.
    assert ModeleDisponible("m-1").to_dict() == {
        "nom": "m-1",
        "libelle": "m-1",
        "efforts": [],
    }
    assert ModeleDisponible("m-1", "Modèle 1", ("bas",)).to_dict() == {
        "nom": "m-1",
        "libelle": "Modèle 1",
        "efforts": ["bas"],
    }


def test_la_fiche_servie_porte_les_trois_cles_meme_a_gamme_vide():
    # Les clés ne dépendent pas du contenu : un client n'a jamais à les deviner.
    assert FournisseurDisponible("vide").to_dict() == {
        "nom": "vide",
        "modeles": [],
        "modeles_libres": False,
    }


def test_la_fiche_de_claude_est_serialisable_telle_quelle():
    charge = ClaudeProvider.catalogue().to_dict()

    assert charge["nom"] == "claude"
    assert charge["modeles_libres"] is True
    premier = charge["modeles"][0]
    assert premier["nom"] == "claude-opus-5" and premier["libelle"] == "Opus 5"
    assert premier["efforts"] == list(EFFORTS)
    # JSON-sérialisable sans encodeur maison : c'est le contrat de la route.
    assert json.loads(json.dumps(charge)) == charge


# --- ③ L'effort admis : le filtre unique -----------------------------------------------


def test_les_efforts_se_lisent_sur_le_modele_et_non_sur_le_fournisseur():
    assert ClaudeProvider.efforts_admis("claude-opus-5") == EFFORTS
    # Hors gamme : on ne sait rien de ce qu'il admet, et supposer serait le seul
    # moyen d'envoyer un réglage qu'un endpoint refuserait.
    assert ClaudeProvider.efforts_admis("claude-modele-de-demain") == ()
    # Un fournisseur qui n'expose pas ce réglage n'en admet aucun, jamais.
    assert OpenAICompatProvider.efforts_admis("gpt-4o") == ()


def test_un_effort_admis_passe_et_lui_seul():
    assert ClaudeProvider.effort_admis("claude-opus-5", "xhigh") == "xhigh"
    # Valeur obsolète restée sur la définition d'un agent : écartée sans erreur —
    # le réglage est un conseil, jamais une condition d'exécution.
    assert ClaudeProvider.effort_admis("claude-opus-5", "delirant") is None
    # Modèle changé depuis : même chemin, même silence.
    assert ClaudeProvider.effort_admis("claude-modele-de-demain", "high") is None
    # Ni effort demandé, ni chaîne vide qui vaudrait « pas de réglage ».
    assert ClaudeProvider.effort_admis("claude-opus-5", None) is None
    assert ClaudeProvider.effort_admis("claude-opus-5", "") is None


def test_un_fournisseur_sans_gamme_n_admet_aucun_effort():
    assert OpenAICompatProvider.effort_admis("qwen2.5:3b", "high") is None
    assert ExecutantAvecGamme.effort_admis("modele-fixe", "leger") is None


# --- ④ Le transport : le mot-clé ne part que s'il a quelque chose à dire ----------------


def test_un_fournisseur_sans_gamme_ne_recoit_jamais_le_mot_cle(tmp_path):
    # L'agent porte pourtant un effort : c'est bien le **filtre** qui l'écarte, et
    # non l'absence de réglage. La signature fermée d'`ExecutantSansGamme` fait le
    # reste — un mot-clé qui partirait quand même lèverait un TypeError.
    store = AgentStore(tmp_path / "agents")
    store.ecrire(_definition(effort="xhigh"))
    executant = ExecutantSansGamme()

    _execute(executant, store)

    (appel,) = executant.calls
    assert "effort" not in appel


def test_l_effort_de_l_agent_atteint_un_fournisseur_qui_l_admet(tmp_path):
    store = AgentStore(tmp_path / "agents")
    store.ecrire(_definition(modele="modele-reglable", effort="soutenu"))
    executant = ExecutantAvecGamme()

    _execute(executant, store)

    (appel,) = executant.calls
    assert appel["model"] == "modele-reglable"
    assert appel["effort"] == "soutenu"


def test_un_effort_hors_gamme_est_tu_plutot_que_transmis(tmp_path):
    # Le modèle existe, l'effort non : l'appel part sans le mot-clé, comme si
    # l'agent n'en portait aucun. Rien n'échoue — c'est la promesse d'#253.
    store = AgentStore(tmp_path / "agents")
    store.ecrire(_definition(modele="modele-reglable", effort="titanesque"))
    executant = ExecutantAvecGamme()

    _execute(executant, store)

    (appel,) = executant.calls
    assert "effort" not in appel


def test_un_modele_sans_effort_admis_n_en_transmet_aucun(tmp_path):
    store = AgentStore(tmp_path / "agents")
    store.ecrire(_definition(modele="modele-fixe", effort="leger"))
    executant = ExecutantAvecGamme()

    _execute(executant, store)

    (appel,) = executant.calls
    assert appel["model"] == "modele-fixe"
    assert "effort" not in appel
