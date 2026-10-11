"""L'historique du banc : chaque passage y laisse une ligne, et la série se lit (#1461).

    .venv/Scripts/python.exe -m maestro.scenarios.historique [--fenetre <n>] [--json]
    .venv/Scripts/python.exe -m maestro.scenarios.historique --importer [<dossier>]

Un passage écrit son rapport sous `.maestro/scenarios/<passage>/`, **relatif à la
copie qui le joue** (#234). Ce rapport ne dit ni le code qu'il a joué, ni la copie,
ni le ticket, et ceux d'un worktree partent avec lui quand on le ramasse. La série
des passages, celle qui dit si le produit s'améliore et quels scénarios sont
fragiles, n'existait donc nulle part : sur les treize passages du clone principal,
S4 était vert à 100 %, S9 à 40 %, et personne ne le lisait.

## Une ligne par passage, sous `~/.maestro/`

`consigner` ajoute au fichier `~/.maestro/historique/scenarios.jsonl` (#1454 : la
racine de l'état du poste, jamais ramassée) **une ligne JSON par passage** : son
identifiant, le **sha** de `HEAD` de la copie, sa branche et l'iid qu'elle porte
(`<type>/<iid>-<slug>`, la règle de `gl_branch_iid`), la copie elle-même, si son
arbre portait des modifications non commitées, puis par scénario son verdict, son
coût, sa durée, son rejeu et son empêchement. La ligne survit au worktree : elle
n'est pas dans la copie.

Trois choix, chacun avec sa raison :

- **le point d'entrée du banc l'écrit** (`maestro.scenarios.banc.main`), juste
  après le rapport. Clone principal, worktree ou pilote passent tous par
  `python -m maestro.scenarios` : aucun n'a rien à faire pour que son passage y
  entre ;
- **la copie est celle du code qui tourne** (`maestro.espace.racine_de_la_copie`),
  la même que celle des dépôts de `core/` et de l'espace Redis (#1164). Le sha est
  lu par Git, au mieux : une copie sans Git, ou un Git absent, écrit sa ligne
  quand même, sha inconnu et dit tel. Un sha dont l'arbre était modifié n'est pas
  ce sha : `modifiee` le dit, pour qu'un « dernier vert » ne soit jamais attribué
  à un commit qui ne l'a pas joué ;
- **une ligne s'ajoute d'un seul `write`, en mode ajout** : deux passages qui
  finissent ensemble n'écrasent rien, et une ligne tronquée par un processus tué
  est écartée à la lecture, en le comptant, jamais en faisant tomber la série.

Un historique qui ne s'écrit pas se dit et ne change pas le verdict : le rapport
est déjà écrit, et c'est lui qui fait foi pour le passage.

## La lecture

`series` rend, par scénario, sur ses `fenetre` derniers passages (tous, sans
consigne) : le taux de réussite, le coût et la durée **médians**, les rejeux et
les empêchements, et le **dernier vert** avec son sha. C'est l'entrée du banc par
lot (lot 2 de #1460, le dernier vert sur `main`) et du choix du scénario le moins
cher (lot 4). `--json` la rend à un script.

## Les passages d'avant

`--importer` fait entrer les `rapport.json` d'un dossier de rapports (celui de la
copie, `.maestro/scenarios`, sans argument). Ils entrent **sans sha** : on ne
sait pas quel code ils ont joué, et la lecture le dit (« sha inconnu ») plutôt
que de le deviner d'une date. Rejouer l'import n'ajoute rien de ce qui y est déjà.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from maestro.emplacements import racine_etat
from maestro.espace import racine_de_la_copie
from maestro.scenarios.modele import VERDICT_VERT, Rapport
from maestro.scenarios.rapport import (
    FICHIER_JSON,
    RACINE_RAPPORTS,
    cout_en_mots,
    duree_en_mots,
)

#: Le nom sous lequel la lecture s'invoque — dérivé, jamais recopié (cf. `banc.MODULE`).
MODULE = __spec__.name if __spec__ else "maestro.scenarios.historique"

#: La variable qui déplace le fichier — pour la suite (`tests/conftest.py`), ou qui
#: veut tenir un historique à part.
VARIABLE_HISTORIQUE = "MAESTRO_SCENARIOS_HISTORIQUE"

#: Sous `~/.maestro/` : un dossier à part, **hors des ateliers** que la rétention de
#: #1457 ramasse passage par passage — elle retire des projets, jamais la série.
DOSSIER_HISTORIQUE = "historique"
FICHIER_HISTORIQUE = "scenarios.jsonl"

#: La forme d'une ligne. Un lecteur qui en verrait une autre la saurait d'un autre âge.
VERSION = 1

#: D'où vient une ligne : écrite à la fin d'un passage, ou importée d'un rapport.
SOURCE_PASSAGE = "passage"
SOURCE_RAPPORT = "rapport"

#: La branche d'un ticket, `<type>/<iid>-<slug>` (docs/10 §2) — le slug toléré absent,
#: comme dans `gl_branch_iid` : c'est l'iid qui porte l'information.
_BRANCHE_DE_TICKET = re.compile(r"^[a-z]+/(\d+)(?:-.*)?$")

#: Ce qu'une lecture Git peut prendre avant qu'on renonce au sha.
DELAI_GIT_S = 30.0

#: Le sha tel que la lecture l'affiche.
SHA_COURT = 7

CODE_OK = 0
CODE_USAGE = 2
CODE_ILLISIBLE = 3

_USAGE = (
    f"Usage : python -m {MODULE} [--fenetre <n>] [--json]\n"
    f"        python -m {MODULE} --importer [<dossier de rapports>]"
)


def chemin_historique(environnement: Mapping[str, str] | None = None) -> Path:
    """Le fichier de l'historique — `MAESTRO_SCENARIOS_HISTORIQUE`, sinon sous `~/.maestro/`.

    Non créé. Lève `RuntimeError`/`OSError` quand l'OS ne sait pas dire où est le
    dossier personnel, comme `racine_etat` : à l'appelant de le dire.
    """
    env = os.environ if environnement is None else environnement
    regle = (env.get(VARIABLE_HISTORIQUE) or "").strip()
    if regle:
        return Path(regle).expanduser()
    return racine_etat() / DOSSIER_HISTORIQUE / FICHIER_HISTORIQUE


# ── La copie qui joue ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Copie:
    """La copie de travail d'un passage : où, sur quel commit, pour quel ticket."""

    racine: str
    sha: str | None = None
    branche: str | None = None
    #: L'arbre portait-il des modifications non commitées de fichiers suivis ?
    #: `None` : on ne sait pas.
    modifiee: bool | None = None

    @property
    def iid(self) -> int | None:
        """Le ticket que la branche porte, s'il y en a un."""
        trouve = _BRANCHE_DE_TICKET.match(self.branche or "")
        return int(trouve.group(1)) if trouve else None


