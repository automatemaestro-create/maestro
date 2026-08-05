"""Tests du runtime outillé générique et de ses profils de rôle (ticket #35).

Reprend la couverture des runtimes dédiés du Développeur (#4) et du BDD (#5), portée
sur le runtime **générique** qui les factorise. Aucun appel réseau : l'exécution
agentique est pilotée par un `ModelProvider` factice qui écrit *réellement* des
fichiers dans l'espace de travail fourni. Couvre :
① le runtime exécute une tâche de bout en bout et **capture un résultat exploitable**
   (compte-rendu + fichiers produits) dans un **contexte isolé** ;
② les profils (`DEVELOPER_PROFILE`, `DATABASE_PROFILE`, `QA_PROFILE` — #45,
   `DEVOPS_PROFILE` — #67, `DESIGNER_PROFILE` — #68) paramètrent le même runtime
   (modèle, outils, prompts, garde-fous du rôle) ;
③ ajouter un rôle outillé = déclarer un profil, sans nouveau code (critère #35).
Plus : capacité optionnelle refusée proprement, validation d'entrée, sérialisation.
"""

import asyncio
from pathlib import Path

import pytest

from maestro.agents import (
    DATABASE_PROFILE,
    DEFAULT_TOOLS,
    DESIGNER_PROFILE,
    DEVELOPER_PROFILE,
    DEVOPS_PROFILE,
    PLAFOND_TOURS_DEFAUT,
    QA_PROFILE,
    TOOLED_PROFILES,
    AgentOutcome,
    AgentRuntime,
    RoleProfile,
)
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
        raise AssertionError("un rôle outillé doit passer par run_agent, pas generate")

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, plafond_tours=None, projet=None,
    ):
        self.calls.append(
            {
                "prompt": prompt,
                "model": model,
                "system_prompt": system_prompt,
                "workspace": workspace,
                "tools": tuple(tools),
                "plafond_tours": plafond_tours,
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


# --- ① Exécution de bout en bout, résultat exploitable, contexte isolé ----------------


def test_execute_capture_les_fichiers_produits():
    provider = WritingProvider(
        files={"app.py": "print('ok')", "README.md": "# Démo"},
        resume="J'ai créé app.py et README.md.",
    )
    runtime = AgentRuntime(provider, DEVELOPER_PROFILE)

    outcome = asyncio.run(runtime.execute("Écris un hello world"))

    assert isinstance(outcome, AgentOutcome)
    assert outcome.a_produit
    assert outcome.role == "Développeur"
    assert outcome.resume == "J'ai créé app.py et README.md."
    par_chemin = {f.chemin: f.contenu for f in outcome.fichiers}
    assert par_chemin == {"app.py": "print('ok')", "README.md": "# Démo"}


def test_execute_sans_fichier_marque_a_produit_faux():
    runtime = AgentRuntime(WritingProvider(files={}, resume="Rien à faire."), DEVELOPER_PROFILE)
    outcome = asyncio.run(runtime.execute("Ne rien produire"))
    assert not outcome.a_produit
    assert outcome.fichiers == ()


def test_execute_fournit_un_workspace_isole_a_l_agent():
    provider = WritingProvider(files={"a.txt": "x"})
    runtime = AgentRuntime(provider, DEVELOPER_PROFILE)

    outcome = asyncio.run(runtime.execute("Tâche"))

    (call,) = provider.calls
    workspace = call["workspace"]
    # L'agent a reçu un répertoire dédié, distinct du cwd du test.
    assert Path(workspace) != Path.cwd()
    assert str(workspace) == outcome.workspace
    # Hors keep_workspace, l'espace est nettoyé après capture.
    assert not Path(workspace).exists()


def test_execute_keep_workspace_conserve_le_repertoire():
    provider = WritingProvider(files={"a.txt": "x"})
    runtime = AgentRuntime(provider, DEVELOPER_PROFILE)

    outcome = asyncio.run(runtime.execute("Tâche", keep_workspace=True))

    chemin = Path(outcome.workspace)
    try:
        assert chemin.is_dir()
        assert (chemin / "a.txt").read_text(encoding="utf-8") == "x"
    finally:
        (chemin / "a.txt").unlink()
        chemin.rmdir()


# --- ② Les profils paramètrent le même runtime -----------------------------------------


def test_profil_developpeur_transmet_modele_prompt_et_outils():
    provider = WritingProvider(files={"a.txt": "x"})
    runtime = AgentRuntime(provider, DEVELOPER_PROFILE)

    asyncio.run(runtime.execute("Tâche", format_sortie="Un module Python"))

    (call,) = provider.calls
    assert call["model"] == "claude-sonnet-5"
    assert "Bash" in call["tools"] and "Write" in call["tools"]
    assert "Développeur" in (call["system_prompt"] or "")
    # La consigne de format de sortie est bien injectée dans le prompt.
    assert "Un module Python" in call["prompt"]
    assert "Tâche de développement" in call["prompt"]


def test_profil_bdd_porte_le_garde_fou_migration_destructive():
    # Le garde-fou du rôle (docs/04 §2) doit être présent dans le prompt système : pas
    # de base réelle/production, opérations destructives à valider par un humain.
    provider = WritingProvider(files={"schema.sql": "-- vide"})
    runtime = AgentRuntime(provider, DATABASE_PROFILE)

    asyncio.run(runtime.execute("Conçois un schéma clients"))

    (call,) = provider.calls
    systeme = (call["system_prompt"] or "").lower()
    assert "production" in systeme
    assert "validation humaine" in systeme
    assert "Tâche de base de données" in call["prompt"]
    assert "base jetable" in call["prompt"]


def test_profil_bdd_prefixe_son_espace_de_travail():
    provider = WritingProvider(files={"schema.sql": "-- vide"})
    runtime = AgentRuntime(provider, DATABASE_PROFILE)

    outcome = asyncio.run(runtime.execute("Tâche"))

    assert "maestro-bdd-" in Path(outcome.workspace).name
    assert outcome.role == "Base de données"


def test_profil_qa_porte_le_garde_fou_verdict_explicite():
    # La particularité du rôle (docs/04 §3.6, #45) doit être dans le prompt système :
    # verdict explicite (conforme / non conforme), et ne jamais corriger soi-même le
    # livrable évalué — la correction revient au rôle producteur.
    provider = WritingProvider(files={"rapport.md": "# Revue"})
    runtime = AgentRuntime(provider, QA_PROFILE)

    asyncio.run(runtime.execute("Valide le livrable de l'API"))

    (call,) = provider.calls
    systeme = (call["system_prompt"] or "").lower()
    assert "verdict" in systeme
    assert "non conforme" in systeme
    assert "tu ne les réécris pas" in systeme
    assert "Tâche de qualité" in call["prompt"]
    assert "verdict explicite" in call["prompt"]


def test_profil_qa_prefixe_son_espace_de_travail():
    provider = WritingProvider(files={"rapport.md": "# Revue"})
    runtime = AgentRuntime(provider, QA_PROFILE)

    outcome = asyncio.run(runtime.execute("Tâche"))

    assert "maestro-qa-" in Path(outcome.workspace).name
    assert outcome.role == "QA / Testeur"


def test_profil_devops_porte_le_garde_fou_deploiement_valide_par_un_humain():
    # La particularité du rôle (docs/04 §3.4, #67) doit être dans le prompt système :
    # jamais de déploiement vers un environnement réel, tout déploiement passe par une
    # validation humaine, plafonds de ressources respectés.
    provider = WritingProvider(files={".gitlab-ci.yml": "stages: [lint]"})
    runtime = AgentRuntime(provider, DEVOPS_PROFILE)

    asyncio.run(runtime.execute("Mets en place le pipeline CI"))

    (call,) = provider.calls
    systeme = (call["system_prompt"] or "").lower()
    assert "ne déploies jamais" in systeme
    assert "validation humaine" in systeme
    assert "plafonds de ressources" in systeme
    assert "Tâche d'infrastructure" in call["prompt"]
    assert "validation humaine" in call["prompt"]


def test_profil_devops_prefixe_son_espace_de_travail():
    provider = WritingProvider(files={".gitlab-ci.yml": "stages: [lint]"})
    runtime = AgentRuntime(provider, DEVOPS_PROFILE)

    outcome = asyncio.run(runtime.execute("Tâche"))

    assert "maestro-devops-" in Path(outcome.workspace).name
    assert outcome.role == "DevOps"


def test_profil_designer_porte_le_garde_fou_charte_non_remplacee_sans_accord():
    # La particularité du rôle (docs/04 §3.5, #68) doit être dans le prompt système :
    # le design system et la charte existants font foi, l'agent propose sans les
    # remplacer — toute évolution reste soumise à accord.
    provider = WritingProvider(files={"ecran-connexion.md": "# Écran"})
    runtime = AgentRuntime(provider, DESIGNER_PROFILE)

    asyncio.run(runtime.execute("Conçois la maquette de l'écran de connexion"))

    (call,) = provider.calls
    systeme = (call["system_prompt"] or "").lower()
    assert "design system" in systeme
    assert "sans accord" in systeme
    assert "proposition" in systeme
    assert "Tâche de design" in call["prompt"]
    assert "charte" in call["prompt"]


def test_profil_designer_prefixe_son_espace_de_travail():
    provider = WritingProvider(files={"ecran-connexion.md": "# Écran"})
    runtime = AgentRuntime(provider, DESIGNER_PROFILE)

    outcome = asyncio.run(runtime.execute("Tâche"))

    assert "maestro-designer-" in Path(outcome.workspace).name
    assert outcome.role == "Designer"


def test_les_profils_outilles_sont_ceux_du_catalogue():
    # Les clés de routage de la boucle : les noms d'agents du catalogue (#6). Depuis
    # #68, les cinq agents exécutants du catalogue sont tous outillés.
    assert [p.nom for p in TOOLED_PROFILES] == ["developpeur", "bdd", "devops", "designer", "qa"]
    assert all(p.outils == DEFAULT_TOOLS for p in TOOLED_PROFILES)


# --- ③ Ajouter un rôle outillé = déclarer un profil (critère #35) ----------------------


def test_un_nouveau_role_outille_ne_demande_qu_un_profil():
    # Un rôle outillé inédit fonctionne sans nouveau code : le runtime générique
    # suffit. (Le DevOps, qui servait ici d'exemple, est un vrai profil depuis #67 —
    # on prend le « Rédacteur technique », l'agent personnalisé de docs/04 §4.)
    redacteur = RoleProfile(
        nom="redacteur",
        role="Rédacteur technique",
        modele="claude-sonnet-5",
        outils=DEFAULT_TOOLS,
        prompt_systeme="Tu es l'agent Rédacteur technique de Maestro.",
        intro_tache="Tâche de rédaction à réaliser de bout en bout :",
        consignes="Écris les fichiers du livrable dans le répertoire courant.",
        consigne_finale="Résume ce que tu as produit.",
        workspace_prefix="maestro-redacteur-",
    )
    provider = WritingProvider(files={"guide.md": "# Guide"})
    runtime = AgentRuntime(provider, redacteur)

    outcome = asyncio.run(runtime.execute("Rédige le guide d'installation"))

    assert outcome.role == "Rédacteur technique"
    assert outcome.a_produit
    (call,) = provider.calls
    assert "Rédacteur technique" in (call["system_prompt"] or "")
    assert "maestro-redacteur-" in Path(str(call["workspace"])).name


def test_le_runtime_accepte_des_surcharges_ponctuelles():
    # Modèle/outils/prompt système restent surchargeables sans toucher au profil.
    provider = WritingProvider(files={"a.txt": "x"})
    runtime = AgentRuntime(
        provider, DEVELOPER_PROFILE, model="claude-opus-4-8", tools=("Read", "Write")
    )

    asyncio.run(runtime.execute("Tâche"))

    (call,) = provider.calls
    assert call["model"] == "claude-opus-4-8"
    assert call["tools"] == ("Read", "Write")


# --- ④ Plafond de tours réglable par agent (#239) --------------------------------------


def test_le_runtime_transmet_le_plafond_du_profil():
    # C'est le profil qui borne la boucle, pas le fournisseur : deux rôles aux
    # tours de coûts très différents n'héritent plus de la même limite.
    for profil, attendu in ((DEVELOPER_PROFILE, PLAFOND_TOURS_DEFAUT), (DESIGNER_PROFILE, 120)):
        provider = WritingProvider(files={"a.txt": "x"})
        asyncio.run(AgentRuntime(provider, profil).execute("Tâche"))
        (call,) = provider.calls
        assert call["plafond_tours"] == attendu


def test_le_plafond_est_surchargeable_comme_le_modele():
    # Même canal que `model`/`tools` : le câblage peut resserrer ou desserrer la
    # borne d'un rôle sans toucher à son profil.
    provider = WritingProvider(files={"a.txt": "x"})
    asyncio.run(AgentRuntime(provider, DESIGNER_PROFILE, plafond_tours=12).execute("Tâche"))
    (call,) = provider.calls
    assert call["plafond_tours"] == 12


def test_chaque_profil_outille_declare_un_plafond_borne():
    # Déclaration explicite (#239) : la valeur d'un rôle est un choix lisible, pas
    # un héritage silencieux — et aucune n'est absente, nulle ni négative.
    assert all(isinstance(p.plafond_tours, int) and p.plafond_tours > 0 for p in TOOLED_PROFILES)
    # Marge accrue pour la conception, défaut conservateur pour les autres.
    assert DESIGNER_PROFILE.plafond_tours > PLAFOND_TOURS_DEFAUT
    autres = [p for p in TOOLED_PROFILES if p is not DESIGNER_PROFILE]
    assert all(p.plafond_tours == PLAFOND_TOURS_DEFAUT for p in autres)


def test_un_profil_qui_ne_declare_rien_reste_borne_par_le_defaut():
    # Le champ a un défaut : un profil tiers (agent personnalisé, docs/04 §4) qui
    # l'ignore est borné quand même — jamais illimité.
    redacteur = RoleProfile(
        nom="redacteur",
        role="Rédacteur technique",
        modele="claude-sonnet-5",
        outils=DEFAULT_TOOLS,
        prompt_systeme="Tu es l'agent Rédacteur technique de Maestro.",
        intro_tache="Tâche de rédaction :",
        consignes="Écris les fichiers du livrable dans le répertoire courant.",
        consigne_finale="Résume ce que tu as produit.",
        workspace_prefix="maestro-redacteur-",
    )
    assert redacteur.plafond_tours == PLAFOND_TOURS_DEFAUT


# --- Garde-fous : entrée, capacité, sérialisation -------------------------------------


def test_execute_refuse_une_description_vide():
    runtime = AgentRuntime(WritingProvider(files={}), DEVELOPER_PROFILE)
    with pytest.raises(ValueError):
        asyncio.run(runtime.execute("   "))


def test_execute_propage_capacite_non_supportee():
    # Un fournisseur texte-seul n'expose pas run_agent : la base lève UnsupportedCapability.
    runtime = AgentRuntime(TextOnlyProvider(), DATABASE_PROFILE)
    with pytest.raises(UnsupportedCapability):
        asyncio.run(runtime.execute("Tâche"))


def test_outcome_synthese_et_to_dict():
    outcome = AgentOutcome(
        role="Base de données",
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
    assert data["role"] == "Base de données"
    assert data["a_produit"] is True
    assert data["workspace"] == "/tmp/ws"
    assert data["fichiers"] == [
        {"chemin": "schema.sql", "contenu": "CREATE TABLE t (id INT);"}
    ]
