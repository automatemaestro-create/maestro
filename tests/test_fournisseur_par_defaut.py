"""Le fournisseur par défaut, choisi et éprouvé dans les Paramètres (#1609).

Ce qui est gardé ici, dans l'ordre où la donnée circule :

① **la priorité** (`maestro.fournisseur_par_defaut`) — le choix du poste ne
   remplit que ce que l'environnement laisse vide ; une variable renseignée garde
   la main, une variable vide ne compte pas ; la clé n'est tirée du coffre que si
   l'environnement n'en porte pas ;
② **le dépôt et le coffre** — un fichier abîmé vaut « rien de choisi », jamais une
   exception ; l'écriture se fait d'un geste ; la clé vit chiffrée dans le coffre
   *poste*, qui ne change pas la façon dont les agents résolvent leurs secrets ;
③ **les routes** — l'épreuve rend toujours son verdict (200), l'enregistrement
   n'écrit **rien** quand elle échoue (409), une épreuve réussie n'est pas payée
   deux fois, l'environnement qui impose se dit sans jamais dire une clé, et une
   clé ne ressort d'aucune réponse ;
④ **le verdict typé** — chaque fournisseur lit son motif dans des champs (erreur
   du SDK, statut HTTP, classe d'exception), jamais dans un texte ;
⑤ **sans redémarrage** — rien ne garde le fournisseur entre deux appels : un
   choix enregistré est pris au suivant.

Ni réseau sortant, ni modèle : l'éprouveur du service est un double, le SDK est
remplacé par un faux `query`, et l'endpoint OpenAI est un serveur HTTP local.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    CLINotFoundError,
    ProcessError,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
)
from fastapi.testclient import TestClient

from maestro.agents.chiffrement import Chiffreur
from maestro.agents.secrets import SecretStore
from maestro.config import Settings, load_settings
from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.fournisseur_defaut import (
    DUREE_EPREUVE_S,
    ServiceFournisseurParDefaut,
)
from maestro.fournisseur_par_defaut import (
    DepotFournisseurParDefaut,
    FournisseurParDefaut,
    environnement_du_poste,
    imposees,
)
from maestro.providers import claude as claude_mod
from maestro.providers.base import PROMPT_EPREUVE, AuthMode, Credentials, Epreuve
from maestro.providers.claude import ClaudeProvider
from maestro.providers.factory import eprouver_reglages, fournisseur_de_l_appel
from maestro.providers.openai_compat import OpenAICompatProvider
from maestro.telemetry.redact import redact_secrets

#: Les variables qu'un choix peut poser : vidées pour les tests qui lisent le poste.
VARIABLES_DU_CHOIX = (
    "MAESTRO_PROVIDER",
    "MAESTRO_MODEL",
    "CLAUDE_AUTH_MODE",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
)

#: Une clé factice qu'aucun motif de `redact` ne reconnaît : seule son inscription
#: au registre des secrets servis peut la masquer.
CLE_OPENAI = "cle-du-poste-0123456789-abcdef"
CLE_ANTHROPIC = "sk-ant-essai-0123456789abcdef"


def _run(coro):
    return asyncio.run(coro)


def _choix(**surcharges: Any) -> FournisseurParDefaut:
    valeurs: dict[str, Any] = {
        "fournisseur": "openai",
        "acces": "local",
        "modele": "qwen2.5",
        "adresse": "http://localhost:11434/v1",
        "variables": (
            ("MAESTRO_PROVIDER", "openai"),
            ("MAESTRO_MODEL", "qwen2.5"),
            ("OPENAI_BASE_URL", "http://localhost:11434/v1"),
        ),
    }
    valeurs.update(surcharges)
    return FournisseurParDefaut(**valeurs)


# --- ① La priorité : l'environnement garde la main -------------------------------


def test_sans_choix_l_environnement_passe_tel_quel(tmp_path: Path) -> None:
    depot = DepotFournisseurParDefaut(tmp_path)
    environ = {"MAESTRO_PROVIDER": "claude", "AUTRE": "x"}

    assert environnement_du_poste(environ, depot=depot) == environ


def test_le_choix_remplit_ce_que_l_environnement_laisse_vide(tmp_path: Path) -> None:
    """`MAESTRO_PROVIDER=` copié du modèle de `.env` ne compte pas comme renseigné."""
    depot = DepotFournisseurParDefaut(tmp_path)
    depot.ecrire(_choix())

    complet = environnement_du_poste({"MAESTRO_PROVIDER": "  "}, depot=depot)

    assert complet["MAESTRO_PROVIDER"] == "openai"
    assert complet["MAESTRO_MODEL"] == "qwen2.5"
    assert complet["OPENAI_BASE_URL"] == "http://localhost:11434/v1"


def test_une_variable_renseignee_l_emporte_sur_le_choix(tmp_path: Path) -> None:
    depot = DepotFournisseurParDefaut(tmp_path)
    depot.ecrire(_choix())
    environ = {"MAESTRO_PROVIDER": "claude", "MAESTRO_MODEL": ""}

    complet = environnement_du_poste(environ, depot=depot)

    assert complet["MAESTRO_PROVIDER"] == "claude"
    assert complet["MAESTRO_MODEL"] == "qwen2.5"
    assert imposees(("MAESTRO_PROVIDER", "MAESTRO_MODEL"), environ) == ("MAESTRO_PROVIDER",)


def test_la_cle_vient_du_coffre_seulement_quand_l_environnement_n_en_porte_pas(
    tmp_path: Path,
) -> None:
    depot = DepotFournisseurParDefaut(tmp_path)
    depot.ecrire(_choix(acces="cle", secret="OPENAI_API_KEY"))
    lues: list[str] = []

    def coffre(nom: str) -> str | None:
        lues.append(nom)
        return CLE_OPENAI

    assert environnement_du_poste({}, depot=depot, secret=coffre)["OPENAI_API_KEY"] == CLE_OPENAI
    assert lues == ["OPENAI_API_KEY"]

    impose = environnement_du_poste({"OPENAI_API_KEY": "cle-de-l-env"}, depot=depot, secret=coffre)
    assert impose["OPENAI_API_KEY"] == "cle-de-l-env"
    assert lues == ["OPENAI_API_KEY"]  # le coffre n'a pas été rouvert


# --- ② Le dépôt et le coffre ----------------------------------------------------------


@pytest.mark.parametrize(
    "contenu",
    ["", "pas du json", "[]", '{"fournisseur": "openai"}', '{"fournisseur": "", "acces": "x"}',
     '{"fournisseur": "openai", "acces": "local", "variables": {"A": 1}}'],
)
def test_un_fichier_abime_vaut_rien_de_choisi(tmp_path: Path, contenu: str) -> None:
    depot = DepotFournisseurParDefaut(tmp_path)
    depot.chemin.write_text(contenu, encoding="utf-8")

    assert depot.lire() is None


def test_le_choix_s_ecrit_d_un_geste_date_et_s_efface(tmp_path: Path) -> None:
    depot = DepotFournisseurParDefaut(tmp_path / "reglages")
    assert depot.lire() is None

    enregistre = depot.ecrire(_choix())

    assert enregistre.pose_le
    assert depot.lire() == enregistre
    assert [p.name for p in depot.chemin.parent.iterdir()] == ["fournisseur.json"]
    assert depot.effacer() is True
    assert depot.effacer() is False
    assert depot.lire() is None


def test_la_cle_du_poste_est_chiffree_et_ne_provisionne_pas_les_agents(tmp_path: Path) -> None:
    """Choisir un fournisseur ne doit pas basculer la résolution des secrets d'agent."""
    coffre = SecretStore(tmp_path / "secrets", chiffreur=Chiffreur(Chiffreur.generer_cle()))

    coffre.enregistrer_poste("OPENAI_API_KEY", CLE_OPENAI)

    assert coffre.lire_poste() == {"OPENAI_API_KEY": CLE_OPENAI}
    assert [etat.cle for etat in coffre.etat_poste()] == ["OPENAI_API_KEY"]
    assert CLE_OPENAI not in (tmp_path / "secrets" / "_poste.json").read_text(encoding="utf-8")
    assert coffre.provisionne is False
    assert coffre.supprimer_poste("OPENAI_API_KEY") is True
    assert coffre.lire_poste() == {}


