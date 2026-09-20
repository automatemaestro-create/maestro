"""Où naissent les espaces jetables, et qui les ramasse quand personne ne l'a fait (#992).

Deux défauts de la revue #568 se répondent ici, et ils n'en font qu'un vus du
disque : des espaces de travail s'accumulent sous le répertoire temporaire, et
ils ne s'accumulent pas là où on les cherche.

**S6 — l'accumulation.** `isolated_workspace` nettoie dans un `finally` ; un
process tué n'y passe jamais. Rien ne ramassait ensuite : ni la purge de la
Control Tower, ni le démarrage d'un hôte. La revue en comptait 69 le 2026-08-26,
72 le 2026-08-28, **76 le 2026-09-20** — la fuite est lente mais elle ne se
referme pas seule.

**S13 — l'adresse.** L'hypothèse du ticket était qu'un `TMPDIR=/tmp` hérité de
Git Bash donne deux endroits différents de part et d'autre. **Elle tient**, et la
mesure du 2026-09-20 la précise :

- un Git Bash **de connexion** pose bien `TMPDIR=/tmp` *et* `TMP=/tmp` — c'est de
  là que la valeur entre dans l'environnement d'un hôte lancé depuis un terminal ;
- MSYS monte `/tmp` sur `%TEMP%` (`C:/Users/<moi>/AppData/Local/Temp`, type
  `usertemp`) : côté agent, `/tmp` est le répertoire temporaire du profil ;
- Python sous Windows, lui, résout `/tmp` comme un chemin **enraciné sans
  lecteur**, donc sur le lecteur courant : `C:\\tmp` quand le process travaille
  sur `C:`, et rien du tout quand il travaille sur `D:`/`E:` — auquel cas
  `tempfile` retombe silencieusement sur `%TEMP%`.

D'où les deux conséquences mesurées : les 76 résidus sont dans `C:\\tmp`, et un
agent qui raisonne en `/tmp` ne trouve pas son propre espace. Le remède est le
même pour les deux : **écarter une valeur MSYS** quand on choisit la racine des
espaces (`racine_des_espaces`). Ce qui reste est `%TEMP%`, c'est-à-dire
exactement ce que le Bash de l'agent appelle `/tmp` — les deux côtés se
retrouvent au même endroit, sans qu'on ait à traduire quoi que ce soit dans le
message de la tâche.

**Ce que le ramassage retire, et ce qu'il ne retire jamais.** Un espace créé
depuis ce ticket porte le **pid** de son process dans son nom
(`maestro-dev-pid4312-a1b2c3d4`, cf. `marquer`) : la question « une tâche vivante
l'occupe-t-elle ? » se pose alors au système, pas à une horloge. Trois verdicts,
et chacun a sa raison :

- **pid vivant** → conservé. Un pid recyclé fait conserver un orphelin ; c'est le
  seul sens dans lequel l'erreur est acceptable, et c'est pourquoi la question est
  posée ainsi ;
- **pid mort** → retiré tout de suite. Le marqueur est là pour ça : nul besoin
  d'attendre qu'un espace refroidisse quand son propriétaire est nommé et mort ;
- **aucun marqueur** (les 76 d'avant ce ticket) → retiré seulement après
  `SEUIL_ORPHELIN_H` sans la moindre activité. Sans pid à interroger, l'âge est le
  seul signal, et il doit être large : un hôte d'une version antérieure pourrait
  encore travailler dedans.

Et un refus qui prime sur les trois : **un espace qui porte un worktree Git n'est
jamais retiré**, même mort, même vieux. C'est la signature d'une tâche sur un
projet *versionné* (`maestro.sandbox.projet`), où ce qui n'est pas commité vit
dans l'arbre et nulle part ailleurs — la règle que `scripts/git/worktree.sh gc`
tient déjà pour les worktrees de ce dépôt (docs/10 §9.2) : on ne ramasse pas du
travail que personne n'a sauvegardé. La signature se lit sans Git : un worktree
porte un `.git` **fichier** (qui pointe vers le dépôt), là où un `git init` fait
par un agent dans son espace jetable pose un `.git` **dossier**.

Le ramassage est **best-effort** de bout en bout : il ne lève pas, il n'attend
rien, et ce qui résiste est laissé pour la prochaine fois. `MAESTRO_RAMASSAGE_ESPACES=0`
l'éteint.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from maestro.fichiers import retirer_arbre

#: Les variables qui désignent le répertoire temporaire, dans l'ordre où
#: `tempfile` les consulte — le même, pour que la racine retenue reste celle que
#: la bibliothèque standard aurait choisie quand rien ne cloche.
VARIABLES_TEMP: tuple[str, ...] = ("TMPDIR", "TEMP", "TMP")

#: Le préfixe que partagent tous les espaces de Maestro. Rien d'autre n'est
#: candidat au ramassage.
PREFIXE_COMMUN = "maestro-"

#: La valeur que Git Bash pose dans `TMPDIR`/`TMP`, et donc la racine que Python
#: a pu résoudre de travers sur le lecteur courant avant ce ticket. Elle est
#: écrite ici plutôt que lue quelque part parce que **le process qui ramasse
#: n'hérite pas de l'environnement de celui qui a fui** : un hôte lancé depuis
#: PowerShell n'a aucun `TMPDIR`, et ne trouverait donc jamais les 76 résidus
#: qu'un hôte lancé depuis un terminal Git Bash a laissés sous `C:\\tmp`. C'est un
#: fait de la plateforme (le montage `usertemp` de MSYS), pas une constante d'un
#: autre module.
VALEUR_MSYS_HISTORIQUE = "/tmp"

#: Les préfixes des espaces nés **avant** le marqueur de pid — ceux des rôles
#: outillés (`RoleProfile.workspace_prefix`). Ils sont écrits ici plutôt
#: qu'importés : `maestro.agents` tire le runtime et son SDK, prix qu'un ménage
#: best-effort n'a pas à payer, et un import raté ferait un ramassage muet qui ne
#: ramasse rien. `tests/test_sandbox_ramassage.py` les confronte aux profils
#: réels : la liste ne peut pas prendre du retard sans rougir.
PREFIXES_HISTORIQUES: tuple[str, ...] = (
    "maestro-bdd-",
    "maestro-designer-",
    "maestro-dev-",
    "maestro-devops-",
    "maestro-qa-",
)

#: Combien de temps un espace **sans marqueur** doit être resté sans la moindre
#: activité avant d'être tenu pour orphelin. Large à dessein : sans pid à
#: interroger, c'est le seul filet contre un hôte d'une version antérieure encore
#: au travail. Même ordre de grandeur que le `MAESTRO_ORPHELIN_SEUIL` des
#: worktrees (docs/10 §9.6), et pour la même raison.
SEUIL_ORPHELIN_H = 6.0

#: Le réglage qui éteint le ramassage (`0`), sur le patron du dépôt.
VARIABLE_RAMASSAGE = "MAESTRO_RAMASSAGE_ESPACES"

#: Le seuil ci-dessus, en heures, quand le poste veut le sien.
VARIABLE_SEUIL = "MAESTRO_ESPACE_ORPHELIN_SEUIL"

#: Le marqueur de propriété dans le nom d'un espace : `…-pid<nombre>-<aléa>`.
#: L'aléa de `mkdtemp` ne contient jamais de tiret, donc le motif ne peut pas
#: mordre sur lui.
_MARQUE = re.compile(r"-pid(\d+)-[^-]*$")

#: `STILL_ACTIVE` de l'API Win32 — le code de sortie d'un process qui tourne.
_TOUJOURS_ACTIF = 259


@dataclass(frozen=True)
class Ramassage:
    """Ce qu'un passage a fait — de quoi écrire une ligne, jamais un rapport."""

    retires: tuple[Path, ...] = ()
    conserves: tuple[Path, ...] = ()
    echecs: tuple[Path, ...] = ()

    def __bool__(self) -> bool:
        """Vrai dès qu'il y avait quelque chose à dire — l'absence est muette."""
        return bool(self.retires or self.echecs)

    def resume(self) -> str:
        """Une ligne pour le journal d'un hôte, vide quand il n'y avait rien à faire."""
        if not self:
            return ""
        morceaux = [f"{len(self.retires)} espace(s) orphelin(s) retiré(s)"]
        if self.echecs:
            morceaux.append(f"{len(self.echecs)} résistant(s)")
        if self.conserves:
            morceaux.append(f"{len(self.conserves)} conservé(s)")
        return "Ramassage des espaces de travail : " + ", ".join(morceaux) + "."


