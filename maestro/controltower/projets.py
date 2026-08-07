"""Service des projets pour la Control Tower : CRUD et explorateur de dossiers (#223).

Le lot 1 (#221) a posé l'entité `Projet`, sa racine validée et sa persistance —
mais **personne ne pouvait l'atteindre** : la Control Tower n'avait aucune route
pour lire ou écrire un projet. Ce module est la pièce que les routes
`/api/projets` appellent, au patron de `maestro.controltower.executions` : le
service tient les formes JSON et les refus, `app.py` ne fait que les traduire en
codes HTTP.

Deux responsabilités :

- **le CRUD** (`lister`, `detail`, `creer`, `remplacer`, `supprimer`) — une
  couche mince au-dessus de `ProjetStore`, qui reste le seul écrivain. La forme
  servie est celle de `Projet.to_dict` (docs/24 §2.3) : aucune seconde
  définition du contrat, donc aucune dérive possible entre le fichier stocké et
  la réponse HTTP ;
- **l'explorateur de dossiers** (`explorer`) — la brique sans laquelle l'écran
  Projets ne peut pas exister : un navigateur ne livre jamais de chemin absolu,
  c'est donc le backend, qui tourne déjà sur le poste, qui énumère
  ([docs/05 §2.7](../../docs/05-interface-control-tower.md)).

**L'explorateur n'est pas un « lis n'importe quel chemin ».** L'API n'a pas
d'authentification au POC (CORS `*`, limite connue de #182 et
[docs/24 §6](../../docs/24-projets-locaux-et-poste-de-travail.md), durcie en
Phase 9) : une route qui énumère le disque est donc à traiter comme une
frontière, pas comme un confort. Elle en porte deux, superposées :

1. **les racines explorables** — le dossier utilisateur par défaut, remplaçable
   par `MAESTRO_EXPLORATEUR_RACINES`, et **toujours** les racines des projets
   déjà déclarés (elles ont passé `valider_racine`, elles sont explorables par
   construction). Hors d'elles, refus ;
2. **les zones interdites** de `maestro.projets.racine` (`.ssh`, `AppData`,
   dossiers système, dépôt de Maestro) — qui s'appliquent *à l'intérieur* des
   racines : le dossier utilisateur est traversable, `~/.ssh` ne l'est pas.

Un refus **porte toujours son motif** (`RacineRefusee.motif`) et jamais une
liste vide : « ce dossier n'a pas de sous-dossier » et « je refuse de regarder
là » sont deux réponses différentes, et les confondre est ce qui rend un
explorateur inutilisable. `STATUT_HTTP_PAR_MOTIF` donne à chaque motif son code
HTTP — c'est la seule connaissance de HTTP de ce module, et elle est ici plutôt
que dans `app.py` pour que motif et code se lisent au même endroit.
"""

from __future__ import annotations

import os
import string
from pathlib import Path
from typing import Any

from maestro.config import Settings, load_settings
from maestro.projets import (
    ORIGINES,
    Perimetre,
    Projet,
    ProjetStore,
    RacineRefusee,
    canonique,
    chemin_dans_racine,
    detecter_vcs,
    valider_racine,
    verifier_zone_interdite,
)

#: Nombre maximum de sous-dossiers rendus par un appel à l'explorateur. Un
#: dossier de dépôts à 50 000 entrées existe (un `node_modules`, un cache) et
#: rien ne justifie d'en faire une réponse HTTP. Le dépassement est **dit**
#: (`tronque: true`), jamais silencieux : une troncature muette se lit comme un
#: dossier complet.
LIMITE_DOSSIERS = 500

#: Nombre de dossiers « récents » proposés sur la page d'entrée (#278) — les
#: parents des projets les plus récemment déclarés. Cinq : de quoi couvrir les
#: quelques endroits où l'on range ses dépôts, sans noyer les autres origines.
LIMITE_RECENTS = 5

