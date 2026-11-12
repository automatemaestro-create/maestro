"""Ce que l'installation fait tourner : la version du produit, et le CLI `claude` employé (#1622).

Une installation porte quatre composants dont les versions peuvent diverger (docs/24 §4.4) : la
coque et le front, le backend Python, ses dépendances, et le CLI `claude` que le SDK lance. Le
paquet les pose ensemble (docs/54 §2bis). Ce module dit ce qui tourne **vraiment**, pour que les
Paramètres le montrent (`GET /api/installation`) et qu'un composant qui ne suit pas le paquet se
signale au lieu d'être supposé.

- **La version du produit** est écrite à l'assemblage, dans `version.json` à la racine du produit
  (`ecrire_version`, appelé par `scripts/distribution/assembler.py`), depuis la source qui donne
  aussi sa version au paquet : `apps/desktop/package.json`. Dans un clone, aucun assemblage n'a eu
  lieu : on lit cette source elle-même. La coque lit le même fichier pour comparer sa version à
  celle du produit qu'elle lance (`apps/desktop/main.js`).
- **Le CLI `claude`** est celui que le produit lance (`cli_claude_resolu`) : celui que le SDK
  embarque dans son paquet, sinon le `claude` du `PATH`. La provenance dit lequel des deux, et
  c'est elle qui signale un CLI pris hors de l'installation. Sa version se **lit** sur ce binaire
  (`<chemin> --version`, par `maestro.clients_du_poste.lire_version`) : elle ne se déduit jamais
  de celle du SDK, et aucun outil interne du CLI n'est lu (#1315, docs/44).

Un CLI qui ne dit pas sa version la laisse **inconnue** (`None`) : elle n'est jamais devinée.

**La forme du produit** — installé ou clone — se lit sur le réglage que le paquet pose
(`maestro.emplacements.installation`), celui qui range aussi la configuration de la personne : un
seul signal décide de « installé » (#1633). La présence d'un `.git` n'en décide pas : une archive
du dépôt n'en porte pas, et un `.git` peut traîner dans une installation.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from maestro.clients_du_poste import lire_version
from maestro.emplacements import installation
from maestro.espace import racine_de_la_copie

#: Le fichier où l'assemblage écrit la version du produit, à la racine de celui-ci. La coque le
#: lit sous `resources/produit/` (`apps/desktop/main.js`) : le nom est un accord entre les deux.
FICHIER_VERSION = "version.json"

#: La source de la version, relativement à la racine d'un clone : celle qui la donne aussi au
#: paquet (electron-builder la lit là). L'assemblage la recopie, un clone la lit en place.
SOURCE_DE_LA_VERSION = PurePosixPath("apps/desktop/package.json")

#: `installation` : un produit posé par le paquet, qui le dit par son réglage ; `depot` : tout
#: autre — un clone, un worktree, une copie du dépôt (`forme_du_produit`).
Forme = Literal["installation", "depot"]

#: D'où vient le CLI que le produit lance : `paquet` (celui que le SDK embarque, qui suit
#: l'installation), `poste` (un `claude` du `PATH`, qui a son propre installeur), `absent`.
Provenance = Literal["paquet", "poste", "absent"]

#: Trouve une commande sur le `PATH` (la signature de `shutil.which`).
Resolveur = Callable[[str], str | None]

#: Lit la version du binaire à ce chemin, ou `None` (celle de `clients_du_poste.lire_version`).
Versionneur = Callable[[str], str | None]


class VersionIllisible(Exception):
    """La source de la version ne la donne pas — le message dit pourquoi."""


def forme_du_produit(environnement: Mapping[str, str] | None = None) -> Forme:
    """`installation` quand le paquet l'a dit (`MAESTRO_INSTALLATION`), `depot` sinon.

    Le réglage qui range la configuration (`maestro.emplacements.installation`), et lui seul :
    le produit ne se présente jamais autrement qu'il ne range ce qu'il écrit. Une valeur
    illisible lève `InstallationIllisible`, sans être rabattue sur l'une ou l'autre forme.
    """
    return "installation" if installation(environnement) else "depot"


def version_du_depot(depot: Path) -> str:
    """La version que `apps/desktop/package.json` donne au paquet. Lève `VersionIllisible`."""
    source = depot / SOURCE_DE_LA_VERSION
    try:
        version = json.loads(source.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError) as erreur:
        raise VersionIllisible(f"{SOURCE_DE_LA_VERSION} illisible ({erreur})") from erreur
    if not isinstance(version, str) or not version.strip():
        raise VersionIllisible(f"{SOURCE_DE_LA_VERSION} ne porte pas de version")
    return version.strip()


def ecrire_version(depot: Path, produit: Path, version: str | None = None) -> str:
    """Écrit la version du paquet à la racine du produit assemblé, et la rend.

    Appelé par l'assembleur : c'est ce fichier que l'API installée et la coque relisent. Une
    version illisible arrête l'assemblage (`VersionIllisible`) plutôt que de poser un produit
    qui ne saurait pas dire ce qu'il est.

    `version` remplace celle de la source quand le paquet est construit sous une autre
    (`construire.sh --version`, qui la donne aussi à la coque) : deux paquets du même code, pour
    vérifier qu'une installation passe de l'un à l'autre (#644).
    """
    if version is None:
        version = version_du_depot(depot)
    elif not version.strip():
        raise VersionIllisible("la version demandée est vide")
    version = version.strip()
    (produit / FICHIER_VERSION).write_text(
        json.dumps({"version": version}, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return version


@dataclass(frozen=True)
class VersionProduit:
    """La version du produit qui tourne, ou `None` avec le relevé qui dit pourquoi."""

    version: str | None
    releve: str = ""


def version_du_produit(racine: Path, forme: Forme) -> VersionProduit:
    """`version.json` s'il a été écrit à l'assemblage ; sinon la source d'un clone.

    Une installation sans `version.json` le dit : elle ne se rabat pas sur une source qu'un
    paquet ne porte pas.
    """
    ecrite = racine / FICHIER_VERSION
    if ecrite.is_file():
        try:
            version = json.loads(ecrite.read_text(encoding="utf-8")).get("version")
        except (OSError, ValueError, AttributeError) as erreur:
            return VersionProduit(None, f"{FICHIER_VERSION} illisible ({erreur})")
        if isinstance(version, str) and version.strip():
            return VersionProduit(version.strip())
        return VersionProduit(None, f"{FICHIER_VERSION} ne porte pas de version")
    if forme == "installation":
        return VersionProduit(None, f"{FICHIER_VERSION} absent de l'installation")
    try:
        return VersionProduit(version_du_depot(racine))
    except VersionIllisible as erreur:
        return VersionProduit(None, str(erreur))


def dossier_du_sdk() -> Path | None:
    """Le dossier du paquet `claude_agent_sdk`, trouvé sans l'importer — `None` s'il manque."""
    spec = importlib.util.find_spec("claude_agent_sdk")
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).resolve().parent


def nom_du_cli() -> str:
    """`claude.exe` sous Windows, où le SDK refuse le `claude.cmd` de npm ; `claude` ailleurs."""
    return "claude.exe" if sys.platform == "win32" else "claude"


def cli_claude_resolu(
    *, sdk: Path | None = None, resolveur: Resolveur = shutil.which
) -> tuple[Provenance, Path | None]:
    """Le CLI `claude` que le produit lance, et d'où il vient.

    Le choix est celui que le SDK fait sans `cli_path` et qu'il documente : le CLI **embarqué**
    dans son paquet (`_bundled/`), sinon le `claude` du `PATH`. Le fournisseur le reprend pour
    nommer la commande que le confinement lance (`maestro.providers.claude._commande_du_cli`) :
    c'est ici qu'il vit, une seule fois.
    """
    nom = nom_du_cli()
    dossier = sdk if sdk is not None else dossier_du_sdk()
    if dossier is not None:
        embarque = dossier / "_bundled" / nom
        if embarque.is_file():
            return "paquet", embarque
    trouve = resolveur(nom)
    if trouve:
        return "poste", Path(trouve)
    return "absent", None


@dataclass(frozen=True)
class CliClaude:
    """Le CLI que le produit lance : sa provenance, son chemin, la version qu'il a dite."""

    provenance: Provenance
    chemin: Path | None
    version: str | None


