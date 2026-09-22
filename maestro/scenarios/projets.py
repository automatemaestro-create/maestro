"""Les projets **jetables** du banc : où ils naissent, ce qu'on y sème, ce qu'on y lit (#1148).

Un scénario de référence a besoin d'un projet à lui : le banc en déclare un par
scénario, le sème avec ce que l'oracle devra constater, et n'y touche plus. Rien
n'est joué sur un projet de l'utilisateur — un scénario qui vide un dossier n'a
pas à choisir lequel.

**Où.** Sous `~/maestro-scenarios/<horodatage>/<scénario>`, et pas ailleurs pour
deux raisons qui se cumulent : `valider_racine` refuse le dépôt de Maestro
lui-même (EF-38) et refuse `AppData`, donc le `TMPDIR` d'un poste Windows
(mesuré en #221). Un dossier visible du profil utilisateur passe les deux, se
retrouve à l'œil nu quand un scénario est rouge, et s'efface d'un geste.
`MAESTRO_SCENARIOS_ATELIER` le déplace pour qui veut un autre disque.

**Ce qui reste après le passage.** Les dossiers, par défaut. Ce sont les
**pièces** du verdict : un S1 rouge se comprend en regardant ce qui est resté
dans la racine, et un S2 rouge en lançant l'application à la main. Le rapport dit
où ils sont, et `--nettoyer` les retire avec les déclarations pour qui joue le
banc en boucle.

**Le périmètre n'est pas recopié.** L'oracle de S1 — « le dossier est vide, hors
périmètre exclu » — se lit avec `maestro.projets.perimetre.exclusions` et
`EXCLUS_DEFAUT`, c'est-à-dire avec la règle que le produit applique. Une seconde
liste d'exclusions écrite ici finirait par juger vert un run qui a mangé le `.env`
(#830).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from maestro.fichiers import retirer_arbre
from maestro.projets.modele import EXCLUS_DEFAUT, Perimetre
from maestro.projets.perimetre import exclusions

#: Le dossier où naissent les projets jetables — déplaçable, jamais deviné.
VARIABLE_ATELIER = "MAESTRO_SCENARIOS_ATELIER"

#: Le nom du dossier d'atelier sous le profil utilisateur (voir l'en-tête).
NOM_ATELIER = "maestro-scenarios"


def racine_atelier(environnement: Mapping[str, str] | None = None) -> Path:
    """Le dossier des ateliers du banc — `MAESTRO_SCENARIOS_ATELIER`, sinon le profil."""
    env = os.environ if environnement is None else environnement
    regle = (env.get(VARIABLE_ATELIER) or "").strip()
    return Path(regle).expanduser() if regle else Path.home() / NOM_ATELIER


class Atelier:
    """Les racines jetables d'un passage : un dossier par scénario, sous l'horodatage."""

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @classmethod
    def pour(
        cls, horodatage: str, *, environnement: Mapping[str, str] | None = None
    ) -> Atelier:
        """L'atelier d'un passage daté."""
        return cls(racine_atelier(environnement) / horodatage)

    @property
    def racine(self) -> Path:
        """Le dossier de l'atelier."""
        return self._racine

    def dossier(self, nom: str) -> Path:
        """Le dossier d'un scénario, créé s'il manque — vide, prêt à être semé."""
        chemin = self._racine / nom
        chemin.mkdir(parents=True, exist_ok=True)
        return chemin

    def retirer(self) -> bool:
        """Efface l'atelier — `--nettoyer`, jamais d'office (voir l'en-tête).

        Par `retirer_arbre` et non un `rmtree` nu : un projet que le run a mis
        sous Git porte des objets en lecture seule, qu'un `rmtree` laisse derrière
        lui en amputant l'arbre sans le dire (#707, #992). Le geste vit une fois
        dans `maestro.fichiers`, jamais recopié ici.
        """
        return retirer_arbre(self._racine)


# --- Ce qu'on sème ---------------------------------------------------------


def semer_a_vider(racine: Path) -> tuple[str, ...]:
    """Un dossier **plein**, avec un périmètre exclu à épargner — la matière de S1.

    Rend les chemins relatifs que le périmètre du projet exclut et que le run ne
    doit donc pas toucher (`.env` ici, par `EXCLUS_DEFAUT`). Ce sont les témoins
    du « hors périmètre exclu » de l'oracle : sans eux, un run qui efface tout,
    secrets compris, passerait pour un succès.
    """
    (racine / "notes").mkdir(parents=True, exist_ok=True)
    (racine / "notes" / "brouillon.txt").write_text("à jeter\n", encoding="utf-8")
    (racine / "notes" / "vieux.md").write_text("# vieux\n", encoding="utf-8")
    (racine / "rapport.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (racine / "lisez-moi.txt").write_text("ce dossier doit finir vide\n", encoding="utf-8")
    (racine / ".env").write_text("SECRET=ne-pas-toucher\n", encoding="utf-8")
    return temoins_exclus(racine)


def semer_projet_existant(racine: Path) -> None:
    """Un petit projet Python réel — ce qu'on **reprend** dans S3 et S4.

    Assez de matière pour que l'analyse du projet (#1039) ait de quoi recommander
    une équipe : un `pyproject.toml`, des sources, un README. C'est la même forme
    que le projet du parcours HTTP de #1146, et pour la même raison — un dossier
    vide ne fait proposer personne.
    """
    (racine / "src").mkdir(parents=True, exist_ok=True)
    (racine / "src" / "app.py").write_text("print('salut')\n", encoding="utf-8")
    (racine / "src" / "modele.py").write_text("X = 1\n", encoding="utf-8")
    (racine / "pyproject.toml").write_text(
        '[project]\nname = "depensio"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (racine / "README.md").write_text(
        "# Dépensio\n\nSuivi de dépenses personnelles, en Python.\n", encoding="utf-8"
    )


# --- Ce qu'on y lit --------------------------------------------------------


def _perimetre() -> Perimetre:
    """Le périmètre d'un projet déclaré sans motif particulier — celui du produit."""
    return Perimetre(exclus=EXCLUS_DEFAUT)


def temoins_exclus(racine: Path) -> tuple[str, ...]:
    """Les chemins que le périmètre du projet exclut, tels qu'ils sont sur le disque."""
    return tuple(exclu.chemin for exclu in exclusions(racine, _perimetre()))


def restes(racine: Path) -> tuple[str, ...]:
    """Ce qui reste dans la racine **hors** périmètre exclu, en chemins relatifs POSIX.

    Vide = le dossier est vide au sens de l'oracle de S1. Un chemin exclu n'est
    pas descendu : il compte pour une entrée absente, exactement comme le
    conteneur le masque d'un seul geste (`maestro.projets.perimetre`).
    """
    if not racine.is_dir():
        return ()
    exclus = set(temoins_exclus(racine))
    trouves: list[str] = []
    for chemin in sorted(racine.rglob("*")):
        relatif = chemin.relative_to(racine).as_posix()
        if any(relatif == exclu or relatif.startswith(f"{exclu}/") for exclu in exclus):
            continue
        trouves.append(relatif)
    return tuple(trouves)


def manquants(racine: Path, temoins: tuple[str, ...]) -> tuple[str, ...]:
    """Ceux des `temoins` que le run a fait disparaître — le périmètre exclu violé."""
    return tuple(nom for nom in temoins if not (racine / nom).exists())
