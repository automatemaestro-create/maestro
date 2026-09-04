"""L'espace de travail d'une tâche, **dérivé du projet** (ticket #224, EF-36, D2).

C'est le module qui change ce que fait le moteur. Jusqu'ici une tâche travaillait
dans un `tempfile.mkdtemp()` **vide** (`maestro.sandbox.workspace`) : l'agent ne
voyait jamais le projet de l'utilisateur. Ici il le voit.

La décision **D2** ([docs/24 §2.4](../../docs/24-projets-locaux-et-poste-de-travail.md))
fixait le patron pour les deux cas ; depuis #839 elle ne tient plus que pour le
premier, et le second est un régime à lui (`maestro.sandbox.en_place`) :

- **projet versionné** → un **worktree Git** monté hors de la racine, sur une
  branche dédiée `maestro/<tâche>` créée depuis la branche de base déclarée
  (`Projet.vcs.branche_base`). En fin de tâche le **worktree est retiré, jamais
  la branche** : c'est elle qui porte le travail jusqu'à la fusion, que
  `maestro.engine.executor` demande dès que la tâche est soldée en succès
  (#705). Pour qu'elle le porte réellement, ce qui reste non commité y est
  **commité avant le démontage** (`_solder_la_branche`) — sans quoi le `--force`
  du retrait l'emporterait et la branche survivrait vide ;
- **projet non versionné** → **la racine elle-même**, en place (#839) : rien
  n'est copié, rien n'est retiré, ce que l'agent écrit est dans le projet
  pendant qu'il l'écrit. La copie du périmètre que D2 prescrivait (option C)
  ne livrait rien — refermée avant qu'un diff puisse être approuvé, 8,80 $ pour
  zéro fichier sur le run `cc2d8e447f83` — et son filet (annuler) n'a pas
  d'objet sur un projet neuf. Ce que la copie garantissait par absence, la
  racine le garantit par **refus** (`FrontiereEcriture`) et le moteur
  **sérialise** les tâches d'un même projet (une seule à la fois dans l'arbre) ;
- **tâche sans projet** → le `mkdtemp()` d'avant, inchangé.

Trois invariants tiennent le worktree, et ce sont eux qu'il ne faut pas défaire :

1. **le chemin de travail est hors de la racine** — vérifié, pas supposé
   (`_verifie_hors_racine`), parce qu'un `TMPDIR` posé *dans* le projet suffirait
   à faire du worktree une écriture en place, dans un arbre que la branche ne
   porterait plus ;
2. **la racine est revalidée ici** (`valider_racine`, EF-38) et pas seulement à la
   déclaration : le dépôt des projets est un dossier de fichiers JSON qu'on peut
   éditer à la main (`maestro.projets.store`), donc la dernière porte avant
   qu'un agent n'écrive est celle-ci — elle vaut pour les **deux** régimes ;
3. **aucun lien symbolique n'est suivi** — c'est le vecteur d'évasion nommé par
   docs/24 §2.5 ; le régime en place le tient à l'écriture et au recensement
   (`maestro.sandbox.en_place`).

Le mécanisme est éprouvé sur ce dépôt — `scripts/git/worktree.sh` fait cela pour
les sessions Claude Code (docs/10 §9) — mais le besoin est ici plus étroit : pas
de `.env`, pas de dépendances liées, pas de ports. On s'en inspire, on ne le
réutilise pas.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from maestro.projets.application import ApplicationRefusee, commiter_en_attente
from maestro.projets.modele import Projet
from maestro.projets.racine import valider_racine
from maestro.sandbox.en_place import EspaceEnPlace
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
    est **dérivé** du projet : worktree Git **hors de la racine** s'il est
    versionné, **la racine elle-même** sinon (#839, `maestro.sandbox.en_place`).

    `keep=True` conserve l'espace (et, pour un projet versionné, le worktree
    monté) : l'inspection après coup en a besoin, et c'est alors à l'appelant de
    faire le ménage — y compris de décider ce qu'il advient du travail non
    commité, que le démontage aurait porté sur la branche. Sinon tout est démonté
    **même en cas d'exception** — le travail encore en attente est d'abord
    **commité sur la branche de la tâche** (`_solder_la_branche`, #705), puis le
    worktree est retiré par `git worktree remove`, jamais par une suppression de
    branche. En place, `keep` n'a pas d'objet : la racine n'est **jamais**
    démontée ni nettoyée, une exception y laisse ce qui a été écrit — c'est le
    critère du ticket (« rien n'est perdu à la fermeture de l'espace »), et le
    `rmtree` d'un `finally` est précisément ce qui a effacé le livrable de
    `squelette-p1` le 2026-08-28 (#568).

    Lève `RacineRefusee` si la racine du projet n'est plus admissible (EF-38) —
    dans les deux régimes, c'est la dernière porte avant qu'un agent n'écrive —
    et `EspaceProjetIndisponible` si le montage du worktree échoue (Git absent,
    worktree refusé, répertoire temporaire situé dans la racine).
    """
    if projet is None:
        with isolated_workspace(prefix=prefix, keep=keep) as ws:
            yield ws
        return

    racine = valider_racine(projet.racine)
    if not projet.versionne:
        # `derive` et non `EspaceEnPlace(path=…)` : la racine n'est pas vide, et
        # sans cette empreinte de départ tout le projet de l'utilisateur
        # ressortirait en « fichiers produits » du rapport de run.
        yield EspaceEnPlace.derive(racine, perimetre=projet.perimetre)
        return

    parent = Path(tempfile.mkdtemp(prefix=prefix))
    chemin = parent / _slug(tache_id)
    monte = False
    try:
        _verifie_hors_racine(chemin, racine)
        _monter_worktree(racine, chemin, _branche(tache_id), _base(projet))
        monte = True
        yield Workspace.derive(chemin)
    finally:
        if not keep:
            if monte:
                _solder_la_branche(chemin, _branche(tache_id))
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
    """Refuse un worktree situé **dans** la racine du projet (critère #224).

    Le cas paraît impossible — le chemin sort de `mkdtemp()` — mais il ne l'est
    pas : un `TMPDIR`/`TEMP` pointant dans le projet suffit, et le worktree
    deviendrait alors une écriture en place dans un arbre que la branche ne
    porte pas — exactement ce que D2 écarte pour un projet versionné. Le régime
    en place (#839) ne passe pas par ici : sa racine **est** son espace.
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


def _solder_la_branche(chemin: Path, branche: str) -> None:
    """Commite sur `branche` ce que le worktree porte encore, avant de le démonter (#705).

    C'est ce qui rend **vraie** la phrase que ce module tient depuis #224 — « le
    worktree est retiré, jamais la branche : c'est elle qui porte le travail
    jusqu'à la fusion ». Elle ne l'était qu'à moitié : un agent outillé écrit des
    fichiers, il ne fait pas forcément `git add`, et `git worktree remove --force`
    emportait tout ce qui n'était pas commité. La branche survivait donc en ne
    portant rien, et la fusion de #705 aurait fusionné le vide.

    Le geste **n'est pas réécrit ici** : c'est `commiter_en_attente`
    (`maestro.projets.application`), la seule orthographe de « commiter ce que le
    worktree porte encore » — hooks de l'utilisateur non contournés, identité
    posée par `-c`.

    Best-effort et silencieux, exactement comme `_retirer_worktree` et pour la
    même raison : ce geste vit dans un `finally`, et lever ici masquerait
    l'exception qui a réellement condamné la tâche. Ce qui n'a pas pu être
    commité — `pre-commit` de l'utilisateur qui refuse, index verrouillé — reste
    hors de la branche, donc hors de la fusion, et le diff vide le dira à
    l'appelant plutôt qu'une exception venue d'un démontage.

    Il a lieu quel que soit le **verdict** de la tâche : une tâche en échec ne
    fusionne rien (c'est le critère de #705), mais son travail mérite d'exister
    sur sa branche plutôt que d'être emporté par le `--force`. Consigner l'échec
    et détruire ce qui l'explique serait le pire des deux.
    """
    try:
        commiter_en_attente(chemin, branche)
    except ApplicationRefusee:
        pass


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
