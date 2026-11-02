"""Une tâche coupée par la limite d'usage met le run en pause, puis reprend dans sa session (#1543).

Le run `0300f3ffdddf` (2026-10-15, docs/51 § 4.2) s'est soldé en échec, « tâches en
échec », parce que sa dernière tâche avait été coupée par « You've hit your session
limit · resets 12pm » : un libellé qu'aucun des motifs cherchés dans le texte de
l'erreur ne reconnaissait. Le commit conforme était écrit ; il ne restait que la
fusion. Cette suite éprouve, dans l'ordre où la limite les traverse :

① **l'adaptateur** reconnaît la limite à un signal **typé** du flux — jamais au
   texte (docs/44) — et la mue en `LimiteUsageAtteinte`, son heure de reprise
   comprise ;
② **le classement** des causes la nomme par son type, et plus jamais par un motif ;
③ **le runtime** attend la levée puis rouvre **la même session**, contexte intact ;
④ **le rendez-vous** du run : une attente pour toutes les tâches coupées, et la
   meilleure information l'emporte ;
⑤ **la Control Tower** met le run en pause, cause et heure de reprise dites, et le
   reprend au reset ou sur « Reprendre » ;
⑥ **de bout en bout**, sur l'app réelle et un double du fournisseur : un run coupé
   par la limite finit « terminé ».

Ni réseau, ni modèle, ni CLI : le SDK est monkeypatché, le bus est en mémoire.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    RateLimitEvent,
    RateLimitInfo,
    ResultError,
    ResultMessage,
    TextBlock,
)
from fastapi.testclient import TestClient

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.agents.runtime import NOTE_REPRISE_LIMITE
from maestro.controltower import ControlTowerState, Event, InMemoryEventBus, create_app
from maestro.controltower.causes import CAUSE_LIMITE_USAGE, cause_de
from maestro.controltower.events import EVENEMENT_EXECUTION_STATUT
from maestro.controltower.limite import AttenteLimiteControlTower
from maestro.controltower.orchestration import etat_du_run, pause_en_mots
from maestro.controltower.state import (
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    ORDRE_PAUSE,
    ORDRE_REPRISE,
)
from maestro.engine import OrchestrationEngine
from maestro.engine.limite import LimiteDuRun, RendezVousLimite
from maestro.engine.pause import PorteExecution
from maestro.engine.retry import est_transitoire
from maestro.orchestrator import Orchestrator
from maestro.providers import ClaudeProvider
from maestro.providers import claude as claude_mod
from maestro.providers.base import Credentials, LimiteUsageAtteinte, ModelProvider, SessionAgent
from maestro.providers.openai_compat import LimiteEndpoint, OpenAICompatError, _erreur_http

#: Plafond d'attente d'une coroutine de test — jamais atteint quand tout va bien.
DELAI_S = 10.0

#: Ce que le CLI a écrit le 2026-10-15, au mot près.
TEXTE_LIMITE = "You've hit your session limit · resets 12pm"

#: L'instant de reprise que le flux typé porte (epoch, en secondes).
RESET = 1_900_000_000


def _evenement(statut: str, resets_at: int | None = RESET) -> RateLimitEvent:
    return RateLimitEvent(
        rate_limit_info=RateLimitInfo(status=statut, resets_at=resets_at),  # type: ignore[arg-type]
        uuid="u",
        session_id="s",
    )


def _resultat_en_erreur(texte: str = TEXTE_LIMITE) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=0,
        is_error=True,
        num_turns=1,
        session_id="s",
        result=texte,
    )


def _erreur_du_sdk(texte: str = TEXTE_LIMITE, **donnees: object) -> ResultError:
    return ResultError(
        f"Claude Code returned an error result: {texte}",
        data={"subtype": "success", "is_error": True, "result": texte, **donnees},
        exit_code=1,
    )


def _query(*messages: object, leve: BaseException | None = None):
    """Un faux `query` du SDK : rend `messages` dans l'ordre, puis lève `leve` s'il y en a."""

    async def fake_query(*, prompt, options):
        for message in messages:
            yield message
        if leve is not None:
            raise leve

    return fake_query


def _session_coupee():
    """La session du 2026-10-15 telle que le CLI la rend : télémétrie, refus, résultat, sortie."""
    return _query(
        # Chaque session s'ouvre sur la fenêtre en cours — un rapport, pas un refus (#203).
        _evenement("allowed", resets_at=RESET - 3600),
        AssistantMessage(
            content=[TextBlock(text=TEXTE_LIMITE)], model="<synthetic>", error="rate_limit"
        ),
        _evenement("rejected"),
        _resultat_en_erreur(),
        leve=_erreur_du_sdk(),
    )


def _run_agent(tmp_path, **reglages):
    return ClaudeProvider(Credentials()).run_agent(
        "Code ceci", model="claude-sonnet-5", workspace=tmp_path, tools=("Read",), **reglages
    )


# --------------------------------------------------------------------------- #
# ① L'adaptateur : un signal typé, jamais un texte
# --------------------------------------------------------------------------- #


def test_la_limite_de_session_du_cli_devient_une_limite_typee_avec_sa_reprise(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(claude_mod, "query", _session_coupee())

    with pytest.raises(LimiteUsageAtteinte) as excinfo:
        asyncio.run(_run_agent(tmp_path))

    assert excinfo.value.reprise_a == datetime.fromtimestamp(RESET, tz=UTC)
    # Ce que le fournisseur en a dit voyage pour qu'une personne le lise.
    assert TEXTE_LIMITE in str(excinfo.value)


def test_la_limite_se_reconnait_aussi_sans_heure_de_reprise(monkeypatch, tmp_path):
    """Le message d'erreur typé suffit : l'heure manque, la limite reste une limite."""
    monkeypatch.setattr(
        claude_mod,
        "query",
        _query(
            AssistantMessage(
                content=[TextBlock(text="limite")], model="<synthetic>", error="rate_limit"
            ),
            leve=_erreur_du_sdk("limite"),
        ),
    )

    with pytest.raises(LimiteUsageAtteinte) as excinfo:
        asyncio.run(_run_agent(tmp_path))

    assert excinfo.value.reprise_a is None


def test_un_solde_epuise_est_une_limite_sans_reprise(monkeypatch, tmp_path):
    """Un solde ne se relève pas avec le temps : la reprise sera un geste humain."""
    monkeypatch.setattr(
        claude_mod,
        "query",
        _query(
            AssistantMessage(
                content=[TextBlock(text="Credit balance is too low")],
                model="<synthetic>",
                error="billing_error",
            ),
            leve=_erreur_du_sdk("Credit balance is too low"),
        ),
    )

    with pytest.raises(LimiteUsageAtteinte) as excinfo:
        asyncio.run(_run_agent(tmp_path))

    assert excinfo.value.reprise_a is None


def test_un_429_de_l_api_est_une_limite(monkeypatch, tmp_path):
    """Le statut HTTP que le SDK porte en attribut typé — pas le texte qui le cite."""
    monkeypatch.setattr(
        claude_mod,
        "query",
        _query(leve=_erreur_du_sdk("API Error: Request rejected", api_error_status=429)),
    )

    with pytest.raises(LimiteUsageAtteinte):
        asyncio.run(_run_agent(tmp_path))


def test_la_telemetrie_allowed_d_une_session_en_echec_n_est_pas_une_limite(
    monkeypatch, tmp_path
):
    """Le piège de #203 : chaque session s'ouvre sur un `allowed` qui rapporte la fenêtre."""
    erreur = RuntimeError("le CLI s'est arrêté")
    flux = _query(_evenement("allowed"), _evenement("allowed_warning"), leve=erreur)
    monkeypatch.setattr(claude_mod, "query", flux)

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(_run_agent(tmp_path))

    assert not isinstance(excinfo.value, LimiteUsageAtteinte)


