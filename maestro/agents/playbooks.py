"""Stockage versionné des playbooks des agents (ticket #76).

Matérialise l'entité `PLAYBOOK_VERSION` (docs/03) et le playbook de docs/04 §1 :
les instructions d'un agent deviennent un document **versionné hors du code**,
à l'historique consultable et au retour arrière possible (EF-24/EF-25). Au POC
le dépôt est sur fichiers (`core/playbooks/<agent>/vNNNN.md`) ; en V1 il passera
en base (table PLAYBOOK_VERSION) sans changer ce contrat.

Le dépôt est **append-only** : chaque écriture publie la version suivante, la
version **courante** est la plus haute, et le retour arrière **republie** une
version passée comme nouvelle version — l'historique reste linéaire, rien n'est
jamais réécrit. Un agent jamais édité retombe sur son playbook « du code »
(`PLAYBOOK_DEFAUTS`, les prompts système des profils outillés) : un dépôt vide
reproduit exactement le comportement actuel, ce qui rend ce lot mergeable seul.

Le chargement s'applique aux deux chemins d'exécution — catalogue texte
(`avec_playbooks`) et runtimes outillés (`maestro.agents.default_runtimes`) — au
**câblage** du moteur ; l'application à chaud en cours de vie du process est le
lot #78. L'API de lecture/écriture vit dans `maestro.controltower.app`.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from maestro.agents.catalog import Agent
from maestro.agents.database import DATABASE_PROFILE
from maestro.agents.designer import DESIGNER_PROFILE
from maestro.agents.developer import DEVELOPER_PROFILE
from maestro.agents.devops import DEVOPS_PROFILE
from maestro.agents.qa import QA_PROFILE
from maestro.config import Settings, load_settings

#: Nom d'agent admissible comme dossier de stockage : slug sûr, sans séparateur ni
#: point — verrouille toute traversée de chemin depuis un nom venu de l'API.
_NOM_AGENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: Un fichier de version : `v0001.md`, `v0002.md`… Numéros sur 4 chiffres minimum,
#: pour que l'ordre lexicographique des noms suive l'ordre numérique.
_FICHIER_VERSION = re.compile(r"^v(\d{4,})\.md$")


@dataclass(frozen=True)
class PlaybookVersion:
    """Une version du playbook d'un agent — l'entité PLAYBOOK_VERSION (docs/03).

    `contenu` est le playbook intégral (Markdown, docs/04 §1) ; `cree_le` son
    horodatage de publication (ISO 8601, UTC).
    """

    agent: str
    version: int
    contenu: str
    cree_le: str

    def to_dict(self, *, avec_contenu: bool = True) -> dict[str, Any]:
        """Réémet la version en dict JSON-sérialisable (métadonnées seules si demandé)."""
        fiche: dict[str, Any] = {
            "agent": self.agent,
            "version": self.version,
            "cree_le": self.cree_le,
        }
        if avec_contenu:
            fiche["contenu"] = self.contenu
        return fiche


@dataclass(frozen=True)
class PlaybookDefaut:
    """Le playbook « du code » d'un rôle : le repli tant que rien n'a été édité."""

    agent: str
    role: str
    contenu: str


#: Les playbooks par défaut, indexés par nom d'agent du catalogue : le prompt
#: système du profil outillé de chaque rôle — les instructions effectives
#: d'exécution du POC. C'est aussi la liste des agents que l'API accepte d'éditer.
PLAYBOOK_DEFAUTS: dict[str, PlaybookDefaut] = {
    profil.nom: PlaybookDefaut(agent=profil.nom, role=profil.role, contenu=profil.prompt_systeme)
    for profil in (
        DEVELOPER_PROFILE,
        DATABASE_PROFILE,
        DEVOPS_PROFILE,
        DESIGNER_PROFILE,
        QA_PROFILE,
    )
}


class PlaybookStore:
    """Dépôt versionné des playbooks, sur fichiers (`<racine>/<agent>/vNNNN.md`).

    Append-only (voir le module) : `ecrire` publie la version suivante, `lire`
    sans numéro rend la courante, `restaurer` republie une version passée. Un
    seul écrivain à la fois au POC (l'API Control Tower) : la numérotation ne
    porte pas de verrou de concurrence.
    """

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @property
    def racine(self) -> Path:
        """La racine du dépôt (un dossier par agent)."""
        return self._racine

    @classmethod
    def default(cls, settings: Settings | None = None) -> PlaybookStore:
        """Le dépôt configuré : `MAESTRO_PLAYBOOKS_DIR`, sinon `core/playbooks/` du dépôt."""
        settings = settings or load_settings()
        if settings.playbooks_dir:
            return cls(Path(settings.playbooks_dir))
        return cls(Path(__file__).resolve().parents[2] / "core" / "playbooks")

    def numeros(self, agent: str) -> tuple[int, ...]:
        """Les numéros de version stockés pour `agent`, croissants (vide si aucun)."""
        dossier = self._dossier(agent)
        if not dossier.is_dir():
            return ()
        return tuple(
            sorted(
                int(m.group(1))
                for chemin in dossier.iterdir()
                if (m := _FICHIER_VERSION.match(chemin.name))
            )
        )

    def versions(self, agent: str) -> tuple[PlaybookVersion, ...]:
        """L'historique consultable du playbook de `agent`, de la première à la courante."""
        return tuple(
            version
            for numero in self.numeros(agent)
            if (version := self.lire(agent, numero)) is not None
        )

    def lire(self, agent: str, version: int | None = None) -> PlaybookVersion | None:
        """La version `version` du playbook (la courante si None), ou None si absente."""
        if version is None:
            numeros = self.numeros(agent)
            if not numeros:
                return None
            version = numeros[-1]
        chemin = self._dossier(agent) / _nom_fichier(version)
        if not chemin.is_file():
            return None
        return PlaybookVersion(
            agent=agent,
            version=version,
            contenu=chemin.read_text(encoding="utf-8"),
            cree_le=_horodatage(chemin),
        )

    def ecrire(self, agent: str, contenu: str) -> PlaybookVersion:
        """Publie `contenu` comme nouvelle version courante du playbook de `agent`.

        Écriture atomique (fichier temporaire puis renommage) : une version
        n'apparaît dans l'historique que complète. Lève `ValueError` sur un
        contenu vide — un playbook vide n'a aucun sens comme prompt système.
        """
        if not contenu.strip():
            raise ValueError(f"contenu de playbook vide pour l'agent {agent!r}.")
        dossier = self._dossier(agent)
        dossier.mkdir(parents=True, exist_ok=True)
        numeros = self.numeros(agent)
        version = numeros[-1] + 1 if numeros else 1
        chemin = dossier / _nom_fichier(version)
        temporaire = dossier / (chemin.name + ".tmp")
        temporaire.write_text(contenu, encoding="utf-8")
        os.replace(temporaire, chemin)
        return PlaybookVersion(
            agent=agent, version=version, contenu=contenu, cree_le=_horodatage(chemin)
        )

    def restaurer(self, agent: str, version: int) -> PlaybookVersion:
        """Retour arrière (EF-25) : republie la version `version` comme nouvelle courante.

        L'historique reste linéaire et complet — la version restaurée devient une
        nouvelle version, rien n'est supprimé. Lève `ValueError` si `version`
        n'existe pas dans l'historique de `agent`.
        """
        source = self.lire(agent, version)
        if source is None:
            raise ValueError(f"version inconnue pour l'agent {agent!r} : {version}")
        return self.ecrire(agent, source.contenu)

    def prompt_systeme(self, agent: str, defaut: str) -> str:
        """Le prompt système effectif de `agent` : la version courante stockée, sinon `defaut`.

        C'est le point de chargement du moteur : tant que le playbook n'a jamais
        été édité, chaque chemin d'exécution garde exactement son prompt du code.
        """
        courant = self.lire(agent)
        return courant.contenu if courant is not None else defaut

    def _dossier(self, agent: str) -> Path:
        """Le dossier de stockage de `agent`, nom validé (jamais un chemin arbitraire)."""
        if not _NOM_AGENT.match(agent):
            raise ValueError(f"nom d'agent invalide : {agent!r} (slug [a-z0-9_-] attendu).")
        return self._racine / agent


def avec_playbooks(agents: Sequence[Agent], store: PlaybookStore) -> tuple[Agent, ...]:
    """Le catalogue `agents`, chaque prompt système remplacé par son playbook stocké.

    La moitié « exécution texte » du chargement (#76) — la moitié outillée passe
    par `maestro.agents.default_runtimes(playbooks=...)`. Un agent jamais édité
    garde son prompt du code : un dépôt vide rend le catalogue à l'identique.
    """
    return tuple(
        replace(agent, prompt_systeme=store.prompt_systeme(agent.nom, agent.prompt_systeme))
        for agent in agents
    )


def _nom_fichier(version: int) -> str:
    """Le nom de fichier d'une version (`v0001.md`) — zéros de tête pour le tri."""
    return f"v{version:04d}.md"


def _horodatage(chemin: Path) -> str:
    """L'horodatage de publication d'une version : le mtime du fichier, en ISO UTC."""
    return datetime.fromtimestamp(chemin.stat().st_mtime, tz=UTC).isoformat(timespec="seconds")
