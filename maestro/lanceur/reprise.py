"""La reprise de ce qu'une installation antérieure a écrit sous son dossier (#1620).

Jusqu'à 0.1.0, le produit installé écrivait sa configuration sous
`resources/produit/core/` — projets déclarés, réglages, coffre, conversations —, dans le
dossier qu'une mise à jour efface puis réécrit. L'installeur en recopie le contenu sous
`~/.maestro/reprise/` avant de retirer l'ancienne version (`apps/desktop/installer.nsh`) ;
ce module le range, au premier démarrage de la version suivante, là où le produit écrit
désormais : `~/.maestro/configuration/` (`maestro.emplacements.configuration_du_poste`).

## Les règles

- **Une seule fois.** Faite, la reprise laisse un témoin (`reprise.json`). Tant qu'il est
  là, rien ne se rejoue : ni au démarrage suivant, ni sur une copie qu'une mise à jour
  ultérieure poserait — l'installeur, lui, ne recopie plus rien dès qu'il le voit.
- **Rien d'écrasé.** Un fichier déjà présent sous l'état du poste est gardé ; l'ancien
  reste dans la copie, et le bilan le nomme.
- **Rien de doublé.** Un fichier identique à celui que le paquet livre est un fichier du
  paquet, pas une donnée : le recopier masquerait les défauts des versions suivantes. Les
  `README.md` et `.gitignore` de tête d'un dépôt sont du paquet par nature.
- **Le coffre se reprend entier, ou pas du tout.** `secrets/` chiffre ses fichiers avec la
  clé locale qu'il porte (`.cle`) : mêler deux coffres rendrait l'un des deux illisible.
- **Le `.env`.** Ce que la personne avait ajouté au `.env` de l'ancienne installation — les
  variables que le nouveau paquet ne pose pas à l'identique — passe dans le sien, sous
  l'état du poste (`maestro.emplacements.env_du_poste`), sauf celles qu'elle y a déjà.

Rien n'est retiré de la copie. Une reprise interrompue (disque plein, fichier verrouillé)
ne pose pas son témoin et se rejoue au démarrage suivant, sans rien doubler : ce qui est
déjà repris est présent, donc gardé.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values

from maestro.emplacements import (
    NOM_ENV,
    NOM_LIVREE,
    configuration_du_poste,
    env_du_poste,
    racine_du_code,
    racine_livree,
    reprise_du_poste,
)

#: Le témoin d'une reprise faite, dans le dossier de la copie. L'installeur le lit aussi.
TEMOIN = "reprise.json"

#: Le dépôt qui se reprend d'un bloc : ses fichiers ne se lisent qu'avec la clé qu'il porte.
DEPOT_INDIVISIBLE = "secrets"

#: Ce que chaque dépôt du paquet porte en tête, et qui n'est jamais une donnée.
NOMS_DU_PAQUET = frozenset({"README.md", ".gitignore"})

#: L'en-tête des réglages repris dans le `.env` de la personne.
ENTETE_ENV = "# Repris du .env de l'installation précédente"


@dataclass(frozen=True)
class Bilan:
    """Ce que la reprise a fait — ou qu'elle était déjà faite."""

    #: Les fichiers repris, relatifs à la configuration (`projets/projets.json`…).
    reprises: tuple[str, ...] = ()
    #: Ceux que l'état du poste avait déjà : gardés, l'ancien restant dans la copie.
    conservees: tuple[str, ...] = ()
    #: Les variables reprises dans le `.env` de la personne.
    reglages: tuple[str, ...] = ()
    #: Vrai quand le témoin était là : rien n'a été regardé.
    deja_faite: bool = False
    #: La copie d'où l'on reprend.
    source: Path | None = None
    #: Là où l'on range.
    cible: Path | None = None

    @property
    def rien(self) -> bool:
        """Rien n'a bougé ni n'est à dire : reprise déjà faite, ou rien à reprendre."""
        return self.deja_faite or not (self.reprises or self.conservees or self.reglages)


def reprendre(
    source: Path,
    cible: Path,
    livree: Path,
    *,
    env_du_produit: Path | None = None,
    env_de_la_personne: Path | None = None,
    maintenant: datetime | None = None,
) -> Bilan:
    """Range sous `cible` ce que la copie `source` porte, selon les règles du module.

    `source` est la copie de l'installeur (`<source>/core/…`, `<source>/.env`), `cible`
    la configuration du poste, `livree` le `core/` que le paquet installé embarque.
    Lève `OSError` sur une écriture impossible, témoin non posé : la reprise se
    rejouera.
    """
    if (source / TEMOIN).is_file():
        return Bilan(deja_faite=True, source=source, cible=cible)

    reprises: list[str] = []
    conservees: list[str] = []
    anciens = source / NOM_LIVREE
    if anciens.is_dir():
        for depot in sorted(chemin for chemin in anciens.iterdir() if chemin.is_dir()):
            fichiers = _donnees(depot, anciens, livree)
            if depot.name == DEPOT_INDIVISIBLE and fichiers and _coffre_present(
                cible / depot.name, cible, livree
            ):
                conservees.extend(relatif for relatif, _ in fichiers)
                continue
            for relatif, fichier in fichiers:
                destination = cible / relatif
                if destination.exists():
                    # Identique : une reprise interrompue l'avait déjà rangé — rien à dire.
                    if not _memes_octets(fichier, destination):
                        conservees.append(relatif)
                    continue
                _copier(fichier, destination)
                reprises.append(relatif)

    reglages = _reprendre_env(source / NOM_ENV, env_du_produit, env_de_la_personne)

    date = (maintenant or datetime.now(UTC)).isoformat(timespec="seconds")
    _ecrire_temoin(
        source / TEMOIN,
        {
            "faite_le": date,
            "cible": str(cible),
            "reprises": reprises,
            "conservees": conservees,
            "reglages": reglages,
        },
    )
    return Bilan(
        reprises=tuple(reprises),
        conservees=tuple(conservees),
        reglages=tuple(reglages),
        source=source,
        cible=cible,
    )