def test_un_texte_qui_cite_la_limite_n_en_fait_pas_une(monkeypatch, tmp_path):
    """Lire une panne dans son texte est ce que docs/44 écarte : sans signal, pas de limite."""
    erreur = RuntimeError("Claude AI usage limit reached|1900000000 — rate limit, 429")
    monkeypatch.setattr(claude_mod, "query", _query(leve=erreur))

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(_run_agent(tmp_path))

    assert not isinstance(excinfo.value, LimiteUsageAtteinte)


def test_une_session_qui_aboutit_n_est_jamais_une_limite(monkeypatch, tmp_path):
    """Un refus vu en route ne coupe rien si la session va au bout (#203) : rien à reprendre."""
    monkeypatch.setattr(
        claude_mod,
        "query",
        _query(
            _evenement("rejected"),
            AssistantMessage(content=[TextBlock(text="Fait.")], model="claude-sonnet-5"),
        ),
    )

    assert asyncio.run(_run_agent(tmp_path)) == "Fait."


def test_le_chemin_texte_reconnait_la_meme_limite(monkeypatch):
    monkeypatch.setattr(claude_mod, "query", _session_coupee())

    with pytest.raises(LimiteUsageAtteinte):
        asyncio.run(ClaudeProvider(Credentials()).generate("Salut", model="claude-opus-4-8"))


