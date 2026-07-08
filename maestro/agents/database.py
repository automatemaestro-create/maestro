"""Agent Base de données — un sous-agent outillé du SDK qui traite une tâche BDD (ticket #5).

Pendant du Développeur (`maestro.agents.developer`) pour le domaine **base de données** :
là où le catalogue (`maestro.agents.catalog`) décrit le BDD comme une *identité*
(compétences `sql`/`schema`/`migration`/`data`, modèle, prompt système), ce module lui
donne un **runtime** — il exécute une tâche BDD *de bout en bout* (concevoir un schéma,
écrire des migrations, optimiser des requêtes) via l'exécution **agentique outillée** du
fournisseur (`ModelProvider.run_agent`, capacité native de l'Agent SDK), dans un **espace
de travail isolé** (`maestro.sandbox`).

Le résultat est **exploitable** : le compte-rendu de l'agent *plus* les artefacts réellement
produits (schéma, fichiers de migration, requêtes ; chemin + contenu), capturés depuis
l'espace isolé avant son nettoyage.

Garde-fou propre au BDD (docs/04 §2, playbook du catalogue) : une migration **destructive**
(DROP, perte de données) doit être clairement signalée et laissée à une **validation
humaine** ; l'agent ne cible **jamais** une base réelle ou de production — toute validation
se fait contre une base jetable créée dans l'espace de travail.

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

#: Modèle par défaut du BDD au POC (Claude Sonnet, cf. docs/04 §2), aligné sur le
#: catalogue. Remplaçable sans changer le rôle (couche d'abstraction fournisseur).
_MODELE_BDD = "claude-sonnet-5"

#: Outils confiés au BDD : lire/écrire/éditer des fichiers, explorer, shell. Le shell
#: sert à *valider* le livrable contre une base jetable (ex. SQLite dans l'espace de
#: travail), jamais contre une base réelle. Volontairement restreint (docs/02 §7 :
#: permissions scopées) — pas d'outils réseau ni MCP au POC.
DATABASE_TOOLS: tuple[str, ...] = ("Read", "Write", "Edit", "Glob", "Grep", "Bash")

#: Prompt système du *runtime* BDD : il doit matérialiser un livrable en fichiers
#: (schéma, migrations, requêtes) dans son répertoire de travail, et porter le
#: garde-fou anti-migration-destructive du rôle.
_SYSTEM_PROMPT = """\
Tu es l'agent Base de données de Maestro. Tu traites une tâche de base de données de \
bout en bout : tu conçois le schéma, tu écris les migrations et tu optimises les \
requêtes, et tu produis un livrable réellement exploitable.

Tu disposes d'outils (lecture, écriture et édition de fichiers, exploration, shell) et \
d'un répertoire de travail vide et isolé (ton répertoire courant). Matérialise TON \
livrable en fichiers dans ce répertoire (schéma SQL, fichiers de migration, requêtes) — \
n'affiche pas seulement du SQL. Garde le résultat minimal, cohérent et applicable.

Garde-fous : reste dans ton répertoire de travail. Ne te connecte JAMAIS à une base \
réelle ou de production ; si tu veux vérifier ton schéma ou tes migrations, fais-le \
uniquement contre une base jetable que tu crées dans ce répertoire (ex. un fichier \
SQLite local). Toute opération destructive (DROP, TRUNCATE, suppression de colonne, \
perte de données) doit être clairement signalée dans ton compte-rendu comme nécessitant \
une validation humaine — tu la proposes, tu ne la réputes jamais appliquée en \
production. Termine par un bref compte-rendu de ce que tu as produit, de la manière de \
l'appliquer et des points qui requièrent une validation."""


@dataclass(frozen=True)
class DatabaseOutcome:
    """Résultat exploitable d'une exécution du BDD.

    `resume` est le compte-rendu final de l'agent ; `fichiers` sont les livrables
    réellement écrits dans l'espace isolé (schéma, migrations, requêtes ; chemin relatif
    + contenu), capturés avant nettoyage ; `workspace` est le chemin de cet espace
    (conservé seulement si l'exécution l'a demandé — sinon il n'existe plus sur le disque).
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
            "# Livrable — agent Base de données",
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


class DatabaseAgent:
    """Runtime du BDD : exécute une tâche de base de données dans un espace isolé."""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        model: str = _MODELE_BDD,
        tools: Sequence[str] = DATABASE_TOOLS,
        system_prompt: str = _SYSTEM_PROMPT,
    ) -> None:
        self._provider = provider
        self._model = model
        self._tools = tuple(tools)
        self._system_prompt = system_prompt

    @classmethod
    def default(cls, settings: Settings | None = None) -> DatabaseAgent:
        """BDD par défaut du POC : exécution outillée via Claude (config).

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
    ) -> DatabaseOutcome:
        """Réalise la tâche BDD `description` de bout en bout et renvoie le livrable.

        Ouvre un espace de travail isolé, y lance l'exécution agentique du fournisseur,
        puis **capture les fichiers produits** avant que l'espace ne soit nettoyé (sauf
        `keep_workspace=True`). Lève `ValueError` si la description est vide ; propage
        `UnsupportedCapability` si le fournisseur n'exécute pas d'agent outillé.
        """
        description = description.strip()
        if not description:
            raise ValueError("La description de la tâche de base de données est vide.")

        prompt = _build_prompt(description, format_sortie)
        with isolated_workspace(prefix="maestro-bdd-", keep=keep_workspace) as ws:
            resume = await self._provider.run_agent(
                prompt,
                model=self._model,
                system_prompt=self._system_prompt,
                workspace=ws.path,
                tools=self._tools,
            )
            # Capture *dans* le contexte : hors `keep`, l'espace disparaît à la sortie.
            fichiers = ws.produced_files()
            return DatabaseOutcome(
                resume=resume.strip(), fichiers=fichiers, workspace=str(ws.path)
            )


def _build_prompt(description: str, format_sortie: str | None) -> str:
    """Compose le message confié au BDD : la tâche + les consignes d'exécution."""
    lignes = [
        "Tâche de base de données à réaliser de bout en bout :",
        "",
        description,
        "",
        "Tu travailles dans un répertoire vide et isolé (le répertoire courant). "
        "Écris-y les fichiers du livrable avec tes outils (schéma, migrations, "
        "requêtes) — ne te contente pas d'afficher du SQL. Vise un résultat minimal "
        "mais réellement applicable. Ne cible aucune base réelle : toute vérification "
        "se fait contre une base jetable créée dans ce répertoire.",
    ]
    if format_sortie:
        lignes += ["", f"Format de sortie attendu : {format_sortie}"]
    lignes += [
        "",
        "Quand c'est fait, résume en quelques lignes ce que tu as produit, comment "
        "l'appliquer, et signale toute opération destructive à valider par un humain.",
    ]
    return "\n".join(lignes)
