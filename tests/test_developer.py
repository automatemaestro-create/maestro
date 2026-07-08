"""Tests du runtime de l'agent Développeur (ticket #4).

Aucun appel réseau : l'exécution agentique est pilotée par un `ModelProvider` factice
qui écrit *réellement* des fichiers dans l'espace de travail fourni. Couvre les deux
critères du ticket :
① l'agent exécute une tâche de dev de bout en bout et **écrit un résultat exploitable**
   (les fichiers produits sont capturés dans le livrable) ;
② l'exécution a lieu dans un **contexte isolé** (un répertoire dédié transmis à l'agent).
Plus : capacité optionnelle refusée proprement, validation d'entrée, sérialisation.
"""

import asyncio
from pathlib import Path

import pytest

from maestro.agents.developer import DeveloperAgent, DeveloperOutcome
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
        raise AssertionError("le Développeur doit passer par run_agent, pas generate")

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


# --- Critère ① : exécute et écrit un résultat exploitable -----------------------------


def test_execute_capture_les_fichiers_produits():
    provider = WritingProvider(
        files={"app.py": "print('ok')", "README.md": "# Démo"},
        resume="J'ai créé app.py et README.md.",
    )
    agent = DeveloperAgent(provider)

    outcome = asyncio.run(agent.execute("Écris un hello world"))

    assert isinstance(outcome, DeveloperOutcome)
    assert outcome.a_produit
    assert outcome.resume == "J'ai créé app.py et README.md."
    par_chemin = {f.chemin: f.contenu for f in outcome.fichiers}
    assert par_chemin == {"app.py": "print('ok')", "README.md": "# Démo"}


def test_execute_sans_fichier_marque_a_produit_faux():
    agent = DeveloperAgent(WritingProvider(files={}, resume="Rien à faire."))
    outcome = asyncio.run(agent.execute("Ne rien produire"))
    assert not outcome.a_produit
    assert outcome.fichiers == ()


# --- Critère ② : contexte d'exécution isolé -------------------------------------------


def test_execute_fournit_un_workspace_isole_a_l_agent():
    provider = WritingProvider(files={"a.txt": "x"})
    agent = DeveloperAgent(provider)

    outcome = asyncio.run(agent.execute("Tâche"))

    (call,) = provider.calls
    workspace = call["workspace"]
    # L'agent a reçu un répertoire dédié, distinct du cwd du test.
    assert Path(workspace) != Path.cwd()
    assert str(workspace) == outcome.workspace
    # Hors keep_workspace, l'espace est nettoyé après capture.
    assert not Path(workspace).exists()


def test_execute_transmet_le_modele_le_prompt_systeme_et_les_outils():
    provider = WritingProvider(files={"a.txt": "x"})
    agent = DeveloperAgent(provider)

    asyncio.run(agent.execute("Tâche", format_sortie="Un module Python"))

    (call,) = provider.calls
    assert call["model"] == "claude-sonnet-5"
    assert "Bash" in call["tools"] and "Write" in call["tools"]
    assert "Développeur" in (call["system_prompt"] or "")
    # La consigne de format de sortie est bien injectée dans le prompt.
    assert "Un module Python" in call["prompt"]


def test_execute_keep_workspace_conserve_le_repertoire():
    provider = WritingProvider(files={"a.txt": "x"})
    agent = DeveloperAgent(provider)

    outcome = asyncio.run(agent.execute("Tâche", keep_workspace=True))

    chemin = Path(outcome.workspace)
    try:
        assert chemin.is_dir()
        assert (chemin / "a.txt").read_text(encoding="utf-8") == "x"
    finally:
        (chemin / "a.txt").unlink()
        chemin.rmdir()


# --- Garde-fous : entrée, capacité, sérialisation -------------------------------------


def test_execute_refuse_une_description_vide():
    agent = DeveloperAgent(WritingProvider(files={}))
    with pytest.raises(ValueError):
        asyncio.run(agent.execute("   "))


def test_execute_propage_capacite_non_supportee():
    # Un fournisseur texte-seul n'expose pas run_agent : la base lève UnsupportedCapability.
    agent = DeveloperAgent(TextOnlyProvider())
    with pytest.raises(UnsupportedCapability):
        asyncio.run(agent.execute("Tâche"))


def test_outcome_synthese_et_to_dict():
    outcome = DeveloperOutcome(
        resume="Compte-rendu.",
        fichiers=(ProducedFile(chemin="a.py", contenu="print(1)"),),
        workspace="/tmp/ws",
    )
    synthese = outcome.synthese()
    assert "1 fichier(s) produit(s)" in synthese
    assert "Compte-rendu." in synthese
    assert "`a.py`" in synthese

    data = outcome.to_dict()
    assert data["a_produit"] is True
    assert data["workspace"] == "/tmp/ws"
    assert data["fichiers"] == [{"chemin": "a.py", "contenu": "print(1)"}]