#: Le code HTTP de chaque motif de refus. Le défaut est **422** (la saisie est
#: en cause) ; **403** dit « je refuse de regarder là » (frontière franchie, pas
#: saisie fautive) et **404** « ce chemin n'existe pas ». Un motif absent de la
#: table retombe sur 422 — ajouter un motif ne peut donc pas rendre un 500.
STATUT_HTTP_PAR_MOTIF: dict[str, int] = {
    "hors-racines-explorables": 403,
    "aucune-racine-explorable": 403,
    "chemin-sensible": 403,
    "chemin-systeme": 403,
    "depot-maestro": 403,
    "acces-refuse": 403,
    "projet-inconnu": 404,
}

#: Le code rendu par un motif inconnu de la table : la saisie est en cause.
STATUT_HTTP_DEFAUT = 422

#: La seule surcharge propre à l'explorateur. Demander à énumérer un dossier
#: absent est un **404** — la ressource demandée n'est pas là ; **déclarer** un
#: projet sur une racine absente reste un **422** — la route existe, c'est le
#: corps qui est inexploitable. Le même motif, deux lectures.
STATUT_HTTP_EXPLORATEUR: dict[str, int] = {"dossier-absent": 404}


class ProjetInconnu(LookupError):
    """Le projet demandé n'est pas dans le dépôt (traduit en 404 par `app.py`)."""

    def __init__(self, id_: str) -> None:
        super().__init__(f"projet inconnu : {id_} (voir GET /api/projets)")
        self.motif = "projet-inconnu"
        self.id = id_


class ProjetIllisible(ValueError):
    """Le fichier du projet existe mais ne se relit pas (JSON corrompu, champ absent).

    Distinct de `ProjetInconnu` à dessein : « il n'y en a pas » et « il y en a un
    que je n'arrive pas à lire » demandent deux gestes différents. Le listing,
    lui, **saute** un projet illisible plutôt que de rendre la page entière
    inutilisable — le même arbitrage que `ProjetStore.par_racine` (#221).
    """

    def __init__(self, id_: str, cause: Exception) -> None:
        super().__init__(f"projet illisible : {id_} ({cause}) — fichier du dépôt à réparer.")
        self.motif = "projet-illisible"
        self.id = id_


def statut_http(refus: Exception, *, explorateur: bool = False) -> int:
    """Le code HTTP d'un refus du service, d'après son `motif` (défaut : 422)."""
    motif = getattr(refus, "motif", "")
    if explorateur and motif in STATUT_HTTP_EXPLORATEUR:
        return STATUT_HTTP_EXPLORATEUR[motif]
    return STATUT_HTTP_PAR_MOTIF.get(motif, STATUT_HTTP_DEFAUT)


def detail_refus(refus: Exception) -> dict[str, str]:
    """Le corps d'erreur servi par les routes projets : `{motif, message}`.

    Le `motif` est un code stable et court (`chemin-sensible`, `hors-racines-explorables`…),
    le `message` la phrase lisible. L'écran Projets doit pouvoir dire *pourquoi*
    une racine est refusée ([docs/05 §2.7](../../docs/05-interface-control-tower.md)) :
    le laisser deviner à partir d'une phrase libre reviendrait à ne rien lui dire.
    """
    return {
        "motif": getattr(refus, "motif", "requete-invalide"),
        "message": str(refus),
    }


