"""Tests du notificateur de supervision Slack (ticket #202, module `maestro.supervision`).

Le module #105 était le seul de `maestro/` à **0 %** de couverture : purement
configuratif à sa livraison (pilote du socle MCP #104), ses tests avaient été
différés. Il porte pourtant deux promesses qu'une régression casserait en
silence, puisque *rien ne lève* de ce côté — la supervision n'a pas le droit
d'interrompre ce qu'elle observe :

① **les deux événements** — `fin_de_run` poste le bilan du `RunReport` (tâches
   réussies/échouées/bloquées, usage, `run_id`) ; `validateur_notifiant` poste
   la demande **avant** d'attendre la décision humaine, et rend la décision du
   validateur enveloppé, jamais la sienne ;
② **les refus de `default()`** — canal `MAESTRO_SLACK_CANAL` absent, agent sans
   runtime outillé, aucun serveur MCP déclaré : chacun lève `ConfigError` à la
   **construction**, échec propre avant tout run ;
③ **le contrat best-effort** — un échec de la mission de publication (serveur
   MCP indisponible, token absent, aléa fournisseur) est consigné au journal
   (étapes `notification` / `<tâche>:notification`) **sans** altérer l'issue du
   run ni la décision de validation.

Aucun appel réseau, ni Slack, ni fournisseur réel : le fournisseur est un
double qui enregistre la mission au lieu de la poster, les dépôts MCP et le
coffre vivent dans un répertoire temporaire, et la fixture `_reseau_coupe`
(autouse) fait **échouer** toute tentative d'ouvrir une connexion — le pendant
local du réseau débranché d'office de `tests/conftest.py` (#195).
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import pytest

from maestro.agents import DEVOPS_PROFILE
from maestro.agents.mcp import McpStore
from maestro.agents.runtime import AgentRuntime
from maestro.agents.secrets import SecretStore
from maestro.config import ConfigError, Settings
from maestro.engine.executor import (
    STATUT_BLOQUEE,
    STATUT_ECHEC,
    STATUT_TERMINEE,
    TaskResult,
)
from maestro.engine.guardrails import DemandeValidation
from maestro.engine.loop import RunReport
from maestro.providers.base import McpServerUnavailable, ModelProvider
from maestro.supervision import (
    ETAPE_FIN_DE_RUN,
    SUFFIXE_ETAPE_NOTIFICATION,
    NotificateurRun,
)
from maestro.telemetry import RunJournal
from maestro.telemetry.usage import StepUsage

#: Le canal de supervision des tests (jamais joint : le fournisseur est un double).
CANAL = "#maestro-runs"

#: Valeur servie à `${SLACK_BOT_TOKEN}` : distinctive, pour qu'une fuite dans un
#: journal se voie. Le registre de rédaction (#109) la masquerait de toute façon.
TOKEN = "xoxb-test-202-jamais-envoye"


# --- Doubles & fixtures -----------------------------------------------------------------


#: Hôtes qu'un test peut joindre : la boucle locale seule. La restriction porte sur
#: la **sortie** et non sur toute connexion, parce que la boucle asyncio de Windows
#: (`ProactorEventLoop`) se réveille par un socketpair local — couper `connect` à la
#: racine casserait `asyncio.run` avant d'avoir rien prouvé.
_BOUCLE_LOCALE = frozenset({"127.0.0.1", "::1", "localhost", ""})


@pytest.fixture(autouse=True)
def _reseau_coupe(monkeypatch):
    """Fait échouer toute connexion **sortante** : la supervision est testée hors ligne.

    Le module confie la publication à un agent outillé — c'est *lui* qui parlerait
    à Slack, via son serveur MCP. Rien dans `maestro.supervision` ne doit joindre
    quoi que ce soit, et ce garde-fou le prouve au lieu de le supposer.
    """
    connect = socket.socket.connect
    create_connection = socket.create_connection

    def _verifie(adresse):
        hote = adresse[0] if isinstance(adresse, tuple) else adresse
        if hote not in _BOUCLE_LOCALE:
            raise AssertionError(
                f"les tests de supervision n'ouvrent aucune connexion réseau (#202) : "
                f"sortie vers {hote!r} refusée — aucun appel Slack ni fournisseur réel."
            )

    def _connect(self, adresse, *args, **kwargs):
        _verifie(adresse)
        return connect(self, adresse, *args, **kwargs)

    def _create_connection(adresse, *args, **kwargs):
        _verifie(adresse)
        return create_connection(adresse, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket, "create_connection", _create_connection)


@pytest.fixture(autouse=True)
def _token_slack(monkeypatch):
    """Sert `${SLACK_BOT_TOKEN}`/`${SLACK_TEAM_ID}` comme le ferait un poste configuré."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("SLACK_TEAM_ID", "T0202")


