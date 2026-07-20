"""Tests des intégrations MCP : serveurs déclarés par agent, montés à l'exécution (ticket #103).

Aucun appel réseau : dépôts sur répertoires temporaires, fournisseurs factices,
SDK monkeypatché. Lot final « tests + doc » du parent #101 — couvre les tests
différés des lots (#104 socle, #105/#106 pilotes purement configuratifs) :

① **déclarations & dépôt** (#104) : lecture d'une déclaration stdio ou distante,
   validation à la lecture (JSON illisible, forme inattendue, doublons, formes
   verrouillées sur leur type…) — une déclaration invalide est refusée avec sa
   cause exacte, jamais montée à moitié ; nom d'agent verrouillé (pas de
   traversée de chemin), racine configurable (`MAESTRO_MCP_DIR`), déclarations
   versionnées du dépôt (`core/mcp/`) valides ;
② **secrets** : les valeurs d'`env`/`headers` référencent l'environnement
   (`${VARIABLE}`), résolu au montage seulement — la forme publique (`to_dict`)
   masque les littéraux et laisse les références visibles ; une variable absente
   rend le serveur indisponible (`McpServerUnavailable`), échec **jamais
   relancé** (ENF-06) — sauf serveur déclaré **optionnel** (#125) : il est
   alors **omis du montage**, la tâche s'exécute sans lui ;
③ **montage sur l'exécution outillée** (#104) : le runtime résout les
   références et confie les serveurs (déclaration d'un serveur MCP factice
   local) au fournisseur ; un serveur non montable échoue proprement **avant**
   toute exécution ; un repli texte-seul exécute sans serveurs ;
④ **application de la configuration par le moteur** (#104) : les serveurs
   déclarés d'un agent équipent ses tâches, un agent sans déclaration exécute
   sans serveur, une déclaration invalide est un échec de tâche propre, et le
   dépôt est relu **à chaud** à chaque tâche — comme les playbooks (#78) ;
⑤ **couture fournisseur Claude** (#104, session pilotée du pilote #105) :
   traduction de la déclaration agnostique au format `mcp_servers` de l'Agent
   SDK, session verrouillée sur la seule liste déclarée (`strict_mcp_config`),
   et premier tour envoyé seulement une fois tous les serveurs déclarés
   **connectés** (statut sondé, délai borné) — un serveur en échec, absent ou
   jamais connecté lève `McpServerUnavailable` avant que l'agent ne travaille
   sans ses capacités.
⑥ **MCP Figma** (#115, bascule officielle #125/#128) : la déclaration versionnée
   du designer est le seul serveur MCP **officiel** Figma en http, token OAuth
   fourni par l'humain via `${FIGMA_OAUTH_TOKEN}` — jamais d'authentification
   automatique, aucun secret en clair. Le token résolu est expurgé des journaux,
   le serveur est **optionnel** — sans token, il est omis du montage, sans
   échec —, plus aucune configuration active ne référence l'ancien mode « Talk
   to Figma » (`cursor-talk-to-figma-mcp`/`FIGMA_CHANNEL`, #128), et la
   politique de permissions (#110) revue laisse passer les outils officiels
   (aucun outil de suppression dédié à barrer — garde-fou porté par le prompt
   du rôle, docs/20).

L'affichage lecture seule du volet MCP des fiches catalogue (API `/api/catalogue`)
est couvert dans `tests/test_controltower.py`.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from maestro.agents import QA_PROFILE, AgentRuntime
from maestro.agents.mcp import McpStore, ServeurMcp, resolus
from maestro.engine import OrchestrationEngine
from maestro.engine.retry import est_transitoire
from maestro.orchestrator import Orchestrator
from maestro.providers import ClaudeProvider, Credentials
from maestro.providers import claude as claude_mod
from maestro.providers.base import McpServerUnavailable, ModelProvider

# --- Fournisseurs factices --------------------------------------------------------------


class ConstantProvider(ModelProvider):
    """Renvoie toujours la même réponse (sert de planificateur factice)."""

    name = "constant"

    def __init__(self, response: str) -> None:
        self._response = response

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._response


class MontageEnregistreur(ModelProvider):
    """Exécutant outillé factice : enregistre les serveurs MCP reçus par `run_agent`."""

    name = "montage-enregistreur"

    def __init__(self) -> None:
        self.run_calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        return "TEXTE"

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None,
    ):
        self.run_calls.append({"mcp_serveurs": tuple(mcp_serveurs)})
        (Path(workspace) / "livrable.txt").write_text("contenu", encoding="utf-8")
        return f"OUTILLE #{len(self.run_calls)}"


class TexteSeul(ModelProvider):
    """Fournisseur *sans* exécution outillée : n'implémente que `generate`."""

    name = "texte-seul"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.calls.append(prompt)
        return "LIVRABLE TEXTE"


