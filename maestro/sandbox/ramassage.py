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
par un agent dans son espace jetable pose un `.git` **dossier**. Le refus tombe
dans un seul cas (#1455) : le dépôt que ce fichier désigne **n'existe plus** —
le travail n'a alors plus nulle part où revenir, et l'espace est une dépouille
comme une autre.

**#1455 — une racine pour tout le jetable, et toutes ses familles.** Le
2026-10-08, ce ramassage ne connaissait que les espaces des rôles outillés, et le
répertoire temporaire du poste portait **1 286** dossiers `maestro-*` : 436
ateliers d'hôte (exclus à dessein, « le système les ramassera » — Windows ne le
fait pas), 692 résidus de l'ancien mode démo (#1210), une trentaine d'espaces
d'équipes sur mesure dont le préfixe n'était dans aucune liste. Depuis, tout le
jetable du produit naît sous **une** racine, `<temp>/maestro/`
(`maestro.emplacements`), et le ramassage balaie deux sortes d'endroits :

- **la racine jetable**, où tout dossier est à Maestro et donc candidat — sauf l'état de la stack
  d'une copie de travail, qui part avec sa copie (`FAMILLES_TIERCES_JETABLES`, #1456) ;
- **les anciens emplacements** (le temporaire lui-même et ses résolutions MSYS),
  où l'est tout `maestro-*` — un rôle ajouté demain n'a pas à entrer dans une
  liste —, sauf les familles qu'un autre mécanisme possède (`FAMILLES_TIERCES`).

Un jetable que le pid de son nom ne désigne pas peut se nommer par un témoin
(`maestro.emplacements.TEMOIN_PID`) : c'est le cas de l'atelier d'un hôte, que
l'API ouvre avant que son occupant n'existe. Un témoin vivant le conserve ; un
témoin mort ou absent le laisse à l'âge — jamais plus tôt, parce que l'API lit
encore le journal d'un hôte mort pour en nommer la cause (#446).

Le ramassage est **best-effort** de bout en bout : il ne lève pas, il n'attend
rien, et ce qui résiste est laissé pour la prochaine fois. `MAESTRO_RAMASSAGE_ESPACES=0`
l'éteint.
"""

from __future__ import annotations

import os
import re
import sys
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from maestro.emplacements import (
    VARIABLES_TEMP,
    _valeur_msys,
    marquer,
    occupant,
    racine_jetable,
    repertoire_temporaire,
)
from maestro.fichiers import retirer_arbre

__all__ = [
    "FAMILLES_TIERCES",
    "FAMILLES_TIERCES_JETABLES",
    "PREFIXE_COMMUN",
    "SEUIL_ORPHELIN_H",
    "VALEUR_MSYS_HISTORIQUE",
    "VARIABLES_TEMP",
    "VARIABLE_RAMASSAGE",
    "VARIABLE_SEUIL",
    "Ramassage",
    "espaces",
    "est_orphelin",
    "marquer",
    "pid_dans",
    "pid_vivant",
    "porte_un_worktree",
    "racine_des_espaces",
    "racines_connues",
    "ramasser",
]

#: Le préfixe que partagent tous les jetables de Maestro **aux anciens
#: emplacements** — là où ils voisinent avec ceux du reste du poste. Sous la
#: racine jetable, aucun préfixe n'est requis : tout y est à Maestro.
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

#: Les `maestro-*` des anciens emplacements qu'**un autre mécanisme possède**, et
#: que ce ramassage ne touche donc jamais — chacun avec sa raison :
#:
#: - `maestro-controltower-` : l'état de la stack d'une copie de travail à son
#:   ancienne adresse (`scripts/controltower/start.sh` avant #1456), profil de
#:   navigateur compris ; `start.sh` le retire à son démarrage, et seulement
#:   quand plus aucune stack ne l'occupe (`scripts/controltower/etat-stack.sh`) ;
#: - `maestro-presentation` : le cache de `scripts/presentation/captures.sh`
#:   (Node et navigateurs de Playwright), gardé à dessein d'un passage à l'autre.
#:
#: Une liste d'**exclusions**, jamais d'inclusions : c'est l'inclusion par préfixe
#: de rôle qui avait laissé les équipes sur mesure sur le disque pour toujours.
FAMILLES_TIERCES: tuple[str, ...] = (
    "maestro-controltower-",
    "maestro-presentation",
)

#: Les dossiers de la **racine jetable** qu'un autre mécanisme possède — la seule
#: exception à « tout y est candidat » :
#:
#: - `controltower-` : l'état de la stack d'une copie de travail
#:   (`scripts/controltower/etat-stack.sh`, #1456) — jeton de session, pid du
#:   chien de garde. Ce ramassage le jugerait par son âge, or une stack inactive
#:   depuis six heures n'en est pas moins vivante : lui retirer son jeton ferait
#:   perdre à son chien de garde la session qu'il surveille. Il part avec la copie
#:   qui l'a démarrée (`worktree.sh gc`, et `start.sh` à chaque démarrage), et
#:   jamais tant qu'un de ses ports écoute.
FAMILLES_TIERCES_JETABLES: tuple[str, ...] = ("controltower-",)

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

#: La ligne d'un `.git` de worktree qui nomme son dossier d'administration dans
#: le dépôt (`<dépôt>/.git/worktrees/<nom>`).
_GITDIR = re.compile(r"^gitdir:\s*(.+?)\s*$", re.MULTILINE)

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
#
# `marquer` et la résolution du répertoire temporaire vivent depuis #1455 dans
# `maestro.emplacements`, avec les deux autres racines du poste ; ils restent
# importables d'ici, où #992 les a démontrés.


def pid_dans(nom: str) -> int | None:
    """Le pid inscrit dans le nom d'un espace, `None` s'il n'en porte pas."""
    trouve = _MARQUE.search(nom)
    return int(trouve.group(1)) if trouve else None


