"""Tests du coffre de secrets par agent et du masquage en sortie (ticket #109, lot #107).

Aucun appel réseau : coffres sur répertoires temporaires, fournisseurs
factices. Fait partie du lot final « tests + doc » du renforcement sécurité
(#102) — couvre le critère « secrets absents des journaux, traces et
rapports » et le scoping par agent :

① **coffre** (`SecretStore`) : lecture d'un coffre valide, validation à la
   lecture (JSON illisible, forme inattendue, nom de variable malformé, valeur
   non-chaîne — on ne résout jamais depuis un coffre douteux), nom d'agent
   verrouillé, racine configurable (`MAESTRO_SECRETS_DIR`) ;
② **bascule opt-in** : coffre non provisionné → résolution dans
   l'environnement du process (comportement historique #104) ; **premier
   coffre écrit** → scoping strict pour tous les agents (le README versionné
   ne compte pas) ;
③ **scoping par agent** : un agent ne résout que ses propres secrets — le
   secret d'un autre coffre, ou une variable du seul environnement du process,
   rend son serveur MCP indisponible (`McpServerUnavailable`, échec propre) ;
④ **masquage en sortie** : toute valeur servie (coffre ou référence `${VAR}`
   résolue) est enregistrée au registre de rédaction et remplacée par
   `[secret masqué]` où qu'elle réapparaisse — journal (qui alimente l'export
   Langfuse #81, le pont Control Tower #98 et les rapports), y compris dans un
   compte-rendu d'agent qui citerait son token ;
⑤ **de bout en bout (moteur)** : coffre provisionné + déclaration MCP → le
   fournisseur reçoit la valeur du coffre de l'agent, et si son livrable la
   cite, elle n'atteint jamais le journal en clair.
"""

import asyncio
import json
import logging
from pathlib import Path

import pytest

from maestro.agents.mcp import McpStore, ServeurMcp, resolus
from maestro.agents.secrets import SecretStore
from maestro.engine import OrchestrationEngine
from maestro.orchestrator import Orchestrator
from maestro.providers.base import McpServerUnavailable, ModelProvider
from maestro.telemetry import (
    LOGGER_NAME,
    MARQUEUR_SECRET,
    RunJournal,
    StepUsage,
    redact_secrets,
)
from maestro.telemetry import redact as redact_mod

#: Une valeur de secret plausible (assez longue pour le registre de rédaction).
SECRET = "glpat-tok-qa-1234567890"


@pytest.fixture(autouse=True)
def registre_vierge(monkeypatch):
    """Isole le registre des secrets servis : il vit sinon pour tout le process."""
    monkeypatch.setattr(redact_mod, "_SECRETS_SERVIS", set())


@pytest.fixture()
def store(tmp_path):
    """Coffre vierge, sur répertoire temporaire."""
    return SecretStore(tmp_path / "secrets")


def _ecrire_coffre(racine: Path, agent: str, secrets: dict) -> None:
    """Écrit le coffre JSON de `agent` sous la racine `racine`."""
    racine.mkdir(parents=True, exist_ok=True)
    (racine / f"{agent}.json").write_text(
        json.dumps({"secrets": secrets}, ensure_ascii=False), encoding="utf-8"
    )


# --- ① Coffre : lecture et validation ---------------------------------------------------


def test_lire_un_coffre_valide(store):
    _ecrire_coffre(store.racine, "qa", {"GITLAB_TOKEN": SECRET})
    assert store.lire("qa") == {"GITLAB_TOKEN": SECRET}


def test_agent_sans_coffre_rend_vide(store):
    _ecrire_coffre(store.racine, "qa", {})
    assert store.lire("devops") == {}


def test_json_illisible_refuse_avec_la_cause(store):
    store.racine.mkdir(parents=True)
    (store.racine / "qa.json").write_text("{pas du json", encoding="utf-8")

    with pytest.raises(ValueError, match="illisible.*'qa'"):
        store.lire("qa")


def test_forme_inattendue_refusee(store):
    store.racine.mkdir(parents=True)
    (store.racine / "qa.json").write_text('{"pas": "la bonne forme"}', encoding="utf-8")

    with pytest.raises(ValueError, match="secrets"):
        store.lire("qa")