# --- Aides ------------------------------------------------------------------------------


def _serveur_factice_local(**surcharges) -> ServeurMcp:
    """La déclaration d'un serveur MCP factice **local** : une commande stdio.

    C'est la forme du pilote réel (un serveur lancé en sous-processus par la
    couche SDK), pointée ici sur l'interpréteur du test — aucun serveur n'est
    réellement lancé, les fournisseurs factices enregistrent ce qu'on leur
    confie.
    """
    champs: dict = {
        "nom": "factice",
        "type": "stdio",
        "commande": sys.executable,
        "args": ("-m", "serveur_mcp_factice"),
        "env": {"FAKE_TOKEN": "${MAESTRO_TEST_MCP_TOKEN}"},
    }
    champs.update(surcharges)
    return ServeurMcp(**champs)


def _ecrire_declaration(racine: Path, agent: str, serveurs: list[dict]) -> None:
    """Écrit la déclaration JSON de `agent` dans le dépôt `racine`."""
    racine.mkdir(parents=True, exist_ok=True)
    (racine / f"{agent}.json").write_text(
        json.dumps({"serveurs": serveurs}, ensure_ascii=False), encoding="utf-8"
    )


def _stdio_brut(nom: str = "factice", **surcharges) -> dict:
    """La forme stockée d'une déclaration stdio factice locale, surchargeable."""
    brut = {
        "nom": nom,
        "type": "stdio",
        "commande": sys.executable,
        "args": ["-m", "serveur_mcp_factice"],
        "env": {"FAKE_TOKEN": "${MAESTRO_TEST_MCP_TOKEN}"},
    }
    brut.update(surcharges)
    return brut


@pytest.fixture()
def store(tmp_path):
    """Dépôt de déclarations vierge, sur répertoire temporaire."""
    return McpStore(tmp_path / "mcp")


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


def _moteur(provider, store):
    """Boucle d'orchestration branchée sur le dépôt MCP (planification factice)."""
    planner = ConstantProvider(_plan_json())
    orchestrator = Orchestrator(planner, model="claude-opus-4-8")
    return OrchestrationEngine(provider, orchestrator, mcp=store)


# --- ① Déclarations & dépôt (#104) : validation à la lecture ----------------------------


def test_lire_une_declaration_stdio_valide(store):
    _ecrire_declaration(store.racine, "qa", [_stdio_brut()])

    (serveur,) = store.lire("qa")

    assert serveur.nom == "factice"
    assert serveur.type == "stdio"
    assert serveur.commande == sys.executable
    assert serveur.args == ("-m", "serveur_mcp_factice")
    assert serveur.env == {"FAKE_TOKEN": "${MAESTRO_TEST_MCP_TOKEN}"}


def test_lire_une_declaration_distante_valide(store):
    _ecrire_declaration(
        store.racine,
        "qa",
        [
            {
                "nom": "tickets",
                "type": "http",
                "url": "https://mcp.example.test/tickets",
                "headers": {"Authorization": "Bearer ${TICKETS_TOKEN}"},
            },
            {"nom": "flux", "type": "sse", "url": "http://localhost:9090/sse"},
        ],
    )

    tickets, flux = store.lire("qa")

    assert (tickets.type, tickets.url) == ("http", "https://mcp.example.test/tickets")
    assert tickets.headers == {"Authorization": "Bearer ${TICKETS_TOKEN}"}
    assert (flux.nom, flux.type, flux.headers) == ("flux", "sse", {})


def test_agent_sans_declaration_rend_vide(store):
    assert store.lire("qa") == ()


def test_json_illisible_refuse_avec_la_cause(store):
    store.racine.mkdir(parents=True)
    (store.racine / "qa.json").write_text("{pas du json", encoding="utf-8")

    with pytest.raises(ValueError, match="illisible.*'qa'"):
        store.lire("qa")