# --- ③ Les routes -------------------------------------------------------------------------


class Eprouveur:
    """Le double de l'épreuve : note ce qu'on lui demande, rend les verdicts posés."""

    def __init__(self) -> None:
        self.verdicts: list[Epreuve] = []
        self.appels: list[tuple[Settings, str]] = []

    async def __call__(self, settings: Settings, modele: str) -> Epreuve:
        self.appels.append((settings, modele))
        return self.verdicts.pop(0) if self.verdicts else Epreuve("ok")


class Banc:
    """Le service branché sur des dépôts jetables, un environnement et une horloge à soi."""

    def __init__(self, racine: Path) -> None:
        self.environ: dict[str, str] = {}
        self.instant = 0.0
        self.eprouveur = Eprouveur()
        self.coffre = SecretStore(racine / "secrets", chiffreur=Chiffreur(Chiffreur.generer_cle()))
        self.depot = DepotFournisseurParDefaut(racine / "reglages")
        self.service = ServiceFournisseurParDefaut(
            coffre=self.coffre,
            depot=self.depot,
            eprouveur=self.eprouveur,
            environ=lambda: self.environ,
            horloge=lambda: self.instant,
        )


@pytest.fixture()
def banc(tmp_path: Path) -> Banc:
    return Banc(tmp_path)