def test_le_fournisseur_claude_declare_qu_il_rouvre_ses_sessions():
    assert ClaudeProvider.rouvre_les_sessions


# --------------------------------------------------------------------------- #
# ② Le classement : par le type, et plus jamais par le texte
# --------------------------------------------------------------------------- #


def test_la_limite_typee_se_nomme_limite_d_usage():
    """Le pendant négatif — un message ne nomme plus rien — vit avec les autres causes,
    `tests/test_run_activite.py::test_aucune_cause_ne_se_reconnait_plus_au_texte`."""
    assert cause_de(LimiteUsageAtteinte("limite atteinte")) == CAUSE_LIMITE_USAGE


def test_la_limite_ne_se_relance_pas_elle_s_attend():
    assert not est_transitoire(LimiteUsageAtteinte("limite", reprise_a=datetime.now(UTC)))


def _erreur_endpoint(statut: int, **entetes: str) -> httpx2.HTTPStatusError:
    requete = httpx2.Request("POST", "http://localhost:11434/v1/chat/completions")
    reponse = httpx2.Response(statut, headers=entetes, text="quota", request=requete)
    return httpx2.HTTPStatusError("refus", request=requete, response=reponse)


def test_un_endpoint_qui_repond_429_oppose_sa_limite_avec_son_retry_after():
    """L'autre adaptateur du dépôt mue son signal natif — un statut HTTP — de la même façon."""
    avant = datetime.now(UTC)
    erreur = _erreur_http(_erreur_endpoint(429, **{"Retry-After": "120"}), "http://ep", "m")

    assert isinstance(erreur, LimiteUsageAtteinte)
    assert isinstance(erreur, OpenAICompatError)  # qui la rattrapait comme erreur la rattrape
    assert erreur.reprise_a is not None
    assert timedelta(seconds=119) <= erreur.reprise_a - avant <= timedelta(seconds=125)
    assert cause_de(erreur) == CAUSE_LIMITE_USAGE


def test_un_429_sans_retry_after_est_une_limite_sans_heure():
    erreur = _erreur_http(_erreur_endpoint(429), "http://ep", "m")
    assert isinstance(erreur, LimiteEndpoint)
    assert erreur.reprise_a is None


def test_un_autre_statut_reste_une_erreur_de_l_endpoint():
    erreur = _erreur_http(_erreur_endpoint(500), "http://ep", "m")
    assert not isinstance(erreur, LimiteUsageAtteinte)
    assert "500" in str(erreur)


# --------------------------------------------------------------------------- #
# ③ Le runtime : la tâche attend, puis rouvre sa session
# --------------------------------------------------------------------------- #


