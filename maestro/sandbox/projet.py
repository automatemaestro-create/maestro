"""L'espace de travail d'une tâche, **dérivé du projet** (ticket #224, EF-36, D2).

C'est le module qui change ce que fait le moteur. Jusqu'ici une tâche travaillait
dans un `tempfile.mkdtemp()` **vide** (`maestro.sandbox.workspace`) : l'agent ne
voyait jamais le projet de l'utilisateur. Ici il le voit.

La décision **D2** ([docs/24 §2.4](../../docs/24-projets-locaux-et-poste-de-travail.md))
fixait le patron pour les deux cas ; depuis #839 elle ne tient plus que pour le
premier, et le second est un régime à lui (`maestro.sandbox.en_place`) :

- **projet versionné** → un **worktree Git** monté hors de la racine, sur une
  branche dédiée `maestro/<tâche>` créée depuis la branche de base déclarée
  (`Projet.vcs.branche_base`) — ou, pour une tâche qui en reprend une autre (un
  redécoupage, #1396), depuis la branche de celle-ci. En fin de tâche le
  **worktree est retiré, jamais la branche** : c'est elle qui porte le travail
  jusqu'à la fusion, que `maestro.engine.executor` demande dès que la tâche est soldée en succès
  (#705). Pour qu'elle le porte réellement, ce qui reste non commité y est
  **commité avant le démontage** (`_solder_la_branche`) — sans quoi le `--force`
  du retrait l'emporterait et la branche survivrait vide. Ce que la tâche a
  **produit** s'y lit par Git, comme ce que la branche porte
  (`EspaceCopieDeTravail`, #1388) : le `.gitignore` du projet en retire les
  dépendances et la sortie de build ;
- **projet non versionné** → **la racine elle-même**, en place (#839) : rien
  n'est copié, rien n'est retiré, ce que l'agent écrit est dans le projet
  pendant qu'il l'écrit — avec, depuis #944, un **atelier** `.maestro/<tâche>/`
  où ranger ce qui n'est pas le livrable. La copie du périmètre que D2 prescrivait (option C)
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

import os
import re
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from maestro.fichiers import retirer_arbre
from maestro.projets.application import ApplicationRefusee, commiter_en_attente
from maestro.projets.modele import Projet
from maestro.projets.racine import valider_racine
from maestro.sandbox.en_place import EspaceEnPlace, chemin_atelier, ouvre_atelier
from maestro.sandbox.ramassage import PREFIXE_COMMUN, racine_des_espaces
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
    stable (`git-indisponible`, `worktree-refuse`, `espace-dans-la-racine`,
    `enumeration-refusee`), pas seulement une phrase. L'exécuteur en fait un échec
    de tâche consigné (`maestro.engine.executor`), donc **visible** — là où un
    repli silencieux sur un répertoire vide ferait travailler l'agent dans le
    vide sans que personne ne l'apprenne.
    """

    def __init__(self, motif: str, message: str) -> None:
        super().__init__(message)
        self.motif = motif


class EspaceCopieDeTravail(Workspace):
    """Le worktree d'une tâche, dont les fichiers sont **ceux que Git voit** (#1388).

    Le recensement d'un espace (`Workspace.fichiers`) parcourait tout le disque, et
    l'empreinte du worktree est prise **avant** que l'agent ne travaille : tout ce
    qu'un `npm install` ou un `next build` dépose ressortait donc en « fichier
    produit ». Mesuré sur le run `da0a8ae6f1b2` (projet p5, 2026-10-01) : 22 891
    fichiers de `node_modules/` et 483 de `.next/` au prompt du juge, qui dépassait
    le million de tokens et jetait une tâche réussie — pendant que la branche, elle,
    ne portait que les 31 fichiers de l'agent, parce que Git avait respecté le
    `.gitignore`.

    L'énumération est donc celle de Git : les fichiers **suivis**, et les **non
    suivis que le projet n'ignore pas** (`git ls-files --cached --others
    --exclude-standard`) — l'ensemble que `git add -A` porte sur la branche au
    démontage (`_solder_la_branche`) et que le diff présente (`maestro.projets.
    application._diff_worktree`). Le juge, le résultat de la tâche, le rapport du
    run et le rassemblement d'un redécoupage lisent ainsi ce que la branche
    livrera, et rien d'autre. Le point unique d'énumération de `Workspace` tient :
    `derive` et `produced_files` passent tous deux par ici.

    Le `.gitignore` est celui du **projet** — c'est lui, et seulement lui, qui dit
    ce qui n'est pas un livrable ; aucun catalogue de dossiers n'est écrit ici. Un
    projet qui n'ignore rien verra donc ses dépendances recensées, exactement comme
    il les verrait commitées. Un lien symbolique n'est pas lu (invariant 3 du
    module) : son contenu ne vit pas dans l'espace.

    Si Git ne répond pas, c'est une **erreur motivée** (`EspaceProjetIndisponible`),
    jamais un repli sur tout le disque : c'est ce repli qui faisait le défaut.
    """

    def fichiers(self) -> Iterator[Path]:
        """Les fichiers que Git voit dans le worktree, triés par chemin."""
        resultat = _git(
            self.path, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
        )
        if resultat.returncode != 0:
            raise EspaceProjetIndisponible(
                "enumeration-refusee",
                f"Git n'a pas pu énumérer les fichiers de l'espace {self.path} : "
                f"{_message_git(resultat)}",
            )
        # Un ensemble : `--cached` rend une entrée par étage d'un fichier en conflit.
        relatifs = {champ for champ in resultat.stdout.split("\0") if champ}
        for fichier in sorted(self.path / relatif for relatif in relatifs):
            # Un fichier suivi que l'agent a supprimé reste dans l'index : il n'est
            # plus un fichier de l'espace, et ne s'est jamais recensé comme tel.
            if fichier.is_file() and not fichier.is_symlink():
                yield fichier