@pytest.fixture()
def client(banc: Banc) -> Iterator[TestClient]:
    app = create_app(
        bus=InMemoryEventBus(), state=ControlTowerState(), fournisseur_defaut=banc.service
    )
    with TestClient(app) as client:
        yield client


ABONNEMENT = {"fournisseur": "claude", "acces": "abonnement"}
CLE_CLAUDE = {"fournisseur": "claude", "acces": "cle"}
LOCAL = {"fournisseur": "openai", "acces": "local", "modele": "qwen2.5"}


def test_sans_rien_choisi_l_etat_dit_ce_que_l_execution_utilise(client: TestClient) -> None:
    etat = client.get("/api/fournisseurs/defaut").json()

    assert etat["choix"] is None
    assert etat["effectif"]["fournisseur"] == "claude"
    assert etat["effectif"]["acces"] == "abonnement"
    assert etat["effectif"]["modele"] is None
    assert etat["effectif"]["modele_par_defaut"].startswith("claude-")
    assert etat["environnement"] == []
    assert etat["cles"] == []


def test_l_epreuve_rend_son_verdict_en_200_reussie_ou_non(client: TestClient, banc: Banc) -> None:
    banc.eprouveur.verdicts = [Epreuve("ok"), Epreuve("authentification", "authentication_failed")]

    reussie = client.post("/api/fournisseurs/epreuve", json=ABONNEMENT)
    echouee = client.post("/api/fournisseurs/epreuve", json=ABONNEMENT)

    assert reussie.status_code == 200
    assert reussie.json()["epreuve"] == {"ok": True, "motif": "ok", "precision": ""}
    assert reussie.json()["modele"].startswith("claude-")
    assert echouee.status_code == 200
    assert echouee.json()["epreuve"]["motif"] == "authentification"
    assert banc.depot.lire() is None  # éprouver n'enregistre rien


