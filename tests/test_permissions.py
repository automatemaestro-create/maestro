"""Tests de la politique de permissions par agent et par outil (ticket #110, lot #107).

Aucun appel réseau : dépôts sur répertoires temporaires, fournisseurs factices,
SDK monkeypatché. Fait partie du lot final « tests + doc » du renforcement
sécurité (#102) — couvre le critère « politique de permissions appliquée et
violations tracées » :

① **sémantique de la politique** (`PolitiqueOutils`) : `deny` l'emporte
   toujours ; `allow` vide = tout permis, non vide = liste fermée ; une entrée
   couvre l'outil exact ou tout ce qu'elle préfixe aux frontières `__`
   (`mcp__slack` couvre `mcp__slack__send_message`, pas `mcp__slackbot__x`) ;
② **dépôt** (`PermissionStore`) : validation à la lecture (JSON illisible,
   forme inattendue, entrée malformée — une politique douteuse est refusée
   avec sa cause, jamais appliquée à moitié), nom d'agent verrouillé, racine
   configurable (`MAESTRO_PERMISSIONS_DIR`) ;
③ **application au montage** (runtime, #110) : les outils intégrés refusés
   sont retirés de la session avant son ouverture, un serveur MCP entièrement
   refusé n'est jamais monté (ses secrets ne sont même pas résolus), un refus
   individuel laisse le serveur monté (le refus au vol s'en charge) ;
④ **application par le moteur** : politique invalide = échec de tâche propre
   avant toute exécution ; **violation tracée** au journal (étape
   `<tâche>:refus-outil`, statut `refus_outil` — le fil temps réel Control
   Tower la voit) sans condamner le run ; dépôt relu à chaud à chaque tâche ;
⑤ **refus au vol** (fournisseur Claude) : le hook PreToolUse — seul point de
   contrôle sous `bypassPermissions` — refuse un appel interdit avec son
   motif, signale la violation via `on_refus`, et n'échoue jamais lui-même
   (un traçage en échec est avalé).
"""

import asyncio
import json
from pathlib import Path

import pytest

from maestro.agents import QA_PROFILE, AgentRuntime
from maestro.agents.mcp import ServeurMcp
from maestro.agents.permissions import PermissionStore, PolitiqueOutils
from maestro.engine import OrchestrationEngine
from maestro.engine.executor import STATUT_REFUS_OUTIL, SUFFIXE_ETAPE_REFUS
from maestro.orchestrator import Orchestrator
from maestro.providers import ClaudeProvider, Credentials
from maestro.providers import claude as claude_mod
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal

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
    """Exécutant outillé factice : enregistre outils, serveurs et politique reçus."""

    name = "montage-enregistreur"

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
        self.run_calls.append(
            {
                "tools": tuple(tools),
                "mcp_serveurs": tuple(mcp_serveurs),
                "politique": politique,
                "on_refus": on_refus,
            }
        )
        (Path(workspace) / "livrable.txt").write_text("contenu", encoding="utf-8")
        return f"OUTILLE #{len(self.run_calls)}"


class ViolateurProvider(MontageEnregistreur):
    """Exécutant factice qui tente un outil interdit : simule le refus au vol du SDK.

    C'est le comportement du fournisseur réel (hook PreToolUse) vu du moteur :
    l'appel interdit est refusé, la violation signalée via `on_refus`, et
    l'exécution **poursuit** — elle rend son livrable malgré le refus.
    """

    name = "violateur"

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, plafond_tours=None, projet=None,
    ):
        if politique is not None and not politique.autorise("Bash") and on_refus is not None:
            on_refus("Bash", politique.raison_refus("Bash"))
        return await super().run_agent(
            prompt, model=model, system_prompt=system_prompt, workspace=workspace,
            tools=tools, mcp_serveurs=mcp_serveurs, politique=politique, on_refus=on_refus,
            plafond_tours=plafond_tours,
        )


# --- Aides ------------------------------------------------------------------------------