def test_forme_inattendue_refusee(store):
    store.racine.mkdir(parents=True)
    (store.racine / "qa.json").write_text('["pas", "un", "objet"]', encoding="utf-8")

    with pytest.raises(ValueError, match="serveurs"):
        store.lire("qa")


def test_serveurs_en_double_refuses(store):
    _ecrire_declaration(store.racine, "qa", [_stdio_brut("factice"), _stdio_brut("factice")])

    with pytest.raises(ValueError, match="double.*factice"):
        store.lire("qa")


def test_nom_de_serveur_invalide_refuse(store):
    # Le nom devient un préfixe d'outils (mcp__<nom>__<outil>) : slug sûr exigé.
    _ecrire_declaration(store.racine, "qa", [_stdio_brut("Slack !")])

    with pytest.raises(ValueError, match="nom de serveur MCP invalide"):
        store.lire("qa")


def test_type_inconnu_refuse(store):
    # « sdk » (instance en process) est hors périmètre d'une déclaration versionnée.
    _ecrire_declaration(store.racine, "qa", [_stdio_brut(type="sdk")])

    with pytest.raises(ValueError, match="type invalide"):
        store.lire("qa")


def test_stdio_sans_commande_refuse(store):
    _ecrire_declaration(store.racine, "qa", [_stdio_brut(commande="   ")])

    with pytest.raises(ValueError, match="commande vide"):
        store.lire("qa")


def test_stdio_avec_url_refuse(store):
    # Chaque forme est verrouillée sur son type : une déclaration ambiguë est
    # refusée plutôt qu'interprétée.
    _ecrire_declaration(store.racine, "qa", [_stdio_brut(url="https://exemple.test")])

    with pytest.raises(ValueError, match="url/headers interdits"):
        store.lire("qa")


def test_distant_sans_url_http_refuse(store):
    _ecrire_declaration(
        store.racine, "qa", [{"nom": "tickets", "type": "http", "url": "ftp://exemple.test"}]
    )

    with pytest.raises(ValueError, match="url invalide"):
        store.lire("qa")


def test_distant_avec_commande_ou_env_refuse(store):
    _ecrire_declaration(
        store.racine,
        "qa",
        [
            {
                "nom": "tickets",
                "type": "http",
                "url": "https://exemple.test",
                "env": {"TOKEN": "${TOKEN}"},
            }
        ],
    )

    with pytest.raises(ValueError, match="commande/args/env interdits"):
        store.lire("qa")


def test_cle_d_env_vide_refusee(store):
    _ecrire_declaration(store.racine, "qa", [_stdio_brut(env={"  ": "x"})])

    with pytest.raises(ValueError, match="clé d'env/headers vide"):
        store.lire("qa")


def test_nom_d_agent_verrouille_pas_de_traversee_de_chemin(store):
    # Même verrou que les autres dépôts : le nom d'agent est un slug, jamais un chemin.
    with pytest.raises(ValueError, match="nom d'agent invalide"):
        store.lire("../evasion")


def test_agents_liste_les_declarations_stockees(store):
    _ecrire_declaration(store.racine, "qa", [_stdio_brut()])
    _ecrire_declaration(store.racine, "devops", [_stdio_brut()])
    # Un fichier hors slug (nom d'agent inadmissible) est ignoré du listing.
    (store.racine / "Pas-Un-Agent.json").write_text("{}", encoding="utf-8")

    assert store.agents() == ("devops", "qa")


def test_racine_configurable_via_mcp_dir(tmp_path):
    class _Settings:
        mcp_dir = str(tmp_path / "depot-mcp")

    assert McpStore.default(_Settings()).racine == tmp_path / "depot-mcp"


def test_les_declarations_versionnees_du_depot_sont_valides():
    # Garde du dépôt Git : les déclarations commitées (pilotes #105/#106…) restent
    # lisibles et valides — une régression de format casserait les vrais runs.
    depot = McpStore(Path(__file__).resolve().parents[1] / "core" / "mcp")
    for agent in depot.agents():
        for serveur in depot.lire(agent):
            assert serveur.nom  # validée à la lecture : lire() aurait levé sinon


# --- ② Secrets : références ${VAR}, masquage public, résolution au montage --------------


