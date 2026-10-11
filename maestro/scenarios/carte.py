"""La carte : quel code chaque scénario du banc a exécuté (#1463).

    .venv/Scripts/python.exe -m maestro.scenarios.carte --preparer
    .venv/Scripts/python.exe -m maestro.scenarios.carte [<couverture.json>]

Le banc au ticket se déclenchait sur un **préfixe** (`GL_BANC_CHEMINS`, `maestro/
core/ packages/shared/`) : tout ticket du produit était « sur le chemin », et le
scénario se choisissait au jugé, large par prudence (docs/47). La carte dit ce que
chaque scénario **exécute vraiment** ; c'est elle qui remplacera le préfixe (#1464).

## Ce qu'elle contient

Pour chaque scénario joué, et pour le **démarrage** (ce que la stack exécute avant
le premier scénario, donc sous chacun) :

- les **fonctions** Python exécutées, par fichier : `{"maestro/x.py": ["Classe.methode",
  "fonction", "<module>"]}`. La granularité est la **fonction** — l'objet code, nommé
  par son nom qualifié —, jamais la ligne : un diff décale les lignes d'un fichier,
  pas les noms de ses fonctions. Le code de niveau module (une constante, un gabarit
  de prompt en tête de fichier) s'exécute à l'import, donc souvent avant le
  scénario : il se rattache à un scénario par son **fichier** ;
- les **fichiers de données lus** — playbooks, `core/`, gabarits, réglages — tels
  que la copie les suit. Le banc sert la configuration depuis sa copie sous
  `.maestro/banc/` (#1164) : une lecture là-bas est rendue au fichier suivi dont
  elle est la copie ;
- les **processus** qui y ont contribué (pid et commande) : l'API, l'hôte détaché et
  ses runs, l'API que S12 rallume, le banc lui-même.

Seuls entrent les fichiers **suivis par Git** dans la copie : l'état d'exécution,
les journaux, `.env` et ce qui n'est pas du dépôt n'y figurent pas.

## Comment elle se mesure

La sonde (`maestro.scenarios.sonde.sitecustomize`) est chargée par **chaque**
processus Python de la stack, parce que `--preparer` pose son dossier sur
`PYTHONPATH` et que `start.sh` l'exporte **seulement pour un passage**
(`--etat-banc --rejouer`) : le produit n'a aucune branche « mode banc ». Le banc
**borne** chaque scénario (`Carte.borner`) en écrivant son identifiant dans le
fichier que les processus relisent, puis attend `ATTENTE_BORNE_S` qu'ils l'aient
vu. À la fin du passage, `Carte.ecrire` assemble leurs relevés en
`couverture.json`, à côté du rapport ; l'historique du poste en garde une copie
avec la ligne du passage (`maestro.scenarios.historique`).

## Ce qu'elle ne sait pas

- **Une borne vaut une période** : ce qu'un processus exécute dans les
  `PERIODE_S` qui suivent une borne peut rester rangé sous le scénario précédent,
  et un travail qu'un scénario laisse en vol au suivant est rangé sous celui-ci.
- **Ce qu'un processus non Python lit** (le CLI de l'agent, l'UI) n'y est pas : la
  configuration qu'il reçoit passe par le Python qui la lit.
- **Une stack démarrée sans la sonde** ne dit rien : la carte le dit (« stack non
  mesurée ») plutôt que de rendre des ensembles vides pour une mesure.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from maestro.espace import racine_de_la_copie
from maestro.fichiers import retirer_arbre
from maestro.scenarios.modele import Rapport
from maestro.scenarios.rapport import RACINE_RAPPORTS
from maestro.scenarios.sonde import sitecustomize as sonde

#: Le nom sous lequel la carte s'invoque — dérivé, jamais recopié (cf. `banc.MODULE`).
MODULE = __spec__.name if __spec__ else "maestro.scenarios.carte"

#: Le dossier des relevés d'un passage, **relatif à la copie** (#234) : la sonde de
#: chaque processus y écrit, le banc y borne. Remis à neuf par `--preparer`.
DOSSIER_CARTE = Path(".maestro") / "carte"

#: La carte d'un passage, à côté de son rapport.
FICHIER_CARTE = "couverture.json"

#: La forme du fichier. Un lecteur qui en verrait une autre la saurait d'un autre âge.
VERSION = 1
GRANULARITE = "fonction"

#: Ce que le banc attend à chaque borne : plusieurs périodes de la sonde, pour qu'un
#: processus occupé l'ait relue avant que le scénario suivant ne commence.
ATTENTE_BORNE_S = 5 * sonde.PERIODE_S

#: Le dossier que `--preparer` pose sur `PYTHONPATH` : celui du `sitecustomize`.
DOSSIER_SONDE = Path(sonde.__file__).resolve().parent

#: Ce que Git doit rendre avant qu'on renonce au filtre des fichiers suivis.
DELAI_GIT_S = 30.0

CODE_OK = 0
CODE_ECHEC = 1
CODE_USAGE = 2

_USAGE = (
    f"Usage : python -m {MODULE} --preparer\n"
    f"        python -m {MODULE} [<{FICHIER_CARTE}>]"
)


# ── Préparer : la sonde, posée sur la stack d'un passage ─────────────────────


def preparer(
    racine: Path | None = None, environnement: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Remet à neuf le dossier des relevés et rend l'environnement qui pose la sonde.

    Deux variables : le dossier (`MAESTRO_CARTE_DOSSIER`), et `PYTHONPATH` avec le
    dossier de la sonde en tête — l'existant gardé derrière. Les chemins sont
    absolus et dans la forme de l'OS : un processus lancé ailleurs que dans la copie
    (l'hôte détaché, un run) doit les lire tels quels. Lève `OSError` si le dossier
    d'un passage précédent résiste.
    """
    env = os.environ if environnement is None else environnement
    dossier = ((racine or racine_de_la_copie()) / DOSSIER_CARTE).resolve()
    if not retirer_arbre(dossier):
        raise OSError(f"le dossier de la carte résiste : {dossier}")
    (dossier / sonde.DOSSIER_TRACES).mkdir(parents=True)
    existant = [
        entree
        for entree in (env.get("PYTHONPATH") or "").split(os.pathsep)
        if entree and Path(entree) != DOSSIER_SONDE
    ]
    return {
        sonde.VARIABLE_DOSSIER: str(dossier),
        "PYTHONPATH": os.pathsep.join([str(DOSSIER_SONDE), *existant]),
    }


