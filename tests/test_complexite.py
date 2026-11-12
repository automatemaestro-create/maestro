"""La complexité du code ne monte plus (#1577, docs/53 §5).

La règle C901 de ruff est dans `ruff check .`, donc dans le job `python-lint` et dans le filet
local, au seuil de l'outil : `pyproject.toml` ne règle aucun `max-complexity`. Une fonction qui le
dépasse porte un `# noqa: C901` à la ligne de son `def`. C'est sa dérogation, nommée là où on lit
la fonction. Le nombre de ces dérogations est un cliquet, le mécanisme du budget de `CLAUDE.md`
(`tests/cliquet.py`, #1523) : il fige la dette du jour sans la rembourser, et chaque baisse
s'inscrit. Les découpages la remboursent (#1525, #1468, #1469).

Trois gardes :

- `TestLeLintVoitLaComplexite` : une fonction neuve au-dessus du seuil fait rougir `ruff check`
  sous chaque racine du code, par la configuration du dépôt, et les deux filets jouent
  `ruff check .` sur tout le dépôt.
- `TestChaqueDerogationSert` : les `# noqa: C901` du dépôt sont exactement les fonctions que ruff
  signale à son propre seuil une fois toute dérogation retirée (`--isolated --ignore-noqa`). Une
  dérogation devenue inutile rougit. Un dépassement que tait une `# noqa` anonyme, une exemption de
  fichier ou un réglage de `pyproject.toml` rougit aussi.
- `TestLeCompteNeRemontePas` : le compte de ces dérogations suit `DEROGATIONS_C901_LIVREES`, un
  registre qui ne fait que descendre.

**Pourquoi pas RUF100.** Activé sur tout le dépôt, il retirerait les `noqa` qui visent des règles
non activées (BLE001, S603…) et portent une intention (docs/53 §5). La confrontation ne regarde que
C901, et lit les positions que ruff rend, jamais le texte de ses messages.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest
import yaml
from cliquet import Cliquet, CliquetRemonte, verifier_cliquet

RACINE = Path(__file__).resolve().parents[1]

#: Chaque compte de dérogations C901 livré sur `main`, du plus ancien au plus récent : le ticket qui
#: l'a posé, et sa valeur. Le premier est le relevé du jour où C901 est entré dans le lint : 70
#: fonctions au-dessus du seuil, 61 sous `maestro/`, 5 sous `scripts/`, 4 sous `tests/`. Une ligne
#: ne s'ajoute que plus basse que la précédente, et la première ne se retouche pas.
DEROGATIONS_C901_LIVREES: tuple[tuple[str, int], ...] = (("#1577", 70), ("#1525", 69))

#: Le cliquet des dérogations, et les mots de ses refus.
CLIQUET_C901 = Cliquet(
    registre="DEROGATIONS_C901_LIVREES",
    valeur="Le compte des `# noqa: C901` du dépôt",
    unite="dérogations",
    livre="livrées",
    regle=(
        "Les dérogations C901 sont un cliquet (#1577, docs/53 §5), leur nombre ne remonte jamais : "
        "une fonction neuve se découpe sous le seuil de ruff au lieu de porter un `# noqa: C901`."
    ),
    decision="docs/53",
)

#: Les racines du code que `ruff check .` juge, chacune éprouvée par son échantillon.
RACINES_DU_CODE = ("maestro", "scripts", "tests")

#: Une directive `noqa` de ruff dans un commentaire, anonyme ou suivie de ses codes. Le mot-clé est
#: insensible à la casse, comme pour ruff ; une exemption de fichier (`ruff: noqa`) n'en est pas
#: une. Un commentaire qui cite la directive en est une pour ruff : ceux de cette suite la
#: décrivent sans l'écrire.
_NOQA = re.compile(r"#\s*(?i:noqa)(?::\s*(?P<codes>[A-Z]+[0-9]+(?:[\s,]+[A-Z]+[0-9]+)*))?")


def ruff(
    *arguments: str, racine: Path = RACINE, entree: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Le ruff de l'environnement qui joue la suite, lancé depuis `racine`."""
    return subprocess.run(
        [sys.executable, "-m", "ruff", *arguments],
        cwd=racine,
        input=entree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@dataclass(frozen=True, order=True)
class Site:
    """Une ligne d'un fichier, en chemin POSIX relatif à la racine jugée."""

    fichier: str
    ligne: int

    def __str__(self) -> str:
        return f"{self.fichier}:{self.ligne}"


def _site(chemin: str | Path, ligne: int, racine: Path) -> Site:
    return Site(Path(chemin).resolve().relative_to(racine.resolve()).as_posix(), ligne)


def depassements(racine: Path = RACINE) -> dict[Site, str]:
    """Les fonctions que ruff signale à son propre seuil une fois toute dérogation retirée.

    `--isolated` écarte `pyproject.toml`, donc un `max-complexity` ou un `per-file-ignores`.
    `--ignore-noqa` écarte les `# noqa` et les exemptions de fichier (`# ruff: noqa`). Chaque site
    porte le message de ruff, rendu tel quel.
    """
    juge = ruff(
        "check",
        "--isolated",
        "--no-cache",
        "--select",
        "C901",
        "--ignore-noqa",
        "--exit-zero",
        "--output-format",
        "json",
        ".",
        racine=racine,
    )
    assert juge.returncode == 0, f"ruff n'a pas jugé {racine} : {juge.stderr}"
    return {
        _site(diagnostic["filename"], diagnostic["location"]["row"], racine): diagnostic["message"]
        for diagnostic in json.loads(juge.stdout)
        if diagnostic["code"] == "C901"
    }


def fichiers_juges(racine: Path = RACINE) -> list[Path]:
    """Les fichiers Python que ruff juge sous `racine`, de son propre aveu (`--show-files`)."""
    liste = ruff("check", "--isolated", "--no-cache", "--show-files", ".", racine=racine)
    assert liste.returncode == 0, f"ruff n'a pas listé {racine} : {liste.stderr}"
    chemins = (racine / ligne.strip() for ligne in liste.stdout.splitlines() if ligne.strip())
    return [chemin for chemin in chemins if chemin.suffix == ".py"]


def nomme_c901(commentaire: str) -> bool:
    """Le commentaire porte une `# noqa` qui nomme C901 ; une `# noqa` anonyme ne la nomme pas."""
    return any(
        "C901" in re.split(r"[\s,]+", directive["codes"] or "")
        for directive in _NOQA.finditer(commentaire)
    )


def derogations(racine: Path = RACINE) -> set[Site]:
    """Les `# noqa` qui nomment C901, dans les commentaires des fichiers que ruff juge.

    Elles sont lues par `tokenize`, pas cherchées dans le texte : une docstring ou une chaîne qui
    cite `# noqa: C901`, comme celles de cette suite, n'est pas une dérogation.
    """
    sites: set[Site] = set()
    for chemin in fichiers_juges(racine):
        donnees = chemin.read_bytes()
        if b"C901" not in donnees:
            continue
        for jeton in tokenize.tokenize(io.BytesIO(donnees).readline):
            if jeton.type == tokenize.COMMENT and nomme_c901(jeton.string):
                sites.add(_site(chemin, jeton.start[0], racine))
    return sites


@dataclass(frozen=True)
class Confrontation:
    """Ce que ruff signale sans dérogation, confronté aux dérogations écrites."""

    #: Les dépassements sans dérogation nommée à la ligne de leur `def`, avec le message de ruff.
    muets: dict[Site, str]
    #: Les dérogations qu'aucun dépassement ne justifie.
    inutiles: frozenset[Site]
    #: Le nombre de dérogations écrites.
    derogations: int


def confronter(racine: Path = RACINE) -> Confrontation:
    sur_le_seuil = depassements(racine)
    nommees = derogations(racine)
    return Confrontation(
        muets={site: message for site, message in sur_le_seuil.items() if site not in nommees},
        inutiles=frozenset(nommees - sur_le_seuil.keys()),
        derogations=len(nommees),
    )


@cache
def confrontation_du_depot() -> Confrontation:
    return confronter(RACINE)


def fonction_de_complexite(complexite: int) -> str:
    """Une fonction propre pour toutes les règles du dépôt, de complexité McCabe `complexite` :
    un chemin, plus un par `if`."""
    branches = "".join(
        f"    if valeur == {rang}:\n        return {rang}\n" for rang in range(complexite - 1)
    )
    return f"def echantillon(valeur: int) -> int:\n{branches}    return -1\n"


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 1. Le lint voit la complexité
# ─────────────────────────────────────────────────────────────────────────────────────────────────


class TestLeLintVoitLaComplexite:
    """Une fonction neuve au-dessus du seuil fait rougir `ruff check .`, dans les deux filets."""

    @pytest.mark.parametrize("racine_du_code", RACINES_DU_CODE)
    def test_une_fonction_neuve_au_dessus_du_seuil_rougit(self, racine_du_code: str) -> None:
        """L'échantillon fautif : une fonction neuve de complexité 11, posée sous la racine par
        `--stdin-filename`. Elle passe par la configuration du dépôt (sélection, seuil,
        `per-file-ignores`), comme sous `ruff check .`. Elle rougit, et C901 en est la seule raison.
        Sa jumelle à 10 passe : le seuil est celui de ruff, au point près."""
        chemin = f"{racine_du_code}/_echantillon_c901.py"
        rouge = ruff(
            "check",
            "--no-cache",
            "--output-format",
            "json",
            "--stdin-filename",
            chemin,
            "-",
            entree=fonction_de_complexite(11),
        )
        assert rouge.returncode == 1, rouge.stderr
        signale = [(d["code"], d["location"]["row"]) for d in json.loads(rouge.stdout)]
        assert signale == [("C901", 1)]
        jumelle = fonction_de_complexite(10)
        vert = ruff("check", "--no-cache", "--stdin-filename", chemin, "-", entree=jumelle)
        assert vert.returncode == 0, vert.stdout + vert.stderr

    def test_les_deux_filets_jouent_ruff_check_sur_tout_le_depot(self) -> None:
        """Le job `python-lint` et le filet local jouent `ruff check .` depuis la racine, sans
        `--select` ni `--ignore` qui retrancherait C901 : l'échantillon vaut pour eux."""
        ci = yaml.safe_load((RACINE / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
        etapes = ci["jobs"]["python-lint"]["steps"]
        assert "ruff check ." in [str(etape.get("run", "")).strip() for etape in etapes]
        local = (RACINE / "scripts/ci/local.sh").read_text(encoding="utf-8")
        corps = local[local.index("\njob_ruff() {") :]
        corps = corps[: corps.index("\n}\n")]
        assert re.search(r'^\s*if execute "\$exe" check \.; then$', corps, re.MULTILINE), corps


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 2. Chaque dérogation sert, et chaque dépassement porte la sienne
# ─────────────────────────────────────────────────────────────────────────────────────────────────


def _ecrire(racine: Path, fichiers: dict[str, str]) -> None:
    for nom, contenu in fichiers.items():
        (racine / nom).write_text(contenu, encoding="utf-8")


class TestChaqueDerogationSert:
    """Les `# noqa: C901` du dépôt sont exactement les fonctions au-dessus du seuil de ruff."""

    def test_la_garde_juge_le_vrai_depot(self) -> None:
        """Le plancher : une garde qui ne verrait aucun fichier trouverait les deux ensembles vides,
        donc égaux, sans rien avoir regardé."""
        fichiers = fichiers_juges()
        assert Path(__file__).resolve() in {chemin.resolve() for chemin in fichiers}
        assert len(fichiers) > 100, f"ruff ne juge que {len(fichiers)} fichiers sous {RACINE}"

    def test_chaque_depassement_porte_sa_derogation_et_chacune_sert(self) -> None:
        confrontation = confrontation_du_depot()
        muets = sorted(confrontation.muets.items())
        assert not muets, (
            "Au-dessus du seuil de C901 sans `# noqa: C901` à la ligne de leur def :\n"
            + "\n".join(f"  {site} — {message}" for site, message in muets)
            + "\nDécoupe la fonction sous le seuil de ruff. Une `# noqa` anonyme, une exemption de "
            "fichier ou un réglage de pyproject.toml ne nomment pas une dérogation, et une "
            "`# noqa: C901` neuve fait rougir le compte (docs/53 §5)."
        )
        assert not confrontation.inutiles, (
            "`# noqa: C901` sans fonction au-dessus du seuil :\n"
            + "\n".join(f"  {site}" for site in sorted(confrontation.inutiles))
            + "\nLa fonction est repassée sous le seuil : retire sa dérogation, puis inscris la "
            "baisse dans DEROGATIONS_C901_LIVREES (tests/test_complexite.py)."
        )

    def test_la_confrontation_voit_chaque_faute(self, tmp_path: Path) -> None:
        """L'échantillon fautif de la confrontation, une faute par fichier. Trois manières de taire
        un dépassement sans le nommer : la `# noqa` anonyme, l'exemption de fichier, et le réglage
        de `pyproject.toml` (un seuil relevé, un `per-file-ignores`). Une dérogation devenue
        inutile. Et deux témoins qui ne sont pas des fautes : la dérogation qui nomme C901 parmi
        d'autres codes, et la chaîne qui la cite."""
        complexe = fonction_de_complexite(11)
        _ecrire(
            tmp_path,
            {
                "pyproject.toml": (
                    "[tool.ruff.lint]\n"
                    'select = ["C90"]\n'
                    'per-file-ignores = {"reglage.py" = ["C901"]}\n'
                    "[tool.ruff.lint.mccabe]\n"
                    "max-complexity = 50\n"
                ),
                "muet.py": complexe,
                "anonyme.py": complexe.replace(":\n", ":  # noqa\n", 1),
                "exempte.py": "# ruff: noqa: C901\n" + complexe,
                "reglage.py": complexe,
                "nommee.py": complexe.replace(":\n", ":  # noqa: B008, C901\n", 1),
                "inutile.py": "def simple() -> int:  # noqa: C901\n    return 1\n",
                "citee.py": '"""Une dérogation s\'écrit `# noqa: C901` à la ligne du def."""\n',
            },
        )
        confrontation = confronter(tmp_path)
        assert set(confrontation.muets) == {
            Site("muet.py", 1),
            Site("anonyme.py", 1),
            Site("exempte.py", 2),
            Site("reglage.py", 1),
        }
        assert all("echantillon" in message for message in confrontation.muets.values())
        assert confrontation.inutiles == {Site("inutile.py", 1)}
        assert confrontation.derogations == 2


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 3. Le compte ne remonte pas
# ─────────────────────────────────────────────────────────────────────────────────────────────────


class TestLeCompteNeRemontePas:
    """Le nombre de dérogations C901 est un cliquet, sur le registre `DEROGATIONS_C901_LIVREES`."""

    def test_le_compte_suit_le_registre(self) -> None:
        """Le compte du dépôt est la dernière ligne du registre. La première est le relevé de
        #1577, épinglée : la réécrire ferait passer une hausse."""
        assert DEROGATIONS_C901_LIVREES[:1] == (("#1577", 70),)
        verifier_cliquet(
            confrontation_du_depot().derogations, DEROGATIONS_C901_LIVREES, CLIQUET_C901
        )

    def test_une_derogation_de_plus_rougit(self) -> None:
        """L'échantillon fautif : la fonction neuve qu'on fait taire d'une `# noqa: C901` au lieu
        de la découper, une dérogation de plus que le registre."""
        _, dernier = DEROGATIONS_C901_LIVREES[-1]
        with pytest.raises(CliquetRemonte) as leve:
            verifier_cliquet(dernier + 1, DEROGATIONS_C901_LIVREES, CLIQUET_C901)
        message = str(leve.value)
        assert f"vaut {dernier + 1} dérogations, au-dessus des {dernier} livrées par" in message
        assert "docs/53 §5" in message

    def test_la_relever_au_registre_rougit(self) -> None:
        """Inscrire la hausse ne passe pas : une ligne neuve doit être plus basse que la
        précédente. Égale non plus, ce serait un compte qui ne descend pas."""
        _, dernier = DEROGATIONS_C901_LIVREES[-1]
        for releve in (dernier + 1, dernier):
            registre = (*DEROGATIONS_C901_LIVREES, ("#9999", releve))
            with pytest.raises(CliquetRemonte) as leve:
                verifier_cliquet(releve, registre, CLIQUET_C901)
            assert "#9999 pose" in str(leve.value) and "renverser docs/53" in str(leve.value)

    def test_une_baisse_s_inscrit_au_registre(self) -> None:
        """Une dérogation retirée s'inscrit : sans sa ligne, la place libérée resterait à prendre
        pour la fonction neuve suivante."""
        _, dernier = DEROGATIONS_C901_LIVREES[-1]
        baisse = dernier - 1
        verifier_cliquet(baisse, (*DEROGATIONS_C901_LIVREES, ("#9999", baisse)), CLIQUET_C901)
        with pytest.raises(CliquetRemonte, match="sans sa ligne"):
            verifier_cliquet(baisse, DEROGATIONS_C901_LIVREES, CLIQUET_C901)