def test_to_dict_masque_les_litteraux_et_laisse_les_references_visibles():
    serveur = _serveur_factice_local(
        env={"FAKE_TOKEN": "${MAESTRO_TEST_MCP_TOKEN}", "EN_CLAIR": "s3cret-oublie"}
    )

    publie = serveur.to_dict()

    # La référence (pas un secret) reste lisible ; un littéral, potentiellement
    # un secret posé en clair par erreur, n'est jamais réémis vers l'API/l'UI.
    assert publie["env"]["FAKE_TOKEN"] == "${MAESTRO_TEST_MCP_TOKEN}"
    assert publie["env"]["EN_CLAIR"] == "•••"
    assert "s3cret-oublie" not in json.dumps(publie)


def test_resolus_remplace_les_references_depuis_l_environnement():
    serveur = ServeurMcp(
        nom="tickets",
        type="http",
        url="https://exemple.test",
        headers={"Authorization": "Bearer ${TICKETS_TOKEN}"},
    )

    (resolu,) = resolus([serveur], {"TICKETS_TOKEN": "tok-123"})

    assert resolu.headers == {"Authorization": "Bearer tok-123"}
    # La déclaration d'origine reste intacte : la forme résolue ne vit qu'en mémoire.
    assert serveur.headers == {"Authorization": "Bearer ${TICKETS_TOKEN}"}


def test_resolus_sans_reference_rend_la_declaration_inchangee():
    serveur = _serveur_factice_local(env={"MODE": "lecture"})
    assert resolus([serveur], {}) == (serveur,)


def test_variable_absente_rend_le_serveur_indisponible():
    serveur = _serveur_factice_local()

    with pytest.raises(McpServerUnavailable, match="factice.*MAESTRO_TEST_MCP_TOKEN"):
        resolus([serveur], {})


def test_variable_vide_vaut_absente():
    with pytest.raises(McpServerUnavailable):
        resolus([_serveur_factice_local()], {"MAESTRO_TEST_MCP_TOKEN": ""})


def test_indisponibilite_jamais_relancee():
    # ENF-06 : configuration ou secret à corriger — relancer reproduirait l'échec.
    assert not est_transitoire(McpServerUnavailable("serveur MCP 'factice' non montable"))


def test_serveur_optionnel_sans_variable_est_omis_du_montage():
    # #125 : un serveur optionnel dont le secret n'est pas fourni n'échoue pas —
    # il est simplement absent du montage, la tâche s'exécute sans lui.
    assert resolus([_serveur_factice_local(optionnel=True)], {}) == ()


def test_serveur_optionnel_resolu_est_monte_normalement():
    (monte,) = resolus(
        [_serveur_factice_local(optionnel=True)], {"MAESTRO_TEST_MCP_TOKEN": "tok-125"}
    )
    assert monte.env == {"FAKE_TOKEN": "tok-125"}


def test_l_omission_d_un_optionnel_n_excuse_pas_un_serveur_requis():
    # Le contrat historique tient : seul le serveur déclaré optionnel est omis,
    # un serveur requis non résolu reste un échec propre.
    optionnel = _serveur_factice_local(nom="en-option", optionnel=True)
    requis = _serveur_factice_local(nom="requis")

    with pytest.raises(McpServerUnavailable, match="requis"):
        resolus([optionnel, requis], {})


def test_optionnel_non_booleen_refuse_a_la_lecture(store):
    # Pas de coercition : "false" (chaîne) serait vrai — déclaration refusée.
    _ecrire_declaration(store.racine, "qa", [_stdio_brut(optionnel="false")])

    with pytest.raises(ValueError, match="optionnel invalide"):
        store.lire("qa")


# --- ③ Montage sur l'exécution outillée (runtime) ---------------------------------------


def test_le_runtime_monte_les_serveurs_resolus_sur_l_execution_outillee(monkeypatch):
    monkeypatch.setenv("MAESTRO_TEST_MCP_TOKEN", "tok-runtime")
    provider = MontageEnregistreur()
    runtime = AgentRuntime(provider, QA_PROFILE)

    asyncio.run(runtime.execute("Vérifie le livrable", mcp_serveurs=(_serveur_factice_local(),)))

    (appel,) = provider.run_calls
    (monte,) = appel["mcp_serveurs"]
    assert monte.nom == "factice"
    # Le fournisseur reçoit la forme montable : références déjà résolues.
    assert monte.env == {"FAKE_TOKEN": "tok-runtime"}