# ── Où les espaces naissent ───────────────────────────────────────────────────


def marquer(prefixe: str) -> str:
    """Le préfixe d'un espace, augmenté du pid de ce process (`maestro-dev-pid4312-`).

    Dans le **nom** et non dans un fichier témoin : un témoin posé au milieu de
    l'espace ressortirait en livrable (`Workspace.produced_files` recense tout ce
    qui s'y trouve) et l'agent le verrait dans son `ls`. Le nom, lui, ne coûte
    rien à personne et survit à tout ce que l'agent écrit.
    """
    return f"{prefixe}pid{os.getpid()}-"


def pid_dans(nom: str) -> int | None:
    """Le pid inscrit dans le nom d'un espace, `None` s'il n'en porte pas."""
    trouve = _MARQUE.search(nom)
    return int(trouve.group(1)) if trouve else None


def _valeur_msys(brut: str) -> bool:
    """Cette valeur de `TMPDIR` est-elle un chemin MSYS, illisible pour Python ?

    Sous Windows seulement, et sur un seul critère : un chemin **enraciné sans
    lecteur** (`/tmp`). Un chemin Windows porte son lecteur (`C:\\…`) ou son hôte
    (`\\\\serveur\\partage`), jamais une barre oblique seule en tête ; `//serveur/…`
    est une UNC écrite à la POSIX et reste donc lisible.
    """
    if sys.platform != "win32":
        return False
    return brut.startswith("/") and not brut.startswith("//")