class Publieur(ModelProvider):
    """Agent outillé factice : enregistre la mission de publication au lieu de poster.

    `erreur` simule un aléa de la publication (serveur MCP injoignable, refus
    fournisseur) ; `temoin` reçoit un jalon à chaque appel, pour prouver l'**ordre**
    notification → décision de `validateur_notifiant`.
    """

    name = "publieur-factice"

    def __init__(
        self,
        *,
        resume: str = "posté sur #maestro-runs (ts 1717.000200)",
        erreur: Exception | None = None,
        temoin: list[str] | None = None,
    ) -> None:
        self.appels: list[dict[str, object]] = []
        self._resume = resume
        self._erreur = erreur
        self._temoin = temoin if temoin is not None else []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError(
            "la supervision passe par l'exécution outillée (MCP), jamais par le texte."
        )

    async def run_agent(
        self,
        prompt,
        *,
        model,
        system_prompt=None,
        workspace,
        tools,
        mcp_serveurs=(),
        politique=None,
        on_refus=None,
        plafond_tours=None,
    ):
        self.appels.append(
            {
                "prompt": prompt,
                "system_prompt": system_prompt,
                "mcp_serveurs": tuple(mcp_serveurs),
            }
        )
        self._temoin.append("notification")
        if self._erreur is not None:
            raise self._erreur
        return self._resume

    @property
    def mission(self) -> str:
        """Le prompt de l'unique publication attendue (échoue s'il y en a 0 ou 2)."""
        assert len(self.appels) == 1, f"{len(self.appels)} publication(s), une attendue."
        return str(self.appels[0]["prompt"])


def _declaration_slack() -> dict:
    """La déclaration MCP Slack du devops, calquée sur `core/mcp/devops.json`."""
    return {
        "nom": "slack",
        "type": "stdio",
        "commande": "npx",
        "args": ["-y", "@modelcontextprotocol/server-slack"],
        "env": {"SLACK_BOT_TOKEN": "${SLACK_BOT_TOKEN}", "SLACK_TEAM_ID": "${SLACK_TEAM_ID}"},
    }