def test_l_epreuve_porte_les_reglages_du_choix_saisi_pas_ceux_du_poste(
    client: TestClient, banc: Banc
) -> None:
    """Une clé dans l'environnement ne doit pas faire éprouver la clé au lieu de l'abonnement."""
    banc.environ.update({"ANTHROPIC_API_KEY": "sk-ant-de-l-environnement", "MAESTRO_MODEL": "x"})

    client.post("/api/fournisseurs/epreuve", json={**ABONNEMENT, "modele": "claude-sonnet-5"})
    client.post("/api/fournisseurs/epreuve", json={**LOCAL, "adresse": " "})

    (claude, modele_claude), (local, modele_local) = banc.eprouveur.appels
    assert claude.provider == "claude"
    assert claude.claude_auth_mode == AuthMode.SUBSCRIPTION.value
    assert claude.anthropic_api_key is None
    assert modele_claude == "claude-sonnet-5"
    assert local.provider == "openai"
    assert local.openai_base_url == "http://localhost:11434/v1"
    assert modele_local == "qwen2.5"


@pytest.mark.parametrize(
    "corps",
    [
        {"fournisseur": "mistral", "acces": "cle"},
        {"fournisseur": "claude", "acces": "navigateur"},
        {"fournisseur": "openai", "acces": "local"},  # sans modèle : ce fournisseur n'en a pas
        CLE_CLAUDE,  # sans clé, ni au coffre ni dans l'environnement
    ],
)
def test_un_choix_qui_ne_se_tient_pas_est_refuse_en_422(
    client: TestClient, banc: Banc, corps: dict[str, Any]
) -> None:
    assert client.post("/api/fournisseurs/epreuve", json=corps).status_code == 422
    assert client.put("/api/fournisseurs/defaut", json=corps).status_code == 422
    assert banc.eprouveur.appels == []


def test_un_enregistrement_dont_l_epreuve_echoue_n_ecrit_rien(
    client: TestClient, banc: Banc, tmp_path: Path
) -> None:
    banc.eprouveur.verdicts = [Epreuve("authentification", "HTTP 401")]

    reponse = client.put("/api/fournisseurs/defaut", json={**CLE_CLAUDE, "cle": CLE_ANTHROPIC})

    assert reponse.status_code == 409
    assert reponse.json()["detail"]["epreuve"]["motif"] == "authentification"
    assert not banc.depot.chemin.exists()
    assert banc.coffre.lire_poste() == {}
    assert CLE_ANTHROPIC not in reponse.text


def test_un_choix_eprouve_s_enregistre_sans_payer_une_seconde_epreuve(
    client: TestClient, banc: Banc
) -> None:
    demande = {**CLE_CLAUDE, "modele": "claude-sonnet-5", "cle": CLE_ANTHROPIC}
    assert client.post("/api/fournisseurs/epreuve", json=demande).json()["epreuve"]["ok"]

    reponse = client.put("/api/fournisseurs/defaut", json=demande)

    assert reponse.status_code == 200
    assert len(banc.eprouveur.appels) == 1
    etat = reponse.json()
    assert etat["choix"]["fournisseur"] == "claude"
    assert etat["choix"]["acces"] == "cle"
    assert etat["choix"]["eprouve_le"]
    assert etat["effectif"] == {
        "fournisseur": "claude",
        "acces": "cle",
        "modele": "claude-sonnet-5",
        "modele_par_defaut": "claude-sonnet-5",
    }
    assert etat["cles"] == ["ANTHROPIC_API_KEY"]
    # La clé est au coffre, chiffrée — ni dans la réponse, ni dans le fichier du choix.
    assert banc.coffre.lire_poste() == {"ANTHROPIC_API_KEY": CLE_ANTHROPIC}
    assert CLE_ANTHROPIC not in reponse.text
    fichier = json.loads(banc.depot.chemin.read_text(encoding="utf-8"))
    assert CLE_ANTHROPIC not in json.dumps(fichier)
    assert fichier["secret"] == "ANTHROPIC_API_KEY"
    assert fichier["variables"] == {
        "MAESTRO_PROVIDER": "claude",
        "CLAUDE_AUTH_MODE": AuthMode.API_KEY.value,
        "MAESTRO_MODEL": "claude-sonnet-5",
    }


