"""L'entité Projet : sa forme persistée et ses défauts (ticket #221, EF-35).

Matérialise l'entité `PROJECT` de [docs/03](../../docs/03-modele-de-donnees.md),
déclarée depuis le cadrage et implémentée nulle part, avec les champs qui la
**relient au disque** ([docs/24 §2.3](../../docs/24-projets-locaux-et-poste-de-travail.md)) :

- `racine` — le chemin absolu du projet sur le poste, **frontière unique** de ce
  que les agents peuvent lire et écrire (la validation vit dans
  `maestro.projets.racine`, EF-38) ;
- `origine` — `nouveau` (un dossier qu'on initie) ou `existant` (un projet qu'on
  reprend) ;
- `vcs` — le gestionnaire de versions **détecté** à la déclaration, `None` si le
  projet n'est pas versionné : un projet non versionné reste déclarable, le VCS
  n'est jamais imposé ;
- `perimetre` — ce que le projet expose (`inclus`) et ce qu'il retire d'office
  (`exclus` : la plomberie Git, les dépendances installées et les deux gisements
  de secrets, cf. `EXCLUS_DEFAUT`).

Le modèle est **inerte** : il décrit et sérialise, il ne touche pas au disque.
Ce qui l'y confronte est ailleurs — `maestro.projets.racine` (valider, détecter)
et `maestro.projets.store` (persister). C'est ce qui rend ce socle testable sans
projet réel, et réutilisable tel quel par l'API des projets (#223).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maestro.appartenance import ID_PROJET as ID_PROJET  # ré-export explicite (#222)

#: Les exclusions posées d'office au périmètre d'un projet (docs/24 §2.3) : la
#: plomberie Git, les dépendances installées, et surtout les deux gisements de
#: secrets d'un dépôt d'utilisateur — le `.env` et tout ce qui vit sous un
#: dossier `secrets/` (docs/24 §2.5, « fuite de secrets du projet »).
EXCLUS_DEFAUT: tuple[str, ...] = (".git", "node_modules", ".env", "**/secrets/**")

#: L'inclusion par défaut : toute la racine. Les motifs du périmètre sont
#: **relatifs à la racine** — jamais des chemins absolus.
INCLUS_DEFAUT: tuple[str, ...] = (".",)

#: Les deux origines d'un projet (docs/24 §2.1) : un dossier neuf qu'on initie
#: (besoin A), ou un projet existant qu'on reprend (besoin B).
ORIGINES: frozenset[str] = frozenset({"nouveau", "existant"})

#: Identifiant de projet admissible comme nom de fichier — même verrou que les
#: autres dépôts (`maestro.agents.store`) : slug sûr, sans séparateur ni point,
#: ce qui interdit toute traversée de chemin depuis un identifiant venu de l'API.
#: **Défini** dans `maestro.appartenance` (#222) et seulement ré-exporté ici (cf.
#: l'import en tête) : le même contrat sert désormais à valider ce que le journal
#: et les événements transportent. Les imports existants — `from
#: maestro.projets.modele import ID_PROJET` — restent servis tels quels.

#: Préfixe des identifiants engendrés (docs/24 §2.3 donne `prj-7f3a` en exemple).
#: L'entropie vient d'un `uuid4` tronqué, comme les identifiants de run
#: (`maestro.controltower.executions`) — 8 hex plutôt que 4, la collision étant
#: ici une **écrasure de projet** et non un doublon de journal.
PREFIXE_ID = "prj-"


def nouvel_id() -> str:
    """Un identifiant de projet neuf, de la forme `prj-<8 hex>` (docs/24 §2.3)."""
    return f"{PREFIXE_ID}{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class Vcs:
    """Le gestionnaire de versions d'un projet — **détecté**, jamais imposé.

    `branche_base` est la branche sur laquelle le projet est posé au moment de
    la déclaration : c'est d'elle que partiront les branches/worktrees par tâche
    (#224, EF-36). Vide si le dépôt est en HEAD détaché. `distant` est l'URL du
    remote `origin`, `None` s'il n'y en a pas (dépôt purement local).
    """

    type: str = "git"
    branche_base: str = ""
    distant: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Réémet le VCS en dict JSON-sérialisable (le fragment `vcs` du fichier)."""
        return {
            "type": self.type,
            "branche_base": self.branche_base,
            "distant": self.distant,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Vcs:
        """Reconstruit un VCS depuis sa forme `to_dict`."""
        return cls(
            type=data.get("type", "git"),
            branche_base=data.get("branche_base", "") or "",
            distant=data.get("distant") or None,
        )


@dataclass(frozen=True)
class Perimetre:
    """Ce qu'un projet expose aux agents : motifs inclus, motifs exclus.

    Les motifs sont **relatifs à la racine** (style glob). `exclus` l'emporte sur
    `inclus` — c'est la règle attendue d'un périmètre, et c'est ce qui garantit
    que les exclusions par défaut (`EXCLUS_DEFAUT`) tiennent même sous un
    `inclus` large comme `.`. L'application des motifs viendra avec l'espace de
    travail dérivé (#224) ; ici on les **déclare** et on les persiste.
    """

    inclus: tuple[str, ...] = INCLUS_DEFAUT
    exclus: tuple[str, ...] = EXCLUS_DEFAUT

    def to_dict(self) -> dict[str, Any]:
        """Réémet le périmètre en dict JSON-sérialisable (listes, pas tuples)."""
        return {"inclus": list(self.inclus), "exclus": list(self.exclus)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Perimetre:
        """Reconstruit un périmètre depuis sa forme `to_dict`.

        Un `inclus`/`exclus` absent retombe sur le défaut ; un `exclus`
        **explicitement vide** est respecté (c'est un choix de l'utilisateur,
        pas une donnée manquante).
        """
        return cls(
            inclus=_motifs(data.get("inclus"), INCLUS_DEFAUT),
            exclus=_motifs(data.get("exclus"), EXCLUS_DEFAUT),
        )


def _motifs(valeur: Any, defaut: tuple[str, ...]) -> tuple[str, ...]:
    """Les motifs d'un périmètre relu depuis le fichier, quel qu'en soit l'auteur.

    Un `exclus` **absent** retombe sur le défaut ; explicitement **vide**, il est
    respecté (c'est un choix). Une **chaîne** vaut un motif unique : un fichier
    édité à la main porte `"exclus": ".env"` bien plus souvent qu'on ne croit,
    et l'éclater en caractères ferait disparaître l'exclusion sans rien dire.
    """
    if valeur is None:
        return defaut
    if isinstance(valeur, str):
        return (valeur,)
    return tuple(str(motif) for motif in valeur)


@dataclass(frozen=True)
class Projet:
    """Un projet de l'utilisateur : une racine sur le disque et son périmètre (EF-35).

    `racine` est stockée en **chemin POSIX absolu** (`D:/projets/depensio`, comme
    docs/24 §2.3) : c'est la même chaîne sur les trois OS, elle traverse le JSON
    et l'API sans échappement, et `racine_chemin` la rend au code sous forme de
    `Path`. Elle est posée **canonicalisée** par le dépôt — jamais telle que
    saisie (EF-38).

    `cree_le`/`modifie_le` sont posés par le dépôt à l'écriture (ISO 8601, UTC,
    docs/03 « horodatage sur toutes les tables principales »).
    """

    id: str
    nom: str
    racine: str
    origine: str = "existant"
    vcs: Vcs | None = None
    perimetre: Perimetre = field(default_factory=Perimetre)
    cree_le: str = ""
    modifie_le: str = ""

    @property
    def racine_chemin(self) -> Path:
        """La racine sous forme de `Path` — le chemin qu'utilisent moteur et sandbox."""
        return Path(self.racine)

    @property
    def versionne(self) -> bool:
        """Le projet est-il sous gestion de versions ? (détermine le patron d'écriture, #224)."""
        return self.vcs is not None

    def to_dict(self) -> dict[str, Any]:
        """Réémet le projet en dict JSON-sérialisable (le fichier stocké)."""
        return {
            "id": self.id,
            "nom": self.nom,
            "racine": self.racine,
            "origine": self.origine,
            "vcs": self.vcs.to_dict() if self.vcs is not None else None,
            "perimetre": self.perimetre.to_dict(),
            "cree_le": self.cree_le,
            "modifie_le": self.modifie_le,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Projet:
        """Reconstruit un projet depuis sa forme `to_dict` (le fichier stocké)."""
        vcs = data.get("vcs")
        perimetre = data.get("perimetre")
        return cls(
            id=data["id"],
            nom=data.get("nom", ""),
            racine=data.get("racine", ""),
            origine=data.get("origine", "existant"),
            vcs=Vcs.from_dict(vcs) if vcs else None,
            perimetre=Perimetre.from_dict(perimetre) if perimetre else Perimetre(),
            cree_le=data.get("cree_le", ""),
            modifie_le=data.get("modifie_le", ""),
        )