def _declare(racine: Path, agent: str, serveurs: list[dict]) -> None:
    """Écrit la déclaration MCP héritée `<agent>.json` sous `racine`."""
    racine.mkdir(parents=True, exist_ok=True)
    (racine / f"{agent}.json").write_text(
        json.dumps({"serveurs": serveurs}, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def store(tmp_path) -> McpStore:
    """Un dépôt MCP temporaire où le devops déclare son serveur Slack."""
    racine = tmp_path / "mcp"
    _declare(racine, "devops", [_declaration_slack()])
    return McpStore(racine)


def _notificateur(store: McpStore, provider: ModelProvider, **kwargs) -> NotificateurRun:
    """Un notificateur câblé sur un vrai `AgentRuntime` et le fournisseur factice."""
    return NotificateurRun(
        AgentRuntime(provider, DEVOPS_PROFILE),
        agent="devops",
        canal=CANAL,
        mcp=store,
        **kwargs,
    )


def _tache(task_id: str, statut: str = STATUT_TERMINEE, **surcharges) -> TaskResult:
    """Un résultat de tâche minimal, dans le statut demandé."""
    champs = dict(
        task_id=task_id,
        titre=f"Tâche {task_id}",
        agent="developpeur",
        role="Développeur",
        competences_requises=(),
        score=1,
        statut=statut,
        sortie="livré" if statut == STATUT_TERMINEE else "",
        usage=StepUsage(appels=1, tokens_entree=10, tokens_sortie=5, cout_usd=0.01),
    )
    champs.update(surcharges)
    return TaskResult(**champs)


def _demande(**surcharges) -> DemandeValidation:
    """Une demande de validation humaine (action sensible classée par les garde-fous)."""
    champs = dict(
        task_id="t2",
        titre="Déployer en production",
        description="Applique la migration puis bascule le trafic.",
        agent="devops",
        role="DevOps",
        raison="mot sensible détecté : « production »",
    )
    champs.update(surcharges)
    return DemandeValidation(**champs)


def _settings(**surcharges) -> Settings:
    """Un `Settings` factice ; seuls les champs lus par `default()` importent ici."""
    defauts = dict(
        anthropic_api_key=None,
        anthropic_model="claude-opus-5",
        claude_auth_mode=None,
        claude_oauth_token=None,
        database_url=None,
        redis_url=None,
    )
    defauts.update(surcharges)
    return Settings(**defauts)


def _seule_ligne(journal: RunJournal):
    """L'unique enregistrement du journal (échoue s'il y en a 0 ou plusieurs)."""
    assert len(journal.records) == 1, f"{len(journal.records)} ligne(s), une attendue."
    return journal.records[0]


# --- ① Fin de run : le bilan est posté, et consigné ---------------------------------------


def test_fin_de_run_poste_le_bilan_du_rapport(store):
    provider = Publieur()
    journal = RunJournal(run_id="run-202")
    report = RunReport(
        objectif="Livrer la Control Tower",
        resultats=(
            _tache("t1"),
            _tache("t2"),
            _tache("t3", STATUT_ECHEC, erreur="timeout"),
            _tache("t4", STATUT_BLOQUEE, erreur="dépend de t3"),
        ),
        run_id="run-202",
    )

    asyncio.run(_notificateur(store, provider).fin_de_run(report, journal))

    mission = provider.mission
    # Le canal et la consigne « telle quelle » encadrent le message posté.
    assert CANAL in mission
    assert "sans reformulation" in mission
    # Le bilan lui-même : objectif, décompte des tâches, usage, run_id.
    assert "Livrer la Control Tower" in mission
    assert "2/4 réussie(s)" in mission
    assert "1 en échec" in mission
    assert "1 bloquée(s)" in mission
    assert report.usage_totale.resume_court() in mission
    assert "run_id : run-202" in mission


def test_fin_de_run_sans_echec_ni_blocage_ni_run_id(store):
    # Les mentions « en échec » / « bloquée(s) » / « run_id » sont conditionnelles :
    # un run nominal poste un bilan sans elles (et un rapport sans run_id existe —
    # `RunReport.run_id` a "" pour défaut).
    provider = Publieur()
    report = RunReport(objectif="Nettoyer le backlog", resultats=(_tache("t1"),))

    asyncio.run(_notificateur(store, provider).fin_de_run(report, RunJournal()))

    mission = provider.mission
    assert "1/1 réussie(s)" in mission
    assert "en échec" not in mission
    assert "bloquée(s)" not in mission
    assert "run_id" not in mission


def test_fin_de_run_consigne_l_etape_notification_au_journal(store):
    provider = Publieur(resume="posté sur #maestro-runs (ts 42)")
    journal = RunJournal(run_id="run-202")

    asyncio.run(
        _notificateur(store, provider).fin_de_run(
            RunReport(objectif="Livrer", resultats=(_tache("t1"),)), journal
        )
    )

    ligne = _seule_ligne(journal)
    assert ligne.etape == ETAPE_FIN_DE_RUN == "notification"
    assert ligne.statut == STATUT_TERMINEE
    assert ligne.agent == "devops"
    assert ligne.role == DEVOPS_PROFILE.role
    assert ligne.erreur is None
    assert ligne.sortie == "posté sur #maestro-runs (ts 42)"
    assert ligne.entree.startswith(":checkered_flag: Run Maestro terminé")
    # Le coût de la supervision est consigné sur son étape propre, durée comprise.
    assert ligne.usage.duree_ms is not None


def test_la_mission_de_supervision_remplace_le_prompt_du_role(store):
    # L'agent équipé n'est pas en train de produire un livrable DevOps : son prompt
    # système est remplacé pour cette exécution (même canal que les playbooks #78).
    provider = Publieur()

    asyncio.run(
        _notificateur(store, provider).fin_de_run(
            RunReport(objectif="Livrer", resultats=()), RunJournal()
        )
    )

    systeme = str(provider.appels[0]["system_prompt"])
    assert systeme != DEVOPS_PROFILE.prompt_systeme
    assert f"Tu es l'agent {DEVOPS_PROFILE.role} de Maestro" in systeme
    assert "UNE seule publication" in systeme


def test_les_serveurs_mcp_sont_relus_a_chaud_a_chaque_notification(store):
    # Comme l'exécuteur à chaque tâche : corriger la déclaration vaut pour la
    # notification suivante, sans reconstruire le notificateur.
    provider = Publieur()
    notificateur = _notificateur(store, provider)
    report = RunReport(objectif="Livrer", resultats=())

    asyncio.run(notificateur.fin_de_run(report, RunJournal()))
    _declare(
        store.racine,
        "devops",
        [_declaration_slack(), {"nom": "teams", "type": "http", "url": "https://exemple.test"}],
    )
    asyncio.run(notificateur.fin_de_run(report, RunJournal()))

    assert [s.nom for s in provider.appels[0]["mcp_serveurs"]] == ["slack"]
    assert [s.nom for s in provider.appels[1]["mcp_serveurs"]] == ["slack", "teams"]


def test_le_token_se_resout_dans_le_coffre_de_l_agent(store, tmp_path, monkeypatch):
    # Coffre par agent provisionné (#109) : le token vient du coffre du notificateur,
    # pas de l'environnement du process — lequel sert une valeur volontairement fausse.
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-environnement-du-process")
    coffre = SecretStore(tmp_path / "coffre")
    for cle, valeur in (("SLACK_BOT_TOKEN", TOKEN), ("SLACK_TEAM_ID", "T0202")):
        coffre.enregistrer("devops", cle, valeur, mode_auth="token_statique")
    provider = Publieur()

    asyncio.run(
        _notificateur(store, provider, secrets=coffre).fin_de_run(
            RunReport(objectif="Livrer", resultats=()), RunJournal()
        )
    )

    (serveur,) = provider.appels[0]["mcp_serveurs"]
    assert serveur.env["SLACK_BOT_TOKEN"] == TOKEN


# --- ② Validation en attente : postée AVANT la décision, qui reste celle du validateur ----


def test_la_notification_precede_l_attente_de_la_decision(store):
    # C'est tout l'intérêt de l'enveloppe : prévenir l'équipe que le run est en
    # pause. Une notification postée *après* la décision n'aurait servi à rien.
    temoin: list[str] = []
    provider = Publieur(temoin=temoin)

    def valideur(demande: DemandeValidation) -> bool:
        temoin.append("decision")
        return True

    valide = _notificateur(store, provider).validateur_notifiant(valideur, RunJournal())
    assert asyncio.run(valide(_demande())) is True
    assert temoin == ["notification", "decision"]


def test_le_message_de_validation_porte_de_quoi_trancher(store):
    provider = Publieur()
    journal = RunJournal()
    valide = _notificateur(store, provider).validateur_notifiant(lambda d: True, journal)

    asyncio.run(valide(_demande()))

    mission = provider.mission
    assert ":raised_hand: Validation humaine en attente" in mission
    assert "Déployer en production (t2)" in mission
    assert "DevOps (`devops`)" in mission
    assert "mot sensible détecté" in mission
    assert "Applique la migration puis bascule le trafic." in mission
    assert "reste en pause tant que personne n'a tranché" in mission

    ligne = _seule_ligne(journal)
    assert ligne.etape == f"t2{SUFFIXE_ETAPE_NOTIFICATION}" == "t2:notification"
    assert "Déployer en production" in ligne.nom
    assert ligne.statut == STATUT_TERMINEE


@pytest.mark.parametrize(
    ("rendu", "attendu"),
    [(True, True), (False, False), (1, True), (0, False), ("", False)],
)
def test_la_decision_reste_celle_du_validateur_enveloppe(store, rendu, attendu):
    # L'enveloppe notifie, elle ne tranche pas : elle rend la décision du validateur
    # (normalisée en booléen, comme l'attend la boucle des garde-fous).
    valide = _notificateur(store, Publieur()).validateur_notifiant(
        lambda d: rendu, RunJournal()
    )

    assert asyncio.run(valide(_demande())) is attendu


def test_un_validateur_asynchrone_est_attendu(store):
    # Le contrat `Validateur` admet les deux formes (console synchrone, Control
    # Tower asynchrone) : l'enveloppe attend le résultat au lieu de rendre la coroutine.
    async def valideur(demande: DemandeValidation) -> bool:
        await asyncio.sleep(0)
        return True

    valide = _notificateur(store, Publieur()).validateur_notifiant(valideur, RunJournal())

    assert asyncio.run(valide(_demande())) is True


# --- ③ Best-effort : l'échec est consigné, il n'altère ni le run ni la décision -----------


def test_un_echec_de_publication_est_consigne_sans_lever(store):
    provider = Publieur(erreur=RuntimeError("serveur MCP slack injoignable"))
    journal = RunJournal(run_id="run-202")

    # Ne lève pas : la supervision n'a pas le droit de casser ce qu'elle observe.
    asyncio.run(
        _notificateur(store, provider).fin_de_run(
            RunReport(objectif="Livrer", resultats=(_tache("t1"),)), journal
        )
    )

    ligne = _seule_ligne(journal)
    assert ligne.etape == ETAPE_FIN_DE_RUN
    assert ligne.statut == STATUT_ECHEC
    assert ligne.erreur == "serveur MCP slack injoignable"
    assert ligne.sortie == ""
    assert ligne.usage.duree_ms is not None


def test_un_token_absent_rend_le_serveur_indisponible_sans_casser_le_run(store, monkeypatch):
    # Cas réel le plus probable : le secret n'est pas servi. `resolus` lève
    # `McpServerUnavailable` avant tout appel modèle — un constat de supervision,
    # pas un échec de run.
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    provider = Publieur()
    journal = RunJournal()

    asyncio.run(
        _notificateur(store, provider).fin_de_run(
            RunReport(objectif="Livrer", resultats=()), journal
        )
    )

    assert provider.appels == []  # rien n'est parti au fournisseur
    ligne = _seule_ligne(journal)
    assert ligne.statut == STATUT_ECHEC
    assert "SLACK_BOT_TOKEN" in (ligne.erreur or "")


def test_sans_serveur_declare_la_notification_echoue_proprement(tmp_path):
    # La déclaration peut disparaître entre la construction et la notification (le
    # dépôt est relu à chaud) : le notificateur le constate au lieu d'appeler le
    # fournisseur sans outil pour poster.
    provider = Publieur()
    journal = RunJournal()

    asyncio.run(
        _notificateur(McpStore(tmp_path / "vide"), provider).fin_de_run(
            RunReport(objectif="Livrer", resultats=()), journal
        )
    )

    assert provider.appels == []
    ligne = _seule_ligne(journal)
    assert ligne.statut == STATUT_ECHEC
    assert "aucun serveur MCP déclaré" in (ligne.erreur or "")


def test_l_echec_de_notification_n_altere_pas_l_issue_du_run(store):
    # Le journal du run garde ses lignes de tâches intactes : la supervision ne fait
    # qu'**ajouter** sa ligne, et n'a aucune prise sur le rapport (qui est déjà agrégé).
    provider = Publieur(erreur=McpServerUnavailable("slack non montable"))
    journal = RunJournal(run_id="run-202")
    journal.consigne(
        etape="t1",
        nom="Tâche 1",
        agent="developpeur",
        role="Développeur",
        statut=STATUT_TERMINEE,
        entree="fais X",
        sortie="fait",
        usage=StepUsage(appels=1, cout_usd=0.02),
    )
    report = RunReport(objectif="Livrer", resultats=(_tache("t1"),), run_id="run-202")

    asyncio.run(_notificateur(store, provider).fin_de_run(report, journal))

    tache, notification = journal.records
    assert (tache.etape, tache.statut, tache.erreur) == ("t1", STATUT_TERMINEE, None)
    assert notification.statut == STATUT_ECHEC
    # Le rapport n'est pas touché : c'est le run qui fait foi, pas sa notification.
    assert report.reussies == report.resultats
    assert report.echouees == ()


def test_l_echec_de_notification_n_altere_pas_la_decision_de_validation(store):
    # Même contrat côté validation : l'équipe n'est pas prévenue, mais le validateur
    # (console, Control Tower) reste seul maître de la décision.
    provider = Publieur(erreur=RuntimeError("canal introuvable"))
    journal = RunJournal()
    valide = _notificateur(store, provider).validateur_notifiant(lambda d: True, journal)

    assert asyncio.run(valide(_demande())) is True

    ligne = _seule_ligne(journal)
    assert ligne.etape == "t2:notification"
    assert ligne.statut == STATUT_ECHEC
    assert ligne.erreur == "canal introuvable"


# --- ④ `default()` : la configuration est validée à la construction ------------------------


@pytest.fixture
def provider_factice(monkeypatch) -> Publieur:
    """Neutralise la fabrique de fournisseur : `default()` ne construit rien de réel."""
    provider = Publieur()
    monkeypatch.setattr(
        "maestro.providers.factory.provider_from_settings", lambda settings=None: provider
    )
    return provider


def test_default_refuse_un_canal_absent(monkeypatch, tmp_path):
    # Le canal est contrôlé **avant** la fabrique de fournisseur : sans canal, il n'y
    # a rien à faire, on ne construit pas un fournisseur pour le découvrir ensuite.
    def _jamais(settings=None):  # pragma: no cover - le test échoue s'il s'exécute
        raise AssertionError("le canal doit être contrôlé avant toute construction.")

    monkeypatch.setattr("maestro.providers.factory.provider_from_settings", _jamais)

    with pytest.raises(ConfigError, match="MAESTRO_SLACK_CANAL"):
        NotificateurRun.default(settings=_settings(slack_canal=None, mcp_dir=str(tmp_path)))


def test_default_refuse_un_agent_sans_runtime_outille(provider_factice, tmp_path):
    settings = _settings(slack_canal=CANAL, mcp_dir=str(tmp_path))

    with pytest.raises(ConfigError, match="runtime outillé"):
        NotificateurRun.default(agent="orchestrateur", settings=settings)


def test_default_refuse_un_agent_sans_serveur_mcp(provider_factice, tmp_path):
    # Sans serveur déclaré, l'agent n'aurait aucun outil pour poster : le refus
    # nomme le fichier attendu plutôt que de laisser le run échouer plus tard.
    settings = _settings(slack_canal=CANAL, mcp_dir=str(tmp_path / "vide"))

    with pytest.raises(ConfigError, match="aucun serveur MCP déclaré"):
        NotificateurRun.default(settings=settings)


def test_default_refuse_une_declaration_mcp_invalide(provider_factice, tmp_path):
    # La `ValueError` de la validation à la lecture est muée en `ConfigError` : pour
    # l'appelant, c'est une erreur de configuration comme les autres.
    racine = tmp_path / "mcp"
    racine.mkdir()
    (racine / "devops.json").write_text("{ pas du json", encoding="utf-8")

    with pytest.raises(ConfigError, match="illisible"):
        NotificateurRun.default(settings=_settings(slack_canal=CANAL, mcp_dir=str(racine)))


def test_default_construit_un_notificateur_qui_poste_sur_le_canal_configure(
    provider_factice, tmp_path
):
    _declare(tmp_path / "mcp", "devops", [_declaration_slack()])
    settings = _settings(
        slack_canal="#supervision",
        mcp_dir=str(tmp_path / "mcp"),
        secrets_dir=str(tmp_path / "coffre"),
    )

    notificateur = NotificateurRun.default(settings=settings)
    asyncio.run(
        notificateur.fin_de_run(RunReport(objectif="Livrer", resultats=()), RunJournal())
    )

    assert "#supervision" in provider_factice.mission


# --- ⑤ Hermétisme : le garde-fou réseau est bien armé --------------------------------------


def test_le_garde_fou_reseau_est_arme():
    # Le méta-test de la fixture `_reseau_coupe` : sans lui, une régression qui
    # rebrancherait un vrai client Slack passerait inaperçue en local. Rien n'est
    # ouvert ici — la sortie est refusée avant l'appel système.
    with pytest.raises(AssertionError, match="aucune connexion réseau"):
        socket.create_connection(("slack.com", 443))
    with pytest.raises(AssertionError, match="aucune connexion réseau"):
        socket.socket().connect(("slack.com", 443))