def test_une_epreuve_ancienne_ou_d_un_autre_choix_est_rejouee(
    client: TestClient, banc: Banc
) -> None:
    client.post("/api/fournisseurs/epreuve", json=LOCAL)

    banc.instant = DUREE_EPREUVE_S + 1
    client.put("/api/fournisseurs/defaut", json=LOCAL)
    client.put("/api/fournisseurs/defaut", json={**LOCAL, "modele": "llama3"})

    assert [modele for _, modele in banc.eprouveur.appels] == ["qwen2.5", "qwen2.5", "llama3"]


def test_la_cle_du_coffre_sert_l_epreuve_suivante_sans_etre_retapee(
    client: TestClient, banc: Banc
) -> None:
    banc.coffre.enregistrer_poste("ANTHROPIC_API_KEY", CLE_ANTHROPIC)

    assert client.post("/api/fournisseurs/epreuve", json=CLE_CLAUDE).status_code == 200

    (settings, _), = banc.eprouveur.appels
    assert settings.anthropic_api_key == CLE_ANTHROPIC


def test_l_environnement_qui_impose_se_dit_sans_jamais_dire_une_cle(
    client: TestClient, banc: Banc
) -> None:
    client.put("/api/fournisseurs/defaut", json=ABONNEMENT)
    banc.environ.update({"MAESTRO_PROVIDER": "openai", "ANTHROPIC_API_KEY": CLE_ANTHROPIC,
                         "MAESTRO_MODEL": ""})

    reponse = client.get("/api/fournisseurs/defaut")

    etat = reponse.json()
    assert etat["choix"]["fournisseur"] == "claude"  # enregistré…
    assert etat["effectif"]["fournisseur"] == "openai"  # …mais pas utilisé
    assert etat["environnement"] == [
        {"variable": "MAESTRO_PROVIDER", "valeur": "openai"},
        {"variable": "ANTHROPIC_API_KEY", "valeur": None},
    ]
    assert CLE_ANTHROPIC not in reponse.text


def test_le_choix_et_la_cle_se_retirent(client: TestClient, banc: Banc) -> None:
    client.put("/api/fournisseurs/defaut", json={**CLE_CLAUDE, "cle": CLE_ANTHROPIC})

    assert client.delete("/api/fournisseurs/defaut/cles/PATH").status_code == 422
    assert client.delete("/api/fournisseurs/defaut/cles/ANTHROPIC_API_KEY").json()["cles"] == []
    assert client.delete("/api/fournisseurs/defaut").json()["choix"] is None
    assert not banc.depot.chemin.exists()


def test_une_cle_tapee_est_masquee_dans_les_journaux_des_l_epreuve(client: TestClient) -> None:
    # Le registre des secrets servis vit le temps du process : une clé qu'un autre
    # test a fait passer par le coffre y serait déjà, et rien ne serait prouvé ici.
    cle = "cle-tapee-a-l-epreuve-seulement-0123456789"
    assert cle in redact_secrets(cle)

    client.post(
        "/api/fournisseurs/epreuve",
        json={"fournisseur": "openai", "acces": "cle", "modele": "gpt-x", "cle": cle},
    )

    assert cle not in redact_secrets(f"réponse brute : {cle}")


# --- ④ Le verdict typé de chaque fournisseur ----------------------------------------------


def _faux_query(
    monkeypatch: pytest.MonkeyPatch, *messages: object, leve: BaseException | None = None
):
    """Remplace `query` du SDK : rend `messages`, puis lève `leve`. Rend ce qu'on lui a passé."""
    recu: dict[str, Any] = {}

    async def query(*, prompt, options):
        recu.update(prompt=prompt, options=options)
        for message in messages:
            yield message
        if leve is not None:
            raise leve

    monkeypatch.setattr(claude_mod, "query", query)
    return recu


def _resultat(
    *, erreur: bool, statut: int | None = None, sous_type: str = "success"
) -> ResultMessage:
    return ResultMessage(
        subtype=sous_type,
        duration_ms=0,
        duration_api_ms=0,
        is_error=erreur,
        num_turns=1,
        session_id="s",
        api_error_status=statut,
    )