class ServiceProjets:
    """CRUD des projets et explorateur de dossiers, au-dessus d'un `ProjetStore`.

    `racines_exploration` fixe les dossiers que l'explorateur accepte
    d'énumérer. `None` (le cas nominal) : le dossier utilisateur. Dans les deux
    cas, les racines des projets **déjà déclarés** s'y ajoutent — un projet
    déclaré est explorable par construction, sa racine ayant passé
    `valider_racine`.
    """

    def __init__(
        self,
        store: ProjetStore,
        *,
        racines_exploration: tuple[Path | str, ...] | None = None,
    ) -> None:
        self._store = store
        self._racines = (
            None if racines_exploration is None else tuple(Path(r) for r in racines_exploration)
        )

    @classmethod
    def default(cls, settings: Settings | None = None) -> ServiceProjets:
        """Le service configuré : dépôt `MAESTRO_PROJETS_DIR`, racines
        explorables `MAESTRO_EXPLORATEUR_RACINES`."""
        settings = settings or load_settings()
        return cls(
            ProjetStore.default(settings),
            racines_exploration=_racines_configurees(settings),
        )

    @property
    def store(self) -> ProjetStore:
        """Le dépôt sous-jacent — le seul écrivain des projets (#221)."""
        return self._store

    # --- CRUD ------------------------------------------------------------

    def lister(self) -> list[dict[str, Any]]:
        """Les projets déclarés, dans l'ordre des identifiants.

        Un fichier **illisible est sauté** : un JSON corrompu ne doit pas rendre
        l'écran Projets inutilisable (même arbitrage que `ProjetStore.par_racine`).
        Il reste diagnosticable par `detail`, qui l'explique au lieu de le taire.
        """
        return [self._fiche(projet) for projet in self._projets_lisibles()]

    def detail(self, id_: str) -> dict[str, Any]:
        """Le projet `id_`. `ProjetInconnu` s'il n'existe pas, `ProjetIllisible` s'il est cassé."""
        return self._fiche(self._lire(id_))

    def creer(
        self,
        nom: str,
        racine: str,
        *,
        origine: str = "existant",
        inclus: tuple[str, ...] | None = None,
        exclus: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Déclare un projet : racine validée (EF-38), VCS détecté, identifiant engendré.

        `origine="nouveau"` **crée** le dossier s'il manque ; `existant` exige
        qu'il soit là. Le `vcs` n'est jamais demandé à l'appelant : il est
        constaté sur le disque. Lève `RacineRefusee` (racine hors périmètre) ou
        `ValueError` (nom vide, origine inconnue, racine déjà déclarée).
        """
        return self._fiche(
            self._store.creer(nom, racine, origine=origine, inclus=inclus, exclus=exclus)
        )

    def remplacer(
        self,
        id_: str,
        nom: str,
        racine: str,
        *,
        origine: str = "existant",
        inclus: tuple[str, ...] | None = None,
        exclus: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Remplace la déclaration du projet `id_` — l'intégrale, pas un diff.

        Même parti pris que `PUT /api/catalogue/{nom}` : un champ non fourni
        retombe sur son défaut, il n'est pas « conservé ». Le `vcs` est
        **re-détecté** sur la racine servie (elle a pu changer, ou le dossier
        être passé sous Git depuis) et `cree_le` est préservé par le dépôt.
        """
        self._lire(id_)  # 404 avant toute écriture — et avant de créer un dossier.
        if origine not in ORIGINES:
            raise ValueError(
                f"origine invalide : {origine!r} ({' ou '.join(sorted(ORIGINES))} attendu)."
            )
        chemin = valider_racine(racine, creer=origine == "nouveau")
        projet = Projet(
            id=id_,
            nom=nom,
            racine=chemin.as_posix(),
            origine=origine,
            vcs=detecter_vcs(chemin),
            perimetre=Perimetre(
                inclus=inclus if inclus is not None else Perimetre().inclus,
                exclus=exclus if exclus is not None else Perimetre().exclus,
            ),
        )
        return self._fiche(self._store.ecrire(projet))

    def supprimer(self, id_: str) -> dict[str, Any]:
        """Oublie le projet `id_`. Ne touche **jamais** au dossier sur le disque (#221)."""
        self._lire(id_)
        self._store.supprimer(id_)
        return {"id": id_, "supprime": True}

    # --- Explorateur de dossiers -----------------------------------------

    def racines(self) -> tuple[Path, ...]:
        """Les dossiers explorables : ceux configurés (ou le dossier utilisateur) + les projets.

        Dédoublonnées, canonicalisées, et **filtrées sur celles qui existent** :
        une racine configurée vers un disque débranché ne sert à rien et
        l'afficher ferait croire à un dossier vide. Une racine **contenue dans
        une autre** est retirée : le projet déclaré sous le dossier utilisateur
        n'ajoute aucune permission, et le laisser ferait un point d'entrée qui
        liste deux fois le même arbre. Triées, pour que deux appels rendent le
        même ordre.
        """
        base = self._racines if self._racines is not None else _racines_defaut()
        candidates = list(base) + [p.racine_chemin for p in self._projets_lisibles()]
        retenues: dict[str, Path] = {}
        for candidate in candidates:
            try:
                resolue = canonique(candidate)
            except OSError:  # pragma: no cover - chemin illisible pour l'OS
                continue
            if resolue.is_dir():
                retenues.setdefault(_cle(resolue), resolue)
        triees = tuple(sorted(retenues.values(), key=lambda p: p.as_posix().casefold()))
        return tuple(
            racine
            for racine in triees
            if _sous_une_racine(racine, tuple(a for a in triees if a != racine)) is None
        )

    def points_entree(self) -> tuple[tuple[Path, str], ...]:
        """Ce que l'écran montre en arrivant : `(dossier, origine)`, dans l'ordre d'affichage.

        À distinguer de `racines()`, qui est la **frontière** — ce qu'on a le
        droit d'énumérer. Les deux ont divergé avec #278 : la frontière contient
        désormais les volumes du poste, et elle dédoublonne par contenance, si
        bien qu'elle se réduirait à `C:/` (ou `/`) — un point d'entrée unique
        d'où il faudrait redescendre tout l'arbre à chaque fois.

        Quatre origines, dans cet ordre :

        - `utilisateur` — le dossier utilisateur, là où sont la plupart des
          projets ;
        - `recent` — les **parents** des projets déclarés, du plus récemment
          déclaré au plus ancien. C'est le dossier où l'on range ses dépôts :
          y avoir déclaré un projet est le meilleur indice qu'on y déclarera le
          suivant. Ce n'est **pas** un historique de navigation — rien n'est
          enregistré au clic, et donc rien n'est à purger ;
        - `projet` — les racines déjà déclarées, pour y revenir directement ;
        - `volume` — les disques du poste, la porte de sortie quand le projet
          n'est sous aucun des précédents.

        Chaque point est canonicalisé, existant, **hors zone interdite** et
        **dans la frontière** — un point d'entrée qui refuserait au clic serait
        pire que son absence. C'est ce dernier filtre qui fait que
        `MAESTRO_EXPLORATEUR_RACINES` reste une **restriction** : les volumes ne
        sont proposés que si la frontière les contient, c'est-à-dire seulement
        quand elle est celle par défaut.
        """
        racines = self.racines()
        vus: dict[str, tuple[Path, str]] = {}

        def retenir(candidat: Path | str, origine: str) -> None:
            try:
                resolu = canonique(candidat)
            except OSError:  # pragma: no cover - chemin illisible pour l'OS
                return
            cle = _cle(resolu)
            if cle in vus or not resolu.is_dir():
                return
            try:
                verifier_zone_interdite(resolu)
            except RacineRefusee:
                return
            if _sous_une_racine(resolu, racines) is None:
                return
            vus[cle] = (resolu, origine)

        if self._racines is not None:
            for configuree in self._racines:
                retenir(configuree, "configuree")
        else:
            try:
                retenir(Path.home(), "utilisateur")
            except (RuntimeError, OSError):  # pragma: no cover - dépend de l'environnement
                pass

        projets = sorted(
            self._projets_lisibles(),
            key=lambda p: (p.cree_le or "", p.id),
            reverse=True,
        )
        for projet in projets[:LIMITE_RECENTS]:
            retenir(projet.racine_chemin.parent, "recent")
        for projet in projets:
            retenir(projet.racine_chemin, "projet")
        for volume in _volumes():
            retenir(volume, "volume")
        return tuple(vus.values())

    def explorer(self, chemin: str | None = None) -> dict[str, Any]:
        """Énumère les **dossiers** de `chemin`, ou les racines explorables sans argument.

        Ne rend que des dossiers (choisir une racine de projet, pas lire des
        fichiers) : chacun avec son marqueur « dépôt Git » et l'identifiant du
        projet qui l'a déjà déclaré, le cas échéant. `parent` est `null` quand
        remonter sortirait des racines — la frontière se voit dans la réponse,
        elle ne se découvre pas au clic suivant.

        Lève `RacineRefusee` **motivée** — jamais une liste vide — quand le
        chemin sort des racines (`hors-racines-explorables`), tombe dans une
        zone sensible (`chemin-sensible`, `chemin-systeme`, `depot-maestro`),
        n'existe pas (`dossier-absent`), n'est pas un dossier (`pas-un-dossier`)
        ou n'est pas lisible (`acces-refuse`).
        """
        racines = self.racines()
        if not racines:
            raise RacineRefusee(
                "aucune-racine-explorable",
                "Aucun dossier explorable : renseignez MAESTRO_EXPLORATEUR_RACINES "
                "(le dossier utilisateur est introuvable et aucun projet n'est déclaré).",
            )
        par_racine = self._projets_par_racine()
        brut = (chemin or "").strip()
        if not brut:
            # La page d'entrée montre les **points d'entrée**, pas la frontière :
            # depuis #278 celle-ci contient les volumes, qui avaleraient le
            # dossier utilisateur et les projets par contenance (voir `racines`).
            return _vue(
                chemin=None,
                parent=None,
                racines=racines,
                dossiers=[
                    _fiche_dossier(point, par_racine, origine=origine)
                    for point, origine in self.points_entree()
                ],
                tronque=False,
            )

        cible = self._dossier_explorable(brut, racines)
        noms = _sous_dossiers(cible)
        tronque = len(noms) > LIMITE_DOSSIERS
        return _vue(
            chemin=cible,
            parent=self._parent_explorable(cible, racines),
            racines=racines,
            dossiers=[
                _fiche_dossier(cible / nom, par_racine) for nom in noms[:LIMITE_DOSSIERS]
            ],
            tronque=tronque,
        )

    # --- Interne ---------------------------------------------------------

    def _dossier_explorable(self, brut: str, racines: tuple[Path, ...]) -> Path:
        """Le dossier `brut` canonicalisé et autorisé, ou `RacineRefusee` motivée."""
        if not Path(brut).expanduser().is_absolute():
            raise RacineRefusee(
                "chemin-relatif",
                f"Chemin relatif refusé : {brut!r} — indiquez un chemin absolu.",
                brut,
            )
        cible = canonique(brut)
        verifier_zone_interdite(cible)
        if not _sous_une_racine(cible, racines):
            raise RacineRefusee(
                "hors-racines-explorables",
                f"Dossier hors des racines explorables : {cible} — autorisées : "
                f"{', '.join(r.as_posix() for r in racines)} "
                "(élargissez avec MAESTRO_EXPLORATEUR_RACINES).",
                cible,
            )
        if not cible.exists():
            raise RacineRefusee("dossier-absent", f"Dossier introuvable : {cible}.", cible)
        if not cible.is_dir():
            raise RacineRefusee(
                "pas-un-dossier",
                f"Chemin invalide : {cible} n'est pas un dossier.",
                cible,
            )
        return cible

    def _parent_explorable(self, cible: Path, racines: tuple[Path, ...]) -> Path | None:
        """Le dossier au-dessus de `cible` s'il reste explorable, `None` sinon."""
        parent = cible.parent
        if parent == cible or not _sous_une_racine(parent, racines):
            return None
        try:
            verifier_zone_interdite(parent)
        except RacineRefusee:
            return None
        return parent

    def _lire(self, id_: str) -> Projet:
        """Le projet `id_`, ou `ProjetInconnu`/`ProjetIllisible` — jamais un 500."""
        try:
            projet = self._store.lire(id_)
        except ValueError as exc:
            # Deux `ValueError` bien différents se croisent ici : l'identifiant
            # hors slug, que le dépôt refuse de transformer en nom de fichier
            # (#221) — vu de l'API, un projet qui n'existe pas —, et le JSON
            # corrompu (`JSONDecodeError` en hérite), qui lui existe et se
            # signale. Les confondre rendrait un 404 sur un fichier à réparer.
            if str(exc).startswith("identifiant de projet invalide"):
                raise ProjetInconnu(id_) from exc
            raise ProjetIllisible(id_, exc) from exc
        except (OSError, KeyError) as exc:
            raise ProjetIllisible(id_, exc) from exc
        if projet is None:
            raise ProjetInconnu(id_)
        return projet

    def _projets_lisibles(self) -> tuple[Projet, ...]:
        """Les projets du dépôt, ceux dont le fichier est cassé étant sautés."""
        lisibles = []
        for id_ in self._store.ids():
            try:
                projet = self._store.lire(id_)
            except (ValueError, OSError, KeyError):
                continue
            if projet is not None:
                lisibles.append(projet)
        return tuple(lisibles)

    def _projets_par_racine(self) -> dict[str, str]:
        """`{racine canonique → id}` — de quoi dire, en un passage, quel dossier est déjà pris.

        Calculé une fois par appel à l'explorateur : interroger le dépôt dossier
        par dossier (`ProjetStore.par_racine`) relirait tous les fichiers à
        chaque entrée listée.
        """
        par_racine: dict[str, str] = {}
        for projet in self._projets_lisibles():
            try:
                par_racine[_cle(canonique(projet.racine))] = projet.id
            except OSError:  # pragma: no cover - chemin illisible pour l'OS
                continue
        return par_racine

    def _fiche(self, projet: Projet) -> dict[str, Any]:
        """La forme JSON d'un projet — celle du fichier stocké (docs/24 §2.3)."""
        return projet.to_dict()


def _vue(
    *,
    chemin: Path | None,
    parent: Path | None,
    racines: tuple[Path, ...],
    dossiers: list[dict[str, Any]],
    tronque: bool,
) -> dict[str, Any]:
    """La forme JSON figée d'une page d'explorateur (docs/05 §6.7)."""
    return {
        "chemin": chemin.as_posix() if chemin is not None else None,
        "parent": parent.as_posix() if parent is not None else None,
        "racines": [r.as_posix() for r in racines],
        "dossiers": dossiers,
        "tronque": tronque,
    }


def _fiche_dossier(
    dossier: Path,
    par_racine: dict[str, str],
    *,
    origine: str | None = None,
) -> dict[str, Any]:
    """Un dossier de la liste : nom, chemin absolu, marqueur Git, projet déjà déclaré.

    `nom` retombe sur le chemin complet quand il est vide — le cas d'une racine
    de disque (`D:/`), qui n'a pas de nom mais doit rester cliquable.

    `origine` n'est renseignée que sur la **page d'entrée** (#278) : elle dit
    *pourquoi* ce dossier est proposé (`utilisateur`, `recent`, `projet`,
    `volume`, `configuree`), ce que l'écran affiche en pastille. Ailleurs elle
    est `null` — un sous-dossier énuméré n'a pas d'autre raison d'être là que
    d'être dans le dossier ouvert.
    """
    return {
        "nom": dossier.name or dossier.as_posix(),
        "chemin": dossier.as_posix(),
        "depot_git": detecter_vcs(dossier) is not None,
        "projet_id": par_racine.get(_cle(dossier)),
        "origine": origine,
    }


def _sous_dossiers(cible: Path) -> list[str]:
    """Les noms des sous-dossiers de `cible`, triés — ou `RacineRefusee` si illisible.

    Rien n'est masqué (ni les dossiers cachés, ni `node_modules`) : un
    explorateur qui filtre en silence fait douter de ce qu'il montre. Le
    marqueur Git et le tri suffisent à s'y retrouver.
    """
    try:
        with os.scandir(cible) as entrees:
            noms = [entree.name for entree in entrees if _est_dossier(entree)]
    except PermissionError as exc:
        raise RacineRefusee(
            "acces-refuse",
            f"Dossier illisible : {cible} — accès refusé par le système.",
            cible,
        ) from exc
    except OSError as exc:
        raise RacineRefusee(
            "acces-refuse",
            f"Dossier illisible : {cible} ({exc.strerror or exc}).",
            cible,
        ) from exc
    return sorted(noms, key=str.casefold)


def _est_dossier(entree: os.DirEntry[str]) -> bool:
    """`entree` est-elle un dossier ? Faux si l'OS refuse de le dire (lien mort, droits)."""
    try:
        return entree.is_dir()
    except OSError:
        return False


def _sous_une_racine(chemin: Path, racines: tuple[Path, ...]) -> Path | None:
    """La racine explorable qui contient `chemin`, ou None.

    Délègue à `chemin_dans_racine` (#221) plutôt que de recomparer des chemins :
    c'est déjà la fonction qui sait résoudre les `..` et les liens symboliques
    **avant** de comparer, et dupliquer cette logique serait dupliquer une
    frontière de sécurité.
    """
    for racine in racines:
        try:
            chemin_dans_racine(racine, chemin)
        except (RacineRefusee, OSError):
            continue
        return racine
    return None


def _racines_defaut() -> tuple[Path, ...]:
    """Le défaut des racines explorables : le dossier utilisateur **et les volumes** (#278).

    Le dossier utilisateur seul était un cul-de-sac : un projet posé sur `D:/`
    ou sur un disque externe n'était pas atteignable, et la seule sortie était
    de renseigner `MAESTRO_EXPLORATEUR_RACINES` — un réglage d'environnement
    pour un geste d'écran. Les volumes du poste élargissent la **frontière** ;
    ils n'ouvrent rien de nouveau, `verifier_zone_interdite` continuant de
    refuser `.ssh`, `AppData`, les dossiers système et le dépôt de Maestro, et
    `valider_racine` de refuser une racine de disque comme racine de projet.

    Le dossier utilisateur reste dans la liste bien qu'un volume le contienne :
    `racines()` dédoublonne par contenance, mais `points_entree()` — ce que
    l'écran montre en arrivant — le garde à part.
    """
    volumes = _volumes()
    try:
        return (Path.home(), *volumes)
    except (RuntimeError, OSError):  # pragma: no cover - dépend de l'environnement
        return volumes


def _volumes() -> tuple[Path, ...]:
    """Les disques et volumes montés du poste, dans l'ordre du système.

    Sous Windows, les lettres de lecteur (`os.listdrives` là où il existe,
    sinon les 26 lettres testées — 26 `is_dir()` valent mieux qu'une dépendance
    à Python 3.12). Sous POSIX, la racine `/` **est** le volume : y ajouter les
    points de montage habituels ferait doublon, tout en étant déjà couvert.

    Un lecteur non prêt (disque optique vide, lettre mappée sur un partage
    déconnecté) lève ou rend faux : il est simplement absent de la liste, et
    c'est ce qu'on veut — l'afficher ferait croire à un dossier vide.
    """
    if os.name != "nt":
        return (Path("/"),)
    lettres: tuple[str, ...]
    if hasattr(os, "listdrives"):  # Python 3.12+
        try:
            lettres = tuple(os.listdrives())
        except OSError:  # pragma: no cover - dépend du système
            lettres = ()
    else:  # pragma: no cover - dépend de la version de Python
        lettres = tuple(f"{lettre}:\\" for lettre in string.ascii_uppercase)
    volumes = []
    for lettre in lettres:
        chemin = Path(lettre)
        try:
            if chemin.is_dir():
                volumes.append(chemin)
        except OSError:  # pragma: no cover - lecteur non prêt
            continue
    return tuple(volumes)


def _racines_configurees(settings: Settings) -> tuple[str, ...] | None:
    """`MAESTRO_EXPLORATEUR_RACINES` éclaté, ou None si la variable est vide.

    Séparateur `os.pathsep` (`;` sous Windows, `:` sous POSIX) : sous Windows,
    `:` serait ambigu avec la lettre de disque (`D:/projets`).
    """
    brut = (settings.explorateur_racines or "").strip()
    if not brut:
        return None
    racines = tuple(part.strip() for part in brut.split(os.pathsep) if part.strip())
    return racines or None


def _cle(chemin: Path) -> str:
    """La clé de comparaison de deux chemins — insensible à la casse là où l'OS l'est."""
    return os.path.normcase(chemin.as_posix())
