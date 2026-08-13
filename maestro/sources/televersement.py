"""Le dépôt des sources téléversées : recevoir des octets, puis les rattacher à un run (#317).

Le maillon manquant entre un navigateur et le modèle des sources. Un navigateur
ne livre **jamais de chemin absolu** — il livre des octets ; le `chemin` d'une
source `fichier` est donc quelque chose que le backend **calcule** (#315,
`emplacement_ingestion`), jamais quelque chose qu'un client déclare. Il fallait
donc un endroit où poser ces octets, et un identifiant pour les y retrouver :
c'est ce module, servi par `POST /api/sources`
([docs/05 §6.8](../../docs/05-interface-control-tower.md)).

**Deux temps, et c'est le sujet.** Téléverser dépose la matière dans un dépôt
**hors de tout projet et hors de tout run** ; lancer la **rattache** au run, dans
son emplacement d'ingestion propre. La séparation n'est pas de la plomberie :

- elle permet de **composer** un objectif — déposer, voir, retirer un document —
  avant de dépenser quoi que ce soit, ce qui est tout le propos de la Phase 8 ;
- elle garantit qu'une matière téléversée n'atterrit **jamais** dans le dossier
  de l'utilisateur, pour la raison qui interdit déjà aux agents d'écrire dans la
  racine (EF-36) : une entrée non fiable
  ([docs/19 §2](../../docs/19-securite-modele-de-menace.md)) ne se mêle pas aux
  fichiers qu'il a écrits lui-même ;
- le rattachement **copie** plutôt qu'il ne déplace : relancer le même objectif
  après un échec ne doit pas demander de re-téléverser. Le revers est assumé —
  les octets restent dans le dépôt tant que personne ne les retire
  (`oublier`) ; leur ramassage n'a pas de déclencheur dans ce lot.

**Rien n'est tronqué à l'entrée.** `accueillir` lit par tranches et confronte les
plafonds d'ingestion (ENF-07, `GardeFousIngestion`) **pendant** la lecture : au
premier dépassement, ce qui a été écrit est effacé et le refus part avec son
motif. C'est le régime de la résolution (#315) et non celui de l'extraction
(#316) : une saisie se corrige, donc on refuse ; un fichier à moitié reçu n'est
pas une source, c'est un piège — le rapport de lecture le dirait « lu » et le
brief conclurait sur un document amputé.

Aucune dépendance à FastAPI ici : le dépôt reçoit un **flux binaire** quelconque,
ce qui le rend testable sans requête HTTP et réutilisable par la CLI le jour où
elle voudra joindre un document.
"""

from __future__ import annotations

import re
import shutil
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from maestro.config import Settings
from maestro.engine.guardrails import GardeFousIngestion
from maestro.projets.racine import RacineRefusee, chemin_dans_racine
from maestro.sources.modele import TYPE_FICHIER, SourceRefusee
from maestro.sources.resolution import nom_de_fichier, racine_ingestion

#: Le sous-dossier du stockage d'ingestion où les octets attendent leur run.
#: Le tiret bas n'est pas cosmétique : `ID_RUN` (#315) exige une **alphanumérique**
#: en première position, si bien qu'aucun `run_id` ne peut jamais désigner ce
#: dossier. La collision entre l'espace des téléversements et celui des runs est
#: donc impossible par construction, et non simplement improbable.
DOSSIER_TELEVERSEMENTS = "_televersements"

#: Un identifiant de téléversement admissible comme **segment de chemin**. Même
#: précaution que `ID_RUN` et `ID_PROJET` : l'identifiant revient d'un client, et
#: rien qui ne soit pas conforme ne doit pouvoir remonter d'un cran.
ID_TELEVERSEMENT = re.compile(r"^[A-Za-z0-9]{6,64}\Z")

#: Taille des tranches de lecture (64 Kio). Lire par tranches est ce qui permet
#: de refuser **pendant** la réception plutôt qu'après : un fichier de 2 Gio ne
#: doit pas d'abord être écrit sur le disque pour être ensuite jugé trop gros.
TAILLE_TRANCHE = 64 * 1024


@dataclass(frozen=True)
class Televersement:
    """Des octets reçus et rangés, en attente d'un run.

    `id` est ce que la route rend au client et ce qu'il reportera dans les
    `sources[]` de son lancement ; `nom` est le nom **assaini** (celui du
    navigateur ne sert que de proposition) ; `taille` compte les octets
    **reçus**, jamais ceux qu'on a annoncés ; `chemin` dit où ils sont posés,
    dans le dépôt et pas encore dans un run.
    """

    id: str
    nom: str
    taille: int
    chemin: Path

    def to_dict(self) -> dict[str, Any]:
        """La forme rendue par `POST /api/sources` (docs/05 §6.8).

        Sans le `chemin` : c'est un détail d'implantation du serveur, et le
        client n'en a aucun usage — il désigne sa matière par son `id`.
        """
        return {"id": self.id, "type": TYPE_FICHIER, "nom": self.nom, "taille": self.taille}

    def declaration(self) -> dict[str, Any]:
        """La déclaration de source que le lancement fera résoudre par #315.

        Le nom et la taille viennent **du dépôt** et non de la requête : ce sont
        ceux des octets réellement reçus. Un client qui annoncerait 12 octets
        pour un fichier de 12 Mio passerait sinon le plafond par source.
        """
        return {"type": TYPE_FICHIER, "nom": self.nom, "taille": self.taille}


