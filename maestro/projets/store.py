"""Dépôt des projets déclarés : un fichier JSON par projet (ticket #221, EF-35).

Même patron que les autres dépôts de configuration du POC
(`maestro.agents.store`, `maestro.agents.capacity`) : un fichier `<id>.json` par
projet sous `core/projets/` (ou `MAESTRO_PROJETS_DIR`), écrit atomiquement,
relu à chaud. En V1 il passera en base (table `PROJECT`, docs/03) sans changer
ce contrat.

Le dépôt est le seul point d'entrée qui **écrit** un projet, et c'est à dessein :
c'est lui qui appelle la validation de racine (EF-38) et la détection du VCS, de
sorte qu'aucun projet stocké ne porte une racine non canonicalisée, hors
périmètre autorisé, ou un `vcs` inventé. Un projet posé à la main dans le
fichier reste possible — le POC n'a pas de gardien à l'écriture disque — mais
tout ce qui passe par le code passe par `creer`/`ecrire`.

Deux méthodes seulement touchent au dossier de l'utilisateur, et toutes deux
parce qu'on le leur a demandé : `creer(origine="nouveau")` crée la racine
manquante (besoin A de docs/24 §2.1), et `versionner` la met sous Git (#704).
Les lectures, elles, ne créent jamais rien — `detecter_vcs` constate Git sans
l'imposer (EF-38), et c'est ce qui rend le second geste explicite.

Un seul écrivain à la fois au POC (l'API Control Tower, #223) : le dépôt ne
porte pas de verrou de concurrence, comme ses voisins.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from maestro.config import Settings, load_settings
from maestro.projets.modele import (
    ID_PROJET,
    ORIGINES,
    Perimetre,
    Projet,
    nouvel_id,
)
from maestro.projets.racine import canonique, detecter_vcs, valider_racine
from maestro.projets.versionnement import initialiser_depot

#: Nombre de tirages d'identifiant avant d'abandonner. Une collision sur 8 hex
#: est déjà improbable ; dix de suite signalerait un dépôt en panne, pas de la
#: malchance — mieux vaut lever que boucler.
_TIRAGES_ID_MAX = 10


class ProjetStore:
    """Dépôt des projets, sur fichiers (`<racine du dépôt>/<id>.json`).

    ⚠ Deux « racines » se croisent ici, et elles n'ont rien à voir : `self.racine`
    est le dossier **du dépôt** (où sont les JSON), `Projet.racine` est le dossier
    **du projet de l'utilisateur** sur son disque.
    """

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @property
    def racine(self) -> Path:
        """La racine du dépôt (un fichier JSON par projet) — pas celle d'un projet."""
        return self._racine

    @classmethod
    def default(cls, settings: Settings | None = None) -> ProjetStore:
        """Le dépôt configuré : `MAESTRO_PROJETS_DIR`, sinon `core/projets/` du dépôt."""
        settings = settings or load_settings()
        if settings.projets_dir:
            return cls(Path(settings.projets_dir))
        return cls(Path(__file__).resolve().parents[2] / "core" / "projets")

    def ids(self) -> tuple[str, ...]:
        """Les identifiants stockés, triés — l'ordre de listing, déterministe."""
        if not self._racine.is_dir():
            return ()
        return tuple(
            sorted(
                chemin.stem
                for chemin in self._racine.glob("*.json")
                if ID_PROJET.match(chemin.stem)
            )
        )

    def lister(self) -> tuple[Projet, ...]:
        """Les projets stockés, dans l'ordre des identifiants."""
        return tuple(
            projet for id_ in self.ids() if (projet := self.lire(id_)) is not None
        )

    def lire(self, id_: str) -> Projet | None:
        """Le projet `id_`, ou None s'il n'est pas dans le dépôt."""
        chemin = self._chemin(id_)
        if not chemin.is_file():
            return None
        projet = Projet.from_dict(json.loads(chemin.read_text(encoding="utf-8")))
        # Le nom de fichier fait foi sur l'identifiant, comme pour les agents :
        # un projet recopié sous un autre id reste adressé par son fichier.
        return replace(projet, id=id_)

    def par_racine(self, racine: Path | str) -> Projet | None:
        """Le projet déclaré sur cette racine, ou None — deux projets n'en partagent pas une.

        La comparaison se fait sur les `Path` **canonicalisés** (`canonique`),
        donc à la casse près sous Windows et sans se laisser prendre à deux
        orthographes du même dossier : `D:/Projets/App`, `d:/projets/app` et
        `\\\\?\\D:\\projets\\app` sont le même projet.

        Un fichier illisible du dépôt est **sauté** plutôt que de faire échouer
        la question posée : « cette racine est-elle prise ? » — un JSON corrompu
        n'occupe aucune racine, et le faire lever ici interdirait de créer le
        moindre projet tant qu'il traîne.
        """
        try:
            cible = canonique(racine)
        except OSError:  # pragma: no cover - chemin illisible pour l'OS
            return None
        for id_ in self.ids():
            try:
                projet = self.lire(id_)
            except (ValueError, OSError):
                continue
            if projet is not None and canonique(projet.racine) == cible:
                return projet
        return None

    def creer(
        self,
        nom: str,
        racine: Path | str,
        *,
        origine: str = "existant",
        inclus: tuple[str, ...] | None = None,
        exclus: tuple[str, ...] | None = None,
    ) -> Projet:
        """Déclare un projet : racine validée, VCS détecté, identifiant engendré.

        C'est le chemin nominal de la déclaration (celui que servira l'API #223).
        La racine est **canonicalisée et confrontée aux racines interdites**
        (EF-38) : un refus lève `RacineRefusee`, motif à l'appui, et rien n'est
        écrit. `origine="nouveau"` **crée** le dossier s'il manque (le besoin A de
        docs/24 §2.1) ; `origine="existant"` exige qu'il soit là. Le `vcs` n'est
        jamais demandé à l'appelant : il est **constaté** sur le disque, et vaut
        `None` pour un dossier non versionné — qui reste déclarable.
        """
        if origine not in ORIGINES:
            raise ValueError(
                f"origine invalide : {origine!r} ({' ou '.join(sorted(ORIGINES))} attendu)."
            )
        chemin = valider_racine(racine, creer=origine == "nouveau")
        perimetre = Perimetre(
            inclus=inclus if inclus is not None else Perimetre().inclus,
            exclus=exclus if exclus is not None else Perimetre().exclus,
        )
        projet = Projet(
            id=self._id_libre(),
            nom=nom,
            racine=chemin.as_posix(),
            origine=origine,
            vcs=detecter_vcs(chemin),
            perimetre=perimetre,
        )
        return self.ecrire(projet)

    def ecrire(self, projet: Projet) -> Projet:
        """Persiste `projet` (création ou remplacement intégral) et le renvoie daté.

        Écriture atomique (fichier temporaire puis renommage) : un projet
        n'apparaît dans le dépôt que complet. Lève `RacineRefusee` si la racine
        n'est plus admissible et `ValueError` si le projet est invalide (id hors
        slug, nom vide, origine inconnue, racine déjà prise par un autre projet)
        — le dépôt ne stocke jamais un projet inexploitable.
        """
        propre = self._valide(projet)
        existant = self.lire(propre.id)
        maintenant = _maintenant()
        propre = replace(
            propre,
            cree_le=existant.cree_le if existant is not None else maintenant,
            modifie_le=maintenant,
        )
        self._racine.mkdir(parents=True, exist_ok=True)
        chemin = self._chemin(propre.id)
        temporaire = chemin.with_suffix(".json.tmp")
        temporaire.write_text(
            json.dumps(propre.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporaire, chemin)
        return propre

    def versionner(self, id_: str, *, branche: str = "") -> Projet:
        """Met le projet `id_` sous Git s'il ne l'est pas, et enregistre son `Vcs` (#704).

        Le geste **explicite** que `detecter_vcs` s'interdit : rien d'autre dans
        le code n'initialise un dépôt dans le dossier de quelqu'un. La racine est
        revalidée puis initialisée par `maestro.projets.versionnement`, et c'est
        `detecter_vcs` qui constate le résultat — le `Vcs` stocké est celui du
        disque, jamais un `Vcs` fabriqué ici.

        Un projet **déjà versionné** est rendu tel quel : aucune commande Git,
        aucune écriture, pas même un `modifie_le` rafraîchi. Un projet déclaré
        non versionné dont la racine est passée sous Git entre-temps est
        **rattrapé** sans qu'aucun dépôt soit créé — c'est la déclaration qui
        avait pris du retard, pas le disque.

        Lève `RacineRefusee` si la racine n'est plus admissible (EF-38),
        `VersionnementRefuse` motivée si l'initialisation échoue — la racine
        restant alors dans l'état d'avant — et `ValueError` si le projet n'est
        pas dans le dépôt.
        """
        projet = self.lire(id_)
        if projet is None:
            raise ValueError(f"projet inconnu : {id_!r} — rien à mettre sous Git.")
        if projet.versionne:
            return projet
        return self.ecrire(replace(projet, vcs=initialiser_depot(projet.racine, branche=branche)))

    def supprimer(self, id_: str) -> bool:
        """Retire le projet `id_` du dépôt ; False s'il n'y était pas (rien à faire).

        Ne touche **jamais** au dossier du projet sur le disque : oublier un
        projet n'est pas supprimer le travail de l'utilisateur.
        """
        chemin = self._chemin(id_)
        if not chemin.is_file():
            return False
        chemin.unlink()
        return True

    def _valide(self, projet: Projet) -> Projet:
        """Le projet normalisé (racine canonicalisée), ou `ValueError` si invalide."""
        if not ID_PROJET.match(projet.id):
            raise ValueError(
                f"identifiant de projet invalide : {projet.id!r} (slug [a-z0-9_-] attendu)."
            )
        if not projet.nom.strip():
            raise ValueError(f"nom vide pour le projet {projet.id!r}.")
        if projet.origine not in ORIGINES:
            raise ValueError(
                f"origine invalide : {projet.origine!r} "
                f"({' ou '.join(sorted(ORIGINES))} attendu)."
            )
        chemin = valider_racine(projet.racine)
        occupant = self.par_racine(chemin)
        if occupant is not None and occupant.id != projet.id:
            raise ValueError(
                f"racine déjà déclarée par le projet {occupant.id!r} "
                f"({occupant.nom!r}) : {chemin}."
            )
        return replace(projet, nom=projet.nom.strip(), racine=chemin.as_posix())

    def _id_libre(self) -> str:
        """Un identifiant engendré qui n'écrase aucun projet du dépôt."""
        for _ in range(_TIRAGES_ID_MAX):
            candidat = nouvel_id()
            if not self._chemin(candidat).exists():
                return candidat
        raise RuntimeError(  # pragma: no cover - dépôt en panne, pas de la malchance
            f"impossible d'engendrer un identifiant de projet libre en "
            f"{_TIRAGES_ID_MAX} tirages."
        )

    def _chemin(self, id_: str) -> Path:
        """Le fichier de stockage de `id_`, identifiant validé (jamais un chemin arbitraire)."""
        if not ID_PROJET.match(id_):
            raise ValueError(
                f"identifiant de projet invalide : {id_!r} (slug [a-z0-9_-] attendu)."
            )
        return self._racine / f"{id_}.json"


def _maintenant() -> str:
    """L'horodatage d'écriture d'un projet (ISO 8601, UTC, à la seconde)."""
    return datetime.now(tz=UTC).isoformat(timespec="seconds")
