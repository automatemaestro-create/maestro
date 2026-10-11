"""Le banc sur `main` intégrée : quand le jouer, et à quelles PR rattacher un rouge (#1462).

    python -m maestro.scenarios.sur_main decider --cible <sha> --chemins "<préfixes>"
    python -m maestro.scenarios.sur_main verdict --cible <sha> --rapport <rapport.json>

Le banc se paie **une fois par lot**, sur `main` intégrée, après le drain final du
pilote ([docs/47](../../docs/47-decision-le-banc-se-paie-une-fois-par-lot.md)). Le
passage lui-même (copie dédiée, stack, scénarios) est l'affaire de
`scripts/orchestrate/banc-main.sh`. Ce module répond aux deux questions qui
encadrent ce passage, et qui ne se répondent qu'en lisant l'historique du banc
(`maestro.scenarios.historique`, #1461) contre l'historique de `main` :

## Faut-il jouer ? (`decider`)

Seulement si `main` a bougé **sur le chemin des scénarios** depuis le dernier
passage **complet** sur `main`. Un passage est complet quand il a joué **tout le
catalogue** sans empêchement : un passage coupé par une limite d'usage n'a rien
vérifié, et s'abstenir à cause de lui reviendrait à ne plus jamais jouer. Il est
**sur `main`** quand son sha est un ancêtre (ou l'égal) du sha visé et que son
arbre n'était pas modifié (`modifiee` à faux — inconnu ne compte pas) : un passage
du clone principal sur `main` compte, celui d'une branche de ticket non, puisque
le squash ne laisse jamais un commit de branche dans `main`.

Le chemin est **donné par l'appelant** (`GL_BANC_CHEMINS` de `scripts/gitlab/lib.sh`),
jamais recopié ici : il a un seul endroit, et la carte de #1463 le remplacera là.
Sans passage complet consigné, on joue — c'est le premier passage par lot.

## À quoi rattacher un rouge ? (`verdict`)

Un scénario rouge nomme **les PR mergées entre son dernier vert sur `main` et le
sha joué**, lues dans l'historique de `main` (`git log --first-parent`). Une PR s'y
reconnaît à son sujet de squash, `… (#<pr>)`, et son ticket au `Closes #<iid>` de
son corps. Celles qui ont touché le chemin viennent d'abord, nommées une à une ;
les autres sont comptées et listées sur une ligne — un rouge vieux de trois
semaines ne doit pas noyer le verdict. Le pilote **nomme**, il ne crée aucun
ticket (même conduite que les butées sans reprise, #611).

Trois cas que le texte distingue, parce qu'on ne les traite pas pareil :

- un dernier vert **au même sha** : aucune PR entre les deux, le rouge est non
  déterministe ;
- **aucun vert** sur `main` consigné : rien à quoi le rattacher, et c'est dit ;
- un scénario **empêché** (API, fournisseur, limite d'usage) : ni vert ni rouge du
  produit — une **abstention**, jamais rattachée à une PR.

Le verdict du passage suit : **rouge** dès qu'un scénario l'est, sinon
**abstention** dès qu'un scénario est empêché (ou qu'aucun n'a été joué), sinon
**vert**. Une abstention n'est jamais un vert (docs/47 §5).

## Codes de sortie

`decider` : `0` jouer · `3` s'abstenir · `2` usage. Le motif est sur une ligne,
`jouer<TAB>motif` ou `abstention<TAB>motif`.
`verdict` : `0` vert · `1` rouge · `3` abstention · `2` usage · `4` rapport
illisible. Le verdict en mots est sur la sortie standard, sa première ligne en
résumé.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from maestro.scenarios import historique
from maestro.scenarios.modele import VERDICT_VERT

#: Le nom sous lequel ce module s'invoque — dérivé, jamais recopié (cf. `banc.MODULE`).
MODULE = __spec__.name if __spec__ else "maestro.scenarios.sur_main"

CODE_JOUER = 0
CODE_VERT = 0
CODE_ROUGE = 1
CODE_USAGE = 2
CODE_ABSTENTION = 3
CODE_ILLISIBLE = 4

ETAT_VERT = "vert"
ETAT_ROUGE = "rouge"
ETAT_ABSTENTION = "abstention"

#: Ce qu'une lecture Git peut prendre avant qu'on renonce.
DELAI_GIT_S = 60.0

#: Le sujet d'un squash de PR : `… (#1474)` en fin de ligne (la convention de GitHub).
_PR_DU_SUJET = re.compile(r"\(#(\d+)\)\s*$")
#: Le ticket qu'une PR ferme, dans son corps (docs/10 §2).
_TICKET_DU_CORPS = re.compile(r"\b(?:Closes|Fixes|Resolves)\s+#(\d+)", re.IGNORECASE)

#: Combien de fichiers du chemin le motif nomme avant de compter le reste.
FICHIERS_NOMMES = 3
#: Ce qu'un motif de scénario occupe dans le verdict — le rapport dit le reste.
MOTIF_MAX = 160

SHA_COURT = historique.SHA_COURT

_USAGE = (
    f"Usage : python -m {MODULE} decider --cible <sha> --chemins \"<préfixes>\" "
    "[--depot <dossier>]\n"
    f"        python -m {MODULE} verdict --cible <sha> --rapport <rapport.json> "
    "[--depot <dossier>]"
)


# ── Git ───────────────────────────────────────────────────────────────────────


class Depot:
    """Les lectures Git dont ce module a besoin, sur un dépôt — et leur cache.

    Une seule instance par question : l'ascendance d'un même sha se demande une fois,
    quel que soit le nombre de lignes d'historique qui le portent.
    """

    def __init__(self, racine: Path) -> None:
        self.racine = racine
        self._ancetres: dict[tuple[str, str], bool] = {}

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(  # noqa: S603 - git, argv construit ici
                ["git", "-C", str(self.racine), *arguments],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=DELAI_GIT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

    def est_ancetre(self, sha: str, cible: str) -> bool:
        """`sha` est-il `cible` ou l'un de ses ancêtres ? Un sha inconnu du dépôt : non."""
        cle = (sha, cible)
        if cle not in self._ancetres:
            resultat = self._git("merge-base", "--is-ancestor", sha, cible)
            self._ancetres[cle] = resultat is not None and resultat.returncode == 0
        return self._ancetres[cle]

    def fichiers_changes(self, de: str, a: str) -> list[str] | None:
        """Les fichiers qui diffèrent entre deux commits — `None` si Git ne sait pas le dire."""
        resultat = self._git("diff", "--name-only", de, a)
        if resultat is None or resultat.returncode != 0:
            return None
        return [ligne.strip() for ligne in resultat.stdout.splitlines() if ligne.strip()]

    def commits(self, de: str, a: str) -> list[Commit] | None:
        """Les commits de `main` entre `de` (exclu) et `a`, du plus ancien au plus récent."""
        resultat = self._git(
            "log", "--first-parent", "--reverse", "--name-only",
            "--format=%x1e%H%x00%s%x00%b%x00", f"{de}..{a}",
        )
        if resultat is None or resultat.returncode != 0:
            return None
        commits = []
        for bloc in resultat.stdout.split("\x1e"):
            if not bloc.strip():
                continue
            sha, sujet, corps, fichiers = (bloc.split("\x00", 3) + ["", "", ""])[:4]
            commits.append(
                Commit(
                    sha=sha.strip(),
                    sujet=sujet.strip(),
                    corps=corps,
                    fichiers=tuple(f.strip() for f in fichiers.splitlines() if f.strip()),
                )
            )
        return commits


