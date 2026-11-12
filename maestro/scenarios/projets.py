"""Les ateliers **jetables** des passages : où ils naissent, ce qu'on y lit, ce qu'on en garde.

Un exercice (`maestro.scenarios.exercice`) comme un comparatif
(`maestro.scenarios.comparatif`) a besoin de projets à lui : il les fait naître ou les
sème dans son **atelier**, et rien n'est joué sur un projet de l'utilisateur.

**Où.** Sous `~/.maestro/ateliers/scenarios/<horodatage>/<projet>` (#1457), et
pas ailleurs pour deux raisons qui se cumulent : `valider_racine` refuse le dépôt
de Maestro lui-même (EF-38) et refuse `AppData`, donc le `TMPDIR` d'un poste
Windows (mesuré en #221). L'état du poste passe les deux, et c'est l'une des trois
racines où Maestro pose quelque chose (`maestro.emplacements`, #1454) — l'atelier
vivait avant à part, à la racine du profil (`~/maestro-scenarios`).
`MAESTRO_SCENARIOS_ATELIER` le déplace pour qui veut un autre disque. Ce dossier
est celui du **poste**, pas d'une copie de travail : un passage y **réserve** son
atelier (`Atelier.reserver`, #1365), et deux copies qui en ouvrent un dans la même
seconde en ont chacune un.

**Ce qui reste après le passage.** Les dossiers, par défaut : ce sont les **pièces**
du verdict, qu'un rouge se comprend en regardant.

**Mais pas tous les passages** (#1457). Garder tous les ateliers en accumulait des
centaines — 208 dossiers le 2026-10-09, projets complets et `node_modules`
compris —, et leurs projets versionnés retenaient à leur tour, par leurs
worktrees, les espaces de tâches laissés sous la racine jetable : le ramassage
(#992) conserve un worktree **tant que son dépôt existe**, donc à vie. Chaque
passage qui réserve son atelier fait donc la **rétention** du poste
(`retenir_le_poste`) : les `PASSAGES_GARDES` derniers restent, plus celui que
`start.sh --etat-banc` rouvre, quel que soit son rang ; et retirer un passage
retire d'abord les worktrees de ses tâches (`Atelier.retirer`), puis les
**déclarations** de ses projets (#1477) dans chaque dépôt du poste. L'ancien atelier
est **repris** par la même règle tant qu'il porte des passages : ses plus récents
comptent parmi les derniers, son état sauvé reste rouvrable, et il disparaît quand
il ne porte plus rien — ce qui n'a pas la forme d'un passage y est nommé, jamais
touché.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from maestro.emplacements import maison, occupant, racine_ateliers, se_nommer_occupant
from maestro.fichiers import retirer_arbre
from maestro.projets.store import ProjetStore
from maestro.sandbox.ramassage import (
    PREFIXE_COMMUN,
    pid_dans,
    pid_vivant,
    racine_des_espaces,
    racines_connues,
)

#: Le dossier où naissent les projets jetables — déplaçable, jamais deviné.
VARIABLE_ATELIER = "MAESTRO_SCENARIOS_ATELIER"

#: Le nom de l'atelier des passages sous la racine des ateliers du poste (voir l'en-tête).
NOM_ATELIER = "scenarios"

#: Où l'atelier vivait avant #1457, à la racine du profil — repris par `retenir`.
ANCIEN_NOM_ATELIER = "maestro-scenarios"

#: Combien de passages la rétention garde, celui que `start.sh --etat-banc` rouvre mis
#: à part (`retenir`). Une décision, pas une mesure : de quoi lire les pièces des
#: derniers rouges et comparer deux passages autour d'un correctif, sans qu'un poste
#: qui exerce tous les jours en accumule des centaines.
PASSAGES_GARDES = 5

#: Le réglage du nombre ci-dessus ; `0` éteint la rétention (rien n'est retiré).
VARIABLE_PASSAGES_GARDES = "MAESTRO_SCENARIOS_PASSAGES_GARDES"

#: La forme du nom d'un passage — celle que `Atelier.reserver` donne : l'horodatage,
#: et son rang quand la seconde était prise. Tout autre dossier de l'atelier (un
#: essai posé là à la main) n'est pas un passage, et la rétention n'y touche pas.
_PASSAGE = re.compile(r"^(\d{8}-\d{6})(?:-(\d+))?$")

#: Où un projet du passage enregistre ses worktrees : `<projet>/.git/worktrees/<nom>/gitdir`
#: nomme le `.git` du worktree. Les projets d'un passage sont ses enfants directs
#: (`Atelier.dossier`).
_MOTIF_GITDIR = "*/.git/worktrees/*/gitdir"


def racine_atelier(environnement: Mapping[str, str] | None = None) -> Path:
    """L'atelier : `MAESTRO_SCENARIOS_ATELIER`, sinon `~/.maestro/ateliers/scenarios`."""
    env = os.environ if environnement is None else environnement
    regle = (env.get(VARIABLE_ATELIER) or "").strip()
    return Path(regle).expanduser() if regle else racine_ateliers() / NOM_ATELIER


