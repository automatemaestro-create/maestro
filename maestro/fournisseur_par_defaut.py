"""Le fournisseur par défaut du poste : choisi dans les Paramètres, relu à chaque usage (#1609).

Le choix se fait à l'écran : le fournisseur, l'accès par lequel on l'atteint
(abonnement par le CLI, clé, serveur local) et, au besoin, le modèle. Ce module
le garde, et `maestro.config.load_settings` s'en sert pour **compléter**
l'environnement à chaque lecture des réglages. Tout ce qui résout un
fournisseur le prend donc au prochain appel, sans redémarrage : l'API, et les
runs qui tournent dans leur propre process.

## Ce qu'on garde : des variables, pas une seconde configuration

Le choix est enregistré dans le vocabulaire de l'exécution, celui d'un `.env` :
`MAESTRO_PROVIDER`, `MAESTRO_MODEL`, et ce que l'accès retenu pose
(`CLAUDE_AUTH_MODE`, `OPENAI_BASE_URL`). Ces variables sont calculées une fois,
quand le choix est enregistré, à partir de l'accès que le fournisseur déclare
(`maestro.providers.base.AccesFournisseur`). Ce module n'a donc rien à savoir
des fournisseurs : il n'importe ni leur couche ni le SDK, et la configuration
le lit sans les tirer.

La clé, quand l'accès en demande une, n'est jamais dans ce fichier : elle vit
chiffrée dans le coffre du poste (`SecretStore.lire_poste`), et le fichier ne
porte que le **nom** de la variable qu'elle remplit.

## L'environnement garde la main

Une variable **renseignée** dans l'environnement l'emporte toujours sur le
choix du poste, et `imposees` dit lesquelles : l'écran l'affiche, avec le
geste qui rend la main. Une variable vide (`MAESTRO_PROVIDER=` dans un `.env`
copié du modèle) ne compte pas comme renseignée.

Le fichier vit dans le dépôt des réglages du poste (`MAESTRO_REGLAGES_DIR`, à
défaut `core/reglages/`), à côté du répertoire des projets
(`maestro.projets.reglages`).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Le fichier du choix, dans le dépôt des réglages du poste.
FICHIER = "fournisseur.json"

#: La variable qui désigne le fournisseur de l'exécution.
VARIABLE_FOURNISSEUR = "MAESTRO_PROVIDER"

#: La variable qui impose un modèle à toute l'exécution.
VARIABLE_MODELE = "MAESTRO_MODEL"

#: Où vit le dépôt des réglages du poste — la même variable que `Settings.reglages_dir`.
_VARIABLE_DEPOT = "MAESTRO_REGLAGES_DIR"


@dataclass(frozen=True)
class FournisseurParDefaut:
    """Le choix enregistré : ce que la personne a choisi, et les variables qu'il pose.

    `variables` sont les paires (nom, valeur) que le choix apporte à
    l'environnement, fournisseur et modèle compris. `secret` est le nom de la
    variable que le coffre du poste remplit — jamais sa valeur.
    """

    fournisseur: str
    acces: str
    modele: str | None = None
    adresse: str | None = None
    variables: tuple[tuple[str, str], ...] = ()
    secret: str | None = None
    eprouve_le: str = ""
    pose_le: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "fournisseur": self.fournisseur,
            "acces": self.acces,
            "modele": self.modele,
            "adresse": self.adresse,
            "variables": {nom: valeur for nom, valeur in self.variables},
            "secret": self.secret,
            "eprouve_le": self.eprouve_le,
            "pose_le": self.pose_le,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FournisseurParDefaut | None:
        """Le choix relu, ou None s'il ne se lit pas — un fichier abîmé vaut « rien de choisi »."""
        fournisseur = data.get("fournisseur")
        acces = data.get("acces")
        variables = data.get("variables")
        if not isinstance(fournisseur, str) or not fournisseur:
            return None
        if not isinstance(acces, str) or not acces:
            return None
        if not isinstance(variables, Mapping) or not all(
            isinstance(nom, str) and isinstance(valeur, str) for nom, valeur in variables.items()
        ):
            return None
        return cls(
            fournisseur=fournisseur,
            acces=acces,
            modele=_texte(data.get("modele")),
            adresse=_texte(data.get("adresse")),
            variables=tuple(variables.items()),
            secret=_texte(data.get("secret")),
            eprouve_le=_texte(data.get("eprouve_le")) or "",
            pose_le=_texte(data.get("pose_le")) or "",
        )