class _FournisseurCoupe(ModelProvider):
    """Un fournisseur outillé que la limite coupe `coupures` fois, puis qui livre.

    Il rouvre ses sessions comme le CLI (`rouvre_les_sessions`) et la marque ouverte
    au premier appel — sauf `avant_ouverture`, qui coupe avant (la sonde du point de
    contrôle passe la première, et la limite peut tomber sur elle).
    """

    name = "coupe"
    rouvre_les_sessions = True

    def __init__(
        self,
        *,
        coupures: int = 1,
        reprise_a: datetime | None = None,
        avant_ouverture: bool = False,
    ) -> None:
        self._coupures = coupures
        self._reprise_a = reprise_a
        self._avant_ouverture = avant_ouverture
        self.appels: list[dict[str, Any]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None, **_):
        # Le classifieur du routage lui parle aussi : une réponse qu'il ne sait pas
        # lire le laisse router sur les compétences déclarées.
        return ""

    async def run_agent(self, prompt, *, session: SessionAgent | None = None, **reglages):
        self.appels.append(
            {
                "prompt": prompt,
                "session": session.identifiant if session is not None else None,
                "rouverte": session.ouverte if session is not None else None,
                "session_passee": "session" in reglages or session is not None,
            }
        )
        if self._coupures > 0:
            self._coupures -= 1
            if session is not None and not self._avant_ouverture:
                session.ouverte = True
            raise LimiteUsageAtteinte(
                f"le fournisseur a opposé sa limite d'usage : {TEXTE_LIMITE}",
                reprise_a=self._reprise_a,
            )
        if session is not None:
            session.ouverte = True
        return "LIVRABLE — commit conforme écrit"


def _execute(fournisseur: ModelProvider, **reglages):
    return asyncio.run(
        AgentRuntime(fournisseur, DEVELOPER_PROFILE).execute(
            "Vérifier et commiter", tache_id="verifier-et-commiter", **reglages
        )
    )


def test_la_tache_attend_la_levee_puis_reprend_dans_sa_session():
    fournisseur = _FournisseurCoupe()
    attendues: list[LimiteUsageAtteinte] = []

    async def attendre(limite: LimiteUsageAtteinte) -> None:
        attendues.append(limite)

    issue = _execute(fournisseur, on_limite=attendre)

    assert issue.resume == "LIVRABLE — commit conforme écrit"
    assert len(attendues) == 1
    premier, reprise = fournisseur.appels
    # La même conversation, rouverte : l'agent n'y reçoit que la suite.
    assert reprise["session"] == premier["session"] is not None
    assert reprise["rouverte"] is True
    assert reprise["prompt"] == NOTE_REPRISE_LIMITE
    assert "Vérifier et commiter" in premier["prompt"]


def test_une_session_coupee_avant_d_etre_ouverte_repart_du_message_entier():
    fournisseur = _FournisseurCoupe(avant_ouverture=True)

    async def attendre(limite: LimiteUsageAtteinte) -> None:
        return None

    _execute(fournisseur, on_limite=attendre)

    premier, reprise = fournisseur.appels
    assert reprise["session"] == premier["session"]
    assert reprise["prompt"] == premier["prompt"]


def test_une_seconde_coupure_se_rattend_et_reprend_encore_la_meme_session():
    fournisseur = _FournisseurCoupe(coupures=2)
    attentes = 0

    async def attendre(limite: LimiteUsageAtteinte) -> None:
        nonlocal attentes
        attentes += 1

    _execute(fournisseur, on_limite=attendre)

    assert attentes == 2
    assert len({appel["session"] for appel in fournisseur.appels}) == 1
    assert [appel["prompt"] for appel in fournisseur.appels[1:]] == [NOTE_REPRISE_LIMITE] * 2


def test_sans_attente_la_limite_remonte_et_aucune_session_n_est_nommee():
    """Le régime d'avant, au bit près : personne ne tient la pause, rien n'est rouvert."""
    fournisseur = _FournisseurCoupe()

    with pytest.raises(LimiteUsageAtteinte):
        _execute(fournisseur)

    assert fournisseur.appels == [
        {
            "prompt": fournisseur.appels[0]["prompt"],
            "session": None,
            "rouverte": None,
            "session_passee": False,
        }
    ]