def copie_de_travail(racine: Path | None = None) -> Copie:
    """Lit la copie `racine` (celle du code qui tourne, sans consigne) — au mieux.

    Chaque lecture qui échoue laisse son champ vide, jamais le passage sans ligne :
    une copie sans Git est une copie dont on ne sait pas le sha, rien de plus.
    """
    racine = (racine or racine_de_la_copie()).resolve()
    sha = _lire_git(racine, "rev-parse", "--verify", "--quiet", "HEAD")
    branche = _lire_git(racine, "symbolic-ref", "--short", "--quiet", "HEAD")
    etat = _lire_git(racine, "status", "--porcelain", "--untracked-files=no", vide_admis=True)
    return Copie(
        racine=str(racine),
        sha=sha or None,
        branche=branche or None,
        modifiee=None if etat is None or sha is None else bool(etat),
    )


def _lire_git(racine: Path, *arguments: str, vide_admis: bool = False) -> str | None:
    """La sortie d'une lecture Git, ou `None` si Git manque, échoue ou ne dit rien."""
    try:
        resultat = subprocess.run(  # noqa: S603 - git, argv construit ici
            ["git", "-C", str(racine), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DELAI_GIT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if resultat.returncode != 0:
        return None
    sortie = resultat.stdout.strip()
    return sortie if sortie or vide_admis else None


# ── L'écriture ────────────────────────────────────────────────────────────────


def ligne_du_passage(
    rapport: Rapport, copie: Copie, *, source: str = SOURCE_PASSAGE
) -> dict[str, Any]:
    """La ligne d'historique d'un passage — sa forme est `VERSION`."""
    return _ligne(
        passage=rapport.horodatage,
        source=source,
        copie=copie,
        scenarios=[
            {
                "id": r.identifiant,
                "verdict": r.verdict,
                "cout_usd": r.cout_usd,
                "duree_s": round(r.duree_s, 3),
                "rejoue": r.rejoue,
                "empechement": r.empechement,
                "run_id": r.run_id,
            }
            for r in rapport.resultats
        ],
    )


def _ligne(
    *, passage: str, source: str, copie: Copie, scenarios: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "version": VERSION,
        "source": source,
        "passage": passage,
        "sha": copie.sha,
        "branche": copie.branche,
        "iid": copie.iid,
        "copie": copie.racine,
        "modifiee": copie.modifiee,
        "scenarios": scenarios,
    }


def consigner(rapport: Rapport, copie: Copie, *, chemin: Path | None = None) -> Path:
    """Ajoute la ligne du passage à l'historique et rend le fichier. Lève `OSError`."""
    fichier = chemin or chemin_historique()
    _ajouter(fichier, [ligne_du_passage(rapport, copie)])
    return fichier


def _ajouter(fichier: Path, lignes: Iterable[dict[str, Any]]) -> None:
    """Ajoute des lignes, chacune d'un seul `write` (voir l'en-tête)."""
    fichier.parent.mkdir(parents=True, exist_ok=True)
    with fichier.open("a", encoding="utf-8", newline="\n") as flux:
        for ligne in lignes:
            flux.write(json.dumps(ligne, ensure_ascii=False, separators=(",", ":")) + "\n")


# ── La lecture ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Lecture:
    """Ce que le fichier contient : ses lignes lisibles, et combien ne l'étaient pas."""

    lignes: tuple[dict[str, Any], ...]
    illisibles: int = 0


def lire(chemin: Path | None = None) -> Lecture:
    """Les lignes de l'historique, dans l'ordre des passages. Un fichier absent est vide.

    Une ligne qui ne se lit pas (tronquée par un processus tué, ou d'une autre
    forme) est écartée et **comptée** : la série reste lisible, et l'écart se voit.
    """
    fichier = chemin or chemin_historique()
    try:
        texte = fichier.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Lecture(lignes=())
    lignes: list[dict[str, Any]] = []
    illisibles = 0
    for brute in texte.splitlines():
        if not brute.strip():
            continue
        try:
            ligne = json.loads(brute)
        except ValueError:
            illisibles += 1
            continue
        if not _recevable(ligne):
            illisibles += 1
            continue
        lignes.append(ligne)
    lignes.sort(key=lambda ligne: str(ligne["passage"]))
    return Lecture(lignes=tuple(lignes), illisibles=illisibles)


def _recevable(ligne: Any) -> bool:
    """Une ligne de la forme `VERSION`, avec un passage et ses scénarios."""
    return (
        isinstance(ligne, dict)
        and ligne.get("version") == VERSION
        and isinstance(ligne.get("passage"), str)
        and isinstance(ligne.get("scenarios"), list)
        and all(isinstance(s, dict) and isinstance(s.get("id"), str) for s in ligne["scenarios"])
    )


@dataclass(frozen=True)
class DernierVert:
    """Le dernier passage où un scénario a été vert, et le code qu'il a joué."""

    passage: str
    sha: str | None
    branche: str | None
    modifiee: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passage": self.passage,
            "sha": self.sha,
            "branche": self.branche,
            "modifiee": self.modifiee,
        }


@dataclass(frozen=True)
class Serie:
    """La série d'un scénario sur la fenêtre lue."""

    identifiant: str
    passages: int
    verts: int
    rejoues: int
    empechements: int
    cout_median_usd: float | None
    duree_mediane_s: float
    dernier_vert: DernierVert | None

    @property
    def taux(self) -> float:
        """La part des passages où le scénario a fini vert (rejeu compris)."""
        return self.verts / self.passages if self.passages else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.identifiant,
            "passages": self.passages,
            "verts": self.verts,
            "taux": round(self.taux, 4),
            "rejoues": self.rejoues,
            "empechements": self.empechements,
            "cout_median_usd": self.cout_median_usd,
            "duree_mediane_s": round(self.duree_mediane_s, 3),
            "dernier_vert": self.dernier_vert.to_dict() if self.dernier_vert else None,
        }