def racine_des_espaces(environnement: Mapping[str, str] | None = None) -> Path:
    """Le répertoire sous lequel naissent les espaces jetables — celui que l'agent verra.

    `<temp>/maestro` depuis #1455 (`maestro.emplacements.racine_jetable`), sous le
    répertoire temporaire que Python et le Bash de l'agent résolvent au même
    endroit (#992, S13 — cf. l'en-tête). Rendu **non créé** : on y crée par
    `maestro.emplacements.jetable`, qui le pose au besoin.
    """
    return racine_jetable(environnement)


def racines_connues(environnement: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    """Les **anciens** emplacements où un jetable de Maestro a pu naître — à balayer.

    La racine jetable n'en est pas : tout y est à Maestro, elle se balaie sans
    filtre (`espaces`). Ici, trois provenances, et la troisième est celle des 76
    résidus de #992 :

    1. le répertoire temporaire de **cet** environnement — là où les espaces
       naissaient de #992 à #1455 ;
    2. celui d'un environnement **vide** (`tempfile.gettempdir()`) — là où ils
       naissaient avant, quand la valeur MSYS n'était pas écartée et que le
       process travaillait sur le bon lecteur ;
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
    candidates: list[Path] = [repertoire_temporaire(env), repertoire_temporaire({})]
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


def _dossiers(racine: Path) -> list[Path]:
    """Les sous-dossiers de `racine`, triés — vide si elle est absente ou illisible."""
    try:
        enfants = sorted(racine.iterdir())
    except OSError:  # racine absente, ou devenue illisible
        return []
    dossiers = []
    for chemin in enfants:
        try:
            if chemin.is_dir():
                dossiers.append(chemin)
        except OSError:  # pragma: no cover - entrée illisible
            continue
    return dossiers


def espaces(racines: Iterable[Path], *, jetables: Iterable[Path] = ()) -> Iterator[Path]:
    """Les jetables de Maestro — tout sous `jetables`, les `maestro-*` sous `racines`.

    Sous la racine jetable (`jetables`), tout dossier est à Maestro, moins les
    `FAMILLES_TIERCES_JETABLES`. Aux anciens emplacements (`racines`), qu'il
    partage avec le reste du poste, seul l'est ce qui porte le préfixe commun,
    moins les `FAMILLES_TIERCES` qu'un autre mécanisme possède.
    """
    for racine in jetables:
        for chemin in _dossiers(racine):
            if not chemin.name.startswith(FAMILLES_TIERCES_JETABLES):
                yield chemin
    for racine in racines:
        for chemin in _dossiers(racine):
            nom = chemin.name
            if nom.startswith(PREFIXE_COMMUN) and not nom.startswith(FAMILLES_TIERCES):
                yield chemin


def _depot_existe(fichier_git: Path) -> bool:
    """Le dépôt qu'un `.git` de worktree désigne existe-t-il encore ?

    Le fichier nomme le dossier d'administration du worktree dans le dépôt
    (`<dépôt>/.git/worktrees/<nom>`), absolu ou relatif au worktree. Illisible ou
    sans ligne `gitdir:`, on répond **oui** : dans le doute, la garde tient.
    """
    try:
        texte = fichier_git.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True
    trouve = _GITDIR.search(texte)
    if trouve is None:
        return True
    cible = Path(trouve.group(1))
    if not cible.is_absolute():
        cible = fichier_git.parent / cible
    try:
        return cible.exists()
    except OSError:  # pragma: no cover - chemin non représentable
        return True


def porte_un_worktree(espace: Path) -> bool:
    """`espace` abrite-t-il un worktree Git **dont le dépôt existe** — du travail à sauver ?

    Un worktree pose un `.git` **fichier** qui pointe vers le dépôt ; un `git init`
    fait par un agent dans son espace jetable pose un `.git` **dossier**. La
    distinction suffit, et elle se lit sans appeler Git. Un worktree dont le dépôt
    a disparu ne compte pas (#1455) : son travail n'a plus nulle part où revenir.
    """
    try:
        enfants = list(espace.iterdir())
    except OSError:  # pragma: no cover - espace illisible
        return False
    for enfant in (espace, *enfants):
        fichier = enfant / ".git"
        try:
            if fichier.is_file() and _depot_existe(fichier):
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

    Les verdicts de l'en-tête, dans l'ordre où ils se posent : le worktree d'abord
    (un refus qui prime), puis le pid du nom s'il y en a un, puis le témoin d'un
    occupant vivant, puis l'âge.
    """
    if porte_un_worktree(espace):
        return False
    pid = pid_dans(espace.name)
    if pid is not None:
        return not pid_vivant(pid)
    nomme = occupant(espace)
    if nomme is not None and pid_vivant(nomme):
        return False
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
    jetables: Iterable[Path] | None = None,
    environnement: Mapping[str, str] | None = None,
    maintenant: float | None = None,
) -> Ramassage:
    """Retire les jetables que plus aucun process vivant n'occupe — best-effort, sans lever.

    Sans argument, balaie la racine jetable du poste et ses anciens emplacements
    (`racines_connues`). Qui nomme l'un des deux (`racines` pour les anciens
    emplacements, `jetables` pour des racines dédiées) **ne balaie que ce qu'il
    nomme** : un appel borné à un dossier ne doit jamais déborder sur le poste.

    Appelé au démarrage de l'hôte détaché (`maestro.controltower.hote_detache`),
    et par quiconque veut faire le ménage. Rendre la main sans rien avoir fait est
    une issue normale : le ramassage est un filet, pas une étape du run.
    """
    env = os.environ if environnement is None else environnement
    if (env.get(VARIABLE_RAMASSAGE) or "").strip() == "0":
        return Ramassage()
    if racines is None and jetables is None:
        racines, jetables = racines_connues(env), (racine_jetable(env),)
    seuil_s = _seuil_du_poste(env)
    retires: list[Path] = []
    conserves: list[Path] = []
    echecs: list[Path] = []
    try:
        candidats = list(espaces(racines or (), jetables=jetables or ()))
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