def test_une_attente_en_panne_laisse_remonter_la_limite():
    fournisseur = _FournisseurCoupe()

    async def attendre(limite: LimiteUsageAtteinte) -> None:
        raise RuntimeError("le bus s'est refermé")

    with pytest.raises(LimiteUsageAtteinte) as excinfo:
        _execute(fournisseur, on_limite=attendre)

    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert len(fournisseur.appels) == 1


def test_un_fournisseur_qui_ne_rouvre_pas_repart_du_message_entier_sans_session():
    class _SansReouverture(_FournisseurCoupe):
        rouvre_les_sessions = False

    fournisseur = _SansReouverture()

    async def attendre(limite: LimiteUsageAtteinte) -> None:
        return None

    _execute(fournisseur, on_limite=attendre)

    premier, reprise = fournisseur.appels
    assert not premier["session_passee"] and not reprise["session_passee"]
    assert reprise["prompt"] == premier["prompt"]


# --------------------------------------------------------------------------- #
# ④ Le rendez-vous du run : une attente, la meilleure information
# --------------------------------------------------------------------------- #


def _limite(tache: str, reprise_a: datetime | None = None, run: str = "run-1") -> LimiteDuRun:
    return LimiteDuRun(run_id=run, projet_id=None, tache_id=tache, titre=tache, reprise_a=reprise_a)


def test_les_taches_coupees_d_un_run_partagent_une_seule_attente():
    async def scenario() -> tuple[list[LimiteDuRun], list[str]]:
        ouvertes: list[LimiteDuRun] = []
        leve = asyncio.Event()

        async def attente(limite: LimiteDuRun) -> None:
            ouvertes.append(limite)
            await leve.wait()

        rdv = RendezVousLimite(attente)
        reveils: list[str] = []

        async def tache(nom: str) -> None:
            await rdv.attendre(_limite(nom))
            reveils.append(nom)

        taches = [asyncio.create_task(tache(nom)) for nom in ("a", "b", "c")]
        for _ in range(5):
            await asyncio.sleep(0)
        assert reveils == []
        leve.set()
        await asyncio.wait_for(asyncio.gather(*taches), timeout=DELAI_S)
        return ouvertes, reveils

    ouvertes, reveils = asyncio.run(scenario())

    assert [limite.tache_id for limite in ouvertes] == ["a"]
    assert sorted(reveils) == ["a", "b", "c"]


def test_une_tache_mieux_informee_remplace_l_attente_aveugle():
    """La règle de #291 : une heure connue l'emporte sur une attente qui n'en a pas."""
    heure = datetime(2026, 10, 15, 12, tzinfo=UTC)

    async def scenario() -> list[LimiteDuRun]:
        ouvertes: list[LimiteDuRun] = []
        leve = asyncio.Event()

        async def attente(limite: LimiteDuRun) -> None:
            ouvertes.append(limite)
            await leve.wait()

        rdv = RendezVousLimite(attente)
        aveugle = asyncio.create_task(rdv.attendre(_limite("a")))
        await asyncio.sleep(0)
        informee = asyncio.create_task(rdv.attendre(_limite("b", heure)))
        await asyncio.sleep(0)
        # À information égale, ou moindre, on ne fait que rejoindre.
        rejoint = asyncio.create_task(rdv.attendre(_limite("c")))
        for _ in range(5):
            await asyncio.sleep(0)
        leve.set()
        await asyncio.wait_for(asyncio.gather(aveugle, informee, rejoint), timeout=DELAI_S)
        return ouvertes

    ouvertes = asyncio.run(scenario())

    assert [(limite.tache_id, limite.reprise_a) for limite in ouvertes] == [
        ("a", None),
        # Remplacée, l'attente nomme toujours la tâche qui l'a ouverte.
        ("a", heure),
    ]


def test_une_attente_qui_leve_fait_lever_toutes_les_taches():
    async def scenario() -> list[BaseException | None]:
        async def attente(limite: LimiteDuRun) -> None:
            await asyncio.sleep(0)
            raise RuntimeError("bus refermé")

        rdv = RendezVousLimite(attente)
        issues = await asyncio.gather(
            rdv.attendre(_limite("a")), rdv.attendre(_limite("b")), return_exceptions=True
        )
        return list(issues)

    issues = asyncio.run(scenario())
    assert all(isinstance(issue, RuntimeError) for issue in issues)


