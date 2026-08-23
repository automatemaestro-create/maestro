"""Le stderr du CLI d'un fournisseur suit l'échec jusqu'au journal (ticket #346).

Avant ce ticket, une tentative plantée ne laissait dans l'événement d'activité et
dans le journal que ceci, et rien d'autre :

    Fatal error in message reader: Command failed with exit code 1 (exit code: 1)
    Error output: Check stderr output for details

« Check stderr output for details » désignait un flux que **personne n'écoutait** :
`maestro/providers/claude.py` construisait ses `ClaudeAgentOptions` sans passer
`stderr=`, donc le SDK n'avait rien à joindre à son exception. La tâche était
relancée, rééchouait de la même façon, et l'incident se soldait sans qu'on sache
s'il s'agissait d'une limite d'usage, d'un plantage du CLI ou d'une erreur de
l'agent (constat du 2026-08-14, run `4b33ea332e60` : 42 min d'agent pour zéro
information).

Trois choses sont couvertes ici, dans l'ordre des critères d'acceptation :

① les **deux** constructions de `ClaudeAgentOptions` (chemin texte `generate`,
   chemin outillé `run_agent`) branchent un collecteur, et ce collecteur est
   **borné** — un stderr volumineux ne noie pas le journal ;
② un échec repart avec ce que le CLI a écrit, **ou** avec la mention explicite
   qu'il s'est tu — sans que le type ni le message de l'exception changent, dont
   dépendent la classification transitoire (#91) et les erreurs typées (#239) ;
③ **le chemin complet**, joué de bout en bout : un CLI qui sort en erreur en ayant
   écrit sur stderr laisse sa raison dans le journal du run — étape `:relance` et
   échec final —, donc dans l'événement que la Control Tower rediffuse.

Aucun appel réseau, aucun sous-processus : le SDK est monkeypatché, et le « CLI »
est une fonction qui écrit sur le rappel `stderr` avant de mourir.
"""

import asyncio
import json
from pathlib import Path

import pytest

from maestro.engine import STATUT_ECHEC, OrchestrationEngine, PolitiqueRelance
from maestro.engine.executor import SUFFIXE_ETAPE_RELANCE
from maestro.engine.retry import est_transitoire
from maestro.orchestrator import Orchestrator
from maestro.providers import ClaudeProvider
from maestro.providers import claude as claude_mod
from maestro.providers.base import (
    MENTION_STDERR_VIDE,
    STDERR_LIGNE_MAX,
    STDERR_LIGNES_MAX,
    CollecteurStderr,
    Credentials,
    ModelProvider,
    TurnLimitReached,
    stderr_de,
)
from maestro.telemetry import RunJournal

#: Le message que le SDK remonte quand son sous-processus meurt — celui du ticket.
MESSAGE_SDK = "Fatal error in message reader: Command failed with exit code 1 (exit code: 1)"

#: Ce qu'un CLI Claude à bout de souffle écrit vraiment sur stderr.
STDERR_CLI = (
    "Error: Claude Code process exited with code 1",
    "API Error: 400 prompt is too long: 214531 tokens > 200000 maximum",
)


def _query_qui_meurt(lignes=STDERR_CLI, *, message=MESSAGE_SDK):
    """Un faux `query` du SDK : écrit `lignes` sur le rappel stderr, puis meurt.

    C'est exactement la séquence du ticket — le CLI parle sur stderr *avant* que
    le SDK relève l'échec, et sans rappel branché ces lignes n'existent nulle part.
    """

    async def fake_query(*, prompt, options):
        for ligne in lignes:
            options.stderr(ligne)
        raise RuntimeError(message)
        yield  # jamais atteint : fait de fake_query un générateur asynchrone

    return fake_query


# --- Critère ① : un collecteur borné, branché sur les DEUX constructions d'options -----


def test_le_collecteur_garde_les_dernieres_lignes_et_tronque_les_trop_longues():
    # Garde-fou du ticket : le stderr d'un CLI peut être volumineux. On borne des
    # deux côtés — nombre de lignes retenues, longueur d'une ligne.
    collecteur = CollecteurStderr(lignes_max=3, ligne_max=10)
    for i in range(10):
        collecteur(f"ligne {i}")
    collecteur("x" * 400)

    # Ce sont les DERNIÈRES lignes qui portent la cause immédiate.
    assert collecteur.lignes == ("ligne 8", "ligne 9", "xxxxxxxxxx […]")
    resume = collecteur.resume()
    assert "ligne 0" not in resume
    # Et le résumé dit combien il en a laissé derrière lui plutôt que de se taire.
    assert "8 antérieure(s) omise(s)" in resume


