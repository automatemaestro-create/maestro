"""Agent Développeur — un sous-agent outillé du SDK qui code de bout en bout (ticket #4).

Là où le catalogue (`maestro.agents.catalog`) décrit le Développeur comme une *identité*
(compétences, modèle, prompt système), ce module lui donne un **runtime** : il exécute
une tâche de développement *de bout en bout* — comprendre, écrire du code, produire des
fichiers — via l'exécution **agentique outillée** du fournisseur (`ModelProvider.run_agent`,
capacité native de l'Agent SDK), dans un **espace de travail isolé** (`maestro.sandbox`).

Le résultat est **exploitable** : le compte-rendu de l'agent *plus* les fichiers réellement
produits (chemin + contenu), capturés depuis l'espace isolé avant son nettoyage.

Reste **agnostique du fournisseur** : il ne dépend que de `ModelProvider`. Un fournisseur
sans exécution outillée lève `UnsupportedCapability` — l'agent la propage sans la simuler.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from maestro.config import Settings, load_settings
from maestro.providers.base import ModelProvider
from maestro.sandbox import ProducedFile, isolated_workspace

#: Modèle par défaut du Développeur au POC (Claude Sonnet, cf. docs/04 §2), aligné sur
#: le catalogue. Remplaçable sans changer le rôle (couche d'abstraction fournisseur).
_MODELE_DEV = "claude-sonnet-5"

#: Outils confiés au Développeur : lire/écrire/éditer des fichiers, explorer, shell.
#: Volontairement restreint (docs/02 §7 : permissions scopées) — pas d'outils réseau
#: ni MCP au POC.
DEVELOPER_TOOLS: tuple[str, ...] = ("Read", "Write", "Edit", "Glob", "Grep", "Bash")

#: Prompt système du *runtime* Développeur : il doit matérialiser un livrable en
#: fichiers dans son répertoire de travail, pas se contenter d'afficher du code.
_SYSTEM_PROMPT = """\
Tu es l'agent Développeur de Maestro. Tu implémentes du code applicatif de bout en \
bout : tu comprends la tâche, tu écris les fichiers nécessaires et tu produis un \
livrable réellement exploitable.

Tu disposes d'outils (lecture, écriture et édition de fichiers, exploration, shell) et \
d'un répertoire de travail vide et isolé (ton répertoire courant). Matérialise TON \
livrable en fichiers dans ce répertoire — n'affiche pas seulement du code. Garde le \
résultat minimal, cohérent et exécutable.

Garde-fous : reste dans ton répertoire de travail ; n'entreprends aucune action \
destructrice hors de cet espace et ne fusionne rien. Termine par un bref compte-rendu \
de ce que tu as produit et de la manière de l'utiliser."""


@dataclass(frozen=True)
class DeveloperOutcome:
    """Résultat exploitable d'une exécution du Développeur.

    `resume` est le compte-rendu final de l'agent ; `fichiers` sont les livrables
    réellement écrits dans l'espace isolé (chemin relatif + contenu), capturés avant
    nettoyage ; `workspace` est le chemin de cet espace (conservé seulement si
    l'exécution l'a demandé — sinon il n'existe plus sur le disque).
    """

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
            "# Livrable — agent Développeur",
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
            "resume": self.resume,
            "workspace": self.workspace,
            "a_produit": self.a_produit,
            "fichiers": [f.to_dict() for f in self.fichiers],
        }


class DeveloperAgent:
    """Runtime du Développeur : exécute une tâche de dev dans un espace isolé."""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        model: str = _MODELE_DEV,
        tools: Sequence[str] = DEVELOPER_TOOLS,
        system_prompt: str = _SYSTEM_PROMPT,
    ) -> None:
        self._provider = provider
        self._model = model
        self._tools = tuple(tools)
        self._system_prompt = system_prompt

    @classmethod
    def default(cls, settings: Settings | None = None) -> DeveloperAgent:
        """Développeur par défaut du POC : exécution outillée via Claude (config).

        Importe le fournisseur ici (et non en tête de module) pour ne pas lier l'agent
        agnostique à un fournisseur concret : seul ce raccourci connaît Claude.
        """
        from maestro.providers.claude import ClaudeProvider

        settings = settings or load_settings()
        provider = ClaudeProvider.from_settings(settings)
        return cls(provider)

    async def execute(
        self,
        description: str,
        *,
        format_sortie: str | None = None,
        keep_workspace: bool = False,
    ) -> DeveloperOutcome:
        """Réalise la tâche `description` de bout en bout et renvoie le livrable.

        Ouvre un espace de travail isolé, y lance l'exécution agentique du fournisseur,
        puis **capture les fichiers produits** avant que l'espace ne soit nettoyé (sauf
        `keep_workspace=True`). Lève `ValueError` si la description est vide ; propage
        `UnsupportedCapability` si le fournisseur n'exécute pas d'agent outillé.
        """
        description = description.strip()
        if not description:
            raise ValueError("La description de la tâche de développement est vide.")

        prompt = _build_prompt(description, format_sortie)
        with isolated_workspace(keep=keep_workspace) as ws:
            resume = await self._provider.run_agent(
                prompt,
                model=self._model,
                system_prompt=self._system_prompt,
                workspace=ws.path,
                tools=self._tools,
            )
            # Capture *dans* le contexte : hors `keep`, l'espace disparaît à la sortie.
            fichiers = ws.produced_files()
            return DeveloperOutcome(
                resume=resume.strip(), fichiers=fichiers, workspace=str(ws.path)
            )


def _build_prompt(description: str, format_sortie: str | None) -> str:
    """Compose le message confié au Développeur : la tâche + les consignes d'exécution."""
    lignes = [
        "Tâche de développement à réaliser de bout en bout :",
        "",
        description,
        "",
        "Tu travailles dans un répertoire vide et isolé (le répertoire courant). "
        "Écris-y les fichiers du livrable avec tes outils — ne te contente pas "
        "d'afficher du code. Vise un résultat minimal mais réellement exploitable.",
    ]
    if format_sortie:
        lignes += ["", f"Format de sortie attendu : {format_sortie}"]
    lignes += [
        "",
        "Quand c'est fait, résume en quelques lignes ce que tu as produit et comment "
        "l'utiliser.",
    ]
    return "\n".join(lignes)