class DepotFournisseurParDefaut:
    """Le fichier du choix : lu sans jamais lever, écrit d'un seul geste."""

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @classmethod
    def default(cls, environ: Mapping[str, str] | None = None) -> DepotFournisseurParDefaut:
        """Le dépôt des réglages du poste : `MAESTRO_REGLAGES_DIR`, sinon `core/reglages/`."""
        source = os.environ if environ is None else environ
        racine = (source.get(_VARIABLE_DEPOT) or "").strip()
        if racine:
            return cls(Path(racine))
        return cls(Path(__file__).resolve().parents[1] / "core" / "reglages")

    @property
    def chemin(self) -> Path:
        return self._racine / FICHIER

    def lire(self) -> FournisseurParDefaut | None:
        """Le choix enregistré, ou None — absent, illisible ou abîmé, jamais une exception."""
        try:
            data = json.loads(self.chemin.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return FournisseurParDefaut.from_dict(data)

    def ecrire(self, choix: FournisseurParDefaut) -> FournisseurParDefaut:
        """Enregistre le choix, daté — par un fichier temporaire renommé, jamais à moitié écrit."""
        date = datetime.now(UTC).isoformat(timespec="seconds")
        enregistre = FournisseurParDefaut(
            fournisseur=choix.fournisseur,
            acces=choix.acces,
            modele=choix.modele,
            adresse=choix.adresse,
            variables=choix.variables,
            secret=choix.secret,
            eprouve_le=choix.eprouve_le,
            pose_le=date,
        )
        self._racine.mkdir(parents=True, exist_ok=True)
        provisoire = self.chemin.with_suffix(".json.tmp")
        provisoire.write_text(
            json.dumps(enregistre.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        provisoire.replace(self.chemin)
        return enregistre

    def effacer(self) -> bool:
        """Retire le choix : l'environnement seul décide de nouveau. Vrai s'il existait."""
        try:
            self.chemin.unlink()
        except FileNotFoundError:
            return False
        return True


def environnement_du_poste(
    environ: Mapping[str, str],
    *,
    depot: DepotFournisseurParDefaut | None = None,
    secret: Callable[[str], str | None] | None = None,
) -> dict[str, str]:
    """`environ`, complété par le choix du poste là où il est vide.

    Ce que `load_settings` lit. Sans choix enregistré, c'est `environ` tel
    quel. La clé de l'accès choisi n'est lue dans le coffre que si
    l'environnement n'en porte pas déjà une. `depot` et `secret` désignent un
    autre dépôt et un autre coffre que ceux du poste (le service de la Control
    Tower passe les siens) ; sans eux, ceux que `environ` désigne.
    """
    complet = dict(environ)
    choix = (depot or DepotFournisseurParDefaut.default(environ)).lire()
    if choix is None:
        return complet
    for nom, valeur in choix.variables:
        if not _renseignee(complet, nom):
            complet[nom] = valeur
    if choix.secret and not _renseignee(complet, choix.secret):
        lu = secret(choix.secret) if secret is not None else secret_du_poste(choix.secret, environ)
        if lu:
            complet[choix.secret] = lu
    return complet


def imposees(variables: tuple[str, ...], environ: Mapping[str, str]) -> tuple[str, ...]:
    """Celles de `variables` que l'environnement renseigne : elles l'emportent sur l'écran."""
    return tuple(nom for nom in variables if _renseignee(environ, nom))


def secret_du_poste(nom: str, environ: Mapping[str, str]) -> str | None:
    """La valeur de `nom` dans le coffre du poste, ou None.

    Un coffre illisible (clé maîtresse changée, fichier abîmé) rend None plutôt
    que de lever : la configuration est lue par tout ce qui appelle un modèle,
    et une clé qui manque se dit à l'épreuve, avec son motif, plutôt qu'en
    panne de chaque appel.
    """
    from maestro.agents.secrets import SecretStore
    from maestro.config import Settings

    try:
        return SecretStore.default(Settings.from_env(environ)).lire_poste().get(nom)
    except Exception:  # noqa: BLE001 — voir le docstring : un coffre illisible vaut « pas de clé »
        return None


def _renseignee(environ: Mapping[str, str], nom: str) -> bool:
    return bool((environ.get(nom) or "").strip())


def _texte(valeur: Any) -> str | None:
    return valeur if isinstance(valeur, str) and valeur else None


__all__ = [
    "FICHIER",
    "VARIABLE_FOURNISSEUR",
    "VARIABLE_MODELE",
    "DepotFournisseurParDefaut",
    "FournisseurParDefaut",
    "environnement_du_poste",
    "imposees",
    "secret_du_poste",
]