def test_le_collecteur_ignore_les_lignes_vides():
    collecteur = CollecteurStderr()
    collecteur("\n")
    collecteur("   ")
    collecteur("")
    assert collecteur.lignes == ()
    # Les bornes par défaut sont celles du dépôt, pas des nombres inventés ici.
    assert STDERR_LIGNES_MAX > 0
    assert STDERR_LIGNE_MAX > 0


def test_le_collecteur_dit_explicitement_que_le_cli_n_a_rien_ecrit():
    # L'autre moitié du ticket : « pas de stderr » et « stderr jamais capturé » se
    # ressemblaient à la lecture, et un seul des deux se répare.
    assert CollecteurStderr().resume() == MENTION_STDERR_VIDE


def test_generate_branche_un_collecteur_sur_les_options_du_sdk(monkeypatch):
    # Critère ① — première construction de ClaudeAgentOptions (chemin texte).
    vu: dict[str, object] = {}

    async def fake_query(*, prompt, options):
        vu["stderr"] = options.stderr
        options.stderr("une ligne du CLI")
        return
        yield  # jamais atteint : fait de fake_query un générateur asynchrone

    monkeypatch.setattr(claude_mod, "query", fake_query)
    asyncio.run(ClaudeProvider(Credentials()).generate("Salut", model="claude-opus-4-8"))

    collecteur = vu["stderr"]
    assert isinstance(collecteur, CollecteurStderr)
    assert collecteur.lignes == ("une ligne du CLI",)


def test_run_agent_branche_un_collecteur_sur_les_options_du_sdk(monkeypatch, tmp_path):
    # Critère ① — seconde construction de ClaudeAgentOptions (chemin outillé).
    vu: dict[str, object] = {}

    async def fake_query(*, prompt, options):
        vu["stderr"] = options.stderr
        options.stderr("une ligne du CLI outillé")
        return
        yield  # jamais atteint : fait de fake_query un générateur asynchrone

    monkeypatch.setattr(claude_mod, "query", fake_query)
    asyncio.run(
        ClaudeProvider(Credentials()).run_agent(
            "Code ceci", model="claude-sonnet-5", workspace=tmp_path, tools=("Read",)
        )
    )

    collecteur = vu["stderr"]
    assert isinstance(collecteur, CollecteurStderr)
    assert collecteur.lignes == ("une ligne du CLI outillé",)


# --- Critère ② : l'échec repart avec sa raison, sans changer de nature -----------------


def test_un_echec_du_cli_repart_avec_ce_qu_il_a_ecrit(monkeypatch):
    monkeypatch.setattr(claude_mod, "query", _query_qui_meurt())

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(ClaudeProvider(Credentials()).generate("Salut", model="claude-opus-4-8"))

    resume = stderr_de(excinfo.value)
    assert resume is not None
    assert "prompt is too long" in resume
    assert "exited with code 1" in resume


def test_un_cli_muet_le_dit_au_lieu_de_renvoyer_a_un_flux_vide(monkeypatch):
    monkeypatch.setattr(claude_mod, "query", _query_qui_meurt(lignes=()))

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(ClaudeProvider(Credentials()).generate("Salut", model="claude-opus-4-8"))

    assert stderr_de(excinfo.value) == MENTION_STDERR_VIDE


def test_le_stderr_voyage_sans_changer_le_type_ni_le_message_de_l_erreur(monkeypatch):
    # Le stderr est un **attribut**, jamais un message enrichi : la classification
    # transitoire (#91) et les erreurs typées de la frontière (#239) lisent le type
    # et le texte — les toucher ferait changer un échec de nature en chemin.
    monkeypatch.setattr(claude_mod, "query", _query_qui_meurt())
    with pytest.raises(RuntimeError) as quelconque:
        asyncio.run(ClaudeProvider(Credentials()).generate("Salut", model="claude-opus-4-8"))
    assert str(quelconque.value) == MESSAGE_SDK
    assert est_transitoire(quelconque.value)

    monkeypatch.setattr(
        claude_mod,
        "query",
        _query_qui_meurt(message="Claude Code returned an error result: error_max_turns"),
    )
    with pytest.raises(TurnLimitReached) as plafond:
        asyncio.run(
            ClaudeProvider(Credentials()).run_agent(
                "Fais",
                model="claude-sonnet-5",
                workspace=Path("."),
                tools=("Read",),
                plafond_tours=15,
            )
        )
    # Erreur typée, borne nommée, jamais relancée — et le stderr est là aussi.
    assert "plafond de tours atteint (15 tours)" in str(plafond.value)
    assert not est_transitoire(plafond.value)
    assert "prompt is too long" in (stderr_de(plafond.value) or "")


def test_un_fournisseur_sans_cli_ne_fabrique_aucune_mention():
    # `None` ≠ `MENTION_STDERR_VIDE` : un échec qui ne vient pas d'un sous-processus
    # ne doit pas se voir coller une ligne sur un CLI qui n'existe pas.
    assert stderr_de(RuntimeError("coupure réseau")) is None


