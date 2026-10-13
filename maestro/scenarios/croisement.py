"""Le banc dû au ticket : les scénarios dont la carte croise le diff (#1464).

    .venv/Scripts/python.exe -m maestro.scenarios.croisement --base <sha>
                                                             [--depot <dossier>] [--court]

Au ticket, le banc se déclenchait sur un **préfixe** (`GL_BANC_CHEMINS` de
`scripts/gitlab/lib.sh`, `maestro/ core/ packages/shared/`) : tout ticket du produit
était « sur le chemin », et la session choisissait son scénario au jugé, large par
prudence — S12, à ≈ 26 min et ≈ 3 $, a été joué six fois en un jour
([docs/47](../../docs/47-decision-le-banc-se-paie-une-fois-par-lot.md)). Ce module
remplace le préfixe par la **carte** de ce que chaque scénario exécute (#1463,
`maestro.scenarios.carte`) : `lib.sh criteres` l'appelle pour **annoncer** les
scénarios à jouer, `lib.sh criteres-note` pour savoir si le constat doit porter sa
ligne `Banc`.

C'est une **détection**, pas un jugement. La carte dit quels scénarios le diff
traverse ; si le changement touche ce que le modèle reçoit ou fait — et donc si le
banc est dû —, c'est la session qui en juge et le consigne (docs/47 §4, docs/41).

## Ce que le diff touche

Le diff va de la base (`--base`, la base commune avec `origin/main`) à l'arbre de
travail, fichiers non suivis compris. Il se range en **touches** :

- dans un fichier Python, la **fonction** qui contient une ligne changée — son nom
  qualifié, celui que la sonde relève (`Classe.methode`, `f.<locals>.g`), lu par
  `ast` sur la version d'après pour une ligne ajoutée, d'avant pour une ligne
  retirée. Une fonction qui en contient d'autres couvre leurs noms
  (`f` croise `f.<locals>.g`), une classe ses méthodes ;
- hors de toute fonction, le **niveau module** (`<module>`) : une constante, un
  gabarit de prompt en tête de fichier. Il s'exécute à l'import, donc souvent au
  démarrage, et se rattache à un scénario **par son fichier** (docs/40 §5) ;
- un fichier entier quand on ne sait pas mieux : un fichier de données (playbook,
  `core/`, gabarit), un fichier Python nouveau, retiré ou qui ne se lit pas.

## Qui le croise, et lequel jouer

Une touche est croisée par les scénarios dont l'empreinte l'a exécutée (ou lue).
Le **démarrage** ne compte que pour ce qu'aucun scénario n'exécute : ce qui ne
tourne qu'au démarrage tourne avant chacun, et tout scénario le rejoue.

Le banc proposé est la **couverture la moins chère** : pour chaque touche, le
scénario le moins cher qui la croise, puis, du plus cher au moins cher, ceux que les
autres couvrent déjà sont retirés. Le coût est le coût **médian** de l'historique
(#1461) ; un coût jamais rapporté passe après, départagé par la durée médiane. Le
plus cher (S12 aujourd'hui) n'est donc proposé que s'il est **le seul** à couvrir une
partie du changement — aucun nom de scénario n'est écrit ici. Les proposés sont
annoncés **le moins cher en tête** ; ceux qui croisent aussi sans rien couvrir de plus
sont comptés, pas nommés.

## La carte composée

Chaque passage n'écrit la carte que des scénarios qu'il a joués. La carte lue ici est
donc **composée** : pour chaque scénario, sa dernière empreinte dans l'historique du
poste (une empreinte d'un scénario empêché ne sert qu'à défaut d'une autre), et le
démarrage du dernier passage mesuré. Un scénario que rien n'a mesuré est nommé
« jamais mesuré » : on ne sait pas s'il croise le diff, et le banc par lot sur `main`
(#1462), qui joue tout le catalogue, le mesurera.

## Codes de sortie

`0` la carte croise le diff (l'annonce sur la sortie standard) · `3` elle ne le croise
pas (rien sur la sortie) · `4` aucune carte lisible (le motif sur la sortie : l'appelant
retombe sur le préfixe, docs/47 §8) · `1` base ou diff illisible · `2` usage.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from maestro.scenarios import carte as carte_du_banc
from maestro.scenarios import historique
from maestro.scenarios.rapport import cout_en_mots, duree_en_mots

#: Le nom sous lequel le module s'invoque — dérivé, jamais recopié (cf. `banc.MODULE`).
MODULE = __spec__.name if __spec__ else "maestro.scenarios.croisement"

#: Le nom qualifié du code de niveau module, tel que la sonde le relève (`co_qualname`).
NIVEAU_MODULE = "<module>"

#: Ce que Git peut prendre avant qu'on renonce au diff.
DELAI_GIT_S = 60.0

#: Combien de touches l'annonce nomme par scénario avant de compter le reste.
TOUCHES_NOMMEES = 3

CODE_CROISE = 0
CODE_ILLISIBLE = 1
CODE_USAGE = 2
CODE_RIEN = 3
CODE_SANS_CARTE = 4

_USAGE = f"Usage : python -m {MODULE} --base <sha> [--depot <dossier>] [--court]"

#: L'en-tête d'un bloc de `git diff -U0` : `@@ -a[,b] +c[,d] @@`.
_BLOC = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class DiffIllisible(RuntimeError):
    """Git n'a pas rendu le diff : base inconnue, dépôt absent, Git manquant."""