class DepotTeleversements:
    """Le dépôt des octets reçus : les accueillir, les relire, les rattacher à un run.

    `racine` est le dossier qui les porte (`<ingestion>/_televersements/` par
    défaut, cf. `default`) ; `garde_fous` porte les plafonds d'ingestion
    appliqués à la réception — actifs par défaut, réglables par injection :
    c'est un réglage de déploiement, pas un choix du client qui téléverse.
    """

    def __init__(
        self, racine: Path | str, *, garde_fous: GardeFousIngestion | None = None
    ) -> None:
        self._racine = Path(racine)
        self._garde_fous = garde_fous if garde_fous is not None else GardeFousIngestion()

    @classmethod
    def default(
        cls, settings: Settings | None = None, *, garde_fous: GardeFousIngestion | None = None
    ) -> DepotTeleversements:
        """Le dépôt de la configuration : `<MAESTRO_INGESTION_DIR|core/ingestion>/_televersements/`.

        Rien n'est créé ici — un déploiement où personne ne téléverse ne pose
        aucun dossier, comme `racine_ingestion` dont il hérite le patron.
        """
        return cls(racine_ingestion(settings) / DOSSIER_TELEVERSEMENTS, garde_fous=garde_fous)

    @property
    def garde_fous(self) -> GardeFousIngestion:
        """Les plafonds appliqués — la route en a besoin pour borner le **nombre**."""
        return self._garde_fous

    def accueillir(
        self, nom: str, flux: IO[bytes], *, index: int = 0, deja_recu: int = 0
    ) -> Televersement:
        """Range les octets de `flux` sous un identifiant neuf et rend le téléversement.

        `nom` est le nom proposé par le client : il est **assaini** par le même
        `nom_de_fichier` que la résolution (#315) — ni séparateur, ni `..`, ni
        caractère interdit —, jamais recopié tel quel. Deux assainisseurs
        divergeraient, et c'est le vecteur classique de la traversée de
        répertoire.

        `index` situe le fichier dans l'appel (un formulaire en dépose
        plusieurs) et voyage jusqu'au refus : « un fichier est trop gros » sans
        dire lequel obligerait à tout recommencer. `deja_recu` porte le cumul des
        fichiers déjà acceptés dans le **même** appel, sans quoi le plafond total
        ne serait jamais atteint — vingt fichiers sous le plafond unitaire
        passeraient tous.

        Lève `SourceRefusee` motivée (`nom-invalide`, `source-trop-volumineuse`,
        `ingestion-trop-volumineuse`…) et **ne laisse rien derrière elle** : ce
        qui avait été écrit est effacé avant que le refus ne parte.
        """
        propre = nom_de_fichier(nom, index=index)
        identifiant = uuid.uuid4().hex[:12]
        dossier = self._racine / identifiant
        dossier.mkdir(parents=True, exist_ok=True)
        destination = dossier / propre
        try:
            taille = self._ecrire(flux, destination, index=index, deja_recu=deja_recu)
        except BaseException:
            # `BaseException` et pas `Exception` : une annulation de la tâche qui
            # portait la copie (client déconnecté, arrêt de l'app) laisserait
            # sinon un fichier tronqué dans le dépôt, indiscernable d'un
            # téléversement complet — précisément ce que ce module refuse.
            shutil.rmtree(dossier, ignore_errors=True)
            raise
        return Televersement(id=identifiant, nom=propre, taille=taille, chemin=destination)

    def lire(self, identifiant: str) -> Televersement | None:
        """Le téléversement `identifiant`, ou **None** s'il n'en désigne aucun.

        None plutôt qu'une exception : c'est l'appelant qui sait dire de quel
        refus il s'agit — `televersement-inconnu` à l'index de la source, au
        lancement. Un identifiant non conforme est traité comme inconnu **sans
        toucher au disque** : il n'a pas à devenir un chemin pour être écarté.
        """
        if not ID_TELEVERSEMENT.match(identifiant or ""):
            return None
        try:
            dossier = chemin_dans_racine(self._racine, identifiant)
        except RacineRefusee:  # pragma: no cover - le motif ci-dessus le borne déjà
            return None
        if not dossier.is_dir():
            return None
        # Un dossier, un fichier : le nom du téléversement est celui du fichier
        # qu'il contient, ce qui évite un fichier de métadonnées à écrire, à
        # relire et à désynchroniser.
        fichiers = sorted(entree for entree in dossier.iterdir() if entree.is_file())
        if not fichiers:
            return None
        fichier = fichiers[0]
        return Televersement(
            id=identifiant,
            nom=fichier.name,
            taille=fichier.stat().st_size,
            chemin=fichier,
        )

    def rattacher(self, identifiant: str, destination: Path | str) -> Path:
        """Copie les octets de `identifiant` vers `destination` (l'emplacement du run).

        Le dossier d'accueil est créé au passage : `emplacement_ingestion` (#315)
        se contente de **calculer** le chemin tant qu'on ne lui demande pas
        d'écrire, et le rattachement est justement le moment où l'on écrit.

        Lève `SourceRefusee("televersement-inconnu")` si l'identifiant ne désigne
        rien — jamais une copie silencieusement sautée, qui rendrait un run
        travaillant sur une matière absente.
        """
        televerse = self.lire(identifiant)
        if televerse is None:
            raise SourceRefusee(
                "televersement-inconnu",
                f"Aucun téléversement sous l'identifiant {identifiant!r}.",
            )
        cible = Path(destination)
        cible.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(televerse.chemin, cible)
        return cible

    def oublier(self, identifiant: str) -> None:
        """Retire un téléversement du dépôt — best-effort, muet s'il n'y est plus.

        Le geste qui rend un appel **atomique** : si le troisième fichier d'un
        formulaire fait déborder le plafond total, les deux premiers n'ont pas à
        rester dans le dépôt sous des identifiants que personne n'a reçus.
        """
        if not ID_TELEVERSEMENT.match(identifiant or ""):
            return
        shutil.rmtree(self._racine / identifiant, ignore_errors=True)

    def _ecrire(
        self, flux: IO[bytes], destination: Path, *, index: int, deja_recu: int
    ) -> int:
        """Écrit `flux` dans `destination` par tranches, plafonds appliqués **en cours**.

        Le plafond est vérifié à chaque tranche et non à la fin : c'est ce qui
        distingue « refusé » de « reçu puis jeté », et ce qui évite qu'un fichier
        de plusieurs gigaoctets soit intégralement écrit pour être ensuite
        déclaré trop gros.
        """
        par_source = self._garde_fous.taille_max_source_octets
        total = self._garde_fous.taille_max_totale_octets
        recus = 0
        with destination.open("wb") as sortie:
            while True:
                tranche = flux.read(TAILLE_TRANCHE)
                if not tranche:
                    break
                recus += len(tranche)
                if par_source is not None and recus > par_source:
                    raise SourceRefusee(
                        "source-trop-volumineuse",
                        f"Fichier {index + 1} trop volumineux : plus de {par_source} octets "
                        "reçus — la réception est interrompue, rien n'est conservé.",
                        index=index,
                    )
                if total is not None and deja_recu + recus > total:
                    raise SourceRefusee(
                        "ingestion-trop-volumineuse",
                        f"Téléversement trop volumineux au total : plus de {total} octets "
                        f"cumulés au fichier {index + 1}.",
                        index=index,
                    )
                sortie.write(tranche)
        return recus