def series(lignes: Sequence[dict[str, Any]], *, fenetre: int | None = None) -> tuple[Serie, ...]:
    """La série de chaque scénario, sur ses `fenetre` derniers passages (tous sans consigne).

    La fenêtre se compte **par scénario** : S12, joué rarement, garde ses passages
    même quand S1 en a dix fois plus. Le dernier vert, lui, se cherche dans tout
    l'historique — c'est la question « depuis quand ? », que la fenêtre ne borne pas.
    """
    passages: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for ligne in sorted(lignes, key=lambda ligne: str(ligne["passage"])):
        for scenario in ligne["scenarios"]:
            passages.setdefault(scenario["id"], []).append((ligne, scenario))
    rendu = []
    for identifiant in sorted(passages, key=_ordre_naturel):
        tous = passages[identifiant]
        vus = tous[-fenetre:] if fenetre else tous
        couts = [s["cout_usd"] for _l, s in vus if isinstance(s.get("cout_usd"), int | float)]
        verts = [(ligne, s) for ligne, s in tous if s.get("verdict") == VERDICT_VERT]
        rendu.append(
            Serie(
                identifiant=identifiant,
                passages=len(vus),
                verts=sum(1 for _l, s in vus if s.get("verdict") == VERDICT_VERT),
                rejoues=sum(1 for _l, s in vus if s.get("rejoue")),
                empechements=sum(1 for _l, s in vus if s.get("empechement")),
                cout_median_usd=statistics.median(couts) if couts else None,
                duree_mediane_s=statistics.median(float(s.get("duree_s") or 0) for _l, s in vus),
                dernier_vert=_dernier_vert(verts[-1][0]) if verts else None,
            )
        )
    return tuple(rendu)


