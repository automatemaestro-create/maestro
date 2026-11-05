"""Le dépôt git du conteneur, pour une tâche sur un projet versionné (ticket #1602).

En mode isolé, l'espace d'une tâche sur un projet **versionné** est un worktree hors de la
racine (`maestro.sandbox.projet`). Son `.git` est un fichier qui pointe dans le `.git` de la
racine, sur l'hôte. Le conteneur ne monte ni la racine ni son `.git` (EF-36), et `.git` est
exclu du périmètre : monté seul, le worktree n'est pas un dépôt pour le git du conteneur.

Ce module donne au conteneur un dépôt **à lui**, construit par l'hôte à côté de l'espace,
et rapatrie ensuite la branche de la tâche dans le dépôt de la personne. Trois règles le
tiennent, et ce sont elles qu'il ne faut pas défaire :

1. **le git de l'hôte ne lit jamais un dépôt que le conteneur a pu écrire.** Une config ou
   un hook posés par l'agent (`core.fsmonitor`, `hooks/`) s'exécuteraient sur l'hôte. Le
   dépôt du conteneur n'est donc lu par l'hôte qu'à sa construction, avant tout conteneur ;
   il est ensuite effacé sans être relu, et reconstruit à la session suivante. Le worktree
   garde son propre `.git`, que le conteneur voit recouvert d'un pointeur en lecture seule ;
2. **ce que le périmètre exclut n'entre pas, même par git.** Le dépôt ne porte que la tête
   de la branche (dépôt superficiel), ses arbres, et les blobs que le périmètre laisse : un
   `.env` versionné n'y a pas de contenu. Le dépôt est déclaré partiel, vers un distant qui
   n'existe pas (`hors-du-perimetre`), pour que git refuse d'en lire un blob au lieu de se
   croire corrompu. Les chemins exclus sont marqués `skip-worktree` : leur masque vide ne se
   commite pas comme un effacement ;
3. **le rapatriement passe par `upload-pack`, joué hors de l'hôte.** La doc de git le dit
   restreint pour un dépôt non fiable, mais sa surface d'attaque est large : le plus sûr est
   de le servir sans privilège. Le côté serveur joue donc dans un conteneur jetable (le
   transport `ext::`, `maestro.sandbox.container`), le dépôt monté en lecture seule. L'hôte
   reçoit un paquet vérifié (`transfer.fsckObjects`) et n'avance la branche de la tâche
   qu'en avance rapide. Une autre branche de l'agent reste dans le conteneur : la rapatrier
   pourrait réécrire `main`.

Le dépôt reprend les réglages de rendu du dépôt de la personne (`REGLAGES_DE_RENDU`). Sans
`core.autocrlf` ni `core.filemode`, le conteneur verrait modifié chaque fichier qu'un poste
Windows a extrait, et l'agent les recommiterait tous.
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from maestro.config import ConfigError
from maestro.fichiers import retirer_arbre
from maestro.projets.modele import Perimetre
from maestro.projets.perimetre import exclu_par

#: Point de montage du dépôt du conteneur, en lecture-écriture.
POINT_DEPOT = "/maestro/depot"

#: Le distant que le dépôt partiel déclare et qui n'existe nulle part : un blob absent —
#: celui d'un chemin exclu — se refuse avec ce nom dans le message de git.
DISTANT_ABSENT = "hors-du-perimetre"

#: Les réglages du dépôt de la personne qui décident de ce que git voit dans l'arbre de
#: travail. Repris tels que l'hôte les lit, système et global compris.
REGLAGES_DE_RENDU: tuple[str, ...] = (
    "core.autocrlf",
    "core.eol",
    "core.filemode",
    "core.symlinks",
    "core.ignorecase",
)

#: Le dépôt d'un espace naît à côté de lui, sous ce suffixe, un nom neuf à chaque session.
_SUFFIXE = ".depot-"

#: Borne d'anomalie d'une commande git (disque réseau, dépôt colossal, démon qui ne répond
#: plus), pas un budget d'attente : construire le dépôt de Maestro prend trois secondes.
_DELAI_S = 300


class DepotIndisponible(ConfigError):
    """Le dépôt du conteneur n'a pas pu être construit : la session ne démarre pas."""