@dataclass(frozen=True)
class Commit:
    """Un commit de `main` : son sujet dit sa PR, son corps son ticket."""

    sha: str
    sujet: str
    corps: str = ""
    fichiers: tuple[str, ...] = ()

    @property
    def pr(self) -> int | None:
        trouve = _PR_DU_SUJET.search(self.sujet)
        return int(trouve.group(1)) if trouve else None

    @property
    def ticket(self) -> int | None:
        trouve = _TICKET_DU_CORPS.search(self.corps)
        return int(trouve.group(1)) if trouve else None

    def nom(self) -> str:
        """`#1474` pour une PR, le sha court pour un commit qui n'en porte pas."""
        return f"#{self.pr}" if self.pr is not None else self.sha[:SHA_COURT]


def sur_le_chemin(fichiers: Iterable[str], chemins: Sequence[str]) -> list[str]:
    """Les fichiers sous l'un des préfixes du chemin des scénarios."""
    return [f for f in fichiers if any(f.startswith(prefixe) for prefixe in chemins)]


def chemins_de(brut: str) -> tuple[str, ...]:
    """« maestro/ core/ » → ('maestro/', 'core/') : la forme de `GL_BANC_CHEMINS`."""
    return tuple(morceau for morceau in brut.split() if morceau)


# ── Les passages sur main ─────────────────────────────────────────────────────