def test_serveur_non_montable_echoue_avant_toute_execution(monkeypatch):
    monkeypatch.delenv("MAESTRO_TEST_MCP_TOKEN", raising=False)
    provider = MontageEnregistreur()
    runtime = AgentRuntime(provider, QA_PROFILE)

    with pytest.raises(McpServerUnavailable, match="factice"):
        asyncio.run(runtime.execute("Vérifie", mcp_serveurs=(_serveur_factice_local(),)))

    # Échec propre *avant* l'exécution : le fournisseur n'a jamais été appelé
    # (et aucun espace de travail n'a été ouvert pour rien).
    assert provider.run_calls == []


# --- ④ Application de la configuration par le moteur ------------------------------------


def test_le_moteur_monte_les_serveurs_declares_de_l_agent(monkeypatch, store):
    monkeypatch.setenv("MAESTRO_TEST_MCP_TOKEN", "tok-moteur")
    _ecrire_declaration(store.racine, "developpeur", [_stdio_brut()])
    provider = MontageEnregistreur()

    rapport = asyncio.run(_moteur(provider, store).run("Objectif"))

    assert rapport.resultats[0].ok
    (monte,) = provider.run_calls[-1]["mcp_serveurs"]
    assert (monte.nom, monte.env) == ("factice", {"FAKE_TOKEN": "tok-moteur"})


def test_agent_sans_declaration_execute_sans_serveur(store):
    provider = MontageEnregistreur()

    rapport = asyncio.run(_moteur(provider, store).run("Objectif"))

    assert rapport.resultats[0].ok
    assert provider.run_calls[-1]["mcp_serveurs"] == ()


def test_declaration_invalide_est_un_echec_de_tache_propre(store):
    store.racine.mkdir(parents=True)
    (store.racine / "developpeur.json").write_text("{pas du json", encoding="utf-8")
    provider = MontageEnregistreur()

    rapport = asyncio.run(_moteur(provider, store).run("Objectif"))

    (resultat,) = rapport.resultats
    # Validation à la lecture : la cause exacte est consignée, l'agent n'a
    # jamais exécuté — on ne monte pas une configuration douteuse.
    assert not resultat.ok
    assert "déclaration MCP illisible" in (resultat.erreur or "")
    assert provider.run_calls == []


def test_serveur_indisponible_est_un_echec_de_tache_consigne(monkeypatch, store):
    monkeypatch.delenv("MAESTRO_TEST_MCP_TOKEN", raising=False)
    _ecrire_declaration(store.racine, "developpeur", [_stdio_brut()])
    provider = MontageEnregistreur()

    rapport = asyncio.run(_moteur(provider, store).run("Objectif"))

    (resultat,) = rapport.resultats
    assert not resultat.ok
    assert "factice" in (resultat.erreur or "")
    assert "MAESTRO_TEST_MCP_TOKEN" in (resultat.erreur or "")
    # L'échec précède tout appel modèle (jamais relancé non plus — ENF-06,
    # cf. test_indisponibilite_jamais_relancee).
    assert provider.run_calls == []


def test_la_declaration_s_applique_a_chaud_a_la_tache_suivante(store):
    # Même contrat que les playbooks (#78) : le dépôt est relu à chaque tâche,
    # une déclaration ajoutée vaut pour la suivante sans reconstruire le moteur.
    provider = MontageEnregistreur()
    moteur = _moteur(provider, store)

    asyncio.run(moteur.run("Objectif"))
    assert provider.run_calls[-1]["mcp_serveurs"] == ()

    _ecrire_declaration(store.racine, "developpeur", [_stdio_brut("ajoute", env={})])

    asyncio.run(moteur.run("Objectif"))
    (monte,) = provider.run_calls[-1]["mcp_serveurs"]
    assert monte.nom == "ajoute"


def test_un_repli_texte_seul_execute_sans_serveurs(store):
    # Un fournisseur sans run_agent retombe sur le livrable texte : le chemin
    # texte n'expose aucun outil, MCP compris — la déclaration n'empêche rien.
    _ecrire_declaration(store.racine, "developpeur", [_stdio_brut("local", env={})])
    provider = TexteSeul()

    rapport = asyncio.run(_moteur(provider, store).run("Objectif"))

    assert rapport.resultats[0].ok
    assert provider.calls  # le livrable est bien passé par generate()


# --- ⑤ Couture fournisseur Claude : traduction SDK et contrôle d'état à l'init ----------