class RapatriementRefuse(Exception):
    """La branche de la tâche n'a pas été rapatriée ; le motif le dit."""


@dataclass(frozen=True)
class Rapatriement:
    """Ce que la fin de session a ramené : la branche, et sa tête avant puis après."""

    branche: str
    avant: str
    apres: str

    @property
    def avance(self) -> bool:
        """La branche a-t-elle avancé ?"""
        return self.avant != self.apres


def pointeur() -> str:
    """Le contenu du `.git` que le conteneur voit dans son espace : le chemin du dépôt."""
    return f"gitdir: {POINT_DEPOT}\n"


def preparer(espace: Path, perimetre: Perimetre) -> Path:
    """Construit le dépôt du conteneur de `espace` et rend son chemin, à côté de l'espace.

    `espace` est le worktree de la tâche, sur sa branche `maestro/<tâche>`. Le dépôt
    construit porte la tête de cette branche, sans les blobs que `perimetre` exclut, un
    index qui la reflète, et les réglages de rendu du dépôt. Les dépôts des sessions
    précédentes du même espace sont effacés d'abord, sans être lus.

    Lève `DepotIndisponible` si git manque, si l'espace n'est pas un worktree sur une
    branche, ou si une étape échoue : le dépôt à moitié construit est alors effacé.
    """
    for ancien in sorted(espace.parent.glob(f"{espace.name}{_SUFFIXE}*")):
        retirer_arbre(ancien)
    branche = _texte(_git("symbolic-ref", "-q", "HEAD", cwd=espace), "branche de l'espace")
    tete = _texte(_git("rev-parse", "--verify", "HEAD^{commit}", cwd=espace), "tête")
    arbre = _texte(_git("rev-parse", "--verify", f"{tete}^{{tree}}", cwd=espace), "arbre")
    entrees = _entrees(espace, tete)
    exclus = [
        chemin
        for _, nature, _, chemin in entrees
        if nature == "blob" and exclu_par(chemin, perimetre)
    ]
    ecartes = set(exclus)
    # L'arbre racine se nomme : `ls-tree -t` ne rend que ce qu'il contient.
    objets = [tete, arbre] + [
        f"{oid} {chemin}"
        for _, nature, oid, chemin in entrees
        if nature == "tree" or (nature == "blob" and chemin not in ecartes)
    ]
    paquet = _git("pack-objects", "--stdout", cwd=espace, entree=_lignes(objets, b"\n"))
    _verifie(paquet, "paquet des objets")
    # À côté de l'espace, dans le jetable qui le porte (`maestro.emplacements`) : le
    # dépôt part avec lui au démontage. Un nom neuf à chaque session, et une création
    # qui refuse un dossier existant.
    depot = espace.parent / f"{espace.name}{_SUFFIXE}{secrets.token_hex(6)}"
    depot.mkdir()
    try:
        _monter(depot, espace, paquet.stdout, branche=branche, tete=tete)
        _indexer(depot, entrees, exclus)
    except BaseException:
        retirer_arbre(depot)
        raise
    return depot


