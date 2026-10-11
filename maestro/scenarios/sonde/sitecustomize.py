"""La sonde : ce qu'un processus Python exécute pendant chaque scénario du banc (#1463).

Python charge ce module au démarrage de **chaque** processus dont le `PYTHONPATH`
porte ce dossier (le mécanisme `sitecustomize` de `site`). Sans
`MAESTRO_CARTE_DOSSIER`, il ne fait rien. Avec elle, il relève, sous la copie de
travail dont il fait partie :

- les **fonctions** exécutées — un objet code par fonction, rangé par son fichier et
  son nom qualifié (`Classe.methode`), **jamais par sa ligne** : un diff décale les
  lignes, pas les noms ;
- les **fichiers lus** — l'événement d'audit `open`, en lecture seulement, hors code.

C'est la matière de la carte que `maestro.scenarios.carte` assemble à la fin d'un
passage du banc.

## Trois choses à ne pas défaire

**Elle n'importe pas `maestro`.** Elle se charge avant que le répertoire courant
n'entre dans `sys.path` : importer `maestro` ici trouverait celui de l'installation
éditable — le clone principal, depuis un worktree —, et le processus garderait ce
paquet-là. D'où la bibliothèque standard seule, et une syntaxe que lit aussi le
Python d'un projet que le banc fait tourner, qui hérite du `PYTHONPATH` : rien n'y
est sous la copie, donc rien n'y est relevé, mais la sonde ne doit pas y lever. Le
`sitecustomize` qu'elle masque, s'il y en a un, est chargé après elle.

**Le produit n'en sait rien.** Aucune route, aucune option, aucune branche « mode
banc » : la sonde se pose par l'environnement que `start.sh` donne à un passage, et
tout processus que la stack lance en hérite — l'API, l'hôte détaché et ses runs,
l'API que S12 rallume, le banc lui-même.

**Les scénarios se bornent par un fichier.** Le banc écrit l'identifiant du scénario
qui commence dans `<dossier>/segment` ; chaque processus le relit toutes les
`PERIODE_S` et range sous ce nom ce qu'il exécute ensuite. Pendant une période, un
processus range encore sous le scénario précédent : le banc attend quelques
périodes à chaque borne. Avant la première borne, tout va sous `_demarrage` (ce que
la stack exécute avant le premier scénario) ; `_fin` éteint la sonde, et la stack
que le passage laisse servie tourne sans elle.

## Comment

Sous Python 3.12 et après, `sys.monitoring` : l'événement `PY_START` se désarme pour
chaque objet code dès sa première exécution (`DISABLE`), et se réarme à chaque
borne (`restart_events`) — une fonction coûte un appel par scénario, pas un par
exécution. Sous 3.11, un `sys.settrace` qui ne trace aucune ligne, plus cher : c'est
un repli. Chaque processus ajoute à `<dossier>/traces/<pid>-<jeton>.tsv` une ligne
par fonction ou par fichier **nouveaux** de son scénario, à chaque période : un
processus tué ne perd que la dernière.
"""

from __future__ import annotations

import atexit
import os
import sys
import threading
from collections.abc import Mapping
from types import CodeType, FrameType

#: Le dossier de la carte d'un passage — posé par `maestro.scenarios.carte --preparer`.
VARIABLE_DOSSIER = "MAESTRO_CARTE_DOSSIER"

#: Le fichier où le banc écrit le scénario en cours, et le dossier des relevés.
FICHIER_SEGMENT = "segment"
DOSSIER_TRACES = "traces"
SUFFIXE_TRACE = ".tsv"

#: Ce que la stack exécute avant la première borne, et la borne qui éteint la sonde.
SEGMENT_DEMARRAGE = "_demarrage"
SEGMENT_FIN = "_fin"

#: Tous les combien un processus relit la borne et écrit ce qu'il a relevé.
PERIODE_S = 0.2