# --- Critère ③ : le chemin complet — la raison arrive dans le journal du run -----------


class ProviderPlanFixe(ModelProvider):
    """Planificateur factice : rend toujours le même plan JSON."""

    name = "plan-fixe"

    def __init__(self, plan: str) -> None:
        self._plan = plan

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._plan


class ClaudeSurCliFactice(ClaudeProvider):
    """Le **vrai** fournisseur Claude, au-dessus d'un SDK monkeypatché.

    Rien n'est simulé du côté qu'on veut éprouver : options réelles, collecteur
    réel, remontée réelle de l'échec. Seul `supports` est élargi, le catalogue
    d'agents du moteur n'ayant pas à être aligné sur ce test.
    """

    def supports(self, model: str) -> bool:
        return True


def _moteur_sur_cli_qui_meurt(*, tentatives: int) -> tuple[OrchestrationEngine, RunJournal]:
    plan = json.dumps(
        [
            {
                "id": "t1",
                "titre": "Cœur métier et persistance",
                "description": "Réaliser la tâche.",
                "competences_requises": ["backend"],
                "format_sortie": "Texte",
                "dependances": [],
            }
        ],
        ensure_ascii=False,
    )
    orchestrator = Orchestrator(ProviderPlanFixe(plan), model="claude-opus-4-8")
    engine = OrchestrationEngine(
        ClaudeSurCliFactice(Credentials()),
        orchestrator,
        # Aucun runtime outillé : la tâche passe par le chemin texte du
        # fournisseur, sans espace de travail à monter.
        runtimes={},
        relance=PolitiqueRelance(max_tentatives=tentatives, backoff_s=0),
    )
    return engine, RunJournal()


def test_un_cli_mort_en_ayant_ecrit_laisse_sa_raison_dans_le_journal(monkeypatch):
    """Le critère ③, joué de bout en bout : plan → tâche → CLI qui meurt → journal."""
    monkeypatch.setattr(claude_mod, "query", _query_qui_meurt())
    engine, journal = _moteur_sur_cli_qui_meurt(tentatives=2)

    report = asyncio.run(engine.run("Objectif", journal=journal))

    (resultat,) = report.resultats
    assert resultat.statut == STATUT_ECHEC

    # L'étape de relance — celle que le pont Control Tower mue en activité d'agent,
    # dont le `detail` est la `sortie` — porte la raison, pas seulement le geste.
    (relance,) = [r for r in journal.records if r.etape.endswith(SUFFIXE_ETAPE_RELANCE)]
    assert "prompt is too long" in relance.sortie
    assert "prompt is too long" in relance.entree
    assert "tentative 1/2" in relance.sortie

    # L'échec final aussi : c'est lui qui reste quand le run est fini.
    assert "prompt is too long" in (resultat.erreur or "")
    assert "relances épuisées" in (resultat.erreur or "")
    # Ce que le ticket appelait « le message inutile » est toujours là — il n'est
    # simplement plus tout seul.
    assert MESSAGE_SDK in (resultat.erreur or "")


def test_un_cli_mort_en_silence_le_dit_dans_le_journal(monkeypatch):
    # Deuxième moitié du critère ② vue depuis le journal : l'étape ne renvoie plus
    # à un flux vide, elle dit que le CLI n'a rien écrit.
    monkeypatch.setattr(claude_mod, "query", _query_qui_meurt(lignes=()))
    engine, journal = _moteur_sur_cli_qui_meurt(tentatives=2)

    report = asyncio.run(engine.run("Objectif", journal=journal))

    (resultat,) = report.resultats
    assert resultat.statut == STATUT_ECHEC
    (relance,) = [r for r in journal.records if r.etape.endswith(SUFFIXE_ETAPE_RELANCE)]
    assert MENTION_STDERR_VIDE in relance.sortie
    assert MENTION_STDERR_VIDE in (resultat.erreur or "")


def test_le_journal_ne_recopie_jamais_l_environnement_du_cli(monkeypatch):
    # Garde-fou du ticket : `options.env` porte les credentials. Seul ce que le CLI
    # écrit lui-même passe — l'environnement n'entre jamais dans le journal.
    monkeypatch.setattr(claude_mod, "query", _query_qui_meurt())
    engine, journal = _moteur_sur_cli_qui_meurt(tentatives=1)

    asyncio.run(
        engine.run(
            "Objectif",
            journal=journal,
        )
    )

    trace = json.dumps([r.to_dict() for r in journal.records], ensure_ascii=False)
    assert "ANTHROPIC_API_KEY" not in trace
    assert "ANTHROPIC_AUTH_TOKEN" not in trace