def sur_main(ligne: Mapping[str, Any], cible: str, depot: Depot) -> bool:
    """Ce passage a-t-il joué un état de `main` qui précède (ou est) `cible` ?"""
    sha = ligne.get("sha")
    return bool(sha) and ligne.get("modifiee") is False and depot.est_ancetre(str(sha), cible)


def complet(ligne: Mapping[str, Any], catalogue: Iterable[str]) -> bool:
    """Le passage a-t-il joué tout le catalogue, sans aucun empêchement ?"""
    joues = {s["id"]: s for s in ligne.get("scenarios", [])}
    return all(i in joues and not joues[i].get("empechement") for i in catalogue)


def dernier_passage_complet(
    lignes: Sequence[Mapping[str, Any]], cible: str, depot: Depot, catalogue: Sequence[str]
) -> Mapping[str, Any] | None:
    """Le plus récent passage complet sur `main` avant (ou à) `cible`, s'il y en a un."""
    for ligne in sorted(lignes, key=lambda ligne: str(ligne["passage"]), reverse=True):
        if complet(ligne, catalogue) and sur_main(ligne, cible, depot):
            return ligne
    return None


def dernier_vert(
    lignes: Sequence[Mapping[str, Any]],
    scenario: str,
    cible: str,
    depot: Depot,
) -> Mapping[str, Any] | None:
    """Le plus récent passage sur `main` où `scenario` était vert."""
    for ligne in sorted(lignes, key=lambda ligne: str(ligne["passage"]), reverse=True):
        verts = any(
            s.get("id") == scenario and s.get("verdict") == VERDICT_VERT
            for s in ligne.get("scenarios", [])
        )
        if verts and sur_main(ligne, cible, depot):
            return ligne
    return None


def _court(sha: Any) -> str:
    return str(sha or "?")[:SHA_COURT]


# ── Décider ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Decision:
    """Jouer ou s'abstenir, et pourquoi — le motif se lit tel quel au journal du run."""

    jouer: bool
    motif: str


def decider(
    lignes: Sequence[Mapping[str, Any]],
    cible: str,
    depot: Depot,
    *,
    chemins: Sequence[str],
    catalogue: Sequence[str],
) -> Decision:
    """Faut-il jouer le banc entier sur `cible` ? Voir l'en-tête du module."""
    precedent = dernier_passage_complet(lignes, cible, depot, catalogue)
    if precedent is None:
        return Decision(
            True,
            "aucun passage complet du banc sur main n'est consigné — premier passage par lot",
        )
    passage, sha = precedent["passage"], str(precedent["sha"])
    if sha == cible:
        return Decision(
            False,
            f"main ({_court(cible)}) a déjà son passage complet : {passage} ({_court(sha)})",
        )
    fichiers = depot.fichiers_changes(sha, cible)
    if fichiers is None:
        return Decision(
            True,
            f"Git ne sait pas comparer {_court(sha)} (passage complet {passage}) à "
            f"{_court(cible)} — on joue plutôt que de supposer",
        )
    touches = sur_le_chemin(fichiers, chemins)
    if not touches:
        return Decision(
            False,
            f"main n'a pas bougé sur le chemin des scénarios ({' '.join(chemins)}) depuis le "
            f"passage complet {passage} ({_court(sha)} → {_court(cible)}, "
            f"{len(fichiers)} fichier(s) changé(s) ailleurs)",
        )
    nommes = ", ".join(touches[:FICHIERS_NOMMES])
    surplus = len(touches) - FICHIERS_NOMMES
    reste = f" et {surplus} autre(s)" if surplus > 0 else ""
    return Decision(
        True,
        f"{len(touches)} fichier(s) du chemin ont bougé depuis le passage complet {passage} "
        f"({_court(sha)} → {_court(cible)}) : {nommes}{reste}",
    )


# ── Le verdict ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Rattachement:
    """Un rouge et ce qui a été mergé depuis son dernier vert sur `main`."""

    scenario: str
    motif: str
    dernier_vert: Mapping[str, Any] | None = None
    #: Les commits de `main` entre le dernier vert et le sha joué ; `None` : illisibles.
    commits: tuple[Commit, ...] | None = ()