def test_nom_de_variable_malforme_refuse(store):
    # La forme est celle des références ${VARIABLE} qu'un coffre sert à résoudre.
    _ecrire_coffre(store.racine, "qa", {"pas une variable": "x"})

    with pytest.raises(ValueError, match="nom de.*variable"):
        store.lire("qa")


def test_valeur_non_chaine_refusee(store):
    _ecrire_coffre(store.racine, "qa", {"GITLAB_TOKEN": 42})

    with pytest.raises(ValueError, match="doit être une chaîne"):
        store.lire("qa")


def test_nom_d_agent_verrouille_pas_de_traversee_de_chemin(store):
    with pytest.raises(ValueError, match="nom d'agent invalide"):
        store.lire("../evasion")


def test_racine_configurable_via_secrets_dir(tmp_path):
    class _Settings:
        secrets_dir = str(tmp_path / "coffre")

    assert SecretStore.default(_Settings()).racine == tmp_path / "coffre"


# --- ② Bascule opt-in : le premier coffre écrit active le scoping -----------------------


def test_sans_coffre_l_environnement_du_process_fait_foi(store, monkeypatch):
    monkeypatch.setenv("GITLAB_TOKEN", SECRET)

    assert not store.provisionne
    assert store.environ("qa") is not None
    assert store.environ("qa").get("GITLAB_TOKEN") == SECRET


def test_un_readme_seul_ne_provisionne_pas(store):
    # La racine versionnée existe partout (README) : le seuil est le premier
    # coffre `<agent>.json` écrit, pas l'existence du dossier.
    store.racine.mkdir(parents=True)
    (store.racine / "README.md").write_text("# Coffre", encoding="utf-8")

    assert not store.provisionne


def test_le_premier_coffre_active_le_scoping_pour_tous(store, monkeypatch):
    # Dès qu'un coffre existe, un agent sans coffre n'a AUCUN secret — même si
    # la variable traîne dans l'environnement du process : un état à moitié
    # migré violerait silencieusement le contrat « un agent ne voit que les siens ».
    monkeypatch.setenv("GITLAB_TOKEN", SECRET)
    _ecrire_coffre(store.racine, "qa", {"GITLAB_TOKEN": SECRET})

    assert store.provisionne
    assert store.environ("qa") == {"GITLAB_TOKEN": SECRET}
    assert store.environ("devops") == {}


# --- ③ Scoping par agent : résolution MCP dans le coffre seul ---------------------------


def _serveur_gitlab() -> ServeurMcp:
    return ServeurMcp(
        nom="tickets", type="http", url="https://exemple.test",
        headers={"Authorization": "Bearer ${GITLAB_TOKEN}"},
    )


def test_le_detenteur_resout_son_secret(store):
    _ecrire_coffre(store.racine, "qa", {"GITLAB_TOKEN": SECRET})

    (resolu,) = resolus([_serveur_gitlab()], store.environ("qa"))

    assert resolu.headers == {"Authorization": f"Bearer {SECRET}"}


def test_le_non_detenteur_perd_le_serveur_meme_si_le_process_a_la_variable(store, monkeypatch):
    # Le token du QA n'est pas résolvable par le DevOps, et réciproquement :
    # serveur indisponible (échec propre), le scoping strict prime sur la commodité.
    monkeypatch.setenv("GITLAB_TOKEN", SECRET)
    _ecrire_coffre(store.racine, "qa", {"GITLAB_TOKEN": SECRET})

    with pytest.raises(McpServerUnavailable, match="tickets.*GITLAB_TOKEN"):
        resolus([_serveur_gitlab()], store.environ("devops"))


# --- ④ Masquage en sortie : registre des secrets servis ---------------------------------


def test_une_valeur_lue_au_coffre_est_masquee_partout(store):
    _ecrire_coffre(store.racine, "qa", {"GITLAB_TOKEN": "tok-secret-du-coffre"})

    store.lire("qa")

    # La valeur servie est au registre de rédaction : masquée où qu'elle
    # réapparaisse — même hors des motifs connus (`sk-…`, `glpat-…`).
    assert redact_secrets("voici tok-secret-du-coffre en clair") == (
        f"voici {MARQUEUR_SECRET} en clair"
    )


def test_une_reference_resolue_est_masquee_partout():
    resolus([_serveur_gitlab()], {"GITLAB_TOKEN": "tok-resolu-au-montage"})

    assert "tok-resolu-au-montage" not in redact_secrets("fuite : tok-resolu-au-montage")