#: Les trois sortes de lignes d'un relevé : `<segment>\t<sorte>\t<champ>[\t<champ>]`.
SORTE_FONCTION = "f"
SORTE_FICHIER = "d"
SORTE_PROCESSUS = "p"
SEPARATEUR = "\t"

#: Ce qu'on ne relève jamais, à aucune profondeur : l'interpréteur et ses paquets,
#: l'outillage du dépôt, git, les caches. Le reste se filtre à l'assemblage.
DOSSIERS_IGNORES = frozenset({".venv", ".tools", ".git", "node_modules", "__pycache__"})

#: Le code se relève par ses fonctions : ces fichiers ne sont pas des données.
SUFFIXES_CODE = (".py", ".pyc", ".pyd", ".pyi", ".so")

#: Le nom de l'outil auprès de `sys.monitoring`, et les identifiants qu'il essaie :
#: 0, 1, 2 et 5 sont ceux du débogueur, de la couverture, du profileur et de
#: l'optimiseur — un `coverage` ou un débogueur lancé là garde le sien.
NOM_OUTIL = "maestro-carte"
OUTILS_CANDIDATS = (3, 4)

#: Ce qu'une ligne de processus garde de sa commande.
COMMANDE_MAX = 200

#: La copie de travail : `maestro/scenarios/sonde/sitecustomize.py` en est à trois
#: dossiers. Dérivée, jamais posée : la sonde d'une copie ne relève que la sienne.
RACINE = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)


class _Seau:
    """Ce qu'un processus a relevé pendant un scénario — et ce qu'il en a déjà écrit."""

    __slots__ = ("segment", "codes", "fichiers", "codes_lus", "fichiers_lus", "ecrits")

    def __init__(self, segment: str) -> None:
        self.segment = segment
        self.codes: set[CodeType] = set()
        self.fichiers: set[str] = set()
        self.codes_lus: set[CodeType] = set()
        self.fichiers_lus: set[str] = set()
        self.ecrits: set[tuple[str, ...]] = set()


