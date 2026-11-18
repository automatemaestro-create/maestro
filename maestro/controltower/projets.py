"""Service des projets pour la Control Tower : CRUD et explorateur de dossiers (#223).

Le lot 1 (#221) a posé l'entité `Projet`, sa racine validée et sa persistance —
mais **personne ne pouvait l'atteindre** : la Control Tower n'avait aucune route
pour lire ou écrire un projet. Ce module est la pièce que les routes
`/api/projets` appellent, au patron de `maestro.controltower.executions` : le
service tient les formes JSON et les refus, ses routes (`routes/projets.py`) ne font que
les traduire en codes HTTP.

Deux responsabilités :

- **le CRUD** (`lister`, `detail`, `creer`, `remplacer`, `supprimer`) — une
  couche mince au-dessus de `ProjectStore`, qui reste le seul écrivain. La forme
  servie est celle de `Project.to_dict` (docs/24 §2.3) : aucune seconde
  définition du contrat, donc aucune dérive possible entre le fichier stocké et
  la réponse HTTP — et, depuis #855, le **geste** `versionner`, qui n'est pas
  un champ de plus du CRUD mais l'appel d'un verbe (`ProjectStore.enable_versioning`,
  #704) : le `vcs` reste **constaté et jamais déclaré** (EF-38), la route ne
  prend aucun `vcs` en entrée, elle déclenche la mise sous Git et rend la
  fiche relue ;
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
   déjà déclarés (elles ont passé `validate_root`, elles sont explorables par
   construction) et les **dossiers autorisés depuis l'écran** (#1610), rejugés
   par `validate_root` à chaque lecture. Hors d'elles, refus — qui dit si le
   dossier peut s'autoriser d'ici (`autorisation`) ;
2. **les zones interdites** de `maestro.projects.root` (`.ssh`, `AppData`,
   dossiers système, dépôt de Maestro) — qui s'appliquent *à l'intérieur* des
   racines : le dossier utilisateur est traversable, `~/.ssh` ne l'est pas.

Un refus **porte toujours son motif** (`RootRejected.reason`) et jamais une
liste vide : « ce dossier n'a pas de sous-dossier » et « je refuse de regarder
là » sont deux réponses différentes, et les confondre est ce qui rend un
explorateur inutilisable. `STATUT_HTTP_PAR_MOTIF` donne à chaque motif son code
HTTP — c'est la seule connaissance de HTTP de ce module, et elle est ici plutôt
que dans les routes pour que motif et code se lisent au même endroit.
"""

from __future__ import annotations

import os
import string
from pathlib import Path
from typing import Any

from maestro.config import Settings, load_settings
from maestro.outillage import CHEMIN_MANIFESTE
from maestro.projects import (
    ORIGINS,
    Perimeter,
    Project,
    ProjectStore,
    RootRejected,
    canonical,
    check_forbidden_zone,
    detect_vcs,
    path_in_root,
    validate_root,
)
from maestro.projects.settings import ProjectSettingsStore

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
#:
#: Les refus de la **mise sous Git** (#855, motifs de `VersioningRejected`,
#: #704) passent par la même table : **409** pour `depot-englobant` — ce n'est
#: ni la saisie ni une frontière, c'est l'**état du disque** qui s'oppose au
#: geste (la racine est déjà dans un autre dépôt, comme un run déjà terminé
#: refuse un second arrêt) —, **503** pour `git-indisponible` — le **poste** ne
#: peut pas (binaire absent, délai dépassé), ce que ni un autre corps ni un
#: autre chemin ne changeraient. `init-refuse`, `commit-refuse` (un `pre-commit`
#: de l'utilisateur qui refuse) et `vcs-introuvable` restent sur le défaut : la
#: racine, telle qu'elle est, est en cause.
STATUT_HTTP_PAR_MOTIF: dict[str, int] = {
    "hors-racines-explorables": 403,
    "aucune-racine-explorable": 403,
    "chemin-sensible": 403,
    "chemin-systeme": 403,
    "depot-maestro": 403,
    "acces-refuse": 403,
    "projet-inconnu": 404,
    "run-inconnu": 404,
    "autorisation-inconnue": 404,
    "depot-englobant": 409,
    "git-indisponible": 503,
}