def test_une_limite_d_un_run_ne_suspend_pas_un_autre_run():
    async def scenario() -> list[str]:
        ouvertes: list[str] = []

        async def attente(limite: LimiteDuRun) -> None:
            ouvertes.append(limite.run_id)

        rdv = RendezVousLimite(attente)
        await asyncio.gather(
            rdv.attendre(_limite("a", run="run-1")), rdv.attendre(_limite("a", run="run-2"))
        )
        return ouvertes

    assert sorted(asyncio.run(scenario())) == ["run-1", "run-2"]


# --------------------------------------------------------------------------- #
# ⑤ La Control Tower : la pause dite, la reprise au reset ou sur geste
# --------------------------------------------------------------------------- #


async def _laisse_s_abonner() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def _ordre(run_id: str, statut: str) -> Event:
    """Un `execution.statut` nu — l'ordre de reprise d'un geste, ou un statut de run."""
    return Event(type=EVENEMENT_EXECUTION_STATUT, run_id=run_id, statut=statut)


def _ordres(vus: list[Event]) -> list[tuple[str, str, str]]:
    return [
        (event.statut, event.cause, event.echeance)
        for event in vus
        if event.type == EVENEMENT_EXECUTION_STATUT
    ]


async def _ecouter(bus: InMemoryEventBus, vus: list[Event]) -> None:
    async for event in bus.subscribe():
        vus.append(event)


def test_au_reset_le_run_reprend_de_lui_meme():
    async def scenario() -> tuple[list[Event], PorteExecution]:
        bus = InMemoryEventBus()
        vus: list[Event] = []
        temoin = asyncio.create_task(_ecouter(bus, vus))
        await _laisse_s_abonner()
        porte = PorteExecution()
        reprise = datetime.now(UTC) + timedelta(seconds=0.2)
        attente = AttenteLimiteControlTower(bus, porte=porte)
        await asyncio.wait_for(attente(_limite("t", reprise)), timeout=DELAI_S)
        await _laisse_s_abonner()
        temoin.cancel()
        return vus, porte

    vus, porte = asyncio.run(scenario())

    (pause, cause_pause, echeance), (reprise, cause_reprise, _) = _ordres(vus)
    assert (pause, cause_pause) == (ORDRE_PAUSE, CAUSE_LIMITE_USAGE)
    assert datetime.fromisoformat(echeance) > datetime(2026, 1, 1, tzinfo=UTC)
    assert (reprise, cause_reprise) == (ORDRE_REPRISE, CAUSE_LIMITE_USAGE)
    assert porte.ouverte


def test_sans_heure_le_run_attend_reprendre():
    async def scenario() -> tuple[list[Event], bool]:
        bus = InMemoryEventBus()
        vus: list[Event] = []
        temoin = asyncio.create_task(_ecouter(bus, vus))
        await _laisse_s_abonner()
        porte = PorteExecution()
        attente = asyncio.create_task(AttenteLimiteControlTower(bus, porte=porte)(_limite("t")))
        await asyncio.sleep(0.3)
        en_pause = not attente.done() and not porte.ouverte
        # « Reprendre » à l'écran : l'ordre que publie `ServiceExecutions.reprendre`.
        await bus.publish(_ordre("run-1", ORDRE_REPRISE))
        await asyncio.wait_for(attente, timeout=DELAI_S)
        temoin.cancel()
        return vus, en_pause

    vus, en_pause = asyncio.run(scenario())

    assert en_pause
    assert _ordres(vus)[0] == (ORDRE_PAUSE, CAUSE_LIMITE_USAGE, "")