def test_une_valeur_trop_courte_n_est_pas_enregistree(store):
    # Anti-faux-positifs : masquer « abc » saccagerait tous les textes.
    _ecrire_coffre(store.racine, "qa", {"PIN": "abc"})

    store.lire("qa")

    assert redact_secrets("le code abc reste lisible") == "le code abc reste lisible"


def test_le_journal_expurge_un_secret_servi(store, caplog):
    _ecrire_coffre(store.racine, "qa", {"GITLAB_TOKEN": "tok-journal-9876"})
    store.lire("qa")
    journal = RunJournal(run_id="run-secrets")

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        record = journal.consigne(
            etape="t1", nom="Tâche", agent="qa", role="QA / Testeur", statut="terminee",
            entree="utilise le token", sortie="j'ai utilisé tok-journal-9876",
            usage=StepUsage(),
        )

    # Le journal alimente l'export Langfuse (#81), le pont Control Tower (#98)
    # et les rapports : le masquage à la consignation suit partout.
    assert "tok-journal-9876" not in record.sortie
    assert "tok-journal-9876" not in caplog.text
    assert MARQUEUR_SECRET in record.sortie


# --- ⑤ De bout en bout : le moteur sert le coffre scopé et le journal reste propre ------


class ConstantProvider(ModelProvider):
    """Renvoie toujours la même réponse (sert de planificateur factice)."""

    name = "constant"

    def __init__(self, response: str) -> None:
        self._response = response

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._response


class BavardProvider(ModelProvider):
    """Exécutant outillé factice : cite dans son compte-rendu le secret MCP reçu.

    C'est le scénario redouté du critère : un agent qui recopie son token dans
    un livrable. Le montage lui a confié la valeur résolue — le masquage doit
    l'arrêter à la consignation.
    """

    name = "bavard"

    def __init__(self) -> None:
        self.run_calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        return "TEXTE"

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, plafond_tours=None, projet=None,
    ):
        self.run_calls.append({"mcp_serveurs": tuple(mcp_serveurs)})
        (jeton,) = [s.headers["Authorization"] for s in mcp_serveurs]
        return f"J'ai appelé l'API avec l'en-tête {jeton}."


def _plan_json():
    return json.dumps(
        [
            {
                "id": "tache-unique",
                "titre": "Tâche unique",
                "description": "Réaliser la tâche.",
                "competences_requises": ["backend"],
                "format_sortie": "Texte",
                "dependances": [],
            }
        ],
        ensure_ascii=False,
    )


def test_le_moteur_sert_le_coffre_scope_et_le_journal_reste_propre(tmp_path, caplog):
    # Câblage complet : déclaration MCP (${GITLAB_TOKEN}) + coffre provisionné
    # pour l'agent routé. La valeur vient du coffre, pas de l'environnement.
    mcp = McpStore(tmp_path / "mcp")
    mcp.racine.mkdir(parents=True)
    (mcp.racine / "developpeur.json").write_text(
        json.dumps(
            {
                "serveurs": [
                    {
                        "nom": "tickets",
                        "type": "http",
                        "url": "https://exemple.test",
                        "headers": {"Authorization": "Bearer ${GITLAB_TOKEN}"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    secrets = SecretStore(tmp_path / "secrets")
    _ecrire_coffre(secrets.racine, "developpeur", {"GITLAB_TOKEN": SECRET})
    provider = BavardProvider()
    orchestrator = Orchestrator(ConstantProvider(_plan_json()), model="claude-opus-4-8")
    moteur = OrchestrationEngine(provider, orchestrator, mcp=mcp, secrets=secrets)
    journal = RunJournal(run_id="run-coffre")

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        rapport = asyncio.run(moteur.run("Objectif", journal=journal))

    # Le fournisseur a bien reçu la valeur du coffre (résolution scopée)…
    (monte,) = provider.run_calls[-1]["mcp_serveurs"]
    assert monte.headers == {"Authorization": f"Bearer {SECRET}"}
    assert rapport.resultats[0].ok
    # …mais le compte-rendu qui la citait est expurgé à la consignation :
    # aucun secret dans le journal (donc ni traces exportées, ni rapports).
    assert SECRET not in caplog.text
    assert MARQUEUR_SECRET in caplog.text
    etape = [r for r in journal.records if r.etape == "tache-unique"]
    assert SECRET not in etape[0].sortie