def racine_des_espaces(environnement: Mapping[str, str] | None = None) -> Path:
    """Le répertoire sous lequel créer un espace jetable — celui que l'agent verra.

    La règle de `tempfile`, moins les valeurs MSYS (cf. l'en-tête du module) :
    c'est ce qui fait tomber Python et le Bash de l'agent au même endroit sous
    Windows. Hors Windows rien ne change — aucune valeur n'est écartée, et
    l'absence de toute variable retombe sur `tempfile.gettempdir()`, qui garde
    ses replis (`/tmp`, le répertoire courant…).

    ⚠ **`tempfile.tempdir` n'est pas consulté**, et c'est une décision : ce
    n'est pas seulement le point de surcharge programmatique de `tempfile`, c'est
    aussi son **cache** — le premier `gettempdir()` du process y écrit ce qu'il
    vient de résoudre. Une valeur trouvée là ne dit donc pas si quelqu'un l'a
    voulue ou si la bibliothèque s'en souvient, et la lire d'abord reviendrait à
    rendre pour toujours la première résolution venue, MSYS comprise. Qui veut
    imposer une racine passe donc par l'environnement — là même d'où vient le
    défaut qu'on répare.
    """
    env = os.environ if environnement is None else environnement
    for nom in VARIABLES_TEMP:
        brut = (env.get(nom) or "").strip()
        if not brut or _valeur_msys(brut):
            continue
        candidat = Path(brut)
        try:
            if candidat.is_dir():
                return candidat
        except OSError:  # pragma: no cover - valeur non représentable
            continue
    return Path(tempfile.gettempdir())