@dataclass(frozen=True)
class Verdict:
    """Le verdict d'un passage sur `main`, scénario par scénario."""

    passage: str
    cible: str
    verts: tuple[str, ...]
    rouges: tuple[Rattachement, ...]
    empeches: tuple[tuple[str, str], ...]
    rapport: Path | None = None
    chemins: tuple[str, ...] = field(default=())

    @property
    def etat(self) -> str:
        if self.rouges:
            return ETAT_ROUGE
        if self.empeches or not self.verts:
            return ETAT_ABSTENTION
        return ETAT_VERT

    @property
    def code(self) -> int:
        return {ETAT_VERT: CODE_VERT, ETAT_ROUGE: CODE_ROUGE}.get(self.etat, CODE_ABSTENTION)


def verdict(
    rapport: Mapping[str, Any],
    lignes: Sequence[Mapping[str, Any]],
    cible: str,
    depot: Depot,
    *,
    chemins: Sequence[str] = (),
    fichier: Path | None = None,
) -> Verdict:
    """Le verdict du passage `rapport` joué sur `cible`, chaque rouge rattaché à ses PR."""
    passage = str(rapport.get("horodatage") or "?")
    verts: list[str] = []
    rouges: list[Rattachement] = []
    empeches: list[tuple[str, str]] = []
    for scenario in rapport.get("scenarios", []):
        identifiant = str(scenario.get("id"))
        motif = " ".join(str(scenario.get("motif") or "").split())
        if scenario.get("verdict") == VERDICT_VERT:
            verts.append(identifiant)
        elif scenario.get("empechement"):
            empeches.append((identifiant, motif))
        else:
            precedent = dernier_vert(lignes, identifiant, cible, depot)
            commits: tuple[Commit, ...] | None = ()
            if precedent is not None and str(precedent["sha"]) != cible:
                lus = depot.commits(str(precedent["sha"]), cible)
                commits = None if lus is None else tuple(lus)
            rouges.append(Rattachement(identifiant, motif, precedent, commits))
    return Verdict(
        passage=passage,
        cible=cible,
        verts=tuple(verts),
        rouges=tuple(rouges),
        empeches=tuple(empeches),
        rapport=fichier,
        chemins=tuple(chemins),
    )


def _motif(motif: str) -> str:
    return motif if len(motif) <= MOTIF_MAX else motif[:MOTIF_MAX].rstrip() + "…"


def _pr_en_mots(commit: Commit) -> str:
    ticket = f" (ticket #{commit.ticket})" if commit.ticket is not None else ""
    sujet = _PR_DU_SUJET.sub("", commit.sujet).strip()
    return f"{commit.nom()}{ticket} {sujet}"


def _rattachement_en_mots(rouge: Rattachement, verdict_: Verdict) -> list[str]:
    lignes = [f"  ✗ {rouge.scenario} rouge — {_motif(rouge.motif)}"]
    precedent = rouge.dernier_vert
    if precedent is None:
        lignes.append("      aucun vert sur main consigné — rien à quoi rattacher ce rouge")
        return lignes
    vu = f"{precedent['passage']} ({_court(precedent['sha'])})"
    if str(precedent["sha"]) == verdict_.cible:
        lignes.append(
            f"      dernier vert sur main au même sha ({vu}) — aucune PR entre les deux : "
            "rouge non déterministe"
        )
        return lignes
    if rouge.commits is None:
        lignes.append(f"      dernier vert sur main : {vu} — l'historique de main est illisible")
        return lignes
    commits = list(rouge.commits)
    lignes.append(f"      dernier vert sur main : {vu} — {len(commits)} PR mergée(s) depuis")
    chemin = [c for c in commits if sur_le_chemin(c.fichiers, verdict_.chemins)]
    ailleurs = [c for c in commits if c not in chemin]
    if not verdict_.chemins:
        chemin, ailleurs = commits, []
    for commit in chemin:
        lignes.append(f"        {_pr_en_mots(commit)}")
    if ailleurs:
        lignes.append(
            f"        hors du chemin des scénarios ({len(ailleurs)}) : "
            + ", ".join(c.nom() for c in ailleurs)
        )
    return lignes