# ── Les touches du diff ───────────────────────────────────────────────────────


@dataclass(frozen=True, order=True)
class Touche:
    """Ce que le diff change : une fonction, le niveau module, ou un fichier entier."""

    fichier: str
    #: Le nom qualifié de la fonction ; `NIVEAU_MODULE` ; `None` pour le fichier entier.
    portee: str | None = None

    def en_mots(self) -> str:
        return self.fichier if self.portee is None else f"{self.fichier}:{self.portee}"


@dataclass
class _Fichier:
    """Un fichier du diff : ses deux chemins et ses lignes changées de chaque côté."""

    avant: str | None = None
    apres: str | None = None
    lignes_avant: set[int] = field(default_factory=set)
    lignes_apres: set[int] = field(default_factory=set)
    #: Se juge-t-il en entier ? Un binaire, un fichier né ou retiré, un changement de
    #: mode seul (aucun bloc).
    entier: bool = True


def touches_du_diff(depot: Path, base: str) -> tuple[Touche, ...]:
    """Les touches du diff de `base` à l'arbre de travail de `depot`, non suivis compris.

    Lève `DiffIllisible` quand Git ne rend pas le diff.
    """
    texte = _git(
        depot,
        "-c",
        "core.quotePath=false",
        "diff",
        "-U0",
        "--no-color",
        "--no-ext-diff",
        "--no-renames",
        base,
        "--",
    )
    touches: set[Touche] = set()
    for fichier in _fichiers_du_diff(texte):
        touches.update(_touches_du_fichier(depot, base, fichier))
    for nouveau in _git(
        depot, "-c", "core.quotePath=false", "ls-files", "--others", "--exclude-standard"
    ).splitlines():
        if nouveau.strip():
            touches.add(Touche(nouveau.strip()))
    return tuple(sorted(touches))