def racines_connues(environnement: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    """Toutes les racines où un espace de Maestro a pu naître — à balayer.

    Trois provenances, et la troisième est celle des 76 résidus :

    1. `racine_des_espaces` — là où ils naissent depuis ce ticket ;
    2. `tempfile.gettempdir()` — là où ils naissaient avant, quand la valeur MSYS
       n'était pas écartée et que le process travaillait sur le bon lecteur ;
    3. sous Windows, la résolution d'une valeur MSYS (`/tmp` → `C:\\tmp`) sur le
       lecteur du répertoire courant **et** sur celui du temporaire du système.
       Celle que l'environnement porte, s'il en porte une, **et de toute façon**
       `VALEUR_MSYS_HISTORIQUE` : le process qui ramasse n'est pas celui qui a
       fui, et un hôte lancé depuis PowerShell n'hérite d'aucun `TMPDIR` alors
       qu'il doit quand même trouver ce qu'un hôte lancé depuis Git Bash a laissé.

    Seuls les dossiers qui **existent** sont rendus — donc rien du tout sur un
    poste où `<lecteur>:\\tmp` n'a jamais été créé —, sans doublon et dans un ordre
    stable.
    """
    env = os.environ if environnement is None else environnement
    candidates: list[Path] = [racine_des_espaces(env), Path(tempfile.gettempdir())]
    if sys.platform == "win32":
        lecteurs = {_lecteur(Path.cwd()), *(_lecteur(c) for c in list(candidates))}
        valeurs = {VALEUR_MSYS_HISTORIQUE}
        for nom in VARIABLES_TEMP:
            brut = (env.get(nom) or "").strip()
            if _valeur_msys(brut):
                valeurs.add(brut)
        for valeur in sorted(valeurs):
            candidates.extend(Path(f"{lettre}{valeur}") for lettre in lecteurs if lettre)
    vues: dict[str, Path] = {}
    for candidate in candidates:
        try:
            if not candidate.is_dir():
                continue
            cle = str(candidate.resolve()).casefold()
        except OSError:  # pragma: no cover - racine illisible
            continue
        vues.setdefault(cle, candidate)
    return tuple(vues.values())


def _lecteur(chemin: Path) -> str:
    """`C:` pour un chemin sur `C:`, vide pour un chemin qui n'en porte pas."""
    try:
        return os.path.splitdrive(str(chemin.resolve()))[0]
    except OSError:  # pragma: no cover - chemin illisible
        return ""


# ── Qui est vivant ────────────────────────────────────────────────────────────


def pid_vivant(pid: int) -> bool:
    """Ce pid **tourne**-t-il encore ? — et un zombie ne tourne pas.

    Sous Windows, `OpenProcess` + `GetExitCodeProcess` : `os.kill(pid, 0)` y
    **tue** au lieu d'interroger, ce qui ferait de la question sa propre réponse.

    Sous POSIX, `os.kill(pid, 0)` réussit encore sur un **zombie** — un process
    mort dont personne n'a lu le code de sortie. Dans le conteneur du filet CI le
    pid 1 n'adopte ni ne récolte, si bien que la dépouille reste visible pour
    toujours : d'où la lecture de `/proc/<pid>/stat`, où l'état `Z` tranche, et le
    repli sur le signal 0 pour les POSIX sans `/proc` (macOS).

    Le champ `comm` de `stat` peut contenir des espaces et des parenthèses :
    l'état se lit **après la dernière** `)`, jamais en découpant sur les espaces.
    """
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            stat_ = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            try:
                os.kill(pid, 0)
            except OSError:
                return False
            return True
        etat = stat_.rpartition(")")[2].split()
        return bool(etat) and etat[0] != "Z"
    import ctypes

    k = ctypes.windll.kernel32
    handle = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(handle, ctypes.byref(code))
    k.CloseHandle(handle)
    return code.value == _TOUJOURS_ACTIF


# ── Le ramassage ──────────────────────────────────────────────────────────────


def espaces(racines: Iterable[Path]) -> Iterator[Path]:
    """Les dossiers d'espaces de Maestro sous `racines` — marqués ou historiques.

    Rien d'autre n'est candidat : les autres `maestro-*` du répertoire temporaire
    (l'atelier d'un hôte, un aperçu de source, un masque de conteneur) appartiennent
    à des mécanismes qui ne sont pas celui-ci et qui ont leur propre fin de vie.
    """
    for racine in racines:
        try:
            enfants = sorted(racine.iterdir())
        except OSError:  # pragma: no cover - racine devenue illisible
            continue
        for chemin in enfants:
            try:
                if not chemin.is_dir():
                    continue
            except OSError:  # pragma: no cover - entrée illisible
                continue
            nom = chemin.name
            if not nom.startswith(PREFIXE_COMMUN):
                continue
            if pid_dans(nom) is not None or nom.startswith(PREFIXES_HISTORIQUES):
                yield chemin


def porte_un_worktree(espace: Path) -> bool:
    """`espace` abrite-t-il un worktree Git — donc peut-être du travail non sauvegardé ?

    Un worktree pose un `.git` **fichier** qui pointe vers le dépôt ; un `git init`
    fait par un agent dans son espace jetable pose un `.git` **dossier**. La
    distinction suffit, et elle se lit sans appeler Git.
    """
    try:
        enfants = list(espace.iterdir())
    except OSError:  # pragma: no cover - espace illisible
        return False
    for enfant in (espace, *enfants):
        try:
            if (enfant / ".git").is_file():
                return True
        except OSError:  # pragma: no cover - entrée illisible
            continue
    return False


def _derniere_activite(espace: Path) -> float:
    """La date de modification la plus récente de l'espace ou de ses enfants directs.

    Un balayage complet coûterait un `rglob` par espace à chaque démarrage d'hôte ;
    les enfants directs suffisent à distinguer un espace où l'on travaille d'un
    espace abandonné depuis des heures.
    """
    dates = []
    try:
        dates.append(espace.stat().st_mtime)
        dates.extend(enfant.stat().st_mtime for enfant in espace.iterdir())
    except OSError:  # pragma: no cover - espace partiellement illisible
        pass
    return max(dates, default=0.0)


def est_orphelin(
    espace: Path, *, maintenant: float | None = None, seuil_s: float | None = None
) -> bool:
    """Plus personne ne travaille ici, et rien de non sauvegardé n'y dort.

    Les trois verdicts de l'en-tête, dans l'ordre où ils se posent : le worktree
    d'abord (un refus qui prime), puis le pid s'il y en a un, puis l'âge.
    """
    if porte_un_worktree(espace):
        return False
    pid = pid_dans(espace.name)
    if pid is not None:
        return not pid_vivant(pid)
    reference = time.time() if maintenant is None else maintenant
    plafond = SEUIL_ORPHELIN_H * 3600 if seuil_s is None else seuil_s
    return reference - _derniere_activite(espace) >= plafond


def _seuil_du_poste(environnement: Mapping[str, str]) -> float:
    """Le seuil en secondes — celui du poste s'il en pose un de lisible et positif."""
    brut = (environnement.get(VARIABLE_SEUIL) or "").strip()
    try:
        heures = float(brut)
    except ValueError:
        return SEUIL_ORPHELIN_H * 3600
    return heures * 3600 if heures > 0 else SEUIL_ORPHELIN_H * 3600


def ramasser(
    *,
    racines: Iterable[Path] | None = None,
    environnement: Mapping[str, str] | None = None,
    maintenant: float | None = None,
) -> Ramassage:
    """Retire les espaces que plus aucune tâche vivante n'occupe — best-effort, sans lever.

    Appelé au démarrage de l'hôte détaché (`maestro.controltower.hote_detache`),
    c'est-à-dire juste avant que des espaces neufs ne naissent, et par quiconque
    veut faire le ménage. Rendre la main sans rien avoir fait est une issue
    normale : le ramassage est un filet, pas une étape du run.
    """
    env = os.environ if environnement is None else environnement
    if (env.get(VARIABLE_RAMASSAGE) or "").strip() == "0":
        return Ramassage()
    seuil_s = _seuil_du_poste(env)
    retires: list[Path] = []
    conserves: list[Path] = []
    echecs: list[Path] = []
    try:
        candidats = list(espaces(racines_connues(env) if racines is None else racines))
    except OSError:  # pragma: no cover - plus aucune racine lisible
        return Ramassage()
    for espace in candidats:
        try:
            if not est_orphelin(espace, maintenant=maintenant, seuil_s=seuil_s):
                conserves.append(espace)
                continue
            (retires if retirer_arbre(espace) else echecs).append(espace)
        except OSError:  # pragma: no cover - espace disparu en cours de route
            echecs.append(espace)
    return Ramassage(tuple(retires), tuple(conserves), tuple(echecs))