# ── Borner et écrire : le banc ────────────────────────────────────────────────


@dataclass(frozen=True)
class Processus:
    """Un processus qui a contribué à une empreinte."""

    pid: int
    commande: str

    def to_dict(self) -> dict[str, Any]:
        return {"pid": self.pid, "commande": self.commande}


@dataclass(frozen=True)
class Empreinte:
    """Ce qu'un scénario (ou le démarrage) a exécuté : fonctions par fichier, fichiers lus."""

    fonctions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    fichiers: tuple[str, ...] = ()
    processus: tuple[Processus, ...] = ()

    @property
    def nombre_de_fonctions(self) -> int:
        return sum(len(noms) for noms in self.fonctions.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "processus": [p.to_dict() for p in self.processus],
            "fonctions": {fichier: list(noms) for fichier, noms in self.fonctions.items()},
            "fichiers": list(self.fichiers),
        }

    @classmethod
    def depuis(cls, charge: Mapping[str, Any]) -> Empreinte:
        return cls(
            fonctions={
                str(fichier): tuple(str(nom) for nom in noms)
                for fichier, noms in charge["fonctions"].items()
            },
            fichiers=tuple(str(f) for f in charge["fichiers"]),
            processus=tuple(
                Processus(pid=int(p["pid"]), commande=str(p["commande"]))
                for p in charge.get("processus", ())
            ),
        )