def _dernier_vert(ligne: dict[str, Any]) -> DernierVert:
    return DernierVert(
        passage=ligne["passage"],
        sha=ligne.get("sha"),
        branche=ligne.get("branche"),
        modifiee=ligne.get("modifiee"),
    )


def _ordre_naturel(identifiant: str) -> tuple[str, int, str]:
    """S2 avant S10 : le préfixe, puis le numéro comme un nombre."""
    trouve = re.match(r"^(\D*)(\d+)(.*)$", identifiant)
    if not trouve:
        return (identifiant, 0, "")
    return (trouve.group(1), int(trouve.group(2)), trouve.group(3))


def en_texte(lecture: Lecture, chemin: Path, *, fenetre: int | None = None) -> str:
    """La série en mots : un tableau par scénario, le fichier et ce qui a été écarté."""
    portee = f" · fenêtre : {fenetre} derniers passages par scénario" if fenetre else ""
    lignes = [
        f"Historique du banc — {chemin} · {len(lecture.lignes)} passage(s){portee}",
    ]
    if lecture.illisibles:
        lignes.append(f"⚠ {lecture.illisibles} ligne(s) illisible(s) écartée(s)")
    if not lecture.lignes:
        lignes.append(
            "Aucun passage consigné. Un passage du banc y écrit sa ligne "
            f"(python -m {MODULE.rpartition('.')[0]}) ; les rapports d'avant y entrent "
            f"par : python -m {MODULE} --importer"
        )
        return "\n".join(lignes) + "\n"
    lignes += [
        "",
        "| | Passages | Réussite | Coût médian | Durée médiane | Rejoués | Empêchés "
        "| Dernier vert |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for serie in series(lecture.lignes, fenetre=fenetre):
        lignes.append(
            f"| {serie.identifiant} | {serie.passages} | {serie.taux:.0%} "
            f"({serie.verts}/{serie.passages}) | {cout_en_mots(serie.cout_median_usd)} | "
            f"{duree_en_mots(serie.duree_mediane_s)} | {serie.rejoues} | "
            f"{serie.empechements} | {_dernier_vert_en_mots(serie.dernier_vert)} |"
        )
    return "\n".join(lignes) + "\n"


def _dernier_vert_en_mots(dernier: DernierVert | None) -> str:
    if dernier is None:
        return "jamais"
    if not dernier.sha:
        return f"{dernier.passage} (sha inconnu)"
    sha = dernier.sha[:SHA_COURT]
    if dernier.modifiee:
        sha += ", arbre modifié"
    branche = f" sur {dernier.branche}" if dernier.branche else ""
    return f"{dernier.passage} ({sha}{branche})"


# ── L'import des passages d'avant ─────────────────────────────────────────────


def importer(dossier: Path, *, chemin: Path | None = None) -> tuple[int, int]:
    """Fait entrer les `rapport.json` de `dossier` — rend (importés, déjà présents).

    Sans sha ni branche : on ne sait pas ce qu'ils ont joué. La copie, elle, se
    lit sur le dossier quand il est le `.maestro/scenarios` d'une copie. Un
    passage déjà présent pour la même copie n'entre pas deux fois. Un rapport
    illisible lève `ValueError`, qui le nomme : un import partiel tairait un trou.
    """
    fichier = chemin or chemin_historique()
    dossier = dossier.resolve()
    copie = Copie(racine=str(_copie_du_dossier(dossier)))
    deja = {(ligne["passage"], ligne.get("copie")) for ligne in lire(fichier).lignes}
    nouvelles: list[dict[str, Any]] = []
    presents = 0
    for rapport in sorted(dossier.glob(f"*/{FICHIER_JSON}")):
        try:
            charge = json.loads(rapport.read_text(encoding="utf-8"))
            passage = str(charge.get("horodatage") or rapport.parent.name)
            scenarios = [_scenario_du_rapport(s) for s in charge["scenarios"]]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"rapport illisible : {rapport} ({exc})") from exc
        if (passage, copie.racine) in deja:
            presents += 1
            continue
        deja.add((passage, copie.racine))
        nouvelles.append(
            _ligne(passage=passage, source=SOURCE_RAPPORT, copie=copie, scenarios=scenarios)
        )
    if nouvelles:
        _ajouter(fichier, nouvelles)
    return len(nouvelles), presents