def rapatrier(espace: Path, depot: Path, serveur: Sequence[str]) -> Rapatriement:
    """Ramène la branche de la tâche du dépôt du conteneur dans le dépôt de la personne.

    `serveur` est la commande qui sert `upload-pack` sur `depot` (`%s` y désigne le
    service) : un conteneur jetable en production, jamais le git de l'hôte sur ce dépôt.
    La branche n'avance qu'en avance rapide ; l'index de l'espace est ensuite réaligné sur
    elle, son arbre de travail restant intact. Rien d'autre n'est rapatrié.

    Lève `RapatriementRefuse` sur un historique réécrit par l'agent, un dépôt illisible ou
    un serveur qui ne répond pas. Le contenu de l'espace n'en est pas perdu : la sauvegarde
    du démontage le porte sur la branche (`maestro.sandbox.projet`).
    """
    try:
        return _rapatrier(espace, depot, serveur)
    except DepotIndisponible as exc:
        raise RapatriementRefuse(str(exc)) from exc


def transport_ext(arguments: Sequence[str]) -> str:
    """L'URL `ext::` qui lance `arguments` : un espace s'y écrit `% `, un `%` s'y écrit `%%`.

    `%s` et `%S` passent tels quels : git y met le nom du service (`upload-pack`).
    """
    return "ext::" + " ".join(
        argument if argument in ("%s", "%S") else argument.replace("%", "%%").replace(" ", "% ")
        for argument in arguments
    )


def _rapatrier(espace: Path, depot: Path, serveur: Sequence[str]) -> Rapatriement:
    """`rapatrier`, une étape de git en échec levant `DepotIndisponible`."""
    branche = _texte(_git("symbolic-ref", "-q", "HEAD", cwd=espace), "branche de l'espace")
    avant = _texte(_git("rev-parse", "--verify", "HEAD", cwd=espace), "tête de l'espace")
    if not depot.is_dir():
        raise RapatriementRefuse(f"dépôt du conteneur introuvable : {depot}")
    recu = _git(
        "-c",
        "protocol.ext.allow=always",
        "-c",
        "transfer.fsckObjects=true",
        "fetch",
        "--quiet",
        "--no-tags",
        "--no-write-fetch-head",
        "--update-head-ok",
        transport_ext(serveur),
        f"{branche}:{branche}",
        cwd=espace,
    )
    if recu.returncode != 0:
        raise RapatriementRefuse(
            f"{branche} non rapatriée depuis le conteneur : {_message(recu)}"
        )
    apres = _texte(_git("rev-parse", "--verify", "HEAD", cwd=espace), "tête de l'espace")
    if apres != avant:
        _verifie(_git("reset", "-q", cwd=espace), "réalignement de l'index")
    return Rapatriement(branche=branche.removeprefix("refs/heads/"), avant=avant, apres=apres)


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


def _entrees(espace: Path, tete: str) -> list[tuple[str, str, str, str]]:
    """Tout ce que porte l'arbre de `tete` : (mode, nature, objet, chemin), arbres compris."""
    sortie = _git("ls-tree", "-r", "-t", "-z", "--full-tree", tete, cwd=espace)
    _verifie(sortie, "lecture de l'arbre")
    entrees = []
    for brute in sortie.stdout.split(b"\0"):
        if not brute:
            continue
        meta, _, chemin = brute.partition(b"\t")
        mode, nature, oid = meta.decode("ascii").split()
        entrees.append((mode, nature, oid, chemin.decode("utf-8", errors="surrogateescape")))
    return entrees