def test_traduction_sdk_d_une_commande_locale():
    serveur = ServeurMcp(
        nom="factice",
        type="stdio",
        commande="python",
        args=("-m", "serveur"),
        env={"FAKE_TOKEN": "tok"},
    )

    assert claude_mod._config_mcp_sdk(serveur) == {
        "type": "stdio",
        "command": "python",
        "args": ["-m", "serveur"],
        "env": {"FAKE_TOKEN": "tok"},
    }


def test_traduction_sdk_n_emet_pas_les_options_vides():
    minimal = ServeurMcp(nom="factice", type="stdio", commande="python")
    assert claude_mod._config_mcp_sdk(minimal) == {"type": "stdio", "command": "python"}

    distant = ServeurMcp(nom="flux", type="sse", url="https://exemple.test/sse")
    assert claude_mod._config_mcp_sdk(distant) == {
        "type": "sse",
        "url": "https://exemple.test/sse",
    }


def test_traduction_sdk_d_un_endpoint_distant_avec_en_tetes():
    serveur = ServeurMcp(
        nom="tickets",
        type="http",
        url="https://exemple.test",
        headers={"Authorization": "Bearer tok"},
    )

    assert claude_mod._config_mcp_sdk(serveur) == {
        "type": "http",
        "url": "https://exemple.test",
        "headers": {"Authorization": "Bearer tok"},
    }


class _FakeTextBlock:
    def __init__(self, text):
        self.text = text


class _FakeAssistantMessage:
    def __init__(self, content):
        self.content = content


class _FakeClaudeSDKClient:
    """`ClaudeSDKClient` factice : statuts MCP scriptés, un sondage les consomme.

    `statuts` est la suite des réponses de `get_mcp_status` (la dernière se
    répète) : elle scripte un serveur qui se connecte, échoue ou ne vient
    jamais — sans sous-processus CLI ni réseau.
    """

    derniere_instance: "_FakeClaudeSDKClient | None" = None

    def __init__(self, options):
        self.options = options
        self.sondages = 0
        self.prompts: list[str] = []
        type(self).derniere_instance = self

    statuts: list[list[dict]] = [[]]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_mcp_status(self):
        etape = min(self.sondages, len(self.statuts) - 1)
        self.sondages += 1
        return {"mcpServers": self.statuts[etape]}

    async def query(self, prompt):
        self.prompts.append(prompt)

    async def receive_response(self):
        yield _FakeAssistantMessage([_FakeTextBlock("Livré.")])


def _patch_sdk_pilote(monkeypatch, statuts):
    """Monte le client factice (statuts scriptés) et neutralise les délais de sondage."""
    fake = type("_Client", (_FakeClaudeSDKClient,), {"statuts": statuts})
    monkeypatch.setattr(claude_mod, "ClaudeSDKClient", fake)
    monkeypatch.setattr(claude_mod, "AssistantMessage", _FakeAssistantMessage)
    monkeypatch.setattr(claude_mod, "TextBlock", _FakeTextBlock)
    monkeypatch.setattr(claude_mod, "_MCP_SONDAGE_S", 0.0)
    return fake


def _run_agent(provider, tmp_path, serveurs):
    return asyncio.run(
        provider.run_agent(
            "Fais",
            model="claude-sonnet-5",
            workspace=tmp_path,
            tools=("Read",),
            mcp_serveurs=serveurs,
        )
    )


def test_run_agent_monte_les_serveurs_et_verrouille_la_session(monkeypatch, tmp_path):
    fake = _patch_sdk_pilote(
        monkeypatch, [[{"name": "factice", "status": "connected"}]]
    )
    provider = ClaudeProvider(Credentials())

    resultat = _run_agent(
        provider, tmp_path, (ServeurMcp(nom="factice", type="stdio", commande="python"),)
    )

    assert resultat == "Livré."
    client = fake.derniere_instance
    # La session ne monte que la liste déclarée, traduite au format SDK, et
    # `strict_mcp_config` écarte toute configuration MCP ambiante.
    assert client.options.mcp_servers == {"factice": {"type": "stdio", "command": "python"}}
    assert client.options.strict_mcp_config is True
    assert client.prompts == ["Fais"]


