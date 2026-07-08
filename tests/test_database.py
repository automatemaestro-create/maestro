"""Tests du runtime de l'agent Base de données (ticket #5).

Aucun appel réseau : l'exécution agentique est pilotée par un `ModelProvider` factice
qui écrit *réellement* des fichiers dans l'espace de travail fourni. Couvre le critère
du ticket :
① l'agent traite une tâche BDD de bout en bout et **produit un résultat exploitable**
   (schéma, migrations, requêtes capturés dans le livrable) ;
② l'exécution a lieu dans un **contexte isolé** (un répertoire dédié transmis à l'agent).
Plus : garde-fou BDD porté par le prompt système, capacité optionnelle refusée proprement,
validation d'entrée, sérialisation.
"""

import asyncio
from pathlib import Path

import pytest

from maestro.agents.database import DatabaseAgent, DatabaseOutcome
from maestro.providers.base import ModelProvider, UnsupportedCapability
from maestro.sandbox import ProducedFile


class WritingProvider(ModelProvider):
    """Fournisseur factice outillé : écrit des fichiers dans le workspace et enregistre l'appel."""

    name = "writing"

    def __init__(self, files: dict[str, str], resume: str = "Fait.") -> None:
        self._files = files
        self._resume = resume
        self.calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("le BDD doit passer par run_agent, pas generate")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools):
        self.calls.append(
            {
                "prompt": prompt,
                "model": model,
                "system_prompt": system_prompt,
                "workspace": workspace,
                "tools": tuple(tools),
            }
        )
        for chemin, contenu in self._files.items():
            cible = Path(workspace) / chemin
            cible.parent.mkdir(parents=True, exist_ok=True)
            cible.write_text(contenu, encoding="utf-8")
        return self._resume


class TextOnlyProvider(ModelProvider):
    """Fournisseur *sans* exécution outillée : n'implémente que `generate`."""

    name = "text-only"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return "texte"


# --- Critère ① : traite une tâche BDD et produit un résultat exploitable ---------------


def test_execute_capture_le_livrable_bdd():
    provider = WritingProvider(
        files={
            "schema.sql": "CREATE TABLE client (id INTEGER PRIMARY KEY);",
            "migrations/001_init.sql": "CREATE TABLE client (id INTEGER PRIMARY KEY);",
        },
        resume="J'ai créé le schéma et la migration initiale.",
    )
    agent = DatabaseAgent(provider)

    outcome = asyncio.run(agent.execute("Conçois un schéma clients"))

    assert isinstance(outcome, DatabaseOutcome)
    assert outcome.a_produit
    assert outcome.resume == "J'ai créé le schéma et la migration initiale."
    par_chemin = {f.chemin: f.contenu for f in outcome.fichiers}
    assert par_chemin == {
        "schema.sql": "CREATE TABLE client (id INTEGER PRIMARY KEY);",
        "migrations/001_init.sql": "CREATE TABLE client (id INTEGER PRIMARY KEY);",
    }


def test_execute_sans_fichier_marque_a_produit_faux():
    agent = DatabaseAgent(WritingProvider(files={}, resume="Rien à faire."))
    outcome = asyncio.run(agent.execute("Ne rien produire"))
    assert not outcome.a_produit
    assert outcome.fichiers == ()


# --- Critère ② : contexte d'exécution isolé -------------------------------------------


def test_execute_fournit_un_workspace_isole_a_l_agent():
    provider = WritingProvider(files={"schema.sql": "-- vide"})
    agent = DatabaseAgent(provider)

    outcome = asyncio.run(agent.execute("Tâche"))

    (call,) = provider.calls
    workspace = call["workspace"]
    # L'agent a reçu un répertoire dédié, distinct du cwd du test.
    assert Path(workspace) != Path.cwd()
    assert str(workspace) == outcome.workspace
    # Hors keep_workspace, l'espace est nettoyé après capture.
    assert not Path(workspace).exists()


def test_execute_transmet_le_modele_le_prompt_systeme_et_les_outils():
    provider = WritingProvider(files={"schema.sql": "-- vide"})
    agent = DatabaseAgent(provider)

    asyncio.run(agent.execute("Tâche", format_sortie="Un fichier de migration SQL"))

    (call,) = provider.calls
    assert call["model"] == "claude-sonnet-5"
    assert "Bash" in call["tools"] and "Write" in call["tools"]
    assert "Base de données" in (call["system_prompt"] or "")
    # La consigne de format de sortie est bien injectée dans le prompt.
    assert "Un fichier de migration SQL" in call["prompt"]


def test_prompt_systeme_porte_le_garde_fou_migration_destructive():
    # Le garde-fou du rôle (docs/04 §2) doit être présent dans le prompt système : pas
    # de base réelle/production, opérations destructives à valider par un humain.
    provider = WritingProvider(files={"schema.sql": "-- vide"})
    agent = DatabaseAgent(provider)

    asyncio.run(agent.execute("Tâche"))

    (call,) = provider.calls
    systeme = (call["system_prompt"] or "").lower()
    assert "production" in systeme
    assert "validation humaine" in systeme


def test_execute_keep_workspace_conserve_le_repertoire():
    provider = WritingProvider(files={"schema.sql": "-- vide"})
    agent = DatabaseAgent(provider)

    outcome = asyncio.run(agent.execute("Tâche", keep_workspace=True))

    chemin = Path(outcome.workspace)
    try:
        assert chemin.is_dir()
        assert (chemin / "schema.sql").read_text(encoding="utf-8") == "-- vide"
    finally:
        (chemin / "schema.sql").unlink()
        chemin.rmdir()


# --- Garde-fous : entrée, capacité, sérialisation -------------------------------------


def test_execute_refuse_une_description_vide():
    agent = DatabaseAgent(WritingProvider(files={}))
    with pytest.raises(ValueError):
        asyncio.run(agent.execute("   "))


def test_execute_propage_capacite_non_supportee():
    # Un fournisseur texte-seul n'expose pas run_agent : la base lève UnsupportedCapability.
    agent = DatabaseAgent(TextOnlyProvider())
    with pytest.raises(UnsupportedCapability):
        asyncio.run(agent.execute("Tâche"))


def test_outcome_synthese_et_to_dict():
    outcome = DatabaseOutcome(
        resume="Compte-rendu.",
        fichiers=(ProducedFile(chemin="schema.sql", contenu="CREATE TABLE t (id INT);"),),
        workspace="/tmp/ws",
    )
    synthese = outcome.synthese()
    assert "Base de données" in synthese
    assert "1 fichier(s) produit(s)" in synthese
    assert "Compte-rendu." in synthese
    assert "`schema.sql`" in synthese

    data = outcome.to_dict()
    assert data["a_produit"] is True
    assert data["workspace"] == "/tmp/ws"
    assert data["fichiers"] == [
        {"chemin": "schema.sql", "contenu": "CREATE TABLE t (id INT);"}
    ]
