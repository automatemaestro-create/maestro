"""Contexte d'exécution isolé — un espace de travail jetable par tâche (ticket #4).

Matérialise l'« isolation d'exécution » attendue d'un agent exécutant (docs/02 §7,
`core/sandbox`) au niveau POC : **un répertoire temporaire dédié par tâche**, créé
vide, dans lequel l'agent produit son livrable sans collision avec les autres tâches.
C'est l'isolation *au niveau du système de fichiers* — suffisante pour que des tâches
parallèles n'écrasent pas leurs fichiers (le pendant de « branche Git par tâche »,
docs/02 §7).

Évolution prévue (hors POC) : adosser cet espace à un **conteneur Docker jetable** par
tâche (isolation des processus/réseau, plafonds de ressources), sans changer ce
contrat — `Workspace` reste la frontière que voit l'agent.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Taille max d'un fichier dont on capture le contenu (octets). Au-delà, on garde le
#: chemin mais pas le contenu : un livrable exploitable n'a pas à charger en mémoire
#: des artefacts binaires ou volumineux.
_MAX_CAPTURE_BYTES = 1_000_000


@dataclass(frozen=True)
class ProducedFile:
    """Un fichier produit dans l'espace de travail : chemin relatif + contenu texte.

    `contenu` vaut None pour un fichier binaire, illisible ou trop volumineux (cf.
    `_MAX_CAPTURE_BYTES`) : le livrable reste exploitable — on sait *ce qui* a été
    produit — sans embarquer d'octets illisibles.
    """

    chemin: str
    contenu: str | None

    def to_dict(self) -> dict[str, str | None]:
        """Réémet le fichier en dict JSON-sérialisable."""
        return {"chemin": self.chemin, "contenu": self.contenu}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProducedFile:
        """Reconstruit le fichier depuis sa forme `to_dict` (aller-retour JSON, #41)."""
        return cls(chemin=str(data["chemin"]), contenu=data.get("contenu"))


@dataclass(frozen=True)
class Workspace:
    """Répertoire isolé où un agent exécute sa tâche et dépose son livrable."""

    path: Path

    def produced_files(self) -> tuple[ProducedFile, ...]:
        """Recense les fichiers présents, triés par chemin relatif (déterministe).

        Capture le contenu texte des fichiers raisonnables et laisse `contenu=None`
        pour le binaire/volumineux. Les répertoires (vides ou non) sont ignorés :
        seul le fichier est un livrable.
        """
        fichiers: list[ProducedFile] = []
        for f in sorted(self.path.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(self.path).as_posix()
            fichiers.append(ProducedFile(chemin=rel, contenu=_lire_texte(f)))
        return tuple(fichiers)


def _lire_texte(f: Path) -> str | None:
    """Renvoie le contenu texte de `f`, ou None si binaire, illisible ou trop gros."""
    try:
        if f.stat().st_size > _MAX_CAPTURE_BYTES:
            return None
        return f.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


@contextmanager
def isolated_workspace(
    *, prefix: str = "maestro-dev-", keep: bool = False
) -> Iterator[Workspace]:
    """Ouvre un espace de travail isolé (répertoire temporaire) et le nettoie en sortie.

    `keep=True` conserve le répertoire (utile pour inspecter le livrable après coup),
    son chemin restant lisible via `Workspace.path`. Sinon il est supprimé **même en
    cas d'exception**, pour ne pas laisser de résidus sur le disque.
    """
    path = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield Workspace(path=path)
    finally:
        if not keep:
            shutil.rmtree(path, ignore_errors=True)