def reprendre_du_poste(environ: Mapping[str, str] | None = None) -> Bilan:
    """La reprise du poste : la copie de l'installeur vers la configuration du poste."""
    return reprendre(
        reprise_du_poste(),
        configuration_du_poste(),
        racine_livree(),
        env_du_produit=racine_du_code() / NOM_ENV,
        env_de_la_personne=env_du_poste(environ),
    )


def _donnees(depot: Path, anciens: Path, livree: Path) -> list[tuple[str, Path]]:
    """Les fichiers de `depot` qui sont des données, relatifs à `anciens`, triés."""
    trouves: list[tuple[str, Path]] = []
    for fichier in sorted(chemin for chemin in depot.rglob("*") if chemin.is_file()):
        relatif = fichier.relative_to(anciens)
        if not _du_paquet(fichier, relatif, livree):
            trouves.append((relatif.as_posix(), fichier))
    return trouves


def _du_paquet(fichier: Path, relatif: Path, livree: Path) -> bool:
    """`fichier` est-il un fichier du paquet plutôt qu'une donnée ?"""
    if len(relatif.parts) == 2 and relatif.name in NOMS_DU_PAQUET:
        return True
    livre = livree / relatif
    return livre.is_file() and _memes_octets(livre, fichier)


def _memes_octets(un: Path, autre: Path) -> bool:
    try:
        return un.read_bytes() == autre.read_bytes()
    except OSError:
        return False


def _coffre_present(coffre: Path, cible: Path, livree: Path) -> bool:
    """L'état du poste a-t-il déjà un coffre (un fichier qui n'est pas du paquet) ?"""
    if not coffre.is_dir():
        return False
    return any(
        not _du_paquet(fichier, fichier.relative_to(cible), livree)
        for fichier in coffre.rglob("*")
        if fichier.is_file()
    )


def _copier(fichier: Path, destination: Path) -> None:
    """Copie `fichier` vers `destination` d'un seul geste : jamais un fichier à moitié."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    provisoire = destination.with_name(destination.name + ".reprise")
    shutil.copy2(fichier, provisoire)
    os.replace(provisoire, destination)


def _reprendre_env(
    ancien: Path, du_produit: Path | None, de_la_personne: Path | None
) -> list[str]:
    """Ajoute au `.env` de la personne ce qu'elle avait posé dans l'ancien. Rend les noms."""
    if de_la_personne is None or not ancien.is_file():
        return []
    anciennes = {cle: val for cle, val in dotenv_values(ancien).items() if val is not None}
    livrees = (
        dotenv_values(du_produit) if du_produit is not None and du_produit.is_file() else {}
    )
    siennes = dotenv_values(de_la_personne) if de_la_personne.is_file() else {}
    a_reprendre = [
        (cle, val)
        for cle, val in anciennes.items()
        if livrees.get(cle) != val and cle not in siennes
    ]
    if not a_reprendre:
        return []
    existant = de_la_personne.read_text(encoding="utf-8") if de_la_personne.is_file() else ""
    lignes = [ENTETE_ENV, *(f"{cle}={_valeur_env(val)}" for cle, val in a_reprendre)]
    separation = "" if not existant or existant.endswith("\n") else "\n"
    de_la_personne.parent.mkdir(parents=True, exist_ok=True)
    provisoire = de_la_personne.with_name(de_la_personne.name + ".reprise")
    provisoire.write_text(existant + separation + "\n".join(lignes) + "\n", encoding="utf-8")
    os.replace(provisoire, de_la_personne)
    return [cle for cle, _ in a_reprendre]


def _valeur_env(valeur: str) -> str:
    """`valeur` entre guillemets, échappée comme `python-dotenv` la relit."""
    echappee = valeur.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{echappee}"'


def _ecrire_temoin(chemin: Path, contenu: dict[str, object]) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    provisoire = chemin.with_name(chemin.name + ".tmp")
    provisoire.write_text(
        json.dumps(contenu, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(provisoire, chemin)


__all__ = ["TEMOIN", "Bilan", "reprendre", "reprendre_du_poste"]
