"""Tests du fournisseur compatible OpenAI et de la bascule par configuration (ticket #69).

Aucun appel réseau sortant : l'« endpoint compatible OpenAI » des tests est un
serveur HTTP local factice qui parle le dialecte chat completions. Couvre les
trois critères d'acceptation du ticket :
① le fournisseur est implémenté derrière `ModelProvider` et **enregistré au
  registre** (résolvable par `ModelSpec`, credentials via le slot commun) ;
② un agent **bascule par configuration seule** (`MAESTRO_PROVIDER`/`MAESTRO_MODEL`
  + variables `OPENAI_*`) — aucun changement de logique d'agent : les raccourcis
  `.default()` construisent tout depuis l'environnement ;
③ une **exécution aboutit de bout en bout** sur ce fournisseur : la boucle
  d'orchestration (plan → agents → livrables) tourne entièrement sur l'endpoint
  factice, chaque appel modèle portant le modèle configuré.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from maestro.config import ConfigError, Settings
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
from maestro.providers.factory import modele_du_canal
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
        if corps.get("stream"):
            self._servir_le_flux(corps)
            return
        statut, payload = self.server.reponse_pour(corps)
        brut = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(statut)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(brut)))
        self.end_headers()
        self.wfile.write(brut)

    def _servir_le_flux(self, corps):
        """Le dialecte `stream: true` : des trames `data:`, puis `[DONE]` (#1222).

        L'endpoint décide lui-même s'il refuse `stream_options` — c'est ce que le
        vrai fait, et c'est la seule façon d'éprouver le rejeu sans l'option.
        """
        statut, trames = self.server.flux_pour(corps)
        if statut >= 400:
            self.send_error(statut, "option inconnue")
            return
        self.send_response(statut)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for trame in trames:
            charge = trame if isinstance(trame, str) else json.dumps(trame, ensure_ascii=False)
            self.wfile.write(f"data: {charge}\n\n".encode())
            self.wfile.flush()

    def log_message(self, *args):  # silencieux : pas de bruit dans la sortie des tests
        pass


def _payload_texte(contenu, *, usage=None):
    return {
        "choices": [{"message": {"role": "assistant", "content": contenu}}],
        "usage": usage if usage is not None else {"prompt_tokens": 12, "completion_tokens": 34},
    }


def _trames_texte(*morceaux, usage=None):
    """Le flux du dialecte : un `delta.content` par morceau, l'usage, puis `[DONE]`."""
    trames = [{"choices": [{"delta": {"content": morceau}}]} for morceau in morceaux]
    if usage is not None:
        # La trame d'usage du dialecte : `choices` vide, `usage` rempli.
        trames.append({"choices": [], "usage": usage})
    return [*trames, "[DONE]"]


@pytest.fixture()
def endpoint():
    """Endpoint factice démarré sur un port libre ; `base_url` pointe dessus."""
    serveur = ThreadingHTTPServer(("127.0.0.1", 0), _ChatCompletionsHandler)
    serveur.requetes = []
    serveur.reponse_pour = lambda corps: (200, _payload_texte("PONG"))
    serveur.flux_pour = lambda corps: (
        200,
        _trames_texte("PO", "NG", usage={"prompt_tokens": 12, "completion_tokens": 34}),
    )
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


def test_une_image_part_en_partie_image_url_du_dialecte(endpoint):
    # #1163 : le texte puis chaque image en URL `data:` — la forme vision du dialecte,
    # que l'endpoint juge seul (aucune liste de modèles « qui voient » ici).
    import base64

    from maestro.providers.base import ImageJointe

    texte = _run(
        _provider(endpoint).generate_with_images(
            "Décris-la.",
            images=[ImageJointe(octets=b"\xff\xd8\xff-jpeg", type_media="image/jpeg")],
            model="qwen2.5vl",
            system_prompt="Tu regardes.",
        )
    )

    assert texte == "PONG"
    (requete,) = endpoint.requetes
    systeme, utilisateur = requete["corps"]["messages"]
    assert systeme == {"role": "system", "content": "Tu regardes."}
    assert utilisateur["content"] == [
        {"type": "text", "text": "Décris-la."},
        {
            "type": "image_url",
            "image_url": {
                "url": "data:image/jpeg;base64,"
                + base64.b64encode(b"\xff\xd8\xff-jpeg").decode("ascii")
            },
        },
    ]


def test_un_endpoint_qui_refuse_l_image_leve_avec_sa_raison(endpoint):
    # Un modèle qui ne voit pas : l'erreur de l'endpoint remonte, citée — c'est elle
    # que la lecture des sources nommera au rapport.
    from maestro.providers.base import ImageJointe

    endpoint.reponse_pour = lambda corps: (
        400,
        {"error": {"message": "model does not support image input"}},
    )

    with pytest.raises(OpenAICompatError, match="does not support image"):
        _run(
            _provider(endpoint).generate_with_images(
                "Décris-la.",
                images=[ImageJointe(octets=b"x", type_media="image/png")],
                model="llama3:8b",
            )
        )


def test_generate_refuse_un_endpoint_injoignable():
    # Port fermé : l'erreur réseau est enveloppée avec l'endpoint fautif.
    provider = OpenAICompatProvider(Credentials(), base_url="http://127.0.0.1:9/v1")

    with pytest.raises(OpenAICompatError, match="injoignable"):
        _run(provider.generate("Bonjour", model="m"))


# --- `generate_stream` : le dialecte en flux (#1222) -----------------------------------


def _morceaux(provider, **kwargs) -> list[str]:
    """Les incréments d'un `generate_stream`, drainés dans l'ordre."""

    async def drainer():
        return [morceau async for morceau in provider.generate_stream("Bonjour", **kwargs)]

    return _run(drainer())


def test_generate_stream_rend_les_morceaux_du_dialecte(endpoint):
    """Critère 2 : ce fournisseur streame, et rend ce que l'endpoint a découpé.

    L'invariant de la frontière tient par construction — on ne recoupe rien : la
    concaténation est ce que `generate` aurait rendu, morceau pour morceau.
    """
    assert _morceaux(_provider(endpoint), model="m") == ["PO", "NG"]

    requete = endpoint.requetes[-1]
    assert requete["corps"]["stream"] is True
    assert requete["corps"]["stream_options"] == {"include_usage": True}


def test_generate_stream_compte_les_tokens_comme_l_aller_simple(endpoint):
    """Streamer ne rend pas l'appel gratuit aux yeux du grand livre.

    C'est la moitié invisible du lot, la même que celle que `ClaudeProvider`
    tient depuis #693 : sans `stream_options`, l'endpoint n'enverrait aucune
    trame d'usage et la dépense d'un fil diffusé disparaîtrait de la télémétrie.
    """
    with collect_usage() as collecteur:
        assert _morceaux(_provider(endpoint), model="m") == ["PO", "NG"]

    total = collecteur.total
    assert total.appels == 1
    assert total.tokens_entree == 12
    assert total.tokens_sortie == 34
    assert total.duree_api_ms is not None


def test_generate_stream_rejoue_sans_l_option_quand_l_endpoint_la_refuse(endpoint):
    """Le repli, et le fait qu'il ne coûte **rien à l'appelant** (#1222).

    Un endpoint qui ne connaît pas `stream_options` répond 400 à l'ouverture,
    donc avant le premier incrément : redemander le même flux sans l'option ne
    peut pas faire voir deux fois la même phrase. Les tokens sont alors inconnus
    — c'est ce que la télémétrie rapporte, et non un chiffre inventé.
    """
    vues = []

    def flux(corps):
        vues.append("stream_options" in corps)
        if "stream_options" in corps:
            return (400, [])
        return (200, _trames_texte("PO", "NG"))

    endpoint.flux_pour = flux

    with collect_usage() as collecteur:
        assert _morceaux(_provider(endpoint), model="m") == ["PO", "NG"]

    assert vues == [True, False]
    assert collecteur.total.tokens_sortie == 0


def test_generate_stream_signale_une_erreur_http_clairement(endpoint):
    """Un refus qui n'est **pas** celui de l'option reste une erreur franche.

    La sonde prouve son motif sur l'échantillon d'à côté : le même chemin rend
    les morceaux quand l'endpoint répond 200, donc ce qui rougit ici est bien le
    statut et non une panne de lecture.
    """
    endpoint.flux_pour = lambda corps: (401, [])

    with pytest.raises(OpenAICompatError, match="401"):
        _morceaux(_provider(endpoint), model="m")


def test_generate_stream_ignore_ce_qu_il_ne_sait_pas_lire(endpoint):
    """Un flux se lit sur ce qu'on y reconnaît, jamais sur ce qu'on y refuse.

    Commentaires de maintien de connexion, trames illisibles, `delta` sans
    `content` (un rôle, un appel d'outil) : rien de cela n'est du texte à
    afficher, et rien de cela ne doit couper une réponse à demi reçue.
    """
    endpoint.flux_pour = lambda corps: (
        200,
        [
            ": ping",
            "{pas du json",
            {"choices": [{"delta": {"role": "assistant"}}]},
            {"choices": [{"delta": {"content": "PO"}}]},
            {"choices": [{"delta": {"content": "NG"}}]},
            "[DONE]",
        ],
    )

    assert _morceaux(_provider(endpoint), model="m") == ["PO", "NG"]


def test_generate_stream_sans_cle_n_envoie_aucune_autorisation(endpoint):
    """Le flux emprunte les mêmes en-têtes que l'aller simple — un seul endroit les écrit."""
    _morceaux(_provider(endpoint), model="m")

    assert endpoint.requetes[-1]["autorisation"] is None


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


# --- Critère ③ : une exécution aboutit de bout en bout sur ce fournisseur -------------

#: Plan que « répond » l'endpoint à l'appel de planification : un duo bdd +
#: developpeur (2 tâches chaînées, 2 agents distincts).
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


def test_une_execution_aboutit_de_bout_en_bout_sur_l_endpoint_openai(endpoint, monkeypatch):
    endpoint.reponse_pour = _reponse_planificateur_ou_agent
    # Toute la bascule tient dans l'environnement : fournisseur, modèle, endpoint.
    monkeypatch.setenv("MAESTRO_PROVIDER", "openai")
    monkeypatch.setenv("MAESTRO_MODEL", "mistral-small-latest")
    monkeypatch.setenv("OPENAI_BASE_URL", endpoint.base_url)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-essai")

    rapport = _run(OrchestrationEngine.default().run("Prototyper un mini-CRM"))

    assert [r.task_id for r in rapport.reussies] == ["schema-contacts", "api-contacts"]
    assert rapport.echouees == () and rapport.bloquees == ()

    # Tous les appels modèle (planification + 2 tâches) ont bien visé l'endpoint
    # configuré, avec le modèle configuré : la preuve de la bascule sans code.
    assert len(endpoint.requetes) == 3
    assert {r["corps"]["model"] for r in endpoint.requetes} == {"mistral-small-latest"}
    assert {r["autorisation"] for r in endpoint.requetes} == {"Bearer sk-essai"}

    # Les livrables portent la réponse de l'endpoint : le résultat vient bien de lui.
    (api,) = [r for r in rapport.resultats if r.task_id == "api-contacts"]
    assert "LIVRABLE (mistral-small-latest)" in api.sortie


# --- #1173 : chaque canal suit le fournisseur configuré --------------------------------


class _Enregistreur(OpenAICompatProvider):
    """Un fournisseur « openai » qui note le modèle qu'on lui demande, sans réseau.

    ⚠ Il surcharge **les deux** générations depuis #1222 : ce fournisseur streame
    désormais pour de vrai, si bien qu'un double qui n'aurait relayé que
    `generate` laisserait `generate_stream` partir sur le réseau — et le fil de
    l'orchestrateur, lui, passe par le flux.
    """

    def __init__(self, reponse: str = "", *, modele_configure: str | None = None) -> None:
        super().__init__(Credentials())
        self.modele_configure = modele_configure
        self.reponse = reponse
        self.modeles: list[str] = []

    async def generate(self, prompt, *, model, system_prompt=None, effort=None):
        self.modeles.append(model)
        return self.reponse

    async def generate_stream(self, prompt, *, model, system_prompt=None, effort=None):
        self.modeles.append(model)
        # Deux morceaux : de quoi éprouver qu'un appelant les recolle, là où un
        # morceau unique rendrait le flux indiscernable d'un aller simple.
        moitie = len(self.reponse) // 2
        for part in (self.reponse[:moitie], self.reponse[moitie:]):
            if part:
                yield part


def test_la_fabrique_pose_le_modele_impose_sur_le_fournisseur():
    """Le modèle voyage avec le fournisseur : un canal n'a pas à relire la configuration."""
    impose = provider_from_settings(_settings(provider="openai", model="qwen2.5"))
    assert impose.modele_configure == "qwen2.5"
    assert provider_from_settings(_settings(provider="openai")).modele_configure is None


def test_le_modele_du_canal_suit_la_regle_de_default_model():
    """MAESTRO_MODEL fait foi ; sinon le défaut du canal chez Claude ; ailleurs, réglage absent."""
    defaut = "claude-sonnet-5"
    assert modele_du_canal(defaut, _Enregistreur(modele_configure="qwen2.5")) == "qwen2.5"
    claude = ClaudeProvider(Credentials())
    assert modele_du_canal(defaut, claude) == defaut
    claude.modele_configure = "claude-opus-5"
    assert modele_du_canal(defaut, claude) == "claude-opus-5"
    with pytest.raises(ConfigError, match="MAESTRO_MODEL"):
        modele_du_canal(defaut, _Enregistreur())


def test_un_double_inconnu_de_la_fabrique_garde_le_defaut_du_canal():
    """Rien ne permet de juger un fournisseur construit à la main : il garde le défaut."""

    class Double(OpenAICompatProvider):
        name = "double"

    assert modele_du_canal("claude-sonnet-5", Double(Credentials())) == "claude-sonnet-5"


def _fil_de_l_orchestrateur(contenu: str):
    from maestro.controltower.chat import UTILISATEUR, MessageChat
    from maestro.controltower.orchestration import NOM_ORCHESTRATION

    return [MessageChat(agent=NOM_ORCHESTRATION, auteur=UTILISATEUR, contenu=contenu)]


def test_le_fil_demande_au_fournisseur_configure_le_modele_configure(monkeypatch):
    """Le critère 1, sur le fil : plus jamais `claude-sonnet-5` envoyé à un Ollama."""
    from maestro.controltower.orchestration import AGENT_ORCHESTRATION, RepondeurOrchestration

    fournisseur = _Enregistreur(
        json.dumps({"verdict": "echange", "reponse": "Bonjour."}), modele_configure="qwen2.5"
    )
    monkeypatch.setattr(
        "maestro.providers.factory.provider_from_settings", lambda *a, **k: fournisseur
    )

    reponse = _run(
        RepondeurOrchestration().produire(AGENT_ORCHESTRATION, _fil_de_l_orchestrateur("Bonjour"))
    )

    assert fournisseur.modeles == ["qwen2.5"]
    assert "Bonjour." in reponse.contenu


def test_sans_modele_le_fil_dit_le_reglage_et_ce_que_le_poste_offre(monkeypatch):
    """Le critère 2 : un réglage absent se dit comme tel, avec ce que la sonde a trouvé.

    Jamais « renvoyez tel quel » : rien ne répondra tant que le modèle n'est pas
    posé. Et le fil ne renvoie pas vers une variable à deviner, il dit ce qui
    tourne ici et quel réglage le branche.
    """
    from maestro.controltower.orchestration import AGENT_ORCHESTRATION, RepondeurOrchestration
    from maestro.poste import GENRE_SERVEUR, Constat, RapportSonde

    fournisseur = _Enregistreur()
    monkeypatch.setattr(
        "maestro.providers.factory.provider_from_settings", lambda *a, **k: fournisseur
    )

    async def sonde() -> RapportSonde:
        return RapportSonde(
            constats=(
                Constat(
                    genre=GENRE_SERVEUR,
                    cle="serveur:ollama",
                    libelle="Ollama",
                    fournisseur="openai",
                    utilisable=True,
                    detail="répond sur le port 11434",
                    modeles=("qwen2.5", "llama3"),
                ),
            )
        )

    reponse = _run(
        RepondeurOrchestration(sonde=sonde).produire(
            AGENT_ORCHESTRATION, _fil_de_l_orchestrateur("Bonjour")
        )
    )

    assert fournisseur.modeles == []  # rien n'est parti vers le fournisseur
    assert "réglage absent" in reponse.contenu
    assert "MAESTRO_MODEL" in reponse.contenu
    assert "renvoyez-le tel quel" not in reponse.contenu
    assert (
        "Sur ce poste, je trouve : Ollama (MAESTRO_PROVIDER=openai, "
        "MAESTRO_MODEL parmi : qwen2.5, llama3)"
    ) in reponse.contenu


def test_le_chat_d_un_agent_suit_le_modele_impose_comme_un_run(monkeypatch):
    """Parler à un agent obéit à MAESTRO_MODEL, exactement comme le faire travailler."""
    from maestro.agents.catalog import GABARITS_DU_CODE
    from maestro.controltower.chat import UTILISATEUR, MessageChat, RepondeurModele

    fournisseur = _Enregistreur("Réponse.", modele_configure="qwen2.5")
    monkeypatch.setattr(
        "maestro.providers.factory.provider_from_settings", lambda *a, **k: fournisseur
    )
    agent = GABARITS_DU_CODE[0]
    fil = [MessageChat(agent=agent.nom, auteur=UTILISATEUR, contenu="Salut")]

    _run(RepondeurModele().repondre(agent, fil))

    assert fournisseur.modeles == ["qwen2.5"]


def test_la_generation_d_agent_demande_le_modele_configure(monkeypatch):
    """Décrire un agent en une phrase passait, lui aussi, `claude-sonnet-5` à tout fournisseur."""
    from maestro.controltower.generation_agent import (
        GenerateurDefinitionAgent,
        GenerationIndisponible,
    )

    fournisseur = _Enregistreur("hors contrat", modele_configure="qwen2.5")
    monkeypatch.setattr(
        "maestro.providers.factory.provider_from_settings", lambda *a, **k: fournisseur
    )

    with pytest.raises(GenerationIndisponible):
        _run(
            GenerateurDefinitionAgent(fournisseurs=lambda: ()).proposer(
                "un agent qui relit mes migrations SQL"
            )
        )

    assert fournisseur.modeles == ["qwen2.5"]


def test_le_classifieur_du_routage_suit_le_modele_impose():
    """Chaque routage ambigu envoyait `claude-haiku-4-5`, quel que soit le fournisseur."""
    from maestro.agents.catalog import GABARITS_DU_CODE
    from maestro.engine.executor import LocalExecutor
    from maestro.router.classifier import MODELE_CLASSIFIEUR

    impose = LocalExecutor(_Enregistreur(), agents=GABARITS_DU_CODE, modele="qwen2.5")
    assert impose._router._classifier._model == "qwen2.5"
    sans = LocalExecutor(_Enregistreur(), agents=GABARITS_DU_CODE)
    assert sans._router._classifier._model == MODELE_CLASSIFIEUR
