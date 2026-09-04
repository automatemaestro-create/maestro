"""Contexte d'exécution isolé — un espace de travail jetable par tâche (ticket #4).

Matérialise l'« isolation d'exécution » attendue d'un agent exécutant (docs/02 §7,
`core/sandbox`) au niveau POC : **un répertoire temporaire dédié par tâche**, créé
vide, dans lequel l'agent produit son livrable sans collision avec les autres tâches.
C'est l'isolation *au niveau du système de fichiers* — suffisante pour que des tâches
parallèles n'écrasent pas leurs fichiers (le pendant de « branche Git par tâche »,
docs/02 §7).

Le renfort prévu est livré en opt-in par le ticket #108 : en mode isolé
(`MAESTRO_ISOLATION=conteneur`), l'exécution outillée qui travaille dans cet espace
tourne dans un **conteneur Docker durci** jetable (isolation des processus/réseau,
plafonds de ressources — cf. `maestro.sandbox.container`), sans changer ce contrat —
`Workspace` reste la frontière que voit l'agent, le répertoire étant monté dans le
conteneur.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Self

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
    """Répertoire isolé où un agent exécute sa tâche et dépose son livrable.

    `empreintes` recense ce qui s'y trouvait **avant** que l'agent ne travaille —
    vide (None) pour l'espace jetable historique, créé vide, où tout fichier
    présent est par construction un livrable. Depuis #224 l'espace peut être
    **dérivé d'un projet** (worktree Git, ou — depuis #839 — la racine même d'un
    projet non versionné) : il est alors peuplé de centaines de fichiers que
    l'agent n'a pas écrits, et les recenser comme livrables gonflerait le rapport
    du run de tout le dépôt de l'utilisateur. L'empreinte (taille + date de
    modification à la nanoseconde) est le moyen le moins cher de faire la
    différence, et le seul qui vaille quel que soit l'espace.

    Le **recensement** — ce que `derive` relève, ce que `produced_files` rend —
    passe par `fichiers`, la seule énumération de l'espace, que la racine d'un
    projet redéfinit (`maestro.sandbox.en_place.EspaceEnPlace`) pour parcourir
    l'arbre **par le périmètre** : sans ce point de passage unique, empreintes et
    livrables seraient relevés sur deux énumérations, et un fichier vu par l'une
    et pas par l'autre ressortirait en livrable sans que l'agent l'ait écrit.
    """

    path: Path
    empreintes: Mapping[str, tuple[int, int]] | None = None

    @classmethod
    def derive(cls, path: Path, **champs: Any) -> Self:
        """Un espace **déjà peuplé** : ce qui s'y trouve à cet instant n'est pas un livrable.

        `champs` sont les champs propres à une sous-classe (le périmètre d'un
        `EspaceEnPlace`) : ils sont posés **avant** le relevé, parce que c'est
        d'eux que dépend ce que `fichiers` énumère.
        """
        vide = cls(path=path, **champs)
        return replace(vide, empreintes=vide._releve())

    def fichiers(self) -> Iterator[Path]:
        """Les fichiers de l'espace, triés par chemin — **tout** ce qui vit sous `path`.

        C'est l'énumération de l'espace jetable et du worktree : rien n'y est
        exclu, l'un est créé vide et l'autre est une copie conforme de la branche.
        """
        for f in sorted(self.path.rglob("*")):
            if f.is_file():
                yield f

    def produced_files(self) -> tuple[ProducedFile, ...]:
        """Recense les fichiers **produits**, triés par chemin relatif (déterministe).

        Capture le contenu texte des fichiers raisonnables et laisse `contenu=None`
        pour le binaire/volumineux. Les répertoires (vides ou non) sont ignorés :
        seul le fichier est un livrable. Un fichier inchangé depuis la dérivation
        de l'espace (cf. `empreintes`) n'en est pas un — c'est le projet, pas le
        travail de l'agent.
        """
        fichiers: list[ProducedFile] = []
        for f in self.fichiers():
            if self._inchange(f):
                continue
            rel = f.relative_to(self.path).as_posix()
            fichiers.append(ProducedFile(chemin=rel, contenu=_lire_texte(f)))
        return tuple(fichiers)

    def _inchange(self, fichier: Path) -> bool:
        """`fichier` est-il celui d'origine, tel que l'espace l'a reçu à sa dérivation ?"""
        if self.empreintes is None:
            return False
        rel = fichier.relative_to(self.path).as_posix()
        depart = self.empreintes.get(rel)
        return depart is not None and depart == _empreinte(fichier)

    def _releve(self) -> dict[str, tuple[int, int]]:
        """L'empreinte de chaque fichier de l'espace, par chemin relatif POSIX."""
        return {f.relative_to(self.path).as_posix(): _empreinte(f) for f in self.fichiers()}


def _empreinte(fichier: Path) -> tuple[int, int]:
    """Taille et date de modification (ns) d'un fichier — `(-1, -1)` s'il est illisible.

    Pas de somme de contrôle : relire tout un dépôt deux fois par tâche coûterait
    plus cher que ce que la précision gagnée rapporte. Une réécriture à taille et
    horodatage identiques à la nanoseconde n'existe pas en pratique ; un fichier
    devenu illisible est traité comme modifié, ce qui le fait recenser plutôt que
    disparaître en silence.
    """
    try:
        etat = fichier.stat()
    except OSError:
        return (-1, -1)
    return (etat.st_size, etat.st_mtime_ns)


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