def _eprouver_claude() -> Epreuve:
    return _run(ClaudeProvider(Credentials()).eprouver("claude-sonnet-5"))


def test_claude_reussit_sur_un_tour_sans_outil(monkeypatch: pytest.MonkeyPatch) -> None:
    recu = _faux_query(monkeypatch, _resultat(erreur=False))

    assert _eprouver_claude() == Epreuve("ok")
    assert recu["prompt"] == PROMPT_EPREUVE
    assert recu["options"].tools == []
    assert recu["options"].max_turns == 1
    assert recu["options"].model == "claude-sonnet-5"


@pytest.mark.parametrize(
    ("messages", "leve", "attendu"),
    [
        (
            [AssistantMessage(content=[], model="m", error="authentication_failed"),
             _resultat(erreur=True, statut=401)],
            None,
            Epreuve("authentification", "authentication_failed"),
        ),
        ([_resultat(erreur=True, statut=429)], None, Epreuve("limite", "HTTP 429")),
        ([_resultat(erreur=True, statut=402)], None, Epreuve("facturation", "HTTP 402")),
        (
            [RateLimitEvent(rate_limit_info=RateLimitInfo(status="rejected"), uuid="u",
                            session_id="s")],
            None,
            Epreuve("limite", "rate_limit"),
        ),
        ([], CLINotFoundError(), Epreuve("outil-absent", "CLI claude introuvable")),
        ([], ProcessError("sortie", exit_code=1), Epreuve("inconnu", "code de sortie 1")),
        ([_resultat(erreur=True, sous_type="error_max_turns")], None,
         Epreuve("inconnu", "error_max_turns")),
    ],
)
def test_claude_lit_son_motif_dans_les_champs_du_sdk(
    monkeypatch: pytest.MonkeyPatch, messages: list[object], leve, attendu: Epreuve
) -> None:
    _faux_query(monkeypatch, *messages, leve=leve)

    assert _eprouver_claude() == attendu


def test_un_reglage_que_le_fournisseur_refuse_de_construire_est_un_verdict() -> None:
    """Un accès « clé » sans clé : le fournisseur ne se construit pas, l'épreuve le dit."""
    reglages = Settings(
        anthropic_api_key=None,
        anthropic_model="claude-sonnet-5",
        claude_auth_mode=AuthMode.API_KEY.value,
        claude_oauth_token=None,
        database_url=None,
        redis_url=None,
    )

    verdict = _run(eprouver_reglages(reglages, "claude-sonnet-5"))

    assert verdict.motif == "authentification"


class _Repondeur(BaseHTTPRequestHandler):
    statut = 200

    def do_POST(self) -> None:  # noqa: N802 — nom imposé par http.server
        # La requête est lue avant de répondre : sous Windows, fermer une socket qui
        # porte encore des octets non lus envoie un RST, que le client peut recevoir
        # avant la réponse — et l'épreuve dirait « injoignable » d'un 404.
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        corps = json.dumps({"error": {"message": "refusé", "type": "x"}}).encode()
        self.send_response(self.statut)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture()
def endpoint() -> Iterator[tuple[str, type[_Repondeur]]]:
    repondeur = type("Repondeur", (_Repondeur,), {})
    serveur = ThreadingHTTPServer(("127.0.0.1", 0), repondeur)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    try:
        yield f"http://127.0.0.1:{serveur.server_address[1]}/v1", repondeur
    finally:
        serveur.shutdown()
        serveur.server_close()


@pytest.mark.parametrize(
    ("statut", "attendu"),
    [(401, "authentification"), (404, "requete"), (402, "facturation")],
)
def test_openai_lit_son_motif_dans_le_statut_http(
    endpoint: tuple[str, type[_Repondeur]], statut: int, attendu: str
) -> None:
    adresse, repondeur = endpoint
    repondeur.statut = statut

    verdict = _run(OpenAICompatProvider(Credentials(), base_url=adresse).eprouver("qwen2.5"))

    assert verdict == Epreuve(attendu, f"HTTP {statut}")


