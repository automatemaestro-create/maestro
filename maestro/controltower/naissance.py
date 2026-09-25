"""Un projet naît dans la conversation : ce que le code vérifie, déclare et relit (#1294).

Le fil de l'orchestration **comprend** ce que la personne veut faire et **propose**
un projet — un nom, un dossier, le versionnement, chacun avec sa raison
(`maestro.controltower.orchestration`, verdict `projet`). Ce module est l'autre
moitié, celle que le modèle ne tient pas : *Maestro juge, l'exécution vérifie*
(docs/41). Trois gestes, dans l'ordre où la conversation les appelle :

- `contexte` — les **faits** du poste que le modèle reçoit avant de proposer : où
  naît un projet neuf (le répertoire des projets, #1022), les projets déjà
  déclarés, si Git est là, et le projet de la fenêtre. Sans eux, il inventerait un
  dossier ou proposerait un nom déjà pris ;
- `verifier` — la proposition du modèle confrontée au disque **avant** d'être
  montrée : la frontière d'EF-38 (`valider_racine`), un dossier neuf qui existe déjà
  et n'est pas vide, un nom ou une racine déjà déclarés, Git absent. Ce qui se
  corrige se corrige en le **disant** (`DemandeProjet.ajustements` : l'alternative et
  pourquoi) ; ce qui ne se corrige pas refuse (`NaissanceRefusee`), et le fil le dit
  au lieu de montrer une carte qu'aucun accord ne pourrait honorer ;
- `declarer` — sur accord seulement : la déclaration par le **même** chemin que
  `POST /api/projets` (`ServiceProjets.creer`), puis la mise sous Git par celui de
  `POST …/versionner` (#855) si elle était proposée. Rien d'autre ne s'écrit : le
  dossier accordé, et son `.git` si on l'a accepté — la frontière d'écriture
  (docs/24 §2.4) ne bouge pas ;
- `comprendre` — un dossier **importé** est lu, et ce qu'on en a compris revient au
  fil (#1158, la lecture du projet par le modèle, `ServiceOutillage.analyser`). Il
  n'est lu qu'**après** l'accord : on ne parcourt pas un dossier que la personne n'a
  pas encore confié.

Rien ici ne parle à la personne : les mots sont ceux du modèle, les faits voyagent
sur la carte (`DemandeProjet`, `ProjetCree`). Seuls les refus portent une phrase,
parce qu'ils disent pourquoi **rien** ne s'est fait — et que seul ce code le sait.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import unicodedata
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from maestro.controltower.chat import (
    ORIGINE_EXISTANT,
    ORIGINE_NOUVEAU,
    DemandeProjet,
    ProjetCree,
)
from maestro.controltower.projets import ServiceProjets
from maestro.projets import RacineRefusee, canonique, detecter_vcs, valider_racine

#: Combien de projets déclarés entrent dans les faits, au plus. Au-delà, le compte
#: est dit : le modèle n'en a besoin que pour ne pas reprendre un nom ou un dossier,
#: et la vérification tient la règle quoi qu'il voie.
PROJETS_DANS_LES_FAITS = 30

#: La longueur d'un nom de projet, au plus — celle d'une ligne de carte. Un nom plus
#: long est coupé, et la carte le montre tel qu'il sera déclaré.
NOM_MAX = 80

#: Combien d'alternatives numérotées essayer pour un nom ou un dossier déjà pris.
#: Au-delà, la proposition est refusée plutôt que de numéroter à l'infini.
ALTERNATIVES_MAX = 50

#: Ce que la lecture d'un dossier importé rend au fil, au plus.
COMPRIS_MAX = 1500


class NaissanceRefusee(ValueError):
    """La proposition ne peut pas être montrée telle quelle, et rien ne la corrige.

    Porte un `motif` stable, comme les refus des routes projets
    (`controltower.projets.detail_refus`) : un dossier hors des frontières d'EF-38,
    un dossier existant introuvable, un dossier déjà déclaré par un autre projet.
    """

    def __init__(self, motif: str, message: str) -> None:
        super().__init__(message)
        self.motif = motif


#: Lit un projet déclaré et rend son analyse (`ServiceOutillage.analyser`, #1158).
LecteurDeProjet = Callable[[str], Awaitable[Mapping[str, Any]]]


class ServiceNaissance:
    """Vérifie, déclare et relit le projet que la conversation fait naître (#1294).

    `projets` est le service des projets de l'app — le seul écrivain des
    déclarations, donc la seule définition de ce qu'est un projet valide.
    `lecteur` lit un dossier importé une fois déclaré ; sans lui, l'import se fait
    et le fil dit qu'il n'a rien lu. `git_disponible` dit si le poste peut
    versionner ; injectable pour les tests, il regarde `PATH` par défaut.
    """

    def __init__(
        self,
        projets: ServiceProjets,
        *,
        lecteur: LecteurDeProjet | None = None,
        git_disponible: Callable[[], bool] | None = None,
    ) -> None:
        self._projets = projets
        self._lecteur = lecteur
        self._git = git_disponible or (lambda: shutil.which("git") is not None)

    # --- Les faits du poste -------------------------------------------------

    def contexte(self, projet_id: str | None) -> str:
        """Les faits qu'il faut au modèle pour proposer un projet — une sonde, jamais un verdict.

        Lus à chaque message, comme les autres blocs du fil : un projet déclaré ou
        un répertoire réglé entre deux tours doit se voir au suivant.
        """
        lignes = ["Les projets de ce poste :"]
        parent, refus = self._repertoire_lu()
        if parent is not None:
            lignes.append(
                f"- répertoire des projets (où naît un projet neuf) : {parent.as_posix()}"
            )
        else:
            lignes.append(
                f"- répertoire des projets inutilisable : {refus} — un projet neuf "
                "demande alors un dossier que la personne indique"
            )
        lignes.append(f"- Git sur le poste : {'disponible' if self._git() else 'absent'}")
        declares = self._projets.lister()
        if not declares:
            lignes.append("- projets déjà déclarés : aucun")
        else:
            lignes.append("- projets déjà déclarés (nom, dossier) :")
            for fiche in declares[:PROJETS_DANS_LES_FAITS]:
                lignes.append(f"  - « {fiche['nom']} » — {fiche['racine']}")
            if len(declares) > PROJETS_DANS_LES_FAITS:
                reste = len(declares) - PROJETS_DANS_LES_FAITS
                lignes.append(f"  - … et {reste} autre(s)")
        fenetre = next((f for f in declares if f["id"] == projet_id), None)
        if fenetre is None:
            lignes.append(
                "- projet de cette fenêtre : aucun — cette conversation ne travaille "
                "encore sur aucun projet"
            )
        else:
            lignes.append(
                f"- projet de cette fenêtre : « {fenetre['nom']} » — {fenetre['racine']}"
            )
        return "\n".join(lignes)

    # --- La vérification ----------------------------------------------------

    def verifier(self, brute: Mapping[str, Any]) -> DemandeProjet:
        """La proposition du modèle, confrontée au disque et aux projets déjà déclarés.

        Rend la `DemandeProjet` que la carte montrera — ajustée au besoin, chaque
        ajustement dit — ou lève `NaissanceRefusee`. Ne crée **rien** : un dossier
        neuf n'est vérifié que sur ses frontières, il naît à l'accord.
        """
        raisons = brute.get("raisons")
        raisons = raisons if isinstance(raisons, Mapping) else {}
        nom = " ".join(str(brute.get("nom") or "").split())[:NOM_MAX]
        dossier = str(brute.get("dossier") or "").strip()
        origine = str(brute.get("origine") or "").strip().lower()
        if origine not in (ORIGINE_NOUVEAU, ORIGINE_EXISTANT):
            origine = ORIGINE_NOUVEAU
        declares = self._projets.lister()
        ajustements: list[str] = []

        if origine == ORIGINE_EXISTANT:
            racine = self._dossier_existant(dossier, declares)
            nom = nom or racine.name
            deja_versionne = detecter_vcs(racine) is not None
        else:
            racine = self._dossier_neuf(dossier, nom, declares, ajustements)
            nom = nom or racine.name
            deja_versionne = False

        nom = self._nom_libre(nom, declares, ajustements)
        versionner = bool(brute.get("versionner")) and not deja_versionne
        if versionner and not self._git():
            versionner = False
            ajustements.append(
                "Git n'est pas disponible sur ce poste : le projet sera déclaré sans "
                "versionnement."
            )
        return DemandeProjet(
            nom=nom,
            racine=racine.as_posix(),
            origine=origine,
            versionner=versionner,
            deja_versionne=deja_versionne,
            raison_nom=_phrase(raisons.get("nom")),
            raison_dossier=_phrase(raisons.get("dossier")),
            raison_versionnement=_phrase(raisons.get("versionnement")),
            ajustements=tuple(ajustements),
        )

    def _dossier_existant(
        self, dossier: str, declares: Sequence[Mapping[str, Any]]
    ) -> Path:
        """Le dossier à importer : là, déclarable, et à aucun autre projet."""
        if not dossier:
            raise NaissanceRefusee(
                "dossier-requis",
                "aucun dossier n'est nommé pour l'import : il me faut son chemin",
            )
        try:
            racine = valider_racine(dossier)
        except RacineRefusee as refus:
            raise NaissanceRefusee(refus.motif, str(refus)) from refus
        proprietaire = _declare_sur(racine, declares)
        if proprietaire is not None:
            raise NaissanceRefusee(
                "deja-declare",
                f"le dossier {racine.as_posix()} est déjà le projet « {proprietaire} »",
            )
        return racine

    def _dossier_neuf(
        self,
        dossier: str,
        nom: str,
        declares: Sequence[Mapping[str, Any]],
        ajustements: list[str],
    ) -> Path:
        """Le dossier à créer : sous les frontières d'EF-38, vide, et à aucun projet.

        Un chemin relatif — ou pas de chemin du tout — se range sous le répertoire
        des projets, là où la personne a dit que naissent ses projets (#1022). Un
        dossier déjà occupé ou déjà déclaré n'est pas un refus : une alternative
        numérotée est retenue, et la carte le dit.
        """
        parent = self._repertoire()
        brut = dossier or _nom_de_dossier(nom)
        if not brut:
            raise NaissanceRefusee(
                "dossier-requis", "ni nom ni dossier : je ne sais pas où créer ce projet"
            )
        candidat = Path(brut).expanduser()
        if not candidat.is_absolute():
            if parent is None:
                raise NaissanceRefusee(
                    "repertoire-inutilisable",
                    "le répertoire des projets n'est pas utilisable : il me faut un "
                    "dossier complet pour ce projet",
                )
            candidat = parent / candidat
        propose = _admissible(candidat)
        retenu = propose
        rang = 2
        while _occupe(retenu) or _declare_sur(retenu, declares) is not None:
            if rang > ALTERNATIVES_MAX:
                raise NaissanceRefusee(
                    "dossier-pris",
                    f"le dossier {propose.as_posix()} est déjà pris, et aucune variante "
                    "numérotée n'est libre",
                )
            retenu = _admissible(propose.with_name(f"{propose.name}-{rang}"))
            rang += 1
        if retenu != propose:
            pris = (
                "est déjà un projet déclaré"
                if _declare_sur(propose, declares) is not None
                else "existe déjà et n'est pas vide"
            )
            ajustements.append(
                f"Le dossier {propose.as_posix()} {pris} : proposé {retenu.as_posix()} "
                "à la place."
            )
        return retenu

    def _nom_libre(
        self, nom: str, declares: Sequence[Mapping[str, Any]], ajustements: list[str]
    ) -> str:
        """Le nom, ou sa variante numérotée s'il est déjà celui d'un projet déclaré.

        Deux projets du même nom se déclarent sans erreur — leur identifiant les
        sépare —, mais le sélecteur et le fil les confondraient. La règle est donc
        de le dire et de proposer une variante, jamais de refuser.
        """
        pris = {str(f["nom"]).casefold() for f in declares}
        if nom.casefold() not in pris:
            return nom
        for rang in range(2, ALTERNATIVES_MAX + 1):
            variante = f"{nom} {rang}"
            if variante.casefold() not in pris:
                ajustements.append(
                    f"Le nom « {nom} » est déjà celui d'un projet : proposé « {variante} »."
                )
                return variante
        raise NaissanceRefusee("nom-pris", f"le nom « {nom} » et ses variantes sont pris")

    def _repertoire(self) -> Path | None:
        """Le répertoire des projets s'il est utilisable — `None` sinon (#1022)."""
        return self._repertoire_lu()[0]

    def _repertoire_lu(self) -> tuple[Path | None, str]:
        """Le répertoire des projets et, s'il est inutilisable, pourquoi.

        Lu **sans le créer** : la conversation le lit à chaque message, et un
        dossier qui naîtrait parce qu'on a dit bonjour au fil serait une écriture
        sans accord. Un répertoire qui n'existe pas encore est utilisable pour
        autant — la déclaration du premier projet le crée, avec lui.
        """
        lu = self._projets.repertoire()
        refus = lu["refus"]
        if refus is None:
            return Path(lu["chemin"]), ""
        if refus["motif"] == "dossier-absent" and lu["chemin"]:
            return canonique(lu["chemin"]), ""
        return None, str(refus["message"])

    # --- La déclaration ------------------------------------------------------

    async def declarer(self, demande: DemandeProjet) -> ProjetCree:
        """Déclare le projet accordé, puis le met sous Git si c'était proposé.

        Par les verbes de `ServiceProjets` et eux seuls — la déclaration de
        `POST /api/projets`, la mise sous Git de `POST …/versionner` (#855) —, hors
        de la boucle d'événements : créer un dossier et un premier commit touchent
        le disque. Une déclaration refusée **lève** (rien n'est créé, le fil le
        dit) ; une mise sous Git refusée **ne lève pas** : le projet existe, et le
        fait voyage avec lui (`versionnement_refuse`).
        """
        fiche = await asyncio.to_thread(
            self._projets.creer, demande.nom, demande.racine, origine=demande.origine
        )
        refus = ""
        if demande.versionner and fiche.get("vcs") is None:
            try:
                fiche = await asyncio.to_thread(self._projets.versionner, str(fiche["id"]))
            except ValueError as echec:  # VersionnementRefuse, RacineRefusee : motivés
                refus = str(echec)
        return ProjetCree(
            id=str(fiche["id"]),
            nom=str(fiche["nom"]),
            racine=str(fiche["racine"]),
            origine=str(fiche["origine"]),
            versionne=fiche.get("vcs") is not None,
            versionnement_refuse=refus,
        )

    async def comprendre(self, projet_id: str) -> str:
        """Ce que la lecture d'un projet importé en a compris, en quelques lignes (#1158).

        Ne lève jamais : un projet déclaré qu'on n'a pas su lire reste déclaré, et
        le fil dit qu'il n'a rien lu plutôt que de perdre la déclaration.
        """
        if self._lecteur is None:
            return ""
        try:
            analyse = await self._lecteur(projet_id)
        except Exception as echec:  # noqa: BLE001 — lire est un plus, déclarer est fait
            return f"La lecture du dossier a échoué : {echec}."
        return _compris(analyse)


def _compris(analyse: Mapping[str, Any]) -> str:
    """L'analyse d'un projet en faits pour le fil — le résumé, les langages, les commandes."""
    lignes: list[str] = []
    resume = str(analyse.get("resume") or "").strip()
    if resume:
        lignes.append(f"Résumé de la lecture : {resume}")
    constats = analyse.get("constats")
    constats = constats if isinstance(constats, Mapping) else {}
    langages = [
        str(langage.get("nom") or "")
        for langage in constats.get("langages") or ()
        if isinstance(langage, Mapping)
    ]
    if any(langages):
        lignes.append("Langages : " + ", ".join(nom for nom in langages if nom))
    for commande in constats.get("commandes") or ():
        if isinstance(commande, Mapping) and commande.get("commande"):
            lignes.append(f"Commande « {commande.get('usage')} » : {commande.get('commande')}")
    lecture = analyse.get("lecture")
    if isinstance(lecture, Mapping) and lecture.get("etat") not in (None, "lue"):
        lignes.append(f"La lecture par le modèle n'a pas abouti : {lecture.get('motif') or '—'}")
    texte = "\n".join(lignes)
    return texte if len(texte) <= COMPRIS_MAX else texte[: COMPRIS_MAX - 1] + "…"


def _phrase(brut: Any) -> str:
    """Une raison du modèle, sur une ligne — rien quand il n'en a pas donné."""
    return " ".join(str(brut or "").split())


def _nom_de_dossier(nom: str) -> str:
    """Un nom de dossier tiré du nom du projet : sans accents, en minuscules, à tirets."""
    sans_accents = "".join(
        c for c in unicodedata.normalize("NFD", nom) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", "-", sans_accents.lower()).strip("-")


def _admissible(candidat: Path) -> Path:
    """`candidat` résolu s'il passe les frontières d'EF-38 — sans rien créer.

    `valider_racine` sans création refuse un dossier absent, et c'est le seul refus
    qu'un dossier **neuf** a le droit de passer : il naîtra à l'accord.
    """
    try:
        return valider_racine(candidat)
    except RacineRefusee as refus:
        if refus.motif == "dossier-absent":
            return canonique(candidat)
        raise NaissanceRefusee(refus.motif, str(refus)) from refus


def _occupe(chemin: Path) -> bool:
    """Le dossier existe et n'est pas vide — ou ce n'est pas un dossier."""
    if not chemin.exists():
        return False
    if not chemin.is_dir():
        return True
    return any(chemin.iterdir())


def _declare_sur(racine: Path, declares: Sequence[Mapping[str, Any]]) -> str | None:
    """Le nom du projet déjà déclaré sur `racine`, `None` s'il n'y en a pas."""
    for fiche in declares:
        try:
            if canonique(str(fiche["racine"])) == racine:
                return str(fiche["nom"])
        except OSError:  # pragma: no cover - racine déclarée devenue illisible
            continue
    return None
