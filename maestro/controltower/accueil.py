"""Le **premier lancement** d'un poste : le parcours d'accueil reste-t-il à faire ? (#642)

Au premier démarrage d'une installation neuve, la porte d'entrée de la Control
Tower ne s'ouvre pas directement sur la création d'un projet. Elle guide trois
réglages, dans cet ordre :

1. le fournisseur de modèle, choisi et éprouvé (#1609) ;
2. le répertoire des projets (#1022), avec les dossiers autorisés (#1610) ;
3. le premier projet, né dans la conversation (#1294).

Ce module ne répond qu'à une question : **ce parcours reste-t-il à faire sur ce
poste ?**

## Le fait qui le clôt

Le parcours se termine sur son dernier fait, la naissance d'un projet. L'écran
le signale (`POST /api/accueil/terminer`) au moment où il ouvre le projet né, et
la date est gardée dans le dépôt des réglages du poste (`accueil.json`, à côté de
`fournisseur.json` et de `projets.json`). Un poste qui retire plus tard tous ses
projets ne revoit donc pas le parcours : il retrouve la création, comme avant.

Un poste qui a déjà des projets n'est jamais à son premier lancement, même sans
ce fichier : la question ne se pose qu'à un poste qui n'en a aucun. Cela tient à
l'écart les installations antérieures au parcours, et rattrape une clôture que
l'écran n'a pas pu écrire (une API coupée à ce moment-là).

## Ce qui n'est pas ici

Les étapes ne sont pas un état de ce module. Chacune se lit sur le réglage
qu'elle pose, par les routes qui le servent déjà : le fournisseur enregistré, le
répertoire accepté, un projet déclaré. Les recopier ici en ferait une seconde
vérité. **Revoir** le parcours depuis les Paramètres n'écrit rien non plus :
c'est une demande de la session de l'écran, pas un réglage du poste, et le
parcours ne se rejoue donc pas au démarrage suivant.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Le fichier du premier lancement, dans le dépôt des réglages du poste.
FICHIER = "accueil.json"

#: Où vit le dépôt des réglages du poste — la même variable que `Settings.reglages_dir`.
_VARIABLE_DEPOT = "MAESTRO_REGLAGES_DIR"


class DepotAccueil:
    """Le fichier du premier lancement : lu sans jamais lever, écrit d'un seul geste."""

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @classmethod
    def default(cls, environ: Mapping[str, str] | None = None) -> DepotAccueil:
        """Le dépôt des réglages du poste : `MAESTRO_REGLAGES_DIR`, sinon `core/reglages/`."""
        source = os.environ if environ is None else environ
        racine = (source.get(_VARIABLE_DEPOT) or "").strip()
        if racine:
            return cls(Path(racine))
        return cls(Path(__file__).resolve().parents[2] / "core" / "reglages")

    @property
    def chemin(self) -> Path:
        return self._racine / FICHIER

    def termine_le(self) -> str | None:
        """La date de fin du parcours, ou None : absent, illisible ou abîmé, jamais une levée."""
        try:
            data = json.loads(self.chemin.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        date = data.get("termine_le")
        return date if isinstance(date, str) and date.strip() else None

    def terminer(self, maintenant: datetime | None = None) -> str:
        """Note la fin du parcours et rend sa date — la première, si elle l'était déjà.

        Écrit par un fichier temporaire renommé, jamais à moitié. Un poste qui
        termine deux fois (deux fenêtres, un projet né puis un autre) garde la
        date où il a vraiment fini.
        """
        deja = self.termine_le()
        if deja is not None:
            return deja
        date = (maintenant or datetime.now(UTC)).isoformat(timespec="seconds")
        self._racine.mkdir(parents=True, exist_ok=True)
        provisoire = self.chemin.with_suffix(".json.tmp")
        provisoire.write_text(
            json.dumps({"termine_le": date}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        provisoire.replace(self.chemin)
        return date


class ServiceAccueil:
    """Ce que `/api/accueil` sert : le parcours reste-t-il à faire, et depuis quand est-il fini."""

    def __init__(self, depot: DepotAccueil, compter_projets: Callable[[], int]) -> None:
        self._depot = depot
        self._compter_projets = compter_projets

    def etat(self) -> dict[str, Any]:
        """`a_faire` : aucun projet déclaré sur ce poste, et un parcours jamais terminé."""
        termine_le = self._depot.termine_le()
        a_faire = termine_le is None and self._compter_projets() == 0
        return {"a_faire": a_faire, "termine_le": termine_le}

    def terminer(self) -> dict[str, Any]:
        """Note la fin du parcours (idempotent) et rend l'état qui en résulte."""
        self._depot.terminer()
        return self.etat()