def _scenario_du_rapport(scenario: dict[str, Any]) -> dict[str, Any]:
    """Un scénario d'un `rapport.json`, dans la forme d'une ligne."""
    return {
        "id": str(scenario["id"]),
        "verdict": str(scenario["verdict"]),
        "cout_usd": scenario.get("cout_usd"),
        "duree_s": float(scenario["duree_s"]),
        "rejoue": bool(scenario.get("rejoue", False)),
        "empechement": bool(scenario.get("empechement", False)),
        "run_id": str(scenario.get("run_id") or ""),
    }


def _copie_du_dossier(dossier: Path) -> Path:
    """La copie dont `dossier` est le `.maestro/scenarios` — sinon le dossier lui-même."""
    if dossier.parts[-len(RACINE_RAPPORTS.parts) :] == RACINE_RAPPORTS.parts:
        return dossier.parents[len(RACINE_RAPPORTS.parts) - 1]
    return dossier


# ── La ligne de commande ──────────────────────────────────────────────────────


def main(
    argv: Sequence[str] | None = None,
    *,
    chemin: Path | None = None,
    sortie: TextIO | None = None,
    erreur: TextIO | None = None,
) -> int:
    """Lit la série (ou importe les rapports d'avant). Codes : `0`, `2` usage, `3` illisible."""
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        options = _options(args)
        fichier = chemin or chemin_historique()
    except ValueError as refus:
        print(f"{_USAGE}\n  {refus}", file=erreur)
        return CODE_USAGE
    except (RuntimeError, OSError) as exc:
        print(
            f"Historique introuvable : dossier personnel illisible ({exc}) — "
            f"le nommer par {VARIABLE_HISTORIQUE}.",
            file=erreur,
        )
        return CODE_ILLISIBLE

    if options.importer is not None:
        try:
            importes, presents = importer(options.importer, chemin=fichier)
        except (OSError, ValueError) as exc:
            print(f"Import refusé : {exc}", file=erreur)
            return CODE_ILLISIBLE
        print(
            f"{importes} passage(s) importé(s) de {options.importer.resolve()} dans {fichier}, "
            f"sans sha (inconnu) ; {presents} déjà présent(s).",
            file=sortie,
        )
        return CODE_OK

    try:
        lecture = lire(fichier)
    except OSError as exc:
        print(f"Historique illisible ({fichier}) : {exc}", file=erreur)
        return CODE_ILLISIBLE
    if options.json:
        charge = {
            "historique": str(fichier),
            "passages": len(lecture.lignes),
            "illisibles": lecture.illisibles,
            "fenetre": options.fenetre,
            "scenarios": [s.to_dict() for s in series(lecture.lignes, fenetre=options.fenetre)],
        }
        print(json.dumps(charge, ensure_ascii=False, indent=2), file=sortie)
    else:
        print(en_texte(lecture, fichier, fenetre=options.fenetre), end="", file=sortie)
    return CODE_OK