@dataclass(frozen=True)
class CarteDuPassage:
    """La carte d'un passage : le démarrage, puis chaque scénario joué."""

    passage: str
    copie: str
    demarrage: Empreinte
    scenarios: Mapping[str, Empreinte]
    #: Les fichiers ont-ils été filtrés par Git ? Faux : Git a manqué, et seuls les
    #: dossiers cachés ont été écartés — c'est dit, pas tu.
    filtre_git: bool = True

    @property
    def stack_mesuree(self) -> bool:
        """Un processus de la stack — pas seulement le banc — a-t-il été mesuré ?"""
        return any(
            p.commande and not _est_le_banc(p.commande)
            for empreinte in (self.demarrage, *self.scenarios.values())
            for p in empreinte.processus
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "passage": self.passage,
            "granularite": GRANULARITE,
            "copie": self.copie,
            "filtre": "git" if self.filtre_git else "dossiers cachés écartés (Git illisible)",
            "demarrage": self.demarrage.to_dict(),
            "scenarios": {ident: e.to_dict() for ident, e in self.scenarios.items()},
        }


class Carte:
    """La carte d'un passage en cours : le banc la borne, puis l'écrit."""

    def __init__(
        self,
        dossier: Path,
        *,
        dormir: Callable[[float], None] = time.sleep,
        attente_s: float = ATTENTE_BORNE_S,
    ) -> None:
        self.dossier = dossier
        self._dormir = dormir
        self._attente_s = attente_s

    @classmethod
    def depuis_environnement(cls, environnement: Mapping[str, str] | None = None) -> Carte | None:
        """La carte que `--preparer` a posée sur ce processus, ou `None` (aucune mesure)."""
        env = os.environ if environnement is None else environnement
        dossier = (env.get(sonde.VARIABLE_DOSSIER) or "").strip()
        return cls(Path(dossier)) if dossier else None

    def borner(self, segment: str) -> None:
        """Ce qui s'exécute désormais se range sous `segment` — puis le temps que chacun le voie."""
        self.dossier.mkdir(parents=True, exist_ok=True)
        (self.dossier / sonde.FICHIER_SEGMENT).write_text(segment, encoding="utf-8")
        self._dormir(self._attente_s)

    def finir(self) -> None:
        """La dernière borne : chaque processus écrit ce qui lui reste, et la sonde s'éteint."""
        self.borner(sonde.SEGMENT_FIN)

    def ecrire(
        self,
        rapport: Rapport,
        dossier_rapport: Path,
        *,
        racine: Path | None = None,
        suivis: Mapping[str, str] | None = None,
        miroirs: Mapping[str, str] | None = None,
    ) -> tuple[Path, CarteDuPassage]:
        """Assemble les relevés en `couverture.json` dans `dossier_rapport`. Lève `OSError`.

        `suivis` (clé → chemin suivi, cf. `fichiers_suivis`) et `miroirs` (copie du
        banc → source, cf. `miroirs_du_banc`) sont lus sur la copie sans consigne ;
        injectables pour les tests.
        """
        racine = (racine or racine_de_la_copie()).resolve()
        if suivis is None:
            suivis = fichiers_suivis(racine)
        carte = assembler(
            self.dossier / sonde.DOSSIER_TRACES,
            passage=rapport.horodatage,
            copie=racine,
            scenarios=[r.identifiant for r in rapport.resultats],
            suivis=suivis,
            miroirs=miroirs_du_banc(racine) if miroirs is None else miroirs,
        )
        chemin = dossier_rapport / FICHIER_CARTE
        chemin.write_text(
            json.dumps(carte.to_dict(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        return chemin, carte


# ── Assembler ─────────────────────────────────────────────────────────────────


def assembler(
    traces: Path,
    *,
    passage: str,
    copie: Path,
    scenarios: Sequence[str],
    suivis: Mapping[str, str] | None,
    miroirs: Mapping[str, str],
) -> CarteDuPassage:
    """La carte des relevés de `traces` : un ensemble par scénario joué, et le démarrage.

    `suivis` à `None` : Git a manqué, seuls les dossiers cachés sont écartés. Un
    scénario joué sans relevé a une empreinte vide — il figure, et le vide se voit.
    Un relevé illisible (une ligne tronquée par un processus tué) est sauté.
    """
    fonctions: dict[str, dict[str, set[str]]] = {}
    fichiers: dict[str, set[str]] = {}
    processus: dict[str, dict[int, str]] = {}
    for releve in sorted(traces.glob(f"*{sonde.SUFFIXE_TRACE}")):
        try:
            texte = releve.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ligne in texte.splitlines():
            champs = ligne.split(sonde.SEPARATEUR)
            if len(champs) < 3:
                continue
            segment, sorte = champs[0], champs[1]
            if sorte == sonde.SORTE_PROCESSUS and len(champs) == 4 and champs[2].isdigit():
                processus.setdefault(segment, {})[int(champs[2])] = champs[3]
            elif sorte == sonde.SORTE_FONCTION and len(champs) == 4:
                fichier = _retenu(champs[2], suivis, {})
                if fichier is not None:
                    fonctions.setdefault(segment, {}).setdefault(fichier, set()).add(champs[3])
            elif sorte == sonde.SORTE_FICHIER and len(champs) == 3:
                fichier = _retenu(champs[2], suivis, miroirs)
                if fichier is not None and not fichier.endswith(sonde.SUFFIXES_CODE):
                    fichiers.setdefault(segment, set()).add(fichier)

    def empreinte(segment: str) -> Empreinte:
        return Empreinte(
            fonctions={
                fichier: tuple(sorted(noms))
                for fichier, noms in sorted(fonctions.get(segment, {}).items())
            },
            fichiers=tuple(sorted(fichiers.get(segment, ()))),
            processus=tuple(
                Processus(pid=pid, commande=commande)
                for pid, commande in sorted(processus.get(segment, {}).items())
            ),
        )

    return CarteDuPassage(
        passage=passage,
        copie=str(copie),
        demarrage=empreinte(sonde.SEGMENT_DEMARRAGE),
        scenarios={ident: empreinte(ident) for ident in dict.fromkeys(scenarios)},
        filtre_git=suivis is not None,
    )


def _retenu(
    relatif: str, suivis: Mapping[str, str] | None, miroirs: Mapping[str, str]
) -> str | None:
    """Le chemin suivi qu'un relevé désigne, ou `None` s'il ne compte pas."""
    for copie_du_banc, source in miroirs.items():
        if relatif.startswith(copie_du_banc + "/"):
            relatif = source + relatif[len(copie_du_banc) :]
            break
    if suivis is None:
        return None if any(p.startswith(".") for p in relatif.split("/")) else relatif
    return suivis.get(_cle(relatif))


def _cle(relatif: str) -> str:
    """La clé d'un chemin : sans casse sous Windows, où le disque l'ignore."""
    return relatif.casefold() if os.name == "nt" else relatif


def fichiers_suivis(racine: Path) -> dict[str, str] | None:
    """Les fichiers que Git suit dans `racine` (clé → chemin), ou `None` si Git manque."""
    try:
        fini = subprocess.run(  # noqa: S603 - git, argv construit ici
            ["git", "-C", str(racine), "ls-files", "-z"],
            capture_output=True,
            timeout=DELAI_GIT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if fini.returncode != 0:
        return None
    chemins = fini.stdout.decode("utf-8", errors="replace").split("\0")
    return {_cle(chemin): chemin for chemin in chemins if chemin}


def miroirs_du_banc(racine: Path) -> dict[str, str]:
    """Copie du banc → source, pour la configuration que le banc sert de sa copie (#1164).

    Le banc remet à neuf ses dépôts de configuration en recopiant ceux de la copie
    (`maestro.scenarios.etat.vider`) : l'API du banc lit `.maestro/banc/agents/x.md`
    là où le produit lirait `core/agents/x.md`. Seuls les dépôts de configuration
    (pas l'état d'exécution), et seulement ceux qui vivent sous la copie.
    """
    # Paresseux : `--preparer` est joué par `start.sh` avant l'API, sans ces dépôts.
    from maestro.controltower.donnees import DEPOTS, donnees_de_la_stack, donnees_du_banc

    banc = donnees_du_banc(racine)
    copie = donnees_de_la_stack()
    rendu: dict[str, str] = {}
    for depot in DEPOTS:
        if depot.etat:
            continue
        de = _sous(banc.racines[depot.nom], racine)
        vers = _sous(copie.racines[depot.nom], racine)
        if de and vers:
            rendu[de] = vers
    return rendu


def _sous(chemin: Path, racine: Path) -> str | None:
    try:
        return chemin.resolve().relative_to(racine).as_posix()
    except ValueError:
        return None


#: Le module du banc (`python -m maestro.scenarios`), dérivé comme `banc.MODULE` : ce
#: module-ci ne l'importe pas, le banc l'importe.
_MODULE_DU_BANC = __package__ or "maestro.scenarios"


def _est_le_banc(commande: str) -> bool:
    """Le processus du banc lui-même — il borne, il ne fait pas partie de la stack."""
    mots = commande.split()
    return len(mots) >= 2 and mots[0] == "-m" and mots[1] == _MODULE_DU_BANC


# ── Lire ──────────────────────────────────────────────────────────────────────


def charger(chemin: Path) -> CarteDuPassage:
    """Relit une carte écrite. Lève `OSError`, ou `ValueError` sur une autre forme."""
    charge = json.loads(chemin.read_text(encoding="utf-8"))
    if not isinstance(charge, dict) or charge.get("version") != VERSION:
        raise ValueError(f"carte d'une autre forme que la version {VERSION} : {chemin}")
    try:
        return CarteDuPassage(
            passage=str(charge["passage"]),
            copie=str(charge["copie"]),
            demarrage=Empreinte.depuis(charge["demarrage"]),
            scenarios={
                str(ident): Empreinte.depuis(e) for ident, e in charge["scenarios"].items()
            },
            filtre_git=charge.get("filtre") == "git",
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"carte illisible : {chemin} ({exc})") from exc


def en_lignes(carte: CarteDuPassage) -> list[str]:
    """La carte en mots : par scénario, fonctions, fichiers de données et processus."""
    lignes = []
    if not carte.stack_mesuree:
        lignes.append(
            "  ⚠ stack non mesurée : aucun processus de la stack n'a chargé la sonde — "
            "un passage la pose par : bash scripts/controltower/start.sh --etat-banc --rejouer"
        )
    if not carte.filtre_git:
        lignes.append(
            "  ⚠ Git illisible : les fichiers ne sont pas filtrés par ce que la copie suit"
        )
    for nom, empreinte in (("démarrage", carte.demarrage), *carte.scenarios.items()):
        lignes.append(
            f"  {nom}  {empreinte.nombre_de_fonctions} fonction(s) dans "
            f"{len(empreinte.fonctions)} fichier(s) · {len(empreinte.fichiers)} fichier(s) "
            f"de données lu(s) · {len(empreinte.processus)} processus"
        )
    return lignes


def main(
    argv: Sequence[str] | None = None,
    *,
    racine: Path | None = None,
    sortie: TextIO | None = None,
    erreur: TextIO | None = None,
) -> int:
    """`--preparer` (lu par `start.sh`), ou la lecture d'une carte. `0`, `1` échec, `2` usage."""
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--preparer"]:
        try:
            variables = preparer(racine)
        except OSError as exc:
            print(f"Carte non préparée : {exc}", file=erreur)
            return CODE_ECHEC
        # Une variable par ligne, `NOM=valeur` : `start.sh` les exporte telles quelles.
        sortie.write("".join(f"{nom}={valeur}\n" for nom, valeur in variables.items()))
        return CODE_OK
    if len(args) > 1 or any(a.startswith("--") for a in args):
        print(_USAGE, file=erreur)
        return CODE_USAGE
    chemin = Path(args[0]) if args else _derniere_carte(racine or Path.cwd())
    if chemin is None:
        print(f"Aucune carte sous {RACINE_RAPPORTS} : un passage du banc l'écrit.", file=erreur)
        return CODE_ECHEC
    try:
        carte = charger(chemin)
    except (OSError, ValueError) as exc:
        print(f"Carte illisible : {exc}", file=erreur)
        return CODE_ECHEC
    print(f"Carte du passage {carte.passage} — {chemin}", file=sortie)
    for ligne in en_lignes(carte):
        print(ligne, file=sortie)
    return CODE_OK


def _derniere_carte(racine: Path) -> Path | None:
    """La carte du dernier passage parmi les rapports de la copie."""
    cartes: Iterable[Path] = (racine / RACINE_RAPPORTS).glob(f"*/{FICHIER_CARTE}")
    return max(cartes, key=lambda c: c.parent.name, default=None)


if __name__ == "__main__":
    raise SystemExit(main())