def test_openai_dit_injoignable_une_adresse_ou_rien_ne_repond() -> None:
    with socket.socket() as libre:
        libre.bind(("127.0.0.1", 0))
        port = libre.getsockname()[1]
    adresse = f"http://127.0.0.1:{port}/v1"

    verdict = _run(OpenAICompatProvider(Credentials(), base_url=adresse).eprouver("qwen2.5"))

    assert verdict == Epreuve("injoignable", adresse)


# --- ⑤ Sans redémarrage : rien ne garde le fournisseur ------------------------------------


class _Enregistreur(OpenAICompatProvider):
    """Un fournisseur sans réseau, qui note les modèles qu'on lui demande."""

    def __init__(self, modele_configure: str | None = None) -> None:
        super().__init__(Credentials())
        self.modele_configure = modele_configure
        self.modeles: list[str] = []

    async def generate(self, prompt, *, model, system_prompt=None, effort=None):
        self.modeles.append(model)
        return "Réponse."


def test_un_fournisseur_injecte_n_est_jamais_resolu(monkeypatch: pytest.MonkeyPatch) -> None:
    def interdit(*_: object) -> None:
        raise AssertionError("un fournisseur injecté ne se résout pas")

    monkeypatch.setattr("maestro.providers.factory.provider_from_settings", interdit)
    injecte = _Enregistreur()

    assert fournisseur_de_l_appel(injecte, "claude-sonnet-5") == (injecte, "claude-sonnet-5")


def test_chaque_appel_resout_le_fournisseur_de_nouveau(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un choix changé entre deux messages vaut pour le suivant : le chat ne garde rien."""
    from maestro.agents.catalog import GABARITS_DU_CODE
    from maestro.controltower.chat import UTILISATEUR, MessageChat, RepondeurModele

    successifs = iter([_Enregistreur("qwen2.5"), _Enregistreur("llama3")])
    servis: list[_Enregistreur] = []

    def resoudre(*_: object) -> _Enregistreur:
        servis.append(next(successifs))
        return servis[-1]

    monkeypatch.setattr("maestro.providers.factory.provider_from_settings", resoudre)
    agent = GABARITS_DU_CODE[0]
    fil = [MessageChat(agent=agent.nom, auteur=UTILISATEUR, contenu="Salut")]
    repondeur = RepondeurModele()

    _run(repondeur.repondre(agent, fil))
    _run(repondeur.repondre(agent, fil))

    assert [f.modeles for f in servis] == [["qwen2.5"], ["llama3"]]


@pytest.mark.fournisseur_du_poste
def test_un_choix_enregistre_est_pris_a_la_resolution_suivante(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """De bout en bout : l'écran enregistre, la fabrique du poste le prend, sans redémarrage."""
    from maestro.providers.factory import provider_from_settings

    for nom in VARIABLES_DU_CHOIX:
        monkeypatch.delenv(nom, raising=False)
    banc = Banc(tmp_path)
    service = ServiceFournisseurParDefaut(
        coffre=banc.coffre,
        depot=DepotFournisseurParDefaut.default(),  # celui que `load_settings` relit
        eprouveur=banc.eprouveur,
    )
    assert isinstance(provider_from_settings(), ClaudeProvider)

    _run(service.enregistrer(service_demande(adresse="http://127.0.0.1:9/v1")))

    resolu = provider_from_settings()
    assert isinstance(resolu, OpenAICompatProvider)
    assert resolu.base_url == "http://127.0.0.1:9/v1"
    assert resolu.modele_configure == "qwen2.5"
    assert load_settings().model == "qwen2.5"

    service.effacer()
    assert isinstance(provider_from_settings(), ClaudeProvider)


def service_demande(**surcharges: Any):
    from maestro.controltower.fournisseur_defaut import DemandeFournisseur

    return DemandeFournisseur(**{**LOCAL, **surcharges})