def declarer_televersements(
    bruts: Any, *, depot: DepotTeleversements
) -> tuple[Any, tuple[str, ...]]:
    """Remplace les renvois `{"type": "fichier", "id": …}` par ce que le dépôt en sait.

    L'aiguillage entre les deux formes qu'une source `fichier` peut prendre
    (docs/05 §6.1) : le **renvoi** à un téléversement (`id`), et la déclaration
    nue de [docs/24 §3.2](../../docs/24-projets-locaux-et-poste-de-travail.md)
    (`nom` + `taille`, sans octets). Le premier est complété ici — nom et taille
    **du dépôt**, jamais ceux du client —, le second passe tel quel : il résoudra
    puis ressortira `ignore` / `source-absente` au rapport de lecture, ce qui est
    exactement ce que le rapport existe pour dire.

    Rend le tableau complété et, **aligné sur ses index**, l'identifiant de
    téléversement de chaque entrée (chaîne vide quand il n'y en a pas) : c'est
    lui qui permettra de rattacher les octets une fois le chemin du run connu.

    Ce qui n'est pas une liste ressort **inchangé** : le refus de forme appartient
    à `resoudre_sources`, qui le motive déjà (`sources-illisibles`) — le
    dupliquer ici ferait deux messages pour une même faute.
    """
    if bruts is None or not isinstance(bruts, Sequence) or isinstance(bruts, str | bytes):
        return bruts, ()
    declarees: list[Any] = []
    identifiants: list[str] = []
    for index, brut in enumerate(bruts):
        identifiant = _identifiant_televerse(brut)
        if not identifiant:
            declarees.append(brut)
            identifiants.append("")
            continue
        televerse = depot.lire(identifiant)
        if televerse is None:
            raise SourceRefusee(
                "televersement-inconnu",
                f"Source {index + 1} : aucun téléversement sous l'identifiant "
                f"{identifiant!r} — il a peut-être déjà été retiré.",
                index=index,
            )
        declarees.append(televerse.declaration())
        identifiants.append(identifiant)
    return declarees, tuple(identifiants)


def _identifiant_televerse(brut: Any) -> str:
    """L'`id` de téléversement d'une entrée de `sources[]` — vide si elle n'en porte pas."""
    if not isinstance(brut, Mapping):
        return ""
    if str(brut.get("type") or "").strip().lower() != TYPE_FICHIER:
        return ""
    return str(brut.get("id") or "").strip()