def en_texte(verdict_: Verdict) -> str:
    """Le verdict en mots : une ligne de résumé, puis un bloc par rouge et par empêchement."""
    joues = len(verdict_.verts) + len(verdict_.rouges) + len(verdict_.empeches)
    compte = (
        f"{joues} scénario(s) : {len(verdict_.verts)} vert(s), {len(verdict_.rouges)} rouge(s)"
        + (f", {len(verdict_.empeches)} empêché(s)" if verdict_.empeches else "")
    )
    tete = {
        ETAT_VERT: "vert",
        ETAT_ROUGE: "rouge",
        ETAT_ABSTENTION: "abstention — un banc injouable n'est jamais compté vert",
    }[verdict_.etat]
    if verdict_.rouges:
        noms = ", ".join(r.scenario for r in verdict_.rouges)
        tete = f"rouge ({noms})"
    lignes = [
        f"Banc sur main {_court(verdict_.cible)} : {tete} · passage {verdict_.passage} · {compte}"
    ]
    for rouge in verdict_.rouges:
        lignes += _rattachement_en_mots(rouge, verdict_)
    for identifiant, motif in verdict_.empeches:
        lignes.append(f"  ~ {identifiant} empêché (abstention) — {_motif(motif)}")
    if verdict_.rapport is not None:
        lignes.append(f"  rapport : {verdict_.rapport.with_name('rapport.md')}")
    return "\n".join(lignes) + "\n"


# ── La ligne de commande ──────────────────────────────────────────────────────


def _catalogue() -> tuple[str, ...]:
    """Les scénarios du banc — importés à l'appel : le catalogue charge tout le banc."""
    from maestro.scenarios.scenarios import SCENARIOS

    return tuple(s.identifiant for s in SCENARIOS)


def main(
    argv: Sequence[str] | None = None,
    *,
    catalogue: Sequence[str] | None = None,
    sortie: TextIO | None = None,
    erreur: TextIO | None = None,
) -> int:
    """`decider` ou `verdict` — voir l'en-tête du module pour les codes."""
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        options = _options(args)
    except ValueError as refus:
        print(f"{_USAGE}\n  {refus}", file=erreur)
        return CODE_USAGE
    depot = Depot(options.depot)
    try:
        lecture = historique.lire(historique.chemin_historique())
        lignes: Sequence[Mapping[str, Any]] = lecture.lignes
    except (OSError, RuntimeError) as exc:
        print(f"Historique du banc illisible ({exc}) — lu comme vide.", file=erreur)
        lignes = ()

    if options.verbe == "decider":
        decision = decider(
            lignes,
            options.cible,
            depot,
            chemins=options.chemins,
            catalogue=tuple(catalogue) if catalogue is not None else _catalogue(),
        )
        print(f"{'jouer' if decision.jouer else ETAT_ABSTENTION}\t{decision.motif}", file=sortie)
        return CODE_JOUER if decision.jouer else CODE_ABSTENTION

    assert options.rapport is not None
    try:
        rapport = json.loads(options.rapport.read_text(encoding="utf-8"))
        if not isinstance(rapport, dict) or not isinstance(rapport.get("scenarios"), list):
            raise ValueError("ni horodatage ni scénarios")
    except (OSError, ValueError) as exc:
        print(f"Rapport illisible ({options.rapport}) : {exc}", file=erreur)
        return CODE_ILLISIBLE
    resultat = verdict(
        rapport, lignes, options.cible, depot, chemins=options.chemins, fichier=options.rapport
    )
    print(en_texte(resultat), end="", file=sortie)
    return resultat.code


@dataclass
class _Options:
    verbe: str = ""
    cible: str = ""
    chemins: tuple[str, ...] = ()
    rapport: Path | None = None
    depot: Path = field(default_factory=Path.cwd)


def _options(args: Sequence[str]) -> _Options:
    """Lit la ligne de commande — lève `ValueError` avec son motif sur tout écart."""
    if not args or args[0] not in ("decider", "verdict"):
        raise ValueError("verbe attendu : decider ou verdict")
    options = _Options(verbe=args[0])
    reste = list(args[1:])
    while reste:
        arg = reste.pop(0)
        if arg not in ("--cible", "--chemins", "--rapport", "--depot"):
            raise ValueError(f"argument inconnu : {arg}")
        if not reste:
            raise ValueError(f"{arg} attend une valeur")
        valeur = reste.pop(0)
        if arg == "--cible":
            options.cible = valeur.strip()
        elif arg == "--chemins":
            options.chemins = chemins_de(valeur)
        elif arg == "--rapport":
            options.rapport = Path(valeur)
        else:
            options.depot = Path(valeur)
    if not options.cible:
        raise ValueError("--cible attend le sha de main joué")
    if options.verbe == "decider" and not options.chemins:
        raise ValueError("decider attend --chemins (les préfixes du chemin des scénarios)")
    if options.verbe == "verdict" and options.rapport is None:
        raise ValueError("verdict attend --rapport <rapport.json>")
    return options


if __name__ == "__main__":
    raise SystemExit(main())
