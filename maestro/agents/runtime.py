"""Runtime outillé générique — un seul moteur d'exécution pour tous les rôles (ticket #35).

Factorise ce que les runtimes du Développeur (#4) et de la Base de données (#5)
dupliquaient : l'ouverture d'un **espace de travail isolé** (`maestro.sandbox`),
l'exécution **agentique outillée** du fournisseur (`ModelProvider.run_agent`), la
capture des fichiers produits et leur mise en forme en livrable exploitable.

Ce qui distingue un rôle d'un autre tient dans un `RoleProfile` (identité, modèle,
outils, prompts) : ajouter un agent outillé = déclarer un profil, sans copier de code.
Les profils existants vivent dans leur module de rôle (`maestro.agents.developer`,
`maestro.agents.database`).

Reste **agnostique du fournisseur** : il ne dépend que de `ModelProvider`. Un
fournisseur sans exécution outillée lève `UnsupportedCapability` — le runtime la
propage sans la simuler (c'est l'appelant, ex. la boucle d'orchestration, qui décide
d'un éventuel repli).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from maestro.config import Settings, load_settings
from maestro.providers.base import ModelProvider
from maestro.sandbox import ProducedFile, isolated_workspace

#: Outils confiés par défaut à un rôle outillé : lire/écrire/éditer des fichiers,
#: explorer, shell. Volontairement restreint (docs/02 §7 : permissions scopées) —
#: pas d'outils réseau ni MCP au POC.
DEFAULT_TOOLS: tuple[str, ...] = ("Read", "Write", "Edit", "Glob", "Grep", "Bash")


@dataclass(frozen=True)
class RoleProfile:
    """Paramétrage d'un rôle outillé : tout ce qui varie d'un agent à l'autre.

    `nom` est l'identifiant du catalogue (`maestro.agents.catalog`) — la clé de
    routage de la boucle d'orchestration ; `role` le libellé humain (titres des
    synthèses). Les trois fragments de prompt (`intro_tache`, `consignes`,
    `consigne_finale`) encadrent la description de la tâche dans le message confié
    à l'agent ; `prompt_systeme` porte l'identité et les garde-fous du rôle.
    """

    nom: str
    role: str
    modele: str
    outils: tuple[str, ...]
    prompt_systeme: str
    intro_tache: str
    consignes: str
    consigne_finale: str
    workspace_prefix: str


@dataclass(frozen=True)
class AgentOutcome:
    """Résultat exploitable d'une exécution outillée.

    `role` est le libellé du rôle qui a produit le livrable ; `resume` le
    compte-rendu final de l'agent ; `fichiers` les livrables réellement écrits dans
    l'espace isolé (chemin relatif + contenu), capturés avant nettoyage ;
    `workspace` le chemin de cet espace (conservé seulement si l'exécution l'a
    demandé — sinon il n'existe plus sur le disque).
    """

    role: str
    resume: str
    fichiers: tuple[ProducedFile, ...]
    workspace: str

    @property
    def a_produit(self) -> bool:
        """L'agent a-t-il écrit au moins un fichier (livrable non vide) ?"""
        return bool(self.fichiers)

    def synthese(self) -> str:
        """Rend le résultat en Markdown : compte-rendu puis liste des fichiers produits."""
        lignes = [
            f"# Livrable — agent {self.role}",
            "",
            f"{len(self.fichiers)} fichier(s) produit(s) dans un espace de travail isolé.",
            "",
            "## Compte-rendu",
            "",
            self.resume or "(aucun compte-rendu)",
            "",
            "## Fichiers produits",
        ]
        if not self.fichiers:
            lignes += ["", "(aucun fichier)"]
        else:
            lignes += [f"- `{f.chemin}`" for f in self.fichiers]
        return "\n".join(lignes).rstrip() + "\n"

    def to_dict(self) -> dict[str, Any]:
        """Réémet le résultat en dict JSON-sérialisable."""
        return {
            "role": self.role,
            "resume": self.resume,
            "workspace": self.workspace,
            "a_produit": self.a_produit,
            "fichiers": [f.to_dict() for f in self.fichiers],
        }


class AgentRuntime:
    """Runtime d'un rôle outillé : exécute une tâche de bout en bout dans un espace isolé."""

    def __init__(
        self,
        provider: ModelProvider,
        profile: RoleProfile,
        *,
        model: str | None = None,
        tools: Sequence[str] | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self._provider = provider
        self._profile = profile
        self._model = model or profile.modele
        self._tools = tuple(tools) if tools is not None else profile.outils
        self._system_prompt = system_prompt or profile.prompt_systeme

    @property
    def profile(self) -> RoleProfile:
        """Profil du rôle exécuté par ce runtime."""
        return self._profile

    @classmethod
    def default(cls, profile: RoleProfile, settings: Settings | None = None) -> AgentRuntime:
        """Runtime par défaut pour `profile` : fournisseur et modèle issus de la config (#69).

        Importe la fabrique ici (et non en tête de module) pour ne pas lier le
        runtime agnostique à un fournisseur concret : le choix vit dans la config
        (`MAESTRO_PROVIDER`), plus dans le code. Un fournisseur sans exécution
        outillée lèvera `UnsupportedCapability` à l'exécution — propagé tel quel.
        """
        from maestro.providers.factory import provider_from_settings

        settings = settings or load_settings()
        return cls(provider_from_settings(settings), profile, model=settings.model)

    async def execute(
        self,
        description: str,
        *,
        format_sortie: str | None = None,
        keep_workspace: bool = False,
        system_prompt: str | None = None,
    ) -> AgentOutcome:
        """Réalise la tâche `description` de bout en bout et renvoie le livrable.

        Ouvre un espace de travail isolé, y lance l'exécution agentique du fournisseur,
        puis **capture les fichiers produits** avant que l'espace ne soit nettoyé (sauf
        `keep_workspace=True`). Lève `ValueError` si la description est vide ; propage
        `UnsupportedCapability` si le fournisseur n'exécute pas d'agent outillé.

        `system_prompt` remplace, pour **cette exécution**, le prompt système du
        runtime : c'est le canal de l'application à chaud des playbooks (#78) —
        l'exécuteur passe la version courante du playbook stocké, sans reconstruire
        le runtime. None : le prompt câblé à la construction (comportement d'origine).
        """
        description = description.strip()
        if not description:
            raise ValueError(
                f"La description de la tâche confiée au rôle {self._profile.role} est vide."
            )

        prompt = _build_prompt(self._profile, description, format_sortie)
        with isolated_workspace(prefix=self._profile.workspace_prefix, keep=keep_workspace) as ws:
            resume = await self._provider.run_agent(
                prompt,
                model=self._model,
                system_prompt=system_prompt or self._system_prompt,
                workspace=ws.path,
                tools=self._tools,
            )
            # Capture *dans* le contexte : hors `keep`, l'espace disparaît à la sortie.
            fichiers = ws.produced_files()
            return AgentOutcome(
                role=self._profile.role,
                resume=resume.strip(),
                fichiers=fichiers,
                workspace=str(ws.path),
            )


def _build_prompt(profile: RoleProfile, description: str, format_sortie: str | None) -> str:
    """Compose le message confié à l'agent : la tâche encadrée par les consignes du rôle."""
    lignes = [profile.intro_tache, "", description, "", profile.consignes]
    if format_sortie:
        lignes += ["", f"Format de sortie attendu : {format_sortie}"]
    lignes += ["", profile.consigne_finale]
    return "\n".join(lignes)
