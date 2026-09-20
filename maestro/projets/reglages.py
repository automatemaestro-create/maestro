"""Le **répertoire des projets** : où naît un projet neuf (#1022).

Un réglage **du poste**, pas d'un projet : il ne dit rien d'un projet déclaré,
il dit seulement dans quel dossier le prochain sera créé. Le formulaire de
déclaration s'en sert pour **remplir d'office** le dossier parent d'un projet
neuf ; l'utilisateur peut en choisir un autre pour ce projet-là sans toucher au
réglage, et l'import d'un projet **existant** ne le voit jamais.

⚠ **Deux « racines » se croisent dans le dépôt, et elles n'ont rien à voir.**
`MAESTRO_PROJETS_DIR` (`maestro.projets.store`) est le dépôt des
**déclarations** — un `<id>.json` par projet, ce que Maestro sait d'eux.
`MAESTRO_REGLAGES_DIR`, ici, est le dépôt des **réglages du poste**, dont ce
`repertoire` : *où* un projet neuf sera créé sur le disque. D'où deux dossiers
distincts, plutôt qu'un `reglages.json` glissé parmi les déclarations — que
`ProjetStore.lister()` lirait comme un projet illisible de plus.

## Le défaut, et pourquoi il porte un nom

`~/Maestro` — un sous-dossier **nommé** du dossier personnel, jamais le dossier
personnel nu, que `valider_racine` refuse (`dossier-utilisateur-nu`) et qui
n'est de toute façon pas un endroit où ranger des projets. C'est aussi le parti
pris que prennent les produits comparables capturés par la veille de #1022 —
`~/IdeaProjects` chez IntelliJ, `Documents/GitHub` chez GitHub Desktop.

## « Créé à la première utilisation »

Le dossier n'existe pas forcément : le réglage est une **intention**, et la
créer au démarrage de l'API poserait un dossier à qui n'ouvrira jamais l'écran
des projets. `resoudre(creer=True)` le crée, et c'est
`GET /api/projets/repertoire` qui le demande — la première fois qu'on demande
*où naît un projet neuf* est exactement sa première utilisation, et rendre un
chemin que l'explorateur refuserait ensuite (`dossier-absent`) serait pire que
le créer. La réponse **dit** qu'elle l'a créé plutôt que de le faire en
silence.

Tout passe par `valider_racine` (EF-38) : il n'y a pas de seconde porte de
validation, ici pas plus qu'ailleurs (docs/24 §2.5).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from maestro.config import Settings, load_settings
from maestro.projets.racine import RacineRefusee, valider_racine

#: Le nom du dossier proposé sous le dossier personnel — nommé, jamais nu.
NOM_DEFAUT = "Maestro"

#: Le fichier unique du dépôt de réglages (un objet, pas un par entité).
_FICHIER = "projets.json"


def repertoire_par_defaut() -> Path:
    """`~/Maestro` — le répertoire proposé quand rien n'a été réglé.

    Rendu **non résolu et non créé** : c'est une proposition, que
    `valider_racine` jugera comme n'importe quelle autre. Lève `RacineRefusee`
    (`dossier-utilisateur-introuvable`) si l'OS ne sait pas dire où est le
    dossier personnel — le cas des conteneurs sans `HOME`, où le réglage devra
    être posé à la main.
    """
    try:
        maison = Path.home()
    except (RuntimeError, OSError) as exc:  # pragma: no cover - dépend de l'environnement
        raise RacineRefusee(
            "dossier-utilisateur-introuvable",
            "Dossier personnel introuvable : réglez le répertoire des projets à la main.",
        ) from exc
    return maison / NOM_DEFAUT


@dataclass(frozen=True)
class ReglagesProjets:
    """Les réglages du poste côté projets — un seul pour l'instant.

    `repertoire` à `None` signifie « le défaut », **et non** « aucun » : le
    stocker nul plutôt que résolu est ce qui fait suivre un déménagement du
    dossier personnel sans réécrire le fichier.
    """

    repertoire: str | None = None
    modifie_le: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Réémet les réglages en dict JSON-sérialisable (le fichier stocké)."""
        return {"repertoire": self.repertoire, "modifie_le": self.modifie_le}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReglagesProjets:
        """Reconstruit les réglages depuis leur forme `to_dict`."""
        brut = data.get("repertoire")
        repertoire = brut.strip() if isinstance(brut, str) and brut.strip() else None
        modifie = data.get("modifie_le")
        return cls(
            repertoire=repertoire,
            modifie_le=modifie if isinstance(modifie, str) else "",
        )