@contextmanager
def espace_de_travail(
    projet: Projet | None = None,
    *,
    tache_id: str = "",
    reprend: str = "",
    prefix: str = "maestro-dev-",
    keep: bool = False,
) -> Iterator[Workspace]:
    """Ouvre l'espace de travail de la tâche et le referme en sortie (EF-36).

    `projet=None` — une tâche sans `projet_id` — rend exactement l'espace jetable
    d'avant (`isolated_workspace`) : ce chemin-là ne change pas. Sinon l'espace
    est **dérivé** du projet : worktree Git **hors de la racine** s'il est
    versionné, **la racine elle-même** sinon (#839, `maestro.sandbox.en_place`).

    `reprend` (#1396) nomme la tâche dont celle-ci reprend le travail
    (`Task.reprend`, posé aux tâches de tête d'un redécoupage) : sur un projet
    versionné, une branche **créée** ici part de celle de cette tâche, si elle
    existe, plutôt que de la base — ce qui est fait n'est pas refait. Il ne joue
    qu'à la création : une branche qui existe déjà se reprend telle quelle. Sans
    objet en place et sans projet.

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
        # L'atelier de la tâche (#944) est ouvert **avant** la dérivation : c'est
        # de lui que dépend ce que `fichiers` énumère, donc l'empreinte de départ.
        atelier = chemin_atelier(_slug(tache_id))
        ouvre_atelier(racine, atelier)
        # `derive` et non `EspaceEnPlace(path=…)` : la racine n'est pas vide, et
        # sans cette empreinte de départ tout le projet de l'utilisateur
        # ressortirait en « fichiers produits » du rapport de run.
        yield EspaceEnPlace.derive(racine, perimetre=projet.perimetre, atelier=atelier)
        return

    # `racine_des_espaces` et non le défaut de `tempfile` (#992, S13) : la même
    # racine que l'espace jetable, donc celle que le Bash de l'agent appelle
    # `/tmp` sous Windows. Le parent n'est **pas** marqué d'un pid, et c'est
    # voulu : le ramassage ne doit jamais emporter un worktree, dont le travail
    # non commité ne vit nulle part ailleurs (`ramassage.porte_un_worktree`).
    parent = Path(tempfile.mkdtemp(prefix=prefix, dir=racine_des_espaces()))
    chemin = parent / _slug(tache_id)
    monte = False
    try:
        _verifie_hors_racine(chemin, racine)
        _monter_worktree(
            racine,
            chemin,
            _branche(tache_id),
            _base(projet),
            depart=_branche(reprend) if reprend.strip() else "",
        )
        monte = True
        yield EspaceCopieDeTravail.derive(chemin)
    finally:
        if not keep:
            if monte:
                _solder_la_branche(chemin, _branche(tache_id))
                _retirer_worktree(racine, chemin)
            retirer_arbre(parent)


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


def _monter_worktree(
    racine: Path, chemin: Path, branche: str, base: str, *, depart: str = ""
) -> None:
    """Monte le worktree de la tâche en `chemin`, sur la branche `branche`.

    La branche est **créée** depuis `base` si elle n'existe pas, **reprise** telle
    quelle sinon : une tâche rejouée retrouve son travail plutôt que de l'écraser
    — conséquence directe de « le worktree est retiré, jamais la branche ». Un
    `worktree prune` préalable libère les enregistrements dont le répertoire a
    disparu (session tuée, disque nettoyé), sans quoi Git refuserait de réutiliser
    la branche pour cause de « already checked out ».

    `depart` (#1396) est la branche de la tâche dont celle-ci reprend le travail :
    quand elle existe, la branche créée part d'elle et non de `base`. Une tâche
    jamais montée n'a pas de branche, et l'on part alors de la base, comme avant.

    Le `prune` ne libère pas tout : un worktree dont le process a été **tué** — la
    tâche en vol quand Maestro s'est éteint — garde son répertoire, donc son
    enregistrement, donc la branche. Un run repris sur son plan (#1391) rejoue cette
    tâche sous le **même** identifiant, et Git refusait de la remonter
    (`_liberer_la_branche`).
    """
    _git(racine, "worktree", "prune")
    _liberer_la_branche(racine, branche, chemin)
    if _ref_existe(racine, branche):
        arguments = ["worktree", "add", str(chemin), branche]
    else:
        arguments = ["worktree", "add", "-b", branche, str(chemin)]
        if depart and _ref_existe(racine, depart):
            arguments.append(depart)
        # Une branche de base disparue depuis la déclaration du projet ne doit pas
        # faire échouer la tâche : on part de HEAD en le laissant visible dans le
        # message d'erreur éventuel, plutôt que d'exiger un projet re-déclaré.
        elif base and _ref_existe(racine, base):
            arguments.append(base)
    resultat = _git(racine, *arguments)
    if resultat.returncode != 0:
        raise EspaceProjetIndisponible(
            "worktree-refuse",
            f"Worktree refusé pour la branche {branche!r} dans {racine} : "
            f"{_message_git(resultat)}",
        )


def _liberer_la_branche(racine: Path, branche: str, chemin: Path) -> None:
    """Rend `branche` qu'un worktree **abandonné** retient — son travail sauvé d'abord (#1391).

    Constaté sur le banc (S12, 2026-10-05) : Maestro éteint pendant qu'une tâche
    écrivait, le process de son run est tué avant que le `finally` d'
    `espace_de_travail` ne démonte son worktree. Le répertoire reste, l'enregistrement
    aussi, et à la reprise du run sur son plan la même tâche — même identifiant, même
    branche — se voit refuser son worktree : « already used by worktree ». Le run
    repris tombait en échec sur la tâche même qu'il devait refaire.

    Deux gestes, dans cet ordre, et le premier est la raison du second :

    1. ce que le worktree porte encore est **commité sur la branche**
       (`_solder_la_branche`, le geste même que le démontage aurait fait) : le
       retirer d'abord emporterait le travail de la tâche, qui ne vit nulle part
       ailleurs ;
    2. le worktree est **retiré**, jamais la branche (`_retirer_worktree`) — elle se
       remonte ensuite, avec ce travail dessus.

    Ne sont libérés que les worktrees qui ont **la forme exacte** de ceux
    qu'`espace_de_travail` monte — `<racine des espaces>/maestro-…/<tâche>` : un
    worktree que la personne aurait ouvert elle-même sur une branche `maestro/…`,
    fût-ce dans son répertoire temporaire, n'est jamais touché, et le refus de Git
    s'affiche alors comme avant. Un worktree de cette forme est celui d'une
    exécution **antérieure** de cette tâche : une tâche n'est jamais exécutée deux
    fois en même temps, et sa tentative précédente a démonté le sien en sortant —
    sauf à être morte sans le pouvoir.
    """
    resultat = _git(racine, "worktree", "list", "--porcelain")
    if resultat.returncode != 0:
        return
    espaces = _normalise(racine_des_espaces())
    cible = _normalise(chemin)
    tache = branche.removeprefix(PREFIXE_BRANCHE)
    for dossier, reference in _worktrees(resultat.stdout):
        abandonne = Path(dossier)
        if (
            reference != f"refs/heads/{branche}"
            or _normalise(abandonne) == cible
            or abandonne.name != tache
            or not abandonne.parent.name.startswith(PREFIXE_COMMUN)
            or _normalise(abandonne.parent.parent) != espaces
        ):
            continue
        _solder_la_branche(abandonne, branche)
        _retirer_worktree(racine, abandonne)
        _git(racine, "worktree", "prune")


def _worktrees(porcelaine: str) -> Iterator[tuple[str, str]]:
    """`(chemin, référence)` de chaque worktree d'un `git worktree list --porcelain`.

    Un worktree en HEAD détaché n'a pas de ligne `branch` : il rend une référence
    vide, qui ne désigne aucune branche.
    """
    for bloc in porcelaine.split("\n\n"):
        champs = dict(
            ligne.split(" ", 1) for ligne in bloc.splitlines() if " " in ligne
        )
        if "worktree" in champs:
            yield champs["worktree"], champs.get("branch", "")


def _normalise(chemin: Path) -> str:
    """Un chemin comparable à un autre : absolu, résolu, casse du système de fichiers."""
    try:
        resolu = chemin.resolve()
    except OSError:  # pragma: no cover - chemin non représentable
        resolu = chemin
    return os.path.normcase(str(resolu))


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
    supprimé à la main — par `retirer_arbre` (#992), parce qu'un worktree est
    plein d'objets Git en lecture seule et qu'un `rmtree` nu en laisserait la
    coquille — et l'enregistrement est purgé au prochain montage.
    """
    try:
        resultat = _git(racine, "worktree", "remove", "--force", str(chemin))
    except EspaceProjetIndisponible:
        resultat = None
    if resultat is None or resultat.returncode != 0:
        retirer_arbre(chemin)


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