def _ecrire_politique(racine: Path, agent: str, politique: dict) -> None:
    """Écrit la politique JSON de `agent` dans le dépôt `racine`."""
    racine.mkdir(parents=True, exist_ok=True)
    (racine / f"{agent}.json").write_text(
        json.dumps(politique, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture()
def store(tmp_path):
    """Dépôt de politiques vierge, sur répertoire temporaire."""
    return PermissionStore(tmp_path / "permissions")


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
    """Boucle d'orchestration branchée sur le dépôt de permissions (planification factice)."""
    planner = ConstantProvider(_plan_json())
    orchestrator = Orchestrator(planner, model="claude-opus-4-8")
    return OrchestrationEngine(provider, orchestrator, permissions=store)


# --- ① Sémantique de la politique -------------------------------------------------------


def test_sans_liste_tout_est_permis():
    politique = PolitiqueOutils()
    assert politique.autorise("Bash")
    assert politique.autorise("mcp__slack__send_message")


def test_deny_l_emporte_toujours_sur_allow():
    politique = PolitiqueOutils(allow=("Bash",), deny=("Bash",))
    assert not politique.autorise("Bash")


def test_allow_non_vide_est_une_liste_fermee():
    politique = PolitiqueOutils(allow=("Read", "Grep"))
    assert politique.autorise("Read")
    assert not politique.autorise("Bash")
    assert not politique.autorise("mcp__slack__send_message")


def test_une_entree_couvre_par_prefixe_aux_frontieres_de_segments():
    politique = PolitiqueOutils(deny=("mcp__slack",))
    # Le serveur entier est couvert : chacun de ses outils est refusé…
    assert not politique.autorise("mcp__slack")
    assert not politique.autorise("mcp__slack__send_message")
    # …mais jamais en plein mot : un autre serveur au nom voisin reste permis.
    assert politique.autorise("mcp__slackbot__envoyer")


def test_filtre_outils_retire_les_refuses():
    politique = PolitiqueOutils(deny=("Bash", "Write"))
    assert politique.filtre_outils(("Read", "Write", "Bash", "Grep")) == ("Read", "Grep")


def test_serveur_entierement_refuse_n_est_pas_montable():
    assert not PolitiqueOutils(deny=("mcp__slack",)).serveur_autorise("slack")


def test_allow_fermee_sans_outil_du_serveur_ne_le_monte_pas():
    politique = PolitiqueOutils(allow=("Read", "mcp__tickets__creer"))
    assert politique.serveur_autorise("tickets")
    assert not politique.serveur_autorise("slack")


def test_refus_individuel_laisse_le_serveur_monte():
    # Un seul outil du serveur est refusé : le serveur reste monté, c'est le
    # refus au vol (hook PreToolUse) qui applique l'interdit.
    politique = PolitiqueOutils(deny=("mcp__slack__chat_delete",))
    assert politique.serveur_autorise("slack")
    assert not politique.autorise("mcp__slack__chat_delete")
    assert politique.autorise("mcp__slack__send_message")


def test_raison_refus_nomme_l_outil_et_la_liste_en_cause():
    politique = PolitiqueOutils(allow=("Read",), deny=("Bash",))
    assert "deny" in politique.raison_refus("Bash")
    assert "'Bash'" in politique.raison_refus("Bash")
    assert "allow" in politique.raison_refus("Write")
    # Le motif invite l'agent à poursuivre : le refus n'est jamais fatal au run.
    assert "Poursuis la tâche" in politique.raison_refus("Write")


def test_aller_retour_dict_preserve_la_politique():
    politique = PolitiqueOutils(allow=("Read",), deny=("mcp__slack",))
    assert PolitiqueOutils.from_dict(politique.to_dict()) == politique


# --- ② Dépôt : validation à la lecture --------------------------------------------------


def test_lire_une_politique_valide(store):
    _ecrire_politique(
        store.racine, "qa", {"allow": ["Read", "mcp__tickets"], "deny": ["Bash"]}
    )

    politique = store.lire("qa")

    assert politique == PolitiqueOutils(allow=("Read", "mcp__tickets"), deny=("Bash",))


def test_agent_sans_politique_rend_none(store):
    # Pas de fichier = pas de politique = tout permis (comportement historique).
    assert store.lire("qa") is None


def test_les_listes_absentes_valent_vides(store):
    _ecrire_politique(store.racine, "qa", {})
    assert store.lire("qa") == PolitiqueOutils()


def test_les_entrees_sont_dedoublonnees(store):
    _ecrire_politique(store.racine, "qa", {"deny": ["Bash", "Bash", "Write"]})
    assert store.lire("qa").deny == ("Bash", "Write")


def test_json_illisible_refuse_avec_la_cause(store):
    store.racine.mkdir(parents=True)
    (store.racine / "qa.json").write_text("{pas du json", encoding="utf-8")

    with pytest.raises(ValueError, match="illisible.*'qa'"):
        store.lire("qa")


def test_forme_inattendue_refusee(store):
    store.racine.mkdir(parents=True)
    (store.racine / "qa.json").write_text('["pas", "un", "objet"]', encoding="utf-8")

    with pytest.raises(ValueError, match="allow.*deny"):
        store.lire("qa")


def test_liste_non_liste_refusee(store):
    _ecrire_politique(store.racine, "qa", {"allow": "Bash"})

    with pytest.raises(ValueError, match="allow doit être une liste"):
        store.lire("qa")


@pytest.mark.parametrize("entree", ["", "outil interdit", "mcp__", 42])
def test_entree_malformee_refusee_en_bloc(store, entree):
    # Une politique fautive est refusée avec sa cause — jamais appliquée en partie.
    _ecrire_politique(store.racine, "qa", {"deny": ["Read", entree]})

    with pytest.raises(ValueError, match="entrée deny"):
        store.lire("qa")


def test_nom_d_agent_verrouille_pas_de_traversee_de_chemin(store):
    with pytest.raises(ValueError, match="nom d'agent invalide"):
        store.lire("../evasion")


def test_agents_liste_les_politiques_stockees(store):
    _ecrire_politique(store.racine, "qa", {})
    _ecrire_politique(store.racine, "devops", {})
    (store.racine / "Pas-Un-Agent.json").write_text("{}", encoding="utf-8")

    assert store.agents() == ("devops", "qa")


def test_racine_configurable_via_permissions_dir(tmp_path):
    class _Settings:
        permissions_dir = str(tmp_path / "depot-permissions")

    assert PermissionStore.default(_Settings()).racine == tmp_path / "depot-permissions"


def test_les_politiques_versionnees_du_depot_sont_valides():
    # Garde du dépôt Git : les politiques commitées (core/permissions/) restent
    # lisibles et valides — une régression de format casserait les vrais runs.
    depot = PermissionStore(Path(__file__).resolve().parents[1] / "core" / "permissions")
    for agent in depot.agents():
        depot.lire(agent)  # validée à la lecture : lire() lèverait sinon


# --- ③ Application au montage (runtime) -------------------------------------------------


def test_les_outils_refuses_sont_retires_de_la_session():
    provider = MontageEnregistreur()
    runtime = AgentRuntime(provider, QA_PROFILE)

    asyncio.run(
        runtime.execute("Vérifie le livrable", politique=PolitiqueOutils(deny=("Bash",)))
    )

    (appel,) = provider.run_calls
    # L'agent ne voit jamais l'outil refusé : retiré avant l'ouverture de session.
    assert "Bash" not in appel["tools"]
    assert "Read" in appel["tools"]


def test_allow_fermee_ne_monte_que_les_outils_cites():
    provider = MontageEnregistreur()
    runtime = AgentRuntime(provider, QA_PROFILE)

    asyncio.run(runtime.execute("Vérifie", politique=PolitiqueOutils(allow=("Read", "Grep"))))

    (appel,) = provider.run_calls
    assert appel["tools"] == ("Read", "Grep")


def test_serveur_refuse_jamais_monte_et_secrets_jamais_resolus(monkeypatch):
    # Le serveur refusé porte une référence ${VAR} irrésoluble : si la politique
    # ne l'écartait pas *avant* la résolution, l'exécution échouerait en
    # McpServerUnavailable. Elle passe : ses secrets n'ont jamais été demandés.
    monkeypatch.delenv("MAESTRO_TEST_MCP_TOKEN", raising=False)
    interdit = ServeurMcp(
        nom="interdit", type="stdio", commande="python",
        env={"TOKEN": "${MAESTRO_TEST_MCP_TOKEN}"},
    )
    provider = MontageEnregistreur()
    runtime = AgentRuntime(provider, QA_PROFILE)

    asyncio.run(
        runtime.execute(
            "Vérifie",
            mcp_serveurs=(interdit,),
            politique=PolitiqueOutils(deny=("mcp__interdit",)),
        )
    )

    (appel,) = provider.run_calls
    assert appel["mcp_serveurs"] == ()


def test_refus_individuel_monte_le_serveur_et_transmet_la_politique():
    serveur = ServeurMcp(nom="slack", type="stdio", commande="npx")
    politique = PolitiqueOutils(deny=("mcp__slack__chat_delete",))
    vu: list[tuple[str, str]] = []
    provider = MontageEnregistreur()
    runtime = AgentRuntime(provider, QA_PROFILE)

    asyncio.run(
        runtime.execute(
            "Vérifie",
            mcp_serveurs=(serveur,),
            politique=politique,
            on_refus=lambda outil, raison: vu.append((outil, raison)),
        )
    )

    (appel,) = provider.run_calls
    # Serveur monté malgré le refus individuel : le refus au vol s'en chargera —
    # politique et canal de traçage sont transmis au fournisseur pour ça.
    assert [s.nom for s in appel["mcp_serveurs"]] == ["slack"]
    assert appel["politique"] is politique
    assert appel["on_refus"] is not None


# --- ④ Application par le moteur : échec propre, traçage, application à chaud -----------


def test_politique_invalide_est_un_echec_de_tache_propre(store):
    store.racine.mkdir(parents=True)
    (store.racine / "developpeur.json").write_text("{pas du json", encoding="utf-8")
    provider = MontageEnregistreur()

    rapport = asyncio.run(_moteur(provider, store).run("Objectif"))

    (resultat,) = rapport.resultats
    # Validation à la lecture : la cause exacte est consignée, l'agent n'a
    # jamais exécuté — on n'exécute pas sous une politique douteuse.
    assert not resultat.ok
    assert "politique de permissions illisible" in (resultat.erreur or "")
    assert provider.run_calls == []


def test_la_violation_est_tracee_au_journal_sans_condamner_le_run(store):
    _ecrire_politique(store.racine, "developpeur", {"deny": ["Bash"]})
    provider = ViolateurProvider()
    journal = RunJournal(run_id="run-violation")

    rapport = asyncio.run(_moteur(provider, store).run("Objectif", journal=journal))

    # Le refus n'est jamais fatal : la tâche a rendu son livrable.
    (resultat,) = rapport.resultats
    assert resultat.ok
    # La violation est consignée : étape dédiée `<tâche>:refus-outil` (le pont
    # Control Tower la mue en activité d'agent, visible au fil temps réel).
    (refus,) = [
        r for r in journal.records if r.etape == f"tache-unique{SUFFIXE_ETAPE_REFUS}"
    ]
    assert refus.statut == STATUT_REFUS_OUTIL
    assert refus.entree == "Bash"
    assert "deny" in refus.sortie
    assert refus.agent == "developpeur"


def test_agent_sans_politique_execute_tout_permis(store):
    provider = MontageEnregistreur()

    rapport = asyncio.run(_moteur(provider, store).run("Objectif"))

    assert rapport.resultats[0].ok
    appel = provider.run_calls[-1]
    assert appel["politique"] is None
    assert "Bash" in appel["tools"]


def test_la_politique_s_applique_a_chaud_a_la_tache_suivante(store):
    # Même contrat que les playbooks (#78) et les déclarations MCP (#104) : le
    # dépôt est relu à chaque tâche, une politique ajoutée vaut pour la suivante.
    provider = MontageEnregistreur()
    moteur = _moteur(provider, store)

    asyncio.run(moteur.run("Objectif"))
    assert "Bash" in provider.run_calls[-1]["tools"]

    _ecrire_politique(store.racine, "developpeur", {"deny": ["Bash"]})

    asyncio.run(moteur.run("Objectif"))
    assert "Bash" not in provider.run_calls[-1]["tools"]


# --- ⑤ Refus au vol : le hook PreToolUse du fournisseur Claude --------------------------


def _hook(politique, on_refus=None):
    return claude_mod._hook_permissions(politique, on_refus)


def test_le_hook_laisse_passer_un_appel_permis():
    hook = _hook(PolitiqueOutils(deny=("Bash",)))
    assert asyncio.run(hook({"tool_name": "Read"}, None, None)) == {}


def test_le_hook_refuse_un_appel_interdit_avec_son_motif():
    vu: list[tuple[str, str]] = []
    hook = _hook(
        PolitiqueOutils(deny=("mcp__slack__chat_delete",)),
        lambda outil, raison: vu.append((outil, raison)),
    )

    sortie = asyncio.run(hook({"tool_name": "mcp__slack__chat_delete"}, "tu-1", None))

    decision = sortie["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "mcp__slack__chat_delete" in decision["permissionDecisionReason"]
    # La violation est signalée au canal de traçage de l'exécuteur.
    assert vu == [("mcp__slack__chat_delete", decision["permissionDecisionReason"])]


def test_un_tracage_en_echec_n_empeche_pas_le_refus():
    # L'observation ne casse jamais l'exécution observée : `on_refus` qui lève
    # est avalé, le refus est rendu quand même.
    def _tracage_casse(outil, raison):
        raise RuntimeError("journal indisponible")

    hook = _hook(PolitiqueOutils(deny=("Bash",)), _tracage_casse)

    sortie = asyncio.run(hook({"tool_name": "Bash"}, None, None))

    assert sortie["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_un_appel_sans_nom_d_outil_est_laisse_au_flux_normal():
    hook = _hook(PolitiqueOutils(deny=("Bash",)))
    assert asyncio.run(hook({}, None, None)) == {}


class _FakeTextBlock:
    def __init__(self, text):
        self.text = text


class _FakeAssistantMessage:
    def __init__(self, content):
        self.content = content


def _run_agent_capture_options(monkeypatch, *, politique):
    """Lance `run_agent` sur un `query` factice et capture les options SDK."""
    vu: dict[str, object] = {}

    async def fake_query(*, prompt, options):
        vu["hooks"] = options.hooks
        yield _FakeAssistantMessage([_FakeTextBlock("Livré.")])

    monkeypatch.setattr(claude_mod, "query", fake_query)
    monkeypatch.setattr(claude_mod, "AssistantMessage", _FakeAssistantMessage)
    monkeypatch.setattr(claude_mod, "TextBlock", _FakeTextBlock)
    provider = ClaudeProvider(Credentials())
    asyncio.run(
        provider.run_agent(
            "Fais", model="claude-sonnet-5", workspace=Path("."), tools=("Read",),
            politique=politique,
        )
    )
    return vu


def test_run_agent_arme_le_hook_quand_une_politique_est_fournie(monkeypatch, tmp_path):
    vu = _run_agent_capture_options(monkeypatch, politique=PolitiqueOutils(deny=("Bash",)))
    assert vu["hooks"] is not None
    assert "PreToolUse" in vu["hooks"]


def test_run_agent_sans_politique_n_arme_aucun_hook(monkeypatch, tmp_path):
    vu = _run_agent_capture_options(monkeypatch, politique=None)
    assert vu["hooks"] is None