#: Les refus de **portée projet** (#277) passent par la même table : ils sont de
#: même nature que ceux du CRUD (« ce projet n'existe pas ») et doivent donc
#: sortir par la même porte — motif, message, code. `projet-inconnu` y est déjà
#: (404) ; `projet-requis` retombe sur le défaut 422, la saisie étant en cause.
#: La **portée run** (#473) suit le même chemin : `run-inconnu` est un 404 pour
#: la raison exacte qui en fait un pour un projet — une liste vide se lirait
#: « ce run n'a rien fait ». Il n'a pas de pendant « requis » : `?run=` est
#: facultatif et l'omettre ne restreint rien.

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
    inutilisable — le même arbitrage que `ProjectStore.by_root` (#221).
    """

    def __init__(self, id_: str, cause: Exception) -> None:
        super().__init__(f"projet illisible : {id_} ({cause}) — fichier du dépôt à réparer.")
        self.motif = "projet-illisible"
        self.id = id_


def motif_du_refus(refus: Exception, defaut: str = "") -> str:
    """Le code stable d'un refus, quel que soit le paquet qui l'a levé.

    Un refus du paquet `sources` (`SourceRejected`) porte son code dans `reason`, ceux des
    projets (`RootRejected` et ses dérivés) dans `motif`. Les deux se lisent ici, et nulle
    part ailleurs, jusqu'à la traduction du paquet `projets` (#1652).
    """
    for attribut in ("reason", "motif"):
        if hasattr(refus, attribut):
            return str(getattr(refus, attribut))
    return defaut


def statut_http(refus: Exception, *, explorateur: bool = False) -> int:
    """Le code HTTP d'un refus du service, d'après son motif (défaut : 422)."""
    motif = motif_du_refus(refus)
    if explorateur and motif in STATUT_HTTP_EXPLORATEUR:
        return STATUT_HTTP_EXPLORATEUR[motif]
    return STATUT_HTTP_PAR_MOTIF.get(motif, STATUT_HTTP_DEFAUT)


class DossierHorsFrontiere(RootRejected):
    """Le refus `hors-racines-explorables`, qui dit en plus si le dossier s'autorise d'ici.

    `autorisation` vaut `{chemin, refus}` : `chemin` est le dossier en POSIX,
    `refus` est `None` quand `validate_root` l'accepterait comme racine de
    projet, et sinon le refus motivé qu'il rendrait (#1610). L'écran ne
    propose le geste que dans le premier cas : il ne devine rien.
    """

    def __init__(self, cible: Path, racines: tuple[Path, ...]) -> None:
        explorables = (
            ", ".join(r.as_posix() for r in racines) if racines else "ce poste n'en a aucun"
        )
        # Une phrase : le dossier, puis les dossiers explorables qu'il ne
        # rejoint pas (#1613). L'écran la pose sous son propre titre, qui dit
        # déjà « Dossier non exploré ». Espace insécable avant les deux-points :
        # à la largeur d'un téléphone, un « : » ne passe pas seul à la ligne.
        super().__init__(
            "hors-racines-explorables",
            f"{cible.as_posix()} est hors des dossiers explorables : {explorables}.",
            cible,
        )
        try:
            validate_root(cible)
        except RootRejected as refus:
            verdict: dict[str, str] | None = detail_refus(refus)
        else:
            verdict = None
        self.autorisation: dict[str, Any] = {"chemin": cible.as_posix(), "refus": verdict}


def detail_refus(refus: Exception) -> dict[str, Any]:
    """Le corps d'erreur servi par les routes projets : `{motif, message}`.

    Le `motif` est un code stable et court (`chemin-sensible`, `hors-racines-explorables`…),
    le `message` la phrase lisible. L'écran Projets doit pouvoir dire *pourquoi*
    une racine est refusée ([docs/05 §2.7](../../docs/05-interface-control-tower.md)) :
    le laisser deviner à partir d'une phrase libre reviendrait à ne rien lui dire.
    Un dossier hors de la frontière porte en plus son `autorisation` (#1610).
    """
    corps: dict[str, Any] = {
        "motif": motif_du_refus(refus, "requete-invalide"),
        "message": str(refus),
    }
    autorisation = getattr(refus, "autorisation", None)
    if autorisation is not None:
        corps["autorisation"] = autorisation
    return corps


class ServiceProjets:
    """CRUD des projets et explorateur de dossiers, au-dessus d'un `ProjectStore`.

    `racines_exploration` fixe les dossiers que l'explorateur accepte
    d'énumérer. `None` (le cas nominal) : le dossier utilisateur. Dans les deux
    cas, les racines des projets **déjà déclarés** s'y ajoutent — un projet
    déclaré est explorable par construction, sa racine ayant passé
    `validate_root`.
    """

    def __init__(
        self,
        store: ProjectStore,
        *,
        racines_exploration: tuple[Path | str, ...] | None = None,
        reglages: ProjectSettingsStore | None = None,
    ) -> None:
        self._store = store
        self._racines = (
            None if racines_exploration is None else tuple(Path(r) for r in racines_exploration)
        )
        self._reglages = reglages if reglages is not None else ProjectSettingsStore.default()

    @classmethod
    def default(cls, settings: Settings | None = None) -> ServiceProjets:
        """Le service configuré : dépôt `MAESTRO_PROJETS_DIR`, racines
        explorables `MAESTRO_EXPLORATEUR_RACINES`, réglages `MAESTRO_REGLAGES_DIR`."""
        settings = settings or load_settings()
        return cls(
            ProjectStore.default(settings),
            racines_exploration=_racines_configurees(settings),
            reglages=ProjectSettingsStore.default(settings),
        )

    @property
    def store(self) -> ProjectStore:
        """Le dépôt sous-jacent — le seul écrivain des projets (#221)."""
        return self._store

    @property
    def reglages(self) -> ProjectSettingsStore:
        """Le dépôt des réglages du poste — où naît un projet neuf (#1022)."""
        return self._reglages

    # --- Répertoire des projets (#1022) ----------------------------------

    def repertoire(self, *, creer: bool = False) -> dict[str, Any]:
        """Le répertoire où naît un projet neuf, tel que l'écran le lit (#1022).

        Rend `{chemin, par_defaut, existe, cree, refus}`. Un refus **ne lève
        pas** ici : le répertoire réglé peut être devenu indéclarable (disque
        débranché, dossier devenu fichier) sans que cela doive empêcher de
        déclarer un projet ailleurs — l'écran affiche le motif et laisse
        parcourir. Le seul cas qui lève est l'écriture (`regler`), parce qu'un
        réglage refusé ne doit pas être stocké.

        `chemin` est rendu en **POSIX**, comme toute racine servie par cette
        API (docs/05 §6.7) : l'écran compare ce chemin à celui que l'explorateur
        lui rend, et deux écritures du même dossier lui feraient dire « hors de
        votre répertoire des projets » d'un dossier qui en est la racine même.
        """
        try:
            chemin, par_defaut, cree = self._reglages.resolve(create=creer)
        except RootRejected as refus:
            reglages = self._reglages.read()
            brut = reglages.directory
            return {
                "chemin": brut if brut is not None else str(refus.path or ""),
                "par_defaut": brut is None,
                "existe": False,
                "cree": False,
                "refus": detail_refus(refus),
            }
        return {
            "chemin": chemin.as_posix(),
            "par_defaut": par_defaut,
            "existe": True,
            "cree": cree,
            "refus": None,
        }

    def regler_repertoire(self, chemin: str | None) -> dict[str, Any]:
        """Pose le répertoire des projets (`None` = retour au défaut) et le relit.

        Le dossier est validé **et créé** par le dépôt avant d'être stocké : le
        réglage posé est toujours un dossier déclarable, jamais une intention à
        vérifier plus tard. Lève `RootRejected` motivée, que l'API traduit.
        """
        self._reglages.write(chemin)
        return self.repertoire(creer=True)

    def _repertoire_explorable(self) -> Path | None:
        """Le répertoire des projets s'il est un dossier — sinon rien.

        Sert la **frontière** et les points d'entrée : un parent prérempli que
        l'explorateur refuserait d'ouvrir serait un cul-de-sac, et c'est le
        premier geste qu'un utilisateur fait après l'avoir lu (« Changer de
        dossier… » s'ouvre dessus).
        """
        try:
            chemin, _, _ = self._reglages.resolve()
        except RootRejected:
            return None
        return chemin

    # --- Dossiers autorisés (#1610) --------------------------------------

    def dossiers_autorises(self) -> dict[str, Any]:
        """Les dossiers autorisés depuis l'écran, chacun avec son verdict du moment.

        Rend `{dossiers: [{chemin, refus}]}`, dans l'ordre d'autorisation. Le
        `refus` est celui que `validate_root` rend **aujourd'hui** (disque
        débranché, dossier supprimé) : une autorisation devenue invalide ne sert
        plus de frontière, et l'écran le dit au lieu de la taire.
        """
        dossiers: list[dict[str, Any]] = []
        for chemin in self._reglages.read().allowed_folders:
            try:
                validate_root(chemin)
            except RootRejected as refus:
                dossiers.append({"chemin": chemin, "refus": detail_refus(refus)})
            else:
                dossiers.append({"chemin": chemin, "refus": None})
        return {"dossiers": dossiers}

    def autoriser_dossier(self, chemin: str) -> dict[str, Any]:
        """Autorise `chemin` sur ce poste ; rend `{chemin, dossiers}`.

        Validé par `validate_root` dans le dépôt : un dossier qu'on ne pourrait
        pas déclarer comme projet ne s'autorise pas, et `RootRejected` motivée
        remonte sans rien écrire. `chemin` est le dossier en POSIX, celui que
        l'explorateur ouvre ensuite.
        """
        _, valeur = self._reglages.allow(chemin)
        return {"chemin": valeur, **self.dossiers_autorises()}

    def retirer_dossier(self, chemin: str) -> dict[str, Any]:
        """Retire une autorisation ; rend `{dossiers}`. `autorisation-inconnue` sinon."""
        self._reglages.remove(chemin)
        return self.dossiers_autorises()

    def _dossiers_autorises_valides(self) -> tuple[Path, ...]:
        """Les dossiers autorisés que `validate_root` accepte encore, canonicalisés."""
        valides = []
        for chemin in self._reglages.read().allowed_folders:
            try:
                valides.append(validate_root(chemin))
            except RootRejected:
                continue
        return tuple(valides)

    # --- CRUD ------------------------------------------------------------

    def lister(self) -> list[dict[str, Any]]:
        """Les projets déclarés, dans l'ordre des identifiants.

        Un fichier **illisible est sauté** : un JSON corrompu ne doit pas rendre
        l'écran Projets inutilisable (même arbitrage que `ProjectStore.by_root`).
        Il reste diagnosticable par `detail`, qui l'explique au lieu de le taire.
        """
        return [self._fiche(projet) for projet in self._projets_lisibles()]

    def detail(self, id_: str) -> dict[str, Any]:
        """Le projet `id_`. `ProjetInconnu` s'il n'existe pas, `ProjetIllisible` s'il est cassé."""
        return self._fiche(self._lire(id_))

    def entite(self, id_: str) -> Project:
        """Le projet `id_` en **entité** — sa racine en `Path`, son périmètre.

        `detail` rend la fiche JSON de l'API ; les services qui vont *regarder le
        disque du projet* (`ServiceOutillage`, `maestro.controltower.equipe`) ont
        besoin de l'objet. Ils passent ici plutôt que de relire le dépôt à côté,
        pour qu'il n'y ait qu'un seul lecteur de projets dans la Control Tower :
        deux relectures de la même fiche finiraient par ne plus se refuser les
        mêmes fichiers.

        Mêmes refus que `detail`, et aux mêmes conditions — `ProjetInconnu`,
        `ProjetIllisible` —, donc la même traduction HTTP.
        """
        return self._lire(id_)

    def existe(self, id_: str) -> bool:
        """Le projet `id_` est-il déclaré ? La question que pose la **portée** d'une lecture (#277).

        Un booléen là où `detail` lève : la portée projet n'a pas à distinguer
        « pas de fichier » de « fichier cassé » — dans les deux cas le projet ne
        peut pas cadrer une lecture, et le refus rendu est le même
        (`projet-inconnu`). C'est `GET /api/projets/{id}` qui sait faire la
        différence, et c'est là qu'un fichier à réparer s'explique.
        """
        try:
            self._lire(id_)
        except (ProjetInconnu, ProjetIllisible):
            return False
        return True

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
        constaté sur le disque. Lève `RootRejected` (racine hors périmètre) ou
        `ValueError` (nom vide, origine inconnue, racine déjà déclarée).
        """
        return self._fiche(
            self._store.create(nom, racine, origin=origine, included=inclus, excluded=exclus)
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

        ⚠ Une exception, et elle est de même nature que `cree_le` : le fragment
        `outillage` (#1034) est **reporté tel quel**. Ce n'est pas un champ de la
        déclaration — la route n'en prend aucun en entrée —, c'est une décision
        déjà prise ; la remise à zéro par un renommage de projet reposerait une
        question à laquelle quelqu'un a répondu.
        """
        ancien = self._lire(id_)  # 404 avant toute écriture — et avant de créer un dossier.
        if origine not in ORIGINS:
            raise ValueError(
                f"origine invalide : {origine!r} ({' ou '.join(sorted(ORIGINS))} attendu)."
            )
        chemin = validate_root(racine, create=origine == "nouveau")
        projet = Project(
            id=id_,
            name=nom,
            root=chemin.as_posix(),
            origin=origine,
            vcs=detect_vcs(chemin),
            perimeter=Perimeter(
                included=inclus if inclus is not None else Perimeter().included,
                excluded=exclus if exclus is not None else Perimeter().excluded,
            ),
            tooling=ancien.tooling,
        )
        return self._fiche(self._store.write(projet))

    def supprimer(self, id_: str) -> dict[str, Any]:
        """Oublie le projet `id_`. Ne touche **jamais** au dossier sur le disque (#221)."""
        self._lire(id_)
        self._store.delete(id_)
        return {"id": id_, "supprime": True}

    def versionner(self, id_: str) -> dict[str, Any]:
        """Met le projet `id_` sous Git s'il ne l'est pas, et rend sa fiche relue (#855).

        Le **seul** geste du service qui écrive dans le dossier de l'utilisateur,
        et il ne fait qu'appeler le verbe de #704 (`ProjectStore.enable_versioning` →
        `init_repository`) : `git init` puis un premier commit qui enregistre
        la racine telle qu'elle est — `git add -A`, donc le `.gitignore` du
        projet respecté —, pour que la branche de base résolve. Rien n'est
        réimplémenté ici, et rien n'est demandé à l'appelant : pas de `vcs` en
        entrée (EF-38 — il est constaté, jamais déclaré), pas de nom de branche
        (Git répond selon la configuration de l'utilisateur).

        Un projet **déjà versionné** rend sa fiche telle quelle — aucune
        commande, aucune écriture, `modifie_le` intact : le verbe est
        idempotent, et l'écran peut le proposer sans craindre un second clic.

        Lève `ProjetInconnu`/`ProjetIllisible` (404/422 — avant toute commande
        Git), `RootRejected` si la racine n'est plus admissible (EF-38, motif à
        l'appui) et `VersioningRejected` motivée si l'initialisation échoue, la
        racine restant alors dans l'état d'avant. Les trois portent un `motif` :
        c'est ce que `statut_http`/`detail_refus` traduisent, jamais un 500.
        """
        self._lire(id_)  # 404/422 d'abord — et avant qu'une commande Git ne parte.
        return self._fiche(self._store.enable_versioning(id_))

    def reporter_outillage(self, id_: str) -> dict[str, Any]:
        """Enregistre le « plus tard » de l'étape d'outillage, et rend la fiche relue (#1034).

        Le pendant de `versionner` pour l'autre décision que l'écran de
        déclaration fait prendre — à ceci près qu'elle n'écrit **rien** dans le
        dossier de l'utilisateur : reporter, c'est précisément ne pas y écrire.
        Elle ne prend donc aucun corps, et ne peut rien refuser du dehors.

        Idempotente (`ProjectStore.defer_tooling` garde la première date) :
        l'écran peut la rappeler sans craindre un double clic. La fiche rendue
        porte déjà `outillage.a_faire`, donc l'écran n'a rien à recalculer.

        Lève `ProjetInconnu`/`ProjetIllisible` (404/422). Un projet **déjà
        outillé** est accepté sans distinction : le report y est sans effet
        visible, `a_faire` restant faux parce que le manifeste est là.
        """
        self._lire(id_)  # 404/422 avant toute écriture.
        return self._fiche(self._store.defer_tooling(id_))

    def reprendre_outillage(self, id_: str) -> dict[str, Any]:
        """Lève le « plus tard » d'un outillage repris dans la conversation (#1161).

        Le pendant de `reporter_outillage`, appelé quand l'outillage d'un projet
        s'ouvre pour lui : la fiche cesse de dire « reporté ». Idempotente.
        """
        self._lire(id_)
        return self._fiche(self._store.resume_tooling(id_))

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
        candidates = list(base) + [p.root_path for p in self._projets_lisibles()]
        # Les dossiers autorisés depuis l'écran **s'ajoutent** à la base sans la
        # remplacer (#1610), et seulement ceux que `validate_root` accepte
        # encore : une autorisation n'ouvre aucune zone interdite.
        candidates.extend(self._dossiers_autorises_valides())
        # Le répertoire des projets entre dans la frontière comme une racine de
        # projet déclaré (#1022) : c'est un dossier que l'écran **propose**, et
        # proposer un parent que l'explorateur refuserait d'ouvrir serait un
        # cul-de-sac. Sans restriction configurée il y est déjà (il est sous le
        # dossier utilisateur) ; avec une restriction, c'est ici qu'il entre.
        repertoire = self._repertoire_explorable()
        if repertoire is not None:
            candidates.append(repertoire)
        retenues: dict[str, Path] = {}
        for candidate in candidates:
            try:
                resolue = canonical(candidate)
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

    def points_entree(self) -> tuple[tuple[Path, str], ...]:  # noqa: C901
        """Ce que l'écran montre en arrivant : `(dossier, origine)`, dans l'ordre d'affichage.

        À distinguer de `racines()`, qui est la **frontière** — ce qu'on a le
        droit d'énumérer. Les deux ont divergé avec #278 : la frontière contient
        désormais les volumes du poste, et elle dédoublonne par contenance, si
        bien qu'elle se réduirait à `C:/` (ou `/`) — un point d'entrée unique
        d'où il faudrait redescendre tout l'arbre à chaque fois.

        Six origines, dans cet ordre :

        - `repertoire` — le **répertoire des projets** (#1022), celui que le
          formulaire propose d'office à un projet neuf : c'est le premier
          endroit à regarder, puisque c'est celui d'où l'on vient. Absent tant
          qu'il n'existe pas sur le disque ;
        - `utilisateur` — le dossier utilisateur, là où sont la plupart des
          projets (`configuree` à sa place quand `MAESTRO_EXPLORATEUR_RACINES`
          est posée) ;
        - `autorise` — les dossiers autorisés depuis l'écran (#1610), dans
          l'ordre de leur autorisation ;
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
                resolu = canonical(candidat)
            except OSError:  # pragma: no cover - chemin illisible pour l'OS
                return
            cle = _cle(resolu)
            if cle in vus or not resolu.is_dir():
                return
            try:
                check_forbidden_zone(resolu)
            except RootRejected:
                return
            if _sous_une_racine(resolu, racines) is None:
                return
            vus[cle] = (resolu, origine)

        repertoire = self._repertoire_explorable()
        if repertoire is not None:
            retenir(repertoire, "repertoire")

        if self._racines is not None:
            for configuree in self._racines:
                retenir(configuree, "configuree")
        else:
            try:
                retenir(Path.home(), "utilisateur")
            except (RuntimeError, OSError):  # pragma: no cover - dépend de l'environnement
                pass
        for autorise in self._dossiers_autorises_valides():
            retenir(autorise, "autorise")

        projets = sorted(
            self._projets_lisibles(),
            key=lambda p: (p.created_at or "", p.id),
            reverse=True,
        )
        for projet in projets[:LIMITE_RECENTS]:
            retenir(projet.root_path.parent, "recent")
        for projet in projets:
            retenir(projet.root_path, "projet")
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

        Lève `RootRejected` **motivée** — jamais une liste vide — quand le
        chemin sort des racines (`hors-racines-explorables`), tombe dans une
        zone sensible (`chemin-sensible`, `chemin-systeme`, `depot-maestro`),
        n'existe pas (`dossier-absent`), n'est pas un dossier (`pas-un-dossier`)
        ou n'est pas lisible (`acces-refuse`).
        """
        racines = self.racines()
        brut = (chemin or "").strip()
        # Sans aucune racine, la page d'entrée n'a rien à montrer ; un chemin
        # donné, lui, passe par la frontière et revient `hors-racines-explorables`
        # avec son `autorisation` — la sortie de ce cul-de-sac (#1610).
        if not racines and not brut:
            raise RootRejected(
                "aucune-racine-explorable",
                "Aucun dossier explorable sur ce poste : le dossier utilisateur est "
                "introuvable, et aucun projet ni aucun dossier autorisé n'est déclaré.",
            )
        par_racine = self._projets_par_racine()
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
        """Le dossier `brut` canonicalisé et autorisé, ou `RootRejected` motivée."""
        if not Path(brut).expanduser().is_absolute():
            raise RootRejected(
                "chemin-relatif",
                f"Chemin relatif refusé : {brut!r} — indiquez un chemin absolu.",
                brut,
            )
        cible = canonical(brut)
        check_forbidden_zone(cible)
        if not _sous_une_racine(cible, racines):
            raise DossierHorsFrontiere(cible, racines)
        if not cible.exists():
            raise RootRejected(
                "dossier-absent", f"Dossier introuvable : {cible.as_posix()}.", cible
            )
        if not cible.is_dir():
            raise RootRejected(
                "pas-un-dossier",
                f"Chemin invalide : {cible.as_posix()} n'est pas un dossier.",
                cible,
            )
        return cible

    def _parent_explorable(self, cible: Path, racines: tuple[Path, ...]) -> Path | None:
        """Le dossier au-dessus de `cible` s'il reste explorable, `None` sinon."""
        parent = cible.parent
        if parent == cible or not _sous_une_racine(parent, racines):
            return None
        try:
            check_forbidden_zone(parent)
        except RootRejected:
            return None
        return parent

    def _lire(self, id_: str) -> Project:
        """Le projet `id_`, ou `ProjetInconnu`/`ProjetIllisible` — jamais un 500."""
        try:
            projet = self._store.read(id_)
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

    def _projets_lisibles(self) -> tuple[Project, ...]:
        """Les projets du dépôt, ceux dont le fichier est cassé étant sautés."""
        lisibles = []
        for id_ in self._store.ids():
            try:
                projet = self._store.read(id_)
            except (ValueError, OSError, KeyError):
                continue
            if projet is not None:
                lisibles.append(projet)
        return tuple(lisibles)

    def _projets_par_racine(self) -> dict[str, str]:
        """`{racine canonique → id}` — de quoi dire, en un passage, quel dossier est déjà pris.

        Calculé une fois par appel à l'explorateur : interroger le dépôt dossier
        par dossier (`ProjectStore.by_root`) relirait tous les fichiers à
        chaque entrée listée.
        """
        par_racine: dict[str, str] = {}
        for projet in self._projets_lisibles():
            try:
                par_racine[_cle(canonical(projet.root))] = projet.id
            except OSError:  # pragma: no cover - chemin illisible pour l'OS
                continue
        return par_racine

    def _fiche(self, projet: Project) -> dict[str, Any]:
        """La forme JSON d'un projet — le fichier stocké, **plus l'outillage constaté** (#1034).

        Le fragment `outillage` du fichier ne porte qu'une décision
        (`reporte_le`, docs/37 §4.6) ; la fiche servie y ajoute deux champs que
        seul le disque sait rendre :

        - `genere` — `.maestro/outillage/manifeste.json` est-il là (docs/38 §4.1) ?
          **Constaté, jamais stocké**, pour la raison exacte qui fait constater le
          VCS (EF-38) : le manifeste peut naître d'une génération relancée, être
          retiré à la main, ou venir d'un clone. Un booléen persisté divergerait
          du dossier, et c'est le dossier qui a raison ;
        - `a_faire` — reporté **et** pas encore généré. C'est le critère du ticket
          rendu par le serveur plutôt que redéduit par chaque écran : *un projet
          qu'on a choisi d'outiller plus tard le dit, **tant qu'il ne l'est pas***.
          Générer suffit donc à faire taire le rappel, sans qu'aucun code de
          génération (#1033) ait à connaître ce champ.

        Le coût est un `is_file()` par projet listé — du même ordre que la
        relecture de sa fiche —, et une racine illisible (disque démonté, droits)
        vaut « pas de manifeste » plutôt qu'une liste de projets en panne.
        """
        fiche = projet.to_dict()
        genere = self._outillage_genere(projet)
        fiche["outillage"] = {
            **projet.tooling.to_dict(),
            "genere": genere,
            "a_faire": projet.tooling.deferred and not genere,
        }
        return fiche

    @staticmethod
    def _outillage_genere(projet: Project) -> bool:
        """Le manifeste d'outillage est-il présent dans la racine du projet ?

        Le chemin vient de `maestro.outillage` (`CHEMIN_MANIFESTE`) et n'est
        **jamais recopié** : deux orthographes du même chemin finiraient par
        répondre différemment à la même question (#830).
        """
        try:
            return (projet.root_path / CHEMIN_MANIFESTE).is_file()
        except OSError:  # pragma: no cover - racine illisible pour l'OS
            return False


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
    *pourquoi* ce dossier est proposé (`repertoire`, `utilisateur`, `recent`,
    `projet`, `volume`, `configuree`), ce que l'écran affiche en pastille. Ailleurs elle
    est `null` — un sous-dossier énuméré n'a pas d'autre raison d'être là que
    d'être dans le dossier ouvert.
    """
    return {
        "nom": dossier.name or dossier.as_posix(),
        "chemin": dossier.as_posix(),
        "depot_git": detect_vcs(dossier) is not None,
        "projet_id": par_racine.get(_cle(dossier)),
        "origine": origine,
    }


def _sous_dossiers(cible: Path) -> list[str]:
    """Les noms des sous-dossiers de `cible`, triés — ou `RootRejected` si illisible.

    Rien n'est masqué (ni les dossiers cachés, ni `node_modules`) : un
    explorateur qui filtre en silence fait douter de ce qu'il montre. Le
    marqueur Git et le tri suffisent à s'y retrouver.
    """
    try:
        with os.scandir(cible) as entrees:
            noms = [entree.name for entree in entrees if _est_dossier(entree)]
    except PermissionError as exc:
        raise RootRejected(
            "acces-refuse",
            f"Dossier illisible : {cible.as_posix()} — accès refusé par le système.",
            cible,
        ) from exc
    except OSError as exc:
        raise RootRejected(
            "acces-refuse",
            f"Dossier illisible : {cible.as_posix()} ({exc.strerror or exc}).",
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

    Délègue à `path_in_root` (#221) plutôt que de recomparer des chemins :
    c'est déjà la fonction qui sait résoudre les `..` et les liens symboliques
    **avant** de comparer, et dupliquer cette logique serait dupliquer une
    frontière de sécurité.
    """
    for racine in racines:
        try:
            path_in_root(racine, chemin)
        except (RootRejected, OSError):
            continue
        return racine
    return None


def _racines_defaut() -> tuple[Path, ...]:
    """Le défaut des racines explorables : le dossier utilisateur **et les volumes** (#278).

    Le dossier utilisateur seul était un cul-de-sac : un projet posé sur `D:/`
    ou sur un disque externe n'était pas atteignable, et la seule sortie était
    de renseigner `MAESTRO_EXPLORATEUR_RACINES` — un réglage d'environnement
    pour un geste d'écran. Les volumes du poste élargissent la **frontière** ;
    ils n'ouvrent rien de nouveau, `check_forbidden_zone` continuant de
    refuser `.ssh`, `AppData`, les dossiers système et le dépôt de Maestro, et
    `validate_root` de refuser une racine de disque comme racine de projet.

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