def ancienne_racine_atelier(environnement: Mapping[str, str] | None = None) -> Path | None:
    """`~/maestro-scenarios`, l'atelier d'avant #1457 — `None` quand l'atelier est réglé.

    Qui règle `MAESTRO_SCENARIOS_ATELIER` a choisi où vit son atelier : l'ancien
    défaut n'est plus le sien, et rien n'y est lu ni retiré.
    """
    env = os.environ if environnement is None else environnement
    if (env.get(VARIABLE_ATELIER) or "").strip():
        return None
    return maison() / ANCIEN_NOM_ATELIER


def racines_des_passages(environnement: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    """Où vivent les passages du poste : l'atelier, puis l'ancien tant qu'il existe."""
    racines = [racine_atelier(environnement)]
    ancienne = ancienne_racine_atelier(environnement)
    if ancienne is not None and ancienne.is_dir() and _cle(ancienne) != _cle(racines[0]):
        racines.append(ancienne)
    return tuple(racines)


def passages_gardes(environnement: Mapping[str, str] | None = None) -> int:
    """Combien de passages la rétention garde — le réglage s'il est lisible, `0` l'éteint."""
    env = os.environ if environnement is None else environnement
    brut = (env.get(VARIABLE_PASSAGES_GARDES) or "").strip()
    return int(brut) if brut.isdigit() else PASSAGES_GARDES


def _cle(chemin: Path) -> str:
    """Un chemin comparable à un autre : absolu, résolu, casse du système de fichiers."""
    try:
        resolu = chemin.resolve()
    except OSError:  # pragma: no cover - chemin non représentable
        resolu = chemin
    return os.path.normcase(str(resolu))


def _rang_du_passage(nom: str) -> tuple[str, int] | None:
    """`(horodatage, rang)` d'un nom de passage — l'ordre des passages ; `None` sinon.

    Le rang et non le nom : `…-123609-10` vient après `…-123609-9`, que l'ordre
    lexical placerait devant.
    """
    trouve = _PASSAGE.match(nom)
    if trouve is None:
        return None
    return trouve.group(1), int(trouve.group(2) or 1)


def worktrees_laisses(
    passage: Path, environnement: Mapping[str, str] | None = None
) -> tuple[Path, ...]:
    """Les espaces de tâches sous la racine jetable qui portent un worktree d'un projet du passage.

    Lu dans le dépôt de chaque projet, sans appeler Git : `<projet>/.git/worktrees/
    <nom>/gitdir` nomme le `.git` du worktree, absolu (le défaut de Git) ou relatif
    à son propre dossier. Rend le dossier **qui l'enveloppe** — celui que
    `maestro.sandbox.projet.espace_de_travail` ouvre par `jetable` et retire en
    sortant —, et seulement quand il est là où le produit en ouvre : sous la racine
    jetable, ou sous un ancien emplacement avec le préfixe commun. Un worktree que
    la personne aurait ouvert ailleurs n'est jamais rendu.
    """
    jetables = {_cle(racine_des_espaces(environnement))}
    anciennes = {_cle(racine) for racine in racines_connues(environnement)}
    enveloppes: dict[str, Path] = {}
    for fichier in sorted(passage.glob(_MOTIF_GITDIR)):
        try:
            brut = fichier.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not brut:
            continue
        cible = Path(brut)
        if not cible.is_absolute():
            cible = fichier.parent / cible
        enveloppe = cible.parent.parent
        parent = _cle(enveloppe.parent)
        if parent in jetables or (
            parent in anciennes and enveloppe.name.startswith(PREFIXE_COMMUN)
        ):
            enveloppes.setdefault(_cle(enveloppe), enveloppe)
    return tuple(enveloppes.values())


def _enveloppe_occupee(enveloppe: Path) -> bool:
    """Une tâche vivante travaille-t-elle encore dans cet espace ? — le pid de son nom."""
    pid = pid_dans(enveloppe.name)
    return pid is not None and pid_vivant(pid)


def passage_occupe(passage: Path, environnement: Mapping[str, str] | None = None) -> bool:
    """Un process vivant tient-il encore ce passage — qui le joue, ou une de ses tâches ?

    Qui joue se nomme dans le passage qu'il réserve (`TEMOIN_PID`) ; une tâche d'un
    projet versionné se nomme dans l'espace de son worktree. Un pid recyclé fait
    garder un passage de trop : c'est le seul sens où l'erreur est acceptable.
    """
    nomme = occupant(passage)
    if nomme is not None and pid_vivant(nomme):
        return True
    return any(_enveloppe_occupee(e) for e in worktrees_laisses(passage, environnement))


class Atelier:
    """Les racines jetables d'un passage : un dossier par projet, sous l'horodatage."""

    def __init__(self, racine: Path) -> None:
        self._racine = racine
        # Combien de fois chaque nom a déjà été servi : c'est ce qui donne au
        # rejeu son propre dossier (voir `dossier`).
        self._servis: dict[str, int] = {}

    @classmethod
    def pour(
        cls, horodatage: str, *, environnement: Mapping[str, str] | None = None
    ) -> Atelier:
        """L'atelier d'un passage daté — nommé, pas réservé (voir `reserver`)."""
        return cls(racine_atelier(environnement) / horodatage)

    @classmethod
    def reserver(
        cls, horodatage: str, *, environnement: Mapping[str, str] | None = None
    ) -> Atelier:
        """L'atelier d'un passage, **à lui seul** — créé, jamais repris (#1365).

        ⚠ Le dossier des ateliers est celui du **poste** : toutes les copies de
        travail y lancent leurs passages. L'horodatage est à la seconde, et deux
        copies qui en lançaient un dans la même seconde recevaient le même nom,
        donc le même atelier — mesuré le 2026-09-27 à 12:36:09 : deux passages ont
        semé, vidé et rejoué les mêmes dossiers, et l'un est sorti rouge sur un
        `.env` que l'autre avait touché (#1365).

        Le dossier est donc **créé de façon exclusive** (`mkdir` sans
        `exist_ok`, que le système refuse si le nom est pris) : lire « existe-t-il
        ? » puis le créer laisserait passer la course. S'il est pris, le passage
        prend `<horodatage>-2`, puis `-3`… Le premier garde son nom nu, pour la
        même raison que dans `dossier`, et l'ordre lexical tient : `…-123609-2`
        vient après `…-123609` et avant `…-123610`. Le nom retenu **est**
        l'identifiant du passage (`passage`) : relevé, atelier et état sauvé se
        retrouvent par lui.

        Qui le réserve s'y **nomme** (`TEMOIN_PID`, #1457) : c'est ce qui tient la
        rétention d'une autre copie loin d'un passage en cours (`passage_occupe`).
        """
        parent = racine_atelier(environnement)
        parent.mkdir(parents=True, exist_ok=True)
        rang = 1
        while True:
            chemin = parent / (horodatage if rang == 1 else f"{horodatage}-{rang}")
            try:
                chemin.mkdir()
            except FileExistsError:
                rang += 1
                continue
            se_nommer_occupant(chemin)
            return cls(chemin)

    @property
    def racine(self) -> Path:
        """Le dossier de l'atelier."""
        return self._racine

    @property
    def passage(self) -> str:
        """L'identifiant du passage que l'atelier porte — le nom de son dossier."""
        return self._racine.name

    def dossier(self, nom: str) -> Path:
        """Le dossier d'un projet, créé s'il manque — vide, prêt à être semé.

        ⚠ **Un dossier par appel** : une racine déjà déclarée fait **refuser** la
        déclaration (`POST /api/projets` → 422, « racine déjà déclarée par le projet
        … »), et un second projet du même nom mourrait sur son premier appel (mesuré
        le 2026-09-23, passage `20260923-185330`).

        Le second appel rend donc `<nom>-2`, le troisième `<nom>-3`. Le premier
        garde son nom nu : les pièces d'un rouge se lisent à l'œil nu, et
        renommer le cas nominal pour la commodité du rejeu ferait payer le
        lecteur pour un cas rare.
        """
        self._servis[nom] = self._servis.get(nom, 0) + 1
        rang = self._servis[nom]
        chemin = self._racine / (nom if rang == 1 else f"{nom}-{rang}")
        chemin.mkdir(parents=True, exist_ok=True)
        return chemin

    def retirer(self, *, environnement: Mapping[str, str] | None = None) -> Retrait:
        """Efface l'atelier, et d'abord les worktrees que ses tâches ont laissés (#1457).

        Joué par `retenir` pour les passages qui sortent de la rétention — jamais
        d'office sur un passage qu'on garde.

        **Les worktrees d'abord**, parce que c'est le dépôt du projet qui les
        nomme : le retirer en premier ferait perdre leur adresse. Ils sont sous la
        racine jetable, que le ramassage ne vide pas tant que leur dépôt existe —
        c'est son refus de toujours (« du travail non commité ») —, et ce travail-là
        est celui du passage, qu'il emporte avec lui. Un espace qu'une tâche
        vivante occupe encore n'est pas touché ; une fois le dépôt parti, le
        ramassage le reprendra comme toute dépouille.

        Par `retirer_arbre` et non un `rmtree` nu : un projet que le run a mis
        sous Git porte des objets en lecture seule, qu'un `rmtree` laisse derrière
        lui en amputant l'arbre sans le dire (#707, #992). Le geste vit une fois
        dans `maestro.fichiers`, jamais recopié ici.
        """
        retires = restants = 0
        for enveloppe in worktrees_laisses(self._racine, environnement):
            if not _enveloppe_occupee(enveloppe) and retirer_arbre(enveloppe):
                retires += 1
            else:
                restants += 1
        return Retrait(
            passage=retirer_arbre(self._racine), worktrees=retires, worktrees_restants=restants
        )


@dataclass(frozen=True)
class Retrait:
    """Ce que le retrait d'un passage a fait : lui-même, et les worktrees de ses tâches."""

    passage: bool
    worktrees: int = 0
    worktrees_restants: int = 0

    def __bool__(self) -> bool:
        """Vrai quand le dossier du passage est parti — ce qui reste se dit à côté."""
        return self.passage


@dataclass(frozen=True)
class Retention:
    """Ce que la rétention a fait des passages du poste — de quoi l'annoncer en une ligne."""

    gardes: int = 0
    retires: tuple[str, ...] = ()
    worktrees: int = 0
    #: Le passage que `start.sh --etat-banc` rouvre, gardé hors du compte.
    rouvert: str | None = None
    occupes: tuple[str, ...] = ()
    echecs: tuple[str, ...] = ()
    #: L'ancien atelier (`~/maestro-scenarios`) quand il existait au départ…
    ancienne: Path | None = None
    #: … s'il est parti, faute de rien porter de plus…
    ancienne_retiree: bool = False
    #: … et sinon, combien de ses passages restent parmi les gardés…
    ancienne_passages: int = 0
    #: … et ce qu'il y reste qui n'est pas un passage.
    etrangers: tuple[str, ...] = ()
    #: Les déclarations de projets oubliées parce que leur passage n'existe plus (#1477).
    declarations: int = 0
    #: Celles qui ont résisté à l'oubli, par leur fichier.
    declarations_resistantes: tuple[str, ...] = ()

    def lignes(self) -> list[str]:  # noqa: C901
        """L'annonce : rien quand il n'y avait rien à faire, sinon ce qui est parti et resté."""
        lignes: list[str] = []
        if self.retires or self.echecs or self.occupes:
            morceaux = [
                f"{len(self.retires)} passage(s) retiré(s) au-delà des {self.gardes} "
                "derniers gardés"
            ]
            if self.worktrees:
                morceaux.append(
                    f"avec {self.worktrees} worktree(s) de tâches sous la racine jetable"
                )
            if self.rouvert:
                morceaux.append(f"{self.rouvert} gardé pour start.sh --etat-banc")
            if self.occupes:
                morceaux.append(f"{len(self.occupes)} encore en cours, gardé(s)")
            if self.echecs:
                morceaux.append(f"{len(self.echecs)} résistant(s) : {', '.join(self.echecs)}")
            lignes.append("Ateliers des passages : " + " · ".join(morceaux) + ".")
        if self.declarations or self.declarations_resistantes:
            morceaux = [
                f"{self.declarations} déclaration(s) de projet oubliée(s) : leur passage "
                "n'existe plus"
            ]
            if self.declarations_resistantes:
                morceaux.append(
                    f"{len(self.declarations_resistantes)} résistante(s) : "
                    + ", ".join(self.declarations_resistantes)
                )
            lignes.append("Ateliers des passages : " + " · ".join(morceaux) + ".")
        if self.ancienne is None:
            return lignes
        if self.ancienne_retiree:
            lignes.append(f"Ancien atelier {self.ancienne} retiré : il ne portait plus rien.")
            return lignes
        morceaux = []
        if self.ancienne_passages:
            morceaux.append(
                f"repris par la rétention — {self.ancienne_passages} de ses passages restent "
                "parmi les gardés"
            )
        if self.etrangers:
            apercu = ", ".join(self.etrangers[:5])
            if len(self.etrangers) > 5:
                apercu += f", … ({len(self.etrangers)} en tout)"
            morceaux.append(
                f"{len(self.etrangers)} dossier(s) qui ne sont pas des passages y restent, "
                f"jamais touchés (à retirer à la main) : {apercu}"
            )
        if morceaux:
            lignes.append(f"Ancien atelier {self.ancienne} : " + " ; ".join(morceaux) + ".")
        return lignes


def retenir(  # noqa: C901
    *,
    rouvert: Path | None = None,
    environnement: Mapping[str, str] | None = None,
    avant: Callable[[int], None] = lambda _nombre: None,
    declarations: Sequence[Path] = (),
) -> Retention:
    """Ne garde que les derniers passages du poste — et celui qu'on rouvre (#1457).

    Les passages de l'atelier **et** de l'ancien (`racines_des_passages`) se
    rangent ensemble, du plus récent au plus ancien : les `passages_gardes`
    premiers restent, les autres partent par `Atelier.retirer` — worktrees de
    leurs tâches compris. Deux exceptions, jamais retirées quel que soit leur
    rang : `rouvert`, le passage dont `start.sh --etat-banc` rouvre l'état (le
    rejouer coûte du vrai modèle), et un passage qu'un process vivant tient encore
    (`passage_occupe`) — celui qu'une autre copie est en train de jouer.

    `avant` reçoit le nombre de passages à retirer avant le premier retrait : la
    première rétention d'un poste qui a gardé deux cents passages prend du temps, et
    ce temps s'annonce (#418). Best-effort de bout en bout : un passage qui résiste
    se nomme et reste pour la prochaine fois.

    Les **déclarations** des projets d'un passage parti partent avec lui (#1477) : dans
    chaque dépôt de `declarations` — l'appelant les nomme, `maestro.menage.depots_de_
    declarations` pour `retenir_le_poste` —, celles dont la racine vit dans un passage
    qui n'existe plus sont oubliées par `ProjetStore.supprimer`, le verbe de
    `DELETE /api/projets`.
    Un passage déjà parti compte : c'est ce qui solde celles que les rétentions d'avant
    ont laissées. Rien d'autre n'est touché — ni un passage gardé, occupé ou résistant
    (son dossier est encore là), ni un projet hors des passages, même disparu : c'est au
    ménage d'en juger, sur un « oui ».
    """
    env = os.environ if environnement is None else environnement
    gardes = passages_gardes(env)
    if gardes <= 0:
        return Retention()
    ancienne = ancienne_racine_atelier(env)
    ancienne = ancienne if ancienne is not None and ancienne.is_dir() else None
    racines = racines_des_passages(env)
    passages: list[tuple[tuple[str, int], Path]] = []
    for racine in racines:
        for chemin in _sous_dossiers(racine):
            rang = _rang_du_passage(chemin.name)
            if rang is not None:
                passages.append((rang, chemin))
    passages.sort(key=lambda paire: paire[0], reverse=True)
    cle_rouvert = None if rouvert is None else _cle(rouvert)

    a_retirer: list[Path] = []
    garde_rouvert: str | None = None
    occupes: list[str] = []
    for _rang, chemin in passages[gardes:]:
        if cle_rouvert is not None and _cle(chemin) == cle_rouvert:
            garde_rouvert = chemin.name
        elif passage_occupe(chemin, env):
            occupes.append(chemin.name)
        else:
            a_retirer.append(chemin)

    if a_retirer:
        avant(len(a_retirer))
    retires: list[str] = []
    echecs: list[str] = []
    worktrees = 0
    for chemin in a_retirer:
        retrait = Atelier(chemin).retirer(environnement=env)
        worktrees += retrait.worktrees
        (retires if retrait else echecs).append(chemin.name)
    oubliees, resistantes = _oublier_les_declarations(declarations, racines)

    ancienne_retiree = False
    ancienne_passages = 0
    etrangers: tuple[str, ...] = ()
    if ancienne is not None:
        restants = sorted(enfant.name for enfant in _entrees(ancienne))
        if not restants:
            try:
                ancienne.rmdir()
                ancienne_retiree = True
            except OSError:
                pass
        ancienne_passages = sum(1 for nom in restants if _rang_du_passage(nom) is not None)
        etrangers = tuple(nom for nom in restants if _rang_du_passage(nom) is None)
    return Retention(
        gardes=gardes,
        retires=tuple(retires),
        worktrees=worktrees,
        rouvert=garde_rouvert,
        occupes=tuple(occupes),
        echecs=tuple(echecs),
        ancienne=ancienne,
        ancienne_retiree=ancienne_retiree,
        ancienne_passages=ancienne_passages,
        etrangers=etrangers,
        declarations=oubliees,
        declarations_resistantes=resistantes,
    )


def retenir_le_poste(dire: Callable[[str], None]) -> None:
    """La rétention du poste, jouée par chaque passage qui réserve son atelier (#1457).

    Le passage que `start.sh --etat-banc` rouvrirait est gardé quel que soit son rang
    (`maestro.scenarios.etat.dernier`), et les déclarations des passages partis sont
    oubliées dans chaque dépôt du poste (`maestro.menage.depots_de_declarations`, #1477)
    — rien n'est cherché quand la rétention est éteinte. Ce qui part, et ce qui résiste,
    se dit par `dire`. Best-effort : la rétention ne fait jamais échouer un passage.
    """
    from maestro import menage
    from maestro.scenarios import etat

    rouvert = etat.dernier()
    depots = menage.depots_de_declarations()[0] if passages_gardes() > 0 else ()
    retention = retenir(
        rouvert=None if rouvert is None else rouvert.dossier.parent,
        avant=lambda nombre: dire(
            f"Ateliers des passages : {nombre} passage(s) au-delà des derniers gardés — "
            "retrait en cours, worktrees de leurs tâches compris…"
        ),
        declarations=depots,
    )
    for ligne in retention.lignes():
        dire(ligne)


def passage_disparu(racine_du_projet: Path | str, racines: Sequence[Path]) -> bool:
    """La racine d'un projet vit-elle dans un passage de l'atelier qui n'existe plus ?

    Le passage est l'enfant d'une des `racines` (`racines_des_passages`) qui mène au
    projet, et il doit en avoir la forme (`_PASSAGE`) : un essai posé à la main dans
    l'atelier n'est pas un passage. Un dossier qu'on ne sait pas lire n'est pas « parti ».
    """
    cle = _cle(Path(racine_du_projet))
    for racine in racines:
        base = _cle(racine)
        if not cle.startswith(base + os.sep):
            continue
        nom = cle[len(base) + 1 :].split(os.sep, 1)[0]
        if _rang_du_passage(nom) is None:
            return False
        try:
            return not (racine / nom).exists()
        except OSError:  # pragma: no cover - dossier illisible : il n'est pas parti
            return False
    return False


def _oublier_les_declarations(
    depots: Sequence[Path], racines: Sequence[Path]
) -> tuple[int, tuple[str, ...]]:
    """Oublie, dans chaque dépôt, les projets dont le passage n'existe plus (voir `retenir`).

    Best-effort comme le reste : une déclaration illisible n'est pas à nous de la juger
    — le ménage la nomme —, et une qui résiste au retrait se nomme par son fichier.
    """
    oubliees = 0
    resistantes: list[str] = []
    for depot in depots:
        store = ProjetStore(depot)
        for id_ in store.ids():
            try:
                projet = store.lire(id_)
            except (OSError, ValueError, TypeError, KeyError):
                continue
            if projet is None or not passage_disparu(projet.racine, racines):
                continue
            try:
                store.supprimer(id_)
            except OSError:
                resistantes.append(str(store.fichier(id_)))
                continue
            oubliees += 1
    return oubliees, tuple(resistantes)


def _entrees(racine: Path) -> Iterator[Path]:
    """Les entrées de `racine` — rien si elle est absente ou illisible."""
    try:
        yield from racine.iterdir()
    except OSError:
        return


def _sous_dossiers(racine: Path) -> list[Path]:
    """Les sous-dossiers de `racine`, triés — vide si elle est absente ou illisible."""
    dossiers: list[Path] = []
    for chemin in sorted(_entrees(racine)):
        try:
            if chemin.is_dir():
                dossiers.append(chemin)
        except OSError:  # pragma: no cover - entrée illisible
            continue
    return dossiers


# --- Ce qu'on lit dans son dépôt (#1408) ------------------------------------
#
# Un projet versionné porte son travail dans Git : chaque tâche sur sa branche, et le
# projet livré sur la sienne (#705). Un passage **lit** ce dépôt, et n'y écrit jamais —
# il ne fait que le cloner ailleurs, dans son atelier, comme le ferait n'importe qui
# pour essayer ce que le run a livré.

#: Ce qu'on laisse à une lecture Git locale : une borne d'anomalie, pas un budget.
DELAI_GIT_S = 60.0


def en_cours_d_ecriture(racine: Path, branche: str) -> tuple[str, ...]:
    """Ce que le worktree qui porte `branche` montre de non commité — vide s'il n'y en a pas.

    Le travail d'une tâche **pendant** qu'elle l'écrit (#1392) : un projet versionné
    la fait travailler dans un worktree de sa branche, hors de la racine, et ce
    qu'elle y a écrit sans le commiter ne se lit qu'en lui (`salissures`) — et un
    travail sauvé par l'extinction n'y est plus en attente. Lève `OSError` si Git ne
    répond pas.

    Un worktree que Git dit `prunable` n'a rien à montrer (#1447) : son adresse est
    vide, ou n'a plus de `.git` — la coquille qu'un démontage coupé par l'extinction
    laissait (« not a git repository »). Ce qui s'y trouvait n'est en attente nulle
    part : c'est la branche qui dit s'il a été sauvé.
    """
    liste = _git(racine, "worktree", "list", "--porcelain")
    if liste.returncode != 0:
        raise OSError(f"`git worktree list` illisible dans {racine} : {_message(liste)}")
    for bloc in liste.stdout.split("\n\n"):
        lignes = bloc.splitlines()
        if any(ligne.split(" ", 1)[0] == "prunable" for ligne in lignes):
            continue
        champs = dict(ligne.split(" ", 1) for ligne in lignes if " " in ligne)
        if champs.get("branch") == f"refs/heads/{branche}" and "worktree" in champs:
            return salissures(Path(champs["worktree"]))
    return ()


def cloner(racine: Path, cible: Path) -> None:
    """Clone ce que le projet a **commité** dans `cible` — vide ou absent. Lève `OSError`.

    C'est ce qu'une personne récupère du projet, et la seule chose qui en soit le
    livrable : ni ce qui traîne non commité dans la racine, ni ce que les commandes
    d'un agent y ont laissé.
    """
    resultat = subprocess.run(  # noqa: S603 - git, sur deux chemins du banc
        ["git", "clone", "--quiet", "--no-hardlinks", str(racine), str(cible)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=DELAI_GIT_S,
        check=False,
    )
    if resultat.returncode != 0:
        raise OSError(f"`git clone` du projet refusé : {_message(resultat)}")


#: L'identité du commit d'instantané : celle d'un poste sans identité Git (#333).
IDENTITE_INSTANTANE: tuple[str, ...] = (
    "-c",
    "user.name=banc",
    "-c",
    "user.email=banc@maestro.invalid",
    "-c",
    "commit.gpgsign=false",
)
MESSAGE_INSTANTANE = "instantané du dossier livré, pris par le banc"

#: Ce qu'on laisse à l'instantané d'un dossier entier : un livrable qui n'ignore pas
#: ses dépendances les inscrit toutes, et c'est plus long qu'une lecture Git.
DELAI_INSTANTANE_S = 600.0


def instantaner(dossier: Path, depot: Path, cible: Path) -> None:
    """Clone dans `cible` ce que `dossier`, non versionné, livre — moins ce qu'il ignore (#1513).

    Ce qu'une personne récupère d'un projet qu'elle n'a pas demandé sous Git, c'est
    son dossier. Le banc l'inscrit dans un dépôt à part (`depot`), dont `dossier` est
    l'arbre de travail : rien n'est écrit dans le dossier livré — ni `.git`, ni
    index —, et ce que ses fichiers `.gitignore` excluent n'est pas pris, comme une
    dépendance installée ou un build. Puis il clone ce dépôt comme il clonerait un
    projet versionné (`cloner`). Lève `OSError` si Git refuse.
    """
    depot.mkdir(parents=True, exist_ok=True)
    arbre = ("--git-dir", str(depot / ".git"), "--work-tree", str(dossier))
    commit = ("commit", "--allow-empty", "--quiet", "-m", MESSAGE_INSTANTANE)
    for arguments in (
        ("init", "--quiet", str(depot)),
        (*arbre, "add", "-A"),
        (*arbre, *IDENTITE_INSTANTANE, *commit),
    ):
        try:
            fait = subprocess.run(  # noqa: S603 - git, argv construit ici
                ["git", *arguments],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=DELAI_INSTANTANE_S,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise OSError(
                f"l'instantané du dossier n'a pas abouti en {DELAI_INSTANTANE_S:g} s"
            ) from exc
        if fait.returncode != 0:
            raise OSError(f"l'instantané du dossier est refusé par Git : {_message(fait)}")
    cloner(depot, cible)


def fichiers_suivis(dossier: Path) -> tuple[str, ...]:
    """Ce que le dépôt de `dossier` suit — `git ls-files`, en chemins POSIX (#1512).

    Ce que le banc comparatif montre d'un livrable rejoué : ce qui a été commité, et
    rien de ce que ses commandes ont refabriqué. Lève `OSError` si Git ne répond pas.
    """
    resultat = _git(dossier, "ls-files", "-z")
    if resultat.returncode != 0:
        raise OSError(f"`git ls-files` illisible dans {dossier} : {_message(resultat)}")
    return tuple(chemin for chemin in resultat.stdout.split("\0") if chemin)


def sujets_des_commits(dossier: Path, *, nombre: int, depuis: str = "") -> tuple[str, ...]:
    """Les sujets des `nombre` derniers commits de `dossier`, du plus récent au plus ancien (#1512).

    `depuis` borne l'histoire à ce que `HEAD` ajoute à ce commit (`depuis..HEAD`) : le
    livrable d'un ticket sur un dépôt existant, sans l'historique de ceux d'avant (#1544).
    Vide sur un dépôt sans commit. Lève `OSError` si Git ne répond pas.
    """
    plage = (f"{depuis}..HEAD",) if depuis else ()
    resultat = _git(dossier, "log", f"-n{nombre}", "--format=%s", *plage)
    if resultat.returncode != 0:
        # Un dépôt sans commit : `git log` le dit en échouant, et il n'y a rien à lire.
        if _git(dossier, "rev-parse", "--verify", "--quiet", "HEAD").returncode != 0:
            return ()
        raise OSError(f"`git log` illisible dans {dossier} : {_message(resultat)}")
    return tuple(ligne for ligne in resultat.stdout.splitlines() if ligne.strip())


def commit_de(dossier: Path, reference: str) -> str | None:
    """Le commit complet que `reference` désigne dans `dossier` — `None` s'il n'en désigne aucun.

    Une référence qui commence par un tiret n'en désigne aucun : Git la lirait comme une
    option. Lève `OSError` si Git ne répond pas.
    """
    if not reference or reference.startswith("-"):
        return None
    resultat = _git(dossier, "rev-parse", "--verify", "--quiet", f"{reference}^{{commit}}")
    if resultat.returncode != 0:
        return None
    return resultat.stdout.strip() or None


#: Les états d'un fichier dans un diff, en mots (`git diff --name-status`).
ETATS_DU_DIFF: dict[str, str] = {"A": "ajouté", "M": "modifié", "D": "supprimé", "T": "modifié"}


def changements_depuis(dossier: Path, base: str) -> tuple[tuple[str, str], ...]:
    """Ce que `HEAD` change depuis `base` : `(état, chemin)` par fichier, en chemins POSIX (#1544).

    L'état est celui de `ETATS_DU_DIFF` ; un renommage se lit comme une suppression et un
    ajout. Lève `OSError` si Git ne répond pas.
    """
    resultat = _git(dossier, "diff", "--name-status", "--no-renames", "-z", base, "HEAD")
    if resultat.returncode != 0:
        raise OSError(f"`git diff` illisible dans {dossier} : {_message(resultat)}")
    morceaux = [morceau for morceau in resultat.stdout.split("\0") if morceau]
    return tuple(
        (ETATS_DU_DIFF.get(etat[:1], etat), chemin)
        for etat, chemin in zip(morceaux[::2], morceaux[1::2], strict=False)
    )


def salissures(dossier: Path) -> tuple[str, ...]:
    """Ce que le dépôt de `dossier` montre de changé ou d'inconnu — `git status --porcelain`.

    Vide : rien de suivi n'a bougé, et tout ce qui est apparu est ignoré par le projet.
    Lève `OSError` si Git ne répond pas : un arbre illisible n'est pas un arbre propre.
    """
    resultat = _git(dossier, "status", "--porcelain", "--untracked-files=normal")
    if resultat.returncode != 0:
        raise OSError(f"`git status` illisible dans {dossier} : {_message(resultat)}")
    return tuple(ligne.rstrip() for ligne in resultat.stdout.splitlines() if ligne.strip())


def _git(dossier: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Une lecture Git dans `dossier` — rend le résultat, ne lève que si Git est absent."""
    try:
        return subprocess.run(  # noqa: S603 - git, argv construit ici
            ["git", "-C", str(dossier), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DELAI_GIT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OSError(f"`git {arguments[0]}` sans réponse en {DELAI_GIT_S:g} s") from exc


def _message(resultat: subprocess.CompletedProcess[str]) -> str:
    """Ce que Git a dit, en une ligne."""
    texte = ((resultat.stderr or "") + (resultat.stdout or "")).strip()
    return " ".join(texte.split())[:300] or f"code {resultat.returncode}"