def test_une_heure_deja_passee_n_est_pas_une_heure_de_reprise():
    """Repartir aussitôt retomberait sur la même limite, sans fin : on attend « Reprendre »."""

    async def scenario() -> bool:
        bus = InMemoryEventBus()
        passee = datetime.now(UTC) - timedelta(minutes=5)
        attente = asyncio.create_task(AttenteLimiteControlTower(bus)(_limite("t", passee)))
        await asyncio.sleep(0.2)
        attend = not attente.done()
        attente.cancel()
        return attend

    assert asyncio.run(scenario())


def test_au_reset_la_pause_d_une_personne_n_est_pas_levee():
    async def scenario() -> tuple[list[Event], PorteExecution]:
        bus = InMemoryEventBus()
        vus: list[Event] = []
        temoin = asyncio.create_task(_ecouter(bus, vus))
        await _laisse_s_abonner()
        porte = PorteExecution()
        porte.fermer()  # la personne avait mis le run en pause avant que la limite tombe
        reprise = datetime.now(UTC) + timedelta(seconds=0.2)
        await asyncio.wait_for(
            AttenteLimiteControlTower(bus, porte=porte)(_limite("t", reprise)), timeout=DELAI_S
        )
        await _laisse_s_abonner()
        temoin.cancel()
        return vus, porte

    vus, porte = asyncio.run(scenario())

    statuts = [statut for statut, _, _ in _ordres(vus)]
    assert ORDRE_REPRISE not in statuts
    assert _ordres(vus)[-1][:2] == (ORDRE_PAUSE, "")  # la pause redevient la sienne
    assert not porte.ouverte


def test_une_pause_humaine_levee_ne_retient_pas_la_limite_suivante():
    """La pause de la personne vaut pour la limite qu'elle a vue, pas pour la suivante."""

    async def scenario() -> list[Event]:
        bus = InMemoryEventBus()
        porte = PorteExecution()
        attente = AttenteLimiteControlTower(bus, porte=porte)
        porte.fermer()  # suspendu par la personne quand la première limite tombe
        bientot = datetime.now(UTC) + timedelta(seconds=0.1)
        await asyncio.wait_for(attente(_limite("a", bientot)), timeout=DELAI_S)
        porte.ouvrir()  # « Reprendre », plus tard
        vus: list[Event] = []
        temoin = asyncio.create_task(_ecouter(bus, vus))
        await _laisse_s_abonner()
        bientot = datetime.now(UTC) + timedelta(seconds=0.1)
        await asyncio.wait_for(attente(_limite("b", bientot)), timeout=DELAI_S)
        await _laisse_s_abonner()
        temoin.cancel()
        return vus

    vus = asyncio.run(scenario())
    assert [statut for statut, _, _ in _ordres(vus)] == [ORDRE_PAUSE, ORDRE_REPRISE]


def test_si_le_bus_se_referme_l_attente_leve():
    class _BusQuiSeTait(InMemoryEventBus):
        def subscribe(self):
            async def vide():
                return
                yield

            return vide()

    with pytest.raises(RuntimeError, match="refermé"):
        asyncio.run(AttenteLimiteControlTower(_BusQuiSeTait())(_limite("t")))


def test_la_projection_dit_la_cause_et_l_heure_de_la_pause_puis_les_efface():
    from maestro.controltower.limite import evenement_pause

    state = ControlTowerState()
    state.appliquer(_ordre("run-1", EXECUTION_EN_COURS))
    heure = datetime(2026, 10, 15, 12, tzinfo=UTC)
    state.appliquer(evenement_pause(_limite("verifier-et-commiter", heure)))

    resume = state.execution("run-1").resume()
    assert resume["en_pause"] and resume["statut"] == EXECUTION_EN_COURS
    assert resume["pause_cause"] == CAUSE_LIMITE_USAGE
    assert resume["reprise_a"] == heure.isoformat()
    assert resume["cause"] == ""  # un run en pause n'est pas un run soldé
    # Le fil le dit, avec l'heure du poste.
    assert "limite d'usage du fournisseur, reprise prévue à" in etat_du_run(resume)

    state.appliquer(_ordre("run-1", ORDRE_REPRISE))
    resume = state.execution("run-1").resume()
    assert not resume["en_pause"]
    assert (resume["pause_cause"], resume["reprise_a"]) == ("", None)