@dataclass
class _Options:
    fenetre: int | None = None
    json: bool = False
    importer: Path | None = None


def _options(args: Sequence[str]) -> _Options:
    """Lit la ligne de commande — lève `ValueError` avec son motif sur tout écart."""
    options = _Options()
    reste = list(args)
    while reste:
        arg = reste.pop(0)
        if arg == "--json":
            options.json = True
        elif arg == "--importer":
            options.importer = (
                Path(reste.pop(0)) if reste and not reste[0].startswith("--") else RACINE_RAPPORTS
            )
        elif arg == "--fenetre" or arg.startswith("--fenetre="):
            brut = arg.partition("=")[2] if "=" in arg else (reste.pop(0) if reste else "")
            options.fenetre = _fenetre(brut)
        else:
            raise ValueError(f"argument inconnu : {arg}")
    if options.importer is not None and (options.json or options.fenetre):
        raise ValueError("--importer ne se combine ni avec --json ni avec --fenetre")
    return options


def _fenetre(brut: str) -> int:
    try:
        valeur = int(brut)
    except ValueError as exc:
        raise ValueError(f"--fenetre attend un nombre de passages (reçu : {brut!r})") from exc
    if valeur <= 0:
        raise ValueError(f"--fenetre doit être > 0 (reçu : {brut!r})")
    return valeur


if __name__ == "__main__":
    raise SystemExit(main())
