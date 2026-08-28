"""Faire d'une racine non versionnée un dépôt Git — **sur demande** (ticket #704).

Le prérequis du parent #703, et lui seul. Toute la mécanique visée par ce
chantier — fusionner la branche d'une tâche dans la branche de base dès que la
tâche est soldée — suppose un projet **versionné** : c'est la branche qui retient
le travail entre l'atelier et la racine. Un projet déclaré non versionné
(`Projet.vcs is None`) ne prend pas cette voie mais la **copie jetable** du
périmètre (`maestro.sandbox.projet`, D2), sans rien pour retenir le travail — le
livrable de `squelette-p1` a ainsi été emporté par le `rmtree` de fin de tâche le
2026-08-28 (#568).

Ce module donne à un tel projet le moyen de **le devenir**. Il ne change pas la
règle, il ouvre une porte à côté d'elle :

- `detecter_vcs` **constate Git sans jamais l'imposer** (EF-38) et ne bouge pas
  d'un caractère. Rien de ce qui parcourt un projet — déclaration
  (`ProjetStore.creer`), relecture, dérivation de l'espace de travail (#224),
  application du travail (#227) — n'initialise quoi que ce soit ;
- ce module est le **seul** endroit du dépôt qui écrive un `.git` dans le dossier
  de quelqu'un, et il n'est appelé que par un verbe qu'on invoque exprès
  (`initialiser_depot`, `ProjetStore.versionner`). *Le geste est explicite ou il
  n'a pas lieu.*

**Ce n'est pas un `git init` nu, et c'en est tout l'intérêt.** Un dépôt qui vient
de naître a un `HEAD` **non né** : `.git/HEAD` pointe bien une branche — donc
`detecter_vcs` rend un `Vcs` avec sa `branche_base` — mais `refs/heads/<base>`
ne résout pas. Dans cet état, rien de ce que le parent vise ne fonctionne :
`maestro.sandbox.projet` ne peut pas brancher le worktree d'une tâche sur la base
(Git bascule en `--orphan` et l'agent hérite d'un espace **vide**, sans le projet
qu'il est censé voir), et `maestro.projets.application` refuse le diff en
`base-introuvable`. L'objectif du ticket est que le `Vcs` soit renseigné « comme
s'il l'avait toujours été » : la racine est donc **enregistrée dans un premier
commit**, ce qui donne à la branche de base une vraie référence et fait du projet
tel qu'il est aujourd'hui la ligne de départ des tâches à venir.

Trois partis pris à connaître avant d'y toucher :

1. **on n'initialise jamais par-dessus l'existant.** Une racine déjà versionnée
   est rendue telle quelle, `detecter_vcs` faisant foi — y compris quand le
   projet déclaré la croit non versionnée (quelqu'un a pu lancer `git init` à la
   main depuis) : c'est alors un simple rattrapage de la déclaration, et aucune
   commande n'est lancée. Et une racine **contenue dans un autre dépôt** est
   refusée (`depot-englobant`) : y poser un dépôt imbriqué modifierait le dépôt
   du dessus, qui verrait apparaître un lien de sous-module là où il avait des
   fichiers — précisément l'état d'avant qu'on s'engage à ne pas toucher ;
2. **le premier commit prend tout ce que la racine porte** (`git add -A`, donc
   ce que le `.gitignore` du projet laisse passer, et rien de plus). Ni le
   périmètre du projet ni un `.gitignore` écrit pour l'occasion ne s'y
   substituent : le périmètre dit ce qu'un **agent** voit, pas ce que
   l'utilisateur versionne, et laisser des fichiers hors de l'index rendrait
   `git status` sale pour toujours — or `maestro.projets.application` refuse de
   fusionner dans une racine qui n'est pas propre (`racine-occupee`). Un projet
   vide donne un commit vide (`--allow-empty`) : la branche de base doit exister
   même quand il n'y a encore rien à enregistrer ;
3. **un échec laisse le projet exactement dans l'état d'avant.** Si le commit
   échoue après l'initialisation, le `.git` qui vient de naître est retiré et le
   refus remonte motivé. Retiré **seulement s'il n'existait pas avant l'appel** :
   un `.git` résiduel qu'on aurait trouvé sur place (clone avorté) appartient à
   l'utilisateur, pas à nous.

Les hooks du dépôt ne sont pas contournés, même règle et même raison que
`maestro.projets.application` : ce sont ceux de l'utilisateur, et un `pre-commit`
qui refuse a exactement le droit de le faire — le refus remonte motivé plutôt que
de passer en force.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from maestro.projets.modele import AUTEUR_COURRIEL, AUTEUR_NOM, Vcs
from maestro.projets.racine import canonique, detecter_vcs, valider_racine

#: Délai maximum des commandes Git **de plomberie** (`init`, `rev-parse`) — même
#: borne d'anomalie que l'espace de travail dérivé (#224) et l'application du
#: travail (#227) : ce sont des opérations locales de quelques dizaines de
#: millisecondes, une minute dit « quelque chose ne va pas » plutôt qu'« attends ».
_DELAI_GIT_S = 60

#: Délai maximum du premier commit. C'est la **seule** commande du module dont le
#: coût est proportionnel au projet et non constant : elle indexe puis enregistre
#: tout ce que la racine porte, `node_modules` compris quand rien ne l'ignore. Une
#: minute y serait une panne fabriquée sur un projet parfaitement sain.
_DELAI_COMMIT_S = 300

#: Le message du premier commit. Fixe et sans interpolation : c'est le repère
#: qu'on relit dans `git log` pour savoir d'où vient l'historique d'un projet mis
#: sous Git par Maestro.
MESSAGE_PREMIER_COMMIT = "Maestro : état initial du projet"


class VersionnementRefuse(RuntimeError):
    """La mise sous Git n'a pas eu lieu — **avec son motif** (#704).

    Même parti pris que `RacineRefusee` (#221), `EspaceProjetIndisponible` (#224)
    et `ApplicationRefusee` (#227) : le refus porte un code court et stable que
    l'API et l'UI peuvent afficher ou traduire (`depot-englobant`, `init-refuse`,
    `commit-refuse`, `vcs-introuvable`, `git-indisponible`), le message restant la
    phrase lisible.

    Un refus est **total** : quand il est levé, la racine est dans l'état où elle
    était avant l'appel.
    """

    def __init__(self, motif: str, message: str) -> None:
        super().__init__(message)
        self.motif = motif


def initialiser_depot(racine: Path | str, *, branche: str = "") -> Vcs:
    """Fait de `racine` un dépôt Git s'il n'en est pas un, et rend son `Vcs`.

    La racine est **revalidée** (`valider_racine`, EF-38) et non reprise telle
    quelle : le dépôt des projets est un dossier de fichiers JSON qu'on peut
    éditer à la main, et c'est ici la dernière porte avant d'écrire dans le
    dossier de quelqu'un.

    Rend le `Vcs` **constaté** par `detecter_vcs` — jamais un `Vcs` fabriqué : la
    branche de base rendue est celle que Git a réellement posée. Une racine déjà
    versionnée est rendue telle quelle, sans qu'aucune commande soit lancée.

    `branche` impose le nom de la branche initiale (`git init --initial-branch`) ;
    vide — le cas nominal — laisse Git répondre selon la configuration de
    l'utilisateur (`init.defaultBranch`), qu'on n'a pas à trancher à sa place.

    Lève `RacineRefusee` si la racine n'est plus admissible et
    `VersionnementRefuse` motivée si l'initialisation échoue, la racine restant
    alors dans l'état où elle était.
    """
    chemin = valider_racine(racine)
    deja = detecter_vcs(chemin)
    if deja is not None:
        return deja

    _exige_hors_depot(chemin)
    # Mesuré **avant** la première commande : c'est ce qui distingue le `.git`
    # que nous créons — donc que nous pouvons retirer — de celui qu'on aurait
    # trouvé sur place.
    git_absent_avant = not (chemin / ".git").exists()
    try:
        _initialiser(chemin, branche)
        _premier_commit(chemin)
        vcs = detecter_vcs(chemin)
        if vcs is None:  # pragma: no cover - Git a rendu 0 sans poser de dépôt
            raise VersionnementRefuse(
                "vcs-introuvable",
                f"Dépôt introuvable après initialisation de {chemin} — rien n'a "
                "été enregistré dans la déclaration du projet.",
            )
        return vcs
    except VersionnementRefuse:
        if git_absent_avant:
            shutil.rmtree(chemin / ".git", ignore_errors=True)
        raise


def _exige_hors_depot(chemin: Path) -> None:
    """Refuse une racine **contenue dans un autre dépôt Git** (`depot-englobant`).

    `detecter_vcs` ne regarde que `<racine>/.git`, donc un sous-dossier de dépôt
    lui répond « non versionné » en toute rigueur. Y initialiser un dépôt
    imbriqué serait pourtant une modification du dépôt **du dessus** — qui verrait
    ses fichiers remplacés par un lien de sous-module —, et l'utilisateur n'a
    demandé à toucher qu'à son projet.
    """
    resultat = _git(chemin, "rev-parse", "--show-toplevel")
    if resultat.returncode != 0:
        return  # hors de tout dépôt : le cas nominal
    brut = resultat.stdout.strip()
    if not brut:
        return
    try:
        englobant = canonique(brut)
    except OSError:  # pragma: no cover - chemin illisible pour l'OS
        return
    if englobant == chemin:
        return
    raise VersionnementRefuse(
        "depot-englobant",
        f"{chemin} est déjà dans le dépôt Git {englobant} — un dépôt imbriqué "
        "modifierait celui-ci. Déclarez le projet sur la racine du dépôt, ou "
        "sortez le dossier de son arborescence.",
    )


def _initialiser(chemin: Path, branche: str) -> None:
    """`git init` dans `chemin`, la branche initiale imposée seulement si on la demande."""
    arguments = ["init"]
    if branche:
        arguments += ["--initial-branch", branche]
    resultat = _git(chemin, *arguments)
    if resultat.returncode != 0:
        raise VersionnementRefuse(
            "init-refuse",
            f"Initialisation Git refusée dans {chemin} : {_message_git(resultat)}",
        )


def _premier_commit(chemin: Path) -> None:
    """Enregistre la racine telle qu'elle est, pour que la branche de base existe.

    `--allow-empty` parce qu'un projet encore vide doit lui aussi avoir une
    branche de base résoluble : sans commit, `HEAD` désigne une branche qui
    n'existe pas, et tout ce qui s'appuie dessus (worktree de tâche, diff,
    fusion) échoue en désignant la mauvaise cause.
    """
    if _git(chemin, "add", "-A", delai=_DELAI_COMMIT_S).returncode != 0:
        raise VersionnementRefuse(
            "commit-refuse", f"Git n'a pas pu indexer le contenu de {chemin}."
        )
    resultat = _git(
        chemin,
        "-c",
        f"user.name={AUTEUR_NOM}",
        "-c",
        f"user.email={AUTEUR_COURRIEL}",
        "commit",
        "--allow-empty",
        "-m",
        MESSAGE_PREMIER_COMMIT,
        delai=_DELAI_COMMIT_S,
    )
    if resultat.returncode != 0:
        raise VersionnementRefuse(
            "commit-refuse",
            f"Premier commit refusé dans {chemin} : {_message_git(resultat)}",
        )


def _git(
    cwd: Path, *arguments: str, delai: int = _DELAI_GIT_S
) -> subprocess.CompletedProcess[str]:
    """Lance `git <arguments>` dans `cwd` et rend le processus achevé.

    Ne lève **que** pour ce qui n'est pas un verdict de Git (binaire absent,
    délai dépassé) : un code de retour non nul est une réponse, que l'appelant
    traduit en refus motivé — c'est ce qui permet à `_exige_hors_depot` de poser
    une question sans attraper d'exception.
    """
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=delai,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VersionnementRefuse(
            "git-indisponible", f"Git indisponible pour {cwd} : {exc}"
        ) from exc


def _message_git(resultat: subprocess.CompletedProcess[str]) -> str:
    """Le message d'erreur de Git, en une ligne (stderr, à défaut stdout)."""
    brut = (resultat.stderr or resultat.stdout or "").strip()
    return " ".join(brut.split()) or f"code de retour {resultat.returncode}"
