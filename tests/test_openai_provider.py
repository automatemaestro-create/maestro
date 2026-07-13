"""Tests du fournisseur compatible OpenAI et de la bascule par configuration (ticket #69).

Aucun appel réseau sortant : l'« endpoint compatible OpenAI » des tests est un
serveur HTTP local factice qui parle le dialecte chat completions. Couvre les
trois critères d'acceptation du ticket :
① le fournisseur est implémenté derrière `ModelProvider` et **enregistré au
  registre** (résolvable par `ModelSpec`, credentials via le slot commun) ;
② un agent **bascule par configuration seule** (`MAESTRO_PROVIDER`/`MAESTRO_MODEL`
  + variables `OPENAI_*`) — aucun changement de logique d'agent : les raccourcis
  `.default()` construisent tout depuis l'environnement ;
③ une **exécution de démonstration aboutit de bout en bout** sur ce fournisseur :
  la démo Phase 0 (plan → agents → artefacts → verdict) tourne entièrement sur
  l'endpoint factice, chaque appel modèle portant le modèle configuré.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from maestro.config import ConfigError, Settings
from maestro.demo import run_demo
from maestro.engine.loop import OrchestrationEngine
from maestro.orchestrator.prompt import ORCHESTRATOR_SYSTEM_PROMPT
from maestro.providers import (
    AuthMode,
    ClaudeProvider,
    Credentials,
    ModelSpec,
    OpenAICompatError,
    OpenAICompatProvider,
    UnknownProviderError,
    available_providers,
    default_model,
    provider_from_settings,
    resolve_provider,
)
from maestro.providers.openai_compat import DEFAULT_BASE_URL
from maestro.telemetry import collect_usage


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _settings(**surcharges) -> Settings:
    """Un `Settings` factice ; les champs hors bascule fournisseur restent inertes."""
    defauts = dict(
        anthropic_api_key=None,
        anthropic_model="claude-opus-4-8",
        claude_auth_mode=None,
        claude_oauth_token=None,
        database_url=None,
        redis_url=None,
    )
    defauts.update(surcharges)
    return Settings(**defauts)


# --- Endpoint compatible OpenAI factice (serveur HTTP local) ---------------------------


class _ChatCompletionsHandler(BaseHTTPRequestHandler):
    """Parle le dialecte chat completions : de quoi servir orchestrateur et agents."""

    def do_POST(self):
        longueur = int(self.headers.get("Content-Length", 0))
        corps = json.loads(self.rfile.read(longueur) or b"{}")
        self.server.requetes.append(
            {
                "chemin": self.path,
                "corps": corps,
                "autorisation": self.headers.get("Authorization"),
            }
        )
        if not self.path.endswith("/chat/completions"):
            self.send_error(404, "chemin inconnu")
            return
        statut, payload = self.server.reponse_pour(corps)
        brut = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(statut)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(brut)))
        self.end_headers()
        self.wfile.write(brut)

    def log_message(self, *args):  # silencieux : pas de bruit dans la sortie des tests
        pass


def _payload_texte(contenu, *, usage=None):
    return {
        "choices": [{"message": {"role": "assistant", "content": contenu}}],
        "usage": usage if usage is not None else {"prompt_tokens": 12, "completion_tokens": 34},
    }


@pytest.fixture()
def endpoint():
    """Endpoint factice démarré sur un port libre ; `base_url` pointe dessus."""
    serveur = ThreadingHTTPServer(("127.0.0.1", 0), _ChatCompletionsHandler)
    serveur.requetes = []
    serveur.reponse_pour = lambda corps: (200, _payload_texte("PONG"))
    thread = threading.Thread(target=serveur.serve_forever, daemon=True)
    thread.start()
    serveur.base_url = f"http://127.0.0.1:{serveur.server_address[1]}/v1"
    try:
        yield serveur
    finally:
        serveur.shutdown()
        serveur.server_close()


def _provider(endpoint, *, api_key=None) -> OpenAICompatProvider:
    creds = (
        Credentials(auth_mode=AuthMode.API_KEY, api_key=api_key) if api_key else Credentials()
    )
    return OpenAICompatProvider(creds, base_url=endpoint.base_url)


# --- Critère ① : derrière `ModelProvider`, enregistré au registre ----------------------


def test_openai_est_enregistre_au_registre():
    assert "openai" in available_providers()


def test_resolve_provider_construit_openai_depuis_le_registre():
    spec = ModelSpec(provider="openai", model="gpt-4o-mini")
    provider = resolve_provider(spec, Credentials())

    assert isinstance(provider, OpenAICompatProvider)
    # La fabrique du registre vise l'endpoint OpenAI officiel (la config permet le reste).
    assert provider.base_url == DEFAULT_BASE_URL


def test_supports_accepte_tout_nom_non_vide():
    provider = OpenAICompatProvider(Credentials())
    # Nommages hétéroclites du dialecte : l'endpoint fait foi, pas le préfixe.
    for modele in ("gpt-4o", "mistral-small-latest", "llama3:8b", "org/modele"):
        assert provider.supports(modele)
    assert not provider.supports("   ")


def test_from_settings_derive_credentials_et_endpoint():
    provider = OpenAICompatProvider.from_settings(
        _settings(openai_api_key="sk-oa", openai_base_url="http://localhost:11434/v1/")
    )

    assert provider.credentials.auth_mode is AuthMode.API_KEY
    assert provider.credentials.api_key == "sk-oa"
    assert provider.base_url == "http://localhost:11434/v1"  # slash final normalisé


def test_from_settings_sans_cle_reste_valide():
    # Endpoint local sans auth (Ollama, vLLM) : la clé est optionnelle.
    provider = OpenAICompatProvider.from_settings(_settings())
    assert provider.credentials.api_key is None


# --- `generate` : dialecte, auth, télémétrie, erreurs ----------------------------------


def test_generate_renvoie_le_contenu_et_forme_la_requete(endpoint):
    texte = _run(
        _provider(endpoint, api_key="sk-test").generate(
            "Bonjour", model="mistral-small-latest", system_prompt="Tu es concis."
        )
    )

    assert texte == "PONG"
    (requete,) = endpoint.requetes
    assert requete["chemin"].endswith("/chat/completions")
    assert requete["autorisation"] == "Bearer sk-test"
    assert requete["corps"]["model"] == "mistral-small-latest"
    assert requete["corps"]["messages"] == [
        {"role": "system", "content": "Tu es concis."},
        {"role": "user", "content": "Bonjour"},
    ]


def test_generate_sans_cle_n_envoie_aucune_autorisation(endpoint):
    _run(_provider(endpoint).generate("Bonjour", model="llama3:8b"))

    (requete,) = endpoint.requetes
    assert requete["autorisation"] is None
    # Sans system_prompt : un seul message utilisateur.
    assert [m["role"] for m in requete["corps"]["messages"]] == ["user"]


def test_generate_signale_l_usage_au_collecteur(endpoint):
    with collect_usage() as collecteur:
        _run(_provider(endpoint).generate("Bonjour", model="m"))

    total = collecteur.total
    assert total.appels == 1
    assert total.tokens_entree == 12
    assert total.tokens_sortie == 34
    assert total.cout_usd is None  # le dialecte ne rapporte pas de coût : inconnu, pas nul
    assert total.duree_api_ms is not None


def test_generate_signale_une_erreur_http_clairement(endpoint):
    endpoint.reponse_pour = lambda corps: (401, {"error": {"message": "invalid api key"}})

    with pytest.raises(OpenAICompatError, match="401"):
        _run(_provider(endpoint).generate("Bonjour", model="m"))


def test_generate_refuse_une_reponse_hors_dialecte(endpoint):
    endpoint.reponse_pour = lambda corps: (200, {"pas": "de choices"})

    with pytest.raises(OpenAICompatError, match="chat completions"):
        _run(_provider(endpoint).generate("Bonjour", model="m"))


def test_generate_refuse_un_endpoint_injoignable():
    # Port fermé : l'erreur réseau est enveloppée avec l'endpoint fautif.
    provider = OpenAICompatProvider(Credentials(), base_url="http://127.0.0.1:9/v1")

    with pytest.raises(OpenAICompatError, match="injoignable"):
        _run(provider.generate("Bonjour", model="m"))


# --- Critère ② : bascule par configuration seule ---------------------------------------


def test_provider_from_settings_construit_claude_par_defaut():
    assert isinstance(provider_from_settings(_settings()), ClaudeProvider)


def test_provider_from_settings_bascule_sur_openai():
    provider = provider_from_settings(
        _settings(provider="openai", openai_base_url="http://localhost:1234/v1")
    )

    assert isinstance(provider, OpenAICompatProvider)
    assert provider.base_url == "http://localhost:1234/v1"


def test_provider_from_settings_refuse_un_nom_inconnu():
    with pytest.raises(UnknownProviderError, match="grok"):
        provider_from_settings(_settings(provider="grok"))


def test_default_model_suit_la_config():
    # MAESTRO_MODEL fait foi, quel que soit le fournisseur.
    assert default_model(_settings(model="mistral-large-latest")) == "mistral-large-latest"
    # Sans MAESTRO_MODEL, Claude garde son défaut historique (ANTHROPIC_MODEL).
    assert default_model(_settings()) == "claude-opus-4-8"


def test_default_model_exige_maestro_model_hors_claude():
    with pytest.raises(ConfigError, match="MAESTRO_MODEL"):
        default_model(_settings(provider="openai"))


def test_engine_default_refuse_openai_sans_modele(monkeypatch):
    monkeypatch.setenv("MAESTRO_PROVIDER", "openai")
    monkeypatch.delenv("MAESTRO_MODEL", raising=False)

    with pytest.raises(ConfigError, match="MAESTRO_MODEL"):
        OrchestrationEngine.default()


# --- Critère ③ : la démo aboutit de bout en bout sur ce fournisseur --------------------

#: Plan que « répond » l'endpoint à l'appel de planification : le duo bdd +
#: developpeur de la démo Phase 0 (2 tâches chaînées, 2 agents distincts).
_PLAN = [
    {
        "id": "schema-contacts",
        "titre": "Schéma des contacts",
        "description": "Table contacts + migration.",
        "competences_requises": ["sql", "schema"],
        "format_sortie": "Fichier SQL",
        "dependances": [],
    },
    {
        "id": "api-contacts",
        "titre": "API des contacts",
        "description": "Endpoints créer/lister.",
        "competences_requises": ["backend", "api"],
        "format_sortie": "Module d'API",
        "dependances": ["schema-contacts"],
    },
]


def _reponse_planificateur_ou_agent(corps):
    """Le plan pour l'appel de l'orchestrateur, un livrable texte pour les agents."""
    messages = corps.get("messages", [])
    if messages and messages[0] == {"role": "system", "content": ORCHESTRATOR_SYSTEM_PROMPT}:
        return 200, _payload_texte(json.dumps(_PLAN, ensure_ascii=False))
    return 200, _payload_texte(f"LIVRABLE ({corps['model']})")


def test_demo_aboutit_de_bout_en_bout_sur_l_endpoint_openai(endpoint, monkeypatch, tmp_path):
    endpoint.reponse_pour = _reponse_planificateur_ou_agent
    # Toute la bascule tient dans l'environnement : fournisseur, modèle, endpoint.
    monkeypatch.setenv("MAESTRO_PROVIDER", "openai")
    monkeypatch.setenv("MAESTRO_MODEL", "mistral-small-latest")
    monkeypatch.setenv("OPENAI_BASE_URL", endpoint.base_url)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-demo")

    code = run_demo(
        OrchestrationEngine.default(), objectif="Prototyper un mini-CRM", dossier=tmp_path
    )

    assert code == 0
    racine = next(tmp_path.glob("run-*"))
    verdict = (racine / "verdict.md").read_text(encoding="utf-8")
    assert "Critère de sortie Phase 0 — VALIDÉ" in verdict

    # Tous les appels modèle (planification + 2 tâches) ont bien visé l'endpoint
    # configuré, avec le modèle configuré : la preuve de la bascule sans code.
    assert len(endpoint.requetes) == 3
    assert {r["corps"]["model"] for r in endpoint.requetes} == {"mistral-small-latest"}
    assert {r["autorisation"] for r in endpoint.requetes} == {"Bearer sk-demo"}

    # Les livrables portent la réponse de l'endpoint : le résultat vient bien de lui.
    livrable = (racine / "livrables" / "api-contacts" / "livrable.md").read_text(encoding="utf-8")
    assert "LIVRABLE (mistral-small-latest)" in livrable
