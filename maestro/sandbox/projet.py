"""L'espace de travail d'une tâche, **dérivé du projet** (ticket #224, EF-36, D2).

C'est le module qui change ce que fait le moteur. Jusqu'ici une tâche travaillait
dans un `tempfile.mkdtemp()` **vide** (`maestro.sandbox.workspace`) : l'agent ne
voyait jamais le projet de l'utilisateur. Ici il le voit — sans jamais écrire
dedans.

La décision **D2** ([docs/24 §2.4](../../docs/24-projets-locaux-et-poste-de-travail.md))
fixe le patron et écarte explicitement l'écriture directe dans la racine (cinq
agents en parallèle dans un même arbre ramèneraient les collisions que le
workspace jetable évitait, sans possibilité d'annuler) :

- **projet versionné** → un **worktree Git** monté hors de la racine, sur une
  branche dédiée `maestro/<tâche>` créée depuis la branche de base déclarée
  (`Projet.vcs.branche_base`). En fin de tâche le **worktree est retiré, jamais
  la branche** : c'est elle qui porte le travail jusqu'à la fusion, geste validé
  du lot 7 (#219) ;
- **projet non versionné** → une **copie** du périmètre déclaré, exclusions du
  socle respectées (`.git`, `node_modules`, `.env`, `**/secrets/**` —
  `maestro.projets.modele.EXCLUS_DEFAUT`) ;
- **tâche sans projet** → le `mkdtemp()` d'avant, inchangé.

Trois invariants tiennent l'ensemble, et ce sont eux qu'il ne faut pas défaire :

1. **le chemin de travail est hors de la racine** — vérifié, pas supposé
   (`_verifie_hors_racine`), parce qu'un `TMPDIR` posé *dans* le projet suffirait
   à faire de la copie une écriture en place ;
2. **la racine est revalidée ici** (`valider_racine`, EF-38) et pas seulement à la
   déclaration : le dépôt des projets est un dossier de fichiers JSON qu'on peut
   éditer à la main (`maestro.projets.store`), donc la dernière porte avant
   qu'un agent n'écrive est celle-ci ;
3. **aucun lien symbolique n'est suivi** à la copie — c'est le vecteur d'évasion
   nommé par docs/24 §2.5, et un lien vers `~/.ssh` recopié dans l'espace de
   travail serait exactement la fuite qu'on prétend fermer.

Le mécanisme est éprouvé sur ce dépôt — `scripts/git/worktree.sh` fait cela pour
les sessions Claude Code (docs/10 §9) — mais le besoin est ici plus étroit : pas
de `.env`, pas de dépendances liées, pas de ports. On s'en inspire, on ne le
réutilise pas.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from maestro.projets.modele import Perimetre, Projet
from maestro.projets.racine import valider_racine
from maestro.sandbox.workspace import Workspace, isolated_workspace

#: Préfixe des branches de tâche (docs/24 §2.4) : une branche `maestro/<tâche>`
#: par tâche, jamais supprimée par le moteur — elle est le livrable tant que la
#: fusion (lot 7) n'a pas eu lieu.
PREFIXE_BRANCHE = "maestro/"

#: Délai maximum d'une commande Git. Monter un worktree est une opération locale
#: de quelques dizaines de millisecondes ; une minute est une borne d'anomalie
#: (disque réseau, dépôt colossal), pas un budget d'attente.
_DELAI_GIT_S = 60

#: Longueur maximale du fragment de nom engendré depuis l'identifiant de tâche —
#: assez pour rester lisible dans `git branch`, assez court pour ne pas crever
#: les limites de chemin de Windows une fois joint au répertoire temporaire.
_LONGUEUR_SLUG = 60

#: Repli de nom quand l'identifiant de tâche ne laisse aucun caractère sûr.
_SLUG_DEFAUT = "tache"


class EspaceProjetIndisponible(RuntimeError):
    """L'espace de travail dérivé n'a pas pu être monté — **avec son motif**.

    Même parti pris que `RacineRefusee` (EF-38) : un refus porte un code court et
    stable (`git-indisponible`, `worktree-refuse`, `espace-dans-la-racine`), pas
    seulement une phrase. L'exécuteur en fait un échec de tâche consigné
    (`maestro.engine.executor`), donc **visible** — là où un repli silencieux sur
    un répertoire vide ferait travailler l'agent dans le vide sans que personne
    ne l'apprenne.
    """

    def __init__(self, motif: str, message: str) -> None:
        super().__init__(message)
        self.motif = motif


@contextmanager
def espace_de_travail(
    projet: Projet | None = None,
    *,
    tache_id: str = "",
    prefix: str = "maestro-dev-",
    keep: bool = False,
) -> Iterator[Workspace]:
    """Ouvre l'espace de travail de la tâche et le referme en sortie (EF-36).

    `projet=None` — une tâche sans `projet_id` — rend exactement l'espace jetable
    d'avant (`isolated_workspace`) : ce chemin-là ne change pas. Sinon l'espace
    est **dérivé** du projet : worktree Git s'il est versionné, copie du périmètre
    sinon, dans les deux cas **hors de la racine**.

    `keep=True` conserve l'espace (et, pour un projet versionné, le worktree
    monté) : l'inspection après coup en a besoin, et c'est alors à l'appelant de
    faire le ménage. Sinon tout est démonté **même en cas d'exception** — le
    worktree par `git worktree remove`, jamais par une suppression de branche.

    Lève `RacineRefusee` si la racine du projet n'est plus admissible (EF-38) et
    `EspaceProjetIndisponible` si le montage échoue (Git absent, worktree refusé,
    répertoire temporaire situé dans la racine).
    """
    if projet is None:
        with isolated_workspace(prefix=prefix, keep=keep) as ws:
            yield ws
        return

    racine = valider_racine(projet.racine)
    versionne = projet.versionne
    parent = Path(tempfile.mkdtemp(prefix=prefix))
    chemin = parent / _slug(tache_id)
    monte = False
    try:
        _verifie_hors_racine(chemin, racine)
        if versionne:
            _monter_worktree(racine, chemin, _branche(tache_id), _base(projet))
            monte = True
        else:
            _copier_perimetre(racine, chemin, projet.perimetre)
        # `derive` et non `Workspace(path=…)` : l'espace n'est pas vide, et sans
        # cette empreinte de départ tout le projet de l'utilisateur ressortirait
        # en « fichiers produits » du rapport de run.
        yield Workspace.derive(chemin)
    finally:
        if not keep:
            if monte:
                _retirer_worktree(racine, chemin)
            shutil.rmtree(parent, ignore_errors=True)


def branche_de_tache(tache_id: str) -> str:
    """Le nom de la branche dédiée à la tâche `tache_id` — `maestro/<tâche>`.

    Exposé parce que la fusion (lot 7) et l'interface ont besoin de nommer cette
    branche sans rejouer l'assainissement : c'est ici qu'il vit.
    """
    return _branche(tache_id)


def _branche(tache_id: str) -> str:
    """`maestro/<tâche>`, fragment assaini (cf. `_slug`)."""
    return f"{PREFIXE_BRANCHE}{_slug(tache_id)}"


def _slug(tache_id: str) -> str:
    """L'identifiant de tâche réduit à ce qu'un nom de branche et de dossier accepte.

    Ne garde que `[A-Za-z0-9_-]` : ce jeu exclut d'un coup tout ce que Git refuse
    dans un nom de branche (espace, `~^:?*[`, `\\`, `..`, `@{`, suffixe `.lock`)
    et tout ce qui permettrait une traversée de chemin depuis un identifiant venu
    de l'extérieur — le point est écarté avec le reste, ce qui ferme les deux
    sujets d'un même filtre.
    """
    reduit = re.sub(r"[^A-Za-z0-9_-]+", "-", tache_id.strip()).strip("-_")
    reduit = re.sub(r"-{2,}", "-", reduit)[:_LONGUEUR_SLUG].strip("-_")
    return reduit or _SLUG_DEFAUT


def _base(projet: Projet) -> str:
    """La branche de base déclarée du projet, "" si le dépôt était en HEAD détaché."""
    return projet.vcs.branche_base if projet.vcs is not None else ""


def _verifie_hors_racine(chemin: Path, racine: Path) -> None:
    """Refuse un espace de travail situé **dans** la racine du projet (critère #224).

    Le cas paraît impossible — le chemin sort de `mkdtemp()` — mais il ne l'est
    pas : un `TMPDIR`/`TEMP` pointant dans le projet suffit, et l'espace « dérivé »
    deviendrait alors une écriture en place, exactement ce que D2 écarte.
    """
    try:
        chemin.resolve().relative_to(racine)
    except ValueError:
        return
    raise EspaceProjetIndisponible(
        "espace-dans-la-racine",
        f"Espace de travail refusé : {chemin} est dans la racine du projet "
        f"({racine}) — vérifiez TMPDIR/TEMP.",
    )


# --------------------------------------------------------------------------- #
# Projet versionné : un worktree Git par tâche
# --------------------------------------------------------------------------- #


def _monter_worktree(racine: Path, chemin: Path, branche: str, base: str) -> None:
    """Monte le worktree de la tâche en `chemin`, sur la branche `branche`.

    La branche est **créée** depuis `base` si elle n'existe pas, **reprise** telle
    quelle sinon : une tâche rejouée retrouve son travail plutôt que de l'écraser
    — conséquence directe de « le worktree est retiré, jamais la branche ». Un
    `worktree prune` préalable libère les enregistrements dont le répertoire a
    disparu (session tuée, disque nettoyé), sans quoi Git refuserait de réutiliser
    la branche pour cause de « already checked out ».
    """
    _git(racine, "worktree", "prune")
    if _ref_existe(racine, branche):
        arguments = ["worktree", "add", str(chemin), branche]
    else:
        arguments = ["worktree", "add", "-b", branche, str(chemin)]
        # Une branche de base disparue depuis la déclaration du projet ne doit pas
        # faire échouer la tâche : on part de HEAD en le laissant visible dans le
        # message d'erreur éventuel, plutôt que d'exiger un projet re-déclaré.
        if base and _ref_existe(racine, base):
            arguments.append(base)
    resultat = _git(racine, *arguments)
    if resultat.returncode != 0:
        raise EspaceProjetIndisponible(
            "worktree-refuse",
            f"Worktree refusé pour la branche {branche!r} dans {racine} : "
            f"{_message_git(resultat)}",
        )


def _retirer_worktree(racine: Path, chemin: Path) -> None:
    """Retire le worktree de `chemin` — **jamais la branche** (EF-36).

    Best-effort et silencieux à dessein : ce démontage vit dans un `finally`, et
    une erreur levée ici masquerait celle qui a réellement condamné la tâche. Si
    Git ne peut pas le retirer (dépôt déplacé, binaire absent), le répertoire est
    supprimé à la main et l'enregistrement est purgé au prochain montage.
    """
    try:
        resultat = _git(racine, "worktree", "remove", "--force", str(chemin))
    except EspaceProjetIndisponible:
        resultat = None
    if resultat is None or resultat.returncode != 0:
        shutil.rmtree(chemin, ignore_errors=True)


def _ref_existe(racine: Path, branche: str) -> bool:
    """La branche locale `branche` existe-t-elle dans le dépôt de `racine` ?"""
    return _git(racine, "rev-parse", "--verify", "--quiet", f"refs/heads/{branche}").returncode == 0


def _git(racine: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Lance `git <arguments>` dans `racine` et rend le processus achevé.

    Ne lève **que** pour ce qui n'est pas un verdict de Git (binaire absent, délai
    dépassé) : un code de retour non nul est une réponse, que l'appelant traduit
    en refus motivé — c'est ce qui permet à `_ref_existe` de poser une question
    sans avoir à attraper une exception.
    """
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=racine,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_DELAI_GIT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EspaceProjetIndisponible(
            "git-indisponible",
            f"Git indisponible pour le projet {racine} : {exc}",
        ) from exc


def _message_git(resultat: subprocess.CompletedProcess[str]) -> str:
    """Le message d'erreur de Git, en une ligne (stderr, à défaut stdout)."""
    brut = (resultat.stderr or resultat.stdout or "").strip()
    return " ".join(brut.split()) or f"code de retour {resultat.returncode}"


# --------------------------------------------------------------------------- #
# Projet non versionné : une copie du périmètre
# --------------------------------------------------------------------------- #


def _copier_perimetre(racine: Path, destination: Path, perimetre: Perimetre) -> None:
    """Recopie le périmètre déclaré de `racine` dans `destination` (copie + diff, D2).

    Le périmètre est appliqué ici pour la première fois : le socle (#221) le
    **déclarait**, ce lot l'**exécute**. `exclus` l'emporte sur `inclus`, comme
    l'annonce `Perimetre` — un dossier exclu n'est même pas parcouru, ce qui vaut
    autant pour la sûreté (rien sous `secrets/` n'est seulement lu) que pour le
    temps de copie (`node_modules` n'est jamais énuméré).
    """
    destination.mkdir(parents=True, exist_ok=True)
    exclus = _motifs_compiles(perimetre.exclus)
    inclus = _motifs_compiles(perimetre.inclus)
    for relatif in _fichiers_du_perimetre(racine, exclus, inclus):
        cible = destination / relatif
        cible.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(racine / relatif, cible)


def _fichiers_du_perimetre(
    racine: Path,
    exclus: tuple[re.Pattern[str], ...],
    inclus: tuple[re.Pattern[str], ...],
) -> Iterator[str]:
    """Les chemins relatifs (POSIX) des fichiers du périmètre, dans l'ordre du parcours.

    Parcours itératif (pas récursif) : un projet réel a des arborescences
    profondes et la pile de Python n'est pas le bon endroit pour en dépendre.
    **Aucun lien symbolique n'est suivi ni recopié** — ni fichier ni dossier :
    c'est le vecteur d'évasion de docs/24 §2.5, et le suivre recopierait dans
    l'espace de travail ce que la racine était censée borner.

    On descend partout où l'exclusion ne l'interdit pas, et c'est `inclus` qui
    tranche fichier par fichier : la traversée d'un dossier vide de contenu
    retenu ne coûte qu'un `scandir`, là où deviner à l'avance si un motif peut
    encore matcher plus bas demanderait un second langage de motifs.
    """
    pile = [""]
    while pile:
        relatif_dossier = pile.pop()
        base = racine / relatif_dossier if relatif_dossier else racine
        try:
            with os.scandir(base) as entrees:
                triees = sorted(entrees, key=lambda entree: entree.name)
        except OSError:  # dossier devenu illisible : sauté, jamais fatal
            continue
        for entree in triees:
            relatif = f"{relatif_dossier}/{entree.name}" if relatif_dossier else entree.name
            if entree.is_symlink() or _correspond(relatif, exclus):
                continue
            if entree.is_dir():
                pile.append(relatif)
            elif entree.is_file() and _sous_inclusion(relatif, inclus):
                yield relatif


def _correspond(relatif: str, motifs: tuple[re.Pattern[str], ...]) -> bool:
    """`relatif` est-il visé par l'un des motifs ?"""
    return any(motif.match(relatif) for motif in motifs)


def _sous_inclusion(relatif: str, inclus: tuple[re.Pattern[str], ...]) -> bool:
    """`relatif` est-il inclus, lui **ou l'un de ses dossiers parents** ?

    Inclure un dossier inclut son contenu — c'est ce qu'attend quiconque écrit
    `inclus: ["src"]`, et c'est ce qui fait tenir le défaut `["."]`.
    """
    morceaux = relatif.split("/")
    return any(
        _correspond("/".join(morceaux[: profondeur + 1]), inclus)
        for profondeur in range(len(morceaux))
    )


def _motifs_compiles(motifs: Sequence[str]) -> tuple[re.Pattern[str], ...]:
    """Compile les motifs d'un périmètre en expressions régulières ancrées."""
    return tuple(
        _compile(normalise) for motif in motifs for normalise in _normalisations(motif)
    )


def _normalisations(motif: str) -> tuple[str, ...]:
    """Les formes normalisées d'un motif de périmètre — une, parfois deux.

    Trois conventions, celles de `.gitignore` parce que c'est ce qu'un
    utilisateur écrit sans y penser :

    - `.` (et la chaîne vide) désigne **tout** — c'est `INCLUS_DEFAUT` ;
    - un motif **sans `/`** vaut à **toute profondeur** : `node_modules` attrape
      `apps/web/node_modules`, `.env` attrape `services/api/.env` ;
    - un motif finissant par `/**` vise aussi **le dossier lui-même**, sans quoi
      `**/secrets/**` exclurait le contenu de `secrets/` mais pas son nom, et le
      dossier serait parcouru pour rien.
    """
    normalise = motif.strip().replace("\\", "/")
    while normalise.startswith("./"):
        normalise = normalise[2:]
    normalise = normalise.rstrip("/")
    if normalise in ("", "."):
        return ("**",)
    if "/" not in normalise:
        normalise = f"**/{normalise}"
    if normalise.endswith("/**"):
        return (normalise, normalise[: -len("/**")])
    return (normalise,)


def _compile(normalise: str) -> re.Pattern[str]:
    """Un motif normalisé en expression régulière ancrée sur le chemin entier.

    `**` traverse les séparateurs, `*` et `?` s'arrêtent au segment — la
    distinction qui manque à `fnmatch`, dont le `*` avale les `/` et ferait
    d'`*.py` un motif récursif. Les classes `[…]` ne sont pas gérées : elles ne
    servent à rien dans un périmètre de projet, et les faire semblant de gérer
    coûterait plus cher qu'un motif de plus.
    """
    segments = normalise.split("/")
    dernier = len(segments) - 1
    morceaux = [
        (".*" if index == dernier else "(?:[^/]+/)*")
        if segment == "**"
        else _segment(segment) + ("" if index == dernier else "/")
        for index, segment in enumerate(segments)
    ]
    return re.compile("".join(morceaux) + r"\Z")


def _segment(segment: str) -> str:
    """Un segment de motif (sans `/`) en fragment d'expression régulière."""
    return "".join(
        "[^/]*" if caractere == "*" else "[^/]" if caractere == "?" else re.escape(caractere)
        for caractere in segment
    )
