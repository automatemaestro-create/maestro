"""Validation de la racine d'un projet et détection du VCS (ticket #221, EF-38).

Le seul endroit du code qui confronte un projet au disque **avant** qu'un agent
n'y touche. Deux responsabilités, toutes deux réclamées par
[docs/24 §2.5](../../docs/24-projets-locaux-et-poste-de-travail.md) (« évasion par
la racine déclarée ») :

1. **Valider une racine** (`valider_racine`) — le chemin est **canonicalisé**
   (`resolve()` : les `..` sont écrasés et les liens symboliques suivis, donc un
   lien vers `~/.ssh` est vu comme `~/.ssh`), puis confronté à une **liste de
   racines interdites** : racine de disque, dossier utilisateur nu et ses
   ancêtres, `.ssh`/`AppData` et leurs cousins, dossiers système, et le dépôt
   Maestro lui-même. Un refus **porte son motif** (`RacineRefusee.motif`), jamais
   un `False` muet : l'écran Projets doit pouvoir dire *pourquoi*
   ([docs/05 §2.7](../../docs/05-interface-control-tower.md)).
   La part de ces refus qui vaut pour **tout** chemin — et pas seulement pour
   une racine de projet — est isolée dans `verifier_zone_interdite` : c'est
   elle que réutilise l'explorateur de dossiers de l'API (#223), pour qu'une
   zone interdite à la déclaration ne devienne pas lisible par ailleurs.
2. **Empêcher d'écrire au-dessus** (`chemin_dans_racine`) — la seconde moitié
   d'EF-38. Tout chemin dérivé d'une racine passe par là : il est résolu puis
   vérifié **sous** la racine canonicalisée, ce qui neutralise aussi bien un
   `../../etc/passwd` qu'un lien symbolique posé dans le projet.

Et une commodité qui relève du même dossier : `detecter_vcs`, qui répond à « ce
dossier est-il un dépôt Git ? » **sans rien imposer** — un projet non versionné
reste déclarable, il n'aura simplement pas de worktree par tâche mais une copie
(#224, D2). La détection lit les fichiers de Git (`.git/HEAD`, `.git/config`)
plutôt que d'appeler le binaire : pas de sous-process, pas de dépendance à un
`git` installé, et une détection testable sur un faux dépôt de trois fichiers.

Rien ici n'est spécifique à un OS : les interdits Windows sont inertes sous
POSIX et réciproquement, ce qui évite une liste par plateforme.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from maestro.projets.modele import Vcs

#: Une ancre de chemin admissible : un disque (`C:\\`) sous Windows, la racine
#: sous POSIX. Tout le reste — partage réseau (`\\\\serveur\\partage`, `//srv`),
#: device (`\\\\.\\C:`) — est refusé : ces ancres **ne se comparent pas** à celles
#: des racines interdites, donc les laisser passer reviendrait à désactiver la
#: liste (un `\\\\localhost\\C$\\Users\\moi\\.ssh` désigne bien `~/.ssh`).
_ANCRE_ADMISE = re.compile(r"^[A-Za-z]:[\\/]$" if os.name == "nt" else r"^/$")

#: Les dossiers du profil utilisateur qu'un projet ne peut pas prendre pour
#: racine : clés SSH/GPG, identifiants cloud, et les grands dossiers d'état
#: applicatif (`AppData` sous Windows, `Library` sous macOS) — déclarer l'un
#: d'eux ouvrirait à un agent l'intégralité des secrets du poste (docs/24 §2.5).
_SOUS_UTILISATEUR_INTERDITS: tuple[str, ...] = (
    ".ssh",
    ".gnupg",
    ".aws",
    ".config",
    "AppData",
    "Library",
)

#: Les dossiers système : jamais un projet, toujours une catastrophe. La liste
#: mêle Windows et POSIX à dessein — un chemin qui n'existe pas sur l'OS courant
#: ne coûte qu'une comparaison.
_SYSTEME_INTERDITS: tuple[str, ...] = (
    "C:/Windows",
    "C:/Program Files",
    "C:/Program Files (x86)",
    "C:/ProgramData",
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/dev",
    "/proc",
    "/sys",
    "/var",
    "/System",
    "/Library",
)


class RacineRefusee(ValueError):
    """Une racine (ou un chemin dérivé) refusée, **avec son motif** (EF-38).

    `motif` est un code stable et court (`racine-de-disque`, `chemin-sensible`,
    `hors-racine`…) : c'est lui que l'API et l'UI affichent ou traduisent, le
    message restant la phrase lisible. Hérite de `ValueError` pour rester
    attrapable comme les refus des autres dépôts (`maestro.agents.store`).
    """

    def __init__(self, motif: str, message: str, chemin: Path | str = "") -> None:
        super().__init__(message)
        self.motif = motif
        self.chemin = str(chemin)


def valider_racine(chemin: Path | str, *, creer: bool = False) -> Path:
    """La racine canonicalisée de `chemin`, ou `RacineRefusee` motivée (EF-38).

    Exige un chemin **absolu** : un chemin relatif serait résolu contre le
    répertoire courant du process — c'est-à-dire ailleurs selon qui appelle, ce
    qu'une frontière de sécurité ne peut pas se permettre. Le retour est le
    chemin **résolu** : c'est lui qu'il faut stocker, jamais la saisie.

    `creer=True` crée le dossier s'il manque (le besoin « dossier neuf » de
    docs/24 §2.1) — après les refus, jamais avant : on ne crée pas un dossier
    pour ensuite le refuser.
    """
    brut = str(chemin).strip()
    if not brut:
        raise RacineRefusee("chemin-vide", "Racine vide : indiquez un dossier de projet.")
    candidat = Path(_sans_prefixe_windows(brut)).expanduser()
    if not candidat.is_absolute():
        raise RacineRefusee(
            "chemin-relatif",
            f"Racine relative refusée : {brut!r} — indiquez un chemin absolu.",
            brut,
        )
    resolu = candidat.resolve()
    if not _ANCRE_ADMISE.match(resolu.anchor):
        raise RacineRefusee(
            "ancre-non-standard",
            f"Racine refusée : {resolu} n'est pas sur un disque local "
            "(partage réseau ou chemin de périphérique).",
            resolu,
        )

    if resolu == Path(resolu.anchor):
        raise RacineRefusee(
            "racine-de-disque",
            f"Racine de disque refusée : {resolu} — choisissez un dossier de projet.",
            resolu,
        )

    utilisateur = _dossier_utilisateur()
    if utilisateur is not None:
        if resolu == utilisateur:
            raise RacineRefusee(
                "dossier-utilisateur-nu",
                f"Dossier utilisateur refusé tel quel : {resolu} — choisissez un "
                "sous-dossier de projet.",
                resolu,
            )
        if _contient(resolu, utilisateur):
            raise RacineRefusee(
                "au-dessus-du-dossier-utilisateur",
                f"Racine trop haute : {resolu} contient le dossier utilisateur "
                f"{utilisateur}.",
                resolu,
            )

    verifier_zone_interdite(resolu)

    depot = _depot_maestro()
    if _contient(resolu, depot):
        raise RacineRefusee(
            "au-dessus-du-depot-maestro",
            f"Racine trop haute : {resolu} contient le dépôt de Maestro ({depot}).",
            resolu,
        )

    if resolu.exists() and not resolu.is_dir():
        raise RacineRefusee(
            "pas-un-dossier",
            f"Racine invalide : {resolu} n'est pas un dossier.",
            resolu,
        )
    if not resolu.exists():
        if not creer:
            raise RacineRefusee(
                "dossier-absent",
                f"Racine introuvable : {resolu} n'existe pas.",
                resolu,
            )
        resolu.mkdir(parents=True, exist_ok=True)
    return resolu


def verifier_zone_interdite(chemin: Path) -> None:
    """Refuse un chemin **déjà résolu** posé dans une zone sensible du poste (EF-38).

    Les interdits qui valent pour **tout** chemin, qu'on le déclare comme racine
    de projet (`valider_racine`) ou qu'on l'énumère depuis l'explorateur de
    dossiers (`maestro.controltower.projets`, #223) : les secrets du profil
    utilisateur (`.ssh`, `.aws`, `AppData`…), les dossiers système, et le dépôt
    de Maestro lui-même.

    Ce qui **reste** propre à une racine de projet vit dans `valider_racine` et
    n'a rien à faire ici : on **traverse** légitimement son dossier utilisateur
    ou le dossier qui contient le dépôt de Maestro pour atteindre un projet, on
    ne peut simplement pas les déclarer tels quels. D'où la coupure : cette
    fonction dit « ce chemin est interdit », `valider_racine` ajoute « et cette
    racine-là n'est pas déclarable ».

    Attend un chemin **résolu** (`canonique`) : elle compare, elle ne
    canonicalise pas — un appelant qui lui passerait une saisie brute
    contournerait la comparaison avec un `..`.
    """
    utilisateur = _dossier_utilisateur()
    if utilisateur is not None:
        for nom in _SOUS_UTILISATEUR_INTERDITS:
            if _est_sous(chemin, utilisateur / nom):
                raise RacineRefusee(
                    "chemin-sensible",
                    f"Chemin sensible refusé : {chemin} est dans {utilisateur / nom}.",
                    chemin,
                )

    for interdit in _systeme_interdits():
        if _est_sous(chemin, Path(interdit)):
            raise RacineRefusee(
                "chemin-systeme",
                f"Dossier système refusé : {chemin} est dans {interdit}.",
                chemin,
            )

    depot = _depot_maestro()
    if _est_sous(chemin, depot):
        raise RacineRefusee(
            "depot-maestro",
            f"Le dépôt de Maestro ne se prend pas lui-même pour projet : {chemin}.",
            chemin,
        )


def chemin_dans_racine(racine: Path | str, chemin: Path | str) -> Path:
    """Le chemin `chemin` résolu **sous** `racine`, ou `RacineRefusee` (EF-38).

    Accepte un chemin relatif (joint à la racine) comme absolu (vérifié tel
    quel), et refuse tout ce qui sort de la racine — `..`, chemin d'un autre
    disque, ou lien symbolique pointant dehors, la résolution ayant lieu **avant**
    la comparaison. C'est la brique que consommeront l'espace de travail dérivé
    (#224) et l'application des livrables (#227) : aucune écriture ne se calcule
    autrement.

    ⚠ Le verdict porte sur l'état du disque **au moment de l'appel** : un lien
    posé juste après redirigerait le chemin rendu (TOCTOU). Refermer cette
    fenêtre revient à qui **ouvre** le fichier (sans suivre les liens), pas à
    qui calcule le chemin.
    """
    base = canonique(racine)
    candidat = Path(_sans_prefixe_windows(str(chemin).strip())).expanduser()
    cible = (candidat if candidat.is_absolute() else base / candidat).resolve()
    if not _est_sous(cible, base):
        raise RacineRefusee(
            "hors-racine",
            f"Chemin hors du projet : {cible} sort de la racine {base}.",
            cible,
        )
    return cible


def detecter_vcs(racine: Path | str) -> Vcs | None:
    """Le VCS du dossier `racine` — `Vcs(type='git', …)` ou `None` s'il n'est pas versionné.

    La détection est **constatée, jamais imposée** : un dossier sans `.git` rend
    `None` et reste un projet parfaitement déclarable (il travaillera par copie
    au lieu d'un worktree, #224). Un `.git` **fichier** (le cas d'un worktree
    Git) est suivi jusqu'à son vrai dossier. Branche et distant sont lus au
    mieux : un dépôt fraîchement `git init`, sans commit ni remote, rend bien un
    `Vcs` — c'est un dépôt Git, avec `distant=None`.
    """
    marqueur = Path(racine).expanduser() / ".git"
    dossier = _dossier_git(marqueur)
    if dossier is None:
        return None
    return Vcs(
        type="git",
        branche_base=_branche_courante(dossier),
        distant=_url_origin(_config_git(dossier)),
    )


def canonique(chemin: Path | str) -> Path:
    """La forme canonique d'un chemin — celle sur laquelle deux racines se comparent.

    Retire le préfixe Windows d'extension de longueur (`\\\\?\\`) **avant** de
    résoudre : sans ça, le même dossier a deux orthographes qui ne se comparent
    pas, et l'une des deux échappe à toutes les vérifications de
    `valider_racine`. C'est aussi ce qui garantit qu'un projet n'est pas déclaré
    deux fois sous deux écritures du même chemin.
    """
    return Path(_sans_prefixe_windows(str(chemin).strip())).expanduser().resolve()


def _sans_prefixe_windows(brut: str) -> str:
    """`brut` sans son préfixe `\\\\?\\` (ou `\\\\?\\UNC\\`), inchangé sinon."""
    normalise = brut.replace("/", "\\")
    if normalise.upper().startswith("\\\\?\\UNC\\"):
        return "\\\\" + brut[8:]
    if normalise.startswith("\\\\?\\"):
        return brut[4:]
    return brut


def _systeme_interdits() -> tuple[str, ...]:
    """Les dossiers système, ceux de l'environnement Windows compris.

    `%SystemRoot%`/`%ProgramFiles%` sont lus à chaud : un Windows installé sur
    `D:` n'est pas couvert par la liste en dur, qui suppose `C:`.
    """
    depuis_env = tuple(
        valeur
        for cle in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")
        if (valeur := os.environ.get(cle, "").strip())
    )
    return _SYSTEME_INTERDITS + depuis_env


def _est_sous(chemin: Path, ancetre: Path) -> bool:
    """`chemin` est-il `ancetre` ou l'un de ses descendants ? (comparaison de l'OS)."""
    try:
        chemin.relative_to(ancetre)
    except ValueError:
        return False
    return True


def _contient(chemin: Path, descendant: Path) -> bool:
    """`chemin` est-il un ancêtre **strict** de `descendant` ?"""
    return chemin != descendant and _est_sous(descendant, chemin)


def _dossier_utilisateur() -> Path | None:
    """Le dossier de l'utilisateur courant, résolu — `None` si l'OS ne le dit pas."""
    try:
        return Path.home().resolve()
    except (RuntimeError, OSError):  # pragma: no cover - dépend de l'environnement
        return None


def _depot_maestro() -> Path:
    """La racine du dépôt de Maestro (depuis `maestro/projets/racine.py`)."""
    return Path(__file__).resolve().parents[2]


def _dossier_git(marqueur: Path) -> Path | None:
    """Le dossier `.git` effectif derrière `marqueur`, ou None si ce n'est pas un dépôt.

    Exige un `HEAD` : un `.git` vide ou résiduel (clone avorté) n'est **pas** un
    dépôt, et le déclarer tel enverrait #224 tenter un worktree Git là où il
    faut une copie. C'est aussi ce qui borne le `gitdir:` d'un worktree, qui
    pointe légitimement **hors** de la racine du projet et ne peut donc pas être
    contraint à y rester.
    """
    if marqueur.is_dir():
        return marqueur if (marqueur / "HEAD").is_file() else None
    if not marqueur.is_file():
        return None
    # `.git` fichier : le cas d'un worktree Git — « gitdir: <chemin> ».
    texte = marqueur.read_text(encoding="utf-8", errors="replace").strip()
    if not texte.startswith("gitdir:"):
        return None
    cible = Path(texte[len("gitdir:") :].strip())
    if not cible.is_absolute():
        cible = marqueur.parent / cible
    return cible.resolve() if (cible / "HEAD").is_file() else None


def _config_git(dossier_git: Path) -> Path:
    """Le `config` du dépôt — celui du dépôt **commun** si `dossier_git` est un worktree."""
    commun = dossier_git / "commondir"
    if commun.is_file():
        cible = Path(commun.read_text(encoding="utf-8", errors="replace").strip())
        if not cible.is_absolute():
            cible = dossier_git / cible
        return cible.resolve() / "config"
    return dossier_git / "config"


def _branche_courante(dossier_git: Path) -> str:
    """La branche pointée par `HEAD`, ou "" (HEAD détaché, ou fichier illisible)."""
    head = dossier_git / "HEAD"
    if not head.is_file():
        return ""
    texte = head.read_text(encoding="utf-8", errors="replace").strip()
    prefixe = "ref: refs/heads/"
    return texte[len(prefixe) :].strip() if texte.startswith(prefixe) else ""


def _url_origin(config: Path) -> str | None:
    """L'URL du remote `origin` dans un `config` Git, ou None s'il n'y en a pas.

    Mini-lecteur volontaire : le format de `git config` n'est pas tout à fait de
    l'INI (sections à sous-nom `[remote "origin"]`, valeurs indentées à la
    tabulation que `configparser` prendrait pour des continuations de ligne).
    """
    if not config.is_file():
        return None
    dans_origin = False
    for ligne in config.read_text(encoding="utf-8", errors="replace").splitlines():
        nue = ligne.strip()
        if nue.startswith("["):
            # Git tolère un commentaire derrière l'en-tête de section.
            entete = nue[: nue.index("]") + 1] if "]" in nue else nue
            dans_origin = entete.replace(" ", "") == '[remote"origin"]'
            continue
        cle, separateur, valeur = nue.partition("=")
        if dans_origin and separateur and cle.strip().lower() == "url":
            if propre := _valeur_git(valeur):
                return propre
    return None


def _valeur_git(brut: str) -> str:
    """La valeur d'une ligne `clé = valeur` de `git config` : commentaire ôté, déquotée."""
    valeur = brut.strip()
    for marque in (" #", " ;", "\t#", "\t;"):
        if marque in valeur:
            valeur = valeur[: valeur.index(marque)].strip()
    if len(valeur) >= 2 and valeur[0] == valeur[-1] == '"':
        valeur = valeur[1:-1]
    return valeur.strip()