def test_le_premier_tour_attend_la_connexion_des_serveurs(monkeypatch, tmp_path):
    # Constat du pilote #105 : le CLI enregistre les outils MCP après l'ouverture
    # de session — le prompt ne part qu'une fois le serveur « connected ».
    fake = _patch_sdk_pilote(
        monkeypatch,
        [
            [{"name": "factice", "status": "pending"}],
            [{"name": "factice", "status": "connected"}],
        ],
    )
    provider = ClaudeProvider(Credentials())

    resultat = _run_agent(
        provider, tmp_path, (ServeurMcp(nom="factice", type="stdio", commande="python"),)
    )

    assert resultat == "Livré."
    client = fake.derniere_instance
    assert client.sondages == 2  # un « pending » a bien été attendu, pas refusé
    assert client.prompts == ["Fais"]


def test_un_serveur_en_echec_stoppe_avant_tout_appel_modele(monkeypatch, tmp_path):
    fake = _patch_sdk_pilote(
        monkeypatch,
        [[{"name": "factice", "status": "failed", "error": "spawn ENOENT"}]],
    )
    provider = ClaudeProvider(Credentials())

    with pytest.raises(McpServerUnavailable, match="factice.*spawn ENOENT"):
        _run_agent(
            provider, tmp_path, (ServeurMcp(nom="factice", type="stdio", commande="python"),)
        )

    # L'erreur propre précède le travail de l'agent : aucun prompt n'est parti.
    assert fake.derniere_instance.prompts == []


def test_un_serveur_jamais_connecte_echoue_a_l_echeance(monkeypatch, tmp_path):
    # Un « pending » sans fin (ou un serveur absent de la session) est un serveur
    # qui ne viendra pas : échéance immédiate ici pour ne pas attendre le délai réel.
    fake = _patch_sdk_pilote(monkeypatch, [[{"name": "factice", "status": "pending"}]])
    monkeypatch.setattr(claude_mod, "_MCP_CONNEXION_MAX_S", 0.0)
    provider = ClaudeProvider(Credentials())

    with pytest.raises(McpServerUnavailable, match="toujours pas connecté"):
        _run_agent(
            provider, tmp_path, (ServeurMcp(nom="factice", type="stdio", commande="python"),)
        )

    assert fake.derniere_instance.prompts == []


def test_needs_auth_et_disabled_valent_echec_definitif(monkeypatch, tmp_path):
    # Ces états ne se résorbent pas en attendant : refus immédiat, état nommé.
    _patch_sdk_pilote(
        monkeypatch,
        [
            [
                {"name": "slack", "status": "needs-auth"},
                {"name": "tickets", "status": "disabled"},
            ]
        ],
    )
    provider = ClaudeProvider(Credentials())

    with pytest.raises(McpServerUnavailable) as excinfo:
        _run_agent(
            provider,
            tmp_path,
            (
                ServeurMcp(nom="slack", type="stdio", commande="npx"),
                ServeurMcp(nom="tickets", type="stdio", commande="npx"),
            ),
        )

    message = str(excinfo.value)
    assert "slack : état « needs-auth »" in message
    assert "tickets : état « disabled »" in message


def test_sans_serveur_declare_la_session_reste_verrouillee(monkeypatch, tmp_path):
    # Sans déclaration, run_agent garde le chemin one-shot — mais la session est
    # tout autant verrouillée : aucune config MCP ambiante (utilisateur, projet).
    vu: dict[str, object] = {}

    async def fake_query(*, prompt, options):
        vu["mcp_servers"] = options.mcp_servers
        vu["strict"] = options.strict_mcp_config
        yield _FakeAssistantMessage([_FakeTextBlock("Livré.")])

    monkeypatch.setattr(claude_mod, "query", fake_query)
    monkeypatch.setattr(claude_mod, "AssistantMessage", _FakeAssistantMessage)
    monkeypatch.setattr(claude_mod, "TextBlock", _FakeTextBlock)
    provider = ClaudeProvider(Credentials())

    assert _run_agent(provider, tmp_path, ()) == "Livré."
    assert vu["mcp_servers"] == {}
    assert vu["strict"] is True


# --- ⑥ MCP Figma (#115, bascule officielle #125/#128) : secrets jamais en clair ---------


def _depot_du_repo(nom: str) -> Path:
    """La racine `core/<nom>/` du dépôt Git — les configurations réellement versionnées."""
    return Path(__file__).resolve().parents[1] / "core" / nom


def _serveur_designer() -> ServeurMcp:
    """Le serveur Figma officiel, seule déclaration du designer depuis la bascule #128."""
    (officiel,) = McpStore(_depot_du_repo("mcp")).lire("designer")
    return officiel