def _git(depot: Path, *arguments: str) -> str:
    try:
        fini = subprocess.run(  # noqa: S603 - git, argv construit ici
            ["git", "-C", str(depot), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DELAI_GIT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DiffIllisible(f"git injouable : {exc}") from exc
    if fini.returncode != 0:
        raise DiffIllisible(fini.stderr.strip() or f"git {arguments[0]} a échoué")
    return fini.stdout


def _fichiers_du_diff(texte: str) -> list[_Fichier]:
    """Les fichiers d'un `git diff -U0`, chacun avec ses lignes changées des deux côtés."""
    fichiers: list[_Fichier] = []
    courant: _Fichier | None = None
    ne_ou_retire = False
    for ligne in texte.splitlines():
        if ligne.startswith("diff --git "):
            # Le chemin de l'en-tête, pour un fichier sans lignes `---`/`+++` (un binaire, un
            # changement de mode) : sans renommage, `a/<p> b/<p>`, espaces compris.
            paire = ligne[len("diff --git a/") :]
            moitie = (len(paire) - len(" b/")) // 2
            chemin = paire[:moitie] if paire[moitie:] == " b/" + paire[:moitie] else None
            courant = _Fichier(avant=chemin, apres=chemin)
            fichiers.append(courant)
            ne_ou_retire = False
        elif courant is None:
            continue
        elif ligne.startswith(("new file mode", "deleted file mode")):
            ne_ou_retire = True
        elif ligne.startswith("--- "):
            courant.avant = _chemin(ligne[4:], "a/")
        elif ligne.startswith("+++ "):
            courant.apres = _chemin(ligne[4:], "b/")
        elif trouve := _BLOC.match(ligne):
            courant.entier = ne_ou_retire
            debut_a, nombre_a = int(trouve.group(1)), int(trouve.group(2) or 1)
            debut_b, nombre_b = int(trouve.group(3)), int(trouve.group(4) or 1)
            courant.lignes_avant.update(range(debut_a, debut_a + nombre_a))
            courant.lignes_apres.update(range(debut_b, debut_b + nombre_b))
    return fichiers


def _chemin(brut: str, prefixe: str) -> str | None:
    brut = brut.rstrip("\t")
    if brut == "/dev/null":
        return None
    return brut[len(prefixe) :] if brut.startswith(prefixe) else brut


def _touches_du_fichier(depot: Path, base: str, fichier: _Fichier) -> set[Touche]:
    chemins = {c for c in (fichier.avant, fichier.apres) if c}
    if fichier.entier or not all(c.endswith(".py") for c in chemins):
        # Un fichier de données, un binaire, un Python né ou retiré, un changement de
        # mode : le fichier entier — c'est lui que la carte connaît.
        return {Touche(c) for c in chemins}
    touches: set[Touche] = set()

    def avant(chemin: str) -> str:
        return _git(depot, "show", f"{base}:{chemin}")

    def apres(chemin: str) -> str:
        return _lire(depot / chemin)

    cotes: tuple[tuple[str | None, set[int], Callable[[str], str]], ...] = (
        (fichier.avant, fichier.lignes_avant, avant),
        (fichier.apres, fichier.lignes_apres, apres),
    )
    for chemin, lignes, source in cotes:
        if not chemin or not lignes:
            continue
        try:
            portees = portees_de(source(chemin))
        except (DiffIllisible, OSError, SyntaxError, ValueError):
            touches.add(Touche(chemin))
            continue
        touches.update(Touche(chemin, portee_de_la_ligne(portees, n)) for n in lignes)
    return touches


def _lire(chemin: Path) -> str:
    return chemin.read_text(encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class Portee:
    """Une fonction ou une classe d'un fichier : ses lignes (décorateurs compris) et son nom."""

    debut: int
    fin: int
    profondeur: int
    nom: str


def portees_de(source: str) -> tuple[Portee, ...]:
    """Les fonctions et classes d'un source, nommées comme `co_qualname` les nomme.

    Dans une classe `C`, `C.m` ; dans une fonction `f`, `f.<locals>.g`. Lève
    `SyntaxError` sur un source qui ne se lit pas.
    """
    rendu: list[Portee] = []

    def visiter(noeud: ast.AST, prefixe: str, profondeur: int) -> None:
        for enfant in ast.iter_child_nodes(noeud):
            if isinstance(enfant, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                nom = prefixe + enfant.name
                debut = min([enfant.lineno, *(d.lineno for d in enfant.decorator_list)])
                rendu.append(Portee(debut, enfant.end_lineno or enfant.lineno, profondeur, nom))
                suite = "." if isinstance(enfant, ast.ClassDef) else ".<locals>."
                visiter(enfant, nom + suite, profondeur + 1)
            else:
                visiter(enfant, prefixe, profondeur)

    visiter(ast.parse(source), "", 0)
    return tuple(rendu)


def portee_de_la_ligne(portees: Iterable[Portee], ligne: int) -> str:
    """Le nom de la portée la plus profonde qui contient `ligne` — sinon le niveau module."""
    contenantes = [p for p in portees if p.debut <= ligne <= p.fin]
    if not contenantes:
        return NIVEAU_MODULE
    return max(contenantes, key=lambda p: p.profondeur).nom


# ── La carte composée ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CarteComposee:
    """Pour chaque scénario mesuré, sa dernière empreinte ; et le démarrage."""

    scenarios: Mapping[str, carte_du_banc.Empreinte]
    #: Le passage d'où vient chaque empreinte.
    passages: Mapping[str, str]
    demarrage: carte_du_banc.Empreinte | None = None


def composer(
    lignes: Sequence[Mapping[str, object]], chemin: Path | None = None
) -> CarteComposee | None:
    """La carte composée des passages de l'historique `chemin`, ou `None` s'il n'y en a aucune.

    Une carte qui ne se lit pas, ou d'une stack non mesurée, est sautée ; une empreinte
    vide ne mesure rien et n'entre pas.
    """
    lues: list[tuple[Mapping[str, object], carte_du_banc.CarteDuPassage]] = []
    for ligne in sorted(lignes, key=lambda ligne: str(ligne["passage"]), reverse=True):
        fichier = historique.carte_du_passage(ligne, chemin)
        if fichier is None:
            continue
        try:
            lue = carte_du_banc.charger(fichier)
        except (OSError, ValueError):
            continue
        if lue.stack_mesuree:
            lues.append((ligne, lue))
    if not lues:
        return None
    scenarios: dict[str, carte_du_banc.Empreinte] = {}
    passages: dict[str, str] = {}
    # Deux tours : une empreinte d'un scénario empêché (coupé en route) ne sert qu'à défaut.
    for empeche_admis in (False, True):
        for ligne, lue in lues:
            joues = ligne.get("scenarios")
            empeches = {
                str(s.get("id"))
                for s in (joues if isinstance(joues, list) else ())
                if isinstance(s, Mapping) and s.get("empechement")
            }
            for ident, empreinte in lue.scenarios.items():
                if ident in scenarios or (ident in empeches and not empeche_admis):
                    continue
                if empreinte.fonctions or empreinte.fichiers:
                    scenarios[ident] = empreinte
                    passages[ident] = lue.passage
    demarrage = next((lue.demarrage for _l, lue in lues if lue.demarrage.fonctions), None)
    return CarteComposee(scenarios=scenarios, passages=passages, demarrage=demarrage)


def _croise(touche: Touche, empreinte: carte_du_banc.Empreinte | None) -> bool:
    """L'empreinte a-t-elle exécuté (ou lu) ce que la touche change ?"""
    if empreinte is None:
        return False
    noms = empreinte.fonctions.get(touche.fichier)
    if touche.portee is None:
        return noms is not None or touche.fichier in empreinte.fichiers
    if not noms:
        return False
    if touche.portee == NIVEAU_MODULE:
        return True
    prefixe = touche.portee + "."
    return any(nom == touche.portee or nom.startswith(prefixe) for nom in noms)


# ── Le croisement ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Croisement:
    """Les scénarios à jouer, le moins cher en tête, et ce que la carte en sait."""

    #: La couverture la moins chère, le moins cher en tête.
    proposes: tuple[str, ...]
    #: Par scénario proposé, les touches qu'il croise (le démarrage marqué à part).
    couvre: Mapping[str, tuple[Touche, ...]]
    #: Ceux qui croisent aussi le diff sans rien couvrir de plus.
    autres: tuple[str, ...]
    #: Les scénarios du catalogue que la carte n'a jamais mesurés.
    jamais_mesures: tuple[str, ...]
    passages: Mapping[str, str]
    series: Mapping[str, historique.Serie]
    #: Les touches que seul le démarrage exécute.
    au_demarrage: frozenset[Touche] = frozenset()


def croiser(
    touches: Iterable[Touche],
    carte: CarteComposee,
    catalogue: Sequence[str],
    series: Sequence[historique.Serie],
) -> Croisement:
    """La couverture la moins chère des touches par les scénarios de la carte."""
    par_id = {s.identifiant: s for s in series}
    tous = tuple(dict.fromkeys([*catalogue, *carte.scenarios]))

    def cout(ident: str) -> tuple[int, float, float, tuple[str, int, str]]:
        serie = par_id.get(ident)
        montant = serie.cout_median_usd if serie is not None else None
        return (
            0 if montant is not None else 1,
            montant if montant is not None else 0.0,
            serie.duree_mediane_s if serie is not None else float("inf"),
            historique.ordre_naturel(ident),
        )

    croisees: dict[Touche, set[str]] = {}
    au_demarrage: set[Touche] = set()
    for touche in touches:
        qui = {ident for ident, e in carte.scenarios.items() if _croise(touche, e)}
        if not qui and _croise(touche, carte.demarrage):
            qui = set(tous)
            au_demarrage.add(touche)
        if qui:
            croisees[touche] = qui

    retenus = {min(qui, key=cout) for qui in croisees.values()}
    for ident in sorted(retenus, key=cout, reverse=True):
        reste = retenus - {ident}
        if all(qui & reste for qui in croisees.values()):
            retenus = reste
    proposes = tuple(sorted(retenus, key=cout))
    tous_croisants = set().union(*croisees.values()) if croisees else set()
    return Croisement(
        proposes=proposes,
        couvre={
            ident: tuple(sorted(t for t, qui in croisees.items() if ident in qui))
            for ident in proposes
        },
        autres=tuple(sorted(tous_croisants - retenus, key=cout)),
        jamais_mesures=tuple(i for i in tous if i not in carte.scenarios),
        passages=dict(carte.passages),
        series=par_id,
        au_demarrage=frozenset(au_demarrage),
    )


def resume(croisement: Croisement) -> str:
    """Une ligne : ce que la carte croise, pour le refus de `criteres-note`."""
    return f"la carte des scénarios croise le diff : {', '.join(croisement.proposes)} à jouer"


def annonce(croisement: Croisement) -> list[str]:
    """L'annonce de `lib.sh criteres` : une ligne par chose à savoir pour juger le banc dû."""
    lignes = [
        "la carte croise le diff — si le changement touche ce que le modèle reçoit ou fait, "
        "joue avant de pousser, le moins cher en tête : "
        + ", ".join(_scenario_en_mots(croisement, i) for i in croisement.proposes)
    ]
    for ident in croisement.proposes:
        touches = croisement.couvre[ident]
        noms = [
            t.en_mots() + (" (au démarrage)" if t in croisement.au_demarrage else "")
            for t in touches[:TOUCHES_NOMMEES]
        ]
        reste = f", … ({len(touches)} en tout)" if len(touches) > TOUCHES_NOMMEES else ""
        lignes.append(f"{ident} passe par {', '.join(noms)}{reste}")
    if croisement.autres:
        lignes.append(
            f"{len(croisement.autres)} autre(s) scénario(s) le croisent aussi, sans rien "
            "couvrir de plus"
        )
    origine = "carte : " + " ; ".join(
        f"{', '.join(ids)} du passage {passage}"
        for passage, ids in _par_passage(croisement.passages).items()
    )
    if croisement.jamais_mesures:
        origine += (
            f" ; jamais mesurés : {', '.join(croisement.jamais_mesures)} — "
            "le banc par lot sur main les mesure"
        )
    lignes.append(origine)
    lignes.append(
        "sinon : | Banc | non joué | mécanique couverte par <tests nommés> ; "
        "banc par lot sur main |"
    )
    return lignes


def _scenario_en_mots(croisement: Croisement, ident: str) -> str:
    serie = croisement.series.get(ident)
    if serie is None:
        return f"{ident} (jamais joué)"
    return (
        f"{ident} ({cout_en_mots(serie.cout_median_usd)} · {duree_en_mots(serie.duree_mediane_s)})"
    )


def _par_passage(passages: Mapping[str, str]) -> dict[str, list[str]]:
    rendu: dict[str, list[str]] = {}
    for ident in sorted(passages, key=historique.ordre_naturel):
        rendu.setdefault(passages[ident], []).append(ident)
    return dict(sorted(rendu.items()))


# ── La ligne de commande ──────────────────────────────────────────────────────


def _catalogue() -> tuple[str, ...]:
    """Les scénarios du banc — importés à l'appel : le catalogue charge tout le banc."""
    from maestro.scenarios.scenarios import SCENARIOS

    return tuple(s.identifiant for s in SCENARIOS)


def main(
    argv: Sequence[str] | None = None,
    *,
    catalogue: Sequence[str] | None = None,
    chemin: Path | None = None,
    sortie: TextIO | None = None,
    erreur: TextIO | None = None,
) -> int:
    """Croise le diff avec la carte — voir l'en-tête du module pour les codes."""
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    args = list(sys.argv[1:] if argv is None else argv)
    base, depot, court = None, Path.cwd(), False
    while args:
        arg = args.pop(0)
        if arg == "--base" and args:
            base = args.pop(0)
        elif arg == "--depot" and args:
            depot = Path(args.pop(0))
        elif arg == "--court":
            court = True
        else:
            print(_USAGE, file=erreur)
            return CODE_USAGE
    if not base:
        print(_USAGE, file=erreur)
        return CODE_USAGE

    try:
        fichier = chemin or historique.chemin_historique()
        lecture = historique.lire(fichier)
    except (RuntimeError, OSError) as exc:
        print(f"historique du banc illisible ({exc})", file=sortie)
        return CODE_SANS_CARTE
    carte = composer(lecture.lignes, fichier)
    if carte is None:
        print(
            f"aucune carte lisible dans l'historique du banc ({fichier}) — "
            "un passage du banc l'écrit",
            file=sortie,
        )
        return CODE_SANS_CARTE
    try:
        touches = touches_du_diff(depot, base)
    except DiffIllisible as exc:
        print(f"diff illisible depuis {base} : {exc}", file=erreur)
        return CODE_ILLISIBLE
    croisement = croiser(
        touches,
        carte,
        _catalogue() if catalogue is None else catalogue,
        historique.series(lecture.lignes),
    )
    if not croisement.proposes:
        return CODE_RIEN
    for ligne in [resume(croisement)] if court else annonce(croisement):
        print(ligne, file=sortie)
    return CODE_CROISE


if __name__ == "__main__":
    raise SystemExit(main())