class Sonde:
    """La sonde d'un processus. `demarrer` la pose ; le reste se fait seul."""

    def __init__(
        self,
        dossier: str,
        *,
        racine: str = RACINE,
        periode_s: float = PERIODE_S,
        monitoring: bool | None = None,
    ) -> None:
        self.dossier = os.path.abspath(dossier)
        self._prefixe = os.path.normcase(os.path.join(os.path.abspath(racine), ""))
        self._prefixe_dossier = os.path.normcase(os.path.join(self.dossier, ""))
        self._propre = os.path.normcase(os.path.abspath(__file__))
        self._periode_s = periode_s
        self.traces = os.path.join(
            self.dossier,
            DOSSIER_TRACES,
            f"{os.getpid()}-{os.urandom(4).hex()}{SUFFIXE_TRACE}",
        )
        self._relatifs: dict[str, str | None] = {}
        self._verrou = threading.Lock()
        self._arret = threading.Event()
        self._actif = False
        self._outil: int | None = None
        self._desarme: object = None
        self.monitoring = (sys.version_info >= (3, 12)) if monitoring is None else monitoring
        self.seau = _Seau(self._lire_segment() or SEGMENT_DEMARRAGE)

    # ── Poser et ôter ────────────────────────────────────────────────────────

    def demarrer(self) -> bool:
        """Pose la sonde sur le processus — faux si le passage est déjà fini."""
        if self.seau.segment == SEGMENT_FIN:
            return False
        self._armer()
        self._actif = True
        sys.addaudithook(self._audit)
        threading.Thread(target=self._battre_sans_fin, name=NOM_OUTIL, daemon=True).start()
        atexit.register(self.arreter)
        return True

    def _armer(self) -> None:
        if sys.version_info >= (3, 12) and self.monitoring:
            surveillance = sys.monitoring
            for outil in OUTILS_CANDIDATS:
                if surveillance.get_tool(outil) is None:
                    surveillance.use_tool_id(outil, NOM_OUTIL)
                    surveillance.register_callback(
                        outil, surveillance.events.PY_START, self._au_debut
                    )
                    surveillance.set_events(outil, surveillance.events.PY_START)
                    self._outil = outil
                    self._desarme = surveillance.DISABLE
                    return
            self.monitoring = False  # aucun identifiant libre : le repli
        sys.settrace(self._a_l_appel)
        threading.settrace(self._a_l_appel)

    def _desarmer(self) -> None:
        self._actif = False
        if sys.version_info >= (3, 12) and self._outil is not None:
            sys.monitoring.set_events(self._outil, 0)
            sys.monitoring.register_callback(self._outil, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(self._outil)
            self._outil = None
        else:
            # `settrace` ne vaut que pour le fil qui l'appelle : les autres gardent la
            # fonction, qui ne relève plus rien une fois la sonde inactive.
            sys.settrace(None)
            threading.settrace(None)  # type: ignore[arg-type,unused-ignore]

    def arreter(self) -> None:
        """Écrit ce qui reste — à la sortie du processus."""
        self._arret.set()
        try:
            self.vider()
        except Exception:  # noqa: BLE001 - la sortie d'un processus ne lève jamais pour la carte
            pass

    # ── Relever ──────────────────────────────────────────────────────────────

    def _au_debut(self, code: CodeType, _decalage: int) -> object:
        self.seau.codes.add(code)
        return self._desarme

    def _a_l_appel(self, frame: FrameType, evenement: str, _arg: object) -> None:
        if evenement == "call" and self._actif:
            self.seau.codes.add(frame.f_code)
        return None

    def _audit(self, evenement: str, arguments: tuple[object, ...]) -> None:
        if evenement != "open" or not self._actif:
            return
        try:
            chemin, mode, drapeaux = arguments
            if not isinstance(chemin, (str, bytes, os.PathLike)) or not _en_lecture(mode, drapeaux):
                return
            texte = os.fsdecode(chemin)
            if texte.endswith(SUFFIXES_CODE):
                return
            relatif = self.relatif(os.path.abspath(texte))
            if relatif is not None:
                self.seau.fichiers.add(relatif)
        except Exception:  # noqa: BLE001 - un hook d'audit qui lève ferait lever `open`
            return

    def relatif(self, absolu: str) -> str | None:
        """Le chemin sous la copie, en `/`, ou `None` s'il n'y est pas (ou ne compte pas)."""
        if absolu in self._relatifs:
            return self._relatifs[absolu]
        rendu: str | None = None
        normal = os.path.normcase(absolu)
        if (
            normal.startswith(self._prefixe)
            and normal != self._propre
            and not normal.startswith(self._prefixe_dossier)
        ):
            parties = absolu[len(self._prefixe) :].split(os.sep)
            if not any(partie in DOSSIERS_IGNORES for partie in parties):
                rendu = "/".join(parties)
        self._relatifs[absolu] = rendu
        return rendu

    # ── Borner et écrire ─────────────────────────────────────────────────────

    def battre(self) -> None:
        """Une période : relit la borne, bascule si elle a bougé, écrit le nouveau."""
        segment = self._lire_segment()
        if segment is not None and segment != self.seau.segment:
            self.basculer(segment)
        else:
            self.vider()

    def basculer(self, segment: str) -> None:
        """Range désormais sous `segment`, et écrit ce que le précédent a relevé."""
        with self._verrou:
            ancien = self.seau
            self.seau = _Seau(segment)
            if segment == SEGMENT_FIN:
                self._desarmer()
                self._arret.set()
            elif sys.version_info >= (3, 12) and self._outil is not None:
                sys.monitoring.restart_events()
            self._vider(ancien)

    def vider(self) -> None:
        """Écrit ce que le scénario en cours a relevé de nouveau."""
        with self._verrou:
            self._vider(self.seau)

    def _vider(self, seau: _Seau) -> None:
        if seau.segment == SEGMENT_FIN:
            return
        lignes: list[tuple[str, ...]] = []
        codes = seau.codes.copy()  # une copie est atomique ; itérer le vivant ne l'est pas
        nouveaux = codes - seau.codes_lus
        seau.codes_lus |= nouveaux
        for code in nouveaux:
            relatif = self.relatif(os.path.abspath(code.co_filename))
            if relatif is None or not relatif.endswith(".py"):
                continue
            nom = getattr(code, "co_qualname", code.co_name)
            lignes.append((SORTE_FONCTION, relatif, nom))
        fichiers = seau.fichiers.copy() - seau.fichiers_lus
        seau.fichiers_lus |= fichiers
        lignes.extend((SORTE_FICHIER, relatif) for relatif in fichiers)
        lignes = [ligne for ligne in lignes if ligne not in seau.ecrits]
        if not lignes:
            return
        if not seau.ecrits:
            lignes.insert(0, (SORTE_PROCESSUS, str(os.getpid()), _commande()))
        seau.ecrits.update(lignes)
        texte = "".join(
            SEPARATEUR.join((seau.segment, *(_champ(champ) for champ in ligne))) + "\n"
            for ligne in lignes
        )
        try:
            os.makedirs(os.path.dirname(self.traces), exist_ok=True)
            with open(self.traces, "a", encoding="utf-8", newline="\n") as flux:
                flux.write(texte)
        except OSError:
            return

    def _lire_segment(self) -> str | None:
        try:
            with open(os.path.join(self.dossier, FICHIER_SEGMENT), encoding="utf-8") as flux:
                texte = flux.read(256)
        except OSError:
            return None
        segment = texte.strip()
        if not segment or any(signe in segment for signe in "\t\r\n"):
            return None  # en cours d'écriture : la période suivante le lira entier
        return segment

    def _battre_sans_fin(self) -> None:
        while not self._arret.wait(self._periode_s):
            try:
                self.battre()
            except Exception:  # noqa: BLE001 - la sonde ne fait jamais tomber le processus
                pass


def _en_lecture(mode: object, drapeaux: object) -> bool:
    """Une ouverture en lecture seule : `open` (son mode) ou `os.open` (ses drapeaux)."""
    if isinstance(mode, str):
        return not any(lettre in mode for lettre in "wax+")
    if isinstance(drapeaux, int):
        ecriture = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
        return not drapeaux & ecriture
    return False


def _commande() -> str:
    """La commande du processus, sans l'interpréteur — ce qui le nomme dans la carte."""
    arguments = list(getattr(sys, "orig_argv", None) or sys.argv)[1:]
    return " ".join(arguments)[:COMMANDE_MAX]


def _champ(valeur: str) -> str:
    return valeur.replace(SEPARATEUR, " ").replace("\n", " ").replace("\r", " ")


def activer(environnement: Mapping[str, str] | None = None) -> Sonde | None:
    """Pose la sonde si l'environnement nomme un dossier de carte — jamais ne lève."""
    env = os.environ if environnement is None else environnement
    dossier = (env.get(VARIABLE_DOSSIER) or "").strip()
    if not dossier:
        return None
    try:
        sonde = Sonde(dossier)
        return sonde if sonde.demarrer() else None
    except Exception:  # noqa: BLE001 - un processus démarre, carte ou pas
        return None


def _chainer() -> None:
    """Charge le `sitecustomize` que celui-ci masque, s'il y en a un plus loin sur le chemin."""
    ici = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))
    for entree in sys.path:
        dossier = os.path.abspath(entree or os.curdir)
        if os.path.normcase(dossier) == ici:
            continue
        candidat = os.path.join(dossier, "sitecustomize.py")
        if not os.path.isfile(candidat):
            continue
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location("_sitecustomize_masque", candidat)
            if spec is not None and spec.loader is not None:
                spec.loader.exec_module(importlib.util.module_from_spec(spec))
        except Exception:  # noqa: BLE001 - comme `site` : un sitecustomize fautif ne bloque rien
            pass
        return


#: La sonde de ce processus, quand il en porte une — ce qu'un test interroge.
SONDE: Sonde | None = None

if __name__ == "sitecustomize":
    SONDE = activer()
    _chainer()