def _monter(depot: Path, espace: Path, paquet: bytes, *, branche: str, tete: str) -> None:
    """Pose le dépôt : objets marqués partiels, tête superficielle, branche, réglages."""
    _verifie(_git("init", "--bare", "--quiet", "--template=", str(depot)), "init")
    indexe = _git("index-pack", "--stdin", git_dir=depot, entree=paquet)
    _verifie(indexe, "index du paquet")
    nom = indexe.stdout.decode("ascii", errors="replace").split()[-1]
    (depot / "objects" / "pack" / f"pack-{nom}.promisor").write_bytes(b"")
    (depot / "shallow").write_text(f"{tete}\n", encoding="ascii")
    reglages = [
        ("core.bare", "false"),
        ("core.repositoryformatversion", "1"),
        ("extensions.partialClone", DISTANT_ABSENT),
        (f"remote.{DISTANT_ABSENT}.url", f"/maestro/{DISTANT_ABSENT}"),
        (f"remote.{DISTANT_ABSENT}.promisor", "true"),
        # Aucun ménage automatique : le dépôt vit le temps d'une session.
        ("gc.auto", "0"),
        ("maintenance.auto", "false"),
    ]
    for cle in REGLAGES_DE_RENDU:
        lue = _git("config", "--get", cle, cwd=espace)
        if lue.returncode == 0:
            reglages.append((cle, lue.stdout.decode("utf-8").strip()))
    for cle, valeur in reglages:
        _verifie(_git("config", cle, valeur, git_dir=depot), f"réglage {cle}")
    _verifie(_git("update-ref", branche, tete, git_dir=depot), "branche")
    _verifie(_git("symbolic-ref", "HEAD", branche, git_dir=depot), "HEAD")


def _indexer(depot: Path, entrees: list[tuple[str, str, str, str]], exclus: list[str]) -> None:
    """L'index de la tête, posé sans lire un blob ; les chemins exclus hors de l'arbre de travail.

    `read-tree` tenterait de rapatrier les blobs absents d'un dépôt partiel : l'index se
    pose entrée par entrée (`--index-info`). Aucune de ces commandes ne touche l'arbre de
    travail, qui est pris sur le dépôt lui-même pour n'en désigner aucun.
    """
    lignes = [
        f"{mode} {oid}\t{chemin}" for mode, nature, oid, chemin in entrees if nature != "tree"
    ]
    indexe = _git(
        "--work-tree", str(depot), "update-index", "-z", "--index-info",
        git_dir=depot, cwd=depot, entree=_lignes(lignes, b"\0"),
    )
    _verifie(indexe, "index")
    if exclus:
        marque = _git(
            "--work-tree", str(depot), "update-index", "--skip-worktree", "--", *exclus,
            git_dir=depot, cwd=depot,
        )
        _verifie(marque, "chemins exclus")


# --------------------------------------------------------------------------- #
# git
# --------------------------------------------------------------------------- #


def _git(
    *arguments: str,
    cwd: Path | None = None,
    git_dir: Path | None = None,
    entree: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Une commande git de l'hôte, sans rapatriement paresseux d'un objet absent."""
    prefixe = ["--git-dir", str(git_dir)] if git_dir is not None else []
    try:
        return subprocess.run(
            ["git", *prefixe, *arguments],
            cwd=cwd,
            input=entree,
            capture_output=True,
            env=os.environ | {"GIT_NO_LAZY_FETCH": "1"},
            timeout=_DELAI_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DepotIndisponible(f"git n'a pas pu être lancé : {exc}") from exc


def _lignes(lignes: list[str], separateur: bytes) -> bytes:
    """Des lignes jointes et terminées par `separateur`, en UTF-8."""
    return b"".join(ligne.encode("utf-8") + separateur for ligne in lignes)


def _verifie(resultat: subprocess.CompletedProcess[bytes], etape: str) -> None:
    """Lève `DepotIndisponible` si l'étape a échoué, avec ce que git en dit."""
    if resultat.returncode != 0:
        raise DepotIndisponible(f"dépôt du conteneur : {etape} en échec — {_message(resultat)}")


def _texte(resultat: subprocess.CompletedProcess[bytes], etape: str) -> str:
    """La sortie d'une étape qui doit réussir, sans son saut de ligne."""
    _verifie(resultat, etape)
    return resultat.stdout.decode("utf-8").strip()


def _message(resultat: subprocess.CompletedProcess[bytes]) -> str:
    """Ce que git en dit (ses trois dernières lignes), le code de sortie à défaut."""
    lignes = resultat.stderr.decode("utf-8", errors="replace").strip().splitlines()
    return " / ".join(ligne.strip() for ligne in lignes[-3:]) or f"code {resultat.returncode}"