def test_le_designer_declare_le_serveur_officiel_figma_sans_secret_en_clair():
    # La déclaration versionnée : le serveur MCP OFFICIEL Figma (#125, bascule
    # #128) en endpoint http — token en référence ${VAR}, jamais de valeur
    # littérale dans le dépôt Git.
    officiel = _serveur_designer()

    assert (officiel.nom, officiel.type) == ("figma-officiel", "http")
    assert officiel.url == "https://mcp.figma.com/mcp"
    assert officiel.headers == {"Authorization": "Bearer ${FIGMA_OAUTH_TOKEN}"}
    # Optionnel : sans token fourni, le serveur est omis du montage, sans échec.
    assert officiel.optionnel
    # Forme publique (API/UI) : la référence reste visible — c'est le contrat
    # qui prouve qu'aucun littéral n'y figure (un littéral serait masqué).
    assert officiel.to_dict()["headers"] == {"Authorization": "Bearer ${FIGMA_OAUTH_TOKEN}"}


def test_aucune_configuration_active_ne_reference_l_ancien_mode_talk_to_figma():
    # #128 : le mode « Talk to Figma » (pilote #115 — plugin compagnon, relais
    # WebSocket, canal d'appairage) est retiré de la configuration active. Sa
    # trace ne survit que dans docs/20 (historique + repli documenté).
    for declaration in sorted(_depot_du_repo("mcp").glob("*.json")):
        contenu = declaration.read_text(encoding="utf-8")
        assert "cursor-talk-to-figma" not in contenu, declaration.name
        assert "FIGMA_CHANNEL" not in contenu, declaration.name
    for politique in sorted(_depot_du_repo("permissions").glob("*.json")):
        assert "mcp__figma__" not in politique.read_text(encoding="utf-8"), politique.name


def test_le_token_officiel_fourni_par_l_humain_monte_le_serveur_et_reste_masque():
    # #125 : le token OAuth est une ACTION HUMAINE (env aujourd'hui, coffre ou
    # Control Tower demain) — fourni, il monte la voie officielle ; et il est
    # enregistré au registre de rédaction à la résolution (#109).
    from maestro.telemetry.redact import MARQUEUR_SECRET, redact_secrets

    token = "figd-oauth-mcp-connect-4b7e91"

    (monte,) = resolus([_serveur_designer()], {"FIGMA_OAUTH_TOKEN": token})

    assert monte.nom == "figma-officiel"
    assert monte.headers == {"Authorization": f"Bearer {token}"}
    journal = f"Session ouverte avec le token {token}."
    assert token not in redact_secrets(journal)
    assert MARQUEUR_SECRET in redact_secrets(journal)


def test_sans_token_le_serveur_figma_est_omis_pas_d_echec():
    # Le serveur est optionnel (#125) : sans token, une tâche de design
    # s'exécute simplement sans outils Figma (plus d'échec au montage comme au
    # pilote #115).
    assert resolus([_serveur_designer()], {}) == ()


def test_la_declaration_officielle_se_traduit_au_format_sdk():
    # La voie officielle est un endpoint http standard côté SDK : url + header
    # Authorization résolu — aucun couplage à un fournisseur de modèle.
    (monte,) = resolus([_serveur_designer()], {"FIGMA_OAUTH_TOKEN": "tok-sdk"})

    assert claude_mod._config_mcp_sdk(monte) == {
        "type": "http",
        "url": "https://mcp.figma.com/mcp",
        "headers": {"Authorization": "Bearer tok-sdk"},
    }


def test_la_politique_du_designer_laisse_passer_les_outils_officiels():
    # Politique revue à la bascule #128 : le serveur officiel n'expose aucun
    # outil de suppression dédié (l'édition passe par le seul `use_figma`,
    # insécable — docs/20) ; le garde-fou « il propose, il ne remplace pas »
    # est porté par le prompt du rôle, pas par un deny à la granularité de
    # l'outil. La politique versionnée reste en place, vide de tout nom
    # d'outil de l'ancien serveur communautaire.
    from maestro.agents.permissions import PermissionStore

    politique = PermissionStore(_depot_du_repo("permissions")).lire("designer")

    assert politique is not None
    assert politique.serveur_autorise("figma-officiel")
    for outil in ("use_figma", "get_design_context", "get_metadata", "create_new_file"):
        assert politique.autorise(f"mcp__figma-officiel__{outil}")