class ReglagesProjetsStore:
    """Dépôt des réglages du poste côté projets (`<racine>/projets.json`).

    Un **fichier unique** — c'est un objet de réglages, pas une collection —,
    écrit atomiquement (temporaire puis renommage). Un dépôt vide, absent ou
    illisible rend les réglages par défaut : un réglage qu'on ne sait pas lire
    ne doit pas empêcher de déclarer un projet.
    """

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @property
    def racine(self) -> Path:
        """La racine du dépôt (un seul fichier dedans)."""
        return self._racine

    @classmethod
    def default(cls, settings: Settings | None = None) -> ReglagesProjetsStore:
        """Le dépôt configuré : `MAESTRO_REGLAGES_DIR`, sinon `core/reglages/`."""
        settings = settings or load_settings()
        if settings.reglages_dir:
            return cls(Path(settings.reglages_dir))
        return cls(Path(__file__).resolve().parents[2] / "core" / "reglages")

    def lire(self) -> ReglagesProjets:
        """Les réglages stockés, ou ceux par défaut — ne lève jamais."""
        chemin = self._racine / _FICHIER
        if not chemin.is_file():
            return ReglagesProjets()
        try:
            data = json.loads(chemin.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ReglagesProjets()
        if not isinstance(data, dict):
            return ReglagesProjets()
        return ReglagesProjets.from_dict(data)

    def ecrire(self, repertoire: str | Path | None) -> ReglagesProjets:
        """Persiste le répertoire (`None` = retour au défaut) et rend les réglages datés.

        Le chemin est **validé et créé** avant d'être stocké (`valider_racine`,
        `creer=True`) : un réglage qui pointe vers un dossier refusé ne servirait
        qu'à faire échouer la déclaration suivante, plus tard et ailleurs. C'est
        le stockage du chemin **résolu**, jamais de la saisie.
        """
        valeur: str | None = None
        if repertoire is not None and str(repertoire).strip():
            # Stocké en **POSIX**, comme toute racine que l'API rend (docs/05
            # §6.7) : sans quoi le même dossier s'écrirait `C:\…` ici et `C:/…`
            # dans l'explorateur, et l'écran ne saurait plus qu'il s'agit du
            # même — constat de la relecture visuelle de #1022.
            valeur = valider_racine(repertoire, creer=True).as_posix()
        propre = replace(ReglagesProjets(repertoire=valeur), modifie_le=_maintenant())
        self._racine.mkdir(parents=True, exist_ok=True)
        chemin = self._racine / _FICHIER
        temporaire = chemin.with_suffix(".json.tmp")
        temporaire.write_text(
            json.dumps(propre.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporaire, chemin)
        return propre

    def resoudre(self, *, creer: bool = False) -> tuple[Path, bool, bool]:
        """Le répertoire courant : `(chemin, par_defaut, cree)`.

        `par_defaut` dit qu'aucun réglage n'a été posé et que le chemin est
        `~/Maestro`. `cree` dit que **cet appel** a créé le dossier — c'est ce
        que la réponse d'API rapporte, pour qu'une création ne soit jamais
        silencieuse. Lève `RacineRefusee` motivée quand le chemin ne peut pas
        être une racine de projet (dossier sensible, disque débranché…), et —
        avec `creer=False` seulement — quand il n'existe pas encore.
        """
        reglages = self.lire()
        par_defaut = reglages.repertoire is None
        brut = reglages.repertoire if not par_defaut else repertoire_par_defaut()
        assert brut is not None  # `repertoire_par_defaut` lève plutôt que de rendre None
        existait = Path(brut).expanduser().is_dir()
        resolu = valider_racine(brut, creer=creer)
        return resolu, par_defaut, creer and not existait


def _maintenant() -> str:
    """L'horodatage UTC ISO-8601 des réglages (même forme que les autres dépôts)."""
    return datetime.now(UTC).isoformat(timespec="seconds")