def test_une_pause_humaine_se_dit_comme_avant():
    assert pause_en_mots(None, "", None) == "en pause"
    assert pause_en_mots(None, CAUSE_LIMITE_USAGE, None).endswith("reprise sur « Reprendre »")


# --------------------------------------------------------------------------- #
# ⑥ De bout en bout : un run coupé par la limite finit « terminé »
# --------------------------------------------------------------------------- #


class _Planificateur(ModelProvider):
    name = "planificateur"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None, **_):
        return json.dumps(
            [
                {
                    "id": "verifier-et-commiter",
                    "titre": "Vérifier et commiter",
                    "description": "Jouer le filet, puis commiter le correctif conforme.",
                    "competences_requises": ["tests"],
                    "format_sortie": "Compte rendu",
                    "dependances": [],
                }
            ]
        )


def _app_reelle(fournisseur: _FournisseurCoupe) -> TestClient:
    """L'app réelle et un vrai moteur, armés comme la production — attente de la limite comprise."""

    def fabrique(**reglages: Any) -> OrchestrationEngine:
        return OrchestrationEngine(
            fournisseur,
            Orchestrator(_Planificateur(), model="claude-opus-5-5"),
            guardrails=reglages["guardrails"],
            max_parallele=reglages["max_parallele"],
            attente_limite=reglages["attente_limite"],
        )

    return TestClient(
        create_app(bus=InMemoryEventBus(), state=ControlTowerState(), fabrique_moteur=fabrique)
    )


def _attendre(client: TestClient, run_id: str, condition) -> dict:
    limite = time.monotonic() + DELAI_S
    while True:
        resume = client.get(f"/api/executions/{run_id}").json()
        if condition(resume):
            return resume
        if time.monotonic() > limite:  # pragma: no cover - filet anti-blocage
            pytest.fail(f"run {run_id} resté en {resume}")
        time.sleep(0.02)


LANCEMENT = {"objectif": "Corriger le calcul et le commiter", "brief": "sans"}


def test_de_bout_en_bout_un_run_coupe_par_la_limite_reprend_au_reset_et_finit_termine():
    fournisseur = _FournisseurCoupe(reprise_a=datetime.now(UTC) + timedelta(seconds=1.5))
    with _app_reelle(fournisseur) as client:
        run_id = client.post("/api/executions", json=LANCEMENT).json()["run_id"]

        pause = _attendre(client, run_id, lambda r: r["en_pause"])
        assert pause["statut"] == EXECUTION_EN_COURS
        assert pause["pause_cause"] == CAUSE_LIMITE_USAGE
        assert pause["reprise_a"] is not None

        fin = _attendre(client, run_id, lambda r: r["statut"] != EXECUTION_EN_COURS)

    assert fin["statut"] == EXECUTION_TERMINEE
    assert (fin["cause"], fin["en_pause"], fin["pause_cause"]) == ("", False, "")
    premier, reprise = fournisseur.appels
    assert reprise["session"] == premier["session"] and reprise["prompt"] == NOTE_REPRISE_LIMITE


def test_de_bout_en_bout_sans_heure_reprendre_relance_la_tache_dans_sa_session():
    fournisseur = _FournisseurCoupe()  # un solde épuisé : le fournisseur ne dit pas quand
    with _app_reelle(fournisseur) as client:
        run_id = client.post("/api/executions", json=LANCEMENT).json()["run_id"]
        pause = _attendre(client, run_id, lambda r: r["en_pause"])
        assert (pause["pause_cause"], pause["reprise_a"]) == (CAUSE_LIMITE_USAGE, None)
        assert len(fournisseur.appels) == 1  # rien ne repart tout seul

        assert client.post(f"/api/executions/{run_id}/reprendre").status_code == 200
        fin = _attendre(client, run_id, lambda r: r["statut"] != EXECUTION_EN_COURS)

    assert fin["statut"] == EXECUTION_TERMINEE
    premier, reprise = fournisseur.appels
    assert reprise["session"] == premier["session"] and reprise["prompt"] == NOTE_REPRISE_LIMITE