@dataclass(frozen=True)
class EtatInstallation:
    """Ce que `GET /api/installation` rend."""

    forme: Forme
    produit: VersionProduit
    cli: CliClaude

    def en_json(self) -> dict[str, Any]:
        return {
            "forme": self.forme,
            "produit": {"version": self.produit.version, "releve": self.produit.releve},
            "cli_claude": {
                "provenance": self.cli.provenance,
                # En `/`, comme les autres chemins que l'API montre (#1614).
                "chemin": self.cli.chemin.as_posix() if self.cli.chemin is not None else None,
                "version": self.cli.version,
            },
        }


@dataclass
class LecteurInstallation:
    """Lit l'état de l'installation à la demande — la seule porte vers le poste de ce sujet.

    Ses sorties hors du process (l'environnement, le paquet du SDK, le `PATH`, le binaire lancé
    avec `--version`) sont des paramètres, pour que les tests décrivent un poste au lieu de lire
    le vrai ; `environnement` à `None`, c'est celui du process, relu à chaque lecture. La version
    lue est retenue par **identité du binaire** (chemin, date de modification, taille) : rouvrir
    les Paramètres ne relance rien, et un CLI remplacé est relu. Une lecture en échec n'est pas
    retenue — elle peut réussir à la suivante.
    """

    racine: Path = field(default_factory=racine_de_la_copie)
    environnement: Mapping[str, str] | None = None
    sdk: Path | None = None
    resolveur: Resolveur = shutil.which
    versionneur: Versionneur = lire_version
    _versions: dict[tuple[str, int, int], str] = field(default_factory=dict, repr=False)

    def lire(self) -> EtatInstallation:
        forme = forme_du_produit(self.environnement)
        provenance, chemin = cli_claude_resolu(sdk=self.sdk, resolveur=self.resolveur)
        return EtatInstallation(
            forme=forme,
            produit=version_du_produit(self.racine, forme),
            cli=CliClaude(provenance, chemin, self._version_du_cli(chemin)),
        )

    def _version_du_cli(self, chemin: Path | None) -> str | None:
        if chemin is None:
            return None
        try:
            etat = chemin.stat()
        except OSError:
            return None
        cle = (str(chemin), etat.st_mtime_ns, etat.st_size)
        if cle not in self._versions:
            lue = self.versionneur(str(chemin))
            if lue is None:
                return None
            self._versions[cle] = lue
        return self._versions[cle]
